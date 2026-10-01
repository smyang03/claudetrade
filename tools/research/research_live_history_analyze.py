import json, csv, collections, statistics, math, sqlite3
from contextlib import closing
from datetime import date, timedelta
R = 'E:/code/claudetrade/'
SP = 'E:/code/claudetrade/data/analysis/live_history_20260924/'
d = json.load(open(SP + 'trips.json', encoding='utf-8'))
T = d['trips']

# add unmatched closes that carry pnl_krw
with closing(sqlite3.connect(f'file:{R}data/v2_event_store.db?mode=ro', uri=True, timeout=30)) as c:
    for u in d['unmatched']:
        pj = json.loads(c.execute('select payload_json from lifecycle_events where event_id=?', (u[0],)).fetchone()[0])
        T.append(dict(key='UNM_' + str(u[0]), market=u[1], ticker=u[3], entry_date=None, exit_date=u[2], qty=pj.get('qty') or 0, entry=None,
                      pnl_pct=pj.get('pnl_pct'), pnl_krw=pj.get('pnl_krw'), pnl_src='event_pnl_krw' if pj.get('pnl_krw') is not None else 'none',
                      notional_krw=None, strategy=pj.get('strategy_used') or '', route=pj.get('entry_route') or '', attribution='', reason=u[8],
                      hold_days=None, n_close_events=1, fee=None, pnl_krw_net=None, decision=u[4], unmatched_entry=True))

for r in json.load(open(SP + 'ghost_recovered.json')):
    T.append(dict(key='GR_'+r[1], market=r[0], ticker=r[1], entry_date=r[2], exit_date=r[3], qty=r[4], entry=r[5], pnl_pct=r[7], pnl_krw=r[8],
                  pnl_src='shortfall_ledger_fill', notional_krw=r[4]*r[5], strategy='', route='', attribution='ghost_recovered', reason='SELL_UNRECORDED(ledger fill)',
                  hold_days=None, n_close_events=0, fee=None, pnl_krw_net=None, decision='', sleeve=False))

def load_px(path):
    out = {}
    with open(path, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            try: out[r['date'][:10]] = (float(r['open']), float(r['close']))
            except: pass
    return out
IDX = {'KOSPI200(069500)': load_px(R + 'data/price/kr/kr_069500.csv'), 'KOSDAQ150(229200)': load_px(R + 'data/price/kr/kr_229200.csv'),
       'SPY': load_px(R + 'data/price/us/us_SPY.csv'), 'IWM': load_px(R + 'data/price/us/us_IWM.csv')}
KRI = ['KOSPI200(069500)', 'KOSDAQ150(229200)']; USI = ['SPY', 'IWM']

def idx_ret(name, d0, d1):
    px = IDX[name]; ds = sorted(px)
    a = [x for x in ds if x >= d0]; b = [x for x in ds if x <= d1]
    if not a or not b or a[0] > b[-1]:
        # same-day or missing
        if a and b and a[0] == b[-1]: pass
        else: return None
    return (px[b[-1]][1] / px[a[0]][0] - 1) * 100

def month_ret(name, ym):
    px = IDX[name]; ds = sorted(px)
    prev = [x for x in ds if x < ym + '-01']; inm = [x for x in ds if x[:7] == ym]
    if not prev or not inm: return None
    return (px[inm[-1]][1] / px[prev[-1]][1] - 1) * 100

def lane(t):
    s = (t['strategy'] or '').strip(); r = (t['route'] or '').strip(); a = t.get('attribution') or ''
    if a.startswith('audited_broker'): return 'unknown-origin(broker backfill)'
    if a == 'ghost_recovered': return 'unknown-origin(ghost sell recovered)'
    if s in ('us_swing_5d', 'kr_fallen_5d'): return 'sleeve:' + s
    if s in ('us_schg_bil_trend_v1', 'kr_factor_trend_v1'): return 'core:' + s
    if r == 'path_b': return 'PathB:' + (s or '?')
    if r in ('plan_a',): return 'PathA:' + (s or '?')
    if s == 'claude_price': return 'PathB:claude_price'
    return 'untagged:' + (s or '?')

for t in T:
    t['lane'] = lane(t)
    t['month'] = t['exit_date'][:7]
    if t.get('entry_date'):
        il = KRI if t['market'] == 'KR' else USI
        t['idx'] = {n: idx_ret(n, t['entry_date'], t['exit_date']) for n in il}
    else:
        t['idx'] = {}

# fee estimate: event fee_pct_round_trip medians
fee_pct = {'KR': [], 'US': []}
with closing(sqlite3.connect(f'file:{R}data/v2_event_store.db?mode=ro', uri=True, timeout=30)) as c:
    for m, p in c.execute("select market,payload_json from lifecycle_events where event_type='CLOSED' and runtime_mode='live'"):
        v = json.loads(p).get('fee_pct_round_trip')
        if v: fee_pct[m].append(float(v))
FEE = {m: statistics.median(v) if v else None for m, v in fee_pct.items()}

def is_net(t):  # sleeve rows carry broker_realized_krw
    return t['lane'].startswith('sleeve:')
for t in T:
    if t['pnl_krw'] is None: t['net_est'] = None; continue
    if is_net(t) or t.get('notional_krw') is None: t['net_est'] = t['pnl_krw']
    else: t['net_est'] = t['pnl_krw'] - t['notional_krw'] * FEE[t['market']] / 100

out = []
P = out.append
P(f"# trips={len(T)} (unmatched-entry closes included={sum(1 for t in T if t.get('unmatched_entry'))}) fee_pct_rt median KR={FEE['KR']:.3f}% (n={len(fee_pct['KR'])}) US={FEE['US']:.3f}% (n={len(fee_pct['US'])})")
P('first fill: ' + min(t['entry_date'] for t in T if t.get('entry_date')) + ' last exit: ' + max(t['exit_date'] for t in T))

def agg(rows):
    k = [r for r in rows if r['pnl_krw'] is not None]
    n = len(rows); w = sum(1 for r in rows if (r['pnl_pct'] or 0) > 0)
    return dict(n=n, win=w / n * 100 if n else 0, krw=sum(r['pnl_krw'] for r in k), net=sum(r['net_est'] for r in k),
                avg=statistics.mean(r['pnl_pct'] for r in rows if r['pnl_pct'] is not None) if n else 0,
                med=statistics.median(r['pnl_pct'] for r in rows if r['pnl_pct'] is not None) if n else 0,
                notional=sum(r['notional_krw'] or 0 for r in rows), nokrw=n - len(k))

P('\n## 월×시장 (청산일 기준)')
P('| 월 | 시장 | 건수 | 승률 | gross KRW | 수수료추정후 KRW | 평균% | 중앙% | 진입금액합 | 지수1 월% | 지수2 월% |')
for ym in sorted({t['month'] for t in T}):
    for m in ['KR', 'US']:
        rows = [t for t in T if t['month'] == ym and t['market'] == m]
        il = KRI if m == 'KR' else USI
        mr = [month_ret(n, ym) for n in il]
        if not rows:
            P(f"| {ym} | {m} | 0 | - | 0 | 0 | - | - | 0 | {mr[0]:+.2f} | {mr[1]:+.2f} |"); continue
        a = agg(rows)
        P(f"| {ym} | {m} | {a['n']} | {a['win']:.0f}% | {a['krw']:+,.0f} | {a['net']:+,.0f} | {a['avg']:+.2f} | {a['med']:+.2f} | {a['notional']:,.0f} | {mr[0]:+.2f} | {mr[1]:+.2f} |")
for m in ['KR', 'US', None]:
    rows = [t for t in T if m is None or t['market'] == m]; a = agg(rows)
    P(f"| 전체 | {m or 'KR+US'} | {a['n']} | {a['win']:.0f}% | {a['krw']:+,.0f} | {a['net']:+,.0f} | {a['avg']:+.2f} | {a['med']:+.2f} | {a['notional']:,.0f} | | |")

P('\n## 레인별')
P('| 레인 | 시장 | 건수 | 승률 | gross KRW | 수수료추정후 | 평균% | 중앙% | 진입금액합 | KRW없음 | 기간 |')
lanes = collections.defaultdict(list)
for t in T: lanes[(t['lane'], t['market'])].append(t)
for (l, m), rows in sorted(lanes.items(), key=lambda x: sum(r['pnl_krw'] or 0 for r in x[1])):
    a = agg(rows)
    P(f"| {l} | {m} | {a['n']} | {a['win']:.0f}% | {a['krw']:+,.0f} | {a['net']:+,.0f} | {a['avg']:+.2f} | {a['med']:+.2f} | {a['notional']:,.0f} | {a['nokrw']} | {min(r['exit_date'] for r in rows)}~{max(r['exit_date'] for r in rows)} |")

P('\n## 레인×청산사유 (KRW 합)')
rs = collections.defaultdict(list)
for t in T: rs[(t['lane'].split(':')[0], t['reason'])].append(t)
P('| 경로 | 청산사유 | 건수 | 승률 | gross KRW | 평균% |')
for (l, r), rows in sorted(rs.items(), key=lambda x: sum(y['pnl_krw'] or 0 for y in x[1])):
    a = agg(rows); P(f"| {l} | {r} | {a['n']} | {a['win']:.0f}% | {a['krw']:+,.0f} | {a['avg']:+.2f} |")

P('\n## 트레이드 초과수익 vs 같은 창 지수 (진입일 시가→청산일 종가), 날짜(청산일) 클러스터')
P('| 레인 | 시장 | 지수 | 건수 | 거래일수 | 트레이드평균% | 지수평균% | 일가중 초과평균% | 일 t | 초과>0 일비율 |')
for (l, m), rows in sorted(lanes.items()):
    il = KRI if m == 'KR' else USI
    for n in il:
        ok = [r for r in rows if r['idx'].get(n) is not None and r['pnl_pct'] is not None]
        if not ok: continue
        byd = collections.defaultdict(list)
        for r in ok: byd[r['exit_date']].append(r['pnl_pct'] - r['idx'][n])
        dm = [statistics.mean(v) for v in byd.values()]
        mu = statistics.mean(dm); sd = statistics.stdev(dm) if len(dm) > 1 else float('nan')
        tt = mu / (sd / math.sqrt(len(dm))) if len(dm) > 1 and sd > 0 else float('nan')
        P(f"| {l} | {m} | {n} | {len(ok)} | {len(dm)} | {statistics.mean(r['pnl_pct'] for r in ok):+.2f} | {statistics.mean(r['idx'][n] for r in ok):+.2f} | {mu:+.2f} | {tt:.2f} | {sum(1 for x in dm if x > 0) / len(dm) * 100:.0f}% |")
# overall per market
for m in ['KR', 'US']:
    il = KRI if m == 'KR' else USI
    for n in il:
        ok = [r for r in T if r['market'] == m and r['idx'].get(n) is not None]
        byd = collections.defaultdict(list)
        for r in ok: byd[r['exit_date']].append(r['pnl_pct'] - r['idx'][n])
        dm = [statistics.mean(v) for v in byd.values()]
        mu = statistics.mean(dm); sd = statistics.stdev(dm)
        P(f"| **{m} 전체** | {m} | {n} | {len(ok)} | {len(dm)} | {statistics.mean(r['pnl_pct'] for r in ok):+.2f} | {statistics.mean(r['idx'][n] for r in ok):+.2f} | {mu:+.2f} | {mu / (sd / math.sqrt(len(dm))):.2f} | {sum(1 for x in dm if x > 0) / len(dm) * 100:.0f}% |")

P('\n## 손실 상위 10 (KRW)')
P('| 시장 | 종목 | 진입 | 청산 | 레인 | 청산사유 | 수량 | 진입가 | % | KRW | 보유일 | 지수(동창)% |')
k = [t for t in T if t['pnl_krw'] is not None]
for t in sorted(k, key=lambda x: x['pnl_krw'])[:10]:
    ix = ' / '.join(f"{v:+.1f}" for v in t['idx'].values() if v is not None)
    P(f"| {t['market']} | {t['ticker']} | {t['entry_date']} | {t['exit_date']} | {t['lane']} | {t['reason']} | {t['qty']:g} | {t['entry'] if t['entry'] else '-'} | {t['pnl_pct']:+.2f} | {t['pnl_krw']:+,.0f} | {t['hold_days']} | {ix} |")
P('\n## 수익 상위 10 (KRW)')
P('| 시장 | 종목 | 진입 | 청산 | 레인 | 청산사유 | 수량 | 진입가 | % | KRW | 보유일 | 지수(동창)% |')
for t in sorted(k, key=lambda x: -x['pnl_krw'])[:10]:
    ix = ' / '.join(f"{v:+.1f}" for v in t['idx'].values() if v is not None)
    P(f"| {t['market']} | {t['ticker']} | {t['entry_date']} | {t['exit_date']} | {t['lane']} | {t['reason']} | {t['qty']:g} | {t['entry'] if t['entry'] else '-'} | {t['pnl_pct']:+.2f} | {t['pnl_krw']:+,.0f} | {t['hold_days']} | {ix} |")

# concentration
tot = sum(t['pnl_krw'] for t in k)
srt = sorted(k, key=lambda x: x['pnl_krw'])
P(f"\n총 gross {tot:+,.0f} / 최악 10건 합 {sum(t['pnl_krw'] for t in srt[:10]):+,.0f} / 최고 10건 합 {sum(t['pnl_krw'] for t in srt[-10:]):+,.0f} / 나머지 {tot - sum(t['pnl_krw'] for t in srt[:10]) - sum(t['pnl_krw'] for t in srt[-10:]):+,.0f}")

# capital: peak concurrent exposure
exp = collections.Counter()
for t in T:
    if not t.get('entry_date') or not t.get('notional_krw'): continue
    d0 = date.fromisoformat(t['entry_date']); d1 = date.fromisoformat(t['exit_date'])
    x = d0
    while x <= d1:
        exp[(x.isoformat(), t['market'])] += t['notional_krw']; x += timedelta(days=1)
for m in ['KR', 'US']:
    v = [(dd, a) for (dd, mm), a in exp.items() if mm == m]
    pk = max(v, key=lambda z: z[1])
    bym = collections.defaultdict(list)
    for dd, a in v: bym[dd[:7]].append(a)
    P(f"{m} 최대 동시노출 {pk[1]:,.0f} ({pk[0]}); 월별 최대: " + ', '.join(f"{ym} {max(a):,.0f}" for ym, a in sorted(bym.items())))
P(f"총 진입금액(회전) {sum(t['notional_krw'] or 0 for t in T):,.0f}")

# hold-days distribution
hd = collections.Counter(('0d' if t['hold_days'] == 0 else '1-2d' if t['hold_days'] is not None and t['hold_days'] <= 2 else '3d+' if t['hold_days'] is not None else '?') for t in T)
P('보유기간 분포(달력일): ' + str(dict(hd)))
# up/down month capture
P('\n## 월별 부호 일치')
for m in ['KR', 'US']:
    il = KRI if m == 'KR' else USI
    for ym in sorted({t['month'] for t in T}):
        rows = [t for t in T if t['month'] == ym and t['market'] == m and t['pnl_krw'] is not None]
        if not rows: continue
        s = sum(r['pnl_krw'] for r in rows); mr = month_ret(il[1], ym); mr0 = month_ret(il[0], ym)
        P(f"{m} {ym}: 시스템 {'+' if s > 0 else '-'} ({s:+,.0f}) / {il[0]} {mr0:+.2f}% / {il[1]} {mr:+.2f}%")
open(SP + 'report.md', 'w', encoding='utf-8').write('\n'.join(out))
json.dump(T, open(SP + 'trips_enriched.json', 'w', encoding='utf-8'), ensure_ascii=False, default=str)
print('\n'.join(out))
