# T1 2단계 설계 — 소액 LOO/MOO 실측 (2026-09-28)

목적: 쉐도우(1단계)가 가정한 "발표 후 첫 시가 체결(LOO) → 20세션 뒤 시가 청산(MOO)"이
실제 KIS 계좌에서 **공식 시가와 같은 가격으로 체결되는지** 소액으로 실측한다.
T1의 합격 여부가 체결 방식에 달려 있다(`us_slippage_by_adv_20260928.md`: 개장 후 일반 체결이면 t ≤ 2.38, 동시호가면 t 2.66~2.79).

## 0. 현재 상태

| 항목 | 상태 |
|---|---|
| 1단계 쉐도우 | `tools/us_earnings_t1_shadow.py` 가동. 예약 `claudetrade_t1_shadow_score`(평일 22:15)·`claudetrade_t1_shadow_settle`(매일 07:30) 등록 |
| KIS 주문 구분 | 공식 샘플 확인: 매수 `ORD_DVSN 32`=LOO · `34`=LOC, 매도 `31`=MOO · `32`=LOO · `33`=MOC · `34`=LOC. 모의투자는 00만 |
| 저장소 주문 함수 | `kis_api._submit_order_us_once`가 `ORD_DVSN "00"` 고정 |
| 코드 변경 시도(09-28) | 선택 인자 추가 시도 → 권한 분류기가 "공유 자원 수정"으로 일부 차단 → **원본으로 복구함**(git checkout). 운영자 권한 부여 후 재시도 필요 |

## 1. 필요한 변경 (전부 운영자 권한·승인 후)

### 1-1. `kis_api.py` — US 주문 구분 선택 인자 (기존 동작 불변)
- `_submit_order_us_once(..., ord_dvsn="00")`: 허용 집합 검증(매수 {00,32,34}, 매도 {00,31,32,33,34}), 모의투자는 00만, 31·33은 단가 0.
- `_place_order_us(..., ord_dvsn="00")`: `ord_dvsn != "00"`일 때만 `functools.partial`로 재시도 래퍼에 전달(두 호출 지점 모두).
- `place_order(..., ord_dvsn=None)`: None이면 기존 경로 그대로, KR에 지정하면 거부.
- 테스트: 기본 호출의 body가 기존과 바이트 동일, 32/31 body 필드, 모의투자 거부, KR 거부, 재시도 경로에서 ord_dvsn 유지.

### 1-2. 운영 봇 소유권 격리 (**실주문 전 필수**)
봇은 `source_strategy`가 `risk_manager.ISOLATED_STRATEGY_SOURCES`에 있는 포지션만 일반 청산(손실 한도·Claude 매도 검토)에서 뺀다.
봇 밖에서 산 종목은 브로커 동기화 때 **출처 없는 일반 포지션**으로 잡혀 팔릴 수 있다.
- `ISOLATED_STRATEGY_SOURCES`에 `us_earnings_t1` 추가.
- 브로커 동기화 시 출처 복원: T1 실측 원장의 제출 이력(티커·주문번호)으로 무귀속 US 보유에 `source_strategy=us_earnings_t1`을 붙인다(US swing `_us_swing_attribution_manifest`와 같은 방식, fail-closed).
- 청산 소유권: T1 포지션의 청산은 T1 도구만 한다(MOO @ 진입+20세션). 봇의 exit_ownership 감사가 이 소유자를 인정하도록 등록.
- env 두 소스(`.env.live` + `config/v2_start_config.json`) 일치 + 봇 재시작 + effective-config 실측(CLAUDE.md 규약).

### 1-3. 2단계 실행 도구 `tools/us_earnings_t1_probe.py` (신규)
- 입력: 쉐도우 원장의 당일 `selected_I1` 중 거래대금 ≥ $5M 종목(T1-plus: 유동 종목만으로도 성과 동일).
- 주문: **1주**, LOO 매수, 지정가 = 전일 종가 × 1.15(동시호가에서 거의 확실히 체결되도록 상한만 둔다). 22:15~22:25 KST 제출.
- 청산: 진입 +20세션 날 22:15~22:25에 MOO 매도.
- 게이트(전부 충족해야 주문): `T1_PROBE_ENABLED=true`(기본 false) · 1-2 격리 등록 확인 · 1건 명목 ≤ $150 · 동시 보유 ≤ 10건 · 일 신규 ≤ 2건 · 브로커 상태 정상(integrity) · 코어 3종목·기존 sleeve 티커와 겹치면 건너뜀.
- 기록: 주문번호, 제출 시각, LOO 한도가, **체결가 vs 공식 시가(yfinance/SIP) 차이 bp**, 미체결 여부, 청산 체결가 vs 공식 시가.
- 판정(사전등록): 20건 이상 체결 후 |체결가 − 공식 시가| 중앙값 ≤ 5bp, 미체결 ≤ 10% 이면 "동시호가 가정 성립" → 3단계(실투자 규모 논의) 안건.

## 2. 비용·위험

- 금액: 1주 × 최대 10건 동시 ≈ $1,500 이하(약 200만원 이하). 최악(전부 −30%) ≈ −60만원.
- 수수료: 건당 최소 수수료가 있으면 1주 소액에서 비율이 커진다 — **체결 품질 측정용이지 수익 측정용이 아니다.** 수익 판정은 쉐도우 원장으로 한다.
- 운영 봇 간섭: 1-2가 완료되기 전에는 게이트가 주문을 거부한다(fail-closed).

## 3. 순서

1. 운영자: 코드 변경 권한 부여(주문 함수 `kis_api.py`, 봇 격리 `risk_manager.py`·`trading_bot.py` 동기화 부분).
2. 1-1 구현 + 테스트 → 전체 QA(기준선 2 failed).
3. 1-2 구현 + 테스트 → env 두 소스 + 봇 재시작 + effective-config 실측.
4. 1-3 도구 작성, `T1_PROBE_ENABLED=false`로 배포 → 드라이런 1주.
5. 운영자 "켜" 지시 → `T1_PROBE_ENABLED=true`.
