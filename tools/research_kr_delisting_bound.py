"""상폐 경계 측정 — yfinance가 KR 상폐 종목을 보존하는가, 그 규모는 얼마인가.

유니버스(811)는 2026-09 현재 생존 종목이다. 급락 매수 전략을 8년으로 늘리면
'사서 죽은 종목'이 통째로 빠지므로 좌측 꼬리가 사라진다. 그 규모를 잰다.

DART 종목코드(3,990) 중 유니버스 밖 3,216개에서 표집해 yfinance로 조회하고
① 데이터 종료일이 최근인가(생존) ② 과거에 유니버스 기준(amt20>=10억·종가>=7,110)을
충족한 적이 있는가를 센다.
"""
import json, random, statistics as st, sys, time
from pathlib import Path
import yfinance as yf

R = Path(r'E:\code\claudetrade')
uni = set(json.loads((R / 'data/analysis/kr_fallen_universe.json').read_text(encoding='utf-8')))
dart = json.loads((R / 'data/dart_corp_codes.json').read_text(encoding='utf-8'))
pool = sorted(set(dart) - uni)
random.Random(20260911).shuffle(pool)
SAMPLE = pool[:180]

res = {"alive": 0, "ended": 0, "nodata": 0}
ended_qualified, ended_all, alive_qualified = [], [], []
for i, tk in enumerate(SAMPLE):
    df = None
    for suf in ('.KS', '.KQ'):
        try:
            d = yf.Ticker(tk + suf).history(start='2018-01-01', end='2026-09-12', auto_adjust=True)
        except Exception:
            d = None
        if d is not None and len(d) > 60:
            df = d
            break
    if df is None or not len(df):
        res["nodata"] += 1
        continue
    last = str(df.index[-1])[:10]
    closes = [float(x) for x in df["Close"]]
    vols = [float(x) for x in df["Volume"]]
    amt = [c * v for c, v in zip(closes, vols)]
    # 어느 시점이든 유니버스 기준(20일 평균 거래대금 10억·종가 7,110)을 넘긴 적이 있나
    qualified = any(st.mean(amt[j - 20:j]) >= 1e9 and closes[j] >= 7110
                    for j in range(20, len(closes)))
    if last >= '2026-08-01':
        res["alive"] += 1
        if qualified:
            alive_qualified.append(tk)
    else:
        res["ended"] += 1
        ended_all.append((tk, last))
        if qualified:
            ended_qualified.append((tk, last))
    if (i + 1) % 30 == 0:
        print(f"  {i+1}/{len(SAMPLE)} ...", flush=True)
    time.sleep(0.1)

n = len(SAMPLE)
print(f"\n표본 {n}개 (DART 종목코드 - 유니버스)")
print(f"  생존(최근 데이터 있음): {res['alive']}   그중 유니버스 기준 충족 이력: {len(alive_qualified)}")
print(f"  종료(상폐·거래정지 추정): {res['ended']}   그중 유니버스 기준 충족 이력: {len(ended_qualified)}")
print(f"  yfinance 데이터 없음:     {res['nodata']}")
print(f"\n→ yfinance가 KR 상폐 종목 이력을 보존하는가: "
      f"{'예' if res['ended'] > 0 else '판정불가'} (종료 표본 {res['ended']}개)")
if ended_qualified:
    print(f"\n기준 충족했는데 사라진 종목 {len(ended_qualified)}개 (샘플 15):")
    for tk, last in sorted(ended_qualified, key=lambda x: x[1])[:15]:
        print(f"   {tk}  마지막 {last}")
resolved = res['alive'] + res['ended']
if resolved:
    print(f"\n■ 경계: 조회 성공 {resolved}개 중 종료 {res['ended']}개 = {res['ended']/resolved*100:.1f}%")
    q = len(ended_qualified) + len(alive_qualified)
    if q:
        print(f"■ 유니버스 기준 충족 이력 {q}개 중 사라진 것 {len(ended_qualified)}개 "
              f"= {len(ended_qualified)/q*100:.1f}%  ← 생존편향 규모의 직접 추정")
