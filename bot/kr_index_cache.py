from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from runtime_paths import get_runtime_path


INDEX_SYMBOLS = {
    "KOSPI": "^KS11",
    "KOSDAQ": "^KQ11",
}

IndexFetchFn = Callable[[str, int], pd.DataFrame]


def normalize_board(value: Any) -> str:
    text = str(value or "").strip().upper()
    if "KOSDAQ" in text or text in {"KQ", "1001"}:
        return "KOSDAQ"
    return "KOSPI"


def index_cache_path(board: str, *, path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    board_key = normalize_board(board).lower()
    return get_runtime_path("state", f"kr_index_history_{board_key}.json")


def load_kr_index_history(
    board: str,
    *,
    lookback_days: int = 120,
    max_age_sec: int = 18 * 60 * 60,
    path: str | Path | None = None,
    fetch_fn: IndexFetchFn | None = None,
) -> pd.DataFrame:
    board_key = normalize_board(board)
    cache_path = index_cache_path(board_key, path=path)
    cached = _read_cache(cache_path, board_key, max_age_sec=max_age_sec)
    if not cached.empty and len(cached) >= min(lookback_days, 20):
        return cached.tail(lookback_days).reset_index(drop=True)

    fetch = fetch_fn or _fetch_yfinance_index
    try:
        frame = _normalize_frame(fetch(board_key, lookback_days))
    except Exception:
        return cached.tail(lookback_days).reset_index(drop=True) if not cached.empty else pd.DataFrame()
    if frame.empty:
        return cached.tail(lookback_days).reset_index(drop=True) if not cached.empty else pd.DataFrame()
    _write_cache(cache_path, board_key, frame)
    return frame.tail(lookback_days).reset_index(drop=True)


def _fetch_yfinance_index(board: str, lookback_days: int) -> pd.DataFrame:
    import yfinance as yf

    symbol = INDEX_SYMBOLS[normalize_board(board)]
    period_days = max(int(lookback_days or 120) * 3, 120)
    hist = yf.Ticker(symbol).history(period=f"{period_days}d", auto_adjust=True)
    if hist is None or hist.empty:
        return pd.DataFrame()
    frame = hist.reset_index()
    frame.columns = [str(column).lower() for column in frame.columns]
    date_col = "date" if "date" in frame.columns else ("datetime" if "datetime" in frame.columns else "")
    if not date_col:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "date": pd.to_datetime(frame[date_col], errors="coerce").dt.tz_localize(None),
            "close": pd.to_numeric(frame.get("close"), errors="coerce"),
            "high": pd.to_numeric(frame.get("high", frame.get("close")), errors="coerce"),
            "volume": pd.to_numeric(frame.get("volume", 0), errors="coerce").fillna(0),
        }
    )


def _read_cache(path: Path, board: str, *, max_age_sec: int) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return pd.DataFrame()
    if not isinstance(payload, dict) or normalize_board(payload.get("board")) != normalize_board(board):
        return pd.DataFrame()
    cached_at = float(payload.get("cached_at") or 0.0)
    if max_age_sec > 0 and cached_at > 0 and time.time() - cached_at > max_age_sec:
        return pd.DataFrame()
    return _normalize_frame(payload.get("rows") or [])


def _write_cache(path: Path, board: str, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = _normalize_frame(frame)
    rows = []
    for item in clean.to_dict("records"):
        rows.append(
            {
                "date": str(pd.to_datetime(item.get("date")).date()) if item.get("date") is not None else "",
                "close": _float_or_none(item.get("close")),
                "high": _float_or_none(item.get("high")),
                "volume": _float_or_none(item.get("volume")),
            }
        )
    payload = {
        "schema_version": 1,
        "board": normalize_board(board),
        "cached_at": time.time(),
        "rows": rows,
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _normalize_frame(raw: Any) -> pd.DataFrame:
    if raw is None:
        return pd.DataFrame()
    try:
        frame = raw.copy() if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
    except Exception:
        return pd.DataFrame()
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame()
    frame.columns = [str(column).lower() for column in frame.columns]
    if "close" not in frame.columns:
        return pd.DataFrame()
    if "date" not in frame.columns:
        frame["date"] = pd.RangeIndex(len(frame))
    if "high" not in frame.columns:
        frame["high"] = frame["close"]
    if "volume" not in frame.columns:
        frame["volume"] = 0.0
    out = frame[["date", "close", "high", "volume"]].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for column in ("close", "high", "volume"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    return out.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def _float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed
