"""claudetrade-audit MCP 서버 (읽기 전용 — 주문·설정 변경 도구 없음).

2026-08-02 운영자 MCP 도입 계획 1순위. stdlib만 사용(서드파티 의존성 0),
stdio JSON-RPC(newline-delimited)로 동작한다. 라이브 주문 루프와 완전 분리 —
이 서버는 Claude Code 세션이 띄울 때만 실행되고, 파일·DB를 읽기 전용으로만 연다.

제공 도구:
  get_effective_config   봇이 기록한 redacted 스냅샷(비밀키 마스킹) 조회
  check_buy_gate         매수 경로 스위치 요약(LEGACY/PathB/us_swing/코어)
  get_us_swing_authority us_swing 실행 권한·최근 결과
  get_open_positions     브로커 truth 보유
  get_pending_orders     브로커 truth 미체결
  get_shadow_performance KR/US shadow 원장 성과 요약
  get_recent_db_health   핵심 DB 존재·크기·최신성

모든 응답에 as_of / source / data_age_sec / schema_version 메타데이터를 포함한다.
등록: .mcp.json 의 claudetrade-audit 항목. 검증: tools/claudetrade_audit_mcp_smoke.py
"""
from __future__ import annotations

import glob
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "audit_mcp_v1"
PROTOCOL_VERSION = "2024-11-05"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _meta(source: str, mtime: float | None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": _now_iso(),
        "source": source,
        "data_age_sec": round(time.time() - mtime, 1) if mtime else None,
    }


def _read_json(path: Path) -> tuple[dict, dict]:
    if not path.exists():
        return {}, _meta(str(path), None)
    mtime = path.stat().st_mtime
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        return {"error": f"read_json_failed: {str(exc)[:160]}"}, _meta(str(path), mtime)
    return payload if isinstance(payload, dict) else {"error": "json_root_not_object"}, _meta(str(path), mtime)


def _ro_connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2.0)
    con.execute("PRAGMA busy_timeout=2000")
    return con


# ---------- 도구 구현 ----------

def get_effective_config(keys: list[str] | None = None) -> dict:
    candidates = sorted(glob.glob(str(ROOT / "logs" / "config" / "effective_config_*_live.redacted.json")))
    if not candidates:
        return {"error": "no redacted effective-config snapshot", "meta": _meta("logs/config", None)}
    path = Path(candidates[-1])
    payload, meta = _read_json(path)
    effective = dict(payload.get("effective") or {})
    if keys:
        effective = {k: effective.get(k) for k in keys}
    return {
        "written_at": payload.get("written_at"),
        "runtime_mode": payload.get("runtime_mode"),
        "effective": effective,
        "meta": meta,
    }


_BUY_GATE_KEYS = [
    "LEGACY_NEW_BUY_DISABLED",
    "PATHB_KR_LIVE_ENABLED",
    "PATHB_US_LIVE_ENABLED",
    "PROFIT_STRATEGY_ENABLED_IDS",
    "US_SWING_AUTHORITY_MODE",
    "US_SWING_ORDER_SUBMIT_ENABLED",
    "US_SWING_ALLOWED_SOURCES",
    "US_GAP_PULLBACK_LIVE_ENABLED",
    "KR_FALLEN_SHADOW_SCHEDULER_ENABLED",
]


def check_buy_gate() -> dict:
    cfg = get_effective_config(_BUY_GATE_KEYS)
    eff = cfg.get("effective") or {}

    def truthy(key: str) -> bool:
        return str(eff.get(key) or "").strip().lower() in {"1", "true", "yes", "on"}

    legacy_blocked = truthy("LEGACY_NEW_BUY_DISABLED")
    authority = get_us_swing_authority()
    execution = dict(authority.get("execution_authority") or {})
    broker, broker_meta = _broker_snapshot()
    broker_ready = _broker_market_ready(broker, broker_meta, "US")
    swing_configured = bool(
        truthy("US_SWING_ORDER_SUBMIT_ENABLED")
        and str(eff.get("US_SWING_AUTHORITY_MODE") or "").lower() == "micro"
    )
    swing_authority_allowed = bool(execution.get("allowed_to_emit_orders"))
    authority_age = (authority.get("meta") or {}).get("data_age_sec")
    authority_fresh = authority_age is not None and float(authority_age) <= 900.0
    swing_live_ready = bool(
        swing_configured
        and swing_authority_allowed
        and authority_fresh
        and bool(authority.get("submit_enabled"))
        and bool(authority.get("live_ack_verified"))
        and broker_ready
    )
    verdict = {
        "US": {
            "us_swing_configured": swing_configured,
            "us_swing_authority_allowed": swing_authority_allowed,
            "us_swing_authority_fresh": authority_fresh,
            "us_swing_broker_truth_ready": broker_ready,
            "us_swing_live_ready_without_signal": swing_live_ready,
            "pathb": truthy("PATHB_US_LIVE_ENABLED") and not legacy_blocked,
            "legacy_paths_blocked": legacy_blocked,
        },
        "KR": {
            "new_buy": truthy("PATHB_KR_LIVE_ENABLED") and not legacy_blocked,
            "fallen_shadow_scan": truthy("KR_FALLEN_SHADOW_SCHEDULER_ENABLED"),
            "legacy_paths_blocked": legacy_blocked,
        },
        "core_new_signals": bool(str(eff.get("PROFIT_STRATEGY_ENABLED_IDS") or "").strip()),
    }
    return {
        "switches": eff,
        "verdict": verdict,
        "us_swing_authority": {
            "generated_at": authority.get("generated_at"),
            "data_age_sec": (authority.get("meta") or {}).get("data_age_sec"),
            "max_new_per_day": execution.get("max_new_per_day"),
            "max_open_slots": execution.get("max_open_slots"),
            "blockers": execution.get("blockers") or [],
        },
        "broker_truth": {
            "generated_at": broker.get("generated_at"),
            "ready": broker_ready,
            "meta": broker_meta,
        },
        "meta": cfg.get("meta"),
    }


def get_us_swing_authority() -> dict:
    payload, meta = _read_json(ROOT / "state" / "us_swing_execution_status.json")
    return {**payload, "meta": meta}


def _broker_snapshot() -> tuple[dict, dict]:
    return _read_json(ROOT / "state" / "live_broker_truth_snapshot.json")


def _broker_market_ready(payload: dict, meta: dict, market: str) -> bool:
    market_payload = dict((payload.get("markets") or {}).get(str(market).upper()) or {})
    if not market_payload or bool(
        market_payload.get("missing")
        or market_payload.get("stale")
        or str(market_payload.get("error") or "").strip()
    ):
        return False
    age = meta.get("data_age_sec")
    ttl = float(market_payload.get("ttl_sec") or 180.0)
    return age is not None and float(age) <= ttl


def get_open_positions() -> dict:
    payload, meta = _broker_snapshot()
    markets = payload.get("markets") or {}
    return {
        "generated_at": payload.get("generated_at"),
        "positions": {mk: (markets.get(mk) or {}).get("positions") or [] for mk in ("KR", "US")},
        "stale": {mk: not _broker_market_ready(payload, meta, mk) for mk in ("KR", "US")},
        "meta": meta,
    }


def get_pending_orders() -> dict:
    payload, meta = _broker_snapshot()
    markets = payload.get("markets") or {}
    return {
        "generated_at": payload.get("generated_at"),
        "open_orders": {mk: (markets.get(mk) or {}).get("open_orders") or [] for mk in ("KR", "US")},
        "meta": meta,
    }


def get_shadow_performance(market: str = "KR", days: int = 45) -> dict:
    market_key = str(market or "KR").upper()
    if market_key == "KR":
        path = ROOT / "data" / "shadow" / "kr_fallen_shadow.jsonl"
        if not path.exists():
            return {"error": "kr shadow ledger missing", "meta": _meta(str(path), None)}
        cutoff = datetime.now().date().fromordinal(datetime.now().date().toordinal() - max(0, int(days)))
        rows = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            raw_date = str(row.get("session_date") or "")[:10].replace("/", "-")
            try:
                row_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
            except ValueError:
                row_date = None
            if row_date is None or row_date >= cutoff:
                rows.append(row)
        settled = [r for r in rows if r.get("status") == "SETTLED"]
        passed = [r for r in settled if r.get("pass_all")]
        nets = [float(r.get("net_pct") or 0.0) for r in passed]
        return {
            "market": "KR",
            "rows_total": len(rows),
            "pending": sum(1 for r in rows if r.get("status") == "PENDING"),
            "settled": len(settled),
            "pass_all_settled": len(passed),
            "pass_all_mean_net_pct": round(sum(nets) / len(nets), 4) if nets else None,
            "pass_all_win_rate": round(100 * sum(1 for x in nets if x > 0) / len(nets), 1) if nets else None,
            "meta": _meta(str(path), path.stat().st_mtime),
        }
    db = ROOT / "data" / "analysis" / "us_swing_shadow.db"
    if not db.exists():
        return {"error": "us swing shadow db missing", "meta": _meta(str(db), None)}
    con = _ro_connect(db)
    try:
        row = con.execute(
            """SELECT COUNT(*), SUM(CASE WHEN status='MATURED' THEN 1 ELSE 0 END),
                      AVG(CASE WHEN status='MATURED' THEN net_krw_pct END),
                      MAX(signal_date)
               FROM signals WHERE signal_date >= date('now', ?)""",
            (f"-{int(days)} day",),
        ).fetchone()
    finally:
        con.close()
    total, matured, mean_net, last_date = row or (0, 0, None, None)
    return {
        "market": "US",
        "window_days": int(days),
        "signals": int(total or 0),
        "matured": int(matured or 0),
        "matured_mean_net_krw_pct": round(float(mean_net), 4) if mean_net is not None else None,
        "last_signal_date": last_date,
        "meta": _meta(str(db), db.stat().st_mtime),
    }


_DB_HEALTH_TARGETS = {
    "decisions": ("data/ml/decisions.db", "SELECT MAX(rowid) FROM decisions"),
    "ticker_selection_log": ("data/ticker_selection_log.db", None),
    "candidate_audit": ("data/audit/candidate_audit.db", None),
    "us_swing_shadow": ("data/analysis/us_swing_shadow.db", "SELECT MAX(rowid) FROM signals"),
}


def get_recent_db_health() -> dict:
    out: dict[str, dict] = {}
    mtimes: list[float] = []
    for name, (rel, probe_sql) in _DB_HEALTH_TARGETS.items():
        path = ROOT / rel
        if not path.exists():
            out[name] = {"exists": False}
            continue
        stat = path.stat()
        mtimes.append(stat.st_mtime)
        entry: dict = {
            "exists": True,
            "size_mb": round(stat.st_size / 1e6, 1),
            "modified_age_sec": round(time.time() - stat.st_mtime, 1),
        }
        if probe_sql:
            try:
                con = _ro_connect(path)
                try:
                    entry["max_rowid"] = con.execute(probe_sql).fetchone()[0]
                finally:
                    con.close()
            except Exception as exc:
                entry["probe_error"] = str(exc)[:120]
        out[name] = entry
    return {"databases": out, "meta": _meta("multiple", max(mtimes) if mtimes else None)}


TOOLS: dict[str, dict] = {
    "get_effective_config": {
        "fn": lambda a: get_effective_config(a.get("keys")),
        "description": "봇이 기록한 redacted effective-config 스냅샷 조회 (비밀키 마스킹). keys로 특정 키만 필터 가능.",
        "schema": {
            "type": "object",
            "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
        },
    },
    "check_buy_gate": {
        "fn": lambda a: check_buy_gate(),
        "description": "현재 매수 가능 경로 요약 — LEGACY 차단, PathB, us_swing micro, 코어, KR shadow 스캔 스위치.",
        "schema": {"type": "object", "properties": {}},
    },
    "get_us_swing_authority": {
        "fn": lambda a: get_us_swing_authority(),
        "description": "us_swing 실행 권한 상태(state/us_swing_execution_status.json) — 모드·슬롯·최근 결과.",
        "schema": {"type": "object", "properties": {}},
    },
    "get_open_positions": {
        "fn": lambda a: get_open_positions(),
        "description": "브로커 truth 스냅샷의 시장별 보유 종목.",
        "schema": {"type": "object", "properties": {}},
    },
    "get_pending_orders": {
        "fn": lambda a: get_pending_orders(),
        "description": "브로커 truth 스냅샷의 시장별 미체결 주문.",
        "schema": {"type": "object", "properties": {}},
    },
    "get_shadow_performance": {
        "fn": lambda a: get_shadow_performance(a.get("market", "KR"), int(a.get("days", 45))),
        "description": "shadow 원장 성과 요약 — KR: kr_fallen_shadow.jsonl, US: us_swing_shadow.db.",
        "schema": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "enum": ["KR", "US"]},
                "days": {"type": "integer"},
            },
        },
    },
    "get_recent_db_health": {
        "fn": lambda a: get_recent_db_health(),
        "description": "핵심 DB(decisions·selection·audit·us_swing_shadow) 존재·크기·최신성·최대 rowid.",
        "schema": {"type": "object", "properties": {}},
    },
}


# ---------- MCP stdio 루프 ----------

def _reply(msg_id, result=None, error=None) -> None:
    payload: dict = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = msg.get("method") or ""
        msg_id = msg.get("id")
        if method == "initialize":
            client_proto = str(((msg.get("params") or {}).get("protocolVersion")) or PROTOCOL_VERSION)
            _reply(msg_id, {
                "protocolVersion": client_proto,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "claudetrade-audit", "version": "1.0.0"},
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _reply(msg_id, {
                "tools": [
                    {"name": name, "description": spec["description"], "inputSchema": spec["schema"]}
                    for name, spec in TOOLS.items()
                ]
            })
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = str(params.get("name") or "")
            args = params.get("arguments") or {}
            spec = TOOLS.get(name)
            if spec is None:
                _reply(msg_id, error={"code": -32602, "message": f"unknown tool: {name}"})
                continue
            try:
                result = spec["fn"](args)
                _reply(msg_id, {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]
                })
            except Exception as exc:
                _reply(msg_id, {
                    "content": [{"type": "text", "text": json.dumps({"error": str(exc)[:300]})}],
                    "isError": True,
                })
        elif msg_id is not None:
            _reply(msg_id, error={"code": -32601, "message": f"method not supported: {method}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
