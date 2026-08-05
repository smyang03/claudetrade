"""KR 급락 레인 브리지 엔드투엔드 리허설 (오프라인, 주문 없음).

브리지 활성화 전 사전 점검 도구(2026-08-05). 합성 원장·스텁 봇·가짜 시세로
실제 브리지 코드를 그대로 통과시키고, 각 관문의 동작을 PASS/FAIL로 판정한다.

검증 항목:
  1. 개정 R2(rv20<=8.0)로 후보가 실제로 선별되는가 (6.24<rv20<=8.0 구간 포함)
  2. 제출 kwargs가 계약과 일치하는가 (tp 0.12 / sl 0.25 / D5 / source_strategy)
  3. 극단 갭업 가드 / 슬롯 캡 / 일일 한도 / 신호 신선도 가드
  4. 생성될 포지션 메타가 risk_manager 계약 경로에 올라타는가
     (isolated 인식 -> TP/SL 후보 생성 -> 리뷰 면제 -> 트레일링 제외)

사용: python tools/kr_fallen_handoff_rehearsal.py
exit code: 전부 PASS면 0, 하나라도 FAIL이면 1.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.session_date import KST  # noqa: E402
import runtime.kr_fallen_order_bridge as bridge  # noqa: E402
from risk_manager import isolated_strategy_source  # noqa: E402
from trading_bot import TradingBot  # noqa: E402

TODAY = "2026-08-05"
PREV = "2026-08-04"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def _row(ticker: str, session: str, disc: float, rv20: float, price: float = 10000.0) -> dict:
    return {
        "session_date": session, "ticker": ticker, "pass_all": False,
        "status": "PENDING", "scanned_at": session + "T16:10:00",
        "feats": {"ma20_disc": disc, "rv20": rv20, "price": price, "chg": -8.0},
        "flags": {},
    }


class StubBot:
    def __init__(self, tmp: Path) -> None:
        self.values = {
            "KR_FALLEN_LIVE_ENABLED": True,
            "KR_FALLEN_LIVE_ACK": bridge.LIVE_ACK,
            "KR_FALLEN_ACTIVE_RULE": "R2",
        }
        self.risk = SimpleNamespace(positions=[])
        self.pending_orders: list[dict] = []
        self.today_judgment = {"consensus": {"mode": "NEUTRAL"}}
        self.submits: list[dict] = []
        self.tmp = tmp

    def _runtime_value(self, key, default=""):
        return self.values.get(key, default)

    def _runtime_bool(self, key, default=False):
        return bool(self.values.get(key, default))

    def _runtime_int(self, key, default=0):
        return int(self.values.get(key, default))

    def _runtime_float(self, key, default=0.0):
        return float(self.values.get(key, default))

    def _current_session_date_str(self, market):
        return TODAY

    def _token_for_market(self, market):
        return "token"

    def _market_budget_available(self, market):
        return 1_000_000.0

    def _broker_orderable_cash_krw(self, market):
        return 1_000_000.0

    def _new_buy_block_state(self, market, ticker, strategy, source_strategy=""):
        return {"allowed": True}

    def _submit_micro_probe_buy_order(self, **kwargs):
        self.submits.append(kwargs)
        return True


def _run(bot: StubBot, rows: list[dict], quote: float = 9500.0, minutes_after_open: int = 10):
    ledger = bot.tmp / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    opened = datetime.now(KST) - timedelta(minutes=minutes_after_open)
    with patch.object(bridge, "LEDGER", ledger), patch(
        "runtime.kr_fallen_order_bridge.regular_open_dt", return_value=opened
    ), patch(
        "runtime.kr_fallen_order_bridge.get_price", return_value={"price": quote}
    ), patch(
        "runtime.kr_fallen_order_bridge.get_runtime_path",
        side_effect=lambda *parts, **_: bot.tmp.joinpath(*parts),
    ):
        return bridge.run_kr_fallen_handoff(bot)


def main() -> int:
    with TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)

        print("[1] 개정 R2(rv20<=8.0) 선별 + 계약 kwargs")
        bot = StubBot(tmp)
        # rv20 7.5 — 구기준(6.24)이면 탈락, 개정 기준이면 통과해야 한다
        out = _run(bot, [_row("111111", PREV, disc=-30.0, rv20=7.5)])
        check("개정 R2로 후보 선별(rv20 7.5)", out.get("status") == "EVALUATED" and bot.submits,
              f"status={out.get('status')} submits={len(bot.submits)}")
        if bot.submits:
            kw = bot.submits[0]
            check("tp_pct=0.12", kw.get("tp_pct") == 0.12)
            check("sl_pct=0.25", kw.get("sl_pct") == 0.25)
            check("max_hold=5 (D5)", kw.get("max_hold") == 5)
            check("source_strategy=kr_fallen_5d", kw.get("source_strategy") == bridge.SOURCE_STRATEGY)
            check("수량=예산 내 정수주", kw.get("qty") == int(300000 // 9500), f"qty={kw.get('qty')}")

        print("[2] 할인 깊은 순 우선순위")
        bot = StubBot(tmp)
        _run(bot, [_row("222222", PREV, disc=-26.0, rv20=5.0),
                   _row("333333", PREV, disc=-40.0, rv20=5.0)])
        check("disc -40이 -26보다 먼저", bot.submits and bot.submits[0].get("ticker") == "333333")

        print("[3] 가드들")
        bot = StubBot(tmp)
        out = _run(bot, [_row("444444", PREV, disc=-30.0, rv20=5.0, price=10000.0)], quote=11500.0)
        blocked = [r for r in (out.get("results") or []) if r.get("status") == "BLOCKED"]
        check("극단 갭업(+15%) 차단", bool(blocked) and blocked[0]["reason"] == "extreme_gap_up_tp_room_gone")

        bot = StubBot(tmp)
        bot.risk.positions = [{"source_strategy": "kr_fallen_5d", "ticker": "999999",
                               "entry_session_date": "2026-08-01"}]
        out = _run(bot, [_row("555555", PREV, disc=-30.0, rv20=5.0)])
        check("슬롯 캡 차단", out.get("status") == "BLOCKED" and out.get("reason") == "strategy_open_slot_cap_reached")

        bot = StubBot(tmp)
        bot.values["KR_FALLEN_MAX_OPEN_SLOTS"] = 3
        bot.risk.positions = [{"source_strategy": "kr_fallen_5d", "ticker": "999999",
                               "entry_session_date": TODAY}]
        out = _run(bot, [_row("666666", PREV, disc=-30.0, rv20=5.0)])
        check("일일 한도 차단(슬롯 여유에도)", out.get("status") == "BLOCKED"
              and out.get("reason") == "daily_new_entry_cap_reached", f"reason={out.get('reason')}")

        bot = StubBot(tmp)
        out = _run(bot, [_row("777777", "2026-07-25", disc=-30.0, rv20=5.0)])
        check("낡은 신호(11일 전) 차단", out.get("status") == "BLOCKED"
              and out.get("reason") == "stale_signal_scan_may_be_dead", f"reason={out.get('reason')}")

        bot = StubBot(tmp)
        out = _run(bot, [_row("888888", PREV, disc=-30.0, rv20=5.0)], minutes_after_open=45)
        check("진입창(2~20분) 밖 스킵", out.get("status") == "SKIPPED"
              and out.get("reason") == "outside_entry_window")

        print("[4] risk_manager 계약 경로")
        pos = {"ticker": "111111", "source_strategy": "kr_fallen_5d", "display_currency": "KRW",
               "entry": 9500.0, "current_price": 10700.0, "tp_pct": 0.12, "sl_pct": 0.25}
        check("isolated 인식", isolated_strategy_source(pos) == "kr_fallen_5d")
        from risk_manager import RiskManager
        rm = RiskManager.__new__(RiskManager)
        isolated, cand = rm._isolated_strategy_exit_candidate(pos)
        check("TP 조건(+12.6%) -> 청산 후보", isolated and cand is not None
              and cand.get("reason") == "strategy_fixed_take_profit",
              f"reason={(cand or {}).get('reason')}")
        pos_sl = {**pos, "current_price": 7000.0}
        isolated, cand = rm._isolated_strategy_exit_candidate(pos_sl)
        check("SL 조건(-26%) -> 청산 후보", isolated and cand is not None
              and cand.get("reason") == "strategy_catastrophe_stop")
        import os
        with patch.dict(os.environ, {"CLAUDE_REVIEW_ALL_AUTOMATED_SELLS": "true"}):
            check("TP 리뷰 면제", not TradingBot._auto_sell_review_required("strategy_fixed_take_profit"))
            check("SL 리뷰 면제", not TradingBot._auto_sell_review_required("strategy_catastrophe_stop"))
            check("D5 만기 리뷰 면제", not TradingBot._auto_sell_review_required("strategy_horizon_exit"))

    failures = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f"결과: {len(RESULTS) - len(failures)}/{len(RESULTS)} PASS"
          + (f"  FAIL: {failures}" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
