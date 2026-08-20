# -*- coding: utf-8 -*-
"""MAX(복권형) 하한 계약 테스트 (2026-08-20).

227세션 백테스트: 밴드 위에 MAX>=8을 얹으면 34건이 걸러지는데 **합계가 +498 -> +508로
오히려 늘어난다**(걸러낸 34건이 합쳐서 순손실). 클러스터 t는 2.63 -> 4.93.
문헌: 복권 수요가 패자 가격을 더 왜곡 -> 더 큰 반전(MAX 효과).
"""
from __future__ import annotations

import codecs
import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from runtime import us_swing_order_bridge as bridge


class _Bot:
    def __init__(self, enabled=True, floor=8.0):
        self._enabled, self._floor = enabled, floor

    def _runtime_bool(self, key, default=False):
        return self._enabled if key == "US_SWING_MAX_FLOOR_ENABLED" else default

    def _runtime_float(self, key, default=0.0):
        return self._floor if key == "US_SWING_MAX_FLOOR_PCT" else default


SD = "2026-08-20"


def _write_csv(root: Path, ticker: str, closes: list[float], *, bom: bool = True) -> None:
    """픽스처는 기본으로 BOM을 단다 — 실제 data/price/us CSV가 전부 그렇다.

    2026-08-20 사고: 픽스처만 BOM 없이 써서 모든 테스트가 통과했는데
    라이브에서는 MAX 하한이 100% fail-open이었다(DictReader 첫 키가 '﻿date').
    픽스처가 프로덕션 파일과 다르면 테스트는 버그를 영원히 통과시킨다.
    """
    p = root / "data" / "price" / "us"
    p.mkdir(parents=True, exist_ok=True)
    enc = "utf-8-sig" if bom else "utf-8"
    with (p / f"us_{ticker}.csv").open("w", encoding=enc, newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for i, c in enumerate(closes):
            # signal_date 이전 날짜만 (no-lookahead 확인용)
            w.writerow([f"2026-07-{i+1:02d}", c, c, c, c, 1000])


class MaxFloorTests(unittest.TestCase):
    def _patched(self, tmp):
        def fake_path(*parts):
            return Path(tmp).joinpath(*parts)
        return mock.patch.object(bridge, "get_runtime_path", fake_path)

    def test_drops_low_max_keeps_high_max(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # LOW: 하루 최대 상승 ~2% / HIGH: 하루 최대 상승 ~20%
            _write_csv(root, "LOW", [100 * (1.02 ** i) for i in range(25)])
            _write_csv(root, "HIGH", [100] * 20 + [120, 121, 122, 123, 124])
            with self._patched(tmp):
                kept, meta = bridge._apply_max_lottery_floor(
                    _Bot(), SD, [{"ticker": "LOW", "rank": 1}, {"ticker": "HIGH", "rank": 2}]
                )
            self.assertTrue(meta["applied"])
            self.assertEqual([s["ticker"] for s in kept], ["HIGH"])
            self.assertIn("LOW", meta["dropped"])

    def test_no_candidate_above_floor_returns_empty(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_csv(root, "FLAT", [100 + i * 0.1 for i in range(25)])
            with self._patched(tmp):
                kept, meta = bridge._apply_max_lottery_floor(
                    _Bot(), SD, [{"ticker": "FLAT", "rank": 1}]
                )
            self.assertEqual(kept, [])
            self.assertTrue(meta["applied"])

    def test_fail_open_when_price_csv_missing(self):
        # 가격 CSV가 없으면 통과시킨다 — 관측 결손이 매매를 막으면 안 된다
        with TemporaryDirectory() as tmp:
            with self._patched(tmp):
                kept, meta = bridge._apply_max_lottery_floor(
                    _Bot(), SD, [{"ticker": "NOPE", "rank": 1}]
                )
            self.assertEqual([s["ticker"] for s in kept], ["NOPE"])
            self.assertIn("NOPE", meta["unknown"])

    def test_disabled_is_noop(self):
        with TemporaryDirectory() as tmp:
            with self._patched(tmp):
                sigs = [{"ticker": "ANY", "rank": 1}]
                kept, meta = bridge._apply_max_lottery_floor(_Bot(enabled=False), SD, sigs)
            self.assertEqual(kept, sigs)
            self.assertFalse(meta["applied"])

    def test_no_lookahead_uses_only_bars_before_session_date(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "data" / "price" / "us"
            p.mkdir(parents=True, exist_ok=True)
            with (p / "us_FUT.csv").open("w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["date", "open", "high", "low", "close", "volume"])
                for i in range(24):                       # 과거는 완만
                    w.writerow([f"2026-07-{i+1:02d}", 100, 100, 100, 100, 1000])
                w.writerow([SD, 200, 200, 200, 200, 1000])  # 당일 +100% (써서는 안 됨)
            with self._patched(tmp):
                value = bridge._max_daily_return_21d("FUT", SD)
            self.assertIsNotNone(value)
            self.assertLess(value, 1.0)   # 당일 급등이 반영되면 100%가 나온다

    def test_floor_is_inclusive(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_csv(root, "EXACT", [100] * 20 + [108.0, 108.1, 108.2, 108.3, 108.4])
            with self._patched(tmp):
                kept, _ = bridge._apply_max_lottery_floor(
                    _Bot(floor=8.0), SD, [{"ticker": "EXACT", "rank": 1}]
                )
            self.assertEqual([s["ticker"] for s in kept], ["EXACT"])

    def test_bom_csv_is_parsed(self):
        """BOM 회귀 — 라이브 CSV와 동일한 조건에서 MAX가 실제로 계산돼야 한다.

        2026-08-20: encoding="utf-8"로 열던 시절 이 값이 None이었고,
        그 결과 밴드 통과 후보 전원이 fail-open으로 흘렀다.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_csv(root, "BOMY", [100] * 20 + [120, 121, 122, 123, 124], bom=True)
            raw = (root / "data" / "price" / "us" / "us_BOMY.csv").read_bytes()
            self.assertTrue(raw.startswith(codecs.BOM_UTF8), "픽스처가 BOM을 달아야 의미가 있다")
            with self._patched(tmp):
                value = bridge._max_daily_return_21d("BOMY", SD)
            self.assertIsNotNone(value, "BOM CSV에서 MAX가 None이면 하한이 무력화된다")
            self.assertGreater(value, 8.0)

    def test_bomless_csv_still_parsed(self):
        """BOM 없는 CSV도 계속 읽혀야 한다(수집기가 바뀌어도 깨지지 않게)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_csv(root, "PLAIN", [100] * 20 + [120, 121, 122, 123, 124], bom=False)
            with self._patched(tmp):
                value = bridge._max_daily_return_21d("PLAIN", SD)
            self.assertIsNotNone(value)
            self.assertGreater(value, 8.0)


if __name__ == "__main__":
    unittest.main()
