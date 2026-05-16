from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.candidate_counterfactual_store import CandidateCounterfactualStore
from runtime_paths import get_runtime_path


def _float(value: Any) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else 0.0
    except Exception:
        return 0.0


def _metrics(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [_float(row.get(field)) for row in rows if row.get(field) is not None]
    gains = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    pf = None
    if losses:
        pf = round(sum(gains) / abs(sum(losses)), 6) if gains else 0.0
    elif gains:
        pf = "INF"
    return {
        "n": len(values),
        "wins": len(gains),
        "losses": len(losses),
        "win_rate_pct": round((len(gains) / len(values)) * 100.0, 4) if values else 0.0,
        "avg_pct": round(sum(values) / len(values), 6) if values else 0.0,
        "profit_factor": pf,
    }


def analyze_counterfactual_paths(*, db_path: str | Path | None = None, session_date: str = "", market: str = "") -> dict[str, Any]:
    store = CandidateCounterfactualStore(db_path or get_runtime_path("data", "audit", "candidate_audit.db"))
    rows = store.fetch_rows(session_date=session_date, market=market)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    candidate_paths: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    trigger_eval = 0
    pending = 0
    for row in rows:
        key = f"{row.get('market')}|{row.get('path_name')}"
        groups[key].append(row)
        candidate_paths[
            (
                str(row.get("runtime_mode") or ""),
                str(row.get("session_date") or ""),
                str(row.get("market") or ""),
                str(row.get("candidate_key") or row.get("ticker") or ""),
            )
        ].add(str(row.get("path_name") or ""))
        if str(row.get("status") or "") == "PENDING":
            pending += 1
        else:
            trigger_eval += 1
    by_path = {
        key: {
            "ret30": _metrics(items, "outcome_30m_pct"),
            "ret60": _metrics(items, "outcome_60m_pct"),
            "rows": len(items),
            "data_missing": sum(1 for row in items if str(row.get("status") or "") == "DATA_MISSING"),
        }
        for key, items in sorted(groups.items())
    }
    candidates = len(candidate_paths)
    rich = sum(1 for paths in candidate_paths.values() if len(paths) >= 3)
    total_eval = trigger_eval + pending
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "filters": {"session_date": session_date, "market": market},
        "row_count": len(rows),
        "candidate_count": candidates,
        "candidates_with_3plus_paths_filled_pct": round((rich / candidates) * 100.0, 4) if candidates else 0.0,
        "trigger_eval_rate_pct": round((trigger_eval / total_eval) * 100.0, 4) if total_eval else 0.0,
        "by_path": by_path,
    }


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Counterfactual Path Review",
        "",
        f"Generated: {payload['generated_at']}",
        f"Rows: {payload['row_count']}",
        f"Candidates: {payload['candidate_count']}",
        f"3+ path candidates: {payload['candidates_with_3plus_paths_filled_pct']}%",
        f"Trigger eval rate: {payload['trigger_eval_rate_pct']}%",
        "",
        "| Path | Rows | Missing | 30m N | 30m Avg | 30m PF | 60m N | 60m Avg | 60m PF |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for path, item in payload.get("by_path", {}).items():
        r30 = item.get("ret30") or {}
        r60 = item.get("ret60") or {}
        lines.append(
            f"| {path} | {item.get('rows', 0)} | {item.get('data_missing', 0)} | "
            f"{r30.get('n', 0)} | {r30.get('avg_pct', 0)} | {r30.get('profit_factor')} | "
            f"{r60.get('n', 0)} | {r60.get('avg_pct', 0)} | {r60.get('profit_factor')} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze candidate counterfactual path outcomes.")
    parser.add_argument("--db-path", default=str(get_runtime_path("data", "audit", "candidate_audit.db")))
    parser.add_argument("--date", default="")
    parser.add_argument("--market", default="")
    parser.add_argument("--stamp", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--output-dir", default=str(ROOT / "docs" / "reports"))
    args = parser.parse_args(argv)
    payload = analyze_counterfactual_paths(db_path=args.db_path, session_date=args.date, market=args.market)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"counterfactual_path_review_{args.stamp}.json"
    md_path = out / f"counterfactual_path_review_{args.stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(payload), encoding="utf-8")
    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
