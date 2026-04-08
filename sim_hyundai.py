"""sim_hyundai.py - 현대자동차(005380) 가짜 데이터로 국장 매수/매도 시뮬레이션

사용법:
    python -X utf8 sim_hyundai.py

출력:
    - 시나리오별 (장세 모드 x 시장 상황) 신호 발생 및 거래 결과
    - KR 전략 체인: gap_pullback -> momentum -> mean_reversion -> volatility_breakout
    - cross_asset KR 보정: VKOSPI + USD/KRW
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
from indicators import calc_all
from strategy import gap_pullback, momentum, mean_reversion, volatility_breakout
from strategy.cross_asset import apply_cross_asset_adjust, get_vix_regime

np.random.seed(7)

# ────────────────────────────────────────────────────────────────
# 1. 가짜 현대차 데이터 생성 (150 거래일)
# 현대차 가격대: 190,000 ~ 260,000원
# 국장 특징: 갭 발생 빈번, 거래량 변동 큼
# ────────────────────────────────────────────────────────────────

def make_hyundai_fake(n: int = 150) -> pd.DataFrame:
    """
    현대차 주가 흐름 (6개월) - 현실적 변동폭 유지
      0~24일:   상승 추세 (220,000 -> 248,000)   +12.7%
      25~44일:  급락 (248,000 -> 210,000)         -15.3%  관세충격
      45~64일:  반등 (210,000 -> 228,000)         +8.6%
      65~99일:  완만한 하락 (228,000 -> 205,000)  -10.1%
      100~149일: 바닥 횡보 (200,000 ~ 212,000)

    특징:
      - daily_vol 을 가격의 1~1.5% 수준으로 제한 (현대차 일 변동성 현실 반영)
      - 갭 상승 날 거래량 연동 스파이크
      - 상승 추세 구간에서 모멘텀 vol_ratio 조건 충족 가능하게 설계
    """
    phase_params = [
        # (days, start,   end,    vol_pct, gap_up_prob, gap_dn_prob, spike_prob)
        (25, 220000, 248000,  0.012, 0.15, 0.03, 0.12),  # 상승 (갭상승+거래량)
        (20, 248000, 210000,  0.018, 0.05, 0.25, 0.25),  # 급락 (갭하락+폭증)
        (20, 210000, 228000,  0.013, 0.18, 0.04, 0.15),  # 반등 (갭상승 다수)
        (35, 228000, 205000,  0.011, 0.05, 0.12, 0.08),  # 완만 하락
        (50, 205000, 210000,  0.009, 0.08, 0.06, 0.06),  # 횡보
    ]

    rows = []
    price = 220000.0
    prev_close = price

    for days, start, end, vol_pct, gap_up_prob, gap_dn_prob, spike_prob in phase_params:
        drift = (end - start) / days

        for d in range(days):
            daily_vol = prev_close * vol_pct

            # 갭 발생 (상승/하락 방향 분리)
            gap_size = 0.0
            gap_occurred = False
            rnd = np.random.random()
            if rnd < gap_up_prob:
                gap_size = np.random.uniform(0.008, 0.022) * prev_close
                gap_occurred = True
            elif rnd < gap_up_prob + gap_dn_prob:
                gap_size = -np.random.uniform(0.008, 0.022) * prev_close
                gap_occurred = True

            open_ = prev_close + gap_size
            # 당일 이동 = drift + noise
            day_move = drift + np.random.normal(0, daily_vol * 0.5)
            close = open_ + day_move
            close = max(close, 100000)  # 10만원 하한

            hi_add = abs(np.random.normal(0, daily_vol * 0.4))
            lo_sub = abs(np.random.normal(0, daily_vol * 0.4))
            high = max(open_, close) + hi_add
            low  = min(open_, close) - lo_sub

            # 거래량
            base_vol = np.random.uniform(3_500_000, 7_000_000)
            if gap_occurred:
                base_vol *= np.random.uniform(1.8, 3.5)   # 갭 연동 거래량
            elif np.random.random() < spike_prob:
                base_vol *= np.random.uniform(1.5, 2.5)   # 랜덤 폭증

            # 100원 단위 반올림
            def r100(v): return round(v / 100) * 100
            rows.append({
                "open":   float(r100(open_)),
                "high":   float(r100(high)),
                "low":    float(r100(low)),
                "close":  float(r100(close)),
                "volume": int(base_vol),
            })
            prev_close = r100(close)

    df = pd.DataFrame(rows[:n])
    df.index = pd.date_range("2025-07-01", periods=len(df), freq="B")
    return df


# ────────────────────────────────────────────────────────────────
# 2. 포지션 시뮬레이터 (KR용 - BB 중심선 TP 포함)
# ────────────────────────────────────────────────────────────────

class Position:
    def __init__(self, entry_idx, entry_price, strategy, params, label, df):
        self.entry_idx   = entry_idx
        self.entry_price = entry_price
        self.strategy    = strategy
        self.params      = params
        self.label       = label
        self.exit_idx    = None
        self.exit_price  = None
        self.exit_reason = None
        self._df         = df

    def check_exit(self, df, i) -> bool:
        row   = df.iloc[i]
        close = float(row["close"])
        hold  = i - self.entry_idx
        sl    = self.params.get("sl_pct", 0.02)
        max_h = self.params.get("max_hold", 5)

        pnl_pct = (close - self.entry_price) / self.entry_price

        # mean_reversion: BB 중심선 도달 시 TP
        if self.params.get("tp_bb_mid") and "ma20" in row:
            if close >= float(row["ma20"]):
                self._close(i, close, "BB_MID_TP")
                return True
        else:
            tp = self.params.get("tp_pct", 0.025)
            if pnl_pct >= tp:
                self._close(i, close, "TP")
                return True

        if pnl_pct <= -sl:
            self._close(i, close, "SL")
            return True
        if hold >= max_h:
            self._close(i, close, "MAXHOLD")
            return True
        return False

    def _close(self, i, price, reason):
        self.exit_idx    = i
        self.exit_price  = price
        self.exit_reason = reason

    @property
    def pnl_pct(self):
        if self.exit_price is None:
            return None
        return round((self.exit_price - self.entry_price) / self.entry_price * 100, 2)

    @property
    def pnl_krw(self):
        if self.exit_price is None:
            return None
        qty = int(1_000_000 / self.entry_price)  # 100만원 기준 주수
        return int((self.exit_price - self.entry_price) * qty)


# ────────────────────────────────────────────────────────────────
# 3. KR 시나리오 정의
# ────────────────────────────────────────────────────────────────

SCENARIOS = [
    {
        "name": "호황장  (MILD_BULL / VKOSPI=13, USD/KRW=1320)",
        "mode": "MILD_BULL",
        "context": {"vix": 14.0, "vkospi": 13.0, "usd_krw": 1320, "sectors": {}},
    },
    {
        "name": "보통장  (NEUTRAL / VKOSPI=17, USD/KRW=1380)",
        "mode": "NEUTRAL",
        "context": {"vix": 18.0, "vkospi": 17.0, "usd_krw": 1380, "sectors": {}},
    },
    {
        "name": "현재장세(CAUTIOUS_BEAR / VKOSPI=22, USD/KRW=1518) - 패치前",
        "mode": "CAUTIOUS_BEAR",
        "context": {"vix": 29.9, "vkospi": 22.0, "usd_krw": 1518, "sectors": {}},
        "no_cross_asset": True,
    },
    {
        "name": "현재장세(CAUTIOUS_BEAR / VKOSPI=22, USD/KRW=1518) - 패치後",
        "mode": "CAUTIOUS_BEAR",
        "context": {"vix": 29.9, "vkospi": 22.0, "usd_krw": 1518, "sectors": {}},
    },
    {
        "name": "환율위기(CAUTIOUS_BEAR / VKOSPI=30, USD/KRW=1560)",
        "mode": "CAUTIOUS_BEAR",
        "context": {"vix": 38.0, "vkospi": 30.0, "usd_krw": 1560, "sectors": {}},
    },
]

# KR 전략 체인 (우선순위 순)
KR_STRATEGY_CHAIN = [
    ("gap_pullback",        gap_pullback.signal,        gap_pullback.params),
    ("momentum",            momentum.signal,            momentum.params),
    ("mean_reversion",      mean_reversion.signal,      mean_reversion.params),
    ("volatility_breakout", volatility_breakout.signal, volatility_breakout.params),
]


# ────────────────────────────────────────────────────────────────
# 4. 시뮬레이션
# ────────────────────────────────────────────────────────────────

def run_scenario(df_calc: pd.DataFrame, scenario: dict) -> dict:
    mode   = scenario["mode"]
    ctx    = scenario["context"]
    no_ca  = scenario.get("no_cross_asset", False)
    market = "KR"
    ticker = "005380"

    trades    = []
    position  = None
    sig_count = {s: 0 for s, _, _ in KR_STRATEGY_CHAIN}
    vol_mults = {}

    # params 미리 계산 (vol_mult 확인용)
    for sname, _, pfn in KR_STRATEGY_CHAIN:
        raw = pfn(mode, conf=0.65) if sname == "volatility_breakout" else pfn(mode, 0.65)
        p   = raw if no_ca else apply_cross_asset_adjust(raw, ctx, market, ticker, mode)
        if "vol_mult" in p:
            vol_mults[sname] = p["vol_mult"]
        elif "rsi_thr" in p:
            vol_mults[sname] = f"rsi<{p['rsi_thr']}/bb<{p['bb_thr']}"
        else:
            vol_mults[sname] = "-"

    for i in range(len(df_calc)):
        row = df_calc.iloc[i]

        # 오픈 포지션 청산 체크
        if position is not None:
            if position.check_exit(df_calc, i):
                trades.append(position)
                position = None
            else:
                continue

        # KR 신호 체인 탐색
        for sname, sfn, pfn in KR_STRATEGY_CHAIN:
            raw_p = pfn(mode, conf=0.65) if sname == "volatility_breakout" else pfn(mode, 0.65)
            p     = raw_p if no_ca else apply_cross_asset_adjust(raw_p, ctx, market, ticker, mode)

            if sfn(df_calc, i, p):
                sig_count[sname] += 1
                entry_price = float(row["close"])
                position = Position(
                    i, entry_price, sname, p,
                    label=df_calc.index[i].strftime("%m/%d"),
                    df=df_calc,
                )
                break

    # 미청산 포지션 강제 청산
    if position is not None:
        last = df_calc.iloc[-1]
        position._close(len(df_calc)-1, float(last["close"]), "END")
        trades.append(position)

    return {"sig_count": sig_count, "trades": trades, "vol_mults": vol_mults}


# ────────────────────────────────────────────────────────────────
# 5. 결과 출력
# ────────────────────────────────────────────────────────────────

def print_result(scenario: dict, result: dict):
    mode = scenario["mode"]
    ctx  = scenario["context"]
    vkospi = ctx.get("vkospi", 0)
    usdkrw = ctx.get("usd_krw", 0)

    print(f"\n{'='*68}")
    print(f"  {scenario['name']}")
    print(f"  VKOSPI={vkospi}  USD/KRW={usdkrw}  MODE={mode}")
    print(f"{'='*68}")

    # vol_mult / 임계값
    print("  [전략 파라미터]")
    for sname, vm in result["vol_mults"].items():
        print(f"    {sname:<24} {vm}")

    # 신호 발생
    total_sig = sum(result["sig_count"].values())
    print(f"\n  [신호 발생] 총 {total_sig}건")
    for sname, cnt in result["sig_count"].items():
        bar = "#" * cnt
        print(f"    {sname:<24} {cnt:>3}건  {bar}")

    # 거래 결과
    trades = result["trades"]
    print(f"\n  [거래 결과] {len(trades)}건")
    if not trades:
        print("    ※ 거래 없음")
        return

    wins   = [t for t in trades if t.pnl_pct and t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct and t.pnl_pct <= 0]
    total_pnl_pct = sum(t.pnl_pct for t in trades if t.pnl_pct)
    total_pnl_krw = sum(t.pnl_krw for t in trades if t.pnl_krw)

    print(f"  {'날짜':>5}  {'전략':<24} {'진입가':>9} {'청산가':>9}  {'사유':<10} {'손익%':>7}  {'손익(원)':>10}")
    print(f"  {'-'*80}")
    for t in trades:
        pnl_str = f"{t.pnl_pct:+.2f}%" if t.pnl_pct is not None else "  -"
        krw_str = f"{t.pnl_krw:+,}원" if t.pnl_krw is not None else "-"
        ep = f"{int(t.entry_price):,}"
        xp = f"{int(t.exit_price):,}" if t.exit_price else "-"
        print(f"  {t.label:>5}  {t.strategy:<24} {ep:>9} {xp:>9}  {t.exit_reason:<10} {pnl_str:>7}  {krw_str:>10}")

    print(f"  {'-'*80}")
    wr = len(wins) / len(trades) * 100 if trades else 0
    print(f"  승률: {wr:.0f}%  ({len(wins)}승 {len(losses)}패)   "
          f"누적 손익: {total_pnl_pct:+.2f}%  ({total_pnl_krw:+,}원 / 100만원 기준)")


# ────────────────────────────────────────────────────────────────
# 6. 신호별 상세 진단 (mean_reversion 왜 안 터지나 분석)
# ────────────────────────────────────────────────────────────────

def diagnose_mean_reversion(df_calc: pd.DataFrame, mode: str, ctx: dict):
    """mean_reversion 미발생 원인 분석: 각 조건별 통과/실패 비율"""
    market = "KR"
    ticker = "005380"
    raw_p  = mean_reversion.params(mode, 0.65)
    p      = apply_cross_asset_adjust(raw_p, ctx, market, ticker, mode)
    rsi_thr = p["rsi_thr"]
    bb_thr  = p["bb_thr"]

    rsi_pass = vol_pass = bb_pass = ma_pass = all_pass = 0
    total = len(df_calc)

    for i in range(20, total):
        row = df_calc.iloc[i]
        rsi      = float(row.get("rsi", 50))
        bb_pct   = float(row.get("bb_pct", 50))
        vol_ratio= float(row.get("vol_ratio", 1))
        close    = float(row.get("close", 0))
        ma60     = float(row.get("ma60", 0))

        r = rsi < rsi_thr
        b = bb_pct < bb_thr
        v = vol_ratio < 2.5
        m = close > ma60 * 0.95

        if r: rsi_pass += 1
        if b: bb_pass += 1
        if v: vol_pass += 1
        if m: ma_pass += 1
        if r and b and v and m: all_pass += 1

    denom = total - 20
    print(f"\n  [mean_reversion 조건 통과율 ({mode} / rsi<{rsi_thr} / bb<{bb_thr})]")
    print(f"    RSI < {rsi_thr}     : {rsi_pass}/{denom}행 ({rsi_pass/denom*100:.1f}%)")
    print(f"    BB_PCT < {bb_thr}   : {bb_pass}/{denom}행 ({bb_pass/denom*100:.1f}%)")
    print(f"    vol_ratio < 2.5 : {vol_pass}/{denom}행 ({vol_pass/denom*100:.1f}%)")
    print(f"    close > ma60*0.95: {ma_pass}/{denom}행 ({ma_pass/denom*100:.1f}%)")
    print(f"    >>> 전체 조건 동시: {all_pass}건")


# ────────────────────────────────────────────────────────────────
# 7. 메인
# ────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*68)
    print("  현대자동차(005380) 가짜 데이터 국장 전략 시뮬레이션")
    print("="*68)

    raw_df  = make_hyundai_fake(150)
    df_calc = calc_all(raw_df)

    print(f"\n데이터: {len(raw_df)}행 생성 -> calc_all 후 {len(df_calc)}행 유효")
    print(f"가격 범위: {int(df_calc['close'].min()):,}원 ~ {int(df_calc['close'].max()):,}원")
    print(f"기간: {df_calc.index[0].date()} ~ {df_calc.index[-1].date()}")

    # 지표 스냅샷
    print("\n[최근 5행 지표 스냅샷]")
    snap_cols = ["close", "rsi", "bb_pct", "vol_ratio", "ma_align", "gap_pct"]
    snap = df_calc[snap_cols].tail(5).copy()
    snap["close"]    = snap["close"].map("{:,.0f}원".format)
    snap["rsi"]      = snap["rsi"].map("{:.1f}".format)
    snap["bb_pct"]   = snap["bb_pct"].map("{:.1f}%".format)
    snap["vol_ratio"]= snap["vol_ratio"].map("{:.2f}x".format)
    snap["gap_pct"]  = snap["gap_pct"].map("{:+.2f}%".format)
    print(snap.to_string())

    # 갭 발생 현황
    gaps = df_calc[df_calc["gap_pct"].abs() > 1.0]
    print(f"\n갭 1% 이상 발생: {len(gaps)}일 / {len(df_calc)}일 ({len(gaps)/len(df_calc)*100:.1f}%)")
    print(f"  갭 상승 1%+: {len(df_calc[df_calc['gap_pct'] > 1.0])}일  "
          f"갭 하락 1%-: {len(df_calc[df_calc['gap_pct'] < -1.0])}일")

    # 시나리오 실행
    all_results = []
    for sc in SCENARIOS:
        res = run_scenario(df_calc, sc)
        all_results.append((sc, res))
        print_result(sc, res)

    # mean_reversion 미발생 진단 (CAUTIOUS_BEAR 기준)
    diagnose_mean_reversion(
        df_calc,
        mode="CAUTIOUS_BEAR",
        ctx={"vkospi": 22.0, "usd_krw": 1518, "vix": 29.9, "sectors": {}},
    )

    # 요약 비교표
    print(f"\n\n{'='*68}")
    print("  국장 시나리오 요약 비교")
    print(f"{'='*68}")
    print(f"  {'시나리오':<44} {'신호':>4}  {'거래':>4}  {'승률':>6}  {'누적손익':>8}")
    print(f"  {'-'*66}")
    for sc, res in all_results:
        trades    = res["trades"]
        total_sig = sum(res["sig_count"].values())
        wins      = [t for t in trades if t.pnl_pct and t.pnl_pct > 0]
        wr        = f"{len(wins)/len(trades)*100:.0f}%" if trades else "  -"
        pnl       = f"{sum(t.pnl_pct for t in trades if t.pnl_pct):+.2f}%" if trades else "  -"
        print(f"  {sc['name']:<44} {total_sig:>4}  {len(trades):>4}  {wr:>6}  {pnl:>8}")

    print(f"\n  ※ 패치 전후: 시나리오 3(패치前) vs 4(패치後) 비교")
    print()


if __name__ == "__main__":
    main()
