#!/usr/bin/env python3
"""밴드 자체의 OOS 검정 — 봉인 교재 21개월 (2026-08-31 사전등록).

**왜 지금 하는가**: 08-30~31 이틀간 계열B(MAX·ATR)가 월 분할 OOS에서, 계열A
(등락·IBS)가 교재 21개월 OOS에서 각각 무너졌다. 그런데 **거래대금 밴드는 같은
검정을 받은 적이 없다.** 근거는 08-20 in-sample 224세션(클러스터 t=2.63) 하나이고,
08-25 OOS 시도는 "프록시 검정력 부족"으로 판정 불가로 끝났다.

밴드는 우리가 라이브에서 쓰는 **유일한 선별 축**이다(MAX는 밴드 위에 얹힌
2차 필터). 계열A가 두 달 노이즈였다면 밴드도 그럴 수 있고, 그렇다면 지금 알아야
한다. 결과가 어느 쪽이든 중요하다.

== 사전등록 (결과 확인 전 고정) ==

**표본**: research_textbook_oos_axes와 동일 — 봉인 교재 day_losers 프록시
  (change_pct <= -5) 중 가격 CSV 커버 구간, 2025-04-21~2026-04-02.
  shadow 발견 표본(2026-07~08)과 교집합 없음.

**baseline**: 계약 적용(TP=일봉 high, SL·BE락=종가). 08-30 확립 규약.

**판정 지표**: 밴드 안 - 밴드 밖 차이. 절대 net은 판정에 쓰지 않는다.
  **핵심은 평균이 아니라 꼬리다** — 08-30이 특정한 밴드의 기전은 하방 꼬리
  억제였다(shadow: 하위10% -15.09% vs -26.21%, 평균 차이는 +2.55%p에 불과).
  따라서 평균이 안 나와도 꼬리가 재현되면 기전은 살아있는 것이고, 꼬리마저
  안 나오면 08-30 기전 특정이 두 달 표본의 산물이었다는 뜻이다.

**판정 기준(고정)**:
  ① 꼬리(하위20% 평균)에서 밴드 안이 얕다
  ② 밴드 안 클러스터 t >= 2 또는 차이의 방향이 연도별로 일치
  ③ 현행 라이브 조합(밴드+MAX>=8)이 밴드 단독보다 나쁘지 않다
  ①②를 만족하면 "기전 OOS 재현"으로 기록한다. ③은 별도 관측 —
  08-30 shadow에서 MAX>=8이 역방향으로 나왔기 때문에 확인이 필요하다.

**한계(명시)**: 교재는 128종목(day_losers 70종목)이고 대형주 위주라 밴드 안이
  24%뿐이다. 우리 실제 유니버스는 1656종목이므로 **모집단이 다르다**. 이것이
  08-25 실패의 원인이었고("후보 재구성 프록시는 스크리너 모집단을 못 담는다"),
  여기서도 절대 수치로 판정하지 않는 이유다. 차이의 부호와 꼬리 구조만 읽는다.

**시도 수 N +3** (밴드 / 밴드+MAX / 꼬리 지표).

== 판정 (2026-08-31 실측, 556행/70종목) ==

**평균 우위는 재현. 기전(꼬리 억제)은 재현 실패. MAX는 부호 역전.**

                      평균      꼬리    무봉우리   클러스터t
  밴드 안 (n=133)    +2.53%  -12.74%     23%     +1.45
  밴드 밖 (n=423)    +1.20%  -12.65%     26%     +2.91
  차이              +1.33%p  **-0.09%p**  -3%p
  [shadow 대조]     +2.55%p   +3.77%p    +8%p

기준① **미달** — 꼬리 차이가 -0.09%p로 사실상 0이고, 연도별로 2025 +0.30 /
  2026 **-1.24**로 부호가 뒤집힌다. shadow에서 +3.77%p였던 꼬리 억제가 없다.
기준② **미달** — 밴드 안 t=+1.45. 오히려 밴드 밖이 t=+2.91로 높다.
기준③ **역전** — 교재에서는 MAX>=8이 +2.16%p **더 좋다**
  (밴드+MAX>=8 +2.99% vs 밴드+MAX<8 +0.84%). shadow에서는 -1.49%p로 반대였다.

**해석 — 세 층으로 나눠 읽는다.**

1) **밴드의 평균 우위는 두 표본에서 같은 방향**이다(+1.33 / +2.55%p). 08-20의
   224세션 근거(t=2.63)까지 셋이 방향은 일치한다. 약하지만 지지된다.
   다만 이 표본에서 클러스터 t는 1.45로 미달이므로 "검증됐다"고 쓰지 않는다.

2) **08-30의 기전 특정("밴드 = 하방 꼬리 억제")은 철회한다.** 그 결론은
   shadow 209건에서 하위10% -15.09% vs -26.21%를 근거로 했는데, 21개월
   OOS에서는 꼬리 차이가 0이고 무봉우리 방향마저 반대다(shadow는 밴드가
   무봉우리를 더 샀고, 교재는 덜 산다). **밴드가 왜 듣는지는 여전히 모른다**가
   정확한 상태다. 기전 설명 없이 평균 방향만 남는다.

3) **MAX는 판정 불가가 확정됐다.** 두 독립 표본에서 부호가 정반대다
   (shadow -1.49%p / 교재 +2.16%p). 08-20 in-sample은 채택 근거였고(t=4.58),
   08-25 OOS는 판정 불가였다. 이제 네 번째 표본에서도 갈렸다.
   **어느 쪽도 근거로 쓸 수 없다** — 라이브 MAX>=8은 유지하되(08-20 근거가
   가장 크고 운영자 승인 사항), 이 축으로 무엇을 주장하는 것은 중단한다.

**메타 관찰**: 이틀간 검정한 축 다섯(조기탈출·MAX·ATR·등락·IBS)이 전부 OOS에서
무너졌고, 밴드는 평균만 살아남았다. 공통점은 **발견 표본이 두 달이었다는 것**이다.
두 달 표본에서 클러스터 t가 3을 넘어도 21개월에서는 0이 될 수 있다는 것이
이번 이틀의 가장 실용적인 교훈이다. 30건 게이트가 "재앙 탐지기지 엣지
증명기가 아니다"(08-20)의 정량적 재확인이기도 하다.

**한계**: 교재 모집단(70종목, 대형주 위주)이 우리 유니버스(1656종목)와 다르다.
밴드 안이 24%뿐이라 밴드의 실제 작동 영역을 대표하지 못할 수 있다. 이 검정이
"밴드 무효"를 뜻하지 않는 이유이며, 동시에 "밴드 유효"의 증거로도 못 쓰는 이유다.
"""
from __future__ import annotations

import sqlite3
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from research_early_exit_no_bump import BAND, NO_BUMP_PCT, bars, cluster_t  # noqa: E402
from research_tail_risk_axes import tail_stats  # noqa: E402
from research_textbook_oos_axes import (  # noqa: E402
    CSV_START, HOLD, PROXY_CHG, YAHOO_DB, contract_net, dvol_m, path_and_axes,
)
from tools.us_daily_alpha_walkforward import load_yahoo_dataset  # noqa: E402


def max20(ticker: str, session_date: str) -> float | None:
    b = bars(ticker)
    i = next((j for j, x in enumerate(b) if x[0] == session_date), None)
    if i is None or i < 20:
        return None
    return max(100 * (b[j][4] / b[j - 1][4] - 1) for j in range(i - 19, i + 1))


def mfe_of(entry: float, win: list[tuple]) -> float:
    best = (win[0][4] - entry) / entry * 100.0
    for bar in win[1:]:
        best = max(best, (bar[2] - entry) / entry * 100.0)
    return best


def show(label: str, rows: list[dict]) -> dict | None:
    if len(rows) < 15:
        print(f"  {label:24s} n={len(rows):3d} (표본부족)")
        return None
    nets = [r["cnet"] for r in rows]
    tail, thick = tail_stats(nets)
    nb = 100.0 * sum(1 for r in rows if r["nb"]) / len(rows)
    t = cluster_t([(r["ticker"], r["cnet"]) for r in rows])
    print(f"  {label:24s} n={len(nets):3d} 평균{st.mean(nets):+6.2f}% "
          f"꼬리{tail:+7.2f}% 무봉우리{nb:3.0f}% t={t:+5.2f}")
    return {"n": len(nets), "avg": st.mean(nets), "tail": tail, "nb": nb, "t": t}


def main() -> int:
    con = sqlite3.connect(f"file:{YAHOO_DB}?mode=ro", uri=True, timeout=20)
    df = load_yahoo_dataset(con, horizon=HOLD)
    con.close()
    dl = df[(df["change_pct"] <= PROXY_CHG) & (df["session_date"] >= CSV_START)]

    rows = []
    for rec in dl.itertuples():
        pa = path_and_axes(rec.ticker, rec.session_date)
        if not pa:
            continue
        entry, win = pa["entry"], pa["win"]
        d = dvol_m(rec.ticker, rec.session_date)
        rows.append({
            "ticker": rec.ticker, "session_date": rec.session_date,
            "cnet": contract_net(entry, win),
            "nb": mfe_of(entry, win) < NO_BUMP_PCT,
            "dvol": d, "max20": max20(rec.ticker, rec.session_date),
            "in_band": d is not None and BAND[0] <= d <= BAND[1],
            "year": str(rec.session_date)[:4],
        })

    print("=== 밴드 OOS 검정 (봉인 교재, 사전등록 2026-08-31) ===")
    print(f"표본 {len(rows)}행 / 종목 {len({r['ticker'] for r in rows})}개 | "
          f"{min(r['session_date'] for r in rows)} ~ {max(r['session_date'] for r in rows)}")
    print("shadow 발견 표본과 기간 교집합 없음. 교재는 대형주 위주라 모집단이 다르다.\n")

    inb = [r for r in rows if r["in_band"]]
    out = [r for r in rows if r["dvol"] is not None and not r["in_band"]]
    print("[전체 기간]")
    a = show("밴드 안(100~500M)", inb)
    b = show("밴드 밖", out)
    if a and b:
        print(f"  → 평균 차이 {a['avg']-b['avg']:+.2f}%p | "
              f"꼬리 차이 {a['tail']-b['tail']:+.2f}%p | "
              f"무봉우리 차이 {a['nb']-b['nb']:+.0f}%p")
        print(f"  [shadow 대조] 평균 +2.55%p / 꼬리 +3.77%p / 무봉우리 +8%p\n")

    print("[연도 분해 — 기준②]")
    for y in sorted({r["year"] for r in rows}):
        si = [r for r in inb if r["year"] == y]
        so = [r for r in out if r["year"] == y]
        print(f" {y}")
        ya = show("  밴드 안", si)
        yb = show("  밴드 밖", so)
        if ya and yb:
            print(f"    → 평균 {ya['avg']-yb['avg']:+.2f}%p | 꼬리 {ya['tail']-yb['tail']:+.2f}%p")

    print("\n[기준③ 현행 라이브 조합]")
    hi = [r for r in inb if r["max20"] is not None and r["max20"] >= 8]
    lo = [r for r in inb if r["max20"] is not None and r["max20"] < 8]
    show("밴드+MAX>=8 (라이브)", hi)
    show("밴드+MAX<8", lo)
    if len(hi) >= 15 and len(lo) >= 15:
        print(f"  → MAX>=8 - MAX<8 = {st.mean([r['cnet'] for r in hi])-st.mean([r['cnet'] for r in lo]):+.2f}%p "
              f"(shadow에선 -1.49%p로 역방향이었다)")

    print("\n판정: ①꼬리에서 밴드 안이 얕은가 ②클러스터 t>=2 또는 연도 부호 일치")
    print("      ③밴드+MAX>=8이 밴드 단독보다 나쁘지 않은가")
    return 0


if __name__ == "__main__":
    sys.exit(main())
