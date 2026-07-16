from __future__ import annotations

import sqlite3

from tools.us_swing_preflight import (
    _active_execution_contract,
    _expected_maturity_session,
)


def test_expected_maturity_session_uses_inclusive_us_trading_sessions() -> None:
    assert _expected_maturity_session("2026-07-10", 5) == "2026-07-16"


def test_active_execution_contract_reports_expected_release_without_stale_label() -> None:
    con = sqlite3.connect(":memory:")
    con.execute(
        """
        CREATE TABLE signals (
            signal_date TEXT,
            ticker TEXT,
            entry_date TEXT,
            execution_shadow_entry_fill_usd REAL,
            execution_shadow_qty INTEGER,
            execution_shadow_reason TEXT,
            execution_shadow_eligible INTEGER,
            execution_shadow_net_krw_pct REAL
        )
        """
    )
    con.execute(
        """
        INSERT INTO signals VALUES (
            '2026-07-10','SMCI','2026-07-10',28.42,1,
            'entry_open_whole_share_confirmed',1,NULL
        )
        """
    )

    active = _active_execution_contract(con, max_hold_sessions=5)

    assert active["state"] == "ACTIVE_CONTRACT"
    assert active["active_count"] == 1
    assert active["rows"][0]["expected_maturity_session"] == "2026-07-16"
