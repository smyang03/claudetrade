#!/usr/bin/env python3
"""단타 탐색을 기한 안에 판정할 수 있나 — 검정력 시뮬레이션 (read-only, 2026-09-24).

운영자 질문: "2번(단타 탐색 계속, 기한·기준)은 어떻게 시뮬레이션해봐."

방법: 우리 **실제 실거래**(351건, `data/analysis/live_history_20260924/trips_enriched.json`)에서
날짜 단위 손익 분포(변동성·하루 거래 수)를 그대로 가져온다. 그 분포의 평균만 "참 우위" 값으로 옮겨 놓고
forward 기간(1·3·6·12개월)을 부트스트랩으로 수천 번 재생해 **판정 문턱을 넘을 확률**을 잰다.
- 참 우위 = 비용 후 거래당 순수익(%). 0 = 비용만큼만 버는 전략(= 지금까지의 우리).
- 판정 문턱: 날짜 클러스터 t ≥ 2(보정 없음) / t ≥ 3(탐색 분모 96~301 반영).
- 거짓 합격률: 참 우위 0인데 문턱을 넘는 비율(= 운으로 통과).
거래일 = 월 21일, 레인 가동일 비율은 실제 기록 그대로(가동일만 표본).
"""
from __future__ import annotations

import json
import math
import random
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRIPS = ROOT / "data" / "analysis" / "live_history_20260924" / "trips_enriched.json"
OUT = ROOT / "docs" / "reports" / "shortterm_verdict_power_20260924.md"
FEE = {"KR": 0.21, "US": 0.44}
SIMS = 4000


def main():
    trips = json.loads(TRIPS.read_text(encoding="utf-8"))
    L = ["# 단타 탐색 — 기한 안에 판정이 나는가 (검정력 시뮬레이션, 2026-09-24)", "",
         "read-only. `tools/research/research_shortterm_verdict_power_20260924.py`(방법은 docstring).",
         "실거래 351건의 **날짜 단위 손익 분포**를 그대로 쓰고, 평균만 '참 우위'로 옮겨 forward를 재생했다.", ""]
    for mk in ("US", "KR"):
        by_day = defaultdict(list)
        span = set()
        for t in trips:
            if t["market"] != mk or t.get("pnl_pct") is None or not t.get("entry_date"):
                continue
            by_day[t["entry_date"]].append(float(t["pnl_pct"]) - FEE[mk])
            span.add(t["entry_date"][:7])
        days = [st.mean(v) for v in by_day.values()]
        mu, sd = st.mean(days), st.stdev(days)
        months = len(span)
        active_per_month = len(days) / months
        resid = [x - mu for x in days]
        L += [f"## {mk} — 실거래 가동일 {len(days)}일 · 월 평균 {active_per_month:.1f}일 · "
              f"실측 일평균 순손익 {mu:+.2f}% · 날짜 변동성 {sd:.2f}%", "",
              "| 참 우위(일평균 순) | 1개월 | 3개월 | 6개월 | 12개월 |", "|---|---:|---:|---:|---:|"]
        for edge in (0.0, 0.25, 0.5, 1.0):
            cells = []
            for m in (1, 3, 6, 12):
                n = max(3, round(active_per_month * m))
                p2 = p3 = 0
                rng = random.Random(int(edge * 1000) * 131 + m * 17 + (0 if mk == "US" else 7))
                for _ in range(SIMS):
                    s = [edge + rng.choice(resid) for _ in range(n)]
                    sdv = st.stdev(s)
                    if sdv == 0:
                        continue
                    t = st.mean(s) / (sdv / math.sqrt(n))
                    p2 += t >= 2
                    p3 += t >= 3
                cells.append(f"{p2 / SIMS:.0%} / {p3 / SIMS:.0%}")
            lab = "0 (운으로 통과 = 거짓 합격)" if edge == 0 else f"+{edge:.2f}%"
            L.append(f"| {lab} | " + " | ".join(cells) + " |")
        need2 = (2 * sd / (0.5)) ** 2
        L += ["", f"칸 = P(t≥2) / P(t≥3). 참 우위 +0.5%를 t≥2로 **평균적으로** 잡는 데 필요한 가동일 ≈ {need2:.0f}일 "
              f"(≈ {need2 / active_per_month:.0f}개월).", ""]
    L += ["## 읽는 법", "",
          "- 날짜 변동성이 크면 짧은 기한으로는 진짜 우위도 못 잡고, 우위 0인 전략도 가끔 통과한다.",
          "- 판정 기한을 정하려면 **참 우위가 어느 크기여야 그 기한에 잡히는지**를 먼저 봐야 한다.",
          "- 우리 실측 일평균 순손익(표 제목)은 음수다 — 지금까지의 단타는 이 표의 '0'보다 아래였다."]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
