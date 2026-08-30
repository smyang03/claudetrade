#!/usr/bin/env python3
"""꼬리 위험 축 재검 — 평균이 아니라 하방 꼬리로 판정한다 (2026-08-30 사전등록).

배경: research_band_mechanism(08-30)이 밴드의 기전을 특정했다 — **하방 꼬리
억제 하나**다. 계약 baseline에서 밴드 안/밖의 평균 차이는 +2.55%p인데
하위10% 평균 차이는 -15.09% vs -26.21%로 **11%p**다. 즉 밴드는 평균보다
꼬리에서 3~4배 강하게 작동한다.

그런데 08-20 전면 스윕(19축 → 3축 생존)은 **평균 net 기준**이었다. 꼬리
기준으로는 아무도 본 적이 없다. 평균으로 무변별인 변수가 꼬리 억제로는
유효할 수 있고, 밴드 자신이 그 증거다.

**가설**: 기각된 축 중 일부는 "안 돌아오는 종목"(뉴스 주도 재평가형)을
평균보다 꼬리에서 더 잘 가른다.

== 사전등록 (결과 확인 전 고정) ==

- 표본: shadow MATURED 209건. baseline은 **계약 적용**(TP12/SL25/BE락,
  `research_early_exit_no_bump.contract_exit`) — 원장 그대로 쓰면 상방이 안
  잘려 꼬리 비교가 오염된다(08-30 2차 검증에서 확인된 오류).
- 축 10종(전부 신호일 종가 기준 past-only, lookahead 없음):
  등락·IBS(종가위치)·갭·장중흐름·거래량비·MAX20·거래대금·20일할인깊이·
  ATR14·모델확률.
- 분할: 각 축의 **표본 중앙값**으로 2분할(세션 내 분할은 세션당 건수가 적어
  불가).
- 판정 지표 2종 병기:
  ① 하위20% 평균(CVaR 근사) — 그룹당 ~20건 확보를 위해 10%가 아닌 20%.
  ② net <= -15% 건수 비율 — 꼬리 두께의 직접 측정.
- 판정 기준(고정): ①②가 **같은 방향** + 월별 부호 일치 + 밴드 대비 유의미한
  크기(꼬리 차이 >= 5%p). 셋 다 만족해야 후속 정밀검정 대상으로 승격한다.
  통과해도 **바로 라이브 제안하지 않는다** — 재검이라 다중검정 부담이 크다.
- 시도 수 N에 **+10**을 기재한다(사전등록 §다중검정 규약).

**in-sample 경고**: 이 209건은 밴드가 발견된 계열의 표본이다. 여기서 나오는
꼬리 우위는 발견이지 검증이 아니다. 밴드를 벤치마크로 함께 출력해 상대 크기를
읽는 용도다.

== 판정 (2026-08-30 실측, 표본 209건/156종목) ==

10축 중 6축이 3기준을 통과했고 **전부 밴드 벤치마크(꼬리차 3.77%p)보다 크다**.
그러나 상관을 걷어내면 실질은 **2계열**이다.

  계열A 하락강도·회복  등락(8.78%p) / IBS(7.65) / 장중흐름(6.91)
      상호 r=+0.62~+0.87 — 사실상 한 축이다. 덜 빠지고 종가가 고가 근처인 쪽이
      꼬리가 얕다. 라이브에 없는 축이며 급락 반등 철학과 상충할 소지가 있다.
  계열B 변동성        ATR14(8.86%p) / MAX20(8.03) / 할인깊이(6.19)
      MAX20~ATR14 r=+0.69, 할인깊이~ATR14 r=-0.70 — 한 축이다.
      **방향이 라이브와 반대다.**

**핵심 발견 — 변동성 축은 TP 상한과 상호작용한다.**

           TP포획   꼬리      평균     클러스터t
  MAX>=8     27%  -15.76%  -1.21%    -1.50   ← 라이브가 요구하는 쪽
  MAX<8       8%   -8.02%  -0.02%    +0.53
  ATR상위     31%  -16.93%  -1.53%    -1.63
  ATR하위      9%   -8.07%  -0.01%    +0.11

높은 변동성은 **TP 포획을 3배 늘리지만 꼬리를 2배 키우고, 순효과는 마이너스**다.
기전은 비대칭이다 — TP12가 상방을 12%에서 자르는데 하방은 사실상 열려 있다
(SL25는 종가 기준으로 209건 중 2건만 발동). 변동성이 주는 상방 이득은 상한에
막히고 하방 손실만 온전히 실현된다.
월별 방향은 07·08월 모두 일관하고 밴드 안에서도 유지된다(08월 밴드 안:
MAX>=8 -1.95% vs MAX<8 +0.20%).

**⚠️ 그러나 이것으로 MAX를 빼자고 주장하지 않는다.** 클러스터 t가 -1.50/+0.53로
검정력이 없고, 08-20 스윕이 MAX≥8을 클러스터 t=4.58로 채택한 근거(224세션)가
이 209건보다 크다. 두 결과가 충돌하는 상태다.

**대신 새 가설을 등록한다: 선별 축(MAX)과 출구 축(TP)은 독립이 아니다.**
사전등록 문서는 "선별 축 변경과 출구 축 변경은 다르다"고 분리해왔는데, 이
실측은 둘이 상호작용함을 시사한다 — MAX를 유지하려면 TP를 올려 상방을 열어야
하고, TP12를 유지하려면 MAX가 역효과일 수 있다. **둘은 같이 결정해야 하는 쌍**
이라는 가설이다.

재검 조건: G3b 이후 현행 계약 정산 30건 시점에 (a) MAX 고저별 TP 포획률과
꼬리를 실거래로 재산출하고, (b) TP 사다리 counterfactual([A11])과 교차한다.
그 전에는 어느 쪽도 건드리지 않는다.

**거래대금이 꼬리차 0.34%p로 꼴찌인 것은 밴드 무효의 증거가 아니다** — 밴드는
양방향 컷(100~500M)인데 이 검정은 중앙값 단방향 분할이라 구조적으로 못 잡는다.
벤치마크 줄(밴드 안/밖 3.77%p)이 정확한 값이다.
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_early_exit_no_bump import (  # noqa: E402
    BAND, bars, cluster_t, contract_exit, load_sample,
)

TAIL_FRAC = 0.20
TAIL_CUT = -15.0
MIN_GAP = 5.0


def features(rec: dict) -> dict | None:
    """신호일 종가까지만 쓰는 past-only 피처."""
    b = bars(rec["ticker"])
    i = next((j for j, x in enumerate(b) if x[0] == rec["signal_date"]), None)
    if i is None or i < 20:
        return None
    d, o, hi, lo, c, v = b[i]
    prev_c = b[i - 1][4]
    win20 = b[i - 19: i + 1]
    vols = [x[5] for x in win20]
    trs = []
    for j in range(i - 13, i + 1):
        pc = b[j - 1][4]
        trs.append(max(b[j][2] - b[j][3], abs(b[j][2] - pc), abs(b[j][3] - pc)))
    return {
        "등락": (c / prev_c - 1) * 100 if prev_c else None,
        "IBS": (c - lo) / (hi - lo) * 100 if hi > lo else None,
        "갭": (o / prev_c - 1) * 100 if prev_c else None,
        "장중흐름": (c / o - 1) * 100 if o else None,
        "거래량비": v / st.mean(vols) if st.mean(vols) else None,
        "MAX20": max(100 * (b[j][4] / b[j - 1][4] - 1) for j in range(i - 19, i + 1)),
        "거래대금": c * v / 1e6,
        "할인깊이": (c / max(x[2] for x in win20) - 1) * 100,
        "ATR14": st.mean(trs) / c * 100 if c else None,
        "모델확률": rec.get("probability"),
    }


def tail_stats(nets: list[float]) -> tuple[float, float]:
    if not nets:
        return float("nan"), float("nan")
    s = sorted(nets)
    k = max(1, int(len(s) * TAIL_FRAC))
    return st.mean(s[:k]), 100.0 * sum(1 for x in s if x <= TAIL_CUT) / len(s)


def main() -> int:
    sample = load_sample()
    for r in sample:
        r["cnet"] = contract_exit(r)[0]
        r["feat"] = features(r)
    sample = [r for r in sample if r["feat"]]
    print("=== 꼬리 위험 축 재검 (사전등록 2026-08-30) ===")
    print(f"표본 {len(sample)}건 / 종목 {len({r['ticker'] for r in sample})}개 "
          f"— 계약 baseline(TP12/SL25/BE락), in-sample\n")

    allnets = [r["cnet"] for r in sample]
    at, ap = tail_stats(allnets)
    print(f"[전체] 평균 {st.mean(allnets):+.2f}% | 하위{TAIL_FRAC:.0%} {at:+.2f}% | "
          f"net<={TAIL_CUT:.0f}% 비율 {ap:.0f}%\n")

    # 벤치마크: 밴드
    inb = [r["cnet"] for r in sample if r["dvol"] is not None and BAND[0] <= r["dvol"] <= BAND[1]]
    outb = [r["cnet"] for r in sample if r["dvol"] is not None and not (BAND[0] <= r["dvol"] <= BAND[1])]
    bi, bpi = tail_stats(inb)
    bo, bpo = tail_stats(outb)
    print(f"[벤치마크] 밴드(100~500M) 안 n={len(inb)} 하위{TAIL_FRAC:.0%} {bi:+.2f}% (<=-15%: {bpi:.0f}%)")
    print(f"           밴드 밖    n={len(outb)} 하위{TAIL_FRAC:.0%} {bo:+.2f}% (<=-15%: {bpo:.0f}%)")
    print(f"           → 꼬리 차이 {bi-bo:+.2f}%p | 두께 차이 {bpi-bpo:+.0f}%p\n")

    axes = list(sample[0]["feat"].keys())
    print(f"{'축':10s} {'분할':>8s} {'상위평균':>8s} {'하위평균':>8s} {'상위꼬리':>8s} {'하위꼬리':>8s} "
          f"{'꼬리차':>7s} {'두께차':>7s} {'월부호':>6s} 판정")
    results = []
    for ax in axes:
        vals = [(r, r["feat"][ax]) for r in sample if r["feat"].get(ax) is not None]
        if len(vals) < 40:
            print(f"{ax:10s} 표본부족 n={len(vals)}")
            continue
        med = st.median([v for _r, v in vals])
        hi = [r for r, v in vals if v >= med]
        lo = [r for r, v in vals if v < med]
        if len(hi) < 20 or len(lo) < 20:
            print(f"{ax:10s} 분할 실패")
            continue
        hn = [r["cnet"] for r in hi]
        ln = [r["cnet"] for r in lo]
        ht, hp = tail_stats(hn)
        lt, lp = tail_stats(ln)
        # 좋은 쪽(꼬리가 얕은 쪽) 기준으로 차이를 낸다
        gap_tail = ht - lt
        gap_thick = lp - hp
        # 월별 부호: 좋은 쪽 그룹이 매월 꼬리가 얕은가
        good_hi = gap_tail > 0
        months = sorted({r["signal_date"][:7] for r in sample})
        signs = []
        for m in months:
            a = [r["cnet"] for r in (hi if good_hi else lo) if r["signal_date"][:7] == m]
            b = [r["cnet"] for r in (lo if good_hi else hi) if r["signal_date"][:7] == m]
            if len(a) >= 5 and len(b) >= 5:
                signs.append(tail_stats(a)[0] - tail_stats(b)[0] > 0)
        mon = "일치" if signs and all(signs) else ("혼재" if signs else "부족")
        ok = abs(gap_tail) >= MIN_GAP and (gap_thick > 0) == good_hi and mon == "일치"
        results.append((ax, abs(gap_tail), ok))
        print(f"{ax:10s} {med:>8.2f} {st.mean(hn):>+8.2f} {st.mean(ln):>+8.2f} "
              f"{ht:>+8.2f} {lt:>+8.2f} {gap_tail:>+7.2f} {gap_thick:>+7.0f} {mon:>6s} "
              f"{'통과' if ok else ''}")

    print("\n[요약] 꼬리 차이 크기순")
    for ax, g, ok in sorted(results, key=lambda x: -x[1]):
        print(f"  {ax:10s} 꼬리차 {g:5.2f}%p {'← 3기준 통과' if ok else ''}")
    passed = [a for a, _g, ok in results if ok]
    print(f"\n통과 {len(passed)}축: {passed if passed else '없음'}")
    print(f"(밴드 벤치마크 꼬리차 {abs(bi-bo):.2f}%p 대비 상대 크기로 읽는다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
