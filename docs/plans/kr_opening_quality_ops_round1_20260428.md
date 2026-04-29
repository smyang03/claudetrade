# KR Opening Quality / Ops Correctness Round 1 - 2026-04-28

## 목적

이번 라운드는 수익률 파라미터 튜닝이 아니다.

목표는 실전 운영에서 드러난 구조적 결함을 줄이고, 후보 품질을 매일 비교할 수 있는 기록 축을 만드는 것이다.

- 브로커 truth와 로컬 lifecycle 상태 불일치를 줄인다.
- Path A와 Path B가 서로 다른 Safety 결과를 내지 않게 한다.
- KOSDAQ 후보 풀 누락처럼 시장 커버리지가 깨지는 문제를 감지하고 고친다.
- 좋은 후보가 있었는데 왜 주문 경로로 이어지지 않았는지 기록한다.
- 09:05 fresh screener가 실제로 후보 품질을 개선하는지 측정한다.

## 이번 라운드에서 하지 않는 것

- 자동 리프라이싱.
- 자동 추격 매수.
- Claude EXIT_REVIEW 결과를 즉시 실매도에 반영.
- 손절/목표가 자동 튜닝.
- 바구니별 최소 슬롯을 live 후보 입력에 강제 적용.
- 하루 데이터 기반 자동 파라미터 변경.
- 대시보드 대규모 재작성.

## 완료 조건

- 각 Phase 종료 시 해당 Phase 테스트를 실행한다.
- 각 Phase 종료 시 이전 Phase 핵심 검증이 깨지지 않았는지 같이 확인한다.
- 최종 QA 후 이 문서를 다시 읽고 구현 누락 여부를 체크한다.
- 누락이 있으면 수정 후 재검증한다.

## Phase 0 - 설계/원인 확정

### 0.1 KOSDAQ raw=0 원인 확정

확인 항목:

- KOSDAQ 수집 함수가 실제 호출되는지.
- KIS/FMP/캐시 중 어느 소스에서 KOSDAQ이 0이 되는지.
- API 파라미터, market code, board code가 맞는지.
- 토큰/rate limit/필터링 문제인지 구분.
- raw는 있는데 필터 후 0인지, raw 자체가 0인지 구분.

결과물:

- 원인 메모.
- 수정 대상 파일 목록.

### 0.2 Path A/B Safety 공통 기준 확정

공통 기준:

- `DAILY_LOSS_LIMIT`: 동일한 일일 손실 기준을 사용한다.
- `MAX_POSITIONS`: broker truth + local positions를 함께 본다.
- `MAX_DAILY_ENTRIES`: lifecycle/event_store 기준으로 센다.
- `ORDER_UNKNOWN`: event_store 기준으로 Path A/B 모두 차단한다.
- `ALREADY_HOLDING`: broker truth를 우선한다.
- `PENDING_ORDER_EXISTS`: broker open order + local pending order를 함께 본다.

구현 원칙:

- Path A와 Path B가 같은 계좌 위험 조건에서 서로 다른 결론을 내면 안 된다.
- 블록 사유는 reason_code로 남긴다.

### 0.3 002780 lifecycle 오염 경로 확정

확인 항목:

- 002780이 `CLOSED`였는지, `FILLED`였는지, `ORDER_UNKNOWN`으로 언제 바뀌었는지.
- `recover_on_startup()`이 CLOSED/FILLED를 잘못 강등했는지.
- broker ccld에 buy+sell full fill이 있는지.
- Path A evidence와 Path B evidence가 섞였는지.

원칙:

- 이미 broker sell ccld로 청산이 확인된 Path B run은 `ORDER_UNKNOWN`으로 되돌리지 않는다.
- broker truth가 불명확한 경우에만 `ORDER_UNKNOWN`을 유지한다.

### 0.4 ORDER_ACKED cancel_above 설계 확정

대상:

- Path B buy order가 `ORDER_SENT` 또는 `ORDER_ACKED` 상태.

흐름:

```text
current_price > cancel_if_open_above
-> cancel_requested_at 없음: KIS 취소 요청 + cancel_requested_at 기록
-> cancel_requested_at 있음: 중복 취소 요청 금지, open_orders 재조회만 수행
-> open_orders에서 사라짐: CANCELLED
-> 아직 열림: ORDER_ACKED 유지
-> 조회 실패 또는 TTL 초과: ORDER_UNKNOWN
```

제약:

- 이번 라운드에서는 새 `CANCEL_SENT` enum을 추가하지 않는다.
- 중복 cancel 요청을 보내지 않는다.

### 0.5 후보 품질 저장 위치 확정

초기 저장 위치:

- `logs/screener_quality/YYYYMMDD_KR_candidates.jsonl`

기록 필드:

- `timestamp`
- `market`
- `phase`
- `ticker`
- `name`
- `price`
- `change_rate`
- `turnover`
- `volume_ratio`
- `bucket`
- `status`
- `input_to_claude`
- `reason`
- `excluded_reason`

초기 status:

- `TRADE_READY`
- `WATCH`
- `VETO`
- `NOT_IN_PROMPT`
- `SCREENER_ONLY`

### 0.6 09:05 fresh screener / Claude 호출 정책 확정

정책:

- 09:05에는 fresh market data 기반 screener를 실행한다.
- Claude JUDGE는 조건부로만 재호출한다.

Claude 재호출 조건:

- `new_top_gainer_not_in_prompt >= 3`
- 또는 `top20_coverage < 50%`
- 또는 기존 trade_ready 2개 이상이 급격히 약화.
- 또는 신규 high-liquidity 후보가 2개 이상 발생.

필수 로그:

```text
[opening_fresh_quality] top20_coverage=... not_in_prompt=... new_high_liq_candidates=... judge_triggered=true/false reason=...
```

검증:

- 08:54, 09:05, 10:10 후보 품질을 비교할 수 있어야 한다.

## Phase 1 - KOSDAQ raw=0 감지 및 수정

## Phase 0 확인 결과

- KOSDAQ 수집은 `kis_api.screen_market_kr()`에서 `market_div="Q"`로 호출된다.
- 현재 KOSDAQ 호출 실패는 debug 로그로만 남아 운영자가 보기 어렵다.
- KOSDAQ raw가 0이어도 screener audit에는 남지만 runtime WARN이 없다.
- Path A 진입 전 `PathExecutionArbiter -> SameDayReentryGuard -> V2 SafetyGate` 순서는 이미 있다.
- Path A `DAILY_LOSS_LIMIT`는 realized PnL 기반 `_daily_pnl_pct()`를 쓰고, Path B는 equity 기반 `_market_daily_return_pct()`를 써서 결과가 갈릴 수 있다.
- Path B `recover_on_startup()`은 `FILLED/SELL_SENT/SELL_ACKED/SELL_PARTIAL_FILLED`가 로컬 포지션에 없으면 broker ccld 확인 전에 `ORDER_UNKNOWN`으로 바꿀 수 있다.
- Path B `cancel_if_open_above`는 WAITING plan 취소는 처리하지만, `ORDER_SENT/ORDER_ACKED` buy order가 이미 나간 뒤의 실제 KIS 취소는 없다.
- 이번 라운드에서는 새 `CANCEL_SENT` status를 만들지 않고, plan_json의 `cancel_requested_at`/`cancel_above_after_ack`로 관리한다.

작업:

- KOSDAQ raw/final count 로그를 명확히 분리한다.
- raw=0이 장중 발생하면 WARN을 남긴다.
- raw=0 원인에 맞춰 수집 경로를 수정한다.
- KOSDAQ이 0인 상태에서 조용히 KOSPI/cache 중심으로만 후보가 구성되지 않게 한다.

검증:

- KOSDAQ raw count가 0일 때 WARN이 찍힌다.
- raw>0이면 정상 count가 로그에 남는다.
- 기존 KR screener 흐름이 깨지지 않는다.

## Phase 2 - Path A/B Safety 통일

작업:

- Path A 진입 경로에서 PathExecutionArbiter/Safety 결과가 실제 주문 전 적용되는지 확인/수정한다.
- Path B에서 막힌 계좌 보호 조건이 Path A에서 우회되지 않게 한다.
- `DAILY_LOSS_LIMIT`, `ORDER_UNKNOWN`, `ALREADY_HOLDING`, `PENDING_ORDER_EXISTS`, `MAX_POSITIONS`, `MAX_DAILY_ENTRIES`를 Path A/B 공통 결과로 맞춘다.

검증:

- 같은 ticker/market/session에서 Path B `DAILY_LOSS_LIMIT` 차단이면 Path A도 차단된다.
- 같은 ticker에 unresolved `ORDER_UNKNOWN`이 있으면 Path A/B 모두 신규 진입 차단된다.
- broker 보유 중 ticker는 Path A/B 모두 신규 매수 차단된다.
- Safety block event에 `path_type`과 reason_code가 남는다.

## Phase 3 - Path B 상태 오염 방지

작업:

- 이미 `CLOSED`인 Path B run을 startup recovery가 `ORDER_UNKNOWN`으로 되돌리지 않게 한다.
- `FILLED`인데 broker position이 없는 경우 broker sell ccld를 확인해 `CLOSED`로 복구한다.
- broker ccld가 없으면 `ORDER_UNKNOWN`으로 유지하되 근거를 plan_json에 남긴다.
- Path A 체결 근거와 Path B 체결 근거를 섞어 복구하지 않는다.

검증:

- 002780 형태: buy+sell ccld full fill이면 CLOSED 유지/복구.
- CLOSED row가 startup recovery 후 ORDER_UNKNOWN으로 강등되지 않는다.
- Path A evidence가 있으면 `path_a_origin_possible`로 분리 표시된다.

## Phase 4 - Path B ORDER_ACKED cancel_above 실제 취소

작업:

- Path B buy pending 상태에서 현재가가 `cancel_if_open_above`를 초과하면 취소 요청을 보낸다.
- `cancel_requested_at`이 이미 있으면 중복 취소 요청을 보내지 않는다.
- open_orders 재조회로 취소 여부를 확인한다.
- 확정 불가 시 ORDER_UNKNOWN으로 escalaton 한다.

기록:

- `cancel_above_after_ack`
- `cancel_requested_at`
- `cancel_confirmed_by_broker`
- `cancel_unknown_after_ttl`

검증:

- SK증권 형태: 지정가 미체결 후 가격이 cancel_above를 넘으면 취소 요청.
- 두 번째 scan에서 중복 취소 요청 없음.
- open_orders에서 사라지면 CANCELLED.
- 취소 확인 실패/TTL 초과 시 ORDER_UNKNOWN.

## Phase 5 - 후보 품질 JSONL 기록

작업:

- `logs/screener_quality/YYYYMMDD_KR_candidates.jsonl` 기록기를 추가한다.
- Claude 입력 후보와 입력되지 못한 top gainer를 함께 기록한다.
- 좋은 후보였지만 주문 경로가 없던 종목을 `SCREENER_ONLY` 또는 `NOT_IN_PROMPT`로 추적한다.
- `excluded_reason`을 저장한다.

검증:

- 09:05/10:10 실행 결과가 JSONL에 남는다.
- 138360 형태의 "좋은 후보였지만 주문 없음"이 사후 분석 가능하다.
- 기존 판단 JSON 저장 흐름과 충돌하지 않는다.

## Phase 6 - 09:05 fresh screener 정책 반영

작업:

- 09:05 fresh market data를 사용해 opening refresh screener를 실행한다.
- 08:54 판단과 09:05 fresh 결과를 비교한다.
- 조건 충족 시에만 Claude JUDGE를 재호출한다.
- 조건 미충족 시에는 quality log만 남긴다.

검증:

- 09:05 fresh screener가 오래된 08:54 가격을 그대로 쓰지 않는다.
- `opening_fresh_quality` 로그가 남는다.
- Claude 재호출 여부와 이유가 기록된다.
- 기존 daily JUDGE 호출 제한 정책과 충돌하지 않는다.

## Phase 7 - QA / Replay

필수 검증:

- 오늘 KR 사례 replay:
  - 001510 SK증권 미체결 후 cancel_above.
  - 002780 청산 후 ORDER_UNKNOWN 오염.
  - 047040/001440 Path A/B Safety mismatch.
  - 138360 good candidate no order path.
- 기존 Path B sell truth reconcile 테스트가 깨지지 않아야 한다.
- 기존 Path A entry 테스트가 깨지지 않아야 한다.

권장 명령:

```powershell
pytest tests/test_path_execution_arbiter.py
pytest tests/test_pathb_runtime.py
pytest tests/test_pathb_sell_reconcile.py
pytest tests/test_order_unknown_reconciliation.py
pytest tests/test_market_utils.py
pytest tests
```

주의:

- root `pytest`는 `_sim_test.py` 같은 루트 테스트 파일이 live KIS 네트워크를 건드릴 수 있으므로 기본 검증은 `pytest tests`를 우선한다.

## Phase 8 - 문서 대비 누락 점검

작업:

- 이 문서를 다시 읽고 각 Phase의 구현 여부를 체크한다.
- 누락 항목이 있으면 구현하거나, 제외 사유를 문서에 명시한다.
- 최종 리포트에 다음을 포함한다:
  - 수정 파일
  - 테스트 결과
  - 아직 shadow로 남겨둔 항목
  - 다음 거래일 운영 체크포인트

## 이상 임계값

운영 WARN/CRITICAL 기준:

- `KOSDAQ raw=0` 장중 발생: WARN, 2회 연속 CRITICAL.
- `Path A/B safety mismatch > 0`: CRITICAL.
- 세션 종료 unresolved `ORDER_UNKNOWN > 0`: CRITICAL.
- `top20_coverage < 50%`: WARN.
- `NOT_IN_PROMPT` 중 장중 top20 winner 5개 이상: WARN.
- `broker truth stale > 60초` 장중: WARN.
- `cancel_requested_at` 이후 같은 주문 중복 취소 요청 발생: CRITICAL.

## 최종 판정 기준

Go 조건:

- 크래시성 FAIL 없음.
- SQLite schema mismatch 없음.
- Path A/B safety mismatch 재현 테스트 통과.
- CLOSED -> ORDER_UNKNOWN 강등 방지 테스트 통과.
- ORDER_ACKED cancel_above 중복 취소 방지 테스트 통과.
- 후보 품질 JSONL 샘플 생성 가능.

No-Go 조건:

- broker truth 조회 실패 시 무분별한 retry 발생.
- Path A가 Path B/공통 Safety 차단을 우회.
- `CLOSED` 상태가 startup 후 `ORDER_UNKNOWN`으로 강등.
- KOSDAQ raw=0이 감지 없이 통과.
- 실주문 pending 상태를 대시보드/로그에서 확인할 수 없음.

## 최종 구현/검증 상태 - 2026-04-28 14:26 KST

구현 완료:

- Phase 1: KOSDAQ raw=0이 조용히 지나가지 않도록 runtime WARNING을 추가했다.
- Phase 2: Path A `DAILY_LOSS_LIMIT` 기준을 Path B와 같은 market equity return 기준으로 맞췄다.
- Phase 3: Path B startup recovery가 `FILLED/SELL_SENT/SELL_ACKED/SELL_PARTIAL_FILLED`를 broker ccld 확인 전에 `ORDER_UNKNOWN`으로 강등하지 않도록 수정했다.
- Phase 4: Path B buy `ORDER_SENT/ORDER_ACKED` 상태에서 `cancel_if_open_above` 초과 시 실제 KIS 취소 요청을 보내고, `cancel_requested_at`으로 중복 취소 요청을 막는다.
- Phase 5: `logs/screener_quality/YYYYMMDD_{MARKET}_candidates.jsonl` 후보 품질 로그를 추가했다.
- Phase 6: KR 09:05 fresh screener를 추가했고, 조건 충족 시에만 `manual_rescreen()`으로 Claude JUDGE를 재호출한다.
- Phase 6 보완: 문서에 있던 `existing_trade_ready_weakened>=2` 조건을 구현에 반영했다.

검증 완료:

- `python -m py_compile trading_bot.py bot\screener_quality.py runtime\pathb_runtime.py kis_api.py execution\claude_price_adapter.py runtime\v2_lifecycle_runtime.py`
- `pytest tests` -> 128 passed, 2 warnings
- `pytest test_trading_improvements.py -k "ScreenerPolicyTests"` -> 4 passed
- `python tools\live_preflight.py --mode live --skip-dashboard --allow-config-conflicts` -> ok=True, fail=0, warn=9

live preflight report:

- JSON: `data/v2_reports/live_preflight_20260428_142648.json`
- MD: `data/v2_reports/live_preflight_20260428_142648.md`

남은 WARN/운영 확인:

- `db.order_unknown_unresolved`: 이전 세션 미해결 ORDER_UNKNOWN 8건.
- `db.pathb_stale_active_runs`: 이전 세션 active Path B row 9건.
- `db.pathb_lifecycle_consistency`: Path B lifecycle consistency warning.
- `kr.today_order_unknown_review`: KR ORDER_UNKNOWN 1건.
- `broker_truth.us_stale_state`: US broker truth snapshot stale.
- `kis.us_credentials`: US 전용 키가 없고 common key fallback 사용.
- `kis.balance_probe`: preflight는 실계좌 잔고 API를 직접 호출하지 않으므로 bot startup health check에서 확인 필요.
- `code.wait_timing_recorded`: enum은 있으나 Path A runtime WAIT_TIMING 배선은 정적 증명 불가.
- `market.session_calendar`: 휴장/조기마감은 preflight가 API 호출 없이 운영 확인 필요.

문서 대비 누락 점검:

- Phase 0~6 구현 항목은 코드 또는 테스트로 반영됐다.
- KOSDAQ raw=0은 현재 코드상 `market_div="Q"` 호출 실패/0건을 운영자가 볼 수 있게 만든 상태다. 실제 외부 API 원인은 다음 live WARN 로그로 확정해야 한다.
- 이번 라운드에서 금지한 자동 리프라이스, 자동 추격 매수, Claude EXIT_REVIEW 실매도 반영, 바구니 최소 슬롯 live 적용, 자동 파라미터 변경은 구현하지 않았다.
