from __future__ import annotations
"""시장상태별 노출 백테스트 — 다양한 ex-ante 신호 × 규칙.

운영자: "장 나빠지면 줄여" 의 살아있는 버전 = 일/국면 스케일. 다양한 조건 비교.

핵심 질문:
1) 지속성: 나쁜 시장 신호(전일 하락/MA아래/연속하락) 뜬 세션의 포지션이 실제로 더 나쁜가?
2) 규칙: 그 신호 뜬 날 '신규 스킵/절반/청산'하면 net 개선되나?
3) 과적합 경계: 여러 신호 중 일관된 방향인가, 한두 개만 우연히 좋은가?

데이터: v2_learning US 청산(pnl_pct) + QQQ 일봉(ex-ante 신호는 세션 시작 시 known).
비용: 왕복 0.5% 가정(net = gross - 0.5). 스킵은 그 포지션 net 제거.
read-only.
"""
import sqlite3, sys
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
COST = 0.5  # 왕복 % 가정


def load_us():
    con = sqlite3.connect(str(ROOT / "data/ml/decisions.db"))
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT ticker,session_date,pnl_pct,close_reason FROM v2_learning_performance "
        "WHERE status='CLOSED' AND market='US' AND pnl_pct IS NOT NULL "
        "AND session_date IS NOT NULL ORDER BY session_date").fetchall()]
    con.close()
    return rows


def qqq_features():
    import yfinance as yf
    df = yf.download("QQQ", period="1y", interval="1d", progress=False, auto_adjust=False)
    if df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    df = df.copy()
    df["ret1"] = df["Close"].pct_change() * 100
    df["ma10"] = df["Close"].rolling(10).mean()
    df["ma20"] = df["Close"].rolling(20).mean()
    df["ma50"] = df["Close"].rolling(50).mean()
    df["ret5"] = df["Close"].pct_change(5) * 100
    feat = {}
    dates = list(df.index)
    for i, ts in enumerate(dates):
        d = ts.strftime("%Y-%m-%d")
        row = df.iloc[i]
        prior = df.iloc[i - 1] if i >= 1 else None
        # 연속 하락(전일까지)
        consec = 0
        j = i - 1
        while j >= 1 and (df["Close"].iloc[j] < df["Close"].iloc[j - 1]):
            consec += 1; j -= 1
        feat[d] = {
            "ret1": float(row["ret1"]) if row["ret1"] == row["ret1"] else 0.0,  # 당일(지속성 진단용)
            "prior_ret1": float(prior["ret1"]) if prior is not None and prior["ret1"] == prior["ret1"] else 0.0,
            "below_ma20": bool(row["Close"] < row["ma20"]) if row["ma20"] == row["ma20"] else False,
            "below_ma50": bool(row["Close"] < row["ma50"]) if row["ma50"] == row["ma50"] else False,
            "below_ma10": bool(row["Close"] < row["ma10"]) if row["ma10"] == row["ma10"] else False,
            "prior_down": bool(prior is not None and prior["ret1"] < 0),
            "consec_down2": consec >= 2,
            "ret5_neg": bool(row["ret5"] < 0) if row["ret5"] == row["ret5"] else False,
        }
    return feat


def split_report(rows, feat, key, label):
    on = [r for r in rows if feat.get(r["session_date"], {}).get(key)]
    off = [r for r in rows if r["session_date"] in feat and not feat[r["session_date"]].get(key)]
    if not on or not off:
        print(f"  {label:22} (표본부족 on={len(on)} off={len(off)})"); return
    on_net = [r["pnl_pct"] - COST for r in on]
    off_net = [r["pnl_pct"] - COST for r in off]
    print(f"  {label:22} ON n={len(on):>3} net평균{mean(on_net):+.2f}% 합{sum(on_net):+6.0f} | "
          f"OFF n={len(off):>3} net평균{mean(off_net):+.2f}% | "
          f"스킵ON시 합{sum(off_net):+6.0f}(현행{sum(on_net)+sum(off_net):+.0f})")


def main():
    rows = load_us()
    feat = qqq_features()
    matched = [r for r in rows if r["session_date"] in feat]
    print(f"US 청산 {len(rows)}건, QQQ 일봉 매칭 {len(matched)}건")
    base_net = sum(r["pnl_pct"] - COST for r in matched)
    print(f"현행 전체 net 합(비용 {COST}% 반영): {base_net:+.1f}%p  "
          f"평균 {mean([r['pnl_pct']-COST for r in matched]):+.2f}%\n")

    print("=== [1] ex-ante 신호별: 신호 ON 세션 포지션이 더 나쁜가? + 스킵 효과 ===")
    print("    (스킵ON시 합 > 현행 이면 그 신호로 줄이는 게 이득)")
    for key, lab in [("prior_down", "전일 하락"), ("below_ma10", "QQQ<MA10"),
                     ("below_ma20", "QQQ<MA20"), ("below_ma50", "QQQ<MA50"),
                     ("consec_down2", "2일+ 연속하락"), ("ret5_neg", "5일 수익률<0")]:
        split_report(matched, feat, key, lab)

    print("\n=== [2] 지속성 진단: 전일 하락 → 당일 QQQ 평균 수익률 ===")
    pd_on = [feat[r["session_date"]]["ret1"] for r in matched if feat[r["session_date"]]["prior_down"]]
    pd_off = [feat[r["session_date"]]["ret1"] for r in matched if not feat[r["session_date"]]["prior_down"]]
    if pd_on and pd_off:
        print(f"  전일 하락 후 당일 QQQ: 평균 {mean(pd_on):+.2f}% (n={len(pd_on)})")
        print(f"  전일 상승 후 당일 QQQ: 평균 {mean(pd_off):+.2f}% (n={len(pd_off)})")
        print(f"  => 전일하락 후가 더 {'나쁨(지속=레버유효)' if mean(pd_on)<mean(pd_off) else '좋음(되돌림=휩쏘)'}")

    print("\n=== [3] 당일 QQQ 방향과 포지션 net (베타 확인) ===")
    up = [r["pnl_pct"]-COST for r in matched if feat[r["session_date"]]["ret1"] > 0]
    dn = [r["pnl_pct"]-COST for r in matched if feat[r["session_date"]]["ret1"] <= 0]
    if up and dn:
        print(f"  QQQ 상승일 포지션 net 평균 {mean(up):+.2f}% (n={len(up)})")
        print(f"  QQQ 하락일 포지션 net 평균 {mean(dn):+.2f}% (n={len(dn)})  <- 차이=베타 노출")

    print("\n=== [4] 월별 robustness: QQQ<MA20 효과가 단일월(6월 하락장)뿐인가? ===")
    from collections import defaultdict
    by_m = defaultdict(lambda: {"on": [], "off": []})
    for r in matched:
        f = feat[r["session_date"]]
        m = r["session_date"][:7]
        by_m[m]["on" if f["below_ma20"] else "off"].append(r["pnl_pct"] - COST)
    for m in sorted(by_m):
        on, off = by_m[m]["on"], by_m[m]["off"]
        on_s = f"ON n={len(on):>3} 평균{mean(on):+.2f}%" if on else "ON n=  0"
        off_s = f"OFF n={len(off):>3} 평균{mean(off):+.2f}%" if off else "OFF n=  0"
        verd = ""
        if on and off:
            verd = " <= ON이 더나쁨(일관)" if mean(on) < mean(off) else " <= 역전!"
        print(f"  {m}: {on_s} | {off_s}{verd}")

    print("\n주: 과적합 경계 — 여러 신호가 같은 방향이어야 신뢰. 한두 개만 좋으면 우연.")
    print("    당일 QQQ 방향/수익률은 ex-ante 아님(진단용). 신호는 전일·MA만 실거래 가능.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
