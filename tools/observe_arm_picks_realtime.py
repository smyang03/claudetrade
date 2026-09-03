#!/usr/bin/env python3
"""실시간 픽 보드 관측기 — 16전략의 오늘 밤 픽을 결정 시점에 박제 (2026-09-03, 설계 정본 §4-2a).

가상 북(virtual_books.py)은 다음날 07:20에야 진입을 적는다. 운영자는 22:35에 "각 전략이 뭘 샀는지"를
대시보드에서 보고 싶다 → 22:36(US 개장 +6분, 핸드오프 창 안)에 같은 픽 규칙(`virtual_books.strategy_passers`·
`strategy_pick_key` **재사용**)으로 전 arm의 픽을 산출해 원장에 적는다. 07:20 일봉 정산이 붙으면 "확정"으로 바뀐다.

시세: yfinance 지연 호가(quote_source="yfinance_delayed") — 분석용 시세 규율(장중 KIS 루프 금지) 준수.
라이브 미러는 실제 봇의 REHEARSAL 호가(KIS)가 있으면 함께 적는다(quote_source="kis_rehearsal").
원장: data/shadow/arm_picks_realtime.jsonl — (session_date, arm, ticker) 멱등.
사용: python tools/observe_arm_picks_realtime.py [--session-date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import virtual_books as vb  # noqa: E402

LEDGER = ROOT / "data" / "shadow" / "arm_picks_realtime.jsonl"
STATUS = ROOT / "state" / "us_swing_execution_status.json"


def latest_us_session(sessions_us: dict) -> str:
    return max(sessions_us) if sessions_us else ""


def rehearsal_quotes() -> dict[str, float]:
    """실제 봇의 REHEARSAL 호가(KIS) — 라이브 미러 대조용."""
    out: dict[str, float] = {}
    try:
        d = json.loads(STATUS.read_text(encoding="utf-8"))
        for r in (d.get("last_result") or {}).get("results", []) or []:
            if r.get("status") == "REHEARSAL_READY" and r.get("quote_price"):
                out[str(r.get("ticker")).upper()] = float(r["quote_price"])
    except Exception:
        pass
    return out


def yf_quotes(tickers: list[str]) -> dict[str, float]:
    if not tickers:
        return {}
    try:
        import yfinance as yf
        data = yf.download(sorted(set(tickers)), period="1d", interval="1m", progress=False,
                           group_by="ticker", threads=True)
        out: dict[str, float] = {}
        for t in set(tickers):
            try:
                df = data[t] if len(tickers) > 1 else data
                close = df["Close"].dropna()
                if len(close):
                    out[t] = float(close.iloc[-1])
            except Exception:
                continue
        return out
    except Exception as exc:
        print(f"[arm_picks] yfinance 실패({str(exc)[:80]}) — 호가 없이 기록")
        return {}


def existing_keys() -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                keys.add((str(r["session_date"]), str(r["arm"]), str(r["ticker"])))
            except (ValueError, KeyError):
                continue
    return keys


def compute_picks(session_date: str) -> list[dict]:
    us = vb.load_sessions()
    kr = vb.load_kr_sessions()
    slow = vb.load_slow_sessions()
    lp = vb.load_lp_sessions()
    rows: list[dict] = []
    for s in vb.STRATEGIES:
        if s.get("retired"):
            continue
        if s["universe"] in ("kr", "krevent", "krlimitup"):
            continue  # KR은 다음날 09:00 진입 — 이 보드(US 밤) 대상 아님
        # S11/B2: 신호일 = 직전 세션, 진입 = 이번 세션 (virtual_books 규약)
        key_sd = session_date
        if s["universe"] in ("slowus", "lpus"):
            src = slow if s["universe"] == "slowus" else lp
            prev = [d for d in src if d < session_date]
            if not prev:
                continue
            key_sd = max(prev)
        passers = vb.strategy_passers(s, us, kr, key_sd, slow, sessions_lp=lp)
        if not passers:
            continue
        if s["pick"] == "all":
            chosen = passers[: int(s["daily_cap"])]
        else:
            chosen = sorted(passers, key=lambda c: vb.strategy_pick_key(s, c))[: int(s["daily_cap"])]
        for pos, c in enumerate(chosen, start=1):
            rows.append({"session_date": session_date, "arm": s["id"], "ticker": str(c["ticker"]).upper(),
                         "pick_pos": pos, "universe": s["universe"], "n_passers": len(passers),
                         # 유령 엔진(③)이 봇 안에서 virtual_books를 import하지 않도록 arm 계약을 여기 박제
                         "book_session_date": key_sd, "tp_pct": float(s.get("tp", vb.TP)) / 100.0,
                         "sl_pct": abs(float(s.get("sl", vb.SL))) / 100.0, "order_krw": float(s["order_krw"]),
                         "slots": int(s["slots"]), "daily_cap": int(s["daily_cap"]),
                         "basis": vb.pick_basis(s, c)})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-date", default=None)
    ap.add_argument("--no-quotes", action="store_true")
    args = ap.parse_args()
    us = vb.load_sessions()
    sd = args.session_date or latest_us_session(us)
    if not sd:
        print("[arm_picks] 후보 풀 없음")
        return 0
    rows = compute_picks(sd)
    done = existing_keys()
    new = [r for r in rows if (r["session_date"], r["arm"], r["ticker"]) not in done]
    if not new:
        print(f"[arm_picks] {sd} 신규 픽 없음 (기존 {sum(1 for k in done if k[0]==sd)}건)")
        return 0
    quotes = {} if args.no_quotes else yf_quotes([r["ticker"] for r in new])
    reh = rehearsal_quotes()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        for r in new:
            q = quotes.get(r["ticker"])
            r.update({"decided_at": stamp, "quote": q, "quote_source": "yfinance_delayed" if q else None,
                      "rehearsal_quote": reh.get(r["ticker"]), "rehearsal_quote_source": "kis_rehearsal" if r["ticker"] in reh else None})
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    by_arm: dict[str, list[str]] = {}
    for r in new:
        by_arm.setdefault(r["arm"], []).append(r["ticker"])
    print(f"[arm_picks] {sd} 기록 {len(new)}건: " + "; ".join(f"{a}={','.join(t[:5])}{'…' if len(t)>5 else ''}" for a, t in by_arm.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
