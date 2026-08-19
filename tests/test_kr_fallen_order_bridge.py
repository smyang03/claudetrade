from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bot.session_date import KST
import runtime.kr_fallen_order_bridge as bridge


def _row(ticker: str, session: str, disc: float, rv20: float, pass_all: bool = False, price: float = 10000.0) -> dict:
    return {
        "session_date": session, "ticker": ticker, "pass_all": pass_all,
        "status": "PENDING", "scanned_at": session + "T16:10:00",
        "feats": {"ma20_disc": disc, "rv20": rv20, "price": price, "chg": -8.0},
        "flags": {},
    }


class FakeBot:
    def __init__(self, tmp: Path, *, live: bool = True, ack: bool = True) -> None:
        self.values = {
            "KR_FALLEN_LIVE_ENABLED": live,
            "KR_FALLEN_LIVE_ACK": bridge.LIVE_ACK if ack else "",
            "KR_FALLEN_ACTIVE_RULE": "R2",
        }
        self.risk = SimpleNamespace(positions=[])
        self.pending_orders: list[dict] = []
        self.today_judgment = {"consensus": {"mode": "NEUTRAL"}}
        self.submits: list[dict] = []
        self.tmp = tmp

    def _runtime_value(self, key, default=""):
        v = self.values.get(key, default)
        return v

    def _runtime_bool(self, key, default=False):
        return bool(self.values.get(key, default))

    def _runtime_int(self, key, default=0):
        return int(self.values.get(key, default))

    def _runtime_float(self, key, default=0.0):
        return float(self.values.get(key, default))

    def _current_session_date_str(self, market):
        return "2026-08-05"

    def _token_for_market(self, market):
        return "token"

    def _market_budget_available(self, market):
        return 1_000_000.0

    def _broker_orderable_cash_krw(self, market):
        return 1_000_000.0

    def _new_buy_block_state(self, market, ticker, strategy, source_strategy=""):
        return {"allowed": True}

    def _has_open_position(self, ticker, market):
        return any(
            str(p.get("ticker") or "") == ticker and float(p.get("qty", 0) or 0) > 0
            for p in self.risk.positions
        )

    def _has_pending_order(self, ticker, market):
        return any(str(o.get("ticker") or "") == ticker for o in self.pending_orders)

    def _submit_micro_probe_buy_order(self, **kwargs):
        self.submits.append(kwargs)
        return True


def _run(bot: FakeBot, ledger_rows: list[dict], quote_price: float = 9500.0):
    ledger = bot.tmp / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ledger_rows), encoding="utf-8")
    opened = datetime.now(KST) - timedelta(minutes=10)
    with patch.object(bridge, "LEDGER", ledger), patch(
        "runtime.kr_fallen_order_bridge.regular_open_dt", return_value=opened
    ), patch(
        "runtime.kr_fallen_order_bridge.get_price", return_value={"price": quote_price}
    ), patch(
        "runtime.kr_fallen_order_bridge.get_runtime_path",
        side_effect=lambda *parts, **_: bot.tmp.joinpath(*parts),
    ):
        return bridge.run_kr_fallen_handoff(bot)


def test_disabled_without_live_flag_and_ack(tmp_path: Path) -> None:
    result = _run(FakeBot(tmp_path, live=False), [_row("A", "2026-08-04", -30.0, 5.0)])
    assert result["status"] == "DISABLED"
    result = _run(FakeBot(tmp_path, ack=False), [_row("A", "2026-08-04", -30.0, 5.0)])
    assert result["status"] == "DISABLED"


def test_submits_rule_matching_candidate_with_contract(tmp_path: Path) -> None:
    bot = FakeBot(tmp_path)
    rows = [
        _row("DEEP", "2026-08-04", -32.0, 5.0),      # R2 충족 (깊은 할인·저변동)
        _row("SHALLOW", "2026-08-04", -10.0, 5.0),    # 할인 미달 — 제외
        _row("VOLATILE", "2026-08-04", -40.0, 12.0),  # 고변동 — 제외
    ]
    result = _run(bot, rows)
    assert result["status"] == "EVALUATED"
    assert len(bot.submits) == 1
    s = bot.submits[0]
    assert s["ticker"] == "DEEP"
    assert s["source_strategy"] == "kr_fallen_5d"
    assert s["tp_pct"] == 0.12 and s["sl_pct"] == 0.25 and s["max_hold"] == 5
    assert s["qty"] == int(300000 // 9500)


def test_one_per_day_even_with_multiple_candidates(tmp_path: Path) -> None:
    bot = FakeBot(tmp_path)
    rows = [_row("A", "2026-08-04", -35.0, 5.0), _row("B", "2026-08-04", -30.0, 5.0)]
    result = _run(bot, rows)
    assert len(bot.submits) == 1
    assert bot.submits[0]["ticker"] == "A"  # 할인 깊은 순 우선


def test_slot_cap_blocks(tmp_path: Path) -> None:
    bot = FakeBot(tmp_path)
    bot.risk.positions = [{"source_strategy": "kr_fallen_5d", "ticker": "HELD"}]
    result = _run(bot, [_row("A", "2026-08-04", -30.0, 5.0)])
    assert result["last_result"]["reason"] == "strategy_open_slot_cap_reached" if "last_result" in result else result["reason"] == "strategy_open_slot_cap_reached"
    assert not bot.submits


def test_extreme_gap_up_guard(tmp_path: Path) -> None:
    bot = FakeBot(tmp_path)
    # 신호일 종가 10,000 → 현재가 11,200 (+12%) — TP 여지 소진, 차단
    result = _run(bot, [_row("A", "2026-08-04", -30.0, 5.0, price=10000.0)], quote_price=11200.0)
    assert result["results"][0]["reason"] == "extreme_gap_up_tp_room_gone"
    assert not bot.submits


def _row_gap(ticker: str, session: str, gap: float, disc: float, rv20: float = 12.0,
             price: float = 10000.0) -> dict:
    r = _row(ticker, session, disc, rv20, price=price)
    r["feats"]["gap"] = gap
    return r


def test_union_rule_r2_plus_r4_keeps_discount_priority(tmp_path: Path) -> None:
    # 2026-08-10 합집합 설계: R2+R4 활성 시 두 규칙 후보가 한 풀에서 할인깊은순
    bot = FakeBot(tmp_path)
    bot.values["KR_FALLEN_ACTIVE_RULE"] = "R2+R4"
    rows = [
        _row_gap("GAPPY", "2026-08-04", gap=-5.0, disc=-16.0, rv20=12.0),  # R4만 충족
        _row("DEEP", "2026-08-04", -32.0, 5.0),                            # R2 충족·할인 최심
    ]
    result = _run(bot, rows)
    assert result["status"] == "EVALUATED"
    assert result["rule"] == "R2+R4"
    assert len(bot.submits) == 1
    assert bot.submits[0]["ticker"] == "DEEP"          # 랭킹(할인깊은순) 불변
    assert result["results"][0]["matched"] == ["R2"]   # 판정 분해용 매칭 태그


def test_union_rule_r4_only_candidate_enters(tmp_path: Path) -> None:
    bot = FakeBot(tmp_path)
    bot.values["KR_FALLEN_ACTIVE_RULE"] = "R2+R4"
    rows = [
        _row_gap("GAPPY", "2026-08-04", gap=-5.0, disc=-16.0, rv20=12.0),  # R2 미달·R4 충족
        _row("VOLATILE", "2026-08-04", -40.0, 12.0),                       # 둘 다 미달
    ]
    result = _run(bot, rows)
    assert result["status"] == "EVALUATED"
    assert len(bot.submits) == 1 and bot.submits[0]["ticker"] == "GAPPY"
    assert bot.submits[0]["selected_reason"] == "kr_fallen_r4"  # 설정 라벨이 아니라 충족 규칙


def test_single_rule_backward_compat_unchanged(tmp_path: Path) -> None:
    # 기본 "R2"에서 R4-only 후보는 여전히 제외 — 현행 동작 불변(후방 호환 고정)
    bot = FakeBot(tmp_path)
    result = _run(bot, [_row_gap("GAPPY", "2026-08-04", gap=-5.0, disc=-16.0, rv20=12.0)])
    assert result["status"] == "SKIPPED" and result["reason"] == "no_rule_candidates"
    assert not bot.submits


def test_invalid_active_rule_fails_closed(tmp_path: Path) -> None:
    # 설계 D3: 무효 토큰은 조용한 R2 폴백이 아니라 ERROR + 무주문
    bot = FakeBot(tmp_path)
    bot.values["KR_FALLEN_ACTIVE_RULE"] = "R2+R9"
    result = _run(bot, [_row("DEEP", "2026-08-04", -32.0, 5.0)])
    assert result["status"] == "ERROR"
    assert str(result["reason"]).startswith("invalid_active_rule")
    assert not bot.submits


def _run_with_blind(bot: FakeBot, ledger_rows: list[dict], blind_rows: list[dict],
                    quote_price: float = 9500.0):
    ledger = bot.tmp / "ledger.jsonl"
    blind = bot.tmp / "blind.jsonl"
    ledger.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ledger_rows), encoding="utf-8")
    blind.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in blind_rows), encoding="utf-8")
    opened = datetime.now(KST) - timedelta(minutes=10)
    with patch.object(bridge, "LEDGER", ledger), patch.object(bridge, "BLIND_LEDGER", blind), patch(
        "runtime.kr_fallen_order_bridge.regular_open_dt", return_value=opened
    ), patch(
        "runtime.kr_fallen_order_bridge.get_price", return_value={"price": quote_price}
    ), patch(
        "runtime.kr_fallen_order_bridge.get_runtime_path",
        side_effect=lambda *parts, **_: bot.tmp.joinpath(*parts),
    ):
        return bridge.run_kr_fallen_handoff(bot)


def _blind_row(ticker: str, session: str, gap: float, disc: float) -> dict:
    r = _row_gap(ticker, session, gap=gap, disc=disc, rv20=12.0)
    r["observe_only"] = True
    r["capture_path"] = "blindspot_gap_disc"
    return r


def test_blindspot_ignored_when_flag_off(tmp_path: Path) -> None:
    # 2026-08-13 사각 편입: 스위치 off(기본)면 사각 원장은 후보로 읽지 않는다
    bot = FakeBot(tmp_path)
    bot.values["KR_FALLEN_ACTIVE_RULE"] = "R2+R4"
    result = _run_with_blind(bot, [], [_blind_row("BLIND", "2026-08-04", gap=-5.0, disc=-16.0)])
    assert result["status"] == "SKIPPED" and result["reason"] == "no_prior_session_candidates"
    assert not bot.submits


def test_blindspot_enters_with_r4b_attribution_when_enabled(tmp_path: Path) -> None:
    bot = FakeBot(tmp_path)
    bot.values["KR_FALLEN_ACTIVE_RULE"] = "R2+R4"
    bot.values["KR_FALLEN_BLINDSPOT_ENTRY_ENABLED"] = True
    result = _run_with_blind(bot, [], [_blind_row("BLIND", "2026-08-04", gap=-5.0, disc=-16.0)])
    assert result["status"] == "EVALUATED"
    assert result["rule"].endswith("+blind")
    assert len(bot.submits) == 1 and bot.submits[0]["ticker"] == "BLIND"
    # 귀속: 본 원장 경유(kr_fallen_r4)와 분리되는 r4b 태그
    assert bot.submits[0]["selected_reason"] == "kr_fallen_r4b"


def test_blindspot_same_ticker_main_ledger_wins(tmp_path: Path) -> None:
    bot = FakeBot(tmp_path)
    bot.values["KR_FALLEN_ACTIVE_RULE"] = "R2+R4"
    bot.values["KR_FALLEN_BLINDSPOT_ENTRY_ENABLED"] = True
    main = [_row_gap("DUP", "2026-08-04", gap=-5.0, disc=-16.0, rv20=12.0)]
    blind = [_blind_row("DUP", "2026-08-04", gap=-5.0, disc=-16.0)]
    result = _run_with_blind(bot, main, blind)
    assert result["status"] == "EVALUATED"
    assert len(bot.submits) == 1
    assert bot.submits[0]["selected_reason"] == "kr_fallen_r4"  # 본 원장 우선 → r4b 아님


def test_blindspot_merged_pool_keeps_discount_priority(tmp_path: Path) -> None:
    # 통합 정렬은 할인 깊은 순 하나 — 사각이 더 깊으면 1순위
    bot = FakeBot(tmp_path)
    bot.values["KR_FALLEN_ACTIVE_RULE"] = "R2+R4"
    bot.values["KR_FALLEN_BLINDSPOT_ENTRY_ENABLED"] = True
    main = [_row("DEEP", "2026-08-04", -26.0, 5.0)]  # R2 충족, 할인 -26
    blind = [_blind_row("DEEPER", "2026-08-04", gap=-6.0, disc=-31.0)]
    result = _run_with_blind(bot, main, blind)
    assert result["status"] == "EVALUATED"
    assert bot.submits[0]["ticker"] == "DEEPER"
    assert bot.submits[0]["selected_reason"] == "kr_fallen_r4b"


def test_blocks_ticker_already_held_by_another_strategy(tmp_path: Path) -> None:
    """교차전략 동일티커 중복매수 차단 (2026-08-17).

    슬롯 계산은 자기 source만 세므로, 코어처럼 다른 전략이 든 티커를 이 레인이
    또 사면 한 브로커 포지션에 두 전략의 lot이 섞여 청산 소유권이 깨진다.
    """
    bot = FakeBot(tmp_path)
    bot.risk.positions = [{"ticker": "HELD", "qty": 3, "market": "KR"}]
    result = _run(bot, [_row("HELD", "2026-08-04", -30.0, 5.0)])
    assert result["status"] == "EVALUATED"
    assert bot.submits == []
    assert result["results"][0]["reason"] == "already_holding_any_strategy"


def test_blocks_ticker_with_pending_order(tmp_path: Path) -> None:
    bot = FakeBot(tmp_path)
    bot.pending_orders = [{"ticker": "WAITING", "market": "KR"}]
    result = _run(bot, [_row("WAITING", "2026-08-04", -30.0, 5.0)])
    assert result["status"] == "EVALUATED"
    assert bot.submits == []
    assert result["results"][0]["reason"] == "pending_order_exists"


def _rows_n(n, session="2026-08-04"):
    return [_row(f"T{i:03d}", session, -30.0 - i, 5.0) for i in range(n)]


def test_phase3_off_keeps_single_entry(tmp_path: Path) -> None:
    """Phase 3 장전(2026-08-19): 기본 OFF에서는 현행 일1건 완전 불변."""
    bot = FakeBot(tmp_path)
    bot.values["KR_FALLEN_ACTIVE_RULE"] = "R2"
    result = _run(bot, _rows_n(12))
    assert len(bot.submits) == 1
    assert result["phase3"] == {"enabled": False, "k": 12, "daily_cap": 1, "submitted_now": 1}


def test_phase3_on_k2_allows_two(tmp_path: Path) -> None:
    bot = FakeBot(tmp_path)
    bot.values["KR_FALLEN_PHASE3_CAPACITY_ENABLED"] = True
    result = _run(bot, _rows_n(2))
    assert len(bot.submits) == 2
    assert result["phase3"]["daily_cap"] == 2


def test_phase3_on_k10_allows_three(tmp_path: Path) -> None:
    bot = FakeBot(tmp_path)
    bot.values["KR_FALLEN_PHASE3_CAPACITY_ENABLED"] = True
    result = _run(bot, _rows_n(12))
    assert len(bot.submits) == 3
    assert result["phase3"]["daily_cap"] == 3


def test_phase3_on_k1_stays_single(tmp_path: Path) -> None:
    bot = FakeBot(tmp_path)
    bot.values["KR_FALLEN_PHASE3_CAPACITY_ENABLED"] = True
    result = _run(bot, _rows_n(1))
    assert len(bot.submits) == 1
    assert result["phase3"]["daily_cap"] == 1


def test_phase3_slot_cap_three_enforced(tmp_path: Path) -> None:
    """동시 보유 ≤3 (사전등록): 기보유 2 + 신규 → 1건만 추가."""
    bot = FakeBot(tmp_path)
    bot.values["KR_FALLEN_PHASE3_CAPACITY_ENABLED"] = True
    bot.risk.positions = [
        {"ticker": "H1", "qty": 1, "source_strategy": "kr_fallen_5d"},
        {"ticker": "H2", "qty": 1, "source_strategy": "kr_fallen_5d"},
    ]
    result = _run(bot, _rows_n(12))
    assert len(bot.submits) == 1  # 슬롯 3 - 보유 2 = 1
