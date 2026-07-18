from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path
from tools.candidate_consensus_status import write_candidate_consensus_status
from tools.candidate_path_prediction_lab import (
    CATEGORICAL_FEATURES,
    FEATURE_GROUPS,
    STOP_PCT,
    _post_open_features,
    _tag,
    load_first_candidates,
    session_entry_floor,
)
from tools.candidate_universe_enhancement_lab import (
    DAILY_FEATURES,
    KR_RICH_FEATURES,
    _float,
    _pipeline,
    add_daily_features,
    load_kr_rich_features,
)


AUTHORITY = "SHADOW_ONLY_NO_ORDER_AUTHORITY"
DEFAULT_INPUT = ROOT / "data" / "analysis" / "candidate_path_labels_lag5_v1.csv"
DEFAULT_AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
DEFAULT_PRICE_ROOT = ROOT / "data" / "price"
DEFAULT_SCREENER_ROOT = ROOT / "logs" / "screener_quality"


def _consensus_decision(
    *,
    left_selected: bool,
    right_selected: bool,
    shared_features: list[str],
) -> tuple[str, str]:
    if shared_features:
        return "ABSTAIN", "feature_overlap_fail_closed"
    if left_selected and right_selected:
        return "SELECT_SHADOW", ""
    return "ABSTAIN", "top3_intersection_not_met"


def _opening_rows(frame: pd.DataFrame, market: str) -> pd.DataFrame:
    subset = frame[frame["market"].astype(str).str.upper() == market].copy()
    known = pd.to_datetime(subset["known_at"], utc=True, errors="coerce")
    floor = pd.Series(
        [session_entry_floor(value, market=market, entry_lag_min=5) for value in subset["known_at"]],
        index=subset.index,
    )
    return subset[known.notna() & floor.notna() & (known <= floor)].copy()


def _current_candidates(
    *,
    audit_db: Path,
    session_date: str,
    markets: list[str],
) -> pd.DataFrame:
    frame = load_first_candidates(audit_db, markets=markets)
    if frame.empty:
        return frame
    frame = frame[frame["session_date"].astype(str) == str(session_date)].copy()
    if frame.empty:
        return frame
    # RVOL 등 개장 후 피처는 candidate 최초 관측(장전)이 아니라 진입 시점(개장+5분,
    # lab의 lag5 계약)에 알 수 있는 정보다. 그 시점을 기준선으로 넘겨야 진입시점 피처가
    # lookahead로 오판돼 드롭되지 않는다(개장+5분 이후 관측은 여전히 차단).
    from tools.candidate_path_prediction_lab import session_entry_floor

    post_open = [
        _post_open_features(
            raw,
            candidate_known_at=(
                session_entry_floor(known_at, market=str(market), entry_lag_min=5) or known_at
            ),
        )
        for raw, known_at, market in zip(
            frame["post_open_features_json"], frame["known_at"], frame["market"]
        )
    ]
    frame = pd.concat(
        [
            frame.drop(columns=["post_open_features_json"], errors="ignore").reset_index(drop=True),
            pd.DataFrame(post_open),
        ],
        axis=1,
    )
    frame["entry_price"] = pd.to_numeric(frame["candidate_price"], errors="coerce")
    frame["ticker"] = frame.apply(
        lambda row: str(row["ticker"]).upper()
        if str(row["market"]).upper() == "US"
        else str(row["ticker"]).zfill(6),
        axis=1,
    )
    return frame


def _arm_specs(enriched: pd.DataFrame) -> dict[str, dict[str, Any]]:
    baseline = [column for column in FEATURE_GROUPS["combined"] if column in enriched]
    system_scores = [column for column in FEATURE_GROUPS["system_scores"] if column in enriched]
    return {
        "US": {
            "left": {
                "name": "US_BASELINE_LOGIT",
                "features": baseline,
                "model_kind": "logistic",
                "multistate": True,
            },
            "right": {
                "name": "US_DAILY_ONLY_FOREST",
                "features": [column for column in DAILY_FEATURES if column in enriched],
                "model_kind": "forest",
                "multistate": True,
            },
        },
        "KR": {
            "left": {
                "name": "KR_SYSTEM_SCORES_LOGIT",
                "features": system_scores,
                "model_kind": "logistic",
                "multistate": False,
            },
            "right": {
                "name": "KR_DAILY_RICH_FOREST",
                "features": [
                    column
                    for column in DAILY_FEATURES + KR_RICH_FEATURES
                    if column in enriched
                ],
                "model_kind": "forest",
                "multistate": False,
            },
        },
    }


def _serveable_features(
    train: pd.DataFrame,
    serve: pd.DataFrame,
    features: list[str],
    categorical_features: list[str],
) -> tuple[list[str], list[str]]:
    categorical = set(categorical_features)
    available: list[str] = []
    dropped: list[str] = []
    for column in features:
        if column not in train or column not in serve:
            dropped.append(column)
            continue
        if column in categorical:
            train_observed = train[column].notna() & train[column].astype(str).str.strip().ne("")
            serve_observed = serve[column].notna() & serve[column].astype(str).str.strip().ne("")
        else:
            train_observed = pd.to_numeric(train[column], errors="coerce").notna()
            serve_observed = pd.to_numeric(serve[column], errors="coerce").notna()
        if bool(train_observed.any()) and bool(serve_observed.any()):
            available.append(column)
        else:
            dropped.append(column)
    return available, dropped


def _score_arm(
    *,
    historical: pd.DataFrame,
    current: pd.DataFrame,
    market: str,
    session_date: str,
    spec: dict[str, Any],
    target: float = 3.6,
) -> tuple[dict[str, Any], pd.DataFrame]:
    prefix = f"h60_t{_tag(target)}_s{_tag(STOP_PCT)}"
    label = f"{prefix}_target_before_stop"
    outcome = f"{prefix}_outcome"
    net = f"{prefix}_policy_net_pct"
    train = _opening_rows(historical, market)
    train = train[train["session_date"].astype(str) < str(session_date)].copy()
    train = train.dropna(subset=[label, outcome, net])
    train_dates = sorted(train["session_date"].astype(str).unique())
    purged_session = train_dates[-1] if train_dates else ""
    if purged_session:
        train = train[train["session_date"].astype(str) < purged_session].copy()
    serve = _opening_rows(current, market)
    requested_features = list(spec.get("features") or [])
    requested_categorical = [
        column for column in CATEGORICAL_FEATURES if column in requested_features
    ]
    features, dropped_features = _serveable_features(
        train,
        serve,
        requested_features,
        requested_categorical,
    )
    categorical = [column for column in requested_categorical if column in features]
    if train.empty or serve.empty or not features:
        return {
            "status": "NO_SCORE",
            "reason": (
                "train_or_serve_empty"
                if train.empty or serve.empty
                else "no_serveable_features"
            ),
            "arm": spec["name"],
            "train_rows": int(len(train)),
            "serve_rows": int(len(serve)),
            "purged_session": purged_session,
            "requested_features": requested_features,
            "features": features,
            "dropped_unavailable_features": dropped_features,
        }, pd.DataFrame()

    for frame in (train, serve):
        for column in features:
            if column not in frame:
                frame[column] = np.nan
            if column in categorical:
                frame[column] = frame[column].fillna("__MISSING__").astype(str)
            else:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
    multistate = bool(spec.get("multistate"))
    target_values = train[outcome].astype(str) if multistate else train[label].astype(int)
    if target_values.nunique() < 2:
        return {
            "status": "NO_SCORE",
            "reason": "single_training_class",
            "arm": spec["name"],
            "train_rows": int(len(train)),
            "serve_rows": int(len(serve)),
            "purged_session": purged_session,
        }, pd.DataFrame()

    model = _pipeline(
        features,
        categorical,
        model_kind=str(spec["model_kind"]),
        binary=not multistate,
    )
    model.fit(train[features], target_values)
    probabilities = model.predict_proba(serve[features])
    classes = [str(value) for value in model.named_steps["model"].classes_]
    if multistate:
        class_payoff = train.groupby(outcome)[net].mean().to_dict()
        rank_score = np.zeros(len(serve), dtype=float)
        for index, state in enumerate(classes):
            rank_score += probabilities[:, index] * float(class_payoff.get(state, 0.0))
        target_probability = (
            probabilities[:, classes.index("TARGET_FIRST")]
            if "TARGET_FIRST" in classes
            else np.zeros(len(serve), dtype=float)
        )
    else:
        positive_index = classes.index("1")
        target_probability = probabilities[:, positive_index]
        rank_score = target_probability

    scored = serve[
        ["candidate_key", "session_date", "market", "ticker", "known_at", "candidate_price"]
    ].copy()
    scored["arm"] = spec["name"]
    scored["target_probability"] = target_probability
    scored["rank_score"] = rank_score
    scored = scored.sort_values(
        ["session_date", "rank_score", "ticker"],
        ascending=[True, False, True],
    )
    scored["arm_rank"] = scored.groupby("session_date").cumcount() + 1
    scored["arm_selected"] = scored["arm_rank"] <= 3
    return {
        "status": "SCORED",
        "arm": spec["name"],
        "model_kind": spec["model_kind"],
        "train_rows": int(len(train)),
        "train_dates": int(train["session_date"].nunique()),
        "serve_rows": int(len(serve)),
        "purged_session": purged_session,
        "requested_features": requested_features,
        "features": features,
        "dropped_unavailable_features": dropped_features,
    }, scored


def _append_unique_jsonl(path: Path, records: list[dict[str, Any]]) -> int:
    existing: set[str] = set()
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                event_id = str(row.get("event_id") or "")
                if event_id:
                    existing.add(event_id)
        except OSError:
            pass
    new_rows = [row for row in records if str(row.get("event_id") or "") not in existing]
    if not new_rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in new_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(new_rows)


def run_shadow(
    *,
    session_date: str,
    markets: list[str],
    input_path: Path = DEFAULT_INPUT,
    audit_db: Path = DEFAULT_AUDIT_DB,
    price_root: Path = DEFAULT_PRICE_ROOT,
    screener_root: Path = DEFAULT_SCREENER_ROOT,
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    historical = pd.read_csv(input_path, low_memory=False)
    current = _current_candidates(
        audit_db=audit_db,
        session_date=session_date,
        markets=markets,
    )
    if current.empty:
        return {
            "status": "NO_CURRENT_CANDIDATES",
            "session_date": session_date,
            "authority": AUTHORITY,
            "written": 0,
        }

    historical["__cohort"] = "historical"
    current["__cohort"] = "current"
    combined = pd.concat([historical, current], ignore_index=True, sort=False)
    enriched, _coverage = add_daily_features(combined, price_root)
    rich = load_kr_rich_features(screener_root)
    if not rich.empty:
        enriched = enriched.merge(rich, on=["session_date", "ticker"], how="left")
    historical_enriched = enriched[enriched["__cohort"] == "historical"].copy()
    current_enriched = enriched[enriched["__cohort"] == "current"].copy()
    specs = _arm_specs(enriched)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records: list[dict[str, Any]] = []
    arm_summaries: dict[str, Any] = {}
    for market in markets:
        market_key = str(market).upper()
        scored_by_side: dict[str, pd.DataFrame] = {}
        for side in ("left", "right"):
            summary, scored = _score_arm(
                historical=historical_enriched,
                current=current_enriched,
                market=market_key,
                session_date=session_date,
                spec=specs[market_key][side],
            )
            arm_summaries[summary["arm"]] = summary
            scored_by_side[side] = scored
        left = scored_by_side["left"]
        right = scored_by_side["right"]
        if left.empty or right.empty:
            continue
        left_map = {
            str(row.ticker): row
            for row in left.itertuples(index=False)
        }
        right_map = {
            str(row.ticker): row
            for row in right.itertuples(index=False)
        }
        left_name = specs[market_key]["left"]["name"]
        right_name = specs[market_key]["right"]["name"]
        left_features = list(arm_summaries.get(left_name, {}).get("features") or [])
        right_features = list(arm_summaries.get(right_name, {}).get("features") or [])
        feature_overlap = sorted(set(left_features) & set(right_features))
        contract = {
            "schema_version": "candidate_consensus_shadow_v1",
            "market": market_key,
            "left_arm": left_name,
            "right_arm": right_name,
            "left_features": left_features,
            "right_features": right_features,
            "shared_features": feature_overlap,
            "feature_sets_disjoint": not feature_overlap,
            "selection": "top3_intersection_else_abstain",
            "authority": AUTHORITY,
        }
        contract_hash = hashlib.sha256(
            json.dumps(contract, sort_keys=True).encode("utf-8")
        ).hexdigest()
        for ticker in sorted(set(left_map) | set(right_map)):
            left_row = left_map.get(ticker)
            right_row = right_map.get(ticker)
            candidate_key = str(
                getattr(left_row, "candidate_key", "")
                or getattr(right_row, "candidate_key", "")
            )
            decision, abstain_reason = _consensus_decision(
                left_selected=bool(left_row is not None and left_row.arm_selected),
                right_selected=bool(right_row is not None and right_row.arm_selected),
                shared_features=feature_overlap,
            )
            selected = decision == "SELECT_SHADOW"
            event_seed = "|".join(
                [session_date, market_key, ticker, candidate_key, contract_hash]
            )
            records.append(
                {
                    "event_id": "cshadow_"
                    + hashlib.sha256(event_seed.encode("utf-8")).hexdigest()[:28],
                    "generated_at": generated_at,
                    "session_date": session_date,
                    "market": market_key,
                    "ticker": ticker,
                    "candidate_key": candidate_key,
                    "decision": decision,
                    "abstain_reason": abstain_reason,
                    "left": (
                        {
                            "arm": left_row.arm,
                            "rank": int(left_row.arm_rank),
                            "selected": bool(left_row.arm_selected),
                            "target_probability": _float(left_row.target_probability),
                            "rank_score": _float(left_row.rank_score),
                        }
                        if left_row is not None
                        else None
                    ),
                    "right": (
                        {
                            "arm": right_row.arm,
                            "rank": int(right_row.arm_rank),
                            "selected": bool(right_row.arm_selected),
                            "target_probability": _float(right_row.target_probability),
                            "rank_score": _float(right_row.rank_score),
                        }
                        if right_row is not None
                        else None
                    ),
                    "contract_hash": contract_hash,
                    "contract": contract,
                    "authority": AUTHORITY,
                }
            )
    target = ledger_path or get_runtime_path(
        "logs",
        "shadow",
        f"candidate_consensus_shadow_{session_date.replace('-', '')}.jsonl",
    )
    written = _append_unique_jsonl(target, records)
    return {
        "status": "OK",
        "session_date": session_date,
        "markets": markets,
        "authority": AUTHORITY,
        "candidate_records": len(records),
        "selected_records": sum(row["decision"] == "SELECT_SHADOW" for row in records),
        "written": written,
        "ledger": str(target),
        "arms": arm_summaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prospective disjoint-consensus shadow scorer")
    parser.add_argument("--session-date", default=date.today().isoformat())
    parser.add_argument("--market", choices=["US", "KR", "ALL"], default="ALL")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB)
    parser.add_argument("--price-root", type=Path, default=DEFAULT_PRICE_ROOT)
    parser.add_argument("--screener-root", type=Path, default=DEFAULT_SCREENER_ROOT)
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args()
    markets = ["US", "KR"] if args.market == "ALL" else [args.market]
    result = run_shadow(
        session_date=args.session_date,
        markets=markets,
        input_path=args.input,
        audit_db=args.audit_db,
        price_root=args.price_root,
        screener_root=args.screener_root,
        ledger_path=args.ledger,
    )
    write_candidate_consensus_status(
        {
            **result,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        kind="shadow",
        markets=markets,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
