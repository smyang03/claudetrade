# KR/US 매수금액 분리 후속 플랜

작성일: 2026-05-21

## 현재 상태

대시보드에는 손실클러스터 옆에 현재 매수범위와 다음 1회 매수 설정 입력이 추가되어 있다.

- 매수범위는 현재 실행 중인 봇의 `risk.max_order_krw`와 시장 모드 비율(`mode_size_pct`)로 표시한다.
- 다음설정 입력은 `config/v2_start_config.json`의 다음 시작 설정을 저장한다.
- 저장 후 즉시 실행 중인 주문 상한이 바뀌지 않고, 봇 재시작 후 적용된다.

현재 구현은 실용적으로 `US_FIXED_ORDER_KRW`, `PATHB_FIXED_ORDER_KRW`와 함께 공용 `MAX_ORDER_KRW`도 갱신한다. 이유는 현재 실행 상한 계산 경로가 아직 `MAX_ORDER_KRW`를 공용 cap으로 보기 때문이다.

## 현재 유지 판단

지금은 추가 수정하지 않는다.

현재 변경은 미국장 매수금액을 올려야 하는 운영 요구에는 동작한다. 다만 구조적으로는 KR/US가 완전히 분리된 상태가 아니므로, 나중에 별도 작업으로 분리한다.

## 남아 있는 구조 이슈

현재 구조의 핵심 문제는 다음과 같다.

- `KR_FIXED_ORDER_KRW`, `US_FIXED_ORDER_KRW`, `PATHB_FIXED_ORDER_KRW`는 시장별 설정이다.
- `MAX_ORDER_KRW`는 현재 `trading_bot.py`의 실제 주문 상한 계산에서 공용으로 사용된다.
- 따라서 대시보드에서 US 설정을 올릴 때 `MAX_ORDER_KRW`까지 올리면 KR 상한에도 영향을 줄 수 있다.

이 상태는 즉시 버그라기보다 설정축이 덜 분리된 구조 문제다.

## 후속 목표

KR과 US의 1회 주문 상한을 완전히 분리한다.

- KR은 `KR_FIXED_ORDER_KRW` 또는 `KR_MAX_ORDER_KRW` 기준으로 계산한다.
- US는 `US_FIXED_ORDER_KRW` 또는 `US_MAX_ORDER_KRW` 기준으로 계산한다.
- PathB는 기존 `PATHB_FIXED_ORDER_KRW` 경로를 유지하거나, 필요 시 시장별 PathB 설정으로 분리한다.
- `MAX_ORDER_KRW`는 하위 호환 fallback으로만 사용한다.

## 개발 범위

1. `trading_bot.py`
   - 시작 시 `MAX_ORDER_KRW` 단일 cap 대신 시장별 cap을 해석한다.
   - 장중 `_sync_runtime_with_broker()` 또는 max order 갱신 경로에서도 시장별 cap을 사용한다.
   - fallback 순서는 시장별 키 -> `MAX_ORDER_KRW` -> 기존 기본값으로 둔다.

2. `dashboard/dashboard_server.py`
   - 주문금액 저장 endpoint가 선택 시장의 설정만 수정하도록 바꾼다.
   - US 저장 시 KR에 영향이 가지 않도록 `MAX_ORDER_KRW` 갱신 여부를 제거하거나 명시적 옵션으로 분리한다.
   - 화면 문구에서 현재실행 상한과 다음설정 상한을 계속 구분한다.

3. 설정 파일
   - `.env.live`, `.env.paper`, `config/v2_start_config.json`에 시장별 cap 키를 명확히 둔다.
   - 예: `KR_MAX_ORDER_KRW`, `US_MAX_ORDER_KRW`
   - 기존 `MAX_ORDER_KRW`는 fallback 또는 legacy 설명으로 남긴다.

4. 테스트
   - US 설정 변경이 KR cap을 바꾸지 않는지 검증한다.
   - KR 설정 변경이 US cap을 바꾸지 않는지 검증한다.
   - `MAX_ORDER_KRW`만 있는 구버전 설정에서도 기존 동작이 깨지지 않는지 검증한다.
   - 대시보드 요약 API가 현재실행/다음설정 값을 분리해서 반환하는지 검증한다.

## QA 기준

- `python -m py_compile trading_bot.py dashboard/dashboard_server.py`
- 관련 대시보드 테스트
- 관련 live config/source 테스트
- live preflight JSON 확인
- 대시보드에서 KR/US를 전환했을 때 매수범위와 다음설정이 서로 독립적으로 표시되는지 확인

## 완료 기준

- KR/US 각각 다른 주문 상한을 설정할 수 있다.
- US 상한 변경이 KR의 실행 상한에 영향을 주지 않는다.
- KR 상한 변경이 US의 실행 상한에 영향을 주지 않는다.
- 재시작 전/후 대시보드 표시가 현재실행과 다음설정을 명확히 구분한다.
- 기존 `MAX_ORDER_KRW`만 쓰던 설정도 fallback으로 동작한다.
