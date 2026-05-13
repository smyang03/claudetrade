# TODO Roadmap - 2026-05-13

이 문서가 현재 active plan의 단일 원장이다. `docs/plans/*.md` 24개와 `docs/KIS_API_TODO.md`, `docs/KIS_WS_FILL_SYNC_PLAN.md`의 미완료 내용을 이 문서로 흡수했고, 완료되었거나 흡수된 원본 plan 문서는 삭제했다.

## 분류 기준

- 완료 삭제: 코드와 focused QA 또는 read-only health check로 개선이 확인된 항목.
- 통합 삭제: 아직 해야 하지만 별도 원본 문서로 남겨 두면 우선순위가 흩어지는 항목.
- 운영 검증: 코드 구현은 끝났지만 실제 계좌, payload, 세션 데이터로 확인해야 닫을 수 있는 항목.
- 장기 보류: P0/P1 안정화 또는 shadow 데이터가 쌓이기 전에는 구현하지 않는 항목.

## 이번 재분류 리뷰

| 구분 | 리뷰 |
| --- | --- |
| 완료된 개선 | PathB token/fill/dashboard QA, post-isolation QA, entry risk control, ML decisions DB 보호/복구/health check는 코드와 테스트 근거가 있어 active plan에서 제거했다. |
| 부분 완료 | live sell reconciliation, order/equity reconciliation, guardian/preflight, preopen shadow, candidate quality 계열은 기반 구현이 있으나 운영 검증 또는 표시/감사 후속이 남았다. |
| 아직 미구현 | `RiskManager` KR/US 분리, candidate tier book, theme injection, KRX/BigKinds credential integration, WATCH_TRIGGER 자동 승격, dual runtime 추출은 아직 active code path가 아니다. |
| 문서 구조 | 미완료 항목을 이 문서 하나로 통합했다. 상세 과거 계획은 Git history에서 복구하고, 완료 요약은 `docs/DEVELOPED_WORK.md`에 둔다. |

## 완료되어 삭제한 계획

| 삭제한 원본 | 완료 판단 근거 | 개선 전 | 개선 후 리뷰 |
| --- | --- | --- | --- |
| `docs/plans/p0_pathb_fill_dashboard_followup_20260503.md` | `tests/test_pathb_runtime.py` 포함 focused batch가 green으로 기록됨 | US PathB token routing, fill ledger eviction, dashboard `kis_profile` 노출이 blocker였다. | market-specific token/fill/dashboard 회귀가 테스트로 고정되어 active plan 필요가 없어졌다. |
| `docs/plans/p0_post_isolation_qa_expansion_20260502.md` | post-isolation focused batch와 후속 batch가 green으로 기록됨 | KIS profile isolation 이후 PathB, broker truth, preflight 회귀 위험이 남아 있었다. | token provider, dashboard profile, WS idempotency 회귀 범위가 테스트로 흡수됐다. |
| `docs/plans/entry_risk_control_development_20260508.md` | `tests/test_entry_risk_controls.py`와 QA report가 있음 | KR/US daily entry cap, US broker quarantine, PlanA MFE breakeven이 흩어져 있었다. | 시장별 cap과 신규 진입 차단, MFE 보호가 구현되어 live risk control 기본선이 올라갔다. |
| `docs/plans/TRADING_IMPROVEMENT_WORKLOG_20260421.md` | 최신 실행 항목이 이 문서, tests, reports로 흡수됨 | 과거 worklog와 최신 TODO가 섞여 우선순위 판단이 흐렸다. | 기록은 Git history에 맡기고 실행 항목만 이 문서에 남긴다. |
| `docs/plans/decisions_db_operational_role_and_recovery_plan_20260512.md` | `tests/test_db_health.py`, `tests/test_recover_decisions_db.py`, `tests/test_ml_db_writer_paths.py`, `tests/test_forward_updater.py`가 `7 passed`; repo health가 `ml.decisions_db_health PASS` | `ml/test_full.py`가 운영 DB를 오염시킬 수 있었고 복구/health check가 불명확했다. | 테스트 DB override, 복구 dry-run, fixture contamination 탐지, repo health 통합이 완료됐다. |

## 남은 작업 우선순위

| 우선 | 작업 | 사유 | 개선 전 리뷰 | 개선 후 목표 |
| --- | --- | --- | --- | --- |
| P0-1 | KIS fill truth 실수신 검증 | 실제 체결 payload는 로컬 mock과 다를 수 있고, fill truth가 틀리면 중복 주문/PnL/포지션이 모두 흔들린다. | WS parser와 pending 반영은 테스트됐지만 모의/실계좌 full, partial, cancel payload는 아직 운영 검증 전이다. US 체결조회 raw 필드도 1회 마스킹 로그로 확정해야 한다. | 모의계좌 후 소액 실전에서 WS full/partial fill 수신, REST fallback 중복 방지, US fill key 조합, 당일 fill cache 재기동 복원을 검증한다. |
| P0-2 | PathB `PULLBACK_WAIT` live 전이 audit와 reconfirm shadow | `trade_ready=[]`여도 PathB conditional plan이 실주문으로 이어질 수 있어 운영자 해석과 stale-plan 위험이 있다. | 현재는 buy-zone 진입 시 safety gate 후 주문 가능하지만 `PULLBACK_WAIT -> live order`가 감사/대시보드에서 충분히 분리되지 않는다. | 전이 reason을 별도 기록하고, `PATHB_PULLBACK_WAIT_RECONFIRM_MODE=off|shadow|required` 후보를 shadow로 비교한다. |
| P0-3 | Dashboard PnL source labeling과 broker/local mismatch 노출 | 운영자가 daily PnL 또는 주문 불일치를 잘못 읽으면 live 중지/재개 판단이 틀어진다. | market-scoped PnL 필드는 생겼지만 legacy `daily_pnl` fallback source가 명확히 구분되지 않고, broker/local mismatch는 ops 화면에서 즉시 보이지 않는다. | `live_status_daily_pnl_legacy` 같은 source를 분리하고, broker-only/local-only/duplicate pending order를 dashboard ops 경고로 노출한다. |
| P0-4 | live 전 guardian/preflight 운영 runbook 고정 | guardian 코드는 구현됐지만 실제 시작 전에는 환경, token, broker truth, dashboard 상태가 매번 바뀐다. | `tests/test_live_guardian.py` 계열은 통과하지만 실제 `BLOCK_START`/`ALLOW_START` 판정은 세션 직전 상태를 봐야 한다. | live 시작 전 `tools/live_guardian.py --mode live --json` 결과를 저장하고 hard fail 0, schema mismatch 0, broker truth stale 0일 때만 시작한다. |
| P0-5 | 기존 broker open order 수동 확인 | 이미 브로커에 남은 주문은 코드 패치로 소급 제거할 수 없다. | `006340`, `047040` 같은 과거 KR 주문은 local state만 믿으면 위험하다. | KIS app/HTS/API에서 open buy order 0을 확인하고, 남아 있으면 수동 취소 후 dashboard/order state와 대조한다. |
| P1-1 | `RiskManager` KR/US 분리 | 단일 `self.risk` 구조는 cash, positions, daily halt, realized PnL 경계를 계속 섞을 수 있다. | 시장별 보정 로직은 늘었지만 핵심 runtime은 여전히 `self.risk` 중심이다. | `_rm(market)` adapter와 호환층부터 도입하고, KR/US cash/position/halt가 분리된 테스트를 추가한다. |
| P1-2 | `SafetyContext` audit field 확장 | daily return 보정은 됐지만 어떤 equity source가 쓰였는지 감사 필드가 부족하다. | 안정화된 `daily_pnl_pct`만 넣어 원인 분석이 어렵다. | `equity_return_pct`, `realized_return_pct`, `unrealized_return_pct`, `equity_source`, `broker_lag_suspected`를 audit에 남긴다. |
| P1-3 | 실행/counterfactual observability | live 규칙 변경은 실제/반사실 결과를 비교할 로그가 있어야 한다. | candidate health, route audit 일부는 있지만 ready-no-signal, prompt expansion, strategy mismatch의 반사실 outcome이 부족하다. | `audit_counterfactual_decisions/outcomes` 계열 저장소를 만들고 30m/60m outcome을 붙여 rule 변경 전 shadow 평가를 가능하게 한다. |
| P1-4 | Claude API 비용 절감 1차 | 비용을 낮추되 selection/action 품질 저하는 막아야 한다. | 최근 baseline은 약 578k tokens/day였고, hold advisor와 selection이 큰 비중이다. | AUTO_SELL_REVIEW 1인화+escalation, price-aware hold cache, preopen duplicate cache를 먼저 적용하고 품질 지표를 비교한다. |
| P2-1 | Preopen/US extended-hours 10세션 관찰 | 장전 후보는 얇은 print와 regular open continuation을 구분해야 한다. | preopen collector/scheduler/dashboard는 있으나 10세션 outcome 판단이 아직 부족하다. | 10세션 report로 Top3 selected/ready, 30m/60m return, spread/liquidity noise를 평가한 뒤 live 반영 여부를 결정한다. |
| P2-2 | Hybrid-lite와 WATCH_TRIGGER shadow 고도화 | A/B 통합, gap hard skip, cancel reactivation, watch promotion은 샘플 없이 켜면 과최적화 위험이 크다. | immediate QA는 끝났지만 miss-quality, route attribution, watch-only missed runup이 아직 충분하지 않다. | PathB miss label, PlanA gap overextension observe-only, WATCH_TRIGGER state shadow를 쌓고 활성 조건을 숫자로 고정한다. |
| P2-3 | Candidate tier state machine shadow | flat `today_tickers`는 CORE/WATCH/BENCH/QUARANTINE 상태를 직접 표현하지 못한다. | trainer tier snapshot은 advisory이고 runtime source of truth가 아니다. | `CandidateTierBook`을 shadow로 만들고 기존 list와 diff를 검증한 뒤 source-of-truth 전환을 판단한다. |
| P2-4 | KRX/BigKinds와 theme injection | KR 후보 품질 개선에는 공식 데이터와 테마 후보 주입 검증이 필요하다. | KRX/BIGKINDS credential이 없고, theme basket은 설계만 있다. | key 확보 후 dry-run, normalized SQLite 저장, digest/theme candidate injection shadow, mocked tests 순서로 진행한다. |
| P3-1 | Dual runtime SharedEngine/AccountRuntime | paper/live 단일 프로세스 구조는 RiskManager 분리 이후가 안전하다. | Phase 0 분리는 되어 있으나 SharedEngine 추출은 아직 전제 조건이 안 됐다. | KR/US/account runtime 경계가 안정된 뒤 decision ledger와 account runtime을 분리한다. |
| P3-2 | Brain Train 모드 | 거래 수를 늘리는 학습 모드는 저품질 샘플로 Brain을 오염시킬 수 있다. | 샘플 부족 문제는 있지만 운영 전략 품질이 먼저 안정되어야 한다. | 운영 품질 안정 후에도 샘플 부족이 명확할 때만 별도 weight/flag를 둔 train mode를 설계한다. |
| P3-3 | 신규 intraday/VWAP/momentum opening gate | 새 전략은 입력 품질과 실행 안전보다 후순위다. | ORP 기반 계획과 momentum opening gate 설계는 있으나 live edge 검증 전이다. | P0/P1 안정화와 shadow 성과 확인 후 VWAP reclaim/reversion, momentum opening gate를 작은 실험으로 시작한다. |

## 바로 실행 순서

1. P0-1 KIS fill truth 실수신 검증을 모의계좌에서 먼저 수행한다.
2. P0-2 PathB `PULLBACK_WAIT` audit reason과 shadow reconfirm 설정을 추가한다.
3. P0-3 dashboard PnL source/mismatch 표시를 작은 패치로 닫는다.
4. P0-4 guardian/preflight 결과를 live 시작 전 runbook 산출물로 남긴다.
5. 그 다음 P1-1 `RiskManager` 분리를 adapter 단계로 시작한다.

## 삭제/흡수한 원본 매핑

| 원본 문서 | 새 위치 |
| --- | --- |
| `docs/KIS_API_TODO.md` | P0-1 |
| `docs/KIS_WS_FILL_SYNC_PLAN.md` | P0-1 |
| `docs/plans/pathb_pullback_wait_live_policy_review_20260512.md` | P0-2 |
| `docs/plans/live_sell_reconciliation_dashboard_pnl_plan_20260506.md` | P0-3 |
| `docs/plans/order_equity_reconciliation_improvement_20260429.md` | P0-3, P0-5, P1-2 |
| `docs/plans/kr_us_live_ops_qa_20260427.md` | P0-4 |
| `docs/plans/MODULARIZATION.md` | P1-1, P3-1 |
| `docs/plans/execution_audit_observability_plan_20260430.md` | P1-3 |
| `docs/plans/live_audit_execution_future_plan_20260508.md` | P1-3, P2-2 |
| `docs/plans/claude_api_cost_optimization_plan_20260507.md` | P1-4 |
| `docs/plans/claude_prompt_usage_performance_ledger_20260511.md` | P1-4 |
| `docs/plans/us_extended_hours_screening_plan_20260502.md` | P2-1 |
| `docs/plans/hybrid_lite_attribution_miss_quality_plan_20260509.md` | P2-2 |
| `docs/plans/watch_trigger_future_backlog_20260508.md` | P2-2 |
| `docs/plans/candidate_tier_state_machine_plan_20260507.md` | P2-3 |
| `docs/plans/theme_candidate_injection_plan_20260506.md` | P2-4 |
| `docs/plans/pending_data_sources_krx_bigkinds_20260510.md` | P2-4 |
| `docs/plans/DUAL_RUNTIME_ARCHITECTURE.md` | P3-1 |
| `docs/plans/BRAIN_TRAIN_TODO.md` | P3-2 |
| `docs/plans/PLAN_intraday_strategy_roadmap.md` | P3-3 |
| `docs/plans/PLAN_momentum_opening_gate.md` | P3-3 |
| 완료 삭제 문서 5개 | `docs/DEVELOPED_WORK.md`와 위 완료 테이블 |
