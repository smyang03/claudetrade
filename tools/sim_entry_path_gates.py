"""진입 경로 게이트 시뮬레이터 — 주문 직전까지 실제로 태워서 어디서 죽는지 관측한다.

배경 (2026-07-29):
  2026-07-28 US 세션에서 judge BUY_READY 12건·즉시매수 6회 발동에도 실주문이 0건이었다.
  원인은 고점근접 차단(FROM_HIGH_BLOCK_PCT=-2.0)이었는데, 그 경로가 log.debug + DB 미기록이라
  로그·DB 어디에도 흔적이 남지 않아 며칠 동안 "국면 차단(설계대로)"으로 오인됐다.

  진입 경로에는 후보를 탈락시키는 지점이 49개 있고, 각 지점이 BUY_READY에 맞는지는
  정적 분석으로 판정되지 않는다(플래그를 세우는 곳과 continue하는 곳이 떨어져 있음).
  그래서 실제로 run_cycle을 태워서 관측한다.

방법
  - TradingBot.__new__로 인스턴스를 만들고 외부 의존성(API·DB·브로커)만 주입한다.
  - 시나리오별로 (경과시간 × 고점대비 × 국면 × BUY_READY 여부)를 바꿔 run_cycle을 호출한다.
  - 로그를 캡처해 어느 게이트 문구가 나왔는지, 주문 함수까지 도달했는지 판정한다.

한계 (보고 시 함께 낸다)
  - 주입한 의존성은 실제 런타임과 다를 수 있다. "통과"는 그 게이트를 안 탔다는 뜻이지
    라이브에서 반드시 주문된다는 보장이 아니다.
  - 하류(리스크·affordability·브로커)는 mock이므로 이 시뮬의 판정 범위 밖이다.
  - 목적은 "어느 게이트가 어떤 상황에서 후보를 죽이는가"의 지도이지 수익 예측이 아니다.
  - ★ --market KR 결과를 실제 KR 동작으로 읽지 말 것.
    `_buy_ready_immediate_enabled`를 True로 고정했는데 라이브는
    SINGLE_SYMBOL_JUDGE_BUY_READY_MARKETS=US (KR은 SHADOW_MARKETS)다.
    즉 KR 결과는 "KR에도 즉시매수를 켠다면"의 가상치다.
  - 기술신호 경로(비 BUY_READY)는 태워진다. 2026-07-30 정정 — 이전 주석은
    "분봉이 없어 ORP를 발화시킬 수 없다"고 적혀 있었으나 사실과 다르다.
    build_bot이 `_or_high`/`_or_low`/`_or_formed`를 직접 주입하고(191~194행),
    run_case가 signal_kind="orp"에 or_state 기본값을 채운다(348행).
    US live base 전략은 opening_range_pullback / mean_reversion 뿐이다
    (momentum·VB는 disabled, gap_pullback은 운영자 파라미터로 차단).
  - ★ ORP 도달률을 낮게 읽지 말 것. ORP는 개장 후 or_minutes~(or_minutes+
    entry_window_min) 구간에서만 발화한다(US: 15~75분, KR: 10~70분).
    시나리오 경과시간이 10/40/120/300/370분이므로 창 안은 40분 하나뿐이고,
    "5개 중 1개 통과"는 시간창 설계대로의 결과다. 결함이 아니다.
  - ★ mean_reversion은 2026-05-12 이후 라이브 발화가 0이다(실측).
    국면 악화 시 rsi_thr을 낮추는데 ma60_thr=0.95는 고정이라, 임계를 통과할 만큼
    빠진 종목은 ma60 조건을 만족하지 못한다(US 05-13 이후 RSI+BB 충족 92건 중
    ma_ok=0). 따라서 이 하네스에서 mean_reversion이 통과해도 라이브 재현이 아니다.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("CLAUDETRADE_SIM", "1")

import pandas as pd  # noqa: E402
import trading_bot  # noqa: E402

# ★ 실주문 차단 가드 (필수) ─────────────────────────────────────────────
# 이 시뮬은 라이브와 같은 trading_bot 모듈을 실제로 태운다. 진입/청산 경로가
# place_order까지 도달할 수 있으므로 모듈 레벨 주문 함수를 무조건 차단본으로
# 덮어쓴다. 절대 제거하지 말 것 — 장중에 돌리면 실주문이 나갈 수 있다.
def _sim_blocked_order(*_a, **_k):
    return {"success": False, "msg": "SIM_ORDER_BLOCKED", "order_no": "", "sim": True}


for _fn in ("place_order", "cancel_order", "precheck_order"):
    if hasattr(trading_bot, _fn):
        setattr(trading_bot, _fn, _sim_blocked_order)


# ★ 라이브 원장 쓰기 차단 가드 (필수) ────────────────────────────────────
# 2026-07-30 실측으로 확인된 오염: run_cycle이 isdb.insert_probe()를 호출해
# data/intraday_strategy_log.db(라이브 ORP 관측 원장)에 SIMTK 행을 남겼다.
# 07-29 13:14~07-30 01:34 사이 1,411행이 실제 세션 날짜로 기록됐고, US 07-29
# 세션 행수가 3건에서 1,342건으로 부풀어 "ORP 발화 66건"으로 오독됐다
# (실제 발화는 0건). bot_mode='paper'로 구분은 되지만, 필터를 모르는 분석자가
# 그대로 집계하면 판정이 뒤집힌다. 주문만 막고 원장 쓰기를 막지 않은 것이
# 원인이었다. 절대 제거하지 말 것.
def _sim_blocked_probe(*_a, **_k):
    return 0


try:
    import intraday_strategy_db as _isdb_mod

    for _wfn in ("insert_probe", "update_outcome", "init"):
        if hasattr(_isdb_mod, _wfn):
            setattr(_isdb_mod, _wfn, _sim_blocked_probe)
except Exception:
    pass

# decisions.db도 같은 경로로 오염됐다(2026-07-30 실측: SIMTK 835행,
# 07-29 13:13~07-30 01:34). is_simulated=0 / data_source='live'로 기록돼
# 플래그로 걸러낼 수도 없었고, 그 결과 "US watch_only가 100%→73.7%로 개선됐다"는
# 판정이 나왔다 — SIMTK 787행을 제외하면 실제로는 100.0% 그대로였다.
# _ml_write_eval은 _ML_DB_ENABLED를 먼저 보므로 그 플래그를 내리는 것이 가장 확실하고,
# import 시점에 바인딩된 이름들도 함께 무력화한다. 제거 금지.
trading_bot._ML_DB_ENABLED = False
for _mlfn in ("_ml_write", "_ml_update_filled", "_ml_update_outcome", "_ml_init_db"):
    if hasattr(trading_bot, _mlfn):
        setattr(trading_bot, _mlfn, _sim_blocked_probe)


class LogCapture(logging.Handler):
    """run_cycle이 남기는 모든 로그를 레벨 무관하게 모은다(debug 포함)."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[tuple[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append((record.levelname, record.getMessage()))
        except Exception:
            pass

    def find(self, needle: str) -> str:
        for _lvl, msg in self.records:
            if needle in msg:
                return msg
        return ""


def _make_candles(n: int = 80, base: float = 100.0, *, kind: str = "flat") -> pd.DataFrame:
    """시나리오별 일봉 합성.

    kind="momentum": ma5>ma20>ma60 정배열 + MACD 골든 + 마지막봉 거래량 급증 + 신고가 돌파
                     → strategy.momentum.signal 이 True (실측으로 확인)
    kind="flat":     완만한 상승만 — 거래량/신고가 조건 미달로 기술 신호가 뜨지 않는다.
                     BUY_READY(judge 결정) 경로만 남기고 싶을 때 쓴다.
    """
    trend = 0.004
    rows = []
    px = base
    for k in range(n):
        px = px * (1.0 + trend)
        rows.append({
            "date": pd.Timestamp("2026-04-01") + pd.Timedelta(days=k),
            "open": px * 0.995,
            "high": px * 1.006,
            "low": px * 0.992,
            "close": px,
            "volume": 1_000_000,
        })
    if kind == "momentum":
        px_last = px * 1.03
        rows[-1].update({
            "open": px * 1.001, "high": px_last * 1.002, "low": px * 0.999,
            "close": px_last, "volume": 4_000_000,
        })
    elif kind == "mean_reversion":
        # US live base 전략은 opening_range_pullback / mean_reversion 뿐이다
        # (trading_bot.py 주석: "momentum/VB 모두 disabled", gap_pullback은 운영자 파라미터로 차단).
        # 따라서 US 기술신호 경로를 태우려면 mean_reversion을 발화시켜야 한다.
        # 전반 상승 후 후반 급락 → rsi≈4.6 / bb_pct≈2.2 로 실측 발화 확인.
        rows = []
        px = base
        for k in range(n):
            px = px * (1.004 if k < n - 12 else 0.985)
            rows.append({
                "date": pd.Timestamp("2026-04-01") + pd.Timedelta(days=k),
                "open": px * 1.002, "high": px * 1.005, "low": px * 0.995,
                "close": px, "volume": 1_000_000,
            })
    # 마지막 종가를 현재가(base)에 맞춘다. 안 맞추면 장중 당일봉 주입이 큰 갭으로 잡혀
    # rsi/bb_pct가 뒤틀리고(급등으로 계산) 의도한 신호가 발화하지 않는다.
    _last = float(rows[-1]["close"]) or 1.0
    _scale = base / _last
    for r in rows:
        for c in ("open", "high", "low", "close"):
            r[c] = float(r[c]) * _scale
    return pd.DataFrame(rows)


def build_bot(*, market: str, mode: str, elapsed_min: float, trade_ready: bool,
              buy_ready_route: bool, price: float, day_high: float,
              signal_kind: str, cap: LogCapture, or_state: dict | None = None,
              fault: str = "", tickers: int = 1) -> object:
    bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
    tk = "SIMTK"
    tks = [tk] if tickers <= 1 else [f"SIMTK{i}" for i in range(1, tickers + 1)]

    bot.session_active = True
    bot.current_market = market
    bot.is_paper = False
    bot.today_judgment = {
        "market": market,
        "consensus": {"mode": mode, "size": 50},
        "judgments": {},
        "digest_raw": {"context": {}},
        # phase는 _EXECUTABLE_JUDGMENT_PHASES = {"opening_confirm", "intraday_live"} 중 하나여야
        # 신규 진입 게이트를 통과한다(trading_bot.py:521). "intraday"는 미포함이라 즉시 탈락한다.
        "judgment_context_basis": {
            "phase": "intraday_live",
            "execution_authority": "BUY_SELL_LIVE",
        },
        "universe_tickers": list(tks),
    }
    bot.today_tickers = {market: list(tks)}
    bot.today_ticker_reasons = {market: {t: "sim" for t in tks}}
    bot.selection_meta = {market: {
        "trade_ready": list(tks) if trade_ready else [],
        "price_targets": {t: {
            "sell_target": price * 1.03,
            "stop_loss": price * 0.985,
            "hold_days": 2,
        } for t in tks} if buy_ready_route else {},
    }}
    bot.trade_ready_tickers = {market: list(tks) if trade_ready else []}
    # price_cache_raw는 직전 정상가(float)를 담는다 — outlier 방어(30% 괴리)에서 float 비교됨.
    # 빈 dict로 두면 get(ticker, 0)=0이라 방어 로직을 건너뛴다(시뮬 목적상 안전).
    bot.price_cache = {}
    bot.price_cache_raw = {}
    bot._session_open_at = {market: time.time() - elapsed_min * 60}
    bot._session_startup_guard_sec = {market: 0}
    bot._pre_session_sell_queue = {market: []}
    bot._vix_refresh_at = 0
    bot.pathb = None
    # self.enable_* 플래그 일괄 기본값 — 시뮬은 진입 경로 게이트만 보므로 부가기능은 모두 끈다.
    for _flag in ("enable_atr_position_sizing", "enable_continuation_live",
                  "enable_dynamic_universe", "enable_kr_momentum_shrink",
                  "enable_limit_order", "enable_micro_probe", "enable_recovery_micro",
                  "enable_slippage_guard", "enable_soft_watch_promotion",
                  "enable_trailing_analyst", "enable_trailing_stop",
                  "enable_watch_trigger_shadow"):
        setattr(bot, _flag, False)
    bot.max_est_slippage_bps = 100.0
    # opening_range_pullback은 US live base 전략의 주력이다. OR(개장 레인지)이 형성돼야
    # 발화하므로 시나리오에서 직접 주입한다. 미주입이면 orp_not_formed로 탈락한다.
    _or = dict(or_state or {})
    bot._or_high = {t: float(_or['high']) for t in tks} if _or.get('high') else {}
    bot._or_low = {t: float(_or['low']) for t in tks} if _or.get('low') else {}
    bot._or_formed = {t: True for t in tks} if _or.get('formed') else {}
    bot._continuation_used = {}
    bot._ticker_no_signal_cycles = {}
    bot._ticker_no_signal_minutes = {}
    bot._daily_sl_count = {market: 0}
    bot._order_error_count = {}
    bot._tsdb_selection_ids = {market: {}}
    bot._funnel = {market: {"ordered": 0, "volume_states": {}, "rejection_reasons": {},
                            "blocked_reasons": {}, "signals": 0, "candidates": 0}}
    bot._ticker_runtime_blocked_reasons = {market: {}}
    bot._ticker_runtime_rejection_reasons = {market: {}}
    # 장중 상태 맵 — 당일봉 주입/장중 고저 추적 경로에서 참조된다.
    bot._intraday_high = {}
    bot._intraday_low = {}
    bot._intraday_minute_cache = {}
    bot._intraday_avg_daily_volume_map = {}
    bot._intraday_candidate_volume_ratio_map = {}
    bot._intraday_fail_closed_feature_map = {}
    bot._intraday_evidence_priority_tickers = set()
    bot._intraday_evidence_retry_due_by_market = {market: {}}
    bot._intraday_evidence_target_limit = 0
    bot._intraday_mode_is_risk_off = False
    bot._intraday_position_review = {}
    bot._intraday_recheck_due_state = {}
    bot.risk = SimpleNamespace(
        halt_reason="", daily_pnl=0.0, positions=[], cash=10_000_000.0,
        max_order_krw=500_000.0,
        update_prices=lambda *a, **k: None,
    )

    # ── 외부 의존성 차단 ────────────────────────────────────────────────
    noop = lambda *a, **k: None
    bot._enter_market_task = lambda m, o: True
    bot._leave_market_task = Mock()
    bot._record_cycle_latency = noop
    bot._update_market_sharp_reversal_shadow = noop
    bot._refresh_operational_halt = noop
    bot._has_broker_sync_risk = lambda m: False
    bot._check_market_halt = lambda *a, **k: False
    bot._refresh_claude_control = noop
    bot._consume_pending_claude_trigger = noop
    bot._consume_pending_position_review = noop
    bot._consume_pending_sell = noop
    bot._maybe_refresh_opening_judgment = noop
    bot._maybe_run_opening_fresh_screener = noop
    bot._sync_runtime_with_broker = noop
    bot._process_exit_candidates = Mock()
    bot._write_live_status = Mock()
    bot._maybe_push_dashboard = Mock()
    bot._runtime_gate_state_text = lambda m: "ok"
    bot._us_order_block_reason = lambda t: ""
    bot._token_for_market = lambda m: "token"
    bot._price_to_krw = lambda p, m: float(p)
    bot._market_elapsed_min = lambda m: float(elapsed_min)
    bot._intraday_session_progress = lambda m: 0.5
    bot._get_ohlcv_cached = lambda t, m: _make_candles(base=price, kind=signal_kind)
    bot._in_entry_blackout = (lambda m: True) if fault == "blackout" else (lambda m: False)
    bot._is_entry_blocked = (lambda t: True) if fault == "cooldown" else (lambda t: False)
    bot._has_open_position = (lambda t, m: True) if fault == "holding" else (lambda t, m: False)
    bot._has_pending_order = (lambda t, m: True) if fault == "pending" else (lambda t, m: False)
    bot._same_day_reentry_state = (
        (lambda t, m: {"allowed": False, "reason": "same_day_reentry"}) if fault == "reentry"
        else (lambda t, m: {"allowed": True}))
    bot._trade_ready_set = lambda m: (set(tks) if trade_ready else set())
    bot._is_trade_ready_ticker = lambda m, t: bool(trade_ready)
    bot._watch_only_bucket = lambda m, t: "SOFT"
    bot._watch_only_reason_text = lambda m, t: "sim watch_only"
    bot._can_recheck_soft_watch_only = lambda m, t, mo: False
    bot._buy_ready_immediate_enabled = lambda: True
    bot._candidate_action_route_for_ticker = (
        (lambda m, t, **kw: {"requested_action": "BUY_READY", "route": "PlanA.buy"})
        if buy_ready_route else (lambda m, t, **kw: None)
    )
    bot._selection_price_target_for_ticker = lambda m, pts, t: (pts or {}).get(t, {})
    bot._runtime_float = lambda k, d: float(d)
    bot._runtime_bool = lambda k, d=False: bool(d)
    bot._runtime_value = lambda k, d="": d
    bot._bump_runtime_reason = noop
    bot._write_funnel_event = noop
    bot._log_watch_trigger_shadow = noop
    bot._log_watch_trigger_not_evaluated = noop
    bot._record_watch_trigger_shadow_evaluation = noop
    bot._watch_trigger_shadow_strategy_for_ticker = lambda m, t: ("momentum", "sim")
    bot._update_candidate_health = noop
    bot._candidate_health_tracker = lambda m: SimpleNamespace(
        state_for=lambda t: {}, record=noop)
    bot._entry_timing_signal_check = noop
    bot._entry_timing_order_sent = lambda *a, **k: {}
    bot._promote_trade_ready_ticker = noop
    bot._selection_ticker_key = lambda m, t: str(t).upper() if m == "US" else str(t)
    bot._effective_entry_priority_cutoff = lambda m: 0.0
    bot._is_entry_priority_blocked = lambda s, m: False
    bot._effective_momentum_wait_window = lambda m, d: d
    bot._momentum_entry_min_elapsed = lambda m, mo, w: 0.0
    bot._live_plan_a_signal_allowed = lambda m, s, mo: True
    bot._partial_data_size_cap_pct = lambda: 100
    bot._MIN_SIGNAL_ROWS = 20
    bot._v2_decision_id_for_ticker = lambda m, t: ""
    bot._v2_ensure_execution_decision_id = lambda *a, **k: "sim-decision"
    bot._project_intraday_volume = lambda m, v: v
    bot._mark_us_order_supported = noop
    bot._mark_us_order_blocked = noop
    # 브로커/리스크 계층 — 이 시뮬의 판정 범위 밖이므로 통과시킨다.
    # (여기서 막히면 "게이트 지도"를 그릴 수 없고, 실제 라이브에선 별도로 검증된다)
    # ── 하류 게이트 fault 주입 ──────────────────────────────────────
    bot._entry_allowed_by_broker_state = (
        (lambda m: (False, "broker_state_untrusted")) if fault == "broker"
        else (lambda m: (True, "OK")))
    bot._broker_trust_level = lambda m: "trusted"
    bot._ticker_market = lambda t: market
    bot.risk.can_open = (
        (lambda t, rp, sp, market=None: (False, "max_positions")) if fault == "risk"
        else (lambda t, rp, sp, market=None: (True, "OK")))
    bot._recommended_strategy_for_ticker = lambda m, t: "momentum"
    bot._notify_signal_state_change = noop
    bot._selection_meta_mark_runtime_filtered = noop
    bot._available_budget_krw = lambda m: 10_000_000.0
    bot._entry_scan_interval_sec = lambda m: 120.0
    bot._momentum_atr_stage_for = lambda *a, **k: {"blocked": False, "stage": "ok",
                                                  "cap": 0.05, "high_cap": 0.09}
    bot._partial_signal_gate = lambda *a, **k: {"allowed": True}
    bot._buy_time_confirm_gate = lambda *a, **k: {"allowed": True}
    bot._record_entry_block = noop
    bot._maybe_record_intraday_strategy = noop
    bot._effective_size_pct = lambda *a, **k: 50.0

    logger = trading_bot.log
    logger.setLevel(logging.DEBUG)
    logger.addHandler(cap)
    return bot


GATE_MARKERS = [
    ("고점근접 차단", "from_high_block"),
    ("고점근접 차단 면제", "from_high_EXEMPT"),
    ("마감 직전 차단", "late_session_cutoff"),
    ("entry_priority cutoff", "entry_priority"),
    ("WATCH_ONLY", "watch_only"),
    ("BUY_READY 즉시", "buy_ready_fired"),
    ("신호 정렬", "reached_order_loop"),
    ("orp_", "orp_reason"),
    ("opening_range_pullback", "orp_strategy"),
    ("halt", "halt_mode"),
    ("모드 진입 억제", "mode_block"),
    ("stop cluster", "stop_cluster"),
    ("예산 소진", "budget"),
    ("신호계산 불가", "signal_rows"),
]


def run_case(*, market, mode, elapsed_min, from_high_pct, trade_ready, buy_ready_route,
             signal_kind="flat", or_state=None, fault="", tickers=1):
    # signal_kind="orp": US live base 주력 전략을 태우기 위해 OR을 자동 주입한다.
    # 현재가 100 기준 OR 99~101(폭 1.0%) → close가 or_high 대비 -0.99%로 눌림구간에 든다.
    if signal_kind == "orp" and or_state is None:
        or_state = {"high": 101.0, "low": 100.0, "formed": True}
    cap = LogCapture()
    price = 100.0
    day_high = price / (1.0 + from_high_pct / 100.0)
    bot = build_bot(market=market, mode=mode, elapsed_min=elapsed_min,
                    trade_ready=trade_ready, buy_ready_route=buy_ready_route,
                    price=price, day_high=day_high, signal_kind=signal_kind, cap=cap,
                    or_state=or_state, fault=fault, tickers=tickers)
    err = ""
    try:
        with patch("trading_bot.get_price", return_value={"price": price, "high": day_high,
                                                          "low": price * 0.97, "open": price * 0.98,
                                                          "volume": 5_000_000}):
            trading_bot.TradingBot.run_cycle(bot, market)
    except Exception as exc:  # 의존성 미주입은 결함이 아니라 하네스 한계로 표시
        err = f"{type(exc).__name__}: {exc}"
    finally:
        trading_bot.log.removeHandler(cap)

    hits = [tag for needle, tag in GATE_MARKERS if cap.find(needle)]
    return {"hits": hits, "err": err, "records": cap.records}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true", help="케이스별 로그 전량 출력")
    ap.add_argument("--limit-logs", type=int, default=12)
    ap.add_argument("--market", default="US", choices=["US", "KR"])
    args = ap.parse_args()

    scenarios = []
    for mode in ("MILD_BULL", "CAUTIOUS", "DEFENSIVE", "HALT"):
        # 300분 = late session score 게이트(US 270분/KR 300분) 구간.
        # 이 축이 없어서 2026-07-29에 BUY_READY 100% 차단을 놓쳤다.
        # 40분 = ORP 진입창(OR 15분 + 60분) 한가운데이자 고점근접 유예(30분) 밖.
        # 이 축이 없어 ORP가 82% 차단되던 것을 시나리오 스윕에서 놓쳤다.
        for elapsed in (10.0, 40.0, 120.0, 300.0, 370.0):
            for fh in (-0.4, -3.0):
                for sig in ("orp", "flat"):
                    for br in (True, False):
                        scenarios.append(dict(market=args.market, mode=mode,
                                              elapsed_min=elapsed, from_high_pct=fh,
                                              trade_ready=True, buy_ready_route=br,
                                              signal_kind=sig))

    print(f"시나리오 {len(scenarios)}건 — 국면 × 경과시간 × 고점대비 × 기술신호 × BUY_READY")
    print("(reached_order_loop = _pending_signals 도달 = 주문 루프 진입)")
    print()
    print(f"{'국면':11s} {'경과':>5s} {'고점':>6s} {'신호':9s} {'BR':3s} | {'도달':4s} | 게이트")
    print("-" * 112)
    fails = 0
    reached = 0
    rows = []
    for sc in scenarios:
        r = run_case(**sc)
        hits = r["hits"]
        ok = "reached_order_loop" in hits
        reached += int(ok)
        gates = ",".join(h for h in hits if h != "reached_order_loop") or "-"
        if r["err"]:
            gates += f"  [하네스오류] {r['err'][:50]}"
            fails += 1
        rows.append((sc, ok, gates))
        print(f"{sc['mode']:11s} {sc['elapsed_min']:5.0f} {sc['from_high_pct']:6.1f}% "
              f"{sc['signal_kind']:9s} {'Y' if sc['buy_ready_route'] else '-':3s} | "
              f"{'OK' if ok else '..':4s} | {gates}")
        if args.verbose:
            for lvl, msg in r["records"][: args.limit_logs]:
                print(f"      {lvl:7s} {msg[:118]}")

    print()
    print(f"주문루프 도달 {reached}/{len(scenarios)} | 하네스 오류 {fails}")
    print()
    print("=== 국면별 도달률 ===")
    for mode in ("MILD_BULL", "CAUTIOUS", "DEFENSIVE", "HALT"):
        sub = [(sc, ok) for sc, ok, _ in rows if sc["mode"] == mode]
        n = sum(1 for _, ok in sub if ok)
        print(f"  {mode:11s} {n:2d}/{len(sub):2d}")
    print()
    print("=== 진입 경로별 도달률 (BUY_READY vs 기술신호) ===")
    for label, pred in (
        ("BUY_READY(judge)", lambda sc: sc["buy_ready_route"]),
        ("기술신호만(ORP)", lambda sc: not sc["buy_ready_route"] and sc["signal_kind"] == "orp"),
        ("신호없음", lambda sc: not sc["buy_ready_route"] and sc["signal_kind"] == "flat"),
    ):
        sub = [(sc, ok) for sc, ok, _ in rows if pred(sc)]
        n = sum(1 for _, ok in sub if ok)
        print(f"  {label:18s} {n:2d}/{len(sub):2d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
