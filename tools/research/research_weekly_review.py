#!/usr/bin/env python3
"""주간 리뷰 — 정산분 종목별 (read-only, 재사용 가능).

    python tools/research/research_weekly_review.py 2026-09-17 2026-09-22

## 09-16판에서 고친 것 둘

**① 벤치 창을 실제 청산 시점으로.** 전에는 TP/SL로 일찍 닫힌 거래에도 계약 보유기간
전체(7세션)의 벤치를 물렸다. 09-20 감사에서 정정폭 +0.09~0.86%p로 확인됐다.
여기서는 `contract_exit_v2`와 같은 규약으로 **실제 청산 봉을 복원해** 그 시점까지만 벤치를 뺀다.

**② 벤치를 하나로 못 박지 않는다.** KR 판정에 `069500`(KOSPI200 대형주 200)만 썼는데,
우리 KR 전략은 고유 1,510종목으로 사실상 전 시장이다. 2026년 7세션 평균이
KOSPI200 +2.15% vs KOSDAQ150 −0.41%로 갈려서 **벤치 선택이 부호를 바꾼다.**
올바른 벤치(유니버스 정합)는 아직 없으므로 **둘 다 싣고 어느 쪽인지 명시**한다.

## 그대로 유지하는 규약

- 창은 **정산일**(`settled_at`) 기준. 진입일로 자르면 보유기간이 안 지나 빈다.
- 승자 편향을 숨기지 않는다 — `exit_reason`을 싣고 만기도달(D_MAT)을 따로 낸다.
- 같은 종목·진입일을 여러 arm이 잡으면 net이 같다. **묶어서 한 줄**, arm 수는 중복이지 확인이 아니다.
- 기저선(`x*`)은 모집단이라 종목 나열에서 뺀다.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import statistics as st
import sys
from collections import defaultdict
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
    """`contract_exit_v2`와 같은 규약으로 **몇 번째 봉에서 닫혔는지**. 미완결이면 None."""
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


def main():
    lo = sys.argv[1] if len(sys.argv) > 2 else "2026-09-17"
    hi = sys.argv[2] if len(sys.argv) > 2 else "2026-09-22"

    ks = bench_bars("kr", "069500")
    ksi = {b[0]: i for i, b in enumerate(ks)}
    rows_kq = sorted(csv.DictReader(KQ_CACHE.open(encoding="utf-8-sig")), key=lambda r: r["date"])
    kq = [(r["date"], f(r["229200_open"]), f(r["229200_close"])) for r in rows_kq]
    kq = [x for x in kq if x[1] and x[2]]
    kqi = {d: i for i, (d, _, _) in enumerate(kq)}
    us = bench_bars("us", "IWM")
    usi = {b[0]: i for i, b in enumerate(us)}

    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT strategy_id, session_date, ticker, entry_price, exit_reason, net_pct "
        "FROM trades WHERE backfill=0 AND status='CLOSED' "
        "AND substr(settled_at,1,10) BETWEEN ? AND ?", (lo, hi)).fetchall()
    con.close()

    base = [r for r in rows if r["strategy_id"].startswith(("xkr_", "xus_"))]
    judged = [r for r in rows if not r["strategy_id"].startswith(("xkr_", "xus_"))]

    g = defaultdict(lambda: {"arms": [], "net": None, "reason": None, "hold": HOLD_SESSIONS,
                             "entry": None})
    for r in judged:
        k = (r["ticker"], r["session_date"], round(r["net_pct"] or 0, 4))
        g[k]["arms"].append(r["strategy_id"])
        g[k]["net"] = r["net_pct"]
        g[k]["reason"] = r["exit_reason"]
        g[k]["hold"] = HOLDS.get(r["strategy_id"], HOLD_SESSIONS)
        g[k]["entry"] = r["entry_price"]

    recs = []
    for (tk, d, _), v in g.items():
        mk = "KR" if tk[:1].isdigit() else "US"
        bb = bars_kr(tk) if mk == "KR" else bars_us(tk)
        j = next((i for i, b in enumerate(bb) if b[0] == d), None)
        ei = None
        if j is not None and v["entry"]:
            ei = exit_bar(float(v["entry"]), bb[j:j + v["hold"]], mk == "US", v["hold"])
        ex = {}
        if ei is not None:
            if mk == "KR":
                i = ksi.get(d)
                if i is not None and i + ei < len(ks):
                    ex["KOSPI200"] = v["net"] - (ks[i + ei][4] / ks[i][1] - 1) * 100
                q = kqi.get(d)
                if q is not None and q + ei < len(kq):
                    ex["KOSDAQ150"] = v["net"] - (kq[q + ei][2] / kq[q][1] - 1) * 100
            else:
                i = usi.get(d)
                if i is not None and i + ei < len(us):
                    ex["IWM"] = v["net"] - (us[i + ei][4] / us[i][1] - 1) * 100
        recs.append({"ticker": tk, "date": d, "market": mk, "net": v["net"],
                     "reason": v["reason"], "arms": sorted(set(v["arms"])),
                     "held": (ei + 1) if ei is not None else None, "ex": ex})
    recs.sort(key=lambda r: -(r["net"] or 0))

    L = [f"# 주간 리뷰 — 정산 완료분 ({lo} ~ {hi})", "",
         "read-only. `tools/research/research_weekly_review.py`(규약은 docstring).",
         "**벤치는 실제 청산 시점까지만** 뺀다(09-20 감사 반영). "
         "**KR은 벤치를 하나로 못 박지 않는다** — 지수 선택이 부호를 바꾼다.",
         "**전부 쉐도우다 — 실제 손익이 아니다**(09-01 운영자 결정으로 실매수 0).", ""]

    # ── A. 문턱 통과 레인 ──
    panic = [r for r in jl("kr_index_etf_panic") if r.get("mode") == "live"]
    sess = [r for r in panic if r.get("kind") == "session" and lo <= r.get("session_date", "") <= hi]
    trades = [r for r in panic if r.get("kind") == "trade" and lo <= r.get("session_date", "") <= hi]
    L += ["## A. 문턱 통과 레인 — 패닉 ETF v2", "",
          f"창내 세션 **{len(sess)}일** · 신호 **{sum(1 for r in sess if r.get('signal_1519'))}일** · "
          f"진입 **{len(trades)}건**", ""]
    for r in sess:
        L.append(f"- {r['session_date']} 갭 {r.get('kq_gap'):+.2f}% · "
                 f"{'**신호**' if r.get('signal_1519') else '무신호'}")
    L += ["", f"종가 rung forward 누적: **{len([r for r in jl('kq_close_rung') if r.get('mode')=='live'])}건**", ""]

    # ── B. 라이브 규칙 ──
    kf = [r for r in jl("kr_fallen_shadow") if lo <= (r.get("session_date") or "") <= hi]
    L += ["## B. 라이브 규칙 쉐도우 — KR 급락", "",
          f"창내 스캔 **{len(kf)}건** · 규칙 통과(pass_all) **{sum(1 for r in kf if r.get('pass_all'))}건**",
          "", "실제 돈이 나갔다면 이번 창에 나간 금액은 **0원**이다." if not any(r.get("pass_all") for r in kf) else "", ""]

    # ── C. 인큐베이터 ──
    L += ["## C. 인큐베이터 — 종목별 전건 (판정 대상 arm만)", "",
          f"행 {len(judged)}건 → **{len(recs)}종목·진입일 조합**", "",
          "| 종목 | 시장 | 진입일 | 보유 | 출구 | net | vs KOSPI200 | vs KOSDAQ150 / IWM | arm |",
          "|---|---|---|---:|---|---:|---:|---:|---|"]
    for r in recs:
        arms = ", ".join(f"`{a}`" for a in r["arms"][:2])
        if len(r["arms"]) > 2:
            arms += f" 외 {len(r['arms'])-2}"
        e1 = r["ex"].get("KOSPI200")
        e2 = r["ex"].get("KOSDAQ150") or r["ex"].get("IWM")
        fmt = lambda x: f"{x:+.2f}%" if x is not None else "-"      # noqa: E731
        L.append(f"| **{r['ticker']}** | {r['market']} | {r['date']} | "
                 f"{r['held'] or '-'}일 | {r['reason']} | {fmt(r['net'])} | "
                 f"{fmt(e1)} | {fmt(e2)} | {arms} |")

    for key, label in (("KOSPI200", "KR vs KOSPI200"), ("KOSDAQ150", "KR vs KOSDAQ150"),
                       ("IWM", "US vs IWM")):
        v = [r["ex"][key] for r in recs if key in r["ex"]]
        mat = [r["ex"][key] for r in recs if key in r["ex"] and r["reason"] == "D_MAT"]
        if not v:
            continue
        L += ["", f"**{label}** — 전체 {len(v)}종목 평균 **{st.mean(v):+.2f}%** · "
              f"양수 {sum(1 for x in v if x > 0)}/{len(v)}"
              + (f" · 만기도달만 {len(mat)}종목 **{st.mean(mat):+.2f}%**" if mat else "")]

    bnet = [r["net_pct"] for r in base if r["net_pct"] is not None]
    L += ["", "## D. 기저선(`x*`) — 판정 대상 아님", "",
          f"{len(base)}건 정산 · net 평균 {st.mean(bnet):+.2f}%" if bnet else "정산 없음", "",
          "## 읽는 법", "",
          "- **벤치가 둘로 갈리면 그 종목군의 판정은 보류다.** 어느 지수가 맞는지 아직 못 정했다.",
          "- 정산분만 보면 승자 편향이 남는다 — 만기도달(D_MAT)이 편향 없는 유일한 부분집합이다.",
          "- 창이 짧으면 이건 판정이 아니라 **기록**이다.", ""]

    out = ROOT / "docs" / "reports" / f"weekly_review_{lo.replace('-','')}_{hi.replace('-','')}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
