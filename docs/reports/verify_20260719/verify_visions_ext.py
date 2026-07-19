"""② 경로유전자 ④ 너비원장 — 외부 데이터 검증. read-only."""
import warnings
warnings.filterwarnings("ignore")
import yfinance as yf
import pandas as pd
import numpy as np

UNIV = ["AAPL","MSFT","GOOGL","AMZN","META","NVDA","TSLA","AMD","INTC","MU","QCOM","CSCO","ORCL","IBM",
        "JPM","BAC","WFC","GS","C","PYPL","JNJ","PFE","MRK","ABBV","GILD","MRNA","XOM","CVX","COP","OXY",
        "KO","PEP","WMT","COST","MCD","NKE","HD","DIS","F","GM","CAT","BA","GE","DAL","AAL","CCL","NCLH",
        "T","VZ","NFLX","ROKU","SNAP","UBER","PLUG","FCX","X","CLF","NEM","MARA","RIOT","COIN"]
print(f"외부 유니버스 {len(UNIV)}, 2016~2026 fetch...")
px = yf.download(UNIV, start="2016-01-01", end="2026-07-01", progress=False, auto_adjust=True)["Close"]
px = px.dropna(axis=1, thresh=int(len(px) * 0.5))
ret = px.pct_change()
print(f"유효 {px.shape[1]}종목 {px.shape[0]}일\n")

# ② 경로유전자: 진입 다음날(d1) 행동이 d5 운명을 예고하는가
print("=== ② 경로 유전자 — 초기(d1) 경로가 최종(d5) 운명을 가르는가 ===")
# 무작위 진입 시뮬: 매 20거래일마다 전 종목 진입, d1/d5 관측
d1d5 = []
idx = list(px.index)
for i in range(0, len(idx) - 6, 5):
    for tk in px.columns:
        p0 = px[tk].iloc[i]; p1 = px[tk].iloc[i + 1]; p5 = px[tk].iloc[i + 5]
        if p0 > 0 and p1 > 0 and p5 > 0:
            d1d5.append(((p1 / p0 - 1) * 100, (p5 / p0 - 1) * 100))
df = pd.DataFrame(d1d5, columns=["d1", "d5"])
print(f"  진입 시뮬 n={len(df)}")
# d1 부호별 d5 결과
for lbl, cond in [("d1 녹색(>+1%)", df.d1 > 1), ("d1 중립(-1~1%)", (df.d1 >= -1) & (df.d1 <= 1)), ("d1 적색(<-1%)", df.d1 < -1)]:
    g = df[cond]
    if len(g):
        wr = 100 * (g.d5 > 0).mean()
        print(f"     {lbl:16} n={len(g):>6} → d5 평균 {g.d5.mean():+.3f}% 승률 {wr:.0f}%")
corr = df.d1.corr(df.d5)
print(f"  d1↔d5 상관 r={corr:+.3f}")
print(f"  판정: d1 녹색이 d5 승률 크게 높이면 = 초기경로가 운명 예고(경로유전자 실체)\n")

# ④ 너비원장: 미묘한 신호가 n작으면 안보이고 n크면 보이는가 (통계력 실증)
print("=== ④ 너비 원장 — 표본 너비가 통계력을 만드는가 (같은 신호, n별 유의성) ===")
# 신호: 중간 변동성 우위(우리 ⑥에서 본 sweet spot) — 외부에서 vol분위별 d5
sig = []
for i in range(21, len(idx) - 6, 5):
    for tk in px.columns:
        w = ret[tk].iloc[i - 21:i].dropna()
        if len(w) < 10: continue
        vol = w.std() * 100
        p0 = px[tk].iloc[i]; p5 = px[tk].iloc[i + 5]
        if p0 > 0 and p5 > 0:
            sig.append((vol, (p5 / p0 - 1) * 100))
sg = pd.DataFrame(sig, columns=["vol", "fwd"]).dropna()
sg["q"] = pd.qcut(sg["vol"], 3, labels=["低", "중", "高"])
mid = sg[sg.q == "중"]["fwd"]; hi = sg[sg.q == "高"]["fwd"]
diff = mid.mean() - hi.mean()
# 통계력: 같은 효과크기를 n=100(우리규모) vs n=full 로 t검정
import statistics
def tstat(a, b):
    ma, mb = a.mean(), b.mean(); va, vb = a.var(), b.var()
    se = (va/len(a) + vb/len(b)) ** 0.5
    return (ma - mb)/se if se else 0
print(f"  전체 외부: 중vol {mid.mean():+.3f}% vs 高vol {hi.mean():+.3f}% (차이 {diff:+.3f}%p)")
print(f"  n=full({len(mid)},{len(hi)}) t={tstat(mid,hi):+.1f}")
# 우리 규모(n~100)로 서브샘플 t
sub_t = []
for seed in range(30):
    ma = mid.sample(min(100, len(mid)), random_state=seed)
    hb = hi.sample(min(100, len(hi)), random_state=seed + 999)
    sub_t.append(abs(tstat(ma, hb)))
print(f"  n=100(우리규모) 서브샘플 30회 평균 |t|={sum(sub_t)/len(sub_t):.2f} (|t|>2 비율 {100*sum(1 for x in sub_t if x>2)/len(sub_t):.0f}%)")
print(f"  판정: full에선 유의(t>2)한데 n=100에선 자주 놓치면 = 너비가 통계력 만듦(비전 실체)")
