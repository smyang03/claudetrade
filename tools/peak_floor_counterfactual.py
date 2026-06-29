from __future__ import annotations

"""A1 — entry-floor 청산을 peak(ratchet) 기준 trail로 치환한 counterfactual 측정 (read-only).

배경(6인 패널 #3): entry 기준 출구 4종(CLOSED_PROFIT_FLOOR / CLOSED_WEAK_MFE /
CLOSED_MFE_BREAKEVEN / 일부 CLOSED_CLAUDE_PRICE_STOP)이 MFE를 반납한다. ladder peak-trail
(give 2.0%)이 US enforce에서 +0.35%p 실증. 이 peak-trail을 위 entry-floor 청산들에
치환하면 net이 얼마나 회수되는지를 국면(월)·시장(KR/US) 분리로 측정.

counterfactual 규칙(저장된 peak/mfe 기반, 경로 yfinance 미재구성):
  peak = entry * (1 + mfe/100)          # mfe = ledger mfe_pct 우선, 없으면 mfe_backfill_yf
  trail_trigger = peak * (1 - give)     # give 기본 2.0%
  - "발동(fired)": 실제 exit_price <= trail_trigger  → 가격이 trail 트리거를 하향 관통했으므로
    peak-trail이 먼저 그 더 높은 가격에 청산했을 것. cf_exit = trail_trigger (측정 신뢰).
  - "미발동": 실제 exit_price > trail_trigger → 실제 청산 시점에 trail이 아직 안 걸림.
    이후 경로(세션종가·다음floor)를 모르면 결과 불확정 → 별도 분류, cf 미산출.
  - mfe < give: trail_trigger < entry → trail이 손실 구간에서만 걸림(약MFE 출구는 회복 불가).

net = measured(pnl_pct_net) 우선, 없으면 gross - 왕복수수료(KR/US 0.5) - US 환전스프레드(0.2).
cf_net 도 동일 net 정의(수수료·환전 동일하므로 상쇄, gross 차이만 delta).

mode=ro + busy_timeout. broker/Claude/API 무호출. 쓰기 없음.
"""

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"

# 패널 #3가 지목한 entry-기준 출구. CLAUDE_PRICE_STOP은 "일부"만 entry-floor라 별도 표기.
TARGET_REASONS = (
    "CLOSED_PROFIT_FLOOR",
    "CLOSED_WEAK_MFE",
    "CLOSED_MFE_BREAKEVEN",
    "CLOSED_CLAUDE_PRICE_STOP",
)
DEFAULT_FEE_PCT = {"US": 0.5, "KR": 0.5}
DEFAULT_FX_SPREAD_PCT = {"US": 0.2, "KR": 0.0}
EPS = 1e-9


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def load_backfill_mfe(conn: sqlite3.Connection) -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        for r in conn.execute(
            "SELECT v2_decision_id,mfe_pct FROM mfe_backfill_yf "
            "WHERE mfe_pct IS NOT NULL AND source!='no_bars'"
        ):
            out[str(r[0])] = float(r[1])
    except sqlite3.Error:
        pass
    return out


def load_targets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ph = ",".join("?" * len(TARGET_REASONS))
    sql = (
        f"SELECT v2_decision_id,market,session_date,ticker,close_reason,"
        f"entry_price,exit_price,pnl_pct,pnl_pct_net,mfe_pct,mae_pct,net_basis "
        f"FROM v2_learning_performance "
        f"WHERE closed=1 AND runtime_mode='live' AND entry_price>0 "
        f"AND close_reason IN ({ph})"
    )
    return [dict(r) for r in conn.execute(sql, TARGET_REASONS).fetchall()]


def net_of(row: dict[str, Any], fee: dict[str, float], fx: dict[str, float]) -> float:
    mkt = str(row.get("market") or "").upper()
    f = fx.get(mkt, 0.0)
    if str(row.get("net_basis") or "") == "measured" and row.get("pnl_pct_net") is not None:
        return float(row["pnl_pct_net"]) - f
    return float(row["pnl_pct"]) - fee.get(mkt, 0.5) - f


def mfe_for(row: dict[str, Any], backfill: dict[str, float]) -> tuple[float | None, str | None]:
    v = row.get("mfe_pct")
    if v is not None:  # mfe=0.0도 유효값(진입 후 우호적 초과 전무 = 최악 leak 케이스) — 결측은 None뿐
        return float(v), "ledger"
    b = backfill.get(str(row.get("v2_decision_id") or ""))
    if b is not None:
        return b, "backfill"
    return None, None


def phase_of(row: dict[str, Any]) -> str:
    return str(row.get("session_date") or "")[:7] or "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description="entry-floor → peak-trail counterfactual (read-only)")
    ap.add_argument("--ml-db", default=str(DEFAULT_ML_DB))
    ap.add_argument("--give", type=float, default=2.0, help="peak-trail giveback %% (기본 2.0)")
    ap.add_argument("--fee-us", type=float, default=DEFAULT_FEE_PCT["US"])
    ap.add_argument("--fee-kr", type=float, default=DEFAULT_FEE_PCT["KR"])
    ap.add_argument("--fx-us", type=float, default=DEFAULT_FX_SPREAD_PCT["US"])
    ap.add_argument("--fx-kr", type=float, default=DEFAULT_FX_SPREAD_PCT["KR"])
    args = ap.parse_args()

    fee = {"US": args.fee_us, "KR": args.fee_kr}
    fx = {"US": args.fx_us, "KR": args.fx_kr}
    give = args.give / 100.0

    conn = _connect_ro(Path(args.ml_db))
    try:
        backfill = load_backfill_mfe(conn)
        rows = load_targets(conn)
    finally:
        conn.close()

    recs: list[dict[str, Any]] = []
    no_mfe = 0
    for r in rows:
        mfe, src = mfe_for(r, backfill)
        if mfe is None:
            no_mfe += 1
            continue
        entry = float(r["entry_price"])
        exitp = float(r["exit_price"]) if r.get("exit_price") not in (None, 0) else None
        peak = entry * (1.0 + mfe / 100.0)
        trig = peak * (1.0 - give)
        a_net = net_of(r, fee, fx)
        rec = {
            "id": r["v2_decision_id"], "mkt": str(r["market"]).upper(),
            "reason": r["close_reason"], "phase": phase_of(r),
            "tk": r["ticker"], "entry": entry, "exit": exitp,
            "mfe": mfe, "src": src, "peak": peak, "trig": trig,
            "a_net": a_net, "mfe_lt_give": mfe < args.give,
        }
        if exitp is None:
            rec["status"] = "no_exitprice"
            rec["cf_net"] = None
        elif exitp <= trig * (1.0 + EPS):
            # 가격이 trail 트리거를 하향 관통 → peak-trail이 더 높은 가격에 먼저 청산
            cf_gross = (trig / entry - 1.0) * 100.0
            # net 정의 동일(수수료·환전 동일) → cf_net = cf_gross - fee - fx
            cf_net = cf_gross - fee.get(rec["mkt"], 0.5) - fx.get(rec["mkt"], 0.0)
            rec["status"] = "fired"
            rec["cf_net"] = cf_net
            rec["delta"] = cf_net - a_net
        else:
            # trail이 실제 청산 시점에 아직 미발동 → 이후 경로 불확정
            rec["status"] = "not_fired_indeterminate"
            rec["cf_net"] = None
        recs.append(rec)

    print(f"=== A1: entry-floor → peak-trail(give {args.give}%) counterfactual (read-only) ===")
    print(f"대상 close_reason: {', '.join(TARGET_REASONS)}")
    print(f"전체 대상행: {len(rows)}  / MFE확보: {len(recs)}  / MFE부재(측정불가): {no_mfe}")
    print(f"net = measured(pnl_pct_net) 우선, 없으면 gross-fee(US/KR {fee})-fx(US {fx['US']})")
    print()

    fired = [r for r in recs if r["status"] == "fired"]
    indet = [r for r in recs if r["status"] == "not_fired_indeterminate"]
    noexit = [r for r in recs if r["status"] == "no_exitprice"]
    print(f"발동(fired, cf측정가능): {len(fired)}  / 미발동(불확정): {len(indet)}  / exit가없음: {len(noexit)}")
    print(f"  (참고) mfe<give[{args.give}%] = trail이 손실구간에서만 걸림: "
          f"{sum(1 for r in recs if r['mfe_lt_give'])}건")
    print()

    if not fired:
        print("발동 표본 0 — peak-trail이 더 나은 가격에 청산했을 건이 없음 → 측정상 회수 없음.")
    else:
        # 시장 × 국면(월) 집계
        print("=== 발동건 net Δ (cf_net - actual_net), 시장×국면(월) ===")
        grp: dict[tuple[str, str], list[float]] = defaultdict(list)
        for r in fired:
            grp[(r["mkt"], r["phase"])].append(r["delta"])
        print(f"{'mkt':4}{'월':9}{'n':>4}{'Δ평균':>9}{'Δ중앙':>9}{'Δ합':>9}{'개선':>6}{'악화':>6}")
        for (mkt, ph), d in sorted(grp.items()):
            better = sum(1 for x in d if x > 0.01)
            worse = sum(1 for x in d if x < -0.01)
            print(f"{mkt:4}{ph:9}{len(d):>4}{mean(d):>+9.2f}{median(d):>+9.2f}"
                  f"{sum(d):>+9.2f}{better:>6}{worse:>6}")
        print()
        print("=== 발동건 시장별 합계 ===")
        for mkt in ("US", "KR"):
            d = [r["delta"] for r in fired if r["mkt"] == mkt]
            if not d:
                continue
            an = [r["a_net"] for r in fired if r["mkt"] == mkt]
            cf = [r["cf_net"] for r in fired if r["mkt"] == mkt]
            print(f"  {mkt}: n={len(d)}  actual_net합={sum(an):+.2f}%  cf_net합={sum(cf):+.2f}%  "
                  f"Δ합={sum(d):+.2f}%p  Δ평균={mean(d):+.2f}%p")
        print()
        print("=== 발동건 close_reason별 Δ ===")
        cr: dict[str, list[float]] = defaultdict(list)
        for r in fired:
            cr[f"{r['mkt']}/{r['reason']}"].append(r["delta"])
        for k, d in sorted(cr.items()):
            print(f"  {k:42} n={len(d):>3} Δ합={sum(d):>+7.2f} Δ평균={mean(d):>+6.2f}")
        print()
        print("=== 발동건 상세 (Δ 오름차순) ===")
        print(f"{'mkt':4}{'reason':28}{'월':9}{'tk':8}{'mfe%':>7}{'src':>9}"
              f"{'a_net':>8}{'cf_net':>8}{'Δ':>8}")
        for r in sorted(fired, key=lambda x: x["delta"]):
            print(f"{r['mkt']:4}{r['reason'][:27]:28}{r['phase']:9}{str(r['tk'])[:7]:8}"
                  f"{r['mfe']:>7.2f}{r['src']:>9}{r['a_net']:>+8.2f}{r['cf_net']:>+8.2f}{r['delta']:>+8.2f}")

    if indet:
        print()
        print("=== 미발동(불확정) 상세 — 실제 floor가 trail트리거 위에서 청산, 이후 경로 모름 ===")
        print(f"{'mkt':4}{'reason':28}{'월':9}{'tk':8}{'mfe%':>7}{'a_net':>8}")
        for r in sorted(indet, key=lambda x: (x["mkt"], x["phase"])):
            print(f"{r['mkt']:4}{r['reason'][:27]:28}{r['phase']:9}{str(r['tk'])[:7]:8}"
                  f"{r['mfe']:>7.2f}{r['a_net']:>+8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
