# 실행/후보/청산 관찰 개선 계획

작성일: 2026-04-30

## 목표

실행 구조의 명확한 구멍은 즉시 수정하고, 판단 계층은 바로 하드 룰로 잠그지 않는다. 후보 바구니와 청산 판단은 별도 로그로 관찰한 뒤 3~5세션 데이터가 쌓이면 적용 여부를 결정한다.

## 원칙

1. 구조 개선과 판단 보조 룰을 분리한다.
2. 원본 상태(raw)와 파생 판단값을 같은 파일에 저장하지 않는다.
3. 후보 기준 MFE/MAE와 포지션 기준 MFE/MAE는 필드명을 분리한다.
4. opening protection은 즉시 매수 차단 룰이 아니라 후보 생명주기/entry timing 관찰 모델로 시작한다.
5. ORDER_UNKNOWN 차단은 자동 해제와 재시작 생존성을 포함해야 한다.

## Phase 1. Qty Zero / Affordability 분류

대상: KR/US 모두.

### 문제 정의

`qty=0` 자체는 예산이 1주 가격보다 작을 때 올바른 계산일 수 있다. 실제 문제는 매수할 수 없는 종목이 `trade_ready`나 `entry_signal`까지 올라온 정책 불일치다.

### 개발 내용

- 후보/신호 단계에 1주 매수 가능성을 계산한다.
- `affordable_1_share`는 저장용 원본 필드로 쓰지 않는다. 대신 수치 필드와 boolean 필드를 분리한다.
  - `price_per_share_krw`: 1주 매수에 필요한 원화 금액.
  - `affordable_1_share_bool`: 현재 budget/cash 기준 1주 매수 가능 여부.
- 주문 스킵 reason을 세분화한다.
  - `unaffordable_high_price`
  - `min_order_not_met`
  - `budget_too_small`
  - `cash_too_low`
  - 기존 `qty_zero`는 하위 호환용으로만 유지.
- KR도 동일하게 확인한다. 과거 KR `047040`에도 qty_zero 기회 손실이 있었다.
- 무조건 1주 강제 매수는 금지한다.
- micro-probe는 별도 옵션으로만 허용한다.
  - `max_order`
  - `cash`
  - `single_position_risk`
  - `market_budget`
  전부 통과해야 한다.

### 로그 필드

- `ticker`
- `market`
- `price_native`
- `price_krw`
- `budget_krw`
- `available_cash_krw`
- `max_order_krw`
- `computed_qty`
- `price_per_share_krw`
- `affordable_1_share_bool`
- `shortfall_krw`
- `affordability_reason`

## Phase 2. Loss Cap 실거래 검증

`loss_cap`은 이미 구현된 구조 개선으로 유지한다. 추가 룰을 붙이지 않고 실제 체결 로그를 검증한다.

### 검증 필드

- `exit_reason`
- `close_reason`
- `loss_cap_price`
- `strategy_stop_price`
- `effective_stop_price`
- `actual_fill_price`
- `position_mfe_pct`
- `position_mae_pct`
- `post_exit_30m_return_pct`
- `post_exit_close_return_pct`

### 주의

`candidate_health.mfe_pct/mae_pct`는 `first_ready_price` 기준이다. 청산 audit의 MFE/MAE는 `entry_price` 기준이므로 반드시 `position_mfe_pct`, `position_mae_pct`로 기록한다.

## Phase 3. Exit Audit

Claude SELL guard는 바로 추가하지 않는다. 먼저 청산 판단의 품질을 관찰한다.

### 청산 시점 기록

- `exit_owner`
  - `loss_cap`
  - `profit_floor`
  - `hard_rule`
  - `claude_intraday`
  - `pathb_preclose`
  - `manual`
- `exit_reason`
- `last_claude_vote`
- `claude_reason`
- `entry_strategy`
- `entry_priority_score`
- `entry_time`
- `exit_time`
- `hold_minutes`
- `entry_price`
- `exit_price`
- `pnl_pct`
- `position_mfe_pct`
- `position_mae_pct`
- `peak_pnl_pct`
- `profit_floor_triggered`

### 사후 가격 수집

`post_exit_30m_return_pct`:

- 청산 시 `exit_time + 30분` 관찰 작업을 등록한다.
- 만료 시 현재가를 조회해 기록한다.
- 장마감 이후 만료되면 session close 가격으로 대체하고 `post_exit_30m_source=session_close_fallback`을 남긴다.
- 청산 후 30분 타이머 만료 전 봇이 재시작되어 작업이 유실되면, session close 보강 시점에 `post_exit_30m_source=timer_lost_restart_fallback`으로 기록하고 session close 가격으로 대체한다.

`post_exit_close_return_pct`:

- 세션 종료 루틴에서 해당 세션 청산 기록을 일괄 보강한다.
- 가격 조회 실패 시 공란으로 두고 `post_exit_close_source=missing_price`를 남긴다.

## Phase 4. ORDER_UNKNOWN Lifecycle

market block만 추가하는 것은 반쪽짜리다. order number 단위 lifecycle과 자동 해제가 필요하다.

### 상태 필드

- `market`
- `ticker`
- `order_no`
- `side`
- `qty`
- `remaining_qty`
- `local_pending`
- `broker_open`
- `cancel_requested_at`
- `cancel_confirmed_at`
- `cancel_status`
  - `pending`
  - `confirmed`
  - `failed`
- `last_checked_at`
- `next_check_at`
- `resolution`
- `resolved_at`
- `restart_recovered`

### Cancel Failed 처리

`cancel_status=failed`는 자동 해제 대상이 아니다.

- 실패 1회: 즉시 재조회 예약.
- 실패 2회 이상: ops 경고와 dashboard 표시.
- 실패 3회 이상 또는 session close까지 미해결: 수동 개입 필요 상태로 승격.
- 수동 해제 전까지 해당 ticker/market block을 유지한다.

### 차단 scope

새 UI/신규 정책에서는 `ticker`와 `market`만 사용한다. `global` scope는 확장하지 않는다. 기존 코드의 global halt는 호환성 대상으로만 보고, 새 개선 항목의 판단 축으로 삼지 않는다.

### 자동 해제 조건

- broker open order 없음.
- local pending 없음.
- unresolved order_no 없음.
- cancel status가 `confirmed` 또는 broker no-evidence로 확정됨.

위 조건이 모두 충족되면 ticker pause를 해제하고, 해당 market의 unresolved가 0이면 market pause도 자동 해제한다.

### 재시작 생존성

- state 파일에서 unresolved order_no를 복구한다.
- startup/session_open 시 broker truth를 조회해 상태를 갱신한다.
- stale block은 TTL 만료만으로 해제하지 않는다. broker truth 또는 수동 해제가 필요하다.

### 반드시 확인할 테스트

- broker-only 주문 발견 시 market pause.
- cancel confirmed 후 ticker pause 해제.
- 마지막 unresolved가 사라지면 market pause 자동 해제.
- 재시작 후 unresolved order가 복구된다.
- market pause 중에도 sell/loss_cap exit은 차단되지 않는다.

## Phase 5. Candidate Health / Opening Simulation

candidate health는 원본 상태만 저장한다. opening protection 관련 시뮬레이션 결과는 별도 로그로 분리한다.

### candidate_health 저장 필드

- `first_seen_at`
- `first_seen_price`
- `first_ready_at`
- `first_ready_price`
- `last_seen_at`
- `last_price`
- `seen_count`
- `ready_count`
- `mfe_pct`
- `mae_pct`
- `recovered_first_ready`

### candidate_health에 저장하지 않을 필드

- `weaken_flag`
- `health_state`
- `current_vs_first_ready_pct`
- `would_delay_entry`
- `would_block_entry`

파생값은 로드 시점 또는 dashboard query 시점에 계산한다.

### opening simulation 별도 로그

경로 예시:

- `logs/opening_sim/KR_YYYYMMDD.jsonl`
- `logs/opening_sim/US_YYYYMMDD.jsonl`

동일 candidate event를 하나의 레코드로 저장하고, 내부에 시나리오별 결과를 담는다. 시나리오를 별도 레코드로 분리하지 않는다.

공통 필드:

- `session_date`
- `market`
- `ticker`
- `event_time`
- `first_ready_at`
- `ready_age_sec`
- `first_ready_price`
- `last_price`
- `mae_pct`
- `mfe_pct`
- `opening_window`
- `is_initial_ready`
- `is_fresh_ready`
- `actual_signal`
- `actual_trade`
- `actual_block_reason`

시나리오 필드:

- `scenario_immediate`
  - `would_enter`
  - `reason`
  - `reference_price`
- `scenario_delay_10m`
  - `would_enter`
  - `reason`
  - `reference_price`
  - `observed_price_after_10m`
- `scenario_keep_initial_basket`
  - `would_keep`
  - `reason`
  - `basket_age_sec`

### 재평가 방향

재평가 조건은 하방 이탈만 본다. 상방 돌파는 opening simulation에서 차단 사유로 쓰지 않는다. 추격 매수 제한은 PathB zone 또는 별도 entry timing이 담당한다.

## Phase 6. Dashboard

### 후보 바구니 화면

표시 항목:

- `market`
- `ticker`
- `name`
- `last_status`
- `first_ready_at`
- `ready_age`
- `ready_count`
- `first_ready_price`
- `last_price`
- `mfe_pct`
- `mae_pct`
- `price_per_share_krw`
- `affordable_1_share_bool`
- `shortfall_krw`
- `affordability_reason`
- `actual_signal`
- `actual_trade`
- `actual_block_reason`

### opening simulation 화면

candidate_health와 분리된 시뮬레이션 결과를 보여준다.

- 즉시 진입 가정
- 10분 대기 가정
- 초기 basket 유지 가정
- 실제 결과

### exit audit 화면

표시 항목:

- `ticker`
- `market`
- `entry_time`
- `exit_time`
- `strategy`
- `exit_owner`
- `exit_reason`
- `pnl_pct`
- `position_mfe_pct`
- `position_mae_pct`
- `post_exit_30m_return_pct`
- `post_exit_close_return_pct`
- `last_claude_vote`

## 개발 순서

1. KR/US qty_zero 원인 세분화와 `price_per_share_krw` / `affordable_1_share_bool` 로그.
2. loss_cap 실거래 로그 검증.
3. exit audit 필드 추가와 사후 가격 수집 경로 구현.
4. ORDER_UNKNOWN lifecycle 보강.
5. candidate health dashboard 표시.
6. opening simulation 별도 로그와 dashboard 표시.
7. 3~5세션 관찰.
8. opening protection / Claude SELL guard 적용 여부 결정.

## 하지 않을 것

- US 고가주를 무조건 1주 매수로 보정하지 않는다.
- opening protection을 바로 hard block으로 넣지 않는다.
- Claude SELL을 바로 무시하지 않는다.
- ORDER_UNKNOWN market block을 자동 해제 없이 사용하지 않는다.
- candidate_health 파일에 시뮬레이션 결과나 파생 판단값을 저장하지 않는다.

## 진행 상태 - 2026-04-30

### 완료

- KR/US 공통 affordability 진단 helper 추가.
- Path A 기본 진입 경로의 `order_size_too_small`, `qty_zero`, `market_budget_exceeded`, `insufficient_cash`에 affordability 필드 연결.
- KR/US Tier2의 `qty_zero` skip이 조용히 사라지지 않도록 decision event와 analysis log 기록 추가.
- affordability 로그 필드 추가:
  - `price_per_share_krw`
  - `affordable_1_share_bool`
  - `shortfall_krw`
  - `affordability_reason`
  - `order_budget_krw`
  - `available_budget_krw`
  - `available_cash_krw`
  - `min_effective_order_krw`
- 포지션 기준 audit 필드 추가:
  - `trough_pnl_pct`
  - `position_mfe_pct`
  - `position_mae_pct`
  - `actual_fill_price`
- Path A/RiskManager/PathB close meta에 `position_mfe_pct`, `position_mae_pct` 연결.
- 단위 테스트 추가:
  - `tests/test_affordability_diag.py`
  - `tests/test_loss_cap_profit_floor.py`의 position MFE/MAE 검증.

### 검증 완료

- `python -m py_compile trading_bot.py risk_manager.py runtime\pathb_runtime.py tests\test_affordability_diag.py tests\test_loss_cap_profit_floor.py`
- `python -m unittest tests.test_affordability_diag tests.test_loss_cap_profit_floor`
- `python -m unittest tests.test_live_order_safety tests.test_affordability_diag tests.test_loss_cap_profit_floor`

### 남은 항목

- `post_exit_30m_return_pct` 타이머와 재시작 fallback 구현.
- `post_exit_close_return_pct` 세션 종료 보강 구현.
- ORDER_UNKNOWN lifecycle의 `cancel_status`, `cancel_confirmed_at`, auto-release 보강.
- candidate health dashboard 표시.
- opening simulation 별도 JSONL 로그와 dashboard 표시.
- 3~5세션 관찰 후 opening protection / Claude SELL guard 적용 여부 결정.
