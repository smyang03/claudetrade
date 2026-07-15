#!/usr/bin/env python3
from __future__ import annotations

"""Materialize index-trend strategy targets into a shadow-only monthly ledger."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.index_trend_strategy_lab import specs, target_weights


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_signal_payload(
    panel: pd.DataFrame,
    research_report: dict[str, Any],
    *,
    as_of: str,
    report_sha256: str,
    price_sha256: str,
) -> dict[str, Any]:
    latest_month = pd.Timestamp(panel.index.max()).to_period("M")
    effective_month = latest_month + 1
    results = research_report.get("results") if isinstance(research_report.get("results"), dict) else {}
    arms: list[dict[str, Any]] = []
    for spec in specs():
        if spec.method == "buy_hold":
            continue
        verdict = str((results.get(spec.name) or {}).get("verdict") or "")
        if not verdict.startswith("SHADOW_READY"):
            continue
        weights = target_weights(panel, spec).iloc[-1]
        clean_weights = {asset: round(float(value), 8) for asset, value in weights.items() if float(value) > 0}
        invested = min(1.0, sum(clean_weights.values()))
        arms.append(
            {
                "strategy": spec.name,
                "market": spec.market,
                "method": spec.method,
                "weights": clean_weights,
                "cash_weight": round(1.0 - invested, 8),
                "research_verdict": verdict,
                "thesis": spec.thesis,
            }
        )
    return {
        "schema_version": "index_trend_shadow_signal_v1",
        "authority": "SHADOW_ONLY_NO_ORDER_OR_LIVE_CONFIG_EFFECT",
        "as_of": as_of,
        "signal_month": str(latest_month),
        "effective_month": str(effective_month),
        "research_report_sha256": report_sha256,
        "price_cache_sha256": price_sha256,
        "arms": arms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Write monthly index-trend shadow targets")
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--research-report", required=True)
    parser.add_argument("--price-cache", required=True)
    parser.add_argument("--output-dir", default=str(ROOT / "data" / "shadow"))
    args = parser.parse_args()
    report_path = Path(args.research_report)
    price_path = Path(args.price_cache)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    panel = pd.read_csv(price_path, index_col=0, parse_dates=True).sort_index()
    payload = build_signal_payload(
        panel,
        report,
        as_of=args.as_of,
        report_sha256=_sha256(report_path),
        price_sha256=_sha256(price_path),
    )
    key = str(payload["effective_month"]).replace("-", "")
    snapshot = output_dir / f"index_trend_shadow_signal_{key}.json"
    snapshot.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    ledger = output_dir / "index_trend_shadow_signals.jsonl"
    existing_keys: set[str] = set()
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                existing_keys.add(str(item.get("effective_month") or ""))
            except json.JSONDecodeError:
                continue
    if str(payload["effective_month"]) not in existing_keys:
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    print(json.dumps({"snapshot": str(snapshot), "ledger": str(ledger), **payload}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
