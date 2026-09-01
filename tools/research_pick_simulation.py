#!/usr/bin/env python3
"""픽 시뮬 — "매수한 것만의 net"으로 선별 규칙을 판정한다 (2026-09-01 사전등록).

배경: 09-01 진단 보고서(docs/reports/system_diagnosis_and_selection_20260901.md)의
헤드라인 수치(현행 −5.83% vs 모델 제거 +0.89%)를 만든 스크립트가 커밋되지 않았다.
오늘 밤 모델 제거 적용(운영자 승인)의 근거이므로 **적용 전 재현이 필수**다.
동시에 운영자 지시 "풀 평균의 오류 금지 — 우리가 매수한 후보만의 수익성"을
조작화한다: 이 저장소의 기존 선별 검증은 전부 "통과분 전체 평균"이었는데
실제 매수는 "통과분 중 1건"이다. 검증 코호트 ≠ 매수 코호트. 이 스크립트는
그 간극을 없앤다 — 세션당 실제로 살 1건을 규칙별로 뽑아 **픽된 건만** 계산한다.

== 사전등록 (결과 확인 전 고정) ==

**표본 A — 우리 풀**: candidate_pool_all(2026-08-12~), eligible=1.
  session_date가 진입 세션이고 신호일은 그 직전 거래일이다(09-01 확립 규약 —
  이걸 틀려서 08-31 결론이 전부 철회됐다). chg_pct는 신호일 등락으로 기록돼 있다.
  진입 = session_date 시가. 정산은 출구 발동 또는 D7 창 완결 행만.

**표본 B — 봉인 교재**: us_yahoo_point_in_time.db day_losers 프록시(chg<=-5),
  research_textbook_oos_axes와 같은 구간. 신호일 다음 세션 시가 진입.
  발견 표본(표본 A·shadow)과 교집합 없음.

**계약**: TP12(일봉 high, D0은 종가만)/SL25(종가)/BE락4(종가)/D7, 수수료 0.48%.
  08-30 확립 규약(실거래 재현 6/9). 교재도 D7로 통일한다(라이브가 D7).

**선별 파이프**: 밴드(신호일 거래대금 100~500M) → MAX21>=8 → 픽 규칙으로 1건.
  현행(incumbent) 대조군은 모델 파이프 전체를 재현한다:
  scored top10 → 밴드 → MAX → 절대허들(prob>=0.55, pred_net>=0.25) → 모델 rank 1위.

**픽 규칙 (방향 포함 고정, 사후 추가 금지)**:
  dvol_desc  거래대금 큰순   — 보고서 §6-2의 잠정안 (이미 보고된 것, 재현)
  dvol_asc   거래대금 작은순 — 실거래 11건 승자 프로필(승 123M vs 패 273M)
  ibs_hi     신호일 IBS 높은순 — 승자 프로필(63 vs 38) + 도달률 축과 동방향
  chg_hi     신호일 등락 높은순(덜 빠진) — 승자 프로필(+1.80 vs −1.23)
  max_lo     MAX21 낮은순 — 승자 프로필(15.4 vs 20.5)
  시도 수 N **+4** (dvol_desc는 기보고, incumbent는 baseline이라 미계상).

**널 분포**: 세션당 무작위 1픽 × 2000 순열의 픽 평균 분포. 규칙의 백분위를 잰다.
  "무작위보다 낫다"를 증명 못 하는 규칙은 규칙이 아니다.

**판정 기준 (고정)**: 어떤 규칙이 "잠정 픽 순서 후보"가 되려면
  ① 두 표본 모두에서 무작위 널의 95% 백분위 초과
  ② 두 표본에서 픽 평균의 부호 일치
  ③ picked P(TP)와 실패군 평균 깊이가 무작위 대비 악화하지 않을 것
    (§5-2 함정 — 도달률↑인데 실패가 깊어지는 규칙 배제)
  전부 미달이면 "픽 순서는 통계적으로 구별 불가"가 결론이고, 순서는 운영자
  선택 + 랭킹 shadow 원장의 forward 축적으로 넘긴다. 통과해도 enforce 제안이
  아니라 잠정 순서(운영자 결정 대상)다.

**한계(명시)**: 표본 A는 정산 세션이 ~12개뿐이라 픽 12건으로는 어떤 t도 못
만든다(30건 게이트 미만) — 널 백분위와 부호만 읽는다. 표본 B는 대형주 위주
모집단이고 세션당 통과 후보가 적어 선택 여지 세션이 제한된다. 둘 다
"선택 여지(후보>=2) 세션" 부분집합을 병기한다. 일한도 시뮬은 top-1 고정이며
같은 날 2건 진입(08-21 SEI→AVAV 실측)은 재현하지 않는다. 창 미완결 세션은
출구 발동 건만 정산에 들어가므로 최근 세션에 상향 편향이 있다(모든 규칙에
동일 적용이라 규칙 간 비교는 견딘다).

== 판정 (2026-09-01 실측) ==

**① 보고서 §4 핵심 수치 재현 — 모델 제거 근거는 유효.**
  rank1(signals 정본): n=9 평균 −7.04% 승률 11% (보고서 −7.16%/8% ≈ 재현)
  현행 라이브 파이프 재현 픽: 3건 −8.54%, **무작위 널 백분위 0.0** —
  모델 픽은 같은 통과분 무작위 픽 분포의 최하단이다. 제거 방향 확정.
  단 prob 게이트 분리(보고서 −4.50 vs −1.53)는 정산 교집합에서 −3.39 vs
  −3.18로 재현 안 됨 — "게이트가 해롭다"까지는 못 가고 "무가치"가 정확하다.

**② 픽 규칙 5종 — 사전등록 기준 전부 미달. 검증된 픽 순서는 없다.**
                 표본A(우리 풀, 널백분위)   표본B(교재, 널백분위)
  dvol_desc         +2.35%  97.7            +2.32%  20.6   ← 표본 간 모순
  dvol_asc          -1.95%  52.3            +3.33%  93.5   ← 표본 간 모순
  ibs_hi            -2.17%  48.6            +2.33%  21.3
  chg_hi            -2.75%  38.4            +3.23%  88.1   ← 표본 간 모순
  max_lo            +2.24%  97.3            +2.49%  31.4   ← 표본 간 모순
  기준①(두 표본 널 95% 초과) 통과 규칙 0개. 거래대금 축은 두 표본에서
  부호가 정반대다 — 실거래 승자 프로필(저거래대금)과 우리 풀 픽 시뮬
  (고거래대금 우위)까지 합치면 3개 표본에서 방향이 갈린다 = 노이즈.
  교재 선택여지 세션의 dvol_asc +6.07%(t=2.80)·chg_hi +5.68%(t=2.31)는
  표본 A와 정반대라 사전등록이 없었다면 "발견"으로 오인했을 값이다.

**③ 부산물 — 후보 폭이 넓은 날이 나쁜 날이다.**
  표본 A 통과분 전량 평균 −5.30% vs 세션가중 무작위 널 −2.02%.
  차이는 통과 후보가 많은 세션(08-19: 100건)이 더 나빴다는 뜻 —
  "후보 수=신호"(08-07 F2) 가설과 같은 방향. 관측 축으로만 등록.

**결론**: 모델 제거는 진행(근거 재현됨). 픽 순서는 통계적으로 구별 불가 —
잠정 순서는 운영자 선택 사항이고, 어떤 순서가 가든 랭킹 shadow 원장으로
전 규칙의 픽·정산을 병행 기록해 우리 코호트 forward 30건으로 재판정한다.
시도 수 N +4.
"""
from __future__ import annotations

import random
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from research_early_exit_no_bump import bars, cluster_t  # noqa: E402

POOL_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
YAHOO_DB = ROOT / "data" / "analysis" / "us_yahoo_point_in_time.db"
PROXY_CHG = -5.0
CSV_START = "2025-01-27"
TP, SL, BE, HOLD, FEE = 12.0, -25.0, 4.0, 7, 0.48
BAND_LO, BAND_HI = 100.0, 500.0
MAX_FLOOR = 8.0
HURDLE_PROB, HURDLE_NET = 0.55, 0.25
N_PERM = 2000

RULES = ("dvol_desc", "dvol_asc", "ibs_hi", "chg_hi", "max_lo")


def _key(rule: str, c: dict) -> float:
    if rule == "dvol_desc":
        return -(c["dvol"] or 0.0)
    if rule == "dvol_asc":
        return c["dvol"] or 1e18
    if rule == "ibs_hi":
        return -(c["ibs"] if c["ibs"] is not None else -1.0)
    if rule == "chg_hi":
        return -(c["chg"] if c["chg"] is not None else -1e9)
    if rule == "max_lo":
        return c["max21"] if c["max21"] is not None else 1e18
    raise ValueError(rule)


def contract_net_d7(entry: float, win: list[tuple]) -> tuple[float, str] | None:
    """TP=high(D0 종가만)/SL·BE락=종가/D7. 창 미완결이고 미발동이면 None(미정산)."""
    if not win or entry <= 0:
        return None
    peak = (win[0][4] - entry) / entry * 100.0
    for i, (_d, _o, hi, _lo, c, _v) in enumerate(win):
        hip = (hi - entry) / entry * 100.0 if i > 0 else (c - entry) / entry * 100.0
        cp = (c - entry) / entry * 100.0
        if hip >= TP:
            return TP - FEE, "TP"
        if cp <= SL:
            return cp - FEE, "SL"
        if peak >= BE and cp <= 0:
            return cp - FEE, "BE"
        peak = max(peak, hip)
    if len(win) < HOLD + 1:
        return None
    return (win[-1][4] - entry) / entry * 100.0 - FEE, "D_MAT"


def max21_at(b: list[tuple], i: int) -> float | None:
    """신호일 i까지 21거래일 최대 일간 상승률(%) — 브리지 MAX 하한과 같은 창."""
    if i < 21:
        return None
    return max(100.0 * (b[j][4] / b[j - 1][4] - 1.0) for j in range(i - 20, i + 1))


def featurize(ticker: str, entry_idx: int, b: list[tuple]) -> dict | None:
    """entry_idx = 진입 세션 봉 인덱스. 신호일 = entry_idx-1."""
    si = entry_idx - 1
    if si < 1:
        return None
    _d, o, hi, lo, c, v = b[si]
    win = b[entry_idx: entry_idx + HOLD + 1]
    if not win:
        return None
    entry = win[0][1]
    if not entry or entry <= 0:
        return None
    res = contract_net_d7(entry, win)
    return {
        "ticker": ticker,
        "ibs": (c - lo) / (hi - lo) * 100.0 if hi > lo else None,
        "chg": 100.0 * (c / b[si - 1][4] - 1.0) if b[si - 1][4] else None,
        "dvol": c * v / 1e6 if v else None,
        "max21": max21_at(b, si),
        "net": res[0] if res else None,
        "exit": res[1] if res else "OPEN",
    }


def load_pool_sessions() -> dict[str, list[dict]]:
    """모델 필드는 signals 테이블(라이브 정본)에서 붙인다.

    candidate_pool_all의 scored/rank에는 wide_net **실험용 별도 모델**의 채점이
    섞여 있어(1차 실행에서 확인) 라이브 모델 판정 재현에 쓰면 오염된다.
    """
    con = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True, timeout=10)
    try:
        rows = con.execute(
            """SELECT p.session_date, p.ticker, p.chg_pct, p.dollar_vol,
                      s.rank, s.probability, s.predicted_net_pct
               FROM candidate_pool_all p
               LEFT JOIN signals s
                 ON s.signal_date = p.session_date AND UPPER(s.ticker) = UPPER(p.ticker)
               WHERE p.eligible=1 ORDER BY p.session_date"""
        ).fetchall()
    finally:
        con.close()
    sessions: dict[str, list[dict]] = defaultdict(list)
    for sd, tk, chg, dvol, rank, prob, pnet in rows:
        t = str(tk).upper()
        b = bars(t)
        ei = next((i for i, x in enumerate(b) if x[0] == str(sd)), None)
        if ei is None:
            continue
        f = featurize(t, ei, b)
        if f is None:
            continue
        # 풀의 신호일 값 우선(진입 결정 시점 가용값), CSV는 보조
        if chg is not None:
            f["chg"] = float(chg)
        if dvol is not None:
            f["dvol"] = float(dvol) / 1e6
        f.update({"scored": 1 if rank else 0, "rank": int(rank or 0),
                  "prob": prob, "pnet": pnet, "session": str(sd)})
        sessions[str(sd)].append(f)
    return sessions


def load_textbook_sessions() -> dict[str, list[dict]]:
    from tools.us_daily_alpha_walkforward import load_yahoo_dataset
    con = sqlite3.connect(f"file:{YAHOO_DB}?mode=ro", uri=True, timeout=20)
    try:
        df = load_yahoo_dataset(con, horizon=5)
    finally:
        con.close()
    dl = df[(df["change_pct"] <= PROXY_CHG) & (df["session_date"] >= CSV_START)]
    sessions: dict[str, list[dict]] = defaultdict(list)
    for rec in dl.itertuples():
        t = str(rec.ticker).upper()
        b = bars(t)
        si = next((i for i, x in enumerate(b) if x[0] == str(rec.session_date)), None)
        if si is None or si + 1 >= len(b):
            continue
        f = featurize(t, si + 1, b)
        if f is None:
            continue
        f["session"] = str(rec.session_date)
        sessions[str(rec.session_date)].append(f)
    return sessions


def passers(cands: list[dict]) -> list[dict]:
    """라이브 계약 선별(밴드 → MAX, fail-open 규약 동일)."""
    out = []
    for c in cands:
        if c["dvol"] is None or not (BAND_LO <= c["dvol"] < BAND_HI):
            continue
        if c["max21"] is not None and c["max21"] < MAX_FLOOR:
            continue  # MAX 미상은 fail-open (브리지와 동일)
        out.append(c)
    return out


def incumbent_pick(cands: list[dict]) -> dict | None:
    """현행 라이브 파이프 재현: scored top10 → 밴드 → MAX → 허들 → 모델 rank 1위."""
    scored = sorted((c for c in cands if c["scored"] and c["rank"]), key=lambda c: c["rank"])[:10]
    pool = passers(scored)
    pool = [c for c in pool
            if (c["prob"] or 0) >= HURDLE_PROB and (c["pnet"] or 0) >= HURDLE_NET]
    return pool[0] if pool else None


def describe(label: str, picks: list[dict], null_means: list[float] | None = None) -> None:
    settled = [p for p in picks if p["net"] is not None]
    if not settled:
        print(f"  {label:12s} 정산 0건")
        return
    nets = [p["net"] for p in settled]
    tp = 100.0 * sum(1 for p in settled if p["exit"] == "TP") / len(settled)
    losses = [n for n in nets if n <= 0]
    mean = st.mean(nets)
    line = (f"  {label:12s} 픽 {len(settled):3d}건 평균 {mean:+6.2f}% 합 {sum(nets):+8.1f}%p "
            f"승률 {100*sum(1 for n in nets if n>0)/len(nets):3.0f}% P(TP) {tp:3.0f}% "
            f"실패평균 {st.mean(losses) if losses else 0:+6.2f}% "
            f"t={cluster_t([(p['ticker'], p['net']) for p in settled]):+5.2f}")
    if null_means:
        pct = 100.0 * sum(1 for m in null_means if m < mean) / len(null_means)
        line += f" | 널백분위 {pct:5.1f}"
    print(line)


def run_sample(name: str, sessions: dict[str, list[dict]], with_incumbent: bool) -> None:
    print(f"\n=== {name} ===")
    per_sess = {sd: passers(c) for sd, c in sessions.items()}
    per_sess = {sd: p for sd, p in per_sess.items() if p}
    n_all = sum(len(c) for c in sessions.values())
    n_pass = sum(len(p) for p in per_sess.values())
    choice = {sd: p for sd, p in per_sess.items() if len(p) >= 2}
    print(f"세션 {len(sessions)}개 / 적격 {n_all}건 / 밴드+MAX 통과 {n_pass}건 "
          f"(세션당 {n_pass/max(1,len(per_sess)):.1f}) / 선택여지(>=2) 세션 {len(choice)}개")

    # 통과분 전량(풀 평균) — 참고 전용, 판정에 쓰지 않는다
    flat = [c for p in per_sess.values() for c in p]
    describe("[참고]전량", flat)

    # 무작위 널
    rng = random.Random(20260901)
    null_means: list[float] = []
    for _ in range(N_PERM):
        picked = [rng.choice(p) for p in per_sess.values()]
        nets = [p["net"] for p in picked if p["net"] is not None]
        if nets:
            null_means.append(st.mean(nets))
    null_means.sort()
    if null_means:
        lo, hi = null_means[int(0.05*len(null_means))], null_means[int(0.95*len(null_means))]
        print(f"  무작위널     평균 {st.mean(null_means):+6.2f}% [5~95%: {lo:+.2f} ~ {hi:+.2f}]")

    if with_incumbent:
        inc = [incumbent_pick(c) for c in sessions.values()]
        describe("현행(모델)", [p for p in inc if p], null_means)

    for rule in RULES:
        picks = [sorted(p, key=lambda c: _key(rule, c))[0] for p in per_sess.values()]
        describe(rule, picks, null_means)

    print("  [선택여지 세션만]")
    for rule in RULES:
        picks = [sorted(p, key=lambda c: _key(rule, c))[0] for p in choice.values()]
        describe(rule, picks)

    # 월별 부호 (규칙별 픽 평균)
    print("  [월별 픽 평균 — 부호 일치 확인]")
    months = sorted({sd[:7] for sd in per_sess})
    for rule in RULES:
        cells = []
        for m in months:
            nets = [sorted(p, key=lambda c: _key(rule, c))[0]["net"]
                    for sd, p in per_sess.items() if sd.startswith(m)]
            nets = [n for n in nets if n is not None]
            cells.append(f"{m[2:]}:{st.mean(nets):+5.1f}" if nets else f"{m[2:]}:  -  ")
        print(f"    {rule:10s} " + " ".join(cells))


def verify_doc_numbers(sessions: dict[str, list[dict]]) -> None:
    """보고서 §4 재현 — rank1 / prob 게이트 / 현행 vs 제거."""
    print("\n=== 보고서 §4 수치 재현 (우리 풀) ===")
    scored = [c for cs in sessions.values() for c in cs if c["scored"] and c["net"] is not None]
    r1 = [c for c in scored if c["rank"] == 1]
    rest = [c for cs in sessions.values() for c in cs
            if not (c["scored"] and c["rank"] == 1) and c["net"] is not None]
    if r1:
        print(f"  rank1        n={len(r1):3d} 평균 {st.mean([c['net'] for c in r1]):+6.2f}% "
              f"승률 {100*sum(1 for c in r1 if c['net']>0)/len(r1):3.0f}%  [보고서 −7.16% / 8%]")
    hi = [c for c in scored if (c["prob"] or 0) >= HURDLE_PROB]
    lo = [c for c in scored if c["prob"] is not None and c["prob"] < HURDLE_PROB]
    if hi and lo:
        print(f"  prob>=0.55   n={len(hi):3d} 평균 {st.mean([c['net'] for c in hi]):+6.2f}% | "
              f"prob<0.55 n={len(lo):3d} 평균 {st.mean([c['net'] for c in lo]):+6.2f}%  "
              f"[보고서 −4.50 / −1.53]")
    print(f"  (대조군 전체 n={len(rest)} 평균 {st.mean([c['net'] for c in rest]):+6.2f}%)")


def main() -> int:
    pool = load_pool_sessions()
    if not pool:
        print("[ERROR] 풀 표본 0건")
        return 1
    verify_doc_numbers(pool)
    run_sample("표본 A — 우리 풀 (eligible=1, session_date=진입일 규약)", pool, with_incumbent=True)
    tb = load_textbook_sessions()
    run_sample("표본 B — 봉인 교재 day_losers 프록시 (D7 통일)", tb, with_incumbent=False)
    print("\n판정(사전등록): ①두 표본 모두 널 95% 초과 ②부호 일치 ③P(TP)·실패깊이 비악화")
    print("전부 미달이면 '픽 순서는 구별 불가'가 결론 — 순서는 운영자 선택 + shadow 원장 forward.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
