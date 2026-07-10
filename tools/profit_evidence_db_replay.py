from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.profit_evidence_gate import evaluate_profit_evidence, select_profit_evidence  # noqa: E402


def _load_start_env(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    values = payload.get("env_overrides") if isinstance(payload, dict) else {}
    return {str(key): str(value) for key, value in dict(values or {}).items()}


@contextmanager
def _mode_override(mode: str) -> Iterator[None]:
    keys = [
        "PROFIT_EVIDENCE_GATE_MODE",
        "PROFIT_EVIDENCE_GATE_MODE_KR_PATH_A",
        "PROFIT_EVIDENCE_GATE_MODE_KR_PATH_B",
        "PROFIT_EVIDENCE_GATE_MODE_US_PATH_A",
        "PROFIT_EVIDENCE_GATE_MODE_US_PATH_B",
    ]
    before = {key: os.environ.get(key) for key in keys}
    os.environ["PROFIT_EVIDENCE_GATE_MODE"] = mode
    for key in keys[1:]:
        os.environ[key] = mode
    try:
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (ValueError, TypeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _parse_ts(value: Any) -> datetime:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _stats(values: list[float]) -> dict[str, Any]:
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return {"n": 0, "mean_pct": None, "median_pct": None, "positive_rate": None, "profit_factor_proxy": None}
    n = len(clean)
    median = clean[n // 2] if n % 2 else (clean[n // 2 - 1] + clean[n // 2]) / 2.0
    gains = sum(value for value in clean if value > 0)
    losses = abs(sum(value for value in clean if value < 0))
    return {
        "n": n,
        "mean_pct": round(sum(clean) / n, 6),
        "median_pct": round(median, 6),
        "positive_rate": round(sum(value > 0 for value in clean) / n, 6),
        "profit_factor_proxy": round(gains / losses, 6) if losses > 0 else None,
    }


def _candidate_rows(con: sqlite3.Connection, market: str = "") -> list[sqlite3.Row]:
    where_market = "AND r.market=?" if market else ""
    params: tuple[Any, ...] = (market,) if market else ()
    sql = f"""
    WITH prompt_rows AS (
      SELECT r.*,
             ROW_NUMBER() OVER (
               PARTITION BY r.market, r.session_date, r.ticker
               ORDER BY COALESCE(r.known_at, r.created_at), r.candidate_key
             ) AS rn
      FROM audit_candidate_rows r
      WHERE r.session_date IS NOT NULL
        AND (r.actual_prompt_included=1 OR r.final_prompt_included=1 OR r.in_prompt=1)
        {where_market}
    ), daily AS (
      SELECT candidate_key, return_pct, max_runup_pct, max_drawdown_pct
      FROM audit_candidate_outcomes
      WHERE horizon_min=1440 AND status='daily_forward'
    )
    SELECT p.market, p.session_date, p.known_at, p.ticker,
           COALESCE(p.recommended_strategy, p.strategy_used, p.primary_bucket, '') AS strategy,
           p.payload_json, d.return_pct, d.max_runup_pct, d.max_drawdown_pct
    FROM prompt_rows p
    JOIN daily d ON d.candidate_key=p.candidate_key
    WHERE p.rn=1
    ORDER BY p.session_date, p.market, p.ticker
    """
    return list(con.execute(sql, params).fetchall())


def _counterfactual_coverage(con: sqlite3.Connection) -> dict[str, Any]:
    rows = con.execute(
        """
        SELECT market, path_name, COUNT(*) AS n,
               SUM(entry_price IS NOT NULL) AS entry_n,
               SUM(outcome_60m_pct IS NOT NULL) AS outcome_60m_n,
               SUM(outcome_close_pct IS NOT NULL) AS outcome_close_n,
               SUM(max_runup_60m_pct IS NOT NULL AND max_drawdown_60m_pct IS NOT NULL) AS mfe_mae_n
        FROM candidate_counterfactual_paths
        GROUP BY market, path_name
        """
    ).fetchall()
    return _counterfactual_nested(rows)


def _counterfactual_nested(rows: list[sqlite3.Row]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for row in rows:
        output.setdefault(str(row[0]), {})[str(row[1])] = {
            "n": int(row[2] or 0),
            "entry_n": int(row[3] or 0),
            "outcome_60m_n": int(row[4] or 0),
            "outcome_close_n": int(row[5] or 0),
            "mfe_mae_60m_n": int(row[6] or 0),
        }
    return output


def replay(db_path: Path, *, mode: str, market: str = "") -> dict[str, Any]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = _candidate_rows(con, market=market)
        counterfactual = _counterfactual_coverage(con)
    finally:
        con.close()

    by_market: dict[str, dict[str, Any]] = {}
    with _mode_override(mode):
        for row in rows:
            market_key = str(row["market"] or "").upper()
            ticker = str(row["ticker"] or "")
            strategy = str(row["strategy"] or "")
            payload = _safe_json(row["payload_json"])
            evidence = select_profit_evidence(payload, market=market_key, ticker=ticker)
            decision = evaluate_profit_evidence(
                market=market_key,
                ticker=ticker,
                strategy=strategy,
                evidence=evidence,
                evidence_source="historical_payload" if evidence else "historical_missing",
                now=_parse_ts(row["known_at"]),
            )
            bucket = by_market.setdefault(
                market_key,
                {
                    "rows": 0,
                    "evidence_present": 0,
                    "allowed_returns": [],
                    "all_returns": [],
                    "would_block": 0,
                    "reason_counts": Counter(),
                    "path_counts": Counter(),
                },
            )
            value = float(row["return_pct"])
            bucket["rows"] += 1
            bucket["evidence_present"] += int(bool(evidence))
            bucket["all_returns"].append(value)
            bucket["would_block"] += int(decision.would_block)
            bucket["path_counts"][decision.path] += 1
            for reason in decision.reasons:
                bucket["reason_counts"][reason] += 1
            if decision.allowed:
                bucket["allowed_returns"].append(value)

    markets: dict[str, Any] = {}
    for market_key, bucket in by_market.items():
        markets[market_key] = {
            "eligible_rows": bucket["rows"],
            "historical_profit_evidence_rows": bucket["evidence_present"],
            "would_block_rows": bucket["would_block"],
            "path_counts": dict(bucket["path_counts"]),
            "reason_counts": dict(bucket["reason_counts"]),
            "baseline_forward_1d": _stats(bucket["all_returns"]),
            "gate_allowed_forward_1d": _stats(bucket["allowed_returns"]),
        }
    return {
        "mode": mode,
        "db_path": str(db_path),
        "markets": markets,
        "counterfactual_path_coverage": counterfactual,
        "interpretation": {
            "exact_contract_replay": True,
            "exact_historical_prediction_replay": all(
                payload["historical_profit_evidence_rows"] == payload["eligible_rows"]
                for payload in markets.values()
            ),
            "fill_and_exit_exact": False,
            "notes": [
                "daily_forward is a candidate-level close-return proxy, not broker-realized net PnL",
                "historical rows without stored profit_evidence must ABSTAIN in enforce mode",
                "counterfactual paths provide entry/60m/close/MFE/MAE coverage but not universal barrier hit ordering",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only historical replay for the profit-evidence gate")
    parser.add_argument("--db", default=str(ROOT / "data" / "audit" / "candidate_audit.db"))
    parser.add_argument("--config", default=str(ROOT / "config" / "v2_start_config.json"))
    parser.add_argument("--mode", choices=("off", "shadow", "enforce"), default="shadow")
    parser.add_argument("--market", choices=("", "KR", "US"), default="")
    args = parser.parse_args()
    for key, value in _load_start_env(Path(args.config)).items():
        os.environ.setdefault(key, value)
    report = replay(Path(args.db), mode=args.mode, market=args.market)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
