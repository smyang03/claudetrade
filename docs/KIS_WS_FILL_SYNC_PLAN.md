# KIS 체결통보 WS 연동 상태 - 2026-05-01

## 목적

REST 주문/체결 조회가 지연되거나 불안정할 때도 실제 체결을 빠르게 인식하기 위해 KIS 실시간 체결통보 WebSocket을 보조 진실 소스로 사용한다.

## 현재 상태

상태: 구현 완료, 로컬 단위 테스트 완료, 실계좌/모의계좌 수신 검증은 남음.

기존 문서에는 `KISWebSocket`이 국내 시세 체결가 `H0STCNT0`만 구독하고 계좌 체결통보는 없다고 되어 있었지만, 현재 코드는 이미 체결통보 경로를 포함한다.

## 구현 완료 항목

### `kis_api.py`

- `KISWebSocket(..., on_notice=...)` 콜백 지원.
- `KIS_HTS_ID` 기반 계좌 체결통보 구독.
- 국내 체결통보 TR:
  - 모의: `H0STCNI9`
  - 실전: `H0STCNI0`
- 해외 체결통보 TR:
  - 모의: `H0GSCNI9`
  - 실전: `H0GSCNI0`
- 체결통보 AES key/iv 수신 및 복호화 경로.
- KR/US 체결통보 payload parser.
- `CNTG_YN == "2"`인 체결 이벤트만 처리.
- `(order_no, filled_qty, filled_time)` 기준 dedupe.

### `trading_bot.py`

- `KISWebSocket` 시작 시 `on_notice=self._on_fill_notice` 연결.
- `_on_fill_notice()`에서 buy 체결통보를 pending order와 `order_no`로 매칭.
- full fill:
  - pending order 제거
  - position 생성
  - lifecycle `FILLED` 기록
  - Telegram fill alert 발송
- partial fill:
  - pending order 잔량 유지
  - `filled_qty_accum`, `partial_fill_at`, `filled_price_native`, `fill_time` 기록
  - lifecycle `PARTIAL_FILLED` 기록
- PathB 주문:
  - `pathb.on_buy_fill(..., partial=...)` 호출
- sell notice, order number 없음, 체결 수량 0 이하는 무시.

## 로컬 테스트

추가 테스트 파일:

- `tests/test_kis_ws_fill_notice.py`

검증 항목:

- KR 체결통보 payload 파싱.
- US 체결통보 payload 파싱 및 `CNTG_UNPR12` 소수 가격 사용.
- `CNTG_YN != "2"` 무시.
- malformed payload 무시.
- 중복 체결통보 dedupe.
- full buy fill 시 pending order 제거 및 position 반영.
- partial PathB buy fill 시 pending 잔량 유지 및 `pathb.on_buy_fill()` 호출.
- sell notice 또는 invalid notice 무시.

검증 결과:

```powershell
python -m py_compile tests\test_kis_ws_fill_notice.py
python -m pytest tests\test_kis_ws_fill_notice.py -q
```

결과:

- `tests/test_kis_ws_fill_notice.py`: 6 passed.
- 경고: 기존 `eventlet`/`distutils` deprecation warning 2건.

## 로컬 환경 확인

2026-05-01 로컬 확인 결과:

```powershell
python -c "from Crypto.Cipher import AES; print('pycryptodome OK')"
python -m py_compile kis_api.py trading_bot.py tests\test_kis_ws_fill_notice.py
python -m pytest tests\test_kis_ws_fill_notice.py tests\test_live_order_reconciliation.py -q
python -m pytest tests\test_order_unknown_reconciliation.py tests\test_pathb_sell_reconcile.py -q
```

결과:

- `pycryptodome`: 설치 확인 완료.
- `KIS_HTS_ID`: 설정 존재 확인 완료. 값은 출력하지 않았고 길이만 확인함.
- 현재 로컬 설정: `IS_PAPER=False`.
- notice 구독 payload:
  - KR: `H0STCNI0`, `tr_key` 존재.
  - US: `H0GSCNI0`, `tr_key` 존재.
- WS fill notice + pending reconciliation 관련 테스트:
  - `tests/test_kis_ws_fill_notice.py tests/test_live_order_reconciliation.py`: 8 passed.
  - `tests/test_order_unknown_reconciliation.py tests/test_pathb_sell_reconcile.py`: 15 passed.

## 남은 운영 검증

아래 항목은 로컬 단위 테스트로 대체할 수 없는 운영 확인 사항이다.

1. `KIS_HTS_ID` 설정 확인
   - 값이 없으면 체결통보 구독은 스킵된다.

2. `pycryptodome` 설치 확인
   - AES 복호화 경로에서 필요하다.

3. KIS 모의계좌 수신 검증
   - 주문 접수 이벤트와 체결 이벤트가 실제로 어떤 payload로 오는지 확인.
   - `CNTG_YN == "2"` 이벤트가 정상 파싱되는지 확인.

4. KIS 실계좌 수신 검증
   - 실전 TR `H0STCNI0`, `H0GSCNI0` 구독과 key/iv 수신 확인.
   - 실전에서는 소액 주문으로 full/partial fill 경로를 확인.

5. REST fallback과의 중복 동작 확인
   - WS가 먼저 pending을 filled로 전환한 뒤 REST reconcile이 같은 체결을 중복 반영하지 않는지 확인.

## 권장 우선순위

1. 로컬 단위 테스트는 완료.
2. 다음 운영 전 체크는 `KIS_HTS_ID`와 `pycryptodome` 확인.
3. 그 다음 모의계좌에서 실제 notice payload를 수집.
4. 실계좌 적용은 소액 full fill 확인 후 partial fill 케이스를 별도 확인.

## 결론

KIS WS 체결통보는 코드상 구현되어 있고, parser와 pending-order 반영 경로는 로컬 테스트로 검증했다. 남은 작업은 환경 설정 확인과 실제 KIS WebSocket 수신 검증이다.
