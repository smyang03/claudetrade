from __future__ import annotations

import threading
import unittest
from datetime import datetime
from unittest.mock import patch

from runtime.intraday_minute_cache import IntradayMinuteCache
from trading_bot import KST, TradingBot


class _RuntimeConfig:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.values.get(key, default)
        return str(value).lower() in {"1", "true", "yes", "y", "on"} if isinstance(value, str) else bool(value)

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self.values.get(key, default))

    def get_int(self, key: str, default: int = 0) -> int:
        return int(float(self.values.get(key, default)))


class _Health:
    def state_for(self, ticker: str) -> dict:
        return {"ticker": ticker, "health_state": "OBSERVE", "ready_count": 0}


def _candles(prefix: str = "2026-05-13T09") -> list[dict]:
    return [
        {"ts": f"{prefix}:00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 100},
        {"ts": f"{prefix}:01:00", "open": 100, "high": 102, "low": 100, "close": 101, "volume": 100},
        {"ts": f"{prefix}:02:00", "open": 101, "high": 103, "low": 101, "close": 102, "volume": 100},
        {"ts": f"{prefix}:03:00", "open": 102, "high": 104, "low": 102, "close": 103, "volume": 100},
        {"ts": f"{prefix}:04:00", "open": 103, "high": 105, "low": 103, "close": 104, "volume": 100},
        {"ts": f"{prefix}:05:00", "open": 104, "high": 106, "low": 104, "close": 105, "volume": 100},
        {"ts": f"{prefix}:06:00", "open": 105, "high": 107, "low": 105, "close": 107, "volume": 100},
    ]


def _us_warmup_candles() -> list[dict]:
    return [
        {"ts": f"2026-05-13T22:{minute:02d}:00", "open": 100 + idx, "high": 101 + idx, "low": 99 + idx, "close": 100 + idx, "volume": 100}
        for idx, minute in enumerate(range(30, 36))
    ]


class _USWarmupDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = datetime(2026, 5, 13, 22, 35, tzinfo=tz)
        return value

    @classmethod
    def fromisoformat(cls, value: str):
        return datetime.fromisoformat(value)


def _make_bot(provider, market: str = "KR") -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    provider_name = "fake"
    bot.runtime_config = _RuntimeConfig(
        {
            "INTRADAY_EVIDENCE_ENABLED": True,
            "INTRADAY_EVIDENCE_MARKETS": "KR,US",
            f"INTRADAY_EVIDENCE_PROVIDER_{market}": provider_name,
            "INTRADAY_EVIDENCE_CACHE_TTL_SEC": 30,
            "INTRADAY_EVIDENCE_PREFETCH_TIMEOUT_SEC": 4,
            "INTRADAY_EVIDENCE_MAX_TICKERS": 30,
            "INTRADAY_EVIDENCE_FAIL_CLOSED": True,
            "KR_INTRADAY_KIS_MAX_WORKERS": 1,
            "KR_INTRADAY_KIS_MIN_CALL_INTERVAL_SEC": 0,
            "ENABLE_POST_OPEN_FEATURES_SHADOW": True,
        }
    )
    bot._intraday_minute_cache = IntradayMinuteCache(provider=provider, provider_name=provider_name, ttl_sec=30)
    bot._last_post_open_features_by_ticker = {"KR": {}, "US": {}}
    bot._post_open_price_history = {}
    bot._post_open_anchor = {}
    bot._post_open_feature_last_emit = {}
    bot._intraday_high = {}
    bot._intraday_low = {}
    bot._or_high = {}
    bot._or_low = {}
    bot._or_formed = {}
    bot.today_judgment = {"digest_raw": {"technicals": {}}, "consensus": {"confidence": 0.6}}
    bot._ca_context_last = None
    bot.enable_continuation_live = False
    bot._market_elapsed_min = lambda _market: 6.0
    bot._minutes_to_close = lambda _market: 300.0
    bot._in_entry_blackout = lambda _market: False
    bot._current_session_date_str = lambda _market: "2026-05-13"
    bot._market_regular_open_dt = lambda _market, session_date=None, now_dt=None: (
        datetime(2026, 5, 13, 22, 30, tzinfo=KST)
        if str(_market).upper() == "US"
        else datetime(2026, 5, 13, 9, 0, tzinfo=KST)
    )
    bot._token_for_market = lambda _market: None
    bot._candidate_health_tracker = lambda _market: _Health()
    bot._candidate_stale_cycle_info = lambda *_args, **_kwargs: {}
    bot._write_funnel_event = lambda event, market_key, payload: setattr(bot, "_last_funnel_event", (event, market_key, payload))
    return bot


class TradingBotIntradayEvidenceTests(unittest.TestCase):
    def test_annotation_prefetch_populates_post_open_features(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles())

        rows = TradingBot._annotate_selection_execution_features(
            bot,
            "KR",
            [{"ticker": "005930", "price": 107, "volume": 700, "vol_ratio": 3.0}],
            "NEUTRAL",
        )

        features = rows[0]["post_open_features"]
        self.assertEqual(features["data_quality"], "minute_complete")
        self.assertAlmostEqual(features["ret_5m_pct"], 5.0)
        self.assertEqual(bot._last_post_open_features_by_ticker["KR"]["005930"]["data_quality"], "minute_complete")
        self.assertEqual(bot._last_funnel_event[0], "selection_intraday_evidence_coverage")

    def test_minute_complete_feature_updates_or_cache(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles())

        TradingBot._merge_last_post_open_features(
            bot,
            "KR",
            {
                "005930": {
                    "data_quality": "minute_complete",
                    "opening_range_high": 104.0,
                    "opening_range_low": 99.0,
                }
            },
        )

        self.assertTrue(bot._or_formed["005930"])
        self.assertEqual(bot._or_high["005930"], 104.0)
        self.assertEqual(bot._or_low["005930"], 99.0)

    def test_partial_feature_does_not_form_or_cache(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles())

        TradingBot._merge_last_post_open_features(
            bot,
            "KR",
            {
                "005930": {
                    "data_quality": "minute_partial",
                    "opening_range_high": 104.0,
                    "opening_range_low": 99.0,
                }
            },
        )

        self.assertNotIn("005930", bot._or_formed)

    def test_existing_formed_or_cache_is_not_overwritten_by_feature(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles())
        bot._or_high = {"005930": 110.0}
        bot._or_low = {"005930": 100.0}
        bot._or_formed = {"005930": True}

        TradingBot._merge_last_post_open_features(
            bot,
            "KR",
            {
                "005930": {
                    "data_quality": "minute_complete",
                    "opening_range_high": 104.0,
                    "opening_range_low": 99.0,
                }
            },
        )

        self.assertEqual(bot._or_high["005930"], 110.0)
        self.assertEqual(bot._or_low["005930"], 100.0)

    def test_prefetch_uses_phase_target_limit_and_candidate_priority(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles())
        bot.runtime_config.values["INTRADAY_EVIDENCE_MAX_TICKERS"] = 2

        rows = TradingBot._annotate_selection_execution_features(
            bot,
            "KR",
            [
                {"ticker": "111111", "action": "WATCH", "confidence": 0.99},
                {"ticker": "222222", "action": "BUY_READY", "confidence": 0.50},
                {"ticker": "333333", "action": "PULLBACK_WAIT", "confidence": 0.40},
            ],
            "NEUTRAL",
        )

        event, market, payload = bot._last_funnel_event
        self.assertEqual(event, "selection_intraday_evidence_coverage")
        self.assertEqual(market, "KR")
        self.assertEqual(payload["target_limit"], 2)
        self.assertEqual(payload["target_tickers_sample"], ["222222", "333333"])
        self.assertEqual(payload["priority_counts"]["entry_ready"], 1)
        self.assertEqual(payload["priority_counts"]["pathb_wait"], 1)
        requested_features = {
            row["ticker"]: row.get("post_open_features")
            for row in rows
            if row.get("post_open_features")
        }
        self.assertIn("222222", requested_features)
        self.assertEqual(requested_features["222222"]["evidence_requested_count"], 2)
        self.assertNotIn("111111", requested_features)

    def test_prefetch_failure_does_not_erase_same_session_feature(self) -> None:
        def provider(**kwargs):
            raise RuntimeError("down")

        bot = _make_bot(provider, market="US")
        bot.runtime_config.values["INTRADAY_EVIDENCE_FAIL_CLOSED"] = False
        bot._last_post_open_features_by_ticker = {
            "US": {
                "AAPL": {
                    "ticker": "AAPL",
                    "market": "US",
                    "known_at": "2026-05-13T22:36:00",
                    "current_price": 107.0,
                    "ret_3m_pct": 3.0,
                    "ret_5m_pct": 5.0,
                    "opening_range_break": True,
                    "vwap_distance_pct": 1.0,
                    "volume_ratio_open": 2.0,
                    "data_quality": "minute_complete",
                }
            }
        }

        rows = TradingBot._annotate_selection_execution_features(bot, "US", [{"ticker": "aapl"}], "NEUTRAL")

        self.assertEqual(rows[0]["post_open_features"]["data_quality"], "minute_complete")
        self.assertEqual(bot._last_post_open_features_by_ticker["US"]["AAPL"]["ret_5m_pct"], 5.0)

    def test_stale_previous_session_feature_is_removed_for_candidate(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles())
        bot.runtime_config.values["INTRADAY_EVIDENCE_ENABLED"] = False
        bot._last_post_open_features_by_ticker = {
            "KR": {
                "005930": {
                    "ticker": "005930",
                    "known_at": "2026-05-12T09:06:00",
                    "data_quality": "minute_complete",
                }
            }
        }

        rows = TradingBot._annotate_selection_execution_features(bot, "KR", [{"ticker": "005930"}], "NEUTRAL")

        self.assertNotIn("post_open_features", rows[0])
        self.assertNotIn("005930", bot._last_post_open_features_by_ticker["KR"])

    def test_session_open_resolve_failure_is_logged_and_fail_closed(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles())
        bot._current_session_date_str = lambda _market: (_ for _ in ()).throw(RuntimeError("session broken"))

        rows = TradingBot._annotate_selection_execution_features(
            bot,
            "KR",
            [{"ticker": "005930", "price": 107, "volume": 700, "vol_ratio": 3.0}],
            "NEUTRAL",
        )

        features = rows[0]["post_open_features"]
        self.assertEqual(features["data_quality"], "minute_missing")
        self.assertTrue(features["fail_closed"])
        self.assertEqual(features["fail_closed_reason"], "session_open_resolve_failed")
        event, market, payload = bot._last_funnel_event
        self.assertEqual(event, "selection_intraday_evidence_coverage")
        self.assertEqual(market, "KR")
        self.assertEqual(payload["missing"], 1)
        self.assertTrue(payload["fail_closed_applied"])
        self.assertEqual(payload["blocked_or_missing_count"], 1)
        self.assertIn("session_open_resolve_failed", payload["errors_sample"][0])

    def test_us_opening_range_warmup_does_not_fail_close_partial_evidence(self) -> None:
        bot = _make_bot(lambda **kwargs: _us_warmup_candles(), market="US")
        bot.runtime_config.values["US_INTRADAY_EVIDENCE_MIN_COMPLETE_RATIO"] = 1.0

        with patch("trading_bot.datetime", _USWarmupDatetime):
            rows = TradingBot._annotate_selection_execution_features(
                bot,
                "US",
                [{"ticker": "aapl", "price": 105, "volume": 600, "vol_ratio": 1.0}],
                "NEUTRAL",
            )

        features = rows[0]["post_open_features"]
        self.assertEqual(features["data_quality"], "minute_partial")
        self.assertFalse(features.get("fail_closed", False))
        event, market, payload = bot._last_funnel_event
        self.assertEqual(event, "selection_intraday_evidence_coverage")
        self.assertEqual(market, "US")
        self.assertTrue(payload["warmup"])
        self.assertEqual(payload["opening_range_min"], 15)
        self.assertFalse(payload["fail_closed_applied"])
        self.assertEqual(payload["partial"], 1)
        self.assertEqual(payload["retry_due_at"], "2026-05-13T22:46:00")
        self.assertEqual(bot._intraday_evidence_retry_due_by_market["US"], "2026-05-13T22:46:00")

    def test_fail_closed_below_threshold_does_not_overwrite_partial_store(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles()[:1])
        bot.runtime_config.values["KR_INTRADAY_EVIDENCE_MIN_COMPLETE_RATIO"] = 1.0
        bot.runtime_config.values["INTRADAY_EVIDENCE_FAIL_CLOSED_REPLACE_STALE_SEC"] = 9999999
        bot._last_post_open_features_by_ticker = {
            "KR": {
                "005930": {
                    "ticker": "005930",
                    "market": "KR",
                    "known_at": "2026-05-13T09:01:00",
                    "current_price": 100.0,
                    "ret_3m_pct": None,
                    "ret_5m_pct": None,
                    "data_quality": "minute_partial",
                }
            },
            "US": {},
        }

        rows = TradingBot._annotate_selection_execution_features(
            bot,
            "KR",
            [{"ticker": "005930", "price": 100, "volume": 100, "vol_ratio": 1.0}],
            "NEUTRAL",
        )

        features = rows[0]["post_open_features"]
        self.assertEqual(features["data_quality"], "minute_missing")
        self.assertTrue(features["fail_closed"])
        self.assertEqual(features["fail_closed_reason"], "coverage_below_threshold")
        self.assertEqual(bot._last_post_open_features_by_ticker["KR"]["005930"]["data_quality"], "minute_partial")
        event, _market, payload = bot._last_funnel_event
        self.assertEqual(event, "selection_intraday_evidence_coverage")
        self.assertTrue(payload["fail_closed_applied"])
        self.assertEqual(payload["blocked_or_missing_count"], 1)

    def test_provider_disabled_fail_closed_returns_missing_sentinel(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles(), market="US")
        bot.runtime_config.values["INTRADAY_EVIDENCE_PROVIDER_US"] = "disabled"

        rows = TradingBot._annotate_selection_execution_features(
            bot,
            "US",
            [{"ticker": "aapl", "price": 190.0}],
            "NEUTRAL",
        )

        features = rows[0]["post_open_features"]
        self.assertEqual(features["ticker"], "AAPL")
        self.assertEqual(features["data_quality"], "minute_missing")
        self.assertEqual(features["fail_closed_reason"], "provider_disabled")
        self.assertEqual(features["runtime_gate_reason"], "provider_disabled")
        self.assertEqual(features["evidence_provider"], "disabled")
        event, market, payload = bot._last_funnel_event
        self.assertEqual(event, "selection_intraday_evidence_coverage")
        self.assertEqual(market, "US")
        self.assertTrue(payload["fail_closed_applied"])

    def test_provider_timeout_fail_closed_returns_missing_sentinel(self) -> None:
        release = threading.Event()

        def provider(**kwargs):
            release.wait(2.0)
            return _candles()

        bot = _make_bot(provider)
        bot.runtime_config.values["INTRADAY_EVIDENCE_PREFETCH_TIMEOUT_SEC"] = 0.1

        rows = TradingBot._annotate_selection_execution_features(
            bot,
            "KR",
            [{"ticker": "005930", "price": 100.0}],
            "NEUTRAL",
        )
        release.set()

        features = rows[0]["post_open_features"]
        self.assertEqual(features["data_quality"], "minute_missing")
        self.assertTrue(features["fail_closed"])
        event, market, payload = bot._last_funnel_event
        self.assertEqual(event, "selection_intraday_evidence_coverage")
        self.assertEqual(market, "KR")
        self.assertTrue(payload["fail_closed_applied"])
        self.assertIn("provider_timeout", payload["errors_sample"][0])

    def test_annotation_without_prefetch_still_applies_same_session_cache(self) -> None:
        def provider(**_kwargs):
            raise AssertionError("prefetch should be skipped")

        bot = _make_bot(provider)
        bot.runtime_config.values["ENABLE_POST_OPEN_FEATURES_SHADOW"] = False
        bot._last_post_open_features_by_ticker = {
            "KR": {
                "005930": {
                    "ticker": "005930",
                    "known_at": "2026-05-13T09:06:00",
                    "data_quality": "minute_complete",
                    "ret_5m_pct": 5.0,
                }
            },
            "US": {},
        }

        rows = TradingBot._annotate_selection_execution_features(
            bot,
            "KR",
            [{"ticker": "005930", "price": 100.0}],
            "NEUTRAL",
            prefetch_intraday_evidence=False,
        )

        self.assertEqual(rows[0]["post_open_features"]["data_quality"], "minute_complete")
        self.assertEqual(rows[0]["post_open_ret_5m_pct"], 5.0)

    def test_final_prompt_alignment_prefetches_trainer_prompt_ticker(self) -> None:
        events: list[tuple[str, str, dict]] = []
        bot = _make_bot(lambda **kwargs: _candles())
        session_day = datetime(2026, 5, 13).date()
        bot._current_session_date_str = lambda _market: "2026-05-13"
        bot._market_regular_open_dt = lambda _market, session_date=None, now_dt=None: datetime(
            session_day.year,
            session_day.month,
            session_day.day,
            9,
            0,
            tzinfo=KST,
        )
        bot.runtime_config.values.update(
            {
                "FINAL_PROMPT_EVIDENCE_ALIGNMENT_ENABLED": True,
                "EVIDENCE_PACK_ENABLED": True,
                "SELECTION_FULL_EVIDENCE_MAX": 5,
            }
        )
        bot._write_funnel_event = lambda event, market_key, payload: events.append((event, market_key, payload))
        bot._candidate_runtime_gate_info = lambda *_args, **_kwargs: {}
        bot._candidate_entry_timing_context = lambda *_args, **_kwargs: {"entry_timing_snapshot": {}}
        prompt_row = {"ticker": "333333", "market": "KR", "price": 107.0, "volume": 700, "vol_ratio": 3.0}

        with patch(
            "trading_bot.prepare_selection_prompt_pool",
            return_value=([dict(prompt_row)], {"prompt_pool": [dict(prompt_row)], "prompt_pool_count": 1}),
        ):
            candidates, prompt_rows, prompt_meta, evidence = TradingBot._prepare_selection_prompt_pool_with_evidence(
                bot,
                "KR",
                [
                    {"ticker": "111111", "market": "KR", "price": 100.0},
                    {"ticker": "222222", "market": "KR", "price": 100.0},
                    {"ticker": "333333", "market": "KR", "price": 107.0, "volume": 700, "vol_ratio": 3.0},
                ],
                "NEUTRAL",
            )

        coverage = next(payload for event, _market, payload in events if event == "selection_intraday_evidence_coverage")
        alignment = next(payload for event, _market, payload in events if event == "selection_final_prompt_evidence_alignment")
        self.assertEqual(coverage["target_tickers_sample"], ["333333"])
        self.assertEqual(alignment["prompt_tickers"], ["333333"])
        self.assertEqual(prompt_meta["evidence_prefetch_source"], "final_prompt_pool")
        self.assertEqual(prompt_meta["evidence_requested_tickers"], ["333333"])
        self.assertEqual(prompt_meta["evidence_prompt_overlap_ratio"], 1.0)
        self.assertEqual(prompt_meta["evidence_fetch_success_ratio"], 1.0)
        self.assertEqual(prompt_meta["evidence_fetch_success_tickers"], ["333333"])
        self.assertEqual([row["ticker"] for row in prompt_rows], ["333333"])
        self.assertEqual(prompt_rows[0]["post_open_features"]["data_quality"], "minute_complete")
        self.assertIn("333333", evidence)
        self.assertEqual(set(evidence), {"333333"})
        feature_by_ticker = {row["ticker"]: row.get("post_open_features") for row in candidates}
        self.assertEqual(feature_by_ticker["333333"]["data_quality"], "minute_complete")

    def test_final_prompt_alignment_boosts_mid_prefetch_to_warn_threshold(self) -> None:
        events: list[tuple[str, str, dict]] = []
        bot = _make_bot(lambda **kwargs: _candles(), market="US")
        bot._market_elapsed_min = lambda _market: 90.0
        bot._minutes_to_close = lambda _market: 180.0
        bot.runtime_config.values.update(
            {
                "FINAL_PROMPT_EVIDENCE_ALIGNMENT_ENABLED": True,
                "FINAL_PROMPT_EVIDENCE_ALIGNMENT_WARN_OVERLAP_MIN": 0.80,
                "EVIDENCE_PACK_ENABLED": True,
                "INTRADAY_EVIDENCE_MAX_TICKERS": 30,
            }
        )
        bot._write_funnel_event = lambda event, market_key, payload: events.append((event, market_key, payload))
        bot._candidate_runtime_gate_info = lambda *_args, **_kwargs: {}
        bot._candidate_entry_timing_context = lambda *_args, **_kwargs: {"entry_timing_snapshot": {}}
        prompt_rows = [
            {"ticker": f"T{i:02d}", "market": "US", "price": 100.0 + i, "volume": 1000 + i, "vol_ratio": 2.0}
            for i in range(35)
        ]

        with patch(
            "trading_bot.prepare_selection_prompt_pool",
            return_value=([dict(row) for row in prompt_rows], {"prompt_pool": [dict(row) for row in prompt_rows], "prompt_pool_count": 35}),
        ):
            _candidates, _prompt_rows, prompt_meta, _evidence = TradingBot._prepare_selection_prompt_pool_with_evidence(
                bot,
                "US",
                [dict(row) for row in prompt_rows],
                "NEUTRAL",
            )

        coverage = next(payload for event, _market, payload in events if event == "selection_intraday_evidence_coverage")
        self.assertEqual(coverage["target_limit"], 28)
        self.assertIn("alignment_min:28", coverage["target_rule"])
        self.assertEqual(coverage["requested"], 28)
        self.assertEqual(prompt_meta["evidence_alignment_min_target"], 28)
        self.assertEqual(prompt_meta["evidence_requested_count"], 28)
        self.assertEqual(prompt_meta["evidence_prompt_overlap_ratio"], 0.8)
        self.assertEqual(len(prompt_meta["missing_evidence_tickers"]), 7)

    def test_intraday_evidence_alignment_min_target_respects_global_cap(self) -> None:
        events: list[tuple[str, str, dict]] = []
        bot = _make_bot(lambda **kwargs: _candles(), market="US")
        bot._write_funnel_event = lambda event, market_key, payload: events.append((event, market_key, payload))
        bot.runtime_config.values["INTRADAY_EVIDENCE_MAX_TICKERS"] = 30
        rows = [
            {"ticker": f"T{i:02d}", "market": "US", "price": 100.0 + i, "volume": 1000 + i}
            for i in range(35)
        ]

        result = TradingBot._prefetch_selection_intraday_evidence(
            bot,
            "US",
            rows,
            phase={"phase": "mid"},
            min_target_limit=40,
        )

        coverage = next(payload for event, _market, payload in events if event == "selection_intraday_evidence_coverage")
        self.assertEqual(coverage["target_limit"], 30)
        self.assertEqual(coverage["requested"], 30)
        self.assertEqual(len(result), 30)

    def test_cached_intraday_evidence_replaces_stale_row_feature_with_missing_state(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles())
        bot._current_session_date_str = lambda _market: "2026-05-13"
        rows = TradingBot._apply_cached_intraday_evidence_to_rows(
            bot,
            "KR",
            [
                {
                    "ticker": "005930",
                    "post_open_features": {
                        "ticker": "005930",
                        "known_at": "2026-05-12T09:06:00",
                        "data_quality": "minute_complete",
                        "ret_5m_pct": 5.0,
                    },
                    "post_open_data_quality": "minute_complete",
                    "post_open_ret_5m_pct": 5.0,
                }
            ],
        )

        features = rows[0]["post_open_features"]
        self.assertEqual(features["data_quality"], "minute_missing")
        self.assertEqual(features["fail_closed_reason"], "stale_cached_feature")
        self.assertIsNone(rows[0].get("post_open_ret_5m_pct"))

    def test_final_prompt_alignment_disabled_keeps_legacy_candidate_prefetch(self) -> None:
        events: list[tuple[str, str, dict]] = []
        bot = _make_bot(lambda **kwargs: _candles())
        bot.runtime_config.values.update(
            {
                "FINAL_PROMPT_EVIDENCE_ALIGNMENT_ENABLED": False,
                "INTRADAY_EVIDENCE_MAX_TICKERS": 1,
                "EVIDENCE_PACK_ENABLED": True,
            }
        )
        bot._write_funnel_event = lambda event, market_key, payload: events.append((event, market_key, payload))
        bot._candidate_runtime_gate_info = lambda *_args, **_kwargs: {}
        bot._candidate_entry_timing_context = lambda *_args, **_kwargs: {"entry_timing_snapshot": {}}
        prompt_row = {"ticker": "333333", "market": "KR", "price": 107.0}

        with patch(
            "trading_bot.prepare_selection_prompt_pool",
            return_value=([dict(prompt_row)], {"prompt_pool": [dict(prompt_row)], "prompt_pool_count": 1}),
        ):
            _candidates, _prompt_rows, prompt_meta, _evidence = TradingBot._prepare_selection_prompt_pool_with_evidence(
                bot,
                "KR",
                [
                    {"ticker": "111111", "market": "KR", "price": 100.0},
                    {"ticker": "333333", "market": "KR", "price": 107.0},
                ],
                "NEUTRAL",
            )

        coverage = next(payload for event, _market, payload in events if event == "selection_intraday_evidence_coverage")
        self.assertEqual(coverage["target_tickers_sample"], ["111111"])
        self.assertEqual(prompt_meta["evidence_prefetch_source"], "legacy_candidates")


if __name__ == "__main__":
    unittest.main()
