# PathB TTL Profit Opportunity and Cleanup Automation Follow-up Plan

작성일: 2026-05-27

## 목적

이번 PathB TTL 개선은 우선순위를 **오인 체결/오취소 방지**에 두고 완료했다.

현재 구현은 다음 운영 리스크를 방어한다.

- plan 생성 시각을 기준으로 주문 직후 TTL 취소가 발생하는 문제 방지
- 다른 주문번호의 broker fill을 PathB fill로 오인하는 문제 방지
- 다른 주문번호의 open order를 PathB 주문으로 오인해 취소하는 문제 방지
- broker truth가 불명확할 때 live 확정 대신 `ORDER_UNKNOWN` 또는 defer로 격리

다만 이 설계는 보수적이다. 수익 기회 회복이나 미체결 정리 자동화는 일부 늦어질 수 있으므로, 운영 모니터링 후 별도 단계로 확장한다.

## 현재 완료 범위

### 1. TTL 기준 시각 보정 완료

`entry_order_sent_at`, `entry_order_acked_at`를 저장하고 TTL age는 실제 주문 송신/승인 시각만 사용한다.

현재 정책:

- `entry_order_acked_at` 우선
- 없으면 `entry_order_sent_at`
- 둘 다 없으면 TTL 자동 취소를 시도하지 않음
- plan `created_at`, run `created_at`, run `updated_at`는 TTL age 기준으로 사용하지 않음

### 2. broker truth exact matching 강화 완료

`entry_execution_id`가 있으면 broker fill/open order는 exact `order_no` match만 신뢰한다.

현재 정책:

- exact fill 존재 -> `FILLED` 복구
- exact open order 존재 -> TTL cancel 요청 가능
- 다른 `order_no` fill 존재 -> `ORDER_UNKNOWN`
- 다른 `order_no` open order 존재 -> cancel 금지, defer
- `order_no` 없는 broker row는 후보가 정확히 1개일 때만 제한 fallback 허용
- 후보가 복수이면 ambiguous 처리

### 3. missing order identity는 보수적으로 격리

plan에 `entry_execution_id` 또는 `entry_qty`가 없으면 자동 복구/취소하지 않고 `ORDER_UNKNOWN`으로 보낸다.

이 처리는 수익 기회/정리 자동화 관점에서는 보수적이지만, broker-truth 안전 원칙에는 맞다.

이유:

- 주문번호가 없는 상태에서 ticker/price만으로 fill 확정하면 잘못된 체결 복구 위험이 있다.
- 주문번호가 없는 상태에서 ticker/price만으로 cancel하면 다른 주문을 취소할 위험이 있다.
- 현재 단계에서는 손실 방지보다 오주문 방지가 우선이다.

## 남은 한계

현재 구현은 아래 상황을 자동으로 최적화하지 않는다.

| 상황 | 현재 동작 | 한계 |
|---|---|---|
| plan에 `entry_execution_id` 없음 | `ORDER_UNKNOWN` 격리 | 실제 주문이 하나뿐이어도 자동 복구/취소하지 않음 |
| broker row에 `order_no` 없음 + 후보 1개 | 제한 fallback 허용 | broker row 품질에 따라 신뢰도 낮음 |
| broker row에 `order_no` 없음 + 후보 복수 | ambiguous | 정리 자동화 지연 |
| broker truth stale/missing | still_open/defer | 미체결 정리 지연 |
| cancel 요청 후 broker open order 지속 | still_open | 실제 취소 실패/지연 원인 분석은 별도 필요 |

## 운영 모니터링 항목

다음 reason 발생 빈도를 최소 1~2주 또는 30건 이상 PathB live 주문 기준으로 관찰한다.

### 핵심 reason

- `buy_pending_ttl_missing_order_identity`
- `buy_pending_ttl_fill_execution_mismatch`
- `buy_pending_ttl_open_order_execution_mismatch`
- `buy_pending_ttl_ambiguous_buy_fill`
- `buy_pending_ttl_ambiguous_open_order`
- `broker_truth_unavailable`

### 모니터링 지표

| 지표 | 해석 |
|---|---|
| missing order identity 발생 건수 | adapter/order ack 경로 누락 또는 legacy row 잔존 가능성 |
| execution mismatch 발생 건수 | broker truth row normalization 또는 주문번호 매핑 문제 가능성 |
| ambiguous 발생 건수 | ticker/price fallback 자동화 위험도 |
| TTL cancel requested 후 still_open 지속 시간 | 취소 API 성공 여부와 broker truth 반영 지연 여부 |
| TTL cancel confirmed 비율 | 미체결 정리 자동화의 실제 효과 |
| TTL 이후 filled 복구 비율 | 취소 전 체결 또는 delayed fill 빈도 |

## 후속 개선 판단 기준

아래 조건 중 하나 이상이 충족될 때 수익 기회/정리 자동화 개선을 별도 개발한다.

1. `buy_pending_ttl_missing_order_identity`가 반복 발생한다.
2. `ORDER_UNKNOWN` 격리 후 수동 확인 결과 실제 단일 주문이었던 사례가 누적된다.
3. TTL cancel requested 후 still_open이 자주 지속된다.
4. broker row `order_no` 누락이 반복되지만 후보가 단일인 경우가 많다.
5. 보수적 격리 때문에 주문 정리 지연 또는 자금 묶임이 실질 손익에 영향을 준다.

## 추후 개선안

### F1. missing execution_id read-only 후보 리포트

자동 복구/취소 전에 read-only 리포트를 만든다.

출력 항목:

- path_run_id
- ticker
- expected qty
- expected order price
- broker candidate count
- candidate rows
- side/price/qty match score
- fallback confidence
- recommended action: `manual_review`, `recover_fill_candidate`, `cancel_candidate`, `no_action`

기본 동작은 관찰 전용이며 주문 상태를 바꾸지 않는다.

### F2. high-confidence single-candidate recovery shadow

read-only 리포트에서 단일 후보가 반복적으로 정확하면 shadow recovery를 추가한다.

조건:

- broker truth fresh
- same ticker
- buy side
- qty 일치 또는 partial fill로 설명 가능
- price가 buy zone 또는 submitted price 근처
- 같은 ticker의 다른 open/fill 후보 없음
- 같은 시각대 PathA/PathB 중복 주문 없음

처음에는 상태 변경 없이 shadow log만 남긴다.

### F3. operator-approved limited auto recovery

shadow 결과가 충분히 안정적이면 운영자 승인 후 제한 자동화를 검토한다.

가능한 자동화:

- 단일 no-order-no fill 후보 -> `FILLED` 복구
- 단일 no-order-no open order 후보 -> cancel 후보 등록
- cancel requested 이후 open order 장기 지속 -> 재조회/재취소 후보 등록

필수 조건:

- 운영 파라미터 기본값 변경 전 승인
- broker truth fresh
- 중복 후보 없음
- fallback evidence payload 저장
- 재시도 횟수 제한
- Telegram/operator alert

### F4. TTL cleanup dashboard/ops summary

운영자가 수익 기회/정리 지연을 볼 수 있도록 요약을 추가한다.

요약 항목:

- pending TTL 대상 수
- TTL age 초과 수
- cancel requested 수
- still_open 수
- ORDER_UNKNOWN 수
- mismatch/ambiguous reason별 count
- longest pending buy age
- broker truth freshness

## 현재 단계 결론

지금 구현은 **운영 안전 방어선으로 충분**하다.

다음 단계는 즉시 자동화를 더 넣는 것이 아니라, 위 reason과 지표를 운영에서 관찰한 뒤 수익 기회/정리 자동화가 실제로 필요한지 판단한다.

특히 `entry_execution_id`가 없는 주문을 자동 복구/취소하는 기능은 지금 바로 live에 넣지 않는다. 먼저 read-only 리포트와 shadow 검증을 거친다.

## 완료 조건

후속 자동화 개발 여부는 다음 자료를 보고 결정한다.

- 최소 1~2주 운영 로그
- 또는 PathB live 주문 30건 이상
- TTL 관련 `ORDER_UNKNOWN`/defer reason 분포
- 수동 확인 결과 실제 오탐/미탐 사례
- 자금 묶임 또는 수익 기회 손실 사례

이 자료가 쌓이기 전까지는 현재의 보수적 broker-truth 정책을 유지한다.
