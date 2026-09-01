#!/usr/bin/env python3
"""가상 북 엔진 — 다전략 병행 가상 운용 원장 (2026-09-01 운영자 결정).

운영자 결정: 실계좌는 유지하되 신규 실매수는 전면 중단(SUBMIT off), 대신
**가상 자본으로 다수 전략을 병행 운용**하며 검증하고, 완성되면 실투자로 복귀한다.
KIS 모의서버는 쓰지 않는다 — 실데이터(가격 CSV·후보 풀)로 우리가 정산한다.

== 규약 ==

**자본**: 전략(북)별 가상 자본. ⚠️ 전부 가상이다 — 실계좌·실손익과 무관하며
  모든 산출물에 [VIRTUAL] 표기를 남긴다.
**진입**: session_date 시가(연구 표준 규약). 수량 대신 명목 KRW(건당 주문액)로
  회계한다 — pnl_krw = 주문액 × net%/100. 현금 부족이면 진입 생략(기록).
**출구**: 현행 계약 TP12(일봉 high, D0은 종가만)/SL25(종가)/BE락4(종가)/D7,
  수수료 왕복 0.48%. 08-30 확립 규약(실거래 재현 6/9) — 근사의 한계 명시:
  실체결가·슬리피지·게이트(추격·갭)는 재현하지 않는다.
**모집단 2종**: live(in_pool=1 = 스크리너 quota 통과 = 현 시스템이 실제로 사는
  풀) / wide(eligible=1 전체 = 공급 확대 가정). 09-01 실증으로 두 모집단이
  다름이 확인됐다(quota가 day_losers를 10/세션으로 자름).
**멱등**: (strategy_id, session_date, ticker) PK. 재실행해도 중복 없음.
**승격 게이트(사전등록)**: 어떤 가상 전략도 이 원장만으로 라이브로 가지 않는다.
  게이트는 forward 표본(백필 제외)이 쌓인 뒤 별도 사전등록으로 정한다 —
  Codex 제안(수신 대기)과 합쳐 확정.

사용:
  python tools/virtual_books.py run       # 신규 세션 진입 + 정산 (관측기 ⑤)
  python tools/virtual_books.py report    # 북 요약
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from research_early_exit_no_bump import bars  # noqa: E402
from research_pick_simulation import (  # noqa: E402
    BAND_HI, BAND_LO, FEE, MAX_FLOOR, RULES, _key, contract_net_d7, max21_at,
)

POOL_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
BOOK_DB = ROOT / "data" / "shadow" / "virtual_books.db"
BACKFILL_START = "2026-08-12"  # 풀 원장 시작. 이 구간은 backfill=1로 표기(forward 아님)
FORWARD_START = "2026-09-01"   # 가상 운용 전환일 — 승격 판정은 이 이후만 센다

# ── 전략 정의 (v1 매트릭스 — 나+Codex 구성, 추가는 여기에) ──────────────────
# universe: live=in_pool=1(현 시스템 실제 공급) / wide=eligible=1(공급 확대 가정)
# pick: research_pick_simulation.RULES 중 하나 / "all"=통과분 전량(알파=용량 실증)
STRATEGIES: list[dict] = [
    {"id": "us_live_dvol",   "universe": "live", "pick": "dvol_desc", "daily_cap": 1,
     "slots": 7,  "order_krw": 540_000, "capital_krw": 10_000_000,
     "note": "현행 라이브 미러(quota 10 공급) — G5 rehearsal 대조군"},
    {"id": "us_wide_dvol",   "universe": "wide", "pick": "dvol_desc", "daily_cap": 1,
     "slots": 7,  "order_krw": 540_000, "capital_krw": 10_000_000,
     "note": "공급 확대안 — 승격 1순위 후보"},
    {"id": "us_wide_all",    "universe": "wide", "pick": "all",       "daily_cap": 10,
     "slots": 70, "order_krw": 540_000, "capital_krw": 50_000_000,
     "note": "통과분 전량 매수 — 알파=용량 가설의 직접 실증"},
    {"id": "us_wide_dvolasc", "universe": "wide", "pick": "dvol_asc", "daily_cap": 1,
     "slots": 7,  "order_krw": 540_000, "capital_krw": 10_000_000,
     "note": "교재 지지 방향(널 93.5)"},
    {"id": "us_wide_ibs",    "universe": "wide", "pick": "ibs_hi",   "daily_cap": 1,
     "slots": 7,  "order_krw": 540_000, "capital_krw": 10_000_000,
     "note": "실거래 승자 프로필 축"},
    {"id": "us_wide_chg",    "universe": "wide", "pick": "chg_hi",   "daily_cap": 1,
     "slots": 7,  "order_krw": 540_000, "capital_krw": 10_000_000,
     "note": "덜 빠진 것 우선(교재 널 88.1)"},
    {"id": "us_wide_maxlo",  "universe": "wide", "pick": "max_lo",   "daily_cap": 1,
     "slots": 7,  "order_krw": 540_000, "capital_krw": 10_000_000,
     "note": "승자 프로필 MAX 낮음"},
]


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS strategies (
        id TEXT PRIMARY KEY, universe TEXT, pick TEXT, daily_cap INTEGER,
        slots INTEGER, order_krw REAL, capital_krw REAL, note TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS trades (
        strategy_id TEXT, session_date TEXT, ticker TEXT,
        entry_price REAL, notional_krw REAL, backfill INTEGER,
        status TEXT, exit_reason TEXT, exit_index INTEGER,
        net_pct REAL, pnl_krw REAL, opened_at TEXT, settled_at TEXT,
        PRIMARY KEY (strategy_id, session_date, ticker));
    CREATE TABLE IF NOT EXISTS book_daily (
        strategy_id TEXT, asof TEXT, cash_krw REAL, open_n INTEGER,
        open_mtm_krw REAL, realized_pnl_krw REAL, equity_krw REAL,
        PRIMARY KEY (strategy_id, asof));
    """)
    con.commit()


def sync_strategies(con: sqlite3.Connection) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for s in STRATEGIES:
        con.execute(
            """INSERT INTO strategies (id, universe, pick, daily_cap, slots, order_krw,
                   capital_krw, note, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET universe=excluded.universe, pick=excluded.pick,
                   daily_cap=excluded.daily_cap, slots=excluded.slots,
                   order_krw=excluded.order_krw, note=excluded.note""",
            (s["id"], s["universe"], s["pick"], s["daily_cap"], s["slots"],
             s["order_krw"], s["capital_krw"], s["note"], now))
    con.commit()


def load_sessions() -> dict[str, list[dict]]:
    """후보 풀 → 세션별 후보(신호일 특징 포함). observe_pick_rules와 같은 규약."""
    with closing(sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True, timeout=10)) as pcon:
        rows = pcon.execute(
            """SELECT session_date, ticker, chg_pct, dollar_vol, in_pool
               FROM candidate_pool_all WHERE eligible=1 ORDER BY session_date""").fetchall()
    sessions: dict[str, list[dict]] = defaultdict(list)
    for sd, tk, chg, dvol, in_pool in rows:
        t = str(tk).upper()
        b = bars(t)
        si = None
        for i in range(len(b) - 1, -1, -1):
            if b[i][0] < str(sd):
                si = i
                break
        if si is None or si < 1:
            continue
        _d, _o, hi, lo, c, v = b[si]
        sessions[str(sd)].append({
            "ticker": t,
            "ibs": (c - lo) / (hi - lo) * 100.0 if hi > lo else None,
            "chg": float(chg) if chg is not None else (
                100.0 * (c / b[si - 1][4] - 1.0) if b[si - 1][4] else None),
            "dvol": float(dvol) / 1e6 if dvol is not None else (c * v / 1e6 if v else None),
            "max21": max21_at(b, si),
            "in_pool": int(in_pool or 0),
        })
    return sessions


def band_max_pass(cands: list[dict]) -> list[dict]:
    return [c for c in cands
            if c["dvol"] is not None and BAND_LO <= c["dvol"] < BAND_HI
            and (c["max21"] is None or c["max21"] >= MAX_FLOOR)]


def entry_of(ticker: str, session_date: str) -> tuple[float, list[tuple]] | None:
    b = bars(ticker)
    ei = next((i for i, x in enumerate(b) if x[0] == str(session_date)), None)
    if ei is None:
        return None
    win = b[ei: ei + 8]  # D0..D7
    if not win or not win[0][1] or win[0][1] <= 0:
        return None
    return float(win[0][1]), win


def open_new_trades(con: sqlite3.Connection, sessions: dict[str, list[dict]]) -> int:
    opened = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for s in STRATEGIES:
        done = {r[0] for r in con.execute(
            "SELECT DISTINCT session_date FROM trades WHERE strategy_id=?", (s["id"],))}
        cash = book_cash(con, s)
        for sd in sorted(sessions):
            if sd < BACKFILL_START or sd in done:
                continue
            pool = [c for c in sessions[sd] if c["in_pool"]] if s["universe"] == "live" else sessions[sd]
            passers = band_max_pass(pool)
            if not passers:
                continue
            if s["pick"] == "all":
                picks = passers[: s["daily_cap"]]
            else:
                picks = sorted(passers, key=lambda c: _key(s["pick"], c))[: s["daily_cap"]]
            open_n = con.execute(
                "SELECT COUNT(*) FROM trades WHERE strategy_id=? AND status='OPEN'",
                (s["id"],)).fetchone()[0]
            for c in picks:
                if open_n >= s["slots"]:
                    break
                if cash < s["order_krw"]:
                    break  # 가상 현금 소진 — 실계좌와 같은 제약을 재현
                eo = entry_of(c["ticker"], sd)
                if eo is None:
                    continue
                entry, _win = eo
                con.execute(
                    """INSERT OR IGNORE INTO trades (strategy_id, session_date, ticker,
                           entry_price, notional_krw, backfill, status, opened_at)
                       VALUES (?,?,?,?,?,?, 'OPEN', ?)""",
                    (s["id"], sd, c["ticker"], entry, s["order_krw"],
                     1 if sd < FORWARD_START else 0, now))
                if con.execute("SELECT changes()").fetchone()[0]:
                    opened += 1
                    open_n += 1
                    cash -= s["order_krw"]
    con.commit()
    return opened


def settle_open_trades(con: sqlite3.Connection) -> int:
    settled = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for sid, sd, tk, entry, notional in con.execute(
            "SELECT strategy_id, session_date, ticker, entry_price, notional_krw "
            "FROM trades WHERE status='OPEN'").fetchall():
        eo = entry_of(tk, sd)
        if eo is None:
            continue
        _e, win = eo
        res = contract_net_d7(float(entry), win)
        if res is None:
            continue  # 창 미완결·미발동 — 다음 실행에서 재시도
        net, reason = res
        con.execute(
            """UPDATE trades SET status='CLOSED', exit_reason=?, net_pct=?,
                   pnl_krw=?, settled_at=? WHERE strategy_id=? AND session_date=? AND ticker=?""",
            (reason, round(net, 4), round(float(notional) * net / 100.0, 2), now, sid, sd, tk))
        settled += 1
    con.commit()
    return settled


def book_cash(con: sqlite3.Connection, s: dict) -> float:
    """현금 = 자본 + 실현손익 − 미결제 명목."""
    realized = con.execute(
        "SELECT COALESCE(SUM(pnl_krw),0) FROM trades WHERE strategy_id=? AND status='CLOSED'",
        (s["id"],)).fetchone()[0]
    open_notional = con.execute(
        "SELECT COALESCE(SUM(notional_krw),0) FROM trades WHERE strategy_id=? AND status='OPEN'",
        (s["id"],)).fetchone()[0]
    return float(s["capital_krw"]) + float(realized) - float(open_notional)


def mark_books(con: sqlite3.Connection) -> None:
    asof = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for s in STRATEGIES:
        realized = con.execute(
            "SELECT COALESCE(SUM(pnl_krw),0) FROM trades WHERE strategy_id=? AND status='CLOSED'",
            (s["id"],)).fetchone()[0]
        open_rows = con.execute(
            "SELECT ticker, entry_price, notional_krw FROM trades "
            "WHERE strategy_id=? AND status='OPEN'", (s["id"],)).fetchall()
        mtm = 0.0
        for tk, entry, notional in open_rows:
            b = bars(str(tk))
            if b and entry:
                mtm += float(notional) * (b[-1][4] / float(entry) - 1.0)
        cash = book_cash(con, s)
        equity = cash + sum(float(r[2]) for r in open_rows) + mtm
        con.execute(
            """INSERT INTO book_daily (strategy_id, asof, cash_krw, open_n, open_mtm_krw,
                   realized_pnl_krw, equity_krw) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(strategy_id, asof) DO UPDATE SET cash_krw=excluded.cash_krw,
                   open_n=excluded.open_n, open_mtm_krw=excluded.open_mtm_krw,
                   realized_pnl_krw=excluded.realized_pnl_krw, equity_krw=excluded.equity_krw""",
            (s["id"], asof, round(cash, 2), len(open_rows), round(mtm, 2),
             round(float(realized), 2), round(equity, 2)))
    con.commit()


def report(con: sqlite3.Connection) -> None:
    print("=== [VIRTUAL] 가상 북 현황 — 실계좌 아님, 가상 자본 ===")
    print(f"{'전략':16s} {'자본':>7s} {'실현손익':>10s} {'미결제':>4s} {'MTM':>9s} "
          f"{'정산':>4s} {'승률':>4s} {'평균net':>8s} {'백필/포워드':>10s}")
    for s in STRATEGIES:
        closed = con.execute(
            "SELECT net_pct, backfill FROM trades WHERE strategy_id=? AND status='CLOSED'",
            (s["id"],)).fetchall()
        nets = [r[0] for r in closed]
        nb = sum(1 for r in closed if r[1])
        realized = sum(float(n) * s["order_krw"] / 100.0 for n in nets)
        open_n = con.execute(
            "SELECT COUNT(*) FROM trades WHERE strategy_id=? AND status='OPEN'",
            (s["id"],)).fetchone()[0]
        row = con.execute(
            "SELECT open_mtm_krw FROM book_daily WHERE strategy_id=? ORDER BY asof DESC LIMIT 1",
            (s["id"],)).fetchone()
        mtm = float(row[0]) if row else 0.0
        wr = 100.0 * sum(1 for n in nets if n > 0) / len(nets) if nets else 0.0
        avg = sum(nets) / len(nets) if nets else 0.0
        print(f"{s['id']:16s} {s['capital_krw']/1e4:6.0f}만 {realized:+9.0f}원 {open_n:4d} "
              f"{mtm:+8.0f}원 {len(nets):4d} {wr:3.0f}% {avg:+7.2f}% {nb:5d}/{len(nets)-nb}")
    print("\n[승격 게이트] forward(09-01 이후 진입) 표본만 판정에 쓴다. 백필은 참고 전용.")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    BOOK_DB.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(BOOK_DB, timeout=30)) as con:
        ensure_schema(con)
        sync_strategies(con)
        if cmd == "run":
            sessions = load_sessions()
            opened = open_new_trades(con, sessions)
            settled = settle_open_trades(con)
            mark_books(con)
            print(f"[VIRTUAL] 진입 {opened}건 / 정산 {settled}건")
            report(con)
        elif cmd == "report":
            report(con)
        else:
            print("사용: virtual_books.py [run|report]")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
