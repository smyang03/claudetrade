# Strategy Flow Audit Requirements - 2026-06-02

## 1. Purpose

이 문서는 KR/US 자동매매 시스템의 전략 모드, 후보 선정, Path A/Path B 진입,
청산, hold advisor, broker truth, 성과 기록까지 이어지는 전체 흐름을
코드레벨에서 점검하기 위한 요구서다.

점검의 목적은 단순히 코드가 존재하는지 확인하는 것이 아니다. 각 전략이
실제로 운영 흐름에 반영되고 있는지, 중간에 값이 누락되거나 다른 이름으로
끊기지 않는지, 의도된 guard와 실수로 생긴 보수화가 구분되는지, 그리고 각
흐름이 성과 지표까지 연결되는지 확인하는 것이다.

최종 목적:

1. 각 전략/게이트/전이 항목별로 "값이 어디서 만들어지고 어디로 흘러야
   하는지"를 코드 경로로 고정한다.
2. 값 누락, stale 값, data_quality mismatch, broker truth 불신, shadow-only
   유지, config gate, affordability/risk block을 분리한다.
3. 각 항목별로 운영 결과와 성과 지표를 본다.
4. 개선 대상은 "성과가 낮다"가 아니라 "성과를 볼 수 있을 만큼 흐름이
   정합한가"와 "정상 흐름인데 지나치게 막히는가"로 나눠 판단한다.

## 2. Non-Goals And Safety

이 요구서는 점검 요구서이며, 아래 변경을 승인하지 않는다.

- `.env*`, `config/v2_start_config.json`, live PathB 운영 파라미터 변경
- 주문금액, max position, confidence, slippage cap, cooldown 변경
- hard stop, loss cap, profit ladder, broker truth fail-closed 완화
- `state/brain.json` 직접 수정 또는 자동 정책 메모리 승격
- ORDER_UNKNOWN, stale PathB row 자동 삭제
- broker holding/open order/fill truth보다 local/event store를 우선하는 변경

보호 영역을 직접 수정해야 하는 경우 AGENTS.md의 `MD 위반 사항` 보고 형식을
따라야 한다.

## 3. Source Of Truth

점검은 아래 truth 우선순위를 사용한다.

1. Broker holdings, open orders, fills
2. V2 lifecycle / canonical performance / learning performance
3. Candidate audit and ticker selection trace
4. Logs and dashboard/preflight visibility
5. Legacy `data/ml/decisions.db` as auxiliary signal/outcome record
6. `state/brain.json` as prompt policy memory only

성과 지표에서 broker fill evidence가 없으면 realized PnL은 provisional로
표시해야 한다. 전략 성과와 broker/order reconcile 문제를 한 표에서 섞지
않는다.

## 4. Required Audit Model

각 점검 항목은 아래 필드를 반드시 가진다.

| Field | Requirement |
| --- | --- |
| Item | 점검 항목명 |
| Code path | 값 생성, 변환, 소비 함수/모듈 |
| Expected flow | 정상 흐름 |
| Observed flow | 실제 로그/DB/테스트에서 확인된 흐름 |
| Break type | missing value, stale value, config gate, shadow-only, risk block, broker truth block, order lifecycle ambiguity, policy memory contamination |
| Intent | 의도된 guard, shadow 관측, 운영 정책, 과거 버그 수정, 신규 버그 의심 |
| Data contamination risk | 성과/학습/대시보드가 오염될 가능성 |
| Performance metrics | 항목별 성과 지표 |
| Acceptance | 정상/개선필요/보류 기준 |
| Evidence | 테스트, 로그, DB query, preflight, commit 근거 |

## 5. Code-Level Scope

점검 대상 코드는 다음 경로를 포함한다.

| Area | Code path | Audit purpose |
| --- | --- | --- |
| Selection | `trading_bot.py`, `ticker_selection_db.py`, `audit/` | 후보가 실제 prompt, watchlist, candidate_actions, trade_ready로 연결되는지 확인 |
| Intraday evidence | `compute_intraday_features()`, `_prefetch_selection_intraday_evidence()`, `runtime/live_evidence_pack.py` | `data_quality`, OR/VWAP/volume/ret 값이 routing까지 전달되는지 확인 |
| Routing | `runtime/action_routing.py::route_candidate_action()` | Claude action이 Path A, Path B, WATCH, HARD_BLOCK로 올바르게 갈라지는지 확인 |
| Path A | `trading_bot.py::_apply_selection_meta()`, strategy loop, affordability/order submit | 전략 신호가 실제 주문 후보까지 도달하는지 확인 |
| Path B entry | `runtime/pathb_runtime.py::scan_waiting_entries()`, `_submit_buy()` | PathB wait, zone hit, broker truth, sizing, safety, broker order 연결 확인 |
| Risk/safety | `trading_bot.py::_new_buy_block_state()`, `execution/safety_gate.py` | 신규 진입 차단 사유가 의도된 안전장치인지 확인 |
| Broker truth | `runtime/broker_truth_snapshot.py`, `_sync_runtime_with_broker()` | stale/local state와 broker truth 우선순위 확인 |
| PathB exit | `scan_exits()`, profit ladder, MFE, loss cap, hard stop, hold policy, pre-close | 청산 우선순위와 성과 귀속 확인 |
| Hold advisor | `minority_report/hold_advisor.py`, `_run_pathb_sell_review_gate()` | HOLD/SELL 정책이 안전하게 적용되고 반복 호출을 만들지 않는지 확인 |
| Lifecycle/performance | `lifecycle/`, `data/ml/`, `data/audit/`, dashboard/API | 주문/체결/성과/학습 데이터가 정확히 연결되는지 확인 |

## 6. Mandatory Flow Groups

아래 그룹은 모두 점검해야 한다. 누락되면 점검 완료로 볼 수 없다.

### G1. Evidence Handoff

목적: evidence pack에서 확인된 데이터가 routing context까지 같은 의미로
전달되는지 확인한다.

필수 항목:

- `volume_ratio_open`
- `opening_range_high/low`
- `vwap`, `vwap_proxy`, `vwap_reclaim`
- `ret_3m_pct`, `ret_5m_pct`, `ret_10m_pct`, `ret_30m_pct`
- `data_quality`
- `data_quality_missing`
- `evidence_data_state`
- `evidence_action_ceiling`
- `evidence_pack.data_quality`

필수 점검:

- `minute_complete`가 routing에서 정상 데이터로 인정되는지
- `evidence_data_state=confirmed`인데 `context.data_quality=missing`으로
  남는 케이스가 있는지
- `minute_partial`과 `minute_missing`이 의도대로 분리되는지
- OR cache가 `minute_partial`이라도 OR high/low가 있으면 형성되는지

최우선 known issue:

```text
evidence_data_state=confirmed
evidence_pack.data_quality=minute_complete
context.data_quality=missing
context.data_quality_missing=True
=> route_candidate_action() returns WATCH / pathb_waiting_kept_bad_data
```

이 케이스는 테스트로 고정한 뒤 backfill 방식 또는 `good_data` predicate
방식을 비교해야 한다.

### G2. Candidate Action And Routing

목적: Claude action이 의도한 live path로 들어가는지 확인한다.

필수 항목:

- `off_list_action`
- `active_order_lock:*`
- `pathb_active_order_blocks_plana`
- `evidence_ceiling_watch`
- `soft_gate_override_failed`
- `kr_risk_combo_confirmation_required`
- `kr_late_*`
- `pathb_waiting_kept_bad_data`
- `pathb_waiting_kept_inside_buy_zone`
- `pathb_waiting_kept_overextended`
- `missing_pullback_target`
- `add_without_position`
- `add_shadow_only`
- `claude_avoid`
- `action_expired`

필수 점검:

- `BUY_READY`, `PROBE_READY`, `ADD_READY`가 Path A executable set으로 가는지
- `PULLBACK_WAIT`가 Path A trade_ready가 아니라 PathB wait로만 가는지
- `WATCH`, `AVOID`, `EXPIRED`, `HARD_BLOCK`이 live order로 이어지지 않는지
- PathB wait cancellation이 confidence, price, data, overextended 조건으로
  정확히 분기되는지

### G3. Strategy Activation

목적: 전략이 코드에는 있지만 live에서 의도적으로 꺼져 있는지, 아니면
실수로 실행되지 않는지 구분한다.

필수 항목:

- `_live_strategy_allowed()`
- `_live_plan_a_signal_allowed()`
- `US_MOMENTUM_LIVE_ENABLED`
- `US_VOLATILITY_BREAKOUT_LIVE_ENABLED`
- `KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED`
- `KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED`
- `KR_PLAN_A_ORP_SIGNAL_ENABLED`
- `_apply_kr_pathb_mode_gate()`
- `_apply_kr_pathb_strategy_filter()`

필수 점검:

- 같은 전략 이름이라도 KR/US 성과를 분리한다.
- KR Plan A disabled 상태가 의도된 redesign인지 확인한다.
- US momentum live allowlist가 false로 바뀌면 수익 엔진이 막히는지 확인한다.
- KR PathB filters가 shadow-only인지 live-removal인지 config와 runtime payload로
  확인한다.

### G4. New-Buy And Safety Gates

목적: 후보 품질 문제와 execution/risk 문제를 분리한다.

필수 항목:

- `MARKET_CLOSED`
- `ENTRY_BLACKOUT`
- `STOP_CLUSTER_*`
- `ANALYST_NEW_BUY_BLOCK`
- `ORDER_UNKNOWN_UNRESOLVED`
- `BROKER_SYNC_QUARANTINE`
- `BROKER_UNTRUSTED`
- `INVALID_PRICE`
- `INVALID_QTY`
- `STALE_MARKET_DATA`
- `SAME_DAY_REENTRY_AFTER_STOP`
- `ALREADY_HOLDING`
- `PENDING_ORDER_EXISTS`
- `MAX_POSITIONS`
- `MAX_DAILY_ENTRIES`
- `DAILY_LOSS_LIMIT`
- `MIN_ORDER_NOT_MET`
- `INSUFFICIENT_CASH`

필수 점검:

- block reason별 후보 수, 주문 전환율, missed return을 집계한다.
- affordability/capacity block은 selection 품질 문제로 분류하지 않는다.
- KR fixed order 450,000 KRW와 gross cap/current capacity mismatch를 별도
  운영 정책 문제로 기록한다.

### G5. PathB Entry

목적: PathB wait가 실제 매수까지 정상적으로 연결되는지 확인한다.

필수 항목:

- PathB control enabled/disabled
- market live enabled/disabled
- shadow-only plan
- `CLAUDE_PRICE_INVALID`
- `HIGH_PRICE_BUDGET_BLOCK`
- `PATHB_CONFIDENCE_TOO_LOW`
- `BLOCKED_BROKER_TRUTH`
- `KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK`
- `KR_PATHB_RISK_ORIGIN_CONFIRMATION_REQUIRED`
- `PATHB_MAX_POSITIONS`
- `PATHB_MAX_DAILY_ENTRIES`
- `PATH_DUPLICATE_HOLDING`
- `PATHB_ORDER_UNKNOWN_HALTED`
- broker precheck/order result

필수 점검:

- WAITING -> HIT -> ORDER_SENT -> ORDER_ACKED -> FILLED 전이가 있는지
- broker truth fail-closed가 완화되지 않았는지
- buy-zone hit인데 order가 안 나간 경우 reason을 하나로 귀속한다.
- submit block이 waiting 보존인지 cancel인지 구분한다.

### G6. PathB Pending BUY And ORDER_UNKNOWN

목적: 주문 전송 후 ambiguity가 성과와 신규 진입을 오염시키지 않게 한다.

필수 항목:

- `reconcile_buy_pending_cancel_above()`
- BUY pending TTL
- exact `entry_execution_id` matching
- open order `remaining_qty`
- fill evidence
- `ORDER_UNKNOWN`
- previous-session stale active row

필수 점검:

- current-session ORDER_UNKNOWN과 historical ORDER_UNKNOWN을 분리한다.
- broker/local exposure가 없다는 이유만으로 자동 삭제하지 않는다.
- audited remediation 가능 여부와 apply 여부를 분리한다.

### G7. PathB Sizing And Capacity

목적: qty=0 또는 주문 차단 사유를 정확히 분리한다.

필수 항목:

- `invalid_price`
- `ORDER_SIZE_TOO_SMALL_GATE`
- `HIGH_PRICE_BUDGET_BLOCK`
- `early_gate_floor_one_share`
- `one_share_over_budget_max_krw`
- `MIN_ORDER_NOT_MET`
- `INSUFFICIENT_CASH`
- gross cap / today capacity

필수 점검:

- sizing reason split이 보호 계약대로 유지되는지
- early gate one-share floor가 과도하게 막거나 과도하게 살리지 않는지
- fixed order와 account/gross cap mismatch가 실제 진입 0건을 만들고 있는지

### G8. PathB Exit And Hold Advisor

목적: 보유 포지션의 청산/보호 정책이 수익을 해치거나 data ambiguity를 만들지
않는지 확인한다.

필수 항목:

- policy stop
- MFE breakeven
- loss cap
- hard stop
- profit ladder
- PathB auto sell policy
- Claude sell manager
- profit protection review
- pre-close carry review
- pre-close force exit
- AUTO_SELL_REVIEW cooldown
- protective hold
- target extension
- stop recovery

필수 점검:

- close reason별 PnL을 본다.
- HOLD가 protective policy 없이 반복되는지 확인한다.
- profit ladder와 pre-close 경로는 보호 수익 엔진으로 보고 완화하지 않는다.
- hold advisor fallback/unavailable이 learning/performance에 섞이지 않는지
  확인한다.

### G9. PathB SELL Submit And Sellability

목적: SELL이 안전하게 나가고, 중복 sell 또는 stuck state가 없는지 확인한다.

필수 항목:

- sell attempt lock
- sell in-flight
- sellability untrusted
- sellable qty reject
- broker open sell order recovery
- broker sell fill recovery
- manual reconciliation required
- partial sell remaining qty
- sell pending session-end behavior

필수 점검:

- SELL 미제출이 HOLD 정책 때문인지 sellability 때문인지 분리한다.
- sellable qty reject가 open order/fill/zero holding evidence로 복구되는지 확인한다.
- manual reconciliation 상태는 live 자동 청산으로 우회하지 않는다.

### G10. Performance And Learning Linkage

목적: 흐름 점검이 성과 지표까지 연결되는지 확인한다.

필수 항목:

- `path_run_id`
- `v2_decision_id`
- `v2_execution_id`
- `candidate_trace_id`
- strategy/source_strategy/path_type
- broker fill price/qty/order_no
- canonical performance freshness
- `v2_learning_performance`
- candidate audit execution link
- legacy decisions fallback usage

필수 점검:

- 체결 성과와 후보 missed opportunity를 분리한다.
- live fill 성과는 V2 canonical/performance를 우선한다.
- broker fill evidence 없는 realized PnL은 확정 성과로 쓰지 않는다.
- `state/brain.json` dirty 또는 lesson candidate가 runtime truth처럼 쓰이지
  않는지 확인한다.

## 7. Required Metrics

각 항목별 성과 지표는 최소 아래를 포함해야 한다.

### Flow Metrics

| Metric | Definition |
| --- | --- |
| candidate_count | 후보 수 |
| prompt_included_count | 실제 Claude prompt 포함 수 |
| action_count_by_type | BUY_READY/PROBE_READY/PULLBACK_WAIT/WATCH/AVOID/EXPIRED |
| route_count_by_final_action | final_action별 수 |
| block_count_by_reason | block/demotion reason별 수 |
| evidence_state_distribution | confirmed/partial/missing |
| data_quality_distribution | minute_complete/minute_partial/minute_missing/missing |
| pathb_wait_registered | PathB wait 등록 수 |
| pathb_zone_hit | buy-zone hit 수 |
| order_sent_count | broker order sent 수 |
| fill_count | broker fill 수 |
| order_unknown_count | ORDER_UNKNOWN 수 |
| sell_signal_count | exit signal 수 |
| sell_sent_count | SELL order sent 수 |

### Performance Metrics

| Metric | Applies to | Definition |
| --- | --- | --- |
| realized_pnl_krw | filled closed trades | broker truth based realized PnL |
| realized_pnl_pct | filled closed trades | normalized realized return |
| mfe_pct | entered or missed PathB/candidate | max favorable excursion |
| mae_pct | entered or missed PathB/candidate | max adverse excursion |
| forward_30m_pct | candidates and blocked routes | 30 minute forward move |
| forward_60m_pct | candidates and blocked routes | 60 minute forward move |
| hit_rate | strategy/route bucket | profitable closed trade ratio |
| fill_rate | executable routes | fills / order attempts |
| submit_rate | executable routes | orders / executable routes |
| block_missed_runup_pct | blocked routes | blocked rows whose forward move exceeded threshold |
| slippage_bps | filled orders | broker fill vs intended limit/reference |
| close_reason_pnl | PathB exits | PnL grouped by close reason |
| hold_advisor_outcome | AUTO_SELL_REVIEW | HOLD/SELL/fallback/cooldown result and subsequent PnL |

### Data Quality Metrics

| Metric | Definition |
| --- | --- |
| missing_required_field_rate | required execution fields missing / routed candidates |
| confirmed_but_routing_missing_count | `evidence_data_state=confirmed` but routing `data_quality_missing=true` |
| stale_cache_reuse_count | stale cached feature blocked/replaced count |
| broker_truth_unavailable_count | broker snapshot missing/stale/error count |
| lifecycle_link_missing_count | missing path_run/execution/audit links |
| provisional_pnl_count | PnL rows without broker fill evidence |

### Performance Interpretation Rules

성과 해석은 흐름 정합성 점검과 분리한다.

- 코드에 전략/게이트가 존재한다는 이유만으로 `OK`로 판정하지 않는다. 각
  항목은 로그, DB, 테스트, preflight, commit 근거 중 하나 이상의 관측
  evidence를 가져야 한다.
- KR/US, Path A/Path B, live/shadow, `strategy`/`source_strategy`,
  close reason은 같은 이름이어도 분리 집계한다.
- filled trade는 broker fill evidence가 있을 때만 realized performance로
  집계한다.
- blocked/watch/shadow 후보는 realized PnL이 아니라 forward return, MFE,
  MAE, missed runup 지표로만 평가한다.
- shadow-only 항목은 의도된 shadow인지, 설정 누락인지, 오래된 보수화인지
  commit/config/log 근거로 구분한다.
- 짧은 기간 성과만으로 전략 철학을 교체하지 않는다. 정책 승격, live gate
  완화, live gate 제거는 별도 승인과 충분한 관측 표본이 필요하다.

## 8. Required Output Reports

점검 결과는 아래 산출물을 만들어야 한다.

### Report A. Flow Integrity Matrix

| Item | Expected flow | Observed flow | Break type | Intent | Status |
| --- | --- | --- | --- | --- | --- |

Status values:

- `OK`
- `INTENTIONAL_GUARD`
- `SHADOW_ONLY`
- `OPERATING_POLICY_MISMATCH`
- `DATA_HANDOFF_BUG`
- `BROKER_TRUTH_BLOCK`
- `ORDER_LIFECYCLE_AMBIGUOUS`
- `PERFORMANCE_TRUTH_AMBIGUOUS`
- `NEEDS_TEST`

### Report B. Reason And Performance Matrix

| Market | Path | Strategy | Route reason | Count | Submit rate | Fill rate | Realized PnL | Forward 30m | Forward 60m | Finding |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Report C. Root Cause Patterns

Root cause는 다음 형식으로 써야 한다.

```text
expected: <value source> -> <consumer>
actual: <where it stopped>
root cause: <missing field / stale value / config gate / broker truth / order lifecycle>
runtime result: <WATCH / HARD_BLOCK / ORDER_UNKNOWN / no order / no sell>
performance impact: <missed runup / protected loss / no measurable impact / unknown>
```

예:

```text
expected: live_pack.data_quality=minute_complete -> route_candidate_action.good_data
actual: context.data_quality=missing, data_quality_missing=True
root cause: live_pack confirmed quality not backfilled into routing context
runtime result: pathb_waiting_kept_bad_data
performance impact: requires blocked-route forward return check
```

### Report D. Item Evidence And Test Checklist

각 항목별 실증 테스트는 아래 표로 남긴다.

| Item | Code path | Field handoff test | DB/log query | Performance metric | Result | Next action |
| --- | --- | --- | --- | --- | --- | --- |

작성 규칙:

- `Field handoff test`는 값 생성 지점과 소비 지점의 이름/값이 같은 의미인지
  검증해야 한다.
- `DB/log query`는 count 또는 대표 row를 재현할 수 있어야 한다.
- `Performance metric`은 체결 성과, blocked forward return, shadow would-have
  return 중 하나 이상이어야 한다.
- `Result`는 `OK`, `INTENTIONAL_GUARD`, `SHADOW_ONLY`,
  `OPERATING_POLICY_MISMATCH`, `DATA_HANDOFF_BUG`,
  `ORDER_LIFECYCLE_AMBIGUOUS`, `NEEDS_TEST` 중 하나로 쓴다.

## 9. Priority Order

### P0

1. Evidence confirmed but routing data_quality missing.
2. KR fixed order amount versus gross cap/capacity.
3. Historical/current ORDER_UNKNOWN separation and remediation evidence.
4. Broker fill truth and provisional PnL prevention.

### P1

1. KR Plan A disabled gates and missed opportunity metrics.
2. KR PathB shadow filters and would-block analysis.
3. KR late-entry/risk-combo/price-cap false-positive block analysis.
4. PathB sizing reason distribution and missed fill/runup impact.
5. PathB sellability and stuck sell-state analysis.

### P2

1. Hold advisor HOLD/SELL outcome quality.
2. Profit ladder/pre-close/target extension performance by close reason.
3. Candidate source/bucket/trainer score performance.
4. Brain/sub-screener prompt-policy contamination checks.

## 10. Acceptance Criteria

점검 완료 조건:

1. `ARCHITECTURE_MAP.md` Flow-Break matrix의 모든 항목이 Report A에 포함된다.
2. 코드에 존재하지만 MD에 없는 live gate/reason이 0개로 정리된다.
3. 각 항목은 최소 하나의 evidence source를 가진다.
4. 각 route/block reason은 count와 성과 지표 중 하나 이상을 가진다.
5. broker fill evidence 없는 realized performance는 확정 성과로 집계하지 않는다.
6. selection quality, routing gate, risk/affordability, broker/order lifecycle,
   hold advisor policy가 분리되어 보고된다.
7. 개선 후보는 테스트 요구사항과 acceptance 기준을 가진다.
8. live/shadow, KR/US, filled/blocked/watch 성과가 한 버킷에 섞이지 않는다.
9. `SHADOW_ONLY` 또는 `INTENTIONAL_GUARD` 판정은 commit, config, 문서, 로그
   중 하나 이상의 의도 근거를 가진다.

## 11. Verification Commands

점검 요구서 자체를 검증할 때:

```powershell
git diff --check -- docs/important/STRATEGY_FLOW_AUDIT_REQUIREMENTS_20260602.md docs/important/ACTIVE_WORK.md docs/important/core/TODO_ROADMAP.md docs/important/core/ARCHITECTURE_MAP.md docs/important/README.md docs/important/core/DOCUMENTATION_INDEX.md docs/important/core/DOCUMENTATION_INVENTORY.md
```

분석 구현 또는 코드 수정이 들어간 경우 최소 검증:

```powershell
python -m pytest tests/test_action_routing.py -q
python -m pytest tests/test_live_evidence_pack.py tests/test_trading_bot_intraday_evidence.py -q
python -m pytest tests/test_pathb_runtime.py tests/test_pathb_sell_reconcile.py -q
python tools/live_preflight.py --mode live --skip-dashboard --json
```

실제 성과 리포트 생성은 read-only로 수행해야 하며, 운영 DB를 수정하는
backfill/remediation/apply 명령은 이 요구서 범위에 포함하지 않는다.
