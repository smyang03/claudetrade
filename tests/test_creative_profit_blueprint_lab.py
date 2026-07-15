from __future__ import annotations

from datetime import timezone

from tools.creative_profit_blueprint_lab import metrics, one_slot, parse_dt, staged_net


def test_parse_dt_normalizes_to_utc() -> None:
    parsed = parse_dt("2026-07-15T09:30:00+09:00")
    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parsed.isoformat() == "2026-07-15T00:30:00+00:00"


def test_metrics_reports_tail_removal() -> None:
    result = metrics([10.0, 5.0, 2.0, -1.0, -2.0])
    assert result["n"] == 5
    assert result["sum_pct_units"] == 14.0
    assert result["sum_ex_top3_pct_units"] == -3.0


def test_one_slot_prohibits_overlapping_holds() -> None:
    rows = [
        {
            "entry_date": "2026-07-01",
            "exit_date_3d": "2026-07-03",
            "ticker": "A",
            "selection_rank": 2,
            "entry_priority_score": 1.0,
            "net_3d_pct": 1.0,
        },
        {
            "entry_date": "2026-07-01",
            "exit_date_3d": "2026-07-03",
            "ticker": "B",
            "selection_rank": 1,
            "entry_priority_score": 1.0,
            "net_3d_pct": 2.0,
        },
        {
            "entry_date": "2026-07-02",
            "exit_date_3d": "2026-07-06",
            "ticker": "C",
            "selection_rank": 1,
            "entry_priority_score": 1.0,
            "net_3d_pct": 3.0,
        },
        {
            "entry_date": "2026-07-06",
            "exit_date_3d": "2026-07-08",
            "ticker": "D",
            "selection_rank": 1,
            "entry_priority_score": 1.0,
            "net_3d_pct": 4.0,
        },
    ]
    accepted = one_slot(rows, 3)
    assert [row["ticker"] for row in accepted] == ["B", "D"]


def test_staged_net_uses_probe_only_when_unconfirmed() -> None:
    net, exposure = staged_net(
        actual_net=-3.0,
        entry_price=100.0,
        exit_price=97.5,
        fee_pct=0.5,
        residual_pct=0.0,
        add_price=None,
    )
    assert net == -1.0
    assert exposure == 1.0 / 3.0


def test_staged_net_prices_confirmed_add_at_trigger() -> None:
    net, exposure = staged_net(
        actual_net=4.5,
        entry_price=100.0,
        exit_price=105.0,
        fee_pct=0.5,
        residual_pct=0.0,
        add_price=101.0,
    )
    expected_add = (105.0 / 101.0 - 1.0) * 100.0 - 0.5
    assert abs(net - (4.5 / 3.0 + expected_add * 2.0 / 3.0)) < 1e-12
    assert exposure == 1.0
