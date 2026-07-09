#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arm B — 저회전 sleeve forward 페이퍼 트래커 (설계: design_tsmom_shadow_sleeve_20260707 §3).

2-arm 페이퍼 장부: TSMOM(12-1 모멘텀+200dMA, 상위 25) vs EW(전 유니버스 동일가중).
Arm A 수정 결론에 따라 EW가 동등 벤치(핵심 가설="저회전이 회전 드레인을 죽인다").
격리 계약: 로컬 data/price CSV 읽기 + data/shadow/ 쓰기만. 라이브 경로·주문·brain 무접촉.

실행: 일 1회(장 마감 후 아무 때나, 멱등 — 같은 날 재실행 무해).
  python tools/tsmom_sleeve_tracker.py            # KR+US
판정 게이트: 설계 §4 (6개월+하락월, graduate/kill 조건 pre-registered).
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tsmom_sleeve_backtest import COST_RT, kr_universe, us_universe  # noqa: E402

SHADOW = ROOT / "data" / "shadow"
PRICE = {"US": ROOT / "data" / "price" / "us", "KR": ROOT / "data" / "price" / "kr"}
PREFIX = {"US": "us_", "KR": "kr_"}
BENCH = {"US": "SPY", "KR": "069500"}
CAPITAL = {"US": 10_000_000.0, "KR": 10_000_000.0}  # 페이퍼 명목(원화 스케일, 실자금 아님)
TOP_N = 25
FX_RT = {"US": 0.20, "KR": 0.0}  # 비관측(상한) — 리포트에 0/0.2 두 트랙 병기


def _closes(market: str, sym: str):
    f = PRICE[market] / f"{PREFIX[market]}{sym}.csv"
    if not f.exists():
        return None
    dates, closes = [], []
    try:
        for line in f.read_text(encoding="utf-8-sig").splitlines()[1:]:
            parts = line.split(",")
            if len(parts) < 5:
                continue
            try:
                c = float(parts[4])
            except ValueError:
                continue
            dates.append(parts[0])
            closes.append(c)
    except OSError:
        return None
    return (dates, closes) if len(closes) >= 260 else None


def _signals(market: str, universe: list) -> tuple[dict, str]:
    """sym -> {mom, ma_ok, px}. 반환 latest_date = 유니버스 최빈 최신일."""
    out, latest = {}, {}
    for sym in universe:
        d = _closes(market, sym)
        if not d:
            continue
        dates, px = d
        mom = px[-21] / px[-252] - 1.0 if px[-252] > 0 else None  # 12-1 근사(거래일)
        ma200 = sum(px[-200:]) / 200.0
        out[sym] = {"mom": mom, "ma_ok": px[-1] > ma200, "px": px[-1], "date": dates[-1]}
        latest[dates[-1]] = latest.get(dates[-1], 0) + 1
    ref_date = max(latest, key=latest.get) if latest else ""
    return {s: v for s, v in out.items() if v["date"] == ref_date}, ref_date


def _book_path(m):
    return SHADOW / f"tsmom_sleeve_book_{m}.json"


def _load_book(m):
    p = _book_path(m)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"inception": "", "last_reb_month": "", "arms": {}, "cum_cost_pct": {"tsmom": 0.0, "ew": 0.0}}


def _rebalance(market, sig, ref_date, book):
    mon = ref_date[:7]
    if book["last_reb_month"] == mon:
        return False
    ranked = sorted((s for s, v in sig.items() if v["mom"] is not None and v["ma_ok"]),
                    key=lambda s: sig[s]["mom"], reverse=True)
    tsmom_syms = ranked[:TOP_N]
    ew_syms = list(sig.keys())
    new_arms = {}
    for arm, syms in (("tsmom", tsmom_syms), ("ew", ew_syms)):
        cap = CAPITAL[market]
        w = cap / max(1, len(syms))
        old = book["arms"].get(arm, {})
        hold = {s: {"qty": w / sig[s]["px"], "px0": sig[s]["px"]} for s in syms if sig[s]["px"] > 0}
        # 회전 = 교체 비중(단순: 나간 종목 수/전체) → 비용 왕복 차감 기록
        gone = [s for s in old if s not in hold]
        turnover = len(gone) / max(1, len(old)) if old else 1.0  # 첫 리밸런스=전량 진입(편도)
        rt = COST_RT[market] + FX_RT[market]
        cost_pct = turnover * (rt if old else rt / 2.0)
        book["cum_cost_pct"][arm] = round(book["cum_cost_pct"].get(arm, 0.0) + cost_pct, 4)
        new_arms[arm] = hold
        _append(SHADOW / f"tsmom_sleeve_portfolio_{mon.replace('-', '')}_{market}.jsonl", {
            "event": "book_rebalanced", "date": ref_date, "market": market, "arm": arm,
            "method": "tsmom_sleeve_v1", "n": len(hold), "turnover": round(turnover, 3),
            "cost_pct_charged": round(cost_pct, 4),
            "symbols": syms[:TOP_N] if arm == "tsmom" else f"EW_all({len(syms)})",
        })
    book["arms"] = new_arms
    book["last_reb_month"] = mon
    if not book["inception"]:
        book["inception"] = ref_date
    return True


def _append(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _mtm(market, sig, ref_date, book):
    snap_f = SHADOW / f"tsmom_sleeve_snapshots_{market}.jsonl"
    if snap_f.exists():
        for line in snap_f.read_text(encoding="utf-8").splitlines()[-5:]:
            try:
                if json.loads(line).get("date") == ref_date:
                    return None  # 멱등
            except json.JSONDecodeError:
                pass
    vals = {}
    for arm, hold in book["arms"].items():
        v = 0.0
        for s, h in hold.items():
            px = sig.get(s, {}).get("px") or h["px0"]
            v += h["qty"] * px
        gross_pct = (v / CAPITAL[market] - 1.0) * 100.0
        vals[arm] = {"value": round(v, 0),
                     "gross_pct": round(gross_pct, 3),
                     "net_pct": round(gross_pct - book["cum_cost_pct"].get(arm, 0.0), 3)}
    bench = _closes(market, BENCH[market])
    bench_px = bench[1][-1] if bench else None
    snap = {"date": ref_date, "market": market, "arms": vals,
            "bench": BENCH[market], "bench_px": bench_px,
            "cum_cost_pct": book["cum_cost_pct"]}
    _append(snap_f, snap)
    return snap


def run(market: str) -> dict:
    uni = us_universe() if market == "US" else kr_universe()
    sig, ref_date = _signals(market, uni)
    if not sig or not ref_date:
        return {"market": market, "error": "no_price_data"}
    book = _load_book(market)
    rebalanced = _rebalance(market, sig, ref_date, book)
    _book_path(market).write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")
    snap = _mtm(market, sig, ref_date, book)
    return {"market": market, "date": ref_date, "universe": len(sig),
            "rebalanced": rebalanced, "snapshot": snap["arms"] if snap else "already_done"}


def main() -> None:
    for m in ("KR", "US"):
        r = run(m)
        print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
