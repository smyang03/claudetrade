-- ml/schema.sql — ML 의사결정 로그 DB 스키마
-- decisions 테이블: 매 사이클 평가된 모든 후보 종목 기록

CREATE TABLE IF NOT EXISTS decisions (
    -- ── 기본 식별 ─────────────────────────────────────────────
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,          -- 평가 시각 (ISO 8601)
    market          TEXT NOT NULL,          -- 'KR' | 'US'
    ticker          TEXT NOT NULL,
    session_date    TEXT NOT NULL,          -- 'YYYY-MM-DD' (거래일 기준)

    -- ── 장세 컨텍스트 ──────────────────────────────────────────
    mode            TEXT NOT NULL,          -- AGGRESSIVE|MODERATE_BULL|...|HALT
    mode_score      REAL,                   -- brain consensus score (0~1)
    bull_stance     INTEGER,                -- STANCE_ORDER 인덱스 0~7
    bear_stance     INTEGER,
    neut_stance     INTEGER,
    bull_conf       REAL,                   -- 0.0~1.0
    bear_conf       REAL,
    neut_conf       REAL,
    vix             REAL,                   -- 수집 못하면 0.0
    usd_krw         REAL,

    -- ── 가격·기술적 지표 ───────────────────────────────────────
    price           REAL,
    rsi             REAL,
    bb_pct          REAL,
    vol_ratio       REAL,                   -- 당일 거래량 / 20일 평균
    macd            REAL,
    macd_signal     REAL,
    ma20            REAL,
    ma60            REAL,
    atr             REAL,
    gap_pct         REAL,                   -- 전일 종가 대비 갭 (%)
    change_pct      REAL,                   -- 당일 등락률 (%)

    -- ── 평균회귀 전략 세부 진단 ────────────────────────────────
    mr_rsi_thr      REAL,                   -- 설정 임계값
    mr_bb_thr       REAL,
    mr_rsi_miss     REAL,                   -- rsi - rsi_thr (음수=조건 통과)
    mr_bb_miss      REAL,                   -- bb_pct - bb_thr
    mr_vol_ok       INTEGER,                -- 1/0
    mr_ma_ok        INTEGER,
    mr_fired        INTEGER,                -- 신호 발생 여부 1/0

    -- ── 변동성 돌파 전략 세부 진단 ────────────────────────────
    vb_target       REAL,                   -- 돌파 목표가
    vb_close_miss   REAL,                   -- close - target
    vb_vol_ok       INTEGER,
    vb_fired        INTEGER,

    -- ── 모멘텀 전략 세부 진단 ─────────────────────────────────
    mom_ma_ok       INTEGER,
    mom_macd_ok     INTEGER,
    mom_vol_ok      INTEGER,
    mom_high_ok     INTEGER,
    mom_fired       INTEGER,

    -- ── 갭+눌림 전략 세부 진단 ────────────────────────────────
    gap_gap_miss    REAL,                   -- gap_pct - gap_min_thr
    gap_vol_ok      INTEGER,
    gap_pullback_ok INTEGER,
    gap_fired       INTEGER,

    -- ── 오버플로우 진단 데이터 ─────────────────────────────────
    diag_json       TEXT,                   -- 추가 진단 정보 JSON (선택)
    entry_priority_score REAL,              -- 진입 우선순위 점수 (Phase 1 로그/분석용)

    -- ── 최종 의사결정 ─────────────────────────────────────────
    decision        TEXT NOT NULL,          -- BUY_SIGNAL|NO_SIGNAL|BLOCKED|SKIPPED
    strategy_used   TEXT,                   -- 신호 발생 전략명 (BUY_SIGNAL/BLOCKED 시)
    block_reason    TEXT,                   -- BLOCKED/SKIPPED 사유

    -- ── 체결 정보 (BUY_SIGNAL 이후 업데이트) ──────────────────
    filled          INTEGER DEFAULT 0,      -- 0=미체결/해당없음, 1=체결
    order_status    TEXT,                   -- 'FILLED'|'REJECTED'|'PARTIAL'|null

    -- ── 거래 결과 (청산 후 업데이트) ──────────────────────────
    entry_price     REAL,
    exit_price      REAL,
    exit_reason     TEXT,                   -- 'tp'|'sl'|'max_hold'|'manual'
    hold_days       INTEGER,
    pnl_pct         REAL,                   -- 수익률 (%)

    -- ── 선행 수익률 (forward_updater 업데이트) ─────────────────
    forward_1d      REAL,                   -- 1일 후 수익률 (%)
    forward_3d      REAL,
    forward_5d      REAL,

    -- ── 데이터 출처 (라이브/백필 구분) ────────────────────────
    data_source     TEXT DEFAULT 'live',    -- 'live' | 'backfill'
    is_simulated    INTEGER DEFAULT 0       -- 0=실거래, 1=시뮬레이션(백필)
);

-- 주요 조회 패턴 인덱스
CREATE INDEX IF NOT EXISTS idx_decisions_date    ON decisions(session_date);
CREATE INDEX IF NOT EXISTS idx_decisions_market  ON decisions(market, session_date);
CREATE INDEX IF NOT EXISTS idx_decisions_ticker  ON decisions(ticker, session_date);
CREATE INDEX IF NOT EXISTS idx_decisions_signal  ON decisions(decision, session_date);
