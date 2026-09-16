#!/usr/bin/env python3
"""비신호 밤 인버스 — 노는 자본 230밤을 쓸 수 있나 (read-only, 2026-09-17).

운영자 지시: *"돈 버는 전략을 찾아야 할 거 아냐"* — 측정만 쌓지 말고 레버를 찾으라는 것.

## 왜 이 판인가

살아있는 축은 코스닥 지수 야간 프리미엄 하나뿐이고, 그 병목은 **가동률(연 20회)**이다.
문턱 완화 2칸(갭·종가)은 09-12에 기각했고, 비신호 밤 **롱**도 09-14에 전 칸 기각했다.
그런데 그 기각 결과 안에 안 쓴 게 있다 — 비신호 밤 2,208건이 **−0.102%, t −3.31로
유의하게 음수**였다. 롱으로 못 쓰는 값이지 **반대 방향으로도 못 쓴다는 뜻은 아니다.**
개인도 인버스 ETF(251340 KODEX 코스닥150선물인버스)는 그냥 매수할 수 있다.

## 사전 등록 — 문턱을 먼저 박는다 (사후에 내리지 않는다)

패닉 레인과 같은 기준을 쓴다.
  · **t ≥ 2.0** (밤 1건 = 날짜 1건이라 클러스터 문제 없음, 단순 1표본 t)
  · **연도 부호 11년 중 9년 이상 양수**
  · **무조건 인버스(모든 밤)보다 나아야 한다** — 조건이 값을 더해야 조건이다
  · 비용 왕복 **0.05%**, 그리고 **0.10% 민감도도 같이 낸다**(아래 함정 ②)
  · 파생형 ETF 배당소득세 15.4%는 금액 환산에만 적용(이익에만)

## 이 판이 틀릴 수 있는 경로 셋 — 미리 적어둔다

**① 우리는 문서화된 프리미엄에 반대로 선다.** 코스닥 ETF는 10.5년 오버나이트가 크게
양수였다. 밤을 체계적으로 파는 건 그 프리미엄을 거슬러 가는 것이다. 비신호 밤이
음수라는 **조건부** 주장이 맞아야만 성립한다.

**② −0.102%는 스프레드 잔해일 수 있다.** 종가→익일시가는 호가 스프레드를 통과한다.
롱에서 마이너스로 보이는 게 인버스에서 플러스로 뒤집히지 않고 **양쪽 다 마이너스**일 수
있다. 그래서 비용 0.05%와 0.10% 두 가지로 낸다. 0.10%에서 죽으면 그건 엣지가 아니라 마찰이다.

**③ 합성 ≠ 실물.** 여기 1x 인버스 수익률은 −1 × 229200(1x 롱)로 **합성**한 값이다.
251340은 선물인버스라 롤·보수(연 0.64% 안팎)·추적오차가 붙는다. 그래서 실물 251340
데이터가 있는 2025-05~2026-09 구간으로 **부호 일치를 따로 확인**한다. 합성만으로는 등록하지 않는다.
"""
from __future__ import annotations

import csv
import math
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "analysis" / "kr_index_etf_prices.csv"
REAL_INV = ROOT / "data" / "price" / "kr" / "kr_251340.csv"

GAP_THR, CLOSE_THR = -1.0, -2.5      # 패닉 v2 신호 정의(그대로 재사용)
COSTS = (0.05, 0.10)                 # 왕복 비용 시나리오
T_BAR = 2.0
SIGN_YEARS_BAR = 9
EXPENSE_ANNUAL = 0.64                # 251340 총보수 근사(%/년) — 합성에 참고로만 표기


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def one_sample_t(v):
    if len(v) < 3 or st.stdev(v) == 0:
        return None
    return st.mean(v) / (st.stdev(v) / math.sqrt(len(v)))


def load_nights():
    """(date, 신호여부, 롱 1박 수익률%) — 229200(1x) 종가 → 익일 시가."""
    rows = list(csv.DictReader(CACHE.open(encoding="utf-8-sig")))
    rows.sort(key=lambda r: r["date"])
    out = []
    for i in range(len(rows) - 1):
        c, nxt = rows[i], rows[i + 1]
        close, nopen = f(c.get("229200_close")), f(nxt.get("229200_open"))
        if not close or not nopen:
            continue
        chg, gap = f(c.get("kq_chg")), f(c.get("kq_gap"))
        if chg is None:
            continue
        sig = (chg <= CLOSE_THR) or (gap is not None and gap <= GAP_THR)
        out.append((c["date"], sig, (nopen / close - 1) * 100))
    return out


def block(name, vals, years, cost, L):
    """vals = 인버스 net(%) 리스트, years = 같은 순서의 연도."""
    if not vals:
        L.append(f"| {name} | 0 | - | - | - | - | 표본 없음 |")
        return None
    t = one_sample_t(vals)
    by_year = defaultdict(list)
    for y, v in zip(years, vals):
        by_year[y].append(v)
    pos = sum(1 for y in sorted(by_year) if st.mean(by_year[y]) > 0)
    ny = len(by_year)
    ok = (t is not None and t >= T_BAR and pos >= SIGN_YEARS_BAR)
    L.append(f"| {name} | {len(vals):,} | {st.mean(vals):+.4f}% | "
             f"{t:+.2f} | {pos}/{ny} | {sum(vals)*1:+.1f}%p | "
             f"{'**통과**' if ok else '기각'} |" if t is not None else
             f"| {name} | {len(vals):,} | {st.mean(vals):+.4f}% | - | {pos}/{ny} | - | 기각 |")
    return {"t": t, "pos": pos, "ny": ny, "mean": st.mean(vals), "ok": ok}


def main():
    nights = load_nights()
    L = ["# 비신호 밤 인버스 — 노는 자본 230밤을 쓸 수 있나 (2026-09-17)", "",
         "read-only. `tools/research/research_kq_offnight_inverse_20260917.py`(사전등록·함정은 docstring).",
         f"합성 1x 인버스 = −1 × 229200(1x 롱) 종가→익일시가. **사전 문턱: t ≥ {T_BAR} · "
         f"연도 부호 {SIGN_YEARS_BAR}/11 이상 · 무조건 인버스보다 우위.**", "",
         f"밤 표본 {len(nights):,}개 · {nights[0][0]} ~ {nights[-1][0]}", ""]

    for cost in COSTS:
        sig = [(d, -r - cost) for d, s, r in nights if s]
        off = [(d, -r - cost) for d, s, r in nights if not s]
        alln = [(d, -r - cost) for d, _, r in nights]
        L += [f"## 왕복 비용 {cost:.2f}% 가정", "",
              "| 구간 | 밤 수 | 평균 net | t | 연도 양수 | 누적 | 판정 |",
              "|---|---:|---:|---:|---:|---:|---|"]
        res = {}
        for nm, arr in (("**비신호 밤 인버스**(이 판)", off),
                        ("무조건 인버스(대조)", alln),
                        ("신호 밤 인버스(패닉의 반대편)", sig)):
            res[nm] = block(nm, [v for _, v in arr], [d[:4] for d, _ in arr], cost, L)
        L.append("")
        o, a = res["**비신호 밤 인버스**(이 판)"], res["무조건 인버스(대조)"]
        if o and a:
            L.append(f"조건 기여: 비신호 {o['mean']:+.4f}% vs 무조건 {a['mean']:+.4f}% → "
                     f"**{o['mean']-a['mean']:+.4f}%p**"
                     f"{' (조건이 값을 더한다)' if o['mean'] > a['mean'] else ' (조건이 값을 못 더한다)'}")
        L.append("")

    # ── 실물 251340 부호 확인 ──
    L += ["## 실물 251340 부호 확인 — 합성만으로는 등록하지 않는다", ""]
    if not REAL_INV.exists():
        L.append("251340 가격 파일 없음 — 확인 불가.")
    else:
        rr = list(csv.DictReader(REAL_INV.open(encoding="utf-8-sig")))
        rr.sort(key=lambda r: r["date"])
        real = {}
        for i in range(len(rr) - 1):
            c, n = rr[i], rr[i + 1]
            cl, op = f(c.get("close")), f(n.get("open"))
            if cl and op:
                real[c["date"]] = (op / cl - 1) * 100
        sigmap = {d: s for d, s, _ in nights}
        synth = {d: -r for d, _, r in nights}
        pairs = [(d, real[d], synth[d], sigmap[d]) for d in real if d in synth and d in sigmap]
        if len(pairs) < 30:
            L.append(f"겹치는 밤 {len(pairs)}개 — 확인에 부족하다.")
        else:
            offr = [(a, b) for _, a, b, s in pairs if not s]
            L += [f"겹치는 밤 **{len(pairs)}개**({min(d for d,_,_,_ in pairs)} ~ "
                  f"{max(d for d,_,_,_ in pairs)}) · 비신호 밤 {len(offr)}개", "",
                  "| 항목 | 실물 251340 | 합성(−1×229200) | 차이 |", "|---|---:|---:|---:|"]
            for nm, idx in (("비신호 밤 평균(비용 전)", None),):
                rm = st.mean(a for a, _ in offr)
                sm = st.mean(b for _, b in offr)
                L.append(f"| {nm} | {rm:+.4f}% | {sm:+.4f}% | {rm-sm:+.4f}%p |")
            drag = (st.mean(a for a, _ in offr) - st.mean(b for _, b in offr))
            L += ["", f"실물−합성 차이 {drag:+.4f}%/밤 ≈ 연 {drag*250:+.2f}%p "
                  f"(총보수 근사 −{EXPENSE_ANNUAL}%/년 + 롤·추적오차).",
                  "**합성이 실물보다 좋게 나오면 그 차이만큼 합성 결과를 깎아서 읽어야 한다.**", ""]

    L += ["## 결론 규칙", "",
          f"위 표의 **비신호 밤 인버스**가 비용 0.10%에서도 t ≥ {T_BAR}이고 연도 부호 "
          f"{SIGN_YEARS_BAR}/11 이상이며 무조건 인버스를 이길 때만 forward 등록 대상이다.",
          "하나라도 미달이면 기각이고, 문턱은 사후에 내리지 않는다."]

    out = ROOT / "docs" / "reports" / "kq_offnight_inverse_20260917.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
