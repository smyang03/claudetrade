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
                    "action_hint": "watch_only missed runup is high; promote when veto is weak.",
                    "claude_actionable": True,
                    "ops_flag": False,
                    "min_sample": 20,
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
                    "action_hint": "",
                    "claude_actionable": False,
                    "ops_flag": True,
                    "min_sample": 1,
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
                 "ACTIVE_LESSONS_ALLOW_RECENT_DAYS": "true",
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
        self.assertEqual(result["metadata"]["ignored_reasons"]["ops_flag"], 1)
        self.assertEqual(result["metadata"]["ignored_reasons"]["execution_scope_excluded"], 2)

    def test_recent_day_lessons_are_disabled_by_default(self) -> None:
        brain = {
            "markets": {
                "US": {
                    "recent_days": [
                        {
                            "date": "2026-05-20",
                            "key_lesson": "Unapproved daily lesson must not reach prompt.",
                            "trades": 1,
                        }
                    ],
                    "execution_lessons": [],
                }
            }
        }
        with patch.object(active_lessons, "_load_lesson_candidates", return_value={"markets": {"US": []}}), \
             patch.object(active_lessons, "_load_brain", return_value=brain), \
             patch.dict(os.environ, {
                 "ACTIVE_LESSONS_ENABLED": "true",
                 "ACTIVE_LESSONS_SHADOW": "false",
                 "ACTIVE_LESSONS_ALLOW_RECENT_DAYS": "false",
             }, clear=False):
            result = active_lessons.build_active_lesson_context("US")

        self.assertEqual(result["section"], "")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["metadata"]["ignored_reasons"]["recent_day_disabled"], 1)

    def test_lesson_candidates_require_action_hint_and_use_500_char_limit(self) -> None:
        marker = "TAIL_MARKER_AFTER_220"
        payload = {
            "markets": {
                "US": [
                    {
                        "id": "long_hint",
                        "market": "US",
                        "scope": "selection",
                        "summary": "summary should not appear",
                        "action_hint": ("x" * 260) + marker,
                        "claude_actionable": True,
                        "ops_flag": False,
                        "min_sample": 1,
                        "breached": True,
                        "severity": "high",
                        "confidence": 0.8,
                        "sample_count": 2,
                        "generated_at": "2026-05-01T23:00:00",
                        "expires_at": "2099-01-01T00:00:00",
                    },
                    {
                        "id": "missing_hint",
                        "market": "US",
                        "scope": "selection",
                        "summary": "missing hint should not appear",
                        "claude_actionable": True,
                        "ops_flag": False,
                        "min_sample": 1,
                        "breached": True,
                        "severity": "high",
                        "confidence": 0.8,
                        "sample_count": 2,
                        "generated_at": "2026-05-01T23:00:00",
                        "expires_at": "2099-01-01T00:00:00",
                    },
                ]
            }
        }
        with patch.object(active_lessons, "_load_lesson_candidates", return_value=payload), \
             patch.object(active_lessons, "_load_brain", return_value={"markets": {"US": {}}}), \
             patch.dict(os.environ, {"ACTIVE_LESSONS_ENABLED": "true", "ACTIVE_LESSONS_SHADOW": "false"}, clear=False):
            result = active_lessons.build_active_lesson_context("US")

        self.assertIn(marker, result["section"])
        self.assertNotIn("summary should not appear", result["section"])
        self.assertEqual(result["metadata"]["ignored_reasons"]["missing_action_hint"], 1)

    def test_recent_day_cap_and_execution_excluded_flags(self) -> None:
        brain = {
            "markets": {
                "US": {
                    "recent_days": [
                        {"date": "2026-05-01", "key_lesson": "First valid market lesson.", "trades": 5},
                        {"date": "2026-05-02", "key_lesson": "Excluded execution contaminated lesson.", "trades": 5, "execution_learning_excluded": True},
                        {"date": "2026-05-03", "key_lesson": "Second valid market lesson.", "trades": 4},
                        {"date": "2026-05-04", "key_lesson": "Third valid market lesson should be capped.", "trades": 3},
                    ],
                    "execution_lessons": ["손실 매도 주요 사유: loss_cap"],
                }
            }
        }
        with patch.object(active_lessons, "_load_lesson_candidates", return_value={"markets": {"US": []}}), \
             patch.object(active_lessons, "_load_brain", return_value=brain), \
             patch.dict(os.environ, {
                 "ACTIVE_LESSONS_ENABLED": "true",
                 "ACTIVE_LESSONS_SHADOW": "false",
                 "ACTIVE_LESSONS_ALLOW_RECENT_DAYS": "true",
             }, clear=False):
            result = active_lessons.build_active_lesson_context("US")

        recent_items = [item for item in result["items"] if item["source"] == "recent_day"]
        self.assertEqual(len(recent_items), 2)
        self.assertNotIn("Excluded execution contaminated", result["section"])
        self.assertNotIn("loss_cap", result["section"])
        self.assertEqual(result["metadata"]["ignored_reasons"]["execution_learning_excluded"], 1)
        self.assertEqual(result["metadata"]["ignored_reasons"]["execution_scope_excluded"], 1)
        self.assertEqual(result["metadata"]["ignored_reasons"]["source_cap"], 1)

    def test_recent_day_contamination_and_prompt_policy_exclusion_are_not_injected(self) -> None:
        brain = {
            "markets": {
                "US": {
                    "recent_days": [
                        {"date": "2026-05-01", "key_lesson": "Clean market lesson.", "trades": 5},
                        {
                            "date": "2026-05-02",
                            "key_lesson": "Contaminated execution lesson must stay out.",
                            "trades": 1,
                            "execution_contaminated": True,
                        },
                        {
                            "date": "2026-05-03",
                            "key_lesson": "Prompt policy excluded lesson must stay out.",
                            "trades": 1,
                            "prompt_policy_excluded": True,
                        },
                    ],
                    "execution_lessons": [],
                }
            }
        }
        with patch.object(active_lessons, "_load_lesson_candidates", return_value={"markets": {"US": []}}), \
             patch.object(active_lessons, "_load_brain", return_value=brain), \
             patch.dict(os.environ, {
                 "ACTIVE_LESSONS_ENABLED": "true",
                 "ACTIVE_LESSONS_SHADOW": "false",
                 "ACTIVE_LESSONS_ALLOW_RECENT_DAYS": "true",
             }, clear=False):
            result = active_lessons.build_active_lesson_context("US")

        self.assertIn("Clean market lesson.", result["section"])
        self.assertNotIn("Contaminated execution lesson", result["section"])
        self.assertNotIn("Prompt policy excluded lesson", result["section"])
        self.assertEqual(result["metadata"]["ignored_reasons"]["execution_contaminated"], 1)
        self.assertEqual(result["metadata"]["ignored_reasons"]["prompt_policy_excluded"], 1)


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

    def test_get_three_judgments_uses_active_lesson_context_for_r1_and_r2(self) -> None:
        from minority_report import analysts as analysts_module

        r1_contexts: list[str] = []
        r2_contexts: list[str] = []
        r1_meta: list[dict] = []
        r2_meta: list[dict] = []

        def _fake_r1(*args, **kwargs):
            r1_contexts.append(kwargs.get("lesson_context", ""))
            r1_meta.append(kwargs.get("lesson_context_meta") or {})
            return {"stance": "NEUTRAL", "confidence": 0.5, "key_reason": "mixed"}

        def _fake_r2(*args, **kwargs):
            r2_contexts.append(kwargs.get("lesson_context", ""))
            r2_meta.append(kwargs.get("lesson_context_meta") or {})
            return {"stance": "NEUTRAL", "confidence": 0.5, "key_reason": "mixed", "changed": False}

        active = {
            "section": "[active lessons]\n- selection: active action hint",
            "metadata": {"enabled": True, "shadow": False, "injected": True, "count": 1, "ignored_reasons": {"ops_flag": 1}},
        }
        with patch.object(analysts_module, "build_active_lesson_context", return_value=active), \
             patch.object(analysts_module, "call_analyst", side_effect=_fake_r1), \
             patch.object(analysts_module, "call_analyst_debate", side_effect=_fake_r2), \
             patch("claude_memory.brain.generate_analyst_summary", return_value=""), \
             patch("claude_memory.brain.get_debate_summary", return_value=""), \
             patch("claude_memory.brain.save_debate_result", return_value=None):
            analysts_module.get_three_judgments(
                "digest",
                "brain",
                "correction",
                delay=0,
                market="US",
                lesson_context="legacy summary should not be used",
            )

        self.assertEqual(len(r1_contexts), 3)
        self.assertEqual(len(r2_contexts), 3)
        self.assertTrue(all("active action hint" in ctx for ctx in r1_contexts + r2_contexts))
        self.assertTrue(all("legacy summary" not in ctx for ctx in r1_contexts + r2_contexts))
        self.assertTrue(all(meta.get("injected") is True for meta in r1_meta + r2_meta))
        self.assertEqual(r1_meta[0]["ignored_reasons"]["ops_flag"], 1)

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
