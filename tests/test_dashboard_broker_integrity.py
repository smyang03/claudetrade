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


def test_position_merge_preserves_sell_signal_metadata_for_display() -> None:
    broker_rows = [
        {
            "ticker": "QCOM",
            "market": "US",
            "qty": 1,
            "avg_price": 106.0,
            "current_price": 111.0,
            "strategy": "broker_balance",
        }
    ]
    live_rows = [
        {
            "ticker": "QCOM",
            "market": "US",
            "qty": 1,
            "auto_sell_reviewed_at": "2026-06-03T23:10:00+09:00",
            "auto_sell_review_action": "SELL",
            "auto_sell_review_price_native": 111.25,
            "auto_sell_review_detail": "profit floor breached",
            "hold_advice": {
                "action": "SELL",
                "sell_urgency": "now",
                "revised_sell_target": 112.5,
                "reason": "momentum fading",
            },
        }
    ]

    merged = dashboard_server._merge_positions_for_display("US", broker_rows, live_rows, broker_ok=True)
    signal = dashboard_server._position_sell_signal(merged[0], "US")

    assert signal["active"] is True
    assert signal["source"] == "hold_advisor_sell"
    assert signal["label"] == "Claude 매도 신호"
    assert signal["price"] == 112.5
    assert signal["currency"] == "USD"
    assert signal["urgency"] == "now"
    assert "momentum fading" in signal["reason"]


def test_position_sell_signal_prefers_pending_sell_order_price() -> None:
    pos = {
        "ticker": "QCOM",
        "market": "US",
        "hold_advice": {"action": "SELL", "revised_sell_target": 112.5},
        "pathb_pending_sell_order_no": "sell-1",
        "pathb_pending_sell_qty": 2,
        "pathb_pending_sell_price": 111.75,
        "pathb_pending_sell_created_at": "2026-06-03T23:12:00+09:00",
        "pending_sell_reason": "profit_ladder",
    }

    signal = dashboard_server._position_sell_signal(pos, "US")

    assert signal["source"] == "pending_sell_order"
    assert signal["label"] == "매도 주문 들어감"
    assert signal["price"] == 111.75
    assert signal["qty"] == 2
    assert signal["order_no"] == "sell-1"


def test_position_sell_signal_uses_dashboard_pending_sell_control() -> None:
    signal = dashboard_server._position_sell_signal(
        {"ticker": "005930", "market": "KR"},
        "KR",
        pending_sell={
            "market": "KR",
            "ticker": "005930",
            "sell_price": 81200,
            "requested_at": "2026-06-03T11:02:00+09:00",
        },
    )

    assert signal["source"] == "dashboard_pending_sell"
    assert signal["label"] == "대시보드 매도 예약"
    assert signal["price"] == 81200
    assert signal["currency"] == "KRW"
