from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path


def _db_path(mode: str) -> Path:
    return get_runtime_path("data", "audit", f"{str(mode or 'live').lower()}_shadow_audit.db")


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in conn.execute(sql, params)]
    except Exception:
        return []


def _md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._\n"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        vals = [str(row.get(col, "")).replace("\n", " ")[:220] for col in columns]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def build_report(path: Path, *, date: str, market: str) -> str:
    lines: list[str] = [f"# Shadow Audit Report - {date} {market}", ""]
    if not path.exists():
        lines.append(f"Audit DB not found: `{path}`")
        return "\n".join(lines)

    conn = sqlite3.connect(str(path), timeout=5)
    conn.row_factory = sqlite3.Row
    market_key = str(market or "").upper()
    params = (date, market_key)
    try:
        lines.append("## Signals")
        lines.append("")
        lines.append(
            _md_table(
                _rows(
                    conn,
                    """
                    SELECT decision, COALESCE(block_reason, '') AS block_reason, COUNT(*) AS rows,
                           ROUND(AVG(score), 4) AS avg_score
                    FROM audit_signals
                    WHERE session_date=? AND market=?
                    GROUP BY decision, COALESCE(block_reason, '')
                    ORDER BY rows DESC
                    """,
                    params,
                ),
                ["decision", "block_reason", "rows", "avg_score"],
            )
        )

        lines.append("## ORDER_UNKNOWN Episodes")
        lines.append("")
        lines.append(
            _md_table(
                _rows(
                    conn,
                    """
                    SELECT episode_id, scope, ticker, started_at, ended_at, status,
                           start_reason, clear_reason
                    FROM audit_episodes
                    WHERE session_date=? AND market=? AND episode_type LIKE 'ORDER_UNKNOWN%'
                    ORDER BY started_at
                    """,
                    params,
                ),
                ["episode_id", "scope", "ticker", "started_at", "ended_at", "status", "start_reason", "clear_reason"],
            )
        )

        lines.append("## Blocked Signals")
        lines.append("")
        lines.append(
            _md_table(
                _rows(
                    conn,
                    """
                    SELECT signal_id, ticker, strategy, signal_at, signal_price, score, block_reason
                    FROM audit_signals
                    WHERE session_date=? AND market=? AND COALESCE(block_reason, '')!=''
                    ORDER BY signal_at
                    LIMIT 100
                    """,
                    params,
                ),
                ["signal_id", "ticker", "strategy", "signal_at", "signal_price", "score", "block_reason"],
            )
        )

        lines.append("## Outcomes")
        lines.append("")
        lines.append(
            _md_table(
                _rows(
                    conn,
                    """
                    SELECT s.decision, COALESCE(s.block_reason, '') AS block_reason,
                           o.horizon_min, o.status, COUNT(*) AS rows,
                           ROUND(AVG(o.return_pct), 4) AS avg_return_pct,
                           ROUND(AVG(o.max_runup_pct), 4) AS avg_runup_pct,
                           ROUND(AVG(o.max_drawdown_pct), 4) AS avg_drawdown_pct
                    FROM audit_signal_outcomes o
                    JOIN audit_signals s ON s.signal_id=o.signal_id
                    WHERE s.session_date=? AND s.market=?
                    GROUP BY s.decision, COALESCE(s.block_reason, ''), o.horizon_min, o.status
                    ORDER BY o.horizon_min, rows DESC
                    """,
                    params,
                ),
                [
                    "decision",
                    "block_reason",
                    "horizon_min",
                    "status",
                    "rows",
                    "avg_return_pct",
                    "avg_runup_pct",
                    "avg_drawdown_pct",
                ],
            )
        )

        lines.append("## Score Buckets")
        lines.append("")
        lines.append(
            _md_table(
                _rows(
                    conn,
                    """
                    SELECT
                        CASE
                            WHEN score >= 4 THEN 'score>=4'
                            WHEN score >= 2 THEN '2<=score<4'
                            WHEN score > 0 THEN '0<score<2'
                            ELSE 'score_missing'
                        END AS score_bucket,
                        decision,
                        COALESCE(block_reason, '') AS block_reason,
                        COUNT(*) AS rows
                    FROM audit_signals
                    WHERE session_date=? AND market=?
                    GROUP BY score_bucket, decision, COALESCE(block_reason, '')
                    ORDER BY score_bucket, rows DESC
                    """,
                    params,
                ),
                ["score_bucket", "decision", "block_reason", "rows"],
            )
        )

        lines.append("## Time Buckets")
        lines.append("")
        lines.append(
            _md_table(
                _rows(
                    conn,
                    """
                    SELECT SUBSTR(signal_at, 12, 2) || ':00' AS hour_bucket,
                           decision,
                           COALESCE(block_reason, '') AS block_reason,
                           COUNT(*) AS rows,
                           ROUND(AVG(score), 4) AS avg_score
                    FROM audit_signals
                    WHERE session_date=? AND market=? AND COALESCE(signal_at, '')!=''
                    GROUP BY hour_bucket, decision, COALESCE(block_reason, '')
                    ORDER BY hour_bucket, rows DESC
                    """,
                    params,
                ),
                ["hour_bucket", "decision", "block_reason", "rows", "avg_score"],
            )
        )

        lines.append("## Path A vs PathB")
        lines.append("")
        lines.append(
            _md_table(
                _rows(
                    conn,
                    """
                    SELECT COALESCE(path_type, '') AS path_type,
                           COALESCE(source, '') AS source,
                           decision,
                           COALESCE(block_reason, '') AS block_reason,
                           COUNT(*) AS rows
                    FROM audit_signals
                    WHERE session_date=? AND market=?
                    GROUP BY COALESCE(path_type, ''), COALESCE(source, ''), decision, COALESCE(block_reason, '')
                    ORDER BY path_type, source, rows DESC
                    """,
                    params,
                ),
                ["path_type", "source", "decision", "block_reason", "rows"],
            )
        )

        lines.append("## Missing Price Ratio")
        lines.append("")
        lines.append(
            _md_table(
                _rows(
                    conn,
                    """
                    SELECT horizon_min,
                           COUNT(*) AS rows,
                           SUM(CASE WHEN status='missing_price' THEN 1 ELSE 0 END) AS missing_rows,
                           ROUND(100.0 * SUM(CASE WHEN status='missing_price' THEN 1 ELSE 0 END) / COUNT(*), 2) AS missing_pct
                    FROM audit_signal_outcomes o
                    JOIN audit_signals s ON s.signal_id=o.signal_id
                    WHERE s.session_date=? AND s.market=?
                    GROUP BY horizon_min
                    ORDER BY horizon_min
                    """,
                    params,
                ),
                ["horizon_min", "rows", "missing_rows", "missing_pct"],
            )
        )

        lines.append("## Writer Health")
        lines.append("")
        lines.append(
            _md_table(
                _rows(
                    conn,
                    """
                    SELECT ts, event_type, queued, written, dropped, error_count, queue_size, last_error
                    FROM audit_writer_health
                    ORDER BY id DESC
                    LIMIT 20
                    """,
                ),
                ["ts", "event_type", "queued", "written", "dropped", "error_count", "queue_size", "last_error"],
            )
        )
    finally:
        conn.close()
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a shadow audit markdown report.")
    parser.add_argument("--date", required=True, help="session date YYYY-MM-DD")
    parser.add_argument("--market", required=True, choices=["KR", "US", "kr", "us"])
    parser.add_argument("--mode", default="live", choices=["live", "paper"])
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    path = _db_path(args.mode)
    report = build_report(path, date=args.date, market=args.market)
    if args.output:
        out = Path(args.output)
    else:
        out = ROOT / "docs" / "reports" / f"shadow_audit_report_{args.mode}_{args.date.replace('-', '')}_{args.market.upper()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
