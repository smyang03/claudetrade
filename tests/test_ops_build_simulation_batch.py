from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from runtime.rehearsal.simulation import load_simulation_batch, run_simulation_suite
from tools import ops_build_simulation_batch


def _write_price(root: Path, market: str, ticker: str, rows: list[tuple[str, float]]) -> None:
    market_key = market.lower()
    path = root / "minute" / market_key / f"{market_key}_{ticker}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "ts,open,high,low,close,volume,source,collected_at\n"
        + "\n".join(f"{ts},{price},{price},{price},{price},100,test,{ts}" for ts, price in rows)
        + "\n",
        encoding="utf-8",
    )


def _write_daily_price(root: Path, market: str, ticker: str, rows: list[tuple[str, float]]) -> None:
    market_key = market.lower()
    path = root / market_key / f"{market_key}_{ticker}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "date,open,high,low,close,volume,source,collected_at\n"
        + "\n".join(f"{ts},{price},{price},{price},{price},100,test,{ts}" for ts, price in rows)
        + "\n",
        encoding="utf-8",
    )


def _event_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE v2_path_runs (
                path_run_id TEXT,
                decision_id TEXT,
                path_type TEXT,
                market TEXT,
                runtime_mode TEXT,
                session_date TEXT,
                ticker TEXT,
                status TEXT,
                plan_json TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        plan = {
            "market": "US",
            "ticker": "NVDA",
            "buy_zone_low": 120.0,
            "buy_zone_high": 125.0,
            "sell_target": 130.0,
            "stop_loss": 118.0,
            "confidence": 0.72,
            "actual_entry_price": 124.0,
            "actual_exit_price": 130.5,
            "entry_qty": 2,
            "close_reason": "CLOSED_CLAUDE_PRICE_TARGET",
            "created_at": "2026-06-01T09:00:00+09:00",
            "closed_at": "2026-06-01T10:00:00+09:00",
        }
        conn.execute(
            """
            INSERT INTO v2_path_runs
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "path_1",
                "dec_1",
                "claude_price",
                "US",
                "live",
                "2026-06-01",
                "NVDA",
                "CLOSED",
                json.dumps(plan),
                "2026-06-01T09:00:00+09:00",
                "2026-06-01T10:00:00+09:00",
            ),
        )


def _candidate_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE candidate_counterfactual_paths (
                id INTEGER PRIMARY KEY,
                runtime_mode TEXT,
                session_date TEXT,
                market TEXT,
                ticker TEXT,
                candidate_key TEXT,
                call_id TEXT,
                signal_time TEXT,
                known_at TEXT,
                trade_ready_action TEXT,
                actual_path TEXT,
                path_name TEXT,
                trigger_time TEXT,
                trigger_price REAL,
                trigger_reason TEXT,
                entry_price REAL,
                entry_delay_min INTEGER,
                outcome_30m_pct REAL,
                outcome_60m_pct REAL,
                outcome_close_pct REAL,
                max_runup_60m_pct REAL,
                max_drawdown_60m_pct REAL,
                status TEXT,
                metadata_json TEXT,
                created_at TEXT,
                updated_at TEXT,
                metadata_quality TEXT,
                label_source TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO candidate_counterfactual_paths (
                runtime_mode, session_date, market, ticker, candidate_key, call_id,
                signal_time, known_at, trade_ready_action, actual_path, path_name,
                trigger_time, trigger_price, trigger_reason, entry_price,
                entry_delay_min, outcome_30m_pct, outcome_60m_pct, outcome_close_pct,
                max_runup_60m_pct, max_drawdown_60m_pct, status, metadata_json,
                created_at, updated_at, metadata_quality, label_source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "live",
                "2026-06-01",
                "KR",
                "005930",
                "cand_1",
                "call_1",
                "2026-06-01T09:00:00+09:00",
                "2026-06-01T09:00:00+09:00",
                "BUY_READY",
                "no_entry",
                "immediate",
                "2026-06-01T09:00:00+09:00",
                30000.0,
                "signal",
                30000.0,
                0,
                2.0,
                6.0,
                8.0,
                9.0,
                -1.0,
                "CLOSE_OUTCOME_FILLED",
                "{}",
                "2026-06-01T09:00:00+09:00",
                "2026-06-01T10:00:00+09:00",
                "ok",
                "test",
            ),
        )


def test_build_simulation_batch_from_read_only_dbs(tmp_path: Path) -> None:
    event_db = tmp_path / "events.db"
    candidate_db = tmp_path / "candidate.db"
    price_root = tmp_path / "price"
    runtime_root = tmp_path / "runtime"
    _event_db(event_db)
    _candidate_db(candidate_db)
    _write_price(
        price_root,
        "US",
        "NVDA",
        [
            ("2026-06-01T09:00:00+09:00", 127.0),
            ("2026-06-01T09:05:00+09:00", 124.0),
            ("2026-06-01T10:00:00+09:00", 130.5),
        ],
    )
    _write_price(
        price_root,
        "KR",
        "005930",
        [
            ("2026-06-01T09:00:00+09:00", 30000.0),
            ("2026-06-01T09:30:00+09:00", 31800.0),
            ("2026-06-01T10:00:00+09:00", 32400.0),
        ],
    )

    report = ops_build_simulation_batch.build_simulation_batch(
        event_db=event_db,
        candidate_db=candidate_db,
        price_root=price_root,
        runtime_root=runtime_root,
        output_dir="batch",
        limit=5,
    )

    assert report["ok"] is True
    assert report["case_count"] == 2
    assert report["live_writes_performed"] is False
    assert report["source_counts"] == {"counterfactual_missed": 1, "pathb_historical": 1}
    batch_path = Path(report["batch_path"])
    assert batch_path.resolve().is_relative_to(runtime_root.resolve())
    assert report["coverage_summary"]["case_count"] == 2
    assert report["coverage_summary"]["by_status"]
    batch_payload = json.loads(batch_path.read_text(encoding="utf-8"))
    coverage = batch_payload["cases"][0]["params"]["price_coverage"]
    assert coverage["matched_rows"] >= 1
    assert coverage["requested_start_at"]
    assert coverage["actual_start_at"]
    cases, overrides, sweep = load_simulation_batch(batch_path)
    assert len(cases) == 2
    assert overrides == {}
    assert sweep == {}
    sim = run_simulation_suite(cases)
    assert sim["summary"]["case_count"] == 2
    assert sim["summary"]["entered_count"] >= 1
    assert sim["summary"]["price_coverage"]["by_status"]


def test_build_simulation_batch_cli_json(tmp_path: Path, capsys) -> None:
    event_db = tmp_path / "events.db"
    candidate_db = tmp_path / "candidate.db"
    price_root = tmp_path / "price"
    runtime_root = tmp_path / "runtime"
    _event_db(event_db)
    _candidate_db(candidate_db)
    _write_price(
        price_root,
        "US",
        "NVDA",
        [
            ("2026-06-01T09:00:00+09:00", 124.0),
            ("2026-06-01T10:00:00+09:00", 130.5),
        ],
    )
    _write_price(
        price_root,
        "KR",
        "005930",
        [
            ("2026-06-01T09:00:00+09:00", 30000.0),
            ("2026-06-01T10:00:00+09:00", 32400.0),
        ],
    )

    rc = ops_build_simulation_batch.main(
        [
            "--event-db",
            str(event_db),
            "--candidate-db",
            str(candidate_db),
            "--price-root",
            str(price_root),
            "--runtime-root",
            str(runtime_root),
            "--output-dir",
            "cli_batch",
            "--limit",
            "3",
            "--sweep",
            "profit_ladder_giveback_pct=1.0,1.5",
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["case_count"] == 2
    batch = json.loads(Path(payload["batch_path"]).read_text(encoding="utf-8"))
    assert batch["sweep"] == {"profit_ladder_giveback_pct": [1.0, 1.5]}
    assert batch["coverage_summary"]["case_count"] == 2


def test_price_tape_uses_daily_fallback_when_minute_window_is_empty(tmp_path: Path) -> None:
    price_root = tmp_path / "price"
    _write_price(
        price_root,
        "US",
        "IBM",
        [
            ("2026-06-04T22:30:00+09:00", 110.0),
        ],
    )
    _write_daily_price(
        price_root,
        "US",
        "IBM",
        [
            ("2026-06-01", 100.0),
            ("2026-06-02", 102.0),
        ],
    )

    tape = ops_build_simulation_batch._read_price_tape(
        price_root=price_root,
        market="US",
        ticker="IBM",
        start_at=ops_build_simulation_batch._parse_dt("2026-06-01T00:00:00+09:00"),
        end_at=ops_build_simulation_batch._parse_dt("2026-06-02T00:00:00+09:00"),
        max_rows=100,
    )

    assert tape is not None
    assert [row["ts"] for row in tape.rows] == ["2026-06-01", "2026-06-02"]
    coverage = tape.coverage
    assert coverage["coverage_status"] == "partial"
    assert coverage["coverage_flags"] == ["daily_fallback_used"]
    price_file = coverage["price_file"].replace("\\", "/")
    price_files = [item.replace("\\", "/") for item in coverage["price_files"]]
    assert price_file.endswith("price/us/us_IBM.csv")
    assert price_files[0].endswith("price/minute/us/us_IBM.csv")
    assert price_files[1].endswith("price/us/us_IBM.csv")


def test_price_tape_merges_minute_and_daily_fallback_rows(tmp_path: Path) -> None:
    price_root = tmp_path / "price"
    _write_price(
        price_root,
        "US",
        "MSFT",
        [
            ("2026-06-01T22:30:00+09:00", 420.0),
            ("2026-06-01T23:00:00+09:00", 421.0),
        ],
    )
    _write_daily_price(
        price_root,
        "US",
        "MSFT",
        [
            ("2026-06-02", 424.0),
            ("2026-06-03", 428.0),
        ],
    )

    tape = ops_build_simulation_batch._read_price_tape(
        price_root=price_root,
        market="US",
        ticker="MSFT",
        start_at=ops_build_simulation_batch._parse_dt("2026-06-01T22:30:00+09:00"),
        end_at=ops_build_simulation_batch._parse_dt("2026-06-03T00:00:00+09:00"),
        max_rows=100,
    )

    assert tape is not None
    assert [row["ts"] for row in tape.rows] == [
        "2026-06-01T22:30:00+09:00",
        "2026-06-01T23:00:00+09:00",
        "2026-06-02",
        "2026-06-03",
    ]
    coverage = tape.coverage
    assert coverage["coverage_status"] == "partial"
    assert coverage["coverage_flags"] == ["daily_fallback_used"]
    assert coverage["price_file"].replace("\\", "/").endswith("price/minute/us/us_MSFT.csv")
    assert coverage["matched_rows"] == 4


def test_us_counterfactual_trigger_after_kst_midnight_uses_trigger_day_end(tmp_path: Path) -> None:
    candidate_db = tmp_path / "candidate.db"
    price_root = tmp_path / "price"
    runtime_root = tmp_path / "runtime"
    _candidate_db(candidate_db)
    with sqlite3.connect(candidate_db) as conn:
        conn.execute(
            """
            INSERT INTO candidate_counterfactual_paths (
                runtime_mode, session_date, market, ticker, candidate_key, call_id,
                signal_time, known_at, trade_ready_action, actual_path, path_name,
                trigger_time, trigger_price, trigger_reason, entry_price,
                entry_delay_min, outcome_30m_pct, outcome_60m_pct, outcome_close_pct,
                max_runup_60m_pct, max_drawdown_60m_pct, status, metadata_json,
                created_at, updated_at, metadata_quality, label_source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "live",
                "2026-06-04",
                "US",
                "AVGO",
                "cand_us_after_midnight",
                "call_us",
                "2026-06-05T03:27:23+09:00",
                "2026-06-05T03:27:23+09:00",
                "BUY_READY",
                "no_entry",
                "immediate",
                "2026-06-05T03:27:23+09:00",
                400.0,
                "signal",
                400.0,
                0,
                1.0,
                2.0,
                3.0,
                4.0,
                -1.0,
                "CLOSE_OUTCOME_FILLED",
                "{}",
                "2026-06-05T03:27:23+09:00",
                "2026-06-05T04:00:00+09:00",
                "ok",
                "test",
            ),
        )
    _write_price(
        price_root,
        "US",
        "AVGO",
        [
            ("2026-06-05T03:28:00+09:00", 400.0),
            ("2026-06-05T03:58:00+09:00", 408.0),
        ],
    )

    report = ops_build_simulation_batch.build_simulation_batch(
        sources="counterfactual",
        market="US",
        candidate_db=candidate_db,
        price_root=price_root,
        runtime_root=runtime_root,
        output_dir="after_midnight",
        limit=5,
    )

    assert report["case_count"] == 1
    assert report["skipped_count"] == 0
    batch = json.loads(Path(report["batch_path"]).read_text(encoding="utf-8"))
    assert batch["cases"][0]["ticker"] == "AVGO"
    coverage = batch["cases"][0]["params"]["price_coverage"]
    assert coverage["requested_end_at"].startswith("2026-06-05")
    assert coverage["matched_rows"] == 2


def test_price_window_missing_is_reported_with_requested_coverage(tmp_path: Path) -> None:
    candidate_db = tmp_path / "candidate.db"
    price_root = tmp_path / "price"
    runtime_root = tmp_path / "runtime"
    _candidate_db(candidate_db)
    with sqlite3.connect(candidate_db) as conn:
        conn.execute(
            """
            INSERT INTO candidate_counterfactual_paths (
                runtime_mode, session_date, market, ticker, candidate_key, call_id,
                signal_time, known_at, trade_ready_action, actual_path, path_name,
                trigger_time, trigger_price, trigger_reason, entry_price,
                entry_delay_min, outcome_30m_pct, outcome_60m_pct, outcome_close_pct,
                max_runup_60m_pct, max_drawdown_60m_pct, status, metadata_json,
                created_at, updated_at, metadata_quality, label_source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "live",
                "2026-06-02",
                "US",
                "AAPL",
                "cand_missing_window",
                "call_missing",
                "2026-06-02T23:00:00+09:00",
                "2026-06-02T23:00:00+09:00",
                "BUY_READY",
                "no_entry",
                "immediate",
                "2026-06-02T23:00:00+09:00",
                200.0,
                "signal",
                200.0,
                0,
                2.0,
                6.0,
                8.0,
                10.0,
                -1.0,
                "CLOSE_OUTCOME_FILLED",
                "{}",
                "2026-06-02T23:00:00+09:00",
                "2026-06-03T01:00:00+09:00",
                "ok",
                "test",
            ),
        )
    _write_price(
        price_root,
        "KR",
        "005930",
        [
            ("2026-06-01T09:00:00+09:00", 30000.0),
            ("2026-06-01T10:00:00+09:00", 32400.0),
        ],
    )
    _write_price(
        price_root,
        "US",
        "AAPL",
        [
            ("2026-06-01T23:00:00+09:00", 200.0),
            ("2026-06-02T01:00:00+09:00", 204.0),
        ],
    )

    report = ops_build_simulation_batch.build_simulation_batch(
        sources="counterfactual",
        candidate_db=candidate_db,
        price_root=price_root,
        runtime_root=runtime_root,
        output_dir="coverage_missing",
        limit=10,
    )

    assert report["case_count"] == 1
    assert report["skipped_count"] == 1
    skipped = report["skipped"][0]
    assert skipped["reason"] == "price_rows_empty_after_filter"
    coverage = skipped["price_coverage"]
    assert coverage["coverage_status"] == "price_rows_empty_after_filter"
    assert coverage["requested_start_at"].startswith("2026-06-02T23:00:00")
    assert coverage["matched_rows"] == 0
