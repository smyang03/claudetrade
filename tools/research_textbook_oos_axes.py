#!/usr/bin/env python3
"""계열A 축 OOS 재현 검정 — 봉인 교재 기간 (2026-08-31 사전등록).

배경: research_lever_comparison(08-30)에서 계열A(등락·IBS)가 shadow 209건에서
클러스터 t=+2.90/+3.15를 냈고, 07->08 월 분할 OOS에서도 t=2.23/2.73을 유지했다.
그러나 두 달치라 "밴드와의 상호작용이 진짜인지 우연인지" 가릴 수 없었다.
계열B(MAX20·ATR14)는 같은 OOS에서 무효가 됐다.

이 검정은 **완전히 겹치지 않는 기간**에서 계열A를 재현한다.
  shadow 표본: 2026-07-10 ~ 2026-08-28 (발견 표본)
  교재 표본  : 2025-01-27 ~ 2026-04-02 (이 검정, 197세션)
두 구간은 교집합이 없다. 교재는 08-24에 "최신화 재학습 무개선"으로 판정된
봉인 데이터셋이라 이 축을 만들 때 쓰인 적이 없다.

== 사전등록 (결과 확인 전 고정) ==

**표본**: 봉인 교재(us_yahoo_point_in_time.db)의 day_losers 프록시
  (`change_pct <= -5`, 기존 도구들이 쓰는 것과 동일 정의) 중 가격 CSV가
  커버하는 2025-01-27 이후 689행 / 77종목.

**축과 방향은 08-30에서 그대로 가져오며 여기서 재탐색하지 않는다**:
  등락(change_pct) 상위 / IBS 상위 — 둘 다 "덜 빠지고 종가가 고가 근처".
  임계는 **교재 표본 자체의 중앙값**을 쓴다(shadow 임계를 이식하면 두 모집단의
  분포 차이가 결과로 새어든다). 방향만 고정하고 임계는 표본 내부에서 정한다.

**baseline**: 08-30 2차 검증에서 확립한 계약 적용(TP=일봉 high, SL·BE락=종가).
  교재의 `net_krw_5d_pct`는 TP/SL 없는 만기 보유라 그대로 쓰면 상방이 안 잘려
  비교가 오염된다(shadow 원장에서 확인된 것과 같은 결함). 대조를 위해 만기 net도
  병기한다.

**판정 지표**: 선택군 - 배제군의 net 차이. **절대 net은 판정에 쓰지 않는다** —
  08-25 밴드+MAX OOS가 절대 수치로 판정하려다 "프록시 검정력 부족"으로 막힌
  전례가 있다. 생존편향·모집단 불일치는 양 군에 동일하게 작용하므로 차이는
  견디지만 절대값은 못 견딘다.

**판정 기준(고정)**: ① 차이 부호가 shadow와 같은 방향(선택군 우위)
  ② 선택군 클러스터 t >= 2 ③ 연도 분해(2025 / 2026)에서 부호 일치.
  셋 다 만족하면 "OOS 재현"으로 기록하고 30건 실거래 재검 1순위로 승격한다.
  **통과해도 라이브 제안하지 않는다** — 교재는 128종목(우리 유니버스는 1656)이라
  모집단이 좁고, 실거래 검증은 여전히 forward의 몫이다.

**생존편향 실측**: 교재 day_losers 77종목 중 수집 정지 2개(APLS·CNTA, 2.6%).
  08-31 repo health가 잡은 전체 24개보다 낮다. 그래도 상폐 종목은 애초에 교재에
  없으므로 편향은 남는다 — 절대 net을 판정에 쓰지 않는 이유다.

**시도 수 N +2** (축 2종, 재탐색 없음).

== 판정 (2026-08-31 실측, 표본 556행/70종목, 2025-04-21~2026-04-02) ==

**OOS 재현 실패. 3기준 미달.**

  전체    등락(hi) 선택 +1.46% t=+2.38 | 배제 +1.58% | 차이 **-0.11%p**
          IBS(hi)  선택 +1.57% t=+1.03 | 배제 +1.47% | 차이 **+0.09%p**
  2025    등락 -0.20%p / IBS +0.54%p
  2026    등락 +0.04%p / IBS **-0.71%p**   <- 부호 역전

기준① 등락은 차이가 음수(선택군 열위)라 미달, IBS는 +0.09%p로 사실상 0.
기준② IBS 클러스터 t=1.03 미달.
기준③ 두 축 모두 연도별 부호가 혼재 — 미달.

**shadow에서 +7~8%p였던 차이가 여기서는 ±0.1%p로 사라진다.** 08-30 발견은
2026-07~08 두 달의 표본 특성이었을 가능성이 높다. 계열B(MAX·ATR)가 월 분할
OOS에서 무너진 것과 같은 경로를 계열A도 밟았다 — 다만 계열A는 더 긴 창에서
무너졌다는 차이뿐이다.

**참고 항목에서만 재현된 것 1건 (판정 아님)**: 밴드 안 부분집합에서
등락(hi) 차이 **+3.46%p, t=+3.82**(n=70 vs 63). shadow의 "밴드 안에서 2~5배
강하다"와 방향이 같다. 그러나 사전등록에서 이를 **참고**로 분류했고 주 판정은
전체 표본이므로 결론을 바꾸지 않는다. 밴드 안 표본이 133행뿐이고 사후에 본
부분집합이라 다중검정 부담도 있다. **관측 가설로만 등록**한다 —
"계열A는 단독으로는 무효이나 밴드와의 상호작용에서는 살아있을 수 있다."

**부산물이 더 중요할 수 있다 — 국면 격차.**
  교재 기간(2025-04~2026-04) day_losers 계약 net **+1.52%**
  shadow 기간(2026-07~08)            계약 net **-0.77%**
같은 규칙·같은 계약인데 2.3%p 차이다. 우리 레인의 최근 적자가 선별 결함이
아니라 **국면**일 수 있다는 정량 증거다. 단 두 표본은 모집단이 다르므로
(교재 70종목 대형주 위주 vs shadow 156종목) 직접 뺄셈으로 단정하지 않는다.
메모리의 "급락 기저 -0.24%(2023~25) vs +0.50%(2025~26) = 국면이 사냥철"과
같은 방향이다.

**결론**: 계열A는 라이브 후보에서 내린다. 30건 재검 1순위 승격도 하지 않는다.
남는 것은 밴드 상호작용 관측 가설 하나이며, 이는 forward 표본으로만 판정한다
(observe_tail_risk_axes가 이미 축을 박제 중이라 재료는 쌓인다).
"""
from __future__ import annotations

import csv
import sqlite3
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from research_early_exit_no_bump import (  # noqa: E402
    BAND, BE_LOCK_PCT, FEE_ROUND_TRIP, SL_PCT, TP_PCT, bars, cluster_t,
)
from tools.us_daily_alpha_walkforward import load_yahoo_dataset  # noqa: E402

YAHOO_DB = ROOT / "data" / "analysis" / "us_yahoo_point_in_time.db"
PROXY_CHG = -5.0
HOLD = 5
CSV_START = "2025-01-27"


def path_and_axes(ticker: str, session_date: str) -> dict | None:
    """신호일 봉에서 IBS를 계산하고 D0..D5 경로를 만든다."""
    b = bars(ticker)
    i = next((j for j, x in enumerate(b) if x[0] == session_date), None)
    if i is None or i + 1 >= len(b):
        return None
    _d, o, hi, lo, c, _v = b[i]
    ibs = (c - lo) / (hi - lo) * 100.0 if hi > lo else None
    intraday = (c / o - 1) * 100.0 if o else None
    # 진입은 신호일 다음 세션 시가 기준(라이브와 같은 규약)
    win = b[i + 1: i + 2 + HOLD]
    if len(win) < 2:
        return None
    return {"ibs": ibs, "intraday": intraday, "entry": win[0][1], "win": win}


def contract_net(entry: float, win: list[tuple]) -> float:
    """TP=일봉 high, SL·BE락=종가 (08-30 확립 규약)."""
    peak = (win[0][4] - entry) / entry * 100.0
    for i, (_d, _o, hi, _lo, c, _v) in enumerate(win):
        hip = (hi - entry) / entry * 100.0 if i > 0 else (c - entry) / entry * 100.0
        cp = (c - entry) / entry * 100.0
        if hip >= TP_PCT:
            return TP_PCT - FEE_ROUND_TRIP
        if cp <= SL_PCT:
            return cp - FEE_ROUND_TRIP
        if peak >= BE_LOCK_PCT and cp <= 0:
            return cp - FEE_ROUND_TRIP
        peak = max(peak, hip)
    return (win[-1][4] - entry) / entry * 100.0 - FEE_ROUND_TRIP


def dvol_m(ticker: str, session_date: str) -> float | None:
    for d, _o, _h, _l, c, v in bars(ticker):
        if d == session_date:
            return c * v / 1e6
    return None


def report(label: str, sel: list[dict], rej: list[dict], key: str = "cnet") -> tuple:
    if len(sel) < 15 or len(rej) < 15:
        print(f"  {label:22s} 표본부족 (선택 {len(sel)} / 배제 {len(rej)})")
        return None
    a = [r[key] for r in sel]
    b = [r[key] for r in rej]
    ta = cluster_t([(r["ticker"], r[key]) for r in sel])
    diff = st.mean(a) - st.mean(b)
    print(f"  {label:22s} 선택 n={len(a):3d} {st.mean(a):+6.2f}% t={ta:+5.2f} | "
          f"배제 n={len(b):3d} {st.mean(b):+6.2f}% | 차이 {diff:+6.2f}%p")
    return diff, ta


def main() -> int:
    con = sqlite3.connect(f"file:{YAHOO_DB}?mode=ro", uri=True, timeout=20)
    df = load_yahoo_dataset(con, horizon=HOLD)
    con.close()
    dl = df[(df["change_pct"] <= PROXY_CHG) & (df["session_date"] >= CSV_START)]

    rows = []
    for rec in dl.itertuples():
        pa = path_and_axes(rec.ticker, rec.session_date)
        if not pa or pa["ibs"] is None:
            continue
        rows.append({
            "ticker": rec.ticker, "session_date": rec.session_date,
            "change_pct": rec.change_pct, "ibs": pa["ibs"], "intraday": pa["intraday"],
            "cnet": contract_net(pa["entry"], pa["win"]),
            "matnet": getattr(rec, f"net_krw_{HOLD}d_pct"),
            "dvol": dvol_m(rec.ticker, rec.session_date),
            "year": str(rec.session_date)[:4],
        })

    print("=== 계열A 축 OOS 재현 검정 (봉인 교재, 사전등록 2026-08-31) ===")
    print(f"표본 {len(rows)}행 / 종목 {len({r['ticker'] for r in rows})}개 | "
          f"{min(r['session_date'] for r in rows)} ~ {max(r['session_date'] for r in rows)}")
    print("shadow 발견 표본(2026-07-10~08-28)과 기간 교집합 없음 — 완전 OOS\n")

    print(f"[baseline] 계약 net 평균 {st.mean([r['cnet'] for r in rows]):+.2f}% | "
          f"만기 net(TP/SL 없음) {st.mean([r['matnet'] for r in rows]):+.2f}% "
          f"— 두 값의 차이가 계약의 효과다")
    inb = [r for r in rows if r["dvol"] is not None and BAND[0] <= r["dvol"] <= BAND[1]]
    print(f"  밴드(100~500M) 안 {len(inb)}행 ({100*len(inb)/len(rows):.0f}%) — "
          f"교재는 대형주 위주라 밴드가 적게 걸린다\n")

    med_chg = st.median([r["change_pct"] for r in rows])
    med_ibs = st.median([r["ibs"] for r in rows])
    print(f"[임계] 등락 중앙 {med_chg:+.2f}% | IBS 중앙 {med_ibs:.1f} "
          f"(shadow 임계 이식하지 않고 표본 내부에서 산출)\n")

    print("[전체 표본]")
    r1 = report("등락(hi)", [r for r in rows if r["change_pct"] >= med_chg],
                [r for r in rows if r["change_pct"] < med_chg])
    r2 = report("IBS(hi)", [r for r in rows if r["ibs"] >= med_ibs],
                [r for r in rows if r["ibs"] < med_ibs])

    print("\n[연도 분해 — 기준③]")
    for y in sorted({r["year"] for r in rows}):
        sub = [r for r in rows if r["year"] == y]
        print(f" {y} (n={len(sub)})")
        report("  등락(hi)", [r for r in sub if r["change_pct"] >= med_chg],
               [r for r in sub if r["change_pct"] < med_chg])
        report("  IBS(hi)", [r for r in sub if r["ibs"] >= med_ibs],
               [r for r in sub if r["ibs"] < med_ibs])

    if inb:
        print("\n[밴드 안 부분집합 — 참고]")
        report("등락(hi)", [r for r in inb if r["change_pct"] >= med_chg],
               [r for r in inb if r["change_pct"] < med_chg])
        report("IBS(hi)", [r for r in inb if r["ibs"] >= med_ibs],
               [r for r in inb if r["ibs"] < med_ibs])

    print("\n[대조] 만기 net 기준(TP/SL 없음) — 계약 적용 전후 비교용")
    report("등락(hi)", [r for r in rows if r["change_pct"] >= med_chg],
           [r for r in rows if r["change_pct"] < med_chg], key="matnet")
    report("IBS(hi)", [r for r in rows if r["ibs"] >= med_ibs],
           [r for r in rows if r["ibs"] < med_ibs], key="matnet")

    print("\n판정: ①차이 부호 선택군 우위 ②선택군 클러스터 t>=2 ③연도 부호 일치")
    print("      셋 다 만족 시 OOS 재현으로 기록. 통과해도 라이브 제안하지 않는다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
