# trading_bot.py 모듈화 계획

> 완료된 항목은 즉시 이 문서에서 삭제한다.
> 각 단계는 반드시 검증 후 커밋한다. 로직 변경 없이 이동만 한다.

---

## 현재 상태

```
trading_bot.py  6,999줄 / 클래스 1개 (bot/market_utils, bot/state 분리 완료 후)
```

단일 파일이라 특정 기능 수정 시 전체 컨텍스트를 로드해야 함.
모듈화 후 평균 파일 크기 ~500줄 → 작업 범위에 맞는 파일만 로드 가능.

---

## 설계 원칙

### 1. 로직 변경 없이 이동만 한다

이 작업의 본질은 **파일 재배치**다. 어떤 메서드도 동작을 바꾸지 않는다.
리팩토링·최적화·변수명 변경은 이 작업과 별개다.

### 2. Mixin은 과도기 구조다

파일 분리에 Python Mixin (다중 상속) 패턴을 사용한다.

```python
class TradingBot(BrokerMixin, ExecutionMixin, ...):
    pass
```

**왜 Mixin인가**: 118개 메서드의 `self.method()` 호출 구조를 바꾸지 않고 파일만 나눌 수 있는 유일한 방법.
컴포지션(의존성 주입) 패턴이 아키텍처상 더 깔끔하지만, 모든 호출부를 바꿔야 해서 실거래 직전 상태에서 리스크가 너무 큼.

**Mixin의 한계 (인지하고 사용한다)**:
- 모든 Mixin이 `self` 전체 상태에 접근 → 결합도는 여전히 높음
- 어떤 메서드가 어떤 상태를 읽고 쓰는지 파일 경계만 보고 알기 어려움
- 순환 의존이 import 단계에서 잘 드러나지 않음

**최종 목표**: Mixin 완료 후 안정화되면 컴포지션 패턴으로 점진 전환. 이 문서는 1차 분해 계획이다.

### 3. scanner.py는 1차에서 단일 파일로 분리한다

run_cycle 내부 nested function들(`_ap`, `_ca`, `_log_or_probe` 등)은
외부 루프의 지역 변수를 클로저로 참조한다.
4개 파일로 세분화하려면 클로저를 명시적 파라미터 전달로 변환해야 하는데,
이는 "이동만" 원칙과 충돌한다.

**1차**: scanner.py 단일 파일 (~1700줄)로 분리
**2차**: scanner 내부 구조 재설계 (별도 단계, 아래 기술)

---

## 목표 구조

```
trading_bot.py          ← 진입점 + TradingBot 클래스 선언만 (~200줄)
bot/
  __init__.py
  market_utils.py       ~180줄   시간/블랙아웃/장 진행도 유틸
  state.py              ~380줄   영속성 (save/load positions·orders·baselines)
  ops.py                ~390줄   startup health check, task 관리, 후보 필터
  dashboard.py          ~570줄   live_status, tg_state, decision_event
  tuning.py             ~340줄   run_tuning, history fill, ohlcv 캐시
  broker.py             ~520줄   broker sync, trust/state, equity/pnl
  execution.py          ~580줄   _execute_sell, pending orders, 주문 유틸
  positions.py          ~700줄   restore, review, hold_advisor, TP/SL
  reinvoke.py           ~310줄   reinvoke_analysts, reselect, consume triggers
  scanner.py           ~1700줄   run_cycle 신호 로직 전체 (1차)
  session.py            ~450줄   세션 생명주기 (open/close/일일 기준선)
  cycle.py              ~320줄   장중 반복 루프 (run_cycle 스켈레톤, tick, fill)
```

### 최종 TradingBot 구조

```python
# trading_bot.py
from bot.market_utils import MarketUtilsMixin
from bot.state       import StateMixin
from bot.ops         import OpsMixin
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
    MarketUtilsMixin, StateMixin, OpsMixin,
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
> 회계/계정 경계 버그가 남은 상태에서 파일만 쪼개면 버그가 더 찾기 어려워진다.

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

- [x] **`bot/market_utils.py`** ✅ 완료
  - 이동: 클래스 메서드 10개 + 모듈 레벨 함수 2개 (`_market_session_date`, `_is_trading_day`)
  - `_EXCHANGE_MAP`, `_ec_cache` 함께 이동, trading_bot.py에서 re-export
  - `TradingBot(MarketUtilsMixin)` 상속 추가

- [x] **`bot/state.py`** ✅ 완료
  - 이동: 클래스 메서드 13개
  - path 상수 4개(`POSITIONS_FILE`, `PENDING_ORDERS_FILE`, `CLAUDE_CONTROL_FILE`, `DAILY_BASELINE_FILE`) state.py로 이동 + trading_bot.py re-export
  - `TradingBot(MarketUtilsMixin, StateMixin)` 상속 추가

- [ ] **`bot/ops.py`** (~390줄)
  - ※ 기존 계획의 `health.py`에서 역할 재정의. startup + 운영 보조 + 후보 필터
  - 이동할 메서드:
    `_startup_health_check`,
    `_enter_market_task`, `_leave_market_task`,
    `manual_rescreen`, `_filter_candidates_by_history`,
    `_log_screen_candidates`, `_flush_funnel`,
    `_build_intraday_context`, `_advisor_pos`
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

- [ ] **`bot/scanner.py`** (~1700줄) ← 1차 단순 이동
  - run_cycle 내부 신호 로직 전체
  - nested function 포함 (클로저 의존 → 이동만, 구조 변경 없음):
    `_ml_base_row`, `_ml_write_eval`, `_ca`, `_ap`,
    `_orp_detail`, `_gap_detail`, `_mr_detail`, `_vb_detail`,
    `_classify_rejection`, `_log_or_probe`, `_cont_detail_str`
  - KR/US 신호 체인, Tier2 섹터 플레이

- [ ] **`bot/session.py`** (~450줄)
  - **역할**: 장 시작/종료/일일 기준선/판단 재사용 등 세션 생명주기
  - 이동할 메서드:
    `session_open`, `_run_param_review`,
    `_pre_session_position_review`, `_post_session_position_review`,
    `_backfill_missed_postmortem`, `_should_run_pre_session_review`

- [ ] **`bot/cycle.py`** (~320줄)
  - **역할**: 장중 반복 루프 스케줄링, 실시간 이벤트 처리
  - 이동할 메서드:
    `run_cycle` (scanner로 위임 후 스켈레톤),
    `run_entry_scan`, `run_housekeeping`,
    `_entry_scan_interval_sec`, `_market_budget_available`,
    `_on_tick`, `_on_fill_notice`
  - ※ `_on_tick`, `_on_fill_notice`는 장중 실시간 이벤트 → session이 아닌 cycle

---

### 🟣 P4 — scanner 2차 분해 (P3 완료 후, 별도 설계 필요)

> 1차 분리 후 scanner.py가 여전히 ~1700줄이면 추가 세분화한다.
> nested function의 클로저 의존을 명시적 파라미터로 변환하는 설계가 선행되어야 한다.

- [ ] **`bot/scanner_signals.py`** — KR/US 전략별 신호 체인
- [ ] **`bot/scanner_detail.py`** — `_orp_detail`, `_gap_detail`, `_mr_detail`, `_vb_detail`
- [ ] **`bot/scanner_ml.py`** — `_ml_base_row`, `_ml_write_eval`
- [ ] **`bot/scanner_log.py`** — `_classify_rejection`, `_log_or_probe`, `_cont_detail_str`

---

## 단계별 검증 기준

각 모듈 분리 후 아래 순서로 반드시 검증한다:

```bash
# 1. 분리된 모듈 전체 구문 검사
python -m py_compile trading_bot.py bot/*.py risk_manager.py kis_api.py

# 2. import 오류 없음 확인
python -c "from trading_bot import TradingBot; print('import OK')"

# 3. 분리한 모듈 직접 import 확인
python -c "from bot.market_utils import MarketUtilsMixin; print('module OK')"

# 4. 단위 테스트
python -m unittest test_broker_sync_cash.py -v

# 5. 봇 부팅만 확인 (1사이클 전)
python trading_bot.py --paper  # 세션 시작 전 init 단계까지만 확인

# 6. 변경 내용 확인 (로직 변경 없이 이동만)
git diff --stat
git status --short
```

---

## 작업 원칙

- **로직은 절대 변경하지 않는다** — 파일 이동만 한다. 리팩토링은 별도 작업
- **한 번에 하나의 모듈만** 분리하고 검증 후 커밋
- 커밋 메시지 형식: `refactor: bot/[모듈명] 분리 — trading_bot.py에서 이동 (로직 변경 없음)`
- 분리 완료된 항목은 이 문서에서 즉시 삭제
- scanner 1차 분리 후에도 여전히 큰 경우 P4 진행 전 설계 세션 별도 진행

---

## 관련 파일

- `trading_bot.py` — 현재 전체 코드
- `test_broker_sync_cash.py` — 브로커 동기화 targeted harness
- `CLAUDE.md` — 전체 원칙 및 아키텍처
- `docs/trading_process.md` — 매매 흐름 기준 문서
