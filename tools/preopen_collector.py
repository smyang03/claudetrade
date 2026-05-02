from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.session_date import KST, resolve_session_date_str
from preopen.models import normalize_candidate
from preopen.scorer import score_candidates
from preopen.storage import save_candidate_records, save_preopen_state
from runtime_paths import get_runtime_path


def _market_key(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _seed_tickers(market: str, explicit: str = "") -> list[str]:
    raw = explicit or os.getenv(f"PREOPEN_{market}_TICKERS", "")
    if raw:
        return [item.strip().upper() if market == "US" else item.strip() for item in raw.split(",") if item.strip()]
    if market == "US":
        return ["NVDA", "TSLA", "AAPL", "MSFT", "AMD", "QCOM"]
    return ["005930", "000660", "035420", "035720", "005380", "068270"]


def _read_kis_token_status(mode: str) -> str:
    path = get_runtime_path("state", f"{mode}_kis_token.json")
    if not path.exists():
        return "token_unavailable"
    try:
        if path.stat().st_size <= 0:
            return "token_unavailable"
    except Exception:
        return "token_unavailable"
    return "token_present_read_only"


def _collect_us_seed_candidates(tickers: list[str], captured_at: str, session_date: str) -> list[dict]:
    candidates = []
    for ticker in tickers:
        candidates.append(normalize_candidate({
            "ticker": ticker,
            "name": ticker,
            "source": "seed_watchlist",
            "source_status": "ranking_provider_not_configured",
            "quality_tags": ["seed_only"],
            "risk_tags": [],
        }, market="US", session_date=session_date, captured_at=captured_at))
    return candidates


def _collect_kr_seed_candidates(tickers: list[str], captured_at: str, session_date: str) -> list[dict]:
    candidates = []
    for ticker in tickers:
        candidates.append(normalize_candidate({
            "ticker": ticker,
            "name": ticker,
            "source": "seed_watchlist",
            "source_status": "kis_rank_not_called_by_shadow_collector",
            "quality_tags": ["seed_only"],
            "risk_tags": [],
            "open_volume_confirmation": None,
        }, market="KR", session_date=session_date, captured_at=captured_at))
    return candidates


def collect_once(market: str, *, mode: str = "live", tickers: str = "") -> dict:
    market = _market_key(market)
    captured_at = datetime.now(KST).isoformat(timespec="seconds")
    session_date = resolve_session_date_str(market)
    seed_tickers = _seed_tickers(market, tickers)

    token_status = ""
    if market == "KR":
        token_status = _read_kis_token_status(mode)
        if token_status == "token_unavailable":
            raw_candidates = []
        else:
            raw_candidates = _collect_kr_seed_candidates(seed_tickers, captured_at, session_date)
    else:
        raw_candidates = _collect_us_seed_candidates(seed_tickers, captured_at, session_date)

    candidates = score_candidates(market, raw_candidates)
    collector_status = "ok" if candidates else "no_candidates"
    if market == "KR" and token_status == "token_unavailable":
        collector_status = "token_unavailable"
    state = {
        "market": market,
        "session_date": session_date,
        "captured_at": captured_at,
        "collector_status": collector_status,
        "collector_mode": "shadow_only",
        "token_status": token_status,
        "source_status": "seed_only",
        "candidate_count": len(candidates),
        "excluded_count": sum(1 for c in candidates if c.get("preopen_grade") == "X"),
        "candidates": candidates,
        "notes": [
            "shadow-only collector",
            "does not refresh KIS token",
            "does not affect bot selection or orders",
        ],
    }
    save_preopen_state(market, state, session_date=session_date)
    save_candidate_records(market, session_date, candidates, state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Shadow-only preopen candidate collector")
    parser.add_argument("--market", choices=["US", "KR"], required=True)
    parser.add_argument("--mode", choices=["paper", "live"], default="live")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-sec", type=int, default=300)
    parser.add_argument("--tickers", default="")
    args = parser.parse_args()

    if not args.once and not args.loop:
        args.once = True

    while True:
        state = collect_once(args.market, mode=args.mode, tickers=args.tickers)
        print(
            f"[preopen collector] {state['market']} {state['session_date']} "
            f"status={state['collector_status']} candidates={state['candidate_count']}"
        )
        if not args.loop:
            return 0
        time.sleep(max(10, int(args.interval_sec)))


if __name__ == "__main__":
    raise SystemExit(main())
