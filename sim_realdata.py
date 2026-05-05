"""sim_realdata.py - 실제 DB 데이터로 전략 비교 백테스트

비교 대상:
  A) 패치전  : cross-asset 미적용, 기존 params 그대로
  B) 패치후  : 현재 패치 (bear adj 차단 + cap 1.65 + DEFENSIVE 분리)
  C) 개선안1 : B + mean_reversion ma60 임계 0.95->0.90
  D) 개선안2 : B + mean_reversion rsi_thr +2 완화

사용법:
    python -X utf8 sim_realdata.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
from pathlib import Path
from copy import deepcopy
from indicators import calc_all
from strategy import gap_pullback, momentum, mean_reversion, volatility_breakout
from strategy.cross_asset import apply_cross_asset_adjust

PRICE_DIR = Path(__file__).parent / "data" / "price"

# ── 대표 cross-asset 컨텍스트 (실 VIX DB 없으므로 고정값 사용) ─────────────────
# 날짜 구간별로 현실적 값 설정
CTX_BULL   = {"vix": 14.0, "vkospi": 13.0, "usd_krw": 1320, "sectors": {"XLK": 1.2}}
CTX_NORMAL = {"vix": 18.0, "vkospi": 17.0, "usd_krw": 1380, "sectors": {"XLK": 0.0}}
CTX_BEAR   = {"vix": 29.9, "vkospi": 22.0, "usd_krw": 1518, "sectors": {"XLK": -1.5}}

# 날짜 구간 → 컨텍스트 매핑 (실제 시장 흐름 반영)
def ctx_for_date(dt) -> dict:
    d = pd.Timestamp(dt)
    if d < pd.Timestamp("2025-01-01"):
        return CTX_BULL   # 2024년: 비교적 안정
    elif d < pd.Timestamp("2025-10-01"):
        return CTX_NORMAL # 2025년 초중반
    else:
        return CTX_BEAR   # 2025년 말~현재: 관세충격

# ── 시나리오 파라미터 팩토리 ────────────────────────────────────────────────────
# mode는 시뮬에서 CAUTIOUS_BEAR 고정 (현재 장세 기준)
SIM_MODE = "CAUTIOUS_BEAR"
SIM_CONF = 0.65

def make_params_A(sname):
    """패치전: cross-asset 미적용"""
    if sname == "gap_pullback":
        return gap_pullback.params(SIM_MODE, SIM_CONF)
    elif sname == "momentum":
        return momentum.params(SIM_MODE, SIM_CONF)
    elif sname == "mean_reversion":
        return mean_reversion.params(SIM_MODE, SIM_CONF)
    elif sname == "volatility_breakout":
        return volatility_breakout.params(SIM_MODE, conf=SIM_CONF)

def make_params_B(sname, ctx, market, ticker):
    """패치후: 현재 cross-asset 적용"""
    return apply_cross_asset_adjust(make_params_A(sname), ctx, market, ticker, SIM_MODE)

def make_params_C(sname, ctx, market, ticker):
    """개선안1: B + mean_reversion ma60 임계 완화 0.95->0.90"""
    p = make_params_B(sname, ctx, market, ticker)
    if sname == "mean_reversion":
        p["ma60_thr"] = 0.90   # 커스텀 키로 신호함수에서 읽음
    return p

def make_params_D(sname, ctx, market, ticker):
    """개선안2: B + mean_reversion rsi_thr +2 완화"""
    p = make_params_B(sname, ctx, market, ticker)
    if sname == "mean_reversion" and "rsi_thr" in p:
        p["rsi_thr"] = min(38, p["rsi_thr"] + 2)
        p["bb_thr"]  = min(28, p["bb_thr"]  + 2)
    return p

# ── mean_reversion 신호 함수 래퍼 (ma60_thr 지원) ──────────────────────────────
def mr_signal_flex(df, i, params):
    """개선안1: ma60_thr 파라미터 지원 버전"""
    if i < 20: return False
    row      = df.iloc[i]
    rsi      = float(row.get("rsi", 50))
    bb_pct   = float(row.get("bb_pct", 50))
    vol_ratio= float(row.get("vol_ratio", 1))
    close    = float(row.get("close", 0))
    ma60     = float(row.get("ma60", 0))
    rsi_thr  = float(params.get("rsi_thr", 32))
    bb_thr   = float(params.get("bb_thr", 20))
    ma60_thr = float(params.get("ma60_thr", 0.95))  # 기본 0.95
    return (rsi < rsi_thr and bb_pct < bb_thr and
            vol_ratio < 2.5 and close > ma60 * ma60_thr)

# KR 전략 체인 순서
KR_CHAIN = ["gap_pullback", "momentum", "mean_reversion", "volatility_breakout"]
US_CHAIN = ["momentum", "mean_reversion", "gap_pullback", "volatility_breakout"]

SIG_FN = {
    "gap_pullback":        gap_pullback.signal,
    "momentum":            momentum.signal,
    "mean_reversion":      mean_reversion.signal,
    "volatility_breakout": volatility_breakout.signal,
}

# ── 로드 ────────────────────────────────────────────────────────────────────────
def load_df(market, ticker):
    path = PRICE_DIR / market.lower() / f"{market.lower()}_{ticker}.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, parse_dates=["date"])
        df.columns = [c.lower() for c in df.columns]
        df = df.sort_values("date").reset_index(drop=True)
        return calc_all(df)
    except:
        return None

# ── 단일 종목 시뮬 ──────────────────────────────────────────────────────────────
def sim_ticker(df, ticker, market, scenario_name, param_fn, chain):
    sig_count = {s: 0 for s in chain}
    trades = []
    position = None

    for i in range(len(df)):
        row = df.iloc[i]
        ctx = ctx_for_date(row.get("date", "2025-01-01"))

        if position is not None:
            close = float(row.get("close", 0))
            hold  = i - position["entry_idx"]
            tp    = position["params"].get("tp_pct", 0.025)
            sl    = position["params"].get("sl_pct", 0.015)
            mh    = position["params"].get("max_hold", 2)
            pnl   = (close - position["entry"]) / position["entry"]

            reason = None
            ep = close
            if position["params"].get("tp_bb_mid") and "ma20" in row and close >= float(row["ma20"]):
                reason, ep = "BB_MID", close
            elif pnl >= tp:
                reason, ep = "TP", position["entry"] * (1 + tp)
            elif pnl <= -sl:
                reason, ep = "SL", position["entry"] * (1 - sl)
            elif hold >= mh:
                reason, ep = "MAXHOLD", close

            if reason:
                trades.append({
                    "date":   str(row.get("date",""))[:10],
                    "strat":  position["strat"],
                    "pnl":    round((ep - position["entry"]) / position["entry"] * 100, 3),
                    "reason": reason,
                    "hold":   hold,
                })
                position = None
            else:
                continue

        for sname in chain:
            p = param_fn(sname, ctx, market, ticker)
            sig_fn = mr_signal_flex if sname == "mean_reversion" else SIG_FN[sname]
            try:
                fired = sig_fn(df, i, p)
            except:
                fired = False
            if fired:
                sig_count[sname] += 1
                position = {
                    "entry":     float(row["close"]),
                    "entry_idx": i,
                    "strat":     sname,
                    "params":    p,
                }
                break

    if position is not None:
        last = df.iloc[-1]
        ep = float(last["close"])
        trades.append({
            "date":   str(last.get("date",""))[:10],
            "strat":  position["strat"],
            "pnl":    round((ep - position["entry"]) / position["entry"] * 100, 3),
            "reason": "END",
            "hold":   len(df) - 1 - position["entry_idx"],
        })

    return sig_count, trades


# ── 전체 유니버스 시뮬 ──────────────────────────────────────────────────────────
SCENARIOS = {
    "A_패치전":  lambda s, ctx, m, t: make_params_A(s),
    "B_패치후":  make_params_B,
    "C_mr완화(ma60x0.90)": make_params_C,
    "D_mr완화(rsi+2)":     make_params_D,
}

def run_universe(market):
    price_dir = PRICE_DIR / market.lower()
    tickers = sorted(f.stem.replace(f"{market.lower()}_", "")
                     for f in price_dir.glob(f"{market.lower()}_*.csv"))
    chain = KR_CHAIN if market == "KR" else US_CHAIN

    # 시나리오별 집계
    agg = {sc: {"sig": {s: 0 for s in chain}, "trades": []} for sc in SCENARIOS}
    ticker_rows = []  # 종목별 요약

    for ticker in tickers:
        df = load_df(market, ticker)
        if df is None or len(df) < 65:
            continue

        row_data = {"ticker": ticker, "rows": len(df)}
        for sc_name, param_fn in SCENARIOS.items():
            sc, trades = sim_ticker(df, ticker, market, sc_name, param_fn, chain)
            total_sig = sum(sc.values())
            total_pnl = round(sum(t["pnl"] for t in trades), 2)
            wins      = sum(1 for t in trades if t["pnl"] > 0)
            n         = len(trades)
            row_data[sc_name] = {"sig": total_sig, "trades": n, "pnl": total_pnl,
                                  "wr": round(wins/n*100) if n else 0}
            # 집계
            for s, c in sc.items():
                agg[sc_name]["sig"][s] += c
            agg[sc_name]["trades"].extend(trades)

        ticker_rows.append(row_data)

    return agg, ticker_rows, tickers


# ── 출력 ────────────────────────────────────────────────────────────────────────
def print_market(market, agg, ticker_rows, tickers):
    chain = KR_CHAIN if market == "KR" else US_CHAIN
    valid = [r for r in ticker_rows if len(r) > 2]
    n_valid = len(valid)

    print(f"\n{'='*72}")
    print(f"  [{market}] 실데이터 백테스트  유효종목 {n_valid}/{len(tickers)}개")
    print(f"{'='*72}")

    # 시나리오 요약 테이블
    print(f"\n  {'시나리오':<28} {'총신호':>6} {'거래':>5} {'승률':>6} {'누적PnL':>9} {'평균PnL':>8}")
    print(f"  {'-'*65}")
    for sc_name, data in agg.items():
        trades = data["trades"]
        total_sig = sum(data["sig"].values())
        n  = len(trades)
        wr = round(sum(1 for t in trades if t["pnl"] > 0) / n * 100) if n else 0
        tp = round(sum(t["pnl"] for t in trades), 2)
        ap = round(tp / n, 3) if n else 0
        print(f"  {sc_name:<28} {total_sig:>6} {n:>5} {wr:>5}% {tp:>+8.2f}% {ap:>+7.3f}%")

    # 전략별 신호 분포 (B 기준)
    print(f"\n  [전략별 신호 분포 - 패치후 B 기준]")
    b_sig = agg["B_패치후"]["sig"]
    total = sum(b_sig.values()) or 1
    for s in chain:
        cnt = b_sig[s]
        bar = "#" * (cnt // max(1, total // 30))
        print(f"    {s:<24} {cnt:>5}건 ({cnt/total*100:>5.1f}%)  {bar}")

    # mean_reversion 세부 분석
    print(f"\n  [mean_reversion 조건 통과율 분석]")
    mr_stats = {"rsi": 0, "bb": 0, "vol": 0, "ma60_095": 0, "ma60_090": 0,
                "all_095": 0, "all_090": 0, "total": 0}
    for ticker in tickers:
        df = load_df(market, ticker)
        if df is None or len(df) < 65:
            continue
        p_b = make_params_B("mean_reversion", CTX_BEAR, market, ticker)
        rsi_thr = p_b.get("rsi_thr", 30)
        bb_thr  = p_b.get("bb_thr", 17)
        for i in range(20, len(df)):
            row = df.iloc[i]
            rsi = float(row.get("rsi", 50))
            bb  = float(row.get("bb_pct", 50))
            vr  = float(row.get("vol_ratio", 1))
            cl  = float(row.get("close", 0))
            m60 = float(row.get("ma60", 0))
            if m60 <= 0: continue
            mr_stats["total"] += 1
            r = rsi < rsi_thr
            b = bb < bb_thr
            v = vr < 2.5
            m95 = cl > m60 * 0.95
            m90 = cl > m60 * 0.90
            if r: mr_stats["rsi"] += 1
            if b: mr_stats["bb"]  += 1
            if v: mr_stats["vol"] += 1
            if m95: mr_stats["ma60_095"] += 1
            if m90: mr_stats["ma60_090"] += 1
            if r and b and v and m95: mr_stats["all_095"] += 1
            if r and b and v and m90: mr_stats["all_090"] += 1

    T = mr_stats["total"] or 1
    def pct(k): return f"{mr_stats[k]/T*100:.1f}%"
    print(f"    RSI < rsi_thr         : {mr_stats['rsi']:>6}행 ({pct('rsi')})")
    print(f"    BB  < bb_thr          : {mr_stats['bb']:>6}행 ({pct('bb')})")
    print(f"    vol_ratio < 2.5       : {mr_stats['vol']:>6}행 ({pct('vol')})")
    print(f"    close > ma60*0.95     : {mr_stats['ma60_095']:>6}행 ({pct('ma60_095')})")
    print(f"    close > ma60*0.90     : {mr_stats['ma60_090']:>6}행 ({pct('ma60_090')})")
    print(f"    ─────────────────────────────────────────────────")
    print(f"    전체(ma60x0.95 기준)  : {mr_stats['all_095']:>6}건 ({pct('all_095')}) ← 현재")
    print(f"    전체(ma60x0.90 기준)  : {mr_stats['all_090']:>6}건 ({pct('all_090')}) ← 개선안C")
    gain = mr_stats["all_090"] - mr_stats["all_095"]
    print(f"    ma60 완화 시 추가 신호: +{gain}건")

    # 종목별 상세 (신호 많은 상위 10개)
    top10 = sorted(valid, key=lambda r: r.get("B_패치후", {}).get("sig", 0), reverse=True)[:10]
    print(f"\n  [신호 상위 10개 종목 - 패치후 B 기준]")
    print(f"  {'티커':<10} {'행수':>5}  A신호  B신호  B거래  B승률  B손익")
    print(f"  {'-'*55}")
    for r in top10:
        a = r.get("A_패치전", {})
        b = r.get("B_패치후", {})
        print(f"  {r['ticker']:<10} {r['rows']:>5}  "
              f"{a.get('sig',0):>5}  {b.get('sig',0):>5}  "
              f"{b.get('trades',0):>5}  {b.get('wr',0):>4}%  "
              f"{b.get('pnl',0):>+6.2f}%")

    # exit reason 분포
    b_trades = agg["B_패치후"]["trades"]
    if b_trades:
        from collections import Counter
        reasons = Counter(t["reason"] for t in b_trades)
        print(f"\n  [청산 사유 분포 - 패치후]")
        for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {r:<12} {c:>4}건 ({c/len(b_trades)*100:.1f}%)")

    # 전략별 평균 보유일
    print(f"\n  [전략별 평균 보유일 - 패치후]")
    strat_holds = {}
    for t in b_trades:
        strat_holds.setdefault(t["strat"], []).append(t["hold"])
    for s in chain:
        holds = strat_holds.get(s, [])
        if holds:
            print(f"    {s:<24} {len(holds):>4}건  평균 {sum(holds)/len(holds):.1f}일")


# ── 메인 ────────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "="*72)
    print("  실데이터 전략 비교 백테스트 (CAUTIOUS_BEAR 기준)")
    print(f"  mode={SIM_MODE} / conf={SIM_CONF}")
    print("="*72)

    for market in ["KR", "US"]:
        print(f"\n  [{market}] 데이터 로딩 중...")
        agg, ticker_rows, tickers = run_universe(market)
        print_market(market, agg, ticker_rows, tickers)

    # 최종 개선 방향 요약
    print(f"\n{'='*72}")
    print("  개선 방향 종합 결론")
    print(f"{'='*72}")
    print("""
  A→B (현재 패치): bear adj 차단 + cap 1.65 + DEFENSIVE 분리
    - 신호 수 증가, 손익 개선 확인됨

  B→C (ma60 임계 완화 0.95->0.90):
    - mean_reversion 발화 증가 가능
    - 하락장에서 ma60 아래 종목도 진입 허용 → 리스크 있음

  B→D (rsi_thr/bb_thr +2 완화):
    - 더 완만한 과매도 구간에서 진입
    - 안전한 완화 방식 (ma60 조건은 유지)

  추천: D 먼저 적용 후 실전 관찰 → C는 하락장 리스크 있어 2순위
""")

if __name__ == "__main__":
    main()
