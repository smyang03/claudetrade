from __future__ import annotations

"""진입변별 본검증 — 진입앵커 고정창 전방 재구성.

기존 backfill_mfe_yfinance.py는 [filled_at, closed_at] 보유구간 MFE를 잰다. 그건 capture/giveback
신호라 "조기청산(exit)"이 진입품질과 뒤섞인다. 이 도구는 우리 청산과 무관하게 진입시점(filled_at)을
앵커로 고정창(+30m/+60m/당일 EOD/익일 EOD)의 전방 MFE/MAE/수익률을 5분봉으로 재구성한다.

목적: held-MFE<1% "죽은 진입"이 진짜 알파 부재(selection 문제)인지, 알파는 왔는데 우리가 먼저
청산한 것(hold/exit 문제)인지 가른다.

read-only: v2_learning_performance를 mode=ro로만 읽는다. 라이브 decisions.db 오염 방지를 위해
결과는 별도 격리 DB(data/analysis/entry_discrimination.db, source='yfinance_est')에 저장한다.
"""

import argparse
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"
OUT_DIR = ROOT / "data" / "analysis"
OUT_DB = OUT_DIR / "entry_discrimination.db"

# 시장별 정규장 마감 (UTC). 2026 4~6월 미국은 EDT(UTC-4): 16:00 ET = 20:00 UTC. KR 15:30 KST = 06:30 UTC.
US_CLOSE_UTC = (20, 0)
KR_CLOSE_UTC = (6, 30)

SCHEMA = """
CREATE TABLE IF NOT EXISTS entry_fwd (
    v2_decision_id TEXT PRIMARY KEY,
    market TEXT, ticker TEXT, yf_symbol TEXT,
    session_date TEXT, strategy TEXT, timing_style TEXT, close_reason TEXT,
    filled_at TEXT, closed_at TEXT, entry_price REAL, exit_price REAL,
    pnl_pct REAL, pnl_pct_net REAL, held_mfe_pct REAL,
    fwd_mfe_m30 REAL, fwd_mae_m30 REAL, fwd_ret_m30 REAL,
    fwd_mfe_m60 REAL, fwd_mae_m60 REAL, fwd_ret_m60 REAL,
    fwd_mfe_eod0 REAL, fwd_mae_eod0 REAL, fwd_ret_eod0 REAL,
    fwd_mfe_eod1 REAL, fwd_mae_eod1 REAL,
    bars_eod0 INTEGER, interval TEXT, source TEXT, synced_at TEXT
)
"""


def parse_utc(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def market_eod(filled: datetime, market: str) -> datetime:
    h, m = US_CLOSE_UTC if market == "US" else KR_CLOSE_UTC
    eod = filled.replace(hour=h, minute=m, second=0, microsecond=0)
    # 진입이 이미 마감시각 이후면(드묾) +6h로 bound
    if eod <= filled:
        eod = filled + timedelta(hours=6)
    return eod


def window_stats(idx, high, low, close, start: datetime, end: datetime):
    mask = (idx >= start) & (idx <= end)
    n = int(mask.sum())
    if n == 0:
        return None, None, None, 0
    return float(high[mask].max()), float(low[mask].min()), float(close[mask][-1]), n


def main() -> int:
    ap = argparse.ArgumentParser(description="진입앵커 전방 MFE/MAE 재구성(read-only 측정)")
    ap.add_argument("--market", default="ALL", choices=["ALL", "US", "KR"])
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--limit", type=int, default=0, help="종목 수 제한(테스트용)")
    ap.add_argument("--sleep", type=float, default=0.25)
    args = ap.parse_args()

    import yfinance as yf

    conn = sqlite3.connect(f"file:{ML_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    q = (
        "SELECT v.v2_decision_id,v.market,v.ticker,v.session_date,v.strategy,v.timing_style,"
        "v.close_reason,v.filled_at,v.closed_at,v.entry_price,v.exit_price,v.pnl_pct,v.pnl_pct_net,"
        "b.mfe_pct AS held_mfe "
        "FROM v2_learning_performance v "
        "LEFT JOIN mfe_backfill_yf b ON v.v2_decision_id=b.v2_decision_id "
        "WHERE v.closed=1 AND v.runtime_mode='live' AND v.filled_at IS NOT NULL "
        "AND v.entry_price IS NOT NULL AND v.entry_price>0"
    )
    if args.market != "ALL":
        q += f" AND v.market='{args.market}'"
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
        s = min(starts) - timedelta(days=1)
        e = max(starts) + timedelta(days=3)
        cands = [ticker] if market == "US" else [f"{ticker}.KS", f"{ticker}.KQ"]
        df = None
        sym = None
        for c in cands:
            try:
                d = yf.download(c, start=s.date(), end=e.date(), interval=args.interval,
                                progress=False, auto_adjust=False)
            except Exception:
                d = None
            if d is not None and len(d) > 0:
                df, sym = d, c
                break
        if df is None or len(df) == 0:
            for p in pl:
                out.append((p, sym, "no_data", {}))
            continue
        high, low, close = df["High"], df["Low"], df["Close"]
        try:
            if hasattr(high, "columns"):
                high = high.iloc[:, 0]
            if hasattr(low, "columns"):
                low = low.iloc[:, 0]
            if hasattr(close, "columns"):
                close = close.iloc[:, 0]
        except Exception:
            pass
        idx = df.index
        try:
            idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        except Exception:
            pass
        hv, lv, cv = high.values, low.values, close.values
        for p in pl:
            f = parse_utc(p["filled_at"])
            entry = float(p["entry_price"])
            eod0 = market_eod(f, market)
            eod1 = eod0 + timedelta(days=1)
            wins = {
                "m30": (f, f + timedelta(minutes=30)),
                "m60": (f, f + timedelta(minutes=60)),
                "eod0": (f, eod0),
                "eod1": (f, eod1),
            }
            res = {}
            ok = False
            for name, (st_, en_) in wins.items():
                hmax, lmin, clast, n = window_stats(idx, hv, lv, cv, st_, en_)
                if hmax is None:
                    res[name] = (None, None, None, 0)
                else:
                    ok = True
                    res[name] = ((hmax / entry - 1) * 100, (lmin / entry - 1) * 100,
                                 (clast / entry - 1) * 100, n)
            out.append((p, sym, "yfinance_est" if ok else "no_bars", res))
        time.sleep(args.sleep)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    w = sqlite3.connect(str(OUT_DB), timeout=30)
    try:
        w.executescript(SCHEMA)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for p, sym, src, res in out:
            def g(name, i):
                return res.get(name, (None, None, None, 0))[i] if res else None
            w.execute(
                "INSERT INTO entry_fwd VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(v2_decision_id) DO UPDATE SET "
                "fwd_mfe_m30=excluded.fwd_mfe_m30,fwd_mae_m30=excluded.fwd_mae_m30,fwd_ret_m30=excluded.fwd_ret_m30,"
                "fwd_mfe_m60=excluded.fwd_mfe_m60,fwd_mae_m60=excluded.fwd_mae_m60,fwd_ret_m60=excluded.fwd_ret_m60,"
                "fwd_mfe_eod0=excluded.fwd_mfe_eod0,fwd_mae_eod0=excluded.fwd_mae_eod0,fwd_ret_eod0=excluded.fwd_ret_eod0,"
                "fwd_mfe_eod1=excluded.fwd_mfe_eod1,fwd_mae_eod1=excluded.fwd_mae_eod1,"
                "bars_eod0=excluded.bars_eod0,source=excluded.source,synced_at=excluded.synced_at",
                (
                    p["v2_decision_id"], p["market"], str(p["ticker"]), sym,
                    p.get("session_date"), p.get("strategy"), p.get("timing_style"), p.get("close_reason"),
                    p.get("filled_at"), p.get("closed_at"), float(p["entry_price"]), p.get("exit_price"),
                    p.get("pnl_pct"), p.get("pnl_pct_net"), p.get("held_mfe"),
                    g("m30", 0), g("m30", 1), g("m30", 2),
                    g("m60", 0), g("m60", 1), g("m60", 2),
                    g("eod0", 0), g("eod0", 1), g("eod0", 2),
                    g("eod1", 0), g("eod1", 1),
                    res.get("eod0", (None, None, None, 0))[3] if res else 0,
                    args.interval, src, now,
                ),
            )
        w.commit()
    finally:
        w.close()

    ok = sum(1 for o in out if o[2] == "yfinance_est")
    nd = sum(1 for o in out if o[2] == "no_data")
    nb = sum(1 for o in out if o[2] == "no_bars")
    print(f"entry_fwd {ok}/{len(out)} reconstructed (no_data={nd} no_bars={nb}) -> {OUT_DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
