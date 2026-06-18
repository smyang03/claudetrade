from __future__ import annotations

"""진입 extension 신호 검증용 — entry bar 일봉 피처 백필(full 커버리지).

decisions.db의 rsi/gap은 Path A 전략로그라 PathB claude_price 진입(원장 다수)엔 6%만 부착.
extension 게이트(과열/추격)를 제대로 검증하려면 진입일 기준 RSI14/gap/5일선행수익을 직접 계산해야 한다.
yfinance 일봉은 60일 한계가 없어 전 기간 커버. read-only, 격리 DB(entry_discrimination.db) 별 테이블 저장.
"""

import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DB = ROOT / "data" / "analysis" / "entry_discrimination.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS entry_daily_feat (
    v2_decision_id TEXT PRIMARY KEY,
    market TEXT, ticker TEXT, session_date TEXT,
    rsi14 REAL, gap_pct REAL, ext_prevclose_pct REAL, ret5d_pct REAL,
    rsi14_prior REAL, ret5d_prior_pct REAL,
    source TEXT, synced_at TEXT
)
"""


def rsi14(closes):
    if len(closes) < 15:
        return None
    gains = losses = 0.0
    for i in range(-14, 0):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    if losses == 0:
        return 100.0
    rs = (gains / 14) / (losses / 14)
    return 100 - 100 / (1 + rs)


def main() -> int:
    import yfinance as yf

    conn = sqlite3.connect(f"file:{OUT_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT v2_decision_id,market,ticker,session_date,entry_price FROM entry_fwd "
        "WHERE source='yfinance_est'"
    ).fetchall()
    conn.close()

    by_tk = defaultdict(list)
    for did, mk, tk, sd, ep in rows:
        by_tk[(mk, str(tk))].append((did, sd, ep))

    out = []
    for (market, ticker), pl in by_tk.items():
        sds = [datetime.fromisoformat(p[1]).date() for p in pl]
        start = min(sds) - timedelta(days=40)
        end = max(sds) + timedelta(days=2)
        cands = [ticker] if market == "US" else [f"{ticker}.KS", f"{ticker}.KQ"]
        df = None
        for c in cands:
            try:
                d = yf.download(c, start=start, end=end, interval="1d",
                                progress=False, auto_adjust=False)
            except Exception:
                d = None
            if d is not None and len(d) > 0:
                df = d
                break
        if df is None or len(df) == 0:
            for did, sd, ep in pl:
                out.append((did, market, ticker, sd, None, None, None, None, None, None, "no_data"))
            continue
        opens, closes = df["Open"], df["Close"]
        try:
            if hasattr(opens, "columns"):
                opens = opens.iloc[:, 0]
            if hasattr(closes, "columns"):
                closes = closes.iloc[:, 0]
        except Exception:
            pass
        dates = [d.date() for d in df.index]
        cl = list(closes.values)
        op = list(opens.values)
        for did, sd, ep in pl:
            target = datetime.fromisoformat(sd).date()
            # 진입일 인덱스(없으면 직전 거래일)
            i = None
            for k in range(len(dates) - 1, -1, -1):
                if dates[k] <= target:
                    i = k
                    break
            if i is None or i < 1:
                out.append((did, market, ticker, sd, None, None, None, None, None, None, "no_bar"))
                continue
            r = rsi14(cl[: i + 1])
            gap = (op[i] / cl[i - 1] - 1) * 100 if cl[i - 1] else None
            ext = (cl[i] / cl[i - 1] - 1) * 100 if cl[i - 1] else None
            ret5 = (cl[i] / cl[i - 5] - 1) * 100 if i >= 5 and cl[i - 5] else None
            # 진입 전에만 아는 값(전일 종가까지) — lookahead 제거
            r_prior = rsi14(cl[:i])
            ret5_prior = (cl[i - 1] / cl[i - 6] - 1) * 100 if i >= 6 and cl[i - 6] else None
            out.append((did, market, ticker, sd, r, gap, ext, ret5, r_prior, ret5_prior, "ok"))
        time.sleep(0.2)

    w = sqlite3.connect(str(OUT_DB), timeout=30)
    try:
        w.executescript(SCHEMA)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for did, mk, tk, sd, r, gap, ext, ret5, r_prior, ret5_prior, src in out:
            w.execute(
                "INSERT INTO entry_daily_feat VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(v2_decision_id) DO UPDATE SET rsi14=excluded.rsi14,gap_pct=excluded.gap_pct,"
                "ext_prevclose_pct=excluded.ext_prevclose_pct,ret5d_pct=excluded.ret5d_pct,"
                "rsi14_prior=excluded.rsi14_prior,ret5d_prior_pct=excluded.ret5d_prior_pct,"
                "source=excluded.source,synced_at=excluded.synced_at",
                (did, mk, tk, sd, r, gap, ext, ret5, r_prior, ret5_prior, src, now),
            )
        w.commit()
    finally:
        w.close()
    ok = sum(1 for o in out if o[-1] == "ok")
    print(f"entry_daily_feat {ok}/{len(out)} computed (no_data/no_bar={len(out)-ok})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
