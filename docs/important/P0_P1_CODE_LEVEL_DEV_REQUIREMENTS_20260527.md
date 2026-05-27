# P0/P1 Code-Level Development Requirements

Updated: 2026-05-27

이 문서는 삭제된 plan/report에서 살아남은 P0/P1 작업을 코드 레벨 개발 요구서로 재정리한 것이다. 우선순위 원장은 [ACTIVE_WORK.md](ACTIVE_WORK.md)를 따르며, 이 문서는 개발자가 바로 구현 범위와 테스트 범위를 잡을 수 있도록 `왜 하는지`, `무엇을 개선하는지`, `개선 전/후`, `대상 코드`, `수용 기준`을 상세화한다.

## 공통 원칙

- live order sizing, PathB live gate, max positions, daily entry cap, confidence, slippage cap, protective hold distance, hard stop, broker truth 우선순위는 변경하지 않는다.
- 수익성 P0 작업은 live ranking, prompt cap, `trade_ready`, 주문 생성, stop/exit rule을 직접 바꾸지 않는다. audit/report/shadow/replay를 먼저 보강한다.
- 운영/버그 P0 작업은 broker/order fail-closed behavior를 약화하지 않는다.
- DB/분석 작업은 `state/brain.json`을 runtime truth로 쓰지 않는다.
- 기존 runtime 산출물, DB, state 파일은 개발 요구서 구현 커밋에 포함하지 않는다.

## P0 Summary

| ID | 카테고리 | 요구사항 | 왜 하는가 | 개선 전 | 개선 후 |
| --- | --- | --- | --- | --- | --- |
| P0-1 | 수익성 | actual prompt profit visibility 검증 | 좋은 후보가 Claude 전에 누락되는지, 분석이 잘못 보는지 구분해야 한다. | `input_to_claude`/timestamp join 기반으로 prompt 포함 여부가 흔들림. | `actual_prompt_*`와 `selection_trace_id` 기준으로 included/missing 성과를 분리. |
| P0-2 | 데이터베이스 | candidate bucket/source/score 품질 전파 | 수익성 저하 원인이 ranker, source, price, execution 중 어디인지 분리해야 한다. | blank bucket/source와 broad `INVALID_PRICE`가 원인을 숨김. | audit/outcome에서 bucket/source/data-quality/score/reason이 query 가능. |
| P0-3 | 수익성 | KR entry/exit shadow instrumentation | first-entry/exit overlay 가설을 샘플 부족 상태에서 live로 바꾸지 않기 위해서다. | 소수 샘플/특정 일자 winner에 과적합 가능. | broker-fill-aware replay와 MFE/MAE/OR/VWAP 근거로 판단. |
| P0-4 | 운영 | KIS token `EGW00133` rate-limit/backoff | token 발급 제한이 live 데이터/주문 경로를 끊거나 refresh storm을 만들 수 있다. | rate-limit와 credential failure가 섞임. | backoff/lock으로 throttle하고 credential failure는 별도 fail-closed. |
| P0-5 | 버그 | broker-truth zero-holding fixture | stale local position 제거는 destructive 작업이므로 row shape mismatch를 막아야 한다. | 실제 KIS row 변형에 취약. | fresh zero/no-open만 제거되고 unsafe case는 fail closed. |
| P0-6 | 운영 | PathB entry broker-truth gate visibility | PathB live entry block을 log scraping 없이 진단해야 한다. | block 원인이 operator에게 보이지 않음. | preflight/ops에서 TTL, attempt, latency, error, block reason 확인. |
| P0-7 | 버그 | PathB pending-buy TTL/order matching | wrong cancel/fill 복구는 live 주문 생명주기 오류다. | plan age 또는 same-ticker fallback으로 잘못 판단 가능. | sent/ACK timestamp와 exact `order_no` 기준 판단. |

## P1 Summary

| ID | 카테고리 | 요구사항 | 왜 하는가 | 개선 전 | 개선 후 |
| --- | --- | --- | --- | --- | --- |
| P1-1 | 버그 | US PathB sizing context/reason split | `qty=0` 차단 원인을 operationally actionable하게 분리해야 한다. | `INVALID_QTY`로 뭉쳐 high price와 early gate 축소가 구분되지 않음. | order policy는 유지하고 reason/payload만 세분화. |
| P1-2 | 데이터베이스 | V2 canonical freshness/fallback exclusion | stale truth와 timeout fallback이 성과/학습에 섞이면 이후 판단이 오염된다. | canonical freshness와 fallback exclusion이 operator-visible하지 않음. | freshness warning과 `learning_excluded` aggregate exclusion이 보장됨. |
| P1-3 | 운영 | Brain/sub-screener guard visibility | hidden trigger와 automatic memory write를 막아야 한다. | scoped trigger/brain write가 운영자에게 불명확. | trigger precedence/counter와 direct-write prevention이 test됨. |
| P1-4 | 버그 | runtime tuning override cleanup | non-tuning field가 override state에 남으면 prompt/debug 분석이 흐려진다. | `action`, `mode`, `reason`, `warning` 등이 payload에 잔존 가능. | bounded numeric adjustment key만 persist. |
| P1-5 | 수익성 | raw-score shadow / multi-source consensus | missing 후보가 좋은 후보인지 source noise인지 구분해야 한다. | raw top30 missing과 source disagreement가 성과와 연결되지 않음. | labeled shadow outcome으로 added/excluded/source overlap 비교. |
| P1-6 | 운영 | PathB fill truth / sell pending / EXPIRED monitoring | partial fill/remainder/expired plan은 PnL truth와 보호 청산에 직접 연결된다. | unresolved lifecycle이 local inference에 기대는 위험. | broker truth 기반 full/partial/cancel/remainder/expired 상태가 visible. |

---

## P0-1. Actual Prompt Profit Visibility Verification

**왜 하는가**

수익성 개선은 좋은 후보가 어디에서 사라지는지 알아야 가능하다. 현재 분석이 old `input_to_claude`나 timestamp-nearest join에 기대면, 실제 Claude prompt에 들어간 후보와 분석상 들어간 후보가 다를 수 있다. 이 상태에서 prompt cap, ranker, overlay를 바꾸면 원인 오판으로 이어진다.

**대상 코드**

- `trading_bot.py`: selection/audit call metadata, actual prompt ticker/rank 저장 경로.
- `audit/candidate_audit_store.py`: `audit_candidate_rows`, `audit_claude_calls` schema/upsert.
- `bot/screener_quality.py`: final prompt/input flag 계산.
- `tools/analyze_candidate_audit.py`: measured/unmeasured, mismatch, included/missing outcome 분석.
- `tools/analyze_prompt_overlay_shadow.py`: old prompt flag 대신 actual prompt flag 우선 사용.
- 관련 tests: `tests/test_candidate_audit.py`, `tests/test_screener_quality.py`, `tests/test_candidate_action_live_mapping.py`.

**개선 전**

- `input_to_claude`가 내부 trim 이후 실제 prompt와 불일치할 수 있다.
- prompt 포함 여부가 timestamp join에 의존하면 같은 사이클/다른 사이클 후보가 섞인다.
- raw top30 missing, trainer top30 missing, prompt included/missing 성과가 한 표에서 섞인다.

**구현 요구**

1. `visibility_contract_version="actual_prompt_v1"` rows는 `selection_trace_id`, `actual_prompt_call_id`, `actual_prompt_included`, `actual_prompt_rank`가 비어 있으면 안 된다.
2. report는 `actual_prompt_included`를 우선 사용하고, 없을 때만 legacy `input_to_claude`를 별도 legacy bucket으로 표시한다.
3. `actual_prompt_mismatch`, `legacy_input_reported_mismatch`, `actual_prompt_unmeasured`를 서로 다른 section/count로 출력한다.
4. included/missing 성과는 최소 30m/60m return, row count, market, session date, source DB를 함께 표시한다.
5. raw top30 missing과 trainer top30 missing은 prompt missing과 구분한다.

**개선 후**

- 최신 KR/US cycle에서 실제 prompt 포함 후보와 제외 후보의 forward outcome을 직접 비교할 수 있다.
- prompt cap/ranker/overlay 개선 판단이 legacy report 오류가 아닌 실제 missing evidence에 기반한다.

**수용 기준**

- 최신 KR/US DB에서 `actual_prompt_v1` measured rows가 0보다 크다.
- `actual_prompt_v1` rows 중 `selection_trace_id` missing count가 0이거나 원인 문서화됨.
- old `input_to_claude` mismatch가 report에서 별도 count로 노출된다.

**검증**

```powershell
python -m pytest tests/test_candidate_audit.py tests/test_screener_quality.py tests/test_candidate_action_live_mapping.py -q
python tools/analyze_candidate_audit.py --db data/audit/candidate_audit.db --market ALL --json
```

## P0-2. Candidate Bucket/Source/Score Data Quality

**왜 하는가**

후보 품질 문제와 execution/risk 문제를 섞으면 잘못된 수정이 나온다. 예를 들어 `INVALID_PRICE`가 price provider 문제인지 unit normalization 문제인지, source fallback 문제인지, 실제로 high-price budget block인지 구분되어야 한다.

**대상 코드**

- `bot/screener_quality.py`: `primary_bucket`, secondary buckets, source tags, data-quality flags 생성/보존.
- `trading_bot.py`: candidate action, ready, PathB, execution audit payload 전파.
- `audit/candidate_audit_store.py`: structured JSON field 저장.
- `tools/analyze_candidate_audit.py`: blank bucket/source rate, invalid-price reason, source-quality grouping.
- `tools/build_claude_decision_facts.py`: downstream fact table field 유지.
- 관련 tests: `tests/test_candidate_audit.py`, `tests/test_screener_quality.py`, `tests/test_dashboard_candidate_audit_api.py`.

**개선 전**

- `primary_bucket=''` 또는 `unclassified`가 많으면 trainer/ranker 판단 근거가 사라진다.
- `INVALID_PRICE` 단일 reason은 missing quote, stale quote, non-positive price, unit issue, provider failure를 구분하지 못한다.
- degraded/FMP/Yahoo/KIS/Nasdaq-derived source가 outcome aggregation에서 섞인다.

**구현 요구**

1. audit row에 최소 `primary_bucket`, `secondary_buckets_json`, `source_tags_json`, `data_quality_flags_json`, `raw_score_current`, `raw_score_components_json`, `trainer_score_rank`, `trainer_score_components_json`을 보존한다.
2. `INVALID_PRICE` broad reason은 analyzer에서 다음 bucket으로 분리한다: `legacy_price_unmeasured`, `missing_quote`, `stale_quote`, `non_positive_price`, `unit_normalization_issue`, `provider_failure`, `unknown_price_issue`.
3. blank bucket/source rate를 market/source/date 기준으로 출력한다.
4. degraded/FMP fallback 후보는 차단하지 않고 source-quality tag로 outcome grouping 가능하게 한다.
5. 이 작업은 live ranker, prompt cap, order/risk behavior를 변경하지 않는다.

**개선 후**

- 후보 누락/차단 원인을 ranker 품질, source 품질, price-data 품질, affordability/risk 문제로 분리할 수 있다.
- raw top30 missing이 정말 좋은 후보였는지, source-quality가 낮아 제외된 후보였는지 판단 가능하다.

**수용 기준**

- 최근 measured rows의 blank `primary_bucket` rate가 10% 미만이거나 source-specific gap으로 문서화된다.
- `INVALID_PRICE` report가 concrete reason count와 sample rows를 출력한다.
- dashboard/API가 source-quality grouping을 깨지 않는다.

**검증**

```powershell
python -m pytest tests/test_candidate_audit.py tests/test_screener_quality.py tests/test_dashboard_candidate_audit_api.py -q
python tools/analyze_candidate_audit.py --db data/audit/candidate_audit.db --market ALL --json
```

## P0-3. KR Entry/Exit Profit Shadow Instrumentation

**왜 하는가**

KR first-entry discipline, OR/VWAP/volume confirmation, 1.2-1.5% loss cap, MFE preservation은 수익성 개선 가능성이 있지만 표본 부족 상태에서 live rule로 바꾸면 위험하다. 먼저 broker-fill-aware replay가 필요하다.

**대상 코드**

- `trading_bot.py`: entry sequence, market phase, bucket/source, route/action metadata.
- `runtime/pathb_runtime.py`: PathB fill/entry timing metadata 보존.
- `tools/collect_counterfactual_minutes.py`, `tools/run_counterfactual_pipeline.py`, `tools/analyze_kr_confirmation_gate.py`: minute/outcome/replay pipeline.
- `data/audit/candidate_audit.db`, `data/ml/decisions.db`, V2 lifecycle DB: outcome join source.
- 관련 tests: `tests/test_collect_counterfactual_minutes.py`, `tests/test_counterfactual_pipeline_scheduler.py`, `tests/test_analyze_kr_confirmation_gate.py`.

**개선 전**

- 첫 진입/두 번째 진입/장중 late entry 성과가 명확히 분리되지 않는다.
- OR/VWAP/volume confirmation 여부가 entry decision과 outcome에 붙지 않는다.
- MFE/MAE와 cap-hit replay가 broker fill 기준으로 연결되지 않는다.

**구현 요구**

1. filled entry에 `entry_sequence_of_day`, `entry_market_phase`, `route`, `origin_action`, `primary_bucket`, `source_tags`를 붙인다.
2. 가능한 경우 OR break, VWAP reclaim, volume ratio, relative strength, minute completeness를 shadow field로 기록한다.
3. MFE/MAE, 30m/60m/close return, cap-hit 여부, MFE preservation replay result를 report로 생성한다.
4. first1/first2 비교와 exit cap replay는 market/date/top-day concentration을 반드시 포함한다.
5. live stop, exit overlay, PathB sizing, order amount는 변경하지 않는다.

**개선 후**

- KR first-entry/exit overlay 가설을 실제 fill 기준으로 검토할 수 있다.
- 단일 winner day가 전체 수익성을 끌어올린 착시를 걸러낼 수 있다.

**수용 기준**

- 30 filled trades 또는 4 calendar weeks 데이터가 쌓일 때 review 가능한 report schema가 준비된다.
- top-day contribution이 report에 표시된다.
- replay는 broker fill price/time 없이 live promotion 근거로 사용되지 않는다.

**검증**

```powershell
python -m pytest tests/test_collect_counterfactual_minutes.py tests/test_counterfactual_pipeline_scheduler.py tests/test_analyze_kr_confirmation_gate.py -q
python tools/run_counterfactual_pipeline.py --dry-run --market KR
```

## P0-4. KIS Token `EGW00133` Rate-Limit Classification And Backoff

**왜 하는가**

KIS token issuance pressure는 운영 장애지만 credential 오류와 같은 방식으로 처리하면 복구 판단이 틀어진다. shared KR/US credential에서 force refresh가 중복되면 live data/order path를 더 오래 막을 수 있다.

**대상 코드**

- `kis_api.py`: token request, `_kis_get/_kis_post` retry, token cache context, force refresh path.
- `trading_bot.py`: startup token refresh, market token helper, balance retry.
- `runtime/broker_truth_snapshot.py`: token provider use.
- 관련 tests: `tests/test_startup_token_refresh.py`, `tests/test_kis_kr_order_safety.py`, `tests/test_pathb_runtime.py`.

**개선 전**

- `EGW00133`이 generic auth/credential failure처럼 보일 수 있다.
- KR/US가 같은 credential을 공유할 때 중복 force refresh storm이 발생할 수 있다.
- backoff 상태가 operator-visible하지 않다.

**구현 요구**

1. KIS response body/error code에서 `EGW00133`을 rate-limit/backoff class로 분류한다.
2. credential/config failure와 rate-limit failure의 exception/status code를 구분한다.
3. shared credential force refresh는 lock 또는 in-memory cooldown으로 중복 발급을 방지한다.
4. cached token이 유효하면 rate-limit 중에도 불필요한 refresh를 시도하지 않는다.
5. token recovery 실패 시 broker/order path는 fail-closed를 유지한다.

**개선 후**

- token issuance pressure는 throttled 상태로 보이고, 실제 credential 장애와 분리된다.
- live entry가 token failure를 우회하지 않고 fail-closed한다.

**수용 기준**

- `EGW00133` test에서 backoff/cooldown path가 호출된다.
- auth/config error test는 retryable rate-limit로 오분류되지 않는다.
- duplicate force refresh test에서 두 번째 호출은 refresh를 즉시 반복하지 않는다.

**검증**

```powershell
python -m pytest tests/test_startup_token_refresh.py tests/test_kis_kr_order_safety.py tests/test_pathb_runtime.py -q
python -m py_compile kis_api.py trading_bot.py runtime/broker_truth_snapshot.py
```

## P0-5. Broker-Truth Zero-Holding Fixture Tests

**왜 하는가**

sell insufficient holding 이후 local stale position을 지우는 작업은 destructive reconcile이다. broker truth가 fresh zero holding이고 open remaining quantity가 0이라는 증거 없이는 local state를 지우면 안 된다.

**대상 코드**

- `trading_bot.py`: `TradingBot._sell_zero_holding_broker_evidence()`, `_remove_local_position_after_zero_holding()`.
- `runtime/pathb_runtime.py`: `PathBRuntime._pathb_zero_holding_broker_evidence()`, zero-holding sell precheck handling.
- `runtime/broker_truth_snapshot.py`: holdings/open orders/today fills snapshot parser.
- 관련 tests: `tests/test_broker_truth_snapshot.py`, `tests/test_pathb_runtime.py`, `tests/test_pre_session_sell_queue.py`.

**개선 전**

- 실제 KIS KR/US row field variation이 충분히 고정되어 있지 않다.
- open order remaining quantity나 ticker key mismatch가 있으면 stale local cleanup이 위험하다.
- fill-only evidence를 zero holding truth로 오해할 수 있다.

**구현 요구**

1. KR fixture: position row, open order row, today fill row의 `pdno`, qty, remaining qty, side field variation을 포함한다.
2. US fixture: `ovrs_pdno`, symbol casing, exchange, remaining qty, partial fill/cancel variation을 포함한다.
3. fresh zero holding + no open remaining -> safe reconcile true.
4. stale broker truth, broker error, missing snapshot, unrecognized ticker, open remaining, sell-fill-only evidence -> safe reconcile false.
5. test는 live broker API를 호출하지 않는다.

**개선 후**

- destructive local cleanup은 fresh broker truth evidence가 있을 때만 동작한다.
- parser 변경이 필요한 경우 fixture failure로 드러난다.

**수용 기준**

- KR/US safe case 각각 통과.
- KR/US unsafe case가 모두 fail closed.
- manual reconciliation required flag가 unsafe case에 남는다.

**검증**

```powershell
python -m pytest tests/test_broker_truth_snapshot.py tests/test_pathb_runtime.py tests/test_pre_session_sell_queue.py -q
python -m py_compile trading_bot.py runtime/pathb_runtime.py runtime/broker_truth_snapshot.py
```

## P0-6. PathB Entry Broker-Truth Gate Ops Visibility

**왜 하는가**

PathB KR/US live entry는 broker truth gate가 막을 수 있다. block 이유가 로그에만 있으면 operator가 protected condition을 config 변경으로 우회하려 할 수 있다.

**대상 코드**

- `runtime/pathb_runtime.py`: `_entry_scan_broker_truth_gate()`, `_audit_entry_scan_blocked()`, block log payload.
- `tools/live_preflight.py`: preflight output.
- `interface/v2_ops_summary.py` 또는 dashboard ops endpoint: operator-visible state.
- 관련 tests: `tests/test_pathb_runtime.py`, `tests/test_live_config_sources.py`, `tests/test_dashboard_pathb.py`.

**개선 전**

- gate allowed/blocked 결과 외에 TTL, attempts, last success/failure, latency가 보이지 않는다.
- paper skip이 success처럼 보일 수 있다.
- token/provider unavailable과 stale truth block이 구분되지 않는다.

**구현 요구**

1. gate state payload에 `enabled`, `ttl_sec`, `min_interval_sec`, `last_attempt_at`, `last_success_at`, `last_failure_at`, `latency_ms`, `last_error`, `block_reason`, `paper_skip`을 포함한다.
2. preflight JSON 또는 ops summary에 위 payload를 market별로 노출한다.
3. paper mode skip은 `skipped`로 표시하고 `success`로 기록하지 않는다.
4. live refresh failure는 entry fail-closed를 유지한다.
5. token backoff item과 섞지 않는다. 이 항목은 visibility이고, token item은 issuance pressure classification이다.

**개선 후**

- PathB entry block을 operator가 로그 없이 진단할 수 있다.
- protected condition을 config 변경으로 우회할 가능성이 줄어든다.

**수용 기준**

- stale broker truth, refresh failure, paper skip, success case가 각각 다른 status로 표시된다.
- preflight JSON에서 market별 block reason이 확인된다.

**검증**

```powershell
python -m pytest tests/test_pathb_runtime.py tests/test_live_config_sources.py tests/test_dashboard_pathb.py -q
python tools/live_preflight.py --mode paper --skip-dashboard --json
```

## P0-7. PathB Pending-Buy TTL And Order Matching

**왜 하는가**

PathB pending buy TTL은 실제 주문 전송/ACK 이후부터 계산되어야 한다. plan 생성 시각이 오래되었다는 이유로 주문 직후 취소하면 live entry를 망가뜨린다. 또한 같은 종목의 다른 `order_no`를 PathB 주문으로 오인하면 잘못된 fill 복구나 cancel이 발생한다.

**대상 코드**

- `execution/claude_price_adapter.py`: `mark_order_sent()`, `mark_order_acked()` timestamp 저장.
- `runtime/pathb_runtime.py`: `_pending_buy_age_sec()`, pending buy TTL reconcile, broker fill/open order matching.
- 관련 tests: `tests/test_pathb_runtime.py`.

**개선 전**

- TTL 기준이 plan `created_at` 또는 run timestamp에 의존할 수 있다.
- `entry_execution_id`가 있는데 exact `order_no` match 실패 후 same-ticker fallback이 잘못 적용될 수 있다.
- cancel requested 이후 후속 broker truth 재판정이 약하면 still-open/fill/cancel confirmed를 놓칠 수 있다.

**구현 요구**

1. `mark_order_sent()`는 plan에 `entry_order_sent_at`을 저장한다.
2. `mark_order_acked()`는 plan에 `entry_order_acked_at`을 저장한다.
3. `_pending_buy_age_sec()`는 `entry_order_acked_at`, 없으면 `entry_order_sent_at`만 TTL 기준으로 쓴다.
4. 주문 timestamp가 없으면 TTL cancel을 시도하지 않고 `skipped` 또는 `still_open`으로 둔다.
5. `entry_execution_id`가 있으면 broker fill/open order matching은 exact `order_no`만 허용한다.
6. exact mismatch + 다른 order_no 후보는 `ORDER_UNKNOWN` 또는 defer reason으로 남기고 live cancel/fill 확정 금지.
7. cancel requested 이후에도 broker truth를 재확인해 fill, still open, cancel confirmed를 분리한다.

**개선 후**

- 실제 주문 직후 TTL 초과로 취소되는 위험이 줄어든다.
- 다른 주문번호의 같은 종목 주문을 PathB 주문으로 오인하지 않는다.
- cancel 요청 후 상태가 broker truth로 계속 수렴한다.

**수용 기준**

- old plan + recent sent/ACK -> TTL cancel 없음.
- no sent/ACK timestamp -> cancel order 호출 없음.
- exact `order_no` fill -> filled recovery.
- different `order_no` fill/open order -> filled/cancel 확정 금지.
- cancel requested 후 open order 사라짐 -> cancel confirmed.

**검증**

```powershell
python -m pytest tests/test_pathb_runtime.py -q
python -m py_compile runtime/pathb_runtime.py execution/claude_price_adapter.py
```

---

## P1-1. US PathB Sizing Context And Reason Split

**왜 하는가**

PathB `qty=0`은 order policy 변경 없이도 원인을 분리해야 운영 판단이 가능하다. MRVL처럼 pre-gate budget으로 1주는 가능하지만 early soft gate 후 effective budget이 부족한 경우와, APP처럼 1주 가격이 pre-gate budget 자체를 초과하는 경우는 대응이 다르다.

**대상 코드**

- `runtime/pathb_runtime.py`: `_pathb_qty()`, proposed `_pathb_qty_with_context()`, submit safety context.
- `execution/safety_gate.py`: `SafetyContext`, qty/price reason classification.
- 관련 tests: `tests/test_live_order_safety.py`, `tests/test_pathb_runtime.py`.

**개선 전**

- `qty=0`이 `INVALID_QTY`나 broad safety reason으로 기록된다.
- original budget, effective budget, early gate size multiplier, one-share 가능 여부가 safety payload에 없다.

**구현 요구**

1. `_pathb_qty()`의 public behavior와 반환값은 유지한다.
2. `_pathb_qty_with_context()`를 추가해 qty와 sizing context를 함께 반환한다.
3. context에는 `original_budget_krw`, `effective_budget_krw`, `early_gate_applied`, `early_gate_size_mult`, `can_buy_1_share`, `fixed_sizing`, `sizing_reason`, `pathb_sizing`을 포함한다.
4. PathB submit safety context에 sizing meta를 전달한다.
5. `price_krw <= 0`은 `INVALID_PRICE`.
6. `qty=0`이고 `price_krw > original_budget_krw`는 `HIGH_PRICE_BUDGET_BLOCK`.
7. `qty=0`이고 early gate 적용, `price_krw <= original_budget_krw`는 `ORDER_SIZE_TOO_SMALL_GATE`.
8. `allow_one_share_over_budget` 조건과 early gate policy는 변경하지 않는다.

**개선 후**

- PathB `qty=0` block이 operationally actionable한 reason으로 분리된다.
- 주문 가능 여부 자체는 기존과 동일하다.

**수용 기준**

- 기존 `_pathb_qty()` tests가 그대로 통과한다.
- MRVL형 case는 `ORDER_SIZE_TOO_SMALL_GATE`.
- APP형 case는 `HIGH_PRICE_BUDGET_BLOCK`.
- early gate off + one-share-over-budget 기존 동작 불변.

**검증**

```powershell
python -m pytest tests/test_live_order_safety.py tests/test_pathb_runtime.py -q
python -m py_compile runtime/pathb_runtime.py execution/safety_gate.py
```

## P1-2. V2 Canonical Freshness And Fallback Exclusion

**왜 하는가**

V2 canonical/lifecycle truth가 preferred truth라면 stale/missing 상태가 operator-visible해야 한다. 또한 profit-review timeout fallback은 전략적 HOLD 판단이 아니므로 learning/canonical aggregate에 섞이면 성과 분석이 오염된다.

**대상 코드**

- `runtime/v2_lifecycle_runtime.py`: canonical/lifecycle event freshness source.
- `tools/live_preflight.py`, `tools/live_guardian.py`: freshness warning.
- `dashboard/dashboard_server.py`, `interface/v2_ops_summary.py`: UI/API exposure.
- `minority_report/hold_advisor.py`, `trading_bot.py`, `runtime/pathb_runtime.py`: `advisor_unavailable`, `learning_excluded` propagation.
- 관련 tests: `tests/test_live_guardian.py`, `tests/test_dashboard_pathb.py`, `tests/test_pathb_profit_protection.py`, `tests/test_v2_phase6.py`.

**개선 전**

- canonical table stale/missing이 분석/운영 화면에서 분명하지 않다.
- timeout fallback HOLD가 real HOLD 판단처럼 보일 수 있다.

**구현 요구**

1. canonical freshness 기준을 명시한다: last event time, market, mode, source DB.
2. stale/missing이면 preflight/guardian/dashboard에 warning을 노출한다.
3. timeout fallback은 `advisor_unavailable=True`, `learning_excluded=True`, `fallback_reason`을 유지한다.
4. learning/canonical aggregate는 unavailable/fallback rows를 default exclude하고, 필요 시 excluded count를 표시한다.
5. broker truth unavailable 상태에서 local inference로 fill/performance truth를 확정하지 않는다.

**개선 후**

- stale canonical 상태에서 성과 분석을 맹신하지 않는다.
- fallback HOLD가 전략 성과로 학습되지 않는다.

**수용 기준**

- stale canonical fixture에서 preflight/guardian warning 발생.
- timeout fallback row는 aggregate에서 제외되고 excluded count로 표시.

**검증**

```powershell
python -m pytest tests/test_live_guardian.py tests/test_dashboard_pathb.py tests/test_pathb_profit_protection.py tests/test_v2_phase6.py -q
python -m py_compile runtime/v2_lifecycle_runtime.py tools/live_preflight.py dashboard/dashboard_server.py
```

## P1-3. Brain/Sub-Screener Operator-Visible Guards

**왜 하는가**

`state/brain.json`은 policy memory이지 runtime truth가 아니다. 자동 판단 경로가 직접 brain을 쓰면 짧은 기간 결과나 provider outage가 장기 정책으로 오염될 수 있다. 또한 sub-screener scoped trigger가 global flag보다 우선하면 operator에게 실제 effective state가 보여야 한다.

**대상 코드**

- `claude_memory/brain.py`: direct write guard 또는 write path audit.
- `trading_bot.py`: lesson candidate append/score path, sub-screener trigger state.
- `runtime/sub_screener.py`: trigger precedence/counter.
- `tools/live_preflight.py`, dashboard ops summary: visibility.
- 관련 tests: `tests/test_active_lessons.py`, `tests/test_sub_screener.py`, `tests/test_sub_screener_integration.py`, `tests/test_live_guardian.py`.

**개선 전**

- automatic path가 brain direct write를 해도 test로 고정되지 않을 수 있다.
- global `SUB_SCREENER_TRIGGER_ENABLED=false`여도 scoped KR/US true가 effective면 operator가 오해할 수 있다.

**구현 요구**

1. automatic debate/judgment/lesson path에서 `state/brain.json` 직접 write가 발생하지 않는 test를 추가한다.
2. lesson 후보는 `state/lesson_candidates.json` append/score 또는 approval workflow로 제한한다.
3. sub-screener effective trigger resolver는 global/scoped/default precedence를 한 함수에서 계산한다.
4. ops/preflight에 market별 `effective_enabled`, `global_value`, `scoped_value`, `default_value`, trigger count/rate-limit status를 표시한다.

**개선 후**

- policy memory 오염 위험이 줄어든다.
- operator가 실제 sub-screener trigger state를 config/log 없이 확인한다.

**수용 기준**

- brain direct-write prevention test가 실패하면 automatic path가 막힌다.
- scoped override true/false combinations가 resolver test로 고정된다.
- ops payload에 effective trigger state가 market별로 있다.

**검증**

```powershell
python -m pytest tests/test_active_lessons.py tests/test_sub_screener.py tests/test_sub_screener_integration.py tests/test_live_guardian.py -q
python -m py_compile trading_bot.py runtime/sub_screener.py claude_memory/brain.py tools/live_preflight.py
```

## P1-4. Runtime Tuning Override Cleanup

**왜 하는가**

Claude tuner 응답에는 `action`, `mode`, `reason`, `warning` 같은 설명 필드가 포함될 수 있다. 이 값이 runtime override state에 남으면 실제 조정값과 설명값이 섞여 prompt/debug/ops 분석이 흐려진다.

**대상 코드**

- `runtime/tuning_bounds.py`: `RUNTIME_ADJUSTMENT_BOUNDS`, `coerce_runtime_adjustments()`.
- `minority_report/tuner.py`: tuner prompt bounds와 coercion.
- `trading_bot.py`: `_runtime_overrides()`, `_restore_runtime_overrides_from_payload()`, `_apply_runtime_tuning_adjustments()`.
- 관련 tests: `test_trading_improvements.py`, `tests/test_market_breadth_prompt_contract.py`, `tests/test_candidate_action_live_mapping.py`.

**개선 전**

- bounds가 여러 파일에 하드코딩될 수 있다.
- non-override key가 normalized dict에 남을 수 있다.
- prompt에 노출되는 bounds와 실제 coercion bounds가 drift될 수 있다.

**구현 요구**

1. `RUNTIME_ADJUSTMENT_BOUNDS`를 단일 source로 둔다.
2. `coerce_runtime_adjustments()`는 bounds key만 반환한다. input dict의 unknown key는 drop한다.
3. `momentum_wait_adjust_min`은 int clamp, 나머지는 float clamp/round를 유지한다.
4. tuner prompt bounds text도 같은 constant에서 생성한다.
5. existing bounds 값은 변경하지 않는다.

**개선 후**

- runtime override payload에는 bounded numeric adjustment만 남는다.
- tuner prompt와 runtime apply/restore bounds가 일치한다.

**수용 기준**

- `action`, `mode`, `reason`, `warning`, unknown key drop test 통과.
- 기존 clamp tests 통과.
- prompt contract test가 constant 기반 text를 확인.

**검증**

```powershell
python -m pytest test_trading_improvements.py tests/test_market_breadth_prompt_contract.py tests/test_candidate_action_live_mapping.py -q
python -m py_compile runtime/tuning_bounds.py minority_report/tuner.py trading_bot.py
```

## P1-5. Raw-Score Shadow And Multi-Source Consensus

**왜 하는가**

raw top30 missing, US PLAN_B/bucket-quality, KIS/Yahoo/FMP disagreement는 live promotion 전에 outcome으로 검증해야 한다. 평균 수익률 하나만 보면 특정 일자 winner에 과적합될 수 있다.

**대상 코드**

- `kis_api.py`: US KIS ranking shadow, projected dollar volume source metadata.
- `bot/screener_quality.py`: source overlap/disagreement, final prompt order.
- `tools/analyze_candidate_audit.py`, `tools/analyze_prompt_overlay_shadow.py`: shadow outcome report.
- `trading_bot.py`: prompt/candidate audit field propagation.
- 관련 tests: `tests/test_screener_quality.py`, `tests/test_candidate_audit.py`, `tests/test_candidate_action_live_mapping.py`.

**개선 전**

- raw score가 높은데 prompt에서 빠진 후보가 좋은 후보인지 알기 어렵다.
- source disagreement가 outcome과 연결되지 않는다.
- US KIS ranking primary 전환 근거가 부족하다.

**구현 요구**

1. KR raw top30 missing은 `volume_surge`, `momentum_now`, bucket/source, score component와 함께 outcome을 기록한다.
2. US candidates는 KIS/Yahoo/FMP source overlap, disagreement, fallback reason을 저장한다.
3. added candidates와 excluded candidates의 30m/60m outcome, PF, count, top-day contribution을 비교한다.
4. report는 minimum sample gate를 표시한다: 10 trading days, 50 labeled outcomes, top-day contribution < 40%.
5. live prompt cap, `trade_ready`, order cap, PathB gate 변경은 하지 않는다.

**개선 후**

- source/ranker shadow가 live promotion 가능한지 증거 기반으로 판단된다.
- source noise와 실제 missed opportunity가 분리된다.

**수용 기준**

- report가 added/excluded/source-overlap group을 출력한다.
- sample 부족이면 promotion blocked로 표시한다.

**검증**

```powershell
python -m pytest tests/test_screener_quality.py tests/test_candidate_audit.py tests/test_candidate_action_live_mapping.py -q
python tools/analyze_candidate_audit.py --db data/audit/candidate_audit.db --market ALL --json
```

## P1-6. PathB Fill Truth / Sell Pending / EXPIRED Monitoring

**왜 하는가**

PathB fill truth와 sell pending remainder는 PnL truth, protective exit, stale waiting plan cleanup에 직접 연결된다. broker truth가 없을 때 local inference로 fill/cancel을 확정하면 위험하다.

**대상 코드**

- `runtime/pathb_runtime.py`: fill recovery, sell pending remainder, expired waiting plan cleanup.
- `kis_api.py`: KR/US full/partial/cancel payload parser.
- `runtime/broker_truth_snapshot.py`: fills/open orders snapshot.
- dashboard/ops summary: unresolved lifecycle visibility.
- 관련 tests: `tests/test_pathb_runtime.py`, `tests/test_pathb_sell_reconcile.py`, `tests/test_kis_kr_order_safety.py`, `tests/test_broker_truth_snapshot.py`.

**개선 전**

- `ORDER_UNKNOWN` 또는 partial sell remainder가 local state에 오래 남을 수 있다.
- KR `EXPIRED` waiting plan quote resampling/stale cleanup이 operator-visible하지 않을 수 있다.
- actual KIS full/partial/cancel payload shape coverage가 부족하다.

**구현 요구**

1. KR/US full fill, partial fill, cancel, open remainder payload fixture를 추가한다.
2. sell pending remainder TTL 후 broker truth로 remaining qty를 확인한다.
3. open remainder가 있으면 duplicate cancel/order를 피하고 still-open/retry로 남긴다.
4. fill이 확인되면 broker fill price/qty/time으로 lifecycle state를 갱신한다.
5. broker truth unavailable이면 fill/cancel 확정 금지.
6. KR EXPIRED plan은 quote resampling, stale waiting-plan cleanup, skip reason을 ops에 노출한다.
7. CRWV 같은 체결 후 route/origin/fill metadata 보존을 회귀 테스트한다.

**개선 후**

- unresolved lifecycle state가 broker truth 기반으로 수렴한다.
- partial remainder와 stale waiting plan이 operator에게 보인다.

**수용 기준**

- full/partial/cancel fixture tests 통과.
- broker truth unavailable case는 fail closed.
- stale active rows와 `ORDER_UNKNOWN` count가 ops summary에 표시된다.

**검증**

```powershell
python -m pytest tests/test_pathb_runtime.py tests/test_pathb_sell_reconcile.py tests/test_kis_kr_order_safety.py tests/test_broker_truth_snapshot.py -q
python -m py_compile runtime/pathb_runtime.py kis_api.py runtime/broker_truth_snapshot.py
```

## Implementation Order

1. P0-5 zero-holding fixtures and P0-4 token backoff tests first, because they protect live broker truth and fail-closed behavior.
2. P0-1/P0-2 audit/report fields next, because profitability decisions need trustworthy measurement.
3. P0-6/P0-7 PathB visibility and pending-buy order matching after broker truth fixtures are stable.
4. P0-3 KR shadow instrumentation can be built in parallel with reporting, but promotion remains blocked by sample gate.
5. P1-1/P1-4 are bounded cleanup tasks and should include strict behavior-invariance tests.
6. P1-2/P1-3/P1-6 protect future analysis and operator visibility.
7. P1-5 shadow outcome analysis should run only after P0-1/P0-2 fields are reliable.

## Final QA Gate

Before marking any P0/P1 item complete:

```powershell
git status --short
git diff --stat
python -m py_compile trading_bot.py kis_api.py runtime/pathb_runtime.py runtime/broker_truth_snapshot.py dashboard/dashboard_server.py
python -m pytest tests/test_candidate_audit.py tests/test_screener_quality.py tests/test_candidate_action_live_mapping.py -q
python -m pytest tests/test_broker_truth_snapshot.py tests/test_kis_kr_order_safety.py tests/test_pathb_runtime.py -q
```

If an item touches live start, broker truth, order lifecycle, audit schema, or dashboard truth, run the relevant wider pytest group before commit. Do not stage `state/brain.json`, runtime DBs, generated JSON reports, logs, or local data artifacts without explicit approval.
