from __future__ import annotations

"""초반 진입 검증 — 개장가 대비 우리 진입 위치 + 개장 진입 카운터팩추얼.

질문: 우리 진입이 그날 개장가보다 위(개장후 추격 상승)냐 아래(눌림 매수)냐. exit를 고정하고
진입만 (a)개장가 (b)첫45분 저가 로 바꿨을 때 net 델타를 잰다. 위라면 더 일찍 들어갔으면 이득.

read-only. entry_fwd(격리DB)를 읽고 5분봉으로 당일 개장가/첫45분 경로 재구성, 별 테이블 저장.
"""

import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DB = ROOT / "data" / "analysis" / "entry_discrimination.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS early_entry_cf (
    v2_decision_id TEXT PRIMARY KEY,
    market TEXT, ticker TEXT, session_date TEXT, entry_min_from_open INTEGER,
    entry_price REAL, exit_price REAL, day_open REAL, first45_low REAL, first45_high REAL,
    actual_net REAL, open_entry_net REAL, low45_entry_net REAL,
    source TEXT, synced_at TEXT
)
"""

US_OPEN = (13, 30)
KR_OPEN = (0, 0)


def parse_utc(s):
    dt = datetime.fromisoformat(str(s))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def main() -> int:
    import yfinance as yf

    conn = sqlite3.connect(f"file:{OUT_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT v2_decision_id,market,ticker,session_date,filled_at,entry_price,exit_price "
        "FROM entry_fwd WHERE source='yfinance_est' AND exit_price IS NOT NULL"
    ).fetchall()
    conn.close()

    by_tk = defaultdict(list)
    for r in rows:
        by_tk[(r[1], str(r[2]))].append(r)

    out = []
    for (market, ticker), pl in by_tk.items():
        starts = [parse_utc(p[4]) for p in pl]
        s = min(starts) - timedelta(days=1)
        e = max(starts) + timedelta(days=2)
        cands = [ticker] if market == "US" else [f"{ticker}.KS", f"{ticker}.KQ"]
        df = None
        for c in cands:
            try:
                d = yf.download(c, start=s.date(), end=e.date(), interval="5m",
                                progress=False, auto_adjust=False)
            except Exception:
                d = None
            if d is not None and len(d) > 0:
                df = d
                break
        if df is None or len(df) == 0:
            for p in pl:
                out.append((p, None, None, None, "no_data"))
            continue
        op, hi, lo = df["Open"], df["High"], df["Low"]
        try:
            for v in ("op", "hi", "lo"):
                pass
            if hasattr(op, "columns"):
                op = op.iloc[:, 0]
            if hasattr(hi, "columns"):
                hi = hi.iloc[:, 0]
            if hasattr(lo, "columns"):
                lo = lo.iloc[:, 0]
        except Exception:
            pass
        idx = df.index
        try:
            idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        except Exception:
            pass
        for p in pl:
            f = parse_utc(p[4])
            oh, om = US_OPEN if market == "US" else KR_OPEN
            day_open_dt = f.replace(hour=oh, minute=om, second=0, microsecond=0)
            w_open = (idx >= day_open_dt) & (idx < day_open_dt + timedelta(minutes=10))
            w45 = (idx >= day_open_dt) & (idx < day_open_dt + timedelta(minutes=45))
            if int(w_open.sum()) == 0 or int(w45.sum()) == 0:
                out.append((p, None, None, None, "no_bars"))
                continue
            day_open = float(op[w_open][0])
            f45_low = float(lo[w45].min())
            f45_high = float(hi[w45].max())
            out.append((p, day_open, f45_low, f45_high, "ok"))
        time.sleep(0.25)

    w = sqlite3.connect(str(OUT_DB), timeout=30)
    try:
        w.executescript(SCHEMA)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for p, day_open, f45_low, f45_high, src in out:
            did, market, ticker, sd, filled_at, entry, exitp = p
            emin = None
            try:
                f = parse_utc(filled_at)
                oh, om = US_OPEN if market == "US" else KR_OPEN
                emin = int((f - f.replace(hour=oh, minute=om, second=0, microsecond=0)).total_seconds() // 60)
            except Exception:
                pass
            actual_net = open_net = low_net = None
            if src == "ok" and entry and exitp:
                actual_net = (exitp / entry - 1) * 100
                if day_open:
                    open_net = (exitp / day_open - 1) * 100
                if f45_low:
                    low_net = (exitp / f45_low - 1) * 100
            w.execute(
                "INSERT INTO early_entry_cf VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(v2_decision_id) DO UPDATE SET day_open=excluded.day_open,"
                "first45_low=excluded.first45_low,first45_high=excluded.first45_high,"
                "actual_net=excluded.actual_net,open_entry_net=excluded.open_entry_net,"
                "low45_entry_net=excluded.low45_entry_net,source=excluded.source,synced_at=excluded.synced_at",
                (did, market, str(ticker), sd, emin, entry, exitp, day_open, f45_low, f45_high,
                 actual_net, open_net, low_net, src, now),
            )
        w.commit()
    finally:
        w.close()
    ok = sum(1 for o in out if o[4] == "ok")
    print(f"early_entry_cf {ok}/{len(out)} (no_data/no_bars={len(out)-ok})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
