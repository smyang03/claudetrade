from __future__ import annotations

import csv
import itertools
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.rehearsal.context import (
    RehearsalContext,
    RehearsalGuardError,
    assert_repo_live_state_unchanged,
    compare_protected_static_files,
    install_no_network_guard,
    install_write_guard,
    snapshot_protected_static_files,
    snapshot_repo_live_state,
)
from runtime.rehearsal.reports import build_simulation_report, write_simulation_csv


@dataclass(frozen=True)
class PriceTick:
    ts: str
    price: float


@dataclass(frozen=True)
class SimulationCase:
    name: str
    market: str
    ticker: str
    path_type: str
    price_tape: list[PriceTick]
    params: dict[str, Any] = field(default_factory=dict)

    def with_overrides(self, overrides: dict[str, Any] | None = None) -> "SimulationCase":
        merged = dict(self.params)
        merged.update(overrides or {})
        return replace(self, params=merged)


def _tick(ts: str, price: float) -> PriceTick:
    return PriceTick(ts=ts, price=float(price))


def _as_float(value: Any, *, field_name: str = "price") -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except Exception as exc:
        raise RehearsalGuardError(f"invalid {field_name}: {value!r}") from exc


def _coerce_scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    raw = value.strip()
    lower = raw.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in {"none", "null"}:
        return None
    try:
        if "." not in raw and "e" not in lower:
            return int(raw)
        return float(raw)
    except ValueError:
        return raw


def _row_value(row: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for key in candidates:
        if key in lowered and lowered[key] not in (None, ""):
            return lowered[key]
    return None


def _tick_from_row(row: Any, idx: int) -> PriceTick:
    if isinstance(row, (int, float, str)):
        return _tick(str(idx), _as_float(row))
    if not isinstance(row, dict):
        raise RehearsalGuardError(f"invalid tape row at index {idx}: {row!r}")
    price = _row_value(row, ("price", "close", "current_price", "last", "c", "adj_close"))
    if price in (None, ""):
        raise RehearsalGuardError(f"tape row missing price field at index {idx}")
    ts = _row_value(row, ("ts", "timestamp", "datetime", "time", "date"))
    return _tick(str(ts if ts not in (None, "") else idx), _as_float(price))


def _normalize_price_tape(rows: Any) -> list[PriceTick]:
    if isinstance(rows, dict):
        for key in ("price_tape", "ticks", "prices", "rows", "data"):
            if key in rows:
                rows = rows[key]
                break
    if not isinstance(rows, list):
        raise RehearsalGuardError("price tape must be a list or an object containing price_tape/ticks/prices")
    tape = [_tick_from_row(row, idx) for idx, row in enumerate(rows)]
    if not tape:
        raise RehearsalGuardError("price tape is empty")
    return tape


def load_price_tape(path: Path) -> list[PriceTick]:
    source = Path(path).expanduser()
    if not source.exists():
        raise RehearsalGuardError(f"tape file not found: {source}")
    suffix = source.suffix.lower()
    if suffix == ".jsonl":
        rows = [json.loads(line) for line in source.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        return _normalize_price_tape(rows)
    if suffix == ".json":
        return _normalize_price_tape(json.loads(source.read_text(encoding="utf-8-sig")))
    if suffix in {".csv", ".txt"}:
        with source.open("r", encoding="utf-8-sig", newline="") as fp:
            sample = fp.read(4096)
            fp.seek(0)
            try:
                has_header = csv.Sniffer().has_header(sample) if sample.strip() else False
            except csv.Error:
                has_header = False
            if has_header:
                return _normalize_price_tape(list(csv.DictReader(fp)))
            return _normalize_price_tape([row[0] for row in csv.reader(fp) if row])
    raise RehearsalGuardError(f"unsupported tape file extension: {source.suffix}")


def _params_from_tape(market: str, ticker: str, tape: list[PriceTick]) -> dict[str, Any]:
    params = _base_params(market=market, ticker=ticker)
    first = float(tape[0].price)
    params.update(
        {
            "buy_zone_low": round(first * 0.97, 6),
            "buy_zone_high": round(first * 1.01, 6),
            "target_price": round(first * 1.05, 6),
            "stop_price": round(first * 0.96, 6),
        }
    )
    return params


def simulation_case_from_tape(
    *,
    name: str,
    market: str,
    ticker: str,
    price_tape: list[PriceTick],
    path_type: str = "",
    params: dict[str, Any] | None = None,
) -> SimulationCase:
    market_key = str(market or "US").upper()
    ticker_key = str(ticker or "UNKNOWN").upper() if market_key == "US" else str(ticker or "UNKNOWN")
    merged = _params_from_tape(market_key, ticker_key, price_tape)
    merged.update({key: _coerce_scalar(value) for key, value in (params or {}).items()})
    return SimulationCase(
        name=str(name or f"{market_key}_{ticker_key}_tape"),
        market=market_key,
        ticker=ticker_key,
        path_type=str(path_type or ("claude_price" if market_key == "US" else "path_a")),
        price_tape=list(price_tape),
        params=merged,
    )


def simulation_case_from_record(record: dict[str, Any], *, base_dir: Path | None = None, index: int = 0) -> SimulationCase:
    if not isinstance(record, dict):
        raise RehearsalGuardError(f"batch case must be an object at index {index}")
    params = dict(record.get("params") or {})
    if "scenario" in record:
        case = simulation_case_for_scenario(str(record["scenario"]))
        if params or record.get("name"):
            case = case.with_overrides(params)
            case = replace(case, name=str(record.get("name") or case.name))
        return case
    tape_rows = record.get("price_tape") or record.get("ticks") or record.get("prices")
    if tape_rows is not None:
        tape = _normalize_price_tape(tape_rows)
    elif record.get("tape_file"):
        path = Path(str(record["tape_file"])).expanduser()
        if not path.is_absolute() and base_dir is not None:
            path = base_dir / path
        tape = load_price_tape(path)
    else:
        raise RehearsalGuardError(f"batch case requires scenario, price_tape, or tape_file at index {index}")
    return simulation_case_from_tape(
        name=str(record.get("name") or f"batch_case_{index}"),
        market=str(record.get("market") or "US"),
        ticker=str(record.get("ticker") or "UNKNOWN"),
        path_type=str(record.get("path_type") or ""),
        price_tape=tape,
        params=params,
    )


def load_simulation_batch(path: Path) -> tuple[list[SimulationCase], dict[str, Any], dict[str, list[Any]]]:
    source = Path(path).expanduser()
    if not source.exists():
        raise RehearsalGuardError(f"batch file not found: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RehearsalGuardError(f"invalid batch JSON: {source}") from exc
    if isinstance(payload, list):
        case_records = payload
        overrides: dict[str, Any] = {}
        sweep: dict[str, list[Any]] = {}
    elif isinstance(payload, dict):
        case_records = payload.get("cases") or payload.get("simulations") or []
        overrides = {str(key): _coerce_scalar(value) for key, value in dict(payload.get("overrides") or {}).items()}
        sweep = {}
        for key, values in dict(payload.get("sweep") or {}).items():
            raw_values = values if isinstance(values, list) else [values]
            sweep[str(key)] = [_coerce_scalar(value) for value in raw_values]
    else:
        raise RehearsalGuardError("batch file must be a JSON object or list")
    if not isinstance(case_records, list) or not case_records:
        raise RehearsalGuardError("batch file contains no cases")
    cases = [simulation_case_from_record(record, base_dir=source.parent, index=idx) for idx, record in enumerate(case_records)]
    return cases, overrides, sweep


def _base_params(*, market: str, ticker: str) -> dict[str, Any]:
    is_us = market.upper() == "US"
    return {
        "market": market.upper(),
        "ticker": ticker.upper() if is_us else ticker,
        "cash_krw": 6_000_000 if is_us else 5_000_000,
        "usd_krw": 1350.0,
        "fixed_order_krw": 450_000.0,
        "min_order_krw": 50_000.0,
        "max_positions": 15,
        "current_positions": 0,
        "daily_entry_cap": 40,
        "current_daily_entries": 0,
        "confidence": 0.72,
        "confidence_threshold": 0.5,
        "buy_zone_low": 120.0 if is_us else 70_000.0,
        "buy_zone_high": 125.0 if is_us else 73_000.0,
        "target_price": 130.0 if is_us else 76_000.0,
        "stop_price": 118.0 if is_us else 69_000.0,
        "slippage_cap": 1.002 if is_us else 1.003,
        "broker_truth_status": "ok",
        "order_unknown_blocks_entry": False,
        "profit_ladder_enabled": True,
        "profit_ladder_trigger_pct": 4.0,
        "profit_ladder_giveback_pct": 1.5,
        "entry_fee_pct": 0.0,
        "exit_fee_pct": 0.0,
    }


def simulation_case_for_scenario(name: str) -> SimulationCase:
    scenario = str(name or "us_pathb_buy_zone_replay").strip()
    base = _base_params(market="US", ticker="NVDA")

    if scenario == "us_pathb_buy_zone_replay":
        tape = [
            _tick("09:30", 127.0),
            _tick("09:35", 124.4),
            _tick("10:10", 126.2),
            _tick("11:00", 130.6),
            _tick("12:00", 132.0),
        ]
    elif scenario == "us_pathb_missed_buy_zone":
        tape = [
            _tick("09:30", 127.0),
            _tick("09:45", 125.8),
            _tick("10:20", 129.5),
            _tick("11:30", 134.0),
        ]
    elif scenario == "us_pathb_stop_then_rebound":
        tape = [
            _tick("09:30", 124.0),
            _tick("09:40", 121.8),
            _tick("10:05", 117.5),
            _tick("11:00", 125.5),
            _tick("12:00", 132.0),
        ]
    elif scenario == "us_pathb_profit_ladder_giveback":
        tape = [
            _tick("09:30", 123.8),
            _tick("10:00", 128.8),
            _tick("10:30", 132.4),
            _tick("11:00", 130.1),
            _tick("12:00", 131.0),
        ]
        base.update({"target_price": 135.0, "profit_ladder_trigger_pct": 5.0, "profit_ladder_giveback_pct": 1.2})
    elif scenario == "broker_truth_fail_closed":
        tape = [_tick("09:30", 124.2), _tick("10:00", 131.0)]
        base.update({"broker_truth_status": "stale"})
    elif scenario == "order_unknown_halts_entry":
        tape = [_tick("09:30", 124.2), _tick("10:00", 131.0)]
        base.update({"order_unknown_blocks_entry": True})
    elif scenario == "kr_patha_order_size_gate":
        base = _base_params(market="KR", ticker="005930")
        tape = [_tick("09:00", 30_000.0), _tick("10:00", 33_500.0)]
        base.update(
            {
                "buy_zone_low": 29_000.0,
                "buy_zone_high": 31_000.0,
                "target_price": 33_000.0,
                "stop_price": 28_500.0,
                "fixed_order_krw": 60_000.0,
                "min_order_krw": 100_000.0,
            }
        )
    else:
        raise RehearsalGuardError(f"unknown simulation scenario: {scenario}")

    return SimulationCase(
        name=scenario,
        market=str(base["market"]),
        ticker=str(base["ticker"]),
        path_type="claude_price" if str(base["market"]) == "US" else "path_a",
        price_tape=tape,
        params=base,
    )


def all_simulation_scenarios() -> list[str]:
    return [
        "us_pathb_buy_zone_replay",
        "us_pathb_missed_buy_zone",
        "us_pathb_stop_then_rebound",
        "us_pathb_profit_ladder_giveback",
        "broker_truth_fail_closed",
        "order_unknown_halts_entry",
        "kr_patha_order_size_gate",
    ]


def _native_to_krw(price: float, params: dict[str, Any]) -> float:
    if str(params.get("market", "")).upper() == "US":
        return float(price) * float(params.get("usd_krw") or 1350.0)
    return float(price)


def _pnl_pct(entry: float, exit_price: float, params: dict[str, Any]) -> float:
    if entry <= 0:
        return 0.0
    gross = ((float(exit_price) - float(entry)) / float(entry)) * 100.0
    return gross - float(params.get("entry_fee_pct") or 0.0) - float(params.get("exit_fee_pct") or 0.0)


def _pnl_krw(entry: float, exit_price: float, qty: int, params: dict[str, Any]) -> float:
    return (_native_to_krw(exit_price, params) - _native_to_krw(entry, params)) * int(qty or 0)


def _buy_qty(price: float, params: dict[str, Any]) -> tuple[int, str]:
    price_krw = _native_to_krw(price, params) * float(params.get("slippage_cap") or 1.0)
    if price_krw <= 0:
        return 0, "INVALID_PRICE"
    fixed = float(params.get("fixed_order_krw") or 0.0)
    min_order = float(params.get("min_order_krw") or 0.0)
    qty = int(fixed // price_krw)
    if qty <= 0:
        if price_krw > fixed:
            return 0, "HIGH_PRICE_BUDGET_BLOCK"
        return 0, "ORDER_SIZE_TOO_SMALL_GATE"
    if qty * price_krw < min_order:
        return 0, "ORDER_SIZE_TOO_SMALL_GATE"
    if qty * price_krw > float(params.get("cash_krw") or 0.0):
        return 0, "AFFORDABILITY_BLOCK"
    return qty, "OK"


def _entry_block_reason(params: dict[str, Any]) -> str:
    if str(params.get("broker_truth_status") or "ok") != "ok":
        return "BLOCKED_BROKER_TRUTH"
    if bool(params.get("order_unknown_blocks_entry")):
        return "ORDER_UNKNOWN_UNRESOLVED"
    if float(params.get("confidence") or 0.0) < float(params.get("confidence_threshold") or 0.0):
        return "BLOCKED_CONFIDENCE"
    if int(params.get("current_positions") or 0) >= int(params.get("max_positions") or 0):
        return "BLOCKED_MAX_POSITIONS"
    if int(params.get("current_daily_entries") or 0) >= int(params.get("daily_entry_cap") or 0):
        return "BLOCKED_DAILY_ENTRY_CAP"
    return ""


def _score(metrics: dict[str, Any]) -> float:
    if metrics.get("entered"):
        if metrics.get("closed"):
            return float(metrics.get("realized_pnl_pct") or 0.0)
        return float(metrics.get("unrealized_pnl_pct") or 0.0)
    missed = float(metrics.get("missed_gain_pct") or 0.0)
    return -missed if missed > 0 else 0.0


def _add_hint(hints: list[dict[str, Any]], *, category: str, priority: str, signal: str, suggestion: str, evidence: dict[str, Any]) -> None:
    hints.append(
        {
            "category": category,
            "priority": priority,
            "signal": signal,
            "suggestion": suggestion,
            "evidence": evidence,
        }
    )


def _improvement_hints(events: list[dict[str, Any]], metrics: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    reasons = {str(event.get("reason") or "") for event in events}
    event_types = {str(event.get("event_type") or "") for event in events}

    if "BLOCKED_BROKER_TRUTH" in reasons:
        _add_hint(
            hints,
            category="operability",
            priority="high",
            signal="broker truth blocked entry",
            suggestion="refresh broker truth before entry scan and expose stale age in the ops summary",
            evidence={"broker_truth_status": params.get("broker_truth_status")},
        )
    if "ORDER_UNKNOWN_UNRESOLVED" in reasons:
        _add_hint(
            hints,
            category="operability",
            priority="high",
            signal="ORDER_UNKNOWN halted entry",
            suggestion="prioritize order_unknown reconciliation before enabling new entries",
            evidence={"order_unknown_blocks_entry": params.get("order_unknown_blocks_entry")},
        )
    if "HIGH_PRICE_BUDGET_BLOCK" in reasons:
        _add_hint(
            hints,
            category="profitability",
            priority="medium",
            signal="fixed budget cannot buy one share",
            suggestion="filter high-price tickers before selection or review market-specific fixed order budget",
            evidence={"fixed_order_krw": params.get("fixed_order_krw"), "ticker": params.get("ticker")},
        )
    if "ORDER_SIZE_TOO_SMALL_GATE" in reasons:
        _add_hint(
            hints,
            category="operability",
            priority="medium",
            signal="order size too small",
            suggestion="align fixed_order_krw and min_order_krw, or keep these candidates out of trade_ready",
            evidence={"fixed_order_krw": params.get("fixed_order_krw"), "min_order_krw": params.get("min_order_krw")},
        )
    if "BLOCKED_CONFIDENCE" in reasons and float(metrics.get("missed_gain_pct") or 0.0) >= 3.0:
        _add_hint(
            hints,
            category="profitability",
            priority="medium",
            signal="confidence block missed a profitable move",
            suggestion="review confidence threshold or add a high-quality override signal for this setup",
            evidence={"confidence": params.get("confidence"), "threshold": params.get("confidence_threshold")},
        )
    if not metrics.get("entered") and float(metrics.get("missed_gain_pct") or 0.0) >= 3.0:
        _add_hint(
            hints,
            category="profitability",
            priority="medium",
            signal="price missed buy zone then rallied",
            suggestion="compare buy_zone width, selection timing, and pullback confirmation latency",
            evidence={"missed_gain_pct": metrics.get("missed_gain_pct"), "buy_zone_high": params.get("buy_zone_high")},
        )
    if "EXIT_HARD_STOP" in event_types and float(metrics.get("final_from_entry_pct") or 0.0) > 2.0:
        _add_hint(
            hints,
            category="profitability",
            priority="medium",
            signal="hard stop was followed by rebound",
            suggestion="review stop distance, protective hold boundary, and rebound-aware exit diagnostics",
            evidence={"stop_price": params.get("stop_price"), "final_from_entry_pct": metrics.get("final_from_entry_pct")},
        )
    if "EXIT_TARGET" in event_types and float(metrics.get("post_exit_runup_pct") or 0.0) >= 2.0:
        _add_hint(
            hints,
            category="profitability",
            priority="low",
            signal="target exit left material run-up",
            suggestion="compare target extension and profit-ladder giveback settings for this setup",
            evidence={"post_exit_runup_pct": metrics.get("post_exit_runup_pct"), "target_price": params.get("target_price")},
        )
    if not hints and metrics.get("entered"):
        _add_hint(
            hints,
            category="baseline",
            priority="low",
            signal="scenario completed without a dominant defect signal",
            suggestion="use this result as a control case for parameter sweeps",
            evidence={"score": metrics.get("score")},
        )
    return hints


def run_simulation_case(case: SimulationCase, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    active = case.with_overrides(overrides)
    params = dict(active.params)
    events: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None
    exit_event: dict[str, Any] | None = None
    block_emitted = False
    peak_price = 0.0
    max_price = max((tick.price for tick in active.price_tape), default=0.0)
    min_price = min((tick.price for tick in active.price_tape), default=0.0)

    for idx, tick in enumerate(active.price_tape):
        price = float(tick.price)
        if position is None:
            block_reason = _entry_block_reason(params)
            if block_reason:
                if not block_emitted:
                    events.append({"event_type": "ENTRY_BLOCKED", "ts": tick.ts, "price": price, "reason": block_reason})
                    block_emitted = True
                continue
            if price < float(params.get("buy_zone_low") or 0.0):
                events.append({"event_type": "WAIT_BELOW_BUY_ZONE", "ts": tick.ts, "price": price})
                continue
            if price > float(params.get("buy_zone_high") or 0.0):
                events.append({"event_type": "WAIT_ABOVE_BUY_ZONE", "ts": tick.ts, "price": price})
                continue
            qty, reason = _buy_qty(price, params)
            if qty <= 0:
                events.append({"event_type": "ENTRY_BLOCKED", "ts": tick.ts, "price": price, "reason": reason})
                continue
            position = {"entry_ts": tick.ts, "entry_idx": idx, "entry_price": price, "qty": qty}
            peak_price = price
            events.append({"event_type": "ENTRY_FILLED", "ts": tick.ts, "price": price, "qty": qty, "reason": "BUY_ZONE_HIT"})
            continue

        entry_price = float(position["entry_price"])
        peak_price = max(peak_price, price)
        pnl_now = _pnl_pct(entry_price, price, params)
        peak_pnl = _pnl_pct(entry_price, peak_price, params)
        giveback = peak_pnl - pnl_now
        if price <= float(params.get("stop_price") or 0.0):
            exit_event = {"event_type": "EXIT_HARD_STOP", "ts": tick.ts, "price": price, "reason": "HARD_STOP"}
        elif price >= float(params.get("target_price") or 0.0):
            exit_event = {"event_type": "EXIT_TARGET", "ts": tick.ts, "price": price, "reason": "TARGET_HIT"}
        elif (
            bool(params.get("profit_ladder_enabled"))
            and peak_pnl >= float(params.get("profit_ladder_trigger_pct") or 0.0)
            and giveback >= float(params.get("profit_ladder_giveback_pct") or 0.0)
        ):
            exit_event = {
                "event_type": "EXIT_PROFIT_LADDER",
                "ts": tick.ts,
                "price": price,
                "reason": "PROFIT_LADDER_GIVEBACK",
                "peak_pnl_pct": round(peak_pnl, 4),
                "giveback_pct": round(giveback, 4),
            }
        if exit_event is not None:
            events.append({**exit_event, "qty": int(position["qty"])})
            break

    last_price = float(active.price_tape[-1].price) if active.price_tape else 0.0
    metrics: dict[str, Any] = {
        "entered": position is not None,
        "closed": exit_event is not None,
        "entry_price": float(position["entry_price"]) if position else None,
        "exit_price": float(exit_event["price"]) if exit_event else None,
        "qty": int(position["qty"]) if position else 0,
        "max_price": max_price,
        "min_price": min_price,
        "final_price": last_price,
        "realized_pnl_pct": 0.0,
        "realized_pnl_krw": 0.0,
        "unrealized_pnl_pct": 0.0,
        "unrealized_pnl_krw": 0.0,
        "missed_gain_pct": 0.0,
        "post_exit_runup_pct": 0.0,
        "final_from_entry_pct": 0.0,
    }
    if position is not None:
        entry_price = float(position["entry_price"])
        metrics["final_from_entry_pct"] = round(_pnl_pct(entry_price, last_price, params), 4)
        if exit_event is not None:
            exit_price = float(exit_event["price"])
            metrics["realized_pnl_pct"] = round(_pnl_pct(entry_price, exit_price, params), 4)
            metrics["realized_pnl_krw"] = round(_pnl_krw(entry_price, exit_price, int(position["qty"]), params), 2)
            if exit_price > 0:
                metrics["post_exit_runup_pct"] = round(((max_price - exit_price) / exit_price) * 100.0, 4)
        else:
            metrics["unrealized_pnl_pct"] = round(_pnl_pct(entry_price, last_price, params), 4)
            metrics["unrealized_pnl_krw"] = round(_pnl_krw(entry_price, last_price, int(position["qty"]), params), 2)
    else:
        buy_zone_high = float(params.get("buy_zone_high") or 0.0)
        if buy_zone_high > 0 and max_price > buy_zone_high:
            metrics["missed_gain_pct"] = round(((max_price - buy_zone_high) / buy_zone_high) * 100.0, 4)

    metrics["score"] = round(_score(metrics), 4)
    result = {
        "ok": True,
        "scenario": active.name,
        "market": active.market,
        "ticker": active.ticker,
        "path_type": active.path_type,
        "params": params,
        "price_tape": [tick.__dict__ for tick in active.price_tape],
        "events": events,
        "metrics": metrics,
    }
    result["improvement_hints"] = _improvement_hints(events, metrics, params)
    return result


def _sweep_combinations(sweep: dict[str, list[Any]] | None) -> list[dict[str, Any]]:
    if not sweep:
        return [{}]
    keys = list(sweep)
    values = [list(sweep[key]) for key in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def run_simulation_suite(
    cases: list[SimulationCase],
    *,
    overrides: dict[str, Any] | None = None,
    sweep: dict[str, list[Any]] | None = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for case in cases:
        for combo in _sweep_combinations(sweep):
            merged = dict(overrides or {})
            merged.update(combo)
            result = run_simulation_case(case, merged)
            result["sweep"] = combo
            results.append(result)
    return build_simulation_report(results)


def simulation_report_path(context: RehearsalContext) -> Path:
    return context.sandbox_root / "reports" / f"{context.scenario}_simulation.json"


def _sandbox_output_path(context: RehearsalContext, requested: Path | None, default_name: str) -> Path:
    if requested is None:
        return context.sandbox_root / "reports" / default_name
    raw = Path(requested).expanduser()
    target = raw if raw.is_absolute() else context.sandbox_root / raw
    target = target.resolve()
    try:
        target.relative_to(context.sandbox_root)
    except ValueError as exc:
        raise RehearsalGuardError(f"simulation output path must stay inside sandbox: {target}") from exc
    return target


def run_guarded_simulation(
    context: RehearsalContext,
    cases: list[SimulationCase],
    *,
    overrides: dict[str, Any] | None = None,
    sweep: dict[str, list[Any]] | None = None,
    report_path: Path | None = None,
    csv_path: Path | None = None,
) -> dict[str, Any]:
    before = snapshot_repo_live_state()
    protected_before = snapshot_protected_static_files()
    with install_no_network_guard(context) as network_guard:
        with install_write_guard(context) as write_guard:
            report = run_simulation_suite(cases, overrides=overrides, sweep=sweep)
            report.update(
                {
                    "runtime_mode": "live",
                    "runtime_context": context.runtime_context,
                    "sandbox_root": str(context.sandbox_root),
                    "network_guard": {"call_count": len(network_guard.get("calls") or [])},
                    "write_guard": {"call_count": len(write_guard.get("calls") or [])},
                    "protected_static_files": compare_protected_static_files(protected_before),
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            )
            path = _sandbox_output_path(context, report_path, f"{context.scenario}_simulation.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            report["report_path"] = str(path)
            if csv_path is not None:
                csv_target = _sandbox_output_path(context, csv_path, f"{context.scenario}_simulation.csv")
                write_simulation_csv(report, csv_target)
                report["csv_path"] = str(csv_target)
    assert_repo_live_state_unchanged(before)
    return report
