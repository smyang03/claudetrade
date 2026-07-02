from __future__ import annotations

"""A2 — 스톱 감지 레이턴시/오버슈트 소비기 (read-only 측정 전용).

목적: loss_cap/hard_stop 트리거 시 plan에 기록된 stop_trigger_price·stop_trigger_at
(커밋 022fc78·2f6edad가 깐 배관)를 실제 체결가(actual_exit_price)·청산 발주시각
(sell_order_sent_at)과 대조해
  (a) 갭(오버슈트) = (트리거가 − 체결가)/트리거가
  (b) 지연 = 트리거시각 → 청산발주시각
를 kind(loss_cap/hard_stop)·시장(KR/US)별로 집계한다.

주문·상태·brain 무접촉. DB는 mode=ro로만 연다. 측정 외 부수효과 없음.

배관이 최근(2026-06-28) 깔려 primary 표본이 0~소수일 수 있다 — 그 경우 정직히
"배관 가동 후 누적 필요"로 보고하고, 참고용 PROXY 섹션(기존 필드 기반)을 분리 제공한다.
"""

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KST_DB = ROOT / "data" / "v2_event_store.db"

# 청산 발주시각 후보(plan 키 우선순위). 트리거 감지 → 실제 매도 발주 사이 지연 측정용.
SELL_TIME_KEYS = ("sell_order_sent_at", "local_sell_order_at", "sell_pending_resolution_at")


def _connect_ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"event store DB not found: {path}")
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _parse_dt(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    txt = raw.strip()
    if not txt:
        return None
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except ValueError:
        return None
    # tz-aware/naive 혼재 시 빼기 TypeError 방지 — tzinfo만 제거(벽시계값)해 naive끼리 빼게 한다.
    # (정밀 delay는 정식 표본에서 동일 시간대 기록 확인 후. 현재는 표본 0이라 크래시 방지 우선)
    return dt.replace(tzinfo=None)


def _stats(values: list[float]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"count": 0}
    vals_sorted = sorted(vals)
    n = len(vals_sorted)
    p90 = vals_sorted[min(n - 1, int(round(0.9 * (n - 1))))]
    return {
        "count": n,
        "avg": round(sum(vals) / n, 4),
        "median": round(float(median(vals)), 4),
        "p90": round(float(p90), 4),
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
    }


def _first_time(plan: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, datetime | None]:
    for k in keys:
        dt = _parse_dt(plan.get(k))
        if dt is not None:
            return k, dt
    return "", None


def _classify_kind(plan: dict[str, Any]) -> str:
    kind = str(plan.get("stop_trigger_kind") or "").strip()
    if kind:
        return kind
    cr = str(plan.get("close_reason") or "").lower()
    if "loss_cap" in cr:
        return "loss_cap"
    if "hard_stop" in cr:
        return "hard_stop"
    return "other"


def _load_closed_stop_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT path_run_id, market, status, plan_json, created_at, updated_at
        FROM v2_path_runs
        WHERE status='CLOSED'
          AND (plan_json LIKE '%stop_trigger_price%'
               OR plan_json LIKE '%loss_cap%'
               OR plan_json LIKE '%hard_stop%')
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            plan = json.loads(r["plan_json"] or "{}")
        except Exception:
            plan = {}
        out.append({
            "path_run_id": r["path_run_id"],
            "market": str(r["market"] or "").upper(),
            "plan": plan,
            "updated_at": r["updated_at"],
        })
    return out


def _gap_pct(trigger_price: float, exit_price: float) -> float | None:
    if trigger_price and trigger_price > 0 and exit_price and exit_price > 0:
        # 양수 = 체결가가 트리거가보다 낮음(하방 오버슈트)
        return (trigger_price - exit_price) / trigger_price * 100.0
    return None


def _build_primary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """stop_trigger_price/stop_trigger_at 가 실제 기록된 정식 표본."""
    buckets: dict[tuple[str, str], dict[str, list]] = defaultdict(lambda: {"gap": [], "delay": [], "decomp": []})
    samples: list[dict[str, Any]] = []
    for run in runs:
        plan = run["plan"]
        trig_price = plan.get("stop_trigger_price")
        if trig_price is None:
            continue
        trig_at = _parse_dt(plan.get("stop_trigger_at"))
        kind = _classify_kind(plan)
        market = run["market"]
        exit_price = float(plan.get("actual_exit_price") or 0)
        gap = _gap_pct(float(trig_price), exit_price)
        sell_key, sell_at = _first_time(plan, SELL_TIME_KEYS)
        delay = (sell_at - trig_at).total_seconds() if (trig_at and sell_at) else None
        obs_low = plan.get("observed_low_price")
        # 갭다운 vs 인지지연 분해 단서:
        #  - 체결가가 관측저점에 근접 → 가격이 임계 관통(갭다운) 가능성
        #  - 체결가가 관측저점보다 한참 위 + delay 큼 → 지연 동안 더 흘러내린 인지지연
        decomp = None
        if exit_price > 0 and obs_low and float(obs_low) > 0:
            low_gap = (exit_price - float(obs_low)) / exit_price * 100.0
            decomp = {"exit_vs_low_pct": round(low_gap, 4)}
        key = (market, kind)
        if gap is not None:
            buckets[key]["gap"].append(gap)
        if delay is not None:
            buckets[key]["delay"].append(delay)
        if decomp is not None:
            buckets[key]["decomp"].append(decomp["exit_vs_low_pct"])
        samples.append({
            "path_run_id": run["path_run_id"],
            "market": market,
            "kind": kind,
            "trigger_price": round(float(trig_price), 6),
            "exit_price": round(exit_price, 6),
            "gap_pct": round(gap, 4) if gap is not None else None,
            "delay_sec": round(delay, 1) if delay is not None else None,
            "sell_time_key": sell_key,
            "exit_vs_observed_low_pct": decomp["exit_vs_low_pct"] if decomp else None,
        })
    summary = {}
    for (market, kind), v in sorted(buckets.items()):
        summary[f"{market}/{kind}"] = {
            "gap_pct": _stats(v["gap"]),
            "delay_sec": _stats(v["delay"]),
            "exit_vs_observed_low_pct": _stats(v["decomp"]),
        }
    return {"sample_count": len(samples), "by_market_kind": summary, "samples": samples}


def _build_proxy(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """배관 이전 청산엔 stop_trigger_*가 없다. 참고용 PROXY:
       - 오버슈트 proxy: auto_sell_review_price_native(감지시 리뷰가) vs actual_exit_price
       - 지연 proxy: auto_sell_reviewed_at → sell_order_sent_at (리뷰 라운드트립)
       정식 측정 아님 — 방향성 참고만."""
    buckets: dict[tuple[str, str], dict[str, list]] = defaultdict(lambda: {"gap": [], "delay": []})
    count = 0
    for run in runs:
        plan = run["plan"]
        if plan.get("stop_trigger_price") is not None:
            continue  # 정식 표본은 primary에서 처리
        kind = _classify_kind(plan)
        if kind not in ("loss_cap", "hard_stop"):
            continue
        market = run["market"]
        review_price = float(plan.get("auto_sell_review_price_native") or 0)
        exit_price = float(plan.get("actual_exit_price") or 0)
        gap = _gap_pct(review_price, exit_price)
        rev_at = _parse_dt(plan.get("auto_sell_reviewed_at"))
        _, sell_at = _first_time(plan, SELL_TIME_KEYS)
        delay = (sell_at - rev_at).total_seconds() if (rev_at and sell_at) else None
        key = (market, kind)
        if gap is not None:
            buckets[key]["gap"].append(gap)
        if delay is not None:
            buckets[key]["delay"].append(delay)
        count += 1
    summary = {}
    for (market, kind), v in sorted(buckets.items()):
        summary[f"{market}/{kind}"] = {
            "overshoot_proxy_pct": _stats(v["gap"]),
            "review_roundtrip_delay_sec": _stats(v["delay"]),
        }
    return {"proxy_run_count": count, "by_market_kind": summary,
            "note": "PROXY only — review_price vs exit_price; review_at→sell_sent. stop_trigger 배관 이전 청산. 정식 측정 아님."}


def main() -> int:
    ap = argparse.ArgumentParser(description="A2 스톱 감지 레이턴시/오버슈트 소비기 (read-only)")
    ap.add_argument("--db", default=str(KST_DB), help="v2_event_store.db 경로")
    ap.add_argument("--market", choices=["KR", "US"], help="시장 필터")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("--show-samples", action="store_true", help="primary 개별 표본 출력")
    args = ap.parse_args()

    conn = _connect_ro(Path(args.db))
    try:
        runs = _load_closed_stop_runs(conn)
    finally:
        conn.close()
    if args.market:
        runs = [r for r in runs if r["market"] == args.market]

    primary = _build_primary(runs)
    proxy = _build_proxy(runs)

    # 모집단 카운트(향후 누적 가늠용)
    pop = Counter()
    for r in runs:
        kind = _classify_kind(r["plan"])
        if kind in ("loss_cap", "hard_stop"):
            pop[(r["market"], kind)] += 1

    report = {
        "db": str(Path(args.db).resolve()),
        "scanned_closed_stop_runs": len(runs),
        "population_loss_hard_by_market_kind": {f"{m}/{k}": n for (m, k), n in sorted(pop.items())},
        "primary": {k: v for k, v in primary.items() if k != "samples" or args.show_samples},
        "proxy": proxy,
    }
    if not args.show_samples:
        report["primary"].pop("samples", None)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("=== A2 스톱 감지 레이턴시/오버슈트 (read-only) ===")
    print(f"DB: {report['db']}")
    print(f"스캔된 CLOSED 스톱계열 path_run: {report['scanned_closed_stop_runs']}")
    print(f"모집단(loss_cap/hard_stop) by 시장/kind: {report['population_loss_hard_by_market_kind']}")
    print()
    print(f"[PRIMARY] stop_trigger_price 기록 표본 수: {primary['sample_count']}")
    if primary["sample_count"] == 0:
        print("  → 배관(022fc78·2f6edad, 2026-06-28) 가동 후 정식 표본 미누적. 누적 필요.")
    else:
        for key, st in primary["by_market_kind"].items():
            print(f"  {key}:")
            print(f"    갭(트리거가→체결가) %: {st['gap_pct']}")
            print(f"    지연(트리거→매도발주) 초: {st['delay_sec']}")
            print(f"    체결가 vs 관측저점 %: {st['exit_vs_observed_low_pct']}")
    print()
    print(f"[PROXY-참고] stop_trigger 이전 청산 {proxy['proxy_run_count']}건 (정식 측정 아님)")
    for key, st in proxy["by_market_kind"].items():
        print(f"  {key}: 오버슈트proxy%={st['overshoot_proxy_pct']} | 리뷰roundtrip지연s={st['review_roundtrip_delay_sec']}")
    print(f"  주: {proxy['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
