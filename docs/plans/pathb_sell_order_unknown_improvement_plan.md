# PathB SELL ORDER_UNKNOWN 개선 플랜

## 배경

2026-05-20 AXTI 사례:
- PathB가 limit sell @113.68 발송 → ORDER_ACKED (order_no: 0030954440)
- 15분 TTL 만료 시점에 KIS `미체결조회` API가 해당 주문을 반환하지 않음
- `sell_fill_not_confirmed:ttl_expired` → ORDER_UNKNOWN 전환
- US 전체 PathB entry scan 차단 (scope=market)
- trading_bot auto sell review가 중복 매도 시도 → `주문수량이 가능수량보다 큽니다` 오류

## 이미 적용된 수정 (2026-05-21)

- `trading_bot.py:18282` `_pathb_sell_in_flight_state()`: 매도 전 PathB sell 상태 감지
- `trading_bot.py:19059` `_execute_sell()`: blocked면 reconcile 시도 후 skip
- `runtime/pathb_runtime.py:4426`: `path_run_id` 직접 조회로 cross-session ORDER_UNKNOWN reconcile 지원

## 추가 개선 항목

### [1] SELL TTL 차등화 (우선순위: 중)

**문제**: 현재 `pathb_sell_pending_ttl_minutes=15`가 limit/market 구분 없이 단일 적용.
현재가 대비 5% 이상 위에 걸린 limit sell은 15분 안에 체결될 가능성이 낮음.

**개선 방향**:
- `_sell_ttl_expired()` 내부에서 limit 가격 vs 현재가 괴리를 계산
- 괴리가 크면 TTL 연장 (예: 60분)
- 환경변수 `PATHB_SELL_LIMIT_FAR_TTL_MINUTES` 추가

**위치**: `runtime/pathb_runtime.py` `_sell_ttl_expired()` (line ~4127)

---

### [2] ORDER_UNKNOWN entry scan 차단 범위 축소 (우선순위: 낮, 복잡도 높음)

**문제**: 현재 SELL ORDER_UNKNOWN 발생 시 해당 ticker가 아니라 market 전체 PathB entry scan이 차단됨.
AXTI sell ORDER_UNKNOWN 때문에 MRVL, ALAB, SMCI 등 신규 진입도 모두 막힘.

**개선 방향**:
- `_order_unknown_blocked()` 에서 ORDER_UNKNOWN 성격을 판별
  - SELL 계통 (`sell_fill_not_confirmed`, `exit_sell_missing_still_held` 등) → 해당 ticker만 신규 매수 차단
  - BUY 계통 → 기존처럼 market 전체 차단 유지
- `_new_buy_block_state()` 에서 ticker 단위 차단 조건 추가

**주의**: 로직 복잡도가 높고 안전 game이 바뀌므로 shadow 검증 후 적용.

**위치**: `runtime/pathb_runtime.py` `_order_unknown_blocked()` (line ~6408), `_new_buy_block_state()` (line ~6436)

---

### [3] SELL ORDER_UNKNOWN Telegram escalation (우선순위: 중)

**문제**: `sell_skipped` 로그는 남지만 운영자에게 실시간 알림 없음.
reconcile로도 풀리지 않는 ORDER_UNKNOWN은 session end까지 수동 개입 없이는 포지션이 안 팔림.

**개선 방향**:
- `_execute_sell()` 에서 `sell_skipped detail=pathb_sell_in_flight` 기록 시 Telegram 알림 발송
  - 메시지: `[PathB SELL 대기 중] {ticker} 매도 대기 order={order_no} run={path_run_id}`
- 반복 알림 방지: 동일 path_run_id로 1회만 발송

**위치**: `trading_bot.py` `_execute_sell()` (line ~19059 근방)

---

### [4] sell 감지 heuristic 개선 (우선순위: 낮)

**현재 코드** (`trading_bot.py:18323`):
```python
"sell_" in str(plan.get("order_unknown_detail", "") or "").lower()
```

**문제**: BUY ORDER_UNKNOWN detail에 "sell_"이 우연히 포함되면 false positive.

**개선 방향**:
```python
int(plan.get("exit_qty", 0) or 0) > 0
or bool(str(plan.get("sell_order_sent_at", "") or "").strip())
```
으로 대체. 더 명시적이고 오탐 가능성 낮음.

**위치**: `trading_bot.py` `_pathb_sell_in_flight_state()` (line ~18323)

## 구현 순서 권장

1. [3] Telegram escalation — 빠르게 운영 가시성 확보
2. [1] SELL TTL 차등화 — 근본 원인 부분 해소
3. [4] heuristic 개선 — 소규모 안정성 개선
4. [2] 차단 범위 축소 — 복잡도 높으므로 shadow 검증 후
