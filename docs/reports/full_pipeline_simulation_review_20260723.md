# 전면 파이프라인 시뮬레이션 점검 리포트

작성일: 2026-07-23  
범위: 2026-07-01 이후 `audit_candidate_rows` 전체 재생 + `v2_canonical_performance` 체결 truth 대조  
산출물:

- `tools/pipeline_simulation_matrix.py`
- `docs/reports/pipeline_simulation_matrix_20260723.md`
- `docs/reports/pipeline_simulation_matrix_20260723.json`

## 1. 결론

운영자 지시인 “단편 분석이 아니라 다양한 종목 × 시나리오를 US/KR 전부 통과시켜 문제를 한 번에 도출” 방식이 맞다. 이번 하네스 실행으로 단일 원인이 아니라 5개 병목이 동시에 확인됐다.

1. 프롬프트 hard cap에서 후보가 대량 탈락한다.
2. 프롬프트에 들어간 뒤 Claude actionable 전환율이 극히 낮다.
3. 장초반 evidence confirmed가 구조적으로 0%라 early judge가 fail-closed/강등 쪽으로 기울 수 있다.
4. 후보 원장에는 `execution_decision_id`가 붙지만 `filled_count`, `first_fill_at`, `execution_event_id`가 갱신되지 않는다.
5. 일부 실제 체결은 candidate pipeline 밖의 별도 sleeve/factor/trend 경로에서 발생한다.

가장 중요한 발견은 4번이다. 기존 하네스가 `filled_count`만 보면 7월 체결이 0건처럼 보이지만, canonical truth에는 실제 체결이 존재한다. 따라서 후보 감사 원장만으로 “체결 없음”이라고 판단하면 잘못된 결론이 나온다.

## 2. 하네스 충실도

실행:

```text
python tools/pipeline_simulation_matrix.py --since 2026-07-01 --json-out docs/reports/pipeline_simulation_matrix_20260723.json --md-out docs/reports/pipeline_simulation_matrix_20260723.md
```

대상:

- 전체 rows: 90,100
- evidence replayable: 84,611
- route replayable: 90,100

재생 충실도:

| 항목 | 일치 | 불일치 | 일치율 |
| --- | ---: | ---: | ---: |
| evidence data_state | 32,124 | 56 | 99.83% |
| evidence action_ceiling | 32,124 | 56 | 99.83% |
| route final_action | 31,785 | 395 | 98.77% |
| route 문자열 | 31,730 | 450 | 98.60% |

판단:

- evidence 재생은 실측으로 취급 가능하다.
- route 재생도 98%대라 대량 매트릭스 분석에는 충분하다.
- route 불일치 top은 `HARD_BLOCK→WATCH`, `PULLBACK_WAIT→WATCH`가 대부분이다. 이건 시점 스냅샷 차이 또는 runtime context 변화 가능성이 높다.

## 3. 시장별 통과 매트릭스

### US

| 단계 | count | 직전 대비 생존 |
| --- | ---: | ---: |
| candidate | 66,550 | 100.00% |
| prompt | 25,881 | 38.89% |
| actionable | 599 | 2.31% |
| evidence_pass | 591 | 98.66% |
| route_pass | 494 | 83.59% |
| entry_wiring | 494 | 100.00% |
| safety_submit | 427 | 86.44% |
| candidate_audit_fill | 0 | 0.00% |
| canonical_fill_fallback | 12 row | n/a |

주요 차단:

- prompt: `hard_cap_cutoff` 37,141
- actionable: `WATCH` 21,322, `no_action` 3,696, `AVOID` 264
- route: `soft_block_floor:late_mover`, `inside_buy_zone`, `repeated_failed_ready`, `pullback_wait_evidence_gate`
- safety: `NO_SIGNAL` 67
- audit fill: safety 통과 427 row 전부 `filled_count=0`

canonical truth:

- canonical filled unique: 4
- canonical closed unique: 3
- canonical decision id가 후보 row 어딘가에 보이는 체결: 2
- executable route row까지 연결되는 체결: 1
- candidate audit fill rows: 0
- candidate audit execution_decision_rows: 462
- candidate audit execution_event_rows: 0

해석:

- US는 실제 체결 일부가 후보 원장에 `execution_decision_id`로는 걸리지만 fill 결과가 backfill되지 않는다.
- SCHG trend sleeve처럼 candidate pipeline 밖에서 발생한 체결도 있다.
- IREN은 PathB row와 canonical decision id가 이어지지만 fill_count가 0이다.
- NVDA는 canonical decision id가 후보 row에 보이지만, 체결 시점 row가 WATCH 상태라 executable attribution이 약하다.

### KR

| 단계 | count | 직전 대비 생존 |
| --- | ---: | ---: |
| candidate | 23,550 | 100.00% |
| prompt | 13,421 | 56.99% |
| actionable | 222 | 1.65% |
| evidence_pass | 214 | 96.40% |
| route_pass | 39 | 18.22% |
| entry_wiring | 39 | 100.00% |
| safety_submit | 39 | 100.00% |
| candidate_audit_fill | 0 | 0.00% |
| canonical_fill_fallback | 1 row | n/a |

주요 차단:

- prompt: `hard_cap_cutoff` 8,087
- actionable: `WATCH` 9,347, `no_action` 3,507, `AVOID` 345
- route: `WATCH:-` 124, `entry_price_cap_exceeded` 13, `inside_buy_zone` 7, `pullback_wait_evidence_gate` 7, `kr_fast_trigger_not_confirmed` 6
- audit fill: safety 통과 39 row 전부 `filled_count=0`

canonical truth:

- canonical filled unique: 3
- canonical closed unique: 1
- canonical decision id가 후보 row 어딘가에 보이는 체결: 1
- executable route row까지 연결되는 체결: 1
- candidate audit fill rows: 0
- candidate audit execution_decision_rows: 38
- candidate audit execution_event_rows: 0

해석:

- KR 003490은 PathB row와 canonical decision id가 이어지지만 fill_count가 0이다.
- 275280/275300은 factor trend sleeve 성격으로 candidate pipeline 밖 체결이다.
- KR은 route 단계에서 US보다 더 크게 죽는다. actionable 222 → route_pass 39, 생존 18.22%.

## 4. 결측 반사실

### US

강등 대상: 7,528 row

주요 결측:

- `opening_range_break`: 3,460
- `vwap_distance_pct`: 3,251
- `volume_ratio_open`: 3,211
- `ret_3m_pct`: 1,894
- `ret_5m_pct`: 1,894

필드 보강 시 BUY_READY 회복:

| 보강 시나리오 | 회복 row | 비율 |
| --- | ---: | ---: |
| volume_ratio_open만 채움 | 24 | 0.3% |
| opening_range_break만 채움 | 1,313 | 17.4% |
| vwap_distance_pct만 채움 | 0 | 0.0% |
| 확인 3필드 전부 채움 | 1,337 | 17.8% |
| time_normalized_rvol 대체 | 19 / 3,704 | 0.5% |

판단:

- US에서 회복량이 큰 필드는 사실상 `opening_range_break`다.
- volume 또는 time-normalized rvol 대체는 거의 효과가 없다.
- vwap 단독 보강도 회복량 0이다.

### KR

강등 대상: 7,118 row

주요 결측:

- `opening_range_break`: 2,443
- `volume_ratio_open`: 2,218
- `vwap_distance_pct`: 2,217
- `ret_3m_pct`: 1,697
- `ret_5m_pct`: 1,697
- `current_price`: 591

필드 보강 시 BUY_READY 회복:

| 보강 시나리오 | 회복 row | 비율 |
| --- | ---: | ---: |
| volume_ratio_open만 채움 | 8 | 0.1% |
| opening_range_break만 채움 | 502 | 7.1% |
| vwap_distance_pct만 채움 | 0 | 0.0% |
| 확인 3필드 전부 채움 | 522 | 7.3% |
| time_normalized_rvol 대체 | 3 / 3,547 | 0.1% |

판단:

- KR도 핵심 결측은 `opening_range_break`다.
- 다만 회복률은 US보다 작다.
- KR은 field 결측보다 route gate 강등 쪽 병목이 더 크다.

## 5. 판정 시점 문제

### US timing

| 구간 | n | confirmed | ORB 결측 | volume 결측 | VWAP 결측 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0~15분 | 5,159 | 0.0% | 82.8% | 72.7% | 75.1% |
| 15~30분 | 893 | 27.5% | 51.4% | 72.5% | 71.9% |
| 30~60분 | 2,385 | 35.1% | 36.6% | 47.0% | 46.1% |
| 1~3시간 | 12,259 | 35.1% | 41.3% | 55.2% | 55.2% |
| 3시간+ | 11,624 | 39.5% | 38.7% | 53.4% | 53.4% |

### KR timing

| 구간 | n | confirmed | ORB 결측 | volume 결측 | VWAP 결측 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0~15분 | 1,603 | 0.0% | 86.5% | 68.2% | 68.4% |
| 15~30분 | 555 | 54.4% | 24.3% | 39.6% | 39.6% |
| 30~60분 | 340 | 69.4% | 20.0% | 28.8% | 28.2% |
| 1~3시간 | 1,550 | 67.3% | 18.6% | 28.3% | 28.3% |
| 3시간+ | 2,982 | 51.4% | 31.2% | 42.5% | 42.5% |

판단:

- 0~15분은 US/KR 모두 evidence confirmed 0%다.
- 이 구간의 fail-closed는 데이터 결함보다 타이밍 구조 문제다.
- 장초반 judge가 필요하면 `opening_range_break` 요구를 완화하거나, early 전용 confirmation 계약을 분리해야 한다.
- 하지만 바로 완화하면 chase/false positive가 늘 수 있으므로 shadow로 분리해야 한다.

## 6. route 반사실

### US

Claude `BUY_READY`: 386 row

route 귀결:

- `PlanA.buy`: 347
- `missing`: 35
- `PlanA.probe`: 4

BUY_READY ceiling 강제 시:

- PlanA.buy 전환: 13 / 39

해석:

- US는 BUY_READY 대부분이 이미 PlanA.buy로 간다.
- 병목은 BUY_READY가 route에서 막히는 것보다, 그 이후 NO_SIGNAL/미체결/실행 attribution이다.
- `PlanA.probe`는 4건으로 작지만, probe가 실제 주문 경로와 연결되는지는 별도 확인 대상이다.

### KR

Claude `BUY_READY`: 92 row

route 귀결:

- `missing`: 82
- `PlanA.buy`: 10

BUY_READY ceiling 강제 시:

- PlanA.buy 전환: 8 / 82

해석:

- KR은 BUY_READY가 route로 제대로 이어지지 않는 비율이 매우 높다.
- 이전 분석의 “KR 과차단/route demotion 점검 필요”와 일치한다.
- KR은 evidence보다 route/gate 해석 누수가 더 핵심이다.

## 7. 발견된 문제 리스트

### P0-1. candidate audit fill attribution 누수

증상:

- 7월 `candidate_audit_fill_rows`가 US/KR 모두 0.
- 하지만 canonical truth에는 US filled unique 4, KR filled unique 3이 존재.
- 후보 원장에는 `execution_decision_id`가 US 462 row, KR 38 row 붙어 있음.
- `execution_event_id`는 US/KR 모두 0.

영향:

- candidate funnel이 실제 체결 성과와 분리된다.
- 하네스/대시보드가 “체결 0”으로 오판한다.
- 후보→체결→손익 학습 루프가 끊긴다.

우선 조치:

- `execution_decision_id` 기준으로 `v2_canonical_performance` 또는 lifecycle fill event를 후보 원장에 backfill하는 sync 보강.
- 최소 필드: `filled_count`, `first_fill_at`, `execution_event_id`, `entry_price`, `exit_price`, `pnl_pct`.
- `AMBIGUOUS`인 경우에도 decision id exact match가 있으면 후보 audit용으로는 `candidate_audit_fill_source=canonical_exact_decision_id`를 남기는 방식 검토.

### P0-2. PathB 상태 전이 attribution 약화

증상:

- IREN/KR 003490은 PathB row에 execution id가 연결된다.
- NVDA는 canonical fill decision id가 후보 row에는 보이나, executable route row가 아니라 WATCH row에 붙어 있다.

영향:

- PathB wait 등록 후 fill까지의 lifecycle이 후보 action과 분리된다.
- “어떤 Claude/action/route가 실제 체결을 만들었는가”가 흐려진다.

우선 조치:

- PathB wait 등록 시점의 candidate_key를 path_run_id/decision_id와 함께 고정.
- fill event 발생 시 가장 최근 WATCH row가 아니라 최초 executable PathB row로 귀속.
- 동일 ticker/session 중복 후보는 `execution_decision_id + path_run_id + first_executable_candidate_key`를 primary attribution으로 사용.

### P1-1. early evidence confirmed 0%

증상:

- 0~15분 confirmed가 US/KR 모두 0%.
- ORB 결측이 US 82.8%, KR 86.5%.

영향:

- 장초반 judge는 구조적으로 evidence partial/missing을 만난다.
- early opportunity를 잡으려면 기존 confirmation 계약과 충돌한다.

우선 조치:

- early 전용 evidence state를 분리한다.
  - 예: `early_confirmed_without_or`
  - 필수: current_price, ret_3m/5m 중 하나, spread/liquidity, chase cap
  - ORB는 hard requirement가 아니라 later confirmation으로 이동
- 단, live 완화 금지. shadow로 회복 후보 forward 성과 확인.

### P1-2. KR route demotion/route missing

증상:

- KR BUY_READY 92 row 중 route missing 82.
- actionable 222 → route_pass 39, 생존 18.22%.

영향:

- KR에서 Claude가 적극 판단한 후보가 실제 매수 경로로 거의 연결되지 않는다.
- 이전 리포트의 `BUY_READY → WATCH` 과차단 의심과 일치한다.

우선 조치:

- KR `BUY_READY/PULLBACK_WAIT`가 route missing/WATCH가 된 row를 reason별로 별도 리포트.
- `entry_price_cap_exceeded`, `kr_fast_trigger_not_confirmed`, `pullback_wait_evidence_gate`, `WATCH:-`를 분리.
- `WATCH:-`처럼 사유 없는 강등은 P0 관측성 결함으로 처리.

### P1-3. prompt hard cap 과다 탈락

증상:

- US hard_cap_cutoff 37,141
- KR hard_cap_cutoff 8,087

영향:

- 후보 생성은 충분하지만 prompt 진입 전 대량 탈락.
- 다만 탈락 자체가 나쁜 것은 아니며, 탈락 후보 forward 성과와 연결해야 판단 가능하다.

우선 조치:

- hard_cap_cutoff 그룹의 ret30/ret60/close forward를 prompt 포함 그룹과 비교.
- 특히 KR은 이전 분석에서 cut된 쪽이 더 좋았다는 힌트가 있으므로 별도 검증 필요.

### P2. missing field 보강은 ORB 중심

증상:

- ORB 하나만 채워도 US 1,313 row, KR 502 row가 BUY_READY로 회복.
- volume/vwap 단독 보강은 거의 효과 없음.

해석:

- confirmation field 중 병목은 volume/vwap이 아니라 ORB.
- `time_normalized_rvol`을 `volume_ratio_open` 대체로 쓰는 것은 효과가 거의 없다.

우선 조치:

- ORB가 아직 형성될 수 없는 시점과 진짜 결측을 분리.
- ORB unavailable 상태를 missing과 다르게 취급.

## 8. 다음 작업 순서

1. candidate audit fill sync 보강
   - 후보 원장 체결 0 문제를 먼저 해결해야 이후 하네스가 성과까지 추적 가능하다.

2. PathB attribution 고정
   - `first_executable_candidate_key`를 decision/path_run에 묶는다.

3. early evidence state 분리
   - 0~15분용 shadow 계약을 만들고 forward 성과를 본다.

4. KR route demotion reason audit
   - `WATCH:-`와 route missing을 먼저 없앤다.

5. prompt hard cap 탈락 후보 성과 분석
   - US/KR 별로 hard cap이 좋은 후보를 잘라내는지 확인한다.

## 9. 현재 판단

전면 파이프라인 시뮬레이션 방식은 효과가 있다. 이번 한 번의 실행으로 실제 실전에서만 보이던 누수가 구조적으로 드러났다.

가장 먼저 고칠 것은 매수 로직이 아니라 관측/귀속이다.

```text
candidate -> executable route -> decision_id/path_run_id -> fill event -> canonical PnL
```

이 축이 연결되지 않으면, 어떤 후보가 돈을 벌었는지/잃었는지 학습할 수 없다. 현재는 `execution_decision_id`까지만 일부 붙고 fill/event가 끊겨 있다.

따라서 다음 개선의 P0는 “더 사게 만드는 것”이 아니라 “체결 truth를 후보 원장으로 되돌리는 것”이다.
