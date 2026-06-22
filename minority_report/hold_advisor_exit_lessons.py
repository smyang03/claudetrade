"""hold advisor profit_guard 청산 교훈 — forward-validation 편입 (#4a).

profit_guard(익절 우선) prior가 라이브에서 net+였는지 검증한다. 임시 도구
`tools/hold_advisor_outcome_review.py`(다른 포지션 SELL vs HOLD = selection bias)와 달리,
**같은 포지션**의 'SELL 실현(actual=would_be 프레임)' vs 'HOLD 지속했다면 forward(반사실)'를
비교해 selection bias 없이 측정한다.

설계: docs/important/HOLD_ADVISOR_LESSON_VALIDATION_HANDOFF.md
- score_cell/upsert_cells/counterfactual_gain은 lesson_validation에서 재사용(변경 0).
- forward 라벨(SELL 후 HOLD 지속 가격)은 selection의 forward_3d처럼 미리 없으므로 yfinance로
  별도 백필(`backfill_forward`). 무거워서 봇 세션마감 hook에는 rescore(테이블 읽기)만 연결한다.
- config 토글 무관하게 축적 자체는 안전(격리 store + read-only decisions.db). 반영(control
  자동 적용)은 하지 않는다 — verdict만 쌓고 토글(HOLD_ADVISOR_PROFIT_GUARD_ENABLED)은 운영자 판정.
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from minority_report import lesson_validation as lv

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"
PROFIT_GUARD_ON_DATE = "2026-06-16"  # _PROFIT_GUARD_PRIOR ON 분기일
LESSON_KEY = "hold_profit_guard_exit"
DEFAULT_FORWARD_DAYS = 3

_TABLE = """
CREATE TABLE IF NOT EXISTS hold_advisor_exit_outcome (
    sell_key TEXT PRIMARY KEY,
    path_run_id TEXT, market TEXT, ticker TEXT,
    sell_ts TEXT, sell_date TEXT,
    entry_price REAL, realized_net REAL,
    hold_fwd_price REAL, hold_fwd_net REAL,
    regime TEXT, hold_mode TEXT, judge_pnl REAL,
    forward_days INTEGER, synced_at TEXT
)
"""


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path or ML_DB), timeout=10)
    con.execute("PRAGMA busy_timeout=8000")
    con.execute(_TABLE)
    return con


def _is_profit_exit(hold_mode: str, judge_pnl: float | None) -> bool:
    """이익 중 SELL(익절)만 — profit_guard가 영향을 주는 케이스. 손절성 SELL은 제외."""
    if hold_mode in ("profit_pullback", "target_extension"):
        return True
    return judge_pnl is not None and judge_pnl > 0


def collect_exit_outcomes(db_path: str | None = None) -> int:
    """hold advisor decisions(profit_guard ON 익절 SELL) + v2 조인 → 테이블 upsert.

    forward(hold_fwd)는 여기서 채우지 않는다(backfill_forward 담당). 반환=수집 행 수.
    """
    # 1) hold advisor decisions: path_run별 마지막 SELL
    sells: dict[str, dict] = {}
    for f in sorted(glob.glob(str(ROOT / "logs" / "hold_advisor" / "decisions_2026-*.jsonl"))):
        for line in open(f, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("decision") != "SELL":
                continue
            ts = str(d.get("ts", "") or "")
            if ts[:10] < PROFIT_GUARD_ON_DATE:
                continue
            ctx = d.get("pathb_revenue_path_context") or {}
            prid = str(ctx.get("path_run_id") or "")
            if not prid:
                continue
            votes = d.get("votes") or {}
            hm = ""
            for k in ("neutral", "bull", "bear"):
                v = votes.get(k) or {}
                if v.get("hold_mode"):
                    hm = str(v["hold_mode"])
                    break
            jp = d.get("pnl_pct")
            if not _is_profit_exit(hm, jp):
                continue
            cur = sells.get(prid)
            if cur is None or ts > cur["ts"]:
                sells[prid] = {"ts": ts, "ticker": d.get("ticker"), "market": d.get("market"),
                               "hold_mode": hm, "judge_pnl": jp}

    if not sells:
        return 0

    con = _connect(db_path)
    try:
        # 2) v2 조인
        real: dict[str, dict] = {}
        for r in con.execute(
            "SELECT path_run_id,entry_price,pnl_pct,market_regime,closed_at "
            "FROM v2_learning_performance WHERE closed=1 AND pnl_pct IS NOT NULL"
        ):
            if r[0]:
                real[r[0]] = {"entry": r[1], "realized": r[2], "regime_raw": r[3], "closed_at": r[4]}

        now = datetime.now().isoformat(timespec="seconds")
        n = 0
        for prid, s in sells.items():
            rr = real.get(prid)
            if not rr or rr["entry"] is None:
                continue
            regime = lv.regime_from_consensus_mode(rr.get("regime_raw"))
            sell_date = str(rr.get("closed_at") or s["ts"])[:10]
            sell_key = f"{prid}:{s['ts']}"
            con.execute(
                "INSERT INTO hold_advisor_exit_outcome "
                "(sell_key,path_run_id,market,ticker,sell_ts,sell_date,entry_price,realized_net,"
                " hold_fwd_price,hold_fwd_net,regime,hold_mode,judge_pnl,forward_days,synced_at) "
                "VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,NULL,?) "
                "ON CONFLICT(sell_key) DO UPDATE SET "
                "  entry_price=excluded.entry_price, realized_net=excluded.realized_net, "
                "  regime=excluded.regime, sell_date=excluded.sell_date, synced_at=excluded.synced_at",
                (sell_key, prid, s["market"], s["ticker"], s["ts"], sell_date,
                 rr["entry"], rr["realized"], regime, s["hold_mode"], s["judge_pnl"], now),
            )
            n += 1
        con.commit()
        return n
    finally:
        con.close()


def _yf_symbol(market: str, ticker: str) -> str:
    if str(market or "").upper() == "KR":
        # KOSPI .KS 우선, 안 되면 .KQ는 호출측에서 재시도
        return f"{ticker}.KS"
    return str(ticker)


def backfill_forward(db_path: str | None = None, forward_days: int = DEFAULT_FORWARD_DAYS) -> int:
    """hold_fwd가 비고 forward가 성숙한 행에 대해 yfinance로 HOLD 지속 forward 채움.

    hold_fwd_net = (sell_date+forward_days 거래일 종가 / entry_price − 1)×100.
    반환=채운 행 수. yfinance 미설치/실패는 0(안전).
    """
    try:
        import yfinance as yf
    except Exception:
        return 0

    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT sell_key,market,ticker,sell_date,entry_price FROM hold_advisor_exit_outcome "
            "WHERE hold_fwd_net IS NULL AND entry_price IS NOT NULL"
        ).fetchall()
        if not rows:
            return 0
        now = datetime.now().isoformat(timespec="seconds")
        filled = 0
        for sell_key, market, ticker, sell_date, entry in rows:
            try:
                sd = datetime.fromisoformat(str(sell_date)[:10])
            except Exception:
                continue
            # forward 성숙 가드: sell_date+forward_days 거래일이 아직 안 지났으면 skip
            if (datetime.now() - sd).days < forward_days + 1:
                continue
            start = sd.date()
            end = (sd + timedelta(days=forward_days + 6)).date()  # 거래일 여유
            price = None
            for sym in ([_yf_symbol(market, ticker), f"{ticker}.KQ"] if str(market).upper() == "KR"
                        else [str(ticker)]):
                try:
                    df = yf.download(sym, start=start, end=end, interval="1d",
                                     progress=False, auto_adjust=False)
                    if df is None or df.empty or "Close" not in df:
                        continue
                    closes = list(df["Close"].dropna().values.flatten())
                    # sell_date 이후 forward_days번째 거래일 종가 (인덱스 0=sell_date 당일/익일)
                    if len(closes) > forward_days:
                        price = float(closes[forward_days])
                        break
                except Exception:
                    continue
            if price is None or price <= 0 or not entry or entry <= 0:
                continue
            hold_fwd_net = (price / float(entry) - 1.0) * 100.0
            con.execute(
                "UPDATE hold_advisor_exit_outcome SET hold_fwd_price=?, hold_fwd_net=?, "
                "forward_days=?, synced_at=? WHERE sell_key=?",
                (price, round(hold_fwd_net, 4), forward_days, now, sell_key),
            )
            filled += 1
        con.commit()
        return filled
    finally:
        con.close()


def rescore(store_db: str | None = None, db_path: str | None = None) -> list[dict]:
    """성숙 라벨(hold_fwd_net NOT NULL)을 (market×regime×month) 셀로 채점 → store upsert.

    would_be = realized_net(익절 SELL 실현), actual = hold_fwd_net(HOLD 지속).
    gain>0 = 익절이 HOLD보다 우위(profit_guard valid). gain<0 = 조기절단(invalid_block).
    """
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT market,regime,sell_date,realized_net,hold_fwd_net FROM hold_advisor_exit_outcome "
            "WHERE hold_fwd_net IS NOT NULL AND realized_net IS NOT NULL"
        ).fetchall()
    finally:
        con.close()

    # regime None = 국면 미상(v2 market_regime sync 전). 위조하지 않고 'unknown' 버킷으로
    # 측정만 한다(국면 confound 미분리 — sync 후 collect 재실행하면 정확한 regime로 갱신).
    g: dict[tuple, dict] = defaultdict(lambda: defaultdict(lambda: {"sell": [], "hold": []}))
    for market, regime, sd, realized, hold_fwd in rows:
        bucket = g[(market, str(regime or "unknown"))][str(sd)[:7]]
        bucket["sell"].append(realized)
        bucket["hold"].append(hold_fwd)

    cells = []
    for (market, regime), months in g.items():
        all_sell = [x for m in months.values() for x in m["sell"]]
        all_hold = [x for m in months.values() for x in m["hold"]]
        overall = lv.counterfactual_gain(all_sell, all_hold)
        subs = []
        for m in months.values():
            if len(m["sell"]) >= 3 and len(m["hold"]) >= 3:
                subs.append(lv.counterfactual_gain(m["sell"], m["hold"]))
        sessions = lv.sign_consistency_sessions(subs, overall)
        cells.append(lv.score_cell(LESSON_KEY, market, regime, all_sell, all_hold,
                                   sessions_confirmed=sessions))
    if cells:
        lv.upsert_cells(cells, db_path=store_db)
    return cells


def run_all(store_db: str | None = None, db_path: str | None = None,
            forward_days: int = DEFAULT_FORWARD_DAYS) -> dict[str, int]:
    """수집 + yfinance 백필 + 채점 (통합 도구/주기용)."""
    collected = collect_exit_outcomes(db_path=db_path)
    filled = backfill_forward(db_path=db_path, forward_days=forward_days)
    cells = rescore(store_db=store_db, db_path=db_path)
    return {"collected": collected, "forward_filled": filled, "cells": len(cells)}


def rescore_safe(store_db: str | None = None) -> int:
    """세션마감 hook용 — 예외 삼킴(봇 루프 보호). 토글 게이트는 호출측. rescore만(가벼움)."""
    try:
        return len(rescore(store_db=store_db))
    except Exception:
        return 0


def enabled() -> bool:
    return os.getenv("HOLD_ADVISOR_EXIT_LESSON_ENABLED", "false").lower() == "true"
