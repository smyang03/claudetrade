# TODO Roadmap - 2026-05-05

이 문서는 `docs/plans/` 정리 후 남은 실행 과제를 현재 상태 기준으로 다시 우선순위화한 리포트입니다.

## 이번 정리 결과

- 완료/QA 완료 또는 구현 계획으로서 역할이 끝난 `docs/plans/*.md` 27개를 삭제했습니다.
- `docs/plans/`에는 아직 미완료이거나, 보류 판단을 계속 볼 가치가 있는 12개만 남겼습니다.
- `p0_pathb_fill_dashboard_followup_20260503.md`, `p0_post_isolation_qa_expansion_20260502.md`는 구현 항목이 대부분 들어갔지만 2026-05-05 focused QA가 실패해 삭제하지 않았습니다.

## 현재 확인 근거

- 통과: `tests/test_broker_truth_snapshot.py`, `tests/test_order_unknown_reconciliation.py`, `tests/test_dashboard_kis_profile.py`, `tests/test_live_guardian.py`, `tests/test_preopen_scheduler.py`, `tests/test_preopen_shadow.py`, `tests/test_kr_ohlcv_fallback.py` → `71 passed, 2 warnings`.
- 실패: `tests/test_pathb_runtime.py` 포함 focused QA → `71 passed, 2 failed, 2 warnings`.
- 최신 guardian 리포트: `data/v2_reports/live_guardian_20260505_195224.json` 기준 `gate=BLOCK_START`.
  - hard fail: `.env.live`와 `config/v2_start_config.json`의 포지션/일일진입 제한 충돌, start-config 내부 top-level/env_overrides 충돌.
  - hard fail로 분류: KR/US broker truth stale.
  - soft/warn: US credential mode가 `fallback_shared_kr`, 이전 세션 ORDER_UNKNOWN/stale active rows 존재.

## P0 - live 시작 전 차단/정합성

| 순위 | 과제 | 근거 | 장점 | 단점/비용 | 지연 리스크 | 권장 액션 |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | Guardian `BLOCK_START` 해소 | `kr_us_live_ops_qa_20260427.md`, 최신 guardian 리포트 | 실제 live 시작 전 config drift와 stale broker truth를 한 번에 차단 | 운영 환경/.env/start-config 확인이 필요하고 수익 로직 개선은 아님 | 잘못된 최대 포지션/진입 한도 또는 stale 계좌 truth로 live 시작 가능 | `.env.live`와 `config/v2_start_config.json` 중 authority를 하나로 정하고, broker truth refresh가 PASS가 될 때까지 live start 금지 |
| 2 | PathB runtime focused QA 2건 복구 | `p0_pathb_fill_dashboard_followup_20260503.md`, `p0_post_isolation_qa_expansion_20260502.md` | ORDER_UNKNOWN 보유 중 신규 PathB plan 생성 차단과 same-day reentry 차단 계약을 복구 | PathB 게이트를 만지므로 과차단/기존 테스트 계약 변경 가능성 | ORDER_UNKNOWN 노출이 있는데 새 plan이 생성되거나, stop 후 재진입 차단 의미가 불명확해짐 | 실패 2건을 먼저 원인 분석. 의도 변경이면 테스트/문서 계약을 같이 갱신하고, 아니면 PathB gate를 복구 |
| 3 | 실제 KIS 미체결/ORDER_UNKNOWN 정리 | `order_equity_reconciliation_improvement_20260429.md`, guardian stale rows | 기존 미체결/불명 상태가 신규 노출 판단을 오염시키지 않음 | HTS/API 대조와 수동 취소가 필요 | KR `006340`, `047040` 또는 이전 US ORDER_UNKNOWN/stale SELL_SENT가 중복 노출/잘못된 차단을 유발 | HTS/API에서 미체결 0 확인, 필요한 경우 수동 취소 후 guardian 재실행 |
| 4 | KIS WS 체결통보 실수신 검증 | `KIS_WS_FILL_SYNC_PLAN.md` | REST 지연보다 빠른 fill truth 확보, full/partial fill 반영 신뢰도 상승 | 모의/실계좌 payload 수집과 소액 주문 검증 필요 | WS와 REST fallback 중복 반영 또는 실체결 누락이 실제 주문에서만 드러남 | `KIS_HTS_ID`, AES/pycryptodome 확인 후 모의→소액 실전 순서로 full/partial fill 수신 검증 |

## P1 - 계좌/시장 경계와 운영 가시성

| 순위 | 과제 | 근거 | 장점 | 단점/비용 | 지연 리스크 | 권장 액션 |
| ---: | --- | --- | --- | --- | --- | --- |
| 5 | `RiskManager` KR/US 분리 | `MODULARIZATION.md` P0, 현재 `trading_bot.py`의 단일 `self.risk` | 시장별 cash/positions/halt/daily_pnl 축이 분리되어 live 계좌 경계가 명확해짐 | 호출부가 많아 작은 패치로도 회귀 위험이 큼 | KR/US 포지션·현금·daily halt가 서로 섞이는 버그를 찾기 어려움 | 전체 모듈화와 분리해 `_rm(market)` 도입부터 작은 단계로 진행 |
| 6 | 주문/계좌 reconciliation follow-up | `order_equity_reconciliation_improvement_20260429.md` | broker/local mismatch, SafetyContext audit, close metric dedupe가 운영자에게 보임 | 대부분 dashboard/ops 배선이라 작은 PR 여러 개가 필요 | mismatch가 로그에만 남고 live 운영자가 즉시 못 봄 | P0 수동 정리 후 dashboard mismatch 노출과 `path_run_id` dedupe부터 처리 |
| 7 | Exit/후보 관찰 보강 | `execution_audit_observability_plan_20260430.md` | post-exit 30m/close 결과, cancel status, opening simulation으로 판단 품질을 측정 가능 | 직접 수익 개선 전 로그/대시보드 작업이 많음 | loss-cap/Claude SELL/opening 보호 적용 여부를 감으로 결정하게 됨 | `post_exit_close_return_pct`와 ORDER_UNKNOWN `cancel_status` visibility를 먼저 구현 |
| 8 | KIS 체결조회 후처리 보강 | `KIS_API_TODO.md` | US 체결조회 필드/키 조합/당일 캐시가 명확해져 broker truth 보강 | API 응답 원문 마스킹 로그와 재기동 캐시 설계 필요 | 주문번호 직접 검색 제한 시 체결 truth 확인이 계속 불안정 | US fill raw 1회 마스킹 로그 → 안정 키 조합 → 당일 fill cache 순서로 진행 |

## P2 - 판단 품질/후보 발견 개선

| 순위 | 과제 | 근거 | 장점 | 단점/비용 | 지연 리스크 | 권장 액션 |
| ---: | --- | --- | --- | --- | --- | --- |
| 9 | Market breadth / prompt contract 개선 | `audit/market_analysis_tune_prompt_audit_20260501.md` | Claude가 개별 대형주 예시보다 breadth/count 기반으로 장세 판단 | 프롬프트 변경은 shadow 비교가 필요하고 즉시 live gate에 연결하면 위험 | morning/tune 판단이 계속 NVDA/AAPL 등 특정 종목에 과앵커링 | P0에서 null/breadth 저장은 완료됐으므로 analyst/tune prompt만 shadow-first로 교체 |
| 10 | Preopen/US extended-hours 5~10세션 관찰 | `us_extended_hours_screening_plan_20260502.md` | TWLO 같은 장전 강세 후보를 정규장 전에 watch 우선순위로 포착 가능 | provider 품질·스프레드·volume noise 검증이 필요 | 검증 없이 빠르게 붙이면 얇은 premarket print를 추격할 수 있음 | 이미 구현된 preopen shadow/scheduler를 운영하고, provider 신뢰도/성과 리포트를 먼저 작성 |
| 11 | PEAD surprise manual review | `CLAUDE.md`, `state/pead_shadow_state.json` | earnings surprise 입력을 안전하게 prompt에 올릴 근거 확보 | 현재 US surprise EPS null rate가 100%라 수작업 확인 필요 | null/저품질 surprise가 Claude 판단을 오염 | prompt 노출은 금지 유지, tier null-rate와 sample 10건 manual review만 진행 |

## P3 - 보류/지금은 낮은 우선순위

| 과제 | 근거 | 지금 보류하는 이유 | 다시 볼 조건 |
| --- | --- | --- | --- |
| `MODULARIZATION.md` P1~P4 파일 분리 | `MODULARIZATION.md` | P0 PathB/guardian/RiskManager가 먼저이며, 큰 이동은 회귀 추적을 어렵게 함 | live gate가 clean이고 RiskManager 분리 후 |
| Dual runtime 구조 | `DUAL_RUNTIME_ARCHITECTURE.md` | shared/account runtime은 모듈화 이후가 안전 | paper/live 계좌 경계가 코드상 분리된 뒤 |
| Brain Train 모드 | `BRAIN_TRAIN_TODO.md` | 거래 수를 늘리려다 저품질 샘플이 Brain을 오염시킬 수 있음 | 운영 전략 품질이 안정되고 샘플 부족이 명확할 때 |
| 신규 전략/게이트 | `PLAN_intraday_strategy_roadmap.md`, `PLAN_momentum_opening_gate.md` | 입력 품질/운영 안전보다 후순위 | P0/P1 안정화와 shadow 성과 확인 후 |
| 과거 worklog 재개 | `TRADING_IMPROVEMENT_WORKLOG_20260421.md` | 대부분 최신 TODO/테스트/리포트로 흡수됐고 직접 blocker가 아님 | 필요한 항목만 새 plan으로 재작성 |

## 바로 실행 순서

1. Guardian hard fail 해소: config 충돌 정리 → broker truth refresh → `tools/live_guardian.py --mode live --json` 재실행.
2. PathB focused QA 2건 복구 또는 의도 변경 문서화.
3. HTS/API에서 미체결/ORDER_UNKNOWN 관련 실제 계좌 상태 확인.
4. KIS WS fill notice 실수신 검증.
5. `RiskManager` KR/US 분리 설계를 작은 패치로 시작.
6. 이후 판단 품질 작업은 prompt shadow 비교부터 진행.
