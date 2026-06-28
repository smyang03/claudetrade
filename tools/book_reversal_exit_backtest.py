from __future__ import annotations
"""북/국면 반전 디리스크 백테스트.

가설: QQQ가 세션 고점 대비 X% 꺾인 시점(T)에 그때 열려있던 포지션을 전부 청산하면,
오늘처럼 녹색 북이 시장 반전에 반납하는 걸 막아 net이 개선되나?

vs 현행: 개별 포지션이 각자 스톱/target까지 가는 것.

방법(read-only, yfinance 5분봉):
- v2_learning US 청산(최근 60일, entry_price/closed_at/pnl_pct/session_date)
- 세션별 QQQ 경로 → trigger T = QQQ가 running high 대비 X% 첫 이탈 시각
- 포지션이 T에 열려있었으면(closed_at >= T) → T 시점 종목가로 청산(counterfactual)
  아니면(이미 T 전에 청산=target 등) → 실제 유지
- 세션에 trigger 없으면 전부 실제 유지
- 비교: 실제 합 vs 북반전 합
주의: entry는 session_date 근사, '지수 천장 사후 인지' 가정(실시간 trigger는 X% 확인 후라 이미 반영).
"""
import argparse, sqlite3, sys, time
from datetime import timedelta
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]


def load_us(days):
    con = sqlite3.connect(str(ROOT / "data/ml/decisions.db"))
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT ticker,session_date,entry_price,closed_at,pnl_pct,close_reason "
        "FROM v2_learning_performance WHERE status='CLOSED' AND market='US' "
        "AND entry_price>0 AND closed_at IS NOT NULL AND pnl_pct IS NOT NULL "
        "AND closed_at >= date('now', ?) ORDER BY closed_at", (f'-{days} days',)).fetchall()]
    con.close()
    return rows


def fetch5m(sym):
    import yfinance as yf
    try:
        df = yf.download(sym, period="60d", interval="5m", progress=False, auto_adjust=False)
        if df is None or df.empty:
            return None
        if df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        import pandas as pd
        idx = df.index
        df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        return df
    except Exception:
        return None


def trigger_time(qqq, session_date, x_pct):
    import pandas as pd
    day = pd.Timestamp(session_date).date()
    sess = qqq[[d.date() == day for d in qqq.index]]
    if sess.empty:
        return None
    run_high = 0.0
    for ts, bar in sess.iterrows():
        run_high = max(run_high, float(bar["High"]))
        if run_high > 0 and float(bar["Close"]) <= run_high * (1 - x_pct / 100.0):
            return ts
    return None


def price_at(df, t):
    after = df[df.index >= t]
    if after.empty:
        return None
    return float(after.iloc[0]["Open"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=58)
    ap.add_argument("--xs", default="0.5,1.0,1.5")
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()
    import pandas as pd
    xs = [float(v) for v in args.xs.split(",")]

    rows = load_us(args.days)
    print(f"US 청산 {len(rows)}건 (최근 {args.days}일)")
    qqq = fetch5m("QQQ")
    if qqq is None:
        print("QQQ 데이터 실패"); return 1

    cache = {}
    for x in xs:
        # 세션별 trigger
        sessions = sorted({r["session_date"] for r in rows})
        trig = {s: trigger_time(qqq, s, x) for s in sessions}
        n_trig = sum(1 for v in trig.values() if v is not None)
        actual, book = [], []
        affected = 0
        sess_delta = {}
        for r in rows:
            a = r["pnl_pct"]
            T = trig.get(r["session_date"])
            closed = pd.Timestamp(r["closed_at"])
            closed = closed.tz_localize("UTC") if closed.tzinfo is None else closed.tz_convert("UTC")
            use = a
            if T is not None and closed >= T:
                tk = r["ticker"]
                if tk not in cache:
                    cache[tk] = fetch5m(tk); time.sleep(args.sleep)
                df = cache[tk]
                if df is not None:
                    p = price_at(df, T)
                    if p and r["entry_price"] > 0:
                        use = (p / r["entry_price"] - 1) * 100
                        affected += 1
            actual.append(a); book.append(use)
            sess_delta.setdefault(r["session_date"], 0.0)
            sess_delta[r["session_date"]] += (use - a)
        print(f"\n[X={x}%] 반전 trigger 발생 세션 {n_trig}/{len(sessions)}, "
              f"북청산으로 바뀐 포지션 {affected}/{len(rows)}")
        print(f"  실제   : 평균 {mean(actual):+.2f}%  합 {sum(actual):+.1f}%p")
        print(f"  북반전 : 평균 {mean(book):+.2f}%  합 {sum(book):+.1f}%p")
        print(f"  차이(북-실제): 합 {sum(book)-sum(actual):+.1f}%p  "
              f"({'개선' if sum(book)>sum(actual) else '악화'})")
        nz = {s: d for s, d in sess_delta.items() if abs(d) > 0.01}
        top = sorted(nz.items(), key=lambda kv: -abs(kv[1]))[:6]
        tot = sum(sess_delta.values())
        print(f"  세션 기여 상위(delta%p): " +
              " ".join(f"{s[5:]}:{d:+.1f}" for s, d in top))
        if top and tot != 0:
            print(f"  상위1세션 비중 {top[0][1]/tot*100:.0f}% / 상위2 "
                  f"{(top[0][1]+top[1][1])/tot*100:.0f}%" if len(top) > 1 else "")
    print("\n주: 비용 동일(둘 다 1왕복). entry=session_date 근사. 단일 스냅샷 청산가.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
