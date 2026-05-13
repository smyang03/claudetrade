# Documentation Inventory - 2026-05-13

## Summary

| 묶음 | 개수 | 상태 | 메모 |
| --- | ---: | --- | --- |
| Repository root Markdown | 6 | tracked | 구조/운영 진입점 |
| `docs/` root Markdown | 9 | tracked | 문서 허브, 단일 TODO, 완료 요약, 리포트성 문서 |
| `docs/plans/` Markdown | 0 | 정리 완료 | active plan은 `docs/TODO_ROADMAP.md`로 통합 |
| `docs/reports/` Markdown | 63 | tracked/untracked 혼재 | 완료 QA/분석/시뮬레이션 리포트 |
| `audit/` Markdown | 3 | tracked | 감사/핫픽스 산출물 |
| generated/ignored Markdown | 다수 | ignored | `data/**`, `.pytest_cache/**` 등 실행 산출물 |

## Root Documents

| 파일 | 분류 | 메모 |
| --- | --- | --- |
| `README.md` | 구조/진입점 | 프로젝트 개요 |
| `CLAUDE.md` | 운영 철학/규칙 | PEAD/manual review TODO 포함 |
| `DATA.md` | 구조/데이터 | 상태/로그/DB 설명 |
| `LIVE_PREFLIGHT_CHECKLIST.md` | 운영/QA | live 실행 전 체크리스트 |
| `v2.md` | 구조/설계 | V2 production design |
| `pathb_v2_live_plan.md` | 구조/계획 | PathB live reference |

## Docs Root

| 파일 | 분류 | 메모 |
| --- | --- | --- |
| `docs/README.md` | 구조/진입점 | 문서 허브 |
| `docs/DOCUMENTATION_INDEX.md` | 구조/인덱스 | 분류 기준과 cleanup policy |
| `docs/ARCHITECTURE_MAP.md` | 구조 | 런타임/상태 저장소 지도 |
| `docs/DEVELOPED_WORK.md` | 완료 요약 | 삭제한 완료 plan과 통합 삭제 범위 |
| `docs/TODO_ROADMAP.md` | 단일 active TODO | 미완료 plan 우선순위, 사유, 개선 전후 리뷰 |
| `docs/DOCUMENTATION_INVENTORY.md` | 인벤토리 | 현재 문서 목록 |
| `docs/trading_process.md` | 구조/운영 | 매매 프로세스 |
| `docs/rsi_threshold_research.md` | 연구 | RSI 기준 |
| `docs/claude_selection_compact_output_report_20260512.md` | 리포트 | compact output 검토 |

## Consolidated Plan Sources

다음 원본은 삭제하고 [TODO_ROADMAP.md](TODO_ROADMAP.md)에 흡수했다.

| 묶음 | 원본 |
| --- | --- |
| KIS / fill truth | `docs/KIS_API_TODO.md`, `docs/KIS_WS_FILL_SYNC_PLAN.md` |
| `docs/plans/` 전체 | tracked Markdown 24개 |

## Reports

`docs/reports/`는 완료 QA, 분석, 시뮬레이션 결과를 보존한다. 일부 report는 삭제된 과거 plan 경로를 historical reference로 언급한다.

## Audit

| 파일 | 분류 | 메모 |
| --- | --- | --- |
| `audit/encoding_mojibake_report_20260501.md` | 감사 산출물 | health check 정책으로 제한 |
| `audit/market_analysis_tune_prompt_audit_20260501.md` | 분석/TODO | prompt/breadth 개선 근거는 TODO로 흡수 |
| `audit/priority_hotfix_improvement_plan_20260501.md` | 완료 성격 핫픽스 | 상세 plan은 완료 요약으로 대체 |
