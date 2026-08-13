from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.kr_fallen_shadow_scan as scan_mod


def _bars(prev_close: float, open_px: float, close_px: float, session: str = "2026-08-12"):
    """워밍업 25봉 + 마지막 봉(검증 대상). 거래대금은 필터(10억) 통과 수준."""
    bars = []
    for i in range(25):
        bars.append({
            "d": f"2026-07-{i + 1:02d}", "o": prev_close, "h": prev_close * 1.01,
            "l": prev_close * 0.99, "c": prev_close, "v": 100000, "amt": 5e9,
        })
    bars.append({
        "d": session, "o": open_px, "h": max(open_px, close_px) * 1.01,
        "l": min(open_px, close_px) * 0.99, "c": close_px, "v": 200000, "amt": 5e9,
    })
    return bars


class BlindspotScanTests(unittest.TestCase):
    def _run_scan(self, cache: dict) -> tuple[list, list]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_p = root / "cache.json"
            out_p = root / "out.jsonl"
            blind_p = root / "blind.jsonl"
            cache_p.write_text(json.dumps(cache), encoding="utf-8")
            with patch.object(scan_mod, "CACHE", cache_p), \
                 patch.object(scan_mod, "OUT", out_p), \
                 patch.object(scan_mod, "BLIND_OUT", blind_p), \
                 patch.object(scan_mod, "_instrument_type", lambda code: "stock"), \
                 patch.object(scan_mod, "_info_event_flags", lambda code, d: {}):
                scan_mod.scan("20260812")
            read = lambda p: [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
            return read(out_p), read(blind_p)

    def test_blind_candidate_goes_to_blind_ledger_only(self) -> None:
        # 워밍업 종가 14500(→MA20), 전일 종가 12000: gap=11400/12000-1=-5.0%,
        # chg=11800/12000-1=-1.67%(장중 회복형), disc=11800/14500-1=-18.6%
        bars = _bars(prev_close=14500.0, open_px=11400.0, close_px=11800.0)
        bars[-2]["c"] = 12000.0
        out, blind = self._run_scan({"000001": bars})
        self.assertEqual(out, [])
        self.assertEqual(len(blind), 1)
        row = blind[0]
        self.assertTrue(row["observe_only"])
        self.assertEqual(row["capture_path"], "blindspot_gap_disc")
        self.assertLessEqual(row["feats"]["gap"], -4.0)
        self.assertLessEqual(row["feats"]["ma20_disc"], -15.0)

    def test_drop_capture_still_goes_to_main_ledger(self) -> None:
        # 종가 낙폭 -8% → 본 원장(기존 경로) 그대로
        bars = _bars(prev_close=14500.0, open_px=11500.0, close_px=11040.0)
        bars[-2]["c"] = 12000.0
        out, blind = self._run_scan({"000002": bars})
        self.assertEqual(len(out), 1)
        self.assertEqual(blind, [])
        self.assertNotIn("observe_only", out[0])

    def test_shallow_discount_blind_candidate_is_dropped(self) -> None:
        # 갭 -5%지만 MA20 할인이 얕으면(-2%) 기록 안 함
        bars = _bars(prev_close=12000.0, open_px=11400.0, close_px=11800.0)
        out, blind = self._run_scan({"000003": bars})
        self.assertEqual(out, [])
        self.assertEqual(blind, [])

    def test_suspended_open_zero_is_excluded(self) -> None:
        bars = _bars(prev_close=14500.0, open_px=0.0, close_px=11800.0)
        bars[-2]["c"] = 12000.0
        out, blind = self._run_scan({"000004": bars})
        self.assertEqual(out, [])
        self.assertEqual(blind, [])


if __name__ == "__main__":
    unittest.main()
