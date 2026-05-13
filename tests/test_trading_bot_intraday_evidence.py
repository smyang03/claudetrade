from __future__ import annotations

import unittest
from datetime import datetime

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

    def test_prefetch_failure_does_not_erase_same_session_feature(self) -> None:
        def provider(**kwargs):
            raise RuntimeError("down")

        bot = _make_bot(provider, market="US")
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

        self.assertNotIn("post_open_features", rows[0])
        event, market, payload = bot._last_funnel_event
        self.assertEqual(event, "selection_intraday_evidence_coverage")
        self.assertEqual(market, "KR")
        self.assertEqual(payload["missing"], 1)
        self.assertIn("session_open_resolve_failed", payload["errors_sample"][0])


if __name__ == "__main__":
    unittest.main()
