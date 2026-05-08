# TODO Roadmap - 2026-05-08

이 문서는 `docs/plans/`의 계획 문서를 현재 코드와 focused QA 결과 기준으로 다시 분류한 실행 우선순위 리포트입니다. 완료로 확인된 plan은 아래 "삭제/정리 후보"에 모아 두고, 실제 실행해야 할 항목만 우선순위 표에 남깁니다.

## 이번 코드 기준 재분류 결과

- `docs/plans/` 현재 Markdown은 17개입니다. 이 중 13개는 active/보류 plan이고, 4개는 삭제 또는 archive 후보입니다.
- 2026-05-05에 남겼던 PathB runtime QA 실패 2건은 현재 재현되지 않습니다.
- `p0_pathb_fill_dashboard_followup_20260503.md`, `p0_post_isolation_qa_expansion_20260502.md`는 원래 범위의 focused QA가 green이므로 삭제 후보로 분류합니다.
- `entry_risk_control_development_20260508.md`는 코드와 focused QA 기준 구현 완료입니다. active 우선순위에서 제거하고 삭제/정리 후보로 이동합니다.
- `TRADING_IMPROVEMENT_WORKLOG_20260421.md`는 최신 TODO와 테스트/리포트로 흡수된 과거 worklog라 archive/delete 후보입니다.
- `live_sell_reconciliation_dashboard_pnl_plan_20260506.md`는 구현이 많이 들어왔지만 dashboard QA 실패와 gate 누락이 남아 active로 유지합니다.

## 재검토 결론

- Guardian `BLOCK_START`는 `tools/live_guardian.py`의 start gate입니다. hard fail이 있으면 `ok=false`, `gate=BLOCK_START`, CLI exit code `2`가 되므로 새 live 시작/재시작 기준으로는 hard block입니다. 다만 이미 떠 있는 bot을 kill한다는 의미는 아닙니다.
- 사용자 검토대로 Entry risk control은 `RECOVERY_MICRO`보다 먼저 닫는 것이 맞았지만, 현재 코드 기준으로는 이미 닫힌 상태입니다. 따라서 "3번보다 먼저 수행"이 아니라 "완료/삭제 후보"로 재분류합니다.
- 남은 P0 순서는 `Guardian BLOCK_START` -> `Live sell/dashboard PnL` -> `RECOVERY_MICRO first-stop gate exemption` -> `KIS WS 실수신 검증`입니다.
- `RECOVERY_MICRO`는 first-stop cooldown만 좁게 예외 처리해야 하므로 과허용 테스트가 필요합니다. Entry risk control보다 늦춘다는 판단은 유효하지만, Entry risk가 완료되었으므로 다음 profit-protection active item으로 남깁니다.
- 원래 6~10번 항목의 상대 순서는 유지합니다. 완료 항목을 active 표에서 제거하면서 번호만 앞으로 당겨졌습니다.

## 현재 확인 근거

- 통과: `python -m pytest tests/test_pathb_runtime.py -q` -> `39 passed, 2 warnings`.
- 통과: `python -m pytest tests/test_pathb_runtime.py tests/test_kis_ws_fill_notice.py tests/test_dashboard_kis_profile.py tests/test_live_order_reconciliation.py tests/test_live_preflight_credentials.py tests/test_p0_data_quality.py tests/test_p0_data_quality_backfill.py -q` -> `81 passed, 2 warnings`.
- 통과: `python -m pytest tests/test_pathb_runtime.py tests/test_broker_truth_snapshot.py tests/test_dashboard_kis_profile.py tests/test_live_preflight_credentials.py tests/test_kis_ws_fill_notice.py tests/test_live_order_reconciliation.py -q` -> `72 passed, 2 warnings`.
- 통과: `python -m pytest tests/test_pid_lock.py tests/test_startup_token_refresh.py tests/test_kis_market_profile.py tests/test_kis_token_auto_refresh.py tests/test_live_token_balance.py tests/test_order_equity_reconciliation_improvement.py tests/test_v2_phase5.py -q` -> `36 passed, 2 warnings`.
- 통과: `python -m pytest tests/test_live_sell_pending_reconcile.py tests/test_pathb_realized_pnl_dedupe.py -q` -> `9 passed, 2 warnings`.
- 통과: `python -m pytest tests/test_entry_risk_controls.py -q` -> `6 passed, 2 warnings`.
- 통과: `python -m py_compile trading_bot.py runtime/v2_lifecycle_runtime.py runtime/pathb_runtime.py`.
- 실패: `python -m pytest tests/test_order_equity_reconciliation_improvement.py tests/test_dashboard_pathb.py tests/test_auto_sell_claude_gate.py -q` -> `63 passed, 2 failed, 2 warnings`.
  - `tests/test_dashboard_pathb.py::DashboardPathBTests::test_lifetime_realized_pnl_summary_adds_active_session_realized_adjustment`
  - `tests/test_dashboard_pathb.py::DashboardPathBTests::test_lifetime_realized_pnl_summary_splits_markets_and_excludes_unknown_cost_basis`
- 최신 guardian 리포트: `data/v2_reports/live_guardian_20260508_140245.json` 기준 `gate=BLOCK_START`.
  - hard fail: `broker_truth.us_stale_state`.
  - soft fail: US credential fallback, 이전 ORDER_UNKNOWN/stale PathB rows, lifecycle warnings, pid lock, calendar/wait timing operational 확인 등.
- 코드 확인:
  - `trading_bot.py`는 여전히 단일 `self.risk` 중심입니다. `RiskManager` KR/US 분리는 미완료입니다.
  - `runtime/candidate_tier_state.py`와 tier book runtime은 없습니다. `candidate_tier_state_machine_plan_20260507.md`는 future plan입니다.
  - `entry_risk_control_development_20260508.md`는 구현 완료입니다. `tests/test_entry_risk_controls.py`가 시장별 daily cap, US broker quarantine, PlanA MFE breakeven을 커버하고 QA report가 있습니다.
  - theme injection 관련 runtime counter/flow(`theme_candidate_count`, `theme_injection`)는 코드에 없습니다. `theme_candidate_injection_plan_20260506.md`는 research/design 단계입니다.
  - `minority_report/hold_advisor.py`는 여전히 bull/bear/neutral 3인 호출 구조입니다. 비용 최적화 plan은 일부 cooldown/기록 외에 핵심 절감 적용 전입니다.

## P0 - live 시작 전 차단/정합성

| 순위 | 과제 | 근거 | 장점 | 단점/비용 | 지연 리스크 | 권장 액션 |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | Guardian `BLOCK_START` 해소 | `kr_us_live_ops_qa_20260427.md`, `live_guardian_20260508_140245.json` | stale broker truth 상태에서 live 시작을 차단 | 운영 계좌/API refresh 확인 필요 | stale US 계좌 truth로 주문/보유 판단이 오염될 수 있음 | US broker truth refresh가 PASS가 될 때까지 live start 금지 |
| 2 | Live sell/dashboard PnL hotfix 마무리 | `live_sell_reconciliation_dashboard_pnl_plan_20260506.md` | pending sell 유실, 중복 실현손익, dashboard PnL 오표시를 줄임 | dashboard accounting 테스트와 live state 의존성을 정리해야 함 | 당일 실현손익과 청산 표시가 live 운영 판단을 왜곡 | 실패한 `tests/test_dashboard_pathb.py` 2건 원인 수정 후 plan QA 재실행 |
| 3 | `RECOVERY_MICRO` first-stop gate exemption 구현 | `live_sell_reconciliation_dashboard_pnl_plan_20260506.md`, `_new_buy_block_state()` | 첫 손절 후 의도한 recovery micro 진입이 실제 gate를 통과 | stop cluster gate라 회귀 테스트 필요 | recovery 후보가 생성돼도 `STOP_CLUSTER_FIRST_STOP_COOLDOWN`에서 다시 차단 | `_new_buy_block_state()`에서 `RECOVERY_MICRO` 예외를 좁게 추가하고 same-ticker/2nd stop 차단 테스트 추가 |
| 4 | KIS WS 체결통보 실수신 검증 | `KIS_WS_FILL_SYNC_PLAN.md`, `tests/test_kis_ws_fill_notice.py` | REST 지연보다 빠른 fill truth 확보 | 모의/실계좌 payload 수집 필요 | WS/REST 중복 반영 또는 실체결 누락이 실제 주문에서만 드러남 | 모의 -> 소액 실전 순서로 full/partial fill 수신 검증 |

## P1 - 계좌/시장 경계와 운영 가시성

| 순위 | 과제 | 근거 | 장점 | 단점/비용 | 지연 리스크 | 권장 액션 |
| ---: | --- | --- | --- | --- | --- | --- |
| 5 | `RiskManager` KR/US 분리 | `MODULARIZATION.md`, `trading_bot.py` 단일 `self.risk` | 시장별 cash/positions/halt/daily_pnl 경계가 명확해짐 | 호출부가 많아 작은 단계로 나눠야 함 | KR/US 포지션·현금·daily halt 혼선이 계속 숨음 | `_rm(market)` adapter부터 시작하고 기존 `self.risk` 호환층을 둠 |
| 6 | 주문/계좌 reconciliation follow-up | `order_equity_reconciliation_improvement_20260429.md` | broker/local mismatch와 SafetyContext audit이 운영자에게 보임 | dashboard/ops 배선이 필요 | mismatch가 로그에만 남고 운영자가 즉시 못 봄 | `path_run_id` dedupe 완료분은 체크 처리하고, dashboard mismatch 노출/SafetyContext audit만 남김 |
| 7 | Exit/후보 관찰 보강 | `execution_audit_observability_plan_20260430.md` | post-exit 결과, cancel status, opening simulation을 근거로 판단 가능 | 로그/대시보드 작업이 많음 | Claude SELL/opening 보호 적용 여부를 감으로 결정 | `post_exit_close_return_pct`, ORDER_UNKNOWN cancel visibility부터 구현 |
| 8 | Claude API 비용 1차 절감 | `claude_api_cost_optimization_plan_20260507.md` | daily token 사용량을 낮추되 핵심 판단 품질 유지 | shadow 비교와 stage별 안전장치 필요 | 호출량 증가가 계속 누적되고 비용/latency가 악화 | AUTO_SELL_REVIEW 1인화, hold_advisor cache, preopen duplicate cache를 shadow/guarded로 적용 |
| 9 | KIS 체결조회 후처리 보강 | `KIS_API_TODO.md` | US 체결조회 필드/키 조합/당일 캐시가 명확해짐 | API 응답 원문 마스킹 로그 필요 | 주문번호 직접 검색 제한 시 fill truth 확인이 불안정 | US fill raw 1회 마스킹 로그 -> 안정 키 조합 -> 당일 fill cache 순서 |

## P2 - 판단 품질/후보 발견 개선

| 순위 | 과제 | 근거 | 장점 | 단점/비용 | 지연 리스크 | 권장 액션 |
| ---: | --- | --- | --- | --- | --- | --- |
| 10 | Preopen/US extended-hours 10세션 관찰 | `us_extended_hours_screening_plan_20260502.md`, `preopen/*` | 정규장 전 후보 우선순위 판단 근거 확보 | provider 품질·스프레드·volume noise 검증 필요 | 얇은 premarket print를 과신할 수 있음 | preopen shadow/scheduler를 계속 운영하고 10-session outcome report 작성 |
| 11 | Theme candidate injection 설계 검증 | `theme_candidate_injection_plan_20260506.md` | 반도체/AI/전력 같은 테마 후보가 selection 표면에 올라옴 | 후보 cap/diversity를 건드려 기존 고품질 후보를 밀 수 있음 | 테마 강세장 후보가 기본 screener 밖이면 놓침 | shadow-only injection counter와 prompt visibility 테스트부터 추가 |
| 12 | Candidate tier state machine shadow 설계 | `candidate_tier_state_machine_plan_20260507.md` | flat `today_tickers`를 stateful lifecycle로 전환 | 상태 소유권 refactor라 회귀 위험 큼 | BENCH/QUARANTINE이 계속 advisory 수준에 머묾 | 현재 trainer guard 안정 후 `runtime/candidate_tier_state.py`를 shadow-only로 추가 |
| 13 | Market breadth / prompt contract 개선 | `audit/market_analysis_tune_prompt_audit_20260501.md` | Claude가 개별 대형주 예시보다 breadth/count 기반으로 장세 판단 | 프롬프트 변경은 shadow 비교 필요 | morning/tune 판단이 특정 종목에 과앵커링 | breadth 저장은 유지하고 analyst/tune prompt를 shadow-first로 교체 |

## P3 - 보류/지금은 낮은 우선순위

| 과제 | 근거 | 지금 보류하는 이유 | 다시 볼 조건 |
| --- | --- | --- | --- |
| `MODULARIZATION.md` P1~P4 파일 분리 | `MODULARIZATION.md` | P0 guardian/live sell/RiskManager 경계가 먼저 | live gate clean 및 `_rm(market)` 도입 후 |
| Dual runtime 구조 | `DUAL_RUNTIME_ARCHITECTURE.md` | shared/account runtime은 모듈화 이후가 안전 | paper/live 계좌 경계가 코드상 분리된 뒤 |
| Brain Train 모드 | `BRAIN_TRAIN_TODO.md` | 샘플 수를 늘리다 저품질 거래가 Brain을 오염시킬 수 있음 | 운영 전략 품질이 안정되고 샘플 부족이 명확할 때 |
| 신규 전략/게이트 | `PLAN_intraday_strategy_roadmap.md`, `PLAN_momentum_opening_gate.md` | 입력 품질/운영 안전보다 후순위 | P0/P1 안정화와 shadow 성과 확인 후 |

## 삭제/정리 후보

| 문서 | 현재 코드 기준 판단 | 정리 액션 |
| --- | --- | --- |
| `docs/plans/p0_pathb_fill_dashboard_followup_20260503.md` | 원래 blocker였던 PathB runtime focused QA가 green입니다. | `DEVELOPED_WORK.md`에 완료 요약만 남기고 `docs/plans/`에서 삭제 가능 |
| `docs/plans/p0_post_isolation_qa_expansion_20260502.md` | post-isolation QA 1/2차 focused command가 green입니다. | `DEVELOPED_WORK.md`에 완료 요약만 남기고 `docs/plans/`에서 삭제 가능 |
| `docs/plans/entry_risk_control_development_20260508.md` | 구현 완료이며 `tests/test_entry_risk_controls.py`와 QA report가 있습니다. | `docs/reports/entry_risk_control_qa_20260508.md`와 `DEVELOPED_WORK.md` 요약만 남기고 `docs/plans/`에서 삭제 가능 |
| `docs/plans/TRADING_IMPROVEMENT_WORKLOG_20260421.md` | 과거 worklog이며 실행 가능한 최신 항목은 이 문서와 개별 plan/test/report로 흡수됐습니다. | 필요하면 archive, 아니면 삭제 가능 |

## 바로 실행 순서

1. US broker truth stale hard fail 해소 후 `tools/live_guardian.py --mode live --json` 재실행.
2. `live_sell_reconciliation_dashboard_pnl_plan_20260506.md`의 dashboard PathB 실패 2건 수정.
3. `RECOVERY_MICRO` first-stop cooldown exemption과 과허용 방지 테스트 추가.
4. KIS WS fill notice 실수신 검증.
5. 완료된 PathB QA plan 2개, Entry risk control plan, 과거 worklog를 삭제/보관 처리.
6. `RiskManager` KR/US 분리의 첫 adapter 패치 시작.
