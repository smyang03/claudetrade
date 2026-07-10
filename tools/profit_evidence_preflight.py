from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.profit_evidence_gate import (  # noqa: E402
    evaluate_profit_evidence,
    load_profit_evidence_snapshot,
    resolve_profit_evidence_mode,
    select_profit_evidence,
)
from runtime.profit_path_predictor import _artifact_path, _enabled, _load_artifact  # noqa: E402


def _load_start_env(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    values = payload.get("env_overrides") if isinstance(payload, dict) else {}
    return {str(key): str(value) for key, value in dict(values or {}).items()}


def _ticker_rows(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    for key in ("profit_evidence_by_ticker", "evidence_by_ticker", "predictions_by_ticker"):
        rows = snapshot.get(key)
        if isinstance(rows, dict):
            return {str(ticker): dict(value) for ticker, value in rows.items() if isinstance(value, dict)}
    return {}


def run_preflight(markets: list[str], strategies: list[str]) -> tuple[dict[str, Any], bool]:
    report: dict[str, Any] = {"ok": True, "markets": {}}
    blocking_failure = False
    for market in markets:
        market_key = str(market or "").upper()
        snapshot = load_profit_evidence_snapshot(market_key)
        rows = _ticker_rows(snapshot)
        market_report: dict[str, Any] = {
            "snapshot_present": bool(snapshot),
            "snapshot_ticker_count": len(rows),
            "paths": {},
        }
        shadow_enabled = _enabled(market_key)
        artifact, artifact_path = _load_artifact(market_key) if shadow_enabled else ({}, str(_artifact_path(market_key)))
        metadata = dict(artifact.get("metadata") or {})
        artifact_ready = bool(
            artifact
            and metadata.get("model_version")
            and str(metadata.get("model_state") or "").upper() == "SHADOW"
            and metadata.get("runtime_format") == "portable_linear_v1"
            and artifact.get("portable_model")
            and artifact.get("probability_calibrator")
            and artifact.get("return_calibrator")
        )
        market_report["path_shadow_model"] = {
            "enabled": shadow_enabled,
            "artifact_path": artifact_path,
            "ready": artifact_ready if shadow_enabled else True,
            "model_version": str(metadata.get("model_version") or ""),
            "model_state": str(metadata.get("model_state") or ""),
            "runtime_format": str(metadata.get("runtime_format") or ""),
            "trained_at": str(metadata.get("trained_at") or ""),
            "validation_auc": metadata.get("validation_auc"),
            "validation_selected_n": metadata.get("validation_selected_n"),
            "promotion_eligible_backtest": bool(metadata.get("promotion_eligible_backtest")),
        }
        if shadow_enabled and not artifact_ready:
            market_report["path_shadow_model"]["blocker"] = "shadow_model_artifact_missing_or_invalid"
            blocking_failure = True
        for strategy in strategies:
            mode, path = resolve_profit_evidence_mode(market_key, strategy)
            decisions = []
            for ticker in rows:
                evidence = select_profit_evidence(snapshot, market=market_key, ticker=ticker)
                decisions.append(
                    evaluate_profit_evidence(
                        market=market_key,
                        ticker=ticker,
                        strategy=strategy,
                        evidence=evidence,
                        evidence_source="snapshot",
                    )
                )
            passed = sum(1 for decision in decisions if decision.passed)
            would_block = sum(1 for decision in decisions if decision.would_block)
            reason_counts: dict[str, int] = {}
            for decision in decisions:
                for reason in decision.reasons:
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
            path_report = {
                "mode": mode,
                "path": path,
                "evaluated": len(decisions),
                "passed": passed,
                "would_block": would_block,
                "reason_counts": reason_counts,
            }
            if mode == "enforce" and passed <= 0:
                path_report["ready"] = False
                path_report["blocker"] = "no_passing_profit_evidence"
                blocking_failure = True
            else:
                path_report["ready"] = True
            market_report["paths"][path] = path_report
        report["markets"][market_key] = market_report
    report["ok"] = not blocking_failure
    return report, blocking_failure


def main() -> int:
    parser = argparse.ArgumentParser(description="Profit-evidence shadow/enforce snapshot preflight")
    parser.add_argument("--config", default=str(ROOT / "config" / "v2_start_config.json"))
    parser.add_argument("--markets", default="KR,US")
    parser.add_argument("--strategies", default="momentum,path_b")
    args = parser.parse_args()

    for key, value in _load_start_env(Path(args.config)).items():
        os.environ.setdefault(key, value)
    markets = [value.strip().upper() for value in str(args.markets).split(",") if value.strip()]
    strategies = [value.strip() for value in str(args.strategies).split(",") if value.strip()]
    report, blocked = run_preflight(markets, strategies)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
