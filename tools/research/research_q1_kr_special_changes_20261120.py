# -*- coding: utf-8 -*-
"""사전등록 docs/reports/preregistration_q1_2027_kr_special_changes_20261119.md 판정 — KOSPI200·KOSDAQ150 수시변경 대체 편입.

L7 엔진(research_kr_index_inclusion_20261030.Book) 그대로. 가격 = L7 캐시 + data/analysis/q1_kr_special_changes/px(보강분).
진입: before_open → 발표일 시가, 그 외(after_close·intraday·unknown) → 발표일 다음 거래일 시가(발표일이 휴장이면 다음 거래일이 '발표일 이후 첫 거래일').
청산: 효력일 전 거래일 종가. 진입일 ≥ 청산일이면 제외(등록 문구 그대로). 비용 0.25%. 규모매칭 초과, 발표일 클러스터 t.
"""
from __future__ import annotations

import bisect
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import research_kr_index_inclusion_20261030 as R  # noqa: E402

SRC = ROOT / "data" / "analysis" / "q1_kr_special_changes" / "kr_special_changes.csv"
EXTRA = ROOT / "data" / "analysis" / "q1_kr_special_changes" / "px"
OUT = ROOT / "data" / "analysis" / "q1_kr_special_changes" / "test_result.json"
ETF = {"KOSPI200": "069500", "KOSDAQ150": "229200"}


def load_book():
    bars = {}
    for d in (R.PX, EXTRA):
        for fp in d.glob("*.json"):
            b = json.loads(fp.read_text(encoding="utf-8"))
            if len(b) > 30:
                bars[fp.stem] = [tuple(x) for x in b]
    cal = [r[0] for r in bars["069500"]]
    return R.Book(bars, cal)


def main() -> int:
    rows = [r for r in csv.DictReader(SRC.open(encoding="utf-8-sig")) if (r.get("status") or "") != "lead"]
    bk = load_book(); cal = bk.cal
    recs, status = [], defaultdict(int)
    for r in rows:
        tk, ad, ed = r["ticker"].strip().zfill(6), r["announce_date"].strip(), r["effective_date"].strip()
        if not ad or not ed:
            status["no_dates"] += 1; continue
        if tk not in bk.col:
            status["no_price"] += 1; continue
        if (r.get("announce_timing") or "").strip() == "before_open":
            e = bisect.bisect_left(cal, ad)                  # 발표일(또는 그 이후 첫 거래일) 시가
            s = e - 1
        else:
            s = bisect.bisect_right(cal, ad) - 1             # 발표일 이하 마지막 거래일
            e = s + 1
        x = bisect.bisect_left(cal, ed) - 1                  # 효력일 전 거래일
        if s < 0 or e >= len(cal) or x < 0:
            status["window"] += 1; continue
        if e >= x:
            status["hold_le_0"] += 1; continue
        j = bk.col[tk]
        h = x - e + 1
        raw = float(bk.fwd(j, e, h))
        if raw != raw or bk.hist[s, j] == 0:
            status["guard_or_missing_bar"] += 1; continue
        sb, _ = bk.size_bench(s, e, h, float(bk.dv20[s, j]))
        ib = float(bk.fwd(bk.col[ETF[r["index"]]], e, h)) if ETF[r["index"]] in bk.col else float("nan")
        gap = float((bk.o[e, j] / bk.c[s, j] - 1) * 100) if bk.c[s, j] > 0 else float("nan")
        status["ok"] += 1
        recs.append({"announce": ad, "index": r["index"], "ticker": tk, "name": r["name"], "reason": r.get("reason"),
                     "h": h, "ex_size": raw - R.COST - sb, "ex_idx": raw - R.COST - ib, "gap_pre": gap})
    by = defaultdict(list)
    for x in recs:
        by[x["announce"]].append(x)
    cl = [{"announce": a, "n": len(g), "ex_size": float(np.nanmean([v["ex_size"] for v in g])),
           "ex_idx": float(np.nanmean([v["ex_idx"] for v in g])), "gap_pre": float(np.nanmean([v["gap_pre"] for v in g]))}
          for a, g in sorted(by.items())]
    ys = [c["ex_size"] for c in cl if c["ex_size"] == c["ex_size"]]
    m, t, n = R.tstat(ys, 0)
    mi, ti, ni = R.tstat([c["ex_size"] for c in cl if c["announce"] < "2020-01-01"], 0)
    mo, to, no = R.tstat([c["ex_size"] for c in cl if c["announce"] >= "2020-01-01"], 0)
    pos = sum(v > 0 for v in ys)
    cover = status["ok"] / max(len(rows), 1)
    if n < 25 or cover < 0.80:
        verdict = "검정 불가"
    elif t >= 2.64 and mi > 0 and mo > 0 and pos > n / 2:
        verdict = "합격"
    else:
        verdict = "기각"
    aux = {"idx_mean": float(np.nanmean([c["ex_idx"] for c in cl])) if cl else None,
           "gap_pre_mean": float(np.nanmean([c["gap_pre"] for c in cl])) if cl else None,
           "by_index": {k: float(np.mean([x["ex_size"] for x in recs if x["index"] == k])) for k in ETF if any(x["index"] == k for x in recs)},
           "hold_median": float(np.median([x["h"] for x in recs])) if recs else None}
    res = {"rows": len(rows), "status": dict(status), "coverage": round(cover, 3), "clusters": n,
           "mean": m, "t": t, "IS": [mi, ti, ni], "OOS": [mo, to, no], "pos": pos, "verdict": verdict, "aux": aux}
    OUT.write_text(json.dumps({**res, "cluster_rows": cl}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
