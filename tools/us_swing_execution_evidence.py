from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("execution counterfactual must be an object")
    return payload


def _portable_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _passed(metrics: dict[str, Any], *, lcb_floor: float, require_regime_sign: bool) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if int(metrics.get("sessions") or 0) < 200:
        reasons.append("sessions_insufficient")
    if float(metrics.get("mean_net_pct") or -999) < 0.25:
        reasons.append("mean_below_hurdle")
    if float(metrics.get("profit_factor") or 0) < 1.20:
        reasons.append("profit_factor_below_hurdle")
    if float(metrics.get("block_lcb_pct") or -999) <= float(lcb_floor):
        reasons.append("block_lcb_failed")
    if float(metrics.get("ex_top3_days_pct") or -999) <= 0:
        reasons.append("concentration_failed")
    if require_regime_sign:
        for year, year_metrics in dict(metrics.get("by_year") or {}).items():
            if float(year_metrics.get("mean_net_pct") or -999) <= 0:
                reasons.append(f"year_{year}_mean_not_positive")
            if float(year_metrics.get("profit_factor") or 0) < 1.0:
                reasons.append(f"year_{year}_profit_factor_below_one")
    return not reasons, reasons


def _capacity_passed(report: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    reasons: list[str] = []
    verdict = dict(report.get("policy_verdict") or {})
    rank1 = dict((report.get("results") or {}).get("rank1_skip") or {})
    required = ("0", "0.25", "0.5")
    scenarios: dict[str, Any] = {}
    if verdict.get("selected_policy") != "rank1_skip":
        reasons.append("capacity_policy_not_rank1")
    for key in required:
        metrics = dict(rank1.get(key) or {})
        scenarios[key] = metrics
        if int(metrics.get("trades") or 0) < 40:
            reasons.append(f"capacity_{key}_trades_insufficient")
        if float(metrics.get("net_pnl_krw") or -999) <= 0:
            reasons.append(f"capacity_{key}_pnl_not_positive")
        if float(metrics.get("profit_factor") or 0) < 1.0:
            reasons.append(f"capacity_{key}_profit_factor_below_one")
        year_2025 = dict((metrics.get("by_year") or {}).get("2025") or {})
        if float(year_2025.get("mean_net_pct") or -999) <= 0:
            reasons.append(f"capacity_{key}_2025_mean_not_positive")
        if float(year_2025.get("profit_factor") or 0) < 1.0:
            reasons.append(f"capacity_{key}_2025_profit_factor_below_one")
    return not reasons, reasons, {
        "selected_policy": verdict.get("selected_policy"),
        "validated_entry_slippage_pct": [0.0, 0.25, 0.5],
        "stress_boundary_entry_slippage_pct": 1.0,
        "scenarios": scenarios,
    }


def build_evidence(report_path: Path, capacity_path: Path | None = None) -> dict[str, Any]:
    report = _load(report_path)
    subsets = report.get("subsets") if isinstance(report.get("subsets"), dict) else {}
    config = "tp12_sl25"
    rank1 = dict((subsets.get("rank1") or {}).get(config) or {})
    top3 = dict((subsets.get("top3") or {}).get(config) or {})
    micro_passed, micro_reasons = _passed(rank1, lcb_floor=-0.25, require_regime_sign=True)
    probe_passed, probe_reasons = _passed(top3, lcb_floor=0.0, require_regime_sign=True)
    capacity_metrics: dict[str, Any] = {}
    capacity_source: dict[str, Any] = {}
    if capacity_path is not None:
        capacity_report = _load(capacity_path)
        capacity_passed, capacity_reasons, capacity_metrics = _capacity_passed(capacity_report)
        micro_passed = bool(micro_passed and capacity_passed)
        micro_reasons.extend(capacity_reasons)
        capacity_source = {
            "capacity_report": _portable_path(capacity_path),
            "capacity_report_sha256": hashlib.sha256(capacity_path.read_bytes()).hexdigest(),
        }
    return {
        "schema_version": "us_swing_execution_evidence_v1",
        "sealed": True,
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "source_report": _portable_path(report_path),
        "source_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        **capacity_source,
        "contract": {
            "take_profit_pct": 0.12,
            "catastrophe_stop_pct": 0.25,
            "max_hold_sessions": 5,
            "entry_proxy": "session_open",
            "cost_pct": 0.5,
            "max_entry_slippage_pct": 0.5,
        },
        "modes": {
            "micro": {
                "passed": micro_passed,
                "selection": "rank1",
                "metrics": rank1,
                "capacity_metrics": capacity_metrics,
                "reasons": micro_reasons,
            },
            "probe": {
                "passed": probe_passed,
                "selection": "top3",
                "metrics": top3,
                "reasons": probe_reasons,
            },
            "standard": {
                "passed": False,
                "selection": "top5_not_materialized",
                "metrics": {},
                "reasons": ["standard_execution_contract_not_validated"],
            },
        },
        "authority": "EVIDENCE_ONLY_NO_AUTO_PROMOTION",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seal US swing live-execution contract evidence")
    parser.add_argument("--report", default=str(ROOT / "reports" / "us_swing_exit_counterfactual_20260711.json"))
    parser.add_argument("--capacity-report", default=str(ROOT / "reports" / "us_swing_capacity_counterfactual_20260711.json"))
    parser.add_argument("--output", default=str(ROOT / "state" / "us_swing_execution_evidence.json"))
    args = parser.parse_args()
    evidence = build_evidence(Path(args.report), Path(args.capacity_report))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output), **evidence}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
