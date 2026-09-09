# -*- coding: utf-8 -*-
"""강제매도 지문 × 출구 계약 (2026-09-10, 강제매도 가설 2차).

1차 결과: 갭다운 급락(반대매매 지문)은 승률 58.7% vs 갭 없는 급락 48.8%인데 **평균은 더 나쁘다**(−1.10 vs −0.48).
승률이 높은데 평균이 낮으면 지는 쪽 꼬리가 두껍다는 뜻이고, 현행 계약 TP12/SL25는 손실을 12%p 더 허용한다.
→ 지문이 방향을 가르는데 계약이 우위를 반납하는지 확인한다. 원장 meta.grid(8계약 박제)와 mfe/mae 사용.
"""
import sqlite3, json, statistics as st
from contextlib import closing
from collections import defaultdict
DB = 'file:data/shadow/virtual_books.db?mode=ro'
GRIDS = ["tp6_sl6_d2", "tp8_sl10_d3", "tp12_sl25_d5", "tp12_sl25_d5_be", "tp12_sl25_d10", "tp20_sl25_d10", "hold_d5", "hold_d10"]

def stats(rows):
    if not rows: return None
    byd = defaultdict(list)
    for d, v in rows: byd[d].append(v)
    sm = [st.mean(v) for v in byd.values()]
    t = st.mean(sm) / (st.pstdev(sm) / len(sm) ** 0.5) if len(sm) > 2 and st.pstdev(sm) else float('nan')
    return st.mean(sm), t, 100 * sum(v > 0 for _, v in rows) / len(rows), len(rows)

with closing(sqlite3.connect(DB, uri=True, timeout=5)) as c:
    raw = c.execute("select session_date, ticker, net_pct, meta from trades where strategy_id='xkr_fallen3' and backfill=1 and status='CLOSED' and net_pct is not null").fetchall()
T = []
for d, tk, net, meta in raw:
    m = json.loads(meta) if meta else {}
    f = m.get('feat') or {}; g = m.get('grid') or {}
    if f.get('gap') is None or not g: continue
    T.append(dict(d=d, tk=tk, net=net, gap=f['gap'], ibs=f.get('ibs') or 0, ds=f.get('down_streak') or 0,
                  vs=f.get('vol_spike') or 0, dvol=f.get('dvol') or 0, mfe=m.get('mfe'), mae=m.get('mae'),
                  g={k: (v[0] if isinstance(v, list) else v) for k, v in g.items()},
                  half='H1' if d < '2026-01-01' else 'H2'))
print(f"xkr_fallen3 백필 grid 있는 건 {len(T)}\n")
GROUPS = {
    "전량": lambda t: True,
    "갭다운 ≤−3 (반대매매형)": lambda t: t['gap'] <= -3,
    "갭다운 ≤−5": lambda t: t['gap'] <= -5,
    "갭 > −3 (장중 밀림형)": lambda t: t['gap'] > -3,
    "정보매도형(갭≥0 & IBS<20)": lambda t: t['gap'] >= 0 and t['ibs'] < 20,
}
print("계약별 세션평균 / t / 승률  — 열: 그룹")
hdr = "계약".ljust(18) + "".join(g[:22].ljust(24) for g in GROUPS)
print(hdr)
for gk in ["tp12_sl25_d7(현행)"] + GRIDS:
    line = gk.ljust(18)
    for _, fn in GROUPS.items():
        sel = [(t['d'], t['net'] if gk.startswith('tp12_sl25_d7') else t['g'].get(gk)) for t in T if fn(t)]
        sel = [(d, v) for d, v in sel if v is not None]
        s = stats(sel)
        line += (f"{s[0]:+6.2f} t{s[1]:+5.2f} {s[2]:4.1f}%" if s else "     ·      ").ljust(24)
    print(line)

print("\nMFE/MAE 분포 (진입 후 7세션 최대 상승/최대 하락, %)")
print("그룹".ljust(26) + "MFE 중앙  MFE 평균  MAE 중앙  MAE 평균  MFE≥12 비율  MAE≤−25 비율")
for g, fn in GROUPS.items():
    sel = [t for t in T if fn(t) and t['mfe'] is not None and t['mae'] is not None]
    if not sel: continue
    mf = [t['mfe'] for t in sel]; ma = [t['mae'] for t in sel]
    print(f"{g[:25].ljust(26)}{st.median(mf):+7.2f} {st.mean(mf):+8.2f} {st.median(ma):+9.2f} {st.mean(ma):+8.2f} "
          f"{100*sum(x>=12 for x in mf)/len(mf):10.1f}% {100*sum(x<=-25 for x in ma)/len(ma):11.1f}%")

print("\n반기 안정성 — 상위 계약 후보")
for gk in ("tp6_sl6_d2", "tp8_sl10_d3", "tp12_sl25_d7(현행)"):
    for g, fn in (("갭다운 ≤−3", GROUPS["갭다운 ≤−3 (반대매매형)"]), ("갭 > −3", GROUPS["갭 > −3 (장중 밀림형)"])):
        row = []
        for h in ("H1", "H2"):
            sel = [(t['d'], t['net'] if gk.startswith('tp12') and 'd7' in gk else t['g'].get(gk)) for t in T if fn(t) and t['half'] == h]
            sel = [(d, v) for d, v in sel if v is not None]
            s = stats(sel); row.append(f"{h} {s[0]:+.2f}(t{s[1]:+.1f}, n{s[3]})" if s else f"{h} ·")
        print(f"  {gk:18s} {g:12s} " + " | ".join(row))

print("\nK=1 실행 형태(세션당 거래대금 1위) × 계약")
for gk in ("tp6_sl6_d2", "tp8_sl10_d3", "tp12_sl25_d5", "tp12_sl25_d7(현행)", "hold_d5"):
    line = f"  {gk:20s}"
    for g, fn in GROUPS.items():
        byd = defaultdict(list)
        for t in T:
            if fn(t): byd[t['d']].append(t)
        picks = [max(v, key=lambda x: x['dvol']) for v in byd.values()]
        sel = [(t['d'], t['net'] if gk.startswith('tp12') and 'd7' in gk else t['g'].get(gk)) for t in picks]
        sel = [(d, v) for d, v in sel if v is not None]
        s = stats(sel)
        line += (f"{s[0]:+6.2f} t{s[1]:+5.2f} {s[2]:4.1f}%" if s else "     ·      ").ljust(24)
    print(line)
