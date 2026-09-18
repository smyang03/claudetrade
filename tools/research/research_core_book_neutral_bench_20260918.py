#!/usr/bin/env python3
"""코어 북 중립 벤치 — 리밸런싱이 값을 더하는가 (read-only, 2026-09-18).

## 왜 지금 벤치로는 판정이 안 되나

`core_etf_book_shadow.py`의 `BENCH = "069500"`인데, **069500은 북을 구성하는 5종 중 하나다.**
분산 포트폴리오를 그 구성요소 하나와 비교하면, 그 하나가 제일 많이 오른 구간에서는
**무조건 진다.** 실제로 2023-01~2026-09에 069500은 +260%였고 북은 +113~142%다.

이 숫자는 "코어가 못했다"는 증거가 **아니다.** 판정이 성립하지 않는 비교다.
그런데 자본의 대부분이 여기 있고, 우리는 이 자리를 한 번도 제대로 재본 적이 없다.

## 진짜 물어야 할 것

코어 북이 하는 일은 **자산 선택이 아니라 운용**이다 — 월 1회 리밸런싱, 12-1 절대모멘텀,
±8% 밴드. 5종 바구니는 주어진 것이다. 그러면 대조군은 지수 하나가 아니라
**같은 5종을 한 번 사고 아무것도 안 하기**여야 한다.

  · `정적 동일가중` — 시작일에 20%씩 사고 **끝까지 방치**(리밸런싱 0, 세금 0, 수수료 1회)
  · 각 구성요소 단독 — 맥락용(바구니 선택 자체가 좋았는지)
  · `ew` / `absmom` / `ew_band8` — 실제 arm(원장 nav)

**arm − 정적 동일가중 = 운용이 더한 값.** 이게 처음으로 판정 가능한 숫자다.

## 규약

- 정적 벤치도 arm과 **같은 계약**으로 만든다: 같은 자본 260만, 정수 주, 편도 수수료 0.015%.
  매도가 없으므로 세금도 없다(그게 방치 전략의 실제 이점이고, 감춰선 안 된다).
- arm nav는 내가 다시 계산하지 않고 **원장(`core_etf_book.jsonl`)에서 읽는다.**
  내 재구현이 원장과 어긋나면 그건 별개 결함이고, 여기서 섞으면 둘 다 못 믿는다.
- **낙폭은 arm과 같은 날짜로 샘플링해 비교한다.** arm nav는 원장 mtm 시점만이라 월 단위(~48점)인데
  정적 벤치를 일별(887점)로 재면 정적 쪽만 낙폭이 깊게 잡혀 arm에 유리한 착시가 생긴다.
  초안이 그 상태였고(정적 −21.3% vs arm −13.6%), 같은 날짜로 맞추니 −15.5%가 됐다.

⚠️ 이건 코어를 바꾸라는 제안이 아니다. **판정 가능한 비교를 처음 만드는 것**이다.
배분 변경·중단은 운영자 결정이다.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "analysis" / "core_etf_prices.csv"
LEDGER = ROOT / "data" / "shadow" / "core_etf_book.jsonl"
REPORT = ROOT / "data" / "analysis" / "core_etf_book_report.json"

UNIVERSE = {"379810": "KODEX 미국나스닥100", "360750": "TIGER 미국S&P500",
            "411060": "ACE KRX금현물", "305080": "TIGER 미국채10년선물",
            "069500": "KODEX 200"}
CAPITAL = 2_600_000.0
FEE_SIDE = 0.00015
ARMS_ORDER = ("ew", "absmom", "ew_band8")


def load_prices():
    rows = {}
    with CACHE.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            px = {}
            for t in UNIVERSE:
                try:
                    v = float(r[t])
                    if v > 0:
                        px[t] = v
                except (TypeError, ValueError, KeyError):
                    pass
            if len(px) == len(UNIVERSE):      # 5종 전부 있는 날만
                rows[r["date"]] = px
    return rows


def static_book(prices, dates, tickers, weights):
    """시작일에 사고 방치. (nav 계열, 시작일)"""
    d0 = dates[0]
    qty, cash = {}, CAPITAL
    for t in tickers:
        budget = CAPITAL * weights[t]
        px = prices[d0][t]
        q = int(math.floor(budget / (px * (1 + FEE_SIDE))))
        qty[t] = q
        cash -= q * px * (1 + FEE_SIDE)
    nav = [(d, cash + sum(qty[t] * prices[d][t] for t in tickers)) for d in dates]
    return nav, qty, cash


def stats(nav, base=None, base_date=None):
    """연수는 **행 수가 아니라 날짜 간격**으로 낸다.

    원장의 arm nav는 일별이 아니라 mtm 시점만 있다(arm당 ~48행). 행 수를 252로 나누면
    연수가 0.19로 잡혀 CAGR이 +5044%처럼 나온다 — 초안에서 실제로 그렇게 나왔다.

    `base`(=투입 자본)를 주면 수익률을 **자본 기준**으로 낸다. 이것도 초안 결함이었다 —
    arm의 첫 mtm 행이 2023-01-31(nav 2,670,226)인데 북 시작은 2023-01-02 260만이다.
    첫 행을 분모로 쓰면 arm만 첫 달 +2.7%를 잃고 들어가, 정적 벤치에 선행을 주게 된다.
    """
    from datetime import date
    start = base if base is not None else nav[0][1]
    end = nav[-1][1]
    peak, dd = -1e18, 0.0
    for _, v in nav:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
    d0 = date.fromisoformat(base_date or nav[0][0])
    d1 = date.fromisoformat(nav[-1][0])
    yrs = max((d1 - d0).days / 365.25, 1e-9)
    cagr = (end / start) ** (1 / yrs) - 1 if start > 0 else 0.0
    ret = (end / start - 1) * 100
    return {"ret": ret, "cagr": cagr * 100, "mdd": dd * 100, "end": end,
            "yrs": yrs, "rr": ret / abs(dd * 100) if dd else None}


def arm_navs():
    """원장에서 arm별 (date, nav) 계열을 읽는다 — 재계산하지 않는다."""
    out = {}
    if not LEDGER.exists():
        return out
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") != "mtm" or not r.get("arm"):
            continue
        out.setdefault(r["arm"], []).append((r.get("date"), float(r.get("nav") or 0)))
    for a in out:
        out[a] = sorted(x for x in out[a] if x[0] and x[1] > 0)
    return out


def main():
    prices = load_prices()
    dates = sorted(prices)
    # arm 원장의 시작일에 맞춘다(2023-01-02) — 창이 다르면 비교가 아니다.
    rep = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    start = min((v.get("start") for v in rep.get("arms", {}).values() if v.get("start")),
                default=dates[0])
    dates = [d for d in dates if d >= start]
    tick = list(UNIVERSE)

    ew_w = {t: 1 / len(tick) for t in tick}
    nav_static, _, _ = static_book(prices, dates, tick, ew_w)
    s_static = stats(nav_static, base=CAPITAL, base_date=dates[0])

    singles = {}
    for t in tick:
        nav, _, _ = static_book(prices, dates, [t], {t: 1.0})
        singles[t] = stats(nav, base=CAPITAL, base_date=dates[0])

    arms = arm_navs()
    a_stats = {}
    for a, series in arms.items():
        series = [x for x in series if x[0] >= start]
        if len(series) > 5:
            # 시작점 (북 개시일, 자본)을 앞에 붙인다 — 첫 달 움직임이 낙폭에서 빠지지 않게.
            a_stats[a] = stats([(dates[0], CAPITAL)] + series,
                               base=CAPITAL, base_date=dates[0])

    # ⚠️ 낙폭 비교 보정. arm nav는 원장 mtm 시점만이라 arm당 ~48개(월 단위)인데
    # 정적 벤치는 887일 일별이다. **드문드문 보면 최대낙폭이 작게 잡힌다** — 그대로 비교하면
    # arm에 유리한 착시가 생긴다. 그래서 정적 벤치를 **arm과 같은 날짜로 샘플링**해 다시 낸다.
    arm_dates = sorted({d for s in arms.values() for d, _ in s if d >= start})
    nav_map = dict(nav_static)
    sparse = [(dates[0], CAPITAL)] + [(d, nav_map[d]) for d in arm_dates if d in nav_map]
    s_static_sparse = (stats(sparse, base=CAPITAL, base_date=dates[0])
                       if len(sparse) > 5 else None)

    L = [f"# 코어 북 중립 벤치 — 운용이 값을 더하는가 ({start} ~ {dates[-1]})", "",
         "read-only. `tools/research/research_core_book_neutral_bench_20260918.py`(규약은 docstring).",
         "**현행 벤치 `069500`은 북을 구성하는 5종 중 하나라 판정이 성립하지 않는다.**",
         "대조군은 지수 하나가 아니라 **같은 5종을 한 번 사고 방치하기**여야 한다.", "",
         f"자본 {CAPITAL:,.0f}원 · 정수 주 · 편도 수수료 {FEE_SIDE*100:.3f}% · "
         f"거래일 {len(dates)}일", "",
         "## 1. 운용 vs 방치 — 이게 진짜 비교다", "",
         "수익만 보면 불공정하다. 리밸런싱의 목적 절반은 **낙폭 억제**이므로 둘 다 싣는다.",
         "`수익/낙폭`은 최대낙폭 1%p당 수익(클수록 좋음).", "",
         "⚠️ **낙폭은 같은 날짜로 재야 한다.** arm nav는 원장 mtm 시점만이라 월 단위(~48점)인데",
         "정적 벤치는 일별(887점)이다. 드문드문 보면 최대낙폭이 작게 잡혀 arm에 유리한 착시가 생긴다.",
         "그래서 정적 벤치를 **arm과 같은 날짜로 샘플링한 행**을 기준선으로 쓴다.", "",
         "| | 리밸런스 | 총수익 | CAGR | 최대낙폭 | **수익/낙폭** | 정적 대비 수익 |",
         "|---|---:|---:|---:|---:|---:|---:|"]
    if s_static_sparse:
        L.append(f"| **정적 동일가중 — arm과 같은 날짜(기준선)** | 0 | "
                 f"{s_static_sparse['ret']:+.1f}% | {s_static_sparse['cagr']:+.1f}% | "
                 f"{s_static_sparse['mdd']:.1f}% | **{s_static_sparse['rr']:.2f}** | — |")
    L += [f"| 정적 동일가중 — 일별(참고, 낙폭이 더 깊게 잡힘) | 0 | {s_static['ret']:+.1f}% | "
          f"{s_static['cagr']:+.1f}% | {s_static['mdd']:.1f}% | **{s_static['rr']:.2f}** | — |"]
    base = s_static_sparse or s_static      # 정적 대비는 **같은 날짜 기준선**으로 잰다
    reb = {a: (rep.get("arms", {}).get(a, {}) or {}).get("rebalances") for a in ARMS_ORDER}
    for a in ARMS_ORDER:
        s = a_stats.get(a)
        if not s:
            continue
        L.append(f"| `{a}` (운용) | {reb.get(a) if reb.get(a) is not None else '-'} | "
                 f"{s['ret']:+.1f}% | {s['cagr']:+.1f}% | {s['mdd']:.1f}% | "
                 f"**{s['rr']:.2f}** | {s['ret']-base['ret']:+.1f}%p |")
    L += ["", "## 2. 구성요소 단독 — 바구니 선택이 좋았나(맥락용)", "",
          "| 종목 | 이름 | 총수익 | CAGR | 최대낙폭 |", "|---|---|---:|---:|---:|"]
    for t, s in sorted(singles.items(), key=lambda kv: -kv[1]["ret"]):
        L.append(f"| `{t}` | {UNIVERSE[t]} | {s['ret']:+.1f}% | {s['cagr']:+.1f}% | {s['mdd']:.1f}% |")

    best = max(singles.items(), key=lambda kv: kv[1]["ret"])
    L += ["", "## 3. 읽는 법", "",
          f"- 현행 리포트의 `bench_ret_pct`는 `069500` 단독({singles['069500']['ret']:+.1f}%)이다. "
          f"이 구간 최고 구성요소는 `{best[0]}`({best[1]['ret']:+.1f}%)이고, "
          "**분산 포트폴리오는 정의상 최고 구성요소에 진다.** 그걸 미달로 보고하면 안 된다.",
          "- 판정 가능한 숫자는 **arm − 정적 동일가중**이다. 양수면 운용이 값을 더한 것이고,",
          "  음수면 수수료·세금·타이밍이 값을 깎은 것이다.",
          f"- 정적 동일가중은 매도가 없어 **세금 0**이다. `ew`는 원장 기준 세금 "
          f"{rep.get('arms', {}).get('ew', {}).get('taxes_total', 0):,}원을 냈다 — "
          "리밸런싱의 실비용이고, 위 차이에 이미 들어 있다.",
          "- ⚠️ 이 리포트는 코어를 바꾸라는 제안이 아니다. 판정 가능한 비교를 처음 만든 것이다.", ""]

    out = ROOT / "docs" / "reports" / "core_book_neutral_bench_20260918.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
