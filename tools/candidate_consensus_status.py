from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from runtime_paths import get_runtime_path


def candidate_consensus_status_path(kind: str, market: str = "") -> Path:
    status_kind = "outcome" if str(kind or "").lower() == "outcome" else "shadow"
    market_key = str(market or "").upper()
    suffix = f"_{market_key}" if market_key in {"KR", "US"} else ""
    return get_runtime_path(
        "state",
        f"candidate_consensus_{status_kind}_status{suffix}.json",
        make_parents=False,
    )


def write_candidate_consensus_status(
    payload: dict[str, Any],
    *,
    kind: str,
    markets: Iterable[str],
    primary_path: Path | None = None,
    write_market_copy: bool = True,
) -> list[Path]:
    normalized_markets = sorted(
        {
            str(market or "").upper()
            for market in markets
            if str(market or "").upper() in {"KR", "US"}
        }
    )
    paths = [primary_path or candidate_consensus_status_path(kind)]
    if write_market_copy and len(normalized_markets) == 1:
        paths.append(candidate_consensus_status_path(kind, normalized_markets[0]))

    written: list[Path] = []
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    for path in dict.fromkeys(paths):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(body, encoding="utf-8")
        temp.replace(path)
        written.append(path)
    return written
