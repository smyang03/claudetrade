"""Safe pnl_krw_net backfill and realized-equity audit.

Canonical write policy:
- KR: qty * entry_price * pnl_pct_net is native-KRW exact enough for backfill.
- US: never write an assumed fixed order size or fee-only approximation into
  pnl_krw_net. Without measured KRW notional/FX, keep the canonical value NULL.

The tool is dry-run by default. Every write creates a SQLite-consistent backup.
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ml" / "decisions.db"
DEFAULT_BACKUP_DIR = ROOT / "state" / "backups"
UNSAFE_US_BASES = ("estimated_fixed_order_us", "backfilled_us_from_gross")


def _backup_database(db: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"{db.name}.bak_{stamp}_pnl_krw_net"
    with sqlite3.connect(db, timeout=30) as source, sqlite3.connect(target) as dest:
        source.backup(dest)
    with sqlite3.connect(target) as check:
        verdict = str(check.execute("PRAGMA integrity_check").fetchone()[0])
    if verdict.lower() != "ok":
        target.unlink(missing_ok=True)
        raise RuntimeError(f"backup integrity failed: {verdict}")
    return target


def audit(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    total = int(con.execute("SELECT COUNT(*) FROM v2_learning_performance WHERE closed=1").fetchone()[0])
    have = int(con.execute(
        "SELECT COUNT(*) FROM v2_learning_performance WHERE closed=1 AND pnl_krw_net IS NOT NULL"
    ).fetchone()[0])
    unsafe = int(con.execute(
        "SELECT COUNT(*) FROM v2_learning_performance WHERE closed=1 AND net_basis IN (?,?)",
        UNSAFE_US_BASES,
    ).fetchone()[0])
    kr_exact_candidates = int(con.execute(
        """
        SELECT COUNT(*) FROM v2_learning_performance
        WHERE closed=1 AND market='KR' AND pnl_krw_net IS NULL
          AND pnl_pct_net IS NOT NULL AND entry_price>0 AND qty>0
        """
    ).fetchone()[0])
    us_blocked = int(con.execute(
        """
        SELECT COUNT(*) FROM v2_learning_performance
        WHERE closed=1 AND market='US' AND pnl_krw_net IS NULL AND pnl_pct_net IS NOT NULL
        """
    ).fetchone()[0])
    con.close()
    return {
        "total_closed": total,
        "canonical_coverage_n": have,
        "canonical_coverage_pct": round(100.0 * have / total, 1) if total else 0.0,
        "unsafe_us_rows": unsafe,
        "kr_exact_candidates": kr_exact_candidates,
        "us_blocked_without_measured_krw_notional_fx": us_blocked,
    }


def apply_safe_backfill(
    db: Path,
    *,
    repair_unsafe_us: bool = False,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> dict:
    backup = _backup_database(db, backup_dir)
    con = sqlite3.connect(db, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    repaired = 0
    filled = 0
    try:
        con.execute("BEGIN IMMEDIATE")
        if repair_unsafe_us:
            repaired = con.execute(
                """
                UPDATE v2_learning_performance
                   SET pnl_krw_net=NULL,
                       net_basis='backfilled_fee_only'
                 WHERE market='US' AND net_basis IN (?,?)
                """,
                UNSAFE_US_BASES,
            ).rowcount
        rows = con.execute(
            """
            SELECT rowid, entry_price, qty, pnl_pct_net, net_basis
            FROM v2_learning_performance
            WHERE closed=1 AND market='KR' AND pnl_krw_net IS NULL
              AND pnl_pct_net IS NOT NULL AND entry_price>0 AND qty>0
            """
        ).fetchall()
        updates = []
        for rowid, entry_price, qty, pnl_pct_net, net_basis in rows:
            value = round(float(qty) * float(entry_price) * float(pnl_pct_net) / 100.0)
            basis = str(net_basis or "backfilled_exact")
            updates.append((value, basis, rowid))
        if updates:
            con.executemany(
                "UPDATE v2_learning_performance SET pnl_krw_net=?, net_basis=? WHERE rowid=?",
                updates,
            )
            filled = len(updates)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {
        "backup_path": str(backup),
        "unsafe_us_repaired": repaired,
        "kr_exact_filled": filled,
        "audit_after": audit(db),
    }


def equity_curve(db: Path) -> dict:
    """Realized curve using canonical non-null rows only; estimates are excluded."""
    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    rows = con.execute(
        """
        SELECT substr(closed_at,1,10), market, pnl_krw_net, net_basis
        FROM v2_learning_performance
        WHERE closed=1 AND pnl_krw_net IS NOT NULL AND closed_at IS NOT NULL
          AND net_basis NOT IN (?,?)
        ORDER BY closed_at
        """,
        UNSAFE_US_BASES,
    ).fetchall()
    coverage = {}
    for market in ("KR", "US"):
        total = int(con.execute(
            "SELECT COUNT(*) FROM v2_learning_performance WHERE closed=1 AND market=?", (market,)
        ).fetchone()[0])
        have = int(con.execute(
            """
            SELECT COUNT(*) FROM v2_learning_performance
            WHERE closed=1 AND market=? AND pnl_krw_net IS NOT NULL
              AND net_basis NOT IN (?,?)
            """,
            (market, *UNSAFE_US_BASES),
        ).fetchone()[0])
        coverage[market] = {"n": have, "total": total, "pct": round(100 * have / total, 1) if total else 0.0}
    con.close()
    daily: dict[str, float] = defaultdict(float)
    bases: Counter[str] = Counter()
    for day, _market, value, basis in rows:
        daily[str(day)] += float(value)
        bases[str(basis or "unknown")] += 1
    cumulative = peak = drawdown = 0.0
    for day in sorted(daily):
        cumulative += daily[day]
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
    return {
        "coverage_by_market": coverage,
        "days": len(daily),
        "final_cum_krw": round(cumulative),
        "max_drawdown_krw": round(drawdown),
        "net_basis_breakdown": dict(bases),
        "estimated_rows_included": 0,
        "label": "canonical_measured_or_exact_only",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="safe pnl_krw_net backfill and audit")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--repair-unsafe-us", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    args = parser.parse_args()
    if args.repair_unsafe_us and not args.apply:
        parser.error("--repair-unsafe-us requires --apply")
    print("=== audit before ===")
    print(audit(args.db))
    if args.apply:
        print("=== write result ===")
        print(apply_safe_backfill(
            args.db,
            repair_unsafe_us=args.repair_unsafe_us,
            backup_dir=args.backup_dir,
        ))
    print("=== canonical realized equity curve ===")
    print(equity_curve(args.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
