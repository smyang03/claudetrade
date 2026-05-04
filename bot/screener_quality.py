from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.bucket_classifier import annotate_candidates_with_bucket_metadata
from runtime_paths import get_runtime_path


def normalize_ticker(market: str, ticker: Any) -> str:
    raw = str(ticker or "").strip()
    return raw.upper() if str(market or "").upper() == "US" else raw


def write_candidate_quality_log(
    *,
    market: str,
    phase: str,
    raw_candidates: list[dict[str, Any]],
    prompt_candidates: list[dict[str, Any]],
    selected: list[str],
    selection_meta: dict[str, Any],
    reasons: dict[str, Any] | None = None,
    now: datetime | None = None,
    path: str | Path | None = None,
    bucket_state_path: str | Path | None = None,
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    ts = now or datetime.now()
    output_path = Path(path) if path else get_runtime_path(
        "logs",
        "screener_quality",
        f"{ts.strftime('%Y%m%d')}_{market_key}_candidates.jsonl",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    selected_set = {normalize_ticker(market_key, ticker) for ticker in selected or []}
    trade_ready = {normalize_ticker(market_key, ticker) for ticker in (selection_meta or {}).get("trade_ready", []) or []}
    watchlist = {normalize_ticker(market_key, ticker) for ticker in (selection_meta or {}).get("watchlist", []) or []}
    veto = {
        normalize_ticker(market_key, ticker): value
        for ticker, value in ((selection_meta or {}).get("veto", {}) or {}).items()
    }
    prompt_map = {
        normalize_ticker(market_key, row.get("ticker")): row
        for row in prompt_candidates or []
        if normalize_ticker(market_key, row.get("ticker"))
    }
    enriched_raw_candidates = annotate_candidates_with_bucket_metadata(
        list(raw_candidates or []),
        market=market_key,
        session_date=ts.date().isoformat(),
        detected_at=ts,
        state_path=bucket_state_path,
    )
    raw_map = {
        normalize_ticker(market_key, row.get("ticker")): row
        for row in enriched_raw_candidates or []
        if normalize_ticker(market_key, row.get("ticker"))
    }
    reason_map = {
        normalize_ticker(market_key, ticker): reason
        for ticker, reason in (reasons or {}).items()
    }

    rows: list[dict[str, Any]] = []
    for ticker, candidate in raw_map.items():
        input_to_claude = ticker in prompt_map
        if ticker in trade_ready:
            status = "TRADE_READY"
        elif ticker in veto:
            status = "VETO"
        elif ticker in watchlist or ticker in selected_set:
            status = "WATCH"
        elif not input_to_claude:
            status = "NOT_IN_PROMPT"
        else:
            status = "SCREENER_ONLY"
        rows.append(
            {
                "timestamp": ts.isoformat(timespec="seconds"),
                "market": market_key,
                "phase": str(phase or ""),
                "ticker": ticker,
                "name": str(candidate.get("name") or ticker),
                "price": _safe_float(candidate.get("price")),
                "change_rate": _safe_float(candidate.get("change_rate")),
                "turnover": _safe_float(candidate.get("turnover"), _safe_float(candidate.get("price")) * _safe_float(candidate.get("volume"))),
                "volume_ratio": _safe_float(candidate.get("vol_ratio")),
                "bucket": _bucket(candidate),
                "primary_bucket": str(candidate.get("primary_bucket") or "unclassified"),
                "secondary_buckets": list(candidate.get("secondary_buckets") or []),
                "bucket_reasons": candidate.get("bucket_reasons") if isinstance(candidate.get("bucket_reasons"), dict) else {},
                "bucket_data_gaps": list(candidate.get("bucket_data_gaps") or []),
                "first_bucket_detected_at": str(candidate.get("first_bucket_detected_at") or ""),
                "last_bucket_detected_at": str(candidate.get("last_bucket_detected_at") or ""),
                "bucket_seen_count": int(candidate.get("bucket_seen_count") or 0),
                "earliest_bucket_detected_at": str(candidate.get("earliest_bucket_detected_at") or ""),
                "score_current": _safe_float(candidate.get("score_current")),
                "score_vol_ratio_capped": _safe_float(candidate.get("score_vol_ratio_capped")),
                "score_vol_ratio_log": _safe_float(candidate.get("score_vol_ratio_log")),
                "score_turnover_weighted": _safe_float(candidate.get("score_turnover_weighted")),
                "forward_30m_from_bucket": None,
                "forward_60m_from_bucket": None,
                "forward_close_from_bucket": None,
                "max_runup_30m_from_bucket": None,
                "max_runup_60m_from_bucket": None,
                "max_runup_close_from_bucket": None,
                "max_drawdown_60m_from_bucket": None,
                "status": status,
                "input_to_claude": input_to_claude,
                "reason": str(reason_map.get(ticker) or ""),
                "excluded_reason": _excluded_reason(candidate, status, veto.get(ticker)),
                "market_type": str(candidate.get("market_type") or ""),
                "category": str(candidate.get("category") or ""),
                "sector": str(candidate.get("sector") or ""),
                "source": str(candidate.get("source") or ""),
                "data_quality": str(candidate.get("data_quality") or ""),
                "history_status": str(candidate.get("history_status") or ""),
                "history_usable_rows": int(candidate.get("history_usable_rows") or 0),
                "history_required_rows": int(candidate.get("history_required_rows") or 0),
                "screen_quality": str(candidate.get("screen_quality") or ""),
                "selection_bias": str(candidate.get("selection_bias") or ""),
                "trade_policy": str(candidate.get("trade_policy") or ""),
            }
        )

    with output_path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"path": str(output_path), "rows": len(rows), "counts": counts}


def opening_fresh_quality_metrics(
    *,
    market: str,
    raw_candidates: list[dict[str, Any]],
    prompt_tickers: list[str],
    current_trade_ready: list[str] | None = None,
    top_n: int = 20,
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    prompt_set = {normalize_ticker(market_key, ticker) for ticker in prompt_tickers or []}
    raw_by_ticker = {
        normalize_ticker(market_key, row.get("ticker")): row
        for row in raw_candidates or []
        if normalize_ticker(market_key, row.get("ticker"))
    }
    ranked = sorted(
        list(raw_candidates or []),
        key=_opening_rank_score,
        reverse=True,
    )[: max(1, int(top_n or 20))]
    top_tickers = [normalize_ticker(market_key, row.get("ticker")) for row in ranked if normalize_ticker(market_key, row.get("ticker"))]
    not_in_prompt = [ticker for ticker in top_tickers if ticker not in prompt_set]
    high_liq_not_in_prompt = []
    for row in ranked:
        ticker = normalize_ticker(market_key, row.get("ticker"))
        if not ticker or ticker in prompt_set:
            continue
        turnover = _safe_float(row.get("turnover"), _safe_float(row.get("price")) * _safe_float(row.get("volume")))
        if turnover >= 1_000_000_000:
            high_liq_not_in_prompt.append(ticker)
    coverage = 0.0 if not top_tickers else (len([ticker for ticker in top_tickers if ticker in prompt_set]) / len(top_tickers)) * 100.0
    weakened_trade_ready = []
    for ticker in current_trade_ready or []:
        norm = normalize_ticker(market_key, ticker)
        if not norm:
            continue
        row = raw_by_ticker.get(norm)
        if row is None:
            weakened_trade_ready.append(norm)
        elif _safe_float(row.get("change_rate")) <= 0.0:
            weakened_trade_ready.append(norm)
    trigger_reasons = []
    if len(not_in_prompt) >= 3:
        trigger_reasons.append("new_top_gainer_not_in_prompt>=3")
    if coverage < 50.0:
        trigger_reasons.append("top20_coverage<50")
    if len(high_liq_not_in_prompt) >= 2:
        trigger_reasons.append("new_high_liq_candidates>=2")
    if len(weakened_trade_ready) >= 2:
        trigger_reasons.append("existing_trade_ready_weakened>=2")
    return {
        "market": market_key,
        "top_n": len(top_tickers),
        "top20_coverage": round(coverage, 2),
        "not_in_prompt": len(not_in_prompt),
        "not_in_prompt_tickers": not_in_prompt,
        "new_high_liq_candidates": len(high_liq_not_in_prompt),
        "new_high_liq_tickers": high_liq_not_in_prompt,
        "existing_trade_ready_weakened": len(weakened_trade_ready),
        "existing_trade_ready_weakened_tickers": weakened_trade_ready,
        "judge_triggered": bool(trigger_reasons),
        "trigger_reason": ",".join(trigger_reasons) if trigger_reasons else "observe_only",
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value or "").replace(",", ""))
    except Exception:
        return float(default)


def _opening_rank_score(row: dict[str, Any]) -> float:
    if row.get("screen_score") not in (None, ""):
        return _safe_float(row.get("screen_score"))
    return _safe_float(row.get("change_rate"))


def _bucket(candidate: dict[str, Any]) -> str:
    pieces = []
    for key in ("market_type", "category", "liquidity_bucket", "from_high_bucket"):
        raw = str(candidate.get(key) or "").strip()
        if raw:
            pieces.append(raw)
    return "|".join(pieces)


def _excluded_reason(candidate: dict[str, Any], status: str, veto_value: Any) -> str:
    if status == "VETO":
        return str(veto_value or candidate.get("veto_reason") or "veto")
    if status == "NOT_IN_PROMPT":
        return str(candidate.get("excluded_reason") or "not_in_prompt")
    if status == "SCREENER_ONLY":
        return str(candidate.get("excluded_reason") or "not_selected_by_claude")
    return str(candidate.get("excluded_reason") or "")
