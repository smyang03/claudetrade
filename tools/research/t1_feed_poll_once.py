"""T1 BMO 실적 피드 지연 관측 — 1회 폴링(예약 작업이 평일 19:00~22:30 KST 5분 간격으로 부른다).

오늘(ET) BMO 예정 종목을 T1 채점기와 같은 후보 규칙(_candidates)으로 뽑아 research_t1_feed_latency_20260930.cmd_poll로
yfinance·Finnhub 캘린더·Finnhub stock/earnings 상태를 기록한다. 약 2주(BMO ~40건) 뒤 `pollsum`으로 확정.
연구 관측 전용 — 주문·원장 변경 없음. 2026-09-30 운영자 승인("추천방향으로 진행").
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "research"))

import pandas as pd  # noqa: E402

import us_earnings_t1_shadow as s  # noqa: E402


def main() -> int:
    import zoneinfo
    today = pd.Timestamp(datetime.now(zoneinfo.ZoneInfo("America/New_York")).date())
    if today.weekday() >= 5:
        return 0
    cands = s._candidates(today, today - pd.Timedelta(days=1))
    tickers = sorted(tk for (tk, ds), r in cands.items() if ds == str(today.date()) and r.get("hour") == "BMO")
    if not tickers:
        print(f"[t1-poll] {today.date()} BMO 예정 없음"); return 0
    import research_t1_feed_latency_20260930 as lat
    lat.cmd_poll(str(today.date()), tickers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
