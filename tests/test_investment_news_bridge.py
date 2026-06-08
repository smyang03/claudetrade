from __future__ import annotations

import sqlite3
from pathlib import Path

from preopen.investment_news_bridge import build_preopen_payload_from_investment_news
from preopen.news_enrichment import enrich_candidates_with_news
from runtime.rehearsal.context import create_rehearsal_context, install_write_guard


def _make_news_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                source TEXT NOT NULL,
                source_type TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL,
                published_at TEXT NOT NULL DEFAULT '',
                collected_at TEXT NOT NULL,
                importance TEXT NOT NULL DEFAULT 'C',
                horizon TEXT NOT NULL DEFAULT 'short',
                confidence TEXT NOT NULL DEFAULT 'medium',
                tickers_json TEXT NOT NULL DEFAULT '[]',
                score_json TEXT NOT NULL DEFAULT '{}',
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            );
            """
        )
        conn.execute(
            """
            INSERT INTO news_items (
                market, source, source_type, title, summary, url, published_at, collected_at,
                importance, horizon, confidence, tickers_json, score_json, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "US",
                "GoogleNews",
                "technology",
                "Nvidia unveils new AI platform",
                "AI data center demand and platform adoption are accelerating.",
                "https://news.example/nvda",
                "2026-06-08T12:00:00+00:00",
                "2026-06-08T12:01:00+00:00",
                "A",
                "long",
                "medium",
                '["NVDA"]',
                '{"score": 7}',
                "technology catalyst",
                "2026-06-08T12:01:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO news_items (
                market, source, source_type, title, summary, url, published_at, collected_at,
                importance, horizon, confidence, tickers_json, score_json, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "GLOBAL",
                "Naver",
                "macro",
                "Fed policy risk rises",
                "",
                "https://news.example/macro",
                "2026-06-08T10:00:00+00:00",
                "2026-06-08T10:01:00+00:00",
                "S",
                "event",
                "medium",
                "[]",
                '{"score": 9}',
                "macro risk",
                "2026-06-08T10:01:00+00:00",
            ),
        )


def test_investment_news_db_rows_convert_to_preopen_news_payload(tmp_path: Path) -> None:
    db = tmp_path / "investment_news.db"
    _make_news_db(db)

    payload = build_preopen_payload_from_investment_news(
        market="US",
        session_date="2026-06-08",
        db_path=db,
    )

    assert payload["target_source"] == "investment_news_db_readonly"
    assert payload["investment_news_bridge"]["row_count"] == 2
    assert payload["corp_news"]["NVDA"]["count"] == 1
    assert payload["corp_news"]["NVDA"]["items"][0]["summary"] == "AI data center demand and platform adoption are accelerating."
    assert payload["market_news"][0]["summary"] == "macro: Fed policy risk rises"


def test_investment_news_bridge_matches_empty_ticker_rows_by_candidate_name(tmp_path: Path) -> None:
    db = tmp_path / "investment_news.db"
    _make_news_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO news_items (
                market, source, source_type, title, summary, url, published_at, collected_at,
                importance, horizon, confidence, tickers_json, score_json, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "US",
                "Naver",
                "company_news",
                "Cisco signs AI networking supply contract",
                "Cisco contract expands AI networking demand.",
                "https://news.example/csco",
                "2026-06-08T11:00:00+00:00",
                "2026-06-08T11:01:00+00:00",
                "A",
                "short",
                "medium",
                "[]",
                '{"score": 8}',
                "company catalyst",
                "2026-06-08T11:01:00+00:00",
            ),
        )

    payload = build_preopen_payload_from_investment_news(
        market="US",
        session_date="2026-06-08",
        db_path=db,
        targets={"CSCO": "Cisco"},
    )

    assert payload["investment_news_bridge"]["name_matched_row_count"] == 1
    assert payload["corp_news"]["CSCO"]["name"] == "Cisco"
    assert payload["corp_news"]["CSCO"]["items"][0]["title"] == "Cisco signs AI networking supply contract"


def test_investment_news_payload_enriches_candidates_without_live_writes(tmp_path: Path) -> None:
    db = tmp_path / "investment_news.db"
    _make_news_db(db)
    ctx = create_rehearsal_context(scenario="news_bridge", runtime_root=tmp_path / "sandbox")

    with install_write_guard(ctx):
        payload = build_preopen_payload_from_investment_news(
            market="US",
            session_date="2026-06-08",
            db_path=db,
            targets={"NVDA": "Nvidia"},
        )

    candidates = [{"ticker": "NVDA", "extended_change_pct": 5.0, "extended_dollar_volume": 10_000_000, "spread_pct": 0.2}]
    enriched, summary = enrich_candidates_with_news(
        "US",
        candidates,
        session_date="2026-06-08",
        news_payload=payload,
        allow_rank_reorder=False,
    )

    assert summary["status"] == "ok"
    assert summary["flagged_count"] == 1
    assert enriched[0]["news_or_earnings_flag"] is True
    assert enriched[0]["news_or_earnings_sources"] == ["GoogleNews"]
    assert enriched[0]["news_or_earnings_sample_title"] == "Nvidia unveils new AI platform"
    assert enriched[0]["news_prompt_eligible"] is True
    assert enriched[0]["news_signal_type"] == "direct_catalyst"
    assert "news_or_earnings" in enriched[0]["quality_tags"]
