from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.session_date import KST
from phase1_trainer.digest_builder import build_kr_digest, build_us_digest
from phase1_trainer.preopen_news_targets import load_preopen_news_targets
from preopen.scheduler import regular_open_dt
from preopen.news_enrichment import enrich_preopen_state, save_preopen_news_snapshot
from preopen.storage import load_preopen_state, save_candidate_records


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _normalize_ticker(market: str, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if str(market or "").upper() == "US":
        return raw.upper()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits.zfill(6) if digits else raw


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


def _news_entry_count(entry: Any) -> int:
    if not isinstance(entry, dict):
        return 0
    items = entry.get("items") if isinstance(entry.get("items"), list) else []
    try:
        declared = int(entry.get("count", 0) or 0)
    except Exception:
        declared = 0
    return max(declared, len(items))


def _provider_key(source: Any) -> str:
    text = str(source or "").strip()
    lower = text.lower()
    if lower.startswith("kis"):
        return "KIS"
    if "naver" in lower:
        return "Naver"
    if "google" in lower:
        return "GoogleNews"
    if lower.startswith("finnhub"):
        return "Finnhub"
    if lower.startswith("sec"):
        return "SEC EDGAR"
    if lower.startswith("alphavantage") or lower.startswith("alpha vantage"):
        return "AlphaVantage"
    if text.startswith("BigKinds"):
        return "BigKinds"
    return text or "unknown"


def _refresh_payload_metadata(payload: dict[str, Any]) -> None:
    corp_news = payload.get("corp_news") if isinstance(payload.get("corp_news"), dict) else {}
    target_tickers = [
        _normalize_ticker(str(payload.get("market") or ""), ticker)
        for ticker in list(payload.get("target_tickers") or [])
        if str(ticker or "").strip()
    ]
    if not target_tickers:
        target_tickers = [_normalize_ticker(str(payload.get("market") or ""), ticker) for ticker in corp_news.keys()]
    target_tickers = list(dict.fromkeys(ticker for ticker in target_tickers if ticker))

    missing = [
        ticker for ticker in target_tickers
        if _news_entry_count(corp_news.get(ticker)) <= 0
    ]
    covered = len(target_tickers) - len(missing)

    provider_counts: dict[str, int] = {}
    for entry in corp_news.values():
        if not isinstance(entry, dict):
            continue
        for item in entry.get("items") or []:
            if isinstance(item, dict):
                key = _provider_key(item.get("source") or item.get("provider"))
                provider_counts[key] = provider_counts.get(key, 0) + 1
    for item in payload.get("market_news") or []:
        if isinstance(item, dict):
            key = _provider_key(item.get("source") or item.get("provider"))
            provider_counts[key] = provider_counts.get(key, 0) + 1
    disclosures = payload.get("disclosures") if isinstance(payload.get("disclosures"), dict) else {}
    dart_count = sum(len(items or []) for items in disclosures.values())
    if dart_count:
        provider_counts["DART"] = provider_counts.get("DART", 0) + dart_count

    payload["target_count"] = len(target_tickers)
    payload["target_tickers"] = target_tickers
    payload["provider_counts"] = provider_counts
    payload["news_coverage"] = {
        "covered_ticker_count": covered,
        "missing_tickers": missing,
        "coverage_ratio": round(covered / len(target_tickers), 4) if target_tickers else 0.0,
    }


def _parse_dt(value: Any):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _append_enriched_candidate_log_snapshot(
    market: str,
    session_date: str,
    *,
    mode: str,
    state_enrichment: dict[str, Any],
) -> dict[str, Any]:
    if str((state_enrichment or {}).get("status") or "") != "ok":
        return {"candidate_log_appended": False, "candidate_log_append_reason": "enrichment_not_ok"}
    if int((state_enrichment or {}).get("candidate_count") or 0) <= 0:
        return {"candidate_log_appended": False, "candidate_log_append_reason": "no_enriched_candidates"}
    applied_at = str((state_enrichment or {}).get("applied_at") or "").strip()
    applied_dt = _parse_dt(applied_at)
    if applied_dt is None:
        return {"candidate_log_appended": False, "candidate_log_append_reason": "missing_applied_at"}
    if applied_dt > regular_open_dt(market, session_date):
        return {"candidate_log_appended": False, "candidate_log_append_reason": "after_regular_open"}

    state = load_preopen_state(market, session_date=session_date, max_age_min=0, mode=mode)
    candidates = list((state or {}).get("candidates") or [])
    if not candidates:
        return {"candidate_log_appended": False, "candidate_log_append_reason": "state_candidates_missing"}
    log_state = dict(state)
    log_state["captured_at"] = applied_at
    save_candidate_records(market, session_date, candidates, log_state, mode=mode)
    return {
        "candidate_log_appended": True,
        "candidate_log_captured_at": applied_at,
        "candidate_log_candidate_count": len(candidates),
    }


def _news_item_key(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item or "").strip()
    return "|".join(
        str(item.get(key) or "").strip().lower()
        for key in ("url", "title", "date", "published_at")
    )


def _merge_news_payloads(base: dict[str, Any], extra: dict[str, Any], *, market: str) -> dict[str, Any]:
    merged = dict(base or {})
    if not extra:
        return merged
    corp_news = dict(merged.get("corp_news") or {})
    for raw_ticker, raw_entry in (extra.get("corp_news") or {}).items():
        if not isinstance(raw_entry, dict):
            continue
        ticker = _normalize_ticker(market, raw_ticker)
        if not ticker:
            continue
        entry = dict(corp_news.get(ticker) or {})
        if raw_entry.get("name") and not entry.get("name"):
            entry["name"] = raw_entry.get("name")
        items = list(entry.get("items") or [])
        seen = {_news_item_key(item) for item in items if _news_item_key(item)}
        added = 0
        for item in list(raw_entry.get("items") or []):
            key = _news_item_key(item)
            if key and key in seen:
                continue
            items.append(item)
            if key:
                seen.add(key)
            added += 1
        entry["items"] = items
        if items:
            entry["count"] = len(items)
        else:
            entry["count"] = int(entry.get("count", 0) or 0) + int(raw_entry.get("count", 0) or 0)
        corp_news[ticker] = entry
    merged["corp_news"] = corp_news

    market_news = list(merged.get("market_news") or [])
    seen_market = {_news_item_key(item) for item in market_news if _news_item_key(item)}
    for item in list(extra.get("market_news") or []):
        key = _news_item_key(item)
        if key and key in seen_market:
            continue
        market_news.append(item)
        if key:
            seen_market.add(key)
    merged["market_news"] = market_news

    bridge_summary = dict(extra.get("investment_news_bridge") or {})
    if bridge_summary:
        merged["investment_news_bridge"] = bridge_summary
    target_source = str(merged.get("target_source") or "")
    if bridge_summary and "investment_news_db_readonly" not in target_source:
        merged["target_source"] = f"{target_source}+investment_news_db_readonly" if target_source else "investment_news_db_readonly"
    _refresh_payload_metadata(merged)
    return merged


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

    investment_news_bridge: dict[str, Any] = {"enabled": False}
    if _env_bool("PREOPEN_INVESTMENT_NEWS_BRIDGE_ENABLED", True):
        investment_news_bridge = {"enabled": True, "status": "missing"}
        try:
            from preopen.investment_news_bridge import build_preopen_payload_from_investment_news

            bridge_payload = build_preopen_payload_from_investment_news(
                market=market_key,
                session_date=day,
                targets=targets,
                limit=_env_int("PREOPEN_INVESTMENT_NEWS_BRIDGE_LIMIT", 250),
                max_items_per_ticker=_env_int("PREOPEN_INVESTMENT_NEWS_BRIDGE_MAX_ITEMS", 5),
            )
            investment_news_bridge = dict(bridge_payload.get("investment_news_bridge") or {})
            investment_news_bridge["enabled"] = True
            if isinstance(news_payload, dict):
                news_payload = _merge_news_payloads(news_payload, bridge_payload, market=market_key)
            else:
                news_payload = bridge_payload
            investment_news_bridge["status"] = "ok"
        except Exception as exc:
            investment_news_bridge = {
                "enabled": True,
                "status": "error",
                "error": str(exc)[:240],
            }

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
    try:
        candidate_log_append = _append_enriched_candidate_log_snapshot(
            market_key,
            day,
            mode=mode,
            state_enrichment=state_enrichment,
        )
    except Exception as exc:
        candidate_log_append = {
            "candidate_log_appended": False,
            "candidate_log_append_reason": f"error:{type(exc).__name__}:{str(exc)[:160]}",
        }

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
        **candidate_log_append,
        "investment_news_bridge": investment_news_bridge,
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
