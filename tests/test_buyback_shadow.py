from __future__ import annotations

import inspect
import io
import json
import sys
import urllib.error
import urllib.parse
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import buyback_shadow as B  # noqa: E402

KEY = "SECRETDARTKEY0123456789"


# ------------------------------------------------------------------ 공통 픽스처
@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(B, "KR_LEDGER", tmp_path / "kr.jsonl")
    monkeypatch.setattr(B, "KR_SETTLE", tmp_path / "kr_settle.jsonl")
    monkeypatch.setattr(B, "US_LEDGER", tmp_path / "us.jsonl")
    monkeypatch.setattr(B, "US_SETTLE", tmp_path / "us_settle.jsonl")
    monkeypatch.setattr(B, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(B, "US_SEED", tmp_path / "no_seed.csv.gz")
    monkeypatch.setattr(B, "DART_SLEEP", 0.0)
    monkeypatch.setattr(B, "DART_BACKOFF", 0.0)
    monkeypatch.setattr(B, "NAVER_SLEEP", 0.0)
    monkeypatch.setattr(B, "SEC_RATE", 1e9)
    monkeypatch.setattr(B, "SEC_BACKOFF", 0.0)
    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    monkeypatch.setenv("DART_API_KEY", KEY)
    B._NAVER.clear()
    B._DART_CALLS[0] = 0
    yield


def _kr_sessions(d0: date, d1: date):
    out, d = [], d0
    while d <= d1:
        if B.kr_is_session(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _write_px(dirp: Path, code: str, cal, close: float, vol: float, jump: dict | None = None):
    lines = ["date,open,high,low,close,volume"]
    for d in cal:
        c = close * (jump or {}).get(d, 1.0)
        lines.append(f"{d},{c},{c * 1.01},{c * 0.99},{c},{vol}")
    (dirp / f"kr_{code}.csv").write_text("﻿" + "\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ KR 규칙
def test_kr_kind_direct_vs_trust():
    assert B.kr_kind("주요사항보고서(자기주식취득결정)") == "buyback"
    assert B.kr_kind("주요사항보고서(자기주식취득신탁계약체결결정)") == "trust"
    assert B.kr_kind("주요사항보고서(자기주식취득신탁계약해지결정)") is None
    assert B.kr_kind("[기재정정]주요사항보고서(자기주식취득결정)") is None
    assert B.kr_kind("주요사항보고서(자기주식처분결정)") == "other"


def test_share_year():
    assert B.share_year("20260922") == "2025"
    assert B.share_year("20260315") == "2024"


def test_kr_session_mapping_holidays():
    assert B.kr_prev_or_same(date(2026, 9, 26)) == date(2026, 9, 23)     # 토 → 추석 09-24·25 건너뜀
    assert B.kr_next_session(date(2026, 9, 23)) == date(2026, 9, 28)
    assert B.kr_next_session(date(2026, 10, 2)) == date(2026, 10, 6)     # 10-05 개천절 대체휴일
    assert B.kr_next_session(date(2026, 10, 8)) == date(2026, 10, 12)    # 10-09 한글날
    assert B.kr_prev_or_same(date(2026, 9, 22)) == date(2026, 9, 22)


# ------------------------------------------------------------------ KR 스캔(모의 DART·가격)
def _fchart(code_bars):
    return "".join(f'<item data="{d.replace("-", "")}|{o}|{h}|{l}|{c}|{v}" />' for d, (o, h, l, c, v) in code_bars)


def _kr_http(events, ds_items, shares, corp_list=None, naver=None, fail_list=False):
    calls = []

    def http(url, headers=None, timeout=30.0):
        calls.append(url)
        if "fchart.stock.naver.com" in url:
            code = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["symbol"][0]
            return (naver or {}).get(code, "").encode("cp949")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert q["crtfc_key"][0] == KEY
        if "/list.json" in url:
            if fail_list:
                raise RuntimeError(f"boom {url}")
            if "corp_code" in q:
                return json.dumps({"status": "000", "total_page": 1, "list": (corp_list or {}).get(q["corp_code"][0], [])}).encode()
            rows = [e for e in events if e["rcept_dt"] == q["bgn_de"][0]]
            return json.dumps({"status": "000" if rows else "013", "total_page": 1, "list": rows}).encode()
        if "/tsstkAqDecsn.json" in url:
            it = ds_items.get(q["corp_code"][0], [])
            return json.dumps({"status": "000" if it else "013", "list": it}).encode()
        if "/stockTotqySttus.json" in url:
            sh = shares.get(q["corp_code"][0])
            return json.dumps({"status": "000", "list": [{"se": "보통주", "istc_totqy": f"{sh:,}"},
                                                         {"se": "합계", "istc_totqy": f"{sh * 2:,}"}]}).encode()
        raise AssertionError(url)
    http.calls = calls
    return http


def _ev(rc, code, corp, nm, dt="20260922"):
    return {"rcept_no": rc, "rcept_dt": dt, "corp_code": corp, "corp_name": f"n{code}", "stock_code": code,
            "corp_cls": "Y", "report_nm": nm}


@pytest.fixture
def kr_env(monkeypatch, tmp_path):
    px = tmp_path / "px"
    px.mkdir()
    cal = _kr_sessions(date(2026, 5, 1), date(2026, 9, 29))
    _write_px(px, "069500", cal, 30000, 1e6)
    _write_px(px, "229200", cal, 10000, 1e6)
    _write_px(px, "000001", cal, 10000, 300000)     # dvol 30억 → main
    _write_px(px, "000002", cal, 10000, 300000)     # main, 비율 0.99%
    _write_px(px, "000003", cal, 5000, 200000)      # dvol 10억 → band
    _write_px(px, "000004", cal, 900, 1e7)          # 종가 < 1000
    _write_px(px, "000005", cal, 10000, 300000)     # 정정 제목 → 제외
    _write_px(px, "000006", cal, 10000, 300000)     # 신탁 → 제외
    for k in range(40):
        _write_px(px, f"1{k:05d}", cal, 5000 + 100 * k, 100000 * (1 + k))
    monkeypatch.setattr(B, "KR_PX", px)
    events = [_ev("20260922000010", "000001", "C1", "주요사항보고서(자기주식취득결정)"),
              _ev("20260922000020", "000002", "C2", "주요사항보고서(자기주식취득결정)"),
              _ev("20260922000030", "000003", "C3", "주요사항보고서(자기주식취득결정)"),
              _ev("20260922000040", "000004", "C4", "주요사항보고서(자기주식취득결정)"),
              _ev("20260922000050", "000005", "C5", "[기재정정]주요사항보고서(자기주식취득결정)"),
              _ev("20260922000060", "000006", "C6", "주요사항보고서(자기주식취득신탁계약체결결정)")]
    ds = {"C1": [{"rcept_no": "20260922000010", "aqpln_stk_ostk": "15,000"}],
          "C2": [{"rcept_no": "20260922000020", "aqpln_stk_ostk": "9,900"}],
          "C3": [{"rcept_no": "20260922000030", "aqpln_stk_ostk": "12,000"}],
          "C4": [{"rcept_no": "20260922000040", "aqpln_stk_ostk": "50,000"}]}
    shares = {c: 1_000_000 for c in ("C1", "C2", "C3", "C4")}
    nav = {"000003": _fchart([(d, (5000, 5050 + (i % 3) * 20, 4950 - (i % 2) * 20, 5000 + (i % 5) * 10, 200000))
                              for i, d in enumerate(cal)])}
    return {"cal": cal, "events": events, "ds": ds, "shares": shares, "naver": nav}


def test_kr_scan_rules_idempotent_and_no_key_leak(monkeypatch, kr_env):
    http = _kr_http(kr_env["events"], kr_env["ds"], kr_env["shares"], naver=kr_env["naver"])
    monkeypatch.setattr(B, "HTTP", http)
    now = datetime(2026, 9, 22, 18, 30)
    buf = io.StringIO()
    with redirect_stdout(buf):
        B.kr_scan(date(2026, 9, 22), date(2026, 9, 22), dry=False, now=now)
    rows = {r["stock_code"]: r for r in B.read_jsonl(B.KR_LEDGER)}
    assert set(rows) == {"000001", "000002", "000003", "000004"}           # 정정·신탁 제외
    assert rows["000001"]["rule"] == "Y1" and rows["000001"]["qualifies"]
    assert abs(rows["000001"]["ratio"] - 0.015) < 1e-12
    assert rows["000002"]["rule"] is None and "ratio<1%" in rows["000002"]["reject_reasons"]   # 0.99%
    assert rows["000003"]["rule"] == "L1_lowliq" and rows["000003"]["qualifies"]
    assert rows["000003"]["spread_ar_pct"] is not None
    assert "close<1000" in rows["000004"]["reject_reasons"]
    r1 = rows["000001"]
    assert r1["signal_date"] == "2026-09-22" and r1["entry_date"] == "2026-09-23" and r1["actionable"]
    assert r1["valueup_period"] is True and r1["fields_via"] == "orig"
    # 멱등: 재실행해도 행 수·내용 불변, 추가 DART 필드 호출 없음
    n_calls = len(http.calls)
    with redirect_stdout(buf):
        B.kr_scan(date(2026, 9, 22), date(2026, 9, 22), dry=False, now=now + timedelta(hours=1))
    rows2 = B.read_jsonl(B.KR_LEDGER)
    assert len(rows2) == 4
    assert [u for u in http.calls[n_calls:] if "tsstkAqDecsn" in u or "stockTotqySttus" in u] == []
    assert KEY not in buf.getvalue()
    assert KEY not in B.KR_LEDGER.read_text(encoding="utf-8")


def test_kr_scan_dry_writes_nothing(monkeypatch, kr_env):
    monkeypatch.setattr(B, "HTTP", _kr_http(kr_env["events"], kr_env["ds"], kr_env["shares"], naver=kr_env["naver"]))
    with redirect_stdout(io.StringIO()):
        B.kr_scan(date(2026, 9, 22), date(2026, 9, 22), dry=True, now=datetime(2026, 9, 22, 18, 30))
    assert not B.KR_LEDGER.exists()


def test_kr_backfill_not_actionable(monkeypatch, kr_env):
    monkeypatch.setattr(B, "HTTP", _kr_http(kr_env["events"], kr_env["ds"], kr_env["shares"], naver=kr_env["naver"]))
    with redirect_stdout(io.StringIO()):
        B.kr_scan(date(2026, 9, 22), date(2026, 9, 22), dry=False, now=datetime(2026, 9, 30, 5, 0))
    r = [x for x in B.read_jsonl(B.KR_LEDGER) if x["stock_code"] == "000001"][0]
    assert r["actionable"] is False and r["backfill"] is True


def test_kr_dart_error_does_not_leak_key(monkeypatch, kr_env):
    monkeypatch.setattr(B, "HTTP", _kr_http(kr_env["events"], kr_env["ds"], kr_env["shares"], fail_list=True))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = B.main(["kr-scan", "--date", "2026-09-22"])
    assert rc == 2
    out = buf.getvalue()
    assert "DART 오류" in out and KEY not in out and "crtfc_key=***" in out


def test_kr_ds005_followup_mapping(monkeypatch):
    items = {"C9": [{"rcept_no": "20260922000099", "aqpln_stk_ostk": "20,000"}]}
    corp_list = {"C9": [
        {"rcept_no": "20260922000010", "report_nm": "주요사항보고서(자기주식취득결정)"},
        {"rcept_no": "20260922000099", "report_nm": "[기재정정]주요사항보고서(자기주식취득결정)"}]}
    monkeypatch.setattr(B, "HTTP", _kr_http([], items, {}, corp_list=corp_list))
    f = B.kr_bb_fields("C9", "20260922000010", "20260922", date(2026, 9, 30))
    assert f["bb_shares"] == 20000 and f["fields_via"] == "followup"
    # 다음 원공시가 먼저 오면 체인 끝(다른 결정의 값을 쓰지 않는다)
    corp_list["C9"].insert(1, {"rcept_no": "20260922000050", "report_nm": "주요사항보고서(자기주식취득결정)"})
    f = B.kr_bb_fields("C9", "20260922000010", "20260922", date(2026, 9, 30))
    assert f["bb_shares"] is None


def test_kr_ds005_empty_common_vs_absent(monkeypatch, kr_env):
    ev = [_ev("20260922000010", "000001", "C1", "주요사항보고서(자기주식취득결정)"),
          _ev("20260922000020", "000002", "C2", "주요사항보고서(자기주식취득결정)")]
    ds = {"C1": [{"rcept_no": "20260922000010", "aqpln_stk_ostk": "-"}]}        # C2 는 DS005 미게시
    monkeypatch.setattr(B, "HTTP", _kr_http(ev, ds, {"C1": 1_000_000, "C2": 1_000_000}))
    with redirect_stdout(io.StringIO()):
        assert B.kr_scan(date(2026, 9, 22), date(2026, 9, 22), dry=False, now=datetime(2026, 9, 22, 18, 30)) == 0
    rows = {r["stock_code"]: r for r in B.read_jsonl(B.KR_LEDGER)}
    assert rows["000001"]["pending"] is False and "no_bb_common_shares" in rows["000001"]["reject_reasons"]
    assert rows["000002"]["pending"] is True and not rows["000002"]["qualifies"]


def test_kr_dup_same_day_earliest(monkeypatch):
    rows = {"KR|2": {"rcept_no": "2", "stock_code": "X", "signal_date": "2026-09-22", "rule": "Y1", "pending": False},
            "KR|1": {"rcept_no": "1", "stock_code": "X", "signal_date": "2026-09-22", "rule": "Y1", "pending": False}}
    B.kr_apply_dup(rows)
    assert rows["KR|1"]["qualifies"] and not rows["KR|2"]["qualifies"] and rows["KR|2"]["dup_same_day"]


def test_kr_settle_math(monkeypatch, kr_env):
    monkeypatch.setattr(B, "HTTP", _kr_http(kr_env["events"], kr_env["ds"], kr_env["shares"], naver=kr_env["naver"]))
    cal = kr_env["cal"]
    bk = B.kr_load_book("2026-05-01", {"069500", "229200"})
    row = {"key": "KR|x", "rule": "Y1", "stock_code": "000001", "signal_date": "2026-08-03",
           "entry_date": "2026-08-04"}
    out = B.kr_settle_rows([row], bk, set())
    rec = [x for x in out if x["h"] == 5][0]
    s = bk.ix["2026-08-03"]
    e = s + 1
    j = bk.col["000001"]
    sb = bk.size_bench(s, e, 5, float(bk.dv20[s, j]))
    assert rec["entry"] == cal[cal.index("2026-08-03") + 1]
    assert abs(rec["ex"] - (float(bk.fwd(j, e, 5)) - 0.25 - sb)) < 1e-9
    assert rec["elig_final"] is True


# ------------------------------------------------------------------ US 규칙
def test_utc_to_et_dst_and_std():
    assert B.utc_to_et("2026-09-24T20:22:45.000Z") == datetime(2026, 9, 24, 16, 22, 45)   # EDT (실측 LEA 8-K)
    assert B.utc_to_et("2026-12-01T14:00:00.000Z") == datetime(2026, 12, 1, 9, 0, 0)      # EST
    assert B.utc_to_et("2026-03-08T06:59:59.000Z") == datetime(2026, 3, 8, 1, 59, 59)     # DST 직전
    assert B.utc_to_et("2026-03-08T07:00:00.000Z") == datetime(2026, 3, 8, 3, 0, 0)       # DST 시작


def test_us_increment_vs_total_parse():
    s = ("Acme Corp. today announced that its Board of Directors has authorized an increase of $100 million to its "
         "share repurchase program, bringing the total authorization to $500 million.")
    p = B.parse_buyback_v2(s, "2026-09-24")
    assert p["usd"] == 100e6                          # 총액 표현 + 금액 2개 → 작은 쪽(증액분)
    s2 = "Acme Corp. today announced that its Board of Directors has authorized a new $250 million share repurchase program."
    assert B.parse_buyback_v2(s2, "2026-09-24")["usd"] == 250e6


def test_us_size_rules():
    assert B.us_size({"pct": 12.0, "usd": 1e9, "shares": None}, 10.0, 1e6) == (0.12, "pct")
    assert B.us_size({"pct": None, "usd": 2e8, "shares": None}, 20.0, 5e7) == (0.2, "usd")
    assert B.us_size({"pct": None, "usd": None, "shares": 6e6}, 20.0, 5e7) == (0.12, "shares")
    assert B.us_size({"pct": None, "usd": 3e9, "shares": None}, 20.0, 5e7)[0] is None     # > 0.5 결측
    assert B.us_size({"pct": None, "usd": 1e8, "shares": None}, 20.0, None)[0] is None


def test_us_split_adjusted_denominator():
    cal = pd.bdate_range("2026-08-01", "2026-09-30").strftime("%Y-%m-%d")
    iwm = pd.DataFrame({"date": cal, "open": 200.0, "close": 200.0, "volume": 1e7, "split": 0.0})
    x = pd.DataFrame({"date": cal, "open": 10.0, "close": 10.0, "volume": 2e5, "split": 0.0})
    x.loc[x.date == "2026-09-28", "split"] = 2.0          # a(09-24) 이후 2:1 분할 → yfinance Close 는 소급 조정돼 있음
    f = B.us_px_features({"IWM": iwm, "XYZ": x}, "XYZ", "2026-09-24")
    assert f["split_factor_after_a"] == 2.0 and f["raw_close_a"] == 20.0 and f["close_a_splitadj"] == 10.0
    assert abs(f["dv20_a"] - 2e6) < 1e-6
    size, basis = B.us_size({"pct": None, "usd": 1e8, "shares": None}, f["raw_close_a"], 5e7)
    assert basis == "usd" and abs(size - 0.10) < 1e-12   # 조정종가(10)를 썼다면 0.20 으로 과대


def test_us_dedupe_60_sessions_before_filters():
    ledger = {"US|a0": {"key": "US|a0", "accession": "a0", "ticker": "XYZ", "file_date": "2026-01-02",
                        "entry_date": "2026-01-05", "dedupe_drop": False}}
    t59 = np.busday_offset("2026-01-05", 59, holidays=sorted(B._ins().NYSE_HOLIDAYS))
    t60 = np.busday_offset("2026-01-05", 60, holidays=sorted(B._ins().NYSE_HOLIDAYS))
    t80 = np.busday_offset(str(t59), 21, holidays=sorted(B._ins().NYSE_HOLIDAYS))
    new = [{"key": "US|a1", "accession": "a1", "ticker": "XYZ", "file_date": "2026-03-01", "entry_date": str(t59)},
           {"key": "US|a2", "accession": "a2", "ticker": "XYZ", "file_date": "2026-03-02", "entry_date": str(t60)},
           {"key": "US|a3", "accession": "a3", "ticker": "XYZ", "file_date": "2026-04-01", "entry_date": str(t80)},
           {"key": "US|b1", "accession": "b1", "ticker": None, "file_date": "2026-03-01", "entry_date": str(t59)}]
    B.us_apply_dedupe(ledger, new)
    d = {r["accession"]: r["dedupe_drop"] for r in new}
    assert d == {"a1": True, "a2": False, "a3": True, "b1": False}   # a3 은 a2(유지) 기준 21세션 → 탈락


def test_us_sessions_and_entry():
    assert B.us_next_session(date(2026, 9, 24)) == date(2026, 9, 25)
    assert B.us_next_session(date(2026, 4, 2)) == date(2026, 4, 6)    # 성금요일(SEC 영업일, NYSE 휴장)
    assert B.us_prev_or_same(date(2026, 4, 3)) == date(2026, 4, 2)


# ------------------------------------------------------------------ US 스캔(모의 SEC·가격)
DOC = ("<html><body><p>EX-99.1</p><p>XYZ Corp. (NYSE: XYZ) today announced that its Board of Directors has authorized "
       "a new share repurchase program of up to $100 million of the Company's common stock.</p></body></html>")


def _us_http():
    calls = []

    def http(url, headers=None, timeout=30.0):
        calls.append(url)
        assert headers and "User-Agent" in headers
        if "efts.sec.gov" in url:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            if q["startdt"][0] != "2026-09-24" or q["from"][0] != "0":
                return json.dumps({"hits": {"hits": [], "total": {"value": 0}}}).encode()
            hits = [{"_id": "0000000001-26-000001:ex991.htm",
                     "_source": {"adsh": "0000000001-26-000001", "ciks": ["0000000001"], "display_names": ["XYZ Corp"],
                                 "file_date": "2026-09-24", "items": ["8.01", "9.01"], "file_type": "EX-99.1", "form": "8-K"}},
                    {"_id": "0000000001-26-000001:xyz8k.htm",
                     "_source": {"adsh": "0000000001-26-000001", "ciks": ["0000000001"], "display_names": ["XYZ Corp"],
                                 "file_date": "2026-09-24", "items": ["8.01", "9.01"], "file_type": "8-K", "form": "8-K"}},
                    {"_id": "0000000002-26-000002:ex991.htm",
                     "_source": {"adsh": "0000000002-26-000002", "ciks": ["0000000002"], "display_names": ["QQQ Inc"],
                                 "file_date": "2026-09-24", "items": ["2.02", "9.01"], "file_type": "EX-99.1", "form": "8-K"}}]
            return json.dumps({"hits": {"hits": hits, "total": {"value": len(hits)}}}).encode()
        if url.endswith("company_tickers.json"):
            return json.dumps({"0": {"cik_str": 1, "ticker": "XYZ"}, "1": {"cik_str": 2, "ticker": "QQQX"}}).encode()
        if "/Archives/edgar/data/1/" in url:
            assert url.endswith("ex991.htm")                 # EX-99 우선
            return DOC.encode()
        if "/Archives/edgar/data/2/" in url:
            raise AssertionError("2.02 동반 공시는 본문을 받지 않는다")
        if "submissions/CIK0000000001.json" in url:
            return json.dumps({"filings": {"recent": {"accessionNumber": ["0000000001-26-000001"],
                                                      "acceptanceDateTime": ["2026-09-24T20:05:00.000Z"]}}}).encode()
        if "companyconcept/CIK0000000001" in url:
            return json.dumps({"units": {"shares": [
                {"end": "2026-06-30", "filed": "2026-08-01", "val": 50_000_000},
                {"end": "2026-09-30", "filed": "2026-10-30", "val": 99_000_000}]}}).encode()   # 미래 filed → 제외
        raise AssertionError(url)
    http.calls = calls
    return http


def _us_fetch(tickers, start, end):
    cal = pd.bdate_range("2026-08-01", "2026-09-29").strftime("%Y-%m-%d")
    out = {"IWM": pd.DataFrame({"date": cal, "open": 200.0, "close": 200.0, "volume": 1e7, "split": 0.0})}
    x = pd.DataFrame({"date": cal, "open": 5.0, "close": 5.0, "volume": 1e6, "split": 0.0})
    x.loc[x.date == "2026-09-28", "split"] = 2.0
    out["XYZ"] = x
    return out


def test_us_scan_end_to_end_idempotent(monkeypatch):
    http = _us_http()
    monkeypatch.setattr(B, "HTTP", http)
    monkeypatch.setattr(B, "US_FETCH", _us_fetch)
    now = datetime(2026, 9, 25, 7, 40)
    buf = io.StringIO()
    with redirect_stdout(buf):
        B.us_scan(date(2026, 9, 24), date(2026, 9, 24), dry=False, now=now)
    rows = B.read_jsonl(B.US_LEDGER)
    assert len(rows) == 1                                  # 2.02 동반 제외
    r = rows[0]
    assert r["ticker"] == "XYZ" and r["entry_date"] == "2026-09-25" and r["anchor_date"] == "2026-09-24"
    assert r["acceptance_et"] == "2026-09-24 16:05:00"
    assert r["raw_close_a"] == 10.0 and r["so"] == 50_000_000
    assert abs(r["size"] - 0.2) < 1e-12                     # 1억 / (10 × 5천만)
    assert r["qualifies"] and r["actionable"] and r["sentence"]
    assert r["ambiguous_total_vs_increment"] is False
    n = len(http.calls)
    with redirect_stdout(buf):
        B.us_scan(date(2026, 9, 24), date(2026, 9, 24), dry=False, now=now)
    assert len(B.read_jsonl(B.US_LEDGER)) == 1
    assert not [u for u in http.calls[n:] if "/Archives/" in u]   # 같은 accession 본문 재수집 없음


def test_us_sec_429_backoff(monkeypatch):
    n = {"k": 0}

    def http(url, headers=None, timeout=30.0):
        n["k"] += 1
        if n["k"] < 3:
            raise urllib.error.HTTPError(url, 429, "Too Many", {}, None)
        return b"ok"
    monkeypatch.setattr(B, "HTTP", http)
    with redirect_stdout(io.StringIO()):
        assert B.sec_get("https://www.sec.gov/x") == b"ok"
    assert n["k"] == 3


# ------------------------------------------------------------------ 판정
def test_report_gate():
    base = date(2026, 1, 5)
    recs = [{"entry": (base + timedelta(days=i)).isoformat(), "ex": 1.0 + (i % 3) * 0.5} for i in range(30)]
    st = B.cell_stats(recs, "ex", 4, "KR")
    assert st["n"] == 30 and st["mean"] > 0 and st["t"] > 2
    assert "소액 실주문 검토" in B.gate(st)
    assert "미달" in B.gate(B.cell_stats(recs[:29], "ex", 4, "KR"))
    neg = [dict(r, ex=-r["ex"]) for r in recs]
    assert "미달" in B.gate(B.cell_stats(neg, "ex", 4, "KR"))


# ------------------------------------------------------------------ 연구 코드와의 동일성(연구 파일이 있을 때만)
def _research(name):
    p = ROOT / "tools" / "research" / f"{name}.py"
    if not p.exists():
        pytest.skip("연구 파일 없음")
    sys.path.insert(0, str(p.parent))
    sys.path.insert(0, str(ROOT))
    return pytest.importorskip(name)


def test_copies_match_research_source():
    U = _research("research_us_events_20260930")
    K = _research("research_kr_event_20260930")
    for fn in ("parse_buyback_v2", "html_text", "snippet"):
        assert inspect.getsource(getattr(B, fn)) == inspect.getsource(getattr(U, fn)), fn
    for nm in ("RETRO", "AGM", "TOTALW", "BADAMT", "DATE_RE", "OUTSH", "AMT", "SHR", "PCT", "PROG", "SENT"):
        assert getattr(B, nm).pattern == getattr(U, nm).pattern, nm
    for fn in ("ar_spread", "naver_ret", "band_bench", "nw_t", "date_series", "read_csv_bars"):
        assert inspect.getsource(getattr(B, fn)) == inspect.getsource(getattr(K, fn)), fn
    assert inspect.getsource(B.Book) == inspect.getsource(K.Book)
    assert inspect.getsource(B.us_nw_t).replace("us_nw_t", "nw_t") == inspect.getsource(U.nw_t)


def test_kr_kind_matches_research_on_real_titles():
    K = _research("research_kr_event_20260930")
    files = sorted((ROOT / "data" / "analysis" / "kr_event_20260930" / "list").glob("B001_*.jsonl"))
    if not files:
        pytest.skip("연구 list 캐시 없음")
    n = 0
    for fn in files[-8:]:
        for line in fn.open(encoding="utf-8"):
            nm = (json.loads(line).get("report_nm") or "").strip()
            assert (B.kr_kind(nm) == "buyback") == (K.event_kind(nm) == "buyback"), nm
            n += 1
    assert n > 1000
