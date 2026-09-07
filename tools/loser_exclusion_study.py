#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""손실 종목 배제 필터 — 풀 안에서 마이너스로 가는 종목을 진입 시점 정보로 걸러낼 수 있는가 (2026-09-07, 운영자 지시 "수익 나는 시스템").

규율: 전반기(2025-06~12)로만 규칙을 만들고 후반기(2026-01~09)에서 검증. 같은 기간 안에서 만들고 재면 우연을 규칙으로 착각한다.
규칙 후보: 수치 특성(낙폭·거래대금·MAX21·IBS·20일 고점 대비·rv20·MA20 괴리·5일 누적·vol_spike·풀 내 순위)의 10분위 구간,
섹터, 국면(breadth). 전반기에서 "구간 평균 < 전체 평균 − 마진 & 세션 t ≤ −2 & n ≥ 40"인 구간만 배제 규칙으로 채택(최대 4개, 배제율 ≤ 35%).
판정(후반기): ① 남는 집합 평균이 전체보다 오르는가 ② 뺀 집합 평균이 유의하게 낮은가(세션 t) ③ 남는 건수가 하루 몇 개인가.
출력: data/analysis/loser_exclusion_<pool>.json + 콘솔.
사용: python tools/loser_exclusion_study.py [--pool c_kr_fallen_regime] [--max-rules 4]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "shadow" / "virtual_books.db"
SECTOR_MAP = ROOT / "data" / "sector_map.json"
OUT_DIR = ROOT / "data" / "analysis"
SPLIT = "2026-01-01"
NUMERIC = ("chg", "dvol", "max21", "ibs", "from_high20", "rv20", "ma20_disc", "cum5", "vol_spike", "gap", "mom20", "down_streak", "rank_dvol", "breadth_down",
           # 개장 스냅샷(kr_open_flow, 09-08부터 쌓임): 매물 소진 후보 특성 — 값이 없는 행은 해당 규칙에서 제외된다
           "flow_gap", "flow_strength_0905", "flow_strength_0930", "flow_imb_0905", "flow_imb_0930", "flow_ret_open_0930", "flow_pos_0930")


def cell(rows):
    nets = [r["net"] for r in rows]
    if not nets:
        return {"n": 0}
    by = defaultdict(list)
    for r in rows:
        by[r["s"]].append(r["net"])
    means = [st.mean(v) for v in by.values()]
    t = round(st.mean(means) / (st.stdev(means) / math.sqrt(len(means))), 2) if len(means) >= 5 and st.stdev(means) > 0 else None
    return {"n": len(nets), "sessions": len(by), "mean": round(st.mean(nets), 3), "median": round(st.median(nets), 3),
            "win_pct": round(100.0 * sum(1 for x in nets if x > 0) / len(nets), 1), "t": t,
            "per_session": round(len(nets) / len(by), 1)}


def load(pool: str, extra_filter=None) -> list[dict]:
    sm = json.loads(SECTOR_MAP.read_text(encoding="utf-8")) if SECTOR_MAP.exists() else {}
    mk = "KR" if ("kr" in pool.split("_")[0] or pool.startswith("c_kr")) else "US"
    sectors = {t: str(v.get("sector") or "") for t, v in (sm.get(mk) or {}).items()}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    immature = {(r[0], r[1]) for r in con.execute("SELECT DISTINCT strategy_id, session_date FROM trades WHERE status='OPEN'")}
    out = []
    for sd, tk, net, reason, meta in con.execute("SELECT session_date, ticker, net_pct, exit_reason, meta FROM trades WHERE strategy_id=? AND status='CLOSED'", (pool,)):
        if (pool, sd) in immature:
            continue
        m = json.loads(meta) if meta else {}
        f = m.get("feat") or {}
        fl = m.get("flow") or {}
        s05, s30 = fl.get("09:05") or {}, fl.get("09:30") or {}
        r = {"s": sd, "t": tk, "net": float(net), "reason": reason, "sector": sectors.get(str(tk)) or "(미분류)",
             "rank_dvol": (m.get("ranks") or {}).get("dvol_desc"), "breadth_down": (m.get("regime") or {}).get("breadth_down_pct"),
             "half": "H1" if sd < SPLIT else "H2",
             "flow_gap": s05.get("gap_pct"), "flow_strength_0905": s05.get("strength"), "flow_strength_0930": s30.get("strength"),
             "flow_imb_0905": s05.get("imbalance"), "flow_imb_0930": s30.get("imbalance"), "flow_ret_open_0930": s30.get("ret_from_open_pct"),
             "flow_pos_0930": s30.get("pos_in_range")}
        for k in NUMERIC:
            if k not in r:
                r[k] = f.get(k)
        if extra_filter and not extra_filter(r):
            continue
        out.append(r)
    con.close()
    return out


def deciles(vals: list[float], k: int = 10) -> list[float]:
    s = sorted(vals)
    return [s[int(len(s) * i / k)] for i in range(1, k)]


def learn_rules(h1: list[dict], *, min_n: int = 40, margin: float = 1.0, max_rules: int = 4, max_excl: float = 0.35) -> list[dict]:
    base = st.mean(r["net"] for r in h1)
    cands = []
    for k in NUMERIC:
        vals = [r[k] for r in h1 if r.get(k) is not None]
        if len(vals) < min_n * 3:
            continue
        cuts = deciles(vals)
        edges = [-1e18] + cuts + [1e18]
        for lo, hi in zip(edges[:-1], edges[1:]):
            sel = [r for r in h1 if r.get(k) is not None and lo <= r[k] < hi]
            c = cell(sel)
            if c["n"] >= min_n and c["t"] is not None and c["t"] <= -2.0 and c["mean"] <= base - margin:
                cands.append({"kind": "num", "key": k, "lo": lo, "hi": hi, "h1": c, "lift": base - c["mean"]})
    for sec in {r["sector"] for r in h1}:
        sel = [r for r in h1 if r["sector"] == sec]
        c = cell(sel)
        if c["n"] >= min_n and c["t"] is not None and c["t"] <= -2.0 and c["mean"] <= base - margin:
            cands.append({"kind": "cat", "key": "sector", "value": sec, "h1": c, "lift": base - c["mean"]})
    cands.sort(key=lambda x: -x["lift"] * x["h1"]["n"])
    rules, excluded = [], set()
    for c in cands:
        hit = {id(r) for r in h1 if matches(r, c)}
        new = hit - excluded
        if len(new) < min_n // 2:
            continue
        if (len(excluded | hit)) / len(h1) > max_excl:
            continue
        rules.append(c); excluded |= hit
        if len(rules) >= max_rules:
            break
    return rules


def matches(r: dict, rule: dict) -> bool:
    if rule["kind"] == "cat":
        return r.get(rule["key"]) == rule["value"]
    v = r.get(rule["key"])
    return v is not None and rule["lo"] <= v < rule["hi"]


def evaluate(rows: list[dict], rules: list[dict]) -> dict:
    drop = [r for r in rows if any(matches(r, ru) for ru in rules)]
    keep = [r for r in rows if not any(matches(r, ru) for ru in rules)]
    return {"all": cell(rows), "keep": cell(keep), "drop": cell(drop), "excl_pct": round(100.0 * len(drop) / max(1, len(rows)), 1),
            "per_rule": [{"rule": describe(ru), "drop": cell([r for r in rows if matches(r, ru)])} for ru in rules]}


def describe(rule: dict) -> str:
    if rule["kind"] == "cat":
        return f"{rule['key']}=={rule['value']}"
    lo = "-∞" if rule["lo"] < -1e17 else f"{rule['lo']:.2f}"; hi = "∞" if rule["hi"] > 1e17 else f"{rule['hi']:.2f}"
    return f"{rule['key']}∈[{lo},{hi})"


def run(pool: str, extra=None, *, max_rules: int = 4, label: str | None = None) -> dict:
    rows = load(pool, extra)
    h1 = [r for r in rows if r["half"] == "H1"]; h2 = [r for r in rows if r["half"] == "H2"]
    res = {"pool": label or pool, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "split": SPLIT,
           "n_h1": len(h1), "n_h2": len(h2)}
    if len(h1) < 200 or len(h2) < 200:
        res["skipped"] = f"표본 부족 H1={len(h1)} H2={len(h2)}"
        return res
    rules = learn_rules(h1, max_rules=max_rules)
    res["rules"] = [dict(describe=describe(ru), h1=ru["h1"]) for ru in rules]
    res["train_h1"] = evaluate(h1, rules)
    res["test_h2"] = evaluate(h2, rules)
    return res


def fmt(c):
    return f"n={c.get('n', 0)} 평균 {c.get('mean', 0):+.2f}% 승 {c.get('win_pct', 0)}% t {c.get('t')} (일 {c.get('per_session', 0)}건)" if c.get("n") else "n=0"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=None)
    ap.add_argument("--max-rules", type=int, default=4)
    a = ap.parse_args()
    targets = [(a.pool, None, None)] if a.pool else [
        ("c_kr_fallen_regime", None, None), ("c_us_fallen_regime", None, None),
        ("xkr_fallen3", lambda r: r["chg"] is not None and r["chg"] <= -5, "xkr_fallen3(≤−5%, 국면 무관)"),
        ("xus_fallen3", lambda r: r["chg"] is not None and r["chg"] <= -5, "xus_fallen3(≤−5%, 국면 무관)"),
        ("c_us_slow8", None, None)]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for pool, extra, label in targets:
        res = run(pool, extra, max_rules=a.max_rules, label=label)
        (OUT_DIR / f"loser_exclusion_{pool}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n### {res['pool']}  H1 {res['n_h1']} / H2 {res['n_h2']}")
        if res.get("skipped"):
            print("  ", res["skipped"]); continue
        for ru in res["rules"]:
            print(f"  규칙(H1 학습) {ru['describe']:40s} H1 배제집합 {fmt(ru['h1'])}")
        for tag in ("train_h1", "test_h2"):
            e = res[tag]
            print(f"  [{tag}] 전체 {fmt(e['all'])}")
            print(f"           남김 {fmt(e['keep'])}  | 배제 {fmt(e['drop'])} (배제율 {e['excl_pct']}%)")
            if tag == "test_h2":
                for pr in e["per_rule"]:
                    print(f"           └ {pr['rule']:40s} H2 배제 {fmt(pr['drop'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
