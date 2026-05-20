from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(data: Any) -> str:
    return json.dumps(data or {}, ensure_ascii=False, sort_keys=True, default=str)


EXTRA_COUNTERFACTUAL_COLUMNS: dict[str, str] = {
    "metadata_quality": "TEXT",
    "label_source": "TEXT",
}

ACCUMULATION_METADATA_KEYS = frozenset({"label_horizons", "source_attempts"})
PRESERVE_OUTCOME_METADATA_KEYS = frozenset(
    {
        "label_horizons",
        "source_attempts",
        "final_attempt_at",
        "outcome_source",
        "price_source",
        "outcome_update_source",
    }
)
def _decode_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if item not in (None, "")]
    return [value]


def merge_metadata(
    existing: Any,
    patch: Any,
    *,
    force_keys: Iterable[str] = (),
    accumulation_keys: Iterable[str] = ACCUMULATION_METADATA_KEYS,
) -> dict[str, Any]:
    existing_obj = _decode_json_object(existing)
    patch_obj = _decode_json_object(patch)
    merged = dict(existing_obj)
    for key, value in patch_obj.items():
        merged.setdefault(key, value)
    for key in force_keys:
        if key in patch_obj:
            merged[key] = patch_obj[key]
    for key in accumulation_keys:
        values = list(_as_list(existing_obj.get(key)))
        seen = {str(item) for item in values}
        for item in _as_list(patch_obj.get(key)):
            item_key = str(item)
            if item_key in seen:
                continue
            values.append(item)
            seen.add(item_key)
        if values:
            merged[key] = sorted(values, key=str)
    return merged


def merge_metadata_json(
    existing: Any,
    patch: Any,
    *,
    force_keys: Iterable[str] = (),
    accumulation_keys: Iterable[str] = ACCUMULATION_METADATA_KEYS,
) -> str:
    return _json(
        merge_metadata(
            existing,
            patch,
            force_keys=force_keys,
            accumulation_keys=accumulation_keys,
        )
    )


def merge_metadata_for_upsert(
    existing: Any,
    incoming: Any,
    *,
    existing_quality: str = "",
    incoming_quality: str = "",
) -> str:
    existing_obj = _decode_json_object(existing)
    incoming_obj = _decode_json_object(incoming)
    existing_runtime = str(existing_quality or "") == "runtime_authoritative"
    incoming_runtime = str(incoming_quality or "") == "runtime_authoritative"
    if existing_runtime and not incoming_runtime:
        merged = dict(incoming_obj)
        merged.update(existing_obj)
    else:
        merged = dict(existing_obj)
        merged.update(incoming_obj)
    for key in ACCUMULATION_METADATA_KEYS:
        values = list(_as_list(existing_obj.get(key)))
        seen = {str(item) for item in values}
        for item in _as_list(incoming_obj.get(key)):
            item_key = str(item)
            if item_key in seen:
                continue
            values.append(item)
            seen.add(item_key)
        if values:
            merged[key] = sorted(values, key=str)
    for key in PRESERVE_OUTCOME_METADATA_KEYS - ACCUMULATION_METADATA_KEYS:
        if key in existing_obj:
            merged[key] = existing_obj[key]
    return _json(merged)


def merge_metadata_for_outcome(
    existing: Any,
    patch: dict[str, Any],
    *,
    force_keys: set[str] | None = None,
) -> str:
    return merge_metadata_json(
        existing,
        patch,
        force_keys=force_keys or set(),
        accumulation_keys=ACCUMULATION_METADATA_KEYS,
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = _table_columns(conn, table)
    for name, column_type in columns.items():
        if name in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


class CandidateCounterfactualStore:
    def __init__(self, path: str | Path, *, timeout: float = 5.0) -> None:
        self.path = Path(path)
        self.timeout = float(timeout or 5.0)
        self.last_upsert_errors: list[dict[str, Any]] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def init(self) -> None:
        with closing(self.connect()) as conn:
            with conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS candidate_counterfactual_paths (
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
                      metadata_quality TEXT,
                      label_source TEXT,
                      metadata_json TEXT DEFAULT '{}',
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    );

                    CREATE UNIQUE INDEX IF NOT EXISTS idx_counterfactual_path_unique
                    ON candidate_counterfactual_paths (
                      runtime_mode,
                      session_date,
                      market,
                      ticker,
                      known_at,
                      path_name
                    );

                    CREATE INDEX IF NOT EXISTS idx_counterfactual_path_lookup
                    ON candidate_counterfactual_paths (
                      session_date,
                      market,
                      ticker,
                      status
                    );
                    """
                )
                _ensure_columns(conn, "candidate_counterfactual_paths", EXTRA_COUNTERFACTUAL_COLUMNS)
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_counterfactual_quality
                    ON candidate_counterfactual_paths (
                      metadata_quality,
                      label_source,
                      session_date,
                      market,
                      status
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_counterfactual_metadata_quality
                    ON candidate_counterfactual_paths (
                      metadata_quality,
                      label_source,
                      session_date
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_counterfactual_promotion_lookup
                    ON candidate_counterfactual_paths (
                      metadata_quality,
                      market,
                      path_name,
                      session_date
                    )
                    """
                )

    def _row_payload(self, row: dict[str, Any], now: str) -> dict[str, Any]:
        metadata = _decode_json_object(row.get("metadata"))
        metadata_json = (
            row.get("metadata_json")
            if isinstance(row.get("metadata_json"), str)
            else _json(metadata)
        )
        return {
            "runtime_mode": str(row.get("runtime_mode") or "live"),
            "session_date": str(row.get("session_date") or ""),
            "market": str(row.get("market") or "").upper(),
            "ticker": str(row.get("ticker") or "").strip().upper()
            if str(row.get("market") or "").upper() == "US"
            else str(row.get("ticker") or "").strip(),
            "candidate_key": row.get("candidate_key"),
            "call_id": row.get("call_id"),
            "signal_time": str(row.get("signal_time") or row.get("known_at") or now),
            "known_at": str(row.get("known_at") or now),
            "trade_ready_action": row.get("trade_ready_action"),
            "actual_path": row.get("actual_path"),
            "path_name": str(row.get("path_name") or "immediate"),
            "trigger_time": row.get("trigger_time"),
            "trigger_price": row.get("trigger_price"),
            "trigger_reason": row.get("trigger_reason"),
            "entry_price": row.get("entry_price"),
            "entry_delay_min": row.get("entry_delay_min"),
            "outcome_30m_pct": row.get("outcome_30m_pct"),
            "outcome_60m_pct": row.get("outcome_60m_pct"),
            "outcome_close_pct": row.get("outcome_close_pct"),
            "max_runup_60m_pct": row.get("max_runup_60m_pct"),
            "max_drawdown_60m_pct": row.get("max_drawdown_60m_pct"),
            "status": str(row.get("status") or "PENDING"),
            "metadata_quality": row.get("metadata_quality") or metadata.get("metadata_quality"),
            "label_source": row.get("label_source") or metadata.get("label_source"),
            "metadata_json": metadata_json,
        }

    @staticmethod
    def _existing_by_unique_key(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT metadata_json, metadata_quality, label_source
            FROM candidate_counterfactual_paths
            WHERE runtime_mode=?
              AND session_date=?
              AND market=?
              AND ticker=?
              AND known_at=?
              AND path_name=?
            """,
            (
                payload["runtime_mode"],
                payload["session_date"],
                payload["market"],
                payload["ticker"],
                payload["known_at"],
                payload["path_name"],
            ),
        ).fetchone()
        return dict(row) if row else {}

    def _upsert_path_conn(self, conn: sqlite3.Connection, row: dict[str, Any]) -> None:
        now = _now()
        payload = self._row_payload(row, now)
        existing = self._existing_by_unique_key(conn, payload)
        payload["metadata_json"] = merge_metadata_for_upsert(
            existing.get("metadata_json"),
            payload.get("metadata_json"),
            existing_quality=str(existing.get("metadata_quality") or ""),
            incoming_quality=str(payload.get("metadata_quality") or ""),
        )
        conn.execute(
            """
            INSERT INTO candidate_counterfactual_paths (
              runtime_mode, session_date, market, ticker, candidate_key, call_id,
              signal_time, known_at, trade_ready_action, actual_path, path_name,
              trigger_time, trigger_price, trigger_reason, entry_price, entry_delay_min,
              outcome_30m_pct, outcome_60m_pct, outcome_close_pct,
              max_runup_60m_pct, max_drawdown_60m_pct, status, metadata_quality,
              label_source, metadata_json,
              created_at, updated_at
            )
            VALUES (
              :runtime_mode, :session_date, :market, :ticker, :candidate_key, :call_id,
              :signal_time, :known_at, :trade_ready_action, :actual_path, :path_name,
              :trigger_time, :trigger_price, :trigger_reason, :entry_price, :entry_delay_min,
              :outcome_30m_pct, :outcome_60m_pct, :outcome_close_pct,
              :max_runup_60m_pct, :max_drawdown_60m_pct, :status, :metadata_quality,
              :label_source, :metadata_json,
              :created_at, :updated_at
            )
            ON CONFLICT(runtime_mode, session_date, market, ticker, known_at, path_name)
            DO UPDATE SET
              candidate_key=excluded.candidate_key,
              call_id=excluded.call_id,
              trade_ready_action=excluded.trade_ready_action,
              actual_path=excluded.actual_path,
              trigger_time=COALESCE(excluded.trigger_time, candidate_counterfactual_paths.trigger_time),
              trigger_price=COALESCE(excluded.trigger_price, candidate_counterfactual_paths.trigger_price),
              trigger_reason=COALESCE(excluded.trigger_reason, candidate_counterfactual_paths.trigger_reason),
              entry_price=COALESCE(excluded.entry_price, candidate_counterfactual_paths.entry_price),
              entry_delay_min=COALESCE(excluded.entry_delay_min, candidate_counterfactual_paths.entry_delay_min),
              outcome_30m_pct=COALESCE(excluded.outcome_30m_pct, candidate_counterfactual_paths.outcome_30m_pct),
              outcome_60m_pct=COALESCE(excluded.outcome_60m_pct, candidate_counterfactual_paths.outcome_60m_pct),
              outcome_close_pct=COALESCE(excluded.outcome_close_pct, candidate_counterfactual_paths.outcome_close_pct),
              max_runup_60m_pct=COALESCE(excluded.max_runup_60m_pct, candidate_counterfactual_paths.max_runup_60m_pct),
              max_drawdown_60m_pct=COALESCE(excluded.max_drawdown_60m_pct, candidate_counterfactual_paths.max_drawdown_60m_pct),
              status=CASE
                WHEN candidate_counterfactual_paths.status IN (
                  'CLOSE_OUTCOME_FILLED',
                  'OUTCOME_FILLED',
                  'OUTCOME_PARTIAL',
                  'PRICE_PENDING',
                  'PRICE_UNAVAILABLE',
                  'DATA_MISSING'
                )
                  THEN candidate_counterfactual_paths.status
                ELSE excluded.status
              END,
              metadata_quality=CASE
                WHEN candidate_counterfactual_paths.metadata_quality = 'runtime_authoritative'
                  THEN candidate_counterfactual_paths.metadata_quality
                WHEN excluded.metadata_quality = 'runtime_authoritative'
                  THEN excluded.metadata_quality
                ELSE COALESCE(candidate_counterfactual_paths.metadata_quality, excluded.metadata_quality)
              END,
              label_source=CASE
                WHEN candidate_counterfactual_paths.label_source LIKE 'actual_%'
                  THEN candidate_counterfactual_paths.label_source
                WHEN excluded.label_source LIKE 'actual_%'
                  THEN excluded.label_source
                ELSE COALESCE(candidate_counterfactual_paths.label_source, excluded.label_source)
              END,
              metadata_json=:metadata_json,
              updated_at=excluded.updated_at
            """,
            {**payload, "created_at": now, "updated_at": now},
        )

    def upsert_path(self, row: dict[str, Any]) -> None:
        with closing(self.connect()) as conn:
            with conn:
                self._upsert_path_conn(conn, row)

    def upsert_paths(self, rows: Iterable[dict[str, Any]]) -> int:
        count = 0
        errors: list[dict[str, Any]] = []
        with closing(self.connect()) as conn:
            with conn:
                for index, row in enumerate(rows):
                    try:
                        self._upsert_path_conn(conn, row)
                        count += 1
                    except Exception as exc:
                        errors.append(
                            {
                                "index": index,
                                "ticker": str((row or {}).get("ticker") or ""),
                                "path_name": str((row or {}).get("path_name") or ""),
                                "error": str(exc),
                            }
                        )
        self.last_upsert_errors = errors
        return count

    def fetch_rows(self, *, session_date: str = "", market: str = "", status: str = "") -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if session_date:
            where.append("session_date=?")
            params.append(session_date)
        if market:
            where.append("market=?")
            params.append(str(market).upper())
        if status:
            where.append("status=?")
            params.append(status)
        sql = "SELECT * FROM candidate_counterfactual_paths"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY session_date, market, ticker, known_at, path_name"
        with closing(self.connect()) as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def mark_outcome(self, row_id: int, **updates: Any) -> None:
        if not updates:
            return
        allowed = {
            "outcome_30m_pct",
            "outcome_60m_pct",
            "outcome_close_pct",
            "max_runup_60m_pct",
            "max_drawdown_60m_pct",
            "status",
            "metadata_quality",
            "label_source",
            "metadata_json",
        }
        items = {k: v for k, v in updates.items() if k in allowed}
        if not items:
            return
        items["updated_at"] = _now()
        assignments = []
        for key in items:
            if key == "metadata_quality":
                assignments.append(f"{key}=COALESCE({key}, :{key})")
            elif key == "label_source":
                assignments.append(
                    """
                    label_source=CASE
                      WHEN label_source LIKE 'actual_%' THEN label_source
                      WHEN :label_source LIKE 'actual_%' THEN :label_source
                      WHEN :label_source = 'virtual_immediate_shadow'
                           AND COALESCE(label_source, '') IN ('', 'counterfactual_outcome_updater')
                        THEN :label_source
                      ELSE COALESCE(label_source, :label_source)
                    END
                    """.strip()
                )
            else:
                assignments.append(f"{key}=:{key}")
        set_sql = ", ".join(assignments)
        with closing(self.connect()) as conn:
            with conn:
                conn.execute(
                    f"UPDATE candidate_counterfactual_paths SET {set_sql} WHERE id=:id",
                    {**items, "id": int(row_id)},
                )

    def promotion_eligible_groups(
        self,
        *,
        market: str = "",
        min_sessions: int = 10,
    ) -> list[dict[str, Any]]:
        where = [
            "metadata_quality='runtime_authoritative'",
            "label_source='virtual_immediate_shadow'",
            "outcome_close_pct IS NOT NULL",
        ]
        params: list[Any] = []
        if market:
            where.append("market=?")
            params.append(str(market).upper())
        params.append(max(1, int(min_sessions or 10)))
        sql = f"""
            SELECT market,
                   json_extract(metadata_json, '$.recommended_strategy') AS recommended_strategy,
                   json_extract(metadata_json, '$.mode_family') AS mode_family,
                   COUNT(*) AS rows,
                   COUNT(DISTINCT session_date) AS distinct_sessions
            FROM candidate_counterfactual_paths
            WHERE {" AND ".join(where)}
            GROUP BY market, recommended_strategy, mode_family
            HAVING COUNT(DISTINCT session_date) >= ?
            ORDER BY market, recommended_strategy, mode_family
        """
        with closing(self.connect()) as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
