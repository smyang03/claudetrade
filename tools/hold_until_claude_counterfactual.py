from __future__ import annotations
"""반사실: 6/23~25 진입을 손절 없이 '지금까지' 들었으면? (read-only, yfinance 현재가)

운영자 질문: 손실 나도 들고 Claude가 매도하라 할때까지 들었으면 지금 계좌는?
- 종목당 최초 진입 1개씩(손절 안 했으면 재진입도 없음)
- hold-to-now = 현재가 vs 진입가. 실제 실현과 비교.
주의: '지금'은 단일 스냅샷(노이즈). '회복했나'의 1차 근사일 뿐, 미래 보장 아님.
"""
import sys, time
from pathlib import Path

# (ticker, qty, entry_price) — 종목당 최초 진입 (logs/system PathB LIVE BUY)
POS = [
    ("NOK", 23, 14.1), ("CRDO", 1, 302.0), ("NVDA", 1, 210.41), ("AAOI", 1, 174.85),
    ("CIEN", 1, 459.64), ("BB", 36, 8.99), ("AMZN", 1, 235.0), ("IBM", 1, 262.5),
    ("INTC", 2, 135.93), ("AAL", 20, 16.2), ("GOOGL", 1, 346.77), ("MRVL", 1, 281.53),
    ("AXON", 1, 431.5), ("ASTS", 4, 76.0), ("TENB", 11, 27.72), ("NTSK", 34, 9.55),
    ("MSFT", 1, 374.93), ("SOFI", 18, 17.89), ("JHX", 12, 25.81), ("IREN", 6, 52.44),
    ("CCL", 10, 29.6), ("PFE", 13, 24.19), ("AAPL", 1, 297.89), ("VKTX", 8, 37.1),
]


def cur_price(sym):
    import yfinance as yf
    try:
        df = yf.download(sym, period="2d", interval="5m", progress=False, auto_adjust=False)
        if df is None or df.empty:
            return None
        if df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        return float(df["Close"].iloc[-1])
    except Exception:
        return None


def main():
    print(f"{'TICKER':7}{'qty':>4}{'entry':>9}{'now':>9}{'pnl%':>8}{'pnl$':>9}")
    inv = held = 0.0
    rows = []
    for tk, q, e in POS:
        c = cur_price(tk)
        time.sleep(0.25)
        if c is None:
            print(f"{tk:7}{q:>4}{e:>9.2f}   (no data)")
            continue
        pnl_pct = (c / e - 1) * 100
        pnl_usd = (c - e) * q
        inv += e * q
        held += c * q
        rows.append((tk, pnl_pct, pnl_usd))
        print(f"{tk:7}{q:>4}{e:>9.2f}{c:>9.2f}{pnl_pct:>8.2f}{pnl_usd:>9.2f}")
    print("-" * 46)
    tot_pct = (held / inv - 1) * 100 if inv else 0
    print(f"투자원금 ${inv:,.0f}  현재가치 ${held:,.0f}  손익 ${held-inv:+,.0f} ({tot_pct:+.2f}%)")
    wins = [r for r in rows if r[1] > 0]
    print(f"종목 {len(rows)}개 중 +회복 {len(wins)} / -지속 {len(rows)-len(wins)}  "
          f"(승률 {len(wins)/len(rows)*100:.0f}%)")
    fx = 1530.0
    print(f"  (≈ {(held-inv)*fx:+,.0f} KRW, FX 1530 가정)")
    print("\n주: '지금' 단일 스냅샷. 손절 회피가 회복으로 이어졌는지의 1차 근사일 뿐 미래 보장 아님.")
    print("    Claude target 청산분(AXON/AAL 등 +)도 여기선 '계속 보유'로 처리됨 — 익절분 포기 포함.")


if __name__ == "__main__":
    sys.exit(main())
