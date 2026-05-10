from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "external_market_data.sqlite"
DATA_TABLES = [
    "dart_disclosures",
    "public_krx_listed",
    "public_stock_quotes",
    "public_securities_products",
    "fred_observations",
]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class ExternalDataStore:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH, timeout: float = 10.0) -> None:
        self.path = Path(path)
        self.timeout = timeout

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS external_api_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    http_status INTEGER,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    fields_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT NOT NULL DEFAULT '',
                    fetched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dart_disclosures (
                    rcept_no TEXT PRIMARY KEY,
                    stock_code TEXT NOT NULL DEFAULT '',
                    corp_code TEXT NOT NULL,
                    corp_name TEXT NOT NULL DEFAULT '',
                    report_name TEXT NOT NULL DEFAULT '',
                    rcept_dt TEXT NOT NULL DEFAULT '',
                    risk_level TEXT NOT NULL DEFAULT 'unknown',
                    risk_tags_json TEXT NOT NULL DEFAULT '[]',
                    url TEXT NOT NULL DEFAULT '',
                    fetched_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS public_krx_listed (
                    base_date TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    isin_code TEXT NOT NULL DEFAULT '',
                    market TEXT NOT NULL DEFAULT '',
                    item_name TEXT NOT NULL DEFAULT '',
                    corp_name TEXT NOT NULL DEFAULT '',
                    corp_reg_no TEXT NOT NULL DEFAULT '',
                    fetched_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (base_date, stock_code)
                );

                CREATE TABLE IF NOT EXISTS public_stock_quotes (
                    base_date TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    isin_code TEXT NOT NULL DEFAULT '',
                    market TEXT NOT NULL DEFAULT '',
                    item_name TEXT NOT NULL DEFAULT '',
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    change_pct REAL,
                    volume REAL,
                    amount REAL,
                    fetched_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (base_date, stock_code)
                );

                CREATE TABLE IF NOT EXISTS public_securities_products (
                    base_date TEXT NOT NULL,
                    product_type TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    isin_code TEXT NOT NULL DEFAULT '',
                    market TEXT NOT NULL DEFAULT '',
                    item_name TEXT NOT NULL DEFAULT '',
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    change_pct REAL,
                    volume REAL,
                    amount REAL,
                    fetched_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (base_date, product_type, stock_code)
                );

                CREATE TABLE IF NOT EXISTS fred_observations (
                    series_id TEXT NOT NULL,
                    observation_date TEXT NOT NULL,
                    value REAL,
                    value_text TEXT NOT NULL DEFAULT '',
                    realtime_start TEXT NOT NULL DEFAULT '',
                    realtime_end TEXT NOT NULL DEFAULT '',
                    fetched_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (series_id, observation_date)
                );

                CREATE INDEX IF NOT EXISTS idx_api_runs_source_time
                    ON external_api_runs(source, fetched_at);
                CREATE INDEX IF NOT EXISTS idx_dart_stock_date
                    ON dart_disclosures(stock_code, rcept_dt);
                CREATE INDEX IF NOT EXISTS idx_fred_series_date
                    ON fred_observations(series_id, observation_date);
                """
            )

    def record_run(
        self,
        *,
        source: str,
        endpoint: str,
        target: str = "",
        status: str,
        http_status: int | None = None,
        row_count: int = 0,
        fields: Iterable[str] | None = None,
        error: str = "",
        fetched_at: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO external_api_runs (
                    source, endpoint, target, status, http_status, row_count,
                    fields_json, error, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    endpoint,
                    target,
                    status,
                    http_status,
                    int(row_count or 0),
                    _json(sorted(set(fields or []))),
                    str(error or "")[:500],
                    fetched_at,
                ),
            )

    def upsert_dart_disclosures(self, rows: Iterable[dict[str, Any]]) -> int:
        count = 0
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO dart_disclosures (
                        rcept_no, stock_code, corp_code, corp_name, report_name,
                        rcept_dt, risk_level, risk_tags_json, url, fetched_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.get("rcept_no", ""),
                        row.get("stock_code", ""),
                        row.get("corp_code", ""),
                        row.get("corp_name", ""),
                        row.get("report_name", ""),
                        row.get("rcept_dt", ""),
                        row.get("risk_level", "unknown"),
                        _json(row.get("risk_tags", [])),
                        row.get("url", ""),
                        row.get("fetched_at", ""),
                        _json(row.get("raw", {})),
                    ),
                )
                count += 1
        return count

    def upsert_public_krx_listed(self, rows: Iterable[dict[str, Any]]) -> int:
        count = 0
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO public_krx_listed (
                        base_date, stock_code, isin_code, market, item_name,
                        corp_name, corp_reg_no, fetched_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.get("base_date", ""),
                        row.get("stock_code", ""),
                        row.get("isin_code", ""),
                        row.get("market", ""),
                        row.get("item_name", ""),
                        row.get("corp_name", ""),
                        row.get("corp_reg_no", ""),
                        row.get("fetched_at", ""),
                        _json(row.get("raw", {})),
                    ),
                )
                count += 1
        return count

    def upsert_public_stock_quotes(self, rows: Iterable[dict[str, Any]]) -> int:
        count = 0
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO public_stock_quotes (
                        base_date, stock_code, isin_code, market, item_name,
                        open, high, low, close, change_pct, volume, amount,
                        fetched_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.get("base_date", ""),
                        row.get("stock_code", ""),
                        row.get("isin_code", ""),
                        row.get("market", ""),
                        row.get("item_name", ""),
                        row.get("open"),
                        row.get("high"),
                        row.get("low"),
                        row.get("close"),
                        row.get("change_pct"),
                        row.get("volume"),
                        row.get("amount"),
                        row.get("fetched_at", ""),
                        _json(row.get("raw", {})),
                    ),
                )
                count += 1
        return count

    def upsert_public_securities_products(self, rows: Iterable[dict[str, Any]]) -> int:
        count = 0
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO public_securities_products (
                        base_date, product_type, stock_code, isin_code, market,
                        item_name, open, high, low, close, change_pct, volume,
                        amount, fetched_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.get("base_date", ""),
                        row.get("product_type", ""),
                        row.get("stock_code", ""),
                        row.get("isin_code", ""),
                        row.get("market", ""),
                        row.get("item_name", ""),
                        row.get("open"),
                        row.get("high"),
                        row.get("low"),
                        row.get("close"),
                        row.get("change_pct"),
                        row.get("volume"),
                        row.get("amount"),
                        row.get("fetched_at", ""),
                        _json(row.get("raw", {})),
                    ),
                )
                count += 1
        return count

    def upsert_fred_observations(self, rows: Iterable[dict[str, Any]]) -> int:
        count = 0
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO fred_observations (
                        series_id, observation_date, value, value_text,
                        realtime_start, realtime_end, fetched_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.get("series_id", ""),
                        row.get("observation_date", ""),
                        row.get("value"),
                        row.get("value_text", ""),
                        row.get("realtime_start", ""),
                        row.get("realtime_end", ""),
                        row.get("fetched_at", ""),
                        _json(row.get("raw", {})),
                    ),
                )
                count += 1
        return count

    def table_counts(self) -> dict[str, int]:
        tables = ["external_api_runs", *DATA_TABLES]
        with self.connect() as conn:
            return {
                table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in tables
            }

    def readiness_summary(self, *, initialize: bool = False) -> dict[str, Any]:
        if not self.path.exists() and not initialize:
            return {
                "db_path": str(self.path),
                "status": "missing_db",
                "table_counts": {},
                "latest_fetched_at_by_table": {},
                "latest_api_run_at": "",
                "total_data_rows": 0,
                "production_ready": False,
            }
        if initialize:
            self.init_schema()
        with self.connect() as conn:
            existing = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                )
            }
            counts: dict[str, int] = {}
            latest_by_table: dict[str, str] = {}
            for table in ["external_api_runs", *DATA_TABLES]:
                if table not in existing:
                    counts[table] = 0
                    if table in DATA_TABLES:
                        latest_by_table[table] = ""
                    continue
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                if table in DATA_TABLES:
                    row = conn.execute(f"SELECT MAX(fetched_at) AS latest_at FROM {table}").fetchone()
                    latest_by_table[table] = str(row["latest_at"] or "") if row else ""
            latest_api_row = (
                conn.execute("SELECT MAX(fetched_at) AS latest_at FROM external_api_runs").fetchone()
                if "external_api_runs" in existing
                else None
            )
        total_data_rows = sum(counts.get(table, 0) for table in DATA_TABLES)
        production_ready = total_data_rows > 0
        return {
            "db_path": str(self.path),
            "status": "ready" if production_ready else "empty",
            "table_counts": counts,
            "latest_fetched_at_by_table": latest_by_table,
            "latest_api_run_at": str(latest_api_row["latest_at"] or "") if latest_api_row else "",
            "total_data_rows": total_data_rows,
            "production_ready": production_ready,
        }
