"""격리 레인 예약매도(A1, 10-21) — 예약 창·전송일·처리결과 분류·이중 매도 방지·unknown 되짚기 (네트워크·주문 없음)."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import kr_buyback_canary as can  # noqa: E402
import kr_panic_etf_micro as pm  # noqa: E402
from live_lane_common import (KST, active_resv, check_resv, classify_resv_row, kr_resv_window,  # noqa: E402
                              reserve_exit_rows, resolve_unknown_order_rows, resv_exit_decision,
                              resv_target_session, unresolved_unknown_orders)


def at(m, d, h, mi):
    return datetime(2026, m, d, h, mi, tzinfo=KST)


class _KisResvOk:
    def __init__(self):
        self.calls = []

    def place_reserved_order_kr(self, ticker, qty, price, side, token):
        self.calls.append((ticker, qty, price, side))
        return {"success": True, "rsvn_seq": "17", "msg": "정상처리", "order_no": ""}


class _KisBoom:
    def place_reserved_order_kr(self, *a, **k):
        raise ConnectionError("NameResolutionError")

    def inquire_reserved_orders_kr(self, *a, **k):
        raise ConnectionError("NameResolutionError")

    def find_kr_order_truth_by_session(self, *a, **k):
        raise ConnectionError("NameResolutionError")


def test_resv_window_and_target_session():
    assert kr_resv_window(at(10, 21, 17, 49))[0]                      # 접수 창(15:40~23:40)
    assert kr_resv_window(at(10, 22, 0, 30))[0]                       # 00:10~07:30
    assert not kr_resv_window(at(10, 21, 23, 50))[0]                  # 서버 초기화
    assert not kr_resv_window(at(10, 22, 8, 42))[0]                   # 08:42 매도 작업 시각은 창 밖
    assert not kr_resv_window(at(10, 21, 15, 30))[0]
    assert resv_target_session(at(10, 21, 17, 49)) == date(2026, 10, 22)
    assert resv_target_session(at(10, 22, 0, 30)) == date(2026, 10, 22)
    assert resv_target_session(at(10, 23, 17, 0)) == date(2026, 10, 26)   # 금요일 저녁 → 월요일
    assert resv_target_session(at(10, 8, 17, 0)) == date(2026, 10, 12)    # 10-09 한글날 건너뜀


def test_classify_resv_row_uses_portal_fields():
    assert classify_resv_row(None) == ("missing", "조회 결과에 예약 행 없음")
    assert classify_resv_row({"odno": "0001234", "prcs_rslt": "정상"}) == ("transmitted", "0001234")
    assert classify_resv_row({"odno": "", "rjct_rson2": "매도가능수량 부족"})[0] == "rejected"
    assert classify_resv_row({"odno": "0000000", "cncl_ord_dt": "20261022"})[0] == "cancelled"
    assert classify_resv_row({"odno": "", "prcs_rslt": ""}) == ("pending", "미처리")


def test_exit_decision_waits_before_grace_then_falls_back():
    assert resv_exit_decision("transmitted", at(10, 22, 8, 42)) == "skip"
    assert resv_exit_decision("rejected", at(10, 22, 8, 42)) == "fallback"
    assert resv_exit_decision("cancelled", at(10, 22, 8, 42)) == "fallback"
    assert resv_exit_decision("pending", at(10, 22, 8, 42)) == "wait"      # 전송 전
    assert resv_exit_decision("pending", at(10, 22, 9, 10)) == "fallback"  # 09:10 재확인에도 미처리면 일반 매도
    assert resv_exit_decision("missing", at(10, 22, 9, 10)) == "fallback"


def test_reserve_exit_rows_idempotent_and_window_guarded():
    kis = _KisResvOk()
    rows, why = reserve_exit_rows(kis, None, [], "229200", 29, now=at(10, 21, 17, 49))
    assert rows and rows[0]["kind"] == "resv_order" and rows[0]["state"] == "ok" and rows[0]["rsvn_seq"] == "17"
    assert rows[0]["target_session"] == "2026-10-22" and rows[0]["placed_session"] == "2026-10-21"
    assert kis.calls == [("229200", 29, 0, "sell")]
    again, why = reserve_exit_rows(kis, None, rows, "229200", 29, now=at(10, 21, 18, 0))
    assert again == [] and "이미 있음" in why and len(kis.calls) == 1
    assert reserve_exit_rows(kis, None, [], "229200", 29, now=at(10, 22, 8, 42))[0] == []
    assert reserve_exit_rows(kis, None, [], "229200", 0, now=at(10, 21, 18, 0))[0] == []
    boom, _ = reserve_exit_rows(_KisBoom(), None, [], "229200", 29, now=at(10, 21, 18, 0))
    assert boom[0]["state"] == "unknown"                                   # 예외는 unknown으로 — 살아 있을 수 있다
    assert active_resv(boom, "229200", "2026-10-22") is boom[0]


def test_check_resv_matches_by_seq_or_qty():
    class K:
        def inquire_reserved_orders_kr(self, token, s, e, **kw):
            assert (s, e) == ("20261021", "20261022")        # 접수일~전송일(조회 기준은 전송일 rsvn_ord_ord_dt — 10-21 실측)
            return [{"rsvn_ord_seq": "0000016", "pdno": "229200", "ord_rsvn_qty": "10", "odno": "", "rjct_rson2": "x"},
                    {"rsvn_ord_seq": "0000017", "pdno": "229200", "ord_rsvn_qty": "29", "odno": "0009001"}]
    resv = {"rsvn_seq": "17", "ticker": "229200", "qty": 29, "placed_session": "2026-10-21", "target_session": "2026-10-22"}
    assert check_resv(K(), None, resv) == ("transmitted", "0009001")
    real = {"rsvn_ord_seq": "63981", "rsvn_ord_ord_dt": "20261022", "rsvn_ord_rcit_dt": "20261021", "pdno": "229200",
            "ord_dvsn_cd": "05", "ord_rsvn_qty": "29", "tot_ccld_qty": "0", "cncl_ord_dt": "", "rjct_rson2": "", "odno": "",
            "prcs_rslt": "미처리", "ord_dvsn_name": "현금매도"}                      # 10-21 19:21 실계좌 응답 그대로
    assert classify_resv_row(real) == ("pending", "미처리")
    assert check_resv(K(), None, {**resv, "rsvn_seq": ""}) == ("transmitted", "0009001")   # seq 없으면 종목·수량으로


def test_unknown_orders_are_recovered_or_closed():
    unknown = {"kind": "order", "session": "2026-10-21", "ticker": "229200", "side": "sell", "qty": 29, "order_no": "",
               "success": None, "state": "unknown", "at": "2026-10-20T23:42:32+00:00"}
    assert unresolved_unknown_orders([unknown]) == [unknown]

    class Found:
        def find_kr_order_truth_by_session(self, token, **kw):
            assert kw == {"ticker": "229200", "side": "sell", "session": "20261021", "qty": 29}
            return {"order_no": "0016500000", "order_qty": 29}

    class NotFound:
        def find_kr_order_truth_by_session(self, token, **kw):
            return None

    out, notes = resolve_unknown_order_rows(Found(), None, [unknown], now=at(10, 22, 8, 42))
    assert out[0]["kind"] == "order" and out[0]["order_no"] == "0016500000" and out[0]["state"] == "ok"
    assert out[0]["recovered_from_at"] == unknown["at"]
    assert unresolved_unknown_orders([unknown] + out) == []
    out, _ = resolve_unknown_order_rows(NotFound(), None, [unknown], now=at(10, 22, 8, 42))     # 다음 날 → 미접수 확정
    assert out[0]["kind"] == "order_resolved" and out[0]["result"] == "not_found"
    assert unresolved_unknown_orders([unknown] + out) == []
    out, _ = resolve_unknown_order_rows(NotFound(), None, [unknown], now=at(10, 21, 8, 50))     # 매도는 당일 장중에도 확정
    assert out[0]["result"] == "not_found"
    buy_unknown = {**unknown, "side": "buy", "at": "2026-10-21T06:20:00+00:00"}
    out, _ = resolve_unknown_order_rows(NotFound(), None, [buy_unknown], now=at(10, 21, 15, 50))  # 매수는 장 마감 전 보류
    assert out == []
    out, _ = resolve_unknown_order_rows(NotFound(), None, [buy_unknown], now=at(10, 21, 16, 12))
    assert out[0]["result"] == "not_found"
    out, notes = resolve_unknown_order_rows(_KisBoom(), None, [unknown], now=at(10, 22, 8, 42))  # 조회 실패 → 다음 실행
    assert out == [] and "실패" in notes[0]


def _panic_ledger(tmp_path, monkeypatch, extra, stub_reconcile=True):
    led = tmp_path / "led.jsonl"
    rows = [{"kind": "fill", "order_no": "1", "ticker": "229200", "side": "buy", "filled_qty": 29, "fill_price": 13725.0},
            {"kind": "position", "ticker": "229200", "status": "OPEN", "entry_session": "2026-10-19", "qty": 29,
             "source": "kr_panic_etf_micro", "order_state": "ok", "filled_qty": 29, "entry_price": 13725.0}] + extra
    led.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(pm, "LEDGER", led)
    monkeypatch.setattr(pm, "_today", lambda: "2026-10-22")
    if stub_reconcile:
        monkeypatch.setattr(pm, "cmd_reconcile", lambda: 0)
    return led


def test_panic_sell_skips_market_order_when_reservation_transmitted(tmp_path, monkeypatch, capsys):
    resv = {"kind": "resv_order", "ticker": "229200", "side": "sell", "qty": 29, "rsvn_seq": "17", "state": "ok",
            "placed_session": "2026-10-21", "target_session": "2026-10-22"}
    led = _panic_ledger(tmp_path, monkeypatch, [resv])
    placed = []

    class K:
        def place_order(self, *a, **k):
            placed.append(a); return {"success": True, "order_no": "X"}
    monkeypatch.setattr(pm, "_kis", lambda: (K(), "tok"))
    monkeypatch.setattr(pm, "check_resv", lambda kis, token, r: ("transmitted", "0009001"))
    monkeypatch.setattr(pm, "resv_exit_decision", lambda status: "skip")
    assert pm.cmd_sell(False) == 0
    assert placed == []                                                     # 이중 매도 없음
    last = json.loads(led.read_text(encoding="utf-8").splitlines()[-1])
    assert last["kind"] == "order" and last["order_no"] == "0009001" and last["via"] == "resv"
    assert pm.cmd_sell(False) == 0 and "이미 있음" in capsys.readouterr().out


def test_panic_sell_refuses_when_reservation_status_unknown(tmp_path, monkeypatch, capsys):
    resv = {"kind": "resv_order", "ticker": "229200", "side": "sell", "qty": 29, "rsvn_seq": "17", "state": "ok",
            "placed_session": "2026-10-21", "target_session": "2026-10-22"}
    _panic_ledger(tmp_path, monkeypatch, [resv])
    placed = []

    class K:
        def place_order(self, *a, **k):
            placed.append(a); return {"success": True, "order_no": "X"}
    monkeypatch.setattr(pm, "_kis", lambda: (K(), "tok"))
    monkeypatch.setattr(pm, "check_resv", lambda kis, token, r: (_ for _ in ()).throw(ConnectionError("dns")))
    assert pm.cmd_sell(False) == 3
    assert placed == [] and "조회 실패" in capsys.readouterr().out


def test_panic_sell_falls_back_to_market_when_reservation_rejected(tmp_path, monkeypatch):
    resv = {"kind": "resv_order", "ticker": "229200", "side": "sell", "qty": 29, "rsvn_seq": "17", "state": "ok",
            "placed_session": "2026-10-21", "target_session": "2026-10-22"}
    led = _panic_ledger(tmp_path, monkeypatch, [resv])
    placed = []

    class K:
        def place_order(self, *a, **k):
            placed.append(a); return {"success": True, "order_no": "X"}
    monkeypatch.setattr(pm, "_kis", lambda: (K(), "tok"))
    monkeypatch.setattr(pm, "check_resv", lambda kis, token, r: ("rejected", "매도가능수량 부족"))
    monkeypatch.setattr(pm, "kr_order_window", lambda kind: (True, ""))
    assert pm.cmd_sell(False) == 0
    assert len(placed) == 1 and placed[0][:4] == ("229200", 29, 0, "sell")
    assert json.loads(led.read_text(encoding="utf-8").splitlines()[-1])["order_no"] == "X"


def test_panic_reconcile_reserves_after_fill_and_flags_overdue(tmp_path, monkeypatch, capsys):
    led = _panic_ledger(tmp_path, monkeypatch, [], stub_reconcile=False)
    monkeypatch.setattr(pm, "_today", lambda: "2026-10-21")
    kis = _KisResvOk()
    monkeypatch.setattr(pm, "_kis", lambda: (kis, "tok"))

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return at(10, 21, 16, 12)
    monkeypatch.setattr(pm, "datetime", _Now)
    rc = pm.cmd_reconcile()
    rows = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines()]
    resv = [r for r in rows if r.get("kind") == "resv_order"]
    assert resv and resv[0]["target_session"] == "2026-10-22" and kis.calls == [("229200", 29, 0, "sell")]
    assert rc == 3 and "청산 지연" in capsys.readouterr().out            # 10-19 매수분이 10-21 장 마감까지 OPEN


def test_panic_reconcile_absorbs_transmitted_reservation_when_sell_task_never_ran(tmp_path, monkeypatch, capsys):
    """A1 주 시나리오: PC가 08:42·09:10에 꺼져 있다 16:12에 켜짐 — 예약매도는 09:00에 체결됐다. reconcile이 전송분을 흡수해
    포지션을 닫아야 하고(재예약 없음), 아니면 매일 유령 예약 + 원장 drift."""
    import live_lane_common as llc
    resv = {"kind": "resv_order", "ticker": "229200", "side": "sell", "qty": 29, "rsvn_seq": "17", "state": "ok",
            "placed_session": "2026-10-21", "target_session": "2026-10-22"}
    led = _panic_ledger(tmp_path, monkeypatch, [resv], stub_reconcile=False)
    kis = _KisResvOk()
    kis.get_order_fill_kr = lambda token, order_no, ticker, d: {"filled_qty": 29, "fill_price": 13500.0}
    monkeypatch.setattr(pm, "_kis", lambda: (kis, "tok"))
    monkeypatch.setattr(llc, "check_resv", lambda k, t, r: ("transmitted", "0009001"))

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return at(10, 22, 16, 12)
    monkeypatch.setattr(pm, "datetime", _Now)
    rc = pm.cmd_reconcile()
    rows = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines()]
    assert [r for r in rows if r.get("kind") == "order"][-1]["order_no"] == "0009001"
    assert [r for r in rows if r.get("kind") == "position"][-1]["status"] == "CLOSED"
    assert [r for r in rows if r.get("kind") == "close"][-1]["pnl_krw"] == pm.close_pnl(13725.0, 13500.0, 29, "229200")
    assert kis.calls == [] and rc == 0                                      # 재예약 없음·지연 경고 없음
    assert "흡수" in capsys.readouterr().out
    assert llc.resv_rows_to_absorb(rows, "2026-10-22") == []


def test_canary_due_next_session_and_reserve_cmd(tmp_path, monkeypatch):
    pos = {"A": {"status": "OPEN", "filled_qty": 3, "entry_session": "2026-10-14"},   # 10-15(1) 16(2) 19(3) 20(4) 21(5)
           "B": {"status": "OPEN", "filled_qty": 2, "entry_session": "2026-10-16"}}
    assert [tk for tk, _ in can.due_next_session(pos, date(2026, 10, 21))] == ["A"]
    assert can.due_next_session(pos, date(2026, 10, 20)) == []
    led = tmp_path / "c.jsonl"
    led.write_text("\n".join(json.dumps({"kind": "position", "ticker": tk, **p}) for tk, p in pos.items()) + "\n",
                   encoding="utf-8")
    monkeypatch.setattr(can, "LEDGER", led)
    kis = _KisResvOk()
    monkeypatch.setattr(can, "_kis", lambda: (kis, "tok"))

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return at(10, 20, 16, 10)
    monkeypatch.setattr(can, "datetime", _Now)
    assert can.cmd_reserve_sell(False) == 0
    assert kis.calls == [("A", 3, 0, "sell")]
    assert can.cmd_reserve_sell(False) == 0 and len(kis.calls) == 1        # 멱등
