from __future__ import annotations

"""Reproducible US-market-specific strategy review.

The review intentionally separates three questions:

1. Does the existing candidate pool make money when entered after the first
   complete five-minute opening window?
2. Do point-in-time opening-tape confirmations improve that result?
3. Is the independent five-session US swing lane healthy and ready to supply
   forward evidence?

Nothing in this module has order authority.  It reads the immutable candidate
registry/audit surfaces and local price files, then writes research artifacts.
"""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.us_swing_authority import load_swing_policy
from tools.candidate_path_prediction_lab import (
    FEATURE_GROUPS,
    STOP_PCT,
    _daily_top,
    _pipeline,
    _read_price,
    _selection_stats,
    _tag,
    label_candidate_path,
    session_entry_floor,
    walk_forward,
)
from tools.us_swing_shadow_runner import summarize_active_execution_shadow


AUTHORITY = "RESEARCH_ONLY_NO_ORDER_AUTHORITY"
DEFAULT_LABELS = ROOT / "data" / "analysis" / "candidate_path_labels_lag5_v1.csv"
DEFAULT_AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
DEFAULT_MINUTE_ROOT = ROOT / "data" / "price" / "minute" / "us"
DEFAULT_DAILY_ROOT = ROOT / "data" / "price" / "us"
DEFAULT_SWING_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
DEFAULT_SWING_POLICY = ROOT / "config" / "us_swing_accelerated.json"
DEFAULT_SWING_HISTORICAL = ROOT / "state" / "us_swing_historical_evidence.json"
DEFAULT_SWING_EXECUTION = ROOT / "state" / "us_swing_execution_evidence.json"
DEFAULT_SWING_STATUS = ROOT / "state" / "us_swing_status.json"
DEFAULT_OUTPUT = ROOT / "reports" / "us_market_shape_strategy_review_20260716.json"
DEFAULT_LEDGER = ROOT / "reports" / "us_market_shape_open15_ledger_20260716.csv"


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _opening_cohort(frame: pd.DataFrame, *, horizon: int) -> pd.DataFrame:
    available = f"h{int(horizon)}_label_available"
    subset = frame[
        frame["market"].astype(str).str.upper().eq("US")
        & pd.to_numeric(frame[available], errors="coerce").eq(1)
    ].copy()
    known = pd.to_datetime(subset["known_at"], utc=True, errors="coerce")
    floor = pd.Series(
        [
            session_entry_floor(value, market="US", entry_lag_min=5)
            for value in subset["known_at"]
        ],
        index=subset.index,
    )
    return subset[known.notna() & floor.notna() & (known <= floor)].copy()


def _summary(rows: pd.DataFrame, *, net_column: str) -> dict[str, Any]:
    if rows.empty:
        return {"n": 0, "dates": 0}
    work = rows.dropna(subset=[net_column]).copy()
    if work.empty:
        return {"n": 0, "dates": 0}
    values = pd.to_numeric(work[net_column], errors="coerce").dropna()
    work = work.loc[values.index].copy()
    session_values = work.groupby("session_date", sort=True)[net_column].mean()
    ordered = work.sort_values(net_column, ascending=False)
    ex_top3 = ordered.iloc[3:] if len(ordered) > 3 else ordered.iloc[0:0]
    ticker_counts = work["ticker"].astype(str).value_counts()
    lcb = None
    if len(session_values) > 1:
        lcb = float(
            session_values.mean()
            - 1.645 * session_values.std(ddof=1) / math.sqrt(len(session_values))
        )
    return {
        "n": int(len(work)),
        "dates": int(work["session_date"].nunique()),
        "unique_tickers": int(work["ticker"].astype(str).nunique()),
        "mean_policy_net_pct": float(values.mean()),
        "median_policy_net_pct": float(values.median()),
        "win_rate": float((values > 0).mean()),
        "ex_top3_mean_pct": (
            float(pd.to_numeric(ex_top3[net_column], errors="coerce").mean())
            if len(ex_top3)
            else None
        ),
        "session_block_lcb_pct": lcb,
        "max_ticker_share": (
            float(ticker_counts.max() / len(work)) if len(ticker_counts) else None
        ),
    }


def evaluate_open5_candidate_pool(
    frame: pd.DataFrame,
    *,
    holdout_start: str,
) -> dict[str, Any]:
    """Evaluate current labels with a fixed pre-holdout model and expanding OOS."""

    features = [
        column for column in FEATURE_GROUPS["system_scores"] if column in frame.columns
    ]
    result: dict[str, Any] = {}
    for horizon in (30, 60):
        cohort = _opening_cohort(frame, horizon=horizon)
        for target in (1.6, 2.3, 3.6):
            prefix = f"h{horizon}_t{_tag(target)}_s{_tag(STOP_PCT)}"
            label = f"{prefix}_target_before_stop"
            net = f"{prefix}_policy_net_pct"
            data = cohort.dropna(subset=[label, net]).copy()
            train = data[data["session_date"].astype(str) < str(holdout_start)].copy()
            test = data[data["session_date"].astype(str) >= str(holdout_start)].copy()
            train_dates = sorted(train["session_date"].astype(str).unique())
            purged_session = train_dates[-1] if train_dates else ""
            if purged_session:
                train = train[
                    train["session_date"].astype(str) < purged_session
                ].copy()
            fixed: dict[str, Any] = {
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "purged_session": purged_session,
                "all_holdout": _summary(test, net_column=net),
            }
            if (
                len(train) >= 200
                and len(test) >= 3
                and train[label].nunique() >= 2
                and features
            ):
                for column in features:
                    train[column] = pd.to_numeric(train[column], errors="coerce")
                    test[column] = pd.to_numeric(test[column], errors="coerce")
                model = _pipeline(features)
                model.fit(train[features], train[label].astype(int))
                score = model.predict_proba(test[features])[:, 1]
                scored = test[
                    ["session_date", "ticker", label, net]
                ].rename(columns={label: "_label", net: "_net"})
                fixed["system_scores_top3"] = _selection_stats(
                    _daily_top(scored, score, top_k=3)
                )
                fixed["system_scores_top_decile"] = _selection_stats(
                    _daily_top(scored, score, fraction=0.10)
                )
            expanding = walk_forward(
                data,
                market="US",
                features=features,
                label=label,
                net_column=net,
                min_train_dates=10,
                purge_dates=1,
            )
            result[f"h{horizon}_t{target}"] = {
                "objective": (
                    f"enter after first complete open+5m bar; {horizon}m "
                    f"target {target}% before stop {STOP_PCT}%; US cost 0.50%"
                ),
                "fixed_holdout": fixed,
                "expanding_walk_forward": {
                    "tested_rows": expanding.get("tested_rows"),
                    "daily_top3": (expanding.get("model") or {}).get("daily_top3"),
                    "daily_top_decile": (
                        expanding.get("model") or {}
                    ).get("daily_top_decile"),
                    "by_month_top_decile": {
                        str(month): (metrics.get("daily_top_decile"))
                        for month, metrics in (expanding.get("by_month") or {}).items()
                    },
                },
            }
    return result


OPEN15_FEATURES = (
    "ret_5m_pct",
    "ret_10m_pct",
    "vwap_distance_pct",
    "opening_range_break",
)


def select_open15_snapshot(
    records: Iterable[dict[str, Any]],
    *,
    min_elapsed: float = 8.0,
    max_elapsed: float = 15.5,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Select the richest point-in-time snapshot available by open+15m."""

    selected: tuple[tuple[int, str], dict[str, Any], dict[str, Any]] | None = None
    for raw in records:
        try:
            payload = json.loads(str(raw.get("post_open_features_json") or "{}"))
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        elapsed = _finite(payload.get("market_open_elapsed_min"))
        if elapsed is None or elapsed < min_elapsed or elapsed > max_elapsed:
            continue
        presence = sum(payload.get(column) is not None for column in OPEN15_FEATURES)
        rank = (presence, str(raw.get("known_at") or ""))
        if selected is None or rank > selected[0]:
            selected = (rank, raw, payload)
    return (selected[1], selected[2]) if selected is not None else None


def classify_us_open_context(
    payload: dict[str, Any],
    *,
    news_count: Any = 0,
    news_type: str = "",
) -> dict[str, Any]:
    """Tag US opening structures without claiming or granting order authority."""

    ret5 = _finite(payload.get("ret_5m_pct"))
    ret10 = _finite(payload.get("ret_10m_pct"))
    vwap = _finite(payload.get("vwap_distance_pct"))
    pullback = _finite(payload.get("pullback_from_high_pct"))
    state = str(payload.get("momentum_state") or "").strip().lower()
    opening_break = payload.get("opening_range_break") is True
    catalyst = (_finite(news_count) or 0.0) > 0 or str(news_type) == "direct_catalyst"
    feature_complete = ret5 is not None and ret10 is not None and vwap is not None
    positive_tape = bool(
        feature_complete
        and 0.15 <= float(ret5) <= 3.0
        and float(ret10) > 0
        and float(vwap) >= 0
        and state not in {"fade", "overextended"}
    )
    vwap_reclaim = bool(
        feature_complete
        and float(ret5) <= 0
        and float(ret10) > 0
        and float(vwap) >= 0
    )
    controlled_pullback = bool(
        feature_complete
        and 0 < float(ret5) <= 2.0
        and pullback is not None
        and -1.5 <= pullback <= 0
        and float(vwap) >= 0
    )
    tags: list[str] = []
    if catalyst:
        tags.append("CATALYST")
    if opening_break:
        tags.append("OPENING_RANGE_BREAK")
    if positive_tape:
        tags.append("POSITIVE_TAPE")
    if vwap_reclaim:
        tags.append("VWAP_RECLAIM")
    if controlled_pullback:
        tags.append("CONTROLLED_PULLBACK")
    if not feature_complete:
        tags.append("FEATURE_INCOMPLETE")
    return {
        "feature_complete": feature_complete,
        "catalyst": catalyst,
        "positive_tape": positive_tape,
        "vwap_reclaim": vwap_reclaim,
        "controlled_pullback": controlled_pullback,
        "opening_range_break": opening_break,
        "tags": tags,
        "authority": AUTHORITY,
    }


def build_open15_ledger(
    *,
    audit_db: Path,
    minute_root: Path,
    session_start: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    uri = f"file:{audit_db.resolve().as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT session_date,UPPER(ticker) AS ticker,known_at,candidate_key,
                   post_open_features_json,
                   COALESCE(news_or_earnings_count,0) AS news_count,
                   COALESCE(news_signal_type,'') AS news_type,
                   COALESCE(news_quality,'') AS news_quality,
                   COALESCE(candidate_source,'') AS candidate_source
            FROM audit_candidate_rows
            WHERE runtime_mode='live'
              AND market='US'
              AND session_date>=?
              AND post_open_features_json IS NOT NULL
              AND post_open_features_json NOT IN ('','{}','null')
            ORDER BY session_date,ticker,known_at
            """,
            (str(session_start),),
        ).fetchall()
    finally:
        con.close()
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        record = dict(row)
        grouped.setdefault(
            (str(record["session_date"]), str(record["ticker"])), []
        ).append(record)

    price_cache: dict[str, pd.DataFrame] = {}
    records: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    selected_snapshots = 0
    for (session_date, ticker), candidates in grouped.items():
        selected = select_open15_snapshot(candidates)
        if selected is None:
            continue
        selected_snapshots += 1
        raw, payload = selected
        path = minute_root / f"us_{ticker}.csv"
        if not path.exists():
            reasons["price_file_missing"] = reasons.get("price_file_missing", 0) + 1
            continue
        if ticker not in price_cache:
            try:
                price_cache[ticker] = _read_price(path)
            except Exception:
                price_cache[ticker] = pd.DataFrame()
        labels, reason = label_candidate_path(
            price_cache[ticker],
            known_at=raw.get("known_at"),
            market="US",
            entry_lag_min=0,
            max_entry_delay_min=3.0,
        )
        if labels is None:
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        context = classify_us_open_context(
            payload,
            news_count=raw.get("news_count"),
            news_type=str(raw.get("news_type") or ""),
        )
        records.append(
            {
                "session_date": session_date,
                "ticker": ticker,
                "candidate_key": raw.get("candidate_key"),
                "context_known_at": raw.get("known_at"),
                "news_count": raw.get("news_count"),
                "news_type": raw.get("news_type"),
                "news_quality": raw.get("news_quality"),
                "candidate_source": raw.get("candidate_source"),
                **{
                    column: payload.get(column)
                    for column in (
                        "market_open_elapsed_min",
                        "ret_3m_pct",
                        "ret_5m_pct",
                        "ret_10m_pct",
                        "vwap_distance_pct",
                        "pullback_from_high_pct",
                        "opening_range_break",
                        "momentum_state",
                        "data_quality",
                    )
                },
                **{f"context_{key}": value for key, value in context.items()},
                **labels,
            }
        )
    return pd.DataFrame(records), {
        "source_rows": int(len(rows)),
        "candidate_sessions": int(len(grouped)),
        "selected_snapshots": selected_snapshots,
        "labeled_snapshots": int(len(records)),
        "label_failures": reasons,
    }


def evaluate_open15_contexts(ledger: pd.DataFrame) -> dict[str, Any]:
    if ledger.empty:
        return {"coverage": {}, "rules": {}}
    masks = {
        "all_open15_candidates": pd.Series(True, index=ledger.index),
        "feature_complete": ledger["context_feature_complete"].eq(True),
        "positive_tape": ledger["context_positive_tape"].eq(True),
        "catalyst_positive_tape": (
            ledger["context_catalyst"].eq(True)
            & ledger["context_positive_tape"].eq(True)
        ),
        "opening_break_positive_tape": (
            ledger["context_opening_range_break"].eq(True)
            & ledger["context_positive_tape"].eq(True)
        ),
        "vwap_reclaim": ledger["context_vwap_reclaim"].eq(True),
        "controlled_pullback": ledger["context_controlled_pullback"].eq(True),
    }
    nets = (
        "h30_t1p6_s2p5_policy_net_pct",
        "h30_t2p3_s2p5_policy_net_pct",
        "h60_t1p6_s2p5_policy_net_pct",
        "h60_t2p3_s2p5_policy_net_pct",
        "h60_t3p6_s2p5_policy_net_pct",
    )
    return {
        "coverage": {
            column: float(ledger[column].notna().mean())
            for column in (
                "ret_5m_pct",
                "ret_10m_pct",
                "vwap_distance_pct",
                "opening_range_break",
            )
            if column in ledger
        },
        "rules": {
            name: {
                net: _summary(ledger[mask], net_column=net)
                for net in nets
                if net in ledger
            }
            for name, mask in masks.items()
        },
    }


def us_swing_state(
    *,
    swing_db: Path,
    daily_root: Path,
    policy_path: Path,
    historical_path: Path,
    execution_path: Path,
    status_path: Path,
) -> dict[str, Any]:
    historical = _load_json(historical_path)
    execution = _load_json(execution_path)
    status = _load_json(status_path)
    if not swing_db.exists() or not policy_path.exists():
        return {"status": "ARTIFACT_MISSING"}
    policy = load_swing_policy(policy_path)
    con = sqlite3.connect(swing_db)
    con.row_factory = sqlite3.Row
    try:
        counts = {
            str(row["status"]): int(row["count"])
            for row in con.execute(
                "SELECT status,COUNT(*) AS count FROM signals GROUP BY status"
            )
        }
        active = summarize_active_execution_shadow(
            con,
            price_dir=daily_root,
            policy=policy,
        )
        latest = [
            dict(row)
            for row in con.execute(
                """
                SELECT signal_date,ticker,rank,probability,predicted_net_pct,
                       status,execution_shadow_eligible,execution_shadow_reason
                FROM signals
                ORDER BY signal_date DESC,rank
                LIMIT 10
                """
            )
        ]
    finally:
        con.close()
    return {
        "status": "OK",
        "historical_top3": (historical.get("cohorts") or {}).get("top3"),
        "execution_micro": (execution.get("modes") or {}).get("micro"),
        "forward": status.get("forward_evidence") or {},
        "active_execution_shadow": active,
        "ledger_counts": counts,
        "latest_signals": latest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Review US-specific strategy surfaces")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB)
    parser.add_argument("--minute-root", type=Path, default=DEFAULT_MINUTE_ROOT)
    parser.add_argument("--daily-root", type=Path, default=DEFAULT_DAILY_ROOT)
    parser.add_argument("--swing-db", type=Path, default=DEFAULT_SWING_DB)
    parser.add_argument("--swing-policy", type=Path, default=DEFAULT_SWING_POLICY)
    parser.add_argument("--swing-historical", type=Path, default=DEFAULT_SWING_HISTORICAL)
    parser.add_argument("--swing-execution", type=Path, default=DEFAULT_SWING_EXECUTION)
    parser.add_argument("--swing-status", type=Path, default=DEFAULT_SWING_STATUS)
    parser.add_argument("--holdout-start", default="2026-07-01")
    parser.add_argument("--open15-start", default="2026-07-01")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ledger-output", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args()

    labels = pd.read_csv(args.labels, low_memory=False)
    open5 = evaluate_open5_candidate_pool(
        labels,
        holdout_start=args.holdout_start,
    )
    open15_ledger, open15_build = build_open15_ledger(
        audit_db=args.audit_db,
        minute_root=args.minute_root,
        session_start=args.open15_start,
    )
    args.ledger_output.parent.mkdir(parents=True, exist_ok=True)
    open15_ledger.to_csv(args.ledger_output, index=False, encoding="utf-8")
    swing = us_swing_state(
        swing_db=args.swing_db,
        daily_root=args.daily_root,
        policy_path=args.swing_policy,
        historical_path=args.swing_historical,
        execution_path=args.swing_execution,
        status_path=args.swing_status,
    )
    report = {
        "schema_version": "us_market_shape_strategy_review_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "authority": AUTHORITY,
        "contracts": {
            "open5": (
                "first complete bar at or after US regular open+5m; "
                "same-bar barrier ambiguity stop-first; round trip cost 0.50%"
            ),
            "open15": (
                "richest snapshot known by regular open+15.5m; enter next complete "
                "minute; no later data in features; cost 0.50%"
            ),
            "holdout": (
                f"fixed {args.holdout_start}+ holdout with the last pre-holdout "
                "session purged, plus one-session-purged expanding walk-forward"
            ),
        },
        "warnings": [
            "Open+15 post-open feature history currently covers only recent sessions and cannot promote a live rule.",
            "Candidate minute-price coverage is incomplete and trigger-oriented, so all positive findings would remain shadow-only.",
            "US swing historical evidence uses one market-data vendor; independent KIS execution cross-check remains required.",
            "STOP_FIRST is booked at the exact stop and excludes adverse stop slippage.",
        ],
        "open5_candidate_pool": open5,
        "open15_build": open15_build,
        "open15_contexts": evaluate_open15_contexts(open15_ledger),
        "open15_ledger": str(args.ledger_output.resolve()),
        "us_swing_5d": swing,
        "verdict": {
            "broad_opening_expansion": "REJECT_CURRENT_FORM",
            "generic_system_score_rescue": "REJECT_CURRENT_FORM",
            "open15_tape_rule": "COLLECT_PROSPECTIVE_ONLY",
            "us_swing_5d": "KEEP_BOUNDED_MICRO_TRIAL_AND_MATURE_FORWARD",
            "us_core_trend": "KEEP_AS_DEFAULT_US_CAPITAL_LANE",
            "next_us_specific_research": [
                "earnings D+1 to D+3 drift with structured surprise and announcement timing",
                "second-day continuation using time-normalized RVOL and prior-day in-play state",
                "opening-auction imbalance only after a licensed point-in-time NOII feed exists",
            ],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "ledger": str(args.ledger_output.resolve()),
                "open15_rows": int(len(open15_ledger)),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
