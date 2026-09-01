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
**출구 (정본 = 런타임, Codex 09-01 검토로 통일)**: TP12(일봉 high, D0은 종가만)/
  SL25(종가)/BE락4(전일까지 봉우리 기준, 종가 청산)/**보유 = 진입일 포함 7세션
  (D0..D6)** — `expected_maturity_session`의 inclusive 규약이자 SEI·AVAV 실측
  (08-21 진입 → 7세션째 08-31 마감 청산)과 일치. 연구 스크립트들의 D0..D7(8봉)은
  off-by-one이었다(Codex 지적). 수수료 왕복 **0.50%**(봉인 policy cost_pct 정본,
  연구용 0.48%와 구분). 한계: 실체결가·슬리피지·장중 게이트는 재현하지 않는다.
  갭 TP는 TP가 체결 보수 규약.
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
    BAND_HI, BAND_LO, MAX_FLOOR, _key, max21_at,
)

POOL_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
KR_LEDGER = ROOT / "data" / "shadow" / "kr_fallen_shadow.jsonl"
KR_BLIND_LEDGER = ROOT / "data" / "shadow" / "kr_fallen_blindspot_shadow.jsonl"
KR_PRICE_DIR = ROOT / "data" / "price" / "kr"
BOOK_DB = ROOT / "data" / "shadow" / "virtual_books.db"
# ── 정산 계약 정본 (런타임 규약과 통일 — docstring 참조) ─────────────────────
TP, SL, BE = 12.0, -25.0, 4.0
HOLD_SESSIONS = 7        # 진입일 포함 (D0..D6)
FEE_US = 0.50            # 봉인 policy cost_pct
FEE_KR = 0.25            # KR 왕복(수수료+거래세) — kr shadow 계약 라벨 cost0.25와 동일
# KR 규칙 임계 (kr_fallen_gate_report 정본과 동일 — 게이트 카운트 진행 중이라 불변)
R2_DISC_LE, R2_RV20_LE = -25.0, 8.0
R4_GAP_LE, R4_DISC_LE = -4.0, -15.0


def contract_exit_v2(entry: float, win: list[tuple], *, fee: float = FEE_US,
                     be_lock: bool = True) -> tuple[float, str] | None:
    """정본 계약 정산. win = D0..D6 (최대 7봉). 미완결·미발동이면 None.

    BE락은 **전일까지의 봉우리**로 활성 판정한다(당일 순서 모호성 제거 —
    us_swing_exit_counterfactual과 같은 규약). TP·종가 SL/BE 동시 성립 시 TP 우선
    (고가는 장중, SL/BE는 종가 판정이므로). KR은 be_lock=False —
    08-25 결정(KR은 BE락이 역방향이라 미적용).
    """
    if not win or entry <= 0:
        return None
    peak = (win[0][4] - entry) / entry * 100.0  # D0은 종가만 (체결 전 고가 오염 방지)
    for i, (_d, _o, hi, _lo, c, _v) in enumerate(win[:HOLD_SESSIONS]):
        hip = (hi - entry) / entry * 100.0 if i > 0 else (c - entry) / entry * 100.0
        cp = (c - entry) / entry * 100.0
        if hip >= TP:
            return TP - fee, "TP"
        if cp <= SL:
            return cp - fee, "SL"
        if be_lock and peak >= BE and cp <= 0:
            return cp - fee, "BE"
        peak = max(peak, hip)
    if len(win) < HOLD_SESSIONS:
        return None
    return (win[HOLD_SESSIONS - 1][4] - entry) / entry * 100.0 - fee, "D_MAT"


_KR_BAR_CACHE: dict[str, list[tuple]] = {}


def bars_kr(ticker: str) -> list[tuple]:
    """KR 일봉 (BOM 필수 — data/price/kr CSV도 BOM을 단다)."""
    key = str(ticker)
    if key not in _KR_BAR_CACHE:
        import csv
        rows: list[tuple] = []
        path = KR_PRICE_DIR / f"kr_{key}.csv"
        if path.exists():
            with path.open(encoding="utf-8-sig", newline="") as fh:
                for r in csv.reader(fh):
                    if len(r) >= 6 and r[0][:2] == "20":
                        try:
                            rows.append((r[0], float(r[1]), float(r[2]), float(r[3]),
                                         float(r[4]), float(r[5])))
                        except ValueError:
                            continue
        _KR_BAR_CACHE[key] = sorted(rows)
    return _KR_BAR_CACHE[key]
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
    # ── Codex 제안 (09-01, gpt-5.6-sol 검토) ──────────────────────────────
    {"id": "us_wide_nomax",  "universe": "wide", "pick": "dvol_desc", "daily_cap": 1,
     "slots": 7,  "order_krw": 540_000, "capital_krw": 10_000_000, "max_floor": False,
     "note": "S6 — 밴드만(MAX 게이트 제거). MAX 부호 혼재 검정. 반증: S2 대비 <=0 30건"},
    {"id": "us_wide_dvol_k3", "universe": "wide", "pick": "dvol_desc", "daily_cap": 3,
     "slots": 21, "order_krw": 540_000, "capital_krw": 20_000_000,
     "note": "S7 — top3 균등. K=1(S2)과 cap10(S3) 사이 용량 곡선. 슬롯별 한계 판정"},
    {"id": "us_wide_lowdens", "universe": "wide", "pick": "dvol_desc", "daily_cap": 1,
     "slots": 7,  "order_krw": 540_000, "capital_krw": 10_000_000, "max_passers": 10,
     "note": "S8 — 통과 후보>10인 고밀도 세션 no-trade (후보 폭=나쁜 날 관측의 검정)"},
    {"id": "kr_r2",          "universe": "kr",   "pick": "disc_deep", "daily_cap": 2,
     "slots": 6,  "order_krw": 220_000, "capital_krw": 5_000_000, "kr_rule": "r2",
     "note": "S9 — KR R2 단독(disc<=-25 & rv20<=8). 할인깊은순(08-04 검증 통과 랭킹)"},
    {"id": "kr_r4x",         "universe": "kr",   "pick": "disc_deep", "daily_cap": 2,
     "slots": 6,  "order_krw": 220_000, "capital_krw": 5_000_000, "kr_rule": "r4x",
     "note": "S10 — KR R4∖R2 순증분(gap<=-4 & disc<=-15, R2 미충족만). R4 추가가치 검정"},
]


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS strategies (
        id TEXT PRIMARY KEY, universe TEXT, pick TEXT, daily_cap INTEGER,
        slots INTEGER, order_krw REAL, capital_krw REAL, note TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS trades (
        strategy_id TEXT, session_date TEXT, ticker TEXT,
        entry_price REAL, notional_krw REAL, backfill INTEGER, pick_pos INTEGER,
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


def band_max_pass(cands: list[dict], *, max_floor: bool = True) -> list[dict]:
    return [c for c in cands
            if c["dvol"] is not None and BAND_LO <= c["dvol"] < BAND_HI
            and (not max_floor or c["max21"] is None or c["max21"] >= MAX_FLOOR)]


def load_kr_sessions() -> dict[str, list[dict]]:
    """KR fallen 원장(+사각) → 세션별 후보. session_date = 신호일(진입은 다음 세션).

    R2/R4는 게이트 정본 임계로 재판정한다(원장 flags가 아니라 feats에서 —
    임계 개정 이력이 flags에 소급 안 되므로 feats가 정본).
    """
    sessions: dict[str, dict[str, dict]] = defaultdict(dict)
    for ledger in (KR_LEDGER, KR_BLIND_LEDGER):
        if not ledger.exists():
            continue
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            f = row.get("feats") or {}
            disc, rv, gap = f.get("ma20_disc"), f.get("rv20"), f.get("gap")
            r2 = disc is not None and rv is not None and disc <= R2_DISC_LE and rv <= R2_RV20_LE
            r4 = disc is not None and gap is not None and gap <= R4_GAP_LE and disc <= R4_DISC_LE
            if not (r2 or r4):
                continue
            sd, tk = str(row.get("session_date")), str(row.get("ticker"))
            sessions[sd].setdefault(tk, {
                "ticker": tk, "disc": float(disc), "r2": bool(r2), "r4x": bool(r4 and not r2),
            })
    return {sd: list(by.values()) for sd, by in sessions.items()}


def strategy_pick_key(s: dict, c: dict) -> float:
    if s.get("pick") == "disc_deep":
        return c["disc"]  # 할인 깊은순(가장 음수 먼저) — 08-04 검증 통과 랭킹
    return _key(s["pick"], c)


def entry_of(ticker: str, session_date: str, *, market: str = "US") -> tuple[float, list[tuple]] | None:
    """진입가·경로 창. US: session_date가 진입 세션(풀 규약). KR: session_date는
    신호일이라 **다음 거래 세션** 시가 진입(핸드오프 규약과 동일)."""
    if market == "KR":
        b = bars_kr(ticker)
        ei = next((i for i, x in enumerate(b) if x[0] > str(session_date)), None)
    else:
        b = bars(ticker)
        ei = next((i for i, x in enumerate(b) if x[0] == str(session_date)), None)
    if ei is None:
        return None
    win = b[ei: ei + HOLD_SESSIONS]  # D0..D6 (진입일 포함 7세션 — 런타임 정본)
    if not win or not win[0][1] or win[0][1] <= 0:
        return None
    return float(win[0][1]), win


def strategy_passers(s: dict, sessions_us: dict, sessions_kr: dict, sd: str) -> list[dict]:
    """전략의 세션 통과 후보 (선별 파이프 전체 적용, 픽 전 단계)."""
    if s["universe"] == "kr":
        cands = sessions_kr.get(sd, [])
        return [c for c in cands if c.get(s["kr_rule"])]
    pool = sessions_us.get(sd, [])
    if s["universe"] == "live":
        pool = [c for c in pool if c["in_pool"]]
    passers = band_max_pass(pool, max_floor=bool(s.get("max_floor", True)))
    if s.get("max_passers") and len(passers) > int(s["max_passers"]):
        return []  # S8 — 고밀도 세션 no-trade
    return passers


def open_new_trades(con: sqlite3.Connection, sessions_us: dict[str, list[dict]],
                    sessions_kr: dict[str, list[dict]]) -> int:
    opened = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for s in STRATEGIES:
        market = "KR" if s["universe"] == "kr" else "US"
        all_dates = sessions_kr if s["universe"] == "kr" else sessions_us
        done = {r[0] for r in con.execute(
            "SELECT DISTINCT session_date FROM trades WHERE strategy_id=?", (s["id"],))}
        cash = book_cash(con, s)
        for sd in sorted(all_dates):
            if sd < BACKFILL_START or sd in done:
                continue
            passers = strategy_passers(s, sessions_us, sessions_kr, sd)
            if not passers:
                continue
            if s["pick"] == "all":
                picks = passers[: s["daily_cap"]]
            else:
                picks = sorted(passers, key=lambda c: strategy_pick_key(s, c))[: s["daily_cap"]]
            open_n = con.execute(
                "SELECT COUNT(*) FROM trades WHERE strategy_id=? AND status='OPEN'",
                (s["id"],)).fetchone()[0]
            for pos, c in enumerate(picks, start=1):
                if open_n >= s["slots"]:
                    break
                if cash < s["order_krw"]:
                    print(f"[VIRTUAL] {s['id']} {sd} 현금 소진 — 진입 생략(용량 회계)")
                    break
                eo = entry_of(c["ticker"], sd, market=market)
                if eo is None:
                    continue
                entry, _win = eo
                con.execute(
                    """INSERT OR IGNORE INTO trades (strategy_id, session_date, ticker,
                           entry_price, notional_krw, backfill, pick_pos, status, opened_at)
                       VALUES (?,?,?,?,?,?,?, 'OPEN', ?)""",
                    (s["id"], sd, c["ticker"], entry, s["order_krw"],
                     1 if sd < FORWARD_START else 0, pos, now))
                if con.execute("SELECT changes()").fetchone()[0]:
                    opened += 1
                    open_n += 1
                    cash -= s["order_krw"]
    con.commit()
    return opened


def settle_open_trades(con: sqlite3.Connection) -> int:
    settled = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    by_id = {s["id"]: s for s in STRATEGIES}
    for sid, sd, tk, entry, notional in con.execute(
            "SELECT strategy_id, session_date, ticker, entry_price, notional_krw "
            "FROM trades WHERE status='OPEN'").fetchall():
        s = by_id.get(sid)
        if s is None:
            continue
        market = "KR" if s["universe"] == "kr" else "US"
        eo = entry_of(tk, sd, market=market)
        if eo is None:
            continue
        _e, win = eo
        res = contract_exit_v2(float(entry), win,
                               fee=FEE_KR if market == "KR" else FEE_US,
                               be_lock=(market != "KR"))
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
            b = bars_kr(str(tk)) if s["universe"] == "kr" else bars(str(tk))
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


def null_percentile(con: sqlite3.Connection, s: dict, sessions_us: dict,
                    sessions_kr: dict, n_perm: int = 2000) -> float | None:
    """C0 널 — 전략이 거래한 세션에서 같은 모집단·같은 건수 무작위 픽의 평균 분포
    대비 실측 평균의 백분위. seed 고정(Codex 제안). 정산 5건 미만이면 None."""
    import random
    market = "KR" if s["universe"] == "kr" else "US"
    traded = con.execute(
        "SELECT session_date, COUNT(*), AVG(net_pct) FROM trades "
        "WHERE strategy_id=? AND status='CLOSED' GROUP BY session_date", (s["id"],)).fetchall()
    if sum(r[1] for r in traded) < 5:
        return None
    per_sess: list[tuple[list[float], int]] = []
    for sd, k, _avg in traded:
        nets = []
        for c in strategy_passers(s, sessions_us, sessions_kr, str(sd)):
            eo = entry_of(c["ticker"], str(sd), market=market)
            if eo is None:
                continue
            res = contract_exit_v2(eo[0], eo[1], fee=FEE_KR if market == "KR" else FEE_US,
                                   be_lock=(market != "KR"))
            if res is not None:
                nets.append(res[0])
        if nets:
            per_sess.append((nets, min(int(k), len(nets))))
    if not per_sess:
        return None
    realized_mean = con.execute(
        "SELECT AVG(net_pct) FROM trades WHERE strategy_id=? AND status='CLOSED'",
        (s["id"],)).fetchone()[0]
    rng = random.Random(20260901)
    means = []
    for _ in range(n_perm):
        picked = [x for nets, k in per_sess for x in rng.sample(nets, k)]
        if picked:
            means.append(sum(picked) / len(picked))
    if not means:
        return None
    return 100.0 * sum(1 for m in means if m < float(realized_mean)) / len(means)


def report(con: sqlite3.Connection, sessions_us: dict | None = None,
           sessions_kr: dict | None = None) -> None:
    print("=== [VIRTUAL] 가상 북 현황 — 실계좌 아님, 가상 자본 ===")
    print(f"{'전략':16s} {'자본':>7s} {'실현손익':>10s} {'미결제':>4s} {'MTM':>9s} "
          f"{'정산':>4s} {'승률':>4s} {'평균net':>8s} {'백필/포워드':>10s} {'널백분위':>7s}")
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
        np_s = "  -  "
        if sessions_us is not None:
            pct = null_percentile(con, s, sessions_us, sessions_kr or {})
            if pct is not None:
                np_s = f"{pct:5.1f}"
        print(f"{s['id']:16s} {s['capital_krw']/1e4:6.0f}만 {realized:+9.0f}원 {open_n:4d} "
              f"{mtm:+8.0f}원 {len(nets):4d} {wr:3.0f}% {avg:+7.2f}% {nb:5d}/{len(nets)-nb} {np_s:>7s}")
    # 슬롯 분해 — 용량 전략(K>1)은 총합이 아니라 한계 슬롯으로 판정 (Codex S7 규약)
    print("\n[슬롯 분해 — K>1 전략]")
    for s in STRATEGIES:
        if int(s["daily_cap"]) <= 1:
            continue
        rows = con.execute(
            "SELECT pick_pos, COUNT(*), AVG(net_pct), SUM(pnl_krw) FROM trades "
            "WHERE strategy_id=? AND status='CLOSED' GROUP BY pick_pos ORDER BY pick_pos",
            (s["id"],)).fetchall()
        if rows:
            cells = " | ".join(f"슬롯{p}: n={n} {a:+.2f}% {int(t):+,}원" for p, n, a, t in rows if p)
            print(f"  {s['id']:16s} {cells}")
    print("\n[승격 게이트] forward(09-01 이후 진입) 표본만 판정에 쓴다. 백필은 참고 전용.")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    BOOK_DB.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(BOOK_DB, timeout=30)) as con:
        ensure_schema(con)
        sync_strategies(con)
        if cmd == "run":
            sessions_us = load_sessions()
            sessions_kr = load_kr_sessions()
            opened = open_new_trades(con, sessions_us, sessions_kr)
            settled = settle_open_trades(con)
            mark_books(con)
            print(f"[VIRTUAL] 진입 {opened}건 / 정산 {settled}건")
            report(con, sessions_us, sessions_kr)
        elif cmd == "report":
            report(con, load_sessions(), load_kr_sessions())
        else:
            print("사용: virtual_books.py [run|report]")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
