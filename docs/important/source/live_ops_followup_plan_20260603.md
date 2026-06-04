# Live Ops Follow-up Plan - 2026-06-03

작성일: 2026-06-03
관련 커밋: `3f1e768 fix: restore live broker truth safety guards`

## 완료된 수정

- `tools/live_guardian.py`: `broker_position_qty`가 없으면 `broker_position_count`만으로 previous-session PathB holding을 `accepted_exception` 처리하지 않도록 수정.
- `tools/broker_truth_scheduler.py`: broker truth refresh 기본 주기를 `2분`으로 낮춰 `ttl_sec=180` 이내로 유지.
- `start_live_stack.bat`: live stack의 broker truth scheduler 실행 플래그를 `--refresh-interval-min 2 --ttl-sec 180`로 동기화.
- `config/v2_start_config.json`: `US_EARLY_ENTRY_SOFT_GATE_END_MIN=60`으로 복구해 US early-entry soft gate 0~60분 보호 범위 복원.
- `audit/candidate_audit_store.py`: INTEGER boolean audit extra column의 빈 문자열 입력을 `NULL`이 아니라 `0`으로 기록하도록 복구.

## 검증 완료

- `python -m pytest tests/test_live_guardian.py tests/test_broker_truth_scheduler.py tests/test_candidate_audit.py tests/test_watchdog_launcher.py -q`
  - 결과: `86 passed`
- `python -m py_compile tools/live_guardian.py tools/broker_truth_scheduler.py audit/candidate_audit_store.py`
  - 결과: 통과
- `python -m pytest tests/test_candidate_action_live_mapping.py::CandidateActionLiveMappingTests::test_candidate_audit_live_write_preserves_action_and_discovery_metadata -q`
  - 결과: `1 passed`
- `python tools/live_preflight.py --mode live --skip-dashboard --json`
  - 결과: `ok=true`, `fail_count=0`

## 남은 작업

### P1. 운영 반영 확인

- 현재 live bot/runtime snapshot이 재시작 전 값인 `US_EARLY_ENTRY_SOFT_GATE_END_MIN=30`을 볼 수 있다.
- live stack 또는 관련 프로세스 재시작 후 `US_EARLY_ENTRY_SOFT_GATE_END_MIN=60`과 broker truth scheduler `--refresh-interval-min 2`가 실제 runtime에 반영됐는지 확인한다.
- 확인 명령:
  - `python tools/live_preflight.py --mode live --skip-dashboard --json`
- 기대 상태:
  - `config.runtime_snapshot_drift`에서 `US_EARLY_ENTRY_SOFT_GATE_END_MIN` drift가 사라진다.
  - effective config에 `US_EARLY_ENTRY_SOFT_GATE_END_MIN=60`이 표시된다.

### P1. Broker Truth Freshness 복구

- preflight 기준 broker truth stale warning이 남아 있었다.
- 새 scheduler 설정은 stale 구간을 줄이는 수정이며, 현재 stale snapshot 자체는 refresh/restart 후 재확인이 필요하다.
- 확인 명령:
  - `python tools/broker_truth_scheduler.py --mode live --markets KR,US --once --force --ttl-sec 180 --json`
  - `python tools/live_preflight.py --mode live --skip-dashboard --json`
- 기대 상태:
  - KR/US broker truth snapshot이 fresh로 평가된다.
  - guardian/dashboard/preflight에서 stale broker truth fail-closed 경고가 반복되지 않는다.

### P2. PathB Hold-advisor Bridge Gain-floor 적용 여부 판단

- 보류 이슈: `runtime/pathb_runtime.py`의 일반 hold-advisor bridge(`apply_general_hold_advice_policy`) target extension 경로가 profit-ladder/target triage와 달리 gain floor elevation 및 too-close 재검증을 적용하지 않는다.
- 이 영역은 US PathB 수익 경로와 hold advisor 보호 영역에 인접하므로 즉시 수정하지 않았다.
- 진행 조건:
  - 운영자가 gain-floor 적용 필요성을 승인한다.
  - 작업 설명/커밋 메시지/PR 본문 중 하나에 `MD 위반 사항` 섹션을 남긴다.
- 최소 검증:
  - `tests/test_pathb_profit_protection.py`
  - `tests/test_auto_sell_claude_gate.py`
  - 관련 `runtime/pathb_runtime.py` py_compile
  - 필요 시 `tests/test_pathb_runtime.py`의 policy/exit-scan 인접 테스트

### P3. Cleanup 후보

- `runtime/pathb_runtime.py` gain-floor `too_close` 중복 조건 정리.
- `trading_bot.py` candidate audit `freshness_verdict`가 prompt-pool과 excluded/not-evaluated branch에서 서로 다른 source를 쓰는 문제 정리.
- `tools/broker_truth_scheduler.py` lazy import 구조는 유지하되, 필요하면 테스트 mock 편의성을 더 명시적으로 문서화.

### P3. 워크트리 정리

- 현재 커밋에 포함하지 않은 변경/산출물이 남아 있다.
  - `state/brain.json`
  - `docs/reports/overnight_us_monitor_*`
  - 루트의 `-` 파일
- 이 항목들은 이번 안전 수정 커밋과 무관하므로 별도 검토 후 보존/삭제/커밋 여부를 결정한다.

## 주의 사항

- `state/brain.json`은 runtime truth가 아니며 승인형 워크플로우 없이 직접 수정하지 않는다.
- PathB broker-truth fail-closed, PathB sizing reason split, hold advisor target/profit ladder 정책은 보호 영역이다.
- 보호 영역을 수정해야 하는 경우 변경 전/후 동작, 주문/리스크/broker truth/Claude/config/env 영향, 실행 테스트, 남은 위험을 `MD 위반 사항`으로 기록한다.
