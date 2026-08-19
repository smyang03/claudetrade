# -*- coding: utf-8 -*-
"""라이브 로그에서 WS 단절 구간을 추출해 계측 이력 원장에 소급 기록한다.

배경: 계측 원장 자동기록(_record_instrument_degradation)은 봇 재시작 후에만
동작한다. 그 전에 발생한 단절, 그리고 자동기록이 놓친 구간을 로그에서 복원해
`data/shadow/instrument_health_events.jsonl`에 채운다. 로그는 롤오버되므로
당일 안에 돌려야 한다.

30건 코호트의 품질은 그 30번 동안의 배관 품질을 넘을 수 없다. 강등 구간을
남겨야 판정 때 "계측 정상 구간만"으로 재검증할 수 있다.

사용:
    python tools/backfill_instrument_health.py --date 20260819 [--apply]
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "shadow" / "instrument_health_events.jsonl"

# on_close(서버측 단절) / 재기동 완료 / 재연결 성공
RE_CLOSE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[KIS WS\] (?P<mkt>KR|US) 연결이 서버"
)
RE_RESTART = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[WS silence restart\] (?P<mkt>KR|US)"
)
RE_RECONNECT = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[KIS WS\] (?P<mkt>KR|US) 재연결 성공"
)
RE_POSITIONS = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[포지션 저장\] \[(?P<items>[^\]]*)\]"
)


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")


def _holdings_at(snapshots: list[tuple[datetime, list[str]]], when: datetime) -> list[str]:
    """해당 시각 직전의 마지막 포지션 스냅샷."""
    latest: list[str] = []
    for ts, items in snapshots:
        if ts <= when:
            latest = items
        else:
            break
    return latest


def _is_market(ticker: str, market: str) -> bool:
    return (market == "KR") == ticker.isdigit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="YYYYMMDD")
    ap.add_argument("--apply", action="store_true", help="실제 기록(미지정 시 미리보기)")
    args = ap.parse_args()

    log_path = ROOT / "logs" / "system" / f"live_trading_{args.date}.log"
    if not log_path.exists():
        print(f"로그 없음: {log_path}")
        return 1

    closes: list[tuple[datetime, str]] = []
    recoveries: list[tuple[datetime, str]] = []
    snapshots: list[tuple[datetime, list[str]]] = []

    with log_path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = RE_CLOSE.match(line)
            if m:
                closes.append((_parse(m.group("ts")), m.group("mkt")))
                continue
            m = RE_RESTART.match(line) or RE_RECONNECT.match(line)
            if m:
                recoveries.append((_parse(m.group("ts")), m.group("mkt")))
                continue
            m = RE_POSITIONS.match(line)
            if m:
                items = [t.strip().strip("'\"") for t in m.group("items").split(",") if t.strip()]
                snapshots.append((_parse(m.group("ts")), items))

    existing = []
    if LEDGER.exists():
        existing = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    seen = {(r.get("market"), r.get("started_at")) for r in existing}

    session_date = f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:]}"
    new_rows = []
    for down_at, market in closes:
        rec = next((ts for ts, mkt in recoveries if mkt == market and ts > down_at), None)
        if rec is None:
            print(f"  [skip] {market} {down_at:%H:%M:%S} 복구 기록 없음(진행 중일 수 있음)")
            continue
        held = [t for t in _holdings_at(snapshots, down_at) if _is_market(t, market)]
        if not held:
            print(f"  [skip] {market} {down_at:%H:%M:%S} 해당 시장 보유 없음 — 판정 무관")
            continue
        started = down_at.isoformat() + "+09:00"
        if (market, started) in seen:
            print(f"  [dup]  {market} {down_at:%H:%M:%S} 이미 기록됨")
            continue
        row = {
            "schema_version": "instrument_health_event_v1",
            "market": market,
            "session_date": session_date,
            "kind": "ws_tick_silence",
            "started_at": started,
            "ended_at": rec.isoformat() + "+09:00",
            "duration_sec": float((rec - down_at).total_seconds()),
            "affected_holdings": held,
            "exit_path_during": "rest_holding_price_refresh",
            "detail": "backfill_from_log by tools/backfill_instrument_health.py",
            "backfilled": True,
        }
        new_rows.append(row)
        print(f"  [new]  {market} {down_at:%H:%M:%S}~{rec:%H:%M:%S} "
              f"{row['duration_sec']:.0f}s {held}")

    if not new_rows:
        print("추가할 구간 없음")
        return 0
    if not args.apply:
        print(f"\n미리보기 {len(new_rows)}건 — 기록하려면 --apply")
        return 0

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        for row in new_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\n{len(new_rows)}건 기록 완료 → {LEDGER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
