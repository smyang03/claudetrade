# Execution Advisor 개발 요구서

작성일: 2026-06-02  
목적: 수동 정정/체결가 괴리로 기존 PathB 가격 계획이 깨지는 상황을 별도 감지하고, 정상 자동 주문은 balanced 로컬 룰로 처리한다.

## 1. 결론

초기 운영 정책은 다음으로 고정한다.

```text
수동 정정/수동 체결/broker-local 주문 불일치: Claude replan 후보
정상 자동 주문/체결/매도 limit: balanced local guard
실제 주문 정정/취소/추가 제출: 초기 구현 금지
```

수동 정정 예시 케이스는 `REPLAN_REQUIRED`가 맞고, Phase 2가 켜진 경우 Claude 판단 대상이다. local PathB 계획 진입가와 broker 실제 평균 매수가가 다르면 Claude replan 입력의 기준 진입가는 반드시 broker actual entry여야 하며, local planned entry는 불일치 탐지용 원래 계획가로만 남긴다. 아래 문서의 `MANUAL_MISMATCH` fixture는 실제 종목명이 아니라 이 수동 정정 패턴을 고정하기 위한 합성 예시다. 반면 HPE, DELL, BBY 같은 정상 자동 흐름은 Claude 없이 로컬 룰로 충분히 분기된다.

개발 범위와 운영 활성 범위는 분리한다.

```text
이번 개발: Phase 0 + Phase 1 + Phase 2 골격까지 한 번에 구현 가능
운영 기본값: disabled/read-only/Claude disabled
실제 주문 실행: Phase 3, 별도 승인 전까지 hard-disabled
```

주의: `EXEC_ADVISOR_CLAUDE_ENABLED=false`는 운영 기본 안전값일 뿐이다. Phase 2에서 이 값을 켜면 `MANUAL_MISMATCH` 같은 수동/불일치 `REPLAN_REQUIRED`는 Claude replan을 1회 호출하는 것이 기대 동작이다.

여기서 shadow는 전략 shadow가 아니라 read-only audit 모드다. live 데이터로 판단은 하되 주문 API, cancel API, sell API를 호출하지 않는 안전 실행 모드다.

## 2. 보호 범위

이번 요구서는 Execution Advisor를 기존 Hold Advisor/PathB exit 보호 경로와 분리한다.

건드리지 않을 보호 영역:

- `runtime/pathb_runtime.py::_pathb_auto_sell_review_cooldown_payload()`
- `runtime/pathb_runtime.py::_run_pathb_sell_review_gate()`
- `runtime/pathb_runtime.py::_entry_scan_broker_truth_gate()`
- `runtime/pathb_runtime.py::_pathb_qty_with_context()`
- `execution/safety_gate.py` sizing reason split
- KIS order normalization의 `remaining_qty` 계약
- zero-holding stale reconcile
- `_sync_runtime_with_broker()` broker truth 우선순위
- `state/brain.json`

초기 구현은 위 보호 영역의 동작을 완화하지 않는다. Runtime hook이 필요해도 주문/청산 판단을 override하지 않고 advisor audit만 남긴다.

## 3. 구현 단계

이번 개발 목표는 Phase 0~2의 "판단/감사/Claude 후보 관리"까지다. Phase 3 실제 주문 실행은 구현하지 않는다.

| Phase | 이번 개발 | 운영 기본값 | 설명 |
|---|---:|---:|---|
| Phase 0 | 포함 | CLI only | 판단 엔진과 시뮬레이터 |
| Phase 1 | 포함 가능 | disabled/read-only | live loop audit hook, 주문 API 호출 0회 |
| Phase 2 | 골격 포함 가능 | Claude disabled | manual/mismatch replan 후보와 cooldown/cap |
| Phase 3 | 제외 | hard-disabled | 실제 주문 정정/취소/매도 |

### Phase 0 - Pure simulation

목표:

- 현재 broker truth snapshot, local positions, `v2_path_runs`, lifecycle events를 읽어 advisor 판단만 산출한다.
- Claude API는 호출하지 않는다.
- 주문 제출/취소/정정은 하지 않는다.

추가 파일:

- `execution/execution_advisor.py`
- `tests/test_execution_advisor.py`
- `tools/simulate_execution_advisor.py`

수정 파일:

- 없음 또는 `.env.example`에 문서용 env 키 추가만 허용

수용 기준:

```text
MANUAL_MISMATCH -> REPLAN_REQUIRED, claude_candidate=true
HPE  -> KEEP_PLAN, claude_candidate=false
DELL -> KEEP_PLAN, claude_candidate=false
BBY  -> KEEP_LIMIT, claude_candidate=false
HPQ  -> NO_EXECUTION_ADVISOR_ACTION, claude_candidate=false
EL   -> BROKER_RECONCILE_REQUIRED, claude_candidate=false
```

이번 개발에서 반드시 포함한다.

### Phase 1 - Runtime read-only audit

목표:

- live loop 안에서 1분 단위로 로컬 룰 판단만 수행한다.
- 결과를 lifecycle/audit에 남기되 주문 상태를 바꾸지 않는다.
- Claude API는 기본 비활성이다.
- `EXEC_ADVISOR_SHADOW_ONLY=true` 상태에서는 주문 API/cancel API/sell API 호출이 0회여야 한다.

추가 파일:

- `runtime/execution_advisor_runtime.py`
- `tests/test_execution_advisor_runtime.py`

수정 파일:

- `lifecycle/models.py`: `EXECUTION_ADVISOR_DECISION` event type 추가
- `lifecycle/event_store.py`: advisor audit event가 `v2_decisions.status`를 오염하지 않도록 status update 예외 추가
- `trading_bot.py`: PathB scan 이후 shadow advisor 호출
- `.env.example`: env 키 문서화

권장 hook:

```text
TradingBot.run_entry_scan(market)
  -> session_active/current_market 확인
  -> self.execution_advisor.scan_market(market)  # read-only, 자체 60초 throttle
  -> 기존 entry scan interval gate
  -> 기존 run_cycle(market)
```

이유:

- 현재 scheduler는 `run_entry_scan`을 1분마다 호출한다.
- `run_cycle()`은 `_entry_scan_interval_sec()` gate 뒤에서 실행되므로 US 정규장에는 2~5분 주기가 될 수 있다.
- `run_housekeeping()`은 현재 `schedule.every(5).minutes`로 등록되어 있어 1분 주기 요구와 맞지 않는다.
- advisor runtime은 자체 `EXEC_ADVISOR_CHECK_INTERVAL_SEC=60` throttle을 가진다.

`runtime/pathb_runtime.py` 직접 수정은 피한다. advisor는 `pathb.store`, `pathb.broker_truth`, `BrokerTruthSnapshot.market_snapshot()`만 읽는다. broker truth는 `EXEC_ADVISOR_BROKER_TRUTH_TTL_SEC=120` 기준으로 재사용하고, stale이면 advisor runtime에서 refresh를 요청하되 주문 API는 호출하지 않는다.

이번 개발에서 포함 가능하다. 단, env 기본값은 `EXEC_ADVISOR_ENABLED=false`이고 live 활성은 운영자가 별도로 켠다.

### Phase 2 - Manual-only Claude replan

목표:

- 수동 정정/주문번호 불일치/체결가 괴리에서만 Claude replan 후보를 만든다.
- Claude 응답은 "새 계획 제안"으로만 저장한다.
- 제안 결과가 있어도 자동 주문 정정/매도/추가매수는 하지 않는다.
- Claude client 호출부는 hard gate 뒤에 둔다. `EXEC_ADVISOR_CLAUDE_ENABLED=false`이면 호출 경로가 실행되지 않아야 한다.

수정 파일 후보:

- `execution/execution_advisor.py`
- `runtime/execution_advisor_runtime.py`
- `tests/test_execution_advisor_claude_policy.py`

Claude 호출 조건:

```text
EXEC_ADVISOR_CLAUDE_ENABLED=true
EXEC_ADVISOR_MANUAL_ONLY_CLAUDE=true
manual_or_mismatch=true
same ticker/order cooldown 통과
daily call cap 통과
broker truth fresh
```

이번 개발에서 골격까지 포함 가능하다. 기본값은 disabled이며, 테스트에서는 fake Claude client만 사용한다. 실제 Claude API smoke는 운영자가 요청한 경우 1회 이하로 제한한다.

### Phase 3 - Operator-approved execution

목표:

- 별도 승인 전까지 구현하지 않는다.
- 구현하더라도 advisor가 직접 hard stop, loss cap, profit ladder, pre-close 경로를 override하지 않는다.
- 이번 개발 범위에서 제외한다.

Phase 3에서 가능한 동작은 별도 승인 후 검토한다.

- missed buy cancel
- guarded reprice
- sell limit lower
- broker-entry 기반 plan metadata correction

## 4. 코드 레벨 설계

### 4.1 `execution/execution_advisor.py`

Pure logic module로 만든다. 파일 I/O, KIS 호출, Claude 호출을 넣지 않는다.

주요 타입:

```python
from dataclasses import dataclass
from enum import Enum

class ExecutionAdvisorAction(str, Enum):
    KEEP_PLAN = "KEEP_PLAN"
    KEEP_PLAN_WITH_BROKER_ENTRY_AUDIT = "KEEP_PLAN_WITH_BROKER_ENTRY_AUDIT"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    WAIT_LIMIT = "WAIT_LIMIT"
    REPRICE_WITH_GUARD_CANDIDATE = "REPRICE_WITH_GUARD_CANDIDATE"
    CANCEL_MISSED = "CANCEL_MISSED"
    KEEP_LIMIT = "KEEP_LIMIT"
    LOWER_LIMIT_WITH_GUARD_CANDIDATE = "LOWER_LIMIT_WITH_GUARD_CANDIDATE"
    HANDOFF_HOLD_ADVISOR = "HANDOFF_HOLD_ADVISOR"
    BROKER_RECONCILE_REQUIRED = "BROKER_RECONCILE_REQUIRED"
    NO_EXECUTION_ADVISOR_ACTION = "NO_EXECUTION_ADVISOR_ACTION"
    WAIT_BROKER_TRUTH = "WAIT_BROKER_TRUTH"

@dataclass(frozen=True)
class ExecutionAdvisorConfig:
    operator_drift_warn_pct: float = 0.75
    max_chase_above_zone_pct: float = 0.50
    min_upside_pct: float = 1.50
    min_entry_reward_risk: float = 0.80
    sell_keep_limit_gap_pct: float = 2.00
    sell_profit_keep_min_pct: float = 1.00
    manual_only_claude: bool = True

@dataclass(frozen=True)
class ExecutionAdvisorDecision:
    market: str
    ticker: str
    action: ExecutionAdvisorAction
    claude_candidate: bool
    manual_or_mismatch: bool
    reason_code: str
    metrics: dict[str, float | str | int | bool | None]
    path_run_id: str = ""
    order_no: str = ""
```

필수 함수:

```python
evaluate_filled_pathb_position(...)
evaluate_pending_buy_order(...)
evaluate_open_sell_order(...)
evaluate_existing_position(...)
should_call_claude(decision, cooldown_state, daily_count, config)
```

금지:

- `kis_api` import 금지
- `Anthropic`/Claude client import 금지
- `EventStore.update_path_run()` 호출 금지
- `submit_order`, `cancel_order`, `sell` 계열 호출 금지

### 4.2 Manual/mismatch 판정

manual_or_mismatch는 다음 중 하나라도 true면 true다.

```text
broker fill order_no exists and plan.entry_execution_id exists and they differ
abs(broker.avg_price / local actual_entry_price - 1) * 100 >= 0.75
broker.avg_price > buy_zone_high * 1.005
broker position exists but local path_run actual_entry_price is missing or invalid
same ticker same session broker fill exists but local lifecycle execution_id differs
```

`today_fills`가 없는 snapshot에서도 avg_price drift만으로 감지할 수 있어야 한다. KIS 체결내역이 일부 계좌/시간대에서 비어도 broker position 평균가는 신뢰 가능한 1차 truth다.

### 4.3 Filled PathB 흐름

입력:

- `v2_path_runs.status in ("FILLED", "PARTIAL_FILLED")`
- `plan.actual_entry_price`
- `plan.entry_execution_id`
- broker `positions`
- broker `today_fills`
- local `live_open_positions`

분기:

```text
broker position 없음
  -> BROKER_RECONCILE_REQUIRED
  -> 기존 reconcile 영역으로 넘김
  -> Claude 호출 금지

manual_or_mismatch=false and plan economics 정상
  -> KEEP_PLAN
  -> Claude 호출 금지

manual_or_mismatch=true and plan economics 정상
  -> KEEP_PLAN_WITH_BROKER_ENTRY_AUDIT
  -> Claude 호출 금지

manual_or_mismatch=true and plan economics 깨짐
  -> REPLAN_REQUIRED
  -> Claude 후보

current <= stop
  -> 기존 PathB protected exit 우선
  -> advisor override 금지

current >= target and manual_or_mismatch=false
  -> 기존 PathB target/profit exit 우선
  -> advisor override 금지
```

plan economics 계산:

```text
entry_drift_pct = broker_avg / local_entry - 1
above_zone_high_pct = broker_avg / buy_zone_high - 1
remaining_upside_pct = target / current - 1
risk_to_stop_from_current_pct = current / stop - 1
entry_reward_risk = (target - broker_avg) / (broker_avg - stop)
current_reward_risk = (target - current) / (current - stop)
```

깨짐 기준:

```text
plan_economics_broken = any(
  remaining_upside_pct < min_upside_pct,
  entry_reward_risk < min_entry_reward_risk,
  current >= target,
  broker_avg > buy_zone_high * (1 + max_chase_above_zone_pct / 100),
)

REPLAN_REQUIRED = manual_or_mismatch and plan_economics_broken
```

즉 깨짐 기준 4개는 OR 관계다. 다만 `REPLAN_REQUIRED`는 단순 OR만으로 발생하지 않고 `manual_or_mismatch=true`가 함께 필요하다.

예외적으로 `broker_avg > buy_zone_high * (1 + max_chase_above_zone_pct / 100)`는 actual entry가 허용 buy zone을 벗어난 체결이므로 단독으로도 `manual_or_mismatch=true`와 `plan_economics_broken=true`를 동시에 만족한다. 이 경우 upside/RR이 아직 좋아 보여도 stale local plan을 그대로 믿지 않고 `REPLAN_REQUIRED`로 보낸다.

### 4.4 Pending buy 흐름

입력:

- `v2_path_runs.status in ("ORDER_SENT", "ORDER_ACKED")`
- broker `open_orders` side=buy
- current quote
- PathB plan buy zone/target/stop

초기 정책:

```text
broker truth stale/missing/error
  -> WAIT_BROKER_TRUTH
  -> no Claude
  -> no order action

matching broker open buy order exists and current <= limit
  -> WAIT_LIMIT
  -> no Claude

current > limit and within buy_zone_high +0.50%, upside/RR 통과
  -> REPRICE_WITH_GUARD_CANDIDATE
  -> shadow only
  -> no Claude unless manual_or_mismatch=true

current too far or economics broken
  -> CANCEL_MISSED
  -> shadow only
  -> no Claude

local pending but no broker open order
  -> BROKER_RECONCILE_REQUIRED
  -> existing reconcile first
```

초기에는 `REPRICE_WITH_GUARD_CANDIDATE`도 주문 정정하지 않는다.

### 4.5 Open sell order 흐름

입력:

- broker `open_orders` side=sell
- broker position avg/current
- local hold advice/path metadata

초기 정책:

```text
position 없음 + sell open order 있음
  -> BROKER_RECONCILE_REQUIRED

distance_to_limit_pct <= 2.00 and pnl_pct >= 1.00
  -> KEEP_LIMIT
  -> no Claude

profitable but limit too far
  -> LOWER_LIMIT_WITH_GUARD_CANDIDATE
  -> shadow only
  -> Claude 후보는 manual/mismatch일 때만

loss/risk/protected stop 영역
  -> HANDOFF_HOLD_ADVISOR or existing PathB exit
  -> advisor direct sell 금지
```

### 4.6 Claude policy

Claude는 다음에서만 후보가 된다.

```text
decision.action == REPLAN_REQUIRED
decision.manual_or_mismatch == true
EXEC_ADVISOR_CLAUDE_ENABLED == true
EXEC_ADVISOR_MANUAL_ONLY_CLAUDE == true
cooldown 통과
daily cap 통과
broker truth fresh
```

명시적 no-call:

```text
KEEP_PLAN
WAIT_LIMIT
KEEP_LIMIT
CANCEL_MISSED
BROKER_RECONCILE_REQUIRED
NO_EXECUTION_ADVISOR_ACTION
WAIT_BROKER_TRUTH
```

Claude 응답 schema:

```json
{
  "action": "KEEP_WITH_NEW_BOUNDS | REDUCE_RISK | SELL_REVIEW | INVALIDATE_PLAN",
  "confidence": 0.0,
  "new_sell_target": 0.0,
  "new_protective_stop": 0.0,
  "max_hold_minutes": 0,
  "reason": "",
  "risk_if_wrong": ""
}
```

Claude 응답도 직접 주문으로 연결하지 않는다. Phase 2에서는 `execution_advisor_replan` payload로 저장만 한다.

## 5. Runtime 설계

### 5.1 `runtime/execution_advisor_runtime.py`

역할:

- broker truth snapshot 읽기
- local positions 읽기
- EventStore에서 active path runs 읽기
- pure advisor 호출
- lifecycle event append
- Claude call budget/cooldown 관리

상태 파일:

```text
state/live_execution_advisor_state.json
state/paper_execution_advisor_state.json
```

상태 파일 schema:

```json
{
  "date": "2026-06-02",
  "claude_calls": 0,
  "cooldowns": {
    "US:MANUAL_MISMATCH:path_...": "2026-06-02T00:55:00+09:00"
  },
  "last_scan_at": {
    "US": "2026-06-02T00:40:00+09:00"
  }
}
```

`state/brain.json`은 사용하지 않는다.

### 5.2 Event logging

`lifecycle/models.py`에 event type을 추가한다.

```python
EXECUTION_ADVISOR_DECISION = "EXECUTION_ADVISOR_DECISION"
```

주의: 현재 `EventStore.append()` / `append_many()`는 `QUALITY_MARKED`를 제외한 lifecycle event를 append할 때 `v2_decisions.status`를 event type으로 갱신한다. `EXECUTION_ADVISOR_DECISION`은 audit event이므로 기존 Claude decision/order lifecycle 상태를 바꾸면 안 된다.

필수 구현:

```python
NON_STATUS_EVENT_TYPES = {
    "QUALITY_MARKED",
    "EXECUTION_ADVISOR_DECISION",
}
```

`lifecycle/event_store.py`에서 위 event type은 `v2_decisions.status` update를 skip한다. 테스트는 기존 decision status가 `FILLED` 또는 `CLAUDE_TRADE_READY`일 때 advisor event append 후에도 그대로 유지되는지 확인한다.

payload 예:

```json
{
  "advisor_version": "execution_advisor_v1",
  "action": "REPLAN_REQUIRED",
  "claude_candidate": true,
  "claude_call_expected_if_enabled": true,
  "manual_or_mismatch": true,
  "reason_code": "operator_entry_drift_plan_economics_broken",
  "metrics": {
    "planned_entry": 100.00,
    "actual_entry": 102.87,
    "current": 104.09,
    "buy_zone_high": 101.42,
    "sell_target": 103.91,
    "stop_loss": 93.97,
    "entry_drift_pct": 2.870,
    "above_zone_high_pct": 1.430,
    "target_upside_from_actual_entry_pct": 1.011,
    "entry_stop_loss_pct": -8.652,
    "entry_risk_to_stop_pct": 8.652,
    "remaining_upside_pct": -0.173,
    "entry_reward_risk": 0.117,
    "current_reward_risk": -0.018
  },
  "claude_replan_input": {
    "entry_price_basis": "broker_actual_entry",
    "entry_price": 102.87,
    "planned_entry_reference": 100.00,
    "current_price": 104.09,
    "original_sell_target": 103.91,
    "original_stop_loss": 93.97,
    "instruction": "Replan from actual broker entry, not from the stale local planned entry."
  },
  "order_contract": {
    "local_entry_execution_id": "old_local_order",
    "broker_fill_order_no": "broker_manual_order"
  },
  "shadow_only": true
}
```

## 6. Env/config

초기 기본값:

```text
EXEC_ADVISOR_PROFILE=balanced
EXEC_ADVISOR_ENABLED=false
EXEC_ADVISOR_SHADOW_ONLY=true
EXEC_ADVISOR_CHECK_INTERVAL_SEC=60
EXEC_ADVISOR_BROKER_TRUTH_TTL_SEC=120

EXEC_ADVISOR_CLAUDE_ENABLED=false
EXEC_ADVISOR_MANUAL_ONLY_CLAUDE=true
EXEC_ADVISOR_CLAUDE_COOLDOWN_MINUTES=15
EXEC_ADVISOR_MAX_CLAUDE_CALLS_PER_DAY=5

EXEC_ADVISOR_OPERATOR_DRIFT_WARN_PCT=0.75
EXEC_ADVISOR_MAX_CHASE_ABOVE_ZONE_PCT=0.50
EXEC_ADVISOR_MIN_UPSIDE_PCT=1.50
EXEC_ADVISOR_MIN_ENTRY_REWARD_RISK=0.80
EXEC_ADVISOR_SELL_KEEP_LIMIT_GAP_PCT=2.00
EXEC_ADVISOR_SELL_PROFIT_KEEP_MIN_PCT=1.00
```

Profile별 threshold:

| Profile | operator drift warn | max chase above zone | min upside | min entry RR | sell keep limit gap | sell profit min |
|---|---:|---:|---:|---:|---:|---:|
| conservative | 0.50% | 0.30% | 2.00% | 0.90 | 1.50% | 1.00% |
| balanced | 0.75% | 0.50% | 1.50% | 0.80 | 2.00% | 1.00% |
| aggressive | 1.00% | 0.80% | 1.00% | 0.70 | 2.50% | 0.50% |

운영 기본값은 `balanced`다. conservative/aggressive는 sensitivity 분석과 테스트용 profile이며 live 기본값으로 쓰지 않는다.

오늘 sensitivity의 `conservative: Claude 후보 3건`은 다음 기준으로 산출됐다.

```text
MANUAL_MISMATCH -> REPLAN_REQUIRED
DELL -> HOLD_REVIEW_REQUIRED, entry RR 0.857 < conservative min entry RR 0.90
BBY  -> LOWER_LIMIT_WITH_GUARD_CANDIDATE, limit gap 1.698% > conservative sell keep limit gap 1.50%
```

따라서 conservative 3건은 production default가 아니라 threshold 민감도 확인 결과다.

`config/v2_start_config.json`에는 초기에는 넣지 않는다. live 활성은 운영자가 명시적으로 켠다.

## 7. 테스트 계획

### Unit

명령:

```text
python -m pytest tests/test_execution_advisor.py -q
```

필수 테스트:

- MANUAL_MISMATCH fixture: planned entry 100.00, broker actual entry 102.87, current 104.09, target 103.91, stop 93.97 -> actual-entry upside +1.011%, stop loss -8.652%, RR 0.117, `REPLAN_REQUIRED`, `claude_candidate=true`, `claude_call_expected_if_enabled=true`
- MANUAL_MISMATCH fixture는 `current=104.09`를 반드시 포함한다. 이 값이 있어야 `current >= target`과 `remaining_upside_pct=-0.173%` 분기가 재현된다.
- HPE fixture: local/broker 44.67 -> `KEEP_PLAN`, no Claude
- DELL fixture: local/broker 460.62, RR 0.857 -> balanced `KEEP_PLAN`, conservative would review
- DELL conservative fixture: `EXEC_ADVISOR_PROFILE=conservative`에서는 `entry_reward_risk=0.857 < 0.90` 때문에 `HOLD_REVIEW_REQUIRED`, Claude 후보
- BBY sell limit fixture: avg 73.44, current 75.99, limit 77.28 -> `KEEP_LIMIT`, no Claude
- BBY conservative fixture: `EXEC_ADVISOR_PROFILE=conservative`에서는 `distance_to_limit_pct=1.698% > 1.50%` 때문에 `LOWER_LIMIT_WITH_GUARD_CANDIDATE`, Claude 후보
- broker stale fixture -> `WAIT_BROKER_TRUTH`, no Claude
- local FILLED but broker zero holding -> `BROKER_RECONCILE_REQUIRED`, no Claude
- manual-only false/true policy 분리
- daily cap/cooldown 통과/차단

### Runtime shadow

명령:

```text
python -m pytest tests/test_execution_advisor_runtime.py -q
python -m py_compile execution/execution_advisor.py runtime/execution_advisor_runtime.py trading_bot.py lifecycle/models.py
```

필수 테스트:

- `scan_market("US")`가 broker snapshot을 TTL 120초로 재사용한다.
- `run_entry_scan()` 상단 hook에서는 `_entry_scan_interval_sec()` gate에 막히지 않고 advisor 자체 60초 throttle로 실행된다.
- stale/error snapshot이면 Claude 후보를 만들지 않는다.
- event append payload에 `shadow_only=true`가 들어간다.
- `EXECUTION_ADVISOR_DECISION` append 후에도 `v2_decisions.status`가 오염되지 않는다.
- `EXEC_ADVISOR_ENABLED=false`이면 아무 것도 하지 않는다.
- `EXEC_ADVISOR_SHADOW_ONLY=true`이면 주문 API/cancel API 호출이 0회다.

### 보호 회귀

Runtime hook을 추가한 경우 다음을 최소 실행한다.

```text
python -m pytest tests/test_auto_sell_claude_gate.py::AutoSellClaudeGateTests::test_pathb_loss_cap_hold_respects_reask_cooldown -q
python -m pytest tests/test_pathb_runtime.py tests/test_pathb_sell_reconcile.py tests/test_broker_truth_snapshot.py -q
```

최종 QA:

```text
python -m pytest -q
python tools/live_preflight.py --mode live --skip-dashboard --json
```

## 8. 운영 테스트 계획

1. `EXEC_ADVISOR_ENABLED=false` 상태에서 CLI simulation만 실행한다.
2. `EXEC_ADVISOR_ENABLED=true`, `EXEC_ADVISOR_SHADOW_ONLY=true`, `EXEC_ADVISOR_CLAUDE_ENABLED=false`로 1세션 shadow 관찰한다.
3. shadow 결과가 오늘 시뮬레이션과 같은지 확인한다.
4. `EXEC_ADVISOR_CLAUDE_ENABLED=true`는 manual/mismatch 케이스에서만 켠다.
5. 하루 Claude call 수가 `EXEC_ADVISOR_MAX_CLAUDE_CALLS_PER_DAY`를 넘지 않는지 확인한다.
6. advisor 이벤트와 실제 주문/포지션 변동이 분리되어 있는지 확인한다.

운영 중 즉시 중단 조건:

```text
advisor가 주문 API를 호출함
normal KEEP_PLAN 케이스에서 Claude 후보가 대량 발생
broker truth stale인데 REPLAN_REQUIRED가 발생
PathB protected sell/hold advisor 호출량이 증가
US PathB target/profit_ladder/pre-close 경로가 advisor로 override됨
```

## 9. 오늘 데이터 기준 재현

시뮬레이션 기준:

```text
snapshot_generated_at: 2026-06-01T15:37:52+00:00
broker_positions: 5
broker_open_orders: 1
active_path_runs_checked: 4
actual_claude_api_calls: 0
simulated_claude_candidates_if_enabled: 1
```

결과:

| Ticker | Action | Claude 후보 | 근거 |
|---|---|---:|---|
| MANUAL_MISMATCH | REPLAN_REQUIRED | yes | Claude replan 입력 기준은 broker actual entry 102.87. target upside +1.011%, stop loss -8.652%, RR 0.117; current 104.09 기준 target 초과/upside -0.173% |
| HPE | KEEP_PLAN | no | entry drift 0%, upside +2.232%, RR 1.096 |
| DELL | KEEP_PLAN | no | entry drift 0%, upside +5.323%, RR 0.857 |
| BBY | KEEP_LIMIT | no | limit까지 +1.698%, pnl +3.472% |
| HPQ | NO_EXECUTION_ADVISOR_ACTION | no | pending execution event 없음 |
| EL | BROKER_RECONCILE_REQUIRED | no | local FILLED but broker position 없음 |

Sensitivity:

```text
conservative: Claude 후보 3건
balanced:     Claude 후보 1건
aggressive:   Claude 후보 1건
```

따라서 초기 운영 기준은 balanced가 맞다.

## 10. 작성 후 코드 레벨 재검토 결과

작성 직후 현재 codebase와 대조한 결과:

- broker truth snapshot의 표준 체결 필드는 `today_fills`다. 요구서도 `today_fills` 기준으로 작성했다.
- `actual_entry_price`, `entry_execution_id`는 `execution/claude_price_adapter.py::mark_filled()`에서 이미 PathB plan에 저장된다.
- `BrokerTruthSnapshot.market_snapshot()` / `refresh_market()`는 TTL 기반 재사용 구조가 있으므로 advisor runtime은 새 KIS 호출보다 snapshot 재사용을 우선해야 한다.
- `runtime/pathb_runtime.py`에는 `scan_waiting_entries()`와 `scan_exits()`가 이미 분리되어 있다. 다만 1분 주기를 맞추려면 hook은 `run_cycle()` 뒤가 아니라 `trading_bot.py::run_entry_scan()` 상단에 붙이는 것이 맞다. 현재 scheduler는 `run_entry_scan`을 1분마다, `run_housekeeping`을 5분마다 호출한다.
- `LifecycleEventType`은 enum 검증을 하므로 custom event를 쓰려면 `lifecycle/models.py` 추가가 필요하다.
- `lifecycle/event_store.py`는 현재 `append()`와 `append_many()` 양쪽에서 `if evt.event_type != "QUALITY_MARKED"`로 `v2_decisions.status` update 예외를 직접 처리한다. `EXECUTION_ADVISOR_DECISION`은 새 `NON_STATUS_EVENT_TYPES` set으로 이 패턴을 확장해야 한다.
- 현재 요구서는 주문 API, cancel API, PathB protected exit, sizing, broker-truth fail-closed를 변경하지 않는 구조다.

## 11. 완료 후 재검토 체크리스트

구현 완료 후 다음을 확인한다.

- 요구서의 Phase와 실제 코드 변경 범위가 일치하는가
- `runtime/pathb_runtime.py` 보호 함수가 변경되지 않았는가
- `trading_bot.py` hook이 PathB scan 이후 shadow audit만 수행하는가
- `EXEC_ADVISOR_CLAUDE_ENABLED=false`에서 Claude call이 0회인가
- `EXEC_ADVISOR_MANUAL_ONLY_CLAUDE=true`에서 manual/mismatch만 후보가 되는가
- HPE/DELL/BBY형 정상 케이스가 로컬 balanced로 끝나는가
- stale broker truth에서 fail-closed/no Claude가 유지되는가
- state 파일이 `state/*execution_advisor_state.json`에만 기록되고 `state/brain.json`을 건드리지 않는가
- live preflight가 기존 warning 외 신규 failure를 만들지 않는가
- full pytest가 통과하는가

## 12. MD 위반 사항 필요 여부

이 요구서 단계에서는 보호 영역 코드 변경이 없으므로 `MD 위반 사항`은 필요 없다.

향후 구현에서 `runtime/pathb_runtime.py`의 protected sell/hold/sizing/broker-truth gate를 직접 수정해야 하면, 구현 PR/커밋에 `MD 위반 사항` 섹션을 별도로 작성한다. 단순 shadow hook이나 별도 advisor module 추가만으로는 보호 계약 완화가 아니다.
