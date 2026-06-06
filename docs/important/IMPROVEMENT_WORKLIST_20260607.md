# Improvement Worklist

Updated: 2026-06-07

이 문서는 플랜 문서, 운영 DB, 코드 경로를 함께 확인한 뒤 남긴 개선 작업 상세 리스트다. 목적은 기존 강점인 US PathB 수익 경로, broker/risk fail-closed, V2 lifecycle truth, Claude selection 구조를 유지하면서 실제 DB와 코드에서 확인된 약점만 좁게 개선하는 것이다.

## Scope Guard

- `.env*`, `config/v2_start_config.json`, live PathB gate, 주문 금액, max positions, cooldown, confidence, slippage, hard stop, broker truth priority, `state/brain.json`은 이 문서 작업에서 변경하지 않는다.
- selection 품질, execution/risk, broker truth, performance sync, learning memory 작업은 분리한다.
- PathB profit ladder, pre-close, `AUTO_SELL_REVIEW` HOLD cooldown, broker-truth entry fail-closed, sizing reason split, zero-holding stale reconcile, KIS `remaining_qty` normalization은 보호 영역이다.
- `CLOSED_AUDITED_BROKER_SELL`은 portfolio realized에는 포함할 수 있지만 strategy learning/promotion 판단에는 섞지 않는다.
- KR/US 같은 전략 이름이라도 성과 축을 분리한다. KR strategy 개선이 US PathB momentum/gap/ORP 성과를 훼손하면 안 된다.

## Direct Evidence Snapshot

### DB Evidence

| DB | 확인 내용 | 판단 |
| --- | --- | --- |
| `data/ml/decisions.db` | `v2_learning_performance` row 512, `portfolio_realized` 없음, `strategy_attribution` 없음 | V2 sync 코드는 있으나 live DB에 적용되지 않았다. |
| `data/ml/decisions.db` | `CLOSED_AUDITED_BROKER_SELL` 5건, 전부 `exit_price=NULL`, 전부 `learning_allowed=0` | audited broker sell backfill이 실제 성과 ledger에 반영되지 않았다. |
| `data/audit/candidate_audit.db` | `daily_pending=1551`, `audit_sparse=19716`, `insufficient_samples=62810` | candidate outcome은 아직 fresh learning source가 아니다. |
| `data/audit/candidate_audit.db` | `audit_candidate_rows` 53123건 중 `candidate_source` blank 53055건 | source attribution이 실질적으로 거의 비어 있다. |
| `data/audit/candidate_audit.db` | `primary_bucket` blank 19648건, `actual_prompt_included IS NULL` 33008건 | legacy rows와 최신 visibility contract가 섞여 있다. |
| `data/ticker_selection_log.db` | KR 2026-06-01~2026-06-05 `trade_ready=8`, `signal_fired=0`, `traded=0` | KR 문제는 selection 부족만이 아니라 `trade_ready -> NO_SIGNAL` 축이다. |
| `data/ticker_selection_log.db` | 최근 KR trade_ready 전략: `gap_pullback=5`, `opening_range_pullback=2`, `mean_reversion=1`, 모두 no-signal | 전략별 no-signal 원인 분석이 필요하다. |
| `data/intraday_strategy_log.db` | KR ORP block: `orp_entry_window_expired=395`, `orp_not_formed=174`, `orp_range_too_high=111` | ORP는 entry window timing mismatch 가능성이 크다. |
| `data/v2_event_store.db` | PathB miss quality: US `INVALID_PRICE` 29건, zone reentered 26건, avg 30m MFE +1.222% | execution price diagnostics가 필요하다. 단 broker truth/sizing/slippage 완화로 풀 항목은 아니다. |

### Code Evidence

| 코드 경로 | 확인 내용 | 판단 |
| --- | --- | --- |
| `runtime/pathb_runtime.py::_selection_reconcile_mode()` | market-specific env를 먼저 보고, 없으면 global `PATHB_SELECTION_RECONCILE_MODE`로 fallback | KR reconcile drift는 정상 startup이면 `KR_*` enforce가 우선되어야 한다. |
| `.env.live`, `config/v2_start_config.json` | 현재 파일 값은 `KR_PATHB_SELECTION_RECONCILE_MODE=enforce` | 파일 기준 config는 align되어 있다. |
| `logs/config/effective_config_20260606_024649_live.redacted.json` | runtime effective 값은 `KR_PATHB_SELECTION_RECONCILE_MODE=shadow` | running process 또는 startup path/env 문제다. |
| `trading_bot.py::_prefetch_selection_intraday_evidence()` | `fail_closed_tickers`는 `data_quality != minute_complete` ticker만 포함하고, complete features를 보존한다 | KR/KIS fail-closed 개선은 동작 완화보다 session/provider degraded visibility 보강이 핵심이다. |
| `strategy/opening_range_pullback.py` | 기본 `entry_window_min=60`, `elapsed_min > or_minutes + entry_window_min`이면 `orp_entry_window_expired` | ORP 후보 선정 시각과 entry window 만료 시각을 조인해 확인해야 한다. |

## Priority Overview

| Priority | Work | Type | Why Now |
| --- | --- | --- | --- |
| P0-1 | Runtime config drift 해소 | 운영 적용/검증 | 현재 preflight fail 원인이고, 파일 값과 runtime truth가 다르다. |
| P0-2 | V2 learning performance sync 적용 | DB sync | 성과/학습 ledger가 실제 broker audited close를 반영하지 못한다. |
| P0-3 | 성과 리포트 재계산 | 분석/리포트 | sync 전 리포트는 portfolio realized와 strategy learning 구분이 흐리다. |
| P0-4 | Ticker selection attribution contamination 정리 | DB 감사/분석 | traded row attribution 오염이 learning 판단을 흐린다. |
| P0-5 | Candidate audit source attribution fallback | 코드/테스트 | `candidate_source` 컬럼은 있으나 신규 live audit row 대부분이 blank로 기록된다. |
| P0-6 | Candidate audit outcome freshness 복구 | DB 감사/분석 | `daily_pending=1551` 상태에서는 candidate audit을 selection evidence로 쓰면 안 된다. |
| P0-7 | KR/KIS evidence degraded visibility | 코드/로그 가시성 | ticker-level hard fail-closed와 session/provider degraded warning을 분리해야 운영 판단이 가능하다. |
| P0-8 | KR `trade_ready -> NO_SIGNAL` / ORP timing report | DB/전략 분석 | 최근 KR trade_ready 8건이 모두 no-signal이고, KR live 확장 전 선행 gate다. |
| P0-9 | PathB `INVALID_PRICE` miss diagnostics | DB/execution 분석 | US `INVALID_PRICE` miss가 zone reentry와 30m MFE를 동반하므로 execution 품질을 분리해야 한다. |
| P1-1 | KR trade-ready carry 효과 측정 | DB/운영 측정 | carry 구현은 있으므로 실제 live 분포를 검증해야 한다. |
| P1-2 | KR screener exposure/ranking 분석 | DB/selection 분석 | source attribution이 비어 있고 hard-cap/watch-miss 품질을 아직 평가하지 못한다. |
| P1-3 | Lesson candidate basis metadata | learning memory | refreshed ledger 전 lesson 승격은 오염 위험이 크다. |
| P1-4 | Hold advisor follow-up reporting | 리포트/가시성 | 수익 경로를 건드리지 않고 비용/차단 이유만 보여준다. |
| P2 | KR shadow veto, US loss-cap cluster, US KIS primary, prompt overlays | observe-only | sample gate와 refreshed ledger 전에는 live behavior 변경 금지다. |

## Code-Level Recheck 2026-06-07

### Recheck Result Summary

| Work | Code-Level Status | Revised Development Need |
| --- | --- | --- |
| Runtime config drift | `trading_bot.py::_apply_v2_start_config_env()` and `tools/live_preflight.py::load_effective_config()` both apply `config/v2_start_config.json` live env overrides. `_selection_reconcile_mode()` in `runtime/pathb_runtime.py` already prefers market-specific mode. | No runtime behavior code change first. Restart/startup env/path verification is required. Add code only if a fresh post-restart snapshot still says KR `shadow`. |
| V2 learning performance sync | `tools/sync_v2_learning_performance.py` already has schema columns, migration fields, `audited_broker_backfill`, `portfolio_realized`, and learning exclusion. Existing tests cover audited broker native price/qty and degraded missing qty. | Operational DB backup/dry-run/apply/verify, not new sync logic. |
| Performance reports | Sync can produce the needed `portfolio_realized`, `strategy_attribution`, and `learning_allowed` fields, but reports must use refreshed DB views. | Report/query layer only after sync. Do not change strategy or broker truth logic. |
| Ticker selection attribution | `tools/audit_ticker_selection_attribution.py` is read-only and classifies manual-review/no-touch cases. `ticker_selection_db.py` has a separate execution-row creation path for causal execution evidence. | Do not auto-fix. If remediation is done, add an explicit audited script that calls the existing split/link path only for reviewed rows. |
| Candidate source attribution | `audit/candidate_audit_store.py` has `candidate_source` as an extra column, and live audit write paths pass it through only when source rows provide `candidate_source`/`source`. Recent DB rows have `source_file` populated but `candidate_source` blank. | Actual dev item: standardize source fallback in live audit write paths or store-level extra-column handling, then add tests that new prompt/excluded/filter rows do not write blank `candidate_source`. |
| Candidate audit outcome freshness | `tools/update_candidate_audit_outcomes.py` already supports dry-run, daily forward horizons, non-null preservation, reports, and `daily_pending` status. Tests cover dry-run and daily horizons. | Operational catch-up first. Code only if pending rows remain for reasons the updater cannot explain. |
| KR/KIS evidence degraded visibility | `_prefetch_selection_intraday_evidence()` already preserves `minute_complete` features and only builds fail-closed sentinel rows for non-complete tickers under threshold. Tests cover provider/session fail-closed, partial preservation, and timeout diagnostics. | Actual dev item: add explicit session/provider degraded fields and tests. This is visibility, not fail-closed behavior change. |
| KR `trade_ready -> NO_SIGNAL` / ORP timing | `ticker_selection_log` records `trade_ready`, `signal_fired`, `strategy_name`, and no-submit metadata. `strategy/opening_range_pullback.py` exposes deterministic `orp_entry_window_expired`; `trading_bot.py` logs ORP probes into `intraday_strategy_log` with `entry_window_elapsed_min`. | Promoted to P0 read-only analysis report by strategy/date/source and ORP timing join. No threshold/window change before report output. |
| PathB `INVALID_PRICE` miss quality | `pathb_miss_quality` has US `INVALID_PRICE` rows with high zone reentry and positive 30m MFE. Existing PathB gates split price/sizing/broker-truth reasons. | Add read-only diagnostics/report first. Do not weaken broker-truth fail-closed, sizing reason split, slippage cap, or submit policy. |
| KR trade-ready carry | Carry implementation and tests already exist. | Measurement/reporting only unless measured carry increases wrong outcomes. |

### Test Recheck

The following targeted tests passed during this review:

```powershell
python -m pytest tests/test_v2_learning_performance_sync.py::V2LearningPerformanceSyncTests::test_sync_uses_audited_broker_native_exit_price_and_qty_keys tests/test_v2_learning_performance_sync.py::V2LearningPerformanceSyncTests::test_sync_marks_price_without_qty_as_degraded_and_blocks_learning tests/test_trading_bot_intraday_evidence.py::TradingBotIntradayEvidenceTests::test_session_open_resolve_failure_is_logged_and_fail_closed tests/test_trading_bot_intraday_evidence.py::TradingBotIntradayEvidenceTests::test_fail_closed_below_threshold_does_not_overwrite_partial_store tests/test_trading_bot_intraday_evidence.py::TradingBotIntradayEvidenceTests::test_intraday_evidence_coverage_records_timeout_diagnostics tests/test_live_config_sources.py::LiveConfigSourceTests::test_live_effective_config_has_no_unapproved_conflicts tests/test_candidate_audit.py::CandidateAuditBackfillTests::test_analyze_candidate_audit_reports_actual_prompt_bucket_and_shadow_readiness tests/test_candidate_action_live_mapping.py::CandidateActionLiveMappingTests::test_candidate_audit_marks_only_actual_prompt_as_input_to_claude -q
```

Result: `8 passed, 2 warnings`. The warnings are existing `eventlet`/`distutils` deprecation warnings.

### Reprioritized Development Cut

After code-level recheck, the true development work is narrower than the original backlog:

1. Add a read-only KR no-signal/ORP timing mismatch report.
2. Add a read-only PathB `INVALID_PRICE` miss diagnostics report.
3. Add KR/KIS session degraded visibility fields and tests in the intraday evidence funnel event.
4. Add candidate audit source attribution fallback/standardization for new rows, with tests.
5. Add an optional audited remediation script for ticker-selection execution split/link only after manual review.
6. Extend reports to use refreshed V2 `portfolio_realized`, `strategy_attribution`, and `learning_allowed` fields after sync.

The following are not code-first tasks right now:

- V2 sync application.
- Runtime config drift resolution.
- Candidate outcome catch-up.
- KR trade-ready carry validation.

## P0 Work Items

### P0-1. Runtime Config Drift 해소

#### 현재 근거

- 현재 파일:
  - `.env.live`: `KR_PATHB_SELECTION_RECONCILE_MODE=enforce`
  - `config/v2_start_config.json`: `KR_PATHB_SELECTION_RECONCILE_MODE=enforce`
- 최신 runtime snapshot:
  - `logs/config/effective_config_20260606_024649_live.redacted.json`
  - `written_at=2026-06-06T02:46:49`
  - `KR_PATHB_SELECTION_RECONCILE_MODE=shadow`
  - `PATHB_SELECTION_RECONCILE_MODE=shadow`
  - `US_PATHB_SELECTION_RECONCILE_MODE=enforce`
- 코드상 `_selection_reconcile_mode()`는 `KR_PATHB_SELECTION_RECONCILE_MODE`가 있으면 global shadow보다 KR 값을 우선한다.
- 따라서 현재 증거로는 코드가 runtime 중 shadow로 덮어쓴다기보다, live process가 이전 env/config 또는 다른 startup path로 떠 있을 가능성이 높다.

#### 작업

1. live stack 재시작 전 현재 process, cwd, startup command를 기록한다.
2. live stack 재시작 또는 config refresh 후 `tools/live_preflight.py --mode live --skip-dashboard --json`을 실행한다.
3. 새 `logs/config/effective_config_*_live.redacted.json`에서 KR/US/global reconcile mode를 확인한다.
4. 재시작 후에도 KR이 `shadow`면 다음을 확인한다.
   - `V2_START_CONFIG_DISABLED`
   - `V2_START_CONFIG_PATH`
   - startup script의 working directory
   - scheduler/bot process가 같은 repo와 같은 config를 읽는지
   - 외부 process manager 또는 shell env override

#### Acceptance

- live preflight에서 `config.runtime_snapshot_drift` fail이 사라진다.
- 새 runtime snapshot에서 `KR_PATHB_SELECTION_RECONCILE_MODE=enforce`다.
- global `PATHB_SELECTION_RECONCILE_MODE=shadow`는 유지되어도 된다. KR/US market-specific enforce가 우선이면 정상이다.

#### 하지 말 것

- 원인 확인 없이 `PATHB_SELECTION_RECONCILE_MODE` global 값을 enforce로 바꾸지 않는다.
- live PathB entry gate, risk gate, order sizing을 건드리지 않는다.

#### 검증 명령

```powershell
python tools/live_preflight.py --mode live --skip-dashboard --json
```

### P0-2. V2 Learning Performance Sync 적용

#### 현재 근거

- `v2_learning_performance` row는 512개지만 `portfolio_realized`, `strategy_attribution` 컬럼이 없다.
- `CLOSED_AUDITED_BROKER_SELL` 5건은 모두 `exit_price=NULL`, `learning_allowed=0`이다.
- sync dry-run은 update/insert 대상이 있음을 보여줬다.
- 이 상태에서는 audited broker close가 portfolio realized 손익에 들어가지 않고, strategy learning과 portfolio reporting 구분도 약하다.

#### 작업

1. 적용 전 DB 백업을 만든다.
   - `data/ml/decisions.db`
   - `data/v2_event_store.db`
2. live dry-run을 다시 실행한다.
3. dry-run 결과에서 selected/filled/closed/update/insert/degraded/learning_allowed count를 기록한다.
4. 이상 count가 없으면 live sync를 실행한다.
5. sync 후 schema와 audited broker sell rows를 검증한다.

#### Acceptance

- `v2_learning_performance`에 `portfolio_realized`, `strategy_attribution`이 존재한다.
- `CLOSED_AUDITED_BROKER_SELL` row는 `portfolio_realized=1`, `strategy_attribution='audited_broker_backfill'`, `learning_allowed=0`으로 분리된다.
- native broker fill evidence가 있는 row는 exit price/qty가 채워진다.
- strategy PF, promotion, lesson input은 `learning_allowed=1`만 사용한다.

#### 하지 말 것

- 운영자 승인 없이 `--repair-decisions`를 붙이지 않는다.
- audited broker backfill을 전략 성과로 섞지 않는다.
- sync 중 PathB runtime/order 경로를 수정하지 않는다.

#### 검증 명령

```powershell
python tools/sync_v2_learning_performance.py --market ALL --runtime-mode live --dry-run
python tools/sync_v2_learning_performance.py --market ALL --runtime-mode live
```

### P0-3. 성과 리포트 재계산

#### 현재 근거

- sync 전 DB는 portfolio realized와 strategy learning attribution이 분리되지 않았다.
- `CLOSED_AUDITED_BROKER_SELL`은 실현 손익에는 필요하지만 strategy learning에는 부적합하다.
- KR/US, PathA/PathB, strategy, audited broker backfill을 한 표에서 섞으면 다음 개선 판단이 오염된다.

#### 작업

1. V2 sync 이후 리포트 쿼리를 재실행한다.
2. 최소 다음 view를 분리한다.
   - portfolio realized 전체
   - `strategy_attribution='strategy'`
   - `strategy_attribution='audited_broker_backfill'`
   - `learning_allowed=1`
   - KR/US split
   - PathA/PathB split
3. US PathB profit ladder/pre-close 성과와 KR strategy 성과를 분리한다.

#### Acceptance

- portfolio realized report에는 audited broker backfill이 포함된다.
- strategy learning report에는 audited broker backfill과 `learning_allowed=0`이 제외된다.
- KR 전략 개선 판단에 US PathB 수익 경로가 섞이지 않는다.

### P0-4. Ticker Selection Attribution 정리

#### 현재 근거

- attribution audit 결과에서 traded 48건 중 contaminated 23건이 확인됐다.
- missing execution id 23건, watch_only traded 14건, split review 대상 10건, no-touch 10건이 있다.

#### 작업

1. `watch_only_traded` split 후보 10건을 review한다.
2. time-delta row 3건과 legacy-only `selection_log_id=7742` IREN row를 별도로 본다.
3. causal execution evidence가 있는 경우에만 별도 execution row/link를 만든다.
4. no-touch rows는 자동 보정하지 않고 analysis query에서 제외한다.

#### Acceptance

- 기존 `watch_only` row를 임의로 `trade_ready=1`로 바꾸지 않는다.
- selection row와 execution row가 인과적으로 연결된다.
- legacy no-touch row는 learning/promotion 입력에서 제외된다.

#### 검증 명령

```powershell
python tools/audit_ticker_selection_attribution.py --mode live --market ALL --sample-limit 20
```

### P0-5. Candidate Audit Source Attribution Fallback

#### 현재 근거

- `audit/candidate_audit_store.py`에는 `candidate_source` extra column이 이미 있다.
- live audit write path는 row에 `candidate_source` 또는 `source`가 있을 때만 값을 넘긴다.
- 최근 DB에서 `source_file`은 채워져 있지만 `candidate_source`는 거의 비어 있다.
  - 2026-06-01: 2440/2440 blank
  - 2026-06-02: 3160/3168 blank
  - 2026-06-03: 2664/2666 blank
  - 2026-06-04: 2690/2692 blank
  - 2026-06-05: 1864/1867 blank
- 따라서 문제는 schema 부재가 아니라 신규 audit row write path의 attribution fallback 부재다.

#### 작업

1. live audit write path에서 `candidate_source` fallback 규칙을 표준화한다.
   - 우선순위: `candidate_source` -> `source` -> source tags에서 명시 source -> `source_file` 기반 stage source.
   - prompt/excluded/filter/runtime filter row 모두 같은 규칙을 쓴다.
2. store-level helper 또는 `trading_bot.py::_record_candidate_funnel_snapshot()` 내부 helper로 중복을 줄인다.
3. source가 정말 불명확한 row는 blank 대신 `unknown:<stage>` 같은 분석 가능한 값으로 남긴다.
4. 과거 row bulk 보정은 별도 운영 승인 없이는 하지 않는다. 신규 row 재발 방지가 우선이다.

#### Acceptance

- 신규 `audit_candidate_rows`에서 `candidate_source` blank가 prompt/excluded/filter 주요 write path에서 재발하지 않는다.
- 기존 `source_file` merge/payload preservation 계약을 깨지 않는다.
- `actual_prompt_included`, `final_prompt_included`, `source_tags_json`, `primary_bucket` 기록은 유지된다.
- legacy row 대량 backfill은 별도 audited remediation 없이 자동 실행하지 않는다.

#### 예상 수정 파일

- `trading_bot.py`
- `audit/candidate_audit_store.py` 또는 store helper 주변
- `tests/test_candidate_action_live_mapping.py`
- `tests/test_candidate_audit.py`

#### 검증 명령

```powershell
python -m pytest tests/test_candidate_action_live_mapping.py::CandidateActionLiveMappingTests::test_candidate_audit_marks_only_actual_prompt_as_input_to_claude tests/test_candidate_audit.py::CandidateAuditBackfillTests::test_analyze_candidate_audit_reports_actual_prompt_bucket_and_shadow_readiness -q
```

### P0-6. Candidate Audit Outcome Freshness 복구

#### 현재 근거

- `audit_candidate_outcomes` 상태:
  - `daily_pending=1551`
  - `audit_sparse=19716`
  - `insufficient_samples=62810`
- daily outcome이 pending인 상태에서는 candidate audit DB를 KR selection evidence로 쓰면 안 된다.

#### 작업

1. daily outcome catch-up을 dry-run으로 먼저 실행한다.
2. pending 감소 예상치와 horizon별 count를 확인한다.
3. 이상 없을 때만 update를 실행한다.
4. update 후 `daily_pending` 잔여 row를 horizon/date/source별로 설명한다.

#### Acceptance

- `daily_pending=1551`이 해소되거나 명확한 잔여 사유가 남는다.
- candidate audit outcome이 fresh하지 않으면 selection evidence로 쓰지 않는다.
- `audit_sparse`와 `insufficient_samples`는 learning 승격 근거가 아니라 관찰 상태로 남긴다.

### P0-7. KR/KIS Evidence Degraded Visibility

#### 현재 근거

- 코드상 `fail_closed_tickers`는 `data_quality != minute_complete` ticker만 포함한다.
- `minute_complete` feature는 `complete_features`로 보존된다.
- 따라서 현재 핵심 문제는 complete ticker까지 전부 hard fail-closed로 막는 동작 변경이 아니라, session/provider degraded와 ticker-level fail-closed가 로그에서 섞여 보이는 가시성 문제다.
- `session_evidence_degraded` 같은 명시 필드는 아직 없다.

#### 작업

1. funnel event에 session/provider degraded 필드를 추가한다.
   - 예: `session_evidence_degraded`
   - 예: `degraded_reason`
   - 예: `complete_ticker_preserved_count`
   - 예: `ticker_fail_closed_count`
2. provider disabled, session-open resolve failure, complete 0개는 hard fail-closed로 유지한다.
3. partial timeout은 missing/partial ticker만 hard fail-closed로 표시한다.
4. dashboard/preflight 문구는 "전면 차단"과 "부분 degraded"를 구분한다.

#### Acceptance

- partial KR/KIS timeout에서 `minute_complete` ticker evidence가 confirmed로 남는다.
- missing/partial ticker만 `fail_closed=true`로 보인다.
- 운영자는 provider/session degraded warning과 ticker hard block 수를 분리해서 볼 수 있다.
- broker/order fail-closed 보호 동작은 완화되지 않는다.

#### 예상 수정 파일

- `trading_bot.py`
- 관련 dashboard/preflight visibility 파일
- `tests/test_trading_bot_intraday_evidence.py` 또는 기존 evidence/preflight 테스트

### P0-8. KR `trade_ready -> NO_SIGNAL` / ORP Timing 원인 분석

#### 현재 근거

- 2026-06-01~2026-06-05 KR selection row 1299개 중 `trade_ready=8`.
- 해당 8건은 `signal_fired=0`, `traded=0`이다.
- 전략별 recent trade_ready:
  - `gap_pullback=5`, 모두 no-signal
  - `opening_range_pullback=2`, 모두 no-signal
  - `mean_reversion=1`, no-signal

#### 작업

1. `trade_ready=1 AND signal_fired=0` row를 날짜/전략/시각/소스별로 집계한다.
2. 각 전략의 no-signal 세부 reason을 붙인다.
   - ORP: window expired, range not formed, range too high, pullback too shallow
   - gap_pullback: gap 조건, pullback depth, VWAP/volume confirm
   - momentum: live allowlist, price extension, volume confirm, market phase
   - mean_reversion: overextension, liquidity, reversal confirm
3. selection이 전략 조건을 충족하기 전에 trade_ready를 올리는지, 전략 조건이 지나간 뒤에도 trade_ready를 유지하는지 분리한다.
4. ORP trade_ready row와 `intraday_strategy_log` ORP row를 date/ticker/time 기준으로 조인한다.
5. selection 시각을 before OR formed, inside entry window, near expiry, after expiry로 분류한다.

#### Acceptance

- KR no-signal이 selection 문제인지 strategy timing 문제인지 분리된다.
- threshold/order 변경은 KR 데이터로만 제안하고, 공유 전략 파일 변경 시 US PathB 영향 검토가 붙는다.
- `NO_SIGNAL`을 매수 차단 오류로 오해하지 않는다.
- ORP trade_ready가 window 안에서 나온 것인지, window 밖에서 나온 것인지 수치로 확인된다.
- entry window 확대는 forward outcome 근거가 있을 때만 별도 제안한다.

### P0-9. PathB `INVALID_PRICE` Miss Diagnostics

#### 현재 근거

- `pathb_miss_quality` 기준 US `INVALID_PRICE`는 29건이다.
- 이 중 zone reentered는 26건이다.
- 평균 30m MFE는 +1.222%다.

#### 작업

1. cancel reason, zone reentry, 30m MFE/MAE를 market/session/ticker/path_run_id별로 리포트한다.
2. `INVALID_PRICE`를 price source, stale quote, tick size, native/KRW conversion, order timing bucket으로 분류한다.
3. 동일 ticker/order timing 주변 lifecycle event와 broker truth state를 함께 표시한다.
4. 해결책 제안은 diagnostics 이후 별도 문서로 분리한다.

#### Acceptance

- `INVALID_PRICE`가 실제 가격 조회 실패인지, stale quote인지, 단위 변환/호가 단위 문제인지 분리된다.
- broker truth fail-closed, sizing reason split, slippage cap, order submit policy는 완화되지 않는다.
- missed opportunity와 safety block이 같은 표에서 섞이지 않는다.

## P1 Work Items

### P1-1. KR Trade-Ready Carry 효과 측정

#### 현재 근거

- KR trade-ready carry 구현과 테스트는 이미 존재한다.
- 남은 일은 carry 이후 실제 live 분포를 재는 것이다.

#### 작업

1. 일자별 `trade_ready_count`, `signal_fired`, `traded`, `NO_SIGNAL`, `watch_only -> trade_ready` transition을 측정한다.
2. carry로 trade_ready가 늘었는데 no-signal이 늘면 strategy signal review로 보낸다.
3. signal은 늘었는데 order가 없으면 risk/order/broker gate 분석으로 분리한다.

#### Acceptance

- carry가 selection 개선인지, 단순 no-signal 증가인지 분리된다.
- risk/order 문제와 selection 문제를 같은 패치에서 섞지 않는다.

### P1-2. KR Screener Exposure/Ranking

#### 현재 근거

- source attribution blank가 많아 source별 품질을 평가하기 어렵다.
- prompt cap, hard-cap cutoff, watch miss가 실제 forward outcome을 놓치는지 아직 정량화가 부족하다.

#### 작업

1. hard-cap cutoff 후보의 forward outcome을 계산한다.
2. prompt pool에 들어간 후보와 제외된 후보를 source/bucket/rank별로 비교한다.
3. bounded overlay exposure를 설계하되 BUY_READY 자동 승격은 금지한다.

#### Acceptance

- overlay 후보는 prompt-visible 또는 shadow-visible일 뿐 live BUY_READY로 자동 승격되지 않는다.
- source/rank별 forward outcome 근거가 있어야 exposure를 늘린다.

### P1-3. Lesson Candidate Basis Metadata

#### 현재 근거

- `state/brain.json`은 runtime truth가 아니라 policy memory다.
- performance ledger가 refresh되기 전 lesson 승격은 오염 가능성이 있다.

#### 작업

1. lesson candidate에 다음 metadata를 붙인다.
   - `basis_source`
   - `basis_max_session`
   - `basis_synced_at`
   - `truth_status`
2. refreshed ledger-backed lesson만 `truth_status=fresh`로 둔다.
3. 자동 `state/brain.json` 승격은 하지 않는다.

#### Acceptance

- stale/manual-review lesson이 prompt-visible policy memory로 자동 승격되지 않는다.
- lesson 후보와 runtime truth가 분리된다.

### P1-6. Hold Advisor Follow-Up Reporting

#### 현재 근거

- US PathB pre-close/profit ladder/hold advisor 연동은 수익 경로 보호 대상이다.
- follow-up은 live sell behavior 변경보다 비용, 반복 호출, missed-runup, block reason visibility 중심이어야 한다.

#### 작업

1. `PRE_CLOSE_CARRY` challenge 비용을 측정한다.
2. pending intraday recheck retry state machine은 반복 호출 위험이 확인될 때만 설계한다.
3. missed-runup bucket report를 read-only로 연결한다.
4. US PathB block reporting은 read-only로 노출한다.

#### Acceptance

- hard stop/loss cap/profit ladder/pre-close behavior를 바꾸지 않는다.
- Claude 호출량 증가 가능성이 있으면 throttle/cooldown 설계가 먼저 붙는다.

## P2 Observe-Only Items

| Item | Observe Rule |
| --- | --- |
| KR shadow veto | refreshed performance ledger와 충분한 entry-time-known feature sample 이후 shadow-only로 설계한다. |
| US loss-cap cluster shadow | audited broker backfill을 strategy loss cluster에 섞지 않고 ledger refresh 후 재계산한다. |
| US KIS ranking/intraday primary | KIS smoke/shadow coverage, latency, overlap, rate-limit gate를 통과하기 전 primary 교체 금지다. |
| Prompt overlay / PLAN_A / KR confirmation / first-entry / exit overlay | 충분한 거래일, labeled outcome, concentration check, broker-fill-aware replay 전까지 observe-only다. |
| Preopen continuation shadow | `data/preopen_continuation.db`가 없으므로 code 추가보다 no-Claude shadow collection을 먼저 시작한다. |
| US preopen auto-sell hold advisor policy | code path는 존재하지만 DB policy rows가 없으므로 live behavior 판단보다 shadow/policy row 수집이 먼저다. |

## Execution Order

1. P0-1 runtime config drift를 해소한다.
2. P0-2 V2 sync를 backup/dry-run/apply/verify 순서로 진행한다.
3. P0-3 refreshed ledger로 성과 리포트를 재계산한다.
4. P0-4 ticker selection attribution review와 P0-5 candidate source fallback을 분리해서 정리한다.
5. P0-6 candidate outcome freshness를 복구한다.
6. P0-7 KR/KIS degraded visibility를 코드/로그/테스트로 보강한다.
7. P0-8 KR no-signal/ORP timing report를 작성한다.
8. P0-9 PathB `INVALID_PRICE` miss diagnostics를 작성한다.
9. P1-1 이후 selection carry, screener exposure, lesson metadata, hold advisor reporting을 순차 진행한다.

## Standard Verification Checklist

- Read-only DB query로 전후 count를 남긴다.
- live behavior 변경 전에는 관련 protected boundary 영향 여부를 문서화한다.
- `trading_bot.py`, PathB, dashboard/preflight, DB schema를 건드린 경우 관련 unit/integration test와 `py_compile`을 실행한다.
- live 설정 변경은 `.env.live`와 `config/v2_start_config.json` 양쪽을 확인한다.
- broker truth/order/risk 관련 변경은 live preflight와 보호 영역 테스트를 함께 실행한다.

## Close Criteria

- 각 항목은 plan text가 아니라 DB row count, runtime snapshot, log/preflight output, test result 중 하나로 닫는다.
- P0 항목이 닫히기 전에는 KR live threshold 확대, ORP window 확대, shadow veto enforce, US KIS primary 전환을 하지 않는다.
- 문서와 실제 DB/code가 어긋나면 문서를 수정하지 말고 먼저 runtime truth를 다시 확인한다.
