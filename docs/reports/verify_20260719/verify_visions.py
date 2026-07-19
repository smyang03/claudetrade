"""여섯 비전 검증 — 우리 데이터. read-only. 각 가설을 실증/반증."""
import sqlite3, csv, os, json, glob, math

conn = sqlite3.connect('file:data/ml/decisions.db?mode=ro', uri=True, timeout=10)
conn.execute('PRAGMA busy_timeout=5000'); conn.row_factory = sqlite3.Row
T = conn.execute(
    "SELECT ticker,market,session_date,close_reason,pnl_pct_net,mfe_pct,mae_pct,strategy,market_regime "
    "FROM v2_learning_performance WHERE closed=1 AND pnl_pct_net IS NOT NULL"
).fetchall()
conn.close()


def maxd(mk, tk, ed, lb=21):
    p = os.path.join('data', 'price', mk.lower(), f'{mk.lower()}_{tk}.csv')
    if not os.path.exists(p):
        return None
    rows = []
    try:
        for r in csv.DictReader(open(p, encoding='utf-8-sig')):
            d = str(r.get('date', '')).strip(); c = r.get('close')
            if d and c not in (None, '') and d < ed:
                try: rows.append((d, float(c)))
                except: pass
    except: return None
    rows.sort(); rows = rows[-(lb + 1):]
    if len(rows) < 6: return None
    rt = [(rows[i][1] / rows[i - 1][1] - 1) * 100 for i in range(1, len(rows)) if rows[i - 1][1] > 0]
    return max(rt) if rt else None


# 사전 로드: KR 외국인 flow (cache by date)
flow_by_date = {}
for f in glob.glob('state/kr_candidate_flow_*.json'):
    try:
        d = json.load(open(f, encoding='utf-8'))
        flow_by_date[d.get('date')] = d.get('records', {})
    except: pass


def prev_foreign(tk, sdate):
    # session_date 이전 가장 가까운 캐시일의 외국인 net
    cand = sorted([dt for dt in flow_by_date if dt and dt < sdate], reverse=True)
    for dt in cand[:3]:
        rec = flow_by_date[dt].get(str(tk).zfill(6))
        if isinstance(rec, dict) and rec.get('flow_values_trusted') and rec.get('foreign') is not None:
            return rec['foreign']
    return None


rows = []
for t in T:
    mx = maxd(t['market'], t['ticker'], t['session_date'])
    rows.append(dict(t, MAX=mx))
nets = [r['pnl_pct_net'] for r in rows]
n = len(nets)
print(f"표본 n={n}\n")

# ① 볼록성 — 분포 왜도·꼬리 집중
print("=== ① 볼록성 인수 기계 (분포가 볼록/우편향인가) ===")
mean = sum(nets) / n
sd = (sum((x - mean) ** 2 for x in nets) / n) ** 0.5
skew = (sum((x - mean) ** 3 for x in nets) / n) / (sd ** 3) if sd else 0
s = sorted(nets)
top5 = s[-max(1, n // 20):]; bot5 = s[:max(1, n // 20)]
wins = [x for x in nets if x > 0]; losses = [x for x in nets if x <= 0]
tail_ratio = (sum(top5) / len(top5)) / abs(sum(bot5) / len(bot5)) if bot5 else 0
total = sum(nets)
top10pct_pnl = sum(s[-max(1, n // 10):])
print(f"  평균 {mean:+.3f}% 표준편차 {sd:.2f}% 왜도(skew) {skew:+.2f} (양수=우편향=볼록)")
print(f"  승 {len(wins)}건 평균 +{sum(wins)/len(wins):.2f}% vs 패 {len(losses)}건 평균 {sum(losses)/len(losses):.2f}%")
print(f"  꼬리비 (상위5% 평균이익 / 하위5% 평균손실) = {tail_ratio:.2f}x")
print(f"  상위10% 트레이드가 전체 P&L의 {100*top10pct_pnl/total if total else float('nan'):.0f}% (total {total:+.1f})" if total else f"  상위10% P&L {top10pct_pnl:+.1f} (total~0)")
print(f"  판정: {'볼록 구조 확인(우편향+꼬리집중)' if skew>0.5 and tail_ratio>1.3 else '볼록성 약함/불명'}\n")

# ③ 캐릭터 — 같은 MFE라도 캐릭터별 실현 net 다른가(출구가 캐릭터별로 달라야 하나)
print("=== ③ 캐릭터(성격)가 출구를 바꿔야 하는가 — MFE 통제 후 캐릭터별 실현차 ===")
# MFE>=3%(녹색 크게 간) 트레이드만: 캐릭터(strategy)별 실현 net (같은 기회, 다른 결과면 캐릭터가 출구 결정)
green = [r for r in rows if r['mfe_pct'] is not None and r['mfe_pct'] >= 3.0]
from collections import defaultdict
by = defaultdict(list)
for r in green:
    by[r['strategy']].append(r['pnl_pct_net'])
print(f"  MFE>=3% 트레이드 {len(green)}건 — 같은 '큰 녹색' 기회를 캐릭터별로:")
for k, v in sorted(by.items(), key=lambda x: -len(x[1])):
    if len(v) >= 5:
        print(f"     {str(k)[:18]:18} n={len(v):>3} 실현 {sum(v)/len(v):+.2f}% (같은MFE인데 캐릭터별 차이)")
print(f"  판정: 캐릭터별 실현 편차 크면 = 출구를 성격별로 갈라야 함(비전 지지)\n")

# ⑤ 스마트머니 vs 개미 — KR: 외국인 net 부호 × MAX × 결과
print("=== ⑤ 스마트머니(외국인) vs 개미(高MAX) 결합 (KR) ===")
kr = [r for r in rows if r['market'] == 'KR' and r['MAX'] is not None]
for r in kr:
    r['fgn'] = prev_foreign(r['ticker'], r['session_date'])
kr_f = [r for r in kr if r['fgn'] is not None]
print(f"  KR 외국인flow 매칭 {len(kr_f)}/{len(kr)}")
if len(kr_f) >= 20:
    for lbl, cond in [
        ("조용한매집 (低MAX<12 + 외인매수)", lambda r: r['MAX'] < 12 and r['fgn'] > 0),
        ("시끄러운추격 (高MAX>=20 + 외인매도/0)", lambda r: r['MAX'] >= 20 and r['fgn'] <= 0),
        ("高MAX + 외인매수 (추격이나 스마트동행)", lambda r: r['MAX'] >= 20 and r['fgn'] > 0),
        ("低MAX + 외인매도", lambda r: r['MAX'] < 12 and r['fgn'] <= 0),
    ]:
        g = [r['pnl_pct_net'] for r in kr_f if cond(r)]
        if g:
            wr = 100 * sum(1 for x in g if x > 0) / len(g)
            print(f"     {lbl:34} n={len(g):>2} avg={sum(g)/len(g):+.2f}% 승률={wr:.0f}%")
    print("  판정: '조용한매집'이 '시끄러운추격'보다 크게 우수면 = 스마트머니 축 실체\n")
else:
    print("  KR 외국인 매칭 표본 부족 — 판정 보류\n")

# ⑥ 안티프래질 — 변동성/국면이 클수록 잘하나 (vol×net)
print("=== ⑥ 안티프래질 (변동성이 클수록 잘하는가) ===")
volrows = []
for r in rows:
    mk = r['market'].lower(); tk = r['ticker']; ed = r['session_date']
    p = os.path.join('data', 'price', mk, f'{mk}_{tk}.csv')
    if not os.path.exists(p): continue
    cl = []
    try:
        for x in csv.DictReader(open(p, encoding='utf-8-sig')):
            d = str(x.get('date', '')).strip(); c = x.get('close')
            if d and c and d < ed:
                try: cl.append(float(c))
                except: pass
    except: continue
    cl = cl[-22:]
    if len(cl) < 10: continue
    rt = [(cl[i]/cl[i-1]-1)*100 for i in range(1, len(cl)) if cl[i-1] > 0]
    if not rt: continue
    m = sum(rt)/len(rt); vol = (sum((x-m)**2 for x in rt)/len(rt))**0.5
    volrows.append((vol, r['pnl_pct_net']))
volrows.sort()
nn = len(volrows)
print(f"  n={nn} — 진입종목 변동성 3분위별 실현 net:")
for lbl, lo, hi in [("低vol", 0, 0.33), ("중vol", 0.33, 0.66), ("高vol", 0.66, 1.0)]:
    seg = volrows[int(lo*nn):int(hi*nn)]
    if seg:
        av = sum(x[1] for x in seg)/len(seg)
        print(f"     {lbl}: n={len(seg)} vol {seg[0][0]:.1f}~{seg[-1][0]:.1f}% avg net={av:+.3f}%")
print("  판정: 高vol이 더 잘하면 안티프래질, 더 못하면 프래질(현 구조는 취약할 것)")
