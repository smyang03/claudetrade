# 후보 경로 예측력 개선 분석 — 정정본 (2026-07-16)

> **최종 통합 판정은 `docs/reports/prediction_candidate_universe_strategy_20260716.md`를
> 따른다. 아래 §1~§10의 기존 US 양수 서술은 오류 경위를 보존하기 위한 역사 기록이며 현재
> 전략 권한으로 인용하면 안 된다.**
>
> **⚠️ 2차 정정 (2026-07-16 저녁) — 아래 본문의 US 수치도 무효.**
> 라벨러에 교차 세션 진입 버그가 있었다: 장전 후보(known < entry_floor)는 지연 상한 검사를
> 우회해, 당일 분봉이 없으면 **며칠 뒤 세션의 첫 봉**이 진입가가 됐다(US 홀드아웃 27픽 중
> 14픽 오염, 예: 7/2 후보 → 7/8 진입, 오염분 평균 +1.362% > 정상분 +0.971% = 부풀림 방향).
> `no_same_session_bar` 가드 + 전 후보 지연 상한(테스트 2건 추가)으로 수정하고 라벨·검증을
> 전량 재생성했다. 재생성 후 판정:
>
> - **US top-3: 기각.** 홀드아웃(9세션·27픽·교차세션 0) 평균 **−0.831%** / 승률 33% /
>   ex-top3 −1.322% / 세션 LCB −1.648%. TARGET base rate 22.7%→13.1%로 하락 — 본문의
>   양수는 전적으로 버그 산물이었다.
> - **KR Claude top-1: 기각 유지**(동률+티커 정렬 구조 문제는 라벨과 무관).
> - **KR system-score top-3: 수정 후 +1.105% / LCB +0.515 / 동률 0 / 집중 최대 8.3%로
>   양수 전환.** 단 같은 40~60세션을 세 번째 다시 본 결과라 사후선택이 누적됐다 —
>   **관찰 전용 유지**, prospective(P0) 신규 세션에서만 판정한다.
>
> 유지되는 결론은 하나다: 소표본에서 arm 판정이 라운드마다 뒤집힌 것 자체가 이 표본이
> 발견용이지 판정용이 아니라는 증거이며, **P0 prospective 수집이 유일한 진짜 다음 단계**다.

## 1. 최종 결론

예측 대상을 장기 방향에서 다음 경로 문제로 바꾼 접근은 유효하다.

> 주문 가능한 시점부터 60분 안에 목표 +3.6%가 손절 -2.5%보다 먼저 도달하는가?

다만 독립 검토를 반영해 실행 가능한 opening cohort만 다시 검증한 결과, 시장별 판정은 다음처럼 갈린다.

- **US:** `TARGET_FIRST / STOP_FIRST / NO_TOUCH` 세 상태의 기대손익을 계산해 개장 전 알려진 후보 중 top-3를 고르는 arm은 **SHADOW_CANDIDATE**로 유지한다.
- **KR Claude-route top-1:** **기각**한다. 개장 전 Claude 필드 대부분이 비어 점수가 동률이었고, 티커 오름차순 tie-break가 선택을 만든 착시였다.
- **KR system-score top-3:** 평균은 양수지만 세션 bootstrap 하한이 음수라 **관찰 전용**이다. 현재 KR에는 주문 후보로 승격할 path-prediction challenger가 없다.

라이브 주문 권한은 변경하지 않는다. 다음 우선순위는 모델이 아니라 편향 없는 prospective 수집이다.

## 2. 라벨·시점 계약

1. `audit_candidate_rows` live 행의 시장/세션/티커별 첫 관측을 사용한다.
2. 결정 중인 1분봉을 보지 않도록 후보시각 이후 첫 완전 분봉만 사용한다.
3. 진입가는 시장 개장+5분보다 이르지 않은 첫 분봉 시가다.
4. 동일 봉에서 목표와 손절이 모두 닿으면 `STOP_FIRST`다.
5. 비용은 US 0.50%, KR 0.21% 왕복을 차감한다.
6. 실제 entry delay와 `entry_vs_candidate_pct`는 학습 피처에서 제외한다.
7. post-open snapshot이 후보시각보다 늦으면 제외한다.
8. opening arm 검증에서는 `candidate_known_at <= market_open+5m` 후보만 사용한다. 그날 나중에 발견된 후보를 opening 순위에 넣지 않는다.

## 3. 생성 데이터

| 시장 | 원천 후보 | 개장+5분 라벨 | 60분 라벨 | +3.6% 결과 |
|---|---:|---:|---:|---|
| US | 8,912 | 3,484 | 3,475 | TARGET 788 / STOP 772 / NO_TOUCH 1,915 |
| KR | 6,424 | 3,010 | 3,006 | TARGET 1,143 / STOP 1,379 / NO_TOUCH 484 |

opening cohort는 전체 60분 라벨 중 US 72.5%, KR 80.9%였다. 기존 검증은 US 5개, KR 2개의 선택이 개장+5분 이후 발견 후보였으므로 정정했다.

## 4. 정정 검증 결과

### US — 유효한 shadow 후보

Arm: `US_CANDIDATE_PATH_OPENING_60M_T36_S25_MULTI_V0`

- 피처: 후보 첫 관측 시점의 기존 시스템 점수·시장/소스/Claude 상태 등.
- 목적: TARGET/STOP/NO_TOUCH 확률로 train-only 기대손익 계산.
- 선택: opening cohort 내 세션당 최대 top-3.
- 고정 7월 검증: 9세션, 27 picks.
- AUC 0.752.
- 비용 후 평균 +1.174%.
- 상위 3기여 제거 +0.933%.
- 세션 bootstrap 5% 하한 +0.586%.
- 추가비용 +0.25%p 후 하한 +0.336%, +0.50%p 후 +0.086%.
- 27 picks 중 고유 티커 24개, 최대 티커 비중 11.1%.
- top-3 경계 점수 동률 세션 0건.

STOP 체결가는 정확히 -2.5%로 계산했으므로 +1.174%는 손절 슬리피지 미반영 상한이다. 실제 quote/fill paired 검증 전에는 이 표현을 항상 병기한다.

US 선택 27건 중 `claude_trade_ready=1`은 0건이었다. 즉 기존 judge 선택과 거의 직교한 별도 랭킹이다. shadow ledger에 judge action, trade-ready, 실제 플랜 생성 여부와 겹침률을 기록해야 한다.

### KR Claude-route — 기각

- 고정 7월 top-1 평균 자체는 +0.848%였지만 하한 -0.677%.
- 12세션 모두 top-1 경계에 동률이 존재했다.
- 고유 선택 티커 7개, 한 티커 최대 비중 33.3%.
- 실제 선택은 비어 있는 Claude 필드와 티커 문자열 tie-break에 지배됐다.

따라서 `KR_CANDIDATE_PATH_..._CLAUDE_ROUTE_V0`는 challenger가 아니다. Claude 예측력의 증거로 인용하지 않는다.

### KR system-score — 관찰만

- opening cohort 고정 7월 top-3 평균 +0.621%.
- 상위 3기여 제거 +0.369%.
- 세션 bootstrap 하한 -0.226%.
- 경계 동률은 없고 고유 티커 분산도 양호하지만 비용·블록 안정성 기준 미달.

P0 데이터가 쌓이면 동일 사전등록 arm으로 재검증할 수 있으나 현재 주문 shadow의 주력 후보로 두지 않는다.

## 5. 데이터 편향

| 시장 | 후보 행 분봉 커버리지 | 고유 티커 커버리지 | 일별 최저 커버리지 |
|---|---:|---:|---:|
| US | 73.7% | 43.3% | 50.9% |
| KR | 74.8% | 40.8% | 45.9% |

분봉은 counterfactual trigger 종목 중심으로 수집되어 missing-at-random이 아니다. KR 후보품질 평균은 보유군 55.44, 누락군 39.08이고 US raw rank 평균은 보유군 37.55, 누락군 51.00이다. prospective 전체 수집에서 성적 하락을 기본 기대값으로 둔다.

## 6. P0 수집 범위 결정

최근 후보 규모는 KR 평균 약 115개/세션, US 약 186개/세션이다. 현재 라이브 evidence cache는 최대 30티커, 15초 timeout, KR 단일 worker이므로 라이브 중 전 후보 API 조회는 주문 경로와 경쟁한다.

따라서 `전체 vs 표본` 중 하나를 고르지 않고 다음 이중 계약을 사용한다.

### A. 결과 경로 — 장후 전체 후보 수집

- 결정시점에 전체 후보와 첫 feature snapshot을 immutable registry에 등록한다.
- 장후 counterfactual pipeline이 모든 등록 후보의 개장+5분~+65분 분봉을 수집한다.
- 기존 수집기는 최근 KR 59~85, US 76~99티커를 이미 장후 처리하며, post-close run은 약 4~7분이다.
- 전체 후보 확대는 라이브 주문시간이 아닌 장후 별도 job으로 실행한다.
- 예상 저장 증가는 월 수백 MB 수준으로 현재 저장공간에서 문제가 아니다.
- provider 실패, 누락, ticker normalization, trading halt를 개별 사유로 기록한다.

### B. 실제 진입 호가 — bounded 표본

- US: 모델 top-3 + ticker hash 고정 대조군 최대 17개, 총 20개.
- KR: 연구 후보 top-1 또는 관찰 top-3 + hash 대조군을 포함해 총 15개 이내.
- hash 표본은 세션일·시장·티커·고정 seed로 결정하고 inclusion probability를 기록한다.
- quote 수집은 live decision cohort를 변경하지 않는 observer-only sidecar이며, broker rate lock을 공유한다.
- scorer 후보는 항상 quote 우선수집 대상이지만 quote 실패가 기존 PathB/코어/us_swing 주문을 막아서는 안 된다.

이 구조는 전체 outcome 우주의 선택편향을 제거하면서 실시간 API 부하는 제한한다.

## 7. 필수 보완 계약

### 선택 원장

`reports/candidate_path_prediction_holdout_picks_20260716.csv`에 arm·날짜·티커·known_at·entry·결과·net·확률·rank score를 저장한다. 라이브 shadow도 동일 스키마의 append-only 일일 ledger를 사용한다.

### 세션 상한

- US는 세션당 최대 3개.
- 같은 세션 후보가 많아도 표본 수를 3개 이상으로 계산하지 않는다.
- 월/반월 블록 부호와 주당 신규 세션 수를 `/monitor`에 표시한다.

### 점수 동률 게이트

- 세션별 고유 score 수, score range, top-k 경계 동률 수를 기록한다.
- `top_k_boundary_tie_count > k`이면 해당 세션 선택은 `UNRANKABLE_TIE`로 제외한다.
- 문자열 티커 정렬은 성과 원장 선택 규칙으로 사용할 수 없다.

### train/serve 계약

현재 모델은 티커의 **첫 후보 관측 snapshot**으로 학습했다. shadow scorer도 현재 09:05 상태를 재계산해서 넣지 않고 동일 immutable first snapshot을 소비해야 한다.

- `market_open_elapsed_min` 등 sparse 필드를 서빙 때 새로 100% 채우면 train/serve skew다.
- scorer 구현 시 학습 추출기와 live 추출기가 같은 feature builder와 schema hash를 사용한다.
- fixture 하나를 양 경로에 통과시켜 값·결측 mask·범주값이 완전히 같은지 테스트한다.
- 장차 09:05 snapshot 모델을 원하면 first-snapshot 모델에 섞지 않고 별도 데이터셋과 별도 arm으로 재학습한다.

## 8. 진행 순서

1. 전체 후보 immutable registry와 장후 전체 60분 분봉 수집.
2. bounded quote 표본과 inclusion probability 원장.
3. US arm을 고정 사양으로 scorer shadow 배선. 주문 미연결.
4. judge overlap, score tie, 실제 quote/fill, STOP slippage, provider coverage를 일일 ledger에 기록.
5. KR은 P0 데이터로 재발견하되 현재 Claude-route arm은 재사용하지 않는다.
6. 신규 30거래세션, US 90 picks, 전체 outcome 커버리지 90% 이상 후 판정.

## 9. 승격 게이트

US arm도 다음을 모두 통과하기 전 MICRO로 올리지 않는다.

1. 사전등록 이후 신규 30거래세션과 top-3 최대 90 picks.
2. 세션당 최대 3개 준수.
3. prospective 전체 후보 60분 라벨 커버리지 90% 이상.
4. 실제 quote/fill 기준 paired net의 세션 bootstrap 5% 하한 > 0.
5. STOP 슬리피지·추가비용 +0.25%p 후 하한 > 0.
6. 상위 3기여 제거 후 양수, 월/반월 블록 부호 유지.
7. top-k 경계 동률 세션 비율 0%, feature/schema drift 0.
8. 기존 주문 레인의 슬롯·자본·API authority와 완전 격리.
9. 자동승격 금지, 운영자 승인 필수.

## 10. 최종 판단

독립 검토는 방향상 맞았고, 그 검토를 따라 더 엄격히 확인하면서 KR 착시와 세션 후보집합 lookahead를 추가로 제거했다. 남은 유효 후보는 US opening-cohort top-3 하나다. 가장 수익성 높은 다음 행동은 이 arm을 곧바로 주문에 연결하는 것이 아니라, 전체 후보 결과를 편향 없이 수집하고 실제 호가 손익으로 상한을 깎아도 양수인지 확인하는 것이다.
