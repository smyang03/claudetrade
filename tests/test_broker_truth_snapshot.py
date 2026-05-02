from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.broker_truth_snapshot import BrokerTruthSnapshot, load_broker_truth_snapshot


class BrokerTruthSnapshotTests(unittest.TestCase):
    def test_create_load_round_trip_and_masks_sensitive_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            snapshot = BrokerTruthSnapshot(
                runtime_mode="live",
                path=path,
                balance_provider=lambda market, force: {
                    "cash": 1000,
                    "orderable_cash": 900,
                    "total_eval": 1200,
                    "stocks": [{"ticker": "005930", "name": "Samsung", "qty": 1, "avg_price": 70000, "current_price": 71000}],
                    "account_no": "1234567890",
                },
                ccld_provider=lambda market, day: [
                    {"ticker": "005930", "side": "buy", "order_qty": 1, "filled_qty": 1, "remaining_qty": 0, "order_no": "ord1"}
                ],
            )
            data = snapshot.refresh_market("KR", force=True, ttl_sec=30)
            self.assertFalse(data["markets"]["KR"]["missing"])
            loaded = load_broker_truth_snapshot("live", path=path)
            self.assertEqual(loaded["markets"]["KR"]["positions"][0]["ticker"], "005930")
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotEqual(raw["markets"]["KR"]["account_summary"].get("account_no"), "1234567890")

    def test_missing_and_broken_snapshot_handling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = BrokerTruthSnapshot(runtime_mode="live", path=Path(tmp) / "missing.json").load_snapshot()
            self.assertTrue(missing["markets"]["KR"]["missing"])

            broken_path = Path(tmp) / "broken.json"
            broken_path.write_text("{", encoding="utf-8")
            broken = BrokerTruthSnapshot(runtime_mode="live", path=broken_path).load_snapshot()
            self.assertTrue(broken["broken"])

    def test_market_failure_preserves_other_market(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"

            def balance(market: str, force: bool) -> dict:
                if market == "US":
                    raise RuntimeError("US timeout")
                return {"cash": 1000, "stocks": [{"ticker": "005930", "qty": 1, "avg_price": 1, "current_price": 1}]}

            snapshot = BrokerTruthSnapshot(
                runtime_mode="live",
                path=path,
                balance_provider=balance,
                ccld_provider=lambda market, day: [],
            )
            data = snapshot.refresh_all(force=True, ttl_by_market={"KR": 30, "US": 30})
            self.assertFalse(data["markets"]["KR"]["missing"])
            self.assertTrue(data["markets"]["US"]["stale"])
            self.assertIn("US timeout", data["markets"]["US"]["error"])

    def test_ttl_and_force_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            calls = {"n": 0}

            def balance(market: str, force: bool) -> dict:
                calls["n"] += 1
                return {"cash": calls["n"], "stocks": []}

            snapshot = BrokerTruthSnapshot(
                runtime_mode="live",
                path=path,
                balance_provider=balance,
                ccld_provider=lambda market, day: [],
            )
            snapshot.refresh_market("KR", force=True, ttl_sec=60)
            snapshot.refresh_market("KR", force=False, ttl_sec=60)
            self.assertEqual(calls["n"], 1)
            snapshot.refresh_market("KR", force=True, ttl_sec=60)
            self.assertEqual(calls["n"], 2)

    def test_date_provider_controls_trade_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seen: list[str] = []
            snapshot = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "snapshot.json",
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: seen.append(day) or [],
                date_provider=lambda market: "2026-04-26" if market == "US" else "2026-04-27",
            )
            snapshot.refresh_market("US", force=True, ttl_sec=30)
            self.assertEqual(seen, ["20260426"])

    def test_token_provider_receives_market_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seen: list[str] = []

            def token_provider(market: str) -> str:
                seen.append(market)
                return f"token-{market}"

            snapshot = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "snapshot.json",
                token_provider=token_provider,
            )

            with patch("kis_api.get_balance", return_value={"cash": 0, "stocks": []}) as get_balance, patch(
                "kis_api.inquire_ccnl_us", return_value=[]
            ) as inquire_us:
                snapshot.refresh_market("US", force=True, ttl_sec=30)

        self.assertEqual(seen, ["US", "US"])
        get_balance.assert_called_once_with("token-US", market="US", force_refresh=True)
        inquire_us.assert_called_once()
        self.assertEqual(inquire_us.call_args.args[0], "token-US")

    def test_legacy_no_arg_token_provider_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "snapshot.json",
                token_provider=lambda: "legacy-token",
            )

            with patch("kis_api.get_balance", return_value={"cash": 0, "stocks": []}) as get_balance, patch(
                "kis_api.inquire_daily_ccld_kr", return_value=[]
            ):
                snapshot.refresh_market("KR", force=True, ttl_sec=30)

        get_balance.assert_called_once_with("legacy-token", market="KR", force_refresh=True)


if __name__ == "__main__":
    unittest.main()
