#!/usr/bin/env python3
"""capture-target 분봉 replay — "승자를 +4에서 자르지 말고 태우면 net이 오르나" (2026-07-04, read-only).

배경: 수익은 fat-tail 러너(+4%↑ 23건=양수P&L 57%)가 전부인데 selection으론 못 고름(AUC0.49).
유일 offense=터진 러너를 안 자르고 태우기(capture). 현 시스템은 DETERMINISTIC_SELL_TARGET_CAP=4로
목표를 +4에 뭉쳐 fat-tail을 자를 수 있음(TARGET 출구 median +4.11). 이 도구는 각 거래의 실제 분봉
경로(진입후)에 목표를 +4/+6/+8/+10/trail로 바꿔 실현 net을 재생(양방향, no-lookahead). 손절 −2%
공통 적용해 상방 capture만 격리. give-back에 죽나 fat-tail을 살리나 판정.

정직: (a)분봉 replay는 목표/손절 레벨서 체결 가정. (b)진입후 가용 분봉 전체 창(멀티데이 일부 결측 가능).
(c)매매 무접촉·기존 DB만. give-back blanket=러너학살(메모리)이나 이건 *뛴 놈 목표천장 올리기*라 다름.
"""
from __future__ import annotations
import argparse, csv, json, sqlite3, statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "v2_event_store.db"
MIN = ROOT / "data" / "price" / "minute" / "us"
COST_US = 0.70
LOSS_STOP = -2.0  # 공통 손절(상방 capture 격리)


def load_bars(ticker: str, since_iso: str):
    f = MIN / f"us_{ticker}.csv"
    if not f.exists():
        return []
    out = []
    for r in csv.DictReader(open(f, encoding="utf-8-sig")):
        ts = str(r["ts"])
        if ts[:16] < since_iso[:16]:
            continue
        try:
            out.append((ts[:16], float(r["high"]), float(r["low"]), float(r["close"])))
        except (KeyError, ValueError):
            continue
    out.sort()
    return out


def replay(entry: float, bars, policy: str):
    """진입가 entry, 분봉 경로 bars에 policy 적용 → 실현 수익률%(gross). 손절 −2% 공통."""
    if entry <= 0 or not bars:
        return None
    stop = entry * (1 + LOSS_STOP / 100)
    peak = entry
    act = False  # trail 활성(peak가 목표 도달)
    tgt_pct = {"t4": 4, "t6": 6, "t8": 8, "t10": 10}.get(policy)
    trail_give = {"trail2": 2.0, "trail3": 3.0}.get(policy)
    for _, hi, lo, cl in bars:
        # 손절 먼저(보수적)
        if lo <= stop:
            return LOSS_STOP
        if tgt_pct is not None:
            if hi >= entry * (1 + tgt_pct / 100):
                return tgt_pct  # 목표 체결
        elif trail_give is not None:
            peak = max(peak, hi)
            if not act and peak >= entry * 1.04:  # +4%부터 trail 활성
                act = True
            if act and cl <= peak * (1 - trail_give / 100):
                return (peak * (1 - trail_give / 100) / entry - 1) * 100
        else:  # hold_close: 목표없이 끝까지
            peak = max(peak, hi)
    return (bars[-1][3] / entry - 1) * 100  # 미발동 → 마지막 종가


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-05-01")
    args = ap.parse_args()
    con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=8000")
    rows = con.execute("SELECT decision_id,ticker,session_date,plan_json,updated_at FROM v2_path_runs "
                       "WHERE status='CLOSED' AND market='US' AND runtime_mode='live' AND session_date>=?",
                       (args.since,)).fetchall()
    con.close()
    best = {}
    for did, tk, sd, pj, ua in rows:
        k = did or f"_{tk}_{sd}"
        if k not in best or (ua or "") > best[k][0]:
            best[k] = (ua or "", tk, sd, json.loads(pj or "{}"))

    pols = ["t4", "t6", "t8", "t10", "trail2", "trail3", "hold_close"]
    agg = {p: [] for p in pols}
    agg["actual"] = []
    matched = 0
    for _, tk, sd, d in best.values():
        efa = str(d.get("entry_filled_at") or "")
        entry = d.get("actual_entry_price") or d.get("hit_price") or d.get("entry_order_price")
        g = d.get("pnl_pct")
        if not efa or entry is None or g is None:
            continue
        try:
            entry = float(entry)
        except (TypeError, ValueError):
            continue
        bars = load_bars(tk, efa)
        if not bars:
            continue
        matched += 1
        agg["actual"].append(float(g) - COST_US)
        for p in pols:
            r = replay(entry, bars, p)
            if r is not None:
                agg[p].append(r - COST_US)

    def line(name, v):
        if not v:
            return f"  {name:12} n=0"
        return (f"  {name:12} n={len(v):3d} 합{sum(v):+7.1f} per{st.mean(v):+.3f} "
                f"중앙{st.median(v):+.2f} 승{100*sum(1 for x in v if x>0)//len(v):3d}%")

    print(f"=== capture-target 분봉 replay (US, since {args.since}, 매칭 {matched}건, 손절 −2% 공통) ===")
    print(line("실제(기록)", agg["actual"]))
    print("  ── 고정목표(승자를 +X%서 청산) ──")
    for p in ["t4", "t6", "t8", "t10"]:
        print(line(f"목표+{p[1:]}%", agg[p]))
    print("  ── 러너 태우기(trail: +4%부터 give만큼 되밀리면 청산) ──")
    for p in ["trail2", "trail3"]:
        print(line(p, agg[p]))
    print(line("끝까지보유", agg["hold_close"]))
    # 판정
    base = st.mean(agg["t4"]) if agg["t4"] else 0
    print("\n판정: 목표+4 대비 각 정책 net 개선(+면 태우기가 이김):")
    for p in ["t6", "t8", "t10", "trail2", "trail3", "hold_close"]:
        if agg[p]:
            print(f"  {p:12} Δ{st.mean(agg[p])-base:+.3f}/거래  (합 {sum(agg[p])-sum(agg['t4']):+.1f})")
    print("\n주: 손절 −2% 공통이라 상방 capture 차이만. 분봉 양방향(no-lookahead). "
          "\n    give-back(태우다 되밀림)은 trail/고정목표 net에 이미 반영. 매매 무접촉.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
