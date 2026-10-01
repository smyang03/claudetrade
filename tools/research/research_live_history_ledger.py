import sqlite3, json, collections, csv, statistics, re, sys
from contextlib import closing
from datetime import datetime, timedelta
R = 'E:/code/claudetrade/'
SP = 'E:/code/claudetrade/data/analysis/live_history_20260924/'

def ro(p):
    c = sqlite3.connect(f'file:{R}{p}?mode=ro', uri=True, timeout=30)
    c.execute('pragma busy_timeout=30000')
    return c

# ---------- FX by date (median usd_krw from decisions, live) ----------
fx = {}
with closing(ro('data/ml/decisions.db')) as c:
    tmp = collections.defaultdict(list)
    for d, v in c.execute("select session_date, usd_krw from decisions where usd_krw>900 and session_date>='2026-04-01'"):
        tmp[d].append(v)
    fx = {d: statistics.median(v) for d, v in tmp.items()}
    canon = {r[0]: r for r in c.execute("select v2_decision_id, strategy, route, path_type, strategy_attribution from v2_canonical_performance")}
    lclose = {r[0]: r[1] for r in c.execute("select v2_decision_id, close_reason from v2_learning_performance")}
fxd = sorted(fx)
def get_fx(d):
    best = None
    for x in fxd:
        if x <= d: best = x
    return fx[best] if best else fx[fxd[0]]

# ---------- events ----------
with closing(ro('data/v2_event_store.db')) as c:
    F = c.execute("select event_id,market,session_date,ticker,decision_id,execution_id,position_id,occurred_at,payload_json from lifecycle_events where event_type='FILLED' and runtime_mode='live' order by event_id").fetchall()
    C = c.execute("select event_id,market,session_date,ticker,decision_id,execution_id,position_id,occurred_at,reason_code,payload_json from lifecycle_events where event_type='CLOSED' and runtime_mode='live' order by event_id").fetchall()

# positions: dedup buy fills by order_no (execution_id); null order -> position_id
pos = {}
raw_fill = collections.Counter()
for r in F:
    pj = json.loads(r[8])
    key = r[5] or pj.get('order_no') or r[6]
    raw_fill[key] += 1
    if key in pos:
        p = pos[key]
        if not p['strategy'] and pj.get('strategy_used'): p['strategy'] = pj.get('strategy_used')
        continue
    pos[key] = dict(key=key, market=r[1], entry_date=r[2], ticker=r[3], decision=r[4], filled_at=r[7],
                    qty=float(pj.get('qty') or 0), entry=float(pj.get('fill_price_native') or pj.get('price') or 0),
                    strategy=pj.get('strategy_used') or '', route=pj.get('entry_route') or '', closes=[])
dec2pos = collections.defaultdict(list)
for k, p in pos.items(): dec2pos[p['decision']].append(k)

def pos_from_posid(pid):
    if not pid: return None
    m = re.match(r'pos_(KR|US)_[^_]+_(.+)$', pid)
    if m and m.group(2) in pos: return m.group(2)
    return None

unmatched = []
for r in C:
    pj = json.loads(r[9])
    k = pos_from_posid(r[6])
    if k is None and r[5] in pos: k = r[5]
    if k is None:
        cands = [x for x in dec2pos.get(r[4], []) if pos[x]['filled_at'] <= r[7]]
        if len(cands) == 1: k = cands[0]
        elif len(cands) > 1:
            # choose the earliest-filled candidate without a close yet
            open_c = [x for x in cands if not pos[x]['closes']]
            k = (open_c or cands)[0]
    if k is None:
        cands = [x for x, p in pos.items() if p['market'] == r[1] and p['ticker'] == r[3] and p['filled_at'] <= r[7] and not p['closes']]
        if cands: k = cands[-1]
    if k is None:
        unmatched.append(r); continue
    pos[k]['closes'].append(dict(event_id=r[0], date=r[2], at=r[7], reason=r[8] or pj.get('close_reason'),
                                 qty=pj.get('qty'), pnl_pct=pj.get('pnl_pct'), pnl_krw=pj.get('pnl_krw'),
                                 price=pj.get('exit_price_native') or pj.get('broker_sell_fill_price_native') or pj.get('price') or pj.get('exit_price'),
                                 exit_fx=pj.get('exit_fx'), entry_fx=pj.get('entry_fx'),
                                 pnl_krw_net=pj.get('pnl_krw_net') or pj.get('pnl_krw_net_est'), fee=pj.get('fee_krw_est'),
                                 src_strat=pj.get('source_strategy') or pj.get('strategy_used'), emitted=pj.get('emitted_by'), sleeve=pj.get('sleeve_contract'), source_strategy=pj.get('source_strategy')))

# ---------- round trips ----------
trips = []
for k, p in pos.items():
    if not p['closes']: continue
    cl = p['closes']
    # final close = last event in time; prefer executor-type (has qty) among events at the final exit date
    last_date = max(x['at'] for x in cl)
    fin = cl[-1]
    q_type = [x for x in cl if x['qty'] is not None]
    # choose last Q-type if its time within 1 hour of last close, else last close
    if q_type:
        lq = q_type[-1]
        if abs((datetime.fromisoformat(fin['at']) - datetime.fromisoformat(lq['at'])).total_seconds()) < 3 * 3600:
            fin = lq
    pct = fin['pnl_pct']
    if pct is None and fin['price'] and p['entry']:
        pct = (float(fin['price']) / p['entry'] - 1) * 100
    if pct is None: continue
    qty = p['qty'] or float(fin['qty'] or 0)
    notional_native = qty * p['entry']
    efx = get_fx(p['entry_date']) if p['market'] == 'US' else 1.0
    xfx = get_fx(fin['date']) if p['market'] == 'US' else 1.0
    notional_krw = notional_native * efx
    if fin['pnl_krw'] is not None:
        pnl_krw = float(fin['pnl_krw']); src = 'event_pnl_krw'
    else:
        # native pnl converted at exit fx + fx change on principal
        exit_px = p['entry'] * (1 + pct / 100)
        pnl_krw = qty * (exit_px * xfx - p['entry'] * efx); src = 'recomputed'
    strat = p['strategy'] or fin['src_strat'] or ''
    sleeve_src = next((x['source_strategy'] for x in cl if x.get('sleeve')), None)
    if sleeve_src: strat = sleeve_src
    cz = canon.get(p['decision'])
    if not strat and cz: strat = cz[1] or ''
    attr = cz[4] if cz else ''
    reason = fin['reason'] or ''
    hold = (datetime.fromisoformat(fin['date']) - datetime.fromisoformat(p['entry_date'])).days
    trips.append(dict(key=k, market=p['market'], ticker=p['ticker'], entry_date=p['entry_date'], exit_date=fin['date'],
                      qty=qty, entry=p['entry'], pnl_pct=float(pct), pnl_krw=pnl_krw, pnl_src=src, notional_krw=notional_krw,
                      strategy=strat, route=p['route'] or (cz[2] if cz else ''), attribution=attr, reason=reason, hold_days=hold,
                      n_close_events=len(cl), sleeve=bool(sleeve_src), fee=fin['fee'], pnl_krw_net=fin['pnl_krw_net'], decision=p['decision']))

openpos = [p for p in pos.values() if not p['closes']]
json.dump(dict(trips=trips, open=[{k: v for k, v in p.items() if k != 'closes'} for p in openpos],
               unmatched=[list(r[:9]) for r in unmatched],
               stats=dict(raw_fill_events=len(F), distinct_positions=len(pos), dup_fill_orders=sum(1 for v in raw_fill.values() if v > 1),
                          close_events=len(C), unmatched_close=len(unmatched), trips=len(trips), open=len(openpos))),
          open(SP + 'trips.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=0, default=str)
print(json.dumps(dict(raw_fill_events=len(F), distinct_positions=len(pos), dup_fill_orders=sum(1 for v in raw_fill.values() if v > 1),
                      close_events=len(C), unmatched_close=len(unmatched), trips=len(trips), open=len(openpos))))
print('pnl src', collections.Counter(t['pnl_src'] for t in trips))
for r in unmatched: print('UNMATCHED', r[1:9])
