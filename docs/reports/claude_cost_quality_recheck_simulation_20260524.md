# Claude 비용/품질 재검토 및 개선안 시뮬레이션 - 2026-05-24

## 범위

이 문서는 이전 Claude 개선 검토 내용을 로컬 근거로 재검토하고, 개선안별 비용/지연/품질 영향을 시뮬레이션한 최종 리포트입니다.

- 저장소: `E:\code\claudetrade`
- 작성 시점: 2026-05-24 KST
- 사용한 로컬 근거:
  - `data/audit/agent_call_events.db`
  - `logs/raw_calls/*.json`
  - `logs/hold_advisor/decisions_*.jsonl`
  - `data/audit/candidate_audit.db`
  - `logs/daily_judgment/live_*.json`
  - `.env.live`
  - `config/v2_start_config.json`
- 하지 않은 것:
  - broker/API 호출 없음
  - Claude 신규 호출 없음
  - 주문 실행 없음
  - live config 변경 없음

비용 단가는 Anthropic 공식 pricing 문서 기준으로 계산했습니다.

- 출처: `https://platform.claude.com/docs/en/about-claude/pricing`

| 모델 | Input | Output |
|---|---:|---:|
| Claude Sonnet 4.6 | $3 / MTok | $15 / MTok |
| Claude Haiku 4.5 | $1 / MTok | $5 / MTok |

계산식:

```text
estimated_cost = input_tokens / 1,000,000 * input_price
               + output_tokens / 1,000,000 * output_price
```

## 결론

이전 결론은 유지됩니다. 지금 바로 Claude selection을 더 공격적으로 바꾸는 것보다, 아래 순서가 더 안전하고 효과가 큽니다.

1. hold advisor 반복 호출을 줄인다.
2. Claude 판단, execution, risk, data 문제를 분리할 수 있도록 audit 연결성을 완성한다.
3. watch_only missed runup과 KR trade_ready 약화를 bucket별로 분해한다.
4. 모델 tiering은 live config 변경 전에 shadow/paper로 비교한다.
5. Claude health dashboard/preflight를 추가한다.

가장 큰 비용/지연 개선 포인트는 hold advisor입니다.

- 2026-05-11부터 2026-05-23까지 hold advisor Claude call은 1,742건입니다.
- 추정 Sonnet 비용은 $18.00입니다.
- 30분 TTL cache만 적용해도 hold advisor call/cost를 약 25.3% 줄일 수 있습니다.
- 30분 TTL cache에 저위험 single review를 더하면 Sonnet만 써도 약 38.4% 절감됩니다.
- 저위험 single review만 Haiku로 돌리는 shadow tiering까지 더하면 약 42.8% 절감됩니다.

가장 중요한 품질 개선 포인트는 audit completeness입니다.

| 필드 | 채워진 행 | 채움 비율 |
|---|---:|---:|
| `config_hash` | 0 / 30,485 | 0.0% |
| `execution_decision_id` | 28 / 30,485 | 0.09% |
| `execution_event_id` | 0 / 30,485 | 0.0% |
| `entry_timing_snapshot_json` | 0 / 30,485 | 0.0% |
| `post_open_features_json` | 0 / 30,485 | 0.0% |
| `scorer_input_snapshot_json` | 8,870 / 30,485 | 29.1% |
| `source_tags_json` | 16,912 / 30,485 | 55.5% |

이미 schema는 있지만 핵심 연결 필드가 비어 있습니다. 따라서 historical backfill만으로는 부족하고, runtime write path에서 새로 채워야 합니다.

## 2026-05-25 검토 반영

추가 검토 결과, cache/tiering을 바로 켜기 전에 관측 필드를 먼저 채우는 순서로 조정합니다.

- hold advisor 최근 3일 평균은 295 calls/day로 13일 평균 134 calls/day의 약 2.2배입니다. 이 증가는 절감 대상이기 전에 원인 분석 신호입니다.
- 2026-05-21 246회, 2026-05-22 369회, 2026-05-23 269회로 증가 구간이 확인되므로 position 수 증가, review reason 변화, 자동 sell review 조건 변화를 먼저 분리합니다.
- `duration_ms`가 비어 있으면 cache가 비용만 줄였는지, 지연까지 줄였는지 측정할 수 없습니다. Phase 1a는 hold advisor raw call/event/decision JSONL duration logging입니다.
- audit linkage는 P0/P1로 격상합니다. 우선 runtime write path에서 `config_hash`, `execution_decision_id`, `entry_timing_snapshot_json`을 채우고, `execution_event_id`, `post_open_features_json`은 가능한 경로부터 보강합니다.
- KR watch_only blocked ratio 98%는 gate 완화 근거가 아닙니다. 같은 구간의 KR trade_ready가 n=4이고 3일 평균이 -2.826%라면 gate를 열수록 악화될 수 있습니다.
- `select_tickers` 최근 20건 input 평균은 10.67k tokens로 전체 평균 8.53k 대비 약 25.1% 높습니다. KR prompt cap 상향, evidence pack 크기, prompt pool 크기를 별도 추적합니다.

따라서 실행 순서는 `duration_ms 로깅 -> audit linkage runtime write -> watch_only bucket report -> TTL cache -> shadow tiering`으로 둡니다.

## 재검토 보정 사항

초기 분석은 방향성은 맞았지만, 일부 수치는 더 정확히 보정했습니다.

| 항목 | 재검토 결과 |
|---|---:|
| 최근 500개 Claude call 중 hold advisor | 430 / 500 |
| 2026-05-23 hold advisor call | 269 |
| hold advisor 누적 call | 1,742 |
| hold advisor 추정 비용 | $18.00 |
| `select_tickers` 누적 call | 306 |
| `select_tickers` 추정 비용 | $15.71 |
| `select_tickers` parse error | 2 / 306, 0.65% |
| `select_tickers` 전체 평균 token | input 8.53k, output 1.72k |
| `select_tickers` 최근 20건 평균 token | input 10.67k, output 1.73k |

현재 live 설정도 비용이 높은 방향입니다.

| 설정 | `.env.live` | `config/v2_start_config.json` |
|---|---|---|
| `BULL_R1_MODEL` | `claude-sonnet-4-6` | `claude-sonnet-4-6` |
| `BEAR_R1_MODEL` | `claude-sonnet-4-6` | `claude-sonnet-4-6` |
| `NEUTRAL_R1_MODEL` | `claude-sonnet-4-6` | `claude-sonnet-4-6` |
| `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS` | `true` | `true` |
| `PATHB_KR_LIVE_ENABLED` | `true` | `true` |
| `PATHB_US_LIVE_ENABLED` | `true` | `true` |

## Claude 호출 비용 근거

`data/audit/agent_call_events.db` 기준, 2026-05-11부터 2026-05-23까지:

| 그룹 | Calls | Input tokens | Output tokens | Sonnet 추정 비용 | Parse errors |
|---|---:|---:|---:|---:|---:|
| hold advisor bull/bear/neutral | 1,742 | 2,564,920 | 687,054 | $18.00 | 0 |
| `select_tickers` | 306 | 2,610,460 | 525,185 | $15.71 | 2 |
| analyst R1 bull/bear/neutral | 207 | 1,175,162 | 47,927 | $4.24 | 0 |
| analyst R2 bull/bear/neutral | 207 | 540,906 | 59,421 | $2.51 | 0 |

개별 label 상위:

| Label | Calls | Sonnet 추정 비용 |
|---|---:|---:|
| `hold_advisor_bull` | 582 | $6.03 |
| `hold_advisor_bear` | 581 | $5.96 |
| `hold_advisor_neutral` | 579 | $6.01 |
| `select_tickers` | 306 | $15.71 |
| `analyst_bull_r1` | 69 | $1.40 |
| `analyst_bear_r1` | 69 | $1.44 |
| `analyst_neutral_r1` | 69 | $1.41 |

최근 일자별 concentration:

| 날짜 | Hold advisor calls | Hold advisor 비용 | `select_tickers` calls | `select_tickers` 비용 |
|---|---:|---:|---:|---:|
| 2026-05-21 | 246 | $2.61 | 31 | $1.66 |
| 2026-05-22 | 369 | $3.94 | 40 | $2.33 |
| 2026-05-23 | 269 | $2.85 | 12 | $0.71 |

해석:

- selection은 1회당 비용이 크지만 호출 수와 parse 품질이 안정적입니다.
- hold advisor는 1회당 비용은 작지만 3-vote 반복 호출이 많아 최근 비용/지연의 주요 누수입니다.
- event store의 hold advisor `duration_ms`가 비어 있어 지연 시간은 실제 ms가 아니라 call count proxy로만 비교했습니다.

## Hold Advisor 요청 단위 근거

request-level 시뮬레이션은 `logs/hold_advisor/decisions_*.jsonl`을 사용했습니다. event store 범위와 맞추기 위해 2026-05-11부터 2026-05-23까지만 보고, 명백한 `TEST` row는 제외했습니다.

| 항목 | 값 |
|---|---:|
| request rows | 588 |
| raw Claude calls | 1,742 |
| request당 관측 call 수 | 2.96 |
| HOLD decisions | 477 |
| SELL decisions | 111 |

stage별 분포:

| Stage | Requests | HOLD | SELL |
|---|---:|---:|---:|
| `INTRADAY_REVIEW` | 372 | 355 | 17 |
| `AUTO_SELL_REVIEW` | 150 | 85 | 65 |
| `PRE_CLOSE_CARRY` | 59 | 31 | 28 |
| `MANUAL_REVIEW` | 7 | 6 | 1 |

이 분포를 보면 cache는 무조건 넓히면 안 됩니다. 안전한 cache 범위는 반복되는 soft HOLD review입니다.

cache 금지 대상:

- `PRE_CLOSE_CARRY`
- `MANUAL_REVIEW`
- SELL decision
- catastrophic exit
- broker-truth exit
- operator kill/manual safety path
- hard stop, hard loss cap, quarantine 관련 path

## Hold Advisor 최적화 시뮬레이션

### TTL cache rule

시뮬레이션 cache key:

```text
(market, ticker, decision_stage, round((current - entry) / entry / 0.5%))
```

cache eligible:

- decision이 `HOLD`
- `INTRADAY_REVIEW`, `AUTO_SELL_REVIEW` 등 soft intraday review
- 같은 종목, 같은 stage, 비슷한 가격 bucket
- `PRE_CLOSE_CARRY`, `MANUAL_REVIEW`, SELL decision은 제외

TTL별 결과:

| TTL | cache hits | hit rate | 주요 hit stage |
|---|---:|---:|---|
| 10분 | 68 / 588 | 11.6% | `AUTO_SELL_REVIEW`, `INTRADAY_REVIEW` |
| 15분 | 114 / 588 | 19.4% | `INTRADAY_REVIEW`, `AUTO_SELL_REVIEW` |
| 30분 | 149 / 588 | 25.3% | `INTRADAY_REVIEW`, `AUTO_SELL_REVIEW` |
| 45분 | 174 / 588 | 29.6% | `INTRADAY_REVIEW`, `AUTO_SELL_REVIEW` |
| 60분 | 179 / 588 | 30.4% | `INTRADAY_REVIEW`, `AUTO_SELL_REVIEW` |

1차 적용 추천은 30분 TTL입니다.

- 15분보다 절감 효과가 의미 있게 큽니다.
- 45분/60분 대비 추가 절감은 크지 않은데, intraday 상태 변화 리스크는 늘어납니다.
- 따라서 첫 live rollout은 30분 TTL, soft HOLD only가 가장 균형적입니다.

### 비용/호출 수 비교

baseline:

- 1,742 hold advisor Claude calls
- Sonnet 추정 비용 $18.00
- 평균 call 비용 약 $0.0103
- 평균 request 비용 약 $0.0306

| Scenario | 총 model calls | Sonnet calls | Haiku calls | 추정 비용 | 절감액 | 절감률 |
|---|---:|---:|---:|---:|---:|---:|
| 현재 3-vote Sonnet | 1,742.0 | 1,742.0 | 0 | $18.00 | $0.00 | 0.0% |
| TTL 15분 cache only | 1,404.3 | 1,404.3 | 0 | $14.51 | $3.49 | 19.4% |
| TTL 30분 cache only | 1,300.6 | 1,300.6 | 0 | $13.44 | $4.56 | 25.3% |
| TTL 60분 cache only | 1,211.7 | 1,211.7 | 0 | $12.52 | $5.48 | 30.4% |
| TTL 30분 + 저위험 single Sonnet | 1,072.9 | 1,072.9 | 0 | $11.09 | $6.91 | 38.4% |
| TTL 30분 + 저위험 single Haiku | 1,072.9 | 956.9 | 116 | $10.29 | $7.71 | 42.8% |
| 전체 hold advisor Haiku, call reduction 없음 | 1,742.0 | 0 | 1,742 | $6.00 | $12.00 | 66.7% |

저위험 single review 조건:

```text
decision_stage == INTRADAY_REVIEW
decision == HOLD
-1.0% <= pnl_pct <= +2.0%
TTL cache hit이 아닌 request
```

해당 조건은 116 request에 매칭되었습니다.

### 안전성 판단

`전체 hold advisor Haiku`는 비용상으로는 가장 큽니다. 하지만 고위험 청산 판단까지 모델 품질을 바꾸므로 첫 live 변경으로는 부적합합니다.

권장 순서:

1. 먼저 측정만 추가한다.
2. 반복 soft HOLD에만 30분 TTL cache를 적용한다.
3. 저위험 intraday HOLD에만 single neutral review를 shadow/paper로 비교한다.
4. Haiku는 저위험 single review에만 shadow로 붙인다.
5. 고위험, 큰 손실, 큰 이익 반납, pre-close carry, manual review, conflicting evidence는 3-vote Sonnet 유지한다.

## 모델 Tiering 시뮬레이션

현재 R1은 bull/bear/neutral 모두 Sonnet입니다.

시뮬레이션 조건:

- selection은 Sonnet 유지
- R2는 Sonnet 유지
- bull R1은 Sonnet 유지
- bear R1과 neutral R1만 Haiku로 가정

| Scenario | R1 추정 비용 | 절감액 | 절감률 |
|---|---:|---:|---:|
| 현재 R1 전부 Sonnet | $4.24 | $0.00 | 0.0% |
| bear/neutral R1 Haiku, bull R1 Sonnet | $2.35 | $1.90 | 44.7% |

해석:

- R1 tiering은 절감률은 크지만 절대 금액은 hold advisor보다 작습니다.
- live config 변경이므로 운영자 승인 없이 바꾸면 안 됩니다.
- 먼저 shadow로 stance/confidence drift를 비교해야 합니다.
- selection은 parse 안정성과 Path A/Path B 영향 범위 때문에 Sonnet 유지가 맞습니다.

## Audit Completeness 시뮬레이션

`data/audit/candidate_audit.db`에는 30,485개 candidate row가 있습니다.

핵심 필드 상태:

| Field | Filled rows | Filled pct | 해석 |
|---|---:|---:|---|
| `config_hash` | 0 | 0.0% | config 변경 영향 분리가 불가능합니다. |
| `feature_flags_json` | 0 | 0.0% | config_hash backfill 소스가 없습니다. |
| `execution_decision_id` | 28 | 0.09% | 실행 연결이 극히 일부만 있습니다. |
| `execution_event_id` | 0 | 0.0% | fill/close event 직접 연결이 없습니다. |
| `entry_timing_snapshot_json` | 0 | 0.0% | Claude가 맞았지만 entry timing이 나빴는지 분리할 수 없습니다. |
| `post_open_features_json` | 0 | 0.0% | 장초반/장중 컨텍스트 영향 분리가 어렵습니다. |
| `scorer_input_snapshot_json` | 8,870 | 29.1% | 최근 scorer snapshot은 개선 중입니다. |
| `candidate_quality_score` | 3,745 | 12.3% | quality scoring은 있지만 coverage가 낮습니다. |
| `quality_data_gaps_json` | 0 | 0.0% | missing data 원인을 구조화하지 못합니다. |
| `source_tags_json` | 16,912 | 55.5% | source provenance는 일부 있습니다. |

현재 row만으로 가능한 backfill:

| Backfill target | Potential rows | 판단 |
|---|---:|---|
| `execution_event_id` from `execution_decision_id` | 28 | 가능하지만 영향이 작습니다. |
| distinct `execution_decision_id` | 16 | 16개 모두 lifecycle event와 match됩니다. |
| `config_hash` from `feature_flags_json` | 0 | 새 instrumentation 필요 |
| `entry_timing_snapshot_json` from existing timing fields | 0 | 새 instrumentation 필요 |
| `post_open_features_json` from existing health snapshot | 0 | 새 instrumentation 필요 |

결론:

- historical backfill은 execution link 일부만 복구할 수 있습니다.
- 핵심은 runtime 저장 시점에 필드를 채우는 것입니다.
- 이 작업은 P1입니다. 이게 없으면 Claude selection 문제인지, timing 문제인지, risk 문제인지 계속 섞입니다.

## Watch-Only / Trade-Ready 품질 재검토

최근 daily judgment는 watch_only missed runup과 KR trade_ready 약화를 동시에 보여줍니다.

기간: 2026-05-15부터 2026-05-22까지

| Market | Sessions | High watch_only missed runup | High watch_only blocked ratio | Weak trade_ready forward |
|---|---:|---:|---:|---:|
| KR | 6 | 6 | 6 | 6 |
| US | 6 | 6 | 6 | 0 |

최근 3일 예시:

| Date | Market | Watch-only missed runup ratio | Watch-only blocked ratio | trade_ready 3d avg | trade_ready n |
|---|---|---:|---:|---:|---:|
| 2026-05-20 | KR | 58.7% | 98.7% | -0.533% | 16 |
| 2026-05-21 | KR | 57.8% | 98.7% | -1.883% | 8 |
| 2026-05-22 | KR | 56.7% | 98.3% | -2.826% | 4 |
| 2026-05-20 | US | 35.1% | 93.6% | +0.863% | 6 |
| 2026-05-21 | US | 36.0% | 93.5% | +0.863% | 6 |
| 2026-05-22 | US | 38.7% | 93.2% | n/a | 0 |

candidate audit 30m/60m outcome 일부:

| Horizon | Market | Bucket | Rows | Non-null return rows | Avg return | Avg max runup | Positive return rate |
|---|---|---|---:|---:|---:|---:|---:|
| 30m | KR | watch_only | 744 | 128 | +0.991% | +1.786% | 39.1% |
| 30m | KR | not_selected | 2,388 | 469 | +0.775% | +1.594% | 36.9% |
| 30m | US | watch_only | 1,030 | 260 | +0.394% | +0.612% | 43.1% |
| 30m | US | trade_ready | 46 | 19 | +0.150% | +0.419% | 42.1% |
| 60m | KR | watch_only | 744 | 129 | +1.607% | +2.882% | 48.8% |
| 60m | KR | not_selected | 2,388 | 461 | +0.826% | +2.276% | 41.2% |
| 60m | US | watch_only | 1,030 | 215 | +0.713% | +1.124% | 65.6% |
| 60m | US | trade_ready | 46 | 18 | +0.373% | +0.724% | 66.7% |

주의:

- 30m/60m outcome coverage는 부분적입니다.
- 3d forward metric에서는 KR trade_ready 약화가 뚜렷합니다.
- 따라서 watch_only missed runup만 보고 gate를 풀면 위험합니다.

필수 bucket decomposition:

| Bucket | 봐야 할 질문 |
|---|---|
| Claude selection 문제 | Claude가 좋은 근거를 보고도 watch_only로 뒀는가? |
| candidate pool 문제 | 좋은 종목이 Claude prompt 안에 있었는가, screener에만 있었는가? |
| evidence pack 문제 | missing/partial evidence 때문에 action ceiling이 WATCH였는가? |
| action routing 문제 | valid trade_ready가 route policy에서 demote됐는가? |
| timing 문제 | 실제 entry window 이전/이후에만 좋은 종목이었는가? |
| risk/affordability 문제 | 현금, size, same-day reentry, risk block이 정당했는가? |
| PathB 문제 | zone hit 자체가 없었는가, zone-hit execution이 실패했는가? |

## Claude Health Dashboard / Preflight 제안

이 작업은 P2지만 운영 안정성에 직접 도움이 됩니다.

추가할 KPI:

| KPI | 목적 |
|---|---|
| calls by label/model/day | hold advisor나 tuner 호출 폭증 탐지 |
| estimated cost by label/day | 비용 누수 즉시 확인 |
| parse error rate | schema/response drift 탐지 |
| `max_tokens` 또는 truncated response count | 숨은 품질 저하 탐지 |
| compact schema validation failures | selection contract 파손 탐지 |
| hold advisor cache hit/miss/bypass | cache가 위험을 숨기지 않는지 검증 |
| hold advisor fallback HOLD/SELL count | safety default 추적 |
| active lesson injected IDs/chars | 어떤 lesson이 prompt에 들어갔는지 확인 |
| audit completeness pct | 재현성 필드 누락 탐지 |
| missing price target demotion count | PathB plan 품질 저하 탐지 |

## 최종 우선순위

| 우선순위 | 작업 | 기대 효과 | 리스크 | 권장 |
|---:|---|---|---|---|
| 1 | hold advisor call audit + TTL cache | cache만으로 19-30% call/cost 절감 | 낮음 | 관측 먼저, 이후 30분 TTL 적용 |
| 2 | hold advisor 저위험 triage | cache + single review로 38-43% 절감 | 중간 | shadow/paper 먼저 |
| 3 | audit linkage instrumentation | 품질/실행/리스크 분리 가능 | 낮음 | P1로 진행 |
| 4 | watch_only/trade_ready bucket report | unsafe gate 완화 방지 | 낮음 | gate 변경 전 필수 |
| 5 | R1 model tiering | R1 비용 44.7% 절감 | 중간 | bear/neutral Haiku shadow 후 승인 |
| 6 | Claude health dashboard/preflight | 운영 디버깅 속도 개선 | 낮음 | P2로 추가 |

## 권장 실행 계획

### Phase 1 - 관측만 추가

live behavior 변경 없음.

- Phase 1a: hold advisor `_ask_one` raw call과 request-level decision JSONL에 `duration_ms` 기록
- Phase 1b: candidate audit runtime write path에서 `config_hash`, `execution_decision_id`, `entry_timing_snapshot_json` 기록
- Phase 1c: KR watch_only blocked/missed-runup bucket decomposition report 추가
- hold advisor call을 date, market, symbol, stage, model, decision, reason별로 집계
- `duration_ms` p50/p95를 bull/bear/neutral, stage, market별로 집계해 TTL cache key 설계에 반영
- cache bypass reason 기록
- near-close, manual review, SELL decision, hard/broker-truth exit, price-bucket mismatch를 별도 카운트
- `config_hash`, `execution_decision_id`, `execution_event_id`, `entry_timing_snapshot_json`, `post_open_features_json` completeness check 추가
- `select_tickers` prompt token rolling average와 prompt cap 변경 이력 추적
- watch_only/trade_ready bucket report 추가

### Phase 2 - 보수적 최적화

작은 live behavior 변경.

- 반복 soft HOLD decision에만 30분 TTL cache 적용
- cache key에는 market, ticker, decision stage, entry/current price bucket, 가능하면 broker position identity 포함
- catastrophic, broker-truth, operator-kill, hard stop, pre-close carry, manual review, SELL decision은 절대 cache하지 않음

### Phase 3 - Shadow tiering / triage

즉시 live 모델 downgrade 금지.

- 저위험 single neutral review를 shadow로 실행
- 현재 3-vote Sonnet action과 shadow action 비교
- bear/neutral R1 Haiku shadow 실행
- unsafe HOLD 증가나 bad SELL 증가가 없을 때만 승격 검토

### Phase 4 - 품질 bucket별 수정

전역 gate 완화 금지.

- missed runup의 원인이 evidence ceiling이면 evidence pack 개선
- routing demotion이면 route logic을 shadow로 조정
- timing 문제면 entry timing/post-open snapshot과 entry window 개선
- KR trade_ready 약화가 유지되면 KR full-size ready를 줄이고 PathB wait/probe로 이동

## 최종 판단

가장 먼저 할 일은 hold advisor `duration_ms` 로깅과 audit linkage runtime write입니다. 이 두 작업이 붙어야 TTL cache, triage, model tiering의 비용/지연/품질 효과를 실측할 수 있습니다.

watch_only missed runup이 높다는 이유만으로 Claude selection을 더 공격적으로 만드는 것은 위험합니다. 같은 로그에서 KR trade_ready forward 약화도 확인되므로, 먼저 bucket별 counterfactual 분석을 만들고 좁은 범위에서만 정책을 바꾸는 것이 맞습니다.
