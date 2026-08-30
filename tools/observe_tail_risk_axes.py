#!/usr/bin/env python3
"""꼬리 위험 축 관측 원장 — 라이브 영향 0, 판정 재료 축적 전용 (2026-08-30).

배경: research_tail_risk_axes(08-30)가 꼬리 기준으로 10축을 재검해 6축이
3기준을 통과했다(전부 밴드 벤치마크 3.77%p보다 큼). 상관을 걷어내면 실질
2계열이고, 그중 변동성 계열은 라이브(MAX>=8)와 **역방향**이다. 다만 클러스터
t가 -1.50/+0.53로 검정력이 없고 08-20 스윕의 t=4.58과 충돌해 판정을 못 냈다.

판정에 필요한 것은 표본이고, 현행 계약(밴드+MAX) 실거래 정산은 **0건**이다.
30건이 찰 때까지 기다리기만 하면 그 시점에도 검정 재료가 없다. 이 스크립트는
기다리는 동안 재료를 쌓는다.

**왜 사후 계산이 아니라 박제인가**: 6축은 전부 신호일 일봉에서 계산되므로
사후에도 산출은 가능하다. 그러나 (a) 가격 CSV가 분할·배당으로 소급 조정되면
과거 축 값이 바뀌고, (b) 진입 시점 값이 원장에 없으면 no-lookahead를 사후에
증명할 수 없다. 계측 이력 원장(08-19)과 같은 이유다.

**라이브 영향**: 없다. shadow DB와 가격 CSV를 읽고 별도 jsonl에만 append한다.
주문·설정·라이브 DB를 건드리지 않는다. 어떤 진입 결정도 이 원장을 참조하지
않는다 — 참조하게 하려면 사전등록 승격 절차를 거쳐야 한다.

사용:
    python tools/observe_tail_risk_axes.py            # 최근 미기록분 추가
    python tools/observe_tail_risk_axes.py --backfill # 전체 소급 백필
    python tools/observe_tail_risk_axes.py --summary  # 원장 현황만 출력
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_early_exit_no_bump import BAND, SIGNALS_DB  # noqa: E402
from research_tail_risk_axes import features  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "shadow" / "tail_risk_axes.jsonl"
SCHEMA = "tail_risk_axes_v1"

# 08-30 검정에서 3기준을 통과한 6축 + 대조군. 통과 여부와 무관하게 전부 기록해
# 사후에 축을 고르지 않는다(골대 이동 방지).
AXES = ["등락", "IBS", "갭", "장중흐름", "거래량비", "MAX20",
        "거래대금", "할인깊이", "ATR14", "모델확률"]
PASSED = ["등락", "IBS", "장중흐름", "MAX20", "할인깊이", "ATR14"]


def existing_keys() -> set[str]:
    if not LEDGER.exists():
        return set()
    keys = set()
    with LEDGER.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                keys.add(json.loads(line)["_key"])
            except (json.JSONDecodeError, KeyError):
                continue
    return keys


def load_rows(backfill: bool) -> list[dict]:
    if not SIGNALS_DB.exists():
        print(f"[ERROR] shadow DB 없음: {SIGNALS_DB}")
        return []
    con = sqlite3.connect(f"file:{SIGNALS_DB}?mode=ro", uri=True, timeout=10)
    try:
        where = "" if backfill else "WHERE signal_date >= date('now','-30 day')"
        rows = con.execute(
            f"""SELECT signal_date, ticker, rank, probability, candidate_source,
                       status, handoff_order_no, entry_price
                FROM signals {where} ORDER BY signal_date"""
        ).fetchall()
    finally:
        con.close()
    return [{"signal_date": str(r[0]), "ticker": str(r[1]).upper(), "rank": r[2],
             "probability": r[3], "source": str(r[4] or ""), "status": str(r[5] or ""),
             "entered": bool(r[6]), "entry_price": r[7]} for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="전체 소급 백필")
    ap.add_argument("--summary", action="store_true", help="원장 현황만 출력")
    args = ap.parse_args()

    if args.summary:
        if not LEDGER.exists():
            print("원장 없음")
            return 0
        rows = [json.loads(l) for l in LEDGER.open(encoding="utf-8")]
        entered = sum(1 for r in rows if r.get("entered"))
        dates = sorted({r["signal_date"] for r in rows})
        print(f"원장 {len(rows)}행 | 세션 {len(dates)}일 ({dates[0]} ~ {dates[-1]}) | "
              f"진입 {entered}건")
        band = sum(1 for r in rows if r.get("in_band"))
        print(f"밴드 안 {band}행 ({100*band/len(rows):.0f}%)")
        for ax in PASSED:
            have = sum(1 for r in rows if r.get("axes", {}).get(ax) is not None)
            print(f"  {ax:8s} 산출 {have}/{len(rows)}행")
        return 0

    seen = existing_keys()
    rows = load_rows(args.backfill)
    added = skipped = nofeat = 0
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        for r in rows:
            key = f"{r['ticker']}|{r['signal_date']}"
            if key in seen:
                skipped += 1
                continue
            feat = features({"ticker": r["ticker"], "signal_date": r["signal_date"],
                             "probability": r["probability"]})
            if not feat:
                nofeat += 1
                continue
            dvol = feat.get("거래대금")
            rec = {
                "_key": key, "_schema": SCHEMA,
                "signal_date": r["signal_date"], "ticker": r["ticker"],
                "rank": r["rank"], "source": r["source"], "status": r["status"],
                "entered": r["entered"], "entry_price": r["entry_price"],
                "in_band": bool(dvol is not None and BAND[0] <= dvol <= BAND[1]),
                "axes": {a: feat.get(a) for a in AXES},
                "passed_axes": PASSED,
                "note": "관측 전용 — 진입 결정에 참조 금지(사전등록 승격 전)",
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            seen.add(key)
            added += 1
    print(f"[관측] 추가 {added}행 | 기존 스킵 {skipped} | 피처 산출 실패 {nofeat} "
          f"(20일 이력 부족)")
    print(f"  원장: {LEDGER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
