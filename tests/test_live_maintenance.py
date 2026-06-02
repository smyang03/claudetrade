from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lifecycle.event_store import EventStore
from tools import live_maintenance


def _runtime_path(root: Path):
    def fake(*parts: str, make_parents: bool = True) -> Path:
        path = root.joinpath(*parts)
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return fake


def _broker_truth(
    *,
    positions: list[dict] | None = None,
    open_orders: list[dict] | None = None,
    today_fills: list[dict] | None = None,
    missing: bool = False,
    stale: bool = False,
) -> dict:
    return {
        "missing": missing,
        "stale": stale,
        "last_success_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "positions": positions or [],
        "open_orders": open_orders or [],
        "today_fills": today_fills or [],
    }


def _create_run(store: EventStore, *, path_run_id: str, ticker: str, status: str = "ORDER_ACKED") -> None:
    store.create_path_run(
        path_run_id=path_run_id,
        decision_id=f"dec_{ticker}",
        path_type="claude_price",
        market="US",
        runtime_mode="live",
        session_date="2026-05-15",
        ticker=ticker,
        status=status,
        plan={"order_no": f"buy_{ticker}"},
    )


class LiveMaintenanceTests(unittest.TestCase):
    def test_process_discovery_and_writer_freeze(self) -> None:
        processes = live_maintenance.discover_live_processes(
            [
                {"pid": 1, "name": "python", "cmdline": ["python", "trading_bot.py", "--live"]},
                {
                    "pid": 2,
                    "name": "python",
                    "cmdline": ["python", "tools/live_guardian.py", "--mode", "live", "--watch"],
                },
                {"pid": 3, "name": "python", "cmdline": ["python", "dashboard/dashboard_server.py"]},
            ]
        )

        self.assertEqual({item["kind"] for item in processes}, {"live_bot", "guardian", "dashboard"})
        with self.assertRaises(RuntimeError):
            live_maintenance.assert_writer_freeze(processes)
        live_maintenance.assert_writer_freeze([item for item in processes if item["kind"] == "dashboard"])

    def test_process_discovery_accepts_equals_mode_live(self) -> None:
        processes = live_maintenance.discover_live_processes(
            [
                {
                    "pid": 10,
                    "name": "python",
                    "cmdline": ["python", "tools/live_guardian.py", "--watch", "--mode=live"],
                },
                {
                    "pid": 11,
                    "name": "python",
                    "cmdline": ["python", "tools/preopen_scheduler.py", "--mode=live"],
                },
            ]
        )

        self.assertEqual({item["pid"]: item["kind"] for item in processes}, {10: "guardian", 11: "preopen_scheduler"})
        with self.assertRaises(RuntimeError):
            live_maintenance.assert_writer_freeze(processes)

    def test_process_discovery_treats_omitted_mode_as_live_default(self) -> None:
        processes = live_maintenance.discover_live_processes(
            [
                {
                    "pid": 12,
                    "name": "python",
                    "cmdline": ["python", "tools/live_guardian.py", "--watch"],
                },
                {
                    "pid": 13,
                    "name": "python",
                    "cmdline": ["python", "tools/preopen_scheduler.py", "--loop"],
                },
            ]
        )

        self.assertEqual({item["pid"]: item["kind"] for item in processes}, {12: "guardian", 13: "preopen_scheduler"})
        with self.assertRaises(RuntimeError):
            live_maintenance.assert_writer_freeze(processes)

    def test_process_discovery_excludes_explicit_paper_sidecars(self) -> None:
        processes = live_maintenance.discover_live_processes(
            [
                {
                    "pid": 14,
                    "name": "python",
                    "cmdline": ["python", "tools/live_guardian.py", "--watch", "--mode", "paper"],
                },
                {
                    "pid": 15,
                    "name": "python",
                    "cmdline": ["python", "tools/preopen_scheduler.py", "--mode=paper", "--loop"],
                },
            ]
        )

        self.assertEqual(processes, [])

    def test_parser_uses_required_subcommands(self) -> None:
        args = live_maintenance.build_parser().parse_args(
            ["reconcile-position", "--mode", "live", "--market", "US", "--ticker", "MSFT", "--dry-run"]
        )

        self.assertEqual(args.command, "reconcile-position")
        self.assertEqual(args.ticker, "MSFT")

    def test_create_live_backup_copies_db_state_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = EventStore(root / "data" / "v2_event_store.db")
            _create_run(store, path_run_id="path_msft", ticker="MSFT")
            state_dir = root / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "live_open_positions.json").write_text("[]", encoding="utf-8")
            (state_dir / "live_pending_orders.json").write_text("[]", encoding="utf-8")

            with patch("tools.live_maintenance.get_runtime_path", side_effect=_runtime_path(root)):
                backup_dir = live_maintenance.create_live_backup(
                    "before_reconcile",
                    backup_root=root / "backups",
                )

            manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
            backed_roles = {item["role"] for item in manifest["files"]}
            self.assertIn("sqlite_backup", backed_roles)
            self.assertTrue((backup_dir / "v2_event_store.db").exists())
            self.assertTrue((backup_dir / "live_open_positions.json").exists())

    def test_create_live_backup_tolerates_locked_sqlite_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = EventStore(root / "data" / "v2_event_store.db")
            _create_run(store, path_run_id="path_msft", ticker="MSFT")
            original_backup = live_maintenance._backup_sqlite_db
            original_copy2 = live_maintenance.shutil.copy2

            def backup_and_create_sidecar(source, target):
                original_backup(source, target)
                Path(str(source) + "-shm").write_text("locked", encoding="utf-8")

            def copy2(source, target, *args, **kwargs):
                if str(source).endswith(".db-shm"):
                    raise OSError(22, "Invalid argument", str(target))
                return original_copy2(source, target, *args, **kwargs)

            with patch("tools.live_maintenance.get_runtime_path", side_effect=_runtime_path(root)), patch.object(
                live_maintenance,
                "_backup_sqlite_db",
                side_effect=backup_and_create_sidecar,
            ), patch.object(
                live_maintenance.shutil,
                "copy2",
                side_effect=copy2,
            ):
                backup_dir = live_maintenance.create_live_backup(
                    "before_reconcile",
                    backup_root=root / "backups",
                )

            manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue((backup_dir / "v2_event_store.db").exists())
            self.assertEqual(manifest["optional_errors"][0]["role"], "sqlite-shm")
            self.assertFalse(manifest["optional_errors"][0]["copied"])
            self.assertNotIn("sqlite-shm", {item["role"] for item in manifest["files"]})

    def test_broker_truth_report_reads_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broker_truth.json"
            env_root = Path(tmp) / "repo"
            env_root.mkdir()
            (env_root / ".env.live").write_text(
                "\n".join(
                    [
                        "KIS_ACCOUNT_NO=12345678",
                        "KIS_ACCOUNT_NO_US=87654321",
                        "KIS_APP_KEY=kr_key",
                        "KIS_APP_SECRET=kr_secret",
                        "KIS_US_CREDENTIAL_FALLBACK_ACCEPTED=false",
                    ]
                ),
                encoding="utf-8",
            )
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            path.write_text(
                json.dumps(
                    {
                        "runtime_mode": "live",
                        "schema_version": 1,
                        "markets": {
                            "US": {
                                "missing": False,
                                "stale": False,
                                "last_success_at": now,
                                "ttl_sec": 3600,
                                "positions": [{"ticker": "MSFT", "qty": 1}],
                                "open_orders": [],
                                "today_fills": [],
                                "account_summary": {"cash": 1},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(live_maintenance, "ROOT", env_root), patch.dict(os.environ, {}, clear=True):
                report = live_maintenance.broker_truth_report(mode="live", market="US", snapshot_path=path)

            self.assertTrue(report["ok"])
            self.assertEqual(report["positions"][0]["ticker"], "MSFT")
            self.assertEqual(report["credential_policy"]["credential_mode"], "fallback_shared_kr")
            self.assertFalse(report["credential_policy"]["fallback_accepted_by_policy"])

    def test_msft_absent_broker_apply_removes_local_and_cancels_path_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path_run_id = "path_20260515_US_MSFT_claude_price_c4bebf0f"
            store = EventStore(root / "events.db")
            _create_run(store, path_run_id=path_run_id, ticker="MSFT", status="ORDER_ACKED")
            positions_path = root / "live_open_positions.json"
            positions_path.write_text(
                json.dumps(
                    [
                        {
                            "ticker": "MSFT",
                            "market": "US",
                            "qty": 1,
                            "pending_next_open_sell": True,
                            "pathb_path_run_id": path_run_id,
                        },
                        {"ticker": "005930", "market": "KR", "qty": 1},
                    ]
                ),
                encoding="utf-8",
            )

            dry = live_maintenance.reconcile_local_position_against_broker(
                market="US",
                ticker="MSFT",
                path_run_id=path_run_id,
                broker_truth=_broker_truth(),
                store=store,
                positions_path=positions_path,
            )
            self.assertEqual(dry["action"], "remove_local")
            self.assertEqual(dry["next_status"], "CANCELLED")
            self.assertEqual(json.loads(positions_path.read_text(encoding="utf-8"))[0]["ticker"], "MSFT")

            applied = live_maintenance.reconcile_local_position_against_broker(
                market="US",
                ticker="MSFT",
                path_run_id=path_run_id,
                broker_truth=_broker_truth(),
                store=store,
                positions_path=positions_path,
                dry_run=False,
                backup_dir=root / "backup",
                operator="test",
            )

            saved = json.loads(positions_path.read_text(encoding="utf-8"))
            run = store.find_path_run(path_run_id)
            events = store.events_for_session(market="US", runtime_mode="live", session_date="2026-05-15")
            self.assertTrue(applied["applied"])
            self.assertEqual([item["ticker"] for item in saved], ["005930"])
            self.assertEqual(run["status"], "CANCELLED")
            self.assertEqual(events[-1]["reason_code"], "BROKER_POSITION_ABSENT_RECONCILED")

    def test_single_reconcile_uses_default_event_store_when_path_run_id_is_given(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path_run_id = "path_msft"
            store = EventStore(root / "data" / "v2_event_store.db")
            _create_run(store, path_run_id=path_run_id, ticker="MSFT", status="ORDER_ACKED")
            positions_path = root / "state" / "live_open_positions.json"
            positions_path.parent.mkdir(parents=True, exist_ok=True)
            positions_path.write_text(
                json.dumps([{"ticker": "MSFT", "market": "US", "qty": 1, "pathb_path_run_id": path_run_id}]),
                encoding="utf-8",
            )

            with patch("lifecycle.event_store.get_runtime_path", side_effect=_runtime_path(root)):
                result = live_maintenance.reconcile_local_position_against_broker(
                    market="US",
                    ticker="MSFT",
                    path_run_id=path_run_id,
                    broker_truth=_broker_truth(),
                    positions_path=positions_path,
                )

            self.assertEqual(result["status_before"], "ORDER_ACKED")
            self.assertEqual(result["changes"][1]["status_before"], "ORDER_ACKED")

    def test_pending_sell_without_broker_fill_is_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path_run_id = "path_20260515_US_SOFI_claude_price_14c464ed"
            store = EventStore(root / "events.db")
            _create_run(store, path_run_id=path_run_id, ticker="SOFI", status="ORDER_UNKNOWN")
            positions_path = root / "live_open_positions.json"
            positions_path.write_text(
                json.dumps(
                    [
                        {
                            "ticker": "SOFI",
                            "market": "US",
                            "qty": 12,
                            "pathb_path_run_id": path_run_id,
                            "pathb_pending_sell_order_no": "0032123235",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            result = live_maintenance.reconcile_local_position_against_broker(
                market="US",
                ticker="SOFI",
                path_run_id=path_run_id,
                broker_truth=_broker_truth(),
                store=store,
                positions_path=positions_path,
            )

            self.assertEqual(result["action"], "manual_review")
            self.assertEqual(result["reason_code"], "BROKER_POSITION_ABSENT_SELL_FILL_UNCONFIRMED")
            self.assertEqual(result["positions_after_count"], 1)

    def test_absent_filled_pathb_run_closes_as_audited_learning_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path_run_id = "path_el"
            store = EventStore(root / "events.db")
            _create_run(store, path_run_id=path_run_id, ticker="EL", status="FILLED")
            positions_path = root / "live_open_positions.json"
            positions_path.write_text("[]", encoding="utf-8")

            dry = live_maintenance.reconcile_local_position_against_broker(
                market="US",
                ticker="EL",
                path_run_id=path_run_id,
                broker_truth=_broker_truth(),
                store=store,
                positions_path=positions_path,
            )
            self.assertEqual(dry["action"], "remove_local")
            self.assertEqual(dry["next_status"], "CLOSED")
            self.assertEqual(dry["reason_code"], live_maintenance.ABSENT_FILLED_CLOSE_REASON)

            applied = live_maintenance.reconcile_local_position_against_broker(
                market="US",
                ticker="EL",
                path_run_id=path_run_id,
                broker_truth=_broker_truth(),
                store=store,
                positions_path=positions_path,
                dry_run=False,
                backup_dir=root / "backup",
                operator="test",
            )

            run = store.find_path_run(path_run_id)
            events = store.events_for_session(market="US", runtime_mode="live", session_date="2026-05-15")
            self.assertTrue(applied["applied"])
            self.assertEqual(run["status"], "CLOSED")
            self.assertEqual(run["plan"]["close_reason"], live_maintenance.ABSENT_FILLED_CLOSE_REASON)
            self.assertTrue(run["plan"]["learning_excluded"])
            self.assertFalse(run["plan"]["exit_fill_confirmed"])
            self.assertIsNone(run["plan"]["pnl_pct"])
            self.assertEqual(events[-1]["event_type"], "CLOSED")
            self.assertEqual(events[-1]["reason_code"], live_maintenance.ABSENT_FILLED_CLOSE_REASON)
            self.assertTrue(events[-1]["payload"]["learning_excluded"])

    def test_sell_fill_evidence_closes_path_run_and_removes_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path_run_id = "path_sofi"
            store = EventStore(root / "events.db")
            _create_run(store, path_run_id=path_run_id, ticker="SOFI", status="SELL_ACKED")
            positions_path = root / "live_open_positions.json"
            positions_path.write_text(
                json.dumps(
                    [
                        {
                            "ticker": "SOFI",
                            "market": "US",
                            "qty": 12,
                            "pathb_path_run_id": path_run_id,
                            "pathb_pending_sell_order_no": "sell1",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            result = live_maintenance.reconcile_local_position_against_broker(
                market="US",
                ticker="SOFI",
                path_run_id=path_run_id,
                broker_truth=_broker_truth(
                    today_fills=[
                        {
                            "ticker": "SOFI",
                            "side": "sell",
                            "order_no": "sell1",
                            "filled_qty": 12,
                            "remaining_qty": 0,
                            "avg_price": 7.1,
                        }
                    ]
                ),
                store=store,
                positions_path=positions_path,
                dry_run=False,
                backup_dir=root / "backup",
            )

            saved = json.loads(positions_path.read_text(encoding="utf-8"))
            run = store.find_path_run(path_run_id)
            self.assertEqual(result["action"], "remove_local")
            self.assertEqual(result["next_status"], "CLOSED")
            self.assertEqual(saved, [])
            self.assertEqual(run["status"], "CLOSED")
            self.assertTrue(run["plan"]["exit_fill_confirmed"])

    def test_broad_reconcile_preserves_unconfirmed_pending_sell_and_removes_absent_plain_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = EventStore(root / "events.db")
            _create_run(store, path_run_id="path_sofi", ticker="SOFI", status="ORDER_UNKNOWN")
            _create_run(store, path_run_id="path_msft", ticker="MSFT", status="ORDER_ACKED")
            positions_path = root / "live_open_positions.json"
            positions_path.write_text(
                json.dumps(
                    [
                        {
                            "ticker": "SOFI",
                            "market": "US",
                            "qty": 12,
                            "pathb_path_run_id": "path_sofi",
                            "pathb_pending_sell_order_no": "sell1",
                        },
                        {"ticker": "MSFT", "market": "US", "qty": 1, "pathb_path_run_id": "path_msft"},
                        {"ticker": "005930", "market": "KR", "qty": 1},
                    ]
                ),
                encoding="utf-8",
            )

            result = live_maintenance.reconcile_positions_against_broker(
                market="US",
                broker_truth=_broker_truth(),
                store_path=root / "events.db",
                positions_path=positions_path,
            )

            actions = {item["ticker"]: item["action"] for item in result["results"]}
            self.assertEqual(actions["SOFI"], "manual_review")
            self.assertEqual(actions["MSFT"], "remove_local")
            self.assertEqual(result["removed_count"], 1)

    def test_stale_broker_truth_returns_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = EventStore(root / "events.db")
            _create_run(store, path_run_id="path_msft", ticker="MSFT", status="ORDER_ACKED")
            positions_path = root / "live_open_positions.json"
            positions_path.write_text(
                json.dumps([{"ticker": "MSFT", "market": "US", "qty": 1, "pathb_path_run_id": "path_msft"}]),
                encoding="utf-8",
            )

            result = live_maintenance.reconcile_local_position_against_broker(
                market="US",
                ticker="MSFT",
                path_run_id="path_msft",
                broker_truth=_broker_truth(stale=True),
                store=store,
                positions_path=positions_path,
            )

            self.assertEqual(result["action"], "manual_review")
            self.assertEqual(result["reason_code"], "BROKER_TRUTH_MISSING_OR_STALE")


if __name__ == "__main__":
    unittest.main()
