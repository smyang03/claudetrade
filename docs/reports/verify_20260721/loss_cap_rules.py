# LOSS_CAP 진입선별 룰 반사실 — 단순 룰별 배제군 net·TARGET 희생·anti-chase25 증분
from __future__ import annotations

import csv
import sqlite3
import statistics
from pathlib import Path

ROOT = Path(r"E:\code\claudetrade")


def load_series(market, ticker):
    sub = "us" if market == "US" else "kr"
    p = ROOT / "data" / "price" / sub / f"{sub}_{ticker}.csv"
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append((str(row["date"]).strip(), float(row["open"]), float(row["high"]),
                            float(row["low"]), float(row["close"])))
            except Exception:
                continue
    out.sort(key=lambda r: r[0])
    return out


def feats(series, session_date, entry_price):
    hist = [r for r in series if r[0] < session_date]
    if len(hist) < 22:
        return None
    closes = [r[4] for r in hist]
    last21 = hist[-21:]
    rets = []
    for i in range(1, len(last21)):
        pc = last21[i - 1][4]
        if pc > 0:
            rets.append((last21[i][4] - pc) / pc * 100)
    prev_close = closes[-1]
    highs20 = [r[2] for r in hist[-20:]]
    return {
        "ret_1d": (closes[-1] - closes[-2]) / closes[-2] * 100,
        "ret_5d": (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else None,
        "max21": max(rets) if rets else None,
        "dist20h": (closes[-1] / max(highs20) - 1) * 100 if highs20 else None,
        "gap": (entry_price - prev_close) / prev_close * 100 if prev_close > 0 and entry_price > 0 else None,
    }


con = sqlite3.connect(f"file:{ROOT/'data'/'ml'/'decisions.db'}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
con.execute("pragma busy_timeout=5000")
trades = [dict(r) for r in con.execute(
    """select market, session_date, ticker, close_reason, entry_price,
    coalesce(pnl_pct_net,pnl_pct) pnl from v2_learning_performance
    where runtime_mode='live' and closed=1 and session_date>='2026-05-15' and entry_price is not null"""
)]
con.close()

rows = []
for t in trades:
    f = feats(load_series(t["market"], str(t["ticker"])), t["session_date"], float(t["entry_price"] or 0))
    if f:
        rows.append({**t, **f})

RULES = {
    "A ret_5d<-5 배제(낙폭베팅)": lambda t: (t["ret_5d"] or 0) < -5,
    "B gap>+7 배제(추격진입)": lambda t: (t["gap"] or 0) > 7,
    "C dist20h<-17 배제(낙폭과대)": lambda t: (t["dist20h"] or 0) < -17,
    "D ret_1d<-2 배제(전일급락)": lambda t: (t["ret_1d"] or 0) < -2,
    "A|B": lambda t: (t["ret_5d"] or 0) < -5 or (t["gap"] or 0) > 7,
    "A|B|C": lambda t: (t["ret_5d"] or 0) < -5 or (t["gap"] or 0) > 7 or (t["dist20h"] or 0) < -17,
}


def report(sub, mkt):
    total_net = sum(t["pnl"] or 0 for t in sub)
    print(f"\n===== {mkt} n={len(sub)} 통산 net={total_net:+.1f}%p =====")
    ac_blocked = [t for t in sub if (t["max21"] or 0) >= 25]
    print(f"  (참고) anti-chase25 기배제군: n={len(ac_blocked)} net={sum(t['pnl'] or 0 for t in ac_blocked):+.1f}%p")
    live = [t for t in sub if (t["max21"] or 0) < 25]  # 증분 = anti-chase 통과분에서만
    print(f"  anti-chase 통과 잔여: n={len(live)} net={sum(t['pnl'] or 0 for t in live):+.1f}%p")
    for name, pred in RULES.items():
        exc = [t for t in live if pred(t)]
        keep = [t for t in live if not pred(t)]
        exc_net = sum(t["pnl"] or 0 for t in exc)
        keep_net = sum(t["pnl"] or 0 for t in keep)
        exc_tgt = sum(1 for t in exc if "TARGET" in str(t["close_reason"] or "") or "LADDER" in str(t["close_reason"] or ""))
        exc_tgt_net = sum(t["pnl"] or 0 for t in exc if "TARGET" in str(t["close_reason"] or "") or "LADDER" in str(t["close_reason"] or ""))
        exc_lc = sum(1 for t in exc if t["close_reason"] == "CLOSED_LOSS_CAP")
        print(f"  {name:28} 배제 n={len(exc):>3} net={exc_net:+7.1f}%p (LC{exc_lc}·TGT{exc_tgt} tgt_net{exc_tgt_net:+.1f}) | 잔존 net={keep_net:+7.1f}%p")


for mkt in ("US", "KR"):
    report([t for t in rows if t["market"] == mkt], mkt)

# 월별 안정성(US, 최유망 룰): 5월/6월/7월 분리
print("\n===== US 월별 분해 (A|B) =====")
us = [t for t in rows if t["market"] == "US" and (t["max21"] or 0) < 25]
for month in ("2026-05", "2026-06", "2026-07"):
    ms = [t for t in us if t["session_date"].startswith(month)]
    if not ms:
        continue
    pred = RULES["A|B"]
    exc = [t for t in ms if pred(t)]
    keep = [t for t in ms if not pred(t)]
    print(f"  {month}: n={len(ms)} 전체net={sum(t['pnl'] or 0 for t in ms):+.1f} | 배제 n={len(exc)} net={sum(t['pnl'] or 0 for t in exc):+.1f} | 잔존net={sum(t['pnl'] or 0 for t in keep):+.1f}")
