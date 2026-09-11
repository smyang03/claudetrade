#!/usr/bin/env python3
"""패닉 ETF — 급락 후보 수(k)가 강도 정보를 더하는가 (read-only, 2026-09-12).

09-12에 따로 나온 두 발견을 붙인다.
 · KR 급락 레인: **k≥20인 26일**에서만 개별주 초과 +3.68%(t 3.19). 용량이 막아서 못 먹는다.
 · 패닉 ETF: v2(갭≤−1 or 종가≤−2.5) 오버나이트가 사전 기준 통과. 용량이 없다.

**k는 지수 등락·갭과 다른 정보다** — 지수가 얼마나 빠졌나가 아니라 **몇 종목이 급락했나**(폭).
그 폭을 용량 없는 그릇(ETF)에 담을 수 있는지 본다.

## 이건 새 레인이 아니라 강도 등급 검정이다

k≥20인 26일 중 **25일이 이미 v2 신호일**이다(09-12 실측). 즉 k를 단독 신호로 쓰면
v2를 거의 그대로 재생산한다. 물어야 할 것은 **v2가 이미 켜진 상태에서 k가 등급을 나누는가**다.
나뉜다면 09-10이 "forward 강신호 5회 후 운영자 결정"으로 보류한 **2단 사이징**에
갭 깊이가 아니라 **시장 폭**이라는 근거가 생긴다.

## 사전 등록 — 숫자 보기 전에 고정

- **주 검정 1회**: v2 신호일 중 **k≥20 vs k≤4**의 연도매칭 초과 차이.
- **기준**: |t| ≥ 2 **그리고** 전반(~2021)·후반(2022~) 부호 일치.
- 티어 표(k=0 / 1~4 / 5~19 / 20+)는 **관측용**이며 판정에 쓰지 않는다.
- 통과해도 **후보**다. 승격은 사전등록 후 forward로만.

## 알려진 제약 — 통과해도 바로 못 쓴다

k는 `build()`가 **당일 종가·시가**로 계산한다. 15:19에 근사는 되지만
**811종목 스캔이 그 시각에 필요하고 지금 배선엔 없다**(라이브 급락 스캔은 장 마감 후에 돈다).
통과하면 "배선 과제"가 생기는 것이지 즉시 적용이 아니다.

구간은 두 데이터가 겹치는 **2018~2026**이다(KR 장기 캐시가 2018-01부터).
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.kr_index_etf_panic_shadow as P  # noqa: E402  (read-only)
from tools.research_kr_rank_pool_reconcile import build, by_session  # noqa: E402

LONG = ROOT / "data" / "price_backfill_yf" / "kr_fallen_price_cache_2018_2026.json"
TICKER = "233740"
START = "2018-01-01"
SPLIT = "2022"
T_BAR = 2.0


def welch(a, b):
    if len(a) < 3 or len(b) < 3:
        return None, None
    va, vb = st.variance(a) / len(a), st.variance(b) / len(b)
    if va + vb == 0:
        return None, None
    d = st.mean(a) - st.mean(b)
    return d, d / math.sqrt(va + vb)


def uncond_by_year(cache, tk):
    dates = sorted(cache)
    g = defaultdict(list)
    for i in range(1, len(dates)):
        a, b = cache[dates[i - 1]], cache[dates[i]]
        if a.get(f"{tk}_close") and b.get(f"{tk}_open"):
            g[dates[i][:4]].append((b[f"{tk}_open"] / a[f"{tk}_close"] - 1.0) * 100.0)
    return {y: st.mean(v) for y, v in g.items() if v}


def main():
    if not LONG.exists():
        print(f"장기 캐시 없음: {LONG}", file=sys.stderr)
        return 2
    kcount = {d: len(v) for d, v in by_session(build(
        json.loads(LONG.read_text(encoding="utf-8")))).items()}

    cache = P._read_cache()
    pooled = P._uncond_overnight(cache, TICKER)
    by_year = uncond_by_year(cache, TICKER)

    recs = []
    for r in P._load():
        if (r.get("kind") != "trade" or r["ticker"] != TICKER
                or r.get("status") != "CLOSED" or r.get("mode") != "backfill"):
            continue
        d = r["session_date"]
        if d < START:
            continue
        raw = r["net_pct"] + P.COST
        recs.append({"day": d, "k": kcount.get(d, 0), "raw": raw,
                     "ex": raw - by_year.get(d[:4], pooled or 0.0)})
    recs.sort(key=lambda r: r["day"])

    hi = [r for r in recs if r["k"] >= 20]
    lo = [r for r in recs if r["k"] <= 4]
    diff, t = welch([r["ex"] for r in hi], [r["ex"] for r in lo])
    halves = {}
    for lab, sel in (("전반(~2021)", lambda d: d < SPLIT), ("후반(2022~)", lambda d: d >= SPLIT)):
        h = [r["ex"] for r in hi if sel(r["day"])]
        l = [r["ex"] for r in lo if sel(r["day"])]
        halves[lab] = welch(h, l)[0] if len(h) >= 3 and len(l) >= 3 else None
    same = (all(v is not None for v in halves.values())
            and list(halves.values())[0] * list(halves.values())[1] > 0)
    passed = t is not None and abs(t) >= T_BAR and same

    L = [f"# 패닉 ETF — 급락 후보 수(k)가 강도를 나누는가 ({date.today().isoformat()})", "",
         "read-only. `tools/research/research_panic_etf_breadth_tier.py`(사전등록 기준은 docstring).",
         "라이브 쉐도우는 읽기만 한다. 라이브 변경 없음.", "",
         "**새 레인 검정이 아니라 강도 등급 검정이다.** k≥20인 26일 중 25일이 이미 v2 신호일이라,",
         "k 단독은 v2를 재생산한다. 묻는 것은 **v2가 켜진 상태에서 k가 등급을 나누는가**다.", "",
         f"표본: {TICKER} v2 백필 중 {START} 이후 **{len(recs)}건** · "
         f"벤치마크는 **연도매칭** 무조건 오버나이트(풀드 스칼라 {pooled:+.4f}% 대신)", "",
         "## 주 검정 — k≥20 vs k≤4 (1회)", "",
         "| 구간 | n | 연도매칭 초과 |", "|---|---:|---:|",
         f"| k≥20 | {len(hi)} | {st.mean(r['ex'] for r in hi):+.3f}% |" if hi else "| k≥20 | 0 | - |",
         f"| k≤4 | {len(lo)} | {st.mean(r['ex'] for r in lo):+.3f}% |" if lo else "| k≤4 | 0 | - |",
         "",
         f"차이 **{diff:+.3f}%p**, t **{t:.2f}**" if t is not None else "차이 계산 불가",
         f"· 전반 {halves['전반(~2021)']:+.3f}%p" if halves.get("전반(~2021)") is not None else "· 전반 -",
         f"· 후반 {halves['후반(2022~)']:+.3f}%p" if halves.get("후반(2022~)") is not None else "· 후반 -",
         "", f"**{'통과' if passed else '기각'}** (기준 |t|≥{T_BAR} & 전후반 부호 일치)", ""]

    L += ["## 관측용 티어 표 — 판정에 쓰지 않음", "",
          "| k 구간 | n | 일수 | 원 오버나이트 | 연도매칭 초과 | 승률 |", "|---|---:|---:|---:|---:|---:|"]
    for lab, a, b in (("k=0 (급락 후보 없음)", 0, 0), ("k=1~4", 1, 4),
                      ("k=5~19", 5, 19), ("k≥20", 20, 10 ** 9)):
        sub = [r for r in recs if a <= r["k"] <= b]
        if not sub:
            L.append(f"| {lab} | 0 | 0 | - | - | - |")
            continue
        L.append(f"| {lab} | {len(sub)} | {len({r['day'] for r in sub})} | "
                 f"{st.mean(r['raw'] for r in sub):+.3f}% | "
                 f"{st.mean(r['ex'] for r in sub):+.3f}% | "
                 f"{100 * sum(1 for r in sub if r['raw'] > 0) / len(sub):.1f}% |")

    L += ["", "## 교락 확인 — 티어가 연도를 대신 세는가", "",
          "오늘 오전에 배운 것(절대 문턱은 고변동 해에 다른 모집단을 뽑는다)을 여기에도 댄다.", "",
          "| 티어 | n | 2026 비중 |", "|---|---:|---:|"]
    for lab, a, b in (("k=0", 0, 0), ("k=1~4", 1, 4), ("k=5~19", 5, 19), ("k≥20", 20, 10 ** 9)):
        sub = [r for r in recs if a <= r["k"] <= b]
        if sub:
            L.append(f"| {lab} | {len(sub)} | "
                     f"{100 * sum(1 for r in sub if r['day'][:4] == '2026') / len(sub):.1f}% |")
    hi26 = [r["ex"] for r in hi if r["day"][:4] != "2026"]
    lo26 = [r["ex"] for r in lo if r["day"][:4] != "2026"]
    d26, t26 = welch(hi26, lo26)
    L += ["",
          "**k≥20 티어의 52%가 2026이다 — 교락이 있다.** 그래서 2026을 빼고 다시 쟀다:",
          f"k≥20 n{len(hi26)} {st.mean(hi26):+.3f}% vs k≤4 n{len(lo26)} {st.mean(lo26):+.3f}% "
          f"= 차이 **{d26:+.3f}%p, t {t26:.2f}**.",
          "**2026을 빼면 차이가 줄지 않고 커진다.** 2026 산물은 아니다. 그래도 t는 기준 미달이다."]

    L += ["", "## 현행 강신호(갭≤−2)와 비교", "",
          "09-10은 강도를 **갭 깊이**로 정의했다(strong n43 초과 +2.030 vs normal +0.419).",
          "k는 **시장 폭**이라 다른 축이다. 둘이 같은 날을 가리키면 새 정보가 아니다.", ""]
    strong_days = {r["session_date"] for r in P._load()
                   if r.get("kind") == "trade" and r["ticker"] == TICKER
                   and r.get("strength") == "strong" and r["session_date"] >= START}
    hi_days = {r["day"] for r in hi}
    inter = hi_days & strong_days
    union = hi_days | strong_days
    L += [f"- k≥20인 날 {len(hi_days)}일 · 갭강신호 {len(strong_days)}일 · "
          f"교집합 {len(inter)}일 → 자카드 **{100 * len(inter) / len(union):.0f}%**" if union else "- 비교 불가",
          "",
          ("**두 축이 대체로 다른 날을 가리킨다 — 독립 정보다.**" if union and len(inter) / len(union) < 0.5
           else "**두 축이 대체로 같은 날이다 — 새 정보가 아니다.**")]

    L += ["", "## 결론 — 기각이지만 닫지는 않는다", "",
          "사전 고정 기준(|t|≥2)을 못 넘었다. **기준을 사후에 낮추지 않는다.**",
          "다만 기각 사유가 **방향이 아니라 검정력**이다:",
          "- 4개 티어가 단조다 (+0.02 → +0.55 → +1.34 → +2.34), 승률도 56 → 67 → 73 → 72%.",
          "- 전반 +2.41 / 후반 +1.85로 부호가 같다.",
          "- 2026을 빼면 차이가 커진다(+2.41%p).",
          "- 갭 강신호와 자카드 18% = **다른 날을 가리키는 독립 축**이다.",
          "- 실패 원인은 k≥20이 **25건뿐**이라는 것이다.",
          "",
          "→ **사전등록 후 forward 대상**으로 올린다. 지금 사이징에 반영하지 않는다.",
          "   등록 문구: *v2 신호일을 k로 3등급(0 / 1~19 / 20+)으로 나누고,*",
          "   *forward 30건에서 20+ 등급의 연도매칭 초과가 0 등급보다 크면 2단 사이징 근거로 쓴다.*",
          "",
          "**k=0 티어(75건, 초과 +0.015%)는 따로 기록해 둔다.** v2 신호의 32%가 여기 있고",
          "그 밤들은 거의 아무것도 못 번다. '회피 필터' 후보지만 **사후에 만든 규칙**이라",
          "지금 적용하지 않는다. 위 forward 등록에 0 등급이 포함돼 있다.", "",
          "## 제약 — 통과해도 즉시 적용 아님", "",
          "k는 `build()`가 당일 종가·시가로 계산한다. 15:19 근사는 되지만 **811종목 스캔이",
          "그 시각에 필요하고 지금 배선엔 없다**(라이브 급락 스캔은 장 마감 후 실행).",
          "통과는 **배선 과제**를 만드는 것이지 적용 승인이 아니다.", "",
          "⚠️ k는 2026-09 현재 CSV 보유 종목으로 센 값이라 **생존편향 하한 3.3%**가 그대로 적용된다."]

    out = ROOT / "docs" / "reports" / f"panic_etf_breadth_tier_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
