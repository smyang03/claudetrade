#!/usr/bin/env python3
"""US vol_ratio 실값 연결 효과 오프라인 시뮬 (read-only, API 미사용).

무엇을 재는가
  US vol_ratio는 1.0 placeholder였고 아래 게이트가 이를 실값처럼 소비했다:
    volatility_breakout : vol_ratio > vol_mult      → placeholder면 영구 미발동
    mean_reversion      : vol_ratio < vol_limit     → placeholder면 필터 영구 무력
    bucket_classifier   : vol_ratio >= 2.0 (US)     → volume_surge 태그 영구 부재
    screen_score        : + vol_ratio * 4.0         → 랭킹에서 거래량 항이 상수
  rel_vol_shadow(장중누적 / (20일평균 × 세션진행률))가 실값이므로, 그것을 썼을 때
  ① 게이트 통과 집합이 어떻게 바뀌고 ② 그 집합의 실현 forward net이 어떻게 바뀌는지
  실제 원장으로 비교한다.

데이터
  data/ticker_selection_log.db — 후보 피처 + forward_1d/3d/5d + max_runup/drawdown 실측.
  네트워크·브로커·LLM 호출 없음. 라이브 상태를 건드리지 않는다.

한계 (정직)
  - forward_*는 종가 기준 gross다. 우리 실제 체결가·슬리피지·부분체결과 다르다.
    따라서 절대 net이 아니라 **조건 간 상대 비교**로만 읽는다.
  - rel_vol_shadow는 2026-07-21부터 배선됐다(그 전 0%). 표본 기간이 짧다.
  - 개장 15분 이전 후보는 rel_vol이 계산되지 않는다(설계상 결측).

집계 규율 (v2에서 교정)
  - 같은 세션의 같은 티커는 rescreen 배치마다 여러 행으로 남는다. 그대로 집계하면
    한 종목이 수십 번 세어져 표본이 부풀고 상위K가 같은 종목으로 채워진다.
    → (date, market, ticker) 1행으로 dedup한 뒤에만 집계한다.
  - placeholder vs 실값 비교는 **같은 모집단**(rel_vol 실값 보유 행)에서만 한다.
    실값 결측 행을 한쪽에만 포함하면 게이트 건수 차이가 값 차이가 아니라
    모집단 차이가 된다.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_DB = ROOT / "data" / "ticker_selection_log.db"

# 원장 실측 왕복비용(net_basis 상수). tools/analysis 공통 가정.
COST_PCT = {"KR": 0.21, "US": 0.50}

# 전략 파라미터 실값 (strategy/*.py params 기준)
VB_VOL_MULT_US = 1.3          # volatility_breakout: vol_ratio > vol_mult
MR_VOL_LIMIT_US = 2.0         # mean_reversion: vol_ratio < vol_limit
BUCKET_SURGE_US = 2.0         # bucket_classifier: vol_ratio >= 2.0


def connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=10000")
    con.row_factory = sqlite3.Row
    return con


def load_rows(con: sqlite3.Connection, market: str, since: str) -> list[dict]:
    rows = con.execute(
        """
        SELECT date, market, ticker, selected_at, selection_batch_id,
               selection_rank, source_type, consensus_mode,
               change_pct, vol_ratio, rel_vol_shadow, gap_pct, from_high_pct,
               forward_1d, forward_3d, forward_5d,
               max_runup_3d, max_drawdown_3d, max_runup_5d, max_drawdown_5d
        FROM ticker_selection_log
        WHERE bot_mode='live' AND market=? AND date >= ?
          AND forward_1d IS NOT NULL
        ORDER BY date, ticker, selected_at, id
        """,
        (market, since),
    ).fetchall()
    return [dict(r) for r in rows]


def dedup(rows: list[dict], mode: str) -> list[dict]:
    """(date, market, ticker)당 1행으로 축약한다.

    원장은 rescreen 배치마다 같은 종목을 다시 기록한다(US 7/21~ 기준 그룹당
    중앙값 20행). 그대로 집계하면 한 종목이 수십 번 세어져 표본이 부풀고
    '세션 상위 K'가 같은 종목 반복으로 채워진다. forward_*는 그룹 내 불변이므로
    행 선택은 피처 시점만 바꾼다.

    mode:
      first_valid — rel_vol 실값이 처음 생긴 배치(게이트가 실제로 판정 가능한
                    최초 시점). 기본값.
      first       — 가장 이른 배치(개장 직후, rel_vol 결측일 수 있음)
      last        — 마지막 배치(세션 후반 = 정보 과다, 낙관 편향 주의)
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["date"], r["market"], r["ticker"])].append(r)
    out: list[dict] = []
    for _key, g in groups.items():
        g = sorted(g, key=lambda r: (str(r.get("selected_at") or ""), r.get("selection_batch_id") or ""))
        if mode == "last":
            pick = g[-1]
        elif mode == "first":
            pick = g[0]
        else:
            pick = next((r for r in g if r.get("rel_vol_shadow") is not None), g[0])
        pick = dict(pick)
        pick["_dup_rows"] = len(g)
        out.append(pick)
    return out


def net_close(row: dict, horizon: str) -> float | None:
    fwd = row.get(f"forward_{horizon}")
    if fwd is None:
        return None
    return float(fwd) - COST_PCT[row["market"]]


def net_target_stop(row: dict, target: float, stop: float, horizon: str) -> float | None:
    """target/stop 우선도달 근사. 동시 도달은 순서 미상이므로 비관(stop 먼저)."""
    ru = row.get(f"max_runup_{horizon}")
    dd = row.get(f"max_drawdown_{horizon}")
    fwd = row.get(f"forward_{horizon}")
    if ru is None or dd is None or fwd is None:
        return None
    hit_t = float(ru) >= target
    hit_s = float(dd) <= -stop
    if hit_t and hit_s:
        gross = -stop
    elif hit_t:
        gross = target
    elif hit_s:
        gross = -stop
    else:
        gross = float(fwd)
    return gross - COST_PCT[row["market"]]


def summarize(vals: list[float | None]) -> dict | None:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    n = len(clean)
    total = sum(clean)
    return {
        "n": n,
        "avg": total / n,
        "sum": total,
        "win": sum(1 for v in clean if v > 0) / n * 100,
        "median": statistics.median(clean),
    }


def fmt(s: dict | None) -> str:
    if not s:
        return "n=0"
    return (f"n={s['n']:5d} avg={s['avg']:+7.3f}% med={s['median']:+7.3f}% "
            f"sum={s['sum']:+9.1f} win={s['win']:5.1f}%")


def gate_sets(rows: list[dict], use_real: bool) -> dict[str, list[dict]]:
    """vol_ratio 소비 게이트를 재현해 통과 집합을 만든다.

    rows는 **양쪽 arm이 공유하는 모집단**이어야 한다(rel_vol 실값 보유 행만).
    한쪽에만 결측 행을 포함하면 통과 건수 차이가 값 차이가 아니라 모집단 차이가 된다.
    """
    passed: dict[str, list[dict]] = {"vb": [], "mr": [], "surge": []}
    for r in rows:
        if use_real:
            raw = r.get("rel_vol_shadow")
            if raw is None:
                raise ValueError("gate_sets: 실값 arm에 결측 행이 들어왔다(모집단 오염)")
            vr = float(raw)
        else:
            vr = float(r.get("vol_ratio") or 1.0)
        if vr > VB_VOL_MULT_US:
            passed["vb"].append(r)
        if vr < MR_VOL_LIMIT_US:
            passed["mr"].append(r)
        if vr >= BUCKET_SURGE_US:
            passed["surge"].append(r)
    return passed


def perm_pvalue(a: list[float], b: list[float], iters: int = 5000, seed: int = 20260726) -> float | None:
    """두 그룹 평균차의 순열검정 p (양측). 표본이 작을 때 격차를 그대로 믿지 않기 위함."""
    a = [v for v in a if v is not None]
    b = [v for v in b if v is not None]
    if len(a) < 3 or len(b) < 3:
        return None
    obs = abs(sum(a) / len(a) - sum(b) / len(b))
    pool = a + b
    na = len(a)
    rng = random.Random(seed)
    hits = 0
    for _ in range(iters):
        rng.shuffle(pool)
        pa = pool[:na]
        pb = pool[na:]
        if abs(sum(pa) / len(pa) - sum(pb) / len(pb)) >= obs:
            hits += 1
    return (hits + 1) / (iters + 1)


def rerank_topk(rows: list[dict], k: int, key: str) -> list[dict]:
    """세션별 상위 K를 뽑는다. key='rank'(현행) 또는 'rel_vol'(실값 재랭킹)."""
    by_session: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_session[r["date"]].append(r)
    out: list[dict] = []
    for _date, group in by_session.items():
        if key == "rank":
            ordered = sorted(group, key=lambda r: (r.get("selection_rank") or 999))
        else:
            scored = [r for r in group if r.get("rel_vol_shadow") is not None]
            ordered = sorted(scored, key=lambda r: -float(r["rel_vol_shadow"]))
        out.extend(ordered[:k])
    return out


def report_gates(rows: list[dict]) -> None:
    print("\n" + "=" * 78)
    print("[1] 게이트 통과 집합 — placeholder(현행) vs rel_vol 실값")
    print("=" * 78)
    pop = [r for r in rows if r.get("rel_vol_shadow") is not None]
    print(f"  공통 모집단 {len(pop)}건 (rel_vol 실값 보유 종목·세션). "
          f"결측 {len(rows) - len(pop)}건은 양쪽 arm 모두에서 제외.")
    if not pop:
        print("  → 비교 불가")
        return
    ph = gate_sets(pop, use_real=False)
    rv = gate_sets(pop, use_real=True)
    labels = {
        "vb": f"volatility_breakout (vol_ratio > {VB_VOL_MULT_US})",
        "mr": f"mean_reversion 거래량조건 (vol_ratio < {MR_VOL_LIMIT_US})",
        "surge": f"bucket volume_surge (vol_ratio >= {BUCKET_SURGE_US})",
    }
    for gate, label in labels.items():
        print(f"\n  {label}")
        for name, sets in (("현행(1.0)", ph), ("실값(rel_vol)", rv)):
            group = sets[gate]
            s1 = summarize([net_close(r, "1d") for r in group])
            s3 = summarize([net_close(r, "3d") for r in group])
            st = summarize([net_target_stop(r, 3.0, 2.0, "3d") for r in group])
            print(f"    {name:14s} 통과 {len(group):5d}건")
            print(f"       1d net  {fmt(s1)}")
            print(f"       3d net  {fmt(s3)}")
            print(f"       T3/S2   {fmt(st)}")


def report_rerank(rows: list[dict], ks: list[int]) -> None:
    print("\n" + "=" * 78)
    print("[2] 세션별 상위K 선별 — 현행 랭크 vs rel_vol 재랭킹")
    print("=" * 78)
    pop = [r for r in rows if r.get("rel_vol_shadow") is not None]
    sessions = len({r["date"] for r in pop})
    print(f"  공통 모집단 {len(pop)}건 / {sessions}세션 "
          f"(세션당 평균 {len(pop)/sessions:.1f}종목)" if sessions else "  표본 없음")
    for k in ks:
        cur = rerank_topk(pop, k, "rank")
        new = rerank_topk(pop, k, "rel_vol")
        print(f"\n  상위 {k}종목/세션")
        for name, group in (("현행 selection_rank", cur), ("rel_vol 재랭킹", new)):
            s1 = summarize([net_close(r, "1d") for r in group])
            s3 = summarize([net_close(r, "3d") for r in group])
            s5 = summarize([net_close(r, "5d") for r in group])
            st = summarize([net_target_stop(r, 3.0, 2.0, "3d") for r in group])
            print(f"    {name:20s}")
            print(f"       1d {fmt(s1)}")
            print(f"       3d {fmt(s3)}")
            print(f"       5d {fmt(s5)}")
            print(f"       T3/S2-3d {fmt(st)}")


def report_coverage(raw_rows: list[dict], rows: list[dict], mode: str) -> None:
    print("\n" + "=" * 78)
    print("[0] 표본·커버리지")
    print("=" * 78)
    total = len(rows)
    if not total:
        print("  표본 없음")
        return
    have = sum(1 for r in rows if r.get("rel_vol_shadow") is not None)
    dups = sum(r.get("_dup_rows", 1) for r in rows)
    print(f"  원장 원행 {len(raw_rows)}건 → dedup({mode}) 후 종목·세션 {total}건 "
          f"(중복 배치 {dups}행이 {total}건으로 축약, 평균 {dups/total:.1f}배)")
    print(f"  rel_vol 실값 보유 {have}건 ({have/total*100:.1f}%)")
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r)
    print("  일별 (rel_vol 보유율):")
    for d in sorted(by_date):
        g = by_date[d]
        h = sum(1 for r in g if r.get("rel_vol_shadow") is not None)
        print(f"    {d}: {len(g):5d}종목 중 {h:5d} ({h/len(g)*100:5.1f}%)")
    fwd5 = sum(1 for r in rows if r.get("forward_5d") is not None)
    print(f"  forward 커버리지: 1d {total}건 / 3d "
          f"{sum(1 for r in rows if r.get('forward_3d') is not None)}건 / 5d {fwd5}건")
    if len(by_date) < 10:
        print(f"  ★표본 한계: 세션 {len(by_date)}일뿐. 여기 격차는 방향 힌트일 뿐이며 "
              "enforce 근거로 쓰기엔 부족하다(순열검정 p 함께 볼 것).")


def report_split(rows: list[dict]) -> None:
    """rel_vol 상·하위 분할 격차와 순열검정 p."""
    print("\n" + "=" * 78)
    print("[3] rel_vol 상·하위 분할 (dedup 후, 순열검정)")
    print("=" * 78)
    pop = sorted((r for r in rows if r.get("rel_vol_shadow") is not None),
                 key=lambda r: float(r["rel_vol_shadow"]))
    n = len(pop)
    if n < 20:
        print(f"  표본 부족 n={n}")
        return
    cut = max(3, n // 5)
    bot, top = pop[:cut], pop[-cut:]
    for horizon in ("1d", "3d"):
        a = [net_close(r, horizon) for r in top]
        b = [net_close(r, horizon) for r in bot]
        st, sb = summarize(a), summarize(b)
        if not st or not sb:
            continue
        p = perm_pvalue(a, b)
        gap = st["avg"] - sb["avg"]
        ptxt = f"p={p:.3f}" if p is not None else "p=n/a"
        print(f"  {horizon}: 상위20% {fmt(st)}")
        print(f"      하위20% {fmt(sb)}")
        print(f"      격차 {gap:+.3f}%p  {ptxt}"
              f"{'  → 유의하지 않음' if (p or 1) > 0.05 else '  → p<0.05'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="US vol_ratio 실값 연결 효과 시뮬 (read-only)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--market", default="US")
    ap.add_argument("--since", default="2026-07-21",
                    help="rel_vol_shadow 배선 시작일 기본값")
    ap.add_argument("--topk", default="5,10,20")
    ap.add_argument("--dedup", default="first_valid",
                    choices=["first_valid", "first", "last"],
                    help="(date,ticker) 중복 배치 축약 기준")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"DB 없음: {db}")
        return 1
    con = connect(db)
    raw_rows = load_rows(con, args.market.upper(), args.since)
    con.close()
    if not raw_rows:
        print("표본 없음")
        return 1
    rows = dedup(raw_rows, args.dedup)

    if args.json:
        pop = [r for r in rows if r.get("rel_vol_shadow") is not None]
        ph = gate_sets(pop, use_real=False)
        rv = gate_sets(pop, use_real=True)
        payload = {
            "market": args.market.upper(),
            "since": args.since,
            "dedup": args.dedup,
            "raw_rows": len(raw_rows),
            "rows": len(rows),
            "population": len(pop),
            "sessions": len({r["date"] for r in rows}),
            "gates": {
                g: {
                    "placeholder_n": len(ph[g]),
                    "real_n": len(rv[g]),
                    "placeholder": summarize([net_close(r, "1d") for r in ph[g]]),
                    "real": summarize([net_close(r, "1d") for r in rv[g]]),
                }
                for g in ("vb", "mr", "surge")
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"시뮬 대상: {args.market.upper()} {args.since}~  (read-only, API 미사용, "
          f"dedup={args.dedup})")
    report_coverage(raw_rows, rows, args.dedup)
    report_gates(rows)
    report_rerank(rows, [int(x) for x in str(args.topk).split(",") if x.strip()])
    report_split(rows)
    print("\n※ forward는 종가 gross다. 절대 net이 아니라 조건 간 상대 비교로만 읽는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
