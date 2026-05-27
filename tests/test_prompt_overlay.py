from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from runtime.candidate_prompt_pool import build_plan_a_overlay_prompt_pool


def _row(ticker: str, state: str = "WATCH", score: float = 50.0, risk: float = 20.0) -> dict:
    return {
        "ticker": ticker,
        "market": "US",
        "trainer_candidate_state": state,
        "trainer_prompt_score": score,
        "trainer_risk_score": risk,
        "primary_bucket": "momentum_now",
        "liquidity_bucket": "high",
        "change_pct": 5.0,
        "price": 100.0,
        "volume": 1000000,
    }


class PromptOverlayHelperTests(unittest.TestCase):
    def test_plan_a_zero_keeps_current_and_never_uses_plan_b(self) -> None:
        current = [_row("KEEP1"), _row("KEEP2")]
        scored = [_row("PLANB1", "PLAN_B", 95.0)]

        result, meta = build_plan_a_overlay_prompt_pool(
            current,
            scored,
            market="US",
            cap=2,
            keep_current=1,
            plan_a_max=1,
        )

        self.assertEqual([row["ticker"] for row in result], ["KEEP1", "KEEP2"])
        self.assertEqual(meta["overlay_candidate_state"], "current_only")
        self.assertEqual(meta["overlay_added_tickers"], [])
        self.assertFalse(meta["overlay_plan_b_used"])

    def test_adds_plan_a_outside_current_top_and_tracks_removed(self) -> None:
        current = [_row("KEEP1"), _row("KEEP2"), _row("DROP1"), _row("DROP2")]
        scored = [
            _row("PLANB1", "PLAN_B", 99.0),
            _row("PA1", "PLAN_A", 98.0, 10.0),
            _row("PA2", "PLAN_A", 90.0, 10.0),
        ]

        result, meta = build_plan_a_overlay_prompt_pool(
            current,
            scored,
            market="US",
            cap=3,
            keep_current=1,
            plan_a_max=1,
        )

        self.assertEqual([row["ticker"] for row in result], ["KEEP1", "PA1", "KEEP2"])
        self.assertEqual(meta["overlay_added_tickers"], ["PA1"])
        self.assertEqual(set(meta["overlay_removed_tickers"]), {"DROP1", "DROP2"})
        self.assertFalse(meta["overlay_plan_b_used"])
        self.assertTrue(result[1]["prompt_overlay_added"])
        self.assertFalse(result[0]["prompt_overlay_added"])

    def test_dedupes_plan_a_already_in_current_top(self) -> None:
        current = [_row("PA0", "PLAN_A", 99.0), _row("KEEP2")]
        scored = [_row("PA0", "PLAN_A", 99.0), _row("PA1", "PLAN_A", 95.0)]

        result, meta = build_plan_a_overlay_prompt_pool(
            current,
            scored,
            market="US",
            cap=2,
            keep_current=1,
            plan_a_max=1,
        )

        self.assertEqual([row["ticker"] for row in result], ["PA0", "PA1"])
        self.assertEqual(meta["overlay_added_tickers"], ["PA1"])
        self.assertEqual(meta["overlay_removed_tickers"], ["KEEP2"])

    def test_market_cap_guard_limits_us_to_24(self) -> None:
        current = [_row(f"KEEP{idx}") for idx in range(30)]
        result, _meta = build_plan_a_overlay_prompt_pool(
            current,
            [],
            market="US",
            cap=30,
            keep_current=15,
            plan_a_max=4,
        )

        self.assertEqual(len(result), 24)

    def test_plan_overlay_does_not_promote_same_day_stopped_over_regular(self) -> None:
        current = [_row("KEEP1"), _row("KEEP2"), _row("DROP1")]
        stopped_plan_a = _row("STOPPA", "PLAN_A", 99.0, 10.0)
        stopped_plan_a["same_day_stopped"] = True

        result, meta = build_plan_a_overlay_prompt_pool(
            current,
            [stopped_plan_a],
            market="US",
            cap=3,
            keep_current=1,
            plan_a_max=1,
        )

        self.assertEqual([row["ticker"] for row in result], ["KEEP1", "KEEP2", "DROP1"])
        self.assertEqual(meta["overlay_added_tickers"], [])
        self.assertEqual(meta["overlay_candidate_state"], "current_only")


class PromptOverlayAnalystTests(unittest.TestCase):
    def _run_select(self, *, mode: str, with_plan_a: bool = True) -> tuple[str, dict]:
        from minority_report import analysts

        captured: dict[str, str] = {}

        def fake_create(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text=json.dumps(
                            {
                                "watchlist": ["KEEP1"],
                                "trade_ready": [],
                                "reasons": {"KEEP1": "watch"},
                                "veto": {},
                            }
                        )
                    )
                ],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                stop_reason="end_turn",
            )

        def fake_pool(*_args, **_kwargs):
            prompt_pool = [_row("KEEP1"), _row("KEEP2"), _row("DROP1")]
            extra = [_row("PA1", "PLAN_A", 98.0, 10.0)] if with_plan_a else [_row("PLANB1", "PLAN_B", 99.0, 10.0)]
            scored_pool = prompt_pool + extra
            return {
                "version": "trainer_prompt_pool_v1",
                "score_version": "trainer_quality_v1",
                "target": 3,
                "hard_cap": 3,
                "full_pool": scored_pool,
                "scored_pool": scored_pool,
                "prompt_pool": prompt_pool,
                "excluded_from_prompt": [{"ticker": extra[0]["ticker"], "candidate": extra[0]}],
                "metrics": {},
            }

        env = {
            "CANDIDATE_QUALITY_TRAINER_ENABLED": "true",
            "CANDIDATE_PROMPT_POOL_REORDER_ENABLED": "true",
            "CANDIDATE_PROMPT_POOL_TARGET_US": "3",
            "CANDIDATE_PROMPT_POOL_HARD_CAP_US": "3",
            "PROMPT_OVERLAY_MODE": mode,
            "PROMPT_OVERLAY_KEEP_CURRENT": "1",
            "PROMPT_OVERLAY_PLAN_A_MAX": "1",
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false",
            "SELECTION_OUTPUT_COMPRESSION_ENABLED": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS_SHADOW": "false",
        }
        with patch.dict("os.environ", env, clear=False), \
             patch("runtime.candidate_prompt_pool.build_trainer_prompt_pool", side_effect=fake_pool), \
             patch.object(analysts.client.messages, "create", side_effect=fake_create), \
             patch.object(analysts, "save_raw_call", lambda **_kwargs: None), \
             patch.object(analysts, "build_active_lesson_context", return_value={"section": "", "metadata": {}}):
            analysts.select_tickers(
                "US",
                "digest",
                "NEUTRAL",
                [_row("KEEP1"), _row("KEEP2"), _row("DROP1"), _row("PA1", "PLAN_A")],
            )

        return captured["prompt"], analysts.get_last_selection_meta()

    def test_shadow_records_overlay_without_changing_claude_prompt(self) -> None:
        prompt, meta = self._run_select(mode="shadow")

        self.assertIn("KEEP1", prompt)
        self.assertNotIn("PA1", prompt)
        self.assertEqual(meta["_prompt_overlay_mode"], "shadow")
        self.assertEqual([row["ticker"] for row in meta["_final_prompt_pool"]], ["KEEP1", "KEEP2", "DROP1"])
        self.assertEqual(meta["_shadow_overlay_added_tickers"], ["PA1"])
        self.assertEqual(meta["_shadow_overlay_removed_tickers"], ["DROP1"])

    def test_live_replaces_prompt_with_overlay_pool(self) -> None:
        prompt, meta = self._run_select(mode="live")

        self.assertIn("PA1", prompt)
        self.assertNotIn("DROP1", prompt)
        self.assertEqual(meta["_prompt_overlay_mode"], "live")
        self.assertEqual([row["ticker"] for row in meta["_final_prompt_pool"]], ["KEEP1", "PA1", "KEEP2"])
        self.assertEqual(meta["_overlay_added_tickers"], ["PA1"])
        self.assertEqual(meta["_overlay_removed_tickers"], ["DROP1"])

    def test_plan_a_zero_keeps_current_prompt_even_in_live_mode(self) -> None:
        prompt, meta = self._run_select(mode="live", with_plan_a=False)

        self.assertIn("KEEP1", prompt)
        self.assertIn("DROP1", prompt)
        self.assertNotIn("PLANB1", prompt)
        self.assertEqual(meta["_prompt_overlay_mode"], "current_only")
        self.assertEqual([row["ticker"] for row in meta["_final_prompt_pool"]], ["KEEP1", "KEEP2", "DROP1"])
        self.assertEqual(meta["_overlay_added_tickers"], [])


if __name__ == "__main__":
    unittest.main()
