# Execution Advisor 구현/검증 리포트

작성일: 2026-06-02

## 이전 내용 요약

- KR은 US PathB와 같은 전략군에서도 성과가 다르게 나와, 전략 자체보다 데이터 파이프라인과 시장별 분리 검증이 우선 과제로 정리됐다.
- KR flow `all_zero_cluster`는 주식 문제가 아니라 조회 타이밍/API 반환 문제 가능성이 커서, KR 전략 확대 전 flow/T-1 검증을 선행하는 방향으로 정리했다.
- 이번 Execution Advisor는 그 KR 개선과 별개로, 수동 정정/브로커 실제 체결가/기존 PathB 계획가가 어긋난 경우를 감지하기 위한 별도 read-only advisor다.
- 실제 수동 정정 케이스는 매도 완료로 요구서/테스트에서 제거했고, 동일 패턴은 합성 `MANUAL_MISMATCH` fixture로 고정했다.
- Claude 비용은 기본 0으로 설계했다. 운영 기본은 `EXEC_ADVISOR_CLAUDE_ENABLED=false`, 이후 manual/mismatch 케이스에서만 cap/cooldown 뒤 fake 또는 실제 Claude를 연결한다.

## 구현 범위

요구서: `docs/reports/execution_advisor_requirements_20260602.md`

구현 완료:

- Phase 0: 순수 판정 엔진
  - `execution/execution_advisor.py`
  - KIS 호출 없음, 파일 I/O 없음, Claude/Anthropic import 없음
  - `MANUAL_MISMATCH`, HPE, DELL conservative, BBY sell limit, stale broker truth, broker zero holding fixture 검증
- Phase 1: runtime read-only audit hook
  - `runtime/execution_advisor_runtime.py`
  - `trading_bot.py::run_entry_scan()` 상단 hook
  - 기존 `_entry_scan_interval_sec()` gate와 별개로 advisor 자체 `EXEC_ADVISOR_CHECK_INTERVAL_SEC=60` throttle
  - 동일 signature audit event는 `EXEC_ADVISOR_EVENT_COOLDOWN_MINUTES=15`로 중복 저장을 제한
  - 주문/cancel/sell API 호출 없음
- Phase 2: manual-only Claude skeleton
  - `EXEC_ADVISOR_CLAUDE_ENABLED=false` 기본값
  - fake client 테스트만 구현
  - cooldown/day cap/state 파일: `state/<mode>_execution_advisor_state.json`
  - Claude 응답은 audit payload에 저장만 가능, 주문과 연결 없음
- Phase 3: 제외
  - 실제 reprice/cancel/sell/buy 실행 없음

## 코드 변경 요약

- `execution/execution_advisor.py`: advisor 판정/metric/Claude gate 순수 로직 추가
- `runtime/execution_advisor_runtime.py`: broker truth + event store read-only runtime 추가
- `tools/simulate_execution_advisor.py`: 운영 DB/snapshot 기반 read-only 시뮬레이터 추가
- `lifecycle/models.py`: `EXECUTION_ADVISOR_DECISION` event type 추가
- `lifecycle/event_store.py`: `NON_STATUS_EVENT_TYPES`로 `QUALITY_MARKED`, `EXECUTION_ADVISOR_DECISION` status 오염 방지
- `trading_bot.py`: Execution Advisor runtime import/init 및 `run_entry_scan()` hook 추가
- `.env.example`: `EXEC_ADVISOR_*` 기본값 추가
- `tests/test_execution_advisor.py`, `tests/test_execution_advisor_runtime.py`: Phase 0~2 회귀 테스트 추가

## MD 대조 결과

- Phase 0 pure simulation: 일치
- Phase 1 disabled/read-only 운영 기본값: 일치
- Phase 1 hook 위치: `run_entry_scan()` 상단으로 일치
- Phase 2 Claude disabled/manual-only/cooldown/cap: 일치
- Phase 3 실제 주문 실행 제외: 일치
- `EXECUTION_ADVISOR_DECISION` 비상태 이벤트 처리: 일치
- env 키 이름: 1차 구현에서 sell-limit env 이름이 MD와 달랐고, 최종 수정 완료
  - 최종 사용: `EXEC_ADVISOR_SELL_KEEP_LIMIT_GAP_PCT`
  - 최종 사용: `EXEC_ADVISOR_SELL_PROFIT_KEEP_MIN_PCT`
- 추가 재검토 보완: 1분 hook에서 동일 advisor event가 반복 저장될 수 있어 `EXEC_ADVISOR_EVENT_COOLDOWN_MINUTES=15` dedupe를 추가했다.
- 실제 종목 참조 제거: 일치. 코드/테스트는 `MANUAL_MISMATCH` 합성 fixture만 사용한다.

누락/차이:

- dashboard/Telegram 표시는 이번 Phase 0~2 범위에 넣지 않았다.
- 실제 Claude API smoke test는 수행하지 않았다. 요구서대로 운영자 요청 전까지 fake client만 사용했다.
- Phase 3 주문 실행은 구현하지 않았다.

## 검증 결과

단계별 검증:

- `python -m pytest tests/test_execution_advisor.py -q` -> 7 passed
- `python -m pytest tests/test_execution_advisor_runtime.py -q` -> 6 passed
- `python -m pytest tests/test_execution_advisor.py tests/test_execution_advisor_runtime.py -q` -> 13 passed
- `python -m py_compile execution/execution_advisor.py runtime/execution_advisor_runtime.py tools/simulate_execution_advisor.py lifecycle/models.py lifecycle/event_store.py trading_bot.py` -> passed

보호 주변 검증:

- `python -m pytest tests/test_auto_sell_claude_gate.py::AutoSellClaudeGateTests::test_pathb_loss_cap_hold_respects_reask_cooldown -q` -> passed
- `python -m pytest tests/test_broker_truth_snapshot.py -q` -> 24 passed
- `python -m pytest tests/test_v2_phase1.py -q` -> 11 passed
- `python -m pytest tests/test_v2_quality_audit.py -q` -> 3 passed
- `python -m pytest tests/test_pathb_foundation.py -q` -> 2 passed
- `python -m pytest tests/test_v2_learning_performance_sync.py::V2LearningPerformanceSyncTests::test_sync_quality_recalculation_overrides_provisional_quality_mark -q` -> passed

전체 QA:

- `python -m pytest -q` -> 2115 passed, 2 skipped, 2 warnings
- 추가 spot-check:
  - `.env.live` / `config/v2_start_config.json`에 `EXEC_ADVISOR_*` 활성 설정 없음
  - 새 advisor 경로에 `kis_api`, `Anthropic`, 실제 주문 submit/cancel 호출 없음
  - `git diff --check` -> whitespace error 없음

운영 read-only 테스트:

- `python tools/simulate_execution_advisor.py --market US --mode live --json`
  - 주문 0건
  - Claude 0건
  - event append 0건
  - latest 결과: HPE/CRWV/NOK `KEEP_PLAN`, EL/ORCL `BROKER_RECONCILE_REQUIRED`
- `python tools/simulate_execution_advisor.py --market KR --mode live --json`
  - 주문 0건
  - Claude 0건
  - event append 0건
  - advisor 대상 active execution surface 없음
- `python tools/live_preflight.py --mode live --skip-dashboard --json`
  - `ok=true`, `fail_count=0`
  - 기존 운영 경고: ORCL current-session `ORDER_UNKNOWN` 1건이 PathB entry blocker로 남아 있음

## 보호 영역 영향 재검토

- `runtime/pathb_runtime.py` 수정 없음
- PathB sizing reason split 수정 없음
- PathB AUTO_SELL_REVIEW HOLD cooldown guard 수정 없음
- broker-truth fail-closed entry gate 수정 없음
- zero-holding stale reconcile 수정 없음
- KIS order normalization 수정 없음
- `state/brain.json` 직접 수정 없음
- 공유 전략 파일(`strategy/momentum.py`, `strategy/gap_pullback.py`) 수정 없음

이번 변경은 별도 advisor 모듈과 audit event 추가다. 보호 계약 완화가 아니므로 별도 `MD 위반 사항`은 없다.

## 이후 해야 할 일

1. ORCL `ORDER_UNKNOWN` 1건 수동/감사 정리
   - preflight 기준 현재 PathB entry blocker다.
   - DB 상태만 보고 닫지 말고 broker positions/open orders/fills 대조 후 audited remediation 사용.

2. 1세션 shadow 관찰
   - `.env.live` 또는 운영 override에서 `EXEC_ADVISOR_ENABLED=true`
   - `EXEC_ADVISOR_SHADOW_ONLY=true`
   - `EXEC_ADVISOR_CLAUDE_ENABLED=false`
   - 목표: `EXECUTION_ADVISOR_DECISION` 이벤트가 과도하게 쌓이지 않는지, 정상 자동 흐름이 `KEEP_PLAN`으로 남는지 확인

3. manual/mismatch 발생 시 fake 또는 실제 Claude 1회 smoke
   - `EXEC_ADVISOR_CLAUDE_ENABLED=true`
   - `EXEC_ADVISOR_MANUAL_ONLY_CLAUDE=true`
   - `EXEC_ADVISOR_MAX_CLAUDE_CALLS_PER_DAY=5`
   - 실제 Claude API smoke는 운영자 요청 시 1회 이하로 제한

4. dashboard/Telegram 표시는 후속 Phase로 분리
   - read-only 이벤트 요약만 표시
   - 주문/정정 버튼은 추가하지 않는다.

5. Phase 3는 별도 승인 전까지 금지
   - reprice/cancel/sell/buy 실행은 아직 구현하지 않는다.
   - Phase 3 요구 시 주문 API 영향, broker truth freshness, cooldown, idempotency, rollback 문서를 별도 작성한다.

6. KR 개선 축은 별도 진행
   - flow T-1 endpoint go/no-go 확인
   - evidence coverage 원인 확인
   - KOSPI/high-liquidity 우선도와 KR claude_price micro-probe는 flow 검증 후 재평가
