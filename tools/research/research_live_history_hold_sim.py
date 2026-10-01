# -*- coding: utf-8 -*-
"""실제 체결 351건을 그대로 두고 '청산만' 바꿔본다 (read-only, lookahead 없음).

진입 = 실제 체결일·실제 체결가(trips_enriched.json, 브로커 체결 기반). 선택 편향 없음 — 실제로 산 것 전부.
청산 변형:
  ACT  : 실제 결과(pnl_pct, 수수료 전)
  HOLD H : 진입일 포함 H세션째 종가 청산(목표·손절 없음) — "장에 올라타 있었다면"
  PLAN H : Claude 플랜 목표가/손절가 + H세션 상한. 진입일은 종가만(장중 순서 모름 → 보수).
           플랜 없는 거래(Path A·슬리브)는 PLAN 계산 제외.
비용: 비교를 위해 전 변형 수수료 전(gross)으로 둔다. 실측 왕복 KR 0.21% · US 0.44%는 표 아래에 같은 값으로 빠진다.
벤치: 같은 창 시가→종가, KR 229200·069500, US IWM·SPY. 판정은 진입일 클러스터.
진입일 봉 매칭: US는 KST 날짜일 수 있어 d, d−1 중 [저가,고가]가 체결가를 담는 봉을 쓴다.
"""
import csv
import json
import math
import sqlite3
import statistics as st
from collections import defaultdict
from contextlib import closing
from pathlib import Path

ROOT = Path(r"E:/code/claudetrade")
HERE = ROOT / "data" / "analysis" / "live_history_20260924"
HOLDS = (1, 3, 5, 7)
_B = {}


def bars(mk, t):
    k = (mk, t)
    if k not in _B:
        out = []
        p = ROOT / "data" / "price" / mk.lower() / f"{mk.lower()}_{t}.csv"
        if p.exists():
            with p.open(encoding="utf-8-sig", newline="") as fh:
                for r in csv.reader(fh):
                    if len(r) >= 6 and r[0][:2] == "20":
                        try:
                            out.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])))
                        except ValueError:
                            pass
        _B[k] = sorted(out)
    return _B[k]


def day_t(pairs):
    g = defaultdict(list)
    for d, v in pairs:
        g[d].append(v)
    m = [st.mean(v) for v in g.values()]
    if len(m) < 5 or st.stdev(m) == 0:
        return None, None, len(m)
    return st.mean(m), st.mean(m) / (st.stdev(m) / math.sqrt(len(m))), len(m)


def find_i(b, d, e):
    idx = [n for n, x in enumerate(b) if x[0] <= d][-2:]
    for n in reversed(idx):
        if b[n][3] * 0.995 <= e <= b[n][2] * 1.005:
            return n
    return None


def main():
    trips = json.loads((HERE / "trips_enriched.json").read_text(encoding="utf-8"))
    with closing(sqlite3.connect(f"file:{ROOT / 'data' / 'v2_event_store.db'}?mode=ro", uri=True, timeout=10)) as c:
        plans = {}
        for (p,) in c.execute("select payload_json from lifecycle_events where runtime_mode='live' "
                              "and event_type='CLAUDE_PRICE_PLAN_CREATED'"):
            pl = (json.loads(p) or {}).get("plan") or {}
            did = pl.get("decision_id")
            if did and did not in plans:
                plans[did] = (float(pl.get("sell_target") or 0), float(pl.get("stop_loss") or 0))
    bench = {"KR": {"KQ150": bars("KR", "229200"), "K200": bars("KR", "069500")},
             "US": {"IWM": bars("US", "IWM"), "SPY": bars("US", "SPY")}}
    bidx = {m: {k: {x[0]: n for n, x in enumerate(v)} for k, v in bb.items()} for m, bb in bench.items()}

    res = defaultdict(list)   # (mk, lane_grp, variant, col) -> [(d, v)]
    miss = defaultdict(int)
    for tr in trips:
        mk, t, e, d = tr["market"], tr["ticker"], float(tr.get("entry") or 0), tr["entry_date"]
        grp = "PathB" if tr.get("route") == "path_b" else ("슬리브" if tr.get("sleeve") else "PathA/기타")
        b = bars(mk, t)
        i = find_i(b, d, e) if b and e > 0 else None
        if i is None:
            miss[mk] += 1
            continue
        d0 = b[i][0]

        def add(var, ret, j):
            for g in (grp, "전체"):
                res[(mk, g, var, "raw")].append((d0, ret))
                for nm, bb in bench[mk].items():
                    a, z = bidx[mk][nm].get(d0), bidx[mk][nm].get(b[j][0])
                    if a is not None and z is not None:
                        res[(mk, g, var, nm)].append((d0, ret - (bb[z][4] / bb[a][1] - 1) * 100))

        if tr.get("pnl_pct") is not None:
            jx = next((n for n, x in enumerate(b) if x[0] >= (tr.get("exit_date") or d0)), i)
            add("ACT", float(tr["pnl_pct"]), max(jx, i))
        pl = plans.get(tr.get("decision"))
        for H in HOLDS:
            if i + H - 1 < len(b):
                add(f"HOLD{H}", (b[i + H - 1][4] / e - 1) * 100, i + H - 1)
            if pl and pl[0] > 0:
                tgt, stp = pl
                out = None
                for k in range(H):
                    j = i + k
                    if j >= len(b):
                        break
                    _, oo, hh, ll, cc = b[j]
                    if k == 0:
                        if cc >= tgt:
                            out = ((tgt / e - 1) * 100, j); break
                        if stp > 0 and cc <= stp:
                            out = ((cc / e - 1) * 100, j); break
                        continue
                    if stp > 0 and ll <= stp:
                        out = ((min(oo, stp) / e - 1) * 100, j); break
                    if hh >= tgt:
                        out = ((max(oo, tgt) / e - 1) * 100, j); break
                else:
                    if i + H - 1 < len(b):
                        out = ((b[i + H - 1][4] / e - 1) * 100, i + H - 1)
                if out:
                    add(f"PLAN{H}", out[0], out[1])

    L = ["# 실제 체결 351건 — 청산만 바꿔보기 (수수료 전 gross, 진입일 클러스터)", "",
         f"봉 매칭 실패: KR {miss['KR']} · US {miss['US']}", ""]
    for mk in ("KR", "US"):
        nms = list(bench[mk])
        for g in ("전체", "PathB", "슬리브", "PathA/기타"):
            if not res.get((mk, g, "ACT", "raw")):
                continue
            L += [f"## {mk} · {g}", "",
                  "| 청산 방식 | 건수 | 일수 | 원 gross 일평균·t | " + " | ".join(f"vs {n}" for n in nms) + " |",
                  "|---|---:|---:|---:|" + "---:|" * len(nms)]
            for var in ["ACT"] + [f"HOLD{h}" for h in HOLDS] + [f"PLAN{h}" for h in HOLDS]:
                r = res.get((mk, g, var, "raw"))
                if not r:
                    continue
                m, tt, n = day_t(r)
                if m is None:
                    continue
                cells = []
                for nm in nms:
                    mm, t2, _ = day_t(res.get((mk, g, var, nm), []))
                    cells.append(f"{mm:+.2f}% · t {t2:+.2f}" if mm is not None else "-")
                L.append(f"| {var} | {len(r)} | {n} | {m:+.2f}% · t {tt:+.2f} | " + " | ".join(cells) + " |")
            L.append("")
    txt = "\n".join(L)
    (HERE / "actual_hold_sim.md").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
