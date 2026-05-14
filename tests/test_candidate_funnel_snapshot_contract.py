from __future__ import annotations

import unittest

from trading_bot import TradingBot


class _RuntimeConfig:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self.values.get(key, default))

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self.values.get(key, default))


class CandidateFunnelSnapshotContractTests(unittest.TestCase):
    def test_legacy_snapshot_does_not_emit_placeholder_quality_report(self) -> None:
        class DummyBot:
            def __init__(self) -> None:
                self.events: list[tuple[str, str, dict]] = []

            def _write_funnel_event(self, event_type: str, market: str, payload: dict) -> None:
                self.events.append((event_type, market, payload))

        bot = DummyBot()

        TradingBot._record_candidate_funnel_snapshot(
            bot,
            "US",
            selected=["INTC"],
            meta={
                "watchlist": ["INTC", "FSLY"],
                "trade_ready": ["INTC"],
                "_candidate_actions_source": "legacy_selection_shadow",
            },
            stages={"selected": 2},
        )

        self.assertEqual(len(bot.events), 1)
        event_type, market, payload = bot.events[0]
        self.assertEqual(event_type, "candidate_funnel_snapshot")
        self.assertEqual(market, "US")
        self.assertIsNone(payload["full_pool_count"])
        self.assertEqual(payload["prompt_pool_count"], 2)
        self.assertEqual(payload["pool_separation_state"], "legacy_prompt_only")

    def test_unified_pool_shadow_records_prompt_exclusions(self) -> None:
        class DummyBot:
            def __init__(self) -> None:
                self.runtime_config = _RuntimeConfig({
                    "ENABLE_UNIFIED_CANDIDATE_POOL_SHADOW": True,
                    "US_PROMPT_POOL_CAP": 1,
                })
                self.events: list[tuple[str, str, dict]] = []

            def _write_funnel_event(self, event_type: str, market: str, payload: dict) -> None:
                self.events.append((event_type, market, payload))

        bot = DummyBot()

        TradingBot._record_candidate_funnel_snapshot(
            bot,
            "US",
            selected=["INTC", "FSLY"],
            meta={
                "watchlist": ["INTC", "FSLY"],
                "trade_ready": ["INTC"],
            },
            stages={"selected": 2},
        )

        payload = bot.events[0][2]
        self.assertEqual(payload["full_pool_count"], 2)
        self.assertEqual(payload["prompt_pool_count"], 1)
        self.assertEqual(payload["pool_separation_state"], "unified_pool_shadow_legacy_input")
        self.assertEqual(payload["excluded_from_prompt"][0]["reason"], "prompt_cap")

    def test_action_routing_shadow_is_emitted_for_candidate_actions(self) -> None:
        class DummyBot:
            def __init__(self) -> None:
                self.runtime_config = _RuntimeConfig({
                    "ENABLE_UNIFIED_CANDIDATE_POOL_SHADOW": False,
                    "ENABLE_ACTION_ROUTING_SHADOW": True,
                })
                self.risk = type("Risk", (), {"positions": []})()
                self.events: list[tuple[str, str, dict]] = []

            def _write_funnel_event(self, event_type: str, market: str, payload: dict) -> None:
                self.events.append((event_type, market, payload))

        bot = DummyBot()

        TradingBot._record_candidate_funnel_snapshot(
            bot,
            "US",
            selected=["INTC"],
            meta={
                "watchlist": ["INTC"],
                "trade_ready": ["INTC"],
                "candidate_actions": [
                    {"ticker": "INTC", "market": "US", "action": "BUY_READY", "confidence": 0.8}
                ],
            },
            stages={"selected": 1},
        )

        route_event = [event for event in bot.events if event[0] == "action_routing_shadow"][0]
        self.assertEqual(route_event[2]["routes"][0]["route"], "PlanA.buy")

    def test_real_quality_report_is_emitted_when_supplied(self) -> None:
        class DummyBot:
            def __init__(self) -> None:
                self.events: list[tuple[str, str, dict]] = []

            def _write_funnel_event(self, event_type: str, market: str, payload: dict) -> None:
                self.events.append((event_type, market, payload))

        bot = DummyBot()

        TradingBot._record_candidate_funnel_snapshot(
            bot,
            "KR",
            selected=["001440"],
            meta={
                "watchlist": ["001440"],
                "trade_ready": ["001440"],
                "_full_pool_count": 59,
                "_prompt_pool_count": 30,
                "candidate_quality_report": {
                    "gainers_ratio": 0.4,
                    "bucket_distribution": {"confirmed": 8},
                },
            },
            stages={"selected": 1},
        )

        self.assertEqual([event[0] for event in bot.events], ["candidate_funnel_snapshot", "candidate_quality_report"])
        self.assertEqual(bot.events[0][2]["full_pool_count"], 59)
        self.assertEqual(bot.events[0][2]["prompt_pool_count"], 30)
        self.assertEqual(bot.events[0][2]["pool_separation_state"], "separated")
        self.assertEqual(bot.events[1][2]["gainers_ratio"], 0.4)

    def test_partial_reselect_diagnostics_are_passed_through(self) -> None:
        class DummyBot:
            def __init__(self) -> None:
                self.events: list[tuple[str, str, dict]] = []

            def _write_funnel_event(self, event_type: str, market: str, payload: dict) -> None:
                self.events.append((event_type, market, payload))

        bot = DummyBot()

        TradingBot._record_candidate_funnel_snapshot(
            bot,
            "KR",
            selected=["010"],
            meta={
                "watchlist": ["010"],
                "trade_ready": [],
                "_partial_reselect_replacement": {
                    "candidate_ready": ["010"],
                    "replacement_pool": ["010"],
                    "accepted": [],
                    "rejected": {"010": {"reason": "trainer_replacement_delta_blocked"}},
                    "slot_unfilled": ["001"],
                },
            },
            stages={"applied": {"selected": ["001"], "trade_ready": []}},
        )

        payload = bot.events[0][2]
        self.assertEqual(payload["execution_pool_count"], 0)
        self.assertEqual(payload["partial_reselect"]["candidate_ready"], ["010"])
        self.assertEqual(
            payload["partial_reselect"]["rejected"]["010"]["reason"],
            "trainer_replacement_delta_blocked",
        )


if __name__ == "__main__":
    unittest.main()
