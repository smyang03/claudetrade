from __future__ import annotations

import os
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
