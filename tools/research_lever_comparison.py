#!/usr/bin/env python3
"""레버 비교 — 출구 임계(공격) vs 진입 필터(방어) (2026-08-30 사전등록).

운영자 질문: "방어가 공격일까? 공격을 파야 하나?"

배경: 손익분기 대수가 두 길을 준다. TP12에서 승 +11.52% / 패 -3.77% / 승률 20%,
손익분기 TP율은 24.7%다.
  공격: TP율 20% -> 24.7% (+5.1%p)
  방어: 패 평균 -3.77% -> -2.81% (+0.96%p)
어느 쪽이 실제로 움직이는지 같은 표본에서 나란히 잰다.

== 사전등록 (결과 확인 전 고정) ==

**A. 출구 임계 그리드**: TP in {6,8,10,12,15,20,25}. SL·BE락은 현행 고정.
   판정: 현행 TP12보다 유의하게 나은 임계가 있는가(클러스터 t >= 2).

**B. 진입 필터 조합**: 밴드(100~500M) 기준선에 08-30 통과 6축을 얹는다.
   축 방향은 08-30 실측에서 꼬리가 얕은 쪽으로 **미리 고정**한다 —
   등락·IBS·장중흐름·할인깊이는 상위, MAX20·ATR14는 하위.
   상관 때문에 실질 2계열이므로 계열 대표(등락 / ATR14)와 전조합을 낸다.
   분할은 표본 중앙값.

   **핵심 판정 지표는 평균이 아니다.** 밴드는 꼬리를 줄이지만 무봉우리를 더
   많이 사서(50% vs 밴드밖 42%) 순효과가 +0.22%p로 깎였다. 따라서
   **무봉우리율을 안 늘리면서 꼬리를 줄이는 조합**이 있는지가 질문이다.

**C. 용량 병기 필수**: 필터를 겹칠수록 후보가 준다. "알파=용량"은 이 저장소의
   반복 실측이다(08-21: 통과 22건 중 7건만 진입). n을 반드시 함께 읽는다.
   n < 25면 판정 대상에서 제외한다(30건 게이트 미만은 재앙 탐지기지 엣지
   증명기가 아니다).

**⚠️ 과적합 경고**: 조합 탐색은 이 저장소에서 가장 위험한 작업이다. 축 6개
   조합만으로도 시도 수가 폭증한다. 그래서 (a) 축 방향을 미리 고정하고
   (b) 계열 대표로 조합을 제한하며 (c) 통과해도 라이브 제안하지 않고 30건
   재검 목록에만 올린다. 시도 수 N에 **+12**를 기재한다(TP 7 + 조합 5).

**표본**: shadow 209건, 계약 baseline. 구 rank1 스트림이므로 현행 선별의 EV가
   아니다. USD 근사이며 KRW net 원장을 대체하지 않는다.

== 판정 (2026-08-30 실측) ==

**A. 출구 임계는 레버가 아니다 — 기각.**
TP 6/8/10/12/15/20/25 전 구간이 -0.63 ~ -0.81%로 평평하고 클러스터 t가 전부
|2| 미만이다. 낮추면 포획률이 오르는 만큼 건당 이익이 줄고 올리면 반대로
정확히 상쇄된다. **출구 위치로는 못 번다.** 앞으로 TP 논의가 나오면 이 표를
근거로 재검정을 생략한다(밴드 안 TP15만 t=+0.17로 유일한 양수인데 검정력
없음 — 30건 재검 목록에만 올린다).

**B. 진입 필터가 레버다 — 계열A 생존, 계열B 탈락.**

  조합                    n   무봉우리   꼬리     평균     t
  밴드 단독              84     50%   -11.08%  -0.55%  -0.23
  밴드+IBS(hi)          44     27%    -8.03%  +3.06%  +3.15
  밴드+등락(hi)          41     24%    -7.12%  +2.69%  +2.90
  밴드+MAX>=8(라이브)     50     50%   -12.82%  -1.15%  -0.55

**오늘 처음으로 클러스터 t가 2를 넘었다.** 무봉우리율이 절반(50%->24~27%)으로
떨어지면서 꼬리도 개선된다 — 밴드가 못 한 일이다(밴드는 꼬리만 줄이고
무봉우리는 오히려 늘렸다).

**OOS 월 분할 검증** (07월 임계로 08월 적용 / 그 역):
  07->08  등락 선택 25건 +3.18% t=2.23 | 배제 30건 -4.00%
          IBS  선택 26건 +3.67% t=2.73 | 배제 29건 -4.69%
          MAX20 +0.01% t=0.15 · ATR14 +0.28% t=0.41  <- 무효
  08->07  등락 +1.94% t=1.08 | IBS +1.84% t=1.21 (검증 표본 29건, 부호만 일치)
          ATR14 -1.59% t=-1.06  <- 역전

**계열B(MAX20/ATR14)는 in-sample에서만 보이고 OOS에서 사라진다.** 이는 라이브가
쓰는 MAX>=8의 근거를 약화시키지만, 08-20 스윕의 t=4.58(224세션)이 이 209건보다
크므로 제거를 주장하지 않는다 — 30건 재검 대상이다.

**스코프 검정**(밴드와의 상호작용): 등락·IBS는 밴드 밖에서도 방향이 유지되나
(밴드밖 +1.41/+0.66 vs 배제 -3.31/-2.16) 밴드 안에서 2~5배 강하다. 전체에서도
t=2.67/2.27. 즉 밴드와 계열A는 **경쟁이 아니라 보완**이다.

**⚠️ 그래도 라이브 제안하지 않는다.** 이유 넷:
  1. shadow 구 rank1 스트림이고 실거래 표본이 아니다.
  2. 두 달치이며 08->07 방향은 t<2다.
  3. 등락~IBS는 r=+0.62로 같은 계열이라 독립 증거 2개가 아니다.
  4. **용량 비용**: 08월 밴드 안 55건 -> 26건(47%). 실제 진입이 월 13건인데
     후보가 절반이면 진입 기회도 준다. "알파=용량"은 이 저장소의 반복 실측이다.
  5. 08-20 전면 스윕은 등락(drop_pct)을 "비단조·무변별"로 기각했다. 상호작용
     가설이 맞는지 아니면 이번이 우연인지 두 달로는 못 가른다.

**후속**: observe_tail_risk_axes(08-30 배선)가 이 축들을 이미 박제 중이므로
forward 표본이 자동으로 쌓인다. 현행 계약 정산 30건 시점에 실거래로 재검한다.
시도 수 N +12 (TP 7 + 조합 5).
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_early_exit_no_bump import (  # noqa: E402
    BAND, FEE_ROUND_TRIP, NO_BUMP_PCT, SL_PCT, BE_LOCK_PCT,
    cluster_t, load_sample, mfe_at,
)
from research_tail_risk_axes import features, tail_stats  # noqa: E402

HURDLE = 0.25
MIN_N = 25
# 08-30 실측에서 꼬리가 얕은 쪽. 결과를 보고 뒤집지 않기 위해 여기 박제한다.
AXIS_DIR = {"등락": "hi", "IBS": "hi", "장중흐름": "hi",
            "할인깊이": "hi", "MAX20": "lo", "ATR14": "lo"}


def sim(rec: dict, tp: float) -> tuple[float, bool]:
    ep, win, held = rec["entry_price"], rec["win"], rec["held_sessions"]
    peak = (win[0][4] - ep) / ep * 100.0
    for i in range(0, held + 1):
        _d, _o, hi, _l, c, _v = win[i]
        hip = (hi - ep) / ep * 100.0 if i > 0 else (c - ep) / ep * 100.0
        cp = (c - ep) / ep * 100.0
        if hip >= tp:
            return tp - FEE_ROUND_TRIP, True
        if cp <= SL_PCT:
            return cp - FEE_ROUND_TRIP, False
        if peak >= BE_LOCK_PCT and cp <= 0:
            return cp - FEE_ROUND_TRIP, False
        peak = max(peak, hip)
    return rec["base_net"], False


def row(label: str, rows: list[dict], base_nb: float | None = None) -> dict | None:
    if not rows:
        print(f"  {label:26s} n=  0")
        return None
    nets = [r["cnet"] for r in rows]
    nb = 100.0 * sum(1 for r in rows if r["nb"]) / len(rows)
    tail, _thick = tail_stats(nets)
    avg = st.mean(nets)
    t = cluster_t([(r["ticker"], r["cnet"]) for r in rows])
    flag = ""
    if len(rows) < MIN_N:
        flag = " 표본부족"
    elif avg >= HURDLE:
        flag = " ★허들도달"
    if base_nb is not None and nb <= base_nb and tail > -11.08:
        flag += " ◀무봉우리 유지+꼬리개선"
    print(f"  {label:26s} n={len(rows):3d} 무봉우리{nb:3.0f}% 꼬리{tail:+7.2f}% "
          f"평균{avg:+6.2f}% t={t:+5.2f}{flag}")
    return {"n": len(rows), "nb": nb, "tail": tail, "avg": avg, "t": t}


def main() -> int:
    sample = load_sample()
    for r in sample:
        r["feat"] = features(r)
    sample = [r for r in sample if r["feat"]]
    for r in sample:
        r["nb"] = mfe_at(r["win"], r["entry_price"], r["held_sessions"]) < NO_BUMP_PCT
        r["in_band"] = r["dvol"] is not None and BAND[0] <= r["dvol"] <= BAND[1]

    print("=== A. 출구 임계 그리드 (공격) ===")
    print(f"{'TP':>5s} | {'전체 평균':>9s} {'TP율':>5s} {'t':>6s} | {'밴드안 평균':>10s} {'t':>6s}")
    inb_all = [r for r in sample if r["in_band"]]
    for tp in (6, 8, 10, 12, 15, 20, 25):
        a = [sim(r, tp) for r in sample]
        b = [sim(r, tp) for r in inb_all]
        ta = cluster_t([(r["ticker"], x[0]) for r, x in zip(sample, a)])
        tb = cluster_t([(r["ticker"], x[0]) for r, x in zip(inb_all, b)])
        mark = "  <- 현행" if tp == 12 else ""
        print(f"{tp:5d} | {st.mean([x[0] for x in a]):+9.2f}% "
              f"{100*sum(1 for x in a if x[1])/len(a):4.0f}% {ta:+6.2f} | "
              f"{st.mean([x[0] for x in b]):+10.2f}% {tb:+6.2f}{mark}")
    print("  → 전 구간 음수이고 t가 모두 |2| 미만이면 출구 임계는 레버가 아니다.\n")

    # 계약 baseline(TP12) 고정 후 진입 필터 비교
    for r in sample:
        r["cnet"] = sim(r, 12.0)[0]

    print("=== B. 진입 필터 조합 (방어) ===")
    print(f"  기준선: 허들 +{HURDLE}%, 표본 하한 n>={MIN_N}")
    base = row("전체(구 rank1 스트림)", sample)
    band = row("밴드 단독", inb_all)
    band_nb = band["nb"] if band else None
    print()

    med = {a: st.median([r["feat"][a] for r in sample if r["feat"].get(a) is not None])
           for a in AXIS_DIR}

    def keep(r: dict, ax: str) -> bool:
        v = r["feat"].get(ax)
        if v is None:
            return False
        return v >= med[ax] if AXIS_DIR[ax] == "hi" else v < med[ax]

    for ax in AXIS_DIR:
        row(f"밴드 + {ax}({AXIS_DIR[ax]})", [r for r in inb_all if keep(r, ax)], band_nb)
    print()
    row("밴드 + 등락 + ATR14", [r for r in inb_all if keep(r, "등락") and keep(r, "ATR14")], band_nb)
    row("밴드 + IBS + ATR14", [r for r in inb_all if keep(r, "IBS") and keep(r, "ATR14")], band_nb)
    row("밴드 + 등락 + MAX20", [r for r in inb_all if keep(r, "등락") and keep(r, "MAX20")], band_nb)
    print()
    print("  [대조] 현행 라이브 조합")
    row("밴드 + MAX>=8 (라이브)",
        [r for r in inb_all if (r["feat"].get("MAX20") or 0) >= 8], band_nb)
    row("밴드 + MAX<8",
        [r for r in inb_all if (r["feat"].get("MAX20") or 0) < 8], band_nb)

    print(f"\n  ◀ 표시 = 무봉우리율이 밴드 단독({band_nb:.0f}%) 이하이면서 "
          f"꼬리가 개선된 조합")
    print("  ★ 표시 = 평균이 허들 도달. 단 n과 t를 함께 읽는다 — 표본이 줄면 "
          "용량이 준다(알파=용량).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
