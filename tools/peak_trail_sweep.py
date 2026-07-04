#!/usr/bin/env python3
"""peak-trail 파라미터 스윕 + 진입조건부 출구 — 분봉 replay (2026-07-05, read-only).

배경: capture_target_replay.py(승자 태우기)에서 trail(+4%활성·3%되밀림)이 양수 net.
이 도구는 (1) 활성레벨 × give-back 스윕으로 최적 셀을 찾고 과적합/단일월/outlier를 검증,
(2) 진입특성(zone-하단/tape색/reward_pct)별로 최적 출구가 갈리는지 판정한다.

규율: 양방향 replay·no-lookahead(조건=진입시점, 파라미터=고정 forward규칙). 손절 −2% 공통.
      net = gross − 0.70. dedup decision_id. DB ro+busy_timeout. 매매 무접촉.
정직: 분봉 목표/손절 체결가정, 멀티데이 창 일부 결측. May n작음(OOS 약함).
"""
from __future__ import annotations
import argparse, csv, json, sqlite3, statistics as st, bisect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "v2_event_store.db"
MIN = ROOT / "data" / "price" / "minute" / "us"
COST_US = 0.70
LOSS_STOP = -2.0

ACT_LEVELS = [3.0, 4.0, 5.0]
GIVES = [1.5, 2.0, 2.5, 3.0, 4.0]


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


def replay_trail(entry: float, bars, act: float, give: float):
    """peak-trail: 손절 −2% 공통. peak가 entry*(1+act%) 도달시 활성, 이후 close가 peak*(1-give%)
    이하로 되밀리면 청산. 미발동시 마지막 종가. gross% 반환."""
    if entry <= 0 or not bars:
        return None
    stop = entry * (1 + LOSS_STOP / 100)
    peak = entry
    active = False
    for _, hi, lo, cl in bars:
        if lo <= stop:  # 손절 먼저(보수적)
            return LOSS_STOP
        peak = max(peak, hi)
        if not active and peak >= entry * (1 + act / 100):
            active = True
        if active and cl <= peak * (1 - give / 100):
            return (peak * (1 - give / 100) / entry - 1) * 100
    return (bars[-1][3] / entry - 1) * 100


def replay_hold(entry: float, bars):
    if entry <= 0 or not bars:
        return None
    stop = entry * (1 + LOSS_STOP / 100)
    for _, hi, lo, cl in bars:
        if lo <= stop:
            return LOSS_STOP
    return (bars[-1][3] / entry - 1) * 100


def _min_diff(a: str, b: str) -> float:
    """두 'YYYY-MM-DDTHH:MM' 문자열 간 분 차이(a-b). 대략치(월경계 무시, 연속세션 판단용)."""
    from datetime import datetime
    fa = datetime.strptime(a[:16], "%Y-%m-%dT%H:%M")
    fb = datetime.strptime(b[:16], "%Y-%m-%dT%H:%M")
    return (fa - fb).total_seconds() / 60.0


def load_spy():
    """SPY 분봉 → 세션 분할. gap>120분이면 새 세션. 반환: (ts_list, close_list, sess_open_close[i])
    sess_open_close[i] = i번째 바가 속한 세션의 시가측 close(세션 첫 바 close)."""
    f = MIN / "us_SPY.csv"
    ts_list, close_list, sess_open = [], [], []
    if not f.exists():
        return ts_list, close_list, sess_open
    rows = []
    for r in csv.DictReader(open(f, encoding="utf-8-sig")):
        try:
            rows.append((str(r["ts"])[:16], float(r["close"])))
        except (KeyError, ValueError):
            continue
    rows.sort()
    cur_open = None
    prev_ts = None
    for ts, cl in rows:
        if prev_ts is None or _min_diff(ts, prev_ts) > 120:
            cur_open = cl  # 새 세션 시작 → 이 바 close를 세션 시가로
        ts_list.append(ts)
        close_list.append(cl)
        sess_open.append(cur_open)
        prev_ts = ts
    return ts_list, close_list, sess_open


def spy_intraday_ret(ts_list, close_list, sess_open, entry_ts):
    """진입시점 SPY close vs 그 세션 시가 SPY close(개장대비 %). 없으면 None."""
    if not ts_list:
        return None
    i = bisect.bisect_right(ts_list, entry_ts[:16]) - 1
    if i < 0 or i >= len(ts_list):
        return None
    op = sess_open[i]
    if not op:
        return None
    return (close_list[i] / op - 1) * 100


def fmt(name, v, width=16):
    if not v:
        return f"  {name:{width}} n=0"
    return (f"  {name:{width}} n={len(v):3d} 합{sum(v):+7.1f} per{st.mean(v):+.3f} "
            f"중앙{st.median(v):+.2f} 승{100*sum(1 for x in v if x>0)//len(v):3d}%")


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

    spy_ts, spy_cl, spy_open = load_spy()

    # 각 거래별 특성 + 분봉 사전로드
    trades = []  # dict: tk,sd,month,entry,bars,zone_pos,reward_pct,tape_ret,actual_net
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
        bl, bh, hp = d.get("buy_zone_low"), d.get("buy_zone_high"), d.get("hit_price")
        zone_pos = None
        if bl and bh and hp and float(bh) > float(bl):
            zone_pos = (float(hp) - float(bl)) / (float(bh) - float(bl))
        rp = d.get("reward_pct")
        rp = float(rp) if rp is not None else None
        # session open ts = 해당 거래일 첫 분봉 ts (그 ticker의 세션 시작에 근접). SPY 세션시가는
        # entry_filled_at 이전 SPY 시가를 쓰되 같은 US거래일 기준. 근사: bars[0][0]을 세션시작으로.
        tape = spy_intraday_ret(spy_ts, spy_cl, spy_open, efa)
        trades.append(dict(tk=tk, sd=sd, month=sd[:7], entry=entry, bars=bars,
                           zone_pos=zone_pos, reward_pct=rp, tape=tape,
                           actual_net=float(g) - COST_US))

    n = len(trades)
    print(f"=== peak-trail 스윕 + 진입조건부 출구 (US, since {args.since}, 거래 {n}건, 손절 −2% 공통) ===")
    by_m = {}
    for t in trades:
        by_m.setdefault(t["month"], 0)
        by_m[t["month"]] += 1
    print(f"월별: {by_m}")
    print(fmt("실제(기록)", [t["actual_net"] for t in trades]))

    # ---- (1) 파라미터 스윕 ----
    def net_list(subset, act, give):
        out = []
        for t in subset:
            r = replay_trail(t["entry"], t["bars"], act, give)
            if r is not None:
                out.append(r - COST_US)
        return out

    print("\n[1] 파라미터 스윕 — 셀=per-trade net (합계) / 전체 %d건" % n)
    header = "  act\\give  " + "".join(f"{g:>10}" for g in GIVES)
    print(header)
    grid = {}
    for act in ACT_LEVELS:
        cells = []
        for give in GIVES:
            v = net_list(trades, act, give)
            grid[(act, give)] = v
            cells.append(f"{st.mean(v):+.3f}")
        print(f"  +{act:<7.1f}  " + "".join(f"{c:>10}" for c in cells))
    print("  (참고 hold_close net per: %+.3f)" % st.mean([replay_hold(t["entry"], t["bars"]) - COST_US for t in trades]))

    # 최적 셀
    best_cell = max(grid, key=lambda k: st.mean(grid[k]))
    print(f"\n  최적 셀: act+{best_cell[0]} give{best_cell[1]}  per{st.mean(grid[best_cell]):+.3f} 합{sum(grid[best_cell]):+.1f}")
    # 인접 셀 안정성
    a, gv = best_cell
    ai, gi = ACT_LEVELS.index(a), GIVES.index(gv)
    neigh = []
    for da in (-1, 0, 1):
        for dg in (-1, 0, 1):
            na, ng = ai + da, gi + dg
            if 0 <= na < len(ACT_LEVELS) and 0 <= ng < len(GIVES) and (da, dg) != (0, 0):
                neigh.append(st.mean(grid[(ACT_LEVELS[na], GIVES[ng])]))
    print(f"  인접셀 net per 범위: {min(neigh):+.3f} ~ {max(neigh):+.3f} (최적과 급변 없으면 노이즈 아님)")

    # ---- 스트레스: 월별 + outlier ----
    print("\n[2] 스트레스 — 최적 셀 & trail3(act4 give3) 월별/outlier")
    for label, (act, give) in [("최적", best_cell), ("trail3", (4.0, 3.0))]:
        print(f"  ── {label} (act+{act} give{give}) ──")
        for m in sorted(by_m):
            sub = [t for t in trades if t["month"] == m]
            v = net_list(sub, act, give)
            print(fmt(f"    {m}", v, 12))
        full = net_list(trades, act, give)
        sv = sorted(full)
        trimmed = sv[:-3]
        print(f"    outlier: 전체 per{st.mean(full):+.3f} | top3제거 per{st.mean(trimmed):+.3f} 합{sum(trimmed):+.1f} (top3={[round(x,1) for x in sv[-3:]]})")

    # ---- (3) 진입조건부 출구 ----
    print("\n[3] 진입조건부 출구 — 조건별 최적 출구 비교")
    # 후보 정책들
    cand = {"tight(a3g1.5)": (3.0, 1.5), "trail3(a4g3)": (4.0, 3.0),
            "wide(a4g4)": (4.0, 4.0), "hold": None}

    def pol_net(subset, pol):
        if pol is None:
            return [replay_hold(t["entry"], t["bars"]) - COST_US for t in subset if replay_hold(t["entry"], t["bars"]) is not None]
        return net_list(subset, *pol)

    def cond_report(name, key, splitfn):
        avail = [t for t in trades if key(t) is not None]
        if len(avail) < 20:
            print(f"  {name}: n={len(avail)} 부족")
            return
        med = st.median([key(t) for t in avail])
        lo = [t for t in avail if key(t) <= med]
        hi = [t for t in avail if key(t) > med]
        print(f"  ── {name} (median {med:+.2f}, low n={len(lo)} / high n={len(hi)}) ──")
        for grp_name, grp in [(splitfn[0], lo), (splitfn[1], hi)]:
            cells = []
            for cn, cp in cand.items():
                v = pol_net(grp, cp)
                cells.append(f"{cn}={st.mean(v):+.3f}")
            bestp = max(cand, key=lambda cn: st.mean(pol_net(grp, cand[cn])))
            # 최적정책의 top3제거 재판정(outlier 의존성)
            bv = sorted(pol_net(grp, cand[bestp]))
            trim = st.mean(bv[:-3]) if len(bv) > 3 else float("nan")
            print(f"    {grp_name:20} " + " ".join(cells) + f"  → 최적:{bestp} (top3제거 {trim:+.3f})")
        return lo, hi, med

    # zone_pos: 낮을수록 buy_zone_low 근처(헤드룸↑)
    cond_report("zone_pos(진입위치)", lambda t: t["zone_pos"], ("하단진입(헤드룸↑)", "상단진입"))
    # tape: SPY 개장대비. 높을수록 초록
    cond_report("tape(SPY개장대비)", lambda t: t["tape"], ("빨강(지수↓)", "초록(지수↑)"))
    # reward_pct: 목표부풀림
    cond_report("reward_pct(목표부풀림)", lambda t: t["reward_pct"], ("낮은목표", "높은목표"))

    # 조건부 정책 vs 균일 trail3 — no-lookahead 배정
    print("\n[4] 조건부 배정 vs 균일 trail3 (조건=진입시점 known, 배정=고정규칙)")
    uni = net_list(trades, 4.0, 3.0)
    print(f"  균일 trail3: per{st.mean(uni):+.3f} 합{sum(uni):+.1f} n={len(uni)}")
    print("  주: 조건부 배정의 '최적'은 in-sample. 월별 부호일치 안하면 노이즈(아래 [3] 표에서 교차확인).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
