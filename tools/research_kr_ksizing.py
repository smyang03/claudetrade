#!/usr/bin/env python3
"""k-조건부 사이징 실험 — 그날 후보 수에 따라 몇 개를 살 것인가 (read-only).

배경(2026-09-11): 랭킹은 잡음이고 레버는 용량이라는 판정이 났다
(`kr_rank_verdict_20260911.md`). 그러면 남는 질문은 "같은 자본에서 어느 날에
몇 개를 사느냐"다. 현행 KR 사다리(k<=1->1 / k 2~9->2 / k 10+->3)는 사전등록
설계지 실측 최적이 아니다.

설계:
  · 모집단·정산은 E5 규약 그대로(`research_kr_rank_pool_reconcile.build`, 게이트 검증됨).
  · **슬롯을 규칙 간에 고정한다.** 안 그러면 사이징이 아니라 용량을 다시 재는 것이 된다.
  · 자본은 **배분이 실제로 구속되는 수준**에서 본다(66만=3슬롯, 220만=10슬롯).
    자본이 크면 모든 규칙이 "전부 사기"로 수렴해 비교가 무의미해진다.
  · 픽은 무작위 60시드 중앙값 — 랭킹 축은 이미 기각됐으므로 사이징만 분리한다.
    단일 시드로 보면 66만 구간 차이가 전부 잡음에 묻힌다(실측 확인).
  · 규칙 10종 × 자본 3종 × 연도 2종 = 60칸을 훑고 최선을 고르는 것이므로
    **다중비교 편향이 있다.** 6/6을 이겨도 확정이 아니라 후보다.
  · 2026 적합 / 2025 확인. 2025는 k>=5인 날이 3일뿐이라 **부호만** 볼 수 있다.

주의: 슬롯 해제가 다음 후보일 기준이라 진입을 과소 계산한다(보수적).
"""
from __future__ import annotations

import json
import statistics as st
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kr_fallen_ranking_experiment import CACHES  # noqa: E402
from tools.research_kr_rank_pool_reconcile import (  # noqa: E402
    ORDER_KRW, build, by_session, random_chooser,
)

BIG = 10 ** 6


def current_ladder(k):
    """라이브 현행 — KR_FALLEN_PHASE3_CAPACITY_ENABLED 사다리."""
    return 1 if k <= 1 else (2 if k <= 9 else 3)


RULES = {
    "현행사다리 1/2/3": current_ladder,
    "고정 1건": lambda k: 1,
    "고정 2건": lambda k: 2,
    "고정 3건": lambda k: 3,
    "전부(슬롯까지)": lambda k: BIG,
    "k<5 건너뛰기 → 전부": lambda k: 0 if k < 5 else BIG,
    "k<5 건너뛰기 → 3건": lambda k: 0 if k < 5 else 3,
    "k<10 건너뛰기 → 전부": lambda k: 0 if k < 10 else BIG,
    "비례 k/4": lambda k: max(1, k // 4),
    "비례 k/2": lambda k: max(1, k // 2),
}

CAPITALS = (660_000, 2_200_000, 8_800_000)
SEEDS = 60


def run(sessions, rule, capital):
    """60시드 손익 분포를 돌려준다(픽 무작위 — 랭킹 축은 기각됨)."""
    from tools.research_kr_rank_pool_reconcile import sequence
    slots = int(capital // ORDER_KRW)
    return [sequence(sessions, random_chooser(s), slots=slots, per_day_fn=rule)[0]
            for s in range(SEEDS)]


def table(sessions, label, months, lines):
    def emit(t=""):
        print(t)
        lines.append(t)
    emit(f"\n### {label} (세션 {len(sessions)}일 / {months}개월 / "
         f"후보 {sum(len(v) for v in sessions.values())}건)")
    emit("")
    emit("| 사이징 규칙 |" + "".join(f" 자본 {c:,}원 (슬롯 {c // ORDER_KRW}) |" for c in CAPITALS))
    emit("|---|" + "---:|" * len(CAPITALS))
    dist = {name: {cap: run(sessions, rule, cap) for cap in CAPITALS}
            for name, rule in RULES.items()}
    base = dist["현행사다리 1/2/3"]
    for name in RULES:
        cells = []
        for cap in CAPITALS:
            v = dist[name][cap]
            med = st.median(v)
            if name == "현행사다리 1/2/3":
                cells.append(f" {med:+,.0f}원 |")
            else:
                win = sum(a > b for a, b in zip(v, base[cap]))
                cells.append(f" {med:+,.0f}원 (승 {win}/{SEEDS}) |")
        emit(f"| {name} |" + "".join(cells))
    emit("")
    emit(f"{SEEDS}시드 중앙값. 승 n/{SEEDS}은 같은 시드에서 현행 사다리를 이긴 횟수 — "
         f"30/{SEEDS}이 동전던지기다.")
    emit("슬롯은 자본÷22만으로 규칙 간 고정했다. 안 그러면 사이징이 아니라 용량을 재는 것이 된다.")
    return lines


def main():
    lines = [f"# KR k-조건부 사이징 실험 ({date.today().isoformat()})", "",
             "read-only 사후 분석. 라이브 변경 아님. 모집단·정산은 E5 규약",
             "(`research_kr_rank_pool_reconcile.build`, 원본 일치 게이트 통과).",
             f"픽은 무작위 {SEEDS}시드 중앙값 — 랭킹 축은 기각됐으므로 사이징만 분리한다.",
             "규칙 10종 × 자본 3종 × 연도 2종을 훑으므로 다중비교 편향이 있다 — 6/6도 후보지 확정이 아니다.",
             "슬롯을 규칙 간에 고정하지 않으면 사이징이 아니라 용량을 다시 재는 것이 된다."]
    for year, path in CACHES.items():
        ss = by_session(build(json.loads(path.read_text(encoding="utf-8"))))
        months = len({d[:7] for d in ss})
        lines.append(f"\n## {year} 캐시")
        table(ss, f"{year} — 라이브 바닥 적용", months, lines)
        k5 = [d for d, v in ss.items() if len(v) >= 5]
        dm = [st.mean(x["net"] for x in v) for d, v in ss.items() if len(v) >= 5]
        lines.append(f"\nk>=5인 날 {len(k5)}일, 그날 일평균 {st.mean(dm):+.2f}%. "
                     f"k<5인 날 {len(ss) - len(k5)}일, {st.mean([st.mean(x['net'] for x in v) for d, v in ss.items() if len(v) < 5]):+.2f}%.")
        if len(k5) <= 7:
            lines.append(f"표본이 {len(k5)}일뿐이다 — 크기가 아니라 부호만 읽는다. 해당일: {', '.join(k5)}")
        print(lines[-1])
    out = ROOT / "docs" / "reports" / f"kr_ksizing_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
