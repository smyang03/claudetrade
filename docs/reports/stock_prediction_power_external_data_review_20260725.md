# 종목 예측력 및 외부 데이터 활용 상세 검토

- 기준일: 2026-07-25
- 범위: KR/US 후보 발굴 → 등록 → 재평가 → 매수 판단 → 사후 라벨
- 목적: 외부 데이터를 무작정 추가하는 것이 아니라, **실제로 매수 전 이용 가능했던 정보만으로 종목 간 순위를 개선할 수 있는지** 검증한다.
- 변경 범위: 본 문서는 분석·실험 설계이며 운영 매수 로직은 변경하지 않는다.

---

## 1. 최종 판단

현 시스템의 예측력 병목은 우선순위상 다음과 같다.

1. **데이터 부족보다 데이터 흐름과 시점 계약의 불완전성이 먼저다.**
   - 후보 호가 스냅샷 23,240건에 실제 bid/ask/spread가 한 건도 없다.
   - 60분 수익률 라벨은 KR 19.6%, US 49.6%만 확보됐다.
   - `time_normalized_rvol`은 계산·저장되지만 후보 정렬이나 매수 판단에서 소비되지 않는다.
   - 외부 earnings/disclosure 파일은 존재하지만 후보 원장에 거의 결합되지 않는다.
2. **하나의 “매수 확률”로 모든 시계를 예측하는 방향은 맞지 않는다.**
   - 전일·프리마켓 정보는 “오늘 관찰할 종목”을 고르는 데 적합하다.
   - 시초 5분 가격·거래량·스프레드는 “지금 진입할지”를 고르는 데 적합하다.
   - 진입 후 15~30분 경로는 신규 진입 예측보다 “계속 보유할지, 실패로 볼지”에 더 적합하다.
   - 실적·공시 충격은 별도의 멀티데이 모델로 분리해야 한다.
3. **외부 데이터의 1순위는 뉴스 감성이 아니라 공식 이벤트 데이터다.**
   - US: SEC 제출·XBRL, 실적 발표 시각과 surprise, 섹터·일봉 상대 모멘텀.
   - KR: OpenDART 공시 종류·금액·상대 규모, 실적·자본조달·자사주, T-1 수급.
   - 뉴스는 방향성 점수가 아니라 사건 종류, 새로움, 예상 대비 충격, 직접성, 발표 시각으로 분해해야 한다.
4. **현재 상태에서는 어떤 신규 모델도 운영 매수 권한을 받을 근거가 없다.**
   - 먼저 실제 호가와 실행비용, 충분한 사후 라벨, point-in-time 결합, 세션 단위 반사실 검증을 확보해야 한다.
   - 그 후 단순 로지스틱/순위 합성 기준선을 이겨야 GBDT 등 비선형 모델을 검토할 수 있다.

따라서 권고 방향은 “더 많은 점수를 현재 총점에 추가”가 아니다.  
**관찰 종목 예측, 시초 진입 예측, 진입 후 경로 예측, 멀티데이 이벤트 예측을 분리하고 각 단계에 맞는 외부 데이터를 point-in-time 방식으로 연결하는 것**이다.

---

## 2. 현 시스템 실측

### 2.1 후보 원장과 라벨

`data/audit/candidate_audit.db`의 prospective registry를 기준으로 확인했다.

| 항목 | KR | US | 해석 |
|---|---:|---:|---|
| 최초 등록 후보 | 639 | 1,230 | KR 5세션, US 7세션 |
| 고유 종목 | 277 | 575 | 단기 검증에는 종목보다 세션 수가 더 부족 |
| 이벤트 행 | 6,344 | 38,748 | 파이프라인 재생 재료는 있음 |
| quote 명칭의 스냅샷 | 4,214 | 19,026 | 실제 bid/ask는 없음 |
| outcome 존재 | 178 | 810 | 후보 전체를 대표하지 못할 수 있음 |
| 30분 수익률 존재 | 123 (19.2%) | 601 (48.9%) | KR이 특히 심각 |
| 60분 수익률 존재 | 125 (19.6%) | 610 (49.6%) | promotion-grade 학습 불가 |

핵심 문제는 라벨 누락이 무작위라는 보장이 없다는 점이다. 예를 들어 거래가 활발하거나 후속 조회가 성공한 종목만 라벨이 남았다면, 현재 학습 데이터는 쉬운 종목과 생존한 종목에 편향된다. 따라서 AUC가 높아도 운영 후보 전체의 예측력이라고 볼 수 없다.

### 2.2 실행비용 데이터

`candidate_registry_quotes` 23,240행의 bid, ask, spread_bps 유효 커버리지는 모두 0%다. 현재 값은 실질적으로 후보 가격 스냅샷이며 true quote가 아니다.

이 상태에서 다음 계산은 신뢰할 수 없다.

- target-first와 stop-first 사이의 실제 체결 가능성
- 소형주·급등주의 진입 슬리피지
- KR/US별 순비용 차감 기대수익
- 높은 예측 확률이 넓은 스프레드를 상쇄하는지 여부
- 후보 간 executable ranking

예측력 개선의 P0은 모델이 아니라 **실제 bid/ask와 시점별 spread 수집**이다.

### 2.3 시초 피처 커버리지

2026-07-20 이후 screener 후보를 기준으로 확인한 값이다.

| 피처 | KR 421건 | US 829건 | 상태 |
|---|---:|---:|---|
| `time_normalized_rvol` | 21 (5.0%) | 108 (13.0%) | 생성되지만 소비 경로 없음 |
| `volume_ratio_open` | 128 (30.4%) | 175 (21.1%) | 소비되지만 커버리지 낮음 |
| `ret_5m` | 189 (44.9%) | 397 (47.9%) | 절반 이하 |
| `vwap_distance` | 129 (30.6%) | 175 (21.1%) | 결측이 후보군별 편향 가능 |
| 실제 spread | 0 | 0 | 진입 가능성 판단 불가 |

`runtime/time_normalized_rvol.py`는 같은 경과 분의 과거 누적 거래량 중앙값과 비교하며 이전 세션만 사용하도록 설계되어 있다. 그러나 `tools/pipeline_integrity_audit.py` 기준으로 이 값은 생성된 뒤 후보 정렬·라우팅에서 소비되지 않는다. 이는 “좋은 피처인가” 이전에 **배선이 끊긴 상태**다.

### 2.4 이미 있는 외부 데이터의 실제 결합률

| 데이터 | 파일 상태 | 후보 원장 반영 | 판단 |
|---|---|---:|---|
| 실적 캘린더 | US 1,498, KR 39 종목 | news/earnings count 양수: KR 0, US 2 | 파일 존재와 런타임 사용이 분리됨 |
| KR DART observer | 95종목, 105건 | observer tag: KR 23/639 | shadow 관측은 있으나 모델 입력·라벨 연결 부족 |
| 섹터 맵 | US 486, KR 0 | 파일 disabled | 섹터 상대수익 계산 불가 |
| KR 외국인/기관 수급 | 일별 파일 존재 | 후보 피처 경로 존재 | 이용 가능 시점과 예측력 재검증 필요 |

실적 캘린더는 방어 게이트나 프롬프트 태그로 일부 쓰일 수 있으나, 후보 원장에서는 실제 사건 정보가 거의 보이지 않는다. “파일을 가져왔다”와 “그 시점 후보의 판단에 들어갔다”를 같은 것으로 간주하면 안 된다.

### 2.5 현재 모델 결과가 말하는 것

`state/models/profit_path_KR.json`과 `profit_path_US.json`은 모두 shadow 상태다.

| 시장 | validation_n | AUC | ECE | selected_n | net LCB |
|---|---:|---:|---:|---:|---|
| KR | 4,149 | 0.538 | 0.0523 | 0 | 없음 |
| US | 6,414 | 0.638 | 0.0096 | 0 | 없음 |

US AUC가 상대적으로 높아도 실제 선택 후보가 0이고 순수익 하한이 없다. 즉 “분류 순서 일부를 맞힌다”와 “거래 가능한 양의 기대수익이 있다” 사이가 연결되지 않았다.

consensus shadow도 top-3 교집합 계약 때문에 사실상 항상 abstain한다.

- US 과거 재생 755건: 선택 0
- KR 과거 재생 955건: 선택 3, 라벨 2
- 최신 KR/US: 선택 0

abstention 자체는 안전장치지만, 현재 방식으로는 예측력의 유무도 검증하기 어렵다. 엄격한 교집합을 운영 완화하는 것이 아니라, 동일 데이터로 **백분위 rank fusion + 합의 마진** challenger를 별도 사전등록해야 한다.

### 2.6 이전 예측력 논쟁에서 이미 확인한 경고

`docs/reports/prediction_power_debate_20260723.md`의 정정 결과는 중요하다.

- `ret_5m` 대박 예측 AUC 0.703은 편향된 48건에서 나온 값이었다.
- 원장 복구 후 203건에서는 0.557로 하락했다.
- 진입 시점 승/패 예측의 최강 단일 피처도 AUC 0.591이었다.
- trainer score는 AUC 0.477이었다.
- 30분 경로는 이후 경로와 관련이 있지만, 이는 진입 전 예측이 아니라 이미 발생한 가격 반응이다.

외부 피처를 추가할 때도 같은 오류가 재발할 수 있다. 소수 라벨에서 보인 높은 AUC를 근거로 운영 점수에 더하면 안 된다.

---

## 3. 예측 목표를 다시 정의해야 하는 이유

### 3.1 연구 결과의 시계와 운영 시계가 다르다

중장기 모멘텀은 강한 자산가격 연구 축이지만, 대표적 모멘텀 연구는 과거 3~12개월 성과와 이후 보유수익을 다룬다. 이것을 시초 5~60분 매수 신호로 바로 옮길 수는 없다. [Jegadeesh and Titman, 1993](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1993.tb04702.x)

52주 신고가 근접도 역시 가격 수준에 내재한 기준점과 이후 모멘텀을 설명하는 일봉 피처이지, 단독으로 당일 target-first를 보장하지 않는다. [George and Hwang, 2004](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.2004.00695.x)

비정상 고거래량 뒤의 수익 프리미엄도 일·주 단위 거래량 충격과 이후 월 단위 수익을 다룬다. 따라서 `time_normalized_rvol`의 당일 진입 효과는 현 데이터에서 별도로 검증해야 한다. [Gervais, Kaniel, and Mingelgrin, 2001](https://onlinelibrary.wiley.com/doi/10.1111/0022-1082.00349)

5분 Opening Range Breakout과 “Stocks in Play”를 결합한 최근 연구는 US 대규모 종목군에서 거래비용 포함 성과를 보고한다. 다만 SSRN working paper이고 데이터·체결 가정이 현 시스템과 다르므로, 아이디어의 근거이지 운영 투입의 증거는 아니다. [Zarattini, Barbon, and Aziz](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284)

### 3.2 모델의 역할을 네 개로 나눈다

| 단계 | 판단 시각 | 예측 대상 | 적합한 입력 | 결과 |
|---|---|---|---|---|
| A. 관찰 후보 | T-1~프리마켓 | 오늘 유의미한 움직임·유동성이 생길 가능성 | 실적/공시, 일봉 모멘텀, 52주 고점, T-1 수급, 섹터 | watch priority |
| B. 진입 후보 | 시초 +5분 | 30/60분 target-first, 비용차감 초과수익 | 시간정규화 RVOL, ORB, VWAP, 실제 spread, 시장/섹터 | entry probability/rank |
| C. 경로 관리 | 진입 후 +15/+30분 | 지속/실패/반전, MFE·MAE, time-to-hit | 진입 후 가격·거래량·호가 경로 | hold/trim/exit evidence |
| D. 이벤트 멀티데이 | 발표 직후~수일 | 1~5일 비정상수익·갭 유지 | surprise, guidance, 공시 규모, 일봉 추세 | event sleeve rank |

단계 C의 높은 예측력을 단계 B의 진입 능력처럼 보고하면 안 된다. 각 모델은 `available_at`이 다른 독립 모델이어야 한다.

---

## 4. 권고 예측 구조

### 4.1 단일 총점 대신 조건부 기대값

최종 진입 판단은 다음 구성으로 분해한다.

```text
P(liquid_and_observable | T-1/preopen)
× P(target_first | open+5, candidate is observable)
× E(excess_return_after_cost | target/path class)
× confidence_and_coverage_gate
```

여기에서:

- `liquid_and_observable`은 당일 관찰 가치다.
- `target_first`는 현재 `candidate_path_prediction_lab.py`와 연결할 수 있다.
- `excess_return_after_cost`는 시장·섹터 수익과 실제 스프레드·슬리피지를 차감해야 한다.
- 결측, OOD, drift, 낮은 calibration 표본은 점수를 0으로 만드는 대신 abstain 사유로 기록한다.

### 4.2 시장·소스별 모델

아래 모델을 섞지 않는다.

- KR screener
- KR Claude/judge
- KR disclosure event
- US screener
- US Claude/judge
- US earnings/SEC event
- US 멀티데이 sleeve

같은 피처라도 시장별 의미와 비용이 다르며, 후보 생성기가 다르면 prior probability도 다르다. 전역 scalar 하나로 합치면 어느 lane에서 성능이 발생했는지 알 수 없다.

### 4.3 기준 모델

표본이 적은 현재 단계의 순서는 다음이 적합하다.

1. 규칙 기반 기준선
2. 표준화 로지스틱 또는 제한된 monotonic score
3. 백분위 rank fusion
4. 충분한 독립 세션이 쌓인 뒤 shallow GBDT
5. 그 후에만 더 복잡한 모델 검토

Gu, Kelly, Xiu는 자산가격 예측에서 tree·neural network의 비선형 상호작용 이점을 보고하고 모멘텀, 유동성, 변동성 계열을 중요한 신호로 찾았다. 그러나 큰 패널과 엄격한 out-of-sample 설정의 결과다. 현 시스템처럼 독립 세션과 완전 라벨이 부족한 상태에서 복잡도를 먼저 올리는 근거는 아니다. [Gu, Kelly, and Xiu, NBER](https://www.nber.org/papers/w25398)

---

## 5. US 외부 데이터 우선순위

### 5.1 Tier 0: 이미 있는 데이터의 배선 복구

| 데이터 | 현재 문제 | 만들 피처 | 적용 단계 |
|---|---|---|---|
| earnings calendar | 후보 원장 결합 거의 없음 | D-day, 발표 전/후, BMO/AMC, estimate 존재 여부 | A/D 및 위험 게이트 |
| time-normalized RVOL | 생성 후 미소비 | RVOL level, 가속, 5분 지속성 | B |
| sector map | disabled | 종목-섹터 초과수익, 섹터 RVOL/추세 | A/B/D |
| true quote | 부재 | spread, depth proxy, quote age | B 및 비용 |
| outcome | 절반 이하 | 30/60m path, 1~5d abnormal return | 모든 검증 |

이 다섯 항목이 외부 데이터 신규 구매보다 먼저다.

### 5.2 Tier 1: 무료·공식 데이터

#### SEC EDGAR 제출 및 XBRL

SEC의 `submissions`와 XBRL API는 인증키 없이 기업 제출 이력과 구조화 재무 데이터를 제공하며, 실시간 갱신 및 bulk 파일도 제공한다. [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)

우선 이벤트:

- 8-K: 실적, 자금조달, M&A, 경영진 변화, 중요 계약
- 10-Q/10-K: 매출·이익·현금흐름·부채 변화
- Form 4: 내부자 거래
- S-1/424B/ATM 관련: 공급 증가와 희석 가능성

필수 파생 피처:

```text
form_type
item_codes
filing_age_minutes
is_amendment
event_category
dilution_flag
insider_direction
amount_to_market_cap
revenue_growth_yoy
operating_margin_delta
cash_to_debt
```

주의:

- filing 문서의 회계 기간과 제출 시각을 분리한다.
- amendment를 원 제출과 별도 revision으로 저장한다.
- 8-K “긍정/부정” 총점보다 사건 종류와 규모가 우선이다.

#### 실적 surprise와 발표 시각

필수 값:

- 발표 시각: BMO/AMC/intraday
- EPS actual-estimate 차이와 estimate 절댓값 정규화
- revenue surprise
- guidance up/down/withdrawn
- 전일 close 대비 발표 후 gap

실적 발표일에는 평균 수익 패턴과 위험 구조가 일반일과 다를 수 있다. 따라서 실적을 단순 보너스로 넣기보다 별도 event lane 또는 regime으로 분리하는 편이 타당하다. [Savor and Wilson, 2016](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12361)

#### 일봉 가격·거래량과 섹터

무료로 가장 먼저 만들 가치가 있는 피처:

- 20/60/120일 모멘텀
- 52주 고점 대비 거리
- 20일 평균 대비 전일 거래량
- 갭 빈도와 갭 유지율
- 20일 실현변동성
- 시장 및 섹터 베타
- SPY/QQQ/섹터 ETF 대비 5/20일 상대수익

이 피처들은 시초 방향을 직접 단정하기 위한 것이 아니라 관찰 후보 prior와 sector-neutral label을 만드는 데 사용한다.

#### ALFRED/FRED vintage

거시 데이터는 종목 순위보다 위험 regime에 사용한다.

- 금리 변화
- 신용 스프레드
- VIX 계열
- 고용·물가 발표일
- 유동성/risk-on 상태

수정된 최신 값으로 과거를 학습하면 look-ahead가 발생한다. ALFRED의 real-time period/vintage를 저장해야 한다. [FRED real-time periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html)

### 5.3 Tier 2: 조건부 도입

#### 뉴스 이벤트

뉴스는 8장에서 별도로 정의한다. generic sentiment scalar는 주 피처가 아니다.

#### 애널리스트 revision

애널리스트 추천, 실적 전망 수정, 목표가 변경에는 정보가 포함될 수 있다. 다만 보통 상용 데이터가 필요하고 초단기보다 수일~수개월 horizon에 가깝다. 데이터 구입 전, 이벤트 시각과 원문 source를 보장할 수 있는지 확인해야 한다. [Bradshaw, NBER](https://www.nber.org/papers/w9246)

### 5.4 Tier 3: 우선 보류

#### 옵션

옵션 거래의 정보성은 단순 put/call ratio가 아니라 **매수자 주도 opening volume** 등 거래 방향과 개시 여부를 구분할 때 나타난다는 연구가 있다. 필요한 원천 데이터가 없다면 무료 집계치를 대체재로 쓰지 않는다. [Pan and Poteshman](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1096000)

#### FINRA short volume

FINRA 일별 short sale volume은 당일 오후 6시 이후 제공되며, 공공 시장에서 보고된 거래의 일부이지 short interest가 아니다. 당일 시초 모델에는 사용할 수 없고 T+1 관찰 피처로만 검토할 수 있다. [FINRA daily short sale volume](https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/daily-short-sale-volume-files), [FINRA 해석 주의사항](https://www.finra.org/rules-guidance/notices/information-notice-051019)

#### 소셜 감성

봇, 표본 선택, ticker ambiguity, 사후 삭제, API 변경 위험이 크다. 공식 이벤트와 뉴스 taxonomy의 추가가치를 증명한 뒤 검토한다.

---

## 6. KR 외부 데이터 우선순위

### 6.1 Tier 0: 기존 배선 복구

| 데이터 | 현재 문제 | 개선 |
|---|---|---|
| DART observer | shadow tag 일부만 반영 | 공시 시각·종류·금액·시가총액 비율을 후보/결과 원장에 결합 |
| earnings-like disclosure | 캘린더에는 있으나 후보 count 0 | 발표 전/후와 실제 수치를 분리 |
| 외국인/기관 수급 | 이용 시점·효과 불확실 | T-1 확정치만 사용, 누락 이유 기록 |
| sector map | KR 0 | KRX 업종 및 자체 테마 맵의 버전 고정 |
| 시초 거래량/호가 | 커버리지 낮고 true spread 없음 | KIS 원천 수신시각과 quote age 저장 |
| outcome | 60분 19.6% | 전 후보 고정 시각 조회로 복구 |

### 6.2 Tier 1: OpenDART 공식 공시

OpenDART는 공시 원문 및 재무정보 API를 제공한다. [OpenDART](https://opendart.fss.or.kr/intro/main.do), [OpenDART 재무정보 개발가이드](https://engopendart.fss.or.kr/guide/main.do?apiGrpCd=DE003)

우선 분류할 사건:

- 유상증자, 전환사채, 신주인수권부사채
- 자기주식 취득·처분
- 단일판매·공급계약
- 최대주주 변경
- 영업정지·회생·관리종목 관련
- 잠정 영업실적
- 합병·분할·인수
- 임상·허가·특허 관련 주요경영사항

공시가 긍정인지 부정인지 고정 사전으로 결정하지 않는다. 다음 상대 규모를 계산해야 한다.

```text
offering_amount / market_cap
contract_amount / trailing_revenue
buyback_amount / market_cap
earnings_surprise / abs(consensus)
ownership_change_pct
filing_age_minutes
is_correction
```

예:

- 공급계약은 계약명만 보면 긍정처럼 보이지만, 최근 매출 대비 1%와 80%는 다른 사건이다.
- 유상증자는 보통 희석 위험이지만 자금 목적, 할인율, 제3자배정 여부와 이미 반영된 갭을 함께 봐야 한다.
- 정정 공시는 최초 공시와 같은 신규 이벤트로 중복 계산하면 안 된다.

### 6.3 Tier 1: T-1 수급

KR 외국인·기관 수급은 다음 제약으로 사용한다.

- 당일 확정되지 않은 잠정치를 과거 학습에 넣지 않는다.
- 최소 T-1 확정치만 preopen 단계에서 사용한다.
- 거래대금 대비 순매수, 시가총액 대비 순매수로 정규화한다.
- 외국인과 기관을 별도 피처로 유지한다.
- 대형주와 소형주를 같은 임계값으로 평가하지 않는다.
- 수급은 직접 매수 명령이 아니라 관찰 prior 또는 상호작용 피처다.

과거 파일이 있다는 이유로 현재 시각에 이용 가능했다고 가정하지 말고, 원천 수신 시각을 별도로 기록해야 한다.

### 6.4 Tier 1: 업종·시장 상대수익

KR은 KOSPI/KOSDAQ 방향과 업종 동행성이 강한 날이 있다. 종목 원수익을 그대로 라벨로 쓰면 모델이 종목 선택이 아니라 시장 방향을 맞힌 것처럼 보일 수 있다.

필수 라벨:

```text
stock_return_60m - beta * market_return_60m
stock_return_60m - sector_return_60m
stock_return_1d/5d - sector_return_1d/5d
```

섹터 맵은 날짜별 버전을 저장하고 상장·이전·분할·종목코드 변경을 추적한다.

### 6.5 Tier 2

- 컨센서스 실적 revision: 라이선스와 timestamp가 보장될 때
- 종목별 대차·공매도: 실제 제공 지연을 반영한 T+1 이상 모델
- 테마/산업 공급망: 수동 매핑이 아니라 버전·근거·유효기간이 있는 그래프
- 뉴스: 공시 중복 제거 후 사건 taxonomy로만 추가

---

## 7. Point-in-time 데이터 계약

외부 데이터 한 건마다 최소 아래 필드를 저장한다.

```text
event_id
symbol_id
market
source
source_event_time
received_at
effective_at
available_at
revision_id
is_revision
payload_hash
event_type
quality
missing_reason
feature_version
raw_reference
```

### 7.1 시각 정의

- `source_event_time`: 거래소·공시기관·뉴스 원문에 기록된 사건 시각
- `received_at`: 우리 수집기가 실제 받은 시각
- `available_at`: 파싱과 검증이 끝나 모델이 사용할 수 있게 된 시각
- 학습 시 사용 가능 여부: `available_at <= decision_at`

`event_time <= decision_at`만 검사하면 늦게 수집한 정보를 과거에 사용할 수 있어 look-ahead가 생긴다.

### 7.2 수정 데이터

실적 actual, XBRL, DART 정정, 거시 통계는 나중에 바뀔 수 있다.

- 최초 관측값을 삭제·덮어쓰지 않는다.
- correction/revision을 새 행으로 저장한다.
- 과거 재생에서는 그 시점까지 받은 revision만 사용한다.
- 최신 정정값으로 과거 전체를 재작성하지 않는다.

### 7.3 결측 사유

0과 결측을 구분한다.

```text
not_applicable
not_published_yet
provider_failed
parse_failed
symbol_unmapped
late_arrival
rate_limited
stale
```

현재처럼 실제 뉴스가 없는 값과 수집 실패의 기본값이 같은 scalar로 들어가면 모델이 장애를 신호로 학습할 수 있다.

---

## 8. 뉴스 정보 재설계

### 8.1 왜 단순 부정 뉴스 감점이 위험한가

미디어의 비관적 어조는 단기 하락 압력과 이후 반전을 함께 만들 수 있다는 연구가 있다. 따라서 부정 점수는 horizon과 사건 종류 없이 고정 감점할 수 없다. [Tetlock, 2007](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.2007.01232.x)

일반 사전은 금융 문맥의 부정 단어를 잘못 분류할 수 있다. 금융 전용 어휘가 필요한 이유다. [Loughran and McDonald, 2011](https://doi.org/10.1111/j.1540-6261.2010.01625.x)

FinBERT 같은 금융 언어모델이 감성 분류를 개선할 수는 있지만, 감성 정확도가 바로 주가 방향 예측력이나 거래비용 차감 수익을 의미하지는 않는다. [FinBERT](https://arxiv.org/abs/1908.10063)

### 8.2 저장할 뉴스 피처

```text
event_type
source_tier
is_primary_source
novelty_24h
duplicate_cluster_id
entity_directness
materiality
expectedness
surprise_direction
sentiment
uncertainty
filing_overlap
published_at
received_at
age_minutes
market_session
```

우선순위:

1. 공식 공시 원문
2. 기업 IR/거래소
3. 통신사·신뢰 가능한 매체
4. 2차 재작성
5. 소셜

같은 공시를 20개 매체가 재작성해도 사건은 1개로 본다.

### 8.3 뉴스의 모델 역할

- 관찰 단계: 당일 움직임 가능성과 유동성 활성화
- 시초 진입 단계: 뉴스 후 실제 가격·거래량 확인과 상호작용
- 멀티데이 단계: surprise와 drift
- 위험 게이트: 거래정지, 희석, 임상 실패, 규제 사건

뉴스 단독 방향 점수로 즉시 매수하지 않는다.

---

## 9. 라벨과 비용 모델

### 9.1 진입 라벨

현재 first-passage 구조를 유지하되 다음을 함께 저장한다.

```text
TARGET_FIRST / STOP_FIRST / NO_TOUCH
return_30m
return_60m
market_excess_30m/60m
sector_excess_30m/60m
MFE_30m/60m
MAE_30m/60m
time_to_target
time_to_stop
```

원수익이 아니라 초과수익을 주 라벨로 삼아 종목 선택력과 시장 방향 노출을 분리한다.

### 9.2 실행비용

```text
entry_mid
entry_ask
exit_bid
spread_cost
estimated_slippage
fees_and_tax
market_impact_bucket
net_return
```

고정 비용률은 비교용 보조 시나리오로 남기되, promotion 판정은 실제 quote 기반 비용을 사용한다.

### 9.3 멀티데이 라벨

- close-to-close 1/2/3/5일
- event-time부터 다음 close
- 시장·섹터 비정상수익
- 최대 상승/하락폭
- 갭 유지/소멸
- 발표 전후 거래대금 변화

---

## 10. 검증 방법

### 10.1 검증 단위

행이 아니라 **거래 세션**이 독립 단위다.

- expanding walk-forward
- purge: 라벨 horizon 전체
- embargo: 인접 사건과 중복 종목 오염 방지
- 동일 종목·동일 뉴스 클러스터의 fold 교차 금지
- session block bootstrap 신뢰구간

### 10.2 필수 지표

| 목적 | 지표 |
|---|---|
| 순위 | Spearman rank IC, precision@K |
| 놓친 기회 | winner recall@K, opportunity regret |
| 확률 | Brier, ECE, reliability plot |
| 거래 | 비용차감 net, median session delta |
| 위험 | MAE, CVaR, drawdown |
| 데이터 | label/feature coverage, late-arrival, stale |
| 선택 | abstain rate, selected_n, source별 attribution |

winner recall은 전체 후보의 라벨이 충분히 있어야 한다. 현재 KR 19.6% 라벨로는 “최고 종목을 놓쳤는지” 판단할 수 없다.

### 10.3 다중 검정

많은 피처와 임계값을 시험하면 우연히 높은 성과가 나온다. Harvey, Liu, Zhu는 새 요인의 통계적 허들을 전통적인 수준보다 높여야 한다고 지적한다. [Harvey, Liu, and Zhu, NBER](https://www.nber.org/papers/w20592)

실험마다 다음을 고정한다.

- 가설 ID
- 피처군
- 방향 가설
- 대상 시장·lane·horizon
- primary metric
- 종료 세션 수
- 후보 임계값
- 실패 판정

실험 전체 수를 ledger에 남기고 Deflated Sharpe Ratio를 보조 판정으로 사용한다. [Bailey and López de Prado](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)

### 10.4 승격 게이트

신규 피처/모델의 최소 shadow 승격 조건:

1. 독립 세션 최소 30, 모델 비교는 60세션 권고
2. 양 arm의 top-K 라벨 커버리지 90% 이상
3. 양 arm 커버리지 차이 5%p 이하
4. median session net delta > 0
5. session bootstrap 95% 하한 >= 0
6. winner recall@K 상대 개선 10% 이상
7. CVaR/MAE 악화 10% 이내
8. source → feature → decision → outcome attribution 100%
9. live authority 전 mature fill 최소 30건
10. 시장·source별 조건 충족, 전역 승격 금지

---

## 11. 실제 데이터 주입 시나리오

파이프라인 하네스에 아래 케이스를 넣어 “좋아 보여야 하는 종목이 어디서 누락되는지”와 “나빠 보여야 하는 종목이 왜 통과하는지”를 확인한다.

### 11.1 US

| 케이스 | 주입 데이터 | 기대 |
|---|---|---|
| 실적 beat + guidance down | EPS 양, guidance 음, AMC | 단순 긍정 금지, event lane에서 혼합 사건 |
| 8-K 자금조달/ATM | dilution flag, 시총 대비 규모 | 일반 뉴스보다 희석 위험 우선 |
| 고 RVOL + ORB 상향 + tight spread | +5분 실제 호가/체결 | 진입 모델 상위, 비용 후에도 양수여야 함 |
| 고 RVOL + wide spread | 동일 가격 신호, 큰 spread | executable rank 하락 또는 abstain |
| 오래된 호재 재탕 | 중복 cluster, novelty 0 | 후보 보너스 없음 |
| sector 급등 동행 | 종목 +3%, sector +3% | 원수익은 높아도 초과수익은 낮음 |
| SEC 제출 지연 수신 | event_time 과거, received_at 늦음 | 과거 decision 재생에서 사용 금지 |
| Form 4 내부자 매수 | 소액/대액 두 버전 | 시총 대비 규모에 따라 차등 |

### 11.2 KR

| 케이스 | 주입 데이터 | 기대 |
|---|---|---|
| 유상증자 | 발행액/시총, 할인율, 방식 | 희석 사건 lane, 일반 감성으로 상쇄 금지 |
| 공급계약 | 최근 매출 대비 1%/80% | 같은 event type 안에서 materiality 차등 |
| 장후 잠정실적 | 발표 시각 16:30 | 당일 장중 모델 사용 금지, 다음 세션 prior |
| 정정 공시 | original + correction | 중복 보너스 금지, revision chain 유지 |
| 외국인 순매수 + 시초 붕괴 | T-1 수급 양, ORB 하향 | 관찰 prior와 진입 증거 분리 |
| 시장 동반 급등 | 종목 +4%, 업종 +4% | 종목 초과수익 낮게 계산 |
| true spread 급확대 | 후보 점수 높음, 호가 악화 | 매수 ceiling 제한 |
| 종목코드 매핑 실패 | DART 기업코드 존재 | silent 0 금지, `symbol_unmapped` 기록 |

### 11.3 하네스 강제 검증

각 케이스마다 다음 열을 출력한다.

```text
source_seen
event_parsed
feature_emitted
registry_persisted
model_consumed
rank_changed
gate_changed
decision_changed
order_eligible
outcome_labeled
missing_reason
```

단순히 코드에 함수가 존재하는지를 확인하지 않고, 주입값 변화가 최종 decision까지 0이 아닌 효과를 내는지 A/B로 검증한다.

---

## 12. 실행 로드맵

### P0 — 예측 가능성의 기반 복구

운영 판단 변경 없이 진행한다.

1. 후보 원장에 실제 bid/ask/spread/quote_age 저장
2. 모든 최초 등록 후보의 +5/+30/+60분 및 EOD 결과 수집
3. KR/US 섹터 맵 활성화와 버전 저장
4. earnings/DART event를 registry first/event snapshot에 point-in-time join
5. RVOL·VWAP·ret_5m 누락 사유 계측
6. feature generated/consumed/persisted 카운터 추가

완료 기준:

- true spread coverage 90% 이상
- 60분 outcome coverage 90% 이상
- 핵심 +5분 피처 coverage 85% 이상
- silent default 0건

### P1 — 현재 피처의 실제 추가가치

시장·source별로 아래 nested model을 비교한다.

```text
B0: 기존 score/rank
B1: B0 + true execution
B2: B1 + time-normalized RVOL/ORB/VWAP
B3: B2 + sector-relative daily context
```

한 번에 하나의 피처군만 추가한다. strict top-3 consensus와 rank-fusion challenger를 같은 라벨·같은 top-K로 비교한다.

### P2 — 공식 이벤트 데이터

```text
US: SEC + earnings surprise
KR: DART event taxonomy + relative materiality
```

관찰 단계와 멀티데이 단계부터 shadow로 시작한다. 즉시 intraday 매수 보너스로 넣지 않는다.

### P3 — 뉴스 사건화

1. 공시/IR 중복 제거
2. event taxonomy
3. novelty/materiality/directness
4. generic sentiment의 추가가치 ablation

`event only`가 `event + sentiment`보다 낫다면 sentiment를 제거한다.

### P4 — 조건부 상용 데이터

P0~P3 후에도 정보 공백이 확인될 때만:

- 애널리스트 estimate/revision
- 정교한 옵션 opening-flow
- KR 컨센서스·대차 데이터

구매 전 shadow 예상가치를 산정하고 데이터 시각·revision·라이선스·과거 복원 가능성을 계약 요건으로 둔다.

---

## 13. 우선 실험 목록

| ID | 가설 | 시장 | 단계 | 판정 |
|---|---|---|---|---|
| EXP-01 | true spread를 넣으면 고점수 비실행 후보가 제거된다 | KR/US | B | 비용차감 net, false-entry 감소 |
| EXP-02 | time-normalized RVOL이 기존 volume ratio보다 추가 정보를 준다 | KR/US | B | Δrank IC, Δnet |
| EXP-03 | sector excess label이 시장 베타를 제거하고 종목 선택력을 낮지만 정직하게 만든다 | KR/US | B/D | raw vs excess 성능 |
| EXP-04 | earnings는 일반 후보 보너스보다 별도 event lane에서 낫다 | US | A/D | event net, calibration |
| EXP-05 | DART 상대 규모가 event type만 쓸 때보다 낫다 | KR | A/D | Δrank IC, tail loss |
| EXP-06 | strict top-3 intersection보다 rank fusion이 커버리지를 늘리면서 순수익을 보존한다 | KR/US | B | selected_n, net LCB |
| EXP-07 | 뉴스 sentiment는 taxonomy/novelty 이후에도 추가가치가 있다 | KR/US | A/D | ablation |
| EXP-08 | T-1 수급은 KR 관찰 prior에는 유효하지만 +5분 가격 증거를 대체하지 못한다 | KR | A/B | 단계별 Δmetric |

가장 먼저 할 실험은 EXP-01이 아니라 P0의 quote 수집 정상화다. spread가 0%인 상태에서는 EXP-01 자체가 실행되지 않는다.

---

## 14. 하지 말아야 할 것

1. 외부 피처마다 임의의 `+5/-5` 점수를 현재 총점에 추가
2. 최신 정정값으로 과거 공시·실적·거시 데이터를 덮어쓰기
3. generic negative news를 시장·horizon 구분 없이 감점
4. 30분 반응 피처를 진입 전 예측력이라고 표현
5. AUC만 보고 운영 승격
6. US에서 유효한 피처를 KR에 전역 적용
7. label이 있는 후보만 골라 회고 분석
8. top-3 교집합이 0을 선택한다고 임의로 조건 완화
9. options put/call, FINRA short volume, 소셜 감성을 이름만 비슷한 대체 데이터로 사용
10. Claude의 종목 판단을 라벨로 학습하고 다시 Claude보다 낫다고 평가

---

## 15. 결론

현 시스템의 종목 예측력을 가장 크게 개선할 가능성이 있는 순서는 다음이다.

1. **실제 호가와 전 후보 outcome을 복구한다.**
2. **이미 계산 중인 시초 피처와 외부 이벤트를 후보 원장과 최종 판단에 끝까지 연결한다.**
3. **관찰·진입·경로관리·멀티데이 모델을 분리한다.**
4. **KR/US 각각 공식 공시와 실적 데이터를 사건 종류·상대 규모·발표 시각으로 구조화한다.**
5. **섹터 초과수익과 실제 비용으로 예측력을 재정의한다.**
6. **세션 단위 walk-forward와 coverage parity를 통과한 피처만 shadow 승격한다.**

외부 데이터는 분명 도움이 될 수 있다. 그러나 현 단계에서 가장 높은 기대가치는 새로운 데이터 공급자가 아니라 **이미 확보한 데이터가 매수 전 시점에 정확히 연결되고, 이후 모든 후보에 동일하게 라벨링되는 구조**에 있다.

그 기반을 만든 뒤 우선 검증할 신호는 다음 세 가지다.

- US/KR 공통: 시간정규화 RVOL + ORB + 실제 spread
- US: SEC/실적 사건 + surprise + 섹터 상대수익
- KR: DART 사건 + 시가총액/매출 대비 상대 규모 + T-1 수급

이 세 축이 세션 단위 순수익 하한을 만들지 못하면, 뉴스 감성·옵션·소셜 데이터를 추가해도 예측력보다 복잡도와 다중검정 위험만 커질 가능성이 높다.
