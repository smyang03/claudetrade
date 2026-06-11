"""벤치마크 정직성 지표 — 시스템 net vs SPY/QQQ 동기간 비교.

"이 고생 안 하고 SPY 사놨으면?"을 매주 한 줄로 답한다 (2026-06-11 운영자 승인).
6월 말 판정에서 '+0.3% net 통과'가 시장 베타인지 진짜 알파인지 구분하는 용도.

사용: python tools/benchmark_compare.py [--days 7] [--json]
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path

DEFAULT_ACCOUNT_BASE_KRW = 4_450_000.0


def _benchmark_change_pct(ticker: str, start_date: str, end_date: str) -> float | None:
    path = get_runtime_path("data", "price", "us") / f"us_{ticker}.csv"
    if not path.exists():
        return None
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    closes = [(str(r.get("date"))[:10], r.get("close")) for r in rows if r.get("close")]
    start_close = end_close = None
    for d, c in closes:
        if d <= start_date:
            start_close = float(c)
        if d <= end_date:
            end_close = float(c)
    if not start_close or not end_close or start_close <= 0:
        return None
    return round((end_close / start_close - 1.0) * 100.0, 2)


def _system_net(start_date: str, end_date: str) -> dict:
    path = get_runtime_path("data", "v2_event_store.db")
    if not Path(path).exists():
        return {"net_krw": None, "trades": 0, "net_recorded": 0}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute(
            "SELECT ticker, session_date, payload_json FROM lifecycle_events "
            "WHERE runtime_mode='live' AND event_type='CLOSED' "
            "AND session_date >= ? AND session_date <= ?",
            (start_date, end_date),
        ).fetchall()
    finally:
        con.close()
    seen: set = set()
    total = 0.0
    trades = 0
    recorded = 0
    for ticker, sd, pj in rows:
        try:
            p = json.loads(pj) if pj else {}
        except Exception:
            continue
        key = (ticker, sd, round(float(p.get("pnl_pct") or 0), 3))
        if key in seen:
            continue
        seen.add(key)
        trades += 1
        net = p.get("pnl_krw_net_est")
        if net is None:
            net = p.get("pnl_krw")  # net 미기록(레이스 등) 시 gross로 폴백
        else:
            recorded += 1
        if net is not None:
            total += float(net)
    return {"net_krw": round(total, 0), "trades": trades, "net_recorded": recorded}


def benchmark_report(days: int = 7, account_base_krw: float = DEFAULT_ACCOUNT_BASE_KRW) -> dict:
    end = datetime.now().date()
    start = end - timedelta(days=days)
    start_date, end_date = start.isoformat(), end.isoformat()
    system = _system_net(start_date, end_date)
    spy = _benchmark_change_pct("SPY", start_date, end_date)
    qqq = _benchmark_change_pct("QQQ", start_date, end_date)
    system_pct = (
        round(float(system["net_krw"]) / account_base_krw * 100.0, 2)
        if system["net_krw"] is not None and account_base_krw > 0
        else None
    )
    alpha_vs_spy = round(system_pct - spy, 2) if system_pct is not None and spy is not None else None
    return {
        "window": f"{start_date}~{end_date}",
        "system_net_krw": system["net_krw"],
        "system_net_pct_of_account": system_pct,
        "trades": system["trades"],
        "net_recorded_trades": system["net_recorded"],
        "spy_pct": spy,
        "qqq_pct": qqq,
        "alpha_vs_spy_pct": alpha_vs_spy,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--account-base-krw", type=float, default=DEFAULT_ACCOUNT_BASE_KRW)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = benchmark_report(days=args.days, account_base_krw=args.account_base_krw)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(
        f"[벤치마크 {report['window']}] 시스템 net {report['system_net_krw'] if report['system_net_krw'] is not None else '?'}원 "
        f"({report['system_net_pct_of_account'] if report['system_net_pct_of_account'] is not None else '?'}% / 거래 {report['trades']}건) "
        f"| SPY {report['spy_pct']}% QQQ {report['qqq_pct']}% "
        f"| 알파(vs SPY) {report['alpha_vs_spy_pct']}%p"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
