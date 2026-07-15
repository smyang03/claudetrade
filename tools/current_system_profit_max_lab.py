from __future__ import annotations

"""Read-only profit-maximisation lab for the current PathB system.

Every feature in this file is constrained to information available before an
entry (or, for the exit study, at the sell trigger).  It deliberately avoids
changing live configuration, databases, or order state.
"""

import argparse
import csv
import json
import sqlite3
import statistics as st
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"
EVENT_DB = ROOT / "data" / "v2_event_store.db"
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
QQQ_DAILY = ROOT / "data" / "price" / "us" / "us_QQQ.csv"
SPY_MINUTE = ROOT / "data" / "price" / "minute" / "us" / "us_SPY.csv"
MINUTE_DIR = {
    "US": ROOT / "data" / "price" / "minute" / "us",
    "KR": ROOT / "data" / "price" / "minute" / "kr",
}

PROTECT_REASONS = {
    "CLOSED_LOSS_CAP",
    "CLOSED_HARD_STOP",
    "CLOSED_CLAUDE_PRICE_STOP",
    "CLOSED_PROFIT_LADDER",
    "CLOSED_MFE_BREAKEVEN",
}


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    # Python versions differ on accepting compact offsets such as +0900.
    if len(raw) >= 5 and raw[-5] in "+-" and raw[-3] != ":":
        raw = raw[:-2] + ":" + raw[-2:]
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def net_value(row: dict[str, Any]) -> float | None:
    value = row.get("pnl_pct_net")
    if value is None:
        value = row.get("pnl_pct")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def load_trades() -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{ML_DB.resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [
        dict(row)
        for row in con.execute(
            """
            SELECT v2_decision_id, path_run_id, market, session_date, ticker,
                   filled_at, closed_at, entry_price, pnl_pct, pnl_pct_net, close_reason,
                   market_regime, strategy
            FROM v2_learning_performance
            WHERE closed=1 AND filled=1 AND runtime_mode='live'
              AND filled_at IS NOT NULL AND session_date IS NOT NULL
            ORDER BY filled_at, v2_decision_id
            """
        )
    ]
    con.close()
    output: list[dict[str, Any]] = []
    for row in rows:
        net = net_value(row)
        filled = parse_dt(row.get("filled_at"))
        closed = parse_dt(row.get("closed_at"))
        if net is None or filled is None:
            continue
        row["net"] = net
        row["filled_dt"] = filled
        row["closed_dt"] = closed
        output.append(row)
    return output


def load_plan_map() -> dict[str, dict[str, Any]]:
    con = sqlite3.connect(f"file:{EVENT_DB.resolve().as_posix()}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT decision_id, path_run_id, market, ticker, plan_json, updated_at "
        "FROM v2_path_runs WHERE runtime_mode='live'"
    ).fetchall()
    con.close()
    latest: dict[str, tuple[str, dict[str, Any]]] = {}
    for decision_id, path_run_id, market, ticker, raw, updated_at in rows:
        try:
            plan = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            plan = {}
        plan["market"] = market
        plan["ticker"] = ticker
        stamp = str(updated_at or "")
        for key in (str(decision_id or ""), str(path_run_id or "")):
            if key and (key not in latest or stamp > latest[key][0]):
                latest[key] = (stamp, plan)
    return {key: item[1] for key, item in latest.items()}


def load_bucket_map() -> dict[tuple[str, str, str], str]:
    con = sqlite3.connect(f"file:{AUDIT_DB.resolve().as_posix()}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT market,ticker,session_date,primary_bucket,known_at "
        "FROM audit_candidate_rows WHERE primary_bucket IS NOT NULL AND primary_bucket!='' "
        "ORDER BY known_at"
    ).fetchall()
    con.close()
    output: dict[tuple[str, str, str], str] = {}
    for market, ticker, session_date, bucket, _ in rows:
        output[(str(market).upper(), str(ticker).upper(), str(session_date))] = str(bucket)
    return output


def prior_qqq_features() -> dict[str, dict[str, Any]]:
    rows: list[tuple[str, float]] = []
    with open(QQQ_DAILY, encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                rows.append((str(row["date"]), float(row["close"])))
            except (KeyError, TypeError, ValueError):
                continue
    rows.sort()
    output: dict[str, dict[str, Any]] = {}
    closes: list[float] = []
    for index, (date, close) in enumerate(rows):
        # The feature for date t is calculated only through t-1.
        if index > 0:
            prior = closes[-1]
            ma20 = st.mean(closes[-20:]) if len(closes) >= 20 else None
            ret1 = (prior / closes[-2] - 1.0) * 100.0 if len(closes) >= 2 else None
            ret5 = (prior / closes[-6] - 1.0) * 100.0 if len(closes) >= 6 else None
            output[date] = {
                "prior_close": prior,
                "prior_ma20": ma20,
                "prior_below_ma20": bool(ma20 is not None and prior < ma20),
                "prior_down": bool(ret1 is not None and ret1 < 0),
                "prior_ret5_neg": bool(ret5 is not None and ret5 < 0),
            }
        closes.append(close)
    return output


def session_key_kst(ts: datetime) -> str:
    kst = ts.astimezone(timezone(timedelta(hours=9)))
    if kst.hour < 12:
        return (kst.date() - timedelta(days=1)).isoformat()
    return kst.date().isoformat()


def load_spy_tape() -> tuple[list[tuple[datetime, str, float]], dict[str, float]]:
    bars: list[tuple[datetime, str, float]] = []
    opens: dict[str, float] = {}
    with open(SPY_MINUTE, encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            ts = parse_dt(row.get("ts"))
            try:
                close = float(row["close"])
            except (KeyError, TypeError, ValueError):
                continue
            if ts is None:
                continue
            key = session_key_kst(ts)
            opens.setdefault(key, close)
            bars.append((ts, key, close))
    bars.sort(key=lambda item: item[0])
    return bars, opens


def tape_move_at(
    entry: datetime,
    bars: list[tuple[datetime, str, float]],
    opens: dict[str, float],
) -> float | None:
    key = session_key_kst(entry)
    prior: float | None = None
    for ts, session, close in bars:
        if ts > entry:
            break
        if session == key:
            prior = close
    opened = opens.get(key)
    if prior is None or opened is None or opened <= 0:
        return None
    return (prior / opened - 1.0) * 100.0


def zone_flag(plan: dict[str, Any], market: str) -> bool:
    if market != "US":
        return False
    try:
        high = float(plan.get("buy_zone_high"))
        low = float(plan.get("buy_zone_low"))
        hit = float(plan.get("hit_price"))
        reward = float(plan.get("reward_pct"))
    except (TypeError, ValueError):
        return False
    if high <= low:
        return False
    return ((hit - low) / (high - low)) >= 0.67 and reward >= 5.0


def attach_entry_features(trades: list[dict[str, Any]]) -> dict[str, int]:
    plans = load_plan_map()
    buckets = load_bucket_map()
    qqq = prior_qqq_features()
    spy_bars, spy_opens = load_spy_tape()
    closed_losses: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    closed_market: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    closed_bucket: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    context_history: dict[str, list[tuple[datetime, float, dict[str, bool]]]] = defaultdict(list)
    counts = defaultdict(int)

    # Replay by entry time.  A prior loss is usable only if it was already
    # closed before this entry, preventing the fill-order leakage in the old
    # repeat-loss review.
    for trade in sorted(trades, key=lambda row: row["filled_dt"]):
        market = str(trade["market"]).upper()
        ticker = str(trade["ticker"]).upper()
        date = str(trade["session_date"])
        bucket = buckets.get((market, ticker, date), "")
        trade["primary_bucket"] = bucket
        cutoff = trade["filled_dt"] - timedelta(days=10)
        prior_losses = [
            closed
            for closed in closed_losses.get((market, bucket), [])
            if cutoff <= closed < trade["filled_dt"]
        ] if bucket else []
        trade["repeat_loss_count"] = len(prior_losses)
        trade["repeat_adverse"] = len(prior_losses) >= 3

        market_history = sorted(
            (
                item for item in closed_market.get(market, [])
                if item[0] < trade["filled_dt"]
            ),
            key=lambda item: item[0],
        )[-20:]
        trade["market_health_n"] = len(market_history)
        trade["market_health_mean"] = (
            st.mean(item[1] for item in market_history) if market_history else None
        )
        trade["market_health_adverse"] = bool(
            len(market_history) >= 10 and trade["market_health_mean"] < 0.0
        )
        bucket_cutoff = trade["filled_dt"] - timedelta(days=20)
        bucket_history = sorted(
            (
                item for item in closed_bucket.get((market, bucket), [])
                if bucket_cutoff <= item[0] < trade["filled_dt"]
            ),
            key=lambda item: item[0],
        )[-10:] if bucket else []
        trade["bucket_health_n"] = len(bucket_history)
        trade["bucket_health_mean"] = (
            st.mean(item[1] for item in bucket_history) if bucket_history else None
        )
        trade["bucket_health_adverse"] = bool(
            len(bucket_history) >= 5 and trade["bucket_health_mean"] < 0.0
        )

        feature = qqq.get(date, {}) if market == "US" else {}
        trade["regime_adverse"] = bool(feature.get("prior_below_ma20", False))
        tape = tape_move_at(trade["filled_dt"], spy_bars, spy_opens) if market == "US" else None
        trade["tape_move_pct"] = tape
        trade["tape_adverse"] = bool(tape is not None and tape < -0.1)
        plan = plans.get(str(trade.get("v2_decision_id") or "")) or plans.get(
            str(trade.get("path_run_id") or "")
        ) or {}
        trade["zone_adverse"] = zone_flag(plan, market)
        flags = (
            trade["regime_adverse"],
            trade["tape_adverse"],
            trade["repeat_adverse"],
            trade["zone_adverse"],
        )
        trade["risk_score"] = sum(bool(flag) for flag in flags)
        context_flags = {
            "regime": bool(trade["regime_adverse"]),
            "tape": bool(trade["tape_adverse"]),
            "repeat": bool(trade["repeat_adverse"]),
            "zone": bool(trade["zone_adverse"]),
        }
        active_contexts: list[str] = []
        context_state: dict[str, dict[str, Any]] = {}
        prior_context = [
            item for item in context_history.get(market, [])
            if item[0] < trade["filled_dt"]
        ]
        for name in context_flags:
            values = [item[1] for item in prior_context if item[2].get(name)][-10:]
            mean_value = st.mean(values) if values else None
            active = bool(len(values) >= 5 and mean_value is not None and mean_value < 0.0)
            context_state[name] = {"n": len(values), "mean": mean_value, "active": active}
            if context_flags[name] and active:
                active_contexts.append(name)
        trade["context_state"] = context_state
        trade["active_adverse_contexts"] = active_contexts
        trade["active_adverse_count"] = len(active_contexts)
        for name, flag in zip(("regime", "tape", "repeat", "zone"), flags):
            if flag:
                counts[f"{market}_{name}"] += 1
        if tape is not None:
            counts["US_tape_covered"] += 1

        if trade["net"] < 0 and trade.get("closed_dt") is not None and bucket:
            closed_losses[(market, bucket)].append(trade["closed_dt"])
        if trade.get("closed_dt") is not None:
            closed_market[market].append((trade["closed_dt"], float(trade["net"])))
            if bucket:
                closed_bucket[(market, bucket)].append((trade["closed_dt"], float(trade["net"])))
            context_history[market].append(
                (trade["closed_dt"], float(trade["net"]), context_flags)
            )
    return dict(counts)


def max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def metric(rows: list[dict[str, Any]], weight_key: str | None = None) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    weights = [float(row.get(weight_key, 1.0)) if weight_key else 1.0 for row in rows]
    pnls = [float(row["net"]) * weight for row, weight in zip(rows, weights)]
    exposure = sum(weights)
    ranked = sorted(pnls)
    trimmed = ranked[3:-3] if len(ranked) > 6 else ranked
    return {
        "n": len(rows),
        "exposure_units": round(exposure, 3),
        "net_sum_pct_units": round(sum(pnls), 3),
        "net_per_offered_trade": round(st.mean(pnls), 4),
        "net_per_exposure": round(sum(pnls) / exposure, 4) if exposure else None,
        "win_rate_pct": round(100 * sum(1 for value in pnls if value > 0) / len(pnls), 2),
        "max_drawdown_pct_units": round(max_drawdown(pnls), 3),
        "trimmed_sum_pct_units": round(sum(trimmed), 3),
    }


def period_rows(trades: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "all": trades,
        "early_2026_04_05": [row for row in trades if str(row["session_date"]) < "2026-06-01"],
        "late_2026_06_07": [row for row in trades if str(row["session_date"]) >= "2026-06-01"],
    }


def entry_policy_report(trades: list[dict[str, Any]]) -> dict[str, Any]:
    for row in trades:
        score = int(row.get("risk_score", 0))
        row["risk_score_weight"] = 1.0 if score == 0 else 0.5 if score == 1 else 0.25 if score == 2 else 0.0
        # An online controller avoids applying a defensive rule during a
        # profitable tape.  It enters defensive mode only after the last 20
        # fully realised trades have a negative mean.
        if not row.get("market_health_adverse"):
            row["adaptive_weight"] = 1.0
        else:
            adverse_count = sum(
                bool(row.get(flag))
                for flag in ("tape_adverse", "bucket_health_adverse", "zone_adverse")
            )
            row["adaptive_weight"] = 0.5 if adverse_count == 0 else 0.25 if adverse_count == 1 else 0.125
        active_count = int(row.get("active_adverse_count", 0))
        row["context_weight"] = 1.0 if active_count == 0 else 0.5 if active_count == 1 else 0.25 if active_count == 2 else 0.125
    output: dict[str, Any] = {}
    for period, rows in period_rows(trades).items():
        flags: dict[str, Any] = {}
        for flag in (
            "regime_adverse",
            "tape_adverse",
            "repeat_adverse",
            "zone_adverse",
            "market_health_adverse",
            "bucket_health_adverse",
        ):
            adverse = [row for row in rows if row.get(flag)]
            clear = [row for row in rows if not row.get(flag)]
            flags[flag] = {"adverse": metric(adverse), "clear": metric(clear)}
        output[period] = {
            "base": metric(rows),
            "risk_score_sizing": metric(rows, "risk_score_weight"),
            "adaptive_sizing": metric(rows, "adaptive_weight"),
            "context_online_sizing": metric(rows, "context_weight"),
            "flags": flags,
            "score_distribution": {
                str(score): sum(1 for row in rows if int(row.get("risk_score", 0)) == score)
                for score in range(5)
            },
        }
    return output


def load_minute_prices(market: str, ticker: str) -> list[tuple[datetime, float]]:
    prefix = "us" if market == "US" else "kr"
    path = MINUTE_DIR[market] / f"{prefix}_{ticker}.csv"
    if not path.exists():
        return []
    output: list[tuple[datetime, float]] = []
    with open(path, encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            ts = parse_dt(row.get("ts"))
            try:
                close = float(row["close"])
            except (KeyError, TypeError, ValueError):
                continue
            if ts is not None:
                output.append((ts, close))
    output.sort()
    return output


def price_at_or_before(bars: list[tuple[datetime, float]], target: datetime) -> float | None:
    best: float | None = None
    for ts, close in bars:
        if ts > target:
            break
        best = close
    return best


def exit_speed_report() -> dict[str, Any]:
    plans = load_plan_map()
    cache: dict[tuple[str, str], list[tuple[datetime, float]]] = {}
    samples: list[dict[str, Any]] = []
    for plan in plans.values():
        market = str(plan.get("market") or "").upper()
        ticker = str(plan.get("ticker") or "")
        reason = str(plan.get("close_reason") or "")
        trigger = parse_dt(plan.get("auto_sell_reviewed_at"))
        sell = None
        for key in ("sell_order_sent_at", "local_sell_order_at", "sell_pending_resolution_at"):
            sell = parse_dt(plan.get(key))
            if sell is not None:
                break
        try:
            exit_price = float(plan.get("actual_exit_price") or 0)
            trigger_price = float(plan.get("auto_sell_review_price_native") or 0)
        except (TypeError, ValueError):
            exit_price = 0.0
            trigger_price = 0.0
        if (
            market not in MINUTE_DIR
            or not ticker
            or trigger is None
            or sell is None
            or sell < trigger
            or exit_price <= 0
            or trigger_price <= 0
        ):
            continue
        time_cost = (trigger_price - exit_price) / trigger_price * 100.0
        samples.append(
            {
                "session_date": str(plan.get("session_date") or trigger.date().isoformat()),
                "market": market,
                "reason": reason,
                "delay_sec": (sell - trigger).total_seconds(),
                "time_cost_pct": time_cost,
                "protective": reason in PROTECT_REASONS,
            }
        )

    output: dict[str, Any] = {"coverage_n": len(samples), "periods": {}}
    for period, rows in period_rows(samples).items():
        groups: dict[str, Any] = {}
        for name, subset in {
            "protective": [row for row in rows if row["protective"]],
            "non_protective": [row for row in rows if not row["protective"]],
        }.items():
            values = [float(row["time_cost_pct"]) for row in subset]
            groups[name] = {
                "n": len(values),
                "mean_time_cost_pct": round(st.mean(values), 4) if values else None,
                "median_time_cost_pct": round(st.median(values), 4) if values else None,
                "total_recoverable_pct_units": round(sum(values), 3) if values else 0.0,
            }
        by_reason: dict[str, Any] = {}
        for reason in sorted({str(row["reason"]) for row in rows}):
            values = [float(row["time_cost_pct"]) for row in rows if row["reason"] == reason]
            by_reason[reason] = {
                "n": len(values),
                "mean_time_cost_pct": round(st.mean(values), 4),
                "total_time_cost_pct_units": round(sum(values), 3),
            }
        output["periods"][period] = {"groups": groups, "by_reason": by_reason}
    return output


def markdown_report(report: dict[str, Any]) -> str:
    entry = report["entry_policy"]
    exits = report["exit_speed"]["periods"]["all"]["groups"]
    lines = [
        "# 현재 시스템 수익 극대화 랩 — 2026-07-15",
        "",
        "## 계약",
        "",
        "- 라이브 주문·설정·DB를 변경하지 않는 read-only 분석이다.",
        "- QQQ 추세는 반드시 전일 종가까지, 반복손실은 진입 전에 청산된 손실만 사용한다.",
        "- 이번 결과를 보고 만든 조합이므로 모든 신규 정책은 `SHADOW_ONLY`다.",
        "",
        "## PathB 위험점수 배분기",
        "",
        "| 구간 | 기본 net합 | 고정점수 net합 | 온라인 적응 net합 | 기본 MDD | 적응 MDD | 적응노출 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for period, label in (("all", "전체"), ("early_2026_04_05", "4~5월"), ("late_2026_06_07", "6~7월")):
        base = entry[period]["base"]
        sized = entry[period]["risk_score_sizing"]
        adaptive = entry[period]["adaptive_sizing"]
        lines.append(
            f"| {label} | {base.get('net_sum_pct_units', 0):+.2f} | "
            f"{sized.get('net_sum_pct_units', 0):+.2f} | {adaptive.get('net_sum_pct_units', 0):+.2f} | "
            f"{base.get('max_drawdown_pct_units', 0):+.2f} | "
            f"{adaptive.get('max_drawdown_pct_units', 0):+.2f} | {adaptive.get('exposure_units', 0):.1f} |"
        )
    lines += [
        "",
        "점수는 전일 QQQ<MA20, 진입 순간 SPY<-0.1%, 최근 10일 동일 버킷 확정손실 3회, "
        "US 존 상단 체결+목표거리 5% 이상을 각각 1점으로 계산한다. 비중은 0/1/2/3점에 "
        "1.0/0.5/0.25/0배다.",
        "온라인 적응형은 직전 20개 확정 청산의 평균 net이 음수일 때만 방어 모드로 들어가고, "
        "그 안에서 tape·버킷건강·존추격 신호에 따라 0.5/0.25/0.125배로 줄인다.",
        "",
        "## auto-sell 검토 오버슈트 재검증",
        "",
        f"- auto-sell 검토가격→체결가 proxy, 보호 출구: n={exits['protective']['n']}, "
        f"평균 {exits['protective']['mean_time_cost_pct']:+.3f}%p, "
        f"합계 {exits['protective']['total_recoverable_pct_units']:+.2f}%p.",
        f"- 비보호 출구: n={exits['non_protective']['n']}, 평균 "
        f"{exits['non_protective']['mean_time_cost_pct']:+.3f}%p.",
        "",
        "판정: 이 수치는 검토 우회만으로 큰 수익을 만들 근거가 아니다. 출구 개선은 실제 경로를 가진 "
        "early-tier/ladder forward 원장으로만 승격한다.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-json", default=str(ROOT / "reports" / "current_system_profit_max_lab_20260715.json"))
    parser.add_argument("--output-md", default=str(ROOT / "docs" / "reports" / "current_system_profit_max_lab_20260715.md"))
    args = parser.parse_args()

    trades = load_trades()
    coverage = attach_entry_features(trades)
    report = {
        "as_of": "2026-07-15",
        "authority": "SHADOW_ONLY",
        "contracts": {
            "regime": "session t uses QQQ data through t-1 only",
            "repeat_loss": "only losses with closed_at < current filled_at",
            "tape": "SPY session-open to entry timestamp only",
            "zone": "entry-time plan values only",
        },
        "trade_n": len(trades),
        "coverage": coverage,
        "entry_policy": entry_policy_report(trades),
        "exit_speed": exit_speed_report(),
    }
    json_path = Path(args.output_json)
    md_path = Path(args.output_md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(f"\nWROTE {json_path}\nWROTE {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
