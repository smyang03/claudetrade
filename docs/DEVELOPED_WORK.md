# Developed Work - 2026-05-02

이 문서는 MD 내용을 확인해 완료 또는 QA 완료로 볼 수 있는 항목만 모았습니다. 완료 문서는 삭제하지 않고 근거 링크로 보존합니다. 아직 남은 follow-up은 [TODO_ROADMAP.md](TODO_ROADMAP.md)에 별도 행으로만 남깁니다.

## Completed Brain / Learning Fixes

| 상태 | 문서 | 완료 근거 |
| --- | --- | --- |
| 완료 | [Brain Issue Pattern ID Contiguity Fix](plans/brain_issue_pattern_id_contiguity_20260501.md) | Execution Results, QA Results, Plan Comparison 있음 |
| 완료 | [Brain Postmortem Cleanup Plan](plans/brain_postmortem_cleanup_20260430.md) | 완료 테이블과 별도 QA 리포트 있음 |
| 완료 | [Brain JSON Postmortem Cleanup QA](reports/brain_json_postmortem_cleanup_20260430.md) | cleanup 결과와 검증 명령 기록 |
| 완료 | [Brain Runtime State Review Fix](plans/brain_runtime_state_review_fix_20260501.md) | 완료 체크와 QA 결과 중심 |

## Completed Live Runtime / Execution Fixes

| 상태 | 문서 | 완료 근거 |
| --- | --- | --- |
| 완료 | [Live Order Review Fix](plans/live_order_review_fix_20260429.md) | 완료 체크 다수, QA 항목 있음 |
| 완료 | [Loss Cap / Profit Floor Improvement](plans/loss_cap_profit_floor_improvement_20260429.md) | 완료 체크와 테스트 항목 있음 |
| 완료 | [KOSDAQ Volume Rank Fix](plans/kosdaq_volume_rank_fix_20260428.md) | 완료 체크와 검증 항목 있음 |
| 완료 | [Path A Entry Timing Improvement](plans/path_a_entry_timing_20260428.md) | 구현/QA 기록 중심 |
| 완료 | [Candidate Health Tracker Improvement](plans/candidate_health_tracker_20260429.md) | 완료 체크와 검증 항목 있음 |
| 구현/로컬 QA 완료, 운영 검증 남음 | [KIS WebSocket Fill Sync](KIS_WS_FILL_SYNC_PLAN.md) | notice parser, dedupe, full/partial fill pending 반영 테스트 통과. 실제 notice 수신 검증은 TODO |
| 완료 | [Priority Hotfix Improvement Plan](../audit/priority_hotfix_improvement_plan_20260501.md) | 최종 QA, root test 398 passed, 추가 정적 검증 기록 있음 |
| 완료 | [Recovery and Follow-up TODO](plans/recovery_and_followup_todo_20260501.md) | `repo_health_check.py`, 장 전/장 후 trigger, manual/JSON trigger 검증 완료 |
| 핵심 구현 완료, follow-up 있음 | [Order / Equity Reconciliation Improvement](plans/order_equity_reconciliation_improvement_20260429.md) | order-state, equity reference, new-buy gate 구현/QA 완료. operator 확인과 ops/dashboard 후속은 TODO |
| 완료, 운영 WARN follow-up 있음 | [KR Opening Quality / Ops Correctness Round 1](plans/kr_opening_quality_ops_round1_20260428.md) | Phase 1-6 구현, `pytest tests` 128 passed, live preflight ok=True/fail=0 기록 |

## Completed PathB / ORDER_UNKNOWN Fixes

| 상태 | 문서 | 완료 근거 |
| --- | --- | --- |
| 완료 | [Path B Sell Truth Reconcile](plans/pathb_sell_truth_reconcile_20260428.md) | Phase 1-8 구현 검증, targeted tests와 `pytest tests` 통과 기록 |
| 완료, deferred 있음 | [ORDER_UNKNOWN Auto-Resolve and PathB Pre-Close Carry](plans/order_unknown_preclose_carry_plan_20260430.md) | Phase 1/2 구현, focused/contract 61 tests passed, MD comparison 있음 |
| 완료 | [Path B ORDER_UNKNOWN Dashboard Reconcile](plans/pathb_order_unknown_dashboard_reconcile_20260501.md) | false Path A evidence 원인 분석, external close sync/API 보강, QA checklist 완료 |

## Completed Claude / Decision Contract Fixes

| 상태 | 문서 | 완료 근거 |
| --- | --- | --- |
| 완료, follow-up 있음 | [Trading Decision Contract Improvement](plans/trading_decision_contract_improvement_20260501.md) | P0-P9 completed, QA Result, Final MD Comparison 있음. F1-F3은 [TODO_ROADMAP.md](TODO_ROADMAP.md)에 별도 분리 |
| 완료, deferred 있음 | [Claude Quality Contract Improvement](plans/claude_quality_contract_improvement_20260501.md) | raw logger, credit tracker, postmortem raw-first, PathB target preservation, hold advisor fallback, WAL, prompt reduction, mojibake P0 복구/QA 완료 |

## Completed Shadow / QA Infrastructure

| 상태 | 문서 | 완료 근거 |
| --- | --- | --- |
| 완료 | [Bucket Shadow Quality Plan](plans/bucket_shadow_quality_20260428.md) | 완료 체크 다수 |
| 완료 | [Shadow Audit Infrastructure Plan](plans/shadow_audit_infrastructure_20260430.md) | 완료/검증 항목 있음 |
| 완료 | [Shadow Audit Review Fix Plan](plans/shadow_audit_review_fix_20260430.md) | 리뷰 수정과 QA 항목 있음 |
| 완료 리포트 | [Shadow Audit QA](reports/shadow_audit_qa_20260430.md) | QA 체크와 결과 있음 |
| 완료 리포트 | [Shadow Audit Gap Report](reports/shadow_audit_gap_20260430.md) | gap 분석 결과 |
| 완료 리포트 | [Shadow Audit Report Live KR](reports/shadow_audit_report_live_20260430_KR.md) | live KR audit 산출물 |

## Completed Soft Exit / Simulation Reports

| 상태 | 문서 | 완료 근거 |
| --- | --- | --- |
| 완료 | [Soft Exit Arbitration Plan](plans/soft_exit_arbitration_20260430.md) | Phase 1 구현과 QA 리포트 있음 |
| 완료 | [Soft Exit Arbitration Follow-up](plans/soft_exit_arbitration_followup_20260501.md) | quick-exit model logging, comment/dead-branch cleanup, 7개 회귀 테스트와 QA 리포트 업데이트 완료 |
| 완료 리포트 | [Soft Exit Arbitration QA](reports/soft_exit_arbitration_qa_20260430.md) | Phase 1과 2026-05-01 follow-up QA 체크 다수 |
| 완료 리포트 | [Rule Simulation](reports/rule_simulation_20260420_20260429.md) | 과거 로그 시뮬레이션 결과 |
| 완료 리포트 | [Historical Candidate / Execution Review](reports/historical_candidate_execution_review_20260420_20260429.md) | 후보/실행 분석 결과 |
| 완료 리포트 | [Live Improvement Simulation](reports/live_improvement_simulation_20260501_live_improvement.md) | live 개선 시뮬레이션 산출물 |

## Completed Analysis / Audit Reports

| 상태 | 문서 | 완료 근거 |
| --- | --- | --- |
| 완료 리포트 | [Claude Call Review](reports/claude_call_review_20260429.md) | 호출 품질 분석 결론 포함 |
| 완료 리포트 | [Claude Usage Quality Optimization](reports/claude_usage_quality_optimization_20260430.md) | 사용량/품질 분석 결과 |
| 완료 산출물 | [Encoding/Mojibake Scan Report](../audit/encoding_mojibake_report_20260501.md) | repository scan report. 실제 수정 대상 선별은 별도 TODO가 아니라 health check 정책으로 제한 |
| 감사 완료, 구현 TODO 있음 | [Market Analysis / Tuning Prompt Audit](../audit/market_analysis_tune_prompt_audit_20260501.md) | 원인 분석과 recommended implementation order 있음. 구현은 [TODO_ROADMAP.md](TODO_ROADMAP.md)에 남김 |

## Completed Or Historical References

| 상태 | 문서 | 성격 |
| --- | --- | --- |
| 보관 | [Trading Improvement Worklog](plans/TRADING_IMPROVEMENT_WORKLOG_20260421.md) | 과거 작업 로그 |
| 보관, ignored | [DEVLOG](archive/DEVLOG.md) | 전체 개발 맥락 핸드오프 |
| 보관, ignored | [TRAINING_DEVLOG](archive/TRAINING_DEVLOG.md) | 훈련/실행 로그 |
| 보관, ignored | [DASHBOARD_DEVLOG](archive/DASHBOARD_DEVLOG.md) | 대시보드 변경 로그 |
| 보관, ignored | [DEBUG 개선](archive/DEBUG_개선.md) | 디버그 개선 이력 |
