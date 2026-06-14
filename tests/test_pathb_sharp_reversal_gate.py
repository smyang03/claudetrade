"""Phase 4: 국면 급반전 enforce 게이트 테스트.

장중 지수 급반전 active 시 PathB 신규 진입만 보류(청산/보유 무관). shadow면 차단 안 함.
"""

import os
import types
import unittest

from runtime.pathb_runtime import PathBRuntime


class SharpReversalGateTests(unittest.TestCase):
    def _rt(self, active):
        rt = PathBRuntime.__new__(PathBRuntime)
        rt.bot = types.SimpleNamespace(_market_sharp_reversal_active=active)
        return rt

    def test_enforce_blocks_when_active(self):
        os.environ["MARKET_SHARP_REVERSAL_GUARD_MODE"] = "enforce"
        rt = self._rt({"US": True, "KR": False})
        self.assertTrue(rt._market_sharp_reversal_block("US"))
        self.assertFalse(rt._market_sharp_reversal_block("KR"))

    def test_shadow_never_blocks(self):
        os.environ["MARKET_SHARP_REVERSAL_GUARD_MODE"] = "shadow"
        rt = self._rt({"US": True})
        self.assertFalse(rt._market_sharp_reversal_block("US"))

    def test_enforce_no_active_passes(self):
        os.environ["MARKET_SHARP_REVERSAL_GUARD_MODE"] = "enforce"
        rt = self._rt({})
        self.assertFalse(rt._market_sharp_reversal_block("US"))


if __name__ == "__main__":
    unittest.main()
