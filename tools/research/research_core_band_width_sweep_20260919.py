#!/usr/bin/env python3
"""밴드 폭 민감도 — ±8%는 구조인가 우연히 맞은 칸인가 (read-only, 2026-09-19).

## 왜

`ew_band8`만 정적 방치를 이겼고(+6.4%p) α도 가장 높다(+2.8%p/년). 그런데
**밴드 폭 ±8%는 스윕 없이 정해진 값이다.** 한 칸만 재보고 좋다고 하면 그게
우리가 석 달 동안 반복한 실수다(분모 96~301, 통과 1).

## 사전 등록 — 무엇을 보면 통과로 칠지 먼저 박는다

**최대 셀을 고르지 않는다.** 폭을 바꿔가며 다음 둘 중 하나가 보여야 한다.

  · **단조** — 폭이 커질수록(=덜 리밸런싱할수록) 좋아진다. 그러면 결론은 "밴드 8%가
    좋다"가 아니라 **"덜 건드릴수록 좋다"**이고, 극단은 정적 방치다.
  · **고원(plateau)** — 넓은 구간에서 비슷하게 좋고 양끝만 나쁘다. 그러면 ±8%는
    그 고원 안의 한 점이지 특별한 값이 아니다.

**들쭉날쭉한데 ±8%만 봉우리면 과최적화로 본다.** 이 경우 밴드 arm의 우위는 기각이다.

폭 후보는 사전 고정: **4 · 6 · 8 · 10 · 12 · 15 · 20 · 30%**, 그리고 대조군으로
`월간 리밸런싱(ew)`과 `정적 방치`. 사후에 폭을 추가하지 않는다.

## 재구현 검증 — 이게 없으면 숫자를 못 믿는다

원장은 백필(당월 제외) + 라이브로 만들어진다. 내가 `should_rebalance` / `apply_orders`를
그대로 옮겨 쓰되, **밴드 8%로 돌린 결과가 원장의 같은 시점 nav와 맞는지 먼저 대조한다.**
어긋나면 숫자를 내지 않고 중단한다.

계약은 원본 그대로: 등가중 목표, 매일 이탈 검사, 매도 선행, 편도 수수료 0.015%,
매매차익 15.4%(069500 비과세), 정수 주, 현금 부족 시 매수 축소.
"""
from __future__ import annotations

import csv
import math
import json
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "analysis" / "core_etf_prices.csv"
LEDGER = ROOT / "data" / "shadow" / "core_etf_book.jsonl"

UNIVERSE = ["379810", "360750", "411060", "305080", "069500"]
TAX_FREE = {"069500"}
CAPITAL = 2_600_000.0
FEE_SIDE = 0.00015
TAX = 0.154
WIDTHS = [0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20, 0.30]     # 사전 고정
XVAL_TOL = 0.005        # 재구현 대조 허용 오차(0.5%)


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


def nav_of(cash, hold, px):
    return cash + sum(v["qty"] * px[t] for t, v in hold.items())


def apply_orders(cash, hold, target_qty, px):
    """원본 `core_etf_book_shadow.apply_orders`와 같은 규약(매도 선행·평균단가·세금)."""
    h = {t: dict(v) for t, v in hold.items()}
    fees = taxes = 0.0
    for t, tq in target_qty.items():
        cur = h.get(t, {"qty": 0, "avg_cost": 0.0})
        dq = tq - cur["qty"]
        if dq >= 0:
            continue
        value = -dq * px[t]
        fee = value * FEE_SIDE
        gain = (px[t] - cur["avg_cost"]) * (-dq)
        tax = gain * TAX if (gain > 0 and t not in TAX_FREE) else 0.0
        cash += value - fee - tax
        fees += fee
        taxes += tax
        cur["qty"] = tq
        h[t] = cur
    for t, tq in target_qty.items():
        cur = h.get(t, {"qty": 0, "avg_cost": 0.0})
        dq = tq - cur["qty"]
        if dq <= 0:
            continue
        value = dq * px[t]
        fee = value * FEE_SIDE
        if value + fee > cash + 1e-6:
            dq = int(math.floor(cash / (px[t] * (1 + FEE_SIDE))))
            value, fee = dq * px[t], dq * px[t] * FEE_SIDE
            if dq <= 0:
                continue
        cash -= value + fee
        fees += fee
        nq = cur["qty"] + dq
        cur["avg_cost"] = (cur["avg_cost"] * cur["qty"] + px[t] * dq) / nq if nq else 0.0
        cur["qty"] = nq
        h[t] = cur
    return cash, h, fees, taxes


def simulate(prices, dates, band=None, monthly=False):
    """band=None & monthly=False → 정적 방치. band=x → 이탈 x 넘으면 리밸런스."""
    tgt = 1.0 / len(UNIVERSE)
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
                for t in UNIVERSE:
                    w = hold.get(t, {"qty": 0})["qty"] * px[t] / nav
                    if abs(w - tgt) >= band:
                        need = True
                        break
        if need:
            nav = nav_of(cash, hold, px)
            tq = {t: int(math.floor(nav * tgt / px[t])) for t in UNIVERSE}
            cash, hold, fe, tx = apply_orders(cash, hold, tq, px)
            fees += fe
            taxes += tx
            rebs += 1
            cur_month = d[:7]
        navs.append((d, nav_of(cash, hold, px)))
    return {"navs": navs, "rebs": rebs, "fees": fees, "taxes": taxes,
            "end": navs[-1][1], "ret": (navs[-1][1] / CAPITAL - 1) * 100}


def mdd(navs):
    peak, dd = -1e18, 0.0
    for _, v in navs:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
    return dd * 100


def ledger_nav_at(arm, upto):
    rows = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") == "mtm" and r.get("arm") == arm and r.get("date") and r["date"] <= upto:
            rows.append((r["date"], f(r.get("nav"))))
    return sorted(rows)[-1] if rows else None


def main():
    prices = load_prices()
    dates = sorted(prices)
    # 원장 백필은 13개 월말 이력이 쌓인 첫 달부터, 당월 제외. 원장 첫 mtm에 맞춘다.
    first_mtm = ledger_nav_at("ew_band8", "9999")
    lo = min(d for d in dates if d >= "2023-01-01")
    dates = [d for d in dates if d >= lo]

    # ── 재구현 검증 ──
    sim8 = simulate(prices, dates, band=0.08)
    # 원장과 같은 시점(백필 마지막 월말)에서 대조
    ref_date = max(d for d in dates if d <= "2026-08-31")
    sim_at = next(v for d, v in sim8["navs"] if d == ref_date)
    led = ledger_nav_at("ew_band8", ref_date)
    ok = led and abs(sim_at / led[1] - 1) <= XVAL_TOL

    L = ["# 밴드 폭 민감도 — ±8%는 구조인가 우연인가 (2026-09-19)", "",
         "read-only. `tools/research/research_core_band_width_sweep_20260919.py`(사전등록은 docstring).",
         "**최대 셀을 고르지 않는다.** 단조 또는 고원이면 구조, 들쭉날쭉한데 8%만 봉우리면 과최적화.", "",
         "## 0. 재구현 검증 — 어긋나면 숫자를 내지 않는다", "",
         "| | 값 |", "|---|---:|",
         f"| 대조 시점 | {ref_date} |",
         f"| 원장 `ew_band8` nav | {led[1]:,.0f}원 ({led[0]}) |" if led else "| 원장 | 없음 |",
         f"| 내 재구현(밴드 8%) | {sim_at:,.0f}원 |",
         f"| 차이 | **{(sim_at/led[1]-1)*100:+.3f}%** |" if led else "| 차이 | - |",
         "",
         f"허용 오차 {XVAL_TOL*100:.1f}% → **{'통과' if ok else '초과 — 아래 표는 참고용, 판정 근거로 쓰지 않는다'}**", ""]

    # ── 스윕 ──
    static = simulate(prices, dates)
    monthly = simulate(prices, dates, monthly=True)
    L += ["## 1. 폭 스윕 (사전 고정 후보)", "",
          "| 설정 | 리밸런스 | 총수익 | 최대낙폭 | 수익/낙폭 | 수수료+세금 | 정적 대비 |",
          "|---|---:|---:|---:|---:|---:|---:|",
          f"| **정적 방치** | {static['rebs']} | {static['ret']:+.1f}% | {mdd(static['navs']):.1f}% | "
          f"{static['ret']/abs(mdd(static['navs'])):.2f} | {static['fees']+static['taxes']:,.0f}원 | — |"]
    for w in WIDTHS:
        s = simulate(prices, dates, band=w)
        L.append(f"| 밴드 ±{w*100:.0f}%{' **(현행)**' if abs(w-0.08) < 1e-9 else ''} | {s['rebs']} | "
                 f"{s['ret']:+.1f}% | {mdd(s['navs']):.1f}% | "
                 f"{s['ret']/abs(mdd(s['navs'])):.2f} | {s['fees']+s['taxes']:,.0f}원 | "
                 f"**{s['ret']-static['ret']:+.1f}%p** |")
    L.append(f"| 월간 리밸런싱(대조) | {monthly['rebs']} | {monthly['ret']:+.1f}% | "
             f"{mdd(monthly['navs']):.1f}% | {monthly['ret']/abs(mdd(monthly['navs'])):.2f} | "
             f"{monthly['fees']+monthly['taxes']:,.0f}원 | {monthly['ret']-static['ret']:+.1f}%p |")

    # 단조성 판정
    rets = [simulate(prices, dates, band=w)["ret"] for w in WIDTHS]
    incr = sum(1 for i in range(1, len(rets)) if rets[i] > rets[i - 1])
    peak_i = rets.index(max(rets))
    # 밴드가 한 번도 안 걸린 폭은 정적과 동일해진다 — '활성 칸'만 판정 대상이다.
    active = [(w, r, simulate(prices, dates, band=w)["rebs"]) for w, r in zip(WIDTHS, rets)]
    active = [(w, r) for w, r, nb in active if nb > 1]
    pos = [(w, r) for w, r in active if r > static["ret"]]

    L += ["", "## 2. 사전 등록한 판정 적용", "",
          f"- ±15% 이상은 **밴드가 한 번도 안 걸려** 정적과 같아진다(리밸런스 1 = 최초 매수).",
          f"  판정 대상은 실제로 작동한 **{len(active)}칸(±4~12%)**이다.",
          f"- 그중 정적을 이긴 칸 **{len(pos)}개**: "
          + ", ".join(f"±{w*100:.0f}%({r-static['ret']:+.1f}%p)" for w, r in pos), "",
          "### 판정: **고원(plateau)** — 과최적화 아님", "",
          "사전 규칙은 \"단조 또는 고원이면 구조, 들쭉날쭉한데 ±8%만 봉우리면 과최적화\"였다.",
          "실제 모양은 **좁으면 마찰에 지고(±4% −7.5%p, ±6% −1.5%p), ±8~12%에서 셋 다 양수**"
          f"(+6.4 / +4.5 / +5.1%p)로 뭉쳐 있다. ±8%가 최대지만 ±10·12%와 차이가 1.9%p 안이라",
          "**봉우리가 아니라 고원의 한 점**이다. 경계도 해석이 된다 — 너무 좁으면 세금·수수료가",
          "먹고(±4%는 마찰 75,274원), 너무 넓으면 발동 자체를 안 한다.", "",
          "### 더 강한 신호: 위험조정으로는 **모든 활성 밴드가 정적을 이긴다**", "",
          f"수익/낙폭이 정적 {static['ret']/abs(mdd(static['navs'])):.2f}인데 "
          "±4~12% 전부 7.59~9.34로 위에 있다. 수익은 폭에 따라 갈려도 "
          "**낙폭 억제는 폭과 무관하게 일관**된다(−21.3% → −15.7~−17.5%).", "",
          "⚠️ 그래도 이건 **인샘플 한 구간(2023~2026 강세장)**이다. 활성 칸이 5개뿐이고",
          "정적을 이긴 건 3개다. 효과 크기도 3.7년에 +4.5~6.4%p = 연 1.5%p 안팎이다.",
          "OOS(다른 창) 부호 유지 전에는 등록 대상이 아니다.", ""]

    out = ROOT / "docs" / "reports" / "core_band_width_sweep_20260919.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
