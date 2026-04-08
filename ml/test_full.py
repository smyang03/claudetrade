"""ml/test_full.py — ML DB 전체 기능 검증"""
import sys, json, sqlite3
from datetime import date, datetime, time as dt_time
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

from ml.db_writer import (
    init_db, write_decision, update_filled, update_trade_outcome,
    update_forward_returns, load_for_ml, print_stats, _get_conn, STANCE_ORDER,
)

print('=' * 60)
print('ML DB 전체 기능 검증')
print('=' * 60)

# 초기화
conn = sqlite3.connect(str(__import__('pathlib').Path(__file__).parent.parent / 'data' / 'ml' / 'decisions.db'))
conn.execute('DELETE FROM decisions')
conn.commit()
conn.close()

errors = []

def check(label, cond, detail=''):
    if cond:
        print(f'  [OK] {label}')
    else:
        msg = f'  [FAIL] {label}'
        if detail:
            msg += f' — {detail}'
        print(msg)
        errors.append(label)

# ── 1. init_db + 컬럼 검증 ──────────────────────────────────────
print('\n[1] DB 초기화 및 컬럼 검증')
init_db()
with _get_conn() as conn:
    cols = {row[1] for row in conn.execute('PRAGMA table_info(decisions)')}
required = {
    'id','ts','market','ticker','session_date',
    'mode','mode_score','bull_stance','bear_stance','neut_stance',
    'bull_conf','bear_conf','neut_conf','vix','usd_krw',
    'price','rsi','bb_pct','vol_ratio','macd','macd_signal',
    'ma20','ma60','atr','gap_pct','change_pct',
    'mr_rsi_thr','mr_bb_thr','mr_rsi_miss','mr_bb_miss',
    'mr_vol_ok','mr_ma_ok','mr_fired',
    'vb_target','vb_close_miss','vb_vol_ok','vb_fired',
    'mom_ma_ok','mom_macd_ok','mom_vol_ok','mom_high_ok','mom_fired',
    'gap_gap_miss','gap_vol_ok','gap_pullback_ok','gap_fired',
    'diag_json','decision','strategy_used','block_reason',
    'filled','order_status',
    'entry_price','exit_price','exit_reason','hold_days','pnl_pct',
    'forward_1d','forward_3d','forward_5d',
}
missing = required - cols
check('필수 컬럼 전체 존재', not missing, f'누락: {missing}')
check('strategy_used 컬럼 존재', 'strategy_used' in cols)

# ── 2. session_date KR/US 분기 ──────────────────────────────────
print('\n[2] session_date KR/US 날짜 분기')
from zoneinfo import ZoneInfo
KST = ZoneInfo('Asia/Seoul')

# trading_bot의 _market_session_date 로직 직접 복제 검증
def _market_session_date(market, now_dt=None):
    now_dt = now_dt or datetime.now(KST)
    d = now_dt.date()
    if market == 'US' and now_dt.time() < dt_time(5, 0):
        return d - __import__('datetime').timedelta(days=1)
    return d

check('KR session_date = 오늘', _market_session_date('KR') == date.today())

t1 = datetime(2026, 4, 2, 22, 0, tzinfo=KST)
check('US 22:00 KST → 당일(4/2)', _market_session_date('US', t1) == date(2026, 4, 2))

t2 = datetime(2026, 4, 3, 2, 0, tzinfo=KST)
check('US 02:00 KST → 전일(4/2)', _market_session_date('US', t2) == date(2026, 4, 2))

t3 = datetime(2026, 4, 3, 5, 1, tzinfo=KST)
check('US 05:01 KST → 당일(4/3)', _market_session_date('US', t3) == date(2026, 4, 3))

# ── 3. write_decision — 5가지 decision 유형 ─────────────────────
print('\n[3] write_decision — 5가지 decision 유형')

d_nosig = write_decision({
    'market': 'KR', 'ticker': '005930', 'decision': 'NO_SIGNAL',
    'mode': 'CAUTIOUS_BEAR', 'price': 59800,
    'bull_stance': 'MILD_BULL', 'bear_stance': 'CAUTIOUS_BEAR', 'neut_stance': 'NEUTRAL',
    'bull_conf': 0.55, 'bear_conf': 0.70, 'neut_conf': 0.50,
    'rsi': 32.9, 'bb_pct': 22.3, 'vol_ratio': 0.8,
    'mr_rsi_thr': 30, 'mr_bb_thr': 17,
    'mr_rsi_miss': 2.9, 'mr_bb_miss': 5.3,
    'mr_vol_ok': True, 'mr_ma_ok': True, 'mr_fired': False,
    'diag_json': {'none_detail': 'RSI 부족'},
})
check('NO_SIGNAL INSERT', d_nosig > 0)

d_blocked = write_decision({
    'market': 'US', 'ticker': 'NVDA', 'decision': 'BLOCKED',
    'strategy_used': 'volatility_breakout', 'block_reason': 'HALT_mode_block',
    'mode': 'HALT', 'price': 875.0,
    'vb_fired': True, 'vb_target': 860.0, 'vb_close_miss': 15.0,
})
check('BLOCKED INSERT', d_blocked > 0)

d_skip1 = write_decision({
    'market': 'KR', 'ticker': '000660', 'decision': 'SKIPPED',
    'block_reason': 'low_confidence', 'mode': 'CAUTIOUS_BEAR',
    'price': 123000, 'diag_json': {'avg_conf': 0.38},
})
check('SKIPPED (low_conf) INSERT', d_skip1 > 0)

d_skip2 = write_decision({
    'market': 'KR', 'ticker': '035420', 'decision': 'SKIPPED',
    'block_reason': 'already_holding', 'mode': 'MILD_BEAR', 'price': 58000,
})
check('SKIPPED (already_holding) INSERT', d_skip2 > 0)

d_buy = write_decision({
    'market': 'US', 'ticker': 'TSLA', 'decision': 'BUY_SIGNAL',
    'strategy_used': 'mean_reversion',
    'mode': 'MODERATE_BULL', 'price': 252.0,
    'rsi': 27.3, 'bb_pct': 7.1, 'vol_ratio': 1.9,
    'mr_rsi_thr': 32, 'mr_bb_thr': 20,
    'mr_rsi_miss': -4.7, 'mr_bb_miss': -12.9,
    'mr_vol_ok': True, 'mr_ma_ok': True, 'mr_fired': True,
    'bull_conf': 0.68, 'bear_conf': 0.45, 'neut_conf': 0.55,
    'vix': 18.3, 'usd_krw': 1385.0,
})
check('BUY_SIGNAL INSERT', d_buy > 0)

# ── 4. stance 인코딩 ─────────────────────────────────────────────
print('\n[4] Stance 인코딩')
with _get_conn() as conn:
    row = conn.execute(
        'SELECT bull_stance, bear_stance, neut_stance FROM decisions WHERE id=?',
        (d_nosig,)
    ).fetchone()
check('bull_stance MILD_BULL', row[0] == STANCE_ORDER.index('MILD_BULL'), f'got {row[0]}')
check('bear_stance CAUTIOUS_BEAR', row[1] == STANCE_ORDER.index('CAUTIOUS_BEAR'), f'got {row[1]}')
check('neut_stance NEUTRAL', row[2] == STANCE_ORDER.index('NEUTRAL'), f'got {row[2]}')

# ── 5. strategy_used & fired 플래그 ─────────────────────────────
print('\n[5] strategy_used & fired 플래그')
with _get_conn() as conn:
    buy_row = conn.execute(
        'SELECT strategy_used, mr_fired, mr_rsi_miss FROM decisions WHERE id=?',
        (d_buy,)
    ).fetchone()
    blk_row = conn.execute(
        'SELECT strategy_used, vb_fired, block_reason FROM decisions WHERE id=?',
        (d_blocked,)
    ).fetchone()
check('BUY_SIGNAL strategy_used=mean_reversion', buy_row[0] == 'mean_reversion', f'got {buy_row[0]}')
check('BUY_SIGNAL mr_fired=1', buy_row[1] == 1, f'got {buy_row[1]}')
check('BUY_SIGNAL mr_rsi_miss=-4.7', abs(buy_row[2] - (-4.7)) < 0.01, f'got {buy_row[2]}')
check('BLOCKED strategy_used=volatility_breakout', blk_row[0] == 'volatility_breakout')
check('BLOCKED vb_fired=1', blk_row[1] == 1)
check('BLOCKED block_reason=HALT_mode_block', blk_row[2] == 'HALT_mode_block')

# ── 6. diag_json ────────────────────────────────────────────────
print('\n[6] diag_json 직렬화')
with _get_conn() as conn:
    diag_raw = conn.execute('SELECT diag_json FROM decisions WHERE id=?', (d_nosig,)).fetchone()[0]
    diag_skip = conn.execute('SELECT diag_json FROM decisions WHERE id=?', (d_skip1,)).fetchone()[0]
diag = json.loads(diag_raw)
check('diag_json 역직렬화', isinstance(diag, dict))
check('diag_json 값 보존', diag.get('none_detail') == 'RSI 부족')
diag2 = json.loads(diag_skip)
check('SKIPPED diag avg_conf', abs(diag2.get('avg_conf', 0) - 0.38) < 0.01)

# ── 7. update_filled 체인 ────────────────────────────────────────
print('\n[7] update_filled 체인')
update_filled(d_buy, 'FILLED')
with _get_conn() as conn:
    row = conn.execute(
        'SELECT filled, order_status FROM decisions WHERE id=?', (d_buy,)
    ).fetchone()
check('filled=1', row[0] == 1)
check('order_status=FILLED', row[1] == 'FILLED')
update_filled(-1, 'FILLED')
check('decision_id=-1 안전', True)

# ── 8. update_trade_outcome ──────────────────────────────────────
print('\n[8] update_trade_outcome')
update_trade_outcome(d_buy, 252.0, 259.5, 'tp', 2, 2.98)
with _get_conn() as conn:
    row = conn.execute(
        'SELECT entry_price, exit_price, exit_reason, hold_days, pnl_pct FROM decisions WHERE id=?',
        (d_buy,)
    ).fetchone()
check('entry_price=252.0', abs(row[0] - 252.0) < 0.01)
check('exit_price=259.5', abs(row[1] - 259.5) < 0.01)
check('exit_reason=tp', row[2] == 'tp')
check('hold_days=2', row[3] == 2)
check('pnl_pct=2.98', abs(row[4] - 2.98) < 0.01)
update_trade_outcome(-1, 0, 0, 'tp', 0, 0)
check('trade_outcome decision_id=-1 안전', True)

# ── 9. update_forward_returns ────────────────────────────────────
print('\n[9] update_forward_returns (near-miss)')
update_forward_returns(d_nosig, 1.8, 3.2, 5.1)
with _get_conn() as conn:
    row = conn.execute(
        'SELECT forward_1d, forward_3d, forward_5d FROM decisions WHERE id=?',
        (d_nosig,)
    ).fetchone()
check('forward_1d=1.8', abs(row[0] - 1.8) < 0.01)
check('forward_3d=3.2', abs(row[1] - 3.2) < 0.01)
check('forward_5d=5.1', abs(row[2] - 5.1) < 0.01)
update_forward_returns(-1, 0, 0, 0)
check('forward_returns decision_id=-1 안전', True)

# ── 10. load_for_ml 필터 ────────────────────────────────────────
print('\n[10] load_for_ml 필터')
import math

df_all = load_for_ml()
check('전체 로드 5건', len(df_all) == 5, f'got {len(df_all)}')
check('decision_id 컬럼 존재', 'decision_id' in df_all.columns)

df_trade = load_for_ml(with_trade_result=True)
check('with_trade_result → 1건(BUY)', len(df_trade) == 1, f'got {len(df_trade)}')
check('with_trade_result 행: BUY_SIGNAL', df_trade.iloc[0]['decision'] == 'BUY_SIGNAL')

df_fwd = load_for_ml(with_forward_return=True)
check('with_forward_return → 1건(NO_SIGNAL)', len(df_fwd) == 1, f'got {len(df_fwd)}')
check('with_forward_return 행: NO_SIGNAL', df_fwd.iloc[0]['decision'] == 'NO_SIGNAL')
import pandas as _pd
check('NO_SIGNAL near-miss: pnl_pct=NULL', _pd.isna(df_fwd.iloc[0]['pnl_pct']))

df_both = load_for_ml(with_trade_result=True, with_forward_return=True)
check('두 필터 AND → 0건', len(df_both) == 0, f'got {len(df_both)}')

df_kr = load_for_ml(market='KR')
df_us = load_for_ml(market='US')
check('KR 필터 → 3건', len(df_kr) == 3, f'got {len(df_kr)}')
check('US 필터 → 2건', len(df_us) == 2, f'got {len(df_us)}')

# ── 11. _recover_decision_id 로직 ───────────────────────────────
print('\n[11] _recover_decision_id 로직')
today = date.today().isoformat()

# filled=1인 행은 복구 대상 아님
with _get_conn() as conn:
    row = conn.execute(
        "SELECT id FROM decisions WHERE market=? AND ticker=? AND session_date=? "
        "AND decision='BUY_SIGNAL' AND filled=0 ORDER BY id DESC LIMIT 1",
        ('US', 'TSLA', today)
    ).fetchone()
check('filled=1 행은 복구 제외', row is None)

# filled=0인 새 BUY_SIGNAL → 복구 가능
d_new = write_decision({
    'market': 'KR', 'ticker': '068270', 'decision': 'BUY_SIGNAL',
    'strategy_used': 'gap_pullback', 'mode': 'NEUTRAL', 'price': 85000,
})
with _get_conn() as conn:
    row2 = conn.execute(
        "SELECT id FROM decisions WHERE market=? AND ticker=? AND session_date=? "
        "AND decision='BUY_SIGNAL' AND filled=0 ORDER BY id DESC LIMIT 1",
        ('KR', '068270', today)
    ).fetchone()
check('미체결 BUY_SIGNAL 복구 조회', row2 is not None and row2[0] == d_new)

# ── 12. 인덱스 존재 확인 ─────────────────────────────────────────
print('\n[12] 인덱스 검증')
with _get_conn() as conn:
    idxs = {row[1] for row in conn.execute("SELECT * FROM sqlite_master WHERE type='index'")}
check('idx_decisions_date', 'idx_decisions_date' in idxs)
check('idx_decisions_market', 'idx_decisions_market' in idxs)
check('idx_decisions_ticker', 'idx_decisions_ticker' in idxs)
check('idx_decisions_signal', 'idx_decisions_signal' in idxs)

# ── 13. WAL 모드 확인 ────────────────────────────────────────────
print('\n[13] SQLite WAL 모드')
with _get_conn() as conn:
    mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
check('journal_mode=wal', mode == 'wal', f'got {mode}')

# ── 최종 통계 ────────────────────────────────────────────────────
print()
print_stats()
print_stats('KR')
print_stats('US')

print()
if errors:
    print(f'[RESULT] {len(errors)}개 FAIL:')
    for e in errors:
        print(f'  - {e}')
    sys.exit(1)
else:
    print(f'[RESULT] 전체 {sum(1 for _ in open(__file__, encoding="utf-8") if "check(" in _)} 검증 항목 모두 통과')
