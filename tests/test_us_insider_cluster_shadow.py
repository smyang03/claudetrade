from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import us_insider_cluster_shadow as S  # noqa: E402


def _acc(rows):
    """rows: (accession, ticker, fdate, accept) → 매수 accession 표."""
    a = pd.DataFrame([{"accession": r[0], "ticker": r[1], "fdate": r[2], "accept": r[3] if len(r) > 3 else None,
                       "bought": 100.0, "value": 1000.0} for r in rows])
    a["fdate"] = pd.to_datetime(a.fdate)
    return a


def _own(rows):
    """rows: (accession, cik, do)"""
    return pd.DataFrame([{"accession": a, "cik": c, "do": d} for a, c, d in rows])


def test_union_find_transitive():
    o = _own([("x1", 1, 1), ("x1", 2, 1), ("x2", 2, 1), ("x2", 3, 1), ("x3", 9, 1)])
    gm = S.build_groups(o)
    assert gm[1] == gm[2] == gm[3] and gm[9] != gm[1]


def test_cofiling_group_counts_once():
    # 같은 펀드 계열 3개 CIK가 한 accession에 공동신고 → 그룹 1개 → 군집 아님
    a = _acc([("a1", "ABC", "2026-09-01"), ("a2", "ABC", "2026-09-02")])
    o = _own([("a1", 1, 0), ("a1", 2, 0), ("a1", 3, 0), ("a2", 2, 0)])
    assert len(S.find_events(a, o, S.build_groups(o), do_only=False)) == 0


def test_window_edge_29_in_30_out():
    o = _own([("a1", 1, 1), ("a2", 2, 1), ("a3", 3, 1), ("b1", 11, 1), ("b2", 12, 1), ("b3", 13, 1)])
    gm = S.build_groups(o)
    inside = _acc([("a1", "AAA", "2026-08-01"), ("a2", "AAA", "2026-08-15"), ("a3", "AAA", "2026-08-30")])  # F-29
    outside = _acc([("b1", "BBB", "2026-08-01"), ("b2", "BBB", "2026-08-15"), ("b3", "BBB", "2026-08-31")])  # F-30
    ev = S.find_events(pd.concat([inside, outside]), o, gm, do_only=False)
    assert list(ev.ticker) == ["AAA"] and ev.date.iloc[0] == pd.Timestamp("2026-08-30") and ev.n_groups.iloc[0] == 3


def test_suppression_90_days_from_last_qualifying_day():
    cik = iter(range(100, 200))
    rows, own = [], []
    # 9/1~9/3 군집 도달(신호), 9/20까지 계속 도달(마지막 도달일 갱신) → 12/1(마지막 도달일+72일) 재도달은 억제
    for i, d in enumerate(["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-20",
                           "2026-12-01", "2026-12-02", "2026-12-03",
                           "2027-03-02", "2027-03-03", "2027-03-04"]):
        rows.append((f"x{i}", "ZZZ", d))
        own.append((f"x{i}", next(cik), 1))
    a, o = _acc(rows), _own(own)
    ev = S.find_events(a, o, S.build_groups(o), do_only=False)
    assert [d.strftime("%Y-%m-%d") for d in ev.date] == ["2026-09-03", "2027-03-04"]


def test_do_only_variant_drops_non_insider_accessions():
    a = _acc([("a1", "ABC", "2026-09-01"), ("a2", "ABC", "2026-09-02"), ("a3", "ABC", "2026-09-03")])
    o = _own([("a1", 1, 1), ("a2", 2, 1), ("a3", 3, 0)])  # 세 번째는 10% 주주 단독
    gm = S.build_groups(o)
    assert len(S.find_events(a, o, gm, do_only=False)) == 1
    assert len(S.find_events(a, o, gm, do_only=True)) == 0


def test_trigger_accession_by_acceptance_order():
    a = _acc([("a1", "ABC", "2026-09-01", "2026-09-01 10:00:00"),
              ("a2", "ABC", "2026-09-02", "2026-09-02 18:00:00"),
              ("a3", "ABC", "2026-09-02", "2026-09-02 11:00:00")])
    o = _own([("a1", 1, 1), ("a2", 2, 1), ("a3", 3, 1)])
    ev = S.find_events(a, o, S.build_groups(o), do_only=False)
    assert ev.trigger_accession.iloc[0] == "a2" and ev.trigger_accept.iloc[0] == "2026-09-02 18:00:00"


def test_session_mapping():
    # 2026-09-25 금, 09-28 월. 09-07 노동절
    assert S.entry_from_filing(date(2026, 9, 25)) == (date(2026, 9, 25), date(2026, 9, 28))
    assert S.entry_from_filing(date(2026, 9, 26)) == (date(2026, 9, 25), date(2026, 9, 28))  # 토요일 filing
    assert S.entry_from_filing(date(2026, 9, 4)) == (date(2026, 9, 4), date(2026, 9, 8))
    assert S.entry_from_acceptance("2026-09-24 15:59:59") == (date(2026, 9, 24), date(2026, 9, 25))
    assert S.entry_from_acceptance("2026-09-24 16:00:01") == (date(2026, 9, 25), date(2026, 9, 28))
    assert S.entry_from_acceptance("2026-09-26 10:00:00") == (date(2026, 9, 28), date(2026, 9, 29))  # 토요일 접수
    assert S.entry_from_acceptance("2026-09-04 17:00:00") == (date(2026, 9, 8), date(2026, 9, 9))  # 노동절 건너뜀
    assert S.entry_from_acceptance(None) == (None, None)


def _px(tickers, days, close=10.0, vol=1_000_000):
    rows = []
    for t in tickers:
        for i, d in enumerate(days):
            c = close * (1 + 0.001 * i)
            rows.append({"date": d, "ticker": t, "open": c, "close": c, "adj": c, "volume": vol})
    return pd.DataFrame(rows)


def test_build_rows_filters_flags_dedupe():
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-08-01", "2026-09-28")
            if S.is_session(d.date())]
    px = pd.concat([_px(["IWM"], days, 200.0), _px(["ABC"], days, 10.0), _px(["LOW"], days, 2.0)])
    M = S.matrices(px)
    ev = pd.DataFrame([
        {"ticker": "ABC", "date": pd.Timestamp("2026-09-03"), "vwap": 10.0, "n_groups": 3, "n_acc": 3,
         "trigger_accession": "a3", "trigger_accept": "2026-09-03 16:30:00", "window_accessions": ["a1", "a2", "a3"]},
        {"ticker": "LOW", "date": pd.Timestamp("2026-09-03"), "vwap": 2.0, "n_groups": 3, "n_acc": 3,
         "trigger_accession": "b3", "trigger_accept": None, "window_accessions": ["b1"]},
        {"ticker": "ABC", "date": pd.Timestamp("2026-09-25"), "vwap": 30.0, "n_groups": 4, "n_acc": 4,
         "trigger_accession": "a9", "trigger_accept": None, "window_accessions": ["a9"]},
    ])
    rows, pending = S.build_rows(ev, "U5r_all", M, lambda a, b: True, [], datetime(2026, 9, 29, 0, 30))
    assert not pending
    r0, r1, r2 = rows
    assert r0["tradable"] and r0["t0_filing"] == "2026-09-04" and r0["t0_accept"] == "2026-09-08"
    assert r0["flag_dv5m"] is True and r0["actionable"] is False
    assert not r1["tradable"] and "price<3" in r1["reject_reasons"]
    assert not r2["tradable"] and "vwap_mismatch" in r2["reject_reasons"]


def test_build_rows_price_pending_when_session_missing():
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-08-01", "2026-09-24") if S.is_session(d.date())]
    M = S.matrices(pd.concat([_px(["IWM"], days, 200.0), _px(["ABC"], days)]))
    ev = pd.DataFrame([{"ticker": "ABC", "date": pd.Timestamp("2026-09-25"), "vwap": 10.0, "n_groups": 3, "n_acc": 3,
                        "trigger_accession": "a", "trigger_accept": None, "window_accessions": []}])
    rows, pending = S.build_rows(ev, "U5r_all", M, lambda a, b: True, [], datetime(2026, 9, 25, 20, 0))
    assert rows == [] and pending == ["U5r_all|ABC|2026-09-25"]


def test_dedupe60_against_ledger():
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-08-01", "2026-09-28") if S.is_session(d.date())]
    M = S.matrices(pd.concat([_px(["IWM"], days, 200.0), _px(["ABC"], days)]))
    prev = [{"rule": "U5r_all", "ticker": "ABC", "tradable": True, "dedupe60_drop": False, "t0_filing": "2026-08-03"}]
    ev = pd.DataFrame([{"ticker": "ABC", "date": pd.Timestamp("2026-09-03"), "vwap": 10.3, "n_groups": 3, "n_acc": 3,
                        "trigger_accession": "a", "trigger_accept": None, "window_accessions": []}])
    rows, _ = S.build_rows(ev, "U5r_all", M, lambda a, b: True, prev, datetime(2026, 9, 3, 20, 0))
    assert rows[0]["tradable"] and rows[0]["dedupe60_drop"] and rows[0]["actionable"]


def test_settle_and_cohort_t():
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-01-02", "2026-09-28") if S.is_session(d.date())]
    px = [_px(["IWM"], days, 200.0)]
    for k in range(120):  # 규모 유니버스
        px.append(_px([f"U{k:03d}"], days, 10.0, vol=100_000 * (k + 10)))
    px.append(_px(["ABC"], days, 10.0, vol=1_000_000))
    M = S.matrices(pd.concat(px))
    row = {"key": "U5r_all|ABC|2026-06-01", "rule": "U5r_all", "ticker": "ABC", "t0_filing": "2026-06-02",
           "t0_accept": "2026-06-03", "flag_bottom": False, "flag_dv5m": True, "actionable": True, "window_complete": True}
    out = S.settle_rows([row], M, set())
    assert {(x["H"], x["entry"]) for x in out} == {(20, "filing"), (60, "filing"), (20, "accept"), (60, "accept")}
    x = next(x for x in out if x["H"] == 60 and x["entry"] == "filing")
    assert x["exit"] == days[days.index("2026-06-02") + 59]
    assert abs(x["x_iwm"] - (x["ret"] - S.COST - x["iwm"])) < 1e-12 and x["sz_n"] >= 5
    assert S.settle_rows([row], M, {x["skey"] for x in out}) == []  # 멱등
    st = S.cohort_stats([{"t0": "d1", "v": 0.02}, {"t0": "d1", "v": 0.04}, {"t0": "d2", "v": 0.01}, {"t0": "d3", "v": 0.02}], "v")
    assert st["dates"] == 3 and abs(st["mean_coh"] - 0.02) < 1e-12
    coh = np.array([0.03, 0.01, 0.02])
    assert abs(st["cl_t"] - coh.mean() / (coh.std(ddof=1) / np.sqrt(3))) < 1e-9


def test_load_collected_excludes_amendments_and_non_purchase(tmp_path):
    import us_form4_daily as F
    from contextlib import closing
    db = tmp_path / "f.db"

    def rec(acc, ft, code="P", ad="A"):
        return {"accession": acc, "form_type": ft, "filing_date": "2026-09-28", "acceptance_dt": "2026-09-28 10:00:00",
                "issuer_cik": "1", "issuer_symbol": "ABC", "ticker": "ABC", "period": "", "status": "ok",
                "owners": [{"owner_cik": acc[-1], "name": "x", "is_dir": 1, "is_off": 0, "is_10pct": 0, "is_other": 0,
                            "title": ""}],
                "trans": [{"seq": 0, "code": code, "acq_disp": ad, "trans_date": "2026-09-25", "shares": 10.0,
                           "price": 5.0, "post": 100.0}]}
    with closing(F.connect(db)) as conn:
        F.upsert_filing(conn, rec("acc1", "4"), "p1", "2026-09-28")
        F.upsert_filing(conn, rec("acc2", "4/A"), "p2", "2026-09-28")
        F.upsert_filing(conn, rec("acc3", "4", code="S", ad="D"), "p3", "2026-09-28")
        F.set_day(conn, "2026-09-28", "done", 3, 3)
    a, o, days = S.load_collected(db)
    assert list(a.accession) == ["acc1"] and a.value.iloc[0] == 50.0
    assert list(o.accession) == ["acc1"] and days == ["2026-09-28"]
