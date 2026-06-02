# 운영 모니터링 개선 요구서

작성 일자: 2026-05-30
범위: 2026-05-30 야간 모니터링 후속. live bot 조용한 종료 감지/종료 원인 기록 항목은 제외하고, 운영 경고 품질과 상태 오염 방지 항목만 정의한다.

## 공통 원칙

- 이번 요구서는 운영 가시성, 사후 정리, 사전 검증 범위다.
- PathB 주문 실행, broker truth fail-closed, sizing, zero-holding stale reconcile, KIS order normalization, `RouteDecision`, live config/env 값은 직접 변경하지 않는다.
- broker holdings/open orders/fills는 계속 주문/보유 truth로 사용한다.
- `state/brain.json`은 runtime truth가 아니라 정책 메모리로 취급한다.
- 모든 정리성 기능은 기본 dry-run/read-only로 시작하고, live DB를 바꾸는 apply는 명시 플래그와 감사 로그를 요구한다.

## R-03 Guardian 코드 버전/재시작 반영

### 배경

모니터링 중 `tools/live_preflight.py`의 false WARN 표시를 수정했지만, 이미 떠 있던 `tools/live_guardian.py` 프로세스는 예전 모듈을 계속 사용했다. 그 결과 직접 실행한 preflight는 `PASS`인데 guardian 리포트는 stale logic으로 `us.today_order_unknown_review WARN`을 계속 냈다.

### 코드 변경 요구

- `tools/live_preflight.py`
  - `run_preflight()` 반환 payload에 `code_version` 블록을 추가한다.
  - 필드 예시:
    - `git_head`: `git rev-parse --short HEAD`
    - `git_dirty`: tracked diff 여부
    - `generated_from`: `"tools.live_preflight"`
    - `module_loaded_at`: 프로세스 기준 module import 시각
  - git 명령 실패 시 preflight 자체를 fail하지 말고 `git_head=""`, `git_error`를 data에 남긴다.
- `tools/live_guardian.py`
  - `_write_guardian_heartbeat()`와 `_write_guardian_report()` payload에 guardian 자체 `code_version`을 추가한다.
  - `run_guardian_once()` 리포트에 `preflight.code_version`과 `guardian.code_version`을 모두 보존한다.
  - 현재 checkout의 `git_head`와 guardian import 시점 `git_head`가 다르면 finding code `runtime.guardian_code_stale`을 `WARN/action_required`로 추가한다.
  - 이 경고는 재시작 지시만 제공하고, guardian이 스스로 live bot이나 자기 자신을 재시작하지 않는다.

### 수용 기준

- guardian report JSON/MD에서 guardian/preflight 각각의 code version이 보인다.
- 코드 변경 후 기존 guardian이 돌아가는 상황에서 `runtime.guardian_code_stale`이 확인된다.
- git 정보를 읽지 못하는 환경에서도 preflight/guardian은 정상 실행된다.

### 테스트 요구

- `tests/test_live_guardian.py`
  - code_version이 heartbeat/report에 포함되는지 검증.
  - guardian import head와 current head가 다를 때 `runtime.guardian_code_stale` finding이 생기는지 검증.
- `tests/test_live_config_sources.py` 또는 신규 테스트
  - `run_preflight()` payload에 `code_version`이 포함되는지 검증.

### 검증 명령

```powershell
python -m pytest tests/test_live_guardian.py tests/test_live_config_sources.py -q
python -m py_compile tools/live_guardian.py tools/live_preflight.py
python tools/live_preflight.py --mode live --skip-dashboard --json
```

## R-04 Previous-session ORDER_UNKNOWN 정리 플로우

### 배경

06:02 기준 current-session `ORDER_UNKNOWN=0`이지만 previous-session `ORDER_UNKNOWN=4`가 계속 preflight/guardian 경고로 남았다. broker position/open order exposure는 없었지만 운영 리포트가 계속 오염되어, 실제 신규 주문 불명과 과거 잔여 row가 섞인다.

### 코드 변경 요구

- 신규 도구 후보: `tools/order_unknown_remediation.py`
  - 기본 모드는 `--dry-run`.
  - `--mode live|paper`, `--market KR|US`, `--session-before YYYY-MM-DD`, `--json` 지원.
  - `--apply`는 아래 조건을 모두 만족할 때만 허용한다.
    - fresh broker truth 존재.
    - broker position qty=0.
    - broker open order evidence 없음.
    - local position/pending sell exposure 없음.
    - PathB run plan에 `manual_reconciliation_required` 또는 동등한 감사 flag를 기록할 수 있음.
  - live apply 시 변경 대상 row별 감사 payload를 `data/v2_reports/order_unknown_remediation_<stamp>.json`에 저장한다.
- `tools/live_preflight.py`
  - `db.order_unknown_unresolved` data를 세 그룹으로 분리한다.
    - `current_session_blocking`
    - `previous_session_with_local_exposure`
    - `previous_session_no_local_exposure`
  - `previous_session_no_local_exposure`는 WARN 유지 가능하나 `accepted=false/action_required`가 아니라 `audited_remediation_available`로 구분한다.
- `interface/v2_ops_summary.py`
  - dashboard/Telegram에 노출되는 PathB unknown row에도 `local_exposure`, `broker_position_qty`, `broker_open_order_evidence`, `remediation_allowed=false|true`를 포함한다.

### 보호 영역 주의

- 자동 closed 처리나 broker truth 무시 금지.
- `runtime/pathb_runtime.py`의 reconcile 경로를 우회하지 않는다.
- apply 도구는 운영자 명시 실행 전까지 DB 변경을 하지 않는다.

### 수용 기준

- current-session ORDER_UNKNOWN은 계속 강한 경고로 남는다.
- previous-session no-exposure row는 별도 그룹으로 보여 실제 blocking 리스크와 구분된다.
- dry-run 출력만으로 어떤 evidence 때문에 apply 가능/불가인지 판단할 수 있다.
- apply 실행 후에는 변경 row, 이전 status/plan, 이후 status/plan, broker truth timestamp가 감사 파일에 남는다.

### 테스트 요구

- 신규 `tests/test_order_unknown_remediation.py`
  - fresh broker truth + exposure 없음이면 dry-run에 `remediation_allowed=true`.
  - broker position/open order/local exposure가 있으면 apply 불가.
  - `--apply` 없이 DB가 변경되지 않음.
  - apply 시 감사 payload가 생성되고 plan metadata가 merge됨.
- 기존 관련 테스트 재실행:
  - `tests/test_live_preflight_pathb_conflicts.py`
  - `tests/test_order_unknown_reconciliation.py`
  - `tests/test_pathb_sell_reconcile_backfill.py`

### 검증 명령

```powershell
python -m pytest tests/test_order_unknown_remediation.py tests/test_live_preflight_pathb_conflicts.py tests/test_order_unknown_reconciliation.py tests/test_pathb_sell_reconcile_backfill.py -q
python -m py_compile tools/order_unknown_remediation.py tools/live_preflight.py interface/v2_ops_summary.py
python tools/order_unknown_remediation.py --mode live --market US --session-before 2026-05-29 --dry-run --json
python tools/live_preflight.py --mode live --skip-dashboard --json
```

## R-05 Preflight/대시보드 경고 기준 정교화

### 배경

SMCI는 PathB run이 이미 `CLOSED`인데 raw lifecycle `ORDER_UNKNOWN` event가 남아 있어 `us.today_order_unknown_review WARN`으로 표시됐다. 운영 경고는 raw event history가 아니라 현재 미해결 상태와 broker/local exposure 기준이어야 한다.

### 코드 변경 요구

- `tools/live_preflight.py`
  - `_ops_summary_checks()`는 `lifecycle.order_unknown` 대신 `path_b_live.order_unknown`만 운영 경고로 사용한다.
  - 이미 반영된 변경을 helper 함수로 분리한다.
    - 예: `_unresolved_order_unknown_rows(summary: dict) -> list[dict]`
  - helper docstring에 raw lifecycle event와 현재 PathB state의 차이를 명시한다.
- `interface/v2_ops_summary.py`
  - `lifecycle.order_unknown`은 "event history"로 이름/설명 명확화.
  - `path_b_live.order_unknown`은 "current unresolved PathB rows" 의미로 유지.
  - 필요하면 payload에 `order_unknown_event_history_count`와 `current_order_unknown_count`를 분리해 노출한다.
- `dashboard/dashboard_server.py`
  - PathB unknown badge/count는 `path_b_live.order_unknown` 기준만 사용한다.
  - lifecycle raw event count는 진단/히스토리 탭으로만 노출한다.

### 수용 기준

- 복구된 SMCI처럼 과거 ORDER_UNKNOWN event만 남은 closed run은 preflight WARN으로 뜨지 않는다.
- 현재 DB row status가 `ORDER_UNKNOWN`인 run은 계속 WARN으로 뜬다.
- dashboard count와 preflight count가 같은 기준을 사용한다.

### 테스트 요구

- 이미 추가된 `tests/test_live_preflight_ops_summary.py`
  - recovered raw lifecycle event 무시.
  - unresolved PathB row는 WARN.
- 추가 후보:
  - `tests/test_dashboard_pathb.py`에 dashboard count가 `path_b_live.order_unknown`만 세는지 검증.
  - `tests/test_v2_phase6.py`에 lifecycle event history와 current PathB order_unknown count 분리 검증.

### 검증 명령

```powershell
python -m pytest tests/test_live_preflight_ops_summary.py tests/test_dashboard_pathb.py tests/test_v2_phase6.py -q
python -m py_compile tools/live_preflight.py interface/v2_ops_summary.py dashboard/dashboard_server.py
python tools/live_preflight.py --mode live --skip-dashboard --json
```

## R-06 ML decisions DB schema 사전 차단

### 배경

live 시작 후 `v2_learning_performance.experiment_bucket` column 부재 warning이 발생했다. 주문 실행에는 직접 영향이 없었지만, 성과/학습/리포트 기록 축이 조용히 빠질 수 있다.

### 코드 변경 요구

- `ml/db_writer.py`
  - 현재 `init_db()` 내부 migration 정보를 외부 read-only 검사에서 재사용할 수 있게 공개 helper로 분리한다.
  - 후보:
    - `required_schema_columns() -> dict[str, dict[str, str]]`
    - `schema_missing_columns(path: Path) -> dict[str, list[str]]`
  - read-only helper는 DB를 열어 `PRAGMA table_info`만 수행하고 write/migration은 하지 않는다.
- `tools/live_preflight.py`
  - `_ml_db_health_checks()`에서 `ml.db_writer.schema_missing_columns(data/ml/decisions.db)`를 호출한다.
  - missing column이 있으면 `FAIL` 또는 `WARN(action_required)`로 승격한다.
  - mode=live에서는 schema missing을 `blocked_if_live_start` 성격으로 분류한다.
  - detail에 실행 가능한 remediation을 명시한다.
    - 예: `python -c "from ml.db_writer import init_db; init_db()"`
- `ml/db_health.py`
  - schema health에도 같은 missing column 정보를 포함한다.

### 수용 기준

- live bot 시작 전 preflight에서 missing schema가 잡힌다.
- schema가 정상이면 기존 known gap warning만 유지한다.
- read-only preflight는 DB schema를 변경하지 않는다.

### 테스트 요구

- `tests/test_ml_db_writer_paths.py`
  - 기존 table에 missing column이 있을 때 `init_db()`가 migration하는지 유지.
  - read-only helper가 missing column을 보고하지만 DB를 변경하지 않는지 추가.
- 신규 또는 기존 live preflight 테스트
  - `_ml_db_health_checks()`가 missing schema를 action_required로 반환하는지 검증.

### 검증 명령

```powershell
python -m pytest tests/test_ml_db_writer_paths.py tests/test_v2_learning_performance_sync.py -q
python -m pytest tests/test_live_preflight_ops_summary.py tests/test_live_config_sources.py -q
python -m py_compile ml/db_writer.py ml/db_health.py tools/live_preflight.py
python tools/live_preflight.py --mode live --skip-dashboard --json
```

## R-07 state/brain.json 변경 감시

### 배경

`state/brain.json`은 runtime truth는 아니지만 Claude 판단에 들어가는 정책 메모리다. live 중 승인/감사 없이 바뀌면 selection/hold 판단이 왜 바뀌었는지 추적이 어렵다.

### 코드 변경 요구

- `tools/live_preflight.py`
  - 신규 check 후보: `_brain_memory_change_check(mode: str)`.
  - 확인 대상:
    - `state/brain.json` 존재 여부.
    - git tracked dirty 여부 또는 파일 hash 변화.
    - `meta.version`, `meta.last_updated`.
    - 승인 큐 (`learning.approval_queue.BrainApprovalQueue`) pending count.
  - live mode에서 uncommitted `state/brain.json` 변경이 있으면 `WARN(action_required)`로 노출한다.
  - 단, preflight는 brain 내용을 자동 수정하지 않는다.
- `claude_memory/brain.py`
  - `save()` 호출 경로에 optional audit hook을 추가하는 방안을 검토한다.
  - audit target 예시: `state/brain_change_audit.jsonl`
  - 최소 필드:
    - timestamp
    - caller/module
    - previous_version
    - next_version
    - changed_markets
    - approved_by 또는 `approval_missing`
  - 승인형 workflow 안정화 전까지 자동 정책 승격 경로를 새로 만들지 않는다.
- `interface/v2_ops_summary.py`
  - dashboard brain 섹션에 `brain_dirty`, `brain_version`, `pending_approval_count`를 표시한다.

### 수용 기준

- live preflight에서 uncommitted `state/brain.json` 변경이 명확히 보인다.
- pending approval과 직접 brain dirty 상태가 구분된다.
- brain check는 runtime 주문/보유 truth에 영향을 주지 않는다.
- brain 파일을 읽을 수 없는 경우에도 preflight 전체가 crash하지 않고 check-level WARN/FAIL로만 반환한다.

### 테스트 요구

- 신규 `tests/test_brain_memory_preflight.py`
  - clean brain은 PASS.
  - dirty brain은 live mode WARN/action_required.
  - missing/unparseable brain은 check-level WARN/FAIL.
  - approval queue pending count가 payload에 포함됨.
- `tests/test_brain_execution_integrity.py`
  - brain이 runtime truth로 사용되지 않는 기존 계약 유지 확인.

### 검증 명령

```powershell
python -m pytest tests/test_brain_memory_preflight.py tests/test_brain_execution_integrity.py -q
python -m py_compile tools/live_preflight.py claude_memory/brain.py interface/v2_ops_summary.py
python tools/live_preflight.py --mode live --skip-dashboard --json
```

## 권장 구현 순서

1. R-05: preflight/dashboard 경고 기준 정교화. 이미 일부 반영됐고 false WARN을 즉시 줄인다.
2. R-04: previous-session ORDER_UNKNOWN dry-run/remediation 분리. 운영 경고 오염을 줄인다.
3. R-06: ML schema 사전 차단. live 시작 후 warning을 줄인다.
4. R-03: guardian/preflight code version. 감시 프로세스 stale 여부를 보이게 한다.
5. R-07: brain 변경 감시. 정책 메모리 변경 추적성을 높인다.

## 완료 정의

- 각 항목은 기능별 독립 PR/커밋으로 분리한다.
- PR 본문에는 변경 파일, 보호 영역 영향 없음 또는 예외 여부, 실행 테스트, config/env 영향, 남은 위험을 기록한다.
- live DB를 바꾸는 remediation 항목은 dry-run 결과와 apply 감사 파일을 함께 첨부한다.
