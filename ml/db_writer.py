"""
ml/db_writer.py — ML 의사결정 로그 DB 인터페이스

역할:
  - 매 사이클 평가된 모든 종목(신호 여부 무관)을 decisions 테이블에 기록
  - BUY_SIGNAL → 체결 여부 업데이트
  - 청산 시 거래 결과 업데이트
  - 주기적으로 선행 수익률(forward return) 업데이트

설계 원칙:
  - write_decision() 은 decision_id(lastrowid)를 반환 → position dict에 저장
  - BUY_SIGNAL 여부와 filled(체결) 여부는 별개 컬럼으로 분리
  - 진단 데이터 오버플로우는 diag_json TEXT에 직렬화
  - DB 오류는 예외를 삼키고 -1 반환 (봇 중단 방지)
"""
import sqlite3
import json
import sys
from pathlib import Path
from datetime import datetime, date

# STANCE → 숫자 인코딩 (낮을수록 Bearish)
STANCE_ORDER = [
    "HALT", "DEFENSIVE", "CAUTIOUS_BEAR", "MILD_BEAR",
    "NEUTRAL", "CAUTIOUS", "MILD_BULL", "MODERATE_BULL", "AGGRESSIVE",
]

_ROOT = Path(__file__).parent.parent
_DB_PATH = _ROOT / "data" / "ml" / "decisions.db"
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# ── logger ────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(_ROOT))
try:
    from logger import get_collector_logger
    _log = get_collector_logger()
except Exception:
    import logging
    _log = logging.getLogger("ml.db_writer")


def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    """DB 초기화 — schema.sql 실행 + 기존 DB 컬럼 마이그레이션."""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    with _get_conn() as conn:
        conn.executescript(sql)
        # 기존 DB에 strategy_used 컬럼 없으면 추가 (ALTER TABLE은 IF NOT EXISTS 미지원)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(decisions)")}
        if "strategy_used" not in existing:
            conn.execute("ALTER TABLE decisions ADD COLUMN strategy_used TEXT")
            _log.info("[ml.db] strategy_used 컬럼 마이그레이션 완료")
        if "data_source" not in existing:
            conn.execute("ALTER TABLE decisions ADD COLUMN data_source TEXT DEFAULT 'live'")
            _log.info("[ml.db] data_source 컬럼 마이그레이션 완료")
        if "is_simulated" not in existing:
            conn.execute("ALTER TABLE decisions ADD COLUMN is_simulated INTEGER DEFAULT 0")
            _log.info("[ml.db] is_simulated 컬럼 마이그레이션 완료")
        if "entry_priority_score" not in existing:
            conn.execute("ALTER TABLE decisions ADD COLUMN entry_priority_score REAL")
            _log.info("[ml.db] entry_priority_score 컬럼 마이그레이션 완료")

    # param_sessions 테이블 (Claude 파라미터 검토 레이어)
    try:
        from strategy.param_tuner import ensure_table as _pt_ensure
        _pt_ensure()
        _log.info("[ml.db] param_sessions 테이블 확인 완료")
    except Exception as _pte:
        _log.warning("[ml.db] param_sessions 테이블 초기화 실패: %s", _pte)

    _log.info(f"[ml.db] 초기화 완료: {_DB_PATH}")


# ── stance 인코딩 헬퍼 ─────────────────────────────────────────────────────────
def _stance_num(stance: str) -> int:
    try:
        return STANCE_ORDER.index(stance.upper())
    except (ValueError, AttributeError):
        return STANCE_ORDER.index("NEUTRAL")


# ── 핵심 인터페이스 ────────────────────────────────────────────────────────────

def write_decision(row: dict) -> int:
    """
    단일 종목 평가 결과를 decisions 테이블에 INSERT.

    row 필수 키:
        market, ticker, decision
    row 선택 키 (없으면 None/0으로 처리):
        ts, session_date, mode, mode_score,
        bull_stance(str), bear_stance(str), neut_stance(str),
        bull_conf, bear_conf, neut_conf, vix, usd_krw,
        price, rsi, bb_pct, vol_ratio, macd, macd_signal,
        ma20, ma60, atr, gap_pct, change_pct,
        mr_rsi_thr, mr_bb_thr, mr_rsi_miss, mr_bb_miss,
        mr_vol_ok, mr_ma_ok, mr_fired,
        vb_target, vb_close_miss, vb_vol_ok, vb_fired,
        mom_ma_ok, mom_macd_ok, mom_vol_ok, mom_high_ok, mom_fired,
        gap_gap_miss, gap_vol_ok, gap_pullback_ok, gap_fired,
        diag_json(dict|str), block_reason

    decision 값:
        BUY_SIGNAL  — 전략 신호 발생 (매수 시도)
        NO_SIGNAL   — 조건 미충족
        BLOCKED     — 조건 충족이나 진입 차단 (포지션 한도, HALT 등)
        SKIPPED     — 데이터 부족/오류로 평가 불가

    반환: decision_id (int), 오류 시 -1
    """
    try:
        ts           = row.get("ts") or datetime.now().isoformat(timespec="seconds")
        session_date = row.get("session_date") or date.today().isoformat()

        # stance 인코딩
        bull_stance = _stance_num(row.get("bull_stance", "NEUTRAL"))
        bear_stance = _stance_num(row.get("bear_stance", "NEUTRAL"))
        neut_stance = _stance_num(row.get("neut_stance", "NEUTRAL"))

        # diag_json 직렬화
        diag = row.get("diag_json")
        if isinstance(diag, dict):
            diag = json.dumps(diag, ensure_ascii=False)

        sql = """
        INSERT INTO decisions (
            ts, market, ticker, session_date,
            mode, mode_score,
            bull_stance, bear_stance, neut_stance,
            bull_conf, bear_conf, neut_conf,
            vix, usd_krw,
            price, rsi, bb_pct, vol_ratio,
            macd, macd_signal, ma20, ma60, atr, gap_pct, change_pct,
            mr_rsi_thr, mr_bb_thr, mr_rsi_miss, mr_bb_miss,
            mr_vol_ok, mr_ma_ok, mr_fired,
            vb_target, vb_close_miss, vb_vol_ok, vb_fired,
            mom_ma_ok, mom_macd_ok, mom_vol_ok, mom_high_ok, mom_fired,
            gap_gap_miss, gap_vol_ok, gap_pullback_ok, gap_fired,
            diag_json, entry_priority_score, decision, strategy_used, block_reason,
            data_source, is_simulated
        ) VALUES (
            ?,?,?,?,  ?,?,  ?,?,?,  ?,?,?,  ?,?,
            ?,?,?,?,  ?,?,?,?,?,?,?,
            ?,?,?,?,  ?,?,?,
            ?,?,?,?,
            ?,?,?,?,?,
            ?,?,?,?,?,  ?,?,?,?,  ?,?
        )
        """
        params = (
            ts, row["market"], row["ticker"], session_date,
            row.get("mode", "NEUTRAL"), _f(row, "mode_score"),
            bull_stance, bear_stance, neut_stance,
            _f(row, "bull_conf"), _f(row, "bear_conf"), _f(row, "neut_conf"),
            _f(row, "vix"), _f(row, "usd_krw"),
            _f(row, "price"), _f(row, "rsi"), _f(row, "bb_pct"), _f(row, "vol_ratio"),
            _f(row, "macd"), _f(row, "macd_signal"),
            _f(row, "ma20"), _f(row, "ma60"), _f(row, "atr"),
            _f(row, "gap_pct"), _f(row, "change_pct"),
            _f(row, "mr_rsi_thr"), _f(row, "mr_bb_thr"),
            _f(row, "mr_rsi_miss"), _f(row, "mr_bb_miss"),
            _i(row, "mr_vol_ok"), _i(row, "mr_ma_ok"), _i(row, "mr_fired"),
            _f(row, "vb_target"), _f(row, "vb_close_miss"),
            _i(row, "vb_vol_ok"), _i(row, "vb_fired"),
            _i(row, "mom_ma_ok"), _i(row, "mom_macd_ok"),
            _i(row, "mom_vol_ok"), _i(row, "mom_high_ok"), _i(row, "mom_fired"),
            _f(row, "gap_gap_miss"), _i(row, "gap_vol_ok"),
            _i(row, "gap_pullback_ok"), _i(row, "gap_fired"),
            diag, _f(row, "entry_priority_score"),
            row["decision"], row.get("strategy_used"), row.get("block_reason"),
            row.get("data_source", "live"), _i(row, "is_simulated") or 0,
        )
        with _get_conn() as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid
    except Exception as e:
        _log.warning(f"[ml.db] write_decision 실패 ({row.get('ticker','?')}): {e}")
        return -1


def update_filled(decision_id: int, order_status: str):
    """
    BUY_SIGNAL 이후 주문 체결 결과 업데이트.
    order_status: 'FILLED' | 'REJECTED' | 'PARTIAL'
    """
    if decision_id <= 0:
        return
    try:
        filled = 1 if order_status == "FILLED" else 0
        with _get_conn() as conn:
            conn.execute(
                "UPDATE decisions SET filled=?, order_status=? WHERE id=?",
                (filled, order_status, decision_id),
            )
    except Exception as e:
        _log.warning(f"[ml.db] update_filled({decision_id}) 실패: {e}")


def update_trade_outcome(
    decision_id: int,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    hold_days: int,
    pnl_pct: float,
):
    """청산 후 거래 결과 업데이트."""
    if decision_id <= 0:
        return
    try:
        with _get_conn() as conn:
            conn.execute(
                """UPDATE decisions
                   SET entry_price=?, exit_price=?, exit_reason=?,
                       hold_days=?, pnl_pct=?
                   WHERE id=?""",
                (entry_price, exit_price, exit_reason, hold_days, pnl_pct, decision_id),
            )
    except Exception as e:
        _log.warning(f"[ml.db] update_trade_outcome({decision_id}) 실패: {e}")


def update_forward_returns(decision_id: int, f1d: float, f3d: float, f5d: float):
    """선행 수익률 업데이트 (forward_updater.py에서 호출)."""
    if decision_id <= 0:
        return
    try:
        with _get_conn() as conn:
            conn.execute(
                "UPDATE decisions SET forward_1d=?, forward_3d=?, forward_5d=? WHERE id=?",
                (f1d, f3d, f5d, decision_id),
            )
    except Exception as e:
        _log.warning(f"[ml.db] update_forward_returns({decision_id}) 실패: {e}")


def load_for_ml(
    market: str = None,
    with_trade_result: bool = False,
    with_forward_return: bool = False,
    start_date: str = None,
    end_date: str = None,
) -> "pd.DataFrame":
    """
    ML 학습용 데이터 로드.

    Parameters
    ----------
    market             : 'KR' | 'US' | None(전체)
    with_trade_result  : True → pnl_pct IS NOT NULL (체결 후 청산된 행만)
    with_forward_return: True → forward_1d IS NOT NULL (선행 수익률 채워진 행만,
                         NO_SIGNAL near-miss 포함)
    start_date/end_date: session_date 범위 필터

    Returns
    -------
    pd.DataFrame — 전체 컬럼, decision_id=id
    """
    import pandas as pd

    conditions = []
    params = []
    if market:
        conditions.append("market=?")
        params.append(market)
    if with_trade_result:
        conditions.append("pnl_pct IS NOT NULL")
    if with_forward_return:
        conditions.append("forward_1d IS NOT NULL")
    if start_date:
        conditions.append("session_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("session_date <= ?")
        params.append(end_date)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM decisions {where} ORDER BY id"

    try:
        with _get_conn() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        df = df.rename(columns={"id": "decision_id"})
        return df
    except Exception as e:
        _log.warning(f"[ml.db] load_for_ml 실패: {e}")
        import pandas as pd
        return pd.DataFrame()


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────
def _f(row: dict, key: str):
    """float or None"""
    v = row.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(row: dict, key: str):
    """int (0/1) or None"""
    v = row.get(key)
    if v is None:
        return None
    try:
        return int(bool(v))
    except (TypeError, ValueError):
        return None


# ── 간단한 통계 ────────────────────────────────────────────────────────────────
def print_stats(market: str = None):
    """현재 DB 상태 간단 출력 (진단용)."""
    try:
        with _get_conn() as conn:
            where = f"WHERE market='{market}'" if market else ""
            total   = conn.execute(f"SELECT COUNT(*) FROM decisions {where}").fetchone()[0]
            signals = conn.execute(f"SELECT COUNT(*) FROM decisions {where} {'AND' if where else 'WHERE'} decision='BUY_SIGNAL'").fetchone()[0]
            filled  = conn.execute(f"SELECT COUNT(*) FROM decisions {where} {'AND' if where else 'WHERE'} filled=1").fetchone()[0]
            with_pnl= conn.execute(f"SELECT COUNT(*) FROM decisions {where} {'AND' if where else 'WHERE'} pnl_pct IS NOT NULL").fetchone()[0]
            avg_pnl = conn.execute(f"SELECT AVG(pnl_pct) FROM decisions {where} {'AND' if where else 'WHERE'} pnl_pct IS NOT NULL").fetchone()[0]
        mkt_str = market or "ALL"
        pnl_str = f"{avg_pnl:+.2f}%" if avg_pnl is not None else "N/A"
        print(f"[ML DB] {mkt_str} | 총 {total}건 | BUY_SIGNAL {signals}건 | 체결 {filled}건 | 결과기록 {with_pnl}건 | 평균PnL {pnl_str}")
    except Exception as e:
        print(f"[ML DB] stats 오류: {e}")


if __name__ == "__main__":
    init_db()
    print_stats()
    print(f"DB 경로: {_DB_PATH}")
