# Ops Next Wave Policy Simulation Review

- 작성일: 2026-06-08
- 목적: 추가 live 코드 수정 없이 다음 개선 후보를 시뮬레이션으로 선별
- 산출물: `.runtime/ops_simulation_analysis/next_wave_policy_20260608/`
- 실행 방식: DB read-only, price CSV read-only, 주문/브로커/Claude 호출 없음

## 결론

이번 추가 시뮬레이션에서도 바로 live에 적용할 새 코드는 없다. 다만 개선 후보의 우선순위는 더 명확해졌다.

1. KR은 신규 진입 확대가 아니라 손실 구간 제한이 우선이다.
2. KR `OPEN_0_30`과 `LATE_AFTER_270` 제한은 손실 감소 효과가 크지만, 일부 수익 기회도 같이 버린다.
3. KR momentum은 성과가 나쁘지만, 한 건의 큰 수익이 있어 전면 차단보다 축소/관찰이 맞다.
4. US unfilled는 slippage 문제가 아니라 event/broker reconcile audit에 가깝다.
5. US 700k 초과 tier는 수익 후보가 있으나 worst가 커서 운영자 승인형 별도 tier로만 검토한다.

## KR Policy Cuts

기준 KR live closed:

| count | sum pnl | avg | median | best | worst | win rate |
|---:|---:|---:|---:|---:|---:|---:|
| 43 | -45.3468 | -1.0546 | -1.7088 | +8.3744 | -9.8179 | 27.91% |

정책별 historical cut:

| policy | kept | kept avg | kept win | removed | removed avg | loss avoided | opportunity cost | 판단 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| keep claude_price only | 4 | +0.3370 | 50.00% | 39 | -1.1973 | 77.7111 | 31.0164 | 표본 너무 작음 |
| exclude momentum + bad buckets | 25 | -0.4071 | 32.00% | 18 | -1.9538 | 51.0429 | 15.8745 | 후보, 과격함 |
| exclude OPEN_0_30 + LATE | 33 | -0.4842 | 30.30% | 10 | -2.9369 | 36.5926 | 7.2232 | 최우선 관찰 후보 |
| exclude momentum | 32 | -0.7450 | 31.25% | 11 | -1.9552 | 30.1586 | 8.6513 | 축소 후보 |
| exclude OPEN_0_30 | 39 | -0.7783 | 28.21% | 4 | -3.7480 | 19.4365 | 4.4444 | 후보 |
| exclude LATE_AFTER_270 | 37 | -0.8370 | 29.73% | 6 | -2.3962 | 17.1561 | 2.7787 | 후보 |

판단:

- `exclude OPEN_0_30 + LATE`가 가장 균형이 좋다.
- 그래도 kept avg가 `-0.4842%`라서 이 정책만으로 KR이 양수 전략으로 바뀌지는 않는다.
- live 적용 전에는 최소 forward monitoring이 필요하다.
- `keep claude_price only`는 평균이 양수지만 count 4라 전면 정책으로 쓰기 어렵다.

## US Unfilled/Event Audit

unfilled order 계열:

| classification | count | 의미 |
|---|---:|---|
| invalid_limit_or_order_time | 5 | 주문 가격/시간 정보가 분석에 부적합 |
| tape_fill_possible_without_fill_event | 2 | 테이프상 체결 가능해 보이나 fill event 없음 |
| limit_never_touched | 1 | slippage를 넓혀도 체결 어려움 |
| price_tape_missing | 1 | 가격 테이프 부족 |

slippage 확대 결과:

- 0bp, +5bp, +10bp, +20bp, +30bp 모두 fill count 2로 동일
- +30bp에서도 fill count 증가 없음

판단:

- slippage cap 확대는 개선이 아니다.
- `tape_fill_possible_without_fill_event` 2건은 broker/event reconcile audit 후보로 분리한다.

## US 700k 초과 Tier

registration block 14건, unique ticker-day 8건:

| subset | count | unique | EOD avg | EOD worst | win |
|---|---:|---:|---:|---:|---:|
| first_and_cap_le_1_100k | 5 | 5 | +1.0100 | -4.5368 | 80.00% |
| cap_le_1_100k | 7 | 5 | +0.8520 | -4.5368 | 85.71% |
| first_per_ticker_day | 8 | 8 | +0.6284 | -4.5368 | 62.50% |
| all | 14 | 8 | +0.3745 | -4.5368 | 64.29% |
| cap_le_900k | 5 | 3 | -0.4933 | -4.5368 | 80.00% |

판단:

- 평균은 양수지만 worst `-4.5368%`가 크다.
- 700k 초과는 기존 fixed budget/one-share cap 정책 밖이다.
- 운영자 승인 없는 live 적용 금지.
- 다음에는 ticker/sector/concentration, 장중 trend, confidence 조건으로 더 좁혀야 한다.

## 개선 후보 분류

### 수익성

| 시장 | 후보 | 상태 |
|---|---|---|
| KR | `OPEN_0_30 + LATE_AFTER_270` 제한 | 최우선 forward monitoring 후보 |
| KR | momentum 축소 | 보류, 수익 기회 손실 확인 필요 |
| US | 700k 초과 tier | 승인형 후속 후보 |
| US | one-share fix | 유지 |

### 버그/운영성

| 시장 | 후보 | 상태 |
|---|---|---|
| US | tape fill 가능하지만 fill event 없음 2건 | broker/event reconcile audit 후보 |
| US | invalid limit/order time 5건 | 이벤트 payload 품질 audit 후보 |

### 적용 금지

| 항목 | 사유 |
|---|---|
| KR 전면 진입 확대 | baseline이 음수 |
| KR momentum 전면 차단 | opportunity cost 존재 |
| US slippage cap 확대 | fill count 증가 없음 |
| US 700k 초과 tier 즉시 적용 | 현재 정책 밖, worst 큼 |

## 다음 시뮬레이션

1. KR `OPEN_0_30 + LATE` 제한을 live-visible gate 기준으로 다시 구성한다.
2. KR momentum을 시간대/근거 상태별로 더 쪼개서 “전면 제외”가 아니라 “나쁜 조합만 제외” 가능한지 본다.
3. US tape-fill/event mismatch 2건의 event payload와 broker evidence를 audit한다.
4. US 700k 초과 tier를 ticker concentration, trend, confidence 조건으로 더 좁혀 재시뮬레이션한다.
