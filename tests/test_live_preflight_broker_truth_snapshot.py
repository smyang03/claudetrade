from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import live_preflight


def _utc_iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _market_payload(last_success_at: str, *, stale: bool = False) -> dict:
    return {
        "missing": False,
        "stale": stale,
        "ttl_sec": 60,
        "last_success_at": last_success_at,
        "last_attempt_at": last_success_at,
        "positions": [],
        "open_orders": [],
        "today_fills": [],
        "account_summary": {},
        "error": "",
    }


class LivePreflightBrokerTruthSnapshotTests(unittest.TestCase):
    def test_stale_state_recomputes_from_last_success_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "live_broker_truth_snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "runtime_mode": "live",
                        "schema_version": 1,
                        "markets": {
                            "KR": _market_payload(_utc_iso(timedelta(minutes=-5)), stale=False),
                            "US": _market_payload(_utc_iso(timedelta(minutes=1)), stale=False),
                        },
                    }
                ),
                encoding="utf-8",
            )

            checks = live_preflight._broker_truth_snapshot_file_checks("live", snapshot_path)

        by_name = {check.name: check for check in checks}
        kr_check = by_name["broker_truth.kr_stale_state"]
        us_check = by_name["broker_truth.us_stale_state"]

        self.assertEqual(kr_check.status, "WARN")
        self.assertEqual(kr_check.detail, "KR snapshot stale")
        self.assertFalse(kr_check.data["stored_stale"])
        self.assertTrue(kr_check.data["stale"])
        self.assertTrue(kr_check.data["stale_recomputed"])
        self.assertEqual(us_check.status, "PASS")


if __name__ == "__main__":
    unittest.main()
