#!/usr/bin/env python3
"""이번 주 정산 완료분 — 종목별 전건 (read-only, 2026-09-16).

운영자 지시: *"이번주 전략들 별 수익성들 마감된애들 종목별로 리포트"* — **평균 내지 말고 종목 단위로.**

## 이 리포트가 정직하려면 붙잡아야 할 것 넷

**① 창은 '정산일' 기준이다.** 진입일이 아니라 `settled_at`(원장에 결과가 기록된 시각)이
09-14~09-16인 행만 센다. 진입일 기준으로 자르면 이번 주 진입분은 D7이 안 지나 거의 빈다.
⚠️ `settled_at`은 **배치가 쓴 시각**이지 장에서 나간 시각이 아니다(원장에 `exit_index`가
안 채워져 있어 실제 청산 세션은 복원 불가). 하루 정도 어긋날 수 있다.

**② 3일짜리 창은 승자 편향이 최대다.** 이긴 건 TP로 먼저 닫히고 진 건 D 만기까지 열려 있다.
그래서 `exit_reason`을 **반드시 같이 싣고**, 편향이 없는 `D_MAT`(만기 도달)만 따로 한 번 더 낸다.
TP만 세서 "이번 주 승률 90%"라고 쓰면 그건 성과가 아니라 시계다.

**③ 지수 초과는 만기 도달분에만 낼 수 있다.** 벤치 창은 진입 시가 → hold-1 세션 종가인데,
TP/BE/SL로 **조기 청산된 행은 실제 보유가 그 창보다 짧다**. 창을 길게 대면 초과가 왜곡되고,
원장에 `exit_index`가 안 채워져 있어 실제 청산 세션을 복원할 수도 없다.
→ **조기 청산 행은 초과를 "산출 불가"로 두고 숫자를 만들지 않는다.** 초과 평균은 `D_MAT`에만 낸다.
초안에서 이걸 안 지켜 3행짜리 평균을 "TP 코호트 초과 +16.66%"로 낼 뻔했다.
벤치 봉이 아직 창을 못 덮는 행도 따로 표기한다(KR 069500 ~09-16 / US IWM ~09-15 보유).

**④ 기저선(`x*`)은 판정 대상이 아니다.** 조건 전량 매수 모집단이라 후보를 뽑아낸 denominator다.
건수·범위만 싣고 종목 나열에서 뺀다.

보유기간 정본은 `virtual_books.STRATEGIES["hold"]`다(09-16 정정 — note의 D표기로 추정 금지).
"""
from __future__ import annotations

import json
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_benchmark_excess import bench_bars  # noqa: E402
from tools.virtual_books import HOLD_SESSIONS, STRATEGIES  # noqa: E402

HOLDS = {s["id"]: int(s.get("hold", HOLD_SESSIONS)) for s in STRATEGIES}
SHADOW = ROOT / "data" / "shadow"
VB = SHADOW / "virtual_books.db"
WEEK_FROM, WEEK_TO = "2026-09-14", "2026-09-16"

BARS = {"KR": bench_bars("kr", "069500"), "US": bench_bars("us", "IWM")}
IDX = {m: {b[0]: i for i, b in enumerate(v)} for m, v in BARS.items()}


def market_of(ticker: str) -> str:
    return "KR" if ticker and ticker[:1].isdigit() else "US"


def excess(market: str, d: str, net: float, hold: int, reason: str | None = None):
    """같은 창 지수 초과(%). 창 = 진입일 시가 → hold-1 세션 종가.

    반환 (값, 사유). 값이 None이면 사유가 왜 못 냈는지 말한다.
    조기 청산(TP/BE/SL)은 실제 보유가 창보다 짧아 **원천적으로 산출 불가**다 — 창을 길게 대서
    숫자를 만들면 그건 초과가 아니라 다른 것이다.
    """
    if reason is not None and reason != "D_MAT":
        return None, "조기청산"
    i = IDX[market].get(d)
    if i is None:
        return None, "벤치일 없음"
    if net is None or i + hold > len(BARS[market]):
        return None, "창 미도달"
    bench = (BARS[market][i + hold - 1][4] / BARS[market][i][1] - 1) * 100
    return net - bench, None


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


def fmt(x, unit="%"):
    return f"{x:+.2f}{unit}" if x is not None else "-"


def main():
    L = [f"# 이번 주 정산 완료분 — 종목별 전건 ({WEEK_FROM} ~ {WEEK_TO})", "",
         "read-only. `tools/research/research_week_closed_by_ticker_20260916.py`(규약은 docstring).",
         "**창은 정산일 기준**(진입일 아님). **판정은 지수 초과분**(KR=069500 / US=IWM).",
         "**전부 쉐도우다 — 실제 손익이 아니다**(09-01 운영자 결정으로 실매수 0).", ""]

    # ── A. 문턱 통과 레인 ──────────────────────────────────────────
    L += ["## A. 문턱 통과 레인 — 패닉 ETF v2 (유일하게 사전등록 통과한 축)", ""]
    panic = [r for r in jl("kr_index_etf_panic")
             if r.get("kind") == "trade" and r.get("mode") == "live"]
    wk = [r for r in panic if WEEK_FROM <= (r.get("session_date") or "") <= WEEK_TO]
    closed = [r for r in wk if r.get("status") == "CLOSED" and r.get("net_pct") is not None]
    if closed:
        L += ["| 종목 | 진입일 | 강도 | 단위 | net | 계약 |", "|---|---|---|---:|---:|---|"]
        for r in sorted(closed, key=lambda x: (x["session_date"], x["ticker"])):
            L.append(f"| `{r['ticker']}` | {r['session_date']} | {r.get('strength')} | "
                     f"{r.get('units')} | **{fmt(r['net_pct'])}** | 종가매수/익일시가매도 |")
        L += ["", f"정산 {len(closed)}건 · 단순합 {fmt(sum(r['net_pct'] for r in closed))} · "
              f"평균 {fmt(st.mean(r['net_pct'] for r in closed))}",
              "⚠️ 이 레인은 1박 오버나이트라 지수 초과 창이 다르다 — 벤치는 같은 밤 지수라 "
              "위 net이 이미 '지수 대비'가 아니다. 축 판정은 사전등록 연도매칭 표로만 한다.", ""]
    else:
        L += [f"이번 주 정산된 패닉 ETF 신호 없음(창내 신호 {len(wk)}건).", ""]

    rung = [r for r in jl("kq_close_rung") if r.get("mode") == "live"]
    L += [f"종가 rung(09-13 신설, 관측 전용): forward **{len(rung)}건** — 이번 주 정산 0.", ""]

    # ── B. 라이브 규칙 쉐도우 ──────────────────────────────────────
    L += ["## B. 라이브 규칙 쉐도우 — 실제 돈이 나갔다면 이대로였다", ""]
    kf = [r for r in jl("kr_fallen_shadow")
          if r.get("pass_all") and r.get("status") == "SETTLED"
          and r.get("net_pct") is not None]
    kfw = [r for r in kf if WEEK_FROM <= (r.get("session_date") or "") <= WEEK_TO]
    if kfw:
        L += ["| 종목 | 진입일 | net | 초과(D7) |", "|---|---|---:|---:|"]
        for r in sorted(kfw, key=lambda x: x["session_date"]):
            ex, why = excess("KR", r["session_date"], r["net_pct"], 7)
            L.append(f"| `{r['ticker']}` | {r['session_date']} | {fmt(r['net_pct'])} | "
                     f"**{fmt(ex) if ex is not None else why}** |")
        L.append("")
    else:
        L += [f"이번 주 진입·정산된 KR 급락 라이브 규칙 통과 건 없음"
              f"(전체 pass_all 정산 누적 {len(kf)}건).", ""]

    # ── C. 인큐베이터 — 종목별 ────────────────────────────────────
    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT strategy_id, session_date, ticker, exit_reason, net_pct "
        "FROM trades WHERE backfill=0 AND status='CLOSED' "
        "AND substr(settled_at,1,10) BETWEEN ? AND ?", (WEEK_FROM, WEEK_TO)).fetchall()
    con.close()

    base = [r for r in rows if r["strategy_id"].startswith(("xkr_", "xus_"))]
    judged = [r for r in rows if not r["strategy_id"].startswith(("xkr_", "xus_"))]

    # 같은 종목·같은 진입일은 계약이 같아 net이 동일하다 — arm만 여러 개다. 묶어서 한 줄.
    g = defaultdict(lambda: {"arms": [], "net": None, "reason": None})
    for r in judged:
        k = (r["ticker"], r["session_date"], round(r["net_pct"] or 0, 4))
        g[k]["arms"].append(r["strategy_id"])
        g[k]["net"] = r["net_pct"]
        g[k]["reason"] = r["exit_reason"]
        g[k]["hold"] = HOLDS.get(r["strategy_id"], HOLD_SESSIONS)

    recs = []
    for (tk, d, _), v in g.items():
        mk = market_of(tk)
        ex, why = excess(mk, d, v["net"], v["hold"], v["reason"])
        recs.append({"ticker": tk, "date": d, "market": mk, "net": v["net"],
                     "reason": v["reason"], "hold": v["hold"], "arms": sorted(set(v["arms"])),
                     "ex": ex, "why": why})
    recs.sort(key=lambda r: (r["net"] is None, -(r["net"] or 0)))

    L += ["## C. 인큐베이터 — 종목별 전건 (판정 대상 arm만)", "",
          f"행 {len(judged)}건이 **{len(recs)}종목·진입일 조합**으로 묶인다"
          f"(같은 픽을 여러 arm이 잡으면 net은 같다 — arm 수는 중복이지 확인이 아니다).",
          "⚠️ **TP 행의 net은 계약 상수다**(KR +11.75% / US +11.50% = TP12 − 비용). "
          "여기서 정보는 금액이 아니라 **몇 종목이 +12% 벽을 쳤는가**라는 건수다.", "",
          "| 종목 | 시장 | 진입일 | 출구 | net | **초과** | D | 잡은 arm |",
          "|---|---|---|---|---:|---:|---:|---|"]
    for r in recs:
        arms = ", ".join(f"`{a}`" for a in r["arms"][:3])
        if len(r["arms"]) > 3:
            arms += f" 외 {len(r['arms']) - 3}"
        ex = f"**{fmt(r['ex'])}**" if r["ex"] is not None else r["why"]
        L.append(f"| **{r['ticker']}** | {r['market']} | {r['date']} | {r['reason']} | "
                 f"{fmt(r['net'])} | {ex} | {r['hold']} | {arms} |")

    # 출구 사유별 — 편향 노출
    by_reason = defaultdict(list)
    for r in recs:
        by_reason[r["reason"]].append(r)
    L += ["", "### 출구 사유별 — 승자 편향이 어디서 오는가", "",
          "| 출구 | 종목수 | net 평균 | **초과 평균** | 비고 |", "|---|---:|---:|---:|---|"]
    note = {"TP": "익절 — **먼저 닫힌다**. 편향의 원천",
            "BE": "BE락 발동(US 한정) — 본전 근처에서 끊김",
            "SL": "손절", "D_MAT": "**만기 도달 — 편향 없음**"}
    for k in ("TP", "BE", "D_MAT", "SL"):
        v = by_reason.get(k)
        if not v:
            continue
        ex = [x["ex"] for x in v if x["ex"] is not None]
        ex_cell = (f"**{fmt(st.mean(ex))}** (n={len(ex)})" if ex
                   else "산출 불가(조기청산)" if k != "D_MAT" else "창 미도달")
        L.append(f"| {k} | {len(v)} | {fmt(st.mean(x['net'] for x in v))} | "
                 f"{ex_cell} | {note.get(k, '')} |")
    L += ["",
          "**이 표의 net 평균으로 전략을 판정하지 않는다.** 3일짜리 정산 창은 TP가 47/74를 차지해 "
          "구성 자체가 승자 쪽으로 기울어 있다. 판정은 [[full_system_audit_20260912]]의 "
          "지수 초과·날짜 클러스터 t로 한다."]

    mat = by_reason.get("D_MAT", [])
    L += ["", "### 편향 없는 표본만 — 만기 도달(D_MAT) 종목", ""]
    if mat:
        L += ["| 종목 | 시장 | 진입일 | net | **초과** | 잡은 arm |", "|---|---|---|---:|---:|---|"]
        for r in sorted(mat, key=lambda x: -(x["net"] or 0)):
            arms = ", ".join(f"`{a}`" for a in r["arms"][:3])
            if len(r["arms"]) > 3:
                arms += f" 외 {len(r['arms']) - 3}"
            L.append(f"| **{r['ticker']}** | {r['market']} | {r['date']} | {fmt(r['net'])} | "
                     f"**{fmt(r['ex'])}** | {arms} |")
        exm = [x["ex"] for x in mat if x["ex"] is not None]
        L += ["", f"만기 도달 {len(mat)}종목 · net 평균 {fmt(st.mean(x['net'] for x in mat))} · "
              f"**초과 평균 {fmt(st.mean(exm) if exm else None)}** · "
              f"초과 양수 {sum(1 for x in exm if x > 0)}/{len(exm)}",
              "표본이 이 크기면 판정이 아니다 — 이번 주가 어땠나의 기록이다.", ""]
    else:
        L += ["만기 도달 건 없음 — 이번 주 정산은 전부 조기 청산이다.", ""]

    # ── D. 기저선 ────────────────────────────────────────────────
    bnet = [r["net_pct"] for r in base if r["net_pct"] is not None]
    L += ["## D. 기저선(`x*`) — 판정 대상 아님", "",
          f"{len(base)}건 정산(조건 전량 매수 모집단). "
          f"net 평균 {fmt(st.mean(bnet) if bnet else None)} · "
          f"범위 {fmt(min(bnet) if bnet else None)} ~ {fmt(max(bnet) if bnet else None)}.",
          "후보를 뽑아낸 denominator라 여기서 성과를 판정하지 않는다. 종목 나열은 생략.", ""]

    out = ROOT / "docs" / "reports" / "week_closed_by_ticker_20260916.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
