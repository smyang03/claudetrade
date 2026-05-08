from __future__ import annotations

import unittest

from runtime.pathb_reasons import (
    ORDER_UNKNOWN_HARD_TIMEOUT_SEC_DEFAULT,
    ORDER_UNKNOWN_MIN_RECONCILE_ATTEMPTS_DEFAULT,
    ORDER_UNKNOWN_SOFT_TIMEOUT_SEC_DEFAULT,
    choose_primary_pathb_close_reason,
    pathb_close_reason_priority,
)
from runtime.pathb_runtime import PathBRuntime


class PathBReasonPolicyTests(unittest.TestCase):
    def test_close_reason_priority_prefers_hard_stop_over_pre_close(self) -> None:
        self.assertLess(
            pathb_close_reason_priority("CLOSED_HARD_STOP"),
            pathb_close_reason_priority("CLOSED_CLAUDE_PRICE_PRE_CLOSE"),
        )
        self.assertEqual(
            choose_primary_pathb_close_reason(
                ["CLOSED_CLAUDE_PRICE_PRE_CLOSE", "CLOSED_HARD_STOP"]
            ),
            "CLOSED_HARD_STOP",
        )
        self.assertLess(
            pathb_close_reason_priority("CLOSED_TRAILING_STOP"),
            pathb_close_reason_priority("CLOSED_CLAUDE_PRICE_PRE_CLOSE"),
        )
        self.assertLess(
            pathb_close_reason_priority("CLOSED_CLAUDE_PRICE_TARGET"),
            pathb_close_reason_priority("CLOSED_UNKNOWN"),
        )

    def test_manual_close_reason_is_separate_primary(self) -> None:
        self.assertEqual(
            choose_primary_pathb_close_reason(["CLOSED_HARD_STOP", "CLOSED_USER_MANUAL"]),
            "CLOSED_USER_MANUAL",
        )

    def test_order_unknown_timeout_defaults_are_exposed(self) -> None:
        self.assertEqual(ORDER_UNKNOWN_SOFT_TIMEOUT_SEC_DEFAULT, 90)
        self.assertEqual(ORDER_UNKNOWN_HARD_TIMEOUT_SEC_DEFAULT, 300)
        self.assertEqual(ORDER_UNKNOWN_MIN_RECONCILE_ATTEMPTS_DEFAULT, 2)
        self.assertEqual(PathBRuntime.ORDER_UNKNOWN_SOFT_TIMEOUT_SEC, 90)
        self.assertEqual(PathBRuntime.ORDER_UNKNOWN_HARD_TIMEOUT_SEC, 300)
        self.assertEqual(PathBRuntime.ORDER_UNKNOWN_MIN_RECONCILE_ATTEMPTS, 2)


if __name__ == "__main__":
    unittest.main()
