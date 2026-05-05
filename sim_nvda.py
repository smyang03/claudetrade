"""sim_nvda.py - NVDA 가짜 데이터로 매수/매도 시뮬레이션 검증

사용법:
    python sim_nvda.py

출력:
    - 시나리오별 (장세 모드 × 시장 상황) 신호 발생 현황
    - 포지션 진입/청산 P&L 요약
    - cross_asset 보정 전후 비교
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
from indicators import calc_all
from strategy import gap_pullback, momentum, mean_reversion, volatility_breakout
from strategy.cross_asset import apply_cross_asset_adjust

np.random.seed(42)

# ────────────────────────────────────────────────────────────────
# 1. 가짜 NVDA 데이터 생성 (150 거래일, ~6개월)
# ────────────────────────────────────────────────────────────────

def make_nvda_fake(n: int = 150) -> pd.DataFrame:
    """
    현실적인 NVDA 주가 흐름 시뮬레이션:
      0~29일:   상승 추세 ($100 → $130)
      30~59일:  급락 + 회복 ($130 → $95 → $110)
      60~89일:  횡보 ($105~$115)
      90~119일: 하락 추세 ($115 → $85) - 현재 장세 반영
      120~149일: 낮은 변동성 바닥권 ($83~$92)
    """
    closes = []
    phase_params = [
        # (days, start, end, vol_base, vol_spike_prob)
        (30, 100, 130, 1.5, 0.10),   # 상승
        (15,  130, 95, 2.5, 0.20),   # 급락
        (15,   95, 110, 2.0, 0.15),  # 반등
        (30,  110, 108, 1.0, 0.05),  # 횡보
        (30,  108,  85, 2.0, 0.12),  # 하락
        (30,   85,  90, 1.2, 0.08),  # 바닥권
    ]

    price = 100.0
    rows = []

    for days, start, end, vol_base, spike_prob in phase_params:
        drift = (end - start) / days
        for d in range(days):
            noise = np.random.normal(0, vol_base)
            price = price + drift + noise
            price = max(price, 10)

            daily_vol = vol_base * abs(np.random.normal(1, 0.3))
            high  = price + abs(np.random.normal(0, daily_vol * 0.6))
            low   = price - abs(np.random.normal(0, daily_vol * 0.6))
            open_ = price + np.random.normal(0, daily_vol * 0.3)
            open_ = max(low, min(high, open_))

            # 거래량: 기본 5~15M, 스파이크 확률
            base_vol = np.random.uniform(5_000_000, 15_000_000)
            if np.random.random() < spike_prob:
                base_vol *= np.random.uniform(2.0, 4.0)  # 스파이크

            rows.append({
                "open":   round(open_, 2),
                "high":   round(high, 2),
                "low":    round(low, 2),
                "close":  round(price, 2),
                "volume": int(base_vol),
            })

    df = pd.DataFrame(rows)
    df.index = pd.date_range("2025-07-01", periods=len(df), freq="B")
    return df


# ────────────────────────────────────────────────────────────────
# 2. 포지션 시뮬레이터
# ────────────────────────────────────────────────────────────────

class Position:
    def __init__(self, entry_idx, entry_price, strategy, params, label):
        self.entry_idx   = entry_idx
        self.entry_price = entry_price
        self.strategy    = strategy
        self.params      = params
        self.label       = label
        self.exit_idx    = None
        self.exit_price  = None
        self.exit_reason = None

    def check_exit(self, df, i) -> bool:
        row = df.iloc[i]
        close = float(row["close"])
        hold  = i - self.entry_idx
        tp    = self.params.get("tp_pct", 0.03)
        sl    = self.params.get("sl_pct", 0.015)
        max_h = self.params.get("max_hold", 5)

        pnl_pct = (close - self.entry_price) / self.entry_price

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
        self.exit_idx   = i
        self.exit_price = price
        self.exit_reason = reason

    @property
    def pnl_pct(self):
        if self.exit_price is None:
            return None
        return round((self.exit_price - self.entry_price) / self.entry_price * 100, 2)


# ────────────────────────────────────────────────────────────────
# 3. 시나리오별 시뮬레이션
# ────────────────────────────────────────────────────────────────

SCENARIOS = [
    {
        "name": "호황장  (MILD_BULL / VIX=14)",
        "mode": "MILD_BULL",
        "context": {"vix": 14.0, "usd_krw": 1340, "vkospi": 13.0,
                    "sectors": {"XLK": 1.5}},
        "market": "US",
    },
    {
        "name": "보통장  (NEUTRAL / VIX=18)",
        "mode": "NEUTRAL",
        "context": {"vix": 18.0, "usd_krw": 1380, "vkospi": 17.0,
                    "sectors": {"XLK": 0.2}},
        "market": "US",
    },
    {
        "name": "현재장세(CAUTIOUS_BEAR / VIX=29.9) - 패치전",
        "mode": "CAUTIOUS_BEAR",
        "context": {"vix": 29.9, "usd_krw": 1518, "vkospi": 22.0,
                    "sectors": {"XLK": -1.5}},
        "market": "US",
        "no_cross_asset": True,   # cross_asset 미적용 비교용
    },
    {
        "name": "현재장세(CAUTIOUS_BEAR / VIX=29.9) - 패치후",
        "mode": "CAUTIOUS_BEAR",
        "context": {"vix": 29.9, "usd_krw": 1518, "vkospi": 22.0,
                    "sectors": {"XLK": -1.5}},
        "market": "US",
    },
    {
        "name": "위기장  (DEFENSIVE / VIX=38)",
        "mode": "DEFENSIVE",
        "context": {"vix": 38.0, "usd_krw": 1560, "vkospi": 33.0,
                    "sectors": {"XLK": -2.5}},
        "market": "US",
    },
]

STRATEGIES = [
    ("momentum",            momentum.signal,            momentum.params),
    ("volatility_breakout", volatility_breakout.signal, volatility_breakout.params),
    ("mean_reversion",      mean_reversion.signal,      mean_reversion.params),
    ("gap_pullback",        gap_pullback.signal,         gap_pullback.params),
]


def run_scenario(df_calc: pd.DataFrame, scenario: dict) -> dict:
    mode    = scenario["mode"]
    ctx     = scenario["context"]
    market  = scenario["market"]
    no_ca   = scenario.get("no_cross_asset", False)
    ticker  = "NVDA"

    trades    = []
    position  = None
    sig_count = {s: 0 for s, _, _ in STRATEGIES}
    vol_mults = {}

    # params 계산 (vol_mult 확인용)
    for sname, _, pfn in STRATEGIES:
        raw = pfn(mode, conf=0.65) if sname == "volatility_breakout" else pfn(mode, 0.65)
        if no_ca:
            p = raw
        else:
            p = apply_cross_asset_adjust(raw, ctx, market, ticker, mode)
        vol_mults[sname] = p.get("vol_mult", "-")

    for i in range(len(df_calc)):
        row = df_calc.iloc[i]

        # 오픈 포지션 청산 체크
        if position is not None:
            if position.check_exit(df_calc, i):
                trades.append(position)
                position = None
            else:
                continue  # 보유 중 → 신호 스킵

        # 신호 탐색 (우선순위 순)
        for sname, sfn, pfn in STRATEGIES:
            raw_p = pfn(mode, conf=0.65) if sname == "volatility_breakout" else pfn(mode, 0.65)
            if no_ca:
                p = raw_p
            else:
                p = apply_cross_asset_adjust(raw_p, ctx, market, ticker, mode)

            if sfn(df_calc, i, p):
                sig_count[sname] += 1
                entry_price = float(row["close"])
                position = Position(i, entry_price, sname, p,
                                    label=df_calc.index[i].strftime("%m/%d"))
                break

    # 남은 포지션 강제 청산
    if position is not None:
        last = df_calc.iloc[-1]
        position._close(len(df_calc)-1, float(last["close"]), "END")
        trades.append(position)

    return {
        "sig_count": sig_count,
        "trades":    trades,
        "vol_mults": vol_mults,
    }


# ────────────────────────────────────────────────────────────────
# 4. 결과 출력
# ────────────────────────────────────────────────────────────────

def print_result(scenario: dict, result: dict):
    sep = "-" * 64
    print(f"\n{'='*64}")
    print(f"  {scenario['name']}")
    print(f"{'='*64}")

    # vol_mult 현황
    print("  [vol_mult 적용값]")
    for sname, vm in result["vol_mults"].items():
        print(f"    {sname:<22} vol_mult={vm}")

    # 신호 발생
    total_sig = sum(result["sig_count"].values())
    print(f"\n  [신호 발생] 총 {total_sig}건")
    for sname, cnt in result["sig_count"].items():
        bar = "#" * cnt
        print(f"    {sname:<22} {cnt:>3}건  {bar}")

    # 거래 결과
    trades = result["trades"]
    print(f"\n  [거래 결과] {len(trades)}건")
    if not trades:
        print("    ※ 거래 없음")
        return

    wins   = [t for t in trades if t.pnl_pct and t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct and t.pnl_pct <= 0]
    total_pnl = sum(t.pnl_pct for t in trades if t.pnl_pct)

    print(f"  {'날짜':>6}  {'전략':<22} {'진입':>7} {'청산':>7}  {'사유':<8} {'손익':>7}")
    print(f"  {sep}")
    for t in trades:
        pnl_str = f"{t.pnl_pct:+.2f}%" if t.pnl_pct is not None else "  -"
        exit_p  = f"{t.exit_price:.2f}" if t.exit_price else "-"
        print(f"  {t.label:>6}  {t.strategy:<22} ${t.entry_price:>6.2f} ${exit_p:>6}  "
              f"{t.exit_reason:<8} {pnl_str:>7}")

    print(f"  {sep}")
    wr = len(wins)/len(trades)*100 if trades else 0
    print(f"  승률: {wr:.0f}%  ({len(wins)}승 {len(losses)}패)  누적 손익: {total_pnl:+.2f}%")


# ────────────────────────────────────────────────────────────────
# 5. 메인
# ────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*64)
    print("  NVDA 가짜 데이터 전략 시뮬레이션")
    print("="*64)

    raw_df   = make_nvda_fake(150)
    df_calc  = calc_all(raw_df)

    print(f"\n데이터: {len(raw_df)}행 생성 → calc_all 후 {len(df_calc)}행 유효")
    print(f"가격 범위: ${df_calc['close'].min():.2f} ~ ${df_calc['close'].max():.2f}")
    print(f"기간: {df_calc.index[0].date()} ~ {df_calc.index[-1].date()}")

    # 지표 스냅샷 (최근 5행)
    print("\n[최근 5행 지표 스냅샷]")
    snap_cols = ["close", "rsi", "bb_pct", "vol_ratio", "ma_align", "macd_cross"]
    snap = df_calc[snap_cols].tail(5).copy()
    snap["close"] = snap["close"].map("${:.2f}".format)
    snap["rsi"]   = snap["rsi"].map("{:.1f}".format)
    snap["bb_pct"]= snap["bb_pct"].map("{:.1f}%".format)
    snap["vol_ratio"]= snap["vol_ratio"].map("{:.2f}x".format)
    print(snap.to_string())

    # 시나리오 실행
    all_results = []
    for sc in SCENARIOS:
        res = run_scenario(df_calc, sc)
        all_results.append((sc, res))
        print_result(sc, res)

    # 요약 비교표
    print(f"\n\n{'='*64}")
    print("  시나리오 요약 비교")
    print(f"{'='*64}")
    print(f"  {'시나리오':<38} {'신호':>4}  {'거래':>4}  {'승률':>6}  {'누적손익':>8}")
    print(f"  {'─'*62}")
    for sc, res in all_results:
        trades = res["trades"]
        total_sig = sum(res["sig_count"].values())
        wins = [t for t in trades if t.pnl_pct and t.pnl_pct > 0]
        wr = f"{len(wins)/len(trades)*100:.0f}%" if trades else "  -"
        pnl = f"{sum(t.pnl_pct for t in trades if t.pnl_pct):+.2f}%" if trades else "  -"
        print(f"  {sc['name']:<38} {total_sig:>4}  {len(trades):>4}  {wr:>6}  {pnl:>8}")

    print(f"\n  ※ 패치 전후 비교: CAUTIOUS_BEAR 시나리오 3 vs 4 차이 확인")
    print()


if __name__ == "__main__":
    main()
