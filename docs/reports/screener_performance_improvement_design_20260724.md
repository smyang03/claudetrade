# KR/US 스크리너 성능·데이터 흐름 재점검 및 개선 설계

- 작성일: 2026-07-24 KST
- 실측 구간: 2026-07-01 ~ 2026-07-23
- 대상: live 후보의 `screener_seen → prompt → action → fill` 경로
- 재생 표본: 시장·세션·종목별 최초 관측 4,602건
- 외부 API 호출: 0건
- 운영 로직 변경: 없음
- 재현 산출물: `docs/reports/verify_20260724/screener_performance_review_20260724.json`
- 재현 도구: `tools/screener_performance_review.py`

## 0. 최종 결론

현 스크리너는 후보를 발견하는 기능 자체는 작동한다. 그러나 현재 후보에게 붙는 여러 점수를 곧바로 “앞으로 오를 확률” 또는 “매수 우선순위”로 해석하면 안 된다.

가장 중요한 결론은 다음과 같다.

1. `CANDIDATE_PROMPT_POOL_REORDER_ENABLED=false`는 구현 누락이 아니다.
   - 2026-07-10 커밋 `43f6e68`에서 의도적으로 `true → false`로 바뀌었다.
   - 당시 이유는 trainer/composite 점수가 역분별될 수 있어 후보 포함 여부, 슬롯, 주문금액에 영향을 주지 못하게 하려는 것이었다.
   - 이번 실데이터 재생도 전역 `true` 전환을 지지하지 않는다.

2. KR과 US를 같은 점수와 같은 정렬 정책으로 다루면 안 된다.
   - KR은 KIS 원시 순위와 trainer 점수에 일부 유효한 신호가 보인다.
   - 하지만 KR hard cap 28 기준 paired counterfactual에서 라벨 커버리지가 충분한 세션만 보면 trainer 우위가 확인되지 않았다.
   - US는 raw rank와 trainer rank의 상위 후보가 모두 60분 기준 음수였고, hard cap 24에서 trainer가 더 나빴다.

3. `score_current`는 수익 점수가 아니다.
   - 코드 주석 자체가 “bullish direction이 아닌 movement intensity”라고 명시한다.
   - KR에서 `raw_score_current` 상위 20%는 60분 평균 -0.734%, 중위값 -1.247%였다.
   - 따라서 이 점수는 매수 우선순위가 아니라 과열·변동성 진단값으로 취급해야 한다.

4. 지금 가장 큰 문제는 점수 미세조정보다 측정 배선이다.
   - 후보 원장에는 KR/US 체결이 각각 0건인데 canonical 성과 원장에는 각각 6건, 5건이 존재한다.
   - 이 상태에서는 `후보 선정 → 실제 체결 → 순수익`을 하나의 event lineage로 평가할 수 없다.
   - 현재 결과는 “후보가 관측된 뒤의 60분 기회수익”이지 실행 가능한 순수익 검증이 아니다.

5. 개선 방향은 전역 재정렬이 아니라 시장별 lane 분리와 point-in-time 데이터 계약이다.
   - KR: 원시 KIS 순위를 보존하면서 과열 penalty, multi-label bucket, trainer shadow를 검증한다.
   - US: `day_gainers`, `most_actives`, `day_losers`를 서로 다른 전략 lane으로 분리한다.
   - US의 `vol_ratio=1.0` placeholder는 실제 값처럼 소비하지 못하게 해야 한다.

따라서 현재 운영 판단은 아래와 같다.

```text
CANDIDATE_PROMPT_POOL_REORDER_ENABLED=false 유지
KR 전용 trainer reorder = shadow A/B만 진행
US trainer reorder = 승격 금지, source-lane 방식부터 검증
후보→결정→주문→체결 lineage 복구 = P0
```

## 1. 다른 AI의 judge recheck 점검과의 관계

이번 점검은 다른 AI가 진행 중인 `judge recheck queue` 분석과 다른 영역이다.

| 점검 | 시작점 | 핵심 질문 | 자본 영향 위치 |
|---|---|---|---|
| 본 리포트 | 스크리너가 종목을 처음 발견한 시점 | 어떤 종목을 Claude에게 보여 주며, 입력 데이터가 실제로 사용되는가 | 후보 풀과 prompt cap 이전 |
| judge recheck 점검 | Claude가 이미 판단했거나 재판단 대상으로 등록된 시점 | 제한된 재호출 예산으로 어떤 후보를 다시 볼 것인가 | 판단 재호출과 route 이전 |

두 작업은 평가 방법만 공유한다.

- 실데이터 재생
- 세션 단위 A/B
- 미래 데이터 차단
- 라벨 커버리지 차이를 먼저 검사

스크리너 개선이 나쁜 후보를 덜 넘기고, judge queue 개선이 이미 넘어온 후보 중 재검토 우선순위를 정한다. 서로 대체 관계가 아니며 중복 작업도 아니다.

## 2. 실제 데이터를 넣어 검증했는가

그렇다. 단순 코드 grep이나 가상 fixture만으로 결론을 내리지 않았다.

### 2.1 입력

`data/audit/candidate_audit.db`에서 다음 조건으로 live 후보를 읽었다.

- `runtime_mode='live'`
- `screener_seen=1`
- 2026-07-01 ~ 2026-07-23
- 시장·세션·종목별 최초 `known_at` 한 건

중복 스냅샷을 제거한 결과는 다음과 같다.

| 시장 | 최초 후보 |
|---|---:|
| KR | 1,727 |
| US | 2,875 |
| 합계 | 4,602 |

### 2.2 60분 결과 라벨

각 후보의 최초 `known_at` 이후 로컬 분봉을 직접 연결했다.

```text
entry = known_at과 같거나 그 이후의 첫 분봉 open
outcome = known_at + 60분과 같거나 그 이후의 첫 분봉 close
MFE/MAE = entry부터 outcome까지의 고가/저가
```

`known_at` 이전 봉은 사용하지 않았다. 같은 세션 종료를 넘어가는 바도 사용하지 않았다.

이 라벨은 다음을 의미한다.

- 의미함: 그 후보를 처음 알게 된 뒤 실제 시장에서 60분 동안 존재한 gross opportunity
- 의미하지 않음: 실제 주문가, 체결가, 슬리피지, 세금·수수료 반영 순수익

### 2.3 추가 실측 입력

- KR provider log: `logs/screener/*_KR_screen.jsonl`
- US quality log: `logs/screener_quality/*_US_candidates.jsonl`
- sub-screener state: `state/sub_screener_{KR,US}_*.json`
- canonical fill: `data/ml/decisions.db`
- 로컬 분봉: `data/price/minute/{kr,us}`

### 2.4 측정 오류 방지

초기 도구의 top-K 계산에서 라벨이 있는 종목만 먼저 남기는 오류를 발견해 수정했다.

올바른 순서는 다음과 같다.

1. 당시 점수·순위만으로 top-K를 확정한다.
2. 확정된 종목에 결과 라벨이 있는지 확인한다.
3. 라벨 커버리지를 별도로 보고한다.
4. A/B 두 처리군의 커버리지가 모두 80% 이상인 세션을 별도 집계한다.

이 계약은 단위 테스트로 고정했다. 총 4개 테스트가 통과했다.

## 3. 현재 스크리너 구조

현재 흐름은 대략 아래와 같다.

```text
provider/collector
  → source별 raw candidate
  → 필터·quota·cache/fallback
  → bucket 및 shadow score
  → prompt pool cap
  → Claude 판단
  → route/signal/order/fill
  → outcome/learning
```

### 3.1 KR

- KIS 거래량 순위에서 KOSPI/KOSDAQ 후보를 받는다.
- KOSDAQ 최소 비율을 적용해 한 시장이 후보 풀을 독점하지 못하게 한다.
- `_kr_screen_score`는 거래대금 로그, 양의 등락률, 거래량 비율을 합성한다.
- live 결과가 부족하면 cache와 fallback 후보를 보강한다.
- bucket과 quality/trainer feature가 후속 prompt pool에 전달된다.

현재 KR score는 대략 다음 성격이다.

```text
log(거래대금) + 양의 등락률 가중 + 거래량 비율 가중
```

이는 유동성과 당일 움직임을 찾는 discovery score이지, 비용 후 미래수익을 직접 최적화한 모델이 아니다.

### 3.2 US

- `most_actives`
- `day_gainers`
- `day_losers`

세 Yahoo predefined source를 주로 사용하고, 실패 시 다른 provider/cache/fallback을 이용한다.

중요한 문제는 Yahoo/FMP 경로에서 `vol_ratio=1.0`이 실제 관측값이 아니라 placeholder라는 점이다. 코드 주석도 이를 명시한다. 그런데 후속 bucket과 gate는 이 필드를 실제 값처럼 소비할 수 있다.

즉 “필드가 존재한다”와 “실데이터가 연결됐다”가 다른 상태다. 이번 점검이 찾으려는 대표적인 데이터 흐름 문제다.

### 3.3 bucket과 score

`bot/bucket_classifier.py`의 `shadow_scores`에는 다음 의미가 명시돼 있다.

```text
movement intensity, not bullish direction
```

또한 한 후보가 여러 조건에 동시에 걸려도 `BUCKET_PRIORITY` 순서에 따라 primary bucket 하나가 결정된다. 이 방식은 다음 문제를 만든다.

- 유동성 우수와 과열이 동시에 존재해도 하나의 label만 전면에 나온다.
- 우선순위가 높은 bucket이 실제 수익성이 낮아도 후보의 대표 label이 된다.
- 시장별로 다른 bucket 성과를 공통 우선순위가 덮는다.

따라서 bucket은 single-label 우선순위보다 multi-label feature로 저장하고, 시장별 결합 성과를 평가해야 한다.

## 4. provider와 collector 상태

### 4.1 KR

| 항목 | 값 |
|---|---:|
| snapshot | 1,272 |
| 관측일 | 22일 |
| live raw | 1,239 |
| live raw 비율 | 97.41% |
| zero-price hard fallback | 32 |
| cache/fallback | 1 |

zero-price hard fallback 32건은 거의 전부 08:00과 08:15에 집중됐다.

| 시각 | hard fallback |
|---|---:|
| 08:00 | 15 |
| 08:15 | 15 |
| 08:01 | 1 |
| 08:16 | 1 |

KR premarket live window보다 이른 시각에 intraday screen을 호출하면서 실가격이 없는 fallback 후보가 생성된 것으로 보인다.

이것을 곧바로 “실매수 오류”라고 단정할 수는 없다. preopen collector일 수 있기 때문이다. 그러나 데이터 계약상 아래 상태를 명시해야 한다.

```text
PREMARKET_NO_LIVE_DATA
price_valid=false
rank_eligible=false
watch_only=true
```

가격 0인 fallback을 정상 후보와 같은 score 공간에 넣어서는 안 된다.

### 4.2 US

| 항목 | 값 |
|---|---:|
| snapshot | 449 |
| 관측일 | 19일 |
| OK | 406 |
| degraded | 43 |
| degraded 비율 | 9.58% |
| cache 사용 | 197 |
| cache 사용 비율 | 43.88% |
| snapshot당 평균 고유 후보 | 77.88 |

US는 후보 수 자체는 충분하지만 cache 의존이 높다. cache 사용이 반드시 오류는 아니지만 다음이 함께 저장돼야 한다.

- 원 provider의 `as_of`
- cache 생성 시각
- 현재 판단 시각
- candidate age
- cache 사용 사유
- 해당 후보가 rank 변경 권한을 갖는지

현재는 “후보가 있음”은 알 수 있어도 “현재 시점 정보로 다시 순위를 매겨도 되는가”를 완전히 증명하기 어렵다.

## 5. 후보 풀의 60분 실측

### 5.1 전체

| 시장 | 후보 | 분봉 연결 | 커버리지 | 평균 gross | 중위 gross | 양수 비율 | 평균 MFE | 평균 MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR | 1,727 | 869 | 50.32% | +0.545% | +0.140% | 50.29% | +5.513% | -4.091% |
| US | 2,875 | 1,137 | 39.55% | +0.102% | +0.111% | 52.42% | +1.773% | -1.511% |

해석:

- 스크리너가 완전히 무작위 후보만 내는 것은 아니다.
- 하지만 분봉 커버리지가 낮고 비무작위이므로 평균값을 모집단 성과로 일반화하면 안 된다.
- KR의 MFE/MAE가 매우 크다. 변동성 높은 종목과 극단치의 영향이 강하므로 평균보다 세션 중위값, tail risk, 비용 후 성과를 우선해야 한다.

### 5.2 source별

#### KR

| source | 연결/후보 | 커버리지 | 평균 | 중위 | 양수 비율 |
|---|---:|---:|---:|---:|---:|
| volume_rank | 854/1,578 | 54.12% | +0.521% | +0.100% | 50.12% |

KR volume rank는 discovery source로 유지할 근거가 있다. 다만 source 전체 평균만으로 상위 순서의 우수성을 증명하지는 못한다.

#### US

| source | 연결/후보 | 커버리지 | 평균 | 중위 | 양수 비율 |
|---|---:|---:|---:|---:|---:|
| day_gainers | 457/1,358 | 33.65% | +0.202% | +0.204% | 56.67% |
| most_actives | 536/704 | 76.14% | -0.089% | -0.112% | 47.76% |
| day_losers | 69/599 | 11.52% | +0.330% | +0.305% | 52.17% |
| mega_gap | 4/12 | 33.33% | +4.526% | +3.980% | 100% |

해석:

- `day_gainers`는 continuation 탐색 lane에 더 많은 실험 예산을 줄 근거가 있다.
- `most_actives`는 alpha source보다 유동성 universe source로 보는 것이 맞다.
- `day_losers`는 커버리지 11.52%라 성과 해석을 보류해야 한다.
- `mega_gap`은 4건뿐이라 정책 근거가 아니다.

세 source를 하나의 scalar score로 섞는 현재 방식보다 source별 목적을 먼저 분리해야 한다.

## 6. bucket별 실측

### 6.1 KR

| primary bucket | 연결/후보 | 평균 | 중위 | 양수 비율 |
|---|---:|---:|---:|---:|
| liquidity_leader | 133/192 | +1.008% | +0.561% | 57.14% |
| volume_surge | 196/288 | +0.737% | 0.000% | 44.39% |
| unclassified | 253/603 | +1.204% | +0.999% | 58.50% |
| pullback_watch | 39/52 | +0.550% | -0.686% | 48.72% |
| near_breakout | 217/415 | -0.544% | 0.000% | 42.86% |
| momentum_now | 16/28 | -2.613% | -1.799% | 31.25% |

KR 개선 시사점:

- `liquidity_leader`는 유지 가치가 있다.
- `momentum_now`는 표본이 16개로 작지만 손실 방향이 크다. 매수 boost가 아니라 overheat/risk label 후보가 맞다.
- `near_breakout`은 이름과 달리 60분 continuation이 확인되지 않았다. 확인 조건 없는 breakout label은 승격하면 안 된다.
- `pullback_watch`는 평균이 양수지만 중위값이 음수다. 일부 큰 반등이 평균을 끌어올린 tail-driven 결과다.
- `unclassified`가 가장 나은 그룹 중 하나라는 것은 현재 bucket 정의가 중요한 구분을 놓치고 있다는 신호다.

### 6.2 US

| primary bucket | 연결/후보 | 평균 | 중위 | 양수 비율 |
|---|---:|---:|---:|---:|
| near_breakout | 682/1,605 | +0.183% | +0.133% | 53.96% |
| liquidity_leader | 217/343 | -0.029% | -0.030% | 49.77% |
| momentum_now | 46/112 | -0.220% | +0.168% | 52.17% |
| pullback_watch | 88/184 | -0.380% | -0.300% | 42.05% |

US 개선 시사점:

- `pullback_watch`는 현재 형태로 매수 우선순위를 높이면 안 된다.
- `near_breakout`은 약한 양의 신호가 있지만 비용 전 +0.183%라 미국 비용·슬리피지 가정 후 사라질 수 있다.
- `liquidity_leader`는 체결 가능성에는 유용하지만 alpha label로는 중립이다.

## 7. 점수와 순위가 실제로 분별하는가

### 7.1 Spearman 상관

| 시장 | 점수 | 표본 | 전체 상관 | 세션 중위 상관 |
|---|---|---:|---:|---:|
| KR | raw_score_current | 810 | -0.151 | -0.228 |
| KR | candidate_quality_score | 854 | +0.032 | +0.036 |
| KR | trainer_prompt_score | 854 | +0.124 | +0.184 |
| US | raw_score_current | 955 | +0.045 | +0.011 |
| US | candidate_quality_score | 1,066 | -0.031 | -0.019 |
| US | trainer_prompt_score | 1,066 | -0.038 | -0.005 |

판단:

- KR `raw_score_current`는 높은 값일수록 60분 수익이 나빠지는 역분별이 관측됐다.
- KR trainer는 약한 양의 분별력이 있으나 승격 기준에는 부족하다.
- US에서는 세 점수 모두 미래수익 순위로서 의미가 거의 없다.

### 7.2 KR raw score 분위

| 분위 | 평균 | 중위 | 양수 비율 |
|---|---:|---:|---:|
| Q1, score 최고 | -0.734% | -1.247% | 34.67% |
| Q2 | -0.255% | 0.000% | 45.83% |
| Q3 | +1.045% | +0.411% | 53.14% |
| Q4 | +1.785% | +1.549% | 65.62% |
| Q5, score 최저 | +1.010% | +1.260% | 55.95% |

이 결과는 `score_current`가 강한 상승 후보가 아니라 “이미 크게 움직인 후보”를 위로 올린다는 코드 의미와 일치한다.

### 7.3 KR trainer score 분위

| 분위 | 평균 | 중위 | 양수 비율 |
|---|---:|---:|---:|
| Q1 | +1.179% | +0.709% | 56.80% |
| Q2 | +1.550% | +0.917% | 58.82% |
| Q3 | +0.301% | +0.236% | 50.35% |
| Q4 | +0.764% | 0.000% | 49.59% |
| Q5 | -1.772% | -1.185% | 29.03% |

KR trainer에는 연구할 가치가 있는 구조가 보인다. 다만 분위별 라벨 커버리지가 38.66%~77.16%로 크게 다르고, 실제 hard cap counterfactual 우위가 안정적이지 않다. 따라서 shadow A/B 대상이지 즉시 enforce 대상이 아니다.

### 7.4 US trainer score 분위

| 분위 | 평균 | 중위 | 양수 비율 |
|---|---:|---:|---:|
| Q1 | -0.054% | -0.157% | 47.55% |
| Q2 | +0.024% | +0.143% | 54.79% |
| Q3 | +0.533% | +0.360% | 60.71% |
| Q4 | +0.187% | +0.391% | 60.14% |
| Q5 | -0.161% | -0.169% | 43.95% |

상위 분위가 가장 좋지 않으며 단조성도 없다. US trainer reorder를 켤 근거가 없다.

## 8. `CANDIDATE_PROMPT_POOL_REORDER_ENABLED=false` 재검토

### 8.1 왜 false가 됐는가

`git blame`과 커밋 내용을 확인한 결과:

- 최초 trainer 기능 커밋: `287ec6e`
- production start config에서 false로 변경: `43f6e68`, 2026-07-10

커밋 메시지의 핵심은 다음과 같다.

```text
점수권한 축소:
composite/trainer hint/pool reorder/priority sort off,
sub_screener raw_order

역분별 가능 종합점수가 후보·slot·주문금액을 바꾸지 못하게 하고
원시 feature만 유지
```

즉 점수가 의미 없어서 코드를 버린 것이 아니라, 검증 전 자본 영향 권한만 제거하고 shadow 학습 대상으로 남긴 것이다.

### 8.2 현재 hard cap 기준 반사실

현재 cap은 KR 28, US 24다. 당시 세션에서 raw rank top-K와 trainer rank top-K를 각각 선택한 뒤 비교했다.

| 시장 | 정책 | 라벨 커버리지 | 세션 평균 gross | 세션 중위 gross | 양수 세션 |
|---|---|---:|---:|---:|---:|
| KR | raw top-28 | 74.33% | -0.009% | -0.506% | 43.75% |
| KR | trainer top-28 | 86.83% | +0.340% | +0.484% | 62.50% |
| US | raw top-24 | 83.59% | -0.208% | -0.266% | 50.00% |
| US | trainer top-24 | 90.89% | -0.351% | -0.675% | 43.75% |

표면상 KR trainer가 좋아 보이지만 paired 검사를 적용하면 승격할 수 없다.

#### KR top-28 paired

- 전체 16세션 평균 trainer-control: +0.349%p
- 중위 delta: 0.000%p
- trainer 승리 세션: 25%
- 두 처리군 라벨 커버리지 80% 이상인 세션: 8
- 해당 8세션 평균 delta: 0.000%p
- 해당 8세션 trainer 승리율: 0%
- 평균 후보 중복률: 62.05%

양의 평균 delta는 주로 양쪽 라벨 커버리지가 불균형한 세션에서 발생했다. 커버리지가 충분한 세션에서는 trainer 우위가 남지 않았다.

#### US top-24 paired

- 전체 16세션 평균 trainer-control: -0.144%p
- 중위 delta: 0.000%p
- trainer 승리 세션: 12.5%
- 커버리지 적격 9세션 평균 delta: -0.014%p
- 적격 세션 trainer 승리율: 11.11%
- 평균 후보 중복률: 75.52%

US는 전환 반대 근거가 더 강하다.

### 8.3 현재 판단

```text
전역 true: 반대
KR 전용 true: 아직 반대
KR 전용 shadow: 찬성
US 전용 trainer reorder: 반대
```

KR trainer가 연구 가치가 있다는 것과 운영 `true`가 맞다는 것은 다른 주장이다. 현재는 전자만 성립한다.

### 8.4 설정 불일치

production effective source인 `config/v2_start_config.json`은 false지만 `.env.example`은 아직 true다.

이 불일치는 신규 배포나 수동 환경 구성에서 위험하다.

- example을 따라 구성하면 의도치 않게 후보 순서가 바뀔 수 있다.
- preflight가 effective source를 명확히 기록하지 않으면 운영자가 실제 값을 오판할 수 있다.

개선 시 아래를 적용해야 한다.

```text
.env.example 기본값 false로 정렬
effective config snapshot에 source와 value 기록
market별 flag 분리
```

## 9. prompt 포함 여부와 downstream 전환

### 9.1 prompt 포함 후보의 60분 결과

| 시장 | prompt | 연결/후보 | 커버리지 | 평균 | 중위 | 양수 비율 |
|---|---|---:|---:|---:|---:|---:|
| KR | 제외 | 256/983 | 26.04% | +0.558% | +0.413% | 51.17% |
| KR | 포함 | 613/744 | 82.39% | +0.539% | 0.000% | 49.92% |
| US | 제외 | 552/2,252 | 24.51% | +0.364% | +0.266% | 58.51% |
| US | 포함 | 585/623 | 93.90% | -0.145% | -0.167% | 46.67% |

US prompt 포함군이 크게 나빠 보이지만 이를 곧바로 인과효과로 해석하면 안 된다.

- 포함군과 제외군의 라벨 커버리지가 크게 다르다.
- prompt 포함은 score 외에도 데이터 가용성, source, 시간, bucket과 함께 결정된다.
- 로컬 분봉 자체가 후보별로 비무작위 수집됐다.

그래도 최소한 “현재 prompt trimming이 좋은 종목을 더 많이 남겼다는 증거는 없다”는 결론은 가능하다.

### 9.2 funnel conversion

| 시장 | 최신 후보 | prompt | prompt율 | actionable | prompt→actionable | 후보 원장 fill | canonical fill |
|---|---:|---:|---:|---:|---:|---:|---:|
| KR | 1,727 | 827 | 47.89% | 3 | 0.36% | 0 | 6 |
| US | 2,875 | 686 | 23.86% | 7 | 1.02% | 0 | 5 |

이 수치는 다음 두 가지를 동시에 보여 준다.

1. 후보 수보다 downstream 전환이 훨씬 좁다.
2. 후보 원장과 canonical 체결 원장의 attribution이 끊겨 있다.

actionable 비율이 낮다고 해서 스크리너가 직접 원인이라고 단정할 수 없다. Claude 판단, evidence gate, route ceiling, 예산, 장 상태가 모두 영향을 준다. 먼저 event lineage를 복구해야 단계별 사망률을 정확히 분해할 수 있다.

## 10. sub-screener 실측

| 시장 | 세션 | scan | detection | full attempt | success_count | dedupe suppress | triage success |
|---|---:|---:|---:|---:|---:|---:|---:|
| KR | 16 | 550 | 213 | 2 | 215 | 211 | 213 |
| US | 16 | 511 | 398 | 180 | 575 | 53 | 398 |

주의할 점:

- `success_count`는 수익 성공이 아니라 운영 처리 성공이다.
- triage success가 success_count에 포함되므로 full attempt보다 success가 클 수 있다.
- KR은 detection 213건 중 full rescreen 2건뿐이고 대부분 triage에서 처리됐다.

따라서 dashboard와 보고서 용어를 분리해야 한다.

```text
scan_processed_count
candidate_detected_count
triage_processed_count
full_rescreen_attempt_count
economic_outcome_count
```

현재 `success_count`라는 이름은 경제적 성과로 오독될 가능성이 크다.

## 11. P0: 데이터 흐름 계약 개선

전략을 더 추가하기 전에 아래 event chain을 복구해야 한다.

| 단계 | 필수 ID | 필수 시점 | 필수 데이터 | 실패 사유 |
|---|---|---|---|---|
| provider snapshot | `screen_snapshot_id` | provider `as_of` | source/category/raw rank/raw value | provider/cache/fallback |
| normalized candidate | `candidate_event_id` | `known_at` | price/volume/change/quality | missing/placeholder/stale |
| feature snapshot | `feature_snapshot_id` | feature `as_of` | 실제 소비 feature와 version | not_loaded/not_point_in_time |
| prompt pool | `prompt_pool_id` | pool build 시각 | included, rank, cap, exclusion | cap/data/bucket/dedupe |
| Claude call | `decision_id` | request/response | 후보 입력 hash, 판단 | budget/error/abstain |
| route | `route_event_id` | route 시각 | final action/ceiling/reason | gate/capacity/expiry |
| order/fill | broker order ID | sent/filled | qty/price/cost/slippage | reject/cancel/partial |
| outcome | `label_id` | label available | 30m/60m/close/multiday/net | no_bar/not_mature |

### 11.1 필드 존재와 실데이터 사용을 분리

각 중요 필드는 아래 metadata를 가져야 한다.

```json
{
  "value": null,
  "source": "yahoo_predefined",
  "as_of": "2026-07-23T22:31:00+09:00",
  "quality": "missing",
  "is_placeholder": false,
  "consumed_by": ["bucket_classifier"],
  "consumer_version": "..."
}
```

최소한 다음 필드는 placeholder를 정상값과 구분해야 한다.

- `vol_ratio`
- price 0 fallback
- spread
- news polarity
- sector
- cohort reliability
- stale cache value

### 11.2 후보→체결 attribution

canonical fill 11건 모두에 대해 아래가 역추적돼야 한다.

```text
fill
 → route_event_id
 → decision_id
 → prompt_pool_id
 → candidate_event_id
 → screen_snapshot_id
```

후보 경로 밖 sleeve라면 억지로 candidate에 연결하지 말고 `origin_lane=sleeve`를 명시해야 한다. 현재처럼 후보 원장 0건, canonical 원장 11건이면 “없음”과 “관측 불가”를 구분할 수 없다.

### 11.3 외부 데이터 사전 채움 원칙

사전 외부 데이터로 채울 수 있는 필드는 먼저 point-in-time 적합성을 검증한다.

검증 항목:

- 당시 이용 가능했던 timestamp인가
- 사후 수정 데이터가 섞이지 않았는가
- symbol mapping과 market session이 맞는가
- delisted/survivorship bias가 없는가
- cache age를 알 수 있는가

검증 실패 시 기능을 포기하지 않는다.

```text
1. missing_reason을 기록한다.
2. collector 또는 저장 주기를 개선한다.
3. 검증된 대체 source를 연결한다.
4. 그 전까지는 rank 권한을 제거하고 watch-only로 둔다.
```

특히 전체 시장 승자 회수율을 계산하려면 후보 원장 밖의 point-in-time full universe가 필요하다. 현재 데이터만으로는 스크리너 precision은 일부 볼 수 있지만 “그날 오른 종목을 얼마나 놓쳤는가”라는 recall은 계산할 수 없다.

## 12. KR 개선 설계

### 12.1 유지할 것

- KIS volume rank를 discovery backbone으로 유지
- KOSPI/KOSDAQ quota 유지
- raw provider order를 baseline control로 유지
- trainer와 quality model은 shadow feature로 유지

### 12.2 score 역할 분리

한 개 합성 점수 대신 아래 축을 분리한다.

| 축 | 목적 | 예시 feature |
|---|---|---|
| liquidity | 주문 가능성과 시장 관심 | turnover, volume rank, spread |
| continuation | 앞으로 추가 상승 가능성 | VWAP 위치, opening range, relative strength |
| reversal | 눌림 후 회복 | pullback depth, reclaim, support distance |
| overheat | 추격 위험 | 당일 급등, 고점 이격, MFE 소진 |
| data quality | 점수 사용 권한 | age, missing, placeholder, source |
| cost/executability | 비용 후 실행 가능성 | 호가, 예상 impact, 최소 기대폭 |

`score_current`는 `movement_intensity_score`로 이름과 권한을 바꾸는 것이 맞다.

- 후보 탐색: 사용 가능
- 매수 정렬: 직접 사용 금지
- 과열 penalty 입력: 사용 가능
- 주문금액 결정: 검증 전 금지

### 12.3 bucket multi-label

예:

```json
{
  "labels": [
    "liquidity_leader",
    "volume_surge",
    "momentum_now"
  ],
  "primary_bucket": "liquidity_leader",
  "risk_labels": ["overheat"]
}
```

평가는 label 단독과 조합으로 한다.

- liquidity only
- liquidity + volume surge
- liquidity + overheat
- near breakout + VWAP confirm
- pullback + reclaim

현재 single primary bucket만 보면 좋은 유동성과 나쁜 과열의 상호작용이 사라진다.

### 12.4 KR prompt shadow

Control:

```text
현 raw order + cap 28
```

Treatment B:

```text
trainer rank + cap 28
```

Treatment C:

```text
source/provider rank
+ trainer rank의 제한적 blend
- movement intensity overheat penalty
+ data quality gate
```

Treatment B를 바로 운영하지 말고 C와 함께 shadow로 비교해야 한다. 이번 표본에서 trainer Q1/Q2는 좋았지만 hard cap paired 개선이 안정적이지 않았기 때문이다.

## 13. US 개선 설계

### 13.1 source lane 분리

| source | 역할 | 허용되는 판단 |
|---|---|---|
| day_gainers | continuation discovery | 추가 상승 확인 후 진입 |
| most_actives | liquidity universe | 별도 alpha 확인 없이는 boost 금지 |
| day_losers | mean-reversion research | reclaim/반전 확인 전 매수 금지 |
| mega_gap | exceptional watch | 표본 축적 전 자동 승격 금지 |

하나의 공통 top-24를 trainer 점수로 재정렬하지 말고 lane quota를 먼저 둔다.

예시 shadow:

```text
day_gainers 10
most_actives 8
day_losers/reversal 4
novelty/other 2
```

이 숫자는 운영값이 아니라 실험 시작값이다. source별 recall, 비용 후 precision, prompt conversion으로 재조정한다.

### 13.2 `vol_ratio=1.0` 제거

실제 값이 없을 때:

```text
vol_ratio=null
vol_ratio_quality=missing
vol_ratio_is_placeholder=false
```

실제 RVOL을 만들 수 있으면 다음을 사용한다.

- 동시간대 누적 거래량 / 과거 동일 시각 평균 누적 거래량
- 장 초반 projection의 신뢰구간
- premarket와 regular session 분리
- split/기업행사 보정

실값 확보 전에는 `volume_surge`나 continuation boost가 이 필드에 의존하면 안 된다.

### 13.3 US top-K 재설계

현재 실측:

- raw top-5: 세션 평균 -0.384%
- raw top-10: -0.498%
- raw top-24: -0.208%
- trainer top-24: -0.351%

따라서 US는 “점수를 조금 고쳐 순서를 바꾸는 문제”보다 “서로 다른 source 목적을 한 순위에 섞은 문제”가 먼저다.

US 승격 순서:

1. source lane
2. lane 내부 실제 RVOL과 continuation/reversal feature
3. 비용 후 60분 및 multiday label
4. lane별 prompt allocation
5. 마지막에만 통합 자본 allocation

## 14. 검증 실험 설계

### 14.1 처리군

| ID | 시장 | 처리 |
|---|---|---|
| A | KR/US | 현재 raw order baseline |
| B | KR | trainer reorder shadow |
| C | KR | trainer + overheat penalty + quality gate |
| D | US | source-lane quota |
| E | US | source-lane + point-in-time RVOL |

### 14.2 평가 단위

후보 건별 통계만 사용하면 같은 날의 시장 국면을 독립 표본으로 잘못 센다. 기본 단위는 세션이어야 한다.

- 세션별 top-K 평균
- 세션별 중위 수익
- 세션별 winner recall@K
- 세션별 opportunity regret
- 세션별 MAE/CVaR
- 세션별 prompt/action/fill/net conversion

통계는 session-stratified bootstrap으로 신뢰구간을 계산한다.

### 14.3 필수 지표

#### 데이터 건강성

- provider 성공률
- stale/cache 비율
- placeholder 비율
- top-K 라벨 커버리지
- 처리군 간 커버리지 차이
- candidate→fill attribution 완전성

#### 후보 분별력

- precision@K
- full-universe winner recall@K
- score decile monotonicity
- Spearman by session
- top-K turnover/stability
- source/bucket별 기회수익

#### 실행 성과

- prompt inclusion
- actionable
- order sent
- fill
- 비용 후 net PnL
- MAE/MFE
- drawdown/CVaR

### 14.4 승격 차단 조건

아래 중 하나라도 만족하면 enforce 승격을 중지한다.

- top-K 라벨 커버리지 < 90%
- control과 treatment 커버리지 차이 > 5%p
- live fill attribution < 100%
- point-in-time 위반 1건 이상
- placeholder가 rank에 영향
- 비용 후 세션 중위 delta ≤ 0
- MAE/CVaR가 baseline 대비 10% 초과 악화

### 14.5 승격 최소 조건

#### shadow → 제한 운영

- 최소 20개 성숙 세션
- 세션 중위 net opportunity delta > 0
- session bootstrap 95% CI 하한 ≥ 0
- 양수 세션 비율 ≥ 55%
- winner recall@K 상대 개선 ≥ 10%
- control/treatment 라벨 커버리지 차이 ≤ 5%p

#### 제한 운영 → enforce

- 최소 30건의 성숙 canonical fill
- 비용 후 net PnL 개선
- drawdown과 tail loss 비열화
- 후보→체결 attribution 100%
- market별 독립 승격

KR이 통과해도 US는 자동 승격하지 않는다.

## 15. 실행 로드맵

### P0 — 측정 가능성 복구

1. candidate→decision→route→order→fill ID 연결
2. canonical fill 11건 backfill 및 누락 사유 기록
3. `vol_ratio=1.0` placeholder를 missing으로 분리
4. KR zero-price fallback을 watch-only로 격리
5. `.env.example`과 production 기본값 정렬
6. sub-screener `success_count` 용어 분리

### P1 — full-universe와 라벨 커버리지

1. KR/US point-in-time universe 저장
2. 후보와 비후보 모두 30m/60m/close/multiday label 생성
3. missing label reason 저장
4. top-K 처리군별 coverage parity 검사

### P2 — 시장별 shadow

1. KR A/B/C 동시 기록
2. US A/D/E 동시 기록
3. 세션별 paired 결과와 bootstrap CI 생성
4. score version, feature hash, selection reason 저장

### P3 — 제한 운영

1. 시장별 독립 flag 추가
2. 매우 작은 allocation으로 제한
3. canonical fill 기준 비용 후 성과 확인
4. risk/tail 비열화 시 자동 rollback

권장 flag 구조:

```text
KR_CANDIDATE_PROMPT_POOL_REORDER_MODE=shadow|limited|enforce
US_CANDIDATE_PROMPT_POOL_REORDER_MODE=shadow|limited|enforce
KR_CANDIDATE_REORDER_POLICY=raw|trainer|trainer_overheat
US_CANDIDATE_REORDER_POLICY=raw|source_lane|source_lane_rvol
```

## 16. 지금 수정해야 하는 것과 아직 수정하면 안 되는 것

### 바로 수정 대상

- 후보→canonical fill attribution
- placeholder와 실값 구분
- provider/cache/fallback timestamp 계약
- KR preopen zero-price 상태
- config example의 true/false 불일치
- sub-screener 운영 성공과 경제 성과 명칭 분리
- `score_current` 의미 명시 또는 이름 변경

### shadow로만 개선할 대상

- KR trainer reorder
- KR overheat penalty
- KR multi-label bucket
- US source-lane quota
- US point-in-time RVOL

### 지금 운영 적용하면 안 되는 것

- 전역 `CANDIDATE_PROMPT_POOL_REORDER_ENABLED=true`
- US trainer 점수 기반 top-24 enforce
- `vol_ratio=1.0`을 실제 거래량 비율로 간주한 boost
- KR `momentum_now` 자동 우선순위 상승
- US `pullback_watch` 자동 우선순위 상승

## 17. 한 줄 판단

현 방향은 “스크리너를 버리거나 Claude가 고른 종목을 바로 사는 것”이 아니다. 올바른 방향은 **스크리너를 시장별 discovery lane으로 유지하고, 점수의 권한을 제한한 채, 실제 소비 데이터와 체결 lineage를 복구한 뒤 세션 단위 반사실로 승격하는 것**이다.

현재 `false`는 맞다. 다만 영구적으로 false여야 한다는 뜻은 아니다. KR은 검증 가능한 후보가 있고, US는 먼저 source 구조를 바꿔야 한다. `true` 전환은 설정 변경이 아니라 market별 정책 승격의 결과여야 한다.

## 18. 재현

```powershell
python tools/screener_performance_review.py `
  --start-date 2026-07-01 `
  --end-date 2026-07-23 `
  --output docs/reports/verify_20260724/screener_performance_review_20260724.json

python -m pytest tests/test_screener_performance_review.py -q
```

검증 결과:

```text
4 passed
candidate first rows: 4,602
external API calls: 0
```

## 19. 해석 제한

- 16개 세션은 진단용이지 최종 승격 표본이 아니다.
- 로컬 분봉 커버리지는 KR 50.32%, US 39.55%로 낮고 비무작위다.
- 전체 시장 universe가 없어 winner recall을 아직 계산할 수 없다.
- 최초 관측 후 60분 gross 결과이며 실제 체결·비용 성과가 아니다.
- KR 극단 변동 종목의 영향으로 평균이 왜곡될 수 있다.
- prompt 포함/제외 결과는 관측 연구이며 인과효과가 아니다.
- canonical fill attribution을 복구하기 전에는 screener의 최종 수익 기여를 확정할 수 없다.
