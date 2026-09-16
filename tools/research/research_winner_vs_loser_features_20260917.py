#!/usr/bin/env python3
"""수익 난 종목 vs 마이너스 종목 — 무엇이 가르는가 (read-only, 2026-09-17).

운영자 질문: *"수익이 난 종목이랑 수익이 마이너스인 종목들이랑 차이는 뭐냐"*

## 왜 7건이 아니라 15만건으로 답하는가

이번 주 만기 도달분은 **7종목(양수 3 / 음수 4)**뿐이다. 7건에서 "승자는 거래대금이 작더라"
같은 말은 무조건 만들어진다 — 17개 피처 × 7행이면 우연히 갈리는 축이 반드시 나온다.
그래서 같은 질문을 **기저선 모집단 전체**(조건 전량 매수, 9개 arm, 152,698건, 333거래일)에
그대로 던진다. 기저선은 후보를 뽑아낸 denominator라 "무엇이 승패를 가르나"를 물을
올바른 자리다(후보 arm은 이미 걸러진 뒤라 분포가 잘려 있다).

## 규약 — 이걸 안 지키면 없는 축이 생긴다

**① 날짜 안에서 3분위로 나눈다.** 전체를 통으로 3분위로 자르면 "거래대금 큰 날"과
"거래대금 큰 종목"이 섞여, 국면이 피처인 척한다. 같은 날 안에서만 비교한다.

**② t는 날짜 클러스터로 낸다.** 종목 단위로 세면 t가 5배 부풀고, 하루에 수백 종목이
같은 시장 충격을 공유하므로 독립 표본이 아니다.

**③ 판정은 지수 초과분**(KR=069500 / US=IWM, 창=arm 계약 보유기간). 강세장에서
원 net으로 승패를 가르면 전부 "지수가 올랐다"로 수렴한다. 원 net도 같이 싣되 판정은 초과로.

**④ 다중검정 보정.** 17피처 × 2시장 = 34회 검정이다. |t|≥2는 34회 중 1~2번은 그냥 나온다.
**통과선은 |t| ≥ 3.0**으로 올린다(대략 Bonferroni 5%). 2~3 구간은 "미달"로 적는다.

⚠️ 이 질문은 이미 두 번 닫힌 적 있다 — 전면 변수 스윕 19축 중 3개만 생존(08-20, 재론 금지 16축),
걸러내기 특성/arm 필터 전면 기각(09-10). 여기서 새 축이 나오면 **그때 기각된 것과 같은 축인지**
먼저 확인해야 한다. 이 스크립트는 판정이 아니라 재확인이다.
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
T_BAR = 3.0          # 34회 검정 보정 통과선
MIN_DAYS = 60        # 날짜 클러스터 최소

FEATURES = ["chg", "gap", "dvol", "dvol20", "vol_spike", "ibs", "rv20", "mom20",
            "from_high20", "ma20_disc", "cum5", "min1_in5", "ret60", "max21",
            "hi_break_n", "down_streak", "price"]
LABEL = {"chg": "전일 등락", "gap": "갭", "dvol": "거래대금", "dvol20": "20일 평균 거래대금",
         "vol_spike": "거래량 급증(=dvol/dvol20)", "ibs": "IBS(종가 위치)",
         "rv20": "20일 실현변동성", "mom20": "20일 모멘텀", "from_high20": "20일 고점 대비",
         "ma20_disc": "MA20 이격", "cum5": "5일 누적", "min1_in5": "5일 중 최악 하루",
         "ret60": "60일 수익률", "max21": "21일 최대 일간상승(복권성)",
         "hi_break_n": "신고가 돌파일수", "down_streak": "연속 하락일", "price": "주가"}

BARS = {"KR": bench_bars("kr", "069500"), "US": bench_bars("us", "IWM")}
IDX = {m: {b[0]: i for i, b in enumerate(v)} for m, v in BARS.items()}


def cluster_t(by_day: dict[str, list[float]]):
    """날짜별 평균의 1표본 t. 종목이 아니라 **날짜**가 관측 단위다."""
    m = [st.mean(v) for v in by_day.values() if v]
    if len(m) < 3 or st.stdev(m) == 0:
        return None, len(m)
    return st.mean(m) / (st.stdev(m) / math.sqrt(len(m))), len(m)


def paired_cluster_t(pairs: list[float]):
    """날짜별 (상위3분위 − 하위3분위) 차이의 t. 같은 날 안에서 짝지었으므로 대응표본이다."""
    if len(pairs) < 3 or st.stdev(pairs) == 0:
        return None
    return st.mean(pairs) / (st.stdev(pairs) / math.sqrt(len(pairs)))


def load():
    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True)
    rows = con.execute(
        "SELECT strategy_id, ticker, session_date, net_pct, meta FROM trades "
        "WHERE status='CLOSED' AND net_pct IS NOT NULL "
        "AND (strategy_id LIKE 'xkr%' OR strategy_id LIKE 'xus%')").fetchall()
    con.close()
    out = []
    for sid, tk, d, net, meta in rows:
        feat = (json.loads(meta or "{}").get("feat") or {})
        if "rv20" not in feat:
            continue
        mk = "KR" if sid.startswith("xkr") else "US"
        hold = HOLDS.get(sid, HOLD_SESSIONS)
        i = IDX[mk].get(d)
        if i is None or i + hold > len(BARS[mk]):
            continue
        bench = (BARS[mk][i + hold - 1][4] / BARS[mk][i][1] - 1) * 100
        out.append({"mk": mk, "d": d, "tk": tk, "net": net, "ex": net - bench, "f": feat})
    return out


def main():
    data = load()
    L = ["# 수익 난 종목 vs 마이너스 종목 — 무엇이 가르는가 (2026-09-17)", "",
         "read-only. `tools/research/research_winner_vs_loser_features_20260917.py`(규약은 docstring).",
         "**날짜 안 3분위 · 날짜 클러스터 t · 지수 초과 기준 · 통과선 |t|≥3.0**(34회 검정 보정).", ""]

    for mk in ("KR", "US"):
        rows = [r for r in data if r["mk"] == mk]
        days = sorted({r["d"] for r in rows})
        win = [r for r in rows if r["net"] > 0]
        los = [r for r in rows if r["net"] <= 0]
        L += [f"## {mk} — {len(rows):,}건 / {len(days)}거래일", "",
              f"원 net 양수 **{len(win):,}건**({len(win)/len(rows)*100:.1f}%) · "
              f"음수 {len(los):,}건. 초과 양수 "
              f"{sum(1 for r in rows if r['ex'] > 0):,}건"
              f"({sum(1 for r in rows if r['ex'] > 0)/len(rows)*100:.1f}%).", ""]

        # ── ① 단순 비교: 승자 평균 vs 패자 평균 (운영자 질문 그대로) ──
        L += ["### ① 승자와 패자의 피처 평균 — 질문 그대로", "",
              "| 피처 | 승자 평균 | 패자 평균 | 차이 | 판정 |", "|---|---:|---:|---:|---|"]
        simple = []
        for f in FEATURES:
            wv = [r["f"][f] for r in win if isinstance(r["f"].get(f), (int, float))]
            lv = [r["f"][f] for r in los if isinstance(r["f"].get(f), (int, float))]
            if len(wv) < 100 or len(lv) < 100:
                continue
            simple.append((f, st.mean(wv), st.mean(lv)))
        for f, w, l in simple:
            diff = w - l
            rel = abs(diff) / (abs(l) + 1e-9)
            L.append(f"| {LABEL[f]} `{f}` | {w:,.2f} | {l:,.2f} | {diff:+,.2f} | "
                     f"{'차이 큼(>20%)' if rel > 0.2 else '사실상 동일'} |")
        L += ["", "⚠️ **이 표로 결론 내면 안 된다.** 사후에 승패로 나눈 뒤 평균을 비교하는 건",
              "\"오른 종목은 오르는 성질이 있었다\"를 확인하는 것에 가깝다. 진짜 질문은",
              "**진입 시점에 보이는 값으로 미리 갈라지느냐**이고, 그건 아래 ②다.", ""]

        # ── ② 날짜 안 3분위 — 사전에 가를 수 있는가 ──
        L += ["### ② 날짜 안 3분위 — 진입 시점 값으로 미리 갈라지는가", "",
              "| 피처 | 하위1/3 초과 | 중위 | 상위1/3 초과 | 상−하 | t(날짜) | 일수 | 판정 |",
              "|---|---:|---:|---:|---:|---:|---:|---|"]
        results = []
        for f in FEATURES:
            terc = {0: defaultdict(list), 1: defaultdict(list), 2: defaultdict(list)}
            diffs = []
            for d in days:
                same = [r for r in rows if r["d"] == d
                        and isinstance(r["f"].get(f), (int, float))]
                if len(same) < 9:          # 3분위가 의미 있으려면 최소 3개씩
                    continue
                same.sort(key=lambda r: r["f"][f])
                n = len(same)
                lo, hi = same[:n // 3], same[-(n // 3):]
                mid = same[n // 3:-(n // 3)]
                terc[0][d] = [r["ex"] for r in lo]
                terc[1][d] = [r["ex"] for r in mid]
                terc[2][d] = [r["ex"] for r in hi]
                diffs.append(st.mean(r["ex"] for r in hi) - st.mean(r["ex"] for r in lo))
            if len(diffs) < MIN_DAYS:
                continue
            means = [st.mean([v for vs in terc[k].values() for v in vs]) for k in (0, 1, 2)]
            t = paired_cluster_t(diffs)
            results.append({"f": f, "lo": means[0], "mid": means[1], "hi": means[2],
                            "diff": st.mean(diffs), "t": t, "days": len(diffs)})
        results.sort(key=lambda r: -abs(r["t"] or 0))
        for r in results:
            t = r["t"]
            v = ("**통과**" if t is not None and abs(t) >= T_BAR
                 else "미달(2~3)" if t is not None and abs(t) >= 2 else "무차별")
            L.append(f"| {LABEL[r['f']]} `{r['f']}` | {r['lo']:+.2f}% | {r['mid']:+.2f}% | "
                     f"{r['hi']:+.2f}% | {r['diff']:+.2f}%p | "
                     f"{t:+.2f} | {r['days']} | {v} |" if t is not None else
                     f"| {LABEL[r['f']]} `{r['f']}` | - | - | - | - | - | {r['days']} | - |")
        passed = [r for r in results if r["t"] is not None and abs(r["t"]) >= T_BAR]
        near = [r for r in results if r["t"] is not None and 2 <= abs(r["t"]) < T_BAR]
        L += ["", f"**{mk} 통과 {len(passed)}축 · 미달(2~3) {len(near)}축 · 검정 {len(results)}축.**", ""]
        if passed:
            for r in passed:
                L.append(f"- **{LABEL[r['f']]}**: 상위1/3이 하위1/3보다 초과 "
                         f"{r['diff']:+.2f}%p (t {r['t']:+.2f}, {r['days']}일)")
            L.append("")

    # ── ③ arm별 재확인 — 섞어 재면 다른 모집단이 축인 척한다 ──
    L += ["## ③ arm별 재확인 — KR 통과축은 어느 모집단의 성질인가", "",
          "②의 KR은 기저선 4 arm(급락·상승·돌파·거래량급증)을 **합쳐서** 쟀다. 이 arm들은 서로",
          "다른 모집단이라, 합친 3분위의 상위가 사실은 '한 arm이 통째로 상위'일 수 있다.",
          "**라이브 레인의 모집단은 `xkr_fallen3`(급락)이다** — 거기서도 살아 있어야 쓸 수 있다.", "",
          "| 피처 | arm | 상−하 초과 | t(날짜) | 일수 | 판정 |", "|---|---|---:|---:|---:|---|"]
    kr_pass = ["max21", "rv20", "price", "cum5", "vol_spike", "down_streak"]
    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True)
    arm_rows = defaultdict(list)
    for sid, tk, d, net, meta in con.execute(
            "SELECT strategy_id, ticker, session_date, net_pct, meta FROM trades "
            "WHERE status='CLOSED' AND net_pct IS NOT NULL AND strategy_id LIKE 'xkr%'"):
        feat = (json.loads(meta or "{}").get("feat") or {})
        if "rv20" not in feat:
            continue
        hold = HOLDS.get(sid, HOLD_SESSIONS)
        i = IDX["KR"].get(d)
        if i is None or i + hold > len(BARS["KR"]):
            continue
        bench = (BARS["KR"][i + hold - 1][4] / BARS["KR"][i][1] - 1) * 100
        arm_rows[sid].append({"d": d, "ex": net - bench, "f": feat})
    con.close()

    for f in kr_pass:
        for sid in sorted(arm_rows):
            rows = arm_rows[sid]
            diffs = []
            for d in sorted({r["d"] for r in rows}):
                same = [r for r in rows if r["d"] == d
                        and isinstance(r["f"].get(f), (int, float))]
                if len(same) < 9:
                    continue
                same.sort(key=lambda r: r["f"][f])
                n = len(same)
                diffs.append(st.mean(r["ex"] for r in same[-(n // 3):])
                             - st.mean(r["ex"] for r in same[:n // 3]))
            if len(diffs) < MIN_DAYS:
                L.append(f"| {LABEL[f]} | `{sid}` | - | - | {len(diffs)} | 일수 부족 |")
                continue
            t = paired_cluster_t(diffs)
            v = ("**유지**" if t is not None and abs(t) >= T_BAR
                 else "미달(2~3)" if t is not None and abs(t) >= 2 else "**사라짐**")
            L.append(f"| {LABEL[f]} | `{sid}` | {st.mean(diffs):+.2f}%p | "
                     f"{t:+.2f} | {len(diffs)} | {v} |" if t is not None else
                     f"| {LABEL[f]} | `{sid}` | - | - | {len(diffs)} | - |")
    L += ["", "**`xkr_fallen3`에서 사라지는 축은 라이브 레인에 못 쓴다** — 다른 모집단의 성질이다.", ""]

    L += ["## 읽는 법", "",
          "- ①은 **사후 분류**다. 승패로 가른 뒤 평균을 비교하면 항상 차이가 나온다 — 근거가 아니다.",
          "- ②가 진짜 질문이다. **진입 시점 값으로 미리 갈라지는가.**",
          "- 통과선을 |t|≥3으로 잡은 건 34회 검정이기 때문이다. |t| 2~3은 우연으로 나온다.",
          "- 여기서 통과한 축이 있어도 **08-20 스윕 16축 / 09-10 걸러내기 기각과 같은 축인지**",
          "  대조하기 전에는 신규 발견이 아니다."]

    out = ROOT / "docs" / "reports" / "winner_vs_loser_features_20260917.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
