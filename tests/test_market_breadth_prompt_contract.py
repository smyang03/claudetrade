from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from phase1_trainer.digest_builder import build_breadth_summary, digest_to_prompt
from minority_report import analysts, tuner


class DigestBreadthContractTests(unittest.TestCase):
    def test_breadth_summary_counts_are_deterministic(self) -> None:
        summary = build_breadth_summary(
            "US",
            {
                "NVDA": {
                    "name": "NVIDIA",
                    "change_pct": 2.5,
                    "rsi": 72,
                    "macd": "골든크로스",
                    "vol_ratio": 1.8,
                    "pos_52w": 99,
                },
                "AAPL": {
                    "name": "Apple",
                    "change_pct": -1.0,
                    "rsi": 28,
                    "macd": "데드크로스",
                    "vol_ratio": 0.9,
                    "pos_52w": 55,
                },
                "RSI": {
                    "name": "Rush Street",
                    "change_pct": 0.0,
                    "rsi": 50,
                    "macd": "골든크로스",
                    "vol_ratio": 3.2,
                    "pos_52w": 95,
                },
            },
            {"vix": None, "dxy": 0, "sectors": {"XLK": 1.2, "XLF": -0.8}},
        )

        self.assertEqual(summary["universe_count"], 3)
        self.assertEqual(summary["advancers"], 1)
        self.assertEqual(summary["decliners"], 1)
        self.assertEqual(summary["unchanged"], 1)
        self.assertEqual(summary["golden_cross"], 2)
        self.assertEqual(summary["dead_cross"], 1)
        self.assertEqual(summary["rsi_overbought"], 1)
        self.assertEqual(summary["rsi_oversold"], 1)
        self.assertEqual(summary["volume_spike"], 2)
        self.assertIn("vix_missing", summary["data_quality_flags"])
        self.assertIn("dxy_missing", summary["data_quality_flags"])

    def test_digest_prompt_places_breadth_before_tickers_and_marks_missing_risk_data(self) -> None:
        technicals = {
            "RSI": {
                "name": "Rush Street",
                "close": 27.16,
                "change_pct": 13.19,
                "rsi": 88.5,
                "macd": "골든크로스",
                "bb_pct": 132,
                "vol_ratio": 0.5,
                "pos_52w": 90,
            }
        }
        context = {
            "sp500": {"change_pct": -0.04},
            "nasdaq": {"change_pct": 0.04},
            "vix": None,
            "dxy": 0,
            "sectors": {"XLK": 1.2},
            "regime": "ranging",
        }
        digest = {
            "date": "2026-05-01",
            "market": "US",
            "context": context,
            "technicals": technicals,
            "breadth_summary": build_breadth_summary("US", technicals, context),
        }

        prompt = digest_to_prompt(digest)

        self.assertIn("VIX N/A (결측)", prompt)
        self.assertIn("DXY N/A (결측)", prompt)
        self.assertLess(prompt.index("▶ 시장 breadth 요약"), prompt.index("▶ 종목 기술 지표"))
        self.assertIn("[Rush Street(ticker=RSI)]", prompt)
        self.assertIn("시장 mode 판단은 breadth 요약", prompt)


class AnalystPromptContractTests(unittest.TestCase):
    def test_us_bear_persona_uses_us_risk_axes(self) -> None:
        us_bear = analysts._persona_for("bear", "US")
        kr_bear = analysts._persona_for("bear", "KR")

        self.assertIn("HYG", us_bear)
        self.assertIn("TNX", us_bear)
        self.assertIn("KR 지표", us_bear)
        self.assertIn("VKOSPI", kr_bear)

    def test_call_analyst_prompt_includes_breadth_first_contract(self) -> None:
        captured: dict[str, str] = {}

        def fake_create(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text=(
                            '{"stance":"NEUTRAL","confidence":0.5,'
                            '"key_reason":"breadth mixed",'
                            '"full_reasoning":"breadth mixed",'
                            '"top_risks":[],"suggested_strategy":"관망",'
                            '"suggested_size_pct":0}'
                        )
                    )
                ],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )

        with (
            patch.object(analysts.client.messages, "create", side_effect=fake_create),
            patch.object(analysts, "credit_record"),
            patch.object(analysts, "save_raw_call"),
        ):
            analysts.call_analyst(
                "bear",
                "[2026-05-01 US 시장 데이터]\n▶ 시장 breadth 요약\n  상승/하락/보합: 1/1/0",
                "",
                "",
                market="US",
            )

        self.assertIn("시장 breadth 우선 계약", captured["prompt"])
        self.assertIn("HYG", captured["prompt"])
        self.assertIn("개별 종목은 시장 판단의 보조 예시", captured["prompt"])


class TunePromptContractTests(unittest.TestCase):
    def test_tune_prompt_contains_breadth_delta_and_maintain_streak(self) -> None:
        captured: dict[str, str] = {}

        def fake_create(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text=(
                            '{"action":"MAINTAIN","mode":"MILD_BULL",'
                            '"size_adj":0,"sl_adj":0.0,'
                            '"momentum_wait_adjust_min":0,'
                            '"entry_priority_cutoff_adjust":0.0,'
                            '"kr_momentum_atr_cap_adjust":0.0,'
                            '"kr_momentum_atr_cap_high_adjust":0.0,'
                            '"reason":"breadth stable","warning":null}'
                        )
                    )
                ],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )

        morning = {
            "universe_count": 10,
            "advancers": 7,
            "decliners": 3,
            "advance_ratio": 0.7,
            "golden_cross": 6,
            "dead_cross": 4,
            "rsi_overbought": 2,
            "rsi_oversold": 1,
        }
        current = {
            "universe_count": 10,
            "advancers": 4,
            "decliners": 6,
            "advance_ratio": 0.4,
            "golden_cross": 4,
            "dead_cross": 6,
            "rsi_overbought": 1,
            "rsi_oversold": 2,
        }

        with (
            patch.object(tuner.client.messages, "create", side_effect=fake_create),
            patch.object(tuner, "credit_record"),
            patch.object(tuner, "save_raw_call"),
            patch.object(tuner.BrainDB, "update_tuning_pattern"),
        ):
            result = tuner.tune(
                "US",
                120,
                {
                    "index_change": 0.2,
                    "index_slope_30m": -0.1,
                    "volume_trend": "low",
                    "alerts": [],
                    "positions": [],
                    "runtime_overrides": {},
                    "morning_breadth": morning,
                    "current_breadth": current,
                    "previous_tune_action": "MAINTAIN",
                    "maintain_streak": 3,
                },
                {"consensus": {"mode": "MILD_BULL"}, "digest_raw": {"breadth_summary": morning}},
                "",
            )

        self.assertEqual(result["action"], "MAINTAIN")
        self.assertIn("아침 breadth", captured["prompt"])
        self.assertIn("현재 breadth", captured["prompt"])
        self.assertIn("breadth 변화", captured["prompt"])
        self.assertIn("연속 MAINTAIN 횟수: 3", captured["prompt"])
        self.assertIn("advance_ratio -30%p", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
