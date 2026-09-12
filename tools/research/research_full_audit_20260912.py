#!/usr/bin/env python3
"""전면 점검 — 전략이 살아만 있는지 제대로 도는지 (read-only, 2026-09-12).

운영자 지시: "모든 시스템과 전략들이 제대로 운영되고 있는지, 살아만 있고 제대로 운영 안 되는 건
없는지 싹 다 점검하고, 실패를 기점으로 찾아낼 수 있는 것도 찾아라."

세 축으로 본다.
 ① **공급** — 신호가 계속 나오는가(레인이 살아 있는가).
 ② **정산** — 나온 신호가 실제로 결과가 되는가(만기 적체 없는가).
 ③ **성과** — 그 결과가 **지수 초과분**으로 양수인가(원 net 아님).

①②가 되는데 ③이 안 되면 "고칠 것", ①이 끊기면 "배선 문제", ③이 크게 음수면 "버릴 것"이다.

⚠️ **기저선(`x*`)은 판정 대상이 아니다.** `xkr_fallen3` 등은 "조건 전량 매수" 모집단이고
후보(`c_*`)를 뽑아낸 denominator다. 09-11 점검이 이미 못 박았다 — *"xkr_fallen3 −0.24%는
걸러낼 대상이 아니다. 이건 라이브 레인의 모집단이다."* 모집단을 죽이면 후보도 못 만든다.
그래서 `kind`로 나누고 **판정은 후보·실행변형에만** 매긴다.
③은 백필+forward 전체로 본다 — forward만으로는 09-01 출범이라 9세션뿐이다.

⚠️ 벤치 창은 `strategies.note`의 D<n>(없으면 KR 5 / US 7)이다. 보유가 더 긴 전략에
짧은 창을 대면 **초과가 과대평가**되므로, 음수 판정은 안전하고 양수 판정은 의심해야 한다.
⚠️ KR 표본에는 생존편향 하한 3.3%가 그대로 들어 있다.
"""
from __future__ import annotations

import math
import re
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_benchmark_excess import bench_bars  # noqa: E402

DB = ROOT / "data" / "shadow" / "virtual_books.db"
TODAY = date.today().isoformat()
STALE_DAYS = 7


def cluster_t(pairs):
    g = defaultdict(list)
    for d, v in pairs:
        g[d].append(v)
    m = [st.mean(v) for v in g.values()]
    if len(m) < 3 or st.stdev(m) == 0:
        return None, len(m)
    return st.mean(m) / (st.stdev(m) / math.sqrt(len(m))), len(m)


def main():
    con = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    notes = {r["id"]: (r["note"] or "") for r in con.execute("SELECT id, note FROM strategies")}
    bars = {"KR": bench_bars("kr", "069500"), "US": bench_bars("us", "IWM")}
    idx = {m: {b[0]: i for i, b in enumerate(v)} for m, v in bars.items()}
    cal = {m: [b[0] for b in v] for m, v in bars.items()}

    rows = defaultdict(list)
    for r in con.execute("SELECT strategy_id, session_date, net_pct, status, backfill FROM trades"):
        rows[r["strategy_id"]].append((r["session_date"], r["net_pct"], r["status"],
                                       r["backfill"] or 0))
    con.close()

    def kind_of(sid):
        if sid.startswith(("xkr_", "xus_")):
            return "기저선"
        return "후보" if sid.startswith("c_") else "실행변형"

    recs = []
    for sid, v in rows.items():
        market = "KR" if "kr" in sid else "US"
        hold = int(m.group(1)) if (m := re.search(r"\bD(\d+)\b", notes.get(sid, ""))) \
            else (5 if market == "KR" else 7)
        fwd = [x for x in v if not x[3]]
        last_supply = max((x[0] for x in fwd), default=None)
        stale = None
        if last_supply:
            stale = (datetime.fromisoformat(TODAY) - datetime.fromisoformat(last_supply)).days
        # 만기 적체: OPEN인데 보유기간 + 2세션이 지났다
        overdue = 0
        for d, _, status, bf in fwd:
            if status != "OPEN":
                continue
            try:
                el = len(cal[market]) - 1 - cal[market].index(d)
            except ValueError:
                continue
            if el > hold + 2:
                overdue += 1
        # 성과: 백필+forward 전체의 지수 초과
        ex, miss = [], 0
        for d, net, status, _ in v:
            if status != "CLOSED" or net is None:
                continue
            i = idx[market].get(d)
            if i is None or i + hold > len(bars[market]):
                miss += 1
                continue
            ex.append((d, net - (bars[market][i + hold - 1][4] / bars[market][i][1] - 1) * 100))
        t, k = cluster_t(ex)
        raw = [x[1] for x in v if x[2] == "CLOSED" and x[1] is not None]
        recs.append({"sid": sid, "kind": kind_of(sid), "market": market, "hold": hold,
                     "fwd_n": sum(1 for x in fwd if x[2] == "CLOSED"),
                     "fwd_open": sum(1 for x in fwd if x[2] == "OPEN"),
                     "last_supply": last_supply, "stale_days": stale, "overdue": overdue,
                     "n": len(ex), "days": k or 0, "bench_missing": miss,
                     "raw": st.mean(raw) if raw else None,
                     "ex": st.mean(x[1] for x in ex) if ex else None, "t": t})

    def verdict(r):
        if r["kind"] == "기저선":
            return "기저선(판정제외)"
        if r["last_supply"] is None:
            return "공급없음"
        if r["stale_days"] is not None and r["stale_days"] > STALE_DAYS:
            return "공급끊김"
        if r["overdue"]:
            return "정산적체"
        if r["ex"] is None or r["days"] < 30:
            return "표본부족"
        if r["t"] is not None and r["t"] <= -2:
            return "버릴것"
        if r["ex"] > 0 and r["t"] is not None and r["t"] >= 2:
            return "살릴것"
        return "고칠것"

    for r in recs:
        r["verdict"] = verdict(r)
    order = {"버릴것": 0, "고칠것": 1, "살릴것": 2, "정산적체": 3, "공급끊김": 4,
             "공급없음": 5, "표본부족": 6, "기저선(판정제외)": 7}
    recs.sort(key=lambda r: (order[r["verdict"]], r["ex"] if r["ex"] is not None else 0))

    L = [f"# 전략 전면 점검 — 공급·정산·성과 3축 ({TODAY})", "",
         "read-only. `tools/research/research_full_audit_20260912.py`(규약은 docstring).",
         "**성과는 지수 초과분**이다(KR=069500 / US=IWM, 창=계약 보유기간). 원 net은 참고.", "",
         f"대상 {len(recs)}전략 · 공급 정체 기준 {STALE_DAYS}일 · 오늘 {TODAY}", "",
         "| 전략 | 구분 | 시장 | D | 판정 | 전체건 | 일수 | 원net | **초과** | t(날짜) | fwd정산 | fwd OPEN | 최근공급 |",
         "|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in recs:
        f = lambda x, s="%": f"{x:+.2f}{s}" if x is not None else "-"      # noqa: E731
        L.append(f"| `{r['sid']}` | {r['kind']} | {r['market']} | {r['hold']} | **{r['verdict']}** | "
                 f"{r['n']} | {r['days']} | {f(r['raw'])} | **{f(r['ex'])}** | "
                 f"{f(r['t'], '')} | {r['fwd_n']} | {r['fwd_open']} | "
                 f"{r['last_supply'] or '-'} |")

    cnt = defaultdict(int)
    for r in recs:
        cnt[r["verdict"]] += 1
    judged = [r for r in recs if r["kind"] != "기저선"]
    L += ["", "## 판정 분포", "",
          f"판정 대상 **{len(judged)}**전략(기저선 {cnt['기저선(판정제외)']}개 제외)", "",
          " · ".join(f"**{k}** {cnt[k]}" for k in sorted(cnt, key=lambda x: order[x])
                     if k != "기저선(판정제외)"), "",
          "판정 규칙(사전 고정): 공급 없음/끊김(>7일) → 배선 문제 · OPEN이 보유기간+2세션 초과 → 정산적체",
          "· 일수<30 → 표본부족 · 초과 t≤−2 → 버릴것 · 초과>0 & t≥2 → 살릴것 · 나머지 → 고칠것", "",
          "⚠️ 벤치 창이 계약보다 짧으면 초과가 **과대**평가된다 — 음수 판정은 안전하고 양수는 의심한다.",
          "⚠️ KR 표본에는 생존편향 하한 3.3%가 그대로 들어 있다."]

    out = ROOT / "docs" / "reports" / f"full_audit_strategies_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
