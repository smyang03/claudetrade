from __future__ import annotations

"""Unified low-turnover core shadow tracker; no broker/order authority."""

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path
from tools.index_trend_strategy_lab import download_panel as download_index_panel
from tools.index_trend_strategy_lab import specs as index_specs
from tools.index_trend_strategy_lab import target_weights as index_target_weights
from tools.integrated_core_strategy_lab import download_monthly as download_integrated_panel
from tools.integrated_core_strategy_lab import signal_weights as integrated_signal_weights
from tools.us_affordable_trend_lab import download_panel as download_us_panel
from tools.us_affordable_trend_lab import target_weights as us_target_weights


AUTHORITY = "SHADOW_ONLY_NO_ORDER_OR_LIVE_CONFIG_EFFECT"
US_PRIMARY = "US_SCHG_BIL_TREND_V1"
KR_PRIMARY = "KR_FACTOR_TREND_V1"
US_COST_ONE_WAY_PCT = 0.25
KR_COST_ONE_WAY_PCT = 0.105


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _append_once(path: Path, payload: dict[str, Any], *, keys: tuple[str, ...]) -> bool:
    identity = tuple(str(payload.get(key) or "") for key in keys)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines()[-100:]:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if tuple(str(row.get(key) or "") for key in keys) == identity:
                return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return True


def _weights(row: pd.Series) -> dict[str, float]:
    return {str(key): float(value) for key, value in row.items() if pd.notna(value) and float(value) > 0}


def build_targets(
    *,
    us_panel: pd.DataFrame,
    integrated_panel: pd.DataFrame,
    index_panel: pd.DataFrame,
    as_of: str,
) -> dict[str, Any]:
    current = pd.Timestamp(as_of).to_period("M")
    us = _weights(us_target_weights(us_panel, 10, 12).iloc[-1])
    integrated = integrated_signal_weights(integrated_panel).iloc[-1]
    kr = _weights(integrated.reindex(["275280.KS", "275300.KS", "153130.KS"]).fillna(0.0))
    kr_total = sum(kr.values())
    if kr_total > 0:
        kr = {asset: value / kr_total for asset, value in kr.items()}

    arms: list[dict[str, Any]] = [
        {"strategy_id": US_PRIMARY, "market": "US", "role": "primary", "weights": us, "cash_weight": 0.0},
        {"strategy_id": KR_PRIMARY, "market": "KR", "role": "primary", "weights": kr, "cash_weight": max(0.0, 1.0 - sum(kr.values()))},
    ]
    for spec in index_specs():
        if spec.method == "buy_hold":
            continue
        weight = _weights(index_target_weights(index_panel, spec).iloc[-1])
        arms.append(
            {
                "strategy_id": spec.name,
                "market": spec.market,
                "role": "benchmark",
                "weights": weight,
                "cash_weight": max(0.0, 1.0 - sum(weight.values())),
            }
        )
    return {
        "schema_version": "core_shadow_targets_v1",
        "authority": AUTHORITY,
        "as_of": as_of,
        "signal_month": str(current - 1),
        "effective_month": str(current),
        "arms": arms,
    }


def update_book(
    book: dict[str, Any],
    targets: dict[str, Any],
    prices_native: dict[str, float],
    *,
    usd_krw: float,
    price_date: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    output = dict(book or {})
    output.setdefault("schema_version", "core_shadow_book_v1")
    output.setdefault("arms", {})
    rows: list[dict[str, Any]] = []
    for target in targets.get("arms") or []:
        strategy_id = str(target.get("strategy_id") or "")
        market = str(target.get("market") or "")
        weights = {str(k): float(v) for k, v in (target.get("weights") or {}).items()}
        previous = dict(output["arms"].get(strategy_id) or {})
        previous_weights = {str(k): float(v) for k, v in (previous.get("weights") or {}).items()}
        previous_prices = {str(k): float(v) for k, v in (previous.get("prices_krw") or {}).items()}
        current_prices: dict[str, float] = {}
        for asset in set(weights) | set(previous_weights):
            native = float(prices_native.get(asset, 0.0) or 0.0)
            if native > 0:
                current_prices[asset] = native * usd_krw if market == "US" else native

        gross = 0.0
        priced_weight = 0.0
        for asset, weight in previous_weights.items():
            before = previous_prices.get(asset, 0.0)
            after = current_prices.get(asset, 0.0)
            if before > 0 and after > 0:
                gross += weight * (after / before - 1.0)
                priced_weight += weight
        turnover = sum(abs(weights.get(asset, 0.0) - previous_weights.get(asset, 0.0)) for asset in set(weights) | set(previous_weights))
        if not previous:
            turnover = sum(abs(value) for value in weights.values())
        cost_pct = US_COST_ONE_WAY_PCT if market == "US" else KR_COST_ONE_WAY_PCT
        cost = turnover * cost_pct / 100.0
        nav_before = float(previous.get("nav", 1.0) or 1.0)
        net = gross - cost
        nav_after = nav_before * (1.0 + net)
        row = {
            "schema_version": "core_shadow_mtm_v1",
            "authority": AUTHORITY,
            "price_date": price_date,
            "effective_month": targets.get("effective_month"),
            "strategy_id": strategy_id,
            "market": market,
            "role": target.get("role"),
            "gross_return": gross,
            "cost_return": cost,
            "net_return": net,
            "nav_before": nav_before,
            "nav_after": nav_after,
            "turnover": turnover,
            "priced_weight": priced_weight,
            "weights": weights,
            "cash_weight": float(target.get("cash_weight", 0.0) or 0.0),
        }
        rows.append(row)
        output["arms"][strategy_id] = {
            "market": market,
            "role": target.get("role"),
            "nav": nav_after,
            "weights": weights,
            "prices_krw": {asset: current_prices[asset] for asset in weights if asset in current_prices},
            "last_price_date": price_date,
            "effective_month": targets.get("effective_month"),
        }
    output["updated_at"] = _now().isoformat(timespec="seconds")
    return output, rows


def _download_quotes(symbols: list[str]) -> tuple[dict[str, float], str]:
    import yfinance as yf

    prices: dict[str, float] = {}
    dates: list[pd.Timestamp] = []
    for symbol in sorted(set(symbols)):
        raw = yf.download(symbol, period="7d", interval="1d", auto_adjust=True, progress=False, threads=False)
        if raw.empty:
            raise RuntimeError(f"no current quote for {symbol}")
        close = raw["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = pd.to_numeric(close, errors="coerce").dropna()
        if close.empty:
            raise RuntimeError(f"no numeric quote for {symbol}")
        prices[symbol] = float(close.iloc[-1])
        dates.append(pd.Timestamp(close.index[-1]))
    price_date = str(min(dates).date()) if dates else ""
    return prices, price_date


def run_once(*, as_of: str, state_dir: Path | None = None, shadow_dir: Path | None = None) -> dict[str, Any]:
    state_dir = state_dir or get_runtime_path("state")
    shadow_dir = shadow_dir or get_runtime_path("data", "shadow")
    state_dir.mkdir(parents=True, exist_ok=True)
    shadow_dir.mkdir(parents=True, exist_ok=True)
    heartbeat = state_dir / "core_shadow_tracker_heartbeat.json"
    started = _now()
    _atomic_json(heartbeat, {
        "process": "core_shadow_tracker", "authority": AUTHORITY, "status": "running",
        "last_started_at": started.isoformat(timespec="seconds"), "pid": os.getpid(),
    })
    key = pd.Timestamp(as_of).strftime("%Y%m")
    cache_dir = state_dir / "core_shadow_cache"
    try:
        us_panel, us_source = download_us_panel(as_of, cache_dir / f"us_affordable_{key}.csv")
        integrated_panel, kr_source = download_integrated_panel(as_of, cache_dir / f"integrated_{key}.csv")
        index_panel, index_source = download_index_panel(as_of, cache_dir / f"index_{key}.csv")
        targets = build_targets(
            us_panel=us_panel,
            integrated_panel=integrated_panel,
            index_panel=index_panel,
            as_of=as_of,
        )
        symbols = [asset for arm in targets["arms"] for asset in (arm.get("weights") or {})]
        native, price_date = _download_quotes([*symbols, "KRW=X"])
        usd_krw = float(native.pop("KRW=X"))
        book_path = state_dir / "core_shadow_book.json"
        try:
            book = json.loads(book_path.read_text(encoding="utf-8"))
        except Exception:
            book = {}
        book, rows = update_book(book, targets, native, usd_krw=usd_krw, price_date=price_date)
        _atomic_json(book_path, book)
        signal_snapshot = shadow_dir / f"core_shadow_signal_{targets['effective_month'].replace('-', '')}.json"
        _atomic_json(signal_snapshot, targets)
        _append_once(shadow_dir / "core_shadow_signals.jsonl", targets, keys=("effective_month",))
        written_rows = 0
        for row in rows:
            written_rows += int(_append_once(
                shadow_dir / "core_shadow_mtm.jsonl", row, keys=("price_date", "strategy_id")
            ))
        finished = _now()
        next_expected = finished + timedelta(hours=24)
        payload = {
            "process": "core_shadow_tracker",
            "authority": AUTHORITY,
            "status": "healthy",
            "pid": os.getpid(),
            "last_started_at": started.isoformat(timespec="seconds"),
            "last_success_at": finished.isoformat(timespec="seconds"),
            "last_tick_at": finished.isoformat(timespec="seconds"),
            "next_expected_at": next_expected.isoformat(timespec="seconds"),
            "signal_month": targets["signal_month"],
            "effective_month": targets["effective_month"],
            "price_data_as_of": price_date,
            "last_mtm_at": finished.isoformat(timespec="seconds"),
            "stale": False,
            "stale_reason": "",
            "primary_arms": [arm["strategy_id"] for arm in targets["arms"] if arm["role"] == "primary"],
            "benchmark_arm_count": sum(arm["role"] == "benchmark" for arm in targets["arms"]),
            "mtm_rows_written": written_rows,
            "sources": {"US": us_source, "KR": kr_source, "index": index_source},
        }
        _atomic_json(heartbeat, payload)
        return payload
    except Exception as exc:
        failed = _now()
        payload = {
            "process": "core_shadow_tracker",
            "authority": AUTHORITY,
            "status": "failed",
            "pid": os.getpid(),
            "last_started_at": started.isoformat(timespec="seconds"),
            "last_error_at": failed.isoformat(timespec="seconds"),
            "last_tick_at": failed.isoformat(timespec="seconds"),
            "last_error": str(exc)[:500],
            "stale": True,
            "stale_reason": "run_failed",
        }
        _atomic_json(heartbeat, payload)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified no-order core shadow tracker")
    parser.add_argument("--as-of", default=str(date.today()))
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-sec", type=int, default=21600)
    args = parser.parse_args()
    while True:
        try:
            result = run_once(as_of=args.as_of if not args.loop else str(date.today()))
            print(json.dumps(result, ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=os.sys.stderr)
            if not args.loop:
                return 1
        if not args.loop:
            return 0
        time.sleep(max(300, int(args.interval_sec)))


if __name__ == "__main__":
    raise SystemExit(main())
