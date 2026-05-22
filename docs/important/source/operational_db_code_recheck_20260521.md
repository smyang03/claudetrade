# 운영 DB truth 코드 레벨 재검토

- 작성일: 2026-05-21
- 범위: 코드 경로 재검토. 운영 DB 직접 수정/재집계 없음.
- 대상: `trading_bot.py`, `runtime/pathb_runtime.py`, `ml/db_writer.py`, `tools/sync_v2_learning_performance.py`, `tools/backfill_candidate_audit.py`, `audit/candidate_audit_store.py`, `strategy/adaptive_params.py`, `dashboard/dashboard_server.py`, `ticker_selection_db.py`, 관련 테스트.

## 결론

이전 DB 진단의 핵심 방향은 코드상으로도 맞다. `data/ml/decisions.db`는 현재 실체결 truth가 아니라 전략 신호/평가 로그에 가깝고, 실제 체결/청산 truth는 `v2_event_store.db`에서 파생되는 `v2_canonical_performance` 쪽이 더 일관된 구조다.

다만 표현은 조금 정정해야 한다. PathB 체결 시 legacy ML 업데이트가 완전히 호출되지 않는 것은 아니고, PathB 주문 객체에 `decision_id=-1` sentinel이 들어가면서 `_ml_update_filled(-1, "FILLED")`가 호출될 수 있다. 하지만 `ml.db_writer.update_filled()`는 `decision_id <= 0`이면 즉시 return하므로 실제 `decisions` row는 갱신되지 않는다.

개선 방향은 그대로 가져가도 된다. 단, 한 번에 전부 바꾸기보다 link 누락처럼 범위가 좁고 검증이 쉬운 부분부터 고치고, 성과 truth 전환처럼 영향 범위가 큰 변경은 별도 단계로 분리하는 것이 안전하다.

## 확인된 코드 근거

### 1. PathB 주문은 legacy `decisions.id`와 연결되지 않는다

`runtime/pathb_runtime.py`의 PathB live buy pending order는 legacy `decision_id`에 `-1`을 넣고, V2 id는 별도 필드에 저장한다.

- `runtime/pathb_runtime.py:2095` `decision_id = -1`
- `runtime/pathb_runtime.py:2096` `v2_decision_id = plan.decision_id`
- `runtime/pathb_runtime.py:6033` PathB metadata attach도 `v2_decision_id`만 보강한다.

체결 확인 경로는 V2 lifecycle에는 정상 기록한다.

- `trading_bot.py:15549-15563` pending order fill 시 `FILLED` lifecycle event를 `v2_decision_id`로 기록
- `trading_bot.py:21217-21222` websocket fill path도 legacy ML update를 `matched["decision_id"]`로 시도

하지만 legacy update 함수는 양수 id가 아니면 아무 것도 하지 않는다.

- `ml/db_writer.py:208-214` `update_filled()`가 `decision_id <= 0`에서 return
- `ml/db_writer.py:226-240` `update_trade_outcome()`도 같은 방식으로 양수 id만 처리

따라서 PathB 체결은 V2 lifecycle/canonical에는 남지만, legacy `decisions.filled`에는 반영되지 않는 구조다.

### 2. PathA는 legacy row와 V2 id를 둘 다 갖는 반면 PathB는 그렇지 않다

PathA 주문 흐름은 `_ml_write_eval()`로 legacy `BUY_SIGNAL` row를 만들고, 동시에 V2 decision id를 확보한다.

- `trading_bot.py:24674-24689` `_ml_decision_id = _ml_write_eval(..., "BUY_SIGNAL", ...)`
- `trading_bot.py:24692-24705` `_v2_decision_id` 확보
- `trading_bot.py:24793-24808` pending order에 `decision_id`와 `v2_decision_id`를 함께 저장

이 구조 때문에 PathA는 legacy repair/update가 가능한 반면, PathB는 별도 legacy row 생성 정책이 없으면 `decisions.db`로 체결 truth를 복원하기 어렵다.

### 3. V2 canonical sync는 truth 경로로 설계되어 있다

`tools/sync_v2_learning_performance.py`는 `v2_learning_performance`, `v2_canonical_performance`, `v2_decision_fill_links`를 만든다.

- `tools/sync_v2_learning_performance.py:73-109` `v2_canonical_performance` schema
- `tools/sync_v2_learning_performance.py:118-137` `v2_decision_fill_links` schema
- `tools/sync_v2_learning_performance.py:1007-1084` event store의 `v2_decisions`/`lifecycle_events`를 읽어 canonical rows와 link rows 생성

legacy repair는 보조 기능이다.

- `tools/sync_v2_learning_performance.py:667-685` lifecycle payload에서 legacy decision id 후보 탐색
- `tools/sync_v2_learning_performance.py:779-833` legacy row 매칭
- `tools/sync_v2_learning_performance.py:836-890` matched row만 repair
- `tools/sync_v2_learning_performance.py:1105-1106` CLI `--repair-decisions`

주의점: `tools/v2_daily_loop.py:82-90`의 daily sync는 `repair_decisions=True`를 넘기지 않는다. 따라서 daily loop는 canonical sync를 수행하지만 legacy `decisions` repair는 별도 명령으로 실행해야 한다.

### 4. 아직 legacy `decisions.db`를 성과 truth처럼 읽는 코드가 남아 있다

`strategy/adaptive_params.py`는 `data/ml/decisions.db`를 직접 읽고 `BUY_SIGNAL`의 `pnl_pct` 또는 `forward_1d`로 성과 통계를 계산한다.

- `strategy/adaptive_params.py:23` `_DB = data/ml/decisions.db`
- `strategy/adaptive_params.py:61-76` `decisions`에서 `pnl_pct`/`forward_1d` 조회

대시보드 ML digest도 legacy `decisions` 기반이다.

- `dashboard/dashboard_server.py:4708-4776` `decisions`의 `BUY_SIGNAL`, `filled`, `pnl_pct`, `forward_1d` 집계

이 두 경로는 PathB 실체결을 과소 반영할 수 있다. live 성과/학습 지표는 `v2_canonical_performance` 또는 `v2_learning_performance`를 우선해야 한다.

### 5. candidate audit live link 저장 누락은 코드상 재확인된다

`_record_decision_event()`는 이벤트에 V2 id를 넣는다.

- `trading_bot.py:26312` `v2_decision_id`
- `trading_bot.py:26313` `v2_execution_id`

하지만 `_candidate_audit_update_from_decision_event()`가 candidate row에 반영하는 값에는 `execution_decision_id`가 없다.

- `trading_bot.py:26228` `execution_link_source`만 초기화
- `trading_bot.py:26260-26276` entry/exit 가격, PnL, 타이밍 snapshot만 업데이트
- `trading_bot.py:26280-26287` `store.update_execution_by_ticker(..., values=values, latest_only=True)`

반대로 store와 backfill은 link 필드를 이미 지원한다.

- `audit/candidate_audit_store.py:110-112` `execution_link_source`, `execution_decision_id`, `execution_event_id` schema
- `audit/candidate_audit_store.py:747-793` `update_execution_by_ticker()` allowed fields에 link 필드 포함
- `tools/backfill_candidate_audit.py:793-801` V2 lifecycle backfill이 `execution_decision_id`, `execution_event_id` 저장

즉 live path만 link 필드를 채우지 않아 `filled_count`/`pnl_pct`는 있는데 `execution_decision_id`가 빈 candidate row가 생길 수 있다.

### 6. candidate audit 중복 row는 설계상 가능하며 latest view 사용이 맞다

store는 동일 ticker/session에 여러 call-level row를 남기고, latest view를 제공한다.

- `audit/candidate_audit_store.py:405-419` `audit_candidate_latest_rows` view
- `dashboard/dashboard_server.py:7880-7883` summary API는 기본 `latest_only=true`
- `dashboard/dashboard_server.py:8066-8069` rows API도 기본 `latest_only=true`

따라서 분석/대시보드 기본값은 올바른 방향이다. 다만 dashboard candidate audit API는 현재 `execution_decision_id`, `execution_event_id`, `execution_link_source`를 응답에 노출하지 않는다. 링크 복구 후 운영자가 확인하려면 응답 필드에 추가하는 것이 좋다.

### 7. `backfill_candidate_audit.py` 기본 reset 위험은 사실이다

함수 기본값과 CLI mapping상 `--no-reset`을 주지 않으면 대상 세션을 먼저 지운다.

- `tools/backfill_candidate_audit.py:992-1006` `reset_session=True` 기본값, true면 `store.clear_session()`
- `tools/backfill_candidate_audit.py:1091` CLI `--no-reset`
- `tools/backfill_candidate_audit.py:1100` `reset_session=not args.no_reset`

운영 DB 대상으로는 반드시 백업 후 실행하거나 `--no-reset`을 기본 운영 명령으로 고정해야 한다.

### 8. ticker_selection_log link gap은 PathB 범위에서 자연스럽다

PathA 주문 흐름은 ticker selection row에 V2 decision id를 기록한다.

- `trading_bot.py:24720-24734` `tsdb.update_traded()` 또는 `insert_execution_row_from_selection()`에 `execution_decision_id=_v2_decision_id`
- `ticker_selection_db.py:315-360` `update_traded()`가 `execution_decision_id`를 저장

반면 `runtime/pathb_runtime.py`에는 `ticker_selection_db.update_traded()` 호출이 보이지 않는다. PathB가 selection log를 실행 truth로 쓰지 않는 설계라면 괜찮지만, ticker selection DB에서 PathB 체결까지 추적하려면 별도 execution row/link write가 필요하다.

## 개선 적용 순서

### 1단계: candidate audit live link 저장

가장 먼저 적용할 변경이다. 이미 DB 컬럼과 backfill 경로가 존재하므로 코드 변경 범위가 작고, 기존 운영 truth를 바꾸지 않는다.

- `trading_bot.py::_candidate_audit_update_from_decision_event`
- `execution_decision_id = event["v2_decision_id"]` 우선 저장
- lifecycle `event_id`가 들어오는 경로가 있으면 `execution_event_id` 저장
- `tests/test_candidate_action_live_mapping.py`에 `v2_decision_id` 입력과 `execution_decision_id` assertion 추가

### 2단계: dashboard candidate audit link 필드 노출

1단계 변경과 backfill 결과를 운영 화면/API에서 바로 확인할 수 있게 한다.

- `/api/candidate-audit/rows` 응답에 `execution_link_source`, `execution_decision_id`, `execution_event_id` 추가
- summary 집계는 기존 latest view 기본값을 유지

### 3단계: live 성과 truth를 V2 canonical로 이동

영향 범위가 크므로 별도 PR/변경 단위로 진행하는 것이 맞다.

- `strategy/adaptive_params.py`: legacy `decisions` 직접 조회보다 `v2_canonical_performance`/`v2_learning_performance` 우선
- `dashboard/dashboard_server.py::_ml_db_digest()`: legacy digest임을 명확히 하거나 canonical digest를 별도/우선 표시
- `decisions.forward_1d`는 신호 품질 보조 지표로 유지하되 체결 성과 truth와 섞지 않음

### 4단계: PathB legacy 정책 고정

현재 권장 방향은 PathB를 legacy `decisions.db`에 억지로 맞추지 않고 canonical-only truth로 정리하는 것이다.

- legacy row 자동 생성은 보류
- legacy repair는 보조/호환 기능으로 유지
- dashboard/adaptive가 legacy `filled`를 체결 truth로 읽지 않도록 제거하는 것이 핵심

### 5단계: backfill reset 기본값 안전화

운영 명령에서는 즉시 `--no-reset`을 표준으로 쓰되, 코드 기본값 변경은 마지막에 검토한다.

- 먼저 문서와 운영 커맨드에서 `--no-reset` 고정
- 이후 기존 스크립트/테스트 의존성 확인
- 필요하면 `reset_session=False` 기본값 또는 명시적 `--reset` 플래그로 전환

## 위험도별 권장 수정

### P0: candidate audit live link 저장

작은 코드 변경으로 현재 누락을 막을 수 있다.

- 위치: `trading_bot.py::_candidate_audit_update_from_decision_event`
- 내용:
  - `event["v2_decision_id"]` 또는 `kwargs["v2_decision_id"]`를 `execution_decision_id`에 저장
  - `execution_event_id`는 현재 INTEGER 컬럼이므로 lifecycle `event_id`가 있을 때만 저장
  - `v2_execution_id`/order number는 별도 TEXT 컬럼을 추가하거나 payload에 보존
- 테스트:
  - `tests/test_candidate_action_live_mapping.py`의 decision event 테스트에 `v2_decision_id` 입력과 `execution_decision_id` assertion 추가

### P0.5: dashboard candidate audit link 필드 노출

`/api/candidate-audit/rows` 응답에 다음 필드를 추가하면 backfill/live link 복구 상태를 바로 볼 수 있다.

- `execution_link_source`
- `execution_decision_id`
- `execution_event_id`

### P1: live 성과/학습 truth를 canonical으로 이동

- `strategy/adaptive_params.py`의 live performance source를 `v2_canonical_performance`/`v2_learning_performance` 우선으로 변경
- `dashboard/dashboard_server.py::_ml_db_digest()`는 이름을 legacy digest로 명확히 바꾸거나 canonical digest를 별도/우선 표시
- `decisions.forward_1d`는 신호 품질 보조 지표로 남기고, 체결 성과 지표와 섞지 않는 편이 안전하다.

### P1: PathB legacy 정책 결정

둘 중 하나로 명확히 고정해야 한다.

1. 권장: legacy `decisions.db`를 PathB execution truth에서 제외하고 V2 canonical만 사용한다.
2. 대안: PathB order sent 시 최소 legacy `BUY_SIGNAL` row를 만들고, lifecycle payload에 `legacy_decision_id`를 넣어 repair 가능하게 한다.

중간 상태, 즉 `decision_id=-1` sentinel을 유지하면서 dashboard/adaptive가 legacy `filled`를 체결 truth로 읽는 구조가 가장 위험하다.

### P2: backfill 기본값 안전화 검토

운영 안전 관점에서는 `backfill_candidate_audit()` 기본값을 `reset_session=False`로 바꾸고, 완전 재빌드는 명시적 `--reset`/`--reset-session` 플래그로 실행하는 편이 안전하다. 다만 기존 스크립트가 reset 기본값에 의존할 수 있으므로 테스트와 운영 문서 동시 수정이 필요하다.

## 테스트 갭

- `_record_decision_event(..., v2_decision_id=...)`가 candidate audit row의 `execution_decision_id`를 채우는 테스트가 없다.
- PathB order/fill이 legacy `decisions.db`를 갱신하지 않는 현상을 고정하는 회귀 테스트가 없다. 정책상 canonical-only라면 "legacy가 갱신되지 않아도 dashboard/adaptive가 canonical을 본다"는 테스트가 더 중요하다.
- `sync_v2_learning_performance`는 repair/link 테스트가 많은 편이지만, `v2_daily_loop.py`가 repair를 실행하지 않는다는 운영 계약을 대시보드/문서가 명확히 반영하는 테스트는 없다.

## 최종 판단

코드 기준의 1차 수정 대상은 `candidate_audit` live link 누락이다. 이 문제는 저장 필드가 이미 존재하고 backfill도 같은 필드를 채우므로 수정 비용이 작다. 이어서 dashboard candidate audit API에 link 필드를 노출하면 live/backfill 복구 상태를 바로 검증할 수 있다.

더 큰 구조 문제는 legacy `decisions.db`를 체결 성과 truth처럼 사용하는 잔여 코드다. PathB 운영이 계속되는 한 live 성과/학습/대시보드는 V2 canonical을 기준으로 옮겨야 하며, legacy decisions는 전략 신호와 forward label 보조 로그로 격하하는 것이 맞다.

## 구현 업데이트 2026-05-21

보고서의 1~5단계를 코드에 반영했다. 운영 DB는 직접 수정하지 않았고, 검증은 테스트 DB/임시 DB와 읽기형 preflight 중심으로 수행한다.

### 반영 완료

1. candidate audit live link 저장
   - `trading_bot.py::_candidate_audit_update_from_decision_event()`가 decision event의 `v2_decision_id`를 `execution_decision_id`로 저장한다.
   - `execution_event_id`는 정수형 값이 명시적으로 들어온 경우에만 저장한다.
   - 검증: `tests/test_candidate_action_live_mapping.py`에 assertion 추가.

2. dashboard candidate audit link 필드 노출
   - `/api/candidate-audit/rows` 응답에 `execution_link_source`, `execution_decision_id`, `execution_event_id`를 추가했다.
   - 검증: `tests/test_dashboard_candidate_audit_api.py`에 rows API assertion 추가.

3. live 성과 truth canonical 우선
   - `strategy/adaptive_params.py`는 `v2_canonical_performance`의 live closed 성과가 있으면 이를 우선 사용하고, 없으면 기존 legacy `decisions` 로직으로 fallback한다.
   - `dashboard/dashboard_server.py::_ml_db_digest()`는 canonical table이 있으면 fill/outcome truth를 `v2_canonical_performance` 기준으로 표시하고, legacy 수치는 `legacy_filled`, `legacy_with_outcome`으로 보존한다.
   - dashboard 화면 문구는 `V2 truth`/`legacy` source를 표시한다.
   - 검증: `tests/test_adaptive_params_canonical.py`, `tests/test_dashboard_refresh_performance.py`.

4. PathB legacy 정책
   - PathB legacy `BUY_SIGNAL` row 자동 생성은 추가하지 않았다.
   - 개선 방향은 canonical-only truth 유지로 고정했다.
   - dashboard/adaptive가 legacy `filled`만 체결 truth로 보는 위험은 위 3단계 변경으로 줄였다.

5. backfill reset 기본값 안전화
   - `tools/backfill_candidate_audit.py`의 함수 기본값을 `reset_session=False`로 변경했다.
   - CLI는 no-reset을 기본으로 하고, 세션 삭제가 필요한 완전 재빌드는 명시적 `--reset`으로 실행한다.
   - 기존 `--no-reset`은 호환용으로 유지했다.
   - 검증: `tests/test_candidate_audit.py::CandidateAuditBackfillTests::test_backfill_candidate_audit_default_does_not_clear_session`.

### 보고서 대비 차이점

- 원 보고서는 3단계 canonical truth 전환을 별도 PR/변경 단위로 권장했지만, 이번 구현에서는 fallback 방식으로 좁게 반영했다. canonical table이 없거나 비어 있으면 기존 legacy behavior가 유지된다.
- 원 보고서는 backfill 기본값 변경을 마지막 검토 항목으로 두었지만, 운영 안전을 위해 이번 변경에 포함했다. 대신 `--no-reset` 호환 플래그를 남겨 기존 운영 명령은 깨지지 않게 했다.
- `v2_execution_id`/order number는 candidate audit schema에 TEXT 컬럼이 없으므로 이번 변경에서는 저장하지 않았다. 필요하면 별도 schema migration으로 `execution_order_id` 같은 TEXT 컬럼을 추가하는 것이 맞다.

### QA 결과

- `python -m py_compile trading_bot.py dashboard/dashboard_server.py strategy/adaptive_params.py tools/backfill_candidate_audit.py`
- `python -m pytest tests/test_candidate_action_live_mapping.py -q` → 57 passed
- `python -m pytest tests/test_dashboard_candidate_audit_api.py -q` → 11 passed
- `python -m pytest tests/test_candidate_audit.py::CandidateAuditBackfillTests -q` → 22 passed
- `python -m pytest tests/test_dashboard_refresh_performance.py -q` → 30 passed
- `python -m pytest tests/test_adaptive_params_canonical.py -q` → 1 passed

### 남은 운영 확인

- 실제 주문이 발생할 수 있는 `trading_bot.py --live` 실행은 하지 않는다.
- 운영 테스트는 `live_preflight`, sync dry-run, backfill 임시 DB/드라이런 수준으로 제한한다.

### 운영 검증 결과

- `python tools/sync_v2_learning_performance.py --market ALL --runtime-mode live --dry-run --repair-decisions`
  - `dry_run=true`, `written=0`, `canonical_written=0`
  - selected 373, filled 130, closed 117
  - decision links: matched 4, unmatched 369, dry-run repair candidates 2
  - quality grades: CLEAN 28, DIRTY 2, LEGACY_UNKNOWN 240, SUSPECT 103
- `python tools/live_preflight.py --mode live --skip-dashboard --json`
  - `ok=true`, `fail_count=0`, `warn_count=8`
  - warnings:
    - `config.runtime_snapshot_drift`: latest runtime config snapshot differs from files
    - `kis.balance_probe`: direct balance API avoided by default preflight
    - `runtime.bot_pid_lock`: pid lock active and process appears alive
    - `runtime.dashboard_pid_lock`: pid lock active and process appears alive
    - `data.price_csv_integrity.kr`: no active blocking issues, but flat/short CSV warnings exist
    - `data.price_csv_integrity.us`: no active blocking issues, but flat/short CSV warnings exist
    - `ml.decisions_db_health`: accepted known gap
    - `external_data.readiness`: external DB has zero production data rows

운영 preflight 기준으로 이번 변경 때문에 live 실행을 막는 신규 fail은 확인되지 않았다.
