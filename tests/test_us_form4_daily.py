from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import us_form4_daily as F  # noqa: E402


def _xml(doc_type="4", owners=(("0001111", "Doe John", "1", "0"),), trans=(("P", "A", "1000", "5.00"),), symbol="ABC"):
    own = "".join(
        f"<reportingOwner><reportingOwnerId><rptOwnerCik>{c}</rptOwnerCik><rptOwnerName>{n}</rptOwnerName></reportingOwnerId>"
        f"<reportingOwnerRelationship><isDirector>{d}</isDirector><isOfficer>{o}</isOfficer>"
        f"<officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner>"
        for c, n, d, o in owners)
    tr = "".join(
        f"<nonDerivativeTransaction><transactionDate><value>2026-09-24</value></transactionDate>"
        f"<transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>"
        f"<transactionAmounts><transactionShares><value>{sh}</value></transactionShares>"
        f"<transactionPricePerShare><value>{px}</value></transactionPricePerShare>"
        f"<transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode></transactionAmounts>"
        f"<postTransactionAmounts><sharesOwnedFollowingTransaction><value>5000</value></sharesOwnedFollowingTransaction>"
        f"</postTransactionAmounts></nonDerivativeTransaction>"
        for code, ad, sh, px in trans)
    return (f"<SEC-DOCUMENT>x\n<ACCEPTANCE-DATETIME>20260928161059\nACCESSION NUMBER:\t\t0000000001-26-000001\n"
            f"CONFORMED SUBMISSION TYPE:\t{doc_type}\nFILED AS OF DATE:\t\t20260928\n<XML>\n<?xml version=\"1.0\"?>\n"
            f"<ownershipDocument><documentType>{doc_type}</documentType><periodOfReport>2026-09-24</periodOfReport>"
            f"<issuer><issuerCik>0000999</issuerCik><issuerName>ABC Inc</issuerName><issuerTradingSymbol>{symbol}</issuerTradingSymbol></issuer>"
            f"{own}<nonDerivativeTable>{tr}</nonDerivativeTable></ownershipDocument>\n</XML>")


def test_parse_codes_owners_acceptance():
    rec = F.parse_submission(_xml(owners=(("0001111", "A", "1", "0"), ("0002222", "B Fund", "0", "0")),
                                  trans=(("P", "A", "1000", "5.00"), ("S", "D", "10", "6"), ("A", "A", "50", "0"))))
    assert rec["status"] == "ok" and rec["form_type"] == "4"
    assert rec["acceptance_dt"] == "2026-09-28 16:10:59" and rec["filing_date"] == "2026-09-28"
    assert rec["issuer_cik"] == "999" and rec["ticker"] == "ABC"
    assert [o["owner_cik"] for o in rec["owners"]] == ["1111", "2222"]
    assert rec["owners"][0]["is_dir"] == 1 and rec["owners"][1]["is_dir"] == 0
    codes = [(t["code"], t["acq_disp"]) for t in rec["trans"]]
    assert codes == [("P", "A"), ("S", "D"), ("A", "A")]
    assert rec["trans"][0]["shares"] == 1000.0 and rec["trans"][0]["price"] == 5.0


def test_amendment_and_symbol_cleanup():
    rec = F.parse_submission(_xml(doc_type="4/A", symbol="nyse: brk.b"))
    assert rec["form_type"] == "4/A" and rec["ticker"] == "BRK-B"
    assert F.parse_submission(_xml(symbol="ABC, DEF"))["ticker"] == ""
    assert F.parse_submission("no xml here")["status"] == "no_xml"


def test_parse_daily_index_dedupes_and_filters():
    txt = "\n".join([
        "Form Type   Company Name                                                  CIK         Date Filed  File Name",
        "-----",
        "4                ACUITY INC.  (DE)                                             1144215     20260928    edgar/data/1144215/0001891538-26-000015.txt   ",
        "4                Tzitzon Nicholas                                              1891538     20260928    edgar/data/1891538/0001891538-26-000015.txt   ",
        "4/A              Alexander Gregory R.                                          2105279     20260928    edgar/data/2105279/0001493152-26-044576.txt",
        "424B2            Some Bank                                                     1           20260928    edgar/data/1/0000000001-26-000009.txt",
        "40-F             Other                                                         2           20260928    edgar/data/2/0000000002-26-000009.txt",
    ])
    rows = F.parse_daily_index(txt)
    assert [(r["form"], r["accession"]) for r in rows] == [("4", "0001891538-26-000015"), ("4/A", "0001493152-26-044576")]


class FakeSEC:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, timeout=60.0):
        self.calls.append(url)
        v = self.routes.get(url, (404, b""))
        if isinstance(v, list):
            return v.pop(0) if len(v) > 1 else v[0]
        return v


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(F, "MIN_INTERVAL", 0.0)
    F._listing_cache.clear()

    def install(routes):
        fs = FakeSEC(routes)
        monkeypatch.setattr(F, "_raw_get", fs)
        return fs
    return install


QDIR = F.BASE + "edgar/daily-index/2026/QTR3/"


def test_holiday_403_is_no_index_not_break(fake):
    # 09-07 노동절: 인덱스 403, 목록에 없음 + 더 늦은 파일 있음 → 휴장
    fake({QDIR + "form.20260907.idx": (403, b""),
          QDIR: (200, b'<a href="form.20260904.idx"> <a href="form.20260908.idx">')})
    st, _ = F.fetch_index(date(2026, 9, 7), date(2026, 9, 29), sleep=lambda s: None)
    assert st == "no_index"


def test_403_listed_file_is_rate_limit_retry(fake):
    fs = fake({QDIR + "form.20260908.idx": [(403, b""), (200, b"ok-index")],
               QDIR: (200, b'<a href="form.20260908.idx">')})
    st, txt = F.fetch_index(date(2026, 9, 8), date(2026, 9, 29), sleep=lambda s: None)
    assert st == "ok" and txt == "ok-index"
    assert fs.calls.count(QDIR + "form.20260908.idx") == 2


def test_not_yet_published_is_pending(fake):
    fake({QDIR + "form.20260928.idx": (403, b""), QDIR: (200, b'<a href="form.20260925.idx">')})
    st, _ = F.fetch_index(date(2026, 9, 28), date(2026, 9, 29), sleep=lambda s: None)
    assert st == "pending"


def test_collect_day_idempotent(fake, tmp_path):
    idx = ("4                ABC Inc                                                       999         20260928    "
           "edgar/data/999/0000000001-26-000001.txt\n")
    fs = fake({QDIR + "form.20260928.idx": (200, idx.encode()),
               F.BASE + "edgar/data/999/0000000001-26-000001.txt": (200, _xml().encode())})
    db = tmp_path / "f.db"
    for _ in range(2):
        with closing(F.connect(db)) as conn:
            assert F.collect_day(conn, date(2026, 9, 28), date(2026, 9, 29), sleep=lambda s: None) == "done"
    assert fs.calls.count(F.BASE + "edgar/data/999/0000000001-26-000001.txt") == 1  # 두 번째는 재요청 없음
    with closing(sqlite3.connect(str(db))) as c:
        assert c.execute("SELECT COUNT(*) FROM filings").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 1
        assert c.execute("SELECT status FROM days").fetchone()[0] == "done"


def test_filing_fetch_error_marks_day_error(fake, tmp_path):
    idx = ("4                ABC Inc                                                       999         20260928    "
           "edgar/data/999/0000000001-26-000001.txt\n")
    fake({QDIR + "form.20260928.idx": (200, idx.encode()),
          F.BASE + "edgar/data/999/0000000001-26-000001.txt": (503, b"")})
    with closing(F.connect(tmp_path / "f.db")) as conn:
        assert F.collect_day(conn, date(2026, 9, 28), date(2026, 9, 29), sleep=lambda s: None) == "error"


def test_404_falls_back_to_alt_cik_path_then_missing(fake, tmp_path):
    idx = ("4                ABC Inc                                                       999         20260928    "
           "edgar/data/999/0000000001-26-000001.txt\n"
           "4                Doe John                                                      1111        20260928    "
           "edgar/data/1111/0000000001-26-000001.txt\n"
           "4                XYZ Inc                                                       777         20260928    "
           "edgar/data/777/0000000001-26-000002.txt\n")
    fake({QDIR + "form.20260928.idx": (200, idx.encode()),
          F.BASE + "edgar/data/999/0000000001-26-000001.txt": (404, b""),
          F.BASE + "edgar/data/1111/0000000001-26-000001.txt": (200, _xml().encode())})
    db = tmp_path / "f.db"
    with closing(F.connect(db)) as conn:
        assert F.collect_day(conn, date(2026, 9, 28), date(2026, 9, 29), sleep=lambda s: None) == "done"
    with closing(sqlite3.connect(str(db))) as c:
        st = dict(c.execute("SELECT accession, status FROM filings").fetchall())
    assert st == {"0000000001-26-000001": "ok", "0000000001-26-000002": "missing"}
