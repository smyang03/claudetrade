# Developed Work Summary - 2026-05-08

이 문서는 완료된 작업의 요약만 보존합니다. 2026-05-05 정리에서 완료된 세부 plan 문서는 `docs/plans/`에서 삭제했고, 필요하면 Git history에서 복구합니다. 현재 실행해야 할 일과 삭제 후보는 [TODO_ROADMAP.md](TODO_ROADMAP.md)에 남깁니다.

## 2026-05-08 코드 기준 삭제 후보

| 문서 | 완료 판단 근거 | 다음 정리 |
| --- | --- | --- |
| `docs/plans/p0_pathb_fill_dashboard_followup_20260503.md` | 원래 남았던 PathB runtime blocker가 해소됐고 focused QA가 `81 passed, 2 warnings`입니다. | 완료 요약만 보존하고 `docs/plans/`에서 삭제 가능 |
| `docs/plans/p0_post_isolation_qa_expansion_20260502.md` | post-isolation QA command가 `72 passed, 2 warnings`, 후속 batch가 `36 passed, 2 warnings`입니다. | 완료 요약만 보존하고 `docs/plans/`에서 삭제 가능 |
| `docs/plans/entry_risk_control_development_20260508.md` | 시장별 daily cap, KR 신규 진입 차단, PlanA MFE breakeven, US broker quarantine이 구현됐고 `tests/test_entry_risk_controls.py`가 green입니다. | `docs/reports/entry_risk_control_qa_20260508.md`와 이 요약만 보존하고 `docs/plans/`에서 삭제 가능 |
| `docs/plans/TRADING_IMPROVEMENT_WORKLOG_20260421.md` | 과거 worklog이며 실행 가능한 최신 항목은 `TODO_ROADMAP.md`, tests, reports로 흡수됐습니다. | archive 또는 삭제 가능 |

## 2026-05-05에 삭제한 완료 plan 범위

| 묶음 | 삭제한 완료 plan |
| --- | --- |
| Brain / postmortem | brain issue pattern ID, postmortem cleanup, runtime state review fix |
| Live/runtime 안전 | broker truth foundation, live order review, loss cap/profit floor, ORDER_UNKNOWN pre-close carry, live guardian automation |
| Dashboard / 데이터 품질 | dashboard KR OHLCV fix, KOSDAQ volume rank fix, P0 data quality/breadth, P0 backfill/ops |
| PathB / execution truth | PathB sell truth reconcile, PathB ORDER_UNKNOWN dashboard reconcile, Path A entry timing |
| Shadow / audit | bucket shadow quality, candidate health tracker, shadow audit infrastructure/review |
| Claude / decision contract | Claude quality contract, trading decision contract |
| Soft exit | soft exit arbitration and follow-up |
| Preopen implementation | preopen shadow basket implementation, preopen scheduler automation |
| Recovery / encoding | repository health check and recovery follow-up |

2026-05-05에는 PathB QA 문서 2개를 실패 근거로 남겼지만, 2026-05-08 focused QA 기준으로는 삭제 후보입니다.

## 남아 있는 완료 리포트

| 문서 | 성격 |
| --- | --- |
| [KIS WebSocket Fill Sync](KIS_WS_FILL_SYNC_PLAN.md) | 구현/로컬 QA 완료, 실수신 운영 검증 남음 |
| [Brain JSON Postmortem Cleanup QA](reports/brain_json_postmortem_cleanup_20260430.md) | brain cleanup 검증 |
| [Shadow Audit QA](reports/shadow_audit_qa_20260430.md) | shadow audit 회귀 검증 |
| [Shadow Audit Gap Report](reports/shadow_audit_gap_20260430.md) | shadow audit gap 분석 |
| [Soft Exit Arbitration QA](reports/soft_exit_arbitration_qa_20260430.md) | soft exit QA 결과 |
| [Rule Simulation](reports/rule_simulation_20260420_20260429.md) | 과거 로그 기반 룰 시뮬레이션 |
| [Historical Candidate / Execution Review](reports/historical_candidate_execution_review_20260420_20260429.md) | 후보/실행 분석 |
| [Live Improvement Simulation](reports/live_improvement_simulation_20260501_live_improvement.md) | live 개선 시뮬레이션 |
| [Preopen Candidate Flow Design Report](reports/preopen_candidate_flow_design_report_20260506.md) | preopen/candidate action 구현 리포트 |
| [Claude Call Review](reports/claude_call_review_20260429.md) | Claude 호출 품질 분석 |
| [Claude Usage Quality Optimization](reports/claude_usage_quality_optimization_20260430.md) | 사용량/품질 최적화 분석 |
| [Entry Risk Control QA](reports/entry_risk_control_qa_20260508.md) | entry risk control 구현/QA 결과 |

## 현재 완료로 보지 않는 항목

- KR/US live ops QA: guardian가 아직 `BLOCK_START`입니다.
- Live sell/dashboard PnL hotfix: pending sell 일부는 완료됐지만 `tests/test_dashboard_pathb.py` 2건 실패와 `RECOVERY_MICRO` gate 누락이 남았습니다.
- RiskManager KR/US 분리: 아직 단일 `self.risk` 구조입니다.
- Preopen/extended-hours 성능 판단: shadow 구현은 있지만 10세션 관찰 리포트가 필요합니다.
- Candidate tier state machine: future plan이며 tier book runtime은 아직 없습니다.
- Theme candidate injection: research/design 단계이며 runtime injection flow는 아직 없습니다.
