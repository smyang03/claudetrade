# 신호 포착 지연 점검 및 개선안 도출

작성일: 2026-06-07

## 1. 문제 정의

현재 시스템의 주요 약점 중 하나는 진입 신호 포착이 늦어 좋은 타이밍을 놓칠 수 있다는 점이다. 이번 작업은 감각적인 "늦다"를 코드 변경으로 바로 연결하지 않고, timestamp와 데이터 흐름 기준으로 어느 구간에서 지연이 발생하는지 먼저 분해한다.

### 늦다고 판단하는 기준

- 후보 발견이 늦다.
- watchlist 편입이 늦다.
- trade_ready 전환이 늦다.
- Claude selection 반영이 늦다.
- 전략 신호 발생 후 주문 가능 시점까지 지연된다.
- PathB buy zone hit 감지가 늦다.
- 이미 데이터는 있었지만 audit/log/dashboard에 지연 원인이 드러나지 않는다.

| 구분 | 현재 의심 | 확인할 기준 |
|---|---|---|
| 후보 생성 | 급등, 뉴스, 갭 이후 늦게 들어옴 | market event 시각 대비 candidate 생성 시각 |
| watchlist 편입 | candidate는 있으나 관찰 대상 반영이 늦음 | candidate 생성 시각 대비 watchlist 편입 시각 |
| trade_ready 전환 | watchlist 이후 진입 가능 후보 전환 지연 | watchlist -> trade_ready 소요 시간 |
| 전략 신호 | 후보는 있으나 전략 trigger가 늦음 | trade_ready -> strategy signal 소요 시간 |
| 주문 연결 | 신호 후 route/risk/order까지 지연 | signal -> risk pass -> order 시각 |
| PathB | buy zone hit 감지 지연 | price zone 도달 시각 대비 entry scan 시각 |

## 2. 분석 대상 범위

이번 작업은 "진입 신호 포착 지연"만 다룬다.

### 포함 범위

- candidate 생성 흐름
- watchlist/trade_ready 전환 흐름
- 전략 신호 발생 타이밍
- PathA selection timing
- PathB entry scan timing
- 로그/audit/DB에 남는 timestamp 품질
- 장 초반/장중/장마감 시간대별 지연 차이
- KR/US 시장별 지연 차이
- Claude 호출 대기, 실패, cooldown이 진입 지연에 미치는 영향

### 제외 범위

- 최종 주문 수량 계산
- hard stop, loss cap, profit ladder 변경
- broker truth fail-closed 완화
- PathB sizing 정책 변경
- PathB live enable/disable 및 운영 파라미터 무단 변경
- `state/brain.json` 자동 수정 또는 장기 정책 메모리 승격
- selection 품질 개선과 execution/risk 정책 변경을 한 패치에서 섞는 작업

## 3. 코드 수정 전 작업 선언

실제 개선 구현에 들어가기 전에는 다음 항목을 먼저 명시한다.

- 이번 이슈의 직접 수정 범위
- 건드리지 않을 보호 영역
- 수정 예정 파일
- 실행할 검증 명령
- config/env 영향 여부
- 주문/리스크/broker truth/Claude 호출량 영향 여부

## 4. 보호 영역

아래 영역은 직접 원인으로 확인되기 전까지 변경하지 않는다.

- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard
- PathB broker-truth entry fail-closed
- PathB sizing reason split과 `_pathb_qty_with_context()`
- PathB profit ladder, pre-close 청산, hold advisor protective hold
- PathB buy zone hit evidence gate와 live routing
- `runtime/action_routing.py::RouteDecision`
- broker truth 우선순위, quarantine, stale reconcile
- KIS order normalization의 `remaining_qty` 보존
- `state/brain.json` 자동 정책 메모리 승격 경로

보호 영역을 피할 수 없이 수정해야 하면 `AGENTS.md`/`CLAUDE.md`의 `MD 위반 사항` 형식에 맞춰 사유, 변경 전후 동작, 안전장치, 테스트, 남은 위험을 남긴다.

## 5. 확인할 데이터

우선 아래 데이터를 기준으로 지연 구간을 분리한다.

- `data/audit/candidate_audit.db`
- `data/ml/decisions.db`
- `data/ticker_selection_log.db`
- PathB event store 관련 DB/로그
- `logs/system/`
- `logs/risk/`
- `logs/normal/`
- `logs/daily_judgment/`
- `logs/screener/`
- dashboard에 노출되는 candidate, PathB, broker integrity 관련 데이터

## 6. 분석 질문

다음 질문에 답한다.

1. 신호가 늦은 원인은 후보 생성 전인가, 후보 생성 후인가?
2. KR/US 중 어느 시장에서 더 심한가?
3. PathA와 PathB 중 어느 경로에서 더 심한가?
4. 장 초반, 장중, 장마감 중 어느 구간에서 지연이 큰가?
5. Claude 호출 대기, 실패, cooldown 때문에 늦는가?
6. 전략 신호 자체가 늦는가, 아니면 routing/risk/order 단계가 늦는가?
7. broker truth/risk gate에 의한 정상 차단을 신호 지연으로 오해하고 있지는 않은가?
8. 이미 데이터는 있었지만 로그/audit/dashboard에 드러나지 않는 관측성 문제인가?
9. US PathB claude_price 수익 경로를 훼손하지 않고 개선할 수 있는가?

## 7. 산출물 A: 지연 구간 분해

| 구간 | 지연 여부 | 근거 | 영향 |
|---|---|---|---|
| market event -> candidate |  |  |  |
| candidate -> watchlist |  |  |  |
| watchlist -> trade_ready |  |  |  |
| trade_ready -> strategy signal |  |  |  |
| signal -> route decision |  |  |  |
| route decision -> order |  |  |  |
| PathB buy zone hit -> entry scan |  |  |  |
| broker truth/risk gate -> final decision |  |  |  |

## 8. 산출물 B: 원인 분류

분석 결과는 아래 중 하나 이상으로 분류한다.

- 데이터 수집 지연
- screener 주기 문제
- candidate scoring 문제
- watchlist 유지/전환 기준 문제
- trade_ready 기준 과도함
- Claude selection latency
- Claude 호출량 보호 장치로 인한 의도된 지연
- 전략 trigger 자체의 후행성
- PathB scan interval 문제
- PathB buy zone evidence 부족
- broker truth/risk gate에 의한 정상 차단
- 로그/audit/dashboard 관측성 부족
- 운영 config/env 불일치

## 9. 산출물 C: 개선안 형식

각 개선안은 아래 형식으로 제시한다.

| 개선안 | 기대 효과 | 위험 | 적용 모드 | 검증 방법 |
|---|---|---|---|---|
| 장 초반 screener 주기 단축 | 초기 급등 포착 개선 | API 호출 증가 | live/enforce 기본 | 과거 로그 replay + dry-run |
| candidate timestamp audit 보강 | 지연 구간 식별 개선 | 저장 필드 증가 | live/enforce | DB schema/consumer 테스트 |
| PathB zone hit 관측 로그 보강 | buy zone hit 누락 원인 파악 | 로그 증가 | live/enforce | PathB entry scan 테스트 |

## 10. 우선순위 기준

개선안은 아래 기준으로 정렬한다.

1. 주문/리스크 보호 영역을 건드리지 않는 개선
2. 로그/audit/dashboard 가시성 개선
3. 후보 생성/scan 주기 개선
4. trade_ready 전환 기준 개선
5. Claude 호출량 증가가 적은 개선
6. KR/US 성과 차이를 분리해 검증 가능한 개선
7. US PathB claude_price 수익 경로를 깨지 않는 개선

## 11. 검증 계획

변경이 생기면 최소 아래를 실행한다.

- 관련 단위 테스트
- 관련 통합 테스트
- `python -m py_compile trading_bot.py runtime/pathb_runtime.py`
- 필요한 경우 `python tools/live_preflight.py --mode live --skip-dashboard --json`
- 필요한 경우 PathA/PathB dry-run 또는 replay성 검증
- 로그/대시보드 timestamp 확인
- config/env 변경 여부 확인
- 주문/리스크/broker truth/Claude 호출량 영향 확인

## 12. 최종 보고 형식

최종 보고는 아래로 구분한다.

- 반영 완료
- 비차단 잔여 리스크
- 범위 밖 후속 개선
- 실행한 검증
- 미검증 축
- config/env 영향
- 주문/리스크/broker truth/Claude 호출량 영향

테스트 통과는 "검증한 범위에서 통과"로만 보고하며, 남은 미검증 축과 비차단 리스크는 별도로 공개한다.

## 13. 2026-06-07 1차 검토 결과

### 검토 범위

이번 1차 검토는 코드 수정 없이 현재 worktree와 운영 산출물을 읽어 진행했다.

- 코드 흐름: `trading_bot.py`, `runtime/pathb_runtime.py`, `bot/entry_timing.py`, `bot/screener_quality.py`, `runtime/sub_screener.py`, `runtime/candidate_discovery_overlay.py`
- DB: `data/ticker_selection_log.db`, `data/audit/candidate_audit.db`, `data/ml/decisions.db`, `data/v2_event_store.db`
- 로그: `logs/entry_timing/`, `logs/funnel/candidate_cycle_latency_*`, `logs/screener_quality/`, `logs/system/live_trading_20260605.log`
- 도구: `tools/analyze_candidate_audit.py`, `tools/sub_screener_uplift_report.py`

### 현재 이미 있는 계측/보호 장치

| 영역 | 현재 상태 | 의미 |
|---|---|---|
| PathA entry timing | `EntryTimingTracker`가 candidate, signal check, signal fired, order, fill 이벤트를 JSONL로 기록 | 후보 이후 지연은 관측 가능하나 audit DB 최신 row와의 연결이 일부 약함 |
| candidate quality | `screener_quality`가 raw/prompt/selected 상태와 `NOT_IN_PROMPT`, `SCREENER_ONLY`, `TRADE_READY`를 기록 | prompt에 못 들어간 후보를 식별 가능 |
| candidate audit | `audit_candidate_rows`, `audit_candidate_outcomes`, `candidate_counterfactual_paths`가 존재 | 후보/route/outcome 분석 기반은 있음 |
| cycle latency | `candidate_cycle_latency` funnel 로그가 존재 | run_cycle 처리 시간 outlier 관측 가능 |
| sub-screener | 장중 신규 후보 감지, rate limit, dedupe, triage 경로 존재 | 장중 후보 보강 장치가 이미 있음 |
| discovery overlay | prompt cap으로 빠진 후보를 `DISCOVERY` role + WATCH ceiling으로 추가하는 구조 존재 | 주문 권한 없이 prompt 관측 커버리지 확대 가능 |
| PathB hit audit | `CLAUDE_PRICE_HIT`, `ORDER_SENT`, `FILLED` lifecycle event 존재 | zone hit 이후 제출 지연은 분리 가능 |

### 정량 근거

최근 20개 selection session 기준 `ticker_selection_log`:

| 시장 | rows | trade_ready | signal_fired | traded | selected -> signal p50/p90/max | selected -> traded p50/p90/max |
|---|---:|---:|---:|---:|---|---|
| KR | 3,014 | 28 | 8 | 8 | 5.389m / 78.828m / 130.523m | 5.410m / 78.849m / 130.543m |
| US | 3,222 | 260 | 17 | 4 | 1.414m / 18.439m / 31.838m | 0.756m / 1.391m / 1.414m |

`entry_timing` 샘플:

- 2026-06-03 US IREN: candidate 22:35:59 -> signal 22:56:59 = 21.0분.
- 같은 사례에서 signal -> order = 0.1167분, candidate -> order = 21.1167분.
- 즉 주문 제출 자체보다 후보/선택 이후 신호 발생까지의 시간이 더 큰 병목으로 보인다.

`candidate_cycle_latency` 2026-06-05:

| 시장 | rows | avg | p90 | max | alert |
|---|---:|---:|---:|---:|---:|
| KR | 89 | 5,053.731ms | 5,419.322ms | 117,105.291ms | 1 |
| US | 84 | 5,527.880ms | 6,531.485ms | 46,874.747ms | 1 |

해석:

- 평시 run_cycle 처리 시간은 대체로 수 초 단위다.
- KR 117초, US 46초 outlier가 있어 일회성 지연은 존재한다.
- 그러나 p90 기준으로는 cycle 자체가 주 병목이라고 단정하기 어렵다.

`screener_quality` 최근 파일:

| 일자/시장 | rows | prompt ratio | NOT_IN_PROMPT | 고거래대금 NOT_IN_PROMPT |
|---|---:|---:|---:|---:|
| 2026-06-05 KR | 705 | 0.498 | 354 | 293 |
| 2026-06-05 US | 519 | 0.486 | 267 | 32 |
| 2026-06-06 US | 454 | 0.463 | 244 | 16 |

해석:

- 후보의 절반 이상이 Claude prompt 바깥에 머무는 시간이 있다.
- KR은 특히 고거래대금 후보가 prompt에 못 들어간 비율이 높다.
- 이 구간은 "후보 생성은 됐지만 판단/감시 레이어 진입이 늦는" 문제로 분류한다.

`analyze_candidate_audit.py --date 2026-06-05` 최신 row 기준:

| 시장 | latest rows | not_in_prompt | in_prompt_not_selected | claude_watch_conservative | pathb_zone_or_plan | latency SLA |
|---|---:|---:|---:|---:|---:|---|
| KR | 122 | 57 | 36 | 16 | 0 | critical, max 117.105s |
| US | 130 | 62 | 33 | 25 | 1 | warn, max 46.875s |

call-level US 2026-06-05:

- rows 1,014
- not_in_prompt 490
- in_prompt_not_selected 287
- claude_watch_conservative 159
- pathb_zone_or_plan 10
- watch trigger not evaluated 885, 주요 사유 `shadow_cycle_cap_exceeded`

PathB 2026-06-01 이후 v2 event store:

- `CLAUDE_PRICE_HIT -> ORDER_SENT` p90은 약 0.017분이다.
- hit 이후 주문 제출은 핵심 지연 병목으로 보이지 않는다.
- `created -> hit`은 zone 대기 성격이므로 지연으로 단정하면 안 된다.
- 개선 초점은 hit 이후 제출이 아니라 waiting plan의 가격 감시 커버리지와 hit 전 관측성이다.

### 1차 결론

현재 증거상 "신호 포착이 늦다"는 단일 원인이 아니다. 아래 세 구간으로 나눠야 한다.

1. **후보가 prompt/감시 레이어에 늦게 들어가는 문제**
   - `NOT_IN_PROMPT`가 많고, 특히 KR 고거래대금 후보가 많이 밀린다.
   - sub-screener와 discovery overlay가 이미 존재하므로 이 경로를 보강하는 것이 우선이다.

2. **watch/trade_ready 이후 strategy signal까지 늦는 문제**
   - KR selected -> signal p90이 78.828분으로 크다.
   - 이 값은 ORP window timing과 직접 연결해서 해석해야 한다. KR ORP 기본값은 `or_minutes=10`, `entry_window_min=60`이고, `elapsed_min > or_minutes + entry_window_min`이면 `orp_entry_window_expired`가 된다. 즉 기본 만료선은 장 시작 후 70분이다.
   - 워크리스트와 현황 문서에는 KR `orp_entry_window_expired=395`가 이미 기록되어 있다. 따라서 KR p90 78.828분은 ORP 후보가 window 만료 직전 또는 만료 이후까지 대기한 케이스가 섞였을 가능성이 높다.
   - 결론은 새 전략 설계가 아니라 기존 Selection Strategy Feasibility Contract 적용 구간을 명확히 하는 것이다. `trading_bot.py::_strategy_feasibility_orp()`와 `_apply_strategy_feasibility_runtime_filter()`가 이 구간을 직접 타겟팅해야 하며, ORP window/threshold 변경은 read-only timing join 리포트 이후에만 검토한다.
   - US도 일부 사례에서 candidate -> signal 21분이 확인된다.
   - signal -> order는 빠르므로 주문 제출 정책보다 신호 평가 주기/대상/전략 gate를 먼저 봐야 한다.

3. **관측성 부족으로 지연 원인을 뒤늦게 알게 되는 문제**
   - `entry_timing_snapshot_missing`이 candidate audit readiness blocker로 남는다.
   - `EntryTimingTracker`는 `first_signal_checked_at`을 기록하지만 `candidate_to_first_signal_check_delay_min` 파생값이 없다.
   - 최신 row 집계와 call-level 집계에서 timing coverage가 다르게 보인다.

## 14. 개선 방향

### P0. ORP window timing과 Feasibility Contract 연결

KR selected -> signal p90 78.828분은 단순한 scan 지연이 아니라 ORP entry window 만료선과 맞물린 전략 timing 문제로 본다. 이 항목은 이미 설계된 Selection Strategy Feasibility Contract를 중복 설계 없이 검증/강화하는 작업이다.

| 개선안 | 기대 효과 | 위험 | 적용 모드 | 검증 방법 |
|---|---|---|---|---|
| ORP selection 시점과 `entry_window_expires_at_min`을 조인하는 read-only 리포트 추가 | p90 78분이 만료 직전/이후 대기 때문인지 수치로 확인 | 리포트 쿼리 추가 | live/enforce read-only | `ticker_selection_log` + `intraday_strategy_log` join 테스트 |
| `strategy_feasibility` pack에 ORP elapsed/window evidence가 감사 row까지 남는지 확인 | selection 시점에 이미 실행 불가능한 ORP를 사후 식별 | payload 증가 | live/enforce | `tests/test_trading_bot_intraday_evidence.py`, candidate audit 테스트 |
| expired ORP는 `strategy_feasibility:orp_entry_window_expired`로 WATCH demotion 유지 | 이미 끝난 window를 BUY_READY로 끌고 가지 않음 | trade_ready 감소 가능 | live/enforce | `tests/test_trade_ready_slot_config.py::TradeReadySlotConfigTests::test_strategy_feasibility_demotes_expired_orp_without_slot_replacement` |
| ORP window/threshold 변경은 timing join 결과 이후 별도 승인 작업으로 분리 | 전략 철학 변경과 관측성 보강을 분리 | 개선 속도 지연 | 운영자 확인 필요 | 별도 backtest/replay |

코드 기준:

- `strategy/opening_range_pullback.py::diagnostics()`는 KR 기본 `or_minutes=10`, `entry_window_min=60`, `entry_window_expires_at_min=70` 계약을 이미 제공한다. 이 값은 바꾸지 않고 리포트/감사 payload에서 소비한다.
- `trading_bot.py::_strategy_feasibility_orp()`는 `orp_entry_window_expired`를 `state="expired"`, `hard_block=True`, `mutable=False`로 남기는지 확인한다.
- `trading_bot.py::_apply_strategy_feasibility_runtime_filter()`와 `_demote_strategy_feasibility_action()`은 expired ORP의 `BUY_READY`/`PROBE_READY`/`PULLBACK_WAIT`를 주문 경로로 보내지 않는지 검증한다.
- `tests/test_trade_ready_slot_config.py`, `tests/test_entry_timing.py`, `tests/test_trading_bot_intraday_evidence.py`를 우선 보강한다.

### P0. 지연 관측성 보강

가장 먼저 적용할 개선이다. 주문/리스크 정책을 바꾸지 않고 live/enforce로 넣을 수 있다.

| 개선안 | 기대 효과 | 위험 | 적용 모드 | 검증 방법 |
|---|---|---|---|---|
| `EntryTimingTracker`에 `candidate_to_first_signal_check_delay_min` 추가 | 후보가 선택된 뒤 첫 전략 평가까지 걸린 시간을 직접 측정 | 저장 필드 증가 | live/enforce | `tests/test_entry_timing.py` 보강 |
| candidate audit에 entry timing snapshot 연결 커버리지 보강 | `entry_timing_snapshot_missing` blocker 감소 | DB payload 증가 | live/enforce | `tests/test_candidate_audit.py`, `tools/analyze_candidate_audit.py` |
| PathB waiting scan에 `last_price_seen_at`, `zone_hit_at`, `hit_to_order_sec`, `price_sample_age_sec` 요약 추가 | PathB hit 전 감시 공백과 hit 후 제출 지연 분리 | 로그 증가 | live/enforce | `tests/test_pathb_runtime.py` 집중 |
| dashboard/report에 latency SLA, not_in_prompt, watch_trigger_not_evaluated를 같은 표에 노출 | 운영자가 병목 구간을 즉시 식별 | 대시보드 필드 증가 | live/enforce | dashboard payload 테스트 |

구현 시 보호 영역 영향:

- 주문 수량, 주문 금액, broker truth gate, hard stop, profit ladder는 건드리지 않는다.
- `runtime/action_routing.py::RouteDecision` 동작은 변경하지 않는다.
- `state/brain.json`은 수정하지 않는다.

### P1. prompt 진입 지연 완화

현재 가장 큰 구조적 병목 후보는 prompt coverage다. 단, 바로 BUY_READY를 넓히면 selection 품질 문제와 execution/risk 문제가 섞인다. 따라서 WATCH ceiling 기반으로 먼저 넓힌다.

| 개선안 | 기대 효과 | 위험 | 적용 모드 | 검증 방법 |
|---|---|---|---|---|
| `candidate_discovery_overlay`를 high-signal excluded 후보에 안정적으로 적용 | prompt cap 때문에 밀린 강한 후보를 Claude가 볼 수 있음 | prompt token 증가 | live/enforce, action ceiling WATCH | `tests/test_candidate_discovery_overlay.py`, selection prompt 테스트 |
| KR 고거래대금 NOT_IN_PROMPT 후보 전용 discovery slot 분리 | KR의 늦은 후보 포착 완화 | KR chase 후보 증가 | live/enforce, WATCH ceiling | KR screener_quality replay |
| discovery 후보는 `BUY_READY`/`PULLBACK_WAIT`를 바로 허용하지 않고 WATCH 또는 명시 ceiling 유지 | 주문 리스크 없이 관측 확대 | 실제 진입은 늦을 수 있음 | live/enforce | route/audit 계약 테스트 |
| discovery/sub_screener bucket을 `sub_screener_uplift_report` 최신 row에서도 분리 | 효과 측정 가능 | 보고서 로직 보강 필요 | live/enforce | `tests/test_sub_screener_uplift_report.py` |

운영 기준:

- discovery 후보가 WATCH 상태로 들어온 뒤 실제 전략 신호가 발생해야 trade_ready 승격을 검토한다.
- 최소 `candidate_to_first_signal_check_delay_min`, `candidate_to_signal_delay_min`, 30/60/120분 outcome coverage를 같이 본다.
- 성과 판단은 KR/US를 분리한다.

### P1. sub-screener triage/dedupe 개선

2026-06-05 US 로그에서 sub-screener가 `new_plan_a`를 감지했지만 dedupe suppress로 종료된 사례가 있었다. dedupe는 Claude 호출 폭증 방지에는 필요하지만, WATCH-only triage까지 막으면 신호 포착이 늦을 수 있다.

| 개선안 | 기대 효과 | 위험 | 적용 모드 | 검증 방법 |
|---|---|---|---|---|
| dedupe는 full Claude reinvoke만 억제하고 WATCH-only triage는 허용 | 중복 호출 없이 watchlist 반영 속도 개선 | watchlist 증가 | live/enforce | `tests/test_sub_screener.py`, integration 테스트 |
| fingerprint가 같아도 score/rank가 의미 있게 개선되면 triage 재허용 | 강해지는 후보를 놓칠 확률 감소 | triage 빈도 증가 | live/enforce | sub_screener state 테스트 |
| `last_dedupe_suppressed`에 skipped/added 후보와 기존 watchlist 여부 기록 | 억제된 후보가 실제로 신규였는지 사후 확인 | state JSON 증가 | live/enforce | state schema 테스트 |
| 장초반 30분 동안 `SUB_SCREENER_INTERVAL_MIN`을 별도 opening interval로 분리 | 장초 후보 반영 속도 개선 | API/CPU 증가 | config 변경은 운영자 확인 필요 | dry-run + live_preflight |

보호 조건:

- triage는 WATCH 추가까지만 허용한다.
- trade_ready, PULLBACK_WAIT, order route는 기존 Claude/routing/evidence gate를 통과해야 한다.
- Claude 호출량을 늘리는 full reinvoke는 rate limit/dedupe를 유지한다.

### P1. watch trigger 평가량 개선

`watch_trigger_not_evaluated`의 주요 원인이 `shadow_cycle_cap_exceeded`로 나타났다. 이 상태에서는 watch 후보가 실제로 신호 조건을 만족했는지 충분히 평가하지 못한다.

| 개선안 | 기대 효과 | 위험 | 적용 모드 | 검증 방법 |
|---|---|---|---|---|
| watch trigger 평가를 후보 점수/신호 family 기준 top-N 우선순위 큐로 변경 | 중요한 watch 후보부터 평가 | 낮은 순위 후보는 계속 지연 | live/enforce 또는 기존 shadow 유지 | watch_trigger 로그 테스트 |
| 평가 못 한 후보에 `next_eval_due_at`, `skipped_cycles`, `skip_reason` 기록 | 지연 원인 추적 가능 | 로그 증가 | live/enforce | funnel 로그 테스트 |
| `would_promote` 후보만 별도 compact report로 노출 | 실제 개선 후보 식별 | 표본 작음 | live/enforce | analyze_candidate_audit 테스트 |

주의:

- watch trigger를 바로 주문으로 연결하지 않는다.
- watch -> trade_ready 승격은 기존 전략 신호와 route/risk gate를 유지한다.

### P2. PathA scan interval의 조건부 단축

현재 `run_entry_scan`은 schedule상 1분마다 호출되지만 내부 interval은 장초반 2분, 이후 KR/US 기본 5분이다. selected -> signal p90이 큰 KR에는 조건부 단축 여지가 있다.

| 개선안 | 기대 효과 | 위험 | 적용 모드 | 검증 방법 |
|---|---|---|---|---|
| 장초반 이후에도 hot market 조건에서는 2분 scan 유지 | 신호 평가 지연 감소 | 가격 API/CPU 증가 | config 변경은 운영자 확인 필요 | cycle latency + API usage 비교 |
| trade_ready 또는 discovery 후보가 있는 경우만 interval 단축 | 불필요한 scan 증가 억제 | 조건 누락 시 효과 제한 | live/enforce | `run_entry_scan` interval 테스트 |
| KR selected -> signal p90 SLA 초과 시 temporary fast scan | KR 장중 지연 완화 | outlier에 반응 과민 가능 | live/enforce | replay성 테스트 |

이 변경은 주문/리스크 보호 영역은 건드리지 않지만 운영 부하와 API 호출량에 영향을 줄 수 있으므로, 실제 config/env 변경 전 운영자 확인이 필요하다.

### P2. PathB waiting price coverage 보강

PathB는 `CLAUDE_PRICE_HIT -> ORDER_SENT`가 빠르다. 따라서 개선 초점은 hit 이후가 아니라 price sample coverage다.

| 개선안 | 기대 효과 | 위험 | 적용 모드 | 검증 방법 |
|---|---|---|---|---|
| waiting run별 `last_price_seen_at`과 `price_sample_age_sec` 기록 | zone hit 감시 공백 식별 | 로그 증가 | live/enforce | PathB waiting scan 테스트 |
| waiting ticker가 WS 구독/price cache 대상인지 report | tick 미수신으로 인한 감시 지연 파악 | report 필드 증가 | live/enforce | dashboard PathB 테스트 |
| `created -> first_price_seen`, `created -> zone_hit`, `zone_hit -> order_sent`를 분리 | 의도된 zone wait와 실제 지연 구분 | event join 보강 필요 | live/enforce | v2 event store 테스트 |

보호 조건:

- broker truth fail-closed, buy zone evidence gate, PathB sizing, profit ladder는 변경하지 않는다.
- 가격 감시 관측성부터 보강하고, order submit 조건은 유지한다.

## 15. 권장 구현 순서

1. **P0 ORP Feasibility 연결 검증**
   - KR selected -> signal p90 78.828분을 ORP `10+60=70분` window와 조인하는 read-only 리포트부터 만든다.
   - `orp_entry_window_expired=395` 샘플이 selection 시점, first signal check, expiry 시점 중 어디에서 발생했는지 분리한다.
   - expired ORP demotion과 감사 payload가 이미 연결되어 있으면 코드 변경 없이 리포트/테스트만 보강한다.
   - ORP window/threshold는 이 단계에서 변경하지 않는다.

2. **P0 관측성 패치**
   - `EntryTimingTracker` 파생 지표 추가.
   - candidate audit timing snapshot coverage 보강.
   - analyze/report에서 최신 row와 call-level timing coverage를 분리 표시.

3. **P1 prompt coverage 패치**
   - discovery overlay가 실제 prompt pool에 반영되는지 live 설정/코드 경로 확인.
   - KR 고거래대금/near_breakout/momentum_now excluded 후보를 WATCH ceiling으로 추가.
   - discovery bucket 성과 report가 최신 row에서도 보이게 보강.

4. **P1 sub-screener dedupe/triage 패치**
   - dedupe가 full reinvoke만 막고 WATCH-only triage는 막지 않도록 분리.
   - score/rank 개선 시 triage 재허용.
   - state에 dedupe suppress 사유와 후보 반영 여부 기록.

5. **P1 watch trigger 평가량 패치**
   - `shadow_cycle_cap_exceeded`를 줄이기 위한 top-N priority queue.
   - 평가 못 한 후보의 next due와 skip count 기록.

6. **P2 scan interval 조건부 단축**
   - 관측성 지표로 KR selected -> first_signal_check / selected -> signal p90이 계속 높을 때만 적용.
   - API 호출량과 cycle latency를 같이 측정.

## 16. 이번 단계에서 바로 구현하지 않는 것

- trade_ready 기준을 전면 완화하지 않는다.
- PathB buy zone evidence gate를 완화하지 않는다.
- broker truth fail-closed를 완화하지 않는다.
- PathB sizing, hard stop, profit ladder, pre-close 청산을 건드리지 않는다.
- `state/brain.json`에 자동 교훈을 승격하지 않는다.
- KR/US 전략 성과를 섞어 하나의 전략 기준으로 바꾸지 않는다.

## 17. 검증 명령 후보

ORP Feasibility 연결:

- `python -m pytest tests/test_trade_ready_slot_config.py::TradeReadySlotConfigTests::test_strategy_feasibility_demotes_expired_orp_without_slot_replacement -q`
- `python -m pytest tests/test_entry_timing.py::EntryTimingTests::test_orp_diagnostics_explain_not_formed_and_expired -q`
- `python -m pytest tests/test_trading_bot_intraday_evidence.py::TradingBotIntradayEvidenceTests::test_annotation_adds_strategy_feasibility_pack -q`

관측성 패치:

- `python -m pytest tests/test_entry_timing.py -q`
- `python -m pytest tests/test_candidate_audit.py -q`
- `python tools/analyze_candidate_audit.py --date 2026-06-05 --market KR --runtime-mode live --limit 5`
- `python tools/analyze_candidate_audit.py --date 2026-06-05 --market US --runtime-mode live --limit 5`

discovery/sub-screener 패치:

- `python -m pytest tests/test_candidate_discovery_overlay.py tests/test_sub_screener.py tests/test_sub_screener_integration.py -q`
- `python -m pytest tests/test_sub_screener_uplift_report.py -q`
- `python tools/sub_screener_uplift_report.py --session-date 2026-06-05 --market KR --runtime-mode live`
- `python tools/sub_screener_uplift_report.py --session-date 2026-06-05 --market US --runtime-mode live`

PathB 관측성 패치:

- `python -m pytest tests/test_pathb_runtime.py -q`
- `python -m pytest tests/test_auto_sell_claude_gate.py::AutoSellClaudeGateTests::test_pathb_loss_cap_hold_respects_reask_cooldown -q`
- `python -m py_compile trading_bot.py runtime/pathb_runtime.py bot/entry_timing.py`

운영 전 확인:

- `python tools/live_preflight.py --mode live --skip-dashboard --json`
- candidate audit / funnel / dashboard timestamp 확인
- config/env 변경 여부 확인
- 주문/리스크/broker truth/Claude 호출량 영향 확인

## 18. 코드레벨 작업 항목

### 18.1 ORP timing attribution report

목표: KR selected -> signal p90 78.828분을 ORP 70분 window와 직접 연결해 증명한다.

| 항목 | 코드 위치 | 구현 내용 | 테스트/검증 |
|---|---|---|---|
| ORP expiry join | 신규 `tools/` 리포트 또는 `tools/analyze_candidate_audit.py` 확장 | `ticker_selection_log`의 selected/trade_ready 시각과 `intraday_strategy_log`의 `orp_entry_window_expired`, `entry_window_elapsed_min`, `session_elapsed_min`을 ticker/session 기준으로 조인 | 샘플 DB fixture로 expired/window-before/window-after 케이스 검증 |
| ORP evidence 출력 | `strategy/opening_range_pullback.py::diagnostics()` 소비부 | `or_minutes`, `entry_window_min`, `entry_window_expires_at_min`, `elapsed_min`, `reason`을 리포트 컬럼에 표시 | `tests/test_entry_timing.py::EntryTimingTests::test_orp_diagnostics_explain_not_formed_and_expired` |
| feasibility 연결 | `trading_bot.py::_strategy_feasibility_orp()` | expired ORP가 `state=expired`, `reason=orp_entry_window_expired`, `hard_block=True`, `mutable=False`로 남는지 회귀 확인 | `tests/test_trade_ready_slot_config.py::TradeReadySlotConfigTests::test_strategy_feasibility_demotes_expired_orp_without_slot_replacement` |

금지 변경:

- `or_minutes`, `entry_window_min`, ORP range/pullback/volume threshold를 이 작업에서 바꾸지 않는다.
- expired ORP를 다른 전략 slot으로 자동 대체하지 않는다.
- PathB wait/order route, broker truth, risk gate는 건드리지 않는다.

### 18.2 EntryTimingTracker 첫 signal check 지표

목표: 후보가 선택된 뒤 첫 전략 평가까지 걸린 시간을 `candidate_to_signal_delay_min`과 분리한다.

| 항목 | 코드 위치 | 구현 내용 | 테스트/검증 |
|---|---|---|---|
| 파생 지표 추가 | `bot/entry_timing.py::EntryTimingTracker._derived_metrics()` | `candidate_to_first_signal_check_delay_min = candidate_detected_at -> first_signal_checked_at` 추가 | `tests/test_entry_timing.py::EntryTimingTests::test_tracker_records_candidate_signal_order_and_fill_delays` 확장 |
| summary 집계 | `bot/entry_timing.py::build_entry_timing_summary()` | 평균/최근 row에 새 필드가 누락되지 않게 유지 | `tests/test_entry_timing.py -q` |
| audit 소비 | `trading_bot.py::_candidate_entry_timing_context()`, `_build_selection_evidence_pack()` | evidence pack `execution_timing`에 first signal check 시각/지연을 넣어 candidate audit과 report에서 읽을 수 있게 함 | candidate audit/evidence 테스트 |

금지 변경:

- signal firing 조건이나 주문 제출 조건을 바꾸지 않는다.
- tracker 로그를 runtime truth로 취급하지 않는다. broker truth/주문 상태는 기존 계약 유지.

### 18.3 Candidate audit timing coverage

목표: `entry_timing_snapshot_missing` blocker를 줄이고 latest-row 집계와 call-level 집계를 분리한다.

| 항목 | 코드 위치 | 구현 내용 | 테스트/검증 |
|---|---|---|---|
| snapshot 주입 | `trading_bot.py::_candidate_entry_timing_context()` | tracker snapshot이 없을 때 fallback source를 명시하고, 빈 snapshot과 누락 snapshot을 구분 | candidate audit 테스트 |
| audit field 보존 | `trading_bot.py` candidate audit row 생성부, `audit/candidate_audit_store.py` | `entry_timing_snapshot_json` 또는 동일 payload가 blank update로 지워지지 않게 보존 | `tests/test_candidate_audit.py` |
| 분석 리포트 분리 | `tools/analyze_candidate_audit.py` | latest row coverage와 call-level coverage를 별도 지표로 출력 | CLI smoke + fixture |

금지 변경:

- candidate audit DB schema 변경이 필요하면 producer/store/analyzer/dashboard 흐름을 한 번에 연결한다.
- 빈 값 기본 처리로 실제 누락을 숨기지 않는다.

### 18.4 Discovery overlay WATCH ceiling

목표: prompt cap 때문에 밀린 high-signal 후보를 주문 리스크 없이 Claude 관측 대상으로 올린다.

| 항목 | 코드 위치 | 구현 내용 | 테스트/검증 |
|---|---|---|---|
| overlay 적용 확인 | `runtime/candidate_discovery_overlay.py::apply_discovery_overlay()` | KR 고거래대금/near_breakout/momentum_now excluded 후보가 `_final_prompt_pool`에 `candidate_pool_role=DISCOVERY`로 들어오는지 확인 | `tests/test_candidate_discovery_overlay.py` |
| action ceiling 보존 | `trading_bot.py::_apply_discovery_action_ceiling()` | discovery 후보의 `BUY_READY`/`PROBE_READY`/`PULLBACK_WAIT`는 기본 WATCH로 demote | `tests/test_candidate_action_live_mapping.py` |
| 성과 bucket 분리 | `tools/sub_screener_uplift_report.py`, `tools/monitoring_ops_report.py` | discovery metadata row와 prompt metrics를 latest-row 기준에서도 분리 | `tests/test_sub_screener_uplift_report.py`, `tests/test_monitoring_ops_report.py` |

금지 변경:

- discovery 후보를 즉시 BUY_READY/live order로 승격하지 않는다.
- US/KR discovery 성과를 합산해 하나의 기준으로 판단하지 않는다.

### 18.5 Sub-screener dedupe와 WATCH-only triage 분리

목표: 중복 Claude full reinvoke는 막되, 신규 강한 후보의 WATCH-only triage는 허용한다.

| 항목 | 코드 위치 | 구현 내용 | 테스트/검증 |
|---|---|---|---|
| dedupe 분기 | `trading_bot.py::maybe_run_sub_screener()` | `is_duplicate_trigger()`가 true여도 triage 후보가 기존 watchlist에 없거나 score/rank가 개선됐으면 `_apply_sub_screener_triage()`만 실행 | `tests/test_sub_screener_integration.py` |
| state 기록 | `runtime/sub_screener.py::record_dedupe_suppressed()` | suppressed 후보별 기존 watchlist 여부, triage 허용/차단 사유, score/rank delta 기록 | `tests/test_sub_screener.py` |
| triage audit | `trading_bot.py::_apply_sub_screener_triage()` | 추가/skip 후보와 reason을 candidate funnel/candidate audit에서 확인 가능하게 유지 | integration + uplift report |

금지 변경:

- dedupe를 완전히 끄지 않는다.
- triage는 WATCH 추가까지만 허용하고 full Claude 재호출 정책은 기존 rate limit/dedupe를 유지한다.

### 18.6 Watch trigger 평가량 보강

목표: `watch_trigger_not_evaluated`와 `shadow_cycle_cap_exceeded`를 후보 우선순위와 다음 평가 시각으로 설명 가능하게 만든다.

현재 watch trigger shadow 평가는 독립 evaluator 함수가 아니라 `trading_bot.py::TradingBot.run_entry_scan()` 내부의 ticker loop에서 수행된다. 따라서 1차 수정 지점은 `run_entry_scan()`이며, 후보 ranking 로직이 길어질 때만 helper를 분리한다.

| 항목 | 코드 위치 | 구현 내용 | 테스트/검증 |
|---|---|---|---|
| priority queue | `trading_bot.py::TradingBot.run_entry_scan()` watch-trigger block, `TradingBot._watch_trigger_shadow_strategy_for_ticker()` | 후보 점수, discovery/sub_screener origin, strategy family, 최근 skip count 기준 top-N 선정. 복잡해지면 신규 `TradingBot._watch_trigger_shadow_rank_candidates()`로 추출 | `tests/test_patha_contract.py::PathAContractTests::test_watch_trigger_shadow_strategy_uses_recommended_strategy` + 신규 ranking 테스트 |
| skip telemetry | `trading_bot.py::TradingBot._log_watch_trigger_not_evaluated()`, `_log_watch_trigger_shadow()`, `_write_funnel_event()` | `next_eval_due_at`, `skipped_cycles`, `skip_reason`, `would_promote` 기록 | `tests/test_candidate_audit.py::CandidateAuditBackfillTests::test_watch_trigger_funnel_summary_counts_shadow_events` |
| compact report | `tools/analyze_candidate_audit.py::watch_trigger_funnel_summary()`, `_watch_trigger_shadow_outcomes()` | 평가 못 한 후보와 would-promote 후보를 분리 표시 | candidate audit CLI fixture |

금지 변경:

- watch trigger 결과를 직접 주문으로 연결하지 않는다.
- 기존 trade_ready/routing/risk gate를 우회하지 않는다.

### 18.7 PathA scan interval 조건부 단축

목표: 관측성 지표로 확인된 경우에만 selected -> first signal check 지연을 줄인다.

| 항목 | 코드 위치 | 구현 내용 | 테스트/검증 |
|---|---|---|---|
| hot condition | `trading_bot.py::TradingBot._entry_scan_interval_sec()`, `TradingBot.run_entry_scan()`의 `_last_entry_scan_at` gate | trade_ready/discovery 후보가 있고 SLA가 초과될 때만 2분 fast scan 유지 | 신규 `_entry_scan_interval_sec` 단위 테스트 + `tests/test_sub_screener_integration.py::SubScreenerIntegrationTests::test_entry_scan_continues_when_sub_screener_fails` |
| 부하 계측 | `trading_bot.py::TradingBot._record_cycle_latency()`, `_write_funnel_event()`, `tools/analyze_candidate_audit.py::latency_sla_summary()` | API 호출량, cycle p90, candidate_to_first_signal_check p90을 같이 기록 | latency SLA fixture + dry-run |
| config gate | `trading_bot.py`의 `_ENTRY_SCAN_OPENING_MIN`, `_ENTRY_SCAN_OPENING_INTERVAL_MIN`, `_ENTRY_SCAN_REGULAR_INTERVAL_MIN`, `US_ENTRY_SCAN_REGULAR_INTERVAL_MIN`, `tools/live_preflight.py` 설정 노출 | 기본값은 현행 유지, 실제 config/env 변경은 운영자 확인 후 적용 | config snapshot 확인 + `python tools/live_preflight.py --mode live --skip-dashboard --json` |

금지 변경:

- 기본 scan interval을 무조건 단축하지 않는다.
- `.env.live`, `config/v2_start_config.json`은 문서/승인 없이 바꾸지 않는다.

### 18.8 PathB waiting price coverage

목표: PathB는 hit -> order가 빠르므로 hit 이전 price coverage 공백만 관측성으로 분리한다.

| 항목 | 코드 위치 | 구현 내용 | 테스트/검증 |
|---|---|---|---|
| price seen audit | `runtime/pathb_runtime.py::_audit_pathb_price_seen()` | waiting run별 `last_price_seen_at`, `price_sample_age_sec`, `created_to_first_price_seen_sec` 기록 | `tests/test_pathb_runtime.py` 집중 |
| zone hit latency | `runtime/pathb_runtime.py::_audit_pathb_zone_hit()` | `created_to_zone_hit_sec`, `zone_hit_to_order_sec`를 lifecycle/report에서 분리 | v2 event store 테스트 |
| dashboard 노출 | `dashboard/dashboard_server.py` PathB summary | waiting ticker별 price coverage stale 여부 표시 | `tests/test_dashboard_pathb.py` |

금지 변경:

- PathB broker-truth fail-closed, buy zone evidence gate, sizing, profit ladder, pre-close 청산은 변경하지 않는다.
- hit 이후 order submit 조건을 완화하지 않는다.
