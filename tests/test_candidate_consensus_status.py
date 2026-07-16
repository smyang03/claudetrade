from __future__ import annotations

import json
from pathlib import Path

from tools.candidate_consensus_status import write_candidate_consensus_status


def test_status_writer_keeps_global_and_market_specific_views(tmp_path: Path) -> None:
    primary = tmp_path / "candidate_consensus_shadow_status.json"
    market_path = tmp_path / "candidate_consensus_shadow_status_US.json"

    from unittest.mock import patch

    with patch(
        "tools.candidate_consensus_status.candidate_consensus_status_path",
        side_effect=lambda kind, market="": market_path if market else primary,
    ):
        written = write_candidate_consensus_status(
            {"status": "OK", "markets": ["US"], "updated_at": "2026-07-16T12:00:00+00:00"},
            kind="shadow",
            markets=["US"],
        )

    assert written == [primary, market_path]
    assert json.loads(primary.read_text(encoding="utf-8"))["markets"] == ["US"]
    assert json.loads(market_path.read_text(encoding="utf-8"))["status"] == "OK"


def test_status_writer_does_not_publish_aggregate_as_market_specific(tmp_path: Path) -> None:
    primary = tmp_path / "candidate_consensus_shadow_status.json"

    written = write_candidate_consensus_status(
        {"status": "OK", "markets": ["KR", "US"]},
        kind="shadow",
        markets=["KR", "US"],
        primary_path=primary,
    )

    assert written == [primary]
