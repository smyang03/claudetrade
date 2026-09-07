#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""탐색 원장 조건 분해 — "어느 칸에 엣지가 사는가" (2026-09-07 D2, read-only).

탐색 arm(x_*)의 정산 행을 칸으로 잘라 n·평균 net·승률·클러스터 t(세션 단위)를 낸다. 판정이 아니라 후보 생성이다.
바늘의 정의(설계 §4): (a) 클러스터 t ≥ 2.5 (b) 인접 칸 같은 부호 (c) 앞뒤 반기 OOS 같은 부호 (d) 실운영 계약 재계산 양수 — 네 개 전부.
칸: 풀 × {전일 낙폭 계단, 거래대금 큰순 순위 계단, 거래대금 계단, MAX21 계단, 국면(지수 MA20 위/아래·breadth), 반기} + 출구 계약 격자 비교.
출력: data/analysis/discovery_breakdown.json + docs/reports/discovery_breakdown_YYYYMMDD.md
사용: python tools/discovery_breakdown.py [--min-n 30] [--pool xus_fallen3]
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
EARN_CAL = ROOT / "data" / "earnings_calendar.json"


def _sector_lookup() -> dict[str, dict[str, str]]:
    try:
        sm = json.loads(SECTOR_MAP.read_text(encoding="utf-8"))
        return {"US": {t: str(v.get("sector") or "") for t, v in (sm.get("US") or {}).items()},
                "KR": {t: str(v.get("sector") or "") for t, v in (sm.get("KR") or {}).items()}}
    except (OSError, ValueError):
        return {"US": {}, "KR": {}}


def _earn_lookup() -> tuple[dict[str, str], str, str]:
    try:
        d = json.loads(EARN_CAL.read_text(encoding="utf-8"))
        return ({str(t).upper(): str(v.get("date") or "") for t, v in (d.get("by_symbol") or {}).items()}, str(d.get("from") or ""), str(d.get("to") or ""))
    except (OSError, ValueError):
        return {}, "", ""
OUT_JSON = ROOT / "data" / "analysis" / "discovery_breakdown.json"

LADDERS = {
    "chg": [(-99, -10, "≤−10%"), (-10, -7, "−10~−7"), (-7, -5, "−7~−5"), (-5, -4, "−5~−4"), (-4, -3, "−4~−3"), (-3, 0, "−3~0"), (0, 3, "0~+3"), (3, 5, "+3~+5"), (5, 8, "+5~+8"), (8, 15, "+8~+15"), (15, 99, "≥+15")],
    "rank_dvol": [(1, 1, "1위"), (2, 3, "2~3위"), (4, 7, "4~7위"), (8, 15, "8~15위"), (16, 9999, "16위+")],
    "dvol_us": [(50, 100, "50~100M"), (100, 500, "100~500M(밴드)"), (500, 2000, "500M~2B"), (2000, 1e9, "≥2B")],
    "dvol_kr": [(20, 50, "20~50억"), (50, 100, "50~100억"), (100, 300, "100~300억"), (300, 1e9, "≥300억")],
    "max21": [(-1, 4, "<4"), (4, 8, "4~8"), (8, 15, "8~15(≥8 실운영)"), (15, 999, "≥15")],
    "from_high20": [(-99, -30, "≤−30%"), (-30, -20, "−30~−20"), (-20, -10, "−20~−10"), (-10, 0, "−10~0"), (0, 99, "≥0")],
}


def cluster_t(nets: list[float], sessions: list[str]) -> float | None:
    """세션 클러스터 t: 세션별 평균의 평균 / (세션 평균의 표준편차/√세션수). 종목 중복은 세션 안에서 상쇄."""
    by: dict[str, list[float]] = defaultdict(list)
    for n, s in zip(nets, sessions):
        by[s].append(n)
    means = [st.mean(v) for v in by.values()]
    if len(means) < 5:
        return None
    sd = st.stdev(means)   # 표본 표준편차(소표본 t 과대 방지)
    return round(st.mean(means) / (sd / math.sqrt(len(means))), 2) if sd > 0 else None


def cell(rows: list[tuple]) -> dict:
    nets = [r[0] for r in rows]; sess = [r[1] for r in rows]
    if not nets:
        return {"n": 0}
    top2 = sorted(nets, reverse=True)[:2]
    return {"n": len(nets), "sessions": len(set(sess)), "mean": round(st.mean(nets), 3), "median": round(st.median(nets), 3),
            "win_pct": round(100.0 * sum(1 for x in nets if x > 0) / len(nets), 1), "t": cluster_t(nets, sess),
            "mean_ex_top2": round((sum(nets) - sum(top2)) / max(1, len(nets) - len(top2)), 3) if len(nets) > 2 else None}


def load(pool: str | None) -> list[dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    q = "SELECT strategy_id, session_date, ticker, net_pct, exit_reason, backfill, meta FROM trades WHERE status='CLOSED' AND strategy_id LIKE 'x%'"
    args: tuple = ()
    if pool:
        q += " AND strategy_id=?"; args = (pool,)
    # 성숙 코호트: 같은 (풀, 세션)에 OPEN이 남아 있으면 그 세션은 미성숙 — 빨리 정산된 TP만 먼저 보이는 선택편향 방지(Codex D1)
    immature = {(r[0], r[1]) for r in con.execute("SELECT DISTINCT strategy_id, session_date FROM trades WHERE status='OPEN' AND strategy_id LIKE 'x%'")}
    sectors = _sector_lookup(); earn, e_from, e_to = _earn_lookup()
    out = []
    for sid, sd, tk, net, reason, bf, meta in con.execute(q, args):
        try:
            m = json.loads(meta) if meta else {}
        except ValueError:
            m = {}
        f = m.get("feat") or {}
        mk = "KR" if sid.startswith("xkr") else "US"
        sig = m.get("signal_date") or sd
        earn_near = None
        if mk == "US" and e_from and e_to and e_from <= sig <= e_to:
            ed = earn.get(str(tk).upper(), "")
            try:
                earn_near = bool(ed) and abs((datetime.strptime(ed, "%Y-%m-%d") - datetime.strptime(sig, "%Y-%m-%d")).days) <= 2
            except ValueError:
                earn_near = None
        out.append({"pool": sid, "session": sd, "ticker": tk, "net": float(net), "reason": reason, "backfill": int(bf or 0),
                    "mature": (sid, sd) not in immature, "sector": sectors[mk].get(str(tk)) or "(미분류)", "earn_near": earn_near,
                    "rank_dvol_raw": (m.get("ranks") or {}).get("dvol_desc"),
                    "chg": f.get("chg"), "dvol": f.get("dvol"), "max21": f.get("max21"), "from_high20": f.get("from_high20"),
                    "rank_dvol": (m.get("ranks") or {}).get("dvol_desc"), "regime": m.get("regime") or {}, "grid": m.get("grid") or {},
                    "half": ("H1" if sd < "2026-01-01" else "H2")})
    con.close()
    return out


def ladder_cells(rows: list[dict], key: str, ladder) -> list[dict]:
    out = []
    for lo, hi, label in ladder:
        sel = [(r["net"], r["session"]) for r in rows if r.get(key) is not None and lo <= r[key] < hi] if key != "rank_dvol" else \
              [(r["net"], r["session"]) for r in rows if r.get(key) is not None and lo <= r[key] <= hi]
        out.append({"label": label, **cell(sel)})
    return out


def cat_cells(rows: list[dict], key: str, min_n: int = 30) -> list[dict]:
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key))].append((r["net"], r["session"]))
    out = [{"label": k, **cell(v)} for k, v in groups.items() if len(v) >= min_n]
    return sorted(out, key=lambda c: -c["n"])


def _sel_ladder(rows: list[dict], key: str, lo, hi) -> list[dict]:
    if key == "rank_dvol":
        return [r for r in rows if r.get(key) is not None and lo <= r[key] <= hi]
    return [r for r in rows if r.get(key) is not None and lo <= r[key] < hi]


def candidate_checks(sel: list[dict]) -> dict:
    """후보 칸의 자동 검사: 반기 OOS 같은 부호(둘 다 n≥10), K=1(거래대금 큰순 1위) 재계산 — 실운영 계약(하루 1건)의 근사."""
    h1 = cell([(r["net"], r["session"]) for r in sel if r["half"] == "H1"]); h2 = cell([(r["net"], r["session"]) for r in sel if r["half"] == "H2"])
    k1 = cell([(r["net"], r["session"]) for r in sel if r.get("rank_dvol_raw") == 1])
    same = (h1.get("n", 0) >= 10 and h2.get("n", 0) >= 10 and (h1["mean"] > 0) == (h2["mean"] > 0))
    return {"h1": h1, "h2": h2, "oos_halves_same_sign": bool(same) if (h1.get("n", 0) >= 10 and h2.get("n", 0) >= 10) else None, "k1": k1}


def analyze(rows: list[dict], min_n: int) -> dict:
    res = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "min_n": min_n, "pools": {}}
    for pool in sorted({r["pool"] for r in rows}):
        pr = [r for r in rows if r["pool"] == pool]
        market = "KR" if pool.startswith("xkr") else "US"
        pr = [r for r in pr if r["mature"]]   # 미성숙 세션 제외(전 칸 공통)
        d = {"all": cell([(r["net"], r["session"]) for r in pr]),
             "forward": cell([(r["net"], r["session"]) for r in pr if not r["backfill"]]),
             "note": "성숙 세션(OPEN 잔여 없음)만. forward=세션 09-01 이후이나 탐색 arm 도입은 09-07(사전등록 시점과 다름) · H1/H2=2026-01-01 전후 달력 분할",
             "by_half": {h: cell([(r["net"], r["session"]) for r in pr if r["half"] == h]) for h in ("H1", "H2")},
             "ladders": {"chg": ladder_cells(pr, "chg", LADDERS["chg"]), "rank_dvol": ladder_cells(pr, "rank_dvol", LADDERS["rank_dvol"]),
                         "dvol": ladder_cells(pr, "dvol", LADDERS["dvol_kr" if market == "KR" else "dvol_us"]),
                         "max21": ladder_cells(pr, "max21", LADDERS["max21"]), "from_high20": ladder_cells(pr, "from_high20", LADDERS["from_high20"])},
             "regime": {"idx_above_ma20": cell([(r["net"], r["session"]) for r in pr if r["regime"].get("idx_above_ma20") is True]),
                        "idx_below_ma20": cell([(r["net"], r["session"]) for r in pr if r["regime"].get("idx_above_ma20") is False]),
                        "breadth_down_ge60": cell([(r["net"], r["session"]) for r in pr if (r["regime"].get("breadth_down_pct") or 0) >= 60]),
                        "breadth_down_lt40": cell([(r["net"], r["session"]) for r in pr if r["regime"].get("breadth_down_pct") is not None and r["regime"]["breadth_down_pct"] < 40])},
             "exit_reason": {k: cell([(r["net"], r["session"]) for r in pr if r["reason"] == k]) for k in ("TP", "SL", "BE", "D_MAT")},
             "sector": cat_cells(pr, "sector", min_n),
             "earnings": {"near": cell([(r["net"], r["session"]) for r in pr if r.get("earn_near") is True]),
                          "not_near": cell([(r["net"], r["session"]) for r in pr if r.get("earn_near") is False])}}
        # 출구 계약 격자 비교(같은 진입)
        grid: dict[str, list] = defaultdict(list)
        for r in pr:
            for name, (net, _reason) in (r["grid"] or {}).items():
                grid[name].append((net, r["session"]))
        d["grid"] = {name: cell(v) for name, v in grid.items()}
        # 실운영 레퍼런스 복원(US 급락 풀만 의미): 전일 ≤−5 & 밴드 100~500M & MAX21 ≥8 & 순위 1
        if pool == "xus_fallen3":
            ref = [(r["net"], r["session"]) for r in pr if r["chg"] is not None and r["chg"] <= -5 and r["dvol"] is not None and 100 <= r["dvol"] < 500
                   and r["max21"] is not None and r["max21"] >= 8]
            d["live_reference_filter"] = {"all_passers": cell(ref)}
        # 바늘 후보: 사다리 칸 중 n≥min_n & t≥2.5 & 인접 칸 같은 부호 & 두 반기 같은 부호
        cands = []
        ladder_defs = {"chg": LADDERS["chg"], "rank_dvol": LADDERS["rank_dvol"], "dvol": LADDERS["dvol_kr" if market == "KR" else "dvol_us"],
                       "max21": LADDERS["max21"], "from_high20": LADDERS["from_high20"]}
        for lname, cells in d["ladders"].items():
            for i, c in enumerate(cells):
                if c.get("n", 0) < min_n or c.get("t") is None or abs(c["t"]) < 2.5:
                    continue
                sign = 1 if c["mean"] > 0 else -1
                neigh = [cells[j] for j in (i - 1, i + 1) if 0 <= j < len(cells) and cells[j].get("n", 0) >= 10]
                if not neigh or any((x["mean"] > 0) != (sign > 0) for x in neigh):
                    continue   # 유효 이웃이 없으면 통과시키지 않는다(문턱 안정성 미확인)
                lo, hi, _label = ladder_defs[lname][i]
                chk = candidate_checks(_sel_ladder(pr, lname, lo, hi))
                k1 = chk["k1"]
                cands.append({"ladder": lname, "cell": c["label"], "n": c["n"], "mean": c["mean"], "t": c["t"],
                              "kind": "buy" if sign > 0 else "avoid",
                              "oos_halves_same_sign": chk["oos_halves_same_sign"], "h1": chk["h1"], "h2": chk["h2"], "k1": k1,
                              "passes_4": bool(sign > 0 and chk["oos_halves_same_sign"] and k1.get("n", 0) >= 10 and k1["mean"] > 0)})
        d["needle_candidates_buy"] = [x for x in cands if x["kind"] == "buy"]
        d["needle_candidates_avoid"] = [x for x in cands if x["kind"] == "avoid"]
        d["needle_candidates"] = cands   # 호환
        res["pools"][pool] = d
    return res


def fmt(c: dict) -> str:
    if not c or not c.get("n"):
        return "n=0"
    return f"n={c['n']} ({c.get('sessions', '-')}세션) {c['mean']:+.2f}% 중앙 {c['median']:+.2f} 승 {c['win_pct']}% t {c['t'] if c['t'] is not None else '-'} 상위2제외 {c['mean_ex_top2'] if c['mean_ex_top2'] is not None else '-'}"


def to_md(res: dict) -> str:
    L = [f"# 탐색 원장 조건 분해 ({res['generated_at'][:10]})", "", "후보 생성용(판정 아님). 클러스터 t는 세션 단위. 비용 반영 net(US 0.50/KR 0.25). 바늘 후보 = n≥min_n & |t|≥2.5 & 인접 칸 같은 부호(반기 OOS·실운영 재계산은 별도 확인).", ""]
    for pool, d in res["pools"].items():
        L += [f"## {pool}", f"- 전체: {fmt(d['all'])}", f"- forward(09-01~): {fmt(d['forward'])}",
              f"- 반기: H1 {fmt(d['by_half']['H1'])} / H2 {fmt(d['by_half']['H2'])}",
              f"- 국면: 지수 MA20 위 {fmt(d['regime']['idx_above_ma20'])} / 아래 {fmt(d['regime']['idx_below_ma20'])} / 하락폭 ≥60% {fmt(d['regime']['breadth_down_ge60'])} / <40% {fmt(d['regime']['breadth_down_lt40'])}",
              "- 출구: " + " · ".join(f"{k} {fmt(v)}" for k, v in d["exit_reason"].items() if v.get("n"))]
        if d.get("live_reference_filter"):
            L.append(f"- 실운영 필터 복원(−5%·밴드·MAX≥8, 전량): {fmt(d['live_reference_filter']['all_passers'])}")
        for lname, cells in d["ladders"].items():
            L += ["", f"| {lname} | n | 세션 | 평균 | 중앙 | 승률 | t | 상위2제외 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
            for c in cells:
                if c.get("n"):
                    L.append(f"| {c['label']} | {c['n']} | {c['sessions']} | {c['mean']:+.2f} | {c['median']:+.2f} | {c['win_pct']} | {c['t'] if c['t'] is not None else '-'} | {c['mean_ex_top2'] if c['mean_ex_top2'] is not None else '-'} |")
        if d.get("grid"):
            L += ["", "| 출구 계약(같은 진입) | n | 평균 | 승률 | t |", "|---|---:|---:|---:|---:|"]
            for name, c in d["grid"].items():
                L.append(f"| {name} | {c['n']} | {c['mean']:+.2f} | {c['win_pct']} | {c['t'] if c['t'] is not None else '-'} |")
        def _cand(x):
            return (f"{x['ladder']}={x['cell']} (n={x['n']}, {x['mean']:+.2f}%, t={x['t']}, 반기 OOS {'같은 부호' if x['oos_halves_same_sign'] else ('다름' if x['oos_halves_same_sign'] is False else '표본부족')}, "
                    f"K=1 n={x['k1'].get('n', 0)} {x['k1'].get('mean', 0) if x['k1'].get('n') else 0:+.2f}%{' ★4조건 통과' if x.get('passes_4') else ''})")
        L += ["", "매수 후보 칸: " + (", ".join(_cand(x) for x in d["needle_candidates_buy"]) or "없음"),
              "회피 후보 칸: " + (", ".join(_cand(x) for x in d["needle_candidates_avoid"]) or "없음"), ""]
        if d.get("sector"):
            L += ["| 섹터 | n | 평균 | 승률 | t |", "|---|---:|---:|---:|---:|"] + [f"| {c['label']} | {c['n']} | {c['mean']:+.2f} | {c['win_pct']} | {c['t'] if c['t'] is not None else '-'} |" for c in d["sector"][:12]] + [""]
        if d["earnings"]["near"].get("n") or d["earnings"]["not_near"].get("n"):
            L += [f"- 어닝 ±2일(US, 캘린더 창 안): 근접 {fmt(d['earnings']['near'])} / 비근접 {fmt(d['earnings']['not_near'])}", ""]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--pool", default=None)
    a = ap.parse_args()
    rows = load(a.pool)
    res = analyze(rows, a.min_n)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    md = to_md(res)
    rep = ROOT / "docs" / "reports" / f"discovery_breakdown_{res['generated_at'][:10].replace('-', '')}.md"
    rep.write_text(md, encoding="utf-8")
    print(md)
    print(f"saved {OUT_JSON} / {rep}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
