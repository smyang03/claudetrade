from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

import json

from tools.profit_strategy_materializer import (
    CORE_LIVE_AUTHORITY,
    CORE_SOURCE_AUTHORITY,
    consensus_signals,
    materialize_core_live_manifest,
    sector_pulse_signals,
)


def _selection_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE ticker_selection_log(
        id INTEGER PRIMARY KEY, date TEXT, ticker TEXT, market TEXT, bot_mode TEXT,
        signal_fired INTEGER, strategy_name TEXT, recommended_strategy TEXT,
        selection_rank INTEGER, entry_priority_score REAL, change_pct REAL)"""
    )
    con.executemany(
        "INSERT INTO ticker_selection_log VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "2026-07-14", "AAPL", "US", "live", 1, "breakout", "breakout", 2, 0.8, 2.0),
            (2, "2026-07-14", "AAPL", "US", "live", 1, "breakout", "breakout", 1, 0.9, 2.1),
            (3, "2026-07-14", "MSFT", "US", "live", 1, "momentum", "pullback", 1, 1.0, 3.0),
            (4, "2026-07-14", "NVDA", "US", "paper", 1, "breakout", "breakout", 1, 2.0, 5.0),
        ],
    )
    con.commit()
    con.close()


def test_consensus_requires_exact_agreement_and_deduplicates(tmp_path: Path) -> None:
    db = tmp_path / "selection.db"
    _selection_db(db)
    rows = consensus_signals(db, session_date="2026-07-15")
    assert [row["ticker"] for row in rows] == ["AAPL"]
    assert rows[0]["strategy_id"] == "US_CONSENSUS_3D_V1"
    assert rows[0]["hold_sessions"] == 3


def test_consensus_rejects_stale_source_session(tmp_path: Path) -> None:
    db = tmp_path / "selection.db"
    _selection_db(db)
    assert consensus_signals(db, session_date="2026-07-20") == []


def test_sector_pulse_uses_completed_closes_and_strongest_leader() -> None:
    values = {
        "SOXX": [100.0, 103.0],
        "XLV": [100.0, 104.5],
        "XLF": [100.0, 101.0],
        "ITA": [100.0, 102.5],
        "LIT": [100.0, 99.0],
    }

    def loader(symbol: str) -> pd.Series:
        return pd.Series(values[symbol], index=pd.to_datetime(["2026-07-13", "2026-07-14"]))

    rows = sector_pulse_signals(session_date="2026-07-15", close_loader=loader)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "227550"
    assert rows[0]["evidence"]["us_leader"] == "XLV"
    assert rows[0]["known_at"] < "2026-07-15"


def test_sector_pulse_rejects_subthreshold_move() -> None:
    def loader(_: str) -> pd.Series:
        return pd.Series([100.0, 101.0], index=pd.to_datetime(["2026-07-13", "2026-07-14"]))

    assert sector_pulse_signals(session_date="2026-07-15", close_loader=loader) == []


def _core_source(path: Path, *, authority: str = CORE_SOURCE_AUTHORITY) -> None:
    path.write_text(json.dumps({
        "schema_version": "core_shadow_targets_v1",
        "authority": authority,
        "as_of": "2026-07-15",
        "signal_month": "2026-06",
        "effective_month": "2026-07",
        "arms": [{
            "strategy_id": "US_SCHG_BIL_TREND_V1",
            "market": "US",
            "role": "primary",
            "weights": {"SCHG": 1.0},
        }],
    }), encoding="utf-8")


def _live_env() -> dict[str, str]:
    return {
        "PROFIT_STRATEGY_AUTHORITY_MODE": "micro",
        "PROFIT_STRATEGY_ORDER_HANDOFF_ENABLED": "true",
        "PROFIT_STRATEGY_ORDER_SUBMIT_ENABLED": "true",
        "PROFIT_STRATEGY_KILL_SWITCH": "false",
        "PROFIT_STRATEGY_ORDER_LIVE_ACK": "I_ACCEPT_LIVE_PROFIT_STRATEGIES",
        "PROFIT_STRATEGY_ENABLED_IDS": "US_SCHG_BIL_TREND_V1,KR_FACTOR_TREND_V1",
    }


def test_core_live_manifest_is_explicit_hashed_promotion(tmp_path: Path) -> None:
    source = tmp_path / "core.json"
    output = tmp_path / "manifest.json"
    _core_source(source)

    payload = materialize_core_live_manifest(
        market="US",
        session_date="2026-07-15",
        output_path=output,
        source_path=source,
        env=_live_env(),
    )

    assert payload["status"] == "healthy"
    assert payload["authority"] == CORE_LIVE_AUTHORITY
    assert payload["source_authority"] == CORE_SOURCE_AUTHORITY
    assert len(payload["source_sha256"]) == 64
    assert payload["signals"][0]["ticker"] == "SCHG"
    assert json.loads(output.read_text(encoding="utf-8"))["source_sha256"] == payload["source_sha256"]


def test_core_live_manifest_fails_closed_without_operator_contract(tmp_path: Path) -> None:
    source = tmp_path / "core.json"
    output = tmp_path / "manifest.json"
    _core_source(source)
    env = _live_env()
    env["PROFIT_STRATEGY_ORDER_LIVE_ACK"] = ""

    payload = materialize_core_live_manifest(
        market="US",
        session_date="2026-07-15",
        output_path=output,
        source_path=source,
        env=env,
    )

    assert payload["status"] == "blocked"
    assert payload["authority"] == "NO_LIVE_AUTHORITY"
    assert payload["signals"] == []
    assert "operator_live_ack_missing" in payload["errors"]
