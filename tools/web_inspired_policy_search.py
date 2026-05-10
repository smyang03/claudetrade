from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search KR/US-positive policy combinations inspired by common intraday trading playbooks."
    )
    parser.add_argument("--stamp", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--output-dir", default=str(ROOT / "docs" / "reports"))
    args = parser.parse_args()

    closed = load_closed_with_entries(ROOT / "state" / "live_decisions.jsonl")
    selection_trades = load_selection_trades(ROOT / "data" / "ticker_selection_log.db")
    intraday_summary = load_intraday_summary(ROOT / "data" / "intraday_strategy_log.db")

    closed_result = search_closed_trade_policies(closed)
    selection_result = search_selection_policies(selection_trades)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "basis": {
            "mode": "local_simulation_only_no_new_claude_no_broker_calls",
            "closed_trades": len(closed),
            "selection_trades": len(selection_trades),
            "intraday_summary": intraday_summary,
            "web_inspired_rule_families": [
                "opening-range confirmation: no immediate KR open chase; cap first signals per market/day",
                "VWAP/volume confirmation: approximated with available liquidity/from-high/change fields because VWAP is not logged",
                "pullback/false-breakout control: demote KR at-high or high-change entries unless confirmed",
                "risk-first exits: tight KR loss cap plus MFE preservation; keep US current overlay unless it degrades",
            ],
            "limits": [
                "This is a policy search on a small recent sample; positive combinations can be overfit.",
                "Forward labels are not used as live gates. They are used only to evaluate candidate quality.",
                "Sell overlays are approximate because tick-level replay and guaranteed stop fills are unavailable.",
                "VWAP was not present in intraday logs, so VWAP rules are recommendations for instrumentation, not fully replayed rules.",
            ],
        },
        "closed_trade_policy_search": closed_result,
        "selection_trade_policy_search": selection_result,
        "recommended_policy_stack": recommended_stack(closed_result, selection_result),
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"web_inspired_policy_search_{args.stamp}.json"
    md_path = output_dir / f"web_inspired_policy_search_{args.stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(payload), encoding="utf-8")
    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    return 0


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


def load_closed_with_entries(path: Path) -> list[dict[str, Any]]:
    events = load_jsonl(path)
    entries: dict[tuple[str, str], deque[dict[str, Any]]] = defaultdict(deque)
    closed: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") == "entry":
            row = dict(event)
            row["_dt"] = parse_dt(row.get("timestamp"))
            entries[(str(row.get("market") or ""), str(row.get("ticker") or ""))].append(row)
            continue
        if event.get("type") != "closed" or event.get("pnl_pct") is None:
            continue
        row = dict(event)
        row["_dt"] = parse_dt(row.get("timestamp"))
        row["_date"] = str(row.get("session_date") or str(row.get("timestamp") or "")[:10])
        key = (str(row.get("market") or ""), str(row.get("ticker") or ""))
        entry = entries[key].popleft() if entries[key] else None
        row["_entry"] = entry
        row["_entry_dt"] = entry.get("_dt") if entry else None
        row["_entry_hour"] = hour_float(row["_entry_dt"]) if row["_entry_dt"] else None
        row["_idx"] = len(closed)
        closed.append(row)
    return closed


def load_selection_trades(db_path: Path) -> list[dict[str, Any]]:
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
                  AND traded=1
                  AND pnl_pct IS NOT NULL
                ORDER BY COALESCE(traded_at, signal_at, selected_at), market, ticker
                """
            )
        ]
    finally:
        conn.close()
    for idx, row in enumerate(rows):
        row["_idx"] = idx
        row["_dt"] = parse_dt(row.get("traded_at") or row.get("signal_at") or row.get("selected_at"))
    return rows


def load_intraday_summary(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    market,
                    stage,
                    bot_mode,
                    COUNT(*) AS rows,
                    SUM(CASE WHEN or_formed=1 THEN 1 ELSE 0 END) AS or_formed,
                    SUM(CASE WHEN vwap IS NOT NULL THEN 1 ELSE 0 END) AS vwap_rows,
                    SUM(CASE WHEN pnl_pct IS NOT NULL THEN 1 ELSE 0 END) AS pnl_rows,
                    MIN(session_date) AS date_min,
                    MAX(session_date) AS date_max
                FROM intraday_strategy_log
                GROUP BY market, stage, bot_mode
                ORDER BY market, stage, bot_mode
                """
            )
        ]
    finally:
        conn.close()
    return {"groups": rows}


def search_closed_trade_policies(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filters = closed_filters(rows)
    overlays = closed_sell_overlays()
    hits = []
    trials = 0
    for filter_name, pred, filter_note in filters:
        kept = [row for row in rows if pred(row)]
        for overlay_name, overlay, overlay_note in overlays:
            trials += 1
            all_m = closed_metrics(kept, overlay)
            kr_m = closed_metrics([row for row in kept if row.get("market") == "KR"], overlay)
            us_m = closed_metrics([row for row in kept if row.get("market") == "US"], overlay)
            if kr_m["n"] > 0 and us_m["n"] > 0 and kr_m["pnl_krw"] > 0 and us_m["pnl_krw"] > 0:
                hits.append(
                    {
                        "filter": filter_name,
                        "filter_note": filter_note,
                        "overlay": overlay_name,
                        "overlay_note": overlay_note,
                        "kept": len(kept),
                        "removed": len(rows) - len(kept),
                        "all": all_m,
                        "KR": kr_m,
                        "US": us_m,
                        "KR_strategy_counts": dict(Counter(str(row.get("strategy") or "") for row in kept if row.get("market") == "KR").most_common()),
                    }
                )
    hits.sort(key=lambda row: (row["all"]["pnl_krw"], row["KR"]["pnl_krw"], row["US"]["pnl_krw"]), reverse=True)
    return {
        "trials": trials,
        "positive_both_market_hits": len(hits),
        "top_hits": hits[:40],
        "baseline": {
            "actual": {
                "all": closed_metrics(rows, lambda row: safe_float(row.get("pnl_pct"))),
                "KR": closed_metrics([row for row in rows if row.get("market") == "KR"], lambda row: safe_float(row.get("pnl_pct"))),
                "US": closed_metrics([row for row in rows if row.get("market") == "US"], lambda row: safe_float(row.get("pnl_pct"))),
            },
            "current_sell": {
                "all": closed_metrics(rows, current_overlay),
                "KR": closed_metrics([row for row in rows if row.get("market") == "KR"], current_overlay),
                "US": closed_metrics([row for row in rows if row.get("market") == "US"], current_overlay),
            },
        },
    }


def search_selection_policies(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filters = selection_filters(rows)
    caps: list[tuple[str, float | None]] = [
        ("actual_exit", None),
        ("cap3", 3.0),
        ("cap2", 2.0),
        ("cap1_5", 1.5),
        ("cap1_2", 1.2),
        ("cap1_0", 1.0),
    ]
    hits = []
    trials = 0
    for filter_name, pred, filter_note in filters:
        kept = [row for row in rows if pred(row)]
        for cap_name, cap in caps:
            trials += 1
            all_m = pct_metrics(kept, cap)
            kr_m = pct_metrics([row for row in kept if row.get("market") == "KR"], cap)
            us_m = pct_metrics([row for row in kept if row.get("market") == "US"], cap)
            if kr_m["n"] > 0 and us_m["n"] > 0 and kr_m["sum_pct"] > 0 and us_m["sum_pct"] > 0:
                hits.append(
                    {
                        "filter": filter_name,
                        "filter_note": filter_note,
                        "exit": cap_name,
                        "kept": len(kept),
                        "removed": len(rows) - len(kept),
                        "all": all_m,
                        "KR": kr_m,
                        "US": us_m,
                        "strategy_counts": dict(Counter(f"{row.get('market')}|{row.get('strategy_name') or ''}" for row in kept).most_common()),
                    }
                )
    hits.sort(key=lambda row: (row["all"]["sum_pct"], row["KR"]["sum_pct"], row["US"]["sum_pct"]), reverse=True)
    return {
        "trials": trials,
        "positive_both_market_hits": len(hits),
        "top_hits": hits[:40],
        "baseline": {
            "actual": {
                "all": pct_metrics(rows, None),
                "KR": pct_metrics([row for row in rows if row.get("market") == "KR"], None),
                "US": pct_metrics([row for row in rows if row.get("market") == "US"], None),
            },
            "cap1_5": {
                "all": pct_metrics(rows, 1.5),
                "KR": pct_metrics([row for row in rows if row.get("market") == "KR"], 1.5),
                "US": pct_metrics([row for row in rows if row.get("market") == "US"], 1.5),
            },
        },
    }


def closed_filters(rows: list[dict[str, Any]]) -> list[tuple[str, Callable[[dict[str, Any]], bool], str]]:
    out: list[tuple[str, Callable[[dict[str, Any]], bool], str]] = [
        ("all", lambda row: True, "Keep every historical closed trade."),
        ("KR_momentum_US_all", lambda row: row.get("market") == "US" or row.get("strategy") == "momentum", "KR only momentum; keep all US."),
        (
            "KR_no_continuation_US_all",
            lambda row: row.get("market") == "US" or row.get("strategy") != "continuation",
            "Block old KR continuation tail; keep all US.",
        ),
        (
            "KR_no_continuation_claude_price_US_all",
            lambda row: row.get("market") == "US" or row.get("strategy") not in {"continuation", "claude_price"},
            "Block KR continuation and claude_price paths; keep all US.",
        ),
        (
            "KR_momentum_gap_pullback_US_all",
            lambda row: row.get("market") == "US" or row.get("strategy") in {"momentum", "gap_pullback"},
            "KR only momentum/gap_pullback; keep all US.",
        ),
        (
            "KR_entry_after_10_US_all",
            lambda row: row.get("market") == "US" or (row.get("_entry_hour") is not None and row["_entry_hour"] >= 10.0),
            "Opening confirmation proxy: no KR entry before 10:00.",
        ),
        (
            "KR_entry_0930_1400_US_all",
            lambda row: row.get("market") == "US"
            or (row.get("_entry_hour") is not None and 9.5 <= row["_entry_hour"] <= 14.0),
            "Avoid first 30 minutes and late-day KR entries when entry timestamp exists.",
        ),
    ]
    for n in (1, 2, 3):
        out.append((f"first{n}_per_market", first_n_closed(rows, n, market=None), f"Keep first {n} trades per market/day."))
        out.append((f"KR_first{n}_US_all", first_n_closed(rows, n, market="KR"), f"Keep only first {n} KR trades per day; keep all US."))
    return out


def selection_filters(rows: list[dict[str, Any]]) -> list[tuple[str, Callable[[dict[str, Any]], bool], str]]:
    def num(row: dict[str, Any], key: str) -> float | None:
        raw = row.get(key)
        if raw is None or raw == "":
            return None
        return safe_float(raw)

    out: list[tuple[str, Callable[[dict[str, Any]], bool], str]] = [
        ("all", lambda row: True, "Keep all selection-log traded rows."),
        ("ready_only", lambda row: int(row.get("trade_ready") or 0) == 1, "Require trade_ready=1."),
        (
            "US_all_KR_ready",
            lambda row: row.get("market") == "US" or int(row.get("trade_ready") or 0) == 1,
            "Require trade_ready only for KR; keep all US.",
        ),
        (
            "KR_high_liq_US_all",
            lambda row: row.get("market") == "US" or row.get("liquidity_bucket") == "high",
            "KR requires high liquidity proxy.",
        ),
        (
            "KR_not_at_high_US_all",
            lambda row: row.get("market") == "US" or row.get("from_high_bucket") != "at_high",
            "KR avoids at-high chase entries.",
        ),
        (
            "KR_change_le10_US_all",
            lambda row: row.get("market") == "US" or (num(row, "change_pct") is not None and num(row, "change_pct") <= 10.0),
            "KR avoids high-change chase entries above 10%.",
        ),
        (
            "KR_gap_0_8_US_all",
            lambda row: row.get("market") == "US"
            or (num(row, "gap_pct") is not None and 0.0 <= num(row, "gap_pct") <= 8.0),
            "KR only moderate positive gap entries.",
        ),
    ]
    for n in (1, 2, 3):
        out.append((f"first{n}_per_market", first_n_selection(rows, n, market=None), f"Keep first {n} trades per market/day."))
        out.append((f"KR_first{n}_US_all", first_n_selection(rows, n, market="KR"), f"Keep only first {n} KR trades per day; keep all US."))
    return out


def first_n_closed(rows: list[dict[str, Any]], n: int, *, market: str | None) -> Callable[[dict[str, Any]], bool]:
    selected: set[int] = set()
    groups: dict[tuple[str, str], list[tuple[datetime, int]]] = defaultdict(list)
    for row in rows:
        if market and row.get("market") != market:
            selected.add(int(row["_idx"]))
            continue
        groups[(str(row.get("_date") or ""), str(row.get("market") or ""))].append(
            (row.get("_entry_dt") or row.get("_dt") or datetime.min, int(row["_idx"]))
        )
    for group in groups.values():
        for _dt, idx in sorted(group)[:n]:
            selected.add(idx)
    return lambda row: int(row["_idx"]) in selected


def first_n_selection(rows: list[dict[str, Any]], n: int, *, market: str | None) -> Callable[[dict[str, Any]], bool]:
    selected: set[int] = set()
    groups: dict[tuple[str, str], list[tuple[datetime, int]]] = defaultdict(list)
    for row in rows:
        if market and row.get("market") != market:
            selected.add(int(row["_idx"]))
            continue
        groups[(str(row.get("date") or ""), str(row.get("market") or ""))].append((row.get("_dt") or datetime.min, int(row["_idx"])))
    for group in groups.values():
        for _dt, idx in sorted(group)[:n]:
            selected.add(idx)
    return lambda row: int(row["_idx"]) in selected


def closed_sell_overlays() -> list[tuple[str, Callable[[dict[str, Any]], float], str]]:
    out: list[tuple[str, Callable[[dict[str, Any]], float], str]] = [
        ("actual_exit", lambda row: safe_float(row.get("pnl_pct")), "No overlay; use realized closed PnL."),
        ("current_sell", current_overlay, "Current cap3 + MFE>=2 floor 0.5 overlay."),
    ]
    for cap_kr in (2.0, 1.5, 1.2, 1.0):
        out.append(
            (
                f"KR_cap{cap_kr:g}_US_current_floor05",
                lambda row, cap_kr=cap_kr: kr_custom_us_current_overlay(row, cap_kr=cap_kr, floor=0.5, ratio=None),
                f"KR loss cap {cap_kr:g}%, US current-like cap3, MFE>=2 floor 0.5.",
            )
        )
        for ratio in (0.35, 0.45, 0.55):
            out.append(
                (
                    f"KR_cap{cap_kr:g}_US_current_mfe{ratio:g}",
                    lambda row, cap_kr=cap_kr, ratio=ratio: kr_custom_us_current_overlay(
                        row, cap_kr=cap_kr, floor=0.5, ratio=ratio
                    ),
                    f"KR loss cap {cap_kr:g}% with MFE preservation; US uses current overlay.",
                )
            )
    for cap_kr in (2.0, 1.5, 1.2, 1.0):
        for cap_us in (2.0, 1.5):
            out.append(
                (
                    f"KR_cap{cap_kr:g}_US_cap{cap_us:g}_floor05",
                    lambda row, cap_kr=cap_kr, cap_us=cap_us: mfe_floor_overlay(
                        row, cap_kr=cap_kr, cap_us=cap_us, floor=0.5, ratio=None
                    ),
                    f"Both markets capped; KR {cap_kr:g}%, US {cap_us:g}%, MFE>=2 floor 0.5.",
                )
            )
    return out


def current_overlay(row: dict[str, Any]) -> float:
    pct = max(safe_float(row.get("pnl_pct")), -3.0)
    mfe = safe_float(row.get("position_mfe_pct") if row.get("position_mfe_pct") is not None else row.get("peak_pnl_pct"))
    if mfe >= 2.0:
        pct = max(pct, 0.5)
    return pct


def kr_custom_us_current_overlay(
    row: dict[str, Any],
    *,
    cap_kr: float,
    floor: float,
    ratio: float | None,
) -> float:
    if row.get("market") != "KR":
        return current_overlay(row)
    return mfe_floor_overlay(row, cap_kr=cap_kr, cap_us=3.0, floor=floor, ratio=ratio)


def mfe_floor_overlay(
    row: dict[str, Any],
    *,
    cap_kr: float,
    cap_us: float,
    floor: float,
    ratio: float | None,
) -> float:
    cap = cap_kr if row.get("market") == "KR" else cap_us
    pct = max(safe_float(row.get("pnl_pct")), -abs(cap))
    mfe = safe_float(row.get("position_mfe_pct") if row.get("position_mfe_pct") is not None else row.get("peak_pnl_pct"))
    if mfe >= 2.0:
        pct = max(pct, (ratio * mfe) if ratio is not None else floor)
    return pct


def closed_metrics(rows: list[dict[str, Any]], transform: Callable[[dict[str, Any]], float]) -> dict[str, Any]:
    values = [transform(row) for row in rows]
    pnl_krw = sum(estimate_krw(row, transform(row)) for row in rows)
    return {
        **basic_metrics(values),
        "pnl_krw": round(pnl_krw, 0),
    }


def pct_metrics(rows: list[dict[str, Any]], cap: float | None) -> dict[str, Any]:
    values = []
    for row in rows:
        value = safe_float(row.get("pnl_pct"))
        if cap is not None:
            value = max(value, -abs(cap))
        values.append(value)
    return basic_metrics(values)


def basic_metrics(values: list[float]) -> dict[str, Any]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    wins = [value for value in clean if value > 0]
    losses = [value for value in clean if value <= 0]
    neg_sum = sum(value for value in clean if value < 0)
    pos_sum = sum(wins)
    return {
        "n": len(clean),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(clean) * 100.0, 2) if clean else 0.0,
        "avg_pct": round(sum(clean) / len(clean), 4) if clean else 0.0,
        "sum_pct": round(sum(clean), 4),
        "profit_factor": round(pos_sum / abs(neg_sum), 4) if neg_sum else ("inf" if pos_sum else None),
        "worst_pct": round(min(clean), 4) if clean else 0.0,
        "best_pct": round(max(clean), 4) if clean else 0.0,
    }


def estimate_krw(row: dict[str, Any], simulated_pct: float) -> float:
    realized_pct = safe_float(row.get("pnl_pct"))
    realized_krw = safe_float(row.get("pnl_krw"))
    if abs(realized_pct) <= 1e-9:
        return realized_krw
    return realized_krw * simulated_pct / realized_pct


def recommended_stack(closed_result: dict[str, Any], selection_result: dict[str, Any]) -> list[dict[str, Any]]:
    closed_hits = closed_result.get("top_hits") or []
    selection_hits = selection_result.get("top_hits") or []
    recommendations: list[dict[str, Any]] = []
    if closed_hits:
        safer = next(
            (
                hit
                for hit in closed_hits
                if hit["filter"] in {"KR_first1_US_all", "KR_first2_US_all", "KR_momentum_US_all"}
                and "US_current" in hit["overlay"]
                and ("cap1.2" in hit["overlay"] or "cap1.5" in hit["overlay"])
            ),
            closed_hits[0],
        )
        recommendations.append(
            {
                "name": "closed_trade_balanced_candidate",
                "read": "First deployable candidate from closed-trade search.",
                "candidate": safer,
            }
        )
    if selection_hits:
        safer_selection = next(
            (
                hit
                for hit in selection_hits
                if hit["filter"] in {"KR_first1_US_all", "KR_first2_US_all", "first1_per_market"}
                and hit["exit"] in {"cap1_5", "cap1_2"}
            ),
            selection_hits[0],
        )
        recommendations.append(
            {
                "name": "selection_trade_balanced_candidate",
                "read": "Candidate using entry-time filters available in ticker_selection_log.",
                "candidate": safer_selection,
            }
        )
    recommendations.append(
        {
            "name": "instrumentation_required",
            "read": "Add OR/VWAP/volume confirmation logs before broad KR re-expansion.",
            "candidate": {
                "KR_buy": "KR BUY_READY/PROBE_READY -> confirmation state; allow at most first 1-2 confirmed entries per day.",
                "KR_sell": "Shadow KR cap 1.2-1.5 plus MFE preservation; compare broker-fill replay before full size.",
                "US": "Keep current US sell overlay and do not reduce throughput unless US metrics deteriorate.",
            },
        }
    )
    return recommendations


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Web-Inspired Policy Search",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Basis",
        "",
        f"- Mode: {payload['basis']['mode']}",
        f"- Closed trades: {payload['basis']['closed_trades']}",
        f"- Selection trades: {payload['basis']['selection_trades']}",
        "- External playbooks reviewed: ORB/opening-range, VWAP confirmation, volume confirmation, pullback/false-breakout control, tight risk caps.",
        "- No new Claude judgment, broker call, or API call was made.",
        "",
        "Limits:",
    ]
    lines.extend(f"- {item}" for item in payload["basis"]["limits"])

    lines.extend(["", "## Rule Families Tested", ""])
    for item in payload["basis"]["web_inspired_rule_families"]:
        lines.append(f"- {item}")

    closed = payload["closed_trade_policy_search"]
    selection = payload["selection_trade_policy_search"]
    lines.extend(
        [
            "",
            "## Search Summary",
            "",
            f"- Closed-trade policy trials: {closed['trials']}",
            f"- Closed-trade KR+US positive hits: {closed['positive_both_market_hits']}",
            f"- Selection-trade policy trials: {selection['trials']}",
            f"- Selection-trade KR+US positive hits: {selection['positive_both_market_hits']}",
        ]
    )

    lines.extend(["", "## Closed Trade Baseline", ""])
    lines.extend(market_block(closed["baseline"]["actual"], "Actual"))
    lines.extend(market_block(closed["baseline"]["current_sell"], "Current Sell Overlay"))

    lines.extend(["", "## Top Closed-Trade Positive Hits", ""])
    lines.append("| Rank | Filter | Overlay | Kept | All PnL | KR PnL | US PnL | KR Avg | US Avg | KR Strategies |")
    lines.append("|---:|---|---|---:|---:|---:|---:|---:|---:|---|")
    for idx, hit in enumerate(closed["top_hits"][:20], start=1):
        lines.append(
            f"| {idx} | {hit['filter']} | {hit['overlay']} | {hit['kept']} | "
            f"{fmt_krw(hit['all']['pnl_krw'])} | {fmt_krw(hit['KR']['pnl_krw'])} | {fmt_krw(hit['US']['pnl_krw'])} | "
            f"{fmt_pct(hit['KR']['avg_pct'])} | {fmt_pct(hit['US']['avg_pct'])} | "
            f"{json.dumps(hit['KR_strategy_counts'], ensure_ascii=False)} |"
        )

    lines.extend(["", "## Selection Trade Baseline", ""])
    lines.extend(pct_market_block(selection["baseline"]["actual"], "Actual"))
    lines.extend(pct_market_block(selection["baseline"]["cap1_5"], "Cap 1.5"))

    lines.extend(["", "## Top Selection-Trade Positive Hits", ""])
    lines.append("| Rank | Filter | Exit | Kept | All Sum | KR Sum | US Sum | KR Avg | US Avg | Strategies |")
    lines.append("|---:|---|---|---:|---:|---:|---:|---:|---:|---|")
    for idx, hit in enumerate(selection["top_hits"][:20], start=1):
        lines.append(
            f"| {idx} | {hit['filter']} | {hit['exit']} | {hit['kept']} | "
            f"{fmt_pct(hit['all']['sum_pct'])} | {fmt_pct(hit['KR']['sum_pct'])} | {fmt_pct(hit['US']['sum_pct'])} | "
            f"{fmt_pct(hit['KR']['avg_pct'])} | {fmt_pct(hit['US']['avg_pct'])} | "
            f"{json.dumps(hit['strategy_counts'], ensure_ascii=False)} |"
        )

    lines.extend(["", "## Recommended Stack", ""])
    for item in payload["recommended_policy_stack"]:
        lines.append(f"### {item['name']}")
        lines.append(f"- {item['read']}")
        lines.append("```json")
        lines.append(json.dumps(item["candidate"], ensure_ascii=False, indent=2))
        lines.append("```")
    lines.append("")
    return "\n".join(lines)


def market_block(data: dict[str, Any], title: str) -> list[str]:
    lines = [f"### {title}", "| Market | N | W/L | Avg | Sum | PF | PnL |", "|---|---:|---:|---:|---:|---:|---:|"]
    for market in ("all", "KR", "US"):
        m = data[market]
        lines.append(
            f"| {market} | {m['n']} | {m['wins']}/{m['losses']} | {fmt_pct(m['avg_pct'])} | "
            f"{fmt_pct(m['sum_pct'])} | {fmt_pf(m['profit_factor'])} | {fmt_krw(m.get('pnl_krw'))} |"
        )
    return lines


def pct_market_block(data: dict[str, Any], title: str) -> list[str]:
    lines = [f"### {title}", "| Market | N | W/L | Avg | Sum | PF |", "|---|---:|---:|---:|---:|---:|"]
    for market in ("all", "KR", "US"):
        m = data[market]
        lines.append(
            f"| {market} | {m['n']} | {m['wins']}/{m['losses']} | {fmt_pct(m['avg_pct'])} | "
            f"{fmt_pct(m['sum_pct'])} | {fmt_pf(m['profit_factor'])} |"
        )
    return lines


def parse_dt(value: Any) -> datetime:
    if not value:
        return datetime.min
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromisoformat(text.split("+")[0])
        except ValueError:
            return datetime.min


def hour_float(value: datetime) -> float:
    return value.hour + value.minute / 60.0 + value.second / 3600.0


def safe_float(value: Any) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else 0.0
    except (TypeError, ValueError):
        return 0.0


def fmt_pct(value: Any) -> str:
    return f"{safe_float(value):+.3f}%"


def fmt_krw(value: Any) -> str:
    return f"{safe_float(value):+,.0f}"


def fmt_pf(value: Any) -> str:
    if value is None:
        return "NA"
    if value == "inf":
        return "inf"
    return f"{safe_float(value):.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
