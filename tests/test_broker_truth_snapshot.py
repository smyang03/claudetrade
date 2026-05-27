from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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

    def test_collect_market_preserves_us_krw_account_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            snapshot = BrokerTruthSnapshot(
                runtime_mode="live",
                path=path,
                balance_provider=lambda market, force: {
                    "cash": 2259.71,
                    "orderable_cash": 780.39,
                    "asset_cash": 2259.71,
                    "total_eval": 1489.56,
                    "market_asset_krw": 3_419_331,
                    "asset_cash_krw": 1_179_781,
                    "total_eval_krw": 2_239_550,
                    "total_profit_krw": 38_211,
                    "kis_exchange_rate": 1503.5,
                    "stocks": [],
                    "currency": "USD",
                },
                ccld_provider=lambda market, day: [],
            )

            data = snapshot.refresh_market("US", force=True, ttl_sec=30)

            summary = data["markets"]["US"]["account_summary"]
            self.assertEqual(summary["orderable_cash"], 780.39)
            self.assertEqual(summary["market_asset_krw"], 3_419_331)
            self.assertEqual(summary["asset_cash_krw"], 1_179_781)
            self.assertEqual(summary["total_eval_krw"], 2_239_550)
            self.assertEqual(summary["kis_exchange_rate"], 1503.5)

    def test_broker_truth_snapshot_preserves_us_orderable_cash_source(self) -> None:
        cases = [
            (
                {"cash": 1_000.0, "orderable_cash": 0.0, "stocks": [], "currency": "USD"},
                "cash_fallback",
                False,
                1_000.0,
            ),
            (
                {"cash": 1_000.0, "orderable_cash": 780.0, "stocks": [], "currency": "USD"},
                "orderable_cash",
                True,
                780.0,
            ),
        ]
        for balance, expected_source, expected_nets, expected_orderable in cases:
            with self.subTest(expected_source=expected_source), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "snapshot.json"
                snapshot = BrokerTruthSnapshot(
                    runtime_mode="live",
                    path=path,
                    balance_provider=lambda market, force, payload=balance: dict(payload),
                    ccld_provider=lambda market, day: [],
                )

                data = snapshot.refresh_market("US", force=True, ttl_sec=30)

                summary = data["markets"]["US"]["account_summary"]
                self.assertEqual(summary["orderable_cash"], expected_orderable)
                self.assertEqual(summary["orderable_cash_source"], expected_source)
                self.assertIs(summary["orderable_cash_nets_open_orders"], expected_nets)

    def test_open_order_preserves_order_price_for_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            snapshot = BrokerTruthSnapshot(
                runtime_mode="live",
                path=path,
                balance_provider=lambda market, force: {"cash": 500_000, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "069540",
                        "side": "buy",
                        "order_no": "0012214400",
                        "order_qty": 33,
                        "filled_qty": 0,
                        "remaining_qty": 33,
                        "fill_price": 0,
                        "order_price": 6_780,
                    }
                ],
            )

            data = snapshot.refresh_market("KR", force=True, ttl_sec=30)

            order = data["markets"]["KR"]["open_orders"][0]
            self.assertEqual(order["order_price"], 6_780)
            self.assertEqual(order["avg_price"], 6_780)

    def test_us_open_order_live_fields_preserve_identity_in_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            snapshot = BrokerTruthSnapshot(
                runtime_mode="live",
                path=path,
                balance_provider=lambda market, force: {"cash": 500_000, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "odno": "0030262408",
                        "pdno": "IBM",
                        "sll_buy_dvsn": "01",
                        "ft_ord_qty": "3",
                        "ft_ccld_qty": "0",
                        "nccs_qty": "3",
                        "ft_ord_unpr3": "254.247",
                    }
                ],
            )

            data = snapshot.refresh_market("US", force=True, ttl_sec=30)

            market_data = data["markets"]["US"]
            order = market_data["open_orders"][0]
            self.assertEqual(order["ticker"], "IBM")
            self.assertEqual(order["side"], "sell")
            self.assertEqual(order["order_no"], "0030262408")
            self.assertEqual(order["remaining_qty"], 3)
            self.assertEqual(order["order_qty"], 3)
            self.assertEqual(order["order_price"], 254.247)
            self.assertEqual(order["avg_price"], 254.247)
            self.assertEqual(market_data["open_order_integrity_issues"], [])

    def test_open_order_integrity_issues_flag_zero_qty_or_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            snapshot = BrokerTruthSnapshot(
                runtime_mode="live",
                path=path,
                balance_provider=lambda market, force: {"cash": 500_000, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "AAPL",
                        "side": "buy",
                        "order_no": "bad-us-open",
                        "order_qty": 0,
                        "filled_qty": 0,
                        "remaining_qty": 2,
                        "order_price": 0,
                    }
                ],
            )

            data = snapshot.refresh_market("US", force=True, ttl_sec=30)

            market_data = data["markets"]["US"]
            order = market_data["open_orders"][0]
            self.assertEqual(order["integrity_flags"], ["order_qty_zero", "order_price_zero"])
            self.assertEqual(len(market_data["open_order_integrity_issues"]), 1)
            issue = market_data["open_order_integrity_issues"][0]
            self.assertEqual(issue["ticker"], "AAPL")
            self.assertEqual(issue["order_no"], "bad-us-open")
            self.assertEqual(issue["remaining_qty"], 2)
            self.assertEqual(issue["flags"], ["order_qty_zero", "order_price_zero"])

    def test_rejected_order_is_not_open_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            snapshot = BrokerTruthSnapshot(
                runtime_mode="live",
                path=path,
                balance_provider=lambda market, force: {"cash": 500_000, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "069540",
                        "side": "buy",
                        "order_no": "0012214400",
                        "order_qty": 33,
                        "filled_qty": 0,
                        "remaining_qty": 0,
                        "rejected_qty": 33,
                        "fill_price": 0,
                        "order_price": 6_780,
                        "order_status": "rejected",
                    }
                ],
            )

            data = snapshot.refresh_market("KR", force=True, ttl_sec=30)

            self.assertEqual(data["markets"]["KR"]["open_orders"], [])

    def test_write_snapshot_preserves_newer_other_market_on_parallel_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            snapshot = BrokerTruthSnapshot(runtime_mode="live", path=path)
            snapshot.write_snapshot(
                {
                    "generated_at": "2026-05-26T12:46:33+00:00",
                    "runtime_mode": "live",
                    "schema_version": 1,
                    "markets": {
                        "KR": {
                            "missing": False,
                            "stale": False,
                            "last_success_at": "2026-05-26T12:46:33+00:00",
                            "open_orders": [{"ticker": "069540", "order_price": 6780.0}],
                        },
                        "US": {"missing": True, "stale": True, "last_success_at": ""},
                    },
                }
            )

            snapshot.write_snapshot(
                {
                    "generated_at": "2026-05-26T12:46:44+00:00",
                    "runtime_mode": "live",
                    "schema_version": 1,
                    "markets": {
                        "KR": {
                            "missing": False,
                            "stale": True,
                            "last_success_at": "2026-05-26T12:23:39+00:00",
                            "open_orders": [],
                        },
                        "US": {
                            "missing": False,
                            "stale": False,
                            "last_success_at": "2026-05-26T12:46:44+00:00",
                        },
                    },
                }
            )

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["markets"]["KR"]["last_success_at"], "2026-05-26T12:46:33+00:00")
            self.assertFalse(data["markets"]["KR"]["stale"])
            self.assertEqual(data["markets"]["KR"]["open_orders"][0]["order_price"], 6780.0)
            self.assertEqual(data["markets"]["US"]["last_success_at"], "2026-05-26T12:46:44+00:00")

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
                    raise RuntimeError(
                        "HTTPSConnectionPool(host='openapi.koreainvestment.com', port=9443): "
                        "Max retries exceeded with url: /uapi/overseas-stock/v1/trading/inquire-balance?"
                        "CANO=12345678&ACNT_PRDT_CD=01&OVRS_EXCG_CD=NASD"
                    )
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
            self.assertIn("CANO=12345678", data["markets"]["US"]["error"])

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("CANO=***", raw["markets"]["US"]["error"])
            self.assertIn("ACNT_PRDT_CD=***", raw["markets"]["US"]["error"])
            self.assertNotIn("CANO=12345678", raw["markets"]["US"]["error"])

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

    def test_load_snapshot_ttl_override_recomputes_staleness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            last_success = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat(timespec="seconds")
            path.write_text(
                json.dumps(
                    {
                        "runtime_mode": "live",
                        "schema_version": 1,
                        "markets": {
                            "US": {
                                "missing": False,
                                "last_success_at": last_success,
                                "last_attempt_at": last_success,
                                "ttl_sec": 30,
                                "error": "",
                                "positions": [{"ticker": "SOFI", "qty": 12}],
                                "open_orders": [],
                                "today_fills": [],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            loaded = BrokerTruthSnapshot(runtime_mode="live", path=path).load_snapshot(ttl_by_market={"US": 300})

            self.assertEqual(loaded["markets"]["US"]["ttl_sec"], 300)
            self.assertFalse(loaded["markets"]["US"]["stale"])
            self.assertEqual(loaded["markets"]["US"]["positions"][0]["ticker"], "SOFI")

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

    def test_refresh_filters_orders_outside_query_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "snapshot.json",
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "AMD",
                        "side": "buy",
                        "order_qty": 1,
                        "filled_qty": 1,
                        "remaining_qty": 0,
                        "order_no": "old",
                        "order_date": "20260427",
                    },
                    {
                        "ticker": "D",
                        "side": "buy",
                        "order_qty": 2,
                        "filled_qty": 2,
                        "remaining_qty": 0,
                        "order_no": "current",
                        "order_date": "20260518",
                    },
                    {
                        "ticker": "SOFI",
                        "side": "sell",
                        "order_qty": 12,
                        "filled_qty": 12,
                        "remaining_qty": 0,
                        "order_no": "no-date",
                    },
                ],
                date_provider=lambda market: "20260518",
            )

            data = snapshot.refresh_market("US", force=True, ttl_sec=30)

            fills = data["markets"]["US"]["today_fills"]
            self.assertEqual([row["order_no"] for row in fills], ["current", "no-date"])
            self.assertEqual(fills[0]["order_date"], "20260518")

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
