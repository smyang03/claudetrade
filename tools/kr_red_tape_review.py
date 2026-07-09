#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KR red-tape 재측정 (2026-07-09, 운영자 승인) — 정정된 net 기준 read-only.

배경: 7/3 판정 "KR red-tape 부적합"의 근거는 ①KR net 양수(흑자시장) ②red 코호트 +0.17.
그 전제가 무너짐: fee 오라벨 43건 교정+NULL 백필 후 KR 통산 −34.3 (7/9 확정).
질문: 정정된 net으로 다시 갈라도 KR red-tape 코호트가 양수인가?

방법(no-lookahead): 진입 체결시각까지의 KODEX200(069500) 개장 대비 %로 tape 판정.
net = v2_learning_performance.pnl_pct_net (교정·백필 반영본). yfinance 5m 60일 한계로
2026-05-12 이후 체결만(4월 22건 제외 — 정직 표기). 임계 스윕 −0.1/−0.3/−0.5.
"""
import sqlite3
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"
KST = timezone(timedelta(hours=9))
SINCE = "2026-05-12"
THRESHOLDS = (-0.1, -0.3, -0.5)


def main() -> int:
    import yfinance as yf

    con = sqlite3.connect(f"file:{ML_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    trades = [dict(r) for r in con.execute(
        """SELECT DISTINCT v2_decision_id, session_date, ticker, filled_at,
                  pnl_pct_net, pnl_pct, close_reason
           FROM v2_learning_performance
           WHERE closed=1 AND filled=1 AND runtime_mode='live' AND market='KR'
             AND pnl_pct IS NOT NULL AND filled_at IS NOT NULL
             AND session_date >= ?""", (SINCE,)
    )]
    con.close()
    print(f"KR closed({SINCE}+, filled_at 보유): {len(trades)}건 (4월 등 이전분은 5m 한계로 제외)")

    idx = yf.download("069500.KS", start=SINCE, end=None, interval="5m",
                      progress=False, auto_adjust=False)
    if idx is None or idx.empty:
        print("지수 5m 조회 실패")
        return 1
    if hasattr(idx.columns, "get_level_values"):
        idx.columns = idx.columns.get_level_values(0)
    # 세션별 개장가
    opens: dict[str, float] = {}
    for ts, row in idx.iterrows():
        d = ts.astimezone(KST).strftime("%Y-%m-%d")
        if d not in opens:
            opens[d] = float(row["Open"])

    rows = []
    skipped = 0
    for t in trades:
        try:
            fa = datetime.fromisoformat(str(t["filled_at"]).replace("Z", "+00:00"))
            fa_kst = fa.astimezone(KST) if fa.tzinfo else fa.replace(tzinfo=KST)
        except ValueError:
            skipped += 1
            continue
        day = fa_kst.strftime("%Y-%m-%d")
        if day not in opens:
            skipped += 1
            continue
        upto = idx[idx.index <= fa_kst.astimezone(timezone.utc)]
        if upto.empty:
            skipped += 1
            continue
        px_at_entry = float(upto.iloc[-1]["Close"])
        tape = (px_at_entry / opens[day] - 1.0) * 100.0
        net = t["pnl_pct_net"] if t["pnl_pct_net"] is not None else (float(t["pnl_pct"]) - 0.21)
        rows.append({"mon": day[:7], "tape": tape, "net": float(net), "ticker": t["ticker"]})

    print(f"tape 산출: {len(rows)}건 / 스킵 {skipped}")
    if not rows:
        return 1

    import statistics as st
    for thr in THRESHOLDS:
        red = [r for r in rows if r["tape"] <= thr]
        green = [r for r in rows if r["tape"] > thr]
        def s(x):
            if not x:
                return "n=0"
            v = [r["net"] for r in x]
            return f"n={len(v)} 합={sum(v):+.2f} 중앙={st.median(v):+.2f} 평균={st.mean(v):+.2f}"
        print(f"\n== 임계 {thr}: red(진입시 지수하락) {s(red)}")
        print(f"           green {s(green)}")
        bym = defaultdict(list)
        for r in red:
            bym[r["mon"]].append(r["net"])
        for m in sorted(bym):
            print(f"   red {m}: n={len(bym[m])} 합={sum(bym[m]):+.2f}")
    print("\n판정 메모: 7/3 기준 'red 코호트 +0.17 양수=부적합' — 위 값이 음수로 뒤집히면 재평가 정당.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
