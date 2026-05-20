from __future__ import annotations

import csv
import json
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from audit.candidate_counterfactual_store import CandidateCounterfactualStore
from bot.session_date import KST
from tools.analyze_counterfactual_paths import analyze_counterfactual_paths, to_markdown
from tools.update_counterfactual_outcomes import _lookup_ticker_value, update_counterfactual_outcomes


def _write_price_csv(price_root: Path, market: str, ticker: str, rows: list[tuple[str, float]]) -> None:
    market_key = market.upper()
    if market_key == "US":
        path = price_root / "us" / f"us_{ticker.upper()}.csv"
    else:
        path = price_root / "kr" / f"kr_{ticker.zfill(6)}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for date_text, close in rows:
            writer.writerow([date_text, close, close, close, close, 1000])


def _write_minute_csv(price_root: Path, market: str, ticker: str, rows: list[tuple[str, float, float, float]]) -> None:
    market_key = market.upper()
    market_dir = market_key.lower()
    ticker_key = ticker.upper() if market_key == "US" else ticker.zfill(6)
    path = price_root / "minute" / market_dir / f"{market_dir}_{ticker_key}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ts", "open", "high", "low", "close", "volume"])
        for ts, close, high, low in rows:
            writer.writerow([ts, close, high, low, close, 1000])


def _insert_counterfactual(
    store: CandidateCounterfactualStore,
    *,
    session_date: str = "2026-05-19",
    market: str = "US",
    ticker: str = "CRDO",
    status: str = "TRIGGERED",
    path_name: str = "immediate",
    entry_price: float | None = 100.0,
    metadata_quality: str = "",
) -> None:
    store.upsert_path(
        {
            "runtime_mode": "live",
            "session_date": session_date,
            "market": market,
            "ticker": ticker,
            "known_at": f"{session_date}T09:35:00+09:00",
            "signal_time": f"{session_date}T09:35:00+09:00",
            "trade_ready_action": "BUY_READY",
            "path_name": path_name,
            "entry_price": entry_price,
            "trigger_time": f"{session_date}T09:35:00+09:00" if entry_price is not None else None,
            "status": status,
            "metadata_quality": metadata_quality or None,
            "metadata": {
                "source_attempts": ["runtime"],
                "label_horizons": ["30m"],
                "reason": "minute_data_not_available" if status == "DATA_MISSING" else "",
            },
        }
    )


def test_update_counterfactual_outcomes_fills_close_from_daily_csv() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, metadata_quality="runtime_authoritative")
        _write_price_csv(price_root, "US", "CRDO", [("2026-05-19", 110.0)])

        with patch.object(CandidateCounterfactualStore, "upsert_path", side_effect=AssertionError("no upsert")):
            with patch.object(CandidateCounterfactualStore, "upsert_paths", side_effect=AssertionError("no upsert")):
                result = update_counterfactual_outcomes(
                    db_path=db,
                    session_date="2026-05-19",
                    market="US",
                    price_root=price_root,
                )

        row = store.fetch_rows(session_date="2026-05-19", market="US")[0]
        assert result["filled"] == 1
        assert row["status"] == "CLOSE_OUTCOME_FILLED"
        assert row["metadata_quality"] == "runtime_authoritative"
        assert row["label_source"] == "virtual_immediate_shadow"
        assert round(float(row["outcome_close_pct"]), 6) == 10.0


def test_update_counterfactual_outcomes_infers_wait_entry_from_minute_csv() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        store.upsert_path(
            {
                "runtime_mode": "live",
                "session_date": "2026-05-19",
                "market": "KR",
                "ticker": "005930",
                "known_at": "2026-05-19T09:05:00+09:00",
                "signal_time": "2026-05-19T09:05:00+09:00",
                "trade_ready_action": "BUY_READY",
                "path_name": "wait_30m",
                "trigger_time": "2026-05-19T09:35:00+09:00",
                "entry_delay_min": 30.0,
                "status": "PENDING",
                "metadata": {"source_attempts": ["runtime"]},
            }
        )
        _write_minute_csv(
            price_root,
            "KR",
            "005930",
            [
                ("2026-05-19T09:35:00+09:00", 101.0, 103.0, 100.0),
                ("2026-05-19T10:05:00+09:00", 104.0, 105.0, 99.0),
                ("2026-05-19T10:35:00+09:00", 106.0, 107.0, 98.0),
            ],
        )
        _write_price_csv(price_root, "KR", "005930", [("2026-05-19", 108.0)])

        result = update_counterfactual_outcomes(
            db_path=db,
            session_date="2026-05-19",
            market="KR",
            price_root=price_root,
            minute_root=price_root,
        )

        row = store.fetch_rows(session_date="2026-05-19", market="KR")[0]
        assert result["filled"] == 1
        assert row["entry_price"] == 101.0
        assert row["status"] == "CLOSE_OUTCOME_FILLED"
        assert row["outcome_30m_pct"] is not None
        metadata = json.loads(row["metadata_json"])
        assert metadata["entry_price_source"] == "minute_csv_trigger"


def test_wait_entry_missing_minute_is_retryable_price_pending() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        store.upsert_path(
            {
                "runtime_mode": "live",
                "session_date": "2026-05-20",
                "market": "KR",
                "ticker": "005930",
                "known_at": "2026-05-20T09:05:00+09:00",
                "signal_time": "2026-05-20T09:05:00+09:00",
                "trade_ready_action": "BUY_READY",
                "path_name": "wait_30m",
                "trigger_time": "2026-05-20T09:35:00+09:00",
                "entry_delay_min": 30.0,
                "status": "PENDING",
                "metadata": {"source_attempts": ["runtime"]},
            }
        )

        first = update_counterfactual_outcomes(
            db_path=db,
            session_date="2026-05-20",
            market="KR",
            price_root=price_root,
            minute_root=price_root,
            _now=datetime(2026, 5, 20, 9, 40, tzinfo=KST),
        )
        _write_minute_csv(
            price_root,
            "KR",
            "005930",
            [
                ("2026-05-20T09:35:00+09:00", 101.0, 103.0, 100.0),
                ("2026-05-20T10:05:00+09:00", 104.0, 105.0, 99.0),
            ],
        )
        _write_price_csv(price_root, "KR", "005930", [("2026-05-20", 108.0)])
        retry = update_counterfactual_outcomes(
            db_path=db,
            session_date="2026-05-20",
            market="KR",
            retry_missing=True,
            price_root=price_root,
            minute_root=price_root,
            _now=datetime(2026, 5, 20, 16, 10, tzinfo=KST),
        )

        row = store.fetch_rows(session_date="2026-05-20", market="KR")[0]
        assert first["price_pending"] == 1
        assert retry["targeted"] == 1
        assert retry["filled"] == 1
        assert row["entry_price"] == 101.0
        assert row["status"] == "CLOSE_OUTCOME_FILLED"


def test_update_counterfactual_outcomes_marks_pending_when_close_not_available_yet() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, session_date="2026-05-20", market="KR", ticker="142280")
        _write_price_csv(price_root, "KR", "142280", [("2026-05-19", 100.0)])

        result = update_counterfactual_outcomes(db_path=db, session_date="2026-05-20", market="KR", price_root=price_root)

        row = store.fetch_rows(session_date="2026-05-20", market="KR")[0]
        assert result["price_pending"] == 1
        assert row["status"] == "PRICE_PENDING"
        assert row["outcome_close_pct"] is None


def test_update_counterfactual_outcomes_marks_unavailable_for_historical_missing_close() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, session_date="2026-05-18", market="US", ticker="CRDO")
        _write_price_csv(price_root, "US", "CRDO", [("2026-05-19", 110.0)])

        result = update_counterfactual_outcomes(db_path=db, session_date="2026-05-18", market="US", price_root=price_root)

        row = store.fetch_rows(session_date="2026-05-18", market="US")[0]
        assert result["price_unavailable"] == 1
        assert row["status"] == "PRICE_UNAVAILABLE"


def test_update_counterfactual_outcomes_marks_missing_entry_or_trigger_data() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, entry_price=None)

        result = update_counterfactual_outcomes(db_path=db, session_date="2026-05-19", market="US", price_root=price_root)

        row = store.fetch_rows(session_date="2026-05-19", market="US")[0]
        assert result["targeted"] == 1
        assert result["data_missing"] == 1
        assert row["status"] == "DATA_MISSING"
        assert row["outcome_close_pct"] is None


def test_update_counterfactual_outcomes_marks_missing_future_price_file_pending() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        future_session = (date.today() + timedelta(days=1)).isoformat()
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, session_date=future_session)

        result = update_counterfactual_outcomes(db_path=db, session_date=future_session, market="US", price_root=price_root)
        retry = update_counterfactual_outcomes(
            db_path=db,
            session_date=future_session,
            market="US",
            retry_missing=True,
            price_root=price_root,
        )

        row = store.fetch_rows(session_date=future_session, market="US")[0]
        assert result["price_pending"] == 1
        assert row["status"] == "PRICE_PENDING"
        assert retry["targeted"] == 1


def test_update_counterfactual_outcomes_keeps_us_previous_session_pending_after_kst_midnight() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, session_date="2026-05-19", market="US", ticker="CRDO")

        result = update_counterfactual_outcomes(
            db_path=db,
            session_date="2026-05-19",
            market="US",
            price_root=price_root,
            _now=datetime(2026, 5, 20, 1, 0, tzinfo=KST),
        )

        row = store.fetch_rows(session_date="2026-05-19", market="US")[0]
        assert result["price_pending"] == 1
        assert row["status"] == "PRICE_PENDING"


def test_update_counterfactual_outcomes_keeps_just_closed_us_session_pending_during_daily_bar_grace() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, session_date="2026-05-19", market="US", ticker="CRDO")

        result = update_counterfactual_outcomes(
            db_path=db,
            session_date="2026-05-19",
            market="US",
            price_root=price_root,
            _now=datetime(2026, 5, 20, 6, 30, tzinfo=KST),
        )

        row = store.fetch_rows(session_date="2026-05-19", market="US")[0]
        assert result["price_pending"] == 1
        assert row["status"] == "PRICE_PENDING"


def test_update_counterfactual_outcomes_marks_us_missing_price_unavailable_after_daily_bar_grace() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, session_date="2026-05-19", market="US", ticker="CRDO")

        result = update_counterfactual_outcomes(
            db_path=db,
            session_date="2026-05-19",
            market="US",
            price_root=price_root,
            _now=datetime(2026, 5, 20, 7, 1, tzinfo=KST),
        )

        row = store.fetch_rows(session_date="2026-05-19", market="US")[0]
        assert result["price_unavailable"] == 1
        assert row["status"] == "PRICE_UNAVAILABLE"


def test_update_counterfactual_outcomes_weekend_us_gap_is_unavailable_but_retryable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, session_date="2026-05-15", market="US", ticker="CRDO")

        first = update_counterfactual_outcomes(
            db_path=db,
            session_date="2026-05-15",
            market="US",
            price_root=price_root,
            _now=datetime(2026, 5, 18, 6, 30, tzinfo=KST),
        )
        _write_price_csv(price_root, "US", "CRDO", [("2026-05-15", 107.0)])
        retry = update_counterfactual_outcomes(
            db_path=db,
            session_date="2026-05-15",
            market="US",
            retry_missing=True,
            price_root=price_root,
        )

        row = store.fetch_rows(session_date="2026-05-15", market="US")[0]
        assert first["price_unavailable"] == 1
        assert retry["targeted"] == 1
        assert retry["filled"] == 1
        assert row["status"] == "CLOSE_OUTCOME_FILLED"


def test_retry_missing_targets_price_statuses_not_data_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, session_date="2026-05-20", status="PRICE_PENDING")
        _insert_counterfactual(store, session_date="2026-05-19", ticker="MSFT", status="DATA_MISSING")
        _write_price_csv(price_root, "US", "CRDO", [("2026-05-20", 105.0)])
        _write_price_csv(price_root, "US", "MSFT", [("2026-05-19", 105.0)])

        result = update_counterfactual_outcomes(
            db_path=db,
            market="US",
            retry_missing=True,
            price_root=price_root,
        )

        rows = {row["ticker"]: row for row in store.fetch_rows(market="US")}
        assert result["targeted"] == 1
        assert rows["CRDO"]["status"] == "CLOSE_OUTCOME_FILLED"
        assert rows["MSFT"]["status"] == "DATA_MISSING"


def test_retry_missing_retries_price_unavailable_when_csv_arrives() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, session_date="2026-05-18", status="PRICE_UNAVAILABLE")
        _write_price_csv(price_root, "US", "CRDO", [("2026-05-18", 106.0)])

        result = update_counterfactual_outcomes(
            db_path=db,
            market="US",
            retry_missing=True,
            price_root=price_root,
        )

        row = store.fetch_rows(market="US")[0]
        assert result["targeted"] == 1
        assert result["filled"] == 1
        assert row["status"] == "CLOSE_OUTCOME_FILLED"


def test_retry_missing_does_not_overwrite_existing_close_outcome() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, session_date="2026-05-18", status="PRICE_UNAVAILABLE")
        row_id = int(store.fetch_rows(market="US")[0]["id"])
        store.mark_outcome(row_id, outcome_close_pct=3.0, status="PRICE_UNAVAILABLE")
        _write_price_csv(price_root, "US", "CRDO", [("2026-05-18", 106.0)])

        result = update_counterfactual_outcomes(
            db_path=db,
            market="US",
            retry_missing=True,
            price_root=price_root,
        )

        row = store.fetch_rows(market="US")[0]
        assert result["targeted"] == 0
        assert row["status"] == "PRICE_UNAVAILABLE"
        assert row["outcome_close_pct"] == 3.0


def test_default_run_does_not_overwrite_existing_close_outcome() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, status="TRIGGERED")
        row_id = int(store.fetch_rows(market="US")[0]["id"])
        store.mark_outcome(row_id, outcome_close_pct=3.0, status="TRIGGERED")
        _write_price_csv(price_root, "US", "CRDO", [("2026-05-19", 120.0)])

        result = update_counterfactual_outcomes(db_path=db, market="US", price_root=price_root)

        row = store.fetch_rows(market="US")[0]
        assert result["targeted"] == 0
        assert row["status"] == "TRIGGERED"
        assert row["outcome_close_pct"] == 3.0


def test_default_run_adds_minute_outcomes_to_existing_close_label_without_refilling_close() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, status="CLOSE_OUTCOME_FILLED")
        row_id = int(store.fetch_rows(market="US")[0]["id"])
        store.mark_outcome(row_id, outcome_close_pct=3.0, status="CLOSE_OUTCOME_FILLED")
        _write_price_csv(price_root, "US", "CRDO", [("2026-05-19", 120.0)])
        _write_minute_csv(
            price_root,
            "US",
            "CRDO",
            [
                ("2026-05-19T10:05:00+09:00", 105.0, 106.0, 98.0),
                ("2026-05-19T10:35:00+09:00", 108.0, 110.0, 97.0),
            ],
        )

        result = update_counterfactual_outcomes(db_path=db, market="US", price_root=price_root)

        row = store.fetch_rows(market="US")[0]
        assert result["targeted"] == 1
        assert result["filled"] == 0
        assert result["minute_filled"] == 1
        assert row["status"] == "CLOSE_OUTCOME_FILLED"
        assert round(float(row["outcome_close_pct"]), 6) == 3.0
        assert round(float(row["outcome_30m_pct"]), 6) == 5.0
        assert round(float(row["outcome_60m_pct"]), 6) == 8.0
        assert round(float(row["max_runup_60m_pct"]), 6) == 10.0
        assert round(float(row["max_drawdown_60m_pct"]), 6) == -3.0


def test_default_run_does_not_downgrade_existing_close_when_entry_or_trigger_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, status="CLOSE_OUTCOME_FILLED", entry_price=None)
        row_id = int(store.fetch_rows(market="US")[0]["id"])
        store.mark_outcome(row_id, outcome_close_pct=3.0, status="CLOSE_OUTCOME_FILLED")
        _write_minute_csv(
            price_root,
            "US",
            "CRDO",
            [("2026-05-19T10:05:00+09:00", 105.0, 106.0, 98.0)],
        )

        result = update_counterfactual_outcomes(db_path=db, market="US", price_root=price_root)

        row = store.fetch_rows(market="US")[0]
        assert result["targeted"] == 0
        assert result["data_missing"] == 0
        assert row["status"] == "CLOSE_OUTCOME_FILLED"
        assert row["outcome_close_pct"] == 3.0


def test_minute_backfill_does_not_overwrite_existing_minute_outcomes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, status="CLOSE_OUTCOME_FILLED")
        row_id = int(store.fetch_rows(market="US")[0]["id"])
        store.mark_outcome(
            row_id,
            outcome_close_pct=3.0,
            outcome_30m_pct=2.0,
            status="CLOSE_OUTCOME_FILLED",
        )
        _write_minute_csv(
            price_root,
            "US",
            "CRDO",
            [
                ("2026-05-19T10:05:00+09:00", 105.0, 106.0, 98.0),
                ("2026-05-19T10:35:00+09:00", 108.0, 110.0, 97.0),
            ],
        )

        result = update_counterfactual_outcomes(db_path=db, market="US", price_root=price_root)

        row = store.fetch_rows(market="US")[0]
        assert result["targeted"] == 1
        assert result["filled"] == 0
        assert result["minute_filled"] == 1
        assert row["status"] == "CLOSE_OUTCOME_FILLED"
        assert round(float(row["outcome_close_pct"]), 6) == 3.0
        assert round(float(row["outcome_30m_pct"]), 6) == 2.0
        assert round(float(row["outcome_60m_pct"]), 6) == 8.0
        assert round(float(row["max_runup_60m_pct"]), 6) == 10.0
        assert round(float(row["max_drawdown_60m_pct"]), 6) == -3.0


def test_default_run_skips_partial_rows_until_retry_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, status="OUTCOME_PARTIAL")
        row_id = int(store.fetch_rows(market="US")[0]["id"])
        store.mark_outcome(
            row_id,
            outcome_30m_pct=2.0,
            status="OUTCOME_PARTIAL",
            metadata_json=json.dumps({"final_attempt_at": "first"}, ensure_ascii=False),
        )
        _write_price_csv(price_root, "US", "CRDO", [("2026-05-19", 110.0)])

        result = update_counterfactual_outcomes(db_path=db, market="US", price_root=price_root)

        row = store.fetch_rows(market="US")[0]
        metadata = json.loads(row["metadata_json"])
        assert result["targeted"] == 0
        assert row["status"] == "OUTCOME_PARTIAL"
        assert row["outcome_close_pct"] is None
        assert metadata["final_attempt_at"] == "first"


def test_retry_missing_fills_partial_close_when_daily_csv_arrives() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, status="OUTCOME_PARTIAL")
        row_id = int(store.fetch_rows(market="US")[0]["id"])
        store.mark_outcome(row_id, outcome_30m_pct=2.0, status="OUTCOME_PARTIAL")
        _write_price_csv(price_root, "US", "CRDO", [("2026-05-19", 110.0)])

        result = update_counterfactual_outcomes(
            db_path=db,
            market="US",
            retry_missing=True,
            price_root=price_root,
        )

        row = store.fetch_rows(market="US")[0]
        assert result["targeted"] == 1
        assert result["filled"] == 1
        assert row["status"] == "CLOSE_OUTCOME_FILLED"
        assert round(float(row["outcome_close_pct"]), 6) == 10.0


def test_repair_legacy_data_missing_is_conditional() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, status="DATA_MISSING")
        _write_price_csv(price_root, "US", "CRDO", [("2026-05-19", 120.0)])

        result = update_counterfactual_outcomes(
            db_path=db,
            market="US",
            repair_legacy_data_missing=True,
            price_root=price_root,
        )

        row = store.fetch_rows(market="US")[0]
        assert result["filled"] == 1
        assert row["status"] == "CLOSE_OUTCOME_FILLED"


def test_update_counterfactual_outcomes_fills_non_immediate_close_rows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, path_name="wait_30m")
        _write_price_csv(price_root, "US", "CRDO", [("2026-05-19", 110.0)])

        result = update_counterfactual_outcomes(db_path=db, market="US", price_root=price_root)

        row = store.fetch_rows(market="US")[0]
        assert result["targeted"] == 1
        assert result["filled"] == 1
        assert row["status"] == "CLOSE_OUTCOME_FILLED"
        assert round(float(row["outcome_close_pct"]), 6) == 10.0


def test_update_counterfactual_outcomes_fills_minute_horizons_and_mfe_mae() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, path_name="wait_30m")
        _write_price_csv(price_root, "US", "CRDO", [("2026-05-19", 112.0)])
        _write_minute_csv(
            price_root,
            "US",
            "CRDO",
            [
                ("2026-05-19T09:40:00+09:00", 101.0, 103.0, 99.0),
                ("2026-05-19T10:05:00+09:00", 105.0, 106.0, 98.0),
                ("2026-05-19T10:35:00+09:00", 108.0, 110.0, 97.0),
            ],
        )

        result = update_counterfactual_outcomes(db_path=db, market="US", price_root=price_root)

        row = store.fetch_rows(market="US")[0]
        metadata = json.loads(row["metadata_json"])
        assert result["targeted"] == 1
        assert result["filled"] == 1
        assert result["minute_filled"] == 1
        assert round(float(row["outcome_30m_pct"]), 6) == 5.0
        assert round(float(row["outcome_60m_pct"]), 6) == 8.0
        assert round(float(row["outcome_close_pct"]), 6) == 12.0
        assert round(float(row["max_runup_60m_pct"]), 6) == 10.0
        assert round(float(row["max_drawdown_60m_pct"]), 6) == -3.0
        assert metadata["price_sample_count"]["minute"] == 3
        assert "60m" in metadata["label_horizons"]


def test_retry_missing_backfills_outcome_partial_close_and_preserves_minute_labels() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "candidate_audit.db"
        price_root = root / "price"
        store = CandidateCounterfactualStore(db)
        _insert_counterfactual(store, path_name="wait_30m")
        _write_minute_csv(
            price_root,
            "US",
            "CRDO",
            [
                ("2026-05-19T09:40:00+09:00", 101.0, 103.0, 99.0),
                ("2026-05-19T10:05:00+09:00", 105.0, 106.0, 98.0),
                ("2026-05-19T10:35:00+09:00", 108.0, 110.0, 97.0),
            ],
        )

        first = update_counterfactual_outcomes(db_path=db, market="US", price_root=price_root)
        first_row = store.fetch_rows(market="US")[0]
        first_30m = float(first_row["outcome_30m_pct"])
        assert first["targeted"] == 1
        assert first["partial"] == 1
        assert first_row["status"] == "OUTCOME_PARTIAL"
        assert first_row["outcome_close_pct"] is None
        assert round(first_30m, 6) == 5.0

        _write_minute_csv(
            price_root,
            "US",
            "CRDO",
            [
                ("2026-05-19T10:05:00+09:00", 130.0, 131.0, 129.0),
                ("2026-05-19T10:35:00+09:00", 132.0, 133.0, 128.0),
            ],
        )
        _write_price_csv(price_root, "US", "CRDO", [("2026-05-19", 112.0)])
        retry = update_counterfactual_outcomes(
            db_path=db,
            market="US",
            retry_missing=True,
            price_root=price_root,
        )

        row = store.fetch_rows(market="US")[0]
        assert retry["targeted"] == 1
        assert retry["filled"] == 1
        assert retry["minute_filled"] == 0
        assert row["status"] == "CLOSE_OUTCOME_FILLED"
        assert round(float(row["outcome_close_pct"]), 6) == 12.0
        assert round(float(row["outcome_30m_pct"]), 6) == round(first_30m, 6)
        assert round(float(row["outcome_60m_pct"]), 6) == 8.0
        assert round(float(row["max_runup_60m_pct"]), 6) == 10.0
        assert round(float(row["max_drawdown_60m_pct"]), 6) == -3.0


def test_analyze_counterfactual_paths_reports_close_and_metadata_counts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        store = CandidateCounterfactualStore(db)
        store.upsert_path(
            {
                "runtime_mode": "live",
                "session_date": "2026-05-19",
                "market": "US",
                "ticker": "CRDO",
                "known_at": "2026-05-19T09:35:00+09:00",
                "signal_time": "2026-05-19T09:35:00+09:00",
                "path_name": "immediate",
                "status": "CLOSE_OUTCOME_FILLED",
                "metadata_quality": "runtime_authoritative",
                "label_source": "virtual_immediate_shadow",
                "outcome_close_pct": 2.5,
            }
        )

        payload = analyze_counterfactual_paths(db_path=db, session_date="2026-05-19", market="US")

        assert payload["metadata_quality_counts"]["runtime_authoritative"] == 1
        assert payload["label_source_counts"]["virtual_immediate_shadow"] == 1
        assert payload["by_path"]["US|immediate"]["ret_close"]["n"] == 1
        assert payload["by_path"]["US|immediate"]["ret_close"]["avg_pct"] == 2.5
        markdown = to_markdown(payload)
        assert "| US\\|immediate |" in markdown
        assert "| US|immediate |" not in markdown


def test_lookup_ticker_value_supports_us_uppercase_and_raw_keys() -> None:
    assert _lookup_ticker_value({"AAPL": 1}, market="US", ticker="aapl") == 1
    assert _lookup_ticker_value({"aapl": 2}, market="US", ticker="aapl") == 2
    assert _lookup_ticker_value({"005930": 3}, market="KR", ticker="005930") == 3
