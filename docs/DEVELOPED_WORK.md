# Developed Work - 2026-05-01

이 문서는 MD 내용을 확인해 완료 또는 QA 완료로 볼 수 있는 항목만 모았습니다. Git 상태가 `Untracked`인 문서는 내용상 완료여도 아직 커밋되지 않은 상태입니다.

## Completed Brain / Learning Fixes

| 상태 | 문서 | 완료 근거 |
| --- | --- | --- |
| 완료, untracked | [Brain Issue Pattern ID Contiguity Fix](plans/brain_issue_pattern_id_contiguity_20260501.md) | Execution Results, QA Results, Plan Comparison 있음 |
| 완료 | [Brain Postmortem Cleanup Plan](plans/brain_postmortem_cleanup_20260430.md) | 완료 테이블과 별도 QA 리포트 있음 |
| 완료 | [Brain JSON Postmortem Cleanup QA](reports/brain_json_postmortem_cleanup_20260430.md) | cleanup 결과와 검증 명령 기록 |
| 완료, untracked | [Brain Runtime State Review Fix](plans/brain_runtime_state_review_fix_20260501.md) | 완료 체크와 QA 결과 중심 |

## Completed Live Runtime / Execution Fixes

| 상태 | 문서 | 완료 근거 |
| --- | --- | --- |
| 완료 | [Live Order Review Fix](plans/live_order_review_fix_20260429.md) | 완료 체크 다수, QA 항목 있음 |
| 완료 | [Loss Cap / Profit Floor Improvement](plans/loss_cap_profit_floor_improvement_20260429.md) | 완료 체크와 테스트 항목 있음 |
| 완료 | [KOSDAQ Volume Rank Fix](plans/kosdaq_volume_rank_fix_20260428.md) | 완료 체크와 검증 항목 있음 |
| 완료 | [Path A Entry Timing Improvement](plans/path_a_entry_timing_20260428.md) | 구현/QA 기록 중심 |
| 완료 | [Candidate Health Tracker Improvement](plans/candidate_health_tracker_20260429.md) | 완료 체크와 검증 항목 있음 |
| 구현/로컬 QA 완료, 운영 검증 남음 | [KIS WebSocket Fill Sync](KIS_WS_FILL_SYNC_PLAN.md) | notice parser, dedupe, full/partial fill pending 반영 테스트 통과 |
| 완료, untracked | [Priority Hotfix Improvement Plan](../audit/priority_hotfix_improvement_plan_20260501.md) | 완료 체크와 QA 항목 있음 |

## Completed Claude / Decision Contract Fixes

| 상태 | 문서 | 완료 근거 |
| --- | --- | --- |
| 완료, untracked, follow-up 있음 | [Trading Decision Contract Improvement](plans/trading_decision_contract_improvement_20260501.md) | P0-P9 completed, QA Result, Final MD Comparison 있음. F1-F3은 [TODO_ROADMAP.md](TODO_ROADMAP.md)에 별도 분리 |

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
| 완료 리포트 | [Soft Exit Arbitration QA](reports/soft_exit_arbitration_qa_20260430.md) | QA 체크 다수 |
| 완료 리포트 | [Rule Simulation](reports/rule_simulation_20260420_20260429.md) | 과거 로그 시뮬레이션 결과 |
| 완료 리포트 | [Historical Candidate / Execution Review](reports/historical_candidate_execution_review_20260420_20260429.md) | 후보/실행 분석 결과 |

## Completed Analysis Reports

| 상태 | 문서 | 완료 근거 |
| --- | --- | --- |
| 완료 리포트 | [Claude Call Review](reports/claude_call_review_20260429.md) | 호출 품질 분석 결론 포함 |
| 완료 리포트 | [Claude Usage Quality Optimization](reports/claude_usage_quality_optimization_20260430.md) | 사용량/품질 분석 결과 |
| 완료 산출물, untracked | [Encoding/Mojibake Scan Report](../audit/encoding_mojibake_report_20260501.md) | repository scan report |

## Completed Or Historical References

| 상태 | 문서 | 성격 |
| --- | --- | --- |
| 보관 | [Trading Improvement Worklog](plans/TRADING_IMPROVEMENT_WORKLOG_20260421.md) | 과거 작업 로그 |
| 보관, ignored | [DEVLOG](archive/DEVLOG.md) | 전체 개발 맥락 핸드오프 |
| 보관, ignored | [TRAINING_DEVLOG](archive/TRAINING_DEVLOG.md) | 훈련/실행 로그 |
| 보관, ignored | [DASHBOARD_DEVLOG](archive/DASHBOARD_DEVLOG.md) | 대시보드 변경 로그 |
| 보관, ignored | [DEBUG 개선](archive/DEBUG_개선.md) | 디버그 개선 이력 |
