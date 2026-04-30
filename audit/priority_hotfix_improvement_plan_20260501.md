# 우선 운영 핫픽스 개선 계획

작성일: 2026-05-01

## 목표

리뷰에서 지적된 운영 리스크 중 즉시 장애나 잘못된 거래 판단으로 이어질 수 있는 항목을 우선 수정한다. 수정 후 항목별 검증, 최종 QA, 본 문서 대비 누락/차이점 확인을 수행한다.

## 적용 순서와 상세 개선 방향

### 1. 필수 인스턴스 상태 초기화 복구

- 대상: `trading_bot.py`
- 확인 대상:
  - `_or_high`
  - `_hist_fill_last_ts`
  - `_ticker_exclude_log`
- 개선 방향:
  - 주석 안에 묻힌 대입문은 독립 실행문으로 복구한다.
  - fresh bot instance에서 opening range, history backfill, ticker exclude 상태 필드가 항상 생성되도록 한다.
  - 기존 구조를 유지하고, 불필요한 리팩터링은 하지 않는다.
- 검증 기준:
  - `_or_high`, `_hist_fill_last_ts`, `_ticker_exclude_log` 대입문이 주석 라인이 아닌 실행 라인에 존재한다.
  - Python 컴파일 검증을 통과한다.

### 2. 세션 시작 per-session reset 복구 확인

- 대상: `trading_bot.py`
- 확인 대상:
  - `_session_events`
  - `_entry_blocked`
  - `_order_error_count`
  - `decision_event_log`
  - `_session_closed_tickers`
  - `_v2_same_day_stop_tickers`
  - `_daily_sl_count`
  - `_index_history`
- 개선 방향:
  - 세션 시작 시 이전 세션 상태가 다음 세션에 남지 않도록 reset 문장을 실행 코드로 유지한다.
  - 이미 복구되어 있으면 추가 변경 없이 검증 항목으로 처리한다.
- 검증 기준:
  - 위 상태 reset 문장이 `session_open()` 흐름에서 실행 라인으로 존재한다.
  - Python 컴파일 검증을 통과한다.

### 3. startup guard 계산 복구 확인

- 대상: `trading_bot.py`
- 확인 대상:
  - `_session_startup_guard_sec[market] = self._compute_startup_guard_sec(market, trigger)`
- 개선 방향:
  - 장중 재시작이나 장마감 임박 open에서 startup guard bypass 계산이 유지되도록 한다.
  - 이미 복구되어 있으면 추가 변경 없이 검증 항목으로 처리한다.
- 검증 기준:
  - startup guard 대입문이 WebSocket start 이후 session open 시점에 실행 라인으로 존재한다.
  - Python 컴파일 검증을 통과한다.

### 4. 주문 성공 후 order error count clear 복구 확인

- 대상: `trading_bot.py`
- 확인 대상:
  - `_order_error_count.pop(_s_tk, None)`
- 개선 방향:
  - 성공 주문 이후 과거 실패 카운트가 남아 non-consecutive failure를 연속 실패처럼 처리하지 않도록 한다.
  - 이미 복구되어 있으면 추가 변경 없이 검증 항목으로 처리한다.
- 검증 기준:
  - 성공 주문 분기 뒤에 clear 문장이 실행 라인으로 존재한다.
  - Python 컴파일 검증을 통과한다.

### 5. broker error marker 복구

- 대상: `trading_bot.py`
- 확인 대상:
  - `매매불가`
  - `주문처리가 안되었습니다`
- 개선 방향:
  - 깨진 문자열 marker를 정상 한국어 marker로 복구한다.
  - broker reject reason에 위 marker가 포함되면 해당 티커를 order blocked 처리하도록 한다.
  - 이번 핫픽스에서는 동작 복구를 우선하고, 대규모 인코딩 정리는 별도 작업으로 분리한다.
- 검증 기준:
  - 정상 한국어 marker가 코드에 존재한다.
  - marker 매칭 분기가 `_mark_us_order_blocked()`를 호출한다.
  - Python 컴파일 검증을 통과한다.

### 6. US PathB carry review 가격 전달 수정

- 대상: `runtime/pathb_runtime.py`
- 확인 대상:
  - carry review advisor 입력의 `current_price`
  - carry review advisor 입력의 `display_current_price`
- 개선 방향:
  - US carry review에서 advisor가 우선 참조하는 `display_current_price`도 현재 native USD 가격으로 갱신한다.
  - `current_price`와 `display_current_price` 기준이 어긋나 stale display price가 쓰이지 않도록 한다.
- 검증 기준:
  - `_run_pre_close_carry_review()`에서 `current_price`와 `display_current_price`가 모두 현재 가격으로 갱신된다.
  - Python 컴파일 검증을 통과한다.

## 최종 QA

- `python -m py_compile trading_bot.py runtime/pathb_runtime.py`
- 코드 패턴 확인:
  - `_or_high` 실행 라인 존재
  - per-session reset 실행 라인 존재
  - startup guard 계산 실행 라인 존재
  - 성공 주문 후 `_order_error_count` clear 실행 라인 존재
  - broker error marker 정상 한국어 문자열 존재
  - carry review의 `display_current_price` 갱신 존재
- 본 문서의 적용 순서와 실제 수정/검증 결과를 대조해 누락이나 차이점을 기록한다.

## 진행 결과

- [x] 1. 필수 인스턴스 상태 초기화 복구
  - `_or_high`를 주석 밖 실행 대입문으로 복구했다.
  - `_hist_fill_last_ts`, `_ticker_exclude_log`는 실행 대입문으로 존재함을 확인했다.
- [x] 2. 세션 시작 per-session reset 복구 확인
  - `_session_events`, `_entry_blocked`, `_order_error_count`, `decision_event_log`, `_daily_sl_count`, `_session_closed_tickers`, `_v2_same_day_stop_tickers`, `_index_history` reset 실행 라인을 확인했다.
- [x] 3. startup guard 계산 복구 확인
  - `_session_startup_guard_sec[market] = self._compute_startup_guard_sec(market, trigger)` 실행 라인을 확인했다.
- [x] 4. 주문 성공 후 order error count clear 복구 확인
  - 성공 주문 이후 `_order_error_count.pop(_s_tk, None)` 실행 라인을 확인했다.
- [x] 5. broker error marker 복구
  - 깨진 marker를 `매매불가`, `주문처리가 안되었습니다`로 복구했다.
- [x] 6. US PathB carry review 가격 전달 수정
  - US carry review advisor 입력에서 `display_current_price`를 최신 native USD 가격으로 갱신하도록 수정했다.
  - `tests/test_pathb_runtime.py`에 US carry review 회귀 테스트를 추가했다.
- [x] 최종 QA
  - `python -m py_compile trading_bot.py runtime/pathb_runtime.py tests/test_pathb_runtime.py` 통과.
  - `python -m pytest tests/test_pathb_runtime.py -q` 통과: 16 passed, eventlet deprecation warning 2건.
  - 정적 패턴 검증에서 문서의 모든 확인 대상 OK.
- [x] 문서 대비 누락/차이점 확인
  - 누락 없음.
  - 일부 항목은 이미 실행 코드로 복구되어 있어 추가 수정 없이 검증 완료로 처리했다.

## 추가 점검 결과

요청에 따라 우선 핫픽스 이후 확장 QA를 진행했다.

- `python -m pytest -q`
  - 최종 결과: 398 passed, 2 skipped, 2 warnings.
  - skipped:
    - `_sim_test.py`: KIS 네트워크를 호출하는 스크립트성 시뮬레이션이라 pytest 수집 시 skip 처리.
    - `test_kr_trade.py`: CLI로 실행하는 KIS smoke 스크립트라 pytest 수집 시 skip 처리.
  - warnings:
    - eventlet/greenlet의 `distutils.version` deprecation warning 2건. 이번 수정 범위와 무관.
- 추가 보정:
  - 한국어 strategy alias `갭+눌림`, `갭 + 눌림`을 `gap_pullback`으로 정규화.
  - 한국어 risk tag `저유동성`, `변동성`, `뉴스`, `테마` 등이 size cap에 반영되도록 보강.
  - watch-only reason label의 깨진 한글 문구를 복구.
  - soft-exit 후보 조회에서 테스트/운영 stub이 `risk.positions`를 갖지 않아도 안전하게 false 반환하도록 방어.
  - live dashboard equity history의 현재 broker asset label을 현재 날짜 기준으로 보정.
  - analyst prompt에 기존 테스트가 기대하는 `recommended_strategy and max_position_pct` 계약 문구를 유지하면서 신규 cap 문구도 보존.
  - `hold_advisor.py`의 Python 3.9 비호환 `X | None` 타입 표기를 `Optional[X]`로 변경.
  - `hold_advisor` decision log 호출/시그니처 경로를 현재 테스트 기준으로 확인.
- 추가 검증:
  - `python -m py_compile trading_bot.py runtime/pathb_runtime.py tests/test_pathb_runtime.py dashboard/dashboard_server.py minority_report/analysts.py minority_report/hold_advisor.py _sim_test.py test_kr_trade.py` 통과.
  - `git diff --check` 통과. CRLF 변환 warning만 있음.
  - 핫픽스 및 추가 QA 정적 패턴 검증 모두 OK.
