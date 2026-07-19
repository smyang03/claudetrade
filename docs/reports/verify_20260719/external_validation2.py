"""교정 검증 — 우리 실제 행태·호라이즌에 맞춘 외부 테스트.
T1b: 스파이크 후 단기(1/3/5일) 반전 — 우리는 급등종목을 수일 보유(우리 발견의 정확한 외부테스트)
T2b: 저변동 위험조정(Sharpe·downside) — 저변동 클레임은 원수익 아닌 위험조정
유니버스에 소·중형 추가로 대형생존편향 완화."""
import warnings
warnings.filterwarnings("ignore")
import yfinance as yf
import pandas as pd
import numpy as np

# 대형 + 중소형/변동성 큰 이름 섞기(생존편향·대형편중 완화)
UNIV = [
    "AAPL","MSFT","GOOGL","AMZN","META","NVDA","TSLA","AMD","INTC","MU","QCOM","CSCO","ORCL","IBM",
    "JPM","BAC","WFC","GS","C","AXP","SCHW","PYPL",
    "JNJ","PFE","MRK","ABBV","GILD","CVS","BMY","MRNA","BIIB",
    "XOM","CVX","COP","SLB","OXY","HAL","DVN","MRO",
    "KO","PEP","WMT","COST","MCD","NKE","SBUX","HD","LOW","TGT","DIS","F","GM",
    "CAT","BA","GE","DAL","AAL","UAL","CCL","NCLH","RCL",
    "T","VZ","NFLX","ROKU","SNAP","PINS","UBER","LYFT","PLUG","FCX","X","CLF","NEM",
]
print(f"유니버스 {len(UNIV)}종목(대형+중소형 혼합), 2014~2026")
raw = yf.download(UNIV, start="2014-01-01", end="2026-07-01", progress=False, auto_adjust=True)
px = raw["Close"].dropna(how="all")
px = px.dropna(axis=1, thresh=int(len(px) * 0.5))
ret = px.pct_change()
print(f"유효 {px.shape[1]}종목, {px.shape[0]}일\n")

print(">>> T1b: 스파이크 후 단기 반전 (급등 다음날 사서 수일 보유 = 우리 행태)")
print("    조건: 일간수익 > 임계 → 그 다음날 진입 → 1/3/5거래일 후 수익")
for thr in [5.0, 8.0, 12.0]:
    fwd = {1: [], 3: [], 5: []}
    for tk in px.columns:
        r = ret[tk].dropna()
        p = px[tk]
        idx = list(r.index)
        rv = r.values
        for i in range(len(idx) - 6):
            if rv[i] * 100.0 > thr:  # 스파이크 당일
                # 다음날 종가 진입 가정
                base_pos = px.index.get_loc(idx[i]) + 1
                if base_pos + 5 >= len(px.index):
                    continue
                p0 = p.iloc[base_pos]
                if not (p0 > 0):
                    continue
                for h in (1, 3, 5):
                    ph = p.iloc[base_pos + h]
                    if ph > 0:
                        fwd[h].append((ph / p0 - 1.0) * 100.0)
    n = len(fwd[5])
    if n:
        print(f"  스파이크>{thr}%: n={n} | 1일 {np.mean(fwd[1]):+.3f}% | 3일 {np.mean(fwd[3]):+.3f}% | 5일 {np.mean(fwd[5]):+.3f}% (승률5일 {100*np.mean([x>0 for x in fwd[5]]):.0f}%)")
# 무조건부 벤치마크(아무 날 진입 5일 보유)
allf = []
for tk in px.columns:
    p = px[tk].dropna().values
    for i in range(len(p) - 6):
        if p[i] > 0:
            allf.append((p[i + 5] / p[i] - 1.0) * 100.0)
print(f"  [벤치] 무조건부 5일보유 평균 {np.mean(allf):+.3f}% (승률 {100*np.mean([x>0 for x in allf]):.0f}%)")
print("  → 스파이크 후 5일이 무조건부보다 낮으면 = '급등 추격' 단기 불리(우리 발견 외부확인)\n")

print(">>> T2b: 저변동 위험조정 (월간 forward의 Sharpe·downside, Q1=低vol)")
dates = list(px.resample("M").last().index)
obs = []
for i in range(12, len(dates) - 1):
    d0, d1 = dates[i], dates[i + 1]
    win = ret.loc[:d0].tail(21)
    for tk in px.columns:
        if win[tk].notna().sum() < 10:
            continue
        vol = win[tk].std() * 100.0
        p0, p1 = px[tk].asof(d0), px[tk].asof(d1)
        if vol and p0 > 0 and p1 > 0:
            obs.append((vol, (p1 / p0 - 1.0) * 100.0))
df = pd.DataFrame(obs, columns=["vol", "fwd"]).dropna()
df["q"] = pd.qcut(df["vol"], 5, labels=[1, 2, 3, 4, 5])
print(f"  n={len(df)}")
for q, gg in df.groupby("q", observed=True):
    m, s = gg["fwd"].mean(), gg["fwd"].std()
    dn = gg[gg["fwd"] < 0]["fwd"].mean()
    sharpe = m / s if s else 0
    print(f"  Q{q}(vol{'低' if q==1 else '高' if q==5 else '중'}): 평균 {m:+.2f}% 표준편차 {s:.2f}% Sharpe {sharpe:+.3f} 평균손실 {dn:.2f}%")
print("  → Q1(低vol) Sharpe·평균손실이 우수하면 = 저변동은 '덜 잃는' 위험조정 이점(원수익 아님)")
