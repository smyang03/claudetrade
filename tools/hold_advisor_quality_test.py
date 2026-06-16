"""hold advisor 판단 품질 측정 (로컬 전용, API 재호출 없음).

logs/raw_calls/*hold_advisor_*.json 의 과거 판단(HOLD/SELL)을 추출해, 같은 종목·세션의
실제 최종 청산 결과(decisions.db v2_learning_performance)와 대조한다. "hold advisor가
오를 종목은 HOLD하고 떨굴 종목은 SELL했는가(변별)"를 정량화한다.

정답 정의 (review 시점 pnl → 그 세션 최종 청산 pnl):
- HOLD 적중 = 최종 pnl >= review 시점 pnl (들고 있어 이득/유지)
- SELL 적중 = review 시점 pnl >= 최종 pnl (팔아서 추가 손실 회피)
이는 방향 지표다(같은 세션 후속 review가 결과를 바꿀 수 있어 사후 근사).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
from collections import defaultdict, Counter
from datetime import datetime
from statistics import mean, median

RAW_DIR = "logs/raw_calls"
ML_DB = "data/ml/decisions.db"

_RX_TICKER = re.compile(r"종목:\s*(\S+)\s*\(")
_RX_PNL = re.compile(r"수익률:\s*([-+]?[\d.]+)%")
_RX_PRICE = re.compile(r"현재가:\s*\$?([\d,]+\.?\d*)")


def _parse_calls(perspectives: set[str]) -> list[dict]:
    recs = []
    for f in glob.glob(os.path.join(RAW_DIR, "*hold_advisor_*.json")):
        label = os.path.basename(f)
        persp = None
        for p in ("bull", "bear", "neutral", "triage", "challenge"):
            if f"hold_advisor_{p}" in label:
                persp = p
                break
        if persp not in perspectives:
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        prompt = d.get("prompt", "") or ""
        parsed = d.get("parsed") or {}
        action = str(parsed.get("action", "") or "").upper()
        if action not in ("HOLD", "SELL"):
            continue
        mt = _RX_TICKER.search(prompt)
        mp = _RX_PNL.search(prompt)
        if not mt or not mp:
            continue
        recs.append({
            "date": d.get("date"),
            "market": d.get("market"),
            "ticker": mt.group(1),
            "review_pnl": float(mp.group(1)),
            "action": action,
            "confidence": parsed.get("confidence"),
            "hold_mode": parsed.get("hold_mode"),
            "stage": (d.get("extra") or {}).get("decision_stage"),
            "perspective": persp,
        })
    return recs


def _load_outcomes() -> dict:
    """(market, ticker, session_date) -> 최종 청산 pnl_pct (그날 마지막 청산)."""
    out = {}
    con = sqlite3.connect(ML_DB)
    con.row_factory = sqlite3.Row
    for r in con.execute(
        """SELECT market,ticker,session_date,pnl_pct,close_reason,closed_at
           FROM v2_learning_performance
           WHERE closed=1 AND runtime_mode='live' AND pnl_pct IS NOT NULL"""):
        key = (r["market"], str(r["ticker"]).upper(), str(r["session_date"])[:10])
        # 같은 키 여러건이면 마지막 청산 사용
        prev = out.get(key)
        if prev is None or str(r["closed_at"]) > prev["closed_at"]:
            out[key] = {"pnl": r["pnl_pct"], "reason": r["close_reason"], "closed_at": str(r["closed_at"])}
    con.close()
    return out


def _hit(action: str, review_pnl: float, final_pnl: float) -> bool:
    if action == "HOLD":
        return final_pnl >= review_pnl
    return review_pnl >= final_pnl  # SELL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perspectives", default="bull,bear,neutral")
    ap.add_argument("--market", default="")
    args = ap.parse_args()
    persp = set(args.perspectives.split(","))

    recs = _parse_calls(persp)
    outcomes = _load_outcomes()

    matched = []
    for r in recs:
        if args.market and r["market"] != args.market:
            continue
        key = (r["market"], r["ticker"].upper(), str(r["date"])[:10])
        oc = outcomes.get(key)
        if oc is None:
            continue
        r["final_pnl"] = oc["pnl"]
        r["final_reason"] = oc["reason"]
        r["hit"] = _hit(r["action"], r["review_pnl"], oc["pnl"])
        r["delta"] = oc["pnl"] - r["review_pnl"]  # review 이후 변화
        matched.append(r)

    print(f"hold_advisor vote 파싱={len(recs)}  결과매칭={len(matched)}")
    if not matched:
        print("매칭 0 — session_date/ticker 정합 확인 필요")
        return

    def report(rows, title):
        if not rows:
            return
        n = len(rows)
        hits = sum(1 for x in rows if x["hit"])
        holds = [x for x in rows if x["action"] == "HOLD"]
        sells = [x for x in rows if x["action"] == "SELL"]
        print(f"\n=== {title} (n={n}) ===")
        print(f"  전체 적중률: {hits/n*100:.0f}%")
        if holds:
            hh = sum(1 for x in holds if x["hit"]) / len(holds) * 100
            print(f"  HOLD {len(holds)}건 적중 {hh:.0f}% | review후 평균변화 {mean(x['delta'] for x in holds):+.2f}%p")
        if sells:
            sh = sum(1 for x in sells if x["hit"]) / len(sells) * 100
            print(f"  SELL {len(sells)}건 적중 {sh:.0f}% | review후 평균변화 {mean(x['delta'] for x in sells):+.2f}%p")

    report(matched, "전체")
    for mkt in ("US", "KR"):
        report([x for x in matched if x["market"] == mkt], f"시장={mkt}")
    # 관점별
    for p in sorted(set(x["perspective"] for x in matched)):
        report([x for x in matched if x["perspective"] == p], f"관점={p}")
    # stage별
    print("\n=== decision_stage별 적중률 ===")
    by_stage = defaultdict(list)
    for x in matched:
        by_stage[x["stage"] or "?"].append(x)
    for s, rows in sorted(by_stage.items(), key=lambda kv: -len(kv[1])):
        hits = sum(1 for x in rows if x["hit"]) / len(rows) * 100
        print(f"  {str(s)[:30]:<32} n={len(rows):>4} 적중 {hits:.0f}%")
    # 변별력: HOLD한 것의 결과 vs SELL한 것의 결과
    print("\n=== 변별력 (HOLD vs SELL 후 실제 변화) ===")
    h = [x["delta"] for x in matched if x["action"] == "HOLD"]
    s = [x["delta"] for x in matched if x["action"] == "SELL"]
    if h and s:
        print(f"  HOLD 후 평균변화 {mean(h):+.2f}%p (n={len(h)}) | SELL 후 평균변화 {mean(s):+.2f}%p (n={len(s)})")
        print(f"  → 변별 양호 조건: HOLD 후 변화 > SELL 후 변화 (HOLD가 오를걸 고름)")


if __name__ == "__main__":
    main()
