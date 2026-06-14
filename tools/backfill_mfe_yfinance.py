from __future__ import annotations

"""과거 PathB 청산 포지션의 MFE/MAE를 yfinance 장중(5분봉)으로 재구성한다.

시스템이 진입 후 장중 가격 경로를 저장하지 않아(Phase 1c는 재시작 후부터 수집) 과거
capture/러너 조기절단을 측정할 수 없었다. yfinance 5분봉은 최근 60일을 제공하므로 이 계좌의
거래 기간(4/27~)을 거의 커버한다. 구간 [filled_at, closed_at]의 고가/저가로 MFE/MAE를 근사한다.

근사치임을 명확히 하기 위해 v2 본 테이블이 아니라 별도 테이블 mfe_backfill_yf에 저장한다
(sync가 덮어쓰지 않음, source='yfinance_est'). filled_at/closed_at은 UTC로 저장돼 있어
yfinance UTC index와 직접 매칭한다. US는 native USD, KR은 .KS/.KQ 원화로 정합한다.
"""

import argparse
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS mfe_backfill_yf (
    v2_decision_id TEXT PRIMARY KEY,
    market TEXT, ticker TEXT, yf_symbol TEXT,
    entry_price REAL, exit_price REAL, pnl_pct REAL, close_reason TEXT,
    mfe_pct REAL, mae_pct REAL,
    bars INTEGER, interval TEXT, source TEXT, synced_at TEXT
)
"""


def parse_utc(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser(description="yfinance 5분봉으로 과거 MFE/MAE 재구성")
    ap.add_argument("--market", default="ALL", choices=["ALL", "US", "KR"])
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--limit", type=int, default=0, help="종목 수 제한(테스트용)")
    ap.add_argument("--sleep", type=float, default=0.25)
    args = ap.parse_args()

    import yfinance as yf

    conn = sqlite3.connect(f"file:{ML_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    q = (
        "SELECT v2_decision_id,market,ticker,filled_at,closed_at,entry_price,exit_price,pnl_pct,close_reason "
        "FROM v2_learning_performance WHERE closed=1 AND runtime_mode='live' "
        "AND filled_at IS NOT NULL AND closed_at IS NOT NULL AND entry_price IS NOT NULL AND entry_price>0"
    )
    if args.market != "ALL":
        q += f" AND market='{args.market}'"
    rows = [dict(r) for r in conn.execute(q).fetchall()]
    conn.close()

    by_tk: dict[tuple, list] = defaultdict(list)
    for r in rows:
        by_tk[(r["market"], str(r["ticker"]))].append(r)

    items = list(by_tk.items())
    if args.limit:
        items = items[: args.limit]

    out = []
    for (market, ticker), pl in items:
        starts = [parse_utc(p["filled_at"]) for p in pl]
        ends = [parse_utc(p["closed_at"]) for p in pl]
        s = min(starts) - timedelta(days=1)
        e = max(ends) + timedelta(days=2)
        cands = [ticker] if market == "US" else [f"{ticker}.KS", f"{ticker}.KQ"]
        df = None
        sym = None
        for c in cands:
            try:
                d = yf.download(c, start=s.date(), end=e.date(), interval=args.interval, progress=False, auto_adjust=False)
            except Exception:
                d = None
            if d is not None and len(d) > 0:
                df = d
                sym = c
                break
        if df is None or len(df) == 0:
            for p in pl:
                out.append((p, None, None, 0, "no_data", None))
            continue
        high = df["High"]
        low = df["Low"]
        try:
            if hasattr(high, "columns"):
                high = high.iloc[:, 0]
            if hasattr(low, "columns"):
                low = low.iloc[:, 0]
        except Exception:
            pass
        idx = df.index
        try:
            idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        except Exception:
            pass
        for p in pl:
            f = parse_utc(p["filled_at"])
            cl = parse_utc(p["closed_at"])
            mask = (idx >= f) & (idx <= cl)
            n = int(mask.sum())
            if n == 0:
                out.append((p, None, None, 0, "no_bars", sym))
                continue
            hmax = float(high[mask].max())
            lmin = float(low[mask].min())
            entry = float(p["entry_price"])
            mfe = (hmax / entry - 1.0) * 100.0
            mae = (lmin / entry - 1.0) * 100.0
            out.append((p, mfe, mae, n, "yfinance_est", sym))
        time.sleep(args.sleep)

    w = sqlite3.connect(str(ML_DB), timeout=30)
    try:
        w.executescript(SCHEMA)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for p, mfe, mae, n, src, sym in out:
            w.execute(
                "INSERT INTO mfe_backfill_yf "
                "(v2_decision_id,market,ticker,yf_symbol,entry_price,exit_price,pnl_pct,close_reason,mfe_pct,mae_pct,bars,interval,source,synced_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(v2_decision_id) DO UPDATE SET yf_symbol=excluded.yf_symbol,mfe_pct=excluded.mfe_pct,"
                "mae_pct=excluded.mae_pct,bars=excluded.bars,interval=excluded.interval,source=excluded.source,synced_at=excluded.synced_at",
                (
                    p["v2_decision_id"], p["market"], str(p["ticker"]), sym,
                    float(p["entry_price"]), p.get("exit_price"), p.get("pnl_pct"), p.get("close_reason"),
                    mfe, mae, n, args.interval, src, now,
                ),
            )
        w.commit()
    finally:
        w.close()

    ok = sum(1 for o in out if o[4] == "yfinance_est")
    print(f"backfill {ok}/{len(out)} reconstructed (no_data/no_bars={len(out)-ok})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
