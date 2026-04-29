# Live Order Review Fix - 2026-04-29

## Goal

Review 지적 사항 5개를 단계별로 수정하고, 최종 QA 후 이 문서를 다시 검토해 누락된 항목이 없는지 확인한다.

## Scope

- `trading_bot.py`: 부분 체결 저장 보장, Path B 부분 체결 수량 전달 수정
- `kis_api.py`: US 주문 복구 timestamp를 조회일 기준으로 고정
- `dashboard/dashboard_server.py`: v2 ops API에 시장별 session date 전달
- `telegram_commander.py`: `/pos` broker truth 미커버 시장 local fallback 병합
- 관련 회귀 테스트 추가 및 실행

## Implementation Checklist

- [x] 1. 부분 체결 상태 저장 보장
  - 부분 체결 branch에서 포지션 추가 시 `positions_changed = True`
  - pending order 잔량/누적 체결/TTL 상태 변경 시 `pending_orders_changed = True`
  - `partial_due`로 `v2_unknown_recorded`가 설정될 때만 `pending_orders_changed = True`
  - `filled`가 있으면 기존 trade log/alert 처리 유지
  - `positions_changed` 기준으로 `_save_positions()` 실행
  - `pending_orders_changed or filled` 기준으로 `self.pending_orders = remaining`, `_save_pending_orders()` 실행

- [x] 2. Path B 부분 체결 수량 전달 수정
  - 이미 생성된 `partial_order`를 Path B `on_buy_fill()`에 전달
  - `partial_order["qty"]`는 실제 부분 체결 수량 유지
  - 원본 `order["qty"]`는 pending 잔량으로 유지

- [x] 3. US 주문 복구 timestamp 날짜 수정
  - `inquire_ccnl_us()` 결과를 `(query_date, row)`로 보관
  - row timestamp는 `query_date + order_time` 기준으로 파싱
  - query date 기준 파싱 실패 시 제한적으로 기존 parser fallback
  - 전날 주문이 오늘 주문으로 오인되지 않도록 lower-bound 필터 유지

- [x] 4. v2 ops API session date 전달
  - request market을 `KR`/`US` 기준으로 정규화
  - market이 지정된 경우 `_session_trade_date(market).isoformat()` 전달
  - market 미지정 시 기존 전체 요약 동작 유지

- [x] 5. Telegram `/pos` fallback 병합
  - `interface/v2_ops_summary.py`는 수정하지 않음
  - `telegram_commander.py`에서 market별 broker truth coverage 판단
  - broker truth가 usable이고 stale이 아닌 시장은 broker 결과 사용
  - missing/stale 시장은 `bot.risk.positions` local fallback 병합
  - `(market, ticker)` 기준으로 중복 표시 방지
  - broker truth가 전혀 없으면 기존 local renderer 유지

## QA Checklist

- [x] 부분 체결만 발생해도 positions/pending orders 저장 테스트
- [x] Path B partial fill에 실제 부분 체결 수량이 전달되는지 테스트
- [x] US 전날 order row가 오늘 제출 주문으로 매칭되지 않는지 테스트
- [x] `/api/v2/ops?market=US`가 session date를 명시하는지 테스트
- [x] `/pos`가 broker truth 미커버 시장의 local position을 표시하는지 테스트
- [x] 관련 Python 테스트 실행
- [x] 이 문서와 실제 변경 내용을 대조해 누락 확인

## QA Result

- `python -m py_compile trading_bot.py kis_api.py dashboard\dashboard_server.py telegram_commander.py tests\test_live_order_reconciliation.py tests\test_kis_kr_order_safety.py tests\test_dashboard_pathb.py tests\test_telegram_positions.py` 통과
- `python -m unittest tests.test_live_order_reconciliation tests.test_kis_kr_order_safety tests.test_dashboard_pathb tests.test_telegram_positions` 통과, 15 tests
- `python -m unittest tests.test_v2_phase6 tests.test_live_pathb_stuck tests.test_order_unknown_reconciliation tests.test_pathb_runtime tests.test_pathb_sell_reconcile` 통과, 27 tests

## Final Review

- 5개 리뷰 지적 사항 모두 코드 변경과 테스트로 커버했다.
- `interface/v2_ops_summary.py`는 의도대로 수정하지 않았고, Telegram `/pos` fallback은 `telegram_commander.py` 안에서 처리했다.
- 최종 확인 시 신규 누락 항목은 발견하지 못했다.

## Notes

- 기존 worktree에 다른 변경이 많으므로 이 작업 범위 밖 파일은 건드리지 않는다.
- live 주문 상태 저장과 operator visibility가 핵심이므로 저장 조건과 fallback 기준을 과도하게 넓히지 않는다.
