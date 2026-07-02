"""
us_buyready_alpha_review — US action_routing BUY_READY 종목의 '진입했을' 알파를
read-only 사후분석으로 측정한다. 게이트(selection trade_ready 등)에 막혀 실제 진입
못 한 BUY_READY가 알파였는지를 다국면 누적 측정(재실행)하기 위한 도구.

배경(6/6~6/27 초기측정): US BUY_READY는 보유 forward_3d +1.97%·D+1단타 +1.41%,
placebo 4중 통과(KR은 깨짐). 단 6월 단일국면·표본소수 → 본 도구로 국면 누적해 enforce 판정.

측정(전부 read-only, 외부 API 없음):
- BUY_READY: logs/funnel/action_routing_shadow_<YYYYMMDD>_US.jsonl, routes[*].reason=='buy_ready'
- 보유 손익: data/ticker_selection_log.db forward_1d/3d, max_drawdown_3d (당일종가 기준)
- 단타 손익: data/price/minute/us/ 1분봉, D+1 시초 진입 → TP/SL 먼저닿은쪽 청산
- placebo: 같은 종목 '비신호일' D+1 (신호 순기여 = 신호일 − 비신호일)
- 가격분해: D+1 시초가×환율(usd_krw)로 예산(<50만 살수있음 / >100만 정당차단) 분리

CLI:
  python tools/us_buyready_alpha_review.py --start 20260606 --end 20260627 --tp 5 --sl 3
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_FUNNEL = _ROOT / "logs" / "funnel"
_MIN = _ROOT / "data" / "price" / "minute" / "us"
_SEL = _ROOT / "data" / "ml" / "decisions.db"  # 환율용
_SELDB = _ROOT / "data" / "ticker_selection_log.db"
_COST = 0.1  # US 왕복 수수료 근사(%) — FX 환전비용 별도

_bar_cache: dict[str, list] = {}


def _bars(ticker: str) -> list:
    if ticker in _bar_cache:
        return _bar_cache[ticker]
    path = _MIN / f"us_{ticker}.csv"
    out = []
    if path.exists():
        with open(path, encoding="utf-8-sig") as f:  # BOM
            for r in csv.DictReader(f):
                ts = (r.get("ts") or "").strip()
                if not ts:
                    continue
                try:
                    out.append((ts[:10], ts, float(r["open"]), float(r["high"]),
                                float(r["low"]), float(r["close"])))
                except (KeyError, ValueError, TypeError):
                    continue
    out.sort(key=lambda b: b[1])
    _bar_cache[ticker] = out
    return out


def _collect_buyready(start: str, end: str) -> set:
    out = set()
    for p in sorted(glob.glob(str(_FUNNEL / "action_routing_shadow_*_US.jsonl"))):
        d = os.path.basename(p).split("_")[3]
        if d < start or d > end:
            continue
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                routes = o.get("routes") or ([o["route"]] if isinstance(o.get("route"), dict) else [])
                for r in routes:
                    if r.get("reason") == "buy_ready" and r.get("ticker"):
                        out.add((f"{d[0:4]}-{d[4:6]}-{d[6:8]}", r["ticker"]))
    return out


def _d1(ticker: str, d0: str, tp: float, sl: float):
    """D+1 시초 진입 → 분봉 TP/SL. 반환 (net%, 진입가) 또는 (None,None)."""
    bs = _bars(ticker)
    days = sorted({b[0] for b in bs})
    if d0 not in days:
        return None, None
    later = [x for x in days if x > d0]
    if not later:
        return None, None
    d1b = [b for b in bs if b[0] == later[0]]
    if not d1b:
        return None, None
    P = d1b[0][2]
    if P <= 0:
        return None, None
    track = d1b[1:] + [b for b in bs if b[0] in later[1:3]]
    tp_px, sl_px = P * (1 + tp / 100), P * (1 - sl / 100)
    for (_d, _t, _o, h, l, _c) in track:
        if h >= tp_px and l <= sl_px:
            return -sl, P
        if h >= tp_px:
            return tp, P
        if l <= sl_px:
            return -sl, P
    return ((track[-1][5] - P) / P * 100 if track else 0.0), P


def _stat(v):
    v = [x for x in v if x is not None]
    if not v:
        return {"n": 0, "mean": None, "win": None}
    return {"n": len(v), "mean": round(statistics.mean(v), 3),
            "win": round(100 * sum(1 for x in v if x > 0) / len(v), 1)}


def _fx(start: str) -> float:
    try:
        con = sqlite3.connect(f"file:{_SEL}?mode=ro", uri=True, timeout=8)
        con.execute("PRAGMA busy_timeout=6000")
        d_iso = f"{start[0:4]}-{start[4:6]}-{start[6:8]}"
        row = con.execute("SELECT avg(usd_krw) FROM decisions WHERE usd_krw IS NOT NULL "
                          "AND session_date>=?", (d_iso,)).fetchone()
        con.close()
        if row and row[0]:
            return float(row[0])
    except sqlite3.Error:
        pass
    return 1450.0


def _labels(keys: set) -> dict:
    out = {}
    if not _SELDB.exists():
        return out
    con = sqlite3.connect(f"file:{_SELDB}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=8000")
    for (d_iso, tk) in keys:
        row = con.execute("SELECT forward_1d,forward_3d,max_drawdown_3d FROM ticker_selection_log "
                          "WHERE market='US' AND ticker=? AND date=? ORDER BY id LIMIT 1",
                          (tk, d_iso)).fetchone()
        if row:
            out[(d_iso, tk)] = row
    con.close()
    return out


def _p(label, s):
    if s["n"] == 0:
        return f"  {label:24s} n=0"
    return f"  {label:24s} mean={s['mean']:+6.2f}%  win={s['win']:5.1f}%  (n={s['n']})"


def run(start, end, tp, sl):
    br = _collect_buyready(start, end)
    lab = _labels(br)
    fx = _fx(start)
    print("=" * 72)
    print(f" us_buyready_alpha_review  기간={start}~{end}  TP{tp:g}/SL{sl:g}  환율={fx:.0f}")
    print(f" BUY_READY (date,ticker)={len(br)}  forward라벨매칭={len(lab)}")
    print("=" * 72)
    if not br:
        print(" BUY_READY 0건.")
        return

    print("[보유 손익 (당일종가 기준)]")
    print(_p("forward_1d", _stat([v[0] for v in lab.values()])))
    print(_p("forward_3d", _stat([v[1] for v in lab.values()])))
    print(_p("max_drawdown_3d", _stat([v[2] for v in lab.values()])))

    # 단타 + placebo + 가격분해
    sig, plac = [], []
    buckets = {"살수있음<50만": [], "50만~100만": [], "예산초과>100만": []}
    sig_keys = set(br)
    for (d, tk) in br:
        net, P = _d1(tk, d, tp, sl)
        if net is None:
            continue
        sig.append(net)
        krw = P * fx
        if krw < 500000:
            buckets["살수있음<50만"].append(net)
        elif krw < 1000000:
            buckets["50만~100만"].append(net)
        else:
            buckets["예산초과>100만"].append(net)
    for tk in {t for _, t in br}:
        days = sorted({b[0] for b in _bars(tk) if start[0:4]+"-"+start[4:6]+"-"+start[6:8] <= b[0]})
        for d in days:
            if (d, tk) in sig_keys:
                continue
            n, _ = _d1(tk, d, tp, sl)
            if n is not None:
                plac.append(n)

    ss, ps = _stat(sig), _stat(plac)
    print(f"\n[단타 D+1 시초 TP{tp:g}/SL{sl:g}]")
    print(_p("신호일(BUY_READY)", ss))
    if ss["n"]:
        print(f"  → 비용 {_COST}% 차감 net={ss['mean']-_COST:+.2f}% (FX 환전비용 별도)")
    print(_p("placebo(비신호일)", ps))
    if ss["mean"] is not None and ps["mean"] is not None:
        print(f"  → 신호 순기여 {ss['mean']-ps['mean']:+.2f}%p "
              f"{'유의' if ss['mean']-ps['mean'] > 0.5 else '미미'}")

    print("\n[가격분해 — 예산 정당차단 분리]")
    for k, v in buckets.items():
        print(_p(k, _stat(v)))
    print("\n 주의: 표본·국면 누적 전 enforce 금지. '살수있음<50만'이 과보수 대상,")
    print("       '예산초과'는 50만원 정책상 정당차단(net+여도 못 삼).")


def main():
    ap = argparse.ArgumentParser(description="US BUY_READY 알파 측정 (read-only, 다국면 누적)")
    ap.add_argument("--start", default="20260606")
    ap.add_argument("--end", default="20260627")
    ap.add_argument("--tp", type=float, default=5)
    ap.add_argument("--sl", type=float, default=3)
    a = ap.parse_args()
    run(a.start, a.end, a.tp, a.sl)


if __name__ == "__main__":
    main()
