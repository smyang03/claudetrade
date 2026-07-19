"""경로유전자 '확인 출구' 규칙 시뮬레이션 — 외부 데이터. read-only.
규칙: 진입 후 d1에 확인(d1>0=녹색)이면 볼록 타깃까지 보유(d5), 미확인(d1<=0)이면 d1에 컷.
baseline=무조건 d5 보유. 비용 반영."""
import warnings
warnings.filterwarnings("ignore")
import yfinance as yf
import pandas as pd
import numpy as np

UNIV = ["AAPL","MSFT","GOOGL","AMZN","META","NVDA","TSLA","AMD","INTC","MU","QCOM","CSCO","ORCL","IBM",
        "JPM","BAC","WFC","GS","C","PYPL","JNJ","PFE","MRK","ABBV","GILD","MRNA","XOM","CVX","COP","OXY",
        "KO","PEP","WMT","COST","MCD","NKE","HD","DIS","F","GM","CAT","BA","GE","DAL","AAL","CCL","NCLH",
        "T","VZ","NFLX","ROKU","SNAP","UBER","PLUG","FCX","X","CLF","NEM","MARA","RIOT","COIN"]
px = yf.download(UNIV, start="2016-01-01", end="2026-07-01", progress=False, auto_adjust=True)["Close"]
px = px.dropna(axis=1, thresh=int(len(px) * 0.5))
COST = 0.30  # 왕복 비용 %/exit (보수적)
idx = list(px.index)

base = []; rule = []; cut_n = 0; hold_n = 0
for i in range(0, len(idx) - 6, 3):
    for tk in px.columns:
        p0 = px[tk].iloc[i]; p1 = px[tk].iloc[i + 1]; p5 = px[tk].iloc[i + 5]
        if not (p0 > 0 and p1 > 0 and p5 > 0):
            continue
        d1 = (p1 / p0 - 1) * 100
        d5 = (p5 / p0 - 1) * 100
        base.append(d5 - COST)  # baseline: 무조건 d5 보유
        if d1 > 0:  # 확인 → 보유
            rule.append(d5 - COST); hold_n += 1
        else:       # 미확인 → d1 컷
            rule.append(d1 - COST); cut_n += 1

b = np.array(base); r = np.array(rule)
print(f"진입 시뮬 n={len(b)} (보유 {hold_n} / 컷 {cut_n})\n")
print("=== baseline (무조건 5일 보유) vs 확인출구 규칙 ===")
print(f"  baseline : 평균 {b.mean():+.3f}%  중앙 {np.median(b):+.3f}%  승률 {100*(b>0).mean():.0f}%  표준편차 {b.std():.2f}%")
print(f"  확인출구 : 평균 {r.mean():+.3f}%  중앙 {np.median(r):+.3f}%  승률 {100*(r>0).mean():.0f}%  표준편차 {r.std():.2f}%")
print(f"  개선: 평균 {r.mean()-b.mean():+.3f}%p/건, Sharpe {b.mean()/b.std():+.3f}→{r.mean()/r.std():+.3f}")
# 하방 보호(꼬리)
print(f"  하위5% 평균손실: baseline {np.percentile(b,5):.2f}% → 규칙 {np.percentile(r,5):.2f}% (꼬리 방어)")
print(f"  최악(min): baseline {b.min():.1f}% → 규칙 {r.min():.1f}%")
print()
print("  판정: 확인출구가 평균·Sharpe·하방 모두 개선하면 = 경로유전자 규칙 실익(시뮬 지지)")

# 강건성: 확인 임계 민감도
print("\n=== 확인 임계 민감도 (d1 컷오프) ===")
for thr in [-0.5, 0.0, 0.5, 1.0]:
    rr = []
    for i in range(0, len(idx) - 6, 3):
        for tk in px.columns:
            p0 = px[tk].iloc[i]; p1 = px[tk].iloc[i + 1]; p5 = px[tk].iloc[i + 5]
            if not (p0 > 0 and p1 > 0 and p5 > 0): continue
            d1 = (p1/p0-1)*100; d5 = (p5/p0-1)*100
            rr.append((d5 if d1 > thr else d1) - COST)
    rr = np.array(rr)
    print(f"  d1>{thr:+.1f}% 확인: 평균 {rr.mean():+.3f}% 승률 {100*(rr>0).mean():.0f}% Sharpe {rr.mean()/rr.std():+.3f}")
