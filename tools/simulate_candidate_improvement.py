from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Metric:
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
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = ROOT / "docs" / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    backtest_rows = load_long_backtest_rows()
    selection_rows = load_selection_rows()
    screener_rows = load_screener_rows()
    raw_calls = load_raw_calls()
    route_rows = load_action_routes()
    preopen_rows = load_preopen_rows()

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "coverage": coverage_payload(
            backtest_rows,
            selection_rows,
            screener_rows,
            raw_calls,
            route_rows,
            preopen_rows,
        ),
        "long_backtest_gap_guard": long_gap_guard_payload(backtest_rows),
        "recent_watch_trigger": watch_trigger_payload(selection_rows),
        "prompt_visibility": prompt_visibility_payload(screener_rows, raw_calls),
        "action_routing_shadow": routing_payload(route_rows, selection_rows),
        "preopen_low_liq": preopen_payload(preopen_rows),
        "interpretation": interpretation(),
        "limits": [
            "2018 long backtest has daily entry_gap/returns, not Claude prompt or intraday VWAP/OR features.",
            "WATCH_TRIGGER simulation on ticker_selection_log uses from_high_bucket/change/liquidity proxies from 2026-04-07..2026-05-08.",
            "Prompt visibility simulation uses raw_calls only where a raw Claude selection prompt exists.",
            "low_liq_ignite60 uses preopen sampled outcome data and is an entry-offset approximation, not tick-level fill simulation.",
        ],
    }

    json_path = output_dir / f"candidate_improvement_simulation_{stamp}.json"
    md_path = output_dir / f"candidate_improvement_simulation_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(to_markdown(payload), encoding="utf-8")
    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    return 0


def load_long_backtest_rows() -> list[dict[str, Any]]:
    db_path = ROOT / "data" / "market_data" / "market_data.sqlite"
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    run_ids: list[str] = []
    try:
        for row in conn.execute(
            """
            SELECT run_id, params_json
            FROM backtest_runs
            WHERE data_start='2018-01-01'
              AND data_end='2026-04-24'
              AND cost_model='realistic'
              AND entry_model='next_open'
            """
        ):
            params = parse_json(row["params_json"])
            if params.get("analysis_window") == "official_2018" and int(params.get("ticker_count") or 0) > 100:
                run_ids.append(row["run_id"])
        if not run_ids:
            return []
        placeholders = ",".join("?" for _ in run_ids)
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM backtest_trades
                WHERE run_id IN ({placeholders})
                """,
                run_ids,
            )
        ]
    finally:
        conn.close()
    return rows


def load_selection_rows() -> list[dict[str, Any]]:
    db_path = ROOT / "data" / "ticker_selection_log.db"
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM ticker_selection_log")]
    finally:
        conn.close()


def load_screener_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((ROOT / "logs" / "screener_quality").glob("*_candidates.jsonl")):
        rows.extend(load_jsonl(path))
    return rows


def load_action_routes() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((ROOT / "logs" / "funnel").glob("action_routing_shadow_*.jsonl")):
        for event in load_jsonl(path):
            market = str(event.get("market") or "").upper()
            session_date = str(event.get("session_date") or "")
            written_at = str(event.get("written_at") or "")
            for route in event.get("routes") or []:
                if not isinstance(route, dict):
                    continue
                rows.append(
                    {
                        "market": market,
                        "session_date": session_date,
                        "written_at": written_at,
                        **route,
                    }
                )
    return rows


def load_preopen_rows() -> list[dict[str, Any]]:
    try:
        from full_profitability_review import load_preopen_rows as _load_preopen_rows
    except Exception:
        return []
    return _load_preopen_rows(ROOT / "state", ROOT / "logs" / "preopen")


def load_raw_calls() -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    raw_dir = ROOT / "logs" / "raw_calls"
    pattern = re.compile(r"(?P<date>\d{8})_(?P<market>KR|US)_select_tickers(?:_retry)?_(?P<time>\d{6})", re.I)
    ticker_line = re.compile(r"^\s*([A-Z][A-Z0-9.\-]{0,12}|\d{6})\s+chg=", re.I)
    for path in sorted(raw_dir.glob("*_select_tickers*.json")):
        match = pattern.search(path.name)
        if not match:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        tickers = []
        for line in str(payload.get("prompt") or "").splitlines():
            m = ticker_line.match(line)
            if m:
                tickers.append(normalize_ticker(match.group("market").upper(), m.group(1)))
        if not tickers:
            continue
        ts = parse_call_ts(match.group("date"), match.group("time"))
        calls.append(
            {
                "path": str(path),
                "date": ts.date().isoformat(),
                "market": match.group("market").upper(),
                "timestamp": ts,
                "tickers": list(dict.fromkeys(tickers)),
            }
        )
    return calls


def coverage_payload(
    backtest_rows: list[dict[str, Any]],
    selection_rows: list[dict[str, Any]],
    screener_rows: list[dict[str, Any]],
    raw_calls: list[dict[str, Any]],
    route_rows: list[dict[str, Any]],
    preopen_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "long_backtest": coverage_dates(backtest_rows, "signal_date", "market"),
        "ticker_selection_log": coverage_dates(selection_rows, "date", "market"),
        "screener_quality": coverage_dates(screener_rows, "timestamp", "market"),
        "raw_selection_calls": coverage_dates(raw_calls, "date", "market"),
        "action_routing_shadow": coverage_dates(route_rows, "session_date", "market"),
        "preopen_state": coverage_dates(preopen_rows, "session_date", "market"),
    }


def long_gap_guard_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    thresholds = {"KR": [3.0, 5.0, 8.0], "US": [2.0, 4.0, 6.0]}
    for market in ("KR", "US"):
        subset = [row for row in rows if str(row.get("market") or "").upper() == market]
        scenarios: dict[str, Any] = {
            "baseline": metric_dict(value(row, "net_return_pct") for row in subset),
            "by_strategy": {
                strategy: metric_dict(value(row, "net_return_pct") for row in subset if str(row.get("strategy") or "") == strategy)
                for strategy in sorted({str(row.get("strategy") or "") for row in subset})
            },
        }
        for threshold in thresholds[market]:
            demoted = [row for row in subset if value(row, "entry_gap_pct") >= threshold]
            kept = [row for row in subset if value(row, "entry_gap_pct") < threshold]
            demoted_non_momo = [
                row
                for row in subset
                if value(row, "entry_gap_pct") >= threshold
                and str(row.get("strategy") or "") not in {"momentum", "gap_pullback"}
            ]
            kept_fast_lane = [row for row in subset if row not in demoted_non_momo]
            scenarios[f"gap_guard_{threshold:g}_demote_all"] = {
                "kept": metric_dict(value(row, "net_return_pct") for row in kept),
                "demoted": metric_dict(value(row, "net_return_pct") for row in demoted),
                "demoted_by_strategy": by_strategy_metrics(demoted, "net_return_pct"),
            }
            scenarios[f"gap_guard_{threshold:g}_fast_lane_momentum_gap"] = {
                "kept": metric_dict(value(row, "net_return_pct") for row in kept_fast_lane),
                "demoted": metric_dict(value(row, "net_return_pct") for row in demoted_non_momo),
            }
        out[market] = scenarios
    return out


def watch_trigger_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    live_ready = [
        row
        for row in rows
        if str(row.get("bot_mode") or "") == "live"
        and int(row.get("trade_ready") or 0) == 1
    ]
    for market in ("KR", "US"):
        subset = [row for row in live_ready if str(row.get("market") or "").upper() == market]
        labeled = [row for row in subset if is_finite(row.get("forward_1d")) or is_finite(row.get("max_runup_3d"))]
        scenarios = {
            "baseline_ready_forward_1d": metric_dict(value(row, "forward_1d") for row in labeled),
            "baseline_ready_forward_3d": metric_dict(value(row, "forward_3d") for row in labeled),
            "baseline_ready_max_runup_3d": metric_dict(value(row, "max_runup_3d") for row in labeled),
            "baseline_ready_max_drawdown_3d": metric_dict(value(row, "max_drawdown_3d") for row in labeled),
            "actual_traded_pnl": metric_dict(value(row, "pnl_pct") for row in subset if is_finite(row.get("pnl_pct"))),
        }
        for name, pred in watch_trigger_scenarios(market).items():
            demoted = [row for row in labeled if pred(row)]
            kept = [row for row in labeled if not pred(row)]
            scenarios[name] = {
                "kept_forward_1d": metric_dict(value(row, "forward_1d") for row in kept),
                "demoted_forward_1d": metric_dict(value(row, "forward_1d") for row in demoted),
                "demoted_forward_3d": metric_dict(value(row, "forward_3d") for row in demoted),
                "demoted_max_runup_3d": metric_dict(value(row, "max_runup_3d") for row in demoted),
                "demoted_max_drawdown_3d": metric_dict(value(row, "max_drawdown_3d") for row in demoted),
                "demoted_sample": compact_selection(demoted[:20]),
            }
        out[market] = scenarios
    return out


def watch_trigger_scenarios(market: str):
    extreme_change = 10.0 if market == "KR" else 5.0
    fast_change_cap = 15.0 if market == "KR" else 8.0
    fast_vol = 10.0 if market == "KR" else 1.0

    def overextended(row: dict[str, Any]) -> bool:
        return str(row.get("from_high_bucket") or "").lower() in {"at_high", "near_high"}

    def fast_lane_proxy(row: dict[str, Any]) -> bool:
        return (
            str(row.get("liquidity_bucket") or "").lower() == "high"
            and value(row, "vol_ratio") >= fast_vol
            and value(row, "change_pct") <= fast_change_cap
            and abs(value(row, "from_high_pct")) <= 3.0
        )

    return {
        "watch_trigger_demote_all_at_high": overextended,
        "watch_trigger_demote_extreme_at_high": lambda row: overextended(row) and value(row, "change_pct") >= extreme_change,
        "watch_trigger_with_fast_lane_proxy": lambda row: overextended(row) and not fast_lane_proxy(row),
    }


def prompt_visibility_payload(screener_rows: list[dict[str, Any]], raw_calls: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in screener_rows:
        market = str(row.get("market") or "").upper()
        ts = parse_dt(row.get("timestamp"))
        if not market or ts is None:
            continue
        key = (market, ts.date().isoformat(), ts.isoformat(timespec="seconds"), str(row.get("phase") or ""))
        groups[key].append(row)

    calls_by_market_date: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for call in raw_calls:
        calls_by_market_date[(call["market"], call["date"])].append(call)

    matched = []
    for (market, date, ts_str, phase), rows in groups.items():
        ts = parse_dt(ts_str)
        if ts is None:
            continue
        candidates = calls_by_market_date.get((market, date), [])
        if not candidates:
            continue
        closest = min(candidates, key=lambda call: abs((call["timestamp"] - ts).total_seconds()))
        if abs((closest["timestamp"] - ts).total_seconds()) > 180:
            continue
        prompt_set = set(closest["tickers"])
        ranked = sorted(rows, key=lambda row: value(row, "score_current"), reverse=True)
        top30 = {normalize_ticker(market, row.get("ticker")) for row in ranked[:30]}
        top36 = {normalize_ticker(market, row.get("ticker")) for row in ranked[:36]}
        actual_missing_top30 = sorted(top30 - prompt_set)
        actual_missing_top36 = sorted(top36 - prompt_set)
        gained_by_score36 = sorted(top36 - prompt_set)
        matched.append(
            {
                "market": market,
                "date": date,
                "timestamp": ts_str,
                "phase": phase,
                "raw_rows": len(rows),
                "actual_prompt_count": len(prompt_set),
                "reported_input_true": sum(1 for row in rows if bool(row.get("input_to_claude"))),
                "actual_missing_top30_count": len(actual_missing_top30),
                "actual_missing_top36_count": len(actual_missing_top36),
                "score36_gain_count": len(gained_by_score36),
                "actual_missing_top30": compact_screener([row for row in ranked[:30] if normalize_ticker(market, row.get("ticker")) in actual_missing_top30]),
                "score36_gain": compact_screener([row for row in ranked[:36] if normalize_ticker(market, row.get("ticker")) in gained_by_score36]),
            }
        )

    out: dict[str, Any] = {}
    for market in ("KR", "US"):
        subset = [row for row in matched if row["market"] == market]
        out[market] = {
            "matched_selection_events": len(subset),
            "avg_raw_rows": avg(row["raw_rows"] for row in subset),
            "avg_actual_prompt_count": avg(row["actual_prompt_count"] for row in subset),
            "avg_reported_input_true": avg(row["reported_input_true"] for row in subset),
            "events_with_missing_top30": sum(1 for row in subset if row["actual_missing_top30_count"] > 0),
            "avg_missing_top30": avg(row["actual_missing_top30_count"] for row in subset),
            "avg_score36_gain": avg(row["score36_gain_count"] for row in subset),
            "worst_missing_events": sorted(
                subset,
                key=lambda row: (row["actual_missing_top30_count"], row["score36_gain_count"]),
                reverse=True,
            )[:10],
        }
    return out


def routing_payload(route_rows: list[dict[str, Any]], selection_rows: list[dict[str, Any]]) -> dict[str, Any]:
    selection_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in selection_rows:
        key = (
            str(row.get("market") or "").upper(),
            str(row.get("date") or ""),
            normalize_ticker(str(row.get("market") or "").upper(), row.get("ticker")),
        )
        selection_index[key] = row

    out: dict[str, Any] = {}
    for market in ("KR", "US"):
        subset = [row for row in route_rows if str(row.get("market") or "").upper() == market]
        plan_a = [row for row in subset if str(row.get("final_action") or "") in {"BUY_READY", "PROBE_READY"}]
        overextended_joined = []
        runtime_overextended = []
        for row in plan_a:
            runtime_gate = row.get("runtime_gate") if isinstance(row.get("runtime_gate"), dict) else {}
            if bool(runtime_gate.get("overextended")):
                runtime_overextended.append(row)
            key = (market, str(row.get("session_date") or ""), normalize_ticker(market, row.get("ticker")))
            sel = selection_index.get(key)
            if sel and str(sel.get("from_high_bucket") or "").lower() in {"at_high", "near_high"}:
                overextended_joined.append({**row, "_selection": sel})
        out[market] = {
            "route_rows": len(subset),
            "plan_a_ready_or_probe": len(plan_a),
            "runtime_gate_overextended": len(runtime_overextended),
            "selection_join_overextended": len(overextended_joined),
            "final_action_counts": dict(Counter(str(row.get("final_action") or "") for row in subset).most_common()),
            "would_watch_trigger_sample": [
                {
                    "date": row.get("session_date"),
                    "ticker": row.get("ticker"),
                    "action": row.get("final_action"),
                    "route": row.get("route"),
                    "reason": row.get("reason"),
                    "change_pct": round(value(row["_selection"], "change_pct"), 3),
                    "from_high_bucket": row["_selection"].get("from_high_bucket"),
                    "liquidity_bucket": row["_selection"].get("liquidity_bucket"),
                }
                for row in overextended_joined[:20]
            ],
        }
    return out


def preopen_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        from full_profitability_review import preopen_rule_table
    except Exception:
        return {}
    valid = [row for row in rows if row.get("valid_outcome")]
    out = {}
    for market in ("KR", "US"):
        subset = [row for row in valid if str(row.get("market") or "").upper() == market]
        rules = preopen_rule_table(subset, market)
        out[market] = {
            "valid_rows": len(subset),
            "all_final": metric_dict(value(row, "final_return_pct") for row in subset),
            "hard_pin_final": rules.get("current_hard_pin", {}).get("final", {}),
            "soft_b_naive_final": rules.get("soft_b_naive", {}).get("final", {}),
            "low_liq_ignite60_final": rules.get("low_liq_ignite60", {}).get("final", {}),
            "low_liq_ignite60_entry_60m_to_final": rules.get("low_liq_ignite60", {}).get("entry_60m_to_final", {}),
            "late_reclaim_watch_final": rules.get("late_reclaim_watch", {}).get("final", {}),
        }
    return out


def interpretation() -> list[dict[str, str]]:
    return [
        {
            "area": "observability",
            "read": "Prompt visibility must be fixed first. Existing screener_quality can overstate input_to_claude when select_tickers trims internally.",
            "action": "Persist actual prompt tickers and curation deferred_reason before changing live behavior.",
        },
        {
            "area": "WATCH_TRIGGER",
            "read": "Use routing-level demotion first. It preserves Claude output while preventing immediate high-zone execution.",
            "action": "Shadow BUY_READY/PROBE_READY -> WATCH_TRIGGER for at_high/near_high until OR/VWAP/volume confirmation exists.",
        },
        {
            "area": "candidate_pool",
            "read": "Cap expansion is a visibility change, not a buy permission change.",
            "action": "Raise KR overextended cap gradually and track score-ranked top30/top36 misses before enabling any extra execution.",
        },
        {
            "area": "low_liq",
            "read": "Low-liquidity ignition should be a separate small-probe path after confirmation, not a relaxation of Claude VETO.",
            "action": "Keep low_liq_ignite60 in shadow until sample size grows.",
        },
    ]


def by_strategy_metrics(rows: list[dict[str, Any]], value_key: str) -> dict[str, Any]:
    out = {}
    for strategy in sorted({str(row.get("strategy") or "") for row in rows}):
        subset = [row for row in rows if str(row.get("strategy") or "") == strategy]
        out[strategy or "(blank)"] = metric_dict(value(row, value_key) for row in subset)
    return out


def coverage_dates(rows: list[dict[str, Any]], date_key: str, market_key: str) -> dict[str, Any]:
    dates = []
    market_counts = Counter()
    for row in rows:
        d = row.get(date_key)
        if isinstance(d, datetime):
            dstr = d.date().isoformat()
        else:
            dstr = str(d or "")[:10]
        if dstr:
            dates.append(dstr)
        market = str(row.get(market_key) or "").upper() or "(blank)"
        market_counts[market] += 1
    return {
        "rows": len(rows),
        "date_min": min(dates) if dates else "",
        "date_max": max(dates) if dates else "",
        "by_market": dict(market_counts.most_common()),
    }


def metric(values: Iterable[float]) -> Metric:
    clean = [float(v) for v in values if math.isfinite(float(v))]
    wins = [v for v in clean if v > 0]
    losses = [v for v in clean if v < 0]
    pf: float | str | None
    pf = sum(wins) / abs(sum(losses)) if losses else ("inf" if wins else None)
    return Metric(
        n=len(clean),
        wins=len(wins),
        losses=len(losses),
        win_rate_pct=(len(wins) / len(clean) * 100 if clean else 0.0),
        avg_pct=(sum(clean) / len(clean) if clean else 0.0),
        median_pct=(median(clean) if clean else 0.0),
        sum_pct=sum(clean),
        profit_factor=pf,
        worst_pct=(min(clean) if clean else 0.0),
        best_pct=(max(clean) if clean else 0.0),
    )


def metric_dict(values: Iterable[float]) -> dict[str, Any]:
    m = metric(values)
    return {
        "n": m.n,
        "wins": m.wins,
        "losses": m.losses,
        "win_rate_pct": round(m.win_rate_pct, 2),
        "avg_pct": round(m.avg_pct, 4),
        "median_pct": round(m.median_pct, 4),
        "sum_pct": round(m.sum_pct, 4),
        "profit_factor": round(m.profit_factor, 4) if isinstance(m.profit_factor, float) else m.profit_factor,
        "worst_pct": round(m.worst_pct, 4),
        "best_pct": round(m.best_pct, 4),
    }


def compact_selection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "date": row.get("date"),
            "ticker": row.get("ticker"),
            "change_pct": round(value(row, "change_pct"), 3),
            "from_high_bucket": row.get("from_high_bucket"),
            "liquidity_bucket": row.get("liquidity_bucket"),
            "forward_1d": round(value(row, "forward_1d"), 3),
            "forward_3d": round(value(row, "forward_3d"), 3),
            "max_runup_3d": round(value(row, "max_runup_3d"), 3),
            "max_drawdown_3d": round(value(row, "max_drawdown_3d"), 3),
        }
        for row in rows
    ]


def compact_screener(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for idx, row in enumerate(rows, start=1):
        output.append(
            {
                "rank": idx,
                "ticker": row.get("ticker"),
                "status": row.get("status"),
                "score_current": round(value(row, "score_current"), 3),
                "change_rate": round(value(row, "change_rate"), 3),
                "turnover": round(value(row, "turnover"), 0),
                "primary_bucket": row.get("primary_bucket"),
            }
        )
    return output


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Candidate Improvement Simulation",
        "",
        f"- generated_at: {payload['generated_at']}",
        "- scope: local DB/log simulation only; no broker/API/Claude calls",
        "",
        "## Data Coverage",
        "",
        "| source | rows | date_min | date_max | by_market |",
        "|---|---:|---|---|---|",
    ]
    for name, data in payload["coverage"].items():
        lines.append(
            f"| {name} | {data.get('rows', 0)} | {data.get('date_min', '')} | {data.get('date_max', '')} | {json.dumps(data.get('by_market', {}), ensure_ascii=False)} |"
        )

    lines.extend(["", "## Long Backtest Gap Guard", ""])
    for market, data in payload["long_backtest_gap_guard"].items():
        lines.append(f"### {market}")
        base = data.get("baseline", {})
        lines.append(
            f"- baseline: n={base.get('n')} avg={base.get('avg_pct')}% win={base.get('win_rate_pct')}% pf={base.get('profit_factor')}"
        )
        lines.append("| scenario | kept n | kept avg | kept pf | demoted n | demoted avg | demoted pf |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for key, item in data.items():
            if not key.startswith("gap_guard_"):
                continue
            kept = item.get("kept", {})
            demoted = item.get("demoted", {})
            lines.append(
                f"| {key} | {kept.get('n', 0)} | {kept.get('avg_pct', 0)} | {kept.get('profit_factor')} | {demoted.get('n', 0)} | {demoted.get('avg_pct', 0)} | {demoted.get('profit_factor')} |"
            )
        lines.append("")

    lines.extend(["## Recent WATCH_TRIGGER Proxy", ""])
    for market, data in payload["recent_watch_trigger"].items():
        base = data.get("baseline_ready_forward_1d", {})
        lines.append(f"### {market}")
        lines.append(f"- ready forward_1d baseline: n={base.get('n')} avg={base.get('avg_pct')}% pf={base.get('profit_factor')}")
        lines.append("| scenario | kept n | kept f1 avg | demoted n | demoted f1 avg | demoted runup avg | demoted drawdown avg |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for key, item in data.items():
            if not key.startswith("watch_trigger_"):
                continue
            kept = item.get("kept_forward_1d", {})
            dem = item.get("demoted_forward_1d", {})
            run = item.get("demoted_max_runup_3d", {})
            dd = item.get("demoted_max_drawdown_3d", {})
            lines.append(
                f"| {key} | {kept.get('n', 0)} | {kept.get('avg_pct', 0)} | {dem.get('n', 0)} | {dem.get('avg_pct', 0)} | {run.get('avg_pct', 0)} | {dd.get('avg_pct', 0)} |"
            )
        lines.append("")

    lines.extend(["## Prompt Visibility", ""])
    lines.append("| market | matched events | avg raw rows | avg actual prompt | avg reported input_true | events missing top30 | avg missing top30 | avg score36 gain |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for market, data in payload["prompt_visibility"].items():
        lines.append(
            f"| {market} | {data.get('matched_selection_events', 0)} | {data.get('avg_raw_rows', 0)} | {data.get('avg_actual_prompt_count', 0)} | {data.get('avg_reported_input_true', 0)} | {data.get('events_with_missing_top30', 0)} | {data.get('avg_missing_top30', 0)} | {data.get('avg_score36_gain', 0)} |"
        )

    lines.extend(["", "## Routing Shadow", ""])
    for market, data in payload["action_routing_shadow"].items():
        lines.append(
            f"- {market}: route_rows={data.get('route_rows')} plan_a={data.get('plan_a_ready_or_probe')} runtime_overextended={data.get('runtime_gate_overextended')} selection_join_overextended={data.get('selection_join_overextended')}"
        )

    lines.extend(["", "## Preopen Low Liquidity", ""])
    for market, data in payload["preopen_low_liq"].items():
        low = data.get("low_liq_ignite60_final", {})
        late = data.get("late_reclaim_watch_final", {})
        lines.append(
            f"- {market}: valid={data.get('valid_rows')} low_liq_ignite60 n={low.get('n')} avg={low.get('avg_pct')}% pf={low.get('profit_factor')} late_reclaim n={late.get('n')} avg={late.get('avg_pct')}%"
        )

    lines.extend(["", "## Interpretation", ""])
    for item in payload["interpretation"]:
        lines.append(f"- {item['area']}: {item['read']} Action: {item['action']}")

    lines.extend(["", "## Limits", ""])
    for item in payload["limits"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def parse_json(text: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(text or "{}"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
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


def parse_call_ts(date_text: str, time_text: str) -> datetime:
    return datetime.strptime(date_text + time_text, "%Y%m%d%H%M%S")


def parse_dt(value_: Any) -> datetime | None:
    if isinstance(value_, datetime):
        return value_
    text = str(value_ or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        try:
            dt = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def normalize_ticker(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def value(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    raw = row.get(key)
    if raw is None or raw == "":
        return default
    try:
        val = float(raw)
    except Exception:
        return default
    return val if math.isfinite(val) else default


def is_finite(raw: Any) -> bool:
    try:
        return math.isfinite(float(raw))
    except Exception:
        return False


def avg(values: Iterable[float]) -> float:
    clean = [float(v) for v in values if is_finite(v)]
    return round(sum(clean) / len(clean), 4) if clean else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
