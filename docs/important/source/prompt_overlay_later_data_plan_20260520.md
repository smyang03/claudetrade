# Prompt Overlay Later Data Plan

작성일: 2026-05-20

## 목적

이 문서는 `prompt_overlay_code_requirements_20260520.md`에서 즉시 코드로 고정할 항목이 아니라, 운영 데이터가 쌓인 뒤 판단해야 할 항목만 분리한 계획이다. 지금 바로 적용하면 표본 부족, 특정 일자 의존, PLAN_B fallback 재유입 같은 리스크가 있으므로 shadow 관찰 후 결정한다.

## 전제

- Phase 0 로깅 수정이 적용되어 있어야 한다.
- `actual_prompt_tickers`, `actual_prompt_count`, `plan_a_in_prompt`, `overlay_mode`가 신뢰 가능해야 한다.
- `PLAN_B`는 fallback 또는 overlay source로 사용하지 않는다.
- `PLAN_A=0`이면 current prompt 유지가 기본값이다.

## 1. Shadow에서 Live 전환

### 왜 하는가

shadow 모드는 실제 Claude 입력을 바꾸지 않으므로 가격/PF counterfactual만 확인할 수 있다. live 전환 전에는 overlay가 실제 prompt 품질을 개선하는지 충분한 관찰이 필요하다.

### 무엇을 볼 것인가

- shadow 기간 10거래일 이상
- overlay 실제 발동일 4일 이상
- overlay 발동일 PF
- top-day 기여율
- PLAN_A=0 사이클에서 current prompt가 그대로 유지됐는지
- `overlay_plan_b_used=True`가 한 번도 없는지

### 어떤 것을 할 것인가

모든 hard gate를 통과할 때만 `PROMPT_OVERLAY_MODE=shadow`에서 `live`로 전환한다. 하나라도 실패하면 shadow를 연장한다. live 전환 후에도 초기 cap은 3~4를 유지한다.

## 2. PLAN_A Overlay Cap 확대

### 왜 하는가

초기 `PROMPT_OVERLAY_PLAN_A_MAX=3~4`는 리스크를 줄이기 위한 보수적 시작값이다. 기존 시뮬레이션에서 `up to 8`이 좋아 보였지만 test label이 55개로 작고 특정 일자 기여도가 컸다. 바로 8로 올리면 나쁜 날의 손실 폭도 같이 커질 수 있다.

### 무엇을 볼 것인가

- live 전환 후 일별 PLAN_A 개수 분포
- live overlay 발동일 수
- live overlay 발동일 PF
- top-day 기여율
- market별 KR/US 성과 분리
- PLAN_A가 current top에 이미 포함된 비율

### 어떤 것을 할 것인가

live 전환 후 최소 10거래일을 관찰한 뒤 gate를 통과하면 `PROMPT_OVERLAY_PLAN_A_MAX`를 단계적으로 올린다.

```text
4 -> 6 -> 8
```

한 번에 2개씩만 올리고, 각 단계마다 최소 10거래일을 다시 본다. PF가 악화되거나 top-day 의존이 커지면 이전 값으로 되돌린다. live 전환 전에는 cap 확대를 결정하지 않는다.

## 3. 별도 Shadow Claude Call 도입 여부

### 왜 하는가

pure shadow에서는 overlay ticker를 Claude가 실제로 보지 않기 때문에 PLAN_A overlay ticker의 `TRADE_READY` 전환율을 측정할 수 없다. ranker와 Claude 판단이 정렬되어 있는지 보려면 별도 dry-run Claude 호출이 필요하다.

### 무엇을 볼 것인가

- 현재 Claude API 비용과 호출량 여유
- selection 호출 실패율
- shadow PF는 좋은데 live TRADE_READY 전환율이 낮은지
- overlay ticker가 실제 Claude 판단에서 계속 WATCH로 밀리는지
- live 전환 후 2주간 PLAN_A overlay ticker의 TRADE_READY 전환율이 현행 평균 대비 70% 미만인지

### 어떤 것을 할 것인가

live 전환 후 2주간 PLAN_A overlay ticker의 `TRADE_READY` 전환율이 현행 평균의 70% 미만이면 `PROMPT_OVERLAY_SHADOW_CLAUDE_CALL=true` 같은 별도 플래그로 dry-run 호출 실험을 검토한다. 이 호출은 주문, watchlist, trade_ready 상태에 절대 반영하지 않고 audit 전용으로만 저장한다.

## 4. Fresh Slot 구현

### 왜 하는가

fresh slot은 신규 후보 탐색을 늘리는 장치지만, 현재는 `candidate_age_min` 같은 freshness 로그가 충분히 안정적으로 쌓였다는 보장이 없다. 정의가 불명확한 상태에서 fresh slot을 넣으면 단순 노이즈 확대로 변질될 수 있다.

### 무엇을 볼 것인가

- `candidate_age_min` 기록률
- 신규 후보의 ret30/ret60 성과
- 신규 후보의 Claude WATCH/TRADE_READY 전환율
- 기존 반복 후보 대비 missed runup 비율
- market별 freshness 효과 차이

### 어떤 것을 할 것인가

freshness 로그가 안정화된 뒤에만 별도 slot을 실험한다. 초기에는 live가 아니라 shadow에서 다음 형태로만 비교한다.

```text
current_top + PLAN_A overlay + fresh_shadow_candidates
```

fresh 후보가 PLAN_A보다 낮은 품질이면 live slot으로 승격하지 않는다.

## 5. 스크리너 Breadth 확장

### 왜 하는가

현재 DB는 스크리너 안에 들어온 후보만 평가할 수 있다. 스크리너 밖에서 크게 오른 missed winner는 전체 시장 가격 데이터가 없으면 볼 수 없다. 이 상태에서 breadth를 늘리면 좋은 후보를 찾기보다 노이즈만 늘릴 가능성이 크다.

### 무엇을 볼 것인가

- 전체 시장 대비 screener recall
- 당일 큰 상승 종목 중 스크리너 미포함 비율
- 미포함 winner의 공통 조건
- 스크리너 포함 후보와 미포함 winner의 유동성/거래대금 차이
- KR/US 시장별 missed winner 패턴
- KR 전체 종목 가격 데이터 확보 가능 여부
- US 전체 종목 가격 데이터 확보 가능 여부

### 어떤 것을 할 것인가

외부 전체 시장 가격 데이터를 붙인 뒤 screener recall audit을 만든다. KR은 우선 KIS API의 국내 전종목/시세 조회 가능 범위를 확인하고, 부족하면 KRX/상장종목 파일과 KIS 현재가를 조합한다. US는 우선 yfinance 또는 기존 외부 시장 데이터 수집 경로로 NASDAQ/NYSE/AMEX 커버리지를 확인한다. missed winner가 특정 조건에서 반복적으로 발생할 때만 스크리너 breadth 또는 category quota를 조정한다.

## 6. PLAN_B 재평가

### 왜 하는가

현재 분석에서 PLAN_B PF는 현재 cap25보다 낮았고 fallback으로 부적합했다. 다만 scorer 또는 시장 환경이 바뀌면 PLAN_B의 의미가 달라질 수 있으므로 영구 폐기보다는 재평가 조건을 둔다.

### 무엇을 볼 것인가

- rolling 20거래일 PLAN_B only PF
- PLAN_B 중 특정 market/category/liquidity bucket의 부분 성과
- PLAN_A=0인 날 PLAN_B 성과
- PLAN_B가 Claude에서 WATCH로만 끝나는 비율

### 어떤 것을 할 것인가

PLAN_B 전체를 fallback으로 쓰는 일은 금지한다. 다만 충분한 데이터에서 특정 subset이 반복적으로 PF > 1.2를 보이면 별도 실험 후보로 분리한다. 이 경우에도 이름은 PLAN_B fallback이 아니라 새로운 explicit subset으로 둔다.

## 7. 자동 Rollback 임계값 조정

### 왜 하는가

초기 rollback 조건인 `5거래일 rolling overlay PF < 0.8`은 보수적 임계값이다. 실제 live 데이터에서 변동성이 크면 너무 자주 꺼질 수 있고, 반대로 너무 느슨하면 손실을 방치할 수 있다.

### 무엇을 볼 것인가

- live 전환 후 rolling 5/10/20세션 PF
- overlay 발동 빈도
- 손실일 연속성
- top-day 제거 후 성과
- market별 rollback 필요 여부

### 어떤 것을 할 것인가

live 관찰 2주 후 rollback 기준을 재검토한다. KR/US 성과 차이가 크면 market별 rollback threshold를 분리한다.

## 우선순위

1. shadow에서 live 전환 여부
2. PLAN_A overlay cap 확대 여부
3. shadow Claude call 필요 여부
4. fresh slot 구현
5. screener breadth 확장
6. PLAN_B subset 재평가
7. rollback 임계값 조정

## 보류 기준

다음 중 하나라도 해당하면 해당 항목은 진행하지 않는다.

- 실제 prompt 로깅이 불안정하다.
- PLAN_A 발동일이 4일 미만이다.
- top-day 기여율이 40% 이상이다.
- PLAN_B fallback이 한 번이라도 감지된다.
- KR/US 중 한 시장에서만 성과가 나오는데 전체 성과로 포장된다.
