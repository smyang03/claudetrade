#!/usr/bin/env python3
"""쉐도우를 실매수로 읽으면 얼마인가 (read-only, 2026-09-17).

운영자 지적: *"쉐도우를 실제 매수처럼 하자고 했는데 뭔소리야"* — 내가 "실매수 0이라 월 환산 불가"로
답한 것에 대한 교정. [[shadow-must-run-like-live-20260902]]가 정본이다. 쉐도우 원장은
**실매매 원장으로 읽는다.** 금액이 안 나오는 건 표본이 없을 때지 실주문이 없어서가 아니다.

## 단, 아무 arm이나 더하면 안 된다 — 대부분이 용량 무제한이다

`virtual_books`의 후보 arm은 대부분 `slots=1_000_000` · `capital_krw=1e12`다. 엣지를
용량 제약 없이 재려고 **일부러** 그렇게 뒀다. 그 원장의 `notional_krw`를 더하면
"사흘에 1억 투입" 같은 값이 나온다 — 실계좌가 아니다.

그래서 **실제 계약 모양(슬롯·일한도·주문금액·자본)이 걸린 레인만** 금액으로 읽는다.
  · `us_live_dvol` — 슬롯7 / 일1 / 54만 = 라이브 `US_SWING_*`와 같은 계약
  · `kr_fallen_shadow` pass_all — 라이브 KR 급락 규칙 그 자체, 22만
  · 패닉 ETF v2 — 단위 50만 × 강2/보통1, 종목당 최대 100만

## 만기 편향을 숨기지 않는다

정산분만 더하면 **이긴 게 먼저 닫혀서** 금액이 부풀려진다. 그래서 각 레인에
**미정산(OPEN) 건수를 같이** 싣는다. OPEN이 정산과 비슷하면 그 손익은 아직 절반만 본 것이다.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kr_index_etf_panic_shadow import UNIT_KRW, pnl_krw, units_for  # noqa: E402

SHADOW = ROOT / "data" / "shadow"
VB = SHADOW / "virtual_books.db"
START = "2026-09-01"          # 09-01 대전환 — forward 원장 시작
TODAY = date.today().isoformat()
KR_FALLEN_ORDER_KRW = 220_000
US_SWING_MIRROR = "us_live_dvol"


def jl(name):
    p = SHADOW / f"{name}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def main():
    lanes = []

    # ── 1. US swing 미러 (virtual_books, 용량 제약 실제로 걸린 arm) ──
    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    s = con.execute("SELECT slots, daily_cap, order_krw, capital_krw FROM strategies WHERE id=?",
                    (US_SWING_MIRROR,)).fetchone()
    r = con.execute("SELECT COUNT(*) n, SUM(pnl_krw) pnl FROM trades "
                    "WHERE backfill=0 AND status='CLOSED' AND strategy_id=?",
                    (US_SWING_MIRROR,)).fetchone()
    op = con.execute("SELECT COUNT(*) FROM trades WHERE backfill=0 AND status='OPEN' "
                     "AND strategy_id=?", (US_SWING_MIRROR,)).fetchone()[0]
    lanes.append({"name": f"US swing 미러 `{US_SWING_MIRROR}`",
                  "contract": f"{s['order_krw']:,.0f}원 × 슬롯{s['slots']} · 일{s['daily_cap']}건",
                  "exposure": s["order_krw"] * s["slots"],
                  "n": r["n"], "open": op, "pnl": r["pnl"] or 0})

    # 용량 무제한 arm이 몇 개인지 — 이게 "실매수처럼"의 구멍이다
    uncapped = con.execute("SELECT COUNT(*) FROM strategies WHERE slots >= 1000").fetchone()[0]
    capped = con.execute("SELECT COUNT(*) FROM strategies WHERE slots < 1000").fetchone()[0]
    con.close()

    # ── 2. KR 급락 라이브 규칙 ──
    pa = [x for x in jl("kr_fallen_shadow") if x.get("pass_all")]
    st = [x for x in pa if x.get("status") == "SETTLED" and x.get("net_pct") is not None]
    lanes.append({"name": "KR 급락 라이브 규칙 `kr_fallen_shadow`",
                  "contract": f"{KR_FALLEN_ORDER_KRW:,}원 × 슬롯3",
                  "exposure": KR_FALLEN_ORDER_KRW * 3,
                  "n": len(st), "open": len(pa) - len(st),
                  "pnl": sum(KR_FALLEN_ORDER_KRW * x["net_pct"] / 100 for x in st)})

    # ── 3. 패닉 ETF v2 (세후) ──
    pt = [x for x in jl("kr_index_etf_panic")
          if x.get("kind") == "trade" and x.get("mode") == "live"]
    cl = [x for x in pt if x.get("net_pct") is not None]
    after = sum(pnl_krw(x["net_pct"], units_for(x.get("strength")))[1] for x in cl)
    lanes.append({"name": "패닉 ETF v2 `kr_index_etf_panic`",
                  "contract": f"단위 {UNIT_KRW:,}원 × 강2/보통1 · 종목당 최대 {UNIT_KRW*2:,}원",
                  "exposure": UNIT_KRW * 2 * 2,          # 2종목 동시
                  "n": len(cl), "open": len(pt) - len(cl), "pnl": after})

    tot_pnl = sum(x["pnl"] for x in lanes)
    tot_exp = sum(x["exposure"] for x in lanes)
    tot_n = sum(x["n"] for x in lanes)
    tot_open = sum(x["open"] for x in lanes)
    days = (date.fromisoformat(TODAY) - date.fromisoformat(START)).days
    per_month = tot_pnl * 30 / days if days else None

    L = [f"# 쉐도우를 실매수로 읽으면 얼마인가 — {START} ~ {TODAY}", "",
         "read-only. `tools/research/research_shadow_money_as_if_live_20260917.py`(규약은 docstring).",
         "[[shadow-must-run-like-live-20260902]] 정본에 따라 **쉐도우 원장 = 실매매 원장**으로 읽는다.",
         "**실제 계약 모양(슬롯·일한도·주문금액)이 걸린 레인만** 금액으로 환산한다.", "",
         "| 레인 | 계약 | 노출 | 정산 | 미정산 | **손익** |",
         "|---|---|---:|---:|---:|---:|"]
    for x in lanes:
        L.append(f"| {x['name']} | {x['contract']} | {x['exposure']:,.0f}원 | "
                 f"{x['n']} | {x['open']} | **{x['pnl']:+,.0f}원** |")
    L += [f"| **합계** | | **{tot_exp:,.0f}원** | {tot_n} | {tot_open} | "
          f"**{tot_pnl:+,.0f}원** |", "",
          f"{days}일({START}~{TODAY}) 기준 → **월 환산 {per_month:+,.0f}원** "
          f"(노출 {tot_exp:,.0f}원 대비 월 {per_month/tot_exp*100:+.2f}%)", "",
          f"⚠️ **정산 {tot_n}건 · 미정산 {tot_open}건.** 이긴 게 먼저 닫히므로 위 손익은 "
          "아직 승자 쪽으로 기운 값이다. n이 이 크기면 월 환산은 **방향도 못 정한다** — "
          "숫자는 내되 추세로 읽지 않는다.", "",
          "## 왜 나머지 arm은 금액으로 안 세는가", "",
          f"`virtual_books` 전략 {capped + uncapped}개 중 **{uncapped}개가 용량 무제한**"
          f"(`slots=1,000,000` · `capital=1조`)이고, 계약 모양이 걸린 건 {capped}개뿐이다.",
          "무제한 arm의 `notional_krw`를 더하면 사흘에 1억 투입 같은 값이 나온다 — 실계좌가 아니다.",
          "무제한은 **엣지를 용량과 분리해 재려는 의도된 설계**지 결함이 아니다. 다만 그 arm들은",
          "**'실매수처럼'의 대상이 아니다** — 금액을 물으려면 계약을 씌운 미러가 있어야 한다.", ""]

    out = ROOT / "docs" / "reports" / "shadow_money_as_if_live_20260917.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
