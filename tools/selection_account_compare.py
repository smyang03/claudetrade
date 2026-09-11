#!/usr/bin/env python3
"""Read-only, retrospective account comparison; never authorizes live trading.

Inputs are mutable historical caches, NOT point-in-time forward evidence. Optional
output is a new, exclusive directory containing a replayable input snapshot and
reports. This module never imports an order client or opens a source DB writable.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import random
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.virtual_books import contract_exit_v2, bar_complete  # noqa: E402

VERSION = "selection-account-v2"
HOLD = 7


@dataclass(frozen=True)
class Config:
    market: str
    capital: float = 4_320_000
    daily_budget: float = 540_000
    fx: float = 1390.0

    def __post_init__(self):
        if self.market not in {"KR", "US"}:
            raise ValueError("market must be KR or US")
        if any(not math.isfinite(x) or x <= 0 for x in (self.capital, self.daily_budget, self.fx)):
            raise ValueError("capital, daily_budget and fx must be positive finite numbers")

    @property
    def fee(self):
        return .25 if self.market == "KR" else .50

    @property
    def conversion(self):
        return 1.0 if self.market == "KR" else self.fx


@dataclass(frozen=True)
class Candidate:
    ticker: str
    signal_date: str
    entry_date: str
    rank_value: float
    win: list
    ibs: float | None = None
    ma20_disc: float | None = None
    live_rank_value: float | None = None


def simulate(candidates, calendar, cfg, rule, *, seed=0, allowed_days=None):
    """Buy at open, settle at close; exit fees are based on original notional.

    Missing execution data is a visible skip, not a replacement by the next pick.
    Missing subsequent marks carry forward with a stale flag. No overnight cash
    from a same-day closing sale is available to the opening auction.
    """
    if rule not in {"baseline", "live", "random1", "random3", "risk"}:
        raise ValueError("unknown selection rule")
    if not calendar or calendar != sorted(set(calendar)):
        raise ValueError("calendar must be nonempty, unique and ordered")
    rng, pools, keys = random.Random(seed), defaultdict(list), set()
    for c in candidates:
        key = (c.entry_date, c.ticker)
        if key in keys:
            raise ValueError(f"duplicate candidate: {key}")
        keys.add(key)
        if not math.isfinite(c.rank_value):
            raise ValueError("non-finite ranking value")
        if rule == "live" and (c.live_rank_value is None or not math.isfinite(c.live_rank_value)):
            raise ValueError("missing live ranking value")
        if c.signal_date >= c.entry_date:
            raise ValueError("features must precede entry")
        if c.win and (c.win[0][0] != c.entry_date or
                      [b[0] for b in c.win] != sorted({b[0] for b in c.win})):
            raise ValueError("invalid price window")
        if any(not all(math.isfinite(float(x)) and float(x) > 0 for x in b[1:5]) for b in c.win):
            raise ValueError("invalid OHLC")
        pools[c.entry_date].append(c)
    cash, peak, mdd = cfg.capital, cfg.capital, 0.0
    positions, trades, equity, skips = {}, [], [], Counter()
    k = 3 if rule == "random3" else 1
    half = cfg.fee / 200
    for day in calendar:
        available = []
        if allowed_days is None or day in allowed_days:
            for c in sorted(pools[day], key=lambda x: x.ticker):
                if c.ticker in positions:
                    skips["already_held"] += 1
                    continue
                if rule == "risk" and (c.ibs is None or c.ma20_disc is None or
                                       not math.isfinite(c.ibs) or not math.isfinite(c.ma20_disc) or
                                       c.ibs < 50 or c.ma20_disc < -10):
                    skips["risk_rejected"] += 1
                    continue
                available.append(c)
        if rule.startswith("random"):
            selected = rng.sample(available, min(k, len(available)))
        elif rule == "live":
            # KR live buys the deepest MA20 discount (ascending); US live buys the
            # largest drop-day dollar volume among band/MAX survivors (descending).
            selected = sorted(available, key=(
                (lambda c: (c.live_rank_value, c.ticker)) if cfg.market == "KR"
                else (lambda c: (-c.live_rank_value, c.ticker))))[:1]
        else:
            selected = sorted(available, key=lambda c: (-c.rank_value, c.ticker))[:1]
        slots = max(0, HOLD * k - len(positions))
        skips["slot_cap"] += max(0, len(selected) - slots)
        selected = selected[:slots]
        # Divide by actual selections, not always three. Unaffordable allocations
        # stay in cash; no hindsight-dependent replacement or redistribution.
        allocation = min(cash, cfg.daily_budget) / len(selected) if selected else 0
        for c in selected:
            window = [tuple(b) for b in c.win if b[0] <= calendar[-1]][:HOLD]
            if not window:
                skips["missing_entry_bar"] += 1
                continue
            unit = window[0][1] * cfg.conversion
            qty = math.floor((allocation + 1e-9) / (unit * (1 + half)))
            if qty < 1:
                skips["unaffordable"] += 1
                continue
            notional = unit * qty
            cash -= notional * (1 + half)
            t = {"ticker": c.ticker, "signal_date": c.signal_date, "entry_date": day,
                 "qty": qty, "notional": notional, "entry_price": window[0][1],
                 "exit_date": None, "exit_reason": None, "net_pct": -cfg.fee,
                 "mark_date": day, "mature_cohort": len(window) >= HOLD}
            trades.append(t)
            positions[c.ticker] = (t, window)
        marked_value, gross_exposure = 0.0, 0.0
        for ticker, (t, window) in list(positions.items()):
            seen = [b for b in window if b[0] <= day]
            res = contract_exit_v2(t["entry_price"], seen, fee=cfg.fee,
                                   be_lock=cfg.market == "US", hold=HOLD)
            t["mark_date"] = seen[-1][0]
            if res is not None:
                net, reason = res
                t.update(exit_date=day, exit_reason=reason, net_pct=net)
                cash += t["notional"] * (1 + net / 100 + half)
                del positions[ticker]
            else:
                gross = seen[-1][4] / t["entry_price"]
                t["net_pct"] = (gross - 1) * 100 - cfg.fee
                marked_value += t["notional"] * (gross - half)
                gross_exposure += t["notional"] * gross
        nav = cash + marked_value
        if cash < -1e-6 or len(positions) > HOLD * k:
            raise AssertionError("cash/position invariant violated")
        peak = max(peak, nav)
        mdd = min(mdd, (nav / peak - 1) * 100)
        equity.append({"date": day, "cash": cash, "nav": nav,
                       "gross_exposure": gross_exposure, "positions": len(positions)})
    closed = [t for t in trades if t["exit_date"] is not None]
    opened = [t for t in trades if t["exit_date"] is None]
    return {
        "nav": equity[-1]["nav"], "return_pct": (equity[-1]["nav"] / cfg.capital - 1) * 100,
        "mdd_pct": mdd, "entry_notional": sum(t["notional"] for t in trades),
        "buy_days": sorted({t["entry_date"] for t in trades}),
        "closed_count": len(closed), "open_count": len(opened),
        "closed_pnl": sum(t["notional"] * t["net_pct"] / 100 for t in closed),
        "open_pnl": sum(t["notional"] * t["net_pct"] / 100 for t in opened),
        "mature_cohort_count": sum(t["mature_cohort"] for t in trades),
        "stale_open_count": sum(t["mark_date"] < calendar[-1] for t in opened),
        "mean_exposure_pct": statistics.mean(p["gross_exposure"] / cfg.capital * 100 for p in equity),
        "skips": dict(sorted(skips.items())), "trades": trades, "equity": equity,
    }


def compare(candidates, calendar, cfg, *, repeats=200, include_risk=False):
    if repeats < 2:
        raise ValueError("at least two random seeds required")
    strategies = {"baseline": simulate(candidates, calendar, cfg, "baseline"),
                  "live_rule": simulate(candidates, calendar, cfg, "live")}
    if include_risk:
        strategies["risk_diagnostic"] = simulate(candidates, calendar, cfg, "risk")
    for rule, allowed in [("random1", None), ("random3", None)] + (
        [("random1_on_risk_days", set(strategies["risk_diagnostic"]["buy_days"]))] if include_risk else []
    ):
        actual_rule = "random1" if allowed is not None else rule
        runs = [simulate(candidates, calendar, cfg, actual_rule, seed=s, allowed_days=allowed)
                for s in range(repeats)]
        values = sorted(r["return_pct"] for r in runs)
        def percentile(p):
            at = (len(values) - 1) * p
            lo, hi = math.floor(at), math.ceil(at)
            return values[lo] + (values[hi] - values[lo]) * (at - lo)
        # Keep seed-level outcome metrics, not just the best path. Replaying any
        # path uses the saved inputs, config and seed; no new market samples here.
        strategies[rule] = {
            "median_return_pct": statistics.median(values), "p05": percentile(.05), "p95": percentile(.95),
            "positive_runs": sum(v > 0 for v in values),
            "seed_results": [{"seed": s, **{key: r[key] for key in (
                "return_pct", "mdd_pct", "entry_notional", "mean_exposure_pct", "closed_count",
                "open_count", "mature_cohort_count", "stale_open_count", "closed_pnl", "open_pnl", "skips")}}
                for s, r in enumerate(runs)],
        }
    return {
        "version": VERSION, "config": asdict(cfg), "start": calendar[0], "as_of": calendar[-1],
        "candidate_count": len(candidates), "candidate_days": len({c.entry_date for c in candidates}),
        "repeats": repeats, "include_risk": include_risk, "strategies": strategies,
        "decision": {"status": "RESEARCH_ONLY", "forward_eligible_trades": 0,
                     "live_authorized": False,
                     "reasons": ["Retrospective caches are not frozen pre-entry observations.",
                                 "Random seeds are not independent market paths or confidence intervals.",
                                 "Positive net NAV and prospective uncertainty checks are required before review."]},
        "assumptions": ["Same daily maximum budget; integer shares; no duplicate holdings or borrowing.",
                        "TP12/close SL25/prior-peak BE4 US only/D7 inclusive; KR cost .25%, US .50%.",
                        "Open fills are hypothetical; no spread, latency, fill rejection or settlement restrictions.",
                        "US FX is constant, not actual KRW FX-inclusive performance.",
                        "Risk IBS>=50 and MA20 discount>=-10 is a rejected diagnostic, not a new strategy.",
                        "live_rule reproduces the live ordering: KR deepest MA20 discount first "
                        "(kr_fallen_order_bridge), US largest drop-day dollar volume first "
                        "(us_swing_order_bridge with US_SWING_PICK_ORDER=dvol_desc). "
                        "US live_rule is identical to baseline by construction.",
                        "KR live MA20 averages the 20 bars before the signal bar, excluding it "
                        "(kr_fallen_shadow_scan). The ma20_disc diagnostic field includes it.",
                        "Matched dates do not guarantee identical amounts, holding times or exposure.",
                        "Historical input caches may contain revisions and survivorship bias."]}


def read_rows(path, query, params=()):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        return [dict(row) for row in con.execute(query, params).fetchall()]


def read_bars(path, as_of, *, invalid_dates=None):
    rows, counts = [], Counter()
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.reader(fh):
            if not row or row[0].lower() == "date":
                continue
            if row[0] > as_of:
                continue
            counts[row[0]] += 1
            try:
                date.fromisoformat(row[0])
                if len(row) < 6:
                    raise ValueError("short price row")
                nums = [float(x) for x in row[1:6]]
                if (not all(math.isfinite(x) for x in nums) or min(nums[:4]) <= 0 or nums[4] < 0 or
                        nums[1] < max(nums[0], nums[2], nums[3]) - 1e-6 or
                        nums[2] > min(nums[0], nums[1], nums[3]) + 1e-6):
                    raise ValueError("invalid OHLCV")
                rows.append((row[0], *nums))
            except ValueError:
                if invalid_dates is None:
                    raise
                invalid_dates.add(row[0])
    rows.sort()
    duplicates = {d for d, count in counts.items() if count > 1}
    if duplicates:
        if invalid_dates is None:
            raise ValueError("duplicate price dates")
        invalid_dates.update(duplicates)
    if invalid_dates is not None:
        rows = [b for b in rows if b[0] not in invalid_dates]
    return rows


def entry_date(market, session_date, calendar):
    i = bisect.bisect_right(calendar, session_date) if market == "KR" else bisect.bisect_left(calendar, session_date)
    if i >= len(calendar) or (market == "US" and calendar[i] != session_date):
        return None
    return calendar[i]


def prior_features(bars, day):
    prior = [b for b in bars if b[0] < day]
    if len(prior) < 22:
        return None
    _, _, hi, lo, close, volume = prior[-1]
    # Two MA20 windows on purpose. The diagnostic field keeps the original
    # definition; ma20_disc_live matches the live scanner, which averages the 20
    # bars *before* the signal bar and excludes it.
    return {"signal_date": prior[-1][0], "ibs": (close - lo) / (hi - lo) * 100 if hi > lo else None,
            "ma20_disc": (close / statistics.mean(b[4] for b in prior[-20:]) - 1) * 100,
            "ma20_disc_live": (close / statistics.mean(b[4] for b in prior[-21:-1]) - 1) * 100,
            "dvol": close * volume,
            "max21": max((prior[i][4] / prior[i - 1][4] - 1) * 100 for i in range(len(prior) - 21, len(prior)))}


def load_dataset(root, market, start, as_of, universe="live"):
    """Audit all candidate exclusions. Missing future bars must not shift KR entry."""
    root = Path(root)
    price_dir = root / "data" / "price" / market.lower()
    benchmark = "069500" if market == "KR" else "SPY"
    all_days = [b[0] for b in read_bars(price_dir / f"{market.lower()}_{benchmark}.csv", as_of)]
    if not all_days or all_days[-1] != as_of:
        raise ValueError("as-of must equal an available completed benchmark session")
    calendar = [d for d in all_days if start <= d <= as_of]
    if not calendar:
        raise ValueError("empty requested calendar")
    if market == "KR":
        path = root / "data" / "shadow" / "virtual_books.db"
        previous = all_days[max(0, bisect.bisect_left(all_days, start) - 1)]
        rows = read_rows(path, "SELECT session_date,ticker,backfill FROM trades WHERE strategy_id=? "
                         "AND session_date>=? AND session_date<? ORDER BY session_date,ticker",
                         ("xkr_fallen3", previous, as_of))
    else:
        path = root / "data" / "analysis" / "us_swing_shadow.db"
        rows = read_rows(path, "SELECT session_date,ticker,dollar_vol,in_pool,recorded_at FROM candidate_pool_all "
                         "WHERE eligible=1 AND session_date>=? AND session_date<=? " +
                         ("AND in_pool=1 " if universe == "live" else "") + "ORDER BY session_date,ticker",
                         (start, as_of))
    counts, issues, cache, candidates = Counter(source_rows=len(rows)), [], {}, []
    def issue(row, reason):
        counts[reason] += 1
        issues.append({"session_date": row["session_date"], "ticker": row["ticker"], "reason": reason})
    for row in rows:
        ticker = str(row["ticker"]).upper()
        if not ticker or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-^" for ch in ticker):
            issue(row, "invalid_ticker")
            continue
        day = entry_date(market, row["session_date"], all_days)
        if day is None or day < start:
            issue(row, "outside_entry_period")
            continue
        if ticker not in cache:
            try:
                # Validate rows separately: future bad data must not exclude a
                # ticker before entry and thereby promote the next-ranked pick.
                cache[ticker] = read_bars(price_dir / f"{market.lower()}_{ticker}.csv", as_of,
                                          invalid_dates=set())
            except (OSError, ValueError) as exc:
                cache[ticker] = type(exc).__name__
        bars = cache[ticker]
        if isinstance(bars, str):
            issue(row, "missing_or_invalid_price")
            continue
        f = prior_features(bars, day)
        if f is None:
            issue(row, "short_feature_history")
            continue
        prior_index = bisect.bisect_left(all_days, day) - 1
        if prior_index < 0 or f["signal_date"] != all_days[prior_index]:
            issue(row, "stale_signal_price")
            continue
        expected_history = all_days[max(0, prior_index - 21):prior_index + 1]
        actual_history = [b[0] for b in bars if b[0] < day][-22:]
        if len(expected_history) != 22 or actual_history != expected_history:
            issue(row, "feature_window_gap")
            continue
        if market == "US" and not (100_000_000 <= f["dvol"] < 500_000_000 and f["max21"] >= 8):
            counts["outside_us_band_max"] += 1
            continue
        expected = all_days[bisect.bisect_left(all_days, day):][:HOLD]
        by_date = {b[0]: b for b in bars}
        win = []
        for d in expected:
            if d not in by_date:
                issue(row, "missing_entry_bar" if not win else "price_path_gap")
                break
            win.append(by_date[d])
        live_rank = f["ma20_disc_live"] if market == "KR" else f["dvol"]
        candidates.append(Candidate(ticker, f["signal_date"], day, f["dvol"], win, f["ibs"],
                                    f["ma20_disc"], live_rank))
    counts["prepared_candidates"] = len(candidates)
    observed_days = {entry_date(market, row["session_date"], all_days) for row in rows}
    unverified = [d for d in calendar if d not in observed_days]
    audit = {"counts": dict(counts), "issues": issues, "source": str(path), "universe": universe,
             "coverage_complete": not issues and not unverified,
             "source_sessions_unverified": unverified, "requested_start": start, "as_of": as_of,
             "source_rows_sha256": digest(rows), "forward_eligible_trades": 0,
             "notes": ["All observations are retrospective, including rows labelled backfill=0 upstream.",
                       "KR universe is recorded xkr_fallen3 trades, not a complete pre-entry candidate archive; ETFs may be included.",
                       "US band/MAX and rankings use prior-bar recomputation, not guaranteed original decision-time features.",
                       "live_rule re-ranks this same pool; it does not reproduce the live lane's own universe.",
                       "Missing source/feature data changes the available pool; incomplete coverage prevents inference."]}
    audit["notes"].append("Absent source sessions are unknown, not proven zero-candidate days. Coverage covers observable inputs only.")
    return candidates, calendar, audit


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()


def code_fingerprints():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        Path(__file__), ROOT / "tools" / "virtual_books.py")}


def markdown_report(report):
    rows = ["# [VIRTUAL / RESEARCH ONLY] Selection account comparison", "",
            f"Period: {report['start']} through {report['as_of']}. Market: {report['config']['market']}.",
            "", "Retrospective only. No live authorization. Random bands are not confidence intervals.", "",
            "| Method | NAV return % | MDD % | Closed / open |", "|---|---:|---:|---:|"]
    for name, value in report["strategies"].items():
        if "return_pct" in value:
            rows.append(f"| {name} | {value['return_pct']:.4f} | {value['mdd_pct']:.4f} | {value['closed_count']} / {value['open_count']} |")
        else:
            seeds = value["seed_results"]
            rows.append(f"| {name} (median) | {value['median_return_pct']:.4f} | "
                        f"{statistics.median(r['mdd_pct'] for r in seeds):.4f} | see JSON |")
    rows += ["", "## Assumptions", ""] + [f"- {a}" for a in report["assumptions"]]
    if "audit" in report:
        rows += ["", "## Coverage", "", f"Complete: {report['audit']['coverage_complete']}", "",
                 "```json", json.dumps(report["audit"]["counts"], indent=2), "```", ""]
        rows += [f"- {n}" for n in report["audit"]["notes"]]
    return "\n".join(rows) + "\n"


def save_run(directory, candidates, calendar, cfg, audit, report):
    directory = Path(directory)
    payload = {"version": VERSION, "candidates": [asdict(c) for c in candidates], "calendar": calendar,
               "config": asdict(cfg), "audit": audit, "code_sha256": code_fingerprints()}
    snapshot = {"payload": payload, "sha256": digest(payload)}
    # Never overwrite source data, previous runs, or unrelated existing paths.
    directory.mkdir(parents=True, exist_ok=False)
    artifacts = {"inputs.json": snapshot, "report.json": report}
    for name, value in artifacts.items():
        with (directory / name).open("x", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, allow_nan=False, indent=2)
            fh.write("\n")
    with (directory / "report.md").open("x", encoding="utf-8") as fh:
        fh.write(markdown_report(report))


def read_snapshot(path):
    snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    p = snapshot["payload"]
    if snapshot["sha256"] != digest(p) or p["version"] != VERSION:
        raise ValueError("snapshot hash/version mismatch")
    if p["code_sha256"] != code_fingerprints():
        raise ValueError("code changed since snapshot; replay with original code")
    return [Candidate(**c) for c in p["candidates"]], p["calendar"], Config(**p["config"]), p["audit"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--market", choices=["KR", "US"])
    parser.add_argument("--start")
    parser.add_argument("--as-of")
    parser.add_argument("--universe", choices=["live", "wide"], default="live")
    parser.add_argument("--capital", type=float, default=4_320_000)
    parser.add_argument("--daily-budget", type=float, default=540_000)
    parser.add_argument("--fx", type=float, default=1390)
    parser.add_argument("--repeats", type=int, default=200)
    parser.add_argument("--include-risk-diagnostic", action="store_true")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.snapshot:
            cs, calendar, cfg, audit = read_snapshot(args.snapshot)
        else:
            if not all([args.market, args.start, args.as_of]):
                parser.error("--market, --start and --as-of are required without --snapshot")
            date.fromisoformat(args.start)
            date.fromisoformat(args.as_of)
            if args.start > args.as_of or not bar_complete(args.as_of, args.market):
                parser.error("invalid period or as-of session not yet complete")
            cfg = Config(args.market, args.capital, args.daily_budget, args.fx)
            cs, calendar, audit = load_dataset(args.root, args.market, args.start, args.as_of, args.universe)
        report = compare(cs, calendar, cfg, repeats=args.repeats, include_risk=args.include_risk_diagnostic)
        report["audit"] = audit
        if not audit["coverage_complete"]:
            report["decision"]["reasons"].append("Incomplete coverage: do not infer selection superiority.")
        if args.output_dir:
            save_run(args.output_dir, cs, calendar, cfg, audit, report)
        print(markdown_report(report))
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"comparison failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
