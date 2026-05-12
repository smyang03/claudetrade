"""
ticker_selection_db.py — 종목 선택 로그 DB (ML 학습용 raw 데이터)

brain.json에는 튜닝하지 않고 별도 DB에 raw 데이터 누적.
Claude 종목 선택 품질 ↔ 실제 수익의 상관관계 분석이 목적.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "ticker_selection_log.db")
PRICE_DIR = os.path.join(os.path.dirname(__file__), "data", "price")

_IS_PAPER = str(os.getenv("KIS_IS_PAPER", "true")).strip().lower() != "false"
_BOT_MODE = "paper" if _IS_PAPER else "live"
_price_cache: dict[str, Optional[dict[str, Any]]] = {}


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, factory=_ClosingConnection)
    try:
        conn.execute("PRAGMA journal_mode=WAL").fetchone()
        conn.execute("PRAGMA busy_timeout=10000").fetchone()
    except Exception:
        pass
    return conn


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
                watchlist_rank       INTEGER,
                source_type          TEXT,
                selection_batch_id   TEXT,
                selected_reason      TEXT,
                veto_reason          TEXT,
                selected_reason_tag  TEXT,
                selected_at          TEXT,
                change_pct           REAL,
                vol_ratio            REAL,
                gap_pct              REAL,
                from_high_pct        REAL,
                above_ma60           INTEGER,
                market_type          TEXT,
                category             TEXT,
                sector               TEXT,
                liquidity_bucket     TEXT,
                from_high_bucket     TEXT,
                trade_ready          INTEGER DEFAULT 0,
                risk_tags            TEXT,
                recommended_strategy TEXT,
                max_position_pct     REAL,
                signal_fired         INTEGER DEFAULT 0,
                strategy_name        TEXT,
                entry_priority_score REAL,
                blocked_reason       TEXT,
                signal_at            TEXT,
                traded               INTEGER DEFAULT 0,
                traded_at            TEXT,
                execution_source_type TEXT,
                execution_decision_id TEXT,
                execution_strategy    TEXT,
                execution_reason      TEXT,
                pnl_pct              REAL,
                exit_reason          TEXT,
                forward_1d           REAL,
                forward_3d           REAL,
                forward_5d           REAL,
                max_runup_3d         REAL,
                max_drawdown_3d      REAL,
                max_runup_5d         REAL,
                max_drawdown_5d      REAL,
                created_at           TEXT DEFAULT (datetime('now'))
            )
        """)
        # 기존 DB 마이그레이션 (bot_mode 컬럼 없으면 추가)
        existing = {r[1] for r in conn.execute("PRAGMA table_info(ticker_selection_log)")}
        if "bot_mode" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN bot_mode TEXT NOT NULL DEFAULT 'paper'")
        if "watchlist_rank" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN watchlist_rank INTEGER")
        if "veto_reason" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN veto_reason TEXT")
        if "trade_ready" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN trade_ready INTEGER DEFAULT 0")
        if "risk_tags" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN risk_tags TEXT")
        if "recommended_strategy" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN recommended_strategy TEXT")
        if "max_position_pct" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN max_position_pct REAL")
        if "market_type" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN market_type TEXT")
        if "category" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN category TEXT")
        if "liquidity_bucket" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN liquidity_bucket TEXT")
        if "from_high_bucket" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN from_high_bucket TEXT")
        if "forward_1d" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN forward_1d REAL")
        if "forward_3d" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN forward_3d REAL")
        if "forward_5d" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN forward_5d REAL")
        if "max_runup_3d" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN max_runup_3d REAL")
        if "max_drawdown_3d" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN max_drawdown_3d REAL")
        if "max_runup_5d" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN max_runup_5d REAL")
        if "max_drawdown_5d" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN max_drawdown_5d REAL")
        if "execution_source_type" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN execution_source_type TEXT")
        if "execution_decision_id" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN execution_decision_id TEXT")
        if "execution_strategy" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN execution_strategy TEXT")
        if "execution_reason" not in existing:
            conn.execute("ALTER TABLE ticker_selection_log ADD COLUMN execution_reason TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tslog_date_market "
            "ON ticker_selection_log(date, market)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tslog_ticker "
            "ON ticker_selection_log(market, ticker)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS micro_probe_log (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_mode                 TEXT NOT NULL DEFAULT 'paper',
                session_date             TEXT NOT NULL,
                market                   TEXT NOT NULL,
                ticker                   TEXT NOT NULL,
                order_no                 TEXT,
                source_strategy          TEXT,
                reason                   TEXT,
                entry_priority_score     REAL,
                original_qty             INTEGER,
                adjusted_qty             INTEGER,
                original_order_cost_krw  REAL,
                adjusted_order_cost_krw  REAL,
                order_budget_krw         REAL,
                min_effective_order_krw  REAL,
                oversize_ratio           REAL,
                status                   TEXT NOT NULL DEFAULT 'ORDERED',
                entered_at               TEXT NOT NULL,
                exited_at                TEXT,
                pnl_pct                  REAL,
                pnl_krw                  REAL,
                exit_reason              TEXT,
                created_at               TEXT DEFAULT (datetime('now'))
            )
        """)
        existing_probe = {r[1] for r in conn.execute("PRAGMA table_info(micro_probe_log)")}
        for col_name, col_type in (
            ("bot_mode", "TEXT NOT NULL DEFAULT 'paper'"),
            ("order_no", "TEXT"),
            ("source_strategy", "TEXT"),
            ("reason", "TEXT"),
            ("entry_priority_score", "REAL"),
            ("original_qty", "INTEGER"),
            ("adjusted_qty", "INTEGER"),
            ("original_order_cost_krw", "REAL"),
            ("adjusted_order_cost_krw", "REAL"),
            ("order_budget_krw", "REAL"),
            ("min_effective_order_krw", "REAL"),
            ("oversize_ratio", "REAL"),
            ("status", "TEXT NOT NULL DEFAULT 'ORDERED'"),
            ("entered_at", "TEXT"),
            ("exited_at", "TEXT"),
            ("pnl_pct", "REAL"),
            ("pnl_krw", "REAL"),
            ("exit_reason", "TEXT"),
        ):
            if col_name not in existing_probe:
                conn.execute(f"ALTER TABLE micro_probe_log ADD COLUMN {col_name} {col_type}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_micro_probe_market_date "
            "ON micro_probe_log(session_date, market)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_micro_probe_order "
            "ON micro_probe_log(market, ticker, order_no)"
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
    selection_meta: Optional[dict] = None,
) -> dict:
    """선택된 종목 배치를 DB에 삽입.

    Returns:
        {ticker: row_id} — 이후 update_signal / update_traded 에서 사용
    """
    if batch_id is None:
        batch_id = f"{date}_{market}_{source_type}"
    cand_map = {c.get("ticker", ""): c for c in (candidates or [])}
    selection_meta = selection_meta or {}
    trade_ready = set(selection_meta.get("trade_ready") or [])
    veto = selection_meta.get("veto") or {}
    risk_tags = selection_meta.get("risk_tags") or {}
    recommended_strategy = selection_meta.get("recommended_strategy") or {}
    max_position_pct = selection_meta.get("max_position_pct") or {}
    now_str = datetime.now().isoformat()
    result: dict = {}
    with _conn() as conn:
        for rank, ticker in enumerate(selected, 1):
            c = cand_map.get(ticker, {})
            reason = (sel_reasons or {}).get(ticker, "")
            above = c.get("above_ma60")
            tags = risk_tags.get(ticker)
            if isinstance(tags, (list, tuple, set)):
                risk_tags_text = json.dumps([str(tag) for tag in tags], ensure_ascii=False)
            elif tags:
                risk_tags_text = json.dumps([str(tags)], ensure_ascii=False)
            else:
                risk_tags_text = None
            try:
                max_position_pct_value = float(max_position_pct.get(ticker))
            except (TypeError, ValueError):
                max_position_pct_value = None
            row_id = conn.execute(
                """
                INSERT INTO ticker_selection_log
                    (bot_mode, date, market, ticker, consensus_mode, selection_rank,
                     watchlist_rank, source_type, selection_batch_id, selected_reason,
                     veto_reason, selected_reason_tag, selected_at,
                     change_pct, vol_ratio, gap_pct, from_high_pct, above_ma60, market_type, category, sector,
                     liquidity_bucket, from_high_bucket,
                     trade_ready, risk_tags, recommended_strategy, max_position_pct)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    _BOT_MODE,
                    date, market, ticker, consensus_mode, rank,
                    rank, source_type, batch_id, reason,
                    veto.get(ticker),
                    None,           # selected_reason_tag: 향후 파싱
                    now_str,
                    c.get("change_rate") or c.get("change_pct"),
                    c.get("vol_ratio"),
                    c.get("gap_pct"),
                    c.get("from_high_pct"),
                    int(bool(above)) if above is not None else None,
                    c.get("market_type"),
                    c.get("category"),
                    c.get("sector"),
                    c.get("liquidity_bucket"),
                    c.get("from_high_bucket"),
                    int(ticker in trade_ready),
                    risk_tags_text,
                    recommended_strategy.get(ticker),
                    max_position_pct_value,
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


def update_traded(
    row_id: int,
    traded_at: str,
    *,
    execution_source_type: str = "",
    execution_decision_id: str = "",
    execution_strategy: str = "",
    execution_reason: str = "",
    allow_watch_execution: bool = False,
) -> bool:
    """매수 주문 접수 성공 시 traded=1 업데이트"""
    if not row_id:
        return False
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, source_type, trade_ready FROM ticker_selection_log WHERE id=?",
            (row_id,),
        ).fetchone()
        if row is None:
            return False
        is_preopen_watch = str(row["source_type"] or "") == "preopen_watch"
        is_watch_only = int(row["trade_ready"] or 0) == 0
        if is_preopen_watch and is_watch_only and not allow_watch_execution:
            return False
        conn.execute(
            """
            UPDATE ticker_selection_log
            SET traded=1,
                traded_at=?,
                execution_source_type=COALESCE(NULLIF(?, ''), execution_source_type),
                execution_decision_id=COALESCE(NULLIF(?, ''), execution_decision_id),
                execution_strategy=COALESCE(NULLIF(?, ''), execution_strategy),
                execution_reason=COALESCE(NULLIF(?, ''), execution_reason)
            WHERE id=?
            """,
            (
                traded_at,
                execution_source_type,
                execution_decision_id,
                execution_strategy,
                execution_reason,
                row_id,
            ),
        )
        return True


def insert_execution_row_from_selection(
    row_id: int,
    traded_at: str,
    *,
    source_type: str = "signal_entry",
    execution_source_type: str = "",
    execution_decision_id: str = "",
    execution_strategy: str = "",
    execution_reason: str = "",
) -> int:
    """watch-only 선택 행을 오염시키지 않고 별도 실행 행을 만든다."""
    if not row_id:
        return 0
    copy_columns = [
        "bot_mode",
        "date",
        "market",
        "ticker",
        "consensus_mode",
        "selection_rank",
        "watchlist_rank",
        "source_type",
        "selection_batch_id",
        "selected_reason",
        "veto_reason",
        "selected_reason_tag",
        "selected_at",
        "change_pct",
        "vol_ratio",
        "gap_pct",
        "from_high_pct",
        "above_ma60",
        "market_type",
        "category",
        "sector",
        "liquidity_bucket",
        "from_high_bucket",
        "trade_ready",
        "risk_tags",
        "recommended_strategy",
        "max_position_pct",
        "signal_fired",
        "strategy_name",
        "entry_priority_score",
        "blocked_reason",
        "signal_at",
        "traded",
        "traded_at",
        "execution_source_type",
        "execution_decision_id",
        "execution_strategy",
        "execution_reason",
        "forward_1d",
        "forward_3d",
        "forward_5d",
        "max_runup_3d",
        "max_drawdown_3d",
        "max_runup_5d",
        "max_drawdown_5d",
    ]
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM ticker_selection_log WHERE id=?", (row_id,)).fetchone()
        if row is None:
            return 0
        existing = {r[1] for r in conn.execute("PRAGMA table_info(ticker_selection_log)").fetchall()}
        source = dict(row)
        values = {column: source.get(column) for column in copy_columns if column in existing}
        exec_source = execution_source_type or source_type
        values.update(
            {
                "source_type": source_type or exec_source,
                "selection_batch_id": f"{source.get('selection_batch_id') or ''}:execution",
                "trade_ready": 1,
                "signal_fired": 1,
                "strategy_name": execution_strategy or source.get("strategy_name"),
                "blocked_reason": execution_reason or source.get("blocked_reason"),
                "signal_at": source.get("signal_at") or traded_at,
                "traded": 1,
                "traded_at": traded_at,
                "execution_source_type": exec_source,
                "execution_decision_id": execution_decision_id,
                "execution_strategy": execution_strategy or source.get("strategy_name"),
                "execution_reason": execution_reason,
            }
        )
        insert_columns = [column for column in copy_columns if column in values and column in existing]
        placeholders = ",".join("?" for _ in insert_columns)
        row_id_new = conn.execute(
            f"""
            INSERT INTO ticker_selection_log ({", ".join(insert_columns)})
            VALUES ({placeholders})
            """,
            [values[column] for column in insert_columns],
        ).lastrowid
        return int(row_id_new or 0)


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


def log_micro_probe_entry(
    *,
    session_date: str,
    market: str,
    ticker: str,
    order_no: str,
    source_strategy: str,
    reason: str,
    entry_priority_score: float,
    original_qty: int,
    adjusted_qty: int,
    original_order_cost_krw: float,
    adjusted_order_cost_krw: float,
    order_budget_krw: float,
    min_effective_order_krw: float,
    oversize_ratio: float,
    entered_at: str,
) -> None:
    """MICRO_PROBE 진입을 일반 전략 성과와 분리해 기록한다."""
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO micro_probe_log
                (bot_mode, session_date, market, ticker, order_no, source_strategy,
                 reason, entry_priority_score, original_qty, adjusted_qty,
                 original_order_cost_krw, adjusted_order_cost_krw,
                 order_budget_krw, min_effective_order_krw, oversize_ratio,
                 status, entered_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _BOT_MODE,
                session_date,
                market,
                ticker,
                order_no,
                source_strategy,
                reason,
                float(entry_priority_score or 0.0),
                int(original_qty or 0),
                int(adjusted_qty or 0),
                float(original_order_cost_krw or 0.0),
                float(adjusted_order_cost_krw or 0.0),
                float(order_budget_krw or 0.0),
                float(min_effective_order_krw or 0.0),
                float(oversize_ratio or 0.0),
                "ORDERED",
                entered_at,
            ),
        )


def update_micro_probe_outcome(
    *,
    market: str,
    ticker: str,
    order_no: str = "",
    pnl_pct: float,
    pnl_krw: float,
    exit_reason: str,
    exited_at: str,
) -> None:
    """MICRO_PROBE 청산 결과를 가장 최근 미청산 진입에 연결한다."""
    params: list[object]
    selector: str
    order_no = str(order_no or "").strip()
    if order_no:
        selector = """
            id=(
                SELECT id FROM micro_probe_log
                WHERE market=? AND ticker=? AND order_no=? AND pnl_pct IS NULL
                ORDER BY id DESC LIMIT 1
            )
        """
        params = [market, ticker, order_no]
    else:
        selector = """
            id=(
                SELECT id FROM micro_probe_log
                WHERE market=? AND ticker=? AND pnl_pct IS NULL
                ORDER BY id DESC LIMIT 1
            )
        """
        params = [market, ticker]

    with _conn() as conn:
        conn.execute(
            f"""
            UPDATE micro_probe_log
            SET status='CLOSED', exited_at=?, pnl_pct=?, pnl_krw=?, exit_reason=?
            WHERE {selector}
            """,
            [exited_at, float(pnl_pct or 0.0), float(pnl_krw or 0.0), exit_reason, *params],
        )


def micro_probe_performance_report(market: Optional[str] = None) -> dict[str, Any]:
    """MICRO_PROBE 성과를 별도 리포트로 반환한다."""
    where = "WHERE pnl_pct IS NOT NULL"
    params: list[object] = []
    if market:
        where += " AND market=?"
        params.append(market)
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS trades,
                SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
                AVG(pnl_pct) AS avg_pnl_pct,
                SUM(pnl_krw) AS total_pnl_krw,
                MIN(pnl_pct) AS max_loss_pct,
                MAX(pnl_pct) AS max_gain_pct
            FROM micro_probe_log
            {where}
            """,
            params,
        ).fetchone()

    trades = int(row["trades"] or 0) if row else 0
    wins = int(row["wins"] or 0) if row else 0
    return {
        "market": market or "ALL",
        "trades": trades,
        "wins": wins,
        "win_rate_pct": round((wins / trades * 100.0), 2) if trades else 0.0,
        "avg_pnl_pct": round(float(row["avg_pnl_pct"] or 0.0), 4) if row else 0.0,
        "total_pnl_krw": round(float(row["total_pnl_krw"] or 0.0), 2) if row else 0.0,
        "max_loss_pct": round(float(row["max_loss_pct"] or 0.0), 4) if row else 0.0,
        "max_gain_pct": round(float(row["max_gain_pct"] or 0.0), 4) if row else 0.0,
    }


def _load_price(market: str, ticker: str) -> Optional[dict[str, Any]]:
    key = f"{market}:{ticker}"
    if key in _price_cache:
        return _price_cache[key]

    mkt = str(market or "").strip().lower()
    path = os.path.join(PRICE_DIR, mkt, f"{mkt}_{ticker}.csv")
    if not os.path.exists(path):
        _price_cache[key] = None
        return None

    rows: dict[str, dict[str, float]] = {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = str(row.get("date", "")).strip()
                close_raw = row.get("close")
                high_raw = row.get("high")
                low_raw = row.get("low")
                if not d or close_raw in (None, "") or high_raw in (None, "") or low_raw in (None, ""):
                    continue
                try:
                    rows[d] = {
                        "close": float(close_raw),
                        "high": float(high_raw),
                        "low": float(low_raw),
                    }
                except (TypeError, ValueError):
                    continue
    except OSError:
        _price_cache[key] = None
        return None

    dates = sorted(rows)
    data = {
        "dates": dates,
        "closes": [rows[d]["close"] for d in dates],
        "highs": [rows[d]["high"] for d in dates],
        "lows": [rows[d]["low"] for d in dates],
        "index": {d: i for i, d in enumerate(dates)},
    }
    _price_cache[key] = data
    return data


def _calc_forward_return(price_data: dict[str, Any], session_date: str, n_days: int) -> Optional[float]:
    base_idx = price_data["index"].get(session_date)
    if base_idx is None:
        return None

    future_idx = base_idx + n_days
    closes = price_data["closes"]
    if future_idx >= len(closes):
        return None

    base_close = float(closes[base_idx])
    future_close = float(closes[future_idx])
    if base_close <= 0:
        return None
    return round((future_close - base_close) / base_close * 100, 4)


def _calc_window_excursion(
    price_data: dict[str, Any],
    session_date: str,
    n_days: int,
) -> tuple[Optional[float], Optional[float]]:
    base_idx = price_data["index"].get(session_date)
    if base_idx is None:
        return None, None

    end_idx = base_idx + n_days
    closes = price_data["closes"]
    highs = price_data["highs"]
    lows = price_data["lows"]
    if end_idx >= len(closes):
        return None, None

    base_close = float(closes[base_idx])
    if base_close <= 0:
        return None, None

    window_high = max(float(v) for v in highs[base_idx + 1 : end_idx + 1])
    window_low = min(float(v) for v in lows[base_idx + 1 : end_idx + 1])
    runup = round((window_high - base_close) / base_close * 100, 4)
    drawdown = round((window_low - base_close) / base_close * 100, 4)
    return runup, drawdown


def update_forward_returns(
    market: Optional[str] = None,
    forward_days: tuple[int, ...] = (1, 3, 5),
) -> dict[str, object]:
    """ticker_selection_log의 선정 종목 사후 수익률을 price CSV 기준으로 채운다."""
    conditions = [
        "("
        "forward_1d IS NULL OR forward_3d IS NULL OR forward_5d IS NULL OR "
        "max_runup_3d IS NULL OR max_drawdown_3d IS NULL OR "
        "max_runup_5d IS NULL OR max_drawdown_5d IS NULL"
        ")"
    ]
    params: list[object] = []
    if market:
        conditions.append("market=?")
        params.append(market)
    where = " AND ".join(conditions)

    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        pending = conn.execute(
            f"""
            SELECT
                id, market, ticker, date,
                forward_1d, forward_3d, forward_5d,
                max_runup_3d, max_drawdown_3d,
                max_runup_5d, max_drawdown_5d
            FROM ticker_selection_log
            WHERE {where}
            ORDER BY id
            """,
            params,
        ).fetchall()

    updated = 0
    skipped = 0
    missing_csv = 0

    for row in pending:
        price_data = _load_price(row["market"], row["ticker"])
        if price_data is None:
            missing_csv += 1
            continue

        calc_map = {n: _calc_forward_return(price_data, row["date"], n) for n in forward_days}
        excursion_map = {
            n: _calc_window_excursion(price_data, row["date"], n)
            for n in (3, 5)
        }
        f1d = row["forward_1d"] if row["forward_1d"] is not None else calc_map.get(1)
        f3d = row["forward_3d"] if row["forward_3d"] is not None else calc_map.get(3)
        f5d = row["forward_5d"] if row["forward_5d"] is not None else calc_map.get(5)
        max_runup_3d = (
            row["max_runup_3d"]
            if row["max_runup_3d"] is not None
            else excursion_map.get(3, (None, None))[0]
        )
        max_drawdown_3d = (
            row["max_drawdown_3d"]
            if row["max_drawdown_3d"] is not None
            else excursion_map.get(3, (None, None))[1]
        )
        max_runup_5d = (
            row["max_runup_5d"]
            if row["max_runup_5d"] is not None
            else excursion_map.get(5, (None, None))[0]
        )
        max_drawdown_5d = (
            row["max_drawdown_5d"]
            if row["max_drawdown_5d"] is not None
            else excursion_map.get(5, (None, None))[1]
        )

        changed = (
            (row["forward_1d"] is None and f1d is not None)
            or (row["forward_3d"] is None and f3d is not None)
            or (row["forward_5d"] is None and f5d is not None)
            or (row["max_runup_3d"] is None and max_runup_3d is not None)
            or (row["max_drawdown_3d"] is None and max_drawdown_3d is not None)
            or (row["max_runup_5d"] is None and max_runup_5d is not None)
            or (row["max_drawdown_5d"] is None and max_drawdown_5d is not None)
        )
        if not changed:
            skipped += 1
            continue

        with _conn() as conn:
            conn.execute(
                """
                UPDATE ticker_selection_log
                SET
                    forward_1d=?,
                    forward_3d=?,
                    forward_5d=?,
                    max_runup_3d=?,
                    max_drawdown_3d=?,
                    max_runup_5d=?,
                    max_drawdown_5d=?
                WHERE id=?
                """,
                (
                    f1d,
                    f3d,
                    f5d,
                    max_runup_3d,
                    max_drawdown_3d,
                    max_runup_5d,
                    max_drawdown_5d,
                    row["id"],
                ),
            )
        updated += 1

    return {
        "market": market or "ALL",
        "pending": len(pending),
        "updated": updated,
        "skipped": skipped,
        "missing_csv": missing_csv,
    }


def get_recent_selection_feedback(
    market: str,
    days: int = 20,
    as_of: Optional[str] = None,
    strong_runup_pct: float = 5.0,
) -> dict[str, object]:
    """최근 종목 선정 품질을 Claude 프롬프트용 요약 지표로 집계한다."""
    if as_of:
        end_date = as_of
    else:
        end_date = date.today().strftime("%Y-%m-%d")
    start_date = (datetime.strptime(end_date, "%Y-%m-%d").date() - timedelta(days=max(days - 1, 0))).strftime("%Y-%m-%d")

    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_rows,
                SUM(CASE WHEN trade_ready=1 THEN 1 ELSE 0 END) AS trade_ready_rows,
                SUM(CASE WHEN trade_ready=0 THEN 1 ELSE 0 END) AS watch_only_rows,
                SUM(CASE WHEN traded=1 THEN 1 ELSE 0 END) AS traded_rows,
                SUM(CASE WHEN trade_ready=1 AND forward_3d IS NOT NULL THEN 1 ELSE 0 END) AS trade_ready_forward_n,
                AVG(CASE WHEN trade_ready=1 THEN forward_3d END) AS trade_ready_avg_forward_3d,
                AVG(CASE WHEN trade_ready=1 THEN max_runup_3d END) AS trade_ready_avg_runup_3d,
                AVG(CASE WHEN trade_ready=1 THEN max_drawdown_3d END) AS trade_ready_avg_drawdown_3d,
                SUM(CASE WHEN trade_ready=1 AND forward_3d > 0 THEN 1 ELSE 0 END) AS trade_ready_hit_count,
                SUM(CASE WHEN trade_ready=1 AND forward_3d <= 0 THEN 1 ELSE 0 END) AS weak_trade_ready_count,
                SUM(CASE WHEN trade_ready=0 AND forward_3d IS NOT NULL THEN 1 ELSE 0 END) AS watch_only_forward_n,
                AVG(CASE WHEN trade_ready=0 THEN forward_3d END) AS watch_only_avg_forward_3d,
                AVG(CASE WHEN trade_ready=0 THEN max_runup_3d END) AS watch_only_avg_runup_3d,
                AVG(CASE WHEN trade_ready=0 THEN max_drawdown_3d END) AS watch_only_avg_drawdown_3d,
                SUM(CASE WHEN trade_ready=0 AND max_runup_3d >= ? THEN 1 ELSE 0 END) AS missed_watch_only_count
            FROM ticker_selection_log
            WHERE market=? AND date>=? AND date<=?
            """,
            (strong_runup_pct, market, start_date, end_date),
        ).fetchone()

    total_rows = int(row["total_rows"] or 0)
    trade_ready_forward_n = int(row["trade_ready_forward_n"] or 0)
    watch_only_forward_n = int(row["watch_only_forward_n"] or 0)
    trade_ready_hit_rate = round((float(row["trade_ready_hit_count"] or 0) / trade_ready_forward_n) * 100, 1) if trade_ready_forward_n else None
    missed_watch_only_rate = round((float(row["missed_watch_only_count"] or 0) / watch_only_forward_n) * 100, 1) if watch_only_forward_n else None
    weak_trade_ready_rate = round((float(row["weak_trade_ready_count"] or 0) / trade_ready_forward_n) * 100, 1) if trade_ready_forward_n else None

    return {
        "market": market,
        "days": days,
        "start_date": start_date,
        "end_date": end_date,
        "total_rows": total_rows,
        "trade_ready_rows": int(row["trade_ready_rows"] or 0),
        "watch_only_rows": int(row["watch_only_rows"] or 0),
        "traded_rows": int(row["traded_rows"] or 0),
        "trade_ready_forward_n": trade_ready_forward_n,
        "trade_ready_avg_forward_3d": row["trade_ready_avg_forward_3d"],
        "trade_ready_avg_runup_3d": row["trade_ready_avg_runup_3d"],
        "trade_ready_avg_drawdown_3d": row["trade_ready_avg_drawdown_3d"],
        "trade_ready_hit_rate_3d": trade_ready_hit_rate,
        "weak_trade_ready_count": int(row["weak_trade_ready_count"] or 0),
        "weak_trade_ready_rate_3d": weak_trade_ready_rate,
        "watch_only_forward_n": watch_only_forward_n,
        "watch_only_avg_forward_3d": row["watch_only_avg_forward_3d"],
        "watch_only_avg_runup_3d": row["watch_only_avg_runup_3d"],
        "watch_only_avg_drawdown_3d": row["watch_only_avg_drawdown_3d"],
        "missed_watch_only_count": int(row["missed_watch_only_count"] or 0),
        "missed_watch_only_rate_3d": missed_watch_only_rate,
        "strong_runup_pct": strong_runup_pct,
    }


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):+.1f}%"


def get_recent_selection_feedback_breakdown(
    market: str,
    group_key: str,
    days: int = 20,
    as_of: Optional[str] = None,
    strong_runup_pct: float = 5.0,
    min_rows: int = 2,
    limit: int = 2,
) -> list[dict[str, object]]:
    """최근 선정 품질을 board/category 단위로 묶어 상위 그룹만 반환한다."""
    if group_key not in {
        "market_type",
        "category",
        "liquidity_bucket",
        "from_high_bucket",
        "recommended_strategy",
    }:
        raise ValueError(f"unsupported group_key: {group_key}")

    if as_of:
        end_date = as_of
    else:
        end_date = date.today().strftime("%Y-%m-%d")
    start_date = (
        datetime.strptime(end_date, "%Y-%m-%d").date() - timedelta(days=max(days - 1, 0))
    ).strftime("%Y-%m-%d")

    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT
                {group_key} AS group_value,
                COUNT(*) AS total_rows,
                SUM(CASE WHEN trade_ready=1 AND forward_3d IS NOT NULL THEN 1 ELSE 0 END) AS trade_ready_forward_n,
                SUM(CASE WHEN trade_ready=1 AND forward_3d <= 0 THEN 1 ELSE 0 END) AS weak_trade_ready_count,
                SUM(CASE WHEN trade_ready=0 AND forward_3d IS NOT NULL THEN 1 ELSE 0 END) AS watch_only_forward_n,
                SUM(CASE WHEN trade_ready=0 AND max_runup_3d >= ? THEN 1 ELSE 0 END) AS missed_watch_only_count,
                AVG(CASE WHEN trade_ready=0 THEN max_runup_3d END) AS watch_only_avg_runup_3d
            FROM ticker_selection_log
            WHERE market=? AND date>=? AND date<=?
              AND COALESCE(TRIM({group_key}), '') != ''
            GROUP BY {group_key}
            HAVING COUNT(*) >= ?
            ORDER BY
                missed_watch_only_count DESC,
                weak_trade_ready_count DESC,
                total_rows DESC,
                group_value ASC
            LIMIT ?
            """,
            (strong_runup_pct, market, start_date, end_date, min_rows, limit),
        ).fetchall()

    result = []
    for row in rows:
        trade_ready_forward_n = int(row["trade_ready_forward_n"] or 0)
        watch_only_forward_n = int(row["watch_only_forward_n"] or 0)
        weak_trade_ready_rate = (
            round((float(row["weak_trade_ready_count"] or 0) / trade_ready_forward_n) * 100, 1)
            if trade_ready_forward_n
            else None
        )
        missed_watch_only_rate = (
            round((float(row["missed_watch_only_count"] or 0) / watch_only_forward_n) * 100, 1)
            if watch_only_forward_n
            else None
        )
        result.append(
            {
                "group_key": group_key,
                "group_value": row["group_value"],
                "total_rows": int(row["total_rows"] or 0),
                "trade_ready_forward_n": trade_ready_forward_n,
                "weak_trade_ready_count": int(row["weak_trade_ready_count"] or 0),
                "weak_trade_ready_rate_3d": weak_trade_ready_rate,
                "watch_only_forward_n": watch_only_forward_n,
                "missed_watch_only_count": int(row["missed_watch_only_count"] or 0),
                "missed_watch_only_rate_3d": missed_watch_only_rate,
                "watch_only_avg_runup_3d": row["watch_only_avg_runup_3d"],
            }
        )
    return result


def format_recent_selection_feedback_breakdown(
    market: str,
    group_key: str,
    days: int = 20,
    as_of: Optional[str] = None,
    strong_runup_pct: float = 5.0,
    min_rows: int = 2,
    limit: int = 2,
) -> str:
    rows = get_recent_selection_feedback_breakdown(
        market=market,
        group_key=group_key,
        days=days,
        as_of=as_of,
        strong_runup_pct=strong_runup_pct,
        min_rows=min_rows,
        limit=limit,
    )
    if not rows:
        return ""

    label_map = {
        "market_type": "board",
        "category": "category",
        "liquidity_bucket": "liquidity",
        "from_high_bucket": "pullback",
        "recommended_strategy": "strategy",
    }
    label = label_map[group_key]
    lines = []
    for row in rows:
        weak_rate = (
            f"{row['weak_trade_ready_rate_3d']:.1f}%"
            if row["weak_trade_ready_rate_3d"] is not None
            else "N/A"
        )
        missed_rate = (
            f"{row['missed_watch_only_rate_3d']:.1f}%"
            if row["missed_watch_only_rate_3d"] is not None
            else "N/A"
        )
        lines.append(
            f"- by {label}: {row['group_value']} "
            f"selected={row['total_rows']} "
            f"miss_watch={missed_rate}(n={row['watch_only_forward_n']}) "
            f"weak_TR={weak_rate}(n={row['trade_ready_forward_n']}) "
            f"watch_runup={_fmt_pct(row['watch_only_avg_runup_3d'])}"
        )
    return "\n".join(lines)


def format_recent_selection_feedback(
    market: str,
    days: int = 20,
    as_of: Optional[str] = None,
    strong_runup_pct: float = 5.0,
) -> str:
    summary = get_recent_selection_feedback(
        market=market,
        days=days,
        as_of=as_of,
        strong_runup_pct=strong_runup_pct,
    )
    if not summary["total_rows"]:
        return ""

    lines = [
        (
            f"- 최근 {summary['days']}일 selected={summary['total_rows']} "
            f"trade_ready={summary['trade_ready_rows']} watch_only={summary['watch_only_rows']} "
            f"traded={summary['traded_rows']}"
        ),
        (
            f"- trade_ready 3d: hit_rate={summary['trade_ready_hit_rate_3d'] if summary['trade_ready_hit_rate_3d'] is not None else 'N/A'}% "
            f"avg_fwd={_fmt_pct(summary['trade_ready_avg_forward_3d'])} "
            f"avg_runup={_fmt_pct(summary['trade_ready_avg_runup_3d'])} "
            f"avg_dd={_fmt_pct(summary['trade_ready_avg_drawdown_3d'])} "
            f"(n={summary['trade_ready_forward_n']})"
        ),
        (
            f"- watch_only 3d: avg_fwd={_fmt_pct(summary['watch_only_avg_forward_3d'])} "
            f"avg_runup={_fmt_pct(summary['watch_only_avg_runup_3d'])} "
            f"avg_dd={_fmt_pct(summary['watch_only_avg_drawdown_3d'])} "
            f"(n={summary['watch_only_forward_n']})"
        ),
        (
            f"- missed watch_only: runup>={float(strong_runup_pct):.1f}% "
            f"{summary['missed_watch_only_count']}건"
            + (
                f" ({summary['missed_watch_only_rate_3d']:.1f}%)"
                if summary["missed_watch_only_rate_3d"] is not None
                else ""
            )
        ),
        (
            f"- weak trade_ready: forward_3d<=0 "
            f"{summary['weak_trade_ready_count']}건"
            + (
                f" ({summary['weak_trade_ready_rate_3d']:.1f}%)"
                if summary["weak_trade_ready_rate_3d"] is not None
                else ""
            )
        ),
    ]
    board_lines = format_recent_selection_feedback_breakdown(
        market=market,
        group_key="market_type",
        days=days,
        as_of=as_of,
        strong_runup_pct=strong_runup_pct,
    )
    category_lines = format_recent_selection_feedback_breakdown(
        market=market,
        group_key="category",
        days=days,
        as_of=as_of,
        strong_runup_pct=strong_runup_pct,
    )
    liquidity_lines = format_recent_selection_feedback_breakdown(
        market=market,
        group_key="liquidity_bucket",
        days=days,
        as_of=as_of,
        strong_runup_pct=strong_runup_pct,
    )
    pullback_lines = format_recent_selection_feedback_breakdown(
        market=market,
        group_key="from_high_bucket",
        days=days,
        as_of=as_of,
        strong_runup_pct=strong_runup_pct,
    )
    strategy_lines = format_recent_selection_feedback_breakdown(
        market=market,
        group_key="recommended_strategy",
        days=days,
        as_of=as_of,
        strong_runup_pct=strong_runup_pct,
    )
    if board_lines:
        lines.append(board_lines)
    if category_lines:
        lines.append(category_lines)
    if liquidity_lines:
        lines.append(liquidity_lines)
    if pullback_lines:
        lines.append(pullback_lines)
    if strategy_lines:
        lines.append(strategy_lines)
    return "\n".join(lines)
