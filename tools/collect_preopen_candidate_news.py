from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phase1_trainer.digest_builder import build_kr_digest, build_us_digest
from phase1_trainer.preopen_news_targets import load_preopen_news_targets
from preopen.news_enrichment import enrich_preopen_state, save_preopen_news_snapshot


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _resolve_session_date(market: str, session_date: str | None) -> str:
    if session_date:
        return session_date
    try:
        from bot.session_date import resolve_session_date_str

        return resolve_session_date_str(market)
    except Exception:
        from datetime import datetime

        return datetime.now().strftime("%Y-%m-%d")


def _fallback_targets(market: str) -> dict[str, str]:
    market_key = market.upper()
    if market_key == "KR":
        from phase1_trainer.kr_news_collector import TARGET_CORPS

        return dict(TARGET_CORPS)
    if market_key == "US":
        from phase1_trainer.us_news_collector import TARGET_TICKERS

        return {k: v for k, v in TARGET_TICKERS.items() if k not in ("SPY", "QQQ")}
    raise ValueError(f"unsupported market: {market}")


def _corp_news_total(payload: dict[str, Any]) -> int:
    return sum(int(v.get("count", len(v.get("items", []))) or 0) for v in (payload.get("corp_news") or {}).values())


def collect_preopen_candidate_news(
    *,
    market: str,
    session_date: str | None = None,
    mode: str = "live",
    limit: int | None = None,
    max_age_min: int | None = None,
    force: bool = False,
    min_coverage_ratio: float = 0.0,
    min_corp_news_total: int = 0,
    fail_on_empty: bool = False,
) -> dict[str, Any]:
    market_key = market.upper()
    if market_key not in {"KR", "US"}:
        raise ValueError("market must be KR or US")

    target_limit = limit if limit is not None else _env_int("PREOPEN_NEWS_TARGET_LIMIT", 60)
    age_limit = max_age_min if max_age_min is not None else _env_int("PREOPEN_NEWS_STATE_MAX_AGE_MIN", 0)
    day = _resolve_session_date(market_key, session_date)
    started = time.monotonic()

    targets = load_preopen_news_targets(
        market_key,
        day,
        limit=target_limit,
        mode=mode,
        max_age_min=age_limit,
    )
    target_source = "preopen_top60"
    if not targets:
        targets = _fallback_targets(market_key)
        target_source = "fallback_target_corps" if market_key == "KR" else "fallback_target_tickers"

    if market_key == "KR":
        from phase1_trainer import kr_news_collector

        news_payload = kr_news_collector.collect_day(
            day,
            targets=targets,
            force=force,
            target_source=target_source,
        )
        digest = build_kr_digest(day, universe_tickers=list(targets))
    else:
        from phase1_trainer import us_news_collector

        news_payload = us_news_collector.collect_day(
            day,
            targets=targets,
            force=force,
            target_source=target_source,
        )
        digest = build_us_digest(day, universe_tickers=list(targets))

    snapshot_path = ""
    try:
        if isinstance(news_payload, dict) and news_payload:
            snapshot_path = str(save_preopen_news_snapshot(market_key, day, news_payload))
    except Exception:
        snapshot_path = ""
    try:
        state_enrichment = enrich_preopen_state(
            market_key,
            day,
            mode=mode,
            news_payload=news_payload if isinstance(news_payload, dict) else {},
            news_path=snapshot_path,
        )
    except Exception as exc:
        state_enrichment = {"status": "error", "error": str(exc)[:240], "flagged_count": 0}

    elapsed = round(time.monotonic() - started, 2)
    coverage = (news_payload or {}).get("news_coverage", {}) if isinstance(news_payload, dict) else {}
    corp_news_total = _corp_news_total(news_payload or {})
    coverage_ratio = float(coverage.get("coverage_ratio", 0.0) or 0.0)
    flags: list[str] = []
    if corp_news_total <= 0:
        flags.append(f"{market_key.lower()}_news_empty")
    if coverage_ratio < float(min_coverage_ratio or 0.0):
        flags.append(f"{market_key.lower()}_news_coverage_low")
    if corp_news_total < int(min_corp_news_total or 0):
        flags.append(f"{market_key.lower()}_corp_news_total_low")
    if market_key == "KR" and len((news_payload or {}).get("market_news") or []) <= 0:
        flags.append("kr_market_news_missing")
    coverage_status = "empty" if corp_news_total <= 0 else "low_coverage" if flags else "ok"
    ok = not (fail_on_empty and coverage_status == "empty")
    return {
        "ok": ok,
        "market": market_key,
        "session_date": day,
        "mode": mode,
        "target_source": target_source,
        "target_count": len(targets),
        "corp_news_total": corp_news_total,
        "covered_ticker_count": coverage.get("covered_ticker_count", 0),
        "coverage_ratio": coverage_ratio,
        "coverage_status": coverage_status,
        "data_quality_flags": flags,
        "top_news_count": len((digest or {}).get("top_news", [])),
        "digest_path": str(ROOT / "data" / "daily_digest" / f"{day}_{market_key}.json"),
        "preopen_news_snapshot_path": snapshot_path,
        "state_enrichment": state_enrichment,
        "state_news_flagged_count": int((state_enrichment or {}).get("flagged_count", 0) or 0),
        "elapsed_sec": elapsed,
        "force": bool(force),
        "min_coverage_ratio": float(min_coverage_ratio or 0.0),
        "min_corp_news_total": int(min_corp_news_total or 0),
        "fail_on_empty": bool(fail_on_empty),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect preopen candidate news and rebuild daily digest.")
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--mode", default="live")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-age-min", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--min-coverage-ratio", type=float, default=0.0)
    parser.add_argument("--min-corp-news-total", type=int, default=0)
    parser.add_argument("--fail-on-empty", action="store_true")
    args = parser.parse_args(argv)

    summary = collect_preopen_candidate_news(
        market=args.market,
        session_date=args.session_date,
        mode=args.mode,
        limit=args.limit,
        max_age_min=args.max_age_min,
        force=args.force,
        min_coverage_ratio=args.min_coverage_ratio,
        min_corp_news_total=args.min_corp_news_total,
        fail_on_empty=args.fail_on_empty,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("ok", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
