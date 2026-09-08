from __future__ import annotations

"""Full candidate pipeline simulation matrix with canonical fill fallback.

Read-only harness:

- replays live evidence from audit_candidate_rows.post_open_features_json
- replays route_candidate_action from payload_json.runtime_gate
- builds KR/US stage survival matrix
- compares candidate-audit fill fields with canonical v2 fills so audit-link leaks
  are visible instead of being mistaken for "no trades"
"""

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.action_routing import route_candidate_action  # noqa: E402
from runtime.live_evidence_pack import build_live_evidence_pack  # noqa: E402


AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
DECISIONS_DB = ROOT / "data" / "ml" / "decisions.db"
ACTIONABLE = {"BUY_READY", "PROBE_READY", "ADD_READY", "PULLBACK_WAIT"}


def _json_loads(value: Any) -> Any:
    if value in (None, "", "{}", "[]", "null"):
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA busy_timeout=50000")
    con.row_factory = sqlite3.Row
    return con


def _ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


@dataclass(frozen=True)
class CanonicalFill:
    market: str
    session_date: str
    ticker: str
    filled_count: int
    closed_count: int
    first_fill_at: str
    route: str
    path_type: str
    strategy: str
    net_sum_krw: float | None
    net_avg_pct: float | None


def load_candidate_rows(*, since: str, market: str | None, limit: int | None) -> list[sqlite3.Row]:
    con = _connect(AUDIT_DB)
    try:
        q = """
            SELECT market, ticker, session_date, known_at,
                   in_prompt, prompt_excluded_reason,
                   claude_action, claude_trade_ready,
                   post_open_features_json, payload_json,
                   evidence_data_state, evidence_action_ceiling, evidence_missing_fields_json,
                   route_final_action, route_route, route_runtime_gate_reason, route_original_action,
                   no_submit_reason_code, filled_count, first_fill_at,
                   execution_link_source, execution_decision_id, execution_event_id,
                   entry_price, exit_price, exit_reason, pnl_pct, data_quality
            FROM audit_candidate_rows
            WHERE session_date >= ?
        """
        args: list[Any] = [since]
        if market:
            q += " AND market = ?"
            args.append(market)
        q += " ORDER BY session_date, market, ticker, known_at"
        if limit:
            q += f" LIMIT {int(limit)}"
        return con.execute(q, args).fetchall()
    finally:
        con.close()


def load_canonical_fills(*, since: str, market: str | None) -> dict[tuple[str, str, str], CanonicalFill]:
    con = _connect(DECISIONS_DB)
    try:
        q = """
            SELECT market, session_date, ticker,
                   SUM(CASE WHEN filled=1 THEN 1 ELSE 0 END) AS filled_count,
                   SUM(CASE WHEN closed=1 THEN 1 ELSE 0 END) AS closed_count,
                   MIN(CASE WHEN filled=1 THEN earliest_fill_at ELSE NULL END) AS first_fill_at,
                   GROUP_CONCAT(DISTINCT COALESCE(route,'')) AS routes,
                   GROUP_CONCAT(DISTINCT COALESCE(path_type,'')) AS path_types,
                   GROUP_CONCAT(DISTINCT COALESCE(strategy,'')) AS strategies,
                   SUM(CASE WHEN closed=1 THEN pnl_krw_net ELSE 0 END) AS net_sum_krw,
                   AVG(CASE WHEN closed=1 THEN pnl_pct_net ELSE NULL END) AS net_avg_pct
            FROM v2_canonical_performance
            WHERE runtime_mode='live' AND session_date >= ? AND (filled=1 OR closed=1)
        """
        args: list[Any] = [since]
        if market:
            q += " AND market = ?"
            args.append(market)
        q += " GROUP BY market, session_date, ticker"
        out: dict[tuple[str, str, str], CanonicalFill] = {}
        for row in con.execute(q, args):
            m = str(row["market"] or "")
            d = str(row["session_date"] or "")
            t = _ticker_key(m, row["ticker"])
            out[(m, d, t)] = CanonicalFill(
                market=m,
                session_date=d,
                ticker=t,
                filled_count=int(row["filled_count"] or 0),
                closed_count=int(row["closed_count"] or 0),
                first_fill_at=str(row["first_fill_at"] or ""),
                route=str(row["routes"] or ""),
                path_type=str(row["path_types"] or ""),
                strategy=str(row["strategies"] or ""),
                net_sum_krw=row["net_sum_krw"],
                net_avg_pct=row["net_avg_pct"],
            )
        return out
    finally:
        con.close()


def load_canonical_fills_by_decision(*, since: str, market: str | None) -> dict[str, CanonicalFill]:
    con = _connect(DECISIONS_DB)
    try:
        q = """
            SELECT v2_decision_id, market, session_date, ticker,
                   filled, closed, earliest_fill_at, route, path_type, strategy,
                   pnl_krw_net, pnl_pct_net
            FROM v2_canonical_performance
            WHERE runtime_mode='live' AND session_date >= ? AND (filled=1 OR closed=1)
        """
        args: list[Any] = [since]
        if market:
            q += " AND market = ?"
            args.append(market)
        out: dict[str, CanonicalFill] = {}
        for row in con.execute(q, args):
            decision_id = str(row["v2_decision_id"] or "").strip()
            if not decision_id:
                continue
            m = str(row["market"] or "")
            out[decision_id] = CanonicalFill(
                market=m,
                session_date=str(row["session_date"] or ""),
                ticker=_ticker_key(m, row["ticker"]),
                filled_count=1 if int(row["filled"] or 0) else 0,
                closed_count=1 if int(row["closed"] or 0) else 0,
                first_fill_at=str(row["earliest_fill_at"] or ""),
                route=str(row["route"] or ""),
                path_type=str(row["path_type"] or ""),
                strategy=str(row["strategy"] or ""),
                net_sum_krw=row["pnl_krw_net"],
                net_avg_pct=row["pnl_pct_net"],
            )
        return out
    finally:
        con.close()


def replay_evidence(row: sqlite3.Row, overrides: dict[str, Any] | None = None) -> dict[str, Any] | None:
    features = _json_loads(row["post_open_features_json"])
    if not isinstance(features, dict):
        return None
    if overrides:
        features = {**features, **overrides}
        if features.get("data_quality") == "minute_missing":
            features["data_quality"] = "minute_partial"
        features.pop("fail_closed", None)
    return build_live_evidence_pack(
        market=str(row["market"]),
        ticker=str(row["ticker"]),
        features=features,
        action={"action": row["claude_action"] or "WATCH"},
    )


def replay_route(row: sqlite3.Row, *, ceiling_override: str | None = None) -> dict[str, Any] | None:
    payload = _json_loads(row["payload_json"])
    if not isinstance(payload, dict):
        return None
    ctx = payload.get("runtime_gate")
    if not isinstance(ctx, dict) or not ctx:
        return None
    ctx = dict(ctx)
    if ceiling_override:
        ctx["evidence_action_ceiling"] = ceiling_override
        if ceiling_override == "BUY_READY":
            ctx["evidence_data_state"] = "confirmed"
    action = {
        "ticker": row["ticker"],
        "action": row["claude_action"] or "WATCH",
        "confidence": ctx.get("confidence") or payload.get("confidence") or 0.0,
        "current_price": ctx.get("current_price"),
        "max_entry_price": ctx.get("entry_price_cap") or 0.0,
    }
    try:
        dec = route_candidate_action(
            action,
            market=str(row["market"]),
            data_quality=str(ctx.get("data_quality") or "missing"),
            pathb_waiting=bool(ctx.get("pathb_waiting")),
            overextended=bool(ctx.get("overextended")),
            execution_context=ctx,
        )
    except Exception as exc:
        return {"error": type(exc).__name__}
    return {
        "final_action": dec.final_action,
        "route": dec.route or "",
        "reason": dec.reason,
        "runtime_gate_reason": dec.runtime_gate_reason,
    }


def fidelity(rows: list[sqlite3.Row]) -> dict[str, Any]:
    ev = Counter()
    rt = Counter()
    route_mismatches = Counter()
    for row in rows:
        if row["evidence_data_state"]:
            pack = replay_evidence(row)
            if pack is None:
                ev["unreplayable"] += 1
            else:
                ev["state_match" if pack["data_state"] == row["evidence_data_state"] else "state_mismatch"] += 1
                ev["ceiling_match" if pack["action_ceiling"] == row["evidence_action_ceiling"] else "ceiling_mismatch"] += 1
        if row["route_final_action"]:
            rep = replay_route(row)
            if rep is None or rep.get("error"):
                rt["unreplayable"] += 1
            else:
                actual_action = str(row["route_final_action"] or "")
                actual_route = str(row["route_route"] or "")
                rt["action_match" if rep["final_action"] == actual_action else "action_mismatch"] += 1
                rt["route_match" if rep["route"] == actual_route else "route_mismatch"] += 1
                if rep["final_action"] != actual_action:
                    route_mismatches[f"{actual_action}->{rep['final_action']}"] += 1
    return {
        "evidence": dict(ev),
        "route": dict(rt),
        "route_mismatch_top": dict(route_mismatches.most_common(12)),
    }


def _rate(num: int, den: int) -> float | None:
    return (num / den) if den else None


def stage_matrix(
    rows: list[sqlite3.Row],
    *,
    market: str,
    canonical_fills: dict[tuple[str, str, str], CanonicalFill],
    canonical_by_decision: dict[str, CanonicalFill],
) -> dict[str, Any]:
    sub = [row for row in rows if str(row["market"]) == market]
    stages = [
        "candidate",
        "prompt",
        "actionable",
        "evidence_pass",
        "route_pass",
        "entry_wiring",
        "safety_submit",
        "candidate_audit_fill",
        "canonical_fill_fallback",
        "canonical_closed_fallback",
    ]
    alive = {stage: 0 for stage in stages}
    sessions: dict[str, set[str]] = {stage: set() for stage in stages}
    blocked: dict[str, Counter] = {stage: Counter() for stage in stages}
    canonical_seen: set[tuple[str, str, str]] = set()
    canonical_closed_seen: set[tuple[str, str, str]] = set()
    canonical_decision_seen_any: set[str] = set()
    canonical_decision_seen_executable: set[str] = set()
    canonical_decision_seen_safety: set[str] = set()
    executable_routes = {"PlanA.buy", "PathB.wait", "PlanA.probe"}

    for row in sub:
        session_date = str(row["session_date"] or "")
        key = (market, session_date, _ticker_key(market, row["ticker"]))
        execution_decision_id = str(row["execution_decision_id"] or "").strip()
        if execution_decision_id in canonical_by_decision:
            canonical_decision_seen_any.add(execution_decision_id)
        alive["candidate"] += 1
        sessions["candidate"].add(session_date)

        if not row["in_prompt"]:
            blocked["prompt"][str(row["prompt_excluded_reason"] or "no_reason")] += 1
            continue
        alive["prompt"] += 1
        sessions["prompt"].add(session_date)

        action = str(row["claude_action"] or "").upper()
        if action not in ACTIONABLE:
            blocked["actionable"][action or "no_action"] += 1
            continue
        alive["actionable"] += 1
        sessions["actionable"].add(session_date)

        ceiling = str(row["evidence_action_ceiling"] or "").upper()
        if ceiling in {"WATCH", "WAIT_CONFIRMATION"}:
            blocked["evidence_pass"][f"ceiling={ceiling}/{row['evidence_data_state'] or ''}"] += 1
            continue
        if action == "BUY_READY" and ceiling == "PROBE_READY":
            blocked["evidence_pass"]["BUY_READY_demoted_to_PROBE_READY"] += 1
            continue
        alive["evidence_pass"] += 1
        sessions["evidence_pass"].add(session_date)

        route = str(row["route_route"] or "")
        if not route or route == "WATCH":
            blocked["route_pass"][f"{row['route_final_action'] or 'missing'}:{row['route_runtime_gate_reason'] or '-'}"] += 1
            continue
        alive["route_pass"] += 1
        sessions["route_pass"].add(session_date)

        if route not in executable_routes:
            blocked["entry_wiring"][f"route={route}"] += 1
            continue
        alive["entry_wiring"] += 1
        sessions["entry_wiring"].add(session_date)
        if execution_decision_id in canonical_by_decision:
            canonical_decision_seen_executable.add(execution_decision_id)

        if route == "PlanA.buy" and row["no_submit_reason_code"]:
            blocked["safety_submit"][str(row["no_submit_reason_code"])] += 1
            continue
        alive["safety_submit"] += 1
        sessions["safety_submit"].add(session_date)
        if execution_decision_id in canonical_by_decision:
            canonical_decision_seen_safety.add(execution_decision_id)

        if int(row["filled_count"] or 0) <= 0:
            blocked["candidate_audit_fill"]["candidate_audit_filled_count_zero"] += 1
        else:
            alive["candidate_audit_fill"] += 1
            sessions["candidate_audit_fill"].add(session_date)

        fill = canonical_fills.get(key)
        if fill and fill.filled_count > 0:
            canonical_seen.add(key)
            alive["canonical_fill_fallback"] += 1
            sessions["canonical_fill_fallback"].add(session_date)
            if fill.closed_count > 0:
                canonical_closed_seen.add(key)
                alive["canonical_closed_fallback"] += 1
                sessions["canonical_closed_fallback"].add(session_date)
        else:
            blocked["canonical_fill_fallback"]["no_canonical_fill_for_candidate_key"] += 1

    all_market_canonical = {
        key: fill for key, fill in canonical_fills.items() if key[0] == market
    }
    prompt_rows = alive["prompt"]
    return {
        "market": market,
        "rows": len(sub),
        "stage_counts": alive,
        "stage_sessions": {stage: len(value) for stage, value in sessions.items()},
        "stage_survival": {
            stage: _rate(alive[stage], alive[stages[index - 1]]) if index else 1.0
            for index, stage in enumerate(stages)
        },
        "blocked_top": {
            stage: dict(counter.most_common(10)) for stage, counter in blocked.items() if counter
        },
        "prompt_evidence_distribution": _prompt_evidence_distribution(sub),
        "canonical_truth": {
            "canonical_filled_unique": sum(1 for fill in all_market_canonical.values() if fill.filled_count > 0),
            "canonical_closed_unique": sum(1 for fill in all_market_canonical.values() if fill.closed_count > 0),
            "canonical_filled_seen_in_safety_candidates": len(canonical_seen),
            "canonical_closed_seen_in_safety_candidates": len(canonical_closed_seen),
            "canonical_filled_decision_ids": sum(
                1 for decision_id, fill in canonical_by_decision.items()
                if fill.market == market and fill.filled_count > 0
            ),
            "canonical_decision_seen_any_candidate_row": len(canonical_decision_seen_any),
            "canonical_decision_seen_executable_route_row": len(canonical_decision_seen_executable),
            "canonical_decision_seen_after_safety_stage": len(canonical_decision_seen_safety),
            "candidate_audit_fill_rows": alive["candidate_audit_fill"],
            "candidate_audit_execution_decision_rows": sum(
                1 for row in sub if str(row["execution_decision_id"] or "").strip()
            ),
            "candidate_audit_execution_event_rows": sum(
                1 for row in sub if str(row["execution_event_id"] or "").strip()
            ),
        },
        "action_mix_in_prompt": dict(Counter(str(row["claude_action"] or "no_action").upper() for row in sub if row["in_prompt"]).most_common(12)),
        "prompt_rows": prompt_rows,
    }


def _prompt_evidence_distribution(rows: list[sqlite3.Row]) -> dict[str, int]:
    counter = Counter()
    for row in rows:
        if row["in_prompt"]:
            counter[str(row["evidence_data_state"] or "not_recorded")] += 1
    return dict(counter.most_common())


def missing_field_counterfactual(rows: list[sqlite3.Row], *, market: str) -> dict[str, Any]:
    sub = [
        row for row in rows
        if str(row["market"]) == market
        and str(row["evidence_action_ceiling"] or "").upper() in {"PROBE_READY", "WATCH"}
        and row["post_open_features_json"]
    ]
    missing = Counter()
    for row in sub:
        fields = _json_loads(row["evidence_missing_fields_json"])
        if isinstance(fields, list):
            for field in fields:
                missing[str(field)] += 1

    scenarios = {
        "fill_volume_ratio_open": {"volume_ratio_open": 1.0},
        "fill_opening_range_break": {"opening_range_break": False},
        "fill_vwap_distance_pct": {"vwap_distance_pct": 0.0},
        "fill_three_confirmation_fields": {
            "volume_ratio_open": 1.0,
            "opening_range_break": False,
            "vwap_distance_pct": 0.0,
        },
    }
    recovered = {}
    for name, overrides in scenarios.items():
        count = 0
        for row in sub:
            pack = replay_evidence(row, overrides)
            if pack and pack.get("action_ceiling") == "BUY_READY":
                count += 1
        recovered[name] = count

    rvol_present = 0
    rvol_recovered = 0
    for row in sub:
        features = _json_loads(row["post_open_features_json"])
        if not isinstance(features, dict):
            continue
        value = features.get("time_normalized_rvol")
        if value is None:
            continue
        rvol_present += 1
        pack = replay_evidence(row, {"volume_ratio_open": value})
        if pack and pack.get("action_ceiling") == "BUY_READY":
            rvol_recovered += 1
    return {
        "market": market,
        "downgraded_rows": len(sub),
        "missing_fields_top": dict(missing.most_common(10)),
        "recovered_to_buy_ready": recovered,
        "time_normalized_rvol_present": rvol_present,
        "time_normalized_rvol_recovered": rvol_recovered,
    }


def route_counterfactual(rows: list[sqlite3.Row], *, market: str) -> dict[str, Any]:
    sub = [row for row in rows if str(row["market"]) == market and str(row["claude_action"] or "").upper() == "BUY_READY"]
    routes = Counter(str(row["route_route"] or "missing") for row in sub)
    flipped = 0
    tried = 0
    for row in sub:
        if str(row["route_route"] or "") == "PlanA.buy":
            continue
        rep = replay_route(row, ceiling_override="BUY_READY")
        if rep is None or rep.get("error"):
            continue
        tried += 1
        if rep.get("route") == "PlanA.buy":
            flipped += 1
    return {
        "market": market,
        "buy_ready_rows": len(sub),
        "route_distribution": dict(routes.most_common(12)),
        "candidate_audit_filled_rows": sum(1 for row in sub if int(row["filled_count"] or 0) > 0),
        "force_buy_ready_plan_a_buy_flips": flipped,
        "force_buy_ready_replayable": tried,
    }


def timing_distribution(rows: list[sqlite3.Row], *, market: str) -> dict[str, Any]:
    buckets = [
        (-10**9, 0, "preopen"),
        (0, 15, "open_0_15"),
        (15, 30, "open_15_30"),
        (30, 60, "open_30_60"),
        (60, 180, "open_1_3h"),
        (180, 10**9, "open_3h_plus"),
    ]
    out: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        if str(row["market"]) != market:
            continue
        features = _json_loads(row["post_open_features_json"])
        if not isinstance(features, dict):
            continue
        elapsed = features.get("market_open_elapsed_min")
        if elapsed is None:
            label = "elapsed_missing"
        else:
            try:
                parsed = float(elapsed)
            except Exception:
                label = "elapsed_invalid"
            else:
                label = next(name for lo, hi, name in buckets if lo <= parsed < hi)
        state = str(row["evidence_data_state"] or "not_recorded")
        out[label]["rows"] += 1
        out[label][f"state_{state}"] += 1
        for field in ("opening_range_break", "volume_ratio_open", "vwap_distance_pct"):
            if features.get(field) is None:
                out[label][f"missing_{field}"] += 1
    return {label: dict(counter) for label, counter in out.items()}


def build_report(*, since: str, market: str | None, limit: int | None) -> dict[str, Any]:
    rows = load_candidate_rows(since=since, market=market, limit=limit)
    canonical_fills = load_canonical_fills(since=since, market=market)
    canonical_by_decision = load_canonical_fills_by_decision(since=since, market=market)
    markets = ["US", "KR"] if market is None else [market]
    return {
        "schema_version": "pipeline_simulation_matrix_v1",
        "since": since,
        "market": market or "both",
        "limit": limit,
        "rows": len(rows),
        "replayable": {
            "evidence_rows": sum(1 for row in rows if row["post_open_features_json"]),
            "route_rows": sum(1 for row in rows if row["payload_json"]),
        },
        "fidelity": fidelity(rows),
        "markets": {
            mk: {
                "stage_matrix": stage_matrix(
                    rows,
                    market=mk,
                    canonical_fills=canonical_fills,
                    canonical_by_decision=canonical_by_decision,
                ),
                "missing_field_counterfactual": missing_field_counterfactual(rows, market=mk),
                "route_counterfactual": route_counterfactual(rows, market=mk),
                "timing_distribution": timing_distribution(rows, market=mk),
            }
            for mk in markets
        },
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Pipeline Simulation Matrix",
        "",
        f"- since: `{report['since']}`",
        f"- market: `{report['market']}`",
        f"- rows: {report['rows']:,}",
        f"- replayable evidence rows: {report['replayable']['evidence_rows']:,}",
        f"- replayable route rows: {report['replayable']['route_rows']:,}",
        "",
        "## Fidelity",
        "",
    ]
    fidelity_payload = report["fidelity"]
    lines.append(f"- evidence: `{fidelity_payload['evidence']}`")
    lines.append(f"- route: `{fidelity_payload['route']}`")
    lines.append(f"- route mismatch top: `{fidelity_payload['route_mismatch_top']}`")
    for market, payload in report["markets"].items():
        matrix = payload["stage_matrix"]
        lines.extend(["", f"## {market}", ""])
        lines.append("| stage | count | survival | sessions |")
        lines.append("| --- | ---: | ---: | ---: |")
        for stage, count in matrix["stage_counts"].items():
            lines.append(
                f"| {stage} | {count:,} | {_pct(matrix['stage_survival'][stage])} | {matrix['stage_sessions'][stage]} |"
            )
        lines.extend(["", "### Top blocks", ""])
        for stage, top in matrix["blocked_top"].items():
            lines.append(f"- {stage}: `{top}`")
        lines.extend(["", "### Canonical fill truth", ""])
        for key, value in matrix["canonical_truth"].items():
            lines.append(f"- {key}: {value}")
        lines.extend(["", "### Missing-field counterfactual", ""])
        missing = payload["missing_field_counterfactual"]
        lines.append(f"- downgraded_rows: {missing['downgraded_rows']:,}")
        lines.append(f"- missing_fields_top: `{missing['missing_fields_top']}`")
        lines.append(f"- recovered_to_buy_ready: `{missing['recovered_to_buy_ready']}`")
        lines.append(
            f"- time_normalized_rvol: present={missing['time_normalized_rvol_present']:,}, "
            f"recovered={missing['time_normalized_rvol_recovered']:,}"
        )
        route = payload["route_counterfactual"]
        lines.extend(["", "### Route counterfactual", ""])
        lines.append(f"- buy_ready_rows: {route['buy_ready_rows']:,}")
        lines.append(f"- route_distribution: `{route['route_distribution']}`")
        lines.append(
            f"- force BUY_READY -> PlanA.buy flips: "
            f"{route['force_buy_ready_plan_a_buy_flips']}/{route['force_buy_ready_replayable']}"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only full pipeline simulation matrix")
    parser.add_argument("--since", default="2026-07-01")
    parser.add_argument("--market", choices=["US", "KR", "both"], default="both")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    args = parser.parse_args()

    market = None if args.market == "both" else args.market
    report = build_report(since=args.since, market=market, limit=args.limit)
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md_out:
        path = Path(args.md_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(to_markdown(report), encoding="utf-8")
    print(to_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
