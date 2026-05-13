# Developed Work Summary - 2026-05-13

이 문서는 완료된 작업의 요약만 보존한다. 실행해야 할 일은 [TODO_ROADMAP.md](TODO_ROADMAP.md) 하나만 기준으로 본다. 삭제된 상세 plan은 Git history에서 복구할 수 있다.

## 2026-05-13 완료로 삭제한 plan

| 문서 | 완료 판단 근거 | 보존 리뷰 |
| --- | --- | --- |
| `docs/plans/p0_pathb_fill_dashboard_followup_20260503.md` | PathB runtime, KIS WS fill notice, dashboard KIS profile focused QA가 green으로 기록됨 | 원래 blocker였던 token routing/fill/dashboard profile 회귀가 테스트로 흡수됐다. |
| `docs/plans/p0_post_isolation_qa_expansion_20260502.md` | post-isolation QA batch와 후속 batch가 green으로 기록됨 | KIS market profile isolation 이후 회귀 위험이 낮아져 active plan에서 제거했다. |
| `docs/plans/entry_risk_control_development_20260508.md` | `tests/test_entry_risk_controls.py`와 `docs/reports/entry_risk_control_qa_20260508.md` | 시장별 entry cap, KR 신규 진입 차단, US broker quarantine, PlanA MFE breakeven이 구현됐다. |
| `docs/plans/TRADING_IMPROVEMENT_WORKLOG_20260421.md` | 최신 실행 항목이 TODO, tests, reports로 흡수됨 | 과거 worklog로만 의미가 있어 별도 active 문서로 유지하지 않는다. |
| `docs/plans/decisions_db_operational_role_and_recovery_plan_20260512.md` | `python -m pytest tests/test_db_health.py tests/test_recover_decisions_db.py tests/test_ml_db_writer_paths.py tests/test_forward_updater.py -q` -> `7 passed`; repo health read-only check가 `ml.decisions_db_health PASS` | 운영 DB 오염 방지, 복구 dry-run, health check, forward skip reason 개선이 구현됐다. |

## 2026-05-13 통합 삭제한 active plan 원본

미완료 항목은 완료 처리하지 않고 [TODO_ROADMAP.md](TODO_ROADMAP.md)에 우선순위, 사유, 개선 전후 리뷰로 통합했다.

| 묶음 | 통합한 원본 |
| --- | --- |
| KIS / fill truth | `docs/KIS_API_TODO.md`, `docs/KIS_WS_FILL_SYNC_PLAN.md` |
| live safety / ops | `kr_us_live_ops_qa_20260427.md`, `live_sell_reconciliation_dashboard_pnl_plan_20260506.md`, `order_equity_reconciliation_improvement_20260429.md`, `pathb_pullback_wait_live_policy_review_20260512.md` |
| audit / observability | `execution_audit_observability_plan_20260430.md`, `live_audit_execution_future_plan_20260508.md` |
| Claude usage / cost | `claude_api_cost_optimization_plan_20260507.md`, `claude_prompt_usage_performance_ledger_20260511.md` |
| 후보/전략 품질 | `hybrid_lite_attribution_miss_quality_plan_20260509.md`, `watch_trigger_future_backlog_20260508.md`, `candidate_tier_state_machine_plan_20260507.md`, `theme_candidate_injection_plan_20260506.md` |
| 데이터 소스 / preopen | `pending_data_sources_krx_bigkinds_20260510.md`, `us_extended_hours_screening_plan_20260502.md` |
| 구조 / 장기 보류 | `MODULARIZATION.md`, `DUAL_RUNTIME_ARCHITECTURE.md`, `BRAIN_TRAIN_TODO.md`, `PLAN_intraday_strategy_roadmap.md`, `PLAN_momentum_opening_gate.md` |

## 이전에 삭제한 완료 plan 범위

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

## 현재 완료로 보지 않는 항목

- KIS WS/REST 체결 truth의 실제 payload 검증.
- PathB `PULLBACK_WAIT` live 전이 audit와 reconfirm shadow.
- Dashboard PnL fallback source labeling과 broker/local mismatch ops 표시.
- `RiskManager` KR/US 분리.
- SafetyContext audit field 확장.
- Counterfactual shadow infrastructure와 hybrid/watch trigger 성과 검증.
- Preopen/extended-hours 10세션 성과 리포트.
- Candidate tier book, theme injection, KRX/BigKinds integration.
- Dual runtime, Brain Train, 신규 intraday/VWAP/momentum gate.
