from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from minority_report import analysts as analysts_module
from trading_bot import TradingBot


def _bot_stub() -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.today_judgment = {"consensus": {"mode": "NEUTRAL"}}
    bot._current_session_date_str = lambda market: "2026-06-08"  # type: ignore[method-assign]
    bot._runtime_bool = lambda key, default=False: False  # type: ignore[method-assign]
    bot._annotate_selection_execution_features = (  # type: ignore[method-assign]
        lambda market, candidates, mode, prefetch_intraday_evidence=True: [dict(row or {}) for row in candidates]
    )
    bot._build_selection_evidence_pack = lambda *args, **kwargs: {}  # type: ignore[method-assign]
    bot._write_funnel_event = lambda *args, **kwargs: None  # type: ignore[method-assign]
    return bot


class SelectionNewsRuntimeTests(unittest.TestCase):
    def test_live_selection_prompt_pool_enriches_candidates_from_daily_news_payload(self) -> None:
        bot = _bot_stub()
        candidates = [
            {
                "ticker": "017670",
                "name": "SK Telecom",
                "market": "KR",
                "price": 50000,
                "volume": 100000,
                "change_rate": 1.7,
                "source": "opening_fresh",
                "prior_day_traded_value": 20_000_000_000,
                "extended_change_pct": 3.2,
            }
        ]
        payload = {
            "date": "2026-06-08",
            "target_source": "preopen_top60",
            "corp_news": {
                "017670": {
                    "name": "SK Telecom",
                    "items": [
                        {
                            "source": "Naver",
                            "source_type": "company_news",
                            "importance": "A",
                            "date": "2026-06-08",
                            "ticker": "017670",
                            "title": "017670 signs AI cloud supply contract",
                            "summary": "SK Telecom signs an AI cloud supply contract.",
                        }
                    ],
                }
            },
        }

        env = {
            "CANDIDATE_QUALITY_TRAINER_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "preopen.news_enrichment.load_preopen_news_payload",
            return_value=(payload, "memory"),
        ):
            enriched, prompt_rows, prompt_meta, evidence = bot._prepare_selection_prompt_pool_with_evidence(
                "KR",
                candidates,
                "live",
            )

        self.assertEqual(evidence, {})
        self.assertEqual(prompt_meta["news_enrichment"]["status"], "ok")
        self.assertEqual(prompt_meta["news_enrichment"]["flagged_count"], 1)
        self.assertEqual(prompt_meta["news_enrichment"]["news_prompt_eligible_count"], 1)
        self.assertTrue(enriched[0]["news_prompt_eligible"])
        self.assertEqual(enriched[0]["news_signal_type"], "direct_catalyst")
        self.assertTrue(prompt_rows[0]["news_prompt_eligible"])
        self.assertEqual(prompt_rows[0]["news_signal_type"], "direct_catalyst")
        self.assertIn("direct_catalyst", prompt_rows[0]["news_prompt_summary"])

    def test_compact_selection_prompt_keeps_news_hint_before_line_truncation(self) -> None:
        bot = _bot_stub()
        candidates = [
            {
                "ticker": "005930",
                "name": "Samsung Electronics",
                "market": "KR",
                "price": 308750,
                "volume": 26_150_000,
                "change_rate": -6.16,
                "source": "opening_fresh",
                "market_type": "KOSPI",
                "liquidity_bucket": "high",
                "candidate_quality_score": 86,
                "quality_data_gaps": ["flow_missing", "index_history_missing"],
                "relative_strength_20d_pct": 13.2,
                "relative_strength_60d_pct": 34.2,
                "turnover_ratio_20d": 0.8,
                "volume_ratio_20d": 0.8,
                "trainer_cohort_reliability": 0.2,
                "trainer_tier": "PLAN_B",
                "evidence_class": "MISSING_OR_STALE",
                "selection_evidence_action_ceiling": "WATCH",
                "selection_evidence_data_state": "missing",
                "selection_evidence_missing_reason": "coverage_gap",
                "prior_day_traded_value": 20_000_000_000,
                "extended_change_pct": 1.2,
            }
        ]
        payload = {
            "date": "2026-06-08",
            "corp_news": {
                "005930": {
                    "name": "Samsung Electronics",
                    "items": [
                        {
                            "source": "Naver",
                            "source_type": "company_news",
                            "importance": "A",
                            "date": "2026-06-08",
                            "ticker": "005930",
                            "title": "005930 signs AI semiconductor supply contract",
                            "summary": "Samsung signs an AI semiconductor supply contract.",
                        }
                    ],
                }
            },
        }
        captured: dict[str, str] = {}

        def fake_create(*, model, max_tokens, messages):
            captured["prompt"] = messages[0]["content"]
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text='{"wl":["005930"],"tr":[],"ca":[{"t":"005930","a":"WATCH","s":"mean_reversion","c":0.1,"fr":"STALE","mat":"WEAK","ceil":"WATCH","rc":"WATCH","blk":[],"inv":"setup_invalid","pt":{}}]}'
                    )
                ],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        env = {
            "CANDIDATE_QUALITY_TRAINER_ENABLED": "true",
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "true",
            "CLAUDE_SELECTION_COMPACT_CANDIDATE_LINE_MAX_CHARS": "260",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "preopen.news_enrichment.load_preopen_news_payload",
            return_value=(payload, "memory"),
        ), patch.object(
            analysts_module.client.messages,
            "create",
            side_effect=fake_create,
        ), patch.object(
            analysts_module,
            "credit_record",
            lambda *args, **kwargs: None,
        ), patch.object(
            analysts_module,
            "save_raw_call",
            lambda *args, **kwargs: None,
        ):
            _enriched, prompt_rows, prompt_meta, evidence = bot._prepare_selection_prompt_pool_with_evidence(
                "KR",
                candidates,
                "live",
            )
            analysts_module.select_tickers(
                "KR",
                "Market context",
                "DEFENSIVE",
                candidates,
                execution_phase="intraday_live",
                evidence_by_ticker=evidence,
                prompt_pool_override=prompt_rows,
                prompt_pool_meta_override=prompt_meta,
                session_date="2026-06-08",
            )

        candidate_line = next(line for line in captured["prompt"].splitlines() if line.startswith("005930 "))
        self.assertLessEqual(len(candidate_line), 260)
        self.assertGreater(len(candidate_line), 120)
        self.assertIn("news=", candidate_line)
        self.assertIn("eligible=true", candidate_line)
        self.assertIn("signal=direct_catalyst", candidate_line)


if __name__ == "__main__":
    unittest.main()
