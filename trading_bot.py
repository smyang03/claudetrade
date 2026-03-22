"""
trading_bot.py
Main loop for KR/US sessions. Paper by default, live with --live.
"""

import os
import sys
import json
import time
import argparse
import schedule
from pathlib import Path
from datetime import date, datetime, timedelta, time as dt_time
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
    place_order,
    KISWebSocket,
    get_daily_ohlcv,
    get_index_change,
    get_usd_krw,
    screen_market_kr,
    screen_market_us,
)
from indicators import calc_all
from risk_manager import RiskManager, HARD_RULES
from telegram_reporter import (
    send,
    morning_briefing,
    tuning_report,
    trade_alert,
    pnl_alert,
    daily_summary,
    status_report,
    dashboard_push,
    analyst_reinvoke_alert,
)
from telegram_commander import commander as tg_commander
from minority_report.analysts import get_three_judgments, select_tickers
from minority_report.consensus import build_consensus
from minority_report.tuner import tune
from minority_report.postmortem import run as run_postmortem
from phase1_trainer.digest_builder import build_kr_digest, build_us_digest, digest_to_prompt
from strategy.momentum import signal as mom_sig, params as mom_params
from strategy.mean_reversion import signal as mr_sig, params as mr_params
from strategy.gap_pullback import signal as gap_sig, params as gap_params
from strategy.volatility_breakout import signal as vb_sig, params as vb_params

from claude_memory import brain as BrainDB
from runtime_paths import get_runtime_path
from universe_manager import (
    UniverseConfig,
    build_universe_from_candidates,
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

# 동적 선택 실패 시 폴백 (기본 3종목)
_DEFAULT_KR_TICKERS = ["005930", "000660", "035420"]
_DEFAULT_US_TICKERS = ["NVDA", "TSLA", "AAPL"]

# 거래소 캘린더 (XKRX=한국거래소, XNYS=NYSE)
_EXCHANGE_MAP = {"KR": "XKRX", "US": "XNYS"}
_ec_cache: dict = {}  # 캘린더 객체 캐시


def _is_trading_day(market: str, check_date=None) -> bool:
    """오늘이 해당 시장의 정규 거래일인지 확인 (주말·공휴일 모두 처리)"""
    check_date = check_date or date.today()
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

        # ── 투자 금액 설정 ──────────────────────────────────────────────────
        # 모의투자: .env의 PAPER_CASH (기본 10,000,000원)
        # 실계좌:  실제 KIS 잔고 조회 → 총자산(현금+평가액) 사용
        if is_paper:
            init_cash = int(os.getenv("PAPER_CASH", "10000000"))
            max_order = int(os.getenv("MAX_ORDER_KRW", "500000"))
            log.info(f"모의투자 | 가상자금 {init_cash:,}원 | 최대주문 {max_order:,}원")
        else:
            try:
                bal = get_balance(self.token, market="KR")
                init_cash = bal["cash"] + bal["total_eval"]
                if init_cash <= 0:
                    raise ValueError("잔고 0 — 계좌 확인 필요")
                # 최대 주문: env 설정값 vs 총자산 5% 중 작은 값
                env_cap = int(os.getenv("MAX_ORDER_KRW", "2000000"))
                max_order = min(env_cap, int(init_cash * 0.05))
                log.info(f"실계좌 | 총자산 {init_cash:,}원 "
                         f"(현금 {bal['cash']:,} + 평가 {bal['total_eval']:,}) "
                         f"| 최대주문 {max_order:,}원")
            except Exception as e:
                log.error(f"잔고 조회 실패: {e}")
                raise SystemExit("실계좌 잔고 조회에 실패했습니다. 계좌/API 설정을 확인하세요.")

        self.risk = RiskManager(init_cash=init_cash, max_order_krw=max_order, market="KR")

        # ── KR / US 시장 예산 분리 ──────────────────────────────────────────
        # .env: KR_ALLOC_PCT=60  →  KR 60%, US 40% 배분
        kr_pct = min(100, max(0, int(os.getenv("KR_ALLOC_PCT", "60")))) / 100.0
        self.kr_budget = init_cash * kr_pct
        self.us_budget = init_cash * (1.0 - kr_pct)
        log.info(f"예산 배분 | KR {self.kr_budget:,.0f}원 ({kr_pct*100:.0f}%) "
                 f"/ US {self.us_budget:,.0f}원 ({(1-kr_pct)*100:.0f}%)")
        # ───────────────────────────────────────────────────────────────────

        self.today_judgment = {}
        self.today_tickers: dict = {}   # {market: [ticker, ...]} — 매일 아침 Claude가 선택
        self.today_universe: dict = {}
        self.tuning_count = 0
        self.ws = None
        self.price_cache = {}
        self.price_cache_raw = {}
        self._ohlcv_cache: dict = {}        # ticker -> DataFrame (일봉 캐시)
        self._ohlcv_cache_time: dict = {}   # ticker -> datetime (캐시 갱신 시각)
        self.session_active = False
        self.current_market = None
        self.usd_krw_rate = float(os.getenv("USD_KRW_RATE", "1350"))
        self.enable_limit_order = _env_bool("ENABLE_LIMIT_ORDER", False)
        self.limit_order_offset_bps = int(os.getenv("LIMIT_ORDER_OFFSET_BPS", "5"))
        self.enable_slippage_guard = _env_bool("ENABLE_SLIPPAGE_GUARD", False)
        self.max_est_slippage_bps = float(os.getenv("MAX_EST_SLIPPAGE_BPS", "25"))
        self.enable_atr_position_sizing = _env_bool("ENABLE_ATR_POSITION_SIZING", False)
        self.atr_target_pct = float(os.getenv("ATR_TARGET_PCT", "0.015"))
        self.enable_dynamic_universe = _env_bool("ENABLE_DYNAMIC_UNIVERSE", False)
        self.dynamic_universe_top_n = int(os.getenv("DYNAMIC_UNIVERSE_TOP_N", "20"))

        # 텔레그램 중복 방지용 마지막 전송 상태
        self._last_tg_state: dict = {}

        # 긴급 재판단 쿨다운 (마지막 재호출 튜닝 카운트, 60분=2사이클 간격 유지)
        self._last_reinvoke_tuning: int = -99

        # 장중 이벤트 기록 (튜닝/긴급재판단) — session_close 시 daily_judgment에 포함
        self._session_events: list = []

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

    # ── API 헬스체크 ───────────────────────────────────────────────────────────

    def _startup_health_check(self):
        """봇 시작 시 연결된 API 전체 상태 점검 후 텔레그램 리포트"""
        import os as _os
        results: dict[str, str] = {}

        # 1. KIS 토큰 (이미 __init__에서 획득했으면 OK)
        results["KIS 토큰"] = "OK" if self.token else "FAIL - 토큰 없음"

        # 2. KIS 실시간 시세 (삼성전자 호출)
        try:
            price_info = get_price("005930", self.token, market="KR")
            results["KIS 시세 (005930)"] = f"OK ({price_info['price']:,}원)"
        except Exception as e:
            results["KIS 시세 (005930)"] = f"FAIL - {e}"

        # 3. Alpha Vantage (US 시세)
        av_key = _os.getenv("ALPHA_VANTAGE_KEY", "")
        if av_key:
            try:
                candles = get_daily_ohlcv("NVDA", self.token, lookback_days=5, market="US")
                results["Alpha Vantage (NVDA)"] = (
                    f"OK ({len(candles)}일 캔들)" if not candles.empty else "WARN - 빈 데이터"
                )
            except Exception as e:
                results["Alpha Vantage (NVDA)"] = f"FAIL - {e}"
        else:
            results["Alpha Vantage"] = "SKIP - ALPHA_VANTAGE_KEY 없음"

        # 4. Telegram
        try:
            import requests as _req
            tg_token = _os.getenv("TELEGRAM_TOKEN", "")
            if tg_token:
                resp = _req.get(
                    f"https://api.telegram.org/bot{tg_token}/getMe", timeout=5
                )
                resp.raise_for_status()
                bot_name = resp.json()["result"].get("username", "?")
                results["Telegram"] = f"OK (@{bot_name})"
            else:
                results["Telegram"] = "SKIP - TELEGRAM_TOKEN 없음"
        except Exception as e:
            results["Telegram"] = f"FAIL - {e}"

        # 5. DART (공시) - 선택
        dart_key = _os.getenv("DART_API_KEY", "")
        if dart_key:
            try:
                import requests as _req
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
            results["DART API"] = "SKIP - DART_API_KEY 없음"

        # 결과 로그 출력
        ok_count  = sum(1 for v in results.values() if v.startswith("OK"))
        fail_count = sum(1 for v in results.values() if v.startswith("FAIL"))
        log.info("─── API Health Check ───────────────────────────────")
        for name, status in results.items():
            icon = "✓" if status.startswith("OK") else ("✗" if status.startswith("FAIL") else "–")
            log.info(f"  {icon} {name}: {status}")
        log.info(f"────────────────────── OK={ok_count} / FAIL={fail_count} ──")

        # 텔레그램으로 헬스체크 결과 전송
        lines = [f"{'paper' if self.is_paper else 'live'} 봇 시작 — API 점검 결과"]
        for name, status in results.items():
            icon = "✅" if status.startswith("OK") else ("❌" if status.startswith("FAIL") else "➖")
            lines.append(f"{icon} {name}: {status}")
        if fail_count:
            lines.append(f"\n⚠️ {fail_count}개 API 연결 실패 — 확인 필요")
        send("\n".join(lines))

    # ── 시장별 예산 조회 ──────────────────────────────────────────────────────

    def _market_budget_available(self, market: str) -> float:
        """해당 시장에 추가로 배분 가능한 잔여 예산 (원)"""
        budget = self.kr_budget if market == "KR" else self.us_budget
        invested = sum(
            p["entry"] * p["qty"]
            for p in self.risk.positions
            if self._ticker_market(p["ticker"]) == market
        )
        return max(0.0, budget - invested)

    # ── 포지션 영속성 ──────────────────────────────────────────────────────────

    def _save_positions(self):
        """이월 포지션을 파일에 저장 (봇 재시작 복구용)"""
        carry = [p for p in self.risk.positions if p.get("max_hold", 1) > 1]
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(carry, f, ensure_ascii=False, indent=2, default=str)
        if carry:
            log.info(f"[포지션 저장] {[p['ticker'] for p in carry]} → {POSITIONS_FILE}")

    def _restore_positions(self):
        """저장된 이월 포지션 복구"""
        if not POSITIONS_FILE.exists():
            return
        try:
            with open(POSITIONS_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            if not saved:
                return

            if not self.is_paper:
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
        """실계좌: 브로커 잔고와 저장 포지션 비교 → 실제 보유 중인 것만 유지"""
        try:
            bal = get_balance(self.token, market="KR")
            broker = {s["ticker"]: s for s in bal["stocks"]}
            verified = []
            for pos in saved:
                tk = pos["ticker"]
                if tk in broker:
                    # 수량 불일치 시 브로커 수량으로 보정
                    if broker[tk]["qty"] != pos["qty"]:
                        log.warning(f"[브로커 동기화] {tk} 수량 불일치 "
                                    f"저장={pos['qty']} 브로커={broker[tk]['qty']} → 보정")
                        pos["qty"] = broker[tk]["qty"]
                    verified.append(pos)
                else:
                    log.warning(f"[브로커 동기화] {tk} 브로커에 없음 → 포지션 제거")
            return verified
        except Exception as e:
            log.error(f"브로커 동기화 실패: {e} → 저장 포지션 그대로 사용")
            return saved

    def _refresh_token(self):
        self.token = get_access_token()

    def _ticker_market(self, ticker: str) -> str:
        return "US" if ticker.isalpha() else "KR"

    def _price_to_krw(self, price: float, market: str) -> float:
        return price if market == "KR" else price * self.usd_krw_rate

    def _compute_order_price(self, side: str, market: str, raw_price: float) -> float:
        """Return 0 for market order, or adjusted limit price when enabled."""
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

    def _process_exit_candidates(self):
        candidates = self.risk.get_exit_candidates()
        for cand in candidates:
            market = self._ticker_market(cand["ticker"])
            if not self.is_paper:
                raw_px = self.price_cache_raw.get(cand["ticker"], cand["exit_price"])
                order_px = self._compute_order_price("sell", market, float(raw_px))
                result = place_order(cand["ticker"], cand["qty"], order_px, "sell", self.token, market=market)
                if not result["success"]:
                    log.error(f"sell order failed [{cand['ticker']}]: {result['msg']}")
                    continue

            ex = self.risk.close_position(cand["ticker"], cand["exit_price"], cand["reason"])
            if not ex:
                continue
            raw_exit = self.price_cache_raw.get(ex["ticker"], ex["exit_price"])
            pnl_alert(ex["ticker"], ex["pnl_pct"], int(ex["pnl"]), ex["reason"])
            trade_alert("sell", ex["ticker"], ex["qty"], int(raw_exit), ex["strategy"], 0, 0, reason=ex["reason"], market=market)
            # 전략별 성과 brain 업데이트
            try:
                BrainDB.update_strategy_performance(
                    market, ex.get("strategy", "unknown"),
                    ex.get("pnl_pct", 0), ex.get("pnl", 0) > 0
                )
            except Exception as e:
                log.warning(f"strategy brain update failed: {e}")

    def session_open(self, market: str):
        log.info("=" * 50)
        log.info(f"[{market}] session_open")

        # ── 휴장일 체크 (주말 + 공휴일) ─────────────────────────────────────
        if not _is_trading_day(market):
            log.info(f"[{market}] 휴장일 — 세션 스킵")
            self.session_active = False
            self.current_market = None
            return

        if market == "US" and not self.is_paper:
            log.warning("[US] live mode is not supported yet. session skipped.")
            self.session_active = False
            self.current_market = None
            return

        self._refresh_token()
        self.session_active = True
        self.current_market = market
        self.risk.market = market          # 수수료율 시장에 맞게 설정
        self.tuning_count = 0
        self._session_events = []          # 세션 이벤트 초기화
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
            log.info(f"[이월 포지션 현재가 갱신] "
                     f"{[(p['ticker'], p['current_price']) for p in self.risk.positions]}")

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

        if market == "KR":
            digest = build_kr_digest(today, universe_tickers=universe_tickers)
        else:
            digest = build_us_digest(today, universe_tickers=universe_tickers)
        digest_prompt = digest_to_prompt(digest)

        # ── 재시작 시 당일 판단 재사용 ────────────────────────────────────────
        live_path = JUDGMENT_DIR / f"{today.replace('-', '')}_{market}.json"
        reused = False
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
            digest = build_kr_digest(today) if market == "KR" else build_us_digest(today)
            digest_prompt = digest_to_prompt(digest)

            brain_summary = BrainDB.generate_prompt_summary(market)
            brain_data = BrainDB.load()
            correction = json.dumps(brain_data.get("correction_guide", {}).get(market, {}), ensure_ascii=False)

            judgments = get_three_judgments(digest_prompt, brain_summary, correction, market=market)
            consensus = build_consensus(judgments, market=market)
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

            # 오늘 집중 종목: 스크리너 → Claude 선택
            log.info(f"[스크리너] {market} 시장 스캔 중...")
            if market == "KR":
                candidates = screen_market_kr(self.token)
            else:
                candidates = screen_market_us()
            analysis_log.info(
                f"[screen {today} {market}] candidates={len(candidates)}",
                extra={"extra": {
                    "event": "screen_candidates",
                    "date": today,
                    "market": market,
                    "count": len(candidates),
                    "tickers": [c.get("ticker") for c in candidates[:20]],
                }},
            )
            log.info(f"[스크리너] {market} 후보 {len(candidates)}개 → Claude 선택 중...")
            selected = select_tickers(market, digest_prompt, consensus["mode"], candidates)
            self.today_tickers[market] = selected
            log.info(f"[종목선택 확정] {market}: {selected}")
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

            # _debate 메타데이터 분리 후 today_judgment에 함께 보관
            debate_meta = judgments.pop("_debate", {})
            self.today_judgment = {
                "date": today,
                "market": market,
                "judgments": judgments,
                "consensus": consensus,
                "digest_prompt": digest_prompt,
                "tickers": selected,
                "round1_judgments": debate_meta.get("r1", {}),
                "debate_changes":   debate_meta.get("changes", []),
            }

        # 오늘 집중 종목: 스크리너 → Claude 선택
        log.info(f"[스크리너] {market} 시장 스캔 중...")
        if pre_candidates is not None:
            candidates = pre_candidates
        elif market == "KR":
            candidates = screen_market_kr(self.token)
        else:
            candidates = screen_market_us()

        selection_pool = candidates
        snapshot = self.today_universe.get(market, {})
        if self.enable_dynamic_universe and snapshot.get("candidates"):
            selection_pool = snapshot.get("candidates", candidates)
        analysis_log.info(
            f"[screen {today} {market}] candidates={len(candidates)}",
            extra={"extra": {
                "event": "screen_candidates",
                "date": today,
                "market": market,
                "count": len(candidates),
                "tickers": [c.get("ticker") for c in candidates[:20]],
            }},
        )
        log.info(f"[스크리너] {market} 후보 {len(selection_pool)}개 → Claude 선택 중...")
        selected = select_tickers(market, digest_prompt, consensus["mode"], selection_pool)
        self.today_tickers[market] = selected
        log.info(f"[종목선택 확정] {market}: {selected}")
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

        self.today_judgment = {
            "date": today,
            "market": market,
            "judgments": judgments,
            "consensus": consensus,
            "digest_prompt": digest_prompt,
            "tickers": selected,
            "universe_tickers": [c.get("ticker") for c in selection_pool if c.get("ticker")],
            "round1_judgments": debate_meta.get("r1", {}),
            "debate_changes":   debate_meta.get("changes", []),
        }

        # 대시보드가 장 중에도 오늘 판단을 볼 수 있도록 session_open 직후 즉시 기록
        try:
            with open(live_path, "w", encoding="utf-8") as f:
                json.dump({
                    **self.today_judgment,
                    "mode": "paper" if self.is_paper else "live",
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"판단 임시저장 실패: {e}")

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

        self.ws = KISWebSocket(self.token, selected, on_tick=self._on_tick, market=market)
        self.ws.start()

    def _on_tick(self, data: dict):
        ticker = data["ticker"]
        market = self._ticker_market(ticker)
        raw_price = data["price"]
        self.price_cache_raw[ticker] = raw_price
        self.price_cache[ticker] = self._price_to_krw(raw_price, market)
        self.risk.update_prices(self.price_cache)
        self._process_exit_candidates()

    def _get_ohlcv_cached(self, ticker: str, market: str, ttl_min: int = 60):
        """일봉 OHLCV 캐시 — TTL 내 재요청 생략 (장중 일봉은 당일 완성 전까지 불변)"""
        now = datetime.now(KST).replace(tzinfo=None)
        cached_at = self._ohlcv_cache_time.get(ticker)
        if cached_at and (now - cached_at).total_seconds() < ttl_min * 60:
            return self._ohlcv_cache[ticker]
        df = get_daily_ohlcv(ticker, self.token, lookback_days=180, market=market)
        self._ohlcv_cache[ticker] = df
        self._ohlcv_cache_time[ticker] = now
        return df

    def run_cycle(self, market: str):
        if not self.session_active:
            return
        if self.current_market != market:
            return
        if self.risk.check_halt():
            return

        mode = self.today_judgment.get("consensus", {}).get("mode", "CAUTIOUS")
        size_pct = self.today_judgment.get("consensus", {}).get("size", 50)
        tickers = self.today_tickers.get(
            market, _DEFAULT_KR_TICKERS if market == "KR" else _DEFAULT_US_TICKERS
        )

        now_str = datetime.now(KST).strftime("%H:%M")
        log.info(
            f"[{market} 사이클 {now_str}] 모드:{mode} size:{size_pct}% | "
            f"종목:{tickers} | 포지션:{len(self.risk.positions)}개"
        )

        for ticker in tickers:
            try:
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
                self.price_cache_raw[ticker] = price
                self.price_cache[ticker] = risk_price
                self.risk.update_prices(self.price_cache)
                self._process_exit_candidates()

                if mode == "HALT":
                    log.debug(f"  [{ticker}] HALT 모드 — 신규진입 스킵")
                    continue
                if self._in_entry_blackout(market):
                    log.debug(f"  [{ticker}] 블랙아웃 시간 — 스킵")
                    continue

                ok, reason = self.risk.can_open(ticker, risk_price, size_pct)
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
                    log.debug(f"  [{ticker}] 진입불가: {reason}")
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
                    log.debug(f"  [{ticker}] 예산 소진 (잔여 {avail:,.0f}원 < {risk_price:,.0f}원) → 스킵")
                    continue

                candles = self._get_ohlcv_cached(ticker, market)
                if candles.empty:
                    log.debug(f"  [{ticker}] 캔들 없음")
                    continue
                sig_df = calc_all(candles)
                if sig_df.empty:
                    continue
                i = len(sig_df) - 1

                signal_fired = False
                strategy_name = ""
                params = {}

                if market == "KR":
                    gap_p = gap_params(mode)
                    if gap_sig(sig_df, i, gap_p):
                        signal_fired = True
                        strategy_name = "gap_pullback"
                        params = gap_p
                    else:
                        mom_p = mom_params(mode)
                        if mom_sig(sig_df, i, mom_p):
                            signal_fired = True
                            strategy_name = "momentum"
                            params = mom_p
                        else:
                            mr_p = mr_params(mode)
                            if mr_sig(sig_df, i, mr_p):
                                signal_fired = True
                                strategy_name = "mean_reversion"
                                params = mr_p
                else:
                    vb_p = vb_params(mode)
                    if vb_sig(sig_df, i, vb_p):
                        signal_fired = True
                        strategy_name = "volatility_breakout"
                        params = vb_p

                if not signal_fired:
                    analysis_log.info(
                        f"[signal {market}] {ticker} none",
                        extra={"extra": {
                            "event": "signal_check",
                            "market": market,
                            "ticker": ticker,
                            "signal": "none",
                            "price": price,
                            "mode": mode,
                        }},
                    )
                    log.debug(f"  [{ticker}] 신호없음 {price:,}원")
                    continue

                if mode in ("HALT", "DEFENSIVE"):
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
                    log.debug(f"  [{ticker}] {strategy_name} 신호 — {mode} 모드 진입 억제")
                    continue

                sl_cap = abs(HARD_RULES["max_single_loss_pct"]) / 100.0
                sl_pct = min(params.get("sl_pct", 0.03), sl_cap)
                # mean_reversion: BB 중선(ma20)을 TP로 사용
                if strategy_name == "mean_reversion" and params.get("tp_bb_mid"):
                    bb_mid = float(sig_df.iloc[i].get("ma20", price))
                    tp_pct = max((bb_mid - price) / price, 0.005) if bb_mid > price else 0.03
                else:
                    tp_pct = params.get("tp_pct", HARD_RULES["take_profit_pct"] / 100.0)
                atr_pct = None
                if self.enable_atr_position_sizing:
                    atr_val = float(sig_df.iloc[i].get("atr", 0) or 0)
                    if risk_price > 0 and atr_val > 0:
                        atr_pct = atr_val / risk_price
                # 전략별 size_mult 적용 (예: momentum 0.4~1.0 → 합의 size_pct 스케일링)
                size_mult = float(params.get("size_mult", 1.0))
                effective_size = max(10, min(100, int(size_pct * size_mult)))
                qty = self.risk.calc_order_size(
                    risk_price,
                    effective_size,
                    sl_pct,
                    atr_pct=atr_pct,
                    atr_target_pct=self.atr_target_pct,
                )
                if qty <= 0:
                    log.debug(f"  [{ticker}] 주문가능 수량 0주")
                    continue
                order_cost = qty * risk_price
                if order_cost > avail:
                    log.debug(f"  [{ticker}] 시장 예산 초과 ({order_cost:,.0f}원 > {avail:,.0f}원) → 스킵")
                    continue
                if order_cost > self.risk.cash:
                    log.debug(f"  [{ticker}] 현금 부족 ({order_cost:,.0f}원 > {self.risk.cash:,.0f}원) → 스킵")
                    continue

                analysis_log.info(
                    f"[signal {market}] {ticker} {strategy_name} qty={qty}",
                    extra={"extra": {
                        "event": "entry_signal",
                        "market": market,
                        "ticker": ticker,
                        "strategy": strategy_name,
                        "mode": mode,
                        "price": price,
                        "risk_price_krw": risk_price,
                        "qty": qty,
                        "order_cost_krw": order_cost,
                        "tp_pct": tp_pct,
                        "sl_pct": sl_pct,
                    }},
                )
                if self.is_paper:
                    log.info(f"[PAPER BUY] {ticker} {qty}@{price:,} | {strategy_name}")
                else:
                    order_px = self._compute_order_price("buy", market, float(price))
                    result = place_order(ticker, qty, order_px, "buy", self.token, market=market)
                    if not result["success"]:
                        log.error(f"order failed [{ticker}]: {result['msg']}")
                        continue

                tp = int(price * (1 + tp_pct))
                sl = int(price * (1 - sl_pct))
                opened = self.risk.open_position(ticker, risk_price, qty, strategy_name, tp_pct, sl_pct, params.get("max_hold", 1))
                if not opened:
                    log.error(f"open_position 실패 [{ticker}]: 현금 부족 또는 내부 오류 (cash={self.risk.cash:,.0f})")
                    continue
                trade_alert("buy", ticker, qty, price, strategy_name, tp, sl, market=market)

            except Exception as e:
                log.error(f"cycle error [{ticker}]: {e}")

        log.info(
            f"[{market} 사이클 완료] 포지션:{len(self.risk.positions)}개 | "
            f"현금:{self.risk.cash:,.0f}원"
        )
        self._maybe_push_dashboard()  # 포지션/P&L 변화 있으면 텔레그램 전송

    def run_tuning(self, market: str):
        if not self.session_active:
            return
        if self.current_market != market:
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
            old_mode = self.today_judgment["consensus"]["mode"]
            self.today_judgment["consensus"]["mode"] = result.get("mode", old_mode)
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

        # 모드가 바뀐 경우만 tuning_report 전송, 유지면 스킵
        if action != "MAINTAIN":
            tuning_report(elapsed, result, self.today_judgment["consensus"]["mode"], self.risk.positions)
            self._maybe_push_dashboard(force=True)
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

    # ── 긴급 재판단 ───────────────────────────────────────────────────────────

    # 분석가 재투입 조건 (하나라도 해당하면 True)
    _REINVOKE_INDEX_THRESHOLD = -2.0   # 지수 -2% 이상 급락
    _REINVOKE_COOLDOWN_CYCLES = 2      # 최소 2사이클(60분) 간격

    def _should_reinvoke_analysts(self, result: dict, current_state: dict) -> tuple:
        """분석가 재투입 여부. (bool, trigger_reason)"""
        # 쿨다운 체크 (같은 세션에서 60분 내 재호출 방지)
        if self.tuning_count - self._last_reinvoke_tuning < self._REINVOKE_COOLDOWN_CYCLES:
            return False, ""

        # 조건 1: 튜너가 방향 전환 권고
        if result.get("action") == "REVERSE":
            return True, f"REVERSE 권고: {result.get('reason', '')[:60]}"

        # 조건 2: 지수 -2% 이상 급락
        idx_chg = current_state.get("index_change", 0)
        if idx_chg <= self._REINVOKE_INDEX_THRESHOLD:
            return True, f"지수 급락 {idx_chg:+.2f}%"

        # 조건 3: 튜너 경고 발령 + TIGHTEN (MAINTAIN이면 낮은 위험)
        if result.get("warning") and result.get("action") == "TIGHTEN":
            return True, f"튜너 경고: {result['warning']}"

        return False, ""

    def _reinvoke_analysts(self, market: str, trigger: str):
        """이상 신호 감지 시 분석가 3명 긴급 재호출 → 판단·합의 갱신"""
        log.warning(f"[긴급 재판단 시작] {market} | 트리거: {trigger}")
        try:
            digest_prompt = self.today_judgment.get("digest_prompt", "")
            brain_summary = BrainDB.generate_prompt_summary(market)
            brain_data = BrainDB.load()
            correction = json.dumps(
                brain_data.get("correction_guide", {}).get(market, {}),
                ensure_ascii=False,
            )

            new_judgments = get_three_judgments(
                digest_prompt, brain_summary, correction, market=market
            )
            new_consensus = build_consensus(new_judgments, market=market)

            old_mode = self.today_judgment.get("consensus", {}).get("mode", "?")
            debate_meta = new_judgments.pop("_debate", {})

            self.today_judgment["judgments"] = new_judgments
            self.today_judgment["consensus"] = new_consensus
            self._last_reinvoke_tuning = self.tuning_count

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

            analyst_reinvoke_alert(
                market, trigger, old_mode, new_consensus["mode"],
                new_judgments, new_consensus,
                round1_judgments=debate_meta.get("r1", {}),
                debate_changes=debate_meta.get("changes", []),
            )
            self._maybe_push_dashboard(force=True)

        except Exception as e:
            log.error(f"[긴급 재판단 실패] {e}")

    def _build_tg_state(self) -> dict:
        """현재 상태 스냅샷 (변화 감지용)"""
        market = self.current_market or ""
        mode   = self.today_judgment.get("consensus", {}).get("mode", "")
        pos_key = tuple(sorted((p["ticker"], p["qty"]) for p in self.risk.positions))
        pnl_bucket = round(self.risk.daily_pnl / max(self.risk.init_cash, 1) * 100, 1)  # 0.1% 단위
        return {
            "market":     market,
            "mode":       mode,
            "pos_key":    pos_key,
            "pnl_bucket": pnl_bucket,
            "cash_m":     round(self.risk.cash / 1_000_000, 2),  # 1만원 단위
        }

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
        pnl_pct   = self.risk.daily_pnl / max(self.risk.init_cash, 1) * 100
        pnl_krw   = int(self.risk.daily_pnl)

        dashboard_push(market, mode, self.risk.positions,
                       self.risk.cash, pnl_pct, pnl_krw, judgments, tickers,
                       max_order_krw=int(self.risk.max_order_krw),
                       total_fee=int(self.risk.total_fee))
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
            log.warning(f"[당일청산] {pos['ticker']} {cp:,.0f}")

        if multi_days:
            log.info(f"[이월] {[p['ticker'] for p in multi_days]} "
                     f"→ 다음 세션 계속 보유 (held_days: "
                     f"{[p['held_days'] for p in multi_days]})")

        # 이월 포지션 파일 저장 (재시작 대비)
        self._save_positions()

        today = date.today().strftime("%Y-%m-%d")
        actual = {
            "market_change": get_index_change(market),
            "pnl_pct": self.risk.daily_pnl / max(self.risk.session_start_equity, 1) * 100,
            "pnl_krw": int(self.risk.daily_pnl),
            "win": self.risk.daily_pnl > 0,
            "trades": len(self.risk.trade_log),
            "cumulative": int(self.risk.equity()),
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
                trade_log=self.risk.trade_log,   # 당일 체결 내역 전달
            )
        judgment_log.info(
            f"[close {today} {market}] pnl={actual['pnl_pct']:+.2f}% mode={self.today_judgment.get('consensus', {}).get('mode', '-')}",
            extra={"extra": {
                "event": "session_close_judgment",
                "date": today,
                "market": market,
                "actual_result": actual,
                "consensus": self.today_judgment.get("consensus", {}),
                "postmortem": pm,
                "trades": self.risk.trade_log,
            }},
        )

        # ── 파인튜닝/프롬프트 개선을 위한 완전한 training record 저장 ──────────
        record = {
            **self.today_judgment,          # date, market, judgments, consensus,
                                            # digest_prompt, tickers,
                                            # round1_judgments, debate_changes 포함
            "actual_result":  actual,
            "postmortem":     pm,
            "trades":         self.risk.trade_log,
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
        sell_trades = [t for t in self.risk.trade_log if t.get("side") == "sell"]
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
    bot = TradingBot(is_paper=is_paper)
    log.info("=== Trading Bot Start ===")

    # 텔레그램 명령어 수신 시작 (백그라운드 스레드)
    tg_commander.start(bot)
    mode_txt = "모의투자" if is_paper else "실계좌"
    send(
        f"🤖 <b>봇 시작됨 [{mode_txt}]</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💰 초기자금: {bot.risk.init_cash:,}원\n"
        f"📦 1회 최대주문: {int(bot.risk.max_order_krw):,}원\n"
        f"⚙️ KR할당: {int(os.getenv('KR_ALLOC_PCT','60'))}%\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"명령어: <b>?</b> 입력 시 도움말  |  <b>/setorder 300000</b> 으로 주문금액 변경"
    )

    schedule.every().day.at("08:50").do(bot.session_open, "KR")
    schedule.every().day.at("16:00").do(bot.session_close, "KR")
    schedule.every().day.at("22:20").do(bot.session_open, "US")
    schedule.every().day.at("05:00").do(bot.session_close, "US")

    schedule.every(5).minutes.do(bot.run_cycle, "KR")
    schedule.every(5).minutes.do(bot.run_cycle, "US")

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
