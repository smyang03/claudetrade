"""
ticker_selection_db.py — 종목 선택 로그 DB (ML 학습용 raw 데이터)

brain.json에는 튜닝하지 않고 별도 DB에 raw 데이터 누적.
Claude 종목 선택 품질 ↔ 실제 수익의 상관관계 분석이 목적.
"""
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "ticker_selection_log.db")

_IS_PAPER = str(os.getenv("KIS_IS_PAPER", "true")).strip().lower() != "false"
_BOT_MODE = "paper" if _IS_PAPER else "live"


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init() -> None:
    """테이블 + 인덱스 생성 (idempotent)"""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ticker_selection_log (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_mode             TEXT NOT NULL DEFAULT 'paper',
                date                 TEXT NOT NULL,
                market               TEXT NOT NULL,
                ticker               TEXT NOT NULL,
                consensus_mode       TEXT,
                selection_rank       INTEGER,
                source_type          TEXT,
                selection_batch_id   TEXT,
                selected_reason      TEXT,
                selected_reason_tag  TEXT,
                selected_at          TEXT,
                change_pct           REAL,
                vol_ratio            REAL,
                gap_pct              REAL,
                from_high_pct        REAL,
                above_ma60           INTEGER,
                sector               TEXT,
                signal_fired         INTEGER DEFAULT 0,
                strategy_name        TEXT,
                entry_priority_score REAL,
                blocked_reason       TEXT,
                signal_at            TEXT,
                traded               INTEGER DEFAULT 0,
                traded_at            TEXT,
                pnl_pct              REAL,
                exit_reason          TEXT,
                created_at           TEXT DEFAULT (datetime('now'))
            )
        """)
        # 기존 DB 마이그레이션 (bot_mode 컬럼 없으면 추가)
        existing = {r[1] for r in conn.execute("PRAGMA table_info(ticker_selection_log)")}
        if "bot_mode" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN bot_mode TEXT NOT NULL DEFAULT 'paper'")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tslog_date_market "
            "ON ticker_selection_log(date, market)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tslog_ticker "
            "ON ticker_selection_log(market, ticker)"
        )


def insert_batch(
    date: str,
    market: str,
    source_type: str,
    selected: list,
    candidates: list,
    sel_reasons: dict,
    consensus_mode: str,
    batch_id: str = None,
) -> dict:
    """선택된 종목 배치를 DB에 삽입.

    Returns:
        {ticker: row_id} — 이후 update_signal / update_traded 에서 사용
    """
    if batch_id is None:
        batch_id = f"{date}_{market}_{source_type}"
    cand_map = {c.get("ticker", ""): c for c in (candidates or [])}
    now_str = datetime.now().isoformat()
    result: dict = {}
    with _conn() as conn:
        for rank, ticker in enumerate(selected, 1):
            c = cand_map.get(ticker, {})
            reason = (sel_reasons or {}).get(ticker, "")
            above = c.get("above_ma60")
            row_id = conn.execute(
                """
                INSERT INTO ticker_selection_log
                    (bot_mode, date, market, ticker, consensus_mode, selection_rank,
                     source_type, selection_batch_id, selected_reason,
                     selected_reason_tag, selected_at,
                     change_pct, vol_ratio, gap_pct, from_high_pct, above_ma60, sector)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    _BOT_MODE,
                    date, market, ticker, consensus_mode, rank,
                    source_type, batch_id, reason,
                    None,           # selected_reason_tag: 향후 파싱
                    now_str,
                    c.get("change_rate") or c.get("change_pct"),
                    c.get("vol_ratio"),
                    c.get("gap_pct"),
                    c.get("from_high_pct"),
                    int(bool(above)) if above is not None else None,
                    None,           # sector: 향후 매핑 테이블로 채움
                ),
            ).lastrowid
            result[ticker] = row_id
    return result


def update_signal(
    row_id: int,
    strategy_name: str,
    entry_priority_score: float,
    signal_at: str,
    blocked_reason: str = None,
) -> None:
    """전략 신호가 발생했을 때 업데이트 (주문 성공/차단 모두 signal_fired=1)"""
    if not row_id:
        return
    with _conn() as conn:
        conn.execute(
            """
            UPDATE ticker_selection_log
            SET signal_fired=1, strategy_name=?, entry_priority_score=?,
                signal_at=?, blocked_reason=?
            WHERE id=?
            """,
            (strategy_name, entry_priority_score, signal_at, blocked_reason, row_id),
        )


def update_traded(row_id: int, traded_at: str) -> None:
    """매수 주문 접수 성공 시 traded=1 업데이트"""
    if not row_id:
        return
    with _conn() as conn:
        conn.execute(
            "UPDATE ticker_selection_log SET traded=1, traded_at=? WHERE id=?",
            (traded_at, row_id),
        )


def update_pnl(market: str, ticker: str, pnl_pct: float, exit_reason: str) -> None:
    """매도 완료 후 pnl 기록 — market+ticker 기준 가장 최근 traded=1 행에 업데이트"""
    with _conn() as conn:
        conn.execute(
            """
            UPDATE ticker_selection_log SET pnl_pct=?, exit_reason=?
            WHERE id=(
                SELECT id FROM ticker_selection_log
                WHERE market=? AND ticker=? AND traded=1 AND pnl_pct IS NULL
                ORDER BY id DESC LIMIT 1
            )
            """,
            (pnl_pct, exit_reason, market, ticker),
        )
