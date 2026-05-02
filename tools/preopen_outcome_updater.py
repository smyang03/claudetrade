from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.session_date import KST, resolve_session_date_str
from preopen.storage import load_preopen_state, save_outcome_record, save_preopen_state


def update_once(market: str, *, offset_min: int) -> dict:
    market = "US" if str(market or "").upper() == "US" else "KR"
    session_date = resolve_session_date_str(market)
    state = load_preopen_state(market, session_date=session_date, max_age_min=24 * 60) or {
        "market": market,
        "session_date": session_date,
        "candidates": [],
    }
    captured_at = datetime.now(KST).isoformat(timespec="seconds")
    updated = 0
    for candidate in state.get("candidates", []) or []:
        record = {
            "market": market,
            "session_date": session_date,
            "ticker": candidate.get("ticker", ""),
            "offset_min": int(offset_min),
            "captured_at": captured_at,
            "outcome_status": "pending_price_provider",
            "post_open_5m_return_pct": candidate.get("post_open_5m_return_pct"),
            "post_open_30m_return_pct": candidate.get("post_open_30m_return_pct"),
            "post_open_60m_return_pct": candidate.get("post_open_60m_return_pct"),
            "post_open_mfe_pct": candidate.get("post_open_mfe_pct"),
            "post_open_mae_pct": candidate.get("post_open_mae_pct"),
            "max_runup_pct": candidate.get("max_runup_pct", candidate.get("post_open_mfe_pct")),
            "max_drawdown_pct": candidate.get("max_drawdown_pct", candidate.get("post_open_mae_pct")),
            "open_to_high_pct": candidate.get("open_to_high_pct"),
            "open_to_close_pct": candidate.get("open_to_close_pct"),
        }
        key = f"outcome_{int(offset_min)}m_captured_at"
        candidate[key] = captured_at
        save_outcome_record(market, session_date, record)
        updated += 1
    state["last_outcome_update_at"] = captured_at
    state["last_outcome_offset_min"] = int(offset_min)
    save_preopen_state(market, state, session_date=session_date)
    return {"market": market, "session_date": session_date, "updated": updated, "offset_min": int(offset_min)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Shadow-only preopen post-open outcome updater")
    parser.add_argument("--market", choices=["US", "KR"], required=True)
    parser.add_argument("--offset-min", type=int, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    result = update_once(args.market, offset_min=args.offset_min)
    print(
        f"[preopen outcome] {result['market']} {result['session_date']} "
        f"offset={result['offset_min']}m updated={result['updated']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
