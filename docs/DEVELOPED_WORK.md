# Developed Work Summary - 2026-05-20

이 문서는 완료된 작업의 요약만 보존한다. 실행해야 할 일은 [TODO_ROADMAP.md](TODO_ROADMAP.md) 하나만 기준으로 본다. 삭제된 상세 plan은 Git history에서 복구할 수 있다.

## 2026-05-20 완료/정리

| 문서/항목 | 완료 판단 근거 | 개선 효과 | 보존 리뷰 |
| --- | --- | --- | --- |
| `audit/priority_hotfix_improvement_plan_20260501.md` | 문서 내 최종 QA: `py_compile`, `tests/test_pathb_runtime.py`, 전체 pytest 398 passed 기록 | startup/session/order 상태 복구 경로가 안정되어 stale state와 broker marker 오염이 live 주문으로 이어질 위험을 줄였다. | 인스턴스 상태 초기화, session reset, startup guard, order error clear, broker marker, PathB US carry price 전달이 복구되어 상세 plan은 삭제했다. |
| `docs/plans/candidate_pipeline_improvement_implementation_plan_20260515.md` | 문서 내 QA batch와 이후 코드 존재: screener quality, audit linkage, outcome catch-up, KR alpha/tooling | 후보 생성, 감사 연결, outcome 기록이 이어져 selection 품질 저하 원인을 execution/risk 문제와 분리해서 볼 수 있다. | 후보 품질/감사 연결 작업은 완료로 정리하고, 운영 모니터링만 TODO에 남겼다. |
| Prompt Overlay Phase 0/1 | `docs/reports/prompt_overlay_impl_qa_20260520.md`: helper, off/shadow/live, audit payload, analyzer, tests 통과 | prompt 개선을 즉시 live로 밀어 넣지 않고 shadow gate로 검증할 수 있어 과최적화와 단기 착시를 줄인다. | 즉시 구현 범위는 완료. shadow→live 전환, cap 확대, fresh slot은 데이터 관찰 항목으로 TODO에 남겼다. |
| Final Prompt Evidence Alignment | `trading_bot.py`, `minority_report/analysts.py`의 final prompt pool 기반 evidence prefetch와 override 경로, 관련 테스트 존재 | Claude가 보는 final prompt 후보와 실행 후보의 괴리를 줄여 판단 근거와 주문 후보를 같은 pool로 맞춘다. | 코드 수정은 완료. 다음 세션 metric 검증만 TODO에 남겼다. |
| KR cap40 confirmation enforce | `docs/reports/kr_cap40_confirmation_enforce_implementation_report_20260516.md`: preflight/guardian/test 통과 | KR live 진입 수량이 cap 40 confirmation 정책을 우회하지 못하게 하여 과도한 단일 주문 노출을 낮춘다. | KR cap 40은 confirmation enforce와 결합된 운영값으로 정리했다. live 전 시작 검증은 TODO 유지. |
| PathB base live implementation | `pathb_v2_live_plan.md`의 Phase 1~8 완료 기록과 PathB runtime/control/dashboard/test 근거 | Claude price plan 기반 조건부 진입/청산을 PathA safety/order truth와 합류시키는 운영 흐름을 확보했다. | 상세 phase checklist는 active plan에서 제거하고, root 문서는 운영 reference로 축소했다. |
| Dashboard PnL source label | dashboard helper와 tests에서 source badge 확인 | daily PnL이 broker truth인지 local 계산인지 드러나 운영자가 잘못된 수익률 축으로 판단할 가능성을 줄였다. | daily PnL source 오해를 줄이는 small patch는 완료. 전면 mismatch 정비는 TODO 유지. |
| PathB `PULLBACK_WAIT` origin audit | `tests/test_pathb_runtime.py`, `tests/test_candidate_action_live_mapping.py`의 origin_action/origin_route 보존 확인 | PathB wait-only plan과 PathA trade_ready를 구분해 stale wait, zone hit, 실제 주문 전이를 추적할 수 있다. | `PULLBACK_WAIT`가 PathA trade_ready가 아닌 PathB wait-only plan임을 추적 가능하게 했다. |
| Counterfactual store/writer base | `audit/candidate_counterfactual_store.py`, `runtime/counterfactual_paths.py`, analyzer/backfill CLI, tests | 차단/비진입 후보도 가격 경로와 연결할 수 있는 저장 기반이 생겨 정책 변경의 기회비용 분석이 가능해졌다. | row 저장/분석 기반은 완료. 실제 outcome label backfill은 TODO P0로 유지. |
| Market risk shadow | `ENABLE_MARKET_RISK_SHADOW`, `_risk(market)`, live status `risk_shadow` | KR/US 리스크 상태를 병렬 관찰할 수 있어 live write path 분리 전 cash/position/halt 오염 가능성을 확인할 수 있다. | 시장별 mirror는 완료. live write path 전환은 TODO P1로 유지. |

## 2026-05-13 완료로 삭제한 plan

| 문서 | 완료 판단 근거 | 보존 리뷰 |
| --- | --- | --- |
| `docs/plans/p0_pathb_fill_dashboard_followup_20260503.md` | PathB runtime, KIS WS fill notice, dashboard KIS profile focused QA가 green으로 기록됨 | 원래 blocker였던 token routing/fill/dashboard profile 회귀가 테스트로 흡수됐다. |
| `docs/plans/p0_post_isolation_qa_expansion_20260502.md` | post-isolation QA batch와 후속 batch가 green으로 기록됨 | KIS market profile isolation 이후 회귀 위험이 낮아져 active plan에서 제거했다. |
| `docs/plans/entry_risk_control_development_20260508.md` | `tests/test_entry_risk_controls.py`와 `docs/reports/entry_risk_control_qa_20260508.md` | 시장별 entry cap, KR 신규 진입 차단, US broker quarantine, PlanA MFE breakeven이 구현됐다. |
| `docs/plans/TRADING_IMPROVEMENT_WORKLOG_20260421.md` | 최신 실행 항목이 TODO, tests, reports로 흡수됨 | 과거 worklog로만 의미가 있어 별도 active 문서로 유지하지 않는다. |
| `docs/plans/decisions_db_operational_role_and_recovery_plan_20260512.md` | `python -m pytest tests/test_db_health.py tests/test_recover_decisions_db.py tests/test_ml_db_writer_paths.py tests/test_forward_updater.py -q` -> `7 passed`; repo health read-only check가 `ml.decisions_db_health PASS` | 운영 DB 오염 방지, 복구 dry-run, health check, forward skip reason 개선이 구현됐다. |

## 2026-05-13 통합 삭제한 active plan 원본

미완료 항목은 완료 처리하지 않고 [TODO_ROADMAP.md](TODO_ROADMAP.md)에 우선순위, 사유, 개선 효과로 통합했다.

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

- live 시작 전 guardian/preflight와 브로커 open order 0 수동 확인.
- KIS WS/REST 체결 truth의 실제 payload 검증.
- V2 lifecycle canonical performance table과 decisions fill 연결.
- counterfactual outcome 30m/60m/close label backfill.
- final prompt evidence alignment와 prompt overlay shadow의 실제 세션 gate 통과.
- `RiskManager` KR/US live write path 분리.
- Safety/equity source/lag audit 확장.
- KR modern action schema, entry timing gate, US preopen 샘플 확충.
- CandidateTierBook, KRX/BigKinds/theme injection, L3 inject, dual runtime, Brain Train, 신규 전략 gate.
