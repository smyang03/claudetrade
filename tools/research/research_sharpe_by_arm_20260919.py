#!/usr/bin/env python3
"""전략별 건당 샤프 — 섞지 말고 쪼개서 (read-only, 2026-09-19).

## 왜 다시 하나

09-19 1차에서 기저선 전체를 **통째로 묶어** 샤프를 냈다(KR −0.230 / US −0.064).
운영자 지적: *"개별주 특성에서 평균을 분해해서 전략별로 나눠보면 모든 전략이 수익을
낼 수는 없지, 개발중이니까."*

맞다. 두 가지를 틀렸다.
  **①** `xkr_fallen3`(급락)·`xkr_rise5`(상승)·`xkr_breakout`(돌파)·`xkr_volspike`(거래량급증)는
     **서로 다른 전략**이다. 섞어 평균 내면 죽은 축이 산 축을 덮는다. CLAUDE.md의
     "평균의 오류를 범하지 않는다"를 내가 정면으로 어겼다.
  **②** 후보 arm(`c_*`)을 **아예 뺐다.** 기저선만 봤는데, 실제 전략은 `c_*`다.
     그중엔 평균이 양수인 것도 있다(`c_kr_fallen_regime` +2.50%).

## 이번 규약

- **arm 단위로** 평균·표준편차·건당 샤프를 낸다. 시장별 통합 평균은 내지 않는다.
- 판정은 **지수 초과분**(KR=069500 / US=IWM, 창=arm 계약 보유기간).
- 건당 샤프에서 **t≥2 도달 필요 표본**을 역산한다: `n = (2/샤프)²`.
- 피처가 다 있는 arm은 **arm 안에서** 날짜 3분위를 잘라 최고 샤프 칸도 같이 낸다.
  (arm 밖 비교가 아니라 arm 안 개선이므로 모집단이 섞이지 않는다.)
- 기저선(`x*`)은 **판정 대상이 아니라 모집단**이다. 표에 같이 싣되 구분 표기한다.

⚠️ 샤프가 음수인 arm은 표본을 쌓아도 양수로 확정되지 않는다 — "느린 것"이 아니라
"그 방향으로는 판정 불가"다. 다만 **부호가 반대인 쪽(공매도)이 가능한지는 별개 문제**이고
KR 개별주는 개인 공매도가 사실상 막혀 있어 실행 경로가 없다.
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_benchmark_excess import bench_bars  # noqa: E402
from tools.virtual_books import HOLD_SESSIONS, STRATEGIES  # noqa: E402

HOLDS = {s["id"]: int(s.get("hold", HOLD_SESSIONS)) for s in STRATEGIES}
VB = ROOT / "data" / "shadow" / "virtual_books.db"
BARS = {"KR": bench_bars("kr", "069500"), "US": bench_bars("us", "IWM")}
IDX = {m: {b[0]: i for i, b in enumerate(v)} for m, v in BARS.items()}
FEATURES = ["chg", "gap", "dvol", "vol_spike", "ibs", "rv20", "mom20", "from_high20",
            "ma20_disc", "cum5", "min1_in5", "ret60", "max21", "down_streak", "price"]
MIN_N = 150          # arm 판정 최소 표본
MIN_CELL = 300       # 칸 판정 최소 표본


def kind_of(sid):
    if sid.startswith(("xkr_", "xus_")):
        return "기저선"
    return "후보" if sid.startswith("c_") else "실행변형"


def load():
    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True)
    rows = con.execute("SELECT strategy_id, ticker, session_date, net_pct, meta FROM trades "
                       "WHERE status='CLOSED' AND net_pct IS NOT NULL").fetchall()
    con.close()
    out = defaultdict(list)
    for sid, tk, d, net, meta in rows:
        mk = "KR" if "kr" in sid else "US"
        h = HOLDS.get(sid, HOLD_SESSIONS)
        i = IDX[mk].get(d)
        if i is None or i + h > len(BARS[mk]):
            continue
        bench = (BARS[mk][i + h - 1][4] / BARS[mk][i][1] - 1) * 100
        ft = (json.loads(meta or "{}").get("feat") or {})
        out[sid].append({"mk": mk, "d": d, "ex": net - bench, "f": ft})
    return out


def sharpe(vals):
    if len(vals) < 3:
        return None, None, None
    m, s = st.mean(vals), st.stdev(vals)
    return m, s, (m / s if s else None)


def main():
    arms = load()
    recs = []
    for sid, v in arms.items():
        if len(v) < MIN_N:
            continue
        m, s, sh = sharpe([x["ex"] for x in v])
        if sh is None:
            continue
        days = len({x["d"] for x in v})
        recs.append({"sid": sid, "kind": kind_of(sid), "mk": v[0]["mk"], "n": len(v),
                     "days": days, "mean": m, "sd": s, "sharpe": sh,
                     "need": (2 / sh) ** 2 if sh > 0 else None,
                     "feat": "rv20" in v[0]["f"]})
    recs.sort(key=lambda r: -r["sharpe"])

    L = ["# 전략별 건당 샤프 — 섞지 말고 쪼개서 (2026-09-19)", "",
         "read-only. `tools/research/research_sharpe_by_arm_20260919.py`(규약은 docstring).",
         "1차에서 기저선을 통째로 묶어 샤프를 낸 것이 **평균의 오류**였다. arm 단위로 다시 낸다.",
         "판정은 지수 초과분. **건당 샤프 = 평균 ÷ 표준편차**, `t≥2 필요 n = (2/샤프)²`.", "",
         f"대상 {len(recs)}arm (표본 {MIN_N}건 이상)", "",
         "| arm | 구분 | 시장 | n | 일수 | 평균초과 | 표준편차 | **건당 샤프** | t≥2 필요 n |",
         "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in recs:
        need = f"{r['need']:,.0f}건" if r["need"] else "**도달 불가**"
        L.append(f"| `{r['sid']}` | {r['kind']} | {r['mk']} | {r['n']:,} | {r['days']} | "
                 f"{r['mean']:+.3f}% | {r['sd']:.2f}% | **{r['sharpe']:+.3f}** | {need} |")

    pos = [r for r in recs if r["sharpe"] > 0]
    L += ["", f"**양수 샤프 arm {len(pos)}/{len(recs)}개.**", ""]
    if pos:
        L += ["| arm | 구분 | 샤프 | t≥2 필요 n | 현재 n | 남은 표본 |", "|---|---|---:|---:|---:|---:|"]
        for r in pos:
            L.append(f"| `{r['sid']}` | {r['kind']} | {r['sharpe']:+.3f} | {r['need']:,.0f} | "
                     f"{r['n']:,} | {max(0, r['need']-r['n']):,.0f} |")
        L.append("")
        L.append("⚠️ '현재 n'은 **백필 포함**이다. 백필은 인샘플이라 판정 표본이 아니다 — "
                 "forward만 센다. 여기 필요 n은 forward 기준으로 다시 읽어야 한다.")
        L.append("")

    # ── 날짜 클러스터 — 판정 단위는 건이 아니라 날짜다 ──
    L += ["", "## ⚠️ 날짜 단위로 보면 — 위 표가 무너진다", "",
          "위 건당 샤프는 **거래를 독립 표본으로 가정**한 값이다. 그런데 `c_kr_fallen_regime`은",
          "8,461건이 **86일**에 몰려 있다(하루 98건). 같은 날 거래는 같은 시장 충격을 공유하므로",
          "독립이 아니다. 우리 규율은 이미 \"판정 t는 날짜 클러스터로\"다.", "",
          "| arm | 건수 | 일수 | 건 평균 | **일 평균** | 일 샤프 | **날짜 t** | t≥2 필요 일수 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in recs:
        if r["sharpe"] <= 0:
            continue
        byday = defaultdict(list)
        for x in arms[r["sid"]]:
            byday[x["d"]].append(x["ex"])
        daily = [st.mean(v) for v in byday.values() if v]
        if len(daily) < 5:
            continue
        dm, ds = st.mean(daily), st.stdev(daily)
        dsh = dm / ds if ds else None
        dt = dm / (ds / math.sqrt(len(daily))) if ds else None
        dneed = f"{(2/dsh)**2:,.0f}일" if dsh and dsh > 0 else "**도달 불가**"
        L.append(f"| `{r['sid']}` | {r['n']:,} | {len(daily)} | {r['mean']:+.2f}% | "
                 f"**{dm:+.2f}%** | {dsh:+.3f} | **{dt:+.2f}** | {dneed} |")
    L += ["", "**건 가중 +2.37%가 일 가중 +0.31%로 7배 줄어든다.** 날짜 t는 0.40~0.50으로",
          "판정 근처도 아니다. 즉 양수 샤프는 **몇 개의 좋은 날에 거래가 몰린 결과**다.", ""]

    # ── arm 안에서 피처 칸으로 샤프를 올릴 수 있나 ──
    L += ["## arm 안에서 피처로 샤프를 올릴 수 있나", "",
          "**arm 밖 비교가 아니라 arm 안 개선**이다(모집단이 안 섞인다). "
          "날짜 안 3분위로 잘라 각 칸의 샤프를 본다.", "",
          "| arm | 원래 샤프 | 최고 칸 | 칸 샤프 | 칸 n | 개선 |",
          "|---|---:|---|---:|---:|---:|"]
    improved = 0
    for r in recs:
        if not r["feat"]:
            continue
        v = arms[r["sid"]]
        days = sorted({x["d"] for x in v})
        best = None
        for f in FEATURES:
            cells = {0: [], 2: []}
            for d in days:
                same = [x for x in v if x["d"] == d and isinstance(x["f"].get(f), (int, float))]
                if len(same) < 9:
                    continue
                same.sort(key=lambda x: x["f"][f])
                n = len(same)
                cells[0] += [x["ex"] for x in same[:n // 3]]
                cells[2] += [x["ex"] for x in same[-(n // 3):]]
            for k, lab in ((0, "하위"), (2, "상위")):
                if len(cells[k]) < MIN_CELL:
                    continue
                _, _, sh = sharpe(cells[k])
                if sh is not None and (best is None or sh > best[0]):
                    best = (sh, f, lab, len(cells[k]))
        if not best:
            continue
        gain = best[0] - r["sharpe"]
        if best[0] > 0:
            improved += 1
        L.append(f"| `{r['sid']}` | {r['sharpe']:+.3f} | `{best[1]}` {best[2]} | "
                 f"**{best[0]:+.3f}** | {best[3]:,} | {gain:+.3f} |")
    L += ["", f"**피처 칸으로 샤프가 양수가 된 arm {improved}개.**", "",
          "## 읽는 법", "",
          "- 샤프가 음수인 arm은 **표본을 쌓아도 양수로 확정되지 않는다.** '느리다'가 아니라",
          "  '그 방향으로는 판정 불가'다. 반대 방향(공매도)은 KR 개별주에서 개인이 실행 경로가 없다.",
          "- 기저선(`x*`)은 모집단이지 전략이 아니다. 후보(`c_*`)가 기저선보다 샤프가 높으면",
          "  **걸러내기가 값을 더한 것**이고, 낮으면 걸러내기가 오히려 깎은 것이다.",
          "- '현재 n'에 백필이 섞여 있다. 판정은 forward만으로 한다.", ""]

    out = ROOT / "docs" / "reports" / "sharpe_by_arm_20260919.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
