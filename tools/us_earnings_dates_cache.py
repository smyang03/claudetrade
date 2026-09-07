# -*- coding: utf-8 -*-
"""US 어닝 발표일 캐시 (yfinance `earnings_dates`, 종목당 최대 40행) — 신규 전략 P7 US 어닝 가격반응 PEAD 백필용 (2026-09-07).

- 추정치·서프라이즈는 PIT가 아니므로 전략에 쓰지 않는다. 쓰는 것은 **발표일·시각(BMO/AMC)** 뿐이다.
- Reported EPS가 비어 있는 과거 행은 발표일 신뢰 불가(추정 일정)로 `confirmed=false` 표시 → 풀에서 제외.
- 반응일: BMO(09:30 ET 이전) = 당일, AMC(16:00 이후) = 다음 거래일, 장중(DMH) = 당일(보수적으로 다음 거래일 진입).
- 재개 가능(종목 단위). 원장: data/analysis/us_earnings_dates.jsonl
사용: python tools/us_earnings_dates_cache.py [--max N] [--refresh-days D]
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
US_DIR = ROOT / "data" / "price" / "us"
OUT = ROOT / "data" / "analysis" / "us_earnings_dates.jsonl"
STATE = ROOT / "data" / "analysis" / "us_earnings_dates.state.json"
SLEEP = 0.25


def _hour(ts) -> str:
    h, m = ts.hour, ts.minute
    if (h, m) < (9, 30):
        return "BMO"
    if (h, m) >= (16, 0):
        return "AMC"
    return "DMH"


def main() -> int:
    import yfinance as yf
    args = sys.argv[1:]
    max_n = int(args[args.index("--max") + 1]) if "--max" in args else None
    refresh_days = int(args[args.index("--refresh-days") + 1]) if "--refresh-days" in args else 7
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {"done": {}}
    now = datetime.now(timezone.utc)
    tickers = sorted(p.stem[3:] for p in US_DIR.glob("us_*.csv"))
    n = 0; rows = 0; fails = 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        for tk in tickers:
            last = st["done"].get(tk)
            if last and (now - datetime.fromisoformat(last)).days < refresh_days:
                continue
            if max_n is not None and n >= max_n:
                break
            n += 1
            try:
                ed = yf.Ticker(tk).get_earnings_dates(limit=40)
            except Exception as exc:  # noqa: BLE001
                fails += 1; print(f"[EARN] {tk} 실패 {exc}"); time.sleep(1.0); continue
            if ed is None or len(ed) == 0:
                st["done"][tk] = now.isoformat(); continue
            for ts, r in ed.iterrows():
                try:
                    est = r.get("EPS Estimate"); act = r.get("Reported EPS")
                    est = None if est != est else float(est); act = None if act != act else float(act)
                except Exception:  # noqa: BLE001
                    est = act = None
                row = {"ticker": tk, "ts": ts.isoformat(), "date": ts.date().isoformat(), "hour": _hour(ts),
                       "eps_est": est, "eps_act": act, "confirmed": act is not None,
                       "fetched_at": now.isoformat(timespec="seconds")}
                fh.write(json.dumps(row) + "\n"); rows += 1
            st["done"][tk] = now.isoformat()
            if n % 50 == 0:
                fh.flush(); STATE.write_text(json.dumps(st), encoding="utf-8"); print(f"[EARN] {n}종목 · {rows}행 · 실패 {fails}")
            time.sleep(SLEEP)
    STATE.write_text(json.dumps(st), encoding="utf-8")
    print(f"[EARN] 완료 — 종목 {n} · 행 {rows} · 실패 {fails}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
