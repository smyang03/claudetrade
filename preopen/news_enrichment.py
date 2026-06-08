from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from bot.session_date import KST
from preopen.news_quality import build_news_quality_snapshot, news_item_id


ROOT = Path(__file__).resolve().parents[1]
NEWS_ROOT = ROOT / "data" / "news"
NEWS_EDGE_PIN_SIGNAL_TYPES = {
    "direct_catalyst",
    "earnings_or_guidance",
    "disclosure_material",
}


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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _market_env_bool(market: str, suffix: str, default: bool) -> bool:
    market_key = _market_key(market)
    raw = os.getenv(f"{market_key}_{suffix}")
    if raw not in (None, ""):
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    return _env_bool(suffix, default)


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).replace(",", ""))
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _candidate_turnover(row: dict[str, Any]) -> float:
    for key in ("extended_dollar_volume", "dollar_volume", "turnover", "prior_day_traded_value"):
        value = _positive_float(row.get(key))
        if value is not None:
            return float(value)
    price = (
        _positive_float(row.get("price"))
        or _positive_float(row.get("extended_price"))
        or _positive_float(row.get("anchor_price"))
        or 0.0
    )
    volume = _positive_float(row.get("volume")) or _positive_float(row.get("extended_volume")) or 0.0
    return float(price) * float(volume)


def _append_quality_tag(row: dict[str, Any], *tags: str) -> None:
    current = list(row.get("quality_tags") or [])
    current.extend(str(tag) for tag in tags if str(tag or "").strip())
    row["quality_tags"] = sorted(set(str(tag) for tag in current if str(tag or "").strip()))


def _clear_news_edge_pin(row: dict[str, Any]) -> None:
    row["preopen_news_edge"] = False
    row["preopen_news_policy"] = ""
    row["preopen_news_edge_reason"] = ""
    stale_tags = {"preopen_news_edge", "news_strict_catalyst"}
    quality_tags = [
        str(tag)
        for tag in row.get("quality_tags") or []
        if str(tag or "").strip() and str(tag) not in stale_tags
    ]
    row["quality_tags"] = sorted(set(quality_tags))
    if str(row.get("preopen_pin_source") or "") == "news_strict_catalyst":
        row["preopen_pinned"] = False
        row["preopen_pin_tier"] = "SOFT"
        row["preopen_pin_require_confirmation"] = False
        row["preopen_pin_reason"] = ""
        row["preopen_pin_source"] = ""
        row.pop("preopen_pin_turnover", None)


def _apply_news_edge_pin(market: str, row: dict[str, Any]) -> None:
    _clear_news_edge_pin(row)
    if not _market_env_bool(market, "PREOPEN_NEWS_PROMPT_PIN_ENABLED", True):
        return
    signal_type = str(row.get("news_signal_type") or "").strip()
    if not bool(row.get("news_prompt_eligible")) or signal_type not in NEWS_EDGE_PIN_SIGNAL_TYPES:
        return
    if str(row.get("risk_news_summary") or "").strip():
        return

    policy = "strict_loss_filter_v1"
    reason = "news_strict_catalyst"
    row["preopen_news_edge"] = True
    row["preopen_news_policy"] = policy
    row["preopen_news_edge_reason"] = reason
    _append_quality_tag(row, "preopen_news_edge", reason)

    if not bool(row.get("preopen_pinned")) and str(row.get("preopen_pin_tier") or "").strip().upper() != "HARD":
        row["preopen_pinned"] = True
        row["preopen_pin_tier"] = "HARD"
        row["preopen_pin_require_confirmation"] = _market_env_bool(
            market,
            "PREOPEN_NEWS_PROMPT_PIN_REQUIRE_CONFIRMATION",
            True,
        )
        row["preopen_pin_reason"] = reason
        row["preopen_pin_source"] = reason
        turnover = _candidate_turnover(row)
        if turnover > 0:
            row["preopen_pin_turnover"] = round(turnover, 2)


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


def _entry_name(entry: dict[str, Any]) -> str:
    for key in ("name", "company_name", "corp_name", "stock_name"):
        value = str((entry or {}).get(key) or "").strip()
        if value:
            return value
    return ""


def _payload_date(payload: dict[str, Any]) -> str:
    for key in ("date", "session_date", "target_date"):
        value = _date_part(str((payload or {}).get(key) or ""))
        if value:
            return value
    return ""


def _item_date_part(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("date", "published_at", "report_date", "rcept_dt"):
        text = str(item.get(key) or "").strip()
        if not text:
            continue
        if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
            return text[:10]
        if len(text) >= 8 and text[:8].isdigit():
            return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return ""


def _item_date_quality(item: Any, payload_date: str) -> str:
    item_date = _item_date_part(item)
    if not item_date:
        return "unknown_date"
    if payload_date and item_date != payload_date:
        return "stale"
    return "dated"


def _item_matches_payload_date(item: Any, payload_date: str) -> bool:
    return _item_date_quality(item, payload_date) != "stale"


_US_NAME_STOPWORDS = {
    "INC",
    "INCORPORATED",
    "CORP",
    "CORPORATION",
    "COMPANY",
    "CO",
    "LTD",
    "PLC",
    "HOLDINGS",
    "GROUP",
    "THE",
    "AND",
    "CLASS",
    "COMMON",
    "STOCK",
}


def _name_tokens(name: str) -> list[str]:
    tokens = [token.upper() for token in re.findall(r"[A-Za-z0-9]+", str(name or ""))]
    return [token for token in tokens if len(token) >= 3 and token not in _US_NAME_STOPWORDS][:4]


def _title_mentions_ticker_or_name(market: str, ticker: str, name: str, item: Any) -> bool:
    title = _title_text(item)
    if not title:
        return False
    market_key = _market_key(market)
    ticker_key = _normalize_ticker(market_key, ticker)
    if market_key == "US":
        upper_title = title.upper()
        if ticker_key and re.search(rf"(?<![A-Z0-9]){re.escape(ticker_key)}(?![A-Z0-9])", upper_title):
            return True
        return any(token in upper_title for token in _name_tokens(name))
    if ticker_key and ticker_key in title:
        return True
    compact_name = str(name or "").strip()
    return bool(compact_name and compact_name in title)


def _related_ticker_match(market: str, ticker: str, item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    ticker_key = _normalize_ticker(market, ticker)
    values: list[Any] = []
    for key in ("ticker", "symbol", "tickers", "related_tickers", "ticker_sentiment"):
        raw = item.get(key)
        if isinstance(raw, list):
            values.extend(raw)
        elif raw not in (None, ""):
            values.append(raw)
    for value in values:
        if isinstance(value, dict):
            raw = value.get("ticker") or value.get("symbol") or value.get("iscd")
        else:
            raw = value
        if _normalize_ticker(market, raw) == ticker_key:
            return True
    return False


def _item_is_broad_weak(market: str, ticker: str, name: str, item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    source = _source_name(item).lower()
    if any(marker in source for marker in ("sec", "dart")):
        return False
    if _related_ticker_match(market, ticker, item) and any(
        marker in source for marker in ("finnhub", "alphavantage", "alpha vantage", "kis")
    ):
        return False
    if "kis" in source and str(item.get("matched_by") or "") == "kis_iscd":
        return False
    if _title_mentions_ticker_or_name(market, ticker, name, item):
        return False
    return any(marker in source for marker in ("finnhub", "alphavantage", "alpha vantage", "news", "naver", "google"))


def _empty_filter_summary(payload_date: str) -> dict[str, Any]:
    return {
        "payload_date": payload_date,
        "raw_corp_item_count": 0,
        "raw_disclosure_item_count": 0,
        "usable_corp_item_count": 0,
        "usable_disclosure_item_count": 0,
        "stale_filtered_count": 0,
        "unknown_date_count": 0,
        "broad_weak_count": 0,
    }


def _filter_items_for_payload_date(
    items: list[Any],
    payload_date: str,
    *,
    summary: dict[str, Any],
    raw_count_key: str,
    usable_count_key: str,
    market: str = "",
    ticker: str = "",
    name: str = "",
    classify_broad: bool = False,
) -> tuple[list[Any], dict[str, int]]:
    filtered: list[Any] = []
    counters = {"unknown_date": 0, "broad_weak": 0}
    summary[raw_count_key] = int(summary.get(raw_count_key, 0) or 0) + len(items)
    for item in items:
        date_quality = _item_date_quality(item, payload_date)
        if date_quality == "stale":
            summary["stale_filtered_count"] = int(summary.get("stale_filtered_count", 0) or 0) + 1
            continue
        if date_quality == "unknown_date":
            counters["unknown_date"] += 1
            summary["unknown_date_count"] = int(summary.get("unknown_date_count", 0) or 0) + 1
        if classify_broad and _item_is_broad_weak(market, ticker, name, item):
            counters["broad_weak"] += 1
            summary["broad_weak_count"] = int(summary.get("broad_weak_count", 0) or 0) + 1
        filtered.append(item)
    summary[usable_count_key] = int(summary.get(usable_count_key, 0) or 0) + len(filtered)
    return filtered, counters


def _quality_from_counts(count: int, weak_count: int) -> str:
    if count <= 0:
        return ""
    if weak_count >= count:
        return "weak"
    if weak_count > 0:
        return "mixed"
    return "normal"


def _date_quality_from_counts(count: int, unknown_date_count: int) -> str:
    if count <= 0:
        return ""
    if unknown_date_count >= count:
        return "unknown_date"
    if unknown_date_count > 0:
        return "mixed_date"
    return "dated"


def _add_hit(
    index: dict[str, dict[str, Any]],
    ticker: str,
    *,
    count: int,
    source: str,
    sample_title: str = "",
    weak_count: int = 0,
    unknown_date_count: int = 0,
) -> None:
    if not ticker or count <= 0:
        return
    info = index.setdefault(
        ticker,
        {
            "count": 0,
            "sources": set(),
            "sample_title": "",
            "weak_count": 0,
            "unknown_date_count": 0,
            "quality_tags": set(),
        },
    )
    info["count"] = int(info.get("count", 0) or 0) + int(count)
    info["weak_count"] = int(info.get("weak_count", 0) or 0) + max(0, int(weak_count or 0))
    info["unknown_date_count"] = int(info.get("unknown_date_count", 0) or 0) + max(0, int(unknown_date_count or 0))
    if weak_count > 0:
        info["quality_tags"].add("broad_weak")
    if unknown_date_count > 0:
        info["quality_tags"].add("unknown_date")
    if source:
        info["sources"].add(str(source))
    if sample_title and not info.get("sample_title"):
        info["sample_title"] = sample_title


def build_news_index_with_summary(
    market: str,
    payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    market_key = _market_key(market)
    index: dict[str, dict[str, Any]] = {}
    quality_items: dict[str, list[Any]] = {}
    date_quality_by_id: dict[str, str] = {}
    broad_weak_ids: set[str] = set()
    names_by_ticker: dict[str, str] = {}
    payload_date = _payload_date(payload)
    summary = _empty_filter_summary(payload_date)
    corp_news = payload.get("corp_news") if isinstance(payload, dict) else {}
    if isinstance(corp_news, dict):
        for raw_ticker, entry in corp_news.items():
            if not isinstance(entry, dict):
                continue
            ticker = _normalize_ticker(market_key, raw_ticker)
            name = _entry_name(entry)
            if name:
                names_by_ticker[ticker] = name
            raw_items = entry.get("items") or []
            if not isinstance(raw_items, list):
                raw_items = []
            items, counters = _filter_items_for_payload_date(
                raw_items,
                payload_date,
                summary=summary,
                raw_count_key="raw_corp_item_count",
                usable_count_key="usable_corp_item_count",
                market=market_key,
                ticker=ticker,
                name=name,
                classify_broad=True,
            )
            if raw_items:
                count = len(items)
                bucket = quality_items.setdefault(ticker, [])
                for item in items:
                    item_id = news_item_id(item)
                    date_quality_by_id[item_id] = _item_date_quality(item, payload_date)
                    if _item_is_broad_weak(market_key, ticker, name, item):
                        broad_weak_ids.add(item_id)
                    bucket.append(item)
            else:
                try:
                    count = int(entry.get("count", 0) or 0)
                except Exception:
                    count = 0
                if count > 0:
                    counters = {"unknown_date": count, "broad_weak": 0}
                    summary["unknown_date_count"] = int(summary.get("unknown_date_count", 0) or 0) + count
                    summary["usable_corp_item_count"] = int(summary.get("usable_corp_item_count", 0) or 0) + count
            if count <= 0:
                continue
            sources = sorted(set(src for src in (_source_name(item) for item in items) if src)) or ["news"]
            sample_title = next((_title_text(item) for item in items if _title_text(item)), "")
            _add_hit(
                index,
                ticker,
                count=count,
                source=sources[0],
                sample_title=sample_title,
                weak_count=counters.get("broad_weak", 0),
                unknown_date_count=counters.get("unknown_date", 0),
            )
            index[ticker]["sources"].update(sources[1:])

    disclosures = payload.get("disclosures") if isinstance(payload, dict) else {}
    if isinstance(disclosures, dict):
        for raw_ticker, items in disclosures.items():
            ticker = _normalize_ticker(market_key, raw_ticker)
            raw_rows = items if isinstance(items, list) else []
            rows, counters = _filter_items_for_payload_date(
                raw_rows,
                payload_date,
                summary=summary,
                raw_count_key="raw_disclosure_item_count",
                usable_count_key="usable_disclosure_item_count",
            )
            if not rows:
                continue
            bucket = quality_items.setdefault(ticker, [])
            for row in rows:
                item_id = news_item_id(row)
                date_quality_by_id[item_id] = _item_date_quality(row, payload_date)
                bucket.append(row)
            sample_title = next((_title_text(item) for item in rows if _title_text(item)), "")
            _add_hit(
                index,
                ticker,
                count=len(rows),
                source="DART",
                sample_title=sample_title,
                unknown_date_count=counters.get("unknown_date", 0),
            )

    normalized: dict[str, dict[str, Any]] = {}
    for ticker, info in index.items():
        sources = sorted(str(src) for src in (info.get("sources") or set()) if str(src or "").strip())
        count = int(info.get("count", 0) or 0)
        weak_count = int(info.get("weak_count", 0) or 0)
        unknown_date_count = int(info.get("unknown_date_count", 0) or 0)
        quality_tags = sorted(str(tag) for tag in (info.get("quality_tags") or set()) if str(tag or "").strip())
        normalized[ticker] = {
            "count": count,
            "sources": sources,
            "sample_title": str(info.get("sample_title") or ""),
            "news_quality": _quality_from_counts(count, weak_count),
            "news_date_quality": _date_quality_from_counts(count, unknown_date_count),
            "news_quality_tags": quality_tags,
            "weak_count": weak_count,
            "unknown_date_count": unknown_date_count,
        }
        snapshot = build_news_quality_snapshot(
            market_key,
            ticker,
            names_by_ticker.get(ticker, ""),
            quality_items.get(ticker, []),
            date_quality_by_id=date_quality_by_id,
            broad_weak_ids=broad_weak_ids,
        )
        normalized[ticker].update(snapshot)
    summary["news_ticker_count"] = len(normalized)
    summary["usable_item_count"] = int(summary.get("usable_corp_item_count", 0) or 0) + int(
        summary.get("usable_disclosure_item_count", 0) or 0
    )
    return normalized, summary


def build_news_index(market: str, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index, _summary = build_news_index_with_summary(market, payload)
    return index


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

    index, filter_summary = build_news_index_with_summary(market_key, payload)
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
            row["news_quality"] = str(hit.get("news_quality") or "normal")
            row["news_date_quality"] = str(hit.get("news_date_quality") or "dated")
            row["news_quality_tags"] = list(hit.get("news_quality_tags") or [])
            row["news_prompt_eligible"] = bool(hit.get("news_prompt_eligible"))
            row["news_signal_type"] = str(hit.get("news_signal_type") or "")
            row["news_score"] = int(hit.get("news_score") or 0)
            row["news_prompt_summary"] = str(hit.get("news_prompt_summary") or "")
            row["risk_news_summary"] = str(hit.get("risk_news_summary") or "")
            row["prompt_news_ids"] = list(hit.get("prompt_news_ids") or [])
            row["top_news"] = list(hit.get("top_news") or [])
            row["risk_news"] = list(hit.get("risk_news") or [])
            row["excluded_news_counts"] = dict(hit.get("excluded_news_counts") or {})
            row["scored_news_count"] = int(hit.get("scored_news_count") or 0)
            quality_tags = list(row.get("quality_tags") or [])
            quality_tags.append("news_or_earnings")
            quality_tags.extend(list(hit.get("news_quality_tags") or []))
            if row["news_prompt_eligible"]:
                quality_tags.append("news_prompt_eligible")
            if row["news_signal_type"]:
                prefix = "news_signal" if row["news_prompt_eligible"] else "news_signal_unscored"
                quality_tags.append(f"{prefix}_{row['news_signal_type']}")
            row["quality_tags"] = sorted(set(str(tag) for tag in quality_tags if str(tag or "").strip()))
            _apply_news_edge_pin(market_key, row)
        else:
            row["news_or_earnings_flag"] = False
            row["news_or_earnings_count"] = 0
            row["news_or_earnings_sources"] = []
            row["news_or_earnings_sample_title"] = ""
            row["news_quality"] = ""
            row["news_date_quality"] = ""
            row["news_quality_tags"] = []
            row["news_prompt_eligible"] = False
            row["news_signal_type"] = ""
            row["news_score"] = 0
            row["news_prompt_summary"] = ""
            row["risk_news_summary"] = ""
            row["prompt_news_ids"] = []
            row["top_news"] = []
            row["risk_news"] = []
            row["excluded_news_counts"] = {}
            row["scored_news_count"] = 0
            _clear_news_edge_pin(row)
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
        "news_prompt_eligible_count": sum(1 for row in enriched if bool(row.get("news_prompt_eligible"))),
        "news_edge_count": sum(1 for row in enriched if bool(row.get("preopen_news_edge"))),
        "news_prompt_pin_count": sum(
            1
            for row in enriched
            if bool(row.get("preopen_news_edge"))
            and (
                bool(row.get("preopen_pinned"))
                or str(row.get("preopen_pin_tier") or "").strip().upper() == "HARD"
            )
        ),
        "risk_news_count": sum(1 for row in enriched if str(row.get("news_signal_type") or "") == "risk_negative"),
        "news_ticker_count": len(index),
        "news_filter_summary": filter_summary,
        "stale_filtered_count": int(filter_summary.get("stale_filtered_count", 0) or 0),
        "unknown_date_count": int(filter_summary.get("unknown_date_count", 0) or 0),
        "broad_weak_count": int(filter_summary.get("broad_weak_count", 0) or 0),
        "usable_item_count": int(filter_summary.get("usable_item_count", 0) or 0),
        "payload_date": str(filter_summary.get("payload_date") or ""),
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
