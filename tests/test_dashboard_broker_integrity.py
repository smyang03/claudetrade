from __future__ import annotations

from dashboard import dashboard_server


def test_position_merge_preserves_broker_integrity_status() -> None:
    broker_rows = [
        {
            "ticker": "EL",
            "market": "US",
            "qty": 1,
            "avg_price": 87.8,
            "current_price": 91.0,
            "strategy": "broker_balance",
        }
    ]
    live_rows = [
        {
            "ticker": "EL",
            "market": "US",
            "qty": 1,
            "strategy": "claude_price",
            "path_type": "claude_price",
            "pathb_path_run_id": "path_el",
            "broker_reconcile_status": "broker_missing_unconfirmed",
            "position_integrity": "protected",
            "management_protected": True,
            "manual_reconciliation_required": True,
            "broker_missing_seen_count": 1,
        }
    ]

    merged = dashboard_server._merge_positions_for_display("US", broker_rows, live_rows, broker_ok=True)

    assert len(merged) == 1
    pos = merged[0]
    assert pos["pathb_path_run_id"] == "path_el"
    assert pos["broker_reconcile_status"] == "broker_missing_unconfirmed"
    assert pos["position_integrity"] == "protected"
    assert pos["management_protected"] is True
    assert pos["manual_reconciliation_required"] is True
    assert pos["broker_missing_seen_count"] == 1
