#!/usr/bin/env python3
"""회전율-비용 축 — 모든 레인을 한 자로 (read-only, 2026-09-19).

## 왜

같은 얼굴이 세 번 나왔다.
  · 코어 북: 리밸런스 45회 arm은 방치에 −12.4%p / −27.8%p, **5회짜리만 +6.4%p로 이긴다**
  · 무조건 야간: 비용 0.05%면 연 +7.77%p, **0.10%면 −4.73%p** — 비용이 부호를 정한다
  · 패닉 레인 매일 걷기: 2x·비용 0.10%에서 연 −0.13%p, 정확히 "비용이 다 먹는다"

세 번 같은 구조가 나오면 그건 개별 레인 문제가 아니라 **축**이다. 그런데 우리는
레인마다 다른 단위로 재왔다(건당 초과 / 연 %p / nav %). 한 자로 맞춘다.

## 핵심 지표 — 손익분기 왕복비용

각 레인에 대해:
  · **연 왕복 횟수** — 자본이 1년에 몇 번 들락거리나
  · **비용 전 연수익(%p)** — 마찰이 0일 때의 수익
  · **손익분기 왕복비용** = 비용 전 연수익 ÷ 연 왕복 횟수
      → "왕복당 이만큼을 넘게 내면 이 레인은 0이 된다"
  · **안전여유** = 손익분기 비용 ÷ 실제 가정 비용 (배수)

안전여유가 1 미만이면 이미 죽은 것이고, **2 미만이면 비용 가정 하나에 결론이 뒤집힌다.**
우리가 쓰는 비용은 추정치(스프레드·슬리피지 포함)라 2배 미만은 신뢰 구간 안이다.

## 규약

- 수치는 **각 레인의 정본 원장/리포트**에서 읽는다. 내가 재계산하지 않는다.
- 비용 전 수익을 못 복원하는 레인은 **추정하지 않고 '복원 불가'로 적는다.**
- 코어 북은 왕복 개념이 다르다(리밸런싱은 부분 교체). 그래서 **회전액 기준**으로
  따로 처리하고 같은 표에 억지로 넣지 않는다.

⚠️ 이 리포트는 레인을 끄라는 제안이 아니다. **비용 민감도라는 공통 축을 처음 만드는 것**이다.
"""
from __future__ import annotations

import csv
import json
import math
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PANIC_REP = ROOT / "data" / "analysis" / "kr_index_etf_panic_report.json"
CORE_REP = ROOT / "data" / "analysis" / "core_etf_book_report.json"
KQ_CACHE = ROOT / "data" / "analysis" / "kr_index_etf_prices.csv"

SESSIONS_PER_YEAR = 250.0


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def kq_nights(ticker: str):
    """(date, 1박 수익률%) — 종가 → 익일 시가. 비용 전."""
    rows = sorted(csv.DictReader(KQ_CACHE.open(encoding="utf-8-sig")),
                  key=lambda r: r["date"])
    out = []
    for i in range(len(rows) - 1):
        c, n = rows[i], rows[i + 1]
        cl, op = f(c.get(f"{ticker}_close")), f(n.get(f"{ticker}_open"))
        if cl and op:
            out.append((c["date"], (op / cl - 1) * 100))
    return out


def main():
    lanes = []

    # ── 1. 패닉 ETF v2 (정본 리포트) ──
    rep = json.loads(PANIC_REP.read_text(encoding="utf-8"))
    cost = float(rep["cost"])
    for tk in ("233740", "229200"):
        b = rep["by"][tk]["backfill"]
        n = int(b["n"])
        # excess_mean = overnight − 그해 무조건 평균. **비용 차감 전**이다
        # (net_mean = overnight − cost로 따로 있다).
        gross_per_trade = float(b["excess_mean"])
        nights = kq_nights(tk)
        years = len({d[:4] for d, _ in nights}) or 1
        per_year = n / years
        lanes.append({
            "name": f"패닉 ETF v2 `{tk}`", "trips": per_year,
            "gross_year": gross_per_trade * per_year, "cost": cost,
            "src": "kr_index_etf_panic_report.json backfill.excess_mean",
        })

    # ── 2. 무조건 야간 보유 (같은 캐시에서 직접) ──
    for tk in ("229200", "233740"):
        nights = kq_nights(tk)
        if not nights:
            continue
        years = len({d[:4] for d, _ in nights}) or 1
        per_year = len(nights) / years
        lanes.append({
            "name": f"무조건 야간 보유 `{tk}`", "trips": per_year,
            "gross_year": st.mean(v for _, v in nights) * per_year, "cost": cost,
            "src": "kr_index_etf_prices.csv 종가→익일시가",
        })

    # ── 3. 무조건 인트라데이 (대조 — 구조적 음수) ──
    rows = sorted(csv.DictReader(KQ_CACHE.open(encoding="utf-8-sig")), key=lambda r: r["date"])
    for tk in ("229200",):
        intra = [(r["date"], (f(r.get(f"{tk}_close")) / f(r.get(f"{tk}_open")) - 1) * 100)
                 for r in rows if f(r.get(f"{tk}_open")) and f(r.get(f"{tk}_close"))]
        years = len({d[:4] for d, _ in intra}) or 1
        lanes.append({
            "name": f"무조건 인트라데이 `{tk}` (대조)", "trips": len(intra) / years,
            "gross_year": st.mean(v for _, v in intra) * (len(intra) / years), "cost": cost,
            "src": "kr_index_etf_prices.csv 시가→종가",
        })

    L = ["# 회전율-비용 축 — 모든 레인을 한 자로 (2026-09-19)", "",
         "read-only. `tools/research/research_turnover_cost_axis_20260919.py`(규약은 docstring).",
         "**손익분기 왕복비용** = 비용 전 연수익 ÷ 연 왕복 횟수. "
         "**안전여유** = 손익분기 ÷ 실제 비용.", "",
         "안전여유 < 1이면 이미 죽은 것이고, **< 2면 비용 가정 하나에 결론이 뒤집힌다**"
         "(우리 비용은 스프레드·슬리피지를 포함한 추정치다).", "",
         "| 레인 | 연 왕복 | 비용 전 연수익 | 실제 비용 | 연 비용부담 | 비용 후 | **손익분기 비용** | **안전여유** |",
         "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for x in sorted(lanes, key=lambda z: -(z["gross_year"] / max(z["trips"], 1e-9) / z["cost"])):
        be = x["gross_year"] / x["trips"] if x["trips"] else 0.0
        margin = be / x["cost"] if x["cost"] else None
        burden = x["trips"] * x["cost"]
        netto = x["gross_year"] - burden
        flag = ("**죽음**" if margin is not None and margin < 1
                else "**취약**" if margin is not None and margin < 2 else "견고")
        L.append(f"| {x['name']} | {x['trips']:.0f}회 | {x['gross_year']:+.2f}%p | "
                 f"{x['cost']:.2f}% | −{burden:.2f}%p | {netto:+.2f}%p | "
                 f"**{be:.3f}%** | **{margin:.1f}배** {flag} |")

    # ── 4. 코어 북 — 왕복이 아니라 회전액 ──
    core = json.loads(CORE_REP.read_text(encoding="utf-8"))
    cap = float(core["capital"])
    L += ["", "## 코어 북 — 왕복이 아니라 **회전액**으로 본다", "",
          "리밸런싱은 전량 교체가 아니라 부분 교체라 '왕복 횟수'가 같은 뜻이 아니다.",
          "실제로 낸 수수료·세금을 자본 대비로 환산해 **마찰 실비**를 낸다.", "",
          "| arm | 리밸런스 | 수수료 | 세금 | 마찰 합계 | 자본 대비 | 연 환산 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for a, v in core["arms"].items():
        fees, tax = float(v.get("fees_total") or 0), float(v.get("taxes_total") or 0)
        tot = fees + tax
        from datetime import date
        yrs = max((date.fromisoformat(v["last"]) - date.fromisoformat(v["start"])).days / 365.25, 1e-9)
        L.append(f"| `{a}` | {v.get('rebalances')}회 | {fees:,.0f}원 | {tax:,.0f}원 | "
                 f"{tot:,.0f}원 | {tot/cap*100:.2f}% | **{tot/cap*100/yrs:.2f}%p/년** |")

    # 마찰만으로 설명되나? (정적 대비 격차 vs 마찰)
    gap = {"ew": -12.4, "absmom": -27.8, "ew_band8": +6.4}     # 중립벤치 리포트 실측
    L += ["", "### 마찰이 격차를 다 설명하나 — 아니다", "",
          "중립 벤치(정적 동일가중) 대비 격차를 마찰과 나란히 놓는다. "
          "마찰로 설명 안 되는 부분이 **타이밍 손실**(오르는 걸 팔아치운 값)이다.", "",
          "| arm | 정적 대비 | 마찰(전 기간) | **타이밍 잔차** |", "|---|---:|---:|---:|"]
    for a, v in core["arms"].items():
        if a not in gap:
            continue
        tot = (float(v.get("fees_total") or 0) + float(v.get("taxes_total") or 0)) / cap * 100
        L.append(f"| `{a}` | {gap[a]:+.1f}%p | −{tot:.2f}%p | **{gap[a] + tot:+.1f}%p** |")

    L += ["", "## 읽는 법 — 그리고 내가 틀렸던 것", "",
          "**① 저가동률은 병목이 아니라 강점의 원천이었다.** 그동안 패닉 레인의 문제를",
          "\"연 20회밖에 안 나온다\"로 봤는데, 거꾸로다. 연 21회라서 비용 부담이 −1.03%p뿐이고",
          "건당 엣지 0.706%가 비용의 **14배**다. 무조건 야간은 연 215회를 돌려 비용만",
          "−10.8%p를 내고 안전여유가 2.0배로 떨어진다.",
          "→ **가동률 상향은 안전여유를 깎는 거래다.** 문턱 완화·비신호 밤·지수 확장이",
          "  전부 기각된 건 우연이 아니라 이 축의 필연이다.", "",
          "**② 손익분기 비용이 레인의 진짜 체력이다.** 안전여유 2 미만이면 '엣지'가 아니라",
          "마찰과 같은 크기의 잔차일 수 있다 — 우리 비용은 스프레드·슬리피지 포함 추정치다.", "",
          "**③ 코어의 격차는 마찰만으로 설명되지 않는다.** 잔차가 타이밍 성분인데,",
          "⚠️ **이걸 '타이밍 능력'으로 읽으면 안 된다.** 2023~2026은 강세 구간이고,",
          "이 구간에서 양수 잔차는 \"덜 팔아서 추세를 탔다\"와 구분되지 않는다.",
          "`ew_band8`의 +9.9%p는 밴드가 우수하다는 증거가 아니라 **국면 의존 결과**다.",
          "하락·횡보 구간에서 부호가 유지되는지는 미검정이고, 그게 다음 판이다.", "",
          "- ⚠️ 레인을 끄라는 제안이 아니다. 비용 민감도라는 공통 축을 만든 것이다.", ""]

    out = ROOT / "docs" / "reports" / "turnover_cost_axis_20260919.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
