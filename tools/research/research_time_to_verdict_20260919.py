#!/usr/bin/env python3
"""판정까지 몇 년 걸리나 — 검증 시계 (read-only, 2026-09-19).

## 운영자 질문

*"결국 지금 쉐도우로 실매수처럼 하는데, 여기서 전략이 나와야 투자를 하든 말든 하는 거지."*

맞다. 그러면 답해야 할 것은 **"이 쉐도우가 전략을 만들어낼 수 있는가, 언제인가"**다.
그건 의견이 아니라 산수다.

## 산수

1표본 t는 `t = 평균 / (표준편차 / √n)`이다. 뒤집으면 목표 t에 필요한 표본이 나온다.

    n = ( t목표 × 표준편차 / 평균 )²  =  ( t목표 / 건당샤프 )²

여기서 **건당 샤프 = 평균 ÷ 표준편차**다. 이 하나가 검증 속도를 전부 결정한다.
수익률이 높아서가 아니라 **비율이 높아야** 빨리 판정된다.

그리고 필요한 t는 우리 분모에 따라 달라진다(09-18 실측: 고유 축 96~301개).

    · t ≥ 2.0 — 보정 없음. 분모를 안 셀 때의 기준.
    · t ≥ 3.0 — 분모 96 보정(Bonferroni 근사).
    · t ≥ 3.5 — 분모 301 보정.

**판정 도달 연수 = 필요 n ÷ 연 이벤트 수.**

## 왜 이게 중요한가

레인마다 "언제 결론이 나는지"가 수년 단위로 다르다. 결론이 10년 뒤에 나는 레인은
**검증 중인 게 아니라 사실상 검증 불가**다. 그걸 알고 기다리는 것과 모르고 기다리는 건
다르다. 그리고 새 축을 고를 때 **건당 샤프**를 먼저 보면 시계가 짧은 것부터 칠 수 있다.

⚠️ 이건 "엣지가 있다"는 전제 하의 계산이다. 백필 효과크기가 그대로 forward에
재현된다고 가정한다. **실제로는 보통 줄어든다**(인샘플 과적합). 그래서 여기 나오는
연수는 **낙관적 하한**이다.
"""
from __future__ import annotations

import csv
import json
import math
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PANIC_REP = ROOT / "data" / "analysis" / "kr_index_etf_panic_report.json"
KQ_CACHE = ROOT / "data" / "analysis" / "kr_index_etf_prices.csv"
VB = ROOT / "data" / "shadow" / "virtual_books.db"

T_TARGETS = [(2.0, "보정 없음"), (3.0, "분모 96"), (3.5, "분모 301")]


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def kq_rows():
    return sorted(csv.DictReader(KQ_CACHE.open(encoding="utf-8-sig")), key=lambda r: r["date"])


def lane_from_series(name, vals, per_year, note=""):
    if len(vals) < 20:
        return None
    m, sd = st.mean(vals), st.stdev(vals)
    if sd == 0 or m <= 0:
        return {"name": name, "n": len(vals), "mean": m, "sd": sd, "sharpe": m / sd if sd else None,
                "per_year": per_year, "dead": True, "note": note}
    return {"name": name, "n": len(vals), "mean": m, "sd": sd, "sharpe": m / sd,
            "per_year": per_year, "dead": False, "note": note}


def main():
    lanes = []
    rows = kq_rows()

    # ── 패닉 ETF v2 — 정본 리포트의 평균·t에서 표준편차를 복원 ──
    rep = json.loads(PANIC_REP.read_text(encoding="utf-8"))
    for tk in ("233740", "229200"):
        b = rep["by"][tk]["backfill"]
        n, t, m = int(b["n"]), float(b["t"]), float(b["excess_mean"])
        sd = m * math.sqrt(n) / t          # t = m/(sd/√n)
        yrs = len({r["date"][:4] for r in rows if f(r.get(f"{tk}_close"))})
        lanes.append({"name": f"패닉 ETF v2 `{tk}`", "n": n, "mean": m, "sd": sd,
                      "sharpe": m / sd, "per_year": n / yrs, "dead": False,
                      "note": "정본 리포트(평균·t)에서 sd 복원"})

    # ── 패닉 강신호만 (효과가 크면 시계가 짧아지는지) ──
    for tk in ("233740",):
        parts = rep["by"][tk].get("backfill_parts", {}).get("strength", {})
        s = parts.get("strong")
        if s:
            # 강신호는 t가 리포트에 없다 — 전체 sd를 보수적으로 그대로 쓴다(과소평가 방지).
            b = rep["by"][tk]["backfill"]
            sd_all = float(b["excess_mean"]) * math.sqrt(int(b["n"])) / float(b["t"])
            m = float(s["excess_mean"])
            yrs = len({r["date"][:4] for r in rows if f(r.get(f"{tk}_close"))})
            lanes.append({"name": f"패닉 **강신호만** `{tk}`", "n": int(s["n"]), "mean": m,
                          "sd": sd_all, "sharpe": m / sd_all, "per_year": int(s["n"]) / yrs,
                          "dead": False, "note": "sd는 전체값 사용(보수적)"})

    # ── 무조건 야간 (직접 계산) ──
    for tk in ("229200", "233740"):
        nights = []
        for i in range(len(rows) - 1):
            cl, op = f(rows[i].get(f"{tk}_close")), f(rows[i + 1].get(f"{tk}_open"))
            if cl and op:
                nights.append((rows[i]["date"], (op / cl - 1) * 100 - 0.05))
        yrs = len({d[:4] for d, _ in nights}) or 1
        r = lane_from_series(f"무조건 야간 `{tk}`", [v for _, v in nights],
                             len(nights) / yrs, "비용 0.05% 차감 후")
        if r:
            lanes.append(r)

    L = ["# 판정까지 몇 년 걸리나 — 검증 시계 (2026-09-19)", "",
         "read-only. `tools/research/research_time_to_verdict_20260919.py`(산수는 docstring).",
         "**n = (t목표 / 건당샤프)²** · 건당샤프 = 평균 ÷ 표준편차. "
         "이 하나가 검증 속도를 전부 결정한다.", "",
         "| 레인 | 백필 n | 평균 | 표준편차 | **건당 샤프** | 연 이벤트 |",
         "|---|---:|---:|---:|---:|---:|"]
    for x in sorted(lanes, key=lambda z: -(z["sharpe"] or -9)):
        L.append(f"| {x['name']} | {x['n']:,} | {x['mean']:+.3f}% | {x['sd']:.2f}% | "
                 f"**{x['sharpe']:+.3f}** | {x['per_year']:.0f}회 |")

    L += ["", "## 판정 도달 시점", "",
          "| 레인 | t≥2 (보정없음) | t≥3 (분모 96) | t≥3.5 (분모 301) |",
          "|---|---:|---:|---:|"]
    for x in sorted(lanes, key=lambda z: -(z["sharpe"] or -9)):
        cells = []
        for t, _ in T_TARGETS:
            if not x["sharpe"] or x["sharpe"] <= 0:
                cells.append("도달 불가")
                continue
            n = (t / x["sharpe"]) ** 2
            yr = n / x["per_year"]
            cells.append(f"{n:,.0f}건 · **{yr:.1f}년**")
        L.append(f"| {x['name']} | " + " | ".join(cells) + " |")

    L += ["", "## 읽는 법", "",
          "- **건당 샤프가 전부다.** 수익률이 높아서가 아니라 비율이 높아야 빨리 판정된다.",
          "  패닉 레인의 0.2대는 '느린 레인'이다 — 같은 샤프면 빈도가 시계를 정한다.",
          "- **빈도를 올리면 시계가 짧아지지 않는다.** 무조건 야간은 연 220회로 10배 빠른데",
          "  샤프가 낮아 오히려 더 오래 걸리거나 아예 도달하지 못한다. 이미 측정된 구조다",
          "  — 문턱을 낮추면 건당 엣지가 떨어진다.",
          "- **분모가 시계를 늘린다.** 같은 레인이 보정 없이 4년, 분모 96이면 9년이다.",
          "  새 가설을 칠 때마다 기존 레인의 판정이 멀어진다는 뜻이다.",
          "- ⚠️ 이건 **백필 효과가 forward에 그대로 재현된다는 낙관 가정**의 하한이다.",
          "  실제 forward는 보통 줄어든다(패닉 현재 4건 전부 음수).", "",
          "## 이 표가 말하는 것", "",
          "지금 설계로 **판정은 수년 뒤**다. 그건 '엣지가 없다'와 다른 문제다 —",
          "**검증 시계가 인내보다 길다.** 이걸 줄이는 길은 셋뿐이고, 둘은 이미 닫혔다.",
          "",
          "  · 빈도 ↑ → **닫힘**(문턱 완화·비신호 밤·지수 확장 전부 기각, 샤프가 떨어짐)",
          "  · 분모 ↓ → **우리가 통제**(새 가설을 덜 치면 필요 t가 내려간다)",
          "  · **건당 샤프 ↑** → 유일하게 열린 방향. 새 축은 수익률이 아니라 **샤프로 골라야 한다.**", ""]

    out = ROOT / "docs" / "reports" / "time_to_verdict_20260919.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
