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
    import psutil
except Exception:  # pragma: no cover - optional dependency
    psutil = None
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - python<3.9 fallback
    from datetime import timezone

    class ZoneInfo:  # type: ignore
        def __new__(cls, _name: str):
            return timezone(timedelta(hours=9))

from dotenv import load_dotenv

# --live 플래그로 env 파일 분기 (argparse 이전에 sys.argv 직접 확인)
# .env.live / .env.paper 없으면 .env fallback
_dotenv_path = ".env.live" if "--live" in sys.argv else ".env.paper"
if not Path(_dotenv_path).exists():
    _dotenv_path = ".env"
load_dotenv(dotenv_path=_dotenv_path, override=True)

# 로거 파일명 분리: import 전에 모드 설정 → 모든 서브모듈 로거 자동 적용
import os as _os
_os.environ["TRADING_BOT_MODE"] = "live" if "--live" in sys.argv else "paper"

sys.path.insert(0, str(Path(__file__).parent))

from logger import (
    get_analysis_logger,
    get_flow_logger,
    get_judgment_logger,
    get_normal_logger,
    get_risk_logger,
    get_trading_logger,
)
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
    save_kr_screen_cache,
    is_trading_halted,
    _US_SCREEN_CACHE_PATH,
    KISTokenExpiredError,
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
    block_alert,
    buy_order_alert,
    fill_confirm_alert,
    trailing_alert,
    watchlist_change_alert,
    system_alert,
    signal_state_alert,
)
from telegram_commander import commander as tg_commander
from minority_report.analysts import get_three_judgments, select_tickers
from minority_report.consensus import build_consensus
from minority_report.tuner import tune
from minority_report.postmortem import run as run_postmortem
from phase1_trainer.digest_builder import build_kr_digest, build_us_digest, digest_to_prompt
from phase1_trainer.sector_play import run_sector_plays, run_kr_sector_plays, TIER2_SIZE_RATIO
from strategy.momentum import signal as mom_sig, params as mom_params, diagnostics as mom_diag
from strategy.mean_reversion import signal as mr_sig, params as mr_params
from strategy.gap_pullback import signal as gap_sig, params as gap_params
from strategy.volatility_breakout import signal as vb_sig, params as vb_params
from strategy.opening_range_pullback import signal as orp_sig
from strategy.continuation import signal as cont_sig, params as cont_params, diagnostics as cont_diag
from strategy.cross_asset import apply_cross_asset_adjust, get_vix_regime
from strategy.adaptive_params import adaptive_params as _adaptive_params
from strategy.entry_priority import compute as entry_priority_score
import strategy.param_tuner as _param_tuner

from claude_memory import brain as BrainDB
from runtime_paths import get_runtime_path
import shared_judgment_cache
import ticker_selection_db as tsdb
import intraday_strategy_db as isdb
from bot.market_utils import (
    MarketUtilsMixin,
    _market_session_date,
    _is_trading_day,
    _EXCHANGE_MAP,
    _ec_cache,
)
from bot.state import StateMixin

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
normal_log = get_normal_logger()
risk_log = get_risk_logger()
flow_log = get_flow_logger()
KST = ZoneInfo("Asia/Seoul")

JUDGMENT_DIR = get_runtime_path("logs", "daily_judgment", make_parents=False)
JUDGMENT_DIR.mkdir(parents=True, exist_ok=True)

BOT_PID_FILE = get_runtime_path("state", "trading_bot.pid")  # main()에서 mode별로 교체됨
DECISIONS_FILE = get_runtime_path("state", "decisions.jsonl")  # main()에서 mode별로 교체됨


def _judgment_runtime_path(mode: str, trade_date: str, market: str) -> Path:
    day = str(trade_date or "").replace("-", "")
    return JUDGMENT_DIR / f"{mode}_{day}_{market}.json"


def _split_log(channel_logger, level: str, message: str, *args, **kwargs) -> None:
    getattr(log, level)(message, *args, **kwargs)
    getattr(channel_logger, level)(message, *args, **kwargs)


def _log_normal(level: str, message: str, *args, **kwargs) -> None:
    _split_log(normal_log, level, message, *args, **kwargs)


def _log_risk(level: str, message: str, *args, **kwargs) -> None:
    _split_log(risk_log, level, message, *args, **kwargs)


def _log_flow(level: str, message: str, *args, **kwargs) -> None:
    _split_log(flow_log, level, message, *args, **kwargs)


def _write_bot_pid_file(is_paper: bool = True):
    try:
        _mode = "paper" if is_paper else "live"
        _pid_file = get_runtime_path("state", f"{_mode}_trading_bot.pid")
        _pid_file.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "mode": _mode,
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


def _clear_bot_pid_file(is_paper: bool = True):
    try:
        _mode = "paper" if is_paper else "live"
        _pid_file = get_runtime_path("state", f"{_mode}_trading_bot.pid")
        if _pid_file.exists():
            _pid_file.unlink()
    except Exception:
        pass

# 동적 선택 실패 시 폴백 (기본 3종목)
_DEFAULT_KR_TICKERS = ["005930", "068270", "035420"]
_DEFAULT_US_TICKERS = ["NVDA", "TSLA", "GOOGL", "AAPL", "NFLX"]

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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


class TradingBot(MarketUtilsMixin, StateMixin):
    def __init__(self, is_paper: bool = True):
        self.is_paper = is_paper
        self.token = get_access_token()

        # ── 투자 금액 설정 — KIS 잔고 직접 조회 (모의/실거래 공통) ─────────
        mode_label = "모의투자" if is_paper else "실거래"
        _bal_retry_max = 3 if is_paper else 3
        _bal_retry_delay = 5  # 초
        bal_kr = None
        for _attempt in range(1, _bal_retry_max + 1):
            try:
                bal_kr = get_balance(self.token, market="KR")
                break
            except Exception as e:
                _log_risk("warning", f"KIS KR 잔고 조회 실패 ({_attempt}/{_bal_retry_max}): {e}")
                if _attempt < _bal_retry_max:
                    import time as _t; _t.sleep(_bal_retry_delay)
                else:
                    if is_paper:
                        _log_risk("warning", "KIS 서버 응답 없음 — PAPER_CASH 폴백으로 기동")
                        bal_kr = {"cash": int(os.getenv("PAPER_CASH", "10000000")), "total_eval": 0}
                    else:
                        raise SystemExit(f"KIS 실거래 잔고 조회 {_bal_retry_max}회 실패 — 종료")
        init_cash = bal_kr["cash"] + bal_kr["total_eval"]
        if init_cash <= 0:
            if is_paper:
                init_cash = int(os.getenv("PAPER_CASH", "10000000"))
                _log_risk(
                    f"모의투자 잔고 0 → PAPER_CASH 폴백({init_cash:,}원) 사용. "
                    f"KIS 앱에서 모의투자 계좌 초기화 필요 (모의투자 메뉴 → 초기화)"
                )
            else:
                raise SystemExit("잔고 0 — 실거래 계좌 확인 필요")
        env_cap = int(os.getenv("MAX_ORDER_KRW", "500000" if is_paper else "2000000"))
        order_pct = float(os.getenv("MAX_ORDER_PCT", "0.05"))
        max_order = min(env_cap, int(init_cash * order_pct))
        _log_normal(
            "info",
            f"{mode_label} | KIS KR 잔고 {init_cash:,}원 "
            f"(현금 {bal_kr['cash']:,} + 평가 {bal_kr['total_eval']:,}) "
            f"| 최대주문 {max_order:,}원",
        )

        # US 잔고 조회 — 환전 현금 + 보유종목 표시, 공유 풀에 합산
        us_cash_init_krw = 0
        try:
            bal_us = get_balance(self.token, market="US")
            usd_krw = get_usd_krw()
            us_cash_usd    = float(bal_us.get("cash", 0) or 0)
            us_cash_krw_   = int(us_cash_usd * usd_krw)
            us_eval_usd    = float(bal_us.get("total_eval", 0) or 0)
            us_eval_krw    = int(us_eval_usd * usd_krw)
            _log_normal(
                "info",
                f"{mode_label} | KIS US 잔고 ${us_cash_usd:.2f} 현금(≈{us_cash_krw_:,}원)"
                f" + ${us_eval_usd:.2f} 주식평가(≈{us_eval_krw:,}원)"
                f" | 보유종목 {len(bal_us['stocks'])}개",
            )
            if not self.is_paper:
                us_cash_init_krw = us_cash_krw_
        except Exception as e:
            _log_risk("warning", f"KIS US 잔고 조회 실패 (무시): {e}")

        init_cash_total = init_cash + us_cash_init_krw
        self.risk = RiskManager(init_cash=init_cash_total, max_order_krw=max_order, market="KR")

        # KR/US 공유 풀 — 단일 현금 계좌, 시장 구분 없이 사용
        if us_cash_init_krw > 0:
            _log_normal("info", f"공유 풀 | 총자금 {init_cash_total:,.0f}원 (KR {init_cash:,} + US ≈{us_cash_init_krw:,}원)")
        else:
            _log_normal("info", f"공유 풀 | 총자금 {init_cash_total:,.0f}원 (KR/US 공용)")

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
        self._session_startup_guard_sec: dict[str, float] = {"KR": _STARTUP_GUARD_SEC, "US": _STARTUP_GUARD_SEC}
        self._daily_baseline_by_market: dict[str, dict] = {"KR": {}, "US": {}}
        self._last_entry_scan_at: dict[str, float] = {"KR": 0.0, "US": 0.0}
        self._last_rescreen_at: dict[str, float] = {"KR": 0.0, "US": 0.0}
        self._vix_refresh_at: float = 0.0          # VIX 장중 갱신 타임스탬프
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
        # 장 시작 전 포지션 리뷰 결과 (session_open에서 채움 → startup guard 후 실행)
        self._pre_session_sell_queue: dict[str, list] = {"KR": [], "US": []}

        # KR 장중 스크리닝 결과 (session_close 시 캐시 저장 → 다음날 장전 사용)
        self._last_kr_candidates: list = []

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

        # 이번 세션에서 매도 완료된 티커 — broker sync 재주입 방지
        self._session_closed_tickers: dict[str, set] = {"KR": set(), "US": set()}

        # WS tick 기반 장중 고가/저가 누적 — 당일봉 주입 시 단일가봉 탈출에 사용
        self._intraday_high: dict[str, float] = {}
        self._intraday_low:  dict[str, float] = {}
        # OR(opening range) 상태 — KR 장초 OR pullback 전략용
        self._or_high: dict[str, float] = {}
        self._or_low: dict[str, float] = {}
        self._or_formed: dict[str, bool] = {}

        # 매도 실패 쿨다운 — ticker → 실패 시각, 90초간 재시도 억제
        self._sell_fail_at:  dict[str, float] = {}
        self._sell_fail_meta: dict[str, dict] = {}
        self._execution_flags: dict[str, set[str]] = {"KR": set(), "US": set()}
        self._broker_state: dict[str, dict] = {
            "KR": {
                "trust_level": "unknown",
                "last_ok_at": "",
                "last_error": "",
                "last_snapshot": {},
                "last_trusted_snapshot": {},
                "consecutive_failures": 0,
            },
            "US": {
                "trust_level": "unknown",
                "last_ok_at": "",
                "last_error": "",
                "last_snapshot": {},
                "last_trusted_snapshot": {},
                "consecutive_failures": 0,
            },
        }

        # entry_priority cutoff (Phase 2) — env로 ON/OFF, 텔레그램으로 실시간 토글
        self.entry_priority_cutoff_enabled: bool = (
            os.getenv("ENTRY_PRIORITY_CUTOFF_ENABLED", "false").lower() == "true"
        )
        self.entry_priority_cutoff: float = float(os.getenv("ENTRY_PRIORITY_CUTOFF", "0.20"))

        # ticker_selection_log DB — 종목 선택 품질 누적 (ML Phase 3 학습용)
        tsdb.init()
        isdb.init()
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

        # ── continuation entry 1회 제한 플래그 (ticker → bool, 세션마다 리셋) ───
        self._continuation_used: dict = {}   # key: ticker, val: True/False

        # ── 주문 예외(HTTP 500 등) 연속 카운터 — 3회 연속 시 당일 차단 ──────────
        self._order_error_count: dict = {}   # key: ticker, val: int

        # ── 퍼널 계측 카운터 (selected→signaled→ordered→filled) ──────────────
        self._funnel: dict = {
            "KR": {"selected": 0, "signaled": 0, "ordered": 0, "filled": 0,
                   "rejection_reasons": {}, "volume_states": {}},
            "US": {"selected": 0, "signaled": 0, "ordered": 0, "filled": 0,
                   "rejection_reasons": {}, "volume_states": {}},
        }

        # ML DB 초기화
        if _ML_DB_ENABLED:
            try:
                _ml_init_db()
            except Exception as _e:
                log.warning(f"[ML DB] 초기화 실패 (봇 동작에 영향 없음): {_e}")

        self._restore_pending_orders()
        self._restore_claude_control()
        self._load_daily_baselines()
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
        live_path = _judgment_runtime_path(self._mode, date.today().strftime('%Y%m%d'), market)
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

    def _build_intraday_context(self, market: str) -> str:
        """장중 재스크리닝용 실시간 시장 컨텍스트 문자열 생성."""
        try:
            from kis_api import get_index_change
            lines = []
            now_str = datetime.now(KST).strftime("%H:%M")
            if market == "KR":
                kospi_chg = get_index_change("KR")
                lines.append(f"현재시각 {now_str} KST | 코스피 현재 {kospi_chg:+.2f}%")
                # 장중 보유 포지션 현황
                positions = list(self.positions.get(market, {}).values())
                if positions:
                    pos_strs = [
                        f"{p.get('ticker')} {p.get('unrealized_pnl_pct', 0):+.1f}%"
                        for p in positions[:5]
                    ]
                    lines.append(f"현재 보유: {' | '.join(pos_strs)}")
                # 아침 모드 대비 지수 변화 참고
                morning_kospi = 0.0
                try:
                    morning_kospi = (self.today_judgment or {}).get(
                        "digest_raw", {}).get("context", {}).get("kospi", {}).get("change_pct", 0.0) or 0.0
                except Exception:
                    pass
                if abs(kospi_chg - morning_kospi) >= 0.5:
                    direction = "상승" if kospi_chg > morning_kospi else "하락"
                    lines.append(
                        f"장전 대비 {abs(kospi_chg - morning_kospi):.1f}%p {direction} "
                        f"(장전 {morning_kospi:+.2f}% → 현재 {kospi_chg:+.2f}%)"
                    )
            else:
                sp_chg = get_index_change("US")
                lines.append(f"현재시각 {now_str} ET | S&P500 현재 {sp_chg:+.2f}%")
                positions = list(self.positions.get(market, {}).values())
                if positions:
                    pos_strs = [
                        f"{p.get('ticker')} {p.get('unrealized_pnl_pct', 0):+.1f}%"
                        for p in positions[:5]
                    ]
                    lines.append(f"현재 보유: {' | '.join(pos_strs)}")
            return "\n".join(lines)
        except Exception as e:
            log.debug(f"[intraday_context 생성 실패] {e}")
            return ""

    def _advisor_pos(self, pos: dict, market: str) -> dict:
        """hold_advisor 호출용 pos 복사본 — mode/entry_time 등 컨텍스트 필드 주입."""
        mode = ""
        try:
            mode = (self.today_judgment or {}).get("consensus", {}).get("mode", "")
        except Exception:
            pass
        return {**pos, "mode": mode}

    def _flush_funnel(self, market: str):
        """세션 종료 시 퍼널 카운터를 일별 JSON 파일에 저장."""
        import json as _json
        from runtime_paths import get_runtime_path as _grp
        _funnel = self._funnel.get(market, {})
        if not any(_funnel.get(k, 0) for k in ("selected", "signaled", "ordered")):
            return  # 아무 활동 없으면 저장 생략
        today = datetime.now(KST).strftime("%Y-%m-%d")
        log_dir = _grp("logs", "funnel", make_parents=True)
        log_file = log_dir / f"funnel_{today}_{market}.json"
        # 기존 파일이 있으면 누적
        existing = {}
        if log_file.exists():
            try:
                existing = _json.loads(log_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        for k in ("selected", "signaled", "ordered", "filled"):
            existing[k] = existing.get(k, 0) + _funnel.get(k, 0)
        # rejection_reasons 누적
        for reason, cnt in _funnel.get("rejection_reasons", {}).items():
            existing.setdefault("rejection_reasons", {})[reason] = \
                existing.get("rejection_reasons", {}).get(reason, 0) + cnt
        # volume_states 누적
        for vs, cnt in _funnel.get("volume_states", {}).items():
            existing.setdefault("volume_states", {})[vs] = \
                existing.get("volume_states", {}).get(vs, 0) + cnt
        existing["market"] = market
        existing["date"] = today
        log_file.write_text(_json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(
            f"[퍼널] {market} {today} "
            f"selected={existing.get('selected',0)} "
            f"signaled={existing.get('signaled',0)} "
            f"ordered={existing.get('ordered',0)}"
        )
        # 카운터 초기화 (재시작 대비)
        self._funnel[market] = {
            "selected": 0, "signaled": 0, "ordered": 0, "filled": 0,
            "rejection_reasons": {}, "volume_states": {},
        }

    def manual_rescreen(self, market: Optional[str] = None) -> list[str]:
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
                candidates = screen_market_kr(self.token, mode=self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
                if candidates:
                    self._last_kr_candidates = candidates
            else:
                candidates = screen_market_us(mode=self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
            if not candidates:
                raise RuntimeError("재스크리닝 후보가 없습니다.")
            self._log_screen_candidates(target_market, candidates, "manual_rescreen")
            candidates = self._prefill_history_sync(candidates, target_market)
            candidates = self._filter_candidates_by_history(candidates, target_market)
            _manual_cooldown = self._get_cooldown_excluded(target_market)
            candidates = [c for c in candidates if c.get("ticker") not in _manual_cooldown]
            if not candidates:
                raise RuntimeError("히스토리 필터 통과 후보가 없습니다.")
            intraday_ctx = self._build_intraday_context(target_market)
            selected, reasons = select_tickers(target_market, digest_prompt, mode, candidates,
                                               intraday_context=intraday_ctx)
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
        system_alert(f"봇 시작 [{mode_label}] — API 점검 결과", lines[1:], icon="🤖")

    # ── 시장별 예산 조회 ──────────────────────────────────────────────────────

    def _market_budget_available(self, market: str) -> float:
        """사용 가능한 현금 잔액 (KR/US 공유 풀)"""
        return max(0.0, self.risk.cash)

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

    def _consume_pending_position_review(self, market: str):
        self._refresh_claude_control()
        pending = self.claude_control.get("pending_position_review")
        if not pending or pending.get("market") != market:
            return
        ticker = str(pending.get("ticker", "") or "").strip()
        source = str(pending.get("source", "dashboard_review") or "dashboard_review")
        try:
            if not ticker:
                raise ValueError("ticker missing")
            self._intraday_position_review(market, force=True, ticker_filter=ticker)
            self.claude_control["last_result_status"] = "success"
            self.claude_control["last_error"] = ""
            log.info(f"[포지션 재판단 트리거 처리] {market} {ticker} | {source}")
        except Exception as e:
            self.claude_control["last_result_status"] = "error"
            self.claude_control["last_error"] = str(e)
            log.error(f"[포지션 재판단 트리거 실패] {market} {ticker}: {e}")
        finally:
            self.claude_control["pending_position_review"] = None
            self.claude_control["last_result_at"] = datetime.now(KST).isoformat(timespec="seconds")
            self.claude_control["last_result_market"] = market
            self._save_claude_control()

    def _consume_pending_sell(self, market: str):
        """대시보드 즉시 매도 버튼 → claude_control.json pending_sell 소비"""
        self._refresh_claude_control()
        pending = self.claude_control.get("pending_sell")
        if not pending or pending.get("market") != market:
            return
        ticker     = str(pending.get("ticker", "") or "").strip()
        sell_price = float(pending.get("sell_price", 0) or 0)
        try:
            if not ticker:
                raise ValueError("ticker missing")
            pos = next((p for p in self.risk.positions if p.get("ticker") == ticker), None)
            if pos is None:
                raise ValueError(f"{ticker} 포지션 없음")
            raw_px = sell_price if sell_price > 0 else self.price_cache_raw.get(ticker, pos.get("current_price", 0))
            exit_px = float(pos.get("current_price", 0) or 0)
            if market == "US" and raw_px > 0 and self.usd_krw_rate > 0:
                exit_px = raw_px * self.usd_krw_rate
            elif market != "US" and raw_px > 0:
                exit_px = raw_px
            cand = {**pos, "exit_price": exit_px, "reason": "manual_sell"}
            self.price_cache_raw[ticker] = raw_px
            self._execute_sell(cand, market, reason="manual_sell")
            log.info(f"[즉시 매도] {market} {ticker} @ {raw_px:,.2f} (대시보드 요청)")
            self.claude_control["last_result_status"] = "success"
            self.claude_control["last_error"] = ""
        except Exception as e:
            self.claude_control["last_result_status"] = "error"
            self.claude_control["last_error"] = str(e)
            log.error(f"[즉시 매도 실패] {market} {ticker}: {e}")
        finally:
            self.claude_control["pending_sell"] = None
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
                "opening_range_pullback": "OR눌림",
                "continuation": "연속진입(소사이즈)",
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
            signal_state_alert(
                event=event,
                market=market,
                ticker=ticker,
                strategy=strategy,
                price=price,
                reason=reason,
                mode=mode,
                summary=summary,
                order_cost_krw=order_cost_krw,
            )
        except Exception as e:
            log.debug(f"[텔레그램 신호상태 전송 실패] {ticker} {event}: {e}")

    def _restore_positions(self):
        """저장된 이월 포지션 복구"""
        _positions_file = get_runtime_path("state", f"{self._mode}_open_positions.json")
        if not _positions_file.exists():
            return
        try:
            with open(_positions_file, encoding="utf-8") as f:
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
            self._set_broker_state(
                "KR",
                trust_level="trusted",
                snapshot=self._broker_snapshot_from_balance("KR", bal_kr),
            )
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
            self._flag_execution_issue("KR", "broker_balance_unreliable")
            kr_ok = False  # 검증 무력화 → 아래 루프에서 KR 포지션 그대로 유지

        if us_ok and saved_us_count > 0 and len(broker_us) == 0:
            log.warning(
                f"[브로커 동기화] US 잔고 조회 성공했으나 보유주식 0개 반환 "
                f"(저장 포지션 {saved_us_count}개) — API 미반영으로 판단, 검증 스킵"
            )
            self._flag_execution_issue("US", "broker_balance_unreliable")
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
                    self._flag_execution_issue(market, "broker_qty_corrected")
                    pos["qty"] = bq
                verified.append(pos)
            else:
                self._flag_execution_issue(market, "broker_position_removed")
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

    def _recover_decision_id(self, ticker: str, market: str) -> Optional[int]:
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

    def _normalize_broker_balance(self, balance: Optional[dict], market: str) -> dict:
        """get_balance() 응답을 ticker -> position dict 형태로 정규화."""
        if not isinstance(balance, dict):
            return {}
        stocks = balance.get("stocks", []) or []
        normalized = {}
        for stock in stocks:
            if not isinstance(stock, dict):
                continue
            ticker = str(stock.get("ticker", "") or "").strip()
            if not ticker:
                continue
            key = ticker.upper() if market == "US" else ticker
            normalized[key] = stock
        return normalized

    def _flag_execution_issue(self, market: str, issue: str):
        if market in self._execution_flags and issue:
            self._execution_flags[market].add(issue)

    def _set_broker_state(self, market: str, *, trust_level: Optional[str] = None,
                          snapshot: Optional[dict] = None, error: str = "") -> None:
        state = self._broker_state.setdefault(market, {
            "trust_level": "unknown",
            "last_ok_at": "",
            "last_error": "",
            "last_snapshot": {},
            "last_trusted_snapshot": {},
            "consecutive_failures": 0,
        })
        if trust_level:
            if trust_level == "trusted":
                state["consecutive_failures"] = 0
            elif trust_level in ("degraded", "untrusted"):
                state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
            state["trust_level"] = trust_level
        if error:
            state["last_error"] = error
        if snapshot is not None:
            snap = dict(snapshot)
            state["last_snapshot"] = snap
            if trust_level == "trusted":
                state["last_trusted_snapshot"] = dict(snap)
                state["last_ok_at"] = datetime.now(KST).isoformat(timespec="seconds")
                state["last_error"] = ""

    def _broker_snapshot_from_balance(self, market: str, balance: Optional[dict]) -> dict:
        balance = balance or {}
        if market == "US":
            cash_usd = float(balance.get("cash", 0) or 0)
            eval_usd = float(balance.get("total_eval", 0) or 0)
            total_krw = (cash_usd + eval_usd) * float(self.usd_krw_rate or 0)
            return {
                "market": market,
                "cash_usd": cash_usd,
                "eval_usd": eval_usd,
                "cash_krw": cash_usd * float(self.usd_krw_rate or 0),
                "eval_krw": eval_usd * float(self.usd_krw_rate or 0),
                "total_krw": total_krw,
                "positions": len(balance.get("stocks", []) or []),
                "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
            }
        cash_krw = float(balance.get("cash", 0) or 0)
        eval_krw = float(balance.get("total_eval", 0) or 0)
        return {
            "market": market,
            "cash_krw": cash_krw,
            "eval_krw": eval_krw,
            "total_krw": cash_krw + eval_krw,
            "positions": len(balance.get("stocks", []) or []),
            "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
        }

    def _broker_trust_level(self, market: str) -> str:
        return str(self._broker_state.get(market, {}).get("trust_level", "unknown") or "unknown")

    def _entry_allowed_by_broker_state(self, market: str) -> tuple[bool, str]:
        trust = self._broker_trust_level(market)
        if trust == "trusted":
            return True, "OK"
        # 포지션이 없을 때는 degraded 상태에서도 신규 진입 허용
        # (보호할 기존 포지션이 없으므로 브로커 불신이 진입을 막을 이유 없음)
        has_positions = any(
            self._ticker_market(p.get("ticker", "")) == market
            for p in self.risk.positions
        )
        if trust == "degraded":
            if not has_positions:
                return True, "OK"
            return False, "broker_state_degraded"
        if trust == "untrusted":
            if not has_positions:
                return True, "OK"
            return False, "broker_state_untrusted"
        return False, "broker_state_unknown"

    def _refresh_operational_halt(self, market: str) -> None:
        trust = self._broker_trust_level(market)
        if self.risk.halt_reason == "daily_loss":
            return
        if trust == "untrusted":
            # 포지션이 있을 때만 halt — 없으면 브로커 불신이 운영을 막을 이유 없음
            has_positions = any(
                self._ticker_market(p.get("ticker", "")) == market
                for p in self.risk.positions
            )
            if has_positions:
                self.risk.halted = True
                self.risk.halt_reason = "broker_sync"
        elif trust in ("trusted", "degraded") and self.risk.halt_reason == "broker_sync":
            self.risk.halted = False
            self.risk.halt_reason = ""

    def _has_broker_sync_risk(self, market: str) -> bool:
        return self._broker_trust_level(market) in {"degraded", "untrusted"}

    def _should_run_pre_session_review(self, market: str) -> bool:
        # session_open is also called on restart while the market is already open.
        # In that case, do not let a restart trigger immediate pre-session sells.
        if self._market_elapsed_min(market) > 0.5:
            return False
        if self._has_broker_sync_risk(market):
            return False
        return True

    def _build_execution_health(self, market: str, session_trades: Optional[list] = None) -> dict:
        session_trades = session_trades or []
        reasons = set(self._execution_flags.get(market, set()))
        market_events = [e for e in self.decision_event_log if e.get("market") == market]
        for event in market_events:
            action = str(event.get("action", "") or "")
            reason = str(event.get("reason", "") or "")
            strategy = str(event.get("strategy", "") or "")
            if action == "sell_failed":
                reasons.add("sell_failed")
                if reason:
                    reasons.add(f"sell_failed:{reason}")
            if action == "buy_filled" and strategy == "broker_sync":
                reasons.add("broker_sync_fill")
            if action == "sell_filled" and strategy == "broker_sync":
                reasons.add("broker_sync_exit")
        if any(str(t.get("strategy", "") or "") == "broker_sync" for t in session_trades):
            reasons.add("broker_sync_trade")
        if any(o.get("market") == market for o in self.pending_orders):
            reasons.add("pending_orders_remain")
        return {
            "contaminated": bool(reasons),
            "reasons": sorted(reasons),
        }

    def _note_sell_failure(self, market: str, ticker: str, reason: str, detail: str = ""):
        sig = f"{reason}|{detail}".strip("|")
        now = time.time()
        self._sell_fail_at[ticker] = now
        meta = dict(self._sell_fail_meta.get(ticker, {}))
        prev_sig = str(meta.get("sig", "") or "")
        prev_ts = float(meta.get("ts", 0.0) or 0.0)
        count = int(meta.get("count", 0) or 0)
        if sig == prev_sig and (now - prev_ts) <= 900:
            count += 1
        else:
            count = 1
        self._sell_fail_meta[ticker] = {"sig": sig, "count": count, "ts": now}
        self._flag_execution_issue(market, "sell_failed")
        if "장종료" in detail or "장 종료" in detail:
            next_open_ts = self._next_market_open_dt(market).timestamp()
            self._sell_fail_at[ticker] = max(self._sell_fail_at.get(ticker, 0.0), next_open_ts)
            log.warning(f"[매도 재시도 억제] {ticker} 장종료 응답 감지 → 다음 세션 시작까지 보류")
            return
        if count >= 3:
            self._sell_fail_at[ticker] = now + 600
            log.warning(
                f"[매도 재시도 억제] {ticker} 동일 실패 {count}회 "
                f"({reason}) → 10분 쿨다운"
            )
            self._record_decision_event(
                market, "sell_retry_suppressed", ticker,
                strategy="",
                reason=reason,
                detail=f"same_failure_count={count} {detail}".strip(),
            )

    def _kis_total_equity_krw(self) -> float:
        """통합계좌 기준 KIS 총자산(KRW). KR+US 잔고 합산 후 실패 시 내부 계산값으로 폴백."""
        fallback = self._internal_total_equity_krw()
        kr_total, us_total_krw = 0.0, 0.0
        kr_ok, us_ok = False, False
        try:
            bal_kr = get_balance(self.token, market="KR")
            kr_total = float(bal_kr.get("cash", 0)) + float(bal_kr.get("total_eval", 0))
            kr_ok = True
            self._set_broker_state(
                "KR",
                trust_level="trusted",
                snapshot=self._broker_snapshot_from_balance("KR", bal_kr),
            )
        except Exception as e:
            self._set_broker_state("KR", trust_level="degraded", error=str(e))
            log.warning(f"[누적자산 동기화] KR 잔고 조회 실패: {e}")
        try:
            bal_us = get_balance(self.token, market="US")
            us_cash_usd = float(bal_us.get("cash", 0))
            us_eval_usd = float(bal_us.get("total_eval", 0))
            us_total_krw = (us_cash_usd + us_eval_usd) * self.usd_krw_rate
            us_ok = True
            self._set_broker_state(
                "US",
                trust_level="trusted",
                snapshot=self._broker_snapshot_from_balance("US", bal_us),
            )
        except Exception as e:
            self._set_broker_state("US", trust_level="degraded", error=str(e))
            log.debug(f"[누적자산 동기화] US 잔고 조회 실패: {e}")
        if not kr_ok and not us_ok:
            return float(self._equity_reference_context().get("total_krw", fallback) or fallback)
        total = (kr_total if kr_ok else 0.0) + (us_total_krw if us_ok else 0.0)
        log.info(
            f"[누적자산 동기화] 총자산 {total:,.0f}원 "
            f"(KR {kr_total:,.0f}원 + US ${(us_total_krw/self.usd_krw_rate):.0f}≈{us_total_krw:,.0f}원)"
        )
        if not (kr_ok and us_ok):
            return float(self._equity_reference_context().get("total_krw", total) or total)
        return total

    def _equity_reference_context(self) -> dict:
        internal_total = float(self._internal_total_equity_krw())
        current_total = 0.0
        current_ok = True
        last_trusted_total = 0.0
        has_last_trusted = True
        for market in ("KR", "US"):
            state = self._broker_state.get(market, {})
            current_snap = state.get("last_snapshot") or {}
            trusted_snap = state.get("last_trusted_snapshot") or {}
            if self._broker_trust_level(market) == "trusted" and current_snap.get("total_krw") is not None:
                current_total += float(current_snap.get("total_krw", 0) or 0)
            else:
                current_ok = False
            if trusted_snap.get("total_krw") is not None:
                last_trusted_total += float(trusted_snap.get("total_krw", 0) or 0)
            else:
                has_last_trusted = False
        if current_ok:
            return {
                "total_krw": current_total,
                "source": "broker_current",
                "internal_krw": internal_total,
                "last_trusted_krw": last_trusted_total if has_last_trusted else None,
            }
        if has_last_trusted:
            return {
                "total_krw": last_trusted_total,
                "source": "broker_last_trusted",
                "internal_krw": internal_total,
                "last_trusted_krw": last_trusted_total,
            }
        return {
            "total_krw": internal_total,
            "source": "internal_estimate",
            "internal_krw": internal_total,
            "last_trusted_krw": None,
        }

    def _sync_runtime_with_broker(self):
        """장중에도 내부 포지션/현금을 브로커 잔고 기준으로 정렬."""
        _pre_sync_cash = float(self.risk.cash)
        _pre_sync_us_cost_by_key = {}
        for _pos in self.risk.positions:
            _ticker = str(_pos.get("ticker", "") or "").strip()
            if self._ticker_market(_ticker) != "US":
                continue
            _pre_sync_us_cost_by_key[_ticker.upper()] = (
                float(_pos.get("entry", 0) or 0) * int(_pos.get("qty", 0) or 0)
            )
        try:
            bal_kr = get_balance(self.token, market="KR")
            self._set_broker_state(
                "KR",
                trust_level="trusted",
                snapshot=self._broker_snapshot_from_balance("KR", bal_kr),
            )
        except KISTokenExpiredError as e:
            log.warning(f"[브로커 런타임 동기화] KR 토큰 만료 감지 → 강제 갱신 후 재시도: {e}")
            try:
                self.token = get_access_token(force_refresh=True)
                bal_kr = get_balance(self.token, market="KR")
                self._set_broker_state(
                    "KR",
                    trust_level="trusted",
                    snapshot=self._broker_snapshot_from_balance("KR", bal_kr),
                )
                log.info("[브로커 런타임 동기화] KR 토큰 갱신 성공")
            except Exception as retry_e:
                _kr_fails = self._broker_state.get("KR", {}).get("consecutive_failures", 0) + 1
                _kr_level = "untrusted" if _kr_fails >= 3 else "degraded"
                self._set_broker_state("KR", trust_level=_kr_level, error=str(retry_e))
                log.error(f"[브로커 런타임 동기화] KR 토큰 갱신 후 재시도 실패({_kr_fails}회): {retry_e}")
                return
        except Exception as e:
            _kr_fails = self._broker_state.get("KR", {}).get("consecutive_failures", 0) + 1
            _kr_level = "untrusted" if _kr_fails >= 3 else "degraded"
            self._set_broker_state("KR", trust_level=_kr_level, error=str(e))
            log.warning(f"[브로커 런타임 동기화] KR 잔고 조회 실패({_kr_fails}회): {e}")
            return

        broker_us: dict = {}
        us_ok = False
        try:
            bal_us = get_balance(self.token, market="US")
            broker_us = {s["ticker"].upper(): s for s in bal_us.get("stocks", [])}
            us_ok = True
            self._set_broker_state(
                "US",
                trust_level="trusted",
                snapshot=self._broker_snapshot_from_balance("US", bal_us),
            )
        except KISTokenExpiredError as e:
            log.warning(f"[브로커 런타임 동기화] US 토큰 만료 감지 → 강제 갱신 후 재시도: {e}")
            try:
                self.token = get_access_token(force_refresh=True)
                bal_us = get_balance(self.token, market="US")
                broker_us = {s["ticker"].upper(): s for s in bal_us.get("stocks", [])}
                us_ok = True
                self._set_broker_state(
                    "US",
                    trust_level="trusted",
                    snapshot=self._broker_snapshot_from_balance("US", bal_us),
                )
                log.info("[브로커 런타임 동기화] US 토큰 갱신 성공")
            except Exception as retry_e:
                self._set_broker_state("US", trust_level="untrusted", error=str(retry_e))
                log.warning(f"[브로커 런타임 동기화] US 토큰 갱신 후 재시도 실패: {retry_e}")
        except Exception as e:
            self._set_broker_state("US", trust_level="untrusted", error=str(e))
            log.warning(f"[브로커 런타임 동기화] US 잔고 조회 실패 — US 포지션 검증 스킵: {e}")

        # US API가 성공 응답이지만 stocks=[] 반환 시 — 내부 US 포지션이 있으면 API 일시 오류로 간주
        if us_ok and len(broker_us) == 0:
            _us_internal_count = sum(
                1 for p in self.risk.positions
                if self._ticker_market(p.get("ticker", "")) == "US"
            )
            if _us_internal_count > 0:
                log.warning(
                    f"[브로커 런타임 동기화] US API 빈 잔고 응답 "
                    f"(내부 {_us_internal_count}개 포지션 존재) → US 포지션 검증 스킵 (False HALT 방지)"
                )
                us_ok = False
                self._set_broker_state(
                    "US",
                    trust_level="untrusted",
                    snapshot=self._broker_snapshot_from_balance("US", bal_us),
                    error="empty_balance_with_internal_positions",
                )

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
        saved_templates = {}
        _positions_file = get_runtime_path("state", f"{self._mode}_open_positions.json")
        try:
            if _positions_file.exists():
                with open(_positions_file, encoding="utf-8") as _spf:
                    for _saved_pos in json.load(_spf) or []:
                        _tk = str(_saved_pos.get("ticker", "") or "").strip()
                        if not _tk:
                            continue
                        _mkt = self._ticker_market(_tk)
                        _key = _tk.upper() if _mkt == "US" else _tk
                        saved_templates[(_mkt, _key)] = _saved_pos
        except Exception as _e:
            log.warning(f"[브로커 런타임 동기화] 저장 포지션 템플릿 로드 실패: {_e}")

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
                self._flag_execution_issue(market, "broker_position_removed")
                removed.append(ticker)
                continue
            if broker_pos.get("qty", 0) != pos.get("qty", 0):
                log.warning(
                    f"[브로커 런타임 동기화] {ticker} 수량 보정 "
                    f"{pos.get('qty', 0)} -> {broker_pos.get('qty', 0)}"
                )
                self._flag_execution_issue(market, "broker_qty_corrected")
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
            if ticker in self._session_closed_tickers.get("KR", set()):
                log.info(f"[브로커 런타임 동기화] KR 재주입 차단: {ticker} (이번 세션 매도 완료)")
                continue
            synced_positions[key] = self._make_runtime_position_from_broker(
                ticker, "KR", broker_pos,
                template=pending_by_key.get(key) or saved_templates.get(key)
            )
            self._flag_execution_issue("KR", "broker_position_injected")
            seen_keys.add(key)
            log.warning(f"[브로커 런타임 동기화] KR 브로커 보유 포지션 주입: {ticker} {broker_pos.get('qty', 0)}주")

        for ticker, broker_pos in broker_us.items():
            key = ("US", ticker.upper())
            if key in seen_keys or int(broker_pos.get("qty", 0) or 0) <= 0:
                continue
            if ticker.upper() in self._session_closed_tickers.get("US", set()):
                log.info(f"[브로커 런타임 동기화] US 재주입 차단: {ticker} (이번 세션 매도 완료)")
                continue
            synced_positions[key] = self._make_runtime_position_from_broker(
                ticker.upper(), "US", broker_pos,
                template=pending_by_key.get(key) or saved_templates.get(key)
            )
            self._flag_execution_issue("US", "broker_position_injected")
            seen_keys.add(key)
            log.warning(f"[브로커 런타임 동기화] US 브로커 보유 포지션 주입: {ticker} {broker_pos.get('qty', 0)}주")

        if removed:
            log.warning(f"[브로커 런타임 동기화] 브로커 미보유 포지션 제거: {removed}")

        self.risk.positions = list(synced_positions.values())
        # 공유 풀 기준 내부 equity는 KR 현금만이 아니라 US 현금까지 포함해야 한다.
        # 그렇지 않으면 US 포지션 청산 후 재시작/재동기화 시
        # "포지션은 제거됐는데 현금은 equity에 미반영" 상태가 되어
        # 일일 손실 한도(halt)가 가짜로 발동할 수 있다.
        kr_cash = float(bal_kr.get("cash", 0) or 0)
        us_cash_krw = 0.0
        if us_ok:
            try:
                us_cash_krw = float(bal_us.get("cash", 0) or 0) * float(self.usd_krw_rate or 0)
            except Exception:
                us_cash_krw = 0.0
        # KIS paper US 계좌는 USD 현금을 0으로 반환한다.
        # broker_sync로 주입한 US paper 포지션은 risk.positions에 들어가지만
        # 대응 현금 차감이 없어 equity = KR_cash + US_pos_value 로 과대계상된다.
        # 이를 방지하기 위해 paper 모드에서 US paper 포지션 cost를 KR 현금에서 차감해 회계를 맞춘다.
        # (실거래 US의 경우 us_cash_krw가 실제 달러 잔고를 반영하므로 기존 로직 유지)
        if self.is_paper and us_ok and us_cash_krw == 0:
            synthetic_cash = _pre_sync_cash
            for _pos in self.risk.positions:
                _ticker = str(_pos.get("ticker", "") or "").strip()
                if self._ticker_market(_ticker) != "US":
                    continue
                _key = _ticker.upper()
                _curr_cost = float(_pos.get("entry", 0) or 0) * int(_pos.get("qty", 0) or 0)
                _prev_cost = float(_pre_sync_us_cost_by_key.get(_key, 0.0) or 0.0)
                if _curr_cost > _prev_cost:
                    synthetic_cash -= (_curr_cost - _prev_cost)
            self.risk.cash = synthetic_cash
        else:
            self.risk.cash = kr_cash + us_cash_krw

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
            "tp_pct": tp_pct,        # 비율 보존 — US 환율 드리프트 방지
            "sl_pct": sl_pct,
            "max_hold": int(order.get("max_hold", 1)),
            "held_days": 0,
            "entry_date": date.today().isoformat(),
            "trailing": False,
            "trail_sl": 0.0,
            "trail_sl_usd": 0.0,     # US 트레일링 SL (USD 기준)
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
            for fname in sorted(JUDGMENT_DIR.glob(f"{self._mode}_*_{market}.json"), reverse=True):
                with open(fname, encoding="utf-8") as f:
                    rec = json.load(f)
                technicals = ((rec.get("digest_raw") or {}).get("technicals") or {})
                info = technicals.get(raw_ticker) or technicals.get(str(ticker or "").strip()) or {}
                name = str((info or {}).get("name", "") or "").strip()
                if name and name != raw_ticker:
                    return name
            for fname in sorted(JUDGMENT_DIR.glob(f"*_{market}.json"), reverse=True):
                if fname.name.startswith(f"{self._mode}_"):
                    continue
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
                self._funnel[market]["filled"] += 1
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
                fill_confirm_alert(
                    market=market,
                    ticker=ticker,
                    qty=int(order.get("qty", 0) or 0),
                    order_no=str(order.get("order_no", "") or ""),
                    price=float(fill_px_native or 0),
                    source="브로커 평균단가",
                    name=str(order.get("name", "") or "").strip(),
                    usd_krw=self.usd_krw_rate,
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

            # ── max_hold 도달 → Claude 먼저 물어보고 결정 ──────────────────
            if cand["reason"] == "max_hold":
                self._handle_max_hold_claude(cand, market)
                continue

            self._execute_sell(cand, market, reason=cand["reason"])
            # 손절/트레일링 청산 후 재진입 쿨다운
            if cand["reason"] in ("stop_loss", "trail_stop"):
                self._block_entry(cand["ticker"], _STOP_COOLDOWN_MIN, cand["reason"])

    def _pre_session_position_review(self, market: str):
        """장 시작 전 보유 포지션 Claude 검토.
        SELL 결정 종목은 _pre_session_sell_queue에 담아두고,
        startup guard 해제 후 run_cycle에서 즉시 매도.
        """
        if not self._should_run_pre_session_review(market):
            self._pre_session_sell_queue[market] = []
            if self._has_broker_sync_risk(market):
                log.warning(f"[장전 리뷰] {market} 브로커 동기화 불신 상태 → 리뷰/SELL 예약 보류")
            else:
                log.info(f"[장전 리뷰] {market} 장중 재시작 감지 → 리뷰/SELL 예약 건너뜀")
            return

        positions = [p for p in self.risk.positions
                     if self._ticker_market(p.get("ticker", "")) == market]
        if not positions:
            return

        self._pre_session_sell_queue[market] = []
        digest = self.today_judgment.get("digest_prompt", "")

        sell_list, hold_list = [], []
        for pos in positions:
            ticker = pos.get("ticker", "")
            if pos.get("pending_next_open_sell"):
                self._pre_session_sell_queue[market].append(pos)
                sell_reason = str(pos.get("pending_next_open_reason", "") or "").strip()
                sell_list.append((ticker, sell_reason))
                log.info(f"[장전 리뷰] {ticker} → 전일 장마감 SELL 예약 유지")
                continue
            if float(pos.get("entry", 0) or 0) <= 0:
                log.warning(f"[장전 리뷰] {ticker} entry=0 → hold_advisor 건너뜀, HOLD 유지")
                hold_list.append((ticker, "진입가 미확정 → 홀드"))
                continue
            try:
                from minority_report.hold_advisor import ask as advisor_ask
                advice = advisor_ask(self._advisor_pos(pos, market), market, digest)
            except Exception as e:
                log.warning(f"[장전 리뷰] {ticker} hold_advisor 오류 → HOLD 유지: {e}")
                hold_list.append((ticker, "오류 → 기본 홀드"))
                continue

            action = advice.get("action", "HOLD")
            votes  = advice.get("votes", {})
            reason = ""
            for v in votes.values():
                if v.get("action") == action and v.get("reason"):
                    reason = v["reason"][:80]
                    break

            if action == "SELL":
                queued = {**pos, "hold_advice": advice, "pending_next_open_sell": False}
                self._pre_session_sell_queue[market].append(queued)
                for p2 in self.risk.positions:
                    if p2.get("ticker") == ticker:
                        p2["pending_next_open_sell"] = False
                        p2["pending_next_open_reason"] = ""
                sell_list.append((ticker, reason))
                log.info(f"[장전 리뷰] {ticker} → SELL 예약: {reason}")
            else:
                trail_pct = advice.get("trail_pct", pos.get("trail_pct", 0.03))
                for p2 in self.risk.positions:
                    if p2.get("ticker") == ticker:
                        p2["pending_next_open_sell"] = False
                        p2["pending_next_open_reason"] = ""
                # TRAIL/HOLD → 트레일링 폭 업데이트만
                if pos.get("trailing"):
                    for p2 in self.risk.positions:
                        if p2.get("ticker") == ticker:
                            p2["trail_pct"] = trail_pct
                            p2["hold_advice"] = advice
                hold_list.append((ticker, reason))
                log.info(f"[장전 리뷰] {ticker} → {action} 유지: {reason}")

        # ── 텔레그램 알림 ───────────────────────────────────────────────────
        lines = [f"🌅 <b>[장 시작 전 포지션 점검] {market}</b>"]
        if sell_list:
            lines.append("🔴 <b>매도 예정 (장 시작 즉시)</b>")
            for tk, rsn in sell_list:
                pos = next((p for p in self.risk.positions if p.get("ticker") == tk), {})
                name = str(pos.get("name", "") or "").strip() or self._lookup_ticker_name(tk, market)
                disp = f"{tk}({name})" if name else tk
                lines.append(f"  • {disp}: {rsn or '분석가 합의'}")
        if hold_list:
            lines.append("🟢 <b>홀드 유지</b>")
            for tk, rsn in hold_list:
                pos = next((p for p in self.risk.positions if p.get("ticker") == tk), {})
                name = str(pos.get("name", "") or "").strip() or self._lookup_ticker_name(tk, market)
                disp = f"{tk}({name})" if name else tk
                lines.append(f"  • {disp}: {rsn or '계속 보유'}")
        if not sell_list and not hold_list:
            lines.append("보유 포지션 없음")
        block_alert("장 시작 전 포지션 점검", lines[1:], market=market, icon="🌅")

    def _post_session_position_review(self, market: str, positions: list):
        """장 종료 후 이월 포지션 Claude 점검 — 왜 보유 중인지 이유 기록 + 텔레그램 전송."""
        if not positions:
            block_alert("장 종료 포지션", ["이월 포지션 없음"], market=market, icon="🌙")
            return

        digest = self.today_judgment.get("digest_prompt", "")
        lines = [f"🌙 <b>[장 종료 포지션 현황] {market}</b>", "━━━━━━━━━━━━━━━━"]

        for pos in positions:
            ticker  = pos.get("ticker", "")
            name    = str(pos.get("name", "") or "").strip() or self._lookup_ticker_name(ticker, market)
            disp    = f"{ticker}({name})" if name else ticker
            entry   = float(pos.get("display_avg_price", pos.get("entry", 0)) or 0)
            cur     = float(pos.get("display_current_price", pos.get("current_price", entry)) or 0)
            qty     = int(pos.get("qty", 0) or 0)
            pnl     = (cur / entry - 1) * 100 if entry else 0
            held    = int(pos.get("held_days", 0) or 0)
            is_us   = market == "US"
            px      = lambda v: f"${v:.2f}" if is_us else f"{v:,.0f}원"
            pnl_icon = "🟢" if pnl >= 0 else "🔴"

            # 수수료 차감 실손익
            fee = entry * qty * 0.00015 + cur * qty * (0.00015 if is_us else 0.00195)
            net = (cur - entry) * qty - fee
            if is_us:
                _rate = float(self.usd_krw_rate or 0)
                _net_krw = int(round(net * _rate)) if _rate > 0 else 0
                net_str = f"${net:+.2f}" + (f" (약 {_net_krw:+,}원)" if _rate > 0 else "")
            else:
                net_str = f"{int(round(net)):+,}원"

            # Claude hold_advisor 호출 (entry=0이면 건너뜀)
            action, reason, trail_info = "HOLD", "", ""
            if float(pos.get("entry", 0) or 0) <= 0:
                log.warning(f"[장후 리뷰] {ticker} entry=0 → hold_advisor 건너뜀, HOLD 유지")
                lines.append(f"  ⚠️ 진입가 미확정 → Claude 판단 보류 (HOLD)")
                continue
            try:
                from minority_report.hold_advisor import ask as advisor_ask
                advice = advisor_ask(self._advisor_pos(pos, market), market, digest)
                action = advice.get("action", "HOLD")
                votes  = advice.get("votes", {})
                for v in votes.values():
                    if v.get("action") == action and v.get("reason"):
                        reason = v["reason"][:80]
                        break
                # hold_advice 포지션에 기록 (대시보드에서 보임)
                for p2 in self.risk.positions:
                    if p2.get("ticker") == ticker:
                        p2["hold_advice"] = advice
                if action == "TRAIL" and advice.get("trail_pct"):
                    trail_info = f" (트레일링 {advice['trail_pct']*100:.1f}%)"
            except Exception as e:
                log.warning(f"[장후 리뷰] {ticker} hold_advisor 오류: {e}")

            action_ko = {"HOLD": "홀드 유지", "TRAIL": "트레일링 유지", "SELL": "매도 권고"}.get(action, action)

            # 매도 기준
            is_trailing = pos.get("trailing", False)
            trail_sl    = float(pos.get("trail_sl", 0) or 0)
            sl          = float(pos.get("sl", 0) or 0)
            tp          = float(pos.get("tp", 0) or 0)
            if is_trailing and trail_sl > 0:
                exit_cond = f"트레일링 {px(trail_sl)} 이하 시 매도"
            elif sl > 0 or tp > 0:
                parts = []
                if sl > 0: parts.append(f"손절 {px(sl)}")
                if tp > 0: parts.append(f"목표 {px(tp)}")
                exit_cond = " · ".join(parts)
            else:
                exit_cond = "장 시작 전 재판단"

            block = (
                f"{pnl_icon} <b>{disp}</b>  {pnl:+.2f}%  {net_str}\n"
                f"  매수 {px(entry)} → 현재 {px(cur)}  {qty}주  {held}일째\n"
                f"  📋 {exit_cond}\n"
                f"  🤖 Claude: <b>{action_ko}{trail_info}</b>"
            )
            if reason:
                block += f"\n     → {reason}"
            lines.append(block)

        # 포지션 파일 다시 저장 (hold_advice 갱신 반영)
        self._save_positions()
        self._write_live_status(market, force=True)
        block_alert("장 종료 포지션 현황", lines[1:], market=market, icon="🌙")

    def _intraday_position_review(self, market: str, force: bool = False,
                                   ticker_filter: str = ""):
        """장 중 1시간 주기 보유 포지션 Claude 점검.
        - 진입 후 최소 60분 경과한 포지션만 대상
        - SELL 결정 시 즉시 매도
        - force=True: 텔레그램 /review 명령어로 수동 호출
        - ticker_filter: 지정 시 해당 종목만 점검
        """
        if not self.session_active or self.current_market != market:
            if not force:
                return
        now_ts = time.time()
        # 해당 마켓 포지션만, 진입 60분 이상 경과한 것만
        MIN_HOLD_SEC = 3600
        positions = []
        for p in self.risk.positions:
            if self._ticker_market(p.get("ticker", "")) != market:
                continue
            if ticker_filter and p.get("ticker", "") != ticker_filter:
                continue
            # fill_time 또는 entry_date로 경과 판단
            fill_ts = p.get("_fill_ts", 0)
            if fill_ts and (now_ts - fill_ts) < MIN_HOLD_SEC and not force:
                continue
            positions.append(p)

        if not positions:
            if force:
                block_alert("장 중 포지션 점검", ["검토 대상 포지션 없음 (진입 후 60분 미경과)"], market=market, icon="🔍")
            return

        _intraday_ctx = self._build_intraday_context(market)
        digest = self.today_judgment.get("digest_prompt", "")
        if _intraday_ctx:
            digest = digest + "\n\n[장중 현재]\n" + _intraday_ctx
        label  = "수동 요청" if force else "1시간 정기"
        lines  = [f"🔍 <b>[장 중 포지션 점검 · {label}] {market}</b>", "━━━━━━━━━━━━━━━━"]

        for pos in positions:
            ticker  = pos.get("ticker", "")
            name    = str(pos.get("name", "") or "").strip() or self._lookup_ticker_name(ticker, market)
            disp    = f"{ticker}({name})" if name else ticker
            entry   = float(pos.get("display_avg_price", pos.get("entry", 0)) or 0)
            cur     = float(pos.get("display_current_price", pos.get("current_price", entry)) or 0)
            qty     = int(pos.get("qty", 0) or 0)
            pnl     = (cur / entry - 1) * 100 if entry else 0
            is_us   = market == "US"
            px      = lambda v: f"${v:.2f}" if is_us else f"{v:,.0f}원"
            pnl_icon = "🟢" if pnl >= 0 else "🔴"
            fee     = entry * qty * 0.00015 + cur * qty * (0.00015 if is_us else 0.00195)
            net     = (cur - entry) * qty - fee
            if is_us:
                _rate = float(self.usd_krw_rate or 0)
                _net_krw = int(round(net * _rate)) if _rate > 0 else 0
                net_str = f"${net:+.2f}" + (f" (약 {_net_krw:+,}원)" if _rate > 0 else "")
            else:
                net_str = f"{int(round(net)):+,}원"

            action, reason = "HOLD", ""
            try:
                from minority_report.hold_advisor import ask as advisor_ask
                advice = advisor_ask(self._advisor_pos(pos, market), market, digest)
                action = advice.get("action", "HOLD")
                votes  = advice.get("votes", {})
                for v in votes.values():
                    if v.get("action") == action and v.get("reason"):
                        reason = v["reason"][:80]
                        break
                # hold_advice 업데이트
                for p2 in self.risk.positions:
                    if p2.get("ticker") == ticker:
                        p2["hold_advice"] = advice
            except Exception as e:
                log.warning(f"[장중 리뷰] {ticker} 오류: {e}")

            action_ko = {"HOLD": "홀드 유지", "TRAIL": "트레일링 유지", "SELL": "즉시 매도"}.get(action, action)
            color_icon = "🔴" if action == "SELL" else ("🟡" if action == "TRAIL" else "🟢")

            block = (
                f"{pnl_icon} <b>{disp}</b>  {pnl:+.2f}%  {net_str}\n"
                f"  매수 {px(entry)} → 현재 {px(cur)}  {qty}주\n"
                f"  {color_icon} Claude: <b>{action_ko}</b>"
            )
            if reason:
                block += f"\n     → {reason}"
            lines.append(block)

            # SELL 결정 → 즉시 매도
            if action == "SELL":
                if not self.session_active:
                    log.warning(f"[장중 리뷰 SELL 스킵] {ticker} — session_active=False, 매도 불가")
                    lines.append(f"  ⚠️ 세션 비활성 — 매도 보류 (다음 세션 시작 시 재검토)")
                else:
                    # exit_price: KRW 기준 (price_cache) 또는 display_current_price
                    sell_px = self.price_cache.get(ticker, cur) or cur
                    if sell_px <= 0:
                        log.error(f"[장중 리뷰 SELL 스킵] {ticker} — 유효 가격 없음 (cur={cur})")
                        lines.append(f"  ❌ 유효 가격 없음 — 매도 보류")
                    else:
                        try:
                            cand = {**pos, "exit_price": sell_px, "reason": "intraday_review_sell"}
                            self._execute_sell(cand, market, reason="intraday_review_sell",
                                               hold_advice=advice)
                            lines.append(f"  ⚡ 매도 주문 접수 ({px(cur)} × {qty}주)")
                            log.info(f"[장중 리뷰 SELL] {ticker} {pnl:+.2f}% → 즉시 매도 ({sell_px:,.0f})")
                        except Exception as se:
                            log.error(f"[장중 리뷰 매도 실패] {ticker}: {se}")
                            lines.append(f"  ❌ 매도 실패: {se}")

        self._save_positions()
        self._write_live_status(market, force=True)
        block_alert(f"장 중 포지션 점검 · {label}", lines[1:], market=market, icon="🔍")

    def _handle_max_hold_claude(self, cand: dict, market: str):
        """max_hold 도달 시 Claude에게 SELL/HOLD 물어본 후 결정.
        - SELL: 즉시 매도
        - HOLD/TRAIL: max_hold +1 연장 (1회만, 이후 재도달 시 강제 청산)
        """
        from telegram_reporter import send as _send
        ticker    = cand["ticker"]
        held_days = int(cand.get("held_days", 0))
        max_hold  = int(cand.get("max_hold", 1))
        pnl       = float(cand.get("pnl_pct", 0))
        cur       = float(cand.get("exit_price", 0))
        is_us     = market == "US"
        px_fmt    = lambda v: f"${v:.2f}" if is_us else f"{v:,.0f}원"

        action, reason_txt, advice = "SELL", "", None

        # 이미 연장했으면: max_hold_final=True이면 진짜 강제 청산, 아니면 Claude 재확인
        already_extended = cand.get("max_hold_extended", False)
        if already_extended and cand.get("max_hold_final", False):
            log.info(f"[max_hold 강제청산] {ticker} — 2회 연장 후 재도달 → 즉시 청산")
            self._execute_sell(cand, market, reason="max_hold_final")
            _send(f"⏰ <b>[보유기한 만료] {ticker}</b>  {pnl:+.2f}%\n"
                  f"  max_hold {max_hold}일 2회 연장 후 재도달 → 즉시 청산")
            return

        entry_ok = float(cand.get("entry", 0) or 0) > 0
        if not entry_ok:
            log.warning(f"[max_hold Claude] {ticker} entry=0 → hold_advisor 건너뜀, HOLD 유지")
            action = "HOLD"
        else:
            try:
                from minority_report.hold_advisor import ask as advisor_ask
                _d = self.today_judgment.get("digest_prompt", "")
                _ic = self._build_intraday_context(market)
                digest = _d + "\n\n[장중 현재]\n" + _ic if _ic else _d
                advice = advisor_ask(self._advisor_pos(cand, market), market, digest)
                action = advice.get("action", "SELL")
                votes  = advice.get("votes", {})
                for v in votes.values():
                    if v.get("action") == action and v.get("reason"):
                        reason_txt = v["reason"][:100]
                        break
            except Exception as e:
                log.warning(f"[max_hold Claude] {ticker} hold_advisor 실패 → 기본 청산: {e}")
                action = "SELL"

        action_ko  = "즉시 청산" if action == "SELL" else f"보유기한 {max_hold+1}일로 연장"
        color_icon = "🔴" if action == "SELL" else "🟡"

        if action == "SELL":
            self._execute_sell(cand, market, reason="max_hold", hold_advice=advice)
            msg = (f"⏰ <b>[보유기한 만료 · Claude 청산] {ticker}</b>  {pnl:+.2f}%\n"
                   f"  {held_days}일 보유, {px_fmt(cur)} 매도\n"
                   f"  {color_icon} Claude: {action_ko}"
                   + (f"\n     → {reason_txt}" if reason_txt else ""))
        else:
            # max_hold +1 연장, 연장 플래그 세팅
            for p2 in self.risk.positions:
                if p2.get("ticker") == ticker:
                    p2["max_hold"] = max_hold + 1
                    p2["max_hold_extended"] = True
                    if already_extended:
                        # 2번째 연장 → 다음 재도달 시 진짜 강제 청산
                        p2["max_hold_final"] = True
                    if advice:
                        p2["hold_advice"] = advice
                    break
            self._save_positions()
            msg = (f"⏰ <b>[보유기한 만료 · Claude 연장] {ticker}</b>  {pnl:+.2f}%\n"
                   f"  {held_days}일 보유 → max_hold {max_hold+1}일로 연장 (1회 한정)\n"
                   f"  {color_icon} Claude: {action_ko}"
                   + (f"\n     → {reason_txt}" if reason_txt else ""))
        _send(msg)
        log.info(f"[max_hold Claude] {ticker} held={held_days} → {action} ({reason_txt[:50]})")

    def _handle_tp_trailing(self, cand: dict, market: str):
        """TP 도달 시 트레일링 스탑 전환 (분석가 합의 옵션)"""
        ticker     = cand["ticker"]
        trail_pct  = self.trailing_stop_pct

        hold_advice = None
        if self.enable_trailing_analyst:
            # 분석가 3명에게 HOLD/SELL 물어봄 (entry=0이면 건너뜀)
            entry_ok = float(cand.get("entry", 0) or 0) > 0
            if not entry_ok:
                log.warning(f"[TP trailing] {ticker} entry=0 → hold_advisor 건너뜀, 트레일링 즉시 전환")
            else:
                try:
                    from minority_report.hold_advisor import ask as advisor_ask
                    _d = self.today_judgment.get("digest_prompt", "")
                    _ic = self._build_intraday_context(market)
                    digest = _d + "\n\n[장중 현재]\n" + _ic if _ic else _d
                    advice = advisor_ask(self._advisor_pos(cand, market), market, digest)
                    if advice["action"] == "SELL":
                        log.info(f"[TP→분석가합의:SELL] {ticker} — 즉시 청산")
                        trailing_alert(market, ticker, "sell", detail="분석가 합의: 즉시 청산")
                        self._execute_sell(cand, market, reason="tp_analyst_sell",
                                           hold_advice=advice)
                        return
                    trail_pct  = advice["trail_pct"]
                    hold_advice = advice
                    log.info(f"[TP→분석가합의:HOLD] {ticker} trail={trail_pct:.2%}")
                    trailing_alert(market, ticker, "hold", trail_pct=trail_pct)
                except Exception as e:
                    log.warning(f"[hold_advisor] 오류 → trail 기본값 적용: {e}")
        else:
            log.info(f"[TP→트레일링] {ticker} trail={trail_pct:.2%} (분석가 생략)")
            trailing_alert(market, ticker, "trail", trail_pct=trail_pct)

        self.risk.activate_trailing(ticker, trail_pct, hold_advice=hold_advice)

    def _execute_sell(self, cand: dict, market: str, reason: str,
                      hold_advice: dict = None):
        """실제 매도 실행 + 텔레그램 알림 + hold_advisor 결과 기록"""
        raw_px   = self.price_cache_raw.get(cand["ticker"], cand["exit_price"])
        if float(cand.get("exit_price", 0) or 0) <= 0 or float(raw_px or 0) <= 0:
            log.error(f"sell skipped [{cand['ticker']}]: invalid exit/raw price exit={cand.get('exit_price')} raw={raw_px}")
            return
        order_px = self._compute_order_price("sell", market, float(raw_px))
        try:
            precheck = precheck_order(cand["ticker"], cand["qty"], order_px, "sell", self.token, market=market)
        except Exception as _pre_e:
            self._note_sell_failure(market, cand["ticker"], reason, str(_pre_e))
            log.error(f"sell precheck exception [{cand['ticker']}]: {_pre_e}")
            return
        if not precheck.get("ok"):
            self._note_sell_failure(market, cand["ticker"], reason, precheck.get("msg", "주문 불가"))
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
        try:
            result = place_order(cand["ticker"], cand["qty"], order_px, "sell", self.token, market=market)
        except Exception as _pe:
            self._note_sell_failure(market, cand["ticker"], reason, str(_pe))
            log.error(f"sell order exception [{cand['ticker']}]: {_pe}")
            return
        if not result["success"]:
            self._note_sell_failure(market, cand["ticker"], reason, result.get("msg", ""))
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
        self._sell_fail_meta.pop(cand["ticker"], None)
        self._sell_fail_at.pop(cand["ticker"], None)
        # 매도 완료 티커 등록 — broker sync 재주입 방지
        _closed_key = cand["ticker"].upper() if market == "US" else cand["ticker"]
        self._session_closed_tickers.setdefault(market, set()).add(_closed_key)

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
        ex_name = str(ex.get("name", "") or "").strip() or self._lookup_ticker_name(ex["ticker"], market)
        pnl_alert(ex["ticker"], ex["pnl_pct"], int(ex["pnl"]), reason, market=market, name=ex_name, usd_krw=self.usd_krw_rate)
        trade_alert("sell", ex["ticker"], ex["qty"], int(raw_exit), ex["strategy"], 0, 0, reason=reason, market=market, name=ex_name, usd_krw=self.usd_krw_rate)
        try:
            _perf_strategy = str(ex.get("strategy", "unknown") or "unknown")
            if _perf_strategy not in ("broker_sync", "broker_balance", ""):
                BrainDB.update_strategy_performance(
                    market, _perf_strategy,
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

            # US: price_cache (KRW 환산값) 우선 사용 — raw USD가 잘못 들어오는 경우 방지
            if market == "US":
                ticker = ex.get("ticker", "")
                krw_px = self.price_cache.get(ticker, 0.0)
                if krw_px > 0 and self.usd_krw_rate > 0:
                    # price_cache 값이 exit_price보다 신뢰도 높음
                    exit_price = krw_px
                elif exit_price > 0 and self.usd_krw_rate > 0 and exit_price < 5000:
                    # exit_price가 USD raw 값으로 의심될 때 (< $5000 수준) → KRW 환산
                    exit_price = exit_price * self.usd_krw_rate

            if decision in ("HOLD", "TRAIL"):
                # HOLD/TRAIL 성공: 청산가 > TP 도달가 (보유/트레일 덕에 더 벌었음)
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
            fname = _judgment_runtime_path(self._mode, target_date, market)
            if not fname.exists():
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

    def _seconds_until_session_close(self, market: str) -> float:
        now_kst = datetime.now(KST)
        if market == "KR":
            close_dt = now_kst.replace(hour=16, minute=0, second=0, microsecond=0)
        else:
            close_dt = now_kst.replace(hour=5, minute=0, second=0, microsecond=0)
            if now_kst.time() >= dt_time(22, 20):
                close_dt += timedelta(days=1)
        return max(0.0, (close_dt - now_kst).total_seconds())

    def _compute_startup_guard_sec(self, market: str, trigger: str) -> float:
        remaining_to_close = self._seconds_until_session_close(market)
        if remaining_to_close <= _STARTUP_GUARD_SEC:
            return 0.0
        if trigger == "startup_mid_session":
            return 0.0
        return _STARTUP_GUARD_SEC

    def _restore_daily_pnl_from_decisions(self, market: str) -> None:
        if not DECISIONS_FILE.exists():
            return
        today = _market_session_date(market).isoformat()
        restored_pnl = 0.0
        restored_count = 0
        seen_keys = set()
        try:
            for line in DECISIONS_FILE.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("type") != "closed" or rec.get("market") != market:
                    continue
                if str(rec.get("timestamp", "") or "")[:10] != today:
                    continue
                order_no = str(rec.get("order_no", "") or "").strip()
                dedup_key = order_no or f"{rec.get('ticker', '')}_{rec.get('timestamp', '')}"
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)
                restored_pnl += float(rec.get("pnl_krw", 0) or 0)
                restored_count += 1
        except Exception as e:
            log.warning(f"[{market}] decisions.jsonl daily_pnl 복구 실패: {e}")
            return
        self.risk.daily_pnl = restored_pnl
        if restored_count > 0:
            log.info(f"[{market}] daily_pnl 복구: {restored_pnl:,.0f}원 ({restored_count}건)")

    def session_open(self, market: str, trigger: str = "schedule"):
        if not self._enter_market_task(market, "session_open"):
            return
        _log_flow("info", "=" * 50)
        _log_flow("info", f"[{market}] session_open")
        self._refresh_claude_control()
        self._backfill_missed_postmortem(market)

        # ── 휴장일 체크 (주말 + 공휴일) ─────────────────────────────────────
        if not _is_trading_day(market):
            _log_flow("info", f"[{market}] 휴장일 — 세션 스킵")
            self.session_active = False
            self.current_market = None
            self._leave_market_task(market, "session_open")
            return

        # US 개장 시 스크리너 캐시 무효화 → 신선한 후보로 시작
        if market == "US":
            try:
                _US_SCREEN_CACHE_PATH.unlink(missing_ok=True)
                _log_flow("info", "[US] 개장 시 스크리너 캐시 무효화")
            except Exception as _uce:
                log.debug(f"[US] 스크리너 캐시 무효화 실패(무시): {_uce}")

        self._refresh_token()
        self.session_active = True
        self.current_market = market
        self._execution_flags[market] = set()
        self.risk.market = market          # 수수료율 시장에 맞게 설정
        self.tuning_count = 0
        self._session_events = []          # 세션 이벤트 초기화
        self._entry_blocked = {}           # 새 세션 시작 시 쿨다운 초기화
        self._order_error_count = {}       # 주문 연속 오류 카운터 초기화
        self.decision_event_log = []
        self._daily_sl_count[market] = 0   # 당일 손절 카운터 초기화
        self._session_closed_tickers[market] = set()  # 매도 완료 티커 초기화
        self._normalize_pending_orders()
        self._save_pending_orders()
        self._reset_us_order_cache()
        self._sync_runtime_with_broker()
        self.risk.increment_holding_days()

        # ── 데이터 무결성 자동 점검 ──────────────────────────────────────────
        try:
            from claude_memory.data_integrity import run_pre_session_check
            integrity = run_pre_session_check(market)
            for msg in integrity["fixed"]:
                _log_normal("info", f"[data_integrity] 수정: {msg}")
            for msg in integrity["warnings"]:
                _log_risk("warning", f"[data_integrity] 경고: {msg}")
            if integrity["fixed"]:
                _log_normal("info", f"[data_integrity] 총 {len(integrity['fixed'])}건 자동 수정 완료")
        except Exception as _di_e:
            _log_risk("warning", f"[data_integrity] 점검 실패 (무시하고 계속): {_di_e}")


        # ── USD/KRW 환율 자동 갱신 ────────────────────────────────────────────
        try:
            new_rate = get_usd_krw()
            if new_rate > 100:
                old_rate = self.usd_krw_rate
                self.usd_krw_rate = new_rate
                _log_normal("info", f"[환율 갱신] USD/KRW {old_rate:,.0f} → {new_rate:,.2f}원")
        except Exception as e:
            _log_risk("warning", f"[환율 갱신 실패] {e} — 이전 값 {self.usd_krw_rate:,.0f}원 유지")

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
        self.risk.update_prices(self.price_cache, self.price_cache_raw)
        # ── session_start_equity: 환율 갱신 + 현재가 반영 이후 기준으로 확정 ──
        # (이전에 reset_daily_state를 먼저 호출하면 구환율 기준으로 base가 잡혀
        #  환율 변동분이 일일 손실로 잘못 계산되는 버그 수정)
        session_date = _market_session_date(market).isoformat()
        baseline_meta = dict(self._daily_baseline_by_market.get(market, {}) or {})
        if baseline_meta.get("session_date") == session_date and float(baseline_meta.get("base", 0) or 0) > 0:
            self.risk.session_start_equity = float(baseline_meta["base"])
            if trigger == "startup_mid_session":
                self._restore_daily_pnl_from_decisions(market)
            _log_normal(
                "info",
                f"[일일 기준 유지] {market} session_date={session_date} "
                f"session_start_equity={self.risk.session_start_equity:,.0f}원"
            )
        else:
            broker_total = float(self._kis_total_equity_krw() or 0)
            if broker_total <= 0:
                broker_total = float(self._equity_reference_context().get("total_krw", 0) or 0)
            self.risk.reset_daily_state(clear_trade_log=True, override_base=broker_total if broker_total > 0 else None)
            self._daily_baseline_by_market[market] = {
                "session_date": session_date,
                "base": float(self.risk.session_start_equity),
                "source": "broker_total" if broker_total > 0 else "internal_fallback",
            }
            self._save_daily_baselines()
            _log_normal(
                "info",
                f"[일일 기준 확정] {market} session_date={session_date} "
                f"session_start_equity={self.risk.session_start_equity:,.0f}원 "
                f"source={self._daily_baseline_by_market[market]['source']}"
            )
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
                pre_candidates = screen_market_kr(self.token, mode=self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
                if pre_candidates:
                    self._last_kr_candidates = pre_candidates
            else:
                pre_candidates = screen_market_us(mode=self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
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
        live_path = _judgment_runtime_path(self._mode, today, market)
        legacy_live_path = JUDGMENT_DIR / f"{today.replace('-', '')}_{market}.json"
        reused = False
        _from_shared_cache = False
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
                        "digest_raw": saved.get("digest_raw", {}),
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

        if (not reused) and legacy_live_path.exists():
            try:
                with open(legacy_live_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                saved_mode = saved.get("mode", "")
                if saved_mode not in ("historical_sim",) and saved.get("judgments") and saved.get("consensus"):
                    self.today_judgment = {
                        "date": today,
                        "market": market,
                        "judgments": saved["judgments"],
                        "consensus": saved["consensus"],
                        "digest_prompt": saved.get("digest_prompt", ""),
                        "digest_raw": saved.get("digest_raw", {}),
                        "tickers": saved.get("tickers", []),
                        "universe_tickers": saved.get("universe_tickers", []),
                        "round1_judgments": saved.get("round1_judgments", {}),
                        "debate_changes": saved.get("debate_changes", []),
                    }
                    self.today_tickers[market] = saved.get("tickers", [])
                    judgments = saved["judgments"]
                    consensus = saved["consensus"]
                    digest_prompt = saved.get("digest_prompt", "")
                    reused = True
                    log.info(f"[session reuse fallback] {today} {market} consensus={consensus['mode']}")
            except Exception as e:
                log.warning(f"[session reuse fallback] load failed: {e}")

        # ── 공유 판단 캐시 확인 (paper/live Claude 중복 호출 방지) ─────────────
        # 같은 날 같은 장에서 한 쪽 프로세스가 이미 판단을 생성했으면
        # get_three_judgments 재호출 없이 결과를 재사용한다.
        # 스크리너 + select_tickers 는 포트폴리오 맥락이 달라 각자 실행한다.
        _from_shared_cache = False
        if not reused:
            _shared = shared_judgment_cache.load(market, today)
            if _shared:
                judgments = _shared["judgments"]
                consensus = _shared["consensus"]
                _from_shared_cache = True
                log.info(
                    f"[공유판단캐시] {today} {market} consensus={consensus['mode']} "
                    f"(Claude 재호출 생략, {self._mode} 런타임 재사용)"
                )

        if not reused:
            digest = build_kr_digest(today, universe_tickers=universe_tickers) if market == "KR" \
                else build_us_digest(today, universe_tickers=universe_tickers)
            # select_tickers / watchlist_alert 는 현재 런타임에서 다시 만든 최신 digest를 사용한다.
            digest_prompt = digest_to_prompt(digest)

            if not _from_shared_cache:
                # ── Claude 판단 (신규 생성) ──────────────────────────────────
                brain_summary = BrainDB.generate_prompt_summary(market)
                brain_data = BrainDB.load()
                correction = json.dumps(brain_data.get("correction_guide", {}).get(market, {}), ensure_ascii=False)
                judgments = get_three_judgments(
                    digest_prompt, brain_summary, correction,
                    market=market,
                )
                consensus = build_consensus(judgments, market=market)

            # ── VIX 동적 사이즈 보정 (US 전용, 항상 최신 digest 기준) ─────────
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

            if not _from_shared_cache:
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
                candidates = screen_market_kr(self.token, mode=consensus.get("mode", "NEUTRAL"))
                if candidates:
                    self._last_kr_candidates = candidates
            else:
                candidates = screen_market_us(mode=consensus.get("mode", "NEUTRAL"))
            self._log_screen_candidates(market, candidates, "session_open")
            candidates = self._prefill_history_sync(candidates, market)
            candidates = self._filter_candidates_by_history(candidates, market)
            log.info(f"[스크리너] {market} 후보 {len(candidates)}개 → Claude 선택 중...")
            selected, sel_reasons = select_tickers(market, digest_prompt, consensus["mode"], candidates)
            self.today_tickers[market] = selected
            self.today_ticker_reasons[market] = sel_reasons or {}
            # 퍼널: selected 카운트 (세션 시작 시 선택된 종목 수)
            self._funnel[market]["selected"] += len(selected)
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
            if _from_shared_cache:
                debate_meta = {
                    "r1": _shared.get("round1_judgments", {}),
                    "changes": _shared.get("debate_changes", []),
                }
            else:
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

            # ── 신규 생성 판단을 공유 캐시에 저장 (상대 프로세스가 재사용) ──────
            if not _from_shared_cache:
                shared_judgment_cache.save(market, today, self.today_judgment)
        else:
            # 판단은 재사용하되 종목은 항상 새로 스크리닝
            # (reused=True 시 전날 저장된 종목이 그대로 고정되는 문제 방지)
            log.info(f"[종목 재스크리닝] {market} 판단 재사용 + 종목만 새로 선택 (mode={consensus['mode']})")
            if market == "KR":
                fresh_candidates = screen_market_kr(self.token, mode=self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
                if fresh_candidates:
                    self._last_kr_candidates = fresh_candidates
            else:
                fresh_candidates = screen_market_us(mode=self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
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

        # 장전 보유 포지션 리뷰는 당일 판단이 준비된 뒤에만 수행한다.
        try:
            self._pre_session_position_review(market)
        except Exception as _psr_e:
            log.warning(f"[장전 포지션 리뷰 오류] {market}: {_psr_e}")

        selected = self.today_tickers.get(market, [])
        try:
            balance = get_balance(self.token, market=market)
        except Exception as e:
            log.warning(f"balance lookup failed [{market}]: {e}")
            balance = {"stocks": [], "total_eval": 0, "cash": 0, "total_profit": 0, "profit_rate": 0.0}
        if not reused and not _from_shared_cache:
            morning_briefing(market, judgments, consensus, balance, digest,
                             round1_judgments=debate_meta.get("r1", {}),
                             debate_changes=debate_meta.get("changes", []))
        else:
            reason = "공유캐시 재사용" if _from_shared_cache else "당일 판단 재사용"
            log.info(f"[{reason}] 브리핑 스킵 consensus={consensus['mode']}")
        log.info(f"consensus: {consensus['mode']} size={consensus['size']}%")

        self.ws = KISWebSocket(
            self.token, selected,
            on_tick=self._on_tick,
            on_notice=self._on_fill_notice,
            market=market,
        )
        self.ws.start()
        self._session_open_at[market] = time.time()  # startup guard 기준점
        self._session_startup_guard_sec[market] = self._compute_startup_guard_sec(market, trigger)
        if self._session_startup_guard_sec[market] <= 0:
            _log_flow(
                "info",
                f"[{market}] startup guard bypass "
                f"(trigger={trigger}, close_in={self._seconds_until_session_close(market):.0f}s)"
            )

        # ── Claude 파라미터 검토 (4th layer) — session_open 1회 ──────────────
        try:
            _param_tuner.reset_session(market)
            self._run_param_review(market, trigger="session_open")
        except Exception as _pt_e:
            log.warning(f"[param_tuner session_open] {market}: {_pt_e}")

        # 새 세션 시작 시 장중 H/L 초기화 + continuation entry 플래그 리셋
        for _t in selected:
            self._intraday_high.pop(_t, None)
            self._intraday_low.pop(_t, None)
            self._or_high.pop(_t, None)
            self._or_low.pop(_t, None)
            self._or_formed.pop(_t, None)
            self._continuation_used.pop(_t, None)
        # session_open 완료 즉시 대시보드 강제 갱신 — stale live_status 방지
        self._maybe_push_dashboard(force=True)
        self._leave_market_task(market, "session_open")

    # ── Claude 파라미터 검토 헬퍼 ─────────────────────────────────────────────
    def _run_param_review(self, market: str, trigger: str = "session_open") -> None:
        """모든 전략의 adaptive_params를 계산해 Claude 검토 레이어에 넘긴다.

        결과는 _param_tuner._cache[market]에 저장되어
        run_cycle의 _ap() 헬퍼에서 자동으로 참조된다.
        """
        consensus  = self.today_judgment.get("consensus", {})
        mode       = consensus.get("mode", "NEUTRAL")
        conf       = float(consensus.get("confidence", 0.6) or 0.6)
        ctx        = (
            getattr(self, "_ca_context_last", None)
            or (self.today_judgment.get("digest_raw", {}) or {}).get("context", {})
            or {}
        )
        vix        = float(ctx.get("vix") or 0) or None
        usd_krw    = float(self.usd_krw_rate or 0) or None

        all_strats = [
            "opening_range_pullback",
            "gap_pullback",
            "momentum",
            "mean_reversion",
            "volatility_breakout",
        ]
        all_params = {}
        for strat in all_strats:
            try:
                all_params[strat] = _adaptive_params(strat, market, mode, conf,
                                                     context=ctx)
            except Exception as _ape:
                log.debug("[param_review] %s params 오류: %s", strat, _ape)

        if not all_params:
            return

        _param_tuner.claude_review(
            market=market,
            mode=mode,
            all_params=all_params,
            context={"vix": vix, "usd_krw": usd_krw, "analyst_conf": conf},
            trigger=trigger,
            force=(trigger in ("reverse", "mode_change")),
        )

    def _on_tick(self, data: dict):
        ticker = data["ticker"]
        market = self._ticker_market(ticker)
        raw_price = data["price"]
        if not raw_price or raw_price <= 0:
            log.warning(f"[WS tick] {ticker} invalid price={raw_price} — 무시")
            return
        self.price_cache_raw[ticker] = raw_price
        self.price_cache[ticker] = self._price_to_krw(raw_price, market)
        self.risk.update_prices(self.price_cache, self.price_cache_raw)
        # 장중 고가/저가 누적 — 당일봉 단일가봉 탈출용
        if raw_price > self._intraday_high.get(ticker, 0):
            self._intraday_high[ticker] = raw_price
        _cur_low = self._intraday_low.get(ticker, float("inf"))
        if raw_price < _cur_low:
            self._intraday_low[ticker] = raw_price
        # 장초 OR(opening range) 추적 — OR 윈도우 종료 후 freeze
        if market in ("KR", "US") and self.session_active and self.current_market == market:
            _or_elapsed = self._market_elapsed_min(market)
            _or_minutes = 10 if market == "KR" else 15
            if 0 < _or_elapsed <= _or_minutes and not self._or_formed.get(ticker, False):
                if raw_price > self._or_high.get(ticker, 0):
                    self._or_high[ticker] = raw_price
                _or_cur_low = self._or_low.get(ticker, float("inf"))
                if raw_price < _or_cur_low:
                    self._or_low[ticker] = raw_price
            elif _or_elapsed > _or_minutes and not self._or_formed.get(ticker, False):
                _h = self._or_high.get(ticker, 0.0)
                _l = self._or_low.get(ticker, float("inf"))
                if _h > 0 and _l != float("inf") and _h >= _l:
                    self._or_formed[ticker] = True
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
        self._funnel[market]["filled"] += 1

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
        fill_confirm_alert(
            market=market,
            ticker=ticker,
            qty=filled_qty,
            order_no=order_no,
            price=filled_price,
            source="WS 실시간",
            name=str(matched.get("name", "") or "").strip(),
            usd_krw=self.usd_krw_rate,
        )
        log.info(
            f"[WS 체결통보] {ticker} {filled_qty}주 @{log_price} "
            f"| 주문번호={order_no} | market={market} | pending→filled 완료"
        )

    def _entry_scan_interval_sec(self, market: str) -> int:
        real_elapsed = self._market_elapsed_min(market)
        if 0 < real_elapsed <= _ENTRY_SCAN_OPENING_MIN:
            return _ENTRY_SCAN_OPENING_INTERVAL_MIN * 60   # 개장: 2분 (KR/US 동일)
        if market == "US":
            return int(os.getenv("US_ENTRY_SCAN_REGULAR_INTERVAL_MIN", "5")) * 60  # US 정규: 5분
        return _ENTRY_SCAN_REGULAR_INTERVAL_MIN * 60       # KR 정규: 10분

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
            self.risk.update_prices(self.price_cache, self.price_cache_raw)
            self._process_exit_candidates()
            self._write_live_status(market)
        except Exception as _hk_e:
            log.error(f"[housekeeping 오류] {market}: {_hk_e}", exc_info=True)
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
        _regular_min = int(os.getenv("US_ENTRY_SCAN_REGULAR_INTERVAL_MIN", "5")) if market == "US" else _ENTRY_SCAN_REGULAR_INTERVAL_MIN
        log.info(
            f"[{market} 엔트리 스캔] interval={int(interval_sec/60)}분 "
            f"(장초 {_ENTRY_SCAN_OPENING_MIN}분={_ENTRY_SCAN_OPENING_INTERVAL_MIN}분, 이후={_regular_min}분)"
        )
        try:
            self.run_cycle(market)
        except Exception as _es_e:
            log.error(f"[entry_scan 오류] {market}: {_es_e}", exc_info=True)

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
        self._refresh_operational_halt(market)
        _allow_halt_auto_release = self._has_broker_sync_risk(market)
        if self.risk.check_halt(
            allow_auto_release=_allow_halt_auto_release,
            auto_release_note="broker_sync_recovery" if _allow_halt_auto_release else "",
        ):
            _log_risk(
                "warning",
                f"[{market}] cycle halt active "
                f"reason={self.risk.halt_reason or 'daily_halt'} "
                f"daily_pnl={self.risk.daily_pnl:,.0f}",
            )
            analysis_log.info(
                f"[skip {market}] session_halt",
                extra={"extra": {
                    "event": "entry_skip",
                    "market": market,
                    "reason": self.risk.halt_reason or "daily_halt",
                    "mode": self.today_judgment.get("consensus", {}).get("mode", ""),
                }},
            )
            self._leave_market_task(market, "run_cycle")
            return
        # startup 보호 구간 — session_open 완료 후 최소 대기
        _since_open = time.time() - self._session_open_at.get(market, 0.0)
        _guard_sec = float(self._session_startup_guard_sec.get(market, _STARTUP_GUARD_SEC) or 0.0)
        if _since_open < _guard_sec:
            _log_flow(
                "info",
                f"[{market}] startup guard — session_open 후 {_since_open:.0f}s 경과 "
                f"(최소 {_guard_sec:.0f}s 대기 중) — cycle 스킵"
            )
            self._leave_market_task(market, "run_cycle")
            return
        # ── 장전 SELL 예약 큐 처리 (startup guard 해제 직후 1회) ─────────────
        sell_queue = self._pre_session_sell_queue.get(market, [])
        if sell_queue:
            if self._has_broker_sync_risk(market):
                _log_risk("warning", f"[장전 SELL 큐] {market} 브로커 동기화 불신 상태 → 예약 매도 전부 보류")
                self._pre_session_sell_queue[market] = []
            else:
                _log_flow("info", f"[장전 SELL 큐] {market} {len(sell_queue)}건 매도 실행")
                self._pre_session_sell_queue[market] = []
                for _sq_pos in sell_queue:
                    _sq_tk = _sq_pos.get("ticker", "")
                    try:
                        # 큐 생성 시점의 current_price는 구환율 기준일 수 있으므로
                        # 실제 실행 시점의 price_cache(최신 환율 반영) 우선 사용
                        _sq_cp = (
                            self.price_cache.get(_sq_tk)
                            or _sq_pos.get("current_price")
                            or _sq_pos.get("entry", 0)
                        )
                        cand = {**_sq_pos, "exit_price": _sq_cp, "reason": "pre_session_sell"}
                        self._execute_sell(cand, market, reason="pre_session_sell",
                                           hold_advice=_sq_pos.get("hold_advice"))
                    except Exception as _sqe:
                        log.error(f"[장전 SELL 실패] {_sq_tk}: {_sqe}")

        self._refresh_claude_control()
        self._consume_pending_claude_trigger(market)
        self._consume_pending_position_review(market)
        self._consume_pending_sell(market)

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
                "is_simulated": 1 if self.is_paper else 0,
                "data_source":  "paper" if self.is_paper else "live",
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
            diag_json_: Optional[dict] = None,
            extra_fields_: Optional[dict] = None,
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
        _ca_context = dict(self.today_judgment.get("digest_raw", {}).get("context", {}))

        # US 전용: VIX 30분마다 장중 갱신 (경계 진동 방지 위해 VIX만 갱신)
        _VIX_REFRESH_INTERVAL = 1800  # 30분
        if market == "US" and _ca_context:
            _now_ts = time.time()
            if _now_ts - self._vix_refresh_at >= _VIX_REFRESH_INTERVAL:
                try:
                    import yfinance as _yf
                    _vix_live = float(_yf.Ticker("^VIX").history(period="1d")["Close"].iloc[-1])
                    if _vix_live > 0:
                        _vix_prev = float(_ca_context.get("vix", 0) or 0)
                        _ca_context = {**_ca_context, "vix": round(_vix_live, 2)}
                        self._vix_refresh_at = _now_ts
                        log.info(f"[VIX 갱신] {_vix_prev:.1f} → {_vix_live:.1f}")
                except Exception as _ve:
                    log.debug(f"[VIX 갱신 실패] {_ve}")

        self._ca_context_last = _ca_context   # param_tuner가 참조
        _fear_label = get_vix_regime(_ca_context, market)
        if _ca_context:
            log.debug(f"[{market}] cross-asset 보정 적용 | {_fear_label} | "
                      f"USD/KRW {_ca_context.get('usd_krw', 0):,.0f}")

        # US 전략 우선순위: 분석가 투표 전략 → mean_reversion → gap_pullback
        # momentum/VB 모두 disabled이므로 base는 mean_reversion, gap_pullback 고정
        if market == "US":
            _voted_strat = _analyst_strategy_vote(_judgments)
            _us_base = ["opening_range_pullback", "mean_reversion", "gap_pullback"]
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
                # 세션 내 주문 차단 종목 스킵 (KR/US 공통 — 매매불가/3회연속 500오류)
                _order_blk = self._us_order_block_reason(ticker)
                if _order_blk:
                    analysis_log.info(
                        f"[skip {market}] {ticker} order_blocked",
                        extra={"extra": {
                            "event": "entry_skip",
                            "market": market,
                            "ticker": ticker,
                            "reason": "order_blocked",
                            "detail": _order_blk,
                            "mode": mode,
                        }},
                    )
                    log.info(f"[주문차단 {market}] {ticker} — {_order_blk}")
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
                    self._flag_execution_issue(market, "quote_invalid")
                    cnt = self._invalid_price_count.get(ticker, 0) + 1
                    self._invalid_price_count[ticker] = cnt
                    _MAX_INVALID = 1 if market == "US" else 2
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
                    analysis_log.info(
                        f"[skip {market}] {ticker} invalid_price",
                        extra={"extra": {
                            "event": "entry_skip",
                            "market": market,
                            "ticker": ticker,
                            "reason": "invalid_price",
                            "invalid_count": cnt,
                            "invalid_limit": _MAX_INVALID,
                            "price": float(price or 0),
                            "mode": mode,
                        }},
                    )
                    continue
                # 이전 정상가 대비 30% 초과 괴리 → KIS API outlier 방어
                _prev_price = self.price_cache_raw.get(ticker, 0)
                if _prev_price > 0 and abs(price - _prev_price) / _prev_price > 0.30:
                    # KR: 1회 재조회 시도 — API 노이즈 vs 실제 급등 구분
                    if market == "KR":
                        try:
                            _retry_info = get_price(ticker, self.token, market=market)
                            _retry_px = _retry_info["price"]
                            if _retry_px > 0 and abs(_retry_px - _prev_price) / _prev_price <= 0.30:
                                # 재조회 결과가 정상 → 첫 조회가 오류였음, 정상 가격 사용
                                log.info(
                                    f"[outlier_retry OK {market}] {ticker} "
                                    f"first={price:,} retry={_retry_px:,} → 정상가 사용"
                                )
                                price = _retry_px
                                price_info = _retry_info
                                risk_price = self._price_to_krw(price, market)
                                self.price_cache_raw[ticker] = price
                                self.price_cache[ticker] = risk_price
                                self._invalid_price_count.pop(ticker, None)
                                # outlier 아님으로 처리 — 아래 skip 블록 건너뜀
                                # (continue를 _outer if 안에서 호출할 수 없으므로 플래그 사용)
                                _outlier_confirmed = False
                            else:
                                log.warning(
                                    f"[outlier_retry FAIL {market}] {ticker} "
                                    f"retry={_retry_px:,} 도 괴리 → 이상치 확정"
                                )
                                _outlier_confirmed = True
                        except Exception as _re:
                            log.warning(f"[outlier_retry ERR {market}] {ticker}: {_re}")
                            _outlier_confirmed = True
                    else:
                        _outlier_confirmed = True

                    if _outlier_confirmed:
                        self._flag_execution_issue(market, "quote_outlier")
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
                        analysis_log.info(
                            f"[skip {market}] {ticker} outlier_price",
                            extra={"extra": {
                                "event": "entry_skip",
                                "market": market,
                                "ticker": ticker,
                                "reason": "outlier_price",
                                "invalid_count": cnt,
                                "invalid_limit": _MAX_OUTLIER,
                                "price": float(price or 0),
                                "prev_price": float(_prev_price or 0),
                                "mode": mode,
                            }},
                        )
                        continue
                self._invalid_price_count.pop(ticker, None)  # 정상 가격 수신 시 카운터 리셋
                self.price_cache_raw[ticker] = price
                self.price_cache[ticker] = risk_price
                self.risk.update_prices(self.price_cache, self.price_cache_raw)
                # US는 VTS에서 WS 시세 미사용 / 실전에서는 WS 틱으로 price_cache 업데이트됨
                # 두 경우 모두 scan 시 polling 가격으로 OR 근사 누적 (실전 WS 틱은 _on_tick에서 별도 누적)
                if market == "US" and self.session_active and self.current_market == "US":
                    _or_elapsed_us = self._market_elapsed_min("US")
                    _or_minutes_us = 15
                    if 0 < _or_elapsed_us <= _or_minutes_us and not self._or_formed.get(ticker, False):
                        if price > self._or_high.get(ticker, 0):
                            self._or_high[ticker] = price
                        _or_cur_low = self._or_low.get(ticker, float("inf"))
                        if price < _or_cur_low:
                            self._or_low[ticker] = price
                    elif _or_elapsed_us > _or_minutes_us and not self._or_formed.get(ticker, False):
                        _h = self._or_high.get(ticker, 0.0)
                        _l = self._or_low.get(ticker, float("inf"))
                        if _h > 0 and _l != float("inf") and _h >= _l:
                            self._or_formed[ticker] = True
                self._process_exit_candidates()

                if mode == "HALT":
                    analysis_log.info(
                        f"[skip {market}] {ticker} halt_mode",
                        extra={"extra": {
                            "event": "entry_skip",
                            "market": market,
                            "ticker": ticker,
                            "reason": "halt_mode",
                            "price": float(price),
                            "mode": mode,
                        }},
                    )
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

                ok, reason = self._entry_allowed_by_broker_state(market)
                if ok:
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
                _vol_missing = False   # today-bar 주입 전 초기화 — 주입 블록 안에서 True로 덮어씀
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
                        else:
                            # 오늘 봉이 이미 CSV에 있음(price_collector 선기록) — live vol 누락 여부는 별도 확인
                            if market == "US":
                                _pi_vol_chk = float(price_info.get("volume", 0) or 0)
                                if _pi_vol_chk == 0.0 or _pi_vol_chk == 1.0:
                                    _vol_missing = True
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
                            # US 모의투자 환경: Finnhub vol=0 또는 vol=1 반환 → 전일 평균으로 대체
                            _vol_missing = (
                                market == "US" and (_raw_vol == 0.0 or _raw_vol == 1.0)
                            )
                            if _vol_missing:
                                # 최근 20일 hist vol 평균으로 추정 (세션 진행률 반영)
                                _hist_vols = candles["volume"].replace(0, float("nan")).dropna()
                                _hist_avg = float(_hist_vols.tail(20).mean()) if len(_hist_vols) >= 5 else 0.0
                                _sess_prog = self._intraday_session_progress(market)
                                # 세션 진행률 × hist_avg → 현재까지 쌓였을 거래량 추정 (하한 20%)
                                _fallback_vol = _hist_avg * max(0.20, min(1.0, _sess_prog))
                                log.debug(
                                    f"[vol_fallback US] {ticker} raw_vol={_raw_vol:.0f}"
                                    f" → hist_avg={_hist_avg:,.0f} × prog={_sess_prog:.2f}"
                                    f" = {_fallback_vol:,.0f}"
                                )
                                _raw_vol = _fallback_vol
                                # 퍼널: volume_state=missing 누적
                                _fv = self._funnel.get(market, {})
                                _fv.setdefault("volume_states", {})
                                _fv["volume_states"]["missing"] = _fv["volume_states"].get("missing", 0) + 1
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
                                + (" [vol_fallback]" if _vol_missing else "")
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
                _intraday_log_row_id = 0

                def _ca(p):
                    return apply_cross_asset_adjust(p, _ca_context, market, ticker, mode)

                def _ap(strat: str) -> dict:
                    """adaptive_params + cross-asset + Claude 검토 통합 헬퍼."""
                    # param_tuner 캐시 우선 (캐시 없으면 adaptive_params 기본)
                    _pt_cache = _param_tuner._cache.get(market, {})
                    if strat in _pt_cache and not _pt_cache[strat].get("disabled"):
                        p = _ca(dict(_pt_cache[strat]))
                    else:
                        p = _ca(_adaptive_params(strat, market, mode, _avg_conf,
                                                context=_ca_context))
                    # 장초반 opening window 판단용 — gap_pullback.signal()이 사용
                    p["session_elapsed_min"] = self._market_elapsed_min(market)
                    if strat == "opening_range_pullback":
                        _or_high = float(self._or_high.get(ticker, 0.0) or 0.0)
                        _or_low_raw = self._or_low.get(ticker, float("inf"))
                        _or_low = 0.0 if _or_low_raw == float("inf") else float(_or_low_raw or 0.0)
                        p["or_high"] = _or_high
                        p["or_low"] = _or_low
                        p["or_formed"] = bool(self._or_formed.get(ticker, False))
                    return p

                def _orp_detail(_df, _i, _p):
                    if _p.get("disabled"):
                        return f"OR눌림: 전략 비활성({market} {mode})"
                    _elapsed = float(_p.get("session_elapsed_min", 999) or 999)
                    _or_minutes = float(_p.get("or_minutes", 10) or 10)
                    _entry_window = float(_p.get("entry_window_min", 60) or 60)
                    _or_formed = bool(_p.get("or_formed", False))
                    _or_high = float(_p.get("or_high", 0.0) or 0.0)
                    _or_low = float(_p.get("or_low", 0.0) or 0.0)
                    if not _or_formed:
                        if _elapsed <= _or_minutes:
                            return f"OR눌림: OR 형성중({int(_elapsed)}분/{int(_or_minutes)}분)"
                        return "OR눌림: OR 미형성"
                    if _elapsed > (_or_minutes + _entry_window):
                        return f"OR눌림: 진입창 종료({int(_elapsed)}분)"
                    _or_range_pct = ((_or_high - _or_low) / _or_low) if _or_low > 0 else 0.0
                    _or_min = float(_p.get("or_min_range_pct", 0.003) or 0.003)
                    _or_max = float(_p.get("or_max_range_pct", 0.030) or 0.030)
                    _row = _df.iloc[_i]
                    _close = float(_row.get("close", 0) or 0)
                    _vol_avg = float(_row.get("vol_avg20", 0) or 0)
                    _vol_ratio = float(_row.get("volume", 0) or 0) / _vol_avg if _vol_avg else 0.0
                    _vol_mult = float(_p.get("vol_mult", 1.3) or 1.3)
                    _pb_min = float(_p.get("pullback_min_pct", 0.002) or 0.002)
                    _pb_max = float(_p.get("pullback_max_pct", 0.010) or 0.010)
                    _upper = _or_high * (1.0 - _pb_min)
                    _lower = _or_high * (1.0 - _pb_max)
                    return (
                        f"OR눌림: OR={_or_range_pct*100:.2f}%"
                        f"(허용 {_or_min*100:.1f}~{_or_max*100:.1f}%){'✓' if _or_min <= _or_range_pct <= _or_max else '✗'}"
                        f" 구간={_lower:.2f}~{_upper:.2f} 현재={_close:.2f}{'✓' if _lower <= _close <= _upper else '✗'}"
                        f" vol={_vol_ratio:.2f}(목표>{_vol_mult:.1f}){'✓' if _vol_ratio > _vol_mult else '✗'}"
                    )

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

                def _classify_rejection(detail: str, volume_missing_detected: bool = False) -> tuple[str, str]:
                    """
                    none_detail 문자열에서 rejection_reason / volume_state 분류.
                    Returns: (rejection_reason, volume_state)
                    - rejection_reason: or_forming | momentum_wait | data_missing | gap_insufficient
                                        | volume_low | pullback_missing | highpoint_missing
                                        | strategy_disabled | signal_not_met
                    - volume_state: missing | low | ok | unknown

                    우선순위 (높은 순):
                      1. or_forming        — OR 아직 형성 중 (시간 문제, 대기 필요)
                      2. momentum_wait     — momentum 시간 게이트 미통과 (정상 대기)
                      3. data_missing      — vol=0/1 등 데이터 누락
                      4. gap_insufficient  — 갭 조건 미충족
                      5. volume_low        — 거래량 부족
                      6. pullback_missing  — 눌림 조건 미충족
                      7. highpoint_missing — 신고가/추세 부족
                      8. strategy_disabled — VB 등 전략 비활성 (최하위 — 항상 함께 발생)
                      9. signal_not_met    — 기타 미충족
                    """
                    import re as _re
                    reasons = []
                    vol_state = "unknown"

                    # 1. OR 형성중 → 아직 OR 미완성 (시간 게이트, 가장 명확한 원인)
                    if "OR 형성중" in detail:
                        reasons.append("or_forming")

                    # 2. momentum 시간 게이트 대기 중
                    if "momentum_wait_window" in detail:
                        reasons.append("momentum_wait")

                    # 3. vol 데이터 누락 (외부 주입 플래그 우선)
                    if volume_missing_detected:
                        vol_state = "missing"
                        reasons.append("data_missing")

                    # 거래량 분석 — 갭눌림/OR눌림/변돌 공통 패턴 + 연속진입 전용 패턴 모두 스캔
                    # 전략 하나라도 vol=✗이면 volume_low, 전략 vol=0/1이면 data_missing
                    _vol_fail = False   # 어느 전략이든 vol 미달
                    _vol_found = False  # 어느 전략이든 vol 패턴 발견
                    # 표준 형식: vol=ratio(목표>X)✓/✗  (갭눌림/OR눌림/변돌)
                    for _vpat in [
                        r"갭눌림[^|]*?vol=([0-9.]+)\(목표>[0-9.]+\)(✓|✗)",
                        r"OR눌림[^|]*?vol=([0-9.]+)\(목표>[0-9.]+\)(✓|✗)",
                        r"변돌[^|]*?vol=([0-9.]+)\(목표>[0-9.]+\)(✓|✗)",
                    ]:
                        _vm = _re.search(_vpat, detail)
                        if not _vm:
                            continue
                        _vv = float(_vm.group(1))
                        _vok = _vm.group(2) == "✓"
                        _vol_found = True
                        if not volume_missing_detected and (_vv == 0.0 or _vv == 1.0):
                            vol_state = "missing"
                            if "data_missing" not in reasons:
                                reasons.append("data_missing")
                            break  # data_missing이 확정되면 더 볼 필요 없음
                        if not _vok:
                            _vol_fail = True
                    # 연속진입 전용 형식: vol=✓/✗(ratio)
                    _cvm = _re.search(r"연속진입[^|]*?vol=(✓|✗)\(([0-9.]+)\)", detail)
                    if _cvm:
                        _cv_ok    = _cvm.group(1) == "✓"
                        _cv_ratio = float(_cvm.group(2))
                        _vol_found = True
                        if not volume_missing_detected and (_cv_ratio == 0.0 or _cv_ratio == 1.0):
                            vol_state = "missing"
                            if "data_missing" not in reasons:
                                reasons.append("data_missing")
                        elif not _cv_ok:
                            _vol_fail = True
                    # 종합 판정 — volume_missing_detected=True면 이미 처리됨
                    if not volume_missing_detected and vol_state != "missing":
                        if _vol_fail:
                            vol_state = "low"
                            if "volume_low" not in reasons:
                                reasons.append("volume_low")  # 5.
                        elif _vol_found:
                            vol_state = "ok"

                    # 4. 갭 조건 미충족
                    _gap_m = _re.search(r"갭=([0-9.-]+)%\(목표>[0-9.]+%\)(✗)", detail)
                    if _gap_m:
                        reasons.append("gap_insufficient")

                    # 6. 눌림 조건 미충족
                    if "눌림=✗" in detail:
                        reasons.append("pullback_missing")

                    # 7. 신고가/추세 부족
                    if "신고가부족" in detail:
                        reasons.append("highpoint_missing")

                    # 8. 전략 비활성 (VB 등 — 최하위, 항상 none_detail에 포함될 수 있음)
                    if "전략 비활성" in detail:
                        reasons.append("strategy_disabled")

                    rejection = reasons[0] if reasons else "signal_not_met"
                    return rejection, vol_state

                def _log_or_probe(_p, _blocked_reason=""):
                    try:
                        _row = sig_df.iloc[i]
                        _or_high = float(_p.get("or_high", 0.0) or 0.0)
                        _or_low = float(_p.get("or_low", 0.0) or 0.0)
                        _or_range_pct = ((_or_high - _or_low) / _or_low) if _or_low > 0 else None
                        _close = float(_row.get("close", 0) or 0)
                        _pullback_depth = ((_or_high - _close) / _or_high) if _or_high > 0 else None
                        _vol_avg = float(_row.get("vol_avg20", 0) or 0)
                        _vol_ratio = float(_row.get("volume", 0) or 0) / _vol_avg if _vol_avg else 0.0
                        _day_high = float(price_info.get("high", 0) or 0)
                        _from_high_pct = ((price - _day_high) / _day_high * 100.0) if _day_high > 0 else None
                        return isdb.insert_probe(
                            session_date=_market_session_date(market).isoformat(),
                            market=market,
                            ticker=ticker,
                            strategy_name="opening_range_pullback",
                            or_formed=1 if _p.get("or_formed") else 0,
                            or_high=_or_high or None,
                            or_low=_or_low or None,
                            or_range_pct=_or_range_pct,
                            pullback_depth_pct=_pullback_depth,
                            entry_window_elapsed_min=max(
                                float(_p.get("session_elapsed_min", 0) or 0) - float(_p.get("or_minutes", 0) or 0),
                                0.0,
                            ),
                            price=price,
                            volume=float(_row.get("volume", 0) or 0),
                            vol_ratio=_vol_ratio,
                            from_high_pct=_from_high_pct,
                            blocked_reason=_blocked_reason or None,
                        )
                    except Exception:
                        return 0

                if market == "KR":
                    orp_p = _ap("opening_range_pullback")
                    _intraday_log_row_id = _log_or_probe(orp_p)
                    if orp_sig(sig_df, i, orp_p):
                        signal_fired = True
                        strategy_name = "opening_range_pullback"
                        params = orp_p
                    else:
                        gap_p = _ap("gap_pullback")
                        if gap_sig(sig_df, i, gap_p):
                            signal_fired = True
                            strategy_name = "gap_pullback"
                            params = gap_p
                        else:
                            mom_p = _ap("momentum")
                            _cont_window_max = float(_ap("continuation").get("cont_entry_max_min", 45) or 45)
                            _momentum_ready = self._market_elapsed_min(market) >= _cont_window_max
                            if _momentum_ready:
                                kr_momentum_diag = mom_diag(sig_df, i, mom_p)
                            if _momentum_ready and mom_sig(sig_df, i, mom_p):
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
                    # continuation entry — 모든 전략 실패 후 개장 초반 갭 연속 상승 시
                    if not signal_fired and not self._continuation_used.get(ticker, False):
                        _cont_p = _ap("continuation")
                        if cont_sig(sig_df, i, _cont_p):
                            signal_fired = True
                            strategy_name = "continuation"
                            params = _cont_p
                            self._continuation_used[ticker] = True
                            log.info(
                                f"[continuation entry {market}] {ticker} "
                                f"elapsed={self._market_elapsed_min(market):.1f}min "
                                f"size_mult={_cont_p.get('size_mult', 0.5)}"
                            )
                    if not signal_fired:
                        orp_p = locals().get("orp_p") or _ap("opening_range_pullback")
                        gap_p = locals().get("gap_p") or _ap("gap_pullback")
                        mom_p = locals().get("mom_p") or _ap("momentum")
                        mr_p = locals().get("mr_p") or _ap("mean_reversion")
                        vb_p = locals().get("vb_p") or _ap("volatility_breakout")
                        _cont_p = locals().get("_cont_p") or _ap("continuation")
                        _kr_md = locals().get("kr_momentum_diag") or {}
                        _cont_window_max = float(_cont_p.get("cont_entry_max_min", 45) or 45)
                        _elapsed_min = self._market_elapsed_min(market)
                        _kr_mom_str = (
                            f"모멘텀: 전략 비활성({market} {mode})"
                            if mom_p.get("disabled") else
                            f"모멘텀: 추세{'충족' if _kr_md.get('ma_ok') else '부족'}"
                            f", MACD{'충족' if _kr_md.get('macd_ok') else '부족'}"
                            f", 거래량{'충족' if _kr_md.get('vol_ok') else '부족'}"
                            f", 신고가{'충족' if _kr_md.get('high_ok') else '부족'}"
                        )
                        _cont_d = cont_diag(sig_df, i, _cont_p)
                        if not mom_p.get("disabled") and _elapsed_min < _cont_window_max:
                            _kr_mom_str = f"momentum_wait_window({_elapsed_min:.0f}m<{_cont_window_max:.0f}m)"
                        _cont_str = (
                            f"연속진입: 비활성({market} {mode})"
                            if _cont_p.get("disabled") else
                            f"연속진입: 창={'✓' if _cont_d.get('in_window') else '✗'}({_cont_d.get('elapsed_min', 0):.0f}분)"
                            f" 갭={'✓' if _cont_d.get('gap_ok') else '✗'}({_cont_d.get('gap', 0):.2f}%≥{_cont_d.get('gap_min', 0):.1f}%)"
                            f" vol={'✓' if _cont_d.get('vol_ok') else '✗'}({_cont_d.get('vol_ratio', 0):.2f})"
                            f" cont={'✓' if _cont_d.get('cont_ok') else '✗'}({_cont_d.get('cont_ratio', 0):.2f}%)"
                            f" 과열={'✓' if _cont_d.get('overheat_ok') else '✗'}"
                            + (" [1회사용]" if self._continuation_used.get(ticker, False) else "")
                        )
                        none_detail = " | ".join([
                            str(_orp_detail(sig_df, i, orp_p)),
                            str(_gap_detail(sig_df, i, gap_p)),
                            str(_kr_mom_str),
                            str(_mr_detail(sig_df, i, mr_p)),
                            str(_vb_detail(sig_df, i, vb_p)),
                            str(_cont_str),
                        ])
                else:
                    _us_orp_p = _ap("opening_range_pullback")
                    _intraday_log_row_id = _log_or_probe(_us_orp_p)
                    # US: 분석가 투표 전략 우선, volatility_breakout 폴백
                    _strat_dispatch = {
                        "opening_range_pullback": (orp_sig, _us_orp_p),
                        "momentum":           (mom_sig,  _ap("momentum")),
                        "mean_reversion":     (mr_sig,   _ap("mean_reversion")),
                        "gap_pullback":       (gap_sig,  _ap("gap_pullback")),
                        "volatility_breakout":(vb_sig,   _ap("volatility_breakout")),
                    }
                    for _strat_name in _us_strat_list:
                        _sig_fn, _p = _strat_dispatch.get(
                            _strat_name, (vb_sig, _ap("volatility_breakout"))
                        )
                        if (
                            _strat_name == "momentum"
                            and self._market_elapsed_min(market)
                            < float(_ap("continuation").get("cont_entry_max_min", 45) or 45)
                        ):
                            continue
                        if _sig_fn(sig_df, i, _p):
                            signal_fired = True
                            strategy_name = _strat_name
                            params = _p
                            break
                    # US continuation entry — 모든 전략 실패 후 개장 초반 갭 연속 상승 시
                    if not signal_fired and not self._continuation_used.get(ticker, False):
                        _us_cont_p = _ap("continuation")
                        if cont_sig(sig_df, i, _us_cont_p):
                            signal_fired = True
                            strategy_name = "continuation"
                            params = _us_cont_p
                            self._continuation_used[ticker] = True
                            log.info(
                                f"[continuation entry {market}] {ticker} "
                                f"elapsed={self._market_elapsed_min(market):.1f}min "
                                f"size_mult={_us_cont_p.get('size_mult', 0.5)}"
                            )

                # 활성도 추적 — 시장별 인라인 교체 기준으로 활용
                _scan_interval_min = self._entry_scan_interval_sec(market) / 60.0
                if signal_fired:
                    self._ticker_no_signal_cycles[ticker] = 0
                    self._ticker_no_signal_minutes[ticker] = 0.0
                    if strategy_name == "opening_range_pullback":
                        isdb.update_signal(_intraday_log_row_id)
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
                                watchlist_change_alert(
                                    market,
                                    [ticker],
                                    [_new],
                                    reason=(
                                        '%d분' % round(_prev_min + _scan_interval_min)
                                        if market == 'KR'
                                        else '%d사이클' % (_prev_cnt + 1)
                                    ) + " 무신호",
                                )

                if not signal_fired:
                    def _cont_detail_str(_p, _ticker):
                        _d = cont_diag(sig_df, i, _p)
                        if _p.get("disabled"):
                            return f"연속진입: 비활성({market} {mode})"
                        return (
                            f"연속진입: 창={'✓' if _d.get('in_window') else '✗'}({_d.get('elapsed_min', 0):.0f}분)"
                            f" 갭={'✓' if _d.get('gap_ok') else '✗'}({_d.get('gap', 0):.2f}%≥{_d.get('gap_min', 0):.1f}%)"
                            f" vol={'✓' if _d.get('vol_ok') else '✗'}({_d.get('vol_ratio', 0):.2f})"
                            f" cont={'✓' if _d.get('cont_ok') else '✗'}({_d.get('cont_ratio', 0):.2f}%)"
                            f" 과열={'✓' if _d.get('overheat_ok') else '✗'}"
                            + (" [1회사용]" if self._continuation_used.get(_ticker, False) else "")
                        )
                    if market == "KR":
                        orp_p = _ap("opening_range_pullback")
                        gap_p = _ap("gap_pullback")
                        mr_p = _ap("mean_reversion")
                        vb_p = _ap("volatility_breakout")
                        mom_p = _ap("momentum")
                        _cont_p2 = _ap("continuation")
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
                        # KR momentum 시간 게이트 — 첫 번째 블록과 동일하게 덮어씌움
                        _kr_elapsed_nd = self._market_elapsed_min(market)
                        _kr_cont_win_nd = float(_cont_p2.get("cont_entry_max_min", 45) or 45)
                        if not mom_p.get("disabled") and _kr_elapsed_nd < _kr_cont_win_nd:
                            _mom_detail = f"momentum_wait_window({_kr_elapsed_nd:.0f}m<{_kr_cont_win_nd:.0f}m)"
                        none_detail = " | ".join([
                            str(_orp_detail(sig_df, i, orp_p)),
                            str(_gap_detail(sig_df, i, gap_p)),
                            str(_mom_detail),
                            str(_mr_detail(sig_df, i, mr_p)),
                            str(_vb_detail(sig_df, i, vb_p)),
                            str(_cont_detail_str(_cont_p2, ticker)),
                        ])
                    else:
                        _us_orp_p = _ap("opening_range_pullback")
                        _us_mom_p = _ap("momentum")
                        _us_mom_diag = mom_diag(sig_df, i, _us_mom_p)
                        _us_mr_p = _ap("mean_reversion")
                        _us_gap_p = _ap("gap_pullback")
                        _us_vb_p = _ap("volatility_breakout")
                        _us_cont_p2 = locals().get("_us_cont_p") or _ap("continuation")
                        _us_cont_window_max2 = float(_us_cont_p2.get("cont_entry_max_min", 45) or 45)
                        _us_elapsed_min2 = self._market_elapsed_min(market)
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
                        if not _us_mom_p.get("disabled") and _us_elapsed_min2 < _us_cont_window_max2:
                            _us_mom_detail = f"momentum_wait_window({_us_elapsed_min2:.0f}m<{_us_cont_window_max2:.0f}m)"
                        none_detail = " | ".join([
                            str(_orp_detail(sig_df, i, _us_orp_p)),
                            str(_vb_detail(sig_df, i, _us_vb_p)),
                            str(_us_mom_detail),
                            str(_mr_detail(sig_df, i, _us_mr_p)),
                            str(_gap_detail(sig_df, i, _us_gap_p)),
                            str(_cont_detail_str(_us_cont_p2, ticker)),
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

                    _rej_reason, _vol_state = _classify_rejection(
                        none_detail,
                        volume_missing_detected=_vol_missing,
                    )
                    # 퍼널: rejection_reason / volume_state 누적
                    _f = self._funnel[market]
                    _f["rejection_reasons"][_rej_reason] = _f["rejection_reasons"].get(_rej_reason, 0) + 1
                    if _vol_state != "unknown":
                        _f["volume_states"][_vol_state] = _f["volume_states"].get(_vol_state, 0) + 1
                    analysis_log.info(
                        f"[signal {market}] {ticker} none | {none_detail}",
                        extra={"extra": {
                            "event": "signal_check",
                            "market": market,
                            "ticker": ticker,
                            "signal": "none",
                            "reason": "no_signal",
                            "rejection_reason": _rej_reason,
                            "volume_state": _vol_state,
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
                    if _intraday_log_row_id:
                        isdb.update_outcome(_intraday_log_row_id, 0.0, note=none_detail[:500])
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

                # KR momentum: ATR%가 과도하면 진입 자체를 차단한다.
                # 손절 폭을 넓히는 것만으로는 갭다운/급락 리스크를 제어하기 어렵기 때문.
                _kr_mom_atr_cap = float(os.getenv("KR_MOMENTUM_ATR_PCT_MAX", "0.06") or 0.06)
                if (
                    market == "KR"
                    and strategy_name == "momentum"
                    and atr_pct is not None
                    and _kr_mom_atr_cap > 0
                    and atr_pct > _kr_mom_atr_cap
                ):
                    log.info(
                        f"  [{ticker}] KR momentum ATR 차단: "
                        f"atr_pct={atr_pct:.2%} > {_kr_mom_atr_cap:.2%}"
                    )
                    analysis_log.info(
                        f"[skip {market}] {ticker} momentum_atr_too_high",
                        extra={"extra": {
                            "event": "entry_skip",
                            "market": market,
                            "ticker": ticker,
                            "reason": "momentum_atr_too_high",
                            "strategy": strategy_name,
                            "price": float(price),
                            "atr_pct": round(float(atr_pct), 4),
                            "atr_cap": round(float(_kr_mom_atr_cap), 4),
                            "mode": mode,
                        }},
                    )
                    if _ML_DB_ENABLED:
                        _ml_write_eval(
                            ticker, price, sig_df.iloc[i].to_dict(), "SKIPPED",
                            block_reason_="momentum_atr_too_high",
                            diag_json_={"atr_pct": float(atr_pct), "atr_cap": float(_kr_mom_atr_cap)},
                        )
                    continue
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
                    "intraday_log_row_id": _intraday_log_row_id,
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
                _isdb_id = int(_sig.get("intraday_log_row_id", 0) or 0)
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
                    # 퍼널: signaled
                    self._funnel[market]["signaled"] += 1
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
                        # 연속 오류 카운터 증가 → 3회 이상이면 당일 차단
                        _err_cnt = self._order_error_count.get(_s_tk, 0) + 1
                        self._order_error_count[_s_tk] = _err_cnt
                        if _err_cnt >= 3:
                            _blk_reason = f"연속오류 {_err_cnt}회: {e}"
                            self._mark_us_order_blocked(_s_tk, _blk_reason)
                            log.warning(f"[주문오류차단 {market}] {_s_tk} {_err_cnt}회 연속 → 당일 차단: {e}")
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
                        _reason = result.get("msg", "") or "주문거절"
                        # 매매불가 종목 → 세션 내 재시도 차단 (KR/US 공통)
                        if "매매불가" in _reason or "주문처리가 안되었습니다" in _reason:
                            self._mark_us_order_blocked(_s_tk, _reason)
                            log.warning(f"[매매불가] {_s_tk} 세션 내 진입 차단: {_reason}")
                        elif market == "US" and self.is_paper:
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
                    # 주문 성공 시 연속 오류 카운터 초기화
                    self._order_error_count.pop(_s_tk, None)
                    log.info(
                        f"[{'PAPER' if self.is_paper else 'LIVE'} BUY] "
                        f"{_s_tk} {qty}@{_s_px:,} | {_s_strat} | "
                        f"주문번호={result.get('order_no','')} | score={_s_ep:.3f}"
                    )
                    # 퍼널: ordered
                    self._funnel[market]["ordered"] += 1
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
                    if _isdb_id and _s_strat == "opening_range_pullback":
                        isdb.update_trade(_isdb_id)
                    self._add_pending_order({
                        "order_no":       result.get("order_no", ""),
                        "ticker":         _s_tk,
                        "name":           str(_s_row.get("name", "") or "").strip() or self._lookup_ticker_name(_s_tk, market),
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
                    buy_order_alert(
                        market=market,
                        ticker=_s_tk,
                        qty=qty,
                        order_no=str(result.get("order_no", "") or ""),
                        detail="체결 확인 후 보유 포지션에 반영됩니다.",
                        name=str(_s_row.get("name", "") or "").strip(),
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
                        self.risk.update_prices(self.price_cache, self.price_cache_raw)
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
                            "name":          str(_t2_price_info.get("name", "") or "").strip() or self._lookup_ticker_name(_t2_ticker, "US"),
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
                        buy_order_alert(
                            market="US",
                            ticker=_t2_ticker,
                            qty=_t2_qty,
                            detail=f"{_play['etf']} {_play['etf_chg']:+.2f}% 섹터강세 | conf={_play['confidence']:.2f} | {_play['reason']}",
                            name=str(_t2_price_info.get("name", "") or "").strip(),
                        )
                    except Exception as _t2_e:
                        log.error(f"[Tier2] {_t2_ticker} 오류: {_t2_e}")
            except Exception as _t2_loop_e:
                log.error(f"[Tier2 섹터플레이] 루프 오류: {_t2_loop_e}")
        # ── KR Tier 2 섹터 플레이 ─────────────────────────────────────────────
        if market == "KR" and mode not in ("HALT", "DEFENSIVE", "CAUTIOUS_BEAR"):
            try:
                _digest_raw    = self.today_judgment.get("digest_raw", {})
                _kr_sectors    = (_digest_raw.get("context") or {}).get("kr_sectors", {})
                _digest_summary = self.today_judgment.get("digest_prompt", "")[:300]
                _kr_t2_plays   = run_kr_sector_plays(
                    kr_sectors=_kr_sectors,
                    market_mode=mode,
                    digest_summary=_digest_summary,
                    max_plays=2,
                )
                for _play in _kr_t2_plays:
                    _t2_ticker = _play["ticker"]
                    try:
                        if any(p["ticker"] == _t2_ticker for p in self.risk.positions):
                            log.debug(f"  [KR Tier2] {_t2_ticker} 이미 포지션 보유 — 스킵")
                            continue
                        if self._is_entry_blocked(_t2_ticker):
                            log.debug(f"  [KR Tier2] {_t2_ticker} 진입 쿨다운 — 스킵")
                            continue
                        _t2_price_info = get_price(_t2_ticker, self.token, market="KR")
                        if not _t2_price_info:
                            continue
                        _t2_risk_price = int(_t2_price_info.get("price", 0) or 0)
                        if _t2_risk_price <= 0:
                            continue
                        self.price_cache[_t2_ticker] = _t2_risk_price
                        self.risk.update_prices(self.price_cache, self.price_cache_raw)
                        _t2_size = max(10, int(size_pct * TIER2_SIZE_RATIO))
                        _t2_sl, _t2_tp = 0.025, 0.05
                        _t2_qty = self.risk.calc_order_size(
                            _t2_risk_price, _t2_size, _t2_sl
                        )
                        if _t2_qty <= 0:
                            continue
                        _t2_result = place_order(
                            _t2_ticker, _t2_qty, 0, "buy",
                            self.token, market="KR",
                        )
                        if not _t2_result["success"]:
                            log.error(f"  [KR Tier2] {_t2_ticker} 주문실패: {_t2_result.get('msg')}")
                            continue
                        log.info(
                            f"[KR Tier2 BUY] {_t2_ticker} {_t2_qty}주 @{_t2_risk_price:,}원 "
                            f"| ETF={_play['etf']} {_play['etf_chg']:+.2f}% "
                            f"| conf={_play['confidence']:.2f} | {_play['reason']}"
                        )
                        self._record_decision_event(
                            "KR", "buy_order", _t2_ticker,
                            strategy="kr_sector_play",
                            qty=_t2_qty,
                            price_native=float(_t2_risk_price),
                            price_krw=_t2_risk_price,
                            reason=f"KR Tier2 섹터플레이 [{_play['etf']} {_play['etf_chg']:+.2f}%]",
                            detail=_play["reason"],
                            order_no=_t2_result.get("order_no", ""),
                            selected_reason=f"kr_sector_etf={_play['etf']}",
                        )
                        self._add_pending_order({
                            "ticker":        _t2_ticker,
                            "side":          "buy",
                            "qty":           _t2_qty,
                            "market":        "KR",
                            "raw_price":     float(_t2_risk_price),
                            "risk_price_krw": float(_t2_risk_price),
                            "strategy":      "kr_sector_play",
                            "tp_pct":        float(_t2_tp),
                            "sl_pct":        float(_t2_sl),
                            "max_hold":      1,
                            "created_at":    datetime.now(KST).isoformat(),
                        })
                        self._block_entry(_t2_ticker, _BUY_COOLDOWN_MIN, "buy_placed")
                        buy_order_alert(
                            market="KR",
                            ticker=_t2_ticker,
                            qty=_t2_qty,
                            detail=f"{_play['etf']} {_play['etf_chg']:+.2f}% 섹터강세 | conf={_play['confidence']:.2f} | {_play['reason']}",
                            name=str(_t2_price_info.get("name", "") or "").strip(),
                        )
                    except Exception as _t2_e:
                        log.error(f"[KR Tier2] {_t2_ticker} 오류: {_t2_e}")
            except Exception as _t2_loop_e:
                log.error(f"[KR Tier2 섹터플레이] 루프 오류: {_t2_loop_e}")
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
            # 모드 변경 시 Claude 파라미터 재검토
            if old_mode != new_mode:
                try:
                    self._run_param_review(
                        market,
                        trigger="reverse" if action == "REVERSE" else "mode_change",
                    )
                except Exception as _pt_e:
                    log.warning(f"[param_tuner mode_change] {market}: {_pt_e}")
            # 모드 변경 시 종목 재선택 (단, REVERSE는 _reinvoke에서 처리)
            if old_mode != new_mode and action != "REVERSE":
                try:
                    log.info(f"[튜너 종목갱신] {old_mode}→{new_mode} 모드 변경 → 종목 재선택")
                    if market == "KR":
                        tune_cands = screen_market_kr(self.token, mode=self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
                    else:
                        tune_cands = screen_market_us(mode=self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
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

            # REVERSE: Claude가 장세 반전 판단 → 보유 포지션 청산
            # broker_sync 전략은 hold_advisor가 개별 판단하므로 REVERSE 대상에서 제외
            if action == "REVERSE" and self.risk.positions:
                reverse_targets = [p for p in self.risk.positions if p.get("strategy") != "broker_sync"]
                skipped = [p["ticker"] for p in self.risk.positions if p.get("strategy") == "broker_sync"]
                if skipped:
                    log.info(f"[REVERSE] broker_sync 포지션 제외 (hold_advisor 관할): {skipped}")
                log.warning(f"[REVERSE] 튜너 판단: {result.get('reason','')} — {len(reverse_targets)}개 포지션 청산")
                for pos in reverse_targets:
                    cp = self.price_cache.get(pos["ticker"], pos["current_price"])
                    if not self.is_paper:
                        place_order(pos["ticker"], pos["qty"], 0, "sell",
                                    self.token, market=self._ticker_market(pos["ticker"]))
                    ex = self.risk.close_position(pos["ticker"], cp, "tuner_reverse")
                    if ex:
                        ex_name = str(ex.get("name", "") or "").strip() or self._lookup_ticker_name(ex["ticker"], market)
                        pnl_alert(ex["ticker"], ex["pnl_pct"], int(ex["pnl"]), "tuner_reverse", market=market, name=ex_name, usd_krw=self.usd_krw_rate)
                        trade_alert("sell", ex["ticker"], ex["qty"], int(cp),
                                    ex["strategy"], 0, 0, reason="tuner_reverse", market=market, name=ex_name, usd_krw=self.usd_krw_rate)
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
                new_cands = screen_market_kr(self.token, mode=self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
            else:
                new_cands = screen_market_us(mode=self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
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

        # 부분교체 후 param_tuner 캐시 갱신 (rescreen 트리거)
        try:
            self._run_param_review(market, trigger="rescreen")
        except Exception as _pt_e:
            log.debug("[param_tuner rescreen] %s: %s", market, _pt_e)

        out_str = ", ".join(replace_out)
        in_str  = ", ".join(replace_in)
        log.info(f"[부분교체] {market}: [{out_str}] → [{in_str}] (무신호 지속)")
        watchlist_change_alert(market, replace_out, replace_in, reason="무신호 지속")

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
                        reinvoke_cands = screen_market_kr(self.token, mode=self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
                    else:
                        reinvoke_cands = screen_market_us(mode=self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
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

    # ── Claude 튜닝용 결정 이벤트 타입 ──────────────────────────────────────
    # entry      : 매수 신호 발생 (Claude 선택 종목)
    # closed     : 포지션 종료 요약 (진입~청산 전체)
    # hold_outcome: hold_advisor 판단 결과 (결과 확정 시)
    # 실행 오류(sell_failed 등)는 decisions.jsonl에 기록하지 않음
    _DECISION_TYPES = {"entry", "closed", "hold_outcome"}

    def _record_decision_event(self, market: str, action: str, ticker: str, **kwargs):
        # ── 실행 오류/상태 이벤트는 decisions.jsonl 제외 ───────────────────
        # sell_failed, buy_blocked 등은 Claude 판단이 아니라 시스템 실행 이벤트
        _SKIP_ACTIONS = {
            "sell_failed", "buy_blocked", "buy_skipped",
            "halt", "cooldown", "no_cash",
        }
        # in-memory 로그는 모든 이벤트 유지 (모니터링용)
        mode = self.today_judgment.get("consensus", {}).get("mode", "")
        event = {
            "timestamp": datetime.now(KST).isoformat(timespec="seconds"),
            "market": market,
            "action": action,
            "ticker": ticker,
            "mode": mode,
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

        # ── Claude 판단 이벤트만 decisions.jsonl 기록 ───────────────────────
        if action not in _SKIP_ACTIONS:
            # buy_order → entry 타입으로 정규화
            # sell_filled / sell_executed → closed 타입으로 정규화
            _type_map = {
                "buy_order":      "entry",
                "buy_signal":     "entry",
                "sell_filled":    "closed",
                "sell_executed":  "closed",
                "HOLD_REVIEW":    "hold_outcome",
            }
            record = {
                "type":      _type_map.get(action, action),
                "timestamp": event["timestamp"],
                "market":    market,
                "ticker":    ticker,
                "mode":      mode,
                "strategy":  event["strategy"],
            }
            # entry: 진입 컨텍스트
            if action in ("buy_order", "buy_signal"):
                record.update({
                    "entry_price": event["price_krw"] or event["price_native"],
                    "qty":         event["qty"],
                    "selected_reason": event["selected_reason"],
                })
            # closed: 종료 요약
            elif action in ("sell_filled", "sell_executed"):
                record.update({
                    "qty":         event["qty"],
                    "order_no":    event["order_no"],
                    "exit_price":  event["price_krw"],
                    "exit_price_native": event["price_native"],
                    "exit_reason": event["reason"],
                    "pnl_pct":     event["pnl_pct"],
                    "pnl_krw":     event["pnl_krw"],
                    "held_days":   kwargs.get("hold_days", 0),
                })
            # hold_outcome: 판단 결과
            elif action == "HOLD_REVIEW":
                record.update({
                    "hold_action":  kwargs.get("hold_action", ""),
                    "trail_pct":    kwargs.get("trail_pct", 0),
                    "votes":        kwargs.get("votes", {}),
                    "source":       kwargs.get("source", ""),
                })
            try:
                with open(DECISIONS_FILE, "a", encoding="utf-8") as _df:
                    _df.write(json.dumps(record, ensure_ascii=False) + "\n")
            except Exception as _de:
                log.debug(f"decisions.jsonl 기록 실패: {_de}")

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
            equity_ctx = self._equity_reference_context()
            broker_state = self._broker_state.get(market, {})
            kis_total_equity = round(float(equity_ctx.get("total_krw", 0) or 0), 0)
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
                # selected_reason: pending_order 또는 포지션 자체에서 복구
                sel_reason = str(pos.get("selected_reason", "") or "").strip()
                if not sel_reason:
                    for pord in self.pending_orders:
                        if pord.get("ticker") == ticker:
                            sel_reason = str(pord.get("selected_reason", "") or "").strip()
                            break
                hold_adv = pos.get("hold_advice") or None
                dedup_positions[key] = {
                    "ticker":          ticker,
                    "name":            pos_name,
                    "qty":             pos.get("qty", 0),
                    "avg_price":       avg_price,
                    "current_price":   current_price,
                    "pnl_pct":         round(pnl_pct, 4),
                    "strategy":        pos.get("strategy", ""),
                    "trailing":        pos.get("trailing", False),
                    "trail_sl":        round(float(pos.get("trail_sl", 0) or 0), 2),
                    "trail_pct":       round(float(pos.get("trail_pct", 0) or 0) * 100, 1),
                    "tp":              round(float(pos.get("tp", 0) or 0), 2),
                    "tp_price":        round(float(pos.get("tp_price", 0) or 0), 2),
                    "tp_triggered":    bool(pos.get("tp_triggered", False)),
                    "sl":              round(float(pos.get("sl", 0) or 0), 2),
                    "entry_date":      pos.get("entry_date", ""),
                    "held_days":       int(pos.get("held_days", 0) or 0),
                    "selected_reason": sel_reason,
                    "hold_advice":     hold_adv,
                    "price_source":    pos.get("price_source", "runtime"),
                    "currency":        pos.get("display_currency", "KRW"),
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
            path = get_runtime_path("state", f"{self._mode}_live_status_{market}.json")
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
                    "equity_source":  equity_ctx.get("source", ""),
                    "internal_equity": round(float(equity_ctx.get("internal_krw", 0) or 0), 0),
                    "last_trusted_equity": round(float(equity_ctx.get("last_trusted_krw", 0) or 0), 0) if equity_ctx.get("last_trusted_krw") is not None else None,
                    "broker": {
                        "trust_level": broker_state.get("trust_level", "unknown"),
                        "last_ok_at": broker_state.get("last_ok_at", ""),
                        "last_error": broker_state.get("last_error", ""),
                        "last_snapshot": broker_state.get("last_snapshot", {}),
                        "last_trusted_snapshot": broker_state.get("last_trusted_snapshot", {}),
                    },
                    "halt": {
                        "active": bool(self.risk.halted),
                        "reason": self.risk.halt_reason or "",
                    },
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
        _log_flow("info", f"[{market}] session_close")
        if self.current_market != market:
            return

        # 장 마감 직전 마지막으로 브로커 잔고/체결을 동기화해 미체결 오판을 줄인다.
        try:
            broker_kr = self._normalize_broker_balance(get_balance(self.token, market="KR", force_refresh=True), "KR")
            broker_us = self._normalize_broker_balance(get_balance(self.token, market="US", force_refresh=True), "US")
            self._reconcile_pending_orders(broker_kr, broker_us)
        except Exception as e:
            self._flag_execution_issue(market, "session_close_sync_failed")
            _log_risk("warning", f"[{market}] session_close 최종 체결 동기화 실패: {e}")

        self._clear_pending_orders_for_market(market, "session_close")
        self.session_active = False
        self.current_market = None
        if self.ws:
            self.ws.stop()
            self.ws = None

        # ── 퍼널 집계 저장 ────────────────────────────────────────────────────
        try:
            self._flush_funnel(market)
        except Exception as _fe:
            log.warning(f"[퍼널 저장 실패] {market}: {_fe}")

        # ── 포지션 정리: 장 종료 후에는 주문하지 않고, 다음 세션 행동만 결정 ─────
        market_positions = [
            p for p in list(self.risk.positions)
            if self._ticker_market(p.get("ticker", "")) == market
        ]
        day_trades = [p for p in market_positions if p.get("max_hold", 1) <= 1]
        multi_days = [p for p in market_positions if p.get("max_hold", 1) > 1]
        from telegram_reporter import send as _tg_send

        # day_trade도 Claude에게 먼저 물어본 후 다음 세션 행동 결정
        claude_review_lines = [f"🔔 <b>[장마감 포지션 검토] {market}</b>", "━━━━━━━━━━━━━━━━"]
        for pos in day_trades:
            ticker = pos.get("ticker", "")
            cp     = self.price_cache.get(ticker, pos.get("current_price", 0))
            entry  = float(pos.get("display_avg_price", pos.get("entry", 0)) or 0)
            pnl    = (cp / entry - 1) * 100 if entry and cp else 0
            is_us  = market == "US"
            px_fmt = lambda v: f"${v:.2f}" if is_us else f"{v:,.0f}원"

            action = "SELL"  # 기본값: 청산
            reason_txt = ""
            advice = None
            try:
                from minority_report.hold_advisor import ask as advisor_ask
                _d = self.today_judgment.get("digest_prompt", "")
                _ic = self._build_intraday_context(market)
                digest = _d + "\n\n[장중 현재]\n" + _ic if _ic else _d
                advice = advisor_ask(self._advisor_pos(pos, market), market, digest)
                action = advice.get("action", "SELL")
                votes  = advice.get("votes", {})
                for v in votes.values():
                    if v.get("action") == action and v.get("reason"):
                        reason_txt = v["reason"][:100]
                        break
                log.info(f"[장마감 검토] {ticker} Claude → {action} ({reason_txt[:50]})")
            except Exception as e:
                log.warning(f"[장마감 검토] {ticker} hold_advisor 실패 → 기본 청산: {e}")
                action = "SELL"

            action_ko  = "다음 세션 청산 권고" if action == "SELL" else "내일로 이월"
            color_icon = "🔴" if action == "SELL" else "🟡"
            pnl_icon   = "🟢" if pnl >= 0 else "🔴"
            block = (
                f"{pnl_icon} <b>{ticker}</b>  {pnl:+.2f}%  {px_fmt(cp)}\n"
                f"  {color_icon} Claude: <b>{action_ko}</b>"
            )
            if reason_txt:
                block += f"\n     → {reason_txt}"
            claude_review_lines.append(block)

            if action == "SELL":
                for p2 in self.risk.positions:
                    if p2.get("ticker") == ticker:
                        p2["hold_advice"] = advice
                        p2["pending_next_open_sell"] = True
                        p2["pending_next_open_reason"] = reason_txt
                        break
                log.info(f"[장마감 Claude 매도권고] {ticker} {pnl:+.2f}% → 다음 세션 장전/시초가 재검토")
            else:
                # HOLD/TRAIL → 이월: max_hold 하루 연장
                for p2 in self.risk.positions:
                    if p2.get("ticker") == ticker:
                        p2["max_hold"] = max(p2.get("max_hold", 1) + 1, 2)
                        if advice:
                            p2["hold_advice"] = advice
                        p2["pending_next_open_sell"] = False
                        p2["pending_next_open_reason"] = ""
                        break
                multi_days.append(pos)
                log.info(f"[장마감 Claude 이월] {ticker} {pnl:+.2f}% → max_hold 연장")

        if len(claude_review_lines) > 2:
            try:
                _tg_send("\n\n".join(claude_review_lines))
            except Exception:
                pass

        if multi_days:
            log.info(f"[이월] {[p['ticker'] for p in multi_days]} "
                     f"→ 다음 세션 계속 보유 (held_days: "
                     f"{[p['held_days'] for p in multi_days]})")

        # 이월 포지션 파일 저장 (재시작 대비)
        self._save_positions()

        # ── 장 종료 후 이월 포지션 Claude 점검 → 텔레그램 전송 ──────────────
        try:
            self._post_session_position_review(market, multi_days)
        except Exception as _pse:
            log.warning(f"[장후 포지션 리뷰 오류] {market}: {_pse}")

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

        # ── decisions.jsonl 폴백 머지 ─────────────────────────────────────────
        # 재시작으로 trade_log가 비거나 부분만 남을 경우 decisions.jsonl의
        # 당일 closed 거래를 보조 소스로 추가해 매매 원장 누락을 방지한다.
        try:
            _dec_trades = []
            if DECISIONS_FILE.exists():
                for _line in DECISIONS_FILE.read_text(encoding="utf-8").splitlines():
                    try:
                        _rec = json.loads(_line)
                    except Exception:
                        continue
                    if _rec.get("type") != "closed":
                        continue
                    if _rec.get("market") != market:
                        continue
                    _ts = str(_rec.get("timestamp", "") or "")[:10]
                    if _ts != today:
                        continue
                    _ono = str(_rec.get("order_no", "") or "").strip()
                    _key = _ono if _ono else f"dec_{_rec.get('ticker','')}_{_rec.get('timestamp','')}"
                    if _key in _seen_orders:
                        continue
                    _seen_orders.add(_key)
                    _usd_krw = _get_usd_krw_cached()
                    _ep = float(_rec.get("exit_price", 0) or 0)
                    _ep_native = float(_rec.get("exit_price_native", 0) or 0)
                    if market == "US":
                        _ep_disp = _ep_native if _ep_native > 0 else (round(_ep / _usd_krw, 4) if _usd_krw > 0 else 0.0)
                    else:
                        _ep_disp = _ep
                    _dec_trades.append({
                        "ticker":    _rec.get("ticker", ""),
                        "side":      "sell",
                        "qty":       int(_rec.get("qty", 0) or 0),
                        "price":     _ep,
                        "display_price": _ep_disp,
                        "pnl":       float(_rec.get("pnl_krw", 0) or 0),
                        "pnl_pct":   float(_rec.get("pnl_pct", 0) or 0),
                        "strategy":  _rec.get("strategy", ""),
                        "reason":    _rec.get("exit_reason", ""),
                        "order_no":  _ono,
                        "date":      today,
                        "currency":  "USD" if market == "US" else "KRW",
                        "source_kind": "decisions_fallback",
                    })
            if _dec_trades:
                log.info(f"[{market}] decisions.jsonl 폴백 {len(_dec_trades)}건 머지 (trade_log 누락 보완)")
                session_trades = session_trades + _dec_trades
        except Exception as _dm_e:
            log.warning(f"[{market}] decisions.jsonl 머지 실패: {_dm_e}")
        _sell_log = [t for t in session_trades if t.get("side") == "sell"]
        execution_health = self._build_execution_health(market, session_trades)
        cumulative_equity = int(round(self._kis_total_equity_krw()))
        actual = {
            "market_change": get_index_change(market),
            "pnl_pct": self.risk.daily_pnl / max(self.risk.session_start_equity, 1) * 100,
            "pnl_krw": int(self.risk.daily_pnl),
            "win": self.risk.daily_pnl > 0,
            "trades": len(_sell_log),   # 청산(매도) 건수만
            "cumulative": cumulative_equity,
            "execution_contaminated": execution_health["contaminated"],
            "execution_issues": execution_health["reasons"],
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
                "execution_health": execution_health,
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
            "execution_health": execution_health,
            "postmortem":     pm,
            "trades":         session_trades,
            "decision_events": [e for e in self.decision_event_log if e.get("market") == market],
            "session_events": self._session_events,   # 튜닝/긴급재판단 전체 이력
            "mode": "paper" if self.is_paper else "live",
        }
        path = _judgment_runtime_path(self._mode, today, market)
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

        # ── param_tuner outcome 업데이트 ──────────────────────────────────────
        try:
            _pt_ids     = _param_tuner.get_session_ids(market)
            _pt_signals = len([e for e in self._session_events
                               if e.get("type") == "signal"])
            _pt_entries = actual.get("trades", 0)
            _pt_losses  = len(sell_trades) - wins
            _pt_avg_pnl = (
                sum(t.get("pnl_pct", 0) for t in sell_trades) / len(sell_trades)
                if sell_trades else 0.0
            )
            _param_tuner.update_outcomes(
                session_ids=_pt_ids,
                signals=_pt_signals,
                entries=_pt_entries,
                wins=wins,
                losses=_pt_losses,
                avg_pnl_pct=_pt_avg_pnl,
                total_pnl_krw=float(actual.get("pnl_krw", 0)),
                market=market,
                session_date=today,
            )
            _param_tuner.clear_cache(market)
            log.info(f"[param_tuner] {market} outcome 기록 완료 (ids={len(_pt_ids)}개, 소급 포함)")
        except Exception as _pt_e:
            log.warning(f"[param_tuner] outcome 업데이트 오류: {_pt_e}")

        # KR 장중 마지막 스크리닝 결과 → 다음 날 장전 A캐시로 저장
        if market == "KR" and self._last_kr_candidates:
            try:
                save_kr_screen_cache(self._last_kr_candidates)
                log.info(f"[KR 스크리너 캐시] session_close 저장 완료 ({len(self._last_kr_candidates)}종목)")
            except Exception as _e:
                log.warning(f"[KR 스크리너 캐시] session_close 저장 실패: {_e}")


def _in_session_now(market: str) -> bool:
    """현재 KST 시각이 해당 시장 세션 중인지 확인"""
    now = datetime.now(ZoneInfo("Asia/Seoul")).time()
    if market == "KR":
        return dt_time(8, 50) <= now < dt_time(16, 0)
    else:  # US: 22:20 ~ (자정 넘어) 05:00
        return now >= dt_time(22, 20) or now < dt_time(5, 0)


def _check_duplicate_bot(is_paper: bool = True) -> bool:
    """동일 모드 봇이 이미 실행 중이면 True"""
    _mode = "paper" if is_paper else "live"
    _pid_file = get_runtime_path("state", f"{_mode}_trading_bot.pid")
    if not _pid_file.exists():
        return False
    try:
        data = json.loads(_pid_file.read_text(encoding="utf-8"))
        existing_pid = int(data.get("pid", 0))
        if existing_pid and existing_pid != os.getpid():
            if psutil is not None:
                if psutil.pid_exists(existing_pid):
                    return True
            else:
                try:
                    os.kill(existing_pid, 0)
                    return True
                except (ProcessLookupError, PermissionError, OSError):
                    pass
    except Exception:
        pass
    return False


def main(is_paper: bool = True):
    global DECISIONS_FILE
    _mode = "paper" if is_paper else "live"

    if _check_duplicate_bot(is_paper):
        log.error(f"[중복 실행 차단] {_mode} 봇이 이미 실행 중입니다. 기존 프로세스를 먼저 종료하세요.")
        sys.exit(1)

    DECISIONS_FILE = get_runtime_path("state", f"{_mode}_decisions.jsonl")
    _write_bot_pid_file(is_paper)
    atexit.register(_clear_bot_pid_file, is_paper)
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
    system_alert(
        f"봇 시작됨 [{mode_txt}]",
        [
            f"💰 초기자금: {bot.risk.init_cash:,}원 (KR/US 공유)",
            f"📦 1회 최대주문: {int(bot.risk.max_order_krw):,}원",
            "명령어: ? 입력 시 도움말 | /setorder 300000 으로 주문금액 변경",
        ],
        icon="🤖",
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

    # 장 중 1시간 주기 보유 포지션 Claude 점검
    schedule.every(60).minutes.do(bot._intraday_position_review, "KR")
    schedule.every(60).minutes.do(bot._intraday_position_review, "US")

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
        bot.session_open("KR", trigger="startup_mid_session")
    if us_mid_session:
        log.info("[startup] US 세션 진행 중 — session_open 즉시 실행")
        bot.session_open("US", trigger="startup_mid_session")

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
