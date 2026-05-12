# decisions.db 운영 역할과 복구/개선 계획 (2026-05-12)

## 결론

`data/ml/decisions.db`는 실시간 매매 판단의 1차 truth는 아니다. 현재 매수/매도 운영은 브로커 잔고/주문, 런타임 state, `state/live_decisions.jsonl`, 현재 후보/selection 결과를 중심으로 돈다.

하지만 `decisions.db`는 버려도 되는 DB가 아니다. 운영 중 발생한 신호, 차단, 체결, 청산 성과를 누적하는 의사결정/성과 원장이고, ML 학습, 품질 리뷰, 운영 통계, 일부 복구 연결에 사용된다. 지금처럼 테스트 데이터로 오염되면 당장 주문이 멈추지는 않더라도 학습과 운영 리뷰가 왜곡된다.

## 왜 써야 하는가

1. 의사결정 원자료 보존
   - 매 사이클의 `BUY_SIGNAL`, `NO_SIGNAL`, `BLOCKED`, `SKIPPED`를 남긴다.
   - 단순 체결 로그만으로는 "왜 안 샀는지", "어떤 조건에서 막혔는지"를 복원하기 어렵다.

2. 성과 분석과 ML 학습
   - 체결 여부, 청산 PnL, forward return을 붙여 전략/조건별 품질을 본다.
   - `entry_priority_score`, 전략별 fired flag, block reason을 학습 데이터로 사용할 수 있다.

3. 운영 리뷰 지표 산출
   - 최근 2주 리뷰 지표 중 일부는 `decisions.db`와 `ticker_selection_log.db`에서 파생된다.
   - 예: blocked ratio, continuation 평균 PnL, trade_ready 이후 성과, watch-only missed runup.

4. 제한적 복구 보조
   - 브로커 복구 과정에서 오늘 `BUY_SIGNAL AND filled=0`인 `decision_id`를 best-effort로 찾는다.
   - 핵심 truth는 아니지만, 정상 데이터가 있으면 포지션/주문 연결 품질이 좋아진다.

## 어디에 쓰이는가

### `data/ml/decisions.db`

- 기록:
  - `trading_bot.py`가 후보 평가 결과를 `ml.db_writer.write_decision()`으로 기록한다.
  - 체결 확인 시 `filled/order_status`를 업데이트한다.
  - 청산 시 `entry_price`, `exit_price`, `exit_reason`, `hold_days`, `pnl_pct`를 업데이트한다.
  - `ml/forward_updater.py`가 가격 CSV를 기반으로 `forward_1d/3d/5d`를 채운다.

- 읽기:
  - 운영 리뷰 통계 일부.
  - 브로커/포지션 복구 시 미체결 `decision_id` 연결.
  - ML 분석 스크립트와 리포트.

### `data/ticker_selection_log.db`

- 기록:
  - Claude selection 결과, watchlist, `trade_ready`, veto, risk tag, 추천 전략, 실제 signal/traded/PnL을 기록한다.
  - `ticker_selection_db.update_forward_returns()`가 selection 후보의 forward return과 runup/drawdown을 채운다.

- 읽기:
  - selection 품질 리뷰.
  - trade_ready 전환율, watch-only missed runup, ATR-blocked missed runup 같은 운영 지표.
  - 대시보드/시뮬레이션/개선 리포트.

### 실시간 운영 truth와의 관계

- 1차 truth:
  - 브로커 보유 종목
  - 브로커 미체결 주문
  - `state/live_open_positions.json`
  - `state/live_pending_orders.json`
  - `state/live_decisions.jsonl`
  - 현재 런타임 후보/selection 상태

- 보조/분석 truth:
  - `data/ml/decisions.db`
  - `data/ticker_selection_log.db`

따라서 DB가 깨졌다고 해서 즉시 매매 엔진이 멈추는 구조는 아니지만, 장기 품질 개선과 성과 판단은 깨진다.

## 현재 확인된 문제

1. `decisions.db`가 테스트 데이터로 오염됨
   - 현재 `decisions` 테이블에는 6행만 남아 있다.
   - 남은 행 패턴은 `ml/test_full.py`의 테스트 데이터와 일치한다.
   - 예: `005930`, `NVDA`, `000660`, `035420`, `TSLA`, `068270`.
   - `005930`에는 테스트용 `forward_1d=1.8`, `forward_3d=3.2`, `forward_5d=5.1`이 들어가 있다.

2. 테스트가 운영 DB를 직접 삭제함
   - `ml/test_full.py`가 `data/ml/decisions.db`에 직접 연결한 뒤 `DELETE FROM decisions`를 실행한다.
   - 이 테스트를 운영 폴더에서 실행하면 실제 운영 ML 원장이 삭제된다.

3. 로그와 현재 DB 상태가 불일치
   - 2026-05-12 16:20 로그에는 KR ML forward pending rows가 1109건으로 찍혔다.
   - 현재 `decisions.db`에는 6행뿐이므로, 이후 테스트/검증 실행으로 운영 행이 지워졌을 가능성이 높다.

4. forward update의 `updated=0` 해석이 혼동됨
   - 당일 또는 미래 영업일 가격이 없으면 `forward_3d/5d`는 당연히 채워지지 않는다.
   - `missing_csv=0`이면 가격 파일은 있으나 아직 미래 데이터가 부족해서 skip된 경우가 많다.
   - 이것은 정상 대기 상태일 수 있으나, 오염된 `decisions.db`에서는 정상 여부를 판단할 수 없다.

5. 외부 데이터 DB는 별도 문제
   - `data/external_market_data.sqlite`는 2026-05-10 이후 갱신이 없고 최근 API run은 네트워크/권한 오류로 실패했다.
   - 이 DB는 `decisions.db`와 별개지만, 외부 데이터 품질 점검 대상이다.

## 운영 영향

### 즉시 영향이 낮은 부분

- 실시간 매수/매도 판단 자체.
- 브로커 truth 기반 포지션 동기화.
- `state/live_decisions.jsonl` 기반 당일 청산/일일 PnL 복구.
- 현재 selection 로그 기록. `ticker_selection_log.db`는 계속 갱신되고 있다.

### 영향이 있는 부분

- ML 학습 데이터 신뢰도.
- 전략/조건별 성과 분석.
- 운영 리뷰 리포트.
- `decision_id` 연결 기반 보조 복구.
- `decisions.db`를 참조하는 대시보드/분석 스크립트.

## 개선 계획

### P0: 추가 오염 방지

1. `ml/test_full.py`를 운영 DB가 아닌 임시 DB로 바꾼다.
   - `tempfile.TemporaryDirectory()`를 사용한다.
   - 또는 `ML_DECISIONS_DB_PATH` 같은 환경 변수로 테스트 DB 경로를 주입할 수 있게 한다.
   - 테스트 시작 시 운영 경로 `data/ml/decisions.db`를 감지하면 즉시 실패하도록 가드한다.

2. `ml/db_writer.py`에 DB 경로 override를 공식화한다.
   - 기본값은 현재처럼 `data/ml/decisions.db`.
   - 테스트에서는 반드시 별도 경로 사용.
   - 운영 DB 삭제성 작업은 명시적 `ALLOW_PROD_DB_MUTATION=1` 같은 가드 없이는 금지.

3. 테스트/검증 문서에서 `python -m unittest ml.test_full` 실행 전 운영 DB 보호 조건을 명시한다.

### P1: 현재 DB 복구

1. 백업 탐색
   - `data/ml/decisions_before_backfill_refresh_20260403_221805.db`
   - Windows 파일 히스토리/수동 백업
   - 최근 배포/작업 전 DB 복사본

2. 백업이 있으면 복구
   - 복구 전 현재 오염 DB를 `decisions_contaminated_YYYYMMDD_HHMMSS.db`로 보존한다.
   - 백업 DB를 운영 경로에 복사한다.
   - WAL/SHM 파일 정합성을 확인한다.

3. 백업이 없으면 부분 재구성
   - `state/live_decisions.jsonl`에서 entry/closed 이벤트를 재구성한다.
   - `data/ticker_selection_log.db`에서 selection/strategy/PnL 일부를 보강한다.
   - `data/price/{kr,us}` CSV로 forward return을 다시 계산한다.
   - 단, 매 사이클의 `NO_SIGNAL`/`BLOCKED` 상세 진단은 완전 복구가 어렵다.

### P2: 운영 안정화

1. DB health check 추가
   - `decisions` 행 수가 전일 대비 비정상적으로 급감하면 경고.
   - 최근 3영업일 row 수가 0이면 경고.
   - 테스트 패턴 tickers와 고정 forward 값이 운영 DB에 있으면 경고.

2. repo health와 live preflight에 DB 상태 포함
   - `decisions.db` row count.
   - `ticker_selection_log.db` 최신 date.
   - `forward_*` pending 분포.
   - `data_source='live'` 최신성.

3. 업데이트 로그 개선
   - `updated=0 skipped=N`만 찍지 말고 skip reason을 분리한다.
   - 예: `future_price_not_available`, `session_date_missing`, `base_close_invalid`, `missing_csv`.

4. 운영/분석 DB 역할 분리
   - 실시간 복구/주문 truth는 계속 state/jsonl/broker 중심으로 유지한다.
   - ML DB는 분석/학습 원장으로 유지하되, 삭제성 테스트가 접근하지 못하게 한다.

## 완료 기준

1. `ml/test_full.py`가 운영 `data/ml/decisions.db`를 더 이상 수정하지 않는다.
2. 테스트 실행 후 운영 `decisions.db` row count가 변하지 않는다.
3. `repo_health_check` 또는 별도 DB health check가 오염 패턴을 탐지한다.
4. `ticker_selection_log.db`와 `decisions.db`의 최신 날짜/row count를 운영자가 한 번에 확인할 수 있다.
5. 복구 또는 재구성 후 `ml/forward_updater.py`가 정상 실행되고, skip reason이 해석 가능하다.

## 운영 판단 원칙

- 당장 매매 안전성은 브로커 truth와 runtime state를 먼저 본다.
- `decisions.db`는 매매 실행의 필수 truth가 아니라 성과/학습 원장이다.
- 하지만 성과 원장이 깨지면 다음 개선 판단이 왜곡된다.
- 따라서 지금 우선순위는 "운영 중단"이 아니라 "추가 오염 방지 후 복구"다.

