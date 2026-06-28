from __future__ import annotations
"""다이아몬드 핸드 반사실 — 전 기간 PathB 청산 전수.

운영자 규칙: Claude sell_target 가격 닿으면 거기서 매도. 안 닿으면 지금까지 계속 보유.
손절(loss_cap/hard_stop)·강제청산(pre_close)·Claude stop 전부 무시. 손실 나도 안고 간다.

데이터: v2_learning_performance(entry_price,close_reason,pnl_pct) +
        v2_path_runs.plan_json(sell_target) + yfinance 1h forward(entry session_date→now).
판정: forward max(High) >= sell_target → realize (target/entry-1). else → 현재가 보유.

read-only. 비교: 반사실 vs 실제 실현(pnl_pct).
주의: yfinance split 보정으로 분할종목(특히 KR) 오염 가능 → US 우선, KR 별도 caveat.
"""
import argparse, json, sqlite3, sys, time
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]


def load_positions(market):
    con = sqlite3.connect(str(ROOT / "data/ml/decisions.db"))
    con.row_factory = sqlite3.Row
    q = ("SELECT market,ticker,session_date,entry_price,exit_price,pnl_pct,close_reason "
         "FROM v2_learning_performance WHERE status='CLOSED' "
         "AND entry_price IS NOT NULL AND entry_price>0")
    args = []
    if market:
        q += " AND market=?"; args.append(market)
    rows = [dict(r) for r in con.execute(q, args).fetchall()]
    con.close()
    # sell_target join
    con2 = sqlite3.connect(str(ROOT / "data/v2_event_store.db"))
    con2.row_factory = sqlite3.Row
    tgt = {}
    for r in con2.execute("SELECT market,ticker,session_date,plan_json FROM v2_path_runs"):
        try:
            j = json.loads(r["plan_json"])
            st = j.get("sell_target")
            if st:
                tgt[(r["market"], r["ticker"], r["session_date"])] = float(st)
        except Exception:
            pass
    con2.close()
    for p in rows:
        p["sell_target"] = tgt.get((p["market"], p["ticker"], p["session_date"]))
    return rows


def yf_symbol(market, ticker):
    return [f"{ticker}.KS", f"{ticker}.KQ"] if market == "KR" else [ticker]


def fetch_1h(sym):
    import yfinance as yf
    try:
        df = yf.download(sym, period="730d", interval="1h",
                         progress=False, auto_adjust=False)
        if df is None or df.empty:
            return None
        if df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["US", "KR"], default="US")
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    import pandas as pd
    pos = load_positions(args.market)
    with_tgt = [p for p in pos if p["sell_target"]]
    print(f"{args.market} CLOSED entry_price 보유 {len(pos)}건, sell_target 매칭 {len(with_tgt)}건")

    cache = {}
    res = []
    no_data = 0
    for p in with_tgt:
        tk = p["ticker"]
        if tk not in cache:
            df = None
            for sym in yf_symbol(args.market, tk):
                df = fetch_1h(sym)
                if df is not None:
                    break
            cache[tk] = df
            time.sleep(args.sleep)
        df = cache[tk]
        if df is None:
            no_data += 1
            continue
        idx = df.index
        idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        d = df.copy(); d.index = idx
        start = pd.Timestamp(str(p["session_date"]) + " 00:00:00").tz_localize("UTC")
        fwd = d[d.index >= start]
        if fwd.empty:
            no_data += 1
            continue
        entry = float(p["entry_price"]); target = float(p["sell_target"])
        hit = float(fwd["High"].max()) >= target
        if hit:
            cf = (target / entry - 1) * 100
            outcome = "target_hit"
        else:
            cf = (float(fwd["Close"].iloc[-1]) / entry - 1) * 100
            outcome = "still_holding"
        res.append({"tk": tk, "actual": p["pnl_pct"] or 0.0, "cf": cf,
                    "outcome": outcome, "reason": p["close_reason"]})

    if not res:
        print("매칭 결과 없음"); return 0
    n = len(res)
    hits = [r for r in res if r["outcome"] == "target_hit"]
    holds = [r for r in res if r["outcome"] == "still_holding"]
    act = [r["actual"] for r in res]
    cf = [r["cf"] for r in res]
    print(f"\n=== 다이아몬드 핸드 (target 닿으면 매도, 아니면 보유) — n={n} (데이터없음 {no_data}) ===")
    print(f"  target 도달(매도): {len(hits)}/{n} ({len(hits)/n*100:.0f}%)  "
          f"여전히 보유중: {len(holds)}/{n}")
    print(f"\n  실제 실현    : 평균 {mean(act):+.2f}%  중앙 {median(act):+.2f}%  합 {sum(act):+.1f}%p")
    print(f"  다이아몬드핸드: 평균 {mean(cf):+.2f}%  중앙 {median(cf):+.2f}%  합 {sum(cf):+.1f}%p")
    print(f"  차이(다이아-실제): 평균 {mean(cf)-mean(act):+.2f}%p  합 {sum(cf)-sum(act):+.1f}%p")
    if holds:
        hv = [r["cf"] for r in holds]
        print(f"\n  '여전히 보유중'(target 못 닿음) {len(holds)}건: "
              f"평균 {mean(hv):+.2f}%  중앙 {median(hv):+.2f}%  "
              f"+ {sum(1 for x in hv if x>0)}/{len(hv)}")
        print(f"    => 이게 핵심: 손절 안 하고 지금 안고 있는 미실현 손익")
    # 실제 손절건이 다이아핸드면 어찌됐나
    stopped = [r for r in res if r["reason"] in ("CLOSED_LOSS_CAP", "CLOSED_HARD_STOP")]
    if stopped:
        sa = [r["actual"] for r in stopped]; sc = [r["cf"] for r in stopped]
        print(f"\n  [실제 기계손절 {len(stopped)}건] 실제 {mean(sa):+.2f}% -> 다이아핸드 {mean(sc):+.2f}% "
              f"(target도달 {sum(1 for r in stopped if r['outcome']=='target_hit')}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
