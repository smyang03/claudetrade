# LOSS_CAP 진입선별 v2 — 가격 CSV에서 진입시점 피처 직접 계산(no-lookahead: session_date 이전 봉만)
from __future__ import annotations

import csv
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"E:\code\claudetrade")


def load_series(market: str, ticker: str):
    sub = "us" if market == "US" else "kr"
    p = ROOT / "data" / "price" / sub / f"{sub}_{ticker}.csv"
    if not p.exists():
        return []
    out = []
    try:
        with p.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                try:
                    out.append((str(row["date"]).strip(), float(row["open"]), float(row["high"]),
                                float(row["low"]), float(row["close"])))
                except Exception:
                    continue
    except OSError:
        return []
    out.sort(key=lambda r: r[0])
    return out


def entry_features(series, session_date: str, entry_price: float):
    hist = [r for r in series if r[0] < session_date]
    if len(hist) < 22:
        return None
    closes = [r[4] for r in hist]
    highs = [r[2] for r in hist]
    last21 = hist[-21:]
    rets = []
    for i in range(1, len(last21)):
        pc = last21[i - 1][4]
        if pc > 0:
            rets.append((last21[i][4] - pc) / pc * 100)
    prev_close = closes[-1]
    f = {
        "ret_1d": (closes[-1] - closes[-2]) / closes[-2] * 100 if closes[-2] > 0 else None,
        "ret_5d": (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 and closes[-6] > 0 else None,
        "ret_21d": (closes[-1] - closes[-22]) / closes[-22] * 100 if len(closes) >= 22 and closes[-22] > 0 else None,
        "max_daily_ret_21d": max(rets) if rets else None,
        "vol_21d": statistics.pstdev(rets) if len(rets) > 2 else None,
        "dist_20d_high": (closes[-1] / max(highs[-20:]) - 1) * 100 if highs[-20:] else None,
        "gap_entry_vs_prevclose": (entry_price - prev_close) / prev_close * 100 if prev_close > 0 and entry_price > 0 else None,
    }
    return f


con = sqlite3.connect(f"file:{ROOT/'data'/'ml'/'decisions.db'}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
con.execute("pragma busy_timeout=5000")
trades = [dict(r) for r in con.execute(
    """select market, session_date, ticker, close_reason, entry_price,
    coalesce(pnl_pct_net,pnl_pct) pnl
    from v2_learning_performance
    where runtime_mode='live' and closed=1 and session_date>='2026-05-15'
      and entry_price is not null"""
)]
con.close()

joined = []
miss = 0
for t in trades:
    s = load_series(t["market"], str(t["ticker"]))
    f = entry_features(s, t["session_date"], float(t["entry_price"] or 0))
    if f is None:
        miss += 1
        continue
    joined.append({**t, **f})
print(f"trades {len(trades)} joined {len(joined)} miss {miss}")

FEATS = ["ret_1d", "ret_5d", "ret_21d", "max_daily_ret_21d", "vol_21d", "dist_20d_high", "gap_entry_vs_prevclose"]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


for mkt in ("US", "KR"):
    sub = [t for t in joined if t["market"] == mkt]
    lc = [t for t in sub if t["close_reason"] == "CLOSED_LOSS_CAP"]
    win = [t for t in sub if (t["pnl"] or 0) > 0]
    print(f"\n===== {mkt}: n={len(sub)} LOSS_CAP={len(lc)} winners={len(win)} net_sum={sum(t['pnl'] or 0 for t in sub):+.1f}%p =====")
    if len(lc) < 5:
        print("  표본 부족")
        continue
    print(f"  {'feature':24} {'LC_med':>8} {'WIN_med':>8} {'ALL_med':>8}")
    for feat in FEATS:
        print(f"  {feat:24} {med([t.get(feat) for t in lc]) or 0:8.2f} {med([t.get(feat) for t in win]) or 0:8.2f} {med([t.get(feat) for t in sub]) or 0:8.2f}")

    # 4분위 반사실: 각 피처를 4분위로 나눠 분위별 net합·TARGET수·LC수 (평균 뒤 분포)
    print("\n  --- 4분위 net 분해 (q1=하위) ---")
    for feat in FEATS:
        vals = sorted([t for t in sub if t.get(feat) is not None], key=lambda t: t[feat])
        if len(vals) < 20:
            continue
        qs = [vals[i * len(vals) // 4:(i + 1) * len(vals) // 4] for i in range(4)]
        parts = []
        for i, q in enumerate(qs):
            net = sum(t["pnl"] or 0 for t in q)
            nlc = sum(1 for t in q if t["close_reason"] == "CLOSED_LOSS_CAP")
            ntg = sum(1 for t in q if "TARGET" in str(t["close_reason"] or "") or "LADDER" in str(t["close_reason"] or ""))
            lo, hi_ = q[0][feat], q[-1][feat]
            parts.append(f"q{i+1}[{lo:+.1f}..{hi_:+.1f}] net{net:+.1f}(LC{nlc}/T{ntg})")
        print(f"  {feat:24} " + " | ".join(parts))
