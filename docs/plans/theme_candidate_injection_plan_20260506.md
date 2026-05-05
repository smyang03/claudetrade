# Theme Candidate Injection Plan - 2026-05-06

## Question

분석가들이 오늘 장에서 반도체, AI, 전력 같은 특정 테마를 따로 분석할 수 있는가?

그리고 기존 후보 30개에 더해 특정 카테고리 후보 10개 정도를 추가 유입시킬 수 있는가?

## Status

- 상태: 검토/설계
- 런타임 코드 변경: 보류
- 목표: 기존 스크리너와 리스크 게이트를 유지하면서 테마 후보가 Claude 판단 표면에 실제로 노출되게 한다.

## Current Structure

현재 본 흐름은 아래 순서다.

```text
session_open
-> 사전 스크리너 후보
-> dynamic universe 생성
-> digest 생성
-> Bull/Bear/Neutral 분석가 시장 판단
-> consensus mode 확정
-> 스크리너 재실행
-> 후보 필터링
-> select_tickers()가 WATCH / TRADE_READY 선택
```

핵심 위치:

- 시장 분석가 판단: `trading_bot.py:get_three_judgments(...)` 호출부
- 후보 생성: `trading_bot.py:_screen_market_candidates(...)`
- 후보 선택: `minority_report/analysts.py:select_tickers(...)`
- 후보 프롬프트 cap: `minority_report/analysts.py:_selection_candidate_cap(...)`

중요한 구조적 차이:

- 분석가 3명은 후보 리스트를 직접 받지 않고 `digest_prompt`를 중심으로 시장 모드와 전략을 판단한다.
- 후보 WATCH / TRADE_READY 선택은 분석가 consensus 이후 `select_tickers()`에서 수행된다.

## Design Direction

테마 분석과 테마 후보 유입을 분리한다.

```text
1. 테마 장세 분석
   digest_prompt에 semiconductor / ai / power breadth 요약 추가

2. 테마 후보 유입
   기본 후보 30개 + 테마 후보 10개를 후보 풀에 추가
   이후 기존 필터와 Claude selection이 판단
```

후보 유입은 강제 매수나 강제 TRADE_READY가 아니다. 테마 후보는 일반 후보와 동일하게 상품 필터, 히스토리 필터, 실행 가능성 feature, 리스크 게이트를 통과해야 한다.

## Proposed Candidate Flow

```text
기본 스크리너 후보 30개
+ 테마 후보 10개
  - semiconductor
  - ai
  - power
=> 중복 제거
=> 상품/ETF 차단
=> OHLCV history 필터
=> 실행 가능성 feature 부여
=> Claude select_tickers()가 WATCH / TRADE_READY 분리
```

테마 후보에는 메타데이터를 붙인다.

```text
category=theme_semiconductor
sector=semiconductor
risk_tags=["theme:semiconductor"]
candidate_source=theme_injection
```

## Candidate Cap Issue

단순히 스크리너 후보를 늘리는 것만으로는 부족하다.

현재 `select_tickers()` 프롬프트 후보 cap이 대략 아래처럼 작동한다.

- US: 기본 24개 근처
- KR: 기본 28개 근처

따라서 `30 + 10 = 40` 후보를 넣어도 실제 Claude 프롬프트에서 잘릴 수 있다.

필요한 조정:

- `US_SELECTION_PROMPT_CAP`, `KR_SELECTION_PROMPT_CAP`을 40 근처로 조정
- 또는 테마 후보가 curation 단계에서 밀리지 않도록 별도 보존 규칙 추가
- 단, 테마 후보가 기존 고품질 후보를 과도하게 밀어내지 않도록 cap과 diversity 제한 유지

## Initial Theme Basket

초기 바스켓은 고정 리스트로 시작하고, 이후 성과 로그 기반으로 조정한다.

US 예시:

```text
semiconductor: NVDA, AMD, AVGO, SMCI, ARM, QCOM, INTC, MU
ai: MSFT, META, GOOGL, AMZN, PLTR, SNOW, CRM, ORCL
power: CEG, VST, NRG, ETN, GE, GEV, OKLO
```

KR 예시:

```text
semiconductor: 005930, 000660, 042700, 039030, 058470, 005290, 036810
ai: 035420, 035720, 078340, 041190, 112040
power: 267260, 010120, 006260, 034020, 042660
```

최종 10개는 테마별 quota로 나눈다.

```text
semiconductor 4
ai 3
power 3
```

## Implementation Options

### Option A - Candidate Injection Only

`_screen_market_candidates()` 결과 뒤에 테마 후보를 삽입한다.

장점:

- 변경 범위가 작다.
- 기존 필터/리스크/선택 로직을 그대로 탄다.

단점:

- 분석가 3명의 시장 판단에는 테마 breadth가 충분히 반영되지 않을 수 있다.
- selection prompt cap에 걸리면 테마 후보가 Claude에게 보이지 않을 수 있다.

### Option B - Digest Theme Breadth + Candidate Injection

digest에 테마별 breadth 요약을 추가하고, 후보 풀에도 테마 후보를 추가한다.

장점:

- 분석가가 "오늘 반도체/AI/전력 장세"를 직접 판단할 수 있다.
- 후보 선택 단계에서도 같은 테마 후보를 볼 수 있다.

단점:

- digest builder와 selection 후보 흐름을 모두 건드린다.
- shadow/paper 검증이 필요하다.

권장안: Option B를 paper/shadow first로 진행한다.

## Guardrails

- 테마 후보는 `trade_ready` 강제 편입 금지
- 가격/거래량/히스토리 없는 후보는 watch_only 또는 shadow_only
- ETF/레버리지/인버스 상품은 기존 product filter 유지
- KOSDAQ visibility는 유지
- 테마 후보가 전체 prompt를 독점하지 않도록 테마 quota와 category/sector cap 유지
- 실거래 전 paper 로그로 유입률, 선택률, 신호 전환률 확인

## Logging And Metrics

추가 로그:

```text
raw_screen_count
theme_candidate_count
theme_inserted_count
post_product_filter_count
post_history_filter_count
selection_prompt_count
watch_theme_count
trade_ready_theme_count
signal_theme_count
order_theme_count
```

성과 판단:

- 테마 후보가 프롬프트에 실제 노출됐는가
- WATCH까지 간 비율
- TRADE_READY까지 간 비율
- 실제 전략 신호 전환률
- non-theme 후보 대비 PnL/손실률
- 테마 후보가 기존 좋은 후보를 밀어낸 부작용

## Test Plan

- 단위 테스트: 테마 후보 dedupe, quota, metadata 부여
- 단위 테스트: selection prompt cap에서 테마 후보 보존
- 단위 테스트: product filter가 테마 ETF/레버리지 후보를 차단
- 통합 테스트: `screen -> theme injection -> history filter -> select_tickers` 후보 수와 메타 검증
- paper run: 하루 이상 shadow 로그로 유입/선택/신호 전환 확인

## Rollout

1. Shadow log only
   - 후보 풀에는 넣지 않고, 오늘 테마 후보가 기존 후보/선택 결과와 얼마나 겹쳤는지만 기록

2. Paper candidate injection
   - paper에서만 `30 + 10` 후보 유입
   - `trade_ready` 강제 금지

3. Paper digest theme breadth
   - 분석가 프롬프트에 테마 breadth 요약 추가
   - 기존 분석가 판단과 비교

4. Live enable
   - paper 기준 유입/선택/손실률이 안정적일 때만 live 환경변수로 활성화

## Open Questions

- 테마 후보 10개를 시장별로 동일하게 적용할지, US/KR quota를 다르게 둘지
- 전력 테마에 원전/전력기기/전력망/데이터센터 전력을 모두 포함할지
- 테마 후보가 기존 `dynamic universe`에도 들어가야 하는지, selection 단계에서만 보강할지
- 테마 breadth를 가격 기준으로 볼지, 상대강도/거래대금/신고가 접근도까지 포함할지
