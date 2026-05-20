from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from audit.candidate_counterfactual_store import CandidateCounterfactualStore
from runtime.counterfactual_paths import build_counterfactual_rows, safe_write_counterfactual_paths


def test_counterfactual_paths_upsert_and_store_missing_kr_feature_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        rows = build_counterfactual_rows(
            runtime_mode="live",
            session_date="2026-05-16",
            market="KR",
            ticker="005930",
            trade_ready_action="BUY_READY",
            known_at="2026-05-16T09:05:00+09:00",
            context={"current_price": 70000, "data_quality": "good"},
        )

        first = safe_write_counterfactual_paths(rows, db_path=db)
        second = safe_write_counterfactual_paths(rows, db_path=db)

        store = CandidateCounterfactualStore(db)
        stored = store.fetch_rows(session_date="2026-05-16", market="KR")
        by_path = {row["path_name"]: row for row in stored}
        assert first["ok"] is True
        assert second["ok"] is True
        assert first["duration_ms"] >= 0
        assert first["errors"] == []
        assert len(stored) == len(rows)
        assert by_path["immediate"]["status"] == "TRIGGERED"
        assert by_path["vi_safe_reclaim"]["status"] == "DATA_MISSING"
        assert by_path["orderbook_support"]["status"] == "DATA_MISSING"
        metadata = json.loads(by_path["immediate"]["metadata_json"])
        assert metadata["kr_confirmation"]["score"] is None
        assert metadata["microstructure"]["vi_state"] is None


def test_counterfactual_write_failure_does_not_raise() -> None:
    result = safe_write_counterfactual_paths(
        [{"runtime_mode": "live", "session_date": "2026-05-16", "market": "KR", "ticker": "005930"}],
        db_path=Path("\0bad"),
    )

    assert result["ok"] is False


def test_counterfactual_bulk_write_keeps_successful_rows_after_row_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        rows = build_counterfactual_rows(
            runtime_mode="live",
            session_date="2026-05-16",
            market="KR",
            ticker="005930",
            trade_ready_action="BUY_READY",
            known_at="2026-05-16T09:05:00+09:00",
            context={"current_price": 70000, "data_quality": "good"},
        )[:3]
        original = CandidateCounterfactualStore._upsert_path_conn

        def fail_or_break(self, conn, row):
            if row.get("path_name") == "or_break":
                raise RuntimeError("bad row")
            return original(self, conn, row)

        with patch.object(CandidateCounterfactualStore, "_upsert_path_conn", autospec=True, side_effect=fail_or_break):
            result = safe_write_counterfactual_paths(rows, db_path=db)

        stored = CandidateCounterfactualStore(db).fetch_rows(session_date="2026-05-16", market="KR")
        assert result["ok"] is False
        assert result["count"] == 2
        assert result["errors"][0]["path_name"] == "or_break"
        assert {row["path_name"] for row in stored} == {"immediate", "vwap_reclaim"}


def test_counterfactual_store_migrates_metadata_columns_for_existing_db() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        conn = sqlite3.connect(db)
        try:
            conn.executescript(
                """
                CREATE TABLE candidate_counterfactual_paths (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  runtime_mode TEXT NOT NULL,
                  session_date TEXT NOT NULL,
                  market TEXT NOT NULL,
                  ticker TEXT NOT NULL,
                  candidate_key TEXT,
                  call_id TEXT,
                  signal_time TEXT NOT NULL,
                  known_at TEXT NOT NULL,
                  trade_ready_action TEXT,
                  actual_path TEXT,
                  path_name TEXT NOT NULL,
                  trigger_time TEXT,
                  trigger_price REAL,
                  trigger_reason TEXT,
                  entry_price REAL,
                  entry_delay_min REAL,
                  outcome_30m_pct REAL,
                  outcome_60m_pct REAL,
                  outcome_close_pct REAL,
                  max_runup_60m_pct REAL,
                  max_drawdown_60m_pct REAL,
                  status TEXT NOT NULL DEFAULT 'PENDING',
                  metadata_json TEXT DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

        store = CandidateCounterfactualStore(db)
        with closing(store.connect()) as conn:
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(candidate_counterfactual_paths)")}

        assert "metadata_quality" in columns
        assert "label_source" in columns


def test_counterfactual_upsert_preserves_first_quality_and_merges_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        store = CandidateCounterfactualStore(db)
        base = {
            "runtime_mode": "live",
            "session_date": "2026-05-16",
            "market": "US",
            "ticker": "aapl",
            "known_at": "2026-05-16T09:05:00+09:00",
            "signal_time": "2026-05-16T09:05:00+09:00",
            "path_name": "immediate",
            "status": "TRIGGERED",
            "metadata_quality": "runtime_authoritative",
            "label_source": "runtime_live",
            "metadata": {
                "runtime_key": "keep",
                "source_attempts": ["runtime"],
                "label_horizons": ["30m"],
            },
        }
        store.upsert_path(base)
        store.upsert_path(
            {
                **base,
                "metadata_quality": "backfill_diagnostic",
                "label_source": "backfill",
                "metadata": {
                    "runtime_key": "replace",
                    "diagnostic_key": "add",
                    "source_attempts": ["backfill"],
                    "label_horizons": ["close"],
                },
            }
        )

        row = store.fetch_rows(session_date="2026-05-16", market="US")[0]
        metadata = json.loads(row["metadata_json"])

        assert row["ticker"] == "AAPL"
        assert row["metadata_quality"] == "runtime_authoritative"
        assert row["label_source"] == "runtime_live"
        assert metadata["runtime_key"] == "keep"
        assert metadata["diagnostic_key"] == "add"
        assert metadata["source_attempts"] == ["backfill", "runtime"]
        assert metadata["label_horizons"] == ["30m", "close"]


def test_counterfactual_upsert_upgrades_diagnostic_to_runtime_authoritative() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        store = CandidateCounterfactualStore(db)
        base = {
            "runtime_mode": "live",
            "session_date": "2026-05-16",
            "market": "US",
            "ticker": "aapl",
            "known_at": "2026-05-16T09:05:00+09:00",
            "signal_time": "2026-05-16T09:05:00+09:00",
            "path_name": "immediate",
            "status": "TRIGGERED",
            "metadata_quality": "backfill_diagnostic",
            "label_source": "virtual_immediate_shadow",
            "metadata": {
                "runtime_key": "old",
                "source_attempts": ["daily_close"],
                "label_horizons": ["close"],
            },
        }
        store.upsert_path(base)
        store.upsert_path(
            {
                **base,
                "metadata_quality": "runtime_authoritative",
                "label_source": "",
                "metadata": {
                    "runtime_key": "new",
                    "recommended_strategy": "momentum",
                    "source_attempts": ["runtime"],
                    "label_horizons": ["30m"],
                },
            }
        )

        row = store.fetch_rows(session_date="2026-05-16", market="US")[0]
        metadata = json.loads(row["metadata_json"])

        assert row["metadata_quality"] == "runtime_authoritative"
        assert row["label_source"] == "virtual_immediate_shadow"
        assert metadata["runtime_key"] == "new"
        assert metadata["recommended_strategy"] == "momentum"
        assert metadata["source_attempts"] == ["daily_close", "runtime"]
        assert metadata["label_horizons"] == ["30m", "close"]


def test_counterfactual_rows_store_metadata_overrides() -> None:
    rows = build_counterfactual_rows(
        runtime_mode="live",
        session_date="2026-05-16",
        market="US",
        ticker="aapl",
        trade_ready_action="BUY_READY",
        known_at="2026-05-16T09:05:00+09:00",
        context={"current_price": 100.0},
        metadata_overrides={
            "metadata_quality": "runtime_authoritative",
            "recommended_strategy": "momentum",
            "mode_family": "RISK_OFF",
            "slot_filter_reason": "slot_disabled:momentum",
        },
    )

    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["metadata_quality"] == "runtime_authoritative"
    assert rows[0]["label_source"] is None
    assert rows[0]["metadata"]["recommended_strategy"] == "momentum"
    assert rows[0]["metadata"]["mode_family"] == "RISK_OFF"
    assert rows[0]["metadata"]["slot_filter_reason"] == "slot_disabled:momentum"


def test_mark_outcome_does_not_downgrade_runtime_authoritative_quality() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        store = CandidateCounterfactualStore(db)
        store.upsert_path(
            {
                "runtime_mode": "live",
                "session_date": "2026-05-16",
                "market": "US",
                "ticker": "AAPL",
                "known_at": "2026-05-16T09:05:00+09:00",
                "signal_time": "2026-05-16T09:05:00+09:00",
                "path_name": "immediate",
                "metadata_quality": "runtime_authoritative",
            }
        )
        row = store.fetch_rows(session_date="2026-05-16", market="US")[0]

        store.mark_outcome(
            int(row["id"]),
            outcome_close_pct=1.2,
            status="CLOSE_OUTCOME_FILLED",
            metadata_quality="backfill_diagnostic",
            label_source="virtual_immediate_shadow",
        )

        updated = store.fetch_rows(session_date="2026-05-16", market="US")[0]
        assert updated["metadata_quality"] == "runtime_authoritative"
        assert updated["label_source"] == "virtual_immediate_shadow"
        assert updated["outcome_close_pct"] == 1.2


def test_mark_outcome_upgrades_diagnostic_label_to_virtual_when_close_filled() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        store = CandidateCounterfactualStore(db)
        store.upsert_path(
            {
                "runtime_mode": "live",
                "session_date": "2026-05-16",
                "market": "US",
                "ticker": "AAPL",
                "known_at": "2026-05-16T09:05:00+09:00",
                "signal_time": "2026-05-16T09:05:00+09:00",
                "path_name": "immediate",
                "label_source": "counterfactual_outcome_updater",
            }
        )
        row = store.fetch_rows(session_date="2026-05-16", market="US")[0]

        store.mark_outcome(
            int(row["id"]),
            outcome_close_pct=1.2,
            status="CLOSE_OUTCOME_FILLED",
            metadata_quality="backfill_diagnostic",
            label_source="virtual_immediate_shadow",
        )

        updated = store.fetch_rows(session_date="2026-05-16", market="US")[0]
        assert updated["label_source"] == "virtual_immediate_shadow"


def test_mark_outcome_does_not_replace_actual_label_source() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        store = CandidateCounterfactualStore(db)
        store.upsert_path(
            {
                "runtime_mode": "live",
                "session_date": "2026-05-16",
                "market": "US",
                "ticker": "AAPL",
                "known_at": "2026-05-16T09:05:00+09:00",
                "signal_time": "2026-05-16T09:05:00+09:00",
                "path_name": "immediate",
                "label_source": "actual_fill",
            }
        )
        row = store.fetch_rows(session_date="2026-05-16", market="US")[0]

        store.mark_outcome(
            int(row["id"]),
            outcome_close_pct=1.2,
            status="CLOSE_OUTCOME_FILLED",
            label_source="virtual_immediate_shadow",
        )

        updated = store.fetch_rows(session_date="2026-05-16", market="US")[0]
        assert updated["label_source"] == "actual_fill"


def test_counterfactual_upsert_does_not_revert_protected_statuses() -> None:
    protected_statuses = [
        "CLOSE_OUTCOME_FILLED",
        "OUTCOME_FILLED",
        "OUTCOME_PARTIAL",
        "PRICE_PENDING",
        "PRICE_UNAVAILABLE",
        "DATA_MISSING",
    ]
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        store = CandidateCounterfactualStore(db)
        for index, status in enumerate(protected_statuses):
            base = {
                "runtime_mode": "live",
                "session_date": "2026-05-16",
                "market": "US",
                "ticker": f"T{index}",
                "known_at": f"2026-05-16T09:{index:02d}:00+09:00",
                "signal_time": f"2026-05-16T09:{index:02d}:00+09:00",
                "path_name": "immediate",
                "entry_price": 100.0,
                "trigger_time": f"2026-05-16T09:{index:02d}:00+09:00",
                "status": status,
            }
            store.upsert_path(base)
            store.upsert_path({**base, "status": "TRIGGERED"})

        rows = store.fetch_rows(session_date="2026-05-16", market="US")

        assert [row["status"] for row in rows] == protected_statuses


def test_counterfactual_promotion_groups_require_authoritative_ten_distinct_sessions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        store = CandidateCounterfactualStore(db)
        for day in range(1, 11):
            store.upsert_path(
                {
                    "runtime_mode": "live",
                    "session_date": f"2026-05-{day:02d}",
                    "market": "US",
                    "ticker": "AAPL",
                    "known_at": f"2026-05-{day:02d}T09:05:00+09:00",
                    "signal_time": f"2026-05-{day:02d}T09:05:00+09:00",
                    "path_name": "immediate",
                    "metadata_quality": "runtime_authoritative",
                    "label_source": "virtual_immediate_shadow",
                    "outcome_close_pct": 1.0,
                    "metadata": {"recommended_strategy": "momentum", "mode_family": "RISK_OFF"},
                }
            )
        for day in range(1, 10):
            store.upsert_path(
                {
                    "runtime_mode": "live",
                    "session_date": f"2026-06-{day:02d}",
                    "market": "US",
                    "ticker": "MSFT",
                    "known_at": f"2026-06-{day:02d}T09:05:00+09:00",
                    "signal_time": f"2026-06-{day:02d}T09:05:00+09:00",
                    "path_name": "wait_30m",
                    "metadata_quality": "runtime_authoritative",
                    "label_source": "virtual_immediate_shadow",
                    "outcome_close_pct": 1.0,
                    "metadata": {"recommended_strategy": "momentum", "mode_family": "RISK_ON"},
                }
            )
        for day in range(1, 12):
            store.upsert_path(
                {
                    "runtime_mode": "live",
                    "session_date": f"2026-07-{day:02d}",
                    "market": "US",
                    "ticker": "NVDA",
                    "known_at": f"2026-07-{day:02d}T09:05:00+09:00",
                    "signal_time": f"2026-07-{day:02d}T09:05:00+09:00",
                    "path_name": "or_break",
                    "metadata_quality": "backfill_diagnostic",
                    "label_source": "virtual_immediate_shadow",
                    "outcome_close_pct": 1.0,
                    "metadata": {"recommended_strategy": "momentum", "mode_family": "RISK_OFF"},
                }
            )
        for day in range(1, 12):
            store.upsert_path(
                {
                    "runtime_mode": "live",
                    "session_date": f"2026-08-{day:02d}",
                    "market": "US",
                    "ticker": "TSLA",
                    "known_at": f"2026-08-{day:02d}T09:05:00+09:00",
                    "signal_time": f"2026-08-{day:02d}T09:05:00+09:00",
                    "path_name": "immediate",
                    "metadata_quality": "runtime_authoritative",
                    "label_source": "counterfactual_outcome_updater",
                    "outcome_close_pct": 1.0,
                    "metadata": {"recommended_strategy": "momentum", "mode_family": "RISK_OFF"},
                }
            )

        groups = store.promotion_eligible_groups(market="US", min_sessions=10)

        assert [
            (row["market"], row["recommended_strategy"], row["mode_family"], row["distinct_sessions"])
            for row in groups
        ] == [
            ("US", "momentum", "RISK_OFF", 10)
        ]
