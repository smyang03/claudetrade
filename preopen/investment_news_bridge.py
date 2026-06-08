from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SIBLING_NEWS_DB = ROOT.parent / "news" / "data" / "investment_news" / "investment_news.db"


def default_investment_news_db_path() -> Path:
    raw = os.getenv("INVEST_NEWS_DB_PATH", "").strip()
    if raw:
        path = Path(raw).expanduser()
        return path if path.is_absolute() else ROOT / path
    return SIBLING_NEWS_DB


def _market_key(market: str) -> str:
    raw = str(market or "").strip().upper()
    if raw in {"US", "USA", "NYSE", "NASDAQ"}:
        return "US"
    if raw in {"KR", "KOREA"}:
        return "KR"
    return "GLOBAL"


def _session_date(value: str) -> str:
    return str(value or "").strip()[:10]


def _json_loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (list, tuple, dict)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _read_only_uri(path: Path) -> str:
    return f"file:{path.resolve().as_posix()}?mode=ro"


def _compact(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _normalize_ticker(market: str, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if _market_key(market) == "US":
        return raw.upper()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits.zfill(6) if digits else raw


def _target_lookup(market: str, targets: dict[str, str] | None) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for raw_ticker, raw_name in (targets or {}).items():
        ticker = _normalize_ticker(market, raw_ticker)
        if ticker:
            lookup[ticker] = str(raw_name or "").strip() or ticker
    return lookup


def _summary_for_row(row: dict[str, Any]) -> str:
    summary = _compact(row.get("summary"), 220)
    if summary:
        return summary
    title = _compact(row.get("title"), 180)
    source_type = str(row.get("source_type") or "news").strip()
    if not title:
        return ""
    return f"{source_type}: {title}"


def _published_date(row: dict[str, Any]) -> str:
    for key in ("published_at", "collected_at", "created_at"):
        value = str(row.get(key) or "").strip()
        if len(value) >= 10:
            return value[:10]
    return ""


def _row_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("title", "summary", "reason")
        if str(row.get(key) or "").strip()
    )


def _row_matches_target(
    row: dict[str, Any],
    *,
    market: str,
    ticker: str,
    name: str,
    aliases: list[str] | None = None,
) -> bool:
    text = _row_text(row)
    if not text:
        return False
    market_key = _market_key(market)
    ticker_key = _normalize_ticker(market_key, ticker)
    if market_key == "US":
        upper = text.upper()
        if ticker_key and re.search(rf"(?<![A-Z0-9]){re.escape(ticker_key)}(?![A-Z0-9])", upper):
            return True
        tokens = [name, *(aliases or [])]
        return any(str(token or "").strip() and str(token).strip().upper() in upper for token in tokens)
    if ticker_key and ticker_key in text:
        return True
    tokens = [name, *(aliases or [])]
    return any(str(token or "").strip() and str(token).strip() in text for token in tokens)


def _matched_targets_for_row(
    row: dict[str, Any],
    *,
    market: str,
    targets: dict[str, str],
    aliases_by_ticker: dict[str, list[str]] | None = None,
) -> list[str]:
    matched: list[str] = []
    for ticker, name in targets.items():
        if _row_matches_target(
            row,
            market=market,
            ticker=ticker,
            name=name,
            aliases=(aliases_by_ticker or {}).get(ticker),
        ):
            matched.append(ticker)
    return matched


def _row_to_news_item(row: dict[str, Any], *, ticker: str = "") -> dict[str, Any]:
    score = _json_loads(row.get("score_json"), {})
    item = {
        "id": str(row.get("id") or "").strip(),
        "news_id": f"investment_news:{row.get('id')}" if row.get("id") is not None else "",
        "dedupe_key": str(row.get("dedupe_key") or "").strip(),
        "source": str(row.get("source") or "InvestmentNews").strip() or "InvestmentNews",
        "date": _published_date(row),
        "published_at": str(row.get("published_at") or ""),
        "title": str(row.get("title") or "").strip(),
        "content": _summary_for_row(row),
        "summary": _summary_for_row(row),
        "url": str(row.get("url") or "").strip(),
        "ticker": ticker,
        "source_type": str(row.get("source_type") or "news").strip() or "news",
        "importance": str(row.get("importance") or "C").strip().upper() or "C",
        "horizon": str(row.get("horizon") or "").strip(),
        "confidence": str(row.get("confidence") or "").strip(),
        "sentiment": str(row.get("sentiment") or "").strip(),
        "reason": str(row.get("reason") or "").strip(),
        "score": score if isinstance(score, dict) else {},
    }
    return item


def _query_news_rows(
    db_path: Path,
    *,
    market: str,
    session_date: str,
    limit: int,
) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    mkt = _market_key(market)
    day = _session_date(session_date)
    where = ["market IN (?, 'GLOBAL')"]
    params: list[Any] = [mkt]
    if day:
        where.append(
            "(substr(COALESCE(NULLIF(published_at,''), collected_at), 1, 10) = ? "
            "OR substr(collected_at, 1, 10) = ?)"
        )
        params.extend([day, day])
    sql = f"""
        SELECT *
          FROM news_items
         WHERE {' AND '.join(where)}
         ORDER BY
             CASE importance WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END,
             COALESCE(NULLIF(published_at, ''), collected_at) DESC,
             id DESC
         LIMIT ?
    """
    params.append(max(1, min(int(limit or 200), 500)))
    with sqlite3.connect(_read_only_uri(db_path), uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def build_preopen_payload_from_investment_news(
    *,
    market: str,
    session_date: str,
    db_path: str | Path | None = None,
    limit: int = 200,
    max_items_per_ticker: int = 5,
    targets: dict[str, str] | None = None,
    aliases_by_ticker: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    mkt = _market_key(market)
    day = _session_date(session_date)
    source_path = Path(db_path).expanduser() if db_path else default_investment_news_db_path()
    rows = _query_news_rows(source_path, market=mkt, session_date=day, limit=limit)
    target_map = _target_lookup(mkt, targets)
    corp_news: dict[str, dict[str, Any]] = {}
    market_news: list[dict[str, Any]] = []
    name_matched_rows = 0

    for row in rows:
        tickers = _json_loads(row.get("tickers_json"), [])
        tickers = [
            _normalize_ticker(mkt, ticker)
            for ticker in tickers
            if str(ticker or "").strip()
        ]
        tickers = [ticker for ticker in tickers if ticker]
        if tickers and target_map:
            tickers = [ticker for ticker in tickers if ticker in target_map]
        if not tickers and target_map:
            tickers = _matched_targets_for_row(
                row,
                market=mkt,
                targets=target_map,
                aliases_by_ticker=aliases_by_ticker,
            )
            if tickers:
                name_matched_rows += 1
        if tickers:
            for ticker in tickers:
                entry = corp_news.setdefault(
                    ticker,
                    {"name": target_map.get(ticker) or ticker, "items": [], "count": 0},
                )
                if len(entry["items"]) < max(1, int(max_items_per_ticker or 5)):
                    entry["items"].append(_row_to_news_item(row, ticker=ticker))
                entry["count"] = int(entry.get("count", 0) or 0) + 1
        else:
            market_news.append(_row_to_news_item(row))

    return {
        "date": day,
        "market": mkt,
        "target_source": "investment_news_db_readonly",
        "source_path": str(source_path),
        "market_news": market_news,
        "corp_news": corp_news,
        "investment_news_bridge": {
            "row_count": len(rows),
            "corp_ticker_count": len(corp_news),
            "market_item_count": len(market_news),
            "name_matched_row_count": name_matched_rows,
            "read_only": True,
        },
    }
