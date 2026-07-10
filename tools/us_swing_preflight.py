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
    parser.add_argument("--status", default=str(ROOT / "state" / "us_swing_status.json"))
    parser.add_argument("--shadow-db", default=str(ROOT / "data" / "analysis" / "us_swing_shadow.db"))
    parser.add_argument("--configured-mode", default=os.getenv("US_SWING_AUTHORITY_MODE", "shadow"))
    args = parser.parse_args()
    policy_path = Path(args.policy)
    historical_path = Path(args.historical)
    status_path = Path(args.status)
    db_path = Path(args.shadow_db)
    missing = [str(path) for path in (policy_path, historical_path, status_path, db_path) if not path.exists()]
    if missing:
        print(json.dumps({"ok": False, "reason": "artifact_missing", "missing": missing}, indent=2))
        return 2
    policy = load_swing_policy(policy_path)
    historical = _load(historical_path)
    status = _load(status_path)
    forward = status.get("forward_evidence") if isinstance(status.get("forward_evidence"), dict) else {}
    configured = evaluate_swing_authority(
        configured_mode=args.configured_mode,
        historical_evidence=historical,
        forward_evidence=forward,
        policy=policy,
    )
    micro = evaluate_swing_authority(
        configured_mode="micro",
        historical_evidence=historical,
        forward_evidence=forward,
        policy=policy,
    )
    con = sqlite3.connect(db_path)
    try:
        total = int(con.execute("SELECT COUNT(*) FROM signals").fetchone()[0])
        pending = int(con.execute("SELECT COUNT(*) FROM signals WHERE status='PENDING'").fetchone()[0])
        matured = int(con.execute("SELECT COUNT(*) FROM signals WHERE status='MATURED'").fetchone()[0])
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
        "forward": forward,
        "ledger": {"total": total, "pending": pending, "matured": matured},
        "order_integration": "NOT_CONNECTED_UNTIL_EXPLICIT_LIVE_WIRING",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
