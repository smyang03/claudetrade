from __future__ import annotations

"""코어 sleeve의 변동성 타겟팅(VOL12) 신호를 긴 표본으로 검증한다.

배경: 2026-07-22 core_shadow_mtm 실측에서 primary 2종(KR_FACTOR_TREND_V1,
US_SCHG_BIL_TREND_V1)이 벤치마크 대비 특별히 나쁘지는 않았으나, 양 시장에서
**VOL12(변동성 타겟팅) 벤치만 방어에 성공**했다(KR -1.5% vs 나머지 -7.5%대,
US -1.3%로 최상위). 같은 방향이 두 시장에서 나오면 우연으로 보기 어렵다.
다만 원장 표본이 가격시점 6개(9일)뿐이라 판정할 수 없었다.

여기서는 동일 구조를 일봉으로 길게 재현해 그 방어가 실재하는지 본다.

비교 대상(모두 동일 지수를 쓰고 포지션 결정만 다르다):
  1) buy&hold            — 항상 100%
  2) SMA10 트렌드        — 종가 > SMA10이면 100%, 아니면 현금
  3) SMA10 + 변동성타겟   — 위와 같되 목표변동성/실현변동성으로 노출 조절(상한 100%)

no-lookahead: 포지션은 t일 종가까지의 정보로 정하고 t+1일 수익률에 적용한다.
실현변동성도 t일까지의 과거 창만 쓴다.

한계(결론에 병기):
- 지수 ETF 일봉 gross다. 수수료·슬리피지·세금 미반영 — 우리 net 판정 기준이 아니다.
- 변동성 타겟팅은 리밸런싱 빈도가 높아 비용에 민감한데 그 비용이 빠져 있다.
  따라서 '방어 효과가 실재하는가'까지만 답하고 채택 여부는 별도 판단이다.
- 코어 arm 구성 변경은 운영자 결정 영역이다.

  python tools/vol_target_core_sleeve_validation.py --years 5
"""

import argparse
from statistics import mean, pstdev

TICKERS = {
    "KR": ("069500.KS", "KODEX 200"),
    "US": ("QQQ", "QQQ"),
}


def main() -> int:
    ap = argparse.ArgumentParser(description="변동성 타겟팅 방어 효과 검증")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--sma", type=int, default=10)
    ap.add_argument("--vol-window", type=int, default=20, help="실현변동성 창(일)")
    ap.add_argument("--target-vol", type=float, default=12.0, help="목표 연율 변동성(%%)")
    args = ap.parse_args()

    import yfinance as yf

    for market, (symbol, label) in TICKERS.items():
        print(f"\n{'='*66}")
        print(f"[{market}] {label} ({symbol})  최근 {args.years}년 일봉")
        try:
            df = yf.download(symbol, period=f"{args.years}y", interval="1d",
                             progress=False, auto_adjust=True)
        except Exception as exc:
            print(f"  다운로드 실패: {exc}")
            continue
        if df is None or len(df) < 260:
            print("  표본 부족")
            continue
        close = df["Close"].iloc[:, 0] if hasattr(df["Close"], "columns") else df["Close"]
        px = [float(x) for x in close.tolist()]
        n = len(px)
        rets = [0.0] + [(px[i] / px[i - 1] - 1.0) for i in range(1, n)]

        sma_w, vol_w = args.sma, args.vol_window
        start = max(sma_w, vol_w) + 1

        curves = {"buy&hold": [1.0], "SMA10": [1.0], "SMA10+VolTarget": [1.0]}
        exposures: list[float] = []
        for i in range(start, n - 1):
            # ── t일까지의 정보로 포지션 결정 ──
            sma = mean(px[i - sma_w + 1: i + 1])
            in_trend = px[i] > sma
            window = rets[i - vol_w + 1: i + 1]
            realized = pstdev(window) * (252 ** 0.5) * 100.0 if len(window) > 1 else 0.0
            if realized > 0:
                scale = min(1.0, args.target_vol / realized)
            else:
                scale = 1.0
            # ── t+1일 수익률에 적용 ──
            r = rets[i + 1]
            curves["buy&hold"].append(curves["buy&hold"][-1] * (1 + r))
            curves["SMA10"].append(curves["SMA10"][-1] * (1 + (r if in_trend else 0.0)))
            expo = scale if in_trend else 0.0
            exposures.append(expo)
            curves["SMA10+VolTarget"].append(curves["SMA10+VolTarget"][-1] * (1 + r * expo))

        def stats(curve: list[float]) -> tuple[float, float, float]:
            total = (curve[-1] - 1.0) * 100.0
            step = [(curve[i] / curve[i - 1] - 1.0) for i in range(1, len(curve))]
            vol = pstdev(step) * (252 ** 0.5) * 100.0 if len(step) > 1 else 0.0
            peak, mdd = curve[0], 0.0
            for v in curve:
                peak = max(peak, v)
                mdd = min(mdd, v / peak - 1.0)
            return total, vol, mdd * 100.0

        print(f"  {'전략':>18} {'총수익':>9} {'연변동성':>9} {'MDD':>8} {'수익/MDD':>9}")
        for name, curve in curves.items():
            total, vol, mdd = stats(curve)
            ratio = (total / abs(mdd)) if mdd else float("nan")
            print(f"  {name:>18} {total:+8.2f}% {vol:8.2f}% {mdd:7.2f}% {ratio:8.2f}")
        if exposures:
            print(f"  평균 노출 {mean(exposures)*100:.1f}% "
                  f"(현금 구간 {sum(1 for e in exposures if e == 0)/len(exposures)*100:.0f}%)")

    print("\n한계: 지수 ETF 일봉 gross(수수료·슬리피지·세금 미반영). 변동성 타겟팅은")
    print("      리밸런싱이 잦아 비용에 민감한데 그 비용이 빠져 있다 — '방어가 실재하는가'")
    print("      까지만 답하며, 코어 arm 구성 변경은 운영자 결정 영역이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
