# -*- coding: utf-8 -*-
"""KR 종가 동시호가 눌림 쉐도우 (P12, 2026-09-08) — 15:19 스냅 대비 종가 ≤−2%인 종목을 다음날 시가 매수, 다음날 15:19 청산.

메커니즘: 마감 동시호가(15:20~15:30)의 ETF·펀드 리밸런싱·반대매매가 종가를 누르면 다음날 시가에 되돌린다(마감 가격 압력 문헌).
시세: 네이버(`tools/analysis_quotes.get_quote_kr`) — 장중 KIS 시세 루프 금지 원칙(analysis-script-runbook) 준수, KIS 호출 예산과 무관.
유니버스: 전일 거래대금 상위 250 + 전일 ≤−5% 급락 풀(CSV). 15:18:20~ 스냅(가격·누적거래량, 0.25s 간격 ≈70s) → 15:30:40~ 종가 스냅 → 눌림 신호.
정산: 다음 거래일 시가(CSV) 진입 가정 → 그날 15:19 스냅 가격으로 청산(이 수집기가 다음날 15:19에 찍는다). 비용 0.21%.
원장: data/shadow/kr_close_auction.jsonl (snapshot / signal / trade). schtask claudetrade_kr_close_auction 주중 15:17, PT20M.
사용: python tools/kr_close_auction_collector.py [--universe N]
"""
from __future__ import annotations

import csv
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
KR_DIR = ROOT / "data" / "price" / "kr"
OUT = ROOT / "data" / "shadow" / "kr_close_auction.jsonl"
DROP_MAX = -2.0
COST = 0.21
CONTRACT = "close_auction_v1:next_open→next_1519"


def _jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _append(row: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def universe(n_top: int, today: str) -> list[str]:
    dv: list[tuple[float, str]] = []; fallen: list[str] = []
    for p in KR_DIR.glob("kr_*.csv"):
        rows = [r for r in csv.reader(p.open(encoding="utf-8-sig")) if r and r[0][:2] == "20" and r[0] < today]
        if len(rows) < 2:
            continue
        try:
            c, v, pc = float(rows[-1][4]), float(rows[-1][5]), float(rows[-2][4])
        except ValueError:
            continue
        if c < 1000:
            continue
        dv.append((c * v, p.stem[3:]))
        if pc > 0 and (c / pc - 1) * 100 <= -5:
            fallen.append(p.stem[3:])
    top = [t for _, t in sorted(dv, reverse=True)[:n_top]]
    return sorted(set(top) | set(fallen))


def snap(tickers: list[str], label: str, today: str) -> dict[str, dict]:
    from tools.analysis_quotes import get_quote_kr
    out = {}
    for tk in tickers:
        q = get_quote_kr(tk)
        if q and q.get("price"):
            out[tk] = {"price": q["price"], "volume": q.get("volume"), "open": q.get("open")}
    _append({"kind": "snapshot", "date": today, "label": label, "n": len(out), "quotes": out,
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return out


def _wait_until(hhmmss: str) -> None:
    while datetime.now().strftime("%H:%M:%S") < hhmmss:
        time.sleep(2)


def main() -> int:
    args = sys.argv[1:]
    n_top = int(args[args.index("--universe") + 1]) if "--universe" in args else 250
    today = date.today().isoformat()
    if date.today().weekday() >= 5:
        return 0
    rows = _jsonl(OUT)
    if any(r.get("kind") == "signal_session" and r.get("date") == today for r in rows):
        print(f"[CLOSEAUC] {today} 이미 기록"); return 0
    uni = universe(n_top, today)
    # 1) 전날 OPEN 거래의 청산 대상도 15:19 스냅에 포함
    opens = [r for r in rows if r.get("kind") == "trade" and r.get("status") == "OPEN"]
    tickers = sorted(set(uni) | {r["ticker"] for r in opens})
    _wait_until("15:18:20")   # 네이버 0.25s×~280종목 ≈ 70s → 15:19:30 전 완료(동시호가 15:20 전)
    s1519 = snap(tickers, "1519", today)
    # 2) 전날 신호의 정산: 오늘 시가(CSV는 16:00 갱신 → 네이버 open 사용) 진입 → 15:19 청산
    for r in opens:
        q = s1519.get(r["ticker"])
        if not q or not q.get("open") or not q.get("price"):
            continue
        entry = float(q["open"]); exit_px = float(q["price"])
        r.update({"status": "CLOSED", "entry_price": entry, "exit_price": exit_px, "exit_at": "15:19",
                  "net_pct": round((exit_px / entry - 1) * 100 - COST, 3), "settled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    if opens:
        tmp = OUT.with_suffix(".tmp"); tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8"); tmp.replace(OUT)
    _wait_until("15:30:40")
    s1531 = snap(sorted(uni), "1531", today)
    n_sig = 0
    for tk in uni:
        a, b = s1519.get(tk), s1531.get(tk)
        if not a or not b:
            continue
        drop = (float(b["price"]) / float(a["price"]) - 1) * 100
        auc_vol = (float(b.get("volume") or 0) - float(a.get("volume") or 0))
        if drop <= DROP_MAX:
            _append({"kind": "signal", "date": today, "ticker": tk, "px_1519": a["price"], "close": b["price"], "drop_pct": round(drop, 3),
                     "auction_volume": auc_vol, "auction_vol_ratio": round(auc_vol / float(b["volume"]), 3) if b.get("volume") else None})
            _append({"kind": "trade", "date": today, "ticker": tk, "contract": CONTRACT, "status": "OPEN", "signal_drop_pct": round(drop, 3),
                     "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            n_sig += 1
    _append({"kind": "signal_session", "date": today, "universe_n": len(uni), "n_1519": len(s1519), "n_1531": len(s1531), "n_signal": n_sig})
    print(f"[CLOSEAUC] {today} 유니버스 {len(uni)} 스냅 {len(s1519)}/{len(s1531)} 신호 {n_sig} 정산 {len(opens)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
