# -*- coding: utf-8 -*-
"""US Form 4 forward 수집기 — SEC 일일 인덱스(form.YYYYMMDD.idx) → 우리 유니버스 CIK의 Form 4 → XML 파싱(코드 P·취득) (2026-09-08).

분기 데이터셋(`edgar_form4_ledger.py`)이 2026Q1에서 끝나므로 2026-04-01부터의 공백을 이 수집기로 메운다(재개 가능, 일 단위).
- CIK→ticker: https://www.sec.gov/files/company_tickers.json (캐시 data/analysis/edgar_form345/company_tickers.json)
- 원장: data/shadow/us_insider_ledger.jsonl (분기 데이터셋과 같은 키 (accession, trans_date, owner, shares)로 멱등). 보고자 전원 저장.
- User-Agent 필수, ≤8 req/s. 상태: data/analysis/edgar_form345/daily_state.json (처리한 날짜)
사용: python tools/edgar_form4_daily.py [--from 2026-04-01] [--to 2026-09-05] [--max-days N]
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
US_DIR = ROOT / "data" / "price" / "us"
CACHE = ROOT / "data" / "analysis" / "edgar_form345"
OUT = ROOT / "data" / "shadow" / "us_insider_ledger.jsonl"
STATE = CACHE / "daily_state.json"
UA = "claudetrade research smyang03@gmail.com"
SLEEP = 0.13


def _get(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def cik_map() -> dict[str, str]:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / "company_tickers.json"
    if not p.exists() or (time.time() - p.stat().st_mtime) > 7 * 86400:
        p.write_bytes(_get("https://www.sec.gov/files/company_tickers.json"))
    d = json.loads(p.read_text(encoding="utf-8"))
    universe = {q.stem[3:].upper() for q in US_DIR.glob("us_*.csv")}
    out = {}
    for v in d.values():
        tk = str(v.get("ticker") or "").upper()
        if tk in universe:
            out[str(int(v["cik_str"]))] = tk
    return out


def _qtr(d: date) -> str:
    return f"{d.year}/QTR{(d.month - 1) // 3 + 1}"


def form4_filings(day: date, ciks: dict[str, str]) -> list[tuple[str, str]]:
    """(cik, path) — 해당 일 인덱스의 Form 4(정정 4/A 제외) 중 우리 유니버스."""
    url = f"https://www.sec.gov/Archives/edgar/daily-index/{_qtr(day)}/form.{day.strftime('%Y%m%d')}.idx"
    try:
        txt = _get(url).decode("latin-1")
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "404" in msg:
            return []   # 휴장·미게시
        raise
    out = []
    for line in txt.splitlines():
        if not line.startswith("4 ") and not line.startswith("4\t"):
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 5:
            continue
        cik = parts[2].strip(); path = parts[4].strip()
        if cik in ciks:
            out.append((cik, path))
    return out


def parse_form4(txt_url: str) -> list[dict]:
    """제출 .txt(전체 서브미션)에서 ownershipDocument XML을 뽑아 코드 P(공개시장 매수) 비파생 거래를 반환."""
    raw = _get(txt_url).decode("utf-8", "ignore")
    m = re.search(r"<ownershipDocument>.*?</ownershipDocument>", raw, re.S)
    if not m:
        return []
    try:
        root = ET.fromstring(m.group(0))
    except ET.ParseError:
        return []
    def tx(el, path):
        e = el.find(path)
        return (e.text or "").strip() if e is not None and e.text else ""
    issuer = tx(root, "issuer/issuerTradingSymbol").upper()
    owners = [tx(o, "reportingOwnerId/rptOwnerName") for o in root.findall("reportingOwner")]
    rel = []
    for o in root.findall("reportingOwner"):
        r = o.find("reportingOwnerRelationship")
        if r is not None:
            rel.append(",".join(k for k in ("isDirector", "isOfficer", "isTenPercentOwner", "isOther") if (tx(r, k) in ("1", "true"))))
    out = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = tx(t, "transactionCoding/transactionCode")
        ad = tx(t, "transactionAmounts/transactionAcquiredDisposedCode/value")
        if code != "P" or ad != "A":
            continue
        try:
            shares = float(tx(t, "transactionAmounts/transactionShares/value") or 0)
            price = float(tx(t, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        out.append({"ticker": issuer, "trans_date": tx(t, "transactionDate/value"), "owners": owners, "relationship": ";".join(rel),
                    "shares": shares, "price": price, "value": round(shares * price, 2)})
    return out


def main() -> int:
    args = sys.argv[1:]
    d0 = date.fromisoformat(args[args.index("--from") + 1]) if "--from" in args else date(2026, 4, 1)
    d1 = date.fromisoformat(args[args.index("--to") + 1]) if "--to" in args else date.today() - timedelta(days=1)
    max_days = int(args[args.index("--max-days") + 1]) if "--max-days" in args else None
    ciks = cik_map()
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {"days_done": []}
    done_days = set(st["days_done"])
    seen = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line); seen.add((r["accession"], r["trans_date"], r["owner"], r["shares"]))
            except (ValueError, KeyError):
                continue
    n_days = 0; n_new = 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        d = d0
        while d <= d1:
            key = d.isoformat()
            if key in done_days or d.weekday() >= 5:
                d += timedelta(days=1); continue
            if max_days is not None and n_days >= max_days:
                break
            try:
                filings = form4_filings(d, ciks)
            except Exception as exc:  # noqa: BLE001
                print(f"[FORM4D] {key} 인덱스 실패 {exc}"); break
            time.sleep(SLEEP)
            n_f = 0
            for cik, path in filings:
                acc = path.rsplit("/", 1)[-1].replace(".txt", "")
                try:
                    rows = parse_form4(f"https://www.sec.gov/Archives/{path}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[FORM4D] {key} {acc} 파싱 실패 {exc}"); time.sleep(1.0); continue
                time.sleep(SLEEP)
                for r in rows:
                    owner = (r["owners"] or [""])[0]
                    k2 = (acc, r["trans_date"], owner, r["shares"])
                    if k2 in seen or not r["trans_date"]:
                        continue
                    row = {"ticker": r["ticker"] or ciks.get(cik, ""), "accession": acc, "trans_date": r["trans_date"], "filing_date": key,
                           "owner": owner, "owners_all": r["owners"], "relationship": r["relationship"], "shares": r["shares"],
                           "price": r["price"], "value": r["value"], "quarter": "daily", "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                    fh.write(json.dumps(row) + "\n"); seen.add(k2); n_new += 1; n_f += 1
            done_days.add(key); n_days += 1
            st["days_done"] = sorted(done_days); STATE.write_text(json.dumps(st), encoding="utf-8"); fh.flush()
            print(f"[FORM4D] {key} Form4 {len(filings)}건 → 매수 행 {n_f}")
            d += timedelta(days=1)
    print(f"[FORM4D] 완료 — 일 {n_days} · 신규 {n_new}행 · 원장 {len(seen)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
