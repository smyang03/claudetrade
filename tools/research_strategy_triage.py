"""전략 점검 — 원장 실측 (read-only)."""
import sqlite3, math, statistics as st
from collections import defaultdict
from pathlib import Path

p = Path(r'E:\code\claudetrade\data\shadow\virtual_books.db').resolve().as_uri() + '?mode=ro'
con = sqlite3.connect(p, uri=True)
con.row_factory = sqlite3.Row

TODAY = '2026-09-11'


def cluster_t(rows, key=0):
    """클러스터 t. key=0 종목 / key=1 날짜.

    급락 계열은 표본이 며칠에 몰리므로 **날짜가 맞는 단위**다. 종목 클러스터는
    같은 며칠을 반복해서 세기 때문에 t를 구조적으로 부풀린다
    (실측 2026-09-11: c_kr_fallen5_nobio_rg 종목 t 9.07 vs 날짜 t 1.67).
    """
    g = defaultdict(list)
    for row in rows:
        g[row[key]].append(row[-1])
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
    fwd = [(r['ticker'], r['session_date'], r['net_pct']) for r in rows
           if r['status'] == 'CLOSED' and (r['backfill'] or 0) == 0 and r['net_pct'] is not None]
    bak = [(r['ticker'], r['session_date'], r['net_pct']) for r in rows
           if r['status'] == 'CLOSED' and (r['backfill'] or 0) == 1 and r['net_pct'] is not None]
    fwd_dates = [r['session_date'] for r in rows if (r['backfill'] or 0) == 0]
    by_year = defaultdict(list)
    for r in rows:
        if r['status'] == 'CLOSED' and r['net_pct'] is not None:
            by_year[r['session_date'][:4]].append(r['net_pct'])
    yr = {y: round(st.mean(v), 2) for y, v in sorted(by_year.items())}
    ft, fk = cluster_t(fwd, 1)          # 날짜 클러스터가 판정 기본값
    bt, bk = cluster_t(bak, 1)
    ft_tk, _ = cluster_t(fwd, 0)        # 종목 클러스터는 부풀림 확인용
    bt_tk, _ = cluster_t(bak, 0)
    s = strat.get(sid) or {}
    out.append({
        'id': sid,
        'fwd_n': len(fwd), 'fwd_mean': st.mean([x[-1] for x in fwd]) if fwd else None,
        'fwd_t': ft, 'fwd_k': fk, 'fwd_t_tk': ft_tk,
        'bak_n': len(bak), 'bak_mean': st.mean([x[-1] for x in bak]) if bak else None,
        'bak_t': bt, 'bak_k': bk, 'bak_t_tk': bt_tk,
        'open': sum(1 for r in rows if r['status'] == 'OPEN'),
        'last_fwd': max(fwd_dates) if fwd_dates else None,
        'yr': yr,
        'pick': (s['pick'] if s else None), 'cap': (s['daily_cap'] if s else None),
        'slots': (s['slots'] if s else None),
    })

out.sort(key=lambda r: (-(r['fwd_n'] or 0), -(r['bak_n'] or 0)))
hdr = (f"{'strategy':32}{'fwdN':>5}{'fwdMean':>9}{'fwdT일':>7}{'K일':>5} | "
       f"{'bakN':>7}{'bakMean':>9}{'bakT일':>7}{'K일':>6}{'bakT종목':>9} | {'open':>5} {'최종공급':>10}  연도별")
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
          f"{r['bak_n']:>7}{f(r['bak_mean'],9)}{f(r['bak_t'],7)}{r['bak_k']:>6}{f(r['bak_t_tk'],9)} | "
          f"{r['open']:>5} {str(r['last_fwd']):>10}{stale}  {r['yr']}")
con.close()
