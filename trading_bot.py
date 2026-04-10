"""
trading_bot.py
Main loop for KR/US sessions. Paper by default, live with --live.
"""

import os
import sys
import json
import time
import atexit
import queue as _queue_mod
import argparse
import threading
import schedule
from pathlib import Path
from datetime import date, datetime, timedelta, time as dt_time
from typing import Optional
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - python<3.9 fallback
    from datetime import timezone

    class ZoneInfo:  # type: ignore
        def __new__(cls, _name: str):
            return timezone(timedelta(hours=9))

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))

from logger import get_analysis_logger, get_judgment_logger, get_trading_logger
from kis_api import (
    get_access_token,
    get_price,
    get_balance,
    get_order_fill_kr,
    get_order_fill_us,
    precheck_order,
    place_order,
    KISWebSocket,
    get_daily_ohlcv,
    get_index_change,
    get_usd_krw,
    screen_market_kr,
    screen_market_us,
    is_trading_halted,
)
from indicators import calc_all
from risk_manager import RiskManager, HARD_RULES
from telegram_reporter import (
    send,
    morning_briefing,
    tuning_report,
    trade_alert,
    display_ticker,
    decision_event_alert,
    pnl_alert,
    daily_summary,
    status_report,
    dashboard_push,
    analyst_reinvoke_alert,
    signal_alert,
    watchlist_alert,
)
from telegram_commander import commander as tg_commander
from minority_report.analysts import get_three_judgments, select_tickers
from minority_report.consensus import build_consensus
from minority_report.tuner import tune
from minority_report.postmortem import run as run_postmortem
from phase1_trainer.digest_builder import build_kr_digest, build_us_digest, digest_to_prompt
from phase1_trainer.sector_play import run_sector_plays, TIER2_SIZE_RATIO
from strategy.momentum import signal as mom_sig, params as mom_params, diagnostics as mom_diag
from strategy.mean_reversion import signal as mr_sig, params as mr_params
from strategy.gap_pullback import signal as gap_sig, params as gap_params
from strategy.volatility_breakout import signal as vb_sig, params as vb_params
from strategy.cross_asset import apply_cross_asset_adjust, get_vix_regime
from strategy.adaptive_params import adaptive_params as _adaptive_params
from strategy.entry_priority import compute as entry_priority_score

from claude_memory import brain as BrainDB
from runtime_paths import get_runtime_path
import ticker_selection_db as tsdb

# ML 의사결정 DB (선택적 — import 실패해도 봇 정상 동작)
try:
    from ml.db_writer import (
        init_db as _ml_init_db,
        write_decision as _ml_write,
        update_filled as _ml_update_filled,
        update_trade_outcome as _ml_update_outcome,
    )
    _ML_DB_ENABLED = True
except Exception as _ml_import_err:
    _ML_DB_ENABLED = False
from universe_manager import (
    UniverseConfig,
    build_universe_from_candidates,
    get_core_tickers,
    load_universe_snapshot,
    save_universe_snapshot,
)

log = get_trading_logger()
analysis_log = get_analysis_logger()
judgment_log = get_judgment_logger()
KST = ZoneInfo("Asia/Seoul")

JUDGMENT_DIR = get_runtime_path("logs", "daily_judgment", make_parents=False)
JUDGMENT_DIR.mkdir(parents=True, exist_ok=True)

POSITIONS_FILE = get_runtime_path("state", "open_positions.json")  # 포지션 영속성 파일
POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
PENDING_ORDERS_FILE = get_runtime_path("state", "pending_orders.json")
PENDING_ORDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
CLAUDE_CONTROL_FILE = get_runtime_path("state", "claude_control.json")
BOT_PID_FILE = get_runtime_path("state", "trading_bot.pid")


def _write_bot_pid_file():
    try:
        BOT_PID_FILE.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
                    "command": [sys.executable, str(Path(__file__).resolve())],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _clear_bot_pid_file():
    try:
        if BOT_PID_FILE.exists():
            BOT_PID_FILE.unlink()
    except Exception:
        pass
CLAUDE_CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)

# 동적 선택 실패 시 폴백 (기본 3종목)
_DEFAULT_KR_TICKERS = ["005930", "068270", "035420"]
_DEFAULT_US_TICKERS = ["NVDA", "TSLA", "GOOGL", "AAPL", "NFLX"]

# 거래소 캘린더 (XKRX=한국거래소, XNYS=NYSE)
_EXCHANGE_MAP = {"KR": "XKRX", "US": "XNYS"}
_ec_cache: dict = {}  # 캘린더 객체 캐시

# 진입 차단 쿨다운 (분)
_STOP_COOLDOWN_MIN = int(os.getenv("STOP_COOLDOWN_MIN", "60"))   # 손절 후 재진입 금지 (20→60: 반복손절 방지)
_BUY_COOLDOWN_MIN  = int(os.getenv("BUY_COOLDOWN_MIN",  "15"))   # 매수 접수 후 중복 차단
_TP_COOLDOWN_MIN   = int(os.getenv("TP_COOLDOWN_MIN",   "10"))   # TP 후 재진입 차단
_MIN_ENTRY_CONF    = float(os.getenv("MIN_ENTRY_CONF",   "0.4"))  # 분석가 평균 confidence 최소값
_STARTUP_GUARD_SEC = float(os.getenv("STARTUP_GUARD_SEC", "60"))  # session_open 후 첫 cycle 보호 구간
_ENTRY_SCAN_OPENING_MIN = int(os.getenv("ENTRY_SCAN_OPENING_MIN", "30"))
_ENTRY_SCAN_OPENING_INTERVAL_MIN = int(os.getenv("ENTRY_SCAN_OPENING_INTERVAL_MIN", "2"))   # 5→2분
_ENTRY_SCAN_REGULAR_INTERVAL_MIN = int(os.getenv("ENTRY_SCAN_REGULAR_INTERVAL_MIN", "10"))
_RESCREEN_INTERVAL_MIN = int(os.getenv("RESCREEN_INTERVAL_MIN", "60"))
_KR_NO_SIGNAL_SWAP_MIN = int(os.getenv("KR_NO_SIGNAL_SWAP_MIN", "60"))   # KR: 무신호 60분 누적 시 교체
_US_NO_SIGNAL_SWAP_CYCLES = int(os.getenv("US_NO_SIGNAL_SWAP_CYCLES", "8"))  # US: 무신호 8사이클 시 교체
# KR: session_open(8:50)과 실제 장 시작(9:00) 사이 오프셋 (분)
_KR_MARKET_OPEN_OFFSET_MIN = int(os.getenv("KR_MARKET_OPEN_OFFSET_MIN", "10"))
# 마감 직전 신규 진입 차단 — session_open 기준 경과 분
# KR: 8:50 KST + 380min = 15:10 KST
# US: 22:20 KST + 370min = 04:30 KST = 15:30 ET  (KST = ET + 13h → 15:30 ET + 13h = 04:30 KST)
_KR_ENTRY_CUTOFF_FROM_OPEN_MIN = int(os.getenv("KR_ENTRY_CUTOFF_FROM_OPEN_MIN", "380"))
_US_ENTRY_CUTOFF_FROM_OPEN_MIN = int(os.getenv("US_ENTRY_CUTOFF_FROM_OPEN_MIN", "370"))
_RESCREEN_SCHEDULE_TICK_MIN = int(os.getenv("RESCREEN_SCHEDULE_TICK_MIN", "30"))
_TASK_SLOW_WARN_SEC = float(os.getenv("TASK_SLOW_WARN_SEC", "240"))  # 작업 지연 경고 임계값 (4분)
_LIVE_STATUS_MIN_INTERVAL = float(os.getenv("LIVE_STATUS_MIN_INTERVAL_SEC", "10"))  # live_status 최소 쓰기 간격

# VIX 구간별 포지션 사이즈 승수 (US 전용)
_VIX_SIZE_TIERS = [
    (30.0, float(os.getenv("VIX_MULT_30", "0.55"))),   # VIX 30+  → 55%
    (25.0, float(os.getenv("VIX_MULT_25", "0.70"))),   # VIX 25+  → 70%
    (20.0, float(os.getenv("VIX_MULT_20", "0.85"))),   # VIX 20+  → 85%
]

# 분석가 suggested_strategy(한글) → 코드 전략명 매핑
_STRATEGY_NAME_MAP = {
    "모멘텀":    "momentum",
    "평균회귀":  "mean_reversion",
    "갭풀백":    "gap_pullback",
    "변동성돌파": "volatility_breakout",
    "관망":      "",
}


def _analyst_strategy_vote(judgments: dict) -> str:
    """
    3명 분석가 suggested_strategy 다수결 → 코드 전략명.
    2표 이상 일치 없으면 "volatility_breakout" 반환.
    """
    from collections import Counter
    votes = [
        _STRATEGY_NAME_MAP.get(
            judgments.get(a, {}).get("suggested_strategy", ""), ""
        )
        for a in ("bull", "bear", "neutral")
    ]
    valid = [v for v in votes if v]
    if not valid:
        return "volatility_breakout"
    top, top_count = Counter(valid).most_common(1)[0]
    return top if top_count >= 2 else "volatility_breakout"


def _market_session_date(market: str, now_dt=None):
    """시장 세션 기준 날짜. US는 KST 자정~05:00 구간을 전일 세션으로 본다."""
    now_dt = now_dt or datetime.now(KST)
    d = now_dt.date()
    if market == "US" and now_dt.time() < dt_time(5, 0):
        return d - timedelta(days=1)
    return d


def _is_trading_day(market: str, check_date=None) -> bool:
    """오늘이 해당 시장의 정규 거래일인지 확인 (주말·공휴일 모두 처리)"""
    if check_date is None:
        check_date = _market_session_date(market)
    exchange = _EXCHANGE_MAP.get(market, "XNYS")
    try:
        import exchange_calendars as ec
        if exchange not in _ec_cache:
            _ec_cache[exchange] = ec.get_calendar(exchange)
        return bool(_ec_cache[exchange].is_session(str(check_date)))
    except Exception as e:
        # exchange_calendars 없거나 오류 시 주말만 체크
        log.warning(f"거래소 캘린더 조회 실패 ({exchange}): {e} — 주말 체크만 적용")
        return check_date.weekday() < 5


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


class TradingBot:
    def __init__(self, is_paper: bool = True):
        self.is_paper = is_paper
        self.token = get_access_token()

        # ── 투자 금액 설정 — KIS 잔고 직접 조회 (모의/실거래 공통) ─────────
        mode_label = "모의투자" if is_paper else "실거래"
        try:
            bal_kr = get_balance(self.token, market="KR")
            init_cash = bal_kr["cash"] + bal_kr["total_eval"]
            if init_cash <= 0:
                if is_paper:
                    # 모의투자 계좌 미초기화 → PAPER_CASH 폴백
                    init_cash = int(os.getenv("PAPER_CASH", "10000000"))
                    log.warning(
                        f"모의투자 잔고 0 → PAPER_CASH 폴백({init_cash:,}원) 사용. "
                        f"KIS 앱에서 모의투자 계좌 초기화 필요 (모의투자 메뉴 → 초기화)"
                    )
                else:
                    raise ValueError("잔고 0 — 계좌 확인 필요")
            env_cap = int(os.getenv("MAX_ORDER_KRW", "500000" if is_paper else "2000000"))
            order_pct = float(os.getenv("MAX_ORDER_PCT", "0.05"))
            max_order = min(env_cap, int(init_cash * order_pct))
            log.info(f"{mode_label} | KIS KR 잔고 {init_cash:,}원 "
                     f"(현금 {bal_kr['cash']:,} + 평가 {bal_kr['total_eval']:,}) "
                     f"| 최대주문 {max_order:,}원")
        except Exception as e:
            log.error(f"KIS 잔고 조회 실패: {e}")
            raise SystemExit(f"KIS {mode_label} 잔고 조회 실패. 계좌/API 설정을 확인하세요.")

        # US 잔고 조회 (참고용 — KR과 공유 풀이므로 init_cash에는 미포함)
        try:
            bal_us = get_balance(self.token, market="US")
            usd_krw = get_usd_krw()
            us_eval_krw = int(bal_us["total_eval"] * usd_krw)
            log.info(f"{mode_label} | KIS US 잔고 ${bal_us['total_eval']:.2f} "
                     f"(≈{us_eval_krw:,}원, 보유종목 {len(bal_us['stocks'])}개)")
        except Exception as e:
            log.warning(f"KIS US 잔고 조회 실패 (무시): {e}")

        self.risk = RiskManager(init_cash=init_cash, max_order_krw=max_order, market="KR")

        # KR/US 공유 풀 — 단일 현금 계좌, 시장 구분 없이 사용
        log.info(f"공유 풀 | 총자금 {init_cash:,.0f}원 (KR/US 공용)")

        self.today_judgment = {}
        self.today_tickers: dict = {}   # {market: [ticker, ...]} — 매일 아침 Claude가 선택
        self.today_ticker_reasons: dict = {}
        self.today_universe: dict = {}
        self.tuning_count = 0
        self.ws = None
        self.price_cache = {}
        self.price_cache_raw = {}
        self._ohlcv_cache: dict = {}        # ticker -> DataFrame (일봉 캐시)
        self._ohlcv_cache_time: dict = {}   # ticker -> datetime (캐시 갱신 시각)
        self._invalid_price_count: dict = {}  # ticker -> 연속 invalid price 횟수
        self.session_active = False
        self.current_market = None
        self._market_task_owner: dict[str, Optional[str]] = {"KR": None, "US": None}
        self._session_open_at: dict[str, float] = {"KR": 0.0, "US": 0.0}  # startup 보호 구간
        self._last_entry_scan_at: dict[str, float] = {"KR": 0.0, "US": 0.0}
        self._last_rescreen_at: dict[str, float] = {"KR": 0.0, "US": 0.0}
        self._task_start_time: dict[str, float] = {"KR": 0.0, "US": 0.0}  # 작업 지연 감지
        self._live_status_written_at: dict[str, float] = {"KR": 0.0, "US": 0.0}  # 중복 쓰기 방지
        self._ticker_no_signal_minutes: dict = {}  # ticker -> 누적 무신호 시간(분), KR 교체 임계에 사용
        self.usd_krw_rate = float(os.getenv("USD_KRW_RATE", "1350"))
        self.enable_limit_order = _env_bool("ENABLE_LIMIT_ORDER", False)
        self.limit_order_offset_bps = int(os.getenv("LIMIT_ORDER_OFFSET_BPS", "5"))
        self.enable_slippage_guard = _env_bool("ENABLE_SLIPPAGE_GUARD", False)
        self.max_est_slippage_bps = float(os.getenv("MAX_EST_SLIPPAGE_BPS", "25"))
        self.enable_atr_position_sizing = _env_bool("ENABLE_ATR_POSITION_SIZING", True)
        self.atr_target_pct = float(os.getenv("ATR_TARGET_PCT", "0.015"))
        self.enable_dynamic_universe = _env_bool("ENABLE_DYNAMIC_UNIVERSE", False)
        self.dynamic_universe_top_n = int(os.getenv("DYNAMIC_UNIVERSE_TOP_N", "20"))

        # 트레일링 스탑
        self.enable_trailing_stop     = _env_bool("TRAILING_STOP_ENABLED", True)
        self.trailing_stop_pct        = max(0.01, min(0.10, float(os.getenv("TRAILING_STOP_PCT", "3")) / 100))
        self.enable_trailing_analyst  = _env_bool("TRAILING_ANALYST_ENABLED", False)

        # 텔레그램 중복 방지용 마지막 전송 상태
        self._last_tg_state: dict = {}
        self._last_tg_signal_state: dict = {}

        # 긴급 재판단 쿨다운 (마지막 재호출 튜닝 카운트, 60분=2사이클 간격 유지)
        self._last_reinvoke_tuning: int = -99

        # 장중 이벤트 기록 (튜닝/긴급재판단) — session_close 시 daily_judgment에 포함
        self._session_events: list = []
        self.decision_event_log: list = []
        self._us_order_supported: set[str] = set()
        self._us_order_blocked: dict[str, str] = {}
        self.pending_orders: list[dict] = []
        # 진입 차단: {ticker: unblock_epoch_sec} — 손절 쿨다운 + 중복 매수 방지
        self._entry_blocked: dict[str, float] = {}
        self.claude_control: dict = {}

        # 당일 손절 카운터 {market: count} — 연속 손절 시 size_mult 자동 축소
        self._daily_sl_count: dict[str, int] = {"KR": 0, "US": 0}

        # WS tick 기반 장중 고가/저가 누적 — 당일봉 주입 시 단일가봉 탈출에 사용
        self._intraday_high: dict[str, float] = {}
        self._intraday_low:  dict[str, float] = {}

        # 매도 실패 쿨다운 — ticker → 실패 시각, 90초간 재시도 억제
        self._sell_fail_at:  dict[str, float] = {}

        # entry_priority cutoff (Phase 2) — env로 ON/OFF, 텔레그램으로 실시간 토글
        self.entry_priority_cutoff_enabled: bool = (
            os.getenv("ENTRY_PRIORITY_CUTOFF_ENABLED", "false").lower() == "true"
        )
        self.entry_priority_cutoff: float = float(os.getenv("ENTRY_PRIORITY_CUTOFF", "0.20"))

        # ticker_selection_log DB — 종목 선택 품질 누적 (ML Phase 3 학습용)
        tsdb.init()
        self._tsdb_selection_ids: dict = {"KR": {}, "US": {}}

        # ── 히스토리 보강 큐 ──────────────────────────────────────────────────
        self._hist_fill_queue: _queue_mod.Queue = _queue_mod.Queue()
        self._hist_fill_queued: set = set()      # 큐 대기 중 (중복 방지)
        self._hist_fill_inflight: set = set()    # 수집 진행 중
        self._hist_fill_last_ts: dict = {}       # ticker → 마지막 시도 time.time()
        _hist_thread = threading.Thread(target=self._history_fill_worker, daemon=True)
        _hist_thread.start()

        # ── 부분 재선택 ───────────────────────────────────────────────────────
        self._partial_reselect_last: dict = {"KR": None, "US": None}
        self._ticker_no_signal_cycles: dict = {}   # ticker → 연속 무신호 사이클 수
        self._ticker_exclude_log: dict = {"KR": [], "US": []}  # [{ticker,reason,ts}]

        # ML DB 초기화
        if _ML_DB_ENABLED:
            try:
                _ml_init_db()
            except Exception as _e:
                log.warning(f"[ML DB] 초기화 실패 (봇 동작에 영향 없음): {_e}")

        self._restore_pending_orders()
        self._restore_claude_control()
        self._sanitize_live_status_file("KR")
        self._sanitize_live_status_file("US")
        # 재시작 시 이월 포지션 복구
        self._restore_positions()

        # API 연결 상태 점검
        self._startup_health_check()

        # Phase1 학습 상태 경고
        try:
            brain = BrainDB.load()
            trained = brain["meta"]["trained_days_kr"] + brain["meta"]["trained_days_us"]
            if trained == 0:
                log.warning("⚠️  brain.json 학습 데이터 없음 — Phase1 시뮬레이션 권장:")
                log.warning("   python phase1_trainer/historical_sim.py --market KR --start 2024-10-01")
                log.warning("   python phase1_trainer/historical_sim.py --market US --start 2025-01-01")
        except Exception:
            pass

        log.info(f"init | {'paper' if is_paper else 'live'}")

    def _enter_market_task(self, market: str, owner: str) -> bool:
        """같은 시장에서 상위 스케줄 작업이 겹치지 않게 막는다."""
        current = self._market_task_owner.get(market)
        if current and current != owner:
            log.info(f"[skip {market}] {owner} busy={current}")
            return False
        self._market_task_owner[market] = owner
        self._task_start_time[market] = time.time()
        return True

    def _leave_market_task(self, market: str, owner: str):
        if self._market_task_owner.get(market) == owner:
            elapsed = time.time() - self._task_start_time.get(market, time.time())
            if elapsed > _TASK_SLOW_WARN_SEC:
                log.warning(
                    f"[slow {market}] {owner} {elapsed:.0f}s 소요 "
                    f"— 다음 주기 밀림 가능 (임계={_TASK_SLOW_WARN_SEC:.0f}s)"
                )
            self._market_task_owner[market] = None

    def _persist_live_judgment(self, market: str):
        """장중 판단 변경을 즉시 저장해 재시작/대시보드가 최신 상태를 보게 한다."""
        if not self.today_judgment or self.today_judgment.get("market") != market:
            return
        live_path = JUDGMENT_DIR / f"{date.today().strftime('%Y%m%d')}_{market}.json"
        try:
            with open(live_path, "w", encoding="utf-8") as f:
                json.dump({
                    **self.today_judgment,
                    "mode": "paper" if self.is_paper else "live",
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"판단 임시저장 실패: {e}")

    def _log_screen_candidates(self, market: str, candidates: list, source: str):
        """대시보드가 최신 후보 집합을 알 수 있도록 재스크리닝도 analysis 로그에 남긴다."""
        today = date.today().isoformat()
        analysis_log.info(
            f"[screen {today} {market}] source={source} candidates={len(candidates)}",
            extra={"extra": {
                "event": "screen_candidates",
                "date": today,
                "market": market,
                "source": source,
                "count": len(candidates),
                "tickers": [c.get("ticker") for c in candidates if c.get("ticker")][:20],
            }},
        )

    def manual_rescreen(self, market: str | None = None) -> list[str]:
        """텔레그램 수동 명령으로 현재 시장 종목만 즉시 재선택."""
        target_market = market or self.current_market or self.today_judgment.get("market")
        if target_market not in ("KR", "US"):
            raise ValueError("재선택할 시장이 없습니다.")
        if not self.session_active:
            raise ValueError("세션이 비활성 상태입니다.")
        owner = "manual_rescreen"
        if not self._enter_market_task(target_market, owner):
            current = self._market_task_owner.get(target_market) or "-"
            raise RuntimeError(f"{target_market} 시장 작업 진행 중: {current}")
        try:
            consensus = dict((self.today_judgment or {}).get("consensus") or {})
            mode = consensus.get("mode", "NEUTRAL")
            digest_prompt = (self.today_judgment or {}).get("digest_prompt", "")
            today = date.today().isoformat()
            if target_market == "KR":
                candidates = screen_market_kr(self.token)
            else:
                candidates = screen_market_us()
            if not candidates:
                raise RuntimeError("재스크리닝 후보가 없습니다.")
            self._log_screen_candidates(target_market, candidates, "manual_rescreen")
            candidates = self._prefill_history_sync(candidates, target_market)
            candidates = self._filter_candidates_by_history(candidates, target_market)
            _manual_cooldown = self._get_cooldown_excluded(target_market)
            candidates = [c for c in candidates if c.get("ticker") not in _manual_cooldown]
            if not candidates:
                raise RuntimeError("히스토리 필터 통과 후보가 없습니다.")
            selected, reasons = select_tickers(target_market, digest_prompt, mode, candidates)
            if not selected:
                raise RuntimeError("최종 선택 종목이 없습니다.")
            self.today_tickers[target_market] = selected
            self.today_ticker_reasons[target_market] = reasons or {}
            self.today_judgment["tickers"] = selected
            self.today_judgment["universe_tickers"] = [
                c.get("ticker") for c in candidates if c.get("ticker")
            ]
            log.info(f"[수동 종목 재선택 완료] {target_market}: {selected}")
            judgment_log.info(
                f"[manual_rescreen {today} {target_market}] {selected}",
                extra={"extra": {
                    "event": "ticker_rescreen",
                    "date": today,
                    "market": target_market,
                    "selected": selected,
                    "consensus_mode": mode,
                    "trigger": "telegram_manual",
                }},
            )
            excluded = [c.get("ticker", "") for c in candidates if c.get("ticker", "") not in selected]
            _mode_size = self.today_judgment.get("consensus", {}).get("size", 50)
            _mode_order_limit = self.risk.calc_order_budget(_mode_size)
            watchlist_alert(target_market, mode, selected, reasons, excluded, trigger="manual_rescreen",
                            mode_order_limit_krw=_mode_order_limit)
            self._persist_live_judgment(target_market)
            return selected
        finally:
            self._leave_market_task(target_market, owner)

    # ── 후보 히스토리 필터 ────────────────────────────────────────────────────

    _MIN_SIGNAL_ROWS = 65  # calc_all 후 유효 행 최소치 (ma60=60봉 + 여유 5봉)

    def _filter_candidates_by_history(self, candidates: list, market: str) -> list:
        """
        스크리너 후보 중 신호 계산에 필요한 히스토리가 부족한 종목 제거.
        - calc_all() 후 유효 행(ma60 기준 dropna) < _MIN_SIGNAL_ROWS 인 종목 제외
        - 가격 데이터 자체가 없는 종목도 제외
        """
        filtered = []
        removed = []
        for c in candidates:
            ticker = c.get("ticker", "")
            if not ticker:
                continue
            try:
                candles = self._get_ohlcv_cached(ticker, market)
                if candles.empty:
                    removed.append((ticker, "캔들없음"))
                    continue
                sig_df = calc_all(candles)
                if len(sig_df) < self._MIN_SIGNAL_ROWS:
                    removed.append((ticker, f"데이터부족({len(sig_df)}행)"))
                    continue
                filtered.append(c)
            except Exception as e:
                removed.append((ticker, f"오류:{e}"))
                continue
        if removed:
            log.info(
                f"[히스토리 필터] {market} 제외 {len(removed)}개: "
                + ", ".join(f"{t}({r})" for t, r in removed)
            )
        log.info(f"[히스토리 필터] {market} 후보 {len(candidates)}개 → 유효 {len(filtered)}개 통과 (제외 {len(removed)}개)")
        # 데이터부족 종목은 백그라운드 히스토리 보강 큐에 등록
        for t, r in removed:
            if "데이터부족" in r:
                self._hist_fill_enqueue(t, market)
        return filtered

    # ── API 헬스체크 ───────────────────────────────────────────────────────────

    def _startup_health_check(self):
        """봇 시작 시 연결된 API 전체 상태 점검 후 텔레그램 리포트"""
        import os as _os
        import requests as _req
        from kis_api import BASE_URL, IS_PAPER as _KIS_PAPER
        results: dict[str, str] = {}

        mode_label  = "모의투자" if self.is_paper else "실거래"
        server_type = "모의서버" if _KIS_PAPER else "실서버"
        server_url  = BASE_URL

        # 1. KIS 모드 / 서버
        results[f"KIS 모드"] = f"OK | {mode_label} ({server_type})"
        results["KIS 서버"]  = f"OK | {server_url}"

        # 2. KIS 토큰
        results["KIS 토큰"] = "OK" if self.token else "FAIL - 토큰 없음"

        # 3. KIS 시세 (삼성전자)
        try:
            price_info = get_price("005930", self.token, market="KR")
            results["KIS 시세 (005930)"] = f"OK | {price_info['price']:,}원"
        except Exception as e:
            results["KIS 시세 (005930)"] = f"FAIL - {e}"

        # 4. KIS 잔고
        try:
            bal = get_balance(self.token, market="KR")
            total = bal["cash"] + bal["total_eval"]
            results["KIS 잔고"] = (
                f"OK | 총 {total:,}원 (현금 {bal['cash']:,} + 평가 {bal['total_eval']:,})"
            )
        except Exception as e:
            results["KIS 잔고"] = f"FAIL - {e}"

        # 5. Finnhub (US 현재가)
        fh_key = _os.getenv("FINNHUB_API_KEY", "")
        if fh_key:
            try:
                resp = _req.get(
                    "https://finnhub.io/api/v1/quote",
                    params={"symbol": "NVDA", "token": fh_key}, timeout=5,
                )
                resp.raise_for_status()
                price = resp.json().get("c", 0)
                results["Finnhub (NVDA)"] = f"OK | ${price:.2f}" if price else "WARN - 가격 0"
            except Exception as e:
                results["Finnhub (NVDA)"] = f"FAIL - {e}"
        else:
            results["Finnhub"] = "SKIP - FINNHUB_API_KEY 없음"

        # 6. FMP (US 스크리너)
        fmp_key = _os.getenv("FMP_API_KEY", "")
        if fmp_key:
            try:
                resp = _req.get(
                    "https://financialmodelingprep.com/stable/most-actives",
                    params={"apikey": fmp_key}, timeout=5,
                )
                resp.raise_for_status()
                cnt = len(resp.json()) if isinstance(resp.json(), list) else 0
                results["FMP (스크리너)"] = f"OK | {cnt}개 종목"
            except Exception as e:
                results["FMP (스크리너)"] = f"FAIL - {e}"
        else:
            results["FMP"] = "SKIP - FMP_API_KEY 없음"

        # 7. Alpha Vantage KEY-1 / KEY-2
        av_key  = _os.getenv("ALPHA_VANTAGE_KEY", "")
        av_key2 = _os.getenv("ALPHA_VANTAGE_KEY_2", "")
        if av_key:
            try:
                candles = get_daily_ohlcv("NVDA", self.token, lookback_days=5, market="US")
                results["Alpha Vantage KEY-1"] = (
                    f"OK | {len(candles)}일 캔들" if not candles.empty else "WARN - 빈 데이터"
                )
            except Exception as e:
                results["Alpha Vantage KEY-1"] = f"FAIL - {e}"
        else:
            results["Alpha Vantage KEY-1"] = "SKIP - 키 없음"
        results["Alpha Vantage KEY-2"] = ("OK | 대기중" if av_key2 else "SKIP - 키 없음")

        # 8. Telegram
        tg_token = _os.getenv("TELEGRAM_TOKEN", "")
        if tg_token:
            try:
                resp = _req.get(
                    f"https://api.telegram.org/bot{tg_token}/getMe", timeout=5
                )
                resp.raise_for_status()
                bot_name = resp.json()["result"].get("username", "?")
                results["Telegram"] = f"OK | @{bot_name}"
            except Exception as e:
                results["Telegram"] = f"FAIL - {e}"
        else:
            results["Telegram"] = "SKIP - TELEGRAM_TOKEN 없음"

        # 9. DART (공시) - 선택
        dart_key = _os.getenv("DART_API_KEY", "")
        if dart_key:
            try:
                resp = _req.get(
                    "https://opendart.fss.or.kr/api/company.json",
                    params={"crtfc_key": dart_key, "corp_code": "00126380"},
                    timeout=5,
                )
                resp.raise_for_status()
                status = resp.json().get("status", "?")
                results["DART API"] = "OK" if status == "000" else f"WARN - status={status}"
            except Exception as e:
                results["DART API"] = f"FAIL - {e}"
        else:
            results["DART API"] = "SKIP - 키 없음"

        # 결과 로그 출력
        ok_count   = sum(1 for v in results.values() if v.startswith("OK"))
        fail_count = sum(1 for v in results.values() if v.startswith("FAIL"))
        log.info(f"─── API Health Check [{mode_label}] ────────────────────")
        for name, status in results.items():
            icon = "✓" if status.startswith("OK") else ("✗" if status.startswith("FAIL") else "–")
            log.info(f"  {icon} {name}: {status}")
        log.info(f"──────────────────────────── OK={ok_count} / FAIL={fail_count} ──")

        # 텔레그램
        lines = [f"🤖 봇 시작 [{mode_label}] — API 점검 결과"]
        for name, status in results.items():
            icon = "✅" if status.startswith("OK") else ("❌" if status.startswith("FAIL") else "➖")
            lines.append(f"{icon} {name}: {status}")
        if fail_count:
            lines.append(f"\n⚠️ {fail_count}개 API 연결 실패 — 확인 필요")
        send("\n".join(lines))

    # ── 시장별 예산 조회 ──────────────────────────────────────────────────────

    def _market_budget_available(self, market: str) -> float:
        """사용 가능한 현금 잔액 (KR/US 공유 풀)"""
        return max(0.0, self.risk.cash)

    # ── 포지션 영속성 ──────────────────────────────────────────────────────────

    def _save_positions(self):
        """이월 포지션을 파일에 저장 (봇 재시작 복구용)
        max_hold>1 장기 보유 뿐 아니라 당일 미청산 포지션(max_hold==1)도 저장:
        VTS 잔고 API 미반영 시 재시작 후 복구 가능하도록.
        """
        carry = list(self.risk.positions)  # 보유 중인 모든 포지션 저장
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(carry, f, ensure_ascii=False, indent=2, default=str)
        if carry:
            log.info(f"[포지션 저장] {[p['ticker'] for p in carry]} ({len(carry)}개) → {POSITIONS_FILE}")

    def _save_pending_orders(self):
        self._normalize_pending_orders()
        with open(PENDING_ORDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.pending_orders, f, ensure_ascii=False, indent=2, default=str)

    def _parse_pending_created_at(self, order: dict):
        raw = order.get("created_at", "")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None

    def _normalize_pending_orders(self):
        if not self.pending_orders:
            return
        today_kst = datetime.now(KST).date()
        latest_by_ticker = {}
        for order in self.pending_orders:
            ticker = (order.get("ticker", "") or "").strip()
            market = order.get("market", "")
            if not ticker or market not in ("KR", "US"):
                continue
            created_at = self._parse_pending_created_at(order)
            if created_at and created_at.astimezone(KST).date() < today_kst:
                continue
            key = (market, ticker.upper())
            prev = latest_by_ticker.get(key)
            if prev is None:
                latest_by_ticker[key] = order
                continue
            prev_dt = self._parse_pending_created_at(prev)
            cur_dt = created_at
            if prev_dt is None or (cur_dt is not None and cur_dt > prev_dt):
                latest_by_ticker[key] = order
        self.pending_orders = sorted(
            latest_by_ticker.values(),
            key=lambda o: self._parse_pending_created_at(o) or datetime.min.replace(tzinfo=KST)
        )

    def _restore_pending_orders(self):
        if not PENDING_ORDERS_FILE.exists():
            return
        try:
            with open(PENDING_ORDERS_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, list):
                self.pending_orders = saved
                self._normalize_pending_orders()
                self._save_pending_orders()
        except Exception as e:
            log.error(f"미체결 주문 복구 실패: {e}")

    def _default_claude_control(self) -> dict:
        return {
            "enabled": True,
            "updated_at": "",
            "updated_by": "default",
            "last_trigger_at": "",
            "last_trigger_market": "",
            "last_trigger_source": "",
            "last_result_at": "",
            "last_result_market": "",
            "last_result_status": "idle",
            "last_error": "",
            "pending_trigger": None,
        }

    def _save_claude_control(self):
        with open(CLAUDE_CONTROL_FILE, "w", encoding="utf-8") as f:
            json.dump(self.claude_control, f, ensure_ascii=False, indent=2, default=str)

    def _normalize_claude_control_state(self, data: dict | None = None) -> dict:
        control = self._default_claude_control()
        control.update(data or {})
        status = str(control.get("last_result_status", "idle") or "idle").lower()
        if status != "running":
            return control
        ts = control.get("last_result_at") or control.get("last_trigger_at") or ""
        if not ts:
            control["last_result_status"] = "error"
            control["last_error"] = control.get("last_error") or "이전 Claude 재판단이 비정상 종료되어 상태를 정리했습니다."
            return control
        try:
            last_dt = datetime.fromisoformat(ts)
            if last_dt.tzinfo is None:
                last_dt = KST.localize(last_dt)
        except Exception:
            control["last_result_status"] = "error"
            control["last_error"] = control.get("last_error") or "이전 Claude 재판단 시각이 손상되어 상태를 정리했습니다."
            return control
        if datetime.now(KST) - last_dt > timedelta(minutes=10):
            control["last_result_status"] = "error"
            control["last_error"] = control.get("last_error") or "이전 Claude 재판단이 10분 이상 완료되지 않아 stale 상태로 정리했습니다."
        return control

    def _restore_claude_control(self):
        data = self._default_claude_control()
        if CLAUDE_CONTROL_FILE.exists():
            try:
                with open(CLAUDE_CONTROL_FILE, encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    data.update(saved)
            except Exception as e:
                log.warning(f"Claude 제어 상태 복구 실패: {e}")
        data["enabled"] = True
        self.claude_control = self._normalize_claude_control_state(data)
        self._save_claude_control()

    def _refresh_claude_control(self):
        if not CLAUDE_CONTROL_FILE.exists():
            self._restore_claude_control()
            return
        try:
            with open(CLAUDE_CONTROL_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                data = self._default_claude_control()
                data.update(saved)
                self.claude_control = self._normalize_claude_control_state(data)
                if self.claude_control != data:
                    self._save_claude_control()
        except Exception as e:
            log.warning(f"Claude 제어 상태 새로고침 실패: {e}")

    def _sanitize_live_status_file(self, market: str):
        path = get_runtime_path("state", f"live_status_{market}.json")
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        changed = False
        positions = []
        for pos in data.get("positions", []) or []:
            ticker = str(pos.get("ticker", "") or "")
            if ticker and self._ticker_market(ticker) == market:
                positions.append(pos)
            else:
                changed = True
        pending_orders = []
        for order in data.get("pending_orders", []) or []:
            ticker = str(order.get("ticker", "") or "")
            order_market = str(order.get("market", "") or "")
            inferred_market = order_market or self._ticker_market(ticker)
            if ticker and inferred_market == market:
                order["market"] = market
                pending_orders.append(order)
            else:
                changed = True
        if data.get("market") != market:
            data["market"] = market
            changed = True
        if positions != (data.get("positions", []) or []):
            data["positions"] = positions
            data["position_count"] = len(positions)
            changed = True
        if pending_orders != (data.get("pending_orders", []) or []):
            data["pending_orders"] = pending_orders
            data["pending_count"] = len(pending_orders)
            changed = True
        if changed:
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def is_claude_reinvoke_enabled(self) -> bool:
        self._refresh_claude_control()
        return bool(self.claude_control.get("enabled", True))

    def _consume_pending_claude_trigger(self, market: str):
        self._refresh_claude_control()
        pending = self.claude_control.get("pending_trigger")
        if not pending or pending.get("market") != market:
            return
        if not self.is_claude_reinvoke_enabled():
            self.claude_control["pending_trigger"] = None
            self.claude_control["last_result_at"] = datetime.now(KST).isoformat(timespec="seconds")
            self.claude_control["last_result_market"] = market
            self.claude_control["last_result_status"] = "disabled"
            self.claude_control["last_error"] = "Claude 재판단 기능이 OFF 상태입니다."
            self._save_claude_control()
            return
        try:
            self._reinvoke_analysts(market, pending.get("source", "dashboard"))
            self.claude_control["last_result_status"] = "success"
            self.claude_control["last_error"] = ""
        except Exception as e:
            self.claude_control["last_result_status"] = "error"
            self.claude_control["last_error"] = str(e)
            log.error(f"[Claude 대시보드 트리거 실패] {market}: {e}")
        finally:
            self.claude_control["pending_trigger"] = None
            self.claude_control["last_result_at"] = datetime.now(KST).isoformat(timespec="seconds")
            self.claude_control["last_result_market"] = market
            self._save_claude_control()

    def _has_pending_order(self, ticker: str, market: str) -> bool:
        normalized = ticker.upper() if market == "US" else ticker
        for order in self.pending_orders:
            if order.get("market") != market:
                continue
            existing = (order.get("ticker", "") or "")
            existing = existing.upper() if market == "US" else existing
            if existing == normalized:
                return True
        return False

    def _has_open_position(self, ticker: str, market: str) -> bool:
        normalized = ticker.upper() if market == "US" else ticker
        for pos in self.risk.positions:
            pos_ticker = (pos.get("ticker", "") or "")
            if self._ticker_market(pos_ticker) != market:
                continue
            existing = pos_ticker.upper() if market == "US" else pos_ticker
            if existing == normalized and float(pos.get("qty", 0) or 0) > 0:
                return True
        return False

    def _block_entry(self, ticker: str, minutes: int, reason: str):
        """ticker 진입을 minutes분 동안 차단"""
        import time as _time
        self._entry_blocked[ticker] = _time.time() + minutes * 60
        log.info(f"[진입 차단] {ticker} {minutes}분 ({reason})")

    def _is_entry_blocked(self, ticker: str) -> bool:
        """차단 중이면 True, 만료됐으면 제거 후 False"""
        import time as _time
        until = self._entry_blocked.get(ticker, 0)
        if _time.time() < until:
            return True
        if ticker in self._entry_blocked:
            del self._entry_blocked[ticker]
        return False

    def _has_same_day_trade(self, ticker: str, market: str) -> bool:
        """당일 동일 종목 체결 이력이 있으면 재진입 금지."""
        today_iso = date.today().isoformat()
        normalized = ticker.upper() if market == "US" else ticker
        for evt in reversed(self.risk.all_trade_log):
            evt_ticker = evt.get("ticker", "")
            evt_market = evt.get("market") or self._ticker_market(evt_ticker)
            evt_norm = evt_ticker.upper() if evt_market == "US" else evt_ticker
            if evt.get("date") != today_iso:
                continue
            if evt_market != market:
                continue
            if evt_norm == normalized and evt.get("side") in ("buy", "sell"):
                return True
        return False

    def _summarize_none_reason(self, detail: str) -> str:
        text = str(detail or "").strip()
        if not text:
            return ""
        if "돌파=" in text and "거래량부족" in text:
            return "핵심: 돌파는 됐지만 거래량 부족"
        priority = [
            ("거래량부족", "핵심: 거래량 부족"),
            ("RSI부족", "핵심: RSI 부족"),
            ("BB부족", "핵심: BB 부족"),
            ("돌파부족", "핵심: 돌파 부족"),
            ("신고가부족", "핵심: 신고가 부족"),
            ("추세부족", "핵심: 추세 부족"),
            ("MACD부족", "핵심: MACD 부족"),
            ("갭부족", "핵심: 갭 부족"),
            ("눌림부족", "핵심: 눌림 부족"),
            ("ma60근접부족", "핵심: ma60 근접 부족"),
        ]
        hits = [label for key, label in priority if key in text]
        if not hits:
            return ""
        if len(hits) == 1:
            return hits[0]
        return f"{hits[0]} + {hits[1].replace('핵심: ', '')}"

    def _notify_signal_state_change(
        self,
        event: str,
        market: str,
        ticker: str,
        *,
        strategy: str = "",
        price: float = 0.0,
        reason: str = "",
        mode: str = "",
        qty: int = 0,
        order_cost_krw: int = 0,
        detail: str = "",
    ):
        summary = self._summarize_none_reason(detail) if event == "signal_check" else str(detail or "").strip()
        key = (market, ticker.upper() if market == "US" else ticker)
        signature = (event, strategy, reason, mode, summary)
        if self._last_tg_signal_state.get(key) == signature:
            return
        self._last_tg_signal_state[key] = signature

        ticker_disp = ticker
        try:
            if market == "US":
                ticker_disp = ticker.upper()
            text_market = "KR" if market == "KR" else "US"
            now = datetime.now(KST).strftime("%H:%M:%S")
            ko_reason = {
                "same_day_reentry_blocked": "당일 재진입 차단",
                "already_holding": "이미 보유중",
                "pending_order": "미체결 주문",
                "signal_blocked": "모드 차단",
                "no_signal": "신호 없음",
            }.get(reason, reason or "-")
            ko_strategy = {
                "volatility_breakout": "변동성돌파",
                "gap_pullback": "갭눌림",
                "momentum": "모멘텀",
                "mean_reversion": "평균회귀",
            }.get(strategy, strategy or "-")
            ko_mode = {
                "NEUTRAL": "중립",
                "MILD_BULL": "완만강세",
                "Bull_Confirmed": "상승확인",
                "MODERATE_BULL": "강한상승",
                "AGGRESSIVE": "공격매수",
                "MILD_BEAR": "완만약세",
                "CAUTIOUS_BEAR": "신중약세",
                "DEFENSIVE": "방어",
                "HALT": "거래중지",
            }.get(mode, mode or "-")
            price_str = (
                f"{int(price):,}원" if market == "KR" and price > 0
                else f"${price:.2f}" if market == "US" and price > 0
                else "-"
            )

            if event == "entry_signal":
                cost_str = f" · 주문금액 {order_cost_krw:,}원" if order_cost_krw else ""
                text = (
                    f"[진입 신호] {ticker_disp} {text_market} {now}\n"
                    f"전략: {ko_strategy} · 가격 {price_str}{cost_str}\n"
                    f"모드: {ko_mode}"
                )
            elif event == "signal_blocked":
                text = (
                    f"[신호 차단] {ticker_disp} {text_market} {now}\n"
                    f"전략: {ko_strategy} · 가격 {price_str}\n"
                    f"모드: {ko_mode} · 사유: {ko_reason}"
                )
            elif event == "signal_check":
                text = (
                    f"[신호 없음] {ticker_disp} {text_market} {now}\n"
                    f"모드: {ko_mode}"
                    + (f"\n{summary}" if summary else "")
                )
            elif event == "entry_skip":
                text = (
                    f"[진입 보류] {ticker_disp} {text_market} {now}\n"
                    f"사유: {ko_reason} · 가격 {price_str}\n"
                    f"모드: {ko_mode}"
                    + (f"\n{summary}" if summary else "")
                )
            else:
                return
            send(text)
        except Exception as e:
            log.debug(f"[텔레그램 신호상태 전송 실패] {ticker} {event}: {e}")

    def _restore_positions(self):
        """저장된 이월 포지션 복구"""
        if not POSITIONS_FILE.exists():
            return
        try:
            with open(POSITIONS_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            if not saved:
                return

            saved = self._verify_live_positions(saved)

            for pos in saved:
                # date 필드가 문자열로 저장됐을 수 있으므로 타입 보정
                pos.setdefault("held_days", 0)
                pos.setdefault("entry_date", date.today().isoformat())
                cost = pos["entry"] * pos["qty"]
                self.risk.cash -= cost
                self.risk.positions.append(pos)

            log.info(f"[포지션 복구] {[p['ticker'] for p in saved]} "
                     f"(총 {len(saved)}개 / 현금 잔여 {self.risk.cash:,.0f}원)")
        except Exception as e:
            log.error(f"포지션 복구 실패: {e}")

    def _verify_live_positions(self, saved: list) -> list:
        """KIS 브로커 잔고와 저장 포지션 비교 → 실제 보유 중인 것만 유지 (KR + US)"""
        # KR 잔고 조회
        broker_kr: dict = {}
        kr_ok = False
        try:
            bal_kr = get_balance(self.token, market="KR")
            broker_kr = {s["ticker"]: s for s in bal_kr["stocks"]}
            kr_ok = True
        except Exception as e:
            log.error(f"[브로커 동기화] KR 잔고 조회 실패: {e}")

        # US 잔고 조회
        broker_us: dict = {}
        us_ok = False
        try:
            bal_us = get_balance(self.token, market="US")
            broker_us = {s["ticker"].upper(): s for s in bal_us["stocks"]}
            us_ok = True
        except Exception as e:
            log.error(f"[브로커 동기화] US 잔고 조회 실패: {e}")

        # 둘 다 실패하면 저장 포지션 그대로 사용
        if not kr_ok and not us_ok:
            log.error("[브로커 동기화] KR/US 모두 조회 실패 → 저장 포지션 그대로 사용")
            return saved

        # VTS(모의) 잔고 API가 장 마감 후 0건 반환하는 경우 안전장치:
        # 저장 포지션이 있는데 브로커가 해당 마켓 주식을 0개 반환하면 API 오류로 간주
        saved_kr_count = sum(1 for p in saved if not p.get("ticker", "").replace(".", "").isalpha())
        saved_us_count = sum(1 for p in saved if p.get("ticker", "").replace(".", "").isalpha())

        if kr_ok and saved_kr_count > 0 and len(broker_kr) == 0:
            log.warning(
                f"[브로커 동기화] KR 잔고 조회 성공했으나 보유주식 0개 반환 "
                f"(저장 포지션 {saved_kr_count}개) — VTS API 미반영으로 판단, 검증 스킵"
            )
            kr_ok = False  # 검증 무력화 → 아래 루프에서 KR 포지션 그대로 유지

        if us_ok and saved_us_count > 0 and len(broker_us) == 0:
            log.warning(
                f"[브로커 동기화] US 잔고 조회 성공했으나 보유주식 0개 반환 "
                f"(저장 포지션 {saved_us_count}개) — API 미반영으로 판단, 검증 스킵"
            )
            us_ok = False  # 검증 무력화 → 아래 루프에서 US 포지션 그대로 유지

        verified = []
        for pos in saved:
            tk = pos["ticker"]
            market = "US" if tk.replace(".", "").isalpha() else "KR"

            # 해당 마켓 잔고 조회 실패 or 0건 의심 → 포지션 제거하지 않고 유지
            if market == "US" and not us_ok:
                log.warning(f"[브로커 동기화] US 잔고 조회 신뢰 불가 — {tk} 포지션 유지 (검증 스킵)")
                verified.append(pos)
                continue
            if market == "KR" and not kr_ok:
                log.warning(f"[브로커 동기화] KR 잔고 조회 신뢰 불가 — {tk} 포지션 유지 (검증 스킵)")
                verified.append(pos)
                continue

            broker = broker_us if market == "US" else broker_kr

            if tk.upper() in broker if market == "US" else tk in broker:
                key = tk.upper() if market == "US" else tk
                bq = broker[key].get("qty", 0)
                if bq != pos["qty"]:
                    log.warning(f"[브로커 동기화] {tk} 수량 불일치 "
                                f"저장={pos['qty']} 브로커={bq} → 보정")
                    pos["qty"] = bq
                verified.append(pos)
            else:
                log.warning(f"[브로커 동기화] {tk} ({market}) 브로커에 없음 → 포지션 제거")

        return verified

    def _make_runtime_position_from_broker(self, ticker: str, market: str, broker_pos: dict,
                                           template: Optional[dict] = None) -> dict:
        template = template or {}
        native_avg_price = float(template.get("filled_price_native", 0) or broker_pos.get("avg_price", 0) or 0)
        native_eval_price = float(broker_pos.get("eval_price", 0) or native_avg_price)
        avg_price = native_avg_price
        eval_price = native_eval_price
        if market == "US":
            avg_price *= self.usd_krw_rate
            eval_price *= self.usd_krw_rate
        tp_pct = float(template.get("tp_pct", 0.025))
        sl_pct = float(template.get("sl_pct", 0.015 if market == "US" else 0.01))
        pos_name = (
            str(template.get("name", "") or "").strip()
            or str(broker_pos.get("name", "") or "").strip()
            or self._lookup_ticker_name(ticker, market)
        )
        return {
            "ticker": ticker,
            "name": pos_name,
            "entry": avg_price,
            "qty": int(broker_pos.get("qty", 0) or 0),
            "current_price": eval_price or avg_price,
            "display_avg_price": native_avg_price,
            "display_current_price": native_eval_price or native_avg_price,
            "display_currency": "USD" if market == "US" else "KRW",
            "price_source": "order_fill" if template.get("filled_price_native") else "broker_balance",
            "order_no": template.get("order_no", ""),
            "fill_time": template.get("fill_time", ""),
            "strategy": template.get("strategy", "broker_sync"),
            "tp": avg_price * (1 + tp_pct),
            "sl": avg_price * (1 - sl_pct),
            "max_hold": int(template.get("max_hold", 1)),
            "held_days": int(template.get("held_days", 0)),
            "entry_date": template.get("entry_date", date.today().isoformat()),
            "trailing": bool(template.get("trailing", False)),
            "trail_sl": float(template.get("trail_sl", 0.0)),
            "trail_pct": float(template.get("trail_pct", 0.03)),
            "tp_triggered": bool(template.get("tp_triggered", False)),
            "hold_advice": template.get("hold_advice"),
            "tp_price": float(template.get("tp_price", 0.0)),
            "decision_id": template.get("decision_id") or self._recover_decision_id(ticker, market),
        }

    def _recover_decision_id(self, ticker: str, market: str) -> int | None:
        """브로커 복구 포지션에 대해 ML DB에서 decision_id를 best-effort로 조회.
        오늘 날짜 BUY_SIGNAL 중 아직 filled=0인 행을 찾아 연결한다."""
        if not _ML_DB_ENABLED:
            return None
        try:
            from ml.db_writer import _get_conn
            session_date = _market_session_date(market).isoformat()
            with _get_conn() as conn:
                row = conn.execute(
                    """SELECT id FROM decisions
                       WHERE market=? AND ticker=? AND session_date=?
                         AND decision='BUY_SIGNAL' AND filled=0
                       ORDER BY id DESC LIMIT 1""",
                    (market, ticker, session_date),
                ).fetchone()
            if row:
                log.debug(f"[ML DB 복구] {ticker} decision_id={row[0]}")
                return row[0]
        except Exception:
            pass
        return None

    def _internal_total_equity_krw(self) -> float:
        return float(self.risk.cash + sum(
            p.get("qty", 0) * p.get("current_price", p.get("entry", 0))
            for p in self.risk.positions
        ))

    def _daily_pnl_pct(self) -> float:
        return float(self.risk.daily_pnl / max(self.risk.session_start_equity, 1) * 100.0)

    def _kis_total_equity_krw(self) -> float:
        """통합계좌 기준 KIS 총자산(KRW). KR+US 잔고 합산 후 실패 시 내부 계산값으로 폴백."""
        fallback = self._internal_total_equity_krw()
        kr_total, us_total_krw = 0.0, 0.0
        kr_ok, us_ok = False, False
        try:
            bal_kr = get_balance(self.token, market="KR")
            kr_total = float(bal_kr.get("cash", 0)) + float(bal_kr.get("total_eval", 0))
            kr_ok = True
        except Exception as e:
            log.warning(f"[누적자산 동기화] KR 잔고 조회 실패: {e}")
        try:
            bal_us = get_balance(self.token, market="US")
            us_cash_usd = float(bal_us.get("cash", 0))
            us_eval_usd = float(bal_us.get("total_eval", 0))
            us_total_krw = (us_cash_usd + us_eval_usd) * self.usd_krw_rate
            us_ok = True
        except Exception as e:
            log.debug(f"[누적자산 동기화] US 잔고 조회 실패: {e}")
        if not kr_ok and not us_ok:
            return fallback
        total = (kr_total if kr_ok else 0.0) + (us_total_krw if us_ok else 0.0)
        log.info(
            f"[누적자산 동기화] 총자산 {total:,.0f}원 "
            f"(KR {kr_total:,.0f}원 + US ${(us_total_krw/self.usd_krw_rate):.0f}≈{us_total_krw:,.0f}원)"
        )
        return total

    def _sync_runtime_with_broker(self):
        """장중에도 내부 포지션/현금을 브로커 잔고 기준으로 정렬."""
        try:
            bal_kr = get_balance(self.token, market="KR")
        except Exception as e:
            log.warning(f"[브로커 런타임 동기화] KR 잔고 조회 실패: {e}")
            return

        broker_us: dict = {}
        us_ok = False
        try:
            bal_us = get_balance(self.token, market="US")
            broker_us = {s["ticker"].upper(): s for s in bal_us.get("stocks", [])}
            us_ok = True
        except Exception as e:
            log.warning(f"[브로커 런타임 동기화] US 잔고 조회 실패 — US 포지션 검증 스킵: {e}")

        broker_kr = {s["ticker"]: s for s in bal_kr.get("stocks", [])}
        self._reconcile_pending_orders(broker_kr, broker_us)
        synced_positions = {}
        removed = []
        seen_keys = set()
        pending_by_key = {}
        for order in self.pending_orders:
            market = order.get("market", "KR")
            ticker = order.get("ticker", "")
            key = ticker.upper() if market == "US" else ticker
            pending_by_key[(market, key)] = order

        for pos in self.risk.positions:
            ticker = pos.get("ticker", "")
            market = self._ticker_market(ticker)
            key = ticker.upper() if market == "US" else ticker
            broker = broker_us if market == "US" else broker_kr

            # US 잔고 조회 실패 시 US 포지션 제거 금지
            if market == "US" and not us_ok:
                synced_positions[(market, key)] = pos
                seen_keys.add((market, key))
                continue

            broker_pos = broker.get(key)
            if not broker_pos or broker_pos.get("qty", 0) <= 0:
                removed.append(ticker)
                continue
            if broker_pos.get("qty", 0) != pos.get("qty", 0):
                log.warning(
                    f"[브로커 런타임 동기화] {ticker} 수량 보정 "
                    f"{pos.get('qty', 0)} -> {broker_pos.get('qty', 0)}"
                )
                pos["qty"] = broker_pos.get("qty", 0)
            native_avg = float(broker_pos.get("avg_price", 0) or 0)
            native_eval = float(broker_pos.get("eval_price", 0) or 0)
            keep_fill_price = pos.get("price_source") == "order_fill" and float(pos.get("display_avg_price", 0) or 0) > 0
            if market == "US":
                if not keep_fill_price:
                    pos["entry"] = native_avg * self.usd_krw_rate
                    pos["display_avg_price"] = native_avg
                pos["current_price"] = native_eval * self.usd_krw_rate
                pos["display_current_price"] = native_eval
                pos["display_currency"] = "USD"
            else:
                if not keep_fill_price:
                    pos["entry"] = native_avg
                    pos["display_avg_price"] = native_avg
                pos["current_price"] = native_eval
                pos["display_current_price"] = native_eval
                pos["display_currency"] = "KRW"
            if not keep_fill_price:
                pos["price_source"] = "broker_balance"
            existing = synced_positions.get((market, key))
            if existing is not None:
                log.warning(f"[브로커 런타임 동기화] 중복 포지션 병합: {ticker}")
            synced_positions[(market, key)] = pos
            seen_keys.add((market, key))

        for ticker, broker_pos in broker_kr.items():
            key = ("KR", ticker)
            if key in seen_keys or int(broker_pos.get("qty", 0) or 0) <= 0:
                continue
            synced_positions[key] = self._make_runtime_position_from_broker(
                ticker, "KR", broker_pos, template=pending_by_key.get(key)
            )
            seen_keys.add(key)
            log.warning(f"[브로커 런타임 동기화] KR 브로커 보유 포지션 주입: {ticker} {broker_pos.get('qty', 0)}주")

        for ticker, broker_pos in broker_us.items():
            key = ("US", ticker.upper())
            if key in seen_keys or int(broker_pos.get("qty", 0) or 0) <= 0:
                continue
            synced_positions[key] = self._make_runtime_position_from_broker(
                ticker.upper(), "US", broker_pos, template=pending_by_key.get(key)
            )
            seen_keys.add(key)
            log.warning(f"[브로커 런타임 동기화] US 브로커 보유 포지션 주입: {ticker} {broker_pos.get('qty', 0)}주")

        if removed:
            log.warning(f"[브로커 런타임 동기화] 브로커 미보유 포지션 제거: {removed}")

        self.risk.positions = list(synced_positions.values())
        self.risk.cash = float(bal_kr.get("cash", self.risk.cash))

    def _make_position_from_broker(self, order: dict, broker_pos: dict) -> dict:
        market = order.get("market", "KR")
        native_avg_price = float(
            order.get("filled_price_native", 0)
            or broker_pos.get("avg_price", 0)
            or order.get("raw_price", 0)   # 체결가/잔고 모두 0일 때 주문 접수가 폴백
            or 0
        )
        native_eval_price = float(broker_pos.get("eval_price", 0) or 0)
        avg_price = native_avg_price
        eval_price = native_eval_price
        if market == "US":
            avg_price *= self.usd_krw_rate
            eval_price *= self.usd_krw_rate
        qty = int(order.get("qty", 0))
        tp_pct = float(order.get("tp_pct", 0.0))
        sl_pct = float(order.get("sl_pct", 0.0))
        pos_name = (
            str(order.get("name", "") or "").strip()
            or str(broker_pos.get("name", "") or "").strip()
            or self._lookup_ticker_name(order.get("ticker", ""), market)
        )
        return {
            "ticker": order.get("ticker", ""),
            "name": pos_name,
            "entry": avg_price,
            "qty": qty,
            "current_price": eval_price or avg_price,
            "display_avg_price": native_avg_price,
            "display_current_price": native_eval_price or native_avg_price,
            "display_currency": "USD" if market == "US" else "KRW",
            "price_source": "order_fill" if order.get("filled_price_native") else "broker_balance",
            "order_no": order.get("order_no", ""),
            "fill_time": order.get("fill_time", ""),
            "strategy": order.get("strategy", "broker_fill"),
            "tp": avg_price * (1 + tp_pct),
            "sl": avg_price * (1 - sl_pct),
            "max_hold": int(order.get("max_hold", 1)),
            "held_days": 0,
            "entry_date": date.today().isoformat(),
            "trailing": False,
            "trail_sl": 0.0,
            "trail_pct": 0.03,
            "tp_triggered": False,
            "hold_advice": None,
            "tp_price": 0.0,
            "decision_id": order.get("decision_id"),   # ML DB 연동
        }

    def _lookup_ticker_name(self, ticker: str, market: str) -> str:
        raw_ticker = str(ticker or "").strip().upper() if market == "US" else str(ticker or "").strip()
        if not raw_ticker:
            return ""
        try:
            technicals = ((self.today_judgment.get("digest_raw") or {}).get("technicals") or {})
            info = technicals.get(raw_ticker) or technicals.get(str(ticker or "").strip()) or {}
            name = str((info or {}).get("name", "") or "").strip()
            if name and name != raw_ticker:
                return name
        except Exception:
            pass
        try:
            for fname in sorted(JUDGMENT_DIR.glob(f"*_{market}.json"), reverse=True):
                with open(fname, encoding="utf-8") as f:
                    rec = json.load(f)
                technicals = ((rec.get("digest_raw") or {}).get("technicals") or {})
                info = technicals.get(raw_ticker) or technicals.get(str(ticker or "").strip()) or {}
                name = str((info or {}).get("name", "") or "").strip()
                if name and name != raw_ticker:
                    return name
        except Exception:
            pass
        return ""

    def _reconcile_pending_orders(self, broker_kr: dict, broker_us: dict):
        if not self.pending_orders:
            return

        accounted = {}
        for pos in self.risk.positions:
            key = pos["ticker"].upper() if self._ticker_market(pos["ticker"]) == "US" else pos["ticker"]
            accounted[key] = accounted.get(key, 0) + int(pos.get("qty", 0))

        remaining = []
        filled = []
        for order in self.pending_orders:
            market = order.get("market", "KR")
            ticker = order.get("ticker", "")
            key = ticker.upper() if market == "US" else ticker
            broker = broker_us if market == "US" else broker_kr
            broker_pos = broker.get(key)
            broker_qty = int((broker_pos or {}).get("qty", 0) or 0)
            current_qty = accounted.get(key, 0)
            order_qty = int(order.get("qty", 0) or 0)
            fill = None
            if not order.get("filled_price_native"):
                try:
                    fill = (
                        get_order_fill_kr(
                            self.token,
                            order_no=order.get("order_no", ""),
                            ticker=ticker,
                            trade_date=datetime.now(KST).strftime("%Y%m%d"),
                        )
                        if market == "KR"
                        else get_order_fill_us(
                            self.token,
                            order_no=order.get("order_no", ""),
                            ticker=ticker,
                            created_at=order.get("created_at", ""),
                        )
                    )
                    if fill:
                        fp = float(fill.get("fill_price", 0) or 0)
                        if fp > 0:
                            order["filled_price_native"] = fp
                        order["fill_time"] = fill.get("order_time", "")
                except Exception as e:
                    label = "국내" if market == "KR" else "해외"
                    log.warning(f"[{label} 체결조회 실패] {ticker} 주문번호={order.get('order_no','')}: {e}")

            filled_by_broker = bool(broker_pos and broker_qty >= current_qty + order_qty)
            filled_by_query = bool(fill and int(fill.get("filled_qty", 0) or 0) >= max(order_qty, 1))
            if filled_by_broker or filled_by_query:
                if not broker_pos:
                    broker_pos = {
                        "ticker": ticker,
                        "qty": order_qty,
                        "avg_price": float(order.get("filled_price_native", 0) or order.get("raw_price", 0) or 0),
                        "eval_price": float(self.price_cache_raw.get(ticker, 0) or self.price_cache.get(ticker, 0) or order.get("filled_price_native", 0) or order.get("raw_price", 0) or 0),
                        "eval_profit": 0.0,
                        "profit_rate": 0.0,
                    }
                self.risk.positions.append(self._make_position_from_broker(order, broker_pos))
                accounted[key] = current_qty + order_qty
                # ML DB: 체결 확인 기록
                if _ML_DB_ENABLED and order.get("decision_id"):
                    try:
                        _ml_update_filled(order["decision_id"], "FILLED")
                    except Exception:
                        pass
                filled.append(order)
            else:
                remaining.append(order)

        if filled:
            for order in filled:
                market = order.get("market", "KR")
                ticker = order.get("ticker", "")
                key = ticker.upper() if market == "US" else ticker
                broker = broker_us if market == "US" else broker_kr
                broker_pos = broker.get(key, {})
                fill_px_native = float(
                    order.get("filled_price_native", 0)
                    or broker_pos.get("avg_price", 0)
                    or order.get("raw_price", 0)   # 체결가/잔고 모두 0일 때 주문 접수가 폴백
                    or 0
                )
                fill_px_risk = fill_px_native * self.usd_krw_rate if market == "US" else fill_px_native
                evt = {
                    "side": "buy",
                    "ticker": ticker,
                    "price": fill_px_native,
                    "qty": int(order.get("qty", 0) or 0),
                    "strategy": order.get("strategy", "broker_fill"),
                    "date": date.today().isoformat(),
                    "order_no": order.get("order_no", ""),
                    "price_source": "order_fill" if order.get("filled_price_native") else "broker_balance",
                    "currency": "USD" if market == "US" else "KRW",
                    "display_price": fill_px_native,
                    "risk_price": fill_px_risk,
                    "fill_time": order.get("fill_time", ""),
                }
                self.risk.trade_log.append(evt)
                self.risk.all_trade_log.append(evt)
                log.info(
                    f"[주문 체결 반영] {ticker} {order.get('qty')}주 "
                    f"| 주문번호={order.get('order_no', '')}"
                )
                fill_label = f"${fill_px_native:.4f}" if market == "US" else f"{fill_px_native:,.0f}원"
                send(
                    f"🟡 <b>[매수 체결 확인]</b> {ticker}\n"
                    f"{order.get('qty')}주 | 주문번호 {order.get('order_no', '')}\n"
                    f"브로커 평균단가 {fill_label}"
                )
            self.pending_orders = remaining
            self._save_pending_orders()

    def _add_pending_order(self, order: dict):
        market = order.get("market", "KR")
        ticker = order.get("ticker", "")
        if self._has_pending_order(ticker, market):
            self.pending_orders = [
                o for o in self.pending_orders
                if not (
                    o.get("market") == market and
                    ((o.get("ticker", "").upper() if market == "US" else o.get("ticker", "")) ==
                     (ticker.upper() if market == "US" else ticker))
                )
            ]
        self.pending_orders.append(order)
        self._save_pending_orders()

    def _clear_pending_orders_for_market(self, market: str, reason: str):
        if not self.pending_orders:
            return
        remaining = []
        removed = []
        for order in self.pending_orders:
            if order.get("market") == market:
                removed.append(order)
            else:
                remaining.append(order)
        if not removed:
            return
        self.pending_orders = remaining
        self._save_pending_orders()
        tickers = [f"{o.get('ticker', '')}({o.get('qty', 0)}주)" for o in removed]
        log.warning(f"[미체결 주문 정리] {market} {reason}: {tickers}")

    def _filter_trades_for_market(self, trades: list, market: str) -> list:
        filtered = []
        seen = set()
        for trade in trades or []:
            ticker = (trade.get("ticker", "") or "").strip()
            if not ticker or self._ticker_market(ticker) != market:
                continue
            side = trade.get("side", "")
            qty = int(trade.get("qty", 0) or 0)
            price = round(float(trade.get("price", 0) or 0), 6)
            pnl = round(float(trade.get("pnl", 0) or 0), 6)
            pnl_pct = round(float(trade.get("pnl_pct", 0) or 0), 6)
            reason = trade.get("reason", "") or ""
            strategy = trade.get("strategy", "") or ""
            trade_date = trade.get("date", date.today().isoformat())
            key = (trade_date, side, ticker.upper(), strategy, qty, price, pnl, pnl_pct, reason)
            if key in seen:
                continue
            seen.add(key)
            filtered.append({
                **trade,
                "date": trade_date,
                "side": side,
                "ticker": ticker,
                "strategy": strategy,
                "qty": qty,
                "price": price,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "reason": reason,
            })
        return filtered

    def _reset_us_order_cache(self):
        self._us_order_supported.clear()
        self._us_order_blocked.clear()

    def _mark_us_order_supported(self, ticker: str):
        normalized = ticker.upper()
        self._us_order_supported.add(normalized)
        self._us_order_blocked.pop(normalized, None)

    def _mark_us_order_blocked(self, ticker: str, reason: str):
        normalized = ticker.upper()
        self._us_order_blocked[normalized] = reason

    def _us_order_block_reason(self, ticker: str) -> str:
        return self._us_order_blocked.get(ticker.upper(), "")

    def _refresh_token(self):
        self.token = get_access_token()

    def _ticker_market(self, ticker: str) -> str:
        return "US" if ticker.isalpha() else "KR"

    def _price_to_krw(self, price: float, market: str) -> float:
        return price if market == "KR" else price * self.usd_krw_rate

    def _compute_order_price(self, side: str, market: str, raw_price: float) -> float:
        """Return 0 for market order, or adjusted limit price when enabled."""
        if market == "US":
            if raw_price <= 0:
                return 0
            if not self.enable_limit_order:
                return round(float(raw_price), 4)
            offset = max(0.0, self.limit_order_offset_bps / 10000.0)
            px = raw_price * (1.0 + offset) if side == "buy" else raw_price * (1.0 - offset)
            return round(max(px, 0.0001), 4)
        if not self.enable_limit_order:
            return 0
        if raw_price <= 0:
            return 0
        offset = max(0.0, self.limit_order_offset_bps / 10000.0)
        if side == "buy":
            px = raw_price * (1.0 + offset)
        else:
            px = raw_price * (1.0 - offset)
        if market == "KR":
            return int(max(1, round(px)))
        return round(px, 4)

    @staticmethod
    def _estimate_slippage_bps(price_info: dict) -> float:
        """
        Lightweight proxy from intraday range.
        Keeps default behavior unchanged unless feature flag is enabled.
        """
        try:
            price = float(price_info.get("price", 0))
            high = float(price_info.get("high", 0))
            low = float(price_info.get("low", 0))
            if price <= 0 or high <= 0 or low <= 0 or high < low:
                return 0.0
            range_bps = ((high - low) / price) * 10000.0
            return max(0.0, min(200.0, range_bps * 0.05))
        except Exception:
            return 0.0

    def _in_entry_blackout(self, market: str) -> bool:
        now = datetime.now(KST).time()
        no_new = HARD_RULES["no_new_entry_min"]
        no_late = HARD_RULES["close_before_min"]

        if market == "KR":
            open_t = dt_time(9, 0)
            close_t = dt_time(16, 0)
        else:
            # US session in KST: 22:30 ~ 05:00(next day)
            open_t = dt_time(22, 30)
            close_t = dt_time(5, 0)

        if market == "KR":
            # timezone-aware 비교 (KST 명시)
            start_block_end = (datetime.combine(date.today(), open_t, tzinfo=KST).timestamp() + no_new * 60)
            end_block_start = (datetime.combine(date.today(), close_t, tzinfo=KST).timestamp() - no_late * 60)
            now_ts = datetime.now(KST).timestamp()
            return now_ts < start_block_end or now_ts > end_block_start

        # US crossing midnight
        now_dt = datetime.now(KST)
        open_dt = datetime.combine(now_dt.date(), open_t, tzinfo=KST)
        close_dt = datetime.combine(now_dt.date(), close_t, tzinfo=KST)
        if now < close_t:
            open_dt = open_dt - timedelta(days=1)
        else:
            close_dt = close_dt + timedelta(days=1)

        start_block_end = open_dt.timestamp() + no_new * 60
        end_block_start = close_dt.timestamp() - no_late * 60
        now_ts = now_dt.timestamp()
        return now_ts < start_block_end or now_ts > end_block_start

    def _is_order_allowed_now(self, market: str) -> bool:
        """US는 22:30(KST) 이전 주문 불가 — 장전 주문 실패 반복 방지"""
        if market != "US":
            return True
        now_t = datetime.now(KST).time()
        # US 장: 22:30 ~ 05:00(익일)
        us_open = dt_time(22, 30)
        us_close = dt_time(5, 0)
        if now_t >= us_open or now_t < us_close:
            return True
        log.debug(f"[US 주문차단] 장 시작 전 ({now_t.strftime('%H:%M')} < 22:30) — 주문 보류")
        return False

    def _intraday_session_progress(self, market: str) -> float:
        """
        Returns session progress in [0, 1].
        KR: 09:00~15:30, US(KST): 22:30~05:00
        """
        now_dt = datetime.now(KST)
        if market == "KR":
            start_dt = datetime.combine(now_dt.date(), dt_time(9, 0), tzinfo=KST)
            end_dt = datetime.combine(now_dt.date(), dt_time(15, 30), tzinfo=KST)
        else:
            start_dt = datetime.combine(now_dt.date(), dt_time(22, 30), tzinfo=KST)
            end_dt = datetime.combine(now_dt.date(), dt_time(5, 0), tzinfo=KST)
            if now_dt.time() < dt_time(5, 0):
                start_dt -= timedelta(days=1)
            else:
                end_dt += timedelta(days=1)

        total_sec = max(1.0, (end_dt - start_dt).total_seconds())
        elapsed_sec = (now_dt - start_dt).total_seconds()
        return max(0.0, min(1.0, elapsed_sec / total_sec))

    def _market_open_anchor_dt(self, market: str) -> datetime:
        """현재 세션의 실제 장 시작 시각(KST)을 반환한다."""
        now_dt = datetime.now(KST)
        if market == "KR":
            return datetime.combine(now_dt.date(), dt_time(9, 0), tzinfo=KST)
        open_dt = datetime.combine(now_dt.date(), dt_time(22, 30), tzinfo=KST)
        if now_dt.time() < dt_time(5, 0):
            open_dt -= timedelta(days=1)
        return open_dt

    def _market_elapsed_min(self, market: str) -> float:
        """실제 장 시작 시각 기준 경과 분. 재시작과 무관하게 고정된다."""
        now_dt = datetime.now(KST)
        open_dt = self._market_open_anchor_dt(market)
        return max(0.0, (now_dt - open_dt).total_seconds() / 60.0)

    def _project_intraday_volume(self, market: str, volume: float) -> float:
        """
        Project current intraday volume to a full-session estimate.
        Keeps early-session overprojection bounded.
        """
        try:
            volume = float(volume or 0)
        except Exception:
            return 0.0
        if volume <= 0:
            return 0.0

        progress = self._intraday_session_progress(market)
        if progress <= 0 or progress >= 1:
            return volume

        # Floor progress to 20% and cap multiplier to 3x to avoid open noise.
        effective_progress = max(progress, 0.20)
        multiplier = min(3.0, 1.0 / effective_progress)
        return volume * multiplier

    _SELL_FAIL_COOLDOWN_SEC = 90  # 매도 실패 후 재시도 억제 시간

    def _process_exit_candidates(self):
        candidates = self.risk.get_exit_candidates()
        for cand in candidates:
            if float(cand.get("exit_price") or 0) <= 0:
                log.error(f"[exit skip] {cand['ticker']} exit_price={cand.get('exit_price')} <= 0 — 매도 차단 (가격 조회 실패)")
                continue
            market = self._ticker_market(cand["ticker"])
            if self.current_market and market != self.current_market:
                continue
            if not self._is_order_allowed_now(market):
                log.debug(f"[exit skip] {cand['ticker']} 장 시작 전 — 매도 보류")
                continue
            # 매도 실패 쿨다운 체크
            _fail_ts = self._sell_fail_at.get(cand["ticker"], 0)
            if time.time() - _fail_ts < self._SELL_FAIL_COOLDOWN_SEC:
                remaining = int(self._SELL_FAIL_COOLDOWN_SEC - (time.time() - _fail_ts))
                log.debug(f"[exit skip] {cand['ticker']} 매도 실패 쿨다운 {remaining}초 남음")
                continue

            # ── 거래 중지 체크 (KR만) ──────────────────────────────────────
            if market == "KR" and is_trading_halted(cand["ticker"], self.token):
                self._sell_fail_at[cand["ticker"]] = time.time() + 600  # 10분 쿨다운
                log.warning(f"[거래 중지] {cand['ticker']} 매도 불가 → 10분 후 재확인")
                continue

            # ── TP 도달 → 트레일링 스탑 처리 ──────────────────────────────
            if cand["reason"] == "tp_check":
                if self.enable_trailing_stop:
                    self._handle_tp_trailing(cand, market)
                else:
                    # trailing 비활성화 → 기존처럼 즉시 청산
                    self._execute_sell(cand, market, reason="take_profit")
                    self._block_entry(cand["ticker"], _TP_COOLDOWN_MIN, "take_profit")
                continue

            self._execute_sell(cand, market, reason=cand["reason"])
            # 손절/트레일링 청산 후 재진입 쿨다운
            if cand["reason"] in ("stop_loss", "trail_stop"):
                self._block_entry(cand["ticker"], _STOP_COOLDOWN_MIN, cand["reason"])

    def _handle_tp_trailing(self, cand: dict, market: str):
        """TP 도달 시 트레일링 스탑 전환 (분석가 합의 옵션)"""
        ticker     = cand["ticker"]
        trail_pct  = self.trailing_stop_pct

        hold_advice = None
        if self.enable_trailing_analyst:
            # 분석가 3명에게 HOLD/SELL 물어봄
            try:
                from minority_report.hold_advisor import ask as advisor_ask
                digest = self.today_judgment.get("digest_prompt", "")
                advice = advisor_ask(cand, market, digest)
                if advice["action"] == "SELL":
                    log.info(f"[TP→분석가합의:SELL] {ticker} — 즉시 청산")
                    send(f"🔴 <b>[TP→분석가 SELL]</b> {ticker}\n분석가 합의: 즉시 청산")
                    self._execute_sell(cand, market, reason="tp_analyst_sell",
                                       hold_advice=advice)
                    return
                trail_pct  = advice["trail_pct"]
                hold_advice = advice
                log.info(f"[TP→분석가합의:HOLD] {ticker} trail={trail_pct:.2%}")
                send(
                    f"📈 <b>[TP→분석가 HOLD]</b> {ticker}\n"
                    f"트레일링 스탑 {trail_pct*100:.1f}% 활성화"
                )
            except Exception as e:
                log.warning(f"[hold_advisor] 오류 → trail 기본값 적용: {e}")
        else:
            log.info(f"[TP→트레일링] {ticker} trail={trail_pct:.2%} (분석가 생략)")
            send(
                f"📈 <b>[TP 도달 → 트레일링]</b> {ticker}\n"
                f"트레일링 스탑 {trail_pct*100:.1f}% 활성화"
            )

        self.risk.activate_trailing(ticker, trail_pct, hold_advice=hold_advice)

    def _execute_sell(self, cand: dict, market: str, reason: str,
                      hold_advice: dict = None):
        """실제 매도 실행 + 텔레그램 알림 + hold_advisor 결과 기록"""
        raw_px   = self.price_cache_raw.get(cand["ticker"], cand["exit_price"])
        if float(cand.get("exit_price", 0) or 0) <= 0 or float(raw_px or 0) <= 0:
            log.error(f"sell skipped [{cand['ticker']}]: invalid exit/raw price exit={cand.get('exit_price')} raw={raw_px}")
            return
        order_px = self._compute_order_price("sell", market, float(raw_px))
        precheck = precheck_order(cand["ticker"], cand["qty"], order_px, "sell", self.token, market=market)
        if not precheck.get("ok"):
            has_pending_buy = any(
                o.get("market") == market and
                o.get("ticker") == cand["ticker"] and
                int(o.get("qty", 0) or 0) > 0
                for o in self.pending_orders
            )
            if precheck.get("reason") == "insufficient_holding" and not has_pending_buy:
                precheck = precheck_order(
                    cand["ticker"], cand["qty"], order_px, "sell",
                    self.token, market=market, force_refresh=True
                )
            log.error(f"sell precheck failed [{cand['ticker']}]: {precheck.get('msg','주문 불가')}")
            self._record_decision_event(
                market, "sell_failed", cand["ticker"],
                strategy=cand.get("strategy", ""),
                qty=cand.get("qty", 0),
                price_native=order_px,
                price_krw=cand.get("exit_price", 0),
                reason=reason,
                detail=precheck.get("msg", "주문 불가"),
            )
            if precheck.get("reason") == "insufficient_holding" and not has_pending_buy:
                before = len(self.risk.positions)
                self.risk.positions = [p for p in self.risk.positions if p.get("ticker") != cand["ticker"]]
                after = len(self.risk.positions)
                if before != after:
                    log.warning(f"[브로커 미보유 정리] {cand['ticker']} 내부 포지션 제거")
                    self._save_positions()
                    self._write_live_status(market, force=True)
            return
        result   = place_order(cand["ticker"], cand["qty"], order_px, "sell", self.token, market=market)
        if not result["success"]:
            log.error(f"sell order failed [{cand['ticker']}]: {result['msg']}")
            self._record_decision_event(
                market, "sell_failed", cand["ticker"],
                strategy=cand.get("strategy", ""),
                qty=cand.get("qty", 0),
                price_native=order_px,
                price_krw=cand.get("exit_price", 0),
                reason=reason,
                detail=result.get("msg", ""),
                order_no=result.get("order_no", ""),
            )
            if "잔고내역이 없습니다" in (result.get("msg", "") or ""):
                # 브로커 실제 잔고 확인 후에만 포지션 제거 (VTS API 오류로 인한 오제거 방지)
                broker_has = False
                try:
                    bal_chk = get_balance(self.token, market=market, force_refresh=True)
                    broker_tickers = {s["ticker"].upper() if market == "US" else s["ticker"]
                                      for s in bal_chk.get("stocks", [])}
                    chk_key = cand["ticker"].upper() if market == "US" else cand["ticker"]
                    broker_has = chk_key in broker_tickers
                except Exception as _be:
                    log.warning(f"[브로커 확인 실패] {cand['ticker']}: {_be} → 포지션 유지")
                    broker_has = True  # 확인 실패 시 안전하게 유지

                if broker_has:
                    self._sell_fail_at[cand["ticker"]] = time.time()  # 쿨다운 시작
                    log.warning(f"[매도 실패 무시] {cand['ticker']} 브로커에 보유 확인 → 포지션 유지, {self._SELL_FAIL_COOLDOWN_SEC}초 후 재시도")
                else:
                    before = len(self.risk.positions)
                    self.risk.positions = [p for p in self.risk.positions if p.get("ticker") != cand["ticker"]]
                    after = len(self.risk.positions)
                    if before != after:
                        log.warning(f"[브로커 미보유 정리] {cand['ticker']} 브로커 미보유 확인 → 내부 포지션 제거")
                        self._save_positions()
                        self._write_live_status(market, force=True)
            return
        log.info(f"[{'PAPER' if self.is_paper else 'LIVE'} SELL] {cand['ticker']} {cand['qty']}@{order_px:,} | 주문번호={result.get('order_no','')}")

        # hold_advice: 분석가 SELL 즉시 청산 시 전달된 advice
        # cand에 저장된 hold_advice(포지션에서 온 것)도 확인
        advice_used = hold_advice or cand.get("hold_advice")

        ex = self.risk.close_position(cand["ticker"], cand["exit_price"], reason)
        if not ex:
            return
        # 당일 손절 카운터 — 연속 손절 시 이후 진입 size_mult 자동 축소
        if reason in ("stop_loss", "trail_stop"):
            _sl_mkt = market
            self._daily_sl_count[_sl_mkt] = self._daily_sl_count.get(_sl_mkt, 0) + 1
            _sl_cnt = self._daily_sl_count[_sl_mkt]
            if _sl_cnt >= 3:
                log.warning(f"[당일 손절 {_sl_mkt}] {_sl_cnt}회 — 신규 진입 중단")
            elif _sl_cnt >= 2:
                log.warning(f"[당일 손절 {_sl_mkt}] {_sl_cnt}회 — 이후 size × 0.6")
            else:
                log.info(f"[당일 손절 {_sl_mkt}] {_sl_cnt}회 — 이후 size × 0.8")
        # ML DB: 거래 결과 기록
        if _ML_DB_ENABLED and ex.get("decision_id"):
            try:
                _ml_update_outcome(
                    ex["decision_id"],
                    entry_price=ex.get("entry", 0),
                    exit_price=ex.get("exit_price", 0),
                    exit_reason=reason,
                    hold_days=ex.get("held_days", 0),
                    pnl_pct=ex.get("pnl_pct", 0),
                )
            except Exception:
                pass
        # ticker_selection_log: 수익 결과 기록
        try:
            tsdb.update_pnl(market, ex["ticker"], ex.get("pnl_pct", 0), reason)
        except Exception:
            pass
        raw_exit = self.price_cache_raw.get(ex["ticker"], ex["exit_price"])
        self._record_decision_event(
            market, "sell_filled", ex["ticker"],
            strategy=ex.get("strategy", ""),
            qty=ex.get("qty", 0),
            price_native=raw_exit,
            price_krw=ex.get("exit_price", 0),
            reason=reason,
            detail=f"entry={ex.get('entry', 0):,.0f} hold_days={ex.get('held_days', 0)}",
            order_no=result.get("order_no", ""),
            pnl_krw=ex.get("pnl", 0),
            pnl_pct=ex.get("pnl_pct", 0),
        )
        pnl_alert(ex["ticker"], ex["pnl_pct"], int(ex["pnl"]), reason)
        trade_alert("sell", ex["ticker"], ex["qty"], int(raw_exit), ex["strategy"], 0, 0, reason=reason, market=market)
        try:
            BrainDB.update_strategy_performance(
                market, ex.get("strategy", "unknown"),
                ex.get("pnl_pct", 0), ex.get("pnl", 0) > 0
            )
        except Exception as e:
            log.warning(f"strategy brain update failed: {e}")

        # ── hold_advisor 결과 기록 ─────────────────────────────────────────────
        if advice_used:
            self._record_hold_advisor_outcome(ex, market, advice_used)

    def _record_hold_advisor_outcome(self, ex: dict, market: str, advice: dict):
        """
        청산 완료 후 hold_advisor 결정의 성공/실패 판정 및 기록.
        - HOLD 결정: exit_price > tp_price → 추가 수익 실현 → 성공
        - SELL 결정: TP 즉시 실현 자체 → 성공 (기회비용은 사후 비교)
        """
        try:
            decision    = advice.get("action", "SELL")
            tp_price    = ex.get("tp_price", 0.0) or ex.get("tp", 0.0)
            exit_price  = ex.get("exit_price", 0.0)
            entry_price = ex.get("entry", 0.0)

            if decision == "HOLD":
                # HOLD 성공: 청산가 > TP 도달가 (트레일 덕에 더 벌었음)
                success = exit_price > tp_price if tp_price > 0 else (ex.get("pnl", 0) > 0)
                # 추가 수익: TP 대비 청산가 차이
                extra_pnl_pct = ((exit_price / tp_price - 1) * 100) if tp_price > 0 else 0.0
            else:  # SELL (즉시 청산)
                # TP 도달 → 즉시 청산 자체는 수익 실현
                success = ex.get("pnl", 0) > 0
                extra_pnl_pct = ex.get("pnl_pct", 0.0)

            # brain.json 누적
            BrainDB.update_hold_advisor_performance(
                market, ex["ticker"], decision, success, extra_pnl_pct
            )

            # JSONL outcome 업데이트
            self._update_hold_advisor_jsonl_outcome(
                ex["ticker"], decision, success, exit_price, extra_pnl_pct
            )

            result_icon = "✅" if success else "❌"
            log.info(
                f"[hold_advisor 결과] {ex['ticker']} {decision} → "
                f"{result_icon} {'성공' if success else '실패'} "
                f"extra_pnl={extra_pnl_pct:+.2f}%"
            )
        except Exception as e:
            log.warning(f"[hold_advisor] 결과 기록 실패: {e}")

    def _update_hold_advisor_jsonl_outcome(
        self, ticker: str, decision: str, success: bool,
        exit_price: float, extra_pnl_pct: float
    ):
        """오늘자 decisions JSONL에서 해당 종목 미완료 레코드에 outcome을 기록"""
        try:
            from runtime_paths import get_runtime_path as _grp
            from datetime import datetime as _dt
            log_dir  = _grp("logs", "hold_advisor", make_parents=False)
            today    = _dt.now().strftime("%Y-%m-%d")
            log_file = log_dir / f"decisions_{today}.jsonl"
            if not log_file.exists():
                return

            lines = log_file.read_text(encoding="utf-8").splitlines()
            updated = []
            patched = False
            for line in reversed(lines):
                if not patched:
                    try:
                        rec = json.loads(line)
                        if rec.get("ticker") == ticker and rec.get("outcome") is None:
                            rec["outcome"] = {
                                "success":       success,
                                "exit_price":    exit_price,
                                "extra_pnl_pct": round(extra_pnl_pct, 4),
                                "decision":      decision,
                            }
                            line = json.dumps(rec, ensure_ascii=False)
                            patched = True
                    except Exception:
                        pass
                updated.append(line)

            log_file.write_text(
                "\n".join(reversed(updated)) + "\n", encoding="utf-8"
            )
        except Exception as e:
            log.warning(f"[hold_advisor] JSONL outcome 업데이트 실패: {e}")

    def _backfill_missed_postmortem(self, market: str):
        """재시작·비정상종료로 session_close 미실행 → actual_result 공백인 날 postmortem 소급"""
        for delta in range(1, 4):
            target_date = (date.today() - timedelta(days=delta)).isoformat()
            fname = JUDGMENT_DIR / f"{target_date.replace('-', '')}_{market}.json"
            if not fname.exists():
                continue
            try:
                rec = json.load(open(fname, encoding="utf-8"))
            except Exception:
                continue
            if not rec.get("judgments"):
                continue
            if rec.get("actual_result"):  # 이미 있으면 스킵
                continue
            sells = [t for t in (rec.get("trades") or []) if t.get("side") == "sell"]
            total_pnl_krw = sum(t.get("pnl", 0) for t in sells)
            start_eq = self.risk.session_start_equity or self.risk.cash or 1
            actual = {
                "market_change": get_index_change(market),
                "pnl_pct":  total_pnl_krw / start_eq * 100 if sells else 0.0,
                "pnl_krw":  int(total_pnl_krw),
                "win":      total_pnl_krw > 0,
                "trades":   len(sells),
                "cumulative": 0,
            }
            log.info(f"[postmortem 소급] {target_date} {market} — session_close 누락 감지, 소급 실행")
            try:
                run_postmortem(
                    market, target_date, rec, actual,
                    rec.get("digest_prompt", ""),
                    trade_log=rec.get("trades", []),
                    decision_event_log=rec.get("decision_events", []),
                )
                rec["actual_result"] = actual
                with open(fname, "w", encoding="utf-8") as f:
                    json.dump(rec, f, ensure_ascii=False, indent=2)
                log.info(f"[postmortem 소급 완료] {target_date} {market}")
            except Exception as e:
                log.warning(f"[postmortem 소급 실패] {target_date} {market}: {e}")

    def session_open(self, market: str):
        if not self._enter_market_task(market, "session_open"):
            return
        log.info("=" * 50)
        log.info(f"[{market}] session_open")
        self._refresh_claude_control()
        self._backfill_missed_postmortem(market)

        # ── 휴장일 체크 (주말 + 공휴일) ─────────────────────────────────────
        if not _is_trading_day(market):
            log.info(f"[{market}] 휴장일 — 세션 스킵")
            self.session_active = False
            self.current_market = None
            self._leave_market_task(market, "session_open")
            return

        if market == "US" and not self.is_paper:
            log.warning("[US] live mode is not supported yet. session skipped.")
            self.session_active = False
            self.current_market = None
            self._leave_market_task(market, "session_open")
            return

        self._refresh_token()
        self.session_active = True
        self.current_market = market
        self.risk.market = market          # 수수료율 시장에 맞게 설정
        self.tuning_count = 0
        self._session_events = []          # 세션 이벤트 초기화
        self._entry_blocked = {}           # 새 세션 시작 시 쿨다운 초기화
        self.decision_event_log = []
        self._daily_sl_count[market] = 0   # 당일 손절 카운터 초기화
        self._normalize_pending_orders()
        self._save_pending_orders()
        if market == "US":
            self._reset_us_order_cache()
        self._sync_runtime_with_broker()
        self.risk.reset_daily_state(clear_trade_log=True)
        self.risk.increment_holding_days()

        # ── USD/KRW 환율 자동 갱신 ────────────────────────────────────────────
        try:
            new_rate = get_usd_krw()
            if new_rate > 100:
                old_rate = self.usd_krw_rate
                self.usd_krw_rate = new_rate
                log.info(f"[환율 갱신] USD/KRW {old_rate:,.0f} → {new_rate:,.2f}원")
        except Exception as e:
            log.warning(f"[환율 갱신 실패] {e} — 이전 값 {self.usd_krw_rate:,.0f}원 유지")

        # 이월 포지션 현재가 갱신 (어제 종가 → 오늘 시가 방향으로 업데이트)
        for pos in self.risk.positions:
            if self._ticker_market(pos["ticker"]) == market:
                try:
                    price_info = get_price(pos["ticker"], self.token, market=market)
                    raw_price = price_info["price"]
                    self.price_cache_raw[pos["ticker"]] = raw_price
                    self.price_cache[pos["ticker"]] = self._price_to_krw(raw_price, market)
                except Exception as e:
                    log.warning(f"이월 포지션 시가 조회 실패 [{pos['ticker']}]: {e}")
        self.risk.update_prices(self.price_cache)
        if self.risk.positions:
            updated_prices = []
            for p in self.risk.positions:
                if self._ticker_market(p["ticker"]) != market:
                    continue
                display_px = float(
                    p.get("display_current_price", p.get("current_price", 0)) or 0
                )
                internal_px = float(p.get("current_price", 0) or 0)
                if market == "US":
                    updated_prices.append(
                        f"{p['ticker']} ${display_px:.4f} (내부KRW {internal_px:,.0f}원)"
                    )
                else:
                    updated_prices.append(f"{p['ticker']} {internal_px:,.0f}원")
            if updated_prices:
                log.info(f"[이월 포지션 현재가 갱신] {updated_prices}")

        today = date.today().strftime("%Y-%m-%d")

        pre_candidates = None
        universe_tickers = None
        if self.enable_dynamic_universe:
            if market == "KR":
                pre_candidates = screen_market_kr(self.token)
            else:
                pre_candidates = screen_market_us()
            if pre_candidates:
                snapshot = build_universe_from_candidates(
                    market=market,
                    target_date=today,
                    candidates=pre_candidates,
                    config=UniverseConfig(top_n=max(3, self.dynamic_universe_top_n)),
                    source="runtime_screen",
                    core_tickers=get_core_tickers(market),
                )
                save_universe_snapshot(snapshot)
                self.today_universe[market] = snapshot
                universe_tickers = snapshot.get("tickers", [])
                log.info(
                    f"[유니버스] {market} 후보 {len(pre_candidates)} -> "
                    f"{len(universe_tickers)}개"
                )
            else:
                snapshot = load_universe_snapshot(market, today)
                if snapshot.get("tickers"):
                    self.today_universe[market] = snapshot
                    universe_tickers = snapshot.get("tickers", [])
                    log.info(f"[유니버스] {market} 스냅샷 로드 {len(universe_tickers)}개")

        # ── 재시작 시 당일 판단 재사용 ────────────────────────────────────────
        live_path = JUDGMENT_DIR / f"{today.replace('-', '')}_{market}.json"
        reused = False
        digest = None
        digest_prompt = ""
        if live_path.exists():
            try:
                with open(live_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                saved_mode = saved.get("mode", "")
                if saved_mode not in ("historical_sim",) and saved.get("judgments") and saved.get("consensus"):
                    self.today_judgment = {
                        "date": today,
                        "market": market,
                        "judgments": saved["judgments"],
                        "consensus": saved["consensus"],
                        "digest_prompt": saved.get("digest_prompt", ""),
                        "tickers": saved.get("tickers", []),
                        "universe_tickers": saved.get("universe_tickers", []),
                        # debate 필드 복원 (파인튜닝 데이터 연속성 유지)
                        "round1_judgments": saved.get("round1_judgments", {}),
                        "debate_changes": saved.get("debate_changes", []),
                    }
                    self.today_tickers[market] = saved.get("tickers", [])
                    judgments = saved["judgments"]
                    consensus = saved["consensus"]
                    digest_prompt = saved.get("digest_prompt", "")
                    reused = True
                    log.info(f"[재시작 판단 재사용] {today} {market} consensus={consensus['mode']}")
            except Exception as e:
                log.warning(f"저장된 판단 로드 실패: {e}")

        if not reused:
            digest = build_kr_digest(today, universe_tickers=universe_tickers) if market == "KR" \
                else build_us_digest(today, universe_tickers=universe_tickers)
            digest_prompt = digest_to_prompt(digest)

            brain_summary = BrainDB.generate_prompt_summary(market)
            brain_data = BrainDB.load()
            correction = json.dumps(brain_data.get("correction_guide", {}).get(market, {}), ensure_ascii=False)

            portfolio_info = {
                "cash":          round(self.risk.cash),
                "total_equity":  round(self._kis_total_equity_krw()),
                "max_order_krw": round(self.risk.max_order_krw),
                "n_positions":   len(self.risk.positions),
                "max_positions": HARD_RULES["max_positions"],
            }
            judgments = get_three_judgments(
                digest_prompt, brain_summary, correction,
                market=market, portfolio_info=portfolio_info,
            )
            consensus = build_consensus(judgments, market=market)

            # ── VIX 동적 사이즈 보정 (US 전용) ───────────────────────────────
            if market == "US":
                vix = float((digest.get("context") or {}).get("vix") or 0)
                if vix > 0:
                    vix_mult = 1.0
                    for threshold, mult in _VIX_SIZE_TIERS:
                        if vix >= threshold:
                            vix_mult = mult
                            break
                    if vix_mult < 1.0:
                        orig_size = consensus["size"]
                        consensus = dict(consensus)
                        consensus["size"] = max(0, int(orig_size * vix_mult))
                        consensus["vix_mult"] = vix_mult
                        log.info(
                            f"[VIX={vix:.1f}] 사이즈 축소 x{vix_mult} "
                            f"{orig_size}% → {consensus['size']}%"
                        )

            judgment_log.info(
                f"[open {today} {market}] consensus={consensus['mode']} size={consensus['size']}%",
                extra={"extra": {
                    "event": "session_open_judgment",
                    "date": today,
                    "market": market,
                    "consensus": consensus,
                    "judgments": judgments,
                }},
            )

            # 오늘 집중 종목: 스크리너 → 히스토리 필터 → Claude 선택
            log.info(f"[스크리너] {market} 시장 스캔 중...")
            if market == "KR":
                candidates = screen_market_kr(self.token)
            else:
                candidates = screen_market_us()
            self._log_screen_candidates(market, candidates, "session_open")
            candidates = self._prefill_history_sync(candidates, market)
            candidates = self._filter_candidates_by_history(candidates, market)
            log.info(f"[스크리너] {market} 후보 {len(candidates)}개 → Claude 선택 중...")
            selected, sel_reasons = select_tickers(market, digest_prompt, consensus["mode"], candidates)
            self.today_tickers[market] = selected
            self.today_ticker_reasons[market] = sel_reasons or {}
            log.info(f"[종목선택 확정] {market}: {selected}")
            try:
                _tsdb_ids = tsdb.insert_batch(
                    today, market, "initial", selected, candidates, sel_reasons, consensus["mode"]
                )
                self._tsdb_selection_ids[market] = _tsdb_ids
            except Exception as _te:
                log.warning(f"[tsdb] insert_batch(initial) failed: {_te}")
                self._tsdb_selection_ids[market] = {}
            judgment_log.info(
                f"[select {today} {market}] {selected}",
                extra={"extra": {
                    "event": "ticker_selection",
                    "date": today,
                    "market": market,
                    "selected": selected,
                    "consensus_mode": consensus["mode"],
                }},
            )
            excluded = [c.get("ticker","") for c in candidates if c.get("ticker","") not in selected]
            _mode_order_limit = self.risk.calc_order_budget(consensus.get("size", 50))
            watchlist_alert(market, consensus["mode"], selected, sel_reasons, excluded, trigger="session_open",
                            mode_order_limit_krw=_mode_order_limit)

            # _debate 메타데이터 분리 후 today_judgment에 함께 보관
            debate_meta = judgments.pop("_debate", {})
            self.today_judgment = {
                "date": today,
                "market": market,
                "judgments": judgments,
                "consensus": consensus,
                "digest_prompt": digest_prompt,
                "digest_raw": digest,          # VIX 등 원본 데이터 재판단 경로에서 참조
                "tickers": selected,
                "universe_tickers": [c.get("ticker") for c in candidates if c.get("ticker")],
                "round1_judgments": debate_meta.get("r1", {}),
                "debate_changes":   debate_meta.get("changes", []),
            }
        else:
            # 판단은 재사용하되 종목은 항상 새로 스크리닝
            # (reused=True 시 전날 저장된 종목이 그대로 고정되는 문제 방지)
            log.info(f"[종목 재스크리닝] {market} 판단 재사용 + 종목만 새로 선택 (mode={consensus['mode']})")
            if market == "KR":
                fresh_candidates = screen_market_kr(self.token)
            else:
                fresh_candidates = screen_market_us()
            if fresh_candidates:
                self._log_screen_candidates(market, fresh_candidates, "session_reuse_rescreen")
                fresh_candidates = self._prefill_history_sync(fresh_candidates, market)
                fresh_candidates = self._filter_candidates_by_history(fresh_candidates, market)
                fresh_selected, fresh_reasons = select_tickers(market, digest_prompt, consensus["mode"], fresh_candidates)
                self.today_tickers[market] = fresh_selected
                self.today_ticker_reasons[market] = fresh_reasons or {}
                self.today_judgment["tickers"] = fresh_selected
                try:
                    _tsdb_ids = tsdb.insert_batch(
                        today, market, "rescreen", fresh_selected, fresh_candidates, fresh_reasons, consensus["mode"]
                    )
                    self._tsdb_selection_ids[market].update(_tsdb_ids)
                except Exception as _te:
                    log.warning(f"[tsdb] insert_batch(rescreen) failed: {_te}")
                self.today_judgment["universe_tickers"] = [
                    c.get("ticker") for c in fresh_candidates if c.get("ticker")
                ]
                log.info(f"[종목 재선택 완료] {market}: {fresh_selected}")
                judgment_log.info(
                    f"[rescreen {today} {market}] {fresh_selected}",
                    extra={"extra": {
                        "event": "ticker_rescreen",
                        "date": today,
                        "market": market,
                        "selected": fresh_selected,
                        "consensus_mode": consensus["mode"],
                    }},
                )
                fresh_excluded = [c.get("ticker", "") for c in fresh_candidates if c.get("ticker", "") not in fresh_selected]
                watchlist_alert(market, consensus["mode"], fresh_selected, fresh_reasons, fresh_excluded, trigger="rescreen")
            else:
                log.warning(f"[종목 재스크리닝] 후보 없음 → 기존 종목 유지: {self.today_tickers.get(market, [])}")

        # 대시보드가 장 중에도 오늘 판단을 볼 수 있도록 session_open 직후 즉시 기록
        self._persist_live_judgment(market)

        selected = self.today_tickers.get(market, [])
        try:
            balance = get_balance(self.token, market=market)
        except Exception as e:
            log.warning(f"balance lookup failed [{market}]: {e}")
            balance = {"stocks": [], "total_eval": 0, "cash": 0, "total_profit": 0, "profit_rate": 0.0}
        if not reused:
            morning_briefing(market, judgments, consensus, balance, digest,
                             round1_judgments=debate_meta.get("r1", {}),
                             debate_changes=debate_meta.get("changes", []))
        else:
            log.info(f"[재시작] 브리핑 스킵 (당일 판단 재사용) consensus={consensus['mode']}")
        log.info(f"consensus: {consensus['mode']} size={consensus['size']}%")

        self.ws = KISWebSocket(
            self.token, selected,
            on_tick=self._on_tick,
            on_notice=self._on_fill_notice,
            market=market,
        )
        self.ws.start()
        self._session_open_at[market] = time.time()  # startup guard 기준점
        # 새 세션 시작 시 장중 H/L 초기화
        for _t in selected:
            self._intraday_high.pop(_t, None)
            self._intraday_low.pop(_t, None)
        self._leave_market_task(market, "session_open")

    def _on_tick(self, data: dict):
        ticker = data["ticker"]
        market = self._ticker_market(ticker)
        raw_price = data["price"]
        if not raw_price or raw_price <= 0:
            log.warning(f"[WS tick] {ticker} invalid price={raw_price} — 무시")
            return
        self.price_cache_raw[ticker] = raw_price
        self.price_cache[ticker] = self._price_to_krw(raw_price, market)
        self.risk.update_prices(self.price_cache)
        # 장중 고가/저가 누적 — 당일봉 단일가봉 탈출용
        if raw_price > self._intraday_high.get(ticker, 0):
            self._intraday_high[ticker] = raw_price
        _cur_low = self._intraday_low.get(ticker, float("inf"))
        if raw_price < _cur_low:
            self._intraday_low[ticker] = raw_price
        self._process_exit_candidates()

    def _on_fill_notice(self, event: dict):
        """KIS WS 체결통보 수신 → pending → filled 즉시 전환 (KR + US)"""
        order_no    = event.get("order_no", "")
        ticker      = event.get("ticker", "")
        filled_qty  = int(event.get("filled_qty", 0) or 0)
        filled_price= float(event.get("filled_price", 0) or 0)
        filled_time = event.get("filled_time", "")
        side        = event.get("side", "buy")

        if side != "buy" or not order_no or filled_qty <= 0:
            return

        # pending_orders에서 order_no 매칭
        matched = None
        for order in self.pending_orders:
            if str(order.get("order_no", "")).strip() == str(order_no).strip():
                matched = order
                break

        if not matched:
            log.debug(f"[WS 체결통보] {ticker} 주문번호={order_no} → pending 미매칭 (이미 처리됨)")
            return

        market = matched.get("market", "KR")
        matched["filled_price_native"] = filled_price
        matched["fill_time"] = filled_time

        # broker_pos 구성 (WS 체결가 기준)
        broker_pos = {
            "ticker": ticker,
            "qty": filled_qty,
            "avg_price": filled_price,
            "eval_price": filled_price,
            "eval_profit": 0.0,
            "profit_rate": 0.0,
        }
        pos = self._make_position_from_broker(matched, broker_pos)
        self.risk.positions.append(pos)

        # pending에서 제거
        self.pending_orders = [o for o in self.pending_orders if o is not matched]
        self._save_pending_orders()
        self._save_positions()

        # ML DB 업데이트
        if _ML_DB_ENABLED and matched.get("decision_id"):
            try:
                from ml.db_writer import update_filled as _ml_update_filled
                _ml_update_filled(matched["decision_id"], "FILLED")
            except Exception:
                pass

        # 텔레그램 체결 확인 (KR: 원 / US: 달러)
        if market == "US":
            fill_label = f"${filled_price:.4f}"
            log_price  = f"${filled_price:.4f}"
        else:
            fill_label = f"{filled_price:,.0f}원"
            log_price  = f"{filled_price:,.0f}원"
        send(
            f"🟡 <b>[매수 체결 확인]</b> {ticker}\n"
            f"{filled_qty}주 | 주문번호 {order_no}\n"
            f"체결가 {fill_label} (WS 실시간)"
        )
        log.info(
            f"[WS 체결통보] {ticker} {filled_qty}주 @{log_price} "
            f"| 주문번호={order_no} | market={market} | pending→filled 완료"
        )

    def _entry_scan_interval_sec(self, market: str) -> int:
        real_elapsed = self._market_elapsed_min(market)
        if 0 < real_elapsed <= _ENTRY_SCAN_OPENING_MIN:
            return _ENTRY_SCAN_OPENING_INTERVAL_MIN * 60   # 개장: 2분
        return _ENTRY_SCAN_REGULAR_INTERVAL_MIN * 60       # 정규: 10분

    def run_housekeeping(self, market: str):
        if not self._enter_market_task(market, "housekeeping"):
            return
        try:
            if not self.session_active:
                return
            if self.current_market != market:
                return
            self._refresh_claude_control()
            self._consume_pending_claude_trigger(market)
            self._sync_runtime_with_broker()
            self.risk.update_prices(self.price_cache)
            self._process_exit_candidates()
            self._write_live_status(market)
        finally:
            self._leave_market_task(market, "housekeeping")

    def run_entry_scan(self, market: str):
        if not self.session_active or self.current_market != market:
            return
        now_ts = time.time()
        interval_sec = self._entry_scan_interval_sec(market)
        last_ts = float(self._last_entry_scan_at.get(market, 0.0) or 0.0)
        if last_ts and (now_ts - last_ts) < interval_sec:
            return
        self._last_entry_scan_at[market] = now_ts
        log.info(
            f"[{market} 엔트리 스캔] interval={int(interval_sec/60)}분 "
            f"(장초 {_ENTRY_SCAN_OPENING_MIN}분={_ENTRY_SCAN_OPENING_INTERVAL_MIN}분, 이후={_ENTRY_SCAN_REGULAR_INTERVAL_MIN}분)"
        )
        self.run_cycle(market)

    def run_rescreen(self, market: str):
        if not self.session_active or self.current_market != market:
            return
        now_ts = time.time()
        last_ts = float(self._last_rescreen_at.get(market, 0.0) or 0.0)
        interval_sec = _RESCREEN_INTERVAL_MIN * 60
        if last_ts and (now_ts - last_ts) < interval_sec:
            return
        self._last_rescreen_at[market] = now_ts
        try:
            selected = self.manual_rescreen(market)
            log.info(f"[{market} 정시 재스크리닝] {selected}")
        except Exception as e:
            log.info(f"[{market} 정시 재스크리닝 스킵] {e}")

    def _get_ohlcv_cached(self, ticker: str, market: str, ttl_min: int = 60):
        """
        일봉 OHLCV 캐시 — TTL 내 재요청 생략.
        흐름:
          1. 메모리 캐시(TTL) → 있으면 즉시 반환
          2. price CSV 있으면 로드 (수집기가 미리 저장한 것)
          3. 없으면 get_daily_ohlcv() on-demand 조회 → CSV 영속 저장
        """
        from pathlib import Path as _Path
        import pandas as _pd

        now = datetime.now(KST).replace(tzinfo=None)
        cached_at = self._ohlcv_cache_time.get(ticker)
        if cached_at and (now - cached_at).total_seconds() < ttl_min * 60:
            return self._ohlcv_cache[ticker]

        # CSV 파일 경로
        _price_base = _Path(__file__).parent / "data" / "price"
        _csv_path = _price_base / market.lower() / f"{market.lower()}_{ticker}.csv"

        if _csv_path.exists():
            try:
                df = _pd.read_csv(_csv_path, parse_dates=["date"])
                df.columns = [c.lower() for c in df.columns]
                df = df.sort_values("date").reset_index(drop=True)
                # CSV가 너무 오래됐거나 행 수가 부족하면 on-demand fetch로 보완
                _last = df["date"].max()
                _today = _pd.Timestamp(datetime.now().date())
                if (_today - _last).days <= 7 and len(df) >= self._MIN_SIGNAL_ROWS:
                    self._ohlcv_cache[ticker] = df
                    self._ohlcv_cache_time[ticker] = now
                    return df
                if len(df) < self._MIN_SIGNAL_ROWS:
                    log.debug(f"[OHLCV] {ticker} CSV {len(df)}행 부족 → on-demand 보완")
            except Exception:
                pass

        # on-demand 조회
        lookback_days = 365 if market == "KR" else 180
        df = get_daily_ohlcv(ticker, self.token, lookback_days=lookback_days, market=market)
        if not df.empty:
            # CSV 영속 저장 (다음 실행부터 파일에서 읽음)
            try:
                _csv_path.parent.mkdir(parents=True, exist_ok=True)
                if _csv_path.exists():
                    existing = _pd.read_csv(_csv_path, parse_dates=["date"])
                    existing.columns = [c.lower() for c in existing.columns]
                    df = _pd.concat([existing, df]).drop_duplicates("date").sort_values("date").reset_index(drop=True)
                df.to_csv(_csv_path, index=False, encoding="utf-8-sig")
                log.info(f"[OHLCV on-demand 저장] {market} {ticker} → {len(df)}일")
            except Exception as _e:
                log.debug(f"[OHLCV CSV 저장 실패] {ticker}: {_e}")

        self._ohlcv_cache[ticker] = df
        self._ohlcv_cache_time[ticker] = now
        return df

    def run_cycle(self, market: str):
        if not self._enter_market_task(market, "run_cycle"):
            return
        if not self.session_active:
            self._leave_market_task(market, "run_cycle")
            return
        if self.current_market != market:
            self._leave_market_task(market, "run_cycle")
            return
        if self.risk.check_halt():
            self._leave_market_task(market, "run_cycle")
            return
        # startup 보호 구간 — session_open 완료 후 최소 대기
        _since_open = time.time() - self._session_open_at.get(market, 0.0)
        if _since_open < _STARTUP_GUARD_SEC:
            log.info(
                f"[{market}] startup guard — session_open 후 {_since_open:.0f}s 경과 "
                f"(최소 {_STARTUP_GUARD_SEC:.0f}s 대기 중) — cycle 스킵"
            )
            self._leave_market_task(market, "run_cycle")
            return
        self._refresh_claude_control()
        self._consume_pending_claude_trigger(market)

        mode = self.today_judgment.get("consensus", {}).get("mode", "CAUTIOUS")
        size_pct = self.today_judgment.get("consensus", {}).get("size", 50)
        tickers = self.today_tickers.get(
            market, _DEFAULT_KR_TICKERS if market == "KR" else _DEFAULT_US_TICKERS
        )
        self._sync_runtime_with_broker()

        # ML DB: run_cycle 공통 컨텍스트 (모드/분석가) — 루프 내에서 재사용
        _ml_judgments = self.today_judgment.get("judgments", {})
        _ml_ca_ctx    = self.today_judgment.get("digest_raw", {}).get("context", {})
        def _ml_base_row(ticker_, price_, sig_row_) -> dict:
            """종목별 ML row 공통 필드 조립"""
            if not _ML_DB_ENABLED:
                return {}
            _jb = _ml_judgments.get("bull", {})
            _jbr= _ml_judgments.get("bear", {})
            _jn = _ml_judgments.get("neutral", {})
            return {
                "market":       market,
                "ticker":       ticker_,
                "session_date": _market_session_date(market).isoformat(),
                "mode":         mode,
                "mode_score":   self.today_judgment.get("consensus", {}).get("score"),
                "bull_stance":  _jb.get("stance", "NEUTRAL"),
                "bear_stance":  _jbr.get("stance", "NEUTRAL"),
                "neut_stance":  _jn.get("stance", "NEUTRAL"),
                "bull_conf":    _jb.get("confidence"),
                "bear_conf":    _jbr.get("confidence"),
                "neut_conf":    _jn.get("confidence"),
                "vix":          _ml_ca_ctx.get("vix"),
                "usd_krw":      _ml_ca_ctx.get("usd_krw"),
                "price":        price_,
                "rsi":          sig_row_.get("rsi"),
                "bb_pct":       sig_row_.get("bb_pct"),
                "vol_ratio":    sig_row_.get("vol_ratio"),
                "macd":         sig_row_.get("macd"),
                "macd_signal":  sig_row_.get("macd_signal"),
                "ma20":         sig_row_.get("ma20"),
                "ma60":         sig_row_.get("ma60"),
                "atr":          sig_row_.get("atr"),
                "gap_pct":      sig_row_.get("gap_pct"),
                "change_pct":   sig_row_.get("change_pct"),
            }

        def _ml_write_eval(
            ticker_: str,
            price_: float,
            sig_row_: dict,
            decision_: str,
            *,
            block_reason_: str = None,
            strategy_used_: str = None,
            fired_strategy_: str = None,
            diag_json_: dict | None = None,
            extra_fields_: dict | None = None,
        ) -> int:
            if not _ML_DB_ENABLED:
                return -1
            try:
                _row = _ml_base_row(ticker_, price_, sig_row_)
                _row["decision"] = decision_
                if block_reason_:
                    _row["block_reason"] = block_reason_
                if strategy_used_:
                    _row["strategy_used"] = strategy_used_
                if fired_strategy_:
                    _fired_col = {
                        "mean_reversion": "mr_fired",
                        "volatility_breakout": "vb_fired",
                        "momentum": "mom_fired",
                        "gap_pullback": "gap_fired",
                    }.get(fired_strategy_)
                    if _fired_col:
                        _row[_fired_col] = True
                if diag_json_:
                    _row["diag_json"] = diag_json_
                if extra_fields_:
                    _row.update(extra_fields_)
                return _ml_write(_row)
            except Exception:
                return -1

        now_str = datetime.now(KST).strftime("%H:%M")
        log.info(
            f"[{market} 사이클 {now_str}] 모드:{mode} size:{size_pct}% | "
            f"종목:{tickers} | 포지션:{len(self.risk.positions)}개"
        )

        # 분석가 평균 confidence 계산 — 신뢰도 낮으면 신규 진입 차단
        _judgments = self.today_judgment.get("judgments", {})
        _avg_conf = (
            sum(_judgments.get(a, {}).get("confidence", 0.5) for a in ("bull", "bear", "neutral")) / 3.0
            if _judgments else 0.5
        )
        _low_conf = _avg_conf < _MIN_ENTRY_CONF
        if _low_conf:
            log.info(f"[{market}] 낮은 분석가 confidence ({_avg_conf:.2f} < {_MIN_ENTRY_CONF}) — 신규 진입 전 사이클 스킵")

        # Cross-asset 컨텍스트 (VIX/VKOSPI, USD/KRW, 섹터 ETF) — 파라미터 보정용
        _ca_context = self.today_judgment.get("digest_raw", {}).get("context", {})
        _fear_label = get_vix_regime(_ca_context, market)
        if _ca_context:
            log.debug(f"[{market}] cross-asset 보정 적용 | {_fear_label} | "
                      f"USD/KRW {_ca_context.get('usd_krw', 0):,.0f}")

        # US 전략 우선순위: 분석가 투표 전략 → mean_reversion → gap_pullback
        # momentum/VB 모두 disabled이므로 base는 mean_reversion, gap_pullback 고정
        if market == "US":
            _voted_strat = _analyst_strategy_vote(_judgments)
            _us_base = ["mean_reversion", "gap_pullback"]
            if _voted_strat and _voted_strat not in _us_base:
                _us_strat_list = [_voted_strat] + _us_base
            elif _voted_strat:
                _us_strat_list = [_voted_strat] + [s for s in _us_base if s != _voted_strat]
            else:
                _us_strat_list = _us_base
            log.debug(f"[US 전략] 투표={_voted_strat} 시도순서={_us_strat_list}")
        else:
            _us_strat_list = []

        _pending_signals: list[dict] = []   # 사이클 내 신호 수집 → 정렬 후 실행

        for ticker in tickers:
            try:
                if market == "US":
                    blocked_reason = self._us_order_block_reason(ticker)
                    if blocked_reason:
                        analysis_log.info(
                            f"[skip {market}] {ticker} us_order_blocked",
                            extra={"extra": {
                                "event": "entry_skip",
                                "market": market,
                                "ticker": ticker,
                                "reason": "us_order_blocked",
                                "detail": blocked_reason,
                                "mode": mode,
                            }},
                        )
                        log.info(f"[US 주문차단] {ticker} — {blocked_reason}")
                        continue
                price_info = get_price(ticker, self.token, market=market)
                price = price_info["price"]
                risk_price = self._price_to_krw(price, market)
                if self.enable_slippage_guard:
                    est_slippage_bps = self._estimate_slippage_bps(price_info)
                    if est_slippage_bps > self.max_est_slippage_bps:
                        analysis_log.info(
                            f"[skip {market}] {ticker} est_slippage_too_high",
                            extra={"extra": {
                                "event": "entry_skip",
                                "market": market,
                                "ticker": ticker,
                                "reason": "est_slippage_too_high",
                                "est_slippage_bps": est_slippage_bps,
                                "max_est_slippage_bps": self.max_est_slippage_bps,
                            }},
                        )
                        log.debug(
                            f"  [{ticker}] est slippage {est_slippage_bps:.1f}bps > "
                            f"{self.max_est_slippage_bps:.1f}bps -> skip"
                        )
                        continue
                if price <= 0:
                    cnt = self._invalid_price_count.get(ticker, 0) + 1
                    self._invalid_price_count[ticker] = cnt
                    _MAX_INVALID = 2 if market == "US" else 3
                    if cnt >= _MAX_INVALID:
                        log.warning(
                            f"[skip {market}] {ticker} invalid price {cnt}회 연속 "
                            f"→ 종목 교체"
                        )
                        universe = self.today_judgment.get("universe_tickers", [])
                        current = self.today_tickers.get(market, [])
                        replacement = next(
                            (t for t in universe if t not in current and t != ticker), None
                        )
                        if replacement:
                            new_tickers = [replacement if t == ticker else t for t in current]
                            self.today_tickers[market] = new_tickers
                            self.today_judgment["tickers"] = new_tickers
                            self._invalid_price_count.pop(ticker, None)
                            log.info(f"[종목 교체] {market} {ticker} → {replacement}")
                            _now_inv = datetime.now(KST).replace(tzinfo=None)
                            self._ticker_exclude_log.setdefault(market, []).append(
                                {"ticker": ticker, "reason": "invalid_price", "ts": _now_inv}
                            )
                        else:
                            log.warning(f"[종목 교체 실패] {market} {ticker} — 대체 후보 없음")
                    else:
                        log.warning(f"[skip {market}] {ticker} invalid price={price} ({cnt}/{_MAX_INVALID}회)")
                    continue
                # 이전 정상가 대비 30% 초과 괴리 → KIS API outlier 방어
                _prev_price = self.price_cache_raw.get(ticker, 0)
                if _prev_price > 0 and abs(price - _prev_price) / _prev_price > 0.30:
                    cnt = self._invalid_price_count.get(ticker, 0) + 1
                    self._invalid_price_count[ticker] = cnt
                    _MAX_OUTLIER = 2 if market == "US" else 3
                    log.warning(
                        f"[outlier {market}] {ticker} price={price:,} vs prev={_prev_price:,} "
                        f"괴리 {abs(price-_prev_price)/_prev_price*100:.1f}% → 스킵 ({cnt}/{_MAX_OUTLIER}회)"
                    )
                    if cnt >= _MAX_OUTLIER:
                        _universe = self.today_judgment.get("universe_tickers", [])
                        _current = self.today_tickers.get(market, [])
                        _replacement = next(
                            (t for t in _universe if t not in _current and t != ticker), None
                        )
                        if _replacement:
                            _new_tickers = [_replacement if t == ticker else t for t in _current]
                            self.today_tickers[market] = _new_tickers
                            self.today_judgment["tickers"] = _new_tickers
                            self._invalid_price_count.pop(ticker, None)
                            log.info(f"[종목 교체] {market} {ticker} → {_replacement} (outlier {cnt}회)")
                            _now_out = datetime.now(KST).replace(tzinfo=None)
                            self._ticker_exclude_log.setdefault(market, []).append(
                                {"ticker": ticker, "reason": "outlier_price", "ts": _now_out}
                            )
                        else:
                            log.warning(f"[종목 교체 실패] {market} {ticker} — 대체 후보 없음")
                    continue
                self._invalid_price_count.pop(ticker, None)  # 정상 가격 수신 시 카운터 리셋
                self.price_cache_raw[ticker] = price
                self.price_cache[ticker] = risk_price
                self.risk.update_prices(self.price_cache)
                self._process_exit_candidates()

                if mode == "HALT":
                    log.debug(f"  [{ticker}] HALT 모드 — 신규진입 스킵")
                    continue

                # 인버스 ETF는 약세(MILD_BEAR 이하) 모드에서만 매수 허용
                _INVERSE = {"SQQQ", "114800"}
                _BEAR_ONLY_MODES = {"MILD_BEAR", "CAUTIOUS_BEAR", "DEFENSIVE", "HALT"}
                if ticker in _INVERSE and mode not in _BEAR_ONLY_MODES:
                    log.debug(f"  [{ticker}] 인버스 ETF — 약세 모드 아님({mode}) → 스킵")
                    continue
                if self._in_entry_blackout(market):
                    log.debug(f"  [{ticker}] 블랙아웃 시간 — 스킵")
                    if _ML_DB_ENABLED:
                        _ml_write_eval(ticker, price, {}, "SKIPPED",
                                       block_reason_="entry_blackout")
                    continue

                if self._is_entry_blocked(ticker):
                    log.debug(f"  [{ticker}] 진입 차단 중 (쿨다운)")
                    continue

                if self._has_same_day_trade(ticker, market):
                    analysis_log.info(
                        f"[skip {market}] {ticker} same_day_reentry_blocked",
                        extra={"extra": {
                            "event": "entry_skip",
                            "market": market,
                            "ticker": ticker,
                            "reason": "same_day_reentry_blocked",
                            "price": price,
                            "risk_price_krw": risk_price,
                            "mode": mode,
                        }},
                    )
                    self._notify_signal_state_change(
                        "entry_skip", market, ticker,
                        price=float(price),
                        reason="same_day_reentry_blocked",
                        mode=mode,
                    )
                    if _ML_DB_ENABLED:
                        _ml_write_eval(
                            ticker, price, {}, "SKIPPED",
                            block_reason_="same_day_reentry_blocked",
                            diag_json_={"reason": "same_day_reentry_blocked"},
                        )
                    log.debug(f"  [{ticker}] 당일 동일 종목 체결 이력 있음 → 재진입 스킵")
                    continue

                if _low_conf:
                    if _ML_DB_ENABLED:
                        _ml_write_eval(
                            ticker, price, {}, "SKIPPED",
                            block_reason_="low_confidence",
                            diag_json_={"avg_conf": round(_avg_conf, 4), "min_conf": _MIN_ENTRY_CONF},
                        )
                    log.debug(f"  [{ticker}] confidence 부족 ({_avg_conf:.2f} < {_MIN_ENTRY_CONF}) → 신규 진입 스킵")
                    continue

                if self._has_open_position(ticker, market):
                    analysis_log.info(
                        f"[skip {market}] {ticker} already_holding",
                        extra={"extra": {
                            "event": "entry_skip",
                            "market": market,
                            "ticker": ticker,
                            "reason": "already_holding",
                            "price": price,
                            "risk_price_krw": risk_price,
                            "mode": mode,
                        }},
                    )
                    self._notify_signal_state_change(
                        "entry_skip", market, ticker,
                        price=float(price),
                        reason="already_holding",
                        mode=mode,
                    )
                    if _ML_DB_ENABLED:
                        _ml_write_eval(
                            ticker, price, {}, "SKIPPED",
                            block_reason_="already_holding",
                            diag_json_={"reason": "already_holding"},
                        )
                    log.debug(f"  [{ticker}] 브로커/런타임 보유중 → 재진입 스킵")
                    continue

                ok, reason = self.risk.can_open(ticker, risk_price, size_pct, market=market)
                if not ok:
                    analysis_log.info(
                        f"[skip {market}] {ticker} {reason}",
                        extra={"extra": {
                            "event": "entry_skip",
                            "market": market,
                            "ticker": ticker,
                            "reason": reason,
                            "price": price,
                            "risk_price_krw": risk_price,
                            "mode": mode,
                        }},
                    )
                    if reason == "already_holding":
                        self._notify_signal_state_change(
                            "entry_skip", market, ticker,
                            strategy=strategy_name,
                            price=float(price),
                            reason=reason,
                            mode=mode,
                        )
                    if _ML_DB_ENABLED:
                        _ml_write_eval(
                            ticker, price, {}, "SKIPPED",
                            block_reason_=str(reason),
                            diag_json_={"reason": str(reason)},
                        )
                    log.debug(f"  [{ticker}] 진입불가: {reason}")
                    continue
                if self._has_pending_order(ticker, market):
                    analysis_log.info(
                        f"[skip {market}] {ticker} pending_order",
                        extra={"extra": {
                            "event": "entry_skip",
                            "market": market,
                            "ticker": ticker,
                            "reason": "pending_order",
                            "price": price,
                            "risk_price_krw": risk_price,
                            "mode": mode,
                        }},
                    )
                    self._notify_signal_state_change(
                        "entry_skip", market, ticker,
                        price=float(price),
                        reason="pending_order",
                        mode=mode,
                    )
                    if _ML_DB_ENABLED:
                        _ml_write_eval(
                            ticker, price, {}, "SKIPPED",
                            block_reason_="pending_order",
                            diag_json_={"reason": "pending_order"},
                        )
                    log.debug(f"  [{ticker}] 미체결 주문 존재 → 재주문 스킵")
                    continue

                # 시장별 예산 초과 확인
                avail = self._market_budget_available(market)
                if avail < risk_price:
                    analysis_log.info(
                        f"[skip {market}] {ticker} budget_exhausted",
                        extra={"extra": {
                            "event": "entry_skip",
                            "market": market,
                            "ticker": ticker,
                            "reason": "budget_exhausted",
                            "available_budget_krw": avail,
                            "risk_price_krw": risk_price,
                        }},
                    )
                    if _ML_DB_ENABLED:
                        _ml_write_eval(
                            ticker, price, {}, "SKIPPED",
                            block_reason_="budget_exhausted",
                            diag_json_={"available_budget_krw": float(avail), "risk_price_krw": float(risk_price)},
                        )
                    log.debug(f"  [{ticker}] 예산 소진 (잔여 {avail:,.0f}원 < {risk_price:,.0f}원) → 스킵")
                    continue

                candles = self._get_ohlcv_cached(ticker, market)
                if candles.empty:
                    log.debug(f"  [{ticker}] 캔들 없음")
                    continue

                # ── 장중 임시 당일봉 주입 (KR/US 공통) ───────────────────
                # CSV 마지막 행은 어제 종가 기준. 장중에는 오늘 price_info(open/
                # high/low/close/volume)를 임시 캔들로 추가해 지표를 당일 기준으로
                # 재계산. 장 마감 후 price_collector가 실제 일봉을 CSV에 저장하면
                # 다음 TTL 갱신 시 자동으로 실제 봉으로 대체됨.
                # US는 KST 자정을 넘어도 ET 기준 날짜를 사용해 올바른 거래일 반영.
                if self.session_active:
                    import pandas as _pd2
                    _ET = ZoneInfo("America/New_York")
                    _today_ts = _pd2.Timestamp(
                        datetime.now(KST).date() if market == "KR"
                        else datetime.now(_ET).date()
                    )
                    _last_date = candles["date"].max() if not candles.empty else _pd2.Timestamp("2000-01-01")
                    # CSV에 오늘 날짜가 있어도 OHLC 전부 동일(pre-open 불완전 데이터)이면
                    # 해당 행을 제거하고 실시간 주입으로 대체
                    if _last_date == _today_ts and not candles.empty:
                        _lr = candles.iloc[-1]
                        if float(_lr.get("open", 0)) == float(_lr.get("high", 0)) == float(_lr.get("low", 0)):
                            candles = candles.iloc[:-1].copy()
                            _last_date = candles["date"].max() if not candles.empty else _pd2.Timestamp("2000-01-01")
                            log.debug(f"[당일봉 교체 {market}] {ticker} CSV pre-open 행 제거 → 실시간 주입으로 대체")
                    if _last_date < _today_ts and price_info.get("price", 0) > 0:
                        _p = price_info["price"]
                        _o = price_info.get("open",   _p)
                        _h = price_info.get("high",   _p)
                        _l = price_info.get("low",    _p)
                        # WS tick 누적 고가/저가로 보정 — KIS API가 intraday H/L 미제공 시 탈출
                        _ws_high = self._intraday_high.get(ticker, 0)
                        _ws_low  = self._intraday_low.get(ticker, float("inf"))
                        if _ws_high > 0:
                            _h = max(_h, _ws_high)
                        if _ws_low < float("inf"):
                            _l = min(_l, _ws_low)
                        _is_single_price = (_o == _p and _h == _p and _l == _p)
                        _prev_close_val = float(candles.iloc[-1]["close"]) if not candles.empty else 0
                        _has_gap = (_prev_close_val > 0
                                    and abs(_o - _prev_close_val) / _prev_close_val >= 0.005)
                        if _is_single_price and not _has_gap:
                            log.debug(f"[당일봉 주입 스킵 {market}] {ticker} 단일가봉+갭없음 → 제외")
                        else:
                            if _is_single_price and _has_gap:
                                log.debug(
                                    f"[당일봉 갭주입 {market}] {ticker} 단일가봉이나 갭 있음"
                                    f" prev_close={_prev_close_val} open={_o} → gap_pct 계산용 주입"
                                )
                            _raw_vol = float(price_info.get("volume", 0) or 0)
                            _proj_vol = self._project_intraday_volume(market, _raw_vol)
                            _today_bar = _pd2.DataFrame([{
                                "date":   _today_ts,
                                "open":   _o,
                                "high":   _h,
                                "low":    _l,
                                "close":  _p,
                                "volume": _proj_vol or _raw_vol,
                            }])
                            candles = _pd2.concat([candles, _today_bar], ignore_index=True)
                            log.debug(
                                f"[당일봉 주입 {market}] {ticker} "
                                f"close={_p} raw_vol={_raw_vol:,.0f} proj_vol={(_proj_vol or _raw_vol):,.0f}"
                            )

                sig_df = calc_all(candles)
                if sig_df.empty or len(sig_df) < self._MIN_SIGNAL_ROWS:
                    log.warning(f"  [{ticker}] 신호계산 불가 — 유효행 {len(sig_df)}개 (최소 {self._MIN_SIGNAL_ROWS})")
                    continue
                i = len(sig_df) - 1

                signal_fired = False
                strategy_name = ""
                params = {}
                kr_momentum_diag = None
                none_detail = ""

                def _ca(p):
                    return apply_cross_asset_adjust(p, _ca_context, market, ticker, mode)

                def _ap(strat: str) -> dict:
                    """adaptive_params + cross-asset 조정 통합 헬퍼."""
                    p = _ca(_adaptive_params(strat, market, mode, _avg_conf,
                                            context=_ca_context))
                    # 장초반 opening window 판단용 — gap_pullback.signal()이 사용
                    p["session_elapsed_min"] = self._market_elapsed_min(market)
                    return p

                def _gap_detail(_df, _i, _p):
                    if _p.get("disabled"):
                        return f"갭눌림: 전략 비활성({market} {mode})"
                    if _i < 5:
                        return "갭눌림: 데이터부족"
                    _row = _df.iloc[_i]
                    _elapsed_min = float(_p.get("session_elapsed_min", 999) or 999)
                    _opening_min = float(_p.get("opening_window_min", 30) or 30)
                    _in_opening = 0 < _elapsed_min <= _opening_min
                    _gap_min = float(_p.get("opening_gap_min", 0.030) if _in_opening
                                     else _p.get("gap_min", 0.010))
                    _vol_mult = float(_p.get("opening_vol_mult", 0.15) if _in_opening
                                      else _p.get("vol_mult", 1.5))
                    _vol_avg = float(_row.get("vol_avg20", 0) or 0)
                    _gap = float(_row.get("gap_pct", 0) or 0) / 100.0
                    _vol_ratio = float(_row.get("volume", 0) or 0) / _vol_avg if _vol_avg else 0.0
                    _pullback = float(_row.get("low", 0) or 0) >= float(_row.get("open", 0) or 0) * 0.995
                    _prefix = "갭눌림(장초)" if _in_opening else "갭눌림"
                    return (
                        f"{_prefix}: 갭={_gap*100:.2f}%(목표>{_gap_min*100:.1f}%){'✓' if _gap > _gap_min else '✗'}"
                        f" vol={_vol_ratio:.2f}(목표>{_vol_mult:.1f}){'✓' if _vol_ratio > _vol_mult else '✗'}"
                        f" 눌림={'✓' if _pullback else '✗'}"
                    )

                def _mr_detail(_df, _i, _p):
                    if _p.get("disabled"):
                        return f"평균회귀: 전략 비활성({market} {mode})"
                    if _i < 20:
                        return "평균회귀: 데이터부족"
                    _row = _df.iloc[_i]
                    _rsi = float(_row.get("rsi", 50) or 50)
                    _bb = float(_row.get("bb_pct", 50) or 50)
                    _vol_ratio = float(_row.get("vol_ratio", 1) or 1)
                    _close = float(_row.get("close", 0) or 0)
                    _ma60 = float(_row.get("ma60", 0) or 0)
                    _rsi_thr = float(_p.get("rsi_thr", 32))
                    _bb_thr = float(_p.get("bb_thr", 20))
                    _ma60_thr = float(_p.get("ma60_thr", 0.90))
                    _vol_limit = float(_p.get("vol_limit", 2.5))
                    _ma_ok = _ma60 > 0 and _close > _ma60 * _ma60_thr
                    return (
                        f"평균회귀: RSI={_rsi:.1f}(목표<{_rsi_thr:.0f}){'✓' if _rsi < _rsi_thr else '✗'}"
                        f" BB={_bb:.1f}(목표<{_bb_thr:.0f}){'✓' if _bb < _bb_thr else '✗'}"
                        f" vol={_vol_ratio:.2f}(목표<{_vol_limit:.1f}){'✓' if _vol_ratio < _vol_limit else '과다'}"
                        f" ma60={'✓' if _ma_ok else '✗'}"
                    )

                def _vb_detail(_df, _i, _p):
                    if _p.get("disabled"):
                        return f"변동성돌파: 전략 비활성({market} {mode})"
                    if _i < 5:
                        return "변동성돌파: 데이터부족"
                    _row = _df.iloc[_i]
                    _prev = _df.iloc[_i - 1]
                    _target = float(_prev.get("open", 0) or 0) + (
                        float(_prev.get("high", 0) or 0) - float(_prev.get("low", 0) or 0)
                    ) * float(_p.get("k", 0.45))
                    _close = float(_row.get("close", 0) or 0)
                    _vol_ratio = float(_row.get("vol_ratio", 1) or 1)
                    _vol_mult = float(_p.get("vol_mult", 2.0))
                    return (
                        f"변돌: 돌파={_close:.2f}(목표>{_target:.2f}){'✓' if _close > _target else '✗'}"
                        f" vol={_vol_ratio:.2f}(목표>{_vol_mult:.1f}){'✓' if _vol_ratio > _vol_mult else '✗'}"
                    )

                if market == "KR":
                    gap_p = _ap("gap_pullback")
                    if gap_sig(sig_df, i, gap_p):
                        signal_fired = True
                        strategy_name = "gap_pullback"
                        params = gap_p
                    else:
                        mom_p = _ap("momentum")
                        kr_momentum_diag = mom_diag(sig_df, i, mom_p)
                        if mom_sig(sig_df, i, mom_p):
                            signal_fired = True
                            strategy_name = "momentum"
                            params = mom_p
                        else:
                            mr_p = _ap("mean_reversion")
                            if mr_sig(sig_df, i, mr_p):
                                signal_fired = True
                                strategy_name = "mean_reversion"
                                params = mr_p
                            else:
                                vb_p = _ap("volatility_breakout")
                                if vb_sig(sig_df, i, vb_p):
                                    signal_fired = True
                                    strategy_name = "volatility_breakout"
                                    params = vb_p
                else:
                    # US: 분석가 투표 전략 우선, volatility_breakout 폴백
                    _strat_dispatch = {
                        "momentum":           (mom_sig,  _ap("momentum")),
                        "mean_reversion":     (mr_sig,   _ap("mean_reversion")),
                        "gap_pullback":       (gap_sig,  _ap("gap_pullback")),
                        "volatility_breakout":(vb_sig,   _ap("volatility_breakout")),
                    }
                    for _strat_name in _us_strat_list:
                        _sig_fn, _p = _strat_dispatch.get(
                            _strat_name, (vb_sig, _ap("volatility_breakout"))
                        )
                        if _sig_fn(sig_df, i, _p):
                            signal_fired = True
                            strategy_name = _strat_name
                            params = _p
                            break

                # 활성도 추적 — 시장별 인라인 교체 기준으로 활용
                _scan_interval_min = self._entry_scan_interval_sec(market) / 60.0
                if signal_fired:
                    self._ticker_no_signal_cycles[ticker] = 0
                    self._ticker_no_signal_minutes[ticker] = 0.0
                else:
                    _prev_cnt = self._ticker_no_signal_cycles.get(ticker, 0)
                    self._ticker_no_signal_cycles[ticker] = _prev_cnt + 1
                    _prev_min = float(self._ticker_no_signal_minutes.get(ticker, 0.0) or 0.0)
                    self._ticker_no_signal_minutes[ticker] = _prev_min + _scan_interval_min

                    # 인라인 빠른 교체: 포지션 없는 종목이 연속 무신호 임계 초과 시
                    _holding = ticker in {p["ticker"] for p in self.risk.positions}
                    _swap_due = (
                        self._ticker_no_signal_minutes.get(ticker, 0.0) >= _KR_NO_SIGNAL_SWAP_MIN
                        if market == "KR"
                        else self._ticker_no_signal_cycles[ticker] >= _US_NO_SIGNAL_SWAP_CYCLES
                    )
                    if (not _holding and _swap_due):
                        _universe = self.today_judgment.get("universe_tickers", [])
                        _current  = set(self.today_tickers.get(market, []))
                        _cooldown = self._get_cooldown_excluded(market)
                        _cands = [
                            t for t in _universe
                            if t not in _current and t not in _cooldown
                        ]
                        if _cands:
                            _new = _cands[0]
                            _cur_list = list(self.today_tickers.get(market, []))
                            if ticker in _cur_list:
                                _cur_list[_cur_list.index(ticker)] = _new
                                self.today_tickers[market] = _cur_list
                                self.today_judgment["tickers"] = _cur_list
                                self._ticker_no_signal_cycles.pop(ticker, None)
                                self._ticker_no_signal_minutes.pop(ticker, None)
                                # 교체 종목 60분 쿨다운 등록
                                _now_swap = datetime.now(KST).replace(tzinfo=None)
                                self._ticker_exclude_log.setdefault(market, []).append(
                                    {"ticker": ticker, "reason": "인라인교체_무신호", "ts": _now_swap}
                                )
                                _swap_label = (
                                    f"{round(_prev_min + _scan_interval_min):d}분 무신호"
                                    if market == "KR"
                                    else f"{_prev_cnt + 1}사이클 무신호"
                                )
                                log.info(f"[인라인교체 {market}] {ticker}({_swap_label}) → {_new}")
                                send(
                                    f"[종목교체 {market}] {ticker} → {_new} "
                                    f"({'%d분' % round(_prev_min + _scan_interval_min) if market == 'KR' else '%d사이클' % (_prev_cnt + 1)} 무신호)"
                                )

                if not signal_fired:
                    if market == "KR":
                        gap_p = _ap("gap_pullback")
                        mr_p = _ap("mean_reversion")
                        vb_p = _ap("volatility_breakout")
                        mom_p = _ap("momentum")
                        _mom_diag = kr_momentum_diag or mom_diag(sig_df, i, mom_p)
                        _mom_detail = (
                            f"모멘텀: 전략 비활성({market} {mode})"
                            if mom_p.get("disabled") else
                            (
                                f"모멘텀: 추세{'충족' if _mom_diag.get('ma_ok') else '부족'}"
                                f", MACD{'충족' if _mom_diag.get('macd_ok') else '부족'}"
                                f", 거래량{'충족' if _mom_diag.get('vol_ok') else '부족'}"
                                f", 신고가{'충족' if _mom_diag.get('high_ok') else '부족'}"
                            )
                        )
                        none_detail = " | ".join([
                            _gap_detail(sig_df, i, gap_p),
                            _mom_detail,
                            _mr_detail(sig_df, i, mr_p),
                            _vb_detail(sig_df, i, vb_p),
                        ])
                    else:
                        _us_mom_p = _ap("momentum")
                        _us_mom_diag = mom_diag(sig_df, i, _us_mom_p)
                        _us_mr_p = _ap("mean_reversion")
                        _us_gap_p = _ap("gap_pullback")
                        _us_vb_p = _ap("volatility_breakout")
                        _us_mom_detail = (
                            f"모멘텀: 전략 비활성({market} {mode})"
                            if _us_mom_p.get("disabled") else
                            (
                                f"모멘텀: 추세{'충족' if _us_mom_diag.get('ma_ok') else '부족'}"
                                f", MACD{'충족' if _us_mom_diag.get('macd_ok') else '부족'}"
                                f", 거래량{'충족' if _us_mom_diag.get('vol_ok') else '부족'}"
                                f", 신고가{'충족' if _us_mom_diag.get('high_ok') else '부족'}"
                            )
                        )
                        none_detail = " | ".join([
                            _vb_detail(sig_df, i, _us_vb_p),
                            _us_mom_detail,
                            _mr_detail(sig_df, i, _us_mr_p),
                            _gap_detail(sig_df, i, _us_gap_p),
                        ])
                    if market == "KR" and kr_momentum_diag:
                        log.debug(
                            f"[KR momentum diag] {ticker} "
                            f"ready={kr_momentum_diag.get('ready')} "
                            f"ma_ok={kr_momentum_diag.get('ma_ok')} "
                            f"macd_ok={kr_momentum_diag.get('macd_ok')} "
                            f"vol_ok={kr_momentum_diag.get('vol_ok')} "
                            f"high_ok={kr_momentum_diag.get('high_ok')} "
                            f"close={kr_momentum_diag.get('close', 0):.2f} "
                            f"high20={kr_momentum_diag.get('high20', 0):.2f} "
                            f"vol={kr_momentum_diag.get('volume', 0):.0f} "
                            f"vol_avg20={kr_momentum_diag.get('vol_avg20', 0):.0f} "
                            f"vol_mult={kr_momentum_diag.get('vol_mult', 0):.2f}"
                        )
                    # 장초반 후보 로깅 — 신호 없음이지만 opening window 내 갭 3%+ 종목 추적
                    try:
                        _oc_real = self._market_elapsed_min(market)
                        if _oc_real <= _ENTRY_SCAN_OPENING_MIN and i >= 5:
                            _oc_p    = _ap("gap_pullback")
                            _oc_row  = sig_df.iloc[i]
                            _oc_gap  = float(_oc_row.get("gap_pct", 0) or 0)
                            _oc_vavg = float(_oc_row.get("vol_avg20", 1) or 1)
                            _oc_vr   = float(_oc_row.get("volume", 0) or 0) / _oc_vavg if _oc_vavg else 0
                            _oc_gap_thr = float(_oc_p.get("opening_gap_min", 0.030)) * 100
                            if not _oc_p.get("disabled") and _oc_gap >= _oc_gap_thr:
                                analysis_log.info(
                                    f"[opening_candidate {market}] {ticker} | "
                                    f"elapsed={_oc_real:.1f}min gap={_oc_gap:.2f}% "
                                    f"vol_ratio={_oc_vr:.3f}",
                                    extra={"extra": {
                                        "event": "opening_candidate",
                                        "market": market,
                                        "ticker": ticker,
                                        "session_elapsed_min": round(_oc_real, 1),
                                        "opening_gap_pct": round(_oc_gap, 2),
                                        "opening_vol_ratio": round(_oc_vr, 3),
                                        "opening_signal_candidate": True,
                                        "mode": mode,
                                    }},
                                )
                    except Exception:
                        pass

                    analysis_log.info(
                        f"[signal {market}] {ticker} none | {none_detail}",
                        extra={"extra": {
                            "event": "signal_check",
                            "market": market,
                            "ticker": ticker,
                            "signal": "none",
                            "reason": "no_signal",
                            "detail": none_detail,
                            "price": price,
                            "mode": mode,
                        }},
                    )
                    self._notify_signal_state_change(
                        "signal_check", market, ticker,
                        price=float(price),
                        reason="no_signal",
                        mode=mode,
                        detail=none_detail,
                    )
                    log.debug(f"  [{ticker}] 신호없음 {price:,}원")
                    # ML DB: NO_SIGNAL 기록 (전략별 near-miss 포함)
                    if _ML_DB_ENABLED:
                        try:
                            _sig_row = sig_df.iloc[i].to_dict()
                            _mr_p2 = _ap("mean_reversion")
                            _vb_p2 = _ap("volatility_breakout")
                            _rsi_v  = float(_sig_row.get("rsi", 50) or 50)
                            _bb_v   = float(_sig_row.get("bb_pct", 50) or 50)
                            _vol_v  = float(_sig_row.get("vol_ratio", 1) or 1)
                            _close_v= float(_sig_row.get("close", 0) or 0)
                            _ma60_v = float(_sig_row.get("ma60", 0) or 0)
                            _ma60_thr2 = float(_mr_p2.get("ma60_thr", 0.90))
                            _mom_d  = kr_momentum_diag or {}
                            _prev_i = sig_df.iloc[i - 1] if i > 0 else _sig_row
                            _k      = float(_vb_p2.get("k", 0.45))
                            _vb_tgt = float(_prev_i.get("open", 0) or 0) + (
                                float(_prev_i.get("high", 0) or 0) - float(_prev_i.get("low", 0) or 0)
                            ) * _k
                            _ml_write_eval(
                                ticker, price, _sig_row, "NO_SIGNAL",
                                diag_json_={"none_detail": none_detail},
                                extra_fields_={
                                "mr_rsi_thr":   _mr_p2.get("rsi_thr"),
                                "mr_bb_thr":    _mr_p2.get("bb_thr"),
                                "mr_rsi_miss":  _rsi_v - float(_mr_p2.get("rsi_thr", 32)),
                                "mr_bb_miss":   _bb_v  - float(_mr_p2.get("bb_thr", 20)),
                                "mr_vol_ok":    _vol_v < 2.5,
                                "mr_ma_ok":     _ma60_v > 0 and _close_v > _ma60_v * _ma60_thr2,
                                "mr_fired":     False,
                                "vb_target":    _vb_tgt,
                                "vb_close_miss":_close_v - _vb_tgt,
                                "vb_vol_ok":    _vol_v > float(_vb_p2.get("vol_mult", 2.0)),
                                "vb_fired":     False,
                                "mom_ma_ok":    _mom_d.get("ma_ok"),
                                "mom_macd_ok":  _mom_d.get("macd_ok"),
                                "mom_vol_ok":   _mom_d.get("vol_ok"),
                                "mom_high_ok":  _mom_d.get("high_ok"),
                                "mom_fired":    False,
                                },
                            )
                        except Exception:
                            pass
                    continue

                # HALT/DEFENSIVE: 전 마켓 진입 차단
                # CAUTIOUS_BEAR 이하: US 신규 진입 차단 (VIX 급등 구간 실증 손실 방지)
                _bear_block = mode in ("HALT", "DEFENSIVE")
                if not _bear_block and market == "US":
                    _US_BEAR_BLOCK_MODES = os.getenv(
                        "US_BEAR_BLOCK_MODES", "HALT,DEFENSIVE,CAUTIOUS_BEAR"
                    ).split(",")
                    _bear_block = mode in _US_BEAR_BLOCK_MODES
                if _bear_block:
                    analysis_log.info(
                        f"[signal {market}] {ticker} blocked_by_mode",
                        extra={"extra": {
                            "event": "signal_blocked",
                            "market": market,
                            "ticker": ticker,
                            "strategy": strategy_name,
                            "mode": mode,
                        }},
                    )
                    self._notify_signal_state_change(
                        "signal_blocked", market, ticker,
                        strategy=strategy_name,
                        price=float(price),
                        reason="signal_blocked",
                        mode=mode,
                    )
                    log.debug(f"  [{ticker}] {strategy_name} 신호 — {mode} 모드 진입 억제")
                    # ML DB: BLOCKED 기록
                    if _ML_DB_ENABLED:
                        try:
                            _sig_row = sig_df.iloc[i].to_dict()
                            _ml_write_eval(
                                ticker, price, _sig_row, "BLOCKED",
                                block_reason_=f"{mode}_mode_block",
                                fired_strategy_=strategy_name,
                                diag_json_={"reason": "mode_block"},
                            )
                        except Exception:
                            pass
                    continue

                # 장중 고점근접 진입 차단 — 당일 고점 대비 -2% 이내 신규 진입 보류
                # 장초반(30분) 제외, KR/US 공통 적용
                # 인버스 ETF 제외 — 고점 근처 = 시장 하락 지속 = 최적 진입점
                _INVERSE_SET = {"114800", "SQQQ"}
                _since_open_for_high = self._market_elapsed_min(market)
                _OPENING_GRACE_MIN   = 30
                if ticker not in _INVERSE_SET and _since_open_for_high > _OPENING_GRACE_MIN:
                    _day_high = float(price_info.get("high", 0) or 0)
                    if _day_high > 0:
                        _from_high_pct = (float(price) - _day_high) / _day_high * 100
                        _FROM_HIGH_BLOCK = float(os.getenv("FROM_HIGH_BLOCK_PCT", "-2.0"))
                        if _from_high_pct > _FROM_HIGH_BLOCK:
                            log.debug(
                                f"  [{ticker}] 고점근접 차단: 현재가={float(price):,.0f} "
                                f"당일고점={_day_high:,.0f} "
                                f"from_high={_from_high_pct:.2f}% > {_FROM_HIGH_BLOCK}% → 진입 보류"
                            )
                            continue

                # 마감 직전 신규 진입 차단 — 당일 청산 불가 방지
                _cutoff = (_KR_ENTRY_CUTOFF_FROM_OPEN_MIN if market == "KR"
                           else _US_ENTRY_CUTOFF_FROM_OPEN_MIN)
                _since_open_min = self._market_elapsed_min(market)
                if _since_open_min >= _cutoff:
                    log.info(f"[마감 직전 차단 {market}] {ticker} — 진입 제한 시간 초과 "
                             f"({_since_open_min:.0f}분 >= {_cutoff}분)")
                    continue

                # ── 진입 우선순위 점수 (Phase 1: 로그/ML 기록, hard cutoff 없음) ────
                try:
                    _ep_elapsed = self._market_elapsed_min(market)
                    _ep_score, _ep_detail = entry_priority_score(
                        strategy_name=strategy_name,
                        sig_row=sig_df.iloc[i].to_dict(),
                        params=params,
                        price_info=price_info,
                        elapsed_min=_ep_elapsed,
                        ticker=ticker,
                    )
                    log.info(
                        f"[entry_priority {market}] {ticker} "
                        f"score={_ep_score:.3f} strategy={strategy_name} "
                        f"strat={_ep_detail.get('strat_score', 0):.3f} "
                        f"from_high={_ep_detail.get('from_high_pct', 'N/A')} "
                        f"ma60={'✓' if _ep_detail.get('ma60_ok') else '✗'}"
                    )
                    analysis_log.info(
                        f"[entry_priority {market}] {ticker} score={_ep_score:.3f}",
                        extra={"extra": {
                            "event":    "entry_priority",
                            "market":   market,
                            "ticker":   ticker,
                            "strategy": strategy_name,
                            "score":    _ep_score,
                            "detail":   _ep_detail,
                            "mode":     mode,
                            "elapsed_min": round(_ep_elapsed, 1),
                        }},
                    )
                except Exception:
                    _ep_score = 0.0
                    _ep_detail = {}

                # ── entry_priority cutoff (Phase 2 — env/텔레그램 ON/OFF) ────
                if self.entry_priority_cutoff_enabled and _ep_score < self.entry_priority_cutoff:
                    log.info(
                        f"  [{ticker}] entry_priority cutoff: "
                        f"score={_ep_score:.3f} < {self.entry_priority_cutoff} → 진입 보류"
                    )
                    if _ML_DB_ENABLED:
                        _ml_write_eval(
                            ticker, price, sig_df.iloc[i].to_dict(), "SKIPPED",
                            block_reason_="entry_priority_cutoff",
                            diag_json_={"score": _ep_score, "cutoff": self.entry_priority_cutoff},
                        )
                    continue

                sl_cap = abs(HARD_RULES["max_single_loss_pct"]) / 100.0
                # ── ATR 기반 동적 TP/SL ───────────────────────────────────────
                # ATR이 있으면 변동성에 비례한 손절/목표가 사용 (Risk:Reward 2:1)
                # ATR 없거나 계산 불가면 전략 파라미터 폴백
                _atr_val = float(sig_df.iloc[i].get("atr", 0) or 0)
                if _atr_val > 0 and float(price) > 0:
                    _atr_r = _atr_val / float(price)
                    sl_pct = min(max(_atr_r * float(os.getenv("ATR_SL_MULT", "1.5")), 0.01), sl_cap)
                    tp_pct = max(_atr_r * float(os.getenv("ATR_TP_MULT", "3.0")), 0.03)
                    # mean_reversion: BB 중선이 더 자연스러운 목표 — BB가 ATR보다 가까우면 BB 우선
                    if strategy_name == "mean_reversion" and params.get("tp_bb_mid"):
                        bb_mid = float(sig_df.iloc[i].get("ma20", price))
                        _bb_tp = max((bb_mid - float(price)) / float(price), 0.005) if bb_mid > float(price) else tp_pct
                        tp_pct = _bb_tp
                    log.debug(f"  [{ticker}] ATR동적 SL={sl_pct:.2%} TP={tp_pct:.2%} (ATR={_atr_val:.2f})")
                else:
                    # 폴백: 전략 파라미터 고정값
                    sl_pct = min(params.get("sl_pct", 0.03), sl_cap)
                    if strategy_name == "mean_reversion" and params.get("tp_bb_mid"):
                        bb_mid = float(sig_df.iloc[i].get("ma20", price))
                        tp_pct = max((bb_mid - float(price)) / float(price), 0.005) if bb_mid > float(price) else 0.03
                    else:
                        tp_pct = params.get("tp_pct", HARD_RULES["take_profit_pct"] / 100.0)
                atr_pct = None
                if self.enable_atr_position_sizing:
                    atr_val = float(sig_df.iloc[i].get("atr", 0) or 0)
                    if risk_price > 0 and atr_val > 0:
                        atr_pct = atr_val / risk_price
                # 전략별 size_mult 적용 (예: momentum 0.4~1.0 → 합의 size_pct 스케일링)
                size_mult = float(params.get("size_mult", 1.0))

                # ── 1. entry_priority score → size_mult 보정 ────────────────
                # 신호 품질이 낮을수록 포지션 축소 (phase 1 데이터 없어도 즉시 적용)
                _ep_size_mult = 1.0
                if _ep_score >= 0.8:
                    _ep_size_mult = 1.0
                elif _ep_score >= 0.5:
                    _ep_size_mult = 0.7
                else:
                    _ep_size_mult = 0.5
                size_mult = round(size_mult * _ep_size_mult, 2)
                if _ep_size_mult < 1.0:
                    log.info(
                        f"  [{ticker}] score={_ep_score:.3f} → size_mult × {_ep_size_mult} = {size_mult:.2f}"
                    )

                # ── 2. 당일 손절 카운터 → size_mult 자동 축소 ───────────────
                _sl_cnt_now = self._daily_sl_count.get(market, 0)
                if _sl_cnt_now >= 3:
                    log.warning(
                        f"  [{ticker}] 당일 손절 {_sl_cnt_now}회 — 신규 진입 차단"
                    )
                    continue
                elif _sl_cnt_now == 2:
                    size_mult = round(size_mult * 0.6, 2)
                    log.info(f"  [{ticker}] 당일 손절 2회 → size_mult × 0.6 = {size_mult:.2f}")
                elif _sl_cnt_now == 1:
                    size_mult = round(size_mult * 0.8, 2)
                    log.info(f"  [{ticker}] 당일 손절 1회 → size_mult × 0.8 = {size_mult:.2f}")

                # ── 3. 장 후반 진입 기준 강화 ────────────────────────────────
                # KR 14:00 이후 / US 03:00 이후: score < 0.6이면 진입 보류
                # 인버스 ETF 제외 — 장 후반 하락 지속 시 진입 기회가 오히려 더 좋음
                _late_session_min = {"KR": 300, "US": 270}  # KR=5h, US=4.5h from open
                _since_open_late  = self._market_elapsed_min(market)
                _late_threshold   = _late_session_min.get(market, 999)
                if ticker not in _INVERSE_SET and _since_open_late >= _late_threshold:
                    _LATE_SCORE_MIN = float(os.getenv("LATE_SESSION_SCORE_MIN", "0.6"))
                    if _ep_score < _LATE_SCORE_MIN:
                        log.info(
                            f"  [{ticker}] 장 후반({_since_open_late:.0f}분) "
                            f"score={_ep_score:.3f} < {_LATE_SCORE_MIN} → 진입 보류"
                        )
                        continue

                effective_size = max(10, min(100, int(size_pct * size_mult)))
                # ── 신호 수집 → 사이클 완료 후 score 정렬 후 주문 실행 ────────
                _pending_signals.append({
                    "ticker":          ticker,
                    "score":           _ep_score,
                    "ep_detail":       _ep_detail,
                    "strategy_name":   strategy_name,
                    "price":           float(price),
                    "risk_price":      float(risk_price),
                    "sig_row":         sig_df.iloc[i].to_dict(),
                    "tp_pct":          float(tp_pct),
                    "sl_pct":          float(sl_pct),
                    "atr_pct":         atr_pct,
                    "effective_size":  effective_size,
                    "max_hold":        int(params.get("max_hold", 1)),
                    "selected_reason": (self.today_ticker_reasons.get(market, {}) or {}).get(ticker, ""),
                    "tsdb_id":         (self._tsdb_selection_ids.get(market) or {}).get(ticker),
                })

            except Exception as e:
                log.error(f"cycle error [{ticker}]: {e}")

        # ── 수집된 신호 정렬 후 주문 실행 ────────────────────────────────────
        if _pending_signals:
            _pending_signals.sort(key=lambda s: s["score"], reverse=True)
            log.info(
                f"[{market}] 신호 정렬: " +
                ", ".join(f"{s['ticker']}({s['score']:.3f})" for s in _pending_signals)
            )
            for _sig in _pending_signals:
                _s_tk   = _sig["ticker"]
                _s_px   = _sig["price"]
                _s_rpx  = _sig["risk_price"]
                _s_row  = _sig["sig_row"]
                _s_strat= _sig["strategy_name"]
                _s_tp   = _sig["tp_pct"]
                _s_sl   = _sig["sl_pct"]
                _s_atr  = _sig["atr_pct"]
                _s_esz  = _sig["effective_size"]
                _s_mh   = _sig["max_hold"]
                _s_sr   = _sig["selected_reason"]
                _s_ep   = _sig["score"]
                _tsdb_id = _sig.get("tsdb_id")
                _sig_at  = datetime.now(KST).isoformat()
                try:
                    avail = self._market_budget_available(market)
                    qty = self.risk.calc_order_size(
                        _s_rpx, _s_esz, _s_sl,
                        atr_pct=_s_atr, atr_target_pct=self.atr_target_pct,
                    )
                    if qty <= 0:
                        if _ML_DB_ENABLED:
                            _ml_write_eval(_s_tk, _s_px, _s_row, "SKIPPED",
                                           block_reason_="qty_zero",
                                           diag_json_={"reason": "qty_zero"})
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, "qty_zero")
                        log.debug(f"  [{_s_tk}] 주문가능 수량 0주")
                        continue
                    order_cost = qty * _s_rpx
                    if order_cost > avail:
                        if _ML_DB_ENABLED:
                            _ml_write_eval(_s_tk, _s_px, _s_row, "SKIPPED",
                                           block_reason_="market_budget_exceeded",
                                           diag_json_={"order_cost_krw": float(order_cost), "available_budget_krw": float(avail)})
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, "market_budget_exceeded")
                        log.debug(f"  [{_s_tk}] 시장 예산 초과 ({order_cost:,.0f}원 > {avail:,.0f}원) → 스킵")
                        continue
                    if order_cost > self.risk.cash:
                        if _ML_DB_ENABLED:
                            _ml_write_eval(_s_tk, _s_px, _s_row, "SKIPPED",
                                           block_reason_="insufficient_cash",
                                           diag_json_={"order_cost_krw": float(order_cost), "cash_krw": float(self.risk.cash)})
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, "insufficient_cash")
                        log.debug(f"  [{_s_tk}] 현금 부족 ({order_cost:,.0f}원 > {self.risk.cash:,.0f}원) → 스킵")
                        continue
                    analysis_log.info(
                        f"[signal {market}] {_s_tk} {_s_strat} qty={qty}",
                        extra={"extra": {
                            "event": "entry_signal", "market": market, "ticker": _s_tk,
                            "strategy": _s_strat, "mode": mode, "price": _s_px,
                            "risk_price_krw": _s_rpx, "qty": qty,
                            "order_cost_krw": order_cost, "tp_pct": _s_tp, "sl_pct": _s_sl,
                        }},
                    )
                    self._notify_signal_state_change(
                        "entry_signal", market, _s_tk,
                        strategy=_s_strat, price=float(_s_px), mode=mode,
                        qty=qty, order_cost_krw=int(order_cost),
                    )
                    order_px = self._compute_order_price("buy", market, float(_s_px))
                    precheck_px = float(_s_px) if order_px == 0 else order_px
                    precheck = precheck_order(_s_tk, qty, precheck_px, "buy", self.token, market=market)
                    if not precheck.get("ok"):
                        self._record_decision_event(
                            market, "buy_failed", _s_tk, strategy=_s_strat, qty=qty,
                            price_native=order_px, price_krw=_s_rpx,
                            reason=precheck.get("reason", "precheck_failed"),
                            detail=precheck.get("msg", ""), selected_reason=_s_sr,
                        )
                        analysis_log.info(
                            f"[signal {market}] {_s_tk} precheck_failed",
                            extra={"extra": {
                                "event": "entry_failed", "market": market, "ticker": _s_tk,
                                "strategy": _s_strat,
                                "reason": precheck.get("reason", "precheck_failed"),
                                "detail": precheck.get("msg", ""),
                                "price": _s_px, "mode": mode,
                            }},
                        )
                        log.info(f"[주문 사전체크 실패] {_s_tk} — {precheck.get('msg','주문 불가')}")
                        if _ML_DB_ENABLED:
                            _ml_write_eval(_s_tk, _s_px, _s_row, "BLOCKED",
                                           block_reason_=precheck.get("reason", "precheck_failed"),
                                           strategy_used_=_s_strat, fired_strategy_=_s_strat,
                                           diag_json_={"detail": precheck.get("msg", ""), "stage": "precheck"})
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, precheck.get("reason", "precheck_failed"))
                        continue
                    try:
                        result = place_order(_s_tk, qty, order_px, "buy", self.token, market=market)
                    except Exception as e:
                        self._record_decision_event(
                            market, "buy_failed", _s_tk, strategy=_s_strat, qty=qty,
                            price_native=order_px, price_krw=_s_rpx,
                            reason="order_exception", detail=str(e), selected_reason=_s_sr,
                        )
                        if market == "US" and self.is_paper:
                            _reason = f"VTS 주문실패: {e}"
                            self._mark_us_order_blocked(_s_tk, _reason)
                            analysis_log.info(
                                f"[signal {market}] {_s_tk} order_exception",
                                extra={"extra": {
                                    "event": "entry_failed", "market": market, "ticker": _s_tk,
                                    "strategy": _s_strat, "reason": "us_order_exception",
                                    "detail": _reason, "price": _s_px, "mode": mode,
                                }},
                            )
                        if _ML_DB_ENABLED:
                            _ml_write_eval(_s_tk, _s_px, _s_row, "BLOCKED",
                                           block_reason_="order_exception",
                                           strategy_used_=_s_strat, fired_strategy_=_s_strat,
                                           diag_json_={"detail": str(e), "stage": "place_order"})
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, "order_exception")
                        raise
                    if not result["success"]:
                        self._record_decision_event(
                            market, "buy_failed", _s_tk, strategy=_s_strat, qty=qty,
                            price_native=order_px, price_krw=_s_rpx,
                            reason="order_rejected", detail=result.get("msg", ""),
                            selected_reason=_s_sr,
                        )
                        log.error(f"order failed [{_s_tk}]: {result['msg']}")
                        if market == "US" and self.is_paper:
                            _reason = result.get("msg", "") or "VTS 주문거절"
                            self._mark_us_order_blocked(_s_tk, _reason)
                            analysis_log.info(
                                f"[signal {market}] {_s_tk} order_rejected",
                                extra={"extra": {
                                    "event": "entry_failed", "market": market, "ticker": _s_tk,
                                    "strategy": _s_strat, "reason": "us_order_rejected",
                                    "detail": _reason, "price": _s_px, "mode": mode,
                                }},
                            )
                        if _ML_DB_ENABLED:
                            _ml_write_eval(_s_tk, _s_px, _s_row, "BLOCKED",
                                           block_reason_="order_rejected",
                                           strategy_used_=_s_strat, fired_strategy_=_s_strat,
                                           diag_json_={"detail": result.get("msg", ""), "stage": "place_order"})
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, "order_rejected")
                        continue
                    if market == "US":
                        self._mark_us_order_supported(_s_tk)
                    log.info(
                        f"[{'PAPER' if self.is_paper else 'LIVE'} BUY] "
                        f"{_s_tk} {qty}@{_s_px:,} | {_s_strat} | "
                        f"주문번호={result.get('order_no','')} | score={_s_ep:.3f}"
                    )
                    self._record_decision_event(
                        market, "buy_order", _s_tk, strategy=_s_strat, qty=qty,
                        price_native=float(_s_px), price_krw=_s_rpx,
                        reason=f"{_s_strat} 진입",
                        detail=(f"선택이유: {_s_sr}" if _s_sr
                                else f"mode={mode} tp={_s_tp:.2%} sl={_s_sl:.2%}"),
                        order_no=result.get("order_no", ""), selected_reason=_s_sr,
                    )
                    _ml_decision_id = -1
                    if _ML_DB_ENABLED:
                        try:
                            _ml_decision_id = _ml_write_eval(
                                _s_tk, _s_px, _s_row, "BUY_SIGNAL",
                                strategy_used_=_s_strat, fired_strategy_=_s_strat,
                                diag_json_={
                                    "selected_reason": _s_sr or "",
                                    "tp_pct": float(_s_tp), "sl_pct": float(_s_sl),
                                    "entry_priority_score": _s_ep,
                                },
                            )
                        except Exception:
                            pass
                    try:
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at)
                        tsdb.update_traded(_tsdb_id, datetime.now(KST).isoformat())
                    except Exception:
                        pass
                    self._add_pending_order({
                        "order_no":       result.get("order_no", ""),
                        "ticker":         _s_tk,
                        "market":         market,
                        "qty":            qty,
                        "raw_price":      float(_s_px),
                        "risk_price_krw": float(_s_rpx),
                        "strategy":       _s_strat,
                        "tp_pct":         float(_s_tp),
                        "sl_pct":         float(_s_sl),
                        "max_hold":       _s_mh,
                        "created_at":     datetime.now(KST).isoformat(),
                        "decision_id":    _ml_decision_id,
                    })
                    self._block_entry(_s_tk, _BUY_COOLDOWN_MIN, "buy_placed")
                    _s_tk_disp = display_ticker(_s_tk, market)
                    send(
                        f"🟡 <b>[매수 주문 접수]</b> {_s_tk_disp}\n"
                        f"{qty}주 | 주문번호 {result.get('order_no','')}\n"
                        f"체결 확인 후 보유 포지션에 반영됩니다."
                    )
                except Exception as e:
                    log.error(f"pending signal execute error [{_s_tk}]: {e}")

        # ── Tier 2 섹터 플레이 (US 전용) ─────────────────────────────────────
        # Core 5 루프 완료 후, 섹터 ETF 강세 신호가 있으면 추가 진입 시도
        if market == "US" and mode not in ("HALT", "DEFENSIVE", "CAUTIOUS_BEAR"):
            try:
                _digest_raw = self.today_judgment.get("digest_raw", {})
                _sectors = (_digest_raw.get("context") or {}).get("sectors", {})
                _digest_summary = self.today_judgment.get("digest_prompt", "")[:300]
                _tier2_plays = run_sector_plays(
                    sectors=_sectors,
                    market_mode=mode,
                    digest_summary=_digest_summary,
                    max_plays=2,
                )
                for _play in _tier2_plays:
                    _t2_ticker = _play["ticker"]
                    try:
                        # 이미 포지션 보유 중이면 스킵
                        if any(p["ticker"] == _t2_ticker for p in self.risk.positions):
                            log.debug(f"  [Tier2] {_t2_ticker} 이미 포지션 보유 — 스킵")
                            continue
                        # 진입 쿨다운 체크
                        if self._is_entry_blocked(_t2_ticker):
                            log.debug(f"  [Tier2] {_t2_ticker} 진입 쿨다운 — 스킵")
                            continue
                        # US 주문 차단 체크
                        _t2_blocked = self._us_order_block_reason(_t2_ticker)
                        if _t2_blocked:
                            log.debug(f"  [Tier2] {_t2_ticker} 주문차단: {_t2_blocked}")
                            continue
                        _t2_price_info = get_price(_t2_ticker, self.token, market="US")
                        _t2_price = _t2_price_info["price"]
                        if _t2_price <= 0:
                            continue
                        _t2_risk_price = self._price_to_krw(_t2_price, "US")
                        self.price_cache_raw[_t2_ticker] = _t2_price
                        self.price_cache[_t2_ticker] = _t2_risk_price
                        self.risk.update_prices(self.price_cache)
                        # 50% 사이즈 적용
                        _t2_size = max(10, int(size_pct * TIER2_SIZE_RATIO))
                        _t2_sl, _t2_tp = 0.025, 0.05  # Tier2 고정 SL 2.5% / TP 5%
                        _t2_qty = self.risk.calc_order_size(
                            _t2_risk_price, _t2_size, _t2_sl
                        )
                        if _t2_qty <= 0:
                            continue
                        _t2_cost = _t2_qty * _t2_risk_price
                        if _t2_cost > self.risk.cash:
                            log.debug(f"  [Tier2] {_t2_ticker} 현금 부족 ({_t2_cost:,.0f}원)")
                            continue
                        _t2_order_px = self._compute_order_price("buy", "US", float(_t2_price))
                        _t2_pre = precheck_order(
                            _t2_ticker, _t2_qty, _t2_order_px, "buy", self.token, market="US"
                        )
                        if not _t2_pre.get("ok"):
                            log.info(f"  [Tier2] {_t2_ticker} precheck 실패: {_t2_pre.get('msg')}")
                            continue
                        _t2_result = place_order(
                            _t2_ticker, _t2_qty, _t2_order_px, "buy", self.token, market="US"
                        )
                        if not _t2_result["success"]:
                            log.error(f"  [Tier2] {_t2_ticker} 주문실패: {_t2_result.get('msg')}")
                            continue
                        log.info(
                            f"[Tier2 BUY] {_t2_ticker} {_t2_qty}주 @{_t2_price} "
                            f"| ETF={_play['etf']} {_play['etf_chg']:+.2f}% "
                            f"| conf={_play['confidence']:.2f} | {_play['reason']}"
                        )
                        self._record_decision_event(
                            "US", "buy_order", _t2_ticker,
                            strategy="sector_play",
                            qty=_t2_qty,
                            price_native=float(_t2_price),
                            price_krw=_t2_risk_price,
                            reason=f"Tier2 섹터플레이 [{_play['etf']} {_play['etf_chg']:+.2f}%]",
                            detail=_play["reason"],
                            order_no=_t2_result.get("order_no", ""),
                            selected_reason=f"sector_etf={_play['etf']}",
                        )
                        self._add_pending_order({
                            "order_no":      _t2_result.get("order_no", ""),
                            "ticker":        _t2_ticker,
                            "market":        "US",
                            "qty":           _t2_qty,
                            "raw_price":     float(_t2_price),
                            "risk_price_krw": float(_t2_risk_price),
                            "strategy":      "sector_play",
                            "tp_pct":        float(_t2_tp),
                            "sl_pct":        float(_t2_sl),
                            "max_hold":      1,
                            "created_at":    datetime.now(KST).isoformat(),
                        })
                        self._block_entry(_t2_ticker, _BUY_COOLDOWN_MIN, "buy_placed")
                        _t2_disp = display_ticker(_t2_ticker, "US")
                        send(
                            f"🟡 <b>[Tier2 섹터 매수]</b> {_t2_disp}\n"
                            f"{_t2_qty}주 | {_play['etf']} {_play['etf_chg']:+.2f}% 섹터강세\n"
                            f"conf={_play['confidence']:.2f} | {_play['reason']}"
                        )
                    except Exception as _t2_e:
                        log.error(f"[Tier2] {_t2_ticker} 오류: {_t2_e}")
            except Exception as _t2_loop_e:
                log.error(f"[Tier2 섹터플레이] 루프 오류: {_t2_loop_e}")
        # ── Tier 2 섹터 플레이 끝 ──────────────────────────────────────────────

        log.info(
            f"[{market} 사이클 완료] 포지션:{len(self.risk.positions)}개 | "
            f"현금:{self.risk.cash:,.0f}원"
        )
        self._write_live_status(market)
        self._maybe_push_dashboard()  # 포지션/P&L 변화 있으면 텔레그램 전송
        self._leave_market_task(market, "run_cycle")

    def run_tuning(self, market: str):
        if not self._enter_market_task(market, "run_tuning"):
            return
        if not self.session_active:
            self._leave_market_task(market, "run_tuning")
            return
        if self.current_market != market:
            self._leave_market_task(market, "run_tuning")
            return

        self.tuning_count += 1
        elapsed = self.tuning_count * 30

        current_state = {
            "index_change": get_index_change(market),
            "volume_trend": "normal",
            "positions": [
                {
                    "ticker": p["ticker"],
                    "qty": p["qty"],
                    "current_price": p["current_price"],
                }
                for p in self.risk.positions
            ],
            "alerts": [],
        }
        brain_summary = BrainDB.generate_prompt_summary(market)
        result = tune(market, elapsed, current_state, self.today_judgment, brain_summary)

        action = result.get("action", "MAINTAIN")
        if action != "MAINTAIN":
            old_mode = self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL")
            new_mode = result.get("mode", old_mode)
            self.today_judgment.setdefault("consensus", {})["mode"] = new_mode
            # 모드 변경 시 종목 재선택 (단, REVERSE는 _reinvoke에서 처리)
            if old_mode != new_mode and action != "REVERSE":
                try:
                    log.info(f"[튜너 종목갱신] {old_mode}→{new_mode} 모드 변경 → 종목 재선택")
                    if market == "KR":
                        tune_cands = screen_market_kr(self.token)
                    else:
                        tune_cands = screen_market_us()
                    if tune_cands:
                        self._log_screen_candidates(market, tune_cands, "tuning_rescreen")
                        digest_p = self.today_judgment.get("digest_prompt", "")
                        tune_tickers, tune_reasons = select_tickers(market, digest_p, new_mode, tune_cands)
                        self.today_tickers[market] = tune_tickers
                        self.today_ticker_reasons[market] = tune_reasons or {}
                        self.today_judgment["tickers"] = tune_tickers
                        self.today_judgment["universe_tickers"] = [
                            c.get("ticker") for c in tune_cands if c.get("ticker")
                        ]
                        log.info(f"[튜너 종목갱신 완료] {market}: {tune_tickers}")
                        tune_excluded = [c.get("ticker", "") for c in tune_cands if c.get("ticker", "") not in tune_tickers]
                        _mode_order_limit = self.risk.calc_order_budget(self.today_judgment.get("consensus", {}).get("size", 50))
                        watchlist_alert(market, new_mode, tune_tickers, tune_reasons, tune_excluded, trigger="rescreen",
                                        mode_order_limit_krw=_mode_order_limit)
                except Exception as te:
                    log.warning(f"[튜너 종목갱신 실패] {te}")
            sl_adj = result.get("sl_adj", 0)
            if sl_adj != 0:
                adj_clamped = max(-0.10, min(0.10, float(sl_adj)))  # ±10% 이내로 제한
                for pos in self.risk.positions:
                    pos["sl"] = pos["sl"] * (1 + adj_clamped)
                log.info(f"SL adjusted: {adj_clamped:+.3f}")

            # REVERSE: Claude가 장세 반전 판단 → 보유 포지션 전체 청산
            if action == "REVERSE" and self.risk.positions:
                log.warning(f"[REVERSE] 튜너 판단: {result.get('reason','')} — 포지션 전체 청산")
                for pos in list(self.risk.positions):
                    cp = self.price_cache.get(pos["ticker"], pos["current_price"])
                    if not self.is_paper:
                        place_order(pos["ticker"], pos["qty"], 0, "sell",
                                    self.token, market=self._ticker_market(pos["ticker"]))
                    ex = self.risk.close_position(pos["ticker"], cp, "tuner_reverse")
                    if ex:
                        pnl_alert(ex["ticker"], ex["pnl_pct"], int(ex["pnl"]), "tuner_reverse")
                        trade_alert("sell", ex["ticker"], ex["qty"], int(cp),
                                    ex["strategy"], 0, 0, reason="tuner_reverse", market=market)
            self._persist_live_judgment(market)

        warning = result.get("warning")

        # 모드가 바뀐 경우만 tuning_report 전송, 유지면 스킵
        if action != "MAINTAIN":
            tuning_report(elapsed, result, old_mode, self.risk.positions)
            self._maybe_push_dashboard(force=True)
        else:
            if warning == "OVERLOADED":
                log.warning(
                    f"[튜닝 {elapsed}분] Claude 과부하로 튜닝 생략 — 기존 모드 유지"
                )
                tuning_report(elapsed, result, self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"), self.risk.positions)
            else:
                log.info(f"[튜닝 {elapsed}분] MAINTAIN — 텔레그램 스킵")

        # 세션 이벤트 기록 (파인튜닝 데이터 — MAINTAIN 포함 전체 기록)
        self._session_events.append({
            "type":    "tuning",
            "elapsed": elapsed,
            "action":  result.get("action", "MAINTAIN"),
            "mode":    result.get("mode", ""),
            "reason":  result.get("reason", ""),
            "warning": result.get("warning"),
            "index_change": current_state.get("index_change", 0),
        })

        # ── 긴급 재판단 조건 체크 ──────────────────────────────────────────────
        should_reinvoke, trigger = self._should_reinvoke_analysts(result, current_state)
        if should_reinvoke:
            self._reinvoke_analysts(market, trigger)

        # ── 120분마다 부분 재선택 (긴급재판단과 독립, 모드 유지 시도 포함) ──
        if elapsed % 120 == 0 and elapsed >= 120 and not should_reinvoke:
            try:
                self._partial_reselect(market)
            except Exception as _pre:
                log.warning(f"[부분교체 오류] {market}: {_pre}")

        self._leave_market_task(market, "run_tuning")

    # ── 긴급 재판단 ───────────────────────────────────────────────────────────

    # 분석가 재투입 조건 (하나라도 해당하면 True)
    _REINVOKE_INDEX_THRESHOLD = -2.0   # 지수 -2% 이상 급락
    _REINVOKE_COOLDOWN_CYCLES = 2      # 최소 2사이클(60분) 간격
    _REINVOKE_SAME_MODE_EXTRA = 2      # 같은 모드 유지 후 재발동 시 추가 대기 사이클

    # ── 히스토리 보강 ─────────────────────────────────────────────────────────

    def _prefill_history_sync(
        self, candidates: list, market: str,
        max_tickers: int = 8, timeout_sec: float = 20.0,
    ) -> list:
        """
        session_open 직전 짧은 동기 보강.
        데이터 부족 종목 중 상위 max_tickers개를 timeout_sec 이내에 즉시 보강하고
        나머지는 백그라운드 큐로 넘긴다.
        반환값: 원본 candidates 그대로 (CSV 갱신 + 캐시 무효화만 수행).
        이후 _filter_candidates_by_history가 다시 필터링하면 보강된 종목이 통과.
        """
        import pandas as _pd
        from pathlib import Path as _Path

        _price_base = _Path(__file__).parent / "data" / "price"
        _MIN = self._MIN_SIGNAL_ROWS

        # 식별: on-demand fetch 없이 메모리 캐시 → CSV 행 수만 빠르게 확인
        # (API 호출 유발 금지 — timeout 전에 식별만으로 시간 소진 방지)
        shortage = []
        for c in candidates:
            ticker = c.get("ticker", "")
            if not ticker:
                continue
            # 1순위: 메모리 캐시
            cached = self._ohlcv_cache.get(ticker)
            if cached is not None and len(cached) >= _MIN:
                continue
            # 2순위: CSV 행 수 직접 확인 (헤더 포함 N+1행만 읽음)
            _csv = _price_base / market.lower() / f"{market.lower()}_{ticker}.csv"
            if _csv.exists():
                try:
                    n = sum(1 for _ in open(_csv, encoding="utf-8-sig")) - 1  # 헤더 제외
                    if n >= _MIN + 60:   # ma60 warm-up 버퍼 포함 → usable row 확보
                        continue
                except Exception:
                    pass
            shortage.append(ticker)

        if not shortage:
            return candidates

        to_fill = shortage[:max_tickers]
        rest    = shortage[max_tickers:]
        log.info(
            f"[동기 보강] {market} 부족 {len(shortage)}개 중 {len(to_fill)}개 즉시 시도"
            f" (timeout={timeout_sec}s)"
        )

        def _fill_one(ticker: str) -> tuple:
            """
            반환: (ticker, status)
              "ok"                  — 저장 + usable rows >= _MIN_SIGNAL_ROWS
              "saved_insufficient"  — 저장 성공, 그러나 usable rows 부족
              "failed"              — 저장 실패
            """
            try:
                _csv_path = _price_base / market.lower() / f"{market.lower()}_{ticker}.csv"
                df = get_daily_ohlcv(ticker, self.token, lookback_days=500, market=market)
                if df.empty or len(df) < 10:
                    return ticker, "failed"
                _csv_path.parent.mkdir(parents=True, exist_ok=True)
                if _csv_path.exists():
                    existing = _pd.read_csv(_csv_path, parse_dates=["date"])
                    existing.columns = [c_.lower() for c_ in existing.columns]
                    df = (_pd.concat([existing, df])
                          .drop_duplicates("date")
                          .sort_values("date")
                          .reset_index(drop=True))
                df.to_csv(_csv_path, index=False, encoding="utf-8-sig")
                self._ohlcv_cache.pop(ticker, None)
                self._ohlcv_cache_time.pop(ticker, None)
                self._hist_fill_last_ts[ticker] = time.time()
                # 저장 후 실제 신호 계산 가능 여부 확인
                usable = len(calc_all(df))
                if usable >= self._MIN_SIGNAL_ROWS:
                    return ticker, "ok"
                return ticker, f"saved_insufficient({usable}usable/{len(df)}raw)"
            except Exception as e:
                log.debug(f"[동기 보강 실패] {ticker}: {e}")
                return ticker, "failed"

        # daemon thread + result queue — timeout 후 즉시 반환 (executor wait 블록 없음)
        import queue as _q
        result_q: _q.Queue = _q.Queue()

        def _fill_and_report(t: str):
            result_q.put(_fill_one(t))

        threads = [
            threading.Thread(target=_fill_and_report, args=(t,), daemon=True)
            for t in to_fill
        ]
        for th in threads:
            th.start()

        filled_ok: set = set()
        deadline = time.time() + timeout_sec
        for _ in to_fill:
            remaining = deadline - time.time()
            if remaining <= 0:
                log.info(f"[동기 보강] {market} timeout {timeout_sec}s — 완료분만 반영")
                break
            try:
                ticker, status = result_q.get(timeout=remaining)
                if status == "ok":
                    filled_ok.add(ticker)
                    log.info(f"[동기 보강 신호가능] {market} {ticker}")
                elif status.startswith("saved_insufficient"):
                    log.info(f"[동기 보강 저장완료/여전히부족] {market} {ticker} {status}")
                    self._hist_fill_enqueue(ticker, market)
                else:
                    log.debug(f"[동기 보강 실패] {market} {ticker}")
            except _q.Empty:
                log.info(f"[동기 보강] {market} timeout {timeout_sec}s — 완료분만 반영")
                break

        # timeout 내 미수신 + 범위 초과 종목은 백그라운드 큐로
        for t in to_fill:
            if t not in filled_ok:
                self._hist_fill_enqueue(t, market)
        for t in rest:
            self._hist_fill_enqueue(t, market)

        not_filled = len(to_fill) - len(filled_ok)
        log.info(
            f"[동기 보강 결과] {market} 성공 {len(filled_ok)}개 / 실패 {not_filled}개"
            f" / 백그라운드 대기 {len(rest) + not_filled}개"
        )
        return candidates   # 구조 변경 없이 반환, _filter가 재판단

    def _hist_fill_enqueue(self, ticker: str, market: str):
        """히스토리 보강 큐 등록 — 중복/쿨다운 방지"""
        if ticker in self._hist_fill_queued or ticker in self._hist_fill_inflight:
            return
        last_ts = self._hist_fill_last_ts.get(ticker, 0)
        if time.time() - last_ts < 3 * 86400:   # 3일 쿨다운
            return
        self._hist_fill_queued.add(ticker)
        self._hist_fill_queue.put((ticker, market))
        log.debug(f"[히스토리 보강 큐] {market} {ticker} 등록")

    def _history_fill_worker(self):
        """백그라운드 히스토리 보강 워커 — OHLCV 부족 종목에 365일치 수집"""
        import pandas as _pd
        from pathlib import Path as _Path
        while True:
            try:
                ticker, market = self._hist_fill_queue.get(timeout=10)
            except Exception:
                continue
            try:
                self._hist_fill_inflight.add(ticker)
                self._hist_fill_last_ts[ticker] = time.time()
                _price_base = _Path(__file__).parent / "data" / "price"
                _csv_path = _price_base / market.lower() / f"{market.lower()}_{ticker}.csv"
                df = get_daily_ohlcv(ticker, self.token, lookback_days=500, market=market)
                if df.empty or len(df) < 10:
                    log.debug(f"[히스토리 보강 실패] {market} {ticker} — {len(df)}raw행")
                else:
                    try:
                        _csv_path.parent.mkdir(parents=True, exist_ok=True)
                        if _csv_path.exists():
                            existing = _pd.read_csv(_csv_path, parse_dates=["date"])
                            existing.columns = [c.lower() for c in existing.columns]
                            df = (_pd.concat([existing, df])
                                  .drop_duplicates("date")
                                  .sort_values("date")
                                  .reset_index(drop=True))
                        df.to_csv(_csv_path, index=False, encoding="utf-8-sig")
                        self._ohlcv_cache.pop(ticker, None)
                        self._ohlcv_cache_time.pop(ticker, None)
                        # 저장 후 신호 가능 여부 확인
                        usable = len(calc_all(df))
                        if usable >= self._MIN_SIGNAL_ROWS:
                            log.info(f"[히스토리 보강 신호가능] {market} {ticker} → {len(df)}raw/{usable}usable")
                        else:
                            log.info(f"[히스토리 보강 저장완료/여전히부족] {market} {ticker} → {len(df)}raw/{usable}usable")
                    except Exception as _e:
                        log.debug(f"[히스토리 보강 저장 실패] {ticker}: {_e}")
            except Exception as e:
                log.debug(f"[히스토리 보강 오류] {ticker}: {e}")
            finally:
                self._hist_fill_inflight.discard(ticker)
                self._hist_fill_queued.discard(ticker)
                self._hist_fill_queue.task_done()
            time.sleep(2)   # rate limit: 종목 간 2초 간격

    # ── 부분 재선택 ───────────────────────────────────────────────────────────

    def _get_cooldown_excluded(self, market: str) -> set:
        """60분 재진입 금지 — 데이터부족 사유는 쿨다운 제외 (보강 후 복귀 허용)"""
        now = datetime.now(KST).replace(tzinfo=None)
        return {
            e["ticker"]
            for e in self._ticker_exclude_log.get(market, [])
            if e.get("reason", "") != "데이터부족"
            and (now - e["ts"]).total_seconds() < 3600
        }

    def _partial_reselect(self, market: str):
        """2시간마다 활성도 하위 2개(최대 3개) 종목 부분 교체"""
        now = datetime.now(KST).replace(tzinfo=None)
        last = self._partial_reselect_last.get(market)
        if last and (now - last).total_seconds() < 115 * 60:
            return

        current_tickers = list(self.today_tickers.get(market, []))
        if not current_tickers:
            return

        # 보호 대상: 보유 포지션
        protected = {p["ticker"] for p in self.risk.positions}

        def _score(t: str) -> float:
            """높을수록 교체 우선 (무신호 지속 + invalid price 누적)"""
            if t in protected:
                return -999
            return (self._ticker_no_signal_cycles.get(t, 0)
                    + self._invalid_price_count.get(t, 0) * 2)

        non_protected = [t for t in current_tickers if t not in protected]
        if not non_protected:
            log.info(f"[부분교체] {market}: 전 종목 포지션 보유 → 스킵")
            return

        sorted_inactive = sorted(non_protected, key=_score, reverse=True)
        # 기본 2개, 3번째도 무신호 3사이클 이상이면 3개
        n_replace = 2
        if len(sorted_inactive) >= 3 and _score(sorted_inactive[2]) >= 3:
            n_replace = 3
        replace_out = sorted_inactive[:n_replace]

        # 새 후보 스크리닝 + 히스토리 필터
        try:
            if market == "KR":
                new_cands = screen_market_kr(self.token)
            else:
                new_cands = screen_market_us()
        except Exception as e:
            log.warning(f"[부분교체] {market} 스크리닝 실패: {e}")
            return
        if not new_cands:
            return
        new_cands = self._filter_candidates_by_history(new_cands, market)

        # 유지 종목 + 쿨다운 제외
        keep_set = set(current_tickers) - set(replace_out)
        cooldown_set = self._get_cooldown_excluded(market)
        new_cands = [
            c for c in new_cands
            if c.get("ticker") not in keep_set
            and c.get("ticker") not in cooldown_set
        ]
        if not new_cands:
            log.info(f"[부분교체] {market}: 새 후보 없음 → 스킵")
            return

        mode = self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL")
        digest_p = self.today_judgment.get("digest_prompt", "")
        new_selected, new_reasons = select_tickers(market, digest_p, mode, new_cands[:20])

        replace_in = [t for t in new_selected if t not in set(current_tickers)][:n_replace]
        if replace_in:
            try:
                _pr_date = datetime.now(KST).strftime("%Y-%m-%d")
                _tsdb_ids = tsdb.insert_batch(
                    _pr_date, market, "partial", replace_in, new_cands, new_reasons, mode
                )
                self._tsdb_selection_ids.setdefault(market, {}).update(_tsdb_ids)
            except Exception as _te:
                log.warning(f"[tsdb] insert_batch(partial) failed: {_te}")
        if not replace_in:
            log.info(f"[부분교체] {market}: 유효 신규 종목 없음 → 스킵")
            return

        final = [t for t in current_tickers if t not in set(replace_out)] + replace_in
        self.today_tickers[market] = final
        self.today_judgment["tickers"] = final

        # 교체된 종목 쿨다운 등록 (60분)
        for t in replace_out:
            self._ticker_exclude_log.setdefault(market, []).append(
                {"ticker": t, "reason": "부분교체_무신호", "ts": now}
            )
        # 2시간 이상 된 항목 정리
        cutoff = now - timedelta(hours=2)
        self._ticker_exclude_log[market] = [
            e for e in self._ticker_exclude_log[market] if e["ts"] > cutoff
        ]

        self._partial_reselect_last[market] = now
        self._persist_live_judgment(market)

        out_str = ", ".join(replace_out)
        in_str  = ", ".join(replace_in)
        log.info(f"[부분교체] {market}: [{out_str}] → [{in_str}] (무신호 지속)")
        send(f"[종목교체 {market}] {out_str} → {in_str} (무신호 지속)")

    def _should_reinvoke_analysts(self, result: dict, current_state: dict) -> tuple:
        """분석가 재투입 여부. (bool, trigger_reason)"""
        if not self.is_claude_reinvoke_enabled():
            return False, ""
        # 기본 쿨다운 체크
        elapsed = self.tuning_count - self._last_reinvoke_tuning
        if elapsed < self._REINVOKE_COOLDOWN_CYCLES:
            return False, ""

        # 조건 1: 튜너가 방향 전환 권고 — 즉시 허용
        if result.get("action") == "REVERSE":
            return True, f"REVERSE 권고: {result.get('reason', '')[:60]}"

        # 조건 2: 지수 -2% 이상 급락
        idx_chg = current_state.get("index_change", 0)
        if idx_chg <= self._REINVOKE_INDEX_THRESHOLD:
            # 직전 재판단이 같은 모드 유지였으면 추가 쿨다운 적용
            _prev_mode = self.today_judgment.get("consensus", {}).get("mode", "")
            _last_mode = getattr(self, "_last_reinvoke_result_mode", _prev_mode)
            if _last_mode == _prev_mode and elapsed < self._REINVOKE_COOLDOWN_CYCLES + self._REINVOKE_SAME_MODE_EXTRA:
                log.debug(f"[reinvoke skip] 직전 재판단 모드 유지({_prev_mode}) — 추가 쿨다운 중")
                return False, ""
            return True, f"지수 급락 {idx_chg:+.2f}%"

        # 조건 3: 튜너 경고 발령 + TIGHTEN (MAINTAIN이면 낮은 위험)
        if result.get("warning") and result.get("action") == "TIGHTEN":
            return True, f"튜너 경고: {result['warning']}"

        return False, ""

    def _is_market_session_now(self, market: str) -> bool:
        """현재 해당 시장의 거래 시간 여부 (KST 기준)"""
        now_t = datetime.now(KST).time()
        if market == "KR":
            return dt_time(8, 40) <= now_t <= dt_time(16, 10)
        else:  # US
            return now_t >= dt_time(22, 30) or now_t < dt_time(5, 0)

    def _reinvoke_analysts(self, market: str, trigger: str):
        """이상 신호 감지 시 분석가 3명 긴급 재호출 → 판단·합의 갱신"""
        if not self.is_claude_reinvoke_enabled():
            raise RuntimeError("Claude 재판단 기능이 OFF 상태입니다.")
        # 장 외 시간 reinvoke 차단 (수동 명령 제외)
        if not self._is_market_session_now(market) and "수동" not in trigger:
            log.info(f"[reinvoke 스킵] {market} 장 외 시간 — 트리거: {trigger}")
            return
        current_owner = self._market_task_owner.get(market)
        if current_owner not in (None, "session_open", "run_cycle", "run_tuning", "reinvoke", "housekeeping"):
            log.info(f"[skip {market}] reinvoke busy={current_owner}")
            return
        log.warning(f"[긴급 재판단 시작] {market} | 트리거: {trigger}")
        try:
            self.claude_control["last_trigger_at"] = datetime.now(KST).isoformat(timespec="seconds")
            self.claude_control["last_trigger_market"] = market
            self.claude_control["last_trigger_source"] = trigger
            self.claude_control["last_result_status"] = "running"
            self.claude_control["last_error"] = ""
            self._save_claude_control()
            digest_prompt = self.today_judgment.get("digest_prompt", "")
            brain_summary = BrainDB.generate_prompt_summary(market)
            brain_data = BrainDB.load()
            correction = json.dumps(
                brain_data.get("correction_guide", {}).get(market, {}),
                ensure_ascii=False,
            )

            portfolio_info = {
                "cash":          round(self.risk.cash),
                "total_equity":  round(self._kis_total_equity_krw()),
                "max_order_krw": round(self.risk.max_order_krw),
                "n_positions":   len(self.risk.positions),
                "max_positions": HARD_RULES["max_positions"],
            }
            new_judgments = get_three_judgments(
                digest_prompt, brain_summary, correction,
                market=market, portfolio_info=portfolio_info,
            )
            new_consensus = build_consensus(new_judgments, market=market)

            # VIX 동적 사이즈 보정 (재판단 경로)
            if market == "US":
                _digest_ctx = (self.today_judgment.get("digest_raw") or {}).get("context") or {}
                _vix = float(_digest_ctx.get("vix") or 0)
                if _vix > 0:
                    _vm = 1.0
                    for _thr, _m in _VIX_SIZE_TIERS:
                        if _vix >= _thr:
                            _vm = _m
                            break
                    if _vm < 1.0:
                        _os = new_consensus["size"]
                        new_consensus = dict(new_consensus)
                        new_consensus["size"] = max(0, int(_os * _vm))
                        new_consensus["vix_mult"] = _vm
                        log.info(f"[reinvoke VIX={_vix:.1f}] size {_os}% → {new_consensus['size']}%")

            old_mode = self.today_judgment.get("consensus", {}).get("mode", "?")
            debate_meta = new_judgments.pop("_debate", {})

            self.today_judgment["judgments"] = new_judgments
            self.today_judgment["consensus"] = new_consensus
            self.today_judgment["round1_judgments"] = debate_meta.get("r1", {})
            self.today_judgment["debate_changes"] = debate_meta.get("changes", [])
            self._last_reinvoke_tuning = self.tuning_count
            self._last_reinvoke_result_mode = new_consensus.get("mode", "")

            judgment_log.info(
                f"[reinvoke {market}] {old_mode} → {new_consensus['mode']} | {trigger}",
                extra={"extra": {
                    "event": "analyst_reinvoke",
                    "market": market,
                    "trigger": trigger,
                    "old_mode": old_mode,
                    "new_mode": new_consensus["mode"],
                    "judgments": new_judgments,
                    "consensus": new_consensus,
                }},
            )
            log.warning(
                f"[긴급 재판단 완료] {old_mode} → {new_consensus['mode']} "
                f"size={new_consensus['size']}%"
            )

            # 세션 이벤트 기록 (긴급 재판단)
            self._session_events.append({
                "type":          "reinvoke",
                "elapsed":       self.tuning_count * 30,
                "trigger":       trigger,
                "old_mode":      old_mode,
                "new_mode":      new_consensus["mode"],
                "new_judgments": new_judgments,
                "debate_meta":   debate_meta,
            })

            # 모드가 바뀌면 종목도 재선택 (예: DEFENSIVE→MODERATE_BULL 시 인버스 ETF 제거)
            BEAR_MODES  = {"HALT", "DEFENSIVE", "CAUTIOUS_BEAR", "MILD_BEAR"}
            BULL_MODES  = {"AGGRESSIVE", "MODERATE_BULL", "MILD_BULL"}
            mode_flip = (old_mode in BEAR_MODES and new_consensus["mode"] in BULL_MODES) or \
                        (old_mode in BULL_MODES and new_consensus["mode"] in BEAR_MODES)
            if mode_flip or old_mode != new_consensus["mode"]:
                try:
                    log.info(f"[종목 재선택] 모드 변경({old_mode}→{new_consensus['mode']}) → 종목 갱신 시작")
                    if market == "KR":
                        reinvoke_cands = screen_market_kr(self.token)
                    else:
                        reinvoke_cands = screen_market_us()
                    if reinvoke_cands:
                        self._log_screen_candidates(market, reinvoke_cands, "analyst_reinvoke_rescreen")
                        reinvoke_cands = self._filter_candidates_by_history(reinvoke_cands, market)
                        _ri_cooldown = self._get_cooldown_excluded(market)
                        reinvoke_cands = [c for c in reinvoke_cands
                                          if c.get("ticker") not in _ri_cooldown]
                        digest_p = self.today_judgment.get("digest_prompt", "")
                        new_tickers, new_ticker_reasons = select_tickers(market, digest_p,
                                                     new_consensus["mode"], reinvoke_cands)
                        self.today_tickers[market] = new_tickers
                        self.today_ticker_reasons[market] = new_ticker_reasons or {}
                        self.today_judgment["tickers"] = new_tickers
                        self.today_judgment["universe_tickers"] = [
                            c.get("ticker") for c in reinvoke_cands if c.get("ticker")
                        ]
                        log.info(f"[종목 재선택 완료] {market}: {new_tickers}")
                        reinvoke_excluded = [c.get("ticker", "") for c in reinvoke_cands if c.get("ticker", "") not in new_tickers]
                        _mode_order_limit = self.risk.calc_order_budget(new_consensus.get("size", 50))
                        watchlist_alert(market, new_consensus["mode"], new_tickers, new_ticker_reasons, reinvoke_excluded, trigger="rescreen",
                                        mode_order_limit_krw=_mode_order_limit)
                        # 다음 run_cycle부터 새 종목 사용 (WS는 다음 재시작 시 갱신)
                except Exception as te:
                    log.error(f"[종목 재선택 실패] {te}")

            analyst_reinvoke_alert(
                market, trigger, old_mode, new_consensus["mode"],
                new_judgments, new_consensus,
                round1_judgments=debate_meta.get("r1", {}),
                debate_changes=debate_meta.get("changes", []),
            )
            self._persist_live_judgment(market)
            self._maybe_push_dashboard(force=True)
            self.claude_control["last_result_at"] = datetime.now(KST).isoformat(timespec="seconds")
            self.claude_control["last_result_market"] = market
            self.claude_control["last_result_status"] = "success"
            self.claude_control["last_error"] = ""
            self._save_claude_control()

        except Exception as e:
            self.claude_control["last_result_at"] = datetime.now(KST).isoformat(timespec="seconds")
            self.claude_control["last_result_market"] = market
            self.claude_control["last_result_status"] = "error"
            self.claude_control["last_error"] = str(e)
            self._save_claude_control()
            log.error(f"[긴급 재판단 실패] {e}")

    def _build_tg_state(self) -> dict:
        """현재 상태 스냅샷 (변화 감지용)"""
        market = self.current_market or ""
        mode   = self.today_judgment.get("consensus", {}).get("mode", "")
        pos_key = tuple(sorted((p["ticker"], p["qty"]) for p in self.risk.positions))
        pnl_bucket = round(self.risk.daily_pnl / max(self.risk.session_start_equity, 1) * 100, 1)  # 0.1% 단위
        return {
            "market":     market,
            "mode":       mode,
            "pos_key":    pos_key,
            "pnl_bucket": pnl_bucket,
            "cash_m":     round(self.risk.cash / 1_000_000, 2),  # 1만원 단위
            "pending_n":  len(self.pending_orders),
        }

    def _record_decision_event(self, market: str, action: str, ticker: str, **kwargs):
        event = {
            "timestamp": datetime.now(KST).isoformat(timespec="seconds"),
            "market": market,
            "action": action,
            "ticker": ticker,
            "mode": self.today_judgment.get("consensus", {}).get("mode", ""),
            "strategy": kwargs.get("strategy", ""),
            "qty": int(kwargs.get("qty", 0) or 0),
            "price_native": float(kwargs.get("price_native", 0) or 0),
            "price_krw": float(kwargs.get("price_krw", 0) or 0),
            "reason": kwargs.get("reason", ""),
            "detail": kwargs.get("detail", ""),
            "order_no": kwargs.get("order_no", ""),
            "pnl_krw": float(kwargs.get("pnl_krw", 0) or 0),
            "pnl_pct": float(kwargs.get("pnl_pct", 0) or 0),
            "selected_reason": kwargs.get("selected_reason", ""),
        }
        self.decision_event_log.append(event)
        self.decision_event_log = self.decision_event_log[-200:]
        try:
            decision_event_alert(event)
        except Exception as e:
            log.warning(f"decision_event_alert failed: {e}")

    def _write_live_status(self, market: str, force: bool = False):
        """사이클마다 라이브 상태를 state/live_status_{market}.json 에 기록 (대시보드용)
        force=True: 속도 제한 무시 (포지션 긴급 변경 시)
        """
        if not force:
            _now = time.time()
            if _now - self._live_status_written_at.get(market, 0.0) < _LIVE_STATUS_MIN_INTERVAL:
                return  # 최근에 이미 씀 — 중복 I/O 스킵
            self._live_status_written_at[market] = _now
        try:
            status = self.risk.get_status()
            kis_total_equity = round(self._kis_total_equity_krw(), 0)
            normalized_claude = self._normalize_claude_control_state(self.claude_control)
            dedup_positions = {}
            for pos in self.risk.positions:
                ticker = pos.get("ticker", "")
                if self._ticker_market(ticker) != market:
                    continue
                key = ticker.upper() if self._ticker_market(ticker) == "US" else ticker
                avg_price = float(pos.get("display_avg_price", pos.get("entry", pos.get("avg_price", 0))) or 0)
                current_price = float(
                    pos.get("display_current_price", pos.get("current_price", pos.get("entry", pos.get("avg_price", 0))))
                    or 0
                )
                pnl_pct = ((current_price / avg_price) - 1.0) * 100.0 if avg_price > 0 and current_price > 0 else 0.0
                pos_name = str(pos.get("name", "") or "").strip() or self._lookup_ticker_name(ticker, market)
                dedup_positions[key] = {
                    "ticker":        ticker,
                    "name":          pos_name,
                    "qty":           pos.get("qty", 0),
                    "avg_price":     avg_price,
                    "current_price": current_price,
                    "pnl_pct":       round(pnl_pct, 4),
                    "strategy":      pos.get("strategy", ""),
                    "trailing":      pos.get("trailing", False),
                    "price_source":  pos.get("price_source", "runtime"),
                    "currency":      pos.get("display_currency", "KRW"),
                }
            positions = list(dedup_positions.values())
            pending_orders = [
                {
                    "order_no": order.get("order_no", ""),
                    "ticker": order.get("ticker", ""),
                    "market": order.get("market", market),
                    "qty": order.get("qty", 0),
                    "raw_price": order.get("raw_price", 0),
                    "strategy": order.get("strategy", ""),
                    "created_at": order.get("created_at", ""),
                }
                for order in self.pending_orders
                if order.get("market", market) == market
            ]
            path = get_runtime_path("state", f"live_status_{market}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "market":         market,
                    "updated_at":     datetime.now(KST).strftime("%H:%M:%S"),
                    "trading_date":   datetime.now(KST).strftime("%Y-%m-%d"),
                    "mode":           self.today_judgment.get("consensus", {}).get("mode", ""),
                    "mode_size_pct":  self.today_judgment.get("consensus", {}).get("size", 0),
                    "max_order_krw":  round(self.risk.max_order_krw, 0),
                    "session_active": bool(self.session_active and self.current_market == market),
                    "daily_pnl":      round(status.get("daily_pnl", 0), 0),
                    "daily_pnl_pct":  round(self._daily_pnl_pct(), 4),
                    "cash":           round(self.risk.cash, 0),
                    "total_equity":   kis_total_equity,
                    "positions":      positions,
                    "position_count": len(positions),
                    "pending_orders": pending_orders,
                    "pending_count":  len(pending_orders),
                    "claude": {
                        "enabled": bool(self.claude_control.get("enabled", True)),
                        "last_trigger_at": self.claude_control.get("last_trigger_at", ""),
                        "last_trigger_market": self.claude_control.get("last_trigger_market", ""),
                        "last_trigger_source": self.claude_control.get("last_trigger_source", ""),
                        "last_result_at": normalized_claude.get("last_result_at", ""),
                        "last_result_market": normalized_claude.get("last_result_market", ""),
                        "last_result_status": normalized_claude.get("last_result_status", "idle"),
                        "last_error": normalized_claude.get("last_error", ""),
                        "pending_trigger": self.claude_control.get("pending_trigger"),
                    },
                }, f, ensure_ascii=False)
        except Exception as e:
            log.warning(f"live_status 저장 실패: {e}")

    def _maybe_push_dashboard(self, force: bool = False):
        """상태 변화 있을 때만 텔레그램 대시보드 푸시"""
        if not self.session_active:
            return
        state = self._build_tg_state()
        if not force and state == self._last_tg_state:
            return  # 변화 없음 → 스킵

        market    = self.current_market
        mode      = self.today_judgment.get("consensus", {}).get("mode", "?")
        judgments = self.today_judgment.get("judgments", {})
        tickers   = self.today_tickers.get(market, [])
        digest_ctx = (self.today_judgment.get("digest_raw") or {}).get("context") or {}
        risk_value = float(digest_ctx.get("vix", 0) or 0)
        risk_label = "VKOSPI" if market == "KR" else "VIX"
        pnl_pct   = self.risk.daily_pnl / max(self.risk.session_start_equity, 1) * 100
        pnl_krw   = int(self.risk.daily_pnl)

        dashboard_push(market, mode, self.risk.positions,
                       self.risk.cash, pnl_pct, pnl_krw, judgments, tickers,
                       max_order_krw=int(self.risk.max_order_krw),
                       total_fee=int(self.risk.total_fee),
                       risk_value=risk_value,
                       risk_label=risk_label,
                       mode_order_limit_krw=self.risk.calc_order_budget(self.today_judgment.get("consensus", {}).get("size", 50)))
        self._last_tg_state = state

    def _heartbeat(self):
        """1시간마다 로그 + 변화 없어도 강제 텔레그램 전송"""
        if not self.session_active:
            return
        market  = self.current_market
        mode    = self.today_judgment.get("consensus", {}).get("mode", "?")
        pos_txt = ", ".join(f"{p['ticker']}({p['qty']}주)" for p in self.risk.positions) or "없음"
        now_str = datetime.now(KST).strftime("%H:%M")
        log.info(f"[Heartbeat {now_str}] {market} | 모드:{mode} | 포지션:{pos_txt}")
        self._maybe_push_dashboard(force=True)  # 1시간마다 강제 전송

    def session_close(self, market: str):
        log.info(f"[{market}] session_close")
        if self.current_market != market:
            return

        # 장 마감 직전 마지막으로 브로커 잔고/체결을 동기화해 미체결 오판을 줄인다.
        try:
            broker_kr = self._normalize_broker_balance(get_balance(self.token, market="KR", force_refresh=True), "KR")
            broker_us = self._normalize_broker_balance(get_balance(self.token, market="US", force_refresh=True), "US")
            self._reconcile_pending_orders(broker_kr, broker_us)
        except Exception as e:
            log.warning(f"[{market}] session_close 최종 체결 동기화 실패: {e}")

        self._clear_pending_orders_for_market(market, "session_close")
        self.session_active = False
        self.current_market = None
        if self.ws:
            self.ws.stop()
            self.ws = None

        # ── 포지션 정리: 당일 청산 전략만 강제 청산, 멀티데이는 이월 ─────────
        day_trades  = [p for p in list(self.risk.positions) if p.get("max_hold", 1) <= 1]
        multi_days  = [p for p in list(self.risk.positions) if p.get("max_hold", 1) > 1]

        for pos in day_trades:
            cp = self.price_cache.get(pos["ticker"], pos["current_price"])
            raw_cp = self.price_cache_raw.get(pos["ticker"], cp)
            if not self.is_paper:
                order_px = self._compute_order_price("sell", market, float(raw_cp))
                result = place_order(pos["ticker"], pos["qty"], order_px, "sell",
                                     self.token, market=market)
                if not result["success"]:
                    log.error(f"force sell failed [{pos['ticker']}]: {result['msg']}")
            ex = self.risk.close_position(pos["ticker"], cp, "session_close")
            if ex:
                pnl_alert(ex["ticker"], ex["pnl_pct"], int(ex["pnl"]), "session_close")
                trade_alert("sell", ex["ticker"], ex["qty"], int(raw_cp),
                            ex["strategy"], 0, 0, reason="session_close", market=market)
                # hold_advisor 결과 기록 (트레일링 활성화 상태였던 포지션)
                if ex.get("hold_advice"):
                    self._record_hold_advisor_outcome(ex, market, ex["hold_advice"])
            log.warning(f"[당일청산] {pos['ticker']} {cp:,.0f}")

        if multi_days:
            log.info(f"[이월] {[p['ticker'] for p in multi_days]} "
                     f"→ 다음 세션 계속 보유 (held_days: "
                     f"{[p['held_days'] for p in multi_days]})")

        # 이월 포지션 파일 저장 (재시작 대비)
        self._save_positions()

        today = date.today().strftime("%Y-%m-%d")
        session_trades = self._filter_trades_for_market(self.risk.trade_log, market)
        # order_no 기준 중복 제거 (재시작 등으로 trade_log에 동일 체결이 2회 기록되는 케이스)
        _seen_orders: set = set()
        _deduped: list = []
        for t in session_trades:
            key = t.get("order_no") or id(t)
            if key not in _seen_orders:
                _seen_orders.add(key)
                _deduped.append(t)
        if len(_deduped) < len(session_trades):
            log.info(f"[{market}] trade_log 중복 제거: {len(session_trades)} → {len(_deduped)}건")
        session_trades = _deduped
        _sell_log = [t for t in session_trades if t.get("side") == "sell"]
        cumulative_equity = int(round(self._kis_total_equity_krw()))
        actual = {
            "market_change": get_index_change(market),
            "pnl_pct": self.risk.daily_pnl / max(self.risk.session_start_equity, 1) * 100,
            "pnl_krw": int(self.risk.daily_pnl),
            "win": self.risk.daily_pnl > 0,
            "trades": len(_sell_log),   # 청산(매도) 건수만
            "cumulative": cumulative_equity,
        }

        # 판단이 없는 날(HALT, 에러 등)은 postmortem 스킵
        if not self.today_judgment.get("judgments"):
            log.info(f"[{market}] 오늘 판단 없음 — postmortem 생략")
            pm = {}
        else:
            pm = run_postmortem(
                market,
                today,
                self.today_judgment,
                actual,
                self.today_judgment.get("digest_prompt", ""),
                trade_log=session_trades,   # 해당 시장 체결 내역만 전달
                decision_event_log=[e for e in self.decision_event_log if e.get("market") == market],
            )
        for event in [e for e in self.decision_event_log if e.get("market") == market]:
            try:
                BrainDB.update_execution_pattern(market, event)
            except Exception as e:
                log.warning(f"[{market}] execution pattern update failed: {e}")
        judgment_log.info(
            f"[close {today} {market}] pnl={actual['pnl_pct']:+.2f}% mode={self.today_judgment.get('consensus', {}).get('mode', '-')}",
            extra={"extra": {
                "event": "session_close_judgment",
                "date": today,
                "market": market,
                "actual_result": actual,
                "consensus": self.today_judgment.get("consensus", {}),
                "postmortem": pm,
                "trades": session_trades,
            }},
        )

        # ── 파인튜닝/프롬프트 개선을 위한 완전한 training record 저장 ──────────
        record = {
            **self.today_judgment,          # date, market, judgments, consensus,
                                            # digest_prompt, tickers,
                                            # round1_judgments, debate_changes 포함
            "actual_result":  actual,
            "postmortem":     pm,
            "trades":         session_trades,
            "decision_events": [e for e in self.decision_event_log if e.get("market") == market],
            "session_events": self._session_events,   # 튜닝/긴급재판단 전체 이력
            "mode": "paper" if self.is_paper else "live",
        }
        path = JUDGMENT_DIR / f"{today.replace('-', '')}_{market}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        log.info(f"[training record 저장] {path.name} "
                 f"| events={len(self._session_events)}")

        try:
            balance = get_balance(self.token, market=market)
        except Exception as e:
            log.warning(f"balance lookup failed [{market}]: {e}")
            balance = {"stocks": [], "total_eval": 0, "cash": 0, "total_profit": 0, "profit_rate": 0.0}
        sell_trades = [t for t in session_trades if t.get("side") == "sell"]
        wins = sum(1 for t in sell_trades if t.get("pnl", 0) > 0)
        win_rate = wins / len(sell_trades) if sell_trades else 0.0
        daily_summary(
            today,
            market,
            actual["pnl_pct"],
            actual["pnl_krw"],
            actual["trades"],
            win_rate,
            actual["cumulative"],
            self.today_judgment.get("judgments", {}),
            pm,
        )
        log.info(f"[{market}] summary | {actual['pnl_pct']:+.2f}% | {actual['pnl_krw']:+,}")



def _in_session_now(market: str) -> bool:
    """현재 KST 시각이 해당 시장 세션 중인지 확인"""
    now = datetime.now(ZoneInfo("Asia/Seoul")).time()
    if market == "KR":
        return dt_time(8, 50) <= now < dt_time(16, 0)
    else:  # US: 22:20 ~ (자정 넘어) 05:00
        return now >= dt_time(22, 20) or now < dt_time(5, 0)


def main(is_paper: bool = True):
    _write_bot_pid_file()
    atexit.register(_clear_bot_pid_file)
    bot = TradingBot(is_paper=is_paper)
    log.info("=== Trading Bot Start ===")
    # 대시보드용: 재시작 시점 기록 → 이전 세션의 미체결 사유 초기화
    for _mkt in ("KR", "US"):
        analysis_log.info(
            f"[session_start {_mkt}]",
            extra={"extra": {"event": "session_start", "market": _mkt}},
        )

    # 텔레그램 명령어 수신 시작 (백그라운드 스레드)
    tg_commander.start(bot)
    mode_txt = "모의투자" if is_paper else "실계좌"
    send(
        f"🤖 <b>봇 시작됨 [{mode_txt}]</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💰 초기자금: {bot.risk.init_cash:,}원 (KR/US 공유)\n"
        f"📦 1회 최대주문: {int(bot.risk.max_order_krw):,}원\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"명령어: <b>?</b> 입력 시 도움말  |  <b>/setorder 300000</b> 으로 주문금액 변경"
    )

    # ── 새벽 스크리너 풀 사전수집 ─────────────────────────────────────────────
    def _screener_collect(market: str):
        try:
            from phase1_trainer.price_collector import collect_screener_pool
            n = collect_screener_pool(market, lookback_days=90, top_n=50)
            log.info(f"[새벽 수집] {market} 스크리너 풀 {n}종목 완료")
        except Exception as e:
            log.warning(f"[새벽 수집] {market} 실패: {e}")

    # ── KR 수급 데이터 자동 수집 ────────────────────────────────────────────────
    # 외국인/기관 순매수 → KR digest의 foreign_flow/inst_flow 필드 채움
    # 08:20: supplement 수집 → 08:30: 스크리너 수집 → 08:50: 세션 오픈
    def _supplement_collect(market: str):
        try:
            from phase1_trainer.supplement_collector import (
                collect_kr_supplement, collect_us_supplement
            )
            today = date.today().strftime("%Y-%m-%d")
            if market == "KR":
                collect_kr_supplement(today)
            else:
                collect_us_supplement(today)
            log.info(f"[수급 수집] {market} supplement 완료")
        except Exception as e:
            log.warning(f"[수급 수집] {market} 실패: {e}")

    # ── 장 마감 후 데이터 최신화 (종가 확정 + forward return) ──────────────────
    def _data_update(market: str):
        try:
            from update_data import run_kr_update, run_us_update
            if market == "KR":
                run_kr_update()
            else:
                run_us_update()
        except Exception as e:
            log.warning(f"[데이터 최신화] {market} 실패: {e}")

    schedule.every().day.at("08:20").do(_supplement_collect, "KR")   # KR 수급 (외국인/기관)
    schedule.every().day.at("21:20").do(_supplement_collect, "US")   # US VIX/DXY
    schedule.every().day.at("08:30").do(_screener_collect, "KR")
    schedule.every().day.at("21:30").do(_screener_collect, "US")

    schedule.every().day.at("08:50").do(bot.session_open, "KR")
    schedule.every().day.at("16:00").do(bot.session_close, "KR")
    schedule.every().day.at("16:10").do(_data_update, "KR")          # KR 장 후 종가 확정
    schedule.every().day.at("22:20").do(bot.session_open, "US")
    schedule.every().day.at("05:00").do(bot.session_close, "US")
    schedule.every().day.at("06:30").do(_data_update, "US")          # US 장 후 종가 확정

    schedule.every(5).minutes.do(bot.run_housekeeping, "KR")
    schedule.every(5).minutes.do(bot.run_housekeeping, "US")
    schedule.every(1).minutes.do(bot.run_entry_scan, "KR")
    schedule.every(1).minutes.do(bot.run_entry_scan, "US")
    schedule.every(_RESCREEN_SCHEDULE_TICK_MIN).minutes.do(bot.run_rescreen, "KR")
    schedule.every(_RESCREEN_SCHEDULE_TICK_MIN).minutes.do(bot.run_rescreen, "US")

    schedule.every(30).minutes.do(bot.run_tuning, "KR")
    schedule.every(30).minutes.do(bot.run_tuning, "US")

    # 1시간마다 상태 보고 (세션 중일 때만 실제 전송)
    schedule.every(60).minutes.do(bot._heartbeat)

    log.info("schedules registered")

    # ── 봇 시작 시 이미 진행 중인 세션이 있으면 즉시 session_open ──────────────
    now_kst = datetime.now(KST)
    now_t   = now_kst.time()
    kr_open  = dt_time(8, 50);  kr_close = dt_time(16, 0)
    us_open  = dt_time(22, 20); us_close = dt_time(5, 0)

    kr_mid_session = kr_open <= now_t < kr_close
    us_mid_session = now_t >= us_open or now_t < us_close  # 자정 걸침

    if kr_mid_session:
        log.info("[startup] KR 세션 진행 중 — session_open 즉시 실행")
        bot.session_open("KR")
    if us_mid_session:
        log.info("[startup] US 세션 진행 중 — session_open 즉시 실행")
        bot.session_open("US")

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="live mode")
    parser.add_argument("--paper", action="store_true", help="paper mode (default)")
    parser.add_argument("--collect", choices=["KR", "US", "ALL"],
                        help="데이터 수집 실행 후 종료: 주가→뉴스→supplement 순서로 수집")
    parser.add_argument("--train", choices=["KR", "US", "ALL"],
                        help="Phase1 역사 학습 실행 후 종료 (예: --train KR)")
    parser.add_argument("--train-start", default=None,
                        help="수집/학습 시작일 (기본: KR=2024-10-01 / US=2025-01-01)")
    args = parser.parse_args()

    if args.collect:
        # 1단계: 주가 수집
        from phase1_trainer.price_collector import collect_kr_incremental, collect_us_incremental
        col_start = args.train_start or "2024-10-01"
        col_end   = date.today().strftime("%Y-%m-%d")
        s_ts = __import__("pandas").Timestamp(col_start)
        e_ts = __import__("pandas").Timestamp(col_end)
        markets_col = ["KR", "US"] if args.collect == "ALL" else [args.collect]
        if "KR" in markets_col:
            log.info(f"[수집] KR 주가 {col_start} ~ {col_end}")
            collect_kr_incremental(s_ts, e_ts)
        if "US" in markets_col:
            log.info(f"[수집] US 주가 {col_start} ~ {col_end}")
            collect_us_incremental(s_ts, e_ts)

        # 2단계: 뉴스 수집
        from phase1_trainer.kr_news_collector import collect_range as kr_news_range
        from phase1_trainer.us_news_collector import collect_range as us_news_range
        from phase1_trainer.supplement_collector import collect_range as supp_range
        if "KR" in markets_col:
            log.info(f"[수집] KR 뉴스 {col_start} ~ {col_end}")
            kr_news_range(col_start, col_end)
        if "US" in markets_col:
            log.info(f"[수집] US 뉴스 {col_start} ~ {col_end}")
            us_news_range(col_start, col_end)

        # 3단계: 보조 데이터
        log.info(f"[수집] supplement {col_start} ~ {col_end}")
        market_arg = args.collect if args.collect != "ALL" else "ALL"
        supp_range(col_start, col_end, market=market_arg)

        log.info("데이터 수집 완료. 이제 --train 으로 Phase1 학습을 실행하세요.")
        sys.exit(0)

    if args.train:
        from phase1_trainer.historical_sim import run_simulation
        markets = ["KR", "US"] if args.train == "ALL" else [args.train]
        defaults = {"KR": "2024-10-01", "US": "2025-01-01"}
        end = date.today().strftime("%Y-%m-%d")
        for mkt in markets:
            start = args.train_start or defaults[mkt]
            log.info(f"Phase1 학습 시작: {mkt} {start} ~ {end}")
            run_simulation(market=mkt, start=start, end=end)
        sys.exit(0)

    is_paper = not args.live
    if not is_paper:
        confirm = input("Live mode. continue? (yes/no): ")
        if confirm.lower() != "yes":
            print("cancelled")
            sys.exit(0)

    main(is_paper=is_paper)
