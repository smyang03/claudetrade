#!/usr/bin/env python3
"""코어 잔차의 정체 — 실력인가 강세장 베타인가 (read-only, 2026-09-19).

## 왜

중립 벤치로 재보니 `ew_band8`만 정적 방치를 이겼다(+6.4%p). 마찰을 빼면
**타이밍 잔차 +9.9%p**다. 그런데 측정 구간 2023-01~2026-09는 KOSPI200이 +260% 오른
극단 강세장이다. 이 구간에서 "덜 팔아서 추세를 탔다"와 "타이밍이 좋았다"는
**구분이 안 된다.** 자본의 대부분이 걸린 자리라 여기가 지금 가장 비싼 미검정이다.

## 어떻게 가르나 — 사후 국면 라벨은 쓰지 않는다

메모리에 박혀 있다: *past-only 국면 게이트는 4패널 전부 역효과(사후 라벨과 혼용 금지)*.
"하락장이었던 구간"을 사후에 오려내면 그 자체가 편향이다. 그래서 두 가지만 쓴다.

**① 달력 분할** — 월/연 단위는 외생이라 편향이 없다. 각 구간에서 잔차 부호를 본다.

**② 잔차 대 시장수익 회귀** — 이게 결정적이다. 월별 잔차를 그 달의 정적 벤치 수익에
회귀한다. **기울기가 유의하게 양수면 그 '엣지'는 시장 베타의 변형**이다
(오를 때 더 들고 있어서 더 번 것). 기울기가 0에 가깝고 절편이 양수여야 실력이다.

  잔차_t = α + β × 정적수익_t

  · β > 0 → 상승장에서만 버는 구조. 강세 구간 산물.
  · β ≈ 0, α > 0 → 국면과 무관한 초과. 이게 실력의 모양이다.
  · β < 0 → 방어형(하락장에서 덜 잃는다).

**③ 상승월 vs 하락월** — 정적 벤치가 음수였던 달만 모아 잔차를 본다. 사후 라벨이 아니라
**대조군 자신의 부호**로 나누는 것이라 arm 정보를 쓰지 않는다.

## 규약

- arm nav는 원장에서, 정적 벤치는 같은 날짜로 샘플링. 수익률은 **구간 수익**(월간)으로 낸다.
- 표본이 작다(월 ~44개). t는 내되 **|t| ≥ 2를 통과선으로 쓰지 않는다** — 여기서 하는 건
  판정이 아니라 **귀속**이다. 부호와 크기를 본다.
"""
from __future__ import annotations

import csv
import json
import math
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "analysis" / "core_etf_prices.csv"
LEDGER = ROOT / "data" / "shadow" / "core_etf_book.jsonl"
REPORT = ROOT / "data" / "analysis" / "core_etf_book_report.json"

UNIVERSE = ["379810", "360750", "411060", "305080", "069500"]
CAPITAL = 2_600_000.0
FEE_SIDE = 0.00015
ARMS = ("ew", "absmom", "ew_band8")


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_prices():
    out = {}
    with CACHE.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            px = {t: f(r.get(t)) for t in UNIVERSE}
            if all(v and v > 0 for v in px.values()):
                out[r["date"]] = px
    return out


def static_nav(prices, dates):
    d0 = dates[0]
    qty, cash = {}, CAPITAL
    for t in UNIVERSE:
        q = int(math.floor((CAPITAL / len(UNIVERSE)) / (prices[d0][t] * (1 + FEE_SIDE))))
        qty[t] = q
        cash -= q * prices[d0][t] * (1 + FEE_SIDE)
    return {d: cash + sum(qty[t] * prices[d][t] for t in UNIVERSE) for d in dates}


def arm_navs():
    out = defaultdict(dict)
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") == "mtm" and r.get("arm") and r.get("date"):
            v = f(r.get("nav"))
            if v and v > 0:
                out[r["arm"]][r["date"]] = v
    return out


def ols(xs, ys):
    """단순 회귀 (기울기, 절편, 기울기 t, R²)."""
    n = len(xs)
    if n < 5:
        return None
    mx, my = st.mean(xs), st.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    resid = [y - (a + b * x) for x, y in zip(xs, ys)]
    sse = sum(r * r for r in resid)
    if n <= 2 or sse == 0:
        return b, a, None, 1.0
    se = math.sqrt(sse / (n - 2) / sxx)
    sst = sum((y - my) ** 2 for y in ys)
    return b, a, (b / se if se else None), (1 - sse / sst if sst else None)


def main():
    prices = load_prices()
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    start = min(v["start"] for v in rep["arms"].values())
    dates = sorted(d for d in prices if d >= start)
    snav = static_nav(prices, dates)
    arms = arm_navs()

    L = [f"# 코어 잔차의 정체 — 실력인가 강세장 베타인가 ({start} ~ {dates[-1]})", "",
         "read-only. `tools/research/research_core_residual_regime_20260919.py`(규약은 docstring).",
         "**사후 국면 라벨을 쓰지 않는다.** 달력 분할 + 잔차-시장수익 회귀 + 대조군 부호 분할만 쓴다.", ""]

    for arm in ARMS:
        nav = arms.get(arm) or {}
        pts = sorted(d for d in nav if d in snav and d >= start)
        if len(pts) < 10:
            continue
        # 구간(월간) 수익률 — 시작점은 자본에서 출발
        seq = [(start, CAPITAL, CAPITAL)] + [(d, nav[d], snav[d]) for d in pts]
        rows = []
        for i in range(1, len(seq)):
            d, a1, s1 = seq[i]
            _, a0, s0 = seq[i - 1]
            if a0 > 0 and s0 > 0:
                rows.append((d, (a1 / a0 - 1) * 100, (s1 / s0 - 1) * 100))
        if len(rows) < 10:
            continue
        resid = [a - s for _, a, s in rows]
        mkt = [s for _, _, s in rows]
        reg = ols(mkt, resid)

        L += [f"## `{arm}` — 구간 {len(rows)}개", "",
              "### ① 잔차 = α + β × 정적수익", "",
              "| | 값 | 뜻 |", "|---|---:|---|"]
        if reg:
            b, a, t, r2 = reg
            L += [f"| **β (시장 민감도)** | **{b:+.3f}** | "
                  f"{'상승장에서만 버는 구조 — 강세 산물' if b > 0.05 else '방어형(하락장에 덜 잃음)' if b < -0.05 else '국면 중립'} |",
                  f"| α (국면 무관 초과) | {a:+.3f}%p/구간 | 연 환산 {a*12:+.1f}%p |",
                  f"| β의 t | {t:+.2f} |" + " 표본이 작아 판정이 아니라 귀속용 |" if t else "| β의 t | - | |",
                  f"| R² | {r2:.3f} |" + " 잔차 변동 중 시장으로 설명되는 몫 |" if r2 is not None else "| R² | - | |"]
        L += ["", "### ② 대조군 부호로 나눈 잔차 (arm 정보를 안 쓴다)", "",
              "| 구간 | 개수 | 정적 평균 | **잔차 평균** | 잔차 양수 |", "|---|---:|---:|---:|---:|"]
        for nm, sel in (("정적 벤치가 **양수**였던 구간", [r for r in rows if r[2] > 0]),
                        ("정적 벤치가 **음수**였던 구간", [r for r in rows if r[2] <= 0])):
            if not sel:
                L.append(f"| {nm} | 0 | - | - | - |")
                continue
            rs = [a - s for _, a, s in sel]
            L.append(f"| {nm} | {len(sel)} | {st.mean(s for _, _, s in sel):+.2f}% | "
                     f"**{st.mean(rs):+.3f}%p** | {sum(1 for v in rs if v > 0)}/{len(rs)} |")

        L += ["", "### ③ 연도별 잔차 (달력 분할 — 외생)", "",
              "| 연도 | 구간 | 정적 | arm | **잔차** |", "|---|---:|---:|---:|---:|"]
        by = defaultdict(list)
        for d, a, s in rows:
            by[d[:4]].append((a, s))
        for y in sorted(by):
            v = by[y]
            ca = math.prod(1 + a / 100 for a, _ in v) - 1
            cs = math.prod(1 + s / 100 for _, s in v) - 1
            L.append(f"| {y} | {len(v)} | {cs*100:+.1f}% | {ca*100:+.1f}% | "
                     f"**{(ca-cs)*100:+.1f}%p** |")
        L.append("")

    # β가 현금 때문인지 확인 — 현금비중만큼은 기계적으로 β를 음수로 만든다.
    cashr = defaultdict(list)
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") == "mtm" and r.get("arm") and f(r.get("nav")) and r.get("cash") is not None:
            cashr[r["arm"]].append(f(r["cash"]) / f(r["nav"]))
    L += ["## β가 현금 때문인가 — 아니다", "",
          "현금을 들고 있으면 그만큼 시장에 덜 노출돼 **기계적으로** β가 음수가 된다.",
          "그 몫을 빼고 봐야 리밸런싱 자체의 효과가 남는다.", "",
          "| arm | 평균 현금비중 | 현금이 설명하는 β | 실측 β | **현금 초과분** |",
          "|---|---:|---:|---:|---:|"]
    betas = {"ew": -0.139, "absmom": -0.152, "ew_band8": -0.105}
    for a in ARMS:
        if a not in cashr or not cashr[a]:
            continue
        c = st.mean(cashr[a])
        L.append(f"| `{a}` | {c*100:.2f}% | {-c:+.3f} | {betas[a]:+.3f} | "
                 f"**{betas[a] + c:+.3f}** ({abs(betas[a]/-c):.0f}배) |")
    L += ["", "현금은 1.4% 안팎이라 β를 −0.014 정도밖에 못 만든다. 실측 β는 그 **7~10배**다.",
          "→ **방어 성향은 현금 탓이 아니라 리밸런싱 자체에서 나온다**(오른 걸 팔고 빠진 걸 산다).", "",
          "## 읽는 법 — 그리고 내가 또 틀렸던 것", "",
          "**어제 단 캐비앳이 방향부터 틀렸다.** 나는 `ew_band8`의 +9.9%p 잔차를",
          "\"강세장에서 덜 팔아 추세를 탄 것일 수 있다\"고 의심했는데, β가 **전부 음수**다.",
          "리밸런싱은 강세장 편승이 아니라 **역행**이다 — 오를 때 팔고 빠질 때 산다.",
          "대조군 부호로 나눠도 같다: 세 arm 모두 **상승 구간에서 지고 하락 구간에서 이긴다.**", "",
          "→ 측정 구간(KOSPI200 +260%)은 이 구조에 **순풍이 아니라 역풍**이었다.",
          "  `ew_band8`의 +6.4%p는 강세장 덕이 아니라 **역풍에도 불구하고** 낸 값이다.",
          "  횡보·하락 국면이 오면 이 격차는 줄어드는 게 아니라 **커지는 쪽**이 기댓값이다.", "",
          "**남는 판별자는 α다.** β는 세 arm이 비슷한데(−0.11~−0.15) α가 갈린다:",
          "`ew_band8` +2.8%p/년 · `ew` +1.5%p/년 · `absmom` +0.2%p/년.",
          "빈도 5회가 45회보다 나은 이유는 방어력 차이가 아니라 **마찰과 타이밍 손실 차이**다.", "",
          "- ②는 **대조군 자신의 부호**로만 나눈다 — arm 성과를 보고 자른 게 아니라 편향이 없다.",
          "- 표본이 작다(구간 ~50개, 그중 하락 16개). **판정이 아니라 귀속**이다.",
          "  하락 국면 표본이 16개뿐이라 방어 성향의 크기는 아직 못 박는다.",
          "- ⚠️ 배분 변경은 운영자 결정이다.", ""]

    out = ROOT / "docs" / "reports" / "core_residual_regime_20260919.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
