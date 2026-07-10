from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from preopen.scheduler import regular_open_dt
from runtime.us_swing_order_handoff import (
    evaluate_handoff,
    load_handoff_signals,
    resolve_handoff_authority,
)


def _latest_pending_session(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT MAX(signal_date) FROM signals WHERE status='PENDING'"
    ).fetchone()
    return str((row or [""])[0] or "")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline-only US swing handoff rehearsal; never contacts broker or submits orders"
    )
    parser.add_argument("--shadow-db", default=str(ROOT / "data" / "analysis" / "us_swing_shadow.db"))
    parser.add_argument("--policy", default=str(ROOT / "config" / "us_swing_accelerated.json"))
    parser.add_argument("--historical", default=str(ROOT / "state" / "us_swing_historical_evidence.json"))
    parser.add_argument("--execution-evidence", default=str(ROOT / "state" / "us_swing_execution_evidence.json"))
    parser.add_argument("--session-date", default="")
    parser.add_argument("--configured-mode", default="shadow")
    parser.add_argument("--eligible-fixture", action="store_true")
    parser.add_argument("--fx-rate", type=float, default=1400.0)
    parser.add_argument("--base-budget-krw", type=float, default=2_000_000.0)
    args = parser.parse_args()

    con = sqlite3.connect(Path(args.shadow_db))
    try:
        session_date = str(args.session_date or _latest_pending_session(con))
        if not session_date:
            print(json.dumps({"ok": False, "reason": "no_pending_session"}, indent=2))
            return 2
        authority: dict[str, Any] = resolve_handoff_authority(
            configured_mode=str(args.configured_mode),
            con=con,
            policy_path=Path(args.policy),
            historical_path=Path(args.historical),
            execution_path=Path(args.execution_evidence),
        )
        if args.eligible_fixture:
            authority = {
                **authority,
                "effective_mode": "micro",
                "allowed_to_emit_orders": True,
                "size_multiplier": 0.10,
                "max_new_per_day": 1,
                "max_open_slots": 1,
                "fixture_override": "MOCK_ONLY_NO_BROKER",
            }
        signals = load_handoff_signals(con, session_date=session_date, limit=1)
        if not signals:
            print(json.dumps({"ok": False, "reason": "no_pending_signal", "session_date": session_date}, indent=2))
            return 2
        signal = signals[0]
        reference = float(signal.get("reference_close") or 100.0)
        opened = regular_open_dt("US", session_date)
        decision = evaluate_handoff(
            signal=signal,
            authority=authority,
            now=opened + timedelta(minutes=10),
            regular_open=opened,
            handoff_enabled=True,
            submit_enabled=False,
            quote={
                "price": reference * 1.005,
                "open": reference,
                "prev_close": reference,
                "volume": 10_000,
            },
            fx_rate=float(args.fx_rate),
            base_order_budget_krw=float(args.base_budget_krw),
            available_budget_krw=float(args.base_budget_krw),
            cash_krw=float(args.base_budget_krw),
            broker_trust_level="trusted",
            already_holding=False,
            pending_order=False,
            same_day_reentry_allowed=True,
            absolute_hurdles_enforced=False,
        )
        report = {
            "ok": True,
            "safety": "OFFLINE_MOCK_NO_BROKER_NO_ORDER",
            "fixture_override": bool(args.eligible_fixture),
            "session_date": session_date,
            "authority": authority,
            "decision": decision.to_dict(),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
