#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""US→KR 오버나이트 정보 이전 연구 (read-only, 2026-09-06).

질문: 미국 섹터(반도체·바이오·SW…)의 전날 밤 움직임이 다음날 한국 동종 업종의 **시가 갭**과 **시가 이후 드리프트(시가→종가)**에
어떻게 반영되는가. 갭은 정보가 시가에 반영된 몫(우리가 못 잡는 몫), 드리프트가 시가 매수로 잡을 수 있는 몫이다.
- 데이터: data/price/us(695 종목 sector_map) → US 섹터 등가중 composite 수익률(당일 종가/전일 종가, SPY 대비 초과),
          data/price/kr(627 종목 sector_map) → 다음 KR 거래일 섹터 등가중 갭·드리프트(전체 KR 등가중 대비 초과).
- 기간: KR 캐시 시작 2025-04-22 이후(약 16개월). lookahead 없음: US 세션 D(KST D+1 05:00 마감) → KR 세션 D+1.
- 출력: data/analysis/us_kr_overnight_study.json (버킷 표·상관·최근 신호) + docs/reports/us_kr_overnight_study_YYYYMMDD.md
사용: python tools/us_kr_overnight_study.py [--min-names 5]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics as st
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTOR_MAP = ROOT / "data" / "sector_map.json"
US_DIR = ROOT / "data" / "price" / "us"
KR_DIR = ROOT / "data" / "price" / "kr"
OUT_JSON = ROOT / "data" / "analysis" / "us_kr_overnight_study.json"

# KR KSIC 대분류(sector_map.KR.sector) ← US yfinance industry 묶음. 대응이 약한 섹터는 넣지 않는다.
PAIRS: dict[str, tuple[str, ...]] = {
    "전자·반도체": ("Semiconductors", "Semiconductor Equipment & Materials", "Computer Hardware", "Electronic Components"),
    "제약·바이오": ("Biotechnology", "Drug Manufacturers - General", "Drug Manufacturers - Specialty & Generic", "Diagnostics & Research"),
    "정보통신·SW": ("Software - Application", "Software - Infrastructure", "Information Technology Services", "Internet Content & Information"),
    "자동차·운송장비": ("Auto Parts", "Auto Manufacturers"),
    "금융·보험": ("Banks - Regional", "Banks - Diversified", "Capital Markets", "Insurance - Diversified", "Asset Management"),
    "석유·화학": ("Oil & Gas E&P", "Oil & Gas Integrated", "Specialty Chemicals", "Chemicals"),
    "금속": ("Steel", "Gold", "Aluminum", "Copper", "Other Industrial Metals & Mining"),
    "건설": ("Engineering & Construction",),
    "기계": ("Specialty Industrial Machinery", "Aerospace & Defense", "Farm & Heavy Construction Machinery"),
    "전기장비": ("Electrical Equipment & Parts", "Solar"),
}
BUCKETS = [(-99, -2.0, "≤−2%"), (-2.0, -1.0, "−2~−1%"), (-1.0, -0.3, "−1~−0.3%"), (-0.3, 0.3, "±0.3%"),
           (0.3, 1.0, "+0.3~1%"), (1.0, 2.0, "+1~2%"), (2.0, 99, "≥+2%")]


def load_bars(path: Path) -> list[tuple[str, float, float, float, float]]:
    rows = []
    try:
        with path.open(encoding="utf-8-sig") as fh:
            for r in csv.reader(fh):
                if r and r[0][:2] == "20" and len(r) >= 6:
                    try:
                        rows.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])))
                    except ValueError:
                        pass
    except OSError:
        return []
    return sorted(rows)


def daily_returns(bars) -> dict[str, dict[str, float]]:
    """date → {ret: 종가/전일종가−1, gap: 시가/전일종가−1, drift: 종가/시가−1} (%)."""
    out = {}
    for i in range(1, len(bars)):
        d, o, h, l, c = bars[i]
        pc = bars[i - 1][4]
        if pc > 0 and o > 0:
            out[d] = {"ret": (c / pc - 1) * 100, "gap": (o / pc - 1) * 100, "drift": (c / o - 1) * 100}
    return out


def composite(tickers: list[str], base: Path, prefix: str, key: str, min_names: int) -> dict[str, float]:
    """날짜별 등가중 평균(해당일 종목 수 ≥ min_names)."""
    acc: dict[str, list[float]] = defaultdict(list)
    for t in tickers:
        for d, v in daily_returns(load_bars(base / f"{prefix}{t}.csv")).items():
            acc[d].append(v[key])
    return {d: st.mean(v) for d, v in acc.items() if len(v) >= min_names}


def next_kr_date(us_date: str, kr_dates: list[str]) -> str | None:
    for d in kr_dates:
        if d > us_date:
            return d
    return None


def tstat(xs: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    sd = st.pstdev(xs)
    return round(st.mean(xs) / (sd / math.sqrt(len(xs))), 2) if sd > 0 else None


def corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 5:
        return None
    mx, my = st.mean(xs), st.mean(ys)
    sx, sy = st.pstdev(xs), st.pstdev(ys)
    if sx == 0 or sy == 0:
        return None
    return round(sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (len(xs) * sx * sy), 3)


def run(min_names: int = 5) -> dict:
    sm = json.load(open(SECTOR_MAP, encoding="utf-8"))
    us_by_ind: dict[str, list[str]] = defaultdict(list)
    for t, v in sm["US"].items():
        us_by_ind[str(v.get("industry"))].append(t)
    kr_by_sec: dict[str, list[str]] = defaultdict(list)
    for t, v in sm["KR"].items():
        kr_by_sec[str(v.get("sector"))].append(t)
    spy = daily_returns(load_bars(US_DIR / "us_SPY.csv"))
    # KR 전체 등가중(시장 조정용)
    all_kr = [t for ts in kr_by_sec.values() for t in ts]
    kr_mkt_gap = composite(all_kr, KR_DIR, "kr_", "gap", 30)
    kr_mkt_drift = composite(all_kr, KR_DIR, "kr_", "drift", 30)
    kr_dates = sorted(kr_mkt_drift)
    result = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "min_names": min_names,
              "kr_period": [kr_dates[0], kr_dates[-1]] if kr_dates else None, "sectors": {}, "latest_signal": {}}
    pooled_x, pooled_gap, pooled_drift = [], [], []
    for kr_sec, inds in PAIRS.items():
        us_t = [t for i in inds for t in us_by_ind.get(i, [])]
        kr_t = kr_by_sec.get(kr_sec, [])
        if len(us_t) < min_names or len(kr_t) < min_names:
            result["sectors"][kr_sec] = {"skipped": f"us={len(us_t)} kr={len(kr_t)}"}
            continue
        us_ret = composite(us_t, US_DIR, "us_", "ret", min_names)
        kr_gap = composite(kr_t, KR_DIR, "kr_", "gap", min_names)
        kr_drift = composite(kr_t, KR_DIR, "kr_", "drift", min_names)
        obs = []
        for ud, ur in sorted(us_ret.items()):
            if ud not in spy:
                continue
            kd = next_kr_date(ud, kr_dates)
            if not kd or kd not in kr_gap or kd not in kr_drift:
                continue
            x = ur - spy[ud]["ret"]            # 섹터 초과수익(시장 요인 제거)
            obs.append({"us_date": ud, "kr_date": kd, "us_excess": x, "us_raw": ur, "spy": spy[ud]["ret"],
                        "gap_x": kr_gap[kd] - kr_mkt_gap.get(kd, 0.0), "drift_x": kr_drift[kd] - kr_mkt_drift.get(kd, 0.0),
                        "gap": kr_gap[kd], "drift": kr_drift[kd]})
        if len(obs) < 40:
            result["sectors"][kr_sec] = {"skipped": f"n={len(obs)}"}
            continue
        xs = [o["us_excess"] for o in obs]
        buckets = []
        for lo, hi, label in BUCKETS:
            sel = [o for o in obs if lo <= o["us_excess"] < hi]
            if not sel:
                continue
            g = [o["gap_x"] for o in sel]; dr = [o["drift_x"] for o in sel]
            buckets.append({"bucket": label, "n": len(sel), "gap_x_mean": round(st.mean(g), 3), "drift_x_mean": round(st.mean(dr), 3),
                            "drift_x_median": round(st.median(dr), 3), "drift_t": tstat(dr),
                            "drift_pos_pct": round(sum(1 for v in dr if v > 0) / len(dr) * 100, 1)})
        # 강한 신호(|초과| ≥ 1%) 방향별 드리프트 — 우리가 잡을 수 있는 몫
        up = [o["drift_x"] for o in obs if o["us_excess"] >= 1.0]
        dn = [o["drift_x"] for o in obs if o["us_excess"] <= -1.0]
        result["sectors"][kr_sec] = {
            "us_names": len(us_t), "kr_names": len(kr_t), "n_days": len(obs),
            "corr_us_gap": corr(xs, [o["gap_x"] for o in obs]), "corr_us_drift": corr(xs, [o["drift_x"] for o in obs]),
            "corr_gap_drift": corr([o["gap_x"] for o in obs], [o["drift_x"] for o in obs]),
            "strong_up": {"n": len(up), "drift_x_mean": round(st.mean(up), 3) if up else None, "t": tstat(up)},
            "strong_down": {"n": len(dn), "drift_x_mean": round(st.mean(dn), 3) if dn else None, "t": tstat(dn)},
            "buckets": buckets,
        }
        pooled_x += xs; pooled_gap += [o["gap_x"] for o in obs]; pooled_drift += [o["drift_x"] for o in obs]
        last = obs[-1]
        latest_us = max(us_ret)
        result["latest_signal"][kr_sec] = {"us_date": latest_us, "us_excess": round(us_ret[latest_us] - spy.get(latest_us, {}).get("ret", 0.0), 3),
                                          "us_raw": round(us_ret[latest_us], 3), "last_pair": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in last.items()}}
    result["pooled"] = {"n": len(pooled_x), "corr_us_gap": corr(pooled_x, pooled_gap), "corr_us_drift": corr(pooled_x, pooled_drift),
                        "corr_gap_drift": corr(pooled_gap, pooled_drift)}
    return result


def to_md(res: dict) -> str:
    L = [f"# US→KR 오버나이트 정보 이전 연구 ({res['generated_at'][:10]})", "",
         f"KR 기간 {res.get('kr_period')} · 섹터 composite 등가중(종목 ≥ {res['min_names']}) · US 초과 = 섹터 − SPY · KR 갭/드리프트 초과 = 섹터 − KR 전체 등가중",
         "", "판정 기준: **갭 상관이 높고 드리프트 상관이 0이면 시가에 다 반영(엣지 없음)**. 드리프트가 US 방향으로 유의하면 시가 매수 몫이 있다. 반대 부호면 시가 과잉반응(역방향 후보).", "",
         f"전체 풀: n={res['pooled']['n']} · corr(US, 갭) {res['pooled']['corr_us_gap']} · corr(US, 드리프트) {res['pooled']['corr_us_drift']} · corr(갭, 드리프트) {res['pooled']['corr_gap_drift']}", "",
         "| KR 섹터 | US/KR 종목 | n일 | corr 갭 | corr 드리프트 | corr 갭→드리프트 | US ≥+1% 뒤 드리프트(n, t) | US ≤−1% 뒤 드리프트(n, t) |", "|---|---|---:|---:|---:|---:|---|---|"]
    for sec, r in res["sectors"].items():
        if "skipped" in r:
            L.append(f"| {sec} | 제외({r['skipped']}) | | | | | | |"); continue
        su, sd = r["strong_up"], r["strong_down"]
        L.append(f"| {sec} | {r['us_names']}/{r['kr_names']} | {r['n_days']} | {r['corr_us_gap']} | {r['corr_us_drift']} | {r['corr_gap_drift']} | "
                 f"{su['drift_x_mean']}% (n={su['n']}, t={su['t']}) | {sd['drift_x_mean']}% (n={sd['n']}, t={sd['t']}) |")
    L += ["", "## 버킷별 (US 섹터 초과수익 → 다음날 KR 섹터 초과 갭 / 초과 드리프트)", ""]
    for sec, r in res["sectors"].items():
        if "skipped" in r:
            continue
        L += [f"### {sec}", "| US 버킷 | n | 갭 평균 | 드리프트 평균 | 드리프트 중앙 | t | 양수% |", "|---|---:|---:|---:|---:|---:|---:|"]
        for b in r["buckets"]:
            L.append(f"| {b['bucket']} | {b['n']} | {b['gap_x_mean']:+.2f} | {b['drift_x_mean']:+.2f} | {b['drift_x_median']:+.2f} | {b['drift_t']} | {b['drift_pos_pct']} |")
        L.append("")
    L += ["## 최근 신호 (마지막 US 세션)", "", "| KR 섹터 | US 날짜 | US 초과 | US 원수익 |", "|---|---|---:|---:|"]
    for sec, s in res["latest_signal"].items():
        L.append(f"| {sec} | {s['us_date']} | {s['us_excess']:+.2f}% | {s['us_raw']:+.2f}% |")
    L += ["", "주의: 16개월·섹터 10개·등가중 composite. 종목 클러스터가 아니라 날짜 클러스터라 t는 날짜 독립 가정. 비용 미반영(KR 왕복 0.25%)."]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-names", type=int, default=5)
    a = ap.parse_args()
    res = run(a.min_names)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    md = to_md(res)
    rep = ROOT / "docs" / "reports" / f"us_kr_overnight_study_{res['generated_at'][:10].replace('-', '')}.md"
    rep.write_text(md, encoding="utf-8")
    print(md)
    print(f"saved {OUT_JSON} / {rep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
