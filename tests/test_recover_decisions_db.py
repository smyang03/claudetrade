from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import recover_decisions_db


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "ml" / "schema.sql"


def _create_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def _insert_row(
    path: Path,
    *,
    row_id: int,
    ticker: str,
    session_date: str,
    market: str = "KR",
    decision: str = "NO_SIGNAL",
    filled: int = 0,
    forward_1d: float | None = None,
    forward_3d: float | None = None,
    forward_5d: float | None = None,
    data_source: str = "live",
    is_simulated: int = 0,
) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            INSERT INTO decisions (
                id, ts, market, ticker, session_date, mode, decision,
                filled, forward_1d, forward_3d, forward_5d, data_source, is_simulated
            ) VALUES (
                ?, '2026-05-13T09:00:00', ?, ?, ?, 'NEUTRAL', ?,
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                row_id,
                market,
                ticker,
                session_date,
                decision,
                filled,
                forward_1d,
                forward_3d,
                forward_5d,
                data_source,
                is_simulated,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _decision_rows(path: Path):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            """
            SELECT id, market, ticker, session_date, decision, filled, hold_days, pnl_pct
            FROM decisions
            ORDER BY id
            """
        ).fetchall()
    finally:
        conn.close()


def _write_live_jsonl(root: Path, rows: list[dict]) -> Path:
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / "live_decisions.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return path


class RecoverDecisionsDbTests(unittest.TestCase):
    def test_apply_to_output_path_merges_only_verified_rows_and_keeps_prod_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "recovered.db"
            _create_schema(backup)
            _create_schema(current)

            _insert_row(backup, row_id=26, ticker="BACK", session_date="2026-04-03")
            _insert_row(current, row_id=86370, ticker="005930", session_date="2026-05-12", forward_1d=1.8, forward_3d=3.2, forward_5d=5.1)
            _insert_row(current, row_id=86371, ticker="LIVE", session_date="2026-05-12")
            _insert_row(current, row_id=86372, ticker="SIM", session_date="2026-05-12", is_simulated=1)

            report = recover_decisions_db.recover(
                backup_path=backup,
                current_path=current,
                output_path=output,
                apply=True,
                skip_forward_update=True,
            )

            self.assertTrue(output.exists())
            self.assertEqual(report["apply_mode"], "output_path")
            self.assertEqual(report["backup_rows_copied"], 1)
            self.assertEqual(report["current_rows_merged"], 1)
            self.assertEqual(report["fixture_rows_removed"], 0)

            conn = sqlite3.connect(str(output))
            try:
                rows = conn.execute(
                    "SELECT id, ticker, session_date FROM decisions ORDER BY id"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(rows, [(26, "BACK", "2026-04-03"), (86371, "LIVE", "2026-05-12")])

    def test_verified_recovery_source_counts_as_live_for_diag_and_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "recovered.db"
            _create_schema(backup)
            _create_schema(current)

            _insert_row(backup, row_id=26, ticker="BACK", session_date="2026-04-03")
            _insert_row(
                current,
                row_id=86371,
                ticker="RECOVERY",
                session_date="2026-05-12",
                data_source="live_verified_recovery",
            )

            report = recover_decisions_db.recover(
                backup_path=backup,
                current_path=current,
                output_path=output,
                apply=True,
                skip_forward_update=True,
            )

            self.assertEqual(report["current_diag"]["live_rows"], 1)
            self.assertEqual(report["current_rows_merged"], 1)
            rows = _decision_rows(output)
            self.assertEqual([(row[2], row[3]) for row in rows], [("BACK", "2026-04-03"), ("RECOVERY", "2026-05-12")])

    def test_dry_run_does_not_create_output_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "dry_recovered.db"
            _create_schema(backup)
            _create_schema(current)
            _insert_row(backup, row_id=26, ticker="BACK", session_date="2026-04-03")

            report = recover_decisions_db.recover(
                backup_path=backup,
                current_path=current,
                output_path=output,
                apply=False,
                skip_forward_update=True,
            )

            self.assertFalse(output.exists())
            self.assertEqual(report["apply_mode"], "dry_run")
            self.assertEqual(report["backup_rows_copied"], 1)

    def test_apply_without_output_path_replaces_current_and_preserves_contaminated_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            _create_schema(backup)
            _create_schema(current)

            _insert_row(backup, row_id=26, ticker="BACK", session_date="2026-04-03")
            _insert_row(current, row_id=86370, ticker="005930", session_date="2026-05-12", forward_1d=1.8, forward_3d=3.2, forward_5d=5.1)
            _insert_row(current, row_id=86371, ticker="LIVE", session_date="2026-05-12")

            report = recover_decisions_db.recover(
                backup_path=backup,
                current_path=current,
                apply=True,
                skip_forward_update=True,
            )

            self.assertEqual(report["apply_mode"], "production_replace")
            self.assertEqual(report["output_path"], str(current.resolve()))
            self.assertTrue(report["preserved_current_files"])
            self.assertTrue(Path(report["preserved_current_files"][0]).exists())

            conn = sqlite3.connect(str(current))
            try:
                rows = conn.execute(
                    "SELECT id, ticker, session_date FROM decisions ORDER BY id"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(rows, [(26, "BACK", "2026-04-03"), (86371, "LIVE", "2026-05-12")])

    def test_id_conflict_post_backup_live_row_is_remapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "recovered.db"
            _create_schema(backup)
            _create_schema(current)
            _insert_row(backup, row_id=1, ticker="BACK", session_date="2026-04-03")
            _insert_row(current, row_id=1, ticker="LIVE", session_date="2026-05-12")

            report = recover_decisions_db.recover(
                backup_path=backup,
                current_path=current,
                output_path=output,
                apply=True,
                skip_forward_update=True,
            )

            rows = _decision_rows(output)
            self.assertEqual([(row[2], row[3]) for row in rows], [("BACK", "2026-04-03"), ("LIVE", "2026-05-12")])
            self.assertNotEqual(rows[1][0], 1)
            self.assertEqual(report["id_conflicts"], 1)
            self.assertEqual(report["id_remapped"], 1)
            self.assertEqual(report["id_remap_log"][0]["old_id"], 1)
            self.assertEqual(report["id_remap_log"][0]["new_id"], rows[1][0])

    def test_filled_default_zero_is_overwritten_by_supplement(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "recovered.db"
            _create_schema(backup)
            _create_schema(current)
            _insert_row(backup, row_id=1, ticker="LIVE", session_date="2026-05-12", decision="BUY_SIGNAL", filled=0)
            _write_live_jsonl(
                temp,
                [
                    {
                        "event": "closed",
                        "market": "KR",
                        "ticker": "LIVE",
                        "session_date": "2026-05-12",
                        "pnl_pct": 1.25,
                    }
                ],
            )

            with patch.object(recover_decisions_db, "ROOT", temp):
                report = recover_decisions_db.recover(
                    backup_path=backup,
                    current_path=current,
                    output_path=output,
                    apply=True,
                    skip_forward_update=True,
                )

            rows = _decision_rows(output)
            self.assertEqual(rows[0][5], 1)
            self.assertEqual(rows[0][7], 1.25)
            self.assertEqual(report["filled_updated"], 1)

    def test_closed_jsonl_infers_held_days_and_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "recovered.db"
            _create_schema(backup)
            _create_schema(current)
            _insert_row(backup, row_id=1, ticker="LIVE", session_date="2026-05-12", decision="BUY_SIGNAL", filled=0)
            _write_live_jsonl(
                temp,
                [
                    {
                        "type": "closed",
                        "market": "KR",
                        "ticker": "LIVE",
                        "session_date": "2026-05-12",
                        "held_days": 3,
                        "pnl_pct": 2.5,
                    }
                ],
            )

            with patch.object(recover_decisions_db, "ROOT", temp):
                report = recover_decisions_db.recover(
                    backup_path=backup,
                    current_path=current,
                    output_path=output,
                    apply=True,
                    skip_forward_update=True,
                )

            rows = _decision_rows(output)
            self.assertEqual(rows[0][5], 1)
            self.assertEqual(rows[0][6], 3)
            self.assertEqual(rows[0][7], 2.5)
            self.assertEqual(report["jsonl_closed_inferred"], 1)

    def test_closed_jsonl_overrides_explicit_false_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "recovered.db"
            _create_schema(backup)
            _create_schema(current)
            _insert_row(backup, row_id=1, ticker="LIVE", session_date="2026-05-12", decision="BUY_SIGNAL", filled=0)
            _write_live_jsonl(
                temp,
                [
                    {
                        "event": "closed",
                        "market": "KR",
                        "ticker": "LIVE",
                        "session_date": "2026-05-12",
                        "held_days": 1,
                        "pnl_pct": -0.5,
                        "filled": False,
                    }
                ],
            )

            with patch.object(recover_decisions_db, "ROOT", temp):
                recover_decisions_db.recover(
                    backup_path=backup,
                    current_path=current,
                    output_path=output,
                    apply=True,
                    skip_forward_update=True,
                )

            rows = _decision_rows(output)
            self.assertEqual(rows[0][5], 1)

    def test_auto_sell_review_hold_does_not_infer_closed_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "recovered.db"
            _create_schema(backup)
            _create_schema(current)
            _insert_row(backup, row_id=1, ticker="LIVE", session_date="2026-05-12", decision="BUY_SIGNAL", filled=0)
            _write_live_jsonl(
                temp,
                [
                    {
                        "type": "auto_sell_review",
                        "auto_sell_review_action": "HOLD",
                        "order_status": "closed",
                        "market": "KR",
                        "ticker": "LIVE",
                        "session_date": "2026-05-12",
                    }
                ],
            )

            with patch.object(recover_decisions_db, "ROOT", temp):
                report = recover_decisions_db.recover(
                    backup_path=backup,
                    current_path=current,
                    output_path=output,
                    apply=True,
                    skip_forward_update=True,
                )

            rows = _decision_rows(output)
            self.assertEqual(rows[0][5], 0)
            self.assertEqual(report["jsonl_closed_inferred"], 0)
            self.assertEqual(report["jsonl_review_skipped"], 1)

    def test_auto_sell_review_sell_without_fill_evidence_does_not_infer_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "recovered.db"
            _create_schema(backup)
            _create_schema(current)
            _insert_row(backup, row_id=1, ticker="LIVE", session_date="2026-05-12", decision="BUY_SIGNAL", filled=0)
            _write_live_jsonl(
                temp,
                [
                    {
                        "event": "sell_review",
                        "auto_sell_review_action": "SELL",
                        "market": "KR",
                        "ticker": "LIVE",
                        "session_date": "2026-05-12",
                    }
                ],
            )

            with patch.object(recover_decisions_db, "ROOT", temp):
                report = recover_decisions_db.recover(
                    backup_path=backup,
                    current_path=current,
                    output_path=output,
                    apply=True,
                    skip_forward_update=True,
                )

            rows = _decision_rows(output)
            self.assertEqual(rows[0][5], 0)
            self.assertEqual(report["jsonl_closed_inferred"], 0)
            self.assertEqual(report["jsonl_review_skipped"], 1)

    def test_pnl_only_jsonl_supplements_without_forcing_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "recovered.db"
            _create_schema(backup)
            _create_schema(current)
            _insert_row(backup, row_id=1, ticker="LIVE", session_date="2026-05-12", decision="BUY_SIGNAL", filled=0)
            _write_live_jsonl(
                temp,
                [
                    {
                        "event": "outcome_snapshot",
                        "market": "KR",
                        "ticker": "LIVE",
                        "session_date": "2026-05-12",
                        "pnl_pct": 1.1,
                    }
                ],
            )

            with patch.object(recover_decisions_db, "ROOT", temp):
                report = recover_decisions_db.recover(
                    backup_path=backup,
                    current_path=current,
                    output_path=output,
                    apply=True,
                    skip_forward_update=True,
                )

            rows = _decision_rows(output)
            self.assertEqual(rows[0][5], 0)
            self.assertEqual(rows[0][7], 1.1)
            self.assertEqual(report["jsonl_closed_inferred"], 0)
            self.assertEqual(report["filled_updated"], 0)

    def test_sell_filled_jsonl_is_treated_as_fill_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "recovered.db"
            _create_schema(backup)
            _create_schema(current)
            _insert_row(backup, row_id=1, ticker="LIVE", session_date="2026-05-12", decision="BUY_SIGNAL", filled=0)
            _write_live_jsonl(
                temp,
                [
                    {
                        "event": "sell_filled",
                        "market": "KR",
                        "ticker": "LIVE",
                        "session_date": "2026-05-12",
                        "held_days": 2,
                        "pnl_pct": 2.2,
                    }
                ],
            )

            with patch.object(recover_decisions_db, "ROOT", temp):
                report = recover_decisions_db.recover(
                    backup_path=backup,
                    current_path=current,
                    output_path=output,
                    apply=True,
                    skip_forward_update=True,
                )

            rows = _decision_rows(output)
            self.assertEqual(rows[0][5], 1)
            self.assertEqual(rows[0][6], 2)
            self.assertEqual(rows[0][7], 2.2)
            self.assertEqual(report["jsonl_closed_inferred"], 1)

    def test_dry_run_and_apply_report_core_counts_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "recovered.db"
            _create_schema(backup)
            _create_schema(current)
            _insert_row(backup, row_id=1, ticker="BACK", session_date="2026-04-03")
            _insert_row(current, row_id=1, ticker="LIVE", session_date="2026-05-12", decision="BUY_SIGNAL", filled=0)
            _write_live_jsonl(
                temp,
                [
                    {
                        "event": "closed",
                        "market": "KR",
                        "ticker": "LIVE",
                        "session_date": "2026-05-12",
                        "held_days": 2,
                        "pnl_pct": 3.0,
                    }
                ],
            )
            before_rows = _decision_rows(current)

            with patch.object(recover_decisions_db, "ROOT", temp):
                dry_report = recover_decisions_db.recover(
                    backup_path=backup,
                    current_path=current,
                    output_path=output,
                    apply=False,
                    skip_forward_update=True,
                )
                self.assertFalse(output.exists())
                apply_report = recover_decisions_db.recover(
                    backup_path=backup,
                    current_path=current,
                    output_path=output,
                    apply=True,
                    skip_forward_update=True,
                )

            self.assertEqual(_decision_rows(current), before_rows)
            for key in (
                "current_rows_merged",
                "current_fixture_rows_skipped",
                "id_conflicts",
                "id_remapped",
                "filled_updated",
                "jsonl_closed_inferred",
                "jsonl_review_skipped",
            ):
                self.assertEqual(dry_report[key], apply_report[key])
            self.assertTrue(dry_report["quick_check_ok"])
            self.assertTrue(apply_report["quick_check_ok"])

    def test_recovery_simulation_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "recovered.db"
            _create_schema(backup)
            _create_schema(current)
            _insert_row(backup, row_id=1, ticker="BACK", session_date="2026-04-03")
            _insert_row(current, row_id=1, ticker="LIVE", session_date="2026-05-12", decision="BUY_SIGNAL", filled=0)
            _write_live_jsonl(
                temp,
                [
                    {
                        "type": "closed",
                        "market": "KR",
                        "ticker": "LIVE",
                        "session_date": "2026-05-12",
                        "held_days": 4,
                        "pnl_pct": 4.2,
                        "filled": False,
                    }
                ],
            )

            with patch.object(recover_decisions_db, "ROOT", temp):
                dry_report = recover_decisions_db.recover(
                    backup_path=backup,
                    current_path=current,
                    output_path=output,
                    apply=False,
                    skip_forward_update=True,
                )
                apply_report = recover_decisions_db.recover(
                    backup_path=backup,
                    current_path=current,
                    output_path=output,
                    apply=True,
                    skip_forward_update=True,
                )

            rows = _decision_rows(output)
            self.assertEqual([(row[2], row[3]) for row in rows], [("BACK", "2026-04-03"), ("LIVE", "2026-05-12")])
            live_row = rows[1]
            self.assertNotEqual(live_row[0], 1)
            self.assertEqual(live_row[5], 1)
            self.assertEqual(live_row[6], 4)
            self.assertEqual(live_row[7], 4.2)
            self.assertEqual(apply_report["id_remapped"], 1)
            self.assertEqual(apply_report["filled_updated"], 1)
            self.assertEqual(apply_report["jsonl_closed_inferred"], 1)
            self.assertTrue(apply_report["id_remap_log"])
            self.assertTrue(apply_report["quick_check_ok"])
            for key in ("id_conflicts", "id_remapped", "filled_updated", "jsonl_closed_inferred", "jsonl_review_skipped"):
                self.assertEqual(dry_report[key], apply_report[key])


if __name__ == "__main__":
    unittest.main()
