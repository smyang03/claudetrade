#!/usr/bin/env python3
"""유령 vs 일봉 괴리 원장 — 관측 체인 ⑩ (2026-09-03, 설계 정본 §2 · 09-06 비교 기준 수리).

같은 (arm, 장부 세션, 종목)의 유령 청산(실시간 호가·실전 출구 로직)과 일봉 장부 정산(virtual_books, 시가 진입·일봉
규약)을 대조해 진입가 차·청산 사유 차·net 차·보유일 차를 적는다. 이 괴리가 곧 "실행 비용/출구 타이밍" 계수다.
키 매핑: 유령은 entry_session_date, 장부는 session_date(slowus/lpus/KR은 신호일) → 유령 행의 book_session_date로 조인.
원장: data/shadow/phantom_vs_daily.jsonl — (arm, book_session_date, ticker) 멱등. 읽기 전용(DB는 ro).

09-06 수리(Codex 리뷰 지적): ① 유령 gross에서 장부 net을 빼고 있었다 → 유령에도 장부와 같은 왕복 비용(FEE_US/FEE_KR)을
차감한 phantom_net_pct로 비교한다. ② 청산 사유가 유령은 실전 코드(strategy_fixed_take_profit …), 장부는 TP/SL/BE/D_MAT라
같은 익절도 불일치로 집계됐다 → REASON_MAP으로 정규화한 뒤 비교한다. `--rebuild`로 기존 원장을 새 기준으로 재산출한다.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
PHANTOM = ROOT / "data" / "shadow" / "phantom_ledger.jsonl"
BOOK = ROOT / "data" / "shadow" / "virtual_books.db"
OUT = ROOT / "data" / "shadow" / "phantom_vs_daily.jsonl"

# 실전 출구 코드 → 장부 정산 코드 (virtual_books.contract_exit_v2: TP / SL / BE / D_MAT)
REASON_MAP = {
    "strategy_fixed_take_profit": "TP",
    "tp_check": "TP",
    "strategy_catastrophe_stop": "SL",
    "stop_loss": "SL",
    "loss_cap": "SL",
    "strategy_breakeven_lock": "BE",
    "mfe_breakeven": "BE",
    "strategy_horizon_exit": "D_MAT",
    "pre_close": "D_MAT",
}


def normalize_reason(reason: str | None) -> str:
    r = str(reason or "").strip()
    return REASON_MAP.get(r.lower(), r.upper())


def _fee_by_arm() -> dict[str, float]:
    """arm → 장부 왕복 비용(%). virtual_books 계약과 같은 값을 쓴다(없으면 US 0.50)."""
    try:
        import virtual_books as vb
        return {s["id"]: (vb.FEE_KR if vb.strategy_market(s) == "KR" else vb.FEE_US) for s in vb.STRATEGIES}
    except Exception:
        return {}


def build_row(c: dict, book: tuple, *, fee_pct: float, stamp: str) -> dict:
    """유령 CLOSE 행 c + 장부 (status, entry_price, net_pct, exit_reason) → 대조 행."""
    _status, b_entry, b_net, b_reason = book
    gross = c.get("gross_pct")
    p_net = round(float(gross) - fee_pct, 4) if gross is not None else None
    p_reason = normalize_reason(c.get("reason"))
    return {"ts": stamp, "arm": str(c.get("arm") or "us_live_dvol"),
            "book_session_date": str(c.get("book_session_date") or c.get("session_date")),
            "ticker": str(c.get("ticker")).upper(),
            "phantom_entry": c.get("entry_usd"), "book_entry": b_entry,
            "entry_diff_pct": (round((float(c["entry_usd"]) / float(b_entry) - 1.0) * 100.0, 3)
                               if c.get("entry_usd") and b_entry else None),
            "phantom_reason": c.get("reason"), "phantom_reason_norm": p_reason, "book_reason": b_reason,
            "reason_match": p_reason == str(b_reason or "").upper(),
            "phantom_gross_pct": gross, "fee_pct": fee_pct, "phantom_net_pct": p_net, "book_net_pct": b_net,
            "net_diff_pct": (round(p_net - float(b_net), 3) if p_net is not None and b_net is not None else None),
            "phantom_held_days": c.get("held_days"), "retro": bool(c.get("retro"))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="기존 원장을 버리고 새 기준(net·사유 정규화)으로 전부 재산출")
    a = ap.parse_args(argv)
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
    if OUT.exists() and not a.rebuild:
        for line in OUT.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                done.add((r["arm"], r["book_session_date"], r["ticker"]))
            except (ValueError, KeyError):
                continue
    fees = _fee_by_arm()
    con = sqlite3.connect(f"file:{BOOK}?mode=ro", uri=True, timeout=10)
    added = 0
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with OUT.open("w" if a.rebuild else "a", encoding="utf-8") as fh:
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
                fh.write(json.dumps(build_row(c, row, fee_pct=fees.get(arm, 0.50), stamp=stamp), ensure_ascii=False) + "\n")
                done.add(key)
                added += 1
    finally:
        con.close()
    print(f"[phantom_vs_daily] {'재산출' if a.rebuild else '신규 대조'} {added}건 (유령 청산 {len(closes)}건, 누적 {len(done)}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
