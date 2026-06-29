"""
cap_widen_exit_sim — entry_price_cap 완화로 '진입했을' 케이스에 청산 규칙(TP/SL)을
결합해 net 손익을 backfill 측정한다 (read-only, API 없음).

앞 단계(cap_widen_shadow_review)는 '보유 시 종가 손익'만 봤다 → D+3 되돌림으로 음전.
이 도구는 max_runup_3d(고점)/max_drawdown_3d(저점)/forward_3d(종가) 라벨로
TP(익절)·SL(손절) 청산을 적용했을 때 net 이 양(+)으로 돌아서는지 측정한다.

청산 모델 (일봉 라벨 기반 근사):
- 3일 내 고점 = +max_runup_3d, 저점 = max_drawdown_3d(음수), 종가 = forward_3d
- TP/SL 둘 다 닿을 수 있고 순서는 일봉 라벨로 알 수 없음 →
    낙관(opt): 익절이 먼저 닿았다고 가정  → hit_tp:+TP / elif hit_sl:-SL / else fwd
    비관(pes): 손절이 먼저 닿았다고 가정  → hit_sl:-SL / elif hit_tp:+TP / else fwd
  실제 net 은 두 값 사이. TP-only(SL 없음)도 별도 출력.

한계:
- 진입가 = 당일 '종가' 가정(라벨 기준가). 실제 추격 진입가(current_price)는 더 높아
  net 은 '낙관적 상한'. 정밀 보정은 price CSV 일별 OHLC 과제(미구현).
- 일봉 max/min 라벨 → 장중 trail 의 정확한 경로는 재현 불가. TP/SL 도달 여부만.

CLI:
  python tools/cap_widen_exit_sim.py --market KR --start 20260506 --end 20260620 \
      --multipliers 0.005,0.01 --tp 3,5 --sl 2,3
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
from tools.cap_widen_shadow_review import _collect_cap_cases, _num  # noqa: E402

_SEL_DB = _ROOT / "data" / "ticker_selection_log.db"


def _load_labels(market: str, keys: set[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """(date,ticker) -> {runup, dd, fwd}. ro 연결, busy_timeout."""
    out: dict[tuple[str, str], dict] = {}
    if not _SEL_DB.exists():
        return out
    con = sqlite3.connect(f"file:{_SEL_DB}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=8000")
    cur = con.cursor()
    try:
        for (date, ticker) in keys:
            d = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"
            row = cur.execute(
                "SELECT max_runup_3d, max_drawdown_3d, forward_3d FROM ticker_selection_log "
                "WHERE market=? AND ticker=? AND date=? "
                "AND forward_3d IS NOT NULL AND max_runup_3d IS NOT NULL "
                "ORDER BY id LIMIT 1",
                (market, ticker, d),
            ).fetchone()
            if row is not None:
                out[(date, ticker)] = {
                    "runup": _num(row[0]), "dd": _num(row[1]), "fwd": _num(row[2]),
                }
    finally:
        con.close()
    return out


def _exit_net(lab: dict, tp: Optional[float], sl: Optional[float], optimistic: bool) -> Optional[float]:
    runup, dd, fwd = lab.get("runup"), lab.get("dd"), lab.get("fwd")
    if fwd is None:
        return None
    hit_tp = tp is not None and runup is not None and runup >= tp
    hit_sl = sl is not None and dd is not None and dd <= -sl
    if optimistic:
        if hit_tp:
            return tp
        if hit_sl:
            return -sl
    else:
        if hit_sl:
            return -sl
        if hit_tp:
            return tp
    return fwd


def _stat(vals: list[float]) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "win": None}
    wins = sum(1 for v in vals if v > 0)
    return {"n": len(vals), "mean": round(statistics.mean(vals), 3),
            "win": round(100.0 * wins / len(vals), 1)}


def _fmt(label: str, s: dict) -> str:
    if s["n"] == 0:
        return f"    {label:26s} n=0"
    return f"    {label:26s} mean={s['mean']:+6.2f}%  win={s['win']:5.1f}%  (n={s['n']})"


def run(market, start, end, multipliers, tps, sls):
    cases, scanned = _collect_cap_cases(market, start, end)
    keys = set(cases.keys())
    lab = _load_labels(market, keys)
    matched = {k: cases[k] for k in keys if k in lab}

    print("=" * 80)
    print(f" cap_widen_exit_sim  market={market}  기간={start}~{end}")
    print(f" cap_exceeded 케이스={len(cases)}  라벨매칭(측정가능)={len(matched)}")
    print("=" * 80)
    if not matched:
        print(" 측정가능 0 — 라벨 미충전/기간 확인.")
        return

    for m in sorted(multipliers):
        sub = [k for k in matched
               if (matched[k]["min_current"] / matched[k]["cap"] - 1.0) <= m]
        if not sub:
            print(f"\n[m=+{m*100:.1f}%] 진입가능 0")
            continue
        labs = [lab[k] for k in sub]
        print(f"\n[m=+{m*100:.1f}%] 진입가능 {len(sub)}/{len(matched)}")
        # baseline: 청산 없이 D+3 종가 보유
        print(_fmt("보유(청산없음, fwd_3d)", _stat([x["fwd"] for x in labs])))
        # TP-only (즉시 익절, 손절 없음)
        for tp in tps:
            print(_fmt(f"TP{tp:g}%-only (opt)", _stat([_exit_net(x, tp, None, True) for x in labs])))
        # TP+SL 낙관/비관 밴드
        for tp in tps:
            for sl in sls:
                opt = _stat([_exit_net(x, tp, sl, True) for x in labs])
                pes = _stat([_exit_net(x, tp, sl, False) for x in labs])
                band = (f"    TP{tp:g}/SL{sl:g}              "
                        f"opt={opt['mean']:+6.2f}%(win{opt['win']:.0f}) | "
                        f"pes={pes['mean']:+6.2f}%(win{pes['win']:.0f})")
                print(band)

    print("\n" + "-" * 80)
    print(" 읽는 법: opt=익절먼저 / pes=손절먼저 가정. 실제는 두 값 사이.")
    print("  - '보유' 대비 TP/SL 적용이 net 을 +로 올리면 → 청산결합이 cap완화의 전제조건.")
    print("  - pes 까지 +면 강한 신호. opt만 +면 청산타이밍 의존(취약).")
    print(" 한계: 진입가=당일종가(낙관상한), 일봉라벨(경로 미상), 표본 소수.")


def main():
    p = argparse.ArgumentParser(description="cap 완화 × 청산(TP/SL) 결합 측정 (read-only)")
    p.add_argument("--market", choices=["KR", "US"], default="KR")
    p.add_argument("--start", default="")
    p.add_argument("--end", default="")
    p.add_argument("--multipliers", default="0.005,0.01")
    p.add_argument("--tp", default="3,5", help="익절 %, 콤마구분")
    p.add_argument("--sl", default="2,3", help="손절 %, 콤마구분")
    a = p.parse_args()
    mults = [float(x) for x in a.multipliers.split(",") if x.strip()]
    tps = [float(x) for x in a.tp.split(",") if x.strip()]
    sls = [float(x) for x in a.sl.split(",") if x.strip()]
    run(a.market, a.start, a.end, mults, tps, sls)


if __name__ == "__main__":
    main()
