# Trainer Positive/Negative Review and Improvement Plan - 2026-06-03

작성 목적: 긍정적인 트레이너 평가, 부정적인 트레이너 평가, 그리고 두 평가를 검증한 뒤 실제 개선 우선순위를 정리한다.

자료 범위:
- `data/ml/decisions.db`
- `data/v2_event_store.db`
- `data/audit/candidate_audit.db`
- `data/audit/agent_call_events.db`
- `logs/funnel/candidate_funnel_snapshot_20260602_*.jsonl`
- `logs/funnel/watch_trigger_not_evaluated_20260602_*.jsonl`
- 현재 작업트리 상태와 주요 설정 파일

이 문서는 운영 판단용 리뷰다. 코드, config, runtime state, broker state는 변경하지 않았다.

## 0. 균형 결론

US PathB `claude_price`는 현재 시스템의 핵심 수익 엔진이다. 반대로 KR, 후보 selection, execution miss 분석, learning loop, config source, 운영 위생은 아직 수익 엔진 수준만큼 성숙하지 않다.

따라서 방향은 다음과 같다.

- US PathB profit ladder, target close, pre-close, broker-truth fail-closed, AUTO_SELL_REVIEW cooldown은 보호한다.
- KR은 live 확장보다 evidence 품질, broker truth 안정성, shadow/probe 검증을 먼저 닫는다.
- selection 문제와 execution/risk 문제를 같은 패치에서 섞지 않는다.
- 학습 개선은 새 전략 튜닝보다 `learning_allowed=0` 사유를 줄이는 것부터 시작한다.

## 1. 긍정적인 평가

### 1.1 구조적 장점

시스템은 단순한 "AI가 고르면 바로 주문" 구조가 아니다.

현재 경로는 대략 다음처럼 분리되어 있다.

```text
candidate generation
-> Claude selection
-> normalized trade_ready / watch / pullback_wait
-> strategy signal
-> affordability / risk / broker truth gate
-> Path A or Path B order
-> hold advisor / profit ladder / pre-close / loss cap exit
-> event store / audit / learning DB
```

이 분리는 자동매매 시스템에서 매우 중요하다.

- selection 품질 문제와 execution/risk 문제를 분리할 수 있다.
- KR/US 성과를 시장별로 따로 볼 수 있다.
- Claude 판단과 최종 주문 수량/금액 계산이 분리되어 있다.
- broker truth fail-closed 구조가 있어 상태 오염 시 신규 진입을 막을 수 있다.
- 후보, 주문, 청산, missed opportunity, learning DB가 남아 사후 분석이 가능하다.

### 1.2 US PathB 수익 엔진

현재 확인된 canonical live closed 기준:

| Market | Closed n | Win rate | Avg pnl% | 판단 |
|---|---:|---:|---:|---|
| KR | 43 | 27.9% | -1.055% | 손실 구조 |
| US | 122 | 50.0% | +0.735% | PathB 중심으로 양호 |

US 전략별 성과:

| Strategy | n | Win rate | Avg pnl% |
|---|---:|---:|---:|
| claude_price | 79 | 54.4% | +1.047% |
| opening_range_pullback | 7 | 57.1% | +1.692% |
| momentum | 11 | 45.5% | +0.655% |
| gap_pullback | 24 | 37.5% | -0.403% |

해석:

- US `claude_price`는 현재 돈을 버는 축이다.
- US PathB target, profit ladder, pre-close close reason은 실제 수익 기여가 확인된다.
- 이 축은 실험이나 리팩터링의 대상이 아니라 보호 대상이다.

### 1.3 매도 루틴의 강점

US close reason 기준:

| Close reason | n | Win rate | Avg pnl% |
|---|---:|---:|---:|
| CLOSED_CLAUDE_PRICE_TARGET | 10 | 100.0% | +5.311% |
| CLOSED_CLAUDE_PRICE_PRE_CLOSE | 34 | 55.9% | +1.442% |
| CLOSED_PROFIT_LADDER | 20 | 65.0% | +1.097% |

해석:

- target close는 표본은 작지만 매우 강하다.
- pre-close는 US PathB의 중요한 수익 실현 장치다.
- profit ladder는 giveback을 제한하면서 수익을 닫는 역할을 하고 있다.
- 이 3개 경로는 임의로 조건을 완화하거나 advisor 정책을 바꿔서 흔들면 안 된다.

### 1.4 안전장치의 긍정적 의미

KR에서 최근 진입이 거의 없거나 WATCH가 많은 것은 불편하지만, 무조건 나쁜 신호는 아니다.

- evidence missing 상태에서 fail-closed가 작동한다.
- broker truth 불신 시 entry가 막힌다.
- KR Plan A 주요 signal flag가 꺼져 있어 손실 전략의 확장을 막고 있다.
- entry blackout, market closed 차단이 정상적으로 남는다.

긍정적으로 보면 KR은 "좋은 후보를 많이 놓치는 시스템" 이전에 "근거가 부족하면 무리하게 사지 않는 시스템"이다.

## 2. 부정적인 평가

### 2.1 KR live 성과는 좋지 않다

KR 전략별 live closed 성과:

| Strategy | n | Win rate | Avg pnl% |
|---|---:|---:|---:|
| momentum | 11 | 18.2% | -1.955% |
| opening_range_pullback | 6 | 16.7% | -1.320% |
| gap_pullback | 20 | 35.0% | -0.693% |
| claude_price | 4 | 50.0% | +0.337% |

해석:

- KR은 현재 live 확장 대상이 아니다.
- 같은 전략명이라도 KR/US 성과가 다르다.
- KR 개선을 이유로 `strategy/momentum.py`, `strategy/gap_pullback.py` 같은 공유 전략 파일을 직접 바꾸면 US 수익 경로를 훼손할 위험이 있다.

### 2.2 후보 funnel의 전환율이 낮다

2026-06-02 funnel 합계:

| Market | Prompt pool | Execution pool | 해석 |
|---|---:|---:|---|
| KR | 315 | 3 | 후보 대부분 WATCH/비실행 |
| US | 347 | 42 | US도 후보 대비 실행 후보는 제한적 |

watch trigger not evaluated:

| Market | Count | Main reason |
|---|---:|---|
| KR | 1013 | shadow_cycle_cap_exceeded |
| US | 638 | shadow_cycle_cap_exceeded |

해석:

- 후보를 많이 만들지만 실행 후보로 닫히는 비율이 낮다.
- WATCH/SHADOW 후보가 쌓이는 구조는 있지만, 그 결과가 충분히 학습 데이터로 닫히지 않는다.
- "못 산 이유"가 selection 문제인지, evidence 문제인지, execution/risk 문제인지 한눈에 분리되어야 한다.

### 2.3 evidence 품질이 selection 품질을 제한한다

최근 funnel에서 누락이 반복된 핵심 필드:

- `ret_3m_pct`
- `ret_5m_pct`
- `opening_range_break`
- `vwap_distance_pct`
- `volume_ratio_open`

특히 KR은 missing/partial evidence가 많다. 이 상태에서 Claude selection을 돌리면 AI가 장중 근거가 아니라 불완전한 설명을 보고 판단할 가능성이 커진다.

### 2.4 execution miss가 실제 기회를 놓쳤다

`v2_event_store.pathb_miss_quality` 기준:

| Market | Cancel reason | n | Zone reentered | Avg 30m MFE |
|---|---|---:|---:|---:|
| US | INVALID_PRICE | 29 | 26 | +1.222% |

해석:

- 기회가 없어서 못 산 것이 아니라, PathB follow-up이 `INVALID_PRICE`로 취소된 뒤 다시 zone에 들어오고 움직인 케이스가 많다.
- 이건 전략 실패가 아니라 execution pipeline 또는 가격 상태 관리 실패로 분류해야 한다.
- 단, sizing/broker-truth fail-closed 보호 영역을 완화해서 해결하면 안 된다.

### 2.5 hold advisor는 outcome 연결 없이 판단하기 어렵다

2026-06-03 hold advisor decision request:

- 총 22건
- HOLD 20건
- SELL 2건
- 평균 약 8.6초

부정적으로 보면 HOLD 편향과 지연이 있다. 다만 이것만으로 "손해를 만들었다"고 결론 내리면 안 된다.

필요한 것은 advisor action 이후 실제 결과 연결이다.

- HOLD 후 추가 수익이 났는가?
- HOLD 후 giveback이 커졌는가?
- SELL 후 손실 확대를 막았는가?
- SELL 후 다음날 기회비용이 컸는가?
- cooldown skip이 Claude 호출량을 줄이면서 성과를 유지했는가?

### 2.6 config source가 운영자에게 혼란스럽다

확인된 값:

| Key | Value | Source/Use |
|---|---:|---|
| MAX_DAILY_LOSS_PCT | -8.0 | `risk_manager.py` hard rule 계열 |
| DAILY_LOSS_LIMIT_PCT | -2.0 | v2/runtime gate 계열 |
| MAX_POSITIONS | 10 | legacy/global |
| KR_MAX_POSITIONS | 20 | market-specific |
| US_MAX_POSITIONS | 20 | market-specific |
| PATHB_MAX_POSITIONS | 15 | PathB-specific |

해석:

- 값이 여러 개인 것 자체가 항상 버그는 아니다.
- 문제는 각 값이 어떤 gate에서 쓰이는지 운영자가 즉시 알기 어렵다는 점이다.
- "오늘 -2%면 멈추는가, -8%면 멈추는가"를 대시보드/preflight에서 source와 gate 의미별로 보여줘야 한다.

### 2.7 learning loop가 아직 닫히지 않는다

closed canonical 기준:

| Market | Closed n | learning_allowed |
|---|---:|---:|
| KR | 43 | 3 |
| US | 122 | 1 |

`learning_allowed=0` 주요 사유:

| Reason | Count |
|---|---:|
| FORWARD_NOT_MEASURED | 101 |
| ORDER_UNKNOWN_UNRESOLVED | 22 |
| FORWARD_PENDING_DATA | 16 |

해석:

- 지금은 "학습 시스템"보다 "로그 수집 시스템"에 가깝다.
- 새 전략 튜닝보다 먼저 forward/outcome 측정과 ORDER_UNKNOWN 정리를 학습 가능 상태로 만들어야 한다.

### 2.8 운영/레포 위생이 약하다

현재 작업트리에는 tracked diff와 untracked 산출물이 많다.

- `state/brain.json`이 dirty 상태다.
- `config/v2_start_config.json`, `trading_bot.py`, `runtime/pathb_runtime.py`, `minority_report/hold_advisor.py` 등 주요 파일에 diff가 있다.
- 루트에 `-`, `=` 같은 임시 파일이 있다.
- 대형 runtime DB와 maintenance backup DB가 많이 쌓여 있다.

해석:

- 실전 운영 중 원인 분리가 어려워진다.
- live 전 dirty worktree 경고와 runtime 산출물 archive 정책이 더 강해야 한다.

## 3. 내 평가: 긍정/부정 중 무엇을 믿어야 하나

| 주제 | 내 판단 | 이유 | 운영 결정 |
|---|---|---|---|
| US PathB는 강하다 | 동의 | US `claude_price`, target, pre-close, profit ladder 성과 확인 | 보호한다 |
| KR은 약하다 | 강하게 동의 | KR 평균 pnl, 승률, 전략별 손실 확인 | live 확장 금지 |
| 후보가 과잉 생산된다 | 대체로 동의 | funnel 전환율과 WATCH/SHADOW 누적 확인 | funnel KPI 고정화 |
| evidence 누락이 크다 | 동의 | KR latest funnel에서 partial/missing 확인 | selection quality에 직접 반영 |
| PathB INVALID_PRICE miss가 심각하다 | 강하게 동의 | US 29건, 30m MFE +1.222% 확인 | execution miss 리포트 P1 |
| hold advisor가 나쁘다 | 보류 | HOLD 비율과 latency는 확인, outcome 손익 연결은 부족 | outcome linkage 먼저 |
| AI parse error가 많다 | 부분 반박 | `parse_error=1`은 5건뿐 | parse error보다 contract/stage 품질 점검 |
| config가 중복이다 | 동의 | daily loss, position cap source가 여럿 | 통합보다 source/meaning 명문화 먼저 |
| learning loop가 약하다 | 강하게 동의 | learning_allowed가 거의 닫히지 않음 | P1 |
| repo 위생이 약하다 | 동의 | dirty tracked/untracked 산출물 확인 | live preflight 경고 강화 |

핵심은 부정 평가를 그대로 따라 "수익 경로를 고치는 것"이 아니다. 부정 평가에서 믿어야 할 부분은 수익 경로 주변의 측정, 분류, 운영 부채다.

## 4. 개선 우선순위

### P0 - 건드리지 말 것

아래는 지금 돈을 벌거나 안전을 보장하는 보호 영역이다.

- US PathB `CLOSED_CLAUDE_PRICE_TARGET`
- US PathB `CLOSED_PROFIT_LADDER`
- US PathB `CLOSED_CLAUDE_PRICE_PRE_CLOSE`
- PathB broker-truth entry fail-closed
- PathB sizing reason split
- AUTO_SELL_REVIEW HOLD cooldown guard
- zero-holding stale reconcile
- KIS order normalization의 `remaining_qty` 보존

이 영역을 건드려야 하는 이슈가 생기면 `MD 위반 사항` 형식으로 이유, 영향, 대체 안전장치, 테스트를 남겨야 한다.

### P1 - 바로 해야 할 개선

#### 1. Daily Candidate Funnel 리포트 고정화

매일 아래 conversion을 시장별/전략별로 표시한다.

```text
raw candidates
-> prompt_pool
-> actual_prompt_included
-> Claude action
-> normalized/applied trade_ready
-> route decision
-> risk/broker/affordability pass
-> order_sent
-> filled
-> closed
```

필수 breakdown:

- `WATCH`
- `BUY_READY`
- `PULLBACK_WAIT`
- `no_signal`
- `evidence_missing`
- `broker_truth_block`
- `high_price_budget_block`
- `invalid_price`
- `order_size_too_small_gate`
- `entry_blackout`
- `market_closed`
- `already_holding`

#### 2. PathB Missed Opportunity 리포트

`pathb_miss_quality`를 다음 기준으로 매일 분리한다.

- `INVALID_PRICE`
- `HIGH_PRICE_BUDGET_BLOCK`
- `ORDER_SIZE_TOO_SMALL_GATE`
- `EXPIRED`
- `BROKER_TRUTH_BLOCK`
- `SAME_DAY_REENTRY_*`

각 사유별로 표시할 값:

- count
- zone reentry count/rate
- 30m MFE/MAE
- follow-up filled 여부
- 해당 ticker의 subsequent close/outcome
- strategy failure인지 execution/risk failure인지 분류

#### 3. learning_allowed=0 사유 dashboard

현재 가장 중요한 학습 KPI는 수익률이 아니라 학습 가능 row 비율이다.

필수 표시:

- market별 closed count
- learning_allowed count/rate
- `FORWARD_NOT_MEASURED`
- `FORWARD_PENDING_DATA`
- `ORDER_UNKNOWN_UNRESOLVED`
- `CLOSED_WITHOUT_FILL`
- `LEGACY_UNKNOWN`
- `SUSPECT`
- `DIRTY`

목표:

- 먼저 `FORWARD_NOT_MEASURED`를 줄인다.
- 그 다음 `ORDER_UNKNOWN_UNRESOLVED`를 audited remediation 흐름으로 줄인다.
- 전략 자동 튜닝은 learning_allowed coverage가 올라온 뒤에 판단한다.

#### 4. Config Source Health 리포트

값을 바로 합치지 말고, source와 gate 의미를 먼저 보여준다.

예:

| Concept | Effective key | Value | Used by | Meaning |
|---|---|---:|---|---|
| legacy hard daily loss | MAX_DAILY_LOSS_PCT | -8.0 | `risk_manager.py` | hard rule 계열 |
| v2 entry/recovery limit | DAILY_LOSS_LIMIT_PCT | -2.0 | v2/runtime | 신규 진입/회복 gate |
| global max positions | MAX_POSITIONS | 10 | legacy/dashboard | fallback |
| market max positions | KR/US_MAX_POSITIONS | 20/20 | v2/dashboard | 시장별 cap |
| PathB max positions | PATHB_MAX_POSITIONS | 15 | PathB | PathB run cap |

운영자가 봐야 할 것은 "값이 몇 개인가"가 아니라 "어느 gate가 어떤 값으로 막았는가"다.

#### 5. KR live 확장 금지와 shadow/probe 기준 명문화

KR은 다음 조건 전까지 live 확대 금지로 둔다.

- 최소 30 filled probes 또는 4 calendar weeks 관찰
- evidence coverage 안정화
- broker truth block 원인 분리
- WATCH/no_signal 후보 MFE/MAE 라벨 확보
- Plan A 주요 signal flag 재개 전 별도 승인

### P2 - 1~2주 내 구조 개선

#### 1. Evidence quality를 selection score에 직접 반영

missing field가 있으면 단순 로그 경고가 아니라 action ceiling과 quality score에 반영한다.

예:

- 핵심 momentum field missing: `BUY_READY` 금지
- OR/VWAP/volume missing: `PROBE_READY` 또는 `WATCH` ceiling
- provider partial state: route decision에는 남기되 live submit 금지

#### 2. Hold advisor outcome linkage

advisor decision row와 이후 close outcome을 연결한다.

필수 라벨:

```text
advisor_action
decision_stage
review_reason
claude_called
cooldown_skip
subsequent_close_reason
subsequent_pnl_pct
time_to_close
max_gain_after_hold
max_giveback_after_hold
```

이 데이터를 보기 전에는 HOLD 비율만 보고 정책을 바꾸지 않는다.

#### 3. Candidate audit null field 채우기

최신 live candidate audit에서 아래 값들이 비어 있는 비율이 높다.

- `candidate_pool_role`
- `freshness_verdict`
- `trainer_tier`
- `lifecycle_state`
- `evidence_data_state`

새로운 전략 튜닝보다 이 메타데이터를 실제 prompt/action/funnel과 연결하는 것이 먼저다.

#### 4. Counterfactual DATA_MISSING/PENDING 줄이기

counterfactual status는 현재 `DATA_MISSING`과 `PENDING`이 많다. shadow/watch 후보가 학습으로 닫히지 않는 주된 이유 중 하나다.

우선순위:

- 가격 파일 존재 여부
- minute/daily horizon label 보강
- 미래 세션 grace 처리
- retryable/unavailable 상태 분리

### P3 - 운영 위생과 장기 구조

#### 1. Dirty worktree preflight 강화

live 전 아래를 경고한다.

- tracked runtime/config/state 파일 dirty
- `state/brain.json` dirty
- 루트 임시 파일 존재
- 대형 DB backup 증가
- untracked tool/report가 live 경로와 충돌 가능

단, 자동 삭제는 하지 않는다. 사용자가 만든 변경을 임의로 되돌리면 안 된다.

#### 2. Runtime DB archive/vacuum 정책

대상:

- `data/audit/candidate_audit.db`
- `data/v2_event_store.db`
- `data/audit/agent_call_events.db`
- maintenance backup DB

목표:

- 운영 DB와 분석 backup 분리
- 오래된 backup archive
- vacuum은 live process가 완전히 꺼진 상태에서만 수행

#### 3. 큰 파일 분리

`trading_bot.py`, `runtime/pathb_runtime.py`는 크지만, 지금 당장 리팩터링하면 보호 경로를 건드릴 위험이 크다.

분리 순서:

1. read-only report/tool 분리
2. 테스트 보강
3. 안전 경계가 명확한 helper만 추출
4. US PathB 수익 경로 주변은 마지막에 검토

## 5. 운영 판단

### US

US는 유지하되 보호한다.

- `claude_price` PathB는 계속 주력 수익 엔진으로 둔다.
- profit ladder, target, pre-close는 변경하지 않는다.
- 일반 WATCH 확장은 live 주문 승격이 아니라 shadow/probe로만 진행한다. 단 section 8의 screener strict expansion은 `hard_cap_cutoff` 후보를 같은 Claude call에 live prompt로 append하는 범위이며, 주문 권한 확대와 분리한다.
- `INVALID_PRICE` missed opportunity는 P1로 분석한다.

### KR

KR은 공격보다 축소/검증이 맞다.

- KR momentum/gap_pullback/ORP live 확대 금지
- KR Plan A signal flag 재개는 별도 승인 필요
- evidence coverage와 broker truth 안정화 전 신규 실험 확대 금지
- preopen/low-liq는 regular sizing이 아니라 micro/probe 후보로만 본다.

### Selection

BUY_READY는 주문 지시가 아니라 고품질 후보 등급으로 본다.

실제 주문 후보가 되려면 다음을 통과해야 한다.

```text
BUY_READY
-> evidence complete
-> market mode fit
-> strategy signal
-> broker truth fresh
-> affordability/risk pass
-> order candidate
```

### Execution

execution failure를 strategy failure와 분리한다.

특히 다음은 따로 집계한다.

- `INVALID_PRICE`
- `HIGH_PRICE_BUDGET_BLOCK`
- `ORDER_SIZE_TOO_SMALL_GATE`
- `BROKER_TRUTH_BLOCK`
- `ORDER_UNKNOWN_UNRESOLVED`

### Learning

학습 개선은 전략 파라미터 조정보다 먼저 data closure 문제를 해결해야 한다.

가장 먼저 줄일 것:

1. `FORWARD_NOT_MEASURED`
2. `ORDER_UNKNOWN_UNRESOLVED`
3. `FORWARD_PENDING_DATA`
4. candidate audit null metadata
5. counterfactual `DATA_MISSING` / `PENDING`

## 6. 최종 정리

긍정적인 평가가 맞는 부분:

- US PathB에는 실제 수익 엔진이 있다.
- broker truth, risk, audit, learning DB 구조는 훈련 가능한 기반이다.
- fail-closed와 gate가 KR에서 무리한 진입을 막고 있다.

부정적인 평가가 맞는 부분:

- KR live는 현재 확장 대상이 아니다.
- 후보 funnel, evidence, execution miss, learning closure가 약하다.
- config source와 운영 산출물이 복잡해 원인 분리가 어렵다.

내 결론:

지금 필요한 것은 큰 전략 교체가 아니다. US PathB 수익 경로를 보호하면서, KR과 selection/execution/learning/ops의 측정 부채를 줄이는 것이다. 새 매매 아이디어를 늘리기 전에 "왜 안 샀는지", "왜 못 샀는지", "왜 학습되지 않았는지"를 매일 닫을 수 있어야 한다.

## 7. Code/DB 검증 보강 - 2026-06-03

이 섹션은 위 문서의 판단이 코드와 DB 기준으로 맞는지 재검증한 결과다. 코드/config/runtime state는 변경하지 않았다.

### 7.1 재검증 결론

기존 판단은 큰 방향에서 맞다.

- US PathB 수익 엔진 보호 판단은 더 강해졌다.
- KR live 확장 금지 판단도 더 강해졌다.
- learning loop가 약하다는 판단은 DB 기준으로 확인됐다.
- PathB missed opportunity 분석은 필요하지만, `pathb_miss_quality` 단일 테이블만 보면 부족하다는 점이 새로 확인됐다.
- candidate audit metadata blank 문제는 단순 데이터 부족이 아니라 audit write/merge 경로의 코드 레벨 이슈 가능성이 있다.

정정해야 할 부분도 있다.

- funnel 숫자는 live 로그가 계속 append되므로 문서 작성 시점 숫자와 이후 재검증 숫자가 달라질 수 있다.
- agent call 문제는 parse failure가 아니라 contract/stage/latency/cost 문제로 표현해야 한다.
- hold advisor는 HOLD 비율이 높지만, outcome linkage 없이 "성과를 악화한다"고 단정하면 과하다.

### 7.2 성과 판단 재검증

canonical live closed 기준:

| Market | n | Win rate | Avg pnl% | Min pnl% | Max pnl% | PF | learning_allowed | pnl_krw missing |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR | 43 | 27.9% | -1.055% | -9.818% | +8.374% | 0.438 | 3 | 17 |
| US | 122 | 50.0% | +0.735% | -9.272% | +12.761% | 1.864 | 1 | 103~104 |

해석:

- KR은 평균 수익률뿐 아니라 PF도 0.438로 낮다.
- US는 평균 수익률과 PF가 모두 양호하다.
- `pnl_krw` missing은 특히 PathB에 집중된다. US PathB는 closed 118건 중 104건이 `pnl_krw` missing으로 잡혔다. 따라서 총손익 KRW 해석은 아직 불완전하고, pnl% 중심으로 봐야 한다.

US PathB `claude_price` 재검증:

| Market | path_type | Strategy | n | Win rate | Avg pnl% | PF |
|---|---|---|---:|---:|---:|---:|
| US | claude_price | claude_price | 79 | 54.4% | +1.047% | 2.275 |
| US | claude_price | momentum | 11 | 45.5% | +0.655% | 1.715 |
| US | claude_price | opening_range_pullback | 6 | 50.0% | +0.667% | 3.269 |
| US | claude_price | gap_pullback | 20 | 40.0% | -0.205% | 0.783 |

추가 판단:

- US PathB 전체가 모두 좋은 것은 아니다.
- 강한 축은 `claude_price`, target/pre-close/profit ladder와 결합된 경로다.
- US `gap_pullback`은 같은 US 안에서도 약한 축이므로 watch/probe 확장 시 별도 관리가 필요하다.

KR path_type별 재검증:

| Market | path_type | Strategy | n | Win rate | Avg pnl% | PF |
|---|---|---|---:|---:|---:|---:|
| KR | claude_price | gap_pullback | 17 | 35.3% | -0.651% | 0.656 |
| KR | claude_price | momentum | 6 | 16.7% | -2.163% | 0.392 |
| KR | claude_price | opening_range_pullback | 5 | 0.0% | -1.642% | 0.000 |
| KR | claude_price | claude_price | 4 | 50.0% | +0.337% | 1.456 |

추가 판단:

- KR 문제는 Plan A만의 문제가 아니다.
- KR PathB 안에서도 momentum/ORP/gap 계열은 손실 축이다.
- KR `claude_price` 자체는 n=4로 작고 양수지만, 표본이 너무 작아 확장 근거로 쓰기 어렵다.

### 7.3 close reason 판단 재검증

US `claude_price` close reason:

| Close reason | n | Win rate | Avg pnl% | PF |
|---|---:|---:|---:|---:|
| CLOSED_CLAUDE_PRICE_TARGET | 10 | 100.0% | +5.311% | N/A |
| CLOSED_CLAUDE_SELL | 6 | 100.0% | +3.913% | N/A |
| CLOSED_CLAUDE_PRICE_PRE_CLOSE | 34 | 55.9% | +1.442% | 4.792 |
| CLOSED_PROFIT_LADDER | 20 | 65.0% | +1.097% | 7.920 |
| CLOSED_LOSS_CAP | 19 | 0.0% | -2.302% | 0.000 |
| CLOSED_AUDITED_BROKER_SELL | 5 | 0.0% | -3.778% | 0.000 |

해석:

- target/pre-close/profit ladder 보호 판단은 맞다.
- `CLOSED_CLAUDE_SELL`도 수익 close reason으로 추가 관찰 가치가 있다.
- `CLOSED_AUDITED_BROKER_SELL`은 별도 운영/브로커 lifecycle review 대상이다. 단, 이 close reason은 audited remediation 성격일 수 있으므로 단순 전략 손실로 섞으면 안 된다.

관련 코드 위치:

- `runtime/pathb_runtime.py::_pathb_profit_ladder_signal()`은 profit ladder floor 도달 시 `CLOSED_PROFIT_LADDER`를 반환한다.
- `execution/claude_price_sell_manager.py`와 `runtime/pathb_runtime.py`는 target/pre-close close reason을 사용한다.
- 이 경로는 P0 보호 대상으로 유지한다.

### 7.4 learning loop 판단 재검증

closed 기준 learning coverage:

| Market | Closed n | learning_allowed |
|---|---:|---:|
| KR | 43 | 3 |
| US | 122 | 1 |

`learning_allowed=0` 사유:

| Market | Top reasons |
|---|---|
| KR | `FORWARD_NOT_MEASURED` 36, `ORDER_UNKNOWN_UNRESOLVED` 3 |
| US | `FORWARD_NOT_MEASURED` 65, `ORDER_UNKNOWN_UNRESOLVED` 19, `FORWARD_PENDING_DATA` 16, `CLOSED_WITHOUT_FILL` 3 |

코드 기준:

- `lifecycle/quality.py::evaluate_decision_quality()`는 `ORDER_UNKNOWN_UNRESOLVED`, `FORWARD_PENDING_DATA`, `FORWARD_NOT_MEASURED`, `CLOSED_WITHOUT_FILL`을 learning block 사유로 만든다.
- `tools/sync_v2_learning_performance.py::build_learning_row()`는 `forward_measurement_complete(events)`와 `live_clean_learning_allowed()`를 통해 `learning_allowed`를 계산한다.

판단:

- learning loop가 약하다는 기존 판단은 맞다.
- 정확히는 "학습 모델이 약하다"가 아니라 "학습 가능한 row로 닫히는 이벤트 품질과 forward 측정이 부족하다"가 핵심이다.
- 전략 튜닝보다 `FORWARD_NOT_MEASURED`, `ORDER_UNKNOWN_UNRESOLVED`, `FORWARD_PENDING_DATA`를 줄이는 작업이 먼저다.

### 7.5 PathB missed opportunity 판단 재검증

`pathb_miss_quality` live 기준:

| Market | cancel_reason | n | Zone reentered | Avg 30m MFE | First | Last |
|---|---|---:|---:|---:|---|---|
| US | INVALID_PRICE | 29 | 26 | +1.222% | 2026-05-11 | 2026-05-26 |
| US | EXPIRED | 11 | 7 | +1.130% | 2026-05-15 | 2026-05-28 |
| KR | EXPIRED | 8 | 6 | +3.551% | 2026-05-11 | 2026-05-27 |
| US | ALREADY_HOLDING | 7 | 6 | +1.243% | 2026-05-22 | 2026-05-29 |
| US | SAME_DAY_REENTRY_AFTER_STOP | 4 | 3 | +1.787% | 2026-05-26 | 2026-05-27 |
| US | HIGH_PRICE_BUDGET_BLOCK | 1 | 1 | +0.383% | 2026-06-02 | 2026-06-02 |

US `INVALID_PRICE` top tickers:

| Ticker | n | Zone reentered | Avg 30m MFE |
|---|---:|---:|---:|
| AMD | 5 | 5 | +0.295% |
| QCOM | 3 | 3 | +2.828% |
| LITE | 3 | 3 | +0.535% |
| NVDA | 3 | 3 | +0.137% |
| WDC | 2 | 1 | +2.003% |
| SNDK | 2 | 2 | +1.102% |

새로운 정정:

- `pathb_miss_quality`만으로 missed opportunity 전체를 보면 안 된다.
- 최신 candidate audit에는 `PATHB_HIGH_PRICE_BUDGET_BLOCK`이 US 7건 보이지만, `pathb_miss_quality`에는 같은 사유가 1건만 있다.
- 따라서 PathB missed opportunity report는 최소 3개 소스를 합쳐야 한다.

필수 소스:

1. `data/v2_event_store.db:pathb_miss_quality`
2. `data/audit/candidate_audit.db:audit_candidate_rows`
3. `logs/funnel/gate_evaluation_*.jsonl` 또는 `candidate_funnel_snapshot_*.jsonl`

코드 기준:

- `execution/claude_price_adapter.py::_record_miss_quality()`는 PathB cancel 시 `record_pathb_miss_quality()`를 호출한다.
- `execution/safety_gate.py::SafetyGate.evaluate()`는 `INVALID_PRICE`, `HIGH_PRICE_BUDGET_BLOCK`, `ORDER_SIZE_TOO_SMALL_GATE`를 분리한다.
- 즉 reason split 계약은 이미 있다. 문제는 모든 blocked/missed 케이스가 같은 품질로 `pathb_miss_quality`에 닫히는 것은 아니라는 점이다.

### 7.6 candidate audit metadata 새 발견

latest audit metadata coverage:

| Market | Session | Rows | actual_prompt_included | pool_role blank | freshness blank | trainer_tier blank | lifecycle blank | evidence blank |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| KR | 2026-06-03 | 1056 | 576 | 1056 | 1056 | 1056 | 1056 | 816 |
| US | 2026-06-02 | 1870 | 875 | 1870 | 1870 | 1870 | 1870 | 1511 |

해석:

- `actual_prompt_included`는 상당히 채워지고 있다.
- 그러나 `candidate_pool_role`, `freshness_verdict`, `trainer_tier`, `lifecycle_state`는 top-level audit column에서 거의 전부 비어 있다.
- 기존 문서의 "candidate audit null fields 채우기"는 맞지만, 원인을 더 구체화해야 한다.

코드 레벨 관찰:

- `audit/candidate_audit_store.py`는 `EXTRA_CANDIDATE_COLUMNS`에 최신 컬럼을 정의하고, `upsert_candidate()`에서 base insert 후 extra column을 별도 UPDATE한다.
- `_candidate_extra_value()`는 값이 `None`이면 해당 컬럼 업데이트를 건너뛴다.
- `trading_bot.py::_record_candidate_funnel_snapshot()`에서는 `freshness_verdict`를 action에서 먼저 넣지만, 뒤쪽에 펼치는 `_trainer_audit_fields(prompt_row, ...)`도 같은 키를 반환한다.
- `prompt_row`에 해당 값이 없으면 action에서 온 값이 `None`으로 덮일 수 있고, 이후 extra update가 스킵되어 blank가 유지될 수 있다.

정확한 개선 방향:

- audit write dict merge 순서를 테스트로 고정한다.
- action/runtime_gate 값이 있는 경우 prompt_row의 빈 값이 덮어쓰지 못하게 한다.
- `trainer_tier`, `freshness_verdict`, `candidate_pool_role`, `lifecycle_state`는 prompt row, action, route runtime_gate, payload 중 우선순위를 명확히 정한다.
- 이는 runtime trading decision을 바꾸는 작업이 아니라 audit truth 품질 개선 작업으로 좁힌다.

### 7.7 no_submit / hard block 집계 정정

최신 audit route/gate 집계에서 US는 다음이 보인다.

| final_action | gate_reason | no_submit | n |
|---|---|---|---:|
| BUY_READY |  | NO_SIGNAL | 30 |
| HARD_BLOCK | same_day_reentry_blocked |  | 13 |
| HARD_BLOCK | already_holding |  | 7 |
| PULLBACK_WAIT |  | PATHB_HIGH_PRICE_BUDGET_BLOCK | 7 |
| HARD_BLOCK | loss_cap_exited |  | 3 |
| HARD_BLOCK | SAME_DAY_REENTRY_AFTER_STOP |  | 2 |
| HARD_BLOCK | SAME_DAY_REENTRY_COOLDOWN |  | 2 |

정정:

- block reason은 `no_submit_reason_code`만 보면 누락된다.
- 일부는 `route_runtime_gate_reason`에 들어간다.
- 일부 HARD_BLOCK은 reason blank로 남는다.

따라서 Daily Candidate Funnel 리포트는 다음 필드를 합쳐야 한다.

```text
no_submit_reason_code
route_runtime_gate_reason
route_final_action
runtime_gate.reason
payload.runtime_gate.*
```

### 7.8 funnel 숫자 정정

2026-06-02 funnel은 live 로그가 계속 append되어 숫자가 바뀌었다.

재검증 시점 기준:

| Market | full_pool sum | prompt_pool sum | execution_pool sum | trade_ready sum |
|---|---:|---:|---:|---:|
| KR | 315 | 315 | 3 | 3 |
| US | 377 | 377 | 48 | 48 |

watch trigger not evaluated:

| Market | n | Unique tickers | Reason |
|---|---:|---:|---|
| KR | 1013 | 42 | shadow_cycle_cap_exceeded |
| US | 803 | 45 | shadow_cycle_cap_exceeded |

evidence missing field 합계:

| Market | Top missing fields |
|---|---|
| KR | `opening_range_break` 153, `vwap_distance_pct` 143, `volume_ratio_open` 143, `ret_3m_pct` 106, `ret_5m_pct` 106 |
| US | `opening_range_break` 48, `ret_3m_pct` 18, `ret_5m_pct` 18, `vwap_distance_pct` 18, `volume_ratio_open` 18 |

정정:

- 기존 문서의 funnel 숫자는 작성 시점 snapshot으로 보면 맞지만, 이후 로그 append 때문에 숫자가 달라질 수 있다.
- 문서에는 "session 누적 합계는 로그 append 시점 의존"이라고 명시해야 한다.

### 7.9 agent/Claude 판단 정정

agent call DB 재검증:

| Metric | Value |
|---|---:|
| calls | 4957 |
| input tokens | 15,121,524 |
| output tokens | 2,593,916 |
| avg duration | 10.24 sec |
| parse_error=1 | 5 |

top call labels:

| Label | Calls | Avg sec | Input M | Output M | parse_errors |
|---|---:|---:|---:|---:|---:|
| hold_advisor_bull | 912 | 7.45 | 1.375 | 0.363 | 0 |
| hold_advisor_bear | 911 | 7.68 | 1.376 | 0.359 | 0 |
| hold_advisor_neutral | 908 | 7.54 | 1.365 | 0.365 | 0 |
| select_tickers | 549 | 23.67 | 5.426 | 0.927 | 3 |
| hold_advisor_triage | 86 | 10.87 | 0.133 | 0.046 | 0 |

정정:

- parse error가 많다는 표현은 틀리다.
- 문제는 parse failure가 아니라 비용, latency, parse_stage 공백, contract 품질, outcome 연결 부족이다.
- `select_tickers`는 평균 23초대로 가장 무거운 핵심 호출이다.
- hold advisor 3-vote 계열은 개별 평균 7초대이고 호출량이 많다.

### 7.10 기존 도구와 중복 여부

이미 일부 기능은 존재한다.

- `tools/monitoring_ops_report.py`: learning gate, candidate audit coverage, hold advisor latency 일부 제공
- `tools/analyze_candidate_audit.py`: candidate audit 분석
- `tools/analyze_kr_live_replay.py`: KR live replay/funnel 분석
- `tools/overnight_us_monitor.py`: US overnight monitor, ORDER_UNKNOWN/keyword 관찰
- dashboard 일부: canonical learning allowed, candidate funnel, config display 일부 제공

따라서 새 도구를 무조건 만들기보다 기존 도구를 확장하는 것이 낫다.

확장 대상:

- `monitoring_ops_report.py`에 PathB missed opportunity multi-source summary 추가
- `analyze_candidate_audit.py`에 audit metadata top-level vs payload coverage 비교 추가
- dashboard/preflight에 config source/gate meaning 표시 강화
- candidate funnel UI/API에서 `no_submit_reason_code`와 `route_runtime_gate_reason` 통합 표시

### 7.11 개선 우선순위 조정

검증 후 우선순위는 다음처럼 조정한다.

P1 유지:

1. US PathB 수익 경로 보호
2. KR live 확장 금지
3. learning_allowed=0 사유 축소
4. PathB missed opportunity 분석
5. config source/gate meaning 명문화

P1에 추가:

1. candidate audit metadata overwrite/coverage 개선
2. no_submit/hard_block reason 통합 집계
3. `pathb_miss_quality`와 candidate audit/gate log의 miss count 불일치 확인

P2로 유지:

1. hold advisor outcome linkage
2. counterfactual DATA_MISSING/PENDING 감소
3. candidate audit null field backfill
4. gross exposure/live market risk adapter 관찰 강화

하지 말 것:

- US PathB profit ladder/target/pre-close 조건 변경
- broker-truth fail-closed 완화
- PathB sizing reason split 완화
- parse error 5건만 보고 Claude prompt/contract 대규모 재설계
- hold advisor HOLD 비율만 보고 SELL 편향으로 정책 변경

### 7.12 최종 검증 판단

기존 MD의 핵심 방향은 맞다. 다만 더 정확한 표현은 다음이다.

```text
US PathB는 수익 엔진이 맞다.
KR은 아직 live 확장 대상이 아니다.
selection은 후보를 많이 만들지만 execution/learning으로 닫히는 품질이 약하다.
learning 문제는 모델 문제가 아니라 forward/order lifecycle/audit metadata closure 문제다.
PathB missed opportunity는 pathb_miss_quality 단일 DB가 아니라 audit/gate 로그까지 합쳐야 보인다.
candidate audit metadata blank는 데이터 부족뿐 아니라 audit write merge 순서 문제 가능성이 있다.
```

따라서 다음 작업은 전략 철학 변경이 아니라 read-only 운영 리포트와 audit truth 개선이다. 다만 이후 screener DB replay 검증에서 기존 CORE 후보를 유지한 채 cap 밖 후보만 추가 노출하는 방식은 전체 구조 교체가 아니라 live 후보군 확장으로 분리할 수 있음이 확인되었으므로, section 8에서 별도 설계로 보정한다.

## 8. Screener live improvement validation - 2026-06-03

### 8.1 검증 목적

스크리너 개선은 전체 selection 구조를 교체하는 작업이 아니다. 현재 시스템의 장점인 최신 context 기반 Claude 판단, 단순 compact schema, US PathB 수익 경로, broker/risk fail-closed 구조를 유지하면서 `hard_cap_cutoff`로 빠지는 유망 후보를 live prompt에 추가로 노출하는 작업이다.

따라서 검증 질문은 다음처럼 좁힌다.

```text
기존 CORE 후보를 유지한 상태에서
cap 밖 후보를 일부 추가하면
30m/60m 후보 품질이 좋아지는가?
```

사용한 검증 데이터:

| DB | 목적 |
|---|---|
| `data/ml/claude_decision_facts.db` | prompt 포함 여부, trainer state/rank, bucket, Claude action 확인 |
| `fact_forward_outcome` | 30m/60m forward outcome 평가 |
| 기간 | 2026-05-20 이후 |
| 범위 | 319개 Claude call group, 후보 11,004개 |

forward outcome은 평가에만 사용했고, 후보 점수 계산에는 사용하지 않는다.

### 8.2 검증 결과 요약

기존 후보를 교체하는 방식은 부적합하다. 특히 KR에서 `v2_same_n`처럼 같은 후보 수를 새 점수로 재정렬하면 평균 60분 수익률이 오히려 낮아졌다.

| Market | Scenario | Rows | n60 | Avg 60m | PF60 | Judgment |
|---|---|---:|---:|---:|---:|---|
| KR | current prompt | 2913 | 905 | +0.4651% | 1.2977 | baseline |
| KR | v2 same n replacement | 2913 | 909 | +0.4320% | 1.3280 | 평균 악화 |
| KR | CORE +5 strict expansion | 3106 | 987 | +0.6203% | 1.4169 | 개선 |
| KR | CORE +10 strict expansion | 3205 | 1027 | +0.7432% | 1.5074 | 가장 강함 |
| US | current prompt | 2339 | 810 | +0.7085% | 2.9918 | baseline |
| US | v2 same n replacement | 2339 | 832 | +0.7199% | 3.2961 | 좋아 보이나 CORE retention 낮음 |
| US | CORE +5 expansion | 2699 | 933 | +0.7366% | 3.2481 | 적정 |
| US | CORE +10 expansion | 3059 | 1067 | +0.6981% | 3.0507 | 평균 약화 |

핵심 판단:

```text
교체형 개선은 금지한다.
기존 CORE는 그대로 유지한다.
개선은 cap 밖 후보를 append하는 방식으로 제한한다.
```

### 8.3 US 후보 75개 확대안 재검토

`docs/design/tiered_selection_design_v3.md`는 US 후보 입력을 75개까지 확대하는 방안을 제안한다. 해당 문서에서 가져올 계약은 유용하다.

채택할 내용:

- `select_tickers()` compact schema 유지
- `candidate_actions` 계약 유지
- `price_targets` Phase 1 유지
- `CLAUDE_SELECTION_COMPACT_WATCH_MAX=15`, `CLAUDE_SELECTION_COMPACT_TRADE_READY_MAX=5` 유지
- 후보 입력 확대는 주문 후보 확대가 아니라는 계약 유지
- `prompt_pool_count`, `evidence_prompt_overlap_ratio`, `candidate_actions_missing_contract`, PathB `PULLBACK_WAIT` 등록 수를 운영 지표로 사용
- raw top_n, prompt cap, trainer hard cap, evidence cap 정합성 확인
- sub_screener trigger는 full rescreen 낭비 대신 다음 selection 강제 호출 reason으로 보존

보정할 내용:

US 75개 확대는 현재 DB replay 기준으로 과하다. stress check 결과는 다음과 같다.

| US Scenario | Rows | n60 | Avg 60m | PF60 | Bad <= -1% | Good >= 1% |
|---|---:|---:|---:|---:|---:|---:|
| current | 2339 | 810 | +0.7085% | 2.9918 | 105 | 267 |
| plus5 | 2699 | 933 | +0.7366% | 3.2481 | 112 | 312 |
| plus10 | 3059 | 1067 | +0.6981% | 3.0507 | 138 | 351 |
| plus20 | 3773 | 1287 | +0.6806% | 2.9816 | 163 | 428 |
| plus40 | 4767 | 1566 | +0.6821% | 2.9463 | 196 | 499 |
| all available tail | 5027 | 1600 | +0.6538% | 2.7587 | 211 | 506 |

따라서 v3의 75개 목표는 비용 관점에서는 가능할 수 있지만, 현재 screener tail 품질과 Claude attention 관점에서는 1차 live 스크리너 개선안으로 채택하지 않는다.

정정된 판단:

```text
US는 CORE +5 expansion부터 적용한다.
75개 확대는 raw screener diversity, evidence coverage, candidate_actions 안정성이 개선된 뒤 재검토한다.
```

### 8.4 Live screener improvement design

개선 구조:

```text
raw screener 후보
-> 기존 trainer scoring
-> 기존 CORE prompt pool 유지
-> hard_cap_cutoff 후보 중 market-specific strict rule 통과 후보 선별
-> EXPANSION 후보를 CORE 뒤에 append
-> Claude는 CORE와 EXPANSION을 같은 call에서 최신 context로 비교
-> 기존 watch/TR output cap은 유지
-> 기존 execution/risk/PathB/broker truth는 변경하지 않음
```

역할 구분:

| Role | Meaning | Live behavior |
|---|---|---|
| CORE | 기존 prompt 후보 | 기존 순서와 계약 유지 |
| EXPANSION | cap 밖 회수 후보 | prompt에 추가 노출하되 CORE를 밀어내지 않음 |

EXPANSION은 설계 용어다. 현재 코드와 DB/audit 계약에는 이미 `DISCOVERY` overlay가 있으므로, 구현에서는 새 `candidate_pool_role=EXPANSION`을 만들지 않고 기존 `candidate_pool_role=DISCOVERY`를 사용한다. section 8의 EXPANSION 후보는 "strict rule을 통과한 DISCOVERY 후보"로 매핑한다.

DISCOVERY/EXPANSION 후보에는 다음 metadata를 붙인다.

```text
candidate_pool_role = DISCOVERY
discovery_reason = hard_cap_recovered / near_breakout / source_consensus
discovery_signal_family
trainer_score_rank
trainer_prompt_score
trainer_candidate_state
primary_bucket
evidence_class
quality_data_gaps
source_tags
```

### 8.5 Market-specific expansion rules

KR은 전체 PLAN_B가 아니라 PLAN_B의 세부 bucket 조합을 분리해야 한다.

KR expansion 허용:

```text
PLAN_A
near_breakout
PLAN_B + near_breakout
PLAN_B + liquidity_leader or momentum_now + change_pct < 7%
```

KR expansion 약화 또는 제외:

```text
PLAN_B + volume_surge only
PLAN_B + pullback_watch only
change_pct >= 15%
data_quality bad/missing/stale
evidence missing or stale
flow_missing/all_zero flow
```

US expansion 허용:

```text
PLAN_A or PLAN_B with high score
momentum_now
near_breakout
high liquidity
source consensus candidate
```

US expansion 약화 또는 제외:

```text
pullback_watch only
low liquidity
extreme chase
quarantine
data degraded
```

### 8.6 장점 보존 및 개선 효과

| Existing strength | Preservation |
|---|---|
| 최신 context 기반 판단 | Delta/snapshot 구조를 만들지 않고 동일 call에서 fresh digest/intraday context 사용 |
| 단순 compact schema | Phase 1에서 prompt/output 계약 변경 없음 |
| US PathB 수익 경로 | PathB runtime, price plan, ladder, pre-close, hold advisor, broker truth 미변경 |
| fail-closed safety | broker/risk/order gate 미변경 |
| audit 가능성 | 기존 candidate audit/meta payload에 role/reason을 추가하는 방식으로 추적 |

좋아지는 부분:

- `hard_cap_cutoff`로 빠지던 유망 후보를 일부 회수한다.
- KR의 단순 `volume_surge`/`pullback_watch` 노이즈를 줄이고 `near_breakout` 조합을 살린다.
- US는 기존 PathB 친화 후보를 밀어내지 않으면서 cap 밖 후보를 소폭 회수한다.
- 후보 확장은 주문 수 확대가 아니라 Claude 비교 입력 확대에 머문다.

### 8.7 리스크와 통제 기준

이 설계는 시스템 전체 교체가 아니라 확장이다. 그래도 후보 입력이 늘면 Claude 판단 공간이 바뀌므로 운영 지표가 필요하다.

| Risk | Control |
|---|---|
| CORE 훼손 | 개선 후 prompt 앞쪽 CORE tickers가 기존 CORE와 일치해야 함 |
| Claude attention 분산 | KR +5부터 시작, US +5 고정, watch/TR output cap 유지 |
| tail 후보 품질 저하 | EXPANSION은 strict rule 통과 후보만 append |
| evidence 누락 | `evidence_prompt_overlap_ratio`, `evidence_missing_fields` 추적 |
| candidate action 계약 불안정 | `candidate_actions_missing_contract`, duplicate action, parse recovery 증가 여부 추적 |
| US PathB 품질 훼손 | PathB `PULLBACK_WAIT` 등록 수와 US PathB candidate retention 추적 |
| KR 잘못된 live 진입 증가 | KR expansion은 evidence/VWAP/OR/ret_3m/ret_5m confirmation과 기존 risk gate 통과 필요 |
| sub_screener 과민 호출 | trigger를 full rescreen이 아니라 다음 selection FORCE_CALL reason으로 통합 |

적용 후 매일 확인할 지표:

```text
prompt_pool_count by market
CORE retention
EXPANSION count
EXPANSION 30m/60m avg, MFE, MAE
hard_cap_cutoff remaining quality
evidence_prompt_overlap_ratio
candidate_actions_missing_contract
PathB PULLBACK_WAIT count
selection latency
select_tickers token cost
```

### 8.8 Current code bridge and missing implementation details

현재 코드 기준으로 section 8은 새 스크리너 구조를 만드는 작업이 아니라 기존 `DISCOVERY` overlay를 강화하는 작업으로 이어지는 것이 맞다.

구현 연결점:

```text
runtime/candidate_prompt_pool.py
-> 기존 CORE prompt pool과 excluded_from_prompt/hard_cap_cutoff 생성

runtime/candidate_discovery_overlay.py
-> hard_cap_cutoff 후보 중 DISCOVERY 후보를 CORE 뒤에 append

minority_report/analysts.py
-> role=DISCOVERY, ceiling, signal, reason을 Claude prompt에 표시

trading_bot.py
-> DISCOVERY 후보의 BUY_READY/PULLBACK_WAIT 권한을 runtime flag로 제한
```

현재 코드와 문서 설계의 차이:

| 항목 | 현재 코드/config | section 8 보강 판단 |
|---|---|---|
| role 이름 | `DISCOVERY` | `EXPANSION`은 설계 용어로만 쓰고 구현은 `DISCOVERY` 유지 |
| append 구조 | `core_pool + discovery_rows` | CORE retention 원칙과 일치 |
| 슬롯 | KR 4, US 3 | 1차 live는 KR +5, US +5로 맞추는 것이 문서 기준 |
| strict rule | generic signal rule | KR/US market-specific strict rule 추가 필요 |
| 주문 권한 | `DISCOVERY_ALLOW_BUY_READY=false`, `DISCOVERY_ALLOW_PULLBACK_WAIT=false` | 기본은 live prompt 노출 + WATCH ceiling. 주문 후보 승격은 별도 승인/게이트 |
| evidence | `SELECTION_FULL_EVIDENCE_MAX=5` | expansion 증가 시 evidence 없는 후보가 생길 수 있으므로 ceiling/overlap 추적 필수 |
| sub_screener | PLAN_A 수, PLAN_B score trigger | section 8 strict rule과 같은 판정 helper를 공유해야 함 |

추가로 필요한 구현 전 확인:

1. `DISCOVERY_MAX_SLOTS_KR/US`를 section 8 목표와 맞출지 결정한다. 1차는 US 5가 live prompt 기준이며, KR 5는 주문 권한 확대가 아닌 prompt-only 노출 기준으로만 해석한다.
2. `DISCOVERY_ALLOW_BUY_READY`, `DISCOVERY_ALLOW_PROBE_READY`, `DISCOVERY_ALLOW_PULLBACK_WAIT`는 기본 false를 유지한다. 이 상태에서도 live prompt 확장은 가능하지만 주문 후보 승격은 막힌다.
3. 주문 후보 승격까지 열려면 market별로 분리한다. US는 `PULLBACK_WAIT`부터, KR은 evidence/VWAP/OR/ret_3m/ret_5m complete 후보만 검토한다.
4. KR `volume_surge only`, `pullback_watch only`, `change_pct >= 15`, `data_quality bad/missing/stale`, `flow_missing/all_zero`는 DISCOVERY에서 제외한다.
5. US `pullback_watch only`, low liquidity, extreme chase, data degraded는 DISCOVERY에서 제외한다.
6. `sub_screener` trigger는 full rescreen을 반복 호출하지 말고, 같은 strict rule을 통과한 신규 후보가 있을 때 다음 selection의 `FORCE_CALL` reason으로 남긴다.
7. 테스트는 CORE tickers가 앞쪽에서 유지되는지, DISCOVERY가 뒤에 append되는지, discovery ceiling이 BUY_READY/PULLBACK_WAIT를 기본 WATCH로 강등하는지, evidence overlap이 낮을 때 ceiling이 적용되는지를 확인해야 한다.

### 8.9 최종 설계 판단

현재 DB 기준으로 가장 타당한 screener live 개선안은 다음이다.

```text
1. 기존 CORE 후보를 교체하지 않는다.
2. EXPANSION 후보를 CORE 뒤에 append한다.
3. KR은 strict expansion +5부터 시작하고, DB/운영 지표가 유지되면 +10까지 허용한다.
4. US는 expansion +5를 1차 live 기준으로 둔다.
5. v3 문서의 US 75개 확대는 현재 DB 기준으로 보류한다.
6. v3 문서의 compact schema, output cap, price_targets 유지, audit 지표는 채택한다.
7. PathB, broker truth, risk sizing, 주문/청산 로직은 변경하지 않는다.
8. 구현 명칭은 기존 `DISCOVERY`를 유지하고, section 8의 EXPANSION은 strict DISCOVERY로 매핑한다.
9. 기본 live 단계에서는 DISCOVERY 후보를 prompt에 노출하되 주문 권한은 기존 ceiling으로 제한한다.
```

따라서 screener 개선은 "전체 구조 교체"가 아니라 "기존 선택 품질 위에 후보 노출 폭을 통제해서 늘리는 live 확장"으로 정의한다.

### 8.10 Cross-document contamination guard

이 report는 DB replay와 운영 판단 근거 문서다. 구현 설계 문서와 중복되는 내용이 있더라도, 이 문서만 보고 코드/config를 바꾸면 안 된다. section 8에서 확정하는 것은 다음의 판단 근거다.

```text
DB replay 기준:
- 교체형 screener 개선은 금지한다.
- 기존 CORE 후보를 보존한다.
- cap 밖 후보는 strict rule 통과분만 CORE 뒤에 append한다.
- 구현 role은 DISCOVERY를 유지한다.
- 주문 권한 확대와 prompt 입력 확대를 분리한다.
```

오염 방지 기준:

| 위험 | section 8 기준 |
|---|---|
| `EXPANSION`을 DB/audit role로 구현 | 금지. `EXPANSION`은 설계 용어이고 구현은 `candidate_pool_role=DISCOVERY` |
| `expansion_reason` 신규 필드 생성 | 금지. 기존 `discovery_reason`, `discovery_signal_family`, `discovery_overlay_rank` 사용 |
| KR live 확장 오해 | KR strict expansion은 prompt-only 노출로 해석. `DISCOVERY_ALLOW_BUY_READY/PROBE_READY/PULLBACK_WAIT=false` 유지 |
| US 75개 즉시 full prompt 확대 | 보류. DB replay 기준 1차는 US CORE +5 DISCOVERY, 75개는 raw diversity/evidence 안정 후 재검토 |
| Smart Skip과 screener expansion 동시 적용 | 보류. 호출 수 절감 효과와 후보 품질 개선 효과가 섞이므로 별도 phase로 분리 |

따라서 이 report에서 바로 이어지는 screener 작업은 다음 하나로 좁힌다.

```text
기존 CORE prompt 보존
-> hard_cap_cutoff 후보 중 market-specific strict rule 통과분만 DISCOVERY로 append
-> watch/TR output cap 유지
-> DISCOVERY 주문 권한 ceiling 유지
-> PathB/broker truth/risk/order/exit 로직 미변경
```

### 8.11 Implementation QA and replay check

구현 후 확인한 실제 적용 범위는 다음으로 제한한다.

```text
runtime/candidate_discovery_overlay.py
tests/test_candidate_discovery_overlay.py
```

변경된 동작:

- `DISCOVERY` overlay는 기존 CORE 후보를 교체하지 않고 뒤에 append한다.
- 기본 slot은 미설정 시 KR/US 모두 5로 해석한다. 단, 실제 운영 config가 별도 값을 주면 config 값을 따른다.
- KR은 `volume_surge only`, `pullback_watch only`, `change_pct >= 15%`, bad/stale evidence, bad flow, low liquidity를 제외한다.
- KR `PLAN_B`의 `momentum_now`/`liquidity_leader`는 `change_pct < 7%`일 때만 DISCOVERY로 허용한다.
- US는 `PLAN_A`, high-score `PLAN_B`, `momentum_now`, `near_breakout`, `source_consensus`, high-liquidity 후보를 허용한다.
- US도 low liquidity, extreme chase, bad/stale evidence, pullback-only 후보를 제외한다.
- PathB runtime, broker truth, risk sizing, 주문/청산, hold advisor, `state/brain.json`은 변경하지 않았다.

구현 후 local DB replay 결과:

| Market | Scenario | Rows | n60 | Avg 60m | PF60 | Discovery-only Avg 60m | Judgment |
|---|---|---:|---:|---:|---:|---:|---|
| KR | current prompt | 2913 | 905 | +0.4651% | 1.2977 | - | baseline |
| KR | old generic DISCOVERY +5 | 3118 | 989 | +0.4075% | 1.2558 | -0.2133% | 부적합 |
| KR | implemented strict DISCOVERY +5 | 3072 | 986 | +0.4624% | 1.3072 | +0.4314% | tail 품질 개선, 총합은 baseline 근접 |
| US | current prompt | 2341 | 810 | +0.7085% | 2.9918 | - | baseline |
| US | old generic DISCOVERY +5 | 2426 | 840 | +0.7464% | 3.1375 | +1.7701% | 성과는 강하지만 더 공격적 |
| US | implemented strict DISCOVERY +5 | 2426 | 841 | +0.7226% | 3.0424 | +1.0910% | 보수적 개선 |

해석:

- KR은 generic 확장이 명확히 나쁘므로 strict rule이 필요하다는 판단이 강화됐다.
- KR implemented strict는 discovery-only tail을 양수로 바꾸지만, 전체 prompt 평균을 크게 끌어올리지는 못한다. 따라서 KR은 prompt 노출 확장과 주문 권한 확대를 계속 분리해야 한다.
- US implemented strict는 current prompt 대비 평균 60m와 PF60이 모두 개선된다. 다만 old generic보다 보수적이므로, 수익 극대화보다 운영 안정성을 우선한 설계다.
- 8.2의 설계 replay 수치는 후보 scoring/replay 조건이 더 넓었던 결과이며, 구현 후 실제 strict rule replay는 위 표를 운영 판단 기준으로 본다.

실행한 검증:

```text
python -m pytest tests/test_candidate_discovery_overlay.py -q
python -m pytest tests/test_candidate_quality_trainer.py::CandidateQualityTrainerTests::test_discovery_overlay_appends_after_trainer_prompt_pool tests/test_candidate_quality_trainer.py::CandidateQualityTrainerTests::test_select_tickers_prompt_labels_discovery_candidate -q
python -m pytest tests/test_candidate_action_live_mapping.py -k discovery -q
python -m pytest tests/test_candidate_audit.py tests/test_v2_learning_performance_sync.py -k discovery -q
python -m pytest tests/test_candidate_discovery_overlay.py tests/test_candidate_quality_trainer.py tests/test_candidate_action_live_mapping.py -q
python -m pytest tests/test_candidate_audit.py tests/test_v2_learning_performance_sync.py -q
python -m py_compile runtime/candidate_discovery_overlay.py minority_report/analysts.py trading_bot.py
python tools/live_preflight.py --mode live --skip-dashboard --json
python tools/live_preflight.py --mode paper --skip-dashboard --json
```

검증 결과:

- screener/discovery 단위 테스트: 통과
- prompt label / CORE 뒤 append 계약: 통과
- DISCOVERY 주문 권한 ceiling 관련 테스트: 통과
- candidate audit / learning sync 주변 테스트: 통과
- 관련 통합 묶음: 통과
- `py_compile`: 통과
- live preflight: PASS
- paper preflight: FAIL. paper broker/config truth 실패이며 이번 screener 변경과 직접 관련 없음.

### 8.12 Post-implementation document delta

구현 후 문서 대비 차이와 남은 항목:

| 항목 | 상태 | 판단 |
|---|---|---|
| CORE 보존 | 구현됨 | 기존 prompt 후보 순서를 유지한다. |
| `DISCOVERY` role 유지 | 구현됨 | `EXPANSION` 신규 role/field를 만들지 않는다. |
| US +5 prompt 확장 | 구현 가능 | 현재 config가 `DISCOVERY_MAX_SLOTS_US=5`이면 설계와 일치한다. |
| KR +5 prompt 확장 | 부분 차이 | 코드 기본값은 5지만 운영 config가 4면 4를 따른다. KR은 보수 운영이므로 blocking 차이는 아니다. |
| DISCOVERY 주문 권한 ceiling | 유지 | `DISCOVERY_ALLOW_*` 기본 false 전제 유지. |
| US 75개 확대 | 미적용 | 현재 tail 품질 기준 보류가 맞다. |
| Smart Skip | 미적용 | screener 품질 개선과 호출 절감 효과를 분리하기 위해 별도 phase로 둔다. |
| sub_screener FORCE_CALL 통합 | 미적용 | 이번 변경 범위 밖이다. 후보 품질 개선 후 별도 운영성 개선으로 검토한다. |
| evidence selective injection | 미적용 | 현재 overlay는 이미 붙어 있는 bad/stale evidence만 제외한다. evidence pack 최적화는 별도 selection prompt 개선 대상이다. |
| MD 삭제 | 미수행 | 이 report는 구현 근거와 replay 결과를 보존하는 audit 문서이므로 삭제하지 않는다. 삭제는 별도 명시 지시가 필요하다. |

최종 적용 판단:

```text
적합:
- US strict DISCOVERY +5는 prompt 후보 폭을 늘리면서 baseline 성과를 보수적으로 개선한다.
- KR strict DISCOVERY는 generic tail 오염을 줄인다.
- 주문 권한, PathB, broker truth, risk gate를 변경하지 않아 수익 엔진과 fail-closed 안전장치를 훼손하지 않는다.

주의:
- KR은 replay상 discovery-only가 좋아졌더라도 전체 live 주문 확대 근거로는 부족하다.
- US는 old generic tail이 더 강하게 보이나, low liquidity/extreme chase/data degraded 리스크를 줄이는 strict 설계가 1차 live에는 더 적합하다.
- 성능 판단은 prompt 품질 기준이며, 실제 filled PnL 개선은 candidate_actions, PathB registration, filled/closed funnel을 며칠 이상 누적해서 다시 봐야 한다.
```
