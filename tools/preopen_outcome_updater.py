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
from tools.preopen_collector import _read_kis_token_state


def _num(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _classify_outcome(record: dict) -> str:
    existing = str(record.get("outcome_status") or "")
    if existing in {"price_provider_error"}:
        return existing
    values = [
        _num(record.get("post_open_return_pct")),
        _num(record.get("post_open_30m_return_pct")),
        _num(record.get("post_open_60m_return_pct")),
        _num(record.get("open_to_close_pct")),
    ]
    values = [v for v in values if v is not None]
    if not values:
        return "pending_price_provider"
    score = values[-1]
    if score >= 0.5:
        return "WIN"
    if score <= -0.5:
        return "LOSS"
    return "FLAT"


def _return_pct(current: float | None, base: float | None) -> float | None:
    current_f = _num(current)
    base_f = _num(base)
    if current_f is None or base_f is None or base_f <= 0:
        return None
    return round(((current_f - base_f) / base_f) * 100.0, 4)


def _should_fetch_price(candidate: dict, state: dict) -> bool:
    data_quality = str(candidate.get("data_quality") or state.get("data_quality") or "").lower()
    provider = str(candidate.get("provider") or state.get("provider") or "").lower()
    if provider == "seed_watchlist" or "seed_only" in data_quality:
        return False
    return bool(str(candidate.get("ticker", "") or "").strip())


def _fetch_price_snapshot(market: str, ticker: str, token: str) -> dict:
    from kis_api import get_price

    info = get_price(ticker, token, market=market)
    current = _num(info.get("price"))
    opened = _num(info.get("open"))
    high = _num(info.get("high"))
    low = _num(info.get("low"))
    volume = _num(info.get("volume"))
    if current is None or current <= 0:
        raise ValueError(f"price unavailable for {market}:{ticker}")
    return {
        "price": current,
        "open": opened,
        "high": high,
        "low": low,
        "volume": volume,
        "name": info.get("name", ""),
        "price_source": "kis_api.get_price",
    }


def _apply_price_outcome(candidate: dict, record: dict, price_snapshot: dict, captured_at: str, offset_min: int) -> None:
    current_price = _num(price_snapshot.get("price"))
    open_price = _num(candidate.get("regular_open_price"))
    if open_price is None or open_price <= 0:
        open_price = _num(price_snapshot.get("open"))
    if open_price is None or open_price <= 0:
        open_price = current_price

    high = _num(price_snapshot.get("high"))
    low = _num(price_snapshot.get("low"))
    ret = _return_pct(current_price, open_price)
    high_ret = _return_pct(high, open_price)
    low_ret = _return_pct(low, open_price)

    candidate["regular_open_price"] = open_price
    candidate["last_price"] = current_price
    candidate["last_price_at"] = captured_at
    candidate["last_outcome_offset_min"] = int(offset_min)
    candidate["last_outcome_price"] = current_price
    candidate["price_source"] = price_snapshot.get("price_source", "")

    dynamic_key = f"post_open_{int(offset_min)}m_return_pct"
    candidate[dynamic_key] = ret
    candidate[f"outcome_{int(offset_min)}m_captured_at"] = captured_at
    candidate[f"outcome_{int(offset_min)}m_price"] = current_price

    if high_ret is not None:
        previous = _num(candidate.get("max_runup_pct"))
        candidate["max_runup_pct"] = high_ret if previous is None else max(previous, high_ret)
        candidate["post_open_mfe_pct"] = candidate["max_runup_pct"]
        candidate["open_to_high_pct"] = high_ret
    if low_ret is not None:
        previous = _num(candidate.get("max_drawdown_pct"))
        candidate["max_drawdown_pct"] = low_ret if previous is None else min(previous, low_ret)
        candidate["post_open_mae_pct"] = candidate["max_drawdown_pct"]
    if ret is not None:
        candidate["open_to_close_pct"] = ret

    record.update({
        "outcome_status": "price_sampled",
        "price": current_price,
        "regular_open_price": open_price,
        "high": high,
        "low": low,
        "volume": _num(price_snapshot.get("volume")),
        "price_source": price_snapshot.get("price_source", ""),
        "post_open_return_pct": ret,
        dynamic_key: ret,
        "post_open_5m_return_pct": candidate.get("post_open_5m_return_pct"),
        "post_open_30m_return_pct": candidate.get("post_open_30m_return_pct"),
        "post_open_60m_return_pct": candidate.get("post_open_60m_return_pct"),
        "post_open_90m_return_pct": candidate.get("post_open_90m_return_pct"),
        "post_open_120m_return_pct": candidate.get("post_open_120m_return_pct"),
        "post_open_mfe_pct": candidate.get("post_open_mfe_pct"),
        "post_open_mae_pct": candidate.get("post_open_mae_pct"),
        "max_runup_pct": candidate.get("max_runup_pct"),
        "max_drawdown_pct": candidate.get("max_drawdown_pct"),
        "open_to_high_pct": candidate.get("open_to_high_pct"),
        "open_to_close_pct": candidate.get("open_to_close_pct"),
    })


def update_once(market: str, *, offset_min: int, mode: str = "live") -> dict:
    market = "US" if str(market or "").upper() == "US" else "KR"
    runtime_mode = "live" if str(mode or "").lower() == "live" else "paper"
    session_date = resolve_session_date_str(market)
    state = load_preopen_state(market, session_date=session_date, max_age_min=24 * 60, mode=runtime_mode) or {
        "market": market,
        "mode": runtime_mode,
        "session_date": session_date,
        "candidates": [],
    }
    captured_at = datetime.now(KST).isoformat(timespec="seconds")
    updated = 0
    sampled = 0
    token_state = _read_kis_token_state(runtime_mode, market)
    token = str(token_state.get("access_token", "") or "")
    for candidate in state.get("candidates", []) or []:
        record = {
            "market": market,
            "session_date": session_date,
            "ticker": candidate.get("ticker", ""),
            "offset_min": int(offset_min),
            "captured_at": captured_at,
            "outcome_status": "pending_price_provider",
            "token_status": token_state.get("status", "token_unavailable"),
            "post_open_5m_return_pct": candidate.get("post_open_5m_return_pct"),
            "post_open_30m_return_pct": candidate.get("post_open_30m_return_pct"),
            "post_open_60m_return_pct": candidate.get("post_open_60m_return_pct"),
            "post_open_90m_return_pct": candidate.get("post_open_90m_return_pct"),
            "post_open_120m_return_pct": candidate.get("post_open_120m_return_pct"),
            "post_open_mfe_pct": candidate.get("post_open_mfe_pct"),
            "post_open_mae_pct": candidate.get("post_open_mae_pct"),
            "max_runup_pct": candidate.get("max_runup_pct", candidate.get("post_open_mfe_pct")),
            "max_drawdown_pct": candidate.get("max_drawdown_pct", candidate.get("post_open_mae_pct")),
            "open_to_high_pct": candidate.get("open_to_high_pct"),
            "open_to_close_pct": candidate.get("open_to_close_pct"),
        }
        if _should_fetch_price(candidate, state):
            try:
                if market == "KR" and not token:
                    raise RuntimeError("KR token unavailable for price sampling")
                snapshot = _fetch_price_snapshot(market, str(candidate.get("ticker", "") or ""), token)
                _apply_price_outcome(candidate, record, snapshot, captured_at, int(offset_min))
                sampled += 1
            except Exception as exc:
                record["outcome_status"] = "price_provider_error"
                record["price_error"] = str(exc)[:240]
        record["outcome_status"] = _classify_outcome(record)
        candidate[f"outcome_{int(offset_min)}m_captured_at"] = captured_at
        save_outcome_record(market, session_date, record, mode=runtime_mode)
        updated += 1
    state["last_outcome_update_at"] = captured_at
    state["last_outcome_offset_min"] = int(offset_min)
    state["last_outcome_sampled_count"] = sampled
    save_preopen_state(market, state, session_date=session_date, mode=runtime_mode)
    return {
        "market": market,
        "mode": runtime_mode,
        "session_date": session_date,
        "updated": updated,
        "sampled": sampled,
        "offset_min": int(offset_min),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Shadow-only preopen post-open outcome updater")
    parser.add_argument("--market", choices=["US", "KR"], required=True)
    parser.add_argument("--mode", choices=["paper", "live"], default="live")
    parser.add_argument("--offset-min", type=int, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    result = update_once(args.market, offset_min=args.offset_min, mode=args.mode)
    print(
        f"[preopen outcome] mode={result['mode']} {result['market']} {result['session_date']} "
        f"offset={result['offset_min']}m updated={result['updated']} sampled={result['sampled']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
