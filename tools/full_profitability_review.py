from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Metrics:
    n: int
    wins: int
    losses: int
    win_rate_pct: float
    avg_pct: float
    median_pct: float
    sum_pct: float
    profit_factor: float | str | None
    worst_pct: float
    best_pct: float


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a full local profitability review for KR/US.")
    parser.add_argument("--stamp", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--output-dir", default=str(ROOT / "docs" / "reports"))
    args = parser.parse_args()

    closed = load_live_closed(ROOT / "state" / "live_decisions.jsonl")
    selection_rows = load_selection_rows(ROOT / "data" / "ticker_selection_log.db")
    preopen_rows = load_preopen_rows(ROOT / "state", ROOT / "logs" / "preopen")
    screener_rows = load_screener_quality(ROOT / "logs" / "screener_quality")
    action_rows = load_action_routing(ROOT / "logs" / "funnel")
    cohorts = load_cohorts(ROOT / "state")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "basis": {
            "closed_trades": len(closed),
            "selection_rows": len(selection_rows),
            "preopen_rows": len(preopen_rows),
            "valid_preopen_rows": sum(1 for row in preopen_rows if row.get("valid_outcome")),
            "screener_quality_rows": len(screener_rows),
            "action_routing_events": len(action_rows),
            "cohort_files": len(cohorts),
            "notes": [
                "All inputs are local files or sqlite rows; no broker/API/Claude calls are made.",
                "Preopen entry simulations are approximate: entry-to-final uses sampled anchor returns, not tick-level fills.",
                "Forward return fields in ticker_selection_log are post-selection audit labels and must not be used inside live gating without known_at controls.",
            ],
        },
        "closed_trade": closed_trade_payload(closed),
        "selection_gate": selection_payload(selection_rows),
        "preopen": preopen_payload(preopen_rows),
        "screener_quality": screener_payload(screener_rows),
        "action_routing": action_payload(action_rows),
        "cohort_reliability": cohort_payload(cohorts),
        "recommendations": recommendations(),
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"full_profitability_review_{args.stamp}.json"
    md_path = output_dir / f"full_profitability_review_{args.stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(to_markdown(payload), encoding="utf-8")
    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    return 0


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def load_live_closed(path: Path) -> list[dict[str, Any]]:
    rows = []
    for row in load_jsonl(path):
        if row.get("type") != "closed" or row.get("pnl_pct") is None:
            continue
        item = dict(row)
        item["_dt"] = parse_dt(row.get("timestamp"))
        rows.append(item)
    return sorted(rows, key=lambda row: row["_dt"])


def load_selection_rows(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute("SELECT * FROM ticker_selection_log")]
    finally:
        conn.close()
    return rows


def load_preopen_rows(state_dir: Path, preopen_log_dir: Path) -> list[dict[str, Any]]:
    rank_diff = load_preopen_rank_diff(preopen_log_dir)
    rows: list[dict[str, Any]] = []
    for path in sorted(state_dir.glob("preopen_*_*.json")):
        if "scheduler" in path.name:
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        market = str(state.get("market") or "").upper()
        session_date = str(state.get("session_date") or "")
        for raw in state.get("candidates") or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["market"] = market or str(item.get("market") or "").upper()
            item["session_date"] = session_date or str(item.get("session_date") or "")
            item["ticker"] = normalize_ticker(item["market"], item.get("ticker"))
            diff = rank_diff.get((item["market"], item["session_date"], item["ticker"]), {})
            for key in (
                "actual_selection_rank",
                "rank_delta",
                "actual_selected",
                "actual_trade_ready",
                "actual_ordered",
                "actual_rejection_reason",
                "claude_reason",
                "phase",
            ):
                if key in diff:
                    item[key] = diff.get(key)
            item["turnover"] = preopen_turnover(item)
            item["final_return_pct"] = first_num(
                item.get("post_open_return_pct"),
                item.get("open_to_close_pct"),
                latest_sample_return(item),
            )
            item["mfe_pct"] = first_num(item.get("post_open_mfe_pct"), item.get("max_runup_pct"))
            item["mae_pct"] = first_num(item.get("post_open_mae_pct"), item.get("max_drawdown_pct"))
            item["valid_outcome"] = (
                item["ticker"]
                and is_finite(item["final_return_pct"])
                and is_finite(item["mfe_pct"])
                and is_finite(item["mae_pct"])
                and float(item["mfe_pct"]) > -90.0
                and float(item["mae_pct"]) > -90.0
            )
            item["low_liq_tag"] = has_low_liq(item)
            item["hard_pin_current"] = is_current_hard_pin(item)
            item["soft_b"] = is_soft_b(item)
            rows.append(item)
    return rows


def load_preopen_rank_diff(root: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not root.exists():
        return out
    for path in sorted(root.glob("*_rank_diff.jsonl")):
        for row in load_jsonl(path):
            market = str(row.get("market") or "").upper()
            session_date = str(row.get("session_date") or "")
            ticker = normalize_ticker(market, row.get("ticker"))
            if not market or not session_date or not ticker:
                continue
            key = (market, session_date, ticker)
            previous = out.get(key)
            if previous is None or parse_dt(row.get("ts")) >= parse_dt(previous.get("ts")):
                out[key] = row
    return out


def load_screener_quality(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*_candidates.jsonl")):
        rows.extend(load_jsonl(path))
    return rows


def load_action_routing(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.jsonl")):
        for row in load_jsonl(path):
            if row.get("event_type") == "action_routing_shadow":
                rows.append(row)
    return rows


def load_cohorts(state_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(state_dir.glob("candidate_cohort_reliability_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        market = "US" if "_US_" in path.name else "KR"
        for key, rec in (payload.get("cohorts") or {}).items():
            if not isinstance(rec, dict):
                continue
            rows.append({"market": market, "cohort_key": key, **rec})
    return rows


def closed_trade_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    live_metrics = metrics_by(rows, lambda row: row.get("market"), "pnl_pct")
    by_strategy = metrics_by(rows, lambda row: f"{row.get('market')}|{row.get('strategy') or '(blank)'}", "pnl_pct")
    by_exit = metrics_by(rows, lambda row: f"{row.get('market')}|{row.get('exit_reason') or '(blank)'}", "pnl_pct")
    worst = sorted(rows, key=lambda row: safe_float(row.get("pnl_pct")))[:12]
    best = sorted(rows, key=lambda row: safe_float(row.get("pnl_pct")), reverse=True)[:12]
    return {
        "by_market": live_metrics,
        "by_strategy": by_strategy,
        "by_exit_reason": by_exit,
        "worst_trades": compact_trades(worst),
        "best_trades": compact_trades(best),
    }


def selection_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    live = [row for row in rows if row.get("bot_mode") == "live"]
    paper = [row for row in rows if row.get("bot_mode") == "paper"]
    traded_live = [row for row in live if int(row.get("traded") or 0) == 1 and row.get("pnl_pct") is not None]
    blocked_live = [
        row
        for row in live
        if int(row.get("signal_fired") or 0) == 1
        and int(row.get("traded") or 0) == 0
        and str(row.get("blocked_reason") or "")
    ]
    selected_forward = [row for row in live if row.get("forward_1d") is not None or row.get("max_runup_3d") is not None]
    missed = [
        row for row in selected_forward
        if int(row.get("traded") or 0) == 0 and safe_float(row.get("max_runup_3d")) >= 5.0
    ]
    missed.sort(key=lambda row: safe_float(row.get("max_runup_3d")), reverse=True)
    return {
        "row_counts": {
            "live": len(live),
            "paper": len(paper),
            "live_traded": len(traded_live),
            "live_blocked_signals": len(blocked_live),
            "live_forward_labeled": len(selected_forward),
        },
        "live_traded_by_market": metrics_by(traded_live, lambda row: row.get("market"), "pnl_pct"),
        "live_traded_by_trade_ready": metrics_by(
            traded_live,
            lambda row: f"{row.get('market')}|ready={int(row.get('trade_ready') or 0)}",
            "pnl_pct",
        ),
        "live_traded_by_strategy": metrics_by(
            traded_live,
            lambda row: f"{row.get('market')}|{row.get('strategy_name') or '(blank)'}",
            "pnl_pct",
        ),
        "live_traded_by_liquidity": metrics_by(
            traded_live,
            lambda row: f"{row.get('market')}|{row.get('liquidity_bucket') or '(blank)'}",
            "pnl_pct",
        ),
        "live_forward_by_ready": metrics_by(
            selected_forward,
            lambda row: f"{row.get('market')}|ready={int(row.get('trade_ready') or 0)}",
            "max_runup_3d",
        ),
        "blocked_by_reason": blocked_by_reason(blocked_live),
        "missed_runup_top": compact_selection(missed[:20]),
        "daily_caps": daily_caps(traded_live),
    }


def preopen_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row.get("valid_outcome")]
    market_groups = metrics_by(valid, lambda row: row.get("market"), "final_return_pct")
    base_groups = {}
    for market in ("KR", "US"):
        subset = [row for row in valid if row.get("market") == market]
        base_groups[market] = {
            "all": metric_dict([safe_float(row.get("final_return_pct")) for row in subset]),
            "actual_selected": metric_dict([safe_float(row.get("final_return_pct")) for row in subset if bool(row.get("actual_selected"))]),
            "actual_trade_ready": metric_dict([safe_float(row.get("final_return_pct")) for row in subset if bool(row.get("actual_trade_ready"))]),
            "hard_pin_current": metric_dict([safe_float(row.get("final_return_pct")) for row in subset if row.get("hard_pin_current")]),
            "soft_b": metric_dict([safe_float(row.get("final_return_pct")) for row in subset if row.get("soft_b")]),
            "low_liq_tag": metric_dict([safe_float(row.get("final_return_pct")) for row in subset if row.get("low_liq_tag")]),
            "rank_1_10": metric_dict([
                safe_float(row.get("final_return_pct")) for row in subset
                if 1 <= safe_float(row.get("shadow_preopen_rank")) <= 10
            ]),
            "rank_11_30": metric_dict([
                safe_float(row.get("final_return_pct")) for row in subset
                if 11 <= safe_float(row.get("shadow_preopen_rank")) <= 30
            ]),
            "rank_31_plus": metric_dict([
                safe_float(row.get("final_return_pct")) for row in subset
                if safe_float(row.get("shadow_preopen_rank")) >= 31
            ]),
        }
    simulations = {
        "KR": preopen_rule_table([row for row in valid if row.get("market") == "KR"], "KR"),
        "US": preopen_rule_table([row for row in valid if row.get("market") == "US"], "US"),
    }
    missed = [
        row for row in valid
        if not bool(row.get("actual_trade_ready"))
        and (safe_float(row.get("final_return_pct")) >= market_threshold(row.get("market"), kr=5.0, us=3.0)
             or safe_float(row.get("mfe_pct")) >= market_threshold(row.get("market"), kr=10.0, us=5.0))
    ]
    missed.sort(key=lambda row: (safe_float(row.get("final_return_pct")), safe_float(row.get("mfe_pct"))), reverse=True)
    risky = [
        row for row in valid
        if (row.get("hard_pin_current") or row.get("soft_b"))
        and safe_float(row.get("final_return_pct")) <= market_threshold(row.get("market"), kr=-5.0, us=-3.0)
    ]
    risky.sort(key=lambda row: safe_float(row.get("final_return_pct")))
    return {
        "by_market": market_groups,
        "segments": base_groups,
        "rule_simulations": simulations,
        "missed_strong_candidates": compact_preopen(missed[:25]),
        "expanded_rule_risks": compact_preopen(risky[:25]),
        "invalid_outcome_count": len(rows) - len(valid),
    }


def preopen_rule_table(rows: list[dict[str, Any]], market: str) -> dict[str, Any]:
    configs = [
        ("current_hard_pin", lambda row: row.get("hard_pin_current"), None),
        ("soft_b_naive", lambda row: row.get("soft_b"), None),
        ("soft_b_confirm30", lambda row: row.get("soft_b") and ret(row, 5) > -3 and ret(row, 30) >= 0 and low_to(row, 30) > -4, 30),
        ("soft_b_confirm60", lambda row: row.get("soft_b") and ret(row, 30) >= 0 and ret(row, 60) >= market_threshold(market, kr=1.0, us=0.5) and low_to(row, 60) > market_threshold(market, kr=-5.0, us=-3.5), 60),
        ("low_liq_ignite60", lambda row: row.get("low_liq_tag") and ret(row, 5) >= market_threshold(market, kr=3.0, us=1.5) and ret(row, 30) >= market_threshold(market, kr=2.0, us=1.0) and ret(row, 60) >= market_threshold(market, kr=3.0, us=1.5) and low_to(row, 60) > market_threshold(market, kr=-1.0, us=-2.0) and sample_value(row, 5) >= market_threshold(market, kr=1_000_000_000.0, us=5_000_000.0), 60),
        ("late_reclaim_watch", lambda row: low_to(row, 60) <= market_threshold(market, kr=-5.0, us=-3.0) and ret(row, 90) >= market_threshold(market, kr=3.0, us=1.5) and ret(row, 120) >= market_threshold(market, kr=5.0, us=2.0), 120),
    ]
    out = {}
    for name, pred, entry_offset in configs:
        selected = [row for row in rows if pred(row)]
        final_values = [safe_float(row.get("final_return_pct")) for row in selected]
        item = {"final": metric_dict(final_values)}
        if entry_offset:
            deltas = [
                entry_to_final(safe_float(row.get("final_return_pct")), ret(row, entry_offset))
                for row in selected
                if is_finite(ret(row, entry_offset))
            ]
            item[f"entry_{entry_offset}m_to_final"] = metric_dict(deltas)
        out[name] = item
    return out


def screener_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter()
    phase_counts = Counter()
    prompt_counts = Counter()
    latest_by_key = {}
    for row in rows:
        market = str(row.get("market") or "").upper()
        status = str(row.get("status") or "")
        phase = str(row.get("phase") or "")
        status_counts[f"{market}|{status}"] += 1
        phase_counts[f"{market}|{phase}"] += 1
        prompt_counts[f"{market}|input={bool(row.get('input_to_claude'))}"] += 1
        key = (market, str(row.get("ticker") or ""))
        latest_by_key[key] = row
    latest = list(latest_by_key.values())
    latest_status = Counter(f"{row.get('market')}|{row.get('status')}" for row in latest)
    not_prompt_high_score = [
        row for row in latest
        if not bool(row.get("input_to_claude")) and safe_float(row.get("score_current")) >= 100.0
    ]
    not_prompt_high_score.sort(key=lambda row: safe_float(row.get("score_current")), reverse=True)
    return {
        "all_status_counts": dict(status_counts.most_common()),
        "phase_counts": dict(phase_counts.most_common()),
        "prompt_counts": dict(prompt_counts.most_common()),
        "latest_status_counts": dict(latest_status.most_common()),
        "latest_high_score_not_in_prompt": [
            {
                "market": row.get("market"),
                "ticker": row.get("ticker"),
                "phase": row.get("phase"),
                "status": row.get("status"),
                "score_current": round(safe_float(row.get("score_current")), 3),
                "change_rate": round(safe_float(row.get("change_rate")), 3),
                "turnover": round(safe_float(row.get("turnover")), 0),
                "excluded_reason": row.get("excluded_reason", ""),
            }
            for row in not_prompt_high_score[:20]
        ],
    }


def action_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter()
    routes = Counter()
    events_by_market = Counter()
    for event in rows:
        market = str(event.get("market") or "").upper()
        events_by_market[market] += 1
        for route in event.get("routes") or []:
            if not isinstance(route, dict):
                continue
            action = str(route.get("final_action") or "")
            reason = str(route.get("reason") or "")
            path = str(route.get("route") or "(none)")
            counts[f"{market}|{action}"] += 1
            routes[f"{market}|{path}|{reason}"] += 1
    return {
        "event_count_by_market": dict(events_by_market.most_common()),
        "final_action_counts": dict(counts.most_common()),
        "route_reason_counts": dict(routes.most_common(30)),
    }


def cohort_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for market in ("KR", "US"):
        subset = [row for row in rows if row.get("market") == market and int(row.get("sample_count") or 0) >= 5]
        worst = sorted(subset, key=lambda row: safe_float(row.get("score")))[:12]
        best = sorted(subset, key=lambda row: safe_float(row.get("score")), reverse=True)[:12]
        out[market] = {
            "sampled_cohorts": len(subset),
            "worst": compact_cohorts(worst),
            "best": compact_cohorts(best),
        }
    return out


def recommendations() -> list[dict[str, str]]:
    return [
        {
            "area": "candidate_state",
            "change": "Promote a tier book to source of truth: CORE, WATCH, PROBATION, BENCH, QUARANTINE.",
            "reason": "Flat today_tickers replacement loses continuity and cannot express watch-only vs executable risk.",
        },
        {
            "area": "preopen",
            "change": "Merge hard pins into session_open candidates, but force watch-only until post-open confirmation.",
            "reason": "Current hard pins are not reliable enough to auto-buy and can be dropped before Claude selection.",
        },
        {
            "area": "preopen",
            "change": "Add low-liq ignition and late-reclaim watch buckets with 60m/120m confirmation, not open auction entry.",
            "reason": "The best missed KR winners were either low-liq ignition or late reclaim; naive soft expansion is negative.",
        },
        {
            "area": "replacement",
            "change": "Use trainer/cohort delta gate for both KR and US replacement-in, with looser KR shadow rollout first.",
            "reason": "Replacement should require incoming quality to beat outgoing quality instead of rotating by freshness alone.",
        },
        {
            "area": "execution",
            "change": "Route only final applied trade_ready, not raw Claude trade_ready, and block all new probes under stop-cluster disaster.",
            "reason": "Raw action can survive in logs after runtime normalization removes it; disaster blocks must own final execution.",
        },
        {
            "area": "risk_exit",
            "change": "Keep cap2/MFE protection as the immediate overlay, then move to broker-backed persistent peak stops.",
            "reason": "Current local simulation shows the largest positive effect comes from left-tail clipping and MFE preservation.",
        },
        {
            "area": "observability",
            "change": "Backfill forward labels into screener_quality rows and add known_at snapshots for every promotion/demotion.",
            "reason": "Current candidate quality logs explain funnel loss, but not enough forward PnL for rule optimization.",
        },
    ]


def metrics_by(rows: list[dict[str, Any]], key_fn, value_key: str) -> dict[str, Any]:
    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        key = str(key_fn(row) or "(blank)")
        value = row.get(value_key)
        if is_finite(value):
            groups[key].append(float(value))
    return {key: metric_dict(values) for key, values in sorted(groups.items())}


def metric_dict(values: Iterable[float]) -> dict[str, Any]:
    metric = metrics(values)
    return {
        "n": metric.n,
        "wins": metric.wins,
        "losses": metric.losses,
        "win_rate_pct": round(metric.win_rate_pct, 2),
        "avg_pct": round(metric.avg_pct, 4),
        "median_pct": round(metric.median_pct, 4),
        "sum_pct": round(metric.sum_pct, 4),
        "profit_factor": round(metric.profit_factor, 4) if isinstance(metric.profit_factor, float) else metric.profit_factor,
        "worst_pct": round(metric.worst_pct, 4),
        "best_pct": round(metric.best_pct, 4),
    }


def metrics(values: Iterable[float]) -> Metrics:
    clean = [float(value) for value in values if is_finite(value)]
    wins = [value for value in clean if value > 0]
    losses = [value for value in clean if value < 0]
    pf: float | str | None
    pf = sum(wins) / abs(sum(losses)) if losses else ("inf" if wins else None)
    return Metrics(
        n=len(clean),
        wins=len(wins),
        losses=len(losses),
        win_rate_pct=(len(wins) / len(clean) * 100.0 if clean else 0.0),
        avg_pct=(sum(clean) / len(clean) if clean else 0.0),
        median_pct=(median(clean) if clean else 0.0),
        sum_pct=sum(clean),
        profit_factor=pf,
        worst_pct=(min(clean) if clean else 0.0),
        best_pct=(max(clean) if clean else 0.0),
    )


def blocked_by_reason(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for reason in sorted({str(row.get("blocked_reason") or "") for row in rows}):
        subset = [row for row in rows if str(row.get("blocked_reason") or "") == reason]
        out[reason] = {
            "n": len(subset),
            "ready": sum(1 for row in subset if int(row.get("trade_ready") or 0) == 1),
            "forward_1d": metric_dict([safe_float(row.get("forward_1d")) for row in subset if row.get("forward_1d") is not None]),
            "max_runup_3d": metric_dict([safe_float(row.get("max_runup_3d")) for row in subset if row.get("max_runup_3d") is not None]),
        }
    return out


def daily_caps(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for by_market in (False, True):
        label = "per_market" if by_market else "total"
        for cap in (1, 2, 3):
            selected = first_n_by_day(rows, cap, by_market=by_market)
            out[f"{label}_cap_{cap}"] = {
                **metric_dict([safe_float(row.get("pnl_pct")) for row in selected]),
                "kept": len(selected),
                "total": len(rows),
            }
    return out


def first_n_by_day(rows: list[dict[str, Any]], n: int, *, by_market: bool) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (str(row.get("date") or ""), str(row.get("market") or "")) if by_market else (str(row.get("date") or ""),)
        groups[key].append(row)
    selected = []
    for group in groups.values():
        selected.extend(
            sorted(group, key=lambda row: parse_dt(row.get("traded_at") or row.get("signal_at") or row.get("selected_at")))[:n]
        )
    return selected


def compact_trades(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": row.get("timestamp"),
            "market": row.get("market"),
            "ticker": row.get("ticker"),
            "strategy": row.get("strategy"),
            "exit_reason": row.get("exit_reason"),
            "pnl_pct": round(safe_float(row.get("pnl_pct")), 4),
            "mfe_pct": round(first_num(row.get("position_mfe_pct"), row.get("peak_pnl_pct")), 4),
            "mae_pct": round(safe_float(row.get("position_mae_pct")), 4),
            "pnl_krw": round(safe_float(row.get("pnl_krw")), 0),
        }
        for row in rows
    ]


def compact_selection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "date": row.get("date"),
            "market": row.get("market"),
            "ticker": row.get("ticker"),
            "trade_ready": int(row.get("trade_ready") or 0),
            "signal_fired": int(row.get("signal_fired") or 0),
            "blocked_reason": row.get("blocked_reason"),
            "strategy": row.get("recommended_strategy") or row.get("strategy_name"),
            "forward_1d": round(safe_float(row.get("forward_1d")), 4),
            "forward_3d": round(safe_float(row.get("forward_3d")), 4),
            "max_runup_3d": round(safe_float(row.get("max_runup_3d")), 4),
            "max_drawdown_3d": round(safe_float(row.get("max_drawdown_3d")), 4),
        }
        for row in rows
    ]


def compact_preopen(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        output.append(
            {
                "session_date": row.get("session_date"),
                "market": row.get("market"),
                "ticker": row.get("ticker"),
                "rank": int(safe_float(row.get("shadow_preopen_rank"))),
                "score": round(safe_float(row.get("preopen_score")), 4),
                "risk_tags": list(row.get("risk_tags") or []),
                "selected": bool(row.get("actual_selected")),
                "trade_ready": bool(row.get("actual_trade_ready")),
                "final": round(safe_float(row.get("final_return_pct")), 4),
                "mfe": round(safe_float(row.get("mfe_pct")), 4),
                "mae": round(safe_float(row.get("mae_pct")), 4),
                "ret5": round(ret(row, 5), 4),
                "ret30": round(ret(row, 30), 4),
                "ret60": round(ret(row, 60), 4),
                "hard_pin": bool(row.get("hard_pin_current")),
                "soft_b": bool(row.get("soft_b")),
                "low_liq": bool(row.get("low_liq_tag")),
            }
        )
    return output


def compact_cohorts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "cohort_key": row.get("cohort_key"),
            "score": round(safe_float(row.get("score")), 4),
            "sample_count": int(row.get("sample_count") or 0),
            "ready_count": int(row.get("ready_count") or 0),
            "healthy_count": int(row.get("healthy_count") or 0),
            "weak_count": int(row.get("weak_count") or 0),
        }
        for row in rows
    ]


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Full Profitability Review",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Basis",
        "",
    ]
    for key, value in payload["basis"].items():
        if key == "notes":
            continue
        lines.append(f"- {key}: {value}")
    lines.extend(["", "Notes:"])
    lines.extend(f"- {note}" for note in payload["basis"]["notes"])

    lines.extend(section_metrics("Closed Trades By Market", payload["closed_trade"]["by_market"]))
    lines.extend(section_metrics("Closed Trades By Strategy", payload["closed_trade"]["by_strategy"], limit=40))
    lines.extend(section_metrics("Selection Live Traded By Ready", payload["selection_gate"]["live_traded_by_trade_ready"]))
    lines.extend(section_metrics("Selection Live Traded By Strategy", payload["selection_gate"]["live_traded_by_strategy"], limit=40))
    lines.extend(section_metrics("Selection Forward Max Runup By Ready", payload["selection_gate"]["live_forward_by_ready"]))
    lines.extend(section_metrics("Preopen By Market", payload["preopen"]["by_market"]))

    lines.append("")
    lines.append("## Preopen Segments")
    for market, groups in payload["preopen"]["segments"].items():
        lines.append("")
        lines.append(f"### {market}")
        lines.extend(metrics_table(groups))

    lines.append("")
    lines.append("## Preopen Rule Simulations")
    for market, rules in payload["preopen"]["rule_simulations"].items():
        lines.append("")
        lines.append(f"### {market}")
        lines.append("| Rule | Basis | N | W/L | Win | Avg | Median | PF | Worst | Best |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for rule, result in rules.items():
            lines.append(metric_line(rule, "final", result["final"]))
            for key, metric in result.items():
                if key == "final":
                    continue
                lines.append(metric_line(rule, key, metric))

    lines.extend(list_table("Missed Strong Preopen Candidates", payload["preopen"]["missed_strong_candidates"]))
    lines.extend(list_table("Expanded Rule Risks", payload["preopen"]["expanded_rule_risks"]))
    lines.extend(list_table("Missed Selection Runup Top", payload["selection_gate"]["missed_runup_top"]))

    lines.append("")
    lines.append("## Daily Entry Caps")
    lines.append("| Rule | Kept | N | W/L | Win | Avg | PF |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for key, item in payload["selection_gate"]["daily_caps"].items():
        lines.append(
            f"| {key} | {item['kept']}/{item['total']} | {item['n']} | {item['wins']}/{item['losses']} | "
            f"{item['win_rate_pct']:.1f}% | {fmt_pct(item['avg_pct'])} | {fmt_pf(item['profit_factor'])} |"
        )

    lines.append("")
    lines.append("## Blocked Signals")
    lines.append("| Reason | N | Ready | Fwd1D Avg | Runup3D Avg |")
    lines.append("|---|---:|---:|---:|---:|")
    for reason, item in payload["selection_gate"]["blocked_by_reason"].items():
        lines.append(
            f"| {reason or '(blank)'} | {item['n']} | {item['ready']} | "
            f"{fmt_pct(item['forward_1d']['avg_pct']) if item['forward_1d']['n'] else 'NA'} | "
            f"{fmt_pct(item['max_runup_3d']['avg_pct']) if item['max_runup_3d']['n'] else 'NA'} |"
        )

    lines.append("")
    lines.append("## Screener Funnel")
    for title, counts in (
        ("All Status Counts", payload["screener_quality"]["all_status_counts"]),
        ("Latest Status Counts", payload["screener_quality"]["latest_status_counts"]),
        ("Prompt Counts", payload["screener_quality"]["prompt_counts"]),
    ):
        lines.append("")
        lines.append(f"### {title}")
        for key, value in counts.items():
            lines.append(f"- {key}: {value}")

    lines.append("")
    lines.append("## Action Routing")
    for title, counts in (
        ("Final Action Counts", payload["action_routing"]["final_action_counts"]),
        ("Route Reason Counts", payload["action_routing"]["route_reason_counts"]),
    ):
        lines.append("")
        lines.append(f"### {title}")
        for key, value in counts.items():
            lines.append(f"- {key}: {value}")

    lines.append("")
    lines.append("## Cohort Reliability")
    for market, data in payload["cohort_reliability"].items():
        lines.append("")
        lines.append(f"### {market} Worst")
        lines.extend(list_table("", data["worst"], header=False))
        lines.append("")
        lines.append(f"### {market} Best")
        lines.extend(list_table("", data["best"], header=False))

    lines.append("")
    lines.append("## Recommendations")
    for rec in payload["recommendations"]:
        lines.append(f"- {rec['area']}: {rec['change']} Reason: {rec['reason']}")
    lines.append("")
    return "\n".join(lines)


def section_metrics(title: str, metrics_map: dict[str, Any], limit: int | None = None) -> list[str]:
    lines = ["", f"## {title}"]
    items = list(metrics_map.items())
    if limit:
        items = sorted(items, key=lambda item: item[1].get("n", 0), reverse=True)[:limit]
    lines.extend(metrics_table(dict(items)))
    return lines


def metrics_table(metrics_map: dict[str, Any]) -> list[str]:
    lines = ["| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for key, item in metrics_map.items():
        lines.append(metric_line(key, "", item))
    return lines


def metric_line(group: str, basis: str, item: dict[str, Any]) -> str:
    if basis:
        return (
            f"| {group} | {basis} | {item['n']} | {item['wins']}/{item['losses']} | "
            f"{item['win_rate_pct']:.1f}% | {fmt_pct(item['avg_pct'])} | {fmt_pct(item['median_pct'])} | "
            f"{fmt_pf(item['profit_factor'])} | {fmt_pct(item['worst_pct'])} | {fmt_pct(item['best_pct'])} |"
        )
    return (
        f"| {group} | {item['n']} | {item['wins']}/{item['losses']} | "
        f"{item['win_rate_pct']:.1f}% | {fmt_pct(item['avg_pct'])} | {fmt_pct(item['median_pct'])} | "
        f"{fmt_pf(item['profit_factor'])} | {fmt_pct(item['worst_pct'])} | {fmt_pct(item['best_pct'])} |"
    )


def list_table(title: str, rows: list[dict[str, Any]], *, header: bool = True) -> list[str]:
    lines: list[str] = []
    if title:
        lines.extend(["", f"## {title}"])
    if not rows:
        lines.append("- none")
        return lines
    keys = list(rows[0].keys())
    if header:
        lines.append("| " + " | ".join(keys) + " |")
        lines.append("|" + "|".join(["---"] * len(keys)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(key, "")) for key in keys) + " |")
    return lines


def normalize_ticker(market: str, ticker: Any) -> str:
    raw = str(ticker or "").strip()
    return raw.upper() if str(market or "").upper() == "US" else raw


def has_low_liq(row: dict[str, Any]) -> bool:
    tags = {str(tag).lower() for tag in row.get("risk_tags") or []}
    if tags.intersection({"low_liquidity", "thin_volume"}):
        return True
    return safe_float(row.get("turnover")) > 0 and safe_float(row.get("turnover")) < market_threshold(row.get("market"), kr=1_000_000_000.0, us=50_000_000.0)


def is_current_hard_pin(row: dict[str, Any]) -> bool:
    market = row.get("market")
    rank = safe_float(row.get("shadow_preopen_rank"))
    score = safe_float(row.get("preopen_score"))
    turnover = safe_float(row.get("turnover"))
    return rank > 0 and rank <= 3 and score >= 0.50 and turnover >= market_threshold(market, kr=1_000_000_000.0, us=50_000_000.0)


def is_soft_b(row: dict[str, Any]) -> bool:
    market = row.get("market")
    rank = safe_float(row.get("shadow_preopen_rank"))
    score = safe_float(row.get("preopen_score"))
    turnover = safe_float(row.get("turnover"))
    return (
        ((rank > 0 and rank <= 20) or score >= 0.50)
        and turnover >= market_threshold(market, kr=1_000_000_000.0, us=50_000_000.0)
        and not has_low_liq(row)
    )


def preopen_turnover(row: dict[str, Any]) -> float:
    for key in ("extended_dollar_volume", "dollar_volume", "turnover", "prior_day_traded_value"):
        value = row.get(key)
        if is_finite(value) and float(value) > 0:
            return float(value)
    return safe_float(row.get("price")) * safe_float(row.get("volume"))


def latest_sample_return(row: dict[str, Any]) -> float | None:
    samples = [sample for sample in row.get("outcome_samples") or [] if isinstance(sample, dict)]
    if not samples:
        return None
    samples.sort(key=lambda item: safe_float(item.get("offset_min")))
    return first_num(samples[-1].get("return_pct"))


def ret(row: dict[str, Any], offset: int) -> float:
    direct = row.get(f"post_open_{offset}m_return_pct")
    if is_finite(direct):
        return float(direct)
    samples = [sample for sample in row.get("outcome_samples") or [] if isinstance(sample, dict)]
    before = [sample for sample in samples if safe_float(sample.get("offset_min")) <= offset and is_finite(sample.get("return_pct"))]
    if not before:
        return math.nan
    before.sort(key=lambda item: safe_float(item.get("offset_min")))
    return safe_float(before[-1].get("return_pct"))


def low_to(row: dict[str, Any], offset: int) -> float:
    samples = [sample for sample in row.get("outcome_samples") or [] if isinstance(sample, dict)]
    lows = [
        safe_float(sample.get("low_return_pct"))
        for sample in samples
        if safe_float(sample.get("offset_min")) <= offset and is_finite(sample.get("low_return_pct"))
    ]
    if lows:
        return min(lows)
    return safe_float(row.get("mae_pct")) if is_finite(row.get("mae_pct")) else math.nan


def sample_value(row: dict[str, Any], offset: int) -> float:
    samples = [sample for sample in row.get("outcome_samples") or [] if isinstance(sample, dict)]
    before = [sample for sample in samples if safe_float(sample.get("offset_min")) <= offset]
    if not before:
        return 0.0
    before.sort(key=lambda item: safe_float(item.get("offset_min")))
    sample = before[-1]
    price = first_num(sample.get("price"), row.get("anchor_price"), row.get("price"))
    return safe_float(price) * safe_float(sample.get("volume"))


def entry_to_final(final_ret: float, entry_ret: float) -> float:
    if not is_finite(final_ret) or not is_finite(entry_ret) or entry_ret <= -99.0:
        return math.nan
    return ((1.0 + final_ret / 100.0) / (1.0 + entry_ret / 100.0) - 1.0) * 100.0


def market_threshold(market: Any, *, kr: float, us: float) -> float:
    return us if str(market or "").upper() == "US" else kr


def first_num(*values: Any) -> float:
    for value in values:
        if is_finite(value):
            return float(value)
    return math.nan


def safe_float(value: Any) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else 0.0
    except Exception:
        return 0.0


def is_finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except Exception:
        return False


def parse_dt(value: Any) -> datetime:
    if not value:
        return datetime.min
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.fromisoformat(text.split("+")[0])
        except Exception:
            return datetime.min


def fmt_pct(value: Any) -> str:
    return f"{safe_float(value):+.3f}%"


def fmt_pf(value: Any) -> str:
    if value is None:
        return "NA"
    if value == "inf":
        return "inf"
    return f"{safe_float(value):.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
