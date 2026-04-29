"""Intraday data capability probe.

The probe records what an intraday provider can actually return. It is designed
to run separately from live trading and can be tested with a mock downloader.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from .data_quality import normalize_ohlcv_frame


DownloadFunc = Callable[..., pd.DataFrame]

DEFAULT_PERIOD_BY_INTERVAL = {
    "5m": "730d",
    "15m": "730d",
    "30m": "730d",
    "60m": "730d",
}


def _default_downloader(**kwargs) -> pd.DataFrame:
    try:
        import yfinance as yf  # type: ignore
    except ImportError as exc:
        raise RuntimeError("yfinance package is not installed") from exc
    return yf.download(
        kwargs["symbol"],
        period=kwargs.get("period", "730d"),
        interval=kwargs.get("interval", "5m"),
        auto_adjust=kwargs.get("auto_adjust", True),
        progress=False,
    )


def _date_str(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def probe_intraday_capability(
    symbols: Iterable[str],
    *,
    intervals: Iterable[str] = ("5m", "15m"),
    downloader: DownloadFunc | None = None,
    period_by_interval: dict[str, str] | None = None,
    auto_adjust: bool = True,
) -> list[dict]:
    download = downloader or _default_downloader
    periods = {**DEFAULT_PERIOD_BY_INTERVAL, **(period_by_interval or {})}
    rows: list[dict] = []
    for symbol in symbols:
        for interval in intervals:
            period = periods.get(interval, "730d")
            try:
                raw = download(symbol=symbol, period=period, interval=interval, auto_adjust=auto_adjust)
                frame = normalize_ohlcv_frame(raw)
                if frame.empty:
                    rows.append(
                        {
                            "symbol": symbol,
                            "interval": interval,
                            "period_requested": period,
                            "status": "empty",
                            "rows": 0,
                            "start_date": "",
                            "end_date": "",
                            "observed_days": 0,
                            "note": "empty DataFrame returned",
                        }
                    )
                    continue
                start_date = _date_str(frame["date"].min())
                end_date = _date_str(frame["date"].max())
                observed_days = int((pd.to_datetime(end_date) - pd.to_datetime(start_date)).days) if start_date and end_date else 0
                note = "YFINANCE_INTRADAY_LIMIT_EXPECTED" if observed_days <= 760 and interval in {"5m", "15m"} else ""
                rows.append(
                    {
                        "symbol": symbol,
                        "interval": interval,
                        "period_requested": period,
                        "status": "ok",
                        "rows": len(frame),
                        "start_date": start_date,
                        "end_date": end_date,
                        "observed_days": observed_days,
                        "note": note,
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "symbol": symbol,
                        "interval": interval,
                        "period_requested": period,
                        "status": "failed",
                        "rows": 0,
                        "start_date": "",
                        "end_date": "",
                        "observed_days": 0,
                        "note": str(exc),
                    }
                )
    return rows


def write_intraday_capability(rows: list[dict], output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": rows,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
