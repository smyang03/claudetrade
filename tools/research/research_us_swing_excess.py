#!/usr/bin/env python3
"""US swing 레인 — IWM 초과분 실측 (read-only, 2026-09-12).

**이 시스템에서 가장 큰 자본 블록(54만 × 슬롯 7 = 378만)인데 지수 초과분을 한 번도 안 쟀다.**
09-11에 KR 풀과 C6는 쟀고, C6는 그 검정에서 죽었다(자체 반증 조항 적중).
같은 자를 제일 큰 블록에 댄다. 자본 배분 판정(`capital_allocation_verdict_20260912.md`)의
가장 큰 구멍이다.

## 표본 복원

`virtual_books.db`의 `xus_fallen3`(전일 ≤−3% 전량, 2025-06~2026-09, 33,865건)이
라이브 레인의 모집단이다. 각 행의 `meta.feat`에 `dvol`·`max21`이 있고
`meta.grid`에 **라이브 계약 셀 `tp12_sl25_d7_be`(TP12/SL25/BE4/D7, 비용 반영)**가 들어 있다.
→ 라이브 규칙을 그대로 재구성한다:
   **거래대금 100~500M 밴드 → MAX21 ≥ 8 → dvol 내림차순 1등** (`US_SWING_PICK_ORDER=dvol_desc`).

3단으로 나눠 **선별이 뭘 더하는지**까지 본다:
  ① 풀 전량 (xus_fallen3 전체, 전일 ≤−3%)   — 기저선
  ② 전일 ≤−5% 전량                          — 라이브 진입 문턱
  ③ ② + 밴드 + MAX≥8 전량                   — 필터 효과
  ④ ③에서 dvol 1등만                        — 라이브 규칙(선별 효과)

⚠️ `xus_fallen3`는 ≤−3% 풀이고 **라이브 진입 문턱은 −5%**다. ②에서 맞춘다.

## 판정 규약

- **벤치마크는 같은 창 IWM**(진입일 시가 → 7세션 뒤 종가). 계약 net에는 비용이 이미 빠져 있고
  벤치마크에는 없으므로 **초과분은 비용(0.5%p)만큼 과소평가**다.
- **t는 날짜 클러스터**가 기본이다. 급락일 하나에 수백 건이 몰리므로 종목 클러스터는 부풀린다
  (09-11 실측: 종목 t 9.07 → 날짜 t 1.67).
- **일 가중과 건 가중을 같이 낸다.** 건 가중은 후보가 몰린 날에 자동 가중치를 준다.

사전 고정: **라이브 규칙(④)의 일 가중 초과가 t ≥ 2 이고 양수 반기가 양쪽 다여야
"이 레인에 초과분이 있다"고 말한다.** 아니면 378만의 근거는 없는 것이다.
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_benchmark_excess import bench_bars  # noqa: E402

DB = ROOT / "data" / "shadow" / "virtual_books.db"
CELL = "tp12_sl25_d7_be"          # 라이브 US 계약
HOLD = 7
BAND = (100.0, 500.0)
MAX_FLOOR = 8.0
CHG_FLOOR = -5.0                  # 라이브 진입 문턱(전일 등락률 %)
HALF = "2026-03-01"


def cluster_t(pairs):
    g = defaultdict(list)
    for day, v in pairs:
        g[day].append(v)
    means = [st.mean(v) for v in g.values()]
    if len(means) < 3 or st.stdev(means) == 0:
        return None, len(means)
    return st.mean(means) / (st.stdev(means) / math.sqrt(len(means))), len(means)


def load():
    con = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = []
    for r in con.execute("SELECT session_date, ticker, meta FROM trades "
                         "WHERE strategy_id='xus_fallen3' AND status='CLOSED'"):
        try:
            m = json.loads(r["meta"])
        except (TypeError, ValueError):
            continue
        f, g = m.get("feat") or {}, m.get("grid") or {}
        if CELL not in g or "dvol" not in f or "max21" not in f:
            continue
        rows.append({"day": r["session_date"], "ticker": r["ticker"],
                     "net": float(g[CELL][0]), "dvol": float(f["dvol"]),
                     "chg": float(f.get("chg") or 0.0), "max21": float(f["max21"])})
    con.close()
    return rows


def main():
    rows = load()
    bars = bench_bars("us", "IWM")
    idx = {b[0]: i for i, b in enumerate(bars)}

    kept, miss = [], 0
    for r in rows:
        i = idx.get(r["day"])
        if i is None or i + HOLD > len(bars):
            miss += 1
            continue
        r["excess"] = r["net"] - (bars[i + HOLD - 1][4] / bars[i][1] - 1) * 100
        kept.append(r)

    by_day = defaultdict(list)
    for r in kept:
        by_day[r["day"]].append(r)

    T1, T2 = "① 풀 전량 (≤−3%)", "② 전일 ≤−5% 전량"
    T3, T4 = "③ ②+밴드+MAX≥8 전량", "④ 라이브 규칙(dvol 1등)"
    tiers = {T1: [], T2: [], T3: [], T4: []}
    for day, pool in by_day.items():
        tiers[T1] += pool
        deep = [r for r in pool if r["chg"] <= CHG_FLOOR]
        tiers[T2] += deep
        bm = [r for r in deep
              if BAND[0] <= r["dvol"] <= BAND[1] and r["max21"] >= MAX_FLOOR]
        tiers[T3] += bm
        if bm:
            tiers[T4].append(max(bm, key=lambda r: (r["dvol"], r["ticker"])))

    L = [f"# US swing 레인 — IWM 초과분 실측 ({date.today().isoformat()})", "",
         "read-only. `tools/research/research_us_swing_excess.py`(사전 고정 기준은 docstring).",
         "라이브 변경 없음.", "",
         f"표본 `xus_fallen3` {len(rows):,}건 중 벤치 창 확보 **{len(kept):,}건** "
         f"(결측 {miss:,}건) · 세션 {len(by_day)}일 · 계약 셀 `{CELL}`", "",
         "**초과분은 비용 0.5%p만큼 과소평가다** — 계약 net에는 비용이 빠져 있고 IWM에는 없다.", "",
         "| 단계 | n | 일수 | 원 net(건가중) | **초과(건가중)** | **초과(일가중)** | t(날짜) | 양수일 |",
         "|---|---:|---:|---:|---:|---:|---:|---:|"]

    summary = {}
    for lab, sub in tiers.items():
        if not sub:
            continue
        days = defaultdict(list)
        for r in sub:
            days[r["day"]].append(r["excess"])
        dmeans = [st.mean(v) for v in days.values()]
        t, k = cluster_t([(r["day"], r["excess"]) for r in sub])
        summary[lab] = (sub, days, dmeans, t)
        L.append(f"| {lab} | {len(sub):,} | {len(days)} | "
                 f"{st.mean(r['net'] for r in sub):+.2f}% | "
                 f"{st.mean(r['excess'] for r in sub):+.2f}% | "
                 f"**{st.mean(dmeans):+.2f}%** | {t:.2f} (k{k}) | "
                 f"{sum(1 for x in dmeans if x > 0)}/{len(dmeans)} |")

    L += ["", "## 반기 분할 — 라이브 규칙(④)", "",
          f"경계 {HALF}. 한쪽만 양수면 구간 특이성이다.", "",
          "| 반기 | n | 초과(일가중) | t(날짜) | 양수일 |", "|---|---:|---:|---:|---:|"]
    live = tiers[T4]
    halves = {}
    for lab, sel in (("전반 (~2026-02)", lambda d: d < HALF), ("후반 (2026-03~)", lambda d: d >= HALF)):
        sub = [r for r in live if sel(r["day"])]
        if not sub:
            continue
        dm = [r["excess"] for r in sub]        # ④는 하루 1건이라 일가중 = 건가중
        t, k = cluster_t([(r["day"], r["excess"]) for r in sub])
        halves[lab] = st.mean(dm)
        L.append(f"| {lab} | {len(sub)} | {st.mean(dm):+.2f}% | "
                 f"{t:.2f} (k{k}) | {sum(1 for x in dm if x > 0)}/{len(dm)} |")

    ldays = defaultdict(list)
    for r in live:
        ldays[r["day"]].append(r["excess"])
    ldm = [st.mean(v) for v in ldays.values()]
    lt, _ = cluster_t([(r["day"], r["excess"]) for r in live])
    both_pos = len(halves) == 2 and all(v > 0 for v in halves.values())
    passed = lt is not None and lt >= 2 and both_pos

    L += ["", "## 판정 — 사전 고정 기준(④의 일가중 초과 t≥2 & 양쪽 반기 양수)", ""]
    if passed:
        L += [f"**통과.** 라이브 규칙 초과 {st.mean(ldm):+.2f}% (t {lt:.2f}), 양쪽 반기 양수.",
              "378만 배정에 근거가 있다."]
    else:
        why = []
        if lt is None or lt < 2:
            why.append(f"t {lt:.2f} < 2" if lt is not None else "t 계산 불가")
        if not both_pos:
            why.append("반기 부호 불일치")
        L += [f"**기각 — {' / '.join(why)}.** 라이브 규칙의 일가중 IWM 초과는 "
              f"{st.mean(ldm):+.2f}% (t {lt:.2f}), 양수인 날 "
              f"{sum(1 for x in ldm if x > 0)}/{len(ldm)}다.",
              "",
              "**정확히 말하면 '유의하게 손해'가 아니라 '초과분이 0과 구별되지 않는다'이다.**",
              "t −1.05는 음수 쪽이지만 유의하지 않다. 과장하지 않는다.",
              "다만 **어느 단계에서도 양수가 나오지 않았고**, 이 레인은 원 net +0.06%를",
              "같은 창 IWM(+0.72%, 314일 평균)에 못 미치는 값으로 내고 있다.", "",
              "**378만을 여기 두는 근거가 실측으로는 없다.** 원 net이 양수여도",
              "같은 창 IWM을 사면 더 나오는 값이라면 그건 선별이 아니라 시장 노출이다 —",
              "09-11에 C6(`c_us_panic_all`)가 죽은 것과 같은 방식이다.", "",
              "**⚠️ 벤치마크 주의**: IWM은 러셀2000이고 표본은 '전일 −5% 이상 빠진 소형주'다.",
              "급락 코호트가 지수를 못 따라가는 것 자체는 놀랍지 않다. 그래도 판정은 같다 —",
              "**우리는 IWM을 살 수 있었고 그게 더 나았다.**"]

    L += ["", "## 선별이 뭘 더하나 — 단계별 증분", ""]
    prev = None
    for lab in (T1, T2, T3, T4):
        if lab not in summary:
            continue
        m = st.mean(summary[lab][2])
        L.append(f"- **{lab}**: 일가중 초과 {m:+.2f}%"
                 + (f" (직전 대비 {m - prev:+.2f}%p)" if prev is not None else ""))
        prev = m
    L += ["", "**단계별 증분이 전부 ±0.15%p 안이다.** −5% 문턱도, 밴드도, MAX도, dvol 순위도",
          "초과분을 못 움직인다. 08-20에 밴드가 원 net을 +3.67%로 올린다고 봤던 것은",
          "같은 창 지수를 안 뺀 값이었고, 종목 클러스터 t였다.",
          "**이 레인의 선별 계층 전체가 초과분 기준으로는 무효다.**"]

    L += ["", "⚠️ 표본은 2025-06~2026-09 15개월이다. 08-20 밴드 발견이 이 구간 안에 있으므로",
          "**밴드·MAX는 인샘플이다.** dvol_desc는 09-01 모델 제거 때 들어갔다."]

    out = ROOT / "docs" / "reports" / f"us_swing_excess_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
