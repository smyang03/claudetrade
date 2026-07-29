"""매도(청산) 경로 게이트 시뮬 — _process_exit_candidates를 실제로 태운다.

배경 (2026-07-29):
  진입 경로는 시뮬로 태웠으나 매도는 정적 인벤토리만 했다(탈락 8개 중 무흔적 7개로 잡힘).
  매도는 손실 방어 축이라 조용한 스킵이 더 위험하므로 실제로 태워 판정한다.

방법
  - TradingBot.__new__ 인스턴스에 risk.get_exit_candidates를 주입해 청산 후보를 만든다.
  - _execute_sell / _handle_tp_trailing / _try_soft_exit_arbitration을 가로채
    "실제 매도까지 갔는가"를 관측한다(주문은 내지 않는다).
  - 사유(reason) × 상황(정상/가격이상/쿨다운/장외/락점유)별로 어디서 멈추는지 본다.

한계
  - 브로커 주문·체결은 mock이다. "매도 도달"이 체결 보장이 아니다.
  - 목적은 청산이 조용히 사라지는 지점의 지도다.
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


class LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[tuple[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append((record.levelname, record.getMessage()))
        except Exception:
            pass

    def find(self, needle: str) -> str:
        for _l, m in self.records:
            if needle in m:
                return m
        return ""


def build_bot(*, market: str, cand: dict, cap: LogCapture, fault: str = "",
              order_allowed: bool = True, lock_held: bool = False):
    bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
    tk = cand["ticker"]

    bot.current_market = market
    bot.is_paper = False
    bot.enable_trailing_stop = True
    bot._sell_fail_at = {tk: time.time()} if fault == "sell_cooldown" else {}
    bot._exit_process_lock = threading.Lock()
    if lock_held:
        bot._exit_process_lock.acquire()

    bot.risk = SimpleNamespace(
        positions=[{"ticker": tk, "qty": 3, "entry": 100.0}],
        cash=1_000_000.0,
        get_exit_candidates=lambda: [dict(cand)],
    )
    noop = lambda *a, **k: None
    bot._fixed_horizon_strategy_exit_candidates = lambda: []
    bot._ticker_market = lambda t: market
    bot._token_for_market = lambda m, **k: "token"
    bot._is_order_allowed_now = lambda m: bool(order_allowed)
    bot._find_live_position_for_candidate = lambda c, m: {"ticker": tk, "qty": 3}
    bot._restore_recovery_micro_metadata = lambda c, p: {}
    bot._record_exit_lifecycle_decision = lambda *a, **k: {}
    bot._annotate_profit_preservation_sla = noop
    bot._runtime_bool = lambda k, d=False: bool(d)
    bot._block_entry = noop
    bot._SELL_FAIL_COOLDOWN_SEC = 90

    # 관측 지점 — 여기 도달하면 "청산 실행까지 갔다"
    bot._execute_sell = Mock(name="_execute_sell")
    bot._handle_tp_trailing = Mock(name="_handle_tp_trailing")
    bot._try_soft_exit_arbitration = Mock(name="_try_soft_exit_arbitration", return_value=False)

    trading_bot.log.setLevel(logging.DEBUG)
    trading_bot.log.addHandler(cap)
    return bot


def run_case(*, reason: str, exit_price: float = 101.0, market: str = "US",
             fault: str = "", order_allowed: bool = True, lock_held: bool = False,
             halted: bool = False):
    cap = LogCapture()
    cand = {"ticker": "SIMTK", "reason": reason, "exit_price": exit_price, "qty": 3}
    bot = build_bot(market=market, cand=cand, cap=cap, fault=fault,
                    order_allowed=order_allowed, lock_held=lock_held)
    err = ""
    try:
        import runtime.pathb_runtime  # noqa: F401
        # KR 거래정지 체크 우회/주입
        orig_halt = getattr(trading_bot, "is_trading_halted", None)
        trading_bot.is_trading_halted = lambda t, tok: bool(halted)
        try:
            trading_bot.TradingBot._process_exit_candidates(bot)
        finally:
            if orig_halt is not None:
                trading_bot.is_trading_halted = orig_halt
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
    finally:
        trading_bot.log.removeHandler(cap)

    return {
        "sold": bot._execute_sell.called,
        "trailing": bot._handle_tp_trailing.called,
        "arbitrated": bot._try_soft_exit_arbitration.called,
        "err": err,
        "cap": cap,
        "records": cap.records,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    reasons = ["loss_cap", "stop_loss", "trail_stop", "profit_floor",
               "tp_check", "pre_close", "mfe_breakeven"]
    print("=== 사유별 (정상 조건) ===")
    print(f"{'reason':22s} {'매도':4s} {'트레일':6s} {'중재':4s} 흔적")
    print("-" * 88)
    silent = []
    for r in reasons:
        res = run_case(reason=r)
        marks = [m for _, m in res["records"] if "exit" in m or "매도" in m]
        note = (marks[0][:44] if marks else ("실행도달" if (res["sold"] or res["trailing"]) else "★무흔적"))
        if not (res["sold"] or res["trailing"] or res["arbitrated"]) and not marks:
            silent.append(r)
        print(f"{r:22s} {'Y' if res['sold'] else '-':4s} {'Y' if res['trailing'] else '-':6s} "
              f"{'Y' if res['arbitrated'] else '-':4s} {note}")
        if res["err"]:
            print(f"   [오류] {res['err'][:70]}")

    print()
    print("=== 상황별 차단 (loss_cap 기준 — 손실 방어가 막히면 가장 위험) ===")
    print(f"{'상황':24s} {'매도':4s} 흔적")
    print("-" * 88)
    for label, kw in (
        ("정상", {}),
        ("exit_price<=0", {"exit_price": 0.0}),
        ("장외(주문불가)", {"order_allowed": False}),
        ("매도실패 쿨다운", {"fault": "sell_cooldown"}),
        ("다른 시장 종목", {"market": "KR"}),
        ("KR 거래정지", {"market": "KR", "halted": True}),
        ("청산락 점유중", {"lock_held": True}),
    ):
        res = run_case(reason="loss_cap", **kw)
        marks = [m for _, m in res["records"] if "exit" in m or "매도" in m or "halt" in m]
        note = (marks[0][:52] if marks else ("실행도달" if res["sold"] else "★무흔적"))
        print(f"{label:24s} {'Y' if res['sold'] else '-':4s} {note}")
        if args.verbose:
            for lvl, m in res["records"][:6]:
                print(f"      {lvl:7s} {m[:100]}")
    print()
    print(f"무흔적 사유: {silent or '없음'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
