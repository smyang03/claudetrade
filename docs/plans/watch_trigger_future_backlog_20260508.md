# Watch Trigger Future Backlog - 2026-05-08

## Purpose

이 문서는 즉시 구현하지 않고, shadow 데이터가 쌓인 뒤 검토할 WATCH_TRIGGER 후속 개발 항목을 정리한다.

현재 우선순위는 자동 승격이 아니라 관측이다. 아래 항목들은 `watch_trigger_shadow` 결과가 충분히 쌓인 뒤, 실제 성과를 확인하고 단계적으로 적용한다.

## Deferred Scope

### F1. 독립 WATCH_TRIGGER state machine

목표:

- Claude가 `WATCH`로 둔 후보 중 장중 조건이 개선되는 종목을 별도 상태로 추적한다.
- 기존 전략 신호에만 의존하지 않고, 가격/거래량/장중 구조 변화를 직접 본다.

후보 상태:

```text
WATCH_ONLY
WATCH_STRENGTHENING
WATCH_TRIGGER_ARMED
WATCH_TRIGGER_PROBE_READY
WATCH_TRIGGER_FAILED
WATCH_TRIGGER_EXPIRED
```

검토할 trigger feature:

```text
opening range 형성 여부
opening range reclaim
VWAP reclaim
VWAP above 유지 시간
3분/5분 수익률
고점 대비 낙폭 축소
거래량 증가율
spread/liquidity 상태
ATR 과열 여부
세션 잔여 시간
```

초기 적용 방향:

- 자동 매수 전 shadow only
- 이후 `PROBE_READY`까지만 제한 적용
- full size `BUY_READY` 승격은 가장 마지막 단계

### F2. 종목별 missed WATCH 피드백 루프

목표:

- 같은 종목이 반복해서 WATCH에 머물렀다가 상승하는 패턴을 Claude selection prompt에 반영한다.

예시 피드백:

```text
IREN: 최근 WATCH_ONLY 이후 +9.28% 진행. 같은 veto가 반복될 경우 명확한 hard risk가 없으면 PROBE_READY 검토.
```

필요 데이터:

```text
ticker
market
watch_only 날짜
watch_only reason
forward_30m
forward_1d
forward_3d
다음 Claude 판단
다음 route action
```

주의점:

- 단일 결과로 바로 공격성을 올리면 안 된다.
- 최소 2회 이상 반복되거나 같은 cohort에서 반복될 때만 prompt feedback에 넣는다.
- "지난번 올랐으니 이번엔 사라"가 아니라 "같은 이유로 반복 보류하지 말라"는 형태가 맞다.

### F3. 자동 soft watch promotion 단계적 활성화

목표:

- shadow 결과가 충분히 좋을 때 `ENABLE_SOFT_WATCH_PROMOTION`을 제한적으로 켠다.

활성화 순서:

```text
1. US only shadow 검증
2. US SOFT watch + PROBE size only
3. US SOFT watch + limited BUY_READY
4. KR 별도 shadow 검증
5. KR 제한 적용 여부 판단
```

초기 제한:

```text
max promotions per day = 1
max promotions per scan cycle = 1
size multiplier <= 0.5
no promotion in DEFENSIVE/HALT
no promotion for high ATR/liquidity hard veto
no promotion near session close
```

중단 조건:

```text
promoted 후보의 평균 forward return이 trade_ready보다 낮음
promoted 후보 손절률 증가
same-day stop 이후 재진입 증가
order_unknown 또는 broker trust 문제 증가
```

### F4. RECOVERY watch 평가

목표:

- `trade_ready`가 비어 있거나 강한 후보가 없을 때, WATCH 후보 중 회복 가능성이 있는 종목을 제한적으로 재평가한다.

지금 제외하는 이유:

- `RECOVERY`는 정의가 넓다.
- 좋은 회복 후보와 억지 후보가 섞일 가능성이 크다.
- SOFT watch shadow 결과 없이 먼저 켜면 노이즈가 크다.

적용 전 조건:

- SOFT watch shadow 분석 완료
- recovery bucket 세분화
- hard veto와 soft veto 분리
- 시장별 결과 KR/US 분리 확인

### F5. 후보 cohort reliability 고도화

목표:

- 단일 종목이 아니라 후보 유형별로 성과를 추적한다.

검토할 cohort:

```text
market
strategy
watch_bucket
claude_action
route_final_action
liquidity_bucket
from_high_bucket
ATR bucket
relative_strength bucket
session_phase
```

사용 방식:

- 초반에는 report/shadow에만 사용
- 충분한 샘플 이후 trainer score에 반영
- 최종적으로 route gate 또는 size 조정에 사용

주의점:

- n이 작은 cohort로 차단 정책을 만들면 과최적화 위험이 크다.
- 최소 샘플 기준을 두고, 기준 미만은 warning만 한다.

### F6. 프롬프트 출력 계약 확장

목표:

- Claude가 WATCH/READY를 더 명확히 구분하게 한다.

검토할 필드:

```text
watch_candidate
setup_ready
execution_ready
veto_type = hard | soft | timing
recheck_condition
miss_cost_risk
promotion_condition
```

주의점:

- 프롬프트 계약 변경은 파싱 안정성에 영향을 준다.
- 기존 `trade_ready` 계약과 충돌하면 안 된다.
- 먼저 shadow 필드로 받고, 런타임 의사결정에는 바로 쓰지 않는다.

### F7. 대시보드 WATCH_TRIGGER 모니터

목표:

- WATCH 후보가 왜 보류/승격 shadow 되었는지 운영 중 확인 가능하게 한다.

권장 표시:

```text
WATCH total
SOFT watch
HARD watch
shadow evaluated
would promote
blocked
no signal
top missed candidates
top bad ready candidates
```

사용 목적:

- 장중 운영자가 "좋은 후보를 놓치고 있는지" 빠르게 확인한다.
- 자동 승격 전 shadow 결과를 사람이 검토한다.

### F8. HOLD dissent-risk stop shadow

목표:

- HOLD 리뷰에서 SELL 소수의견이 있을 때 stop을 타이트하게 하는 정책을 shadow로만 평가한다.

현재 판단:

- 즉시 강제 적용은 아직 이르다.
- 기존 데이터에서는 좁은 조건의 개선 가능성이 보였지만 샘플이 작다.
- WATCH_TRIGGER와 별도 축이므로 이번 즉시 작업에서는 제외한다.

권장 조건:

```text
HOLD majority
SELL dissent >= 1
pnl_pct <= 0
risk/review stage
shadow protective stop only
```

## Required Evidence Before Activation

자동 승격 또는 실행 정책 적용 전 최소 확인 항목:

```text
시장별 최소 샘플 KR/US 분리
would_promote 그룹의 forward return 우위
would_promote 그룹의 손절률
trade_ready 대비 성과 차이
watch_only_not_evaluated 대비 성과 차이
세션별 편중 여부
특정 종목 반복 편중 여부
order/broker 오류 증가 여부
```

## Priority After Shadow Data

권장 순서:

```text
1. SOFT watch shadow 결과 분석
2. ticker-level missed feedback prompt 주입
3. US 제한 soft watch promotion
4. 독립 WATCH_TRIGGER state machine shadow
5. RECOVERY watch 확장
6. KR 별도 적용 여부 판단
7. 대시보드 모니터링 강화
```

## Non Goals

아래는 이 backlog의 목적이 아니다.

- 후보군 전체를 더 넓히는 것
- Claude 호출 수를 무조건 늘리는 것
- WATCH 후보를 즉시 매수 대상으로 바꾸는 것
- 단기 결과 몇 건만 보고 자동 승격을 켜는 것
- KR/US를 같은 기준으로 강제 통합하는 것
