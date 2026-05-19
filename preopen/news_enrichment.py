from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from bot.session_date import KST


ROOT = Path(__file__).resolve().parents[1]
NEWS_ROOT = ROOT / "data" / "news"


def _market_key(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _date_part(session_date: str) -> str:
    return str(session_date or "").strip()[:10]


def _normalize_ticker(market: str, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if _market_key(market) == "US":
        return raw.upper()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits.zfill(6) if digits else raw


def preopen_news_snapshot_path(
    market: str,
    session_date: str,
    *,
    news_root: Optional[Path] = None,
) -> Path:
    root = news_root or NEWS_ROOT
    return root / _market_key(market).lower() / f"{_date_part(session_date)}_preopen.json"


def regular_news_path(
    market: str,
    session_date: str,
    *,
    news_root: Optional[Path] = None,
) -> Path:
    root = news_root or NEWS_ROOT
    return root / _market_key(market).lower() / f"{_date_part(session_date)}.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_preopen_news_payload(
    market: str,
    session_date: str,
    *,
    news_root: Optional[Path] = None,
) -> tuple[dict[str, Any], str]:
    for path in (
        preopen_news_snapshot_path(market, session_date, news_root=news_root),
        regular_news_path(market, session_date, news_root=news_root),
    ):
        if path.exists():
            payload = _read_json(path)
            if payload:
                return payload, str(path)
    return {}, ""


def save_preopen_news_snapshot(
    market: str,
    session_date: str,
    payload: dict[str, Any],
    *,
    news_root: Optional[Path] = None,
) -> Path:
    path = preopen_news_snapshot_path(market, session_date, news_root=news_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(payload or {})
    data["preopen_snapshot"] = True
    data.setdefault("market", _market_key(market))
    data.setdefault("date", _date_part(session_date))
    data["snapshot_written_at"] = datetime.now(KST).isoformat(timespec="seconds")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def _source_name(item: Any, fallback: str = "news") -> str:
    if isinstance(item, dict):
        raw = item.get("source") or item.get("provider") or fallback
    else:
        raw = fallback
    text = str(raw or fallback).strip()
    return text or fallback


def _title_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("title", "headline", "report_nm", "name"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
    return str(item or "").strip()


def _add_hit(
    index: dict[str, dict[str, Any]],
    ticker: str,
    *,
    count: int,
    source: str,
    sample_title: str = "",
) -> None:
    if not ticker or count <= 0:
        return
    info = index.setdefault(
        ticker,
        {"count": 0, "sources": set(), "sample_title": ""},
    )
    info["count"] = int(info.get("count", 0) or 0) + int(count)
    if source:
        info["sources"].add(str(source))
    if sample_title and not info.get("sample_title"):
        info["sample_title"] = sample_title


def build_news_index(market: str, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    market_key = _market_key(market)
    index: dict[str, dict[str, Any]] = {}
    corp_news = payload.get("corp_news") if isinstance(payload, dict) else {}
    if isinstance(corp_news, dict):
        for raw_ticker, entry in corp_news.items():
            if not isinstance(entry, dict):
                continue
            ticker = _normalize_ticker(market_key, raw_ticker)
            items = entry.get("items") or []
            if not isinstance(items, list):
                items = []
            try:
                count = int(entry.get("count", len(items)) or 0)
            except Exception:
                count = len(items)
            count = max(count, len(items))
            if count <= 0:
                continue
            sources = sorted(set(src for src in (_source_name(item) for item in items) if src)) or ["news"]
            sample_title = next((_title_text(item) for item in items if _title_text(item)), "")
            _add_hit(index, ticker, count=count, source=sources[0], sample_title=sample_title)
            index[ticker]["sources"].update(sources[1:])

    disclosures = payload.get("disclosures") if isinstance(payload, dict) else {}
    if isinstance(disclosures, dict):
        for raw_ticker, items in disclosures.items():
            ticker = _normalize_ticker(market_key, raw_ticker)
            rows = items if isinstance(items, list) else []
            if not rows:
                continue
            sample_title = next((_title_text(item) for item in rows if _title_text(item)), "")
            _add_hit(index, ticker, count=len(rows), source="DART", sample_title=sample_title)

    normalized: dict[str, dict[str, Any]] = {}
    for ticker, info in index.items():
        sources = sorted(str(src) for src in (info.get("sources") or set()) if str(src or "").strip())
        normalized[ticker] = {
            "count": int(info.get("count", 0) or 0),
            "sources": sources,
            "sample_title": str(info.get("sample_title") or ""),
        }
    return normalized


def _score_without_rank_reorder(market: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from preopen.scorer import score_kr_candidate, score_us_candidate

    scorer = score_us_candidate if _market_key(market) == "US" else score_kr_candidate
    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        original_rank = candidate.get("shadow_preopen_rank")
        row = scorer(dict(candidate))
        row["shadow_preopen_rank"] = original_rank
        scored.append(row)
    return scored


def enrich_candidates_with_news(
    market: str,
    candidates: list[dict[str, Any]],
    *,
    session_date: str = "",
    news_payload: Optional[dict[str, Any]] = None,
    news_path: str = "",
    allow_rank_reorder: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    market_key = _market_key(market)
    payload = news_payload
    source_path = news_path
    if payload is None and session_date:
        payload, source_path = load_preopen_news_payload(market_key, session_date)
    if not payload:
        return [dict(row or {}) for row in candidates], {
            "status": "missing_news_payload",
            "candidate_count": len(candidates or []),
            "flagged_count": 0,
            "news_path": source_path,
            "allow_rank_reorder": bool(allow_rank_reorder),
        }

    index = build_news_index(market_key, payload)
    enriched: list[dict[str, Any]] = []
    flagged = 0
    changed = 0
    for raw in candidates or []:
        row = dict(raw or {})
        ticker = _normalize_ticker(market_key, row.get("ticker"))
        hit = index.get(ticker)
        before = (
            bool(row.get("news_or_earnings_flag")),
            int(row.get("news_or_earnings_count") or 0),
        )
        if hit:
            flagged += 1
            row["news_or_earnings_flag"] = True
            row["news_or_earnings_count"] = int(hit.get("count", 0) or 0)
            row["news_or_earnings_sources"] = list(hit.get("sources") or [])
            row["news_or_earnings_sample_title"] = str(hit.get("sample_title") or "")
            quality_tags = list(row.get("quality_tags") or [])
            quality_tags.append("news_or_earnings")
            row["quality_tags"] = sorted(set(str(tag) for tag in quality_tags if str(tag or "").strip()))
        else:
            row["news_or_earnings_flag"] = False
            row["news_or_earnings_count"] = 0
            row["news_or_earnings_sources"] = []
            row["news_or_earnings_sample_title"] = ""
        after = (
            bool(row.get("news_or_earnings_flag")),
            int(row.get("news_or_earnings_count") or 0),
        )
        if before != after:
            changed += 1
        enriched.append(row)

    if allow_rank_reorder:
        from preopen.scorer import score_candidates

        enriched = score_candidates(market_key, enriched)
    else:
        enriched = _score_without_rank_reorder(market_key, enriched)

    return enriched, {
        "status": "ok",
        "candidate_count": len(candidates or []),
        "flagged_count": flagged,
        "changed_count": changed,
        "news_ticker_count": len(index),
        "news_path": source_path,
        "target_source": str(payload.get("target_source") or ""),
        "allow_rank_reorder": bool(allow_rank_reorder),
    }


def enrich_preopen_state(
    market: str,
    session_date: str,
    *,
    mode: str = "live",
    news_payload: Optional[dict[str, Any]] = None,
    news_path: str = "",
) -> dict[str, Any]:
    from preopen.storage import load_preopen_state, save_preopen_state

    market_key = _market_key(market)
    state = load_preopen_state(market_key, session_date=session_date, max_age_min=0, mode=mode)
    candidates = list((state or {}).get("candidates") or [])
    if not state or not candidates:
        return {
            "status": "no_preopen_state",
            "candidate_count": 0,
            "flagged_count": 0,
            "news_path": news_path,
        }
    allow_rank_reorder = not bool(state.get("last_outcome_update_at"))
    enriched, summary = enrich_candidates_with_news(
        market_key,
        candidates,
        session_date=session_date,
        news_payload=news_payload,
        news_path=news_path,
        allow_rank_reorder=allow_rank_reorder,
    )
    if summary.get("status") != "ok":
        return summary
    state = dict(state)
    state["candidates"] = enriched
    state["candidate_count"] = len(enriched)
    state["news_enrichment"] = {
        **summary,
        "applied_at": datetime.now(KST).isoformat(timespec="seconds"),
    }
    save_preopen_state(market_key, state, session_date=session_date, mode=mode)
    return state["news_enrichment"]
