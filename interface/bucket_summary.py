from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
import json

from bot.bucket_classifier import classify_candidate_bucket
from runtime_paths import get_runtime_path


CANDIDATE_DISPLAY_LIMIT = 30


def build_bucket_summary(
    *,
    market: str | None = None,
    session_date: str | None = None,
    runtime_mode: str | None = None,
    log_dir: str | Path | None = None,
    allow_judgment_fallback: bool = True,
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    date_key = _date_key(session_date)
    directory = Path(log_dir) if log_dir else get_runtime_path("logs", "screener_quality", make_parents=False)
    markets = [market_key] if market_key else ["KR", "US"]
    files = [directory / f"{date_key}_{mkt}_candidates.jsonl" for mkt in markets]

    rows: list[dict[str, Any]] = []
    existing_files: list[str] = []
    for path in files:
        if not path.exists():
            continue
        existing_files.append(str(path))
        rows.extend(_read_jsonl(path))

    source = "screener_quality"
    if not rows and allow_judgment_fallback:
        fallback_rows, fallback_files = _rows_from_daily_judgment(markets, date_key, runtime_mode)
        rows.extend(fallback_rows)
        existing_files.extend(fallback_files)
        if rows:
            source = "daily_judgment_fallback"

    if not rows:
        return {
            "ok": True,
            "market": market_key or "ALL",
            "session_date": _display_date(date_key),
            "source_files": existing_files,
            "source": "none",
            "row_count": 0,
            "missing": True,
            "message": "후보 바구니 데이터가 아직 없습니다.",
            "buckets": [],
            "candidates": [],
            "warnings": ["NO_BUCKET_DATA"],
        }

    latest_by_bucket_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    latest_by_ticker: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        mkt = str(row.get("market") or "").upper()
        ticker = _norm_ticker(mkt, row.get("ticker"))
        primary = str(row.get("primary_bucket") or row.get("bucket") or "unclassified")
        bucket_key = (mkt, ticker, primary)
        ticker_key = (mkt, ticker)
        if _newer(row, latest_by_bucket_key.get(bucket_key)):
            latest_by_bucket_key[bucket_key] = row
        if _newer(row, latest_by_ticker.get(ticker_key)):
            latest_by_ticker[ticker_key] = row

    bucket_rows = list(latest_by_bucket_key.values())
    buckets = _aggregate_buckets(bucket_rows)
    candidate_cards_all = [_candidate_card(row) for row in sorted(latest_by_ticker.values(), key=_row_ts, reverse=True)]
    candidates = candidate_cards_all[:CANDIDATE_DISPLAY_LIMIT]
    warnings = _warnings(rows, buckets, market_key)
    return {
        "ok": True,
        "market": market_key or "ALL",
        "session_date": _display_date(date_key),
        "source_files": existing_files,
        "source": source,
        "row_count": len(rows),
        "unique_candidate_count": len(latest_by_ticker),
        "candidate_display_limit": CANDIDATE_DISPLAY_LIMIT,
        "candidate_total_count": len(candidate_cards_all),
        "missing": False,
        "buckets": buckets,
        "candidates": candidates,
        "warnings": warnings,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def _aggregate_buckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("primary_bucket") or "unclassified")].append(row)

    out: list[dict[str, Any]] = []
    for bucket, bucket_rows in grouped.items():
        statuses = defaultdict(int)
        for row in bucket_rows:
            statuses[str(row.get("status") or "UNKNOWN")] += 1
        out.append(
            {
                "primary_bucket": bucket,
                "candidates": len(bucket_rows),
                "claude_input": sum(1 for row in bucket_rows if bool(row.get("input_to_claude"))),
                "watch": statuses.get("WATCH", 0),
                "trade_ready": statuses.get("TRADE_READY", 0),
                "not_in_prompt": statuses.get("NOT_IN_PROMPT", 0),
                "screener_only": statuses.get("SCREENER_ONLY", 0),
                "path_a_entries": 0,
                "path_b_entries": 0,
                "winner_30m": _winner_count(bucket_rows, "forward_30m_from_bucket", "KR", 2.0, "US", 1.0),
                "winner_60m": _winner_count(bucket_rows, "forward_60m_from_bucket", "KR", 3.0, "US", 1.5),
                "winner_close": _close_winner_count(bucket_rows),
                "missed_winner": _missed_winner_count(bucket_rows),
                "bad_signal": _bad_signal_count(bucket_rows),
                "avg_forward_30m": _avg(bucket_rows, "forward_30m_from_bucket"),
                "avg_forward_60m": _avg(bucket_rows, "forward_60m_from_bucket"),
                "avg_forward_close": _avg(bucket_rows, "forward_close_from_bucket"),
                "avg_max_runup_60m": _avg(bucket_rows, "max_runup_60m_from_bucket"),
                "avg_max_drawdown_60m": _avg(bucket_rows, "max_drawdown_60m_from_bucket"),
            }
        )
    out.sort(key=lambda item: (int(item.get("trade_ready") or 0), int(item.get("claude_input") or 0), int(item.get("candidates") or 0)), reverse=True)
    return out


def _candidate_card(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "market": str(row.get("market") or ""),
        "ticker": str(row.get("ticker") or ""),
        "name": str(row.get("name") or row.get("ticker") or ""),
        "display_ticker": _display_ticker(row.get("ticker"), row.get("name")),
        "phase": str(row.get("phase") or ""),
        "timestamp": str(row.get("timestamp") or ""),
        "price": row.get("price"),
        "change_rate": row.get("change_rate"),
        "turnover": row.get("turnover"),
        "volume_ratio": row.get("volume_ratio"),
        "primary_bucket": str(row.get("primary_bucket") or "unclassified"),
        "secondary_buckets": row.get("secondary_buckets") if isinstance(row.get("secondary_buckets"), list) else [],
        "bucket_reasons": row.get("bucket_reasons") if isinstance(row.get("bucket_reasons"), dict) else {},
        "bucket_data_gaps": row.get("bucket_data_gaps") if isinstance(row.get("bucket_data_gaps"), list) else [],
        "first_bucket_detected_at": str(row.get("first_bucket_detected_at") or ""),
        "last_bucket_detected_at": str(row.get("last_bucket_detected_at") or ""),
        "bucket_seen_count": int(float(row.get("bucket_seen_count") or 0)),
        "status": str(row.get("status") or ""),
        "input_to_claude": bool(row.get("input_to_claude")),
        "excluded_reason": str(row.get("excluded_reason") or ""),
        "reason": str(row.get("reason") or ""),
    }


def _warnings(rows: list[dict[str, Any]], buckets: list[dict[str, Any]], market: str) -> list[str]:
    warnings: list[str] = []
    markets = {str(row.get("market") or "").upper() for row in rows}
    if market == "KR" or (not market and "KR" in markets):
        kr_rows = [row for row in rows if str(row.get("market") or "").upper() == "KR"]
        has_market_type = any(str(row.get("market_type") or "").strip() for row in kr_rows)
        has_kosdaq = any(str(row.get("market_type") or "").upper() == "KOSDAQ" for row in kr_rows)
        if kr_rows and not has_market_type:
            warnings.append("MARKET_TYPE_MISSING")
        elif kr_rows and not has_kosdaq:
            warnings.append("KOSDAQ_BUCKET_ZERO")
    for bucket in buckets:
        if bucket.get("primary_bucket") == "pre_move_setup" and int(bucket.get("not_in_prompt") or 0) > 0:
            warnings.append("PRE_MOVE_NOT_IN_PROMPT")
        if bucket.get("primary_bucket") == "momentum_now" and int(bucket.get("bad_signal") or 0) > 0:
            warnings.append("MOMENTUM_BAD_SIGNAL")
    return sorted(set(warnings))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    except Exception:
        return rows
    return rows


def _rows_from_daily_judgment(markets: list[str], date_key: str, runtime_mode: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    files: list[str] = []
    mode = str(runtime_mode or "live").lower()
    for market in markets:
        path = _daily_judgment_path(market, date_key, mode)
        if path is None:
            continue
        files.append(str(path))
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
        except Exception:
            continue
        rows.extend(_rows_from_judgment_record(data, market))
    return rows, files


def _daily_judgment_path(market: str, date_key: str, mode: str) -> Path | None:
    candidates = [
        get_runtime_path("logs", "daily_judgment", f"{mode}_{date_key}_{market}.json", make_parents=False),
        get_runtime_path("logs", "daily_judgment", f"live_{date_key}_{market}.json", make_parents=False),
        get_runtime_path("logs", "daily_judgment", f"{date_key}_{market}.json", make_parents=False),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _rows_from_judgment_record(data: dict[str, Any], market: str) -> list[dict[str, Any]]:
    market_key = str(market or data.get("market") or "").upper()
    digest = data.get("digest_raw") if isinstance(data.get("digest_raw"), dict) else {}
    technicals = digest.get("technicals") if isinstance(digest.get("technicals"), dict) else {}
    meta = data.get("selection_meta") if isinstance(data.get("selection_meta"), dict) else {}
    watchlist = {_norm_ticker(market_key, ticker) for ticker in (meta.get("watchlist") or data.get("tickers") or [])}
    trade_ready = {_norm_ticker(market_key, ticker) for ticker in (meta.get("trade_ready") or data.get("trade_ready_tickers") or [])}
    veto = {_norm_ticker(market_key, ticker) for ticker in ((meta.get("veto") or {}).keys())} if isinstance(meta.get("veto"), dict) else set()
    universe = [_norm_ticker(market_key, ticker) for ticker in (data.get("universe_tickers") or watchlist or technicals.keys())]
    ts = str(digest.get("built_at") or data.get("date") or datetime.now().isoformat(timespec="seconds"))
    output: list[dict[str, Any]] = []
    for ticker in universe:
        if not ticker:
            continue
        tech = technicals.get(ticker) or technicals.get(ticker.upper()) or {}
        if not isinstance(tech, dict):
            tech = {}
        candidate = {
            "ticker": ticker,
            "name": tech.get("name") or ticker,
            "price": tech.get("close") or tech.get("price") or 0,
            "change_rate": tech.get("change_pct") or tech.get("change_rate") or 0,
            "volume": tech.get("volume") or 0,
            "vol_ratio": tech.get("vol_ratio") or 1.0,
            "market_type": tech.get("market_type") or "",
            "category": "daily_judgment_fallback",
            "above_ma60": tech.get("above_ma60"),
            "from_high_pct": _pos_52w_to_from_high_pct(tech.get("pos_52w")),
            "recent_strength_pct": tech.get("recent_strength_pct") or tech.get("rs_5d") or tech.get("rs_3d"),
            "sector_strength_pct": tech.get("sector_strength_pct"),
        }
        bucket = classify_candidate_bucket(candidate, market_key)
        if ticker in trade_ready:
            status = "TRADE_READY"
        elif ticker in veto:
            status = "VETO"
        elif ticker in watchlist:
            status = "WATCH"
        else:
            status = "SCREENER_ONLY"
        output.append(
            {
                "timestamp": ts,
                "market": market_key,
                "phase": "daily_judgment_fallback",
                "ticker": ticker,
                "name": str(candidate.get("name") or ticker),
                "price": candidate.get("price"),
                "change_rate": candidate.get("change_rate"),
                "turnover": 0,
                "volume_ratio": candidate.get("vol_ratio"),
                "bucket": "",
                "status": status,
                "input_to_claude": True,
                "reason": str((meta.get("reasons") or {}).get(ticker, "")) if isinstance(meta.get("reasons"), dict) else "",
                "excluded_reason": "",
                "market_type": str(candidate.get("market_type") or ""),
                "category": "daily_judgment_fallback",
                "sector": str(tech.get("sector") or ""),
                "first_bucket_detected_at": ts,
                "last_bucket_detected_at": ts,
                "bucket_seen_count": 1,
                "earliest_bucket_detected_at": ts,
                **bucket,
                "forward_30m_from_bucket": None,
                "forward_60m_from_bucket": None,
                "forward_close_from_bucket": None,
                "max_runup_30m_from_bucket": None,
                "max_runup_60m_from_bucket": None,
                "max_runup_close_from_bucket": None,
                "max_drawdown_60m_from_bucket": None,
            }
        )
    return output


def _pos_52w_to_from_high_pct(value: Any) -> float | None:
    pct = _to_float(value)
    if pct is None:
        return None
    if 0 <= pct <= 1:
        return round((pct - 1.0) * 100.0, 4)
    if 0 <= pct <= 100:
        return round(pct - 100.0, 4)
    return None


def _winner_count(rows: list[dict[str, Any]], field: str, kr_key: str, kr_threshold: float, us_key: str, us_threshold: float) -> int:
    count = 0
    for row in rows:
        value = _to_float(row.get(field))
        if value is None:
            continue
        threshold = us_threshold if str(row.get("market") or "").upper() == us_key else kr_threshold
        if value >= threshold:
            count += 1
    return count


def _close_winner_count(rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        market = str(row.get("market") or "").upper()
        close = _to_float(row.get("forward_close_from_bucket"))
        runup = _to_float(row.get("max_runup_close_from_bucket"))
        close_threshold = 1.5 if market == "US" else 3.0
        runup_threshold = 2.5 if market == "US" else 5.0
        if (close is not None and close >= close_threshold) or (runup is not None and runup >= runup_threshold):
            count += 1
    return count


def _missed_winner_count(rows: list[dict[str, Any]]) -> int:
    missed = 0
    for row in rows:
        if str(row.get("status") or "") not in {"NOT_IN_PROMPT", "SCREENER_ONLY", "WATCH"}:
            continue
        if _row_is_winner(row):
            missed += 1
    return missed


def _bad_signal_count(rows: list[dict[str, Any]]) -> int:
    bad = 0
    for row in rows:
        if str(row.get("status") or "") not in {"TRADE_READY", "WATCH"} and not bool(row.get("input_to_claude")):
            continue
        value = _to_float(row.get("forward_60m_from_bucket"))
        if value is not None and value <= -1.0:
            bad += 1
    return bad


def _row_is_winner(row: dict[str, Any]) -> bool:
    market = str(row.get("market") or "").upper()
    f30 = _to_float(row.get("forward_30m_from_bucket"))
    f60 = _to_float(row.get("forward_60m_from_bucket"))
    close = _to_float(row.get("forward_close_from_bucket"))
    runup = _to_float(row.get("max_runup_close_from_bucket"))
    if market == "US":
        return bool(
            (f30 is not None and f30 >= 1.0)
            or (f60 is not None and f60 >= 1.5)
            or (close is not None and close >= 1.5)
            or (runup is not None and runup >= 2.5)
        )
    return bool(
        (f30 is not None and f30 >= 2.0)
        or (f60 is not None and f60 >= 3.0)
        or (close is not None and close >= 3.0)
        or (runup is not None and runup >= 5.0)
    )


def _avg(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [_to_float(row.get(field)) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _date_key(session_date: str | None) -> str:
    raw = str(session_date or "").strip()
    if not raw:
        return datetime.now().strftime("%Y%m%d")
    return raw.replace("-", "")[:8]


def _display_date(date_key: str) -> str:
    if len(date_key) == 8:
        return f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"
    return date_key


def _norm_ticker(market: str, ticker: Any) -> str:
    raw = str(ticker or "").strip()
    return raw.upper() if market == "US" else raw


def _newer(row: dict[str, Any], current: dict[str, Any] | None) -> bool:
    if current is None:
        return True
    return _row_ts(row) >= _row_ts(current)


def _row_ts(row: dict[str, Any]) -> str:
    return str(row.get("timestamp") or row.get("last_bucket_detected_at") or "")


def _display_ticker(ticker: Any, name: Any) -> str:
    ticker_text = str(ticker or "").strip().upper()
    name_text = str(name or "").strip()
    if ticker_text and name_text and name_text.upper() != ticker_text:
        return f"{name_text} ({ticker_text})"
    return ticker_text or name_text or "-"


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
