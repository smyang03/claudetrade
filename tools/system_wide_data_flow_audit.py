from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
ML_DB = ROOT / "data" / "ml" / "decisions.db"
SELECTION_DB = ROOT / "data" / "ticker_selection_log.db"
EVENT_DB = ROOT / "data" / "v2_event_store.db"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else {}


def _json_file_summary(rel: str) -> dict[str, Any]:
    path = ROOT / rel
    out: dict[str, Any] = {
        "path": rel,
        "exists": path.exists(),
    }
    if not path.exists():
        return out
    out["size"] = path.stat().st_size
    out["mtime"] = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        out["json_error"] = f"{type(exc).__name__}: {exc}"
        return out
    if isinstance(data, dict):
        out["type"] = "dict"
        out["key_count"] = len(data)
        out["keys"] = list(data.keys())[:20]
        if rel.endswith("earnings_calendar.json"):
            out["from"] = data.get("from")
            out["to"] = data.get("to")
            out["fetched_at"] = data.get("fetched_at")
            out["kr_fetched_at"] = data.get("kr_fetched_at")
            out["us_count"] = len(data.get("by_symbol") or {})
            out["kr_count"] = len(data.get("kr_by_code") or {})
        if "sector_map" in rel:
            out["market_counts"] = {
                str(k): len(v) if isinstance(v, dict) else None
                for k, v in data.items()
                if str(k) in {"KR", "US", "_meta"}
            }
        if rel.endswith("dart_corp_codes.json"):
            out["corp_code_count"] = len(data)
    elif isinstance(data, list):
        out["type"] = "list"
        out["len"] = len(data)
    else:
        out["type"] = type(data).__name__
    return out


def audit_candidate_rows(start_date: str) -> dict[str, Any]:
    with _connect(AUDIT_DB) as conn:
        return {
            "row_counts": _rows(
                conn,
                """
                SELECT market, COUNT(*) rows, MIN(session_date) min_session, MAX(session_date) max_session
                  FROM audit_candidate_rows
                 WHERE session_date >= ?
                 GROUP BY market
                """,
                (start_date,),
            ),
            "cohort_reliability": _rows(
                conn,
                """
                SELECT market, COUNT(*) rows,
                       SUM(CASE WHEN COALESCE(cohort_reliability,0) <> 0 THEN 1 ELSE 0 END) column_nonzero,
                       SUM(CASE WHEN json_type(payload_json,'$.runtime_gate.cohort_reliability') IS NOT NULL THEN 1 ELSE 0 END) runtime_gate_key,
                       SUM(CASE WHEN json_extract(payload_json,'$.runtime_gate.cohort_reliability') <> 0 THEN 1 ELSE 0 END) runtime_gate_nonzero,
                       SUM(CASE WHEN payload_json LIKE '%trainer_cohort_key%' THEN 1 ELSE 0 END) payload_trainer_key,
                       SUM(CASE WHEN scorer_input_snapshot_json LIKE '%trainer_cohort_key%' THEN 1 ELSE 0 END) scorer_trainer_key
                  FROM audit_candidate_rows
                 WHERE session_date >= ?
                 GROUP BY market
                """,
                (start_date,),
            ),
            "candidate_source_blank_by_path": _rows(
                conn,
                """
                SELECT market, source_file, COUNT(*) rows,
                       SUM(CASE WHEN COALESCE(candidate_source,'')='' THEN 1 ELSE 0 END) blank,
                       ROUND(100.0 * SUM(CASE WHEN COALESCE(candidate_source,'')='' THEN 1 ELSE 0 END) / COUNT(*), 2) blank_pct
                  FROM audit_candidate_rows
                 WHERE session_date >= ?
                 GROUP BY market, source_file
                 ORDER BY market, blank_pct DESC, rows DESC
                """,
                (start_date,),
            ),
            "post_open_feature_values": _rows(
                conn,
                """
                SELECT market, COUNT(*) rows,
                       SUM(CASE WHEN COALESCE(post_open_features_json,'')='' THEN 1 ELSE 0 END) missing_post_open,
                       SUM(CASE WHEN json_type(post_open_features_json,'$.spread_bps') IS NOT NULL THEN 1 ELSE 0 END) spread_key_rows,
                       SUM(CASE WHEN json_extract(post_open_features_json,'$.spread_bps') IS NOT NULL THEN 1 ELSE 0 END) spread_value_rows,
                       SUM(CASE WHEN json_type(post_open_features_json,'$.vwap_distance_pct') IS NOT NULL THEN 1 ELSE 0 END) vwap_key_rows,
                       SUM(CASE WHEN json_extract(post_open_features_json,'$.vwap_distance_pct') IS NOT NULL THEN 1 ELSE 0 END) vwap_value_rows,
                       SUM(CASE WHEN json_type(post_open_features_json,'$.volume_ratio_open') IS NOT NULL THEN 1 ELSE 0 END) rvol_key_rows,
                       SUM(CASE WHEN json_extract(post_open_features_json,'$.volume_ratio_open') IS NOT NULL THEN 1 ELSE 0 END) rvol_value_rows,
                       SUM(CASE WHEN json_extract(post_open_features_json,'$.time_normalized_rvol') IS NOT NULL THEN 1 ELSE 0 END) time_normalized_rvol_rows
                  FROM audit_candidate_rows
                 WHERE session_date >= ?
                 GROUP BY market
                """,
                (start_date,),
            ),
            "news_signal_summary": _rows(
                conn,
                """
                SELECT market, COALESCE(news_signal_type,'') signal, news_prompt_eligible, COUNT(*) rows,
                       AVG(news_score) avg_news_score,
                       AVG(candidate_quality_score) avg_candidate_quality_score,
                       AVG(trainer_prompt_score) avg_trainer_prompt_score,
                       SUM(CASE WHEN final_prompt_included=1 THEN 1 ELSE 0 END) final_prompt_included,
                       SUM(CASE WHEN claude_watchlist=1 THEN 1 ELSE 0 END) claude_watchlist,
                       SUM(CASE WHEN claude_trade_ready=1 THEN 1 ELSE 0 END) claude_trade_ready,
                       SUM(CASE WHEN buy_signal_count>0 THEN 1 ELSE 0 END) buy_signal_rows,
                       SUM(CASE WHEN filled_count>0 THEN 1 ELSE 0 END) filled_rows
                  FROM audit_candidate_rows
                 WHERE session_date >= ?
                 GROUP BY market, COALESCE(news_signal_type,''), news_prompt_eligible
                 ORDER BY market, rows DESC
                """,
                (start_date,),
            ),
            "kr_news_bonus_component": _rows(
                conn,
                """
                SELECT COALESCE(news_signal_type,'') signal, news_prompt_eligible, COUNT(*) rows,
                       SUM(CASE WHEN trainer_score_components_json LIKE '%kr_catalyst_bonus%' THEN 1 ELSE 0 END) kr_catalyst_component
                  FROM audit_candidate_rows
                 WHERE session_date >= ? AND market='KR'
                 GROUP BY COALESCE(news_signal_type,''), news_prompt_eligible
                 ORDER BY rows DESC
                """,
                (start_date,),
            ),
            "us_future_news_age": _rows(
                conn,
                """
                SELECT session_date, COUNT(*) news_rows,
                       SUM(CASE WHEN top_news_json LIKE '%"age_min": -%' OR risk_news_json LIKE '%"age_min": -%' THEN 1 ELSE 0 END) future_age_rows
                  FROM audit_candidate_rows
                 WHERE session_date >= ? AND market='US' AND COALESCE(news_signal_type,'')<>''
                 GROUP BY session_date
                 ORDER BY session_date
                """,
                (start_date,),
            ),
            "counterfactual_status": _rows(
                conn,
                """
                SELECT market, status, COUNT(*) rows,
                       SUM(CASE WHEN outcome_30m_pct IS NOT NULL OR outcome_60m_pct IS NOT NULL OR outcome_close_pct IS NOT NULL THEN 1 ELSE 0 END) with_outcome
                  FROM candidate_counterfactual_paths
                 WHERE session_date >= ?
                 GROUP BY market, status
                 ORDER BY market, rows DESC
                """,
                (start_date,),
            ),
            "daily_outcome_status": _rows(
                conn,
                """
                SELECT r.market, o.horizon_min, o.status, COUNT(*) rows
                  FROM audit_candidate_outcomes o
                  JOIN audit_candidate_rows r ON r.candidate_key=o.candidate_key
                 WHERE r.session_date >= ?
                 GROUP BY r.market, o.horizon_min, o.status
                 ORDER BY r.market, o.horizon_min, rows DESC
                """,
                (start_date,),
            ),
        }


def audit_selection_log(start_date: str) -> dict[str, Any]:
    with _connect(SELECTION_DB) as conn:
        return {
            "coverage": _rows(
                conn,
                """
                SELECT market, COUNT(*) rows,
                       SUM(CASE WHEN COALESCE(sector,'')='' THEN 1 ELSE 0 END) blank_sector,
                       SUM(CASE WHEN COALESCE(source_type,'')='' THEN 1 ELSE 0 END) blank_source,
                       SUM(CASE WHEN COALESCE(rel_vol_shadow,'')<>'' THEN 1 ELSE 0 END) rel_vol_rows,
                       SUM(CASE WHEN atr_pct IS NOT NULL THEN 1 ELSE 0 END) atr_rows,
                       MIN(date) min_date, MAX(date) max_date
                  FROM ticker_selection_log
                 WHERE date >= ?
                 GROUP BY market
                """,
                (start_date,),
            ),
            "recent_by_date": _rows(
                conn,
                """
                SELECT date, market, COUNT(*) rows,
                       SUM(CASE WHEN COALESCE(rel_vol_shadow,'')<>'' THEN 1 ELSE 0 END) rel_vol_rows,
                       SUM(CASE WHEN atr_pct IS NOT NULL THEN 1 ELSE 0 END) atr_rows,
                       SUM(CASE WHEN COALESCE(sector,'')<>'' THEN 1 ELSE 0 END) sector_rows
                  FROM ticker_selection_log
                 WHERE date >= '2026-07-15'
                 GROUP BY date, market
                 ORDER BY date, market
                """,
            ),
        }


def audit_performance() -> dict[str, Any]:
    with _connect(ML_DB) as conn:
        return {
            "performance_summary": {
                table: _rows(
                    conn,
                    f"""
                    SELECT market, COUNT(*) rows, MIN(session_date) min_session, MAX(session_date) max_session, MAX(synced_at) max_sync,
                           SUM(CASE WHEN filled=1 THEN 1 ELSE 0 END) filled,
                           SUM(CASE WHEN closed=1 THEN 1 ELSE 0 END) closed,
                           SUM(CASE WHEN closed=1 AND (mfe_pct IS NOT NULL OR mae_pct IS NOT NULL) THEN 1 ELSE 0 END) excursion_rows,
                           SUM(CASE WHEN closed=1 AND COALESCE(mfe_time,'')<>'' THEN 1 ELSE 0 END) mfe_time_rows,
                           SUM(CASE WHEN closed=1 AND COALESCE(mae_time,'')<>'' THEN 1 ELSE 0 END) mae_time_rows
                      FROM {table}
                     WHERE runtime_mode='live'
                     GROUP BY market
                    """,
                )
                for table in ("v2_learning_performance", "v2_canonical_performance")
            },
            "sleeve_rows": _rows(
                conn,
                """
                SELECT v2_decision_id, market, session_date, ticker, status, route, path_type, strategy,
                       origin_action, filled, closed, pnl_pct_net, pnl_krw_net, net_basis,
                       quality_grade, learning_allowed, strategy_attribution, portfolio_realized, synced_at
                  FROM v2_canonical_performance
                 WHERE runtime_mode='live'
                   AND session_date >= '2026-07-01'
                   AND (strategy LIKE '%trend%' OR ticker IN ('SCHG','275280','275300'))
                 ORDER BY session_date, market, ticker
                """,
            ),
            "mfe_backfill_coverage": _rows(
                conn,
                """
                SELECT v.market, COUNT(*) backfill_rows,
                       SUM(CASE WHEN b.mfe_pct IS NOT NULL OR b.mae_pct IS NOT NULL THEN 1 ELSE 0 END) backfill_excursion_rows,
                       SUM(CASE WHEN b.mfe_minutes IS NOT NULL OR b.mae_minutes IS NOT NULL THEN 1 ELSE 0 END) backfill_time_rows,
                       MIN(v.session_date) min_session, MAX(v.session_date) max_session
                  FROM mfe_backfill_yf b
                  JOIN v2_learning_performance v ON v.v2_decision_id=b.v2_decision_id
                 GROUP BY v.market
                """,
            ),
            "learning_canonical_mfe_delta": _rows(
                conn,
                """
                SELECT v.market, COUNT(*) closed,
                       SUM(CASE WHEN v.mfe_pct IS NOT NULL OR v.mae_pct IS NOT NULL THEN 1 ELSE 0 END) learning_excursion_rows,
                       SUM(CASE WHEN c.mfe_pct IS NOT NULL OR c.mae_pct IS NOT NULL THEN 1 ELSE 0 END) canonical_excursion_rows,
                       SUM(CASE WHEN b.mfe_pct IS NOT NULL OR b.mae_pct IS NOT NULL THEN 1 ELSE 0 END) backfill_excursion_rows,
                       SUM(CASE WHEN (v.mfe_pct IS NOT NULL OR v.mae_pct IS NOT NULL)
                                  AND (c.mfe_pct IS NULL AND c.mae_pct IS NULL) THEN 1 ELSE 0 END) learning_not_canonical
                  FROM v2_learning_performance v
                  LEFT JOIN v2_canonical_performance c ON c.v2_decision_id=v.v2_decision_id
                  LEFT JOIN mfe_backfill_yf b ON b.v2_decision_id=v.v2_decision_id
                 WHERE v.runtime_mode='live' AND v.closed=1
                 GROUP BY v.market
                """,
            ),
        }


def audit_mfe_sync_loss() -> dict[str, Any]:
    from tools import sync_v2_learning_performance as sync

    with _connect(ML_DB) as ml, _connect(EVENT_DB) as events:
        existing = [
            row["v2_decision_id"]
            for row in ml.execute(
                """
                SELECT v2_decision_id
                  FROM v2_learning_performance
                 WHERE runtime_mode='live' AND closed=1
                   AND (mfe_pct IS NOT NULL OR mae_pct IS NOT NULL)
                """
            ).fetchall()
        ]
        decision_map = {
            str(row.get("decision_id") or ""): row
            for row in sync._load_decisions(events, runtime_mode="live")
        }
        would_keep: list[str] = []
        would_lose: list[str] = []
        for decision_id in existing:
            decision = decision_map.get(decision_id)
            if not decision:
                continue
            event_rows = sync._load_events(events, decision_id)
            path_run = sync._load_path_run(events, decision_id, sync._entry_path_run_id_from_events(event_rows))
            built = sync.build_learning_row(decision, event_rows, path_run)
            if built.get("mfe_pct") is None and built.get("mae_pct") is None:
                would_lose.append(decision_id)
            else:
                would_keep.append(decision_id)
        by_market = {}
        for market in ("KR", "US"):
            market_existing = [
                row["v2_decision_id"]
                for row in ml.execute(
                    """
                    SELECT v2_decision_id
                      FROM v2_learning_performance
                     WHERE runtime_mode='live' AND closed=1 AND market=?
                       AND (mfe_pct IS NOT NULL OR mae_pct IS NOT NULL)
                    """,
                    (market,),
                ).fetchall()
            ]
            loss_set = set(would_lose)
            by_market[market] = {
                "existing_learning_excursion": len(market_existing),
                "event_sync_would_lose": sum(1 for item in market_existing if item in loss_set),
            }
        return {
            "existing_learning_excursion": len(existing),
            "event_sync_keep": len(would_keep),
            "event_sync_loss": len(would_lose),
            "by_market": by_market,
            "loss_sample": would_lose[:20],
        }


def audit_config() -> dict[str, Any]:
    files = sorted((ROOT / "logs" / "config").glob("effective_config_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {"latest_effective_config": None}
    path = files[0]
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    effective = data.get("effective") if isinstance(data.get("effective"), dict) else {}
    keys = [
        "CANDIDATE_PROMPT_POOL_REORDER_ENABLED",
        "CANDIDATE_QUALITY_TRAINER_ENABLED",
        "CANDIDATE_TRAINER_QUALITY_SCORE_ENABLED",
        "CANDIDATE_QUALITY_TRAINER_PROMPT_HINT_ENABLED",
        "CANDIDATE_QUALITY_COMPOSITE_SCORE_PROMPT_ENABLED",
        "KR_CATALYST_SCORE_BONUS_ENABLED",
        "US_CATALYST_SCORE_BONUS_ENABLED",
        "PREOPEN_WEAK_NEWS_PENALTY",
        "SECTOR_MAP_ENABLED",
        "CANDIDATE_AUDIT_DB_PATH",
        "CLAUDE_REVIEW_ALL_AUTOMATED_SELLS",
    ]
    return {
        "latest_effective_config": str(path.relative_to(ROOT)),
        "written_at": data.get("written_at"),
        "selected_effective_values": {key: effective.get(key) for key in keys},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only system-wide data flow audit.")
    parser.add_argument("--start-date", default="2026-07-01")
    parser.add_argument("--skip-mfe-sync-loss", action="store_true")
    args = parser.parse_args(argv)

    output = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "start_date": args.start_date,
        "paths": {
            "audit_db": str(AUDIT_DB.relative_to(ROOT)),
            "ml_db": str(ML_DB.relative_to(ROOT)),
            "selection_db": str(SELECTION_DB.relative_to(ROOT)),
            "event_db": str(EVENT_DB.relative_to(ROOT)),
        },
        "config": audit_config(),
        "candidate_rows": audit_candidate_rows(args.start_date),
        "selection_log": audit_selection_log(args.start_date),
        "performance": audit_performance(),
        "external_files": [
            _json_file_summary("data/earnings_calendar.json"),
            _json_file_summary("data/sector_map.json"),
            _json_file_summary("data/sector_map.json.disabled_until_restart"),
            _json_file_summary("data/dart_corp_codes.json"),
            _json_file_summary("state/profit_evidence_KR.json"),
            _json_file_summary("state/profit_evidence_US.json"),
            _json_file_summary("data/shadow/core_shadow_signal_202607.json"),
            _json_file_summary("state/trend_overlay_signal.json"),
        ],
    }
    if not args.skip_mfe_sync_loss:
        output["mfe_sync_loss_risk"] = audit_mfe_sync_loss()

    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
