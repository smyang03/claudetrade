# -*- coding: utf-8 -*-
"""T1-rev 게이트 1단계: match.csv의 CIK별 SEC companyfacts에서 매출 태그 항목만 추려 슬림 JSON으로 저장.
usage: fetch_cf.py <match.csv> <out_dir> [limit]"""
import gzip, json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd
TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "RevenuesNetOfInterestExpense",
        "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet", "TotalRevenuesAndOtherIncome"]
UA = {"User-Agent": "claudetrade research smyang03@gmail.com", "Accept-Encoding": "gzip, deflate"}
match, out = Path(sys.argv[1]), Path(sys.argv[2]); limit = int(sys.argv[3]) if len(sys.argv) > 3 else None
m = pd.read_csv(match); ciks = sorted(int(c) for c in m.loc[m.status == "matched", "cik"].dropna().astype(int).unique())
if limit: ciks = ciks[:limit]
def get(cik):
    fp = out / f"{cik}.json"
    if fp.exists(): return "skip"
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    for a in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read(); raw = gzip.decompress(raw) if r.headers.get("Content-Encoding") == "gzip" else raw
            j = json.loads(raw); gaap = j.get("facts", {}).get("us-gaap", {})
            slim = {"cik": cik, "name": j.get("entityName"), "tags": {}}
            for t in TAGS:
                u = gaap.get(t, {}).get("units", {}).get("USD")
                if u: slim["tags"][t] = [{k: e.get(k) for k in ("start", "end", "val", "filed", "form", "fy", "fp", "frame")} for e in u]
            fp.write_text(json.dumps(slim), encoding="utf-8"); time.sleep(0.25); return "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404: fp.write_text(json.dumps({"cik": cik, "tags": {}, "err": 404}), encoding="utf-8"); return "404"
            ERRS.append(e.code); time.sleep(5 + 10 * a)
        except Exception as ex:  # noqa
            ERRS.append(type(ex).__name__); time.sleep(5 + 10 * a)
    return "fail"
ERRS = []
t0 = time.time()
with ThreadPoolExecutor(2) as ex: res = list(ex.map(get, ciks))
from collections import Counter
print(f"ciks {len(ciks)} {Counter(res)} errs={Counter(ERRS)} {time.time()-t0:.0f}s", flush=True)
