from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(data: Any) -> str:
    return json.dumps(data or {}, ensure_ascii=False, sort_keys=True, default=str)


def build_replay_cache_key(*, model: str, prompt_hash: str, config_hash: str, known_at: str) -> str:
    raw = "|".join([str(model or ""), str(prompt_hash or ""), str(config_hash or ""), str(known_at or "")])
    return "agent_cache_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


class AgentCallEventStore:
    """SQLite index for raw agent calls.

    Raw prompts/responses stay in JSON files. The DB stores hashes, paths, parse
    status and a debug-only replay cache key for audit/retry workflows.
    """

    def __init__(self, path: str | Path, *, timeout: float = 5.0) -> None:
        self.path = Path(path)
        self.timeout = float(timeout or 5.0)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def init(self) -> None:
        conn = sqlite3.connect(str(self.path), timeout=self.timeout)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_call_events (
                    call_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    market TEXT NOT NULL,
                    call_date TEXT NOT NULL,
                    known_at TEXT NOT NULL,
                    model TEXT,
                    prompt_hash TEXT,
                    response_hash TEXT,
                    config_hash TEXT,
                    raw_call_path TEXT,
                    parse_stage TEXT,
                    parse_error INTEGER DEFAULT 0,
                    duration_ms INTEGER,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_agent_call_events_market_date
                    ON agent_call_events(market, call_date, known_at);
                CREATE INDEX IF NOT EXISTS idx_agent_call_events_prompt_hash
                    ON agent_call_events(prompt_hash, config_hash, model);

                CREATE TABLE IF NOT EXISTS agent_call_replay_cache (
                    cache_key TEXT PRIMARY KEY,
                    call_id TEXT NOT NULL,
                    model TEXT,
                    prompt_hash TEXT,
                    config_hash TEXT,
                    known_at TEXT,
                    raw_call_path TEXT,
                    parsed_json TEXT NOT NULL DEFAULT '{}',
                    advisory_only INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

    def upsert_event(self, event: dict[str, Any]) -> str:
        now = _utc_now()
        call_id = str(event.get("call_id") or "")
        if not call_id:
            raise ValueError("call_id is required")
        model = str(event.get("model") or "")
        prompt_hash = str(event.get("prompt_hash") or "")
        config_hash = str(event.get("config_hash") or "")
        known_at = str(event.get("known_at") or event.get("timestamp") or now)
        cache_key = build_replay_cache_key(
            model=model,
            prompt_hash=prompt_hash,
            config_hash=config_hash,
            known_at=known_at,
        )
        conn = self.connect()
        try:
            conn.execute(
                """
                INSERT INTO agent_call_events (
                    call_id, label, market, call_date, known_at, model,
                    prompt_hash, response_hash, config_hash, raw_call_path,
                    parse_stage, parse_error, duration_ms, input_tokens,
                    output_tokens, payload_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(call_id) DO UPDATE SET
                    label=excluded.label,
                    market=excluded.market,
                    call_date=excluded.call_date,
                    known_at=excluded.known_at,
                    model=excluded.model,
                    prompt_hash=excluded.prompt_hash,
                    response_hash=excluded.response_hash,
                    config_hash=excluded.config_hash,
                    raw_call_path=excluded.raw_call_path,
                    parse_stage=excluded.parse_stage,
                    parse_error=excluded.parse_error,
                    duration_ms=excluded.duration_ms,
                    input_tokens=excluded.input_tokens,
                    output_tokens=excluded.output_tokens,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    call_id,
                    str(event.get("label") or ""),
                    str(event.get("market") or "").upper(),
                    str(event.get("call_date") or ""),
                    known_at,
                    model,
                    prompt_hash,
                    str(event.get("response_hash") or ""),
                    config_hash,
                    str(event.get("raw_call_path") or ""),
                    str(event.get("parse_stage") or ""),
                    1 if bool(event.get("parse_error")) else 0,
                    event.get("duration_ms"),
                    int(event.get("input_tokens") or 0),
                    int(event.get("output_tokens") or 0),
                    _json(event.get("payload") or {}),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO agent_call_replay_cache (
                    cache_key, call_id, model, prompt_hash, config_hash,
                    known_at, raw_call_path, parsed_json, advisory_only,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    call_id=excluded.call_id,
                    raw_call_path=excluded.raw_call_path,
                    parsed_json=excluded.parsed_json,
                    updated_at=excluded.updated_at
                """,
                (
                    cache_key,
                    call_id,
                    model,
                    prompt_hash,
                    config_hash,
                    known_at,
                    str(event.get("raw_call_path") or ""),
                    _json(event.get("parsed") or {}),
                    now,
                    now,
                ),
            )
            conn.commit()
            return cache_key
        finally:
            conn.close()

    def event(self, call_id: str) -> dict[str, Any] | None:
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM agent_call_events WHERE call_id=?",
                (str(call_id or ""),),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()
