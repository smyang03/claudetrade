"""US breadth proxy 일봉 백필 (RSP/SPY 중심).

`data/analysis/us_breadth_proxy_daily.csv`가 2026-07-09에서 멈춰 있어
`tools/us_swing_shadow_runner.load_breadth_context`가 매 세션 `MISSING`을 반환했다.
(50건 중 45건 MISSING — 국면별 분해가 diagnostic에서조차 불가능한 상태)

breadth_context_state는 `narrow_excess_pct = RSP수익률 - SPY수익률`만으로 결정된다.
NYSE_comp / SP400_mid는 상태 판정에 쓰이지 않으므로 받지 못해도 백필을 진행하고,
빈 값으로 남긴다(있으면 채운다).

사용:
  python tools/backfill_us_breadth_proxy.py --dry-run   # 받을 구간만 확인
  python tools/backfill_us_breadth_proxy.py             # append

이 도구는 CSV에 **없는 날짜만** 덧붙인다. 기존 행은 고치지 않는다.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data" / "analysis" / "us_breadth_proxy_daily.csv"
COLUMNS = ["date", "RSP", "SPY", "NYSE_comp", "SP400_mid", "RSP_SPY_ratio"]

# 상태 판정 필수 2종 + 선택 2종.
REQUIRED_TICKERS = {"RSP": "RSP", "SPY": "SPY"}
OPTIONAL_TICKERS = {"NYSE_comp": "^NYA", "SP400_mid": "^MID"}


def _download(symbols: dict[str, str], start: str, end: str) -> dict[str, pd.Series]:
    import yfinance as yf

    output: dict[str, pd.Series] = {}
    for column, symbol in symbols.items():
        try:
            frame = yf.download(
                symbol, start=start, end=end, progress=False, auto_adjust=False, threads=False
            )
        except Exception as exc:  # 네트워크/심볼 문제는 해당 열만 포기한다.
            print(f"[warn] {column}({symbol}) download failed: {exc}", file=sys.stderr)
            continue
        if frame is None or frame.empty or "Close" not in frame:
            print(f"[warn] {column}({symbol}) empty frame", file=sys.stderr)
            continue
        close = frame["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close.index = pd.to_datetime(close.index).strftime("%Y-%m-%d")
        output[column] = close.astype(float)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill US breadth proxy daily CSV")
    parser.add_argument("--target", default=str(TARGET))
    parser.add_argument("--until", default="", help="YYYY-MM-DD (기본: 어제)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target = Path(args.target)
    existing = pd.read_csv(target) if target.exists() else pd.DataFrame(columns=COLUMNS)
    existing["date"] = existing["date"].astype(str)
    have = set(existing["date"])
    last = max(have) if have else "2015-01-01"
    # yfinance end는 배타적이므로 하루 더한다.
    until = args.until or (date.today() - timedelta(days=1)).isoformat()
    start = (date.fromisoformat(last) + timedelta(days=1)).isoformat()
    if start > until:
        print(json.dumps({"status": "UP_TO_DATE", "last": last, "until": until}, ensure_ascii=False))
        return 0
    end_exclusive = (date.fromisoformat(until) + timedelta(days=1)).isoformat()

    if args.dry_run:
        print(json.dumps({"status": "DRY_RUN", "last": last, "fetch": [start, until]}, ensure_ascii=False))
        return 0

    series = _download({**REQUIRED_TICKERS, **OPTIONAL_TICKERS}, start, end_exclusive)
    missing_required = [name for name in REQUIRED_TICKERS if name not in series]
    if missing_required:
        print(
            json.dumps(
                {"status": "FAILED", "reason": "required_series_missing", "missing": missing_required},
                ensure_ascii=False,
            )
        )
        return 1

    dates = sorted(set(series["RSP"].index) & set(series["SPY"].index) - have)
    if not dates:
        print(json.dumps({"status": "NO_NEW_ROWS", "last": last}, ensure_ascii=False))
        return 0

    rows = []
    for day in dates:
        rsp = float(series["RSP"].loc[day])
        spy = float(series["SPY"].loc[day])
        row = {
            "date": day,
            "RSP": round(rsp, 4),
            "SPY": round(spy, 4),
            "NYSE_comp": round(float(series["NYSE_comp"].loc[day]), 4)
            if "NYSE_comp" in series and day in series["NYSE_comp"].index
            else "",
            "SP400_mid": round(float(series["SP400_mid"].loc[day]), 4)
            if "SP400_mid" in series and day in series["SP400_mid"].index
            else "",
            "RSP_SPY_ratio": round(rsp / spy, 6) if spy else "",
        }
        rows.append(row)

    merged = pd.concat([existing, pd.DataFrame(rows, columns=COLUMNS)], ignore_index=True)
    merged = merged.drop_duplicates(subset=["date"], keep="first").sort_values("date")
    merged.to_csv(target, index=False)
    print(
        json.dumps(
            {
                "status": "APPENDED",
                "added": len(rows),
                "from": dates[0],
                "to": dates[-1],
                "total_rows": int(len(merged)),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
