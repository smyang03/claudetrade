from __future__ import annotations

from typing import Any

from lifecycle.models import PathType, make_path_run_id


def make_claude_price_path_run_id(market: str, session_date: str, ticker: str) -> str:
    return make_path_run_id(PathType.CLAUDE_PRICE, market, session_date, ticker)


def attach_path_context(
    payload: dict[str, Any] | None,
    *,
    path_type: str,
    path_run_id: str,
    parent_decision_id: str,
    path_status: str = "",
) -> dict[str, Any]:
    merged = dict(payload or {})
    merged["path_type"] = str(path_type)
    merged["path_run_id"] = str(path_run_id)
    merged["parent_decision_id"] = str(parent_decision_id)
    if path_status:
        merged["path_status"] = str(path_status)
    return merged


def extract_path_context(payload: dict[str, Any] | None) -> dict[str, str]:
    data = payload or {}
    return {
        "path_type": str(data.get("path_type", "") or ""),
        "path_run_id": str(data.get("path_run_id", "") or ""),
        "parent_decision_id": str(data.get("parent_decision_id", "") or ""),
        "path_status": str(data.get("path_status", "") or ""),
    }
