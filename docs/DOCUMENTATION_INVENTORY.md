# Documentation Inventory - 2026-05-05

## Summary

| 묶음 | 개수 | 상태 | 메모 |
| --- | ---: | --- | --- |
| Repository root Markdown | 6 | tracked | 구조/운영 진입점 |
| `docs/` root Markdown | 10 | tracked | 문서 허브, TODO, 완료 요약, KIS 문서 |
| `docs/plans/` active plans | 12 | tracked | 미완료/보류/QA 실패 plan만 유지 |
| `docs/reports/` reports | 10 | tracked | 완료 QA/분석 리포트 |
| `audit/` Markdown | 3 | tracked | 감사/핫픽스 산출물 |
| `docs/archive/` Markdown | 4 | ignored | 오래된 devlog/debug log |
| generated/ignored Markdown | 979 | ignored | `data/**`, `.pytest_cache/**` 등 실행 산출물 |

## Root Documents

| 파일 | 분류 | 메모 |
| --- | --- | --- |
| `README.md` | 구조/진입점 | 프로젝트 개요 |
| `CLAUDE.md` | 운영 철학/규칙 | PEAD/manual review TODO 포함 |
| `DATA.md` | 구조/데이터 | 상태/로그/DB 설명 |
| `LIVE_PREFLIGHT_CHECKLIST.md` | 운영/QA | live 실행 전 체크리스트 |
| `v2.md` | 구조/설계 | V2 production design |
| `pathb_v2_live_plan.md` | 구조/계획 | PathB live plan reference |

## Docs Root

| 파일 | 분류 | 메모 |
| --- | --- | --- |
| `docs/README.md` | 구조/진입점 | 문서 허브 |
| `docs/DOCUMENTATION_INDEX.md` | 구조/인덱스 | 분류 기준과 cleanup policy |
| `docs/ARCHITECTURE_MAP.md` | 구조 | 런타임/상태 저장소 지도 |
| `docs/DEVELOPED_WORK.md` | 완료 요약 | 삭제한 완료 plan 범위와 남은 완료 리포트 |
| `docs/TODO_ROADMAP.md` | 우선순위 리포트 | 미완료 과제의 장단점/리스크 분석 |
| `docs/DOCUMENTATION_INVENTORY.md` | 인벤토리 | 현재 문서 목록 |
| `docs/trading_process.md` | 구조/운영 | 매매 프로세스 |
| `docs/rsi_threshold_research.md` | 연구 | RSI 기준 |
| `docs/KIS_API_TODO.md` | 할 일 | KIS API 보완 목록 |
| `docs/KIS_WS_FILL_SYNC_PLAN.md` | 구현/운영 검증 | 로컬 구현 완료, 실수신 검증 남음 |

## Active Plans

| 파일 | 분류 | 현재 판단 |
| --- | --- | --- |
| `docs/plans/kr_us_live_ops_qa_20260427.md` | P0 운영 QA | guardian `BLOCK_START` 해소 전까지 active |
| `docs/plans/p0_pathb_fill_dashboard_followup_20260503.md` | P0 QA 실패 | PathB focused QA 2건 실패로 active |
| `docs/plans/p0_post_isolation_qa_expansion_20260502.md` | P0 QA 실패 | PathB runtime contract 재확인 필요 |
| `docs/plans/order_equity_reconciliation_improvement_20260429.md` | P0/P1 follow-up | KIS 수동 확인, dashboard/ops 후속 |
| `docs/plans/MODULARIZATION.md` | P1/P3 구조 | RiskManager KR/US 분리만 우선, 나머지는 보류 |
| `docs/plans/execution_audit_observability_plan_20260430.md` | P1 관찰 | exit/candidate/opening 관찰 보강 |
| `docs/plans/us_extended_hours_screening_plan_20260502.md` | P2 shadow/research | preopen/extended-hours 5~10세션 관찰 필요 |
| `docs/plans/DUAL_RUNTIME_ARCHITECTURE.md` | P3 장기 구조 | 모듈화 이후 보류 |
| `docs/plans/BRAIN_TRAIN_TODO.md` | P3 보류 | 학습용 거래 모드, 지금은 미적용 |
| `docs/plans/PLAN_intraday_strategy_roadmap.md` | P3 전략 | 운영 안전 이후 재평가 |
| `docs/plans/PLAN_momentum_opening_gate.md` | P3 전략 | prompt/data 품질 이후 재평가 |
| `docs/plans/TRADING_IMPROVEMENT_WORKLOG_20260421.md` | 보관/참고 | 대부분 최신 TODO로 흡수, 필요 시 새 plan으로 재작성 |

## Reports

| 파일 | 분류 |
| --- | --- |
| `docs/reports/brain_json_postmortem_cleanup_20260430.md` | 완료 QA |
| `docs/reports/claude_call_review_20260429.md` | 분석 리포트 |
| `docs/reports/claude_usage_quality_optimization_20260430.md` | 분석 리포트 |
| `docs/reports/historical_candidate_execution_review_20260420_20260429.md` | 분석 리포트 |
| `docs/reports/live_improvement_simulation_20260501_live_improvement.md` | 시뮬레이션 리포트 |
| `docs/reports/rule_simulation_20260420_20260429.md` | 시뮬레이션 리포트 |
| `docs/reports/shadow_audit_gap_20260430.md` | 분석 리포트 |
| `docs/reports/shadow_audit_qa_20260430.md` | QA 리포트 |
| `docs/reports/shadow_audit_report_live_20260430_KR.md` | live audit 리포트 |
| `docs/reports/soft_exit_arbitration_qa_20260430.md` | QA 리포트 |

## Audit

| 파일 | 분류 | 메모 |
| --- | --- | --- |
| `audit/encoding_mojibake_report_20260501.md` | 감사 산출물 | health check 정책으로 제한 |
| `audit/market_analysis_tune_prompt_audit_20260501.md` | 분석/TODO | prompt/breadth 개선 근거 |
| `audit/priority_hotfix_improvement_plan_20260501.md` | 완료 성격 핫픽스 | 상세 plan은 완료 요약으로 대체 |
