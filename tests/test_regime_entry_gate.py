from __future__ import annotations

import unittest

from bot.regime_entry_gate import (
    evaluate_regime_entry_gate,
    normalize_mode,
    parse_block_modes,
    DEFAULT_BLOCK_MODES,
)


class RegimeEntryGateTests(unittest.TestCase):
    def test_off_is_noop(self) -> None:
        v = evaluate_regime_entry_gate("CAUTIOUS", "off")
        self.assertEqual(v["decision"], "off")
        self.assertFalse(v["block"])

    def test_enforce_blocks_default_cautious(self) -> None:
        v = evaluate_regime_entry_gate("CAUTIOUS", "enforce")
        self.assertEqual(v["decision"], "skip")
        self.assertTrue(v["block"])

    def test_enforce_allows_good_regime(self) -> None:
        v = evaluate_regime_entry_gate("MODERATE_BULL", "enforce")
        self.assertEqual(v["decision"], "allow")
        self.assertFalse(v["block"])

    def test_shadow_would_skip_no_block(self) -> None:
        v = evaluate_regime_entry_gate("CAUTIOUS", "shadow")
        self.assertEqual(v["decision"], "would_skip")
        self.assertFalse(v["block"])

    def test_fail_open_unknown_regime(self) -> None:
        v = evaluate_regime_entry_gate("", "enforce")
        self.assertEqual(v["decision"], "allow_no_regime")
        self.assertFalse(v["block"])

    def test_custom_block_modes(self) -> None:
        # MILD_BEAR·CAUTIOUS 둘 다 차단
        v = evaluate_regime_entry_gate("MILD_BEAR", "enforce", block_modes="CAUTIOUS,MILD_BEAR")
        self.assertTrue(v["block"])
        # MILD_BULL은 목록에 없으면 통과
        v2 = evaluate_regime_entry_gate("MILD_BULL", "enforce", block_modes="CAUTIOUS,MILD_BEAR")
        self.assertFalse(v2["block"])

    def test_case_insensitive(self) -> None:
        v = evaluate_regime_entry_gate("cautious", "enforce")
        self.assertTrue(v["block"])

    def test_parse_block_modes_default(self) -> None:
        self.assertEqual(parse_block_modes(None), DEFAULT_BLOCK_MODES)
        self.assertEqual(parse_block_modes("MILD_BEAR, CAUTIOUS"), ("MILD_BEAR", "CAUTIOUS"))

    def test_normalize_mode(self) -> None:
        self.assertEqual(normalize_mode("ENFORCE"), "enforce")
        self.assertEqual(normalize_mode("bogus"), "off")


class MarketScopedBlockModesTests(unittest.TestCase):
    """차단 국면의 시장별 분리 — 최적 조합이 KR/US 정반대다.

    2026-07-22 실측(퍼센트 net, 커버리지 99%, 25개 조합 스캔):
      US  CAUTIOUS,MILD_BEAR  → -53.97% → +5.09% (거래 176건 유지)  ※MILD_BULL 빼야 함
      KR  CAUTIOUS,MILD_BULL  → -33.27% → +5.78%                    ※MILD_BEAR 빼야 함
    단일 설정(CAUTIOUS,MILD_BEAR,MILD_BULL)은 양 시장 모두 4위였다.
    """

    def _resolve(self, values: dict, market: str) -> str:
        """pathb_runtime의 조회 순서를 그대로 재현한다(시장별 키 우선, 없으면 공용)."""
        market_key = "US" if str(market or "").upper() == "US" else "KR"
        v = values.get(f"{market_key}_REGIME_ENTRY_GATE_BLOCK_MODES", "")
        if not str(v or "").strip():
            v = values.get("REGIME_ENTRY_GATE_BLOCK_MODES", "CAUTIOUS")
        return v

    def test_market_specific_key_wins(self) -> None:
        values = {
            "REGIME_ENTRY_GATE_BLOCK_MODES": "CAUTIOUS,MILD_BEAR,MILD_BULL",
            "US_REGIME_ENTRY_GATE_BLOCK_MODES": "CAUTIOUS,MILD_BEAR",
            "KR_REGIME_ENTRY_GATE_BLOCK_MODES": "CAUTIOUS,MILD_BULL",
        }
        self.assertEqual(self._resolve(values, "US"), "CAUTIOUS,MILD_BEAR")
        self.assertEqual(self._resolve(values, "KR"), "CAUTIOUS,MILD_BULL")

    def test_falls_back_to_shared_key(self) -> None:
        """시장별 키가 없으면 기존 공용 키로 후퇴한다(현행 동작 보존)."""
        values = {"REGIME_ENTRY_GATE_BLOCK_MODES": "CAUTIOUS,MILD_BEAR,MILD_BULL"}
        self.assertEqual(self._resolve(values, "US"), "CAUTIOUS,MILD_BEAR,MILD_BULL")
        self.assertEqual(self._resolve(values, "KR"), "CAUTIOUS,MILD_BEAR,MILD_BULL")

    def test_empty_market_key_falls_back(self) -> None:
        values = {"REGIME_ENTRY_GATE_BLOCK_MODES": "CAUTIOUS", "US_REGIME_ENTRY_GATE_BLOCK_MODES": "  "}
        self.assertEqual(self._resolve(values, "US"), "CAUTIOUS")

    def test_us_optimal_keeps_mild_bull(self) -> None:
        """US 최적 조합에서 MILD_BULL은 통과해야 한다(즉시매수 허용 국면과도 정합)."""
        v = evaluate_regime_entry_gate("MILD_BULL", "enforce", block_modes="CAUTIOUS,MILD_BEAR")
        self.assertFalse(v.get("block"))
        v2 = evaluate_regime_entry_gate("MILD_BEAR", "enforce", block_modes="CAUTIOUS,MILD_BEAR")
        self.assertTrue(v2.get("block"))

    def test_kr_optimal_keeps_mild_bear(self) -> None:
        v = evaluate_regime_entry_gate("MILD_BEAR", "enforce", block_modes="CAUTIOUS,MILD_BULL")
        self.assertFalse(v.get("block"))
        v2 = evaluate_regime_entry_gate("MILD_BULL", "enforce", block_modes="CAUTIOUS,MILD_BULL")
        self.assertTrue(v2.get("block"))


if __name__ == "__main__":
    unittest.main()
