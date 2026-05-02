# TODO Roadmap - 2026-05-02

이 문서는 `docs/plans/`, `docs/*TODO*.md`, `audit/*.md`, `CLAUDE.md`의 진행 중 항목을 기준으로 다시 정리했습니다. 완료/QA 완료로 볼 수 있는 문서는 [DEVELOPED_WORK.md](DEVELOPED_WORK.md)로 분리했고, 여기에는 실제로 다음 행동이 필요한 항목만 남깁니다.

## 판단 기준

| 값 | 의미 |
| --- | --- |
| 직접 연관성 높음 | live 주문, 체결, 계좌 truth, 손실 제한, 실시간 판단 품질에 직접 영향 |
| 직접 연관성 중간 | shadow 관찰, 품질 개선, dashboard/reporting처럼 의사결정 보조 영향 |
| 직접 연관성 낮음 | 구조 개선, 장기 리팩터링, 참고 설계 문서 |
| 진행 | 다음 작업 사이클에서 실제로 처리 |
| 관찰/Shadow | live 동작 변경 없이 로그/리포트만 수집 |
| 보류 | 전제 조건 충족 전까지 하지 않음 |
| 생략 | 지금 성과/안전과 직접 연결이 약해 하지 않음 |

## 완료로 분리한 항목

아래 항목은 계획 대비 구현/QA 결과가 문서에 있으므로 `DEVELOPED_WORK.md`에서 완료 항목으로 관리합니다. 후속 TODO가 남은 경우에는 아래 우선순위 표에 별도 행으로만 남겼습니다.

| 문서 | 완료 판단 |
| --- | --- |
| [Recovery and Follow-up TODO](plans/recovery_and_followup_todo_20260501.md) | health check 도구, 자동 trigger, manual/JSON 검증 완료. 비용/DB maintenance는 별도 후속 |
| [Claude Quality Contract Improvement](plans/claude_quality_contract_improvement_20260501.md) | P0-P9와 mojibake P0 복구/QA 완료. F1-F3만 deferred |
| [Priority Hotfix Improvement Plan](../audit/priority_hotfix_improvement_plan_20260501.md) | 운영 핫픽스 및 확장 QA 완료 |
| [Path B Sell Truth Reconcile](plans/pathb_sell_truth_reconcile_20260428.md) | sell truth state machine과 QA 완료 |
| [ORDER_UNKNOWN Auto-Resolve and PathB Pre-Close Carry](plans/order_unknown_preclose_carry_plan_20260430.md) | Phase 1/2 구현, focused/contract test 완료 |
| [Path B ORDER_UNKNOWN Dashboard Reconcile](plans/pathb_order_unknown_dashboard_reconcile_20260501.md) | 원인 분석, API 보강, 테스트/QA 체크 완료 |
| [Soft Exit Arbitration](plans/soft_exit_arbitration_20260430.md) / [Follow-up](plans/soft_exit_arbitration_followup_20260501.md) | Phase 1과 follow-up QA 완료 |
| [KR Opening Quality / Ops Correctness Round 1](plans/kr_opening_quality_ops_round1_20260428.md) | Phase 1-6 구현 및 `pytest tests`/preflight 완료. live WARN은 운영 follow-up |

## P0 - live 안전/계좌 truth 우선

| 순위 | 상태 | 문서 | 해야 할 일 | 직접 연관성 | 진행 판단 |
| ---: | --- | --- | --- | --- | --- |
| 1 | 운영 확인 | [Order / Equity Reconciliation Improvement](plans/order_equity_reconciliation_improvement_20260429.md) | KR `006340`, `047040` 및 관련 주문번호가 KIS 앱/HTS/API에서 실제 미체결 0인지 최종 확인. 남아 있으면 수동 취소 | 높음 | 진행. 확인 전에는 해당 KR PathB 신규 진입 신뢰도를 낮게 봄 |
| 2 | Live Verify | [KIS WebSocket Fill Sync Plan](KIS_WS_FILL_SYNC_PLAN.md) | 실제 KR/US 모의 또는 실전 notice payload 수집, full/partial fill 반영 확인, WS 선반영 후 REST fallback 중복 반영 여부 확인 | 높음 | 진행. 로컬 테스트 완료라 다음은 실제 수신 검증 |
| 3 | 코드/문서 대조 | [MODULARIZATION](plans/MODULARIZATION.md) P0 | `RiskManager` KR/US 분리 필요성 재확인 후 작은 안전 패치로 분리. 전체 모듈화와 섞지 않기 | 높음 | 진행. 단일 `self.risk`가 시장별 cash/halt/pnl 판단과 충돌할 수 있어 모듈화보다 먼저 처리 |
| 4 | 코드/문서 대조 | [KIS API TODO](KIS_API_TODO.md) / `kis_api.py` | US 전용 account/app key/secret/paper 설정이 token/header/order/balance 전체에 실제 적용되는지 확인하고, common-key fallback이 의도인지 문서화 | 높음 | 진행. live US 계좌 경계와 직접 관련. KIS indicator/조건검색은 이 작업과 분리 |
| 5 | 구현 상태 감사 | [Broker Truth Live Plan](plans/broker_truth_live_plan_20260427.md) | 현재 코드가 broker snapshot, ORDER_UNKNOWN reconcile, dashboard/Telegram snapshot-first 원칙을 어디까지 충족하는지 최신 preflight로 재확인 | 높음 | 진행. 이미 여러 후속 패치가 들어갔으므로 plan-vs-code 재대조 필요 |

## P1 - 주문/체결/운영 정확성 follow-up

| 순위 | 상태 | 문서 | 해야 할 일 | 직접 연관성 | 진행 판단 |
| ---: | --- | --- | --- | --- | --- |
| 6 | Mixed | [Order / Equity Reconciliation Improvement](plans/order_equity_reconciliation_improvement_20260429.md) | `SafetyContext` audit field 확장, broker/local mismatch dashboard 노출, v2 close metric `path_run_id` dedupe, ops review `session_date` grouping | 높음 | 진행. P0 확인 후 작은 단위로 처리 |
| 7 | Partial | [Execution Audit Observability Plan](plans/execution_audit_observability_plan_20260430.md) | `post_exit_30m_return_pct` timer/restart fallback, `post_exit_close_return_pct` session-close 보강, ORDER_UNKNOWN `cancel_status/cancel_confirmed_at` visibility | 중간~높음 | 진행. live 주문 차단보다 후순위지만 청산 품질 검증에 필요 |
| 8 | 운영 QA | [KR/US Live Ops QA Plan](plans/kr_us_live_ops_qa_20260427.md) | 다음 live 전 `tools/live_preflight.py` 최신 실행, FAIL=0 확인, 남은 WARN을 계좌/DB/스냅샷/캘린더로 분류 | 높음 | 진행. 반복 운영 체크로 유지 |
| 9 | 운영 관찰 | [KR Opening Quality / Ops Correctness Round 1](plans/kr_opening_quality_ops_round1_20260428.md) | KOSDAQ raw=0 WARN 반복 여부, 09:05 fresh screener 품질 로그, 이전 live preflight WARN 해소 여부 확인 | 중간 | 관찰/Shadow. WARN 반복 시 P1 작업으로 승격 |
| 10 | API 보강 | [KIS API TODO](KIS_API_TODO.md) | US 체결조회 응답 원문 1회 마스킹 로그, 주문번호 직접검색 제한 시 안정 키 조합, 당일 fill cache 재사용 여부 검토 | 높음 | 진행. 체결 truth 보강만 진행하고 지표/조건검색 대체는 보류 |
| 11 | Deferred | [ORDER_UNKNOWN Auto-Resolve and PathB Pre-Close Carry](plans/order_unknown_preclose_carry_plan_20260430.md) | previous-day order-number fill lookup 가능성, PathB carry performance report, `CARRIED_OUT` reporting 보강 | 중간 | 보류/부분 진행. 실제 미해결 사례가 생기면 우선순위 상승 |

## P2 - 판단 품질/후보 발견 개선

| 순위 | 상태 | 문서 | 해야 할 일 | 직접 연관성 | 진행 판단 |
| ---: | --- | --- | --- | --- | --- |
| 12 | Audit TODO | [Market Analysis / Tuning Prompt Audit](../audit/market_analysis_tune_prompt_audit_20260501.md) | `market_breadth_summary` 추가, analyst prompt breadth-first 전환, US bear 축 보강, tune current-breadth delta, VIX/DXY null 처리, `RSI` ticker disambiguation, old/new shadow 비교 | 높음 | 진행. 주문 안전 P0/P1 뒤에 shadow-first로 적용 |
| 13 | Shadow 계획 | [US Extended-Hours Screening Plan](plans/us_extended_hours_screening_plan_20260502.md) | KIS/current provider extended-hours quote/ranking 검증, premarket shadow JSONL 저장, dashboard watch 후보 표시, 10-session outcome report | 중간~높음 | 관찰/Shadow. live buy/order path는 건드리지 않음 |
| 14 | Deferred | [Trading Decision Contract Improvement](plans/trading_decision_contract_improvement_20260501.md) | F1: `market_regime`, `data_quality`, `new_buy_permission`, `max_gross_exposure_pct`를 live gate가 아니라 log-only/shadow로 관찰 | 중간~높음 | 관찰/Shadow. 즉시 hard gate 연결 금지 |
| 15 | 진행 중 | [CLAUDE.md](../CLAUDE.md) TODO / PEAD state | PEAD surprise shadow 5거래일은 충족됐지만 manual review 미완료, 최근 summary에서 surprise EPS null rate 100% 확인 필요 | 중간 | 진행은 manual review만. prompt surprise enable은 보류 |
| 16 | Partial | [Execution Audit Observability Plan](plans/execution_audit_observability_plan_20260430.md) | candidate health dashboard, opening simulation JSONL/dashboard, 3~5세션 관찰 후 opening protection/Claude SELL guard 여부 결정 | 중간 | 관찰/Shadow. hard block은 아직 하지 않음 |
| 17 | Reference | [Path B Claude Price Live Plan](../pathb_v2_live_plan.md) | live 적용 상태와 현재 PathB 후속 TODO를 최신 완료 문서들과 맞춰 갱신 | 낮음~중간 | 보류. 구현 변경보다 문서 싱크 성격 |

## P3 - 지금은 안 하거나 전제 조건 뒤로 미룰 항목

| 순위 | 상태 | 문서 | 해야 할 일 | 직접 연관성 | 진행 판단 |
| ---: | --- | --- | --- | --- | --- |
| 18 | Deferred | [Trading Decision Contract Improvement](plans/trading_decision_contract_improvement_20260501.md) | F2: prompt 파일 외부화 | 낮음 | 보류. in-code contract 안정화 후 진행 |
| 19 | Deferred | [Trading Decision Contract Improvement](plans/trading_decision_contract_improvement_20260501.md) | F3: selection rank / execution plan 2-call 분리 | 낮음~중간 | 보류. shadow-only 비교 전 live 적용 금지 |
| 20 | Open | [MODULARIZATION](plans/MODULARIZATION.md) P1-P3 | `bot/ops.py`, `dashboard.py`, `tuning.py`, `broker.py`, `execution.py`, `scanner.py` 등 파일 분리 | 낮음~중간 | 보류. P0 Risk/KIS 계정 경계 해결 후 로직 변경 없이 이동만 |
| 21 | Open | [Dual Runtime Architecture](plans/DUAL_RUNTIME_ARCHITECTURE.md) | `SharedEngine`, `AccountRuntime`, 단일 프로세스 dual runtime, dashboard All/Paper/Live | 낮음 | 보류. modularization 완료 전에는 하지 않음 |
| 22 | Open | [Brain Train TODO](plans/BRAIN_TRAIN_TODO.md) | 학습용 별도 거래 모드 설계 | 낮음 | 생략/보류. 저품질 거래가 Brain을 오염시킬 수 있음 |
| 23 | Open | [PLAN Momentum Opening Gate](plans/PLAN_momentum_opening_gate.md) / [Intraday Strategy Roadmap](plans/PLAN_intraday_strategy_roadmap.md) | 신규 전략/게이트 설계 재평가 | 낮음~중간 | 보류. 입력 품질/운영 안전 작업 후 재검토 |
| 24 | Reference | [V2 Production Design](../v2.md) | 현재 구현과 설계 차이 갱신 | 낮음 | 보류. 릴리즈 노트 성격으로 묶어서 처리 |
| 25 | Optional | [KIS API TODO](KIS_API_TODO.md) | KIS 기술지표 API, 조건검색, KIS 해외 차트로 yfinance/Alpha Vantage 대체 검토 | 낮음 | 생략. 현재 blocker가 아니므로 API 안정화 뒤 검토 |

## 바로 진행 순서

1. P0-1, P0-2를 운영 확인으로 먼저 닫습니다.
2. P0-3, P0-4는 전체 리팩터링 없이 계좌/시장 경계만 작은 패치로 처리합니다.
3. P0-5와 P1-8 preflight로 broker truth / dashboard / Telegram 현재 상태를 재확인합니다.
4. 그 다음 P2-12 market breadth/prompt 개선을 shadow-first로 진행합니다.
5. US extended-hours는 P2-13 범위처럼 research-only 로그부터 시작하고 live 주문 동작은 바꾸지 않습니다.

## 정리 정책

- 완료된 계획 문서는 삭제하지 않고 [DEVELOPED_WORK.md](DEVELOPED_WORK.md)에서 완료 항목으로 연결합니다.
- `docs/plans/` 안에서 완료된 문서는 이 문서에 중복 TODO로 남기지 않습니다. 단, deferred follow-up은 별도 행으로 남깁니다.
- `data/backtest_audit/**/*.md`와 `data/v2_reports/**/*.md`는 자동 생성 산출물로 유지합니다.
- 광범위한 mojibake 문자열 정리는 생략합니다. 코드가 주석에 삼켜지는 실행 영향 패턴만 health check 대상으로 유지합니다.
