from __future__ import annotations

"""judge가 BUY_READY(즉시매수)를 낸 건을 '그때 샀다면'으로 반사실 검증한다.

2026-07-22: 즉시매수 4건이 전부 버그(regime 누락 2 + 현재가 미조회 2)로 죽고 있었다.
버그를 고친 뒤, 그 4건이 실제로 벌었을지 확인해 수정의 가치를 검증했다.

최초 실행 결과(US 4건):
  HUT  1h +6.85% / 2h +8.44% / 4h +8.07%  → TARGET 도달(+7.50%)
  WDC  1h -0.13% / 2h +0.91% / 4h +0.43%
  AMAT 1h -0.39% / 2h +0.80% / 4h +0.12%
  WDC  1h +0.46% / 2h +1.41% / 4h +0.49%
  평균 2h +2.89%, 손절 도달 0건.

★대비: 눌림존 규칙이 거부한 건을 샀다면 2h -1.075%(tools/pullback_rule_counterfactual.py).
즉 "무작정 추격"과 "judge가 선별한 즉시매수"는 정반대다. 진입 확대의 답은
눌림 완화가 아니라 즉시매수다.

한계: 표본 4건 · gross(비용 미반영) · yfinance 5일 창(최근 건만 조회 가능).

  python tools/buy_ready_counterfactual.py
"""

import json, glob, os, sys
from datetime import datetime, timedelta
sys.path.insert(0, r"E:\code\claudetrade")
import yfinance as yf

# 오늘 judge가 BUY_READY를 낸 건들 추출
picks=[]
for f in sorted(glob.glob("logs/raw_calls/*single_symbol_judge*")):
    if not os.path.basename(f).startswith(("20260721_US","20260722_US")): continue
    j=json.load(open(f,encoding="utf-8"))
    raw=j.get("raw_response","")
    if '"action":"BUY_READY"' not in raw.replace(" ",""): continue
    def g(k):
        try: return raw.split(f'"{k}":')[1].split(",")[0].strip().strip('"')
        except Exception: return None
    tk = raw.split('"ticker":"')[1].split('"')[0] if '"ticker":"' in raw else "?"
    picks.append({"ts": j.get("timestamp"), "ticker": tk,
                  "ref": g("reference_price"), "target": g("sell_target"), "stop": g("stop_loss")})
print(f"judge가 BUY_READY를 낸 건: {len(picks)}건\n")

for p in picks:
    tk=p["ticker"]
    try:
        df=yf.download(tk, period="5d", interval="5m", progress=False, auto_adjust=False)
        c=df["Close"].iloc[:,0] if hasattr(df["Close"],"columns") else df["Close"]
        idx=df.index
        idx = idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")
    except Exception as e:
        print(f"  {tk}: 데이터 실패"); continue
    try:
        dtu = datetime.fromisoformat(str(p["ts"])) - timedelta(hours=9)
    except Exception:
        continue
    pos=[i for i,t in enumerate(idx) if t.to_pydatetime().replace(tzinfo=None) <= dtu]
    if not pos:
        print(f"  {tk}: 시각 매칭 실패"); continue
    t0=pos[-1]
    entry=float(p["ref"] or 0) or float(c.iloc[t0])
    tgt=float(p["target"] or 0); stp=float(p["stop"] or 0)
    outs=[]
    for h,lab in ((12,"1h"),(24,"2h"),(48,"4h"),(78,"당일마감")):
        if t0+h < len(c):
            outs.append(f"{lab} {(float(c.iloc[t0+h])/entry-1)*100:+.2f}%")
    # 목표/손절 도달 여부
    hit="-"
    for i in range(t0+1, min(t0+78, len(c))):
        px=float(c.iloc[i])
        if tgt and px>=tgt: hit=f"TARGET({(tgt/entry-1)*100:+.2f}%)"; break
        if stp and px<=stp: hit=f"STOP({(stp/entry-1)*100:+.2f}%)"; break
    print(f"  {p['ts'][11:19]} {tk:6s} entry={entry:.2f} target={tgt} stop={stp}")
    print(f"     이후: {' / '.join(outs)}   먼저도달: {hit}")
