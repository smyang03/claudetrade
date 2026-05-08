# Documentation Inventory - 2026-05-08

## Summary

| 묶음 | 개수 | 상태 | 메모 |
| --- | ---: | --- | --- |
| Repository root Markdown | 6 | tracked | 구조/운영 진입점 |
| `docs/` root Markdown | 10 | tracked | 문서 허브, TODO, 완료 요약, KIS 문서 |
| `docs/plans/` Markdown | 17 | 15 tracked + 2 untracked | 13 active/보류, 4 삭제/정리 후보 |
| `docs/reports/` reports | 15 | 10 tracked + 5 untracked | 완료 QA/분석/시뮬레이션 리포트 |
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
| `pathb_v2_live_plan.md` | 구조/계획 | PathB live plan reference |

## Docs Root

| 파일 | 분류 | 메모 |
| --- | --- | --- |
| `docs/README.md` | 구조/진입점 | 문서 허브 |
| `docs/DOCUMENTATION_INDEX.md` | 구조/인덱스 | 분류 기준과 cleanup policy |
| `docs/ARCHITECTURE_MAP.md` | 구조 | 런타임/상태 저장소 지도 |
| `docs/DEVELOPED_WORK.md` | 완료 요약 | 삭제한 완료 plan 범위와 남은 완료 리포트 |
| `docs/TODO_ROADMAP.md` | 우선순위 리포트 | 코드 기준 active TODO와 삭제 후보 |
| `docs/DOCUMENTATION_INVENTORY.md` | 인벤토리 | 현재 문서 목록 |
| `docs/trading_process.md` | 구조/운영 | 매매 프로세스 |
| `docs/rsi_threshold_research.md` | 연구 | RSI 기준 |
| `docs/KIS_API_TODO.md` | 할 일 | KIS API 보완 목록 |
| `docs/KIS_WS_FILL_SYNC_PLAN.md` | 구현/운영 검증 | 로컬 구현 완료, 실수신 검증 남음 |

## Plans

| 파일 | 분류 | 현재 판단 |
| --- | --- | --- |
| `docs/plans/kr_us_live_ops_qa_20260427.md` | P0 운영 QA | 최신 guardian `BLOCK_START`, US broker truth stale hard fail로 active |
| `docs/plans/live_sell_reconciliation_dashboard_pnl_plan_20260506.md` | P0 hotfix | pending sell 일부 구현/테스트 완료, dashboard PathB QA 2건 실패와 `RECOVERY_MICRO` gate 누락으로 active |
| `docs/plans/entry_risk_control_development_20260508.md` | 완료/삭제 후보 | untracked 새 plan, 구현 완료 및 `tests/test_entry_risk_controls.py` green, QA report 보존 후 삭제 가능 |
| `docs/plans/order_equity_reconciliation_improvement_20260429.md` | P0/P1 follow-up | path_run_id dedupe 등 일부 완료, KIS 수동 확인/dashboard mismatch/SafetyContext 후속 남음 |
| `docs/plans/MODULARIZATION.md` | P1/P3 구조 | `RiskManager` KR/US 분리 미완료, 전체 파일 분리는 보류 |
| `docs/plans/execution_audit_observability_plan_20260430.md` | P1 관찰 | candidate health는 구현됨, post-exit/cancel status/opening simulation 관찰 보강 남음 |
| `docs/plans/claude_api_cost_optimization_plan_20260507.md` | P1 비용 최적화 | 핵심 절감 적용 전, hold_advisor는 여전히 3인 호출 |
| `docs/plans/us_extended_hours_screening_plan_20260502.md` | P2 shadow/research | preopen shadow 경로는 있음, 10세션 관찰/성과 리포트 필요 |
| `docs/plans/theme_candidate_injection_plan_20260506.md` | P2 research | 관련 runtime flow/counter 미구현, shadow 설계부터 필요 |
| `docs/plans/candidate_tier_state_machine_plan_20260507.md` | P3 future plan | untracked 새 plan, tier book runtime/module 미구현 |
| `docs/plans/DUAL_RUNTIME_ARCHITECTURE.md` | P3 장기 구조 | Phase 0 완료, SharedEngine/AccountRuntime은 모듈화 이후 보류 |
| `docs/plans/BRAIN_TRAIN_TODO.md` | P3 보류 | 학습용 거래 모드, 지금은 미적용 |
| `docs/plans/PLAN_intraday_strategy_roadmap.md` | P3 전략 | 운영 안전 이후 재평가 |
| `docs/plans/PLAN_momentum_opening_gate.md` | P3 전략 | prompt/data 품질 이후 재평가 |
| `docs/plans/p0_pathb_fill_dashboard_followup_20260503.md` | 완료/삭제 후보 | focused QA `81 passed`, 원래 blocker 해소 |
| `docs/plans/p0_post_isolation_qa_expansion_20260502.md` | 완료/삭제 후보 | post-isolation QA `72 passed` + 후속 batch `36 passed` |
| `docs/plans/TRADING_IMPROVEMENT_WORKLOG_20260421.md` | 보관/삭제 후보 | 과거 worklog, 최신 TODO/테스트/리포트로 흡수 |

## Reports

| 파일 | 분류 |
| --- | --- |
| `docs/reports/brain_json_postmortem_cleanup_20260430.md` | 완료 QA |
| `docs/reports/claude_call_review_20260429.md` | 분석 리포트 |
| `docs/reports/claude_usage_quality_optimization_20260430.md` | 분석 리포트 |
| `docs/reports/entry_risk_control_qa_20260508.md` | 완료 QA |
| `docs/reports/historical_candidate_execution_review_20260420_20260429.md` | 분석 리포트 |
| `docs/reports/live_improvement_simulation_20260501_live_improvement.md` | 시뮬레이션 리포트 |
| `docs/reports/preopen_candidate_flow_design_report_20260506.md` | 설계/구현 리포트 |
| `docs/reports/rule_simulation_20260420_20260429.md` | 시뮬레이션 리포트 |
| `docs/reports/shadow_audit_gap_20260430.md` | 분석 리포트 |
| `docs/reports/shadow_audit_qa_20260430.md` | QA 리포트 |
| `docs/reports/shadow_audit_report_live_20260430_KR.md` | live audit 리포트 |
| `docs/reports/soft_exit_arbitration_qa_20260430.md` | QA 리포트 |
| `docs/reports/candidate_improvement_simulation_20260508_133001.md` | 시뮬레이션 리포트 |
| `docs/reports/full_profitability_review_20260508_full_v2.md` | 분석 리포트 |
| `docs/reports/live_improvement_simulation_20260508_full_review.md` | 시뮬레이션 리포트 |

## Audit

| 파일 | 분류 | 메모 |
| --- | --- | --- |
| `audit/encoding_mojibake_report_20260501.md` | 감사 산출물 | health check 정책으로 제한 |
| `audit/market_analysis_tune_prompt_audit_20260501.md` | 분석/TODO | prompt/breadth 개선 근거 |
| `audit/priority_hotfix_improvement_plan_20260501.md` | 완료 성격 핫픽스 | 상세 plan은 완료 요약으로 대체 |
