# trading_bot.py 모듈화 계획

> 완료된 항목은 즉시 이 문서에서 삭제한다.
> 각 단계는 반드시 검증 후 커밋한다. 로직 변경 없이 이동만 한다.

---

## 현재 상태

```
trading_bot.py  7,261줄 / 118개 메서드 / 클래스 1개
```

단일 파일이라 특정 기능 수정 시 전체 컨텍스트를 로드해야 함.
모듈화 후 평균 파일 크기 ~500줄 → 작업 범위에 맞는 파일만 로드 가능.

---

## 목표 구조

```
trading_bot.py          ← 진입점 + TradingBot 클래스 선언만 (~200줄)
bot/
  __init__.py
  market_utils.py       ~180줄
  state.py              ~380줄
  health.py             ~390줄
  dashboard.py          ~570줄
  tuning.py             ~340줄
  broker.py             ~520줄
  execution.py          ~580줄
  positions.py          ~700줄
  reinvoke.py           ~310줄
  scanner.py           ~1700줄  ← run_cycle 신호 로직
  session.py            ~450줄
  cycle.py              ~320줄
```

### 최종 TradingBot 구조

```python
# trading_bot.py
from bot.market_utils import MarketUtilsMixin
from bot.state       import StateMixin
from bot.health      import HealthMixin
from bot.broker      import BrokerMixin
from bot.execution   import ExecutionMixin
from bot.positions   import PositionsMixin
from bot.session     import SessionMixin
from bot.cycle       import CycleMixin
from bot.scanner     import ScannerMixin
from bot.dashboard   import DashboardMixin
from bot.tuning      import TuningMixin
from bot.reinvoke    import ReInvokeMixin

class TradingBot(
    MarketUtilsMixin, StateMixin, HealthMixin,
    BrokerMixin, ExecutionMixin, PositionsMixin,
    SessionMixin, CycleMixin, ScannerMixin,
    DashboardMixin, TuningMixin, ReInvokeMixin,
):
    def __init__(self, is_paper: bool = True):
        ...
```

---

## 우선순위 및 진행 상태

### 🔴 P0 — 실거래 전 필수 (모듈화 아님, 정확성 버그)

> 이 두 작업은 모듈화보다 먼저 완료해야 한다.

- [ ] **RiskManager KR/US 분리**
  - `self.risk` 단일 풀 → `risk_kr` / `risk_us` 분리
  - `_rm(market)` 헬퍼 추가
  - `_sync_runtime_with_broker`, `run_cycle`, `session_open`, `session_close` 전체 분기
  - 완료 전까지 `_sync_runtime_with_broker` 임시 패치 유지

- [ ] **kis_api.py US 자격증명 분리**
  - `KIS_ACCOUNT_NO_US`, `KIS_APP_KEY_US`, `KIS_APP_SECRET_US`, `KIS_IS_PAPER_US`
  - .env에는 있으나 코드 미연결 상태

---

### 🟡 P1 — 모듈화 1단계 (실거래 로직 무관, 리스크 낮음)

- [ ] **`bot/market_utils.py`** (~180줄)
  - 이동할 메서드:
    `_in_entry_blackout`, `_is_order_allowed_now`, `_intraday_session_progress`,
    `_market_open_anchor_dt`, `_market_close_anchor_dt`, `_minutes_to_close`,
    `_next_market_open_dt`, `_market_elapsed_min`, `_project_intraday_volume`,
    `_is_market_session_now`, `_market_session_date`, `_is_trading_day`
  - 의존성: `ZoneInfo`, `datetime`, `kis_api` (장 시간 상수)
  - 외부 의존성 없음 → 가장 먼저 분리 가능

- [ ] **`bot/state.py`** (~380줄)
  - 이동할 메서드:
    `_save_positions`, `_restore_pending_orders`, `_normalize_pending_orders`,
    `_load_daily_baselines`, `_save_daily_baselines`, `_save_daily_baselines`,
    `_default_claude_control`, `_save_claude_control`, `_normalize_claude_control_state`,
    `_restore_claude_control`, `_refresh_claude_control`, `_sanitize_live_status_file`,
    `_save_pending_orders`, `_parse_pending_created_at`
  - 의존성: `runtime_paths`, `json`, `os`

- [ ] **`bot/health.py`** (~390줄)
  - 이동할 메서드:
    `_startup_health_check`, `manual_rescreen`, `_filter_candidates_by_history`,
    `_log_screen_candidates`, `_flush_funnel`, `_build_intraday_context`,
    `_advisor_pos`, `_enter_market_task`, `_leave_market_task`
  - 의존성: `kis_api`, `telegram_reporter`

- [ ] **`bot/dashboard.py`** (~570줄)
  - 이동할 메서드:
    `_write_live_status`, `_build_tg_state`, `_record_decision_event`,
    `_maybe_push_dashboard`, `_notify_signal_state_change`,
    `_persist_live_judgment`, `_build_execution_health`
  - 의존성: `telegram_reporter`, `runtime_paths`
  - 실거래 로직과 완전히 분리 → 안전

- [ ] **`bot/tuning.py`** (~340줄)
  - 이동할 메서드:
    `run_tuning`, `_prefill_history_sync`, `_hist_fill_enqueue`,
    `_history_fill_worker`, `_get_ohlcv_cached`, `run_rescreen`
  - 의존성: `minority_report/tuner`, `kis_api`

---

### 🟠 P2 — 모듈화 2단계 (중간 의존성, P1 완료 후)

- [ ] **`bot/broker.py`** (~520줄)
  - 이동할 메서드:
    `_sync_runtime_with_broker`, `_make_runtime_position_from_broker`,
    `_normalize_broker_balance`, `_broker_snapshot_from_balance`,
    `_broker_trust_level`, `_entry_allowed_by_broker_state`,
    `_set_broker_state`, `_flag_execution_issue`,
    `_refresh_operational_halt`, `_has_broker_sync_risk`,
    `_internal_total_equity_krw`, `_kis_total_equity_krw`,
    `_equity_reference_context`, `_daily_pnl_pct`
  - 의존성: `risk_manager`, `kis_api`
  - ⚠️ RiskManager KR/US 분리 완료 후 작업

- [ ] **`bot/execution.py`** (~580줄)
  - 이동할 메서드:
    `_execute_sell`, `_compute_order_price`, `_estimate_slippage_bps`,
    `_add_pending_order`, `_clear_pending_orders_for_market`,
    `_reconcile_pending_orders`, `_make_position_from_broker`,
    `_reset_us_order_cache`, `_mark_us_order_supported`,
    `_mark_us_order_blocked`, `_us_order_block_reason`,
    `_has_pending_order`, `_has_open_position`,
    `_block_entry`, `_is_entry_blocked`, `_has_same_day_trade`
  - 의존성: `kis_api`, `risk_manager`

- [ ] **`bot/positions.py`** (~700줄)
  - 이동할 메서드:
    `_restore_positions`, `_verify_live_positions`,
    `_recover_decision_id`, `_pre_session_position_review`,
    `_post_session_position_review`, `_intraday_position_review`,
    `_handle_tp_trailing`, `_handle_max_hold_claude`,
    `_process_exit_candidates`, `_should_run_pre_session_review`,
    `_record_hold_advisor_outcome`, `_update_hold_advisor_jsonl_outcome`
  - 의존성: `risk_manager`, `kis_api`, `minority_report`

- [ ] **`bot/reinvoke.py`** (~310줄)
  - 이동할 메서드:
    `_reinvoke_analysts`, `_should_reinvoke_analysts`,
    `_partial_reselect`, `_get_cooldown_excluded`,
    `_consume_pending_claude_trigger`, `_consume_pending_position_review`,
    `_consume_pending_sell`, `_backfill_missed_postmortem`
  - 의존성: `minority_report/analysts`, `kis_api`

---

### 🔵 P3 — 모듈화 3단계 (핵심, P2 완료 후, 가장 신중하게)

- [ ] **`bot/scanner.py`** (~1700줄) ← 가장 큰 작업
  - run_cycle 내부 신호 로직 전체
  - nested function 포함:
    `_ml_base_row`, `_ml_write_eval`, `_ca`, `_ap`,
    `_orp_detail`, `_gap_detail`, `_mr_detail`, `_vb_detail`,
    `_classify_rejection`, `_log_or_probe`, `_cont_detail_str`
  - KR/US 신호 체인, Tier2 섹터 플레이
  - ⚠️ nested function을 어떻게 이동할지 설계 먼저

- [ ] **`bot/session.py`** (~450줄)
  - 이동할 메서드:
    `session_open`, `_run_param_review`,
    `_on_tick`, `_on_fill_notice`
  - 의존성: 거의 모든 모듈

- [ ] **`bot/cycle.py`** (~320줄)
  - 이동할 메서드:
    `run_cycle` (스켈레톤 — scanner로 위임 후),
    `run_entry_scan`, `run_housekeeping`,
    `_entry_scan_interval_sec`, `_market_budget_available`

---

## 단계별 검증 기준

각 모듈 분리 후 아래 순서로 반드시 검증한다:

```bash
# 1. import 오류 없음 확인
python -c "from trading_bot import TradingBot; print('import OK')"

# 2. 구문 검사
python -c "import ast; ast.parse(open('trading_bot.py', encoding='utf-8').read()); print('syntax OK')"

# 3. 단위 테스트
python -m unittest test_broker_sync_cash.py -v

# 4. 변경 내용 확인 (로직 변경 없이 이동만)
git diff --stat

# 5. 모의투자 1사이클 실행 후 로그 확인
# logs/system/trading_YYYYMMDD.log 에서 오류 없음 확인
```

---

## 작업 원칙

- **로직은 절대 변경하지 않는다** — 파일 이동만 한다. 리팩토링은 별도 작업
- **한 번에 하나의 모듈만** 분리하고 검증 후 커밋
- 커밋 메시지 형식: `refactor: [모듈명] trading_bot.py에서 분리 (로직 변경 없음)`
- 분리 완료된 항목은 이 문서에서 즉시 삭제
- scanner.py는 별도 설계 세션 필요 (nested function 처리 방식 결정)

---

## 관련 파일

- `trading_bot.py` — 현재 전체 코드
- `test_broker_sync_cash.py` — 브로커 동기화 단위 테스트
- `CLAUDE.md` — 전체 원칙 및 아키텍처
- `docs/trading_process.md` — 매매 흐름 기준 문서
