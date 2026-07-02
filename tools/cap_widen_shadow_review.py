"""
cap_widen_shadow_review — entry_price_cap(buy_ready_price_cap_exceeded)을 넓혔을 때
"진입했을" 종목군의 사후 forward 손익을 read-only로 backfill 측정한다.

설계 (실거래 무영향):
- 입력: logs/funnel/action_routing_shadow_<YYYYMMDD>_<MKT>.jsonl  (이미 쌓인 로그)
- cap_exceeded 케이스 추출: routes[*].reason == 'buy_ready_price_cap_exceeded'
    runtime_gate.current_price / runtime_gate.entry_price_cap 사용
- (date,ticker) 단위 dedupe: 그날 cap에 가장 가까운(=초과율 최소) current_price 대표
- 가정 배수 sweep: current_price <= cap*(1+m) 이면 "넓혔으면 진입가능"
- 사후 손익: data/ticker_selection_log.db 의 forward_1d/3d, max_runup_3d (당일종가 기준 %)
- 집계: 배수별 진입가능 건수 / forward 평균·중앙값·승률 / max_runup

한계(리포트에도 출력):
- forward 기준가 = 당일 '종가'. cap 막힌 current_price(장중)는 보통 종가보다 높으므로
  이 net 은 추격 진입의 '낙관적 상한'. 정밀 보정은 2차(price CSV) 과제.
- 청산 무관, '진입 자체의 방향적 기대값'만 측정.

CLI:
  python tools/cap_widen_shadow_review.py --market KR \
      --start 20260506 --end 20260626 --multipliers 0.003,0.005,0.01
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import statistics
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
_FUNNEL_DIR = _ROOT / "logs" / "funnel"
_SEL_DB = _ROOT / "data" / "ticker_selection_log.db"

_FNAME_RE = re.compile(r"action_routing_shadow_(\d{8})_([A-Z]{2})\.jsonl$")
_CAP_REASON = "buy_ready_price_cap_exceeded"


def _num(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _iter_routes(obj: dict):
    """레코드에서 route dict 들을 산출 (routes 리스트 또는 단일 route)."""
    routes = obj.get("routes")
    if isinstance(routes, list):
        for r in routes:
            if isinstance(r, dict):
                yield r
    r = obj.get("route")
    if isinstance(r, dict):
        yield r


def _collect_cap_cases(market: str, start: str, end: str) -> dict[tuple[str, str], dict]:
    """(date,ticker) -> {cap, min_current, n_events}. 초과율 최소 current 대표."""
    cases: dict[tuple[str, str], dict] = {}
    files = sorted(glob.glob(str(_FUNNEL_DIR / f"action_routing_shadow_*_{market}.jsonl")))
    scanned = 0
    for path in files:
        m = _FNAME_RE.search(os.path.basename(path))
        if not m:
            continue
        date, mkt = m.group(1), m.group(2)
        if mkt != market:
            continue
        if start and date < start:
            continue
        if end and date > end:
            continue
        scanned += 1
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                top_ticker = obj.get("ticker")
                for r in _iter_routes(obj):
                    if r.get("reason") != _CAP_REASON:
                        continue
                    rg = r.get("runtime_gate") or {}
                    cur = _num(rg.get("current_price"))
                    cap = _num(rg.get("entry_price_cap"))
                    ticker = r.get("ticker") or top_ticker
                    if not ticker or cur is None or cap is None or cap <= 0 or cur <= 0:
                        continue
                    key = (date, str(ticker))
                    rec = cases.get(key)
                    if rec is None:
                        cases[key] = {"cap": cap, "min_current": cur, "n_events": 1}
                    else:
                        rec["n_events"] += 1
                        if cur < rec["min_current"]:
                            rec["min_current"] = cur
                            rec["cap"] = cap  # 가장 진입가능성 높은 시점의 cap
    return cases, scanned


def _load_forward(market: str, keys: set[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """(date,ticker) -> {forward_1d, forward_3d, max_runup_3d}. ro 연결."""
    out: dict[tuple[str, str], dict] = {}
    if not _SEL_DB.exists():
        return out
    con = sqlite3.connect(f"file:{_SEL_DB}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=8000")
    cur = con.cursor()
    try:
        for (date, ticker) in keys:
            # 정규화: funnel date=YYYYMMDD, sel db date=YYYY-MM-DD
            d = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"
            row = cur.execute(
                "SELECT forward_1d, forward_3d, max_runup_3d FROM ticker_selection_log "
                "WHERE market=? AND ticker=? AND date=? "
                "AND forward_1d IS NOT NULL ORDER BY id LIMIT 1",
                (market, ticker, d),
            ).fetchone()
            if row is not None:
                out[(date, ticker)] = {
                    "forward_1d": _num(row[0]),
                    "forward_3d": _num(row[1]),
                    "max_runup_3d": _num(row[2]),
                }
    finally:
        con.close()
    return out


def _stat(vals: list[float]) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "winrate": None}
    wins = sum(1 for v in vals if v > 0)
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 3),
        "median": round(statistics.median(vals), 3),
        "winrate": round(100.0 * wins / len(vals), 1),
    }


def _fmt_stat(label: str, s: dict) -> str:
    if s["n"] == 0:
        return f"  {label:22s} n=0"
    mean = f"{s['mean']:+.2f}%"
    med = f"{s['median']:+.2f}%"
    return f"  {label:22s} n={s['n']:3d}  mean={mean:>8s}  median={med:>8s}  win={s['winrate']:5.1f}%"


def run(market: str, start: str, end: str, multipliers: list[float]) -> None:
    cases, scanned = _collect_cap_cases(market, start, end)
    keys = set(cases.keys())
    fwd = _load_forward(market, keys)

    matched = {k: cases[k] for k in keys if k in fwd}

    print("=" * 78)
    print(f" cap_widen_shadow_review  market={market}  기간={start}~{end}")
    print(f" funnel 파일 스캔={scanned}  cap_exceeded (date,ticker) 케이스={len(cases)}")
    print(f" forward 라벨 매칭(측정가능)={len(matched)}  "
          f"미매칭(라벨 미충전/선정로그 없음)={len(cases) - len(matched)}")
    print("=" * 78)

    if not matched:
        print(" 측정 가능한 케이스 0 — forward 라벨이 아직 안 채워졌거나 기간/시장 확인 필요.")
        return

    # baseline: 막힌 케이스 전체
    base_f1 = [fwd[k]["forward_1d"] for k in matched]
    base_f3 = [fwd[k]["forward_3d"] for k in matched]
    base_ru = [fwd[k]["max_runup_3d"] for k in matched]
    print("\n[BASELINE] cap_exceeded 로 '막힌' 전체 케이스의 사후 (당일종가 기준)")
    print(_fmt_stat("forward_1d", _stat(base_f1)))
    print(_fmt_stat("forward_3d", _stat(base_f3)))
    print(_fmt_stat("max_runup_3d", _stat(base_ru)))

    print("\n[SWEEP] cap*(1+m) 으로 넓혔을 때 '진입가능'해지는 부분집합")
    for m in sorted(multipliers):
        sub = [k for k in matched
               if (matched[k]["min_current"] / matched[k]["cap"] - 1.0) <= m]
        f1 = [fwd[k]["forward_1d"] for k in sub]
        f3 = [fwd[k]["forward_3d"] for k in sub]
        ru = [fwd[k]["max_runup_3d"] for k in sub]
        cov = 100.0 * len(sub) / len(matched) if matched else 0.0
        print(f"\n  --- m=+{m*100:.1f}%  진입가능={len(sub)}/{len(matched)} ({cov:.0f}%) ---")
        print(_fmt_stat("forward_1d", _stat(f1)))
        print(_fmt_stat("forward_3d", _stat(f3)))
        print(_fmt_stat("max_runup_3d", _stat(ru)))

    print("\n" + "-" * 78)
    print(" 해석 한계:")
    print("  - forward 기준가=당일 '종가'. 추격 진입가(current_price)는 보통 종가보다 높아")
    print("    이 net 은 '낙관적 상한' — 실제 진입손익은 이보다 나쁠 수 있음(2차 CSV 보정 과제).")
    print("  - 청산 무관, 진입 방향성만. max_runup_3d 는 사후 3일 최대상승(고점 도달 가능성).")
    print("  - winrate>50% & mean>0 이 복수 배수에서 일관되면 'cap 소폭 완화' 검토 신호.")
    print("    음(-)이면 cap 유지가 옳다는 근거.")


def main() -> None:
    p = argparse.ArgumentParser(description="entry_price_cap 완화 shadow 측정 (read-only)")
    p.add_argument("--market", choices=["KR", "US"], default="KR")
    p.add_argument("--start", default="", help="YYYYMMDD (포함)")
    p.add_argument("--end", default="", help="YYYYMMDD (포함)")
    p.add_argument("--multipliers", default="0.003,0.005,0.01",
                   help="콤마구분 배수(예 0.003=+0.3%)")
    args = p.parse_args()
    mults = [float(x.strip()) for x in args.multipliers.split(",") if x.strip()]
    run(args.market, args.start, args.end, mults)


if __name__ == "__main__":
    main()
