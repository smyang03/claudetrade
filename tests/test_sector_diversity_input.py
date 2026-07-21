from __future__ import annotations

"""섹터 다양성 캡의 입력이 실제로 공급되는지.

analysts.py의 후보 다양성 캡은 `if sector and sector_counts.get(sector,0) >= cap` 형태라
sector가 비면 조용히 통과한다. 2026-07-22 실측에서 ticker_selection_log 35,124행의 sector가
KR/US 100% 비어 있었다 — universe_manager는 값을 전달만 하고 생산자가 없어 섹터 집중 방지가
코드만 있고 영구 미작동이었다. 캐시 공급이 끊기면 같은 상태로 조용히 되돌아가므로 고정한다.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import universe_manager as um


class SectorLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "sector_map.json"
        self.path.write_text(
            json.dumps({
                "US": {
                    "NVDA": {"sector": "Technology", "industry": "Semiconductors"},
                    "AAL": {"sector": "Industrials", "industry": "Airlines"},
                    "PLAIN": "Energy",
                },
                "KR": {},
            }),
            encoding="utf-8",
        )
        um._sector_map_cache = None
        um._sector_map_mtime = 0.0

    def tearDown(self) -> None:
        um._sector_map_cache = None
        um._sector_map_mtime = 0.0
        self._tmp.cleanup()

    def test_lookup_returns_sector_for_known_ticker(self) -> None:
        with patch.object(um, "_SECTOR_MAP_PATH", self.path):
            self.assertEqual(um._sector_lookup("US", "NVDA"), "Technology")
            self.assertEqual(um._sector_lookup("US", "AAL"), "Industrials")

    def test_lookup_accepts_plain_string_entry(self) -> None:
        with patch.object(um, "_SECTOR_MAP_PATH", self.path):
            self.assertEqual(um._sector_lookup("US", "PLAIN"), "Energy")

    def test_unknown_ticker_returns_empty_not_fabricated(self) -> None:
        """모르는 종목의 섹터를 위조하지 않는다(캡은 현행대로 통과)."""
        with patch.object(um, "_SECTOR_MAP_PATH", self.path):
            self.assertEqual(um._sector_lookup("US", "NOSUCH"), "")
            self.assertEqual(um._sector_lookup("KR", "005930"), "")

    def test_missing_cache_file_is_safe(self) -> None:
        """캐시가 없어도 예외 없이 빈 값 — 현행 동작으로 안전하게 후퇴한다."""
        with patch.object(um, "_SECTOR_MAP_PATH", Path(self._tmp.name) / "nope.json"):
            self.assertEqual(um._sector_lookup("US", "NVDA"), "")

    def test_corrupt_cache_is_safe(self) -> None:
        bad = Path(self._tmp.name) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with patch.object(um, "_SECTOR_MAP_PATH", bad):
            self.assertEqual(um._sector_lookup("US", "NVDA"), "")

    def test_build_universe_fills_sector_from_cache(self) -> None:
        """스크리너가 sector를 주지 않아도 후보에 섹터가 실린다."""
        candidates = [
            {"ticker": "NVDA", "name": "NVDA", "price": 100.0, "volume": 1_000_000, "change_rate": 5.0},
            {"ticker": "AAL", "name": "AAL", "price": 20.0, "volume": 2_000_000, "change_rate": 3.0},
        ]
        with patch.object(um, "_SECTOR_MAP_PATH", self.path):
            snap = um.build_universe_from_candidates("US", "2026-07-22", candidates)
        by_ticker = {c["ticker"]: c for c in snap.get("candidates", [])}
        self.assertEqual(by_ticker["NVDA"]["sector"], "Technology")
        self.assertEqual(by_ticker["AAL"]["sector"], "Industrials")

    def test_screener_supplied_sector_wins_over_cache(self) -> None:
        """스크리너가 값을 주면 그것을 우선한다(캐시는 보완용)."""
        candidates = [
            {"ticker": "NVDA", "name": "NVDA", "price": 100.0, "volume": 1_000_000,
             "change_rate": 5.0, "sector": "FromScreener"},
        ]
        with patch.object(um, "_SECTOR_MAP_PATH", self.path):
            snap = um.build_universe_from_candidates("US", "2026-07-22", candidates)
        by_ticker = {c["ticker"]: c for c in snap.get("candidates", [])}
        self.assertEqual(by_ticker["NVDA"]["sector"], "FromScreener")


if __name__ == "__main__":
    unittest.main()
