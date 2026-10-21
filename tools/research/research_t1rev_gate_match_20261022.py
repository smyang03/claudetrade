# -*- coding: utf-8 -*-
"""T1-rev 게이트 2단계: 보도자료(EX-99.1 텍스트) 매출 <-> companyfacts 10-Q/10-K 매출 일치율.
usage: gate_match.py <match.csv> <sample.csv.gz> <docs_dir> <cf_dir> <out_dir>
규칙: 분기 사실은 start/end로만 식별(fp 무시), 이벤트일 D 기준 end in [D-100, D-10], filed >= D-3 중 최소 filed(원 공시).
      Q4는 FY(350~380일) - 같은 start의 9M YTD(260~285일). 숫자 매칭은 매출 키워드 뒤 220자 안의 숫자만, 문서 단위 반올림 ±1 끝자리.
      대조: 같은 달력 분기의 다른 회사 값으로 같은 매칭 -> 허위 일치 바닥."""
import json, re, sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

match, sample, DOCS, CF, OUT = [Path(a) for a in sys.argv[1:6]]
OUT.mkdir(parents=True, exist_ok=True)
m = pd.read_csv(match)
m = m[m.status == "matched"].copy()
s = pd.read_csv(sample)[["ticker", "date", "is_i1"]]
m = m.merge(s, on=["ticker", "date"], how="left")
m["D"] = pd.to_datetime(m.date).dt.date
KW = re.compile(r"(total\s+)?(net\s+)?(revenues?|net\s+sales|sales)\b", re.I)
NUM = re.compile(r"\(?\$?\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\)?\s*(billion|million|thousand)?", re.I)
UNIT = re.compile(r"in\s+(thousands|millions|billions)", re.I)
SCALE = {"thousand": 1e3, "thousands": 1e3, "million": 1e6, "millions": 1e6, "billion": 1e9, "billions": 1e9}
_cf = {}


def cf(cik):
    if cik not in _cf:
        fp = CF / f"{cik}.json"
        _cf[cik] = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else None
    return _cf[cik]


def dur(e):
    return (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days if e.get("start") and e.get("end") else None


def cf_value(cik, D):
    """-> (values:set, kind, tag)  kind in q / q4 / q4_noytd / none / nocf"""
    j = cf(cik)
    if not j:
        return set(), "nocf", ""
    ents = [(t, e) for t, es in j["tags"].items() for e in es
            if e.get("form") in ("10-Q", "10-K", "10-Q/A", "10-K/A") and e.get("val") is not None and e.get("filed")]
    lo, hi, fmin = D - timedelta(100), D - timedelta(10), D - timedelta(3)
    q = [(t, e) for t, e in ents if 80 <= (dur(e) or 0) <= 100 and lo <= date.fromisoformat(e["end"]) <= hi
         and date.fromisoformat(e["filed"]) >= fmin]
    if q:
        f0 = min(date.fromisoformat(e["filed"]) for _, e in q)
        best = [(t, e) for t, e in q if date.fromisoformat(e["filed"]) == f0]
        return {float(e["val"]) for _, e in best}, "q", "|".join(sorted({t for t, _ in best}))
    fy = [(t, e) for t, e in ents if 350 <= (dur(e) or 0) <= 380 and lo <= date.fromisoformat(e["end"]) <= hi
          and date.fromisoformat(e["filed"]) >= fmin]
    if fy:
        f0 = min(date.fromisoformat(e["filed"]) for _, e in fy)
        vals, tags = set(), set()
        for t, e in fy:
            if date.fromisoformat(e["filed"]) != f0:
                continue
            ytd = [x for tt, x in ents if tt == t and x.get("start") == e["start"] and 260 <= (dur(x) or 0) <= 285]
            if ytd:
                y0 = min(ytd, key=lambda x: x["filed"])          # 9M YTD도 원 공시(최소 filed) — 최신 값은 재공시 비교치일 수 있다
                vals.add(float(e["val"]) - float(y0["val"]))
                tags.add(t)
        if vals:
            return vals, "q4", "|".join(sorted(tags))
        return set(), "q4_noytd", ""
    return set(), "none", ""


def doc_numbers(text):
    """매출 키워드 뒤 220자 안의 (숫자, 소수자릿수, 단위어 scale|None) 목록 + 문서 단위 후보"""
    units = {SCALE[u.lower()] for u in UNIT.findall(text)} or {1e3, 1e6, 1e9}
    out = []
    for km in KW.finditer(text):
        seg = text[km.end(): km.end() + 220]
        for nm in NUM.finditer(seg):
            raw = nm.group(1).replace(",", "")
            d = len(raw.split(".")[1]) if "." in raw else 0
            try:
                n = float(raw)
            except ValueError:
                continue
            if n < 1:
                continue
            out.append((n, d, SCALE[nm.group(2).lower()] if nm.group(2) else None))
    return out, units


def hit(nums, units, vals):
    for V in vals:
        if V <= 0:
            continue
        for n, d, w in nums:
            for sc in ([w] if w else units):
                x = V / sc
                tol = ((0.6 if w else 1.5) * 10 ** (-d)) if d else (0.6 if w else 1.0)
                if abs(n - x) <= tol and (n >= 10 or d >= 1):
                    return True
    return False


rows = []
for r in m.itertuples():
    fp = DOCS / f"{r.acc}.txt"
    rec = dict(ticker=r.ticker, date=r.date, cik=int(r.cik), acc=r.acc, is_i1=bool(r.is_i1), year=r.D.year,
               cq=f"{r.D.year}Q{(r.D.month - 1) // 3 + 1}")
    if not fp.exists():
        rec.update(cat="no_doc"); rows.append(rec); continue
    vals, kind, tag = cf_value(int(r.cik), r.D)
    rec.update(kind=kind, tag=tag, cf_val=max(vals) if vals else None)
    if not vals:
        rec.update(cat="cf_missing" if kind == "nocf" else "cf_no_value"); rows.append(rec); continue
    if max(vals) <= 0:
        rec.update(cat="cf_zero"); rows.append(rec); continue                 # 무매출(바이오 등) — 매출 SUE 정의 불가, 분모 제외
    nums, units = doc_numbers(fp.read_text(encoding="utf-8", errors="ignore"))
    if not nums:
        rec.update(cat="extract_fail"); rows.append(rec); continue
    rec["_nums"] = (nums, units)
    rec.update(cat="matched" if hit(nums, units, vals) else "mismatch")
    rows.append(rec)

# 대조: 같은 달력 분기 안에서 다른 회사의 cf 값으로 같은 매칭 -> 허위 일치율
byq = defaultdict(list)
for i, rec in enumerate(rows):
    if rec.get("cf_val") and "_nums" in rec:
        byq[rec["cq"]].append(i)
sp = []
for cq, idx in byq.items():
    if len(idx) < 2:
        continue
    idx = sorted(idx, key=lambda i: rows[i]["cik"])
    for a, b in zip(idx, idx[1:] + idx[:1]):
        if rows[a]["cik"] == rows[b]["cik"]:
            continue
        rows[a]["spurious"] = hit(*rows[a]["_nums"], {rows[b]["cf_val"]})
        sp.append(rows[a]["spurious"])
for rec in rows:
    rec.pop("_nums", None)
d = pd.DataFrame(rows)
d.to_csv(OUT / "gate_events.csv", index=False)


def rate(x):
    have = x[x.cat.isin(["matched", "mismatch"])]
    strict = x[x.cat.isin(["matched", "mismatch", "extract_fail"])]            # extract_fail을 불일치로 치는 보수 분모
    return dict(n=int(len(x)), n_have=int(len(have)),
                cov=round(len(x[x.cat.isin(["matched", "mismatch", "extract_fail", "cf_zero"])]) / max(len(x), 1), 4),
                match=round(float((have.cat == "matched").mean()), 4) if len(have) else None,
                match_strict=round(float((strict.cat == "matched").mean()), 4) if len(strict) else None,
                cats={k: int(v) for k, v in x.cat.value_counts().items()})


res = dict(all=rate(d), i1=rate(d[d.is_i1 == True]), by_year={int(y): rate(g) for y, g in d.groupby("year")},
           by_kind={k: rate(g) for k, g in d[d.cat.isin(["matched", "mismatch"])].groupby("kind")},
           spurious=round(sum(sp) / len(sp), 4) if sp else None, n_spurious=len(sp),
           tags={k: int(v) for k, v in d.tag.value_counts().head(8).items()} if "tag" in d else {})
(OUT / "gate_results.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: res[k] for k in ("all", "i1", "spurious", "n_spurious", "by_kind")}, ensure_ascii=False, indent=1))
