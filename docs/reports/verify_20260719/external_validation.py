"""외부 데이터(yfinance)로 4개 알파 레버 out-of-sample 검증. 분석 전용.
T1 MAX회피, T2 저변동, T3 퀄리티(프록시·한계명시), T4 turn-of-month.
주의: 유니버스는 현존 종목이라 생존편향 있음(횡단면 상대비교라 부분완화). 명시."""
import warnings
warnings.filterwarnings("ignore")
import yfinance as yf
import pandas as pd
import numpy as np

# 섹터 분산 US 대형·중형 유니버스 (생존편향 caveat)
UNIV = [
    "AAPL","MSFT","GOOGL","AMZN","META","NVDA","TSLA","AVGO","ORCL","CRM","ADBE","AMD","INTC","CSCO","QCOM","TXN","IBM","MU",
    "JPM","BAC","WFC","GS","MS","C","AXP","BLK","SCHW",
    "JNJ","UNH","PFE","MRK","ABBV","TMO","ABT","LLY","BMY","AMGN","GILD","CVS",
    "XOM","CVX","COP","SLB","EOG",
    "PG","KO","PEP","WMT","COST","MCD","NKE","SBUX","HD","LOW","TGT","DIS",
    "CAT","BA","GE","HON","UPS","LMT","MMM","DE",
    "V","MA","PYPL","NFLX","T","VZ","CMCSA","F","GM",
]
print(f"유니버스 {len(UNIV)}종목, 2014-01~2026-06")
raw = yf.download(UNIV, start="2014-01-01", end="2026-07-01", progress=False, auto_adjust=True)
px = raw["Close"].dropna(how="all")
px = px.dropna(axis=1, thresh=int(len(px) * 0.7))  # 70%+ 커버 종목만
print(f"유효 종목 {px.shape[1]}, 거래일 {px.shape[0]}\n")
ret = px.pct_change()

# 월말 인덱스
me = px.resample("M").last().index


def monthly_panel(signal_fn):
    """각 월말 signal(종목별) + 익월 총수익. (signal, fwd_ret) 관측 리스트 반환."""
    obs = []
    dates = list(px.resample("M").last().index)
    for i in range(12, len(dates) - 1):
        d0, d1 = dates[i], dates[i + 1]
        win = ret.loc[:d0].tail(21)
        for tk in px.columns:
            sig = signal_fn(tk, d0, win)
            if sig is None or np.isnan(sig):
                continue
            p0 = px[tk].asof(d0)
            p1 = px[tk].asof(d1)
            if not (p0 > 0) or not (p1 > 0):
                continue
            fwd = (p1 / p0 - 1.0) * 100.0
            obs.append((sig, fwd))
    return obs


def quintile_report(obs, label, ascending_good=True):
    df = pd.DataFrame(obs, columns=["sig", "fwd"]).dropna()
    df["q"] = pd.qcut(df["sig"], 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
    print(f"=== {label} — 익월 수익 by signal 5분위 (n={len(df)}) ===")
    g = df.groupby("q", observed=True)["fwd"].agg(["mean", "median", "count"])
    for q, row in g.iterrows():
        print(f"  Q{q}: 평균 {row['mean']:+.3f}%/월  중앙 {row['median']:+.3f}%  n={int(row['count'])}")
    lo, hi = g.loc[g.index.min(), "mean"], g.loc[g.index.max(), "mean"]
    print(f"  Q5-Q1 스프레드: {hi - lo:+.3f}%/월\n")
    return g


# T1: MAX = 최근 21일 최대 일간수익
print(">>> T1: MAX/로또주 회피 (高MAX가 언더퍼폼하는가)")
obs = monthly_panel(lambda tk, d0, win: win[tk].max() * 100.0 if win[tk].notna().sum() >= 10 else None)
quintile_report(obs, "MAX (Q5=高MAX 로또주)")
print("  → Q5(高MAX)가 Q1보다 낮으면 = 회피 스크린 유효(문헌 지지)\n")

# T2: 저변동 = 최근 21일 일간수익 표준편차 (낮을수록 좋다는 가설)
print(">>> T2: 저변동성 틸트 (低vol이 아웃퍼폼/우수 위험조정)")
obs = monthly_panel(lambda tk, d0, win: win[tk].std() * 100.0 if win[tk].notna().sum() >= 10 else None)
g = quintile_report(obs, "변동성 (Q1=低vol Q5=高vol)")
print("  → Q1(低vol)이 Q5 이상이면 = 저변동 이상현상 존재\n")

# T3: 퀄리티 프록시 — 현재 grossProfits/totalAssets 정적 정렬 (★look-ahead·생존편향, 한계명시)
print(">>> T3: 퀄리티 프록시 (★한계: 현재 펀더멘털 정적사용 = point-in-time 아님)")
try:
    gp = {}
    for tk in list(px.columns)[:80]:
        try:
            info = yf.Ticker(tk).info
            g_ = info.get("grossProfits")
            a_ = info.get("totalAssets")
            if g_ and a_ and a_ > 0:
                gp[tk] = g_ / a_
        except Exception:
            pass
    if len(gp) >= 15:
        s = pd.Series(gp)
        hi_q = s[s >= s.quantile(0.66)].index
        lo_q = s[s <= s.quantile(0.34)].index
        full = (px.iloc[-1] / px.iloc[0] - 1.0) * 100.0
        print(f"  현재 GP/Assets 상위(고퀄) 전기간수익 중앙 {full[hi_q].median():.1f}% vs 하위 {full[lo_q].median():.1f}% (n_hi={len(hi_q)},n_lo={len(lo_q)})")
        print("  ★이 테스트는 look-ahead(현재 퀄리티로 과거설명)라 방향 참고만. 정식검증=point-in-time 펀더멘털 필요(무료 부재)")
    else:
        print("  펀더멘털 커버 부족 — 스킵")
except Exception as e:
    print(f"  퀄리티 테스트 실패: {str(e)[:100]}")
print()

# T4: turn-of-month (지수)
print(">>> T4: Turn-of-Month 지수 효과 (월말1일+월초3일 vs 나머지)")
for idx, name in [("^GSPC", "S&P500"), ("^KS11", "KOSPI")]:
    try:
        ip = yf.download(idx, start="2005-01-01", end="2026-07-01", progress=False, auto_adjust=True)["Close"].dropna()
        if isinstance(ip, pd.DataFrame):
            ip = ip.iloc[:, 0]
        r = ip.pct_change().dropna() * 100.0
        dfm = pd.DataFrame({"r": r})
        dfm["ym"] = dfm.index.to_period("M")
        # 각 월의 마지막 거래일 + 다음 월 첫 3거래일 = turn window
        tom_mask = pd.Series(False, index=dfm.index)
        days = list(dfm.index)
        for j in range(len(days) - 1):
            if days[j].month != days[j + 1].month:  # 월말
                tom_mask.iloc[j] = True
                for k in range(1, 4):
                    if j + k < len(days):
                        tom_mask.iloc[j + k] = True
        tom = dfm.loc[tom_mask, "r"]
        rest = dfm.loc[~tom_mask, "r"]
        wr = 100.0 * (tom > 0).mean()
        print(f"  {name} ({ip.index.min().date()}~): TOM 평균 {tom.mean():+.4f}%/일 (승률 {wr:.0f}%, n={len(tom)}) vs 나머지 {rest.mean():+.4f}%/일 (n={len(rest)})")
    except Exception as e:
        print(f"  {name} 실패: {str(e)[:80]}")
print("  → TOM 평균이 나머지보다 크게 높으면 = 효과 존재(저비용 오버레이 근거)")
