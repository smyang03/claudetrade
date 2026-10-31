# -*- coding: utf-8 -*-
"""2027 Q1 사전등록 검정 — MSCI Korea 분기 리뷰 신규 편입 (docs/reports/preregistration_q1_2027_msci_draft_20261031.md, 10-31 등록 e080392).

L7 엔진(research_kr_index_inclusion_20261030.py)의 Book·size_bench·tstat를 수정 없이 가져다 쓴다. 다른 점은 창뿐이다.
- 진입 e = KST 발표일 이상 첫 거래일(휴장이면 다음 거래일) 시가, 신호일 s = e − 1(규모매칭 dv20 기준일).
- 청산 x = 리밸런싱일 이하 마지막 거래일 종가. h = x − e + 1.
- 비용 0.25%(L7 COST). 클러스터 = 리뷰(같은 리뷰 편입 종목 등가중). 주 t = lag 0(회차 창이 겹치지 않음), NW lag 1 병기.
- 합격: 클러스터 t ≥ 2.64 AND IS(2014~2019)·OOS(2020~) 평균 둘 다 양(+) AND 양 클러스터 과반.
- 검정 불가: 진입·청산 가격 있는 편입 < 90% 또는 가격 있는 클러스터 < 30.
가격: L7 캐시 px/ + q1_kr_passive_2027/px/(네이버 일봉, 분석 전용). 출력 data/analysis/q1_kr_passive_2027/msci_test_result.json
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
import research_kr_index_inclusion_20261030 as L7  # noqa: E402

D = ROOT / "data" / "analysis" / "q1_kr_passive_2027"
EXTRA_PX = D / "px"
L7_REF = ROOT / "data" / "reference" / "kr_index_changes.csv"


def load_book() -> "L7.Book":
    bars = {}
    for folder in (L7.PX, EXTRA_PX):
        for fp in folder.glob("*.json"):
            b = json.loads(fp.read_text(encoding="utf-8"))
            if len(b) > 30 and fp.stem not in bars:
                bars[fp.stem] = [tuple(x) for x in b]
    cal = [r[0] for r in bars["069500"]]
    return L7.Book(bars, cal)


def windows(cal: list[str], announce: str, rebalance: str) -> tuple[int, int, int]:
    e = bisect.bisect_left(cal, announce)
    x = bisect.bisect_right(cal, rebalance) - 1
    return e - 1, e, x


def l7_overlap(rows: list[dict]) -> set[tuple[str, str]]:
    """L7 편입 종목과 30일 안 같은 종목(보조 ④ 제외용) — (review, ticker)."""
    from datetime import date
    l7 = [(r["ticker"], date.fromisoformat(r["announce_date"])) for r in csv.DictReader(L7_REF.open(encoding="utf-8"))
          if r["action"] == "add" and r["announce_date"]]
    out = set()
    for r in rows:
        a = date.fromisoformat(r["announce_date_kst"])
        if any(t == r["ticker"] and abs((d - a).days) <= 30 for t, d in l7):
            out.add((r["review"], r["ticker"]))
    return out


def summ(cl: list[dict], key: str) -> dict:
    ys = [c[key] for c in cl if c[key] == c[key]]
    m, t0, n = L7.tstat(ys, 0)
    _, t1, _ = L7.tstat(ys, 1)
    is_ = [c[key] for c in cl if c["review"] < "2020" and c[key] == c[key]]
    oos = [c[key] for c in cl if c["review"] >= "2020" and c[key] == c[key]]
    mi, ti, ni = L7.tstat(is_, 0)
    mo, to, no = L7.tstat(oos, 0)
    r2 = lambda v: round(v, 3) if v == v else None  # noqa: E731
    return {"mean": r2(m), "t": r2(t0), "t_nw1": r2(t1), "n": n, "pos": sum(1 for v in ys if v > 0),
            "median": r2(float(np.median(ys))) if ys else None,
            "IS": {"mean": r2(mi), "t": r2(ti), "n": ni}, "OOS": {"mean": r2(mo), "t": r2(to), "n": no},
            "ex_best_dropped_mean": r2(float(np.mean(sorted(ys)[:-1]))) if len(ys) > 2 else None}


def main() -> int:
    rows = list(csv.DictReader((D / "msci_korea_changes.csv").open(encoding="utf-8")))
    bk = load_book()
    cal = bk.cal
    ov = l7_overlap([r for r in rows if r["action"] == "add"])
    ev = []
    for r in rows:
        if r["action"] not in ("add", "delete"):
            continue
        tk = r["ticker"]
        s, e, x = windows(cal, r["announce_date_kst"], r["rebalance_date"])
        h = x - e + 1
        out = {"review": r["review"], "ticker": tk, "name": r["name"], "action": r["action"],
               "entry": cal[e] if 0 <= e < len(cal) else None, "exit": cal[x] if 0 <= x < len(cal) else None,
               "h": h, "status": "ok", "l7_overlap": (r["review"], tk) in ov}
        if s < 0 or e >= len(cal) or h < 1:
            out["status"] = "window"
        elif tk not in bk.col:
            out["status"] = "no_price"
        elif bk.hist[s, bk.col[tk]] == 0:
            out["status"] = "listed_after_signal"
        if out["status"] == "ok":
            j = bk.col[tk]
            raw = float(bk.fwd(j, e, h))
            dv = float(bk.dv20[s, j])
            sb, dec = bk.size_bench(s, e, h, dv)
            ib = float(bk.fwd(bk.col["069500"], e, h))
            with np.errstate(invalid="ignore", divide="ignore"):
                gap = float((bk.o[e, j] / bk.c[s, j] - 1) * 100) if bk.c[s, j] > 0 else float("nan")
            out.update(raw=raw, size_bench=sb, decile=dec, idx_bench=ib, gap_pre=gap,
                       ex_size=raw - L7.COST - sb, ex_idx=raw - L7.COST - ib)
            if raw != raw or sb != sb:
                out["status"] = "guard_or_missing_bar" if raw != raw else "no_bench"
            ee = x + 1
            if ee + 19 < len(cal):
                r20 = float(bk.fwd(j, ee, 20))
                sb20, _ = bk.size_bench(ee - 1, ee, 20, float(bk.dv20[ee - 1, j]))
                out["post20_ex"] = r20 - L7.COST - sb20
        ev.append(out)

    adds = [r for r in ev if r["action"] == "add"]
    ok = [r for r in adds if r["status"] == "ok"]

    def clusters(okrows: list[dict]) -> list[dict]:
        by = defaultdict(list)
        for r in okrows:
            by[r["review"]].append(r)
        return [{"review": k, "n": len(g), "h": g[0]["h"],
                 "ex_size": float(np.mean([r["ex_size"] for r in g])),
                 "ex_idx": float(np.mean([r["ex_idx"] for r in g if r["ex_idx"] == r["ex_idx"]] or [np.nan])),
                 "gap_pre": float(np.nanmean([r["gap_pre"] for r in g]))} for k, g in sorted(by.items())]

    cl = clusters(ok)
    res = {"adds": len(adds), "adds_ok": len(ok), "coverage_pct": round(len(ok) / len(adds) * 100, 1),
           "clusters_ok": len(cl), "status_counts": {k: sum(1 for r in adds if r["status"] == k) for k in {r["status"] for r in adds}},
           "not_ok": [f"{r['review']} {r['ticker']} {r['name']} {r['status']}" for r in adds if r["status"] != "ok"]}
    untestable = []
    if len(ok) < 0.9 * len(adds):
        untestable.append(f"가격 커버리지 {len(ok)}/{len(adds)} < 90%")
    if len(cl) < 30:
        untestable.append(f"가격 있는 클러스터 {len(cl)} < 30")
    s = summ(cl, "ex_size")
    conds = {"t>=2.64": bool(s["t"] is not None and s["t"] >= L7.T_PASS),
             "IS>0 & OOS>0": bool((s["IS"]["mean"] or 0) > 0 and (s["OOS"]["mean"] or 0) > 0),
             "pos majority": bool(s["pos"] * 2 > s["n"])}
    res.update(untestable=untestable, size=s, conditions=conds,
               verdict="검정 불가" if untestable else ("통과" if all(conds.values()) else "기각"))
    # 보조(판정 제외)
    res["aux"] = {
        "idx_069500": summ(cl, "ex_idx"),
        "gap_pre_mean_pct": round(float(np.nanmean([c["gap_pre"] for c in cl])), 3),
        "ex_l7_overlap": summ(clusters([r for r in ok if not r["l7_overlap"]]), "ex_size"),
        "post20_add": {"mean": round(float(np.mean(v)), 3) if (v := [r["post20_ex"] for r in ev if r["action"] == "add" and r.get("post20_ex") == r.get("post20_ex") and "post20_ex" in r]) else None, "n": len(v)},
        "post20_delete": {"mean": round(float(np.mean(v)), 3) if (v := [r["post20_ex"] for r in ev if r["action"] == "delete" and r.get("post20_ex") == r.get("post20_ex") and "post20_ex" in r]) else None, "n": len(v)},
        "by_year": {y: round(float(np.mean([c["ex_size"] for c in cl if c["review"][:4] == y])), 2)
                    for y in sorted({c["review"][:4] for c in cl})},
    }
    res["clusters"] = [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in c.items()} for c in cl]
    (D / "msci_test_result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "clusters"}, ensure_ascii=False, indent=1))
    for c in res["clusters"]:
        print(f"  {c['review']} n={c['n']} h={c['h']} ex_size={c['ex_size']:+.2f} gap={c['gap_pre']:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
