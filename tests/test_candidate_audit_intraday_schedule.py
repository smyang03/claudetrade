from __future__ import annotations

from unittest.mock import patch

import trading_bot


def test_candidate_audit_outcome_update_runs_once_per_configured_bucket() -> None:
    bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
    elapsed = {"value": 30.0}
    bot.is_paper = False
    bot._runtime_bool = lambda _key, default=False: default
    bot._runtime_int = lambda _key, default=0: default
    bot._market_open_elapsed_min = lambda _market: elapsed["value"]
    bot._current_session_date_str = lambda _market: "2026-05-08"
    bot._write_funnel_event = lambda *_args, **_kwargs: None

    summary = {
        "candidate_rows": 1,
        "outcome_rows": 2,
        "status_counts": {"audit_sparse": 2},
        "last_success_at": "2026-05-08T01:30:00+00:00",
        "next_due_at": "",
        "outcome_health": "ok",
    }
    with patch("tools.update_candidate_audit_outcomes.update_candidate_audit_outcomes", return_value=summary) as update:
        bot._maybe_update_candidate_audit_outcomes_intraday("KR")
        elapsed["value"] = 34.9
        bot._maybe_update_candidate_audit_outcomes_intraday("KR")
        elapsed["value"] = 35.0
        bot._maybe_update_candidate_audit_outcomes_intraday("KR")

    assert update.call_count == 2
    assert update.call_args.kwargs["horizons"] == (30, 60)


def test_candidate_audit_outcome_update_waits_for_minimum_horizon() -> None:
    bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
    bot.is_paper = False
    bot._runtime_bool = lambda _key, default=False: default
    bot._runtime_int = lambda _key, default=0: default
    bot._market_open_elapsed_min = lambda _market: 29.9

    with patch("tools.update_candidate_audit_outcomes.update_candidate_audit_outcomes") as update:
        bot._maybe_update_candidate_audit_outcomes_intraday("KR")

    update.assert_not_called()
