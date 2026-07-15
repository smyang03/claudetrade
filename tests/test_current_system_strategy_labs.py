from __future__ import annotations

from datetime import timezone

import pandas as pd
import pytest

from tools.current_system_profit_max_lab import parse_dt, session_key_kst, zone_flag
from tools.early_tier_shadow_review import tier_counterfactual
from tools.profit_path_tier_relabel_lab import folds, sequential_threshold
from tools.split_exit_runner_lab import _trimmed_sum, evaluate


def test_parse_dt_accepts_compact_offset_and_normalises_to_utc() -> None:
    parsed = parse_dt("2026-05-04T22:30:00+0900")

    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parsed.isoformat() == "2026-05-04T13:30:00+00:00"
    assert session_key_kst(parsed) == "2026-05-04"


def test_zone_flag_is_us_only_and_requires_upper_zone_high_reward() -> None:
    risky = {
        "buy_zone_low": 100.0,
        "buy_zone_high": 101.0,
        "hit_price": 100.8,
        "reward_pct": 5.5,
    }

    assert zone_flag(risky, "US") is True
    assert zone_flag(risky, "KR") is False
    assert zone_flag({**risky, "hit_price": 100.5}, "US") is False
    assert zone_flag({**risky, "reward_pct": 4.9}, "US") is False


def test_walk_forward_folds_purge_one_date_between_train_and_test() -> None:
    dates = [f"2026-04-{day:02d}" for day in range(1, 25)]
    generated = folds(dates, min_train=15, test_size=4)

    train, test, purge = generated[0]
    assert purge == dates[14]
    assert train == dates[:14]
    assert test == dates[15:19]
    assert purge not in train
    assert purge not in test
    assert max(train) < purge < min(test)


def test_sequential_threshold_uses_arrival_order_not_future_daily_rank() -> None:
    frame = pd.DataFrame(
        [
            {"session_date": "2026-05-04", "entry_ts": "09:00", "path_id": "1", "ticker_key": "A", "prediction": 0.80},
            {"session_date": "2026-05-04", "entry_ts": "09:01", "path_id": "2", "ticker_key": "B", "prediction": 0.85},
            {"session_date": "2026-05-04", "entry_ts": "09:02", "path_id": "3", "ticker_key": "C", "prediction": 0.99},
            {"session_date": "2026-05-04", "entry_ts": "09:03", "path_id": "4", "ticker_key": "A", "prediction": 1.00},
        ]
    )

    selected = sequential_threshold(frame, threshold=0.80, cap=2)

    assert selected["ticker_key"].tolist() == ["A", "B"]
    assert selected["path_id"].tolist() == ["1", "2"]


def test_split_exit_uses_integer_quantity_and_preserves_runner() -> None:
    trade = {"entry": 100.0, "qty": 3, "mfe": 5.0, "net": -2.0}

    result = tier_counterfactual(trade, target=110.0, level=3.6, f=0.5, cost=0.21)

    assert result is not None
    assert result["executable"] is True
    assert result["sell_qty"] == 1
    assert result["effective_f"] == 1 / 3
    expected = (1 / 3) * (3.6 - 0.21) + (2 / 3) * -2.0
    assert result["cf_net"] == pytest.approx(expected)


def test_split_exit_does_not_fake_partial_fill_for_single_share() -> None:
    trade = {"entry": 100.0, "qty": 1, "mfe": 5.0, "net": 1.25}

    result = tier_counterfactual(trade, target=110.0, level=3.6, f=0.5, cost=0.21)

    assert result is not None
    assert result["executable"] is False
    assert result["sell_qty"] == 0
    assert result["cf_net"] == trade["net"]


def test_split_exit_robustness_summary_applies_partial_fill_stress_only() -> None:
    trades = [
        {"did": "a", "market": "KR", "session_date": "2026-04-01", "entry": 100.0, "qty": 2, "mfe": 5.0, "net": -2.0},
        {"did": "b", "market": "KR", "session_date": "2026-04-02", "entry": 100.0, "qty": 1, "mfe": 5.0, "net": 1.0},
    ]
    summary, rows = evaluate(
        trades,
        {"a": 110.0, "b": 110.0},
        market="KR",
        level=3.6,
        fraction=0.5,
        extra_partial_slippage_pct=0.30,
    )

    assert summary["integer_executable_n"] == 1
    assert rows[0]["stressed_net_pct"] == pytest.approx(rows[0]["counterfactual_net_pct"] - 0.15)
    assert rows[1]["stressed_net_pct"] == rows[1]["actual_net_pct"]


def test_trimmed_sum_removes_both_tail_contributors() -> None:
    assert _trimmed_sum([-10.0, -2.0, 1.0, 4.0, 12.0], remove_top=1, remove_bottom=1) == 3.0
