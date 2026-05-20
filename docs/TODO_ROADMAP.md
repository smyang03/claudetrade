# TODO Roadmap - 2026-05-20

이 문서가 현재 active plan의 단일 원장이다. 2026-05-20 재리뷰에서 완료된 계획은 `docs/DEVELOPED_WORK.md`로 옮기고, 남은 실행 항목만 우선순위로 다시 정렬했다. 상세 과거 계획은 Git history와 `docs/reports/` QA 리포트에서만 추적한다.

## 분류 기준

- 완료 삭제: 코드와 테스트/QA 리포트 또는 운영 health check로 개선이 확인되어 active plan에서 제거한다.
- 통합 삭제: 아직 해야 하지만 별도 plan 문서로 두면 우선순위가 흩어지는 항목은 이 문서에만 남긴다.
- 운영 검증: 구현은 끝났지만 실제 세션, 브로커 payload, live dashboard 데이터로 확인해야 닫을 수 있는 항목이다.
- 장기 보류: P0/P1 데이터 truth와 안전장치가 안정화되기 전에는 켜지 않는 항목이다.

## 2026-05-20 재분류 리뷰

| 구분 | 리뷰 |
| --- | --- |
| 완료된 개선 | KR cap40 confirmation enforce, candidate pipeline/screener quality/audit linkage, prompt overlay Phase 0/1, final prompt evidence alignment, dashboard PnL source label, PathB `PULLBACK_WAIT` origin audit, counterfactual store/writer base는 구현과 테스트 근거가 있어 active plan에서 제거했다. |
| 부분 완료 | `RiskManager`는 시장별 shadow mirror와 `_risk(market)` adapter가 생겼지만 live write path는 아직 global `self.risk` 중심이다. `SafetyContext`는 realized/equity basis가 생겼지만 equity source/lag audit은 더 필요하다. counterfactual path는 row 저장은 되지만 outcome label이 비어 있다. |
| 운영 검증 필요 | live 시작 전 guardian/preflight, 기존 open order 0 확인, KIS WS/REST fill truth payload 검증, final prompt evidence alignment의 next-session metric, prompt overlay shadow gate는 실제 세션 데이터가 필요하다. |
| 보류/흡수 | L3 price inject, Market Regime/RR check, KRX/BigKinds/theme injection, CandidateTierBook, dual runtime, Brain Train, 신규 intraday/VWAP/momentum gate는 이 문서의 P2/P3로 흡수했다. |

## 한 것 — 우선순위별 완료/정리

| 우선 | 완료 항목 | 개선 효과 | 완료 판단 근거 | 남은 연결 |
| --- | --- | --- | --- | --- |
| P0 | KR cap40 confirmation enforce | KR live 진입 수량이 cap 40 정책을 우회하지 못하게 하여 과도한 단일 주문/노출을 줄인다. | `docs/reports/kr_cap40_confirmation_enforce_implementation_report_20260516.md`; preflight/guardian/test 통과 기록 | live 시작 전마다 guardian/preflight 재확인 |
| P0 | Final prompt evidence alignment | 최종 prompt에 보이는 후보와 실제 실행 후보의 불일치를 줄여 Claude 판단 근거와 주문 후보를 같은 집합으로 맞춘다. | `prepare_selection_prompt_pool()`, `prompt_pool_override`, `FINAL_PROMPT_EVIDENCE_ALIGNMENT_ENABLED=true`, 관련 테스트 존재 | 다음 live 세션에서 overlap/exec-missing/READY 회복 확인 |
| P0 | Candidate pipeline 품질 보강 | degraded cache, 감사 단절, outcome 누락을 줄여 selection 품질 문제와 execution/risk 문제를 더 분리해서 볼 수 있다. | `docs/plans/candidate_pipeline_improvement_implementation_plan_20260515.md` 내용을 구현 완료로 정리: US degraded cache 차단, audit linkage, outcome catch-up, KR alpha report 등 | 운영 모니터링만 유지 |
| P0 | Prompt overlay Phase 0/1 | overlay를 즉시 live 변경이 아닌 shadow/gate 흐름으로 넣어 prompt 개선 후보를 성과 지표로 검증할 수 있다. | `docs/reports/prompt_overlay_impl_qa_20260520.md`; helper, off/shadow/live, audit payload, gate analyzer 구현 | shadow 10거래일/4발동일 gate 전까지 live 전환 금지 |
| P0 | Dashboard PnL source 표시 | dashboard 사용자가 daily PnL이 broker truth인지 local 계산인지 구분할 수 있어 운영 판단 오류를 줄인다. | dashboard source label/helper와 `tests/test_dashboard_pathb.py` 근거 | broker/local mismatch 전면 정비는 P1/P2 |
| P0 | PathB `PULLBACK_WAIT` origin audit | wait-only plan이 PathA trade_ready로 오해되는 문제를 줄이고, PathB 대기/진입 전이를 audit에서 추적 가능하게 한다. | `_pathb_wait_tickers`, `_pathb_wait_origins`, plan/order/position `pathb_origin_action` 테스트 근거 | stale wait/zone hit 성과 검증은 P2 |
| P1 | Counterfactual path 저장소/분석 base | 차단/비진입 후보도 이후 가격 경로를 붙일 수 있는 저장 기반이 생겨 정책 변경의 기회비용을 측정할 수 있다. | `candidate_counterfactual_paths` store, non-blocking writer, analyzer, bulk error 수집 구현 | outcome backfill은 아직 P0 |
| P1 | `RiskManager` 시장별 shadow 시작 | KR/US 리스크 상태를 병렬 비교할 수 있어 live write path 전환 전에 cash/position/halt 분리 리스크를 관찰할 수 있다. | `ENABLE_MARKET_RISK_SHADOW=true`, `_risk(market)` adapter, live status `risk_shadow` 노출 | live adapter 전환은 아직 P1 |
| P2 | L3 priority backfill 판단 | 중복 backfill 작업을 줄이고, 실제 반복 누락이 있는 핀/수동 후보에만 데이터 보강 비용을 쓰게 한다. | 일반 screener 후보는 priority JSON으로 커버됨을 확인 | 핀/수동 후보 반복 누락 증거가 나올 때만 구현 |

## 해야 할 것 — 우선순위

### P0 — 운영 truth와 학습 truth 복구

| 우선 | 작업 | 사유 | 개선 효과 | 완료 조건 |
| --- | --- | --- | --- | --- |
| P0-1 | live 시작 전 truth gate 고정 | guardian/preflight, ORDER_UNKNOWN, 기존 open order는 코드보다 브로커 truth가 우선이다. | live 시작 시 남은 주문, broker 불신, quarantine 상태를 조기에 막아 신규 진입보다 포지션 보호를 우선하게 한다. | `tools/live_preflight.py --mode live --skip-dashboard --json` 및 `tools/live_guardian.py --mode live --skip-dashboard --json` 저장, hard fail 0, 미해결 ORDER_UNKNOWN/open buy order 0 확인 |
| P0-2 | KIS fill truth 실수신 검증 | 실제 full/partial/cancel payload가 틀리면 중복 주문, 포지션, PnL이 모두 오염된다. | 체결/취소/부분체결 truth가 맞아져 포지션 수량, 재주문, realized PnL 계산의 신뢰도를 높인다. | 모의 후 소액 live에서 WS/REST fill key, partial fill, cancel, 재기동 fill cache 복원 검증 |
| P0-3 | V2 lifecycle canonical performance table | 현재 lifecycle raw 이벤트는 중복이 있고 decisions.db filled는 실제 V2 체결을 따라가지 못한다. | 거래 단위 성과 원장이 생겨 후보 품질, 실행 품질, 시장별 성과를 같은 기준으로 비교할 수 있다. | decision/ticker dedupe 기준의 performance table 생성, earliest fill/first close/last close/quality link/KR-US 분리 저장 |
| P0-4 | decisions fill 연결 복구 | `decisions.db` KR filled 0, US filled 3 상태라 학습 DB가 운영 성과를 반영하지 못한다. | 학습/리포트 DB가 실제 체결 결과를 반영해 잘못된 미체결 학습과 성과 누락을 줄인다. | canonical performance table을 기준으로 decisions/link audit table 생성, unmatched row 별도 저장 |
| P0-5 | counterfactual outcome backfill | `candidate_counterfactual_paths` row는 쌓이지만 30m/60m/close outcome이 비어 있어 정책 판단에 쓸 수 없다. | 차단된 후보의 이후 수익/위험을 비교할 수 있어 gate 완화/강화 판단을 실제 기회비용 기준으로 할 수 있다. | trigger 기준 30m/60m return, close return, MFE/MAE, price source/sample count, DATA_MISSING reason 채움 |
| P0-6 | metric contract 고정 | live/paper, raw/dedupe, latest/state split, raw/pure watch가 섞이면 정책 결론이 뒤집힌다. | 분석 리포트마다 같은 분모와 bucket을 쓰게 되어 정책 판단이 표본 혼합 때문에 뒤집히는 일을 줄인다. | 모든 분석 리포트가 네 축을 명시하고 pure watch/demoted/filled bucket을 분리 |
| P0-7 | final prompt evidence alignment 운영 검증 | 구현은 끝났지만 provider 응답과 prompt overlap은 다음 실제 세션에서 확인해야 한다. | 실제 세션에서 prompt 후보와 실행 후보가 맞는지 확인해 selection 품질 평가의 전제 조건을 고정한다. | `evidence_prompt_overlap_ratio >= 0.8`, `prompt_exec_missing_pct` 감소, READY 0 반복 여부 확인 |
| P0-8 | prompt overlay shadow gate 관찰 | 현재 `PROMPT_OVERLAY_MODE=shadow` 상태에서는 실제 prompt를 바꾸지 않으므로, live 전환 전 데이터 gate가 필수다. | prompt 변경이 성과를 개선하는지 먼저 shadow 지표로 확인해 과최적화나 단일 일자 착시를 줄인다. | 10거래일 이상, overlay 발동 4일 이상, PF > 1.0, top-day 기여율 < 40%, `overlay_plan_b_used=false` |

### P1 — 안전장치와 실행 품질 강화

| 우선 | 작업 | 사유 | 개선 효과 | 완료 조건 |
| --- | --- | --- | --- | --- |
| P1-1 | KR modern action schema/route 점검 | KR modern actions가 최근 30m/60m 성과에서 크게 부진했고 `PULLBACK_WAIT`/`ADD_READY`가 ready bucket에 섞일 수 있다. | action별 의도와 route를 분리해 BUY 가능한 신호와 wait/watch 신호를 혼동하지 않게 한다. | `BUY_READY`, `PROBE_READY`, `PULLBACK_WAIT`, `ADD_READY`를 route/fill 기준으로 분리해 case table 작성 후 gate 수정 여부 결정 |
| P1-2 | KR entry timing gate 재검토 | dedupe 기준 첫 30분과 14:00 이후 손실이 반복된다. | 손실이 반복되는 시간대를 진입 정책에 반영해 KR 장중 과열/마감 전 품질 저하를 줄인다. | canonical table 기준 09:00-09:30, 09:30-10:30, 10:30-12:00, 12:00-14:00, 14:00+ 정책 확정 |
| P1-3 | `RiskManager` KR/US adapter live 이행 | shadow mirror는 있으나 write path가 global `self.risk` 중심이라 cash/positions/halt 경계가 섞일 수 있다. | KR/US cash, position, halt 상태가 분리되어 한 시장의 리스크 상태가 다른 시장 주문을 오염시키는 위험을 낮춘다. | `_risk(market)` 호출부 확대, `ENABLE_MARKET_RISK_ADAPTER_LIVE` shadow→limited 전환 테스트, 시장별 cash/position/halt 회귀 추가 |
| P1-4 | Safety/equity audit field 확장 | realized/equity basis는 생겼지만 source/lag/unrealized 분해가 부족하다. | safety 차단/허용의 근거가 broker/local/equity lag 중 어디서 왔는지 추적되어 오탐과 누락을 줄인다. | safety decision/details에 `equity_source`, `unrealized_return_pct`, `broker_lag_suspected` 등 저장 |
| P1-5 | slot-disabled momentum shadow label | CRDO 같은 US RISK_OFF momentum은 live 금지지만 counterfactual label은 필요하다. | live 금지 슬롯도 기회비용을 측정할 수 있어 momentum 재활성화 여부를 감이 아닌 표본으로 판단한다. | `slot_disabled:momentum` 후보에 strategy/slot reason을 counterfactual metadata로 저장, 10 labeled sessions 전 live 승격 금지 |
| P1-6 | KIS microstructure/MFE 보강 | KR orderbook/VI wrapper는 연결됐지만 API 실패와 MFE restart 정확도는 추가 검증이 필요하다. | KR 호가/VI와 MFE 계산 신뢰도가 높아져 진입 품질과 사후 peak/손실 분석이 덜 흔들린다. | VI/orderbook source/sample audit, minute cache 기반 peak/MFE 재계산, degraded 시 주문 경로 영향 없음 확인 |
| P1-7 | Claude API 비용 절감 1차 | 비용 절감은 필요하지만 P0 데이터 truth 전에는 품질 저하를 구분하기 어렵다. | 반복 호출을 줄이면서도 escalation과 shadow 지표를 남겨 비용 절감이 매매 품질을 훼손하는지 분리 관찰한다. | AUTO_SELL_REVIEW 1인화+escalation, hold cache, preopen duplicate cache를 shadow 지표와 함께 적용 |

### P2 — Shadow 실험과 후보 품질 고도화

| 우선 | 작업 | 보류 이유/조건 | 개선 효과 | 완료 조건 |
| --- | --- | --- | --- | --- |
| P2-1 | US preopen/extended-hours 10 decision 이상 관찰 | 현재 preopen 손실은 n=3/day 집중이라 영구 hard block 근거로 부족하다. | preopen 손실이 구조 문제인지 표본 착시인지 구분해 과도한 영구 차단을 피한다. | preopen 신규 진입 임시 보수 가드 유지, 10 decision 이상 후 재평가 |
| P2-2 | Hybrid-lite/WATCH_TRIGGER shadow 고도화 | soft watch simulation은 신호가 있지만 샘플과 label 품질이 부족하다. | missed runup과 watch-only 기회를 더 정확히 라벨링해 후보 승격/차단 정책을 개선한다. | miss-quality, one-block relaxed, watch-only missed runup을 pure bucket 기준으로 평가 |
| P2-3 | PathB EXPIRED 재샘플링과 sell pending remainder | KR EXPIRED 표본은 MFE가 높지만 quote sample이 부족하고, sell partial TTL은 dedicated reconciliation이 약하다. | 진입 zone 재접근과 부분매도 잔량을 더 정확히 처리해 놓친 진입과 미정리 잔량 리스크를 줄인다. | EXPIRED 전 quote refresh/zone reentry shadow, sell partial remainder 재주문 테스트 |
| P2-4 | CandidateTierBook shadow | flat list는 CORE/WATCH/BENCH/QUARANTINE 상태를 표현하지 못한다. | 후보 상태를 등급별로 분리해 같은 종목을 매수 후보, 관찰 후보, 격리 후보로 혼동하지 않게 한다. | shadow tier book과 기존 list diff 검증 후 source-of-truth 전환 판단 |
| P2-5 | KRX/BigKinds/theme injection | credential과 공식 데이터 pipeline이 없으면 KR 후보 품질 개선을 검증할 수 없다. | KR 공식/뉴스/테마 데이터를 후보 metadata로 붙여 국내 종목 selection의 정보 부족을 줄인다. | key 확보, dry-run, normalized SQLite, theme candidate injection shadow |
| P2-6 | Market Regime / RR check shadow | 변동성 regime과 손익비 차단은 기존 ATR cap/stop 경로와 충돌 가능성이 있다. | 시장 변동성과 손익비가 나쁜 setup을 observe-only로 걸러 기존 stop/risk 정책과의 충돌을 검증한다. | 5거래일 shadow, RR threshold observe-only, 충돌 테스트 후 하나씩 live 판단 |
| P2-7 | Prompt overlay cap 확대/Shadow Claude call/Fresh slot | overlay live 전환 전에는 cap 확대나 별도 Claude call이 과최적화가 될 수 있다. | prompt 실험 범위를 단계적으로 키워 성과 개선이 유지되는지 확인하면서 호출 비용과 과최적화 리스크를 관리한다. | later-data gate 통과 후 4→6→8 단계 확대, 필요 시 audit-only dry-run Claude call |

### P3 — 구조 개편/장기 보류

| 우선 | 작업 | 개선 효과 | 조건 |
| --- | --- | --- | --- |
| P3-1 | L3 price collection inject | 반복적으로 가격 이력이 비는 수동/핀 후보의 분석 공백을 줄인다. | 핀/수동 후보가 2세션 이상 `HISTORY_UNAVAILABLE`로 반복될 때만 구현 |
| P3-2 | Dual runtime SharedEngine/AccountRuntime | KR/US runtime 분리를 구조적으로 강화해 장기적으로 시장별 운영/장애 격리를 단순하게 만든다. | `RiskManager` KR/US live adapter와 performance truth가 안정된 뒤 착수 |
| P3-3 | Brain Train 모드 | 정책 메모리를 자동 변경하지 않고도 후보 교훈을 별도 weight/flag로 실험할 수 있게 한다. | 운영 전략 품질 안정 후에도 샘플 부족이 명확할 때만 별도 flag/weight로 설계 |
| P3-4 | 신규 intraday/VWAP/momentum opening gate | 신규 장중 전략을 기존 운영 truth가 안정된 뒤 작은 shadow 실험으로 검증할 수 있다. | P0/P1 안정화와 shadow 성과 확인 후 작은 실험으로만 시작 |

## 바로 실행 순서

1. live 시작 전 P0-1을 수행하고 JSON 결과를 저장한다.
2. 시장 시간에 맞춰 P0-2 KIS fill truth 실수신을 모의→소액 live 순서로 검증한다.
3. 개발 작업은 P0-3 canonical performance table부터 시작하고, 이어서 P0-4 decisions link, P0-5 counterfactual outcome backfill을 연결한다.
4. 다음 live 세션 후 P0-7 evidence alignment와 P0-8 overlay shadow gate 지표를 한 번에 점검한다.
5. P0 데이터 truth가 안정되면 P1-1/P1-2 KR action/time gate를 실제 성과 기준으로 조정한다.

## 흡수/삭제한 원본 매핑

| 원본 문서 | 처리 |
| --- | --- |
| `audit/priority_hotfix_improvement_plan_20260501.md` | 완료 요약을 `DEVELOPED_WORK.md`에 남기고 삭제 |
| `docs/plans/candidate_pipeline_improvement_implementation_plan_20260515.md` | 완료 요약을 `DEVELOPED_WORK.md`에 남기고 삭제 |
| `docs/plans/data_collection_l3_priority_backfill_plan_20260516.md` | P3-1로 조건만 흡수하고 삭제 |
| `docs/plans/logic_regime_strengthening_plan_20260516.md` | P2-6으로 흡수하고 삭제 |
| `pathb_v2_live_plan.md` | 완료 phase 상세를 제거하고 운영 reference + TODO 연결 문서로 축소 |
| `docs/reports/prompt_overlay_later_data_plan_20260520.md` | P0-8, P2-7로 흡수 |
| `docs/reports/evidence_alignment_why_what_how_20260520.md` | 구현 완료, P0-7 운영 검증으로 흡수 |
| `docs/reports/codex_kr_us_db_reanalysis_20260519.md` | P0-3~P0-6, P1-1~P1-2로 흡수 |
| `docs/reports/momentum_reenable_full_db_analysis_20260520.md` | P1-5로 흡수 |
