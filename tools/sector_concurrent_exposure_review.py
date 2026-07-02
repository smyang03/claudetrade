#!/usr/bin/env python3
"""G1 — 섹터/상관 동시발화 좌측꼬리 측정 (read-only, 2026-07-02 갭감사).

질문: 같은 섹터 종목을 동시 보유(동시발화)한 날의 합산 net 꼬리가,
단독 보유일 대비 얼마나 나쁜가? — 기존 cluster_halt(시간축: 당일 손절 N개)가
못 보는 사전 상관축(같은 베타 동시 배치)의 전용 계측.

판정문 고정(debate_funnel_gap_audit_20260702.md): 이 도구는 측정 전용 —
enforce(섹터 cap 배선)는 양방향 실측(동섹터 승자 공존, right-tail 절단 위험)
근거로 부당 판정. 다국면 누적 후에도 처방은 별도 검증을 거친다.

방법: v2_path_runs CLOSED의 보유구간(entry_filled_at→sell시각) 겹침 × 섹터맵
(yfinance, 캐시 data/analysis/sector_map_cache.json) → 일별 최대 동섹터 동시보유 k
버킷별 일합산 net 분포(평균·중앙·좌꼬리 분위수) + 동시발화 에피소드 목록.
주문·상태 무접촉, DB mode=ro.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "v2_event_store.db"
CACHE = ROOT / "data" / "analysis" / "sector_map_cache.json"
SELL_TIME_KEYS = ("sell_order_sent_at", "local_sell_order_at", "sell_pending_resolution_at")


def _parse_dt(raw) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _sector(cache: dict, market: str, ticker: str) -> str:
    key = f"{market}|{ticker}"
    if key in cache:
        return cache[key]
    sector = "UNKNOWN"
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        syms = [ticker] if market == "US" else [ticker + ".KS", ticker + ".KQ"]
        for sym in syms:
            try:
                info = yf.Ticker(sym).info or {}
                s = info.get("sector") or info.get("industry")
                if s:
                    sector = str(s)
                    break
            except Exception:
                continue
    except Exception:
        pass
    cache[key] = sector
    return sector


def _stats_line(vals: list[float]) -> str:
    v = sorted(vals)
    if not v:
        return "n=0"
    p10 = v[max(0, int(round(0.1 * (len(v) - 1))))]
    return (f"n={len(v)} mean={st.mean(v):+.2f} med={st.median(v):+.2f} "
            f"p10(좌꼬리)={p10:+.2f} min={v[0]:+.2f} 합={sum(v):+.1f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="G1 섹터 동시발화 노출 측정 (read-only)")
    ap.add_argument("--since", default="2026-04-01", help="session_date 하한")
    ap.add_argument("--market", choices=["KR", "US"], default="US")
    ap.add_argument("--min-overlap-min", type=int, default=5, help="동시보유 최소 겹침(분)")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    rows = conn.execute(
        "SELECT ticker, session_date, plan_json FROM v2_path_runs "
        "WHERE status='CLOSED' AND market=? AND session_date>=?",
        (args.market, args.since),
    ).fetchall()
    conn.close()

    cache = _load_cache()
    # 보유구간 수집
    holds = []  # (session_date, ticker, sector, entry_dt, exit_dt, gross, net_est)
    for ticker, sd, pj in rows:
        plan = json.loads(pj or "{}")
        ent = _parse_dt(plan.get("entry_filled_at")) or _parse_dt(plan.get("filled_at"))
        ex = None
        for k in SELL_TIME_KEYS:
            ex = _parse_dt(plan.get(k))
            if ex:
                break
        if not ent or not ex or ex <= ent:
            continue
        sector = _sector(cache, args.market, str(ticker))
        holds.append((str(sd), str(ticker), sector, ent, ex,
                      plan.get("pnl_pct"), plan.get("pnl_pct_net_est")))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    # 일별: 최대 동섹터 동시보유 k + 일합산 net
    by_day = defaultdict(list)
    for h in holds:
        by_day[h[0]].append(h)
    day_rows = []
    episodes = []
    min_ov = args.min_overlap_min * 60
    for sd, hs in sorted(by_day.items()):
        max_k = 1
        best_ep = None
        by_sec = defaultdict(list)
        for h in hs:
            if h[2] != "UNKNOWN":
                by_sec[h[2]].append(h)
        for sec, group in by_sec.items():
            if len(group) < 2:
                continue
            # 그룹 내 쌍별 겹침 → 동시보유 카운트(스위프)
            events = []
            for _, tk, _, ent, ex, g, n in group:
                events.append((ent, 1, tk))
                events.append((ex, -1, tk))
            events.sort(key=lambda x: x[0])
            cur = 0
            peak = 0
            for t, d, _ in events:
                cur += d
                peak = max(peak, cur)
            # 겹침 최소시간 검증(쌍별)
            real_pairs = 0
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    ov = (min(group[i][4], group[j][4]) - max(group[i][3], group[j][3])).total_seconds()
                    if ov >= min_ov:
                        real_pairs += 1
            if peak >= 2 and real_pairs >= 1 and peak > max_k - 1:
                if peak > max_k or best_ep is None:
                    nets = [x[6] if x[6] is not None else x[5] for x in group]
                    nets = [x for x in nets if x is not None]
                    best_ep = (sec, [x[1] for x in group], round(sum(nets), 2) if nets else None)
                max_k = max(max_k, peak)
        day_net = [h[6] if h[6] is not None else h[5] for h in hs]
        day_net = [x for x in day_net if x is not None]
        if day_net:
            day_rows.append((sd, max_k, sum(day_net), len(hs)))
        if best_ep and max_k >= 2:
            episodes.append((sd, max_k, *best_ep))

    print(f"=== G1 섹터 동시발화 노출 ({args.market}, since {args.since}) ===")
    print(f"보유구간 표본 {len(holds)}건 / 거래일 {len(day_rows)}일 / 섹터 미상 "
          f"{sum(1 for h in holds if h[2]=='UNKNOWN')}건")
    if not day_rows:
        print("→ 표본 없음.")
        return 0

    print("\n[일별 합산 net 분포 — 최대 동섹터 동시보유 k 버킷]")
    by_k = defaultdict(list)
    for sd, k, dn, cnt in day_rows:
        by_k["k=1(동시발화 없음)" if k < 2 else ("k=2" if k == 2 else "k>=3")].append(dn)
    for kb in ("k=1(동시발화 없음)", "k=2", "k>=3"):
        if by_k.get(kb):
            print(f"  {kb:18}: {_stats_line(by_k[kb])}")

    print("\n[동시발화 에피소드 (최대 k 섹터)]")
    for sd, k, sec, tks, net in sorted(episodes, key=lambda x: (x[4] if x[4] is not None else 0)):
        print(f"  {sd} k={k} {sec[:24]:24} {','.join(tks[:5]):40} 그룹net합={net}")

    print("\n주: 측정 전용 — enforce(섹터 cap) 부당 판정 유지(양방향: 동섹터 승자 공존 실측).")
    print("    다국면 누적 후 k>=2 좌꼬리(p10)가 k=1 대비 유의하게 나쁠 때만 처방 논의 개시.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
