from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd

from tools.candidate_consensus_shadow import _append_unique_jsonl, _arm_specs


def test_consensus_arm_feature_families_are_disjoint() -> None:
    columns = {
        "prompt_rank": [1],
        "raw_rank": [1],
        "trainer_prompt_score": [0.5],
        "daily_ret_1d_pct": [1.0],
        "daily_ret_5d_pct": [2.0],
        "sq_candidate_quality_score": [70.0],
    }
    specs = _arm_specs(pd.DataFrame(columns))

    for market in ("US", "KR"):
        left = set(specs[market]["left"]["features"])
        right = set(specs[market]["right"]["features"])
        assert left
        assert right
        assert left.isdisjoint(right)
        assert specs[market]["left"]["model_kind"] != specs[market]["right"]["model_kind"]


def test_shadow_ledger_is_append_only_and_idempotent_by_event_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "shadow.jsonl"
        row = {
            "event_id": "event1",
            "decision": "ABSTAIN",
            "authority": "SHADOW_ONLY_NO_ORDER_AUTHORITY",
        }
        assert _append_unique_jsonl(path, [row]) == 1
        assert _append_unique_jsonl(path, [row]) == 0
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert records == [row]
