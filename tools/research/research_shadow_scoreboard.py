#!/usr/bin/env python3
"""쉐도우 전략 성적표 — 전 레인 한 장 (read-only, 재사용 가능).

    python tools/research/research_shadow_scoreboard.py [기준일]

## 규약 — 이번까지 고친 것 전부 반영

  **① 벤치는 실제 청산 시점까지만.** TP/SL로 일찍 닫힌 거래에 계약 보유기간 전체의
     벤치를 물리면 상승장에서 초과가 과소평가된다(09-20 감사, 정정폭 +0.09~0.86%p).
  **② KR 벤치를 하나로 못 박지 않는다.** `069500`(KOSPI200 대형주 200)만 쓰면
     2026년 7세션 +2.15%가 허들인데 KOSDAQ150은 −0.41%다. 우리 KR 전략은 고유
     1,510종목이라 둘 다 아니다. **어느 쪽이든 명시해서 싣는다.**
  **③ 판정 단위는 날짜다.** 같은 날 수십~수백 건이 들어가므로 건 단위 t는 부풀려진다.
     건 가중과 일 가중이 갈리면 **일 가중이 판정값**이다.
  **④ forward와 백필을 섞지 않는다.** 백필은 인샘플이라 판정 표본이 아니다.
  **⑤ 기저선(`x*`)은 모집단이지 전략이 아니다.** 구분해서 싣는다.

## 읽을 때 붙잡을 것

- forward 표본이 작다(09-01 출범). 여기 t는 **판정이 아니라 현재 위치**다.
- 정산분만 보면 승자 편향이 남는다(이긴 건 TP로 먼저 닫힌다). OPEN 수를 같이 본다.
- 09-18 실측 분모 96~301을 감안하면 판정 문턱은 |t| ≥ 2가 아니라 3~3.5다.

  **⑥ 초과는 만기 도달분에만 매긴다.** 진입일+보유기간이 아직 안 지났는데 닫혀 있는 건
     필연적으로 조기 청산(TP 승자)이다. 그걸 넣으면 승자만 세는 것이고, 실제로 초안에서
     만기도달 0%인 arm이 t +6.07로 표 맨 위에 올라왔다.
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_benchmark_excess import bench_bars  # noqa: E402
from tools.virtual_books import (  # noqa: E402
    BE, HOLD_SESSIONS, SL, STRATEGIES, TP, bars_kr,
)
from research_early_exit_no_bump import bars as bars_us  # noqa: E402

HOLDS = {s["id"]: int(s.get("hold", HOLD_SESSIONS)) for s in STRATEGIES}
SHADOW = ROOT / "data" / "shadow"
VB = SHADOW / "virtual_books.db"
KQ_CACHE = ROOT / "data" / "analysis" / "kr_index_etf_prices.csv"
FORWARD_START = "2026-09-01"
KR_FALLEN_ORDER = 220_000


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def jl(name):
    p = SHADOW / f"{name}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def exit_bar(entry, win, be_lock, hold):
    if not win or entry <= 0:
        return None
    peak = (win[0][4] - entry) / entry * 100
    for i, (_d, _o, hi, _lo, c, _v) in enumerate(win[:hold]):
        hip = (hi - entry) / entry * 100 if i > 0 else (c - entry) / entry * 100
        cp = (c - entry) / entry * 100
        if hip >= TP or cp <= SL:
            return i
        if be_lock and peak >= BE and cp <= 0:
            return i
        peak = max(peak, hip)
    return hold - 1 if len(win) >= hold else None


def day_stat(pairs):
    """(일평균, 날짜t, 일수) — 판정값."""
    g = defaultdict(list)
    for d, v in pairs:
        g[d].append(v)
    m = [st.mean(v) for v in g.values()]
    if len(m) < 3 or st.stdev(m) == 0:
        return (st.mean(m) if m else None), None, len(m)
    return st.mean(m), st.mean(m) / (st.stdev(m) / math.sqrt(len(m))), len(m)


def main():
    today = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    ks = bench_bars("kr", "069500")
    ksi = {b[0]: i for i, b in enumerate(ks)}
    rows_kq = sorted(csv.DictReader(KQ_CACHE.open(encoding="utf-8-sig")), key=lambda r: r["date"])
    kq = [(r["date"], f(r["229200_open"]), f(r["229200_close"])) for r in rows_kq]
    kq = [x for x in kq if x[1] and x[2]]
    kqi = {d: i for i, (d, _, _) in enumerate(kq)}
    us = bench_bars("us", "IWM")
    usi = {b[0]: i for i, b in enumerate(us)}

    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True)
    rows = con.execute(
        "SELECT strategy_id, session_date, ticker, entry_price, net_pct, status, backfill "
        "FROM trades WHERE session_date >= ?", (FORWARD_START,)).fetchall()
    con.close()

    # 만기 도달 판정용 거래일 달력
    cal = {"KR": [b[0] for b in ks], "US": [b[0] for b in us]}

    def matured(mk, d, hold):
        """진입일 d에서 보유기간이 실제로 지났나."""
        try:
            return len(cal[mk]) - 1 - cal[mk].index(d) >= hold
        except ValueError:
            return False

    arms = defaultdict(lambda: {"cl": [], "open": 0, "ex1": [], "ex2": [],
                                "entries": 0, "mat": 0})
    for sid, d, tk, ep, net, status, bf in rows:
        if bf:
            continue
        a = arms[sid]
        mk0 = "KR" if str(tk)[:1].isdigit() else "US"
        a["entries"] += 1
        if matured(mk0, d, HOLDS.get(sid, HOLD_SESSIONS)):
            a["mat"] += 1
        if status == "OPEN":
            a["open"] += 1
            continue
        if net is None:
            continue
        a["cl"].append((d, net))
        # ⚠️ 판정 표본은 **진입일+보유기간이 지난 것만**이다. 아직 만기가 안 지났는데
        # 닫혀 있다는 건 필연적으로 조기 청산(=TP 승자)이므로, 넣으면 승자만 세게 된다.
        if not matured(mk0, d, HOLDS.get(sid, HOLD_SESSIONS)):
            continue
        mk = "KR" if str(tk)[:1].isdigit() else "US"
        hold = HOLDS.get(sid, HOLD_SESSIONS)
        bb = bars_kr(tk) if mk == "KR" else bars_us(tk)
        j = next((i for i, b in enumerate(bb) if b[0] == d), None)
        ei = exit_bar(float(ep), bb[j:j + hold], mk == "US", hold) if (j is not None and ep) else None
        if ei is None:
            continue
        if mk == "KR":
            i = ksi.get(d)
            if i is not None and i + ei < len(ks):
                a["ex1"].append((d, net - (ks[i + ei][4] / ks[i][1] - 1) * 100))
            q = kqi.get(d)
            if q is not None and q + ei < len(kq):
                a["ex2"].append((d, net - (kq[q + ei][2] / kq[q][1] - 1) * 100))
        else:
            i = usi.get(d)
            if i is not None and i + ei < len(us):
                a["ex1"].append((d, net - (us[i + ei][4] / us[i][1] - 1) * 100))

    def kind(sid):
        if sid.startswith(("xkr_", "xus_")):
            return "기저선"
        return "후보" if sid.startswith("c_") else "실행변형"

    recs = []
    for sid, a in arms.items():
        if not a["cl"]:
            continue
        mk = "KR" if "kr" in sid else "US"
        m1, t1, d1 = day_stat(a["ex1"])
        m2, t2, _ = day_stat(a["ex2"]) if a["ex2"] else (None, None, 0)
        recs.append({"sid": sid, "kind": kind(sid), "mk": mk, "n": len(a["cl"]),
                     "open": a["open"], "days": d1,
                     "entries": a["entries"], "mat": a["mat"],
                     "raw": st.mean(v for _, v in a["cl"]),
                     "m1": m1, "t1": t1, "m2": m2, "t2": t2})
    recs.sort(key=lambda r: -(r["t2"] if r["t2"] is not None else (r["t1"] or -99)))

    L = [f"# 쉐도우 전략 성적표 — forward {FORWARD_START} ~ {today}", "",
         "read-only. `tools/research/research_shadow_scoreboard.py`(규약은 docstring).",
         "**백필 제외 · 벤치는 실제 청산 시점까지 · KR은 두 지수 병기 · 판정값은 일 가중**",
         "**초과는 만기 도달분에만 매긴다** — 미만기 정산분은 전부 조기 청산(승자)이라 넣으면 편향된다.",
         "**전부 쉐도우다 — 실제 손익이 아니다**(09-01 운영자 결정으로 실매수 0).", ""]

    # ── 문턱 통과 레인 ──
    panic = [r for r in jl("kr_index_etf_panic")
             if r.get("kind") == "trade" and r.get("mode") == "live"]
    cl = [r for r in panic if r.get("net_pct") is not None]
    rung = [r for r in jl("kq_close_rung") if r.get("mode") == "live"]
    kf = [r for r in jl("kr_fallen_shadow") if r.get("pass_all")
          and r.get("status") == "SETTLED" and r.get("net_pct") is not None]
    L += ["## 1. 문턱 통과 레인 — 사전등록을 통과한 축", "",
          "| 레인 | forward 정산 | 승 | 평균 net | 합 |", "|---|---:|---:|---:|---:|",
          f"| 패닉 ETF v2 | **{len(cl)}건** | {sum(1 for r in cl if r['net_pct']>0)} | "
          f"{st.mean(r['net_pct'] for r in cl):+.2f}% | {sum(r['net_pct'] for r in cl):+.2f}% |"
          if cl else "| 패닉 ETF v2 | 0건 | - | - | - |",
          f"| 종가 rung(관측) | {len(rung)}건 | - | - | - |", "",
          "## 2. 라이브 규칙 쉐도우 — 실제 돈이 나갔다면", "",
          f"KR 급락 규칙 통과·정산 **{len(kf)}건**"
          + (f" · 평균 {st.mean(r['net_pct'] for r in kf):+.2f}%"
             f" · 22만 기준 **{sum(KR_FALLEN_ORDER*r['net_pct']/100 for r in kf):+,.0f}원**" if kf else ""), ""]
    for r in sorted(kf, key=lambda x: x["session_date"]):
        L.append(f"- {r['session_date']} `{r['ticker']}` {r['net_pct']:+.2f}% "
                 f"({KR_FALLEN_ORDER*r['net_pct']/100:+,.0f}원)")

    # ── 인큐베이터 ──
    L += ["", "## 3. 인큐베이터 arm별 — forward만", "",
          "**판정값은 일 가중**이다. 건 가중은 같은 날 수십 건이 몰려 t가 부풀려진다.", "",
          "⚠️ **`만기도달`이 `진입`보다 훨씬 작으면 이 성적은 승자 편향이다** — 이긴 건 TP로 "
          "먼저 닫히고 진 건 만기까지 열려 있다. 만기도달률이 낮은 arm의 t는 읽지 않는다.", "",
          "| arm | 구분 | 진입 | 정산 | OPEN | **만기도달** | 일수 | 원net | "
          "KOSPI200 일평균/t | KOSDAQ150·IWM 일평균/t |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in recs:
        c1 = f"{r['m1']:+.2f}% · t {r['t1']:+.2f}" if r["t1"] is not None else (
            f"{r['m1']:+.2f}% · t -" if r["m1"] is not None else "-")
        c2 = f"{r['m2']:+.2f}% · t {r['t2']:+.2f}" if r["t2"] is not None else (
            f"{r['m2']:+.2f}% · t -" if r["m2"] is not None else "—")
        mr = r["mat"] / r["entries"] * 100 if r["entries"] else 0
        flag = "**" if mr < 50 else ""
        L.append(f"| `{r['sid']}` | {r['kind']} | {r['entries']:,} | {r['n']:,} | {r['open']:,} | "
                 f"{flag}{r['mat']:,} ({mr:.0f}%){flag} | {r['days']} | "
                 f"{r['raw']:+.2f}% | {c1} | {c2} |")

    judged = [r for r in recs if r["kind"] != "기저선"]
    pos2 = [r for r in judged if (r["t2"] if r["t2"] is not None else r["t1"]) and
            (r["t2"] if r["t2"] is not None else r["t1"]) > 0]
    L += ["", f"**판정 대상 {len(judged)}arm · 일 가중 양수 {len(pos2)}개 · "
          f"|t| ≥ 2 도달 {sum(1 for r in judged if (r['t2'] or r['t1'] or 0) >= 2)}개 · "
          f"분모 보정 문턱(|t| ≥ 3) 도달 "
          f"{sum(1 for r in judged if (r['t2'] or r['t1'] or 0) >= 3)}개**", "",
          "## 읽는 법", "",
          f"- forward는 {FORWARD_START} 출범이라 표본이 작다. 여기 t는 **판정이 아니라 현재 위치**다.",
          "- KR 두 벤치가 갈리면 그 arm의 판정은 보류다 — 올바른 벤치(유니버스 정합)는 아직 없다.",
          "- OPEN이 정산과 비슷하면 그 성적은 **아직 절반만 본 것**이다(이긴 게 먼저 닫힌다).",
          "- 09-18 실측 분모 96~301을 감안하면 판정 문턱은 |t| ≥ 3~3.5다.", ""]

    out = ROOT / "docs" / "reports" / f"shadow_scoreboard_{today.replace('-','')}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
