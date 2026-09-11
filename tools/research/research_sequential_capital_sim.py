#!/usr/bin/env python3
"""순차 자본 구조 — 패닉 ETF(밤) + KR 급락(낮 D5)을 한 자본으로 (read-only, 2026-09-12).

09-12에 "두 레인이 같은 자본을 순차로 쓴다"고 적었다. 근거는 시각이었다 —
패닉 ETF는 급락일 **15:30 종가 매수 → 익일 09:00 시가 매도**, KR 급락은 **익일 09:00 시가 매수**.
ETF를 파는 그 시가에 개별주를 산다. 신호도 겹친다(k≥20인 26일 중 25일이 패닉 신호일).

**그런데 그건 하루짜리 그림이다.** KR 급락은 **D5 보유**다. 5일간 자본이 묶여 있으면
그 사이에 뜨는 패닉 신호는 못 받는다. 게다가 패닉 신호는 연쇄 급락일에 몰린다
(2026-07-28/29/30). **진짜 순차인지, 급락 레인이 ETF의 밤을 잡아먹는지**를 실제로 센다.

## 이건 가설 검정이 아니라 포트폴리오 구성 시뮬이다

t 기준을 두지 않는다. 물어야 할 것은 세 가지다.
 1. 한 자본을 두 레인에 쓰면 **ETF 밤을 몇 개 잃는가.**
 2. 합쳐서 **연 수익이 각각보다 나은가.**
 3. 둘 중 하나를 빼면 어떻게 되는가.

## 규약

- 자본은 **3단위**(라이브 KR 급락 슬롯 3과 같은 구조). 단위당 금액은 결과를 %로 내므로 무관.
- **패닉 ETF**: 신호일 종가에 **비어 있는 단위 전부** 투입 → 익일 시가 회수(1박).
  233740, 비용 0.05%, 세후 15.4%.
- **KR 급락**: 신호일 다음 시가에 **1단위씩**, 사전등록 사다리(k≤1→1건 / 2~9→2건 / 10+→3건),
  D5(TP12/SL25), 비용 0.25% 포함(E5 규약). KR 주식은 매매차익 비과세.
- **충돌 규칙**: 개별주가 들고 있는 단위는 그날 밤 ETF에 못 쓴다. 반대로
  ETF는 1박이라 아침이면 반드시 풀린다 — **ETF가 개별주를 막는 일은 없다.**
- 4개 arm을 같은 자본·같은 달력으로 돌린다:
  A) ETF 단독 · B) 급락 단독 · C) 순차 결합(둘 다) · D) 순차 결합 + k≥20인 날만 급락.

구간 2018-01~2026-09(두 데이터가 겹치는 창). 원 수익과 **연도별**을 같이 낸다.
⚠️ KR 급락 쪽은 생존편향 하한 3.3%가 그대로 적용된다 — **결합 수치는 위쪽으로 치우쳐 있다.**
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.kr_index_etf_panic_shadow as P  # noqa: E402  (read-only)
from tools.research_kr_rank_pool_reconcile import build, by_session  # noqa: E402

LONG = ROOT / "data" / "price_backfill_yf" / "kr_fallen_price_cache_2018_2026.json"
TICKER = "233740"
START = "2018-01-01"
UNITS = 3
ETF_TAX = 0.154


def ladder(k):
    return 1 if k <= 1 else (2 if k <= 9 else 3)


def simulate(days, panic, fallen, *, use_etf, use_fallen, fallen_min_k=0):
    """단위 기반 시뮬. 반환: (단위·연도별 수익%, 통계)."""
    busy = {}                       # unit_index -> 개별주 청산일(그날 아침 풀림)
    by_year = defaultdict(float)
    etf_taken = etf_blocked = fal_taken = 0
    for d in days:
        # 아침: 만기된 개별주 단위 회수
        busy = {u: e for u, e in busy.items() if e > d}
        free = [u for u in range(UNITS) if u not in busy]

        # 아침 09:00 — 전날 신호의 개별주 진입
        if use_fallen:
            pool = fallen.get(d)
            if pool and len(pool) >= fallen_min_k:
                room = min(ladder(len(pool)), len(free))
                for c in pool[:room]:
                    busy[free.pop(0)] = c["exit_date"]
                    by_year[d[:4]] += c["net"] / UNITS
                    fal_taken += 1

        # 15:30 — 패닉 신호. 남은 빈 단위 전부
        if use_etf and d in panic:
            if free:
                net = panic[d]
                after = net * (1 - ETF_TAX) if net > 0 else net
                by_year[d[:4]] += after * len(free) / UNITS
                etf_taken += 1
            else:
                etf_blocked += 1
    return by_year, {"etf_taken": etf_taken, "etf_blocked": etf_blocked,
                     "fallen_taken": fal_taken}


def main():
    if not LONG.exists():
        print(f"장기 캐시 없음: {LONG}", file=sys.stderr)
        return 2
    sessions = by_session(build(json.loads(LONG.read_text(encoding="utf-8"))))
    fallen = {d: v for d, v in sessions.items() if d >= START}

    panic = {}
    for r in P._load():
        if (r.get("kind") == "trade" and r["ticker"] == TICKER
                and r.get("status") == "CLOSED" and r["session_date"] >= START):
            panic[r["session_date"]] = r["net_pct"]

    days = sorted(set(fallen) | set(panic))
    years = sorted({d[:4] for d in days})
    n_years = len(years)

    arms = [("A) ETF 단독", dict(use_etf=True, use_fallen=False)),
            ("B) 급락 단독", dict(use_etf=False, use_fallen=True)),
            ("C) 순차 결합", dict(use_etf=True, use_fallen=True)),
            ("D) 결합 + 급락은 k≥20만", dict(use_etf=True, use_fallen=True, fallen_min_k=20))]

    L = [f"# 순차 자본 구조 — 패닉 ETF(밤) + KR 급락(낮) 한 자본 ({date.today().isoformat()})", "",
         "read-only. `tools/research/research_sequential_capital_sim.py`(규약은 docstring).",
         "라이브 변경 없음. **가설 검정이 아니라 포트폴리오 구성 시뮬이다** — t 기준을 두지 않는다.", "",
         f"자본 {UNITS}단위(라이브 급락 슬롯 3과 같은 구조) · 구간 {days[0]}~{days[-1]} "
         f"({n_years}개 연도) · 패닉 신호 {len(panic)}일 · 급락 신호 {len(fallen)}일", "",
         "**질문**: 개별주 D5 보유가 ETF의 밤을 잡아먹는가, 합치면 각각보다 나은가.", "",
         "| arm | 연평균 | 중앙 연 | 최악 해 | 양수 연도 | ETF 진입 | **ETF 놓침** | 급락 진입 |",
         "|---|---:|---:|---:|---:|---:|---:|---:|"]

    results = {}
    for lab, kw in arms:
        by_year, stat = simulate(days, panic, fallen, **kw)
        vals = [by_year.get(y, 0.0) for y in years]
        results[lab] = (vals, stat)
        L.append(f"| {lab} | **{st.mean(vals):+.2f}%** | {st.median(vals):+.2f}% | "
                 f"{min(vals):+.2f}% | {sum(1 for v in vals if v > 0)}/{n_years} | "
                 f"{stat['etf_taken']} | **{stat['etf_blocked']}** | {stat['fallen_taken']} |")

    a_etf = results["A) ETF 단독"][1]
    c_stat = results["C) 순차 결합"][1]
    lost = c_stat["etf_blocked"] - a_etf["etf_blocked"]
    L += ["", "## 1. 개별주 D5 보유가 ETF의 밤을 잡아먹는가", "",
          f"ETF 단독일 때 놓친 밤 **{a_etf['etf_blocked']}개**(단독이면 항상 자본이 비어 있으므로 0이어야 정상).",
          f"결합하면 **{c_stat['etf_blocked']}개**를 놓친다 — 개별주 때문에 추가로 잃는 밤 **{lost}개**"
          f"({100 * lost / max(1, len(panic)):.0f}%).", "",
          ("**순차가 아니다. 개별주 D5가 ETF의 밤을 상당수 막는다.**" if lost >= len(panic) * 0.15
           else "**대체로 순차가 성립한다. 막히는 밤이 많지 않다.**")]

    taken = []
    busy2 = {}
    for d in days:
        busy2 = {u: e for u, e in busy2.items() if e > d}
        free2 = [u for u in range(UNITS) if u not in busy2]
        pool = fallen.get(d)
        if pool:
            for c in pool[:min(ladder(len(pool)), len(free2))]:
                busy2[free2.pop(0)] = c["exit_date"]
                taken.append(c["net"])
    all_cand = [c["net"] for v in fallen.values() for c in v]
    L += ["", "## 1-2. 부수 확인 — 슬롯을 끼우면 건당 수익이 달라진다", "",
          "| | 건수 | 건당 평균 net | 승률 |", "|---|---:|---:|---:|",
          f"| 풀 전체 후보(건 가중) | {len(all_cand):,} | {st.mean(all_cand):+.2f}% | "
          f"{100 * sum(1 for x in all_cand if x > 0) / len(all_cand):.1f}% |",
          f"| **슬롯 3으로 실제 진입** | {len(taken)} | **{st.mean(taken):+.2f}%** | "
          f"{100 * sum(1 for x in taken if x > 0) / len(taken):.1f}% |", "",
          "**건 가중 풀 평균은 후보가 쏟아진 날이 끌어올린 값이고, 슬롯 3으로는 그 날을 다 못 받는다.**",
          "09-12 오전의 '건 가중 vs 일 가중' 교훈이 자본 시뮬에서 그대로 재현된다 —",
          f"풀 평균 {st.mean(all_cand):+.2f}%를 레인 성능으로 인용하면 안 된다. 실제로 잡히는 건 {st.mean(taken):+.2f}%다.",
          "", "## 2. 연도별", "",
          "| 연도 | " + " | ".join(lab for lab, _ in arms) + " |",
          "|---|" + "---:|" * len(arms)]
    for y in years:
        L.append(f"| {y} | " + " | ".join(
            f"{results[lab][0][years.index(y)]:+.2f}%" for lab, _ in arms) + " |")

    best = max(arms, key=lambda a: st.mean(results[a[0]][0]))[0]
    L += ["", "## 3. 판단", "",
          f"연평균이 가장 높은 것은 **{best}**({st.mean(results[best][0]):+.2f}%)다.", ""]

    a_mean = st.mean(results["A) ETF 단독"][0])
    c_mean = st.mean(results["C) 순차 결합"][0])
    b_mean = st.mean(results["B) 급락 단독"][0])
    L += [f"- ETF 단독 {a_mean:+.2f}% · 급락 단독 {b_mean:+.2f}% · 결합 {c_mean:+.2f}%",
          f"- **결합 − ETF 단독 = {c_mean - a_mean:+.2f}%p.** "
          + ("개별주를 얹으면 더 번다." if c_mean > a_mean else
             "**개별주를 얹으면 오히려 못 번다 — 막힌 밤의 값이 개별주가 벌어온 것보다 크다.**"),
          "",
          "⚠️ 급락 쪽 수치에는 **생존편향 하한 3.3%**가 그대로 들어 있다"
          "(`kr_pool_long_verdict_20260912.md`). 즉 위 결합·급락 수치는 **위쪽으로 치우쳐 있고**,",
          "그걸 감안하면 결론은 ETF 쪽으로 더 기운다.",
          "⚠️ ETF는 세후 15.4%, KR 주식은 비과세로 계산했다. 비용은 ETF 0.05% / 급락 0.25%."]

    out = ROOT / "docs" / "reports" / f"sequential_capital_sim_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
