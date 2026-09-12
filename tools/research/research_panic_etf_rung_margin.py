#!/usr/bin/env python3
"""패닉 ETF — 문턱 사다리 **한계 칸**의 값 (read-only, 2026-09-12).

## 운영자 과제

*"1년에 10%면 너무 작잖아."* 맞다. 그런데 이 레인의 병목은 회당 수익이 아니라 **가동률**이다.
현행 v2는 **연 20.3회**만 일한다(2015~2025). 나머지 밤에 자본은 논다.
회당 +0.573%(연도매칭 초과, 비용 전)를 두 배로 만드는 길은 없고, **일하는 밤을 늘리는 것**이
유일하게 남은 안쪽 레버다.

## 과적합을 피하는 방법 — 최댓값을 고르지 않는다

문턱 격자를 전부 돌려 제일 좋은 칸을 집으면 그건 과적합이다. 그래서 **정확히 두 칸**만
미리 정해 묻는다. "현행 바로 아래 한 칸을 더 내려가면, 그 칸 **자체**가 비용 후 양수인가?"

  기준(base)    : 갭 ≤ −1.0%  또는  종가 ≤ −2.5%      ← 현행 v2, 건드리지 않는다
  한계 칸 ①     : 갭 ∈ (−1.0, −0.5]  이면서 base 아님
  한계 칸 ②     : 종가 ∈ (−2.5, −2.0] 이면서 base 아님

## 판정 규칙 — 숫자 보기 전에 고정

한 칸은 **아래를 전부** 만족할 때만 추가 후보가 된다.
  ① 비용(0.05%) 차감 후 한계 초과 > 0
  ② 전반기(2015~2020)에서 t ≥ 2
  ③ 후반기(2021~2025)에서 **부호 유지**(크기는 묻지 않는다)
하나라도 못 넘으면 기각이다. 세 조건은 "전반기에서 보이고 후반기에도 살아 있는가"를 묻는
분할 검정이고, 최댓값 탐색이 아니다.

⚠️ 2026은 뺀다 — 사전등록이 이미 2026을 **다른 국면**으로 분리했다(신호 연율 3.3배).
⚠️ **08-24 "문턱 완화는 틀린 처방"은 여기 적용되지 않는다.** 그건 개별주 급락 레인 얘기였고,
거기선 공급이 국면에 묶여 있어 문턱을 낮추면 사냥철이 아닌 날만 늘었다. 이 레인은 지수
한 종목이고 용량 제약이 없어서 메커니즘이 다르다. 그래도 결론은 데이터가 낸다.
⚠️ **라이브 `GAP_THR`은 건드리지 않는다.** 통과해도 쉐도우 사전등록 후보일 뿐이고,
라이브 파라미터 변경은 운영자 승인 + `.env.live`/start-config 두 소스 동시 변경 사항이다.

## 부록 — 2단 사이징이 얼마짜리인가

사전등록 §D(k 3등급)와 강신호(갭 ≤ −2)는 **이미 측정된 등급**이다. 여기서 새로 검정하지
않고, 같은 최대 노출에서 강한 밤에 크게 실었을 때 **연 수익률이 얼마나 달라지는지만**
환산한다. 판정이 아니라 가격표다 — forward 30건이 무엇을 사는지 운영자가 알아야 한다.
"""
from __future__ import annotations

import statistics as st
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.kr_index_etf_panic_shadow as P  # noqa: E402  (read-only)
from tools.research.research_panic_etf_year_decomp import t_stat, uncond_by_year  # noqa: E402

TK = "233740"
CUT_YEAR = "2026"                 # 사전등록이 분리한 국면 경계
HALF = "2021"                     # 전반 2015~2020 / 후반 2021~2025
COST = P.COST                     # 0.05% 왕복
T_BAR = 2.0
GAP_BASE, CLOSE_BASE = -1.0, -2.5
GAP_RUNG, CLOSE_RUNG = -0.5, -2.0


def in_base(c):
    g, x = c.get("kq_gap"), c.get("kq_chg")
    return (g is not None and g <= GAP_BASE) or (x is not None and x <= CLOSE_BASE)


def rungs():
    return [
        (f"① 갭 ∈ ({GAP_BASE}, {GAP_RUNG}]",
         lambda c: (not in_base(c)) and c.get("kq_gap") is not None
         and GAP_BASE < c["kq_gap"] <= GAP_RUNG),
        (f"② 종가 ∈ ({CLOSE_BASE}, {CLOSE_RUNG}]",
         lambda c: (not in_base(c)) and c.get("kq_chg") is not None
         and CLOSE_BASE < c["kq_chg"] <= CLOSE_RUNG),
    ]


def collect(cache, dates, by_year, pooled, cond):
    """(연도, 비용 후 초과) — 종가 매수 → 다음 시가 매도."""
    out = []
    for i in range(len(dates) - 1):
        y = dates[i][:4]
        if y >= CUT_YEAR or not cond(cache[dates[i]]):
            continue
        ca = cache[dates[i]].get(f"{TK}_close")
        ob = cache[dates[i + 1]].get(f"{TK}_open")
        if not ca or not ob:
            continue
        raw = (ob / ca - 1.0) * 100.0
        out.append((y, raw - by_year.get(y, pooled or 0.0) - COST))
    return out


def summarize(recs):
    if not recs:
        return None
    ys = sorted({r[0] for r in recs})
    pos = sum(1 for y in ys if st.mean(r[1] for r in recs if r[0] == y) > 0)
    return {"n": len(recs), "years": len(ys), "pos": pos,
            "mean": st.mean(r[1] for r in recs), "t": t_stat([r[1] for r in recs]),
            "per_year": len(recs) / len(ys), "total": st.mean(r[1] for r in recs) * len(recs) / len(ys)}


def fmt(s):
    if not s:
        return "| - | - | - | - | - | - |"
    t = f"{s['t']:+.2f}" if s["t"] is not None else "-"
    return (f"| {s['n']} | {s['per_year']:.1f} | {s['mean']:+.3f}% | {t} | "
            f"{s['pos']}/{s['years']} | **{s['total']:+.2f}%p** |")


def main():
    cache = P._read_cache()
    dates = sorted(cache)
    by_year = uncond_by_year(cache, TK)
    pooled = P._uncond_overnight(cache, TK)

    base = collect(cache, dates, by_year, pooled, in_base)
    bs = summarize(base)

    L = [f"# 패닉 ETF — 문턱 사다리 한계 칸의 값 ({date.today().isoformat()})", "",
         "read-only. `tools/research/research_panic_etf_rung_margin.py`(판정 규칙은 docstring).",
         f"{TK} · 2015~2025(2026 제외) · **연도매칭 초과, 비용 {COST}% 차감 후**.",
         "라이브 파라미터 변경 없음 — 통과해도 쉐도우 사전등록 후보다.", "",
         "운영자 과제는 *\"연 10%는 작다\"*이고, 이 레인의 병목은 회당이 아니라 **가동률**이다.",
         "현행은 연 20회만 일한다. 그래서 '문턱 바로 아래 한 칸'의 값을 따로 잰다.", "",
         "| 구간 | 건수 | 연 회수 | 회당 초과(비용후) | t | 양수연도 | **연 기여** |",
         "|---|---:|---:|---:|---:|---:|---:|",
         f"| **기준(현행 v2)** {fmt(bs)}"[:0] + f"| **기준(현행 v2)** {fmt(bs)[1:]}"]

    results = []
    for label, cond in rungs():
        recs = collect(cache, dates, by_year, pooled, cond)
        s = summarize(recs)
        L.append(f"| {label} {fmt(s)[1:]}")
        first = summarize([r for r in recs if r[0] < HALF])
        second = summarize([r for r in recs if r[0] >= HALF])
        results.append((label, s, first, second))

    L += ["", "## 분할 검정 — 전반기에서 보이고 후반기에도 살아 있는가", "",
          f"규칙(사전 고정): 비용 후 한계 초과 > 0 **그리고** 전반기 t ≥ {T_BAR} **그리고** 후반기 부호 유지.", "",
          "| 칸 | 전반 2015~2020 | t | 후반 2021~2025 | t | 판정 |",
          "|---|---:|---:|---:|---:|---|"]
    passed = []
    for label, s, a, b in results:
        ok = (s and s["mean"] > 0
              and a and a["t"] is not None and a["t"] >= T_BAR
              and b and b["mean"] > 0)
        if ok:
            passed.append((label, s))
        fa = f"{a['mean']:+.3f}%" if a else "-"
        ta = f"{a['t']:+.2f}" if a and a["t"] is not None else "-"
        fb = f"{b['mean']:+.3f}%" if b else "-"
        tb = f"{b['t']:+.2f}" if b and b["t"] is not None else "-"
        L.append(f"| {label} | {fa} | {ta} | {fb} | {tb} | {'**추가 후보**' if ok else '기각'} |")

    L += ["", "## 읽기", ""]
    if bs:
        L.append(f"- 현행 v2의 연 기여는 **{bs['total']:+.2f}%p**(연 {bs['per_year']:.1f}회 × "
                 f"회당 {bs['mean']:+.3f}%)다. 이게 '연 10%'의 정체다.")
    for label, s, a, b in results:
        L.append(f"- {label}: 연 {s['per_year']:.1f}회 추가, 연 기여 **{s['total']:+.2f}%p** "
                 f"(회당 {s['mean']:+.3f}%, t {s['t']:+.2f})" if s else f"- {label}: 표본 없음")
    if passed:
        add = sum(s["total"] for _, s in passed)
        L.append(f"- **통과한 칸의 합: 연 {add:+.2f}%p** → 현행 {bs['total']:+.2f}%p 대비 "
                 f"{100 * add / bs['total']:+.0f}%. 66만 기준 월 "
                 f"{660000 * add / 100 / 12 * (1 - 0.154):,.0f}원 추가(세후).")
    else:
        L.append("- **통과한 칸 없음.** 문턱을 내려 가동률을 올리는 길은 이 데이터에서 닫혔다. "
                 "연 10%는 이 레인 안쪽에서는 상한에 가깝고, 상향은 **자본 규모**나 "
                 "**다른 레인**에서 찾아야 한다.")

    # ── 부록: 2단 사이징 가격표 (새 검정 아님, 이미 측정된 등급의 환산) ──
    def is_strong(c):
        return in_base(c) and c.get("kq_gap") is not None and c["kq_gap"] <= P.GAP_STRONG

    strong = collect(cache, dates, by_year, pooled, is_strong)
    weak = collect(cache, dates, by_year, pooled, lambda c: in_base(c) and not is_strong(c))
    assert len(strong) + len(weak) == len(base), "강/약 분해가 기준 집합과 안 맞는다"
    ss, ws = summarize(strong), summarize(weak)
    L += ["", "## 부록 — 2단 사이징은 얼마짜리인가", "",
          f"강신호 = 갭 ≤ {P.GAP_STRONG}%(사전등록된 등급). 새 검정이 아니라 환산이다.", "",
          "| 등급 | 건수 | 연 회수 | 회당 초과(비용후) | t | **연 기여(1단위)** |",
          "|---|---:|---:|---:|---:|---:|",
          f"| 강신호(갭≤{P.GAP_STRONG}) {fmt(ss)[1:]}",
          f"| 약신호 {fmt(ws)[1:]}"]
    if ss and ws:
        flat = bs["total"]
        # 강한 밤에 3배를 실으면: 기여는 3×강 + 1×약, 평균 배치자본은 (3n_s+n_w)/(n_s+n_w) 단위
        tier_contrib = 3 * ss["total"] + ws["total"]
        avg_units = (3 * ss["n"] + ws["n"]) / (ss["n"] + ws["n"])
        per_cap = tier_contrib / avg_units
        L += ["",
              f"- 전액 평탄(현행 가정): 자본당 연 **{flat:+.2f}%p**. 최악 1박 −12.96%.",
              f"- 강한 밤 3배·약한 밤 1배: 연 {tier_contrib:+.2f}%p, 평균 배치 {avg_units:.2f}단위 → "
              f"**자본당 {per_cap:+.2f}%p** (평탄 대비 **{per_cap - flat:+.2f}%p**).",
              f"  대가는 **최악 1박이 −12.96% → 약 −{12.96 * 3:.0f}%**다(강신호 밤에만).",
              "",
              "**읽기 — 이게 '연 10%'를 올리는 유일한 안쪽 레버다.**",
              "1박 보유라 밤이 겹치지 않아서, 모든 신호에 이미 전액을 넣을 수 있다. 따라서",
              "사이징으로 수익을 올리려면 **강한 밤에 평소 한도를 넘어서** 실어야 한다.",
              "그건 사이징 튜닝이 아니라 **리스크 한도 결정**이고 운영자 몫이다.",
              "거래는 연 3.2회뿐이라 forward로 이걸 확인하려면 여러 해가 걸린다 —",
              "판단 근거는 백필과 한도 감수 여부이지 forward 표본이 아니다."]

    out = ROOT / "docs" / "reports" / f"panic_etf_rung_margin_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
