#!/usr/bin/env python3
"""MFE 결측 트레이드만 안전하게 yfinance 5m 백필 (기존 양호값 절대 안 덮음).

backfill_mfe_yfinance.py는 전체를 재fetch하며 upsert가 기존 non-null을 null로 덮을 위험(4월
트레이드는 60일 경계라 재fetch시 no_bars). 이 스크립트는:
  - v2.mfe_pct 결측 + mfe_backfill_yf에 non-null 없는 트레이드만 대상.
  - fetch 성공(mfe non-null)한 것만 기록. **실패(null)는 쓰지 않음 → 기존값 보존, 표본 감소 0.**
read: v2_learning_performance. write: mfe_backfill_yf (성공분만 INSERT/UPDATE).
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"


def parse_utc(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def main() -> int:
    import yfinance as yf

    con = sqlite3.connect(f"file:{ML_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        """
        SELECT v.v2_decision_id, v.market, v.ticker, v.filled_at, v.closed_at,
               v.entry_price, v.exit_price, v.pnl_pct, v.close_reason
        FROM v2_learning_performance v
        LEFT JOIN mfe_backfill_yf b ON v.v2_decision_id=b.v2_decision_id
        WHERE v.closed=1 AND v.runtime_mode='live'
          AND v.mfe_pct IS NULL AND b.mfe_pct IS NULL
          AND v.filled_at IS NOT NULL AND v.closed_at IS NOT NULL
          AND v.entry_price IS NOT NULL AND v.entry_price>0
        """
    ).fetchall()]
    con.close()
    print(f"결측 대상 {len(rows)}건")

    by_tk: dict[tuple, list] = defaultdict(list)
    for r in rows:
        by_tk[(r["market"], str(r["ticker"]))].append(r)

    out = []
    for (market, ticker), pl in by_tk.items():
        s = min(parse_utc(p["filled_at"]) for p in pl) - timedelta(days=1)
        e = max(parse_utc(p["closed_at"]) for p in pl) + timedelta(days=2)
        cands = [ticker] if market == "US" else [f"{ticker}.KS", f"{ticker}.KQ"]
        df, sym = None, None
        for c in cands:
            try:
                d = yf.download(c, start=s.date(), end=e.date(), interval="5m", progress=False, auto_adjust=False)
            except Exception:
                d = None
            if d is not None and len(d) > 0:
                df, sym = d, c
                break
        if df is None or len(df) == 0:
            continue
        high, low = df["High"], df["Low"]
        if hasattr(high, "columns"):
            high = high.iloc[:, 0]
        if hasattr(low, "columns"):
            low = low.iloc[:, 0]
        idx = df.index
        try:
            idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        except Exception:
            pass
        for p in pl:
            f, cl = parse_utc(p["filled_at"]), parse_utc(p["closed_at"])
            mask = (idx >= f) & (idx <= cl)
            if int(mask.sum()) == 0:
                continue
            entry = float(p["entry_price"])
            mfe = (float(high[mask].max()) / entry - 1.0) * 100.0
            mae = (float(low[mask].min()) / entry - 1.0) * 100.0
            out.append((p, mfe, mae, int(mask.sum()), sym))
        time.sleep(0.25)

    if out:
        w = sqlite3.connect(str(ML_DB), timeout=30)
        try:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for p, mfe, mae, n, sym in out:
                w.execute(
                    "INSERT INTO mfe_backfill_yf "
                    "(v2_decision_id,market,ticker,yf_symbol,entry_price,exit_price,pnl_pct,close_reason,mfe_pct,mae_pct,bars,interval,source,synced_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(v2_decision_id) DO UPDATE SET mfe_pct=excluded.mfe_pct,mae_pct=excluded.mae_pct,"
                    "bars=excluded.bars,yf_symbol=excluded.yf_symbol,source=excluded.source,synced_at=excluded.synced_at",
                    (p["v2_decision_id"], p["market"], str(p["ticker"]), sym, float(p["entry_price"]),
                     p.get("exit_price"), p.get("pnl_pct"), p.get("close_reason"), mfe, mae, n, "5m", "yfinance_est", now),
                )
            w.commit()
        finally:
            w.close()
    print(f"신규 백필 성공 {len(out)}건 (실패분은 미기록 = 기존값 보존)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
