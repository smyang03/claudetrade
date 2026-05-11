# 후보군 품질 트레이너 개발 요구서

작성일: 2026-05-12
상태: 구현 전 요구서
범위: 후보 수집, 후보 점수화, Claude 프롬프트 상단 후보 선정, PlanA/PlanB/watch 후보 분리

## 1. 핵심 결론

후보군 개선의 방향은 "후보를 적게 뽑기"가 아니다.

정확한 방향은 다음이다.

```text
넓게 수집한다.
엄격하게 점수화한다.
Claude에게 보여줄 상단 후보만 강하게 선별한다.
PlanA 즉시 후보는 더 좁힌다.
좋지만 비싼 후보는 PlanB/watch로 남긴다.
```

현재 시스템은 움직이는 종목을 찾는 능력은 있다. 약한 지점은 "좋은 후보가 Claude 입력 상단에 안정적으로 들어가는가", "즉시 매수 후보와 대기 후보가 후보 단계에서 분리되는가"이다.

따라서 이번 개발의 목적은 매수 빈도를 늘리는 것이 아니라, 후보 상단의 수익 기대값 밀도를 높이는 것이다.

## 2. 근거 요약

분석 기준:

- `data/audit/candidate_audit.db`
- `data/v2_event_store.db`
- `docs/reports/candidate_improvement_simulation_20260511_181703.md`
- `docs/reports/full_profitability_review_20260511_revalidation.md`
- `docs/reports/live_improvement_simulation_claude_led_no_replacement_20260512.md`
- `docs/reports/market_policy_review_post_impl_20260510.md`

주요 관찰:

| 항목 | 관찰 |
|---|---|
| 프롬프트 가시성 | raw 후보는 평균 43~60개였지만 실제 Claude 입력은 25~28개 수준 |
| 상단 누락 | top30 후보가 거의 매번 누락, 평균 8~10개 수준 |
| PlanA | 후보 등장 직후 매수 가정이 약함. 특히 KR이 부진 |
| PlanB | 즉시 매수보다는 가격 discipline이 있는 대기 후보로 의미 있음 |
| KR | KOSDAQ 쪽이 KOSPI보다 유리. 일반 KOSPI momentum은 약함 |
| US | high-liquidity, momentum_now, opening_range_pullback 쪽이 상대적으로 유리 |
| 감사 데이터 | source, freshness, trainer tier, 실제 prompt 포함 여부가 부족하거나 누락됨 |

## 3. 현재 문제 정의

현재 후보 레이어는 서로 다른 네 가지 결정을 섞어서 처리한다.

1. 어떤 종목을 넓게 발견할 것인가.
2. 어떤 종목을 Claude에게 보여줄 것인가.
3. 어떤 종목을 PlanA 즉시 후보로 볼 것인가.
4. 어떤 종목을 PlanB/watch 후보로 대기시킬 것인가.

이 네 가지가 섞이면 다음 문제가 생긴다.

- 좋은 종목이 raw 후보에는 있었지만 Claude 프롬프트 상단에 못 들어간다.
- 이미 너무 오른 종목이 강한 종목이라는 이유로 PlanA 즉시 후보가 된다.
- PlanB로 기다려야 할 종목이 즉시 매수 후보처럼 취급된다.
- 후보 교체가 기대값이 아니라 단순 스크리너 점수나 최신성에 끌린다.

## 4. 설계 원칙

1. 후보 수집은 recall 중심으로 넓게 유지한다.
2. Claude 프롬프트 상단은 precision 중심으로 엄격하게 제한한다.
3. 프롬프트 포함은 "검토 권한"이지 "매수 권한"이 아니다.
4. PlanA는 가장 좁은 후보군이어야 한다.
5. PlanB/watch는 실패 상태가 아니라 좋은 후보를 비싸게 사지 않기 위한 상태다.
6. forward return 라벨은 학습/평가용이고 live gate 입력으로 쓰면 안 된다.
7. KR과 US는 반드시 다른 prior와 임계값을 사용한다.
8. 새 후보가 기존 후보를 교체하려면 기대수익뿐 아니라 예상 낙폭도 더 좋아야 한다.

## 5. 범위와 비범위

### 개발 범위

- 후보 feature 정규화
- 후보 source/cohort 기록 보강
- 실제 Claude prompt 포함 ticker 저장
- 후보 품질 점수 `candidate_quality_score` 산출
- Claude prompt pool 재정렬
- PlanA/PlanB/watch/bench/quarantine 후보 분리
- 후보 교체 정책 개선
- shadow 리포트와 대시보드 표시

### 제외 범위

- 매도 로직 변경
- 브로커 주문 로직 변경
- 새 외부 데이터 소스 추가
- Claude를 제거하거나 대체
- 후보 수 증가를 매수 수 증가로 연결
- 미래 수익률 라벨을 live 의사결정에 직접 사용

## 6. 목표 상태

목표 구조:

```text
Discovery Pool
  스크리너, 장전, PathB, watch trigger, theme/sector, 기존 상태 후보를 넓게 수집

Scored Pool
  live-known feature와 cohort prior로 점수화

Prompt Pool
  Claude에게 실제로 보여줄 상위 25~36개 후보

Action Candidate Pool
  PlanA / PlanB / Watch / Bench / Quarantine으로 분리
```

핵심 목표:

- 좋은 후보가 Claude 상단에 들어가는 비율을 높인다.
- PlanA 후보의 품질을 높인다.
- 좋은데 비싼 후보는 삭제하지 않고 PlanB/watch로 보존한다.
- 후보 교체 로그를 재현 가능하게 만든다.
- 어떤 후보가 왜 상단에서 빠졌는지 확인 가능하게 만든다.

## 7. 기능 요구사항

### R1. 실제 Claude 프롬프트 포함 후보 저장

Claude 호출마다 실제 프롬프트에 들어간 ticker와 순서를 저장해야 한다.

필수 필드:

| 필드 | 설명 |
|---|---|
| `prompt_id` | Claude 호출 식별자 |
| `market` | KR/US |
| `session_date` | 세션 날짜 |
| `cycle_at` | 후보 사이클 시각 |
| `ticker` | 종목 |
| `raw_rank` | raw 후보 순위 |
| `score_rank` | trainer 점수 순위 |
| `prompt_rank` | 실제 Claude 입력 순위 |
| `included_in_prompt` | 실제 포함 여부 |
| `excluded_reason` | 제외 사유 |
| `candidate_quality_score` | 당시 후보 품질 점수 |
| `known_at` | live-known 시각 |

완료 기준:

- 모든 Claude 선택 호출의 실제 prompt ticker를 재구성할 수 있어야 한다.
- 상단 후보가 빠졌다면 제외 사유가 100% 남아야 한다.
- raw 후보 수, scored 후보 수, prompt 후보 수, omitted top30 수를 리포트할 수 있어야 한다.

### R2. 후보 feature 정규화

Scored Pool에 들어가는 모든 후보는 같은 feature pack을 가져야 한다.

필수 feature:

| feature | 설명 |
|---|---|
| `source` | screener/preopen/pathb/watch_trigger 등 |
| `primary_bucket` | momentum_now, liquidity_leader, gap_pullback 등 |
| `market_type` | KR KOSDAQ/KOSPI |
| `liquidity_bucket` | high/mid/low/unknown |
| `change_pct` | 현재 등락률 |
| `change_bin` | 0~3, 3~7, 7~15, 15+ 등 |
| `from_high_pct` | 고점 대비 위치 |
| `candidate_detected_at` | 최초 포착 시각 |
| `candidate_age_min` | 후보 나이 |
| `price_change_since_first_seen_pct` | 최초 포착 이후 추격률 |
| `post_open_confirm` | OR/VWAP/volume 확인 |
| `trainer_cohort_key` | cohort key |
| `trainer_tier` | CORE/WATCH/PROBATION/BENCH/QUARANTINE |
| `data_quality_flags` | 결측, stale, partial history 등 |

완료 기준:

- 결측값을 0처럼 쓰지 않고 `unknown` 또는 명시적 null로 남긴다.
- source, bucket, liquidity, market type, age 기준으로 후보 성과를 집계할 수 있다.
- prompt builder가 metadata를 조용히 잃어버리지 않는다.

### R3. offline 후보 품질 라벨 생성

후보 품질 평가는 30/60분 후 결과와 MFE/MAE로 계산한다.

이 라벨은 offline 학습/평가용이다. live scoring 함수가 직접 읽으면 안 된다.

초기 라벨:

```text
good_candidate =
  ret60_pct >= +0.5
  and mae60_pct > -2.0
  and mfe60_pct >= +1.0

bad_candidate =
  ret30_pct <= -1.0
  or mae60_pct <= -3.0
  or (mfe60_pct < +0.5 and ret60_pct < 0)
```

완료 기준:

- prompt rank 구간별 good/bad 후보 비율을 볼 수 있다.
- 선택 후보, 미선택 후보, prompt 누락 후보를 비교할 수 있다.
- 라벨 생성 시 `known_at` 기준이 지켜진다.

### R4. 후보 품질 점수 산출

`candidate_quality_score`를 추가한다.

초기 점수 구조:

```text
candidate_quality_score =
  market_bucket_prior
  + cohort_forward_edge
  + liquidity_score
  + freshness_score
  + confirmation_score
  + source_reliability_score
  - chase_penalty
  - drawdown_risk_penalty
  - stale_penalty
  - data_quality_penalty
```

점수는 하나로 끝내지 말고 목적별로 나눈다.

| score | 목적 |
|---|---|
| `prompt_score` | Claude에게 보여줄 가치 |
| `plan_a_score` | 즉시 후보 가능성 |
| `pathb_wait_score` | PlanB/watch 대기 가치 |
| `risk_score` | 낙폭, stale, chase 위험 |

완료 기준:

- 같은 입력이면 같은 점수가 나온다.
- 점수 구성요소를 audit에서 볼 수 있다.
- 기존 prompt 순서와 trainer 제안 순서를 shadow 비교할 수 있다.

### R5. KR/US 시장별 prior 분리

KR 초기 prior:

| 구간 | 정책 |
|---|---|
| KOSDAQ | 가중치 상향 |
| KOSPI 일반 momentum | 확인 없으면 감점 |
| 0~7% 상승 | 우선 검토 |
| 7~15% 상승 | fresh confirmation 없으면 PlanB/watch |
| 15%+ 상승 | chase penalty 강하게 적용 |
| low-liquidity ignition | 일반 PlanA 금지, 별도 small-probe/watch 검토 |
| stale 후보 | PlanA 강등 |

US 초기 prior:

| 구간 | 정책 |
|---|---|
| high liquidity | 가중치 상향 |
| momentum_now | 가중치 상향 |
| opening_range_pullback | confirmation 있으면 가중치 상향 |
| 3~15% 상승 | 선호 momentum 구간 |
| 15%+ 상승 | 뉴스/거래대금/fresh confirmation 없으면 감점 |
| mid liquidity | cohort 검증 전 기본 감점 |
| unclassified | source attribution 개선 전 감점 |

완료 기준:

- KR/US 점수와 임계값을 독립적으로 조정할 수 있다.
- 리포트가 KR KOSDAQ/KOSPI, US liquidity 성과를 분리한다.
- 전역 임계값 하나로 양 시장을 동시에 바꾸지 않는다.

### R6. Claude Prompt Pool Builder

Scored Pool을 받아서 실제 Claude 입력 후보를 만든다.

초기 cap:

| 시장 | target | hard cap |
|---|---:|---:|
| KR | 30 | 36 |
| US | 30 | 36 |

규칙:

- active holding 또는 CORE 후보는 Claude 재검토가 필요할 때 우선 포함한다.
- 그 외에는 `prompt_score` 기준 상위 후보를 포함한다.
- high-upside지만 위험한 후보는 PlanA가 아니라 watch candidate로 제한적으로 포함한다.
- quarantine 후보는 일반 prompt에 넣지 않는다.
- cap 때문에 빠진 후보는 제외 사유를 저장한다.

완료 기준:

- 실제 prompt 수와 cap 동작이 일치한다.
- top score 후보가 빠진 이유를 재현할 수 있다.
- cap 확장은 매수 권한 증가가 아니라 가시성 증가로만 동작한다.

### R7. PlanA/PlanB/watch 분리

후보 단계에서 다음 상태를 나눠야 한다.

```text
PlanA:
  plan_a_score 높음
  risk_score 낮음
  fresh
  chase 아님
  시장별 positive prior 존재

PlanB:
  prompt_score 또는 pathb_wait_score는 높지만
  현재가가 비싸거나 zone discipline이 필요한 후보

Watch:
  흥미는 있지만 confirmation, freshness, data quality가 부족한 후보

Bench:
  reserve 후보

Quarantine:
  당일 stop, failed ready, hard block, severe stale/chase, 데이터 오염 후보
```

완료 기준:

- Claude에게 보여줄 수는 있지만 PlanA가 아닌 후보를 표현할 수 있다.
- 즉시 진입은 안 되지만 PlanB/watch 가치가 있는 후보를 보존할 수 있다.
- discovery 수가 늘어도 PlanA 수가 자동 증가하지 않는다.

### R8. 후보 교체 정책 개선

새 후보가 기존 후보를 교체하려면 기대값과 위험이 모두 좋아야 한다.

초기 규칙:

```text
replace 가능 조건:
  incoming.prompt_score - outgoing.prompt_score >= min_delta
  and incoming.risk_score <= outgoing.risk_score + risk_tolerance
  and incoming not stale/quarantine
  and outgoing not CORE/held/strong_ready
```

완료 기준:

- CORE 후보는 단순 순위 변동으로 제거되지 않는다.
- 교체 로그에 incoming/outgoing 점수, delta, risk 비교가 남는다.
- 당일 손실/failed ready 후보는 명시적 회복 정책 없이는 재진입하지 않는다.

### R9. source/cohort 신뢰도 관리

후보 트레이너는 source와 cohort별 성과를 관리해야 한다.

cohort 차원:

- market
- source
- primary_bucket
- market_type
- liquidity_bucket
- change_bin
- freshness_bin
- from_high_bin
- session_phase

지표:

- 표본 수
- ret30 평균/중앙값
- ret60 평균/중앙값
- good label 비율
- bad label 비율
- MFE/MAE
- positive sum / negative sum 비율

완료 기준:

- 표본이 부족한 cohort는 hard rule이 아니라 soft prior로만 사용한다.
- 모든 추천에는 표본 수가 같이 표시된다.

### R10. Shadow mode 우선 적용

첫 버전은 live 행동을 바꾸지 않고 shadow로 돌린다.

shadow mode:

- trainer score 계산
- 제안 prompt 순서 생성
- 기존 prompt 순서와 비교
- 누락 winner, 승격 loser 기록
- feature flag 켜기 전에는 live prompt/order 변경 없음

완료 기준:

- 최소 3~5 세션 shadow 결과 확보
- trainer 제안 top30의 good-label density가 기존 top30보다 나아야 함
- promoted loser 비율이 기존보다 악화되지 않아야 함

## 8. 데이터 저장 요구사항

신규 또는 확장 테이블:

```text
candidate_prompt_audit
```

권장 컬럼:

```text
id
runtime_mode
market
session_date
prompt_id
cycle_at
known_at
ticker
candidate_key
raw_rank
score_rank
prompt_rank
included_in_prompt
excluded_reason
candidate_quality_score
prompt_score
plan_a_score
pathb_wait_score
risk_score
score_components_json
source
source_tags_json
primary_bucket
market_type
liquidity_bucket
change_pct
change_bin
from_high_pct
candidate_detected_at
candidate_age_min
price_change_since_first_seen_pct
trainer_tier
trainer_cohort_key
data_quality_flags_json
created_at
```

offline label 저장은 기존 `audit_candidate_outcomes`를 우선 활용한다. 부족하면 `candidate_quality_labels` 파생 테이블 또는 daily export를 만든다.

## 9. 설정 요구사항

초기 flag:

```text
CANDIDATE_QUALITY_TRAINER_ENABLED=false
CANDIDATE_QUALITY_TRAINER_SHADOW=true
CANDIDATE_PROMPT_POOL_REORDER_ENABLED=false
CANDIDATE_PROMPT_POOL_TARGET_KR=30
CANDIDATE_PROMPT_POOL_TARGET_US=30
CANDIDATE_PROMPT_POOL_HARD_CAP_KR=36
CANDIDATE_PROMPT_POOL_HARD_CAP_US=36
CANDIDATE_PLAN_A_SCORE_MIN_KR=<shadow-default>
CANDIDATE_PLAN_A_SCORE_MIN_US=<shadow-default>
CANDIDATE_REPLACEMENT_MIN_DELTA_KR=<shadow-default>
CANDIDATE_REPLACEMENT_MIN_DELTA_US=<shadow-default>
```

운영 원칙:

- shadow 기본값은 true.
- prompt reorder는 별도 flag.
- PlanA 임계값은 prompt reorder와 별도 flag.
- startup 시 effective config를 로그로 남긴다.

## 10. 리포트 요구사항

일일 후보 품질 리포트를 만든다.

필수 섹션:

1. discovery/scored/prompt 후보 수
2. prompt top10/top20/top30 good/bad 비율
3. 기존 prompt 순서 vs trainer 제안 순서
4. 상단에서 빠진 후보와 사후 성과
5. 상단으로 승격된 후보와 사후 성과
6. source/cohort 성과
7. KR KOSDAQ/KOSPI 분리 성과
8. US liquidity 분리 성과
9. PlanA/PlanB/watch/bench/quarantine 후보 수
10. 후보 교체 결정 로그
11. data quality 결측 현황

핵심 지표:

| 지표 | 의미 |
|---|---|
| `prompt_top30_good_rate` | 상단 후보 품질 |
| `prompt_top30_bad_rate` | 상단 후보 위험도 |
| `omitted_top30_count` | 좋은 후보 누락 가능성 |
| `promoted_loser_count` | ranker false positive |
| `missed_winner_count` | ranker false negative |
| `plan_a_candidate_count` | 즉시 후보 압력 |
| `unknown_source_pct` | attribution 품질 |
| `unknown_feature_pct` | feature 완성도 |

## 11. 대시보드 요구사항

후보 품질 패널:

- raw 후보 수
- scored 후보 수
- prompt 후보 수
- omitted top30 수
- PlanA / PlanB / watch / bench / quarantine 수
- unknown source 비율
- top promoted candidates
- top omitted candidates

후보 카드 추가 표시:

- quality score
- prompt rank
- selected rank
- first seen time
- source/cohort
- PlanA/PlanB/watch/bench/quarantine 분류
- demotion/exclusion reason

표시 원칙:

- quality score는 매수 추천 점수가 아니다.
- 화면에는 "후보 랭킹/트레이너 점수"로 표기한다.

## 12. 장점

| 장점 | 효과 |
|---|---|
| Claude 입력 품질 개선 | Claude가 실제로 볼 후보 상단의 기대값이 올라감 |
| PlanA 노이즈 감소 | 즉시 매수 후보가 더 좁고 엄격해짐 |
| missed winner 보존 | 좋은데 비싼 후보를 삭제하지 않고 PlanB/watch로 보존 |
| 시장별 학습 가능 | KR/US가 하나의 임계값에 묶이지 않음 |
| 디버깅 개선 | 후보가 왜 들어갔고 왜 빠졌는지 설명 가능 |
| 안전한 배포 | shadow로 검증 후 live 적용 가능 |

## 13. 단점과 비용

| 단점 | 영향 |
|---|---|
| 상태와 audit 데이터 증가 | DB/log 용량 증가 |
| 설정값 증가 | paper/live config drift 위험 증가 |
| ranking 복잡도 증가 | 잘못된 점수가 좋은 후보를 숨길 수 있음 |
| 초기 구현 속도 느림 | 관측/리포트부터 만들어야 해서 즉시 수익 개선은 늦음 |
| Claude 비용 증가 가능성 | prompt cap 또는 metadata 증가 시 token 증가 |

## 14. 리스크와 완화책

### 거래 리스크

| 리스크 | 심각도 | 설명 | 완화책 |
|---|---:|---|---|
| missed winner | High | 엄격한 상단 승격 때문에 강한 종목이 prompt/action에서 밀릴 수 있음 | broad discovery 유지, watch fast-lane, missed winner daily report |
| overfit | High | 최근 KR/US 패턴이 일시적일 수 있음 | 최소 표본 수, shadow 검증, hard rule 전 soft prior |
| forward leakage | Critical | 미래 수익률 라벨이 live scoring에 섞이면 치명적 | label table 분리, known_at 검증, live scoring에서 outcome join 금지 |
| under-trading | Medium | PlanA가 너무 좁아져 매수 기회가 줄 수 있음 | PlanA count 추적, PlanB/watch 활성 유지 |
| chase false negative | Medium | 강한 추세 지속 종목을 overextended로 잘못 강등할 수 있음 | fresh confirmation fast-lane 유지 |
| low-liq misuse | Medium | KR low-liq 급등주가 일반 PlanA로 들어올 수 있음 | 별도 small-probe/watch path, 일반 PlanA 금지 |

### 개발 리스크

| 리스크 | 심각도 | 설명 | 완화책 |
|---|---:|---|---|
| hidden prompt trimming | High | 최종 prompt builder가 내부에서 또 잘라내면 ranker 효과가 사라짐 | 최종 prompt 생성 후 실제 ticker 저장 |
| candidate key mismatch | High | source, prompt, selection, outcome join 실패 | candidate key/trace id 표준화 |
| dirty default | High | 결측 feature가 0으로 처리되면 잘못된 점수 발생 | unknown/data_quality_flags 강제 |
| config drift | Medium | paper/live 설정이 달라질 수 있음 | startup effective config report |
| `trading_bot.py` 비대화 | Medium | orchestration 파일에 로직이 더 쌓임 | scoring/prompt builder를 runtime module로 분리 |
| DB 증가 | Low | prompt audit row 증가 | daily summary와 retention 정책 |

## 15. 보완된 개선안

초기 생각은 "후보를 적게 뽑고 좋은 애만 보자"였다.

보완된 설계는 다음이다.

```text
후보 수집량은 줄이지 않는다.
상단 승격 기준만 엄격하게 만든다.
Claude prompt 상단을 trainer score로 재정렬한다.
PlanA는 좁힌다.
PlanB/watch는 보존한다.
후보 제외/강등 사유를 전부 남긴다.
```

보완 포인트:

1. discovery pool 축소 금지.
2. prompt visibility 문제 해결 전 live 매수 정책 변경 금지.
3. 표본 부족 cohort는 hard block 금지.
4. KR/US 전역 공통 threshold 금지.
5. PlanA와 PlanB 후보 로직 통합 금지.
6. at-high 후보는 강해 보여도 기본 PlanA 금지, confirmation 필요.
7. 좋은데 비싼 후보는 삭제하지 말고 PlanB/watch로 강등.

## 16. 배포 계획

### Phase 0. 관측 보강

목표:

- 실제 prompt 포함/제외와 feature 결측을 볼 수 있게 만든다.

작업:

- 최종 prompt ticker 저장
- prompt rank, score rank, exclusion reason 저장
- source/cohort/freshness 필드 채우기
- prompt visibility report 생성

완료 기준:

- 모든 Claude 호출의 prompt ticker 재구성 가능
- top30 omission 측정 가능
- unknown source/feature 비율 확인 가능

### Phase 1. Shadow Quality Scorer

목표:

- live 행동 변경 없이 trainer score 계산

작업:

- scoring module 추가
- KR/US prior 추가
- score component logging
- 기존 prompt 순서 vs trainer 순서 비교

완료 기준:

- 3~5 세션 shadow 확보
- trainer top30 good rate가 기존보다 높거나 같음
- promoted loser 비율 악화 없음

### Phase 2. Paper Prompt Reorder

목표:

- paper 또는 guarded mode에서 trainer 순서로 Claude 입력

작업:

- paper prompt reorder flag 활성화
- buy permission은 기존대로 유지
- Claude output 변화 비교

완료 기준:

- prompt 리스트가 안정적으로 재현됨
- token cost 허용 가능
- hidden trimming 없음

### Phase 3. Live Prompt Reorder

목표:

- execution gate는 그대로 두고 live prompt 순서만 개선

작업:

- `CANDIDATE_PROMPT_POOL_REORDER_ENABLED` 활성화
- PlanA threshold는 보수적으로 유지
- omitted winner, promoted loser, PlanA count 모니터링

완료 기준:

- 여러 세션에서 prompt quality density 개선
- ranking 때문에 생긴 심각한 missed winner cluster 없음
- bad PlanA density 증가 없음

### Phase 4. 후보 교체와 tier 통합

목표:

- trainer score를 후보 교체와 tier 승격/강등에 사용

작업:

- replacement min-delta 적용
- CORE 보호
- stale/chase 후보 PlanB/watch 강등
- same-day loser quarantine 유지

완료 기준:

- 교체 결정 재현 가능
- active strong candidate가 rank churn으로 제거되지 않음
- 후보 churn이 줄거나 설명 가능해짐

## 17. 구현 위치 제안

| 영역 | 위치 |
|---|---|
| 후보 점수화 | `runtime/candidate_quality_trainer.py` |
| prompt pool builder | `runtime/candidate_prompt_pool.py` |
| audit 저장 | `audit/candidate_audit_store.py` |
| orchestration hook | `trading_bot.py` |
| daily report | `tools/candidate_quality_report.py` |
| 테스트 | `tests/test_candidate_quality_trainer.py`, `tests/test_candidate_prompt_pool.py` |

구현 원칙:

- 점수화 로직은 `trading_bot.py`에 직접 많이 넣지 않는다.
- `trading_bot.py`는 모듈 호출과 결과 저장만 담당한다.
- scoring 함수는 deterministic 해야 한다.
- live scoring path는 outcome label을 읽지 않는다.

## 18. 테스트 요구사항

unit test:

- 같은 입력이면 같은 점수가 나온다.
- 결측 feature는 unknown/data_quality penalty가 된다.
- KR/US prior가 다르게 적용된다.
- quarantine 후보는 prompt pool에 들어가지 않는다.
- prompt cap이 정확히 적용된다.
- 제외된 후보는 exclusion reason을 가진다.
- replacement는 min_delta와 CORE 보호를 지킨다.
- live scoring 함수가 forward label을 읽지 않는다.

integration test:

- mock 후보 사이클이 prompt audit row를 남긴다.
- 기존 prompt 순서와 trainer prompt 순서가 비교된다.
- 중복 ticker source를 prompt builder가 병합한다.
- restart 후 저장된 score/audit 상태를 재구성할 수 있다.

report test:

- source 결측이 있어도 daily report가 생성된다.
- prompt-visible, selected, omitted, not-selected 후보가 분리된다.

## 19. 초기 성공 기준

첫 버전의 성공 기준은 live PnL 증가가 아니다. 먼저 후보 품질 통제가 증명되어야 한다.

초기 성공 기준:

- 실제 prompt ticker 저장 coverage 100%.
- omitted top30 reason coverage 100%.
- unknown source 비율 감소.
- trainer 제안 top30 good-label density가 기존 top30보다 개선.
- prompt cap 확장 후에도 PlanA 후보 수가 자동 증가하지 않음.
- live scoring path에 forward-label leakage 없음.
- daily report에서 missed winner top10, promoted loser top10 확인 가능.

## 20. 최종 권고

후보군 개선은 새 매수 규칙을 더 붙이는 방식으로 시작하면 안 된다.

먼저 해야 할 일은 후보가 왜 Claude 상단에 들어갔는지, 왜 빠졌는지, 왜 PlanA가 아니라 PlanB/watch인지 설명 가능한 구조를 만드는 것이다.

즉시 개발 순서:

1. 실제 prompt ticker와 제외 사유 저장.
2. 후보 feature/source/cohort/freshness 누락 보강.
3. 30/60분 후보 품질 offline label/report 생성.
4. shadow `candidate_quality_score` 추가.
5. 기존 prompt 순서와 trainer 순서 비교.
6. shadow 성과 확인 후 prompt reorder 적용.
7. 마지막에 후보 교체/tier 승격 로직에 trainer score 반영.

