from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


CORE_SIGNAL_TYPES = {
    "direct_catalyst",
    "earnings_or_guidance",
    "disclosure_material",
    "analyst_or_report",
}
RISK_SIGNAL_TYPES = {"risk_negative"}
WEAK_SIGNAL_TYPES = {"theme_broad", "weak_generic", "price_action_only"}

HIGH_EVENT_TERMS = (
    "contract",
    "supply",
    "partnership",
    "approval",
    "fda",
    "clinical",
    "trial",
    "merger",
    "acquisition",
    "m&a",
    "investment",
    "capex",
    "guidance",
    "earnings",
    "buyback",
    "dividend",
    "수주",
    "공급계약",
    "계약",
    "공급",
    "협력",
    "파트너십",
    "승인",
    "허가",
    "임상",
    "합병",
    "인수",
    "투자",
    "증설",
    "실적",
    "가이던스",
    "자사주",
    "배당",
)
TECH_GROWTH_TERMS = (
    "launch",
    "unveil",
    "develop",
    "commercial",
    "breakthrough",
    "robot",
    "ai",
    "semiconductor",
    "hbm",
    "data center",
    "cloud",
    "신제품",
    "신규",
    "개발",
    "상용화",
    "양산",
    "기술",
    "로봇",
    "휴머노이드",
    "반도체",
    "데이터센터",
    "클라우드",
    "성장",
)
REPORT_TERMS = (
    "price target",
    "upgrade",
    "outperform",
    "overweight",
    "initiates",
    "analyst",
    "목표가",
    "리포트",
    "증권사",
    "투자의견",
    "상향",
    "커버리지",
)
RISK_TERMS = (
    "lawsuit",
    "investigation",
    "bankruptcy",
    "delisting",
    "trading halt",
    "offering",
    "dilution",
    "recall",
    "guidance cut",
    "misses",
    "소송",
    "조사",
    "상장폐지",
    "거래정지",
    "불성실공시",
    "유상증자",
    "전환사채",
    "CB",
    "BW",
    "리콜",
    "실적 쇼크",
    "적자",
    "횡령",
    "배임",
    "매각 무산",
)
PRICE_ACTION_TERMS = (
    "shares rise",
    "shares fall",
    "stock moves",
    "why shares",
    "특징주",
    "급등",
    "급락",
    "상한가",
    "강세",
    "약세",
    "신고가",
)
GENERIC_TERMS = (
    "주간 이슈",
    "테마 스케줄",
    "관련주",
    "증시",
    "코스피",
    "코스닥",
    "나스닥",
    "s&p",
    "market today",
    "stocks to watch",
    "watchlist",
)
SOURCE_TYPE_CORE = {
    "technology",
    "investment",
    "growth",
    "company_news",
    "earnings",
    "disclosure",
}
SOURCE_TYPE_WEAK = {"macro", "industry", "market_data", "news"}


def compact_news_text(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").replace("|", " ").split())
    if len(text) <= limit:
        return text
    return text[: max(1, int(limit) - 3)].rstrip() + "..."


def news_item_id(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("news_id", "id", "source_id", "url"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
        seed = "|".join(
            str(item.get(key) or "")
            for key in ("source", "provider", "title", "published_at", "date")
        )
    else:
        seed = str(item or "")
    return "news_" + hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:16]


def normalize_ticker(market: str, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if str(market or "").upper() == "US":
        return raw.upper()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits.zfill(6) if digits else raw


def item_title(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("title", "headline", "report_nm", "name"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
    return str(item or "").strip()


def item_text(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item or "")
    return " ".join(
        str(item.get(key) or "")
        for key in ("title", "headline", "summary", "content", "description", "reason")
        if item.get(key)
    )


def source_name(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("source") or item.get("provider") or "news").strip() or "news"
    return "news"


def source_type(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("source_type") or "").strip().lower()
    return ""


def importance_rank(value: Any) -> int:
    return {"S": 0, "A": 1, "B": 2, "C": 3}.get(str(value or "C").strip().upper(), 3)


def parse_news_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) >= 8 and text[:8].isdigit():
        try:
            return datetime.strptime(text[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(str(term).lower() in lowered for term in terms)


def _related_ticker_match(market: str, ticker: str, item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    ticker_key = normalize_ticker(market, ticker)
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
        if normalize_ticker(market, raw) == ticker_key:
            return True
    return False


def _direct_text_match(market: str, ticker: str, name: str, item: Any, aliases: list[str] | None = None) -> bool:
    text = item_text(item)
    if not text:
        return False
    return _direct_match_text(market, ticker, name, text, aliases=aliases)


def _direct_title_match(market: str, ticker: str, name: str, item: Any, aliases: list[str] | None = None) -> bool:
    title = item_title(item)
    if not title:
        return False
    return _direct_match_text(market, ticker, name, title, aliases=aliases)


def _direct_match_text(market: str, ticker: str, name: str, text: str, aliases: list[str] | None = None) -> bool:
    market_key = str(market or "").upper()
    ticker_key = normalize_ticker(market_key, ticker)
    if market_key == "US":
        upper = text.upper()
        if ticker_key and re.search(rf"(?<![A-Z0-9]){re.escape(ticker_key)}(?![A-Z0-9])", upper):
            return True
        names = [name, *(aliases or [])]
        return any(str(token or "").strip() and str(token).strip().upper() in upper for token in names)
    if ticker_key and ticker_key in text:
        return True
    names = [name, *(aliases or [])]
    return any(str(token or "").strip() and str(token).strip() in text for token in names)


def score_news_item(
    market: str,
    ticker: str,
    name: str,
    item: Any,
    *,
    aliases: list[str] | None = None,
    date_quality: str = "dated",
    broad_weak: bool = False,
) -> dict[str, Any]:
    market_key = "US" if str(market or "").upper() == "US" else "KR"
    title = item_title(item)
    text = item_text(item)
    source = source_name(item)
    src_type = source_type(item)
    ticker_match = _related_ticker_match(market_key, ticker, item)
    text_match = _direct_text_match(
        market_key,
        ticker,
        name,
        item,
        aliases=aliases,
    )
    title_match = _direct_title_match(
        market_key,
        ticker,
        name,
        item,
        aliases=aliases,
    )
    source_lower = source.lower()
    noisy_web_source = bool(
        source_lower in {"naver", "googlenews", "google news", "investmentnews"}
        or (isinstance(item, dict) and str(item.get("news_id") or "").startswith("investment_news:"))
    )
    metadata_direct = bool(
        ticker_match
        and (
            source_lower in {"dart", "sec", "sec edgar", "kis", "alphavantage", "alpha vantage", "finnhub"}
            or (isinstance(item, dict) and str(item.get("matched_by") or "") == "kis_iscd")
        )
    )
    direct_match = bool(title_match or metadata_direct or (text_match and not noisy_web_source))
    report = src_type == "research_report" or _contains_any(text, REPORT_TERMS)
    risk = _contains_any(text, RISK_TERMS)
    price_action = _contains_any(text, PRICE_ACTION_TERMS)
    generic = broad_weak or src_type in SOURCE_TYPE_WEAK or _contains_any(text, GENERIC_TERMS)
    strong_event = src_type in {"earnings", "disclosure", "company_news"} or _contains_any(text, HIGH_EVENT_TERMS)
    tech_growth = src_type in {"technology", "investment", "growth"} or _contains_any(text, TECH_GROWTH_TERMS)

    score = 0
    reasons: list[str] = []
    if direct_match:
        score += 30
        reasons.append("direct_match")
    else:
        score -= 25
        reasons.append("no_direct_match")

    rank = importance_rank(item.get("importance") if isinstance(item, dict) else "")
    if rank == 0:
        score += 18
        reasons.append("importance_s")
    elif rank == 1:
        score += 12
        reasons.append("importance_a")
    elif rank == 2:
        score += 5
        reasons.append("importance_b")

    if strong_event:
        score += 30
        reasons.append("material_event")
    elif tech_growth:
        score += 22
        reasons.append("technology_or_growth")
    elif report:
        score += 14
        reasons.append("analyst_or_report")

    if src_type in SOURCE_TYPE_CORE:
        score += 8
    if source.lower() in {"dart", "sec", "sec edgar", "kis", "alphavantage", "finnhub"}:
        score += 6
        reasons.append("trusted_source")

    if date_quality == "unknown_date":
        score -= 8
        reasons.append("unknown_date")
    if generic:
        score -= 25
        reasons.append("generic_or_broad")
    if price_action and not strong_event:
        score -= 12
        reasons.append("price_action_only")
    if risk:
        score += 10 if direct_match else 0
        reasons.append("risk_event")

    if risk:
        signal_type = "risk_negative"
        prompt_eligible = direct_match and score >= 35
    elif direct_match and strong_event:
        signal_type = "earnings_or_guidance" if src_type == "earnings" else "disclosure_material" if src_type == "disclosure" else "direct_catalyst"
        prompt_eligible = score >= 60
    elif direct_match and tech_growth:
        signal_type = "direct_catalyst"
        prompt_eligible = score >= 60
    elif direct_match and report:
        signal_type = "analyst_or_report"
        prompt_eligible = score >= 55
    elif price_action:
        signal_type = "price_action_only"
        prompt_eligible = False
    elif generic:
        signal_type = "theme_broad" if broad_weak else "weak_generic"
        prompt_eligible = False
    else:
        signal_type = "weak_generic"
        prompt_eligible = False

    summary = compact_news_text(str(item.get("summary") or item.get("content") or title) if isinstance(item, dict) else title, 180)
    return {
        "id": news_item_id(item),
        "source": source,
        "source_type": src_type,
        "title": compact_news_text(title, 180),
        "summary": summary,
        "url": str(item.get("url") or "") if isinstance(item, dict) else "",
        "published_at": str(item.get("published_at") or item.get("date") or "") if isinstance(item, dict) else "",
        "date_quality": date_quality,
        "signal_type": signal_type,
        "score": max(0, min(100, int(round(score)))),
        "prompt_eligible": bool(prompt_eligible),
        "direct_match": bool(direct_match),
        "reasons": reasons[:8],
    }


def build_news_quality_snapshot(
    market: str,
    ticker: str,
    name: str,
    items: list[Any],
    *,
    aliases: list[str] | None = None,
    date_quality_by_id: dict[str, str] | None = None,
    broad_weak_ids: set[str] | None = None,
    max_items: int = 2,
) -> dict[str, Any]:
    scored = []
    excluded_counts: dict[str, int] = {}
    for item in items or []:
        item_id = news_item_id(item)
        signal = score_news_item(
            market,
            ticker,
            name,
            item,
            aliases=aliases,
            date_quality=(date_quality_by_id or {}).get(item_id, "dated"),
            broad_weak=item_id in (broad_weak_ids or set()),
        )
        scored.append(signal)
        if not signal.get("prompt_eligible"):
            key = str(signal.get("signal_type") or "excluded")
            excluded_counts[key] = excluded_counts.get(key, 0) + 1

    positives = [
        row for row in scored
        if row.get("prompt_eligible") and row.get("signal_type") in CORE_SIGNAL_TYPES
    ]
    risks = [
        row for row in scored
        if row.get("prompt_eligible") and row.get("signal_type") in RISK_SIGNAL_TYPES
    ]
    positives.sort(key=lambda row: (-int(row.get("score") or 0), str(row.get("published_at") or ""), str(row.get("id") or "")))
    risks.sort(key=lambda row: (-int(row.get("score") or 0), str(row.get("published_at") or ""), str(row.get("id") or "")))
    top_news = positives[: max(0, int(max_items or 2))]
    risk_news = risks[:1]
    best = (top_news + risk_news + sorted(scored, key=lambda row: -int(row.get("score") or 0))[:1])[:1]
    best_row = best[0] if best else {}
    prompt_ids = [str(row.get("id") or "") for row in top_news + risk_news if row.get("id")]
    signal_type = str(best_row.get("signal_type") or "")
    score = int(best_row.get("score") or 0)
    prompt_eligible = bool(top_news or risk_news)
    prompt_summary = "; ".join(
        compact_news_text(f"{row.get('signal_type')}:{row.get('title')}", 170)
        for row in top_news
        if row.get("title")
    )
    risk_summary = "; ".join(
        compact_news_text(f"{row.get('signal_type')}:{row.get('title')}", 170)
        for row in risk_news
        if row.get("title")
    )
    return {
        "news_prompt_eligible": prompt_eligible,
        "news_signal_type": signal_type,
        "news_score": score,
        "news_prompt_summary": prompt_summary,
        "risk_news_summary": risk_summary,
        "prompt_news_ids": prompt_ids,
        "top_news": top_news,
        "risk_news": risk_news,
        "excluded_news_counts": excluded_counts,
        "scored_news_count": len(scored),
    }


def json_dumps_compact(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, default=str)
