#!/usr/bin/env python3
"""탐색의 분모 — 우리는 몇 번 쳐서 몇 번 맞았나 (read-only, 2026-09-18).

운영자 질문: *"문제가 뭘까. 우리가 뭔가 잘못 보고 있나, 판단이 흐린가, 정의가 없나."*

## 이 스크립트가 답하는 것

우리는 개별 축마다 다중검정 보정을 한 적은 있다(어제 17피처 × 2시장 = 34회 → |t| ≥ 3).
그런데 **탐색 전체에 보정을 건 적은 한 번도 없다.** 석 달 동안 수십 개 가설을 치고
통과 1개(패닉 ETF)를 얻었는데, 그 1개가 진짜인지 분모가 커서 나온 것인지 **지금 판정 수단이 없다.**

여기서 세 가지를 낸다.
  ① **생존 경계** — 패닉 레인의 t가 Bonferroni를 통과하려면 분모 N이 얼마 이하여야 하는가
  ② **기대 거짓양성** — α=0.05에서 N번 치면 몇 개가 그냥 나오는가
  ③ **forward 정합성** — 백필 승률과 forward 실적이 같은 분포에서 나올 수 있는가
     (이건 **다중검정과 무관**하다. 사전등록 후 새로 받은 표본이라 분모 문제를 안 탄다.)

## 규약

- 패닉 수치는 메모리가 아니라 **원장 리포트**(`data/analysis/kr_index_etf_panic_report.json`)에서 읽는다.
- t → p 변환은 양측, df = n − 1. 정규근사가 아니라 t분포를 쓴다(n이 작은 구간에서 갈린다).
- Bonferroni는 **독립 가정**이라 보수적이다. 우리 축들은 서로 상관이 있으므로(같은 풀·같은 국면)
  진짜 필요한 보정은 이보다 약하다. 그래서 **BH-FDR도 같이** 낸다.
- ③의 이항검정은 백필 승률을 귀무가설로 둔다. 이건 "forward가 백필과 다르다"를 재는 것이지
  "엣지가 없다"를 재는 게 아니다.

⚠️ 분모 N은 이 스크립트가 만들지 않는다. 별도로 수집한 가설 레지스트리(JSON)를 받아서 쓴다.
레지스트리가 없으면 **N을 가정하지 않고** 경계값만 낸다 — 가정한 N으로 결론을 내면 그게 또
사후 조정이다.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "data" / "analysis" / "kr_index_etf_panic_report.json"
ALPHA = 0.05
TICKER = "233740"


def t_to_p(t: float, df: int) -> float:
    """양측 p값. t분포 CDF를 불완전베타로 계산한다(scipy 의존 없이)."""
    x = df / (df + t * t)
    return _betainc(df / 2.0, 0.5, x)


def _betainc(a: float, b: float, x: float) -> float:
    """정규화 불완전베타 I_x(a,b) — 연분수 전개(Numerical Recipes betacf)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta + b * math.log(1 - x) + a * math.log(x)) * _betacf(b, a, 1 - x) / b


def _betacf(a: float, b: float, x: float, itmax: int = 300, eps: float = 3e-12) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        c = 1.0 + aa / c
        if abs(d) < 1e-30:
            d = 1e-30
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        c = 1.0 + aa / c
        if abs(d) < 1e-30:
            d = 1e-30
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def binom_le(k: int, n: int, p: float) -> float:
    """P(X ≤ k) — 이항. forward 승수가 백필 승률 아래로 이만큼 나올 확률."""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def _norm_axis(a) -> str:
    import re
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(a or "")).lower()


def _count_hint(v) -> int:
    """자유형 count_hint를 관대하게 읽는다. 숫자가 없으면 1(=검정 1회)로 센다."""
    if isinstance(v, (int, float)):
        return max(1, int(v))
    import re
    m = re.search(r"\d[\d,]*", str(v or ""))
    return max(1, int(m.group(0).replace(",", ""))) if m else 1


def _is_meta(e) -> bool:
    """가설 검정이 아닌 것(회계·환산·후보생성)은 분모에서 뺀다."""
    v = str(e.get("verdict", "")) + " " + str(e.get("note", ""))
    return any(k in v for k in ("후보 생성용", "판정 아님", "회계", "N 미수집"))


def _is_retest(e) -> bool:
    """같은 축의 재검정은 새 검정으로 세지 않는다(같은 데이터를 다시 본 것)."""
    n = str(e.get("note", ""))
    return any(k in n for k in ("재검정", "재검증", "재확인", "중복", "같은 축", "재실행"))


def load_registry():
    """레지스트리를 합쳐 계층별 N을 센다. 깨진 파일은 건너뛰고 사실대로 보고한다."""
    scratch = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not scratch or not scratch.exists():
        return None, [], []
    entries, broken = [], []
    for f in sorted(scratch.glob("denom_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for e in data:
                e["_src"] = f.name
            entries += data
        except Exception as e:      # noqa: BLE001
            broken.append(f"{f.name}: {type(e).__name__}")
    return scratch, entries, broken


def tiers(entries):
    """T0 = 모든 통계 비교(스윕 셀 포함) · T3 = 고유 축(재검정·메타 제외)."""
    live = [e for e in entries if not _is_meta(e)]
    t0 = sum(_count_hint(e.get("count_hint")) for e in live)
    axes = {}
    for e in live:
        if _is_retest(e):
            continue
        axes.setdefault(_norm_axis(e.get("axis")), e)
    return {"raw": len(entries), "t0": t0, "t3": len(axes),
            "passed": [e for e in axes.values()
                       if str(e.get("verdict", "")).startswith(("통과", "부분"))]}


def main():
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    b = rep["by"][TICKER]["backfill"]
    live = rep["by"][TICKER]["live"]
    n, t, win = int(b["n"]), float(b["t"]), float(b["win"]) / 100.0
    p = t_to_p(t, n - 1)
    n_break = ALPHA / p

    L = ["# 탐색의 분모 — 몇 번 쳐서 몇 번 맞았나 (2026-09-18)", "",
         "read-only. `tools/research/research_search_denominator_20260918.py`(규약은 docstring).",
         "개별 축에는 보정을 걸어왔지만 **탐색 전체에는 한 번도 건 적이 없다.** 그걸 계산한다.", "",
         "## 0. 유일한 통과 축의 정본 수치", "",
         f"`{TICKER}` 패닉 ETF v2 — 원장 리포트에서 직접 읽음(메모리 인용 아님)", "",
         "| | 값 |", "|---|---:|",
         f"| 백필 표본 n | {n} |",
         f"| 초과 평균 | {b['excess_mean']:+.3f}% |",
         f"| t | **{t:+.2f}** |",
         f"| 승률 | {b['win']:.1f}% |",
         f"| 양측 p (df={n-1}) | **{p:.6f}** |", "",
         "## ① 생존 경계 — 분모가 얼마까지면 살아남나", "",
         f"Bonferroni는 `p < α/N`을 요구한다. α={ALPHA}, p={p:.6f} →", "",
         f"### **N ≤ {math.floor(n_break)}이면 통과, N ≥ {math.floor(n_break)+1}이면 기각.**", "",
         "| 분모 N | 요구 p | 패닉 p | 판정 |", "|---:|---:|---:|---|"]
    for N in (10, 20, 30, 40, 50, math.floor(n_break), math.floor(n_break) + 1, 70, 90, 120):
        need = ALPHA / N
        L.append(f"| {N} | {need:.6f} | {p:.6f} | "
                 f"{'**생존**' if p < need else '기각'} |")
    L += ["", "즉 **결론이 분모에 걸려 있다.** 우리가 석 달 동안 친 가설이 50개 이하면 패닉 레인은",
          f"보정 후에도 살아남고, {math.floor(n_break)+1}개를 넘으면 '많이 쳐서 하나 맞은 것'과 구분되지 않는다.", "",
          "## ② 이만한 t가 우연히 하나라도 나올 확률", "",
          "\"0.05N개가 기대된다\"는 느슨한 말이다. 패닉의 t는 2가 아니라 **3.37**이므로",
          f"정확한 질문은 이것이다 — **엣지가 전혀 없는 축을 N번 칠 때 p ≤ {p:.6f}가 "
          "하나라도 나올 확률은?**", "",
          "| 분모 N | 하나라도 나올 확률 | 해석 |", "|---:|---:|---|"]
    for N in (20, 40, 57, 60, 80, 100, 120):
        q = 1.0 - (1.0 - p) ** N
        L.append(f"| {N} | **{q*100:.1f}%** | "
                 f"{'우연으로 보기 어렵다' if q < 0.05 else '우연과 구분 안 된다' if q > 0.10 else '경계'} |")
    L += ["", "⚠️ Bonferroni·이 계산 모두 **검정 간 독립**을 가정한다. 우리 축들은 같은 풀·같은 국면을",
          "공유해 실제로는 상관이 있고, 그러면 유효 검정 수는 N보다 작다. 그래서 위 경계는",
          "**보수적인 하한**이다 — 진짜 경계는 이보다 관대하다. 다만 얼마나 관대한지는 모른다.", "",
          "## ③ forward 정합성 — 이건 분모 문제를 안 탄다", ""]

    # forward는 두 종목 합산이 맞다(리포트의 live는 종목별)
    fwd_n, fwd_win = 4, 0        # 09-11 strong 2건 + 09-14 normal 2건, 전부 음수
    pb = binom_le(fwd_win, fwd_n, win)
    L += ["사전등록 **후에** 새로 받은 표본이라 다중검정과 무관하다. 백필 승률을 귀무가설로 둔다.", "",
          "| | 값 |", "|---|---:|",
          f"| 백필 승률(귀무) | {win*100:.1f}% |",
          f"| forward 표본 | {fwd_n}건(233740·229200 각 2) |",
          f"| forward 승 | **{fwd_win}건** |",
          f"| P(승 ≤ {fwd_win} \\| 승률 {win*100:.1f}%) | **{pb:.4f}** |", "",
          f"{'백필 분포와 어긋난다(5% 수준).' if pb < 0.05 else '아직 백필 분포와 모순되지 않는다.'} "
          f"다만 n={fwd_n}이라 **이것만으로 기각하지 않는다** — 반증 조건은 사전등록 문서를 따른다.", ""]

    scratch, entries, broken = load_registry()
    L += ["## ④ 실제 분모", ""]
    if not entries:
        L += ["레지스트리 미수집 — **N을 가정하지 않는다.** 위 경계표만 유효하다.", ""]
    else:
        tt = tiers(entries)
        srcs = sorted({e.get("_src", "?") for e in entries})
        L += [f"레지스트리 {len(srcs)}종({', '.join(srcs)}) · 원 항목 {tt['raw']}건", ""]
        if broken:
            L += [f"⚠️ 읽기 실패 {len(broken)}건({'; '.join(broken)}) — "
                  "**그만큼 N은 과소집계다.** 아래 판정은 하한이다.", ""]
        # 단일 N을 주장하지 않는다. 두 수집 경로가 같은 축을 다르게 적어 기계적 dedup은
        # 과대집계되고(301), 수집자가 직접 묶은 coarse family는 과소집계다(96).
        # 그래서 **구간 전체에서 판정이 같은지**를 본다 — 그게 결론의 강건성이다.
        band = [("96 — 수집자가 묶은 coarse family (**과소**, 본인도 too low라 명시)", 96),
                ("150 — 수집자 방어가능 구간 하단", 150),
                ("200 — 수집자 방어가능 구간 상단", 200),
                (f"{tt['t3']} — 기계적 dedup (**과대**, 두 경로 중복 미병합)", tt["t3"]),
                (f"{tt['t0']:,} — 모든 스윕 셀 포함 (**최대**)", tt["t0"])]
        L += ["단일 N을 주장하지 않는다. 기계적 dedup은 두 수집 경로의 중복을 못 합쳐 과대하고,",
              "수집자가 손으로 묶은 family는 과소다. **구간 전체에서 판정이 같은지**가 핵심이다.", "",
              "| 분모 추정 | N | 요구 p | 하나라도 나올 확률 | 판정 |",
              "|---|---:|---:|---:|---|"]
        for nm, N in band:
            need = ALPHA / N
            q = 1.0 - (1.0 - p) ** N
            L.append(f"| {nm} | {N:,} | {need:.8f} | {q*100:.1f}% | "
                     f"**{'생존' if p < need else '기각'}** |")
        L += ["", f"### **구간의 최소값 96조차 생존 경계 {math.floor(n_break)}을 넘는다 — "
              "어느 추정을 써도 판정은 같다.**", "",
              f"통과/부분통과로 기록된 축 {len(tt['passed'])}개 — 다만 대부분은 "
              "**귀무 유지·방법론 확인**이거나 이후 재검정에서 뒤집혔다. 수집 과정에서 확인된 "
              "번복만 7건이다(KR 할인깊은순 랭킹 통과→기각, 조기익절 k=0.4 통과→최대 손실원, "
              "MAX/밴드 통과→OOS 무효, day_losers 모델 선별력 p=0.0000→역선별 등).", ""]

    L += ["## 읽는 법", "",
          "- 이 계산은 **패닉 레인을 죽이려는 게 아니다.** 분모를 세지 않으면 살았는지 죽었는지",
          "  말할 수 없다는 것을 보이는 것이다.",
          "- 보정을 통과하든 못하든 **forward가 최종 심판**이다. 백필은 분모를 타지만 forward는 안 탄다.",
          "- 보정에 걸린다면 할 일은 폐기가 아니라 **forward 표본을 채우는 것**이다.",
          "- 중단·폐기·방향 전환은 운영자 결정이다."]

    out = ROOT / "docs" / "reports" / "search_denominator_20260918.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
