#!/usr/bin/env python3
from __future__ import annotations

"""Reproducible read-only lab for creative profitability candidates.

The lab deliberately keeps two very different questions separate:

1. Can agreement between the mechanical signal family and Claude's recommended
   family support a fixed-horizon US swing challenger?
2. Can entry-time microstructure reduce the PathB falling-knife loss budget
   without pretending that smaller exposure is a new source of alpha?

No live configuration, order path, or database is modified.
"""

import argparse
import csv
import json
import math
import sqlite3
import statistics as st
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SELECTION_DB = ROOT / "data" / "ticker_selection_log.db"
DECISIONS_DB = ROOT / "data" / "ml" / "decisions.db"
PRICE_DIR = {"US": ROOT / "data" / "price" / "us", "KR": ROOT / "data" / "price" / "kr"}
MINUTE_DIR = {
    "US": ROOT / "data" / "price" / "minute" / "us",
    "KR": ROOT / "data" / "price" / "minute" / "kr",
}
COST_PCT = {"US": 0.50, "KR": 0.21}


def ro_connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def profit_factor(values: Iterable[float]) -> float | None:
    values = list(values)
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    return gains / losses if losses > 0 else None


def max_drawdown(values: Iterable[float]) -> float:
    equity = peak = worst = 0.0
    for value in values:
        equity += float(value)
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def block_lcb(values: list[float], *, seed: int, samples: int = 3000, block: int = 5) -> float | None:
    if len(values) < 25:
        return None
    import random

    rng = random.Random(seed)
    starts = list(range(max(1, len(values) - block + 1)))
    needed = int(math.ceil(len(values) / block))
    means: list[float] = []
    for _ in range(samples):
        sample: list[float] = []
        for _ in range(needed):
            start = rng.choice(starts)
            sample.extend(values[start : start + block])
        means.append(st.mean(sample[: len(values)]))
    means.sort()
    return means[int(0.05 * (len(means) - 1))]


def metrics(values: Iterable[float], *, seed: int = 20260715) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean:
        return {"n": 0}
    ordered = sorted(clean, reverse=True)
    return {
        "n": len(clean),
        "mean_pct": round(st.mean(clean), 4),
        "median_pct": round(st.median(clean), 4),
        "sum_pct_units": round(sum(clean), 4),
        "win_rate": round(sum(value > 0 for value in clean) / len(clean), 4),
        "profit_factor": round(profit_factor(clean), 4) if profit_factor(clean) is not None else None,
        "max_drawdown_pct_units": round(max_drawdown(clean), 4),
        "sum_ex_top1_pct_units": round(sum(ordered[1:]), 4) if len(ordered) > 1 else None,
        "sum_ex_top3_pct_units": round(sum(ordered[3:]), 4) if len(ordered) > 3 else None,
        "block_mean_lcb_5pct": round(block_lcb(clean, seed=seed), 4) if len(clean) >= 25 else None,
    }


def read_daily_prices(market: str, ticker: str) -> list[dict[str, Any]]:
    path = PRICE_DIR[market] / f"{market.lower()}_{ticker}.csv"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                rows.append({"date": str(row["date"]), "open": float(row["open"]), "close": float(row["close"])})
            except (KeyError, TypeError, ValueError):
                continue
    rows.sort(key=lambda row: row["date"])
    return rows


def load_signal_rows() -> list[dict[str, Any]]:
    connection = ro_connect(SELECTION_DB)
    rows = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM ticker_selection_log "
            "WHERE signal_fired=1 AND bot_mode='live' ORDER BY id"
        )
    ]
    connection.close()
    # A ticker can be emitted repeatedly in a session.  Only the latest
    # observable row is retained, preventing duplicate forward trades.
    latest = {(str(row["market"]), str(row["date"]), str(row["ticker"])): row for row in rows}
    return list(latest.values())


def build_consensus_ledger() -> list[dict[str, Any]]:
    cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    output: list[dict[str, Any]] = []
    for row in load_signal_rows():
        market = str(row.get("market") or "").upper()
        ticker = str(row.get("ticker") or "").upper()
        if market not in PRICE_DIR or not ticker:
            continue
        key = (market, ticker)
        cache.setdefault(key, read_daily_prices(market, ticker))
        prices = cache[key]
        next_indexes = [idx for idx, item in enumerate(prices) if item["date"] > str(row["date"])]
        if not next_indexes:
            continue
        entry_index = next_indexes[0]
        strategy = str(row.get("strategy_name") or "").strip().lower()
        recommended = str(row.get("recommended_strategy") or "").strip().lower()
        item: dict[str, Any] = {
            "signal_date": str(row["date"]),
            "market": market,
            "ticker": ticker,
            "strategy_name": strategy,
            "recommended_strategy": recommended,
            "strategy_agreement": int(bool(strategy) and strategy == recommended),
            "selection_rank": row.get("selection_rank"),
            "entry_priority_score": row.get("entry_priority_score"),
            "change_pct": row.get("change_pct"),
            "entry_date": prices[entry_index]["date"],
            "entry_price": prices[entry_index]["open"],
        }
        for hold in (1, 3, 5):
            exit_index = entry_index + hold - 1
            if exit_index >= len(prices):
                continue
            item[f"exit_date_{hold}d"] = prices[exit_index]["date"]
            item[f"net_{hold}d_pct"] = (
                (prices[exit_index]["close"] / prices[entry_index]["open"] - 1.0) * 100.0
                - COST_PCT[market]
            )
        output.append(item)
    output.sort(key=lambda row: (row["entry_date"], row["market"], row["ticker"]))
    return output


def one_slot(rows: list[dict[str, Any]], hold: int) -> list[dict[str, Any]]:
    """Select one known-at-entry candidate and prohibit overlapping holdings."""

    by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get(f"net_{hold}d_pct") is not None:
            by_entry[str(row["entry_date"])].append(row)
    daily: list[dict[str, Any]] = []
    for entry_date, candidates in by_entry.items():
        candidates.sort(
            key=lambda row: (
                int(row["selection_rank"]) if row.get("selection_rank") is not None else 999999,
                -float(row["entry_priority_score"]) if row.get("entry_priority_score") is not None else 999999.0,
                str(row["ticker"]),
            )
        )
        daily.append(candidates[0])
    daily.sort(key=lambda row: (row["entry_date"], row["ticker"]))
    accepted: list[dict[str, Any]] = []
    last_exit = ""
    for row in daily:
        if str(row["entry_date"]) > last_exit:
            accepted.append(row)
            last_exit = str(row[f"exit_date_{hold}d"])
    return accepted


def period_groups(rows: list[dict[str, Any]], date_key: str) -> dict[str, list[dict[str, Any]]]:
    return {
        "all": rows,
        "discovery_april": [row for row in rows if str(row[date_key]) < "2026-05-01"],
        "oos_may_plus": [row for row in rows if str(row[date_key]) >= "2026-05-01"],
    }


def micro_period_groups(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Use the actual minute-ledger coverage boundary, not the signal split."""

    return {
        "all": rows,
        "early_through_may": [row for row in rows if str(row["session_date"]) < "2026-06-01"],
        "late_june_july": [row for row in rows if str(row["session_date"]) >= "2026-06-01"],
    }


def consensus_report(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for market in ("US", "KR"):
        market_rows = [row for row in ledger if row["market"] == market]
        groups = {
            "strategy_agreement": [row for row in market_rows if row["strategy_agreement"]],
            "other_fired": [row for row in market_rows if not row["strategy_agreement"]],
            "goldilocks_change_7_12": [
                row
                for row in market_rows
                if row.get("change_pct") is not None and 7.0 <= float(row["change_pct"]) <= 12.0
            ],
        }
        market_report: dict[str, Any] = {}
        for name, rows in groups.items():
            group_report: dict[str, Any] = {}
            for period, period_rows in period_groups(rows, "signal_date").items():
                horizons: dict[str, Any] = {}
                for hold in (1, 3, 5):
                    eligible = [row for row in period_rows if row.get(f"net_{hold}d_pct") is not None]
                    slot_rows = one_slot(eligible, hold)
                    horizons[f"{hold}d"] = {
                        "independent_signals": metrics(
                            [row[f"net_{hold}d_pct"] for row in eligible], seed=20260715 + hold
                        ),
                        "one_concurrent_slot": metrics(
                            [row[f"net_{hold}d_pct"] for row in slot_rows], seed=20260725 + hold
                        ),
                    }
                group_report[period] = horizons
            group_report["by_month_3d"] = {
                month: metrics(
                    [row["net_3d_pct"] for row in rows if str(row["signal_date"]).startswith(month)],
                    seed=20260801,
                )
                for month in sorted({str(row["signal_date"])[:7] for row in rows})
            }
            market_report[name] = group_report
        report[market] = market_report
    return report


def read_minute_prices(market: str, ticker: str) -> list[tuple[datetime, float, float]]:
    path = MINUTE_DIR[market] / f"{market.lower()}_{ticker}.csv"
    if not path.exists():
        return []
    output: list[tuple[datetime, float, float]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            stamp = parse_dt(row.get("ts"))
            try:
                close = float(row["close"])
                volume = float(row.get("volume") or 0.0)
            except (KeyError, TypeError, ValueError):
                continue
            if stamp is not None:
                output.append((stamp, close, volume))
    output.sort(key=lambda item: item[0])
    return output


def load_live_trades() -> list[dict[str, Any]]:
    connection = ro_connect(DECISIONS_DB)
    rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT v2_decision_id, market, session_date, ticker, filled_at, closed_at,
                   entry_price, exit_price, pnl_pct_net, fee_pct_round_trip, qty, close_reason
            FROM v2_learning_performance
            WHERE runtime_mode='live' AND filled=1 AND closed=1 AND portfolio_realized=1
              AND filled_at IS NOT NULL AND closed_at IS NOT NULL
              AND entry_price>0 AND exit_price>0 AND pnl_pct_net IS NOT NULL
            ORDER BY filled_at, v2_decision_id
            """
        )
    ]
    connection.close()
    return rows


def staged_net(
    *,
    actual_net: float,
    entry_price: float,
    exit_price: float,
    fee_pct: float,
    residual_pct: float,
    add_price: float | None,
    probe_fraction: float = 1.0 / 3.0,
) -> tuple[float, float]:
    """Return offered-trade net and deployed exposure units.

    ``residual_pct`` aligns local-price return with the recorded broker/net
    ledger, retaining observed FX and legacy accounting residuals.
    """

    if add_price is None:
        return actual_net * probe_fraction, probe_fraction
    add_net = (exit_price / add_price - 1.0) * 100.0 - fee_pct + residual_pct
    add_fraction = 1.0 - probe_fraction
    return actual_net * probe_fraction + add_net * add_fraction, 1.0


def build_microstate_ledger() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trades = load_live_trades()
    cache: dict[tuple[str, str], list[tuple[datetime, float, float]]] = {}
    output: list[dict[str, Any]] = []
    for row in trades:
        market = str(row["market"]).upper()
        ticker = str(row["ticker"]).upper()
        key = (market, ticker)
        cache.setdefault(key, read_minute_prices(market, ticker))
        bars = cache[key]
        filled = parse_dt(row["filled_at"])
        closed = parse_dt(row["closed_at"])
        if not bars or filled is None or closed is None:
            continue
        minute_floor = filled.replace(second=0, microsecond=0)
        # A bar stamped 14:20 is only usable after that minute has closed.
        # Excluding the fill minute prevents pre-entry look-ahead.
        pre = [
            bar
            for bar in bars
            if minute_floor - timedelta(minutes=20) <= bar[0] < minute_floor
        ]
        post = [
            bar
            for bar in bars
            if minute_floor <= bar[0] <= min(closed, filled + timedelta(minutes=60))
        ]
        if len(pre) < 6 or not post:
            continue
        closes = [bar[1] for bar in pre]
        volumes = [bar[2] for bar in pre]
        vwap_volume = sum(volumes[-10:])
        vwap10 = (
            sum(price * volume for price, volume in zip(closes[-10:], volumes[-10:])) / vwap_volume
            if vwap_volume > 0
            else st.mean(closes[-10:])
        )
        pre5 = (closes[-1] / closes[-6] - 1.0) * 100.0
        down_count3 = sum(closes[idx] < closes[idx - 1] for idx in range(len(closes) - 3, len(closes)))
        actual_net = float(row["pnl_pct_net"])
        fee = float(row.get("fee_pct_round_trip") or COST_PCT[market])
        local_net = (float(row["exit_price"]) / float(row["entry_price"]) - 1.0) * 100.0 - fee
        residual = actual_net - local_net
        item: dict[str, Any] = {
            **row,
            "actual_net_pct": actual_net,
            "prefill_ret_5m_pct": pre5,
            "prefill_below_vwap10": int(closes[-1] < vwap10),
            "prefill_down_count3": down_count3,
            "net_alignment_residual_pct": residual,
        }
        for threshold in (-0.3, -0.5, -0.7, -1.0):
            suffix = str(abs(threshold)).replace(".", "p")
            item[f"prefill_fall_{suffix}"] = int(
                pre5 <= threshold and closes[-1] < vwap10 and down_count3 >= 2
            )
        for window in (15, 30, 60):
            for trigger in (0.3, 0.5, 0.7, 1.0):
                add_price: float | None = None
                deadline = filled + timedelta(minutes=window)
                for stamp, close, _ in post:
                    if stamp > deadline:
                        break
                    if close >= float(row["entry_price"]) * (1.0 + trigger / 100.0):
                        add_price = close
                        break
                suffix = str(trigger).replace(".", "p")
                policy_net, exposure = staged_net(
                    actual_net=actual_net,
                    entry_price=float(row["entry_price"]),
                    exit_price=float(row["exit_price"]),
                    fee_pct=fee,
                    residual_pct=residual,
                    add_price=add_price,
                )
                item[f"probe_confirmed_w{window}_t{suffix}"] = int(add_price is not None)
                item[f"probe_net_w{window}_t{suffix}_pct"] = policy_net
                item[f"probe_exposure_w{window}_t{suffix}"] = exposure
        output.append(item)
    coverage = {
        "source_trade_n": len(trades),
        "minute_covered_n": len(output),
        "coverage_rate": round(len(output) / len(trades), 4) if trades else None,
        "by_market": {
            market: {
                "source_n": sum(str(row["market"]).upper() == market for row in trades),
                "covered_n": sum(str(row["market"]).upper() == market for row in output),
            }
            for market in ("US", "KR")
        },
    }
    return output, coverage


def exposure_metrics(rows: list[dict[str, Any]], net_key: str, exposure_key: str) -> dict[str, Any]:
    result = metrics([row[net_key] for row in rows], seed=20260716)
    exposure = sum(float(row[exposure_key]) for row in rows)
    result["exposure_units"] = round(exposure, 4)
    result["net_per_exposure_pct"] = round(
        sum(float(row[net_key]) for row in rows) / exposure, 4
    ) if exposure > 0 else None
    return result


def microstate_report(ledger: list[dict[str, Any]], coverage: dict[str, Any]) -> dict[str, Any]:
    report: dict[str, Any] = {"coverage": coverage, "markets": {}}
    for market in ("US", "KR"):
        market_rows = [row for row in ledger if str(row["market"]).upper() == market]
        market_report: dict[str, Any] = {"baseline": metrics([row["actual_net_pct"] for row in market_rows])}
        prefill: dict[str, Any] = {}
        for threshold in (-0.3, -0.5, -0.7, -1.0):
            suffix = str(abs(threshold)).replace(".", "p")
            flag = f"prefill_fall_{suffix}"
            flagged = [row for row in market_rows if row[flag]]
            prefill[str(threshold)] = {
                "flagged_actual": metrics([row["actual_net_pct"] for row in flagged]),
                "skip_policy": metrics(
                    [0.0 if row[flag] else row["actual_net_pct"] for row in market_rows]
                ),
            }
        market_report["prefill_fallthrough_sensitivity"] = prefill
        probes: dict[str, Any] = {}
        for window in (15, 30, 60):
            for trigger in (0.3, 0.5, 0.7, 1.0):
                suffix = str(trigger).replace(".", "p")
                net_key = f"probe_net_w{window}_t{suffix}_pct"
                exposure_key = f"probe_exposure_w{window}_t{suffix}"
                confirm_key = f"probe_confirmed_w{window}_t{suffix}"
                probes[f"w{window}_t{trigger}"] = {
                    **exposure_metrics(market_rows, net_key, exposure_key),
                    "confirmed_n": sum(int(row[confirm_key]) for row in market_rows),
                }
        market_report["probe_confirm_sensitivity"] = probes
        market_report["central_policy_periods"] = {}
        for period, rows in micro_period_groups(market_rows).items():
            market_report["central_policy_periods"][period] = {
                "baseline": metrics([row["actual_net_pct"] for row in rows]),
                "prefill_skip_m0p5": metrics(
                    [0.0 if row["prefill_fall_0p5"] else row["actual_net_pct"] for row in rows]
                ),
                "probe_w15_t0p7": exposure_metrics(
                    rows, "probe_net_w15_t0p7_pct", "probe_exposure_w15_t0p7"
                ),
            }
        report["markets"][market] = market_report
    return report


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    columns = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=str(date.today()))
    parser.add_argument("--output-dir", default=str(ROOT / "reports"))
    args = parser.parse_args()
    tag = args.as_of.replace("-", "")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    consensus = build_consensus_ledger()
    microstate, coverage = build_microstate_ledger()
    report = {
        "as_of": args.as_of,
        "authority": "SHADOW_ONLY_READ_ONLY_ANALYSIS",
        "contracts": {
            "consensus": "signal date D; next session open entry; fixed 1/3/5 session close; US/KR cost 0.50/0.21",
            "consensus_oos": "April discovery; May+ OOS; independent and one-concurrent-slot both reported",
            "prefill": "only fully completed minute bars strictly before the fill minute",
            "probe": "one-third at actual fill; add two-thirds at first qualifying minute close; actual exit owner retained",
            "net_alignment": "counterfactual local return plus recorded net/local residual",
        },
        "consensus": consensus_report(consensus),
        "microstate": microstate_report(microstate, coverage),
    }
    json_path = output_dir / f"creative_profit_blueprint_lab_{tag}.json"
    consensus_path = output_dir / f"creative_profit_consensus_ledger_{tag}.csv"
    microstate_path = output_dir / f"creative_profit_microstate_ledger_{tag}.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(consensus_path, consensus)
    write_csv(microstate_path, microstate)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"WROTE {json_path}")
    print(f"WROTE {consensus_path}")
    print(f"WROTE {microstate_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
