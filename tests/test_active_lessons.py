from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from minority_report import active_lessons


def _lesson_payload() -> dict:
    return {
        "markets": {
            "US": [
                {
                    "id": "watch_only_missed_runup_review",
                    "market": "US",
                    "scope": "selection",
                    "summary": "watch_only missed runup is high; reconsider trade_ready promotion when veto is weak.",
                    "breached": True,
                    "severity": "high",
                    "confidence": 0.92,
                    "sample_count": 42,
                    "generated_at": "2026-05-01T23:00:00",
                    "expires_at": "2099-01-01T00:00:00",
                },
                {
                    "id": "kis_token_timeout",
                    "market": "US",
                    "scope": "execution",
                    "summary": "KIS token timeout and broker stale state should be checked.",
                    "breached": True,
                    "severity": "high",
                    "confidence": 0.95,
                    "sample_count": 99,
                    "generated_at": "2026-05-01T23:00:00",
                    "expires_at": "2099-01-01T00:00:00",
                },
            ]
        }
    }


def _brain_payload() -> dict:
    return {
        "markets": {
            "US": {
                "recent_days": [
                    {
                        "date": "2026-05-01",
                        "key_lesson": "Low volume sector candidates need lower conviction even when the sector is strong.",
                        "trades": 8,
                    },
                    {
                        "date": "2026-05-01",
                        "key_lesson": "JSON parser truncation should be fixed before retry.",
                        "trades": 1,
                    },
                ],
                "execution_lessons": [
                    "Affordability failures should reduce execution confidence before order review.",
                    "Broker stale state is an infra issue, not a market lesson.",
                ],
                "current_beliefs": {
                    "learned_lessons": [
                        "Legacy broad momentum belief should stay archived by default.",
                    ]
                },
            }
        }
    }


class ActiveLessonBuilderTests(unittest.TestCase):
    def test_shadow_default_selects_but_does_not_inject(self) -> None:
        with patch.object(active_lessons, "_load_lesson_candidates", return_value=_lesson_payload()), \
             patch.object(active_lessons, "_load_brain", return_value=_brain_payload()), \
             patch.dict(os.environ, {
                 "ACTIVE_LESSONS_ENABLED": "false",
                 "ACTIVE_LESSONS_SHADOW": "true",
                 "ACTIVE_LESSONS_ALLOW_LEGACY_BRAIN": "false",
             }, clear=False):
            result = active_lessons.build_active_lesson_context("US")

        self.assertEqual(result["section"], "")
        self.assertIn("[active lessons]", result["preview"])
        self.assertFalse(result["metadata"]["injected"])
        self.assertGreaterEqual(result["metadata"]["count"], 1)

    def test_filters_system_issues_and_legacy_by_default(self) -> None:
        with patch.object(active_lessons, "_load_lesson_candidates", return_value=_lesson_payload()), \
             patch.object(active_lessons, "_load_brain", return_value=_brain_payload()), \
             patch.dict(os.environ, {
                 "ACTIVE_LESSONS_ENABLED": "true",
                 "ACTIVE_LESSONS_SHADOW": "false",
                 "ACTIVE_LESSONS_ALLOW_LEGACY_BRAIN": "false",
             }, clear=False):
            result = active_lessons.build_active_lesson_context("US")

        section = result["section"]
        self.assertIn("[active lessons]", section)
        self.assertIn("watch_only missed runup", section)
        self.assertIn("Low volume", section)
        self.assertNotIn("KIS token", section)
        self.assertNotIn("JSON parser", section)
        self.assertNotIn("Broker stale", section)
        self.assertNotIn("Legacy broad momentum", section)


class ActiveLessonSelectionPromptTests(unittest.TestCase):
    def test_enabled_lessons_reach_primary_retry_and_raw_metadata(self) -> None:
        from minority_report import analysts as analysts_module

        prompts: list[str] = []
        raw_calls: list[dict] = []

        def _fake_create(*, model, max_tokens, messages):
            prompts.append(messages[0]["content"])
            return SimpleNamespace(
                content=[SimpleNamespace(text="{}")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        parsed = [
            {
                "watchlist": ["AAPL", "MSFT"],
                "trade_ready": ["AAPL"],
                "reasons": {"AAPL": "ok"},
                "_fallback_mode": "selection_partial",
                "_parse_recovered": True,
            },
            {
                "watchlist": ["AAPL", "MSFT"],
                "trade_ready": ["AAPL"],
                "reasons": {"AAPL": "ok"},
            },
        ]

        with patch.object(active_lessons, "_load_lesson_candidates", return_value=_lesson_payload()), \
             patch.object(active_lessons, "_load_brain", return_value=_brain_payload()), \
             patch.dict(os.environ, {
                 "ACTIVE_LESSONS_ENABLED": "true",
                 "ACTIVE_LESSONS_SHADOW": "false",
                 "ACTIVE_LESSONS_ALLOW_LEGACY_BRAIN": "false",
             }, clear=False), \
             patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "_extract_json", side_effect=parsed), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda **kwargs: raw_calls.append(kwargs)):
            tickers, reasons = analysts_module.select_tickers(
                market="US",
                digest_prompt="market digest",
                consensus_mode="NEUTRAL",
                candidates=[
                    {"ticker": "AAPL", "price": 100.0, "volume": 1000, "change_rate": 1.0},
                    {"ticker": "MSFT", "price": 200.0, "volume": 1000, "change_rate": 0.5},
                ],
                market_change_pct=0.0,
                secondary_change_pct=0.0,
            )

        self.assertEqual(tickers, ["AAPL", "MSFT"])
        self.assertEqual(reasons["AAPL"], "ok")
        self.assertEqual(len(prompts), 2)
        self.assertIn("[active lessons]", prompts[0])
        self.assertIn("[active lessons]", prompts[1])
        self.assertNotIn('"price_targets"', prompts[1])
        self.assertEqual(len(raw_calls), 2)
        self.assertTrue(raw_calls[0]["extra"]["active_lessons"]["injected"])
        self.assertTrue(raw_calls[1]["extra"]["active_lessons"]["retry"])
        self.assertTrue(analysts_module.get_last_selection_meta()["active_lessons"]["retry"]["injected"])


if __name__ == "__main__":
    unittest.main()
