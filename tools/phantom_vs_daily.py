#!/usr/bin/env python3
"""유령 vs 일봉 괴리 원장 — 관측 체인 ⑩ (2026-09-03, 설계 정본 §2).

같은 (arm, 장부 세션, 종목)의 유령 청산(실시간 호가·실전 출구 로직)과 일봉 장부 정산(virtual_books, 시가 진입·일봉
규약)을 대조해 진입가 차·청산 사유 차·net 차·보유일 차를 적는다. 이 괴리가 곧 "실행 비용/출구 타이밍" 계수다.
키 매핑: 유령은 entry_session_date, 장부는 session_date(slowus/lpus/KR은 신호일) → 유령 행의 book_session_date로 조인.
원장: data/shadow/phantom_vs_daily.jsonl — (arm, book_session_date, ticker) 멱등. 읽기 전용(DB는 ro).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHANTOM = ROOT / "data" / "shadow" / "phantom_ledger.jsonl"
BOOK = ROOT / "data" / "shadow" / "virtual_books.db"
OUT = ROOT / "data" / "shadow" / "phantom_vs_daily.jsonl"


def main() -> int:
    if not PHANTOM.exists() or not BOOK.exists():
        print("[phantom_vs_daily] 원장/장부 없음 — 스킵")
        return 0
    closes = []
    for line in PHANTOM.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") == "CLOSE":
            closes.append(r)
    done: set[tuple] = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                done.add((r["arm"], r["book_session_date"], r["ticker"]))
            except (ValueError, KeyError):
                continue
    con = sqlite3.connect(f"file:{BOOK}?mode=ro", uri=True, timeout=10)
    added = 0
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with OUT.open("a", encoding="utf-8") as fh:
            for c in closes:
                arm = str(c.get("arm") or "us_live_dvol")
                bsd = str(c.get("book_session_date") or c.get("session_date"))
                tk = str(c.get("ticker")).upper()
                key = (arm, bsd, tk)
                if key in done:
                    continue
                row = con.execute(
                    "SELECT status, entry_price, net_pct, exit_reason FROM trades "
                    "WHERE strategy_id=? AND session_date=? AND ticker=?", (arm, bsd, tk)).fetchone()
                if row is None or row[0] != "CLOSED":
                    continue  # 장부 미정산 — 다음 실행에서 재시도
                status, b_entry, b_net, b_reason = row
                out = {"ts": stamp, "arm": arm, "book_session_date": bsd, "ticker": tk,
                       "phantom_entry": c.get("entry_usd"), "book_entry": b_entry,
                       "entry_diff_pct": (round((float(c["entry_usd"]) / float(b_entry) - 1.0) * 100.0, 3)
                                          if c.get("entry_usd") and b_entry else None),
                       "phantom_reason": c.get("reason"), "book_reason": b_reason,
                       "reason_match": (str(c.get("reason") or "").lower() == str(b_reason or "").lower()),
                       "phantom_gross_pct": c.get("gross_pct"), "book_net_pct": b_net,
                       "net_diff_pct": (round(float(c["gross_pct"]) - float(b_net), 3)
                                        if c.get("gross_pct") is not None and b_net is not None else None),
                       "phantom_held_days": c.get("held_days"), "retro": bool(c.get("retro"))}
                fh.write(json.dumps(out, ensure_ascii=False) + "\n")
                done.add(key)
                added += 1
    finally:
        con.close()
    print(f"[phantom_vs_daily] 신규 대조 {added}건 (유령 청산 {len(closes)}건, 누적 {len(done)}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
