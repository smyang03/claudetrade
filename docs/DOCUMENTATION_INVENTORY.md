# Documentation Inventory - 2026-05-01

## Summary

| 묶음 | 개수 | Git 성격 | 정렬 |
| --- | ---: | --- | --- |
| Repository root Markdown | 6 | tracked clean | 구조/운영 진입점 |
| `docs/` 관리 문서 | 52 | tracked, modified, untracked, ignored archive 혼합 | active docs |
| `audit/` Markdown | 2 | untracked | 감사/핫픽스 산출물 |
| `data/backtest_audit/**/*.md` | 45 | ignored | 자동 생성 backtest 산출물 |
| `data/v2_reports/**/*.md` | 107 | ignored | 자동 생성 V2/live 산출물 |
| `.pytest_cache/README.md` | 1 | ignored | 도구 캐시 |

## Root Documents

| 파일 | 상태 | 정렬 | 메모 |
| --- | --- | --- | --- |
| `README.md` | clean | 구조/진입점 | 프로젝트 개요 |
| `CLAUDE.md` | clean | 운영 철학/규칙 | Codex/Claude 작업 지침 |
| `DATA.md` | clean | 구조/데이터 | 상태/로그/DB 설명 |
| `LIVE_PREFLIGHT_CHECKLIST.md` | clean | 운영/QA | live 실행 전 체크리스트 |
| `v2.md` | clean | 구조/설계 | V2 production design |
| `pathb_v2_live_plan.md` | clean | 구조/계획 | PathB live plan |

## Docs Root

| 파일 | 상태 | 정렬 | 메모 |
| --- | --- | --- | --- |
| `docs/README.md` | rebuilt | 구조/진입점 | 새 문서 허브 |
| `docs/DOCUMENTATION_INDEX.md` | untracked | 구조/인덱스 | 분류 기준 |
| `docs/ARCHITECTURE_MAP.md` | untracked | 구성도 | runtime/data/doc 구조 |
| `docs/DEVELOPED_WORK.md` | untracked | 개발 완료 | 완료/QA 문서 모음 |
| `docs/TODO_ROADMAP.md` | untracked | 할 일 | 진행/후속 작업 모음 |
| `docs/DOCUMENTATION_INVENTORY.md` | untracked | 인벤토리 | 전체 MD 분류 |
| `docs/trading_process.md` | clean | 구조/운영 | 매매 프로세스 |
| `docs/rsi_threshold_research.md` | clean | 연구 | RSI 기준 |
| `docs/KIS_API_TODO.md` | clean | 할 일 | KIS API TODO |
| `docs/KIS_WS_FILL_SYNC_PLAN.md` | modified | 구현/로컬 QA + 운영 검증 | KIS WS 체결 연동 |

## Plans

| 파일 | 상태 | 정렬 |
| --- | --- | --- |
| `docs/plans/brain_issue_pattern_id_contiguity_20260501.md` | untracked | 완료/QA |
| `docs/plans/brain_postmortem_cleanup_20260430.md` | clean | 완료/QA |
| `docs/plans/brain_runtime_state_review_fix_20260501.md` | untracked | 완료/QA |
| `docs/plans/BRAIN_TRAIN_TODO.md` | clean | 할 일 |
| `docs/plans/broker_truth_live_plan_20260427.md` | clean | 할 일/운영 |
| `docs/plans/bucket_shadow_quality_20260428.md` | clean | 완료/QA |
| `docs/plans/candidate_health_tracker_20260429.md` | clean | 완료/QA |
| `docs/plans/claude_quality_contract_improvement_20260501.md` | modified | 진행 중 |
| `docs/plans/DUAL_RUNTIME_ARCHITECTURE.md` | clean | 구조/장기 |
| `docs/plans/execution_audit_observability_plan_20260430.md` | clean | 할 일 |
| `docs/plans/kosdaq_volume_rank_fix_20260428.md` | clean | 완료/QA |
| `docs/plans/kr_opening_quality_ops_round1_20260428.md` | clean | 할 일/QA |
| `docs/plans/kr_us_live_ops_qa_20260427.md` | clean | 운영/QA |
| `docs/plans/live_order_review_fix_20260429.md` | clean | 완료/QA |
| `docs/plans/loss_cap_profit_floor_improvement_20260429.md` | clean | 완료/QA |
| `docs/plans/MODULARIZATION.md` | clean | 구조/할 일 |
| `docs/plans/order_equity_reconciliation_improvement_20260429.md` | clean | mixed |
| `docs/plans/order_unknown_preclose_carry_plan_20260430.md` | clean | mixed |
| `docs/plans/path_a_entry_timing_20260428.md` | clean | 완료/QA |
| `docs/plans/pathb_sell_truth_reconcile_20260428.md` | clean | mixed |
| `docs/plans/PLAN_intraday_strategy_roadmap.md` | clean | 할 일 |
| `docs/plans/PLAN_momentum_opening_gate.md` | clean | 할 일 |
| `docs/plans/recovery_and_followup_todo_20260501.md` | untracked | 할 일 |
| `docs/plans/shadow_audit_infrastructure_20260430.md` | clean | 완료/QA |
| `docs/plans/shadow_audit_review_fix_20260430.md` | clean | 완료/QA |
| `docs/plans/soft_exit_arbitration_20260430.md` | clean | mixed |
| `docs/plans/soft_exit_arbitration_followup_20260501.md` | clean | 할 일 |
| `docs/plans/trading_decision_contract_improvement_20260501.md` | untracked | 완료/QA + 후속 TODO |
| `docs/plans/TRADING_IMPROVEMENT_WORKLOG_20260421.md` | clean | 보관/기록 |

## Reports

| 파일 | 상태 | 정렬 |
| --- | --- | --- |
| `docs/reports/brain_json_postmortem_cleanup_20260430.md` | clean | 완료 리포트 |
| `docs/reports/claude_call_review_20260429.md` | clean | 분석 리포트 |
| `docs/reports/claude_usage_quality_optimization_20260430.md` | clean | 분석 리포트 |
| `docs/reports/historical_candidate_execution_review_20260420_20260429.md` | clean | 분석 리포트 |
| `docs/reports/rule_simulation_20260420_20260429.md` | clean | 시뮬레이션 리포트 |
| `docs/reports/shadow_audit_gap_20260430.md` | clean | 분석 리포트 |
| `docs/reports/shadow_audit_qa_20260430.md` | clean | QA 리포트 |
| `docs/reports/shadow_audit_report_live_20260430_KR.md` | clean | live audit 리포트 |
| `docs/reports/soft_exit_arbitration_qa_20260430.md` | clean | QA 리포트 |

## Audit

| 파일 | 상태 | 정렬 |
| --- | --- | --- |
| `audit/encoding_mojibake_report_20260501.md` | untracked | 감사 산출물 |
| `audit/priority_hotfix_improvement_plan_20260501.md` | untracked | 완료 성격 핫픽스 계획 |

## Archive

| 파일 | 상태 | 정렬 |
| --- | --- | --- |
| `docs/archive/DEVLOG.md` | ignored | 보관 |
| `docs/archive/TRAINING_DEVLOG.md` | ignored | 보관 |
| `docs/archive/DASHBOARD_DEVLOG.md` | ignored | 보관 |
| `docs/archive/DEBUG_개선.md` | ignored | 보관 |

## Generated Markdown

아래 파일들은 개별 개발 문서가 아니라 실행 산출물입니다. active TODO나 완료 문서와 섞지 않습니다.

| 경로 | 개수 | 정렬 |
| --- | ---: | --- |
| `data/backtest_audit/**/*.md` | 45 | backtest/generated |
| `data/v2_reports/**/*.md` | 107 | v2/live/generated |
| `.pytest_cache/README.md` | 1 | tool cache |
