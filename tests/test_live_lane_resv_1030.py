# -*- coding: utf-8 -*-
"""10-30 수리(코덱스 지적 1~3) 회귀 — 미접수 확정 주문의 재매도 · 예약번호 없는 매칭의 수량 필터 · 이전 예약 미확인 시 보류 ·
청산 뒤 잔여 예약 취소 · 끝난 예약 종결. 네트워크·주문 없음."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import kr_buyback_canary as can  # noqa: E402
import kr_panic_etf_micro as pm  # noqa: E402
import live_lane_common as llc  # noqa: E402
from live_lane_common import KST  # noqa: E402


def at(m, d, h, mi):
    return datetime(2026, m, d, h, mi, tzinfo=KST)


POS = [{"kind": "fill", "order_no": "1", "ticker": "229200", "side": "buy", "filled_qty": 29, "fill_price": 13725.0,
        "at": "2026-10-19T07:00:00+00:00"},
       {"kind": "position", "ticker": "229200", "status": "OPEN", "entry_session": "2026-10-19", "qty": 29,
        "source": "kr_panic_etf_micro", "order_state": "ok", "filled_qty": 29, "entry_price": 13725.0,
        "at": "2026-10-19T07:00:01+00:00"}]
UNKNOWN_SELL = {"kind": "order", "session": "2026-10-21", "ticker": "229200", "side": "sell", "qty": 29, "order_no": "",
                "success": None, "state": "unknown", "msg": "unknown:ConnectionError", "at": "2026-10-20T23:42:32+00:00"}
NOT_FOUND = {"kind": "order_resolved", "ref_at": UNKNOWN_SELL["at"], "ticker": "229200", "side": "sell",
             "session": "2026-10-21", "result": "not_found", "at": "2026-10-21T00:10:02+00:00"}


def _ledger(tmp_path, monkeypatch, extra, today="2026-10-21"):
    led = tmp_path / "led.jsonl"
    led.write_text("\n".join(json.dumps(r) for r in POS + extra) + "\n", encoding="utf-8")
    monkeypatch.setattr(pm, "LEDGER", led)
    monkeypatch.setattr(pm, "_today", lambda: today)
    monkeypatch.setattr(pm, "cmd_reconcile", lambda: 0)
    return led


def _rows(led):
    return [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines() if x.strip()]


class _Placer:
    def __init__(self):
        self.placed = []

    def place_order(self, *a, **k):
        self.placed.append(a)
        return {"success": True, "order_no": "0000999"}


# ── 지적 2: 미접수 확정(not_found)된 unknown 매도는 재매도를 막지 않는다
def test_live_sell_order_exists_ignores_resolved_unknown():
    assert llc.live_sell_order_exists([UNKNOWN_SELL], "2026-10-21")                 # 확정 전엔 살아 있을 수 있다
    assert not llc.live_sell_order_exists([UNKNOWN_SELL, NOT_FOUND], "2026-10-21")  # 확정 뒤엔 없다
    rej = {**UNKNOWN_SELL, "success": False, "state": "rejected", "at": "x"}
    assert not llc.live_sell_order_exists([rej], "2026-10-21")
    ok = {**UNKNOWN_SELL, "order_no": "0001", "success": True, "state": "ok", "at": "y"}
    assert llc.live_sell_order_exists([ok], "2026-10-21", "229200")
    assert not llc.live_sell_order_exists([ok], "2026-10-21", "233740")


def test_panic_sell_retries_after_unknown_resolved_not_found(tmp_path, monkeypatch):
    led = _ledger(tmp_path, monkeypatch, [UNKNOWN_SELL, NOT_FOUND])
    k = _Placer()
    monkeypatch.setattr(pm, "_kis", lambda: (k, "tok"))
    monkeypatch.setattr(pm, "kr_order_window", lambda kind: (True, ""))
    assert pm.cmd_sell(False) == 0
    assert len(k.placed) == 1 and k.placed[0][:4] == ("229200", 29, 0, "sell")     # 09:10 재매도 길이 열렸다
    assert _rows(led)[-1]["order_no"] == "0000999"


def test_canary_sell_retries_after_unknown_resolved_not_found(tmp_path, monkeypatch):
    led = tmp_path / "c.jsonl"
    rows = [{"kind": "position", "ticker": "A", "status": "OPEN", "entry_session": "2026-10-14", "qty": 3, "filled_qty": 3,
             "at": "2026-10-14T00:10:00+00:00"},
            {**UNKNOWN_SELL, "ticker": "A", "qty": 3, "session": "2026-10-21"},
            {**NOT_FOUND, "ticker": "A"}]
    led.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(can, "LEDGER", led)
    monkeypatch.setattr(can, "cmd_reconcile", lambda: 0)
    monkeypatch.setattr(can, "_today_kst", lambda: datetime(2026, 10, 21).date())
    monkeypatch.setattr(can, "kr_order_window", lambda kind: (True, ""))
    k = _Placer()
    monkeypatch.setattr(can, "_kis", lambda: (k, "tok"))
    assert can.cmd_sell(False) == 0
    assert len(k.placed) == 1 and k.placed[0][:2] == ("A", 3)


# ── 지적 3: 예약번호가 없으면 종목·수량·전송일로 좁히고, 여럿이면 보류
class _ResvRows:
    def __init__(self, rows):
        self.rows = rows

    def inquire_reserved_orders_kr(self, token, s, e, **kw):
        return self.rows


def test_find_resv_row_without_seq_filters_qty_regardless_of_order():
    resv = {"rsvn_seq": "", "ticker": "229200", "qty": 29, "placed_session": "2026-10-21", "target_session": "2026-10-22"}
    a = {"rsvn_ord_seq": "16", "pdno": "229200", "ord_rsvn_qty": "29", "rsvn_ord_ord_dt": "20261022", "odno": "0009001"}
    b = {"rsvn_ord_seq": "18", "pdno": "229200", "ord_rsvn_qty": "1", "rsvn_ord_ord_dt": "20261022", "odno": "0009002"}
    assert llc.check_resv(_ResvRows([a, b]), None, resv) == ("transmitted", "0009001")   # 1주 예약이 뒤에 와도
    assert llc.check_resv(_ResvRows([b, a]), None, resv) == ("transmitted", "0009001")
    other_day = {**a, "rsvn_ord_seq": "19", "rsvn_ord_ord_dt": "20261023", "odno": "0009003"}
    assert llc.check_resv(_ResvRows([other_day, a]), None, resv) == ("transmitted", "0009001")
    with pytest.raises(RuntimeError):
        llc.check_resv(_ResvRows([a, {**a, "rsvn_ord_seq": "20", "odno": "0009004"}]), None, resv)
    assert llc.check_resv(_ResvRows([b]), None, resv)[0] == "missing"


# ── 지적 1: 이전 예약 미확인이면 새 예약·일반 매도 보류, 청산 뒤 잔여 예약 취소, 끝난 예약 종결
RESV_OLD = {"kind": "resv_order", "ticker": "229200", "side": "sell", "qty": 29, "rsvn_seq": "63981", "state": "ok",
            "placed_session": "2026-10-21", "target_session": "2026-10-22", "at": "2026-10-21T10:21:55+00:00"}


class _ResvKis:
    def __init__(self):
        self.calls, self.cancels = [], []

    def place_reserved_order_kr(self, ticker, qty, price, side, token):
        self.calls.append((ticker, qty))
        return {"success": True, "rsvn_seq": "61142", "msg": "ok"}

    def cancel_reserved_order_kr(self, token, seq, orgno, dt):
        self.cancels.append((seq, dt))
        return {"success": True, "msg": "처리 완료"}


def test_reserve_blocked_while_previous_reservation_unconfirmed():
    k = _ResvKis()
    new, why = llc.reserve_exit_rows(k, None, POS + [RESV_OLD], "229200", 29, now=at(10, 22, 17, 59))
    assert new == [] and why.startswith("⚠️") and k.calls == []                     # 10-22 61142 재현 차단
    sold = {"kind": "order", "session": "2026-10-22", "ticker": "229200", "side": "sell", "qty": 29,
            "order_no": "0001017100", "success": True, "state": "ok", "at": "z"}
    new, why = llc.reserve_exit_rows(k, None, POS + [RESV_OLD, sold], "229200", 29, now=at(10, 22, 17, 59))
    assert new and k.calls == [("229200", 29)]                                       # 흡수 뒤엔 정상 예약


def test_panic_sell_refuses_when_past_reservation_unconfirmed(tmp_path, monkeypatch, capsys):
    _ledger(tmp_path, monkeypatch, [RESV_OLD], today="2026-10-23")
    k = _Placer()
    monkeypatch.setattr(pm, "_kis", lambda: (k, "tok"))
    monkeypatch.setattr(pm, "kr_order_window", lambda kind: (True, ""))
    assert pm.cmd_sell(False) == 3 and k.placed == []
    assert "처리결과 미확인" in capsys.readouterr().out


def test_absorb_closes_finished_reservations():
    gone = {**RESV_OLD, "rsvn_seq": "61142", "target_session": "2026-10-23", "at": "2026-10-22T08:59:50+00:00"}
    k = _ResvRows([])                                                                 # 취소분은 조회에서 사라진다(10-23 실측)
    out, notes = llc.absorb_transmitted_resv_rows(k, None, POS + [gone], "2026-10-26")
    assert out and out[0]["kind"] == "resv_resolved" and out[0]["result"] == "missing" and out[0]["ref_at"] == gone["at"]
    assert llc.resv_rows_to_absorb(POS + [gone] + out, "2026-10-26") == []          # 더는 매일 재조회하지 않는다
    assert llc.active_resv(POS + [gone] + out, "229200", "2026-10-23") is None
    today_pending = {**gone, "target_session": "2026-10-26"}                         # 오늘 전송분의 missing은 아직 종결 안 함
    out, _ = llc.absorb_transmitted_resv_rows(k, None, POS + [today_pending], "2026-10-26")
    assert out == []


def test_cancel_orphan_reservation_after_close():
    future = {**RESV_OLD, "rsvn_seq": "61142", "placed_session": "2026-10-22", "target_session": "2026-10-23",
              "at": "2026-10-22T08:59:50+00:00"}
    row = {"rsvn_ord_seq": "61142", "pdno": "229200", "ord_rsvn_qty": "29", "rsvn_ord_ord_dt": "20261023",
           "rsvn_ord_orgno": None, "odno": "", "prcs_rslt": "미처리", "cncl_ord_dt": "", "rjct_rson2": ""}

    class K(_ResvKis):
        def inquire_reserved_orders_kr(self, token, s, e, **kw):
            return [row]
    k = K()
    assert llc.orphan_resv_candidates([future], "2026-10-22", {"229200": 29}) == []   # 보유와 맞으면 그대로
    assert llc.orphan_resv_candidates([future], "2026-10-22", {}) == [future]
    out, notes = llc.cancel_orphan_resv(k, None, [future], "2026-10-22", {})
    assert k.cancels == [("61142", "20261023")]
    assert out[0]["kind"] == "resv_resolved" and out[0]["result"] == "cancelled_orphan"
    assert llc.orphan_resv_candidates([future] + out, "2026-10-22", {}) == []        # 다시 안 건드린다
    transmitted = {**row, "odno": "0001"}

    class T(_ResvKis):
        def inquire_reserved_orders_kr(self, token, s, e, **kw):
            return [transmitted]
    t = T()
    out, _ = llc.cancel_orphan_resv(t, None, [future], "2026-10-22", {})
    assert t.cancels == [] and out == []                                              # 전송분은 체결 경로가 처리

    class Boom(_ResvKis):
        def inquire_reserved_orders_kr(self, *a, **kw):
            raise ConnectionError("dns")
    out, notes = llc.cancel_orphan_resv(Boom(), None, [future], "2026-10-22", {})
    assert out == [] and notes[0].startswith("⚠️")


def test_panic_reconcile_cancels_orphan_after_close(tmp_path, monkeypatch, capsys):
    closed = {**POS[1], "status": "CLOSED", "filled_qty": 0, "exit_price": 13235.0, "at": "2026-10-22T17:35:38+00:00"}
    sold = {"kind": "order", "session": "2026-10-22", "ticker": "229200", "side": "sell", "qty": 29,
            "order_no": "0001017100", "success": True, "state": "ok", "at": "2026-10-22T17:35:37+00:00"}
    fill = {"kind": "fill", "order_no": "0001017100", "ticker": "229200", "side": "sell", "filled_qty": 29,
            "fill_price": 13235.0, "at": "2026-10-22T17:35:38+00:00"}
    future = {**RESV_OLD, "rsvn_seq": "61142", "placed_session": "2026-10-22", "target_session": "2026-10-23",
              "at": "2026-10-22T08:59:50+00:00"}
    led = _ledger(tmp_path, monkeypatch, [RESV_OLD, sold, fill, closed, future], today="2026-10-22")
    monkeypatch.undo()                                                                # cmd_reconcile 원본을 쓴다
    monkeypatch.setattr(pm, "LEDGER", led)
    monkeypatch.setattr(pm, "_today", lambda: "2026-10-22")

    class K(_ResvKis):
        def inquire_reserved_orders_kr(self, token, s, e, **kw):
            return [{"rsvn_ord_seq": "61142", "pdno": "229200", "ord_rsvn_qty": "29", "rsvn_ord_ord_dt": "20261023",
                     "odno": "", "prcs_rslt": "미처리", "cncl_ord_dt": "", "rjct_rson2": ""}]
    k = K()
    monkeypatch.setattr(pm, "_kis", lambda: (k, "tok"))

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return at(10, 22, 18, 0)
    monkeypatch.setattr(pm, "datetime", _Now)
    assert pm.cmd_reconcile() == 0
    assert k.cancels == [("61142", "20261023")] and k.calls == []                    # 취소만, 새 예약 없음
    assert _rows(led)[-1]["result"] == "cancelled_orphan"
