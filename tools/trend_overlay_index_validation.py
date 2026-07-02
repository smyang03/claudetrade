from __future__ import annotations
"""트렌드 방어 오버레이 — 표준 룰 검증 (수십 년 지수 역사).

스펙 1단계: Faber 10개월 SMA 룰(월말 종가 > 10mo SMA면 투자, 아니면 현금)이
정말 drawdown을 깎나? 우리 2달 봇 데이터가 아니라 SPY/QQQ/KOSPI 수십 년으로 sanity check.

룰: 월말 종가 vs 10개월 SMA. 다음달 포지션 = 위면 100% / 아래면 현금(0).
비교: buy&hold vs trend. 지표: CAGR, MaxDD, 연변동성, Sharpe(rf=0), 투자비중, 전환횟수.
비용: 전환 1회당 SWITCH_COST% 차감(ETF 저비용 가정).
read-only. 외부 지수 데이터(yfinance period=max).
"""
import sys
from statistics import mean, pstdev

SWITCH_COST = 0.1  # % per in/out switch


def analyze(sym, label):
    import yfinance as yf
    df = yf.download(sym, period="max", interval="1mo", progress=False, auto_adjust=True)
    if df is None or df.empty:
        print(f"{label} ({sym}): 데이터 없음"); return
    if df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    c = df["Close"].dropna()
    if len(c) < 130:
        print(f"{label}: 표본부족 {len(c)}개월"); return
    sma = c.rolling(10).mean()
    rets = c.pct_change()

    bh_eq, tr_eq = [1.0], [1.0]
    in_mkt = []
    switches = 0
    prev_pos = 0
    months = []
    for i in range(1, len(c)):
        date = c.index[i]
        if str(sma.index[i - 1]) == "NaT" or sma.iloc[i - 1] != sma.iloc[i - 1]:
            pos = 1  # SMA 없으면 투자(워밍업)
        else:
            pos = 1 if c.iloc[i - 1] > sma.iloc[i - 1] else 0  # 전월 신호(룩어헤드 없음)
        r = rets.iloc[i]
        if r != r:
            r = 0.0
        bh_eq.append(bh_eq[-1] * (1 + r))
        cost = (SWITCH_COST / 100.0) if pos != prev_pos else 0.0
        if pos != prev_pos:
            switches += 1
        tr_eq.append(tr_eq[-1] * (1 + (r if pos else 0.0) - cost))
        in_mkt.append(pos)
        prev_pos = pos
        months.append(date)

    def stats(eq, used_rets):
        yrs = len(eq) / 12.0
        cagr = (eq[-1]) ** (1 / yrs) - 1
        peak = -1e9; mdd = 0.0
        for v in eq:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        vol = pstdev(used_rets) * (12 ** 0.5) if len(used_rets) > 1 else 0
        sharpe = (mean(used_rets) * 12) / vol if vol else 0
        return cagr * 100, mdd * 100, vol * 100, sharpe

    bh_r = [rets.iloc[i] if rets.iloc[i] == rets.iloc[i] else 0.0 for i in range(1, len(c))]
    tr_r = [(bh_r[i] if in_mkt[i] else 0.0) for i in range(len(in_mkt))]
    bh = stats(bh_eq, bh_r)
    tr = stats(tr_eq, tr_r)
    yrs = len(c) / 12.0
    print(f"\n=== {label} ({sym}) — {len(c)}개월 ≈ {yrs:.0f}년 ===")
    print(f"  {'':12}{'CAGR':>8}{'MaxDD':>9}{'변동성':>8}{'Sharpe':>8}")
    print(f"  {'Buy&Hold':12}{bh[0]:>7.1f}%{bh[1]:>8.1f}%{bh[2]:>7.1f}%{bh[3]:>8.2f}")
    print(f"  {'Trend(10mo)':12}{tr[0]:>7.1f}%{tr[1]:>8.1f}%{tr[2]:>7.1f}%{tr[3]:>8.2f}")
    print(f"  투자비중 {mean(in_mkt)*100:.0f}%, 전환 {switches}회 ({switches/yrs:.1f}회/년)")
    dd_cut = bh[1] - tr[1]
    cagr_cost = bh[0] - tr[0]
    sharpe_gain = tr[3] - bh[3]
    print(f"  => MaxDD {dd_cut:+.1f}%p({'개선' if dd_cut>0 else '악화'}), "
          f"CAGR {-cagr_cost:+.1f}%p, Sharpe {sharpe_gain:+.2f} "
          f"({'유리' if sharpe_gain>0 else '불리'})")


def main():
    print("트렌드 방어 오버레이 표준 룰(Faber 10mo SMA) 검증 — 지수 역사")
    print(f"(전환비용 {SWITCH_COST}%/회 반영, 현금수익률 0 가정=보수적)")
    for sym, lab in [("SPY", "美 S&P500"), ("QQQ", "美 나스닥100"), ("^KS11", "韓 KOSPI")]:
        try:
            analyze(sym, lab)
        except Exception as e:
            print(f"{lab} 오류: {str(e)[:80]}")
    print("\n해석: MaxDD 크게↓ + Sharpe↑면 방어 오버레이 유효(risk-알파). "
          "CAGR는 약간↓ 정상(현금 구간 기회비용). Sharpe 0 이하 개선이면 폐기.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
