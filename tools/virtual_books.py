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
import os
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
ENTRY_SKIP_LEDGER = ROOT / "data" / "shadow" / "virtual_books_entry_skips.jsonl"
PRICE_MARKER = {m: ROOT / "state" / f"price_update_marker_{m}.json" for m in ("KR", "US")}
# ── 정산 계약 정본 (런타임 규약과 통일 — docstring 참조) ─────────────────────
TP, SL, BE = 12.0, -25.0, 4.0
HOLD_SESSIONS = 7        # 진입일 포함 (D0..D6)
FEE_US = 0.50            # 봉인 policy cost_pct
FEE_KR = 0.25            # KR 왕복(수수료+거래세) — kr shadow 계약 라벨 cost0.25와 동일
# KR 규칙 임계 (kr_fallen_gate_report 정본과 동일 — 게이트 카운트 진행 중이라 불변)
R2_DISC_LE, R2_RV20_LE = -25.0, 8.0
R4_GAP_LE, R4_DISC_LE = -4.0, -15.0


def contract_exit_v2(entry: float, win: list[tuple], *, fee: float = FEE_US,
                     be_lock: bool = True, tp: float = TP,
                     sl: float = SL, hold: int = HOLD_SESSIONS) -> tuple[float, str] | None:
    """정본 계약 정산. win = D0..D6 (최대 7봉). 미완결·미발동이면 None.

    BE락은 **전일까지의 봉우리**로 활성 판정한다(당일 순서 모호성 제거 —
    us_swing_exit_counterfactual과 같은 규약). TP·종가 SL/BE 동시 성립 시 TP 우선
    (고가는 장중, SL/BE는 종가 판정이므로). KR은 be_lock=False —
    08-25 결정(KR은 BE락이 역방향이라 미적용).
    """
    if not win or entry <= 0:
        return None
    peak = (win[0][4] - entry) / entry * 100.0  # D0은 종가만 (체결 전 고가 오염 방지)
    for i, (_d, _o, hi, _lo, c, _v) in enumerate(win[:hold]):
        hip = (hi - entry) / entry * 100.0 if i > 0 else (c - entry) / entry * 100.0
        cp = (c - entry) / entry * 100.0
        if hip >= tp:
            return tp - fee, "TP"
        if cp <= sl:
            return cp - fee, "SL"
        if be_lock and peak >= BE and cp <= 0:
            return cp - fee, "BE"
        peak = max(peak, hip)
    if len(win) < hold:
        return None
    return (win[hold - 1][4] - entry) / entry * 100.0 - fee, "D_MAT"


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
    # v2 (2026-09-02, 게이트 개정 1): v1은 daily_cap=10 무순서 컷이라 후보>10 세션에서
    # "전량"이 아니었다(8월 8세션 중 4세션, 104건 중 36건 누락 — Codex 실증). v1 행은
    # DB에 남기되(backfill 전용) 새 id로 진짜 전량을 다시 쌓는다. forward 표본은 0에서 시작.
    {"id": "us_wide_all",    "universe": "wide", "pick": "all",       "daily_cap": 10,
     "slots": 70, "order_krw": 540_000, "capital_krw": 50_000_000, "retired": True,
     "note": "[RETIRED 09-02] cap10 무순서 컷 — 잔여 OPEN 정산만 진행, 판정·신규진입 제외"},
    {"id": "us_wide_all_v2", "universe": "wide", "pick": "all",       "daily_cap": 999,
     "slots": 999, "order_krw": 540_000, "capital_krw": 100_000_000,
     "note": "통과분 전량 매수(진짜 전량, cap 없음) — 알파=용량 가설의 직접 실증. v1(cap10) 대체"},
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
    # ── KR 공시 이벤트 family (09-03 DART 12개월 재생 → 09-04 편입, 운영자 "둘 다 반영·진행") ──
    {"id": "kr_bonus_issue",      "universe": "krevent",   "pick": "all", "daily_cap": 2,
     "slots": 8,  "order_krw": 220_000, "capital_krw": 5_000_000, "tp": 25.0, "sl": -10.0, "hold": 20,
     "note": "F6 — 무상증자결정 다음날 시가 매수·20일 드리프트 (재생 n=27: 5일 +7.4%/20일 +12.4%, 중앙 +9.8%, 5일 −10%↓ 3.7%)"},
    {"id": "kr_limitup_catalyst", "universe": "krlimitup", "pick": "all", "daily_cap": 1,
     "slots": 3,  "order_krw": 100_000, "capital_krw": 1_000_000, "tp": 100.0, "sl": -25.0, "hold": 20,
     "note": "F7 — 상한가(+29%↑) & 당일 촉매공시(공급계약/무상증자/자사주) 다음날 시가, 복권 슬롯 (무필터 상한가 20일 내 +100% 터치 10.3%)"},
    # ── 독립 가설 3종 (09-01 오후, 운영자 '하고 싶은 것 다' 지시) ──────────
    {"id": "us_slow_fallen", "universe": "slowus", "pick": "cum5_deep", "daily_cap": 1,
     "slots": 7,  "order_krw": 540_000, "capital_krw": 10_000_000,
     "note": "S11 — 5일 -12% 완만하락(단일 -5% 없음, day_losers 배타 공급). 사각 검정"},
    {"id": "us_wide_tp20",   "universe": "wide", "pick": "dvol_desc", "daily_cap": 1,
     "slots": 7,  "order_krw": 540_000, "capital_krw": 10_000_000, "tp": 20.0,
     "note": "S12 — 상방 절단 완화(TP20). 신규 코호트 출구 검정(구 코호트 기각과 별개)"},
    {"id": "us_wide_noearn", "universe": "wide", "pick": "dvol_desc", "daily_cap": 1,
     "slots": 7,  "order_krw": 540_000, "capital_krw": 10_000_000, "no_earnings": True,
     "forward_only": True,
     "note": "S13 — 어닝 ±2일 하락 배제(정보성 하락 무반등 가설). 캘린더가 forward만 커버"},
    # ── 패밀리 B (2026-09-01 사전등록 — F0 15개와 별도 family, 자체 널·게이트) ──
    {"id": "b2_leader_pb",   "universe": "lpus", "pick": "ret60_desc", "daily_cap": 1,
     "slots": 7,  "order_krw": 540_000, "capital_krw": 10_000_000,
     "tp": 8.0, "sl": -8.0,
     "note": "B2 — 주도주 눌림목(ret60>=30%·MA50 위·20일고점 -4~-10% 되돌림). "
             "추세주 차익실현 물량 수확. 반증: forward 30건 클러스터 net<=0 또는 널<50"},
]


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS strategies (
        id TEXT PRIMARY KEY, universe TEXT, pick TEXT, daily_cap INTEGER,
        slots INTEGER, order_krw REAL, capital_krw REAL, note TEXT, created_at TEXT,
        contract_hash TEXT, code_commit TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS trades (
        strategy_id TEXT, session_date TEXT, ticker TEXT,
        entry_price REAL, notional_krw REAL, backfill INTEGER, pick_pos INTEGER,
        status TEXT, exit_reason TEXT, exit_index INTEGER,
        net_pct REAL, pnl_krw REAL, opened_at TEXT, settled_at TEXT, meta TEXT,
        PRIMARY KEY (strategy_id, session_date, ticker));
    CREATE TABLE IF NOT EXISTS book_daily (
        strategy_id TEXT, asof TEXT, cash_krw REAL, open_n INTEGER,
        open_mtm_krw REAL, realized_pnl_krw REAL, equity_krw REAL,
        PRIMARY KEY (strategy_id, asof));
    """)
    # 마이그레이션 — 기존 DB에 계보 컬럼 추가 (09-01 v1.4)
    have = {r[1] for r in con.execute("PRAGMA table_info(strategies)")}
    for col in ("contract_hash", "code_commit", "updated_at"):
        if col not in have:
            con.execute(f"ALTER TABLE strategies ADD COLUMN {col} TEXT")
    if "meta" not in {r[1] for r in con.execute("PRAGMA table_info(trades)")}:
        con.execute("ALTER TABLE trades ADD COLUMN meta TEXT")
    con.commit()


def _contract_hash(s: dict) -> str:
    """전략 계약 지문 — 전략 dict + 엔진 정산 상수. Codex 취약점 ① 증거 계보.

    바뀌면 '변경 열차' 위반 후보다: 파라미터·정산 코드는 묶음 변경 + 새 epoch가 원칙."""
    import hashlib
    payload = json.dumps({**{k: v for k, v in s.items() if k not in ("note", "retired")},
                          "_engine": [TP, SL, BE, HOLD_SESSIONS, FEE_US, FEE_KR,
                                      R2_DISC_LE, R2_RV20_LE, R4_GAP_LE, R4_DISC_LE]},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _code_commit() -> str:
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def sync_strategies(con: sqlite3.Connection) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    commit = _code_commit()
    for s in STRATEGIES:
        chash = _contract_hash(s)
        prev = con.execute("SELECT contract_hash FROM strategies WHERE id=?", (s["id"],)).fetchone()
        if prev and prev[0] and prev[0] != chash:
            print(f"[VIRTUAL][경고] {s['id']} 계약 지문 변경 {prev[0]} → {chash} — "
                  f"변경 열차 위반 후보. 의도 변경이면 새 epoch(전략 id 갱신)를 권장")
        con.execute(
            """INSERT INTO strategies (id, universe, pick, daily_cap, slots, order_krw,
                   capital_krw, note, created_at, contract_hash, code_commit, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET universe=excluded.universe, pick=excluded.pick,
                   daily_cap=excluded.daily_cap, slots=excluded.slots,
                   order_krw=excluded.order_krw, note=excluded.note,
                   contract_hash=excluded.contract_hash, code_commit=excluded.code_commit,
                   updated_at=excluded.updated_at""",
            (s["id"], s["universe"], s["pick"], s["daily_cap"], s["slots"],
             s["order_krw"], s["capital_krw"], s["note"], now, chash, commit, now))
    con.commit()


def reconcile(con: sqlite3.Connection) -> bool:
    """일일 장부 대사 — Codex 훔칠 규율 ①. 불변식이 깨지면 성과 확정 금지."""
    problems: list[str] = []
    for status, need_net in (("CLOSED", True), ("OPEN", False)):
        bad = con.execute(
            f"SELECT COUNT(*) FROM trades WHERE status='{status}' AND "
            f"(net_pct IS {'NULL' if need_net else 'NOT NULL'} "
            f"OR pnl_krw IS {'NULL' if need_net else 'NOT NULL'})").fetchone()[0]
        if bad:
            problems.append(f"{status} 행 {bad}건의 손익 필드 불일치")
    today = datetime.now().strftime("%Y-%m-%d")
    future = con.execute("SELECT COUNT(*) FROM trades WHERE session_date>?", (today,)).fetchone()[0]
    if future:
        problems.append(f"미래 세션 거래 {future}건")
    for s in STRATEGIES:
        realized = con.execute(
            "SELECT COALESCE(SUM(pnl_krw),0) FROM trades WHERE strategy_id=? AND status='CLOSED'",
            (s["id"],)).fetchone()[0]
        row = con.execute(
            "SELECT realized_pnl_krw FROM book_daily WHERE strategy_id=? ORDER BY asof DESC LIMIT 1",
            (s["id"],)).fetchone()
        if row is not None and abs(float(row[0]) - float(realized)) > 1.0:
            problems.append(f"{s['id']} 실현손익 대사 불일치 (원장 {realized:.0f} vs 북 {row[0]:.0f})")
    if problems:
        print("[VIRTUAL] RECONCILE_REQUIRED — 성과 확정 금지:")
        for p in problems:
            print("  -", p)
        return False
    print("[VIRTUAL] 장부 대사 OK (불변식 전건 통과)")
    return True


def overlap_report(con: sqlite3.Connection) -> None:
    """합성 포트폴리오 겹침 — Codex 취약점 ③. '하나의 엣지를 17번 센' 정도를 잰다."""
    import statistics as st
    rows = con.execute(
        "SELECT strategy_id, session_date, ticker, COALESCE(pnl_krw,0), status "
        "FROM trades").fetchall()
    by_strat: dict[str, set] = defaultdict(set)
    pnl_by: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for sid, sd, tk, pnl, status in rows:
        by_strat[sid].add((sd, tk))
        if status == "CLOSED":
            pnl_by[sid][sd] += float(pnl)
    ids = [s["id"] for s in STRATEGIES if by_strat.get(s["id"]) and not s.get("retired")]
    print("\n[겹침/상관 — 같은 (세션,종목) 비율과 세션 P&L 상관. 판정 아님, 중첩 경보용]")
    printed = 0
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            inter = len(by_strat[a] & by_strat[b])
            union = len(by_strat[a] | by_strat[b])
            jac = inter / union if union else 0.0
            common = sorted(set(pnl_by[a]) & set(pnl_by[b]))
            corr = None
            if len(common) >= 6:
                xa = [pnl_by[a][d] for d in common]
                xb = [pnl_by[b][d] for d in common]
                ma, mb = st.mean(xa), st.mean(xb)
                cov = sum((p - ma) * (q - mb) for p, q in zip(xa, xb))
                va = sum((p - ma) ** 2 for p in xa)
                vb = sum((q - mb) ** 2 for q in xb)
                corr = cov / (va * vb) ** 0.5 if va > 0 and vb > 0 else None
            if jac >= 0.3 or (corr is not None and abs(corr) >= 0.6):
                print(f"  {a:16s} × {b:16s} 겹침 {jac*100:3.0f}% ({inter}건) "
                      f"상관 {f'{corr:+.2f}' if corr is not None else '  -  '}")
                printed += 1
    if not printed:
        print("  경보 기준(겹침>=30% 또는 |상관|>=0.6) 해당 쌍 없음")


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
                # 픽 근거 표시용(판정에는 쓰지 않음)
                "gap": float(gap) if gap is not None else None,
                "rv20": float(rv) if rv is not None else None,
                "chg": float(f["chg"]) if f.get("chg") is not None else None,
                "from_high20": float(f["from_high20"]) if f.get("from_high20") is not None else None,
            })
    return {sd: list(by.values()) for sd, by in sessions.items()}


# ── KR 공시 이벤트 universe (09-04) — 백필: data/analysis/dart_events_12m.jsonl(DART 재생, 접수일 기준)
#    forward: data/shadow/kr_event_signals.jsonl(실시간 레인 원장). session_date = 공시일(진입은 다음 세션 시가).
KR_EVENT_BACKFILL = ROOT / "data" / "analysis" / "dart_events_12m.jsonl"
KR_EVENT_SIGNALS = ROOT / "data" / "shadow" / "kr_event_signals.jsonl"
_KR_EVENT_KIND_MAP = {"무상증자결정": "bonus_issue", "공급계약체결": "supply_contract", "자기주식취득결정": "buyback",
                      "최대주주변경": "major_holder_change", "유상증자결정(대조)": "rights_offering"}
_CATALYST_KINDS = ("supply_contract", "bonus_issue", "buyback")
LIMITUP_CHG = 0.29


def _kr_event_rows() -> list[dict]:
    rows: list[dict] = []
    if KR_EVENT_BACKFILL.exists():
        for line in KR_EVENT_BACKFILL.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if "정정" in str(r.get("report", "")):
                continue
            kind = _KR_EVENT_KIND_MAP.get(str(r.get("kind", "")))
            d = str(r.get("date", ""))
            if kind and len(d) == 8:
                rows.append({"ticker": str(r["stock"]), "sd": f"{d[:4]}-{d[4:6]}-{d[6:]}", "kind": kind, "src": "backfill",
                             "ratio": None})
    if KR_EVENT_SIGNALS.exists():
        for line in KR_EVENT_SIGNALS.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("is_correction") or not r.get("stock_code") or r.get("kind") in (None, "other"):
                continue
            f = r.get("fields") or {}
            rows.append({"ticker": str(r["stock_code"]), "sd": str(r.get("session_date")), "kind": r["kind"], "src": "forward",
                         "ratio": f.get("ratio_per_share") if r["kind"] == "bonus_issue" else f.get("ratio_pct")})
    return rows


def load_kr_event_sessions() -> dict[str, dict[str, list[dict]]]:
    """{"krevent": {sd: [무상증자 후보]}, "krlimitup": {sd: [상한가+촉매 후보]}}.
    같은 (sd, ticker, kind)는 1건(백필·forward 겹침은 forward 우선)."""
    rows = _kr_event_rows()
    by: dict[tuple, dict] = {}
    for r in rows:
        key = (r["sd"], r["ticker"], r["kind"])
        if key not in by or r["src"] == "forward":
            by[key] = r
    bonus: dict[str, dict[str, dict]] = defaultdict(dict)
    catalysts: dict[tuple, set] = defaultdict(set)
    for (sd, tk, kind), r in by.items():
        if kind == "bonus_issue":
            bonus[sd].setdefault(tk, {"ticker": tk, "kind": kind, "bonus_ratio": r.get("ratio"), "src": r["src"]})
        if kind in _CATALYST_KINDS:
            catalysts[(sd, tk)].add(kind)
    limitup: dict[str, dict[str, dict]] = defaultdict(dict)
    for (sd, tk), kinds in catalysts.items():
        b = bars_kr(tk)
        i = next((k for k, x in enumerate(b) if x[0] == sd), None)
        if i is None or i < 1 or not b[i - 1][4]:
            continue
        chg = b[i][4] / b[i - 1][4] - 1.0
        if chg >= LIMITUP_CHG:
            limitup[sd][tk] = {"ticker": tk, "kind": "limitup_catalyst", "chg": round(chg * 100, 2),
                               "catalysts": sorted(kinds)}
    return {"krevent": {sd: list(v.values()) for sd, v in bonus.items()},
            "krlimitup": {sd: list(v.values()) for sd, v in limitup.items()}}


def strategy_pick_key(s: dict, c: dict) -> float:
    if s.get("pick") == "disc_deep":
        return c["disc"]  # 할인 깊은순(가장 음수 먼저) — 08-04 검증 통과 랭킹
    if s.get("pick") == "cum5_deep":
        return c["cum5"]  # 5일 누적낙폭 깊은순
    if s.get("pick") == "ret60_desc":
        return -c["ret60"]  # 추세 강한순 (B2)
    return _key(s["pick"], c)


SLOW_LEDGER = ROOT / "data" / "shadow" / "slow_fallen_shadow.jsonl"
SLOW_CACHE = ROOT / "data" / "analysis" / "slow_fallen_market_cache.json"
_SLOW_SERIES: dict | None = None


def _load_jsonl_sessions(path: Path, fields: dict[str, type]) -> dict[str, list[dict]]:
    sessions: dict[str, list[dict]] = defaultdict(list)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            for c in row.get("candidates") or []:
                item = {"ticker": str(c["ticker"])}
                try:
                    for k, cast in fields.items():
                        item[k] = cast(c[k])
                except (KeyError, TypeError, ValueError):
                    continue
                sessions[str(row["session_date"])].append(item)
    return sessions


def load_slow_sessions() -> dict[str, list[dict]]:
    return _load_jsonl_sessions(SLOW_LEDGER, {"cum5": float})


LP_LEDGER = ROOT / "data" / "shadow" / "leader_pullback_shadow.jsonl"


def load_lp_sessions() -> dict[str, list[dict]]:
    return _load_jsonl_sessions(LP_LEDGER, {"ret60": float})


def bars_slow(ticker: str) -> list[tuple]:
    """느린 급락 레인은 전시장 Alpaca 캐시가 가격 소스다(CSV 없는 종목 다수)."""
    global _SLOW_SERIES
    if _SLOW_SERIES is None:
        _SLOW_SERIES = json.loads(SLOW_CACHE.read_text()) if SLOW_CACHE.exists() else {}
    rows = []
    for x in sorted(_SLOW_SERIES.get(str(ticker), []), key=lambda b: b["t"]):
        try:
            rows.append((str(x["t"])[:10], float(x["o"]), float(x["h"]),
                         float(x["l"]), float(x["c"]), float(x.get("v") or 0)))
        except (TypeError, ValueError, KeyError):
            continue
    return rows


def load_earnings_windows() -> tuple[dict[str, str], str, str]:
    """(티커→어닝날짜, 창 시작, 창 끝). 캘린더는 롤링 스냅샷이라 **창 밖 세션은
    unknown**이다 — fail-open을 조용히 흘리면 S13이 no-op인지 가설 기각인지
    구분 불가(08-21 MAX BOM 사고 구조). 판정 결과는 결정 시점에 trades.meta로 박제."""
    path = ROOT / "data" / "earnings_calendar.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ({str(t).upper(): str(v.get("date") or "")
                 for t, v in (data.get("by_symbol") or {}).items()},
                str(data.get("from") or ""), str(data.get("to") or ""))
    except (OSError, ValueError):
        return {}, "", ""


def entry_of(ticker: str, session_date: str, *, market: str = "US",
             hold: int = HOLD_SESSIONS) -> tuple[float, list[tuple]] | None:
    """진입가·경로 창. US: session_date가 진입 세션(풀 규약). KR: session_date는
    신호일이라 **다음 거래 세션** 시가 진입(핸드오프 규약과 동일)."""
    if market == "KR":
        b = bars_kr(ticker)
        ei = next((i for i, x in enumerate(b) if x[0] > str(session_date)), None)
    elif market == "SLOW":
        b = bars_slow(ticker)
        ei = next((i for i, x in enumerate(b) if x[0] > str(session_date)), None)
    else:
        b = bars(ticker)
        ei = next((i for i, x in enumerate(b) if x[0] == str(session_date)), None)
    if ei is None:
        return None
    win = b[ei: ei + hold]  # D0..D(hold-1) (진입일 포함 — 기본 7세션 런타임 정본, arm별 hold 가능)
    # 미완성 봉 방어(09-04): 23:40 수동 실행에서 22:00 갱신이 쓴 당일 US 프리마켓 행(FRVO 09-03)으로
    # 8건이 진입됐다. 세션이 끝나지 않은 날짜의 봉은 진입·정산 어디에도 쓰지 않는다.
    win = [x for x in win if bar_complete(x[0], "KR" if market == "KR" else "US")]
    if not win or not win[0][1] or win[0][1] <= 0:
        return None
    return float(win[0][1]), win


def bar_complete(bar_date: str, market: str, now: datetime | None = None) -> bool:
    """일봉이 확정됐는가. KR: 당일 16:00 KST 이후. US: 다음날 06:00 KST 이후(서머타임 05:00 마감 포함 여유)."""
    from datetime import timedelta as _td
    now = now or (datetime.now(timezone.utc) + _td(hours=9))  # KST naive
    today = now.strftime("%Y-%m-%d")
    if market == "KR":
        return bar_date < today or (bar_date == today and now.hour >= 16)
    nxt = (datetime.strptime(bar_date, "%Y-%m-%d") + _td(days=1)).strftime("%Y-%m-%d")
    return nxt < today or (nxt == today and now.hour >= 6)


_EARN_CAL: tuple[dict[str, str], str, str] | None = None


_KR_EVENT_SESSIONS: dict | None = None


def kr_event_sessions_cached() -> dict:
    global _KR_EVENT_SESSIONS
    if _KR_EVENT_SESSIONS is None:
        _KR_EVENT_SESSIONS = load_kr_event_sessions()
    return _KR_EVENT_SESSIONS


def strategy_passers(s: dict, sessions_us: dict, sessions_kr: dict, sd: str,
                     sessions_slow: dict | None = None,
                     out_meta: dict | None = None,
                     sessions_lp: dict | None = None) -> list[dict]:
    """전략의 세션 통과 후보 (선별 파이프 전체 적용, 픽 전 단계).

    out_meta: 결정 시점에만 알 수 있는 판정 근거를 호출자가 박제할 수 있게 채운다
    (S13 어닝 필터의 캘린더 커버 여부·배제 수 — 사후 재산출 불가 데이터)."""
    global _EARN_CAL
    if s["universe"] == "kr":
        cands = sessions_kr.get(sd, [])
        return [c for c in cands if c.get(s["kr_rule"])]
    if s["universe"] in ("krevent", "krlimitup"):
        return list(kr_event_sessions_cached().get(s["universe"], {}).get(sd, []))
    if s["universe"] == "slowus":
        return list((sessions_slow or {}).get(sd, []))
    if s["universe"] == "lpus":
        return list((sessions_lp or {}).get(sd, []))
    pool = sessions_us.get(sd, [])
    if s["universe"] == "live":
        pool = [c for c in pool if c["in_pool"]]
    passers = band_max_pass(pool, max_floor=bool(s.get("max_floor", True)))
    if s.get("max_passers") and len(passers) > int(s["max_passers"]):
        return []  # S8 — 고밀도 세션 no-trade
    if s.get("no_earnings"):
        if _EARN_CAL is None:
            _EARN_CAL = load_earnings_windows()
        by_ticker, c_from, c_to = _EARN_CAL
        covers = bool(c_from and c_to and c_from <= sd <= c_to)
        before = len(passers)
        if covers:
            def near_earnings(t: str) -> bool:
                d = by_ticker.get(str(t).upper(), "")
                if not d:
                    return False
                try:
                    gap = abs((datetime.strptime(d, "%Y-%m-%d")
                               - datetime.strptime(sd, "%Y-%m-%d")).days)
                except ValueError:
                    return False
                return gap <= 2
            passers = [c for c in passers if not near_earnings(c["ticker"])]
        if out_meta is not None:
            out_meta["earnings_calendar_covers"] = covers
            out_meta["earnings_dropped"] = before - len(passers)
    return passers


# ── 픽 근거(사람이 읽는 한 줄) — 대시보드 열·관측기 원장·trades.meta.basis (2026-09-03) ──
PICK_LABEL = {
    "all": "전량", "dvol_desc": "거래대금 큰순", "dvol_asc": "거래대금 작은순",
    "chg_hi": "덜 빠진 순(낙폭 얕은순)", "ibs_hi": "IBS 높은순(고가 쪽 마감)", "max_lo": "MAX 낮은순",
    "disc_deep": "할인 깊은순", "cum5_deep": "5일 누적낙폭 깊은순", "ret60_desc": "60일 추세 강한순",
}


def _fmt(v, spec: str, suffix: str = "") -> str:
    try:
        return format(float(v), spec) + suffix
    except (TypeError, ValueError):
        return "-"


def pick_basis(s: dict, c: dict) -> str:
    """전략 s가 후보 c를 고른 근거 한 줄. 숫자는 신호일 피처(판정 입력이 아니라 표시용)."""
    label = PICK_LABEL.get(str(s.get("pick")), str(s.get("pick")))
    uni = s.get("universe")
    if uni == "kr":
        rule = "R2(할인≤-25&저변동)" if c.get("r2") else "R4(갭≤-4&할인≤-15)"
        parts = [f"할인 {_fmt(c.get('disc'), '+.1f', '%')}", f"갭 {_fmt(c.get('gap'), '+.1f', '%')}",
                 f"전일 {_fmt(c.get('chg'), '+.1f', '%')}", f"rv20 {_fmt(c.get('rv20'), '.1f')}",
                 f"20일고점 대비 {_fmt(c.get('from_high20'), '+.0f', '%')}", rule, label]
        return " · ".join(parts)
    if uni == "krevent":
        r = c.get("bonus_ratio")
        return f"무상증자결정 공시 · 1주당 {_fmt(r, '.2f', '주') if r is not None else '?'} · 다음날 시가 · 20일 드리프트 · {label}"
    if uni == "krlimitup":
        return f"상한가 {_fmt(c.get('chg'), '+.1f', '%')} · 당일 촉매 {'/'.join(c.get('catalysts') or [])} · 다음날 시가 · 복권(TP100/SL25/20일) · {label}"
    if uni == "slowus":
        return f"5일 누적 {_fmt(c.get('cum5'), '+.1f', '%')} · {label}"
    if uni == "lpus":
        return f"60일 {_fmt(c.get('ret60'), '+.1f', '%')} · {label}"
    parts = [f"전일 {_fmt(c.get('chg'), '+.1f', '%')}", f"거래대금 {_fmt(c.get('dvol'), '.0f', 'M')}",
             f"MAX21 {_fmt(c.get('max21'), '.1f')}", "풀 in" if c.get("in_pool") else "풀 wide", label]
    if s.get("max_floor") is False:
        parts.append("MAX하한 없음")
    if s.get("no_earnings"):
        parts.append("어닝 제외")
    if s.get("max_passers"):
        parts.append(f"고밀도 no-trade(>{s['max_passers']})")
    if s.get("tp") and float(s["tp"]) != float(TP):
        parts.append(f"TP{int(float(s['tp']))}")
    return " · ".join(parts)


def _feat_compact(c: dict) -> dict:
    out = {}
    for k in ("chg", "dvol", "max21", "ibs", "in_pool", "disc", "gap", "rv20", "from_high20", "cum5", "ret60", "bonus_ratio"):
        v = c.get(k)
        if isinstance(v, (int, float)):
            out[k] = round(float(v), 3)
    return out


def backfill_basis(con: sqlite3.Connection, sessions_us: dict, sessions_kr: dict,
                   sessions_slow: dict, sessions_lp: dict) -> int:
    """meta.basis가 없는 기존 행에 근거를 소급 박제(표시용, 판정 무관). 멱등."""
    by_id = {s["id"]: s for s in STRATEGIES}
    updated = 0
    for sid, sd, tk, meta in con.execute(
            "SELECT strategy_id, session_date, ticker, meta FROM trades").fetchall():
        try:
            m = json.loads(meta) if meta else {}
        except ValueError:
            m = {}
        if m.get("basis"):
            continue
        s = by_id.get(sid)
        if s is None:
            continue
        pool = {"kr": sessions_kr, "slowus": sessions_slow, "lpus": sessions_lp}.get(s["universe"], sessions_us)
        if s["universe"] in ("krevent", "krlimitup"):
            pool = kr_event_sessions_cached().get(s["universe"], {})
        c = next((x for x in pool.get(sd, []) if str(x.get("ticker")).upper() == str(tk).upper()), None)
        if c is None:
            continue
        m["basis"] = pick_basis(s, c)
        m["feat"] = _feat_compact(c)
        con.execute("UPDATE trades SET meta=? WHERE strategy_id=? AND session_date=? AND ticker=?",
                    (json.dumps(m, ensure_ascii=False), sid, sd, tk))
        updated += 1
    con.commit()
    return updated


def strategy_market(s: dict) -> str:
    return {"kr": "KR", "krevent": "KR", "krlimitup": "KR", "slowus": "SLOW", "lpus": "SLOW"}.get(s["universe"], "US")


def open_new_trades(con: sqlite3.Connection, sessions_us: dict[str, list[dict]],
                    sessions_kr: dict[str, list[dict]],
                    sessions_slow: dict[str, list[dict]],
                    sessions_lp: dict[str, list[dict]]) -> int:
    opened = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        from runtime.virtual_overrides import load_overrides, arm_state
        _ov = load_overrides()
    except Exception:
        _ov, arm_state = {}, (lambda a, o=None: "active")  # type: ignore
    for s in STRATEGIES:
        if s.get("retired"):
            continue  # 잔여 OPEN 정산만, 신규 진입 없음
        if arm_state(s["id"], _ov) != "active":
            continue  # 관제 오버라이드(paused/retired): 신규 진입 없음, 보유분 정산은 계속
        market = strategy_market(s)
        all_dates = {"kr": sessions_kr, "slowus": sessions_slow,
                     "lpus": sessions_lp}.get(s["universe"], sessions_us)
        if s["universe"] in ("krevent", "krlimitup"):
            all_dates = kr_event_sessions_cached().get(s["universe"], {})
        done = {r[0] for r in con.execute(
            "SELECT DISTINCT session_date FROM trades WHERE strategy_id=?", (s["id"],))}
        cash = book_cash(con, s)
        for sd in sorted(all_dates):
            if sd < BACKFILL_START or sd in done:
                continue
            if s.get("forward_only") and sd < FORWARD_START:
                continue
            sess_meta: dict = {}
            passers = strategy_passers(s, sessions_us, sessions_kr, sd, sessions_slow,
                                       out_meta=sess_meta, sessions_lp=sessions_lp)
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
                    record_entry_skip(s, sd, c["ticker"], market, all_dates)
                    continue
                entry, _win = eo
                con.execute(
                    """INSERT OR IGNORE INTO trades (strategy_id, session_date, ticker,
                           entry_price, notional_krw, backfill, pick_pos, status, opened_at, meta)
                       VALUES (?,?,?,?,?,?,?, 'OPEN', ?, ?)""",
                    (s["id"], sd, c["ticker"], entry, s["order_krw"],
                     1 if sd < FORWARD_START else 0, pos, now,
                     json.dumps({**sess_meta, "basis": pick_basis(s, c), "feat": _feat_compact(c)},
                                ensure_ascii=False)))
                if con.execute("SELECT changes()").fetchone()[0]:
                    opened += 1
                    open_n += 1
                    cash -= s["order_krw"]
    con.commit()
    return opened


# ── 진입 스킵 원장 + 가격 캐시 갱신 대기 (2026-09-03 KR 캐시 경합 수리) ─────────────────
# 09-03 실측: kr_r4x 09-02 통과자 348340·466100이 16:22 실행 시점에 09-03 봉이 없어(KR CSV
# 갱신 16:00→16:40, 그 시점 720/1307 완료) entry_of가 None → 조용히 건너뜀. 세션이 완료
# 처리되지 않아 다음 실행에서 소급 기록되긴 하지만 ① 사유가 어디에도 남지 않고 ② 대시보드에
# 하루 늦게 뜬다. 수리: ① 스킵 원장(사유 분류) ② 체인 실행 전 마커 대기.
_NEXT_SESSION_UNIVERSES = ("kr", "krevent", "krlimitup", "slowus", "lpus")


def classify_entry_skip(s: dict, sd: str, all_dates: dict) -> str:
    """봉 없음 스킵 분류. 다음 세션 진입 arm(kr/slowus/lpus)이 **최신 신호일**을 보고 있으면
    아직 진입 세션이 오지 않은 것(awaiting_session, 정상). 그 외(옛 신호일인데 봉 없음,
    US 당일 진입인데 봉 없음)는 캐시 미갱신/종목 미수집(no_bar_stale, 결함)."""
    if s.get("universe") in _NEXT_SESSION_UNIVERSES and all_dates and sd >= max(all_dates):
        return "awaiting_session"
    return "no_bar_stale"


def record_entry_skip(s: dict, sd: str, ticker: str, market: str, all_dates: dict,
                      path: Path | None = None) -> dict:
    reason = classify_entry_skip(s, sd, all_dates)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategy_id": s["id"], "session_date": sd, "ticker": str(ticker),
        "market": market, "reason": reason,
    }
    tag = "정상(다음 세션 대기)" if reason == "awaiting_session" else "결함 의심(캐시 미갱신/종목 미수집)"
    print(f"[VIRTUAL] {s['id']} {sd} {ticker} 진입 보류 — 봉 없음 · {reason} {tag}")
    try:
        out = path or ENTRY_SKIP_LEDGER
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[VIRTUAL] 스킵 원장 기록 실패: {exc}")
    return row


def _read_marker(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def price_marker_ready(market: str, now: datetime, marker: dict | None = None) -> bool:
    """오늘 캐시 갱신 완료 여부. KR: end_date == today(08:30 실행은 end_dt=어제라 run_date로는
    못 가른다). US: run_date == today(07:00 실행이 당일 마커, 22:00 실행은 전날 run_date)."""
    m = marker if marker is not None else _read_marker(PRICE_MARKER[market])
    if not m:
        return False
    today = now.strftime("%Y-%m-%d")
    key = "end_date" if market == "KR" else "run_date"
    return str(m.get(key)) == today


def markers_to_wait(now: datetime) -> list[str]:
    """이 실행이 기다려야 할 시장. 16시 이후 = KR 마감 갱신(16:00 작업), 12시 전 = US 갱신
    (07:00 작업). 주말은 갱신 작업이 없으니 대기하지 않는다."""
    if now.weekday() >= 5:
        return []
    if now.hour >= 16:
        return ["KR"]
    if now.hour < 12:
        return ["US"]
    return []


def wait_for_price_markers(now_fn=None, sleep_fn=None, max_wait_s: float | None = None,
                           poll_s: float = 30.0) -> dict[str, bool]:
    """체인 실행 전 가격 캐시 갱신 마커 대기. 타임아웃이면 WARN 출력 후 진행(뒤 단계 ⑨⑩을
    영원히 막지 않는다). 남는 봉 없음은 스킵 원장이 no_bar_stale로 잡는다."""
    now_fn = now_fn or datetime.now
    sleep_fn = sleep_fn or __import__("time").sleep
    if max_wait_s is None:
        max_wait_s = float(os.getenv("VIRTUAL_BOOKS_MARKER_WAIT_MAX_MIN", "60")) * 60.0
    start = now_fn()
    targets = markers_to_wait(start)
    result: dict[str, bool] = {}
    for market in targets:
        waited = 0.0
        while not price_marker_ready(market, start):
            if waited >= max_wait_s:
                print(f"[VIRTUAL] ⚠ {market} 가격 캐시 갱신 마커 대기 타임아웃({int(max_wait_s // 60)}분) — "
                      f"진행. 봉 없음 스킵은 원장(no_bar_stale)으로 확인")
                break
            if waited == 0.0:
                print(f"[VIRTUAL] {market} 가격 캐시 갱신 마커 대기 시작 ({PRICE_MARKER[market].name})")
            sleep_fn(poll_s)
            waited += poll_s
        result[market] = price_marker_ready(market, start)
        if result[market]:
            print(f"[VIRTUAL] {market} 가격 캐시 갱신 완료 확인 (대기 {int(waited)}s)")
    return result


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
        market = strategy_market(s)
        hold = int(s.get("hold", HOLD_SESSIONS))
        eo = entry_of(tk, sd, market=market, hold=hold)
        if eo is None:
            continue
        _e, win = eo
        res = contract_exit_v2(float(entry), win,
                               fee=FEE_KR if market == "KR" else FEE_US,
                               be_lock=(market != "KR"),
                               tp=float(s.get("tp", TP)), sl=float(s.get("sl", SL)), hold=hold)
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
            m = strategy_market(s)
            b = bars_kr(str(tk)) if m == "KR" else (bars_slow(str(tk)) if m == "SLOW" else bars(str(tk)))
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
    market = strategy_market(s)
    traded = con.execute(
        "SELECT session_date, COUNT(*), AVG(net_pct) FROM trades "
        "WHERE strategy_id=? AND status='CLOSED' GROUP BY session_date", (s["id"],)).fetchall()
    if sum(r[1] for r in traded) < 5:
        return None
    sessions_slow = load_slow_sessions() if s["universe"] == "slowus" else {}
    sessions_lp = load_lp_sessions() if s["universe"] == "lpus" else {}
    per_sess: list[tuple[list[float], int]] = []
    for sd, k, _avg in traded:
        nets = []
        for c in strategy_passers(s, sessions_us, sessions_kr, str(sd), sessions_slow,
                                  sessions_lp=sessions_lp):
            eo = entry_of(c["ticker"], str(sd), market=market, hold=int(s.get("hold", HOLD_SESSIONS)))
            if eo is None:
                continue
            res = contract_exit_v2(eo[0], eo[1], fee=FEE_KR if market == "KR" else FEE_US,
                                   be_lock=(market != "KR"), tp=float(s.get("tp", TP)),
                                   sl=float(s.get("sl", SL)), hold=int(s.get("hold", HOLD_SESSIONS)))
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


SUMMARY_MARK = ROOT / "state" / "virtual_books_summary_sent.json"


def send_daily_summary(con: sqlite3.Connection, *, opened: int, settled: int,
                       reconcile_ok: bool) -> bool:
    """가상 북 일일 요약 텔레그램 1건 (2026-09-02 텔레그램 정리, 운영자 지시).

    하루 두 번(07:20·16:20) 도는 체인 중 **첫 실행만** 보낸다(마커 파일, KST 날짜).
    forward(backfill=0) 수치만 싣는다 — 백필은 판정 표본이 아니다. 실패는 조용히 False."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        mark = json.loads(SUMMARY_MARK.read_text(encoding="utf-8")) if SUMMARY_MARK.exists() else {}
        if mark.get("date") == today and mark.get("sent"):
            return False  # 오늘 이미 발송. 실패(sent=false)였으면 다음 실행에서 재시도
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env.live", override=False)  # 체인 프로세스엔 TELEGRAM_* 없음
        except Exception:
            pass
        import telegram_reporter as tg
        if not tg.TOKEN:
            tg.TOKEN = os.getenv("TELEGRAM_TOKEN", "")
            tg.CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
        rows = con.execute(
            """SELECT strategy_id, COUNT(*), COALESCE(AVG(net_pct),0), COALESCE(SUM(pnl_krw),0)
               FROM trades WHERE backfill=0 AND status='CLOSED' GROUP BY strategy_id
               ORDER BY 3 DESC""").fetchall()
        fwd_open = con.execute("SELECT COUNT(*) FROM trades WHERE backfill=0 AND status='OPEN'").fetchone()[0]
        fwd_closed = sum(r[1] for r in rows)
        sessions = con.execute("SELECT COUNT(DISTINCT session_date) FROM trades WHERE backfill=0").fetchone()[0]
        today_open = con.execute(
            "SELECT strategy_id, ticker FROM trades WHERE backfill=0 AND opened_at>=? ORDER BY strategy_id",
            (datetime.now(timezone.utc).strftime("%Y-%m-%d"),)).fetchall()
        lines = [f"🧪 [VIRTUAL] 가상 북 일일 요약 {today} — 실계좌 아님",
                 f"오늘 진입 {opened} / 정산 {settled} / 대사 {'OK' if reconcile_ok else 'FAIL(성과 확정 금지)'}",
                 f"forward 누적: 정산 {fwd_closed}건 · 미결제 {fwd_open}건 · 거래세션 {sessions} (게이트 50건/80세션)"]
        if today_open:
            lines.append("오늘 진입: " + ", ".join(f"{sid}:{tk}" for sid, tk in today_open[:12]))
        if rows:
            top = rows[:3]; bot_ = rows[-3:] if len(rows) > 3 else []
            lines.append("forward 상위: " + " | ".join(f"{sid} {n}건 {avg:+.2f}%" for sid, n, avg, _ in top))
            if bot_:
                lines.append("forward 하위: " + " | ".join(f"{sid} {n}건 {avg:+.2f}%" for sid, n, avg, _ in bot_))
        sent = bool(tg.send("\n".join(lines)[:4000], parse_mode=None, critical=True))
        SUMMARY_MARK.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY_MARK.write_text(json.dumps({"date": today, "sent": sent}), encoding="utf-8")
        print(f"[VIRTUAL] 일일 요약 텔레그램 {'발송' if sent else '미발송(토큰 없음/실패)'}")
        return sent
    except Exception as exc:
        print(f"[VIRTUAL] 일일 요약 실패({str(exc)[:80]}) — 무시")
        return False


def report(con: sqlite3.Connection, sessions_us: dict | None = None,
           sessions_kr: dict | None = None) -> None:
    print("=== [VIRTUAL] 가상 북 현황 — 실계좌 아님, 가상 자본 ===")
    print(f"{'전략':16s} {'자본':>7s} {'실현손익':>10s} {'미결제':>4s} {'MTM':>9s} "
          f"{'정산':>4s} {'승률':>4s} {'평균net':>8s} {'백필/포워드':>10s} {'널백분위':>7s}")
    for s in STRATEGIES:
        if s.get("retired"):
            continue
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
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    cmd = args[0] if args else "run"
    BOOK_DB.parent.mkdir(parents=True, exist_ok=True)
    if cmd == "run" and "--no-wait" not in flags:
        wait_for_price_markers()  # 캐시 갱신 전 진입 시도 방지 (bars_kr 캐시는 프로세스 수명 — 먼저 기다린다)
    with closing(sqlite3.connect(BOOK_DB, timeout=30)) as con:
        ensure_schema(con)
        sync_strategies(con)
        if cmd == "run":
            sessions_us = load_sessions()
            sessions_kr = load_kr_sessions()
            sessions_slow = load_slow_sessions()
            sessions_lp = load_lp_sessions()
            opened = open_new_trades(con, sessions_us, sessions_kr, sessions_slow, sessions_lp)
            settled = settle_open_trades(con)
            mark_books(con)
            print(f"[VIRTUAL] 진입 {opened}건 / 정산 {settled}건")
            ok = reconcile(con)
            report(con, sessions_us, sessions_kr)
            overlap_report(con)
            send_daily_summary(con, opened=opened, settled=settled, reconcile_ok=ok)
        elif cmd == "report":
            report(con, load_sessions(), load_kr_sessions())
            overlap_report(con)
        elif cmd == "reconcile":
            return 0 if reconcile(con) else 1
        elif cmd == "backfill-basis":
            n = backfill_basis(con, load_sessions(), load_kr_sessions(), load_slow_sessions(), load_lp_sessions())
            print(f"[VIRTUAL] 픽 근거 소급 박제 {n}건")
        else:
            print("사용: virtual_books.py [run|report|reconcile|backfill-basis] [--no-wait]")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
