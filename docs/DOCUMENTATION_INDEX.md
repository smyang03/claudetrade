# Documentation Index - 2026-05-02

## Scope

확인 대상은 저장소 안의 모든 `*.md` 파일입니다.

- 전체 Markdown: 223개
- Git tracked Markdown: 59개
- Git untracked Markdown: 1개
- Git ignored Markdown: 163개
- active Markdown: 60개
- Git ignored 실행 산출물/캐시/archive Markdown: 163개

## Classification Rules

| 분류 | 의미 | 위치 |
| --- | --- | --- |
| 구조/철학 | 시스템 설계, 운영 원칙, 런타임 구성 | repo root, `docs/`, `docs/plans/*ARCHITECTURE*` |
| 개발 완료 | 구현 결과, QA 결과, 완료 체크가 있는 작업 | `docs/reports/`, 완료 섹션이 있는 `docs/plans/`, [DEVELOPED_WORK.md](DEVELOPED_WORK.md) |
| 할 일 | 아직 해야 할 작업, follow-up, TODO, pending 항목 | `docs/plans/`, `docs/*TODO*`, [TODO_ROADMAP.md](TODO_ROADMAP.md) |
| 운영/QA | live preflight, 리포트, 검증 절차 | `LIVE_PREFLIGHT_CHECKLIST.md`, `docs/reports/` |
| 실행 산출물 | backtest, simulation, preflight 자동 생성 결과 | `data/backtest_audit/`, `data/v2_reports/` |
| 보관 | 오래된 devlog, debug log, 훈련 로그 | `docs/archive/` |

## Current Git State

2026-05-02 정리 작업 기준으로 수정 또는 신규 상태인 Markdown입니다.

| Git 상태 | 파일 | 정렬 |
| --- | --- | --- |
| Modified | `docs/DEVELOPED_WORK.md` | 완료/QA 목록 최신화 |
| Modified | `docs/TODO_ROADMAP.md` | 완료/해야 할 일 분리, 우선순위, 직접 연관성/진행 판단 추가 |
| Modified | `docs/DOCUMENTATION_INDEX.md` | 문서 인덱스 카운트/상태 갱신 |
| Modified | `docs/DOCUMENTATION_INVENTORY.md` | 전체 MD 인벤토리 갱신 |
| Untracked | `docs/plans/us_extended_hours_screening_plan_20260502.md` | US extended-hours research-only plan |

## Reading Order

1. 구조를 보려면 [ARCHITECTURE_MAP.md](ARCHITECTURE_MAP.md)를 봅니다.
2. 이미 끝난 작업을 보려면 [DEVELOPED_WORK.md](DEVELOPED_WORK.md)를 봅니다.
3. 다음 작업을 고르려면 [TODO_ROADMAP.md](TODO_ROADMAP.md)를 봅니다.
   - 이 문서가 해야 할 일을 중요도순으로 나열하고, 직접 연관성/진행/보류/생략 판단을 포함합니다.
4. 특정 파일의 성격과 상태를 찾으려면 [DOCUMENTATION_INVENTORY.md](DOCUMENTATION_INVENTORY.md)를 봅니다.

## Cleanup Policy

- 완료된 계획 문서는 삭제하지 않고 `DEVELOPED_WORK.md`에서 완료 항목으로 연결합니다.
- 진행해야 하는 항목은 `TODO_ROADMAP.md`에만 남기고, 완료 문서와 중복 TODO로 관리하지 않습니다.
- 새 계획 문서는 `docs/plans/YYYYMMDD_slug.md` 형태를 권장합니다.
- QA 결과가 따로 있으면 `docs/reports/YYYYMMDD_slug.md`로 분리합니다.
- 자동 생성 리포트는 `data/**` 아래에 유지하고, 사람이 관리하는 문서 목록에는 묶음으로만 표시합니다.
- Git 상태가 `Modified` 또는 `Untracked`인 문서는 커밋 전까지 진행 중으로 봅니다.
