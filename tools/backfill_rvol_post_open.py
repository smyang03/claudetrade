"""과거 audit_candidate_rows.post_open_features_json에 time-normalized RVOL 소급 백필.

RVOL 배선(intraday_features)은 배포 이후 세션부터만 post_open feature에 RVOL을 채운다.
consensus shadow가 학습에 쓰는 과거 세션(학습창)은 RVOL이 None이라 serve에서 드롭된다.
data/price/minute의 과거 분봉으로 각 (market, session, ticker)의 RVOL을 소급 계산해
post_open_features_json에 주입하면 학습창이 즉시 RVOL을 커버한다(야후 추가 불필요).

기본은 dry-run(계산만, DB 미변경). --apply로 실제 UPDATE. 라이브 세션 중 대량 write는
봇의 candidate_audit write와 경합하므로 US/KR 마감 후 실행을 권장한다.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sqlite3
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.time_normalized_rvol import LocalTimeNormalizedRvolStore

DEFAULT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
MINUTE_ROOT = ROOT / "data" / "price" / "minute"


def _session_minute_rows(market: str, ticker: str, session_date: str) -> list[dict]:
    prefix = "us" if str(market).upper() == "US" else "kr"
    symbol = str(ticker).upper() if prefix == "us" else str(ticker)
    path = MINUTE_ROOT / prefix / f"{prefix}_{symbol}.csv"
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                ts = str(row.get("ts") or "")
                if ts[:10] == str(session_date):
                    out.append({"ts": ts, "volume": row.get("volume")})
    except (OSError, csv.Error):
        return []
    return out


def _all_minute_rows_by_session(market: str, ticker: str) -> dict[str, list[dict]]:
    """종목 CSV를 1회만 읽어 세션(날짜)별 분봉 리스트로 나눈다(행마다 재파싱 방지)."""
    prefix = "us" if str(market).upper() == "US" else "kr"
    symbol = str(ticker).upper() if prefix == "us" else str(ticker)
    path = MINUTE_ROOT / prefix / f"{prefix}_{symbol}.csv"
    if not path.exists():
        return {}
    by_session: dict[str, list[dict]] = {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                ts = str(row.get("ts") or "")
                if not ts:
                    continue
                by_session.setdefault(ts[:10], []).append({"ts": ts, "volume": row.get("volume")})
    except (OSError, csv.Error):
        return {}
    return by_session


def backfill(db_path: Path, *, market: str, since: str, apply: bool, limit: int,
             batch: int = 2000, values_only: bool = True) -> dict:
    from runtime.time_normalized_rvol import compute_time_normalized_rvol

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA busy_timeout=30000")
    con.row_factory = sqlite3.Row
    where = ["post_open_features_json IS NOT NULL", "post_open_features_json != ''",
             "post_open_features_json NOT LIKE '%rvol_backfilled%'"]  # 재개: 이미 처리분 skip
    params: list = []
    if market != "ALL":
        where.append("market = ?")
        params.append(market)
    if since:
        where.append("session_date >= ?")
        params.append(since)
    sql = f"SELECT rowid, market, session_date, ticker, post_open_features_json FROM audit_candidate_rows WHERE {' AND '.join(where)} ORDER BY market, ticker, session_date"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = con.execute(sql, params).fetchall()

    stats = {"scanned": 0, "already": 0, "filled_ok": 0, "filled_status_only": 0,
             "no_minute": 0, "parse_err": 0, "updated": 0, "samples": []}
    pending: list[tuple[str, int]] = []
    cur_key = None
    cur_by_session: dict[str, list[dict]] = {}

    def _flush():
        if apply and pending:
            con.executemany(
                "UPDATE audit_candidate_rows SET post_open_features_json = ? WHERE rowid = ?",
                pending,
            )
            con.commit()
            stats["updated"] += len(pending)
            pending.clear()

    for row in rows:
        stats["scanned"] += 1
        try:
            pof = json.loads(row["post_open_features_json"])
            if not isinstance(pof, dict):
                stats["parse_err"] += 1
                continue
        except Exception:
            stats["parse_err"] += 1
            continue
        if pof.get("time_normalized_rvol") is not None:
            stats["already"] += 1
            continue
        key = (row["market"], row["ticker"])
        if key != cur_key:
            cur_key = key
            cur_by_session = _all_minute_rows_by_session(row["market"], row["ticker"])  # 종목당 1회
        session = str(row["session_date"])
        current = cur_by_session.get(session)
        if not current:
            stats["no_minute"] += 1
            continue
        historical = [r for s, rs in cur_by_session.items() if s < session for r in rs]
        res = compute_time_normalized_rvol(
            current_rows=current,
            historical_rows=historical,
            market=row["market"],
            known_at=pof.get("known_at"),
            session_date=session,
        )
        val = res.get("time_normalized_rvol")
        if val is None and values_only:
            # 값이 없으면(history_insufficient 등) write 생략 — 학습에 쓸모 없음.
            # 단 재스캔을 피하려 마커는 남기지 않는다(다음 백필/야후 보충 때 재시도 가능).
            stats["filled_status_only"] += 1
            continue
        pof["time_normalized_rvol"] = val
        pof["rvol_profile_sessions"] = res.get("rvol_profile_sessions")
        pof["rvol_profile_status"] = res.get("rvol_profile_status")
        pof["rvol_backfilled"] = True
        if val is not None:
            stats["filled_ok"] += 1
            if len(stats["samples"]) < 8:
                stats["samples"].append(
                    (row["market"], session, row["ticker"], round(float(val), 3), res.get("rvol_profile_status"))
                )
        else:
            stats["filled_status_only"] += 1
        pending.append((json.dumps(pof, ensure_ascii=False, separators=(",", ":")), row["rowid"]))
        if len(pending) >= batch:
            _flush()

    _flush()
    con.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="past post_open RVOL backfill from local minute bars")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--market", default="ALL", choices=["ALL", "US", "KR"])
    ap.add_argument("--since", default="", help="session_date >= (YYYY-MM-DD)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true", help="실제 DB UPDATE (기본 dry-run)")
    args = ap.parse_args()
    stats = backfill(args.db, market=args.market, since=args.since, apply=args.apply, limit=args.limit)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
