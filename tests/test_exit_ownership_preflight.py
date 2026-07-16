from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tools import live_preflight


def _snapshot(*, kr_positions=None, us_positions=None) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def market(positions):
        return {
            "missing": False,
            "stale": False,
            "last_success_at": now,
            "last_attempt_at": now,
            "ttl_sec": 180,
            "error": "",
            "positions": positions or [],
            "open_orders": [],
            "today_fills": [],
        }

    return {
        "generated_at": now,
        "runtime_mode": "live",
        "schema_version": 1,
        "markets": {
            "KR": market(kr_positions),
            "US": market(us_positions),
        },
    }


def _check(local_positions: list[dict], snapshot: dict) -> live_preflight.CheckResult:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "state"
        state.mkdir(parents=True)
        (state / "live_open_positions.json").write_text(
            json.dumps(local_positions),
            encoding="utf-8",
        )
        (state / "live_broker_truth_snapshot.json").write_text(
            json.dumps(snapshot),
            encoding="utf-8",
        )

        def fake_runtime_path(*parts: str, make_parents: bool = True) -> Path:
            return root.joinpath(*parts)

        with patch.object(live_preflight, "get_runtime_path", side_effect=fake_runtime_path):
            return live_preflight._position_exit_ownership_check("live")


def test_exit_ownership_passes_for_reconciled_plan_a_position() -> None:
    check = _check(
        [{"market": "US", "ticker": "PYPL", "qty": 2, "display_avg_price": 75.0}],
        _snapshot(us_positions=[{"ticker": "PYPL", "qty": 2}]),
    )

    assert check.status == "PASS"
    assert check.data["owners"][0]["owner"] == "plan_a_risk_manager"


def test_exit_ownership_fails_for_orphan_broker_position() -> None:
    check = _check(
        [],
        _snapshot(us_positions=[{"ticker": "PYPL", "qty": 2}]),
    )

    assert check.status == "FAIL"
    assert {row["reason"] for row in check.data["critical"]} == {"orphan_broker_position"}


def test_exit_ownership_fails_when_isolated_sleeve_has_generic_sell_state() -> None:
    check = _check(
        [
            {
                "market": "US",
                "ticker": "SCHG",
                "qty": 1,
                "display_avg_price": 30.0,
                "source_strategy": "us_schg_bil_trend_v1",
                "exit_owner": "us_schg_bil_trend_v1",
                "pending_next_open_sell": True,
            }
        ],
        _snapshot(us_positions=[{"ticker": "SCHG", "qty": 1}]),
    )

    assert check.status == "FAIL"
    finding = next(
        row
        for row in check.data["critical"]
        if row["reason"] == "isolated_owner_generic_exit_conflict"
    )
    assert "pending_next_open_sell" in finding["active_generic_exit_fields"]


def test_exit_ownership_warns_when_isolated_owner_metadata_is_inferred() -> None:
    check = _check(
        [
            {
                "market": "US",
                "ticker": "SCHG",
                "qty": 1,
                "display_avg_price": 30.0,
                "source_strategy": "us_schg_bil_trend_v1",
            }
        ],
        _snapshot(us_positions=[{"ticker": "SCHG", "qty": 1}]),
    )

    assert check.status == "WARN"
    assert check.data["warnings"][0]["reason"] == "exit_owner_metadata_inferred_from_source_strategy"


def test_exit_ownership_passes_flat_account() -> None:
    check = _check([], _snapshot())

    assert check.status == "PASS"
    assert check.data["local_position_count"] == 0
    assert check.data["broker_position_count"] == 0
