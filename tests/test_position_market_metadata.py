from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bot.state import StateMixin
from runtime.market_resolver import infer_ticker_market
from tools import live_preflight
from trading_bot import TradingBot


class _StateBot(StateMixin):
    is_paper = False

    def __init__(self) -> None:
        self.risk = SimpleNamespace(positions=[])

    def _ticker_market(self, ticker: str) -> str:
        return infer_ticker_market(ticker, unknown="KR")


class PositionMarketMetadataTests(unittest.TestCase):
    def test_save_positions_fills_missing_market(self) -> None:
        bot = _StateBot()
        bot.risk.positions = [
            {"ticker": "SOFI", "qty": 12, "display_currency": "USD"},
            {"ticker": "005930", "qty": 1, "display_currency": "KRW"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_runtime_path(*parts: str, make_parents: bool = True) -> Path:
                path = root.joinpath(*parts)
                if make_parents:
                    path.parent.mkdir(parents=True, exist_ok=True)
                return path

            with patch("bot.state.get_runtime_path", side_effect=fake_runtime_path):
                bot._save_positions()

            saved = json.loads((root / "state" / "live_open_positions.json").read_text(encoding="utf-8"))

        self.assertEqual(saved[0]["market"], "US")
        self.assertEqual(saved[1]["market"], "KR")

    def test_save_positions_uses_currency_before_dotted_ticker_heuristic(self) -> None:
        bot = _StateBot()
        bot.risk.positions = [{"ticker": "BRK.B", "qty": 1, "display_currency": "USD"}]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_runtime_path(*parts: str, make_parents: bool = True) -> Path:
                path = root.joinpath(*parts)
                if make_parents:
                    path.parent.mkdir(parents=True, exist_ok=True)
                return path

            with patch("bot.state.get_runtime_path", side_effect=fake_runtime_path):
                bot._save_positions()

            saved = json.loads((root / "state" / "live_open_positions.json").read_text(encoding="utf-8"))

        self.assertEqual(saved[0]["market"], "US")

    def test_dotted_us_ticker_market_inference_is_us(self) -> None:
        self.assertEqual(TradingBot._ticker_market(object(), "BRK.B"), "US")  # type: ignore[arg-type]
        self.assertEqual(live_preflight._position_market_from_ticker("BRK.B"), "US")
        self.assertEqual(live_preflight._position_market_from_ticker("005930"), "KR")

    def test_preflight_accepts_dotted_us_ticker_with_us_market(self) -> None:
        check = self._open_position_check(
            [{"ticker": "BRK.B", "market": "US", "qty": 1, "display_currency": "USD"}]
        )

        self.assertEqual(check.status, "PASS")

    def test_normalize_position_metadata_sets_explicit_market(self) -> None:
        bot = SimpleNamespace(
            _current_session_date_str=lambda market: "2026-05-15",
            _recover_position_mfe_state=lambda pos, market, origin="": pos,
        )
        pos = {"ticker": "MSFT", "qty": 1}

        normalized = TradingBot._normalize_position_metadata(
            bot,  # type: ignore[arg-type]
            pos,
            "US",
            origin="test",
            integrity="trusted",
            management_protected=False,
        )

        self.assertEqual(normalized["market"], "US")
        self.assertEqual(normalized["entry_session_date"], "2026-05-15")

    def test_restore_positions_resaves_normalized_market_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "state" / "live_open_positions.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([{"ticker": "SOFI", "entry": 10.0, "qty": 2}]), encoding="utf-8")
            save_positions = Mock()
            bot = SimpleNamespace(
                _mode="live",
                risk=SimpleNamespace(cash=1000.0, positions=[]),
                _verify_live_positions=lambda saved: saved,
                _ticker_market=lambda ticker: infer_ticker_market(ticker, unknown="KR"),
                _current_session_date_str=lambda market: "2026-05-15",
                _normalize_position_metadata=lambda pos, market, **_: pos.update({"market": market}) or pos,
                _save_positions=save_positions,
            )

            def fake_runtime_path(*parts: str, make_parents: bool = True) -> Path:
                return root.joinpath(*parts)

            with patch("trading_bot.get_runtime_path", side_effect=fake_runtime_path):
                TradingBot._restore_positions(bot)  # type: ignore[arg-type]

        self.assertEqual(bot.risk.positions[0]["market"], "US")
        save_positions.assert_called_once()

    def _open_position_check(self, items: list[dict]) -> live_preflight.CheckResult:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "state" / "live_open_positions.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(items), encoding="utf-8")

            def fake_runtime_path(*parts: str, make_parents: bool = True) -> Path:
                return root.joinpath(*parts)

            with patch.object(live_preflight, "get_runtime_path", side_effect=fake_runtime_path):
                return live_preflight._open_positions_market_metadata_check("live")

    def test_preflight_warns_when_position_market_missing(self) -> None:
        check = self._open_position_check(
            [{"ticker": "SOFI", "qty": 12, "display_currency": "USD"}]
        )

        self.assertEqual(check.status, "WARN")
        self.assertEqual(len(check.data["missing_market"]), 1)

    def test_preflight_fails_when_position_market_conflicts(self) -> None:
        check = self._open_position_check(
            [
                {
                    "ticker": "SOFI",
                    "market": "KR",
                    "qty": 12,
                    "display_currency": "USD",
                    "pathb_plan": {"market": "US"},
                }
            ]
        )

        self.assertEqual(check.status, "FAIL")
        reasons = {item["reason"] for item in check.data["conflicts"]}
        self.assertIn("ticker_market_mismatch", reasons)
        self.assertIn("pathb_plan_market_mismatch", reasons)
        self.assertIn("usd_currency_market_mismatch", reasons)

    def test_preflight_passes_when_position_market_matches(self) -> None:
        check = self._open_position_check(
            [{"ticker": "005930", "market": "KR", "qty": 1, "display_currency": "KRW"}]
        )

        self.assertEqual(check.status, "PASS")


if __name__ == "__main__":
    unittest.main()
