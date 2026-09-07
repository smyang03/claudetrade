#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""회피 필터 반사실 — 기존 레퍼런스·후보 arm에 "필터가 있었다면" 성적을 같이 기록한다 (2026-09-07, read-only).

필터(탐색 분해에서 유의했던 회피 칸, 사전등록 §4):
- kr_bio: KR 종목 섹터 == 제약·바이오 (급락/급등 다음날 모두 음수)
- kr_rise_prev: KR 전일 ≥+5% (급등 다음날) — 급락 arm엔 해당 없음, 돌파·거래량 풀에 적용
- hi_break_pullback: 돌파 풀에서 20일 고점 대비 −20~−10%
계약은 바꾸지 않는다. 각 arm의 CLOSED 행을 '제외됐을 행'과 '남는 행'으로 갈라 n·평균·승률·세션 t를 낸다.
출력: data/analysis/avoid_filter_counterfactual.json + 콘솔 표
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "shadow" / "virtual_books.db"
SECTOR_MAP = ROOT / "data" / "sector_map.json"
OUT = ROOT / "data" / "analysis" / "avoid_filter_counterfactual.json"


def cell(rows):
    nets = [r[0] for r in rows]; sess = [r[1] for r in rows]
    if not nets:
        return {"n": 0}
    by = defaultdict(list)
    for n, s in zip(nets, sess):
        by[s].append(n)
    means = [st.mean(v) for v in by.values()]
    t = round(st.mean(means) / (st.stdev(means) / math.sqrt(len(means))), 2) if len(means) >= 5 and st.stdev(means) > 0 else None
    return {"n": len(nets), "sessions": len(by), "mean": round(st.mean(nets), 3), "win_pct": round(100.0 * sum(1 for x in nets if x > 0) / len(nets), 1), "t": t}


def main() -> int:
    sm = json.loads(SECTOR_MAP.read_text(encoding="utf-8")) if SECTOR_MAP.exists() else {"KR": {}, "US": {}}
    kr_sector = {t: str(v.get("sector") or "") for t, v in (sm.get("KR") or {}).items()}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    arms = [r[0] for r in con.execute("SELECT id FROM strategies WHERE id LIKE 'kr_%' OR id LIKE 'c_%' OR id LIKE 'xkr_%' ORDER BY rowid")]
    res = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "arms": {}}
    print(f"{'arm':22s} {'필터':16s} {'남는 행':>40s} | {'제외됐을 행':>40s}")
    for sid in arms:
        rows = con.execute("SELECT session_date, ticker, net_pct, backfill, meta FROM trades WHERE strategy_id=? AND status='CLOSED'", (sid,)).fetchall()
        if not rows:
            continue
        recs = []
        for sd, tk, net, bf, meta in rows:
            try:
                m = json.loads(meta) if meta else {}
            except ValueError:
                m = {}
            f = m.get("feat") or {}
            recs.append({"s": sd, "t": tk, "net": float(net), "bf": int(bf or 0), "sector": kr_sector.get(str(tk), ""),
                         "chg": f.get("chg"), "from_high20": f.get("from_high20"), "pool": m.get("pool")})
        filters = {"kr_bio": lambda r: r["sector"] == "제약·바이오"}
        if sid.endswith("breakout") or sid.endswith("volspike") or sid.endswith("rise5"):
            filters["kr_rise_prev"] = lambda r: r["chg"] is not None and r["chg"] >= 5.0
        if sid.endswith("breakout"):
            filters["hi_break_pullback"] = lambda r: r["from_high20"] is not None and -20 <= r["from_high20"] < -10
        res["arms"][sid] = {}
        for name, fn in filters.items():
            keep = [(r["net"], r["s"]) for r in recs if not fn(r)]
            drop = [(r["net"], r["s"]) for r in recs if fn(r)]
            fk = [(r["net"], r["s"]) for r in recs if not fn(r) and not r["bf"]]
            fd = [(r["net"], r["s"]) for r in recs if fn(r) and not r["bf"]]
            res["arms"][sid][name] = {"keep": cell(keep), "drop": cell(drop), "forward_keep": cell(fk), "forward_drop": cell(fd)}
            k, d = cell(keep), cell(drop)
            print(f"{sid:22s} {name:16s} n={k['n']:5d} {k.get('mean', 0):+6.2f}% 승 {k.get('win_pct', 0):4.1f} t {k.get('t')} | n={d['n']:5d} {d.get('mean', 0):+6.2f}% 승 {d.get('win_pct', 0):4.1f} t {d.get('t')}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
