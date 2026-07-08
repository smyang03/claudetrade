#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""4~5월 v2_learning_performance.market_regime 백필 (C1-④, 2026-07-09 최적성 토론).

배경: market_regime이 6월부터만 기록 → mode-사이징 기여 판정(C2)이 6월 단일월로 강제됨.
소스: 같은 DB decisions 테이블(ts·market·mode, 4~7월 전 기간 100% 기록).

방법(no-lookahead): 각 대상 행의 filled_at(없으면 session_date 정오)에 대해
같은 market의 decisions 중 |ts−기준시각| 최소인 mode를 채택 (±6h 밖이면 스킵=정직).
가드: market_regime IS NULL/''인 closed 행만, 대상 월 4~5월만. 기본 dry-run, --apply로 기록.
"""
import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ml" / "decisions.db"
TARGET_MONTHS = ("2026-04", "2026-05")
MAX_GAP_H = 6.0


def _parse_ts(v: str):
    """KST naive datetime으로 정규화. decisions.ts=KST naive, v2 filled_at=UTC(+00:00)."""
    s = str(v or "").strip().replace("Z", "+00:00")
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.fromisoformat(s[:19])
        except ValueError:
            return None
    if dt.tzinfo is not None:
        # aware(UTC 등) → KST 변환 후 naive
        from datetime import timezone
        dt = dt.astimezone(timezone(timedelta(hours=9))).replace(tzinfo=None)
    return dt


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true", help="실제 기록 (미지정 시 dry-run)")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 소스 1: decisions(ts KST naive·mode) — 단 4/4~5/11 공백(4/2~4/3 버스트 후 5/12 재개) 실측.
    # 소스 2(보강): ticker_selection_log.consensus_mode(KST naive selected_at, 4~5월 전 거래일 커버).
    src = {"KR": [], "US": []}
    for r in cur.execute(
        "SELECT ts, market, mode FROM decisions "
        "WHERE ts >= '2026-03-31' AND ts < '2026-06-02' AND mode IS NOT NULL AND mode != ''"
    ):
        t = _parse_ts(r["ts"])
        mk = str(r["market"] or "").upper()
        if t and mk in src:
            src[mk].append((t, str(r["mode"])))
    sel_db = ROOT / "data" / "ticker_selection_log.db"
    if sel_db.exists():
        scon = sqlite3.connect(f"file:{sel_db}?mode=ro", uri=True)
        for r in scon.execute(
            "SELECT selected_at, market, consensus_mode FROM ticker_selection_log "
            "WHERE date < '2026-06' AND consensus_mode IS NOT NULL AND consensus_mode != '' "
            "AND consensus_mode != 'PREOPEN_WATCH'"
        ):
            t = _parse_ts(r[0])
            mk = str(r[1] or "").upper()
            if t and mk in src:
                src[mk].append((t, str(r[2])))
        scon.close()
    for mk in src:
        src[mk].sort()
    print(f"소스(decisions+selection_log): KR {len(src['KR'])}행 / US {len(src['US'])}행")

    month_pred = " OR ".join("session_date LIKE ?" for _ in TARGET_MONTHS)
    params = [f"{m}%" for m in TARGET_MONTHS]
    rows = list(cur.execute(
        f"""SELECT rowid, market, session_date, filled_at FROM v2_learning_performance
            WHERE closed=1 AND (market_regime IS NULL OR market_regime='')
              AND ({month_pred})""",
        params,
    ))
    print(f"백필 대상: {len(rows)}행 / 모드: {'APPLY' if args.apply else 'DRY-RUN'}")

    updates, skipped = [], 0
    stats: dict[str, int] = {}
    for r in rows:
        mk = str(r["market"] or "").upper()
        base = _parse_ts(r["filled_at"]) or _parse_ts(f"{r['session_date']}T12:00:00")
        if not base or mk not in src or not src[mk]:
            skipped += 1
            continue
        # 최근접 mode (이분탐색 생략 — 소스 수만 행, 대상 ~107행이라 선형 충분)
        best = min(src[mk], key=lambda x: abs((x[0] - base).total_seconds()))
        gap_h = abs((best[0] - base).total_seconds()) / 3600.0
        if gap_h > MAX_GAP_H:
            skipped += 1
            continue
        updates.append((best[1], r["rowid"]))
        stats[f"{mk}:{best[1]}"] = stats.get(f"{mk}:{best[1]}", 0) + 1

    for k in sorted(stats):
        print(f"  {k}: {stats[k]}")
    print(f"채움 {len(updates)} / 스킵(소스갭>{MAX_GAP_H}h·파싱불가) {skipped}")

    if args.apply and updates:
        cur.executemany(
            "UPDATE v2_learning_performance SET market_regime=? WHERE rowid=?", updates
        )
        con.commit()
        print("기록 완료")
    elif not args.apply:
        print("→ 실제 기록: --apply")


if __name__ == "__main__":
    main()
