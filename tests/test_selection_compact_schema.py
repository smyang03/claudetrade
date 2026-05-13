from __future__ import annotations

import unittest

from bot.candidate_policy import normalize_selection_result
from runtime.selection_compact_schema import is_compact_selection_response
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
                    "pt": {"ref": 218.76, "lo": 216.5, "hi": 219.5, "tgt": 226.0, "stp": 213.5, "days": 1, "conf": 0.72},
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
                    "pt": {"ref": 238.72, "lo": 235.0, "hi": 239.5, "tgt": 250.0, "stp": 229.0, "days": 1, "conf": 0.64},
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
                    "pt": {},
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

    def test_compact_watch_empty_pt_is_warning_free(self) -> None:
        meta = normalize_selection_result(
            {
                "wl": ["SMCI"],
                "tr": [],
                "ca": [
                    {
                        "t": "SMCI",
                        "a": "WATCH",
                        "s": "momentum",
                        "c": 0.2,
                        "fr": "UNKNOWN",
                        "mat": "WEAK",
                        "ceil": "WATCH",
                        "rc": "WATCH_ONLY",
                        "blk": [],
                        "inv": "setup_invalid",
                        "pt": {},
                    }
                ],
            },
            self._candidates(),
            "US",
            reference_prices=reference_prices_from_candidates(self._candidates(), "US"),
            stop_reason="end_turn",
        )

        self.assertEqual(meta["watchlist"], ["SMCI"])
        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["candidate_actions"][0]["price_targets"], {})
        warnings = meta["_compact_validation"]["warnings"]
        self.assertFalse(any("non_actionable_price_targets_ignored" in item for item in warnings))

    def test_compact_watch_non_empty_pt_is_ignored_with_warning(self) -> None:
        meta = normalize_selection_result(
            {
                "wl": ["SMCI"],
                "tr": [],
                "ca": [
                    {
                        "t": "SMCI",
                        "a": "WATCH",
                        "s": "momentum",
                        "c": 0.2,
                        "fr": "UNKNOWN",
                        "mat": "WEAK",
                        "ceil": "WATCH",
                        "rc": "WATCH_ONLY",
                        "blk": [],
                        "inv": "setup_invalid",
                        "pt": {"ref": 48.25, "lo": 47.0},
                    }
                ],
            },
            self._candidates(),
            "US",
            reference_prices=reference_prices_from_candidates(self._candidates(), "US"),
            stop_reason="end_turn",
        )

        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["candidate_actions"][0]["price_targets"], {})
        self.assertNotIn("SMCI", meta["price_targets"])
        warnings = meta["_compact_validation"]["warnings"]
        self.assertTrue(any("non_actionable_price_targets_ignored" in item for item in warnings))

    def test_compact_text_hold_direction_and_confirmation_fallback_keep_ready(self) -> None:
        candidates = [
            {"ticker": "RKLB", "price": 119.32},
            {"ticker": "APLD", "price": 46.54},
        ]
        meta = normalize_selection_result(
            {
                "wl": ["RKLB", "APLD"],
                "tr": ["RKLB", "APLD"],
                "ca": [
                    {
                        "t": "RKLB",
                        "a": "BUY_READY",
                        "s": "gap_pullback",
                        "c": 0.72,
                        "fr": "at_high",
                        "mat": "STRONG",
                        "ceil": "BUY_READY",
                        "rc": "TRADE_READY",
                        "blk": [],
                        "inv": "",
                        "pt": {
                            "ref": 119.32,
                            "lo": 117.5,
                            "hi": 122.0,
                            "tgt": 128.0,
                            "stp": 114.0,
                            "d": "long",
                            "cf": "pullback_entry_near_high",
                        },
                    },
                    {
                        "t": "APLD",
                        "a": "BUY_READY",
                        "s": "gap_pullback",
                        "c": 0.68,
                        "fr": "at_high",
                        "mat": "STRONG",
                        "ceil": "BUY_READY",
                        "rc": "TRADE_READY",
                        "blk": [],
                        "inv": "",
                        "pt": {
                            "ref": 46.54,
                            "lo": 45.5,
                            "hi": 48.0,
                            "tgt": 51.0,
                            "stp": 43.5,
                            "d": "long",
                            "cf": "pullback_entry_near_high",
                        },
                    },
                ],
            },
            candidates,
            "US",
            reference_prices=reference_prices_from_candidates(candidates, "US"),
            stop_reason="end_turn",
        )

        self.assertEqual(meta["trade_ready"], ["RKLB", "APLD"])
        self.assertEqual(meta["candidate_actions"][0]["action"], "BUY_READY")
        self.assertEqual(meta["price_targets"]["RKLB"]["hold_days"], 1)
        self.assertEqual(meta["price_targets"]["RKLB"]["confidence"], 0.72)
        warnings = meta["_compact_validation"]["warnings"]
        self.assertTrue(any("price_target_hold_days_non_numeric" in item for item in warnings))
        self.assertTrue(any("price_target_confidence_filled_from_action" in item for item in warnings))
        self.assertFalse(any("missing_price_targets" in item for item in warnings))

    def test_compact_missing_core_price_levels_still_demotes_ready(self) -> None:
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
                        "pt": {"ref": 218.76, "d": "long", "cf": "confirmed"},
                    }
                ],
            },
            self._candidates(),
            "US",
            reference_prices={"NVDA": 218.76},
            stop_reason="end_turn",
        )

        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["candidate_actions"][0]["action"], "WATCH")
        warnings = meta["_compact_validation"]["warnings"]
        self.assertTrue(any("missing_price_targets:sell_target,stop_loss" in item for item in warnings))

    def test_compact_ready_with_internal_contradictions_is_demoted(self) -> None:
        base_pt = {"ref": 218.76, "lo": 216.0, "hi": 219.0, "tgt": 226.0, "stp": 213.0, "days": 1, "conf": 0.7}
        meta = normalize_selection_result(
            {
                "wl": ["NVDA", "QCOM", "SMCI"],
                "tr": ["NVDA", "QCOM", "SMCI"],
                "ca": [
                    {
                        "t": "NVDA",
                        "a": "BUY_READY",
                        "s": "momentum",
                        "c": "high",
                        "fr": "FRESH",
                        "mat": "CONFIRMED",
                        "ceil": "BUY_READY",
                        "rc": "MOMENTUM_READY",
                        "blk": [],
                        "inv": "break_vwap",
                        "pt": base_pt,
                    },
                    {
                        "t": "QCOM",
                        "a": "BUY_READY",
                        "s": "momentum",
                        "c": 0.7,
                        "fr": "FRESH",
                        "mat": "CONFIRMED",
                        "ceil": "WATCH",
                        "rc": "MOMENTUM_READY",
                        "blk": [],
                        "inv": "break_vwap",
                        "pt": {**base_pt, "ref": 238.72, "lo": 235.0, "hi": 239.0, "tgt": 250.0, "stp": 229.0},
                    },
                    {
                        "t": "SMCI",
                        "a": "PROBE_READY",
                        "s": "gap_pullback",
                        "c": 0.6,
                        "fr": "FRESH",
                        "mat": "CONFIRMED",
                        "ceil": "PROBE_READY",
                        "rc": "GAP_PULLBACK_READY",
                        "blk": ["data_missing"],
                        "inv": "break_base",
                        "pt": {**base_pt, "ref": 48.25, "lo": 47.0, "hi": 49.0, "tgt": 52.0, "stp": 45.0},
                    },
                ],
            },
            self._candidates(),
            "US",
            reference_prices=reference_prices_from_candidates(self._candidates(), "US"),
            stop_reason="end_turn",
        )

        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual([item["action"] for item in meta["candidate_actions"]], ["WATCH", "WATCH", "WATCH"])
        warnings = meta["_compact_validation"]["warnings"]
        self.assertTrue(any("ready_confidence_missing_demoted" in item for item in warnings))
        self.assertTrue(any("ready_exceeds_action_ceiling_demoted" in item for item in warnings))
        self.assertTrue(any("ready_with_blockers_demoted" in item for item in warnings))

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
                        "pt": {"ref": 218.76, "lo": 216.0, "hi": 218.5, "tgt": 224.0, "stp": 212.5, "days": 1, "conf": 0.62},
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

    def test_compact_absent_candidate_actions_blocks_trade_ready(self) -> None:
        meta = normalize_selection_result(
            {"wl": ["NVDA", "QCOM"], "tr": ["NVDA"]},
            self._candidates(),
            "US",
            stop_reason="end_turn",
        )

        self.assertEqual(meta["watchlist"][:2], ["NVDA", "QCOM"])
        self.assertEqual(meta["trade_ready"], [])
        self.assertTrue(meta["_candidate_actions_missing_contract"])
        self.assertIn("candidate_actions_missing", meta["_compact_validation"]["errors"])

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
                        "pt": {"ref": 999.0, "lo": 216.0, "hi": 219.0, "tgt": 226.0, "stp": 213.0, "days": 1, "conf": 0.8},
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
                        "pt": {"ref": 218.76, "lo": 216.0, "hi": 219.0, "tgt": 226.0, "stp": 213.0, "days": 1, "conf": 0.8},
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
        self.assertTrue(meta["_partial_contract_recovery_watch_only"])

    def test_compact_detection_routes_missing_or_invalid_ca_to_contract_validation(self) -> None:
        self.assertTrue(is_compact_selection_response({"wl": ["NVDA"], "tr": ["NVDA"]}))
        self.assertTrue(is_compact_selection_response({"wl": ["NVDA"], "tr": [], "ca": "not-list"}))
        self.assertFalse(is_compact_selection_response({"watchlist": ["NVDA"], "ca": []}))
        self.assertTrue(is_compact_selection_response({"wl": ["NVDA"], "tr": [], "ca": []}))

    def test_compact_prompt_contract_does_not_request_legacy_fields(self) -> None:
        contract = compact_output_contract(watch_max=15, trade_max=5)
        self.assertIn("Use keys only: wl,tr,ca", contract)
        self.assertIn("Do not include reasons", contract)
        self.assertIn("WATCH/AVOID use empty pt={}", contract)
        self.assertIn("pt.days must be numeric hold_days", contract)
        self.assertIn("pt.conf must be numeric confidence", contract)
        self.assertIn('"pt":{}', contract)
        self.assertNotIn("WATCH/AVOID omit pt", contract)
        self.assertNotIn('"candidate_actions"', contract)


if __name__ == "__main__":
    unittest.main()
