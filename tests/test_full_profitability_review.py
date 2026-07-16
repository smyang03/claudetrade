from __future__ import annotations

import sqlite3
from pathlib import Path

from tools.full_profitability_review import (
    closed_trade_payload,
    load_canonical_closed,
    selection_payload,
)


def test_daily_caps_prefer_canonical_net_truth(tmp_path: Path) -> None:
    db_path = tmp_path / "decisions.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE v2_canonical_performance (
            v2_decision_id TEXT PRIMARY KEY,
            market TEXT,
            runtime_mode TEXT,
            session_date TEXT,
            ticker TEXT,
            strategy TEXT,
            filled INTEGER,
            closed INTEGER,
            portfolio_realized INTEGER,
            earliest_fill_at TEXT,
            pnl_pct REAL,
            pnl_pct_net REAL
        );
        INSERT INTO v2_canonical_performance VALUES
          ('a','US','live','2026-07-01','AAA','pathb',1,1,1,'2026-07-01T22:35:00+09:00',5.0,-1.0),
          ('b','US','live','2026-07-01','BBB','pathb',1,1,1,'2026-07-01T22:36:00+09:00',5.0,2.0),
          ('c','KR','live','2026-07-01','000001','pathb',1,1,1,'2026-07-01T09:05:00+09:00',5.0,-3.0),
          ('d','US','paper','2026-07-01','PAPER','pathb',1,1,1,'2026-07-01T22:34:00+09:00',99.0,99.0),
          ('e','US','live','2026-07-01','SHADOW','pathb',1,1,0,'2026-07-01T22:34:00+09:00',99.0,99.0);
        """
    )
    conn.commit()
    conn.close()

    canonical = load_canonical_closed(db_path)
    payload = selection_payload(
        [
            {
                "bot_mode": "live",
                "traded": 1,
                "pnl_pct": 10.0,
                "date": "2026-07-01",
                "market": "US",
                "traded_at": "2026-07-01T22:30:00+09:00",
            }
        ],
        canonical_closed=canonical,
    )

    assert len(canonical) == 3
    assert payload["daily_caps_basis"] == "v2_canonical_performance_net"
    assert payload["daily_caps"]["per_market_cap_1"]["sum_pct"] == -4.0
    assert payload["daily_caps"]["per_market_cap_1"]["n"] == 2


def test_daily_caps_keep_legacy_fallback_when_canonical_missing() -> None:
    legacy = [
        {
            "bot_mode": "live",
            "traded": 1,
            "pnl_pct": 1.5,
            "date": "2026-07-01",
            "market": "US",
            "traded_at": "2026-07-01T22:35:00+09:00",
        }
    ]

    payload = selection_payload(legacy, canonical_closed=[])

    assert payload["daily_caps_basis"] == "ticker_selection_log_legacy"
    assert payload["daily_caps"]["per_market_cap_1"]["sum_pct"] == 1.5


def test_closed_trade_headline_uses_canonical_net_truth() -> None:
    payload = closed_trade_payload(
        [{"market": "US", "pnl_pct": 10.0, "strategy": "operational"}],
        canonical_closed=[
            {"market": "US", "pnl_pct": -2.0, "strategy": "pathb"},
            {"market": "KR", "pnl_pct": 1.0, "strategy": "pathb"},
        ],
    )

    assert payload["profitability_truth_basis"] == "v2_canonical_performance_net"
    assert payload["canonical_by_market"]["US"]["sum_pct"] == -2.0
    assert payload["canonical_by_market"]["KR"]["sum_pct"] == 1.0
    assert payload["by_market"]["US"]["sum_pct"] == 10.0
