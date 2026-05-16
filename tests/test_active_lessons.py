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
                    {
                        "date": "2026-05-02",
                        "key_lesson": "실행오염: 브로커 동기화 거래",
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
    def test_disabled_lessons_do_not_query_or_inject(self) -> None:
        with patch.object(active_lessons, "_load_lesson_candidates", return_value=_lesson_payload()), \
             patch.object(active_lessons, "_load_brain", return_value=_brain_payload()), \
             patch.dict(os.environ, {
                 "ACTIVE_LESSONS_ENABLED": "false",
                 "ACTIVE_LESSONS_SHADOW": "true",
                 "ACTIVE_LESSONS_ALLOW_LEGACY_BRAIN": "false",
             }, clear=False):
            result = active_lessons.build_active_lesson_context("US")

        self.assertEqual(result["section"], "")
        self.assertEqual(result["preview"], "")
        self.assertFalse(result["metadata"]["injected"])
        self.assertEqual(result["metadata"]["count"], 0)
        self.assertTrue(result["metadata"]["disabled_skipped"])

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
        self.assertNotIn("실행오염", section)
        self.assertNotIn("브로커 동기화", section)
        self.assertNotIn("Legacy broad momentum", section)


class ActiveLessonSelectionPromptTests(unittest.TestCase):
    def test_lesson_context_helper_no_longer_hard_clamps_at_500_chars(self) -> None:
        from minority_report import analysts as analysts_module

        long_context = "x" * 1600
        with patch.dict(os.environ, {"ACTIVE_LESSONS_ANALYST_MAX_CHARS": "3000"}, clear=False):
            text, meta = analysts_module._lesson_context_for_prompt(long_context, scope="r1")

        self.assertEqual(len(text), 1600)
        self.assertTrue(meta["injected"])
        self.assertEqual(meta["omitted_chars"], 0)

    def test_selection_retry_prompt_uses_lesson_helper_not_500_char_slice(self) -> None:
        from minority_report import analysts as analysts_module

        marker = "TAIL_MARKER_AFTER_500"
        long_context = ("x" * 650) + marker
        with patch.dict(os.environ, {"ACTIVE_LESSONS_ANALYST_MAX_CHARS": "3000"}, clear=False):
            prompt = analysts_module._build_selection_retry_prompt(
                "US",
                "NEUTRAL",
                [{"ticker": "AAPL", "price": 100.0, "volume": 1000, "change_rate": 1.0}],
                market_change_pct=0.0,
                secondary_change_pct=0.0,
                active_lessons_context=long_context,
            )

        self.assertIn(marker, prompt)

    def test_debate_prompt_receives_lesson_context_when_enabled(self) -> None:
        from minority_report import analysts as analysts_module

        captured: dict[str, object] = {}

        def _fake_create(*, model, max_tokens, messages):
            captured["model"] = model
            captured["prompt"] = messages[0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"stance":"NEUTRAL","confidence":0.5,"key_reason":"mixed","changed":false}')],
                usage=SimpleNamespace(input_tokens=123, output_tokens=45),
            )

        raw_calls: list[dict] = []
        with patch.dict(os.environ, {"ACTIVE_LESSONS_DEBATE_ENABLED": "true", "ACTIVE_LESSONS_DEBATE_MAX_CHARS": "1200"}, clear=False), \
             patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda **kwargs: raw_calls.append(kwargs)):
            result = analysts_module.call_analyst_debate(
                "bear",
                {"stance": "MILD_BEAR", "confidence": 0.6, "key_reason": "risk"},
                {"bull": {"stance": "MILD_BULL", "confidence": 0.6, "key_reason": "breadth"}},
                "digest",
                market="US",
                lesson_context="lesson abc",
            )

        self.assertEqual(result["stance"], "NEUTRAL")
        self.assertIn("[recent lesson candidates]", str(captured["prompt"]))
        self.assertIn("lesson abc", str(captured["prompt"]))
        self.assertTrue(raw_calls[0]["extra"]["lesson_context"]["injected"])
        self.assertEqual(raw_calls[0]["extra"]["token_budget"]["max_tokens"], 900)
        self.assertEqual(raw_calls[0]["extra"]["model_route"]["analyst"], "bear")
        self.assertTrue(raw_calls[0]["extra"]["persona"]["us_bear_persona"])

    def test_r1_model_routes_are_role_specific_and_us_bear_persona_is_preserved(self) -> None:
        from minority_report import analysts as analysts_module

        captured: dict[str, object] = {}

        def _fake_create(*, model, max_tokens, messages):
            captured["model"] = model
            captured["prompt"] = messages[0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"stance":"CAUTIOUS_BEAR","confidence":0.6,"key_reason":"risk"}')],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )

        raw_calls: list[dict] = []
        with patch.dict(os.environ, {"BEAR_R1_MODEL": "bear-model", "R1_MODEL": "fallback-model"}, clear=False), \
             patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda **kwargs: raw_calls.append(kwargs)):
            analysts_module.call_analyst(
                "bear",
                "digest",
                "brain",
                "correction",
                market="US",
                lesson_context="lesson",
            )

        self.assertEqual(raw_calls[0]["extra"]["model_route"]["r1_model"], "bear-model")
        self.assertEqual(raw_calls[0]["extra"]["token_budget"]["max_tokens"], 700)
        self.assertTrue(raw_calls[0]["extra"]["lesson_context"]["injected"])
        self.assertEqual(captured["model"], "bear-model")
        self.assertIn("미국 주식 헤지펀드 리스크 매니저", str(captured["prompt"]))

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
                 "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false",
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
        meta = analysts_module.get_last_selection_meta()
        self.assertTrue(meta["active_lessons"]["retry"]["injected"])
        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["_selection_retry_trade_ready_ignored"], ["AAPL"])


if __name__ == "__main__":
    unittest.main()
