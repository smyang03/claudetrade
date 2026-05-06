from __future__ import annotations

import unittest

from execution.order_failure import broker_reject_reason, is_permanent_order_failure
from runtime.pathb_runtime import PathBRuntime


class OrderFailureClassificationTests(unittest.TestCase):
    def test_buying_power_rejects_are_common_permanent_failures(self) -> None:
        samples = [
            "주문가능금액을 초과 했습니다",
            "매수가능금액 부족",
            "증거금 부족",
            "insufficient buying power",
            "insufficient funds",
        ]

        for detail in samples:
            with self.subTest(detail=detail):
                self.assertTrue(is_permanent_order_failure(detail))
                self.assertEqual(broker_reject_reason(detail), "permanent_order_reject")
                self.assertTrue(PathBRuntime._is_permanent_order_failure(detail))

    def test_transient_reject_keeps_default_reason(self) -> None:
        detail = "broker timeout after order request"

        self.assertFalse(is_permanent_order_failure(detail))
        self.assertEqual(broker_reject_reason(detail), "order_rejected")
        self.assertEqual(broker_reject_reason(detail, default="order_exception"), "order_exception")


if __name__ == "__main__":
    unittest.main()
