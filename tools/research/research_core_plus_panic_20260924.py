#!/usr/bin/env python3
"""지수 보유 본체 + 패닉 밤 오버레이 — 조합 포트폴리오 (read-only, 2026-09-24).

질문: "장이 움직일 때 번다"(지수 보유)에 유일한 사전등록 통과 축(패닉 ETF 밤)을 얹으면
      지수만 들고 있는 것보다 나은가? 새 가설 탐색이 아니라 **이미 있는 두 조각의 조합 검증**이다.

구성(고정, 실행 전 결정):
- 본체: 069500(KODEX 200) 보유, 또는 069500 + SPY(원화 환산) 50/50 무리밸런스. 수정주가(yfinance, voltarget_20260924 폴더).
- 오버레이 슬리브(자본의 s): 평소 현금(이자 0, 보수). 패닉 신호일 종가에 233740 매수 → 익일 시가 매도.
  원장 `data/shadow/kr_index_etf_panic.jsonl`의 net_pct(비용 반영) 그대로. 강신호(units 2) = 슬리브 100%, 보통(1) = 50%.
  → 현행 사전등록 계약(강신호 2단위)과 같은 비례.
- 비교: A 본체 100% · B 본체 (1−s) + 현금 s · C 본체 (1−s) + 오버레이 s. s ∈ {0.2, 0.3}.
판정 기준(사전): C가 A보다 샤프 높고 최대낙폭 얕으면 "조합이 값을 더한다". 수익률 자체는 A가 높을 수 있다(현금 비중).
⚠️ 오버레이 net은 백필(인샘플, t 3.37)이고 분모 보정에서 기각된 값이다 — 이 표는 상한이다.
"""
from __future__ import annotations

import csv
import json
import math
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "analysis" / "voltarget_20260924"
LEDGER = ROOT / "data" / "shadow" / "kr_index_etf_panic.jsonl"
OUT = ROOT / "docs" / "reports" / "core_plus_panic_20260924.md"


def load(name):
    out = {}
    with (D / f"{name}.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                c = float(r["Close"])
            except (TypeError, ValueError):
                continue
            if c > 0:
                out[r[next(iter(r))][:10]] = c
    return out


def stats(rets):
    nav = [1.0]
    for r in rets:
        nav.append(nav[-1] * (1 + r))
    yrs = len(rets) / 252
    cagr = nav[-1] ** (1 / yrs) - 1
    vol = st.pstdev(rets) * math.sqrt(252)
    peak, mdd = 1.0, 0.0
    for v in nav:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return cagr * 100, vol * 100, (st.mean(rets) * 252 / vol if vol else 0), mdd * 100


def main():
    k200, spy, fx = load("069500.KS"), load("SPY"), load("KRW_X")
    ov = defaultdict(float)   # exit_date -> 슬리브 수익(소수)
    n_sig = 0
    for line in LEDGER.open(encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") != "trade" or r.get("ticker") != "233740" or r.get("net_pct") is None:
            continue
        w = 1.0 if int(r.get("units") or 1) >= 2 else 0.5
        ov[r["exit_date"]] += w * float(r["net_pct"]) / 100
        n_sig += 1
    start = "2016-01-04"
    dates = [d for d in sorted(k200) if d >= start]
    # US 본체: SPY 원화 환산, KR 거래일 기준 마지막 가용값
    spy_d, fx_d = sorted(spy), sorted(fx)

    def last(series, keys, d):
        import bisect
        i = bisect.bisect_right(keys, d) - 1
        return series[keys[i]] if i >= 0 else None

    base = {"KR": [], "KR+US": []}
    ovr = []
    for i in range(1, len(dates)):
        d0, d1 = dates[i - 1], dates[i]
        rk = k200[d1] / k200[d0] - 1
        s0, s1 = last(spy, spy_d, d0) * last(fx, fx_d, d0), last(spy, spy_d, d1) * last(fx, fx_d, d1)
        ru = s1 / s0 - 1
        base["KR"].append(rk)
        base["KR+US"].append(None)      # 무리밸런스 50/50은 nav로 따로 계산
        ovr.append(ov.get(d1, 0.0))
    # 50/50 무리밸런스 → 일간 수익으로 변환
    vk, vu, mix = 0.5, 0.5, []
    for i in range(1, len(dates)):
        d0, d1 = dates[i - 1], dates[i]
        s0, s1 = last(spy, spy_d, d0) * last(fx, fx_d, d0), last(spy, spy_d, d1) * last(fx, fx_d, d1)
        before = vk + vu
        vk *= k200[d1] / k200[d0]
        vu *= s1 / s0
        mix.append((vk + vu) / before - 1)
    base["KR+US"] = mix

    L = ["# 지수 보유 + 패닉 밤 오버레이 — 조합 포트폴리오 (2016-01 ~ 2026-09)", "",
         "read-only. `tools/research/research_core_plus_panic_20260924.py`(구성·판정 기준은 docstring에 실행 전 고정).",
         f"오버레이 거래 {n_sig}건(233740, 원장 net · 백필 인샘플 상한). 현금 이자 0.", ""]
    for bname, br in base.items():
        L += [f"## 본체 = {bname}", "", "| 구성 | CAGR | 변동성 | 샤프 | 최대낙폭 |", "|---|---:|---:|---:|---:|"]
        rows = [("A 본체 100%", br)]
        for s in (0.2, 0.3):
            rows.append((f"B 본체 {1-s:.0%} + 현금 {s:.0%}", [(1 - s) * x for x in br]))
            rows.append((f"**C 본체 {1-s:.0%} + 오버레이 {s:.0%}**", [(1 - s) * x + s * o for x, o in zip(br, ovr)]))
        rows.append(("참고: 오버레이 슬리브 단독", ovr))
        for lab, rr in rows:
            c, v, sh, m = stats(rr)
            L.append(f"| {lab} | {c:+.1f}% | {v:.1f}% | {sh:.2f} | {m:.1f}% |")
        # 연도별 A vs C(0.2)
        L += ["", "| 연도 | A 본체 | C 80/20 | 오버레이 단독 |", "|---|---:|---:|---:|"]
        by = defaultdict(list)
        for d, x, o in zip(dates[1:], br, ovr):
            by[d[:4]].append((x, o))
        for y, v in sorted(by.items()):
            a = math.prod(1 + x for x, _ in v) - 1
            c = math.prod(1 + 0.8 * x + 0.2 * o for x, o in v) - 1
            oo = math.prod(1 + o for _, o in v) - 1
            L.append(f"| {y} | {a*100:+.1f}% | {c*100:+.1f}% | {oo*100:+.1f}% |")
        L.append("")
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
