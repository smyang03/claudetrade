from __future__ import annotations

import unittest

from bot.candidate_policy import normalize_selection_result
from runtime.selection_compact_schema import (
    compact_output_contract,
    reference_prices_from_candidates,
)


class SelectionCompactSchemaTests(unittest.TestCase):
    def _candidates(self) -> list[dict]:
        return [
            {"ticker": "NVDA", "price": 218.76},
            {"ticker": "QCOM", "price": 238.72},
            {"ticker": "SMCI", "price": 48.25},
        ]

    def test_compact_response_converts_to_canonical_meta(self) -> None:
        parsed = {
            "wl": ["NVDA", "QCOM", "SMCI"],
            "tr": ["NVDA", "QCOM"],
            "ca": [
                {
                    "t": "NVDA",
                    "a": "BUY_READY",
                    "s": "opening_range_pullback",
                    "c": 0.72,
                    "fr": "FRESH",
                    "mat": "CONFIRMED",
                    "ceil": "BUY_READY",
                    "rc": "OR_PULLBACK_CONFIRMED",
                    "blk": [],
                    "inv": "break_OR_low",
                    "pt": {"ref": 218.76, "lo": 216.5, "hi": 219.5, "tgt": 226.0, "stp": 213.5, "d": 1, "cf": 0.72},
                },
                {
                    "t": "QCOM",
                    "a": "PROBE_READY",
                    "s": "gap_pullback",
                    "c": 0.64,
                    "fr": "FRESH",
                    "mat": "CONFIRMED",
                    "ceil": "PROBE_READY",
                    "rc": "GAP_PULLBACK_CONFIRMED",
                    "blk": [],
                    "inv": "break_gap_base",
                    "pt": {"ref": 238.72, "lo": 235.0, "hi": 239.5, "tgt": 250.0, "stp": 229.0, "d": 1, "cf": 0.64},
                },
                {
                    "t": "SMCI",
                    "a": "WATCH",
                    "s": "opening_range_pullback",
                    "c": 0.2,
                    "fr": "UNKNOWN",
                    "mat": "WEAK",
                    "ceil": "WATCH",
                    "rc": "WATCH_WEAK",
                    "blk": ["weak"],
                    "inv": "setup_invalid",
                },
            ],
        }

        meta = normalize_selection_result(
            parsed,
            self._candidates(),
            "US",
            reference_prices=reference_prices_from_candidates(self._candidates(), "US"),
            stop_reason="end_turn",
            source_prompt_id="selection_compact.v1",
        )

        self.assertEqual(meta["watchlist"], ["NVDA", "QCOM", "SMCI"])
        self.assertEqual(meta["trade_ready"], ["NVDA", "QCOM"])
        self.assertEqual(meta["recommended_strategy"]["NVDA"], "opening_range_pullback")
        self.assertEqual(meta["reasons"]["NVDA"], "OR_PULLBACK_CONFIRMED")
        self.assertIn("NVDA", meta["price_targets"])
        self.assertEqual(meta["price_targets"]["NVDA"]["reference_price"], 218.76)
        self.assertEqual(meta["candidate_actions"][0]["strategy"], "opening_range_pullback")
        self.assertEqual(meta["candidate_actions"][1]["size_intent"], "probe")
        self.assertTrue(meta["candidate_actions"][0]["why_not_watch"])
        self.assertFalse(meta["_candidate_actions_missing_contract"])
        self.assertEqual(meta["_selection_raw_schema"], "compact")

    def test_pullback_wait_keeps_price_targets_without_trade_ready(self) -> None:
        meta = normalize_selection_result(
            {
                "wl": ["NVDA"],
                "tr": [],
                "ca": [
                    {
                        "t": "NVDA",
                        "a": "PULLBACK_WAIT",
                        "s": "opening_range_pullback",
                        "c": 0.62,
                        "fr": "FRESH",
                        "mat": "EARLY",
                        "ceil": "PULLBACK_WAIT",
                        "rc": "WAIT_FOR_PULLBACK",
                        "blk": ["above_zone"],
                        "inv": "break_vwap",
                        "pt": {"ref": 218.76, "lo": 216.0, "hi": 218.5, "tgt": 224.0, "stp": 212.5, "d": 1, "cf": 0.62},
                    }
                ],
            },
            self._candidates(),
            "US",
            reference_prices={"NVDA": 218.76},
            stop_reason="end_turn",
        )

        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["candidate_actions"][0]["action"], "PULLBACK_WAIT")
        self.assertEqual(meta["candidate_actions"][0]["price_targets"]["buy_zone_low"], 216.0)
        self.assertEqual(meta["price_targets"]["NVDA"]["buy_zone_high"], 218.5)

    def test_compact_missing_candidate_actions_blocks_trade_ready(self) -> None:
        meta = normalize_selection_result(
            {"wl": ["NVDA", "QCOM"], "tr": ["NVDA"], "ca": []},
            self._candidates(),
            "US",
            stop_reason="end_turn",
        )

        self.assertEqual(meta["trade_ready"], [])
        self.assertTrue(meta["_candidate_actions_missing_contract"])
        self.assertIn("candidate_actions_empty", meta["_compact_validation"]["errors"])

    def test_compact_reference_price_is_corrected_from_input(self) -> None:
        meta = normalize_selection_result(
            {
                "wl": ["NVDA"],
                "tr": ["NVDA"],
                "ca": [
                    {
                        "t": "NVDA",
                        "a": "BUY_READY",
                        "s": "momentum",
                        "c": 0.8,
                        "fr": "FRESH",
                        "mat": "CONFIRMED",
                        "ceil": "BUY_READY",
                        "rc": "MOMENTUM_READY",
                        "blk": [],
                        "inv": "break_vwap",
                        "pt": {"ref": 999.0, "lo": 216.0, "hi": 219.0, "tgt": 226.0, "stp": 213.0, "d": 1, "cf": 0.8},
                    }
                ],
            },
            self._candidates(),
            "US",
            reference_prices={"NVDA": 218.76},
            stop_reason="end_turn",
        )

        self.assertEqual(meta["price_targets"]["NVDA"]["reference_price"], 218.76)
        warnings = meta["_compact_validation"]["warnings"]
        self.assertTrue(any("reference_price_corrected_to_input" in item for item in warnings))

    def test_compact_stop_reason_max_tokens_is_not_executable(self) -> None:
        meta = normalize_selection_result(
            {
                "wl": ["NVDA"],
                "tr": ["NVDA"],
                "ca": [
                    {
                        "t": "NVDA",
                        "a": "BUY_READY",
                        "s": "momentum",
                        "c": 0.8,
                        "fr": "FRESH",
                        "mat": "CONFIRMED",
                        "ceil": "BUY_READY",
                        "rc": "MOMENTUM_READY",
                        "blk": [],
                        "inv": "break_vwap",
                        "pt": {"ref": 218.76, "lo": 216.0, "hi": 219.0, "tgt": 226.0, "stp": 213.0, "d": 1, "cf": 0.8},
                    }
                ],
            },
            self._candidates(),
            "US",
            stop_reason="max_tokens",
        )

        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["_fallback_mode"], "selection_truncated")
        self.assertIn("stop_reason_max_tokens", meta["_compact_validation"]["errors"])
        self.assertEqual(meta["price_targets"], {})

    def test_compact_prompt_contract_does_not_request_legacy_fields(self) -> None:
        contract = compact_output_contract(watch_max=15, trade_max=5)
        self.assertIn("Use keys only: wl,tr,ca", contract)
        self.assertIn("Do not include reasons", contract)
        self.assertNotIn('"candidate_actions"', contract)


if __name__ == "__main__":
    unittest.main()
