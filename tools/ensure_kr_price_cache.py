# -*- coding: utf-8 -*-
"""KR 가격 캐시 커버리지 보강 — 이벤트 종목의 일봉 CSV가 없으면 만든다 (2026-09-04).

09-03 실측: DART 재생에서 무상증자 58건 중 24건(41%), 공급계약 2,018건 중 894건이 우리 캐시(1,330종목) 밖이라
가상 북 F6/F7이 진입 자체를 못 한다. 한 번 CSV가 생기면 16:00 update_data(--market KR)가 이후 자동 유지한다.
소스: price_collector.fetch_kr_daily(KIS 일봉, 100행 페이지) → 실패 시 yfinance. 저장은 수집기의 검증 writer(_save).
사용:
  python tools/ensure_kr_price_cache.py --tickers 475460 052710
  python tools/ensure_kr_price_cache.py --from-events [--limit 200]   # DART 재생 + 실시간 레인 원장의 종목
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.live", override=False)

KR_DIR = ROOT / "data" / "price" / "kr"
EVENT_FILES = (ROOT / "data" / "analysis" / "dart_events_12m.jsonl", ROOT / "data" / "shadow" / "kr_event_signals.jsonl")
DAYS_BACK = 400


def missing(tickers: list[str]) -> list[str]:
    return [t for t in dict.fromkeys(str(x).strip() for x in tickers if x) if len(t) == 6 and not (KR_DIR / f"kr_{t}.csv").exists()]


def tickers_from_events() -> list[str]:
    out: list[str] = []
    for p in EVENT_FILES:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t = r.get("stock") or r.get("stock_code")
            kind = str(r.get("kind", ""))
            if t and ("무상증자" in kind or "공급계약" in kind or "자기주식" in kind
                      or kind in ("bonus_issue", "supply_contract", "buyback")):
                out.append(str(t))
    return out


def ensure(tickers: list[str], *, days_back: int = DAYS_BACK, sleep_sec: float = 0.3, verbose: bool = True) -> dict:
    """없는 CSV만 생성. 반환 {created:[...], failed:[...], skipped:n}."""
    import pandas as pd
    from phase1_trainer import price_collector as pc

    need = missing(tickers)
    res = {"created": [], "failed": [], "skipped": len(tickers) - len(need)}
    end = date.today()
    start = end - timedelta(days=days_back)
    for t in need:
        path = KR_DIR / f"kr_{t}.csv"
        df = pd.DataFrame()
        try:
            df = pc.fetch_kr_daily(t, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        except Exception as exc:
            if verbose:
                print(f"  [{t}] KIS 실패: {str(exc)[:80]}")
        if df is None or df.empty:
            try:
                df = pc.fetch_kr_daily_yfinance(t, pd.Timestamp(start), pd.Timestamp(end))
            except Exception as exc:
                if verbose:
                    print(f"  [{t}] yfinance 실패: {str(exc)[:80]}")
                df = pd.DataFrame()
        if df is None or df.empty:
            res["failed"].append(t)
            continue
        try:
            pc._save(path, df, pd.Timestamp(start), pd.Timestamp(end), f"kr_{t}")
            if path.exists():
                res["created"].append(t)
            else:
                res["failed"].append(t)
        except Exception as exc:
            if verbose:
                print(f"  [{t}] 저장 실패: {str(exc)[:80]}")
            res["failed"].append(t)
        time.sleep(sleep_sec)
    if verbose:
        print(f"[KR-CACHE] 대상 {len(tickers)} · 없음 {len(need)} · 생성 {len(res['created'])} · 실패 {len(res['failed'])}")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--from-events", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    tickers = list(a.tickers or [])
    if a.from_events:
        tickers += tickers_from_events()
    tickers = list(dict.fromkeys(tickers))
    if a.limit:
        tickers = tickers[: a.limit]
    if not tickers:
        ap.print_help()
        return 1
    r = ensure(tickers)
    return 0 if not r["failed"] or r["created"] else 1


if __name__ == "__main__":
    sys.exit(main())
