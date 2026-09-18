#!/usr/bin/env python3
"""기업행위 미조정 탐지 — 원장 오염 규모와 통계 영향 (read-only, 2026-09-18).

## 왜

09-17에 `kr_bonus_issue`가 475460을 **−65.71%**로 정산했다. 계약은 SL25인데 그 아래가
나왔다. 같은 종목·같은 진입가를 `xkr_rise5`는 **+11.75% TP**로 닫았다 — 보유기간만 다르다.
무상증자 권리락을 손실로 계산한 것이다.

원장 전수 스캔 결과 계약 경계(−30%) 밖이 **1,749건(0.72%)**, 전부 SL 방향이고
TP 방향 위반은 0이다. **한쪽으로만 치우친 건 기업행위 오염의 지문이다** — 권리락·분할은
가격을 내리기만 한다.

영향이 작지 않다. 경계 밖을 빼면 `xkr_fallen3`(라이브 레인 모집단)가 **−0.17% → +0.08%로
부호가 뒤집힌다.** 어제 보유기간 오류가 부호를 뒤집은 것과 같은 계열이다.

## 그런데 꼬리를 그냥 자르면 안 된다

"−30% 밖을 제외"는 **손실만 골라 버리는 것**이라 결과를 위로 편향시킨다. 진짜 폭락도
−30%를 넘는다. 그래서 **기업행위를 실제로 탐지해서** 가려야 한다.

## 탐지 원리 — 외부 데이터 없이

분할·무상증자·권리락은 가격에 **단순 비율의 불연속**을 남긴다. 1:2 무상증자면 ×1/3,
액면분할 1:5면 ×1/5, 병합이면 ×2·×5 식이다. 그래서 전일 종가 → 당일 시가 비율이
**단순 분수에 가깝고** 동시에 이동폭이 큰 날을 찾는다.

두 번째 조건은 **거래대금**(가격 × 거래량)이다. 여기서 초안이 틀렸었다 —
"권리락은 거래량이 평소와 비슷하다"고 뒀는데, 1주당 2주 무상증자면 **주식 수가 3배가 되니
주식 수 거래량은 당연히 3배 뛴다.** 실제로 발단이 된 475460이 거래량 12.3배로 튀어
"의심"조차 못 받고 빠졌다. 분할·권리락에서 **거의 불변인 것은 거래대금**이다
(가격 ×1/3 × 거래량 ×3 ≈ 그대로). 진짜 폭락은 거래대금이 같이 터진다.

⚠️ 이건 **탐지지 확정이 아니다.** 비율이 우연히 맞는 폭락이 있을 수 있다. 그래서
분류를 세 단계(확실/의심/판단보류)로 내고, 원장을 고치지 않는다 — 규모와 영향만 보고한다.
원장 수정은 별도 지시 사항이다.
"""
from __future__ import annotations

import csv
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VB = ROOT / "data" / "shadow" / "virtual_books.db"
KR_DIR = ROOT / "data" / "price" / "kr"
US_DIR = ROOT / "data" / "price" / "us"

# 기업행위가 남기는 단순 비율. (분자, 분모) — 가격이 ×(분자/분모)로 점프한다.
RATIOS = [(1, 2), (1, 3), (1, 4), (1, 5), (1, 10), (2, 3), (3, 4), (2, 5), (3, 5),
          (2, 1), (3, 1), (5, 1), (10, 1), (4, 1)]
RATIO_TOL = 0.06         # 비율 허용 오차(±6%) — 권리락 당일 실제 등락이 비율 위에 얹힌다.
                         # 3.5%로 뒀다가 475460(오차 3.82%, 실제 1주당 2주 무상증자)을 놓쳤다.
MOVE_MIN = 20.0          # 이 이상 움직인 날만 후보(%)
TURNOVER_BAR = 4.0       # **거래대금**이 20일 평균의 이 배를 넘으면 진짜 사건 쪽으로 본다.
                         # 주식 수 거래량이 아니다 — 분할이면 주식 수는 배수만큼 당연히 뛴다.
CONTRACT_LO = -30.0      # 계약 경계(SL25 + 여유)


def load_bars(ticker: str):
    p = KR_DIR / f"kr_{ticker}.csv" if ticker[:1].isdigit() else US_DIR / f"us_{ticker}.csv"
    if not p.exists():
        return []
    rows = []
    with p.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.reader(fh):
            if len(r) >= 6 and r[0][:2] == "20":
                try:
                    rows.append((r[0], float(r[1]), float(r[2]), float(r[3]),
                                 float(r[4]), float(r[5])))
                except ValueError:
                    continue
    return sorted(rows)


def nearest_ratio(x: float):
    """x에 가장 가까운 단순 비율과 상대오차."""
    best, err = None, 9e9
    for a, b in RATIOS:
        r = a / b
        e = abs(x - r) / r
        if e < err:
            best, err = (a, b), e
    return best, err


def scan_ticker(ticker: str):
    """(date, 비율, 오차, 이동%, 거래대금배수, 등급) 리스트."""
    bars = load_bars(ticker)
    if len(bars) < 25:
        return []
    out = []
    for i in range(20, len(bars)):
        pc = bars[i - 1][4]
        o = bars[i][1]
        if pc <= 0 or o <= 0:
            continue
        move = (o / pc - 1) * 100
        if abs(move) < MOVE_MIN:
            continue
        ratio, err = nearest_ratio(o / pc)
        # 거래대금 = 종가 × 거래량. 분할이면 가격과 주식 수가 서로 상쇄해 거의 불변이다.
        t20 = st.mean(b[4] * b[5] for b in bars[i - 20:i]) or 1.0
        turn = (bars[i][4] * bars[i][5]) / t20 if t20 else 0.0
        if err <= RATIO_TOL and turn < TURNOVER_BAR:
            grade = "확실"
        elif err <= RATIO_TOL:
            grade = "의심"          # 비율은 맞는데 거래대금도 터졌다
        else:
            grade = "판단보류"       # 그냥 큰 갭
        out.append((bars[i][0], ratio, err, move, turn, grade))
    return out


def main():
    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    viol = con.execute(
        "SELECT strategy_id, session_date, ticker, net_pct, exit_reason, backfill "
        "FROM trades WHERE status='CLOSED' AND net_pct IS NOT NULL AND net_pct < ? "
        "ORDER BY net_pct", (CONTRACT_LO,)).fetchall()
    allrows = con.execute(
        "SELECT strategy_id, ticker, net_pct FROM trades "
        "WHERE status='CLOSED' AND net_pct IS NOT NULL").fetchall()
    con.close()

    tickers = sorted({r["ticker"] for r in viol})
    events = {t: scan_ticker(t) for t in tickers}

    # 위반 거래의 보유창(진입일 + 25거래일) 안에 기업행위가 있었나
    tagged = []
    for r in viol:
        evs = [e for e in events.get(r["ticker"], []) if e[0] >= r["session_date"]]
        evs = [e for e in evs if e[0] <= _plus_days(r["session_date"], 40)]
        grade = "판단보류"
        hit = None
        for e in evs:
            if e[5] == "확실":
                grade, hit = "확실", e
                break
            if e[5] == "의심" and grade == "판단보류":
                grade, hit = "의심", e
        tagged.append((r, grade, hit))

    cnt = defaultdict(int)
    for _, g, _ in tagged:
        cnt[g] += 1

    L = ["# 기업행위 미조정 탐지 — 원장 오염 규모와 영향 (2026-09-18)", "",
         "read-only. `tools/research/research_corporate_action_scan_20260918.py`(원리는 docstring).",
         "**원장은 고치지 않는다.** 규모·영향만 보고한다.", "",
         f"계약 경계({CONTRACT_LO}%) 밖 **{len(viol):,}건** · 관련 종목 {len(tickers)}개", "",
         "## 1. 분류", "",
         "| 등급 | 건수 | 판정 근거 |", "|---|---:|---|",
         f"| **확실** | {cnt['확실']:,} | 단순 비율(±{RATIO_TOL*100:.1f}%) 불연속 + 거래량 정상 |",
         f"| 의심 | {cnt['의심']:,} | 비율은 맞지만 거래량도 터짐 — 폭락일 수도 |",
         f"| 판단보류 | {cnt['판단보류']:,} | 비율 안 맞음 — 진짜 갭일 가능성 |", ""]

    # 통계 영향: 확실만 뺐을 때
    sure = {(r["strategy_id"], r["ticker"], round(r["net_pct"], 4))
            for r, g, _ in tagged if g == "확실"}
    susp = sure | {(r["strategy_id"], r["ticker"], round(r["net_pct"], 4))
                   for r, g, _ in tagged if g == "의심"}

    def arm_means(drop: set):
        by = defaultdict(list)
        for r in allrows:
            if (r["strategy_id"], r["ticker"], round(r["net_pct"], 4)) in drop:
                continue
            by[r["strategy_id"]].append(r["net_pct"])
        return {a: st.mean(v) for a, v in by.items() if len(v) >= 50}

    base = arm_means(set())
    m_sure = arm_means(sure)
    m_susp = arm_means(susp)

    L += ["## 2. 통계 영향 — 등급별로 뺐을 때 arm 평균", "",
          "꼬리를 통째로 자르지 않고 **탐지된 것만** 뺀다.", "",
          "| arm | n | 현재 | 확실 제외 | 확실+의심 제외 | 부호 변화 |",
          "|---|---:|---:|---:|---:|---|"]
    rows_out = []
    for a in base:
        d1 = m_sure.get(a, base[a]) - base[a]
        d2 = m_susp.get(a, base[a]) - base[a]
        if abs(d1) < 0.005 and abs(d2) < 0.005:
            continue
        flip = "**부호 뒤집힘**" if (base[a] < 0 <= m_susp.get(a, base[a])) else ""
        rows_out.append((abs(d2), a, base[a], m_sure.get(a, base[a]),
                         m_susp.get(a, base[a]), flip))
    rows_out.sort(reverse=True)
    for _, a, b0, b1, b2, flip in rows_out[:20]:
        n = sum(1 for r in allrows if r["strategy_id"] == a)
        L.append(f"| `{a}` | {n:,} | {b0:+.2f}% | {b1:+.2f}% | {b2:+.2f}% | {flip} |")
    if not rows_out:
        L.append("| — | | | | | 영향 있는 arm 없음 |")

    L += ["", "## 3. 확실 등급 표본 (상위 15건)", "",
          "| 종목 | 진입일 | net | 기업행위일 | 비율 | 이동 | 거래대금배수 |",
          "|---|---|---:|---|---|---:|---:|"]
    sure_rows = [(r, h) for r, g, h in tagged if g == "확실" and h]
    sure_rows.sort(key=lambda x: x[0]["net_pct"])
    for r, h in sure_rows[:15]:
        L.append(f"| `{r['ticker']}` | {r['session_date']} | {r['net_pct']:+.2f}% | "
                 f"{h[0]} | {h[1][0]}/{h[1][1]} | {h[3]:+.1f}% | {h[4]:.2f}x |")
    if not sure_rows:
        L.append("| — | | | | | | 확실 등급 없음 |")

    L += ["", "## 4. 결론", "",
          f"- 계약 경계 밖 {len(viol):,}건 중 **{cnt['확실']:,}건이 기업행위로 설명된다**"
          f"(비율 불연속 + 거래량 정상).",
          f"- 의심 {cnt['의심']:,}건은 폭락과 구분이 안 된다 — 외부 공시 데이터 없이는 확정 불가.",
          f"- 판단보류 {cnt['판단보류']:,}건은 **진짜 갭으로 취급해야 한다**. 빼면 손실만 골라 버리는 것이다.",
          "- ⚠️ 원장을 고치지 않았다. 수정 방법은 두 가지이고 둘 다 운영자 판단이 필요하다:",
          "  ① 가격 시리즈를 기업행위 비율로 소급 조정(정확하지만 전 종목 재정산)",
          "  ② 해당 거래를 `suspect_corporate_action`으로 표기하고 통계에서 분리(안전하지만 표본 손실)", ""]

    out = ROOT / "docs" / "reports" / "corporate_action_scan_20260918.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


def _plus_days(d: str, n: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
