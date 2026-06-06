from __future__ import annotations

import threading
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from runtime.intraday_minute_cache import IntradayMinuteCache
from runtime.post_open_features import append_feature_snapshot_payload
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


def _long_candles(prefix: str = "2026-05-13T09") -> list[dict]:
    return [
        {
            "ts": f"{prefix}:{minute:02d}:00",
            "open": 100 + minute * 0.1,
            "high": 101 + minute * 0.1,
            "low": 99 + minute * 0.1,
            "close": 100 + minute * 0.1,
            "volume": 1000 + minute,
        }
        for minute in range(80)
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

    def test_annotation_adds_strategy_feasibility_pack(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles())
        bot.runtime_config.values["KR_PLAN_A_ORP_SIGNAL_ENABLED"] = True
        bot._market_elapsed_min = lambda _market: 80.0
        bot._get_ohlcv_cached = lambda ticker, market: pd.DataFrame(_long_candles())  # type: ignore[method-assign]

        rows = TradingBot._annotate_selection_execution_features(
            bot,
            "KR",
            [{"ticker": "005930", "price": 107}],
            "MILD_BULL",
            prefetch_intraday_evidence=False,
        )

        pack = rows[0]["strategy_feasibility"]
        orp = pack["opening_range_pullback"]
        self.assertEqual(orp["action_ceiling"], "WATCH")
        self.assertEqual(orp["reason"], "orp_not_formed_after_window")
        self.assertEqual(orp["session_date"], "2026-05-13")
        self.assertEqual(orp["session_phase"], "mid")
        self.assertTrue(orp["hard_block"])
        self.assertTrue(orp["evidence_hash"])

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

    def test_partial_feature_with_or_data_does_form_or_cache(self) -> None:
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

        self.assertIn("005930", bot._or_formed)
        self.assertTrue(bot._or_formed["005930"])

    def test_missing_feature_does_not_form_or_cache(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles())

        TradingBot._merge_last_post_open_features(
            bot,
            "KR",
            {
                "005930": {
                    "data_quality": "minute_missing",
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

    def test_later_lower_quality_feature_does_not_overwrite_complete_evidence(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles())

        TradingBot._merge_last_post_open_features(
            bot,
            "KR",
            {
                "005930": {
                    "ticker": "005930",
                    "known_at": "2026-05-13T09:16:00",
                    "data_quality": "minute_complete",
                    "ret_5m_pct": 5.0,
                    "opening_range_break": True,
                    "vwap_distance_pct": 1.2,
                    "volume_ratio_open": 2.0,
                }
            },
        )
        TradingBot._merge_last_post_open_features(
            bot,
            "KR",
            {
                "005930": {
                    "ticker": "005930",
                    "known_at": "2026-05-13T09:17:00",
                    "data_quality": "first_observed",
                    "ret_5m_pct": None,
                    "opening_range_break": None,
                    "vwap_distance_pct": None,
                    "volume_ratio_open": None,
                }
            },
        )

        features = bot._last_post_open_features_by_ticker["KR"]["005930"]
        self.assertEqual(features["data_quality"], "minute_complete")
        self.assertTrue(features["opening_range_break"])
        self.assertEqual(features["volume_ratio_open"], 2.0)

    def test_startup_restore_loads_post_open_feature_jsonl_and_or_cache(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles())
        bot._last_post_open_features_by_ticker = {"KR": {}, "US": {}}
        bot._post_open_price_history = {}
        bot._post_open_anchor = {}
        bot._or_high = {}
        bot._or_low = {}
        bot._or_formed = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("runtime_paths._RUNTIME_ROOT", Path(tmpdir)):
                append_feature_snapshot_payload(
                    {
                        "market": "KR",
                        "ticker": "005930",
                        "known_at": "2026-05-13T09:16:00",
                        "anchor_at": "2026-05-13T09:00:00",
                        "anchor_price": 100.0,
                        "current_price": 106.0,
                        "data_quality": "minute_complete",
                        "ret_5m_pct": 5.0,
                        "opening_range_high": 104.0,
                        "opening_range_low": 99.0,
                        "opening_range_break": True,
                        "vwap_distance_pct": 1.0,
                        "volume_ratio_open": 2.0,
                    }
                )

                TradingBot._restore_post_open_features_from_jsonl(bot)

        features = bot._last_post_open_features_by_ticker["KR"]["005930"]
        self.assertEqual(features["data_quality"], "minute_complete")
        self.assertTrue(bot._or_formed["005930"])
        self.assertEqual(bot._or_high["005930"], 104.0)
        self.assertIn("KR:005930", bot._post_open_anchor)
        self.assertGreaterEqual(len(bot._post_open_price_history["KR:005930"]), 2)

    def test_jsonl_restore_does_not_overwrite_handoff_fail_closed_with_older_complete(self) -> None:
        bot = _make_bot(lambda **kwargs: _candles())
        bot._last_post_open_features_by_ticker = {
            "KR": {
                "005930": {
                    "ticker": "005930",
                    "market": "KR",
                    "known_at": "2026-05-13T15:05:00",
                    "data_quality": "minute_missing",
                    "fail_closed": True,
                    "evidence_status": "fail_closed",
                    "evidence_action_ceiling": "WATCH",
                }
            },
            "US": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("runtime_paths._RUNTIME_ROOT", Path(tmpdir)):
                append_feature_snapshot_payload(
                    {
                        "market": "KR",
                        "ticker": "005930",
                        "known_at": "2026-05-13T15:00:00",
                        "anchor_at": "2026-05-13T09:00:00",
                        "anchor_price": 100.0,
                        "current_price": 106.0,
                        "data_quality": "minute_complete",
                        "ret_5m_pct": 5.0,
                        "opening_range_high": 104.0,
                        "opening_range_low": 99.0,
                        "opening_range_break": True,
                    }
                )

                TradingBot._restore_post_open_features_from_jsonl(bot)

        features = bot._last_post_open_features_by_ticker["KR"]["005930"]
        self.assertEqual(features["known_at"], "2026-05-13T15:05:00")
        self.assertEqual(features["data_quality"], "minute_missing")
        self.assertTrue(features["fail_closed"])
        self.assertEqual(features["evidence_action_ceiling"], "WATCH")
        self.assertNotIn("KR:005930", bot._post_open_anchor)

    def test_runtime_handoff_snapshot_restores_volatile_selection_memory(self) -> None:
        source = _make_bot(lambda **kwargs: _candles())
        source.is_paper = False
        source.today_tickers = {"KR": ["005930"]}
        source.trade_ready_tickers = {"KR": ["005930"], "US": []}
        source.selection_meta = {"KR": {"selection_snapshot_ts": "2026-05-13T09:16:00"}, "US": {}}
        source.selection_stages = {"KR": {"applied": {"selected": ["005930"]}}, "US": {}}
        source.price_cache = {"005930": 106.0}
        source.price_cache_raw = {"005930": 106.0}
        source._ticker_no_signal_cycles = {"005930": 3}
        source._ticker_runtime_blocked_reasons = {"KR": {"005930": {"NO_SIGNAL": 2}}, "US": {}}
        source._ticker_runtime_rejection_reasons = {"KR": {"005930": {"WEAK_SIGNAL": 1}}, "US": {}}
        source._intraday_high = {"005930": 107.0}
        source._intraday_low = {"005930": 99.0}
        source._or_high = {"005930": 104.0}
        source._or_low = {"005930": 99.0}
        source._or_formed = {"005930": True}
        source._post_open_anchor = {
            "KR:005930": {
                "anchor_at": "2026-05-13T09:00:00",
                "anchor_price": 100.0,
                "anchor_source": "preopen_anchor",
            }
        }
        source._post_open_price_history = {
            "KR:005930": [
                {"ts": "2026-05-13T09:00:00", "price": 100.0, "source": "test"},
                {"ts": "2026-05-13T09:16:00", "price": 106.0, "source": "test"},
            ]
        }
        source._last_post_open_features_by_ticker = {
            "KR": {
                "005930": {
                    "ticker": "005930",
                    "known_at": "2026-05-13T09:16:00",
                    "anchor_at": "2026-05-13T09:00:00",
                    "current_price": 106.0,
                    "ret_5m_pct": 5.0,
                    "opening_range_break": True,
                    "vwap_distance_pct": 1.0,
                    "volume_ratio_open": 2.0,
                    "data_quality": "minute_complete",
                }
            },
            "US": {},
        }

        target = _make_bot(lambda **kwargs: _candles())
        target.is_paper = False
        target.today_tickers = {}
        target.trade_ready_tickers = {"KR": [], "US": []}
        target.selection_meta = {"KR": {}, "US": {}}
        target.selection_stages = {"KR": {}, "US": {}}
        target.price_cache = {}
        target.price_cache_raw = {}
        target._ticker_no_signal_cycles = {}
        target._ticker_runtime_blocked_reasons = {"KR": {}, "US": {}}
        target._ticker_runtime_rejection_reasons = {"KR": {}, "US": {}}
        target._intraday_high = {}
        target._intraday_low = {}
        target._or_high = {}
        target._or_low = {}
        target._or_formed = {}
        target._post_open_anchor = {}
        target._post_open_price_history = {}
        target._last_post_open_features_by_ticker = {"KR": {}, "US": {}}

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("runtime_paths._RUNTIME_ROOT", Path(tmpdir)):
                TradingBot._write_runtime_handoff_snapshot(source, "test_shutdown")
                TradingBot._restore_runtime_handoff_snapshot(target)

        self.assertEqual(target.today_tickers["KR"], ["005930"])
        self.assertEqual(target.trade_ready_tickers["KR"], ["005930"])
        self.assertEqual(target.price_cache["005930"], 106.0)
        self.assertTrue(target._or_formed["005930"])
        self.assertEqual(target._or_high["005930"], 104.0)
        self.assertIn("KR:005930", target._post_open_anchor)
        self.assertEqual(target._last_post_open_features_by_ticker["KR"]["005930"]["ret_5m_pct"], 5.0)
        TradingBot._ensure_runtime_selection_memory(target)
        self.assertEqual(target._ticker_no_signal_cycles["005930"], 3)
        self.assertEqual(target._ticker_runtime_blocked_reasons["KR"]["005930"]["NO_SIGNAL"], 2)
        self.assertEqual(target._ticker_runtime_rejection_reasons["KR"]["005930"]["WEAK_SIGNAL"], 1)
        self.assertEqual(target._last_funnel_event[0], "runtime_handoff_restore")

    def test_runtime_handoff_snapshot_skips_previous_session_market_state(self) -> None:
        source = _make_bot(lambda **kwargs: _candles())
        source.is_paper = False
        source.today_tickers = {"KR": ["005930"], "US": ["AAPL"]}
        source.trade_ready_tickers = {"KR": ["005930"], "US": ["AAPL"]}
        source.selection_meta = {"KR": {"trade_ready": ["005930"]}, "US": {"trade_ready": ["AAPL"]}}
        source.selection_stages = {
            "KR": {"applied": {"selected": ["005930"]}},
            "US": {"applied": {"selected": ["AAPL"]}},
        }
        source.price_cache = {"005930": 106.0, "AAPL": 200.0}
        source.price_cache_raw = {"005930": 106.0, "AAPL": 200.0}
        source._or_high = {"005930": 104.0, "AAPL": 201.0}
        source._post_open_anchor = {
            "KR:005930": {"anchor_price": 100.0},
            "US:AAPL": {"anchor_price": 198.0},
        }
        source._last_post_open_features_by_ticker = {
            "KR": {"005930": {"ret_5m_pct": 5.0}},
            "US": {"AAPL": {"ret_5m_pct": 2.0}},
        }
        source._last_rescreen_at = {"KR": 111.0, "US": 222.0}
        source._last_sub_screener_at = {"KR": 333.0, "US": 444.0}
        source._ticker_runtime_blocked_reasons = {
            "KR": {"005930": {"NO_SIGNAL": 2}},
            "US": {"AAPL": {"NO_SIGNAL": 1}},
        }
        source._ticker_runtime_rejection_reasons = {
            "KR": {"005930": {"WEAK_SIGNAL": 1}},
            "US": {"AAPL": {"WEAK_SIGNAL": 1}},
        }

        target = _make_bot(lambda **kwargs: _candles())
        target.is_paper = False
        target._current_session_date_str = lambda market: (
            "2026-05-14" if str(market).upper() == "KR" else "2026-05-13"
        )
        target.today_tickers = {"KR": [], "US": []}
        target.trade_ready_tickers = {"KR": [], "US": []}
        target.selection_meta = {"KR": {}, "US": {}}
        target.selection_stages = {"KR": {}, "US": {}}
        target.price_cache = {}
        target.price_cache_raw = {}
        target._or_high = {}
        target._post_open_anchor = {}
        target._last_post_open_features_by_ticker = {"KR": {}, "US": {}}
        target._last_rescreen_at = {"KR": 0.0, "US": 0.0}
        target._last_sub_screener_at = {"KR": 0.0, "US": 0.0}
        target._ticker_runtime_blocked_reasons = {"KR": {}, "US": {}}
        target._ticker_runtime_rejection_reasons = {"KR": {}, "US": {}}

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("runtime_paths._RUNTIME_ROOT", Path(tmpdir)):
                TradingBot._write_runtime_handoff_snapshot(source, "test_shutdown")
                TradingBot._restore_runtime_handoff_snapshot(target)

        self.assertEqual(target.today_tickers["KR"], [])
        self.assertEqual(target.trade_ready_tickers["KR"], [])
        self.assertEqual(target.selection_meta["KR"], {})
        self.assertNotIn("005930", target.price_cache)
        self.assertNotIn("KR:005930", target._post_open_anchor)
        self.assertNotIn("005930", target._last_post_open_features_by_ticker["KR"])
        self.assertEqual(target._last_rescreen_at["KR"], 0.0)
        self.assertEqual(target.today_tickers["US"], ["AAPL"])
        self.assertEqual(target.trade_ready_tickers["US"], ["AAPL"])
        self.assertEqual(target._post_open_anchor["US:AAPL"]["anchor_price"], 198.0)
        self.assertEqual(target._last_post_open_features_by_ticker["US"]["AAPL"]["ret_5m_pct"], 2.0)
        self.assertEqual(target._last_rescreen_at["US"], 222.0)
        self.assertEqual(target._last_funnel_event[2]["skipped_markets"]["KR"]["saved"], "2026-05-13")

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

    def test_us_cached_feature_after_kst_midnight_matches_ny_session(self) -> None:
        def provider(**_kwargs):
            raise AssertionError("prefetch should be skipped")

        bot = _make_bot(provider, market="US")
        bot.runtime_config.values["ENABLE_POST_OPEN_FEATURES_SHADOW"] = False
        bot._current_session_date_str = lambda _market: "2026-06-03"
        bot._last_post_open_features_by_ticker = {
            "KR": {},
            "US": {
                "AAPL": {
                    "ticker": "AAPL",
                    "market": "US",
                    "known_at": "2026-06-04T01:10:00+09:00",
                    "data_quality": "minute_complete",
                    "ret_5m_pct": 4.2,
                }
            },
        }

        rows = TradingBot._annotate_selection_execution_features(
            bot,
            "US",
            [{"ticker": "aapl", "price": 100.0}],
            "NEUTRAL",
            prefetch_intraday_evidence=False,
        )

        self.assertEqual(rows[0]["post_open_features"]["data_quality"], "minute_complete")
        self.assertEqual(rows[0]["post_open_ret_5m_pct"], 4.2)
        self.assertIn("AAPL", bot._last_post_open_features_by_ticker["US"])

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

    def test_final_prompt_evidence_class_marks_pack_prefetch_and_compact_only(self) -> None:
        events: list[tuple[str, str, dict]] = []
        bot = _make_bot(lambda **kwargs: _candles())
        bot.runtime_config.values.update(
            {
                "FINAL_PROMPT_EVIDENCE_ALIGNMENT_ENABLED": True,
                "INTRADAY_EVIDENCE_MAX_TICKERS": 2,
                "EVIDENCE_PACK_ENABLED": True,
                "SELECTION_FULL_EVIDENCE_MAX": 1,
                "SELECTION_EVIDENCE_CLASS_ENABLED": True,
            }
        )
        bot._write_funnel_event = lambda event, market_key, payload: events.append((event, market_key, payload))
        prompt_rows = [
            {"ticker": "111111", "market": "KR", "price": 107.0, "volume": 700, "vol_ratio": 3.0},
            {"ticker": "222222", "market": "KR", "price": 107.0, "volume": 700, "vol_ratio": 3.0},
            {"ticker": "333333", "market": "KR", "price": 107.0, "volume": 700, "vol_ratio": 3.0},
        ]

        with patch(
            "trading_bot.prepare_selection_prompt_pool",
            return_value=([dict(row) for row in prompt_rows], {"prompt_pool": [dict(row) for row in prompt_rows], "prompt_pool_count": 3}),
        ):
            _candidates, annotated_rows, prompt_meta, evidence = TradingBot._prepare_selection_prompt_pool_with_evidence(
                bot,
                "KR",
                [dict(row) for row in prompt_rows],
                "NEUTRAL",
            )

        by_ticker = {row["ticker"]: row for row in annotated_rows}
        self.assertEqual(set(evidence), {"111111"})
        self.assertEqual(by_ticker["111111"]["evidence_class"], "FULL_PACK")
        self.assertEqual(by_ticker["222222"]["evidence_class"], "PREFETCHED_COMPLETE")
        self.assertEqual(by_ticker["333333"]["evidence_class"], "COMPACT_ONLY")
        self.assertEqual(by_ticker["333333"]["selection_evidence_action_ceiling"], "WATCH")
        self.assertIn("selection_evidence_shadow_rank", by_ticker["333333"])
        self.assertEqual(prompt_meta["selection_evidence_reorder_shadow_tickers"][0], "111111")
        self.assertEqual(prompt_meta["evidence_class_counts"]["COMPACT_ONLY"], 1)
        alignment = next(payload for event, _market, payload in events if event == "selection_final_prompt_evidence_alignment")
        self.assertEqual(alignment["evidence_class_counts"]["FULL_PACK"], 1)
        self.assertEqual(alignment["evidence_class_counts"]["PREFETCHED_COMPLETE"], 1)

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

    def test_intraday_evidence_coverage_records_timeout_diagnostics(self) -> None:
        events: list[tuple[str, str, dict]] = []

        def _provider(**kwargs):
            if kwargs.get("ticker") == "005930":
                raise TimeoutError("provider_timeout")
            return _candles()

        bot = _make_bot(_provider)
        bot._write_funnel_event = lambda event, market_key, payload: events.append((event, market_key, payload))

        TradingBot._prefetch_selection_intraday_evidence(
            bot,
            "KR",
            [
                {"ticker": "005930", "market": "KR", "price": 100.0},
                {"ticker": "000660", "market": "KR", "price": 100.0},
            ],
            phase={"phase": "mid"},
        )

        coverage = next(payload for event, _market, payload in events if event == "selection_intraday_evidence_coverage")
        self.assertEqual(coverage["provider_timeout_count"], 1)
        self.assertEqual(coverage["prefetch_timeout_count"], 0)
        self.assertEqual(coverage["worker_count"], 1)
        self.assertEqual(coverage["timeout_seconds"], 4.0)
        self.assertIn("elapsed_seconds", coverage)

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
