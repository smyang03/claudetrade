from __future__ import annotations

from typing import Any


RUNTIME_ADJUSTMENT_BOUNDS: dict[str, tuple[float, float]] = {
    "momentum_wait_adjust_min": (-10, 10),
    "entry_priority_cutoff_adjust": (-0.05, 0.05),
    "kr_momentum_atr_cap_adjust": (-0.01, 0.02),
    "kr_momentum_atr_cap_high_adjust": (-0.01, 0.02),
}


def coerce_runtime_adjustments(result: dict[str, Any] | None, *, preserve_extra: bool = True) -> dict[str, Any]:
    source = dict(result or {})
    normalized = dict(source) if preserve_extra else {}
    for key, (low, high) in RUNTIME_ADJUSTMENT_BOUNDS.items():
        raw_value = source.get(key, 0)
        try:
            value = float(raw_value or 0)
        except (TypeError, ValueError):
            value = 0.0
        value = max(low, min(high, value))
        if key == "momentum_wait_adjust_min":
            normalized[key] = int(round(value))
        else:
            normalized[key] = round(value, 4)
    return normalized
