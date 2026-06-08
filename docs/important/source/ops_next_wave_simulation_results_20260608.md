# Ops Next Wave Simulation Results

- 작성일: 2026-06-08
- 목적: live 코드 추가 수정 없이 기존 DB/price tape로 다음 개선 후보 탐색
- 실행 원칙: read-only DB, price CSV read-only, 결과는 `.runtime/ops_simulation_analysis/*`와 문서에만 저장

## 결론

이번 추가 시뮬레이션에서 바로 live 코드로 개선할 항목은 아직 없다. 다만 다음 개선 후보는 명확해졌다.

1. US high-price one-share fix는 유지한다. 기존 수익 후보를 밀어내는 정황이 없고, 실제 missed 후보 `CLS`가 확인됐다.
2. US high-price 후보 전용 exit는 ladder 계열이 가장 높았지만 표본 16개라 protected exit 정책 변경 근거는 부족하다.
3. US 손실 필터는 confidence 조건이 좋아 보이나, 유일한 실제 addable missed 수익 후보 `CLS`를 제외할 수 있어 live 필터로 쓰면 안 된다.
4. Fill/slippage는 slippage cap 확대 근거가 약하다. RDDT는 +30bp까지도 테이프상 fill 조건이 나오지 않았다.
5. 700k 초과 tier는 수익 후보가 있으나 손실도 커서 live 적용이 아니라 승인형 별도 tier 후보로만 남긴다.
6. KR 손실 구조는 장초반 0~30분과 late 진입, momentum 전략이 특히 나쁘다. KR은 신규 확대보다 손실 필터/시간 제한 후보를 먼저 봐야 한다.

## US High-price Displacement

산출물:

- `.runtime/ops_simulation_analysis/us_high_price_next_wave_20260608/us_high_price_simulation.md`
- `.runtime/ops_simulation_analysis/us_high_price_next_wave_20260608/us_high_price_simulation.json`
- `.runtime/ops_simulation_analysis/us_high_price_next_wave_20260608/us_high_price_candidates.csv`

대상:

- affected candidate count: 16
- complete price replay: 16
- post-fix class: `post_fix_waiting_size_gate` 16
- status: CLOSED 13, CANCELLED 3

전체 post-gate replay:

| horizon | count | avg | median | best | worst | win rate |
|---|---:|---:|---:|---:|---:|---:|
| 60m | 16 | +0.5924 | +0.2844 | +3.4993 | -1.5974 | 62.50% |
| EOD | 16 | +0.6502 | +1.2100 | +5.5683 | -4.0859 | 62.50% |

displacement:

| item | value |
|---|---:|
| addable cancelled | 1 |
| daily/position pressure | 0 |
| cash pressure | 0 |
| addable 60m | +1.9250 |
| addable EOD | +2.7087 |

개선 전/후:

| 항목 | 개선 전 | 개선 후 판단 |
|---|---|---|
| CLS급 high-price 후보 | `HIGH_PRICE_BUDGET_BLOCK`로 취소될 수 있음 | soft gate 이후 재평가 대상으로 남기는 것이 맞음 |
| 기존 수익 후보 침범 | 미확인 | 이번 표본에서는 daily/position/cash pressure 0 |
| live 반영 | 이미 one-share fix 반영 | 추가 코드 변경 없음 |

판단: 수익성 개선 후보로 유지. 다만 추가 진입이 앞으로 기존 후보를 밀어내는지는 계속 모니터링한다.

## US High-price Exit Replay

affected 16개 후보 기준:

| policy | count | avg | median | best | worst | win rate |
|---|---:|---:|---:|---:|---:|---:|
| ladder_t4_g1_5 | 16 | +0.8560 | +1.1591 | +3.6281 | -2.6543 | 62.50% |
| ladder_t3_g1_0 | 16 | +0.8301 | +1.1591 | +3.9172 | -2.6543 | 62.50% |
| target_stop | 16 | +0.7678 | +1.1591 | +3.4935 | -2.6543 | 62.50% |
| hold_to_eod | 16 | +0.6502 | +1.2100 | +5.5683 | -4.0859 | 62.50% |
| time_stop_60 | 16 | +0.5924 | +0.2844 | +3.4993 | -1.5974 | 62.50% |
| time_stop_120 | 16 | +0.3758 | +0.2804 | +3.1988 | -2.2411 | 62.50% |

개선 전/후:

| 항목 | 개선 전 | 개선 후 판단 |
|---|---|---|
| high-price exit | 기존 US PathB exit 그대로 | 현재 유지 |
| ladder 강화 | 좋아 보임 | 표본 16개라 protected exit 변경 금지 |
| time stop | worst는 줄지만 avg도 낮음 | 적용 근거 부족 |

판단: exit 변경 없음. live 수익 경로 보호.

## US Loss Filter

base EOD:

| count | avg | median | worst | win rate |
|---:|---:|---:|---:|---:|
| 16 | +0.6502 | +1.2100 | -4.0859 | 62.50% |

상위 rule:

| rule | kept | avg | worst | win rate | delta avg |
|---|---:|---:|---:|---:|---:|
| confidence >= 0.60 | 2 | +1.4745 | +1.2132 | 100.00% | +0.8243 |
| confidence >= 0.58 | 5 | +0.9897 | -0.7987 | 80.00% | +0.3395 |

주의:

- confidence rule은 좋아 보이지만 표본이 너무 작다.
- `CLS`는 confidence 0.55인데 실제 addable missed 후보이고 EOD +2.7087이었다.
- 따라서 confidence filter를 live에 넣으면 이번에 찾은 핵심 missed 후보를 다시 놓칠 수 있다.

판단: 손실 필터 후보는 보류. 신규 표본을 더 모은 뒤 재검토.

## US Fill/Slippage

unfilled order 계열 6개를 테이프로 확인했다.

| slippage widen | fill count | EOD avg | EOD worst | 판단 |
|---:|---:|---:|---:|---|
| 0bp | 2 | +2.6995 | +0.8532 | 이미 기존 limit로도 가능했던 케이스 |
| +5bp | 2 | +2.6482 | +0.8028 | fill 증가 없음 |
| +10bp | 2 | +2.5969 | +0.7524 | fill 증가 없음 |
| +20bp | 2 | +2.4946 | +0.6519 | fill 증가 없음 |
| +30bp | 2 | +2.3923 | +0.5515 | fill 증가 없음 |

주요 케이스:

- RDDT: operator cancelled unfilled, EOD는 좋았지만 +30bp까지도 테이프상 limit fill 조건이 나오지 않았다.
- SMCI/FTNT: 기존 limit로도 fill 가능하게 보이나 이벤트/성과 상태와 완전히 일치하지 않아 broker/event audit 대상이다.

판단: slippage cap 확대 금지. 먼저 unfilled/event reconcile audit가 필요하다.

## US 700k 초과 Tier

registration 단계에서 700k cap 초과로 차단된 후보 14개를 post-gate 기준 replay했다.

| horizon | count | avg | median | best | worst | win rate |
|---|---:|---:|---:|---:|---:|---:|
| 60m | 14 | +0.0213 | +0.0558 | +2.2498 | -1.4903 | 64.29% |
| EOD | 14 | +0.3745 | +0.4571 | +5.9642 | -4.5368 | 64.29% |

예시:

| ticker | EOD | risk note |
|---|---:|---|
| CRWD 2026-06-04 | +5.9642 | 강한 수익 후보 |
| LITE 2026-06-02 | +4.0040 | 강한 수익 후보 |
| AMD 2026-06-05 | -4.5368 | 큰 손실 |
| LLY 2026-06-05 | -2.4627 | 큰 손실 |

판단:

- 수익성은 완전히 나쁘지 않지만 notional이 75만~180만 KRW라 현재 정책과 다르다.
- 이번 one-share fix 범위가 아니다.
- live 적용은 운영자 승인형 별도 high-confidence tier가 필요하다.

## KR 손실 구조

KR live closed 43개:

| count | avg | median | best | worst | win rate |
|---:|---:|---:|---:|---:|---:|
| 43 | -1.0546 | -1.7088 | +8.3744 | -9.8179 | 27.91% |

entry bucket:

| bucket | count | avg | median | worst | win rate |
|---|---:|---:|---:|---:|---:|
| OPEN_0_30 | 4 | -3.7480 | -4.8093 | -9.8179 | 25.00% |
| LATE_AFTER_270 | 6 | -2.3962 | -3.1035 | -5.6165 | 16.67% |
| OPEN_30_60 | 10 | -0.7392 | -1.4998 | -3.2104 | 20.00% |
| OPEN_90_270 | 20 | -0.6051 | -0.7561 | -3.6481 | 35.00% |
| OPEN_60_90 | 3 | +1.1721 | -1.9841 | -2.4362 | 33.33% |

strategy:

| strategy | count | avg | median | win rate |
|---|---:|---:|---:|---:|
| momentum | 11 | -1.9552 | -2.6293 | 18.18% |
| opening_range_pullback | 6 | -1.3198 | -1.3946 | 16.67% |
| gap_pullback | 20 | -0.6933 | -0.9230 | 35.00% |
| claude_price | 4 | +0.3370 | +0.4491 | 50.00% |

개선 후보:

- KR 신규 확대 금지.
- KR `OPEN_0_30`와 `LATE_AFTER_270` 진입은 손실 필터 후보.
- KR momentum은 바로 확대하면 안 된다. 별도 축소/관찰 후보.
- KR claude_price는 작지만 양수이므로 우선 보호하고 표본 확대 관찰.

## 카테고리별 개선 후보

### 수익성

| 시장 | 후보 | 판단 |
|---|---|---|
| US | one-share fix 유지 | 확정 유지 |
| US | high-price exit ladder 강화 | 보류, protected exit 변경 금지 |
| US | 700k 초과 tier | 승인형 후속 후보 |
| KR | OPEN_0_30/LATE 진입 제한 | 추가 시뮬레이션 후보 |
| KR | momentum 축소 | 추가 시뮬레이션 후보 |

### 버그/운영성

| 시장 | 후보 | 판단 |
|---|---|---|
| US | RDDT unfilled/operator cancel | fill/slippage가 아니라 event/broker reconcile audit 후보 |
| US | SMCI/FTNT existing limit fill 가능성 | 이벤트/성과 sync audit 후보 |
| 공통 | price replay vs performance mismatch | 데이터 audit 후보 |

### 적용 금지

| 항목 | 사유 |
|---|---|
| US slippage cap 확대 | fill 증가 없음 |
| high-price confidence filter 즉시 적용 | CLS 같은 missed 수익 후보를 제외할 수 있음 |
| US exit 정책 변경 | 표본 작고 protected live path |
| 700k 초과 tier live 적용 | notional/리스크가 기존 정책과 다름 |
| KR broad wait/live 확대 | 기존 분석상 broad wait 음수 |

## 다음 시뮬레이션

1. KR `OPEN_0_30`/`LATE_AFTER_270` 차단 counterfactual: 기존 수익 후보를 얼마나 같이 잃는지 확인
2. KR momentum 축소/보류 시뮬레이션: 손실 감소와 놓치는 수익 계산
3. US unfilled/event reconcile audit: fill 가능했는데 성과/이벤트가 왜 불일치하는지 확인
4. 700k 초과 tier는 confidence/sector/ticker concentration 조건으로 더 좁혀서 재시뮬레이션
