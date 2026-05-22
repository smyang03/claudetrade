# KR/US DB 재검토 리포트

- generated_at: 2026-05-19T18:02:41+09:00 KST
- scope: 로컬 코드와 SQLite DB만 조회. 브로커, 주문, API, Claude 호출 없음.
- objective: 기존 수익성 리포트와 후속 해석을 재검토하고, 실제로 정책 변경에 사용할 수 있는 신호와 아직 추가 분석이 필요한 영역을 분리한다.

## 결론 요약

전체 DB를 모두 같은 신뢰도로 본 것은 아니다. 주요 DB는 모두 훑었지만, 정책 판단에 바로 쓸 수 있는 축은 제한적이다. 특히 `v2_event_store.db`는 실제 체결/청산 truth에 가장 가깝지만 중복 이벤트가 많고, `candidate_audit.db`의 30m/60m outcome은 sparse label이며, `decisions.db`는 실제 fill 학습 데이터가 거의 없다.

가장 중요한 보정은 다음 네 가지다.

1. `ticker_selection_log`의 기존 `KR watch_only > trade_ready` 결론은 paper 포함 all-mode와 latest-state 집계의 영향이 크다. live-only state split으로 보면 KR ever-ready는 오히려 watch보다 좋다.
2. `candidate_audit`의 `claude_ready < watch_only` 신호는 재현되지만, raw bucket이 상호배타가 아니다. watch_only 안에 filled row와 같은 ticker-day에서 ready였던 row가 섞여 있어 그대로 selection 결론으로 쓰면 안 된다.
3. KR의 나쁜 진입 시간대는 dedupe 후에도 유지된다. 첫 30분과 14:00 이후는 계속 손실이 크다.
4. US preopen은 나쁘지만 raw 7건이 dedupe 후 2026-04-27 하루의 3개 decision으로 줄어든다. 따라서 hard block 확정이 아니라 임시 보수 가드와 추가 라벨 수집이 맞다.

## 조회한 DB와 기간

| DB | 주요 테이블 | rows | 조회 기간/범위 |
| --- | --- | ---: | --- |
| `data/ml/decisions.db` | `decisions` | 31,042 | KR 2025-02-19~2026-05-19, US 2024-06-27~2026-05-18 |
| `data/audit/candidate_audit.db` | `audit_candidate_rows` | 22,234 | live only, 2026-04-20~2026-05-19 |
| `data/audit/candidate_audit.db` | `audit_candidate_outcomes` | 34,215 | 후보 outcome label, 30m/60m sparse 중심 |
| `data/audit/candidate_audit.db` | `candidate_counterfactual_paths` | 5,145 | 2026-05-18~2026-05-19, outcome 미기입 |
| `data/ticker_selection_log.db` | `ticker_selection_log` | 3,365 | 2026-04-07~2026-05-19, live/paper 혼재 |
| `data/v2_event_store.db` | `lifecycle_events` | 2,535 | live only, 2026-04-27~2026-05-18 |
| `data/v2_event_store.db` | `v2_decisions` | 340 | live only, 2026-04-27~2026-05-18 |
| `data/v2_event_store.db` | `v2_path_runs` | 313 | live only, 2026-04-27~2026-05-18 |
| `data/v2_event_store.db` | `pathb_miss_quality` | 24 | 2026-05-11~2026-05-18 |
| `data/intraday_strategy_log.db` | `intraday_strategy_log` | 15,178 | 2026-04-10~2026-05-19, live/paper 혼재 |
| `data/market_data/market_data.sqlite` | `strategy_metrics` | 383 | 백테스트 메트릭 |
| `data/market_data/market_data.sqlite` | `backtest_trades` | 34,997 | 백테스트 거래 |

## 데이터 신뢰도 판정

| 축 | 판정 | 이유 |
| --- | --- | --- |
| 실제 체결/청산 성과 | 중간 | `v2_event_store.lifecycle_events`가 가장 가깝지만 `FILLED`, `CLOSED` 중복 이벤트가 많아 dedupe 필요 |
| selection forward 1d | 중간 | `ticker_selection_log`에 live/paper가 혼재하고, latest-state와 ever-ready 정의가 서로 다른 결론을 낸다 |
| candidate audit 30m/60m | 낮음~중간 | label이 `audit_sparse`이고 평균 샘플 수가 2~4개 수준 |
| learning DB | 낮음 | `decisions.db` KR filled 0, US filled 3 |
| counterfactual path | 낮음 | row는 있으나 30m/60m/close outcome이 전부 0 |
| market_data backtest | 보조 참고 | 구조적 전략 방향 참고용. live 체결 truth는 아님 |

## Learning DB 상태

`decisions.db`는 장기 이력 범위는 넓지만 실제 체결 학습 데이터가 거의 없다.

| market | rows | days | min | max | BUY rows | filled rows | avg fwd1d |
| --- | ---: | ---: | --- | --- | ---: | ---: | ---: |
| KR | 10,026 | 280 | 2025-02-19 | 2026-05-19 | 602 | 0 | -0.557% |
| US | 21,016 | 448 | 2024-06-27 | 2026-05-18 | 1,231 | 3 | +0.363% |

판단: 현재 ML/learning 축은 실제 체결 성과를 학습한다고 보기 어렵다. 수익률 개선의 P0는 새 전략보다 V2 lifecycle truth를 learning/performance table로 정규화하는 것이다.

## V2 Lifecycle 커버리지와 중복

`lifecycle_events` raw count와 decision/ticker dedupe count가 다르다. 따라서 entry band, preopen, hard stop 통계는 raw 기준만 쓰면 왜곡된다.

| market | filled events | filled decisions | closed events | closed decisions | QUALITY_MARKED | quality/closed events | quality/closed decisions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KR | 63 | 42 | 48 | 40 | 3 | 6.2% | 7.5% |
| US | 150 | 78 | 81 | 67 | 18 | 22.2% | 26.9% |

raw vs dedupe closed PnL:

| market | raw closed n | raw avg pnl | first close dedupe n | first avg pnl | last close dedupe n | last avg pnl |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| KR | 48 | -1.015% | 40 | -0.917% | 40 | -0.941% |
| US | 81 | +0.560% | 67 | +0.660% | 67 | +0.662% |

판단: 기존 방향성은 크게 바뀌지 않지만, 샘플 수와 band별 평균은 dedupe 기준으로 다시 고정해야 한다.

## Entry Timing 재검토

아래 표는 `FILLED` earliest time을 기준으로 band를 나누고, `CLOSED`는 decision/ticker별 첫 close로 dedupe한 결과다. KR은 KST, US는 ET 기준이다.

### KR

| band | n | avg pnl | win rate | loss/hard/stop count |
| --- | ---: | ---: | ---: | ---: |
| 09:00-09:30 | 3 | -4.252% | 33.3% | 2 |
| 09:30-10:30 | 12 | +0.093% | 33.3% | 7 |
| 10:30-12:00 | 9 | -1.188% | 33.3% | 3 |
| 12:00-14:00 | 10 | -0.148% | 30.0% | 2 |
| 14:00-15:00 | 6 | -2.146% | 16.7% | 2 |

KR raw 기준도 같은 방향이다. 첫 30분은 raw `n=4 avg -4.989%`, 14:00 이후는 raw `n=7 avg -2.227%`였다. 09:30-10:30만 상대적으로 괜찮고, 10:30-12:00도 다시 나빠진다. 따라서 단순히 "첫 30분만 지나면 허용"이 아니라, 09:30-10:30, 12:00-14:00, 그 외 구간을 분리해야 한다.

### US

| band | n | avg pnl | win rate | loss/hard/stop count |
| --- | ---: | ---: | ---: | ---: |
| preopen | 3 | -0.755% | 0.0% | 0 |
| 09:30-09:45 | 13 | +1.463% | 53.8% | 3 |
| 09:45-10:30 | 15 | -0.288% | 46.7% | 7 |
| 10:30-12:00 | 22 | +1.206% | 59.1% | 5 |
| 12:00-14:00 | 7 | +1.171% | 42.9% | 2 |
| 14:00-15:30 | 4 | -0.278% | 50.0% | 0 |
| 15:30+ | 1 | +1.019% | 100.0% | 0 |

US preopen raw는 7 closed rows였지만, decision/ticker dedupe 후에는 2026-04-27 하루의 AMD, AMZN, ARM 3개 decision이다. 모두 손실이므로 위험 신호는 맞지만, 통계적으로 hard block을 확정하기에는 표본이 작다. 정책상으로는 임시 보수 가드, 예를 들어 preopen 신규 진입 차단 또는 극단적으로 높은 confirmation 요구를 적용하고, 라벨을 더 모은 뒤 확정하는 편이 맞다.

## Selection Forward 재검토

기존 리포트의 `KR trade_ready -0.618%`, `watch_only +0.384%`는 all-mode, 즉 paper까지 섞은 latest ticker-day 집계였다. live-only와 state split으로 보면 해석이 달라진다.

### All-mode latest ticker-day

| market | bucket | n | days | avg fwd1d | pos | bad<=-3 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| KR | trade_ready | 65 | 12 | -0.618% | 35.4% | 43.1% |
| KR | watch_only | 580 | 27 | +0.384% | 38.3% | 36.9% |
| US | trade_ready | 80 | 13 | +1.384% | 55.0% | 12.5% |
| US | watch_only | 821 | 29 | +0.496% | 54.4% | 21.2% |

### Live-only latest ticker-day

| market | bucket | n | days | avg fwd1d | pos | bad<=-3 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| KR | trade_ready | 65 | 12 | -0.618% | 35.4% | 43.1% |
| KR | watch_only | 418 | 19 | -0.007% | 37.6% | 42.1% |
| US | trade_ready | 80 | 13 | +1.384% | 55.0% | 12.5% |
| US | watch_only | 532 | 20 | -0.379% | 46.2% | 28.4% |

### Live-only state split

state split은 같은 ticker-day가 ready였다가 나중에 watch로 바뀐 경우를 ready와 watch 상태별로 분리한다.

| market | bucket | n | days | avg fwd1d | pos | bad<=-3 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| KR | trade_ready | 83 | 12 | +1.174% | 41.0% | 38.6% |
| KR | watch_only | 432 | 19 | -0.059% | 37.5% | 42.1% |
| US | trade_ready | 112 | 13 | +1.281% | 54.5% | 15.2% |
| US | watch_only | 563 | 20 | -0.261% | 47.1% | 27.4% |

판단: KR selection 자체가 무조건 watch보다 나쁘다고 결론내리면 안 된다. 다만 latest-state 기준의 KR ready가 약한 것은 실제 운영상 중요하다. 즉 "한 번 ready로 잡는 능력"과 "끝까지 ready로 유지하는 기준"을 분리해서 봐야 한다.

## Candidate Audit 30m/60m 재검토

`candidate_audit` outcome label은 `audit_sparse`이며, 평균 샘플 수가 30m는 약 2.4~2.6개, 60m는 약 3.7개다. 라벨 자체가 완전한 minute-bar truth가 아니라는 점을 전제로 봐야 한다.

### Outcome label coverage

| market | horizon | status | rows | days | period | labeled | avg return | avg samples |
| --- | ---: | --- | ---: | ---: | --- | ---: | ---: | ---: |
| KR | 30m | audit_sparse | 1,033 | 8 | 2026-04-29~2026-05-18 | 1,033 | +0.115% | 2.56 |
| KR | 30m | insufficient_samples | 6,371 | 16 | 2026-04-20~2026-05-19 | 0 | - | 0.34 |
| KR | 60m | audit_sparse | 808 | 8 | 2026-04-22~2026-05-18 | 808 | +0.055% | 3.68 |
| KR | 60m | insufficient_samples | 6,596 | 16 | 2026-04-20~2026-05-19 | 0 | - | 0.68 |
| US | 30m | audit_sparse | 1,157 | 12 | 2026-04-22~2026-05-15 | 1,157 | +0.069% | 2.43 |
| US | 30m | insufficient_samples | 7,771 | 19 | 2026-04-20~2026-05-18 | 0 | - | 0.36 |
| US | 60m | audit_sparse | 725 | 10 | 2026-04-22~2026-05-15 | 725 | -0.014% | 3.65 |
| US | 60m | insufficient_samples | 8,203 | 19 | 2026-04-20~2026-05-18 | 0 | - | 0.70 |

중요한 추가 문제: `audit_candidate_latest_rows`에 조인되는 30m/60m outcome return label은 사실상 0이다. 최신 후보 상태를 기준으로 보면 outcome이 비어 있고, 전체 row 기준으로만 sparse label이 붙어 있다.

### Raw ready/watch bucket

| market | horizon | bucket | n | days | avg return | pos | bad<=-3 | filled rows | same ticker-day ever ready |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KR | 30m | claude_ready | 58 | 5 | -0.361% | 32.8% | 20.7% | 22 | 58 |
| KR | 30m | watch_only | 241 | 8 | +0.292% | 33.2% | 14.9% | 28 | 43 |
| KR | 60m | claude_ready | 54 | 6 | -0.701% | 50.0% | 22.2% | 17 | 54 |
| KR | 60m | watch_only | 199 | 8 | +0.240% | 39.2% | 17.6% | 26 | 38 |
| US | 30m | claude_ready | 98 | 10 | +0.175% | 41.8% | 0.0% | 31 | 98 |
| US | 30m | watch_only | 367 | 12 | +0.242% | 35.4% | 2.7% | 60 | 68 |
| US | 60m | claude_ready | 72 | 10 | +0.218% | 54.2% | 0.0% | 16 | 72 |
| US | 60m | watch_only | 224 | 10 | +0.173% | 48.7% | 8.9% | 37 | 43 |

판단: raw로는 KR `claude_ready`가 watch보다 나빠 보이는 것이 맞다. 하지만 watch_only bucket에 filled row와 ever-ready row가 섞인다. 이 표는 경고 신호로는 유효하지만, selection 편향 확정 증거로는 부족하다.

## KR modern action 품질 저하

KR에서 더 명확하게 봐야 할 것은 legacy `TRADE_READY`와 modern action schema의 차이다.

| horizon | bucket | n | days | avg return | pos | bad<=-3 | min | max |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 30m | legacy_trade_ready | 40 | 3 | +0.870% | 42.5% | 7.5% | -9.552% | +16.221% |
| 30m | modern_actions | 16 | 2 | -3.292% | 6.2% | 50.0% | -9.935% | +1.908% |
| 60m | legacy_trade_ready | 37 | 4 | +0.480% | 64.9% | 8.1% | -7.402% | +7.937% |
| 60m | modern_actions | 15 | 2 | -3.504% | 13.3% | 53.3% | -10.109% | +4.489% |

modern action rows는 2026-05-07~2026-05-08에 집중되어 있다. `BUY_READY`, `PROBE_READY`, `PULLBACK_WAIT`, `ADD_READY`를 같은 ready bucket으로 묶으면 KR에서 손실이 크게 보인다. 따라서 KR 문제는 "Claude selection 전체"보다 "최근 action schema와 route 변환이 어떤 후보를 실행 가능 상태로 만들었는지"를 우선 조사해야 한다.

## Counterfactual Path 상태

`candidate_counterfactual_paths`는 만들어졌지만 outcome이 없다.

| market | status | rows | days | period | 30m outcomes | 60m outcomes | close outcomes |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: |
| KR | BASELINE_NO_TRADE | 300 | 2 | 2026-05-18~2026-05-19 | 0 | 0 | 0 |
| KR | DATA_MISSING | 580 | 2 | 2026-05-18~2026-05-19 | 0 | 0 | 0 |
| KR | PENDING | 1,820 | 2 | 2026-05-18~2026-05-19 | 0 | 0 | 0 |
| KR | TRIGGERED | 300 | 2 | 2026-05-18~2026-05-19 | 0 | 0 | 0 |
| US | BASELINE_NO_TRADE | 195 | 1 | 2026-05-18 | 0 | 0 | 0 |
| US | PENDING | 1,755 | 1 | 2026-05-18 | 0 | 0 | 0 |
| US | TRIGGERED | 195 | 1 | 2026-05-18 | 0 | 0 | 0 |

판단: 현재 counterfactual path는 "관측 대상"일 뿐, 아직 성과 비교 테이블이 아니다. watch_only missed runup, blocked candidate, alternate entry를 데이터 기반으로 승격하려면 outcome backfill이 필수다.

## PathB Miss Quality

| market | cancel reason | n | period | avg MFE30 | avg MAE30 | zone reentered | avg quotes |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |
| KR | EXPIRED | 7 | 2026-05-11~2026-05-14 | +4.490% | +4.490% | 71.4% | 1.0 |
| US | EXPIRED | 4 | 2026-05-15 | -3.907% | -3.907% | 100.0% | 1.0 |
| US | INVALID_PRICE | 10 | 2026-05-11~2026-05-18 | +1.219% | +0.049% | 90.0% | 14.5 |
| US | MAX_DAILY_ENTRIES | 3 | 2026-05-14 | -0.044% | -0.314% | 100.0% | 71.3 |

KR `EXPIRED`는 MFE30이 높고 평균 quote sample이 1.0이라, 단순히 후보가 나빠서 놓친 것이 아니라 가격 재샘플링/만료 로직 문제일 가능성이 있다. 단, 표본은 7건이다.

## Intraday Strategy Log

`intraday_strategy_log` live는 현재 opening range pullback 중심이다.

| market | rows | days | signals | traded | 주요 blocked reason |
| --- | ---: | ---: | ---: | ---: | --- |
| KR | 3,775 | 20 | 0 | 0 | `orp_not_formed`, `orp_forming`, `orp_range_too_high` |
| US | 6,250 | 21 | 2 | 2 | `orp_not_formed`, `orp_entry_window_expired`, `orp_forming` |

판단: 이 DB는 실행 성과 개선의 1차 truth가 아니다. ORP가 거의 체결로 이어지지 않았기 때문에, 지금 단계에서는 entry timing/selection audit보다 우선순위가 낮다.

## Backtest 방향성

`market_data.sqlite`는 live truth가 아니므로 방향성 참고로만 사용한다.

KR에서는 momentum 계열이 상대적으로 강하다.

| market | strategy | entry_model | universe | window | trades | avg_net | PF | win |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| KR | momentum | same_close | core | official_2018 | 189 | +0.879% | 1.499 | 50.8% |
| KR | momentum | gap_filter | ALL/core | post_covid_2020 | 123 | +0.813% | 1.411 | 46.3% |
| KR | momentum | confirmation_next_open | core | official_2018 | 82 | +0.553% | 1.285 | 46.3% |

US에서는 mean_reversion + confirmation/gap_filter 계열이 상대적으로 안정적이다.

| market | strategy | entry_model | universe | window | trades | avg_net | PF | win |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| US | mean_reversion | confirmation_next_open | fallback | post_covid_2020 | 69 | +0.397% | 1.385 | 49.3% |
| US | gap_pullback | same_close | dynamic_only | official_2018 | 42 | +0.364% | 1.793 | 54.8% |
| US | mean_reversion | confirmation_next_open | ALL | post_covid_2020 | 107 | +0.351% | 1.332 | 48.6% |
| US | mean_reversion | gap_filter | ALL/fallback | stress_2022 | 71 | +0.324% | 1.314 | 47.9% |
| US | mean_reversion | confirmation_next_open | fallback | official_2018 | 88 | +0.312% | 1.292 | 47.7% |

## 개선 우선순위

### P0. V2 lifecycle truth를 canonical performance table로 정규화

필수 요구:

- decision/ticker 기준 중복 `FILLED`, `CLOSED` dedupe 규칙 고정
- earliest fill, first close, last close를 모두 보존
- market, runtime_mode, session_date, ticker, decision_id, route, strategy, entry_at, exit_at, pnl_pct, close_reason 저장
- `QUALITY_MARKED`와 연결
- KR/US 분리 집계

이 작업 전에는 raw lifecycle 통계를 자동 학습에 넣으면 안 된다.

### P0. `decisions.db` fill 연결 복구

현재 KR filled 0, US filled 3이다. 실제 V2 lifecycle closed는 KR 40~48건, US 67~81건이 있다. 이 괴리 때문에 학습 DB가 실제 운영 결과를 따라가지 못한다.

개선 방향:

- lifecycle closed를 `decisions.db`에 직접 덮어쓰기보다 별도 `learning/performance` table로 먼저 정규화
- 이후 decisions row와 execution_decision_id 또는 ticker/session_date로 linking
- unmatched row는 별도 audit table에 남김

### P0. Counterfactual outcome backfill

`candidate_counterfactual_paths`는 5,145 rows가 있지만 outcome이 0개다. 다음 값을 채워야 한다.

- trigger 기준 30m/60m return
- close 기준 return
- 60m MFE/MAE
- data_missing reason
- price source와 sample count

### P0. Metric contract 고정

앞으로 리포트는 최소 네 가지를 항상 분리해야 한다.

- live-only vs paper 포함
- raw row vs decision/ticker dedupe
- latest ticker-day vs state split vs ever-ready
- raw watch_only vs pure watch_only

특히 `watch_only`는 다음처럼 나눠야 한다.

- raw_watch_only
- pure_watch_only: filled_count=0, same ticker-day ever_ready=0
- demoted_after_ready
- filled_from_watch_or_later_ready

### P1. KR entry timing gate

dedupe 후에도 첫 30분과 14:00 이후 손실이 크다.

추천:

- 09:00-09:30: confirmation 없으면 신규 진입 block 또는 micro/probe 이하
- 09:30-10:30: 상대적으로 허용 가능하지만 hard stop 빈도 높으므로 size/confirmation 유지
- 10:30-12:00: 단순 허용 금지, 추가 confirmation 필요
- 12:00-14:00: 중립 구간
- 14:00 이후: 신규 진입 강한 제한

### P1. KR modern action schema 점검

KR `modern_actions`가 30m/60m 모두 크게 부진하다. 이건 legacy `TRADE_READY` 전체 문제가 아니라 최근 action schema와 route 변환 문제일 수 있다.

점검 대상:

- `BUY_READY`와 `PROBE_READY`가 같은 execution 강도로 처리되는지
- `PULLBACK_WAIT`가 PathA trade_ready처럼 성과에 섞이는지
- `ADD_READY` shadow_only가 ready bucket에 섞이는지
- `route_final_action`, `route_reason`, `filled_count` 기준으로 실제 실행 가능 action만 분리되는지

### P1. US preopen 정책은 임시 보수 가드

US preopen은 dedupe 후 n=3, 평균 -0.755%, 승률 0%다. 나쁘지만 2026-04-27 하루에 집중되어 표본이 작다.

추천:

- 즉시 영구 hard block으로 확정하지 않는다.
- 임시로 09:30 ET 전 신규 진입은 block 또는 매우 높은 confirmation 필요 조건을 둔다.
- 09:45-10:30은 손익이 약하므로 size 0.5와 confirmation 유지.
- 10:30 이후는 현재 데이터상 가장 안정적이므로 정상 size 후보로 둔다.

### P2. KR PathB EXPIRED 재샘플링

KR EXPIRED 7건은 avg MFE30 +4.49%, zone reentry 71.4%, avg quotes 1.0이다. 가격 샘플이 한 번뿐인 상태에서 만료되는 문제가 의심된다.

개선:

- EXPIRED 전 quote refresh 횟수 증가
- market-open 이후 일정 시간 동안 재평가
- zone_reentered 이후 PlanB 재등록 여부를 shadow로 기록

### P2. KR strength_capture shadow

기존 리포트의 `CAUTIOUS/NEUTRAL & chg>=25 & vol>=20` 후보군은 여전히 흥미롭지만 표본이 작다. live 승격이 아니라 shadow lane이 맞다.

초기 조건:

- `consensus_mode in (CAUTIOUS, NEUTRAL)`
- `DEFENSIVE`, `CAUTIOUS_BEAR`에서는 gate override 금지
- `change_pct >= 25 and vol_ratio >= 20` 또는 `from_high_pct >= 10`
- 최소 10 labeled sessions 이상 관찰 후 검토

## 추가 분석이 필요한가

필요하다. 특히 정책 변경 전에는 아래 분석을 먼저 끝내야 한다.

1. lifecycle canonical performance table 생성 후 entry-band와 preopen 재집계
2. candidate audit pure bucket 재집계
3. latest candidate row에 outcome label이 붙지 않는 원인 수정
4. counterfactual outcome backfill
5. KR modern action 2026-05-07~2026-05-08 케이스별 리뷰
6. US preopen은 최소 10개 decision 이상 쌓일 때까지 임시 가드로만 운영

최종 판단은 다음과 같다. 수익률 개선을 위해 지금 바로 필요한 것은 새 전략 추가가 아니라, 실행 truth와 학습 truth를 연결하고, 중복/버킷 오염을 제거한 뒤 KR 시간대와 modern action route를 보수적으로 조정하는 것이다.
