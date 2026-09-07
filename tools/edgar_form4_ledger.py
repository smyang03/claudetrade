# -*- coding: utf-8 -*-
"""US 내부자 공개시장 매수 원장 — SEC Form 3/4/5 구조화 데이터셋(분기 zip) 백필 (2026-09-07 신규 전략 P8).

출처: https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/<yyyy>q<n>_form345.zip
  NONDERIV_TRANS.tsv (TRANS_CODE 'P' = 공개시장 매수, TRANS_ACQUIRED_DISP_CD 'A'), SUBMISSION.tsv(ISSUERTRADINGSYMBOL, FILING_DATE),
  REPORTINGOWNER.tsv(RPTOWNER_RELATIONSHIP).
- 최신 분기는 SEC 게시 지연(09-07 기준 2026q1까지) → 그 이후는 forward 수집기 몫(미착수, 보고서에 명시).
- 우리 US 유니버스(data/price/us) 종목만 저장. 원장: data/shadow/us_insider_ledger.jsonl (accession+trans_date+owner 멱등)
사용: python tools/edgar_form4_ledger.py [--quarters 2025q3,2025q4,2026q1]
"""
from __future__ import annotations

import csv
import io
import json
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
US_DIR = ROOT / "data" / "price" / "us"
CACHE = ROOT / "data" / "analysis" / "edgar_form345"
OUT = ROOT / "data" / "shadow" / "us_insider_ledger.jsonl"
UA = "claudetrade research smyang03@gmail.com"
DEFAULT_Q = "2025q3,2025q4,2026q1"


def _download(q: str) -> Path | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{q}_form345.zip"
    if p.exists() and p.stat().st_size > 1000:
        return p
    url = f"https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        data = urllib.request.urlopen(req, timeout=120).read()
    except Exception as exc:  # noqa: BLE001
        print(f"[FORM4] {q} 다운로드 실패 {exc}"); return None
    p.write_bytes(data); return p


def _read_tsv(z: zipfile.ZipFile, name: str) -> list[dict]:
    nm = next((n for n in z.namelist() if n.upper().endswith(name.upper())), None)
    if not nm:
        return []
    with z.open(nm) as fh:
        txt = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
        return list(csv.DictReader(txt, delimiter="\t"))


def _fdate(s: str) -> str | None:
    s = (s or "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def main() -> int:
    args = sys.argv[1:]
    quarters = (args[args.index("--quarters") + 1] if "--quarters" in args else DEFAULT_Q).split(",")
    universe = {p.stem[3:].upper() for p in US_DIR.glob("us_*.csv")}
    seen = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line); seen.add((r["accession"], r["trans_date"], r["owner"], r["shares"]))
            except (ValueError, KeyError):
                continue
    n_new = 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        for q in quarters:
            p = _download(q.strip())
            if not p:
                continue
            z = zipfile.ZipFile(p)
            subs = {r["ACCESSION_NUMBER"]: r for r in _read_tsv(z, "SUBMISSION.tsv")}
            owners: dict[str, list[dict]] = {}
            for r in _read_tsv(z, "REPORTINGOWNER.tsv"):
                owners.setdefault(r["ACCESSION_NUMBER"], []).append(r)
            n_q = 0
            for r in _read_tsv(z, "NONDERIV_TRANS.tsv"):
                if (r.get("TRANS_CODE") or "").strip() != "P" or (r.get("TRANS_ACQUIRED_DISP_CD") or "").strip() != "A":
                    continue
                acc = r["ACCESSION_NUMBER"]; s = subs.get(acc) or {}
                tk = (s.get("ISSUERTRADINGSYMBOL") or "").strip().upper()
                if tk not in universe:
                    continue
                try:
                    shares = float(r.get("TRANS_SHARES") or 0); price = float(r.get("TRANS_PRICEPERSHARE") or 0)
                except ValueError:
                    continue
                own = (owners.get(acc) or [{}])[0]
                owner = (own.get("RPTOWNERNAME") or "").strip()
                key = (acc, _fdate(r.get("TRANS_DATE")), owner, shares)
                if key in seen or not key[1]:
                    continue
                row = {"ticker": tk, "accession": acc, "trans_date": key[1], "filing_date": _fdate(s.get("FILING_DATE")),
                       "owner": owner, "relationship": (own.get("RPTOWNER_RELATIONSHIP") or "").strip(),
                       "shares": shares, "price": price, "value": round(shares * price, 2), "quarter": q,
                       "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                fh.write(json.dumps(row) + "\n"); seen.add(key); n_new += 1; n_q += 1
            print(f"[FORM4] {q}: 신규 {n_q}행")
    print(f"[FORM4] 완료 — 신규 {n_new}행 · 원장 {len(seen)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
