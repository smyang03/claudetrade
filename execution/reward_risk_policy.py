from __future__ import annotations

"""Single source of truth for Path B minimum reward/risk policy."""

import os
from collections.abc import Callable
from typing import Any


REWARD_RISK_POLICY_VERSION = "pathb_rr_v2"
DEFAULT_MIN_REWARD_RISK = 1.5


def _valid_positive_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def resolve_min_reward_risk(
    market: str,
    *,
    value_getter: Callable[[str, Any], Any] | None = None,
    default: float = DEFAULT_MIN_REWARD_RISK,
) -> float:
    """Resolve market override -> global value -> default.

    All judgment, registration, submit, and prompt paths use PATHB_* keys.  A
    caller may supply the runtime-config getter so JSON/env precedence remains
    owned by the bot's effective configuration.
    """

    getter = value_getter or (lambda key, fallback=None: os.getenv(key, fallback))
    market_key = "US" if str(market or "").upper() == "US" else "KR"
    for key in (f"PATHB_MIN_REWARD_RISK_{market_key}", "PATHB_MIN_REWARD_RISK"):
        parsed = _valid_positive_float(getter(key, None))
        if parsed is not None:
            return parsed
    return float(default)
