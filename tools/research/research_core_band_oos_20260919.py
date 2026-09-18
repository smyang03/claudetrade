#!/usr/bin/env python3
"""밴드 리밸런싱 OOS — 2022 약세장에서 부호가 유지되는가 (read-only, 2026-09-19).

## 검정하려는 예측

09-19 귀속에서 나온 결론은 **"리밸런싱은 방어 구조다"**였다 —
잔차 = α + β×시장에서 β가 −0.105~−0.152(현금으로는 −0.014밖에 설명 안 됨),
대조군 부호로 나눠도 세 arm 모두 **상승 구간에서 지고 하락 구간에서 이겼다.**

그 결론이 맞다면 **떨어지는 해에는 밴드 arm이 정적 방치를 더 크게 이겨야 한다.**
이건 인샘플에서 뽑은 구조가 만든 **반증 가능한 예측**이고, 다른 창에서 시험할 수 있다.

## 창 — 있는 데이터의 한계를 먼저 적는다

인샘플은 2023-01~2026-09였다. 진짜 OOS는 그 이전인데,
**5종 전부 있는 건 2021-12-15부터다**(411060 ACE KRX금현물 상장일). 그래서:

  · **OOS-A (5종, 엄격)** — 2021-12-15 ~ 2022-12-31. 짧지만(약 1년) 인샘플 완전 밖이고
    2022는 주식·채권이 같이 빠진 드문 해라 방어 구조를 시험하기에 오히려 좋다.
  · **OOS-B (3종, 긴 창)** — 069500·305080·360750만으로 2020-08-07 ~ 2022-12-31.
    유니버스가 달라 수익 수준은 인샘플과 직접 비교 못 하지만,
    **밴드 vs 정적의 부호**는 비교할 수 있다.

⚠️ 둘 다 표본이 작다. 여기서 확인하는 건 크기가 아니라 **부호**다.

## 사전 등록

  · 비교는 **밴드 ±8/10/12%(인샘플 고원) vs 정적 방치**. 폭을 새로 찾지 않는다.
  · 통과 조건: **OOS-A에서 고원 3칸의 정적 대비가 전부 ≥ 0**,
    그리고 **낙폭이 정적보다 작을 것**(방어 주장의 직접 내용).
  · 하나라도 어긋나면 "방어 구조" 주장은 그 크기만큼 약해진 것으로 적는다.
    문턱을 사후에 내리지 않는다.
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research.research_core_band_width_sweep_20260919 import (  # noqa: E402
    CAPITAL, FEE_SIDE, TAX, TAX_FREE, apply_orders, mdd,
)

CACHE = ROOT / "data" / "analysis" / "core_etf_prices.csv"
FULL5 = ["379810", "360750", "411060", "305080", "069500"]
TRIO = ["360750", "305080", "069500"]
PLATEAU = [0.08, 0.10, 0.12]        # 인샘플 고원 — 새로 찾지 않는다


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load(univ, lo, hi):
    out = {}
    with CACHE.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            d = r["date"]
            if not (lo <= d <= hi):
                continue
            px = {t: f(r.get(t)) for t in univ}
            if all(v and v > 0 for v in px.values()):
                out[d] = px
    return out


def nav_of(cash, hold, px):
    return cash + sum(v["qty"] * px[t] for t, v in hold.items())


def simulate(prices, dates, univ, band=None, monthly=False):
    tgt = 1.0 / len(univ)
    cash, hold = CAPITAL, {}
    fees = taxes = 0.0
    rebs = 0
    navs = []
    cur_month = None
    for d in dates:
        px = prices[d]
        need = False
        if not hold:
            need = True
        elif monthly:
            need = cur_month != d[:7]
        elif band is not None:
            nav = nav_of(cash, hold, px)
            if nav > 0:
                for t in univ:
                    w = hold.get(t, {"qty": 0})["qty"] * px[t] / nav
                    if abs(w - tgt) >= band:
                        need = True
                        break
        if need:
            nav = nav_of(cash, hold, px)
            tq = {t: int(math.floor(nav * tgt / px[t])) for t in univ}
            cash, hold, fe, tx = apply_orders(cash, hold, tq, px)
            fees += fe
            taxes += tx
            rebs += 1
            cur_month = d[:7]
        navs.append((d, nav_of(cash, hold, px)))
    return {"navs": navs, "rebs": rebs, "fees": fees, "taxes": taxes,
            "ret": (navs[-1][1] / CAPITAL - 1) * 100, "mdd": mdd(navs)}


def block(L, title, univ, lo, hi, note):
    prices = load(univ, lo, hi)
    dates = sorted(prices)
    if len(dates) < 100:
        L += [f"## {title}", "", f"거래일 {len(dates)}일 — 부족해서 돌리지 않는다.", ""]
        return None
    static = simulate(prices, dates, univ)
    L += [f"## {title}", "", note, "",
          f"종목 {len(univ)}종 · 거래일 {len(dates)}일 · {dates[0]} ~ {dates[-1]}", "",
          "| 설정 | 리밸런스 | 총수익 | 최대낙폭 | 수익/낙폭 | **정적 대비 수익** | **정적 대비 낙폭** |",
          "|---|---:|---:|---:|---:|---:|---:|",
          f"| **정적 방치** | {static['rebs']} | {static['ret']:+.1f}% | {static['mdd']:.1f}% | "
          f"{static['ret']/abs(static['mdd']):.2f} | — | — |"]
    res = []
    for w in PLATEAU:
        s = simulate(prices, dates, univ, band=w)
        res.append((w, s))
        L.append(f"| 밴드 ±{w*100:.0f}% | {s['rebs']} | {s['ret']:+.1f}% | {s['mdd']:.1f}% | "
                 f"{s['ret']/abs(s['mdd']):.2f} | **{s['ret']-static['ret']:+.1f}%p** | "
                 f"**{s['mdd']-static['mdd']:+.1f}%p** |")
    m = simulate(prices, dates, univ, monthly=True)
    L.append(f"| 월간(대조) | {m['rebs']} | {m['ret']:+.1f}% | {m['mdd']:.1f}% | "
             f"{m['ret']/abs(m['mdd']):.2f} | {m['ret']-static['ret']:+.1f}%p | "
             f"{m['mdd']-static['mdd']:+.1f}%p |")
    L.append("")
    return static, res


def main():
    L = ["# 밴드 리밸런싱 OOS — 2022 약세장에서 부호가 유지되는가 (2026-09-19)", "",
         "read-only. `tools/research/research_core_band_oos_20260919.py`(사전등록은 docstring).",
         "인샘플(2023-01~2026-09)에서 나온 **\"리밸런싱은 방어 구조(β<0)\"** 예측을 시험한다.",
         "**폭은 인샘플 고원(±8/10/12%)을 그대로 쓴다 — 새로 찾지 않는다.**", ""]

    a = block(L, "OOS-A · 5종 엄격 (2021-12-15 ~ 2022-12-31)", FULL5,
              "2021-12-15", "2022-12-31",
              "인샘플 완전 밖. 짧지만 **2022는 주식·채권이 같이 빠진 해**라 방어 주장을 직접 시험한다.")
    b = block(L, "OOS-B · 3종 긴 창 (2020-08-07 ~ 2022-12-31)", TRIO,
              "2020-08-07", "2022-12-31",
              "금현물·나스닥100이 없어 유니버스가 다르다. 수익 수준이 아니라 **부호**만 본다.")

    L += ["## 사전 등록한 판정", ""]
    if a:
        static, res = a
        gains_ok = all(s["ret"] - static["ret"] >= 0 for _, s in res)
        dd_ok = all(s["mdd"] > static["mdd"] for _, s in res)   # 낙폭은 음수 — 더 얕아야 함
        detail = " · ".join(f"±{w*100:.0f}% {s['ret']-static['ret']:+.1f}%p" for w, s in res)
        dd_detail = " · ".join(f"±{w*100:.0f}% {s['mdd']-static['mdd']:+.1f}%p" for w, s in res)
        L += ["통과 조건은 둘이었다 — **고원 3칸이 전부 정적 이상**이고 **낙폭이 정적보다 얕을 것.**", "",
              f"- 수익: {'**3칸 전부 정적 이상 — 통과**' if gains_ok else '**미달**'} ({detail})",
              f"- 낙폭: {'**3칸 전부 정적보다 얕음 — 통과**' if dd_ok else '**미달**'} ({dd_detail})", "",
              f"→ **{'방어 구조 주장이 OOS에서 유지된다.' if (gains_ok and dd_ok) else '방어 구조 주장은 그만큼 약해진 것으로 적는다.'}**", ""]
    L += ["⚠️ 한계를 그대로 적는다. OOS-A는 약 1년, OOS-B는 유니버스가 다르다.",
          "여기서 보는 건 크기가 아니라 **부호**이고, 이걸로 인샘플 효과 크기를 확인한 것은 아니다.", "",
          "## OOS가 공백인 이유 — 그리고 그게 인샘플까지 무너뜨린다", "",
          "OOS에서 밴드가 **한 번도 안 걸렸다**(리밸런스 1 = 최초 매수뿐). 2022엔 5자산이",
          "**같이** 빠져서 상대 비중이 ±8% 이상 벌어지지 않았기 때문이다.",
          "밴드는 자산이 **서로 갈라질 때** 걸리지, 같이 떨어질 때는 안 걸린다.", "",
          "→ **여기서 '리밸런싱이 하락장을 방어한다'는 설명이 깨진다.** 실제 하락장에서",
          "  장치가 작동조차 하지 않았다. 09-19 귀속의 β<0은 방어 능력이 아니라",
          "  **정적 북이 승자(069500)로 쏠려 베타가 커진 것의 거울상**이다.", "",
          "### 인샘플 우위는 몇 번의 거래에서 나왔나", "",
          "| 폭 | 실제 조정 | 정적 대비 | **조정 1회당** |", "|---:|---:|---:|---:|",
          "| ±4% | 8회 | −7.5%p | −0.9%p |",
          "| ±6% | 5회 | −1.5%p | −0.3%p |",
          "| ±8% | **4회** | +6.4%p | +1.6%p |",
          "| ±10% | **2회** | +4.5%p | +2.2%p |",
          "| ±12% | **1회** | +5.1%p | **+5.1%p** |", "",
          "**±12%의 +5.1%p 전부가 단 한 번의 거래(2026-06-01, 069500을 32.6%→20%로 축소)에서 나왔다.**",
          "그리고 ±8/10/12는 독립된 세 칸이 아니라 **2026년 중반의 같은 거래를 공유**한다",
          "(±8% 2026-06-19 · ±10% 2026-06-18 · ±12% 2026-06-01).", "",
          "조정 횟수가 많은 칸(±4% 8회, ±6% 5회)은 **음수**다. 즉 표본이 쌓이는 쪽은 마이너스고,",
          "표본이 1~4개인 쪽만 플러스다. 이건 구조의 모양이 아니라 **작은 n의 모양**이다.", "",
          "### 판정 정정", "",
          "09-19 스윕에서 나는 ±8/10/12가 뭉친 것을 보고 **\"고원 = 구조, 과최적화 아님\"**으로 적었다.",
          "**그 판정을 철회한다.** 세 칸은 독립 확인이 아니라 같은 1~4개 거래의 다른 절단면이고,",
          "OOS에서는 장치가 발동조차 하지 않아 부호를 시험할 기회 자체가 없었다.",
          "지금 말할 수 있는 것은 **\"인샘플에서 리밸런싱을 적게 할수록 좋았다\"**까지이고,",
          "그 이상은 표본이 없다.", "",
          "⚠️ 배분 변경은 운영자 결정이다. 이 정정은 `ew_band8`을 끄라는 뜻이 아니라,",
          "**+6.4%p를 검증된 엣지로 인용하지 말라**는 뜻이다.", ""]

    out = ROOT / "docs" / "reports" / "core_band_oos_20260919.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
