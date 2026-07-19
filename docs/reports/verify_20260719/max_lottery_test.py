"""검증: 우리 LOSS_CAP 손실 종목이 진입 전 高MAX(로또주 시그니처)인가? read-only.
Bali/Cakici/Whitelaw(2011): 최근 1M 최대 일간수익(MAX) 높은 종목 = 이후 언더퍼폼.
가설: 우리가 잃는 종목 = 진입 시점 高MAX(급등 추격). 맞으면 MAX 회피 스크린이 손실 축소."""
import sqlite3, csv, os
from datetime import datetime, timedelta

conn = sqlite3.connect('file:data/ml/decisions.db?mode=ro', uri=True, timeout=10)
conn.execute('PRAGMA busy_timeout=5000'); conn.row_factory = sqlite3.Row

trades = conn.execute(
    "SELECT ticker, market, session_date, close_reason, pnl_pct_net "
    "FROM v2_learning_performance WHERE closed=1 AND pnl_pct_net IS NOT NULL AND session_date IS NOT NULL"
).fetchall()
conn.close()


def daily_max_and_vol(market, ticker, entry_date, lookback=21):
    mk = market.lower()
    path = os.path.join('data', 'price', mk, f'{mk}_{ticker}.csv')
    if not os.path.exists(path):
        return None, None
    rows = []
    try:
        with open(path, encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                d = str(r.get('date', '')).strip()
                c = r.get('close')
                if d and c not in (None, '') and d < entry_date:  # 진입일 이전만(lookahead 방지)
                    try:
                        rows.append((d, float(c)))
                    except ValueError:
                        pass
    except OSError:
        return None, None
    rows.sort()
    rows = rows[-(lookback + 1):]
    if len(rows) < 6:
        return None, None
    rets = [(rows[i][1] / rows[i - 1][1] - 1.0) * 100.0 for i in range(1, len(rows)) if rows[i - 1][1] > 0]
    if not rets:
        return None, None
    max_daily = max(rets)
    mean = sum(rets) / len(rets)
    vol = (sum((x - mean) ** 2 for x in rets) / len(rets)) ** 0.5
    return round(max_daily, 2), round(vol, 2)


buckets = {}  # close_reason group -> list of (max_daily, vol, pnl)
covered = 0
for t in trades:
    mx, vol = daily_max_and_vol(t['market'], t['ticker'], t['session_date'])
    if mx is None:
        continue
    covered += 1
    grp = 'TARGET' if t['close_reason'] == 'CLOSED_CLAUDE_PRICE_TARGET' else (
        'LOSS_CAP' if t['close_reason'] == 'CLOSED_LOSS_CAP' else 'OTHER')
    buckets.setdefault(grp, []).append((mx, vol, t['pnl_pct_net']))
    buckets.setdefault('ALL', []).append((mx, vol, t['pnl_pct_net']))

print(f"일봉 커버 {covered}/{len(trades)} 트레이드\n")
print("=== 진입 전 MAX(최근 21일 최대 일간수익%) / 변동성 by 결과 그룹 ===")
for grp in ('TARGET', 'LOSS_CAP', 'OTHER', 'ALL'):
    v = buckets.get(grp, [])
    if not v:
        continue
    mxs = sorted(x[0] for x in v)
    vols = [x[1] for x in v]
    med_mx = mxs[len(mxs) // 2]
    avg_mx = sum(mxs) / len(mxs)
    avg_vol = sum(vols) / len(vols)
    print(f"  {grp:9} n={len(v):>3} | MAX 평균={avg_mx:5.2f}% 중앙={med_mx:5.2f}% | 변동성평균={avg_vol:.2f}%")

print("\n=== MAX 분위(진입 전)별 실현 net — 高MAX가 정말 더 잃나 ===")
allv = sorted(buckets.get('ALL', []), key=lambda x: x[0])
n = len(allv)
if n >= 10:
    for lbl, lo, hi in [('하위30% (저MAX)', 0, 0.3), ('중간40%', 0.3, 0.7), ('상위30% (高MAX)', 0.7, 1.0)]:
        seg = allv[int(lo * n):int(hi * n)]
        if seg:
            nets = [x[2] for x in seg]
            wr = 100.0 * sum(1 for x in nets if x > 0) / len(nets)
            print(f"  {lbl:16} n={len(seg):>3} MAX범위 {seg[0][0]:.1f}~{seg[-1][0]:.1f}% | 평균net={sum(nets)/len(nets):+.3f}% 승률={wr:.0f}%")
