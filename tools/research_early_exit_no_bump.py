#!/usr/bin/env python3
"""무봉우리형 조기 탈출 counterfactual (2026-08-30 사전등록).

배경: 301af84(08-28)가 무봉우리형 공략의 두 경로 중 (a) 진입 시점 예고 후
배제를 검정해 **기각**했다(후보 10종 중 모델확률만 부분 생존, 3기준 미달).
남은 경로가 (b) **초기 무반등 시 조기 손절**이고 이 스크립트가 그것을 검정한다.

무봉우리형 = 진입 후 한 번도 +4%를 못 가보는 유형. 표본의 36%인데 손실 기여
-376%p를 차지하고, 어떤 출구 규칙(TP·트레일·BE락)도 손대지 못한다 — 봉우리가
없으므로 BE락이 발동할 수 없다. 실거래 실측: AXTI MFE +0.00%(net -19.97%),
SEI +0.93%, SYRE +0.65%, MXL(08-20) +2.42%.

== 사전등록 (결과 확인 전 고정) ==

**가설**: 진입 후 D_k까지 MFE가 θ% 미만이면 그 시점에 끊는 것이 D7까지 들고
가는 것보다 net이 낫다.

**1차 가설 셀 (사전 지정)**: k=2, θ=4.0.
  - k=2: 08-27 출구 분해의 "D1~D2까지 MFE가 거의 없고 손실만 확대" 문구에서 도출.
  - θ=4.0: 무봉우리 정의(MFE<4%)·BE락 임계와 **동일 값**을 쓴다. 새 자유도를
    만들지 않기 위함이다. 나머지 14셀은 감도분석이며 1차 판정 근거가 아니다.

**격자**: k ∈ {1,2,3} × θ ∈ {0,1,2,3,4}% = 15셀.
  사전등록 §다중검정 규약에 따라 **시도 수 N에 +15를 기재**한다.

**baseline = 원장 실측 (시뮬 재생 금지)**.
  최초 설계는 TP12/SL25/D7/BE락4를 일봉으로 재시뮬해 baseline을 만들려 했으나,
  **실거래 9건 대조에서 2건만 재현돼 폐기했다**(2026-08-30 실측):
    CVI 실제 D7 +0.59 vs 시뮬 D7 +11.25 (D5 계약기 건에 D7 적용)
    FA  실제 D7 -1.33 vs 시뮬 BE_LOCK 0.00 (BE락 오발동)
    RGTI 실제 BE락 -0.45 vs 시뮬 D7 -5.69 (BE락 미발동)
    AXTI 실제 D7 -19.97 vs 시뮬 SL -25.00 (SL 오발동)
  원인은 구현 결함이 아니라 **원리적 재현 불가**다. 실제 출구는
  `_fixed_horizon_strategy_exit_candidates`가 장 마감 15분 창
  (`PROFIT_STRATEGY_HORIZON_EXIT_WINDOW_MIN`)에서 폴링 시점의 실시간 가격으로
  판정하고, Claude 매도 리뷰 게이트까지 개입한다. 일봉 low/high는 봇이 본 적
  없는 순간가라 SL·BE락을 과대발동시킨다.
  → **baseline은 원장의 실제 net을 그대로 쓴다.** counterfactual은 규칙이
    발동한 건만 "D_k 종가에 끊었다면"으로 대체한다. 이러면 baseline 재현 오류가
    구조적으로 제거되고, 유일한 근사는 D_k 종가 체결인데 실제 horizon exit도
    마감 15분 창이라 종가 근사가 정확하다.
  실제 청산이 D_k 이전인 건은 규칙이 발동할 수 없으므로 원장 net을 유지한다.

**단위**: 원장 `net_krw_pct`는 FX 포함 KRW net이고 counterfactual은 USD 기준이라
섞을 수 없다. 양쪽 모두 **USD gross - 수수료**로 통일한다(`gross_usd_pct` 사용).
FX 차이는 무시하며 이 값은 상대 비교 전용이다.

**일봉 근사 규약 (2026-08-30 실측 검증 후 고정)**:
  sleeve_mfe_path.jsonl 12건(US)과 대조한 결과 오차의 주원인은 봉 자체가 아니라
  **창 경계** 두 곳이었다.
    - D0(진입 당일) 봉의 high는 **진입 시각 이전** 구간을 포함한다.
      실측: AXTI 08-19 진입가 82.09(13:36Z, 개장 6분 후)인데 당일 high 85.45가
      개장 직후 값이라 일봉이 +4.09%를 만든다. 실측 MFE는 -1.88%다.
    - 청산일 봉의 high는 **청산 시각 이후** 구간을 포함한다.
      실측: MXL 08-11은 TP12로 08-14 청산인데 그날 high 85.0은 체결 후 값이라
      일봉이 +21.15%를 만든다. 실측 MFE는 +11.56%다.
  → 규약: **D0의 MFE는 종가만 사용**(보수적, 진입 후 상승만 인정),
    **D1 이후는 high 사용**. 청산일 문제는 이 스크립트가 실측 청산일을 쓰지 않고
    baseline을 처음부터 재시뮬하므로 발생하지 않는다.
  중간 구간(D1..D_{n-1}) 봉은 실측과 일치하며, 실측(스냅샷 주기 이산 관측)이
  오히려 과소추정 쪽이다.

**동시 도달 순서**: TP와 SL이 같은 봉에서 모두 닿으면 **SL 우선**(보수).
갭 보너스는 무시하고 임계가로 체결됐다고 본다 — 리포트 [A11]의 보수 하한 관례와
같은 방향이다.

**비용**: 왕복 수수료 0.48%(실측 fee_pct_round_trip 0.45~0.50%의 중앙)를 gross에서
차감한다. FX는 시뮬 대상이 아니므로 **USD net 근사**이며, 이 값은 판정용 상대
비교에만 쓴다 — 우리 KRW net 원장을 대체하지 않는다.

**판정 기준 (301af84와 동일 3종, 고정)**:
  ① 방향 일관 + 종목 클러스터 t >= 2
  ② 월별 부호 일치
  ③ 밴드(100~500M) 부분집합에서 유지
  셋 다 만족해야 운영자에게 반영을 제안한다. 미달이면 관측 가설로 등록만 한다.

**반대 가설(반드시 함께 산출)**: "무봉우리 = 아직 안 온 것"이라면 규칙은 늦게
피는 건을 전부 끊는다. **D6~D7에 처음 +4%를 만든 건수**를 세고, 규칙이 끊은
건들의 이후 경로(놓침)를 분해한다.

**용량 관점**: 조기 청산이 푸는 슬롯-일수는 보고하되 **가상 재진입 이득을 net에
합산하지 않는다** — 검증 불가한 가정이다.

**표본 성격**: shadow 근사이며 판정 표본이 아니다. 실거래 정산은 계약 발효 후
9건뿐이고 현행 선별(밴드+MAX) 코호트는 0건이다. 이 스크립트의 결과는 방향
탐색이며 반영 결정은 운영자 몫이다.

== 판정 (2026-08-30 실측, 표본 209건/156종목) ==

**기각.** 1차 가설 셀(k=2, θ=4)은 net 합계 **-122.0%p**(건당 -0.584%p)로 악화했고
사전등록 3기준을 전부 미달했다.
  ① 클러스터 t = -0.81, 방향 악화 → 미달
  ② 월별 07월 -90.2%p / 08월 -31.8%p — 부호는 일치하나 **둘 다 악화** → 미달
  ③ 밴드(100~500M) n=84: 합 -108.1%p, 클러스터 t=-1.72 → 미달
**격자 15셀이 전부 음수**다(최선인 k=3·θ=0도 -27.1%p). 파라미터를 어떻게 잡아도
개선이 없으므로 이것은 격자 선택의 문제가 아니다.

**반대 가설이 데이터로 이겼다 — "무봉우리 = 아직 안 온 것"이 맞다.**
첫 +4% 도달 시점 분포: D0 27 / D1 56 / D2 17 / **D3 10 / D4 4** / 없음 95.
k=2 규칙은 109건(52%)을 끊는데 무봉우리는 95건이다 — 즉 **D3~D4에 필 14건을
잘못 끊는다.** 살림 46건 +203.1%p vs 놓침 63건 -325.1%p로 놓침이 압도한다.
극단 반례: AXTI 07-27은 D2 MFE **-1.28%**(규칙 발동 대상)였는데 원장 net
**+24.12%**로 끝났다. BE 07-27도 D2 -1.22% → +7.55%.

**무봉우리형이 최대 손실원이라는 사실 자체는 재확인됐다** — 95건(45%), 평균
-6.83%, 승률 14%, 합계 -649.1%p(전체 -144.1%p 중). 문제는 그것을 **초기에
식별할 수 없다는 것**이다. 301af84가 진입 시점 예고(a)를 기각했고 이 검정이
보유 초기 식별(b)을 기각했다. 무봉우리는 제거 가능한 결함이 아니라 TP12/SL25
비대칭 계약의 **구조적 비용**으로 보는 것이 실측에 부합한다.

**표본 한계(명시)**: shadow 209건은 전부 D5 계약기라 보유일이 D4로 균일하다.
현행 D7 창에서는 늦게 피는 건(D3~D4에 14건)이 더 회수될 것이므로 조기탈출은
**더 불리해지는 방향**이지 유리해질 여지가 아니다. 또한 baseline이 원장 실측이라
US gross 기준이며 우리 KRW net 원장과 단위가 다르다.

**남은 경로**: 출구 축에서 무봉우리를 공략하는 길은 (a)(b) 모두 닫혔다. 손실
축소는 진입 선별(밴드+MAX)이 유일하게 열린 축이고 그것은 이미 라이브이며
G3b 정산을 기다리는 중이다. 재검 조건: 현행 계약 정산 30건 시점에 D7 창
실측으로 재산출.

== 2차 검증 (2026-08-30, baseline 오류 수정 후 재계산) ==

위 판정 직후 **원장 baseline 자체가 현행 계약과 다르다**는 것을 발견했다 —
shadow는 TP12/SL25가 없는 순수 만기 보유 시뮬이다(`contract_exit` docstring
참조). 상방이 안 잘리므로 조기탈출의 "놓침"이 과대평가된 상태였다.

`contract_exit`로 TP/SL/BE락을 얹어 격자를 재산출한 결과:

  k\θ         0%       1%       2%       3%       4%
  k=1     -104.6   -141.9   -133.8   -126.0    -90.7
  k=2      -70.7    -90.3   -105.0   -128.6   -117.0★
  k=3      -31.5    -40.2    -50.3    -53.2    -39.0

**기각 판정은 그대로다.** 1차 가설 셀이 -122.0 → -117.0%p로 거의 변하지 않고
15셀이 여전히 전부 음수다. baseline 오류에도 불구하고 결론이 견고했던 이유는,
조기탈출 대상(MFE<θ)이 애초에 TP를 못 잡는 건들이라 TP 상한의 영향이 작기
때문이다. 계약 baseline 자체는 평균 -0.77%/합계 -161.7%p(출구 D_MAT 131·TP 41·
BE 35·SL 2)로 원장 baseline(-0.69%/-144.1%p)보다 나쁘다.
"""
from __future__ import annotations

import csv
import math
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNALS_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
PRICE_DIR = ROOT / "data" / "price" / "us"

NO_BUMP_PCT = 4.0
TP_PCT = 12.0
SL_PCT = -25.0
MAX_HOLD = 7
BE_LOCK_PCT = 4.0
FEE_ROUND_TRIP = 0.48
BAND = (100.0, 500.0)
PRIMARY_CELL = (2, 4.0)
GRID_K = (1, 2, 3)
GRID_THETA = (0.0, 1.0, 2.0, 3.0, 4.0)

_BAR_CACHE: dict[str, list[tuple]] = {}


def bars(ticker: str) -> list[tuple]:
    """일봉 (date, open, high, low, close, volume). 정렬 보장."""
    if ticker not in _BAR_CACHE:
        path = PRICE_DIR / f"us_{ticker}.csv"
        rows: list[tuple] = []
        if path.exists():
            with path.open(encoding="utf-8-sig") as fh:
                for r in csv.reader(fh):
                    if len(r) >= 6 and r[0][:2] == "20":
                        try:
                            rows.append((r[0], float(r[1]), float(r[2]), float(r[3]),
                                         float(r[4]), float(r[5])))
                        except ValueError:
                            continue
        _BAR_CACHE[ticker] = sorted(rows)
    return _BAR_CACHE[ticker]


def signal_day_dvol_m(ticker: str, signal_date: str) -> float | None:
    """신호일 거래대금(M USD) = close x volume. 진입 결정 시점 가용값이다.

    candidate_pool_all의 dollar_vol은 08-17 이후 619행뿐이라 07월 표본을 못
    덮는다 — 301af84와 같이 가격 봉에서 직접 계산한다.
    """
    for d, _o, _h, _l, c, v in bars(ticker):
        if d == signal_date:
            return c * v / 1e6
    return None


def path_window(ticker: str, entry_date: str) -> list[tuple]:
    """진입일 포함 D0..D7 (최대 8봉). 부족하면 있는 만큼."""
    b = bars(ticker)
    idx = next((i for i, x in enumerate(b) if x[0] >= entry_date), None)
    if idx is None:
        return []
    return b[idx: idx + MAX_HOLD + 1]


def mfe_at(win: list[tuple], entry_price: float, k: int) -> float:
    """D0..Dk 누적 MFE(%). D0은 종가만, D1+는 high (사전등록 근사 규약)."""
    if not win or entry_price <= 0:
        return float("nan")
    best = (win[0][4] - entry_price) / entry_price * 100.0
    for bar in win[1: k + 1]:
        best = max(best, (bar[2] - entry_price) / entry_price * 100.0)
    return best


def contract_exit(rec: dict, cut_k: int | None = None,
                  cut_theta: float | None = None) -> tuple[float, str, int]:
    """현행 계약(TP12/SL25/BE락4)을 shadow 경로에 얹는다 (2026-08-30 2차 수정).

    **왜 필요한가**: shadow 원장은 TP/SL이 없는 **순수 만기 보유** 시뮬이다
    (실측: gross>+12% 18건·최대 +31.6%, gross<-25% 2건·최소 -43.2%;
    MXL 08-11 shadow +20.23% vs 실거래 +12.46% TP). 원장을 그대로 baseline으로
    쓰면 상방이 안 잘려 조기탈출의 "놓침"이 과대평가되고, 밴드의 수익 기여도
    과대평가된다.

    **판정 기준이 규칙마다 다른 이유**: TP는 일봉 high로 판정해도 실거래
    3/3(FRMI·MXL·WIX)을 정확히 재현한다 — 지정가 도달이라 순간가로도 체결된다.
    반면 SL·BE락을 일봉 low로 판정하면 과대발동한다(AXTI가 SL로 잘못 잡힘) —
    실제로는 폴링 시점 가격이라 종가 근사가 맞다. 그래서 **TP=high, SL·BE락=종가**.
    이 조합으로 실거래 재현이 2/9 → 6/9로 올라간다. 남은 미스매치 2건은 BE락
    (FA 오발동·FRVO 미발동)이며 이는 명시된 한계다.
    """
    ep, win, held = rec["entry_price"], rec["win"], rec["held_sessions"]
    peak = (win[0][4] - ep) / ep * 100.0
    for i in range(0, held + 1):
        _d, _o, hi, _lo, c, _v = win[i]
        hi_pct = (hi - ep) / ep * 100.0 if i > 0 else (c - ep) / ep * 100.0
        c_pct = (c - ep) / ep * 100.0
        if hi_pct >= TP_PCT:
            return TP_PCT - FEE_ROUND_TRIP, "TP", i
        if c_pct <= SL_PCT:
            return c_pct - FEE_ROUND_TRIP, "SL", i
        if peak >= BE_LOCK_PCT and c_pct <= 0:
            return c_pct - FEE_ROUND_TRIP, "BE", i
        peak = max(peak, hi_pct)
        if cut_k is not None and i == cut_k and i < held:
            if mfe_at(win, ep, i) < float(cut_theta):
                return c_pct - FEE_ROUND_TRIP, "CUT", i
    return rec["base_net"], "D_MAT", held


def apply_rule(rec: dict, k: int, theta: float) -> tuple[float, bool]:
    """조기탈출 규칙 적용. 반환: (USD net %, 발동 여부).

    발동 조건: 실제 보유일 > k 이고, D_k까지의 MFE < theta.
    발동 시 D_k 종가에 청산했다고 본다. 아니면 원장 실측 net 유지.
    """
    base = rec["base_net"]
    win, ep, held = rec["win"], rec["entry_price"], rec["held_sessions"]
    if held is None or held <= k or len(win) <= k:
        return base, False
    if mfe_at(win, ep, k) >= theta:
        return base, False
    close_k = win[k][4]
    return (close_k - ep) / ep * 100.0 - FEE_ROUND_TRIP, True


def cluster_t(pairs: list[tuple[str, float]]) -> float:
    """종목 클러스터 t (종목별 평균을 표본으로). k<2면 nan."""
    by: dict[str, list[float]] = defaultdict(list)
    for tkr, v in pairs:
        by[tkr].append(v)
    means = [st.mean(v) for v in by.values()]
    if len(means) < 2:
        return float("nan")
    sd = st.pstdev(means) * math.sqrt(len(means) / (len(means) - 1))
    if sd == 0:
        return float("nan")
    return st.mean(means) / (sd / math.sqrt(len(means)))


def load_sample() -> list[dict]:
    if not SIGNALS_DB.exists():
        print(f"[ERROR] shadow DB 없음: {SIGNALS_DB}")
        return []
    con = sqlite3.connect(f"file:{SIGNALS_DB}?mode=ro", uri=True, timeout=10)
    try:
        rows = con.execute(
            """SELECT signal_date, ticker, entry_date, entry_price, candidate_source,
                      probability, gross_usd_pct, exit_date
               FROM signals WHERE status='MATURED' AND entry_price>0
                 AND gross_usd_pct IS NOT NULL
               ORDER BY signal_date"""
        ).fetchall()
    finally:
        con.close()

    out = []
    for sd, tk, ed, ep, src, prob, gross, xd in rows:
        t = str(tk).upper()
        entry_date = str(ed or sd)
        win = path_window(t, entry_date)
        if len(win) < 2:
            continue
        held = None
        if xd:
            held = next((i for i, b in enumerate(win) if b[0] >= str(xd)), None)
        if held is None:
            held = len(win) - 1
        out.append({
            "signal_date": str(sd), "ticker": t, "entry_date": entry_date,
            "entry_price": float(ep), "source": str(src or ""),
            "probability": prob, "base_net": float(gross) - FEE_ROUND_TRIP,
            "held_sessions": held, "win": win,
            "dvol": signal_day_dvol_m(t, str(sd)),
        })
    return out


def net_of(gross: float) -> float:
    return gross - FEE_ROUND_TRIP


def report(sample: list[dict]) -> None:
    print("=== 무봉우리형 조기 탈출 counterfactual (사전등록 2026-08-30) ===")
    print(f"표본 {len(sample)}건 / 종목 {len({s['ticker'] for s in sample})}개 "
          f"— shadow 근사, 판정 표본 아님")
    print("baseline = 원장 실측(USD gross - 수수료). 시뮬 재생은 실거래 재현 실패로 폐기.\n")

    base_nets = [s["base_net"] for s in sample]
    print(f"[baseline] 평균 {st.mean(base_nets):+.2f}% | 합계 {sum(base_nets):+.1f}%p | "
          f"승률 {100*sum(1 for x in base_nets if x>0)/len(base_nets):.0f}%")
    held_dist = defaultdict(int)
    for s in sample:
        held_dist[s["held_sessions"]] += 1
    print(f"  보유일 분포: {dict(sorted(held_dist.items()))}\n")

    # 무봉우리 실태 — 실제 보유창 기준
    nb = [s for s in sample if mfe_at(s["win"], s["entry_price"], s["held_sessions"]) < NO_BUMP_PCT]
    print(f"[무봉우리 실태] 실제 보유창 MFE<{NO_BUMP_PCT}%: {len(nb)}건 ({100*len(nb)/len(sample):.0f}%)")
    if nb:
        nbn = [s["base_net"] for s in nb]
        print(f"  평균 {st.mean(nbn):+.2f}% | 합계 {sum(nbn):+.1f}%p "
              f"(전체 {sum(base_nets):+.1f}%p 중) | 승률 {100*sum(1 for x in nbn if x>0)/len(nbn):.0f}%\n")

    # 반대 가설 — 봉우리가 언제 처음 서는가
    print(f"[반대 가설] 첫 +{NO_BUMP_PCT}% 도달 시점 분포 (규칙이 끊었을 대상)")
    first_hit = defaultdict(int)
    for s in sample:
        hit = None
        for k in range(0, s["held_sessions"] + 1):
            if mfe_at(s["win"], s["entry_price"], k) >= NO_BUMP_PCT:
                hit = k
                break
        first_hit["없음" if hit is None else f"D{hit}"] += 1
    print(f"  {dict(sorted(first_hit.items(), key=lambda x: (x[0]=='없음', x[0])))}")
    late = sum(v for kk, v in first_hit.items() if kk not in ("없음", "D0", "D1", "D2"))
    print(f"  → D3 이후에 처음 서는 건 {late}건 ({100*late/len(sample):.0f}%) — k=2 규칙이 전부 끊는다\n")

    # 격자
    print(f"[격자] net 합계 변화 %p (양수면 개선, 1차 가설 k={PRIMARY_CELL[0]} θ={PRIMARY_CELL[1]:.0f} ★)")
    print("  k\\θ  " + "".join(f"{th:>8.0f}%" for th in GRID_THETA))
    grid: dict[tuple, list] = {}
    for k in GRID_K:
        cells = []
        for th in GRID_THETA:
            deltas = []
            for s in sample:
                cf, fired = apply_rule(s, k, th)
                deltas.append((s["ticker"], cf - s["base_net"], fired, s))
            grid[(k, th)] = deltas
            tot = sum(d for _t, d, _f, _s in deltas)
            cells.append(f"{tot:>+8.1f}{'★' if (k, th) == PRIMARY_CELL else ' '}")
        print(f"  k={k}  " + "".join(cells))
    print()

    # 1차 가설 정밀
    k, th = PRIMARY_CELL
    deltas = grid[(k, th)]
    fired = [(t, d, s) for t, d, f, s in deltas if f]
    saved = [(t, d) for t, d, _s in fired if d > 0]
    missed = [(t, d) for t, d, _s in fired if d < 0]
    tot = sum(d for _t, d, _f, _s in deltas)
    print(f"[1차 가설 정밀] k={k}, θ={th:.0f}%")
    print(f"  발동 {len(fired)}건 / 표본 {len(sample)}건 ({100*len(fired)/len(sample):.0f}%)")
    print(f"  살림 {len(saved)}건 합 {sum(d for _t, d in saved):+.1f}%p | "
          f"놓침 {len(missed)}건 합 {sum(d for _t, d in missed):+.1f}%p")
    print(f"  순효과 {tot:+.1f}%p (표본 건당 {tot/len(sample):+.3f}%p)")
    ct = cluster_t([(t, d) for t, d, _f, _s in deltas])
    ok1 = ct == ct and abs(ct) >= 2 and tot > 0
    print(f"  기준① 클러스터 t = {ct:.2f} | 방향 {'개선' if tot > 0 else '악화'} "
          f"→ {'통과' if ok1 else '미달'}")

    by_month: dict[str, list[float]] = defaultdict(list)
    for _t, d, _f, s in deltas:
        by_month[s["signal_date"][:7]].append(d)
    months = {m: sum(v) for m, v in sorted(by_month.items())}
    signs = {("+" if v > 0 else "-") for v in months.values() if v != 0}
    print("  기준② 월별: " + " | ".join(f"{m} {v:+.1f}%p(n={len(by_month[m])})" for m, v in months.items())
          + f" → {'통과' if len(signs) <= 1 and tot > 0 else '미달'}")

    band = [(t, d) for t, d, _f, s in deltas
            if s.get("dvol") is not None and BAND[0] <= s["dvol"] <= BAND[1]]
    if band:
        bsum = sum(d for _t, d in band)
        print(f"  기준③ 밴드 n={len(band)}: 합 {bsum:+.1f}%p, "
              f"클러스터 t={cluster_t(band):.2f} → {'통과' if bsum > 0 else '미달'}")
    else:
        print("  기준③ 밴드: 거래대금 원장 매칭 0건 — 판정 불가")

    freed = sum(s["held_sessions"] - k for _t, _d, s in fired)
    print(f"\n[용량 관점·정성] 조기 청산이 푸는 슬롯-일수 {freed}일 "
          f"(재진입 이득은 net에 합산하지 않음)")

    print("\n[최악 놓침 상위 5건]")
    for t, d, s in sorted(fired, key=lambda x: x[1])[:5]:
        print(f"  {t:6s} {s['signal_date']} 규칙 {d:+7.2f}%p | "
              f"원장 {s['base_net']:+7.2f}% | D{k}MFE {mfe_at(s['win'], s['entry_price'], k):+6.2f}% "
              f"| 보유 D{s['held_sessions']}")


def main() -> int:
    sample = load_sample()
    if not sample:
        print("[ERROR] 표본 0건")
        return 1
    report(sample)
    return 0


if __name__ == "__main__":
    sys.exit(main())
