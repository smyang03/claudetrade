# Claude 입력 품질 점검 요청

## 목적

우리 자동매매 시스템에서 Claude 판단에 들어가는 입력 데이터가 실제 운영 판단에 적합한지 점검한다.

대상은 아래 3개 축이다.

1. 장판단 / market mode 판단
2. 교훈 후보군 / lesson candidate 선정
3. hold advisor / 보유 종목 HOLD/SELL 판단

이번 작업은 우선 읽기 전용 진단이다. 코드 수정은 하지 말고, 필요한 개선안을 근거와 함께 정리한다.

## 점검 범위

다음 흐름을 각 대상별로 추적한다.

- 데이터 producer
- 저장소 / DB / state 파일
- runtime consumer
- Claude prompt/payload builder
- Claude raw response 저장 위치
- normalized/applied 결과
- audit/log/dashboard/report 노출 여부

특히 아래 항목을 확인한다.

- 입력 데이터가 최신 broker truth, 가격, 포지션, 주문 상태와 일치하는가
- stale/missing/default 값이 Claude에게 오해를 줄 수 있는가
- KR/US 시장별 데이터가 섞이지 않는가
- strategy 성과/selection 품질/execution/risk 문제가 섞이지 않는가
- `state/brain.json`을 runtime truth처럼 쓰는 경로가 없는가
- `state/lesson_candidates.json` 흐름이 승인형 워크플로우 전제와 맞는가
- hold advisor가 PathB 수익 경로를 조기 SELL 쪽으로 과도하게 기울게 하지 않는가
- prompt에 필요한 데이터는 빠지지 않고, 불필요한 noise/token 낭비는 없는가
- 로그/audit/dashboard에서 입력과 결과를 운영자가 재현 가능하게 볼 수 있는가

## 보호 영역

아래는 직접 원인이 확인되기 전에는 변경하지 않는다.

- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard
- PathB broker-truth entry fail-closed
- PathB sizing reason split
- zero-holding stale reconcile
- KIS order normalization
- Path A / Path B routing contract
- `_sync_runtime_with_broker()` broker truth 우선순위
- `state/brain.json` 자동 정책 메모리 승격

보호 영역에 문제가 의심되면 바로 수정하지 말고, 증거와 영향 범위를 먼저 보고한다.

## 원하는 산출물

최종 보고서는 아래 형식으로 작성한다.

### 1. 데이터 흐름 요약

대상별로 다음을 정리한다.

- 관련 파일 / 함수
- 입력 필드
- 데이터 출처
- 저장 위치
- Claude로 전달되는 최종 payload/prompt 구조
- 결과가 다시 반영되는 위치

### 2. 품질 평가

각 대상별로 아래 등급으로 평가한다.

- 양호
- 비차단 개선 필요
- 운영 리스크 있음
- 즉시 수정 필요

평가 근거는 파일/함수/로그/테스트 기준으로 적는다.

### 3. 발견 사항

각 발견 사항은 아래 형식으로 작성한다.

- 심각도:
- 대상:
- 위치:
- 현재 동작:
- 왜 문제인지:
- 운영 영향:
- 권장 수정:
- 필요한 테스트:

### 4. 처리 결과 분류

이번 점검에서 나온 항목을 세 그룹으로 나눈다.

- 반영 완료: 실제 수정까지 끝낸 항목이 있을 경우
- 비차단 잔여 리스크: 지금 운영은 가능하지만 공개해야 하는 위험
- 범위 밖 후속 개선: 별도 승인이나 운영 데이터가 필요한 항목

이번 요청에서는 기본적으로 코드 수정하지 않는다. 단, 명백한 오타/문서 오류처럼 안전한 수정이 필요하면 수정 전 계획을 먼저 보고한다.

## 검증 방식

코드 수정이 없다면 다음을 수행한다.

- 관련 파일 정적 분석
- 관련 테스트 존재 여부 확인
- 주요 로그/audit 저장 경로 확인
- 필요 시 read-only DB/schema 조회

코드 수정이 생기는 경우에는 수정 전 다음을 먼저 명시한다.

- 이번 이슈의 직접 수정 범위
- 건드리지 않을 보호 영역
- 수정 예정 파일
- 실행할 검증 명령

## 주의사항

- 실제 주문 수량/금액 계산 로직은 변경하지 않는다.
- hard stop, broker truth, quarantine, PathB live gate는 완화하지 않는다.
- selection 품질 문제와 execution/risk 문제를 같은 수정으로 섞지 않는다.
- `state/brain.json` 직접 수정 또는 자동 승격 경로를 만들지 않는다.
- live/paper 설정값, `.env*`, `config/v2_start_config.json`은 변경하지 않는다.
