#!/usr/bin/env python3
"""loss-exit 분봉 replay — "손실측 출구를 바꾸면 net 드레인이 주나" (2026-07-05, read-only).

배경: US 최대 드레인=CLOSED_LOSS_CAP(n53 gross합 -122.68 ≈ -2.31%/거래). 현행 -2% 손절이 최적인가?
이 도구는 각 US live CLOSED 거래의 실제 진입후 분봉경로(양방향, no-lookahead)에 **손실측 정책만** 바꿔
실현 net을 재생한다. 상방은 승자 trail3(+4%부터 3% 되밀림 청산) 공통 고정 → 손실측 차이만 격리.

손실 정책:
  s10/s15/s20/s30 : 고정 손절 -1.0/-1.5/-2.0(현행)/-3.0%
  time30/time60   : -2% 손절 유지 + 진입후 N봉 지나도 close pnl<+thr%면(러너 아니면) 청산=dead money 조기컷
  nostop          : 손절 없음(상방 trail + 마감보유만)

정직: (a)손절/목표 레벨서 체결 가정(stop=레벨가, 갭다운시 낙관 가능=현실은 더 나쁨). (b)분봉 창은 진입후
가용 전체(멀티데이 일부 결측). (c)매매 무접촉·기존 DB만. (d)-2%=운영자 리스크게이트라 반증엔 stress-test 필수.
"""
from __future__ import annotations
import argparse, csv, json, sqlite3, statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "v2_event_store.db"
MIN = ROOT / "data" / "price" / "minute" / "us"
COST_US = 0.70
TRAIL_ACT = 4.0   # 상방 trail 활성 임계(+%)
TRAIL_GIVE = 3.0  # 되밀림 청산(%)

# (fixed_stop_pct or None, time_nbars or None, time_thr_pct)
POLICIES = {
    "s10":    (-1.0, None, 0.0),
    "s15":    (-1.5, None, 0.0),
    "s20":    (-2.0, None, 0.0),   # 현행 baseline
    "s30":    (-3.0, None, 0.0),
    "time30": (-2.0, 30, 0.5),     # -2% + 30봉 후 <+0.5%면 컷
    "time60": (-2.0, 60, 1.0),     # -2% + 60봉 후 <+1.0%면 컷
    "nostop": (None, None, 0.0),
}


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
    """상방 trail3 공통. 손실측만 policy로. 실현 gross% 반환."""
    if entry <= 0 or not bars:
        return None
    fstop, ntime, tthr = POLICIES[policy]
    stop_px = entry * (1 + fstop / 100) if fstop is not None else None
    peak = entry
    act = False
    for i, (_, hi, lo, cl) in enumerate(bars):
        # 1) 손절 먼저(보수적)
        if stop_px is not None and lo <= stop_px:
            return fstop
        # 2) 상방 trail(공통)
        peak = max(peak, hi)
        if not act and peak >= entry * (1 + TRAIL_ACT / 100):
            act = True
        if act and cl <= peak * (1 - TRAIL_GIVE / 100):
            return (peak * (1 - TRAIL_GIVE / 100) / entry - 1) * 100
        # 3) 시간손절(러너=act면 면제, dead money만 컷)
        if ntime is not None and not act and i + 1 >= ntime:
            cl_pnl = (cl / entry - 1) * 100
            if cl_pnl < tthr:
                return cl_pnl
    return (bars[-1][3] / entry - 1) * 100  # 미발동 → 마지막 종가


def stat_line(name, v):
    if not v:
        return f"  {name:14} n=0"
    return (f"  {name:14} n={len(v):3d} 합{sum(v):+7.1f} per{st.mean(v):+.3f} "
            f"중앙{st.median(v):+.2f} 승{100*sum(1 for x in v if x>0)//len(v):3d}%")


def summarize(title, recs, pols):
    print(f"\n=== {title} (n={len(recs)}) ===")
    agg = {p: [x[p] for x in recs] for p in pols}
    agg["actual"] = [x["actual"] for x in recs]
    print(stat_line("실제(기록)", agg["actual"]))
    for p in pols:
        tag = p + ("*" if p == "s20" else "")
        print(stat_line(tag, agg[p]))
    base = st.mean(agg["s20"]) if agg["s20"] else 0.0
    print("  판정(s20=-2% 현행 대비 net Δ/거래, +면 개선):")
    for p in pols:
        if p == "s20" or not agg[p]:
            continue
        print(f"    {p:10} Δ{st.mean(agg[p])-base:+.3f}  (합 {sum(agg[p])-sum(agg['s20']):+.1f})")


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

    pols = list(POLICIES.keys())
    recs = []
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
        rec = {"ticker": tk, "date": sd, "month": sd[:7], "actual": float(g) - COST_US}
        ok = True
        for p in pols:
            r = replay(entry, bars, p)
            if r is None:
                ok = False
                break
            rec[p] = r - COST_US
        if ok:
            recs.append(rec)

    print(f"손절 replay: US live CLOSED, since {args.since}, 매칭 {len(recs)}건. "
          f"상방 trail{TRAIL_GIVE:.0f}(+{TRAIL_ACT:.0f}%활성) 공통, net=gross-{COST_US}. s20=-2% 현행.")
    summarize("전체", recs, pols)

    # OOS 월별
    for m in sorted({x["month"] for x in recs}):
        sub = [x for x in recs if x["month"] == m]
        summarize(f"월 {m}", sub, pols)

    # outlier 제거(baseline s20 실현 기준 top3/bottom3)
    ranked = sorted(recs, key=lambda x: x["s20"])
    trimmed = ranked[3:-3] if len(ranked) > 6 else ranked
    summarize("outlier 제거(s20 기준 상·하위 3건 제외)", trimmed, pols)

    print("\n주: 상방 trail3 공통이라 손실측 차이만. 분봉 양방향 no-lookahead. 손절=레벨가 체결(갭다운시 낙관). 매매 무접촉.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
