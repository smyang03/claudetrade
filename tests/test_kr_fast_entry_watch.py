# -*- coding: utf-8 -*-
"""특례편입 감시(10-31) — 순위·신규상장·이전상장·CSV 중복·알림 창. 네트워크 없음."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import kr_fast_entry_watch as W  # noqa: E402


def _row(t, rank, name=None):
    return {"ticker": t, "name": name or t, "rank": rank, "mcap_eok": 100_000 - rank}


def test_rank_stocks_skips_etf_and_keeps_alphanumeric():
    st = [{"stockEndType": "stock", "itemCode": "005930", "stockName": "삼성전자", "marketValue": "12,891,044"},
          {"stockEndType": "etf", "itemCode": "379800", "stockName": "KODEX", "marketValue": "104,308"},
          {"stockEndType": "stock", "itemCode": "0126z0", "stockName": "삼성에피스", "marketValue": "50,000"}]
    r = W.rank_stocks(st)
    assert [(x["ticker"], x["rank"]) for x in r] == [("005930", 1), ("0126Z0", 2)]
    assert r[0]["mcap_eok"] == 12_891_044


def test_new_listing_flagged_old_skipped_and_alert_window(tmp_path):
    csvp = tmp_path / "c.csv"
    csvp.write_text("index,announce_date,effective_date,ticker,name,action,source_url\n", encoding="utf-8")
    bars = {"OLD001": [f"d{i}" for i in range(60)], "NEW001": [f"2026-10-{i:02d}" for i in range(1, 13)]}
    rk = {"KOSPI": [_row("OLD001", 1), _row("NEW001", 30), _row("FAR001", 200)], "KOSDAQ": []}
    cands, st = W.scan({}, rk, bars_fn=lambda t: bars[t], today="2026-10-31", csv_path=csvp)
    assert [c["ticker"] for c in cands] == ["NEW001"]
    c = cands[0]
    assert c["session_n"] == 12 and c["in_cutoff"] and c["since"] == "2026-10-01" and c["index"] == "KOSPI200"
    assert st["tickers"]["OLD001"]["old"] is True and "FAR001" not in st["tickers"]
    assert [x["ticker"] for x in W.alerts(cands)] == ["NEW001"]
    # 이미 CSV에 상장 이후 add 행이 있으면 알림 안 함
    csvp.write_text("index,announce_date,effective_date,ticker,name,action,source_url\n"
                    "KOSPI200,2026-10-20,2026-11-13,NEW001,신규,add,u\n", encoding="utf-8")
    cands2, _ = W.scan({}, rk, bars_fn=lambda t: bars[t], today="2026-10-31", csv_path=csvp)
    assert cands2[0]["in_csv"] and W.alerts(cands2) == []


def test_old_ticker_not_refetched_and_transfer_detected(tmp_path):
    csvp = tmp_path / "c.csv"
    calls = []
    st = {"tickers": {"KAKAO1": {"market": "KOSDAQ", "old": True}, "OLD001": {"market": "KOSPI", "old": True}}}
    rk = {"KOSPI": [_row("OLD001", 3), _row("KAKAO1", 20)], "KOSDAQ": []}
    cands, st = W.scan(st, rk, bars_fn=lambda t: calls.append(t) or [], today="2026-11-02", csv_path=csvp)
    assert calls == []                                   # old 종목은 일봉 재조회 없음
    assert [(c["ticker"], c["kind"], c["session_n"]) for c in cands] == [("KAKAO1", "이전상장", 1)]
    cands, st = W.scan(st, rk, bars_fn=lambda t: [], today="2026-11-03", csv_path=csvp)
    assert cands[0]["session_n"] == 2 and cands[0]["since"] == "2026-11-02"


def test_alert_window_bounds():
    base = {"in_cutoff": True, "in_csv": False}
    assert W.alerts([{**base, "session_n": W.ALERT_FROM - 1}]) == []
    assert len(W.alerts([{**base, "session_n": W.ALERT_FROM}, {**base, "session_n": W.ALERT_TO}])) == 2
    assert W.alerts([{**base, "session_n": 15, "in_cutoff": False}]) == []
