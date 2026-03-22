"""
예산 계산 방식 시뮬레이션
- A: 현재 방식 (남은 현금 기준)
- B: init_cash 기준 고정 예산
- C: init_cash 기준 + 현금 부족 시 잔액 전부 사용 (제안 방식)
"""

INIT_CASH     = 10_000_000
MAX_ORDER_KRW = 500_000
MAX_POS_PCT   = 0.20
MODE_PCT      = 0.70   # CAUTIOUS

# 시나리오: 매수/매도 시퀀스 (종목, 진입가, 청산가, 사유)
SCENARIOS = [
    ("005930", 75_000, 80_000, "tp"),
    ("000660", 130_000, 118_000, "sl"),
    ("035720", 55_000, 58_000, "tp"),
    ("051910", 480_000, 460_000, "sl"),   # 비싼 주식
    ("035420", 68_000, 72_000, "tp"),
    ("005380", 220_000, 235_000, "tp"),
    ("006400", 85_000, 82_000, "sl"),
]


def calc_budget_A(cash, init_cash):
    """현재 방식: 남은 현금 기준"""
    b = cash * MAX_POS_PCT * MODE_PCT
    b = min(b, MAX_ORDER_KRW)
    b = min(b, cash * 0.5)
    return max(0.0, b)

def calc_budget_B(cash, init_cash):
    """init_cash 기준 고정"""
    b = init_cash * MAX_POS_PCT * MODE_PCT
    b = min(b, MAX_ORDER_KRW)
    b = min(b, cash)          # 현금 없으면 못 삼
    return max(0.0, b)

def calc_budget_C(cash, init_cash):
    """init_cash 기준 + 현금 부족 시 잔액 전부"""
    b = init_cash * MAX_POS_PCT * MODE_PCT
    b = min(b, MAX_ORDER_KRW)
    b = min(b, cash)          # 남은 현금이 상한
    return max(0.0, b)


def simulate(name, budget_fn):
    cash = float(INIT_CASH)
    total_pnl = 0.0
    total_fee = 0.0
    trades = 0
    skipped = 0

    print(f"\n{'='*55}")
    print(f"  방식 {name}")
    print(f"{'='*55}")
    print(f"  {'종목':<10} {'진입가':>8} {'수량':>5} {'예산':>9} {'현금잔액':>12} {'손익':>10}")
    print(f"  {'-'*55}")

    for ticker, entry, exit_p, reason in SCENARIOS:
        budget = budget_fn(cash, INIT_CASH)
        qty    = int(budget / entry)

        if qty == 0:
            skipped += 1
            print(f"  {ticker:<10} {entry:>8,} {'SKIP':>5} {int(budget):>9,} {int(cash):>12,}  ← 예산 부족 스킵")
            continue

        cost     = entry * qty
        buy_fee  = cost * 0.00015
        cash    -= (cost + buy_fee)
        total_fee += buy_fee

        revenue  = exit_p * qty
        sell_fee = revenue * 0.00195
        pnl      = (exit_p - entry) * qty - sell_fee
        cash    += revenue - sell_fee
        total_fee += sell_fee
        total_pnl += pnl
        trades += 1

        icon = "OK" if pnl > 0 else "NG"
        print(f"  {ticker:<10} {entry:>8,} {qty:>5}ju {int(budget):>9,} {int(cash):>12,}  {icon} {pnl:+,.0f}")

    print(f"  {'-'*55}")
    print(f"  최종 현금:   {int(cash):>12,}원")
    print(f"  누적 손익:   {int(total_pnl):>+12,}원")
    print(f"  누적 수수료: {int(total_fee):>12,}원")
    print(f"  체결: {trades}건  스킵: {skipped}건")
    print(f"  수익률: {(cash - INIT_CASH) / INIT_CASH * 100:+.2f}%")
    return {"cash": cash, "pnl": total_pnl, "fee": total_fee, "trades": trades, "skipped": skipped}


if __name__ == "__main__":
    print("\n초기자금: 10,000,000원  MAX_ORDER: 500,000원  모드: CAUTIOUS(70%)")

    r = {}
    r["A"] = simulate("A  (현재: 남은현금 기준)", calc_budget_A)
    r["B"] = simulate("B  (init_cash 기준 고정)", calc_budget_B)
    r["C"] = simulate("C  (init_cash + 잔액 소진)", calc_budget_C)

    print(f"\n{'='*55}")
    print(f"  비교 요약")
    print(f"{'='*55}")
    print(f"  {'방식':<6} {'체결':>5} {'스킵':>5} {'손익':>12} {'수수료':>10} {'최종현금':>13}")
    print(f"  {'-'*55}")
    for k, v in r.items():
        print(f"  {k:<6} {v['trades']:>5}건 {v['skipped']:>5}건 {int(v['pnl']):>+12,} {int(v['fee']):>10,} {int(v['cash']):>13,}")
