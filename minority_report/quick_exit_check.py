from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

try:
    import anthropic
except Exception:  # pragma: no cover - optional in offline tests
    anthropic = None  # type: ignore

sys.path.insert(0, str(Path(__file__).parent.parent))

from credit_tracker import record as credit_record
from logger import get_trading_logger
from minority_report.claude_utils import extract_json
from minority_report.raw_call_logger import save as save_raw_call
from minority_report.prompt_contracts import COMMON_DECISION_CONTRACT, HARD_SOFT_RULE_CONTRACT


log = get_trading_logger()
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")


def _client(timeout_sec: float):
    if anthropic is None:
        raise RuntimeError("anthropic package unavailable")
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    try:
        return anthropic.Anthropic(api_key=api_key, timeout=float(timeout_sec or 8.0))
    except TypeError:
        return anthropic.Anthropic(api_key=api_key)


def _fallback(reason: str) -> dict[str, Any]:
    return {
        "action": "HOLD",
        "confidence": 0.0,
        "reason": reason[:500],
        "protective_stop": 0.0,
        "fallback": True,
    }


def quick_exit_check(
    *,
    ticker: str,
    market: str,
    current_price: float,
    entry_price: float,
    reference_target: float,
    reference_stop: float = 0.0,
    mfe_pct: float = 0.0,
    pnl_pct: float = 0.0,
    exit_reason: str = "",
    strategy: str = "",
    market_mode: str = "",
    timeout_sec: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Single-call HOLD/SELL check for soft exits.

    Any failure returns HOLD so soft exits do not become blind sells.
    """
    timeout = float(timeout_sec if timeout_sec is not None else os.getenv("SOFT_EXIT_CLAUDE_TIMEOUT_SEC", "8"))
    tokens = int(max_tokens if max_tokens is not None else os.getenv("SOFT_EXIT_CLAUDE_MAX_TOKENS", "220"))
    ticker_key = str(ticker or "").strip().upper()
    market_key = str(market or "").strip().upper()

    if float(current_price or 0) <= 0 or float(entry_price or 0) <= 0:
        return _fallback("missing_price")
    if float(reference_target or 0) <= 0:
        return _fallback("missing_reference_target")

    prompt = f"""You are a fast exit arbiter for an automated trading system.

{COMMON_DECISION_CONTRACT}
{HARD_SOFT_RULE_CONTRACT}

Return a strict JSON object only. Decide whether this soft-exit sell should proceed now.

Position:
- decision_stage: SOFT_EXIT
- ticker: {ticker_key}
- market: {market_key}
- strategy: {strategy}
- soft_exit_reason: {exit_reason}
- market_mode: {market_mode}
- entry_price: {float(entry_price):.4f}
- current_price: {float(current_price):.4f}
- pnl_pct: {float(pnl_pct):+.3f}
- max_favorable_excursion_pct: {float(mfe_pct):+.3f}
- reference_target: {float(reference_target):.4f}
- reference_stop: {float(reference_stop or 0):.4f}

Rules:
- SELL if upside is no longer compelling, risk has deteriorated, or the soft exit should be respected.
- HOLD only if the target thesis is still valid and risk is controlled.
- If HOLD, provide a protective_stop at or above entry when possible.

JSON schema:
{{
  "action": "HOLD" or "SELL",
  "confidence": 0.0,
  "protective_stop": 0.0,
  "reason": "short reason"
}}"""

    try:
        client = _client(timeout)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        parsed = extract_json(raw)
        usage = getattr(resp, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        if input_tokens or output_tokens:
            credit_record(
                input_tokens, output_tokens, "quick_exit_check", model=MODEL,
                cache_creation_input_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
                cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            )
        save_raw_call(
            label="quick_exit_check",
            prompt=prompt,
            raw_response=raw,
            parsed=parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            market=market_key,
            model=MODEL,
            prompt_version="quick_exit_v2",
        )
        action = str(parsed.get("action", "SELL") or "SELL").strip().upper()
        if action not in {"HOLD", "SELL"}:
            return _fallback("invalid_action")
        return {
            "action": action,
            "confidence": max(0.0, min(1.0, float(parsed.get("confidence", 0.0) or 0.0))),
            "protective_stop": max(0.0, float(parsed.get("protective_stop", 0.0) or 0.0)),
            "reason": str(parsed.get("reason", "") or "")[:500],
            "fallback": False,
        }
    except Exception as exc:
        log.warning(f"[quick_exit_check] fallback HOLD {ticker_key}: {exc}")
        return _fallback(str(exc))
