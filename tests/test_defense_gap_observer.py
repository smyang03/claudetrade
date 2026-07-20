from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import defense_gap_observer as obs


class DefenseGapObserverTests(unittest.TestCase):
    def _patched_path(self, tmp):
        def fake(kind, *parts):
            return Path(tmp) / "_".join(str(p) for p in parts)
        return fake

    def test_core_entry_regime_records_defensive_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(obs, "get_runtime_path", side_effect=lambda *a: Path(tmp) / a[-1]):
                obs.record_core_entry_regime(
                    session_date="2026-07-20", market="KR", ticker="275280",
                    source_strategy="kr_factor_trend_v1", regime="DEFENSIVE",
                    analyst_blocked_for_discretionary=True,
                )
                f = list(Path(tmp).glob("core_entry_regime_*"))
                self.assertEqual(len(f), 1)
                row = json.loads(f[0].read_text(encoding="utf-8").strip())
                self.assertEqual(row["ticker"], "275280")
                self.assertTrue(row["defensive_regime"])
                self.assertTrue(row["discretionary_would_block"])

    def test_non_defensive_regime_flag_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(obs, "get_runtime_path", side_effect=lambda *a: Path(tmp) / a[-1]):
                obs.record_core_entry_regime(
                    session_date="2026-07-20", market="KR", ticker="X",
                    source_strategy="s", regime="MODERATE_BULL",
                )
                row = json.loads(list(Path(tmp).glob("core_entry_regime_*"))[0].read_text(encoding="utf-8").strip())
                self.assertFalse(row["defensive_regime"])

    def test_next_open_sell_records_close_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(obs, "get_runtime_path", side_effect=lambda *a: Path(tmp) / a[-1]):
                obs.record_next_open_sell_scheduled(
                    session_date="2026-07-20", market="KR", ticker="275280",
                    close_price=40745.0, pnl_pct_at_schedule=-2.65, reason="급락 손실중",
                )
                row = json.loads(list(Path(tmp).glob("next_open_sell_scheduled_*"))[0].read_text(encoding="utf-8").strip())
                self.assertEqual(row["scheduled_close_price"], 40745.0)
                self.assertEqual(row["pnl_pct_at_schedule"], -2.65)

    def test_bad_number_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(obs, "get_runtime_path", side_effect=lambda *a: Path(tmp) / a[-1]):
                obs.record_next_open_sell_scheduled(
                    session_date="2026-07-20", market="US", ticker="SCHG",
                    close_price="n/a",
                )
                row = json.loads(list(Path(tmp).glob("next_open_sell_scheduled_*"))[0].read_text(encoding="utf-8").strip())
                self.assertIsNone(row["scheduled_close_price"])


if __name__ == "__main__":
    unittest.main()
