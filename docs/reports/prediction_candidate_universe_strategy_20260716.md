# 예측력·후보군 강화 통합 설계 (2026-07-16)

## 0. 결론

현재 시스템의 예측력을 높이는 올바른 방향은 모든 피처를 한 모델에 넣는 것이 아니다.

> **후보 생성기를 다양화하고, 각 생성기별 전문가 모델을 분리한 뒤, 서로 독립적인 모델이
> 동의할 때만 거래하는 고정밀·저빈도 구조**가 가장 적합하다.

이번 검증에서 기존 피처에 일봉 모멘텀·잔차 모멘텀·변동성·유동성·KR 수급 피처를 한꺼번에
추가하자 KR 성능은 오히려 악화됐다. 반면 기존 모델과 확장 모델의 top-3 교집합은 US 4건,
KR 6건의 작은 표본에서 전부 양수였다. 이 교집합은 사후 발견이라 즉시 주문 근거는 아니지만,
다음 prospective shadow의 가장 유망한 사전등록 후보다.

## 1. 이번에 직접 검증한 사실

### 1.1 라벨 계약

- 입력: `data/analysis/candidate_path_labels_lag5_v1.csv`
- 후보: 라이브 시장/세션/티커별 첫 관측
- 진입: 같은 거래일의 개장+5분 이후 첫 완전 1분봉 시가
- 목표: 60분 안에 +3.6%가 -2.5%보다 먼저 도달
- 동일 봉 양쪽 도달: STOP_FIRST
- 비용: US 0.50%, KR 0.21%
- 미래 날짜 분봉을 붙이는 교차세션 행: 0

원본 `candidate_path_labels_v1.csv`에는 교차세션 오염 행이 남아 있으므로 판정 입력으로 쓰면
안 된다. `lag5` 라벨만 현재 유효하다.

### 1.2 US 고정 7월 홀드아웃

| Arm | top-3 평균 net | 세션 LCB | ex-top3 | 판정 |
|---|---:|---:|---:|---|
| 기존 combined logit | -0.831% | -1.648% | -1.322% | 기각 |
| 일봉 피처만 logit | -0.724% | -1.486% | -1.201% | 기각 |
| 기존+일봉 logit | -0.635% | -1.261% | -1.102% | 기각 |
| 기존+일봉 shallow forest | -0.358% | -0.977% | -0.790% | 개선됐지만 음수 |

일봉·잔차 모멘텀과 비선형 상호작용은 손실을 줄였지만 양수로 뒤집지 못했다. 따라서 US
단일 path-ranking 모델은 현재 enforce 대상이 아니다.

### 1.3 KR 고정 7월 홀드아웃

| Arm | top-3 평균 net | 세션 LCB | ex-top3 | 판정 |
|---|---:|---:|---:|---|
| 기존 system-score binary logit | +1.105% | +0.515% | +0.898% | 관찰 후보 |
| system-score+일봉+KR rich logit | -0.628% | -1.476% | -0.994% | 기각 |
| system-score+일봉+KR rich forest | +0.275% | -0.608% | -0.009% | 기각 |
| 기존 combined+일봉+KR rich forest | +0.654% | -0.099% | +0.405% | 하한 미달 |

KR에서는 기존 system-score가 가장 강했다. 상대강도·수급·거래대금 피처는 정보가 없어서가
아니라, 현재 짧은 표본에서는 모델에 그대로 합칠 때 잡음을 추가했다.

### 1.4 단순 필터도 기존 KR arm을 개선하지 못함

기존 KR system-score top-3에 train 구간 사분위로 만든 다음 필터를 적용했다.

- 5일 상승률 상위 25% 제거
- 잔차 5일 상승률 상위 25% 제거
- 20일 고점 근접 종목 제거
- 과도한 갭 제거
- RS20 상위 과열 제거
- direct catalyst 제거
- low/mid liquidity만 허용

대부분 평균 또는 LCB가 하락했다. 현재 KR arm은 임의의 anti-chase 필터를 덧붙이지 않고
동결하는 것이 낫다.

### 1.5 새로 발견한 모델 합의형 정밀 arm

| 합의 조건 | n / 세션 | 평균 net | 양수율 | 상태 |
|---|---:|---:|---:|---|
| US 기존 logit top-3 ∩ 일봉 forest top-3 | 4 / 4 | +1.948% | 100% | 탐색적 |
| KR system-score top-3 ∩ rich forest top-3 | 6 / 5 | +3.390% | 100% | 탐색적 |

US는 TARGET 2건과 양수 NO_TOUCH 2건, KR은 6건 모두 TARGET_FIRST였다. 하지만 모델과
교집합 규칙을 같은 7월 데이터에서 본 사후 발견이고 표본이 매우 작다. 성과 주장이 아니라
**사전등록 prospective consensus shadow**로만 채택한다.

## 2. 현재 데이터 구조의 병목

### 2.1 outcome 선택편향

현재 분봉 라벨 커버리지는 후보 행 기준 약 74%지만, 고유 티커 기준은 US 약 43%, KR 약
41%다. 수집 대상이 counterfactual trigger 종목 중심이라 missing-at-random이 아니다.
모델보다 먼저 전체 후보 outcome을 수집해야 한다.

### 2.2 KR의 잠긴 피처

`logs/screener_quality`에는 다음 정보가 이미 있다.

- 5/20/60일 수익률
- KOSPI/KOSDAQ 대비 RS20/RS60
- 20일 변동성
- 거래대금·거래량의 20일 대비 비율
- 52주 고점 거리와 20일 낙폭
- 외국인·기관 1일 수급
- 유동성·상대강도·추세·수급·위험 품질 구성요소

opening 라벨 1,093행 중 RS20은 1,091행에 붙었다. 그러나 이 값들이 감사 DB의 first-class
컬럼이 아니어서 학습·서빙 계약과 drift 감시가 어렵다. immutable first snapshot에 정규화해
저장해야 한다.

### 2.3 섹터 정보 공백

`bucket_classifier`는 `sector_lagging_leader`를 지원하지만 실제 후보의 sector가 거의
비어 있다. 산업 모멘텀·동섹터 동시발화·리더/래거 전략을 판정할 기반이 없다.

### 2.4 이벤트 정보가 구조화되지 않음

현재 generic `direct_catalyst`는 US와 KR 모두 train/holdout에서 안정적 양수 신호가 아니다.
필요한 것은 뉴스 감성 추가가 아니라 다음의 구조화다.

- 공시 종류와 정확한 공개시각
- 실적·매출·가이던스 surprise
- 신규 계약·M&A·자금조달·자사주·임원매매
- 동일 사실의 재인용 여부와 뉴스 novelty
- 이벤트 전 기대치와 이벤트 후 가격 반응의 분리

## 3. 최종 예측 아키텍처

### Layer 0 — 편향 없는 후보 원장

모든 시장 후보에 대해 결정시점 first snapshot을 append-only로 등록한다.

- candidate id, 시장, 세션일, 티커, 최초 관측시각
- 당시 사용 가능한 모든 피처와 결측 mask
- 후보 생성기와 inclusion probability
- 모델/피처 schema hash
- 당일 분봉 outcome, 실제 quote, 가상 fill, stop slippage
- provider 누락·거래정지·VI·티커 정규화 실패 사유

장후에는 전 후보의 개장+5~65분 분봉을 수집하고, 라이브 중에는 모델 후보+고정 hash
대조군만 bounded quote를 수집한다.

### Layer 1 — 후보 생성기

하나의 day-gainer 풀에 의존하지 않고 다음 생성기를 별도 유지한다.

1. **기존 screener generator**: 현재 system-score의 기준선.
2. **structured event generator**: SEC/DART 공시, 실적·가이던스·계약·자사주·임원매매.
3. **sector/residual generator**: 산업 모멘텀, 동섹터 co-fire, 시장·섹터 제거 잔차 강도.
4. **microstructure generator**: opening imbalance, order-flow imbalance, spread/depth, KR VI 재개.
5. **crowding generator**: short interest/short volume, borrow/option skew. 단독 방향신호가 아니라
   squeeze·낙하 위험 상호작용으로 사용.
6. **quality/slow generator**: 재무 품질·내부자 순매수. 60분 arm이 아니라 1~20일 슬리브용.

각 생성기는 별도 데이터 계약·라벨·보유기간을 가진다. 서로 다른 horizon을 한 모델에 섞지 않는다.

### Layer 2 — 전문가 모델

1. **System expert**: 현재 점수·버킷·소스.
2. **Path expert**: TARGET/STOP/NO_TOUCH 경쟁위험과 도달시간.
3. **Event expert**: surprise·novelty·공개시각·초기 가격반응.
4. **Sector expert**: 산업 강도와 종목 잔차.
5. **Microstructure expert**: OFI·spread·depth·auction/VI.
6. **Horizon expert**: 60분, 종가, 1일, 5일 중 적합 route.

저신호 금융 데이터에서는 깊은 모델보다 규제된 logit과 얕은 tree/boosting을 기준선으로 쓴다.
복잡한 모델은 동일 prospective 원장에서 이길 때만 남긴다.

### Layer 3 — 합의·기권 meta-selector

모든 점수를 평균하지 않는다.

- 두 독립 전문가가 모두 top-k일 때 `CONSENSUS_PRECISION`
- 한 모델만 강하면 `SINGLE_EXPERT_SHADOW`
- 모델 방향 불일치·확률 미보정·데이터 결측이면 `ABSTAIN`
- 예상 net은 목표확률뿐 아니라 STOP/NO_TOUCH payoff, 비용, 실제 stop slippage를 포함
- 모델 disagreement 자체를 uncertainty로 기록

이번에 발견한 US/KR 교집합 arm은 이 구조의 첫 사전등록 후보다.

### Layer 4 — 포트폴리오·실행

- 후보 예측과 자본배분을 분리
- 세션·섹터·전략별 노출 상한
- 같은 섹터 다중 후보는 독립 거래로 표본 수를 부풀리지 않음
- spread/depth가 나쁘면 좋은 후보도 주문하지 않음
- isolated core·swing·PathB exit owner를 침범하지 않음

## 4. 전략별 검토

### A. 즉시 가치가 있는 전략

#### A1. Cross-model consensus precision

- US: 기존 combined logit과 일봉 shallow forest의 top-3 교집합
- KR: 기존 system-score와 rich shallow forest의 top-3 교집합
- 거래가 아니라 shadow scorer부터 시작
- 합의가 없으면 억지로 3개를 채우지 않음
- 장점: 빈도보다 precision을 우선해 낙하 체결을 직접 줄일 가능성
- 위험: n=4/6의 사후 발견. 신규 표본에서 쉽게 사라질 수 있음

#### A2. Competing-risk path prediction

현재 binary `목표 도달 여부`보다 다음을 동시에 예측한다.

- TARGET_FIRST 확률
- STOP_FIRST 확률
- NO_TOUCH의 60분 순손익
- 목표·손절 예상 도달시간
- 예상 MAE와 stop slippage

동일한 TARGET 확률이라도 STOP 위험과 NO_TOUCH 기대값이 다르면 순위가 달라진다.

#### A3. Route prediction

예측 질문을 “오를까?”에서 “어느 경로로 보유할까?”로 바꾼다.

- 60분 harvest
- 종가 청산
- overnight/1일
- 5일 uncapped runner

같은 후보를 모든 exit 규칙에 넣지 않고 예상 경로에 따라 owner를 고른다.

### B. 후보군 자체를 강화하는 신규 생성기

#### B1. Structured disclosure surprise

US는 SEC EDGAR의 실시간 submissions/XBRL, KR은 OpenDART를 이용한다.

- 8-K/10-Q/10-K 및 DART 주요사항보고서
- 매출·영업이익·EPS의 전년/전분기 변화와 시장 기대 대비 surprise
- 가이던스 상향/하향
- 계약금액/매출 비율, 증자·전환사채 희석, 자사주 규모
- Form 4 내부자 순매수와 10b5-1 여부

generic news와 별도 generator로 유지하고 60분·1일·5일 라벨을 각각 만든다.

#### B2. News novelty / stale-news veto

기사 제목과 본문을 최근 30일 종목별 뉴스 임베딩과 비교한다.

- 높은 novelty + 구조화 catalyst 일치: event expert 입력
- 낮은 novelty/재인용: 후보 승격 금지 또는 reversal 관찰
- 같은 기사 신디케이션 중복 제거

기존 `news_score`를 높이는 것이 아니라 “새 정보인가?”를 먼저 판정한다.

#### B3. Sector co-fire and residual momentum

- KRX 업종분류, SEC SIC/ETF mapping을 point-in-time cache로 구축
- 종목 수익률에서 시장·섹터 수익률을 제거한 residual 5/20/60일 강도
- 같은 세션 같은 섹터에서 독립 후보 2개 이상 발화한 경우 sector pulse
- 리더 지속과 미발화 laggard catch-up을 별 arm으로 분리

기존 US→KR 광역 sector-pulse 롱 arm은 기각됐으므로 그대로 재사용하지 않는다. 종목 수준
co-fire와 잔차 강도를 새로 판정한다.

#### B4. KR VI resumption

KRX VI는 2분 단일가 냉각 후 거래가 재개된다. 다음 라벨을 수집한다.

- VI 종류·발동시각·직전가
- 예상체결가·재개가·첫 1/3/5분 OFI
- 재개 후 +1.6/+2.3/+3.6%와 -2.5% first passage
- VI 중 주문잔량 불균형과 재개 spread

`VI 발생`만으로 매수하지 않고, 재개 후 수급 지속과 가격 수용 여부를 예측한다.

#### B5. Opening auction / order-flow imbalance

US는 가능하면 Nasdaq NOII 또는 브로커 depth, KR은 KIS 호가를 사용한다.

- bid/ask depth imbalance
- best quote OFI
- spread bps와 depth-adjusted spread
- opening auction imbalance와 indicative price 거리
- 체결·취소 흐름

이 축의 우선 목적은 방향 예측보다 낙하 체결과 나쁜 체결가를 제거하는 것이다.

#### B6. Crowding / squeeze interaction

- FINRA short interest와 daily short-sale volume은 다른 데이터로 분리
- short-sale volume을 short interest로 오해하지 않음
- short interest 변화, days-to-cover, off-exchange short-volume shock
- 옵션 IV skew/put-call/borrow proxy가 있으면 event·gap 신호와 상호작용

단독 매수신호보다 `positive catalyst × crowded short × liquid execution` 조건의 tail arm으로
검증한다.

### C. 낮은 우선순위 또는 별도 horizon

#### C1. 재무 quality

수익성·안전성·성장·희석·현금흐름은 60분 path보다 5~20일 후보 품질에 맞다. core/swing
후보 universe 정제에 사용하고 PathB opening 모델에 직접 합치지 않는다.

#### C2. 애널리스트 revision

실적전망·목표가·추천의 동시 상향은 이론적 후보지만 정확한 발표시각과 point-in-time
consensus 데이터가 필요하다. 무료 current snapshot을 과거 데이터처럼 쓰면 누수다. 데이터
계약을 확보하기 전에는 보류한다.

#### C3. 옵션 정보

IV skew와 call-put IV spread는 후보 정보가 될 수 있지만 완전한 OPRA/Greeks 역사 데이터는
대체로 유료다. 현재 자본과 거래빈도에서는 structured disclosure·sector·OFI보다 우선순위가
낮다.

## 5. 학습·검증 방법

### 5.1 모델

1. 규제 logit 기준선
2. 깊이 3~5의 shallow tree/boosting
3. 두 모델 확률의 calibration
4. market/session별 mixture-of-experts
5. 모델 합의와 기권

딥러닝은 전체 prospective 표본이 수만 건으로 커지고 얕은 모델을 OOS에서 이기기 전에는
사용하지 않는다.

### 5.2 누수 방지

- feature known_at과 event published_at을 모두 저장
- session group walk-forward
- label horizon이 겹치는 train 행 purge
- 최소 1세션 embargo
- point-in-time sector/재무/consensus 사용
- adjusted daily 가격의 미래 corporate-action 영향 감사
- train/serve 동일 builder와 schema hash

### 5.3 목적함수

AUC가 아니라 다음 순서로 판정한다.

1. 실제 비용·slippage 후 평균 net
2. 세션 bootstrap 5% 하한
3. 상위 3기여 제거
4. 월/반월 블록 부호
5. calibration ECE/Brier
6. 최대 drawdown과 연속 STOP
7. 기존 전략과의 손익 상관

## 6. 실행 우선순위

### P0 — 판정 기반

1. 전체 후보 immutable registry
2. 장후 전 후보 60분 outcome 수집
3. bounded live quote/fill 대조군
4. KR rich 피처를 audit DB first snapshot 컬럼/JSON schema로 정규화
5. outcome coverage·표본 증가·schema drift를 `/monitor`에 표시

### P1 — 즉시 shadow

6. US consensus precision V0 사전등록
7. KR consensus precision V0 사전등록
8. 기존 KR system-score V0 사양 동결
9. disagreement/abstain ledger
10. 실제 STOP slippage를 반영한 paired net

### P2 — 신규 후보 생성

11. SEC/DART structured event registry
12. sector/SIC/KRX industry point-in-time mapping
13. sector co-fire·residual momentum lab
14. news novelty/staleness label
15. KR VI resumption label
16. opening OFI/spread/depth observer

### P3 — 비용을 보고 선택

17. FINRA short/crowding interaction
18. SEC Form 4 insider overlay
19. options IV/skew 데이터 견적 후 ROI 판정
20. analyst revision 데이터 계약 검토

## 7. 승격 게이트

### 기존 top-3형

- 신규 30세션
- 최대 90 picks
- 전체 outcome coverage 90% 이상
- 실제 quote/fill net의 세션 LCB > 0
- 추가 비용 +0.25%p와 stop slippage 후 LCB > 0
- ex-top3 > 0, 월/반월 부호 유지

### consensus 정밀형

- 신규 15세션 이상
- 합의 pick 20건 이상
- 합의 없는 날을 0수익으로 포함한 포트폴리오 원장도 양수
- 모델별 단독 arm보다 precision·drawdown 개선
- 동일 섹터/동일 티커 집중 제거 후 양수
- 사전등록 이후 규칙 변경 0

둘 다 자동승격은 금지한다.

## 8. 최종 판단

1. **US:** 기존 후보 랭커를 확장해도 아직 음수다. 단일 모델 enforce는 금지한다. 서로 다른
   모델의 합의 arm과 structured event/sector/OFI 신규 후보 생성이 다음 경로다.
2. **KR:** 기존 system-score top-3가 현재 최선이다. rich 피처를 무작정 합치지 않는다.
   system-score와 독립 forest가 동시에 고른 정밀 arm을 prospective shadow로 검증한다.
3. **공통:** 예측력 부족의 상당 부분은 모델 문제가 아니라 후보 outcome·섹터·이벤트·호가
   데이터 계약의 공백이다. P0 수집이 해결되지 않으면 어떤 고급 모델도 같은 표본을 재채굴할
   뿐이다.
4. **우리만의 전략:** `다중 후보 생성기 → 전문가 모델 → 교집합 합의 → 경로별 exit owner`
   구조다. 매일 억지로 사는 시스템이 아니라 서로 다른 증거가 동시에 맞을 때만 공격하고,
   어느 증거도 확실하지 않으면 core sleeve로 남는 시스템으로 간다.

## 9. 재현 산출물

- `tools/candidate_universe_enhancement_lab.py`
- `reports/candidate_universe_enhancement_lab_20260716.json`
- `reports/candidate_universe_enhancement_picks_20260716.csv`
- 입력: `data/analysis/candidate_path_labels_lag5_v1.csv`

이번 작업은 분석·shadow 설계만 수행했으며 라이브 주문·설정·봇 프로세스는 변경하지 않았다.

## 10. 외부 근거

- Gu, Kelly, Xiu: 저신호 주식 예측에서 momentum·liquidity·volatility와 비선형 상호작용이
  중요하고, 데이터 규모상 shallow learning이 deep learning보다 유리할 수 있음:
  https://academic.oup.com/rfs/article/33/5/2223/5758276
- Residual momentum:
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2319861
- Industry momentum:
  https://onlinelibrary.wiley.com/doi/pdf/10.1111/0022-1082.00146
- Order-flow imbalance와 단기 가격변화:
  https://arxiv.org/abs/1011.6402
- 뉴스 staleness와 과잉반응:
  https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID1684648_code193383.pdf?abstractid=1018221
- SEC EDGAR API:
  https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC 내부자 거래 데이터:
  https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets
- OpenDART:
  https://opendart.fss.or.kr/intro/main.do
- KRX 투자자·업종·VI·공매도 데이터:
  https://data.krx.co.kr/
- KRX VI 제도:
  https://global.krx.co.kr/contents/GLB/06/0602/0602020204/GLB0602020204T7.jsp
- FINRA short-sale volume 주의사항:
  https://www.finra.org/investors/insights/short-interest
- Nasdaq opening cross/NOII:
  https://classic.nasdaqtrader.com/Trader.aspx?id=OpenClose
- Cboe options data:
  https://www.cboe.com/services/analytics/hanweck/historical_data/
