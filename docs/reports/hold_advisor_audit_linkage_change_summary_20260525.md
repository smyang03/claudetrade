# Hold Advisor / Candidate Audit 수정 사항 요약 - 2026-05-25

## 목적

이번 수정은 hold advisor 호출 폭증과 Claude 품질 분석 불확실성을 줄이기 위한 선행 계측 작업입니다.

핵심 판단은 다음과 같습니다.

- 최근 hold advisor 호출 수가 2026-05-21 246회, 2026-05-22 369회, 2026-05-23 269회로 급증했습니다.
- cache를 바로 켜면 호출 수는 줄일 수 있지만, 현재는 `duration_ms`가 비어 있어 지연 개선 효과를 실측할 수 없습니다.
- candidate audit의 `config_hash`, `execution_decision_id`, `entry_timing_snapshot_json`이 거의 비어 있어 Claude 판단 문제와 execution/risk/timing 문제를 분리하기 어렵습니다.
- KR watch_only blocked ratio가 높더라도 trade_ready forward 성과가 약한 상태라 gate 완화는 위험합니다.

따라서 이번 변경은 live 매매 정책을 바꾸지 않고, 이후 cache/tiering/gate 분석을 가능하게 하는 관측성과 audit linkage를 먼저 채우는 데 한정했습니다.

## 수정 파일

| 파일 | 수정 내용 | 이유 |
|---|---|---|
| `minority_report/hold_advisor.py` | hold advisor Claude 단일 호출마다 `duration_ms` 측정 및 raw call 저장 | cache 적용 전 지연 baseline과 모델 호출 latency를 실측하기 위해 |
| `minority_report/hold_advisor.py` | 3-vote request 전체 duration을 decision JSONL과 반환값에 기록 | symbol/reason/stage 단위의 실제 advisor 지연을 분석하기 위해 |
| `trading_bot.py` | candidate audit config fingerprint 생성 | 어떤 config/model/flag 상태에서 후보 판단이 만들어졌는지 재현하기 위해 |
| `trading_bot.py` | audit candidate row에 `config_hash`, `feature_flags_json` 기록 | config 변경 전후 selection 품질 비교와 회귀 분석을 가능하게 하기 위해 |
| `trading_bot.py` | v2 trade_ready 등록 후 audit row에 `execution_decision_id` 연결 | Claude selection row와 execution lifecycle decision을 연결하기 위해 |
| `trading_bot.py` | Path A buy_order decision event에 v2 decision id를 넘기도록 순서 조정 | 주문 이벤트가 candidate audit row에 바로 연결되도록 하기 위해 |
| `trading_bot.py` | buy event에 explicit timing snapshot이 없을 때 fallback `entry_timing_snapshot_json` 기록 | 최소한 주문 시점, 가격, 전략, ticker를 남겨 timing 분석 공백을 줄이기 위해 |
| `tests/test_claude_quality_contracts.py` | hold advisor `duration_ms` 저장 테스트 추가 | raw call duration 계측이 회귀로 빠지지 않게 하기 위해 |
| `tests/test_candidate_action_live_mapping.py` | config hash, v2 decision id linkage, fallback entry snapshot 테스트 추가 | audit linkage 필드가 실제 SQLite row에 저장되는지 검증하기 위해 |
| `docs/reports/claude_cost_quality_recheck_simulation_20260524.md` | 2026-05-25 검토 반영 섹션 추가 | cache 전 duration logging, audit linkage 선행, KR gate 완화 보류 판단을 리포트에 반영하기 위해 |
| `tools/analyze_hold_advisor_latency.py` | hold advisor latency p50/p95 리포트 도구 추가 | TTL cache key 설계 전 analyst/stage/market별 지연 병목을 확인하기 위해 |
| `tools/analyze_candidate_audit.py` | `watch_only_bucket_decomposition` 추가 | KR watch_only blocked/missed-runup 원인을 evidence, PathB, risk, routing, no-signal 등으로 분해하기 위해 |
| `tests/test_analyze_hold_advisor_latency.py` | latency 리포트 도구 테스트 추가 | raw call/decision JSONL 기반 p50/p95 집계가 회귀하지 않게 하기 위해 |
| `tests/test_candidate_audit.py` | watch_only bucket decomposition 테스트 추가 | candidate audit row가 의도한 원인 bucket으로 분류되는지 검증하기 위해 |

## 세부 변경

### 1. Hold advisor duration logging

`minority_report/hold_advisor.py`의 `_ask_one()`에서 Claude API 호출 전후 시간을 측정합니다.

저장 위치:

- raw call JSON의 `duration_ms`
- `agent_call_events.db`의 `duration_ms`
- vote dict의 `duration_ms`

추가로 `ask()` 전체 3-vote 수행 시간도 측정해 `logs/hold_advisor/decisions_YYYY-MM-DD.jsonl`에 저장합니다.

이제 이후 분석에서 다음 질문에 답할 수 있습니다.

- 호출 수 증가가 실제 지연 증가로 이어졌는가?
- bull/bear/neutral 중 특정 analyst가 느린가?
- symbol/reason/stage별 평균 latency가 다른가?
- TTL cache 적용 후 latency가 얼마나 줄었는가?

### 2. Candidate audit config fingerprint

`trading_bot.py`에 `_candidate_audit_config_fingerprint()`를 추가했습니다.

fingerprint 대상은 Claude/model/action routing/evidence/PathB/lesson/live review 관련 주요 runtime flag입니다.

audit row에 저장되는 필드:

- `config_hash`
- `feature_flags_json`

이 변경으로 같은 ticker라도 어떤 설정 상태에서 Claude selection과 route 결과가 만들어졌는지 구분할 수 있습니다.

### 3. Candidate audit execution decision linkage

v2 trade_ready registration 이후 `meta["v2_decision_ids"]`가 생성되면, 기존 candidate audit row에 `execution_decision_id`를 update하도록 했습니다.

저장되는 값:

- `execution_link_source = trading_bot.v2_register_trade_ready`
- `execution_decision_id = <v2 decision id>`

Path A buy_order 이벤트도 `_record_decision_event()` 호출 전에 v2 decision id를 확보해 event에 넘기도록 조정했습니다.

이 변경으로 이후 분석에서 candidate row와 실제 주문 decision을 같은 id로 추적할 수 있습니다.

### 4. Entry timing fallback snapshot

기존에는 명시적인 `entry_timing_snapshot`이 넘어온 경우에만 `entry_timing_snapshot_json`이 채워졌습니다.

이번 수정으로 buy event에 명시 snapshot이 없더라도 다음 최소 정보가 저장됩니다.

- `snapshot_source = record_decision_event_fallback`
- event timestamp
- session date
- market
- ticker
- action
- price native/KRW
- strategy
- selected reason

정밀한 delay 계산은 explicit timing snapshot이 있어야 가능하지만, fallback만 있어도 “언제 어떤 가격으로 어떤 전략이 주문됐는지”는 audit row에 남습니다.

## 의도적으로 변경하지 않은 것

| 항목 | 변경하지 않은 이유 |
|---|---|
| 30분 TTL cache | duration baseline이 쌓인 뒤 적용해야 비용/지연 개선 효과를 측정할 수 있음 |
| Haiku/R1 model tiering | live config 변경이므로 shadow/paper 비교와 운영자 승인 필요 |
| KR gate 완화 | watch_only blocked ratio가 높아도 trade_ready 성과가 약해 즉시 완화하면 손실 위험이 큼 |
| hard stop / broker truth / catastrophic exit 정책 | AI advisor가 override하면 안 되는 안전 영역 |
| live `.env` / `config/v2_start_config.json` | 이번 작업은 계측/감사 보강이며 운영 파라미터 변경이 아님 |

## 검증

실행한 검증:

```text
python -m py_compile minority_report/hold_advisor.py trading_bot.py audit/candidate_audit_store.py
python -m pytest tests/test_claude_quality_contracts.py tests/test_candidate_action_live_mapping.py -q
git diff --check -- minority_report/hold_advisor.py trading_bot.py tests/test_claude_quality_contracts.py tests/test_candidate_action_live_mapping.py docs/reports/claude_cost_quality_recheck_simulation_20260524.md
```

결과:

- `py_compile` 통과
- 관련 pytest `73 passed`
- `git diff --check` 통과
- pytest warning 2개는 기존 eventlet deprecation warning

## 기대 효과

이번 변경 후 새로 쌓이는 데이터로 다음 분석이 가능해집니다.

- hold advisor 호출 수 증가가 비용 문제인지, 지연 문제인지, 둘 다인지 분리
- TTL cache 적용 전후 latency와 call 절감률 비교
- selection 당시 config와 이후 outcome의 상관 분석
- Claude가 좋은 후보를 골랐는데 timing이 나빴는지, 또는 execution/risk gate에서 막혔는지 분리
- watch_only missed runup 원인을 evidence ceiling, routing demotion, PathB zone miss, entry timing, affordability/risk로 분해

## 다음 단계

권장 순서는 다음과 같습니다.

1. 새 로그로 hold advisor duration/call trend를 1~2영업일 관측
2. `duration_ms` 분포를 analyst, stage, market별로 집계
3. 가장 느린 analyst/stage와 반복 호출 reason을 TTL cache key 설계에 반영
4. KR watch_only blocked/missed-runup bucket decomposition report 작성
5. soft HOLD only 30분 TTL cache를 좁은 조건으로 적용
6. cache 적용 전후 call count, cost, p50/p95 duration 비교
7. model tiering은 Haiku shadow 결과를 본 뒤 별도 승인 하에 검토

Step 2의 기본 집계 축:

| 축 | 목적 |
|---|---|
| `analyst_type` | bull/bear/neutral 중 latency 병목 확인 |
| `decision_stage` | `TP_REVIEW`, `INTRADAY_REVIEW`, near-close 등 stage별 비용 확인 |
| `market` | KR/US 보유 구조 차이와 장중 review 패턴 분리 |
| `symbol` | 특정 종목 반복 review 또는 position age 문제 확인 |
| `review_reason` | TTL cache 적용 가능한 soft review와 cache 금지 reason 분리 |
| `duration_ms p50/p95` | 평균에 가려지는 tail latency 확인 |

## 추가 개선 반영

2026-05-25 추가 개선으로 분석 도구 2개를 보강했습니다.

### Hold advisor latency report

실행 예시:

```text
python tools/analyze_hold_advisor_latency.py --start-date 2026-05-21 --end-date 2026-05-23 --market ALL --format md --output docs/reports/hold_advisor_latency_20260521_20260523.md
```

이 도구는 다음을 집계합니다.

- single Claude call 기준 analyst별 p50/p95 duration
- 3-vote decision request 기준 stage/market/ticker별 p50/p95 duration
- `duration_ms` missing count
- 가장 느린 request/call 목록

현재 과거 로그에는 `duration_ms`가 비어 있을 수 있으므로, 이 도구의 첫 번째 용도는 “관측 필드가 제대로 차기 시작했는가”를 확인하는 것입니다.

### Watch-only bucket decomposition

`tools/analyze_candidate_audit.py` 결과에 `watch_only_bucket_decomposition`을 추가했습니다.

대표 bucket:

- `evidence_ceiling`
- `pathb_zone_or_plan`
- `risk_or_affordability`
- `routing_demotion`
- `strategy_no_signal`
- `claude_watch_conservative`
- `not_in_prompt`
- `claude_not_selected`

실행 예시:

```text
python tools/analyze_candidate_audit.py --date 2026-05-22 --market KR --horizon-min 60
```

이 결과를 먼저 보고 어떤 bucket이 큰지 확인한 뒤, 좁은 범위의 수정만 진행합니다. 전역 gate 완화는 여전히 금지합니다.
