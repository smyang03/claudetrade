"""전략 점검 — 원장 실측 (read-only)."""
import sqlite3, math, statistics as st
from collections import defaultdict
from pathlib import Path

p = Path(r'E:\code\claudetrade\data\shadow\virtual_books.db').resolve().as_uri() + '?mode=ro'
con = sqlite3.connect(p, uri=True)
con.row_factory = sqlite3.Row

TODAY = '2026-09-11'


def cluster_t(rows):
    """종목 클러스터 t — 같은 종목 반복을 1표본으로."""
    g = defaultdict(list)
    for tk, net in rows:
        g[tk].append(net)
    means = [st.mean(v) for v in g.values()]
    if len(means) < 3:
        return None, len(means)
    sd = st.stdev(means)
    if sd == 0:
        return None, len(means)
    return st.mean(means) / (sd / math.sqrt(len(means))), len(means)


strat = {r['id']: r for r in con.execute("SELECT * FROM strategies")}
out = []
for sid, in con.execute("SELECT DISTINCT strategy_id FROM trades"):
    rows = list(con.execute(
        "SELECT ticker, session_date, net_pct, backfill, status FROM trades WHERE strategy_id=?", (sid,)))
    fwd = [(r['ticker'], r['net_pct']) for r in rows
           if r['status'] == 'CLOSED' and (r['backfill'] or 0) == 0 and r['net_pct'] is not None]
    bak = [(r['ticker'], r['net_pct']) for r in rows
           if r['status'] == 'CLOSED' and (r['backfill'] or 0) == 1 and r['net_pct'] is not None]
    fwd_dates = [r['session_date'] for r in rows if (r['backfill'] or 0) == 0]
    by_year = defaultdict(list)
    for r in rows:
        if r['status'] == 'CLOSED' and r['net_pct'] is not None:
            by_year[r['session_date'][:4]].append(r['net_pct'])
    yr = {y: round(st.mean(v), 2) for y, v in sorted(by_year.items())}
    ft, fk = cluster_t(fwd)
    bt, bk = cluster_t(bak)
    s = strat.get(sid) or {}
    out.append({
        'id': sid,
        'fwd_n': len(fwd), 'fwd_mean': st.mean([x[1] for x in fwd]) if fwd else None,
        'fwd_t': ft, 'fwd_k': fk,
        'bak_n': len(bak), 'bak_mean': st.mean([x[1] for x in bak]) if bak else None,
        'bak_t': bt, 'bak_k': bk,
        'open': sum(1 for r in rows if r['status'] == 'OPEN'),
        'last_fwd': max(fwd_dates) if fwd_dates else None,
        'yr': yr,
        'pick': (s['pick'] if s else None), 'cap': (s['daily_cap'] if s else None),
        'slots': (s['slots'] if s else None),
    })

out.sort(key=lambda r: (-(r['fwd_n'] or 0), -(r['bak_n'] or 0)))
hdr = f"{'strategy':32}{'fwdN':>5}{'fwdMean':>9}{'fwdT':>7}{'fwdK':>5} | {'bakN':>7}{'bakMean':>9}{'bakT':>7}{'bakK':>6} | {'open':>5} {'최종공급':>10}  연도별"
print(hdr)
print('-' * len(hdr))
for r in out:
    def f(v, w, d=2):
        return f"{v:>{w}.{d}f}" if isinstance(v, float) else f"{'-':>{w}}"
    stale = ''
    if r['last_fwd']:
        gap = (int(TODAY[:4]) * 372 + int(TODAY[5:7]) * 31 + int(TODAY[8:])) - \
              (int(r['last_fwd'][:4]) * 372 + int(r['last_fwd'][5:7]) * 31 + int(r['last_fwd'][8:]))
        stale = f" (D+{gap})" if gap > 7 else ""
    print(f"{r['id']:32}{r['fwd_n']:>5}{f(r['fwd_mean'],9)}{f(r['fwd_t'],7)}{r['fwd_k']:>5} | "
          f"{r['bak_n']:>7}{f(r['bak_mean'],9)}{f(r['bak_t'],7)}{r['bak_k']:>6} | "
          f"{r['open']:>5} {str(r['last_fwd']):>10}{stale}  {r['yr']}")
con.close()
