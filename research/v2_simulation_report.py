from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import csv
import json
import math

from config.v2 import DEFAULT_V2_CONFIG


@dataclass(frozen=True)
class SimulationSource:
    path: Path
    rows: list[dict[str, Any]]


def discover_previous_simulation_rows(root: str | Path) -> list[SimulationSource]:
    root_path = Path(root)
    candidates = list((root_path / "data" / "backtest_audit").rglob("audit_summary_*.csv"))
    candidates += list((root_path / "data" / "backtest").glob("sim_summary_*.csv"))
    sources: list[SimulationSource] = []
    for path in sorted(candidates):
        rows = _read_csv_rows(path)
        if rows:
            sources.append(SimulationSource(path=path, rows=rows))
    return sources


def build_simulation_report(root: str | Path, output_dir: str | Path | None = None) -> dict[str, str]:
    root_path = Path(root)
    sources = discover_previous_simulation_rows(root_path)
    summary_rows = [_normalize_row(source.path, row) for source in sources for row in source.rows]
    ranked = sorted(summary_rows, key=_rank_key, reverse=True)
    trade_sources = discover_previous_trade_rows(root_path)
    overlay_rows = [_simulate_v2_overlay(source.path, source.rows) for source in trade_sources]
    overlay_rows = [row for row in overlay_rows if row]
    ranked_overlay = sorted(overlay_rows, key=_overlay_rank_key, reverse=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "basis": "previous simulation artifacts with V2 fixed-sizing overlay; no Claude calls",
        "source_count": len(sources),
        "row_count": len(summary_rows),
        "trade_source_count": len(trade_sources),
        "trade_row_count": sum(len(source.rows) for source in trade_sources),
        "top_rows": ranked[:25],
        "top_robust_rows": [row for row in ranked if _robust_sample(row)][:25],
        "v2_overlay_rows": ranked_overlay[:25],
        "v2_overlay_robust_rows": [row for row in ranked_overlay if _robust_overlay_sample(row)][:25],
        "conclusion": _conclusion(ranked, ranked_overlay),
        "limits": [
            "The V2 overlay applies fixed order budgets and existing net trade outcomes; it does not re-run Claude selection.",
            "Timing, broker fills, ORDER_UNKNOWN, daily loss halt, and max-position overlap are approximated from available trade artifacts.",
            "Rows come from existing backtest artifacts and may mix windows, universes, and entry models.",
        ],
    }
    out_dir = Path(output_dir) if output_dir else root_path / "data" / "v2_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"v2_simulation_report_{stamp}.json"
    md_path = out_dir / f"v2_simulation_report_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_to_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def discover_previous_trade_rows(root: str | Path) -> list[SimulationSource]:
    root_path = Path(root)
    candidates = list((root_path / "data" / "backtest_audit").rglob("audit_trades_*.csv"))
    sources: list[SimulationSource] = []
    for path in sorted(candidates):
        rows = _read_csv_rows(path)
        if rows and "net_pnl_pct" in rows[0]:
            sources.append(SimulationSource(path=path, rows=rows))
    return sources


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            with path.open(newline="", encoding=encoding) as fp:
                return list(csv.DictReader(fp))
        except UnicodeDecodeError:
            continue
        except Exception:
            return []
    return []


def _normalize_row(path: Path, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": str(path),
        "market": row.get("market", ""),
        "strategy": row.get("strategy", ""),
        "engine": row.get("engine", row.get("entry_model", "")),
        "analysis_window": row.get("analysis_window", ""),
        "n_trades": _to_float(row.get("n_trades", row.get("trades"))),
        "win_rate": _to_float(row.get("win_rate")),
        "avg_pnl_pct": _to_float(row.get("avg_pnl_pct", row.get("avg_pnl"))),
        "profit_factor": _to_float(row.get("profit_factor", row.get("pf"))),
        "max_drawdown_pct": _to_float(row.get("max_drawdown_pct", row.get("maxdd"))),
    }


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(str(value).replace("%", "").replace(",", ""))
        return parsed if math.isfinite(parsed) else None
    except ValueError:
        return None


def _rank_key(row: dict[str, Any]) -> tuple[float, float, float, float]:
    pf = row.get("profit_factor")
    avg = row.get("avg_pnl_pct")
    trades = row.get("n_trades")
    dd = row.get("max_drawdown_pct")
    return (
        float(pf if pf is not None else -1),
        float(avg if avg is not None else -999),
        float(trades if trades is not None else 0),
        -abs(float(dd if dd is not None else 999)),
    )


def _robust_sample(row: dict[str, Any], min_trades: int = 30) -> bool:
    trades = row.get("n_trades")
    return trades is not None and float(trades) >= min_trades


def _simulate_v2_overlay(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    trades = [_normalize_trade(row) for row in rows]
    trades = [trade for trade in trades if trade.get("net_pnl_pct") is not None]
    if not trades:
        return None
    markets = sorted({str(trade.get("market") or "") for trade in trades if trade.get("market")})
    market = markets[0] if len(markets) == 1 else "MIXED"
    strategy_counts: dict[str, int] = {}
    for trade in trades:
        strategy = str(trade.get("strategy") or "")
        strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
    strategy = max(strategy_counts.items(), key=lambda item: item[1])[0] if strategy_counts else ""
    budgets = [_fixed_budget_krw(str(trade.get("market") or market)) for trade in trades]
    pnl_values = [budget * float(trade["net_pnl_pct"]) / 100.0 for trade, budget in zip(trades, budgets)]
    wins = [pnl for pnl in pnl_values if pnl > 0]
    losses = [pnl for pnl in pnl_values if pnl < 0]
    total_deployed = sum(budgets)
    risk_capital = _risk_capital_krw(markets)
    total_pnl = sum(pnl_values)
    avg_pnl_pct = sum(float(trade["net_pnl_pct"]) for trade in trades) / len(trades)
    sorted_pairs = sorted(zip(trades, pnl_values), key=lambda item: str(item[0].get("entry_date") or item[0].get("signal_date") or ""))
    max_dd_pct = _max_drawdown_pct([pnl for _trade, pnl in sorted_pairs], risk_capital)
    return {
        "source": str(path),
        "market": market,
        "strategy": strategy,
        "n_trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100.0, 3) if trades else 0.0,
        "avg_net_pnl_pct": round(avg_pnl_pct, 6),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 6) if losses else None,
        "fixed_budget_total_pnl_krw": round(total_pnl, 0),
        "return_on_deployed_pct": round(total_pnl / total_deployed * 100.0, 6) if total_deployed > 0 else None,
        "return_on_risk_capital_pct": round(total_pnl / risk_capital * 100.0, 6) if risk_capital > 0 else None,
        "max_drawdown_on_risk_capital_pct": round(max_dd_pct, 6),
        "risk_capital_krw": round(risk_capital, 0),
        "basis": "V2 fixed sizing overlay on audit_trades net_pnl_pct",
    }


def _normalize_trade(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "market": str(row.get("market", "") or "").upper(),
        "strategy": row.get("strategy", ""),
        "ticker": row.get("ticker", ""),
        "entry_date": row.get("entry_date", ""),
        "signal_date": row.get("signal_date", ""),
        "net_pnl_pct": _to_float(row.get("net_pnl_pct")),
    }


def _fixed_budget_krw(market: str) -> float:
    market_key = str(market or "").upper()
    if market_key == "US":
        return float(DEFAULT_V2_CONFIG.us_fixed_order_usd) * 1400.0
    return float(DEFAULT_V2_CONFIG.kr_fixed_order_krw)


def _risk_capital_krw(markets: list[str]) -> float:
    if not markets:
        return float(DEFAULT_V2_CONFIG.kr_fixed_order_krw * DEFAULT_V2_CONFIG.kr_max_positions)
    total = 0.0
    for market in set(markets):
        if market == "US":
            total += float(DEFAULT_V2_CONFIG.us_fixed_order_usd) * 1400.0 * DEFAULT_V2_CONFIG.us_max_positions
        else:
            total += float(DEFAULT_V2_CONFIG.kr_fixed_order_krw * DEFAULT_V2_CONFIG.kr_max_positions)
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


def _overlay_rank_key(row: dict[str, Any]) -> tuple[float, float, float, float]:
    pf = row.get("profit_factor")
    avg = row.get("avg_net_pnl_pct")
    trades = row.get("n_trades")
    total = row.get("return_on_risk_capital_pct")
    return (
        float(pf if pf is not None else -1),
        float(avg if avg is not None else -999),
        float(total if total is not None else -999),
        float(trades if trades is not None else 0),
    )


def _robust_overlay_sample(row: dict[str, Any], min_trades: int = 30) -> bool:
    trades = row.get("n_trades")
    return trades is not None and float(trades) >= min_trades


def _conclusion(rows: list[dict[str, Any]], overlay_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"status": "NO_DATA", "message": "No previous simulation rows were found."}
    robust_rows = [row for row in rows if _robust_sample(row)]
    best = robust_rows[0] if robust_rows else rows[0]
    robust_overlay = [row for row in overlay_rows if _robust_overlay_sample(row)]
    best_overlay = robust_overlay[0] if robust_overlay else (overlay_rows[0] if overlay_rows else None)
    status = "V2_OVERLAY_FROM_PREVIOUS_TRADES" if best_overlay else "BASELINE_ONLY"
    return {
        "status": status,
        "sample_rule": "preferred conclusion uses rows with n_trades >= 30",
        "best_profit_factor": best.get("profit_factor"),
        "best_avg_pnl_pct": best.get("avg_pnl_pct"),
        "best_n_trades": best.get("n_trades"),
        "best_market": best.get("market"),
        "best_strategy": best.get("strategy"),
        "best_engine": best.get("engine"),
        "best_source": best.get("source"),
        "best_v2_overlay_profit_factor": best_overlay.get("profit_factor") if best_overlay else None,
        "best_v2_overlay_avg_net_pnl_pct": best_overlay.get("avg_net_pnl_pct") if best_overlay else None,
        "best_v2_overlay_total_pnl_krw": best_overlay.get("fixed_budget_total_pnl_krw") if best_overlay else None,
        "best_v2_overlay_return_on_deployed_pct": best_overlay.get("return_on_deployed_pct") if best_overlay else None,
        "best_v2_overlay_return_on_risk_capital_pct": best_overlay.get("return_on_risk_capital_pct") if best_overlay else None,
        "best_v2_overlay_max_drawdown_pct": best_overlay.get("max_drawdown_on_risk_capital_pct") if best_overlay else None,
        "best_v2_overlay_source": best_overlay.get("source") if best_overlay else None,
        "robust_rows": len(robust_rows),
        "robust_overlay_rows": len(robust_overlay),
        "message": "Overlay uses prior trade outcomes with V2 fixed sizing. It is not a guarantee of live profitability.",
    }


def _to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# V2 Simulation Report",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- basis: {payload['basis']}",
        f"- sources: {payload['source_count']}",
        f"- rows: {payload['row_count']}",
        f"- trade_sources: {payload['trade_source_count']}",
        f"- trade_rows: {payload['trade_row_count']}",
        "",
        "## Conclusion",
        "",
    ]
    conclusion = payload["conclusion"]
    for key, value in conclusion.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Top Rows", ""])
    headers = ["market", "strategy", "engine", "analysis_window", "n_trades", "avg_pnl_pct", "profit_factor", "max_drawdown_pct"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in payload["top_rows"][:15]:
        lines.append("| " + " | ".join(str(row.get(header, "")) for header in headers) + " |")
    lines.extend(["", "## Robust Rows", ""])
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in payload["top_robust_rows"][:15]:
        lines.append("| " + " | ".join(str(row.get(header, "")) for header in headers) + " |")
    lines.extend(["", "## V2 Fixed-Sizing Overlay", ""])
    overlay_headers = [
        "market",
        "strategy",
        "n_trades",
        "avg_net_pnl_pct",
        "profit_factor",
        "fixed_budget_total_pnl_krw",
        "return_on_deployed_pct",
        "return_on_risk_capital_pct",
        "max_drawdown_on_risk_capital_pct",
    ]
    lines.append("| " + " | ".join(overlay_headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(overlay_headers)) + " |")
    for row in payload["v2_overlay_robust_rows"][:15]:
        lines.append("| " + " | ".join(str(row.get(header, "")) for header in overlay_headers) + " |")
    lines.extend(["", "## Limits", ""])
    for item in payload["limits"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)
