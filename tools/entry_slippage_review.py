from __future__ import annotations

"""진입 슬리피지 리뷰 — 발주가→체결가 갭 (read-only).

execution이 수익 레버인데 슬리피지 계측이 비어 있었다. 이 도구는 lifecycle_events의
ORDER_SENT(price_native=발주가)와 FILLED(fill_price_native=체결가)를 path_run_id로
조인해 진입 체결의 실제 슬리피지를 측정한다(매수 한정). 시장별 슬리피지 캡(KR 0.3%/
US 0.2%) 대비 초과 비율도 본다.

슬리피지%는 매수 기준 (체결가-발주가)/발주가*100 — 양수가 불리(비용). 입력은 로컬
sqlite(v2_event_store)뿐, 외부 호출 없음. 한 path_run에 발주/체결 다건이면 occurred_at
최초값을 쓴다(첫 진입 체결 기준).

주의(범위): 이 도구는 발주가→체결가 구간만 잰다. 트리거임계가→발주가 구간(loss_cap
오버슈트 원인 후보)은 트리거가가 lifecycle payload에 아직 기록되지 않아 측정 불가 —
런타임 기록 배선이 선행돼야 함(후속 과제).
"""

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EV_DB = ROOT / "data" / "v2_event_store.db"

# 진입 슬리피지 캡(편도). compute_buy_limit: current*1.003(KR)/1.002(US)
ENTRY_CAP_PCT = {"KR": 0.3, "US": 0.2}


@dataclass
class SlipStat:
    market: str
    n: int
    mean_pct: float
    median_pct: float
    adverse_rate_pct: float       # 슬리피지>0 (불리) 비율
    over_cap_rate_pct: float      # 캡 초과 비율
    cap_pct: float
    p90_pct: float | None
    worst_pct: float | None
    zero_fill_rate_pct: float     # 슬리피지=0 (발주가=체결가) 비율


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    conn.execute("PRAGMA busy_timeout=12000")
    return conn


def _is_buy_entry(p: dict[str, Any]) -> bool:
    """진입 매수만. side가 sell이거나 close_reason 있으면 청산 → 제외."""
    side = str(p.get("side") or "").strip().lower()
    if side in ("sell", "s", "매도"):
        return False
    if p.get("close_reason"):
        return False
    return True


def _percentile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    i = q * (len(sorted_vals) - 1)
    lo = int(i)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] * (1 - (i - lo)) + sorted_vals[hi] * (i - lo)


def load_slippage(
    ev_db: Path, runtime_mode: str | None
) -> tuple[dict[str, list[float]], dict[str, dict[str, int]]]:
    """(market->[슬리피지%], market->커버리지). execution_id로 ORDER_SENT 발주가↔FILLED 체결가 조인.

    커버리지: 매수 ORDER_SENT 총수 대비 발주가(price_native) 기록 수 — 측정 사각지대를 드러낸다.
    """
    conn = _connect_ro(ev_db)
    try:
        sent_rows = conn.execute(
            "SELECT market, runtime_mode, execution_id, occurred_at, payload_json "
            "FROM lifecycle_events WHERE event_type='ORDER_SENT' AND payload_json IS NOT NULL "
            "ORDER BY occurred_at"
        ).fetchall()
        fill_rows = conn.execute(
            "SELECT execution_id, occurred_at, payload_json "
            "FROM lifecycle_events WHERE event_type='FILLED' AND payload_json IS NOT NULL "
            "ORDER BY occurred_at"
        ).fetchall()
    finally:
        conn.close()

    cov: dict[str, dict[str, int]] = {"KR": {"buy_sent": 0, "with_price": 0},
                                      "US": {"buy_sent": 0, "with_price": 0}}
    sent: dict[Any, dict[str, Any]] = {}  # execution_id -> {price, market}
    for market, mode, exec_id, _occ, pj in sent_rows:
        if runtime_mode and mode != runtime_mode:
            continue
        try:
            p = json.loads(pj)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(p, dict) or not _is_buy_entry(p):
            continue
        mkt = "US" if str(market or "").upper() == "US" else "KR"
        cov[mkt]["buy_sent"] += 1
        price = p.get("price_native")
        if not exec_id or price in (None, 0):
            continue
        cov[mkt]["with_price"] += 1
        if exec_id not in sent:  # 첫 발주
            sent[exec_id] = {"price": float(price), "market": mkt}

    fill: dict[Any, float] = {}
    for exec_id, _occ, pj in fill_rows:
        if not exec_id:
            continue
        try:
            p = json.loads(pj)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(p, dict):
            continue
        fp = p.get("fill_price_native")
        if fp in (None, 0):
            continue
        if exec_id not in fill:  # 첫 체결
            fill[exec_id] = float(fp)

    out: dict[str, list[float]] = {"KR": [], "US": []}
    for exec_id, s in sent.items():
        if exec_id not in fill:
            continue
        sp, fp = s["price"], fill[exec_id]
        if sp <= 0:
            continue
        out[s["market"]].append((fp - sp) / sp * 100.0)
    return out, cov


def compute(market: str, slips: list[float]) -> SlipStat | None:
    if not slips:
        return None
    cap = ENTRY_CAP_PCT.get(market, 0.3)
    adverse = sum(1 for x in slips if x > 0)
    over = sum(1 for x in slips if x > cap)
    zero = sum(1 for x in slips if abs(x) < 1e-9)
    s = sorted(slips)
    return SlipStat(
        market=market, n=len(slips),
        mean_pct=round(mean(slips), 4), median_pct=round(median(slips), 4),
        adverse_rate_pct=round(adverse / len(slips) * 100, 1),
        over_cap_rate_pct=round(over / len(slips) * 100, 1),
        cap_pct=cap,
        p90_pct=round(_percentile(s, 0.9), 4),
        worst_pct=round(max(slips), 4),
        zero_fill_rate_pct=round(zero / len(slips) * 100, 1),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="진입 슬리피지 리뷰 (read-only)")
    ap.add_argument("--market", choices=["KR", "US", "both"], default="both")
    ap.add_argument("--runtime-mode", default="live", help="live/paper/all (기본 live)")
    ap.add_argument("--ev-db", default=str(DEFAULT_EV_DB))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    ev_db = Path(args.ev_db)
    if not ev_db.exists():
        print(f"[ERR] DB 없음: {ev_db}")
        return 2

    mode = None if args.runtime_mode == "all" else args.runtime_mode
    data, cov = load_slippage(ev_db, mode)
    markets = ["KR", "US"] if args.market == "both" else [args.market]
    results = [compute(m, data.get(m, [])) for m in markets]
    results = [r for r in results if r is not None]

    if args.json:
        print(json.dumps({"stats": [asdict(r) for r in results], "coverage": cov},
                         ensure_ascii=False, indent=2))
    else:
        print(f"=== 진입 슬리피지 리뷰 (mode={args.runtime_mode}, 매수 발주가→체결가) ===")
        for m in markets:
            c = cov.get(m, {})
            bs, wp = c.get("buy_sent", 0), c.get("with_price", 0)
            rate = f"{wp / bs * 100:.0f}%" if bs else "n/a"
            print(f"  [{m}] 발주가 기록 커버리지: {wp}/{bs} ({rate}) ← 측정 사각지대 지표")
        if not results:
            print("  매칭된 진입 체결 없음(발주가 기록 부재).")
        for r in results:
            print(f"\n[{r.market}] n={r.n}  (캡 {r.cap_pct}%)")
            print(f"  슬리피지 평균 {r.mean_pct:+.4f}%  median {r.median_pct:+.4f}%")
            print(f"  불리(>0) {r.adverse_rate_pct}%  캡초과 {r.over_cap_rate_pct}%  "
                  f"정확체결(=0) {r.zero_fill_rate_pct}%")
            print(f"  p90 {r.p90_pct:+.4f}%  worst {r.worst_pct:+.4f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
