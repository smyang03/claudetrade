from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.candidate_prompt_pool import build_trainer_prompt_pool


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", ""))
    except Exception:
        return default


def _rows(db_path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    r.*,
                    o30.return_pct AS ret30,
                    o30.max_runup_pct AS mfe30,
                    o30.max_drawdown_pct AS mae30,
                    o60.return_pct AS ret60,
                    o60.max_runup_pct AS mfe60,
                    o60.max_drawdown_pct AS mae60
                FROM audit_candidate_rows r
                LEFT JOIN audit_candidate_outcomes o30
                    ON o30.candidate_key=r.candidate_key AND o30.horizon_min=30
                LEFT JOIN audit_candidate_outcomes o60
                    ON o60.candidate_key=r.candidate_key AND o60.horizon_min=60
                WHERE r.runtime_mode='live'
                ORDER BY r.session_date, r.market, r.call_id, r.prompt_rank, r.ticker
                """
            )
        ]
    finally:
        conn.close()


def _good(row: dict[str, Any]) -> bool | None:
    if row.get("ret60") is None:
        return None
    return _float(row.get("ret60")) >= 0.5 and _float(row.get("mae60")) > -2.0 and _float(row.get("mfe60")) >= 1.0


def _bad(row: dict[str, Any]) -> bool | None:
    if row.get("ret60") is None and row.get("ret30") is None:
        return None
    return (
        _float(row.get("ret30")) <= -1.0
        or _float(row.get("mae60")) <= -3.0
        or (_float(row.get("mfe60")) < 0.5 and _float(row.get("ret60")) < 0.0)
    )


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ret60 = [_float(row.get("ret60")) for row in rows if row.get("ret60") is not None]
    ret30 = [_float(row.get("ret30")) for row in rows if row.get("ret30") is not None]
    good = [_good(row) for row in rows if _good(row) is not None]
    bad = [_bad(row) for row in rows if _bad(row) is not None]
    pos = sum(value for value in ret60 if value > 0)
    neg = -sum(value for value in ret60 if value < 0)
    return {
        "n": len(rows),
        "ret60_n": len(ret60),
        "ret60_avg": round(sum(ret60) / len(ret60), 4) if ret60 else None,
        "ret60_median": round(median(ret60), 4) if ret60 else None,
        "ret30_avg": round(sum(ret30) / len(ret30), 4) if ret30 else None,
        "good_rate_pct": round(100.0 * sum(1 for value in good if value) / len(good), 2) if good else None,
        "bad_rate_pct": round(100.0 * sum(1 for value in bad if value) / len(bad), 2) if bad else None,
        "pf60": round(pos / neg, 4) if neg > 0 else (999.0 if pos > 0 else None),
    }


def _market(row: dict[str, Any]) -> str:
    return "US" if str(row.get("market") or "").upper() == "US" else "KR"


def _with_outcomes(scored: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    out = dict(scored)
    for key in ("ret30", "mfe30", "mae30", "ret60", "mfe60", "mae60"):
        out[key] = source.get(key)
    return out


def simulate(rows: list[dict[str, Any]], *, caps: list[int]) -> dict[str, Any]:
    by_call: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_call[str(row.get("call_id") or "")].append(row)

    scenarios: dict[str, dict[str, Any]] = {}
    examples = {"promoted": [], "omitted": []}
    for cap in caps:
        current: list[dict[str, Any]] = []
        proposed: list[dict[str, Any]] = []
        proposed_plan_a: list[dict[str, Any]] = []
        for call_id, group in by_call.items():
            if len(group) < 5:
                continue
            market = _market(group[0])
            current_rows = sorted(
                [row for row in group if int(row.get("in_prompt") or 0) > 0],
                key=lambda row: (row.get("prompt_rank") or 999999, row.get("ticker") or ""),
            )[:cap]
            pool = build_trainer_prompt_pool(
                [dict(row) for row in group],
                market=market,
                target=min(30, cap),
                hard_cap=cap,
                reorder_enabled=True,
            )
            source_by_ticker = {str(row.get("ticker") or ""): row for row in group}
            proposed_rows = [
                _with_outcomes(row, source_by_ticker.get(str(row.get("ticker") or ""), {}))
                for row in pool.get("prompt_pool", [])
            ]
            current.extend(current_rows)
            proposed.extend(proposed_rows)
            proposed_plan_a.extend(
                [
                    row
                    for row in proposed_rows
                    if str(row.get("trainer_candidate_state") or "").upper() == "PLAN_A"
                ]
            )
            if cap == max(caps):
                current_keys = {str(row.get("ticker") or "") for row in current_rows}
                proposed_keys = {str(row.get("ticker") or "") for row in proposed_rows}
                for row in proposed_rows:
                    if str(row.get("ticker") or "") not in current_keys and len(examples["promoted"]) < 20:
                        examples["promoted"].append(
                            {
                                "call_id": call_id,
                                "market": market,
                                "ticker": row.get("ticker"),
                                "trainer_prompt_score": row.get("trainer_prompt_score"),
                                "trainer_candidate_state": row.get("trainer_candidate_state"),
                                "ret60": row.get("ret60"),
                            }
                        )
                for row in current_rows:
                    if str(row.get("ticker") or "") not in proposed_keys and len(examples["omitted"]) < 20:
                        examples["omitted"].append(
                            {
                                "call_id": call_id,
                                "market": market,
                                "ticker": row.get("ticker"),
                                "prompt_rank": row.get("prompt_rank"),
                                "ret60": row.get("ret60"),
                            }
                        )

        scenarios[f"current_prompt_cap{cap}"] = _metrics(current)
        scenarios[f"trainer_prompt_cap{cap}"] = _metrics(proposed)
        scenarios[f"trainer_plan_a_shadow_cap{cap}"] = _metrics(proposed_plan_a)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "basis": {
            "rows": len(rows),
            "date_min": min((str(row.get("session_date") or "") for row in rows), default=""),
            "date_max": max((str(row.get("session_date") or "") for row in rows), default=""),
            "caps": caps,
            "label_policy": "forward/outcome labels are used only after scoring for evaluation",
        },
        "scenarios": scenarios,
        "examples": examples,
    }


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Candidate Quality Ranker Simulation",
        "",
        f"- generated_at: {payload['generated_at']}",
        "- scope: local DB only; no broker/API/Claude calls",
        f"- label_policy: {payload['basis']['label_policy']}",
        "",
        "## Basis",
        "",
        f"- rows: {payload['basis']['rows']}",
        f"- date_min: {payload['basis']['date_min']}",
        f"- date_max: {payload['basis']['date_max']}",
        f"- caps: {payload['basis']['caps']}",
        "",
        "## Scenario Metrics",
        "",
        "| scenario | n | ret60_n | ret60_avg | ret30_avg | good_rate | bad_rate | pf60 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, metrics in payload["scenarios"].items():
        lines.append(
            "| {name} | {n} | {ret60_n} | {ret60_avg} | {ret30_avg} | {good_rate_pct} | {bad_rate_pct} | {pf60} |".format(
                name=name,
                **metrics,
            )
        )
    lines.extend(["", "## Promoted Examples", ""])
    lines.append("| market | ticker | score | state | ret60 |")
    lines.append("|---|---|---:|---|---:|")
    for row in payload["examples"]["promoted"][:10]:
        lines.append(
            f"| {row.get('market')} | {row.get('ticker')} | {row.get('trainer_prompt_score')} | {row.get('trainer_candidate_state')} | {row.get('ret60')} |"
        )
    lines.extend(["", "## Omitted Examples", ""])
    lines.append("| market | ticker | old_rank | ret60 |")
    lines.append("|---|---|---:|---:|")
    for row in payload["examples"]["omitted"][:10]:
        lines.append(f"| {row.get('market')} | {row.get('ticker')} | {row.get('prompt_rank')} | {row.get('ret60')} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate trainer prompt ranker on candidate audit DB.")
    parser.add_argument("--db", default=str(ROOT / "data" / "audit" / "candidate_audit.db"))
    parser.add_argument("--output-dir", default=str(ROOT / "docs" / "reports"))
    parser.add_argument("--stamp", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    args = parser.parse_args()

    db_path = Path(args.db)
    rows = _rows(db_path)
    payload = simulate(rows, caps=[25, 30, 36])
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"candidate_quality_ranker_sim_{args.stamp}.json"
    md_path = output_dir / f"candidate_quality_ranker_sim_{args.stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
