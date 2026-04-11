# KIS 체결통보 WS 연동 계획

## 목적

한투 모의투자(VTS)에서 `inquire-daily-ccld` REST 조회가 지연되거나 `0건/500`으로 불안정한 경우가 있어,
실제 체결이 발생해도 봇이 `pending -> filled` 전환을 놓치는 문제가 있다.

이를 보완하기 위해 **REST 체결조회 보조 수단**으로 **KIS 실시간 체결통보 WebSocket**을 붙인다.

## 공식 샘플 기준 확인사항

공식 샘플 저장소:
- `E:\code\open-trading-api\examples_user\domestic_stock\domestic_stock_functions_ws.py`
- `E:\code\open-trading-api\examples_user\domestic_stock\domestic_stock_examples_ws.py`

핵심 내용:
- 국내주식 체결통보 TR:
  - 실전: `H0STCNI0`
  - 모의: `H0STCNI9`
- 공식 샘플 설명:
  - 주문/정정/취소/거부 접수 통보와 체결 통보가 모두 수신됨
  - `CNTG_YN`
    - `1`: 주문/정정/취소/거부 접수 통보
    - `2`: 체결 통보
- 모의투자도 전용 TR(`H0STCNI9`)이 존재하므로, **WS 체결통보 자체는 지원하는 구조**로 본다.

## 현재 코드 상태

현재 구현:
- `E:\code\claudetrade\kis_api.py`
  - `KISWebSocket`는 현재 국내 시세 체결가 `H0STCNT0`만 구독
  - 계좌 체결통보 구독은 없음
- `E:\code\claudetrade\trading_bot.py`
  - 체결 인식은
    - 잔고 증가
    - REST 주문/체결 조회
  - 에 의존

즉 현재는 **WS 실시간 체결 이벤트를 전혀 사용하지 않는다.**

## 붙일 위치

### 1. `kis_api.py`

`KISWebSocket` 확장:
- 기존 시세 체결가 구독 유지
- 계좌 체결통보 구독 추가

필요 항목:
- 새 인자:
  - `notice_key` (보통 HTS ID)
  - `on_notice`
- 새 구독 함수:
  - 실전 `H0STCNI0`
  - 모의 `H0STCNI9`
- 수신 파싱:
  - `CNTG_YN == "2"` 인 경우만 체결 이벤트로 처리

공식 샘플 컬럼:
- `CUST_ID`
- `ACNT_NO`
- `ODER_NO`
- `OODER_NO`
- `SELN_BYOV_CLS`
- `RCTF_CLS`
- `ODER_KIND`
- `ODER_COND`
- `STCK_SHRN_ISCD`
- `CNTG_QTY`
- `CNTG_UNPR`
- `STCK_CNTG_HOUR`
- `RFUS_YN`
- `CNTG_YN`
- `ACPT_YN`
- `BRNC_NO`
- `ODER_QTY`
- `ACNT_NAME`
- `ORD_COND_PRC`
- `ORD_EXG_GB`
- `POPUP_YN`
- `FILLER`
- `CRDT_CLS`
- `CRDT_LOAN_DATE`
- `CNTG_ISNM40`
- `ODER_PRC`

봇에서 실제로 필요한 최소 필드:
- `order_no`
- `ticker`
- `filled_qty`
- `filled_price`
- `filled_time`
- `side`
- `is_fill` (`CNTG_YN == "2"`)

### 2. `trading_bot.py`

세션 오픈 시 WS notice callback 연결:
- KR 세션 시작 시
  - 시세 tick callback
  - 체결 notice callback
  둘 다 연결

체결 notice 수신 시 할 일:
1. `pending_orders[market]`에서 `order_no` 매칭
2. `pending -> filled` 즉시 전환
3. `positions` 반영
4. `decisions.db` `filled=1`, `entry_price`, `order_no` 업데이트
5. 텔레그램 `[매수 체결 확인]` 발송
6. 중복 수신 방지를 위해 `seen_fill_keys` 캐시 처리

## 권장 동작 우선순위

체결 인식 우선순위:
1. **WS 체결통보**
2. REST 주문/체결 조회
3. 잔고 증가 감지

즉 WS를 1차 소스로 두고,
REST/잔고는 보조 복구용으로 유지한다.

## 구현 시 주의점

### 1. `tr_key`는 종목코드가 아니라 HTS ID 계열

공식 샘플:
- `ccnl_notice("1", trenv.my_htsid, env_dv="demo")`

즉 계좌 체결통보는 일반 종목코드가 아니라 **HTS ID 기반**이다.
현재 프로젝트에서 이 값이 `.env` 또는 런타임 설정에 있는지 먼저 확인 필요.

후보 키:
- `KIS_HTS_ID`
- 사용자 ID/고객 ID 계열

없다면:
- 새 env 추가 필요

### 2. 복호화 필요 가능성

공식 샘플 설명상 체결통보는 AES256 KEY/IV 기반 복호화 경로가 있다.
현재 `claudetrade`의 단순 시세 WS 파서처럼 `split("^")`만으로 끝나지 않을 수 있다.

따라서 구현 전:
- 공식 샘플의 복호화 처리 부분을 같이 가져와야 함
- 특히 모의 `H0STCNI9`의 실제 payload 형식을 먼저 확인해야 함

### 3. 중복 체결 통보

동일 주문에 대해:
- 접수 통보
- 체결 통보
- 부분 체결 통보
가 올 수 있다.

따라서 키는 최소:
- `order_no`
- `filled_qty`
- `filled_time`
조합으로 dedupe 필요.

## 추천 구현 순서

1. `.env`/설정에 HTS ID 존재 여부 확인
2. 공식 샘플 복호화 로직 확인
3. `kis_api.KISWebSocket`에 `notice` 구독 추가
4. KR만 먼저 적용
5. 실전 검증:
   - 주문 접수
   - 체결
   - `pending -> filled` 전환
   - 텔레그램 체결 확인
6. 안정화 후 US 체결통보 여부 별도 검토

## 이번 단계의 결론

현재 VTS REST 체결조회는 신뢰도가 낮다.
따라서 **국내주식 체결 인식의 정답 경로는 WS 체결통보 연동**이다.

다음 구현 작업은:
- `KISWebSocket`에 국내주식 체결통보(`H0STCNI9`) 추가
- `trading_bot` pending order 동기화에 notice 이벤트 연결
이다.
