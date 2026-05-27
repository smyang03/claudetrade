from __future__ import annotations

import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
RISK_REASON = "KR_PATHB_RISK_ORIGIN_CONFIRMATION_REQUIRED"


def _make_candidate(ticker: str, **kwargs) -> dict:
    return {
        "ticker": ticker,
        "category": kwargs.pop("category", f"cat_{ticker}"),
        "sector": kwargs.pop("sector", f"sec_{ticker}"),
        **kwargs,
    }


def test_risk_origin_block_log_first_block_allowed():
    from runtime.pathb_runtime import PathBRuntime

    allowed = PathBRuntime._pathb_risk_origin_block_log_allowed(
        SimpleNamespace(),
        {},
        RISK_REASON,
        now=datetime(2026, 5, 27, 9, 0, 0, tzinfo=KST),
    )

    assert allowed is True


def test_risk_origin_block_log_suppressed_within_log_cooldown():
    from runtime.pathb_runtime import PathBRuntime

    now = datetime(2026, 5, 27, 9, 1, 0, tzinfo=KST)
    plan_data = {
        "last_submit_block_log_reason": RISK_REASON,
        "last_submit_block_log_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
        "last_submit_block_reason": RISK_REASON,
        "last_submit_block_at": now.isoformat(timespec="seconds"),
    }

    allowed = PathBRuntime._pathb_risk_origin_block_log_allowed(
        SimpleNamespace(),
        plan_data,
        RISK_REASON,
        now=now,
    )

    assert allowed is False


def test_risk_origin_block_log_reemits_after_log_cooldown_with_continuous_seen_updates():
    from runtime.pathb_runtime import PathBRuntime

    start = datetime(2026, 5, 27, 9, 0, 0, tzinfo=KST)
    plan_data = {
        "last_submit_block_log_reason": RISK_REASON,
        "last_submit_block_log_at": start.isoformat(timespec="seconds"),
        "last_submit_block_reason": RISK_REASON,
        "last_submit_block_at": start.isoformat(timespec="seconds"),
    }

    for seconds in range(10, 180, 10):
        now = start + timedelta(seconds=seconds)
        plan_data["last_submit_block_at"] = now.isoformat(timespec="seconds")
        assert (
            PathBRuntime._pathb_risk_origin_block_log_allowed(
                SimpleNamespace(),
                plan_data,
                RISK_REASON,
                now=now,
            )
            is False
        )

    now = start + timedelta(seconds=180)
    plan_data["last_submit_block_at"] = now.isoformat(timespec="seconds")
    assert (
        PathBRuntime._pathb_risk_origin_block_log_allowed(
            SimpleNamespace(),
            plan_data,
            RISK_REASON,
            now=now,
        )
        is True
    )


def test_risk_origin_block_log_different_reason_bypasses_cooldown():
    from runtime.pathb_runtime import PathBRuntime

    now = datetime(2026, 5, 27, 9, 1, 0, tzinfo=KST)
    plan_data = {
        "last_submit_block_log_reason": "SOME_OTHER_REASON",
        "last_submit_block_log_at": (now - timedelta(seconds=10)).isoformat(timespec="seconds"),
    }

    allowed = PathBRuntime._pathb_risk_origin_block_log_allowed(
        SimpleNamespace(),
        plan_data,
        RISK_REASON,
        now=now,
    )

    assert allowed is True


def test_risk_origin_block_log_uses_default_when_env_invalid(monkeypatch):
    from runtime.pathb_runtime import PathBRuntime

    monkeypatch.setenv("PATHB_RISK_ORIGIN_BLOCK_LOG_COOLDOWN_SEC", "bad")
    now = datetime(2026, 5, 27, 9, 1, 0, tzinfo=KST)
    plan_data = {
        "last_submit_block_log_reason": RISK_REASON,
        "last_submit_block_log_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
    }

    allowed = PathBRuntime._pathb_risk_origin_block_log_allowed(
        SimpleNamespace(),
        plan_data,
        RISK_REASON,
        now=now,
    )

    assert allowed is False


def test_risk_origin_block_log_zero_cooldown_logs_every_time(monkeypatch):
    from runtime.pathb_runtime import PathBRuntime

    monkeypatch.setenv("PATHB_RISK_ORIGIN_BLOCK_LOG_COOLDOWN_SEC", "0")
    now = datetime(2026, 5, 27, 9, 1, 0, tzinfo=KST)
    plan_data = {
        "last_submit_block_log_reason": RISK_REASON,
        "last_submit_block_log_at": now.isoformat(timespec="seconds"),
    }

    allowed = PathBRuntime._pathb_risk_origin_block_log_allowed(
        SimpleNamespace(),
        plan_data,
        RISK_REASON,
        now=now,
    )

    assert allowed is True


def test_curate_drops_same_day_stopped_when_cap_full():
    from minority_report.analysts import _curate_selection_candidates

    stopped = _make_candidate("005930", same_day_stopped=True)
    normals = [_make_candidate(f"00000{i}") for i in range(5)]

    result = _curate_selection_candidates(normals + [stopped], "KR", prompt_cap=5)

    tickers = [c["ticker"] for c in result]
    assert "005930" not in tickers
    assert len(result) == 5


def test_curate_includes_same_day_stopped_last_when_cap_not_filled():
    from minority_report.analysts import _curate_selection_candidates

    stopped = _make_candidate("005930", same_day_stopped=True)
    normals = [_make_candidate(f"00000{i}") for i in range(2)]

    result = _curate_selection_candidates([stopped] + normals, "KR", prompt_cap=10)

    tickers = [c["ticker"] for c in result]
    assert tickers[-1] == "005930"


def test_curate_defers_same_day_stopped_even_when_hard_pinned():
    from minority_report.analysts import _curate_selection_candidates

    stopped = _make_candidate(
        "005930",
        same_day_stopped=True,
        preopen_pinned=True,
        preopen_pin_tier="HARD",
        category="stopped_cat",
        sector="stopped_sec",
    )
    hard_ok = _make_candidate(
        "000660",
        preopen_pinned=True,
        preopen_pin_tier="HARD",
        category="hard_cat",
        sector="hard_sec",
    )
    normals = [
        _make_candidate("035420", category="normal_cat_1", sector="normal_sec_1"),
        _make_candidate("012345", category="normal_cat_2", sector="normal_sec_2"),
    ]

    result = _curate_selection_candidates([stopped, hard_ok] + normals, "KR", prompt_cap=3)

    tickers = [c["ticker"] for c in result]
    assert tickers[0] == "000660"
    assert "005930" not in tickers
    assert len(result) == 3


def test_push_same_day_stop_to_back_calls_trading_bot_method():
    from trading_bot import TradingBot

    bot = TradingBot.__new__(TradingBot)
    bot._v2_same_day_stop_tickers = {"KR": {"005930", "000660"}, "US": set()}
    candidates = [
        {"ticker": "005930"},
        {"ticker": "035420"},
        {"ticker": "000660"},
        {"ticker": "012345"},
    ]

    result = TradingBot._push_same_day_stop_to_back(bot, "KR", candidates)

    assert [c["ticker"] for c in result] == ["035420", "012345", "005930", "000660"]
    assert result[2]["same_day_stopped"] is True
    assert result[3]["same_day_stopped"] is True
    assert "same_day_stopped" not in candidates[0]


def _make_mock_plan(ticker: str = "005930", market: str = "KR"):
    plan = MagicMock()
    plan.ticker = ticker
    plan.market = market
    plan.path_run_id = "run-001"
    return plan


def _make_pathb_with_bot(feature_quality: str = "first_observed", cache_result: dict | None = None):
    from runtime.pathb_runtime import PathBRuntime

    bot = MagicMock()
    bot._last_post_open_features_by_ticker = {
        "KR": {
            "005930": {"data_quality": feature_quality, "current_price": 70000.0}
        }
    }
    bot._market_regular_open_dt.return_value = datetime(2026, 5, 27, 9, 0, 0, tzinfo=KST)
    bot._current_session_date_str.return_value = "20260527"
    bot._token_for_market.return_value = "dummy_token"

    cache = MagicMock()
    cache.get_many.return_value = {"features_by_ticker": dict(cache_result or {})}
    bot._ensure_intraday_minute_cache.return_value = cache

    merge_results = {}

    def _merge(_market_key, incoming):
        merge_results.update(incoming)
        return merge_results

    bot._merge_last_post_open_features.side_effect = _merge

    runtime = MagicMock(spec=PathBRuntime)
    runtime.bot = bot
    runtime._kr_pathb_feature_upgrade_at = {}

    return runtime, bot, cache, merge_results


def test_upgrade_skips_when_already_complete():
    from runtime.pathb_runtime import PathBRuntime

    runtime, _bot, cache, _ = _make_pathb_with_bot(feature_quality="minute_complete")
    plan = _make_mock_plan()

    PathBRuntime._kr_try_upgrade_post_open_features(runtime, plan)

    cache.get_many.assert_not_called()


def test_upgrade_skips_when_disabled():
    from runtime.pathb_runtime import PathBRuntime

    runtime, _bot, cache, _ = _make_pathb_with_bot(feature_quality="first_observed")
    plan = _make_mock_plan()
    runtime._runtime_bool = lambda key, default: False if key == "KR_PATHB_FEATURE_UPGRADE_ENABLED" else default

    PathBRuntime._kr_try_upgrade_post_open_features(runtime, plan)

    cache.get_many.assert_not_called()


def test_upgrade_calls_cache_for_first_observed():
    from runtime.pathb_runtime import PathBRuntime

    runtime, _bot, cache, _ = _make_pathb_with_bot(
        feature_quality="first_observed",
        cache_result={
            "005930": {
                "data_quality": "minute_complete",
                "current_price": 70000.0,
                "ret_3m_pct": 0.5,
                "opening_range_break": True,
                "vwap_distance_pct": 0.1,
                "volume_ratio_open": 1.5,
                "bar_count": 15,
            }
        },
    )
    plan = _make_mock_plan()
    runtime._runtime_bool = lambda _key, default: True
    runtime._ticker_key = lambda _market, ticker: ticker
    runtime._session_date = lambda _market: "20260527"

    PathBRuntime._kr_try_upgrade_post_open_features(runtime, plan)

    cache.get_many.assert_called_once()
    call_kwargs = cache.get_many.call_args[1]
    assert call_kwargs["market"] == "KR"
    assert "005930" in call_kwargs["tickers"]
    assert call_kwargs["opening_range_min"] == 10


def test_upgrade_merges_when_quality_improves():
    from runtime.pathb_runtime import PathBRuntime

    new_features = {
        "data_quality": "minute_complete",
        "current_price": 71000.0,
        "ret_3m_pct": 0.8,
        "bar_count": 20,
    }
    runtime, bot, _cache, _merge_results = _make_pathb_with_bot(
        feature_quality="first_observed",
        cache_result={"005930": new_features},
    )
    plan = _make_mock_plan()
    runtime._runtime_bool = lambda _key, default: True
    runtime._ticker_key = lambda _market, ticker: ticker
    runtime._session_date = lambda _market: "20260527"

    PathBRuntime._kr_try_upgrade_post_open_features(runtime, plan)

    bot._merge_last_post_open_features.assert_called_once()
    call_args = bot._merge_last_post_open_features.call_args
    assert call_args[0][0] == "KR"
    assert "005930" in call_args[0][1]


def test_upgrade_respects_cooldown():
    from runtime.pathb_runtime import PathBRuntime

    runtime, _bot, cache, _ = _make_pathb_with_bot(feature_quality="first_observed")
    plan = _make_mock_plan()
    runtime._runtime_bool = lambda _key, default: True
    runtime._ticker_key = lambda _market, ticker: ticker
    runtime._session_date = lambda _market: "20260527"
    runtime._kr_pathb_feature_upgrade_at = {"005930": time.time()}

    PathBRuntime._kr_try_upgrade_post_open_features(runtime, plan)

    cache.get_many.assert_not_called()


def test_upgrade_proceeds_after_cooldown():
    from runtime.pathb_runtime import PathBRuntime

    runtime, _bot, cache, _ = _make_pathb_with_bot(feature_quality="first_observed")
    plan = _make_mock_plan()
    runtime._runtime_bool = lambda _key, default: True
    runtime._ticker_key = lambda _market, ticker: ticker
    runtime._session_date = lambda _market: "20260527"
    runtime._kr_pathb_feature_upgrade_at = {"005930": time.time() - 200}

    PathBRuntime._kr_try_upgrade_post_open_features(runtime, plan)

    cache.get_many.assert_called_once()


def test_feature_upgrade_uses_default_when_env_invalid(monkeypatch):
    from runtime.pathb_runtime import PathBRuntime

    monkeypatch.setenv("KR_PATHB_FEATURE_UPGRADE_COOLDOWN_SEC", "bad")
    runtime, _bot, cache, _ = _make_pathb_with_bot(feature_quality="first_observed")
    plan = _make_mock_plan()
    runtime._runtime_bool = lambda _key, default: True
    runtime._ticker_key = lambda _market, ticker: ticker
    runtime._session_date = lambda _market: "20260527"
    runtime._kr_pathb_feature_upgrade_at = {"005930": time.time()}

    PathBRuntime._kr_try_upgrade_post_open_features(runtime, plan)

    cache.get_many.assert_not_called()


def test_upgrade_does_not_merge_on_missing_quality():
    from runtime.pathb_runtime import PathBRuntime

    runtime, bot, _cache, _ = _make_pathb_with_bot(
        feature_quality="first_observed",
        cache_result={"005930": {"data_quality": "minute_missing", "bar_count": 0}},
    )
    plan = _make_mock_plan()
    runtime._runtime_bool = lambda _key, default: True
    runtime._ticker_key = lambda _market, ticker: ticker
    runtime._session_date = lambda _market: "20260527"

    PathBRuntime._kr_try_upgrade_post_open_features(runtime, plan)

    bot._merge_last_post_open_features.assert_not_called()


def test_upgrade_does_not_merge_on_empty_result():
    from runtime.pathb_runtime import PathBRuntime

    runtime, bot, _cache, _ = _make_pathb_with_bot(
        feature_quality="first_observed",
        cache_result={},
    )
    plan = _make_mock_plan()
    runtime._runtime_bool = lambda _key, default: True
    runtime._ticker_key = lambda _market, ticker: ticker
    runtime._session_date = lambda _market: "20260527"

    PathBRuntime._kr_try_upgrade_post_open_features(runtime, plan)

    bot._merge_last_post_open_features.assert_not_called()
