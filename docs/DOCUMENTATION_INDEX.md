# Documentation Index - 2026-05-01

## Scope

확인 대상은 저장소 안의 모든 `*.md` 파일입니다.

- 정리 전 전체 Markdown: 208개
- 새 정리 문서 추가 후 전체 Markdown: 213개
- active Markdown: 56개
- Git ignore 대상 실행 산출물/캐시/archive Markdown: 157개

## Classification Rules

| 분류 | 의미 | 위치 |
| --- | --- | --- |
| 구조/철학 | 시스템 설계, 운영 원칙, 런타임 구성 | repo root, `docs/`, `docs/plans/*ARCHITECTURE*` |
| 개발 완료 | 구현 결과, QA 결과, 완료 체크가 있는 작업 | `docs/reports/`, 완료 섹션이 있는 `docs/plans/` |
| 할 일 | 아직 해야 할 작업, follow-up, TODO, pending 항목 | `docs/plans/`, `docs/*TODO*` |
| 운영/QA | live preflight, 리포트, 검증 절차 | `LIVE_PREFLIGHT_CHECKLIST.md`, `docs/reports/` |
| 실행 산출물 | backtest, simulation, preflight 자동 생성 결과 | `data/backtest_audit/`, `data/v2_reports/` |
| 보관 | 오래된 devlog, debug log, 훈련 로그 | `docs/archive/` |

## Current Git State

현재 Markdown 기준으로 수정 또는 신규 상태인 파일입니다.

| Git 상태 | 파일 | 정렬 |
| --- | --- | --- |
| Modified | `docs/README.md` | 새 문서 허브 |
| Modified | `docs/KIS_WS_FILL_SYNC_PLAN.md` | 구현/로컬 QA 완료, 운영 검증 남음 |
| Modified | `docs/plans/claude_quality_contract_improvement_20260501.md` | 진행 중/부분 적용 |
| Untracked | `audit/encoding_mojibake_report_20260501.md` | 감사 산출물 |
| Untracked | `audit/priority_hotfix_improvement_plan_20260501.md` | 완료 성격의 운영 핫픽스 계획 |
| Untracked | `docs/ARCHITECTURE_MAP.md` | 새 구성도 |
| Untracked | `docs/DEVELOPED_WORK.md` | 새 완료 목록 |
| Untracked | `docs/DOCUMENTATION_INDEX.md` | 새 문서 인덱스 |
| Untracked | `docs/DOCUMENTATION_INVENTORY.md` | 새 전체 인벤토리 |
| Untracked | `docs/TODO_ROADMAP.md` | 새 할 일 목록 |
| Untracked | `docs/plans/brain_issue_pattern_id_contiguity_20260501.md` | 완료/QA 기록 |
| Untracked | `docs/plans/brain_runtime_state_review_fix_20260501.md` | 완료/QA 기록 |
| Untracked | `docs/plans/recovery_and_followup_todo_20260501.md` | TODO/후속 작업 |
| Untracked | `docs/plans/trading_decision_contract_improvement_20260501.md` | 완료/QA 기록 + deferred follow-up |

## Reading Order

1. 구조를 보려면 [ARCHITECTURE_MAP.md](ARCHITECTURE_MAP.md)를 봅니다.
2. 이미 끝난 작업을 보려면 [DEVELOPED_WORK.md](DEVELOPED_WORK.md)를 봅니다.
3. 다음 작업을 고르려면 [TODO_ROADMAP.md](TODO_ROADMAP.md)를 봅니다.
4. 특정 파일의 성격과 상태를 찾으려면 [DOCUMENTATION_INVENTORY.md](DOCUMENTATION_INVENTORY.md)를 봅니다.

## Cleanup Policy

- 완료된 계획 문서는 삭제하지 않고 `DEVELOPED_WORK.md`에서 완료 항목으로 연결합니다.
- 새 계획 문서는 `docs/plans/YYYYMMDD_slug.md` 형태를 권장합니다.
- QA 결과가 따로 있으면 `docs/reports/YYYYMMDD_slug.md`로 분리합니다.
- 자동 생성 리포트는 `data/**` 아래에 유지하고, 사람이 관리하는 문서 목록에는 묶음으로만 표시합니다.
- Git 상태가 `Modified` 또는 `Untracked`인 문서는 커밋 전까지 진행 중으로 봅니다.
