from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from minority_report import analysts as analysts_module
from minority_report.analysts import (
    _digest_news_excerpt,
    _json_array_object_cap,
    _recover_compact_watch_selection,
)


class SelectionPromptStabilityTests(unittest.TestCase):
    def test_json_array_object_cap_keeps_valid_json_and_whole_objects(self) -> None:
        first = {"ticker": "AAA", "blob": "x" * 30}
        second = {"ticker": "BBB", "blob": "y" * 200}
        first_encoded = json.dumps(first, ensure_ascii=False, separators=(",", ":"))

        text, included, omitted = _json_array_object_cap(
            [first, second],
            max_chars=2 + len(first_encoded),
        )

        self.assertEqual(json.loads(text), [first])
        self.assertEqual(included, [first])
        self.assertEqual(omitted, 1)
        self.assertNotIn("BBB", text)

    def test_compact_watch_recovery_preserves_wl_from_extra_brace_response(self) -> None:
        broken = (
            '{"wl":["319400","356680","012860"],"tr":["319400"],"ca":['
            '{"t":"319400","a":"BUY_READY","pt":{"ref":43400,"lo":42500}},'
            '{"t":"356680","a":"WATCH","s":"gap_pullback","inv":"price_fails_at_high"}},'
            '{"t":"012860","a":"WATCH","s":"momentum","inv":"fade_deepens"}}]}'
        )

        recovered = _recover_compact_watch_selection(broken)

        self.assertEqual(recovered["wl"], ["319400", "356680", "012860"])
        self.assertEqual(recovered["tr"], [])
        self.assertEqual(recovered["ca"], [])
        self.assertTrue(recovered["_parse_recovered"])
        self.assertEqual(recovered["_fallback_mode"], "compact_watch_recovered")
        self.assertEqual(recovered["_recovered_raw_trade_ready"], ["319400"])

    def test_digest_news_excerpt_keeps_news_when_digest_head_is_truncated(self) -> None:
        digest = (
            "[2026-05-15 US 시장 데이터]\n"
            "시장 mode 판단은 breadth 요약과 지수/매크로를 우선한다.\n"
            + "x" * 500
            + "\n▶ 주요 뉴스 (중요도 상위)\n"
            "  • [TSLA] Tesla supplier headline\n"
            "  • [NVDA] AI demand headline\n"
            "\n▶ 전일 결과: NEUTRAL\n"
        )

        excerpt = _digest_news_excerpt(digest)

        self.assertIn("Digest news excerpt:", excerpt)
        self.assertIn("Tesla supplier headline", excerpt)
        self.assertNotIn("전일 결과", excerpt)

    def test_select_tickers_prompt_includes_digest_news_excerpt(self) -> None:
        captured = {}

        def fake_create(*, model, max_tokens, messages):
            captured["prompt"] = messages[0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"watchlist":["TSLA"],"trade_ready":[],"reasons":{"TSLA":"watch"}}')],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        digest = (
            "[2026-05-15 US 시장 데이터]\n"
            "시장 mode 판단은 breadth 요약과 지수/매크로를 우선한다.\n"
            + "x" * 500
            + "\n▶ 주요 뉴스 (중요도 상위)\n"
            "  • [TSLA] Tesla supplier headline\n"
        )
        candidates = [
            {"ticker": "TSLA", "price": 100.0, "volume": 1_000_000, "change_rate": 1.2},
        ]

        env = {
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false",
            "CANDIDATE_QUALITY_TRAINER_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch.object(analysts_module.client.messages, "create", side_effect=fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda *args, **kwargs: None):
            analysts_module.select_tickers("US", digest, "NEUTRAL", candidates)

        self.assertIn("Digest news excerpt:", captured["prompt"])
        self.assertIn("Tesla supplier headline", captured["prompt"])

    def test_select_tickers_prompt_includes_ticker_scoped_news_hint_only_for_matching_candidate(self) -> None:
        captured = {}

        def fake_create(*, model, max_tokens, messages):
            captured["prompt"] = messages[0]["content"]
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text='{"watchlist":["AAPL","MSFT"],"trade_ready":[],"reasons":{"AAPL":"watch","MSFT":"watch"}}'
                    )
                ],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        candidates = [
            {
                "ticker": "AAPL",
                "price": 200.0,
                "volume": 1_000_000,
                "change_rate": 2.4,
                "news_or_earnings_flag": True,
                "news_or_earnings_count": 2,
                "news_or_earnings_sources": ["Finnhub", "SEC"],
                "news_or_earnings_sample_title": "Apple product launch catalyst",
                "news_quality": "mixed",
                "news_date_quality": "unknown_date",
            },
            {
                "ticker": "MSFT",
                "price": 420.0,
                "volume": 900_000,
                "change_rate": 1.1,
            },
        ]

        env = {
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false",
            "CANDIDATE_QUALITY_TRAINER_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch.object(analysts_module.client.messages, "create", side_effect=fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda *args, **kwargs: None):
            analysts_module.select_tickers("US", "Market context without company headlines", "NEUTRAL", candidates)

        prompt_lines = captured["prompt"].splitlines()
        aapl_line = next(line for line in prompt_lines if line.startswith("AAPL "))
        msft_line = next(line for line in prompt_lines if line.startswith("MSFT "))
        self.assertIn("news=count=2|src=Finnhub,SEC|quality=mixed|date=unknown_date|title=Apple product launch catalyst", aapl_line)
        self.assertNotIn("news=", msft_line)


if __name__ == "__main__":
    unittest.main()
