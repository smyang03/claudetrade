#!/usr/bin/env python3
"""코스닥 패닉 ETF 오버나이트 — 연도 분해와 벤치마크 보정 (read-only, 2026-09-12).

09-10 정본이 이 레인을 "용량 우회"의 유일한 승격 후보로 올렸다(11년 250건, 초과 +0.70%/건,
t 3.35). 09-12에 KR 급락 레인에서 배운 것을 그대로 적용해 두 가지를 확인한다.
**전략 재설계가 아니라 기존 결과의 분해 + 벤치마크 보정이다. 새 가설 검정이 아니다.**

## 보정 1 — 무조건 평균이 11년 풀드 스칼라다

`kr_index_etf_panic_shadow._uncond_overnight()`는 2015~2026 전 구간 평균 1개를 쓴다
(233740 0.1047% / 229200 0.0834%). 그런데 2026은 11년 평균의 해가 아니다 —
069500 일간변동이 2018 0.92% → 2026 4.39%다. 지수가 급등하는 해에는
무조건 오버나이트 자체가 커지므로, **2026 거래가 너무 낮은 기준선 대비로 후하게 계산된다.**
그래서 **연도매칭 무조건 평균**(같은 ETF·같은 연도·전 영업일)으로 다시 센다.
보수적 방향의 보정이고, 라이브 쉐도우 파일은 건드리지 않는다.

## 보정 2 — 문턱이 절대값이라 2026은 다른 모집단이다

`갭≤−1% or 종가≤−2.5%`는 일간변동 1%인 해에는 ~2σ 사건이지만
4.4%인 해에는 평범한 화요일이다. 2015~2025 백필은 '진짜 패닉'을 재고,
2026 신호는 '고변동 국면의 보통 날'을 잴 수 있다. 연도별 신호 수가 이걸 바로 보여준다.
**2026(8개월) 신호 수가 2015~2025 연평균의 2배 이상이면, 지금 돌고 있는 forward는
재현 검정이 아니라 다른 국면 검정이다.**

## 무엇이 살아남는가 — 숫자 보기 전에 고정

**2015~2025만으로 연도매칭 초과의 t ≥ 2 이고 양수 연도가 11년 중 8년 이상이면,
이 엣지는 2026 산물이 아니다.** 그 조건을 못 넘으면 승격 후보 지위를 내린다.

원장·가격은 라이브 쉐도우 모듈에서 읽기만 한다(import, 쓰기 없음).
"""
from __future__ import annotations

import statistics as st
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.kr_index_etf_panic_shadow as P  # noqa: E402  (read-only 사용)

EXPOSURE_KRW = 1_000_000     # 09-10 설계의 위성 층 노출
CUT_YEAR = "2026"


def t_stat(xs):
    if len(xs) < 3 or st.pstdev(xs) == 0:
        return None
    return st.mean(xs) / (st.pstdev(xs) / len(xs) ** 0.5)


def uncond_by_year(cache, tk):
    """연도매칭 무조건 오버나이트 — 같은 ETF·같은 연도 전 영업일 평균."""
    dates = sorted(cache)
    g = defaultdict(list)
    for i in range(1, len(dates)):
        a, b = cache[dates[i - 1]], cache[dates[i]]
        if a.get(f"{tk}_close") and b.get(f"{tk}_open"):
            g[dates[i][:4]].append((b[f"{tk}_open"] / a[f"{tk}_close"] - 1.0) * 100.0)
    return {y: st.mean(v) for y, v in g.items() if v}


def kq_vol_by_year(cache):
    """코스닥 지수 일간 등락 표준편차(연도별) — 문턱의 σ 환산을 위해."""
    g = defaultdict(list)
    for d in sorted(cache):
        chg = cache[d].get("kq_chg")
        if chg is not None:
            g[d[:4]].append(chg)
    return {y: st.pstdev(v) for y, v in g.items() if len(v) > 20}


def main():
    cache = P._read_cache()
    rows = P._load()
    closed = [r for r in rows if r.get("kind") == "trade" and r.get("status") == "CLOSED"]
    vol = kq_vol_by_year(cache)

    L = [f"# 코스닥 패닉 ETF — 연도 분해와 벤치마크 보정 ({date.today().isoformat()})", "",
         "read-only. `tools/research/research_panic_etf_year_decomp.py`(사전 고정 기준은 docstring).",
         "라이브 쉐도우(`tools/kr_index_etf_panic_shadow.py`)는 읽기만 하고 안 바꾼다.", "",
         "기존 결과의 **분해와 벤치마크 보정**이다. 새 가설 검정이 아니다.", "",
         "> KR 급락 레인의 남은 과제 ①(k 문턱 사전등록 후 forward)·③(상폐 목록 확보)은",
         "> **보류이지 폐기가 아니다.** 용량이 병목이라 용량이 없는 레인을 먼저 본다.", ""]

    verdict_rows = []
    for tk, name in P.ETFS.items():
        pooled = P._uncond_overnight(cache, tk)
        by_year = uncond_by_year(cache, tk)
        sub = [r for r in closed if r["ticker"] == tk and r.get("mode") == "backfill"]
        recs = []
        for r in sub:
            y = r["session_date"][:4]
            raw = r["net_pct"] + P.COST                 # 비용 전 오버나이트
            recs.append({"y": y, "raw": raw,
                         "ex_pooled": raw - (pooled or 0.0),
                         "ex_year": raw - by_year.get(y, pooled or 0.0)})
        total_year = sum(r["ex_year"] for r in recs)
        L += ["", f"## {tk} {name}", "",
              f"풀드 무조건 오버나이트 **{pooled:+.4f}%** (11년 1개 스칼라, 현행 리포트가 쓰는 값)", "",
              "| 연도 | 신호 | 원 오버나이트 | 초과(풀드) | 연도 무조건 | **초과(연도매칭)** | 100만 초과금액 | 코스닥 일변동 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for y in sorted({r["y"] for r in recs}):
            g = [r for r in recs if r["y"] == y]
            L.append(f"| {y} | {len(g)} | {st.mean(r['raw'] for r in g):+.3f}% | "
                     f"{st.mean(r['ex_pooled'] for r in g):+.3f}% | {by_year.get(y, float('nan')):+.3f}% | "
                     f"**{st.mean(r['ex_year'] for r in g):+.3f}%** | "
                     f"{EXPOSURE_KRW * sum(r['ex_year'] for r in g) / 100:>10,.0f}원 | "
                     f"{vol.get(y, float('nan')):.2f}% |")

        pre = [r for r in recs if r["y"] < CUT_YEAR]
        cur = [r for r in recs if r["y"] >= CUT_YEAR]
        pre_years = sorted({r["y"] for r in pre})
        pos_years = sum(1 for y in pre_years
                        if st.mean(r["ex_year"] for r in recs if r["y"] == y) > 0)
        tp_all, tp_pre = t_stat([r["ex_year"] for r in recs]), t_stat([r["ex_year"] for r in pre])
        f = lambda x: f"{x:.2f}" if x is not None else "-"          # noqa: E731
        L += ["", "| 구간 | n | 초과(풀드) | 초과(연도매칭) | t(연도매칭) | 양수 연도 | 총액 비중 |",
              "|---|---:|---:|---:|---:|---:|---:|",
              f"| 전체 | {len(recs)} | {st.mean(r['ex_pooled'] for r in recs):+.3f}% | "
              f"{st.mean(r['ex_year'] for r in recs):+.3f}% | {f(tp_all)} | - | 100% |",
              f"| **2015~2025** | {len(pre)} | {st.mean(r['ex_pooled'] for r in pre):+.3f}% | "
              f"**{st.mean(r['ex_year'] for r in pre):+.3f}%** | **{f(tp_pre)}** | "
              f"**{pos_years}/{len(pre_years)}** | "
              f"{100 * sum(r['ex_year'] for r in pre) / total_year:.0f}% |"]
        if cur:
            L.append(f"| 2026 (8개월) | {len(cur)} | {st.mean(r['ex_pooled'] for r in cur):+.3f}% | "
                     f"{st.mean(r['ex_year'] for r in cur):+.3f}% | {f(t_stat([r['ex_year'] for r in cur]))} | - | "
                     f"{100 * sum(r['ex_year'] for r in cur) / total_year:.0f}% |")
        rate_pre = len(pre) / max(1, len(pre_years))
        rate_cur = len(cur) / (8 / 12) if cur else 0.0
        L += ["", f"신호 발생률: 2015~2025 **연 {rate_pre:.1f}회** vs 2026 **연율 {rate_cur:.1f}회** "
                  f"({rate_cur / rate_pre:.1f}배)"]
        passed = tp_pre is not None and tp_pre >= 2 and pos_years >= 8
        verdict_rows.append((tk, name, passed, tp_pre, pos_years, len(pre_years),
                            st.mean(r["ex_year"] for r in pre) if pre else None,
                            rate_cur / rate_pre if rate_pre else None))

    L += ["", "## 판정 — 사전 고정 기준(2015~2025 연도매칭 t≥2 & 양수 연도 ≥8)", "",
          "| ETF | 2015~2025 초과 | t | 양수 연도 | 2026 신호 배수 | 판정 |", "|---|---:|---:|---:|---:|---|"]
    for tk, name, passed, tpre, pos, ny, ex, mult in verdict_rows:
        L.append(f"| {tk} {name} | {ex:+.3f}% | {tpre:.2f} | {pos}/{ny} | "
                 f"{mult:.1f}배 | {'**통과**' if passed else '기각'} |")

    L += ["", "## 그래서 100만 노출로 월 얼마인가", "",
          f"노출 {EXPOSURE_KRW:,}원 고정·단일 사이징. **net = 오버나이트 − 왕복비용 {P.COST}%**,",
          f"거기에 파생형 ETF 배당소득세 {P.TAX_RATE * 100:.1f}%를 이익분에만 적용한다.",
          "검증된 구간(2015~2025)과 2026을 따로 낸다 — 2026은 신호 발생률이 3.3배라 같은 모집단이 아니다.", "",
          "| ETF | 구간 | 개월 | 신호 | 세후 누적 | **월** |", "|---|---|---:|---:|---:|---:|"]
    for tk, name in P.ETFS.items():
        sub = [r for r in closed if r["ticker"] == tk and r.get("mode") == "backfill"]
        for lab, sel, months in (("2015~2025", lambda y: y < CUT_YEAR, 123), ("2026 (8개월)", lambda y: y >= CUT_YEAR, 8)):
            g = [r for r in sub if sel(r["session_date"][:4])]
            if not g:
                continue
            gross = sum(EXPOSURE_KRW * r["net_pct"] / 100 for r in g)
            after = gross * (1 - P.TAX_RATE) if gross > 0 else gross
            L.append(f"| {tk} | {lab} | {months} | {len(g)}건 | {after:,.0f}원 | **{after / months:,.0f}원** |")
    L += ["",
          "**검증 구간 기준 233740이 월 8,252원이다.** 세금 15.4%가 크고, 레버리지 2배라",
          "최악 1건 −12.96%(100만이면 −13만)을 감수한 값이다. 229200(비레버리지)은 월 3,966원.",
          "2026만 보면 월 7만원이지만 **신호가 3.3배 쏟아진 8개월이라 같은 모집단이 아니다** —",
          "이 숫자로 레인을 평가하지 않는다.",
          "", "**다만 이 레인은 KR 급락 레인과 병목이 다르다.** 급락 레인은 슬롯 3이 신호일의 절반을",
          "버려서 자본을 넣어도 안 늘었다. 여기는 신호일 ETF 거래대금 중앙 2,784억이라",
          "**용량이 벽이 아니다 — 자본만큼 선형으로 늘어난다.** 500만이면 월 5만, 1,000만이면 월 10만이다.",
          "그 대가는 레버리지 ETF 오버나이트 노출이고, 최악 1건은 노출의 −13%다."]

    out = ROOT / "docs" / "reports" / f"panic_etf_year_decomp_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
