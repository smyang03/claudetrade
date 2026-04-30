"""
test_kr_trade.py — KR 매수/매도 흐름 테스트

1. 실제 KR 종목 신호 점검 (현재 왜 NONE인지 이유 표시)
2. 합성 데이터로 신호 강제 발생 → can_open(market='KR') → place_order dry-run
3. RiskManager can_open 마켓 분리 검증

실행:
  python test_kr_trade.py
  python test_kr_trade.py --place-order   # 실제 주문 (장외 → 오류 예상)
"""
import sys, os, argparse
from pathlib import Path
from dotenv import load_dotenv

if "pytest" in sys.modules:
    import pytest

    pytest.skip("script-style KIS trade smoke; run with python test_kr_trade.py", allow_module_level=True)

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import numpy as np

from kis_api import get_access_token, get_daily_ohlcv, get_price, place_order
from indicators import calc_all
from strategy.momentum import signal as mom_sig, params as mom_params
from strategy.gap_pullback import signal as gap_sig, params as gap_params
from strategy.mean_reversion import signal as mr_sig, params as mr_params
from risk_manager import RiskManager, HARD_RULES

# ── 테스트 대상 종목 ──────────────────────────────────────────
TICKERS = ["105560", "055550", "068270"]   # 오늘 KR 선택 종목
MODE    = "MODERATE_BULL"

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "


# ─────────────────────────────────────────────────────────────
# 1. 실제 데이터 신호 점검
# ─────────────────────────────────────────────────────────────
def test_real_signals(token, tickers, mode):
    print(f"\n{'='*60}")
    print(f"[1] 실제 KR 신호 점검  (mode={mode})")
    print(f"{'='*60}")

    for ticker in tickers:
        try:
            candles = get_daily_ohlcv(ticker, token, lookback_days=90, market="KR")
            if candles.empty:
                print(f"  {ticker}: {FAIL} 캔들 없음")
                continue
            sig_df = calc_all(candles)
            if sig_df.empty:
                print(f"  {ticker}: {FAIL} 지표 계산 실패")
                continue
            i   = len(sig_df) - 1
            row = sig_df.iloc[i]

            close    = float(row.get("close", 0))
            ma5      = float(row.get("ma5", 0))
            ma20     = float(row.get("ma20", 0))
            ma60     = float(row.get("ma60", 0))
            macd     = float(row.get("macd", 0))
            macd_s   = float(row.get("macd_signal", 0))
            vol      = float(row.get("volume", 0))
            vol_avg  = float(row.get("vol_avg20", 1))
            high20   = float(row.get("high20", 0))

            g = gap_sig(sig_df, i, gap_params(mode))
            m = mom_sig(sig_df, i, mom_params(mode))
            r = mr_sig(sig_df, i, mr_params(mode))
            sig_name = "gap_pullback" if g else ("momentum" if m else ("mean_reversion" if r else "NONE"))

            icon = PASS if sig_name != "NONE" else WARN
            print(f"\n  {icon} {ticker}  →  신호: {sig_name}")
            print(f"     close={close:,.0f}  high20={high20:,.0f}  "
                  f"{'close>high20 ✓' if close > high20 else 'close<high20 ✗'}")
            print(f"     MA: ma5={ma5:,.0f} ma20={ma20:,.0f} ma60={ma60:,.0f}  "
                  f"{'ma5>ma20>ma60 ✓' if ma5 > ma20 > ma60 else 'MA정렬 ✗'}")
            print(f"     MACD: {macd:.1f} / signal: {macd_s:.1f}  "
                  f"{'MACD>sig ✓' if macd > macd_s else 'MACD<sig ✗'}")
            print(f"     vol_ratio: {vol/vol_avg:.2f}x  "
                  f"{'≥2.0 ✓' if vol/vol_avg >= 2.0 else '<2.0 ✗ (모멘텀 기준 미달)'}")

        except Exception as e:
            print(f"  {ticker}: {FAIL} 오류 — {e}")


# ─────────────────────────────────────────────────────────────
# 2. 합성 데이터 신호 강제 발생 테스트
# ─────────────────────────────────────────────────────────────
def _make_synthetic_df(n=80, base_price=150000):
    """모멘텀 신호가 발생하도록 설계된 합성 OHLCV.

    조건:
      - 60일 이상 꾸준히 우상향 → ma5 > ma20 > ma60
      - MACD > signal (골든크로스 직후)
      - 마지막 봉: 거래량 폭발(vol_avg*3), close > high20
    """
    dates = pd.date_range("2025-01-01", periods=n)
    np.random.seed(7)

    # 안정적 우상향 시계열 (변동성 작게)
    drift = np.linspace(0, base_price * 0.25, n)
    noise = np.cumsum(np.random.randn(n) * 200)
    prices = base_price + drift + noise
    prices = np.clip(prices, base_price * 0.8, base_price * 2.0)

    volume_base = 300_000
    volumes = np.random.randint(volume_base, volume_base * 2, n).astype(float)
    # 마지막 봉: 거래량 3배 폭발
    volumes[-1] = volume_base * 3.5

    df = pd.DataFrame({
        "open":   prices * 0.998,
        "high":   prices * 1.006,
        "low":    prices * 0.992,
        "close":  prices,
        "volume": volumes,
    }, index=dates)

    sig_df = calc_all(df)

    # 마지막 봉 close를 high20 이상으로 강제 설정
    i = len(sig_df) - 1
    h20 = float(sig_df.iloc[i].get("high20", sig_df.iloc[i]["close"]))
    if h20 > 0 and sig_df.iloc[i]["close"] <= h20:
        sig_df.iloc[i, sig_df.columns.get_loc("close")] = h20 * 1.02

    return sig_df


def test_synthetic_signal(mode):
    print(f"\n{'='*60}")
    print(f"[2] 합성 데이터 신호 테스트  (mode={mode})")
    print(f"{'='*60}")

    sig_df = _make_synthetic_df()
    i = len(sig_df) - 1
    row = sig_df.iloc[i]

    g = gap_sig(sig_df, i, gap_params(mode))
    m = mom_sig(sig_df, i, mom_params(mode))
    r = mr_sig(sig_df, i, mr_params(mode))
    sig_name = "gap_pullback" if g else ("momentum" if m else ("mean_reversion" if r else "NONE"))

    icon = PASS if sig_name != "NONE" else FAIL
    print(f"  {icon} 합성 종목  →  신호: {sig_name}")
    print(f"     close={float(row['close']):,.0f}  "
          f"vol_ratio={float(row.get('vol_ratio', row['volume']/row.get('vol_avg20',1))):.2f}x  "
          f"MACD={float(row.get('macd',0)):.1f}")
    return sig_name != "NONE"


# ─────────────────────────────────────────────────────────────
# 3. can_open 마켓 분리 검증
# ─────────────────────────────────────────────────────────────
def test_can_open_market_separation():
    print(f"\n{'='*60}")
    print(f"[3] can_open 마켓 분리 검증")
    print(f"{'='*60}")

    rm = RiskManager(init_cash=50_000_000, max_order_krw=2_000_000)

    # US 포지션 MAX_POSITIONS개 채우기 (순수 알파벳 티커 → US로 분류됨)
    max_pos = HARD_RULES["max_positions"]
    us_tickers = [chr(65+i)*4 for i in range(max_pos)]   # AAAA, BBBB, ...
    for tk in us_tickers:
        rm.open_position(
            ticker=tk,
            price=1_500_000,
            qty=1,
            strategy="test",
            tp_pct=0.025,
            sl_pct=0.015,
        )
    print(f"  US 포지션 {max_pos}개 주입: {us_tickers[:3]}... (MAX_POSITIONS={max_pos})")

    # market 파라미터 없이 (구버전)
    ok_old, reason_old = rm.can_open("005930", 70000, market="")
    print(f"  {FAIL if ok_old else PASS} market='' → ok={ok_old}  reason='{reason_old}'")
    assert not ok_old, "구버전 동작 확인 실패"

    # market='KR' 지정 (신버전)
    ok_new, reason_new = rm.can_open("005930", 70000, market="KR")
    print(f"  {PASS if ok_new else FAIL} market='KR' → ok={ok_new}  reason='{reason_new}'")
    assert ok_new, f"KR 마켓 분리 실패: {reason_new}"

    # market='US' 지정 → 여전히 차단
    ok_us, reason_us = rm.can_open("AAPL", 1_500_000, market="US")
    print(f"  {PASS if not ok_us else FAIL} market='US' → ok={ok_us}  reason='{reason_us}'")
    assert not ok_us, "US 한도 초과 감지 실패"

    print(f"\n  {PASS} can_open 마켓 분리 정상 동작 확인")


# ─────────────────────────────────────────────────────────────
# 4. 실제 주문 dry-run (--place-order 플래그 시만)
# ─────────────────────────────────────────────────────────────
def test_place_order(token, place=False):
    print(f"\n{'='*60}")
    print(f"[4] KR 주문 테스트  (place={place})")
    print(f"{'='*60}")

    ticker = "105560"
    try:
        info = get_price(ticker, token, market="KR")
        price = info["price"]
        print(f"  {ticker} 현재가: {price:,}원")
    except Exception as e:
        print(f"  {FAIL} 현재가 조회 실패: {e}")
        return

    qty = 1
    print(f"  주문 예정: {ticker} 매수 {qty}주 @ {price:,}원")

    if not place:
        print(f"  {WARN} --place-order 없이 실행 → 실제 주문 생략 (dry-run)")
        print(f"  place_order 호출 파라미터:")
        print(f"    place_order('{ticker}', {qty}, {price}, 'buy', token, market='KR')")
        return

    print(f"  실제 주문 시도 (장 외 시간이면 KIS 오류 예상)...")
    try:
        result = place_order(ticker, qty, price, "buy", token, market="KR")
        print(f"  {PASS} 매수 결과: {result}")
    except Exception as e:
        print(f"  {WARN} 주문 오류 (장외 예상): {e}")

    # 매도 테스트
    print(f"\n  매도 dry-run: {ticker} 1주")
    if not place:
        print(f"  place_order 호출 파라미터:")
        print(f"    place_order('{ticker}', 1, {price}, 'sell', token, market='KR')")
        return
    try:
        result = place_order(ticker, 1, price, "sell", token, market="KR")
        print(f"  {PASS} 매도 결과: {result}")
    except Exception as e:
        print(f"  {WARN} 매도 오류 (보유없음/장외 예상): {e}")


# ─────────────────────────────────────────────────────────────
def main(place_order_flag=False):
    print("=== KR 매수/매도 흐름 테스트 시작 ===")
    token = get_access_token()
    print(f"KIS 토큰: {'OK' if token else 'FAIL'}")

    test_real_signals(token, TICKERS, MODE)
    sig_ok = test_synthetic_signal(MODE)
    test_can_open_market_separation()
    test_place_order(token, place=place_order_flag)

    print(f"\n{'='*60}")
    print("테스트 완료")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--place-order", action="store_true",
                        help="실제 KIS 주문 API 호출 (장외 시간 오류 예상)")
    args = parser.parse_args()
    main(place_order_flag=args.place_order)
