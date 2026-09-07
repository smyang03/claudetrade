# -*- coding: utf-8 -*-
"""family C_EVENT_V1 + 패닉 마감 진입 검증 리포트 (2026-09-07) — 가상 북 DB의 신규 arm 행을 세션 클러스터 t·반기 OOS·K=1로 요약.

판정 잣대는 discovery_breakdown ★4조건과 같다(이중 클러스터 t≥2.5 · 인접 칸 · 반기 OOS · K=1 양수). 여기서는 arm 단위 요약만 낸다.
사용: python tools/event_family_report.py [--json out.json]
"""
from __future__ import annotations

import json
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "shadow" / "virtual_books.db"
PANIC = ROOT / "data" / "shadow" / "us_panic_close.jsonl"
ARMS = ["c_kr_fallen_buyback30", "c_kr_fallen_buyback_active", "c_kr_fallen_nomajor", "c_kr_insider_cluster", "c_kr_plan_buy",
        "c_kr_exright", "c_kr_buyback_start", "c_us_earn_gap", "c_us_insider_cluster", "c_us_volfirst", "c_kr_insider_k1", "c_us_insider_k1"]
H1 = ("2025-06-01", "2025-12-31"); H2 = ("2026-01-01", "2026-12-31")


def _t(vals: list[float]) -> float | None:
    if len(vals) < 2:
        return None
    sd = st.pstdev(vals)
    return round(st.mean(vals) / (sd / len(vals) ** 0.5), 2) if sd else None


def _summ(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    by = defaultdict(list)
    for r in rows:
        by[r["sd"]].append(r["net"])
    sm = [st.mean(v) for v in by.values()]
    top2 = sorted(sm, reverse=True)[2:] if len(sm) > 2 else sm
    return {"n": len(rows), "sessions": len(sm), "net_mean": round(st.mean(r["net"] for r in rows), 2),
            "win": round(sum(1 for r in rows if r["net"] > 0) / len(rows), 2),
            "session_mean": round(st.mean(sm), 2), "session_t": _t(sm),
            "session_mean_ex_top2": round(st.mean(top2), 2) if top2 else None,
            "tp": sum(1 for r in rows if r["reason"] == "TP"), "sl": sum(1 for r in rows if r["reason"] == "SL"),
            "forward_n": sum(1 for r in rows if not r["backfill"])}


def _k1_within(rows: list[dict]) -> list[dict]:
    """K=1 = arm 통과자 안에서 세션별 전일 거래대금 1위(풀 전체 순위가 아니라 필터 후 순위 — Codex P1)."""
    by = defaultdict(list)
    for r in rows:
        by[r["sd"]].append(r)
    out = []
    for v in by.values():
        v = [r for r in v if (r.get("feat") or {}).get("dvol") is not None]
        if v:
            out.append(max(v, key=lambda r: r["feat"]["dvol"]))
    return out


def load(con: sqlite3.Connection, sid: str) -> list[dict]:
    out = []
    for sd, tk, net, reason, bf, meta in con.execute(
            "SELECT session_date, ticker, net_pct, exit_reason, backfill, meta FROM trades WHERE strategy_id=? AND status='CLOSED'", (sid,)):
        try:
            m = json.loads(meta) if meta else {}
        except ValueError:
            m = {}
        out.append({"sd": sd, "tk": tk, "net": float(net), "reason": reason, "backfill": int(bf or 0),
                    "rank_dvol": (m.get("ranks") or {}).get("dvol_desc"), "feat": m.get("feat") or {}})
    return out


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); con.execute("PRAGMA busy_timeout=5000")
    res = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "arms": {}}
    open_n = {sid: con.execute("SELECT COUNT(*) FROM trades WHERE strategy_id=? AND status='OPEN'", (sid,)).fetchone()[0] for sid in ARMS}
    for sid in ARMS:
        rows = load(con, sid)
        d = {"all": _summ(rows), "open": open_n[sid],
             "H1": _summ([r for r in rows if H1[0] <= r["sd"] <= H1[1]]),
             "H2": _summ([r for r in rows if H2[0] <= r["sd"] <= H2[1]]),
             "k1_dvol": _summ(_k1_within(rows))}
        # ★4조건 근사: 세션 t≥2.5 · 반기 부호 일치(각 n≥10) · K=1 양수 (인접 칸은 arm 단위라 생략)
        a, h1, h2, k1 = d["all"], d["H1"], d["H2"], d["k1_dvol"]
        d["star"] = {"t_ge_2.5": bool(a.get("session_t") is not None and a["session_t"] >= 2.5),
                     "halves_same_sign": bool(h1.get("n", 0) >= 10 and h2.get("n", 0) >= 10 and (h1["session_mean"] > 0) == (h2["session_mean"] > 0) and a["session_mean"] > 0),
                     "k1_positive": bool(k1.get("n", 0) >= 10 and k1["session_mean"] > 0),
                     "halves_underpowered": not (h1.get("n", 0) >= 10 and h2.get("n", 0) >= 10)}
        d["grade"] = ("후보(3/3 근사 통과)" if all(v for k, v in d["star"].items() if k != "halves_underpowered")
                      else ("미달(표본)" if a.get("n", 0) < 30 else ("기각(음수)" if (a.get("session_mean") or 0) <= 0 else "후보(일부 통과)")))
        res["arms"][sid] = d
    # 패닉 마감 진입 vs 같은 세션 C6(다음 시가) 증분
    pc = [json.loads(l) for l in PANIC.read_text(encoding="utf-8").splitlines() if l.strip()] if PANIC.exists() else []
    pcs = [r for r in pc if r.get("kind") == "trade" and r.get("status") == "CLOSED" and r.get("instrument") == "stock"]
    c6 = load(con, "c_us_panic_all")
    by_pc = defaultdict(list); by_c6 = defaultdict(list)
    for r in pcs:
        by_pc[r["session_date"]].append(float(r["net_pct"]))
    for r in c6:
        f = r["feat"]
        # c6 세션 키는 진입일(신호일 다음 봉) — 신호일로 되돌려 패닉 세션과 맞춘다
        by_c6[str((r.get("feat") or {}).get("signal_date") or "")].append(r["net"])
    sig_by_c6 = defaultdict(list)
    for sd, tk, net, meta in con.execute("SELECT session_date, ticker, net_pct, meta FROM trades WHERE strategy_id='c_us_panic_all' AND status='CLOSED'"):
        try:
            m = json.loads(meta)
        except ValueError:
            continue
        sig_by_c6[m.get("signal_date")].append(float(net))
    # (신호일, 종목) 짝이 맞는 것만 비교 — 종목군이 달라 생기는 차이를 진입 시각 증분으로 오독하지 않는다(Codex P1)
    pc_pair = {(r["session_date"], str(r["ticker"]).upper()): float(r["net_pct"]) for r in pcs}
    c6_pair = {}
    for sd, tk, net, meta in con.execute("SELECT session_date, ticker, net_pct, meta FROM trades WHERE strategy_id='c_us_panic_all' AND status='CLOSED'"):
        try:
            m = json.loads(meta)
        except ValueError:
            continue
        c6_pair[(m.get("signal_date"), str(tk).upper())] = float(net)
    keys = sorted(set(pc_pair) & set(c6_pair))
    by_d = defaultdict(list)
    for k in keys:
        by_d[k[0]].append(pc_pair[k] - c6_pair[k])
    diffs = [st.mean(v) for v in by_d.values()]
    res["panic_close_vs_c6"] = {"paired_trades": len(keys), "paired_sessions": len(diffs),
                                "close_entry_mean": round(st.mean(pc_pair[k] for k in keys), 2) if keys else None,
                                "next_open_c6_mean": round(st.mean(c6_pair[k] for k in keys), 2) if keys else None,
                                "increment_session_mean": round(st.mean(diffs), 2) if diffs else None, "increment_session_t": _t(diffs),
                                "note": "같은 (신호일, 종목) 짝만. 계약 동일(TP20/SL25/D10), 진입 시각만 다름(15:45 vs 다음 시가). 짝 밖 종목(15:40 ≤−5%인데 종가 >−5% 등)은 제외"}
    con.close()
    args = sys.argv[1:]
    if "--json" in args:
        Path(args[args.index("--json") + 1]).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for sid, d in res["arms"].items():
        a = d["all"]
        print(f"{sid:28s} n={a.get('n',0):5d} sess={a.get('sessions',0):4d} open={d['open']:4d} net={a.get('net_mean',0):+6.2f}% "
              f"sess_mean={a.get('session_mean',0):+6.2f}% t={a.get('session_t')} ex_top2={a.get('session_mean_ex_top2')} "
              f"H1={d['H1'].get('session_mean')}({d['H1'].get('n',0)}) H2={d['H2'].get('session_mean')}({d['H2'].get('n',0)}) "
              f"K1={d['k1_dvol'].get('session_mean')}({d['k1_dvol'].get('n',0)}) TP/SL={a.get('tp',0)}/{a.get('sl',0)} → {d['grade']}")
    print("panic_close vs C6:", res["panic_close_vs_c6"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
