# -*- coding: utf-8 -*-
"""KR WS 관측 목록 생산자 (2026-09-08 N2) — schtask claudetrade_ws_observe_list 주중 08:40.

전일 ≤−5% · 거래대금 ≥20억 · 가격 ≥1,000원 · 제약·바이오 제외(sector_map) 종목을 전일 거래대금 큰순 20개까지 state/ws_observe_kr.json에 쓴다.
봇 _start_ws_for_market(KR)이 08:53에 읽어 남는 자리(41 − 매매 구독)만큼 관측 구독한다. 오늘 날짜가 아니면 봇이 무시한다.
사용: python tools/ws_observe_list.py [--max 20]
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KR_DIR = ROOT / "data" / "price" / "kr"
OUT = ROOT / "state" / "ws_observe_kr.json"
SECTOR = ROOT / "data" / "sector_map.json"


def main() -> int:
    args = sys.argv[1:]
    mx = int(args[args.index("--max") + 1]) if "--max" in args else 20
    today = date.today().isoformat()
    bio = set()
    try:
        sm = json.loads(SECTOR.read_text(encoding="utf-8"))
        bio = {t for t, v in (sm.get("KR") or {}).items() if (v.get("sector") or "") == "제약·바이오"}
    except (OSError, ValueError):
        pass
    cands: list[tuple[float, str, float]] = []
    for p in KR_DIR.glob("kr_*.csv"):
        rows = [r for r in csv.reader(p.open(encoding="utf-8-sig")) if r and r[0][:2] == "20" and r[0] < today]
        if len(rows) < 2:
            continue
        try:
            c, v, pc = float(rows[-1][4]), float(rows[-1][5]), float(rows[-2][4])
        except ValueError:
            continue
        tk = p.stem[3:]
        if c < 1000 or pc <= 0 or tk in bio:
            continue
        chg = (c / pc - 1) * 100; dv = c * v
        if chg <= -5.0 and dv >= 2e9:
            cands.append((dv, tk, round(chg, 2)))
    cands.sort(reverse=True)
    picks = cands[:mx]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"date": today, "generated_at": datetime.now().isoformat(timespec="seconds"), "n_candidates": len(cands),
                               "tickers": [t for _, t, _ in picks], "detail": [{"ticker": t, "dvol": round(dv), "chg": ch} for dv, t, ch in picks]},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[WS-OBS] {today} 후보 {len(cands)} → 관측 {len(picks)}: {[t for _, t, _ in picks]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
