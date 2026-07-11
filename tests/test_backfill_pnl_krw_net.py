from __future__ import annotations

import sqlite3
from pathlib import Path

from tools.backfill_pnl_krw_net import apply_safe_backfill, audit, equity_curve


def _db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE v2_learning_performance (
            closed INTEGER, market TEXT, closed_at TEXT,
            entry_price REAL, qty REAL, pnl_pct_net REAL,
            pnl_krw_net REAL, net_basis TEXT
        )
        """
    )
    con.executemany(
        "INSERT INTO v2_learning_performance VALUES (?,?,?,?,?,?,?,?)",
        [
            (1, "KR", "2026-07-10T15:30:00+09:00", 1000, 10, 1.0, None, "backfilled_exact"),
            (1, "US", "2026-07-10T20:00:00+00:00", 100, 1, 2.0, 10000, "estimated_fixed_order_us"),
            (1, "US", "2026-07-10T20:00:00+00:00", 50, 2, -1.0, -5000, "backfilled_us_from_gross"),
            (1, "US", "2026-07-10T20:00:00+00:00", 20, 3, 1.5, 900, "measured"),
        ],
    )
    con.commit()
    con.close()


def test_safe_apply_repairs_unsafe_us_and_only_backfills_kr(tmp_path: Path) -> None:
    db = tmp_path / "decisions.db"
    backups = tmp_path / "backups"
    _db(db)

    before = audit(db)
    assert before["unsafe_us_rows"] == 2
    assert before["kr_exact_candidates"] == 1

    result = apply_safe_backfill(db, repair_unsafe_us=True, backup_dir=backups)

    assert result["unsafe_us_repaired"] == 2
    assert result["kr_exact_filled"] == 1
    assert Path(result["backup_path"]).exists()
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT market,pnl_krw_net,net_basis FROM v2_learning_performance ORDER BY rowid"
    ).fetchall()
    con.close()
    assert rows[0] == ("KR", 100.0, "backfilled_exact")
    assert rows[1] == ("US", None, "backfilled_fee_only")
    assert rows[2] == ("US", None, "backfilled_fee_only")
    assert rows[3] == ("US", 900.0, "measured")

    curve = equity_curve(db)
    assert curve["estimated_rows_included"] == 0
    assert curve["final_cum_krw"] == 1000


def test_audit_is_read_only(tmp_path: Path) -> None:
    db = tmp_path / "decisions.db"
    _db(db)
    audit(db)
    con = sqlite3.connect(db)
    value = con.execute(
        "SELECT pnl_krw_net FROM v2_learning_performance WHERE net_basis='estimated_fixed_order_us'"
    ).fetchone()[0]
    con.close()
    assert value == 10000
