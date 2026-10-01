# -*- coding: utf-8 -*-
"""US Form 4 일별 수집기 v2 (2026-09-29) — EDGAR daily-index → 전체 Form 4/4A 제출 원문 → sqlite.

왜 새로 만들었나 (구 수집기 `tools/edgar_form4_daily.py` 진단)
- 05-25(메모리얼 데이, 휴장)의 `form.20260525.idx`는 존재하지 않는데 SEC는 **404가 아니라 403**을 준다.
  구 수집기는 "404"만 휴장으로 보고 나머지는 `break` 하므로, 매 실행 첫 날(05-25)에서 멈추고 rc=0으로
  끝났다(래퍼 로그 09-11 이후 전부 같은 줄). → 05-22에서 영구 정지.
- 게다가 구 수집기는 우리 가격 유니버스(data/price/us) 종목만, 보고자 첫 1명 이름만, 보고자 CIK·접수시각 없이
  저장한다. 재키잉 군집(보고자 CIK 공동신고 그룹) 규칙과 $1~5M 소형주 효과를 재현할 수 없다.

이 수집기
- 모든 Form 4 / 4/A(유니버스 제한 없음)의 제출 .txt 1건씩 받아 헤더(<ACCEPTANCE-DATETIME>, 제출 유형)와
  ownershipDocument XML(발행사 CIK·심볼, 보고자 전원 CIK·관계, 비파생 거래 전부)을 저장한다.
  규칙 필터(P/A·4만·수량·가격 > 0)는 소비자(쉐도우)가 SQL에서 건다.
- 저장: data/insider/form4.db (filings / owners / transactions / days). accession 키 upsert → 재실행 멱등.
- 휴장 판별: 인덱스가 403/404면 분기 디렉터리 목록을 본다. 파일이 없고 더 늦은 날짜 인덱스가 있으면 휴장
  (no_index, 완료 처리). 더 늦은 게 없으면 아직 미게시(pending, 완료 처리 안 함). 목록에 있는데 403이면
  레이트리밋 → 백오프 후 재시도. 한 날의 실패가 루프를 끊지 않는다. 실패한 영업일이 있으면 rc=1.
- SEC 예절: 저장소 기존 UA 재사용, 요청 간격 ≥ 0.25초(≤ 4 req/s), 429/403/5xx 백오프.
- 게시 시각 실측: daily-index는 ~22:00 ET(= KST 11:00 EDT / 12:00 EST)에 올라온다. 07:00 KST 실행은
  전날(D-1) 것만 본다 → 권장 실행 12:30 KST.

사용:
  python tools/us_form4_daily.py                       # 최근 7일 중 미완료 영업일
  python tools/us_form4_daily.py --backfill-days 45    # 최근 45일
  python tools/us_form4_daily.py --from 2026-07-01 --to 2026-08-14
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "insider" / "form4.db"
UA = "claudetrade research smyang03@gmail.com"  # 저장소 기존 SEC 접근 UA(edgar_form4_*, T3, K1)
MIN_INTERVAL = 0.25  # ≤ 4 req/s
BASE = "https://www.sec.gov/Archives/"
FORMS = ("4", "4/A")

RE_PREFIX = re.compile(r"^(NYSE\s*AMERICAN|NYSE|NASDAQ|AMEX|OTCBB|OTCQX|OTCQB|OTC|PINK)\s*[:\-]\s*")
IDX_LINE = re.compile(r"^(\S+)\s+(.*?)\s+(\d+)\s+(\d{8}|\d{4}-\d{2}-\d{2})\s+(edgar/\S+)\s*$")

_last_req = [0.0]


def log(msg: str) -> None:
    print(f"[FORM4v2] {msg}", flush=True)


# ------------------------------------------------------------------ HTTP
def _raw_get(url: str, timeout: float = 60.0) -> Tuple[int, bytes]:
    """(status, body). 테스트는 이 함수를 대체한다."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), r.read()
    except urllib.error.HTTPError as e:
        return int(e.code), b""


def _throttle() -> None:
    wait = _last_req[0] + MIN_INTERVAL - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_req[0] = time.time()


def sec_get(url: str, tries: int = 5, sleep=time.sleep, retry_403: bool = True) -> Tuple[int, bytes]:
    """429/5xx(와 retry_403이면 403)는 백오프 재시도. 404는 즉시 반환. 예외는 네트워크 오류로 재시도."""
    status, body = 0, b""
    for k in range(tries):
        _throttle()
        try:
            status, body = _raw_get(url)
        except Exception as exc:  # noqa: BLE001 — 네트워크 오류
            status, body = -1, str(exc).encode()
        if status == 200 or status == 404:
            return status, body
        if status == 403 and not retry_403:
            return status, body
        if status in (403, 429):
            sleep(min(300, 30 * (k + 1)))
        else:
            sleep(5 * (k + 1))
    return status, body


# ------------------------------------------------------------------ daily index
def qtr_dir(day: date) -> str:
    return f"edgar/daily-index/{day.year}/QTR{(day.month - 1) // 3 + 1}/"


def index_name(day: date) -> str:
    return f"form.{day.strftime('%Y%m%d')}.idx"


def parse_daily_index(txt: str) -> List[Dict[str, str]]:
    """Form 4 / 4/A 행 → [{form, cik, path, accession}].
    제출 1건이 신고인(발행사·보고자)마다 한 줄씩, 그리고 **경로도 각 CIK 디렉터리로 달리** 나온다
    (edgar/data/<issuer>/<acc>.txt, edgar/data/<owner>/<acc>.txt) → accession 기준으로 중복 제거."""
    out: Dict[str, Dict[str, str]] = {}
    for line in txt.splitlines():
        if not (line.startswith("4 ") or line.startswith("4/A ") or line.startswith("4\t") or line.startswith("4/A\t")):
            continue
        m = IDX_LINE.match(line.rstrip())
        if not m:
            continue
        form, _name, cik, _d, path = m.groups()
        acc = path.rsplit("/", 1)[-1].replace(".txt", "")
        if form not in FORMS:
            continue
        if acc in out:  # 같은 제출의 다른 CIK 디렉터리 경로 — 한쪽이 404일 때 대체 경로로 쓴다
            out[acc]["alt_paths"].append(path)
            continue
        out[acc] = {"form": form, "cik": cik, "path": path, "accession": acc, "alt_paths": []}
    return list(out.values())


_listing_cache: Dict[str, Optional[List[str]]] = {}


def quarter_listing(day: date, sleep=time.sleep) -> Optional[List[str]]:
    key = qtr_dir(day)
    if key not in _listing_cache:
        st, body = sec_get(BASE + key, sleep=sleep)
        _listing_cache[key] = sorted(set(re.findall(r"form\.(\d{8})\.idx", body.decode("latin-1")))) if st == 200 else None
    return _listing_cache[key]


def fetch_index(day: date, today: date, sleep=time.sleep) -> Tuple[str, str]:
    """('ok', txt) | ('no_index', '') 휴장 | ('pending', '') 미게시 | ('error', msg)."""
    url = BASE + qtr_dir(day) + index_name(day)
    for attempt in range(4):
        st, body = sec_get(url, sleep=sleep, retry_403=False)
        if st == 200:
            return "ok", body.decode("latin-1")
        if st in (403, 404):
            lst = quarter_listing(day, sleep=sleep)
            ymd = day.strftime("%Y%m%d")
            if lst is None:
                return "error", f"index {st}, listing unavailable"
            if ymd in lst:  # 파일은 있는데 403 → 레이트리밋
                sleep(min(300, 60 * (attempt + 1)))
                continue
            if any(x > ymd for x in lst) or (today - day).days > 7:
                return "no_index", ""
            return "pending", ""
        return "error", f"index HTTP {st}"
    return "error", "index 403 persisted"


# ------------------------------------------------------------------ submission parse
def clean_sym(s: str) -> str:
    """T3 심볼 정리 규칙 그대로."""
    s = (s or "").strip().upper()
    s = RE_PREFIX.sub("", s).strip()
    if s in ("", "NONE", "N/A", "NA", "-") or re.search(r"[,;\s]", s):
        return ""
    return s.replace(".", "-").replace("/", "-")


def _t(el, path: str) -> str:
    if el is None:
        return ""
    e = el.find(path)
    return (e.text or "").strip() if e is not None and e.text else ""


def _flag(el, path: str) -> int:
    return 1 if _t(el, path).lower() in ("1", "true") else 0


def _num(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", "")) if s else None
    except ValueError:
        return None


def parse_submission(raw: str) -> Dict:
    """제출 .txt 전체 → {accession, form_type, acceptance_dt, filing_date, issuer_*, ticker, owners[], trans[], status}."""
    out: Dict = {"owners": [], "trans": [], "status": "ok"}
    m = re.search(r"<ACCEPTANCE-DATETIME>\s*(\d{14})", raw)
    out["acceptance_dt"] = (datetime.strptime(m.group(1), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S") if m else None)
    m = re.search(r"ACCESSION NUMBER:\s*(\S+)", raw)
    out["accession"] = m.group(1) if m else None
    m = re.search(r"CONFORMED SUBMISSION TYPE:\s*(\S+)", raw)
    out["form_type"] = m.group(1) if m else None
    m = re.search(r"FILED AS OF DATE:\s*(\d{8})", raw)
    out["filing_date"] = datetime.strptime(m.group(1), "%Y%m%d").date().isoformat() if m else None
    m = re.search(r"<ownershipDocument>.*?</ownershipDocument>", raw, re.S)
    if not m:
        out["status"] = "no_xml"
        return out
    try:
        root = ET.fromstring(m.group(0))
    except ET.ParseError:
        out["status"] = "xml_error"
        return out
    doc_type = _t(root, "documentType")
    if doc_type:
        out["form_type"] = doc_type
    out["period"] = _t(root, "periodOfReport")
    out["issuer_cik"] = _t(root, "issuer/issuerCik").lstrip("0")
    out["issuer_symbol"] = _t(root, "issuer/issuerTradingSymbol")
    out["ticker"] = clean_sym(out["issuer_symbol"])
    for o in root.findall("reportingOwner"):
        rel = o.find("reportingOwnerRelationship")
        cik = _t(o, "reportingOwnerId/rptOwnerCik").lstrip("0")
        if not cik:
            continue
        out["owners"].append({
            "owner_cik": cik, "name": _t(o, "reportingOwnerId/rptOwnerName"),
            "is_dir": _flag(rel, "isDirector"), "is_off": _flag(rel, "isOfficer"),
            "is_10pct": _flag(rel, "isTenPercentOwner"), "is_other": _flag(rel, "isOther"),
            "title": _t(rel, "officerTitle")})
    for i, t in enumerate(root.findall("nonDerivativeTable/nonDerivativeTransaction")):
        out["trans"].append({
            "seq": i, "code": _t(t, "transactionCoding/transactionCode"),
            "acq_disp": _t(t, "transactionAmounts/transactionAcquiredDisposedCode/value"),
            "trans_date": _t(t, "transactionDate/value")[:10],
            "shares": _num(_t(t, "transactionAmounts/transactionShares/value")),
            "price": _num(_t(t, "transactionAmounts/transactionPricePerShare/value")),
            "post": _num(_t(t, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"))})
    return out


# ------------------------------------------------------------------ DB
SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
  accession TEXT PRIMARY KEY, form_type TEXT, filing_date TEXT, acceptance_dt TEXT, index_day TEXT,
  issuer_cik TEXT, issuer_symbol TEXT, ticker TEXT, period TEXT, path TEXT, status TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS owners (
  accession TEXT, owner_cik TEXT, name TEXT, is_dir INT, is_off INT, is_10pct INT, is_other INT, title TEXT,
  PRIMARY KEY (accession, owner_cik));
CREATE TABLE IF NOT EXISTS transactions (
  accession TEXT, seq INT, code TEXT, acq_disp TEXT, trans_date TEXT, shares REAL, price REAL, post REAL,
  PRIMARY KEY (accession, seq));
CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, status TEXT, n_filings INT, n_fetched INT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_filings_fdate ON filings(filing_date);
CREATE INDEX IF NOT EXISTS ix_filings_ticker ON filings(ticker);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    return conn


def upsert_filing(conn: sqlite3.Connection, rec: Dict, path: str, index_day: str) -> None:
    acc = rec["accession"]
    with conn:
        conn.execute("INSERT OR REPLACE INTO filings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (acc, rec.get("form_type"), rec.get("filing_date") or index_day, rec.get("acceptance_dt"), index_day,
                      rec.get("issuer_cik"), rec.get("issuer_symbol"), rec.get("ticker"), rec.get("period"), path,
                      rec.get("status"), datetime.now(timezone.utc).isoformat(timespec="seconds")))
        conn.execute("DELETE FROM owners WHERE accession=?", (acc,))
        conn.execute("DELETE FROM transactions WHERE accession=?", (acc,))
        conn.executemany("INSERT OR REPLACE INTO owners VALUES (?,?,?,?,?,?,?,?)",
                         [(acc, o["owner_cik"], o["name"], o["is_dir"], o["is_off"], o["is_10pct"], o["is_other"], o["title"])
                          for o in rec["owners"]])
        conn.executemany("INSERT OR REPLACE INTO transactions VALUES (?,?,?,?,?,?,?,?)",
                         [(acc, t["seq"], t["code"], t["acq_disp"], t["trans_date"], t["shares"], t["price"], t["post"])
                          for t in rec["trans"]])


def done_accessions(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute("SELECT accession FROM filings WHERE status IN ('ok','no_xml','xml_error','missing')")}


def set_day(conn: sqlite3.Connection, day: str, status: str, n_f: int, n_new: int) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO days VALUES (?,?,?,?,?)",
                     (day, status, n_f, n_new, datetime.now(timezone.utc).isoformat(timespec="seconds")))


def day_status(conn: sqlite3.Connection) -> Dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT day, status FROM days")}


# ------------------------------------------------------------------ run
def collect_day(conn: sqlite3.Connection, day: date, today: date, sleep=time.sleep,
                max_filings: Optional[int] = None) -> str:
    key = day.isoformat()
    st, txt = fetch_index(day, today, sleep=sleep)
    if st in ("no_index", "pending"):
        if st == "no_index":
            set_day(conn, key, "no_index", 0, 0)
        log(f"{key} 인덱스 {st}")
        return st
    if st == "error":
        set_day(conn, key, "error", 0, 0)
        log(f"{key} 인덱스 실패 {txt}")
        return "error"
    rows = parse_daily_index(txt)
    have = done_accessions(conn)
    todo = [r for r in rows if r["accession"] not in have]
    if max_filings is not None:
        todo = todo[:max_filings]
    n_new, n_err = 0, 0
    n_missing = 0
    for r in todo:
        code, body, path = 0, b"", r["path"]
        for path in [r["path"]] + r.get("alt_paths", []):
            code, body = sec_get(BASE + path, sleep=sleep)
            if code != 404:
                break
        if code == 404:  # 모든 CIK 경로가 404 — 영구 결측으로 기록(재시도 안 함)
            upsert_filing(conn, {"accession": r["accession"], "form_type": r["form"], "status": "missing",
                                 "owners": [], "trans": []}, r["path"], key)
            n_missing += 1
            continue
        if code != 200:
            n_err += 1
            continue
        rec = parse_submission(body.decode("utf-8", "ignore"))
        rec["accession"] = rec.get("accession") or r["accession"]
        if not rec.get("form_type"):
            rec["form_type"] = r["form"]
        upsert_filing(conn, rec, path, key)
        n_new += 1
    partial = max_filings is not None and len(todo) < len([r for r in rows if r["accession"] not in have])
    status = "error" if n_err else ("partial" if partial else "done")
    set_day(conn, key, status, len(rows), n_new)
    log(f"{key} Form4/4A {len(rows)}건 · 신규 {n_new} · 결측(404) {n_missing} · 실패 {n_err} → {status}")
    return status


def business_days(d0: date, d1: date):
    d = d0
    while d <= d1:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from")
    ap.add_argument("--to", dest="d_to")
    ap.add_argument("--backfill-days", type=int, default=None)
    ap.add_argument("--max-filings", type=int, default=None, help="일당 상한(테스트용)")
    ap.add_argument("--redo", action="store_true", help="완료된 날도 다시 인덱스 대조")
    ap.add_argument("--db", default=str(DB_PATH))
    a = ap.parse_args(argv)
    today = date.today()
    d1 = date.fromisoformat(a.d_to) if a.d_to else today - timedelta(days=1)
    if a.d_from:
        d0 = date.fromisoformat(a.d_from)
    else:
        d0 = today - timedelta(days=a.backfill_days if a.backfill_days else 7)
    fails = 0
    with closing(connect(Path(a.db))) as conn:
        ds = day_status(conn)
        for d in business_days(d0, d1):
            if not a.redo and ds.get(d.isoformat()) in ("done", "no_index"):
                continue
            st = collect_day(conn, d, today, max_filings=a.max_filings)
            if st == "error":
                fails += 1
        n = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
        n_p = conn.execute("SELECT COUNT(DISTINCT accession) FROM transactions WHERE code='P' AND acq_disp='A'").fetchone()[0]
    log(f"완료 — 범위 {d0}~{d1} · 실패일 {fails} · filings {n} · P/A accession {n_p}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
