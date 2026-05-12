# PathB PULLBACK_WAIT Live 정책 검토

작성일: 2026-05-12

## 배경

2026-05-12 02:59:25 재시작 이후 US 장중 selection에서 Claude는 일부 종목을 `PULLBACK_WAIT`로 판단했다.
PathA 기준 `trade_ready`는 비어 있었지만, 현재 PathB 정책은 `PULLBACK_WAIT`의 buy-zone 가격 계획을 대기 플랜으로 등록하고 가격이 구간에 들어오면 safety gate 이후 실매수할 수 있다.

이번에는 동작을 변경하지 않고, 나중에 정책 판단을 위해 코드 주석과 본 문서에 검토 사항만 남긴다.

## 현재 액션 계약

- `BUY_READY`: 즉시 정상 진입 후보. PathA 매수 후보이며 runtime gate 통과 후 주문 가능.
- `PROBE_READY`: 즉시 소액 탐색 진입 후보. PathA 매수 후보이며 runtime gate 통과 후 주문 가능.
- `PULLBACK_WAIT`: 즉시 진입이 아니라 buy-zone까지 기다리는 조건부 가격 계획. `price_targets`가 필요하다.
- `WATCH`: 관찰 전용. 실주문 없음.
- `AVOID`: 적극 제외. 실주문 없음.

Compact contract 기준으로 `tr`에는 `BUY_READY` 또는 `PROBE_READY`만 들어갈 수 있다. 따라서 `PULLBACK_WAIT`는 PathA `trade_ready`가 아니다.

## 현재 Runtime 동작

현재 구현은 다음 흐름을 허용한다.

1. Claude가 후보를 `PULLBACK_WAIT`로 반환한다.
2. parser는 해당 종목을 PathA `trade_ready`에서는 제거한다.
3. `trading_bot.py`는 `PULLBACK_WAIT` 종목과 가격대를 `_pathb_wait_tickers`, `_pathb_price_targets`로 분리한다.
4. `runtime/pathb_runtime.py`는 이를 PathB waiting plan으로 등록한다.
5. 장중 가격이 buy-zone에 들어오면 PathB safety gate를 통과한 뒤 실매수할 수 있다.

관련 코드 주석 위치:

- `runtime/pathb_runtime.py` `register_from_selection_meta`
- `runtime/pathb_runtime.py` `scan_waiting_entries`

## Claude API 확인 결과

Claude API에 현재 계약과 runtime 설명을 그대로 전달해 확인한 요약은 다음과 같다.

- `PULLBACK_WAIT`는 즉시 매수 준비가 아니라 조건부 buy-zone 대기 계획으로 이해한다.
- PathB가 `PULLBACK_WAIT`를 대기 플랜으로 등록하고, 가격이 zone에 들어온 뒤 safety gate를 통과해 주문하는 것은 문언상 조건부로 일치할 수 있다.
- 하지만 명시적 `BUY_READY` 또는 `PROBE_READY` 재확인 없이 실주문까지 직행하는 것은 stale-plan 리스크가 있다.
- 가장 엄격한 해석은 buy-zone 진입 시점에 Claude 재확인 또는 runtime 재분류를 거쳐 `BUY_READY/PROBE_READY`가 되었을 때만 실주문하는 것이다.

## 남은 모호성

- `PULLBACK_WAIT`가 "가격대 도달 시 자동 실행 가능한 조건부 주문 계획"인지, "가격대 도달 시 재검토해야 하는 대기 후보"인지 계약이 완전히 분리되어 있지 않다.
- buy-zone에 들어온 시점에는 최초 Claude 판단 당시의 시장 구조, freshness, invalidation condition이 바뀌었을 수 있다.
- 운영자가 `trade_ready=[]`를 "오늘 실매수 없음"으로 이해할 수 있는데, 현재는 PathB가 별도 경로로 실매수할 수 있다.
- 감사 로그상 `PULLBACK_WAIT -> live order` 상태 전이가 명확히 표시되지 않으면 사후 QA에서 혼동될 수 있다.

## 선택 가능한 정책안

### A. 현재 유지

`PULLBACK_WAIT`를 Claude가 제시한 조건부 매수 계획으로 보고, PathB buy-zone 진입 시 safety gate 이후 실매수를 허용한다.

장점:
- 좋은 pullback 진입 기회를 놓치지 않는다.
- Claude가 가격대를 명시한 계획을 실행까지 연결할 수 있다.
- 현재 코드 변경이 거의 필요 없다.

단점:
- `trade_ready=[]`인데 실매수가 발생할 수 있다.
- Claude 최종 매수 철학을 엄격히 보면 애매하다.
- 오래된 price plan이 시장 변화 후 실행될 위험이 있다.

### B. 재확인 필수

PathB가 `PULLBACK_WAIT` 가격대에 진입하더라도 즉시 주문하지 않고, Claude 또는 runtime action gate에서 `BUY_READY/PROBE_READY`로 재확인된 경우만 실매수한다.

장점:
- "Claude가 최종 매수 판단"이라는 철학에 가장 잘 맞는다.
- stale-plan 리스크를 줄인다.
- `trade_ready`와 실매수 의미가 더 명확해진다.

단점:
- API 호출 비용과 지연이 늘어난다.
- 빠른 pullback 체결 기회를 놓칠 수 있다.
- 재확인 실패나 지연 시 PathB 효율이 떨어질 수 있다.

### C. 설정값으로 분리

기본값은 현재 유지 또는 보수값 중 하나로 두고, `PATHB_PULLBACK_WAIT_LIVE_RECONFIRM_REQUIRED` 같은 설정으로 정책을 전환 가능하게 한다.

장점:
- 운영 중 실험과 비교가 가능하다.
- 시장/계좌 규모/운영 단계별로 정책을 바꿀 수 있다.
- 바로 강제 변경하지 않고 shadow QA를 붙일 수 있다.

단점:
- 설정 복잡도가 늘어난다.
- 대시보드/리포트에서 현재 정책 상태를 명확히 보여줘야 한다.

## 임시 결론

2026-05-12 현재는 정책 변경 없이 A안을 유지한다.

다만 다음 검토 때는 C안을 우선 검토한다. 즉, 현재 동작을 바로 제거하지 말고 설정값과 shadow QA를 추가해 아래를 비교한다.

- 현재 방식으로 실제 진입한 PathB 성과
- buy-zone 진입 시점에 재확인했다면 `BUY_READY/PROBE_READY`가 나왔을 비율
- 재확인 요구 시 놓친 수익 기회
- 재확인 없이 들어간 stale-plan 손실 사례

## 다음 개발 후보

- `PULLBACK_WAIT -> PathB live order` 전이를 별도 audit reason으로 기록.
- 대시보드에 `PathA trade_ready`와 `PathB conditional live`를 분리 표시.
- 설정값 추가 검토:
  - `PATHB_PULLBACK_WAIT_LIVE_RECONFIRM_REQUIRED`
  - `PATHB_PULLBACK_WAIT_RECONFIRM_MODE=off|shadow|required`
- shadow 모드에서 buy-zone 진입 시 Claude 재확인 결과만 저장하고 실제 주문 정책은 유지.
- 1~3일 운영 데이터로 A/B 비교 리포트 생성.

