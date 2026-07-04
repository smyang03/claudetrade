#!/usr/bin/env python3
"""neg_context 블록 종가경로 다국면 누적 리뷰 (read-only).

질문: US에서 fresh_selection_suspended_negative_context(=pullback_wait_blocked_negative_context)로
막은 종목이, 블록 시점 → 세션 고점 → 종가로 어떻게 가나? regime(consensus_mode)별로.
- 강세장에서 블록종목이 종가까지 유지/상승 = 게이트 기회손실(완화 후보 신호)
- 약세장에서 블록종목이 종가까지 하락 = 게이트 손실회피(정당)

매매 무변경. audit(이미 기록) + 분봉(이미 수집)만 읽는다. 세션 쌓일수록 표본 누적.
판정 함정: 블록후 고점은 추격가라 다 못먹음 → 핵심지표는 '블록→종가'(보유 실현 근사)지 '블록→고점' 아님.
"""
from __future__ import annotations
import csv
import os
import sqlite3
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "data" / "audit" / "candidate_audit.db"
MIN_DIR = ROOT / "data" / "price" / "minute" / "us"

STRONG = {"MODERATE_BULL", "MILD_BULL"}
WEAK = {"MILD_BEAR", "CAUTIOUS", "CAUTIOUS_BEAR"}


def load_bars(ticker: str):
    p = MIN_DIR / f"us_{ticker}.csv"
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                out.append((r["ts"], float(r["high"]), float(r["low"]), float(r["close"])))
            except (KeyError, ValueError, TypeError):
                continue
    return out


def session_path(bars, session_date, block_ts):
    """블록 시점 이후 ~ 세션 종료(다음날 06시 전)까지. 블록ref=block_ts 이하 마지막 close."""
    # 세션 범위: session_date 또는 (다음날 & 06시 전)
    sess = [b for b in bars if b[0][:10] == session_date or (b[0][:10] > session_date and b[0][11:13] < "06")]
    if not sess:
        return None
    ref = None
    for ts, h, l, cl in sess:
        if ts <= block_ts:
            ref = cl
    after = [b for b in sess if b[0] > block_ts]
    if ref is None or ref <= 0 or not after:
        return None
    high_after = max(b[1] for b in after)
    close = after[-1][3]
    return ref, (high_after / ref - 1) * 100, (close / ref - 1) * 100


def main() -> int:
    c = sqlite3.connect(f"file:{AUDIT}?mode=ro", uri=True, timeout=10)
    c.execute("PRAGMA busy_timeout=5000")
    q = """SELECT session_date, ticker, consensus_mode, MIN(known_at)
           FROM audit_candidate_rows
           WHERE market='US'
             AND (route_reason LIKE '%negative_context%'
                  OR route_runtime_gate_reason LIKE '%negative_pullback%'
                  OR claude_reason LIKE '%negative_context%')
             AND known_at IS NOT NULL
           GROUP BY session_date, ticker, consensus_mode
           ORDER BY session_date"""
    blocks = list(c.execute(q))
    c.close()
    print(f"neg_context 블록 (US, 세션-종목) {len(blocks)}건")

    by_regime = defaultdict(list)   # regime -> [(close_ret, high_ret)]
    by_session = defaultdict(list)
    missing = 0
    for sd, tk, mode, kat in blocks:
        bars = load_bars(str(tk))
        if not bars:
            missing += 1
            continue
        res = session_path(bars, sd, kat)
        if res is None:
            missing += 1
            continue
        _, high_ret, close_ret = res
        by_regime[mode or "NA"].append((close_ret, high_ret))
        by_session[sd].append(close_ret)

    print(f"분봉 경로 복원 {sum(len(v) for v in by_regime.values())}건 / 결측 {missing}\n")

    def block(title, modes):
        rows = [v for m in modes for v in by_regime.get(m, [])]
        if not rows:
            print(f"  {title:16} n=0")
            return
        cl = [r[0] for r in rows]
        held = 100 * sum(1 for x in cl if x > 0.3) / len(cl)
        fell = 100 * sum(1 for x in cl if x < -0.3) / len(cl)
        print(f"  {title:16} n={len(rows):3} | 블록→종가 평균 {st.mean(cl):+.2f}% 중앙 {st.median(cl):+.2f}% | 유지 {held:.0f}% 하락 {fell:.0f}%")

    print("=== regime 묶음별 (핵심지표=블록→종가) ===")
    block("강세(BULL)", STRONG)
    block("약세/주의(BEAR)", WEAK)
    block("NEUTRAL", {"NEUTRAL"})
    print()
    print("=== regime 상세 ===")
    for mode in sorted(by_regime, key=lambda m: -len(by_regime[m])):
        block(mode, {mode})
    print()
    print("=== 세션별 블록→종가 평균 ===")
    for sd in sorted(by_session):
        v = by_session[sd]
        print(f"  {sd}: n={len(v):2} 평균 {st.mean(v):+.2f}%")
    print("\n주: 블록→종가>0 = 안 산 게 기회손실(강세장서 누적되면 완화후보). <0 = 손실회피(정당).")
    print("    블록→고점은 추격가라 실현불가 — 종가가 보유 실현 근사. 분봉 결측 세션은 누적되며 채워짐.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
