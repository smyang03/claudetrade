# Documentation Index - 2026-05-08

## Scope

확인 대상은 저장소 안의 주요 사람이 관리하는 Markdown 문서입니다. `data/**`와 `.pytest_cache/**`의 Markdown은 실행 산출물로 분리합니다.

## Classification Rules

| 분류 | 의미 | 위치 |
| --- | --- | --- |
| 구조/철학 | 시스템 설계, 운영 원칙, 런타임 구성 | repo root, `docs/`, `docs/plans/*ARCHITECTURE*` |
| 개발 완료 | 구현 결과와 QA 요약 | [DEVELOPED_WORK.md](DEVELOPED_WORK.md), `docs/reports/` |
| 할 일 | 아직 해야 할 작업, follow-up, TODO, pending 항목 | [TODO_ROADMAP.md](TODO_ROADMAP.md), 남아 있는 `docs/plans/`, `docs/*TODO*` |
| 운영/QA | live preflight, guardian, 검증 절차 | `LIVE_PREFLIGHT_CHECKLIST.md`, `docs/reports/`, `data/v2_reports/` |
| 실행 산출물 | backtest, simulation, guardian 자동 생성 결과 | `data/backtest_audit/`, `data/v2_reports/` |
| 보관 | 오래된 devlog, debug log, 훈련 로그 | `docs/archive/` |

## Current Cleanup State

2026-05-08 코드 기준 재분류:

- `docs/plans/` 현재 Markdown 17개 중 13개는 active/보류, 4개는 삭제/정리 후보입니다.
- `p0_pathb_fill_dashboard_followup_20260503.md`와 `p0_post_isolation_qa_expansion_20260502.md`는 focused QA가 green으로 확인되어 삭제 후보로 이동했습니다.
- `entry_risk_control_development_20260508.md`는 구현 완료와 focused QA green으로 확인되어 삭제 후보로 이동했습니다.
- `TRADING_IMPROVEMENT_WORKLOG_20260421.md`는 과거 worklog라 archive/delete 후보입니다.
- 상세 완료 이력은 Git history 또는 [DEVELOPED_WORK.md](DEVELOPED_WORK.md)의 요약으로 확인합니다.
- 현재 우선순위/장단점/리스크 분석과 삭제 후보 목록은 [TODO_ROADMAP.md](TODO_ROADMAP.md)를 기준으로 봅니다.

## Reading Order

1. 구조를 보려면 [ARCHITECTURE_MAP.md](ARCHITECTURE_MAP.md)를 봅니다.
2. 지금 할 일을 고르려면 [TODO_ROADMAP.md](TODO_ROADMAP.md)를 봅니다.
3. 완료된 작업의 요약만 확인하려면 [DEVELOPED_WORK.md](DEVELOPED_WORK.md)를 봅니다.
4. 특정 파일의 성격과 상태를 찾으려면 [DOCUMENTATION_INVENTORY.md](DOCUMENTATION_INVENTORY.md)를 봅니다.

## Cleanup Policy

- 완료된 계획 문서는 `docs/plans/`에 계속 쌓지 않고 삭제합니다.
- 완료 요약은 `DEVELOPED_WORK.md`에 남기고, 실행 가능한 다음 작업은 `TODO_ROADMAP.md`에만 남깁니다.
- QA가 실패한 plan은 완료로 보지 않고 `docs/plans/`에 유지합니다.
- 새 계획 문서는 `docs/plans/YYYYMMDD_slug.md` 형태를 권장합니다.
- QA 결과가 따로 있으면 `docs/reports/YYYYMMDD_slug.md`로 분리합니다.
- 자동 생성 리포트는 `data/**` 아래에 유지하고, 사람이 관리하는 문서 목록에는 묶음으로만 표시합니다.
