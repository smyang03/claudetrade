#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""탐색 풀 생성기 — 가격 CSV만으로 US·KR 후보 풀을 넓게 만든다 (2026-09-07, 쉐도우 탐색 체계 개편 D1).

운영자 방향(09-07): 지금은 실매수가 아니므로 하루 1건 한도를 두지 말고 조건에 맞는 상황을 최대한 많이·다양하게 DB에 쌓는다.
이 모듈은 virtual_books의 탐색 arm(x_*)에 세션별 후보를 공급한다. 스크리너 DB(us_swing_shadow.db)나 kr_fallen 원장에 의존하지 않고
data/price/{us,kr} 일봉 전체(US 1,673·KR 1,744 종목)에서 특성값·풀 통과·순위·국면 태그를 계산한다.

규약
- US: 신호봉 i(전일) → 진입 세션 = 다음 봉 날짜(세션 키). virtual_books.entry_of(market="US")가 그 날짜 시가로 진입한다.
- KR: 신호일 = 세션 키, 진입은 다음 거래 세션 시가(entry_of(market="KR") 규약과 동일).
- 특성값은 전부 신호봉까지의 데이터(no-lookahead). 국면 태그(지수 20일·MA20·breadth)도 신호일 기준.
- 풀 문턱은 넓게 잡고 문턱 자체는 특성값으로 남긴다 → "어느 문턱부터 엣지가 살아나는가"는 사후 분해로 본다.

출력: discovery_sessions(market) → {pool_id: {session_key: [cand, ...]}}, pool_stats(market) → {session_key: {pool_id: n, "_universe": n}}
cand = {ticker, chg, gap, dvol, dvol20, vol_spike, ibs, rv20, mom20, from_high20, ma20_disc, cum5, ret60, max21, hi_break_n,
        down_streak, price, ranks{rule: rank}, regime{...}, pool}
사용: python tools/discovery_pools.py [--market US|KR] [--date YYYY-MM-DD]   # 풀 규모·샘플 출력(read-only)
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
US_DIR = ROOT / "data" / "price" / "us"
KR_DIR = ROOT / "data" / "price" / "kr"
KR_INDEX = ROOT / "state" / "kr_index_history_kospi.json"
DISCOVERY_START = "2025-06-02"   # KR 캐시 2025-04-22 시작 + 특성 창(20~60봉)
MIN_HISTORY = 25                 # 특성 계산 최소 봉 수

# 풀 정의 — (id, 시장, 설명). 문턱은 아래 predicate. 넓게 잡는다(실운영 문턱은 특성값 필터로 복원).
US_DVOL_MIN_M = 50.0     # 백만 달러
KR_DVOL_MIN_EOK = 20.0   # 억 원
POOLS_US = {
    "xus_fallen3":  "전일 ≤−3% (거래대금 ≥50M) — 급락 풀 확장(실운영 −5%·밴드·MAX는 특성 필터로 복원)",
    "xus_slow8":    "5일 누적 ≤−8% & 단일 −5% 없음 — 느린 급락 확장",
    "xus_rise5":    "전일 ≥+5% — 급등 다음날(지속/되돌림) 관측",
    "xus_breakout": "종가가 직전 120~250봉 최고 종가 돌파 — 신고가 추세",
    "xus_volspike": "거래량 20일 평균의 3배↑ & |전일| <3% — 조용한 거래량 급증",
}
POOLS_KR = {
    "xkr_fallen3":  "전일 ≤−3% (거래대금 ≥20억) — 급락 풀 확장(8조건은 플래그, R2/R4는 특성 필터로 복원)",
    "xkr_rise5":    "전일 ≥+5% — 급등 다음날 관측(상한가 복권·회피 판단 데이터)",
    "xkr_breakout": "종가가 직전 120봉 최고 종가 돌파 — 신고가 추세",
    "xkr_volspike": "거래량 20일 평균의 3배↑ & |전일| <3%",
}
RANK_RULES = ("dvol_desc", "dvol_asc", "chg_hi", "chg_lo", "ibs_hi", "max_lo", "disc_deep", "cum5_deep", "ret60_desc", "volspike_desc")

_CACHE: dict[str, tuple[dict, dict]] = {}


def _load_bars(path: Path) -> list[tuple]:
    rows = []
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.reader(fh):
                if len(r) >= 6 and r[0][:2] == "20":
                    try:
                        rows.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
                    except ValueError:
                        continue
    except OSError:
        return []
    return sorted(rows)


def featurize(b: list[tuple], i: int, market: str) -> dict | None:
    """신호봉 i의 특성값. 데이터 부족이면 None."""
    if i < MIN_HISTORY or i >= len(b):
        return None
    d, o, h, l, c, v = b[i]
    pc = b[i - 1][4]
    if pc <= 0 or c <= 0 or o <= 0:
        return None
    closes = [x[4] for x in b[max(0, i - 60): i + 1]]
    vols = [x[5] for x in b[i - 20: i]]
    rets = [100.0 * (b[j][4] / b[j - 1][4] - 1.0) for j in range(i - 19, i + 1) if b[j - 1][4] > 0]
    ma20 = st.mean(closes[-20:])
    dv = c * v / (1e6 if market == "US" else 1e8)
    dv20 = st.mean(x[4] * x[5] for x in b[i - 19: i + 1]) / (1e6 if market == "US" else 1e8)
    hi20 = max(x[2] for x in b[i - 19: i + 1])
    n_break = min(250, i)
    prior_max = max(x[4] for x in b[i - n_break: i]) if n_break >= 120 else None
    streak = 0
    for j in range(i, 0, -1):
        if b[j][4] < b[j - 1][4]:
            streak += 1
        else:
            break
    single5 = min(100.0 * (b[j][4] / b[j - 1][4] - 1.0) for j in range(max(1, i - 4), i + 1))
    f = {
        "ticker": None, "date": d, "price": c,
        "chg": 100.0 * (c / pc - 1.0), "gap": 100.0 * (o / pc - 1.0),
        "dvol": dv, "dvol20": dv20, "vol_spike": (v / st.mean(vols)) if vols and st.mean(vols) > 0 else None,
        "ibs": (c - l) / (h - l) * 100.0 if h > l else None,
        "rv20": st.pstdev(rets) if len(rets) >= 10 else None,
        "mom20": 100.0 * (c / closes[-21] - 1.0) if len(closes) >= 21 else None,
        "from_high20": 100.0 * (c / hi20 - 1.0),
        "ma20_disc": 100.0 * (c / ma20 - 1.0),
        "cum5": 100.0 * (c / b[i - 5][4] - 1.0) if i >= 5 and b[i - 5][4] > 0 else None,
        "min1_in5": single5,
        "ret60": 100.0 * (c / closes[0] - 1.0) if len(closes) >= 61 else None,
        "max21": max(100.0 * (b[j][4] / b[j - 1][4] - 1.0) for j in range(i - 20, i + 1)) if i >= 21 else None,
        "hi_break_n": n_break if (prior_max is not None and c >= prior_max) else 0,
        "down_streak": streak,
    }
    return f


def pool_pass(f: dict, market: str) -> list[str]:
    out = []
    if market == "US":
        if f["dvol"] is None or f["dvol"] < US_DVOL_MIN_M:
            return out
        if f["chg"] <= -3.0:
            out.append("xus_fallen3")
        if f["cum5"] is not None and f["cum5"] <= -8.0 and f["min1_in5"] > -5.0:
            out.append("xus_slow8")
        if f["chg"] >= 5.0:
            out.append("xus_rise5")
        if f["hi_break_n"] >= 120:
            out.append("xus_breakout")
        if f["vol_spike"] is not None and f["vol_spike"] >= 3.0 and abs(f["chg"]) < 3.0:
            out.append("xus_volspike")
    else:
        if f["dvol"] is None or f["dvol"] < KR_DVOL_MIN_EOK or f["price"] < 1000:
            return out
        if f["chg"] <= -3.0:
            out.append("xkr_fallen3")
        if f["chg"] >= 5.0:
            out.append("xkr_rise5")
        if f["hi_break_n"] >= 120:
            out.append("xkr_breakout")
        if f["vol_spike"] is not None and f["vol_spike"] >= 3.0 and abs(f["chg"]) < 3.0:
            out.append("xkr_volspike")
    return out


def _rank_key(rule: str, c: dict) -> float:
    g = lambda k, dflt: c[k] if c.get(k) is not None else dflt
    return {
        "dvol_desc": -g("dvol", 0.0), "dvol_asc": g("dvol", 1e18), "chg_hi": -g("chg", -1e9), "chg_lo": g("chg", 1e9),
        "ibs_hi": -g("ibs", -1.0), "max_lo": g("max21", 1e18), "disc_deep": g("ma20_disc", 1e9), "cum5_deep": g("cum5", 1e9),
        "ret60_desc": -g("ret60", -1e9), "volspike_desc": -g("vol_spike", -1.0),
    }[rule]


def _index_regime(market: str) -> dict[str, dict]:
    """날짜 → {idx_ret20, idx_above_ma20}. US=SPY CSV, KR=kr_index_history_kospi.json(종가)."""
    closes: list[tuple[str, float]] = []
    if market == "US":
        closes = [(x[0], x[4]) for x in _load_bars(US_DIR / "us_SPY.csv")]
    else:
        try:
            k = json.loads(KR_INDEX.read_text(encoding="utf-8"))
            rows = k if isinstance(k, list) else (k.get("rows") or k.get("history") or list(k.values())[0])
            closes = sorted((str(r["date"]), float(r["close"])) for r in rows if r.get("date") and r.get("close"))
        except Exception:
            closes = []
    out = {}
    for i in range(20, len(closes)):
        d, c = closes[i]
        ma = st.mean(x[1] for x in closes[i - 19: i + 1])
        out[d] = {"idx_ret20": round(100.0 * (c / closes[i - 20][1] - 1.0), 2), "idx_above_ma20": bool(c >= ma)}
    return out


def build(market: str, *, start: str | None = None) -> tuple[dict, dict]:
    """전 종목 스캔 → (sessions, stats). sessions[pool][session_key] = [cand...]."""
    market = market.upper()
    start = start or DISCOVERY_START
    d = US_DIR if market == "US" else KR_DIR
    prefix = "us_" if market == "US" else "kr_"
    pools = POOLS_US if market == "US" else POOLS_KR
    per_day: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    breadth: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # date → [n_total, n_down]
    for p in sorted(d.glob(f"{prefix}*.csv")):
        t = p.stem[len(prefix):]
        b = _load_bars(p)
        if len(b) < MIN_HISTORY + 1:
            continue
        for i in range(MIN_HISTORY, len(b)):
            sig_date = b[i][0]
            if sig_date < start:
                continue
            f = featurize(b, i, market)
            if f is None:
                continue
            f["ticker"] = t
            breadth[sig_date][0] += 1
            if f["chg"] < 0:
                breadth[sig_date][1] += 1
            hits = pool_pass(f, market)
            if not hits:
                continue
            key = b[i + 1][0] if market == "US" and i + 1 < len(b) else (sig_date if market == "KR" else None)
            if key is None:
                continue  # US: 다음 봉이 아직 없음(오늘 신호) — 다음 실행에서 잡힌다
            f["signal_date"] = sig_date
            for pid in hits:
                per_day[pid][key].append(dict(f))
    regime = _index_regime(market)
    for pid, days in per_day.items():
        for key, cands in days.items():
            sd = cands[0]["signal_date"]
            tot, dn = breadth.get(sd, [0, 0])
            reg = {**regime.get(sd, {}), "breadth_down_pct": round(100.0 * dn / tot, 1) if tot else None, "universe_n": tot}
            for rule in RANK_RULES:
                order = sorted(range(len(cands)), key=lambda j: _rank_key(rule, cands[j]))
                for r, j in enumerate(order, start=1):
                    cands[j].setdefault("ranks", {})[rule] = r
            for c in cands:
                c["regime"] = reg
                c["pool"] = pid
                c["pool_n"] = len(cands)
    stats = {}
    all_keys = set(k for days in per_day.values() for k in days)
    for key in all_keys:
        stats[key] = {pid: len(per_day[pid].get(key, [])) for pid in pools}
        sd = next((cands[0]["signal_date"] for pid in pools for k2, cands in per_day[pid].items() if k2 == key and cands), None)
        stats[key]["_universe"] = breadth.get(sd, [0, 0])[0] if sd else 0
    return {pid: dict(days) for pid, days in per_day.items()}, stats


def discovery_sessions(market: str) -> dict:
    market = market.upper()
    if market not in _CACHE:
        _CACHE[market] = build(market)
    return _CACHE[market][0]


def pool_stats(market: str) -> dict:
    market = market.upper()
    if market not in _CACHE:
        _CACHE[market] = build(market)
    return _CACHE[market][1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US")
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    sessions, stats = build(a.market)
    pools = POOLS_US if a.market.upper() == "US" else POOLS_KR
    keys = sorted(stats)
    print(f"[{a.market}] 세션 {len(keys)} ({keys[0] if keys else '-'} ~ {keys[-1] if keys else '-'})")
    for pid, desc in pools.items():
        ns = [stats[k].get(pid, 0) for k in keys]
        tot = sum(ns)
        print(f"  {pid:<14} 총 {tot:>6} · 일평균 {tot / max(1, len(keys)):5.1f} · 최대 {max(ns) if ns else 0:>4} — {desc}")
    k = a.date or (keys[-1] if keys else None)
    if k:
        print(f"\n[{k}] 유니버스 {stats[k].get('_universe')} · 풀별 {{{', '.join(f'{p}: {stats[k].get(p, 0)}' for p in pools)}}}")
        for pid in pools:
            for c in sessions.get(pid, {}).get(k, [])[:3]:
                print(f"   {pid} {c['ticker']} chg {c['chg']:+.1f} dvol {c['dvol']:.0f} max21 {c['max21']} ranks dvol#{c['ranks']['dvol_desc']} regime {c['regime']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
