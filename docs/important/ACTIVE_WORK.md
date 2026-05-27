# Active Work

Updated: 2026-05-27

## 2026-05-27 Implementation Delta

- P0-4: `EGW00133` token rate-limit classifier/cooldown, shared KR/US cooldown marker, cached-token preservation, and startup fail-closed path are now implemented in code and covered by focused tests. Keep only operator-visible preflight/dashboard status and environment QA as active follow-up.
- P0-6: PathB live entry scan now blocks with `BLOCKED_BROKER_TRUTH` when bot token or balance provider is unavailable. Keep only ops/preflight/dashboard visibility for TTL, attempt, latency, skip/block reason as active follow-up.
- MD cleanup decision: do not delete the active P0/P1 MD set yet. P0-3/P1 items and the P0-4/P0-6 visibility follow-up are still active.

이 문서는 정리 후 남은 단일 작업 원장이다. `docs/plans/`, `docs/reports/`, 일회성 QA/분석 JSON은 완료 또는 흡수된 뒤 삭제 대상이며, 활성 백로그는 이 파일과 [core/TODO_ROADMAP.md](core/TODO_ROADMAP.md)에만 둔다.

P0/P1의 코드 레벨 상세 개발 요구서는 [P0_P1_CODE_LEVEL_DEV_REQUIREMENTS_20260527.md](P0_P1_CODE_LEVEL_DEV_REQUIREMENTS_20260527.md)를 기준으로 본다. 현재 working tree 기준 코드 확인 결과와 남은 개발 순서는 [P0_P1_CODE_LEVEL_RECHECK_REPORT_20260527.md](P0_P1_CODE_LEVEL_RECHECK_REPORT_20260527.md)를 함께 본다.

## 정리 기준

- 완료 판단은 커밋 히스토리와 현재 코드 경로가 함께 맞을 때만 한다.
- 커밋 근거가 없거나 현재 작업트리 구현만 있는 항목은 운영 중으로 보지 않고 `검토/완료 전` 상태로 남긴다. 재검토 리포트의 `코드 확인` 판정도 커밋/QA 전에는 active 추적을 유지한다.
- 이미 코드에 있고 운영 중인 일회성 계획은 활성 목록에서 제거하고 [core/DEVELOPED_WORK.md](core/DEVELOPED_WORK.md)에만 요약한다.
- 운영 파라미터, PathB live gate, 주문금액, 최대 포지션, 쿨다운, confidence, slippage cap, protective-hold 거리, hard stop, broker truth 우선순위는 이 정리로 변경하지 않는다.

## 우선순위 규칙

1. **P0 수익성**: 수익 개선 판단을 가능하게 하는 측정, 후보 손실 경로, entry/exit shadow 데이터.
2. **P0 운영/버그**: live 진입/청산을 막거나 PnL truth를 오염시키는 토큰, broker truth, 주문 생명주기, destructive reconcile 문제.
3. **P1 데이터베이스/가드**: 향후 수익성 판단을 오염시키지 않도록 DB truth, canonical freshness, fallback exclusion, audit schema를 보강하는 일.
4. **P2 이후**: 비용 최적화, UI polish, 장기 shadow 연구.

## Do Now

| 순서 | 카테고리 | 우선순위 | 항목 | 현재 판단 | 개선 전 | 개선 후/완료 기준 | 남은 작업 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 수익성 | P0 | 최신 KR/US profit visibility 검증 | `selection_trace_id`, `actual_prompt_*`, prompt count 기본 계측은 코드에 있다. 최신 사이클 검증은 미완료. | old `input_to_claude` 또는 시간 근접 join으로 실제 Claude 입력 여부를 오판할 수 있음. | 최신 KR/US 사이클에서 `actual_prompt_v1` measured rows가 있고, included/missing 30/60m 성과가 실제 prompt 필드 기준으로 분리됨. | `tools/analyze_candidate_audit.py`와 대시보드/리포트가 actual prompt 필드를 우선 사용하는지 확인하고 최신 DB로 재집계. |
| 2 | 데이터베이스 | P0 | candidate bucket/source/score 품질 | bucket/source 일부 계측은 있으나 blank/광범위 block reason이 남아 있다. `INVALID_PRICE` 세분화는 작업트리 구현 흔적이 있으나 커밋 완료로 보지 않는다. | `primary_bucket=''`, source 품질 미상, `INVALID_PRICE` 단일 bucket 때문에 ranker/source/price/execution 원인 분리가 어려움. | raw top30 missing, trainer top30 missing, included/missing outcomes가 bucket/source/data-quality/raw-score/trainer-score로 query 가능하고 blank bucket이 10% 미만 또는 원인 문서화됨. | candidate audit, screener quality, outcome aggregation에 필드 전파 및 blank-rate/assertion 추가. |
| 3 | 수익성 | P0 | KR entry/exit profit shadow | counterfactual minute 수집 기반은 있으나 first-entry, OR/VWAP, MFE-cap 검토가 promotion-ready가 아니다. | 소수 샘플이나 상위 1일 기여로 entry/exit 정책을 바꿀 위험. | 30 filled trades 또는 4 calendar weeks, top-day contribution < 40%, broker-fill-aware replay로 first1/first2와 1.2-1.5% cap/MFE 보존 효과를 검토. | `entry_sequence_of_day`, OR/VWAP/volume, MFE/MAE, cap-hit, exit-overlay replay 필드와 report 작성. |
| 4 | 운영 | P0 | KIS token `EGW00133` rate-limit/backoff ops status | core classifier/cooldown은 working tree에 구현됐다. preflight/dashboard에서 rate-limit 상태가 아직 별도 operator status로 보이지 않는다. | 환경 문제와 rate-limit가 섞이면 운영자가 credential 장애 또는 broker truth 정상으로 오판할 수 있음. | `EGW00133` cooldown 상태가 preflight/dashboard/ops에 표시되고, valid cached token 사용/force-refresh 차단이 운영 QA로 확인된다. | token 만료/중복 process 환경 정리 후 preflight 재검증, rate-limit status 표시 보강. |
| 5 | 버그 | P0 | broker-truth zero-holding KR/US fixture | Plan A/PathB zero-holding reconcile 로직은 있으나 실제 KIS row fixture coverage가 부족하다. | stale local position을 지우는 destructive reconcile이 provider row shape 차이로 오작동할 수 있음. | KR/US position/open-order/today-fill fixture에서 safe zero holding만 제거되고 stale/error/open-remainder/fill-only는 fail closed. | `TradingBot._sell_zero_holding_broker_evidence()`와 `PathBRuntime._pathb_zero_holding_broker_evidence()` fixture 추가. |
| 6 | 운영 | P0 | PathB entry broker-truth gate visibility | live token/provider unavailable fail-closed는 working tree에 구현됐다. operator가 TTL/실패 원인을 로그 없이 보는 범위가 아직 부족하다. | broker truth block이 보이지 않아 config 변경으로 우회하려는 운영 판단이 생길 수 있음. | preflight/ops에서 enabled, TTL, attempt, success/failure, latency, last error, block reason, paper skip을 확인 가능. | preflight 또는 ops summary payload 추가 및 tests. |
| 7 | 버그 | P0 | PathB pending-buy TTL/order matching 보강 | 현재 작업트리에 `entry_order_sent_at/acked_at`, TTL defer reason, exact order mismatch 테스트 구현 흔적이 있다. 커밋/QA 전 완료로 보지 않는다. | plan 생성 시각 기준 TTL로 주문 직후 취소되거나, 같은 종목 다른 `order_no`를 fill/open order로 오인할 수 있음. | 실제 주문 전송/ACK 시각만 TTL 기준으로 쓰고, `entry_execution_id`가 있으면 exact `order_no`만 fill/cancel 판단에 사용. cancel 요청 후에도 broker truth로 fill/open/cancel 확정. | 작업트리 diff 리뷰, `tests/test_pathb_runtime.py` 관련 케이스 실행, 운영 파라미터 변경 없음 확인. |
| 8 | 버그 | P1 | US PathB sizing context/reason split | 작업트리에 `SafetyContext.can_buy_1_share`, `ORDER_SIZE_TOO_SMALL_GATE`, `HIGH_PRICE_BUDGET_BLOCK` 구현 흔적이 있다. 커밋/QA 전 완료로 보지 않는다. | PathB `qty=0`이 `INVALID_QTY` 또는 broad safety reason으로 뭉쳐 MRVL형 early gate 축소와 APP형 high-price budget 초과를 구분하기 어려움. | PathB 주문 수량 계산 결과는 그대로 두고, blocked payload가 original/effective budget, early gate, one-share 가능 여부, reason code를 분리 기록. | `tests/test_live_order_safety.py`, `tests/test_pathb_runtime.py` 확인 및 기존 qty 결과 불변 assertion 유지. |
| 9 | 데이터베이스 | P1 | V2 canonical truth freshness와 fallback exclusion | canonical truth가 preferred truth이나 stale/missing 경고와 profit-review fallback exclusion 검증이 부족하다. | stale canonical 또는 timeout fallback HOLD가 성과/학습 데이터에 섞일 수 있음. | preflight/dashboard/API에서 canonical freshness가 보이고, `advisor_unavailable`/`learning_excluded` fallback은 학습/canonical aggregate에서 제외됨. | guard tests, dashboard/API 표시, learning/canonical exclusion assertion 추가. |
| 10 | 운영 | P1 | Brain/sub-screener/operator-visible guard tests | 일부 runtime behavior는 있으나 자동 정책 메모리 쓰기, scoped trigger visibility, stale truth 경고가 불충분하다. | `state/brain.json` 또는 숨은 scoped trigger가 운영 판단을 오염시킬 수 있음. | automatic path가 `state/brain.json` 직접 쓰기를 하지 않고, sub-screener effective trigger가 global/scoped/default 우선순위와 counter로 보임. | Brain direct-write prevention, sub-screener trigger visibility, guardian freshness warning tests. |
| 11 | 버그 | P1 | runtime tuning override cleanup | bounds 단일화는 작업트리에 `runtime/tuning_bounds.py` intent/add 상태와 import 변경이 있다. 커밋 완료로 보지 않는다. | `claude_runtime_overrides`에 `action`, `mode`, `reason`, `warning` 같은 비조정 필드가 남아 prompt/debug noise를 만들 수 있음. | `coerce_runtime_adjustments()`가 `RUNTIME_ADJUSTMENT_BOUNDS` 키만 반환하고 bounds는 기존 값 그대로 유지. | untracked/intent-to-add 상태 정리, non-key drop 테스트 추가, py_compile. |
| 12 | 수익성 | P1 | raw-score shadow / multi-source consensus | shadow 근거는 있으나 live prompt/ranker 변경 근거는 부족하다. | raw top30 missing이나 US PLAN_B 후보가 수익성 후보인지 source 오류인지 분리되지 않음. | 10 trading days, 50 labeled outcomes, top-day contribution < 40%, added candidates가 excluded candidates를 앞서는지 확인. | KR raw top30 `volume_surge`/`momentum_now`, US KIS/Yahoo/FMP overlap outcome report. |
| 13 | 운영 | P1 | PathB fill truth / sell pending / EXPIRED monitoring | lifecycle reconcile은 있으나 실제 KIS full/partial/cancel payload 검증과 stale waiting-plan monitoring이 남아 있다. | `ORDER_UNKNOWN`, partial sell remainder, KR EXPIRED resampling이 local inference에 의존할 수 있음. | broker truth unavailable 때 fill 추론 금지, partial remainder TTL/reorder/close가 visible, stale active rows가 ops에 노출. | real payload fixture, sell-pending remainder, KR EXPIRED resample, stale waiting-plan cleanup 검토. |
| 14 | 운영 | P2 | hold advisor TTL/cache와 low-risk model tiering | `duration_ms`와 audit linkage baseline은 커밋 완료. cache/tiering은 deferred. | latency/cost 절감 시 sell protection 품질 저하 위험. | 1-2 sessions baseline 후 low-risk HOLD에 한정한 cache/model-tiering shadow가 비용/품질을 함께 측정. | duration/call trend 리뷰 후 설계. |
| 15 | 운영 | P2 | analyst outage UI polish | core unavailable/quorum/learning exclusion은 커밋 완료. UI polish만 남음. | provider outage가 neutral judgment처럼 보일 수 있음. | dashboard/API가 unavailable/partial/quorum 상태를 raw provider error 노출 없이 보여줌. | dashboard/API regression tests. |

## 해야 할 것 / 검토해야 할 것

| 구분 | 항목 |
| --- | --- |
| 해야 할 것 | P0 actual prompt 최신 사이클 검증, candidate bucket/source/score DB 전파, KIS token rate-limit ops status, zero-holding fixture, PathB broker-truth ops visibility. |
| 커밋/QA 전 검토 | PathB pending-buy TTL/order matching, US PathB sizing reason split, runtime tuning bounds migration, KR sector-play gate clarity. 현재 작업트리 구현 흔적은 있으나 운영 완료로 보지 않는다. |
| 샘플 축적 후 검토 | KR first-entry/exit overlay, raw-score shadow, US KIS ranking primary, Prompt overlay/PLAN_A, KR confirmation/WATCH_TRIGGER policy changes. |

## Observe Gates

| Gate | 현재 판단 | 개선 전 | 개선 후/승격 조건 | 규칙 |
| --- | --- | --- | --- | --- |
| Prompt overlay / PLAN_A | shadow 유지 | trigger days와 labeled outcomes가 부족하고 top-day concentration이 높음. | 10 trading days, 4 trigger days, 50 labeled outcomes, PF threshold, top-day contribution < 40%, added > excluded. | prompt/order 영향 금지. |
| US KIS ranking primary | shadow collector는 커밋됨 | KIS ranking을 primary로 바꾸면 fallback/source 편향을 아직 모름. | 10 shadow trading days, 30 evaluated rows, fallback/overlap review. | Yahoo/FMP fallback 유지, order/risk 변경 금지. |
| KR confirmation / WATCH_TRIGGER | demotion 변경 보류 | 60m label이 적어 kept/demoted 차이를 신뢰하기 어려움. | kept/demoted 각 30 labels, market-phase split, concentration gate. | sparse sample로 gate 조정 금지. |
| KR first-entry / exit overlay | replay/shadow 유지 | 수익성 가설은 있으나 샘플과 broker-fill-aware replay가 부족. | 30 filled trades 또는 4 weeks, single-winner dominance 없음. | live first-entry/stop/exit/PathB sizing 변경 금지. |

## 완료되어 Active에서 제거한 항목

커밋과 현재 코드 경로가 확인된 항목만 active backlog에서 제거했다. 상세는 [core/DEVELOPED_WORK.md](core/DEVELOPED_WORK.md)에 둔다.

| 항목 | 커밋 근거 | 현재 처리 |
| --- | --- | --- |
| KR `minute_complete` / `fade_recovered_shadow` | `6f8fdc1` | 완료. KR-only shadow rule만 observe gate에 남김. |
| analyst outage core safety | `469be29`, `59a8c26` | 완료. UI polish만 P2. |
| US projected dollar volume / KIS ranking shadow | `56ddbf4` | 완료. primary promotion은 observe gate. |
| hold advisor duration/audit linkage baseline | `5484e6a`, `980cc16` | 완료. TTL/cache는 P2. |
| live guardian / ensure-bot safety | `6c63668`, `b2a4adb`, `f218cc1` | 완료. 새 freshness warning 요구만 P1에 남김. |
| dashboard KIS period_profit | `5f83189` | 완료. active backlog에서 제거. |
| PathB gain_lock/protective hold base | `d8d7d5a` | 완료. fill truth/remainder 검증만 별도 P1. |

## 운영자 승인 필요

이 정리는 문서/백로그 정리다. `.env.live`, `config/v2_start_config.json`, PathB live enable, 주문금액, 최대 포지션 수, daily entry cap, confidence, reentry cooldown, slippage cap, protective hold distance, hard stop 동작, `state/brain.json`은 변경하지 않는다.
