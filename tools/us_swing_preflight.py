from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.us_swing_authority import evaluate_swing_authority, load_swing_policy


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check US swing accelerated evidence and authority readiness")
    parser.add_argument("--policy", default=str(ROOT / "config" / "us_swing_accelerated.json"))
    parser.add_argument("--historical", default=str(ROOT / "state" / "us_swing_historical_evidence.json"))
    parser.add_argument("--execution-evidence", default=str(ROOT / "state" / "us_swing_execution_evidence.json"))
    parser.add_argument("--status", default=str(ROOT / "state" / "us_swing_status.json"))
    parser.add_argument("--shadow-db", default=str(ROOT / "data" / "analysis" / "us_swing_shadow.db"))
    parser.add_argument("--runtime-config", default=str(ROOT / "config" / "v2_start_config.json"))
    parser.add_argument("--configured-mode", default=os.getenv("US_SWING_AUTHORITY_MODE", "shadow"))
    args = parser.parse_args()
    policy_path = Path(args.policy)
    historical_path = Path(args.historical)
    execution_path = Path(args.execution_evidence)
    status_path = Path(args.status)
    db_path = Path(args.shadow_db)
    runtime_config_path = Path(args.runtime_config)
    missing = [str(path) for path in (policy_path, historical_path, execution_path, status_path, db_path, runtime_config_path) if not path.exists()]
    if missing:
        print(json.dumps({"ok": False, "reason": "artifact_missing", "missing": missing}, indent=2))
        return 2
    policy = load_swing_policy(policy_path)
    historical = _load(historical_path)
    execution_evidence = _load(execution_path)
    status = _load(status_path)
    runtime_config = _load(runtime_config_path)
    env_overrides = runtime_config.get("env_overrides") if isinstance(runtime_config.get("env_overrides"), dict) else {}
    forward = status.get("forward_evidence") if isinstance(status.get("forward_evidence"), dict) else {}
    configured = evaluate_swing_authority(
        configured_mode=args.configured_mode,
        historical_evidence=historical,
        forward_evidence=forward,
        policy=policy,
        execution_evidence=execution_evidence,
    )
    micro = evaluate_swing_authority(
        configured_mode="micro",
        historical_evidence=historical,
        forward_evidence=forward,
        policy=policy,
        execution_evidence=execution_evidence,
    )
    con = sqlite3.connect(db_path)
    try:
        columns = {str(row[1]) for row in con.execute("PRAGMA table_info(signals)")}
        total = int(con.execute("SELECT COUNT(*) FROM signals").fetchone()[0])
        pending = int(con.execute("SELECT COUNT(*) FROM signals WHERE status='PENDING'").fetchone()[0])
        matured = int(con.execute("SELECT COUNT(*) FROM signals WHERE status='MATURED'").fetchone()[0])
        breadth_tagged = int(con.execute(
            "SELECT COUNT(*) FROM signals WHERE breadth_context_state IS NOT NULL AND breadth_context_state!='MISSING'"
        ).fetchone()[0])
        breadth_states = {
            str(state or "MISSING"): int(count)
            for state, count in con.execute(
                "SELECT COALESCE(breadth_context_state,'MISSING'),COUNT(*) FROM signals GROUP BY 1"
            ).fetchall()
        }
        reference_ready = int(con.execute(
            "SELECT COUNT(*) FROM signals WHERE status='PENDING' AND reference_close IS NOT NULL"
        ).fetchone()[0]) if "reference_close" in columns else 0
        handoff_states = {
            str(state or "UNTOUCHED"): int(count)
            for state, count in con.execute(
                "SELECT COALESCE(handoff_status,'UNTOUCHED'),COUNT(*) FROM signals GROUP BY 1"
            ).fetchall()
        } if "handoff_status" in columns else {"SCHEMA_NOT_INSTALLED": total}
    finally:
        con.close()
    report = {
        "ok": True,
        "configured_authority": configured.to_dict(),
        "micro_readiness": micro.to_dict(),
        "historical": {
            "policy_hash_matches": historical.get("policy_sha256") == policy.get("_policy_sha256"),
            "oos_sessions": historical.get("oos_sessions"),
            "top3": (historical.get("cohorts") or {}).get("top3"),
            "top5": (historical.get("cohorts") or {}).get("top5"),
            "source_independence": historical.get("source_independence"),
        },
        "execution_contract": execution_evidence,
        "forward": forward,
        "ledger": {
            "total": total,
            "pending": pending,
            "matured": matured,
            "breadth_tagged": breadth_tagged,
            "breadth_states": breadth_states,
            "reference_ready_pending": reference_ready,
            "handoff_states": handoff_states,
        },
        "order_integration": {
            "state": "WIRED_FAIL_CLOSED_DISABLED",
            "handoff_enabled": str(env_overrides.get("US_SWING_ORDER_HANDOFF_ENABLED", "false")).lower() == "true",
            "submit_enabled": str(env_overrides.get("US_SWING_ORDER_SUBMIT_ENABLED", "false")).lower() == "true",
            "live_ack_configured": bool(str(env_overrides.get("US_SWING_ORDER_LIVE_ACK", "") or "")),
            "required_schema_columns_present": all(
                name in columns for name in ("reference_close", "handoff_status", "handoff_order_no")
            ),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
