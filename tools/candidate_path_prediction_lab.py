from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "candidate_path_labels_v1"
DEFAULT_AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
DEFAULT_PRICE_ROOT = ROOT / "data" / "price" / "minute"
DEFAULT_OUTPUT = ROOT / "data" / "analysis" / "candidate_path_labels_v1.csv"
DEFAULT_META = ROOT / "data" / "analysis" / "candidate_path_labels_v1.meta.json"
DEFAULT_REPORT = ROOT / "reports" / "candidate_path_prediction_lab_20260716.json"

HORIZONS_MIN = (30, 60)
TARGETS_PCT = (1.6, 2.3, 3.6)
STOP_PCT = 2.5
ROUND_TRIP_COST_PCT = {"US": 0.50, "KR": 0.21}

NUMERIC_FEATURES = [
    "candidate_price",
    "change_pct",
    "volume_ratio",
    "turnover",
    "prompt_rank",
    "raw_rank",
    "trainer_prompt_score",
    "trainer_plan_a_score",
    "trainer_pathb_wait_score",
    "trainer_risk_score",
    "candidate_quality_score",
    "raw_score_current",
    "from_high_pct",
    "candidate_age_min",
    "price_change_since_first_seen_pct",
    "news_score",
    "news_or_earnings_count",
    "claude_trade_ready",
    "claude_watchlist",
    "market_open_elapsed_min",
    "ret_3m_pct",
    "ret_5m_pct",
    "ret_10m_pct",
    "ret_30m_pct",
    "volume_ratio_open",
    "time_normalized_rvol",
    "rvol_profile_sessions",
    "vwap_distance_pct",
    "pullback_from_high_pct",
    "from_open_high_pct",
    "spread_bps",
]

CATEGORICAL_FEATURES = [
    "claude_action",
    "recommended_strategy",
    "candidate_source",
    "liquidity_bucket",
    "market_type",
    "primary_bucket",
    "consensus_mode",
    "candidate_pool_role",
    "discovery_signal_family",
    "news_quality",
    "news_signal_type",
    "momentum_state",
    "opening_range_break",
    "post_open_data_quality",
]

FEATURE_GROUPS = {
    "system_scores": [
        "prompt_rank",
        "raw_rank",
        "trainer_prompt_score",
        "trainer_plan_a_score",
        "trainer_pathb_wait_score",
        "trainer_risk_score",
        "candidate_quality_score",
        "raw_score_current",
    ],
    "market_tape": [
        "change_pct",
        "volume_ratio",
        "turnover",
        "from_high_pct",
        "candidate_age_min",
        "price_change_since_first_seen_pct",
    ],
    "claude": [
        "claude_trade_ready",
        "claude_watchlist",
        "claude_action",
        "recommended_strategy",
        "consensus_mode",
    ],
    "setup_source": [
        "candidate_source",
        "liquidity_bucket",
        "market_type",
        "primary_bucket",
        "candidate_pool_role",
        "discovery_signal_family",
    ],
    "news_event": [
        "news_score",
        "news_or_earnings_count",
        "news_quality",
        "news_signal_type",
    ],
    "entry_context": [
        "market_open_elapsed_min",
        "ret_3m_pct",
        "ret_5m_pct",
        "ret_10m_pct",
        "ret_30m_pct",
        "volume_ratio_open",
        "time_normalized_rvol",
        "rvol_profile_sessions",
        "vwap_distance_pct",
        "pullback_from_high_pct",
        "from_open_high_pct",
        "spread_bps",
        "momentum_state",
        "opening_range_break",
        "post_open_data_quality",
    ],
    "combined": NUMERIC_FEATURES + CATEGORICAL_FEATURES,
}

POST_OPEN_NUMERIC = [
    "market_open_elapsed_min",
    "ret_3m_pct",
    "ret_5m_pct",
    "ret_10m_pct",
    "ret_30m_pct",
    "volume_ratio_open",
    "time_normalized_rvol",
    "rvol_profile_sessions",
    "vwap_distance_pct",
    "pullback_from_high_pct",
    "from_open_high_pct",
    "spread_bps",
]


@dataclass(frozen=True)
class BarrierResult:
    outcome: str
    target_before_stop: int
    target_hit_min: float | None
    stop_hit_min: float | None
    policy_gross_pct: float


def _tag(value: float) -> str:
    return str(value).replace(".", "p")


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_known_at(value: Any) -> pd.Timestamp | None:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    return None if pd.isna(parsed) else parsed


def _post_open_features(raw: Any, *, candidate_known_at: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(raw or ""))
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    snapshot_at = pd.to_datetime(payload.get("known_at"), errors="coerce")
    candidate_at = _parse_known_at(candidate_known_at)
    if not pd.isna(snapshot_at):
        if snapshot_at.tzinfo is None:
            snapshot_at = snapshot_at.tz_localize("Asia/Seoul")
        snapshot_at = snapshot_at.tz_convert("UTC")
        if candidate_at is not None and snapshot_at > candidate_at + pd.Timedelta(seconds=5):
            return {}
    result = {column: _float(payload.get(column)) for column in POST_OPEN_NUMERIC}
    result.update(
        {
            "momentum_state": str(payload.get("momentum_state") or ""),
            "opening_range_break": str(bool(payload.get("opening_range_break"))).lower()
            if payload.get("opening_range_break") is not None
            else "",
            "post_open_data_quality": str(payload.get("data_quality") or ""),
        }
    )
    return result


def next_complete_minute(value: Any) -> pd.Timestamp | None:
    """Return the first bar start whose entire OHLC is unknown at decision time."""

    known = _parse_known_at(value)
    if known is None:
        return None
    floored = known.floor("min")
    return floored if known == floored else floored + pd.Timedelta(minutes=1)


def session_entry_floor(value: Any, *, market: str, entry_lag_min: int = 0) -> pd.Timestamp | None:
    known = _parse_known_at(value)
    if known is None:
        return None
    market = str(market).upper()
    timezone_name, hour, minute = (
        ("Asia/Seoul", 9, 0) if market == "KR" else ("America/New_York", 9, 30)
    )
    local = known.tz_convert(timezone_name)
    session_open = local.normalize() + pd.Timedelta(hours=hour, minutes=minute + int(entry_lag_min))
    return session_open.tz_convert("UTC")


def first_passage(
    window: pd.DataFrame,
    *,
    entry_price: float,
    target_pct: float,
    stop_pct: float,
    horizon_return_pct: float,
) -> BarrierResult:
    """Resolve ordered barriers; an ambiguous same-bar touch is stop-first."""

    target_price = float(entry_price) * (1.0 + float(target_pct) / 100.0)
    stop_price = float(entry_price) * (1.0 - float(stop_pct) / 100.0)
    target_minutes: list[float] = []
    stop_minutes: list[float] = []
    for row in window.itertuples(index=False):
        minute = float(getattr(row, "elapsed_min"))
        target_hit = float(getattr(row, "high")) >= target_price
        stop_hit = float(getattr(row, "low")) <= stop_price
        if target_hit:
            target_minutes.append(minute)
        if stop_hit:
            stop_minutes.append(minute)
    target_at = min(target_minutes) if target_minutes else None
    stop_at = min(stop_minutes) if stop_minutes else None
    if target_at is not None and (stop_at is None or target_at < stop_at):
        return BarrierResult("TARGET_FIRST", 1, target_at, stop_at, float(target_pct))
    if stop_at is not None:
        return BarrierResult("STOP_FIRST", 0, target_at, stop_at, -float(stop_pct))
    return BarrierResult("NO_TOUCH", 0, None, None, float(horizon_return_pct))


def label_candidate_path(
    bars: pd.DataFrame,
    *,
    known_at: Any,
    market: str,
    entry_lag_min: int = 0,
    max_entry_delay_min: float = 10.0,
    min_coverage_ratio: float = 0.80,
) -> tuple[dict[str, Any] | None, str]:
    candidate_minute = next_complete_minute(known_at)
    entry_floor = session_entry_floor(known_at, market=market, entry_lag_min=entry_lag_min)
    known = _parse_known_at(known_at)
    if candidate_minute is None or entry_floor is None or known is None:
        return None, "known_at_invalid"
    entry_minute = max(candidate_minute, entry_floor)
    if bars.empty:
        return None, "price_file_empty"
    usable = bars[bars["ts_utc"] >= entry_minute].copy()
    if usable.empty:
        return None, "no_bar_after_known_at"
    first = usable.iloc[0]
    entry_ts = first["ts_utc"]
    # 진입 봉은 후보와 같은 세션이어야 한다. 당일 분봉이 없으면 다음 세션 봉이
    # 진입가로 선택돼 후보 시점 피처와 며칠 뒤 체결이 섞인다(장전 후보 14/27 오염 실측).
    timezone_name = "Asia/Seoul" if str(market).upper() == "KR" else "America/New_York"
    if entry_ts.tz_convert(timezone_name).date() != entry_floor.tz_convert(timezone_name).date():
        return None, "no_same_session_bar"
    delay_sec = float((entry_ts - known).total_seconds())
    # 지연 상한은 장중·장전 후보 모두에 적용한다. 기준은 계약 진입 시점(entry_minute)
    # — 장전 후보는 개장+lag, 장중 후보는 후보 직후 완전 분봉.
    lateness_sec = float((entry_ts - entry_minute).total_seconds())
    if delay_sec < 0 or lateness_sec > float(max_entry_delay_min) * 60.0:
        return None, "entry_delay_exceeded"
    entry_price = _float(first["open"])
    if entry_price is None or entry_price <= 0:
        return None, "entry_price_invalid"
    usable["elapsed_min"] = (usable["ts_utc"] - entry_ts).dt.total_seconds() / 60.0
    result: dict[str, Any] = {
        "entry_ts": entry_ts.isoformat(),
        "entry_price": entry_price,
        "entry_delay_sec": delay_sec,
        "price_source": str(first.get("source") or ""),
    }
    cost = ROUND_TRIP_COST_PCT[str(market).upper()]
    available_horizons = 0
    for horizon in HORIZONS_MIN:
        window = usable[(usable["elapsed_min"] >= 0) & (usable["elapsed_min"] < horizon)].copy()
        tag = f"h{horizon}"
        result[f"{tag}_bar_count"] = int(len(window))
        required = max(1, int(math.ceil(horizon * float(min_coverage_ratio))))
        if len(window) < required:
            result[f"{tag}_label_available"] = 0
            continue
        available_horizons += 1
        result[f"{tag}_label_available"] = 1
        high = float(window["high"].max())
        low = float(window["low"].min())
        last_close = float(window.iloc[-1]["close"])
        return_pct = (last_close / entry_price - 1.0) * 100.0
        result[f"{tag}_return_pct"] = return_pct
        result[f"{tag}_mfe_pct"] = (high / entry_price - 1.0) * 100.0
        result[f"{tag}_mae_pct"] = (low / entry_price - 1.0) * 100.0
        for target in TARGETS_PCT:
            barrier = first_passage(
                window,
                entry_price=entry_price,
                target_pct=target,
                stop_pct=STOP_PCT,
                horizon_return_pct=return_pct,
            )
            prefix = f"{tag}_t{_tag(target)}_s{_tag(STOP_PCT)}"
            result[f"{prefix}_outcome"] = barrier.outcome
            result[f"{prefix}_target_before_stop"] = barrier.target_before_stop
            result[f"{prefix}_target_hit_min"] = barrier.target_hit_min
            result[f"{prefix}_stop_hit_min"] = barrier.stop_hit_min
            result[f"{prefix}_policy_gross_pct"] = barrier.policy_gross_pct
            result[f"{prefix}_policy_net_pct"] = barrier.policy_gross_pct - cost
    if available_horizons == 0:
        return None, "insufficient_horizon_coverage"
    return result, "ok"


def _read_price(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, usecols=lambda c: c in {"ts", "open", "high", "low", "close", "source"})
    if "source" not in frame.columns:
        frame["source"] = ""
    frame["ts_utc"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return (
        frame.dropna(subset=["ts_utc", "open", "high", "low", "close"])
        .sort_values("ts_utc")
        .drop_duplicates("ts_utc", keep="last")
        .reset_index(drop=True)
    )


def load_first_candidates(audit_db: Path, *, markets: Iterable[str]) -> pd.DataFrame:
    selected = [str(value).upper() for value in markets]
    placeholders = ",".join("?" for _ in selected)
    output_columns = [
        "candidate_key",
        "market",
        "session_date",
        "known_at",
        "ticker",
        "candidate_price",
        "change_pct",
        "volume_ratio",
        "turnover",
        "prompt_rank",
        "raw_rank",
        "trainer_prompt_score",
        "trainer_plan_a_score",
        "trainer_pathb_wait_score",
        "trainer_risk_score",
        "candidate_quality_score",
        "raw_score_current",
        "from_high_pct",
        "candidate_age_min",
        "price_change_since_first_seen_pct",
        "news_score",
        "news_or_earnings_count",
        "claude_trade_ready",
        "claude_watchlist",
        "claude_action",
        "recommended_strategy",
        "candidate_source",
        "liquidity_bucket",
        "market_type",
        "primary_bucket",
        "consensus_mode",
        "candidate_pool_role",
        "discovery_signal_family",
        "news_quality",
        "news_signal_type",
        "post_open_features_json",
    ]
    columns = [
        "candidate_key",
        "market",
        "session_date",
        "known_at",
        "ticker",
        "price AS candidate_price",
        "change_pct",
        "volume_ratio",
        "turnover",
        "prompt_rank",
        "raw_rank",
        "trainer_prompt_score",
        "trainer_plan_a_score",
        "trainer_pathb_wait_score",
        "trainer_risk_score",
        "candidate_quality_score",
        "raw_score_current",
        "from_high_pct",
        "candidate_age_min",
        "price_change_since_first_seen_pct",
        "news_score",
        "news_or_earnings_count",
        "claude_trade_ready",
        "claude_watchlist",
        "claude_action",
        "recommended_strategy",
        "candidate_source",
        "liquidity_bucket",
        "market_type",
        "primary_bucket",
        "consensus_mode",
        "candidate_pool_role",
        "discovery_signal_family",
        "news_quality",
        "news_signal_type",
        "post_open_features_json",
    ]
    sql = f"""
        WITH ranked AS (
            SELECT {','.join(columns)},
                   ROW_NUMBER() OVER (
                       PARTITION BY market,session_date,UPPER(ticker)
                       ORDER BY known_at,candidate_key
                   ) AS rn
            FROM audit_candidate_rows
            WHERE runtime_mode='live'
              AND known_at IS NOT NULL
              AND market IN ({placeholders})
        )
        SELECT * FROM ranked WHERE rn=1
        ORDER BY market,session_date,ticker
    """
    uri = f"file:{audit_db.resolve().as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        registry_exists = con.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='candidate_registry_first'
            """
        ).fetchone()
        if registry_exists:
            registry_rows = con.execute(
                f"""
                SELECT registry_key, market, session_date, ticker,
                       first_seen_at, first_price, first_snapshot_json
                FROM candidate_registry_first
                WHERE runtime_mode='live'
                  AND first_seen_at IS NOT NULL
                  AND market IN ({placeholders})
                ORDER BY market, session_date, ticker
                """,
                selected,
            ).fetchall()
            if registry_rows:
                records: list[dict[str, Any]] = []
                for raw in registry_rows:
                    try:
                        snapshot = json.loads(str(raw["first_snapshot_json"] or "{}"))
                    except (TypeError, ValueError):
                        snapshot = {}
                    if not isinstance(snapshot, dict):
                        snapshot = {}
                    record = {column: snapshot.get(column) for column in output_columns}
                    record.update(
                        {
                            "candidate_key": raw["registry_key"],
                            "market": raw["market"],
                            "session_date": raw["session_date"],
                            "known_at": raw["first_seen_at"],
                            "ticker": raw["ticker"],
                            "candidate_price": raw["first_price"],
                        }
                    )
                    post_open = record.get("post_open_features_json")
                    if isinstance(post_open, dict):
                        record["post_open_features_json"] = json.dumps(
                            post_open,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    records.append(record)
                return pd.DataFrame(records, columns=output_columns)
        return pd.read_sql_query(sql, con, params=selected)
    finally:
        con.close()


def generate_labels(
    *,
    audit_db: Path,
    price_root: Path,
    markets: list[str],
    output: Path,
    meta_output: Path,
    entry_lag_min: int = 0,
) -> dict[str, Any]:
    candidates = load_first_candidates(audit_db, markets=markets)
    rows: list[dict[str, Any]] = []
    reasons: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    price_files_used: set[str] = set()
    for (market, ticker), group in candidates.groupby(["market", "ticker"], sort=False):
        path = price_root / str(market).lower() / f"{str(market).lower()}_{ticker}.csv"
        if not path.exists():
            reasons[str(market)]["price_file_missing"] += int(len(group))
            continue
        try:
            bars = _read_price(path)
        except Exception:
            reasons[str(market)]["price_file_unreadable"] += int(len(group))
            continue
        price_files_used.add(str(path.resolve()))
        for candidate in group.to_dict("records"):
            labels, reason = label_candidate_path(
                bars,
                known_at=candidate.get("known_at"),
                market=str(market),
                entry_lag_min=entry_lag_min,
            )
            reasons[str(market)][reason] += 1
            if labels is None:
                continue
            clean = {key: value for key, value in candidate.items() if key != "rn"}
            clean.update(
                _post_open_features(
                    clean.pop("post_open_features_json", ""),
                    candidate_known_at=clean.get("known_at"),
                )
            )
            clean.update(labels)
            candidate_price = _float(clean.get("candidate_price"))
            clean["entry_vs_candidate_pct"] = (
                (float(clean["entry_price"]) / candidate_price - 1.0) * 100.0
                if candidate_price is not None and candidate_price > 0
                else None
            )
            clean["schema_version"] = SCHEMA_VERSION
            rows.append(clean)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(output, index=False, encoding="utf-8")
    meta = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "audit_db": str(audit_db.resolve()),
        "audit_db_size": audit_db.stat().st_size,
        "price_root": str(price_root.resolve()),
        "output": str(output.resolve()),
        "candidate_contract": "first live market/session/ticker observation",
        "entry_contract": (
            f"first complete minute bar at or after known_at and market open+{entry_lag_min}m; "
            "entry bar must belong to the candidate session (no_same_session_bar otherwise); "
            "lateness beyond the contract entry point <=10m for pre-open and intraday candidates"
        ),
        "entry_lag_min": int(entry_lag_min),
        "bar_contract": "at least 80% of requested 30m/60m bars",
        "same_bar_contract": "stop-first",
        "horizons_min": list(HORIZONS_MIN),
        "targets_pct": list(TARGETS_PCT),
        "stop_pct": STOP_PCT,
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "source_candidates": int(len(candidates)),
        "labeled_rows": int(len(frame)),
        "by_market": {
            market: {
                "source_candidates": int((candidates["market"] == market).sum()),
                "labeled_rows": int((frame["market"] == market).sum()) if not frame.empty else 0,
                "reason_counts": dict(reasons[market]),
            }
            for market in markets
        },
        "price_files_used": len(price_files_used),
    }
    meta_output.parent.mkdir(parents=True, exist_ok=True)
    meta_output.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def _ece(y_true: np.ndarray, probability: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = max(1, len(y_true))
    value = 0.0
    for idx in range(bins):
        if idx == bins - 1:
            mask = (probability >= edges[idx]) & (probability <= edges[idx + 1])
        else:
            mask = (probability >= edges[idx]) & (probability < edges[idx + 1])
        if not mask.any():
            continue
        value += float(mask.sum()) / total * abs(float(y_true[mask].mean()) - float(probability[mask].mean()))
    return value


def _block_lcb(values: pd.DataFrame, *, seed: int = 20260716, draws: int = 2000) -> float | None:
    if values.empty or values["session_date"].nunique() < 3:
        return None
    daily = values.groupby("session_date")["_net"].mean().to_numpy(dtype=float)
    rng = np.random.RandomState(seed)
    samples = np.asarray([rng.choice(daily, size=len(daily), replace=True).mean() for _ in range(draws)])
    return float(np.quantile(samples, 0.05))


def _daily_top(rows: pd.DataFrame, score: np.ndarray, *, fraction: float | None = None, top_k: int | None = None) -> pd.DataFrame:
    ranked = rows.copy()
    ranked["_score"] = np.asarray(score, dtype=float)
    selected: list[pd.DataFrame] = []
    for _date, group in ranked.groupby("session_date", sort=True):
        ordered = group.sort_values(["_score", "ticker"], ascending=[False, True])
        if top_k is not None:
            selected.append(ordered.head(top_k))
        else:
            count = max(1, int(math.ceil(len(ordered) * float(fraction or 0.1))))
            selected.append(ordered.head(count))
    return pd.concat(selected, ignore_index=True) if selected else ranked.iloc[0:0]


def _selection_stats(rows: pd.DataFrame) -> dict[str, Any]:
    values = rows["_net"].astype(float).tolist()
    ex_top3 = sorted(values, reverse=True)[3:] if len(values) > 3 else []
    return {
        "n": len(values),
        "dates": int(rows["session_date"].nunique()) if len(rows) else 0,
        "mean_policy_net_pct": float(np.mean(values)) if values else None,
        "win_rate": float(np.mean(np.asarray(values) > 0)) if values else None,
        "ex_top3_mean_pct": float(np.mean(ex_top3)) if ex_top3 else None,
        "session_block_lcb_pct": _block_lcb(rows),
    }


def _metric_summary(
    rows: pd.DataFrame,
    probability: np.ndarray,
    *,
    label: str,
    net_column: str,
    rank_score: np.ndarray | None = None,
) -> dict[str, Any]:
    y = rows[label].astype(int).to_numpy()
    net = rows[net_column].astype(float).to_numpy()
    auc = float(roc_auc_score(y, probability)) if len(np.unique(y)) > 1 else None
    ranked = rows.copy()
    ranked["_net"] = net
    score = np.asarray(rank_score if rank_score is not None else probability, dtype=float)
    top_decile = _daily_top(ranked, score, fraction=0.10)
    top1 = _daily_top(ranked, score, top_k=1)
    top3 = _daily_top(ranked, score, top_k=3)
    return {
        "n": int(len(rows)),
        "positive_rate": float(y.mean()) if len(y) else None,
        "auc": auc,
        "brier": float(brier_score_loss(y, probability)) if len(y) else None,
        "ece": _ece(y, probability) if len(y) else None,
        "all_mean_policy_net_pct": float(net.mean()) if len(net) else None,
        "daily_top_decile": _selection_stats(top_decile),
        "daily_top_decile_uplift_pct": float(top_decile["_net"].mean() - net.mean()) if len(top_decile) else None,
        "daily_top1": _selection_stats(top1),
        "daily_top3": _selection_stats(top3),
    }


def _pipeline(features: list[str]) -> Pipeline:
    numeric = [value for value in features if value in NUMERIC_FEATURES]
    categorical = [value for value in features if value in CATEGORICAL_FEATURES]
    transformers: list[tuple[str, Any, list[str]]] = []
    if numeric:
        transformers.append(
            (
                "num",
                Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)), ("scale", StandardScaler())]),
                numeric,
            )
        )
    if categorical:
        transformers.append(
            (
                "cat",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical,
            )
        )
    return Pipeline(
        [
            ("pre", ColumnTransformer(transformers, sparse_threshold=0.3)),
            ("model", LogisticRegression(C=0.25, max_iter=1500, solver="liblinear")),
        ]
    )


def _multiclass_pipeline(features: list[str]) -> Pipeline:
    pipeline = _pipeline(features)
    pipeline.set_params(model=LogisticRegression(C=0.25, max_iter=2000, solver="lbfgs"))
    return pipeline


def walk_forward(
    frame: pd.DataFrame,
    *,
    market: str,
    features: list[str],
    label: str,
    net_column: str,
    min_train_dates: int = 10,
    purge_dates: int = 1,
) -> dict[str, Any]:
    data = frame.copy()
    for column in NUMERIC_FEATURES:
        if column in data:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    for column in CATEGORICAL_FEATURES:
        if column in data:
            data[column] = data[column].fillna("__MISSING__").astype(str)
    dates = sorted(str(value) for value in data["session_date"].dropna().unique())
    predictions: list[dict[str, Any]] = []
    for index in range(min_train_dates + purge_dates, len(dates)):
        test_date = dates[index]
        train_dates = dates[: index - purge_dates]
        train = data[data["session_date"].astype(str).isin(train_dates)]
        test = data[data["session_date"].astype(str) == test_date]
        if len(train) < 200 or len(test) < 3 or train[label].nunique() < 2:
            continue
        model = _pipeline(features)
        model.fit(train[features], train[label].astype(int))
        probability = model.predict_proba(test[features])[:, 1]
        base_probability = float(train[label].mean())
        for row_index, (_, row) in enumerate(test.iterrows()):
            predictions.append(
                {
                    "session_date": test_date,
                    "ticker": str(row["ticker"]),
                    "probability": float(probability[row_index]),
                    "base_probability": base_probability,
                    "label": int(row[label]),
                    "policy_net_pct": float(row[net_column]),
                }
            )
    if not predictions:
        return {"market": market, "features": features, "tested_rows": 0, "status": "insufficient_walk_forward"}
    scored = pd.DataFrame(predictions)
    renamed = scored.rename(columns={"label": "_label", "policy_net_pct": "_net"})
    model_metrics = _metric_summary(renamed, scored["probability"].to_numpy(), label="_label", net_column="_net")
    base_metrics = _metric_summary(renamed, scored["base_probability"].to_numpy(), label="_label", net_column="_net")
    by_month: dict[str, Any] = {}
    for month, group in scored.groupby(scored["session_date"].str[:7]):
        metrics_frame = group.rename(columns={"label": "_label", "policy_net_pct": "_net"})
        by_month[str(month)] = _metric_summary(
            metrics_frame,
            group["probability"].to_numpy(),
            label="_label",
            net_column="_net",
        )
    return {
        "market": market,
        "features": features,
        "date_count": len(dates),
        "tested_rows": len(scored),
        "model": model_metrics,
        "constant_base_rate": base_metrics,
        "by_month": by_month,
    }


def walk_forward_multistate(
    frame: pd.DataFrame,
    *,
    market: str,
    features: list[str],
    outcome_column: str,
    label: str,
    net_column: str,
    min_train_dates: int = 10,
    purge_dates: int = 1,
) -> dict[str, Any]:
    """Rank candidates by point-in-time expected policy net across all path states."""

    data = frame.copy()
    for column in NUMERIC_FEATURES:
        if column in data:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    for column in CATEGORICAL_FEATURES:
        if column in data:
            data[column] = data[column].fillna("__MISSING__").astype(str)
    dates = sorted(str(value) for value in data["session_date"].dropna().unique())
    predictions: list[dict[str, Any]] = []
    required_states = {"TARGET_FIRST", "STOP_FIRST", "NO_TOUCH"}
    for index in range(min_train_dates + purge_dates, len(dates)):
        test_date = dates[index]
        train_dates = dates[: index - purge_dates]
        train = data[data["session_date"].astype(str).isin(train_dates)]
        test = data[data["session_date"].astype(str) == test_date]
        train_states = set(train[outcome_column].dropna().astype(str))
        if len(train) < 200 or len(test) < 3 or not required_states.issubset(train_states):
            continue
        model = _multiclass_pipeline(features)
        model.fit(train[features], train[outcome_column].astype(str))
        state_probability = model.predict_proba(test[features])
        classes = [str(value) for value in model.named_steps["model"].classes_]
        class_payoff = train.groupby(outcome_column)[net_column].mean().to_dict()
        expected_net = np.zeros(len(test), dtype=float)
        for class_index, state in enumerate(classes):
            expected_net += state_probability[:, class_index] * float(class_payoff[state])
        target_index = classes.index("TARGET_FIRST")
        target_probability = state_probability[:, target_index]
        for row_index, (_, row) in enumerate(test.iterrows()):
            predictions.append(
                {
                    "session_date": test_date,
                    "ticker": str(row["ticker"]),
                    "target_probability": float(target_probability[row_index]),
                    "expected_net_pct": float(expected_net[row_index]),
                    "label": int(row[label]),
                    "policy_net_pct": float(row[net_column]),
                }
            )
    if not predictions:
        return {"market": market, "features": features, "tested_rows": 0, "status": "insufficient_walk_forward"}
    scored = pd.DataFrame(predictions)
    renamed = scored.rename(columns={"label": "_label", "policy_net_pct": "_net"})
    model_metrics = _metric_summary(
        renamed,
        scored["target_probability"].to_numpy(),
        label="_label",
        net_column="_net",
        rank_score=scored["expected_net_pct"].to_numpy(),
    )
    by_month: dict[str, Any] = {}
    for month, group in scored.groupby(scored["session_date"].str[:7]):
        metrics_frame = group.rename(columns={"label": "_label", "policy_net_pct": "_net"})
        by_month[str(month)] = _metric_summary(
            metrics_frame,
            group["target_probability"].to_numpy(),
            label="_label",
            net_column="_net",
            rank_score=group["expected_net_pct"].to_numpy(),
        )
    return {
        "market": market,
        "features": features,
        "date_count": len(dates),
        "tested_rows": len(scored),
        "ranking_objective": "train-only expected policy net from TARGET_FIRST/STOP_FIRST/NO_TOUCH probabilities",
        "model": model_metrics,
        "by_month": by_month,
    }


def walk_forward_rank_ensemble(
    frame: pd.DataFrame,
    *,
    market: str,
    feature_sets: dict[str, list[str]],
    label: str,
    net_column: str,
    min_train_dates: int = 10,
    purge_dates: int = 1,
) -> dict[str, Any]:
    """Exploratory consensus of independent feature-family ranks."""

    data = frame.copy()
    for column in NUMERIC_FEATURES:
        if column in data:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    for column in CATEGORICAL_FEATURES:
        if column in data:
            data[column] = data[column].fillna("__MISSING__").astype(str)
    dates = sorted(str(value) for value in data["session_date"].dropna().unique())
    predictions: list[dict[str, Any]] = []
    for index in range(min_train_dates + purge_dates, len(dates)):
        test_date = dates[index]
        train_dates = dates[: index - purge_dates]
        train = data[data["session_date"].astype(str).isin(train_dates)]
        test = data[data["session_date"].astype(str) == test_date]
        if len(train) < 200 or len(test) < 3 or train[label].nunique() < 2:
            continue
        family_probability: list[np.ndarray] = []
        for features in feature_sets.values():
            model = _pipeline(features)
            model.fit(train[features], train[label].astype(int))
            family_probability.append(model.predict_proba(test[features])[:, 1])
        ranks = np.vstack([pd.Series(values).rank(pct=True).to_numpy() for values in family_probability])
        consensus_score = ranks.mean(axis=0)
        target_probability = np.vstack(family_probability).mean(axis=0)
        for row_index, (_, row) in enumerate(test.iterrows()):
            predictions.append(
                {
                    "session_date": test_date,
                    "ticker": str(row["ticker"]),
                    "target_probability": float(target_probability[row_index]),
                    "consensus_score": float(consensus_score[row_index]),
                    "label": int(row[label]),
                    "policy_net_pct": float(row[net_column]),
                }
            )
    if not predictions:
        return {"market": market, "tested_rows": 0, "status": "insufficient_walk_forward"}
    scored = pd.DataFrame(predictions)
    renamed = scored.rename(columns={"label": "_label", "policy_net_pct": "_net"})
    metrics = _metric_summary(
        renamed,
        scored["target_probability"].to_numpy(),
        label="_label",
        net_column="_net",
        rank_score=scored["consensus_score"].to_numpy(),
    )
    by_month: dict[str, Any] = {}
    for month, group in scored.groupby(scored["session_date"].str[:7]):
        metrics_frame = group.rename(columns={"label": "_label", "policy_net_pct": "_net"})
        by_month[str(month)] = _metric_summary(
            metrics_frame,
            group["target_probability"].to_numpy(),
            label="_label",
            net_column="_net",
            rank_score=group["consensus_score"].to_numpy(),
        )
    top1_ledger = _daily_top(
        scored.rename(columns={"policy_net_pct": "_net"}),
        scored["consensus_score"].to_numpy(),
        top_k=1,
    )
    return {
        "market": market,
        "feature_families": list(feature_sets),
        "date_count": len(dates),
        "tested_rows": len(scored),
        "post_selection_exploratory": True,
        "ranking_objective": "mean within-session rank of system_scores, market_tape, and claude",
        "model": metrics,
        "by_month": by_month,
        "daily_top1_ledger": top1_ledger[
            ["session_date", "ticker", "target_probability", "consensus_score", "label", "_net"]
        ].to_dict(orient="records"),
    }


def analyze_labels(input_path: Path, *, report_path: Path) -> dict[str, Any]:
    frame = pd.read_csv(input_path, low_memory=False)
    report: dict[str, Any] = {
        "schema_version": "candidate_path_prediction_analysis_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path.resolve()),
        "method": "expanding date walk-forward; one purged session; first candidate per market/session/ticker",
        "markets": {},
    }
    for market in ("US", "KR"):
        target = 2.3 if market == "US" else 3.6
        prefix = f"h60_t{_tag(target)}_s{_tag(STOP_PCT)}"
        label = f"{prefix}_target_before_stop"
        outcome_column = f"{prefix}_outcome"
        net_column = f"{prefix}_policy_net_pct"
        subset = frame[(frame["market"] == market) & (frame["h60_label_available"] == 1)].copy()
        subset = subset.dropna(subset=[label, net_column])
        market_result = {
            "target_pct": target,
            "stop_pct": STOP_PCT,
            "cost_pct": ROUND_TRIP_COST_PCT[market],
            "rows": int(len(subset)),
            "session_dates": int(subset["session_date"].nunique()),
            "date_range": [str(subset["session_date"].min()), str(subset["session_date"].max())] if len(subset) else [],
            "base_rate": float(subset[label].mean()) if len(subset) else None,
            "groups": {},
        }
        for name, features in FEATURE_GROUPS.items():
            available = [value for value in features if value in subset.columns]
            market_result["groups"][name] = walk_forward(
                subset,
                market=market,
                features=available,
                label=label,
                net_column=net_column,
            )
        combined_features = [value for value in FEATURE_GROUPS["combined"] if value in subset.columns]
        market_result["groups"]["combined_multistate_expected_net"] = walk_forward_multistate(
            subset,
            market=market,
            features=combined_features,
            outcome_column=outcome_column,
            label=label,
            net_column=net_column,
        )
        ensemble_families = {
            name: [value for value in FEATURE_GROUPS[name] if value in subset.columns]
            for name in ("system_scores", "market_tape", "claude")
        }
        market_result["groups"]["three_family_rank_ensemble"] = walk_forward_rank_ensemble(
            subset,
            market=market,
            feature_sets=ensemble_families,
            label=label,
            net_column=net_column,
        )
        report["markets"][market] = market_result
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate point-in-time candidate path labels and test prediction lift")
    parser.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB)
    parser.add_argument("--price-root", type=Path, default=DEFAULT_PRICE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--meta-output", type=Path, default=DEFAULT_META)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--markets", default="KR,US")
    parser.add_argument("--entry-lag-min", type=int, default=0)
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--analyze-only", action="store_true")
    args = parser.parse_args()
    markets = [value.strip().upper() for value in args.markets.split(",") if value.strip()]
    if not args.analyze_only:
        meta = generate_labels(
            audit_db=args.audit_db,
            price_root=args.price_root,
            markets=markets,
            output=args.output,
            meta_output=args.meta_output,
            entry_lag_min=args.entry_lag_min,
        )
        print(json.dumps({"generation": meta}, ensure_ascii=False, indent=2))
    if not args.generate_only:
        report = analyze_labels(args.output, report_path=args.report)
        print(json.dumps({"analysis_report": str(args.report.resolve()), "markets": report["markets"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
