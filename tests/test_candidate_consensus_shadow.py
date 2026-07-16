from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd

from tools.candidate_consensus_shadow import (
    _append_unique_jsonl,
    _arm_specs,
    _consensus_decision,
    _serveable_features,
)


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


def test_feature_overlap_forces_abstain_even_when_both_arms_select() -> None:
    decision, reason = _consensus_decision(
        left_selected=True,
        right_selected=True,
        shared_features=["shared_score"],
    )

    assert decision == "ABSTAIN"
    assert reason == "feature_overlap_fail_closed"


def test_all_missing_or_unavailable_features_are_dropped_before_fit() -> None:
    train = pd.DataFrame(
        {
            "usable": [1.0, 2.0],
            "train_only": [3.0, 4.0],
            "all_missing": [None, None],
            "category": ["a", "b"],
        }
    )
    serve = pd.DataFrame(
        {
            "usable": [5.0],
            "train_only": [None],
            "all_missing": [None],
            "category": ["a"],
        }
    )

    available, dropped = _serveable_features(
        train,
        serve,
        ["usable", "train_only", "all_missing", "category", "absent"],
        ["category"],
    )

    assert available == ["usable", "category"]
    assert dropped == ["train_only", "all_missing", "absent"]
