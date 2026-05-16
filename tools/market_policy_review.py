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
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
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
    parser = argparse.ArgumentParser(
        description="Review KR/US candidate quality and buy/sell policy options from local data only."
    )
    parser.add_argument("--output-dir", default=str(ROOT / "docs" / "reports"))
    parser.add_argument("--stamp", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    args = parser.parse_args()

    closed = load_closed(ROOT / "state" / "live_decisions.jsonl")
    live_events = load_live_events(ROOT / "state" / "live_decisions.jsonl")
    selection_rows = load_selection_rows(ROOT / "data" / "ticker_selection_log.db")
    audit_rows = load_audit_rows(ROOT / "data" / "audit" / "candidate_audit.db")

    payload = build_payload(closed, live_events, selection_rows, audit_rows)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"market_policy_review_{args.stamp}.json"
    md_path = output_dir / f"market_policy_review_{args.stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
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


def load_live_events(path: Path) -> list[dict[str, Any]]:
    rows = load_jsonl(path)
    for row in rows:
        row["_session_date"] = session_date(row)
    return rows


def load_closed(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in load_jsonl(path):
        if row.get("type") != "closed" or row.get("pnl_pct") is None:
            continue
        item = dict(row)
        item["_session_date"] = session_date(item)
        item["_pnl_pct"] = safe_float(item.get("pnl_pct"))
        item["_pnl_krw"] = safe_float(item.get("pnl_krw"))
        item["_mfe_pct"] = safe_float(
            item.get("position_mfe_pct")
            if item.get("position_mfe_pct") is not None
            else item.get("peak_pnl_pct")
        )
        item["_dt"] = parse_dt(item.get("timestamp"))
        rows.append(item)
    return sorted(rows, key=lambda row: row["_dt"])


def load_selection_rows(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM ticker_selection_log
                WHERE bot_mode='live'
                ORDER BY date, market, COALESCE(selected_at, signal_at, traded_at), ticker
                """
            )
        ]
    finally:
        conn.close()
    return rows


def load_audit_rows(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    r.candidate_key,
                    r.session_date,
                    r.market,
                    r.known_at,
                    r.ticker,
                    r.change_pct,
                    r.turnover,
                    r.liquidity_bucket,
                    r.primary_bucket,
                    r.claude_trade_ready,
                    r.recommended_strategy,
                    r.route_original_action,
                    r.route_final_action,
                    r.route_route,
                    r.route_reason,
                    r.route_demoted_to,
                    r.route_overextended,
                    r.filled_count,
                    r.pnl_pct AS audit_pnl_pct,
                    o30.return_pct AS ret30,
                    o30.max_runup_pct AS runup30,
                    o30.max_drawdown_pct AS drawdown30,
                    o60.return_pct AS ret60,
                    o60.max_runup_pct AS runup60,
                    o60.max_drawdown_pct AS drawdown60
                FROM audit_candidate_rows r
                LEFT JOIN audit_candidate_outcomes o30
                  ON r.candidate_key=o30.candidate_key AND o30.horizon_min=30
                LEFT JOIN audit_candidate_outcomes o60
                  ON r.candidate_key=o60.candidate_key AND o60.horizon_min=60
                WHERE r.runtime_mode='live'
                ORDER BY r.session_date, r.market, r.known_at, r.ticker
                """
            )
        ]
    finally:
        conn.close()
    return rows


def build_payload(
    closed: list[dict[str, Any]],
    live_events: list[dict[str, Any]],
    selection_rows: list[dict[str, Any]],
    audit_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    immediate = [row for row in audit_rows if row.get("route_final_action") in {"BUY_READY", "PROBE_READY"}]
    routed = [row for row in audit_rows if str(row.get("route_final_action") or "")]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "basis": {
            "mode": "local_logs_only_no_new_claude_no_broker_calls",
            "closed_trades": len(closed),
            "live_events": len(live_events),
            "selection_rows": len(selection_rows),
            "audit_rows": len(audit_rows),
            "routed_rows": len(routed),
            "immediate_buy_probe_rows": len(immediate),
            "date_ranges": {
                "closed": date_range([row.get("_session_date") for row in closed]),
                "selection": date_range([row.get("date") for row in selection_rows]),
                "audit": date_range([row.get("session_date") for row in audit_rows]),
                "routed_audit": date_range([row.get("session_date") for row in routed]),
            },
            "limits": [
                "No new Claude judgment was requested or used.",
                "Existing route/action fields are treated as persisted system logs, not as new reasoning.",
                "Forward returns and audit outcomes are evaluation labels, not live-safe gate inputs.",
                "Sell overlays are approximate because no intraday tick replay is available.",
            ],
        },
        "actual_live": actual_live_payload(closed, live_events),
        "candidate_quality": candidate_quality_payload(selection_rows),
        "action_routing_quality": action_routing_payload(audit_rows),
        "buy_policy_simulations": buy_policy_simulations(immediate),
        "selection_policy_simulations": selection_policy_simulations(selection_rows),
        "sell_policy_simulations": sell_policy_simulations(closed),
        "combined_policy_simulations": combined_policy_simulations(closed),
        "diagnosis": diagnosis(),
    }


def actual_live_payload(closed: list[dict[str, Any]], live_events: list[dict[str, Any]]) -> dict[str, Any]:
    strategy_closed = [row for row in closed if not is_broker_sync(row)]
    broker_sync_closed = [row for row in closed if is_broker_sync(row)]
    by_market = grouped_metrics(closed, lambda row: str(row.get("market") or ""), "_pnl_pct", pnl_key="_pnl_krw")
    by_date_market = grouped_metrics(
        closed,
        lambda row: f"{row.get('_session_date')}|{row.get('market')}",
        "_pnl_pct",
        pnl_key="_pnl_krw",
    )
    by_strategy = grouped_metrics(
        strategy_closed,
        lambda row: f"{row.get('market')}|{row.get('strategy') or '(blank)'}",
        "_pnl_pct",
        pnl_key="_pnl_krw",
    )
    by_exit = grouped_metrics(
        closed,
        lambda row: f"{row.get('market')}|{row.get('exit_reason') or '(blank)'}",
        "_pnl_pct",
        pnl_key="_pnl_krw",
    )
    order_counts: dict[str, Any] = {}
    for market in ("KR", "US"):
        subset = [row for row in live_events if str(row.get("market") or "") == market]
        order_counts[market] = {
            "entry": sum(1 for row in subset if row.get("type") == "entry"),
            "buy_failed": sum(1 for row in subset if row.get("type") == "buy_failed"),
            "closed": sum(1 for row in subset if row.get("type") == "closed"),
        }
    return {
        "by_market": by_market,
        "by_date_market": by_date_market,
        "by_strategy": by_strategy,
        "broker_sync_operational": grouped_metrics(
            broker_sync_closed,
            lambda row: f"{row.get('market')}|broker_sync",
            "_pnl_pct",
            pnl_key="_pnl_krw",
        ),
        "broker_sync_count": len(broker_sync_closed),
        "by_exit_reason": by_exit,
        "buy_order_counts": order_counts,
        "worst_trades": compact_closed(sorted(closed, key=lambda row: row["_pnl_pct"])[:15]),
        "best_trades": compact_closed(sorted(closed, key=lambda row: row["_pnl_pct"], reverse=True)[:15]),
    }


def is_broker_sync(row: dict[str, Any]) -> bool:
    strategy = str(row.get("strategy") or row.get("strategy_name") or "").strip().lower()
    reason = str(row.get("reason") or row.get("exit_reason") or "").strip().lower()
    return strategy in {"broker_sync", "broker_balance"} or "broker_sync" in reason


def candidate_quality_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def market_key(row: dict[str, Any]) -> str:
        return str(row.get("market") or "")

    def ready_key(row: dict[str, Any]) -> str:
        return f"{row.get('market')}|ready={int(row.get('trade_ready') or 0)}"

    def strategy_key(row: dict[str, Any]) -> str:
        strategy = row.get("strategy_name") or row.get("recommended_strategy") or "(blank)"
        return f"{row.get('market')}|ready={int(row.get('trade_ready') or 0)}|{strategy}"

    def bucket_key(row: dict[str, Any]) -> str:
        return (
            f"{row.get('market')}|liq={row.get('liquidity_bucket') or '(blank)'}|"
            f"high={row.get('from_high_bucket') or '(blank)'}"
        )

    traded = [row for row in rows if int(row.get("traded") or 0) == 1 and row.get("pnl_pct") is not None]
    return {
        "forward_1d_by_market": grouped_metrics(rows, market_key, "forward_1d"),
        "forward_1d_by_ready": grouped_metrics(rows, ready_key, "forward_1d"),
        "runup3_by_ready": grouped_metrics(rows, ready_key, "max_runup_3d"),
        "drawdown3_by_ready": grouped_metrics(rows, ready_key, "max_drawdown_3d"),
        "forward_1d_by_strategy_ready": top_groups(grouped_metrics(rows, strategy_key, "forward_1d"), limit=40),
        "forward_1d_by_bucket": top_groups(grouped_metrics(rows, bucket_key, "forward_1d"), limit=40),
        "actual_traded_by_market": grouped_metrics(traded, market_key, "pnl_pct"),
        "actual_traded_by_strategy_ready": top_groups(grouped_metrics(traded, strategy_key, "pnl_pct"), limit=40),
    }


def action_routing_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    routed = [row for row in rows if str(row.get("route_final_action") or "")]
    immediate = [row for row in rows if row.get("route_final_action") in {"BUY_READY", "PROBE_READY"}]

    def market_action(row: dict[str, Any]) -> str:
        return f"{row.get('market')}|{row.get('route_final_action') or '(blank)'}"

    def reason_key(row: dict[str, Any]) -> str:
        return f"{row.get('market')}|{row.get('route_final_action') or '(blank)'}|{row.get('route_reason') or '(blank)'}"

    def strategy_key(row: dict[str, Any]) -> str:
        return f"{row.get('market')}|{row.get('route_final_action') or '(blank)'}|{row.get('recommended_strategy') or '(blank)'}"

    def date_key(row: dict[str, Any]) -> str:
        return f"{row.get('session_date')}|{row.get('market')}|{row.get('route_final_action') or '(blank)'}"

    return {
        "final_action_counts": dict(Counter(str(row.get("route_final_action") or "(blank)") for row in routed).most_common()),
        "ret30_by_market_action": grouped_metrics(routed, market_action, "ret30"),
        "ret60_by_market_action": grouped_metrics(routed, market_action, "ret60"),
        "ret30_by_reason": top_groups(grouped_metrics(routed, reason_key, "ret30"), limit=50),
        "ret30_by_strategy": top_groups(grouped_metrics(immediate, strategy_key, "ret30"), limit=50),
        "ret30_by_date_action": grouped_metrics(routed, date_key, "ret30"),
        "immediate_rows": compact_audit(immediate[:80]),
    }


def buy_policy_simulations(immediate: list[dict[str, Any]]) -> list[dict[str, Any]]:
    policies: list[tuple[str, Callable[[dict[str, Any]], bool], str]] = [
        ("current_all_buy_probe", lambda row: True, "현재 최종 BUY_READY/PROBE_READY 전체"),
        ("us_only_kr_watch", lambda row: row.get("market") == "US", "KR 즉시진입을 전부 WATCH로 돌리는 보수안"),
        ("kr_only_reference", lambda row: row.get("market") == "KR", "KR 즉시진입만 떼어 본 손상 구간"),
        (
            "us_all_kr_buy_only",
            lambda row: row.get("market") == "US"
            or (row.get("market") == "KR" and row.get("route_final_action") == "BUY_READY"),
            "KR PROBE_READY만 차단",
        ),
        (
            "us_all_kr_high_liq_only",
            lambda row: row.get("market") == "US"
            or (row.get("market") == "KR" and str(row.get("liquidity_bucket") or "") == "high"),
            "KR은 high liquidity만 즉시진입",
        ),
        (
            "us_all_kr_gap_pullback_only",
            lambda row: row.get("market") == "US"
            or (row.get("market") == "KR" and str(row.get("recommended_strategy") or "") == "gap_pullback"),
            "KR은 gap_pullback만 즉시진입",
        ),
        (
            "us_all_kr_change_le_10",
            lambda row: row.get("market") == "US"
            or (row.get("market") == "KR" and safe_float(row.get("change_pct")) <= 10.0),
            "KR 급등률 10% 이하만 즉시진입",
        ),
        (
            "us_all_kr_change_le_5",
            lambda row: row.get("market") == "US"
            or (row.get("market") == "KR" and safe_float(row.get("change_pct")) <= 5.0),
            "KR 급등률 5% 이하만 즉시진입",
        ),
        (
            "us_buy_ready_only",
            lambda row: row.get("market") == "US" and row.get("route_final_action") == "BUY_READY",
            "US도 BUY_READY만 두고 PROBE_READY 차단",
        ),
        (
            "us_probe_ready_only",
            lambda row: row.get("market") == "US" and row.get("route_final_action") == "PROBE_READY",
            "US PROBE_READY만 참고",
        ),
    ]
    out = []
    baseline_events = len(immediate)
    for name, pred, description in policies:
        kept = [row for row in immediate if pred(row)]
        removed = [row for row in immediate if not pred(row)]
        out.append(
            {
                "name": name,
                "description": description,
                "kept_events": len(kept),
                "removed_events": len(removed),
                "event_reduction_pct": pct_change(baseline_events, len(kept)),
                "kept_ret30": metric_dict(value(row, "ret30") for row in kept),
                "kept_ret60": metric_dict(value(row, "ret60") for row in kept),
                "removed_ret30": metric_dict(value(row, "ret30") for row in removed),
                "removed_ret60": metric_dict(value(row, "ret60") for row in removed),
                "kept_by_market": dict(Counter(str(row.get("market") or "") for row in kept).most_common()),
            }
        )
    return out


def selection_policy_simulations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    traded = [row for row in rows if int(row.get("traded") or 0) == 1 and row.get("pnl_pct") is not None]

    def strategy(row: dict[str, Any]) -> str:
        return str(row.get("strategy_name") or row.get("recommended_strategy") or "")

    policies: list[tuple[str, Callable[[dict[str, Any]], bool]]] = [
        ("selection_traded_baseline", lambda row: True),
        ("trade_ready_only", lambda row: int(row.get("trade_ready") or 0) == 1),
        ("us_only", lambda row: row.get("market") == "US"),
        ("us_ready_only", lambda row: row.get("market") == "US" and int(row.get("trade_ready") or 0) == 1),
        ("kr_ready_only", lambda row: row.get("market") == "KR" and int(row.get("trade_ready") or 0) == 1),
        ("block_kr_momentum", lambda row: not (row.get("market") == "KR" and strategy(row) == "momentum")),
        ("block_kr_all", lambda row: row.get("market") != "KR"),
    ]
    out = []
    for name, pred in policies:
        kept = [row for row in traded if pred(row)]
        removed = [row for row in traded if not pred(row)]
        out.append(
            {
                "name": name,
                "kept": len(kept),
                "removed": len(removed),
                "kept_metrics": metric_dict(value(row, "pnl_pct") for row in kept),
                "removed_metrics": metric_dict(value(row, "pnl_pct") for row in removed),
                "kept_by_market": dict(Counter(str(row.get("market") or "") for row in kept).most_common()),
            }
        )
    for cap in (1, 2, 3):
        kept = first_n_by_day(traded, cap, by_market=False)
        out.append(
            {
                "name": f"max_total_daily_entries_{cap}",
                "kept": len(kept),
                "removed": len(traded) - len(kept),
                "kept_metrics": metric_dict(value(row, "pnl_pct") for row in kept),
                "removed_metrics": metric_dict(value(row, "pnl_pct") for row in traded if row not in kept),
                "kept_by_market": dict(Counter(str(row.get("market") or "") for row in kept).most_common()),
            }
        )
    for cap in (1, 2, 3):
        kept = first_n_by_day(traded, cap, by_market=True)
        out.append(
            {
                "name": f"max_market_daily_entries_{cap}",
                "kept": len(kept),
                "removed": len(traded) - len(kept),
                "kept_metrics": metric_dict(value(row, "pnl_pct") for row in kept),
                "removed_metrics": metric_dict(value(row, "pnl_pct") for row in traded if row not in kept),
                "kept_by_market": dict(Counter(str(row.get("market") or "") for row in kept).most_common()),
            }
        )
    return out


def sell_policy_simulations(closed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scenarios: list[tuple[str, Callable[[dict[str, Any]], float], str]] = [
        ("baseline", lambda row: safe_float(row.get("pnl_pct")), "실제 closed 수익률"),
        ("loss_cap_3_only", lambda row: apply_loss_cap(row, 3.0), "손실 -3% 클립"),
        ("loss_cap_2_only", lambda row: apply_loss_cap(row, 2.0), "손실 -2% 클립"),
        ("loss_cap_1_5_only", lambda row: apply_loss_cap(row, 1.5), "손실 -1.5% 클립"),
        ("current_code_cap3_floor0_5_at_mfe2", apply_current_code, "현재 코드형 cap3 + MFE>=2% floor +0.5%"),
        (
            "proposed_global_cap2_mfe",
            lambda row: apply_mfe_protection(row, cap_pct=2.0, preserve_ratio=0.45, breakeven_floor=0.0),
            "글로벌 cap2 + MFE 보존",
        ),
        (
            "split_kr_cap2_mfe_us_current",
            lambda row: apply_mfe_protection(row, cap_pct=2.0, preserve_ratio=0.45, breakeven_floor=0.0)
            if row.get("market") == "KR"
            else apply_current_code(row),
            "KR만 cap2+MFE, US는 current 유지",
        ),
        (
            "split_kr_cap1_5_mfe_us_current",
            lambda row: apply_mfe_protection(row, cap_pct=1.5, preserve_ratio=0.5, breakeven_floor=0.1)
            if row.get("market") == "KR"
            else apply_current_code(row),
            "KR만 cap1.5+MFE, US는 current 유지",
        ),
    ]
    out = []
    for name, func, description in scenarios:
        out.append(
            {
                "name": name,
                "description": description,
                "all": closed_metrics(closed, func),
                "KR": closed_metrics([row for row in closed if row.get("market") == "KR"], func),
                "US": closed_metrics([row for row in closed if row.get("market") == "US"], func),
            }
        )
    return out


def combined_policy_simulations(closed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buy_filters: list[tuple[str, Callable[[dict[str, Any]], bool], str]] = [
        ("actual_all", lambda row: True, "실제 전체"),
        ("us_only", lambda row: row.get("market") == "US", "KR 신규 진입 중단 가정"),
        ("us_plus_kr_momentum_only", lambda row: row.get("market") == "US" or (row.get("market") == "KR" and row.get("strategy") == "momentum"), "KR은 momentum만 허용"),
        ("us_plus_kr_no_claude_price", lambda row: not (row.get("market") == "KR" and row.get("strategy") == "claude_price"), "KR claude_price 경로 제외"),
        ("drop_kr_continuation_claude_price", lambda row: not (row.get("market") == "KR" and row.get("strategy") in {"continuation", "claude_price"}), "KR continuation/claude_price 제외"),
    ]
    sell_overlays: list[tuple[str, Callable[[dict[str, Any]], float]]] = [
        ("baseline_sell", lambda row: safe_float(row.get("pnl_pct"))),
        ("current_sell", apply_current_code),
        (
            "split_kr_cap2_mfe_us_current",
            lambda row: apply_mfe_protection(row, cap_pct=2.0, preserve_ratio=0.45, breakeven_floor=0.0)
            if row.get("market") == "KR"
            else apply_current_code(row),
        ),
    ]
    out = []
    baseline_n = len(closed)
    for filter_name, pred, description in buy_filters:
        kept = [row for row in closed if pred(row)]
        for sell_name, overlay in sell_overlays:
            out.append(
                {
                    "name": f"{filter_name}+{sell_name}",
                    "buy_filter": description,
                    "kept_trades": len(kept),
                    "removed_trades": baseline_n - len(kept),
                    "metrics": closed_metrics(kept, overlay),
                    "kept_by_market": dict(Counter(str(row.get("market") or "") for row in kept).most_common()),
                }
            )
    return out


def grouped_metrics(
    rows: list[dict[str, Any]],
    key_func: Callable[[dict[str, Any]], str],
    value_key: str,
    *,
    pnl_key: str | None = None,
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = key_func(row)
        if key:
            groups[key].append(row)
    out: dict[str, Any] = {}
    for key in sorted(groups):
        vals = [value(row, value_key) for row in groups[key]]
        item = metric_dict(vals)
        item["events"] = len(groups[key])
        if pnl_key:
            item["pnl_krw"] = round(sum(safe_float(row.get(pnl_key)) for row in groups[key]), 0)
        out[key] = item
    return out


def closed_metrics(rows: list[dict[str, Any]], transform: Callable[[dict[str, Any]], float]) -> dict[str, Any]:
    values = [transform(row) for row in rows]
    pnl_krw = sum(estimate_krw(row, transform(row)) for row in rows)
    out = metric_dict(values)
    out["pnl_krw"] = round(pnl_krw, 0)
    return out


def metric(values: Iterable[float]) -> Metrics:
    clean = [float(v) for v in values if math.isfinite(float(v))]
    wins = [v for v in clean if v > 0]
    losses = [v for v in clean if v <= 0]
    loss_sum = sum(v for v in clean if v < 0)
    profit_factor: float | str | None
    profit_factor = sum(wins) / abs(loss_sum) if loss_sum else ("inf" if wins else None)
    return Metrics(
        n=len(clean),
        wins=len(wins),
        losses=len(losses),
        win_rate_pct=(len(wins) / len(clean) * 100.0 if clean else 0.0),
        avg_pct=(sum(clean) / len(clean) if clean else 0.0),
        median_pct=(median(clean) if clean else 0.0),
        sum_pct=sum(clean),
        profit_factor=profit_factor,
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


def first_n_by_day(rows: list[dict[str, Any]], n: int, *, by_market: bool) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (str(row.get("date") or ""), str(row.get("market") or "")) if by_market else (str(row.get("date") or ""),)
        groups[key].append(row)
    selected: list[dict[str, Any]] = []
    for group in groups.values():
        selected.extend(
            sorted(
                group,
                key=lambda row: parse_dt(row.get("traded_at") or row.get("signal_at") or row.get("selected_at")),
            )[:n]
        )
    return selected


def top_groups(groups: dict[str, Any], *, limit: int) -> dict[str, Any]:
    return dict(
        sorted(groups.items(), key=lambda item: (item[1].get("events", item[1].get("n", 0)), item[0]), reverse=True)[
            :limit
        ]
    )


def apply_loss_cap(row: dict[str, Any], cap_pct: float) -> float:
    return max(safe_float(row.get("pnl_pct")), -abs(float(cap_pct)))


def apply_current_code(row: dict[str, Any]) -> float:
    pct = apply_loss_cap(row, 3.0)
    if safe_float(row.get("_mfe_pct")) >= 2.0:
        pct = max(pct, 0.5)
    return pct


def apply_mfe_protection(
    row: dict[str, Any],
    *,
    cap_pct: float,
    preserve_ratio: float,
    breakeven_floor: float,
) -> float:
    pct = apply_loss_cap(row, cap_pct)
    mfe = safe_float(row.get("_mfe_pct"))
    if mfe >= 2.0:
        return max(pct, preserve_ratio * mfe)
    if mfe >= 1.0:
        return max(pct, breakeven_floor)
    return pct


def estimate_krw(row: dict[str, Any], simulated_pct: float) -> float:
    realized_pct = safe_float(row.get("pnl_pct"))
    realized_krw = safe_float(row.get("pnl_krw"))
    if abs(realized_pct) <= 1e-9:
        return realized_krw
    return realized_krw * simulated_pct / realized_pct


def compact_closed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "date": row.get("_session_date"),
            "timestamp": row.get("timestamp"),
            "market": row.get("market"),
            "ticker": row.get("ticker"),
            "strategy": row.get("strategy"),
            "exit_reason": row.get("exit_reason"),
            "pnl_pct": round(safe_float(row.get("pnl_pct")), 4),
            "pnl_krw": round(safe_float(row.get("pnl_krw")), 0),
            "mfe_pct": round(safe_float(row.get("_mfe_pct")), 4),
        }
        for row in rows
    ]


def compact_audit(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "date": row.get("session_date"),
            "market": row.get("market"),
            "ticker": row.get("ticker"),
            "action": row.get("route_final_action"),
            "reason": row.get("route_reason"),
            "strategy": row.get("recommended_strategy"),
            "liquidity": row.get("liquidity_bucket"),
            "bucket": row.get("primary_bucket"),
            "change_pct": round(safe_float(row.get("change_pct")), 3),
            "ret30": round(value(row, "ret30"), 4) if row.get("ret30") is not None else None,
            "ret60": round(value(row, "ret60"), 4) if row.get("ret60") is not None else None,
        }
        for row in rows
    ]


def diagnosis() -> list[dict[str, str]]:
    return [
        {
            "area": "KR candidate timing",
            "finding": "KR selected candidates have large runup potential but also much larger drawdown. The current immediate route captures early adverse movement.",
            "action": "Treat KR BUY_READY/PROBE_READY as watch/confirmation by default until OR/VWAP/volume confirmation exists.",
        },
        {
            "area": "Market split",
            "finding": "US is close to breakeven before overlays and positive under current sell overlay; KR remains negative even after sell overlay.",
            "action": "Keep US policy independent. Do not let KR risk controls reduce US throughput unless US metrics deteriorate.",
        },
        {
            "area": "KR sell control",
            "finding": "KR loss tails dominate realized PnL. Loss-cap and MFE preservation address the tail but do not fix entry quality.",
            "action": "Use tighter KR cap/MFE preservation only with order replay or shadow-first monitoring.",
        },
        {
            "area": "Policy shape",
            "finding": "The data supports routing/demotion and market-specific sizing more than adding more global thresholds.",
            "action": "Implement KR route demotion, KR size cap, and per-market promotion/demotion dashboards before expanding entries.",
        },
    ]


def to_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Market Policy Review",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Basis",
        "",
        f"- Mode: {payload['basis']['mode']}",
        f"- Closed trades: {payload['basis']['closed_trades']}",
        f"- Selection rows: {payload['basis']['selection_rows']}",
        f"- Audit rows: {payload['basis']['audit_rows']}",
        f"- Routed audit window: {payload['basis']['date_ranges']['routed_audit']}",
        "- No new Claude judgment, broker call, or API call was made.",
        "",
        "Limits:",
    ]
    lines.extend(f"- {item}" for item in payload["basis"]["limits"])

    lines.extend(["", "## Executive Read", ""])
    lines.extend(
        [
            "- KR is not simply a bad universe problem. KR candidates show higher runup but much worse drawdown and very poor immediate 30/60m outcomes.",
            "- US is near breakeven before sell overlays and turns positive under the current sell overlay. KR remains the drag.",
            "- The strongest buy-side improvement is KR immediate-entry demotion, not a new global threshold.",
            "- Sell overlays reduce the left tail, but entry timing still determines whether the strategy can turn structurally positive.",
        ]
    )

    lines.extend(["", "## Actual Live Result", ""])
    lines.extend(metrics_table("By Market", payload["actual_live"]["by_market"], include_pnl=True))
    lines.extend(metrics_table("By Strategy", payload["actual_live"]["by_strategy"], include_pnl=True, limit=30))
    lines.extend(metrics_table("Broker Sync Operational Cases", payload["actual_live"].get("broker_sync_operational", {}), include_pnl=True, limit=20))
    lines.extend(metrics_table("By Exit Reason", payload["actual_live"]["by_exit_reason"], include_pnl=True, limit=30))
    lines.append("")
    lines.append("### Buy Order Counts")
    lines.append("| Market | Entry | Buy failed | Closed |")
    lines.append("|---|---:|---:|---:|")
    for market, item in payload["actual_live"]["buy_order_counts"].items():
        lines.append(f"| {market} | {item['entry']} | {item['buy_failed']} | {item['closed']} |")

    lines.extend(["", "## Candidate Quality", ""])
    lines.extend(metrics_table("Forward 1D By Market", payload["candidate_quality"]["forward_1d_by_market"]))
    lines.extend(metrics_table("Forward 1D By Ready", payload["candidate_quality"]["forward_1d_by_ready"]))
    lines.extend(metrics_table("Runup 3D By Ready", payload["candidate_quality"]["runup3_by_ready"]))
    lines.extend(metrics_table("Drawdown 3D By Ready", payload["candidate_quality"]["drawdown3_by_ready"]))
    lines.extend(metrics_table("Actual Traded By Market", payload["candidate_quality"]["actual_traded_by_market"]))

    lines.extend(["", "## Action Routing Quality", ""])
    lines.extend(metrics_table("30m By Market/Action", payload["action_routing_quality"]["ret30_by_market_action"]))
    lines.extend(metrics_table("60m By Market/Action", payload["action_routing_quality"]["ret60_by_market_action"]))
    lines.extend(metrics_table("30m By Immediate Strategy", payload["action_routing_quality"]["ret30_by_strategy"], limit=25))

    lines.extend(["", "## Buy Policy Simulations", ""])
    lines.append("| Policy | Kept | Removed | Kept Markets | 30m W/L | 30m Avg | 30m Sum | 60m W/L | 60m Avg | Removed 30m Avg |")
    lines.append("|---|---:|---:|---|---:|---:|---:|---:|---:|---:|")
    for row in payload["buy_policy_simulations"]:
        r30 = row["kept_ret30"]
        r60 = row["kept_ret60"]
        rem30 = row["removed_ret30"]
        lines.append(
            f"| {row['name']} | {row['kept_events']} | {row['removed_events']} | "
            f"{json.dumps(row['kept_by_market'], ensure_ascii=False)} | {r30['wins']}/{r30['losses']} | "
            f"{fmt_pct(r30['avg_pct'])} | {fmt_pct(r30['sum_pct'])} | {r60['wins']}/{r60['losses']} | "
            f"{fmt_pct(r60['avg_pct'])} | {fmt_pct(rem30['avg_pct']) if rem30['n'] else 'NA'} |"
        )

    lines.extend(["", "## Selection Policy Simulations", ""])
    lines.append("| Policy | Kept | Removed | Markets | W/L | Avg | Sum | PF | Removed Avg |")
    lines.append("|---|---:|---:|---|---:|---:|---:|---:|---:|")
    for row in payload["selection_policy_simulations"]:
        m = row["kept_metrics"]
        rem = row["removed_metrics"]
        lines.append(
            f"| {row['name']} | {row['kept']} | {row['removed']} | {json.dumps(row['kept_by_market'], ensure_ascii=False)} | "
            f"{m['wins']}/{m['losses']} | {fmt_pct(m['avg_pct'])} | {fmt_pct(m['sum_pct'])} | {fmt_pf(m['profit_factor'])} | "
            f"{fmt_pct(rem['avg_pct']) if rem['n'] else 'NA'} |"
        )

    lines.extend(["", "## Sell Policy Simulations", ""])
    lines.append("| Scenario | All W/L | All Avg | All Sum | All PnL | KR Avg | KR PnL | US Avg | US PnL |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in payload["sell_policy_simulations"]:
        all_m = row["all"]
        kr = row["KR"]
        us = row["US"]
        lines.append(
            f"| {row['name']} | {all_m['wins']}/{all_m['losses']} | {fmt_pct(all_m['avg_pct'])} | "
            f"{fmt_pct(all_m['sum_pct'])} | {fmt_krw(all_m.get('pnl_krw'))} | "
            f"{fmt_pct(kr['avg_pct'])} | {fmt_krw(kr.get('pnl_krw'))} | {fmt_pct(us['avg_pct'])} | {fmt_krw(us.get('pnl_krw'))} |"
        )

    lines.extend(["", "## Combined Policy Simulations", ""])
    lines.append("| Scenario | Kept | Removed | Markets | W/L | Avg | Sum | PnL | PF |")
    lines.append("|---|---:|---:|---|---:|---:|---:|---:|---:|")
    for row in payload["combined_policy_simulations"]:
        m = row["metrics"]
        lines.append(
            f"| {row['name']} | {row['kept_trades']} | {row['removed_trades']} | {json.dumps(row['kept_by_market'], ensure_ascii=False)} | "
            f"{m['wins']}/{m['losses']} | {fmt_pct(m['avg_pct'])} | {fmt_pct(m['sum_pct'])} | {fmt_krw(m.get('pnl_krw'))} | {fmt_pf(m['profit_factor'])} |"
        )

    lines.extend(["", "## Diagnosis And Improvement Points", ""])
    for item in payload["diagnosis"]:
        lines.append(f"- {item['area']}: {item['finding']} Action: {item['action']}")
    lines.append("")
    return "\n".join(lines)


def metrics_table(title: str, groups: dict[str, Any], *, include_pnl: bool = False, limit: int | None = None) -> list[str]:
    items = list(groups.items())
    if limit is not None:
        items = items[:limit]
    lines = ["", f"### {title}"]
    if include_pnl:
        lines.append("| Group | Events | N | W/L | Win | Avg | Sum | PF | Worst | Best | PnL |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    else:
        lines.append("| Group | Events | N | W/L | Win | Avg | Sum | PF | Worst | Best |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for key, m in items:
        base = (
            f"| {key} | {m.get('events', m.get('n', 0))} | {m['n']} | {m['wins']}/{m['losses']} | "
            f"{m['win_rate_pct']:.1f}% | {fmt_pct(m['avg_pct'])} | {fmt_pct(m['sum_pct'])} | "
            f"{fmt_pf(m['profit_factor'])} | {fmt_pct(m['worst_pct'])} | {fmt_pct(m['best_pct'])}"
        )
        if include_pnl:
            base += f" | {fmt_krw(m.get('pnl_krw'))} |"
        else:
            base += " |"
        lines.append(base)
    return lines


def value(row: dict[str, Any], key: str) -> float:
    raw = row.get(key)
    if raw is None or raw == "":
        return math.nan
    return safe_float(raw)


def safe_float(value_: Any) -> float:
    try:
        parsed = float(value_)
        return parsed if math.isfinite(parsed) else 0.0
    except (TypeError, ValueError):
        return 0.0


def parse_dt(value_: Any) -> datetime:
    if not value_:
        return datetime.min
    text = str(value_)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromisoformat(text.split("+")[0])
        except ValueError:
            return datetime.min


def session_date(row: dict[str, Any]) -> str:
    return str(row.get("session_date") or str(row.get("timestamp") or "")[:10])


def date_range(values: Iterable[Any]) -> dict[str, str]:
    clean = sorted({str(value)[:10] for value in values if str(value or "")[:10]})
    return {"min": clean[0] if clean else "", "max": clean[-1] if clean else "", "days": len(clean)}


def pct_change(before: int, after: int) -> float:
    if before == 0:
        return 0.0
    return round((after - before) / before * 100.0, 2)


def fmt_pct(value_: Any) -> str:
    return f"{safe_float(value_):+.3f}%"


def fmt_krw(value_: Any) -> str:
    return f"{safe_float(value_):+,.0f}"


def fmt_pf(value_: Any) -> str:
    if value_ is None:
        return "NA"
    if value_ == "inf":
        return "inf"
    return f"{safe_float(value_):.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
