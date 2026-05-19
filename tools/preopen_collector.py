from __future__ import annotations

import argparse
import json
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
from preopen.news_enrichment import enrich_candidates_with_news
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


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return float(value)
    except Exception:
        return default


def _preopen_top_n(market: str) -> int:
    raw = os.getenv(f"PREOPEN_{market}_TOP_N", os.getenv("PREOPEN_TOP_N", "60"))
    try:
        return max(1, int(raw))
    except Exception:
        return 60


def _screen_mode(market: str) -> str:
    return os.getenv(f"PREOPEN_{market}_SCREEN_MODE", os.getenv("PREOPEN_SCREEN_MODE", "NEUTRAL")).strip() or "NEUTRAL"


def _token_paths(mode: str, market: str) -> list[Path]:
    market_key = _market_key(market).lower()
    paths = [get_runtime_path("state", f"{mode}_kis_token_{market_key}.json")]
    legacy = get_runtime_path("state", f"{mode}_kis_token.json")
    if market_key == "kr":
        paths.append(legacy)
    else:
        paths.append(legacy)
    return list(dict.fromkeys(paths))


def _read_kis_token_state(mode: str, market: str) -> dict:
    for path in _token_paths(mode, market):
        if not path.exists():
            continue
        try:
            if path.stat().st_size <= 0:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            expires_raw = str(data.get("expires_at", "") or "")
            if not expires_raw:
                return {"status": "token_invalid", "path": str(path), "detail": "missing expires_at"}
            expires_at = datetime.fromisoformat(expires_raw)
            if expires_at.tzinfo is None:
                now = datetime.now()
            else:
                now = datetime.now(tz=expires_at.tzinfo)
            minutes_left = (expires_at - now).total_seconds() / 60.0
            if minutes_left <= 10:
                return {
                    "status": "token_expired",
                    "path": str(path),
                    "detail": f"expires_at={expires_raw}, minutes_left={minutes_left:.1f}",
                }
            return {
                "status": "token_present_read_only",
                "path": str(path),
                "detail": f"expires_at={expires_raw}, minutes_left={minutes_left:.1f}",
                "access_token": str(data.get("access_token", "") or ""),
            }
        except Exception as exc:
            return {"status": "token_invalid", "path": str(path), "detail": str(exc)}
    return {"status": "token_unavailable", "path": "", "detail": "token file not found"}


def _collect_us_seed_candidates(
    tickers: list[str],
    captured_at: str,
    session_date: str,
    *,
    source_status: str,
    data_quality: str,
    stale: bool,
) -> list[dict]:
    candidates = []
    for ticker in tickers:
        candidates.append(normalize_candidate({
            "ticker": ticker,
            "name": ticker,
            "source": "seed_watchlist",
            "provider": "seed_watchlist",
            "source_status": source_status,
            "data_quality": data_quality,
            "stale": stale,
            "quality_tags": ["seed_only"],
            "risk_tags": [],
        }, market="US", session_date=session_date, captured_at=captured_at))
    return candidates


def _collect_kr_seed_candidates(
    tickers: list[str],
    captured_at: str,
    session_date: str,
    *,
    source_status: str,
    data_quality: str,
    stale: bool,
) -> list[dict]:
    candidates = []
    for ticker in tickers:
        candidates.append(normalize_candidate({
            "ticker": ticker,
            "name": ticker,
            "source": "seed_watchlist",
            "provider": "seed_watchlist",
            "source_status": source_status,
            "data_quality": data_quality,
            "stale": stale,
            "quality_tags": ["seed_only"],
            "risk_tags": [],
            "open_volume_confirmation": None,
        }, market="KR", session_date=session_date, captured_at=captured_at))
    return candidates


def _collect_kr_screen_candidates(
    token: str,
    captured_at: str,
    session_date: str,
    *,
    top_n: int,
    mode: str,
) -> list[dict]:
    from kis_api import screen_market_kr

    rows = screen_market_kr(token, top_n=top_n, mode=mode)
    candidates = []
    for idx, row in enumerate(rows or [], start=1):
        price = _safe_float(row.get("price"))
        volume = _safe_float(row.get("volume"))
        change_rate = _safe_float(row.get("change_rate"))
        vol_ratio = _safe_float(row.get("vol_ratio"), 1.0)
        traded_value = price * volume if price > 0 and volume > 0 else 0.0
        candidates.append(normalize_candidate({
            "ticker": row.get("ticker", ""),
            "name": row.get("name", row.get("ticker", "")),
            "source": "kis_screen_market_kr",
            "provider": "kis_volume_rank",
            "provider_rank": idx,
            "screen_score": row.get("screen_score"),
            "source_status": "kis_screen_market_kr",
            "data_quality": "kis_volume_rank",
            "stale": False,
            "quality_tags": ["kis_volume_rank", str(row.get("market_type", "") or "").lower()],
            "risk_tags": [],
            "price": price,
            "extended_price": price,
            "change_rate": change_rate,
            "gap_pct": change_rate,
            "extended_change_pct": change_rate,
            "volume": volume,
            "extended_volume": volume,
            "volume_ratio": vol_ratio,
            "prior_day_traded_value": traded_value,
            "extended_dollar_volume": traded_value,
            "open_volume_confirmation": volume,
        }, market="KR", session_date=session_date, captured_at=captured_at))
    return candidates


def _collect_us_screen_candidates(
    captured_at: str,
    session_date: str,
    *,
    top_n: int,
    mode: str,
) -> list[dict]:
    from kis_api import screen_market_us

    rows = screen_market_us(top_n=top_n, mode=mode)
    candidates = []
    for idx, row in enumerate(rows or [], start=1):
        price = _safe_float(row.get("price"))
        volume = _safe_float(row.get("volume"))
        change_rate = _safe_float(row.get("change_rate"))
        dollar_volume = price * volume if price > 0 and volume > 0 else 0.0
        candidates.append(normalize_candidate({
            "ticker": row.get("ticker", ""),
            "name": row.get("name", row.get("ticker", "")),
            "source": str(row.get("category") or "us_screen_market"),
            "provider": "us_screen_market",
            "provider_rank": idx,
            "source_status": "us_screen_market",
            "data_quality": "us_screen_market",
            "stale": False,
            "quality_tags": ["us_screen_market", str(row.get("category", "") or "").lower()],
            "risk_tags": [],
            "price": price,
            "extended_price": price,
            "change_rate": change_rate,
            "gap_pct": change_rate,
            "extended_change_pct": change_rate,
            "volume": volume,
            "extended_volume": volume,
            "extended_dollar_volume": dollar_volume,
            "volume_ratio": row.get("vol_ratio"),
        }, market="US", session_date=session_date, captured_at=captured_at))
    return candidates


def collect_once(market: str, *, mode: str = "live", tickers: str = "") -> dict:
    market = _market_key(market)
    runtime_mode = "live" if str(mode or "").lower() == "live" else "paper"
    captured_at = datetime.now(KST).isoformat(timespec="seconds")
    session_date = resolve_session_date_str(market)
    seed_tickers = _seed_tickers(market, tickers)
    explicit_tickers = bool(str(tickers or "").strip())
    top_n = _preopen_top_n(market)
    screen_mode = _screen_mode(market)

    token_state = _read_kis_token_state(runtime_mode, market)
    token_status = token_state.get("status", "token_unavailable")
    token_bad = token_status in {"token_expired", "token_invalid"}
    source_status = "seed_only"
    data_quality = "seed_only"
    provider = "seed_watchlist"
    stale = False

    if token_bad:
        raw_candidates = []
        source_status = f"kis_enrichment_skipped_{token_status}"
        data_quality = token_status
        stale = True
    elif market == "KR":
        if token_status == "token_unavailable":
            raw_candidates = []
            source_status = "kis_enrichment_skipped_token_unavailable"
            data_quality = "unavailable"
            stale = True
        elif explicit_tickers:
            source_status = "explicit_seed_tickers"
            raw_candidates = _collect_kr_seed_candidates(
                seed_tickers,
                captured_at,
                session_date,
                source_status=source_status,
                data_quality=data_quality,
                stale=stale,
            )
        else:
            try:
                raw_candidates = _collect_kr_screen_candidates(
                    str(token_state.get("access_token", "") or ""),
                    captured_at,
                    session_date,
                    top_n=top_n,
                    mode=screen_mode,
                )
                source_status = "kis_screen_market_kr"
                data_quality = "kis_volume_rank" if raw_candidates else "no_screen_candidates"
                provider = "kis_volume_rank"
            except Exception as exc:
                source_status = f"kis_screen_failed:{type(exc).__name__}:{str(exc)[:160]}"
                raw_candidates = _collect_kr_seed_candidates(
                    seed_tickers,
                    captured_at,
                    session_date,
                    source_status=source_status,
                    data_quality="seed_only_after_screen_error",
                    stale=True,
                )
                data_quality = "seed_only_after_screen_error"
                stale = True
    else:
        if explicit_tickers:
            source_status = "explicit_seed_tickers"
            raw_candidates = _collect_us_seed_candidates(
                seed_tickers,
                captured_at,
                session_date,
                source_status=source_status,
                data_quality=data_quality,
                stale=stale,
            )
        else:
            try:
                raw_candidates = _collect_us_screen_candidates(
                    captured_at,
                    session_date,
                    top_n=top_n,
                    mode=screen_mode,
                )
                source_status = "us_screen_market"
                data_quality = "us_screen_market" if raw_candidates else "no_screen_candidates"
                provider = "us_screen_market"
            except Exception as exc:
                source_status = f"us_screen_failed:{type(exc).__name__}:{str(exc)[:160]}"
                raw_candidates = _collect_us_seed_candidates(
                    seed_tickers,
                    captured_at,
                    session_date,
                    source_status=source_status,
                    data_quality="seed_only_after_screen_error",
                    stale=True,
                )
                data_quality = "seed_only_after_screen_error"
                stale = True

    candidates = score_candidates(market, raw_candidates)
    try:
        candidates, news_enrichment = enrich_candidates_with_news(
            market,
            candidates,
            session_date=session_date,
            allow_rank_reorder=True,
        )
    except Exception as exc:
        news_enrichment = {"status": "error", "error": str(exc)[:240], "flagged_count": 0}
    collector_status = "ok" if candidates else "no_candidates"
    if token_status in {"token_unavailable", "token_expired", "token_invalid"} and not candidates:
        collector_status = token_status
    state = {
        "market": market,
        "mode": runtime_mode,
        "session_date": session_date,
        "captured_at": captured_at,
        "collector_status": collector_status,
        "collector_mode": "shadow_only",
        "token_status": token_status,
        "token_detail": token_state.get("detail", ""),
        "token_path": token_state.get("path", ""),
        "source_status": source_status,
        "provider": provider,
        "data_quality": data_quality,
        "stale": stale,
        "screen_mode": screen_mode,
        "screen_top_n": top_n,
        "candidate_count": len(candidates),
        "excluded_count": sum(1 for c in candidates if c.get("preopen_grade") == "X"),
        "news_enrichment": news_enrichment,
        "candidates": candidates,
        "notes": [
            "shadow-only collector",
            "does not refresh KIS token",
            "does not affect bot selection or orders",
        ],
    }
    save_preopen_state(market, state, session_date=session_date, mode=runtime_mode)
    save_candidate_records(market, session_date, candidates, state, mode=runtime_mode)
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
