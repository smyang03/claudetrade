#!/usr/bin/env python3
"""밴드(100~500M)+MAX(8%) 선별의 2023~2025 OOS 검정 (2026-08-25 사전등록).

배경: 현행 US swing 선별 계약(거래대금 밴드 100~500M + MAX 하한 8%)은
2025-04~2026-08 데이터(220세션·27종목·138건, in-sample)에서 선택됐고 라이브
정산 실증은 아직 0건이다. 오늘 백필한 Alpaca 일봉(2023-01~2025-04,
data/price_backfill_alpaca, 규약 all=기존 CSV와 경계 100% 정합)으로 선택에
전혀 쓰이지 않은 과거 구간에서 같은 규칙을 검정한다 — "전략 자체에 문제가
없는가"에 지금 낼 수 있는 가장 강한 답.

== 사전등록 (결과 확인 전 고정) ==
- 후보 재구성(day_losers 프록시): 일 수익률 <= -5.0%(기존 실험 PROXY_CHG_LE
  동일), 종가 >= $5(수집기 하한 프록시), 계약 라벨은 기존 시뮬 재사용
  (t+1 시가 진입, TP12/SL25/D5, 동일봉 SL 우선, 비용 0.50).
- 밴드: 신호일 거래대금(종가x거래량) 100~500M$. MAX: 신호일 포함 직전 21종가
  -> 20수익의 최대값 >= 8%(현행 창 정의, 1f3c714).
- OOS 창: 2023-02-01 ~ 2025-03-31 신호일 (in-sample 시작 2025-04-14와 분리).
- 집계: 후보 전량 평균(계약 선택과 같은 "조건 전량" 관점) + 종목 클러스터 t
  (kr_rule_discrimination_backtest 규약) + 반기 버킷 부호.
- 판정 기준(고정):
  ① 밴드내-밴드밖 차이 부호가 in-sample과 같고(+) 합동 클러스터 t>=2
  ② 반기 버킷(2023H1/H2·2024H1/H2·2025Q1) 부호 일치 >= 4/5
  ③ 밴드+MAX가 밴드 단독보다 크거나 같은 방향
  -> 전부 만족 = "선별 강화 확인". 부호 유지+t 미달 = 약한 지지(현행 유지).
  부호 반전 = 경고(과최적화 신호, 운영자 보고 후 논의). 문턱 사후 조정 금지.
- 한계 명시: day_losers 프록시는 실제 수집기(야후 스크리너)와 다르다. 상폐
  포함(Alpaca 실증)이라 생존편향은 없으나, 무작위 불일치 7종목은 제외.

사용: python tools/band_max_oos_backtest_2023_2025.py
보정 체크: --price-dir data/price/us --start 2025-05-15 --end 2026-08-15
read-only. live DB·주문 접근 없음.

== 판정 (2026-08-25, 보정 체크 후 확정) ==
⚠️ **판정 불가 — 프록시 검정력 부족.** 같은 프록시를 in-sample 창(2025-05~
2026-08 라이브 CSV)에 돌린 보정 체크에서 밴드 효과(+3.67%/건, 실제 스크리너
풀 138건)가 재현되지 않았다(밴드내 +0.37 vs 밖 +0.56, top10 풀에서도 음).
일봉 재구성(-5% 전량/하락 상위10)은 야후 스크리너 모집단(수집 시점 장중
등락·정렬·시총 필터)을 담지 못한다 — 픽스처가 프로덕션과 다르면 검정은
무의미하다(08-21 교훈). 따라서 OOS 무변별을 "선별 재현 실패"로 읽지 않는다.
밴드+MAX 판정은 사전등록대로 라이브 forward 30건 몫.

유효한 부산물(같은 프록시끼리의 창 간 비교라 모집단 정합):
급락 기저 net이 2023-02~2025-03 -0.24%(t -3.67) vs 2025-05~2026-08
+0.50%(t 6.91) — "약세/변동 국면이 사냥철" 계열의 대규모 재확인(1,445종목).
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.us_swing_contract_label_experiment import _contract_labels  # noqa: E402

BF_DIR = ROOT / "data" / "price_backfill_alpaca" / "us"
EXCLUDED = {"AIEV", "CLM", "STLA", "USAS", "RACE", "CIB", "LTM"}  # 경계 무작위 불일치(manifest)
PROXY_CHG_LE = -5.0
MIN_CLOSE = 5.0
BAND_MIN_M, BAND_MAX_M = 100.0, 500.0
MAX_FLOOR_PCT = 8.0
COST = 0.50
SIG_START, SIG_END = "2023-02-01", "2025-03-31"


def _cluster_t(nets_by_ticker: dict[str, list[float]]) -> tuple[float | None, int, float]:
    means = [st.mean(v) for v in nets_by_ticker.values()]
    k = len(means)
    if k < 3:
        return None, k, (st.mean(means) if means else 0.0)
    sd = st.pstdev(means)
    return ((st.mean(means) / (sd / k ** 0.5)) if sd > 0 else None), k, st.mean(means)


def _bucket(day: str) -> str:
    y, m = day[:4], int(day[5:7])
    return f"{y}{'H1' if m <= 6 else 'H2'}"


def _stats(rows: list[dict]) -> str:
    if not rows:
        return "n=0"
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append(r["net"])
    t, k, mean_c = _cluster_t(by)
    wins = sum(1 for r in rows if r["net"] > 0)
    t_txt = f"{t:.2f}" if t is not None else "-"
    return f"n={len(rows):5d} k={k:4d} 평균(클러스터) {mean_c:+6.2f}% 승률 {100*wins/len(rows):3.0f}% t={t_txt}"


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="밴드+MAX OOS/보정 검정")
    parser.add_argument("--price-dir", default=str(BF_DIR),
                        help="가격 CSV 디렉터리 (보정 체크: data/price/us = in-sample 창)")
    parser.add_argument("--start", default=SIG_START)
    parser.add_argument("--end", default=SIG_END)
    args = parser.parse_args()
    price_dir = Path(args.price_dir)
    sig_start, sig_end = args.start, args.end

    rows: list[dict] = []
    files = sorted(price_dir.glob("us_*.csv"))
    print(f"가격 파일 {len(files)}개 스캔 (제외 {len(EXCLUDED)}종목) | 신호창 {sig_start}~{sig_end} | dir={price_dir.name}")
    for path in files:
        ticker = path.stem.replace("us_", "", 1)
        if ticker in EXCLUDED:
            continue
        # 라이브 CSV는 BOM 헤더(08-21 함정) — utf-8-sig로 양쪽 다 안전
        bars = pd.read_csv(path, encoding="utf-8-sig")
        if len(bars) < 30:
            continue
        bars["date"] = bars["date"].astype(str)
        closes = bars["close"].astype(float)
        vols = bars["volume"].astype(float)
        rets = closes.pct_change() * 100.0
        max20 = rets.rolling(20).max()  # 신호일 포함 21종가/20수익
        for i in range(21, len(bars)):
            day = bars.at[i, "date"]
            if not (sig_start <= day <= sig_end):
                continue
            chg = rets.iat[i]
            close = closes.iat[i]
            if not (chg == chg and chg <= PROXY_CHG_LE and close >= MIN_CLOSE):
                continue
            label = _contract_labels(bars, day, COST)
            if label is None:
                continue
            dollar_m = close * vols.iat[i] / 1e6
            rows.append({
                "ticker": ticker, "date": day, "bucket": _bucket(day), "chg": float(chg),
                "net": float(label["label_contract"]),
                "in_band": BAND_MIN_M <= dollar_m <= BAND_MAX_M,
                "max_pass": (max20.iat[i] == max20.iat[i]) and max20.iat[i] >= MAX_FLOOR_PCT,
            })
    print(f"급락 후보 재구성 {len(rows)}건 · 종목 {len({r['ticker'] for r in rows})} · 세션 {len({r['date'] for r in rows})}")

    # 보조 분석(사전등록 주 판정과 별개, 모집단 정합용): 실제 수집기는 "그날 하락
    # 상위 ~10종목" 스크리너 풀이다. -5% 전부(세션당 ~40건)와 모집단이 달라
    # 주 결과가 무변별이어도 풀 프록시에서 재확인한다(모집단 다른 두 숫자 함정 방지).
    by_session: dict[str, list[dict]] = {}
    for r in rows:
        by_session.setdefault(r["date"], []).append(r)
    top10: list[dict] = []
    for day_rows in by_session.values():
        top10.extend(sorted(day_rows, key=lambda x: x["chg"])[:10])

    band_in = [r for r in rows if r["in_band"]]
    band_out = [r for r in rows if not r["in_band"]]
    band_max = [r for r in band_in if r["max_pass"]]
    print("\n== 합동 (2023-02~2025-03 OOS) ==")
    print(f"  전체 급락      {_stats(rows)}")
    print(f"  밴드 내        {_stats(band_in)}")
    print(f"  밴드 밖        {_stats(band_out)}")
    print(f"  밴드+MAX>=8    {_stats(band_max)}")
    print(f"  밴드내 MAX미달 {_stats([r for r in band_in if not r['max_pass']])}")

    print("\n== 반기 버킷 (판정 ② 부호) ==")
    for bucket in sorted({r["bucket"] for r in rows}):
        b_in = [r for r in band_in if r["bucket"] == bucket]
        b_out = [r for r in band_out if r["bucket"] == bucket]
        m_in = st.mean([r["net"] for r in b_in]) if b_in else float("nan")
        m_out = st.mean([r["net"] for r in b_out]) if b_out else float("nan")
        diff = m_in - m_out if b_in and b_out else float("nan")
        print(f"  {bucket}: 밴드내 {m_in:+6.2f}%(n={len(b_in)}) vs 밖 {m_out:+6.2f}%(n={len(b_out)}) 차이 {diff:+6.2f}%p")

    t10_in = [r for r in top10 if r["in_band"]]
    t10_out = [r for r in top10 if not r["in_band"]]
    print("\n== 보조: 세션별 하락 상위 10 풀 프록시 (수집기 모집단 정합) ==")
    print(f"  풀 전체        {_stats(top10)}")
    print(f"  풀 내 밴드 내  {_stats(t10_in)}")
    print(f"  풀 내 밴드 밖  {_stats(t10_out)}")
    print(f"  풀 내 밴드+MAX {_stats([r for r in t10_in if r['max_pass']])}")

    print("\n판정은 사전등록 기준(①합동 부호+t>=2 ②반기 4/5 ③MAX 방향)으로 — 스크립트는 집계만 한다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
