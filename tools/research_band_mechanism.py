#!/usr/bin/env python3
"""밴드가 net을 개선하는 기전 분해 (2026-08-30).

배경: research_early_exit_no_bump(08-30)에서 예상 밖 사실이 나왔다 —
**밴드는 무봉우리율을 줄이지 않는다**(밴드 안 50% vs 밖 42%, 전체 45%).
그런데 net은 개선한다(-0.14% vs -1.06%). 즉 우리가 암묵적으로 가정해온
"선별 = 무봉우리를 덜 산다"가 틀렸다. 08-20 스윕은 net만 봤고 기전은 안 봤다.

밴드를 "왜 좋은지 모르는 채" 라이브로 쓰고 있으므로, 개선 경로를 특정한다.
경로가 특정되면 강화 방향이 나오고, 특정되지 않으면 밴드 우위 자체가
표본 우연일 가능성을 의심해야 한다.

== 검정 대상 (사전 지정) ==
  경로1 손실 억제: 밴드 안 무봉우리 평균 net > 밴드 밖 무봉우리 평균 net
  경로2 수익 확대: 밴드 안 유봉우리 평균 net > 밴드 밖 유봉우리 평균 net
  경로3 포획 개선: 밴드 안의 첫 봉우리 도달이 더 빠르거나 TP 포획률이 높다

== 규약 ==
- baseline은 원장 실측(USD gross - 수수료). 08-30 판정에서 일봉 시뮬 재생이
  실거래 9건 중 2건만 재현해 폐기한 규약을 그대로 따른다.
- 봉우리 = 실제 보유창 내 MFE >= 4%(BE락 임계와 동일).
- **in-sample 경고**: 밴드(100~500M)는 08-20 전면 스윕이 이 계열 표본에서
  발견한 축이다. 여기서 나오는 우위는 확증이 아니라 기전 서술이다.
- 평균만 보지 않는다 — 꼬리(상·하위 10%)와 분포를 함께 낸다.

== 판정 (2026-08-30 실측, 표본 209건/156종목) ==

**기전 = 상방 포기와 맞바꾼 꼬리 손실 억제.** 경로1·2가 작동하고 경로3은 역방향이다.

  경로1 손실 억제  **작동** +2.29%p — 무봉우리 평균 밴드안 -5.56% vs 밖 -7.84%.
      결정적인 것은 평균이 아니라 **꼬리**다: 하위10% 평균이 -15.09% vs -23.47%.
      08-20 문서의 "밴드 밖 대형주(AXTI 1,281M)는 뉴스 주도 재평가라 되돌아오지
      않는 유형"과 정확히 맞는다. 밴드는 안 돌아오는 종목을 덜 산다.
  경로2 수익 확대  **작동** +1.33%p — 유봉우리 평균 +5.27% vs +3.94%,
      승률 79% vs 65%(+14%p). 봉우리가 서면 밴드 안이 더 잘 지킨다.
  경로3 포획 개선  **역방향** — MFE>=12% 도달 밴드안 15% vs 밖 22%,
      TP 포획 6% vs 11%. 첫 봉우리 시점은 양쪽 중앙 D1로 같다.
      **밴드는 큰 상승을 오히려 놓친다.** 그런데도 net이 나은 것은 꼬리 억제가
      상방 포기보다 크기 때문이다.

**⚠️ 그러나 우위 자체의 검정력이 없다.**
  클러스터 t: 밴드 안 +0.06 / 밴드 밖 -0.59 — 0과 구분 불가.
  월별: 07월 +3.06%p → **08월 -0.18%p로 소멸**.
  즉 밴드 우위는 07월 표본이 만든 것이고 08월에는 관측되지 않는다. 이것이
  국면 의존인지 표본 우연인지는 이 표본으로 가릴 수 없다(08-25 밴드+MAX OOS
  "판정 불가"와 같은 결론).

**해석 규약**: 이 결과를 "밴드를 빼자"로 읽지 않는다 — 08-20 스윕의 224세션
근거(클러스터 t=2.63)가 이 209건보다 크고, AXTI 소급 대조라는 실거래 증거도
있다. 반대로 "밴드가 검증됐다"로도 읽지 않는다. 정확한 상태는 **기전은
특정됐고 우위의 지속성은 미검증**이다. G3b(밴드+MAX 첫 실거래 코호트) 정산이
이 질문에 답할 첫 표본이다.

== 재판정 (2026-08-30, 계약 baseline 적용 후) ==

위 판정은 원장 baseline(TP/SL 없는 만기 보유) 기준이었다.
`research_early_exit_no_bump.contract_exit`로 TP12/SL25/BE락을 얹어 재산출한 결과
**경로2가 소멸했다.**

                    n    평균net   하위10%   상위10%  승률
  밴드안 무봉우리   42    -5.56%   -15.09%   +1.24%   10%
        유봉우리   42    +4.46%    -8.93%  +11.52%   71%
  밴드밖 무봉우리   53    -8.10%   -26.21%   +1.62%   17%
        유봉우리   72    +4.36%    -8.99%  +11.52%   65%

  경로1 손실 억제  **유지** +2.55%p (원장 baseline에선 +2.29%p)
      하위10%가 -15.09% vs -26.21%. SL25는 종가 기준으로 거의 안 걸리므로
      (209건 중 2건) 하방 꼬리를 자르는 것은 SL이 아니라 밴드다.
  경로2 수익 확대  **소멸** +1.33%p → **+0.11%p**
      TP12가 상한을 씌우면 양쪽 상위10%가 똑같이 +11.52%가 되고 평균도
      +4.46 vs +4.36으로 같아진다. 원장 baseline의 +1.33%p 차이는 **TP 없는
      시뮬의 산물**이었다. 남는 것은 승률 차이(71% vs 65%)뿐이다.

**최종 기전: 밴드의 가치는 하방 꼬리 억제 하나다.** 상방에서는 TP12가 이미
상한을 정하므로 밴드가 기여할 여지가 구조적으로 없다. 따라서 "밴드를 강화한다"는
**안 돌아오는 종목을 더 정확히 배제한다**는 뜻이지 상방 포획을 늘리는 것이
아니다.

우위 크기도 줄었다: 07월 +3.06 → +1.04%p, 08월 -0.18 → +0.05%p.
부호는 양월 모두 양수가 됐으나 클러스터 t는 여전히 -0.23/-1.13으로 검정력이 없다.
"""
from __future__ import annotations

import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_early_exit_no_bump import (  # noqa: E402
    BAND, NO_BUMP_PCT, TP_PCT, bars, cluster_t, load_sample, mfe_at,
)


def first_peak_session(rec: dict) -> int | None:
    for k in range(0, rec["held_sessions"] + 1):
        if mfe_at(rec["win"], rec["entry_price"], k) >= NO_BUMP_PCT:
            return k
    return None


def describe(label: str, rows: list[dict]) -> None:
    if not rows:
        print(f"  {label:22s} n=0")
        return
    nets = sorted(r["base_net"] for r in rows)
    n = len(nets)
    tail = max(1, n // 10)
    print(f"  {label:22s} n={n:3d} | 평균 {st.mean(nets):+6.2f}% | 합계 {sum(nets):+7.1f}%p "
          f"| 중앙 {st.median(nets):+6.2f}% | 하위10% {st.mean(nets[:tail]):+7.2f}% "
          f"| 상위10% {st.mean(nets[-tail:]):+6.2f}% | 승률 {100*sum(1 for x in nets if x>0)/n:3.0f}%")


def main() -> int:
    sample = load_sample()
    if not sample:
        print("[ERROR] 표본 0건")
        return 1
    for r in sample:
        r["peak_k"] = first_peak_session(r)
        r["has_peak"] = r["peak_k"] is not None
        r["mfe"] = mfe_at(r["win"], r["entry_price"], r["held_sessions"])
        r["in_band"] = (r["dvol"] is not None and BAND[0] <= r["dvol"] <= BAND[1])

    inb = [r for r in sample if r["in_band"]]
    out = [r for r in sample if r["dvol"] is not None and not r["in_band"]]
    print("=== 밴드 개선 기전 분해 (2026-08-30) ===")
    print(f"표본 {len(sample)}건 / 종목 {len({r['ticker'] for r in sample})}개 — "
          f"shadow 원장, in-sample(밴드는 이 계열에서 발견된 축)\n")

    print("[4분할] 봉우리 유무 x 밴드 내외")
    for lbl, grp in (("밴드 안", inb), ("밴드 밖", out)):
        print(f" {lbl}")
        describe("무봉우리(MFE<4%)", [r for r in grp if not r["has_peak"]])
        describe("유봉우리(MFE>=4%)", [r for r in grp if r["has_peak"]])
    print()

    nb_in = [r for r in inb if not r["has_peak"]]
    nb_out = [r for r in out if not r["has_peak"]]
    hp_in = [r for r in inb if r["has_peak"]]
    hp_out = [r for r in out if r["has_peak"]]

    def gap(a: list[dict], b: list[dict], name: str) -> None:
        if not a or not b:
            print(f"  {name}: 표본 부족")
            return
        d = st.mean([r["base_net"] for r in a]) - st.mean([r["base_net"] for r in b])
        ta = cluster_t([(r["ticker"], r["base_net"]) for r in a])
        tb = cluster_t([(r["ticker"], r["base_net"]) for r in b])
        print(f"  {name}: 차이 {d:+.2f}%p (밴드안 t={ta:+.2f} / 밴드밖 t={tb:+.2f})")

    print("[경로 판정]")
    gap(nb_in, nb_out, "경로1 손실 억제(무봉우리)")
    gap(hp_in, hp_out, "경로2 수익 확대(유봉우리)")

    # 경로3: 봉우리 시점 + TP 포획
    print("  경로3 포획 개선:")
    for lbl, grp in (("밴드 안", inb), ("밴드 밖", out)):
        peaks = [r["peak_k"] for r in grp if r["has_peak"]]
        dist: dict[str, int] = defaultdict(int)
        for k in peaks:
            dist[f"D{k}"] += 1
        tp_reach = sum(1 for r in grp if r["mfe"] >= TP_PCT)
        tp_captured = sum(1 for r in grp if r["base_net"] >= TP_PCT - 1.0)
        med = st.median(peaks) if peaks else float("nan")
        print(f"    {lbl}: 첫봉우리 중앙 D{med:.0f} {dict(sorted(dist.items()))} | "
              f"MFE>=12% 도달 {tp_reach}건({100*tp_reach/len(grp):.0f}%) → "
              f"TP 포획 {tp_captured}건({100*tp_captured/len(grp):.0f}%)")

    # 기여도 분해 — 밴드 밖 손실이 어디서 오는가
    print("\n[기여도] 밴드 밖 합계 손실의 출처")
    tot_out = sum(r["base_net"] for r in out)
    for lbl, grp in (("무봉우리", nb_out), ("유봉우리", hp_out)):
        s = sum(r["base_net"] for r in grp)
        print(f"  {lbl:10s} {s:+7.1f}%p ({100*s/tot_out:4.0f}% of {tot_out:+.1f}%p) n={len(grp)}")

    # 월별 부호
    print("\n[월별] 밴드 우위의 시간 안정성")
    for m in sorted({r["signal_date"][:7] for r in sample}):
        a = [r["base_net"] for r in inb if r["signal_date"][:7] == m]
        b = [r["base_net"] for r in out if r["signal_date"][:7] == m]
        if a and b:
            print(f"  {m}: 밴드안 {st.mean(a):+6.2f}%(n={len(a):3d}) vs "
                  f"밴드밖 {st.mean(b):+6.2f}%(n={len(b):3d}) → 차이 {st.mean(a)-st.mean(b):+.2f}%p")

    # 밴드 우위 자체의 클러스터 t
    print("\n[밴드 우위 검정력]")
    print(f"  밴드 안 클러스터 t={cluster_t([(r['ticker'], r['base_net']) for r in inb]):+.2f} "
          f"(n={len(inb)}, 종목 {len({r['ticker'] for r in inb})})")
    print(f"  밴드 밖 클러스터 t={cluster_t([(r['ticker'], r['base_net']) for r in out]):+.2f} "
          f"(n={len(out)}, 종목 {len({r['ticker'] for r in out})})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
