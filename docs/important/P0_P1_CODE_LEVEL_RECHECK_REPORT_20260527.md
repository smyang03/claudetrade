# P0/P1 코드 레벨 재검토 리포트

Updated: 2026-05-27

## 2026-05-27 Implementation Recheck Addendum

이번 코드 반영 범위는 MD의 P0-4와 P0-6 중 live fail-closed에 직접 연결되는 부분이다.

- P0-4 KIS `EGW00133` token rate-limit/backoff
  - 개선 전: `kis_api.get_access_token()`이 `EGW00133`을 generic token 발급 실패로 처리했고, startup token helper가 rate-limit와 일반 네트워크 실패를 같은 retry path로 취급했다.
  - 개선 후: `kis_api.py`에 `KISTokenRateLimitError`, `EGW00133` classifier, app-key/profile fingerprint 기반 cooldown marker(`state/`)를 추가했다. cooldown 중에는 token 발급 HTTP call을 다시 보내지 않고, 기존 valid token file은 덮어쓰지 않는다. `trading_bot.py` startup token helper는 rate-limit를 만나면 추가 sleep/retry 없이 fail-closed 한다.
  - 검증: `python -m pytest tests/test_kis_token_auto_refresh.py tests/test_startup_token_refresh.py -q` 통과. `python -m py_compile kis_api.py trading_bot.py runtime/pathb_runtime.py` 통과.
  - 남은 확인: live/preflight 화면에서 rate-limit 상태를 operator-visible status로 표시하는 범위는 아직 P0-4 후속으로 남긴다.

- P0-6 PathB entry broker-truth gate
  - 개선 전: live entry scan에서 bot token unavailable 또는 bot balance provider unavailable이면 broker truth refresh를 skip하면서 `allowed=True`로 이어질 수 있었다.
  - 개선 후: 두 unavailable branch가 `allowed=False`, `blocked=True`, `reason=BLOCKED_BROKER_TRUTH`, `scope=market`로 fail-closed 하며 `broker_truth_skip_reason`, `broker_truth_missing`, `broker_truth_stale`을 details에 남긴다.
  - 검증: `python -m pytest tests/test_pathb_runtime.py -q` 통과. token unavailable/provider unavailable 단위 테스트와 기존 refresh-before-buy/refresh-failure 회귀를 함께 확인했다.
  - 남은 확인: preflight/dashboard/ops summary에서 TTL, last attempt, latency, skip/block reason을 별도 화면 상태로 노출하는 범위는 아직 P0-6 후속으로 남긴다.

MD 삭제 판단: P0-4/P0-6의 핵심 fail-closed 코드는 반영됐지만, P0-3 및 P1 항목과 P0-4/P0-6의 operator-visible 표시 후속이 남아 있다. 따라서 이 MD 세트는 아직 삭제하지 않고, 남은 항목이 ACTIVE_WORK/TODO와 코드/QA로 완전히 흡수된 뒤 삭제한다.

운영 dry-run: `python tools/live_preflight.py --mode paper --skip-dashboard --json`은 실행됐으나 현재 paper token 만료, KR/US token refresh fail, duplicate paper bot process inventory 때문에 `ok=false`였다. 동시에 KR PathB readiness가 `BLOCKED_BROKER_TRUTH`로 노출되고 broker truth missing/stale summary가 표시되는 것은 fail-closed 방향과 일치한다. 이 결과는 환경/운영 상태 문제이며, 이번 코드 변경의 단위/회귀 테스트는 통과했다.

재검토 보정: KR/US shared KIS credential에서 cooldown marker가 `credential_mode` 차이로 분리될 수 있는 점을 수정했다. shared credential은 동일 marker filename을 쓰며, active cooldown 중에도 valid cached token은 그대로 사용되는 테스트를 추가했다.

## 범위

이 문서는 [P0_P1_CODE_LEVEL_DEV_REQUIREMENTS_20260527.md](P0_P1_CODE_LEVEL_DEV_REQUIREMENTS_20260527.md)의 P0/P1 요구사항을 현재 코드와 테스트 기준으로 다시 검토한 결과다. 삭제된 plan/report에서 되살린 항목 중 실제 코드 경로가 확인된 것과 아직 구현이 필요한 것을 분리한다.

주의: 이 리포트의 "코드 확인"은 현재 working tree 기준이다. 커밋 검증 완료와 운영 종료 판정은 [core/DEVELOPED_WORK.md](core/DEVELOPED_WORK.md) 및 실제 QA/운영 DB 리포트 결과로 별도 판단한다.

판정 기준:

| 판정 | 의미 |
| --- | --- |
| 코드 확인 | 현재 코드와 핵심 테스트 경로가 있다. 커밋/QA 또는 운영 DB 리포트 관찰이 남을 수 있다. |
| 부분 | 기반 코드는 있으나 안전장치, 운영 가시성, 승격 게이트, 테스트 중 일부가 빠져 있다. |
| 미구현 | 의미 있는 구현 경로를 찾지 못했다. |

## 핵심 결론

1. P0-6의 live fail-open 결함은 현재 working tree에서 보정됐다. PathB entry broker-truth gate는 token 또는 bot balance provider unavailable 상태에서 `BLOCKED_BROKER_TRUTH`로 fail closed 한다. 남은 범위는 preflight/dashboard/ops summary에 TTL, attempt, latency, skip/block reason을 더 명확히 노출하는 것이다.
2. P0-4의 `EGW00133` token rate-limit classifier/cooldown은 현재 working tree에서 보정됐다. 남은 범위는 token rate-limit 상태를 live/preflight operator-visible status로 표시하고, 환경 token 만료/중복 process 문제를 운영 상태에서 해소한 뒤 재확인하는 것이다.
3. P0-3은 shadow 기반은 있으나 승격 요구를 충족하지 못한다. counterfactual row, KR microstructure metadata, minute outcome, scheduler는 있으나 first-entry sequence, top-day contribution, sample/calendar gate, exit cap-hit/MFE-cap 리포트가 빠져 있다.
4. P1-3과 P1-4는 닫기 전에 정리해야 한다. sub-screener trigger guard는 구현됐지만 direct `BrainDB` write가 남아 있고, runtime override 저장 경로는 정리됐으나 shared coercion helper는 extra field를 기본 보존한다.

## 상태 매트릭스

| ID | 카테고리 | 항목 | 판정 | 코드 레벨 확인 결과 | 다음 개발 요구 |
| --- | --- | --- | --- | --- | --- |
| P0-1 | 수익성 | actual prompt profit visibility | 코드 확인 | `actual_prompt_*`, `selection_trace_id`, prompt rank, mismatch 분석 경로가 있다. | live DB 리포트 실행 후 운영 종료 판정. |
| P0-2 | 데이터베이스 | candidate bucket/source/score 품질 | 코드 확인 | bucket/source/raw/trainer/data-gap 필드가 audit/dashboard로 전달된다. | 운영 DB blank-rate/source coverage 확인. |
| P0-3 | 수익성 | KR entry/exit shadow instrumentation | 부분 | counterfactual/minute outcome 기반은 있으나 승격 게이트와 first-entry/exit overlay 분석이 부족하다. | sample/top-day/first-entry/exit-cap 리포트와 테스트 추가. |
| P0-4 | 운영 | KIS `EGW00133` token rate-limit/backoff | 코드 확인 / 운영 가시성 후속 | `KISTokenRateLimitError`, `EGW00133` classifier, shared cooldown marker, startup fail-closed, cached-token 보존 테스트가 있다. | preflight/dashboard에서 token rate-limit status 노출, 운영 환경 token 만료 해소 후 재검증. |
| P0-5 | 버그 | broker-truth zero-holding fixture | 코드 확인 | fresh zero-holding evidence일 때만 stale PathB state를 닫고 stale/unavailable은 유지한다. | 커밋/QA 전까지 ACTIVE_WORK에서 추적. |
| P0-6 | 운영 | PathB entry broker-truth gate visibility | 코드 확인 / 운영 가시성 후속 | token/provider unavailable 경로가 `BLOCKED_BROKER_TRUTH`로 fail closed 되며 테스트가 있다. | TTL/attempt/latency/skip reason을 preflight/dashboard/ops summary에 더 명확히 노출. |
| P0-7 | 버그 | PathB pending-buy TTL/order matching | 코드 확인 | ACK/sent timestamp와 exact order identity 기준으로 TTL/cancel/fill 판단한다. | 커밋/QA 전까지 ACTIVE_WORK에서 추적. |
| P1-1 | 버그 | US PathB sizing context/reason split | 코드 확인 | `HIGH_PRICE_BUDGET_BLOCK`과 `ORDER_SIZE_TOO_SMALL_GATE`가 sizing context와 분리된다. | 커밋/QA 전까지 ACTIVE_WORK에서 추적. |
| P1-2 | 데이터베이스 | V2 canonical freshness/fallback exclusion | 부분 | canonical table과 fallback `learning_excluded`는 있으나 freshness 운영 가시성이 약하다. | dashboard/preflight freshness age/status와 테스트 추가. |
| P1-3 | 운영 | Brain/sub-screener guard visibility | 부분 | sub-screener scoped trigger/shadow/counter/test는 있으나 direct BrainDB write가 남아 있다. | Brain auto-write gate/approval과 effective trigger visibility 추가. |
| P1-4 | 버그 | runtime tuning override cleanup | 부분 | live override persistence는 clean하지만 helper/tuner는 `action`/`reason`을 보존할 수 있다. | bounded numeric adjustment와 decision metadata 분리. |
| P1-5 | 수익성 | raw-score shadow / multi-source consensus | 부분 | raw/trainer/source 필드는 있으나 labeled source-overlap/missing-winner 리포트가 부족하다. | raw top-N missing/source overlap/outcome analyzer 추가. |
| P1-6 | 운영 | PathB fill truth / sell pending / EXPIRED monitoring | 부분 | sell-pending, ORDER_UNKNOWN recovery, preflight conflict check가 있다. | EXPIRED/partial remainder aging 가시성 보강. |

## P0 상세

### P0-1. Actual Prompt Profit Visibility

판정: 코드 확인, 운영 DB 검증 전.

왜 하는지:

- 좋은 후보가 Claude 판단 전에 빠졌는지, Claude가 실제 prompt를 보고도 놓쳤는지 구분해야 수익성 개선 방향을 정할 수 있다.

코드 근거:

- `audit/candidate_audit_store.py:37-130`: `selection_trace_id`, `visibility_contract_version`, `actual_prompt_call_id`, `actual_prompt_included`, `actual_prompt_rank`, `reported_input_to_claude`, `prompt_join_delta_sec` 저장.
- `bot/screener_quality.py:53-99`: `_final_prompt_pool`을 실제 prompt-visible 후보로 보고 제외 row를 분리.
- `trading_bot.py:12831-12868`, `trading_bot.py:12961-12980`, `trading_bot.py:13204-13213`: call id, actual prompt ticker/rank map, row-level actual prompt fields 생성.
- `trading_bot.py:13242-13306`: call payload에 `prompt_candidate_count=actual_prompt_count`, `actual_prompt_tickers`, `actual_prompt_ranked_tickers` 기록.
- `tests/test_screener_quality.py:63-107`, `tests/test_candidate_action_live_mapping.py:2053-2235`, `tests/test_candidate_audit.py:30-71`: actual prompt visibility와 mismatch count 검증.

개선 전:

- `input_to_claude`와 timestamp join이 prompt 포함 여부를 과대/과소 판정할 수 있었다.

개선 후:

- prompt 노출 여부가 `actual_prompt_call_id`와 `selection_trace_id` 기준으로 측정된다.
- included/missing 성과를 actual prompt rank 기준으로 분리할 수 있다.

남은 요구:

- 현재 live `data/audit/candidate_audit.db`에 대해 `tools/analyze_candidate_audit.py`를 실행하고 mismatch/unmeasured count가 허용 범위인지 확인해야 운영 완료로 닫는다.

### P0-2. Candidate Bucket/Source/Score Data Quality

판정: 코드 확인, 운영 DB 검증 전.

왜 하는지:

- selection 수익성 저하 원인을 ranker, source, price/data, execution/risk로 분리해야 한다.

코드 근거:

- `audit/candidate_audit_store.py:93-115`: `raw_score_current`, `raw_score_components_json`, trainer score, `source_tags_json`, `bucket_reasons_json`, data gap, `candidate_quality_score` 저장.
- `trading_bot.py:12911-12953`: bucket/source tag가 비어 있을 때 보강.
- `trading_bot.py:13144-13172`: trainer/bucket/source/data-gap 필드를 audit row에 기록.
- `tests/test_candidate_action_live_mapping.py:1941-2010`, `tests/test_candidate_audit.py:186-279`, `tests/test_candidate_audit.py:293-349`: persistence/merge 동작 검증.
- `dashboard/dashboard_server.py:8090-8196`: candidate audit API summary에 trainer/source metric 노출.

개선 전:

- 후보가 왜 prompt에 들어갔거나 제외됐는지 row만 보고 추적하기 어려웠다.

개선 후:

- bucket, source tag, trainer state, raw score, data gap, action route 기준으로 query 가능하다.

남은 요구:

- 향후 필드 추가 시 기존 audit payload/source merge 계약을 깨지 말아야 한다.

### P0-3. KR Entry/Exit Shadow Instrumentation

판정: 부분.

왜 하는지:

- KR first-entry/exit overlay 가설은 소수 표본이나 특정 일자 winner에 과적합될 수 있으므로 shadow와 broker-fill-aware replay로 검증해야 한다.

구현된 것:

- `runtime/counterfactual_paths.py:12-26`: common path와 KR `vi_safe_reclaim`, `orderbook_support` 정의.
- `runtime/counterfactual_paths.py:65-213`: KR confirmation, VI, orderbook, OR/VWAP/volume, trigger metadata 포함 row 생성.
- `trading_bot.py:4328-4395`: candidate action runtime context에서 counterfactual row 기록.
- `tools/update_counterfactual_outcomes.py:39-44`: `outcome_30m_pct`, `outcome_60m_pct`, `max_runup_60m_pct`, `max_drawdown_60m_pct` 정의.
- `tests/test_update_counterfactual_outcomes.py:934-967`: minute horizon과 MFE/MAE fill 검증.
- `tools/run_counterfactual_pipeline.py`, `tests/test_counterfactual_pipeline_scheduler.py:22-179`: collect/update/analyze scheduler 검증.
- `tools/analyze_kr_confirmation_gate.py`: sample warning 기반 분석기 존재.

부족한 것:

- 실제 fill 기준 `entry_sequence_of_day` 또는 first-entry sequence 필드가 없다.
- first-entry-only, first-two-entry 성과 리포트가 운영 분석기로 구현되지 않았다.
- 최소 `30` filled probes 또는 `4` calendar weeks, top-day contribution `<40%` 승격 게이트가 enforce되지 않는다.
- exit cap-hit/MFE-cap 비교 리포트가 counterfactual promotion flow에 묶여 있지 않다.
- 현재 counterfactual actual path는 route/action context 중심이라, promotion 리포트에는 broker fill id와 실제 entry/exit identity 연결이 더 필요하다.

개선 전:

- 특정 날 성과가 좋다는 이유로 KR entry/exit 정책을 live 변경할 위험이 있다.

개선 후 요구:

- shadow report가 broker-filled sample count, calendar span, top-day concentration, first-entry sequence, MFE/MAE, OR/VWAP/volume/VI/orderbook evidence, exit cap-hit 비교를 모두 보여야 한다.

개발 요구:

1. 실제 decision/fill 경로에 entry sequence 필드를 추가한다.
2. `tools/analyze_kr_confirmation_gate.py` 또는 신규 KR shadow report에 sample/calendar/top-day gate를 추가한다.
3. top-day contribution이 40% 이상이면 promotion을 block하는 테스트를 추가한다.
4. exit overlay 분석에 cap-hit, MFE floor, max runup, max drawdown, final PnL을 포함한다.

### P0-4. KIS Token `EGW00133` Rate-Limit Backoff

판정: 코드 확인 / 운영 가시성 후속.

왜 하는지:

- token 발급 제한은 broker truth, order status, live entry safety를 동시에 끊을 수 있다. rate-limit와 credential failure를 구분하지 못하면 refresh storm이나 false-safe 상태가 생긴다.

코드 근거:

- `kis_api.py`: `KISTokenRateLimitError`, `EGW00133` classifier, cooldown marker, shared KR/US marker, cached-token preservation path가 있다.
- `trading_bot.py`: startup token helper가 `KISTokenRateLimitError`를 만나면 추가 sleep/retry 없이 fail-closed 한다.
- `tests/test_kis_token_auto_refresh.py`: cooldown 기록, active cooldown HTTP call 차단, shared KR/US marker, valid cached token 사용을 검증한다.
- `tests/test_startup_token_refresh.py`: startup helper가 token rate-limit에서 retry storm을 만들지 않는지 검증한다.

개선 전:

- rate-limit, network failure, credential failure가 같은 실패로 보인다.
- KR/US shared credential 경로에서 반복 token 요청이 발생할 수 있다.

개선 후 요구:

- `EGW00133`은 KIS token rate-limit로 분류된다.
- 기존 valid token은 보존된다.
- shared cooldown/lock으로 반복 발급 시도를 막는다.
- startup/preflight는 token rate-limited 상태를 표시하고 broker truth healthy처럼 진행하지 않는다.

개발 요구:

1. `kis_api.py`에 KIS token error classifier를 추가해 `rt_cd`, `msg_cd`, `msg1`, HTTP status/body를 추출한다.
2. runtime `state/` 아래에 app key/profile 기준의 짧은 cooldown marker를 둔다. secret은 저장하지 않는다.
3. `get_access_token(force_refresh=True)`가 cooldown을 존중하게 한다.
4. `_get_startup_token_with_backoff()`가 classifier 결과를 보고 rate-limit와 credential failure를 분리한다.
5. cached valid token 보존, `EGW00133` cooldown, non-rate credential failure, startup retry 테스트를 추가한다.

### P0-5. Broker-Truth Zero-Holding Fixture

판정: 코드 확인, 커밋/QA 전.

왜 하는지:

- local stale position 삭제는 destructive 동작이므로 fresh broker truth가 없으면 실행하면 안 된다.

코드 근거:

- `runtime/pathb_runtime.py:5097-5161`: fresh broker truth, zero broker qty, no open order, sell-fill evidence를 확인한 뒤 safe zero-holding으로 판단.
- `runtime/pathb_runtime.py:5190-5245`: safe evidence일 때만 stale run/local position을 닫는다.
- `tests/test_pathb_runtime.py:715-825`: fresh zero-holding cleanup과 stale broker-truth fail-closed 검증.

개선 전:

- stale/malformed broker row가 local state 삭제로 이어질 위험이 있었다.

개선 후:

- fresh broker truth가 아니면 local state를 유지한다.

남은 요구:

- 현재 working tree 기준 구현/테스트 경로는 확인됐다. 커밋/QA가 끝나기 전까지 ACTIVE_WORK 추적은 유지한다.
- 운영 로그에서 새로운 KIS row shape가 발견될 때 fixture를 추가한다.

### P0-6. PathB Entry Broker-Truth Gate Visibility

판정: 부분, P0 수정 필요.

왜 하는지:

- PathB live entry는 broker truth가 증명되지 않으면 scan/buy로 이어지면 안 된다. 운영 가시성보다 먼저 fail-closed 동작이 필요하다.

구현된 것:

- `runtime/pathb_runtime.py:703-791`: entry-scan broker truth refresh, TTL, min interval, latency, metrics, snapshot status, block reason 구현.
- `runtime/pathb_runtime.py:1584-1590`: entry scan 전에 gate 실행.
- `runtime/pathb_runtime.py:600-668`: `BLOCKED_BROKER_TRUTH`, `BROKER_SYNC_QUARANTINE`, `ORDER_UNKNOWN_UNRESOLVED` blocked scan audit/log 처리.
- `tests/test_pathb_runtime.py:2324-2399`: refresh-before-buy와 refresh-failure block 검증.

결함:

- 이전 결함: bot token unavailable 또는 bot balance provider unavailable일 때 `allowed=True`로 scan이 계속될 수 있었다.
- 현재 working tree: 두 경로 모두 `BLOCKED_BROKER_TRUTH`로 fail closed 한다.

개선 전:

- refresh failure는 block하지만, token/provider unavailable은 broker truth를 건너뛰고 scan이 계속될 수 있다.

개선 후 요구:

- live mode에서는 token/provider unavailable이 `allowed=False`, `blocked=True`, `reason=BLOCKED_BROKER_TRUTH`를 반환해야 한다.
- `broker_truth_skip_reason`은 details에 남긴다.
- paper mode skip/allow는 명시적으로 의도된 경우에만 유지한다.
- preflight/dashboard에서 last attempt, TTL, stale/error, latency, skip/block reason을 log scraping 없이 확인해야 한다.

개발 요구:

1. `_entry_scan_broker_truth_gate()`의 live token/provider unavailable branch를 fail closed로 바꾼다.
2. token unavailable, bot balance provider unavailable, stale snapshot, refresh error, paper skip 테스트를 추가한다.
3. live preflight가 broker truth dependency unavailable 상태를 fail-closed로 판정하는지 검증한다.
4. gate state가 live status/ops summary/dashboard에 없으면 추가한다.

### P0-7. PathB Pending-Buy TTL And Order Matching

판정: 코드 확인, 커밋/QA 전.

왜 하는지:

- pending-buy TTL은 다른 broker order를 cancel하거나 다른 order fill을 내 fill로 오인하면 안 된다.

코드 근거:

- `runtime/pathb_runtime.py:5441-5447`: TTL age 기준은 `entry_order_acked_at`, 없으면 `entry_order_sent_at`.
- `runtime/pathb_runtime.py:5453-5507`: fill/open order를 execution/order identity로 exact match하고 no-order fallback은 제한적으로 처리.
- `runtime/pathb_runtime.py:5510-5711`: broker truth unavailable은 defer, execution mismatch는 `ORDER_UNKNOWN`, exact open order는 cancel, no-evidence는 cancel confirmed.
- `tests/test_pathb_runtime.py:3282-3581`: unavailable, mismatched fill/open order, single no-order fallback, ambiguous no-order open order 검증.

개선 전:

- same-ticker evidence를 과신할 수 있었다.

개선 후:

- TTL action이 exact 또는 unambiguous broker evidence를 요구한다.

남은 요구:

- 현재 working tree 기준 구현/테스트 경로는 확인됐다. 커밋/QA가 끝나기 전까지 ACTIVE_WORK 추적은 유지한다.
- broker order schema 확장 시 exact-match 테스트를 유지한다.

## P1 상세

### P1-1. US PathB Sizing Context And Reason Split

판정: 코드 확인, 커밋/QA 전.

왜 하는지:

- `qty=0` 차단은 high price budget 문제인지 early soft gate size reduction 문제인지 분리되어야 운영 조치가 가능하다.

코드 근거:

- `runtime/pathb_runtime.py:8688-8765`: original/effective budget, early gate state, can-buy-one-share context 반환.
- `execution/safety_gate.py:121-128`: high-price budget은 `HIGH_PRICE_BUDGET_BLOCK`, early gate size reduction은 `ORDER_SIZE_TOO_SMALL_GATE`.
- `tests/test_live_order_safety.py:219-302`: 두 reason split 검증.

개선 전:

- operator가 broad `INVALID_QTY`만 볼 수 있었다.

개선 후:

- budget, early gate, price level 중 어느 축을 봐야 하는지 reason code로 구분된다.

남은 요구:

- 현재 working tree 기준 구현/테스트 경로는 확인됐다. 커밋/QA가 끝나기 전까지 ACTIVE_WORK 추적은 유지한다.

### P1-2. V2 Canonical Freshness And Fallback Exclusion

판정: 부분.

왜 하는지:

- stale performance truth와 timeout fallback row가 학습/수익성 분석에 섞이면 이후 판단이 오염된다.

구현된 것:

- `tools/sync_v2_learning_performance.py:73-109`: `v2_canonical_performance`에 `learning_allowed`, raw event count, metric contract, `synced_at` 저장.
- `tools/sync_v2_learning_performance.py:582-650`: live/clean/forward-complete row만 learning allowed.
- `dashboard/dashboard_server.py:5010-5161`: canonical total, learning-allowed count, `canonical_last_synced_at`, truth source 전환.
- `runtime/pathb_runtime.py:3944-3951`: profit review timeout fallback을 `advisor_unavailable`, `learning_excluded`로 표시.
- `tests/test_v2_learning_performance_sync.py:289-310`, `tests/test_v2_learning_performance_sync.py:1324-1484`, `tests/test_pathb_runtime.py:863-870`: canonical/fallback exclusion 검증.

부족한 것:

- dashboard UI는 `V2 truth`/`legacy`는 보여주지만 freshness age/status와 learning-allowed ratio가 충분히 드러나지 않는다.
- live preflight의 explicit freshness threshold check를 찾지 못했다.

개선 전:

- operator가 stale canonical row를 fresh truth처럼 볼 수 있다.

개선 후 요구:

- dashboard/preflight가 canonical last sync age, stale/fresh status, learning-allowed count, fallback-excluded count를 보여야 한다.

개발 요구:

1. freshness threshold env/config를 보수적 기본값으로 추가한다.
2. `_ml_db_digest()`에서 canonical age seconds/status를 계산한다.
3. dashboard에 stale/fresh와 `learning_allowed/total`을 표시한다.
4. live preflight가 stale canonical truth를 warning/fail로 판정하게 한다.

### P1-3. Brain/Sub-Screener Operator-Visible Guards

판정: 부분.

왜 하는지:

- hidden rescreen trigger와 automatic long-term memory write는 운영자 승인 없이 전략 행동을 바꿀 수 있다.

구현된 sub-screener 근거:

- `trading_bot.py:632-637`: market-scoped trigger가 global trigger보다 우선.
- `trading_bot.py:23197-23305`: enabled, interval, blackout, session/min interval rate limit, shadow scan, trigger attempt, success tracking 구현.
- `runtime/sub_screener.py:53-63`: per-market session counter 저장.
- `tests/test_sub_screener.py:27-143`: trigger detection, rate limit, scan/attempt/success counter 검증.
- `tests/test_sub_screener_integration.py:59-327`: disabled, shadow mode, market-scoped trigger, loop prevention, blackout, failure isolation, entry-scan continuation 검증.

Brain write 우려:

- `trading_bot.py:2356-2544`: `state/lesson_candidates.json`에 lesson candidate를 쓰는 안전한 후보 흐름은 있다.
- direct `BrainDB` write가 `trading_bot.py:21276`, `trading_bot.py:21322`, `trading_bot.py:29275`에 남아 있다.
- `minority_report/postmortem.py:694-708`도 policy gate 조건에서 BrainDB strategy/debate/correction state를 갱신한다.

개선 전:

- sub-screener effective trigger와 memory write가 운영자 눈에 잘 보이지 않는다.

개선 후 요구:

- effective sub-screener trigger state가 preflight/live status에 표시된다.
- 승인 워크플로우가 켜지기 전 direct `brain.json` mutation은 block 또는 gate된다.
- 자동 학습 산출물의 기본 경로는 `state/lesson_candidates.json`이어야 한다.

개발 요구:

1. direct `BrainDB.update_*` 호출에 auto-write gate 또는 승인 workflow를 둔다.
2. gate disabled일 때는 lesson candidate만 기록한다.
3. gate disabled 상태에서 direct BrainDB write가 호출되지 않는 테스트를 추가한다.
4. market-scoped/global sub-screener effective value와 counters를 preflight/status에 노출한다.

### P1-4. Runtime Tuning Override Cleanup

판정: 부분.

왜 하는지:

- persisted runtime override는 bounded numeric adjustment만 가져야 한다. `action`, `mode`, `reason`, `warning`은 로그/판단 히스토리에 남아야지 override state에 섞이면 안 된다.

코드 근거:

- `runtime/tuning_bounds.py:6-28`: bounds를 중앙화했지만 `coerce_runtime_adjustments(..., preserve_extra=True)`가 기본값이다.
- `trading_bot.py:8976`, `trading_bot.py:10012`, `trading_bot.py:10123`: `preserve_extra=False`를 사용해 live override 저장은 대체로 clean하다.
- `minority_report/tuner.py:66-67`: wrapper가 `preserve_extra=False` 없이 helper를 호출한다.
- `test_trading_improvements.py:2108-2123`: legacy test가 `_coerce_runtime_adjustments()`에서 `action` 보존을 기대한다.
- `tests/test_candidate_action_live_mapping.py:145-182`: `TradingBot` override가 `reason`/`action`을 drop하는지 검증한다.

개선 전:

- non-runtime field가 adjustment state나 debug view에 섞일 수 있다.

개선 후 요구:

- persisted override로 가는 coercion은 `RUNTIME_ADJUSTMENT_BOUNDS` key만 반환한다.
- tuning decision metadata는 별도 payload로 유지한다.

개발 요구:

1. bounded adjustment helper와 full tuner decision wrapper를 분리하거나 기본값을 `preserve_extra=False`로 바꾼다.
2. `minority_report/tuner.py`에서 persisted override 경로로 `action`, `mode`, `reason`, `warning`이 넘어가지 않게 한다.
3. legacy test의 `action` 보존 기대를 decision payload 쪽으로 옮긴다.
4. `tests/test_runtime_tuning_bounds.py`를 추가해 clamp/drop behavior를 검증한다.

### P1-5. Raw-Score Shadow And Multi-Source Consensus

판정: 부분.

왜 하는지:

- missing winner가 raw ranker 실패인지, source noise인지, trainer penalty인지, prompt cap인지, execution gate인지 분리해야 selection logic을 안전하게 개선할 수 있다.

구현된 것:

- `trading_bot.py:13182-13213`: `raw_score_current`와 raw score components 기록.
- `audit/candidate_audit_store.py:93-115`: raw/trainer/source/quality fields 저장.
- `runtime/candidate_quality_trainer.py:174-377`: source tag, trainer component, candidate state 산출.
- `tests/test_candidate_quality_trainer.py:73-201`: score components, future-field ignore, env-gated KR quality score bonus, trainer state 검증.
- `dashboard/dashboard_server.py:8090-8196`, `dashboard/dashboard_server.py:8519-8524`: candidate audit API에 trainer/source fields 노출.

부족한 것:

- raw top-N missed winners, source overlap, source disagreement, excluded reason, forward outcome을 한 번에 비교하는 labeled analyzer가 완성되어 있지 않다.

개선 전:

- missed winner 하나를 보고 source/ranker/prompt를 즉흥적으로 바꿀 위험이 있다.

개선 후 요구:

- raw top-N, trainer prompt pool, excluded rows, selected rows, forward outcomes를 source tag와 bucket reason 기준으로 비교하는 shadow report가 있어야 한다.

개발 요구:

1. `tools/analyze_candidate_audit.py`를 확장하거나 신규 analyzer를 추가한다.
2. `source_tags_json`, `bucket_reasons_json`, `trainer_candidate_state`, `prompt_excluded_reason`, `route_final_action` 기준으로 group한다.
3. source/ranker 변경 권고 전 minimum sample과 concentration gate를 적용한다.
4. raw high scorer가 trainer에서 제외되고 lower scorer가 selected된 fixture 테스트를 추가한다.

### P1-6. PathB Fill Truth / Sell Pending / EXPIRED Monitoring

판정: 부분.

왜 하는지:

- partial sell fill, remainder, unresolved order, expired plan은 PnL truth와 보호 청산에 직접 영향을 준다.

구현된 것:

- `runtime/pathb_runtime.py:5275-5308`: sell-pending run reconcile과 session close finalize.
- `runtime/pathb_runtime.py:5978-6125`: sell fill evidence, partial fill, open order evidence, TTL expiry, `sell_pending_resolution` 기록.
- `runtime/pathb_runtime.py:6441-6557`: session open/close `ORDER_UNKNOWN` reconcile.
- `runtime/pathb_runtime.py:7116-7203`: broker truth가 still-held를 증명하고 sell evidence가 없을 때 stale exit `ORDER_UNKNOWN`을 `FILLED`로 복구.
- `tests/test_pathb_sell_reconcile.py:228-331`: pending-sell TTL, session-close unresolved sell, wrong-side fill rejection, still-held recovery 검증.
- `tests/test_pathb_sell_reconcile.py:410-445`: session-end partial sell fill과 open sell order retry 검증.
- `tools/live_preflight.py:589-724`: recoverable still-held와 broker/local pending-sell conflict 식별.

부족한 것:

- `ORDER_UNKNOWN`/sell-pending 복구는 강하지만, EXPIRED aging과 partial remainder count를 dashboard/preflight 양쪽에서 operator threshold로 보여주는 완성 경로는 확인하지 못했다.

개선 전:

- unresolved lifecycle state를 logs/local state에서 추론해야 했다.

개선 후 요구:

- aged `EXPIRED`, `SELL_PARTIAL_FILLED`, `SELL_SENT/ACKED`, `ORDER_UNKNOWN` count가 manual/auto-remediable 분류와 함께 preflight/dashboard에 보여야 한다.

개발 요구:

1. PathB expired/sell-pending status aging threshold를 ops summary에 추가한다.
2. partial fill remaining quantity와 next recheck time을 표시한다.
3. runtime reconcile 테스트뿐 아니라 dashboard/preflight payload 테스트를 추가한다.

## 개발 우선순위

1. P0-4/P0-6: 구현된 fail-closed core를 커밋/QA하고 preflight/dashboard/operator-visible status를 마무리한다.
2. P0-3: KR first-entry/exit shadow promotion report에 sample, calendar-span, concentration gate를 넣는다.
3. P0-5: broker-truth zero-holding fixture를 실제 KR/US row shape 기준으로 보강한다.
4. P1-4: runtime tuning coercion helper와 테스트를 정리해 persisted override가 bounded numeric key만 갖게 한다.
5. P1-3: direct BrainDB write gate와 sub-screener effective trigger visibility를 추가한다.
6. P1-2: canonical freshness status를 dashboard/preflight에 추가한다.
7. P1-5: raw-score/source-overlap outcome analyzer를 추가한다.
8. P1-6: EXPIRED와 partial sell remainder aging 가시성을 마무리한다.

## 검증 명령

이번 작업은 문서 재검토이므로 코드 테스트는 실행하지 않았다. 문서 sanity check:

```powershell
git diff --check -- docs/important/P0_P1_CODE_LEVEL_RECHECK_REPORT_20260527.md docs/important/README.md docs/important/core/DOCUMENTATION_INDEX.md docs/important/core/DOCUMENTATION_INVENTORY.md
rg -n "[ \t]+$" docs/important/P0_P1_CODE_LEVEL_RECHECK_REPORT_20260527.md docs/important/README.md docs/important/core/DOCUMENTATION_INDEX.md docs/important/core/DOCUMENTATION_INVENTORY.md
```

남은 개발을 구현할 때 권장 테스트:

```powershell
python -m pytest tests/test_pathb_runtime.py tests/test_pathb_sell_reconcile.py -q
python -m pytest tests/test_startup_token_refresh.py tests/test_kis_token_auto_refresh.py -q
python -m pytest tests/test_counterfactual_pipeline_scheduler.py tests/test_update_counterfactual_outcomes.py -q
python -m pytest tests/test_candidate_audit.py tests/test_candidate_action_live_mapping.py tests/test_candidate_quality_trainer.py -q
python -m pytest tests/test_sub_screener.py tests/test_sub_screener_integration.py -q
python -m pytest tests/test_live_order_safety.py -q
```
