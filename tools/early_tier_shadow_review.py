"""조기익절 tier shadow 판독 (read-only, 라이브 동작 불변).

목표 캘리브레이션 실측(2026-07-11): 계획 sell_target이 도달가능 MFE 대비 과대
(US 목표 5.7% vs MFE중앙 2.4%, 도달률 17%)라, 익절 엔진(LADDER ACT 4%)이
MFE 분포 위에서 발화해 대부분 미실현이익을 반납한다.

이 도구는 각 실거래 청산건에 대해 "도달가능 레벨(US 2.3%/KR 3.6%)에서 비율 f 부분익절 +
(1-f) 러너" 반실험 net을 계산해 실제 net과 비교한다. enforce 전 shadow 근거 누적용.

한계: held MFE(ledger) 기반 낙관 ceiling — 한계지정가 체결 가정, 슬리피지 미모델.
enforce는 이 shadow가 forward로 충분히 쌓이고 운영자 승인 후에만.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"
DEFAULT_EVENT_DB = ROOT / "data" / "v2_event_store.db"

# 실측 도달가능 레벨(0.4~0.5x 계획목표) 및 기본 부분익절 비율.
DEFAULT_LEVEL = {"US": 2.3, "KR": 3.6}
DEFAULT_F = 0.5
DEFAULT_COST = {"US": 0.5, "KR": 0.21}


def _connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)


def load_plan_targets(event_db: Path) -> dict[str, float]:
    """decision_id -> sell_target (plan_json)."""
    out: dict[str, float] = {}
    if not event_db.exists():
        return out
    with _connect_ro(event_db) as c:
        for did, pj in c.execute(
            "SELECT decision_id, plan_json FROM v2_path_runs WHERE plan_json IS NOT NULL AND plan_json != ''"
        ):
            try:
                st = json.loads(pj).get("sell_target")
            except Exception:
                continue
            if st and did:
                out[str(did)] = float(st)
    return out


def load_trades(ml_db: Path, since: str | None) -> list[dict]:
    where = "closed=1 AND pnl_pct_net IS NOT NULL AND mfe_pct IS NOT NULL"
    params: tuple = ()
    if since:
        where += " AND session_date >= ?"
        params = (since,)
    with _connect_ro(ml_db) as c:
        rows = list(c.execute(
            f"SELECT v2_decision_id, market, session_date, entry_price, mfe_pct, pnl_pct_net, "
            f"fee_pct_round_trip FROM v2_learning_performance WHERE {where}",
            params,
        ))
    out = []
    for did, m, sd, ep, mfe, net, fee in rows:
        out.append(dict(did=str(did or ""), market=m, session_date=sd, entry=ep,
                        mfe=mfe, net=net, fee=fee))
    return out


def tier_counterfactual(t: dict, target: float, level: float, f: float, cost: float) -> dict | None:
    """도달레벨 level에서 f 부분익절 + (1-f) 러너(실제net). target_pct<=0 plan 제외."""
    ep = t["entry"]
    if not ep or target is None:
        return None
    target_pct = (target - ep) / ep * 100.0
    if target_pct <= 0:
        return None
    reached = t["mfe"] >= level
    if reached:
        cf_net = f * (level - cost) + (1.0 - f) * t["net"]
    else:
        cf_net = t["net"]
    return dict(target_pct=target_pct, reached=reached, cf_net=cf_net, actual_net=t["net"])


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    a = [r["actual_net"] for r in rows]
    c = [r["cf_net"] for r in rows]
    a_s, c_s = sorted(a), sorted(c)
    return {
        "n": len(rows),
        "reach_rate": round(sum(r["reached"] for r in rows) / len(rows), 3),
        "actual_mean": round(mean(a), 3), "actual_median": round(median(a), 3),
        "tier_mean": round(mean(c), 3), "tier_median": round(median(c), 3),
        "delta_mean": round(mean(c) - mean(a), 3),
        "actual_max": round(a_s[-1], 3), "tier_max": round(c_s[-1], 3),  # 러너 보존 확인
        "actual_p90": round(a_s[int(len(a_s) * 0.9)], 3), "tier_p90": round(c_s[int(len(c_s) * 0.9)], 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="조기익절 tier shadow 판독 (read-only)")
    ap.add_argument("--ml-db", default=str(DEFAULT_ML_DB))
    ap.add_argument("--event-db", default=str(DEFAULT_EVENT_DB))
    ap.add_argument("--since", default=None, help="session_date >= (forward 누적판독)")
    ap.add_argument("--level-us", type=float, default=DEFAULT_LEVEL["US"])
    ap.add_argument("--level-kr", type=float, default=DEFAULT_LEVEL["KR"])
    ap.add_argument("--f", type=float, default=DEFAULT_F, help="부분익절 비율 (0~1)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    plans = load_plan_targets(Path(args.event_db))
    trades = load_trades(Path(args.ml_db), args.since)
    levels = {"US": args.level_us, "KR": args.level_kr}

    by_mkt: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        target = plans.get(t["did"])
        cf = tier_counterfactual(t, target, levels.get(t["market"], 99.0),
                                 args.f, DEFAULT_COST.get(t["market"], 0.5))
        if cf is not None:
            by_mkt[t["market"]].append(cf)

    result = {
        "params": {"level": levels, "f": args.f, "since": args.since},
        "markets": {m: summarize(rows) for m, rows in by_mkt.items()},
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"=== 조기익절 tier shadow (level {levels}, f={args.f}, since={args.since}) ===")
    for m in ("US", "KR"):
        s = result["markets"].get(m, {"n": 0})
        if s["n"] == 0:
            print(f"  {m}: (표본 없음)")
            continue
        print(f"  {m} (n={s['n']}, 도달률 {s['reach_rate']}):")
        print(f"     net  실제 mean {s['actual_mean']:+.3f} / med {s['actual_median']:+.3f}"
              f"  → tier mean {s['tier_mean']:+.3f} / med {s['tier_median']:+.3f}  (Δmean {s['delta_mean']:+.3f})")
        print(f"     러너 보존  실제 max {s['actual_max']:+.2f} / p90 {s['actual_p90']:+.2f}"
              f"  → tier max {s['tier_max']:+.2f} / p90 {s['tier_p90']:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
