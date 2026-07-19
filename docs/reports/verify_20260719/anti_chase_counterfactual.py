"""anti-chase 반사실 — spike_chase_level별 net + 배제 시 winner/loser 트레이드오프. read-only."""
import sqlite3, csv, os

conn = sqlite3.connect('file:data/ml/decisions.db?mode=ro', uri=True, timeout=10)
conn.execute('PRAGMA busy_timeout=5000'); conn.row_factory = sqlite3.Row
trades = conn.execute(
    "SELECT ticker, market, session_date, close_reason, pnl_pct_net, pnl_krw_net "
    "FROM v2_learning_performance WHERE closed=1 AND pnl_pct_net IS NOT NULL AND session_date IS NOT NULL"
).fetchall()
conn.close()


def max_daily_21d(market, ticker, entry_date, lookback=21):
    mk = market.lower()
    path = os.path.join('data', 'price', mk, f'{mk}_{ticker}.csv')
    if not os.path.exists(path):
        return None
    rows = []
    try:
        with open(path, encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                d = str(r.get('date', '')).strip()
                c = r.get('close')
                if d and c not in (None, '') and d < entry_date:
                    try:
                        rows.append((d, float(c)))
                    except ValueError:
                        pass
    except OSError:
        return None
    rows.sort()
    rows = rows[-(lookback + 1):]
    if len(rows) < 6:
        return None
    rets = [(rows[i][1] / rows[i - 1][1] - 1.0) * 100.0 for i in range(1, len(rows)) if rows[i - 1][1] > 0]
    return max(rets) if rets else None


def level(mx):
    if mx is None:
        return None
    return 12 if mx >= 12.0 else (8 if mx >= 8.0 else 0)


enr = []
for t in trades:
    mx = max_daily_21d(t['market'], t['ticker'], t['session_date'])
    lv = level(mx)
    if lv is None:
        continue
    krw = t['pnl_krw_net'] if t['pnl_krw_net'] is not None else 0.0
    enr.append((lv, t['pnl_pct_net'], krw, t['close_reason'], t['market']))

print(f"커버 {len(enr)}/{len(trades)}\n")
print("=== spike_chase_level별 net ===")
for lv in (0, 8, 12):
    g = [x for x in enr if x[0] == lv]
    if not g:
        continue
    n = len(g)
    net_krw = sum(x[2] for x in g)
    avg_pct = sum(x[1] for x in g) / n
    wr = 100.0 * sum(1 for x in g if x[1] > 0) / n
    tgt = sum(1 for x in g if x[3] == 'CLOSED_CLAUDE_PRICE_TARGET')
    lcap = sum(1 for x in g if x[3] == 'CLOSED_LOSS_CAP')
    print(f"  level {lv:>2}: n={n:>3} netKRW={net_krw:>9.0f} avg={avg_pct:+.3f}% 승률={wr:.0f}% | TARGET승={tgt} LOSS_CAP={lcap}")

base_krw = sum(x[2] for x in enr)
print(f"\n전체 net = {base_krw:.0f}원\n")
print("=== 반사실: spike 코호트 배제 시 (winner도 잃는가 확인) ===")
for cut in (8, 12):
    excluded = [x for x in enr if x[0] >= cut]
    kept = [x for x in enr if x[0] < cut]
    exc_krw = sum(x[2] for x in excluded)
    exc_tgt = sum(1 for x in excluded if x[3] == 'CLOSED_CLAUDE_PRICE_TARGET')
    exc_lcap = sum(1 for x in excluded if x[3] == 'CLOSED_LOSS_CAP')
    exc_tgt_krw = sum(x[2] for x in excluded if x[3] == 'CLOSED_CLAUDE_PRICE_TARGET')
    new_net = sum(x[2] for x in kept)
    print(f"  level>={cut} 배제: 배제 {len(excluded)}건(net {exc_krw:.0f}원, TARGET승 {exc_tgt}건/{exc_tgt_krw:.0f}원, LOSS_CAP {exc_lcap}건)")
    print(f"     → 잔여 net {new_net:.0f}원 (전체 {base_krw:.0f} 대비 {new_net-base_krw:+.0f}원)")
    if excluded:
        print(f"     배제 코호트 승률 {100.0*sum(1 for x in excluded if x[1]>0)/len(excluded):.0f}% (버리는 게 주로 loser면 좋음)")
    print()
