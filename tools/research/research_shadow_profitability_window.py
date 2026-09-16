#!/usr/bin/env python3
"""쉐도우 전략 수익성 — 8/25~현재 창 (read-only, 2026-09-14).

운영자 질문: *"모든 전략들 지금까지의 수익성 검토해봐. 8월 말부터 쉐도우 전략들 붙었을 때부터."*

## 이 리포트가 정직하려면 붙잡아야 할 것 셋

**① 원 net으로 판정하지 않는다.** 강세장에서 원 net은 모든 롱 전략을 통과시킨다.
판정은 **같은 창 지수 초과**(KR=069500 / US=IWM)다. 09-12에 게이트도 이 기준으로 고쳤다.

**② 미성숙을 숨기지 않고 옆에 놓는다.** 3주짜리 창에서는 **이긴 것부터 닫힌다**
(TP가 먼저 터지고 진 건 D5/D7까지 열려 있다). 그래서 "정산 n"과 "만기 도달 n"을
나란히 싣는다. 정산분만 보면 승률 100%가 나오는데 그건 성과가 아니라 시계다.

**③ 같은 거래를 두 곳에서 세지 않는다.** `kr_fallen_shadow.jsonl`(라이브 규칙 스캔)과
`virtual_books`의 `c_kr_fallen*`(변형 arm)은 같은 풀에서 나온다. 전자는 **라이브 규칙 그 자체**,
후자는 **변형**으로 층을 갈라 싣는다. 두 소스가 같은 거래에 동의하는 건 확인 2개가 아니다.

## 층 구분 — 운영자가 결정할 것 기준

  A. **문턱 통과 레인**(패닉 ETF v2 + 종가 rung 관측) — 유일하게 사전등록을 통과한 축
  B. **라이브 규칙 쉐도우** — 실제 돈이 나갔다면 이게 그대로 일어났다
  C. **인큐베이터**(virtual_books 45+9) — 09-01 출범, 판정 예정 12월

⚠️ 전부 **쉐도우다. 실제 손익이 아니다**(09-01 운영자 결정으로 실매수 0).
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_benchmark_excess import bench_bars  # noqa: E402
from tools.virtual_books import HOLD_SESSIONS, STRATEGIES  # noqa: E402

# 보유기간 정본은 STRATEGIES["hold"]다(09-16 정정 — 상세는 research_full_audit_20260912.py).
HOLDS = {s["id"]: int(s.get("hold", HOLD_SESSIONS)) for s in STRATEGIES}

SHADOW = ROOT / "data" / "shadow"
VB = SHADOW / "virtual_books.db"
START, END = "2026-08-25", date.today().isoformat()
UNIT_KRW = 220_000 * 3          # KR 급락 현행 계약 노출(22만 × 슬롯3)


def jl(name):
    p = SHADOW / f"{name}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def main():
    bars = {"KR": bench_bars("kr", "069500"), "US": bench_bars("us", "IWM")}
    idx = {m: {b[0]: i for i, b in enumerate(v)} for m, v in bars.items()}
    cal = {m: [b[0] for b in v] for m, v in bars.items()}

    def excess(market, d, net, hold):
        i = idx[market].get(d)
        if i is None or i + hold > len(bars[market]):
            return None
        return net - (bars[market][i + hold - 1][4] / bars[market][i][1] - 1) * 100

    def matured(market, d, hold):
        """진입일 d에서 보유기간이 실제로 지났는가(만기 도달)."""
        try:
            return len(cal[market]) - 1 - cal[market].index(d) >= hold
        except ValueError:
            return False

    def block(label, rows, market, hold, note=""):
        """rows: [(session_date, net_pct or None, settled_bool)]"""
        win = [r for r in rows if START <= r[0] <= END]
        cl = [r for r in win if r[2] and r[1] is not None]
        mat = [r for r in win if matured(market, r[0], hold)]
        ex = [e for e in (excess(market, d, n, hold) for d, n, s in cl if s) if e is not None]
        f = lambda x: f"{x:+.2f}%" if x is not None else "-"     # noqa: E731
        return (f"| {label} | {len(win)} | {len(cl)} | {len(mat)} | "
                f"{f(st.mean(r[1] for r in cl) if cl else None)} | "
                f"**{f(st.mean(ex) if ex else None)}** | {note} |")

    L = [f"# 쉐도우 전략 수익성 — {START} ~ {END}", "",
         "read-only. `tools/research/research_shadow_profitability_window.py`(규약은 docstring).",
         "**판정은 지수 초과분**(KR=069500 / US=IWM, 창=계약 보유기간). 원 net은 참고.",
         "**전부 쉐도우다 — 실제 손익이 아니다**(09-01 운영자 결정으로 실매수 0).", ""]

    # ── A. 문턱 통과 레인 ──
    panic = [r for r in jl("kr_index_etf_panic")
             if r.get("kind") == "trade" and r.get("mode") == "live" and r.get("ticker") == "233740"]
    rung_live = [r for r in jl("kq_close_rung") if r.get("mode") == "live"]
    rung_bf = [r for r in jl("kq_close_rung") if r.get("mode") == "backfill"]
    L += ["## A. 문턱 통과 레인 — 패닉 ETF", "",
          "사전등록을 통과한 유일한 축이다(233740 2015~2025 연도매칭 초과 +0.573%, t 3.27, 10/10).", ""]
    for r in sorted(panic, key=lambda x: x["session_date"]):
        L.append(f"- **{r['session_date']}** {r.get('strength')} {r.get('units')}단위 · "
                 f"status **{r.get('status')}** · net {r.get('net_pct') if r.get('net_pct') is not None else '미정산'}")
    if not panic:
        L.append("- forward 신호 없음")
    L += ["", f"종가 rung(09-13 신설, 관측 전용): 백필 {len(rung_bf)}건 · **forward {len(rung_live)}건**", ""]

    # ── B. 라이브 규칙 쉐도우 ──
    kf = [r for r in jl("kr_fallen_shadow") if r.get("pass_all")]
    up = [r for r in jl("us_panic_close") if r.get("kind") == "trade" and r.get("mode") == "live"]
    L += ["## B. 라이브 규칙 쉐도우 — 실제 돈이 나갔다면 이대로였다", "",
          "| 레인 | 창내 진입 | 정산 | **만기도달** | 원net | **초과** | 비고 |",
          "|---|---:|---:|---:|---:|---:|---|",
          block("KR 급락 (라이브 규칙, TP12/SL25/D5)",
                [(r["session_date"], r.get("net_pct"), r.get("status") == "SETTLED") for r in kf],
                "KR", 5, "`kr_fallen_shadow` pass_all"),
          block("US 패닉 마감 (TP20/SL25/D10)",
                [(r["session_date"], r.get("net_pct"), r.get("status") == "CLOSED") for r in up],
                "US", 10, "`us_panic_close` mode=live"),
          ""]

    # ── C. 인큐베이터 ──
    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True)
    notes = {r[0]: (r[1] or "") for r in con.execute("SELECT id, note FROM strategies")}
    vb = defaultdict(list)
    for sid, d, net, stt in con.execute(
            "SELECT strategy_id, session_date, net_pct, status FROM trades WHERE backfill=0"):
        vb[sid].append((d, net, stt))
    con.close()

    def kind_of(sid):
        if sid.startswith(("xkr_", "xus_")):
            return "기저선"
        return "후보" if sid.startswith("c_") else "실행변형"

    groups = defaultdict(lambda: {"win": 0, "cl": 0, "mat": 0, "net": [], "ex": []})
    for sid, v in vb.items():
        mk = "KR" if "kr" in sid else "US"
        hold = HOLDS.get(sid)
        if hold is None:      # 원장에만 남은 은퇴 id — note 표기로 보조
            m = re.search(r"\bD(\d+)\b", notes.get(sid, ""))
            hold = int(m.group(1)) if m else HOLD_SESSIONS
        g = groups[kind_of(sid)]
        for d, net, stt in v:
            if not (START <= d <= END):
                continue
            g["win"] += 1
            if matured(mk, d, hold):
                g["mat"] += 1
            if stt == "CLOSED" and net is not None:
                g["cl"] += 1
                g["net"].append(net)
                e = excess(mk, d, net, hold)
                if e is not None:
                    g["ex"].append(e)

    L += ["## C. 인큐베이터 (virtual_books, 09-01 출범 · 판정 예정 12월)", "",
          "| 구분 | 창내 진입 | 정산 | **만기도달** | 원net | **초과** |",
          "|---|---:|---:|---:|---:|---:|"]
    for k in ("후보", "실행변형", "기저선"):
        g = groups.get(k)
        if not g:
            continue
        f = lambda x: f"{x:+.2f}%" if x is not None else "-"      # noqa: E731
        L.append(f"| {k} | {g['win']} | {g['cl']} | {g['mat']} | "
                 f"{f(st.mean(g['net']) if g['net'] else None)} | "
                 f"**{f(st.mean(g['ex']) if g['ex'] else None)}** |")

    # ── 만기 시점 ──
    L += ["", "## D. 언제부터 읽을 수 있나", "",
          "계약이 KR D5 / US D7~D10이라 **진입 후 그만큼 지나야** 판정 표본이 된다.",
          "지금 표의 '정산'이 '만기도달'보다 크면, 닫힌 건 **TP를 먼저 친 승자들**이다.", ""]
    for mk, hold in (("KR", 5), ("US", 7)):
        c = cal[mk]
        if len(c) > hold:
            L.append(f"- {mk} D{hold}: {c[-1]} 진입분은 약 {hold}세션 뒤 만기 "
                     f"(직전 만기 도달일 {c[-1 - hold]})")

    tot_ex = groups["후보"]["ex"]
    L += ["", "## E. 원화 환산", "",
          f"KR 급락 현행 계약 노출 {UNIT_KRW:,}원(22만 × 슬롯3) 기준.",
          "⚠️ **쉐도우다. 이 돈은 벌지도 잃지도 않았다**(실매수 0)."]
    if tot_ex:
        L.append(f"- 인큐베이터 후보 초과 평균 {st.mean(tot_ex):+.2f}% → 건당 "
                 f"{UNIT_KRW * st.mean(tot_ex) / 100:,.0f}원 상당")
    else:
        L.append("- 후보 초과를 낼 표본이 아직 없다 — 환산할 숫자가 없다.")

    out = ROOT / "docs" / "reports" / f"shadow_profitability_{START.replace('-','')}_{END.replace('-','')}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
