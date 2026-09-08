# Pipeline Data Flow Gap Review 2026-07-23

## 목적

코드상 전략 판단이 완성돼 있는지보다, 실제 운영 데이터가 각 계층에 제대로 들어가고 쓰이는지 검증했다. 검증 방식은 DB의 실제 미매수 후보를 뽑아 `build_live_evidence_pack()` / `route_candidate_action()`에 원본 및 주입 데이터를 넣고, route 이후 `NO_SIGNAL` / `CLAUDE_PRICE_INVALID` 기록까지 추적하는 방식이다.

생성물:

- `tools/pipeline_case_injection.py`
- `docs/reports/pipeline_case_injection_20260723.md`
- `docs/reports/pipeline_case_injection_20260723.json`

## 결론

현재 핵심 문제는 “전략 로직 부재”보다 “선정/evidence에서 확보한 데이터가 실행 신호/PathB 등록 계층까지 일관되게 전달되지 않는 것”이다.

대표 케이스 20개 직접 주입 결과:

| 분류 | 건수 | 의미 |
|---|---:|---|
| `data_or_wiring_gap_recovers` | 8 | 데이터를 보강하거나 soft gate 상태를 정리하면 route가 열림 |
| `submit_layer_block_after_route_open` | 8 | route는 이미 열렸지만 submit/registration 계층에서 죽음 |
| `normal_price_cap_block` | 2 | 현재가가 entry cap 초과. cap 안쪽으로 넣으면 BUY route 열림 |
| `strategy_gate_holds` | 2 | negative pullback 등 명시 게이트가 주입 후에도 유지 |

즉 개선 우선순위는 route 함수 자체보다, `runtime_gate -> PlanA signal row`, `PathB raw_plan -> candidate payload`, `route reason attribution` 계약이다.

## 1. PlanA `NO_SIGNAL`: 데이터가 있는데 signal row로 안 들어간다

7월 이후 submit 차단 집계:

| market | reason | count |
|---|---:|---:|
| US | `NO_SIGNAL` | 77 |
| US | `CLAUDE_PRICE_INVALID` | 76 |
| KR | `CLAUDE_PRICE_INVALID` | 15 |

`NO_SIGNAL` 77건의 공통점:

- `no_submit_signal_flags_json.raw`의 `mom/gap/mr/vb`가 77/77 전부 null
- `no_submit_block_meta_json.volume_state='missing'`인데, 같은 row의 `payload_json.runtime_gate`에는 volume 관련 값이 대부분 존재
- runtime 데이터 존재율:
  - `volume_ratio_open`: 75/77
  - `volume_acceleration`: 75/77
  - `vwap_reclaim`: 75/77
  - `opening_range_break`: 75/77
  - `ret_3m_pct`: 76/77
  - `ret_5m_pct`: 76/77
  - `market_open_elapsed_min`: 76/77
  - `entry_window_bucket`: 76/77

대표 예:

- US VSAT `cand_31dae30e799596524304`
  - Claude/route: `BUY_READY -> PlanA.buy`
  - no-submit: `NO_SIGNAL`
  - runtime에는 `ret_3m=1.70`, `ret_5m=1.73`, `volume_ratio_open=2.04`, `vwap_reclaim=True` 존재
  - submit block에는 `volume_state=missing`, raw signal flags null

판정:

- 이것은 “Claude가 틀렸다” 또는 “전략이 너무 빡세다”로 바로 결론내릴 문제가 아니다.
- 먼저 PlanA 신호 판정 입력 row가 `runtime_gate/post_open_features` 값을 받아 쓰는지 확인해야 한다.
- 현재 기록만 보면 전략 함수가 판단할 row와 evidence row가 서로 다른 데이터 소스/스키마를 보고 있다.

개선 후보:

1. `_plan_a_signal_flags()`에 넘기는 `_no_signal_row`에 `volume_ratio_open`, `volume_acceleration`, `vwap_reclaim`, `ret_3m_pct`, `ret_5m_pct`, `opening_range_break`를 병합한다.
2. `NO_SIGNAL` 기록 시 `runtime_gate_signal_snapshot`을 같이 저장한다.
3. `volume_state=missing` 산정 시 runtime/evidence volume 값이 있으면 `missing`으로 기록하지 않도록 fallback 계약을 둔다.
4. `signal_flags.raw`가 전부 null이면 candidate audit에 `metadata_contract_violation=plan_a_signal_raw_empty`를 남긴다.

## 2. PathB `CLAUDE_PRICE_INVALID`: 가격계획은 들어오지만 payload 계약이 약하다

`CLAUDE_PRICE_INVALID`는 단순 누락이 아니라 `raw_plan`은 들어온다.

7월 이후 오류 분포:

| market/error | count |
|---|---:|
| US `reward_risk_below_minimum` | 65 |
| US `confidence_below_minimum` | 39 |
| KR `confidence_below_minimum` | 11 |
| KR `reward_risk_below_minimum` | 6 |

raw plan 분포:

- confidence: min 0.35 / avg 0.484 / max 0.68 / n 91
- reward_risk: min 1.75 / avg 3.062 / max 4.833 / n 84

대표 예:

- KR 010140 `cand_25622360dd24e63cb9fe`
  - route: `PULLBACK_WAIT -> PathB.wait`
  - no-submit: `CLAUDE_PRICE_INVALID`
  - raw_plan에는 `buy_zone_high/low`, `sell_target`, `stop_loss`, `hold_days`, `confidence=0.45` 존재
  - errors: `confidence_below_minimum`

판정:

- `confidence_below_minimum` 자체는 정상 차단일 수 있다.
- 하지만 사후 재현 계약은 약하다. `payload_json`에는 `action`/`route` 원본이 없고, 가격계획은 `no_submit_block_meta_json.raw_plan`에만 남는다.
- 따라서 “왜 PathB가 죽었는지”는 알 수 있지만, selection → route → PathB registration의 동일 객체 재생은 어렵다.

개선 후보:

1. candidate payload에 `action.price_targets` 또는 `pathb_raw_plan`을 항상 저장한다.
2. PathB registration 실패 시 `errors`, `validated_min_confidence`, `validated_min_reward_risk`, `raw_confidence`, `raw_reward_risk`를 별도 컬럼 또는 표준 JSON에 저장한다.
3. `confidence_below_minimum`이 반복되는 경우, Claude prompt에 최소 confidence/reward-risk 계약을 명시하고, 불충족 시 `PULLBACK_WAIT` 대신 `WATCH`로 응답하게 한다.

## 3. KR 가격상한 차단은 정상 동작

KR 403870, 055550 케이스는 원본에서 `entry_price_cap_exceeded`로 막혔다. 현재가를 cap 안쪽으로 주입하면 `BUY_READY/PlanA.buy`가 열린다.

판정:

- 이 계층은 데이터 흐름 문제로 보기 어렵다.
- 가격상한 초과 차단은 의도대로 동작한다.

개선 후보:

- 수정 대상 아님.
- 다만 리포트에서는 “막힌 이유가 정상 가격 차단”으로 분리 표시해야 한다.

## 4. KR action demotion 기록 불일치

KR 066980, 475150 케이스에서 다음 불일치가 확인됐다.

- `claude_action=BUY_READY`
- `runtime_gate.route_requested_action=PROBE_READY`
- 기록 route reason은 `kr_early_entry_buy_demoted_to_probe` 또는 `kr_late_fresh_buy_demoted_to_probe`
- 재생 중 실제 차단은 `entry_price_cap_exceeded`로 귀결

판정:

- demotion 자체는 전략상 있을 수 있다.
- 문제는 candidate audit에서 “Claude 원판단”, “시스템 demotion”, “최종 route 차단”이 한 줄에 명확히 분리되어 있지 않다는 점이다.

개선 후보:

1. `claude_action_original`, `system_requested_action`, `route_final_action`을 분리 저장한다.
2. demotion reason과 최종 block reason을 별도 필드로 저장한다.
3. `route_reason`과 `route_runtime_gate_reason` 중 하나가 비면 동일한 원인 분석에서 누락되므로 표준 reason resolver를 둔다.

## 5. Pullback evidence gate / negative context는 일부 정상 차단

KR 011230, 066980의 `PULLBACK_WAIT` 케이스는 데이터를 보강해도 `negative_pullback_context`가 유지됐다.

판정:

- 이 케이스는 단순 누락이 아니라, fade/deep pullback/negative context 계층이 의도대로 막은 것으로 본다.
- 다만 기록에는 `pullback_wait_evidence_gate`, 재생에는 `negative_pullback_context`가 나타나 reason attribution이 흔들린다.

개선 후보:

- `first_blocking_layer`, `final_blocking_layer`, `all_blocking_reasons`를 저장한다.
- evidence gate와 negative context가 동시에 존재하면 둘 중 하나만 덮어쓰지 않게 한다.

## 우선순위

P0:

1. PlanA no-submit raw signal row와 runtime/evidence data 병합 계약 보강
2. `NO_SIGNAL` 77건 중 runtime 데이터는 있는데 `volume_state=missing`인 케이스 재생 테스트 추가
3. candidate audit에 signal-row raw null 계약 위반 플래그 추가

P1:

1. PathB raw_plan을 `payload_json`에도 보존
2. `CLAUDE_PRICE_INVALID` 상세 원인 표준 필드화
3. Claude prompt에 PathB 최소 confidence/reward-risk 계약 명시

P2:

1. action demotion/route block reason 분리
2. `route_reason`, `route_runtime_gate_reason`, `no_submit_reason_code` 통합 resolver 추가
3. case injection harness를 회귀 테스트화

