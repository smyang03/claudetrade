"""sleeve 계약 청산의 CLOSED 이벤트 소급 주입 (라이브 코드 무접촉).

2026-08-06 실측 결함: `CLOSED` lifecycle 이벤트를 발행하는 실행 경로는 PathB
매도 하나뿐이다. isolated sleeve의 계약 청산(strategy_fixed_take_profit /
strategy_catastrophe_stop / strategy_horizon_exit)은 이벤트를 남기지 않는다.

  AXTI 2026-07-31 TP 청산  -> CLOSED 없음
  FRMI 2026-08-05 TP 청산  -> CLOSED 없음 (+12.32%, +36,898원)
  lifecycle_events 마지막 CLOSED: 2026-07-31 MSFT(PathB 경로)

영향: 학습 원장에 sleeve 성과가 귀속되지 않고, integrity_check의 "무청산" 경고가
오탐이 되어 진짜 사고와 구분되지 않는다. 주문 동작에는 영향이 없다(기록 계층).

C안(운영자 결정 2026-08-06): 30건 판정까지 계약·라이브 코드는 동결한다.
대신 이 도구가 봇 로그의 확정 청산 라인을 읽어 CLOSED 이벤트를 소급 주입한다.
멱등 — 같은 (ticker, occurred_at)이 이미 있으면 건너뛴다.

사용:
  python tools/backfill_sleeve_closed_events.py --dry-run   # 주입 대상만 표시
  python tools/backfill_sleeve_closed_events.py             # 실제 주입
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lifecycle.event_store import EventStore  # noqa: E402
from lifecycle.models import LifecycleEvent  # noqa: E402

EVENT_DB = ROOT / "data" / "v2_event_store.db"
KST = timezone(timedelta(hours=9))

# sleeve 계약 청산만 대상으로 한다. 일반 Path-A 청산은 기존 경로가 처리한다.
SLEEVE_REASONS = {
    "strategy_fixed_take_profit",
    "strategy_catastrophe_stop",
    "strategy_horizon_exit",
}

_CLOSE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*close_position:\d+ \| "
    r"\[(?P<reason>[a-z_]+)\] (?P<ticker>\S+) (?P<pnl_krw>[+-][\d,]+) \((?P<pnl_pct>[+-][\d.]+)%\)"
)


def _is_us(ticker: str) -> bool:
    return not ticker.isdigit()


def _session_date(ts: str, market: str) -> str:
    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    # US 세션은 KST 22:30 개장이라 자정을 넘긴다 — 자정 후 청산은 전일 세션이다.
    if market == "US" and dt.hour < 9:
        return (dt - timedelta(days=1)).date().isoformat()
    return dt.date().isoformat()


def scan(days: int) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d") if days else ""
    found: list[dict] = []
    for path in sorted((ROOT / "logs" / "system").glob("live_trading_*.log")):
        stamp = "".join(ch for ch in path.stem if ch.isdigit())
        if cutoff and stamp < cutoff:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = _CLOSE_RE.match(line)
            if not m:
                continue
            g = m.groupdict()
            if g["reason"] not in SLEEVE_REASONS:
                continue
            market = "US" if _is_us(g["ticker"]) else "KR"
            occurred = datetime.strptime(g["ts"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
            found.append({
                "ticker": g["ticker"], "market": market, "reason": g["reason"],
                "pnl_pct": float(g["pnl_pct"]),
                "pnl_krw": float(g["pnl_krw"].replace(",", "")),
                "session_date": _session_date(g["ts"], market),
                "occurred_at": occurred.astimezone(timezone.utc).isoformat(),
                "ts_kst": g["ts"],
            })
    return found


def existing_keys() -> set[tuple[str, str]]:
    """이미 기록된 CLOSED 식별키.

    2026-08-17: 라이브 청산 경로가 CLOSED를 직접 발행하기 시작했다(운영자 승인).
    라이브 발행 시각과 로그 타임스탬프는 초 단위로 다를 수 있으므로 occurred_at만
    비교하면 같은 청산을 두 번 넣는다. (ticker, session_date) 키를 함께 반환해
    세션 단위로도 멱등을 보장한다. 같은 세션에 같은 종목을 두 번 청산하는 계약은
    없다(sleeve는 D5 단일 보유).
    """
    if not EVENT_DB.exists():
        return set()
    con = sqlite3.connect(f"file:{EVENT_DB}?mode=ro", uri=True, timeout=10)
    try:
        rows = con.execute(
            "SELECT ticker, occurred_at, session_date FROM lifecycle_events WHERE event_type='CLOSED'"
        ).fetchall()
    finally:
        con.close()
    keys: set[tuple[str, str]] = set()
    for ticker, occurred_at, session_date in rows:
        keys.add((str(ticker), str(occurred_at)))
        if session_date:
            keys.add((str(ticker), f"session:{session_date}"))
    return keys


def backfill_sleeve_closed(days: int = 0, *, dry_run: bool = False, verbose: bool = True) -> dict:
    """로그의 sleeve 계약 청산을 CLOSED 이벤트로 주입하고 결과 요약을 돌려준다.

    2026-08-17: v2_daily_loop이 매일 호출한다. sync는 세션 범위 필터가 있어서
    **청산 당일에 CLOSED가 만들어져야** 그날 sync가 canonical로 옮긴다. 며칠 지나
    수동으로 넣으면 범위 밖이라 정본에 안 들어간다(CVI 08-10·MXL 08-14 실측).
    멱등이므로 매일 돌아도 안전하다.
    """
    def _say(msg: str) -> None:
        if verbose:
            print(msg)

    closes = scan(days)
    known = existing_keys()
    todo = [
        c for c in closes
        if (c["ticker"], c["occurred_at"]) not in known
        and (c["ticker"], f"session:{c['session_date']}") not in known
    ]

    _say(f"sleeve 계약 청산 로그 {len(closes)}건 | 이미 기록됨 {len(closes) - len(todo)} | 주입 대상 {len(todo)}")
    for c in todo:
        _say(f"  {c['ts_kst']}  {c['market']} {c['ticker']:<6} {c['reason']:<28} "
             f"{c['pnl_pct']:+.2f}% ({c['pnl_krw']:+,.0f}원)")
    summary = {"scanned": len(closes), "already": len(closes) - len(todo),
               "pending": len(todo), "written": 0, "dry_run": bool(dry_run),
               "tickers": [c["ticker"] for c in todo]}
    if dry_run or not todo:
        return summary

    store = EventStore(EVENT_DB)
    written = 0
    for c in todo:
        event = LifecycleEvent(
            event_type="CLOSED",
            market=c["market"],
            runtime_mode="live",
            session_date=c["session_date"],
            ticker=c["ticker"],
            # sleeve는 Claude decision을 거치지 않으므로 합성 id를 쓴다(추적 가능하게 표기).
            decision_id=f"sleeve_{c['market']}_{c['ticker']}_{c['session_date'].replace('-', '')}",
            prompt_version="n/a",
            brain_snapshot_id="n/a",
            reason_code=f"CLOSED_{c['reason'].upper()}",
            payload={
                "pnl_pct": c["pnl_pct"],
                "pnl_krw": c["pnl_krw"],
                # close_position 로그의 손익률은 KRW 실현 net(수수료·FX 반영) — B3 실측.
                # sync가 pnl_pct_net 정본 컬럼으로 옮긴다(2026-08-18 배선).
                "pnl_pct_net": c["pnl_pct"],
                "pnl_krw_net": c["pnl_krw"],
                "net_source": "broker_realized_krw",
                "close_reason": c["reason"],
                "backfilled_by": "tools/backfill_sleeve_closed_events.py",
                "backfill_note": "sleeve 계약 청산은 라이브 경로가 CLOSED를 발행하지 않아 로그에서 소급 주입",
                "source_log_ts_kst": c["ts_kst"],
            },
            occurred_at=c["occurred_at"],
        )
        store.append(event)
        written += 1
    _say(f"주입 완료 {written}건 -> {EVENT_DB}")
    summary["written"] = written
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill sleeve CLOSED lifecycle events")
    parser.add_argument("--days", type=int, default=0, help="최근 N일 로그만 (0=전체)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    backfill_sleeve_closed(args.days, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
