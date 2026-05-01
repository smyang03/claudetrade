"""
trading_bot.py
Main loop for KR/US sessions. Paper by default, live with --live.
"""
import os
import sys
import json
import time
import math
import atexit
import sqlite3
import subprocess
import queue as _queue_mod
import argparse
import threading
import schedule
from collections import deque
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
# --live ?뚮옒洹몃줈 env ?뚯씪 遺꾧린 (argparse ?댁쟾??sys.argv 吏곸젒 ?뺤씤)
# .env.live / .env.paper ?놁쑝硫?.env fallback
_dotenv_path = ".env.live" if "--live" in sys.argv else ".env.paper"
if not Path(_dotenv_path).exists():
    _dotenv_path = ".env"
load_dotenv(dotenv_path=_dotenv_path, override=True)
# 濡쒓굅 ?뚯씪紐?遺꾨━: import ?꾩뿉 紐⑤뱶 ?ㅼ젙 ??紐⑤뱺 ?쒕툕紐⑤뱢 濡쒓굅 ?먮룞 ?곸슜
import os as _os
_os.environ["TRADING_BOT_MODE"] = "live" if "--live" in sys.argv else "paper"
def _apply_v2_start_config_env() -> None:
    if "--live" not in sys.argv:
        return
    if str(os.getenv("V2_START_CONFIG_DISABLED", "") or "").strip().lower() in ("1", "true", "yes", "y", "on"):
        return
    path = Path(os.getenv("V2_START_CONFIG_PATH", "config/v2_start_config.json"))
    if not path.is_absolute():
        path = Path(__file__).parent / path
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        overrides = data.get("env_overrides") or {}
        if not isinstance(overrides, dict):
            return
        for key, value in overrides.items():
            if value is None:
                continue
            if isinstance(value, bool):
                value = str(value).lower()
            os.environ[str(key)] = str(value)
    except Exception:
        pass
_apply_v2_start_config_env()
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
    _daily_ohlcv_us_alpha,
    _daily_ohlcv_us_yf,
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
from minority_report.analysts import get_last_selection_meta, get_three_judgments, select_tickers
from minority_report.consensus import (
    apply_unanimous_override,
    build_consensus,
    build_judgment_eval,
)
from minority_report.tuner import tune
from minority_report.postmortem import run as run_postmortem
from phase1_trainer.digest_builder import build_breadth_summary, build_kr_digest, build_us_digest, digest_to_prompt, get_market_vol_trend
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
from bot.candidate_health import CandidateHealthTracker, normalize_ticker as _candidate_health_ticker
from bot.candidate_policy import filter_tradable_candidates, selection_limits
from bot.entry_timing import EntryTimingTracker
from bot.log_sanitizer import mask_secrets
from bot.screener_quality import opening_fresh_quality_metrics, write_candidate_quality_log
import shared_judgment_cache
import ticker_selection_db as tsdb
import intraday_strategy_db as isdb
try:
    from audit.shadow_audit_ids import make_episode_id as _shadow_make_episode_id
    from audit.shadow_audit_ids import make_signal_id as _shadow_make_signal_id
    from audit.shadow_audit_ids import minute_bucket as _shadow_minute_bucket
    from audit.shadow_audit_writer import ShadowAuditWriter as _ShadowAuditWriter
    from audit.shadow_audit_writer import try_emit as _shadow_try_emit
    from audit.shadow_outcome_updater import ShadowOutcomeUpdater as _ShadowOutcomeUpdater
    _SHADOW_AUDIT_AVAILABLE = True
except Exception:
    _shadow_make_episode_id = None
    _shadow_make_signal_id = None
    _shadow_minute_bucket = None
    _ShadowAuditWriter = None
    _ShadowOutcomeUpdater = None
    def _shadow_try_emit(_writer, _event):
        return False
    _SHADOW_AUDIT_AVAILABLE = False
try:
    from runtime.v2_lifecycle_runtime import V2LifecycleRuntime as _V2LifecycleRuntime
    from runtime.v2_lifecycle_runtime import v2_close_reason as _v2_close_reason
    _V2_LIFECYCLE_AVAILABLE = True
except Exception as _v2_import_err:
    _V2LifecycleRuntime = None
    def _v2_close_reason(reason: str) -> str:
        return "CLOSED_USER_MANUAL"
    _V2_LIFECYCLE_AVAILABLE = False
try:
    from runtime.pathb_runtime import PathBRuntime as _PathBRuntime
    _PATHB_RUNTIME_AVAILABLE = True
except Exception as _pathb_import_err:
    _PathBRuntime = None
    _PATHB_RUNTIME_AVAILABLE = False
try:
    from execution.path_arbiter import build_late_entry_payload as _build_late_entry_payload
except Exception:
    def _build_late_entry_payload(**kwargs):
        return {}
from bot.market_utils import (
    MarketUtilsMixin,
    _market_session_date,
    _is_trading_day,
    _EXCHANGE_MAP,
    _ec_cache,
)
from bot.state import StateMixin
# ML ?섏궗寃곗젙 DB (?좏깮????import ?ㅽ뙣?대룄 遊??뺤긽 ?숈옉)
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
_DECISIONS_DB_PATH = Path(__file__).parent / "data" / "ml" / "decisions.db"
_OPS_REVIEW_DAYS = 14
_OPS_REVIEW_SESSION_LIMIT = 10
_LESSON_CANDIDATES_PATH = get_runtime_path("state", "lesson_candidates.json")
BOT_PID_FILE = get_runtime_path("state", "trading_bot.pid")
DECISIONS_FILE = get_runtime_path("state", "decisions.jsonl")
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
# ?숈쟻 ?좏깮 ?ㅽ뙣 ???대갚 (湲곕낯 3醫낅ぉ)
_DEFAULT_KR_TICKERS = ["005930", "068270", "035420"]
_DEFAULT_US_TICKERS = ["NVDA", "TSLA", "GOOGL", "AAPL", "NFLX"]
# 吏꾩엯 李⑤떒 荑⑤떎??(遺?
_STOP_COOLDOWN_MIN = int(os.getenv("STOP_COOLDOWN_MIN", "60"))   # ?먯젅 ???ъ쭊??湲덉? (20??0: 諛섎났?먯젅 諛⑹?)
_BUY_COOLDOWN_MIN  = int(os.getenv("BUY_COOLDOWN_MIN",  "15"))   # 留ㅼ닔 ?묒닔 ??以묐났 李⑤떒
_TP_COOLDOWN_MIN   = int(os.getenv("TP_COOLDOWN_MIN",   "10"))   # TP ???ъ쭊??李⑤떒
_MIN_ENTRY_CONF    = float(os.getenv("MIN_ENTRY_CONF",   "0.4"))  # minimum average analyst confidence
_STARTUP_GUARD_SEC = float(os.getenv("STARTUP_GUARD_SEC", "60"))  # protect first cycle after session_open
_ENTRY_SCAN_OPENING_MIN = int(os.getenv("ENTRY_SCAN_OPENING_MIN", "30"))
_ENTRY_SCAN_OPENING_INTERVAL_MIN = int(os.getenv("ENTRY_SCAN_OPENING_INTERVAL_MIN", "2"))
_ENTRY_SCAN_REGULAR_INTERVAL_MIN = int(os.getenv("ENTRY_SCAN_REGULAR_INTERVAL_MIN", "5"))
_RESCREEN_INTERVAL_MIN = int(os.getenv("RESCREEN_INTERVAL_MIN", "60"))
_KR_NO_SIGNAL_SWAP_MIN = int(os.getenv("KR_NO_SIGNAL_SWAP_MIN", "60"))   # KR: 臾댁떊??60遺??꾩쟻 ??援먯껜
_US_NO_SIGNAL_SWAP_CYCLES = int(os.getenv("US_NO_SIGNAL_SWAP_CYCLES", "8"))  # US: 臾댁떊??8?ъ씠????援먯껜
# KR: session_open(8:50)怨??ㅼ젣 ???쒖옉(9:00) ?ъ씠 ?ㅽ봽??(遺?
_KR_MARKET_OPEN_OFFSET_MIN = int(os.getenv("KR_MARKET_OPEN_OFFSET_MIN", "10"))
# 留덇컧 吏곸쟾 ?좉퇋 吏꾩엯 李⑤떒 ??session_open 湲곗? 寃쎄낵 遺?# KR: 8:50 KST + 380min = 15:10 KST
# US: 22:20 KST + 370min = 04:30 KST = 15:30 ET  (KST = ET + 13h ??15:30 ET + 13h = 04:30 KST)
_KR_ENTRY_CUTOFF_FROM_OPEN_MIN = int(os.getenv("KR_ENTRY_CUTOFF_FROM_OPEN_MIN", "380"))
_US_ENTRY_CUTOFF_FROM_OPEN_MIN = int(os.getenv("US_ENTRY_CUTOFF_FROM_OPEN_MIN", "370"))
_RESCREEN_SCHEDULE_TICK_MIN = int(os.getenv("RESCREEN_SCHEDULE_TICK_MIN", "30"))
_TASK_SLOW_WARN_SEC = float(os.getenv("TASK_SLOW_WARN_SEC", "240"))  # ?묒뾽 吏??寃쎄퀬 ?꾧퀎媛?(4遺?
_LIVE_STATUS_MIN_INTERVAL = float(os.getenv("LIVE_STATUS_MIN_INTERVAL_SEC", "10"))  # live_status 理쒖냼 ?곌린 媛꾧꺽
# VIX 援ш컙蹂??ъ????ъ씠利??뱀닔 (US ?꾩슜)
_MIN_EFFECTIVE_ORDER_KRW = float(os.getenv("MIN_EFFECTIVE_ORDER_KRW", "30000"))
_MIN_EFFECTIVE_ORDER_USD = float(os.getenv("MIN_EFFECTIVE_ORDER_USD", "30"))
_MICRO_PROBE_STRATEGY = "MICRO_PROBE"
_VIX_SIZE_TIERS = [
    (30.0, float(os.getenv("VIX_MULT_30", "0.55"))),   # VIX 30+  ??55%
    (25.0, float(os.getenv("VIX_MULT_25", "0.70"))),   # VIX 25+  ??70%
    (20.0, float(os.getenv("VIX_MULT_20", "0.85"))),   # VIX 20+  ??85%
]
# 遺꾩꽍媛 suggested_strategy(?쒓?) ??肄붾뱶 ?꾨왂紐?留ㅽ븨
_STRATEGY_NAME_MAP = {
    "momentum": "momentum",
    "모멘텀": "momentum",
    "mean_reversion": "mean_reversion",
    "평균회귀": "mean_reversion",
    "gap_pullback": "gap_pullback",
    "갭풀백": "gap_pullback",
    "갭 풀백": "gap_pullback",
    "갭눌림": "gap_pullback",
    "갭+눌림": "gap_pullback",
    "갭 + 눌림": "gap_pullback",
    "opening_range_pullback": "opening_range_pullback",
    "or_pullback": "opening_range_pullback",
    "or눌림": "opening_range_pullback",
    "OR눌림": "opening_range_pullback",
    "volatility_breakout": "volatility_breakout",
    "변동성돌파": "volatility_breakout",
    "continuation": "continuation",
    "연속진입": "continuation",
    "observe": "",
    "관망": "",
}
_SELECTION_RISK_TAG_CAPS = (
    (("low_liquidity", "liquidity", "watch_only", "저유동성"), 35),
    (("overheated", "chase", "stop_loss", "spike", "과열", "추격", "손절"), 50),
    (("volatility", "news", "theme", "변동성", "뉴스", "테마"), 70),
)
_WATCH_ONLY_RECOVERY_FALLBACKS = {"selection_partial", "safe_watch", "ticker_regex"}
_WATCH_ONLY_HARD_KEYWORDS = (
    "low_liquidity",
    "liquidity_limit",
    "insufficient_liquidity",
    "trading_halt",
    "etf",
    "etn",
    "structured_product",
    "leverage",
    "inverse",
    "missing_data",
    "data_insufficient",
    "trading_code",
    "price_error",
    "invalid",
    "outlier",
)
_WATCH_ONLY_SOFT_KEYWORDS = (
    "rs",
    "rs 약",
    "rs 약세",
    "약세",
    "pullback",
    "눌림",
    "반등",
    "watch",
    "감시",
    "관찰",
    "ma60",
    "deep",
    "회복",
    "trend",
    "추세 약",
    "부적합",
)
_RAW_SCREEN_TOP_N_DEFAULTS = {"KR": 80, "US": 80}
_DYNAMIC_UNIVERSE_TOP_N_DEFAULTS = {"KR": 40, "US": 40}
_TRADE_READY_SLOT_LIMITS = {
    "RISK_ON": {
        "__total__": 6,
        "momentum": 2,
        "gap_pullback": 2,
        "opening_range_pullback": 1,
        "mean_reversion": 1,
        "continuation": 1,
        "__unassigned__": 1,
    },
    "BALANCED": {
        "__total__": 5,
        "gap_pullback": 2,
        "opening_range_pullback": 1,
        "mean_reversion": 1,
        "momentum": 1,
        "__unassigned__": 1,
    },
    "RISK_OFF": {
        "__total__": 1,
        "mean_reversion": 1,
        "__unassigned__": 0,
    },
}
_CLAUDE_RUNTIME_OVERRIDE_BOUNDS = {
    "momentum_wait_adjust_min": (-15, 15),
    "entry_priority_cutoff_adjust": (-0.08, 0.08),
    "kr_momentum_atr_cap_adjust": (-0.02, 0.03),
    "kr_momentum_atr_cap_high_adjust": (-0.02, 0.03),
}
def _mode_family(mode: str) -> str:
    normalized = str(mode or "").upper()
    if normalized in {"AGGRESSIVE", "MODERATE_BULL", "MILD_BULL"}:
        return "RISK_ON"
    if not normalized or normalized in {"CAUTIOUS", "NEUTRAL"}:
        return "BALANCED"
    return "RISK_OFF"
def _normalize_strategy_name(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    normalized = _STRATEGY_NAME_MAP.get(value)
    if normalized is not None:
        return normalized
    normalized = _STRATEGY_NAME_MAP.get(value.lower())
    if normalized is not None:
        return normalized
    return ""
def _analyst_strategy_vote(judgments: dict) -> str:
    """
    3紐?遺꾩꽍媛 suggested_strategy ?ㅼ닔寃???肄붾뱶 ?꾨왂紐?
    2???댁긽 ?쇱튂 ?놁쑝硫?鍮?臾몄옄??諛섑솚.
    """
    from collections import Counter
    votes = [
        _normalize_strategy_name(judgments.get(a, {}).get("suggested_strategy", ""))
        for a in ("bull", "bear", "neutral")
    ]
    valid = [v for v in votes if v]
    if not valid:
        return ""
    top, top_count = Counter(valid).most_common(1)[0]
    return top if top_count >= 2 else ""
def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")
def _v2_fresh_brain_policy_enabled() -> bool:
    policy = str(os.getenv("V2_BRAIN_POLICY", "") or "").strip().lower()
    if policy in {"fresh", "fresh_v2", "fresh_v2_reference_v1"}:
        return True
    return _env_bool("V2_FRESH_BRAIN_START", False)
def _enabled_markets_from_env() -> set[str]:
    raw = str(os.getenv("ENABLED_MARKETS", "KR,US") or "KR,US")
    markets = {item.strip().upper() for item in raw.split(",") if item.strip()}
    return markets & {"KR", "US"} or {"KR", "US"}
class TradingBot(MarketUtilsMixin, StateMixin):
    def __init__(self, is_paper: bool = True):
        self.is_paper = is_paper
        self.token = get_access_token()
        self.v2 = _V2LifecycleRuntime(self, is_paper=is_paper) if _V2_LIFECYCLE_AVAILABLE else None
        self.enabled_markets = _enabled_markets_from_env()
        self.usd_krw_rate = float(os.getenv("USD_KRW_RATE", "1350"))
        if self.v2 is None:
            self.v2_lifecycle_enabled = False
            self.v2_registry = None
            self.v2_brain_snapshot_store = None
            self.v2_decision_ids: dict[str, dict[str, str]] = {"KR": {}, "US": {}}
            self.v2_brain_snapshot_ids: dict[str, str] = {"KR": "", "US": ""}
            self.v2_fixed_sizing_enabled = False
            self.v2_fixed_sizer = None
            self.v2_safety_gate = None
            self.v2_partial_fill_policy = None
            self.v2_order_unknown = None
            self.v2_order_rate_limiter = None
            self.v2_risk_profiles = {}
            self._v2_same_day_stop_tickers: dict[str, set[str]] = {"KR": set(), "US": set()}
        # ?? ?ъ옄 湲덉븸 ?ㅼ젙 ??KIS ?붽퀬 吏곸젒 議고쉶 (紐⑥쓽/?ㅺ굅??怨듯넻) ?????????
        mode_label = "paper" if is_paper else "live"
        _bal_retry_max = 3 if is_paper else 3
        _bal_retry_delay = 5
        bal_kr = None
        for _attempt in range(1, _bal_retry_max + 1):
            try:
                bal_kr = self._get_balance_with_token_refresh("KR")
                break
            except Exception as e:
                _log_risk("warning", f"KIS KR ?붽퀬 議고쉶 ?ㅽ뙣 ({_attempt}/{_bal_retry_max}): {e}")
                if _attempt < _bal_retry_max:
                    import time as _t; _t.sleep(_bal_retry_delay)
                else:
                    if is_paper:
                        _log_risk("warning", "KIS ?쒕쾭 ?묐떟 ?놁쓬 ??PAPER_CASH ?대갚?쇰줈 湲곕룞")
                        bal_kr = {"cash": int(os.getenv("PAPER_CASH", "10000000")), "total_eval": 0}
                    else:
                        raise SystemExit(f"KIS ?ㅺ굅???붽퀬 議고쉶 {_bal_retry_max}???ㅽ뙣 ??醫낅즺")
        init_cash = bal_kr["cash"] + bal_kr["total_eval"]
        if init_cash <= 0:
            if is_paper:
                init_cash = int(os.getenv("PAPER_CASH", "10000000"))
                _log_risk(
                    f"紐⑥쓽?ъ옄 ?붽퀬 0 ??PAPER_CASH ?대갚({init_cash:,}?? ?ъ슜. "
                    f"KIS ?깆뿉??紐⑥쓽?ъ옄 怨꾩쥖 珥덇린???꾩슂 (紐⑥쓽?ъ옄 硫붾돱 ??珥덇린??"
                )
            else:
                raise SystemExit("?붽퀬 0 ???ㅺ굅??怨꾩쥖 ?뺤씤 ?꾩슂")
        env_cap = int(os.getenv("MAX_ORDER_KRW", "500000" if is_paper else "2000000"))
        order_pct = float(os.getenv("MAX_ORDER_PCT", "0.05"))
        max_order = min(env_cap, int(init_cash * order_pct))
        _log_normal(
            "info",
            f"{mode_label} | KIS KR ?붽퀬 {init_cash:,}??"
            f"(?꾧툑 {bal_kr['cash']:,} + ?됯? {bal_kr['total_eval']:,}) "
            f"| max_order {max_order:,} KRW",
        )
        # US ?붽퀬 議고쉶 ???섏쟾 ?꾧툑 + 蹂댁쑀醫낅ぉ ?쒖떆, 怨듭쑀 ????⑹궛
        us_cash_init_krw = 0
        if self._market_enabled("US"):
            try:
                bal_us = self._get_balance_with_token_refresh("US")
                usd_krw = get_usd_krw()
                self.usd_krw_rate = float(usd_krw)
                us_cash_usd    = float(bal_us.get("cash", 0) or 0)
                us_cash_krw_   = int(us_cash_usd * usd_krw)
                us_eval_usd    = float(bal_us.get("total_eval", 0) or 0)
                us_eval_krw    = int(us_eval_usd * usd_krw)
                _log_normal(
                    "info",
                    f"{mode_label} | KIS US balance ${us_cash_usd:.2f} "
                    f"cash({us_cash_krw_:,} KRW) + ${us_eval_usd:.2f} eval({us_eval_krw:,} KRW)"
                    f" | positions {len(bal_us['stocks'])}",
                )
                if not self.is_paper:
                    us_cash_init_krw = us_cash_krw_
            except Exception as e:
                _log_risk("warning", f"KIS US ?붽퀬 議고쉶 ?ㅽ뙣 (臾댁떆): {e}")
        else:
            _log_normal("info", "US disabled by ENABLED_MARKETS; skipping US balance lookup")
        init_cash_total = init_cash + us_cash_init_krw
        self.risk = RiskManager(init_cash=init_cash_total, max_order_krw=max_order, market="KR")
        # KR/US 怨듭쑀 ? ???⑥씪 ?꾧툑 怨꾩쥖, ?쒖옣 援щ텇 ?놁씠 ?ъ슜
        if us_cash_init_krw > 0:
            _log_normal("info", f"shared pool | total {init_cash_total:,.0f} KRW (KR {init_cash:,} + US {us_cash_init_krw:,})")
        else:
            _log_normal("info", f"shared pool | total {init_cash_total:,.0f} KRW (KR/US shared)")
        self.today_judgment = {}
        self.today_tickers: dict = {}   # {market: [ticker, ...]} ??留ㅼ씪 ?꾩묠 Claude媛 ?좏깮
        self.today_ticker_reasons: dict = {}
        self.trade_ready_tickers: dict[str, list[str]] = {"KR": [], "US": []}
        self.selection_meta: dict[str, dict] = {"KR": {}, "US": {}}
        self.selection_stages: dict[str, dict] = {"KR": {}, "US": {}}
        self.candidate_health_trackers: dict[str, Optional[CandidateHealthTracker]] = {"KR": None, "US": None}
        self.today_universe: dict = {}
        self.tuning_count = 0
        self._last_tune_result: dict[str, dict] = {"KR": {}, "US": {}}
        self._tune_maintain_streak: dict[str, int] = {"KR": 0, "US": 0}
        # 30遺?媛꾧꺽 吏??蹂?숇쪧 ?덉뒪?좊━ (?쒖옣蹂? 理쒕? 8媛?= 4?쒓컙)
        self._index_history: dict[str, deque] = {
            "KR": deque(maxlen=8),
            "US": deque(maxlen=8),
        }
        self._active_session_date: dict[str, Optional[date]] = {"KR": None, "US": None}
        self.ws = None
        self.price_cache = {}
        self.price_cache_raw = {}
        self._ohlcv_cache: dict = {}        # ticker -> DataFrame (?쇰큺 罹먯떆)
        self._ohlcv_cache_time: dict = {}   # ticker -> datetime (罹먯떆 媛깆떊 ?쒓컖)
        self._invalid_price_count: dict = {}  # ticker -> ?곗냽 invalid price ?잛닔
        self.session_active = False
        self.current_market = None
        self._market_task_owner: dict[str, Optional[str]] = {"KR": None, "US": None}
        self._session_open_at: dict[str, float] = {"KR": 0.0, "US": 0.0}  # startup 蹂댄샇 援ш컙
        self._session_startup_guard_sec: dict[str, float] = {"KR": _STARTUP_GUARD_SEC, "US": _STARTUP_GUARD_SEC}
        self._daily_baseline_by_market: dict[str, dict] = {"KR": {}, "US": {}}
        self._recent_sell_proceeds_by_market: dict[str, list[dict]] = {"KR": [], "US": []}
        self._last_entry_scan_at: dict[str, float] = {"KR": 0.0, "US": 0.0}
        self._last_rescreen_at: dict[str, float] = {"KR": 0.0, "US": 0.0}
        self.entry_timing = EntryTimingTracker(runtime_mode=self._mode)
        self.shadow_audit = _ShadowAuditWriter.from_env(runtime_mode=self._mode) if _SHADOW_AUDIT_AVAILABLE else None
        if self.shadow_audit is not None:
            self.shadow_audit.start()
            atexit.register(self._audit_shutdown)
        self._shadow_audit_episode_ids: dict[str, str] = {}
        self._vix_refresh_at: float = 0.0          # VIX ?μ쨷 媛깆떊 ??꾩뒪?ы봽
        self._task_start_time: dict[str, float] = {"KR": 0.0, "US": 0.0}  # ?묒뾽 吏??媛먯?
        self._live_status_written_at: dict[str, float] = {"KR": 0.0, "US": 0.0}  # 以묐났 ?곌린 諛⑹?
        self._ticker_no_signal_minutes: dict = {}  # ticker -> ?꾩쟻 臾댁떊???쒓컙(遺?, KR 援먯껜 ?꾧퀎???ъ슜
        self.usd_krw_rate = float(getattr(self, "usd_krw_rate", 0) or os.getenv("USD_KRW_RATE", "1350"))
        self.enable_limit_order = _env_bool("ENABLE_LIMIT_ORDER", False)
        self.limit_order_offset_bps = int(os.getenv("LIMIT_ORDER_OFFSET_BPS", "5"))
        self.enable_slippage_guard = _env_bool("ENABLE_SLIPPAGE_GUARD", False)
        self.max_est_slippage_bps = float(os.getenv("MAX_EST_SLIPPAGE_BPS", "25"))
        self.enable_atr_position_sizing = _env_bool("ENABLE_ATR_POSITION_SIZING", True)
        self.continuation_atr_position_sizing = _env_bool("CONTINUATION_ATR_POSITION_SIZING", False)
        self.enable_continuation_live = _env_bool("ENABLE_CONTINUATION_LIVE", False)
        self.enable_soft_watch_promotion = _env_bool("ENABLE_SOFT_WATCH_PROMOTION", False)
        self.enable_kr_momentum_shrink = _env_bool("ENABLE_KR_MOMENTUM_SHRINK", True)
        self.enable_micro_probe = _env_bool("MICRO_PROBE_ENABLED", False)
        self.micro_probe_paper_only = _env_bool("MICRO_PROBE_PAPER_ONLY", True)
        self.micro_probe_allowed_markets = {
            item.strip().upper()
            for item in os.getenv("MICRO_PROBE_MARKETS", "KR,US").split(",")
            if item.strip()
        }
        self.micro_probe_allowed_modes = {
            item.strip().upper()
            for item in os.getenv("MICRO_PROBE_MODES", "RISK_ON,MILD_BULL,BALANCED").split(",")
            if item.strip()
        }
        self.micro_probe_min_entry_priority = float(os.getenv("MICRO_PROBE_MIN_ENTRY_PRIORITY", "0.45"))
        self.micro_probe_max_oversize_ratio = float(os.getenv("MICRO_PROBE_MAX_OVERSIZE_RATIO", "2.0"))
        self.micro_probe_max_order_krw = float(os.getenv("MICRO_PROBE_MAX_ORDER_KRW", "50000"))
        self.micro_probe_max_daily_trades = int(os.getenv("MICRO_PROBE_MAX_DAILY_TRADES", "2"))
        self.micro_probe_max_open_positions = int(os.getenv("MICRO_PROBE_MAX_OPEN_POSITIONS", "2"))
        self._last_session_date_diag = {"KR": None, "US": None}
        self.gap_pullback_atr_position_sizing = _env_bool("GAP_PULLBACK_ATR_POSITION_SIZING", False)
        self.soft_exit_arbitration_enabled = _env_bool("SOFT_EXIT_ARBITRATION_ENABLED", True)
        self.soft_exit_reference_buffer_pct = float(os.getenv("SOFT_EXIT_REFERENCE_BUFFER_PCT", "0.005"))
        self.soft_exit_min_mfe_pct = float(os.getenv("SOFT_EXIT_MIN_MFE_PCT", "2.5"))
        self.soft_exit_cooldown_sec = int(os.getenv("SOFT_EXIT_COOLDOWN_SEC", "600"))
        self.soft_exit_max_reviews = int(os.getenv("SOFT_EXIT_MAX_REVIEWS", "2"))
        self.atr_target_pct = float(os.getenv("ATR_TARGET_PCT", "0.015"))
        self.enable_dynamic_universe = _env_bool("ENABLE_DYNAMIC_UNIVERSE", False)
        self.dynamic_universe_top_n = int(os.getenv("DYNAMIC_UNIVERSE_TOP_N", "20"))
        self.raw_screen_top_n = {
            "KR": int(os.getenv("KR_SCREEN_TOP_N", str(_RAW_SCREEN_TOP_N_DEFAULTS["KR"]))),
            "US": int(os.getenv("US_SCREEN_TOP_N", str(_RAW_SCREEN_TOP_N_DEFAULTS["US"]))),
        }
        self.dynamic_universe_top_n_by_market = {
            "KR": int(os.getenv("KR_DYNAMIC_UNIVERSE_TOP_N", str(_DYNAMIC_UNIVERSE_TOP_N_DEFAULTS["KR"]))),
            "US": int(os.getenv("US_DYNAMIC_UNIVERSE_TOP_N", str(_DYNAMIC_UNIVERSE_TOP_N_DEFAULTS["US"]))),
        }
        # ?몃젅?쇰쭅 ?ㅽ깙
        self.enable_trailing_stop     = _env_bool("TRAILING_STOP_ENABLED", True)
        self.trailing_stop_pct        = max(0.01, min(0.10, float(os.getenv("TRAILING_STOP_PCT", "3")) / 100))
        self.enable_trailing_analyst  = _env_bool("TRAILING_ANALYST_ENABLED", False)
        # ?붾젅洹몃옩 以묐났 諛⑹???留덉?留??꾩넚 ?곹깭
        self._last_tg_state: dict = {}
        self._last_tg_signal_state: dict = {}
        # 湲닿툒 ?ы뙋??荑⑤떎??(留덉?留??ы샇異??쒕떇 移댁슫?? 60遺?2?ъ씠??媛꾧꺽 ?좎?)
        self._last_reinvoke_tuning: int = -99
        # ???쒖옉 ???ъ???由щ럭 寃곌낵 (session_open?먯꽌 梨꾩? ??startup guard ???ㅽ뻾)
        self._pre_session_sell_queue: dict[str, list] = {"KR": [], "US": []}
        # KR ?μ쨷 ?ㅽ겕由щ떇 寃곌낵 (session_close ??罹먯떆 ??????ㅼ쓬???μ쟾 ?ъ슜)
        self._last_kr_candidates: list = []
        # ?μ쨷 ?대깽??湲곕줉 (?쒕떇/湲닿툒?ы뙋?? ??session_close ??daily_judgment???ы븿
        self._session_events: list = []
        self.decision_event_log: list = []
        self._us_order_supported: set[str] = set()
        self._us_order_blocked: dict[str, str] = {}
        self.pending_orders: list[dict] = []
        # 吏꾩엯 李⑤떒: {ticker: unblock_epoch_sec} ???먯젅 荑⑤떎??+ 以묐났 留ㅼ닔 諛⑹?
        self._entry_blocked: dict[str, float] = {}
        self.claude_control: dict = {}
        # ?뱀씪 ?먯젅 移댁슫??{market: count} ???곗냽 ?먯젅 ??size_mult ?먮룞 異뺤냼
        self._daily_sl_count: dict[str, int] = {"KR": 0, "US": 0}
        # ?대쾲 ?몄뀡?먯꽌 留ㅻ룄 ?꾨즺???곗빱 ??broker sync ?ъ＜??諛⑹?
        self._session_closed_tickers: dict[str, set] = {"KR": set(), "US": set()}
        # WS tick 湲곕컲 ?μ쨷 怨좉?/?媛 ?꾩쟻 ???뱀씪遊?二쇱엯 ???⑥씪媛遊??덉텧???ъ슜
        self._intraday_high: dict[str, float] = {}
        self._intraday_low:  dict[str, float] = {}
        # OR(opening range) state used by KR OR pullback entry.
        self._or_high: dict[str, float] = {}
        self._or_low: dict[str, float] = {}
        self._or_formed: dict[str, bool] = {}
        # 留ㅻ룄 ?ㅽ뙣 荑⑤떎????ticker ???ㅽ뙣 ?쒓컖, 90珥덇컙 ?ъ떆???듭젣
        self._sell_fail_at:  dict[str, float] = {}
        self._sell_fail_meta: dict[str, dict] = {}
        self._exit_process_lock = threading.Lock()
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
        # entry_priority cutoff (Phase 2) ??env濡?ON/OFF, ?붾젅洹몃옩?쇰줈 ?ㅼ떆媛??좉?
        self.entry_priority_cutoff_enabled: bool = (
            os.getenv("ENTRY_PRIORITY_CUTOFF_ENABLED", "true").lower() == "true"
        )
        self.entry_priority_cutoff: float = float(os.getenv("ENTRY_PRIORITY_CUTOFF", "0.20"))
        self._claude_runtime_overrides: dict[str, dict] = {"KR": {}, "US": {}}
        # ticker_selection_log DB ??醫낅ぉ ?좏깮 ?덉쭏 ?꾩쟻 (ML Phase 3 ?숈뒿??
        tsdb.init()
        isdb.init()
        self._tsdb_selection_ids: dict = {"KR": {}, "US": {}}
        # ?? ?덉뒪?좊━ 蹂닿컯 ????????????????????????????????????????????????????
        self._hist_fill_queue: _queue_mod.Queue = _queue_mod.Queue()
        self._hist_fill_queued: set = set()      # ???湲?以?(以묐났 諛⑹?)
        self._hist_fill_inflight: set = set()
        self._hist_fill_last_ts: dict = {}
        _hist_thread = threading.Thread(target=self._history_fill_worker, daemon=True)
        _hist_thread.start()
        # Partial reselect state.
        self._partial_reselect_last: dict = {"KR": None, "US": None}
        self._ticker_no_signal_cycles: dict = {}
        self._ticker_exclude_log: dict = {"KR": [], "US": []}
        self._ticker_runtime_blocked_reasons: dict = {"KR": {}, "US": {}}
        self._ticker_runtime_rejection_reasons: dict = {"KR": {}, "US": {}}
        # ?? continuation entry 1???쒗븳 ?뚮옒洹?(ticker ??bool, ?몄뀡留덈떎 由ъ뀑) ???
        self._continuation_used: dict = {}   # key: ticker, val: True/False
        # ?? 二쇰Ц ?덉쇅(HTTP 500 ?? ?곗냽 移댁슫????3???곗냽 ???뱀씪 李⑤떒 ??????????
        self._order_error_count: dict = {}   # key: ticker, val: int
        # ?? ?쇰꼸 怨꾩륫 移댁슫??(selected?뭩ignaled?뭥rdered?뭚illed) ??????????????
        self._funnel: dict = {
            "KR": {"selected": 0, "signaled": 0, "ordered": 0, "filled": 0,
                   "rejection_reasons": {}, "volume_states": {}},
            "US": {"selected": 0, "signaled": 0, "ordered": 0, "filled": 0,
                   "rejection_reasons": {}, "volume_states": {}},
        }
        # ML DB init
        if _ML_DB_ENABLED:
            try:
                _ml_init_db()
            except Exception as _e:
                log.warning(f"[ML DB] 珥덇린???ㅽ뙣 (遊??숈옉???곹뼢 ?놁쓬): {_e}")
        self._restore_pending_orders()
        self._restore_claude_control()
        self._load_daily_baselines()
        self._sanitize_live_status_file("KR")
        self._sanitize_live_status_file("US")
        # ?ъ떆?????댁썡 ?ъ???蹂듦뎄
        self._restore_positions()
        self.pathb = _PathBRuntime(self, is_paper=is_paper) if _PATHB_RUNTIME_AVAILABLE else None
        if self.pathb is None:
            log.warning("[PathB] runtime unavailable; Claude Price live path disabled")
        else:
            try:
                log.info(f"[PathB] runtime ready: {self.pathb.status()}")
            except Exception:
                log.info("[PathB] runtime ready")
            try:
                self.pathb.recover_on_startup()
            except Exception as _pathb_recover_e:
                log.error(f"[PathB] startup recovery failed: {_pathb_recover_e}", exc_info=True)
        # API ?곌껐 ?곹깭 ?먭?
        self._startup_health_check()
        # Phase1 ?숈뒿 ?곹깭 寃쎄퀬
        try:
            brain = BrainDB.load()
            trained = brain["meta"]["trained_days_kr"] + brain["meta"]["trained_days_us"]
            if trained == 0:
                log.warning("?좑툘  brain.json ?숈뒿 ?곗씠???놁쓬 ??Phase1 ?쒕??덉씠??沅뚯옣:")
                log.warning("   python phase1_trainer/historical_sim.py --market KR --start 2024-10-01")
                log.warning("   python phase1_trainer/historical_sim.py --market US --start 2025-01-01")
        except Exception:
            pass
        log.info(f"init | {'paper' if is_paper else 'live'}")
    def _get_balance_with_token_refresh(self, market: str, *, force_refresh_balance: bool = False) -> dict:
        try:
            return get_balance(self.token, market=market, force_refresh=force_refresh_balance)
        except KISTokenExpiredError as exc:
            log.warning(f"[startup balance] {market} token expired; forcing KIS token refresh: {exc}")
            self.token = get_access_token(force_refresh=True)
            return get_balance(self.token, market=market, force_refresh=True)
    def _enter_market_task(self, market: str, owner: str) -> bool:
        """媛숈? ?쒖옣?먯꽌 ?곸쐞 ?ㅼ?以??묒뾽??寃뱀튂吏 ?딄쾶 留됰뒗??"""
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
                    f"[slow {market}] {owner} {elapsed:.0f}s ?뚯슂 "
                    f"???ㅼ쓬 二쇨린 諛由?媛??(?꾧퀎={_TASK_SLOW_WARN_SEC:.0f}s)"
                )
            self._market_task_owner[market] = None
    def _persist_live_judgment(self, market: str):
        """?μ쨷 ?먮떒 蹂寃쎌쓣 利됱떆 ??ν빐 ?ъ떆????쒕낫?쒓? 理쒖떊 ?곹깭瑜?蹂닿쾶 ?쒕떎."""
        if not self.today_judgment or self.today_judgment.get("market") != market:
            return
        live_path = _judgment_runtime_path(self._mode, self._current_session_date_str(market), market)
        market_key = "US" if str(market).upper() == "US" else "KR"
        persisted_overrides = dict(self.today_judgment.get("claude_runtime_overrides") or {})
        persisted_overrides[market_key] = self._runtime_overrides(market_key)
        try:
            with open(live_path, "w", encoding="utf-8") as f:
                json.dump({
                    **self.today_judgment,
                    "mode": "paper" if self.is_paper else "live",
                    "ticker_reasons": self.today_ticker_reasons.get(market, {}),
                    "selection_meta": self.selection_meta.get(market, {}),
                    "trade_ready_tickers": self.trade_ready_tickers.get(market, []),
                    "claude_runtime_overrides": persisted_overrides,
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"?먮떒 ?꾩떆????ㅽ뙣: {e}")
    def _set_active_session_date(self, market: str, session_date: Optional[date] = None) -> date:
        resolved = session_date or _market_session_date(market)
        if not hasattr(self, "_active_session_date") or not isinstance(self._active_session_date, dict):
            self._active_session_date = {"KR": None, "US": None}
        self._active_session_date[market] = resolved
        return resolved
    def _current_session_date(self, market: str) -> date:
        active = getattr(self, "_active_session_date", {}) or {}
        resolved = active.get(market)
        if resolved is not None and hasattr(resolved, "isoformat"):
            return resolved
        return _market_session_date(market)
    def _current_session_date_str(self, market: str) -> str:
        return self._current_session_date(market).isoformat()
    def _entry_timing_mark_candidates(self, market: str, tickers, source: str) -> None:
        tracker = getattr(self, "entry_timing", None)
        if tracker is None:
            return
        try:
            price_by_ticker = {}
            for ticker in tickers or []:
                key = str(ticker or "").strip().upper() if market == "US" else str(ticker or "").strip()
                if key:
                    price_by_ticker[key] = self.price_cache_raw.get(key) or self.price_cache_raw.get(str(ticker or "").strip())
            tracker.mark_candidates(
                market,
                tickers or [],
                source=source,
                session_date=self._current_session_date_str(market),
                price_by_ticker=price_by_ticker,
            )
        except Exception as exc:
            log.debug(f"[entry_timing] candidate mark failed {market}: {exc}")
    def _entry_timing_signal_check(self, market: str, ticker: str, price: float) -> None:
        tracker = getattr(self, "entry_timing", None)
        if tracker is None:
            return
        try:
            tracker.mark_signal_check(
                market,
                ticker,
                session_date=self._current_session_date_str(market),
                price=price,
            )
            self._audit_emit_price_sample(
                market,
                ticker,
                price=float(price or 0.0),
                source="entry_timing:signal_check",
                decision_id=self._audit_decision_id(market, ticker),
                payload={"stage": "signal_check"},
            )
        except Exception as exc:
            log.debug(f"[entry_timing] signal check mark failed {market} {ticker}: {exc}")
    def _entry_timing_signal_fired(self, market: str, ticker: str, *, price: float, strategy: str, reason: str = "") -> dict:
        tracker = getattr(self, "entry_timing", None)
        if tracker is None:
            return {}
        try:
            snapshot = tracker.mark_signal_fired(
                market,
                ticker,
                session_date=self._current_session_date_str(market),
                price=price,
                strategy=strategy,
                reason=reason,
            )
            self._audit_emit_price_sample(
                market,
                ticker,
                price=float(price or 0.0),
                source="entry_timing:signal_fired",
                decision_id=self._audit_decision_id(market, ticker),
                payload={"strategy": strategy, "reason": reason, "entry_timing": snapshot},
            )
            return snapshot
        except Exception as exc:
            log.debug(f"[entry_timing] signal fired mark failed {market} {ticker}: {exc}")
            return {}
    def _entry_timing_order_sent(self, market: str, ticker: str, *, price: float, order_no: str = "", strategy: str = "", qty: int = 0) -> dict:
        tracker = getattr(self, "entry_timing", None)
        if tracker is None:
            return {}
        try:
            snapshot = tracker.mark_order_sent(
                market,
                ticker,
                session_date=self._current_session_date_str(market),
                price=price,
                order_no=order_no,
                strategy=strategy,
                qty=qty,
                intraday_high=self._intraday_high.get(ticker, 0),
            )
            self._audit_emit_price_sample(
                market,
                ticker,
                price=float(price or 0.0),
                source="entry_timing:order_sent",
                decision_id=self._audit_decision_id(market, ticker),
                payload={"order_no": order_no, "strategy": strategy, "qty": qty, "entry_timing": snapshot},
            )
            return snapshot
        except Exception as exc:
            log.debug(f"[entry_timing] order sent mark failed {market} {ticker}: {exc}")
            return {}
    def _entry_timing_filled(self, market: str, ticker: str, *, fill_price: float, order_no: str = "", qty: int = 0, partial: bool = False) -> dict:
        tracker = getattr(self, "entry_timing", None)
        if tracker is None:
            return {}
        try:
            snapshot = tracker.mark_filled(
                market,
                ticker,
                session_date=self._current_session_date_str(market),
                fill_price=fill_price,
                order_no=order_no,
                qty=qty,
                partial=partial,
            )
            decision_id = self._audit_decision_id(market, ticker)
            self._audit_emit_price_sample(
                market,
                ticker,
                price=float(fill_price or 0.0),
                source="entry_timing:filled",
                decision_id=decision_id,
                payload={"order_no": order_no, "qty": qty, "partial": partial, "entry_timing": snapshot},
            )
            self._audit_try_emit(
                {
                    "kind": "trade_link",
                    "decision_id": decision_id,
                    "order_no": order_no,
                    "entry_price": float(fill_price or 0.0),
                    "payload": {"ticker": ticker, "market": market, "qty": qty, "partial": partial},
                }
            )
            return snapshot
        except Exception as exc:
            log.debug(f"[entry_timing] fill mark failed {market} {ticker}: {exc}")
            return {}
    def _audit_shutdown(self) -> None:
        try:
            writer = getattr(self, "shadow_audit", None)
            if writer is not None:
                writer.stop(timeout=1.5)
        except Exception:
            pass
    def _audit_enabled(self) -> bool:
        try:
            writer = getattr(self, "shadow_audit", None)
            return bool(writer is not None and getattr(writer, "enabled", False))
        except Exception:
            return False
    def _audit_try_emit(self, event: dict) -> bool:
        try:
            return bool(_shadow_try_emit(getattr(self, "shadow_audit", None), event))
        except Exception:
            return False
    def _audit_decision_id(self, market: str, ticker: str) -> str:
        try:
            return str(self._v2_decision_id_for_ticker(market, ticker) or "")
        except Exception:
            return ""
    def _audit_signal_id(
        self,
        market: str,
        ticker: str,
        *,
        strategy: str,
        signal_at: str,
        signal_price: float,
        source: str,
    ) -> str:
        if not self._audit_enabled() or _shadow_make_signal_id is None:
            return ""
        try:
            return str(
                _shadow_make_signal_id(
                    runtime_mode=self._mode,
                    market=market,
                    session_date=self._current_session_date_str(market),
                    ticker=ticker,
                    strategy=strategy,
                    signal_at=signal_at,
                    signal_price=signal_price,
                    source=source,
                )
            )
        except Exception:
            return ""
    def _audit_episode_id(
        self,
        market: str,
        *,
        episode_type: str,
        scope: str,
        ticker: str = "",
        reason: str,
        started_at: str,
    ) -> str:
        if not self._audit_enabled() or _shadow_make_episode_id is None:
            return ""
        try:
            return str(
                _shadow_make_episode_id(
                    runtime_mode=self._mode,
                    market=market,
                    session_date=self._current_session_date_str(market),
                    episode_type=episode_type,
                    scope=scope,
                    ticker=ticker,
                    started_at=started_at,
                    reason=reason,
                )
            )
        except Exception:
            return ""
    def _audit_active_episode(
        self,
        market: str,
        *,
        episode_type: str,
        scope: str,
        reason: str,
        ticker: str = "",
        payload: Optional[dict] = None,
    ) -> str:
        if not self._audit_enabled():
            return ""
        now_iso = datetime.now(KST).isoformat(timespec="seconds")
        key = self._audit_episode_cache_key(
            market,
            episode_type=episode_type,
            scope=scope,
            reason=reason,
            ticker=ticker,
        )
        episode_id = self._shadow_audit_episode_ids.get(key, "")
        if not episode_id:
            episode_id = self._audit_episode_id(
                market,
                episode_type=episode_type,
                scope=scope,
                ticker=ticker,
                reason=reason,
                started_at=now_iso,
            )
            if episode_id:
                self._shadow_audit_episode_ids[key] = episode_id
                self._audit_try_emit(
                    {
                        "kind": "episode",
                        "episode_id": episode_id,
                        "episode_type": episode_type,
                        "market": str(market or "").upper(),
                        "runtime_mode": self._mode,
                        "session_date": self._current_session_date_str(market),
                        "scope": scope,
                        "ticker": ticker,
                        "started_at": now_iso,
                        "status": "open",
                        "start_reason": reason,
                        "payload": payload or {},
                    }
                )
        return episode_id
    def _audit_episode_cache_key(
        self,
        market: str,
        *,
        episode_type: str,
        scope: str,
        reason: str,
        ticker: str = "",
    ) -> str:
        market_key = str(market or "").upper()
        session_date = self._current_session_date_str(market_key)
        ticker_key = str(ticker or "").strip().upper() if market_key == "US" else str(ticker or "").strip()
        return f"{self._mode}:{market_key}:{session_date}:{episode_type}:{scope}:{ticker_key or '*'}:{reason}"
    def _audit_close_active_episode(
        self,
        market: str,
        *,
        episode_type: str,
        scope: str,
        reason: str,
        ticker: str = "",
        clear_reason: str = "",
        payload: Optional[dict] = None,
    ) -> str:
        if not self._audit_enabled():
            return ""
        try:
            key = self._audit_episode_cache_key(
                market,
                episode_type=episode_type,
                scope=scope,
                reason=reason,
                ticker=ticker,
            )
            episode_id = self._shadow_audit_episode_ids.pop(key, "")
            if not episode_id:
                return ""
            market_key = str(market or "").upper()
            self._audit_try_emit(
                {
                    "kind": "episode",
                    "episode_id": episode_id,
                    "episode_type": episode_type,
                    "market": market_key,
                    "runtime_mode": self._mode,
                    "session_date": self._current_session_date_str(market_key),
                    "scope": scope,
                    "ticker": ticker,
                    "ended_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "status": "cleared",
                    "clear_reason": clear_reason,
                    "payload": payload or {},
                }
            )
            return episode_id
        except Exception:
            return ""
    def _audit_emit_signal(
        self,
        market: str,
        ticker: str,
        *,
        strategy: str,
        signal_at: str,
        signal_price: float,
        risk_price_krw: float = 0.0,
        score: float = 0.0,
        decision: str = "",
        block_reason: str = "",
        source: str = "path_a",
        path_type: str = "path_a",
        path_run_id: str = "",
        decision_id: str = "",
        payload: Optional[dict] = None,
    ) -> str:
        if not self._audit_enabled():
            return ""
        signal_id = self._audit_signal_id(
            market,
            ticker,
            strategy=strategy,
            signal_at=signal_at,
            signal_price=signal_price,
            source=source,
        )
        if not signal_id:
            return ""
        bucket = _shadow_minute_bucket(signal_at) if _shadow_minute_bucket is not None else ""
        decision_id = str(decision_id or self._audit_decision_id(market, ticker) or "")
        self._audit_try_emit(
            {
                "kind": "signal",
                "signal_id": signal_id,
                "decision_id": decision_id,
                "path_run_id": path_run_id,
                "market": str(market or "").upper(),
                "runtime_mode": self._mode,
                "session_date": self._current_session_date_str(market),
                "ticker": str(ticker or "").strip().upper() if str(market or "").upper() == "US" else str(ticker or "").strip(),
                "strategy": strategy,
                "path_type": path_type,
                "source": source,
                "signal_at": signal_at,
                "signal_at_bucket": bucket,
                "signal_price": float(signal_price or 0.0),
                "risk_price_krw": float(risk_price_krw or 0.0),
                "score": float(score or 0.0),
                "decision": decision,
                "block_reason": block_reason,
                "payload": payload or {},
            }
        )
        self._audit_try_emit(
            {
                "kind": "signal_event",
                "signal_id": signal_id,
                "event_type": decision or "signal_fired",
                "occurred_at": signal_at,
                "reason_code": block_reason,
                "payload": payload or {},
            }
        )
        self._audit_emit_price_sample(
            market,
            ticker,
            price=float(signal_price or 0.0),
            source=f"{source}:signal",
            signal_id=signal_id,
            decision_id=decision_id,
            path_run_id=path_run_id,
            payload={"strategy": strategy, **(payload or {})},
        )
        return signal_id
    def _audit_emit_price_sample(
        self,
        market: str,
        ticker: str,
        *,
        price: float,
        source: str,
        signal_id: str = "",
        decision_id: str = "",
        path_run_id: str = "",
        episode_id: str = "",
        quality: str = "observed",
        payload: Optional[dict] = None,
    ) -> None:
        if not self._audit_enabled():
            return
        try:
            price_f = float(price or 0.0)
        except Exception:
            return
        if price_f <= 0:
            return
        market_key = str(market or "").upper()
        ticker_key = str(ticker or "").strip().upper() if market_key == "US" else str(ticker or "").strip()
        if not decision_id:
            decision_id = self._audit_decision_id(market_key, ticker_key)
        has_position = any(
            str(pos.get("ticker", "") or "").strip().upper() == ticker_key.upper()
            and str(pos.get("market", market_key) or market_key).upper() == market_key
            for pos in list(getattr(getattr(self, "risk", None), "positions", []) or [])
        )
        has_pending = any(
            str(order.get("ticker", "") or "").strip().upper() == ticker_key.upper()
            and str(order.get("market", market_key) or market_key).upper() == market_key
            for order in list(getattr(self, "pending_orders", []) or [])
        )
        if not any([signal_id, decision_id, path_run_id, episode_id, has_position, has_pending]):
            return
        self._audit_try_emit(
            {
                "kind": "price_sample",
                "market": market_key,
                "runtime_mode": self._mode,
                "session_date": self._current_session_date_str(market_key),
                "ticker": ticker_key,
                "sampled_at": datetime.now(KST).isoformat(timespec="seconds"),
                "price": price_f,
                "source": source,
                "quality": quality,
                "decision_id": decision_id,
                "signal_id": signal_id,
                "path_run_id": path_run_id,
                "episode_id": episode_id,
                "payload": payload or {},
            }
        )
    def _audit_link_signal_episode(self, signal_id: str, episode_id: str, *, reason: str) -> None:
        if not self._audit_enabled() or not signal_id or not episode_id:
            return
        self._audit_try_emit(
            {
                "kind": "episode_link",
                "signal_id": signal_id,
                "episode_id": episode_id,
                "link_reason": reason,
            }
        )
    def _audit_mark_signal_decision(
        self,
        market: str,
        ticker: str,
        *,
        signal_id: str,
        decision: str,
        block_reason: str = "",
        signal_price: float = 0.0,
        risk_price_krw: float = 0.0,
        strategy: str = "",
        score: float = 0.0,
        source: str = "path_a",
        path_type: str = "path_a",
        path_run_id: str = "",
        decision_id: str = "",
        payload: Optional[dict] = None,
    ) -> None:
        if not self._audit_enabled() or not signal_id:
            return
        now_iso = datetime.now(KST).isoformat(timespec="seconds")
        self._audit_try_emit(
            {
                "kind": "signal",
                "signal_id": signal_id,
                "decision_id": str(decision_id or self._audit_decision_id(market, ticker) or ""),
                "path_run_id": path_run_id,
                "market": str(market or "").upper(),
                "runtime_mode": self._mode,
                "session_date": self._current_session_date_str(market),
                "ticker": str(ticker or "").strip().upper() if str(market or "").upper() == "US" else str(ticker or "").strip(),
                "strategy": strategy,
                "path_type": path_type,
                "source": source,
                "signal_at": now_iso,
                "signal_at_bucket": _shadow_minute_bucket(now_iso) if _shadow_minute_bucket is not None else "",
                "signal_price": float(signal_price or 0.0),
                "risk_price_krw": float(risk_price_krw or 0.0),
                "score": float(score or 0.0),
                "decision": decision,
                "block_reason": block_reason,
                "payload": payload or {},
            }
        )
        self._audit_try_emit(
            {
                "kind": "signal_event",
                "signal_id": signal_id,
                "event_type": decision,
                "occurred_at": now_iso,
                "reason_code": block_reason,
                "payload": payload or {},
            }
        )
    def _audit_flush_outcomes(self, market: str, *, force_close: bool = False) -> None:
        if not self._audit_enabled() or _ShadowOutcomeUpdater is None:
            return
        try:
            writer = getattr(self, "shadow_audit", None)
            if writer is not None:
                writer.flush()
                config = getattr(writer, "config", None)
                if config is not None:
                    updater = _ShadowOutcomeUpdater(config.db_path, timeout=getattr(config, "db_timeout_sec", 2.0))
                    updater.update_pending(
                        session_date=self._current_session_date_str(market),
                        market=market,
                        force_close=force_close,
                    )
        except Exception:
            pass
    def _update_session_date_diagnostics(self, market: str, trigger: str = "") -> dict:
        active_date = self._current_session_date(market).isoformat()
        calendar_date = _market_session_date(market).isoformat()
        now_kr = datetime.now(KST).isoformat(timespec="seconds")
        mismatch = active_date != calendar_date
        diag = {
            "trigger": trigger,
            "active_session_date": active_date,
            "calendar_session_date": calendar_date,
            "mismatch": mismatch,
            "updated_at": now_kr,
        }
        self.today_judgment.setdefault("session_date_diagnostics", {})[market] = diag
        if mismatch:
            last_warned = (getattr(self, "_last_session_date_diag", {}) or {}).get(market)
            if last_warned != (active_date, calendar_date, trigger):
                log.warning(
                    f"[session_date drift] {market} trigger={trigger or 'unknown'} "
                    f"active={active_date} calendar={calendar_date} at={now_kr}"
                )
                self._last_session_date_diag[market] = (active_date, calendar_date, trigger)
        return diag
    def _apply_consensus_guards(
        self,
        market: str,
        judgments: dict,
        consensus: dict,
        *,
        source: str = "",
    ) -> dict:
        guarded = apply_unanimous_override(judgments or {}, consensus or {})
        if (
            guarded.get("unanimous_override_applied")
            and (
                guarded.get("mode") != (consensus or {}).get("mode")
                or guarded.get("size") != (consensus or {}).get("size")
            )
        ):
            _src = f" source={source}" if source else ""
            log.warning(
                f"[consensus guard {market}]{_src} "
                f"{(consensus or {}).get('mode')}->{guarded.get('mode')} "
                f"size={(consensus or {}).get('size')}->{guarded.get('size')} "
                f"unanimous={guarded.get('unanimous_direction')}"
            )
        return guarded
    def _ops_review_mode_value(self) -> str:
        return "paper" if getattr(self, "is_paper", True) else "live"
    def _load_recent_judgment_review_records(
        self,
        market: str,
        *,
        days: int = _OPS_REVIEW_DAYS,
        session_limit: int = _OPS_REVIEW_SESSION_LIMIT,
        current_record: Optional[dict] = None,
    ) -> list[dict]:
        end_date = self._current_session_date(market)
        start_date = end_date - timedelta(days=max(days - 1, 0))
        records: list[dict] = []
        seen_dates: set[str] = set()
        def _append_record(raw: Optional[dict]) -> None:
            if not isinstance(raw, dict):
                return
            raw_date = str(raw.get("date") or raw.get("session_date") or "").strip()
            if not raw_date:
                return
            try:
                parsed_date = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
            except ValueError:
                return
            if parsed_date < start_date or parsed_date > end_date:
                return
            if raw_date in seen_dates:
                return
            judgment_eval = raw.get("judgment_eval") or {}
            actual_result = raw.get("actual_result") or {}
            if not judgment_eval or actual_result.get("market_change") is None:
                return
            records.append({
                "date": raw_date,
                "judgment_eval": judgment_eval,
                "actual_result": actual_result,
            })
            seen_dates.add(raw_date)
        _append_record(current_record)
        for fname in sorted(JUDGMENT_DIR.glob(f"{self._mode}_*_{market}.json"), reverse=True):
            try:
                with open(fname, encoding="utf-8-sig") as f:
                    _append_record(json.load(f))
            except Exception:
                continue
            if len(records) >= session_limit:
                break
        return records[:session_limit]
    def _build_ops_review_snapshot(
        self,
        market: str,
        *,
        days: int = _OPS_REVIEW_DAYS,
        session_limit: int = _OPS_REVIEW_SESSION_LIMIT,
        current_record: Optional[dict] = None,
    ) -> dict:
        def _pct(num: float, den: float) -> Optional[float]:
            if not den:
                return None
            return round((float(num) / float(den)) * 100, 1)
        def _metric(value, threshold, *, sample=0, min_sample=0, direction="lt") -> dict:
            breached = False
            if value is not None and sample >= min_sample:
                breached = value < threshold if direction == "lt" else value >= threshold
            return {
                "value": value,
                "threshold": threshold,
                "sample": int(sample or 0),
                "min_sample": int(min_sample or 0),
                "breached": bool(breached),
            }
        mode_value = self._ops_review_mode_value()
        as_of = self._current_session_date_str(market)
        start_date = (self._current_session_date(market) - timedelta(days=max(days - 1, 0))).isoformat()
        judgment_rows = self._load_recent_judgment_review_records(
            market,
            days=days,
            session_limit=session_limit,
            current_record=current_record,
        )
        judgment_sample = len(judgment_rows)
        analyst_hits = {"bull": 0, "bear": 0, "neutral": 0}
        consensus_hits = 0
        unanimous_mismatch_count = 0
        for row in judgment_rows:
            eval_row = row.get("judgment_eval") or {}
            consensus_hits += 1 if eval_row.get("consensus_hit") else 0
            unanimous_mismatch_count += 1 if eval_row.get("unanimous_consensus_mismatch") else 0
            for analyst in analyst_hits:
                analyst_hits[analyst] += 1 if (eval_row.get("analyst_hits") or {}).get(analyst) else 0
        consensus_hit_rate = _pct(consensus_hits, judgment_sample)
        analyst_rates = {
            analyst: _pct(hit_count, judgment_sample)
            for analyst, hit_count in analyst_hits.items()
        }
        best_analyst = max(analyst_hits, key=lambda analyst: analyst_hits[analyst]) if judgment_sample else ""
        best_analyst_hit_rate = analyst_rates.get(best_analyst) if best_analyst else None
        best_analyst_gap = (
            round(float(best_analyst_hit_rate) - float(consensus_hit_rate), 1)
            if best_analyst_hit_rate is not None and consensus_hit_rate is not None
            else None
        )
        selection_stats = {
            "trade_ready_rows": 0,
            "trade_ready_signal_rows": 0,
            "trade_ready_forward_n": 0,
            "trade_ready_forward_avg": None,
            "watch_only_forward_n": 0,
            "watch_only_missed_rows": 0,
            "atr_blocked_rows": 0,
            "atr_blocked_runup_avg": None,
        }
        if os.path.exists(tsdb.DB_PATH):
            try:
                with sqlite3.connect(tsdb.DB_PATH) as conn:
                    conn.row_factory = sqlite3.Row
                    row = conn.execute(
                        """
                        SELECT
                            SUM(CASE WHEN trade_ready=1 THEN 1 ELSE 0 END) AS trade_ready_rows,
                            SUM(CASE WHEN trade_ready=1 AND signal_fired=1 THEN 1 ELSE 0 END) AS trade_ready_signal_rows,
                            SUM(CASE WHEN trade_ready=1 AND forward_3d IS NOT NULL THEN 1 ELSE 0 END) AS trade_ready_forward_n,
                            AVG(CASE WHEN trade_ready=1 THEN forward_3d END) AS trade_ready_forward_avg,
                            SUM(CASE WHEN trade_ready=0 AND forward_3d IS NOT NULL THEN 1 ELSE 0 END) AS watch_only_forward_n,
                            SUM(CASE WHEN trade_ready=0 AND max_runup_3d >= 5.0 THEN 1 ELSE 0 END) AS watch_only_missed_rows,
                            SUM(CASE WHEN blocked_reason='momentum_atr_too_high' AND max_runup_3d IS NOT NULL THEN 1 ELSE 0 END) AS atr_blocked_rows,
                            AVG(CASE WHEN blocked_reason='momentum_atr_too_high' THEN max_runup_3d END) AS atr_blocked_runup_avg
                        FROM ticker_selection_log
                        WHERE bot_mode=? AND market=? AND date>=? AND date<=?
                        """,
                        (mode_value, market, start_date, as_of),
                    ).fetchone()
                if row:
                    selection_stats = {
                        "trade_ready_rows": int(row["trade_ready_rows"] or 0),
                        "trade_ready_signal_rows": int(row["trade_ready_signal_rows"] or 0),
                        "trade_ready_forward_n": int(row["trade_ready_forward_n"] or 0),
                        "trade_ready_forward_avg": round(float(row["trade_ready_forward_avg"]), 3) if row["trade_ready_forward_avg"] is not None else None,
                        "watch_only_forward_n": int(row["watch_only_forward_n"] or 0),
                        "watch_only_missed_rows": int(row["watch_only_missed_rows"] or 0),
                        "atr_blocked_rows": int(row["atr_blocked_rows"] or 0),
                        "atr_blocked_runup_avg": round(float(row["atr_blocked_runup_avg"]), 3) if row["atr_blocked_runup_avg"] is not None else None,
                    }
            except Exception as e:
                log.warning(f"[ops review {market}] selection stats load failed: {e}")
        decision_stats = {
            "blocked_rows": 0,
            "entry_blackout_rows": 0,
            "watch_only_blocked_rows": 0,
            "continuation_trades": 0,
            "continuation_avg_pnl": None,
        }
        if _DECISIONS_DB_PATH.exists():
            try:
                with sqlite3.connect(str(_DECISIONS_DB_PATH)) as conn:
                    conn.row_factory = sqlite3.Row
                    row = conn.execute(
                        """
                        SELECT
                            SUM(CASE WHEN COALESCE(TRIM(block_reason), '') != '' THEN 1 ELSE 0 END) AS blocked_rows,
                            SUM(CASE WHEN block_reason='entry_blackout' THEN 1 ELSE 0 END) AS entry_blackout_rows,
                            SUM(CASE WHEN block_reason='watch_only' THEN 1 ELSE 0 END) AS watch_only_blocked_rows,
                            SUM(CASE WHEN decision='BUY_SIGNAL' AND strategy_used='continuation' AND pnl_pct IS NOT NULL THEN 1 ELSE 0 END) AS continuation_trades,
                            AVG(CASE WHEN decision='BUY_SIGNAL' AND strategy_used='continuation' AND pnl_pct IS NOT NULL THEN pnl_pct END) AS continuation_avg_pnl
                        FROM decisions
                        WHERE data_source=? AND market=? AND session_date>=? AND session_date<=?
                        """,
                        (mode_value, market, start_date, as_of),
                    ).fetchone()
                if row:
                    decision_stats = {
                        "blocked_rows": int(row["blocked_rows"] or 0),
                        "entry_blackout_rows": int(row["entry_blackout_rows"] or 0),
                        "watch_only_blocked_rows": int(row["watch_only_blocked_rows"] or 0),
                        "continuation_trades": int(row["continuation_trades"] or 0),
                        "continuation_avg_pnl": round(float(row["continuation_avg_pnl"]), 3) if row["continuation_avg_pnl"] is not None else None,
                    }
            except Exception as e:
                log.warning(f"[ops review {market}] decision stats load failed: {e}")
        trade_ready_signal_conversion = _pct(
            selection_stats["trade_ready_signal_rows"],
            selection_stats["trade_ready_rows"],
        )
        watch_only_missed_runup_ratio = _pct(
            selection_stats["watch_only_missed_rows"],
            selection_stats["watch_only_forward_n"],
        )
        entry_blackout_ratio = _pct(
            decision_stats["entry_blackout_rows"],
            decision_stats["blocked_rows"],
        )
        watch_only_blocked_ratio = _pct(
            decision_stats["watch_only_blocked_rows"],
            decision_stats["blocked_rows"],
        )
        signal_conversion_threshold = 15.0 if market == "KR" else 10.0
        metrics = {
            "consensus_directional_hit_rate": _metric(consensus_hit_rate, 45.0, sample=judgment_sample, min_sample=1, direction="lt"),
            "best_analyst_minus_consensus_hit_gap": _metric(best_analyst_gap, 10.0, sample=judgment_sample, min_sample=1, direction="ge"),
            "unanimous_mismatch_count": _metric(unanimous_mismatch_count, 1, sample=judgment_sample, min_sample=1, direction="ge"),
            "trade_ready_signal_conversion": _metric(trade_ready_signal_conversion, signal_conversion_threshold, sample=selection_stats["trade_ready_rows"], min_sample=20, direction="lt"),
            "watch_only_missed_runup_ratio": _metric(watch_only_missed_runup_ratio, 30.0, sample=selection_stats["watch_only_forward_n"], min_sample=20, direction="ge"),
            "trade_ready_forward_3d_average": _metric(selection_stats["trade_ready_forward_avg"], 0.0, sample=selection_stats["trade_ready_forward_n"], min_sample=1, direction="lt"),
            "atr_blocked_missed_runup": _metric(selection_stats["atr_blocked_runup_avg"], 4.0, sample=selection_stats["atr_blocked_rows"], min_sample=10, direction="ge"),
            "entry_blackout_ratio": _metric(entry_blackout_ratio, 15.0, sample=decision_stats["blocked_rows"], min_sample=1, direction="ge"),
            "watch_only_blocked_ratio": _metric(watch_only_blocked_ratio, 25.0, sample=decision_stats["blocked_rows"], min_sample=1, direction="ge"),
            "continuation_average_pnl": _metric(decision_stats["continuation_avg_pnl"], -3.0, sample=decision_stats["continuation_trades"], min_sample=5, direction="lt"),
        }
        return {
            "market": market,
            "mode": mode_value,
            "as_of": as_of,
            "window_days": int(days),
            "session_limit": int(session_limit),
            "judgment_sessions": judgment_sample,
            "best_analyst": best_analyst,
            "analyst_hit_rates": analyst_rates,
            "samples": {
                "trade_ready_rows": selection_stats["trade_ready_rows"],
                "trade_ready_forward_n": selection_stats["trade_ready_forward_n"],
                "watch_only_forward_n": selection_stats["watch_only_forward_n"],
                "atr_blocked_rows": selection_stats["atr_blocked_rows"],
                "blocked_rows": decision_stats["blocked_rows"],
                "continuation_trades": decision_stats["continuation_trades"],
            },
            "metrics": metrics,
            "triggers": {
                "low_consensus_hit_rate": metrics["consensus_directional_hit_rate"]["breached"],
                "large_analyst_gap": metrics["best_analyst_minus_consensus_hit_gap"]["breached"],
                "unanimous_mismatch": metrics["unanimous_mismatch_count"]["breached"],
                "low_trade_ready_conversion": metrics["trade_ready_signal_conversion"]["breached"],
                "high_watch_only_missed_runup": metrics["watch_only_missed_runup_ratio"]["breached"],
                "high_atr_blocked_missed_runup": metrics["atr_blocked_missed_runup"]["breached"],
                "high_entry_blackout_ratio": metrics["entry_blackout_ratio"]["breached"],
                "high_watch_only_blocked_ratio": metrics["watch_only_blocked_ratio"]["breached"],
                "weak_trade_ready_forward": metrics["trade_ready_forward_3d_average"]["breached"],
                "weak_continuation_pnl": metrics["continuation_average_pnl"]["breached"],
            },
        }
    def _build_lesson_candidates(self, market: str, ops_review_snapshot: dict) -> list[dict]:
        metrics = dict(ops_review_snapshot.get("metrics") or {})
        candidates: list[dict] = []
        now_iso = datetime.now(KST).isoformat(timespec="seconds")
        expires_at = (datetime.now(KST) + timedelta(days=14)).isoformat(timespec="seconds")
        def _severity(breached: bool, sample: int) -> str:
            if breached and sample >= 20:
                return "high"
            if breached:
                return "medium"
            return "info"
        def _confidence(breached: bool, sample: int) -> float:
            base = 0.35 + min(max(sample, 0), 40) / 100.0
            if breached:
                base += 0.2
            return round(min(0.95, base), 2)
        def _append(metric_key: str, candidate_id: str, scope: str, action: str, summary: str) -> None:
            metric = dict(metrics.get(metric_key) or {})
            value = metric.get("value")
            if value is None:
                return
            sample = int(metric.get("sample") or 0)
            breached = bool(metric.get("breached"))
            candidates.append({
                "id": candidate_id,
                "market": market,
                "scope": scope,
                "action": action,
                "summary": summary,
                "metric_key": metric_key,
                "metric_value": value,
                "sample_count": sample,
                "window_days": int(ops_review_snapshot.get("window_days") or _OPS_REVIEW_DAYS),
                "breached": breached,
                "severity": _severity(breached, sample),
                "confidence": _confidence(breached, sample),
                "generated_at": now_iso,
                "expires_at": expires_at,
            })
        _append(
            "trade_ready_signal_conversion",
            "trade_ready_conversion_review",
            "selection",
            "prompt_hint",
            "trade_ready 종목의 실제 신호 전환율이 낮습니다. trade_ready 기준과 veto 사유를 재검토하세요.",
        )
        _append(
            "watch_only_missed_runup_ratio",
            "watch_only_missed_runup_review",
            "selection",
            "prompt_hint",
            "watch_only에서 놓친 상승 비율이 높습니다. 명확한 veto 없이 watch_only로만 두지 말고 trade_ready 전환 기준을 재검토하세요.",
        )
        _append(
            "continuation_average_pnl",
            "continuation_shadow_review",
            "strategy",
            "strategy_review",
            "continuation 성과가 약합니다. live 진입 확대보다 shadow 관찰을 우선하고 근거를 재검토하세요.",
        )
        _append(
            "unanimous_mismatch_count",
            "unanimous_override_review",
            "consensus",
            "logic_review",
            "분석가 만장일치와 최종 결과 불일치가 감지됐습니다. unanimous override 경로를 재검토하세요.",
        )
        affordability_events = [
            event
            for event in (self.decision_event_log or [])
            if event.get("market") == market and event.get("reason_family") == "affordability"
        ]
        if affordability_events:
            candidates.append({
                "id": "affordability_fail_cluster",
                "market": market,
                "scope": "execution",
                "action": "prompt_hint",
                "summary": "주문 가능 금액/수량 부족이 반복됐습니다. affordability fail 사유를 selection/entry 리뷰에 반영하세요.",
                "metric_key": "affordability_fail_count",
                "metric_value": len(affordability_events),
                "sample_count": len(affordability_events),
                "window_days": 1,
                "breached": len(affordability_events) >= 2,
                "severity": "medium" if len(affordability_events) >= 2 else "info",
                "confidence": round(min(0.9, 0.3 + len(affordability_events) * 0.15), 2),
                "generated_at": now_iso,
                "expires_at": expires_at,
                "examples": [
                    {
                        "ticker": event.get("ticker"),
                        "reason": event.get("reason"),
                        "detail": event.get("detail"),
                    }
                    for event in affordability_events[:5]
                ],
            })
        order = {"high": 3, "medium": 2, "info": 1}
        candidates.sort(key=lambda item: (order.get(item.get("severity", "info"), 0), item.get("confidence", 0.0)), reverse=True)
        return candidates
    def _load_lesson_candidate_summary(
        self,
        market: str,
        max_items: int = 3,
        max_chars: int = 600,
    ) -> str:
        try:
            if not _LESSON_CANDIDATES_PATH.exists():
                return ""
            with open(_LESSON_CANDIDATES_PATH, encoding="utf-8-sig") as f:
                payload = json.load(f)
            market_rows = list((payload.get("markets") or {}).get(market, []) or [])
        except Exception as e:
            log.debug(f"[lesson candidates] summary skipped: {e}")
            return ""
        severity_rank = {"high": 0, "medium": 1, "info": 2, "low": 3}
        today_iso = self._current_session_date_str(market)
        rows = []
        for item in market_rows:
            if not isinstance(item, dict):
                continue
            if not bool(item.get("breached", False)):
                continue
            expires_at = str(item.get("expires_at") or "").strip()
            if expires_at and expires_at[:10] < today_iso:
                continue
            rows.append(item)
        if not rows:
            return ""
        rows.sort(
            key=lambda item: (
                severity_rank.get(str(item.get("severity") or "info").lower(), 9),
                -float(item.get("confidence", 0.0) or 0.0),
                -int(item.get("sample_count", 0) or 0),
            )
        )
        lines = []
        for item in rows[:max_items]:
            summary = str(item.get("summary") or "").strip()
            if not summary:
                continue
            sample_count = int(item.get("sample_count", 0) or 0)
            severity = str(item.get("severity") or "info").lower()
            lines.append(f"- {summary} (n={sample_count}, severity={severity})")
        if not lines:
            return ""
        return ("recent lesson candidates:\n" + "\n".join(lines))[:max_chars]
    def _persist_lesson_candidates(self, market: str, ops_review_snapshot: dict) -> list[dict]:
        candidates = self._build_lesson_candidates(market, ops_review_snapshot)
        store = {"generated_at": datetime.now(KST).isoformat(timespec="seconds"), "markets": {"KR": [], "US": []}}
        try:
            if _LESSON_CANDIDATES_PATH.exists():
                with open(_LESSON_CANDIDATES_PATH, encoding="utf-8-sig") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    store.update(loaded)
                    store.setdefault("markets", {}).setdefault("KR", [])
                    store.setdefault("markets", {}).setdefault("US", [])
        except Exception as e:
            log.warning(f"[lesson candidates] load failed: {e}")
        store["generated_at"] = datetime.now(KST).isoformat(timespec="seconds")
        store.setdefault("markets", {})[market] = candidates
        try:
            with open(_LESSON_CANDIDATES_PATH, "w", encoding="utf-8") as f:
                json.dump(store, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"[lesson candidates] save failed: {e}")
        self.today_judgment["lesson_candidates"] = candidates
        return candidates
    def _screen_top_n_for_market(self, market: str) -> int:
        defaults = getattr(self, "raw_screen_top_n", {}) or {}
        value = defaults.get(market)
        if value is None:
            value = _RAW_SCREEN_TOP_N_DEFAULTS.get(market, 30)
        return max(10, int(value))
    def _dynamic_universe_top_n_for_market(self, market: str) -> int:
        values = getattr(self, "dynamic_universe_top_n_by_market", {}) or {}
        value = values.get(market)
        if value is None:
            value = getattr(self, "dynamic_universe_top_n", _DYNAMIC_UNIVERSE_TOP_N_DEFAULTS.get(market, 20))
        return max(3, int(value))
    def _screen_market_candidates(self, market: str, mode: str) -> list[dict]:
        top_n = self._screen_top_n_for_market(market)
        if market == "KR":
            return screen_market_kr(self.token, top_n=top_n, mode=mode)
        return screen_market_us(top_n=top_n, mode=mode)
    def _trade_ready_slot_config(self, mode: str, market: str = "") -> dict[str, int]:
        family = _mode_family(mode)
        config = dict(_TRADE_READY_SLOT_LIMITS.get(family, _TRADE_READY_SLOT_LIMITS["BALANCED"]))
        market_key = "US" if str(market).upper() == "US" else "KR"
        if market_key == "KR" and getattr(self, "enable_kr_momentum_shrink", True):
            momentum_slots = int(config.get("momentum", 0) or 0)
            if momentum_slots > 0:
                shrink_target = 1 if family == "RISK_ON" else 0
                config["momentum"] = min(momentum_slots, shrink_target)
        if not getattr(self, "enable_continuation_live", False):
            cont_slots = int(config.get("continuation", 0) or 0)
            if cont_slots > 0:
                config["continuation"] = 0
                config["__unassigned__"] = int(config.get("__unassigned__", 0) or 0) + cont_slots
        return config
    def _normalize_selection_meta_runtime(
        self,
        market: str,
        meta: dict,
        selected: list[str],
        mode: str = "",
    ) -> dict:
        normalized_meta = dict(meta or {})
        watchlist = list(dict.fromkeys(normalized_meta.get("watchlist") or selected or []))
        if not watchlist:
            watchlist = list(selected or [])
        normalized_meta["watchlist"] = watchlist
        raw_ready = list(dict.fromkeys(normalized_meta.get("trade_ready") or []))
        normalized_meta["_raw_watchlist"] = list(watchlist)
        normalized_meta["_raw_trade_ready"] = list(raw_ready)
        ready_candidates = [ticker for ticker in raw_ready if ticker in watchlist]
        runtime_filtered: dict[str, str] = {}
        if not getattr(self, "enable_continuation_live", False):
            recommended_map = normalized_meta.get("recommended_strategy") or {}
            filtered_ready: list[str] = []
            for ticker in ready_candidates:
                raw_strategy = ""
                if isinstance(recommended_map, dict):
                    raw_strategy = recommended_map.get(ticker, "")
                    if not raw_strategy and market == "US":
                        for raw_key, raw_value in recommended_map.items():
                            if str(raw_key).upper() == str(ticker).upper():
                                raw_strategy = raw_value
                                break
                if _normalize_strategy_name(raw_strategy) == "continuation":
                    runtime_filtered[ticker] = "continuation_shadow_only"
                    continue
                filtered_ready.append(ticker)
            ready_candidates = filtered_ready
        slot_config = self._trade_ready_slot_config(mode, market)
        total_cap = min(selection_limits(market)["trade_max"], int(slot_config.get("__total__", len(ready_candidates))))
        slot_counts: dict[str, int] = {}
        final_ready: list[str] = []
        recommended_map = normalized_meta.get("recommended_strategy") or {}
        for ticker in ready_candidates:
            raw_strategy = ""
            if isinstance(recommended_map, dict):
                raw_strategy = recommended_map.get(ticker, "")
                if not raw_strategy and market == "US":
                    for raw_key, raw_value in recommended_map.items():
                        if str(raw_key).upper() == str(ticker).upper():
                            raw_strategy = raw_value
                            break
            strategy_name = _normalize_strategy_name(raw_strategy)
            slot_name = strategy_name or "__unassigned__"
            slot_cap = int(slot_config.get(slot_name, slot_config.get("__unassigned__", 0)))
            if slot_cap <= 0:
                runtime_filtered[ticker] = f"slot_disabled:{slot_name}"
                continue
            if slot_counts.get(slot_name, 0) >= slot_cap:
                runtime_filtered[ticker] = f"slot_cap:{slot_name}"
                continue
            if len(final_ready) >= total_cap:
                runtime_filtered[ticker] = "total_cap_reached"
                break
            final_ready.append(ticker)
            slot_counts[slot_name] = slot_counts.get(slot_name, 0) + 1
        normalized_meta["trade_ready"] = final_ready
        normalized_meta["_runtime_filtered_trade_ready"] = runtime_filtered
        if self.today_judgment.get("market") == market:
            normalized_meta["slot_plan"] = {
                key: value
                for key, value in slot_config.items()
                if not str(key).startswith("__")
            }
        return normalized_meta
    def _v2_prompt_version(self) -> str:
        return self.v2.prompt_version() if getattr(self, "v2", None) is not None else str(os.getenv("PROMPT_VERSION", "v2") or "v2")
    def _market_enabled(self, market: str) -> bool:
        return str(market or "").upper() in getattr(self, "enabled_markets", {"KR", "US"})
    def _brain_context_for_judge(self, market: str) -> tuple[str, str]:
        if _v2_fresh_brain_policy_enabled():
            return (
                (
                    f"[{market}] V2 fresh brain start. "
                    "Do not inject legacy v1 brain patterns or correction guide. "
                    "Judge only from the current digest, portfolio state, and deterministic V2 safety constraints. "
                    "Live brain candidates require CLEAN live data after forward measurement."
                ),
                "{}",
            )
        brain_summary = BrainDB.generate_prompt_summary(market)
        brain_data = BrainDB.load()
        correction = json.dumps(
            brain_data.get("correction_guide", {}).get(market, {}),
            ensure_ascii=False,
        )
        return brain_summary, correction
    def _v2_brain_snapshot_id(self, market: str) -> str:
        if getattr(self, "v2", None) is not None:
            return self.v2.brain_snapshot_id(market)
        session_date = self._current_session_date_str(market).replace("-", "")
        return f"brain_{self._mode}_{market}_{session_date}_pending"
    def _v2_register_trade_ready(self, market: str, meta: dict) -> dict[str, str]:
        return self.v2.register_trade_ready(market, meta) if getattr(self, "v2", None) is not None else {}
    def _v2_decision_id_for_ticker(self, market: str, ticker: str) -> str:
        return self.v2.decision_id_for_ticker(market, ticker) if getattr(self, "v2", None) is not None else ""
    def _v2_record_lifecycle_event(
        self,
        event_type: str,
        market: str,
        ticker: str,
        *,
        decision_id: str = "",
        execution_id: str = "",
        position_id: str = "",
        reason_code: str = "",
        payload: Optional[dict] = None,
    ) -> None:
        if getattr(self, "v2", None) is not None:
            self.v2.record_event(
                event_type,
                market,
                ticker,
                decision_id=decision_id,
                execution_id=execution_id,
                position_id=position_id,
                reason_code=reason_code,
                payload=payload,
            )
    def _v2_fixed_size_entry(self, market: str, risk_price_krw: float):
        return self.v2.fixed_size_entry(market, risk_price_krw) if getattr(self, "v2", None) is not None else None
    def _v2_daily_entry_count(self, market: str) -> int:
        return self.v2.daily_entry_count(market) if getattr(self, "v2", None) is not None else 0
    def _v2_max_daily_entries(self) -> Optional[int]:
        return self.v2.max_daily_entries() if getattr(self, "v2", None) is not None else None
    def _v2_order_unknown_blocked(self, market: str, ticker: str) -> bool:
        return self.v2.order_unknown_blocked(market, ticker) if getattr(self, "v2", None) is not None else False
    def _v2_order_unknown_block_state(self, market: str, ticker: str) -> dict:
        if getattr(self, "v2", None) is None:
            return {"blocked": False}
        return self.v2.order_unknown_block_state(market, ticker)
    def _new_buy_block_state(self, market: str, ticker: str = "", strategy: str = "") -> dict:
        market_key = str(market or "").upper()
        raw_ticker = str(ticker or "").strip()
        ticker_key = raw_ticker.upper() if market_key == "US" else raw_ticker
        details = {
            "market": market_key,
            "ticker": ticker_key,
            "strategy": str(strategy or ""),
            "checked_at": datetime.now(KST).isoformat(),
        }
        if not self._is_order_allowed_now(market_key):
            return {
                "allowed": False,
                "blocked": True,
                "reason": "MARKET_CLOSED",
                "scope": "market",
                "details": details,
            }
        if self._in_entry_blackout(market_key):
            return {
                "allowed": False,
                "blocked": True,
                "reason": "ENTRY_BLACKOUT",
                "scope": "market",
                "details": details,
            }
        unknown = getattr(self, "v2_order_unknown", None)
        if unknown is not None:
            try:
                if bool(unknown.should_block_global()):
                    return {
                        "allowed": False,
                        "blocked": True,
                        "reason": "ORDER_UNKNOWN_UNRESOLVED",
                        "scope": "global",
                        "details": details,
                    }
            except Exception:
                pass
            try:
                if bool(unknown.should_block_market(market_key)):
                    return {
                        "allowed": False,
                        "blocked": True,
                        "reason": "ORDER_UNKNOWN_UNRESOLVED",
                        "scope": "market",
                        "details": details,
                    }
            except Exception:
                pass
        if ticker_key:
            state = self._v2_order_unknown_block_state(market_key, ticker_key)
            if bool((state or {}).get("blocked")):
                return {
                    "allowed": False,
                    "blocked": True,
                    "reason": str((state or {}).get("reason") or "ORDER_UNKNOWN_UNRESOLVED"),
                    "scope": str((state or {}).get("scope") or "ticker"),
                    "details": {**details, "unknown_state": state},
                }
        return {"allowed": True, "blocked": False, "reason": "", "scope": "", "details": details}
    def _record_new_buy_block(
        self,
        market: str,
        ticker: str,
        strategy: str,
        state: dict,
        *,
        qty: int = 0,
        price_native: float = 0.0,
        price_krw: float = 0.0,
        selected_reason: str = "",
        stage: str = "new_buy_gate",
        tsdb_id: Optional[int] = None,
        entry_priority: float = 0.0,
        signal_at: str = "",
        signal_row: Optional[dict] = None,
    ) -> None:
        market_key = str(market or "").upper()
        ticker_key = str(ticker or "").strip()
        reason = str((state or {}).get("reason") or "NEW_BUY_BLOCKED")
        scope = str((state or {}).get("scope") or "")
        details = dict((state or {}).get("details") or {})
        details.update({"stage": stage, "scope": scope})
        if ticker_key:
            self._bump_runtime_reason(market_key, ticker_key, reason)
            self._record_decision_event(
                market_key,
                "buy_blocked",
                ticker_key,
                strategy=strategy,
                qty=int(qty or 0),
                price_native=float(price_native or 0.0),
                price_krw=float(price_krw or 0.0),
                reason=reason,
                reason_family="new_buy_gate",
                detail=f"{stage}:{scope}",
                selected_reason=selected_reason,
            )
            self._v2_record_lifecycle_event(
                "SAFETY_BLOCKED",
                market_key,
                ticker_key,
                reason_code=reason,
                payload=details,
            )
            if float(price_native or 0.0) > 0:
                _audit_signal_id = self._audit_emit_signal(
                    market_key,
                    ticker_key,
                    strategy=strategy,
                    signal_at=signal_at or datetime.now(KST).isoformat(timespec="seconds"),
                    signal_price=float(price_native or 0.0),
                    risk_price_krw=float(price_krw or 0.0),
                    score=float(entry_priority or 0.0),
                    decision="BLOCKED",
                    block_reason=reason,
                    source=stage,
                    payload={"new_buy_gate": details, "signal_row": signal_row or {}},
                )
                if reason == "ORDER_UNKNOWN_UNRESOLVED":
                    _episode_id = self._audit_active_episode(
                        market_key,
                        episode_type="ORDER_UNKNOWN_PAUSE",
                        scope=scope or "market",
                        reason=reason,
                        ticker=ticker_key if scope == "ticker" else "",
                        payload=details,
                    )
                    self._audit_link_signal_episode(_audit_signal_id, _episode_id, reason="ORDER_UNKNOWN_SIGNAL_BLOCKED")
        log.warning(f"[NEW_BUY_BLOCKED] {market_key} {ticker_key or '*'} {strategy} {reason} scope={scope}")
        if signal_row is not None and _ML_DB_ENABLED:
            try:
                _ml_write_eval(
                    ticker_key,
                    float(price_native or 0.0),
                    signal_row,
                    "BLOCKED",
                    block_reason_=reason,
                    strategy_used_=strategy,
                    fired_strategy_=strategy,
                    diag_json_=details,
                )
            except Exception:
                pass
        if tsdb_id is not None:
            try:
                tsdb.update_signal(tsdb_id, strategy, float(entry_priority or 0.0), signal_at, reason)
            except Exception:
                pass
    def _v2_arbitrate_path_a_entry(
        self,
        market: str,
        ticker: str,
        *,
        current_price: Optional[float] = None,
        strategy: str = "",
    ):
        if getattr(self, "v2", None) is None:
            return None
        return self.v2.arbitrate_path_a_entry(
            market,
            ticker,
            current_price=current_price,
            strategy=strategy,
        )
    def _v2_same_day_reentry_decision(self, market: str, ticker: str):
        if getattr(self, "v2", None) is None:
            return None
        return self.v2.same_day_reentry_decision(market, ticker)
    def _v2_safety_decision(
        self,
        market: str,
        ticker: str,
        *,
        risk_price_krw: float,
        qty: int,
        order_cost_krw: float,
        min_order_krw: float,
    ):
        if getattr(self, "v2", None) is None:
            return None
        return self.v2.safety_decision(
            market,
            ticker,
            risk_price_krw=risk_price_krw,
            qty=qty,
            order_cost_krw=order_cost_krw,
            min_order_krw=min_order_krw,
        )
    def _v2_record_order_unknown(self, market: str, ticker: str, order: dict, detail: str) -> None:
        if getattr(self, "v2", None) is not None:
            self.v2.record_order_unknown(market, ticker, order, detail)
    def _candidate_health_tracker(self, market: str) -> CandidateHealthTracker:
        market_key = "US" if str(market or "").upper() == "US" else "KR"
        session_date = self._current_session_date_str(market_key)
        if not hasattr(self, "candidate_health_trackers"):
            self.candidate_health_trackers = {"KR": None, "US": None}
        tracker = self.candidate_health_trackers.get(market_key)
        if tracker is None or tracker.session_date != session_date:
            tracker = CandidateHealthTracker(market_key, session_date)
            self.candidate_health_trackers[market_key] = tracker
        return tracker
    def _candidate_health_price_map(self, market: str, candidates: list[dict]) -> dict[str, float]:
        market_key = "US" if str(market or "").upper() == "US" else "KR"
        def _num(value) -> float:
            try:
                return float(str(value or "").replace(",", ""))
            except Exception:
                return 0.0
        price_map: dict[str, float] = {}
        for row in candidates or []:
            if not isinstance(row, dict):
                continue
            ticker = _candidate_health_ticker(market_key, row.get("ticker"))
            if not ticker:
                continue
            price = 0.0
            for key in ("price", "current_price", "last_price", "close"):
                price = _num(row.get(key))
                if price > 0:
                    break
            if price > 0:
                price_map[ticker] = price
        for raw_ticker, raw_price in (getattr(self, "price_cache_raw", {}) or {}).items():
            ticker = _candidate_health_ticker(market_key, raw_ticker)
            price = _num(raw_price)
            if ticker and price > 0:
                price_map.setdefault(ticker, price)
        if market_key == "KR":
            for raw_ticker, raw_price in (getattr(self, "price_cache", {}) or {}).items():
                ticker = _candidate_health_ticker(market_key, raw_ticker)
                price = _num(raw_price)
                if ticker and price > 0:
                    price_map.setdefault(ticker, price)
        return price_map
    def _update_candidate_health(
        self,
        market: str,
        phase: str,
        selected: list[str],
        selection_meta: dict,
        candidates: list[dict],
    ) -> None:
        if not _env_bool("CANDIDATE_HEALTH_ENABLED", True):
            return
        market_key = "US" if str(market or "").upper() == "US" else "KR"
        try:
            tracker = self._candidate_health_tracker(market_key)
            watchlist = list((selection_meta or {}).get("watchlist") or selected or [])
            trade_ready = list((selection_meta or {}).get("trade_ready") or [])
            price_map = self._candidate_health_price_map(market_key, candidates)
            states = tracker.update_selection(
                watchlist=watchlist,
                trade_ready=trade_ready,
                price_by_ticker=price_map,
                phase=phase,
                now=datetime.now(KST),
            )
            counts = tracker.state_counts(states)
            log.info(
                f"[candidate_health] {market_key} {phase} "
                f"updated={len(states)} states={counts} file={tracker.path.name}"
            )
            analysis_log.info(
                f"[candidate_health] {market_key} {phase} states={counts}",
                extra={"extra": {
                    "event": "candidate_health_update",
                    "market": market_key,
                    "phase": phase,
                    "session_date": tracker.session_date,
                    "updated": len(states),
                    "state_counts": counts,
                    "path": str(tracker.path),
                }},
            )
            for state in tracker.interesting_states(states):
                log.info(
                    f"[candidate_health] {market_key} {state.get('ticker')} "
                    f"{state.get('health_state')} ready={state.get('ready_count', 0)} "
                    f"current={state.get('current_vs_first_ready_pct')} "
                    f"mae={state.get('mae_pct')} mfe={state.get('mfe_pct')} "
                    f"recovered={bool(state.get('recovered_first_ready'))}"
                )
        except Exception as exc:
            log.warning(f"[candidate_health] update failed {market_key} {phase}: {exc}")
    def _apply_selection_meta(self, market: str, selected: list[str]) -> dict:
        """Persist Claude WATCH/TRADE_READY split while keeping legacy tickers intact."""
        raw_meta = dict(get_last_selection_meta() or {})
        meta = dict(raw_meta)
        if not meta.get("watchlist"):
            meta = {
                "watchlist": list(selected or []),
                "trade_ready": list(selected or [])[:10],
                "reasons": {},
                "veto": {},
                "risk_tags": {},
                "recommended_strategy": {},
                "max_position_pct": {},
                "allocation_intent": {},
                "max_order_cap_pct": {},
                "risk_budget_pct": {},
                "size_reason": {},
            }
        mode = str((self.today_judgment or {}).get("consensus", {}).get("mode", "") or "")
        meta = self._normalize_selection_meta_runtime(market, meta, selected, mode=mode)
        stages = {
            "raw": {
                "watchlist": list(dict.fromkeys(raw_meta.get("watchlist") or selected or [])),
                "trade_ready": list(dict.fromkeys(raw_meta.get("trade_ready") or [])),
            },
            "normalized": {
                "watchlist": list(meta.get("watchlist") or []),
                "trade_ready": list(meta.get("trade_ready") or []),
                "runtime_filtered": dict(meta.get("_runtime_filtered_trade_ready") or {}),
            },
            "applied": {
                "selected": list(selected or []),
                "trade_ready": list(meta.get("trade_ready") or []),
            },
        }
        self.selection_stages[market] = stages
        log.info(
            f"[selection normalize] {market} "
            f"raw_trade={stages['raw']['trade_ready']} "
            f"normalized_trade={stages['normalized']['trade_ready']} "
            f"applied_trade={stages['applied']['trade_ready']}"
        )
        analysis_log.info(
            f"[selection normalize] {market} raw={stages['raw']['trade_ready']} "
            f"normalized={stages['normalized']['trade_ready']} "
            f"applied={stages['applied']['trade_ready']}",
            extra={"extra": {
                "event": "selection_normalized",
                "market": market,
                "stages": stages,
            }},
        )
        self.selection_meta[market] = meta
        self.trade_ready_tickers[market] = list(meta.get("trade_ready") or [])
        v2_ids = self._v2_register_trade_ready(market, meta)
        if v2_ids:
            meta["v2_decision_ids"] = v2_ids
            self.selection_meta[market] = meta
        if getattr(self, "pathb", None) is not None:
            try:
                pathb_runs = self.pathb.register_from_selection_meta(market, meta)
                if pathb_runs:
                    meta["pathb_run_ids"] = pathb_runs
                    self.selection_meta[market] = meta
            except Exception as _pathb_e:
                log.error(f"[PathB] plan registration failed {market}: {_pathb_e}", exc_info=True)
        if self.today_judgment.get("market") == market:
            self.today_judgment["selection_meta"] = meta
            self.today_judgment["trade_ready_tickers"] = self.trade_ready_tickers[market]
            self.today_judgment["selection_stages"] = stages
        return meta
    def _trade_ready_set(self, market: str) -> set[str]:
        raw = self.trade_ready_tickers.get(market, []) or []
        if market == "US":
            return {str(t).upper() for t in raw}
        return {str(t) for t in raw}
    def _build_portfolio_info(self) -> dict[str, int]:
        return {
            "cash": round(self.risk.cash),
            "total_equity": round(self._kis_total_equity_krw()),
            "max_order_krw": round(self.risk.max_order_krw),
            "n_positions": len(self.risk.positions),
            "max_positions": HARD_RULES["max_positions"],
        }
    def _selection_meta_value(self, market: str, ticker: str, key: str):
        meta = self.selection_meta.get(market, {}) or {}
        values = meta.get(key) or {}
        if not isinstance(values, dict):
            return None
        lookup = str(ticker).upper() if market == "US" else str(ticker)
        if lookup in values:
            return values.get(lookup)
        if market == "US":
            for raw_key, raw_value in values.items():
                if str(raw_key).upper() == lookup:
                    return raw_value
        return None
    def _merge_selection_price_targets(
        self,
        market: str,
        final_trade_ready: list[str],
        *target_maps: dict,
    ) -> dict:
        merged: dict[str, dict] = {}
        market_key = str(market or "").upper()
        def _norm(value: str) -> str:
            text = str(value or "").strip()
            return text.upper() if market_key == "US" else text
        for target_map in target_maps:
            if not isinstance(target_map, dict):
                continue
            for raw_key, raw_plan in target_map.items():
                key = _norm(raw_key)
                if key and isinstance(raw_plan, dict):
                    merged[key] = dict(raw_plan)
        final_targets: dict[str, dict] = {}
        for ticker in final_trade_ready or []:
            key = _norm(ticker)
            if key in merged:
                final_targets[key] = merged[key]
        return final_targets
    @staticmethod
    def _float_or_zero(value) -> float:
        try:
            return float(value or 0)
        except Exception:
            return 0.0
    def _selection_reference_for_ticker(self, market: str, ticker: str) -> dict:
        raw = self._selection_meta_value(market, ticker, "price_targets")
        if not isinstance(raw, dict):
            return {}
        target = self._float_or_zero(
            raw.get("sell_target")
            or raw.get("target")
            or raw.get("target_price")
            or raw.get("take_profit")
        )
        if target <= 0:
            return {}
        stop = self._float_or_zero(raw.get("stop_loss") or raw.get("stop") or raw.get("stop_price"))
        confidence = self._float_or_zero(raw.get("confidence") or raw.get("conf"))
        return {
            "selection_reference_target": target,
            "selection_reference_stop": stop,
            "selection_reference_confidence": confidence,
            "selection_reference_source": "selection_meta_price_targets",
            "selection_reference_copied_at": datetime.now(KST).isoformat(timespec="seconds"),
        }
    def _cancelled_pathb_reference_for_ticker(self, market: str, ticker: str) -> dict:
        pathb = getattr(self, "pathb", None)
        store = getattr(pathb, "store", None)
        if store is None:
            return {}
        market_key = str(market or "").upper()
        ticker_key = str(ticker or "").strip().upper() if market_key == "US" else str(ticker or "").strip()
        try:
            runs = store.path_runs_for_session(
                market=market_key,
                runtime_mode=str(getattr(pathb, "mode", getattr(self, "_mode", "")) or ""),
                session_date=self._current_session_date_str(market_key),
                status="CANCELLED",
                path_type="claude_price",
            )
        except Exception as exc:
            log.debug(f"[soft exit ref] PathB lookup failed {market_key} {ticker_key}: {exc}")
            return {}
        for run in reversed(list(runs or [])):
            run_ticker = str(run.get("ticker", "") or "").strip().upper() if market_key == "US" else str(run.get("ticker", "") or "").strip()
            if run_ticker != ticker_key:
                continue
            plan = run.get("plan") or {}
            if not isinstance(plan, dict):
                continue
            target = self._float_or_zero(plan.get("sell_target"))
            if target <= 0:
                continue
            return {
                "pathb_reference_target": target,
                "pathb_reference_stop": self._float_or_zero(plan.get("stop_loss")),
                "pathb_reference_confidence": self._float_or_zero(plan.get("confidence")),
                "pathb_reference_status": str(plan.get("cancel_reason") or run.get("status") or "CANCELLED"),
                "pathb_reference_path_run_id": str(run.get("path_run_id", "") or plan.get("path_run_id", "") or ""),
                "pathb_reference_source": "cancelled_same_day_pathb",
                "pathb_reference_copied_at": datetime.now(KST).isoformat(timespec="seconds"),
            }
        return {}
    def _entry_reference_metadata(self, market: str, ticker: str) -> dict:
        meta: dict = {}
        meta.update(self._selection_reference_for_ticker(market, ticker))
        meta.update(self._cancelled_pathb_reference_for_ticker(market, ticker))
        return meta
    def _recommended_strategy_for_ticker(self, market: str, ticker: str) -> str:
        return _normalize_strategy_name(
            self._selection_meta_value(market, ticker, "recommended_strategy") or ""
        )
    def _risk_tags_for_ticker(self, market: str, ticker: str) -> list[str]:
        raw = self._selection_meta_value(market, ticker, "risk_tags")
        if isinstance(raw, (list, tuple, set)):
            return [str(tag).strip() for tag in raw if str(tag).strip()]
        if raw:
            value = str(raw).strip()
            return [value] if value else []
        return []
    def _selection_meta_flag(self, market: str, key: str):
        meta = self.selection_meta.get(market, {}) or {}
        return meta.get(key)
    def _watch_only_text_has_any(self, text: str, keywords: tuple[str, ...]) -> bool:
        haystack = str(text or "").strip()
        if not haystack:
            return False
        lower_haystack = haystack.lower()
        for keyword in keywords:
            raw = str(keyword or "").strip()
            if not raw:
                continue
            if raw in haystack or raw.lower() in lower_haystack:
                return True
        return False
    def _watch_only_bucket(self, market: str, ticker: str) -> str:
        fallback_mode = str(self._selection_meta_flag(market, "_fallback_mode") or "").strip()
        if bool(self._selection_meta_flag(market, "_parse_recovered")) or fallback_mode in _WATCH_ONLY_RECOVERY_FALLBACKS:
            return "RECOVERY"
        reason_map = self.today_ticker_reasons.get(market, {}) or {}
        lookup = str(ticker).upper() if market == "US" else str(ticker)
        selected_reason = str(reason_map.get(lookup, "") or reason_map.get(ticker, "") or "").strip()
        if market == "US" and not selected_reason:
            for raw_key, raw_value in reason_map.items():
                if str(raw_key).upper() == lookup:
                    selected_reason = str(raw_value or "").strip()
                    break
        veto_reason = str(self._selection_meta_value(market, ticker, "veto") or "").strip()
        risk_tags = self._risk_tags_for_ticker(market, ticker)
        combined = " | ".join(filter(None, [selected_reason, veto_reason, " ".join(risk_tags)]))
        if veto_reason:
            return "HARD"
        if self._watch_only_text_has_any(combined, _WATCH_ONLY_HARD_KEYWORDS):
            return "HARD"
        if self._watch_only_text_has_any(combined, _WATCH_ONLY_SOFT_KEYWORDS):
            return "SOFT"
        if selected_reason or risk_tags:
            return "SOFT"
        if not self._trade_ready_set(market):
            return "RECOVERY"
        return "SOFT"
    def _can_recheck_soft_watch_only(self, market: str, ticker: str, mode: str) -> bool:
        if not getattr(self, "enable_soft_watch_promotion", False):
            return False
        if self._watch_only_bucket(market, ticker) != "SOFT":
            return False
        if str(mode or "").upper() in {"DEFENSIVE", "HALT"}:
            return False
        return True
    def _promote_trade_ready_ticker(
        self,
        market: str,
        ticker: str,
        strategy_name: str = "",
        trigger: str = "soft_watch_signal",
    ) -> bool:
        normalized = str(ticker).upper() if market == "US" else str(ticker)
        if normalized in self._trade_ready_set(market):
            return False
        meta = dict(self.selection_meta.get(market) or {})
        meta_ready = [normalized] + [t for t in (meta.get("trade_ready") or []) if t != normalized]
        meta["trade_ready"] = meta_ready
        normalized_strategy = _normalize_strategy_name(strategy_name)
        if normalized_strategy:
            recommended = dict(meta.get("recommended_strategy") or {})
            recommended[normalized] = normalized_strategy
            meta["recommended_strategy"] = recommended
        mode = str((self.today_judgment or {}).get("consensus", {}).get("mode", "") or "")
        meta = self._normalize_selection_meta_runtime(
            market,
            meta,
            list(meta.get("watchlist") or []),
            mode=mode,
        )
        if normalized not in set(meta.get("trade_ready") or []):
            return False
        self.selection_meta[market] = meta
        self.trade_ready_tickers[market] = list(meta.get("trade_ready") or [])
        self.today_judgment["selection_meta"] = meta
        self.today_judgment["trade_ready_tickers"] = self.trade_ready_tickers[market]
        analysis_log.info(
            f"[trade_ready promote] {market} {normalized} trigger={trigger}",
            extra={"extra": {
                "event": "trade_ready_promote",
                "market": market,
                "ticker": normalized,
                "strategy": strategy_name,
                "trigger": trigger,
                "trade_ready": list(self.trade_ready_tickers[market]),
            }},
        )
        log.info(
            f"[trade_ready ?밴꺽] {market} {normalized}"
            + (f" strategy={strategy_name}" if strategy_name else "")
            + f" trigger={trigger}"
        )
        try:
            self._persist_live_judgment(market)
        except Exception:
            pass
        return True
    def _selection_replace_candidates(self, selection_meta: dict, selected: list[str]) -> list[str]:
        trade_ready = selection_meta.get("trade_ready")
        if isinstance(trade_ready, list):
            return list(trade_ready)
        return list(selected or [])
    def _pick_partial_replace_in(
        self,
        market: str,
        replace_out: list[str],
        candidate_trade_ready: list[str],
        candidate_meta: dict,
        candidate_map: dict[str, dict],
        n_replace: int,
    ) -> list[str]:
        remaining = list(dict.fromkeys(candidate_trade_ready or []))
        if not remaining:
            return []
        selected: list[str] = []
        selected_strategies: set[str] = set()
        selected_categories: set[str] = set()
        selected_sectors: set[str] = set()
        selected_pullbacks: set[str] = set()
        target_strategies = [
            self._recommended_strategy_for_ticker(market, ticker)
            for ticker in replace_out
        ]
        def _meta_strategy(ticker: str) -> str:
            recommended = candidate_meta.get("recommended_strategy") or {}
            raw_value = ""
            if isinstance(recommended, dict):
                raw_value = recommended.get(ticker, "")
                if not raw_value and market == "US":
                    for raw_key, raw_candidate in recommended.items():
                        if str(raw_key).upper() == str(ticker).upper():
                            raw_value = raw_candidate
                            break
            return _normalize_strategy_name(raw_value)
        def _score(ticker: str, target_strategy: str = "") -> float:
            candidate = candidate_map.get(ticker, {}) or {}
            strategy_name = _meta_strategy(ticker)
            category = str(candidate.get("category", "") or "").strip().lower()
            sector = str(candidate.get("sector", "") or "").strip().lower()
            pullback = str(candidate.get("from_high_bucket", "") or "").strip().lower()
            liq = str(candidate.get("liquidity_bucket", "") or "").strip().lower()
            score = 0.0
            if target_strategy:
                if strategy_name == target_strategy:
                    score += 4.0
                else:
                    score -= 1.0
            if strategy_name and strategy_name not in selected_strategies:
                score += 2.0
            if category and category not in selected_categories:
                score += 1.0
            if sector and sector not in selected_sectors:
                score += 1.0
            if pullback and pullback not in selected_pullbacks:
                score += 0.5
            if liq == "low":
                score -= 1.0
            return score
        def _accept(ticker: str) -> None:
            candidate = candidate_map.get(ticker, {}) or {}
            strategy_name = _meta_strategy(ticker)
            if strategy_name:
                selected_strategies.add(strategy_name)
            category = str(candidate.get("category", "") or "").strip().lower()
            sector = str(candidate.get("sector", "") or "").strip().lower()
            pullback = str(candidate.get("from_high_bucket", "") or "").strip().lower()
            if category:
                selected_categories.add(category)
            if sector:
                selected_sectors.add(sector)
            if pullback:
                selected_pullbacks.add(pullback)
            selected.append(ticker)
        for target_strategy in target_strategies:
            if len(selected) >= n_replace or not remaining:
                break
            best = max(remaining, key=lambda ticker: _score(ticker, target_strategy))
            remaining.remove(best)
            _accept(best)
        while len(selected) < n_replace and remaining:
            best = max(remaining, key=lambda ticker: _score(ticker, ""))
            remaining.remove(best)
            _accept(best)
        return selected[:n_replace]
    def _watch_only_reason_text(self, market: str, ticker: str) -> str:
        parts: list[str] = []
        reason_map = self.today_ticker_reasons.get(market, {}) or {}
        lookup = str(ticker).upper() if market == "US" else str(ticker)
        selected_reason = str(reason_map.get(lookup, "") or reason_map.get(ticker, "") or "").strip()
        if market == "US" and not selected_reason:
            for raw_key, raw_value in reason_map.items():
                if str(raw_key).upper() == lookup:
                    selected_reason = str(raw_value or "").strip()
                    break
        if selected_reason:
            parts.append(selected_reason)
        veto_reason = str(self._selection_meta_value(market, ticker, "veto") or "").strip()
        if veto_reason:
            parts.append(f"제외사유 {veto_reason}")
        risk_tags = self._risk_tags_for_ticker(market, ticker)
        if risk_tags:
            parts.append(f"리스크 {', '.join(risk_tags[:3])}")
        recommended = self._recommended_strategy_for_ticker(market, ticker)
        if recommended:
            parts.append(f"권장전략 {recommended}")
        raw_cap = (
            self._selection_meta_value(market, ticker, "max_order_cap_pct")
            or self._selection_meta_value(market, ticker, "max_position_pct")
        )
        try:
            cap_pct = float(raw_cap)
        except (TypeError, ValueError):
            cap_pct = None
        if cap_pct is not None and cap_pct > 0:
            parts.append(f"비중상한 {int(round(cap_pct))}%")
        intent = self._selection_meta_value(market, ticker, "allocation_intent")
        if intent:
            parts.append(f"allocation_intent={intent}")
        if parts:
            return " | ".join(parts)
        bucket = self._watch_only_bucket(market, ticker)
        if bucket == "RECOVERY":
            return "selection 복구 상태 - trade_ready 확정 전"
        if bucket == "HARD":
            return "명시적 제외 사유 - watch_only 유지"
        if not self._trade_ready_set(market):
            return "trade_ready 비어 있음 - watch_only 유지"
        return "not trade_ready - keep watch_only"
    def _selection_size_cap_pct(self, market: str, ticker: str) -> tuple[Optional[int], list[str]]:
        caps: list[int] = []
        reasons: list[str] = []
        raw_max_order_cap_pct = self._selection_meta_value(market, ticker, "max_order_cap_pct")
        raw_legacy_max_position_pct = self._selection_meta_value(market, ticker, "max_position_pct")
        raw_max_position_pct = raw_max_order_cap_pct or raw_legacy_max_position_pct
        try:
            max_position_pct = float(raw_max_position_pct)
        except (TypeError, ValueError):
            max_position_pct = None
        if max_position_pct is not None and max_position_pct > 0:
            cap = max(1, min(100, int(round(max_position_pct))))
            caps.append(cap)
            reason_key = "max_order_cap_pct" if raw_max_order_cap_pct not in (None, "") else "max_position_pct"
            reasons.append(f"{reason_key}={cap}%")
        risk_tags = self._risk_tags_for_ticker(market, ticker)
        risk_tag_cap = None
        for tag in risk_tags:
            for keywords, candidate_cap in _SELECTION_RISK_TAG_CAPS:
                if any(keyword in tag for keyword in keywords):
                    risk_tag_cap = candidate_cap if risk_tag_cap is None else min(risk_tag_cap, candidate_cap)
                    break
        if risk_tag_cap is not None:
            caps.append(risk_tag_cap)
            reasons.append(f"risk_tags={','.join(risk_tags)}")
        return (min(caps), reasons) if caps else (None, reasons)
    def _prioritize_strategy_order(self, market: str, ticker: str, base_order: list[str]) -> list[str]:
        order = list(base_order or [])
        recommended = self._recommended_strategy_for_ticker(market, ticker)
        if not recommended:
            return order
        return [recommended] + [strategy for strategy in order if strategy != recommended]
    def _is_trade_ready_ticker(self, market: str, ticker: str) -> bool:
        normalized = str(ticker).upper() if market == "US" else str(ticker)
        return normalized in self._trade_ready_set(market)
    def _runtime_overrides(self, market: str) -> dict:
        raw_map = getattr(self, "_claude_runtime_overrides", None)
        if not isinstance(raw_map, dict):
            raw_map = {"KR": {}, "US": {}}
            self._claude_runtime_overrides = raw_map
        market_key = "US" if str(market).upper() == "US" else "KR"
        raw = dict(raw_map.get(market_key, {}) or {})
        normalized = {}
        for key, (low, high) in _CLAUDE_RUNTIME_OVERRIDE_BOUNDS.items():
            try:
                value = float(raw.get(key, 0) or 0)
            except (TypeError, ValueError):
                value = 0.0
            value = max(low, min(high, value))
            if key == "momentum_wait_adjust_min":
                normalized[key] = int(round(value))
            else:
                normalized[key] = round(value, 4)
        return normalized
    def _reset_runtime_reason_counters(self, market: str) -> None:
        market_key = "US" if str(market).upper() == "US" else "KR"
        self._ticker_runtime_blocked_reasons[market_key] = {}
        self._ticker_runtime_rejection_reasons[market_key] = {}
    def _bump_runtime_reason(self, market: str, ticker: str, reason: str, *, bucket: str = "blocked") -> None:
        market_key = "US" if str(market).upper() == "US" else "KR"
        normalized = str(ticker).upper() if market_key == "US" else str(ticker)
        reason_key = str(reason or "").strip()
        if not normalized or not reason_key:
            return
        target = self._ticker_runtime_rejection_reasons if bucket == "rejection" else self._ticker_runtime_blocked_reasons
        market_map = target.setdefault(market_key, {})
        ticker_map = market_map.setdefault(normalized, {})
        ticker_map[reason_key] = int(ticker_map.get(reason_key, 0) or 0) + 1
    def _min_effective_order_krw(self, market: str) -> float:
        market_key = "US" if str(market).upper() == "US" else "KR"
        if market_key == "US":
            rate = float(getattr(self, "usd_krw_rate", 0) or 0)
            return max(0.0, _MIN_EFFECTIVE_ORDER_USD * rate) if rate > 0 else 0.0
        return max(0.0, _MIN_EFFECTIVE_ORDER_KRW)
    def _is_order_size_too_small(
        self,
        market: str,
        qty: int,
        order_cost_krw: float,
        order_budget_krw: float,
    ) -> tuple[bool, float]:
        minimum = self._min_effective_order_krw(market)
        if minimum <= 0:
            return False, minimum
        if qty <= 0:
            return 0 < order_budget_krw < minimum, minimum
        return 0 < order_cost_krw < minimum, minimum
    def _affordability_diag(
        self,
        *,
        price_krw: float,
        qty: int,
        order_cost_krw: float,
        order_budget_krw: float,
        available_budget_krw: Optional[float] = None,
        cash_krw: Optional[float] = None,
        min_effective_order_krw: float = 0.0,
    ) -> dict:
        price = max(0.0, float(price_krw or 0.0))
        qty_int = int(qty or 0)
        order_cost = max(0.0, float(order_cost_krw or 0.0))
        order_budget = max(0.0, float(order_budget_krw or 0.0))
        available = None if available_budget_krw is None else max(0.0, float(available_budget_krw or 0.0))
        cash = None if cash_krw is None else max(0.0, float(cash_krw or 0.0))
        minimum = max(0.0, float(min_effective_order_krw or 0.0))
        capacity_values = [order_budget]
        if available is not None:
            capacity_values.append(available)
        if cash is not None:
            capacity_values.append(cash)
        effective_capacity = min(capacity_values) if capacity_values else 0.0
        affordable_one_share = price > 0 and effective_capacity >= price
        required = price if qty_int <= 0 else order_cost
        if minimum > required and (qty_int <= 0 or order_cost < minimum):
            required = minimum
        shortfall = max(0.0, required - effective_capacity) if required > 0 else 0.0
        reason = ""
        if price <= 0:
            reason = "invalid_price"
        elif qty_int <= 0:
            if cash is not None and cash < price:
                reason = "cash_too_low"
            elif available is not None and available < price:
                reason = "budget_too_small"
            elif order_budget < price:
                reason = "unaffordable_high_price"
            else:
                reason = "budget_too_small"
        elif minimum > 0 and order_cost < minimum:
            reason = "min_order_not_met"
        elif available is not None and order_cost > available:
            reason = "budget_too_small"
        elif cash is not None and order_cost > cash:
            reason = "cash_too_low"
        return {
            "price_per_share_krw": price,
            "affordable_1_share_bool": bool(affordable_one_share),
            "shortfall_krw": float(shortfall),
            "affordability_reason": reason,
            "order_budget_krw": order_budget,
            "available_budget_krw": float(available) if available is not None else 0.0,
            "available_cash_krw": float(cash) if cash is not None else 0.0,
            "cash_krw": float(cash) if cash is not None else 0.0,
            "min_effective_order_krw": minimum,
        }
    def _is_micro_probe_record(self, item: dict) -> bool:
        return bool(item.get("micro_probe")) or str(item.get("strategy", "") or "").upper() == _MICRO_PROBE_STRATEGY
    def _micro_probe_session_count(self, market: str) -> int:
        market_key = "US" if str(market).upper() == "US" else "KR"
        count = 0
        for event in getattr(self, "decision_event_log", []) or []:
            if event.get("market") != market_key:
                continue
            if event.get("action") in ("buy_order", "buy_signal") and self._is_micro_probe_record(event):
                count += 1
        return count
    def _micro_probe_open_count(self, market: str) -> int:
        market_key = "US" if str(market).upper() == "US" else "KR"
        count = 0
        for pos in getattr(getattr(self, "risk", None), "positions", []) or []:
            ticker = str(pos.get("ticker", "") or "").strip()
            if ticker and self._ticker_market(ticker) == market_key and self._is_micro_probe_record(pos):
                count += 1
        for order in getattr(self, "pending_orders", []) or []:
            if order.get("market") == market_key and self._is_micro_probe_record(order):
                count += 1
        return count
    def _micro_probe_adjustment(
        self,
        *,
        market: str,
        ticker: str,
        mode: str,
        source_strategy: str,
        entry_priority_score: float,
        qty: int,
        risk_price_krw: float,
        original_order_cost_krw: float,
        order_budget_krw: float,
        min_effective_order_krw: float,
        available_budget_krw: float,
        cash_krw: float,
    ) -> dict:
        market_key = "US" if str(market).upper() == "US" else "KR"
        if not getattr(self, "enable_micro_probe", False):
            return {"allowed": False, "reason": "disabled"}
        if getattr(self, "micro_probe_paper_only", True) and not getattr(self, "is_paper", True):
            return {"allowed": False, "reason": "live_disabled"}
        if market_key not in getattr(self, "micro_probe_allowed_markets", {"KR", "US"}):
            return {"allowed": False, "reason": "market_not_allowed"}
        allowed_modes = getattr(self, "micro_probe_allowed_modes", set())
        if allowed_modes and str(mode or "").upper() not in allowed_modes:
            return {"allowed": False, "reason": "mode_not_allowed"}
        min_score = float(getattr(self, "micro_probe_min_entry_priority", 0.45) or 0.0)
        if float(entry_priority_score or 0.0) < min_score:
            return {"allowed": False, "reason": "entry_priority_too_low"}
        if self._micro_probe_session_count(market_key) >= int(getattr(self, "micro_probe_max_daily_trades", 0) or 0):
            return {"allowed": False, "reason": "daily_limit"}
        if self._micro_probe_open_count(market_key) >= int(getattr(self, "micro_probe_max_open_positions", 0) or 0):
            return {"allowed": False, "reason": "open_limit"}
        price = float(risk_price_krw or 0.0)
        minimum = float(min_effective_order_krw or 0.0)
        budget = float(order_budget_krw or 0.0)
        original_cost = float(original_order_cost_krw or 0.0)
        if price <= 0 or minimum <= 0 or budget <= 0:
            return {"allowed": False, "reason": "invalid_probe_basis"}
        adjusted_qty = max(int(qty or 0), int(math.ceil(minimum / price)))
        adjusted_cost = adjusted_qty * price
        ratio_base = max(original_cost, budget)
        oversize_ratio = adjusted_cost / max(ratio_base, 1.0)
        max_order = float(getattr(self, "micro_probe_max_order_krw", 0.0) or 0.0)
        if max_order > 0 and adjusted_cost > max_order:
            return {
                "allowed": False,
                "reason": "probe_order_too_large",
                "adjusted_qty": adjusted_qty,
                "adjusted_order_cost_krw": adjusted_cost,
                "oversize_ratio": oversize_ratio,
            }
        max_ratio = float(getattr(self, "micro_probe_max_oversize_ratio", 0.0) or 0.0)
        if max_ratio > 0 and oversize_ratio > max_ratio:
            return {
                "allowed": False,
                "reason": "oversize_ratio_too_high",
                "adjusted_qty": adjusted_qty,
                "adjusted_order_cost_krw": adjusted_cost,
                "oversize_ratio": oversize_ratio,
            }
        if adjusted_cost > float(available_budget_krw or 0.0):
            return {"allowed": False, "reason": "market_budget_exceeded"}
        if adjusted_cost > float(cash_krw or 0.0):
            return {"allowed": False, "reason": "insufficient_cash"}
        return {
            "allowed": True,
            "reason": "order_size_too_small_probe",
            "source_strategy": source_strategy,
            "original_qty": int(qty or 0),
            "adjusted_qty": adjusted_qty,
            "original_order_cost_krw": original_cost,
            "adjusted_order_cost_krw": adjusted_cost,
            "order_budget_krw": budget,
            "min_effective_order_krw": minimum,
            "oversize_ratio": oversize_ratio,
            "entry_priority_score": float(entry_priority_score or 0.0),
        }
    def _submit_micro_probe_buy_order(
        self,
        *,
        market: str,
        ticker: str,
        name: str,
        qty: int,
        raw_price: float,
        risk_price_krw: float,
        tp_pct: float,
        sl_pct: float,
        max_hold: int,
        mode: str,
        selected_reason: str,
        source_strategy: str,
        entry_priority_score: float,
        tsdb_id: int,
        isdb_id: int,
        signal_at: str,
        signal_row: dict,
        probe_meta: dict,
    ) -> bool:
        order_cost = float(qty or 0) * float(risk_price_krw or 0.0)
        if qty <= 0 or raw_price <= 0 or risk_price_krw <= 0 or order_cost <= 0:
            return False
        analysis_log.info(
            f"[signal {market}] {ticker} MICRO_PROBE qty={qty}",
            extra={"extra": {
                "event": "entry_signal",
                "market": market,
                "ticker": ticker,
                "strategy": _MICRO_PROBE_STRATEGY,
                "source_strategy": source_strategy,
                "mode": mode,
                "price": raw_price,
                "risk_price_krw": risk_price_krw,
                "qty": qty,
                "order_cost_krw": order_cost,
                "tp_pct": tp_pct,
                "sl_pct": sl_pct,
                "micro_probe": True,
                **{k: v for k, v in (probe_meta or {}).items() if k != "allowed"},
            }},
        )
        self._funnel.setdefault(market, {}).setdefault("signaled", 0)
        self._funnel[market]["signaled"] += 1
        self._notify_signal_state_change(
            "entry_signal",
            market,
            ticker,
            strategy=_MICRO_PROBE_STRATEGY,
            price=float(raw_price),
            mode=mode,
            qty=qty,
            order_cost_krw=int(order_cost),
        )
        order_px = self._compute_order_price("buy", market, float(raw_price))
        precheck_px = float(raw_price) if order_px == 0 else order_px
        buy_gate = self._new_buy_block_state(market, ticker, _MICRO_PROBE_STRATEGY)
        if not bool(buy_gate.get("allowed", True)):
            self._record_new_buy_block(
                market,
                ticker,
                _MICRO_PROBE_STRATEGY,
                buy_gate,
                qty=qty,
                price_native=order_px,
                price_krw=risk_price_krw,
                selected_reason=selected_reason,
                stage="micro_probe_precheck",
                tsdb_id=tsdb_id,
                entry_priority=entry_priority_score,
                signal_at=signal_at,
                signal_row=signal_row,
            )
            return False
        precheck = precheck_order(ticker, qty, precheck_px, "buy", self.token, market=market)
        if not precheck.get("ok"):
            self._record_decision_event(
                market,
                "buy_failed",
                ticker,
                strategy=_MICRO_PROBE_STRATEGY,
                qty=qty,
                price_native=order_px,
                price_krw=risk_price_krw,
                reason=precheck.get("reason", "precheck_failed"),
                detail=precheck.get("msg", ""),
                selected_reason=selected_reason,
            )
            analysis_log.info(
                f"[signal {market}] {ticker} micro_probe_precheck_failed",
                extra={"extra": {
                    "event": "entry_failed",
                    "market": market,
                    "ticker": ticker,
                    "strategy": _MICRO_PROBE_STRATEGY,
                    "source_strategy": source_strategy,
                    "reason": precheck.get("reason", "precheck_failed"),
                    "detail": precheck.get("msg", ""),
                    "price": raw_price,
                    "mode": mode,
                    "micro_probe": True,
                }},
            )
            try:
                tsdb.update_signal(tsdb_id, _MICRO_PROBE_STRATEGY, entry_priority_score, signal_at, precheck.get("reason", "precheck_failed"))
            except Exception:
                pass
            return False
        try:
            result = place_order(ticker, qty, order_px, "buy", self.token, market=market)
        except Exception as exc:
            self._record_decision_event(
                market,
                "buy_failed",
                ticker,
                strategy=_MICRO_PROBE_STRATEGY,
                qty=qty,
                price_native=order_px,
                price_krw=risk_price_krw,
                reason="order_exception",
                detail=str(exc),
                selected_reason=selected_reason,
            )
            try:
                tsdb.update_signal(tsdb_id, _MICRO_PROBE_STRATEGY, entry_priority_score, signal_at, "order_exception")
            except Exception:
                pass
            log.error(f"micro_probe order exception [{ticker}]: {exc}")
            return False
        if not result.get("success"):
            detail = result.get("msg", "")
            self._record_decision_event(
                market,
                "buy_failed",
                ticker,
                strategy=_MICRO_PROBE_STRATEGY,
                qty=qty,
                price_native=order_px,
                price_krw=risk_price_krw,
                reason="order_rejected",
                detail=detail,
                order_no=result.get("order_no", ""),
                selected_reason=selected_reason,
            )
            try:
                tsdb.update_signal(tsdb_id, _MICRO_PROBE_STRATEGY, entry_priority_score, signal_at, "order_rejected")
            except Exception:
                pass
            log.error(f"micro_probe order failed [{ticker}]: {detail}")
            return False
        if market == "US":
            self._mark_us_order_supported(ticker)
        self._order_error_count.pop(ticker, None)
        self._funnel.setdefault(market, {}).setdefault("ordered", 0)
        self._funnel[market]["ordered"] += 1
        order_no = str(result.get("order_no", "") or "")
        log.info(
            f"[{'PAPER' if self.is_paper else 'LIVE'} MICRO_PROBE BUY] "
            f"{ticker} {qty}@{raw_price:,} | source={source_strategy} | order_no={order_no}"
        )
        detail = (
            f"?먯쟾??{source_strategy} ?먯＜臾?{probe_meta.get('original_order_cost_krw', 0):,.0f}??"
            f"議곗젙二쇰Ц={order_cost:,.0f}??珥덇낵鍮꾩쑉=x{probe_meta.get('oversize_ratio', 0):.2f}"
        )
        self._record_decision_event(
            market,
            "buy_order",
            ticker,
            strategy=_MICRO_PROBE_STRATEGY,
            qty=qty,
            price_native=float(raw_price),
            price_krw=float(risk_price_krw),
            reason="MICRO_PROBE 吏꾩엯",
            detail=detail,
            order_no=order_no,
            selected_reason=selected_reason,
            source_strategy=source_strategy,
            micro_probe=True,
            micro_probe_reason=probe_meta.get("reason", "order_size_too_small_probe"),
        )
        ml_decision_id = -1
        try:
            tsdb.update_signal(tsdb_id, _MICRO_PROBE_STRATEGY, entry_priority_score, signal_at)
            tsdb.update_traded(tsdb_id, datetime.now(KST).isoformat())
        except Exception:
            pass
        if isdb_id and source_strategy == "opening_range_pullback":
            try:
                isdb.update_trade(isdb_id)
            except Exception:
                pass
        entered_at = datetime.now(KST).isoformat()
        try:
            tsdb.log_micro_probe_entry(
                session_date=self._current_session_date_str(market),
                market=market,
                ticker=ticker,
                order_no=order_no,
                source_strategy=source_strategy,
                reason=probe_meta.get("reason", "order_size_too_small_probe"),
                entry_priority_score=entry_priority_score,
                original_qty=int(probe_meta.get("original_qty", 0) or 0),
                adjusted_qty=qty,
                original_order_cost_krw=float(probe_meta.get("original_order_cost_krw", 0) or 0),
                adjusted_order_cost_krw=order_cost,
                order_budget_krw=float(probe_meta.get("order_budget_krw", 0) or 0),
                min_effective_order_krw=float(probe_meta.get("min_effective_order_krw", 0) or 0),
                oversize_ratio=float(probe_meta.get("oversize_ratio", 0) or 0),
                entered_at=entered_at,
            )
        except Exception as exc:
            log.warning(f"micro_probe DB 湲곕줉 ?ㅽ뙣: {exc}")
        self._add_pending_order({
            "order_no": order_no,
            "ticker": ticker,
            "name": name or self._lookup_ticker_name(ticker, market),
            "market": market,
            "qty": qty,
            "raw_price": float(raw_price),
            "risk_price_krw": float(risk_price_krw),
            "strategy": _MICRO_PROBE_STRATEGY,
            "source_strategy": source_strategy,
            "micro_probe": True,
            "micro_probe_reason": probe_meta.get("reason", "order_size_too_small_probe"),
            "original_order_cost_krw": float(probe_meta.get("original_order_cost_krw", 0) or 0),
            "adjusted_order_cost_krw": order_cost,
            "oversize_ratio": float(probe_meta.get("oversize_ratio", 0) or 0),
            "order_budget_krw": float(probe_meta.get("order_budget_krw", 0) or 0),
            "min_effective_order_krw": float(probe_meta.get("min_effective_order_krw", 0) or 0),
            "tp_pct": float(tp_pct),
            "sl_pct": float(sl_pct),
            "max_hold": int(max_hold),
            "created_at": entered_at,
            "decision_id": ml_decision_id,
        })
        self._block_entry(ticker, _BUY_COOLDOWN_MIN, "micro_probe_buy_placed")
        buy_order_alert(
            market=market,
            ticker=ticker,
            qty=qty,
            order_no=order_no,
            detail="MICRO_PROBE 泥닿껐 ?뺤씤 ??蹂댁쑀 ?ъ??섏뿉 諛섏쁺?⑸땲??",
            name=name,
            buy_path="path_a",
        )
        return True
    def _restrict_candidates_to_universe(self, candidates: list, allowed_tickers: Optional[list] = None) -> list:
        rows = list(candidates or [])
        allowed = [str(t or "").strip().upper() for t in (allowed_tickers or []) if str(t or "").strip()]
        if not rows or not allowed:
            return rows
        allowed_set = set(allowed)
        filtered = [row for row in rows if str(row.get("ticker", "") or "").strip().upper() in allowed_set]
        return filtered or rows
    def _partial_replace_score(self, market: str, ticker: str, protected: Optional[set] = None) -> float:
        normalized = str(ticker).upper() if market == "US" else str(ticker)
        if protected and normalized in protected:
            return -999.0
        score = (
            float(self._ticker_no_signal_cycles.get(normalized, self._ticker_no_signal_cycles.get(ticker, 0)) or 0)
            + float(self._invalid_price_count.get(normalized, self._invalid_price_count.get(ticker, 0)) or 0) * 2.0
        )
        bucket = self._watch_only_bucket(market, normalized)
        if bucket == "HARD":
            score += 4.0
        elif bucket == "SOFT":
            score -= 2.0
        elif bucket == "RECOVERY":
            score -= 4.0
        blocked_counts = (self._ticker_runtime_blocked_reasons.get(market, {}) or {}).get(normalized, {})
        rejection_counts = (self._ticker_runtime_rejection_reasons.get(market, {}) or {}).get(normalized, {})
        score += float(blocked_counts.get("momentum_atr_too_high", 0)) * 2.5
        score += float(blocked_counts.get("affordability_fail", 0)) * 1.0
        score += float(blocked_counts.get("order_size_too_small", 0)) * 1.5
        score += float(blocked_counts.get("entry_blackout", 0)) * 1.5
        score += float(blocked_counts.get("blocked_by_mode", 0)) * 2.0
        score += float(blocked_counts.get("same_day_reentry_blocked", 0)) * 1.0
        score += float(rejection_counts.get("momentum_wait", 0)) * 1.0
        score += float(rejection_counts.get("gap_insufficient", 0)) * 0.5
        score += float(rejection_counts.get("pullback_missing", 0)) * 0.5
        score += float(rejection_counts.get("highpoint_missing", 0)) * 0.5
        return score
    def _reset_runtime_overrides(self, market: str) -> None:
        market_key = "US" if str(market).upper() == "US" else "KR"
        raw_map = getattr(self, "_claude_runtime_overrides", None)
        if not isinstance(raw_map, dict):
            raw_map = {"KR": {}, "US": {}}
            self._claude_runtime_overrides = raw_map
        raw_map[market_key] = {}
        if isinstance(getattr(self, "today_judgment", None), dict) and self.today_judgment.get("market") == market_key:
            self.today_judgment.setdefault("claude_runtime_overrides", {})
            self.today_judgment["claude_runtime_overrides"][market_key] = {}
    def _restore_runtime_overrides_from_payload(self, market: str, payload: Optional[dict]) -> dict:
        market_key = "US" if str(market).upper() == "US" else "KR"
        self._reset_runtime_overrides(market_key)
        source = payload if isinstance(payload, dict) else {}
        raw_blob = source.get("claude_runtime_overrides")
        raw_market = {}
        if isinstance(raw_blob, dict):
            if isinstance(raw_blob.get(market_key), dict):
                raw_market = dict(raw_blob.get(market_key) or {})
            elif any(key in raw_blob for key in _CLAUDE_RUNTIME_OVERRIDE_BOUNDS):
                raw_market = dict(raw_blob)
        normalized = {}
        for key, (low, high) in _CLAUDE_RUNTIME_OVERRIDE_BOUNDS.items():
            try:
                value = float(raw_market.get(key, 0) or 0)
            except (TypeError, ValueError):
                value = 0.0
            value = max(low, min(high, value))
            if key == "momentum_wait_adjust_min":
                normalized[key] = int(round(value))
            else:
                normalized[key] = round(value, 4)
        self._claude_runtime_overrides[market_key] = normalized
        if isinstance(getattr(self, "today_judgment", None), dict) and self.today_judgment.get("market") == market_key:
            self.today_judgment.setdefault("claude_runtime_overrides", {})
            self.today_judgment["claude_runtime_overrides"][market_key] = dict(normalized)
        return normalized
    def _effective_entry_priority_cutoff(self, market: str = "") -> float:
        cutoff = float(getattr(self, "entry_priority_cutoff", 0.20) or 0.20)
        if market:
            cutoff += float(self._runtime_overrides(market).get("entry_priority_cutoff_adjust", 0.0))
        return max(0.05, min(0.50, cutoff))
    def _effective_momentum_wait_window(self, market: str, base_window: float) -> float:
        wait_window = float(base_window or 0)
        wait_window += float(self._runtime_overrides(market).get("momentum_wait_adjust_min", 0))
        return max(5.0, min(60.0, wait_window))
    def _effective_kr_momentum_atr_caps(self) -> tuple[float, float]:
        base_cap = float(os.getenv("KR_MOMENTUM_ATR_PCT_MAX", "0.06") or 0.06)
        base_high_cap = float(os.getenv("KR_MOMENTUM_ATR_PCT_HIGH", "0.10") or 0.10)
        overrides = self._runtime_overrides("KR")
        cap = base_cap + float(overrides.get("kr_momentum_atr_cap_adjust", 0.0) or 0.0)
        high_cap = base_high_cap + float(overrides.get("kr_momentum_atr_cap_high_adjust", 0.0) or 0.0)
        cap = max(0.04, min(0.12, cap))
        high_cap = max(cap + 0.01, min(0.15, high_cap))
        return round(cap, 4), round(high_cap, 4)
    def _effective_kr_momentum_atr_stage(self, atr_pct: Optional[float]) -> dict:
        cap, high_cap = self._effective_kr_momentum_atr_caps()
        if atr_pct is None:
            return {"blocked": False, "size_cap": None, "stage": "none", "cap": cap, "high_cap": high_cap}
        atr_pct = float(atr_pct)
        if atr_pct <= cap:
            return {"blocked": False, "size_cap": None, "stage": "base", "cap": cap, "high_cap": high_cap}
        stage_1 = min(high_cap, round(cap + 0.01, 4))
        stage_2 = min(high_cap, round(cap + 0.02, 4))
        if atr_pct <= stage_1:
            return {"blocked": False, "size_cap": 70, "stage": "atr_70", "cap": cap, "high_cap": high_cap}
        if atr_pct <= stage_2:
            return {"blocked": False, "size_cap": 50, "stage": "atr_50", "cap": cap, "high_cap": high_cap}
        if atr_pct <= high_cap:
            return {"blocked": False, "size_cap": 35, "stage": "atr_35", "cap": cap, "high_cap": high_cap}
        return {"blocked": True, "size_cap": None, "stage": "blocked", "cap": cap, "high_cap": high_cap}
    def _kr_momentum_shrink_signals(self, candidate: dict) -> list[str]:
        if not getattr(self, "enable_kr_momentum_shrink", True):
            return []
        signals: list[str] = []
        liquidity_bucket = str(candidate.get("liquidity_bucket", "") or "").strip().lower()
        from_high_bucket = str(candidate.get("from_high_bucket", "") or "").strip().lower()
        atr_stage = str(candidate.get("atr_stage", "") or "").strip().lower()
        try:
            atr_pct = float(candidate.get("atr_pct", 0.0) or 0.0)
        except Exception:
            atr_pct = 0.0
        try:
            change_rate = abs(float(candidate.get("change_rate", 0.0) or 0.0))
        except Exception:
            change_rate = 0.0
        try:
            from_high_pct = float(candidate.get("from_high_pct", -99.0) or -99.0)
        except Exception:
            from_high_pct = -99.0
        if liquidity_bucket in {"mid", "low", "unknown"}:
            signals.append(f"liq={liquidity_bucket or 'unknown'}")
        if from_high_bucket in {"near_high", "at_high"} or from_high_pct >= -1.5:
            signals.append("near_high")
        if atr_stage in {"atr_50", "atr_35", "blocked"} or atr_pct >= 7.0:
            signals.append(f"atr={atr_stage or f'{atr_pct:.1f}%'}")
        if change_rate >= 8.0:
            signals.append("hot_move")
        return signals
    def _market_position_count(self, market: str) -> int:
        market_key = "US" if str(market).upper() == "US" else "KR"
        count = 0
        for pos in getattr(self.risk, "positions", []) or []:
            ticker = str(pos.get("ticker", "") or "")
            pos_market = "US" if ticker.replace(".", "").isalpha() else "KR"
            if pos_market == market_key:
                count += 1
        return count
    def _risk_off_mr_exception(self, market: str, mode: str, strategy_name: str, sig_row: dict) -> dict:
        market_key = "US" if str(market).upper() == "US" else "KR"
        normalized_mode = str(mode or "").upper()
        normalized_strategy = _normalize_strategy_name(strategy_name)
        result = {"allowed": False, "size_cap": 40, "reason": "not_applicable"}
        if normalized_mode == "HALT":
            result["reason"] = "halt"
            return result
        if _mode_family(normalized_mode) != "RISK_OFF":
            result["reason"] = "not_risk_off"
            return result
        if normalized_strategy != "mean_reversion":
            result["reason"] = "not_mean_reversion"
            return result
        if self._market_position_count(market_key) >= 1:
            result["reason"] = "risk_off_position_limit"
            return result
        primary_change = self._get_market_change_pct(market_key)
        secondary_change = self._get_secondary_change_pct(market_key)
        if primary_change is not None and primary_change <= -2.5:
            result["reason"] = "panic_primary"
            return result
        if secondary_change is not None and secondary_change <= -3.5:
            result["reason"] = "panic_secondary"
            return result
        vol_ratio = float(sig_row.get("vol_ratio", 0) or 0)
        if vol_ratio > 2.0:
            result["reason"] = "overheated_volume"
            return result
        result["allowed"] = True
        result["reason"] = "risk_off_mr_exception"
        return result
    def _apply_runtime_tuning_adjustments(self, market: str, result: dict) -> dict:
        market_key = "US" if str(market).upper() == "US" else "KR"
        current = self._runtime_overrides(market_key)
        updated = {}
        changed = {}
        for key, (low, high) in _CLAUDE_RUNTIME_OVERRIDE_BOUNDS.items():
            try:
                value = float((result or {}).get(key, 0) or 0)
            except (TypeError, ValueError):
                value = 0.0
            value = max(low, min(high, value))
            if key == "momentum_wait_adjust_min":
                value = int(round(value))
            else:
                value = round(value, 4)
            updated[key] = value
            if current.get(key) != value:
                changed[key] = value
        self._claude_runtime_overrides[market_key] = updated
        if isinstance(getattr(self, "today_judgment", None), dict):
            self.today_judgment.setdefault("claude_runtime_overrides", {})
            self.today_judgment["claude_runtime_overrides"][market_key] = updated
        if changed:
            log.info(
                f"[claude runtime override] {market_key} "
                + ", ".join(f"{key}={value}" for key, value in changed.items())
            )
        return changed
    def _runtime_gate_state(self, market: str) -> dict:
        market_key = "US" if str(market).upper() == "US" else "KR"
        wait_base = 45.0
        wait_effective = self._effective_momentum_wait_window(market_key, wait_base)
        cutoff = self._effective_entry_priority_cutoff(market_key)
        state = {
            "momentum_wait_base": wait_base,
            "momentum_wait_effective": wait_effective,
            "entry_priority_cutoff": cutoff,
        }
        if market_key == "KR":
            atr_cap, atr_high_cap = self._effective_kr_momentum_atr_caps()
            state["kr_momentum_atr_cap"] = atr_cap
            state["kr_momentum_atr_cap_high"] = atr_high_cap
        return state
    def _runtime_gate_state_text(self, market: str) -> str:
        market_key = "US" if str(market).upper() == "US" else "KR"
        state = self._runtime_gate_state(market_key)
        overrides = self._runtime_overrides(market_key)
        text = (
            f"wait={int(state['momentum_wait_base'])}->{int(state['momentum_wait_effective'])}m "
            f"cutoff={state['entry_priority_cutoff']:.2f}"
        )
        if market_key == "KR":
            text += (
                f" kr_atr_cap={state['kr_momentum_atr_cap']:.2f}/"
                f"{state['kr_momentum_atr_cap_high']:.2f}"
            )
        text += (
            f" adjust(wait={int(overrides.get('momentum_wait_adjust_min', 0) or 0):+d}m"
            f", cutoff={float(overrides.get('entry_priority_cutoff_adjust', 0.0) or 0.0):+.2f}"
        )
        if market_key == "KR":
            text += (
                f", atr={float(overrides.get('kr_momentum_atr_cap_adjust', 0.0) or 0.0):+.2f}/"
                f"{float(overrides.get('kr_momentum_atr_cap_high_adjust', 0.0) or 0.0):+.2f}"
        )
        text += ")"
        return text
    def _selection_active_strategies(self, market: str, mode: str = "") -> list[str]:
        market_key = "US" if str(market).upper() == "US" else "KR"
        mode_value = str(mode or (self.today_judgment.get("consensus", {}) or {}).get("mode", "NEUTRAL")).upper()
        risk_on = {"AGGRESSIVE", "MODERATE_BULL", "MILD_BULL"}
        balanced = {"CAUTIOUS", "NEUTRAL"}
        risk_off = {"MILD_BEAR", "CAUTIOUS_BEAR", "DEFENSIVE", "HALT"}
        if mode_value in risk_off:
            return ["mean_reversion"]
        if market_key == "KR":
            if mode_value in risk_on:
                active = ["opening_range_pullback", "gap_pullback", "momentum", "mean_reversion", "continuation"]
                return [s for s in active if s != "continuation" or self.enable_continuation_live]
            if mode_value in balanced:
                return ["opening_range_pullback", "gap_pullback", "mean_reversion", "momentum"]
            return ["opening_range_pullback", "gap_pullback", "mean_reversion"]
        if mode_value in risk_on:
            active = ["opening_range_pullback", "gap_pullback", "mean_reversion", "momentum", "continuation"]
            return [s for s in active if s != "continuation" or self.enable_continuation_live]
        if mode_value in balanced:
            return ["opening_range_pullback", "gap_pullback", "mean_reversion", "momentum"]
        return ["mean_reversion"]
    def _selection_session_phase(self, market: str) -> dict:
        market_key = "US" if str(market).upper() == "US" else "KR"
        elapsed = float(self._market_elapsed_min(market_key))
        minutes_to_close = float(self._minutes_to_close(market_key))
        close_guard = float(HARD_RULES.get("close_before_min", 30) or 30)
        if minutes_to_close <= close_guard:
            phase = "close_guard"
        elif elapsed <= 15:
            phase = "opening"
        elif elapsed <= 60:
            phase = "early"
        elif minutes_to_close <= 60:
            phase = "late"
        else:
            phase = "mid"
        return {
            "phase": phase,
            "elapsed_min": round(elapsed, 1),
            "minutes_to_close": round(minutes_to_close, 1),
            "entry_blackout": bool(self._in_entry_blackout(market_key)),
        }
    def _format_ops_review_context(self, market: str, max_lines: int = 4) -> str:
        snapshot = dict((self.today_judgment or {}).get("ops_review_snapshot") or {})
        if str(snapshot.get("market", "") or "").upper() != str(market or "").upper():
            return ""
        metrics = snapshot.get("metrics") or {}
        lines: list[str] = []
        def _metric_line(key: str, label: str, suffix: str = "%") -> None:
            metric = metrics.get(key) or {}
            value = metric.get("value")
            if value is None:
                return
            breached = bool(metric.get("breached"))
            sample = metric.get("sample")
            sample_text = f" n={sample}" if sample else ""
            status = " breach" if breached else ""
            lines.append(f"{label}={value}{suffix}{sample_text}{status}")
        _metric_line("consensus_directional_hit_rate", "consensus_hit")
        _metric_line("best_analyst_minus_consensus_hit_gap", "best_gap")
        _metric_line("trade_ready_signal_conversion", "tr_ready_to_signal")
        _metric_line("watch_only_missed_runup_ratio", "watch_missed")
        _metric_line("atr_blocked_missed_runup", "atr_blocked_runup")
        _metric_line("entry_blackout_ratio", "blackout")
        if not lines:
            return ""
        return "ops review: " + " | ".join(lines[:max_lines])
    def _build_execution_profile_text(self, market: str, mode: str = "") -> str:
        phase = self._selection_session_phase(market)
        active = self._selection_active_strategies(market, mode)
        return (
            f"session_phase={phase['phase']} elapsed={phase['elapsed_min']:.0f}m "
            f"to_close={phase['minutes_to_close']:.0f}m blackout={'yes' if phase['entry_blackout'] else 'no'} | "
            f"active_strategies={','.join(active)} | gates={self._runtime_gate_state_text(market)}"
        )
    def _annotate_selection_execution_features(self, market: str, candidates: list, mode: str = "") -> list:
        market_key = "US" if str(market).upper() == "US" else "KR"
        if not candidates:
            return []
        active_strategies = self._selection_active_strategies(market_key, mode)
        phase = self._selection_session_phase(market_key)
        ctx = (
            getattr(self, "_ca_context_last", None)
            or (self.today_judgment.get("digest_raw", {}) or {}).get("context", {})
            or {}
        )
        conf = float(((self.today_judgment or {}).get("consensus", {}) or {}).get("confidence", 0.6) or 0.6)
        or_minutes = 10 if market_key == "KR" else 15
        def _entry_bucket(score: float) -> str:
            if score >= 1.5:
                return "high"
            if score >= 0.8:
                return "mid"
            if score > 0:
                return "low"
            return "weak"
        def _atr_stage_text(price_ref: float, atr_val: float) -> tuple[str, Optional[float]]:
            if price_ref <= 0 or atr_val <= 0:
                return "unknown", None
            atr_pct = atr_val / price_ref
            if market_key == "KR":
                stage = self._effective_kr_momentum_atr_stage(atr_pct)
                return str(stage.get("stage") or "unknown"), atr_pct
            if atr_pct <= 0.03:
                return "low", atr_pct
            if atr_pct <= 0.05:
                return "mid", atr_pct
            if atr_pct <= 0.07:
                return "high", atr_pct
            return "extreme", atr_pct
        enriched_rows = []
        for candidate in candidates:
            row = dict(candidate or {})
            ticker = str(row.get("ticker", "") or "").strip().upper()
            if not ticker:
                enriched_rows.append(row)
                continue
            row.setdefault("session_phase", phase.get("phase"))
            row.setdefault("minutes_to_close", phase.get("minutes_to_close"))
            row.setdefault("entry_blackout_now", phase.get("entry_blackout"))
            try:
                technicals = ((self.today_judgment.get("digest_raw", {}) or {}).get("technicals") or {})
                tech = technicals.get(ticker) or technicals.get(str(candidate.get("ticker", "") or "").strip()) or {}
                if tech:
                    for key in (
                        "earnings_date",
                        "earnings_window",
                        "confidence_tier",
                        "pead_bias",
                        "prompt_applied",
                        "surprise_sign",
                        "surprise_strength",
                    ):
                        if key in tech and key not in row:
                            row[key] = tech.get(key)
            except Exception:
                pass
            if self._or_formed.get(ticker, False):
                row["or_state"] = "formed"
            elif float(phase.get("elapsed_min", 0.0) or 0.0) <= or_minutes:
                row["or_state"] = "forming"
            else:
                row["or_state"] = "missing"
            try:
                candles = self._get_ohlcv_cached(ticker, market_key)
                sig_df = calc_all(candles) if not getattr(candles, "empty", True) else None
                if sig_df is not None and not getattr(sig_df, "empty", True):
                    sig_row = sig_df.iloc[-1].to_dict()
                    price_ref = float(row.get("price", 0) or 0) or float(sig_row.get("close", 0) or 0)
                    atr_val = float(sig_row.get("atr", 0) or 0)
                    atr_stage, atr_pct = _atr_stage_text(price_ref, atr_val)
                    row["atr_stage"] = atr_stage
                    if atr_pct is not None:
                        row["atr_pct"] = round(float(atr_pct) * 100.0, 2)
                    price_info = {
                        "price": price_ref,
                        "high": float(candles.iloc[-1].get("high", 0) or 0),
                    }
                    best_score = 0.0
                    best_strategy = ""
                    for strategy_name in active_strategies[:4]:
                        try:
                            params = _adaptive_params(strategy_name, market_key, mode or "NEUTRAL", conf, context=ctx)
                        except Exception:
                            params = {}
                        if strategy_name == "opening_range_pullback":
                            params = dict(params or {})
                            _or_low = self._or_low.get(ticker, float("inf"))
                            params.update(
                                {
                                    "or_high": float(self._or_high.get(ticker, 0.0) or 0.0),
                                    "or_low": 0.0 if _or_low == float("inf") else float(_or_low or 0.0),
                                    "or_formed": bool(self._or_formed.get(ticker, False)),
                                    "or_minutes": or_minutes,
                                }
                            )
                        score, _ = entry_priority_score(
                            strategy_name=strategy_name,
                            sig_row=sig_row,
                            params=params or {},
                            price_info=price_info,
                            elapsed_min=float(phase.get("elapsed_min", 0.0) or 0.0),
                            ticker=ticker,
                        )
                        if float(score) > best_score:
                            best_score = float(score)
                            best_strategy = strategy_name
                    if best_strategy:
                        row["raw_entry_priority_score"] = round(best_score, 3)
                        row["raw_execution_fit_strategy"] = best_strategy
                        row["entry_priority_score"] = round(best_score, 3)
                        row["entry_priority_bucket"] = _entry_bucket(best_score)
                        row["execution_fit_strategy"] = best_strategy
                        if market_key == "KR" and best_strategy == "momentum":
                            shrink_signals = self._kr_momentum_shrink_signals(row)
                            if len(shrink_signals) >= 3 and "near_high" in shrink_signals and any(sig.startswith("atr=") for sig in shrink_signals):
                                row["entry_priority_score"] = round(min(float(row["entry_priority_score"]), 0.79), 3)
                                row["entry_priority_bucket"] = "low"
                                row["execution_fit_strategy"] = "observe"
                                row["selection_bias"] = "watch_only"
                                row["selection_bias_reason"] = "kr_momentum_shrink:" + ",".join(shrink_signals)
                                row["kr_momentum_shrink_signals"] = list(shrink_signals)
            except Exception:
                pass
            enriched_rows.append(row)
        return enriched_rows
    def _is_entry_priority_blocked(self, score: float, market: str = "") -> bool:
        cutoff = self._effective_entry_priority_cutoff(market) if market else self._effective_entry_priority_cutoff()
        return bool(self.entry_priority_cutoff_enabled and float(score or 0.0) < cutoff)
    def _get_market_change_pct(self, market: str, digest: dict = None) -> Optional[float]:
        """digest_raw?먯꽌 二?吏???깅씫瑜?異붿텧 ??KR=KOSPI, US=S&P500. ?곗씠???놁쑝硫?None."""
        ctx = (digest or self.today_judgment.get("digest_raw") or {}).get("context") or {}
        key = "kospi" if market == "KR" else "sp500"
        v = (ctx.get(key) or {}).get("change_pct")
        return float(v) if v is not None else None
    def _get_secondary_change_pct(self, market: str, digest: dict = None) -> Optional[float]:
        """蹂댁“ 吏???깅씫瑜???KR=KOSDAQ, US=NASDAQ. ?곗씠???놁쑝硫?None."""
        ctx = (digest or self.today_judgment.get("digest_raw") or {}).get("context") or {}
        key = "kosdaq" if market == "KR" else "nasdaq"
        v = (ctx.get(key) or {}).get("change_pct")
        return float(v) if v is not None else None
    def _log_screen_candidates(self, market: str, candidates: list, source: str):
        """??쒕낫?쒓? 理쒖떊 ?꾨낫 吏묓빀???????덈룄濡??ъ뒪?щ━?앸룄 analysis 濡쒓렇???④릿??"""
        today = self._current_session_date_str(market)
        self._update_session_date_diagnostics(market, "session_close")
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
    def _record_candidate_quality(
        self,
        market: str,
        phase: str,
        raw_candidates: list,
        prompt_candidates: list,
        selected: list,
        selection_meta: dict,
        reasons: Optional[dict] = None,
    ) -> None:
        try:
            summary = write_candidate_quality_log(
                market=market,
                phase=phase,
                raw_candidates=list(raw_candidates or []),
                prompt_candidates=list(prompt_candidates or []),
                selected=list(selected or []),
                selection_meta=dict(selection_meta or {}),
                reasons=dict(reasons or {}),
            )
            log.info(
                f"[candidate quality] {market} phase={phase} "
                f"rows={summary.get('rows', 0)} counts={summary.get('counts', {})}"
            )
        except Exception as exc:
            log.warning(f"[candidate quality] save failed {market} {phase}: {exc}")
    def _opening_fresh_quality_metrics(self, market: str, raw_candidates: list) -> dict:
        prompt_tickers = (
            (self.today_judgment or {}).get("universe_tickers")
            or self.today_tickers.get(market, [])
            or []
        )
        return opening_fresh_quality_metrics(
            market=market,
            raw_candidates=list(raw_candidates or []),
            prompt_tickers=list(prompt_tickers or []),
            current_trade_ready=list((self.selection_meta.get(market, {}) or {}).get("trade_ready", []) or []),
        )
    def run_opening_fresh_screener(self, market: str = "KR") -> None:
        market = str(market or "KR").upper()
        if not self.session_active or self.current_market != market:
            return
        try:
            mode = str((self.today_judgment or {}).get("consensus", {}).get("mode", "NEUTRAL") or "NEUTRAL")
            raw_candidates = self._screen_market_candidates(market, mode)
            if market == "KR" and raw_candidates:
                self._last_kr_candidates = raw_candidates
            metrics = self._opening_fresh_quality_metrics(market, raw_candidates)
            log.info(
                f"[opening_fresh_quality] {market} "
                f"top20_coverage={metrics.get('top20_coverage')} "
                f"not_in_prompt={metrics.get('not_in_prompt')} "
                f"new_high_liq_candidates={metrics.get('new_high_liq_candidates')} "
                f"existing_trade_ready_weakened={metrics.get('existing_trade_ready_weakened')} "
                f"judge_triggered={str(metrics.get('judge_triggered')).lower()} "
                f"reason={metrics.get('trigger_reason')}"
            )
            prompt_set = set((self.today_judgment or {}).get("universe_tickers") or self.today_tickers.get(market, []) or [])
            prompt_candidates = [c for c in raw_candidates or [] if str(c.get("ticker", "") or "") in prompt_set]
            self._record_candidate_quality(
                market,
                "opening_fresh_observe",
                raw_candidates or [],
                prompt_candidates,
                self.today_tickers.get(market, []),
                self.selection_meta.get(market, {}),
                self.today_ticker_reasons.get(market, {}),
            )
            if bool(metrics.get("judge_triggered")):
                self.manual_rescreen(market)
        except Exception as exc:
            log.warning(f"[opening_fresh_quality] {market} failed: {exc}")
    def _build_current_breadth_summary(self, market: str, mode: str) -> dict:
        """Build a lightweight intraday breadth summary for tune prompts."""
        try:
            candidates = self._screen_market_candidates(market, mode)
            if market == "KR" and candidates:
                self._last_kr_candidates = candidates
            universe = (
                (self.today_universe.get(market) or {}).get("tickers")
                or (self.today_judgment or {}).get("universe_tickers")
                or []
            )
            if universe:
                candidates = self._restrict_candidates_to_universe(candidates, universe)
            ctx = ((self.today_judgment or {}).get("digest_raw") or {}).get("context") or {}
            summary = build_breadth_summary(market, candidates or [], ctx)
            summary["source"] = "intraday_screen"
            return summary
        except Exception as exc:
            log.debug("[tune breadth] %s current breadth unavailable: %s", market, exc)
            return {}
    def _build_intraday_context(self, market: str) -> str:
        """?μ쨷 ?ъ뒪?щ━?앹슜 ?ㅼ떆媛??쒖옣 而⑦뀓?ㅽ듃 臾몄옄???앹꽦."""
        try:
            from kis_api import get_index_change
            lines = []
            now_str = datetime.now(KST).strftime("%H:%M")
            mode = str((self.today_judgment.get("consensus", {}) or {}).get("mode", "NEUTRAL"))
            lines.append(self._build_execution_profile_text(market, mode))
            ops_context = self._format_ops_review_context(market)
            if ops_context:
                lines.append(ops_context)
            if market == "KR":
                kospi_chg = get_index_change("KR")
                lines.append(f"?꾩옱?쒓컖 {now_str} KST | 肄붿뒪???꾩옱 {kospi_chg:+.2f}%")
                # ?μ쨷 蹂댁쑀 ?ъ????꾪솴
                positions = list(self.positions.get(market, {}).values())
                if positions:
                    pos_strs = [
                        f"{p.get('ticker')} {p.get('unrealized_pnl_pct', 0):+.1f}%"
                        for p in positions[:5]
                    ]
                    lines.append(f"?꾩옱 蹂댁쑀: {' | '.join(pos_strs)}")
                # ?꾩묠 紐⑤뱶 ?鍮?吏??蹂??李멸퀬
                morning_kospi = 0.0
                try:
                    morning_kospi = (self.today_judgment or {}).get(
                        "digest_raw", {}).get("context", {}).get("kospi", {}).get("change_pct", 0.0) or 0.0
                except Exception:
                    pass
                if abs(kospi_chg - morning_kospi) >= 0.5:
                    direction = "?곸듅" if kospi_chg > morning_kospi else "?섎씫"
                    lines.append(
                        f"?μ쟾 ?鍮?{abs(kospi_chg - morning_kospi):.1f}%p {direction} "
                        f"(?μ쟾 {morning_kospi:+.2f}% ???꾩옱 {kospi_chg:+.2f}%)"
                    )
            else:
                sp_chg = get_index_change("US")
                lines.append(f"?꾩옱?쒓컖 {now_str} ET | S&P500 ?꾩옱 {sp_chg:+.2f}%")
                positions = list(self.positions.get(market, {}).values())
                if positions:
                    pos_strs = [
                        f"{p.get('ticker')} {p.get('unrealized_pnl_pct', 0):+.1f}%"
                        for p in positions[:5]
                    ]
                    lines.append(f"?꾩옱 蹂댁쑀: {' | '.join(pos_strs)}")
            return "\n".join(lines)
        except Exception as e:
            log.debug(f"[intraday_context ?앹꽦 ?ㅽ뙣] {e}")
            return ""
    def _advisor_pos(self, pos: dict, market: str) -> dict:
        """hold_advisor ?몄텧??pos 蹂듭궗蹂???mode/entry_time ??而⑦뀓?ㅽ듃 ?꾨뱶 二쇱엯."""
        mode = ""
        try:
            mode = (self.today_judgment or {}).get("consensus", {}).get("mode", "")
        except Exception:
            pass
        return {**pos, "mode": mode}
    def _flush_funnel(self, market: str):
        """?몄뀡 醫낅즺 ???쇰꼸 移댁슫?곕? ?쇰퀎 JSON ?뚯씪?????"""
        import json as _json
        from runtime_paths import get_runtime_path as _grp
        _funnel = self._funnel.get(market, {})
        if not any(_funnel.get(k, 0) for k in ("selected", "signaled", "ordered")):
            return  # ?꾨Т ?쒕룞 ?놁쑝硫?????앸왂
        today = self._current_session_date_str(market)
        log_dir = _grp("logs", "funnel", make_parents=True)
        log_file = log_dir / f"funnel_{today}_{market}.json"
        # 湲곗〈 ?뚯씪???덉쑝硫??꾩쟻
        existing = {}
        if log_file.exists():
            try:
                existing = _json.loads(log_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        for k in ("selected", "signaled", "ordered", "filled"):
            existing[k] = existing.get(k, 0) + _funnel.get(k, 0)
        # rejection_reasons ?꾩쟻
        for reason, cnt in _funnel.get("rejection_reasons", {}).items():
            existing.setdefault("rejection_reasons", {})[reason] = \
                existing.get("rejection_reasons", {}).get(reason, 0) + cnt
        # volume_states ?꾩쟻
        for vs, cnt in _funnel.get("volume_states", {}).items():
            existing.setdefault("volume_states", {})[vs] = \
                existing.get("volume_states", {}).get(vs, 0) + cnt
        existing["market"] = market
        existing["date"] = today
        log_file.write_text(_json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(
            f"[?쇰꼸] {market} {today} "
            f"selected={existing.get('selected',0)} "
            f"signaled={existing.get('signaled',0)} "
            f"ordered={existing.get('ordered',0)}"
        )
        # 移댁슫??珥덇린??(?ъ떆???鍮?
        self._funnel[market] = {
            "selected": 0, "signaled": 0, "ordered": 0, "filled": 0,
            "rejection_reasons": {}, "volume_states": {},
        }
    def manual_rescreen(self, market: Optional[str] = None) -> list[str]:
        """?붾젅洹몃옩 ?섎룞 紐낅졊?쇰줈 ?꾩옱 ?쒖옣 醫낅ぉ留?利됱떆 ?ъ꽑??"""
        target_market = market or self.current_market or self.today_judgment.get("market")
        if target_market not in ("KR", "US"):
            raise ValueError("?ъ꽑?앺븷 ?쒖옣???놁뒿?덈떎.")
        if not self.session_active:
            raise ValueError("?몄뀡??鍮꾪솢???곹깭?낅땲??")
        owner = "manual_rescreen"
        if not self._enter_market_task(target_market, owner):
            current = self._market_task_owner.get(target_market) or "-"
            raise RuntimeError(f"{target_market} ?쒖옣 ?묒뾽 吏꾪뻾 以? {current}")
        try:
            consensus = dict((self.today_judgment or {}).get("consensus") or {})
            mode = consensus.get("mode", "NEUTRAL")
            digest_prompt = (self.today_judgment or {}).get("digest_prompt", "")
            today = self._current_session_date_str(target_market)
            if target_market == "KR":
                candidates = self._screen_market_candidates("KR", self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
                if candidates:
                    self._last_kr_candidates = candidates
            else:
                candidates = self._screen_market_candidates("US", self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
            if not candidates:
                raise RuntimeError("?ъ뒪?щ━???꾨낫媛 ?놁뒿?덈떎.")
            raw_candidates = list(candidates or [])
            self._log_screen_candidates(target_market, candidates, "manual_rescreen")
            candidates = self._prefill_history_sync(candidates, target_market)
            candidates = self._filter_candidates_by_history(candidates, target_market)
            _manual_cooldown = self._get_cooldown_excluded(target_market)
            candidates = [c for c in candidates if c.get("ticker") not in _manual_cooldown]
            if not candidates:
                raise RuntimeError("?덉뒪?좊━ ?꾪꽣 ?듦낵 ?꾨낫媛 ?놁뒿?덈떎.")
            candidates = self._annotate_selection_execution_features(target_market, candidates, mode)
            intraday_ctx = self._build_intraday_context(target_market)
            lesson_context = self._load_lesson_candidate_summary(target_market)
            selected, reasons = select_tickers(target_market, digest_prompt, mode, candidates,
                                               lesson_context=lesson_context,
                                               intraday_context=intraday_ctx,
                                               market_change_pct=self._get_market_change_pct(target_market),
                                               secondary_change_pct=self._get_secondary_change_pct(target_market))
            if not selected:
                raise RuntimeError("理쒖쥌 ?좏깮 醫낅ぉ???놁뒿?덈떎.")
            sel_meta = self._apply_selection_meta(target_market, selected)
            self._record_candidate_quality(
                target_market,
                "manual_rescreen",
                raw_candidates,
                candidates,
                selected,
                sel_meta,
                reasons,
            )
            self._update_candidate_health(
                target_market,
                "manual_rescreen",
                selected,
                sel_meta,
                candidates,
            )
            self.today_tickers[target_market] = selected
            self._entry_timing_mark_candidates(target_market, selected, "manual_rescreen")
            self.today_ticker_reasons[target_market] = reasons or {}
            self.today_judgment["tickers"] = selected
            self.today_judgment["universe_tickers"] = [
                c.get("ticker") for c in candidates if c.get("ticker")
            ]
            log.info(
                f"[수동 종목 재선정 완료] {target_market}: watch={selected} "
                f"trade_ready={sel_meta.get('trade_ready', [])}"
            )
            judgment_log.info(
                f"[manual_rescreen {today} {target_market}] {selected}",
                extra={"extra": {
                    "event": "ticker_rescreen",
                    "date": today,
                    "market": target_market,
                    "selected": selected,
                    "trade_ready": sel_meta.get("trade_ready", []),
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
    # ?? ?꾨낫 ?덉뒪?좊━ ?꾪꽣 ????????????????????????????????????????????????????
    _MIN_SIGNAL_ROWS = 65  # calc_all ???좏슚 ??理쒖냼移?(ma60=60遊?+ ?ъ쑀 5遊?
    def _signal_usable_rows(self, df) -> int:
        if df is None or getattr(df, "empty", True):
            return 0
        try:
            return len(calc_all(df))
        except Exception:
            return 0
    def _merge_ohlcv_frames(self, *frames):
        import pandas as _pd
        valid = []
        for frame in frames:
            if frame is None or getattr(frame, "empty", True):
                continue
            normalized = frame.copy()
            normalized.columns = [str(col).lower() for col in normalized.columns]
            if "date" not in normalized.columns:
                continue
            valid.append(normalized)
        if not valid:
            return _pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        return (
            _pd.concat(valid, ignore_index=True)
            .drop_duplicates("date")
            .sort_values("date")
            .reset_index(drop=True)
        )
    def _fetch_signal_ready_ohlcv(self, ticker: str, market: str, lookback_days: Optional[int] = None):
        target_usable = self._MIN_SIGNAL_ROWS
        if lookback_days is None:
            lookback_days = 365 if market == "KR" else max(260, target_usable + 120)
        df = get_daily_ohlcv(ticker, self.token, lookback_days=lookback_days, market=market)
        usable = self._signal_usable_rows(df)
        source = "primary"
        if market == "US" and usable < target_usable:
            fallback_lookback = max(int(lookback_days), 365)
            for fallback_name, fetcher in (
                ("yfinance", _daily_ohlcv_us_yf),
                ("alpha", _daily_ohlcv_us_alpha),
            ):
                try:
                    fallback_df = fetcher(ticker, lookback_days=fallback_lookback)
                except Exception:
                    fallback_df = None
                merged = self._merge_ohlcv_frames(df, fallback_df)
                merged_usable = self._signal_usable_rows(merged)
                if merged_usable > usable:
                    df = merged
                    usable = merged_usable
                    source = fallback_name
                if usable >= target_usable:
                    break
        return df, usable, source
    def _enrich_candidate_with_history(self, candidate: dict, candles, sig_df) -> dict:
        enriched = dict(candidate or {})
        if candles is None or getattr(candles, "empty", True) or sig_df is None or getattr(sig_df, "empty", True):
            return enriched
        try:
            last_candle = candles.iloc[-1]
            last_sig = sig_df.iloc[-1]
        except Exception:
            return enriched
        price_ref = float(enriched.get("price", 0) or 0) or float(last_candle.get("close", 0) or 0)
        day_high = float(last_candle.get("high", 0) or 0)
        ma60 = float(last_sig.get("ma60", 0) or 0)
        if enriched.get("gap_pct") is None:
            try:
                gap_pct = last_sig.get("gap_pct")
                if gap_pct is not None:
                    enriched["gap_pct"] = float(gap_pct)
            except (TypeError, ValueError):
                pass
        if enriched.get("from_high_pct") is None and price_ref > 0 and day_high > 0:
            enriched["from_high_pct"] = round((price_ref - day_high) / day_high * 100.0, 4)
        if enriched.get("above_ma60") is None and price_ref > 0 and ma60 > 0:
            enriched["above_ma60"] = bool(price_ref >= ma60)
        return enriched
    def _filter_candidates_by_history(self, candidates: list, market: str) -> list:
        """
        ?ㅽ겕由щ꼫 ?꾨낫 以??좏샇 怨꾩궛???꾩슂???덉뒪?좊━媛 遺議깊븳 醫낅ぉ ?쒓굅.
        - 怨꾩쥖/?꾨왂?먯꽌 留ㅻℓ?섏? ?딆쓣 ?뚯깮/?덈쾭由ъ?/?몃쾭??ETF ?꾨낫瑜?癒쇱? ?쒓굅
        - calc_all() ???좏슚 ??ma60 湲곗? dropna) < _MIN_SIGNAL_ROWS ??醫낅ぉ ?쒖쇅
        - 媛寃??곗씠???먯껜媛 ?녿뒗 醫낅ぉ???쒖쇅
        """
        candidates, product_removed = filter_tradable_candidates(candidates, market)
        filtered = []
        removed = [
            (c.get("ticker", ""), c.get("blocked_reason", "product_blocked"))
            for c in product_removed
        ]
        for c in candidates:
            ticker = c.get("ticker", "")
            if not ticker:
                continue
            try:
                candles = self._get_ohlcv_cached(ticker, market)
                if candles.empty:
                    removed.append((ticker, "罹붾뱾?놁쓬"))
                    continue
                sig_df = calc_all(candles)
                if len(sig_df) < self._MIN_SIGNAL_ROWS:
                    removed.append((ticker, f"?곗씠?곕?議?{len(sig_df)}??"))
                    continue
                filtered.append(self._enrich_candidate_with_history(c, candles, sig_df))
            except Exception as e:
                removed.append((ticker, f"?ㅻ쪟:{e}"))
                continue
        if removed:
            log.info(
                f"[?덉뒪?좊━ ?꾪꽣] {market} ?쒖쇅 {len(removed)}媛? "
                + ", ".join(f"{t}({r})" for t, r in removed)
            )
        log.info(f"[?덉뒪?좊━ ?꾪꽣] {market} ?꾨낫 {len(candidates)}媛????좏슚 {len(filtered)}媛??듦낵 (?쒖쇅 {len(removed)}媛?")
        # ?곗씠?곕?議?醫낅ぉ? 諛깃렇?쇱슫???덉뒪?좊━ 蹂닿컯 ?먯뿉 ?깅줉
        for t, r in removed:
            if "data" in r or "?곗씠?곕?議?" in r:
                self._hist_fill_enqueue(t, market)
        return filtered
    # ?? API ?ъ뒪泥댄겕 ???????????????????????????????????????????????????????????
    def _startup_health_check(self):
        """Run startup API health checks."""
        import os as _os
        import requests as _req
        from kis_api import BASE_URL, IS_PAPER as _KIS_PAPER
        results: dict[str, str] = {}
        mode_label = "paper" if self.is_paper else "live"
        server_type = "paper_server" if _KIS_PAPER else "live_server"
        server_url  = BASE_URL
        # 1. KIS 紐⑤뱶 / ?쒕쾭
        results[f"KIS 紐⑤뱶"] = f"OK | {mode_label} ({server_type})"
        results["KIS ?쒕쾭"]  = f"OK | {server_url}"
        # 2. KIS ?좏겙
        results["KIS ?좏겙"] = "OK" if self.token else "FAIL - ?좏겙 ?놁쓬"
        # 3. KIS ?쒖꽭 (?쇱꽦?꾩옄)
        try:
            price_info = get_price("005930", self.token, market="KR")
            results["KIS price (005930)"] = f"OK | {price_info['price']:,} KRW"
        except Exception as e:
            results["KIS ?쒖꽭 (005930)"] = f"FAIL - {e}"
        # 4. KIS ?붽퀬
        try:
            bal = get_balance(self.token, market="KR")
            total = bal["cash"] + bal["total_eval"]
            results["KIS ?붽퀬"] = (
                f"OK | 珥?{total:,}??(?꾧툑 {bal['cash']:,} + ?됯? {bal['total_eval']:,})"
            )
        except Exception as e:
            results["KIS ?붽퀬"] = f"FAIL - {e}"
        # 5. Finnhub (US ?꾩옱媛)
        fh_key = _os.getenv("FINNHUB_API_KEY", "")
        if fh_key:
            try:
                resp = _req.get(
                    "https://finnhub.io/api/v1/quote",
                    params={"symbol": "NVDA", "token": fh_key}, timeout=5,
                )
                resp.raise_for_status()
                price = resp.json().get("c", 0)
                results["Finnhub (NVDA)"] = f"OK | ${price:.2f}" if price else "WARN - 媛寃?0"
            except Exception as e:
                results["Finnhub (NVDA)"] = f"FAIL - {e}"
        else:
            results["Finnhub"] = "SKIP - FINNHUB_API_KEY ?놁쓬"
        # 6. FMP (US ?ㅽ겕由щ꼫)
        fmp_key = _os.getenv("FMP_API_KEY", "")
        if fmp_key:
            try:
                resp = _req.get(
                    "https://financialmodelingprep.com/stable/most-actives",
                    params={"apikey": fmp_key}, timeout=5,
                )
                resp.raise_for_status()
                cnt = len(resp.json()) if isinstance(resp.json(), list) else 0
                results["FMP (?ㅽ겕由щ꼫)"] = f"OK | {cnt}媛?醫낅ぉ"
            except Exception as e:
                results["FMP (?ㅽ겕由щ꼫)"] = f"FAIL - {e}"
        else:
            results["FMP"] = "SKIP - FMP_API_KEY ?놁쓬"
        # 7. Alpha Vantage KEY-1 / KEY-2
        av_key  = _os.getenv("ALPHA_VANTAGE_KEY", "")
        av_key2 = _os.getenv("ALPHA_VANTAGE_KEY_2", "")
        if av_key:
            try:
                candles = get_daily_ohlcv("NVDA", self.token, lookback_days=5, market="US")
                results["Alpha Vantage KEY-1"] = (
                    f"OK | {len(candles)} candles" if not candles.empty else "WARN - empty data"
                )
            except Exception as e:
                results["Alpha Vantage KEY-1"] = f"FAIL - {e}"
        else:
            results["Alpha Vantage KEY-1"] = "SKIP - ???놁쓬"
        results["Alpha Vantage KEY-2"] = ("OK | ?湲곗쨷" if av_key2 else "SKIP - ???놁쓬")
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
                results["Telegram"] = f"FAIL - {mask_secrets(e)}"
        else:
            results["Telegram"] = "SKIP - TELEGRAM_TOKEN ?놁쓬"
        # 9. DART (怨듭떆) - ?좏깮
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
            results["DART API"] = "SKIP - ???놁쓬"
        # 寃곌낵 濡쒓렇 異쒕젰
        ok_count   = sum(1 for v in results.values() if v.startswith("OK"))
        fail_count = sum(1 for v in results.values() if v.startswith("FAIL"))
        log.info(f"??? API Health Check [{mode_label}] ????????????????????")
        for name, status in results.items():
            icon = "OK" if status.startswith("OK") else ("FAIL" if status.startswith("FAIL") else "WARN")
            log.info(f"  {icon} {name}: {status}")
        log.info(f"???????????????????????????? OK={ok_count} / FAIL={fail_count} ??")
        # ?붾젅洹몃옩
        lines = [f"?쨼 遊??쒖옉 [{mode_label}] ??API ?먭? 寃곌낵"]
        for name, status in results.items():
            icon = "OK" if status.startswith("OK") else ("FAIL" if status.startswith("FAIL") else "WARN")
            lines.append(f"{icon} {name}: {status}")
        if fail_count:
            lines.append(f"\n?좑툘 {fail_count}媛?API ?곌껐 ?ㅽ뙣 ???뺤씤 ?꾩슂")
        system_alert(f"遊??쒖옉 [{mode_label}] ??API ?먭? 寃곌낵", lines[1:], icon="?쨼")
    # ?? ?쒖옣蹂??덉궛 議고쉶 ??????????????????????????????????????????????????????
    def _market_budget_available(self, market: str) -> float:
        """Return available shared cash budget for the market."""
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
            self.claude_control["last_error"] = "Claude ?ы뙋??湲곕뒫??OFF ?곹깭?낅땲??"
            self._save_claude_control()
            return
        try:
            self._reinvoke_analysts(market, pending.get("source", "dashboard"))
            self.claude_control["last_result_status"] = "success"
            self.claude_control["last_error"] = ""
        except Exception as e:
            self.claude_control["last_result_status"] = "error"
            self.claude_control["last_error"] = str(e)
            log.error(f"[Claude ??쒕낫???몃━嫄??ㅽ뙣] {market}: {e}")
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
            log.info(f"[?ъ????ы뙋???몃━嫄?泥섎━] {market} {ticker} | {source}")
        except Exception as e:
            self.claude_control["last_result_status"] = "error"
            self.claude_control["last_error"] = str(e)
            log.error(f"[?ъ????ы뙋???몃━嫄??ㅽ뙣] {market} {ticker}: {e}")
        finally:
            self.claude_control["pending_position_review"] = None
            self.claude_control["last_result_at"] = datetime.now(KST).isoformat(timespec="seconds")
            self.claude_control["last_result_market"] = market
            self._save_claude_control()
    def _consume_pending_sell(self, market: str):
        """??쒕낫??利됱떆 留ㅻ룄 踰꾪듉 ??claude_control.json pending_sell ?뚮퉬"""
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
                raise ValueError(f"{ticker} ?ъ????놁쓬")
            raw_px = sell_price if sell_price > 0 else self.price_cache_raw.get(ticker, pos.get("current_price", 0))
            exit_px = float(pos.get("current_price", 0) or 0)
            if market == "US" and raw_px > 0 and self.usd_krw_rate > 0:
                exit_px = raw_px * self.usd_krw_rate
            elif market != "US" and raw_px > 0:
                exit_px = raw_px
            cand = {**pos, "exit_price": exit_px, "reason": "manual_sell"}
            self.price_cache_raw[ticker] = raw_px
            self._execute_sell(cand, market, reason="manual_sell")
            log.info(f"[利됱떆 留ㅻ룄] {market} {ticker} @ {raw_px:,.2f} (??쒕낫???붿껌)")
            self.claude_control["last_result_status"] = "success"
            self.claude_control["last_error"] = ""
        except Exception as e:
            self.claude_control["last_result_status"] = "error"
            self.claude_control["last_error"] = str(e)
            log.error(f"[利됱떆 留ㅻ룄 ?ㅽ뙣] {market} {ticker}: {e}")
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
        """ticker 吏꾩엯??minutes遺??숈븞 李⑤떒"""
        import time as _time
        self._entry_blocked[ticker] = _time.time() + minutes * 60
        log.info(f"[진입 차단] {ticker} {minutes}분({reason})")
    def _is_entry_blocked(self, ticker: str) -> bool:
        """李⑤떒 以묒씠硫?True, 留뚮즺?먯쑝硫??쒓굅 ??False"""
        import time as _time
        until = self._entry_blocked.get(ticker, 0)
        if _time.time() < until:
            return True
        if ticker in self._entry_blocked:
            del self._entry_blocked[ticker]
        return False
    def _has_same_day_trade(self, ticker: str, market: str) -> bool:
        """?뱀씪 ?숈씪 醫낅ぉ 泥닿껐 ?대젰???덉쑝硫??ъ쭊??湲덉?."""
        today_iso = self._current_session_date_str(market)
        normalized = ticker.upper() if market == "US" else ticker
        for evt in reversed(self.risk.all_trade_log):
            evt_ticker = evt.get("ticker", "")
            evt_market = evt.get("market") or self._ticker_market(evt_ticker)
            evt_norm = evt_ticker.upper() if evt_market == "US" else evt_ticker
            evt_session_date = str(evt.get("session_date") or evt.get("date") or "")
            if evt_session_date != today_iso:
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
        lower = text.lower()
        if "breakout=" in lower and "volume" in lower:
            return "missing signal: breakout and volume condition not met"
        priority = [
            ("volume", "missing signal: volume condition not met"),
            ("rsi", "missing signal: RSI condition not met"),
            ("bb", "missing signal: Bollinger condition not met"),
            ("breakout", "missing signal: breakout condition not met"),
            ("macd", "missing signal: MACD condition not met"),
            ("ma60", "missing signal: ma60 condition not met"),
        ]
        hits = [label for key, label in priority if key in lower]
        if not hits:
            return ""
        if len(hits) == 1:
            return hits[0]
        return f"{hits[0]} + {hits[1].replace('missing signal: ', '')}"
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
        summary: str = "",
        qty: int = 0,
        order_cost_krw: float = 0.0,
        detail: str = "",
    ) -> None:
        key = (str(market or ""), str(ticker or ""), str(event or ""))
        _detail_key = str(detail or "")[:80]
        signature = (str(strategy or ""), round(float(price or 0), 4), str(reason or ""), str(mode or ""), str(summary or ""), _detail_key)
        if self._last_tg_signal_state.get(key) == signature:
            return
        self._last_tg_signal_state[key] = signature
        _effective_summary = summary or (str(detail or "")[:200] if detail else "")
        try:
            signal_state_alert(
                event=event,
                market=market,
                ticker=ticker,
                strategy=strategy,
                price=price,
                reason=reason,
                mode=mode,
                summary=_effective_summary,
                order_cost_krw=order_cost_krw,
            )
        except Exception as e:
            log.debug(f"[signal state alert failed] {ticker} {event}: {e}")
    def _restore_positions(self):
        """??λ맂 ?댁썡 ?ъ???蹂듦뎄"""
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
                market = self._ticker_market(pos.get("ticker", ""))
                # date ?꾨뱶媛 臾몄옄?대줈 ??λ릱?????덉쑝誘濡????蹂댁젙
                pos.setdefault("held_days", 0)
                pos.setdefault("entry_date", self._current_session_date_str(market))
                pos.setdefault("session_date", pos.get("entry_date") or self._current_session_date_str(market))
                default_integrity = "protected" if str(pos.get("price_source", "") or "") == "broker_balance" else "trusted"
                default_protected = default_integrity != "trusted"
                self._normalize_position_metadata(
                    pos,
                    market,
                    origin="saved_restore",
                    integrity=default_integrity,
                    management_protected=default_protected,
                )
                cost = pos["entry"] * pos["qty"]
                self.risk.cash -= cost
                self.risk.positions.append(pos)
            log.info(f"[?ъ???蹂듦뎄] {[p['ticker'] for p in saved]} "
                     f"(珥?{len(saved)}媛?/ ?꾧툑 ?붿뿬 {self.risk.cash:,.0f}??")
        except Exception as e:
            log.error(f"?ъ???蹂듦뎄 ?ㅽ뙣: {e}")
    def _broker_reconcile_key(self, market: str, ticker: str) -> tuple[str, str]:
        raw_ticker = str(ticker or "").strip()
        return market, raw_ticker.upper() if market == "US" else raw_ticker
    def _pending_orders_by_key(self, market: Optional[str] = None) -> dict[tuple[str, str], list[dict]]:
        grouped: dict[tuple[str, str], list[dict]] = {}
        for order in self.pending_orders:
            order_market = str(order.get("market", "KR") or "KR").strip().upper()
            if market and order_market != market:
                continue
            key = self._broker_reconcile_key(order_market, order.get("ticker", ""))
            grouped.setdefault(key, []).append(order)
        return grouped
    def _pending_order_no_key(self, market: str, order_no: str) -> tuple[str, str]:
        return str(market or "KR").strip().upper(), str(order_no or "").strip()
    def _pending_ticker_key(self, order: dict) -> tuple[str, str]:
        order_market = str(order.get("market", "KR") or "KR").strip().upper()
        return self._broker_reconcile_key(order_market, order.get("ticker", ""))
    def _flag_duplicate_pending_exposure(self, market: str, ticker: str) -> None:
        key = self._broker_reconcile_key(market, ticker)
        same = [o for o in self.pending_orders if self._pending_ticker_key(o) == key]
        order_nos = sorted({str(o.get("order_no", "") or "").strip() for o in same if str(o.get("order_no", "") or "").strip()})
        if len(order_nos) < 2:
            return
        for order in same:
            order["duplicate_ticker_pending"] = True
            order["duplicate_ticker_order_nos"] = order_nos
        try:
            self._flag_execution_issue(market, "duplicate_ticker_pending")
        except Exception:
            pass
        log.warning(
            f"[pending duplicate exposure] {market} {ticker} has multiple local pending order_nos={order_nos}"
        )
    def _broker_truth_market_snapshot(self, market: str, *, force: bool = False, ttl_sec: Optional[int] = None) -> dict:
        pathb = getattr(self, "pathb", None)
        if pathb is None or getattr(pathb, "broker_truth", None) is None:
            return {}
        market_key = str(market or "").upper()
        if force:
            try:
                pathb.refresh_broker_truth(market_key, force=True, ttl_sec=ttl_sec)
            except TypeError:
                pathb.refresh_broker_truth(market_key, force=True)
        return dict(pathb.broker_truth.market_snapshot(market_key, ttl_sec=ttl_sec))
    def _reconcile_broker_open_orders(self, market: str, *, reason: str = "", force: bool = False) -> dict:
        market_key = str(market or "").upper()
        summary = {
            "market": market_key,
            "reason": reason,
            "broker_only_open_orders": [],
            "local_only_pending_orders": [],
            "duplicate_ticker_orders": [],
            "broker_truth_unavailable": False,
        }
        try:
            market_data = self._broker_truth_market_snapshot(market_key, force=force)
        except Exception as exc:
            summary["broker_truth_unavailable"] = True
            summary["error"] = str(exc)
            log.warning(f"[broker/local open order reconcile] {market_key} broker truth failed: {exc}")
            return summary
        if not market_data or bool(market_data.get("missing")) or bool(market_data.get("stale")) or str(market_data.get("error", "") or ""):
            summary["broker_truth_unavailable"] = True
            return summary
        local_orders = [o for o in self.pending_orders if str(o.get("market", "KR") or "KR").strip().upper() == market_key]
        local_by_order_no = {
            self._pending_order_no_key(market_key, o.get("order_no", ""))[1]: o
            for o in local_orders
            if str(o.get("order_no", "") or "").strip()
        }
        broker_open = [
            row for row in list(market_data.get("open_orders", []) or [])
            if int(row.get("remaining_qty", 0) or 0) > 0
        ]
        broker_by_order_no = {
            str(row.get("order_no", "") or "").strip(): row
            for row in broker_open
            if str(row.get("order_no", "") or "").strip()
        }
        for order_no, row in broker_by_order_no.items():
            if order_no in local_by_order_no:
                continue
            ticker = str(row.get("ticker", "") or "").strip()
            payload = {
                "market": market_key,
                "ticker": ticker,
                "order_no": order_no,
                "side": row.get("side", ""),
                "qty": int(row.get("order_qty", row.get("qty", 0)) or 0),
                "remaining_qty": int(row.get("remaining_qty", 0) or 0),
                "reason": reason,
            }
            summary["broker_only_open_orders"].append(payload)
            try:
                unknown = getattr(self, "v2_order_unknown", None)
                if unknown is not None and hasattr(unknown, "record_broker_open_order"):
                    unknown.record_broker_open_order(**payload)
            except Exception as exc:
                log.debug(f"[broker/local open order reconcile] unknown registry update failed: {exc}")
            try:
                self._flag_execution_issue(market_key, "broker_only_open_order")
            except Exception:
                pass
        for order_no, order in local_by_order_no.items():
            if order_no not in broker_by_order_no:
                summary["local_only_pending_orders"].append(
                    {
                        "market": market_key,
                        "ticker": order.get("ticker", ""),
                        "order_no": order_no,
                        "qty": int(order.get("qty", 0) or 0),
                        "reason": reason,
                    }
                )
        grouped: dict[tuple[str, str], list[str]] = {}
        for row in broker_open:
            ticker = str(row.get("ticker", "") or "").strip()
            if not ticker:
                continue
            key = self._broker_reconcile_key(market_key, ticker)
            order_no = str(row.get("order_no", "") or "").strip()
            if order_no:
                grouped.setdefault(key, []).append(order_no)
        for key, order_nos in grouped.items():
            uniq = sorted(set(order_nos))
            if len(uniq) >= 2:
                summary["duplicate_ticker_orders"].append({"market": key[0], "ticker": key[1], "order_nos": uniq})
                try:
                    unknown = getattr(self, "v2_order_unknown", None)
                    if unknown is not None and hasattr(unknown, "record_duplicate_open_orders"):
                        unknown.record_duplicate_open_orders(market=key[0], ticker=key[1], order_nos=uniq, reason=reason)
                except Exception as exc:
                    log.debug(f"[broker/local open order reconcile] duplicate registry update failed: {exc}")
        if summary["broker_only_open_orders"] or summary["duplicate_ticker_orders"]:
            log.warning(f"[broker/local open order mismatch] {summary}")
        return summary
    def _verify_live_positions(self, saved: list) -> list:
        """Broker holdings + pending orders are the source of truth for saved positions."""
        broker_balances: dict[str, dict] = {"KR": {}, "US": {}}
        broker_ok = {"KR": False, "US": False}
        pending_by_key = self._pending_orders_by_key()
        for market in ("KR", "US"):
            try:
                balance = get_balance(self.token, market=market)
                broker_balances[market] = self._normalize_broker_balance(balance, market)
                broker_ok[market] = True
                self._set_broker_state(
                    market,
                    trust_level="trusted",
                    snapshot=self._broker_snapshot_from_balance(market, balance),
                )
            except Exception as e:
                log.error(f"[broker verify] {market} balance lookup failed: {e}")
        if not broker_ok["KR"] and not broker_ok["US"]:
            log.error("[broker verify] KR/US balance lookup both failed; keeping saved positions")
            return saved
        verified: list[dict] = []
        for pos in saved:
            ticker = str(pos.get("ticker", "") or "").strip()
            if not ticker:
                continue
            market = self._ticker_market(ticker)
            key = self._broker_reconcile_key(market, ticker)
            if not broker_ok[market]:
                log.warning(f"[broker verify] {market} lookup unavailable; keeping {ticker}")
                verified.append(
                    self._normalize_position_metadata(
                        pos,
                        market,
                        origin="saved_restore",
                        integrity="protected",
                        management_protected=True,
                    )
                )
                continue
            broker_pos = broker_balances[market].get(key[1])
            pending_orders = pending_by_key.get(key) or []
            if broker_pos and int(broker_pos.get("qty", 0) or 0) > 0:
                broker_qty = int(broker_pos.get("qty", 0) or 0)
                if broker_qty != int(pos.get("qty", 0) or 0):
                    log.warning(
                        f"[broker verify] {market} {ticker} qty corrected "
                        f"{int(pos.get('qty', 0) or 0)} -> {broker_qty}"
                    )
                    self._flag_execution_issue(market, "broker_qty_corrected")
                    pos["qty"] = broker_qty
                verified.append(pos)
                continue
            if pending_orders:
                log.warning(
                    f"[broker verify] {market} {ticker} missing from holdings but pending exists; keep protected"
                )
                pos["broker_reconcile_status"] = "pending_only"
                verified.append(
                    self._normalize_position_metadata(
                        pos,
                        market,
                        origin="saved_restore",
                        integrity="protected",
                        management_protected=True,
                    )
                )
                continue
            self._flag_execution_issue(market, "broker_position_removed")
            log.warning(f"[broker verify] removed stale legacy position: {market} {ticker}")
        return verified
    def _make_runtime_position_from_broker(self, ticker: str, market: str, broker_pos: dict,
                                           template: Optional[dict] = None) -> dict:
        template = template or {}
        trusted_template = bool(template)
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
        pos = {
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
            "entry_date": template.get("entry_date", self._current_session_date_str(market)),
            "session_date": template.get("session_date", self._current_session_date_str(market)),
            "entry_session_date": template.get(
                "entry_session_date",
                template.get("session_date", self._current_session_date_str(market)),
            ),
            "trailing": bool(template.get("trailing", False)),
            "trail_sl": float(template.get("trail_sl", 0.0)),
            "trail_sl_usd": float(template.get("trail_sl_usd", 0.0)),
            "trail_pct": float(template.get("trail_pct", 0.03)),
            "tp_triggered": bool(template.get("tp_triggered", False)),
            "hold_advice": template.get("hold_advice"),
            "tp_price": float(template.get("tp_price", 0.0)),
            "decision_id": template.get("decision_id") or self._recover_decision_id(ticker, market),
        }
        return self._normalize_position_metadata(
            pos,
            market,
            origin="broker_recovered" if trusted_template else "broker_injected",
            integrity="trusted" if trusted_template else "protected",
            management_protected=not trusted_template,
        )
    def _recover_decision_id(self, ticker: str, market: str) -> Optional[int]:
        """釉뚮줈而?蹂듦뎄 ?ъ??섏뿉 ???ML DB?먯꽌 decision_id瑜?best-effort濡?議고쉶.
        ?ㅻ뒛 ?좎쭨 BUY_SIGNAL 以??꾩쭅 filled=0???됱쓣 李얠븘 ?곌껐?쒕떎."""
        if not _ML_DB_ENABLED:
            return None
        try:
            from ml.db_writer import _get_conn
            session_date = self._current_session_date_str(market)
            with _get_conn() as conn:
                row = conn.execute(
                    """SELECT id FROM decisions
                       WHERE market=? AND ticker=? AND session_date=?
                         AND decision='BUY_SIGNAL' AND filled=0
                       ORDER BY id DESC LIMIT 1""",
                    (market, ticker, session_date),
                ).fetchone()
            if row:
                log.debug(f"[ML DB 蹂듦뎄] {ticker} decision_id={row[0]}")
                return row[0]
        except Exception:
            pass
        return None
    def _normalize_position_metadata(
        self,
        pos: dict,
        market: str,
        *,
        origin: str,
        integrity: str,
        management_protected: bool,
    ) -> dict:
        current_session = self._current_session_date_str(market)
        entry_session = str(
            pos.get("entry_session_date")
            or pos.get("session_date")
            or pos.get("entry_date")
            or current_session
        ).strip()
        pos["entry_session_date"] = entry_session
        pos.setdefault("entry_date", entry_session)
        pos.setdefault("session_date", entry_session)
        pos["position_origin"] = str(pos.get("position_origin") or origin)
        pos["position_integrity"] = str(pos.get("position_integrity") or integrity)
        pos["management_protected"] = bool(pos.get("management_protected", management_protected))
        if pos["management_protected"]:
            pos.setdefault("recovery_session_date", current_session)
        return pos
    def _count_session_holding_days(self, market: str, entry_session_iso: str, current_session_iso: str) -> int:
        try:
            entry_session = date.fromisoformat(str(entry_session_iso or "").strip())
            current_session = date.fromisoformat(str(current_session_iso or "").strip())
        except Exception:
            return 0
        if current_session <= entry_session:
            return 0
        held_days = 0
        cursor = entry_session + timedelta(days=1)
        while cursor <= current_session:
            if _is_trading_day(market, cursor):
                held_days += 1
            cursor += timedelta(days=1)
        return held_days
    def _refresh_position_holding_days(self, market: str):
        current_session = self._current_session_date_str(market)
        for pos in self.risk.positions:
            ticker = str(pos.get("ticker", "") or "").strip()
            if self._ticker_market(ticker) != market:
                continue
            entry_session = str(
                pos.get("entry_session_date")
                or pos.get("session_date")
                or pos.get("entry_date")
                or current_session
            ).strip()
            pos["entry_session_date"] = entry_session
            pos["held_days"] = self._count_session_holding_days(market, entry_session, current_session)
            pos["last_hold_update"] = current_session
    def _internal_total_equity_krw(self) -> float:
        return float(self.risk.cash + sum(
            p.get("qty", 0) * p.get("current_price", p.get("entry", 0))
            for p in self.risk.positions
        ))
    def _daily_pnl_pct(self, market: Optional[str] = None) -> float:
        if market:
            base = self._market_session_start_equity_krw(market)
            return float(self.risk.daily_pnl / max(base, 1.0) * 100.0)
        return float(self.risk.daily_pnl / max(self.risk.session_start_equity, 1) * 100.0)
    def _normalize_broker_balance(self, balance: Optional[dict], market: str) -> dict:
        """get_balance() ?묐떟??ticker -> position dict ?뺥깭濡??뺢퇋??"""
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
        # ?ъ??섏씠 ?놁쓣 ?뚮뒗 degraded ?곹깭?먯꽌???좉퇋 吏꾩엯 ?덉슜
        # (蹂댄샇??湲곗〈 ?ъ??섏씠 ?놁쑝誘濡?釉뚮줈而?遺덉떊??吏꾩엯??留됱쓣 ?댁쑀 ?놁쓬)
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
            # ?ъ??섏씠 ?덉쓣 ?뚮쭔 halt ???놁쑝硫?釉뚮줈而?遺덉떊???댁쁺??留됱쓣 ?댁쑀 ?놁쓬
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
        detail_lower = str(detail or "").lower()
        if "insufficient_holding" in detail_lower or "not enough" in detail_lower:
            next_open_ts = self._next_market_open_dt(market).timestamp()
            self._sell_fail_at[ticker] = max(self._sell_fail_at.get(ticker, 0.0), next_open_ts)
            log.warning(f"[sell failure blocked] {ticker} insufficient holding; retry deferred to next open")
            return
        if count >= 3:
            self._sell_fail_at[ticker] = now + 600
            log.warning(f"[sell failure cooldown] {ticker} repeated={count} reason={reason} cooldown=10m")
            self._record_decision_event(
                market, "sell_retry_suppressed", ticker,
                strategy="",
                reason=reason,
                detail=f"same_failure_count={count} {detail}".strip(),
            )
    def _kis_total_equity_krw(self) -> float:
        """?듯빀怨꾩쥖 湲곗? KIS 珥앹옄??KRW). KR+US ?붽퀬 ?⑹궛 ???ㅽ뙣 ???대? 怨꾩궛媛믪쑝濡??대갚."""
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
            log.warning(f"[?꾩쟻?먯궛 ?숆린?? KR ?붽퀬 議고쉶 ?ㅽ뙣: {e}")
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
            log.debug(f"[?꾩쟻?먯궛 ?숆린?? US ?붽퀬 議고쉶 ?ㅽ뙣: {e}")
        if not kr_ok and not us_ok:
            return float(self._equity_reference_context().get("total_krw", fallback) or fallback)
        total = (kr_total if kr_ok else 0.0) + (us_total_krw if us_ok else 0.0)
        log.info(
            f"[broker equity] total={total:,.0f} KRW "
            f"(KR {kr_total:,.0f} + US {us_total_krw:,.0f})"
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
    def _market_position_value_krw(self, market: str) -> float:
        total = 0.0
        for pos in self.risk.positions:
            ticker = str(pos.get("ticker", "") or "").strip()
            if self._ticker_market(ticker) != market:
                continue
            total += float(pos.get("current_price", pos.get("entry", 0)) or 0) * int(pos.get("qty", 0) or 0)
        return float(total)
    def _market_unrealized_session_pnl_krw(self, market: str) -> float:
        session_date = self._current_session_date_str(market)
        total = 0.0
        for pos in self.risk.positions:
            ticker = str(pos.get("ticker", "") or "").strip()
            if self._ticker_market(ticker) != market:
                continue
            qty = int(pos.get("qty", 0) or 0)
            if qty <= 0:
                continue
            current = float(pos.get("current_price", pos.get("entry", 0)) or 0)
            entry_session = str(
                pos.get("entry_session_date")
                or pos.get("session_date")
                or pos.get("entry_date")
                or ""
            )
            if entry_session == session_date:
                basis = float(pos.get("entry", 0) or 0)
            else:
                basis = float(pos.get("session_open_price_krw", 0) or pos.get("session_open_price", 0) or 0)
            if basis <= 0 or current <= 0:
                continue
            total += (current - basis) * qty
        return float(total)
    def _market_realized_pnl_krw(self, market: str) -> float:
        market_key = str(market or "").upper()
        session_date = self._current_session_date_str(market_key)
        realized = 0.0
        seen = set()
        def _realized_key(ticker: str, qty: int, pnl: float, reason: str) -> str:
            return f"{ticker}:{int(qty or 0)}:{round(float(pnl or 0), 4)}:{str(reason or '')}:{session_date}"
        for evt in list(getattr(self.risk, "all_trade_log", []) or []):
            if str(evt.get("side", "") or "").lower() != "sell":
                continue
            evt_ticker = str(evt.get("ticker", "") or "").strip()
            evt_market = str(evt.get("market") or self._ticker_market(evt_ticker))
            if evt_market != market_key:
                continue
            evt_session = str(evt.get("session_date") or evt.get("date") or "")
            if evt_session != session_date:
                continue
            key = _realized_key(evt_ticker, int(evt.get("qty", 0) or 0), float(evt.get("pnl", evt.get("pnl_krw", 0)) or 0), str(evt.get("reason", "") or ""))
            if key in seen:
                continue
            seen.add(key)
            realized += float(evt.get("pnl", evt.get("pnl_krw", 0)) or 0)
        try:
            if DECISIONS_FILE.exists():
                for line in DECISIONS_FILE.read_text(encoding="utf-8").splitlines():
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("type") != "closed" or rec.get("market") != market_key:
                        continue
                    rec_session = str(rec.get("session_date") or str(rec.get("timestamp", "") or "")[:10] or "")
                    if rec_session != session_date:
                        continue
                    key = _realized_key(
                        str(rec.get("ticker", "") or ""),
                        int(rec.get("qty", 0) or 0),
                        float(rec.get("pnl_krw", 0) or 0),
                        str(rec.get("exit_reason", "") or ""),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    realized += float(rec.get("pnl_krw", 0) or 0)
        except Exception as exc:
            log.debug(f"[market realized pnl] {market_key} decisions scan failed: {exc}")
        return float(realized)
    def _market_internal_session_equity_krw(self, market: str) -> float:
        base = self._market_session_start_equity_krw(market)
        if base <= 0:
            return 0.0
        return float(
            base
            + self._market_realized_pnl_krw(market)
            + self._market_unrealized_session_pnl_krw(market)
        )
    def _note_recent_sell_proceeds(
        self,
        market: str,
        ticker: str,
        *,
        order_no: str = "",
        proceeds_krw: float = 0.0,
        reason: str = "",
        ttl_sec: int = 900,
    ) -> None:
        market_key = str(market or "").upper()
        proceeds = float(proceeds_krw or 0.0)
        if proceeds <= 0:
            return
        if not hasattr(self, "_recent_sell_proceeds_by_market") or not isinstance(self._recent_sell_proceeds_by_market, dict):
            self._recent_sell_proceeds_by_market = {"KR": [], "US": []}
        rows = self._recent_sell_proceeds_by_market.setdefault(market_key, [])
        now_ts = time.time()
        rows.append(
            {
                "ticker": str(ticker or ""),
                "order_no": str(order_no or ""),
                "proceeds_krw": proceeds,
                "reason": reason,
                "recorded_at": datetime.now(KST).isoformat(timespec="seconds"),
                "expires_at": now_ts + int(ttl_sec or 900),
            }
        )
        self._recent_sell_proceeds_by_market[market_key] = [r for r in rows if float(r.get("expires_at", 0) or 0) > now_ts]
    def _recent_sell_proceeds_adjustment_krw(self, market: str) -> float:
        market_key = str(market or "").upper()
        if not hasattr(self, "_recent_sell_proceeds_by_market") or not isinstance(self._recent_sell_proceeds_by_market, dict):
            self._recent_sell_proceeds_by_market = {"KR": [], "US": []}
        now_ts = time.time()
        rows = [
            r for r in self._recent_sell_proceeds_by_market.get(market_key, [])
            if float(r.get("expires_at", 0) or 0) > now_ts
        ]
        self._recent_sell_proceeds_by_market[market_key] = rows
        return float(sum(float(r.get("proceeds_krw", 0) or 0) for r in rows))
    def _market_session_start_equity_krw(self, market: str) -> float:
        baseline_meta = dict(self._daily_baseline_by_market.get(market, {}) or {})
        base = float(baseline_meta.get("base", 0) or 0)
        if base > 0:
            return base
        return float(self.risk.session_start_equity or 0)
    def _market_broker_total_equity_krw(self, market: str, force_refresh: bool = False) -> float:
        try:
            balance = get_balance(self.token, market=market, force_refresh=force_refresh)
            snapshot = self._broker_snapshot_from_balance(market, balance)
            self._set_broker_state(market, trust_level="trusted", snapshot=snapshot)
            return float(snapshot.get("total_krw", 0) or 0)
        except Exception as e:
            self._set_broker_state(market, trust_level="degraded", error=str(e))
            return 0.0
    def _market_equity_reference_context(self, market: str) -> dict:
        market_key = str(market or "").upper()
        state = dict(self._broker_state.get(market_key, {}) or {})
        current_snap = dict(state.get("last_snapshot") or {})
        trusted_snap = dict(state.get("last_trusted_snapshot") or {})
        position_value = self._market_position_value_krw(market_key)
        trust = self._broker_trust_level(market_key)
        base = self._market_session_start_equity_krw(market_key)
        internal_total = self._market_internal_session_equity_krw(market_key)
        broker_total = 0.0
        broker_source = ""
        cash_krw = 0.0
        if trust == "trusted" and current_snap.get("total_krw") is not None:
            broker_total = float(current_snap.get("total_krw", 0) or 0)
            cash_krw = float(current_snap.get("cash_krw", 0) or 0)
            broker_source = "broker_current"
        elif current_snap.get("cash_krw") is not None:
            cash_krw = float(current_snap.get("cash_krw", 0) or 0)
            broker_total = cash_krw + position_value
            broker_source = "broker_cash_plus_positions"
        elif trusted_snap.get("cash_krw") is not None:
            cash_krw = float(trusted_snap.get("cash_krw", 0) or 0)
            broker_total = cash_krw + position_value
            broker_source = "broker_last_trusted_cash_plus_positions"
        elif trusted_snap.get("total_krw") is not None:
            broker_total = float(trusted_snap.get("total_krw", 0) or 0)
            cash_krw = float(trusted_snap.get("cash_krw", 0) or 0)
            broker_source = "broker_last_trusted_total"
        sell_adjustment = 0.0
        if market_key == "US":
            candidate_adjustment = self._recent_sell_proceeds_adjustment_krw(market_key)
            if candidate_adjustment > 0 and broker_total > 0:
                adjusted_total = broker_total + candidate_adjustment
                if internal_total <= 0 or abs(adjusted_total - internal_total) < abs(broker_total - internal_total):
                    sell_adjustment = candidate_adjustment
        adjusted_broker_total = broker_total + sell_adjustment if broker_total > 0 else 0.0
        selected_total = 0.0
        selected_source = ""
        fallback_reason = ""
        if market_key == "KR" and internal_total > 0:
            selected_total = internal_total
            selected_source = "internal_session_equity"
        elif market_key == "US" and adjusted_broker_total > 0:
            selected_total = adjusted_broker_total
            selected_source = "broker_current_sell_lag_adjusted" if sell_adjustment else (broker_source or "broker_total")
        elif internal_total > 0:
            selected_total = internal_total
            selected_source = "internal_session_equity"
        elif broker_total > 0:
            selected_total = broker_total
            selected_source = broker_source or "broker_total"
            fallback_reason = "internal_unavailable"
        elif trusted_snap.get("total_krw") is not None and float(trusted_snap.get("total_krw", 0) or 0) > 0:
            selected_total = float(trusted_snap.get("total_krw", 0) or 0)
            selected_source = "broker_last_trusted_total"
            fallback_reason = "broker_current_unavailable"
        elif position_value > 0:
            selected_total = position_value
            selected_source = "positions_only"
            fallback_reason = "equity_reference_unavailable"
        else:
            selected_total = 0.0
            selected_source = "uninitialized"
            fallback_reason = "no_positive_equity_reference"
        threshold = max(5_000.0, float(base or 0) * 0.02)
        lag_suspected = bool(
            broker_total > 0
            and internal_total > 0
            and abs((adjusted_broker_total or broker_total) - internal_total) > threshold
        )
        return {
            "market": market_key,
            "total_krw": float(selected_total),
            "cash_krw": cash_krw,
            "position_krw": position_value,
            "source": selected_source,
            "broker_total_krw": float(broker_total),
            "internal_krw": float(internal_total),
            "adjustment_krw": float(sell_adjustment),
            "lag_suspected": lag_suspected,
            "fallback_reason": fallback_reason,
        }
    def _market_daily_return_pct(self, market: str) -> float:
        base = self._market_session_start_equity_krw(market)
        if base <= 0:
            return 0.0
        equity_context = self._market_equity_reference_context(market)
        total = float(equity_context.get("total_krw", 0) or 0)
        if total <= 0:
            return 0.0
        return (total - base) / base * 100.0
    def _market_realized_daily_return_pct(self, market: str) -> float:
        base = self._market_session_start_equity_krw(market)
        if base <= 0:
            return 0.0
        return self._market_realized_pnl_krw(market) / base * 100.0
    def _check_market_halt(self, market: str, allow_auto_release: bool = False, auto_release_note: str = "") -> bool:
        if self.risk.halt_reason == "broker_sync":
            return True
        ret = self._market_daily_return_pct(market)
        realized_ret = self._market_realized_daily_return_pct(market)
        threshold = HARD_RULES["max_daily_loss_pct"]
        equity_breach = ret < threshold
        pnl_breach = realized_ret < threshold
        if equity_breach and pnl_breach:
            if not self.risk.halted:
                log.warning(
                    f"daily loss limit reached [{market}] (equity={ret:.2f}% realized={realized_ret:.2f}%) -> halt"
                )
            self.risk.halted = True
            if not self.risk.halt_reason:
                self.risk.halt_reason = "daily_loss"
        elif equity_breach and not pnl_breach:
            log.warning(
                f"[HALT 蹂대쪟 {market}] equity breach only "
                f"(equity={ret:.2f}% realized={realized_ret:.2f}% threshold={threshold:.2f}%)"
            )
        elif self.risk.halted and allow_auto_release and ret > threshold * 0.5:
            note = f" note={auto_release_note}" if auto_release_note else ""
            log.warning(
                f"[HALT ?먮룞 ?댁젣 {market}] daily_return={ret:.2f}% recovered "
                f"(threshold={threshold:.2f}%){note}"
            )
            self.risk.halted = False
            self.risk.halt_reason = ""
        return self.risk.halted
    def _sync_runtime_with_broker(self):
        """?μ쨷?먮룄 ?대? ?ъ????꾧툑??釉뚮줈而??붽퀬 湲곗??쇰줈 ?뺣젹."""
        _pre_sync_cash = float(self.risk.cash)
        _pre_sync_us_cost_by_key = {}
        for _pos in self.risk.positions:
            _ticker = str(_pos.get("ticker", "") or "").strip()
            if self._ticker_market(_ticker) != "US":
                continue
            _pre_sync_us_cost_by_key[_ticker.upper()] = (
                float(_pos.get("entry", 0) or 0) * int(_pos.get("qty", 0) or 0)
            )
        bal_us = {}
        try:
            bal_kr = get_balance(self.token, market="KR")
            self._set_broker_state(
                "KR",
                trust_level="trusted",
                snapshot=self._broker_snapshot_from_balance("KR", bal_kr),
            )
        except KISTokenExpiredError as e:
            log.warning(f"[釉뚮줈而??고????숆린?? KR ?좏겙 留뚮즺 媛먯? ??媛뺤젣 媛깆떊 ???ъ떆?? {e}")
            try:
                self.token = get_access_token(force_refresh=True)
                bal_kr = get_balance(self.token, market="KR")
                self._set_broker_state(
                    "KR",
                    trust_level="trusted",
                    snapshot=self._broker_snapshot_from_balance("KR", bal_kr),
                )
                log.info("[釉뚮줈而??고????숆린?? KR ?좏겙 媛깆떊 ?깃났")
            except Exception as retry_e:
                _kr_fails = self._broker_state.get("KR", {}).get("consecutive_failures", 0) + 1
                _kr_level = "untrusted" if _kr_fails >= 3 else "degraded"
                self._set_broker_state("KR", trust_level=_kr_level, error=str(retry_e))
                log.error(f"[釉뚮줈而??고????숆린?? KR ?좏겙 媛깆떊 ???ъ떆???ㅽ뙣({_kr_fails}??: {retry_e}")
                return
        except Exception as e:
            _kr_fails = self._broker_state.get("KR", {}).get("consecutive_failures", 0) + 1
            _kr_level = "untrusted" if _kr_fails >= 3 else "degraded"
            self._set_broker_state("KR", trust_level=_kr_level, error=str(e))
            log.warning(f"[釉뚮줈而??고????숆린?? KR ?붽퀬 議고쉶 ?ㅽ뙣({_kr_fails}??: {e}")
            return
        broker_us: dict = {}
        us_ok = False
        try:
            bal_us = get_balance(self.token, market="US")
            broker_us = self._normalize_broker_balance(bal_us, "US")
            us_ok = True
            self._set_broker_state(
                "US",
                trust_level="trusted",
                snapshot=self._broker_snapshot_from_balance("US", bal_us),
            )
        except KISTokenExpiredError as e:
            log.warning(f"[釉뚮줈而??고????숆린?? US ?좏겙 留뚮즺 媛먯? ??媛뺤젣 媛깆떊 ???ъ떆?? {e}")
            try:
                self.token = get_access_token(force_refresh=True)
                bal_us = get_balance(self.token, market="US")
                broker_us = self._normalize_broker_balance(bal_us, "US")
                us_ok = True
                self._set_broker_state(
                    "US",
                    trust_level="trusted",
                    snapshot=self._broker_snapshot_from_balance("US", bal_us),
                )
                log.info("[釉뚮줈而??고????숆린?? US ?좏겙 媛깆떊 ?깃났")
            except Exception as retry_e:
                self._set_broker_state("US", trust_level="untrusted", error=str(retry_e))
                log.warning(f"[釉뚮줈而??고????숆린?? US ?좏겙 媛깆떊 ???ъ떆???ㅽ뙣: {retry_e}")
        except Exception as e:
            self._set_broker_state("US", trust_level="untrusted", error=str(e))
            log.warning(f"[釉뚮줈而??고????숆린?? US ?붽퀬 議고쉶 ?ㅽ뙣 ??US ?ъ???寃利??ㅽ궢: {e}")
        broker_kr = self._normalize_broker_balance(bal_kr, "KR")
        self._reconcile_pending_orders(broker_kr, broker_us)
        pending_by_key = self._pending_orders_by_key()
        _positions_before = json.dumps(self.risk.positions, ensure_ascii=False, sort_keys=True, default=str)
        synced_positions = {}
        removed = []
        seen_keys = set()
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
                        saved_templates[self._broker_reconcile_key(_mkt, _tk)] = _saved_pos
        except Exception as _e:
            log.warning(f"[釉뚮줈而??고????숆린?? ????ъ????쒗뵆由?濡쒕뱶 ?ㅽ뙣: {_e}")
        for pos in self.risk.positions:
            ticker = str(pos.get("ticker", "") or "").strip()
            if not ticker:
                continue
            market = self._ticker_market(ticker)
            key = self._broker_reconcile_key(market, ticker)
            broker = broker_us if market == "US" else broker_kr
            # US ?붽퀬 議고쉶 ?ㅽ뙣 ??US ?ъ????쒓굅 湲덉?
            if market == "US" and not us_ok:
                synced_positions[key] = pos
                seen_keys.add(key)
                continue
            broker_pos = broker.get(key[1])
            pending_orders = pending_by_key.get(key) or []
            if not broker_pos or broker_pos.get("qty", 0) <= 0:
                if pending_orders:
                    pos["broker_reconcile_status"] = "pending_only"
                    synced_positions[key] = self._normalize_position_metadata(
                        pos,
                        market,
                        origin=str(pos.get("position_origin") or "saved_restore"),
                        integrity="protected",
                        management_protected=True,
                    )
                    seen_keys.add(key)
                    log.warning(f"[釉뚮줈而??고????숆린?? {market} {ticker} 蹂댁쑀 ?놁쓬 + 誘몄껜寃?議댁옱 ??蹂댄샇 ?좎?")
                    continue
                self._flag_execution_issue(market, "broker_position_removed")
                removed.append(ticker)
                log.warning(f"[釉뚮줈而??고????숆린?? stale ?ъ????쒓굅: {market} {ticker}")
                continue
            if broker_pos.get("qty", 0) != pos.get("qty", 0):
                log.warning(
                    f"[釉뚮줈而??고????숆린?? {ticker} ?섎웾 蹂댁젙 "
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
            existing = synced_positions.get(key)
            if existing is not None:
                log.warning(f"[釉뚮줈而??고????숆린?? 以묐났 ?ъ???蹂묓빀: {ticker}")
            synced_positions[key] = pos
            seen_keys.add(key)
        for ticker, broker_pos in broker_kr.items():
            key = ("KR", ticker)
            if key in seen_keys or int(broker_pos.get("qty", 0) or 0) <= 0:
                continue
            if ticker in self._session_closed_tickers.get("KR", set()):
                log.info(f"[釉뚮줈而??고????숆린?? KR ?ъ＜??李⑤떒: {ticker} (?대쾲 ?몄뀡 留ㅻ룄 ?꾨즺)")
                continue
            pending_templates = pending_by_key.get(key) or []
            template = pending_templates[0] if pending_templates else saved_templates.get(key)
            synced_positions[key] = self._make_runtime_position_from_broker(
                ticker, "KR", broker_pos,
                template=template,
            )
            self._flag_execution_issue("KR", "broker_position_injected")
            seen_keys.add(key)
            log.warning(f"[broker truth sync] KR injected broker position: {ticker} qty={broker_pos.get('qty', 0)}")
        for ticker, broker_pos in broker_us.items():
            key = ("US", ticker.upper())
            if key in seen_keys or int(broker_pos.get("qty", 0) or 0) <= 0:
                continue
            if ticker.upper() in self._session_closed_tickers.get("US", set()):
                log.info(f"[釉뚮줈而??고????숆린?? US ?ъ＜??李⑤떒: {ticker} (?대쾲 ?몄뀡 留ㅻ룄 ?꾨즺)")
                continue
            pending_templates = pending_by_key.get(key) or []
            template = pending_templates[0] if pending_templates else saved_templates.get(key)
            synced_positions[key] = self._make_runtime_position_from_broker(
                ticker.upper(), "US", broker_pos,
                template=template,
            )
            self._flag_execution_issue("US", "broker_position_injected")
            seen_keys.add(key)
            log.warning(f"[broker sync] US injected broker-held position: {ticker} qty={broker_pos.get('qty', 0)}")
        if removed:
            log.warning(f"[釉뚮줈而??고????숆린?? 釉뚮줈而?誘몃낫???ъ????쒓굅: {removed}")
        self.risk.positions = list(synced_positions.values())
        if json.dumps(self.risk.positions, ensure_ascii=False, sort_keys=True, default=str) != _positions_before:
            self._save_positions()
        # 怨듭쑀 ? 湲곗? ?대? equity??KR ?꾧툑留뚯씠 ?꾨땲??US ?꾧툑源뚯? ?ы븿?댁빞 ?쒕떎.
        # 洹몃젃吏 ?딆쑝硫?US ?ъ???泥?궛 ???ъ떆???щ룞湲고솕 ??        # "?ъ??섏? ?쒓굅?먮뒗???꾧툑? equity??誘몃컲?? ?곹깭媛 ?섏뼱
        # ?쇱씪 ?먯떎 ?쒕룄(halt)媛 媛吏쒕줈 諛쒕룞?????덈떎.
        kr_cash = float(bal_kr.get("cash", 0) or 0)
        us_cash_krw = 0.0
        if us_ok:
            try:
                us_cash_krw = float(bal_us.get("cash", 0) or 0) * float(self.usd_krw_rate or 0)
            except Exception:
                us_cash_krw = 0.0
        # KIS paper US 怨꾩쥖??USD ?꾧툑??0?쇰줈 諛섑솚?쒕떎.
        # broker_sync濡?二쇱엯??US paper ?ъ??섏? risk.positions???ㅼ뼱媛吏留?        # ????꾧툑 李④컧???놁뼱 equity = KR_cash + US_pos_value 濡?怨쇰?怨꾩긽?쒕떎.
        # ?대? 諛⑹??섍린 ?꾪빐 paper 紐⑤뱶?먯꽌 US paper ?ъ???cost瑜?KR ?꾧툑?먯꽌 李④컧???뚭퀎瑜?留욎텣??
        # (?ㅺ굅??US??寃쎌슦 us_cash_krw媛 ?ㅼ젣 ?щ윭 ?붽퀬瑜?諛섏쁺?섎?濡?湲곗〈 濡쒖쭅 ?좎?)
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
            or order.get("raw_price", 0)   # 泥닿껐媛/?붽퀬 紐⑤몢 0????二쇰Ц ?묒닔媛 ?대갚
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
        pos = {
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
            "source_strategy": order.get("source_strategy", ""),
            "micro_probe": bool(order.get("micro_probe")),
            "micro_probe_reason": order.get("micro_probe_reason", ""),
            "original_order_cost_krw": float(order.get("original_order_cost_krw", 0) or 0),
            "adjusted_order_cost_krw": float(order.get("adjusted_order_cost_krw", 0) or 0),
            "oversize_ratio": float(order.get("oversize_ratio", 0) or 0),
            "tp": avg_price * (1 + tp_pct),
            "sl": avg_price * (1 - sl_pct),
            "tp_pct": tp_pct,        # 鍮꾩쑉 蹂댁〈 ??US ?섏쑉 ?쒕━?꾪듃 諛⑹?
            "sl_pct": sl_pct,
            "max_hold": int(order.get("max_hold", 1)),
            "held_days": 0,
            "entry_date": self._current_session_date_str(market),
            "session_date": order.get("session_date", self._current_session_date_str(market)),
            "entry_session_date": order.get("session_date", self._current_session_date_str(market)),
            "trailing": False,
            "trail_sl": 0.0,
            "trail_sl_usd": 0.0,     # US ?몃젅?쇰쭅 SL (USD 湲곗?)
            "trail_pct": 0.03,
            "tp_triggered": False,
            "hold_advice": None,
            "tp_price": 0.0,
            "decision_id": order.get("decision_id"),   # ML DB ?곕룞
            "v2_decision_id": order.get("v2_decision_id", ""),
            "v2_execution_id": order.get("v2_execution_id", order.get("order_no", "")),
            "position_id": order.get("position_id") or f"pos_{market}_{order.get('ticker', '')}_{order.get('order_no', '') or int(time.time())}",
            "path_type": order.get("path_type", ""),
            "pathb_path_run_id": order.get("pathb_path_run_id", ""),
            "pathb_plan": order.get("pathb_plan", {}),
        }
        for key in (
            "pathb_reference_target",
            "pathb_reference_stop",
            "pathb_reference_confidence",
            "pathb_reference_status",
            "pathb_reference_path_run_id",
            "pathb_reference_source",
            "pathb_reference_copied_at",
            "selection_reference_target",
            "selection_reference_stop",
            "selection_reference_confidence",
            "selection_reference_source",
            "selection_reference_copied_at",
        ):
            if key in order:
                pos[key] = order.get(key)
        return self._normalize_position_metadata(
            pos,
            market,
            origin="order_fill",
            integrity="trusted",
            management_protected=False,
        )
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
        positions_changed = False
        pending_orders_changed = False
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
                            pending_orders_changed = True
                        order["fill_time"] = fill.get("order_time", "")
                        pending_orders_changed = True
                except Exception as e:
                    label = "援?궡" if market == "KR" else "?댁쇅"
                    log.warning(f"[{label} 泥닿껐議고쉶 ?ㅽ뙣] {ticker} 二쇰Ц踰덊샇={order.get('order_no','')}: {e}")
            filled_by_broker = bool(broker_pos and broker_qty >= current_qty + order_qty)
            filled_by_query = bool(fill and int(fill.get("filled_qty", 0) or 0) >= max(order_qty, 1))
            broker_delta_qty = max(0, broker_qty - current_qty) if broker_pos else 0
            query_filled_qty = int(fill.get("filled_qty", 0) or 0) if fill else 0
            partial_qty = 0
            if 0 < query_filled_qty < order_qty:
                partial_qty = query_filled_qty
            elif 0 < broker_delta_qty < order_qty:
                partial_qty = broker_delta_qty
            if partial_qty > 0:
                partial_order = dict(order)
                partial_order["qty"] = partial_qty
                if not broker_pos:
                    broker_pos = {
                        "ticker": ticker,
                        "qty": partial_qty,
                        "avg_price": float(order.get("filled_price_native", 0) or order.get("raw_price", 0) or 0),
                        "eval_price": float(self.price_cache_raw.get(ticker, 0) or self.price_cache.get(ticker, 0) or order.get("filled_price_native", 0) or order.get("raw_price", 0) or 0),
                        "eval_profit": 0.0,
                        "profit_rate": 0.0,
                    }
                _v2_pos = self._make_position_from_broker(partial_order, broker_pos)
                self.risk.positions.append(_v2_pos)
                positions_changed = True
                if not order.get("pathb_path_run_id"):
                    self._entry_timing_filled(
                        market,
                        ticker,
                        fill_price=float(order.get("filled_price_native", 0) or broker_pos.get("avg_price", 0) or 0),
                        order_no=str(order.get("order_no", "") or ""),
                        qty=int(partial_qty),
                        partial=True,
                    )
                accounted[key] = current_qty + partial_qty
                order["qty"] = max(0, order_qty - partial_qty)
                order["filled_qty_accum"] = int(order.get("filled_qty_accum", 0) or 0) + partial_qty
                order.setdefault("partial_fill_at", datetime.now(KST).isoformat())
                if self.v2_partial_fill_policy is not None:
                    order["partial_fill_ttl_sec"] = self.v2_partial_fill_policy.ttl_sec(market)
                pending_orders_changed = True
                self._v2_record_lifecycle_event(
                    "PARTIAL_FILLED",
                    market,
                    ticker,
                    decision_id=str(order.get("v2_decision_id", "") or ""),
                    execution_id=str(order.get("v2_execution_id", "") or order.get("order_no", "") or ""),
                    position_id=str(_v2_pos.get("position_id", "") or ""),
                    payload={
                        "order_no": order.get("order_no", ""),
                        "qty": int(partial_qty),
                        "original_qty": int(order_qty),
                        "remaining_qty": int(order.get("qty", 0) or 0),
                        "fill_price_native": float(order.get("filled_price_native", 0) or broker_pos.get("avg_price", 0) or 0),
                    },
                )
                if getattr(self, "pathb", None) is not None and order.get("pathb_path_run_id"):
                    try:
                        self.pathb.on_buy_fill(partial_order, position=_v2_pos, partial=True)
                    except Exception as _pathb_fill_e:
                        log.error(f"[PathB partial fill] {market} {ticker}: {_pathb_fill_e}", exc_info=True)
                remaining.append(order)
                continue
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
                _v2_pos = self._make_position_from_broker(order, broker_pos)
                self.risk.positions.append(_v2_pos)
                positions_changed = True
                if not order.get("pathb_path_run_id"):
                    self._entry_timing_filled(
                        market,
                        ticker,
                        fill_price=float(order.get("filled_price_native", 0) or broker_pos.get("avg_price", 0) or 0),
                        order_no=str(order.get("order_no", "") or ""),
                        qty=int(order_qty),
                        partial=False,
                    )
                self._v2_record_lifecycle_event(
                    "FILLED",
                    market,
                    ticker,
                    decision_id=str(order.get("v2_decision_id", "") or ""),
                    execution_id=str(order.get("v2_execution_id", "") or order.get("order_no", "") or ""),
                    position_id=str(_v2_pos.get("position_id", "") or ""),
                    payload={
                        "order_no": order.get("order_no", ""),
                        "qty": int(order_qty),
                        "fill_price_native": float(order.get("filled_price_native", 0) or broker_pos.get("avg_price", 0) or 0),
                    },
                )
                if getattr(self, "pathb", None) is not None and order.get("pathb_path_run_id"):
                    try:
                        self.pathb.on_buy_fill(order, position=_v2_pos, partial=False)
                    except Exception as _pathb_fill_e:
                        log.error(f"[PathB fill] {market} {ticker}: {_pathb_fill_e}", exc_info=True)
                accounted[key] = current_qty + order_qty
                # ML DB: 泥닿껐 ?뺤씤 湲곕줉
                if _ML_DB_ENABLED and order.get("decision_id"):
                    try:
                        _ml_update_filled(order["decision_id"], "FILLED")
                    except Exception:
                        pass
                self._funnel[market]["filled"] += 1
                filled.append(order)
            else:
                if order.get("partial_fill_at") and not order.get("v2_unknown_recorded"):
                    ttl_sec = int(order.get("partial_fill_ttl_sec", 0) or 0)
                    if ttl_sec <= 0 and self.v2_partial_fill_policy is not None:
                        ttl_sec = self.v2_partial_fill_policy.ttl_sec(market)
                    try:
                        first_partial = datetime.fromisoformat(str(order.get("partial_fill_at")).replace("Z", "+00:00"))
                        if first_partial.tzinfo is None:
                            first_partial = first_partial.replace(tzinfo=KST)
                        partial_due = ttl_sec > 0 and (datetime.now(KST) - first_partial.astimezone(KST)).total_seconds() >= ttl_sec
                    except Exception:
                        partial_due = True
                    if partial_due:
                        order["v2_unknown_recorded"] = True
                        pending_orders_changed = True
                        self._v2_record_order_unknown(
                            market,
                            ticker,
                            order,
                            "partial fill remainder unresolved after TTL; cancel API unavailable",
                        )
                        log.warning(f"[V2 PARTIAL TTL] {market} {ticker} remainder unresolved -> ORDER_UNKNOWN")
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
                    or order.get("raw_price", 0)   # 泥닿껐媛/?붽퀬 紐⑤몢 0????二쇰Ц ?묒닔媛 ?대갚
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
                    "session_date": self._current_session_date_str(market),
                    "order_no": order.get("order_no", ""),
                    "price_source": "order_fill" if order.get("filled_price_native") else "broker_balance",
                    "currency": "USD" if market == "US" else "KRW",
                    "display_price": fill_px_native,
                    "risk_price": fill_px_risk,
                    "fill_time": order.get("fill_time", ""),
                    "source_strategy": order.get("source_strategy", ""),
                    "path_type": order.get("path_type", ""),
                    "pathb_path_run_id": order.get("pathb_path_run_id", ""),
                    "micro_probe": bool(order.get("micro_probe")),
                    "micro_probe_reason": order.get("micro_probe_reason", ""),
                    "original_order_cost_krw": float(order.get("original_order_cost_krw", 0) or 0),
                    "adjusted_order_cost_krw": float(order.get("adjusted_order_cost_krw", 0) or 0),
                    "oversize_ratio": float(order.get("oversize_ratio", 0) or 0),
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
                    source="釉뚮줈而??됯퇏?④?",
                    name=str(order.get("name", "") or "").strip(),
                    usd_krw=self.usd_krw_rate,
                    buy_path="path_b" if (order.get("pathb_path_run_id") or order.get("path_type") == "claude_price") else "path_a",
                )
        if positions_changed or filled:
            self._save_positions()
        if pending_orders_changed or filled:
            self.pending_orders = remaining
            self._save_pending_orders()
    def _add_pending_order(self, order: dict):
        market = order.get("market", "KR")
        ticker = order.get("ticker", "")
        market = str(market or "KR").strip().upper()
        ticker_key = ticker.upper() if market == "US" else ticker
        order["market"] = market
        order_no = str(order.get("order_no", "") or "").strip()
        if order_no:
            next_orders = []
            replaced = False
            same_ticker_existing_order_nos = []
            for existing in self.pending_orders:
                existing_market = str(existing.get("market", "KR") or "KR").strip().upper()
                existing_ticker = existing.get("ticker", "")
                existing_key = existing_ticker.upper() if existing_market == "US" else existing_ticker
                existing_order_no = str(existing.get("order_no", "") or "").strip()
                if existing_market == market and existing_order_no and existing_order_no == order_no:
                    merged = dict(existing)
                    merged.update(order)
                    next_orders.append(merged)
                    replaced = True
                    continue
                if existing_market == market and existing_key == ticker_key and existing_order_no:
                    same_ticker_existing_order_nos.append(existing_order_no)
                next_orders.append(existing)
            if not replaced:
                next_orders.append(order)
            self.pending_orders = next_orders
            if same_ticker_existing_order_nos:
                self._flag_duplicate_pending_exposure(market, ticker)
                try:
                    self._reconcile_broker_open_orders(market, reason="same_ticker_pending_added", force=True)
                except Exception as exc:
                    log.debug(f"[pending duplicate exposure] broker truth reconcile failed: {exc}")
        else:
            # Before the broker returns an order number, keep only one temporary same-ticker pending row.
            # Never remove rows that already have an order_no; those may be live at the broker.
            self.pending_orders = [
                o for o in self.pending_orders
                if not (
                    str(o.get("market", "KR") or "KR").strip().upper() == market and
                    not str(o.get("order_no", "") or "").strip() and
                    ((o.get("ticker", "").upper() if market == "US" else o.get("ticker", "")) == ticker_key)
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
        if str(reason or "") == "session_close":
            for order in removed:
                self._v2_record_order_unknown(
                    str(order.get("market", market) or market),
                    str(order.get("ticker", "") or ""),
                    order,
                    "pending order remained at session_close",
                )
        self.pending_orders = remaining
        self._save_pending_orders()
        tickers = [f"{o.get('ticker', '')}({o.get('qty', 0)}二?" for o in removed]
        log.warning(f"[誘몄껜寃?二쇰Ц ?뺣━] {market} {reason}: {tickers}")
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
    _SELL_FAIL_COOLDOWN_SEC = 90  # 留ㅻ룄 ?ㅽ뙣 ???ъ떆???듭젣 ?쒓컙
    def _native_position_price(self, pos: dict, market: str, *, current: bool) -> float:
        if str(market or "").upper() == "US":
            key = "display_current_price" if current else "display_avg_price"
            return self._float_or_zero(pos.get(key))
        return self._float_or_zero(pos.get("current_price" if current else "entry"))
    def _soft_exit_reference_for_position(self, pos: dict) -> dict:
        target = self._float_or_zero(pos.get("pathb_reference_target"))
        if target > 0:
            return {
                "target": target,
                "stop": self._float_or_zero(pos.get("pathb_reference_stop")),
                "confidence": self._float_or_zero(pos.get("pathb_reference_confidence")),
                "source": "pathb_reference",
            }
        target = self._float_or_zero(pos.get("selection_reference_target"))
        if target > 0:
            return {
                "target": target,
                "stop": self._float_or_zero(pos.get("selection_reference_stop")),
                "confidence": self._float_or_zero(pos.get("selection_reference_confidence")),
                "source": "selection_reference",
            }
        return {}
    def _find_live_position_for_candidate(self, cand: dict, market: str) -> Optional[dict]:
        ticker = str(cand.get("ticker", "") or "")
        key = ticker.upper() if market == "US" else ticker
        positions = getattr(getattr(self, "risk", None), "positions", []) or []
        for pos in positions:
            pos_ticker = str(pos.get("ticker", "") or "")
            pos_key = pos_ticker.upper() if market == "US" else pos_ticker
            if pos_key == key:
                return pos
        return None
    def _call_quick_exit_check(self, payload: dict) -> dict:
        from minority_report.quick_exit_check import quick_exit_check
        return quick_exit_check(**payload)
    def _apply_soft_exit_hold(self, pos: dict, cand: dict, market: str, result: dict, reference: dict) -> None:
        now = datetime.now(KST)
        cooldown_until = now + timedelta(seconds=max(0, int(getattr(self, "soft_exit_cooldown_sec", 600) or 600)))
        current_native = self._native_position_price({**pos, **cand}, market, current=True)
        entry_native = self._native_position_price(pos, market, current=False)
        suggested_floor = self._float_or_zero(result.get("protective_stop"))
        existing_floor = max(
            self._float_or_zero(pos.get("soft_exit_floor_price")),
            self._float_or_zero(cand.get("effective_stop_price")),
            self._float_or_zero(cand.get("strategy_stop_price")),
            self._float_or_zero(cand.get("profit_floor_price")),
            self._float_or_zero(cand.get("loss_cap_price")),
        )
        protective_floor = max(existing_floor, suggested_floor, entry_native)
        pos["soft_exit_floor_price"] = round(float(protective_floor or 0), 4)
        pos["soft_exit_floor_source"] = "quick_exit_check"
        pos["soft_exit_deferred_at"] = now.isoformat(timespec="seconds")
        pos["soft_exit_deferred_reason"] = str(cand.get("reason", "") or "")
        pos["soft_exit_reference_source"] = str(reference.get("source", "") or "")
        pos["soft_exit_reference_target"] = float(reference.get("target", 0) or 0)
        pos["soft_exit_review_action"] = "HOLD"
        pos["soft_exit_review_reason"] = str(result.get("reason", "") or "")[:500]
        pos["soft_exit_review_confidence"] = self._float_or_zero(result.get("confidence"))
        pos["soft_exit_review_count"] = int(pos.get("soft_exit_review_count", 0) or 0) + 1
        pos["soft_exit_review_cooldown_until"] = cooldown_until.isoformat(timespec="seconds")
        try:
            self._save_positions()
        except Exception:
            pass
    def _soft_exit_in_cooldown(self, pos: dict) -> bool:
        raw = str(pos.get("soft_exit_review_cooldown_until", "") or "").strip()
        if not raw:
            return False
        try:
            until = datetime.fromisoformat(raw)
            if until.tzinfo is None:
                until = until.replace(tzinfo=KST)
            return datetime.now(KST) < until.astimezone(KST)
        except Exception:
            return False
    def _try_soft_exit_arbitration(self, cand: dict, market: str) -> bool:
        reason = str(cand.get("reason", "") or "")
        if not bool(getattr(self, "soft_exit_arbitration_enabled", True)):
            return False
        if reason not in {"profit_floor", "trail_stop", "max_hold"}:
            return False
        if cand.get("pathb_path_run_id"):
            return False
        pos = self._find_live_position_for_candidate(cand, market)
        if not pos:
            return False
        max_reviews = int(getattr(self, "soft_exit_max_reviews", 2) or 2)
        if int(pos.get("soft_exit_review_count", 0) or 0) >= max_reviews:
            return False
        if self._soft_exit_in_cooldown(pos):
            return False
        reference = self._soft_exit_reference_for_position(pos)
        target = self._float_or_zero(reference.get("target"))
        if target <= 0:
            return False
        merged = {**pos, **cand}
        current_native = self._native_position_price(merged, market, current=True)
        entry_native = self._native_position_price(pos, market, current=False)
        if current_native <= 0 or entry_native <= 0:
            return False
        buffer_pct = float(getattr(self, "soft_exit_reference_buffer_pct", 0.005) or 0.0)
        min_mfe_pct = float(getattr(self, "soft_exit_min_mfe_pct", 2.5) or 0.0)
        mfe_pct = self._float_or_zero(cand.get("position_mfe_pct") or cand.get("peak_pnl_pct"))
        if target <= current_native * (1.0 + buffer_pct):
            return False
        if mfe_pct < min_mfe_pct:
            return False
        if current_native <= entry_native:
            return False
        pnl_pct = (current_native / entry_native - 1.0) * 100.0
        market_mode = str(((self.today_judgment or {}).get("consensus") or {}).get("mode", "") or "")
        payload = {
            "ticker": str(cand.get("ticker", "") or ""),
            "market": market,
            "current_price": float(current_native),
            "entry_price": float(entry_native),
            "reference_target": float(target),
            "reference_stop": self._float_or_zero(reference.get("stop")),
            "mfe_pct": float(mfe_pct),
            "pnl_pct": float(pnl_pct),
            "exit_reason": reason,
            "strategy": str(cand.get("strategy", "") or ""),
            "market_mode": market_mode,
        }
        result = self._call_quick_exit_check(payload)
        action = str(result.get("action", "SELL") or "SELL").upper()
        cand["_soft_exit_arbitration_checked"] = True
        cand["soft_exit_review_action"] = action
        cand["soft_exit_review_reason"] = str(result.get("reason", "") or "")[:500]
        cand["soft_exit_review_confidence"] = self._float_or_zero(result.get("confidence"))
        cand["soft_exit_reference_source"] = str(reference.get("source", "") or "")
        cand["soft_exit_reference_target"] = float(target)
        cand["soft_exit_review_fallback"] = bool(result.get("fallback", False))
        if action != "HOLD":
            return False
        self._apply_soft_exit_hold(pos, cand, market, result, reference)
        try:
            self._record_decision_event(
                market,
                "soft_exit_hold",
                str(cand.get("ticker", "") or ""),
                strategy=str(cand.get("strategy", "") or ""),
                qty=int(cand.get("qty", 0) or 0),
                price_native=float(current_native),
                price_krw=float(cand.get("exit_price", 0) or 0),
                reason=reason,
                detail=str(result.get("reason", "") or "")[:500],
                soft_exit_review_action="HOLD",
                soft_exit_reference_source=str(reference.get("source", "") or ""),
                soft_exit_reference_target=float(target),
            )
        except Exception:
            pass
        log.info(
            f"[soft exit HOLD] {market} {cand.get('ticker')} reason={reason} "
            f"target={target:g} current={current_native:g} floor={pos.get('soft_exit_floor_price')}"
        )
        return True
    def _process_exit_candidates(self):
        if not self._exit_process_lock.acquire(blocking=False):
            log.debug("[exit skip] exit processing already in progress")
            return
        try:
            candidates = self.risk.get_exit_candidates()
            reason_priority = {
                "loss_cap": 0,
                "soft_exit_floor_price": 1,
                "profit_floor": 2,
                "stop_loss": 3,
                "trail_stop": 4,
                "tp_check": 5,
                "max_hold": 6,
            }
            deduped: dict[tuple[str, str], dict] = {}
            for cand in candidates:
                market = self._ticker_market(cand.get("ticker", ""))
                key = (market, cand.get("ticker", "").upper() if market == "US" else cand.get("ticker", ""))
                prev = deduped.get(key)
                if prev is None:
                    deduped[key] = cand
                    continue
                if reason_priority.get(cand.get("reason", ""), 99) < reason_priority.get(prev.get("reason", ""), 99):
                    deduped[key] = cand
            for cand in deduped.values():
                if float(cand.get("exit_price") or 0) <= 0:
                    log.error(f"[exit skip] {cand['ticker']} exit_price={cand.get('exit_price')} <= 0 ??留ㅻ룄 李⑤떒 (媛寃?議고쉶 ?ㅽ뙣)")
                    continue
                market = self._ticker_market(cand["ticker"])
                if self.current_market and market != self.current_market:
                    continue
                if not self._is_order_allowed_now(market):
                    log.debug(f"[exit skip] {cand['ticker']} ???쒖옉 ????留ㅻ룄 蹂대쪟")
                    continue
                # 留ㅻ룄 ?ㅽ뙣 荑⑤떎??泥댄겕
                _fail_ts = self._sell_fail_at.get(cand["ticker"], 0)
                if time.time() - _fail_ts < self._SELL_FAIL_COOLDOWN_SEC:
                    remaining = int(self._SELL_FAIL_COOLDOWN_SEC - (time.time() - _fail_ts))
                    log.debug(f"[exit skip] {cand['ticker']} 留ㅻ룄 ?ㅽ뙣 荑⑤떎??{remaining}珥??⑥쓬")
                    continue
                # KR trading halt check.
                if market == "KR" and is_trading_halted(cand["ticker"], self.token):
                    self._sell_fail_at[cand["ticker"]] = time.time() + 600
                    log.warning(f"[trading halted] {cand['ticker']} sell blocked; retry after 10m")
                    continue
                # ?? TP ?꾨떖 ???몃젅?쇰쭅 ?ㅽ깙 泥섎━ ??????????????????????????????
                if cand["reason"] == "tp_check":
                    if self.enable_trailing_stop:
                        self._handle_tp_trailing(cand, market)
                    else:
                        # trailing 鍮꾪솢?깊솕 ??湲곗〈泥섎읆 利됱떆 泥?궛
                        self._execute_sell(cand, market, reason="take_profit")
                        self._block_entry(cand["ticker"], _TP_COOLDOWN_MIN, "take_profit")
                    continue
                # Soft exits with a valid Claude reference target get one quick arbitration pass.
                if self._try_soft_exit_arbitration(cand, market):
                    continue
                # max_hold without successful quick arbitration falls back to its existing handling.
                if cand["reason"] == "max_hold":
                    if cand.get("_soft_exit_arbitration_checked"):
                        self._execute_sell(cand, market, reason="max_hold")
                        continue
                    self._handle_max_hold_claude(cand, market)
                    continue
                self._execute_sell(cand, market, reason=cand["reason"])
                if cand["reason"] in ("loss_cap", "stop_loss", "trail_stop"):
                    self._block_entry(cand["ticker"], _STOP_COOLDOWN_MIN, cand["reason"])
        finally:
            self._exit_process_lock.release()
    def _pre_session_position_review(self, market: str):
        """???쒖옉 ??蹂댁쑀 ?ъ???Claude 寃??
        SELL 결정 종목은 _pre_session_sell_queue에 담아두고,
        startup guard 해제 후 run_cycle에서 즉시 매도한다.
        """
        if not self._should_run_pre_session_review(market):
            self._pre_session_sell_queue[market] = []
            if self._has_broker_sync_risk(market):
                log.warning(f"[오전 리뷰] {market} 브로커 동기화 불신 상태 - 리뷰/SELL 예약 보류")
            else:
                log.info(f"[오전 리뷰] {market} 장중 재시작 감지 - 리뷰/SELL 예약 건너뜀")
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
                log.info(f"[오전 리뷰] {ticker} 전일 장마감 SELL 예약 유지")
                continue
            if float(pos.get("entry", 0) or 0) <= 0:
                log.warning(f"[오전 리뷰] {ticker} entry=0 - hold_advisor 건너뜀, HOLD 유지")
                hold_list.append((ticker, "entry price missing"))
                continue
            try:
                from minority_report.hold_advisor import ask as advisor_ask
                advice = advisor_ask(
                    self._advisor_pos(pos, market),
                    market,
                    digest,
                    decision_stage="PRE_SESSION",
                )
            except Exception as e:
                log.warning(f"[오전 리뷰] {ticker} hold_advisor 오류 - HOLD 유지: {e}")
                hold_list.append((ticker, "advisor error default hold"))
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
                log.info(f"[오전 리뷰] {ticker} SELL 예약: {reason}")
            else:
                trail_pct = advice.get("trail_pct", pos.get("trail_pct", 0.03))
                for p2 in self.risk.positions:
                    if p2.get("ticker") == ticker:
                        p2["pending_next_open_sell"] = False
                        p2["pending_next_open_reason"] = ""
                # Update trail metadata only for trailing positions.
                if pos.get("trailing"):
                    for p2 in self.risk.positions:
                        if p2.get("ticker") == ticker:
                            p2["trail_pct"] = trail_pct
                            p2["hold_advice"] = advice
                hold_list.append((ticker, reason))
                log.info(f"[오전 리뷰] {ticker} {action} 유지: {reason}")
        # ?? ?붾젅洹몃옩 ?뚮┝ ???????????????????????????????????????????????????
        lines = [f"?똿 <b>[???쒖옉 ???ъ????먭?] {market}</b>"]
        if sell_list:
            lines.append("?뵶 <b>留ㅻ룄 ?덉젙 (???쒖옉 利됱떆)</b>")
            for tk, rsn in sell_list:
                pos = next((p for p in self.risk.positions if p.get("ticker") == tk), {})
                name = str(pos.get("name", "") or "").strip() or self._lookup_ticker_name(tk, market)
                disp = f"{tk}({name})" if name else tk
                lines.append(f"  ??{disp}: {rsn or '遺꾩꽍媛 ?⑹쓽'}")
        if hold_list:
            lines.append("?윟 <b>????좎?</b>")
            for tk, rsn in hold_list:
                pos = next((p for p in self.risk.positions if p.get("ticker") == tk), {})
                name = str(pos.get("name", "") or "").strip() or self._lookup_ticker_name(tk, market)
                disp = f"{tk}({name})" if name else tk
                lines.append(f"  ??{disp}: {rsn or '怨꾩냽 蹂댁쑀'}")
        if not sell_list and not hold_list:
            lines.append("蹂댁쑀 ?ъ????놁쓬")
        block_alert("???쒖옉 ???ъ????먭?", lines[1:], market=market, icon="?똿")
    def _post_session_position_review(self, market: str, positions: list):
        """??醫낅즺 ???댁썡 ?ъ???Claude ?먭? ????蹂댁쑀 以묒씤吏 ?댁쑀 湲곕줉 + ?붾젅洹몃옩 ?꾩넚."""
        if not positions:
            block_alert("post-session positions", ["no carry positions"], market=market, icon="info")
            return
        digest = self.today_judgment.get("digest_prompt", "")
        lines = [f"?뙔 <b>[??醫낅즺 ?ъ????꾪솴] {market}</b>", "?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺"]
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
            px      = lambda v: f"${v:.2f}" if is_us else f"{v:,.0f} KRW"
            pnl_icon = "UP" if pnl >= 0 else "DOWN"
            # Net estimate after fees.
            fee = entry * qty * 0.00015 + cur * qty * (0.00015 if is_us else 0.00195)
            net = (cur - entry) * qty - fee
            if is_us:
                _rate = float(self.usd_krw_rate or 0)
                _net_krw = int(round(net * _rate)) if _rate > 0 else 0
                net_str = f"${net:+.2f}" + (f" (??{_net_krw:+,}??" if _rate > 0 else "")
            else:
                net_str = f"{int(round(net)):+,} KRW"
            # Claude hold_advisor ?몄텧 (entry=0?대㈃ 嫄대꼫?)
            action, reason, trail_info = "HOLD", "", ""
            if float(pos.get("entry", 0) or 0) <= 0:
                log.warning(f"[?ν썑 由щ럭] {ticker} entry=0 ??hold_advisor 嫄대꼫?, HOLD ?좎?")
                lines.append(f"  ?좑툘 吏꾩엯媛 誘명솗????Claude ?먮떒 蹂대쪟 (HOLD)")
                continue
            try:
                from minority_report.hold_advisor import ask as advisor_ask
                advice = advisor_ask(
                    self._advisor_pos(pos, market),
                    market,
                    digest,
                    decision_stage="PRE_CLOSE_CARRY",
                )
                action = advice.get("action", "HOLD")
                votes  = advice.get("votes", {})
                for v in votes.values():
                    if v.get("action") == action and v.get("reason"):
                        reason = v["reason"][:80]
                        break
                # hold_advice ?ъ??섏뿉 湲곕줉 (??쒕낫?쒖뿉??蹂댁엫)
                for p2 in self.risk.positions:
                    if p2.get("ticker") == ticker:
                        p2["hold_advice"] = advice
                if action == "TRAIL" and advice.get("trail_pct"):
                    trail_info = f" (?몃젅?쇰쭅 {advice['trail_pct']*100:.1f}%)"
            except Exception as e:
                log.warning(f"[?ν썑 由щ럭] {ticker} hold_advisor ?ㅻ쪟: {e}")
            action_ko = {"HOLD": "????좎?", "TRAIL": "?몃젅?쇰쭅 ?좎?", "SELL": "留ㅻ룄 沅뚭퀬"}.get(action, action)
            # 留ㅻ룄 湲곗?
            is_trailing = pos.get("trailing", False)
            trail_sl    = float(pos.get("trail_sl", 0) or 0)
            sl          = float(pos.get("sl", 0) or 0)
            tp          = float(pos.get("tp", 0) or 0)
            if is_trailing and trail_sl > 0:
                exit_cond = f"?몃젅?쇰쭅 {px(trail_sl)} ?댄븯 ??留ㅻ룄"
            elif sl > 0 or tp > 0:
                parts = []
                if sl > 0: parts.append(f"?먯젅 {px(sl)}")
                if tp > 0: parts.append(f"紐⑺몴 {px(tp)}")
                exit_cond = " 쨌 ".join(parts)
            else:
                exit_cond = "pre-session review"
            block = (
                f"{pnl_icon} <b>{disp}</b>  {pnl:+.2f}%  {net_str}\n"
                f"  留ㅼ닔 {px(entry)} ???꾩옱 {px(cur)}  {qty}二? {held}?쇱㎏\n"
                f"  ?뱥 {exit_cond}\n"
                f"  ?쨼 Claude: <b>{action_ko}{trail_info}</b>"
            )
            if reason:
                block += f"\n     ??{reason}"
            lines.append(block)
        # ?ъ????뚯씪 ?ㅼ떆 ???(hold_advice 媛깆떊 諛섏쁺)
        self._save_positions()
        self._write_live_status(market, force=True)
        block_alert("??醫낅즺 ?ъ????꾪솴", lines[1:], market=market, icon="?뙔")
    def _intraday_position_review(self, market: str, force: bool = False,
                                   ticker_filter: str = ""):
        """??以?1?쒓컙 二쇨린 蹂댁쑀 ?ъ???Claude ?먭?.
        - 吏꾩엯 ??理쒖냼 60遺?寃쎄낵???ъ??섎쭔 ???        - SELL 寃곗젙 ??利됱떆 留ㅻ룄
        - force=True: ?붾젅洹몃옩 /review 紐낅졊?대줈 ?섎룞 ?몄텧
        - ticker_filter: 吏?????대떦 醫낅ぉ留??먭?
        """
        if not self.session_active or self.current_market != market:
            if not force:
                return
        now_ts = time.time()
        # ?대떦 留덉폆 ?ъ??섎쭔, 吏꾩엯 60遺??댁긽 寃쎄낵??寃껊쭔
        MIN_HOLD_SEC = 3600
        positions = []
        for p in self.risk.positions:
            if self._ticker_market(p.get("ticker", "")) != market:
                continue
            if ticker_filter and p.get("ticker", "") != ticker_filter:
                continue
            # fill_time ?먮뒗 entry_date濡?寃쎄낵 ?먮떒
            fill_ts = p.get("_fill_ts", 0)
            if fill_ts and (now_ts - fill_ts) < MIN_HOLD_SEC and not force:
                continue
            positions.append(p)
        if not positions:
            if force:
                block_alert("??以??ъ????먭?", ["寃??????ъ????놁쓬 (吏꾩엯 ??60遺?誘멸꼍怨?"], market=market, icon="?뵇")
            return
        _intraday_ctx = self._build_intraday_context(market)
        digest = self.today_judgment.get("digest_prompt", "")
        if _intraday_ctx:
            digest = digest + "\n\n[?μ쨷 ?꾩옱]\n" + _intraday_ctx
        label  = "?섎룞 ?붿껌" if force else "1?쒓컙 ?뺢린"
        lines  = [f"?뵇 <b>[??以??ъ????먭? 쨌 {label}] {market}</b>", "?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺"]
        for pos in positions:
            ticker  = pos.get("ticker", "")
            name    = str(pos.get("name", "") or "").strip() or self._lookup_ticker_name(ticker, market)
            disp    = f"{ticker}({name})" if name else ticker
            entry   = float(pos.get("display_avg_price", pos.get("entry", 0)) or 0)
            cur     = float(pos.get("display_current_price", pos.get("current_price", entry)) or 0)
            qty     = int(pos.get("qty", 0) or 0)
            pnl     = (cur / entry - 1) * 100 if entry else 0
            is_us   = market == "US"
            px      = lambda v: f"${v:.2f}" if is_us else f"{v:,.0f} KRW"
            pnl_icon = "?윟" if pnl >= 0 else "?뵶"
            fee     = entry * qty * 0.00015 + cur * qty * (0.00015 if is_us else 0.00195)
            net     = (cur - entry) * qty - fee
            if is_us:
                _rate = float(self.usd_krw_rate or 0)
                _net_krw = int(round(net * _rate)) if _rate > 0 else 0
                net_str = f"${net:+.2f}" + (f" (??{_net_krw:+,}??" if _rate > 0 else "")
            else:
                net_str = f"{int(round(net)):+,} KRW"
            action, reason = "HOLD", ""
            try:
                from minority_report.hold_advisor import ask as advisor_ask
                advice = advisor_ask(
                    self._advisor_pos(pos, market),
                    market,
                    digest,
                    decision_stage="INTRADAY_REVIEW",
                )
                action = advice.get("action", "HOLD")
                votes  = advice.get("votes", {})
                for v in votes.values():
                    if v.get("action") == action and v.get("reason"):
                        reason = v["reason"][:80]
                        break
                # hold_advice ?낅뜲?댄듃
                for p2 in self.risk.positions:
                    if p2.get("ticker") == ticker:
                        p2["hold_advice"] = advice
            except Exception as e:
                log.warning(f"[?μ쨷 由щ럭] {ticker} ?ㅻ쪟: {e}")
            action_ko = {"HOLD": "????좎?", "TRAIL": "?몃젅?쇰쭅 ?좎?", "SELL": "利됱떆 留ㅻ룄"}.get(action, action)
            color_icon = "?뵶" if action == "SELL" else ("?윞" if action == "TRAIL" else "?윟")
            block = (
                f"{pnl_icon} <b>{disp}</b>  {pnl:+.2f}%  {net_str}\n"
                f"  留ㅼ닔 {px(entry)} ???꾩옱 {px(cur)}  {qty}二?n"
                f"  {color_icon} Claude: <b>{action_ko}</b>"
            )
            if reason:
                block += f"\n     ??{reason}"
            lines.append(block)
            # SELL 寃곗젙 ??利됱떆 留ㅻ룄
            if action == "SELL":
                if not self.session_active:
                    log.warning(f"[?μ쨷 由щ럭 SELL ?ㅽ궢] {ticker} ??session_active=False, 留ㅻ룄 遺덇?")
                    lines.append(f"  ?좑툘 ?몄뀡 鍮꾪솢????留ㅻ룄 蹂대쪟 (?ㅼ쓬 ?몄뀡 ?쒖옉 ???ш???")
                else:
                    # exit_price: KRW 湲곗? (price_cache) ?먮뒗 display_current_price
                    sell_px = self.price_cache.get(ticker, cur) or cur
                    if sell_px <= 0:
                        log.error(f"[?μ쨷 由щ럭 SELL ?ㅽ궢] {ticker} ???좏슚 媛寃??놁쓬 (cur={cur})")
                        lines.append(f"  ???좏슚 媛寃??놁쓬 ??留ㅻ룄 蹂대쪟")
                    else:
                        try:
                            cand = {**pos, "exit_price": sell_px, "reason": "intraday_review_sell"}
                            self._execute_sell(cand, market, reason="intraday_review_sell",
                                               hold_advice=advice)
                            lines.append(f"  ??留ㅻ룄 二쇰Ц ?묒닔 ({px(cur)} 횞 {qty}二?")
                            log.info(f"[?μ쨷 由щ럭 SELL] {ticker} {pnl:+.2f}% ??利됱떆 留ㅻ룄 ({sell_px:,.0f})")
                        except Exception as se:
                            log.error(f"[?μ쨷 由щ럭 留ㅻ룄 ?ㅽ뙣] {ticker}: {se}")
                            lines.append(f"  ??留ㅻ룄 ?ㅽ뙣: {se}")
        self._save_positions()
        self._write_live_status(market, force=True)
        block_alert(f"??以??ъ????먭? 쨌 {label}", lines[1:], market=market, icon="?뵇")
    def _handle_max_hold_claude(self, cand: dict, market: str):
        """max_hold ?꾨떖 ??Claude?먭쾶 SELL/HOLD 臾쇱뼱蹂???寃곗젙.
        - SELL: 利됱떆 留ㅻ룄
        - HOLD/TRAIL: max_hold +1 ?곗옣 (1?뚮쭔, ?댄썑 ?щ룄????媛뺤젣 泥?궛)
        """
        from telegram_reporter import send as _send
        ticker    = cand["ticker"]
        held_days = int(cand.get("held_days", 0))
        max_hold  = int(cand.get("max_hold", 1))
        pnl       = float(cand.get("pnl_pct", 0))
        cur       = float(cand.get("exit_price", 0))
        is_us     = market == "US"
        px_fmt    = lambda v: f"${v:.2f}" if is_us else f"{v:,.0f} KRW"
        action, reason_txt, advice = "SELL", "", None
        # If already extended, a final max-hold candidate is sold without another review.
        already_extended = cand.get("max_hold_extended", False)
        if already_extended and cand.get("max_hold_final", False):
            log.info(f"[max_hold 媛뺤젣泥?궛] {ticker} ??2???곗옣 ???щ룄????利됱떆 泥?궛")
            self._execute_sell(cand, market, reason="max_hold_final")
            _send(f"??<b>[蹂댁쑀湲고븳 留뚮즺] {ticker}</b>  {pnl:+.2f}%\n"
                  f"  max_hold {max_hold}??2???곗옣 ???щ룄????利됱떆 泥?궛")
            return
        entry_ok = float(cand.get("entry", 0) or 0) > 0
        if not entry_ok:
            log.warning(f"[max_hold Claude] {ticker} entry=0 ??hold_advisor 嫄대꼫?, HOLD ?좎?")
            action = "HOLD"
        else:
            try:
                from minority_report.hold_advisor import ask as advisor_ask
                _d = self.today_judgment.get("digest_prompt", "")
                _ic = self._build_intraday_context(market)
                digest = _d + "\n\n[?μ쨷 ?꾩옱]\n" + _ic if _ic else _d
                advice = advisor_ask(
                    self._advisor_pos(cand, market),
                    market,
                    digest,
                    decision_stage="MAX_HOLD",
                )
                action = advice.get("action", "SELL")
                votes  = advice.get("votes", {})
                for v in votes.values():
                    if v.get("action") == action and v.get("reason"):
                        reason_txt = v["reason"][:100]
                        break
            except Exception as e:
                log.warning(f"[max_hold Claude] {ticker} hold_advisor ?ㅽ뙣 ??湲곕낯 泥?궛: {e}")
                action = "SELL"
        action_ko  = "利됱떆 泥?궛" if action == "SELL" else f"蹂댁쑀湲고븳 {max_hold+1}?쇰줈 ?곗옣"
        color_icon = "?뵶" if action == "SELL" else "?윞"
        if action == "SELL":
            self._execute_sell(cand, market, reason="max_hold", hold_advice=advice)
            msg = (f"??<b>[蹂댁쑀湲고븳 留뚮즺 쨌 Claude 泥?궛] {ticker}</b>  {pnl:+.2f}%\n"
                   f"  {held_days}??蹂댁쑀, {px_fmt(cur)} 留ㅻ룄\n"
                   f"  {color_icon} Claude: {action_ko}"
                   + (f"\n     ??{reason_txt}" if reason_txt else ""))
        else:
            # max_hold +1 ?곗옣, ?곗옣 ?뚮옒洹??명똿
            for p2 in self.risk.positions:
                if p2.get("ticker") == ticker:
                    p2["max_hold"] = max_hold + 1
                    p2["max_hold_extended"] = True
                    if already_extended:
                        # 2踰덉㎏ ?곗옣 ???ㅼ쓬 ?щ룄????吏꾩쭨 媛뺤젣 泥?궛
                        p2["max_hold_final"] = True
                    if advice:
                        p2["hold_advice"] = advice
                    break
            self._save_positions()
            msg = (f"??<b>[蹂댁쑀湲고븳 留뚮즺 쨌 Claude ?곗옣] {ticker}</b>  {pnl:+.2f}%\n"
                   f"  {held_days}??蹂댁쑀 ??max_hold {max_hold+1}?쇰줈 ?곗옣 (1???쒖젙)\n"
                   f"  {color_icon} Claude: {action_ko}"
                   + (f"\n     ??{reason_txt}" if reason_txt else ""))
        _send(msg)
        log.info(f"[max_hold Claude] {ticker} held={held_days} ??{action} ({reason_txt[:50]})")
    def _handle_tp_trailing(self, cand: dict, market: str):
        """TP ?꾨떖 ???몃젅?쇰쭅 ?ㅽ깙 ?꾪솚 (遺꾩꽍媛 ?⑹쓽 ?듭뀡)"""
        ticker     = cand["ticker"]
        trail_pct  = self.trailing_stop_pct
        hold_advice = None
        if self.enable_trailing_analyst:
            # 遺꾩꽍媛 3紐낆뿉寃?HOLD/SELL 臾쇱뼱遊?(entry=0?대㈃ 嫄대꼫?)
            entry_ok = float(cand.get("entry", 0) or 0) > 0
            if not entry_ok:
                log.warning(f"[TP trailing] {ticker} entry=0 ??hold_advisor 嫄대꼫?, ?몃젅?쇰쭅 利됱떆 ?꾪솚")
            else:
                try:
                    from minority_report.hold_advisor import ask as advisor_ask
                    _d = self.today_judgment.get("digest_prompt", "")
                    _ic = self._build_intraday_context(market)
                    digest = _d + "\n\n[?μ쨷 ?꾩옱]\n" + _ic if _ic else _d
                    advice = advisor_ask(
                        self._advisor_pos(cand, market),
                        market,
                        digest,
                        decision_stage="TP_REVIEW",
                    )
                    if advice["action"] == "SELL":
                        log.info(f"[TP?믩텇?앷??⑹쓽:SELL] {ticker} ??利됱떆 泥?궛")
                        trailing_alert(market, ticker, "sell", detail="遺꾩꽍媛 ?⑹쓽: 利됱떆 泥?궛")
                        self._execute_sell(cand, market, reason="tp_analyst_sell",
                                           hold_advice=advice)
                        return
                    trail_pct  = advice["trail_pct"]
                    hold_advice = advice
                    log.info(f"[TP?믩텇?앷??⑹쓽:HOLD] {ticker} trail={trail_pct:.2%}")
                    trailing_alert(market, ticker, "hold", trail_pct=trail_pct)
                except Exception as e:
                    log.warning(f"[hold_advisor] ?ㅻ쪟 ??trail 湲곕낯媛??곸슜: {e}")
        else:
            log.info(f"[TP?믫듃?덉씪留? {ticker} trail={trail_pct:.2%} (遺꾩꽍媛 ?앸왂)")
            trailing_alert(market, ticker, "trail", trail_pct=trail_pct)
        self.risk.activate_trailing(ticker, trail_pct, hold_advice=hold_advice)
    def _execute_sell(self, cand: dict, market: str, reason: str,
                      hold_advice: dict = None):
        """?ㅼ젣 留ㅻ룄 ?ㅽ뻾 + ?붾젅洹몃옩 ?뚮┝ + hold_advisor 寃곌낵 湲곕줉"""
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
            self._note_sell_failure(market, cand["ticker"], reason, precheck.get("msg", "二쇰Ц 遺덇?"))
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
            log.error(f"sell precheck failed [{cand['ticker']}]: {precheck.get('msg','二쇰Ц 遺덇?')}")
            self._record_decision_event(
                market, "sell_failed", cand["ticker"],
                strategy=cand.get("strategy", ""),
                qty=cand.get("qty", 0),
                price_native=order_px,
                price_krw=cand.get("exit_price", 0),
                reason=reason,
                detail=precheck.get("msg", "二쇰Ц 遺덇?"),
            )
            if precheck.get("reason") == "insufficient_holding" and not has_pending_buy:
                before = len(self.risk.positions)
                self.risk.positions = [p for p in self.risk.positions if p.get("ticker") != cand["ticker"]]
                after = len(self.risk.positions)
                if before != after:
                    log.warning(f"[釉뚮줈而?誘몃낫???뺣━] {cand['ticker']} ?대? ?ъ????쒓굅")
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
            if "?붽퀬?댁뿭???놁뒿?덈떎" in (result.get("msg", "") or ""):
                # 釉뚮줈而??ㅼ젣 ?붽퀬 ?뺤씤 ?꾩뿉留??ъ????쒓굅 (VTS API ?ㅻ쪟濡??명븳 ?ㅼ젣嫄?諛⑹?)
                broker_has = False
                try:
                    bal_chk = get_balance(self.token, market=market, force_refresh=True)
                    broker_tickers = {s["ticker"].upper() if market == "US" else s["ticker"]
                                      for s in bal_chk.get("stocks", [])}
                    chk_key = cand["ticker"].upper() if market == "US" else cand["ticker"]
                    broker_has = chk_key in broker_tickers
                except Exception as _be:
                    log.warning(f"[釉뚮줈而??뺤씤 ?ㅽ뙣] {cand['ticker']}: {_be} ???ъ????좎?")
                    broker_has = True  # ?뺤씤 ?ㅽ뙣 ???덉쟾?섍쾶 ?좎?
                if broker_has:
                    self._sell_fail_at[cand["ticker"]] = time.time()  # 荑⑤떎???쒖옉
                    log.warning(f"[sell failure ignored] {cand['ticker']} broker holding confirmed; retry cooldown {self._SELL_FAIL_COOLDOWN_SEC}s")
                else:
                    before = len(self.risk.positions)
                    self.risk.positions = [p for p in self.risk.positions if p.get("ticker") != cand["ticker"]]
                    after = len(self.risk.positions)
                    if before != after:
                        log.warning(f"[釉뚮줈而?誘몃낫???뺣━] {cand['ticker']} 釉뚮줈而?誘몃낫???뺤씤 ???대? ?ъ????쒓굅")
                        self._save_positions()
                        self._write_live_status(market, force=True)
            return
        log.info(f"[{'PAPER' if self.is_paper else 'LIVE'} SELL] {cand['ticker']} {cand['qty']}@{order_px:,} | 二쇰Ц踰덊샇={result.get('order_no','')}")
        self._sell_fail_meta.pop(cand["ticker"], None)
        self._sell_fail_at.pop(cand["ticker"], None)
        # 留ㅻ룄 ?꾨즺 ?곗빱 ?깅줉 ??broker sync ?ъ＜??諛⑹?
        _closed_key = cand["ticker"].upper() if market == "US" else cand["ticker"]
        self._session_closed_tickers.setdefault(market, set()).add(_closed_key)
        # hold_advice: 遺꾩꽍媛 SELL 利됱떆 泥?궛 ???꾨떖??advice
        # cand????λ맂 hold_advice(?ъ??섏뿉????寃????뺤씤
        advice_used = hold_advice or cand.get("hold_advice")
        audit_keys = (
            "strategy_stop_price",
            "loss_cap_price",
            "effective_stop_price",
            "loss_budget_krw",
            "profit_floor_price",
            "profit_floor_triggered",
            "soft_exit_floor_price",
            "soft_exit_floor_triggered",
            "soft_exit_review_action",
            "soft_exit_review_reason",
            "soft_exit_review_confidence",
            "soft_exit_review_fallback",
            "soft_exit_reference_source",
            "soft_exit_reference_target",
            "pathb_reference_target",
            "pathb_reference_stop",
            "pathb_reference_confidence",
            "pathb_reference_status",
            "selection_reference_target",
            "selection_reference_stop",
            "selection_reference_confidence",
            "peak_pnl_pct",
            "position_mfe_pct",
            "position_mae_pct",
            "exit_owner",
        )
        exit_meta = {key: cand[key] for key in audit_keys if key in cand}
        if reason in ("loss_cap", "profit_floor", "stop_loss", "trail_stop", "soft_exit_floor_price"):
            exit_meta.setdefault("exit_owner", "system_hard_rule")
        ex = self.risk.close_position(cand["ticker"], cand["exit_price"], reason, exit_meta=exit_meta)
        if not ex:
            return
        try:
            proceeds_krw = float(ex.get("exit_price", 0) or 0) * int(ex.get("qty", 0) or 0)
            self._note_recent_sell_proceeds(
                market,
                ex.get("ticker", cand.get("ticker", "")),
                order_no=str(result.get("order_no", "") or ""),
                proceeds_krw=proceeds_krw,
                reason=reason,
            )
        except Exception as exc:
            log.debug(f"[sell proceeds ledger] record failed {market} {cand.get('ticker')}: {exc}")
        # ?뱀씪 ?먯젅 移댁슫?????곗냽 ?먯젅 ???댄썑 吏꾩엯 size_mult ?먮룞 異뺤냼
        if reason in ("loss_cap", "stop_loss", "trail_stop"):
            _sl_mkt = market
            _stop_key = ex["ticker"].upper() if market == "US" else ex["ticker"]
            self._v2_same_day_stop_tickers.setdefault(market, set()).add(_stop_key)
            self._daily_sl_count[_sl_mkt] = self._daily_sl_count.get(_sl_mkt, 0) + 1
            _sl_cnt = self._daily_sl_count[_sl_mkt]
            if _sl_cnt >= 3:
                log.warning(f"[?뱀씪 ?먯젅 {_sl_mkt}] {_sl_cnt}?????좉퇋 吏꾩엯 以묐떒")
            elif _sl_cnt >= 2:
                log.warning(f"[?뱀씪 ?먯젅 {_sl_mkt}] {_sl_cnt}?????댄썑 size 횞 0.6")
            else:
                log.info(f"[?뱀씪 ?먯젅 {_sl_mkt}] {_sl_cnt}?????댄썑 size 횞 0.8")
        # ML DB: 嫄곕옒 寃곌낵 湲곕줉
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
        # ticker_selection_log: ?섏씡 寃곌낵 湲곕줉
        try:
            tsdb.update_pnl(market, ex["ticker"], ex.get("pnl_pct", 0), reason)
        except Exception:
            pass
        if self._is_micro_probe_record(ex):
            try:
                tsdb.update_micro_probe_outcome(
                    market=market,
                    ticker=ex["ticker"],
                    order_no=str(ex.get("order_no", "") or ""),
                    pnl_pct=float(ex.get("pnl_pct", 0) or 0),
                    pnl_krw=float(ex.get("pnl", 0) or 0),
                    exit_reason=reason,
                    exited_at=datetime.now(KST).isoformat(),
                )
            except Exception as exc:
                log.warning(f"micro_probe outcome 湲곕줉 ?ㅽ뙣: {exc}")
        raw_exit_cached = self.price_cache_raw.get(ex["ticker"], 0)
        raw_exit = raw_exit_cached or ex["exit_price"]
        event_price_native = float(raw_exit or 0)
        if market == "US" and not raw_exit_cached and event_price_native > 0 and self.usd_krw_rate > 0:
            event_price_native = event_price_native / float(self.usd_krw_rate)
        self._record_decision_event(
            market, "sell_filled", ex["ticker"],
            strategy=ex.get("strategy", ""),
            qty=ex.get("qty", 0),
            price_native=event_price_native,
            price_krw=ex.get("exit_price", 0),
            reason=reason,
            detail=f"entry={ex.get('entry', 0):,.0f} hold_days={ex.get('held_days', 0)}",
            order_no=result.get("order_no", ""),
            pnl_krw=ex.get("pnl", 0),
            pnl_pct=ex.get("pnl_pct", 0),
            source_strategy=ex.get("source_strategy", ""),
            micro_probe=bool(ex.get("micro_probe")),
            micro_probe_reason=ex.get("micro_probe_reason", ""),
            actual_fill_price=event_price_native,
            **exit_meta,
        )
        self._v2_record_lifecycle_event(
            "CLOSED",
            market,
            ex["ticker"],
            decision_id=str(ex.get("v2_decision_id", "") or ""),
            execution_id=str(result.get("order_no", "") or ex.get("v2_execution_id", "") or ""),
            position_id=str(ex.get("position_id", "") or ""),
            reason_code=_v2_close_reason(reason),
            payload={
                "close_reason": _v2_close_reason(reason),
                "raw_reason": reason,
                "pnl_krw": float(ex.get("pnl", 0) or 0),
                "pnl_pct": float(ex.get("pnl_pct", 0) or 0),
                "qty": int(ex.get("qty", 0) or 0),
                "path_type": ex.get("path_type", ""),
                "path_run_id": ex.get("pathb_path_run_id", ""),
                **exit_meta,
            },
        )
        if getattr(self, "pathb", None) is not None and ex.get("pathb_path_run_id"):
            try:
                self.pathb.on_external_close(
                    ex,
                    market=market,
                    execution_id=str(result.get("order_no", "") or ex.get("v2_execution_id", "") or ""),
                    close_reason=_v2_close_reason(reason),
                    price=float(event_price_native or 0),
                )
            except Exception as _pathb_close_e:
                log.error(f"[PathB external close sync] {market} {ex.get('ticker')}: {_pathb_close_e}", exc_info=True)
        ex_name = str(ex.get("name", "") or "").strip() or self._lookup_ticker_name(ex["ticker"], market)
        _exit_buy_path = "path_b" if (ex.get("pathb_path_run_id") or ex.get("path_type") == "claude_price") else "path_a"
        alert_exit_price = event_price_native
        pnl_alert(ex["ticker"], ex["pnl_pct"], int(ex["pnl"]), reason, market=market, name=ex_name, usd_krw=self.usd_krw_rate, buy_path=_exit_buy_path)
        trade_alert("sell", ex["ticker"], ex["qty"], alert_exit_price, ex["strategy"], 0, 0, reason=reason, market=market, name=ex_name, usd_krw=self.usd_krw_rate, buy_path=_exit_buy_path)
        try:
            _perf_strategy = str(ex.get("strategy", "unknown") or "unknown")
            if _perf_strategy not in ("broker_sync", "broker_balance", ""):
                BrainDB.update_strategy_performance(
                    market, _perf_strategy,
                    ex.get("pnl_pct", 0), ex.get("pnl", 0) > 0
                )
        except Exception as e:
            log.warning(f"strategy brain update failed: {e}")
        try:
            self._save_positions()
        except Exception:
            pass
        # ?? hold_advisor 寃곌낵 湲곕줉 ?????????????????????????????????????????????
        if advice_used:
            self._record_hold_advisor_outcome(ex, market, advice_used)
    def _record_hold_advisor_outcome(self, ex: dict, market: str, advice: dict):
        """
        泥?궛 ?꾨즺 ??hold_advisor 寃곗젙???깃났/?ㅽ뙣 ?먯젙 諛?湲곕줉.
        - HOLD 寃곗젙: exit_price > tp_price ??異붽? ?섏씡 ?ㅽ쁽 ???깃났
        - SELL 寃곗젙: TP 利됱떆 ?ㅽ쁽 ?먯껜 ???깃났 (湲고쉶鍮꾩슜? ?ы썑 鍮꾧탳)
        """
        try:
            decision    = advice.get("action", "SELL")
            tp_price    = ex.get("tp_price", 0.0) or ex.get("tp", 0.0)
            exit_price  = ex.get("exit_price", 0.0)
            entry_price = ex.get("entry", 0.0)
            # US: price_cache (KRW ?섏궛媛? ?곗꽑 ?ъ슜 ??raw USD媛 ?섎せ ?ㅼ뼱?ㅻ뒗 寃쎌슦 諛⑹?
            if market == "US":
                ticker = ex.get("ticker", "")
                krw_px = self.price_cache.get(ticker, 0.0)
                if krw_px > 0 and self.usd_krw_rate > 0:
                    # price_cache 媛믪씠 exit_price蹂대떎 ?좊ː???믪쓬
                    exit_price = krw_px
                elif exit_price > 0 and self.usd_krw_rate > 0 and exit_price < 5000:
                    # exit_price媛 USD raw 媛믪쑝濡??섏떖????(< $5000 ?섏?) ??KRW ?섏궛
                    exit_price = exit_price * self.usd_krw_rate
            if decision in ("HOLD", "TRAIL"):
                # HOLD/TRAIL ?깃났: 泥?궛媛 > TP ?꾨떖媛 (蹂댁쑀/?몃젅???뺤뿉 ??踰뚯뿀??
                success = exit_price > tp_price if tp_price > 0 else (ex.get("pnl", 0) > 0)
                # 異붽? ?섏씡: TP ?鍮?泥?궛媛 李⑥씠
                extra_pnl_pct = ((exit_price / tp_price - 1) * 100) if tp_price > 0 else 0.0
            else:  # SELL (利됱떆 泥?궛)
                # TP ?꾨떖 ??利됱떆 泥?궛 ?먯껜???섏씡 ?ㅽ쁽
                success = ex.get("pnl", 0) > 0
                extra_pnl_pct = ex.get("pnl_pct", 0.0)
            # brain.json ?꾩쟻
            BrainDB.update_hold_advisor_performance(
                market, ex["ticker"], decision, success, extra_pnl_pct
            )
            # JSONL outcome ?낅뜲?댄듃
            self._update_hold_advisor_jsonl_outcome(
                ex["ticker"], decision, success, exit_price, extra_pnl_pct
            )
            result_icon = "OK" if success else "FAIL"
            log.info(
                f"[hold_advisor 寃곌낵] {ex['ticker']} {decision} ??"
                f"{result_icon} {'?깃났' if success else '?ㅽ뙣'} "
                f"extra_pnl={extra_pnl_pct:+.2f}%"
            )
        except Exception as e:
            log.warning(f"[hold_advisor] 寃곌낵 湲곕줉 ?ㅽ뙣: {e}")
    def _update_hold_advisor_jsonl_outcome(
        self, ticker: str, decision: str, success: bool,
        exit_price: float, extra_pnl_pct: float
    ):
        """?ㅻ뒛??decisions JSONL?먯꽌 ?대떦 醫낅ぉ 誘몄셿猷??덉퐫?쒖뿉 outcome??湲곕줉"""
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
            log.warning(f"[hold_advisor] JSONL outcome ?낅뜲?댄듃 ?ㅽ뙣: {e}")
    def _backfill_missed_postmortem(self, market: str):
        """?ъ떆?뫢룸퉬?뺤긽醫낅즺濡?session_close 誘몄떎????actual_result 怨듬갚????postmortem ?뚭툒"""
        base_date = self._current_session_date(market)
        for delta in range(1, 4):
            target_date = (base_date - timedelta(days=delta)).isoformat()
            fname = _judgment_runtime_path(self._mode, target_date, market)
            if not fname.exists():
                fname = JUDGMENT_DIR / f"{target_date.replace('-', '')}_{market}.json"
            if not fname.exists():
                continue
            try:
                with open(fname, encoding="utf-8") as f:
                    rec = json.load(f)
            except Exception:
                continue
            if not rec.get("judgments"):
                continue
            if rec.get("actual_result"):  # ?대? ?덉쑝硫??ㅽ궢
                continue
            sells = [t for t in (rec.get("trades") or []) if t.get("side") == "sell"]
            total_pnl_krw = sum(t.get("pnl", 0) for t in sells)
            start_eq = (
                getattr(self.risk, "session_start_equity", 0)
                or getattr(self.risk, "cash", 0)
                or 1
            )
            actual = {
                "market_change": get_index_change(market),
                "pnl_pct":  total_pnl_krw / start_eq * 100 if sells else 0.0,
                "pnl_krw":  int(total_pnl_krw),
                "win":      total_pnl_krw > 0,
                "trades":   len(sells),
                "cumulative": 0,
            }
            log.info(f"[postmortem ?뚭툒] {target_date} {market} ??session_close ?꾨씫 媛먯?, ?뚭툒 ?ㅽ뻾")
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
                log.info(f"[postmortem ?뚭툒 ?꾨즺] {target_date} {market}")
            except Exception as e:
                log.warning(f"[postmortem ?뚭툒 ?ㅽ뙣] {target_date} {market}: {e}")
    def _seconds_until_session_close(self, market: str) -> float:
        now_kst = datetime.now(KST)
        try:
            close_dt = self._market_close_anchor_dt(str(market or "").upper())
        except Exception:
            if market == "KR":
                close_dt = now_kst.replace(hour=15, minute=30, second=0, microsecond=0)
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
        today = self._current_session_date_str(market)
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
                rec_session_date = str(rec.get("session_date") or str(rec.get("timestamp", "") or "")[:10] or "")
                if rec_session_date != today:
                    continue
                order_no = str(rec.get("order_no", "") or "").strip()
                dedup_key = order_no or f"{rec.get('ticker', '')}_{rec.get('timestamp', '')}"
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)
                restored_pnl += float(rec.get("pnl_krw", 0) or 0)
                restored_count += 1
        except Exception as e:
            log.warning(f"[{market}] decisions.jsonl daily_pnl 蹂듦뎄 ?ㅽ뙣: {e}")
            return
        self.risk.daily_pnl = restored_pnl
        if restored_count > 0:
            log.info(f"[{market}] daily_pnl 蹂듦뎄: {restored_pnl:,.0f}??({restored_count}嫄?")
    def session_open(self, market: str, trigger: str = "schedule"):
        if not self._enter_market_task(market, "session_open"):
            return
        if not self._market_enabled(market):
            log.info(f"[{market}] disabled by ENABLED_MARKETS; session_open skipped")
            self._leave_market_task(market, "session_open")
            return
        _log_flow("info", "=" * 50)
        _log_flow("info", f"[{market}] session_open")
        self._refresh_claude_control()
        session_date_obj = _market_session_date(market)
        # ?? ?댁옣??泥댄겕 (二쇰쭚 + 怨듯쑕?? ?????????????????????????????????????
        if not _is_trading_day(market, session_date_obj):
            _log_flow("info", f"[{market}] ?댁옣?????몄뀡 ?ㅽ궢")
            self.session_active = False
            self.current_market = None
            if hasattr(self, "_active_session_date") and isinstance(self._active_session_date, dict):
                self._active_session_date[market] = None
            self._update_session_date_diagnostics(market, "session_open_skipped")
            self._leave_market_task(market, "session_open")
            return
        # US 媛쒖옣 ???ㅽ겕由щ꼫 罹먯떆 臾댄슚?????좎꽑???꾨낫濡??쒖옉
        self._set_active_session_date(market, session_date_obj)
        self._update_session_date_diagnostics(market, "session_open")
        self._reset_runtime_overrides(market)
        self._reset_runtime_reason_counters(market)
        self._backfill_missed_postmortem(market)
        if market == "US":
            try:
                _US_SCREEN_CACHE_PATH.unlink(missing_ok=True)
                _log_flow("info", "[US] session-open screener cache invalidated")
            except Exception as _uce:
                log.debug(f"[US] ?ㅽ겕由щ꼫 罹먯떆 臾댄슚???ㅽ뙣(臾댁떆): {_uce}")
        self._refresh_token()
        self.session_active = True
        self.current_market = market
        try:
            for _cache in (self.price_cache_raw, self.price_cache):
                for _ticker in list(_cache.keys()):
                    if self._ticker_market(str(_ticker)) == market:
                        _cache.pop(_ticker, None)
            log.info(f"[{market}] price cache cleared at session_open")
        except Exception as _pc_clear_e:
            log.warning(f"[{market}] price cache clear failed at session_open: {_pc_clear_e}")
        self._execution_flags[market] = set()
        self.risk.market = market          # ?섏닔猷뚯쑉 ?쒖옣??留욊쾶 ?ㅼ젙
        self.tuning_count = 0
        self._last_tune_result[market] = {}
        self._tune_maintain_streak[market] = 0
        self._index_history[market].clear()
        self._session_events = []
        self._entry_blocked = {}
        self._order_error_count = {}
        self.decision_event_log = []
        self._daily_sl_count[market] = 0
        self._session_closed_tickers[market] = set()
        self._v2_same_day_stop_tickers[market] = set()
        self._normalize_pending_orders()
        self._save_pending_orders()
        self._reset_us_order_cache()
        self._sync_runtime_with_broker()
        if getattr(self, "pathb", None) is not None:
            try:
                self.pathb.refresh_broker_truth(market, force=True)
                self._reconcile_broker_open_orders(market, reason="session_open", force=False)
                _ou_summary = self.pathb.reconcile_order_unknowns_at_open(market)
                try:
                    _esc = (_ou_summary or {}).get("escalator") or {}
                    if (_ou_summary or {}).get("auto_cleared_no_broker_evidence") or _esc.get("market_pause_cleared"):
                        self._audit_close_active_episode(
                            market,
                            episode_type="ORDER_UNKNOWN_PAUSE",
                            scope="market",
                            reason="ORDER_UNKNOWN_UNRESOLVED",
                            clear_reason="session_open_auto_reconcile",
                            payload=_ou_summary or {},
                        )
                except Exception:
                    pass
                self.pathb.scan_waiting_entries(market)
                self.pathb.scan_exits(market)
            except Exception as _pathb_cycle_e:
                log.error(f"[PathB cycle] {market}: {_pathb_cycle_e}", exc_info=True)
        self._refresh_position_holding_days(market)
        # ?쒖옣蹂?max_order_krw ?ш퀎????KR: ?먰솕?붽퀬 湲곗?, US: USD ?꾧툑 湲곗?
        try:
            env_cap   = int(os.getenv("MAX_ORDER_KRW", "500000" if self.is_paper else "2000000"))
            order_pct = float(os.getenv("MAX_ORDER_PCT", "0.05"))
            if market == "US":
                bal_us    = get_balance(self.token, market="US")
                us_cash   = float(bal_us.get("cash", 0) or 0) * self.usd_krw_rate
                new_max   = min(env_cap, int(us_cash * order_pct))
            else:
                bal_kr    = get_balance(self.token, market="KR")
                kr_cash   = float(bal_kr.get("cash", 0) or 0) + float(bal_kr.get("total_eval", 0) or 0)
                new_max   = min(env_cap, int(kr_cash * order_pct))
            if new_max > 0:
                self.risk.max_order_krw = float(new_max)
                _log_normal("info", f"[max_order update] {market} {new_max:,} KRW")
        except Exception as _mo_e:
            _log_risk("warning", f"[max_order 媛깆떊 ?ㅽ뙣] {market}: {_mo_e}")
        # ?? ?곗씠??臾닿껐???먮룞 ?먭? ??????????????????????????????????????????
        try:
            from claude_memory.data_integrity import run_pre_session_check
            integrity = run_pre_session_check(market)
            for msg in integrity["fixed"]:
                _log_normal("info", f"[data_integrity] ?섏젙: {msg}")
            for msg in integrity["warnings"]:
                _log_risk("warning", f"[data_integrity] 寃쎄퀬: {msg}")
            if integrity["fixed"]:
                _log_normal("info", f"[data_integrity] 珥?{len(integrity['fixed'])}嫄??먮룞 ?섏젙 ?꾨즺")
        except Exception as _di_e:
            _log_risk("warning", f"[data_integrity] ?먭? ?ㅽ뙣 (臾댁떆?섍퀬 怨꾩냽): {_di_e}")
        # ?? USD/KRW ?섏쑉 ?먮룞 媛깆떊 ????????????????????????????????????????????
        try:
            new_rate = get_usd_krw()
            if new_rate > 100:
                old_rate = self.usd_krw_rate
                self.usd_krw_rate = new_rate
                _log_normal("info", f"[fx update] USD/KRW {old_rate:,.0f} -> {new_rate:,.2f}")
        except Exception as e:
            _log_risk("warning", f"[?섏쑉 媛깆떊 ?ㅽ뙣] {e} ???댁쟾 媛?{self.usd_krw_rate:,.0f}???좎?")
        # ?댁썡 ?ъ????꾩옱媛 媛깆떊 (?댁젣 醫낃? ???ㅻ뒛 ?쒓? 諛⑺뼢?쇰줈 ?낅뜲?댄듃)
        for pos in self.risk.positions:
            if self._ticker_market(pos["ticker"]) == market:
                try:
                    price_info = get_price(pos["ticker"], self.token, market=market)
                    raw_price = price_info["price"]
                    self.price_cache_raw[pos["ticker"]] = raw_price
                    self.price_cache[pos["ticker"]] = self._price_to_krw(raw_price, market)
                except Exception as e:
                    log.warning(f"?댁썡 ?ъ????쒓? 議고쉶 ?ㅽ뙣 [{pos['ticker']}]: {e}")
        self.risk.update_prices(self.price_cache, self.price_cache_raw)
        # ?? session_start_equity: ?섏쑉 媛깆떊 + ?꾩옱媛 諛섏쁺 ?댄썑 湲곗??쇰줈 ?뺤젙 ??
        # (?댁쟾??reset_daily_state瑜?癒쇱? ?몄텧?섎㈃ 援ы솚??湲곗??쇰줈 base媛 ?≫?
        #  ?섏쑉 蹂?숇텇???쇱씪 ?먯떎濡??섎せ 怨꾩궛?섎뒗 踰꾧렇 ?섏젙)
        session_date = self._current_session_date_str(market)
        baseline_meta = dict(self._daily_baseline_by_market.get(market, {}) or {})
        if baseline_meta.get("session_date") == session_date and float(baseline_meta.get("base", 0) or 0) > 0:
            self.risk.session_start_equity = float(baseline_meta["base"])
            if trigger == "startup_mid_session":
                self._restore_daily_pnl_from_decisions(market)
            _log_normal(
                "info",
                f"[?쇱씪 湲곗? ?좎?] {market} session_date={session_date} "
                f"session_start_equity={self.risk.session_start_equity:,.0f} KRW"
            )
        else:
            broker_total = float(self._market_broker_total_equity_krw(market) or 0)
            if broker_total <= 0:
                broker_total = float(self._market_equity_reference_context(market).get("total_krw", 0) or 0)
            self.risk.reset_daily_state(clear_trade_log=True, override_base=broker_total if broker_total > 0 else None)
            self._daily_baseline_by_market[market] = {
                "session_date": session_date,
                "base": float(self.risk.session_start_equity),
                "source": "broker_market_total" if broker_total > 0 else "internal_market_fallback",
            }
            self._save_daily_baselines()
            _log_normal(
                "info",
                f"[?쇱씪 湲곗? ?뺤젙] {market} session_date={session_date} "
                f"session_start_equity={self.risk.session_start_equity:,.0f}??"
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
                        f"{p['ticker']} ${display_px:.4f} (?대?KRW {internal_px:,.0f}??"
                    )
                else:
                    updated_prices.append(f"{p['ticker']} {internal_px:,.0f} KRW")
            if updated_prices:
                log.info(f"[?댁썡 ?ъ????꾩옱媛 媛깆떊] {updated_prices}")
        today = self._current_session_date_str(market)
        pre_candidates = None
        universe_tickers = None
        if self.enable_dynamic_universe:
            if market == "KR":
                pre_candidates = self._screen_market_candidates("KR", self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
                if pre_candidates:
                    self._last_kr_candidates = pre_candidates
            else:
                pre_candidates = self._screen_market_candidates("US", self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
            if pre_candidates:
                snapshot = build_universe_from_candidates(
                    market=market,
                    target_date=today,
                    candidates=pre_candidates,
                    config=UniverseConfig(top_n=self._dynamic_universe_top_n_for_market(market)),
                    source="runtime_screen",
                    core_tickers=get_core_tickers(market),
                )
                save_universe_snapshot(snapshot)
                self.today_universe[market] = snapshot
                universe_tickers = snapshot.get("tickers", [])
                log.info(
                    f"[?좊땲踰꾩뒪] {market} ?꾨낫 {len(pre_candidates)} -> "
                    f"{len(universe_tickers)} tickers"
                )
            else:
                snapshot = load_universe_snapshot(market, today)
                if snapshot.get("tickers"):
                    self.today_universe[market] = snapshot
                    universe_tickers = snapshot.get("tickers", [])
                    log.info(f"[universe] {market} loaded {len(universe_tickers)} tickers")
        # ?? ?ъ떆?????뱀씪 ?먮떒 ?ъ궗??????????????????????????????????????????
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
                        "selection_meta": saved.get("selection_meta", {}),
                        "trade_ready_tickers": saved.get("trade_ready_tickers", []),
                        "claude_runtime_overrides": saved.get("claude_runtime_overrides", {}),
                        # debate ?꾨뱶 蹂듭썝 (?뚯씤?쒕떇 ?곗씠???곗냽???좎?)
                        "round1_judgments": saved.get("round1_judgments", {}),
                        "debate_changes": saved.get("debate_changes", []),
                    }
                    self.today_tickers[market] = saved.get("tickers", [])
                    self._entry_timing_mark_candidates(market, self.today_tickers.get(market, []), "session_reuse_saved")
                    restored_meta = dict(saved.get("selection_meta", {}) or {})
                    if saved.get("trade_ready_tickers") and not restored_meta.get("trade_ready"):
                        restored_meta["trade_ready"] = list(saved.get("trade_ready_tickers") or [])
                    self.selection_meta[market] = self._normalize_selection_meta_runtime(
                        market,
                        restored_meta,
                        saved.get("tickers", []),
                        mode=saved.get("consensus", {}).get("mode", ""),
                    )
                    self.trade_ready_tickers[market] = list(self.selection_meta[market].get("trade_ready") or [])
                    self.today_judgment["selection_meta"] = self.selection_meta[market]
                    self.today_judgment["trade_ready_tickers"] = self.trade_ready_tickers[market]
                    self._restore_runtime_overrides_from_payload(market, saved)
                    judgments = saved["judgments"]
                    consensus = self._apply_consensus_guards(
                        market,
                        judgments,
                        saved["consensus"],
                        source="session_reuse_live",
                    )
                    self.today_judgment["consensus"] = consensus
                    digest_prompt = saved.get("digest_prompt", "")
                    reused = True
                    log.info(f"[?ъ떆???먮떒 ?ъ궗?? {today} {market} consensus={consensus['mode']}")
            except Exception as e:
                log.warning(f"??λ맂 ?먮떒 濡쒕뱶 ?ㅽ뙣: {e}")
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
                        "selection_meta": saved.get("selection_meta", {}),
                        "trade_ready_tickers": saved.get("trade_ready_tickers", []),
                        "claude_runtime_overrides": saved.get("claude_runtime_overrides", {}),
                        "round1_judgments": saved.get("round1_judgments", {}),
                        "debate_changes": saved.get("debate_changes", []),
                    }
                    self.today_tickers[market] = saved.get("tickers", [])
                    self._entry_timing_mark_candidates(market, self.today_tickers.get(market, []), "session_reuse_legacy")
                    restored_meta = dict(saved.get("selection_meta", {}) or {})
                    if saved.get("trade_ready_tickers") and not restored_meta.get("trade_ready"):
                        restored_meta["trade_ready"] = list(saved.get("trade_ready_tickers") or [])
                    self.selection_meta[market] = self._normalize_selection_meta_runtime(
                        market,
                        restored_meta,
                        saved.get("tickers", []),
                        mode=saved.get("consensus", {}).get("mode", ""),
                    )
                    self.trade_ready_tickers[market] = list(self.selection_meta[market].get("trade_ready") or [])
                    self.today_judgment["selection_meta"] = self.selection_meta[market]
                    self.today_judgment["trade_ready_tickers"] = self.trade_ready_tickers[market]
                    self._restore_runtime_overrides_from_payload(market, saved)
                    judgments = saved["judgments"]
                    consensus = self._apply_consensus_guards(
                        market,
                        judgments,
                        saved["consensus"],
                        source="session_reuse_legacy",
                    )
                    self.today_judgment["consensus"] = consensus
                    digest_prompt = saved.get("digest_prompt", "")
                    reused = True
                    log.info(f"[session reuse fallback] {today} {market} consensus={consensus['mode']}")
            except Exception as e:
                log.warning(f"[session reuse fallback] load failed: {e}")
        # ?? 怨듭쑀 ?먮떒 罹먯떆 ?뺤씤 (paper/live Claude 以묐났 ?몄텧 諛⑹?) ?????????????
        # 媛숈? ??媛숈? ?μ뿉????履??꾨줈?몄뒪媛 ?대? ?먮떒???앹꽦?덉쑝硫?        # get_three_judgments ?ы샇異??놁씠 寃곌낵瑜??ъ궗?⑺븳??
        # ?ㅽ겕由щ꼫 + select_tickers ???ы듃?대━??留λ씫???щ씪 媛곸옄 ?ㅽ뻾?쒕떎.
        _from_shared_cache = False
        if not reused:
            _shared = shared_judgment_cache.load(market, today)
            if _shared:
                judgments = _shared["judgments"]
                consensus = self._apply_consensus_guards(
                    market,
                    judgments,
                    _shared["consensus"],
                    source="shared_cache",
                )
                _from_shared_cache = True
                log.info(
                    f"[怨듭쑀?먮떒罹먯떆] {today} {market} consensus={consensus['mode']} "
                    f"(Claude ?ы샇異??앸왂, {self._mode} ?고????ъ궗??"
                )
        if not reused:
            digest = build_kr_digest(today, universe_tickers=universe_tickers) if market == "KR" \
                else build_us_digest(today, universe_tickers=universe_tickers)
            # select_tickers / watchlist_alert ???꾩옱 ?고??꾩뿉???ㅼ떆 留뚮뱺 理쒖떊 digest瑜??ъ슜?쒕떎.
            digest_prompt = digest_to_prompt(digest)
            if not _from_shared_cache:
                # ?? Claude ?먮떒 (?좉퇋 ?앹꽦) ??????????????????????????????????
                brain_summary, correction = self._brain_context_for_judge(market)
                lesson_context = self._load_lesson_candidate_summary(market)
                judgments = get_three_judgments(
                    digest_prompt, brain_summary, correction,
                    market=market,
                    lesson_context=lesson_context,
                    portfolio_info=self._build_portfolio_info(),
                )
                consensus = self._apply_consensus_guards(
                    market,
                    judgments,
                    build_consensus(judgments, market=market),
                    source="session_open",
                )
            # ?? VIX ?숈쟻 ?ъ씠利?蹂댁젙 (US ?꾩슜, ??긽 理쒖떊 digest 湲곗?) ?????????
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
                            f"[VIX={vix:.1f}] ?ъ씠利?異뺤냼 x{vix_mult} "
                            f"{orig_size}% ??{consensus['size']}%"
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
            # ?ㅻ뒛 吏묒쨷 醫낅ぉ: ?ㅽ겕由щ꼫 ???덉뒪?좊━ ?꾪꽣 ??Claude ?좏깮
            log.info(f"[?ㅽ겕由щ꼫] {market} ?쒖옣 ?ㅼ틪 以?..")
            if market == "KR":
                candidates = self._screen_market_candidates("KR", consensus.get("mode", "NEUTRAL"))
                if candidates:
                    self._last_kr_candidates = candidates
            else:
                candidates = self._screen_market_candidates("US", consensus.get("mode", "NEUTRAL"))
            raw_candidates = list(candidates or [])
            self._log_screen_candidates(market, candidates, "session_open")
            candidates = self._prefill_history_sync(candidates, market)
            candidates = self._filter_candidates_by_history(candidates, market)
            candidates = self._restrict_candidates_to_universe(candidates, universe_tickers)
            candidates = self._annotate_selection_execution_features(market, candidates, consensus.get("mode", "NEUTRAL"))
            _intraday_ctx = self._build_intraday_context(market)
            _lesson_context = self._load_lesson_candidate_summary(market)
            log.info(f"[?ㅽ겕由щ꼫] {market} ?꾨낫 {len(candidates)}媛???Claude ?좏깮 以?..")
            selected, sel_reasons = select_tickers(market, digest_prompt, consensus["mode"], candidates,
                                                    lesson_context=_lesson_context,
                                                    intraday_context=_intraday_ctx,
                                                    market_change_pct=self._get_market_change_pct(market, digest),
                                                    secondary_change_pct=self._get_secondary_change_pct(market, digest))
            sel_meta = self._apply_selection_meta(market, selected)
            self._record_candidate_quality(
                market,
                "session_open",
                raw_candidates,
                candidates,
                selected,
                sel_meta,
                sel_reasons,
            )
            self._update_candidate_health(
                market,
                "session_open",
                selected,
                sel_meta,
                candidates,
            )
            self.today_tickers[market] = selected
            self._entry_timing_mark_candidates(market, selected, "session_open")
            self.today_ticker_reasons[market] = sel_reasons or {}
            # 퍼널: selected 카운트 (세션 시작 시 선택된 종목 수)
            self._funnel[market]["selected"] += len(selected)
            log.info(f"[종목선택 확정] {market}: watch={selected} trade_ready={sel_meta.get('trade_ready', [])}")
            try:
                _tsdb_ids = tsdb.insert_batch(
                    today, market, "initial", selected, candidates, sel_reasons, consensus["mode"],
                    selection_meta=sel_meta,
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
                    "trade_ready": sel_meta.get("trade_ready", []),
                    "consensus_mode": consensus["mode"],
                }},
            )
            excluded = [c.get("ticker","") for c in candidates if c.get("ticker","") not in selected]
            _mode_order_limit = self.risk.calc_order_budget(consensus.get("size", 50))
            watchlist_alert(market, consensus["mode"], selected, sel_reasons, excluded, trigger="session_open",
                            mode_order_limit_krw=_mode_order_limit)
            # _debate 硫뷀??곗씠??遺꾨━ ??today_judgment???④퍡 蹂닿?
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
                "digest_raw": digest,          # VIX ???먮낯 ?곗씠???ы뙋??寃쎈줈?먯꽌 李몄“
                "tickers": selected,
                "selection_meta": sel_meta,
                "selection_stages": self.selection_stages.get(market, {}),
                "trade_ready_tickers": sel_meta.get("trade_ready", []),
                "universe_tickers": [c.get("ticker") for c in candidates if c.get("ticker")],
                "round1_judgments": debate_meta.get("r1", {}),
                "debate_changes":   debate_meta.get("changes", []),
            }
            # ?? ?좉퇋 ?앹꽦 ?먮떒??怨듭쑀 罹먯떆?????(?곷? ?꾨줈?몄뒪媛 ?ъ궗?? ??????
            self._restore_runtime_overrides_from_payload(
                market,
                _shared if _from_shared_cache else self.today_judgment,
            )
            if not _from_shared_cache:
                shared_judgment_cache.save(market, today, self.today_judgment)
        else:
            # 판단은 재사용하되 종목은 항상 새로 스크리닝한다.
            # reused=True 때 전날 저장된 종목을 그대로 고정하는 문제를 막는다.
            log.info(f"[종목 재스크리닝] {market} 판단 재사용 + 종목만 새로 선택 (mode={consensus['mode']})")
            if market == "KR":
                fresh_candidates = self._screen_market_candidates("KR", self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
                if fresh_candidates:
                    self._last_kr_candidates = fresh_candidates
            else:
                fresh_candidates = self._screen_market_candidates("US", self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
            if fresh_candidates:
                fresh_raw_candidates = list(fresh_candidates or [])
                self._log_screen_candidates(market, fresh_candidates, "session_reuse_rescreen")
                fresh_candidates = self._prefill_history_sync(fresh_candidates, market)
                fresh_candidates = self._filter_candidates_by_history(fresh_candidates, market)
                fresh_candidates = self._restrict_candidates_to_universe(
                    fresh_candidates,
                    (self.today_universe.get(market) or {}).get("tickers")
                    or self.today_judgment.get("universe_tickers", []),
                )
                fresh_candidates = self._annotate_selection_execution_features(market, fresh_candidates, consensus.get("mode", "NEUTRAL"))
                _fresh_intraday_ctx = self._build_intraday_context(market)
                _fresh_lesson_context = self._load_lesson_candidate_summary(market)
                fresh_selected, fresh_reasons = select_tickers(market, digest_prompt, consensus["mode"], fresh_candidates,
                                                               lesson_context=_fresh_lesson_context,
                                                               intraday_context=_fresh_intraday_ctx,
                                                               market_change_pct=self._get_market_change_pct(market),
                                                               secondary_change_pct=self._get_secondary_change_pct(market))
                fresh_meta = self._apply_selection_meta(market, fresh_selected)
                self._record_candidate_quality(
                    market,
                    "session_reuse_rescreen",
                    fresh_raw_candidates,
                    fresh_candidates,
                    fresh_selected,
                    fresh_meta,
                    fresh_reasons,
                )
                self._update_candidate_health(
                    market,
                    "session_reuse_rescreen",
                    fresh_selected,
                    fresh_meta,
                    fresh_candidates,
                )
                self.today_tickers[market] = fresh_selected
                self._entry_timing_mark_candidates(market, fresh_selected, "session_reuse_rescreen")
                self.today_ticker_reasons[market] = fresh_reasons or {}
                self.today_judgment["tickers"] = fresh_selected
                try:
                    _tsdb_ids = tsdb.insert_batch(
                        today, market, "rescreen", fresh_selected, fresh_candidates, fresh_reasons, consensus["mode"],
                        selection_meta=fresh_meta,
                    )
                    self._tsdb_selection_ids[market].update(_tsdb_ids)
                except Exception as _te:
                    log.warning(f"[tsdb] insert_batch(rescreen) failed: {_te}")
                self.today_judgment["universe_tickers"] = [
                    c.get("ticker") for c in fresh_candidates if c.get("ticker")
                ]
                log.info(
                    f"[종목 재선정 완료] {market}: watch={fresh_selected} "
                    f"trade_ready={fresh_meta.get('trade_ready', [])}"
                )
                judgment_log.info(
                    f"[rescreen {today} {market}] {fresh_selected}",
                    extra={"extra": {
                        "event": "ticker_rescreen",
                        "date": today,
                        "market": market,
                        "selected": fresh_selected,
                        "trade_ready": fresh_meta.get("trade_ready", []),
                        "consensus_mode": consensus["mode"],
                    }},
                )
                fresh_excluded = [c.get("ticker", "") for c in fresh_candidates if c.get("ticker", "") not in fresh_selected]
                watchlist_alert(market, consensus["mode"], fresh_selected, fresh_reasons, fresh_excluded, trigger="rescreen")
            else:
                log.warning(f"[醫낅ぉ ?ъ뒪?щ━?? ?꾨낫 ?놁쓬 ??湲곗〈 醫낅ぉ ?좎?: {self.today_tickers.get(market, [])}")
        # ??쒕낫?쒓? ??以묒뿉???ㅻ뒛 ?먮떒??蹂????덈룄濡?session_open 吏곹썑 利됱떆 湲곕줉
        self._persist_live_judgment(market)
        # ?μ쟾 蹂댁쑀 ?ъ???由щ럭???뱀씪 ?먮떒??以鍮꾨맂 ?ㅼ뿉留??섑뻾?쒕떎.
        try:
            self._pre_session_position_review(market)
        except Exception as _psr_e:
            log.warning(f"[?μ쟾 ?ъ???由щ럭 ?ㅻ쪟] {market}: {_psr_e}")
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
            reason = "shared_cache_reuse" if _from_shared_cache else "same_day_reuse"
            log.info(f"[{reason}] consensus={consensus['mode']}")
        log.info(f"consensus: {consensus['mode']} size={consensus['size']}%")
        self.ws = KISWebSocket(
            self.token, selected,
            on_tick=self._on_tick,
            on_notice=self._on_fill_notice,
            market=market,
        )
        self.ws.start()
        self._session_open_at[market] = time.time()
        self._session_startup_guard_sec[market] = self._compute_startup_guard_sec(market, trigger)
        if self._session_startup_guard_sec[market] <= 0:
            _log_flow(
                "info",
                f"[{market}] startup guard bypass "
                f"(trigger={trigger}, close_in={self._seconds_until_session_close(market):.0f}s)"
            )
        # ?? Claude ?뚮씪誘명꽣 寃??(4th layer) ??session_open 1????????????????
        try:
            _param_tuner.reset_session(market)
            self._run_param_review(market, trigger="session_open")
        except Exception as _pt_e:
            log.warning(f"[param_tuner session_open] {market}: {_pt_e}")
        # ???몄뀡 ?쒖옉 ???μ쨷 H/L 珥덇린??+ continuation entry ?뚮옒洹?由ъ뀑
        for _t in selected:
            self._intraday_high.pop(_t, None)
            self._intraday_low.pop(_t, None)
            self._or_high.pop(_t, None)
            self._or_low.pop(_t, None)
            self._or_formed.pop(_t, None)
            self._continuation_used.pop(_t, None)
        # session_open ?꾨즺 利됱떆 ??쒕낫??媛뺤젣 媛깆떊 ??stale live_status 諛⑹?
        self._maybe_push_dashboard(force=True)
        self._leave_market_task(market, "session_open")
    # ?? Claude ?뚮씪誘명꽣 寃???ы띁 ?????????????????????????????????????????????
    def _run_param_review(self, market: str, trigger: str = "session_open") -> None:
        """紐⑤뱺 ?꾨왂??adaptive_params瑜?怨꾩궛??Claude 寃???덉씠?댁뿉 ?섍릿??
        寃곌낵??_param_tuner._cache[market]????λ릺??        run_cycle??_ap() ?ы띁?먯꽌 ?먮룞?쇰줈 李몄“?쒕떎.
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
                log.debug("[param_review] %s params ?ㅻ쪟: %s", strat, _ape)
        if not all_params:
            return
        _param_tuner.claude_review(
            market=market,
            mode=mode,
            all_params=all_params,
            context={
                "vix":          vix,
                "usd_krw":      usd_krw,
                "analyst_conf": conf,
                "asof":         datetime.now(KST).strftime("%H:%M"),
            },
            trigger=trigger,
            force=(trigger in ("reverse", "mode_change")),
        )
    def _on_tick(self, data: dict):
        ticker = data["ticker"]
        market = self._ticker_market(ticker)
        raw_price = data["price"]
        if not raw_price or raw_price <= 0:
            log.warning(f"[WS tick] {ticker} invalid price={raw_price}")
            return
        self.price_cache_raw[ticker] = raw_price
        self.price_cache[ticker] = self._price_to_krw(raw_price, market)
        self.risk.update_prices(self.price_cache, self.price_cache_raw)
        # Track intraday high/low for signal diagnostics.
        if raw_price > self._intraday_high.get(ticker, 0):
            self._intraday_high[ticker] = raw_price
        _cur_low = self._intraday_low.get(ticker, float("inf"))
        if raw_price < _cur_low:
            self._intraday_low[ticker] = raw_price
        # ?μ큹 OR(opening range) 異붿쟻 ??OR ?덈룄??醫낅즺 ??freeze
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
        if getattr(self, "pathb", None) is not None and self.session_active and self.current_market == market:
            try:
                self.pathb.scan_waiting_entries(market)
                self.pathb.scan_exits(market)
            except Exception as _pathb_tick_e:
                log.error(f"[PathB tick] {market} {ticker}: {_pathb_tick_e}", exc_info=True)
    def _on_fill_notice(self, event: dict):
        """KIS WS 泥닿껐?듬낫 ?섏떊 ??pending ??filled 利됱떆 ?꾪솚 (KR + US)"""
        order_no    = event.get("order_no", "")
        ticker      = event.get("ticker", "")
        filled_qty  = int(event.get("filled_qty", 0) or 0)
        filled_price= float(event.get("filled_price", 0) or 0)
        filled_time = event.get("filled_time", "")
        side        = event.get("side", "buy")
        if side != "buy" or not order_no or filled_qty <= 0:
            return
        # pending_orders?먯꽌 order_no 留ㅼ묶
        matched = None
        for order in self.pending_orders:
            if str(order.get("order_no", "")).strip() == str(order_no).strip():
                matched = order
                break
        if not matched:
            log.debug(f"[WS 泥닿껐?듬낫] {ticker} 二쇰Ц踰덊샇={order_no} ??pending 誘몃ℓ移?(?대? 泥섎━??")
            return
        market = matched.get("market", "KR")
        matched["filled_price_native"] = filled_price
        matched["fill_time"] = filled_time
        # broker_pos 援ъ꽦 (WS 泥닿껐媛 湲곗?)
        broker_pos = {
            "ticker": ticker,
            "qty": filled_qty,
            "avg_price": filled_price,
            "eval_price": filled_price,
            "eval_profit": 0.0,
            "profit_rate": 0.0,
        }
        original_qty = int(matched.get("qty", 0) or 0)
        fill_qty_for_position = min(filled_qty, original_qty) if original_qty > 0 else filled_qty
        position_order = dict(matched)
        position_order["qty"] = fill_qty_for_position
        pos = self._make_position_from_broker(position_order, broker_pos)
        self.risk.positions.append(pos)
        partial = original_qty > 0 and filled_qty < original_qty
        if not matched.get("pathb_path_run_id"):
            self._entry_timing_filled(
                market,
                ticker,
                fill_price=float(filled_price),
                order_no=str(order_no or ""),
                qty=int(fill_qty_for_position),
                partial=partial,
            )
        self._v2_record_lifecycle_event(
            "PARTIAL_FILLED" if partial else "FILLED",
            market,
            ticker,
            decision_id=str(matched.get("v2_decision_id", "") or ""),
            execution_id=str(matched.get("v2_execution_id", "") or order_no or ""),
            position_id=str(pos.get("position_id", "") or ""),
            payload={
                "order_no": order_no,
                "qty": int(fill_qty_for_position),
                "original_qty": int(original_qty),
                "remaining_qty": max(0, original_qty - filled_qty),
                "fill_price_native": float(filled_price),
                "source": "websocket_notice",
            },
        )
        if getattr(self, "pathb", None) is not None and matched.get("pathb_path_run_id"):
            try:
                pathb_order = dict(matched)
                pathb_order["qty"] = int(fill_qty_for_position)
                pathb_order["filled_price_native"] = float(filled_price)
                self.pathb.on_buy_fill(pathb_order, position=pos, partial=partial)
            except Exception as _pathb_fill_e:
                log.error(f"[PathB WS fill] {market} {ticker}: {_pathb_fill_e}", exc_info=True)
        self._funnel[market]["filled"] += 1
        # partial fill? 泥닿껐 ?섎웾留?position?쇰줈 留뚮뱾怨??붾웾? TTL ?숈븞 pending ?좎?
        if partial:
            matched["qty"] = max(0, original_qty - filled_qty)
            matched["filled_qty_accum"] = int(matched.get("filled_qty_accum", 0) or 0) + int(fill_qty_for_position)
            matched.setdefault("partial_fill_at", datetime.now(KST).isoformat())
            if self.v2_partial_fill_policy is not None:
                matched["partial_fill_ttl_sec"] = self.v2_partial_fill_policy.ttl_sec(market)
        else:
            self.pending_orders = [o for o in self.pending_orders if o is not matched]
        self._save_pending_orders()
        self._save_positions()
        # ML DB ?낅뜲?댄듃
        if _ML_DB_ENABLED and matched.get("decision_id"):
            try:
                from ml.db_writer import update_filled as _ml_update_filled
                _ml_update_filled(matched["decision_id"], "FILLED")
            except Exception:
                pass
        # ?붾젅洹몃옩 泥닿껐 ?뺤씤 (KR: ??/ US: ?щ윭)
        if market == "US":
            fill_label = f"${filled_price:.4f}"
            log_price  = f"${filled_price:.4f}"
        else:
            fill_label = f"{filled_price:,.0f} KRW"
            log_price  = f"{filled_price:,.0f} KRW"
        fill_confirm_alert(
            market=market,
            ticker=ticker,
            qty=filled_qty,
            order_no=order_no,
            price=filled_price,
            source="WS realtime",
            name=str(matched.get("name", "") or "").strip(),
            usd_krw=self.usd_krw_rate,
            buy_path="path_b" if (matched.get("pathb_path_run_id") or matched.get("path_type") == "claude_price") else "path_a",
        )
        log.info(
            f"[WS 泥닿껐?듬낫] {ticker} {filled_qty}二?@{log_price} "
            f"| 二쇰Ц踰덊샇={order_no} | market={market} | pending?뭚illed ?꾨즺"
        )
    def _entry_scan_interval_sec(self, market: str) -> int:
        real_elapsed = self._market_elapsed_min(market)
        if 0 < real_elapsed <= _ENTRY_SCAN_OPENING_MIN:
            return _ENTRY_SCAN_OPENING_INTERVAL_MIN * 60   # 媛쒖옣: 2遺?(KR/US ?숈씪)
        if market == "US":
            return int(os.getenv("US_ENTRY_SCAN_REGULAR_INTERVAL_MIN", "5")) * 60
        return _ENTRY_SCAN_REGULAR_INTERVAL_MIN * 60
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
            if getattr(self, "pathb", None) is not None:
                self.pathb.scan_exits(market)
            self._write_live_status(market)
        except Exception as _hk_e:
            log.error(f"[housekeeping ?ㅻ쪟] {market}: {_hk_e}", exc_info=True)
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
            f"[{market} 엔트리 스캔] interval={int(interval_sec/60)}분"
            f"(초반 {_ENTRY_SCAN_OPENING_MIN}분={_ENTRY_SCAN_OPENING_INTERVAL_MIN}분 이후={_regular_min}분)"
        )
        try:
            self.run_cycle(market)
        except Exception as _es_e:
            log.error(f"[entry_scan ?ㅻ쪟] {market}: {_es_e}", exc_info=True)
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
            log.info(f"[{market} ?뺤떆 ?ъ뒪?щ━?? {selected}")
        except Exception as e:
            log.info(f"[{market} ?뺤떆 ?ъ뒪?щ━???ㅽ궢] {e}")
    def _get_ohlcv_cached(self, ticker: str, market: str, ttl_min: int = 60):
        """
        ?쇰큺 OHLCV 罹먯떆 ??TTL ???ъ슂泥??앸왂.
        ?먮쫫:
          1. 硫붾え由?罹먯떆(TTL) ???덉쑝硫?利됱떆 諛섑솚
          2. price CSV ?덉쑝硫?濡쒕뱶 (?섏쭛湲곌? 誘몃━ ??ν븳 寃?
          3. ?놁쑝硫?get_daily_ohlcv() on-demand 議고쉶 ??CSV ?곸냽 ???        """
        from pathlib import Path as _Path
        import pandas as _pd
        now = datetime.now(KST).replace(tzinfo=None)
        cached_at = self._ohlcv_cache_time.get(ticker)
        if cached_at and (now - cached_at).total_seconds() < ttl_min * 60:
            return self._ohlcv_cache[ticker]
        # CSV ?뚯씪 寃쎈줈
        _price_base = _Path(__file__).parent / "data" / "price"
        _csv_path = _price_base / market.lower() / f"{market.lower()}_{ticker}.csv"
        if _csv_path.exists():
            try:
                df = _pd.read_csv(_csv_path, parse_dates=["date"])
                df.columns = [c.lower() for c in df.columns]
                df = df.sort_values("date").reset_index(drop=True)
                # CSV媛 ?덈Т ?ㅻ옒?먭굅?????섍? 遺議깊븯硫?on-demand fetch濡?蹂댁셿
                _last = df["date"].max()
                _today = _pd.Timestamp(datetime.now().date())
                usable = 0
                if len(df) >= self._MIN_SIGNAL_ROWS + 60:
                    usable = self._MIN_SIGNAL_ROWS
                elif len(df) >= self._MIN_SIGNAL_ROWS:
                    usable = self._signal_usable_rows(df)
                if (_today - _last).days <= 7 and usable >= self._MIN_SIGNAL_ROWS:
                    self._ohlcv_cache[ticker] = df
                    self._ohlcv_cache_time[ticker] = now
                    return df
                if usable < self._MIN_SIGNAL_ROWS:
                    log.debug(f"[OHLCV] {ticker} CSV {len(df)}??遺議???on-demand 蹂댁셿")
            except Exception:
                pass
        # on-demand 議고쉶
        lookback_days = 365 if market == "KR" else max(260, self._MIN_SIGNAL_ROWS + 120)
        df, usable, source = self._fetch_signal_ready_ohlcv(ticker, market, lookback_days=lookback_days)
        if not df.empty:
            # CSV ?곸냽 ???(?ㅼ쓬 ?ㅽ뻾遺???뚯씪?먯꽌 ?쎌쓬)
            try:
                _csv_path.parent.mkdir(parents=True, exist_ok=True)
                if _csv_path.exists():
                    existing = _pd.read_csv(_csv_path, parse_dates=["date"])
                    existing.columns = [c.lower() for c in existing.columns]
                    df = self._merge_ohlcv_frames(existing, df)
                df.to_csv(_csv_path, index=False, encoding="utf-8-sig")
                log.info(f"[OHLCV on-demand save] {market} {ticker} rows={len(df)}")
            except Exception as _e:
                log.debug(f"[OHLCV CSV ????ㅽ뙣] {ticker}: {_e}")
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
        if self._check_market_halt(
            market,
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
        # Wait briefly after session_open completes before run_cycle starts.
        _since_open = time.time() - self._session_open_at.get(market, 0.0)
        _guard_sec = float(self._session_startup_guard_sec.get(market, _STARTUP_GUARD_SEC) or 0.0)
        if _since_open < _guard_sec:
            _log_flow(
                "info",
                f"[{market}] startup guard ??session_open ??{_since_open:.0f}s 寃쎄낵 "
                f"(理쒖냼 {_guard_sec:.0f}s ?湲?以? ??cycle ?ㅽ궢"
            )
            self._leave_market_task(market, "run_cycle")
            return
        # ?? ?μ쟾 SELL ?덉빟 ??泥섎━ (startup guard ?댁젣 吏곹썑 1?? ?????????????
        sell_queue = self._pre_session_sell_queue.get(market, [])
        if sell_queue:
            if self._has_broker_sync_risk(market):
                _log_risk("warning", f"[?μ쟾 SELL ?? {market} 釉뚮줈而??숆린??遺덉떊 ?곹깭 ???덉빟 留ㅻ룄 ?꾨? 蹂대쪟")
                self._pre_session_sell_queue[market] = []
            else:
                _log_flow("info", f"[?μ쟾 SELL ?? {market} {len(sell_queue)}嫄?留ㅻ룄 ?ㅽ뻾")
                self._pre_session_sell_queue[market] = []
                for _sq_pos in sell_queue:
                    _sq_tk = _sq_pos.get("ticker", "")
                    try:
                        # ???앹꽦 ?쒖젏??current_price??援ы솚??湲곗??????덉쑝誘濡?                        # ?ㅼ젣 ?ㅽ뻾 ?쒖젏??price_cache(理쒖떊 ?섏쑉 諛섏쁺) ?곗꽑 ?ъ슜
                        _sq_cp = (
                            self.price_cache.get(_sq_tk)
                            or _sq_pos.get("current_price")
                            or _sq_pos.get("entry", 0)
                        )
                        cand = {**_sq_pos, "exit_price": _sq_cp, "reason": "pre_session_sell"}
                        self._execute_sell(cand, market, reason="pre_session_sell",
                                           hold_advice=_sq_pos.get("hold_advice"))
                    except Exception as _sqe:
                        log.error(f"[?μ쟾 SELL ?ㅽ뙣] {_sq_tk}: {_sqe}")
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
        # Common ML DB context reused inside the ticker loop.
        _ml_judgments = self.today_judgment.get("judgments", {})
        _ml_ca_ctx    = self.today_judgment.get("digest_raw", {}).get("context", {})
        def _ml_base_row(ticker_, price_, sig_row_) -> dict:
            """醫낅ぉ蹂?ML row 怨듯넻 ?꾨뱶 議곕┰"""
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
                "session_date": self._current_session_date_str(market),
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
        if getattr(self, "pathb", None) is not None:
            try:
                self.pathb.scan_waiting_entries(market)
                self.pathb.scan_exits(market)
            except Exception as _pathb_cycle_e:
                log.error(f"[PathB cycle] {market}: {_pathb_cycle_e}", exc_info=True)
        now_str = datetime.now(KST).strftime("%H:%M")
        log.info(
            f"[{market} ?ъ씠??{now_str}] 紐⑤뱶:{mode} size:{size_pct}% | "
            f"tickers:{tickers} | positions:{len(self.risk.positions)}"
        )
        # 遺꾩꽍媛 ?됯퇏 confidence 怨꾩궛 ???좊ː????쑝硫??좉퇋 吏꾩엯 李⑤떒
        log.info(f"[runtime gates {market}] source=run_cycle {self._runtime_gate_state_text(market)}")
        _judgments = self.today_judgment.get("judgments", {})
        _avg_conf = (
            sum(_judgments.get(a, {}).get("confidence", 0.5) for a in ("bull", "bear", "neutral")) / 3.0
            if _judgments else 0.5
        )
        _low_conf = _avg_conf < _MIN_ENTRY_CONF
        if _low_conf:
            log.info(f"[{market}] ??? 遺꾩꽍媛 confidence ({_avg_conf:.2f} < {_MIN_ENTRY_CONF}) ???좉퇋 吏꾩엯 ???ъ씠???ㅽ궢")
        _ca_context = dict(self.today_judgment.get("digest_raw", {}).get("context", {}))
        # US ?꾩슜: VIX 30遺꾨쭏???μ쨷 媛깆떊 (寃쎄퀎 吏꾨룞 諛⑹? ?꾪빐 VIX留?媛깆떊)
        _VIX_REFRESH_INTERVAL = 1800
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
                        log.info(f"[VIX 媛깆떊] {_vix_prev:.1f} ??{_vix_live:.1f}")
                except Exception as _ve:
                    log.debug(f"[VIX 媛깆떊 ?ㅽ뙣] {_ve}")
        self._ca_context_last = _ca_context   # param_tuner媛 李몄“
        _fear_label = get_vix_regime(_ca_context, market)
        if _ca_context:
            log.debug(f"[{market}] cross-asset 蹂댁젙 ?곸슜 | {_fear_label} | "
                      f"USD/KRW {_ca_context.get('usd_krw', 0):,.0f}")
        _voted_strat = _analyst_strategy_vote(_judgments)
        # KR ?꾨왂 ?곗꽑?쒖쐞: 遺꾩꽍媛 ?ㅼ닔寃??꾨왂??留??욎쑝濡??щ┛ ??湲곕낯 ?쒖꽌 ?좎?.
        # continuation? 媛쒖옣 珥덈컲 ?덉쇅 ?좏샇??湲곕낯 ?쒖꽌???ｌ? ?딄퀬 蹂꾨룄 議곌굔?쇰줈留??덉슜?쒕떎.
        if market == "KR":
            _kr_base = [
                "opening_range_pullback",
                "gap_pullback",
                "momentum",
                "mean_reversion",
            ]
            if _voted_strat and _voted_strat in _kr_base:
                _kr_strat_list = [_voted_strat] + [s for s in _kr_base if s != _voted_strat]
            else:
                _kr_strat_list = _kr_base
            log.debug(f"[KR ?꾨왂] ?ы몴={_voted_strat} ?쒕룄?쒖꽌={_kr_strat_list}")
        else:
            _kr_strat_list = []
        # US ?꾨왂 ?곗꽑?쒖쐞: 遺꾩꽍媛 ?ы몴 ?꾨왂 ??mean_reversion ??gap_pullback
        # momentum/VB 紐⑤몢 disabled?대?濡?base??mean_reversion, gap_pullback 怨좎젙
        if market == "US":
            _us_base = ["opening_range_pullback", "mean_reversion", "gap_pullback"]
            if _voted_strat and _voted_strat not in _us_base:
                _us_strat_list = [_voted_strat] + _us_base
            elif _voted_strat:
                _us_strat_list = [_voted_strat] + [s for s in _us_base if s != _voted_strat]
            else:
                _us_strat_list = _us_base
            log.debug(f"[US ?꾨왂] ?ы몴={_voted_strat} ?쒕룄?쒖꽌={_us_strat_list}")
        else:
            _us_strat_list = []
        _pending_signals: list[dict] = []   # ?ъ씠?????좏샇 ?섏쭛 ???뺣젹 ???ㅽ뻾
        for ticker in tickers:
            try:
                # ?몄뀡 ??二쇰Ц 李⑤떒 醫낅ぉ ?ㅽ궢 (KR/US 怨듯넻 ??留ㅻℓ遺덇?/3?뚯뿰??500?ㅻ쪟)
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
                    log.info(f"[二쇰Ц李⑤떒 {market}] {ticker} ??{_order_blk}")
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
                            f"[skip {market}] {ticker} invalid price {cnt}???곗냽 "
                            f"??醫낅ぉ 援먯껜"
                        )
                        universe = self.today_judgment.get("universe_tickers", [])
                        current = self.today_tickers.get(market, [])
                        replacement = next(
                            (t for t in universe if t not in current and t != ticker), None
                        )
                        if replacement:
                            new_tickers = [replacement if t == ticker else t for t in current]
                            self.today_tickers[market] = new_tickers
                            self._entry_timing_mark_candidates(market, [replacement], "inline_replacement")
                            self.today_judgment["tickers"] = new_tickers
                            self._invalid_price_count.pop(ticker, None)
                            log.info(f"[醫낅ぉ 援먯껜] {market} {ticker} ??{replacement}")
                            _now_inv = datetime.now(KST).replace(tzinfo=None)
                            self._ticker_exclude_log.setdefault(market, []).append(
                                {"ticker": ticker, "reason": "invalid_price", "ts": _now_inv}
                            )
                        else:
                            log.warning(f"[醫낅ぉ 援먯껜 ?ㅽ뙣] {market} {ticker} ???泥??꾨낫 ?놁쓬")
                    else:
                        log.warning(f"[skip {market}] {ticker} invalid price={price} ({cnt}/{_MAX_INVALID}??")
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
                # ?댁쟾 ?뺤긽媛 ?鍮?30% 珥덇낵 愿대━ ??KIS API outlier 諛⑹뼱
                _prev_price = self.price_cache_raw.get(ticker, 0)
                if _prev_price > 0 and abs(price - _prev_price) / _prev_price > 0.30:
                    # KR: 1???ъ“???쒕룄 ??API ?몄씠利?vs ?ㅼ젣 湲됰벑 援щ텇
                    if market == "KR":
                        try:
                            _retry_info = get_price(ticker, self.token, market=market)
                            _retry_px = _retry_info["price"]
                            if _retry_px > 0 and abs(_retry_px - _prev_price) / _prev_price <= 0.30:
                                # ?ъ“??寃곌낵媛 ?뺤긽 ??泥?議고쉶媛 ?ㅻ쪟??? ?뺤긽 媛寃??ъ슜
                                log.info(
                                    f"[outlier_retry OK {market}] {ticker} "
                                    f"first={price:,} retry={_retry_px:,} ???뺤긽媛 ?ъ슜"
                                )
                                price = _retry_px
                                price_info = _retry_info
                                risk_price = self._price_to_krw(price, market)
                                self.price_cache_raw[ticker] = price
                                self.price_cache[ticker] = risk_price
                                self._invalid_price_count.pop(ticker, None)
                                # outlier ?꾨떂?쇰줈 泥섎━ ???꾨옒 skip 釉붾줉 嫄대꼫?
                                # (continue瑜?_outer if ?덉뿉???몄텧?????놁쑝誘濡??뚮옒洹??ъ슜)
                                _outlier_confirmed = False
                            else:
                                log.warning(
                                    f"[outlier_retry FAIL {market}] {ticker} "
                                    f"retry={_retry_px:,} ??愿대━ ???댁긽移??뺤젙"
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
                            f"愿대━ {abs(price-_prev_price)/_prev_price*100:.1f}% ???ㅽ궢 ({cnt}/{_MAX_OUTLIER}??"
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
                                self._entry_timing_mark_candidates(market, [_replacement], "inline_replacement")
                                self.today_judgment["tickers"] = _new_tickers
                                self._invalid_price_count.pop(ticker, None)
                                log.info(f"[醫낅ぉ 援먯껜] {market} {ticker} ??{_replacement} (outlier {cnt}??")
                                _now_out = datetime.now(KST).replace(tzinfo=None)
                                self._ticker_exclude_log.setdefault(market, []).append(
                                    {"ticker": ticker, "reason": "outlier_price", "ts": _now_out}
                                )
                            else:
                                log.warning(f"[醫낅ぉ 援먯껜 ?ㅽ뙣] {market} {ticker} ???泥??꾨낫 ?놁쓬")
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
                self._invalid_price_count.pop(ticker, None)  # ?뺤긽 媛寃??섏떊 ??移댁슫??由ъ뀑
                self.price_cache_raw[ticker] = price
                self.price_cache[ticker] = risk_price
                self.risk.update_prices(self.price_cache, self.price_cache_raw)
                # US??VTS?먯꽌 WS ?쒖꽭 誘몄궗??/ ?ㅼ쟾?먯꽌??WS ?깆쑝濡?price_cache ?낅뜲?댄듃??                # ??寃쎌슦 紐⑤몢 scan ??polling 媛寃⑹쑝濡?OR 洹쇱궗 ?꾩쟻 (?ㅼ쟾 WS ?깆? _on_tick?먯꽌 蹂꾨룄 ?꾩쟻)
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
                    log.debug(f"  [{ticker}] HALT 紐⑤뱶 ???좉퇋吏꾩엯 ?ㅽ궢")
                    continue
                _norm_trade_ready = self._trade_ready_set(market)
                _watch_bucket = "TRADE_READY"
                _watch_only_reason = ""
                _soft_watch_candidate = False
                if not self._is_trade_ready_ticker(market, ticker):
                    _watch_bucket = self._watch_only_bucket(market, ticker)
                    _watch_only_reason = self._watch_only_reason_text(market, ticker)
                    if self._can_recheck_soft_watch_only(market, ticker, mode):
                        _soft_watch_candidate = True
                    else:
                        analysis_log.info(
                            f"[skip {market}] {ticker} watch_only",
                            extra={"extra": {
                                "event": "entry_skip",
                                "market": market,
                                "ticker": ticker,
                                "reason": "watch_only",
                                "price": float(price),
                                "mode": mode,
                                "detail": _watch_only_reason,
                                "select_reason": _watch_only_reason,
                                "watch_status": f"WATCH_ONLY_{_watch_bucket}",
                                "watch_bucket": _watch_bucket,
                                "trade_ready": list(_norm_trade_ready),
                            }},
                        )
                        if _ML_DB_ENABLED:
                            _ml_write_eval(
                                ticker, price, {}, "SKIPPED",
                                block_reason_="watch_only",
                                diag_json_={
                                    "trade_ready": list(_norm_trade_ready),
                                    "watch_only_reason": _watch_only_reason,
                                    "watch_bucket": _watch_bucket,
                                },
                            )
                        log.debug(
                            f"  [{ticker}] WATCH_ONLY({ _watch_bucket }) ??trade_ready ?꾨떂 ???좉퇋吏꾩엯 ?ㅽ궢"
                        )
                        continue
                # ?몃쾭??ETF???쎌꽭(MILD_BEAR ?댄븯) 紐⑤뱶?먯꽌留?留ㅼ닔 ?덉슜
                _INVERSE = {"SQQQ", "114800"}
                _BEAR_ONLY_MODES = {"MILD_BEAR", "CAUTIOUS_BEAR", "DEFENSIVE", "HALT"}
                if ticker in _INVERSE and mode not in _BEAR_ONLY_MODES:
                    log.debug(f"  [{ticker}] ?몃쾭??ETF ???쎌꽭 紐⑤뱶 ?꾨떂({mode}) ???ㅽ궢")
                    continue
                if self._in_entry_blackout(market):
                    self._bump_runtime_reason(market, ticker, "entry_blackout")
                    log.debug(f"  [{ticker}] 釉붾옓?꾩썐 ?쒓컙 ???ㅽ궢")
                    if _ML_DB_ENABLED:
                        _ml_write_eval(ticker, price, {}, "SKIPPED",
                                       block_reason_="entry_blackout")
                    continue
                if self._is_entry_blocked(ticker):
                    log.debug(f"  [{ticker}] 진입 차단 중(쿨다운)")
                    continue
                if self._has_same_day_trade(ticker, market):
                    self._bump_runtime_reason(market, ticker, "same_day_reentry_blocked")
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
                    log.debug(f"  [{ticker}] ?뱀씪 ?숈씪 醫낅ぉ 泥닿껐 ?대젰 ?덉쓬 ???ъ쭊???ㅽ궢")
                    continue
                if _low_conf:
                    if _ML_DB_ENABLED:
                        _ml_write_eval(
                            ticker, price, {}, "SKIPPED",
                            block_reason_="low_confidence",
                            diag_json_={"avg_conf": round(_avg_conf, 4), "min_conf": _MIN_ENTRY_CONF},
                        )
                    log.debug(f"  [{ticker}] confidence 遺議?({_avg_conf:.2f} < {_MIN_ENTRY_CONF}) ???좉퇋 吏꾩엯 ?ㅽ궢")
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
                    log.debug(f"  [{ticker}] 釉뚮줈而??고???蹂댁쑀以????ъ쭊???ㅽ궢")
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
                    log.debug(f"  [{ticker}] 吏꾩엯遺덇?: {reason}")
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
                    log.debug(f"  [{ticker}] 誘몄껜寃?二쇰Ц 議댁옱 ???ъ＜臾??ㅽ궢")
                    continue
                # ?쒖옣蹂??덉궛 珥덇낵 ?뺤씤
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
                    log.debug(f"  [{ticker}] ?덉궛 ?뚯쭊 (?붿뿬 {avail:,.0f}??< {risk_price:,.0f}?? ???ㅽ궢")
                    continue
                candles = self._get_ohlcv_cached(ticker, market)
                if candles.empty:
                    log.debug(f"  [{ticker}] 罹붾뱾 ?놁쓬")
                    continue
                # ?? ?μ쨷 ?꾩떆 ?뱀씪遊?二쇱엯 (KR/US 怨듯넻) ???????????????????
                # CSV 留덉?留??됱? ?댁젣 醫낃? 湲곗?. ?μ쨷?먮뒗 ?ㅻ뒛 price_info(open/
                # high/low/close/volume)瑜??꾩떆 罹붾뱾濡?異붽???吏?쒕? ?뱀씪 湲곗??쇰줈
                # ?ш퀎?? ??留덇컧 ??price_collector媛 ?ㅼ젣 ?쇰큺??CSV????ν븯硫?                # ?ㅼ쓬 TTL 媛깆떊 ???먮룞?쇰줈 ?ㅼ젣 遊됱쑝濡??泥대맖.
                # US??KST ?먯젙???섏뼱??ET 湲곗? ?좎쭨瑜??ъ슜???щ컮瑜?嫄곕옒??諛섏쁺.
                _vol_missing = False   # today-bar 二쇱엯 ??珥덇린????二쇱엯 釉붾줉 ?덉뿉??True濡???뼱?
                if self.session_active:
                    import pandas as _pd2
                    _ET = ZoneInfo("America/New_York")
                    _today_ts = _pd2.Timestamp(
                        datetime.now(KST).date() if market == "KR"
                        else datetime.now(_ET).date()
                    )
                    _last_date = candles["date"].max() if not candles.empty else _pd2.Timestamp("2000-01-01")
                    # CSV???ㅻ뒛 ?좎쭨媛 ?덉뼱??OHLC ?꾨? ?숈씪(pre-open 遺덉셿???곗씠???대㈃
                    # Replace incomplete same-day pre-open row with realtime data.
                    if _last_date == _today_ts and not candles.empty:
                        _lr = candles.iloc[-1]
                        if float(_lr.get("open", 0)) == float(_lr.get("high", 0)) == float(_lr.get("low", 0)):
                            candles = candles.iloc[:-1].copy()
                            _last_date = candles["date"].max() if not candles.empty else _pd2.Timestamp("2000-01-01")
                            log.debug(f"[same-day replacement {market}] {ticker} removed pre-open CSV, using realtime injection")
                        else:
                            # ?ㅻ뒛 遊됱씠 ?대? CSV???덉쓬(price_collector ?좉린濡? ??live vol ?꾨씫 ?щ???蹂꾨룄 ?뺤씤
                            if market == "US":
                                _pi_vol_chk = float(price_info.get("volume", 0) or 0)
                                if _pi_vol_chk == 0.0 or _pi_vol_chk == 1.0:
                                    _vol_missing = True
                    if _last_date < _today_ts and price_info.get("price", 0) > 0:
                        _p = price_info["price"]
                        _o = price_info.get("open",   _p)
                        _h = price_info.get("high",   _p)
                        _l = price_info.get("low",    _p)
                        # WS tick ?꾩쟻 怨좉?/?媛濡?蹂댁젙 ??KIS API媛 intraday H/L 誘몄젣怨????덉텧
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
                            log.debug(f"[?뱀씪遊?二쇱엯 ?ㅽ궢 {market}] {ticker} ?⑥씪媛遊?媛?뾾?????쒖쇅")
                        else:
                            if _is_single_price and _has_gap:
                                log.debug(
                                    f"[?뱀씪遊?媛?＜??{market}] {ticker} ?⑥씪媛遊됱씠??媛??덉쓬"
                                    f" prev_close={_prev_close_val} open={_o} gap_pct=unknown"
                                )
                            _raw_vol = float(price_info.get("volume", 0) or 0)
                            # For US paper data, replace missing volume with a historical estimate.
                            _vol_missing = (
                                market == "US" and (_raw_vol == 0.0 or _raw_vol == 1.0)
                            )
                            if _vol_missing:
                                # 理쒓렐 20??hist vol ?됯퇏?쇰줈 異붿젙 (?몄뀡 吏꾪뻾瑜?諛섏쁺)
                                _hist_vols = candles["volume"].replace(0, float("nan")).dropna()
                                _hist_avg = float(_hist_vols.tail(20).mean()) if len(_hist_vols) >= 5 else 0.0
                                _sess_prog = self._intraday_session_progress(market)
                                # ?몄뀡 吏꾪뻾瑜?횞 hist_avg ???꾩옱源뚯? ?볦???嫄곕옒??異붿젙 (?섑븳 20%)
                                _fallback_vol = _hist_avg * max(0.20, min(1.0, _sess_prog))
                                log.debug(
                                    f"[vol_fallback US] {ticker} raw_vol={_raw_vol:.0f}"
                                    f" ??hist_avg={_hist_avg:,.0f} 횞 prog={_sess_prog:.2f}"
                                    f" = {_fallback_vol:,.0f}"
                                )
                                _raw_vol = _fallback_vol
                                # ?쇰꼸: volume_state=missing ?꾩쟻
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
                                f"[?뱀씪遊?二쇱엯 {market}] {ticker} "
                                f"close={_p} raw_vol={_raw_vol:,.0f} proj_vol={(_proj_vol or _raw_vol):,.0f}"
                                + (" [vol_fallback]" if _vol_missing else "")
                            )
                sig_df = calc_all(candles)
                if sig_df.empty or len(sig_df) < self._MIN_SIGNAL_ROWS:
                    log.warning(f"  [{ticker}] ?좏샇怨꾩궛 遺덇? ???좏슚??{len(sig_df)}媛?(理쒖냼 {self._MIN_SIGNAL_ROWS})")
                    continue
                i = len(sig_df) - 1
                self._entry_timing_signal_check(market, ticker, float(price))
                signal_fired = False
                strategy_name = ""
                params = {}
                kr_momentum_diag = None
                none_detail = ""
                _intraday_log_row_id = 0
                def _ca(p):
                    return apply_cross_asset_adjust(p, _ca_context, market, ticker, mode)
                def _ap(strat: str) -> dict:
                    """adaptive_params + cross-asset + Claude 寃???듯빀 ?ы띁."""
                    # param_tuner 罹먯떆 ?곗꽑 (罹먯떆 ?놁쑝硫?adaptive_params 湲곕낯)
                    _pt_cache = _param_tuner._cache.get(market, {})
                    if strat in _pt_cache and not _pt_cache[strat].get("disabled"):
                        p = _ca(dict(_pt_cache[strat]))
                    else:
                        p = _ca(_adaptive_params(strat, market, mode, _avg_conf,
                                                context=_ca_context))
                    # ?μ큹諛?opening window ?먮떒????gap_pullback.signal()???ъ슜
                    p["session_elapsed_min"] = self._market_elapsed_min(market)
                    if strat == "opening_range_pullback":
                        _or_high = float(self._or_high.get(ticker, 0.0) or 0.0)
                        _or_low_raw = self._or_low.get(ticker, float("inf"))
                        _or_low = 0.0 if _or_low_raw == float("inf") else float(_or_low_raw or 0.0)
                        p["or_high"] = _or_high
                        p["or_low"] = _or_low
                        p["or_formed"] = bool(self._or_formed.get(ticker, False))
                    return p
                _recommended_strategy = self._recommended_strategy_for_ticker(market, ticker)
                _ticker_kr_strat_list = self._prioritize_strategy_order(market, ticker, _kr_strat_list)
                _ticker_us_strat_list = self._prioritize_strategy_order(market, ticker, _us_strat_list)
                if _recommended_strategy:
                    _selected_order = _ticker_kr_strat_list if market == "KR" else _ticker_us_strat_list
                    log.debug(
                        f"[selection_meta] {market} {ticker} recommended_strategy={_recommended_strategy} "
                        f"order={_selected_order}"
                    )
                def _orp_detail(_df, _i, _p):
                    if _p.get("disabled"):
                        return f"OR?뚮┝: ?꾨왂 鍮꾪솢??{market} {mode})"
                    _elapsed = float(_p.get("session_elapsed_min", 999) or 999)
                    _or_minutes = float(_p.get("or_minutes", 10) or 10)
                    _entry_window = float(_p.get("entry_window_min", 60) or 60)
                    _or_formed = bool(_p.get("or_formed", False))
                    _or_high = float(_p.get("or_high", 0.0) or 0.0)
                    _or_low = float(_p.get("or_low", 0.0) or 0.0)
                    if not _or_formed:
                        if _elapsed <= _or_minutes:
                            return f"OR?뚮┝: OR ?뺤꽦以?{int(_elapsed)}遺?{int(_or_minutes)}遺?"
                        return "OR pullback: OR not formed"
                    if _elapsed > (_or_minutes + _entry_window):
                        return f"OR?뚮┝: 吏꾩엯李?醫낅즺({int(_elapsed)}遺?"
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
                    return f"OR pullback: range={_or_range_pct*100:.2f}% price={_close:.2f} vol={_vol_ratio:.2f}"
                def _gap_detail(_df, _i, _p):
                    if _p.get("disabled"):
                        return f"媛?닃由? ?꾨왂 鍮꾪솢??{market} {mode})"
                    if _i < 5:
                        return "gap_pullback: data insufficient"
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
                    _o = float(_row.get("open", 0) or 0)
                    _h = float(_row.get("high", 0) or 0)
                    _l = float(_row.get("low", 0) or 0)
                    _c = float(_row.get("close", 0) or 0)
                    _pb_min = float(_p.get("opening_pullback_min_pct", 0.006) if _in_opening
                                    else _p.get("pullback_min_pct", 0.010))
                    _pb_max = float(_p.get("opening_pullback_max_pct", 0.050) if _in_opening
                                    else _p.get("pullback_max_pct", 0.040))
                    _pb_depth = ((_h - _l) / _h) if _h > 0 else 0.0
                    _open_dd = ((_o - _l) / _o) if _o > 0 and _l < _o else 0.0
                    _open_dd_max = float(_p.get("open_drawdown_max_pct", 0.025))
                    _recovered = (
                        _l > 0
                        and _c >= _l * (1 + float(_p.get("recovery_min_pct", 0.003)))
                        and _c >= _o * (1 - float(_p.get("open_reclaim_buffer_pct", 0.003)))
                    )
                    _pullback = _pb_min <= _pb_depth <= _pb_max and _open_dd <= _open_dd_max and _recovered
                    _prefix = "gap_pullback(open)" if _in_opening else "gap_pullback"
                    return f"{_prefix}: gap={_gap*100:.2f}% vol={_vol_ratio:.2f} pullback={_pb_depth*100:.2f}% recovered={_recovered}"
                def _mr_detail(_df, _i, _p):
                    if _p.get("disabled"):
                        return f"?됯퇏?뚭?: ?꾨왂 鍮꾪솢??{market} {mode})"
                    if _i < 20:
                        return "mean_reversion: data insufficient"
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
                    return f"mean_reversion: RSI={_rsi:.1f} BB={_bb:.1f} vol={_vol_ratio:.2f} ma60_ok={_ma_ok}"
                def _vb_detail(_df, _i, _p):
                    if _p.get("disabled"):
                        return f"蹂?숈꽦?뚰뙆: ?꾨왂 鍮꾪솢??{market} {mode})"
                    if _i < 5:
                        return "volatility_breakout: data insufficient"
                    _row = _df.iloc[_i]
                    _prev = _df.iloc[_i - 1]
                    _target = float(_prev.get("open", 0) or 0) + (
                        float(_prev.get("high", 0) or 0) - float(_prev.get("low", 0) or 0)
                    ) * float(_p.get("k", 0.45))
                    _close = float(_row.get("close", 0) or 0)
                    _vol_ratio = float(_row.get("vol_ratio", 1) or 1)
                    _vol_mult = float(_p.get("vol_mult", 2.0))
                    return f"volatility_breakout: close={_close:.2f} target={_target:.2f} vol={_vol_ratio:.2f}"
                def _classify_rejection(detail: str, volume_missing_detected: bool = False) -> tuple[str, str]:
                    """
                    none_detail 臾몄옄?댁뿉??rejection_reason / volume_state 遺꾨쪟.
                    Returns: (rejection_reason, volume_state)
                    - rejection_reason: or_forming | momentum_wait | data_missing | gap_insufficient
                                        | volume_low | pullback_missing | highpoint_missing
                                        | strategy_disabled | signal_not_met
                    - volume_state: missing | low | ok | unknown
                    ?곗꽑?쒖쐞 (?믪? ??:
                      1. or_forming        ??OR ?꾩쭅 ?뺤꽦 以?(?쒓컙 臾몄젣, ?湲??꾩슂)
                      2. momentum_wait     ??momentum ?쒓컙 寃뚯씠??誘명넻怨?(?뺤긽 ?湲?
                      3. data_missing      ??vol=0/1 ???곗씠???꾨씫
                      4. gap_insufficient  ??媛?議곌굔 誘몄땐議?                      5. volume_low        ??嫄곕옒??遺議?                      6. pullback_missing  ???뚮┝ 議곌굔 誘몄땐議?                      7. highpoint_missing ???좉퀬媛/異붿꽭 遺議?                      8. strategy_disabled ??VB ???꾨왂 鍮꾪솢??(理쒗븯??????긽 ?④퍡 諛쒖깮)
                      9. signal_not_met    ??湲고? 誘몄땐議?                    """
                    import re as _re
                    reasons = []
                    vol_state = "unknown"
                    # 1. OR ?뺤꽦以????꾩쭅 OR 誘몄셿??(?쒓컙 寃뚯씠?? 媛??紐낇솗???먯씤)
                    if "OR active" in detail:
                        reasons.append("or_forming")
                    # 2. Momentum wait-window gate.
                    if "momentum_wait_window" in detail:
                        reasons.append("momentum_wait")
                    # 3. vol ?곗씠???꾨씫 (?몃? 二쇱엯 ?뚮옒洹??곗꽑)
                    if volume_missing_detected:
                        vol_state = "missing"
                        reasons.append("data_missing")
                    # 嫄곕옒??遺꾩꽍 ??媛?닃由?OR?뚮┝/蹂??怨듯넻 ?⑦꽩 + ?곗냽吏꾩엯 ?꾩슜 ?⑦꽩 紐⑤몢 ?ㅼ틪
                    # ?꾨왂 ?섎굹?쇰룄 vol=?쀬씠硫?volume_low, ?꾨왂 vol=0/1?대㈃ data_missing
                    _vol_fail = False   # ?대뒓 ?꾨왂?대뱺 vol 誘몃떖
                    _vol_found = False  # ?대뒓 ?꾨왂?대뱺 vol ?⑦꽩 諛쒓껄
                    # vol ratio classification - ASCII-only patterns (mojibake regex removed).
                    # Detail format: "gap_pullback: ... vol=0.85 ..." or "mean_reversion: ... vol=1.2 ..."
                    _vm = _re.search(r"vol=([0-9.]+)", detail)
                    if _vm:
                        _vv = float(_vm.group(1))
                        _vol_found = True
                        if not volume_missing_detected and (_vv == 0.0 or _vv == 1.0):
                            vol_state = "missing"
                            if "data_missing" not in reasons:
                                reasons.append("data_missing")
                    # Final volume classification unless missing was already determined.
                    if not volume_missing_detected and vol_state != "missing":
                        if _vol_fail:
                            vol_state = "low"
                            if "volume_low" not in reasons:
                                reasons.append("volume_low")  # 5.
                        elif _vol_found:
                            vol_state = "ok"
                    # 4. Gap condition not met.
                    _gap_m = _re.search(r"gap=([0-9.-]+)%", detail)
                    if _gap_m and "gap_ok=False" in detail:
                        reasons.append("gap_insufficient")
                    # 6. Pullback condition not met.
                    if "pullback_ok=False" in detail or "pullback=False" in detail:
                        reasons.append("pullback_missing")
                    # 7. Highpoint/trend condition not met.
                    if "high_ok=False" in detail or "high=False" in detail:
                        reasons.append("highpoint_missing")
                    # 8. ?꾨왂 鍮꾪솢??(VB ????理쒗븯?? ??긽 none_detail???ы븿?????덉쓬)
                    if "strategy disabled" in detail:
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
                            session_date=self._current_session_date_str(market),
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
                    gap_p = _ap("gap_pullback")
                    mom_p = _ap("momentum")
                    mr_p = _ap("mean_reversion")
                    vb_p = _ap("volatility_breakout")
                    _kr_dispatch = {
                        "opening_range_pullback": (orp_sig, orp_p),
                        "gap_pullback": (gap_sig, gap_p),
                        "momentum": (mom_sig, mom_p),
                        "mean_reversion": (mr_sig, mr_p),
                        "volatility_breakout": (vb_sig, vb_p),
                    }
                    _cont_window_max = self._effective_momentum_wait_window(
                        market,
                        float(_ap("continuation").get("cont_entry_max_min", 45) or 45),
                    )
                    for _strat_name in _ticker_kr_strat_list:
                        _sig_fn, _p = _kr_dispatch[_strat_name]
                        if _strat_name == "momentum":
                            _momentum_ready = self._market_elapsed_min(market) >= _cont_window_max
                            if _momentum_ready:
                                kr_momentum_diag = mom_diag(sig_df, i, mom_p)
                            if not _momentum_ready:
                                continue
                        if _sig_fn(sig_df, i, _p):
                            signal_fired = True
                            strategy_name = _strat_name
                            params = _p
                            break
                    # continuation entry after other strategy checks fail.
                    if (
                        self.enable_continuation_live
                        and not signal_fired
                        and not self._continuation_used.get(ticker, False)
                    ):
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
                    elif not signal_fired:
                        _cont_p = _ap("continuation")
                        if cont_sig(sig_df, i, _cont_p):
                            self._bump_runtime_reason(market, ticker, "continuation_shadow_only")
                            analysis_log.info(
                                f"[shadow {market}] {ticker} continuation",
                                extra={"extra": {
                                    "event": "shadow_signal",
                                    "market": market,
                                    "ticker": ticker,
                                    "strategy": "continuation",
                                    "reason": "continuation_shadow_only",
                                    "mode": mode,
                                }},
                            )
                    if not signal_fired:
                        orp_p = locals().get("orp_p") or _ap("opening_range_pullback")
                        gap_p = locals().get("gap_p") or _ap("gap_pullback")
                        mom_p = locals().get("mom_p") or _ap("momentum")
                        mr_p = locals().get("mr_p") or _ap("mean_reversion")
                        vb_p = locals().get("vb_p") or _ap("volatility_breakout")
                        _cont_p = locals().get("_cont_p") or _ap("continuation")
                        _kr_md = locals().get("kr_momentum_diag") or {}
                        _cont_window_max = self._effective_momentum_wait_window(
                            market,
                            float(_cont_p.get("cont_entry_max_min", 45) or 45),
                        )
                        _elapsed_min = self._market_elapsed_min(market)
                        _kr_mom_str = "momentum disabled" if mom_p.get("disabled") else "momentum diagnostic"
                        _cont_d = cont_diag(sig_df, i, _cont_p)
                        if not mom_p.get("disabled") and _elapsed_min < _cont_window_max:
                            _kr_mom_str = f"momentum_wait_window({_elapsed_min:.0f}m<{_cont_window_max:.0f}m)"
                        _cont_str = "continuation disabled" if _cont_p.get("disabled") else "continuation diagnostic"
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
                    # US: 遺꾩꽍媛 ?ы몴 ?꾨왂 ?곗꽑, volatility_breakout ?대갚
                    _strat_dispatch = {
                        "opening_range_pullback": (orp_sig, _us_orp_p),
                        "momentum":           (mom_sig,  _ap("momentum")),
                        "mean_reversion":     (mr_sig,   _ap("mean_reversion")),
                        "gap_pullback":       (gap_sig,  _ap("gap_pullback")),
                        "volatility_breakout":(vb_sig,   _ap("volatility_breakout")),
                    }
                    for _strat_name in _ticker_us_strat_list:
                        _sig_fn, _p = _strat_dispatch.get(
                            _strat_name, (vb_sig, _ap("volatility_breakout"))
                        )
                        if (
                            _strat_name == "momentum"
                            and self._market_elapsed_min(market)
                            < self._effective_momentum_wait_window(
                                market,
                                float(_ap("continuation").get("cont_entry_max_min", 45) or 45),
                            )
                        ):
                            continue
                        if _sig_fn(sig_df, i, _p):
                            signal_fired = True
                            strategy_name = _strat_name
                            params = _p
                            break
                    if (
                        self.enable_continuation_live
                        and not signal_fired
                        and not self._continuation_used.get(ticker, False)
                    ):
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
                    elif not signal_fired:
                        _us_cont_p = _ap("continuation")
                        if cont_sig(sig_df, i, _us_cont_p):
                            self._bump_runtime_reason(market, ticker, "continuation_shadow_only")
                            analysis_log.info(
                                f"[shadow {market}] {ticker} continuation",
                                extra={"extra": {
                                    "event": "shadow_signal",
                                    "market": market,
                                    "ticker": ticker,
                                    "strategy": "continuation",
                                    "reason": "continuation_shadow_only",
                                    "mode": mode,
                                }},
                            )
                # ?쒖꽦??異붿쟻 ???쒖옣蹂??몃씪??援먯껜 湲곗??쇰줈 ?쒖슜
                _scan_interval_min = self._entry_scan_interval_sec(market) / 60.0
                if signal_fired:
                    if _soft_watch_candidate:
                        self._promote_trade_ready_ticker(
                            market,
                            ticker,
                            strategy_name=strategy_name,
                            trigger="soft_watch_signal",
                        )
                    self._ticker_no_signal_cycles[ticker] = 0
                    self._ticker_no_signal_minutes[ticker] = 0.0
                    if strategy_name == "opening_range_pullback":
                        isdb.update_signal(_intraday_log_row_id)
                else:
                    _prev_cnt = self._ticker_no_signal_cycles.get(ticker, 0)
                    self._ticker_no_signal_cycles[ticker] = _prev_cnt + 1
                    _prev_min = float(self._ticker_no_signal_minutes.get(ticker, 0.0) or 0.0)
                    self._ticker_no_signal_minutes[ticker] = _prev_min + _scan_interval_min
                    # Replace inactive watch tickers only when no position is held.
                    _holding = ticker in {p["ticker"] for p in self.risk.positions}
                    _swap_due = (
                        self._ticker_no_signal_minutes.get(ticker, 0.0) >= _KR_NO_SIGNAL_SWAP_MIN
                        if market == "KR"
                        else self._ticker_no_signal_cycles[ticker] >= _US_NO_SIGNAL_SWAP_CYCLES
                    )
                    if _watch_bucket in ("SOFT", "RECOVERY"):
                        _swap_due = False
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
                                self._entry_timing_mark_candidates(market, [_new], "inline_replacement")
                                self.today_judgment["tickers"] = _cur_list
                                self._ticker_no_signal_cycles.pop(ticker, None)
                                self._ticker_no_signal_minutes.pop(ticker, None)
                                # 援먯껜 醫낅ぉ 60遺?荑⑤떎???깅줉
                                _now_swap = datetime.now(KST).replace(tzinfo=None)
                                self._ticker_exclude_log.setdefault(market, []).append(
                                    {"ticker": ticker, "reason": "triangle_replace_no_signal", "ts": _now_swap}
                                )
                                _swap_label = (
                                    f"{round(_prev_min + _scan_interval_min):d}m no signal"
                                    if market == "KR"
                                    else f"{_prev_cnt + 1} cycles no signal"
                                )
                                log.info(f"[?몃씪?멸탳泥?{market}] {ticker}({_swap_label}) ??{_new}")
                                watchlist_change_alert(
                                    market,
                                    [ticker],
                                    [_new],
                                    reason=(
                                        f"{round(_prev_min + _scan_interval_min):d}min"
                                        if market == 'KR'
                                        else f"{_prev_cnt + 1} cycles"
                                    ) + " no signal",
                                )
                if not signal_fired:
                    def _cont_detail_str(_p, _ticker):
                        _d = cont_diag(sig_df, i, _p)
                        if _p.get("disabled"):
                            return f"continuation disabled {market} {mode}"
                        return (
                            f"continuation: window={bool(_d.get('in_window'))} "
                            f"elapsed={_d.get('elapsed_min', 0):.0f}m "
                            f"gap={_d.get('gap', 0):.2f}% "
                            f"vol={_d.get('vol_ratio', 0):.2f} "
                            f"cont={_d.get('cont_ratio', 0):.2f}%"
                        )
                    if market == "KR":
                        orp_p = _ap("opening_range_pullback")
                        gap_p = _ap("gap_pullback")
                        mr_p = _ap("mean_reversion")
                        vb_p = _ap("volatility_breakout")
                        mom_p = _ap("momentum")
                        _cont_p2 = _ap("continuation")
                        _mom_diag = kr_momentum_diag or mom_diag(sig_df, i, mom_p)
                        if mom_p.get("disabled"):
                            _mom_detail = f"momentum disabled {market} {mode}"
                        else:
                            _mom_detail = (
                                f"momentum: ma={bool(_mom_diag.get('ma_ok'))}, "
                                f"macd={bool(_mom_diag.get('macd_ok'))}, "
                                f"vol={bool(_mom_diag.get('vol_ok'))}, "
                                f"high={bool(_mom_diag.get('high_ok'))}"
                            )
                        # KR momentum ?쒓컙 寃뚯씠????泥?踰덉㎏ 釉붾줉怨??숈씪?섍쾶 ??뼱?뚯?
                        _kr_elapsed_nd = self._market_elapsed_min(market)
                        _kr_cont_win_nd = self._effective_momentum_wait_window(
                            market,
                            float(_cont_p2.get("cont_entry_max_min", 45) or 45),
                        )
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
                        _us_cont_window_max2 = self._effective_momentum_wait_window(
                            market,
                            float(_us_cont_p2.get("cont_entry_max_min", 45) or 45),
                        )
                        _us_elapsed_min2 = self._market_elapsed_min(market)
                        if _us_mom_p.get("disabled"):
                            _us_mom_detail = f"momentum disabled {market} {mode}"
                        else:
                            _us_mom_detail = (
                                f"momentum: ma={bool(_us_mom_diag.get('ma_ok'))}, "
                                f"macd={bool(_us_mom_diag.get('macd_ok'))}, "
                                f"vol={bool(_us_mom_diag.get('vol_ok'))}, "
                                f"high={bool(_us_mom_diag.get('high_ok'))}"
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
                    # ?μ큹諛??꾨낫 濡쒓퉭 ???좏샇 ?놁쓬?댁?留?opening window ??媛?3%+ 醫낅ぉ 異붿쟻
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
                    self._bump_runtime_reason(market, ticker, _rej_reason, bucket="rejection")
                    # ?쇰꼸: rejection_reason / volume_state ?꾩쟻
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
                    log.debug(f"  [{ticker}] no signal {price:,}")
                    # ML DB: NO_SIGNAL 湲곕줉 (?꾨왂蹂?near-miss ?ы븿)
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
                # HALT/DEFENSIVE: ??留덉폆 吏꾩엯 李⑤떒
                # CAUTIOUS_BEAR ?댄븯: US ?좉퇋 吏꾩엯 李⑤떒 (VIX 湲됰벑 援ш컙 ?ㅼ쬆 ?먯떎 諛⑹?)
                _risk_off_mr = self._risk_off_mr_exception(
                    market,
                    mode,
                    strategy_name,
                    sig_df.iloc[i].to_dict(),
                )
                _bear_block = mode in ("HALT", "DEFENSIVE")
                if not _bear_block and market == "US":
                    _US_BEAR_BLOCK_MODES = os.getenv(
                        "US_BEAR_BLOCK_MODES", "HALT,DEFENSIVE,CAUTIOUS_BEAR"
                    ).split(",")
                    _bear_block = mode in _US_BEAR_BLOCK_MODES
                if _bear_block and _risk_off_mr.get("allowed"):
                    log.info(
                        f"  [{ticker}] Risk-Off MR ?덉쇅 ?덉슜: "
                        f"mode={mode} size_cap={int(_risk_off_mr.get('size_cap', 40))}%"
                    )
                    _bear_block = False
                if _bear_block:
                    self._bump_runtime_reason(market, ticker, "blocked_by_mode")
                    analysis_log.info(
                        f"[signal {market}] {ticker} blocked_by_mode",
                        extra={"extra": {
                            "event": "signal_blocked",
                                "market": market,
                                "ticker": ticker,
                                "strategy": strategy_name,
                                "mode": mode,
                                "detail": _risk_off_mr.get("reason"),
                            }},
                        )
                    self._notify_signal_state_change(
                        "signal_blocked", market, ticker,
                        strategy=strategy_name,
                        price=float(price),
                        reason="signal_blocked",
                        mode=mode,
                    )
                    log.debug(f"  [{ticker}] {strategy_name} ?좏샇 ??{mode} 紐⑤뱶 吏꾩엯 ?듭젣")
                    # ML DB: BLOCKED 湲곕줉
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
                # ?μ쨷 怨좎젏洹쇱젒 吏꾩엯 李⑤떒 ???뱀씪 怨좎젏 ?鍮?-2% ?대궡 ?좉퇋 吏꾩엯 蹂대쪟
                # ?μ큹諛?30遺? ?쒖쇅, KR/US 怨듯넻 ?곸슜
                # Inverse ETFs are exempt from the near-day-high entry block.
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
                                f"  [{ticker}] 怨좎젏洹쇱젒 李⑤떒: ?꾩옱媛={float(price):,.0f} "
                                f"?뱀씪怨좎젏={_day_high:,.0f} "
                                f"from_high={_from_high_pct:.2f}% > {_FROM_HIGH_BLOCK}% ??吏꾩엯 蹂대쪟"
                            )
                            continue
                # 留덇컧 吏곸쟾 ?좉퇋 吏꾩엯 李⑤떒 ???뱀씪 泥?궛 遺덇? 諛⑹?
                _cutoff = (_KR_ENTRY_CUTOFF_FROM_OPEN_MIN if market == "KR"
                           else _US_ENTRY_CUTOFF_FROM_OPEN_MIN)
                _since_open_min = self._market_elapsed_min(market)
                if _since_open_min >= _cutoff:
                    log.info(f"[留덇컧 吏곸쟾 李⑤떒 {market}] {ticker} ??吏꾩엯 ?쒗븳 ?쒓컙 珥덇낵 "
                             f"({_since_open_min:.0f}遺?>= {_cutoff}遺?")
                    continue
                # ?? 吏꾩엯 ?곗꽑?쒖쐞 ?먯닔 (Phase 1: 濡쒓렇/ML 湲곕줉, hard cutoff ?놁쓬) ????
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
                        f"ma60={bool(_ep_detail.get('ma60_ok'))}"
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
                # ?? entry_priority cutoff (Phase 2 ??env/?붾젅洹몃옩 ON/OFF) ????
                _effective_entry_cutoff = self._effective_entry_priority_cutoff(market)
                if self._is_entry_priority_blocked(_ep_score, market):
                    log.info(
                        f"  [{ticker}] entry_priority cutoff: "
                        f"score={_ep_score:.3f} < {_effective_entry_cutoff:.3f} ??吏꾩엯 蹂대쪟"
                    )
                    if _ML_DB_ENABLED:
                        _ml_write_eval(
                            ticker, price, sig_df.iloc[i].to_dict(), "SKIPPED",
                            block_reason_="entry_priority_cutoff",
                            diag_json_={"score": _ep_score, "cutoff": _effective_entry_cutoff},
                            extra_fields_={"entry_priority_score": float(_ep_score)},
                        )
                    continue
                sl_cap = abs(HARD_RULES["max_single_loss_pct"]) / 100.0
                # ?? ATR 湲곕컲 ?숈쟻 TP/SL ???????????????????????????????????????
                # ATR???덉쑝硫?蹂?숈꽦??鍮꾨????먯젅/紐⑺몴媛 ?ъ슜 (Risk:Reward 2:1)
                # ATR ?녾굅??怨꾩궛 遺덇?硫??꾨왂 ?뚮씪誘명꽣 ?대갚
                _atr_val = float(sig_df.iloc[i].get("atr", 0) or 0)
                if _atr_val > 0 and float(price) > 0:
                    _atr_r = _atr_val / float(price)
                    sl_pct = min(max(_atr_r * float(os.getenv("ATR_SL_MULT", "1.5")), 0.01), sl_cap)
                    tp_pct = max(_atr_r * float(os.getenv("ATR_TP_MULT", "3.0")), 0.03)
                    # mean_reversion: BB 以묒꽑?????먯뿰?ㅻ윭??紐⑺몴 ??BB媛 ATR蹂대떎 媛源뚯슦硫?BB ?곗꽑
                    if strategy_name == "mean_reversion" and params.get("tp_bb_mid"):
                        bb_mid = float(sig_df.iloc[i].get("ma20", price))
                        _bb_tp = max((bb_mid - float(price)) / float(price), 0.005) if bb_mid > float(price) else tp_pct
                        tp_pct = _bb_tp
                    log.debug(f"  [{ticker}] ATR?숈쟻 SL={sl_pct:.2%} TP={tp_pct:.2%} (ATR={_atr_val:.2f})")
                else:
                    # Fallback to strategy fixed parameters when ATR is unavailable.
                    sl_pct = min(params.get("sl_pct", 0.03), sl_cap)
                    if strategy_name == "mean_reversion" and params.get("tp_bb_mid"):
                        bb_mid = float(sig_df.iloc[i].get("ma20", price))
                        tp_pct = max((bb_mid - float(price)) / float(price), 0.005) if bb_mid > float(price) else 0.03
                    else:
                        tp_pct = params.get("tp_pct", HARD_RULES["take_profit_pct"] / 100.0)
                atr_pct = None
                _atr_sizing_on = self.enable_atr_position_sizing and (
                    strategy_name != "continuation" or self.continuation_atr_position_sizing
                ) and (
                    strategy_name != "gap_pullback" or self.gap_pullback_atr_position_sizing
                )
                if _atr_sizing_on:
                    atr_val = float(sig_df.iloc[i].get("atr", 0) or 0)
                    if risk_price > 0 and atr_val > 0:
                        atr_pct = atr_val / risk_price
                # KR momentum: 湲곕낯 cap 珥덇낵 援ш컙? ?④퀎??媛먯븸?쇰줈 諛쏄퀬,
                # high cap 珥덇낵 援ш컙留?李⑤떒?쒕떎.
                _momentum_atr_block  = False
                _momentum_atr_size_cap = None  # None?대㈃ cap ?놁쓬
                _momentum_atr_stage = None
                if (
                    market == "KR"
                    and strategy_name == "momentum"
                    and atr_pct is not None
                    and atr_pct > 0
                ):
                    _momentum_atr_stage = self._effective_kr_momentum_atr_stage(atr_pct)
                    if not _momentum_atr_stage["blocked"] and _momentum_atr_stage["size_cap"] is not None:
                        _momentum_atr_size_cap = int(_momentum_atr_stage["size_cap"])
                        log.info(
                            f"  [{ticker}] KR momentum ATR 媛먯븸: "
                            f"atr_pct={atr_pct:.2%} "
                            f"stage={_momentum_atr_stage['stage']} "
                            f"size_cap={_momentum_atr_size_cap}% "
                            f"(cap={_momentum_atr_stage['cap']:.2%}, high={_momentum_atr_stage['high_cap']:.2%})"
                        )
                    elif _momentum_atr_stage["blocked"]:
                        _momentum_atr_block = True
                        self._bump_runtime_reason(market, ticker, "momentum_atr_too_high")
                        log.info(
                            f"  [{ticker}] KR momentum ATR 李⑤떒: "
                            f"atr_pct={atr_pct:.2%} > {_momentum_atr_stage['high_cap']:.2%}"
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
                                "atr_cap": round(float(_momentum_atr_stage["cap"]), 4),
                                "atr_high_cap": round(float(_momentum_atr_stage["high_cap"]), 4),
                                "mode": mode,
                            }},
                        )
                        if _ML_DB_ENABLED:
                            _ml_write_eval(
                                ticker, price, sig_df.iloc[i].to_dict(), "SKIPPED",
                                block_reason_="momentum_atr_too_high",
                                diag_json_={
                                    "atr_pct": float(atr_pct),
                                    "atr_cap": float(_momentum_atr_stage["cap"]),
                                    "atr_high_cap": float(_momentum_atr_stage["high_cap"]),
                                },
                            )
                if _momentum_atr_block:
                    continue
                # ?꾨왂蹂?size_mult ?곸슜 (?? momentum 0.4~1.0 ???⑹쓽 size_pct ?ㅼ??쇰쭅)
                size_mult = float(params.get("size_mult", 1.0))
                # ?? 1. entry_priority score ??size_mult 蹂댁젙 ????????????????
                # ?좏샇 ?덉쭏????쓣?섎줉 ?ъ???異뺤냼 (phase 1 ?곗씠???놁뼱??利됱떆 ?곸슜)
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
                        f"  [{ticker}] score={_ep_score:.3f} ??size_mult 횞 {_ep_size_mult} = {size_mult:.2f}"
                    )
                # ?? 2. ?뱀씪 ?먯젅 移댁슫????size_mult ?먮룞 異뺤냼 ???????????????
                _sl_cnt_now = self._daily_sl_count.get(market, 0)
                if _sl_cnt_now >= 3:
                    log.warning(
                        f"  [{ticker}] 당일 손절 {_sl_cnt_now}회 - 신규 진입 차단"
                    )
                    continue
                elif _sl_cnt_now == 2:
                    size_mult = round(size_mult * 0.6, 2)
                    log.info(f"  [{ticker}] ?뱀씪 ?먯젅 2????size_mult 횞 0.6 = {size_mult:.2f}")
                elif _sl_cnt_now == 1:
                    size_mult = round(size_mult * 0.8, 2)
                    log.info(f"  [{ticker}] ?뱀씪 ?먯젅 1????size_mult 횞 0.8 = {size_mult:.2f}")
                # ?? 3. ???꾨컲 吏꾩엯 湲곗? 媛뺥솕 ????????????????????????????????
                # KR 14:00 ?댄썑 / US 03:00 ?댄썑: score < 0.6?대㈃ 吏꾩엯 蹂대쪟
                # ?몃쾭??ETF ?쒖쇅 ?????꾨컲 ?섎씫 吏????吏꾩엯 湲고쉶媛 ?ㅽ엳????醫뗭쓬
                _late_session_min = {"KR": 300, "US": 270}  # KR=5h, US=4.5h from open
                _since_open_late  = self._market_elapsed_min(market)
                _late_threshold   = _late_session_min.get(market, 999)
                if ticker not in _INVERSE_SET and _since_open_late >= _late_threshold:
                    _LATE_SCORE_MIN = float(os.getenv("LATE_SESSION_SCORE_MIN", "0.6"))
                    if _ep_score < _LATE_SCORE_MIN:
                        log.info(
                            f"  [{ticker}] ???꾨컲({_since_open_late:.0f}遺? "
                            f"score={_ep_score:.3f} < {_LATE_SCORE_MIN} ??吏꾩엯 蹂대쪟"
                        )
                        continue
                effective_size = max(10, min(100, int(size_pct * size_mult)))
                if _momentum_atr_size_cap is not None:
                    effective_size = min(effective_size, _momentum_atr_size_cap)
                if _risk_off_mr.get("allowed"):
                    effective_size = min(effective_size, int(_risk_off_mr.get("size_cap", 40)))
                _selection_size_cap, _selection_size_reasons = self._selection_size_cap_pct(market, ticker)
                if _selection_size_cap is not None and effective_size > _selection_size_cap:
                    log.info(
                        f"  [{ticker}] selection_meta cap: "
                        f"{effective_size}% -> {_selection_size_cap}% "
                        f"({', '.join(_selection_size_reasons)})"
                    )
                    effective_size = max(1, min(100, _selection_size_cap))
                # ?? ?좏샇 ?섏쭛 ???ъ씠???꾨즺 ??score ?뺣젹 ??二쇰Ц ?ㅽ뻾 ????????
                _entry_timing_snapshot = self._entry_timing_signal_fired(
                    market,
                    ticker,
                    price=float(price),
                    strategy=strategy_name,
                    reason=str(_ep_detail or none_detail or ""),
                )
                _audit_signal_id = self._audit_emit_signal(
                    market,
                    ticker,
                    strategy=strategy_name,
                    signal_at=datetime.now(KST).isoformat(timespec="seconds"),
                    signal_price=float(price),
                    risk_price_krw=float(risk_price),
                    score=float(_ep_score),
                    decision="signal_fired",
                    source="path_a",
                    path_type="path_a",
                    payload={
                        "entry_priority_detail": _ep_detail,
                        "entry_timing": _entry_timing_snapshot,
                        "selected_reason": (self.today_ticker_reasons.get(market, {}) or {}).get(ticker, ""),
                        "market_mode": mode,
                    },
                )
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
                    "elapsed_min":      float(_ep_elapsed),
                    "tsdb_id":         (self._tsdb_selection_ids.get(market) or {}).get(ticker),
                    "entry_timing":    _entry_timing_snapshot,
                    "audit_signal_id": _audit_signal_id,
                })
            except Exception as e:
                log.error(f"cycle error [{ticker}]: {e}")
        # ?? ?섏쭛???좏샇 ?뺣젹 ??二쇰Ц ?ㅽ뻾 ????????????????????????????????????
        if _pending_signals:
            _pending_signals.sort(key=lambda s: s["score"], reverse=True)
            log.info(
                f"[{market}] ?좏샇 ?뺣젹: " +
                ", ".join(f"{s['ticker']}({s['score']:.3f})" for s in _pending_signals)
            )
            for _sig in _pending_signals:
                _s_tk   = _sig["ticker"]
                _s_px   = _sig["price"]
                _s_rpx  = _sig["risk_price"]
                _s_row  = _sig["sig_row"]
                _s_strat= _sig["strategy_name"]
                _s_source_strat = _s_strat
                _micro_probe_meta = {}
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
                _audit_signal_id = str(_sig.get("audit_signal_id") or "")
                try:
                    avail = self._market_budget_available(market)
                    order_budget = self.risk.calc_order_budget(
                        _s_esz,
                        atr_pct=_s_atr,
                        atr_target_pct=self.atr_target_pct,
                    )
                    qty = self.risk.calc_order_size(
                        _s_rpx, _s_esz, _s_sl,
                        atr_pct=_s_atr, atr_target_pct=self.atr_target_pct,
                    )
                    order_cost = qty * _s_rpx if qty > 0 else 0.0
                    _v2_fixed = self._v2_fixed_size_entry(market, _s_rpx)
                    if _v2_fixed is not None:
                        qty = int(_v2_fixed.qty)
                        order_budget = float(_v2_fixed.budget_krw)
                        order_cost = float(_v2_fixed.order_cost_krw)
                    _v2_min_order_krw = (
                        float(_v2_fixed.min_order_krw)
                        if _v2_fixed is not None
                        else float(self._min_effective_order_krw(market))
                    )
                    _s_ep_detail = _sig.get("ep_detail") or {}
                    try:
                        _s_elapsed_min = float(_sig.get("elapsed_min", self._market_elapsed_min(market)) or 0.0)
                    except Exception:
                        _s_elapsed_min = None
                    try:
                        _s_change_pct = float(
                            _s_row.get("change_pct")
                            if _s_row.get("change_pct") is not None
                            else _s_row.get("chg_pct")
                        )
                    except Exception:
                        _s_change_pct = None
                    try:
                        _s_from_high_pct = float(
                            _s_ep_detail.get("from_high_pct")
                            if _s_ep_detail.get("from_high_pct") is not None
                            else _s_row.get("from_high_pct")
                        )
                    except Exception:
                        _s_from_high_pct = None
                    _v2_unknown_state = self._v2_order_unknown_block_state(market, _s_tk)
                    _v2_arb = self._v2_arbitrate_path_a_entry(
                        market,
                        _s_tk,
                        current_price=float(_s_px),
                        strategy=_s_strat,
                    )
                    _v2_reentry = self._v2_same_day_reentry_decision(market, _s_tk)
                    _v2_late_payload = _build_late_entry_payload(
                        entry_elapsed_min=_s_elapsed_min,
                        change_pct_at_entry=_s_change_pct,
                        from_high_pct=_s_from_high_pct,
                        selected_reason=_s_sr,
                        arbiter_shadow=getattr(_v2_arb, "shadow", {}) if _v2_arb is not None else {},
                        reentry_shadow=getattr(_v2_reentry, "shadow", {}) if _v2_reentry is not None else {},
                    )
                    if bool((_v2_unknown_state or {}).get("blocked")):
                        _reason = str((_v2_unknown_state or {}).get("reason") or "ORDER_UNKNOWN_UNRESOLVED")
                        _details = {
                            "market": market,
                            "ticker": _s_tk,
                            "strategy": _s_strat,
                            "unknown_state": _v2_unknown_state,
                            **_v2_late_payload,
                        }
                        self._bump_runtime_reason(market, _s_tk, _reason)
                        self._record_decision_event(
                            market,
                            "buy_blocked",
                            _s_tk,
                            strategy=_s_strat,
                            qty=int(qty),
                            price_native=float(_s_px),
                            price_krw=float(_s_rpx),
                            reason=_reason,
                            reason_family="v2_order_unknown",
                            detail="ORDER_UNKNOWN escalation blocks new entry",
                            selected_reason=_s_sr,
                        )
                        self._v2_record_lifecycle_event(
                            "SAFETY_BLOCKED",
                            market,
                            _s_tk,
                            reason_code=_reason,
                            payload=_details,
                        )
                        if _ML_DB_ENABLED:
                            _ml_write_eval(
                                _s_tk,
                                _s_px,
                                _s_row,
                                "BLOCKED",
                                block_reason_=_reason,
                                strategy_used_=_s_strat,
                                fired_strategy_=_s_strat,
                                diag_json_={"stage": "v2_order_unknown", **_details},
                            )
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, _reason)
                        _audit_episode_id = self._audit_active_episode(
                            market,
                            episode_type="ORDER_UNKNOWN_PAUSE",
                            scope=str((_v2_unknown_state or {}).get("scope") or "ticker"),
                            reason=_reason,
                            ticker=_s_tk if str((_v2_unknown_state or {}).get("scope") or "") == "ticker" else "",
                            payload={"unknown_state": _v2_unknown_state},
                        )
                        self._audit_mark_signal_decision(
                            market,
                            _s_tk,
                            signal_id=_audit_signal_id,
                            decision="BLOCKED",
                            block_reason=_reason,
                            signal_price=float(_s_px),
                            risk_price_krw=float(_s_rpx),
                            strategy=_s_strat,
                            score=float(_s_ep),
                            payload={"stage": "v2_order_unknown", **_details},
                        )
                        self._audit_link_signal_episode(_audit_signal_id, _audit_episode_id, reason="ORDER_UNKNOWN_SIGNAL_BLOCKED")
                        self._audit_emit_price_sample(
                            market,
                            _s_tk,
                            price=float(_s_px),
                            source="path_a:order_unknown_blocked",
                            signal_id=_audit_signal_id,
                            episode_id=_audit_episode_id,
                            payload=_details,
                        )
                        log.warning(f"[V2 ORDER_UNKNOWN_BLOCKED] {market} {_s_tk} {_reason}")
                        continue
                    if _v2_arb is not None and not _v2_arb.allowed:
                        _arb_payload = {
                            **(getattr(_v2_arb, "details", {}) or {}),
                            **_v2_late_payload,
                            "pathb_order_unknown_conflict": getattr(_v2_arb, "reason_code", "") == "PATHB_ORDER_UNKNOWN_SAME_TICKER",
                            "message": getattr(_v2_arb, "message", ""),
                            "strategy": _s_strat,
                        }
                        self._bump_runtime_reason(market, _s_tk, _v2_arb.reason_code)
                        self._record_decision_event(
                            market,
                            "buy_blocked",
                            _s_tk,
                            strategy=_s_strat,
                            qty=int(qty),
                            price_native=float(_s_px),
                            price_krw=float(_s_rpx),
                            reason=_v2_arb.reason_code,
                            reason_family="path_arbiter",
                            detail=_v2_arb.message,
                            selected_reason=_s_sr,
                        )
                        self._v2_record_lifecycle_event(
                            "SAFETY_BLOCKED",
                            market,
                            _s_tk,
                            reason_code=_v2_arb.reason_code,
                            payload=_arb_payload,
                        )
                        if _ML_DB_ENABLED:
                            _ml_write_eval(
                                _s_tk,
                                _s_px,
                                _s_row,
                                "BLOCKED",
                                block_reason_=_v2_arb.reason_code,
                                strategy_used_=_s_strat,
                                fired_strategy_=_s_strat,
                                diag_json_={"stage": "path_arbiter", **_arb_payload},
                            )
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, _v2_arb.reason_code)
                        self._audit_mark_signal_decision(
                            market,
                            _s_tk,
                            signal_id=_audit_signal_id,
                            decision="BLOCKED",
                            block_reason=_v2_arb.reason_code,
                            signal_price=float(_s_px),
                            risk_price_krw=float(_s_rpx),
                            strategy=_s_strat,
                            score=float(_s_ep),
                            payload={"stage": "path_arbiter", **_arb_payload},
                        )
                        log.warning(f"[Path Arbiter BLOCKED] {market} {_s_tk} {_v2_arb.reason_code} - {_v2_arb.message}")
                        continue
                    if _v2_reentry is not None and not _v2_reentry.allowed:
                        _reentry_payload = {
                            **(getattr(_v2_reentry, "details", {}) or {}),
                            **_v2_late_payload,
                            "message": getattr(_v2_reentry, "message", ""),
                            "strategy": _s_strat,
                        }
                        self._bump_runtime_reason(market, _s_tk, _v2_reentry.reason_code)
                        self._record_decision_event(
                            market,
                            "buy_blocked",
                            _s_tk,
                            strategy=_s_strat,
                            qty=int(qty),
                            price_native=float(_s_px),
                            price_krw=float(_s_rpx),
                            reason=_v2_reentry.reason_code,
                            reason_family="same_day_reentry",
                            detail=_v2_reentry.message,
                            selected_reason=_s_sr,
                        )
                        self._v2_record_lifecycle_event(
                            "SAFETY_BLOCKED",
                            market,
                            _s_tk,
                            reason_code=_v2_reentry.reason_code,
                            payload=_reentry_payload,
                        )
                        if _ML_DB_ENABLED:
                            _ml_write_eval(
                                _s_tk,
                                _s_px,
                                _s_row,
                                "BLOCKED",
                                block_reason_=_v2_reentry.reason_code,
                                strategy_used_=_s_strat,
                                fired_strategy_=_s_strat,
                                diag_json_={"stage": "same_day_reentry", **_reentry_payload},
                            )
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, _v2_reentry.reason_code)
                        self._audit_mark_signal_decision(
                            market,
                            _s_tk,
                            signal_id=_audit_signal_id,
                            decision="BLOCKED",
                            block_reason=_v2_reentry.reason_code,
                            signal_price=float(_s_px),
                            risk_price_krw=float(_s_rpx),
                            strategy=_s_strat,
                            score=float(_s_ep),
                            payload={"stage": "same_day_reentry", **_reentry_payload},
                        )
                        log.warning(f"[Reentry Cooldown BLOCKED] {market} {_s_tk} {_v2_reentry.reason_code} - {_v2_reentry.message}")
                        continue
                    _v2_safety = self._v2_safety_decision(
                        market,
                        _s_tk,
                        risk_price_krw=float(_s_rpx),
                        qty=int(qty),
                        order_cost_krw=float(order_cost),
                        min_order_krw=float(_v2_min_order_krw),
                    )
                    if _v2_safety is not None and not _v2_safety.passed:
                        self._bump_runtime_reason(market, _s_tk, _v2_safety.reason_code)
                        self._record_decision_event(
                            market,
                            "buy_blocked",
                            _s_tk,
                            strategy=_s_strat,
                            qty=int(qty),
                            price_native=float(_s_px),
                            price_krw=float(_s_rpx),
                            reason=_v2_safety.reason_code,
                            reason_family="v2_safety",
                            detail=_v2_safety.message,
                            selected_reason=_s_sr,
                        )
                        self._v2_record_lifecycle_event(
                            "SAFETY_BLOCKED",
                            market,
                            _s_tk,
                            reason_code=_v2_safety.reason_code,
                            payload={
                                **(_v2_safety.details or {}),
                                **_v2_late_payload,
                                "message": _v2_safety.message,
                                "strategy": _s_strat,
                                "fixed_sizing": _v2_fixed is not None,
                            },
                        )
                        if _ML_DB_ENABLED:
                            _ml_write_eval(
                                _s_tk,
                                _s_px,
                                _s_row,
                                "BLOCKED",
                                block_reason_=_v2_safety.reason_code,
                                strategy_used_=_s_strat,
                                fired_strategy_=_s_strat,
                                diag_json_={"stage": "v2_safety", **(_v2_safety.details or {})},
                            )
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, _v2_safety.reason_code)
                        self._audit_mark_signal_decision(
                            market,
                            _s_tk,
                            signal_id=_audit_signal_id,
                            decision="BLOCKED",
                            block_reason=_v2_safety.reason_code,
                            signal_price=float(_s_px),
                            risk_price_krw=float(_s_rpx),
                            strategy=_s_strat,
                            score=float(_s_ep),
                            payload={"stage": "v2_safety", **(_v2_safety.details or {})},
                        )
                        log.info(f"[V2 SAFETY_BLOCKED] {market} {_s_tk} {_v2_safety.reason_code} - {_v2_safety.message}")
                        continue
                    elif _v2_safety is not None:
                        self._v2_record_lifecycle_event(
                            "SAFETY_PASSED",
                            market,
                            _s_tk,
                            payload={
                                **(_v2_safety.details or {}),
                                **_v2_late_payload,
                                "strategy": _s_strat,
                                "fixed_sizing": _v2_fixed is not None,
                            },
                        )
                    is_too_small, min_effective_order = self._is_order_size_too_small(
                        market,
                        qty,
                        order_cost,
                        order_budget,
                    )
                    affordability_diag = self._affordability_diag(
                        price_krw=float(_s_rpx),
                        qty=int(qty),
                        order_cost_krw=float(order_cost),
                        order_budget_krw=float(order_budget),
                        available_budget_krw=float(avail),
                        cash_krw=float(self.risk.cash),
                        min_effective_order_krw=float(min_effective_order),
                    )
                    if is_too_small:
                        diag = {
                            "qty": int(qty),
                            "order_cost_krw": float(order_cost),
                            "order_budget_krw": float(order_budget),
                            "min_effective_order_krw": float(min_effective_order),
                            "effective_size": int(_s_esz),
                            "atr_pct": float(_s_atr) if _s_atr is not None else None,
                            **affordability_diag,
                        }
                        probe = self._micro_probe_adjustment(
                            market=market,
                            ticker=_s_tk,
                            mode=mode,
                            source_strategy=_s_strat,
                            entry_priority_score=_s_ep,
                            qty=qty,
                            risk_price_krw=_s_rpx,
                            original_order_cost_krw=order_cost,
                            order_budget_krw=order_budget,
                            min_effective_order_krw=min_effective_order,
                            available_budget_krw=avail,
                            cash_krw=self.risk.cash,
                        )
                        if probe.get("allowed"):
                            _s_source_strat = _s_strat
                            _s_strat = _MICRO_PROBE_STRATEGY
                            qty = int(probe["adjusted_qty"])
                            order_cost = float(probe["adjusted_order_cost_krw"])
                            _micro_probe_meta = {
                                **probe,
                                "original_strategy": _s_source_strat,
                            }
                            analysis_log.info(
                                f"[micro_probe {market}] {_s_tk} {_s_source_strat} -> {qty} shares",
                                extra={"extra": {
                                    "event": "micro_probe_entry",
                                    "market": market,
                                    "ticker": _s_tk,
                                    "strategy": _MICRO_PROBE_STRATEGY,
                                    "source_strategy": _s_source_strat,
                                    "mode": mode,
                                    "price": _s_px,
                                    "risk_price_krw": _s_rpx,
                                    **{k: v for k, v in _micro_probe_meta.items() if k != "allowed"},
                                }},
                            )
                            log.info(
                                f"  [{_s_tk}] MICRO_PROBE ?꾪솚 "
                                f"{probe['original_order_cost_krw']:,.0f}??-> {order_cost:,.0f}??"
                                f"(x{probe['oversize_ratio']:.2f})"
                            )
                            self._submit_micro_probe_buy_order(
                                market=market,
                                ticker=_s_tk,
                                name=str(_s_row.get("name", "") or "").strip(),
                                qty=qty,
                                raw_price=float(_s_px),
                                risk_price_krw=float(_s_rpx),
                                tp_pct=float(_s_tp),
                                sl_pct=float(_s_sl),
                                max_hold=int(_s_mh),
                                mode=mode,
                                selected_reason=_s_sr,
                                source_strategy=_s_source_strat,
                                entry_priority_score=float(_s_ep),
                                tsdb_id=_tsdb_id,
                                isdb_id=_isdb_id,
                                signal_at=_sig_at,
                                signal_row=_s_row,
                                probe_meta=_micro_probe_meta,
                            )
                            continue
                        else:
                            diag["micro_probe_block_reason"] = probe.get("reason", "")
                            if "adjusted_order_cost_krw" in probe:
                                diag["micro_probe_adjusted_order_cost_krw"] = float(probe.get("adjusted_order_cost_krw") or 0.0)
                            if "oversize_ratio" in probe:
                                diag["micro_probe_oversize_ratio"] = float(probe.get("oversize_ratio") or 0.0)
                        self._bump_runtime_reason(market, _s_tk, "order_size_too_small")
                        self._bump_runtime_reason(market, _s_tk, "affordability_fail")
                        self._record_decision_event(
                            market,
                            "buy_skipped",
                            _s_tk,
                            strategy=_s_strat,
                            qty=int(qty),
                            price_native=float(_s_px),
                            price_krw=float(_s_rpx),
                            reason="order_size_too_small",
                            reason_family="affordability",
                            detail=(
                                f"cost={order_cost:,.0f} budget={order_budget:,.0f} "
                                f"min={min_effective_order:,.0f}"
                            ),
                            selected_reason=_s_sr,
                            **affordability_diag,
                        )
                        if _ML_DB_ENABLED:
                            _ml_write_eval(
                                _s_tk,
                                _s_px,
                                _s_row,
                                "SKIPPED",
                                block_reason_="order_size_too_small",
                                diag_json_=diag,
                            )
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, "order_size_too_small")
                        analysis_log.info(
                            f"[skip {market}] {_s_tk} order_size_too_small",
                            extra={"extra": {
                                "event": "entry_skip", "market": market, "ticker": _s_tk,
                                "reason": "order_size_too_small", "strategy": _s_strat,
                                "mode": mode, "price": _s_px, "risk_price_krw": _s_rpx,
                                **diag,
                            }},
                        )
                        log.info(
                            f"  [{_s_tk}] order_size_too_small "
                            f"({order_cost:,.0f}??< {min_effective_order:,.0f}?? "
                            f"budget={order_budget:,.0f}?? - skip"
                        )
                        continue
                    if qty <= 0:
                        self._bump_runtime_reason(market, _s_tk, "affordability_fail")
                        self._record_decision_event(
                            market,
                            "buy_skipped",
                            _s_tk,
                            strategy=_s_strat,
                            qty=0,
                            price_native=float(_s_px),
                            price_krw=float(_s_rpx),
                            reason="qty_zero",
                            reason_family="affordability",
                            detail="risk engine returned zero quantity",
                            selected_reason=_s_sr,
                            **affordability_diag,
                        )
                        if _ML_DB_ENABLED:
                            _ml_write_eval(_s_tk, _s_px, _s_row, "SKIPPED",
                                           block_reason_="qty_zero",
                                           diag_json_={"reason": "qty_zero", **affordability_diag})
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, "qty_zero")
                        log.debug(f"  [{_s_tk}] order price/qty invalid 0")
                        continue
                    if order_cost > avail:
                        self._bump_runtime_reason(market, _s_tk, "affordability_fail")
                        self._record_decision_event(
                            market,
                            "buy_skipped",
                            _s_tk,
                            strategy=_s_strat,
                            qty=int(qty),
                            price_native=float(_s_px),
                            price_krw=float(_s_rpx),
                            reason="market_budget_exceeded",
                            reason_family="affordability",
                            detail=f"cost={order_cost:,.0f} available={avail:,.0f}",
                            selected_reason=_s_sr,
                            **affordability_diag,
                        )
                        if _ML_DB_ENABLED:
                            _ml_write_eval(_s_tk, _s_px, _s_row, "SKIPPED",
                                           block_reason_="market_budget_exceeded",
                                           diag_json_={"order_cost_krw": float(order_cost), "available_budget_krw": float(avail), **affordability_diag})
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, "market_budget_exceeded")
                        log.debug(f"  [{_s_tk}] ?쒖옣 ?덉궛 珥덇낵 ({order_cost:,.0f}??> {avail:,.0f}?? ???ㅽ궢")
                        continue
                    if order_cost > self.risk.cash:
                        self._bump_runtime_reason(market, _s_tk, "affordability_fail")
                        self._record_decision_event(
                            market,
                            "buy_skipped",
                            _s_tk,
                            strategy=_s_strat,
                            qty=int(qty),
                            price_native=float(_s_px),
                            price_krw=float(_s_rpx),
                            reason="insufficient_cash",
                            reason_family="affordability",
                            detail=f"cost={order_cost:,.0f} cash={self.risk.cash:,.0f}",
                            selected_reason=_s_sr,
                            **affordability_diag,
                        )
                        if _ML_DB_ENABLED:
                            _ml_write_eval(_s_tk, _s_px, _s_row, "SKIPPED",
                                           block_reason_="insufficient_cash",
                                           diag_json_={"order_cost_krw": float(order_cost), "cash_krw": float(self.risk.cash), **affordability_diag})
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, "insufficient_cash")
                        log.debug(f"  [{_s_tk}] ?꾧툑 遺議?({order_cost:,.0f}??> {self.risk.cash:,.0f}?? ???ㅽ궢")
                        continue
                    _selection_cap_pct, _selection_cap_reasons = self._selection_size_cap_pct(market, _s_tk)
                    _atr_size_scale = None
                    if _s_atr is not None and float(_s_atr or 0) > 0 and float(self.atr_target_pct or 0) > 0:
                        _atr_size_scale = max(0.1, min(1.0, float(self.atr_target_pct) / float(_s_atr)))
                    _sizing_contract = {
                        "sizing_contract_version": "sizing_contract_v1",
                        "claude_allocation_intent": self._selection_meta_value(market, _s_tk, "allocation_intent"),
                        "claude_max_order_cap_pct": _selection_cap_pct,
                        "claude_risk_budget_pct": self._selection_meta_value(market, _s_tk, "risk_budget_pct"),
                        "claude_size_reason": self._selection_meta_value(market, _s_tk, "size_reason"),
                        "selection_size_cap_reasons": _selection_cap_reasons,
                        "consensus_size_pct": int(size_pct),
                        "effective_mode_size_pct": int(_s_esz),
                        "atr_size_scale": _atr_size_scale,
                        "cash_krw": float(self.risk.cash),
                        "market_available_budget_krw": float(avail),
                        "final_order_budget_krw": float(order_budget),
                        "final_qty": int(qty),
                    }
                    analysis_log.info(
                        f"[signal {market}] {_s_tk} {_s_strat} qty={qty}",
                        extra={"extra": {
                            "event": "entry_signal", "market": market, "ticker": _s_tk,
                            "strategy": _s_strat, "mode": mode, "price": _s_px,
                            "risk_price_krw": _s_rpx, "qty": qty,
                            "order_cost_krw": order_cost, "tp_pct": _s_tp, "sl_pct": _s_sl,
                            **_v2_late_payload,
                            **_sizing_contract,
                        }},
                    )
                    # ?쇰꼸: signaled
                    self._funnel[market]["signaled"] += 1
                    self._notify_signal_state_change(
                        "entry_signal", market, _s_tk,
                        strategy=_s_strat, price=float(_s_px), mode=mode,
                        qty=qty, order_cost_krw=int(order_cost),
                    )
                    order_px = self._compute_order_price("buy", market, float(_s_px))
                    precheck_px = float(_s_px) if order_px == 0 else order_px
                    _buy_gate = self._new_buy_block_state(market, _s_tk, _s_strat)
                    if not bool(_buy_gate.get("allowed", True)):
                        self._record_new_buy_block(
                            market,
                            _s_tk,
                            _s_strat,
                            _buy_gate,
                            qty=qty,
                            price_native=order_px,
                            price_krw=_s_rpx,
                            selected_reason=_s_sr,
                            stage="path_a_precheck",
                            tsdb_id=_tsdb_id,
                            entry_priority=_s_ep,
                            signal_at=_sig_at,
                            signal_row=_s_row,
                        )
                        continue
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
                        log.info(f"[二쇰Ц ?ъ쟾泥댄겕 ?ㅽ뙣] {_s_tk} ??{precheck.get('msg','二쇰Ц 遺덇?')}")
                        if _ML_DB_ENABLED:
                            _ml_write_eval(_s_tk, _s_px, _s_row, "BLOCKED",
                                           block_reason_=precheck.get("reason", "precheck_failed"),
                                           strategy_used_=_s_strat, fired_strategy_=_s_strat,
                                           diag_json_={"detail": precheck.get("msg", ""), "stage": "precheck"})
                        tsdb.update_signal(_tsdb_id, _s_strat, _s_ep, _sig_at, precheck.get("reason", "precheck_failed"))
                        continue
                    if self.v2_order_rate_limiter is not None:
                        _rate_key = f"{self._mode}:{market}:buy"
                        if not self.v2_order_rate_limiter.allow(_rate_key, time.time()):
                            self._record_decision_event(
                                market, "buy_blocked", _s_tk, strategy=_s_strat, qty=qty,
                                price_native=order_px, price_krw=_s_rpx,
                                reason="rate_limited", reason_family="v2_execution",
                                detail="V2 order rate limiter blocked the order",
                                selected_reason=_s_sr,
                            )
                            self._v2_record_lifecycle_event(
                                "SAFETY_BLOCKED",
                                market,
                                _s_tk,
                                reason_code="MAX_DAILY_ENTRIES",
                                payload={"message": "order rate limited", "rate_key": _rate_key},
                            )
                            log.warning(f"[V2 rate limit] {market} {_s_tk} buy order blocked")
                            continue
                    try:
                        result = place_order(_s_tk, qty, order_px, "buy", self.token, market=market)
                    except Exception as e:
                        self._record_decision_event(
                            market, "buy_failed", _s_tk, strategy=_s_strat, qty=qty,
                            price_native=order_px, price_krw=_s_rpx,
                            reason="order_exception", detail=str(e), selected_reason=_s_sr,
                        )
                        # ?곗냽 ?ㅻ쪟 移댁슫??利앷? ??3???댁긽?대㈃ ?뱀씪 李⑤떒
                        _err_cnt = self._order_error_count.get(_s_tk, 0) + 1
                        self._order_error_count[_s_tk] = _err_cnt
                        if _err_cnt >= 3:
                            _blk_reason = f"?곗냽?ㅻ쪟 {_err_cnt}?? {e}"
                            self._mark_us_order_blocked(_s_tk, _blk_reason)
                            log.warning(f"[二쇰Ц?ㅻ쪟李⑤떒 {market}] {_s_tk} {_err_cnt}???곗냽 ???뱀씪 李⑤떒: {e}")
                        if market == "US" and self.is_paper:
                            _reason = f"VTS 二쇰Ц?ㅽ뙣: {e}"
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
                        # Broker-declared untradable symbols should not be retried.
                        if "매매불가" in _reason or "주문처리가 안되었습니다" in _reason:
                            self._mark_us_order_blocked(_s_tk, _reason)
                            log.warning(f"[untradable] {_s_tk} order blocked: {_reason}")
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
                    # Clear consecutive order errors after a successful order.
                    self._order_error_count.pop(_s_tk, None)
                    _entry_timing_order_snapshot = self._entry_timing_order_sent(
                        market,
                        _s_tk,
                        price=float(_s_px),
                        order_no=str(result.get("order_no", "") or ""),
                        strategy=_s_strat,
                        qty=int(qty),
                    )
                    log.info(
                        f"[{'PAPER' if self.is_paper else 'LIVE'} BUY] "
                        f"{_s_tk} {qty}@{_s_px:,} | {_s_strat} | "
                        f"二쇰Ц踰덊샇={result.get('order_no','')} | score={_s_ep:.3f}"
                    )
                    # ?쇰꼸: ordered
                    self._funnel[market]["ordered"] += 1
                    self._record_decision_event(
                        market, "buy_order", _s_tk, strategy=_s_strat, qty=qty,
                        price_native=float(_s_px), price_krw=_s_rpx,
                        reason=f"{_s_strat} 吏꾩엯",
                        detail=(f"?좏깮?댁쑀: {_s_sr}" if _s_sr
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
                                        "selection_recommended_strategy": self._recommended_strategy_for_ticker(market, _s_tk),
                                        "selection_risk_tags": self._risk_tags_for_ticker(market, _s_tk),
                                        "selection_size_cap_pct": self._selection_size_cap_pct(market, _s_tk)[0],
                                    },
                                    extra_fields_={"entry_priority_score": float(_s_ep)},
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
                    _v2_decision_id = self._v2_decision_id_for_ticker(market, _s_tk)
                    _v2_execution_id = str(result.get("order_no", "") or f"exec_{market}_{_s_tk}_{int(time.time())}")
                    self._v2_record_lifecycle_event(
                        "ORDER_SENT",
                        market,
                        _s_tk,
                        decision_id=_v2_decision_id,
                        execution_id=_v2_execution_id,
                        payload={
                            "order_no": result.get("order_no", ""),
                            "qty": int(qty),
                            "price_native": float(_s_px),
                            "price_krw": float(_s_rpx),
                            "strategy": _s_strat,
                            **_v2_late_payload,
                        },
                    )
                    self._audit_mark_signal_decision(
                        market,
                        _s_tk,
                        signal_id=_audit_signal_id,
                        decision="BUY_SIGNAL",
                        signal_price=float(_s_px),
                        risk_price_krw=float(_s_rpx),
                        strategy=_s_strat,
                        score=float(_s_ep),
                        payload={
                            "order_no": result.get("order_no", ""),
                            "qty": int(qty),
                            "v2_execution_id": _v2_execution_id,
                            **_v2_late_payload,
                        },
                    )
                    self._audit_try_emit(
                        {
                            "kind": "trade_link",
                            "signal_id": _audit_signal_id,
                            "decision_id": _v2_decision_id,
                            "order_no": result.get("order_no", ""),
                            "entry_price": float(_s_px),
                            "payload": {"ticker": _s_tk, "market": market, "qty": int(qty), "path_type": "path_a"},
                        }
                    )
                    _entry_reference_meta = self._entry_reference_metadata(market, _s_tk)
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
                        "v2_decision_id": _v2_decision_id,
                        "v2_execution_id": _v2_execution_id,
                        "entry_timing":   _entry_timing_order_snapshot,
                        **_entry_reference_meta,
                    })
                    self._block_entry(_s_tk, _BUY_COOLDOWN_MIN, "buy_placed")
                    buy_order_alert(
                        market=market,
                        ticker=_s_tk,
                        qty=qty,
                        order_no=str(result.get("order_no", "") or ""),
                        detail="泥닿껐 ?뺤씤 ??蹂댁쑀 ?ъ??섏뿉 諛섏쁺?⑸땲??",
                        name=str(_s_row.get("name", "") or "").strip(),
                        buy_path="path_a",
                    )
                except Exception as e:
                    log.error(f"pending signal execute error [{_s_tk}]: {e}")
        # ?? Tier 2 ?뱁꽣 ?뚮젅??(US ?꾩슜) ?????????????????????????????????????
        # Core 5 猷⑦봽 ?꾨즺 ?? ?뱁꽣 ETF 媛뺤꽭 ?좏샇媛 ?덉쑝硫?異붽? 吏꾩엯 ?쒕룄
        if market == "US" and mode not in ("HALT", "DEFENSIVE", "CAUTIOUS_BEAR"):
            try:
                _tier2_market_gate = self._new_buy_block_state("US", strategy="sector_play")
                if not bool(_tier2_market_gate.get("allowed", True)):
                    self._record_new_buy_block("US", "", "sector_play", _tier2_market_gate, stage="tier2_market_preflight")
                    raise StopIteration
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
                        _tier2_ticker_gate = self._new_buy_block_state("US", _t2_ticker, "sector_play")
                        if not bool(_tier2_ticker_gate.get("allowed", True)):
                            self._record_new_buy_block(
                                "US", _t2_ticker, "sector_play", _tier2_ticker_gate, stage="tier2_ticker_preflight"
                            )
                            continue
                        # ?대? ?ъ???蹂댁쑀 以묒씠硫??ㅽ궢
                        if any(p["ticker"] == _t2_ticker for p in self.risk.positions):
                            log.debug(f"  [Tier2] {_t2_ticker} ?대? ?ъ???蹂댁쑀 ???ㅽ궢")
                            continue
                        # 吏꾩엯 荑⑤떎??泥댄겕
                        if self._is_entry_blocked(_t2_ticker):
                            log.debug(f"  [Tier2] {_t2_ticker} 吏꾩엯 荑⑤떎?????ㅽ궢")
                            continue
                        # US 二쇰Ц 李⑤떒 泥댄겕
                        _t2_blocked = self._us_order_block_reason(_t2_ticker)
                        if _t2_blocked:
                            log.debug(f"  [Tier2] {_t2_ticker} 二쇰Ц李⑤떒: {_t2_blocked}")
                            continue
                        _t2_price_info = get_price(_t2_ticker, self.token, market="US")
                        _t2_price = _t2_price_info["price"]
                        if _t2_price <= 0:
                            continue
                        _t2_risk_price = self._price_to_krw(_t2_price, "US")
                        self.price_cache_raw[_t2_ticker] = _t2_price
                        self.price_cache[_t2_ticker] = _t2_risk_price
                        self.risk.update_prices(self.price_cache, self.price_cache_raw)
                        # 50% ?ъ씠利??곸슜
                        _t2_size = max(10, int(size_pct * TIER2_SIZE_RATIO))
                        _t2_sl, _t2_tp = 0.025, 0.05  # Tier2 怨좎젙 SL 2.5% / TP 5%
                        _t2_budget = self.risk.calc_order_budget(_t2_size)
                        _t2_qty = self.risk.calc_order_size(
                            _t2_risk_price, _t2_size, _t2_sl
                        )
                        _t2_cost = _t2_qty * _t2_risk_price if _t2_qty > 0 else 0.0
                        _t2_avail = self._market_budget_available("US")
                        _t2_small, _t2_min = self._is_order_size_too_small("US", _t2_qty, _t2_cost, _t2_budget)
                        _t2_affordability = self._affordability_diag(
                            price_krw=float(_t2_risk_price),
                            qty=int(_t2_qty),
                            order_cost_krw=float(_t2_cost),
                            order_budget_krw=float(_t2_budget),
                            available_budget_krw=float(_t2_avail),
                            cash_krw=float(self.risk.cash),
                            min_effective_order_krw=float(_t2_min),
                        )
                        if _t2_small:
                            self._bump_runtime_reason("US", _t2_ticker, "order_size_too_small")
                            analysis_log.info(
                                f"[skip US] {_t2_ticker} order_size_too_small",
                                extra={"extra": {
                                    "event": "entry_skip", "market": "US", "ticker": _t2_ticker,
                                    "reason": "order_size_too_small", "strategy": "sector_play",
                                    "mode": mode, "price": _t2_price, "risk_price_krw": _t2_risk_price,
                                    "qty": int(_t2_qty), "order_cost_krw": float(_t2_cost),
                                    "order_budget_krw": float(_t2_budget),
                                    "min_effective_order_krw": float(_t2_min),
                                    **_t2_affordability,
                                }},
                            )
                            log.info(
                                f"  [Tier2] {_t2_ticker} order_size_too_small "
                                f"({_t2_cost:,.0f}??< {_t2_min:,.0f}?? budget={_t2_budget:,.0f}?? - skip"
                            )
                            continue
                        if _t2_qty <= 0:
                            self._bump_runtime_reason("US", _t2_ticker, "affordability_fail")
                            self._record_decision_event(
                                "US",
                                "buy_skipped",
                                _t2_ticker,
                                strategy="sector_play",
                                qty=0,
                                price_native=float(_t2_price),
                                price_krw=float(_t2_risk_price),
                                reason="qty_zero",
                                reason_family="affordability",
                                detail="tier2 risk engine returned zero quantity",
                                selected_reason=f"sector_etf={_play['etf']}",
                                **_t2_affordability,
                            )
                            analysis_log.info(
                                f"[skip US] {_t2_ticker} qty_zero",
                                extra={"extra": {
                                    "event": "entry_skip", "market": "US", "ticker": _t2_ticker,
                                    "reason": "qty_zero", "strategy": "sector_play",
                                    "mode": mode, "price": _t2_price, "risk_price_krw": _t2_risk_price,
                                    "qty": int(_t2_qty), "order_cost_krw": float(_t2_cost),
                                    **_t2_affordability,
                                }},
                            )
                            continue
                        if _t2_cost > self.risk.cash:
                            self._record_decision_event(
                                "US",
                                "buy_skipped",
                                _t2_ticker,
                                strategy="sector_play",
                                qty=int(_t2_qty),
                                price_native=float(_t2_price),
                                price_krw=float(_t2_risk_price),
                                reason="insufficient_cash",
                                reason_family="affordability",
                                detail=f"cost={_t2_cost:,.0f} cash={self.risk.cash:,.0f}",
                                selected_reason=f"sector_etf={_play['etf']}",
                                **_t2_affordability,
                            )
                            log.debug(f"  [Tier2] {_t2_ticker} ?꾧툑 遺議?({_t2_cost:,.0f}??")
                            continue
                        _tier2_submit_gate = self._new_buy_block_state("US", _t2_ticker, "sector_play")
                        if not bool(_tier2_submit_gate.get("allowed", True)):
                            self._record_new_buy_block(
                                "US",
                                _t2_ticker,
                                "sector_play",
                                _tier2_submit_gate,
                                qty=_t2_qty,
                                price_native=float(_t2_price),
                                price_krw=_t2_risk_price,
                                selected_reason=f"sector_etf={_play['etf']}",
                                stage="tier2_submit_precheck",
                            )
                            continue
                        _t2_order_px = self._compute_order_price("buy", "US", float(_t2_price))
                        _t2_pre = precheck_order(
                            _t2_ticker, _t2_qty, _t2_order_px, "buy", self.token, market="US"
                        )
                        if not _t2_pre.get("ok"):
                            log.info(f"  [Tier2] {_t2_ticker} precheck ?ㅽ뙣: {_t2_pre.get('msg')}")
                            continue
                        _t2_result = place_order(
                            _t2_ticker, _t2_qty, _t2_order_px, "buy", self.token, market="US"
                        )
                        if not _t2_result["success"]:
                            log.error(f"  [Tier2] {_t2_ticker} 二쇰Ц?ㅽ뙣: {_t2_result.get('msg')}")
                            continue
                        log.info(
                            f"[Tier2 BUY] {_t2_ticker} {_t2_qty}二?@{_t2_price} "
                            f"| ETF={_play['etf']} {_play['etf_chg']:+.2f}% "
                            f"| conf={_play['confidence']:.2f} | {_play['reason']}"
                        )
                        self._record_decision_event(
                            "US", "buy_order", _t2_ticker,
                            strategy="sector_play",
                            qty=_t2_qty,
                            price_native=float(_t2_price),
                            price_krw=_t2_risk_price,
                            reason=f"Tier2 ?뱁꽣?뚮젅??[{_play['etf']} {_play['etf_chg']:+.2f}%]",
                            detail=_play["reason"],
                            order_no=_t2_result.get("order_no", ""),
                            selected_reason=f"sector_etf={_play['etf']}",
                        )
                        _t2_reference_meta = self._entry_reference_metadata("US", _t2_ticker)
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
                            **_t2_reference_meta,
                        })
                        self._block_entry(_t2_ticker, _BUY_COOLDOWN_MIN, "buy_placed")
                        buy_order_alert(
                            market="US",
                            ticker=_t2_ticker,
                            qty=_t2_qty,
                            detail=f"{_play['etf']} {_play['etf_chg']:+.2f}% ?뱁꽣媛뺤꽭 | conf={_play['confidence']:.2f} | {_play['reason']}",
                            name=str(_t2_price_info.get("name", "") or "").strip(),
                            buy_path="path_a",
                        )
                    except Exception as _t2_e:
                        log.error(f"[Tier2] {_t2_ticker} ?ㅻ쪟: {_t2_e}")
            except StopIteration:
                pass
            except Exception as _t2_loop_e:
                log.error(f"[Tier2 ?뱁꽣?뚮젅?? 猷⑦봽 ?ㅻ쪟: {_t2_loop_e}")
        # ?? KR Tier 2 ?뱁꽣 ?뚮젅???????????????????????????????????????????????
        if market == "KR" and mode not in ("HALT", "DEFENSIVE", "CAUTIOUS_BEAR"):
            try:
                _kr_tier2_market_gate = self._new_buy_block_state("KR", strategy="kr_sector_play")
                if not bool(_kr_tier2_market_gate.get("allowed", True)):
                    self._record_new_buy_block("KR", "", "kr_sector_play", _kr_tier2_market_gate, stage="tier2_market_preflight")
                    raise StopIteration
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
                        _kr_tier2_ticker_gate = self._new_buy_block_state("KR", _t2_ticker, "kr_sector_play")
                        if not bool(_kr_tier2_ticker_gate.get("allowed", True)):
                            self._record_new_buy_block(
                                "KR", _t2_ticker, "kr_sector_play", _kr_tier2_ticker_gate, stage="tier2_ticker_preflight"
                            )
                            continue
                        if any(p["ticker"] == _t2_ticker for p in self.risk.positions):
                            log.debug(f"  [KR Tier2] {_t2_ticker} ?대? ?ъ???蹂댁쑀 ???ㅽ궢")
                            continue
                        if self._is_entry_blocked(_t2_ticker):
                            log.debug(f"  [KR Tier2] {_t2_ticker} 吏꾩엯 荑⑤떎?????ㅽ궢")
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
                        _t2_budget = self.risk.calc_order_budget(_t2_size)
                        _t2_qty = self.risk.calc_order_size(
                            _t2_risk_price, _t2_size, _t2_sl
                        )
                        _t2_cost = _t2_qty * _t2_risk_price if _t2_qty > 0 else 0.0
                        _t2_avail = self._market_budget_available("KR")
                        _t2_small, _t2_min = self._is_order_size_too_small("KR", _t2_qty, _t2_cost, _t2_budget)
                        _t2_affordability = self._affordability_diag(
                            price_krw=float(_t2_risk_price),
                            qty=int(_t2_qty),
                            order_cost_krw=float(_t2_cost),
                            order_budget_krw=float(_t2_budget),
                            available_budget_krw=float(_t2_avail),
                            cash_krw=float(self.risk.cash),
                            min_effective_order_krw=float(_t2_min),
                        )
                        if _t2_small:
                            self._bump_runtime_reason("KR", _t2_ticker, "order_size_too_small")
                            analysis_log.info(
                                f"[skip KR] {_t2_ticker} order_size_too_small",
                                extra={"extra": {
                                    "event": "entry_skip", "market": "KR", "ticker": _t2_ticker,
                                    "reason": "order_size_too_small", "strategy": "kr_sector_play",
                                    "mode": mode, "price": _t2_risk_price, "risk_price_krw": _t2_risk_price,
                                    "qty": int(_t2_qty), "order_cost_krw": float(_t2_cost),
                                    "order_budget_krw": float(_t2_budget),
                                    "min_effective_order_krw": float(_t2_min),
                                    **_t2_affordability,
                                }},
                            )
                            log.info(
                                f"  [KR Tier2] {_t2_ticker} order_size_too_small "
                                f"({_t2_cost:,.0f}??< {_t2_min:,.0f}?? budget={_t2_budget:,.0f}?? - skip"
                            )
                            continue
                        if _t2_qty <= 0:
                            self._bump_runtime_reason("KR", _t2_ticker, "affordability_fail")
                            self._record_decision_event(
                                "KR",
                                "buy_skipped",
                                _t2_ticker,
                                strategy="kr_sector_play",
                                qty=0,
                                price_native=float(_t2_risk_price),
                                price_krw=float(_t2_risk_price),
                                reason="qty_zero",
                                reason_family="affordability",
                                detail="tier2 risk engine returned zero quantity",
                                selected_reason=f"kr_sector_etf={_play['etf']}",
                                **_t2_affordability,
                            )
                            analysis_log.info(
                                f"[skip KR] {_t2_ticker} qty_zero",
                                extra={"extra": {
                                    "event": "entry_skip", "market": "KR", "ticker": _t2_ticker,
                                    "reason": "qty_zero", "strategy": "kr_sector_play",
                                    "mode": mode, "price": _t2_risk_price, "risk_price_krw": _t2_risk_price,
                                    "qty": int(_t2_qty), "order_cost_krw": float(_t2_cost),
                                    **_t2_affordability,
                                }},
                            )
                            continue
                        _kr_tier2_submit_gate = self._new_buy_block_state("KR", _t2_ticker, "kr_sector_play")
                        if not bool(_kr_tier2_submit_gate.get("allowed", True)):
                            self._record_new_buy_block(
                                "KR",
                                _t2_ticker,
                                "kr_sector_play",
                                _kr_tier2_submit_gate,
                                qty=_t2_qty,
                                price_native=float(_t2_risk_price),
                                price_krw=float(_t2_risk_price),
                                selected_reason=f"kr_sector_etf={_play['etf']}",
                                stage="tier2_submit_precheck",
                            )
                            continue
                        _t2_result = place_order(
                            _t2_ticker, _t2_qty, 0, "buy",
                            self.token, market="KR",
                        )
                        if not _t2_result["success"]:
                            log.error(f"  [KR Tier2] {_t2_ticker} 二쇰Ц?ㅽ뙣: {_t2_result.get('msg')}")
                            continue
                        log.info(
                            f"[KR Tier2 BUY] {_t2_ticker} {_t2_qty}二?@{_t2_risk_price:,}??"
                            f"| ETF={_play['etf']} {_play['etf_chg']:+.2f}% "
                            f"| conf={_play['confidence']:.2f} | {_play['reason']}"
                        )
                        self._record_decision_event(
                            "KR", "buy_order", _t2_ticker,
                            strategy="kr_sector_play",
                            qty=_t2_qty,
                            price_native=float(_t2_risk_price),
                            price_krw=_t2_risk_price,
                            reason=f"KR Tier2 ?뱁꽣?뚮젅??[{_play['etf']} {_play['etf_chg']:+.2f}%]",
                            detail=_play["reason"],
                            order_no=_t2_result.get("order_no", ""),
                            selected_reason=f"kr_sector_etf={_play['etf']}",
                        )
                        _t2_reference_meta = self._entry_reference_metadata("KR", _t2_ticker)
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
                            **_t2_reference_meta,
                        })
                        self._block_entry(_t2_ticker, _BUY_COOLDOWN_MIN, "buy_placed")
                        buy_order_alert(
                            market="KR",
                            ticker=_t2_ticker,
                            qty=_t2_qty,
                            detail=f"{_play['etf']} {_play['etf_chg']:+.2f}% ?뱁꽣媛뺤꽭 | conf={_play['confidence']:.2f} | {_play['reason']}",
                            name=str(_t2_price_info.get("name", "") or "").strip(),
                            buy_path="path_a",
                        )
                    except Exception as _t2_e:
                        log.error(f"[KR Tier2] {_t2_ticker} ?ㅻ쪟: {_t2_e}")
            except StopIteration:
                pass
            except Exception as _t2_loop_e:
                log.error(f"[KR Tier2 ?뱁꽣?뚮젅?? 猷⑦봽 ?ㅻ쪟: {_t2_loop_e}")
        # ?? Tier 2 ?뱁꽣 ?뚮젅??????????????????????????????????????????????????
        log.info(
            f"[{market} ?ъ씠???꾨즺] ?ъ???{len(self.risk.positions)}媛?| "
            f"cash:{self.risk.cash:,.0f} KRW"
        )
        self._write_live_status(market)
        self._maybe_push_dashboard()  # ?ъ???P&L 蹂???덉쑝硫??붾젅洹몃옩 ?꾩넚
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
        # 吏??蹂?숇쪧 ?덉뒪?좊━ 湲곕줉 (30遺?湲곗슱湲?怨꾩궛??
        _idx_now = get_index_change(market)
        _hist    = self._index_history[market]
        _hist.append(_idx_now)
        # 吏곸쟾 ?쒕떇 ?鍮?湲곗슱湲? ?꾩옱 - 30遺???(踰꾪띁 2媛??댁긽 ?덉쓣 ?뚮쭔)
        _slope_30m = round(_idx_now - _hist[-2], 2) if len(_hist) >= 2 else None
        def _pos_pnl(p):
            entry = float(p.get("entry", 0) or 0)
            cp    = float(p.get("current_price", entry) or entry)
            return round((cp / entry - 1) * 100, 2) if entry else 0.0
        try:
            _vol_trend = get_market_vol_trend(market)
        except Exception:
            _vol_trend = "normal"
        _tune_mode = (self.today_judgment.get("consensus", {}) or {}).get("mode", "NEUTRAL")
        _morning_breadth = ((self.today_judgment.get("digest_raw") or {}).get("breadth_summary") or {})
        _current_breadth = self._build_current_breadth_summary(market, _tune_mode)
        _prev_tune_result = self._last_tune_result.get(market, {}) or {}
        current_state = {
            "index_change":    _idx_now,
            "index_slope_30m": _slope_30m,   # None = 泥??쒕떇 (?댁쟾 湲곗?媛??놁쓬)
            "volume_trend":    _vol_trend,
            "morning_breadth": _morning_breadth,
            "current_breadth": _current_breadth,
            "previous_tune_action": _prev_tune_result.get("action", "N/A"),
            "maintain_streak": self._tune_maintain_streak.get(market, 0),
            "positions": [
                {
                    "ticker":        p["ticker"],
                    "qty":           p["qty"],
                    "entry":         p.get("entry", 0),
                    "current_price": p.get("current_price", p.get("entry", 0)),
                    "pnl_pct":       _pos_pnl(p),
                    "strategy":      p.get("strategy", "-"),
                    "sl":            p.get("sl", 0),
                    "tp":            p.get("tp", 0),
                }
                for p in self.risk.positions
            ],
            "alerts": [],
            "runtime_overrides": self._runtime_overrides(market),
            "execution_profile": self._build_execution_profile_text(
                market,
                _tune_mode,
            ),
            "ops_review_context": self._format_ops_review_context(market),
        }
        brain_summary, _ = self._brain_context_for_judge(market)
        result = tune(market, elapsed, current_state, self.today_judgment, brain_summary)
        runtime_override_changes = self._apply_runtime_tuning_adjustments(market, result)
        log.info(f"[runtime gates {market}] source=tuning_{elapsed}m {self._runtime_gate_state_text(market)}")
        action = result.get("action", "MAINTAIN")
        self._last_tune_result[market] = dict(result or {})
        self._tune_maintain_streak[market] = (
            self._tune_maintain_streak.get(market, 0) + 1
            if action == "MAINTAIN"
            else 0
        )
        if action != "MAINTAIN":
            old_mode = self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL")
            new_mode = result.get("mode", old_mode)
            self.today_judgment.setdefault("consensus", {})["mode"] = new_mode
            # Refresh Claude parameter review when the consensus mode changes.
            if old_mode != new_mode:
                try:
                    self._run_param_review(
                        market,
                        trigger="reverse" if action == "REVERSE" else "mode_change",
                    )
                except Exception as _pt_e:
                    log.warning(f"[param_tuner mode_change] {market}: {_pt_e}")
            # 紐⑤뱶 蹂寃???醫낅ぉ ?ъ꽑??(?? REVERSE??_reinvoke?먯꽌 泥섎━)
            if old_mode != new_mode and action != "REVERSE":
                try:
                    log.info(f"[ticker refresh] {old_mode}->{new_mode} mode changed; rescreening")
                    if market == "KR":
                        tune_cands = self._screen_market_candidates("KR", self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
                    else:
                        tune_cands = self._screen_market_candidates("US", self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
                    if tune_cands:
                        self._log_screen_candidates(market, tune_cands, "tuning_rescreen")
                        tune_cands = self._filter_candidates_by_history(tune_cands, market)
                        tune_cands = self._restrict_candidates_to_universe(
                            tune_cands,
                            (self.today_universe.get(market) or {}).get("tickers")
                            or self.today_judgment.get("universe_tickers", []),
                        )
                        tune_cands = self._annotate_selection_execution_features(market, tune_cands, new_mode)
                        digest_p = self.today_judgment.get("digest_prompt", "")
                        _tune_intraday_ctx = self._build_intraday_context(market)
                        _tune_lesson_context = self._load_lesson_candidate_summary(market)
                        tune_tickers, tune_reasons = select_tickers(market, digest_p, new_mode, tune_cands,
                                                                    lesson_context=_tune_lesson_context,
                                                                    intraday_context=_tune_intraday_ctx,
                                                                    market_change_pct=self._get_market_change_pct(market),
                                                                    secondary_change_pct=self._get_secondary_change_pct(market))
                        tune_meta = self._apply_selection_meta(market, tune_tickers)
                        self.today_tickers[market] = tune_tickers
                        self._entry_timing_mark_candidates(market, tune_tickers, "tuning_rescreen")
                        self.today_ticker_reasons[market] = tune_reasons or {}
                        self.today_judgment["tickers"] = tune_tickers
                        self.today_judgment["universe_tickers"] = [
                            c.get("ticker") for c in tune_cands if c.get("ticker")
                        ]
                        log.info(
                            f"[튜너 종목갱신 완료] {market}: watch={tune_tickers} "
                            f"trade_ready={tune_meta.get('trade_ready', [])}"
                        )
                        tune_excluded = [c.get("ticker", "") for c in tune_cands if c.get("ticker", "") not in tune_tickers]
                        _mode_order_limit = self.risk.calc_order_budget(self.today_judgment.get("consensus", {}).get("size", 50))
                        watchlist_alert(market, new_mode, tune_tickers, tune_reasons, tune_excluded, trigger="rescreen",
                                        mode_order_limit_krw=_mode_order_limit)
                except Exception as te:
                    log.warning(f"[튜너 종목갱신 실패] {te}")
            sl_adj = result.get("sl_adj", 0)
            if sl_adj != 0:
                adj_clamped = max(-0.10, min(0.10, float(sl_adj)))  # 짹10% ?대궡濡??쒗븳
                for pos in self.risk.positions:
                    pos["sl"] = pos["sl"] * (1 + adj_clamped)
                log.info(f"SL adjusted: {adj_clamped:+.3f}")
            # REVERSE: Claude媛 ?μ꽭 諛섏쟾 ?먮떒 ??蹂댁쑀 ?ъ???泥?궛
            # broker_sync ?꾨왂? hold_advisor媛 媛쒕퀎 ?먮떒?섎?濡?REVERSE ??곸뿉???쒖쇅
            if action == "REVERSE" and self.risk.positions:
                reverse_targets = [p for p in self.risk.positions if p.get("strategy") != "broker_sync"]
                skipped = [p["ticker"] for p in self.risk.positions if p.get("strategy") == "broker_sync"]
                if skipped:
                    log.info(f"[REVERSE] broker_sync ?ъ????쒖쇅 (hold_advisor 愿??: {skipped}")
                log.warning(f"[REVERSE] ?쒕꼫 ?먮떒: {result.get('reason','')} ??{len(reverse_targets)}媛??ъ???泥?궛")
                for pos in reverse_targets:
                    cp = self.price_cache.get(pos["ticker"], pos["current_price"])
                    alert_cp = float(cp or 0)
                    if market == "US":
                        raw_cp = float(self.price_cache_raw.get(pos["ticker"], 0) or 0)
                        if raw_cp > 0:
                            alert_cp = raw_cp
                        elif alert_cp > 0 and self.usd_krw_rate > 0:
                            alert_cp = alert_cp / float(self.usd_krw_rate)
                    if not self.is_paper:
                        place_order(pos["ticker"], pos["qty"], 0, "sell",
                                    self.token, market=self._ticker_market(pos["ticker"]))
                    ex = self.risk.close_position(pos["ticker"], cp, "tuner_reverse")
                    if ex:
                        ex_name = str(ex.get("name", "") or "").strip() or self._lookup_ticker_name(ex["ticker"], market)
                        pnl_alert(ex["ticker"], ex["pnl_pct"], int(ex["pnl"]), "tuner_reverse", market=market, name=ex_name, usd_krw=self.usd_krw_rate)
                        trade_alert("sell", ex["ticker"], ex["qty"], alert_cp,
                                    ex["strategy"], 0, 0, reason="tuner_reverse", market=market, name=ex_name, usd_krw=self.usd_krw_rate)
            self._persist_live_judgment(market)
        warning = result.get("warning")
        # 紐⑤뱶媛 諛붾?寃쎌슦留?tuning_report ?꾩넚, ?좎?硫??ㅽ궢
        if action != "MAINTAIN":
            tuning_report(elapsed, result, old_mode, self.risk.positions)
            self._maybe_push_dashboard(force=True)
        else:
            if warning == "OVERLOADED":
                log.warning(
                    f"[?쒕떇 {elapsed}遺? Claude 怨쇰??섎줈 ?쒕떇 ?앸왂 ??湲곗〈 紐⑤뱶 ?좎?"
                )
                tuning_report(elapsed, result, self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"), self.risk.positions)
            else:
                log.info(f"[?쒕떇 {elapsed}遺? MAINTAIN ???붾젅洹몃옩 ?ㅽ궢")
        # ?몄뀡 ?대깽??湲곕줉 (?뚯씤?쒕떇 ?곗씠????MAINTAIN ?ы븿 ?꾩껜 湲곕줉)
        if action == "MAINTAIN" and runtime_override_changes:
            self._persist_live_judgment(market)
            self._maybe_push_dashboard(force=True)
        self._session_events.append({
            "type":    "tuning",
            "elapsed": elapsed,
            "action":  result.get("action", "MAINTAIN"),
            "mode":    result.get("mode", ""),
            "reason":  result.get("reason", ""),
            "warning": result.get("warning"),
            "index_change": current_state.get("index_change", 0),
            "runtime_override_changes": runtime_override_changes,
            "runtime_overrides": self._runtime_overrides(market),
        })
        # ?? 湲닿툒 ?ы뙋??議곌굔 泥댄겕 ??????????????????????????????????????????????
        should_reinvoke, trigger = self._should_reinvoke_analysts(result, current_state)
        if should_reinvoke:
            self._reinvoke_analysts(market, trigger)
        # ?? 120遺꾨쭏??遺遺??ъ꽑??(湲닿툒?ы뙋?④낵 ?낅┰, 紐⑤뱶 ?좎? ?쒕룄 ?ы븿) ??
        if elapsed % 120 == 0 and elapsed >= 120 and not should_reinvoke:
            try:
                self._partial_reselect(market)
            except Exception as _pre:
                log.warning(f"[遺遺꾧탳泥??ㅻ쪟] {market}: {_pre}")
        self._leave_market_task(market, "run_tuning")
    # ?? 湲닿툒 ?ы뙋?????????????????????????????????????????????????????????????
    # 遺꾩꽍媛 ?ы닾??議곌굔 (?섎굹?쇰룄 ?대떦?섎㈃ True)
    _REINVOKE_INDEX_THRESHOLD = -2.0   # 吏??-2% ?댁긽 湲됰씫
    _REINVOKE_COOLDOWN_CYCLES = 2      # 理쒖냼 2?ъ씠??60遺? 媛꾧꺽
    _REINVOKE_SAME_MODE_EXTRA = 2      # 媛숈? 紐⑤뱶 ?좎? ???щ컻????異붽? ?湲??ъ씠??
    # ?? ?덉뒪?좊━ 蹂닿컯 ?????????????????????????????????????????????????????????
    def _prefill_history_sync(
        self, candidates: list, market: str,
        max_tickers: int = 8, timeout_sec: float = 20.0,
    ) -> list:
        """
        session_open 吏곸쟾 吏㏃? ?숆린 蹂닿컯.
        ?곗씠??遺議?醫낅ぉ 以??곸쐞 max_tickers媛쒕? timeout_sec ?대궡??利됱떆 蹂닿컯?섍퀬
        ?섎㉧吏??諛깃렇?쇱슫???먮줈 ?섍릿??
        諛섑솚媛? ?먮낯 candidates 洹몃?濡?(CSV 媛깆떊 + 罹먯떆 臾댄슚?붾쭔 ?섑뻾).
        ?댄썑 _filter_candidates_by_history媛 ?ㅼ떆 ?꾪꽣留곹븯硫?蹂닿컯??醫낅ぉ???듦낵.
        """
        import pandas as _pd
        from pathlib import Path as _Path
        _price_base = _Path(__file__).parent / "data" / "price"
        _MIN = self._MIN_SIGNAL_ROWS
        # ?앸퀎: on-demand fetch ?놁씠 硫붾え由?罹먯떆 ??CSV ???섎쭔 鍮좊Ⅴ寃??뺤씤
        # (API ?몄텧 ?좊컻 湲덉? ??timeout ?꾩뿉 ?앸퀎留뚯쑝濡??쒓컙 ?뚯쭊 諛⑹?)
        shortage = []
        for c in candidates:
            ticker = c.get("ticker", "")
            if not ticker:
                continue
            # 1?쒖쐞: 硫붾え由?罹먯떆
            cached = self._ohlcv_cache.get(ticker)
            if cached is not None:
                if len(cached) >= _MIN + 60:
                    continue
                if self._signal_usable_rows(cached) >= _MIN:
                    continue
            # 2?쒖쐞: CSV ????吏곸젒 ?뺤씤 (?ㅻ뜑 ?ы븿 N+1?됰쭔 ?쎌쓬)
            _csv = _price_base / market.lower() / f"{market.lower()}_{ticker}.csv"
            if _csv.exists():
                try:
                    n = sum(1 for _ in open(_csv, encoding="utf-8-sig")) - 1  # ?ㅻ뜑 ?쒖쇅
                    if n >= _MIN + 60:   # ma60 warm-up 踰꾪띁 ?ы븿 ??usable row ?뺣낫
                        continue
                except Exception:
                    pass
            shortage.append(ticker)
        if not shortage:
            return candidates
        to_fill = shortage[:max_tickers]
        rest    = shortage[max_tickers:]
        log.info(
            f"[?숆린 蹂닿컯] {market} 遺議?{len(shortage)}媛?以?{len(to_fill)}媛?利됱떆 ?쒕룄"
            f" (timeout={timeout_sec}s)"
        )
        def _fill_one(ticker: str) -> tuple:
            """
            諛섑솚: (ticker, status)
              "ok"                  ?????+ usable rows >= _MIN_SIGNAL_ROWS
              "saved_insufficient"  ??????깃났, 洹몃윭??usable rows 遺議?              "failed"              ??????ㅽ뙣
            """
            try:
                _csv_path = _price_base / market.lower() / f"{market.lower()}_{ticker}.csv"
                df, usable, source = self._fetch_signal_ready_ohlcv(
                    ticker,
                    market,
                    lookback_days=max(500, _MIN + 120),
                )
                if df.empty or len(df) < 10:
                    return ticker, "failed"
                _csv_path.parent.mkdir(parents=True, exist_ok=True)
                if _csv_path.exists():
                    existing = _pd.read_csv(_csv_path, parse_dates=["date"])
                    existing.columns = [c_.lower() for c_ in existing.columns]
                    df = self._merge_ohlcv_frames(existing, df)
                df.to_csv(_csv_path, index=False, encoding="utf-8-sig")
                self._ohlcv_cache.pop(ticker, None)
                self._ohlcv_cache_time.pop(ticker, None)
                self._hist_fill_last_ts[ticker] = time.time()
                # ??????ㅼ젣 ?좏샇 怨꾩궛 媛???щ? ?뺤씤
                if usable >= self._MIN_SIGNAL_ROWS:
                    return ticker, f"ok({source},{usable}usable/{len(df)}raw)"
                return ticker, f"saved_insufficient({source},{usable}usable/{len(df)}raw)"
            except Exception as e:
                log.debug(f"[?숆린 蹂닿컯 ?ㅽ뙣] {ticker}: {e}")
                return ticker, "failed"
        # daemon thread + result queue ??timeout ??利됱떆 諛섑솚 (executor wait 釉붾줉 ?놁쓬)
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
                log.info(f"[?숆린 蹂닿컯] {market} timeout {timeout_sec}s ???꾨즺遺꾨쭔 諛섏쁺")
                break
            try:
                ticker, status = result_q.get(timeout=remaining)
                if status.startswith("ok"):
                    filled_ok.add(ticker)
                    log.info(f"[?숆린 蹂닿컯 ?좏샇媛?? {market} {ticker}")
                elif status.startswith("saved_insufficient"):
                    log.info(f"[?숆린 蹂닿컯 ??μ셿猷??ъ쟾?덈?議? {market} {ticker} {status}")
                    self._hist_fill_enqueue(ticker, market)
                else:
                    log.debug(f"[?숆린 蹂닿컯 ?ㅽ뙣] {market} {ticker}")
            except _q.Empty:
                log.info(f"[?숆린 蹂닿컯] {market} timeout {timeout_sec}s ???꾨즺遺꾨쭔 諛섏쁺")
                break
        # timeout ??誘몄닔??+ 踰붿쐞 珥덇낵 醫낅ぉ? 諛깃렇?쇱슫???먮줈
        for t in to_fill:
            if t not in filled_ok:
                self._hist_fill_enqueue(t, market)
        for t in rest:
            self._hist_fill_enqueue(t, market)
        not_filled = len(to_fill) - len(filled_ok)
        log.info(
            f"[history backfill result] {market} success {len(filled_ok)} / failed {not_filled}"
            f" / background pending {len(rest) + not_filled}"
        )
        return candidates   # 援ъ“ 蹂寃??놁씠 諛섑솚, _filter媛 ?ы뙋??
    def _hist_fill_enqueue(self, ticker: str, market: str):
        """?덉뒪?좊━ 蹂닿컯 ???깅줉 ??以묐났/荑⑤떎??諛⑹?"""
        if ticker in self._hist_fill_queued or ticker in self._hist_fill_inflight:
            return
        last_ts = self._hist_fill_last_ts.get(ticker, 0)
        if time.time() - last_ts < 3 * 86400:
            return
        self._hist_fill_queued.add(ticker)
        self._hist_fill_queue.put((ticker, market))
        log.debug(f"[?덉뒪?좊━ 蹂닿컯 ?? {market} {ticker} ?깅줉")
    def _history_fill_worker(self):
        """諛깃렇?쇱슫???덉뒪?좊━ 蹂닿컯 ?뚯빱 ??OHLCV 遺議?醫낅ぉ??365?쇱튂 ?섏쭛"""
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
                df, usable, source = self._fetch_signal_ready_ohlcv(
                    ticker,
                    market,
                    lookback_days=max(500, self._MIN_SIGNAL_ROWS + 120),
                )
                if df.empty or len(df) < 10:
                    log.debug(f"[history backfill failed] {market} {ticker} rows={len(df)}")
                else:
                    try:
                        _csv_path.parent.mkdir(parents=True, exist_ok=True)
                        if _csv_path.exists():
                            existing = _pd.read_csv(_csv_path, parse_dates=["date"])
                            existing.columns = [c.lower() for c in existing.columns]
                            df = self._merge_ohlcv_frames(existing, df)
                        df.to_csv(_csv_path, index=False, encoding="utf-8-sig")
                        self._ohlcv_cache.pop(ticker, None)
                        self._ohlcv_cache_time.pop(ticker, None)
                        # ??????좏샇 媛???щ? ?뺤씤
                        usable = len(calc_all(df))
                        if usable >= self._MIN_SIGNAL_ROWS:
                            log.info(f"[?덉뒪?좊━ 蹂닿컯 ?좏샇媛?? {market} {ticker} ??{len(df)}raw/{usable}usable")
                        else:
                            log.info(f"[?덉뒪?좊━ 蹂닿컯 ??μ셿猷??ъ쟾?덈?議? {market} {ticker} ??{len(df)}raw/{usable}usable")
                    except Exception as _e:
                        log.debug(f"[?덉뒪?좊━ 蹂닿컯 ????ㅽ뙣] {ticker}: {_e}")
            except Exception as e:
                log.debug(f"[?덉뒪?좊━ 蹂닿컯 ?ㅻ쪟] {ticker}: {e}")
            finally:
                self._hist_fill_inflight.discard(ticker)
                self._hist_fill_queued.discard(ticker)
                self._hist_fill_queue.task_done()
            time.sleep(2)   # rate limit: 醫낅ぉ 媛?2珥?媛꾧꺽
    # ?? 遺遺??ъ꽑?????????????????????????????????????????????????????????????
    def _get_cooldown_excluded(self, market: str) -> set:
        """60遺??ъ쭊??湲덉? ???곗씠?곕?議??ъ쑀??荑⑤떎???쒖쇅 (蹂닿컯 ??蹂듦? ?덉슜)"""
        now = datetime.now(KST).replace(tzinfo=None)
        return {
            e["ticker"]
            for e in self._ticker_exclude_log.get(market, [])
            if e.get("reason", "") != "data_insufficient"
            and (now - e["ts"]).total_seconds() < 3600
        }
    def _partial_reselect(self, market: str):
        """2?쒓컙留덈떎 ?쒖꽦???섏쐞 2媛?理쒕? 3媛? 醫낅ぉ 遺遺?援먯껜"""
        now = datetime.now(KST).replace(tzinfo=None)
        last = self._partial_reselect_last.get(market)
        if last and (now - last).total_seconds() < 115 * 60:
            return
        current_tickers = list(self.today_tickers.get(market, []))
        if not current_tickers:
            return
        # Protect currently held tickers from partial replacement.
        protected = {p["ticker"] for p in self.risk.positions}
        def _score(t: str) -> float:
            """?믪쓣?섎줉 援먯껜 ?곗꽑 (臾댁떊??吏??+ invalid price ?꾩쟻)"""
            return self._partial_replace_score(market, t, protected)
        non_protected = [t for t in current_tickers if t not in protected]
        if not non_protected:
            log.info(f"[遺遺꾧탳泥? {market}: ??醫낅ぉ ?ъ???蹂댁쑀 ???ㅽ궢")
            return
        sorted_inactive = sorted(non_protected, key=_score, reverse=True)
        # Replace two by default, three only when the third is also inactive enough.
        n_replace = 2
        if len(sorted_inactive) >= 3 and _score(sorted_inactive[2]) >= 3:
            n_replace = 3
        replace_out = sorted_inactive[:n_replace]
        # ???꾨낫 ?ㅽ겕由щ떇 + ?덉뒪?좊━ ?꾪꽣
        try:
            if market == "KR":
                new_cands = self._screen_market_candidates("KR", self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
            else:
                new_cands = self._screen_market_candidates("US", self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
        except Exception as e:
            log.warning(f"[遺遺꾧탳泥? {market} ?ㅽ겕由щ떇 ?ㅽ뙣: {e}")
            return
        if not new_cands:
            return
        new_cands = self._filter_candidates_by_history(new_cands, market)
        # ?좎? 醫낅ぉ + 荑⑤떎???쒖쇅
        keep_set = set(current_tickers) - set(replace_out)
        cooldown_set = self._get_cooldown_excluded(market)
        new_cands = [
            c for c in new_cands
            if c.get("ticker") not in keep_set
            and c.get("ticker") not in cooldown_set
        ]
        if not new_cands:
            log.info(f"[遺遺꾧탳泥? {market}: ???꾨낫 ?놁쓬 ???ㅽ궢")
            return
        mode = self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL")
        digest_p = self.today_judgment.get("digest_prompt", "")
        new_cands = self._annotate_selection_execution_features(market, new_cands, mode)
        _partial_intraday_ctx = self._build_intraday_context(market)
        _partial_lesson_context = self._load_lesson_candidate_summary(market)
        new_selected, new_reasons = select_tickers(market, digest_p, mode, new_cands,
                                                    lesson_context=_partial_lesson_context,
                                                    intraday_context=_partial_intraday_ctx,
                                                    market_change_pct=self._get_market_change_pct(market),
                                                    secondary_change_pct=self._get_secondary_change_pct(market))
        new_meta = get_last_selection_meta() or {}
        candidate_trade_ready = self._selection_replace_candidates(new_meta, new_selected)
        candidate_map = {
            (str(c.get("ticker", "")).upper() if market == "US" else str(c.get("ticker", ""))): c
            for c in new_cands
            if c.get("ticker")
        }
        replace_in = self._pick_partial_replace_in(
            market,
            replace_out,
            [t for t in candidate_trade_ready if t not in set(current_tickers)],
            new_meta,
            candidate_map,
            n_replace,
        )
        if replace_in:
            try:
                _pr_date = datetime.now(KST).strftime("%Y-%m-%d")
                _tsdb_ids = tsdb.insert_batch(
                    _pr_date, market, "partial", replace_in, new_cands, new_reasons, mode,
                    selection_meta=new_meta,
                )
                self._tsdb_selection_ids.setdefault(market, {}).update(_tsdb_ids)
            except Exception as _te:
                log.warning(f"[tsdb] insert_batch(partial) failed: {_te}")
        if not replace_in:
            log.info(f"[遺遺꾧탳泥? {market}: ?좏슚 ?좉퇋 醫낅ぉ ?놁쓬 ???ㅽ궢")
            return
        final = [t for t in current_tickers if t not in set(replace_out)] + replace_in
        self.today_tickers[market] = final
        self._entry_timing_mark_candidates(market, replace_in, "partial_reselect")
        self.today_judgment["tickers"] = final
        current_ready = [
            t for t in self.trade_ready_tickers.get(market, [])
            if t in final and t not in set(replace_out)
        ]
        final_ready = list(dict.fromkeys(current_ready + [t for t in replace_in if t in final]))
        existing_meta = self.selection_meta.get(market) or {}
        merged_price_targets = self._merge_selection_price_targets(
            market,
            final_ready,
            existing_meta.get("price_targets") or {},
            new_meta.get("price_targets") or {},
        )
        final_meta = {
            **existing_meta,
            "watchlist": final,
            "trade_ready": final_ready,
            "reasons": {**(self.today_ticker_reasons.get(market, {}) or {}), **(new_reasons or {})},
            "veto": new_meta.get("veto", {}),
            "risk_tags": new_meta.get("risk_tags", {}),
            "recommended_strategy": new_meta.get("recommended_strategy", {}),
            "max_position_pct": new_meta.get("max_position_pct", {}),
            "price_targets": merged_price_targets,
        }
        final_meta = self._normalize_selection_meta_runtime(market, final_meta, final, mode=mode)
        final_meta["price_targets"] = self._merge_selection_price_targets(
            market,
            list(final_meta.get("trade_ready") or []),
            final_meta.get("price_targets") or {},
        )
        self.selection_meta[market] = final_meta
        self.trade_ready_tickers[market] = list(final_meta.get("trade_ready") or [])
        self.today_ticker_reasons[market] = final_meta["reasons"]
        self.today_judgment["selection_meta"] = final_meta
        self.today_judgment["trade_ready_tickers"] = self.trade_ready_tickers[market]
        # 援먯껜??醫낅ぉ 荑⑤떎???깅줉 (60遺?
        for t in replace_out:
            self._ticker_exclude_log.setdefault(market, []).append(
                {"ticker": t, "reason": "partial_replace_no_signal", "ts": now}
            )
        # 2?쒓컙 ?댁긽 ????ぉ ?뺣━
        cutoff = now - timedelta(hours=2)
        self._ticker_exclude_log[market] = [
            e for e in self._ticker_exclude_log[market] if e["ts"] > cutoff
        ]
        self._partial_reselect_last[market] = now
        self._persist_live_judgment(market)
        self._update_candidate_health(market, "partial_reselect", final, final_meta, new_cands)
        # 遺遺꾧탳泥???param_tuner 罹먯떆 媛깆떊 (rescreen ?몃━嫄?
        try:
            self._run_param_review(market, trigger="rescreen")
        except Exception as _pt_e:
            log.debug("[param_tuner rescreen] %s: %s", market, _pt_e)
        out_str = ", ".join(replace_out)
        in_str  = ", ".join(replace_in)
        log.info(f"[遺遺꾧탳泥? {market}: [{out_str}] ??[{in_str}] (臾댁떊??吏??")
        watchlist_change_alert(market, replace_out, replace_in, reason="no_signal_delay")
    def _should_reinvoke_analysts(self, result: dict, current_state: dict) -> tuple:
        """遺꾩꽍媛 ?ы닾???щ?. (bool, trigger_reason)"""
        if not self.is_claude_reinvoke_enabled():
            return False, ""
        # 湲곕낯 荑⑤떎??泥댄겕
        elapsed = self.tuning_count - self._last_reinvoke_tuning
        if elapsed < self._REINVOKE_COOLDOWN_CYCLES:
            return False, ""
        # 議곌굔 1: ?쒕꼫媛 諛⑺뼢 ?꾪솚 沅뚭퀬 ??利됱떆 ?덉슜
        if result.get("action") == "REVERSE":
            return True, f"REVERSE 沅뚭퀬: {result.get('reason', '')[:60]}"
        # 議곌굔 2: 吏??-2% ?댁긽 湲됰씫
        idx_chg = current_state.get("index_change", 0)
        if idx_chg <= self._REINVOKE_INDEX_THRESHOLD:
            # 吏곸쟾 ?ы뙋?⑥씠 媛숈? 紐⑤뱶 ?좎???쇰㈃ 異붽? 荑⑤떎???곸슜
            _prev_mode = self.today_judgment.get("consensus", {}).get("mode", "")
            _last_mode = getattr(self, "_last_reinvoke_result_mode", _prev_mode)
            if _last_mode == _prev_mode and elapsed < self._REINVOKE_COOLDOWN_CYCLES + self._REINVOKE_SAME_MODE_EXTRA:
                log.debug(f"[reinvoke skip] same mode maintained ({_prev_mode}); extra cooldown active")
                return False, ""
            return True, f"吏??湲됰씫 {idx_chg:+.2f}%"
        # 議곌굔 3: ?쒕꼫 寃쎄퀬 諛쒕졊 + TIGHTEN (MAINTAIN?대㈃ ??? ?꾪뿕)
        if result.get("warning") and result.get("action") == "TIGHTEN":
            return True, f"?쒕꼫 寃쎄퀬: {result['warning']}"
        return False, ""
    def _reinvoke_analysts(self, market: str, trigger: str):
        """?댁긽 ?좏샇 媛먯? ??遺꾩꽍媛 3紐?湲닿툒 ?ы샇異????먮떒쨌?⑹쓽 媛깆떊"""
        if not self.is_claude_reinvoke_enabled():
            raise RuntimeError("Claude ?ы뙋??湲곕뒫??OFF ?곹깭?낅땲??")
        # ?????쒓컙 reinvoke 李⑤떒 (?섎룞 紐낅졊 ?쒖쇅)
        if not self._is_market_session_now(market) and "?섎룞" not in trigger:
            log.info(f"[reinvoke ?ㅽ궢] {market} ?????쒓컙 ???몃━嫄? {trigger}")
            return
        current_owner = self._market_task_owner.get(market)
        if current_owner not in (None, "session_open", "run_cycle", "run_tuning", "reinvoke", "housekeeping"):
            log.info(f"[skip {market}] reinvoke busy={current_owner}")
            return
        log.warning(f"[湲닿툒 ?ы뙋???쒖옉] {market} | ?몃━嫄? {trigger}")
        try:
            self.claude_control["last_trigger_at"] = datetime.now(KST).isoformat(timespec="seconds")
            self.claude_control["last_trigger_market"] = market
            self.claude_control["last_trigger_source"] = trigger
            self.claude_control["last_result_status"] = "running"
            self.claude_control["last_error"] = ""
            self._save_claude_control()
            digest_prompt = self.today_judgment.get("digest_prompt", "")
            brain_summary, correction = self._brain_context_for_judge(market)
            portfolio_info = self._build_portfolio_info()
            lesson_context = self._load_lesson_candidate_summary(market)
            new_judgments = get_three_judgments(
                digest_prompt, brain_summary, correction,
                market=market, lesson_context=lesson_context, portfolio_info=portfolio_info,
            )
            new_consensus = self._apply_consensus_guards(
                market,
                new_judgments,
                build_consensus(new_judgments, market=market),
                source=f"reinvoke:{trigger}",
            )
            # VIX ?숈쟻 ?ъ씠利?蹂댁젙 (?ы뙋??寃쎈줈)
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
                        log.info(f"[reinvoke VIX={_vix:.1f}] size {_os}% ??{new_consensus['size']}%")
            old_mode = self.today_judgment.get("consensus", {}).get("mode", "?")
            debate_meta = new_judgments.pop("_debate", {})
            self.today_judgment["judgments"] = new_judgments
            self.today_judgment["consensus"] = new_consensus
            self.today_judgment["round1_judgments"] = debate_meta.get("r1", {})
            self.today_judgment["debate_changes"] = debate_meta.get("changes", [])
            self._last_reinvoke_tuning = self.tuning_count
            self._last_reinvoke_result_mode = new_consensus.get("mode", "")
            judgment_log.info(
                f"[reinvoke {market}] {old_mode} ??{new_consensus['mode']} | {trigger}",
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
                f"[湲닿툒 ?ы뙋???꾨즺] {old_mode} ??{new_consensus['mode']} "
                f"size={new_consensus['size']}%"
            )
            # ?몄뀡 ?대깽??湲곕줉 (湲닿툒 ?ы뙋??
            self._session_events.append({
                "type":          "reinvoke",
                "elapsed":       self.tuning_count * 30,
                "trigger":       trigger,
                "old_mode":      old_mode,
                "new_mode":      new_consensus["mode"],
                "new_judgments": new_judgments,
                "debate_meta":   debate_meta,
            })
            # 紐⑤뱶媛 諛붾뚮㈃ 醫낅ぉ???ъ꽑??(?? DEFENSIVE?묺ODERATE_BULL ???몃쾭??ETF ?쒓굅)
            BEAR_MODES  = {"HALT", "DEFENSIVE", "CAUTIOUS_BEAR", "MILD_BEAR"}
            BULL_MODES  = {"AGGRESSIVE", "MODERATE_BULL", "MILD_BULL"}
            mode_flip = (old_mode in BEAR_MODES and new_consensus["mode"] in BULL_MODES) or \
                        (old_mode in BULL_MODES and new_consensus["mode"] in BEAR_MODES)
            if mode_flip or old_mode != new_consensus["mode"]:
                try:
                    log.info(f"[selection refresh] mode changed {old_mode}->{new_consensus['mode']}; refreshing candidates")
                    if market == "KR":
                        reinvoke_cands = self._screen_market_candidates("KR", self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
                    else:
                        reinvoke_cands = self._screen_market_candidates("US", self.today_judgment.get("consensus", {}).get("mode", "NEUTRAL"))
                    if reinvoke_cands:
                        self._log_screen_candidates(market, reinvoke_cands, "analyst_reinvoke_rescreen")
                        reinvoke_cands = self._filter_candidates_by_history(reinvoke_cands, market)
                        _ri_cooldown = self._get_cooldown_excluded(market)
                        reinvoke_cands = [c for c in reinvoke_cands
                                          if c.get("ticker") not in _ri_cooldown]
                        digest_p = self.today_judgment.get("digest_prompt", "")
                        reinvoke_cands = self._annotate_selection_execution_features(market, reinvoke_cands, new_consensus["mode"])
                        _reinvoke_intraday_ctx = self._build_intraday_context(market)
                        _reinvoke_lesson_context = self._load_lesson_candidate_summary(market)
                        new_tickers, new_ticker_reasons = select_tickers(market, digest_p,
                                                     new_consensus["mode"], reinvoke_cands,
                                                     lesson_context=_reinvoke_lesson_context,
                                                     intraday_context=_reinvoke_intraday_ctx,
                                                     market_change_pct=self._get_market_change_pct(market),
                                                     secondary_change_pct=self._get_secondary_change_pct(market))
                        reinvoke_meta = self._apply_selection_meta(market, new_tickers)
                        self.today_tickers[market] = new_tickers
                        self._entry_timing_mark_candidates(market, new_tickers, "analyst_reinvoke")
                        self.today_ticker_reasons[market] = new_ticker_reasons or {}
                        self.today_judgment["tickers"] = new_tickers
                        self.today_judgment["universe_tickers"] = [
                            c.get("ticker") for c in reinvoke_cands if c.get("ticker")
                        ]
                        log.info(
                            f"[종목 재선정 완료] {market}: watch={new_tickers} "
                            f"trade_ready={reinvoke_meta.get('trade_ready', [])}"
                        )
                        reinvoke_excluded = [c.get("ticker", "") for c in reinvoke_cands if c.get("ticker", "") not in new_tickers]
                        _mode_order_limit = self.risk.calc_order_budget(new_consensus.get("size", 50))
                        watchlist_alert(market, new_consensus["mode"], new_tickers, new_ticker_reasons, reinvoke_excluded, trigger="rescreen",
                                        mode_order_limit_krw=_mode_order_limit)
                        # ?ㅼ쓬 run_cycle遺????醫낅ぉ ?ъ슜 (WS???ㅼ쓬 ?ъ떆????媛깆떊)
                except Exception as te:
                    log.error(f"[종목 재선정 실패] {te}")
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
            log.error(f"[湲닿툒 ?ы뙋???ㅽ뙣] {e}")
    def _build_tg_state(self) -> dict:
        """?꾩옱 ?곹깭 ?ㅻ깄??(蹂??媛먯???"""
        market = self.current_market or ""
        mode   = self.today_judgment.get("consensus", {}).get("mode", "")
        pos_key = tuple(sorted((p["ticker"], p["qty"]) for p in self.risk.positions))
        pnl_bucket = round(self._daily_pnl_pct(market), 1)  # 0.1% ?⑥쐞
        return {
            "market":     market,
            "mode":       mode,
            "pos_key":    pos_key,
            "pnl_bucket": pnl_bucket,
            "cash_m":     round(self.risk.cash / 1_000_000, 2),  # 1留뚯썝 ?⑥쐞
            "pending_n":  len(self.pending_orders),
            "pathb":      getattr(self, "pathb", None).status() if getattr(self, "pathb", None) is not None else {},
        }
    # ?? Claude ?쒕떇??寃곗젙 ?대깽???????????????????????????????????????????
    # entry      : 留ㅼ닔 ?좏샇 諛쒖깮 (Claude ?좏깮 醫낅ぉ)
    # closed     : ?ъ???醫낅즺 ?붿빟 (吏꾩엯~泥?궛 ?꾩껜)
    # hold_outcome: hold_advisor ?먮떒 寃곌낵 (寃곌낵 ?뺤젙 ??
    # ?ㅽ뻾 ?ㅻ쪟(sell_failed ????decisions.jsonl??湲곕줉?섏? ?딆쓬
    _DECISION_TYPES = {"entry", "closed", "hold_outcome"}
    def _record_decision_event(self, market: str, action: str, ticker: str, **kwargs):
        # ?? ?ㅽ뻾 ?ㅻ쪟/?곹깭 ?대깽?몃뒗 decisions.jsonl ?쒖쇅 ???????????????????
        # System execution events are kept out of decisions.jsonl.
        _SKIP_ACTIONS = {
            "sell_failed", "buy_blocked", "buy_skipped",
            "halt", "cooldown", "no_cash",
        }
        # in-memory 濡쒓렇??紐⑤뱺 ?대깽???좎? (紐⑤땲?곕쭅??
        mode = self.today_judgment.get("consensus", {}).get("mode", "")
        event = {
            "timestamp": datetime.now(KST).isoformat(timespec="seconds"),
            "session_date": self._current_session_date_str(market),
            "market": market,
            "action": action,
            "ticker": ticker,
            "mode": mode,
            "strategy": kwargs.get("strategy", ""),
            "source_strategy": kwargs.get("source_strategy", ""),
            "micro_probe": bool(kwargs.get("micro_probe", False)),
            "micro_probe_reason": kwargs.get("micro_probe_reason", ""),
            "qty": int(kwargs.get("qty", 0) or 0),
            "price_native": float(kwargs.get("price_native", 0) or 0),
            "price_krw": float(kwargs.get("price_krw", 0) or 0),
            "reason": kwargs.get("reason", ""),
            "reason_family": kwargs.get("reason_family", ""),
            "detail": kwargs.get("detail", ""),
            "order_no": kwargs.get("order_no", ""),
            "pnl_krw": float(kwargs.get("pnl_krw", 0) or 0),
            "pnl_pct": float(kwargs.get("pnl_pct", 0) or 0),
            "selected_reason": kwargs.get("selected_reason", ""),
            "exit_owner": kwargs.get("exit_owner", ""),
            "strategy_stop_price": float(kwargs.get("strategy_stop_price", 0) or 0),
            "loss_cap_price": float(kwargs.get("loss_cap_price", 0) or 0),
            "effective_stop_price": float(kwargs.get("effective_stop_price", 0) or 0),
            "loss_budget_krw": float(kwargs.get("loss_budget_krw", 0) or 0),
            "profit_floor_price": float(kwargs.get("profit_floor_price", 0) or 0),
            "profit_floor_triggered": bool(kwargs.get("profit_floor_triggered", False)),
            "soft_exit_floor_price": float(kwargs.get("soft_exit_floor_price", 0) or 0),
            "soft_exit_floor_triggered": bool(kwargs.get("soft_exit_floor_triggered", False)),
            "soft_exit_review_action": kwargs.get("soft_exit_review_action", ""),
            "soft_exit_review_reason": kwargs.get("soft_exit_review_reason", ""),
            "soft_exit_review_confidence": float(kwargs.get("soft_exit_review_confidence", 0) or 0),
            "soft_exit_reference_source": kwargs.get("soft_exit_reference_source", ""),
            "soft_exit_reference_target": float(kwargs.get("soft_exit_reference_target", 0) or 0),
            "pathb_reference_target": float(kwargs.get("pathb_reference_target", 0) or 0),
            "selection_reference_target": float(kwargs.get("selection_reference_target", 0) or 0),
            "peak_pnl_pct": float(kwargs.get("peak_pnl_pct", 0) or 0),
            "position_mfe_pct": float(kwargs.get("position_mfe_pct", kwargs.get("peak_pnl_pct", 0)) or 0),
            "position_mae_pct": float(kwargs.get("position_mae_pct", 0) or 0),
            "actual_fill_price": float(kwargs.get("actual_fill_price", 0) or 0),
            "price_per_share_krw": float(kwargs.get("price_per_share_krw", 0) or 0),
            "affordable_1_share_bool": bool(kwargs.get("affordable_1_share_bool", False)),
            "shortfall_krw": float(kwargs.get("shortfall_krw", 0) or 0),
            "affordability_reason": kwargs.get("affordability_reason", ""),
            "order_budget_krw": float(kwargs.get("order_budget_krw", 0) or 0),
            "available_budget_krw": float(kwargs.get("available_budget_krw", 0) or 0),
            "available_cash_krw": float(kwargs.get("available_cash_krw", kwargs.get("cash_krw", 0)) or 0),
            "min_effective_order_krw": float(kwargs.get("min_effective_order_krw", 0) or 0),
        }
        self.decision_event_log.append(event)
        self.decision_event_log = self.decision_event_log[-200:]
        # ?? Claude ?먮떒 ?대깽?몃쭔 decisions.jsonl 湲곕줉 ???????????????????????
        if action not in _SKIP_ACTIONS:
            # Normalize decision event types for decisions.jsonl.
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
                "session_date": event["session_date"],
                "market":    market,
                "ticker":    ticker,
                "mode":      mode,
                "strategy":  event["strategy"],
                "source_strategy": event.get("source_strategy", ""),
                "micro_probe": event.get("micro_probe", False),
                "micro_probe_reason": event.get("micro_probe_reason", ""),
            }
            # entry: 吏꾩엯 而⑦뀓?ㅽ듃
            if action in ("buy_order", "buy_signal"):
                record.update({
                    "entry_price": event["price_krw"] or event["price_native"],
                    "qty":         event["qty"],
                    "selected_reason": event["selected_reason"],
                })
            # closed: 醫낅즺 ?붿빟
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
                    "exit_owner":  event.get("exit_owner", ""),
                    "strategy_stop_price": event.get("strategy_stop_price", 0),
                    "loss_cap_price": event.get("loss_cap_price", 0),
                    "effective_stop_price": event.get("effective_stop_price", 0),
                    "loss_budget_krw": event.get("loss_budget_krw", 0),
                    "profit_floor_price": event.get("profit_floor_price", 0),
                    "profit_floor_triggered": event.get("profit_floor_triggered", False),
                    "soft_exit_floor_price": event.get("soft_exit_floor_price", 0),
                    "soft_exit_floor_triggered": event.get("soft_exit_floor_triggered", False),
                    "soft_exit_review_action": event.get("soft_exit_review_action", ""),
                    "soft_exit_review_reason": event.get("soft_exit_review_reason", ""),
                    "soft_exit_review_confidence": event.get("soft_exit_review_confidence", 0),
                    "soft_exit_reference_source": event.get("soft_exit_reference_source", ""),
                    "soft_exit_reference_target": event.get("soft_exit_reference_target", 0),
                    "pathb_reference_target": event.get("pathb_reference_target", 0),
                    "selection_reference_target": event.get("selection_reference_target", 0),
                    "peak_pnl_pct": event.get("peak_pnl_pct", 0),
                    "position_mfe_pct": event.get("position_mfe_pct", 0),
                    "position_mae_pct": event.get("position_mae_pct", 0),
                    "actual_fill_price": event.get("actual_fill_price", 0),
                })
            # hold_outcome: ?먮떒 寃곌낵
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
                log.debug(f"decisions.jsonl 湲곕줉 ?ㅽ뙣: {_de}")
        try:
            decision_event_alert(event)
        except Exception as e:
            log.warning(f"decision_event_alert failed: {e}")
    def _write_live_status(self, market: str, force: bool = False):
        """?ъ씠?대쭏???쇱씠釉??곹깭瑜?state/live_status_{market}.json ??湲곕줉 (??쒕낫?쒖슜)
        force=True: ?띾룄 ?쒗븳 臾댁떆 (?ъ???湲닿툒 蹂寃???
        """
        if not force:
            _now = time.time()
            if _now - self._live_status_written_at.get(market, 0.0) < _LIVE_STATUS_MIN_INTERVAL:
                return  # 理쒓렐???대? ? ??以묐났 I/O ?ㅽ궢
            self._live_status_written_at[market] = _now
        try:
            status = self.risk.get_status()
            equity_ctx = self._market_equity_reference_context(market)
            broker_state = self._broker_state.get(market, {})
            kis_total_equity = round(float(equity_ctx.get("total_krw", 0) or 0), 0)
            market_cash_krw = round(float(equity_ctx.get("cash_krw", 0) or 0), 0)
            last_trusted_snapshot = dict(broker_state.get("last_trusted_snapshot", {}) or {})
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
                # selected_reason: pending_order ?먮뒗 ?ъ????먯껜?먯꽌 蹂듦뎄
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
                    "source_strategy": pos.get("source_strategy", ""),
                    "path_type":       pos.get("path_type", ""),
                    "pathb_path_run_id": pos.get("pathb_path_run_id", ""),
                    "pathb_plan":      pos.get("pathb_plan", {}),
                    "micro_probe":     bool(pos.get("micro_probe")),
                    "micro_probe_reason": pos.get("micro_probe_reason", ""),
                    "oversize_ratio":  round(float(pos.get("oversize_ratio", 0) or 0), 4),
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
                    "source_strategy": order.get("source_strategy", ""),
                    "path_type": order.get("path_type", ""),
                    "pathb_path_run_id": order.get("pathb_path_run_id", ""),
                    "micro_probe": bool(order.get("micro_probe")),
                    "micro_probe_reason": order.get("micro_probe_reason", ""),
                    "oversize_ratio": round(float(order.get("oversize_ratio", 0) or 0), 4),
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
                    "trading_date":   self._current_session_date_str(market),
                    "mode":           self.today_judgment.get("consensus", {}).get("mode", ""),
                    "mode_size_pct":  self.today_judgment.get("consensus", {}).get("size", 0),
                    "max_order_krw":  round(self.risk.max_order_krw, 0),
                    "session_active": bool(self.session_active and self.current_market == market),
                    "daily_pnl":      round(status.get("daily_pnl", 0), 0),
                    "daily_pnl_pct":  round(self._daily_pnl_pct(market), 4),
                    "cash":           market_cash_krw,
                    "total_equity":   kis_total_equity,
                    "equity_source":  equity_ctx.get("source", ""),
                    "internal_equity": round(float(equity_ctx.get("total_krw", 0) or 0), 0),
                    "last_trusted_equity": round(float(last_trusted_snapshot.get("total_krw", 0) or 0), 0) if last_trusted_snapshot.get("total_krw") is not None else None,
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
                    "pathb": getattr(self, "pathb", None).status() if getattr(self, "pathb", None) is not None else {
                        "enabled": False,
                        "runtime_available": False,
                    },
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
            log.warning(f"live_status ????ㅽ뙣: {e}")
    def _maybe_push_dashboard(self, force: bool = False):
        """?곹깭 蹂???덉쓣 ?뚮쭔 ?붾젅洹몃옩 ??쒕낫???몄떆"""
        if not self.session_active:
            return
        state = self._build_tg_state()
        if not force and state == self._last_tg_state:
            return  # 蹂???놁쓬 ???ㅽ궢
        market    = self.current_market
        mode      = self.today_judgment.get("consensus", {}).get("mode", "?")
        judgments = self.today_judgment.get("judgments", {})
        tickers   = self.today_tickers.get(market, [])
        digest_ctx = (self.today_judgment.get("digest_raw") or {}).get("context") or {}
        risk_value = float(digest_ctx.get("vix", 0) or 0)
        risk_label = "VKOSPI" if market == "KR" else "VIX"
        pnl_pct   = self._daily_pnl_pct(market)
        pnl_krw   = int(self.risk.daily_pnl)
        market_positions = [
            p for p in list(self.risk.positions)
            if self._ticker_market(p.get("ticker", "")) == market
        ]
        equity_ctx = self._market_equity_reference_context(market)
        broker_state = dict(self._broker_state.get(market, {}) or {})
        broker_snapshot = dict(
            broker_state.get("last_snapshot")
            or broker_state.get("last_trusted_snapshot")
            or {}
        )
        stock_value_krw = float(
            broker_snapshot.get("eval_krw")
            if broker_snapshot.get("eval_krw") is not None
            else equity_ctx.get("position_krw", 0)
            or 0
        )
        total_equity_krw = float(equity_ctx.get("total_krw", 0) or 0)
        market_cash_krw = float(equity_ctx.get("cash_krw", 0) or 0)
        dashboard_push(market, mode, market_positions,
                       market_cash_krw, pnl_pct, pnl_krw, judgments, tickers,
                       max_order_krw=int(self.risk.max_order_krw),
                       total_fee=int(self.risk.total_fee),
                       stock_value_krw=stock_value_krw,
                       total_equity_krw=total_equity_krw,
                       risk_value=risk_value,
                       risk_label=risk_label,
                       mode_order_limit_krw=self.risk.calc_order_budget(self.today_judgment.get("consensus", {}).get("size", 50)))
        self._last_tg_state = state
    def _heartbeat(self):
        """1?쒓컙留덈떎 濡쒓렇 + 蹂???놁뼱??媛뺤젣 ?붾젅洹몃옩 ?꾩넚"""
        if not self.session_active:
            return
        market  = self.current_market
        mode    = self.today_judgment.get("consensus", {}).get("mode", "?")
        pos_txt = ", ".join(f"{p['ticker']}({p['qty']}二?" for p in self.risk.positions) or "?놁쓬"
        now_str = datetime.now(KST).strftime("%H:%M")
        log.info(f"[Heartbeat {now_str}] {market} | 紐⑤뱶:{mode} | ?ъ???{pos_txt}")
        self._maybe_push_dashboard(force=True)  # 1?쒓컙留덈떎 媛뺤젣 ?꾩넚
    def session_close(self, market: str):
        _log_flow("info", f"[{market}] session_close")
        if self.current_market != market:
            return
        today = self._current_session_date_str(market)
        # ??留덇컧 吏곸쟾 留덉?留됱쑝濡?釉뚮줈而??붽퀬/泥닿껐???숆린?뷀빐 誘몄껜寃??ㅽ뙋??以꾩씤??
        try:
            broker_kr = self._normalize_broker_balance(get_balance(self.token, market="KR", force_refresh=True), "KR")
            broker_us = self._normalize_broker_balance(get_balance(self.token, market="US", force_refresh=True), "US")
            self._reconcile_pending_orders(broker_kr, broker_us)
        except Exception as e:
            self._flag_execution_issue(market, "session_close_sync_failed")
            _log_risk("warning", f"[{market}] session_close 理쒖쥌 泥닿껐 ?숆린???ㅽ뙣: {e}")
        if getattr(self, "pathb", None) is not None:
            try:
                self.pathb.refresh_broker_truth(market, force=True)
                self._reconcile_broker_open_orders(market, reason="session_close", force=False)
                self.pathb.finalize_sell_pending_at_session_close(market)
                self.pathb.reconcile_filled_positions(market, force=True)
                self.pathb.finalize_carried_positions_at_session_close(market)
                _pathb_unknown_close = self.pathb.finalize_order_unknowns_at_session_close(market)
                if int((_pathb_unknown_close or {}).get("session_end_unresolved", 0) or 0) > 0:
                    _episode_id = self._audit_active_episode(
                        market,
                        episode_type="ORDER_UNKNOWN_SESSION_CLOSE",
                        scope="market",
                        reason="session_end_unresolved",
                        payload={"stage": "session_close", "summary": _pathb_unknown_close},
                    )
                    if _episode_id:
                        self._audit_try_emit(
                            {
                                "kind": "episode",
                                "episode_id": _episode_id,
                                "episode_type": "ORDER_UNKNOWN_SESSION_CLOSE",
                                "market": str(market or "").upper(),
                                "runtime_mode": self._mode,
                                "session_date": self._current_session_date_str(market),
                                "scope": "market",
                                "ended_at": datetime.now(KST).isoformat(timespec="seconds"),
                                "status": "open",
                                "start_reason": "session_end_unresolved",
                                "payload": {"stage": "session_close", "summary": _pathb_unknown_close},
                            }
                        )
                self.pathb.expire_waiting_at_session_close(market)
            except Exception as _pathb_close_e:
                log.error(f"[PathB session_close] {market}: {_pathb_close_e}", exc_info=True)
        self._clear_pending_orders_for_market(market, "session_close")
        self.session_active = False
        self.current_market = None
        if self.ws:
            self.ws.stop()
            self.ws = None
        # ?? ?쇰꼸 吏묎퀎 ???????????????????????????????????????????????????????
        try:
            self._flush_funnel(market)
        except Exception as _fe:
            log.warning(f"[?쇰꼸 ????ㅽ뙣] {market}: {_fe}")
        # ?? ?ъ????뺣━: ??醫낅즺 ?꾩뿉??二쇰Ц?섏? ?딄퀬, ?ㅼ쓬 ?몄뀡 ?됰룞留?寃곗젙 ?????
        market_positions = [
            p for p in list(self.risk.positions)
            if self._ticker_market(p.get("ticker", "")) == market
        ]
        day_trades = [p for p in market_positions if p.get("max_hold", 1) <= 1]
        multi_days = [p for p in market_positions if p.get("max_hold", 1) > 1]
        from telegram_reporter import send as _tg_send
        # day_trade??Claude?먭쾶 癒쇱? 臾쇱뼱蹂????ㅼ쓬 ?몄뀡 ?됰룞 寃곗젙
        claude_review_lines = [f"?뵒 <b>[?λ쭏媛??ъ???寃?? {market}</b>", "?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺"]
        for pos in day_trades:
            ticker = pos.get("ticker", "")
            cp     = self.price_cache.get(ticker, pos.get("current_price", 0))
            entry  = float(pos.get("display_avg_price", pos.get("entry", 0)) or 0)
            pnl    = (cp / entry - 1) * 100 if entry and cp else 0
            is_us  = market == "US"
            px_fmt = lambda v: f"${v:.2f}" if is_us else f"{v:,.0f} KRW"
            action = "SELL"  # 湲곕낯媛? 泥?궛
            reason_txt = ""
            advice = None
            try:
                from minority_report.hold_advisor import ask as advisor_ask
                _d = self.today_judgment.get("digest_prompt", "")
                _ic = self._build_intraday_context(market)
                digest = _d + "\n\n[?μ쨷 ?꾩옱]\n" + _ic if _ic else _d
                advice = advisor_ask(
                    self._advisor_pos(pos, market),
                    market,
                    digest,
                    decision_stage="PRE_CLOSE_CARRY",
                )
                action = advice.get("action", "SELL")
                votes  = advice.get("votes", {})
                for v in votes.values():
                    if v.get("action") == action and v.get("reason"):
                        reason_txt = v["reason"][:100]
                        break
                log.info(f"[?λ쭏媛?寃?? {ticker} Claude ??{action} ({reason_txt[:50]})")
            except Exception as e:
                log.warning(f"[?λ쭏媛?寃?? {ticker} hold_advisor ?ㅽ뙣 ??湲곕낯 泥?궛: {e}")
                action = "SELL"
            action_ko  = "?ㅼ쓬 ?몄뀡 泥?궛 沅뚭퀬" if action == "SELL" else "?댁씪濡??댁썡"
            color_icon = "?뵶" if action == "SELL" else "?윞"
            pnl_icon   = "?윟" if pnl >= 0 else "?뵶"
            block = (
                f"{pnl_icon} <b>{ticker}</b>  {pnl:+.2f}%  {px_fmt(cp)}\n"
                f"  {color_icon} Claude: <b>{action_ko}</b>"
            )
            if reason_txt:
                block += f"\n     ??{reason_txt}"
            claude_review_lines.append(block)
            if action == "SELL":
                for p2 in self.risk.positions:
                    if p2.get("ticker") == ticker:
                        p2["hold_advice"] = advice
                        p2["pending_next_open_sell"] = True
                        p2["pending_next_open_reason"] = reason_txt
                        break
                log.info(f"[session close Claude sell] {ticker} {pnl:+.2f}% queued for next session open")
            else:
                # HOLD/TRAIL ???댁썡: max_hold ?섎（ ?곗옣
                for p2 in self.risk.positions:
                    if p2.get("ticker") == ticker:
                        p2["max_hold"] = max(p2.get("max_hold", 1) + 1, 2)
                        if advice:
                            p2["hold_advice"] = advice
                        p2["pending_next_open_sell"] = False
                        p2["pending_next_open_reason"] = ""
                        break
                multi_days.append(pos)
                log.info(f"[?λ쭏媛?Claude ?댁썡] {ticker} {pnl:+.2f}% ??max_hold ?곗옣")
        if len(claude_review_lines) > 2:
            try:
                _tg_send("\n\n".join(claude_review_lines))
            except Exception:
                pass
        if multi_days:
            log.info(f"[?댁썡] {[p['ticker'] for p in multi_days]} "
                     f"???ㅼ쓬 ?몄뀡 怨꾩냽 蹂댁쑀 (held_days: "
                     f"{[p['held_days'] for p in multi_days]})")
        # ?댁썡 ?ъ????뚯씪 ???(?ъ떆???鍮?
        self._save_positions()
        # ?? ??醫낅즺 ???댁썡 ?ъ???Claude ?먭? ???붾젅洹몃옩 ?꾩넚 ??????????????
        try:
            self._post_session_position_review(market, multi_days)
        except Exception as _pse:
            log.warning(f"[?ν썑 ?ъ???由щ럭 ?ㅻ쪟] {market}: {_pse}")
        session_trades = self._filter_trades_for_market(self.risk.trade_log, market)
        # order_no 湲곗? 以묐났 ?쒓굅 (?ъ떆???깆쑝濡?trade_log???숈씪 泥닿껐??2??湲곕줉?섎뒗 耳?댁뒪)
        _seen_orders: set = set()
        _deduped: list = []
        for t in session_trades:
            key = t.get("order_no") or id(t)
            if key not in _seen_orders:
                _seen_orders.add(key)
                _deduped.append(t)
        if len(_deduped) < len(session_trades):
            log.info(f"[{market}] trade_log dedupe: {len(session_trades)} -> {len(_deduped)}")
        session_trades = _deduped
        # ?? decisions.jsonl ?대갚 癒몄? ?????????????????????????????????????????
        # ?ъ떆?묒쑝濡?trade_log媛 鍮꾧굅??遺遺꾨쭔 ?⑥쓣 寃쎌슦 decisions.jsonl??        # ?뱀씪 closed 嫄곕옒瑜?蹂댁“ ?뚯뒪濡?異붽???留ㅻℓ ?먯옣 ?꾨씫??諛⑹??쒕떎.
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
                    _ts = str(_rec.get("session_date") or str(_rec.get("timestamp", "") or "")[:10] or "")
                    if _ts != today:
                        continue
                    _ono = str(_rec.get("order_no", "") or "").strip()
                    _key = _ono if _ono else f"dec_{_rec.get('ticker','')}_{_rec.get('timestamp','')}"
                    if _key in _seen_orders:
                        continue
                    _seen_orders.add(_key)
                    _usd_krw = float(getattr(self, "usd_krw_rate", 0) or 1350)
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
                log.info(f"[{market}] decisions.jsonl ?대갚 {len(_dec_trades)}嫄?癒몄? (trade_log ?꾨씫 蹂댁셿)")
                session_trades = session_trades + _dec_trades
        except Exception as _dm_e:
            log.warning(f"[{market}] decisions.jsonl 癒몄? ?ㅽ뙣: {_dm_e}")
        _sell_log = [t for t in session_trades if t.get("side") == "sell"]
        execution_health = self._build_execution_health(market, session_trades)
        cumulative_equity = int(round(self._kis_total_equity_krw()))
        actual = {
            "market_change": get_index_change(market),
            "pnl_pct": self._daily_pnl_pct(market),
            "pnl_krw": int(self.risk.daily_pnl),
            "win": self.risk.daily_pnl > 0,
            "trades": len(_sell_log),   # 泥?궛(留ㅻ룄) 嫄댁닔留?            "cumulative": cumulative_equity,
            "execution_contaminated": execution_health["contaminated"],
            "execution_issues": execution_health["reasons"],
        }
        # ?먮떒???녿뒗 ??HALT, ?먮윭 ??? postmortem ?ㅽ궢
        if not self.today_judgment.get("judgments"):
            log.info(f"[{market}] ?ㅻ뒛 ?먮떒 ?놁쓬 ??postmortem ?앸왂")
            pm = {}
            judgment_eval = {}
        else:
            pm = run_postmortem(
                market,
                today,
                self.today_judgment,
                actual,
                self.today_judgment.get("digest_prompt", ""),
                trade_log=session_trades,   # ?대떦 ?쒖옣 泥닿껐 ?댁뿭留??꾨떖
                decision_event_log=[e for e in self.decision_event_log if e.get("market") == market],
            )
        if self.today_judgment.get("judgments"):
            judgment_eval = build_judgment_eval(
                self.today_judgment.get("judgments", {}),
                self.today_judgment.get("consensus", {}),
                actual.get("market_change"),
            )
        self.today_judgment["judgment_eval"] = judgment_eval
        ops_review_snapshot = self._build_ops_review_snapshot(
            market,
            current_record={
                "date": today,
                "market": market,
                "judgment_eval": judgment_eval,
                "actual_result": actual,
            },
        )
        self.today_judgment["ops_review_snapshot"] = ops_review_snapshot
        lesson_candidates = self._persist_lesson_candidates(market, ops_review_snapshot)
        self._persist_live_judgment(market)
        _ops_metrics = ops_review_snapshot.get("metrics", {})
        log.info(
            f"[ops review {market}] "
            f"hit={(_ops_metrics.get('consensus_directional_hit_rate') or {}).get('value', 'N/A')}% "
            f"gap={(_ops_metrics.get('best_analyst_minus_consensus_hit_gap') or {}).get('value', 'N/A')}pp "
            f"tr_conv={(_ops_metrics.get('trade_ready_signal_conversion') or {}).get('value', 'N/A')}% "
            f"watch_miss={(_ops_metrics.get('watch_only_missed_runup_ratio') or {}).get('value', 'N/A')}%"
        )
        log.info(f"[lesson candidates {market}] {len(lesson_candidates)} items")
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
                "judgment_eval": judgment_eval,
                "ops_review_snapshot": ops_review_snapshot,
                "postmortem": pm,
                "trades": session_trades,
            }},
        )
        # ?? ?뚯씤?쒕떇/?꾨＼?꾪듃 媛쒖꽑???꾪븳 ?꾩쟾??training record ?????????????
        record = {
            **self.today_judgment,          # date, market, judgments, consensus,
                                            # digest_prompt, tickers,
                                            # round1_judgments, debate_changes ?ы븿
            "actual_result":  actual,
            "execution_health": execution_health,
            "judgment_eval": judgment_eval,
            "ops_review_snapshot": ops_review_snapshot,
            "postmortem":     pm,
            "trades":         session_trades,
            "decision_events": [e for e in self.decision_event_log if e.get("market") == market],
            "session_events": self._session_events,   # ?쒕떇/湲닿툒?ы뙋???꾩껜 ?대젰
            "mode": "paper" if self.is_paper else "live",
        }
        path = _judgment_runtime_path(self._mode, today, market)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        log.info(f"[training record ??? {path.name} "
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
        try:
            probe_report = tsdb.micro_probe_performance_report(market)
            if int(probe_report.get("trades", 0) or 0) > 0:
                log.info(
                    f"[MICRO_PROBE ?깃낵 {market}] "
                    f"trades={probe_report['trades']} win_rate={probe_report['win_rate_pct']:.1f}% "
                    f"avg={probe_report['avg_pnl_pct']:+.2f}% total={probe_report['total_pnl_krw']:+,.0f}??"
                    f"max_loss={probe_report['max_loss_pct']:+.2f}%"
                )
        except Exception as exc:
            log.warning(f"MICRO_PROBE ?깃낵 由ы룷???ㅽ뙣: {exc}")
        # ?? param_tuner outcome ?낅뜲?댄듃 ??????????????????????????????????????
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
            _pt_strategy_outcomes = {}
            for _trade in sell_trades:
                _strat = str(_trade.get("strategy") or "unknown")
                _bucket = _pt_strategy_outcomes.setdefault(
                    _strat,
                    {"signals": 0, "entries": 0, "wins": 0, "losses": 0, "_pnls": [], "total_pnl_krw": 0.0},
                )
                _pnl = float(_trade.get("pnl", 0) or 0)
                _bucket["entries"] += 1
                _bucket["signals"] += 1
                _bucket["wins"] += 1 if _pnl > 0 else 0
                _bucket["losses"] += 0 if _pnl > 0 else 1
                _bucket["_pnls"].append(float(_trade.get("pnl_pct", 0) or 0))
                _bucket["total_pnl_krw"] += _pnl
            for _bucket in _pt_strategy_outcomes.values():
                _pnls = _bucket.pop("_pnls", [])
                _bucket["avg_pnl_pct"] = (sum(_pnls) / len(_pnls)) if _pnls else 0.0
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
                strategy_outcomes=_pt_strategy_outcomes,
            )
            _param_tuner.clear_cache(market)
            log.info(f"[param_tuner] {market} outcome 湲곕줉 ?꾨즺 (ids={len(_pt_ids)}媛? ?뚭툒 ?ы븿)")
        except Exception as _pt_e:
            log.warning(f"[param_tuner] outcome ?낅뜲?댄듃 ?ㅻ쪟: {_pt_e}")
        # Save the latest KR screen cache for the next pre-market session.
        if market == "KR" and self._last_kr_candidates:
            try:
                save_kr_screen_cache(self._last_kr_candidates)
                log.info(f"[KR 스크리너 캐시] session_close 저장 완료 ({len(self._last_kr_candidates)}종목)")
            except Exception as _e:
                log.warning(f"[KR 스크리너 캐시] session_close 저장 실패: {_e}")
        self._audit_flush_outcomes(market, force_close=True)
        if hasattr(self, "_active_session_date") and isinstance(self._active_session_date, dict):
            self._active_session_date[market] = None
def _in_session_now(market: str) -> bool:
    """?꾩옱 KST ?쒓컖???대떦 ?쒖옣 ?몄뀡 以묒씤吏 ?뺤씤"""
    now = datetime.now(ZoneInfo("Asia/Seoul")).time()
    if market == "KR":
        return dt_time(8, 50) <= now < dt_time(16, 0)
    else:  # US: 22:20 ~ (?먯젙 ?섏뼱) 05:00
        return now >= dt_time(22, 20) or now < dt_time(5, 0)
def _check_duplicate_bot(is_paper: bool = True) -> bool:
    """?숈씪 紐⑤뱶 遊뉗씠 ?대? ?ㅽ뻾 以묒씠硫?True"""
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
        log.error(f"[以묐났 ?ㅽ뻾 李⑤떒] {_mode} 遊뉗씠 ?대? ?ㅽ뻾 以묒엯?덈떎. 湲곗〈 ?꾨줈?몄뒪瑜?癒쇱? 醫낅즺?섏꽭??")
        sys.exit(1)
    DECISIONS_FILE = get_runtime_path("state", f"{_mode}_decisions.jsonl")
    _write_bot_pid_file(is_paper)
    atexit.register(_clear_bot_pid_file, is_paper)
    bot = TradingBot(is_paper=is_paper)
    enabled_markets = set(getattr(bot, "enabled_markets", {"KR", "US"}))
    log.info("=== Trading Bot Start ===")
    # Record session_start at boot.
    for _mkt in sorted(enabled_markets):
        analysis_log.info(
            f"[session_start {_mkt}]",
            extra={"extra": {"event": "session_start", "market": _mkt}},
        )
    # ?붾젅洹몃옩 紐낅졊???섏떊 ?쒖옉 (諛깃렇?쇱슫???ㅻ젅??
    tg_commander.start(bot)
    mode_txt = "paper" if is_paper else "live"
    system_alert(
        f"遊??쒖옉??[{mode_txt}]",
        [
            f"?뮥 珥덇린?먭툑: {bot.risk.init_cash:,}??(KR/US 怨듭쑀)",
            f"1 order max: {int(bot.risk.max_order_krw):,} KRW",
            "Commands: /setorder 300000 changes max order amount",
        ],
        icon="?쨼",
    )
    # ?? ?덈꼍 ?ㅽ겕由щ꼫 ? ?ъ쟾?섏쭛 ?????????????????????????????????????????????
    def _screener_collect(market: str):
        try:
            from phase1_trainer.price_collector import collect_screener_pool
            n = collect_screener_pool(market, lookback_days=90, top_n=50)
            log.info(f"[?덈꼍 ?섏쭛] {market} ?ㅽ겕由щ꼫 ? {n}醫낅ぉ ?꾨즺")
        except Exception as e:
            log.warning(f"[?덈꼍 ?섏쭛] {market} ?ㅽ뙣: {e}")
    # ?? KR ?섍툒 ?곗씠???먮룞 ?섏쭛 ????????????????????????????????????????????????
    # ?멸뎅??湲곌? ?쒕ℓ????KR digest??foreign_flow/inst_flow ?꾨뱶 梨꾩?
    # 08:20: supplement ?섏쭛 ??08:30: ?ㅽ겕由щ꼫 ?섏쭛 ??08:50: ?몄뀡 ?ㅽ뵂
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
            log.info(f"[?섍툒 ?섏쭛] {market} supplement ?꾨즺")
        except Exception as e:
            log.warning(f"[?섍툒 ?섏쭛] {market} ?ㅽ뙣: {e}")
    # ?? ??留덇컧 ???곗씠??理쒖떊??(醫낃? ?뺤젙 + forward return) ??????????????????
    def _data_update(market: str):
        try:
            from update_data import run_kr_update, run_us_update
            if market == "KR":
                run_kr_update()
            else:
                run_us_update()
        except Exception as e:
            log.warning(f"[?곗씠??理쒖떊?? {market} ?ㅽ뙣: {e}")
    def _repo_health_check(trigger: str):
        # Non-blocking operational guardrail: detect/report only, never stop trading flow.
        check_script = Path(__file__).parent / "tools" / "repo_health_check.py"
        cmd = [sys.executable, str(check_script), "--trigger", trigger, "--json"]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(Path(__file__).parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
            )
            payload = json.loads(proc.stdout or "{}")
            checks = payload.get("checks") or []
            failed = [c for c in checks if c.get("status") == "FAIL"]
            warned = [c for c in checks if c.get("status") == "WARN"]
            if proc.returncode == 0 and payload.get("ok") and not failed:
                log.info(f"[repo health] {trigger} OK ({len(checks)} checks, warnings={len(warned)})")
                return
            details = [
                f"{c.get('name')}: {c.get('detail')}"
                for c in (failed or warned)[:6]
            ]
            if not details:
                details = [(proc.stderr or proc.stdout or f"exit={proc.returncode}").strip()[:800]]
            log.warning(f"[repo health] {trigger} FAIL | " + " | ".join(details))
            system_alert(
                f"repo health warning: {trigger}",
                details,
                icon="!",
            )
        except Exception as e:
            log.warning(f"[repo health] {trigger} failed to run: {e}")
            system_alert(
                f"repo health runner warning: {trigger}",
                [str(e)],
                icon="!",
            )
    if "KR" in enabled_markets:
        schedule.every().day.at("08:20").do(_supplement_collect, "KR")   # KR ?섍툒 (?멸뎅??湲곌?)
        schedule.every().day.at("08:30").do(_screener_collect, "KR")
    if "US" in enabled_markets:
        schedule.every().day.at("21:20").do(_supplement_collect, "US")   # US VIX/DXY
        schedule.every().day.at("21:30").do(_screener_collect, "US")
    def _midnight_token_refresh():
        """KIS ?좏겙 ?먯젙 臾댄슚???????00:01??媛뺤젣 媛깆떊 ??釉뚮줈而??곹깭 蹂듦뎄"""
        try:
            bot.token = get_access_token(force_refresh=True)
            bot._sync_runtime_with_broker()
            log.info("[?먯젙 ?좏겙 媛깆떊] ?꾨즺")
        except Exception as e:
            log.warning(f"[?먯젙 ?좏겙 媛깆떊] ?ㅽ뙣: {e}")
    schedule.every().day.at("00:01").do(_midnight_token_refresh)
    if "KR" in enabled_markets:
        schedule.every().day.at("08:40").do(_repo_health_check, "KR_PREOPEN")
        schedule.every().day.at("08:50").do(bot.session_open, "KR")
        schedule.every().day.at("09:05").do(bot.run_opening_fresh_screener, "KR")
        schedule.every().day.at("16:00").do(bot.session_close, "KR")
        schedule.every().day.at("16:10").do(_data_update, "KR")          # KR ????醫낃? ?뺤젙
        schedule.every().day.at("16:15").do(_repo_health_check, "KR_POSTCLOSE")
    if "US" in enabled_markets:
        schedule.every().day.at("22:10").do(_repo_health_check, "US_PREOPEN")
        schedule.every().day.at("22:20").do(bot.session_open, "US")
        schedule.every().day.at("05:00").do(bot.session_close, "US")
        schedule.every().day.at("05:10").do(_repo_health_check, "US_POSTCLOSE")
        schedule.every().day.at("06:30").do(_data_update, "US")          # US ????醫낃? ?뺤젙
    for _mkt in sorted(enabled_markets):
        schedule.every(5).minutes.do(bot.run_housekeeping, _mkt)
        schedule.every(1).minutes.do(bot.run_entry_scan, _mkt)
        schedule.every(_RESCREEN_SCHEDULE_TICK_MIN).minutes.do(bot.run_rescreen, _mkt)
        schedule.every(30).minutes.do(bot.run_tuning, _mkt)
        schedule.every(60).minutes.do(bot._intraday_position_review, _mkt)
    # 1?쒓컙留덈떎 ?곹깭 蹂닿퀬 (?몄뀡 以묒씪 ?뚮쭔 ?ㅼ젣 ?꾩넚)
    schedule.every(60).minutes.do(bot._heartbeat)
    log.info("schedules registered")
    # ?? 遊??쒖옉 ???대? 吏꾪뻾 以묒씤 ?몄뀡???덉쑝硫?利됱떆 session_open ??????????????
    now_kst = datetime.now(KST)
    now_t   = now_kst.time()
    kr_open  = dt_time(8, 50);  kr_close = dt_time(16, 0)
    us_open  = dt_time(22, 20); us_close = dt_time(5, 0)
    kr_mid_session = kr_open <= now_t < kr_close
    us_mid_session = now_t >= us_open or now_t < us_close  # ?먯젙 嫄몄묠
    if kr_mid_session and "KR" in enabled_markets:
        log.info("[startup] KR 세션 진행 중 - session_open 즉시 실행")
        bot.session_open("KR", trigger="startup_mid_session")
    if us_mid_session and "US" in enabled_markets:
        log.info("[startup] US 세션 진행 중 - session_open 즉시 실행")
        bot.session_open("US", trigger="startup_mid_session")
    while True:
        schedule.run_pending()
        time.sleep(1)
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="live mode")
    parser.add_argument("--paper", action="store_true", help="paper mode (default)")
    parser.add_argument("--collect", choices=["KR", "US", "ALL"],
                        help="?곗씠???섏쭛 ?ㅽ뻾 ??醫낅즺: 二쇨??믩돱?ㅲ넂supplement ?쒖꽌濡??섏쭛")
    parser.add_argument("--train", choices=["KR", "US", "ALL"],
                        help="Phase1 ??궗 ?숈뒿 ?ㅽ뻾 ??醫낅즺 (?? --train KR)")
    parser.add_argument("--train-start", default=None,
                        help="?섏쭛/?숈뒿 ?쒖옉??(湲곕낯: KR=2024-10-01 / US=2025-01-01)")
    args = parser.parse_args()
    if args.collect:
        # 1?④퀎: 二쇨? ?섏쭛
        from phase1_trainer.price_collector import collect_kr_incremental, collect_us_incremental
        col_start = args.train_start or "2024-10-01"
        col_end   = date.today().strftime("%Y-%m-%d")
        s_ts = __import__("pandas").Timestamp(col_start)
        e_ts = __import__("pandas").Timestamp(col_end)
        markets_col = ["KR", "US"] if args.collect == "ALL" else [args.collect]
        if "KR" in markets_col:
            log.info(f"[?섏쭛] KR 二쇨? {col_start} ~ {col_end}")
            collect_kr_incremental(s_ts, e_ts)
        if "US" in markets_col:
            log.info(f"[?섏쭛] US 二쇨? {col_start} ~ {col_end}")
            collect_us_incremental(s_ts, e_ts)
        # 2?④퀎: ?댁뒪 ?섏쭛
        from phase1_trainer.kr_news_collector import collect_range as kr_news_range
        from phase1_trainer.us_news_collector import collect_range as us_news_range
        from phase1_trainer.supplement_collector import collect_range as supp_range
        if "KR" in markets_col:
            log.info(f"[?섏쭛] KR ?댁뒪 {col_start} ~ {col_end}")
            kr_news_range(col_start, col_end)
        if "US" in markets_col:
            log.info(f"[?섏쭛] US ?댁뒪 {col_start} ~ {col_end}")
            us_news_range(col_start, col_end)
        # Step 3: supplementary data.
        log.info(f"[collect] supplement {col_start} ~ {col_end}")
        market_arg = args.collect if args.collect != "ALL" else "ALL"
        supp_range(col_start, col_end, market=market_arg)
        log.info("?곗씠???섏쭛 ?꾨즺. ?댁젣 --train ?쇰줈 Phase1 ?숈뒿???ㅽ뻾?섏꽭??")
        sys.exit(0)
    if args.train:
        from phase1_trainer.historical_sim import run_simulation
        markets = ["KR", "US"] if args.train == "ALL" else [args.train]
        defaults = {"KR": "2024-10-01", "US": "2025-01-01"}
        end = date.today().strftime("%Y-%m-%d")
        for mkt in markets:
            start = args.train_start or defaults[mkt]
            log.info(f"Phase1 ?숈뒿 ?쒖옉: {mkt} {start} ~ {end}")
            run_simulation(market=mkt, start=start, end=end)
        sys.exit(0)
    is_paper = not args.live
    main(is_paper=is_paper)
