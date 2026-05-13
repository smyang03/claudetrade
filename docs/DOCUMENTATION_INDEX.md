# Documentation Index - 2026-05-13

## Scope

확인 대상은 저장소 안의 주요 사람이 관리하는 Markdown 문서다. `data/**`와 `.pytest_cache/**`의 Markdown은 실행 산출물로 분리한다.

## Classification Rules

| 분류 | 의미 | 위치 |
| --- | --- | --- |
| 구조/철학 | 시스템 설계, 운영 원칙, 런타임 구성 | repo root, `docs/`, `pathb_v2_live_plan.md`, `v2.md` |
| 개발 완료 | 구현 결과와 QA 요약 | [DEVELOPED_WORK.md](DEVELOPED_WORK.md), `docs/reports/` |
| 할 일 | 아직 해야 할 작업, follow-up, pending 항목 | [TODO_ROADMAP.md](TODO_ROADMAP.md) |
| 운영/QA | live preflight, guardian, 검증 절차 | `LIVE_PREFLIGHT_CHECKLIST.md`, `docs/reports/`, `data/v2_reports/` |
| 실행 산출물 | backtest, simulation, guardian 자동 생성 결과 | `data/backtest_audit/`, `data/v2_reports/` |
| 보관 | 오래된 devlog, debug log, 훈련 로그 | Git history 또는 `docs/reports/` 완료 리포트 |

## Current Cleanup State

2026-05-13 코드 기준 재분류:

- `docs/plans/`의 tracked Markdown 24개를 모두 검토했다.
- 완료된 plan 5개는 [DEVELOPED_WORK.md](DEVELOPED_WORK.md)에 요약을 남기고 삭제했다.
- 미완료 plan과 KIS TODO 2개는 [TODO_ROADMAP.md](TODO_ROADMAP.md)에 우선순위, 사유, 개선 전후 리뷰로 통합했다.
- active 실행 계획은 `TODO_ROADMAP.md` 하나만 기준으로 본다.
- 과거 reports 안의 삭제된 plan 링크는 당시 검증 근거로 남겨 둔다.

## Reading Order

1. 구조를 보려면 [ARCHITECTURE_MAP.md](ARCHITECTURE_MAP.md)를 본다.
2. 지금 할 일을 고르려면 [TODO_ROADMAP.md](TODO_ROADMAP.md)를 본다.
3. 완료된 작업의 요약만 확인하려면 [DEVELOPED_WORK.md](DEVELOPED_WORK.md)를 본다.
4. 문서 목록과 정리 상태는 [DOCUMENTATION_INVENTORY.md](DOCUMENTATION_INVENTORY.md)를 본다.

## Cleanup Policy

- active plan은 `TODO_ROADMAP.md` 하나에 통합한다.
- 완료 요약은 `DEVELOPED_WORK.md`에 남긴다.
- 상세 QA 결과는 `docs/reports/YYYYMMDD_slug.md`로 분리한다.
- 새 상세 plan이 필요하면 임시로 만들 수 있지만, 구현 또는 보류 판단 후 `TODO_ROADMAP.md`로 흡수하고 원본은 삭제한다.
- 자동 생성 리포트는 `data/**` 아래에 유지하고, 사람이 관리하는 문서 목록에는 묶음으로만 표시한다.
