from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import csv
import json
import math

from config.v2 import DEFAULT_V2_CONFIG


GROUP_DIMENSIONS: tuple[tuple[str, ...], ...] = (
    ("market", "strategy"),
    ("market", "strategy", "entry_timing"),
    ("market", "strategy", "entry_timing", "entry_day_exit_policy"),
    ("market", "strategy", "mode"),
    ("market", "strategy", "entry_timing", "mode"),
    ("market", "strategy", "entry_timing", "entry_day_exit_policy", "mode"),
)


@dataclass(frozen=True)
class OptimizerConfig:
    min_trades: int = 60
    min_validation_trades: int = 20
    validation_ratio: float = 0.3
    min_validation_pf: float = 1.05
    min_validation_avg_pct: float = 0.02
    min_positive_source_ratio: float = 0.45
    usd_krw: float = 1400.0


def build_policy_optimization_report(
    root: str | Path,
    output_dir: str | Path | None = None,
    *,
    config: OptimizerConfig | None = None,
) -> dict[str, str]:
    root_path = Path(root)
    cfg = config or OptimizerConfig()
    trades = load_audit_trades(root_path)
    candidates = evaluate_candidates(trades, cfg)
    accepted = [row for row in candidates if row["accepted"]]
    ranked = sorted(candidates, key=lambda row: row["score"], reverse=True)
    ranked_accepted = sorted(accepted, key=lambda row: row["score"], reverse=True)
    production_candidates = [row for row in ranked_accepted if row.get("production_supported")]
    top_research = ranked_accepted[0] if ranked_accepted else (ranked[0] if ranked else None)
    top_production = production_candidates[0] if production_candidates else None
    selected_for_sizing = top_production or top_research
    sizing_grid = optimize_sizing(trades, selected_for_sizing, cfg) if selected_for_sizing else []
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "basis": "audit_trades walk-forward policy optimization; no Claude calls; no live config mutation",
        "config": cfg.__dict__,
        "trade_rows": len(trades),
        "source_count": len({trade["source"] for trade in trades}),
        "candidate_count": len(candidates),
        "accepted_count": len(ranked_accepted),
        "top_candidates": ranked[:30],
        "accepted_candidates": ranked_accepted[:30],
        "sizing_grid": sizing_grid[:30],
        "recommendation": build_recommendation(top_research, top_production, sizing_grid),
        "limits": [
            "This optimizer selects operating filters over existing strategy artifacts; it does not create a new strategy.",
            "It uses train/validation splits inside each candidate, but repeated backtest runs can still correlate.",
            "Sizing recommendations must be promoted manually after live CLEAN evidence; no automatic config change is made.",
        ],
    }
    out_dir = Path(output_dir) if output_dir else root_path / "data" / "v2_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"v2_policy_optimization_{stamp}.json"
    md_path = out_dir / f"v2_policy_optimization_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(to_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def load_audit_trades(root: str | Path) -> list[dict[str, Any]]:
    root_path = Path(root)
    rows: list[dict[str, Any]] = []
    for path in sorted((root_path / "data" / "backtest_audit" / "runs").rglob("audit_trades_*.csv")):
        for row in _read_csv(path):
            trade = _normalize_trade(path, row)
            if trade.get("net_pnl_pct") is not None:
                rows.append(trade)
    return rows


def evaluate_candidates(trades: list[dict[str, Any]], cfg: OptimizerConfig) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for dims in GROUP_DIMENSIONS:
        grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for trade in trades:
            key = tuple(str(trade.get(dim, "") or "") for dim in dims)
            if any(part == "" for part in key[:2]):
                continue
            grouped[key].append(trade)
        for key, group in grouped.items():
            if len(group) < cfg.min_trades:
                continue
            group_sorted = sorted(group, key=_trade_sort_key)
            split_idx = max(1, int(len(group_sorted) * (1.0 - cfg.validation_ratio)))
            train = group_sorted[:split_idx]
            validation = group_sorted[split_idx:]
            if len(validation) < cfg.min_validation_trades:
                continue
            all_metrics = metrics(group_sorted, cfg)
            train_metrics = metrics(train, cfg)
            validation_metrics = metrics(validation, cfg)
            source_stability = _source_stability(group_sorted)
            accepted = _accepted(train_metrics, validation_metrics, source_stability, cfg)
            candidate = {
                "dimensions": list(dims),
                "key": list(key),
                "filter": dict(zip(dims, key)),
                "production_supported": _production_supported(dict(zip(dims, key))),
                "accepted": accepted,
                "score": _score(train_metrics, validation_metrics, source_stability),
                "all": all_metrics,
                "train": train_metrics,
                "validation": validation_metrics,
                "source_stability": source_stability,
            }
            candidates.append(candidate)
    return candidates


def optimize_sizing(
    trades: list[dict[str, Any]],
    candidate: dict[str, Any] | None,
    cfg: OptimizerConfig,
) -> list[dict[str, Any]]:
    if not candidate:
        return []
    selected = _filter_trades(trades, candidate.get("filter") or {})
    if not selected:
        return []
    markets = sorted({str(trade.get("market") or "") for trade in selected if trade.get("market")})
    kr_budgets = [50_000, 100_000, 150_000, 200_000, 300_000]
    us_budgets = [30, 50, 75, 100, 150]
    max_positions = [1, 2, 3]
    daily_loss_limits = [-1.0, -2.0, -3.0]
    rows: list[dict[str, Any]] = []
    for max_pos in max_positions:
        for daily_loss_limit_pct in daily_loss_limits:
            if markets == ["US"]:
                for usd_budget in us_budgets:
                    rows.append(
                        _simulate_sizing(
                            selected,
                            kr_budget=DEFAULT_V2_CONFIG.kr_fixed_order_krw,
                            us_budget_usd=usd_budget,
                            max_positions=max_pos,
                            daily_loss_limit_pct=daily_loss_limit_pct,
                            cfg=cfg,
                        )
                    )
            elif markets == ["KR"]:
                for kr_budget in kr_budgets:
                    rows.append(
                        _simulate_sizing(
                            selected,
                            kr_budget=kr_budget,
                            us_budget_usd=DEFAULT_V2_CONFIG.us_fixed_order_usd,
                            max_positions=max_pos,
                            daily_loss_limit_pct=daily_loss_limit_pct,
                            cfg=cfg,
                        )
                    )
            else:
                for kr_budget in kr_budgets:
                    for usd_budget in us_budgets:
                        rows.append(
                            _simulate_sizing(
                                selected,
                                kr_budget=kr_budget,
                                us_budget_usd=usd_budget,
                                max_positions=max_pos,
                                daily_loss_limit_pct=daily_loss_limit_pct,
                                cfg=cfg,
                            )
                        )
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def build_recommendation(
    top_research: dict[str, Any] | None,
    top_production: dict[str, Any] | None,
    sizing_grid: list[dict[str, Any]],
) -> dict[str, Any]:
    if not top_research:
        return {
            "status": "NO_ACCEPTED_POLICY",
            "message": "No candidate passed validation thresholds. Keep V2 in shadow/min-size mode.",
        }
    top = top_production or top_research
    best_size = sizing_grid[0] if sizing_grid else {}
    validation = top.get("validation") or {}
    status = "RESEARCH_ONLY_TIMING_UNSUPPORTED"
    if top_production is not None:
        status = "PAPER_OR_MIN_SIZE_LIVE"
    if (
        top_production is not None
        and
        validation.get("profit_factor") is not None
        and validation.get("profit_factor") >= 1.15
        and validation.get("avg_net_pnl_pct", 0) >= 0.10
        and (top.get("source_stability") or {}).get("positive_source_ratio", 0) >= 0.6
    ):
        status = "MIN_SIZE_LIVE_CANDIDATE"
    if status == "MIN_SIZE_LIVE_CANDIDATE":
        suggested_sizing = {
            "kr_fixed_order_krw": best_size.get("kr_fixed_order_krw"),
            "us_fixed_order_usd": best_size.get("us_fixed_order_usd"),
            "max_positions": best_size.get("max_positions"),
            "daily_loss_limit_pct": best_size.get("daily_loss_limit_pct"),
        }
    else:
        suggested_sizing = {
            "kr_fixed_order_krw": 50_000,
            "us_fixed_order_usd": 30,
            "max_positions": 1,
            "daily_loss_limit_pct": DEFAULT_V2_CONFIG.daily_loss_limit_pct,
        }
    return {
        "status": status,
        "best_research_filter": top_research.get("filter"),
        "best_research_validation_profit_factor": (top_research.get("validation") or {}).get("profit_factor"),
        "best_research_validation_avg_net_pnl_pct": (top_research.get("validation") or {}).get("avg_net_pnl_pct"),
        "production_policy_filter": top_production.get("filter") if top_production else None,
        "validation_profit_factor": validation.get("profit_factor"),
        "validation_avg_net_pnl_pct": validation.get("avg_net_pnl_pct"),
        "positive_source_ratio": (top.get("source_stability") or {}).get("positive_source_ratio"),
        "source_count": (top.get("source_stability") or {}).get("source_count"),
        "suggested_sizing": suggested_sizing,
        "promotion_rule": "Only move above min-size after live CLEAN >= 20 trades, PF >= 1.15, avg net pnl >= 0.10%, and no unresolved ORDER_UNKNOWN.",
        "operation": [
            "Run this optimizer after every daily review, but never auto-apply changes.",
            "Promote only filters with live CLEAN evidence and at least 20 new trades.",
            "Treat same_close and blank timing rows as research-only until a matching V2 Timing Adapter is implemented.",
            "Demote immediately if validation-like live PF drops below 1.0 or ORDER_UNKNOWN appears.",
        ],
    }


def metrics(trades: list[dict[str, Any]], cfg: OptimizerConfig) -> dict[str, Any]:
    pnl_pct = [float(trade["net_pnl_pct"]) for trade in trades if trade.get("net_pnl_pct") is not None]
    if not pnl_pct:
        return _empty_metrics()
    wins = [x for x in pnl_pct if x > 0]
    losses = [x for x in pnl_pct if x < 0]
    budgets = [_budget_krw(str(trade.get("market") or ""), cfg) for trade in trades]
    pnl_krw = [budget * pct / 100.0 for budget, pct in zip(budgets, pnl_pct)]
    markets = sorted({str(trade.get("market") or "") for trade in trades if trade.get("market")})
    risk_capital = _risk_capital_krw(markets, cfg, max_positions=3)
    return {
        "n_trades": len(pnl_pct),
        "avg_net_pnl_pct": round(sum(pnl_pct) / len(pnl_pct), 6),
        "win_rate": round(len(wins) / len(pnl_pct) * 100.0, 3),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 6) if losses else None,
        "total_pnl_krw": round(sum(pnl_krw), 0),
        "return_on_deployed_pct": round(sum(pnl_krw) / sum(budgets) * 100.0, 6) if sum(budgets) > 0 else None,
        "return_on_risk_capital_pct": round(sum(pnl_krw) / risk_capital * 100.0, 6) if risk_capital > 0 else None,
        "max_drawdown_on_risk_capital_pct": round(_max_drawdown_pct(pnl_krw, risk_capital), 6),
        "loss_streak": _max_loss_streak(pnl_pct),
    }


def _simulate_sizing(
    trades: list[dict[str, Any]],
    *,
    kr_budget: int,
    us_budget_usd: int,
    max_positions: int,
    daily_loss_limit_pct: float,
    cfg: OptimizerConfig,
) -> dict[str, Any]:
    day_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        day = str(trade.get("entry_date") or trade.get("signal_date") or "")
        source = str(trade.get("source") or "")
        day_groups[(source, day)].append(trade)
    selected: list[dict[str, Any]] = []
    for _key, group in day_groups.items():
        selected.extend(sorted(group, key=lambda trade: str(trade.get("ticker") or ""))[:max_positions])
    selected = sorted(selected, key=_trade_sort_key)
    pnl_values: list[float] = []
    daily_pnl: dict[str, float] = defaultdict(float)
    for trade in selected:
        budget = kr_budget if trade.get("market") == "KR" else us_budget_usd * cfg.usd_krw
        pnl = budget * float(trade.get("net_pnl_pct") or 0.0) / 100.0
        pnl_values.append(pnl)
        daily_pnl[str(trade.get("exit_date") or trade.get("entry_date") or "")] += pnl
    markets = sorted({str(trade.get("market") or "") for trade in selected if trade.get("market")})
    risk_capital = _risk_capital_krw(markets, cfg, max_positions=max_positions, kr_budget=kr_budget, us_budget_usd=us_budget_usd)
    breach_days = sum(1 for value in daily_pnl.values() if risk_capital > 0 and value / risk_capital * 100.0 <= daily_loss_limit_pct)
    gross_profit = sum(value for value in pnl_values if value > 0)
    gross_loss = -sum(value for value in pnl_values if value < 0)
    total_pnl = sum(pnl_values)
    mdd = _max_drawdown_pct(pnl_values, risk_capital)
    return {
        "kr_fixed_order_krw": kr_budget,
        "us_fixed_order_usd": us_budget_usd,
        "max_positions": max_positions,
        "daily_loss_limit_pct": daily_loss_limit_pct,
        "n_trades_after_daily_cap": len(selected),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss else None,
        "total_pnl_krw": round(total_pnl, 0),
        "return_on_risk_capital_pct": round(total_pnl / risk_capital * 100.0, 6) if risk_capital > 0 else None,
        "max_drawdown_on_risk_capital_pct": round(mdd, 6),
        "daily_loss_breach_days": breach_days,
        "score": round((total_pnl / max(risk_capital, 1.0) * 100.0) + mdd * 0.5 - breach_days * 2.0, 6),
    }


def _read_csv(path: Path) -> list[dict[str, Any]]:
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            with path.open(newline="", encoding=encoding) as fp:
                return list(csv.DictReader(fp))
        except UnicodeDecodeError:
            continue
        except Exception:
            return []
    return []


def _normalize_trade(path: Path, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": str(path),
        "market": str(row.get("market", "") or "").upper(),
        "strategy": str(row.get("strategy", "") or ""),
        "mode": str(row.get("mode", "") or ""),
        "entry_timing": str(row.get("entry_timing", "") or ""),
        "entry_day_exit_policy": str(row.get("entry_day_exit_policy", "") or ""),
        "reason": str(row.get("reason", "") or ""),
        "ticker": str(row.get("ticker", "") or ""),
        "signal_date": str(row.get("signal_date", "") or ""),
        "entry_date": str(row.get("entry_date", "") or ""),
        "exit_date": str(row.get("exit_date", "") or ""),
        "net_pnl_pct": _to_float(row.get("net_pnl_pct")),
        "cost_bps": _to_float(row.get("cost_bps")),
    }


def _accepted(train: dict[str, Any], validation: dict[str, Any], stability: dict[str, Any], cfg: OptimizerConfig) -> bool:
    train_pf = train.get("profit_factor")
    val_pf = validation.get("profit_factor")
    return bool(
        train.get("avg_net_pnl_pct", 0) > 0
        and validation.get("avg_net_pnl_pct", 0) >= cfg.min_validation_avg_pct
        and train_pf is not None
        and train_pf > 1.0
        and val_pf is not None
        and val_pf >= cfg.min_validation_pf
        and validation.get("n_trades", 0) >= cfg.min_validation_trades
        and stability.get("positive_source_ratio", 0) >= cfg.min_positive_source_ratio
    )


def _score(train: dict[str, Any], validation: dict[str, Any], stability: dict[str, Any]) -> float:
    val_pf = float(validation.get("profit_factor") or 0)
    val_avg = float(validation.get("avg_net_pnl_pct") or 0)
    val_n = float(validation.get("n_trades") or 0)
    val_mdd = abs(float(validation.get("max_drawdown_on_risk_capital_pct") or 0))
    train_avg = float(train.get("avg_net_pnl_pct") or 0)
    source_ratio = float(stability.get("positive_source_ratio") or 0)
    return round(
        val_avg * 8.0
        + min(val_pf, 3.0) * 1.5
        + math.log10(max(val_n, 1.0)) * 0.5
        + source_ratio * 2.0
        + min(train_avg, 1.0)
        - val_mdd * 0.03,
        6,
    )


def _source_stability(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        by_source[str(trade.get("source") or "")].append(float(trade.get("net_pnl_pct") or 0.0))
    source_avgs = [sum(values) / len(values) for values in by_source.values() if values]
    positive = sum(1 for value in source_avgs if value > 0)
    return {
        "source_count": len(source_avgs),
        "positive_source_count": positive,
        "positive_source_ratio": round(positive / len(source_avgs), 6) if source_avgs else 0.0,
        "median_source_avg_pct": round(sorted(source_avgs)[len(source_avgs) // 2], 6) if source_avgs else 0.0,
    }


def _filter_trades(trades: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        trade
        for trade in trades
        if all(str(trade.get(key, "") or "") == str(value or "") for key, value in filters.items())
    ]


def _production_supported(filters: dict[str, Any]) -> bool:
    strategy = str(filters.get("strategy", "") or "")
    entry_timing = str(filters.get("entry_timing", "") or "")
    entry_day_policy = str(filters.get("entry_day_exit_policy", "") or "")
    if entry_timing in ("", "same_close"):
        return False
    if strategy != "momentum":
        return False
    return entry_timing in ("next_open", "next_open_confirmed") and entry_day_policy in ("", "defer", "allow")


def _trade_sort_key(trade: dict[str, Any]) -> tuple[str, str, str, str]:
    day = str(trade.get("entry_date") or trade.get("signal_date") or "")
    return (day, str(trade.get("source") or ""), str(trade.get("ticker") or ""), str(trade.get("strategy") or ""))


def _budget_krw(market: str, cfg: OptimizerConfig) -> float:
    return float(DEFAULT_V2_CONFIG.us_fixed_order_usd) * cfg.usd_krw if market == "US" else float(DEFAULT_V2_CONFIG.kr_fixed_order_krw)


def _risk_capital_krw(
    markets: list[str],
    cfg: OptimizerConfig,
    *,
    max_positions: int,
    kr_budget: int | None = None,
    us_budget_usd: int | None = None,
) -> float:
    if not markets:
        return float((kr_budget or DEFAULT_V2_CONFIG.kr_fixed_order_krw) * max_positions)
    total = 0.0
    for market in set(markets):
        if market == "US":
            budget = float(us_budget_usd or DEFAULT_V2_CONFIG.us_fixed_order_usd) * cfg.usd_krw
            total += budget * max_positions
        else:
            total += float(kr_budget or DEFAULT_V2_CONFIG.kr_fixed_order_krw) * max_positions
    return total


def _max_drawdown_pct(pnl_values: list[float], base_capital: float) -> float:
    if base_capital <= 0:
        return 0.0
    equity = base_capital
    peak = equity
    max_dd = 0.0
    for pnl in pnl_values:
        equity += pnl
        peak = max(peak, equity)
        drawdown = (equity - peak) / peak * 100.0 if peak > 0 else 0.0
        max_dd = min(max_dd, drawdown)
    return max_dd


def _max_loss_streak(pnl_pct: list[float]) -> int:
    best = 0
    current = 0
    for value in pnl_pct:
        if value < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _empty_metrics() -> dict[str, Any]:
    return {
        "n_trades": 0,
        "avg_net_pnl_pct": 0.0,
        "win_rate": 0.0,
        "profit_factor": None,
        "total_pnl_krw": 0.0,
        "return_on_deployed_pct": None,
        "return_on_risk_capital_pct": None,
        "max_drawdown_on_risk_capital_pct": 0.0,
        "loss_streak": 0,
    }


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(str(value).replace("%", "").replace(",", ""))
        return parsed if math.isfinite(parsed) else None
    except ValueError:
        return None


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# V2 Policy Optimization Report",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- basis: {payload['basis']}",
        f"- trade_rows: {payload['trade_rows']}",
        f"- sources: {payload['source_count']}",
        f"- candidates: {payload['candidate_count']}",
        f"- accepted: {payload['accepted_count']}",
        "",
        "## Recommendation",
        "",
    ]
    recommendation = payload.get("recommendation") or {}
    for key, value in recommendation.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Accepted Candidates", ""])
    headers = [
        "filter",
        "prod",
        "score",
        "all_n",
        "all_avg",
        "all_pf",
        "val_n",
        "val_avg",
        "val_pf",
        "source_ratio",
        "val_mdd",
    ]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in payload.get("accepted_candidates", [])[:15]:
        all_m = row.get("all") or {}
        val = row.get("validation") or {}
        stab = row.get("source_stability") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    json.dumps(row.get("filter"), ensure_ascii=False, sort_keys=True),
                    str(row.get("production_supported")),
                    str(row.get("score")),
                    str(all_m.get("n_trades")),
                    str(all_m.get("avg_net_pnl_pct")),
                    str(all_m.get("profit_factor")),
                    str(val.get("n_trades")),
                    str(val.get("avg_net_pnl_pct")),
                    str(val.get("profit_factor")),
                    str(stab.get("positive_source_ratio")),
                    str(val.get("max_drawdown_on_risk_capital_pct")),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Sizing Grid", ""])
    size_headers = [
        "kr_fixed_order_krw",
        "us_fixed_order_usd",
        "max_positions",
        "daily_loss_limit_pct",
        "n_trades_after_daily_cap",
        "profit_factor",
        "total_pnl_krw",
        "return_on_risk_capital_pct",
        "max_drawdown_on_risk_capital_pct",
        "daily_loss_breach_days",
        "score",
    ]
    lines.append("| " + " | ".join(size_headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(size_headers)) + " |")
    for row in payload.get("sizing_grid", [])[:15]:
        lines.append("| " + " | ".join(str(row.get(header, "")) for header in size_headers) + " |")
    lines.extend(["", "## Limits", ""])
    for item in payload.get("limits", []):
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)
