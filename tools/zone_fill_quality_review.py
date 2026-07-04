#!/usr/bin/env python3
"""체결가 품질(zone-fill) 게이트 검증 — top-of-zone 추격체결 회피의 net 값어치 (2026-07-04).

배경(12인 토론 판정 debate_loss_min_profit_max_20260704): 진입시점 승자선별은 죽음(confidence
역상관·reward_risk 역전·OOS 부호역전)이나, **체결가 품질**은 살아있는 손실축소 레버로 검증 통과.
buy_zone 상단(top)에서 체결된 진입 + 목표부풀림(reward_pct↑)이 겹친 "최악셀"이 US 손실에 집중,
양 월(5·6월) 모두 음수(부호역전 없음). 기계적 인과(높은 진입가=목표헤드룸↓), selection 아니라
execution. red-tape와 같은 방어족. 이 도구는 그 레버를 소급 측정(저장필드만, API/매매 무접촉).

zone_pos = (hit_price − buy_zone_low) / (buy_zone_high − buy_zone_low)  # 0=존저점, 1=존고점, 진입시점 확정
정직: (a)양날 — 체결가를 조이면 미체결(zone 안 내려오면 진입 못함)이 늘어 승자도 놓친다. 이 도구는
CLOSED만 봐서 **미체결 미스를 측정 못한다**(red-tape forward처럼 shadow 누적으로만 확증). (b)생존편향
있으나 band간 비교엔 양 arm 동일작용해 상쇄. (c)레버=손실축소지 흑자전환 아님(회피후도 비용 아래).
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "v2_event_store.db"
COST = {"US": 0.70, "KR": 0.23}  # 한투 일반 왕복 실측(kis-fees 메모리)


def _f(d: dict, k: str):
    try:
        return float(d[k]) if d.get(k) is not None else None
    except (TypeError, ValueError, KeyError):
        return None


def net_of(mkt: str, d: dict):
    fx = _f(d, "pnl_pct_net_after_fx_est")
    if fx is not None:
        return fx
    g = _f(d, "pnl_pct")
    return None if g is None else g - COST.get(mkt, 0.5)


def load(since: str):
    con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=8000")
    rows = con.execute(
        "SELECT decision_id, market, session_date, plan_json, updated_at FROM v2_path_runs "
        "WHERE status='CLOSED' AND runtime_mode='live' AND session_date>=?", (since,)).fetchall()
    con.close()
    best = {}
    for did, mk, sd, pj, ua in rows:
        key = did or f"_{mk}_{sd}_{(pj or '')[:30]}"
        if key not in best or (ua or "") > best[key][0]:
            best[key] = (ua or "", mk, sd, json.loads(pj or "{}"))
    T = []
    for _, mk, sd, d in best.values():
        net = net_of(mk, d)
        bzh, bzl, hit = _f(d, "buy_zone_high"), _f(d, "buy_zone_low"), _f(d, "hit_price")
        if net is None or None in (bzh, bzl, hit) or bzh <= bzl:
            continue
        T.append({"mkt": mk, "mth": sd[:7], "net": net,
                  "zpos": (hit - bzl) / (bzh - bzl), "rp": _f(d, "reward_pct")})
    return T


def s(v):
    if not v:
        return "n=0"
    return f"n={len(v):3d} 합{sum(v):+6.1f} per{st.mean(v):+.3f} 중앙{st.median(v):+.3f} 승{100*sum(1 for x in v if x>0)//len(v):3d}%"


def main() -> int:
    ap = argparse.ArgumentParser(description="체결가 품질(zone-fill) 게이트 검증")
    ap.add_argument("--since", default="2026-05-01")
    ap.add_argument("--top", type=float, default=0.67, help="top-of-zone 경계(zone_pos)")
    ap.add_argument("--rp", type=float, default=5.0, help="목표부풀림 경계(reward_pct)")
    args = ap.parse_args()
    T = load(args.since)

    def band(r):
        return "bottom" if r["zpos"] <= 0.33 else ("top" if r["zpos"] >= args.top else "mid")

    def worst(r):
        return r["zpos"] >= args.top and (r["rp"] is not None and r["rp"] >= args.rp)

    for mkt in ("US", "KR"):
        M = [r for r in T if r["mkt"] == mkt]
        if not M:
            continue
        print(f"\n{'='*70}\n=== {mkt}  체결가 품질(zone-fill) — CLOSED {len(M)}건 (since {args.since}) ===")
        print(f"[zone_pos 3층 × 월별(OOS)]  (전체 {s([r['net'] for r in M])})")
        for b in ("bottom", "mid", "top"):
            for lbl, sub in [("전체", M)] + [(m, [x for x in M if x["mth"] == m]) for m in sorted({x["mth"] for x in M})]:
                v = [r["net"] for r in sub if band(r) == b]
                if v:
                    print(f"    {b:7} {lbl:9} {s(v)}")
        wc = [r["net"] for r in M if worst(r)]
        oth = [r["net"] for r in M if not worst(r)]
        print(f"\n[최악셀 top≥{args.top} & reward_pct≥{args.rp}]")
        print(f"    최악셀   전체 {s(wc)}")
        for m in sorted({x['mth'] for x in M}):
            v = [r["net"] for r in M if worst(r) and r["mth"] == m]
            if v:
                print(f"    최악셀   {m} {s(v)}")
        print(f"    회피후   전체 {s(oth)}")
        if wc and oth:
            neg = sum(x for x in [r['net'] for r in M] if x < 0)
            wneg = sum(x for x in wc if x < 0)
            book = st.mean([r['net'] for r in M])
            print(f"    → 최악셀 회피 시 book per {book:+.3f} → {st.mean(oth):+.3f} (Δ{st.mean(oth)-book:+.3f}/거래), "
                  f"손실집중 {100*wneg/neg:.0f}%")
        # 판정 문턱
        if mkt == "US" and wc:
            months = sorted({r['mth'] for r in M if worst(r)})
            allneg = all(st.mean([r['net'] for r in M if worst(r) and r['mth'] == m]) < 0
                         for m in months if [r for r in M if worst(r) and r['mth'] == m])
            print(f"    판정문턱: 최악셀 양월 음수={allneg}, n={len(wc)}. "
                  f"(≥2월 음수 AND n≥30 AND 회피Δ≥+0.15 → shadow 게이트 논의 / 아니면 표본대기)")

    print("\n주: 양날(체결 조이면 미체결 미스=승자놓침)은 CLOSED로 측정불가 → forward shadow 누적으로만 확증.")
    print("    zone_pos=진입시점 확정(no-lookahead). 레버=손실축소지 흑자전환 아님. red-tape와 같은 방어족.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
