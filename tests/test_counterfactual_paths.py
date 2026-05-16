from __future__ import annotations

import json
import tempfile
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
