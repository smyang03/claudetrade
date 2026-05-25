# Screener Prompt Shadow Follow-Up Plan

작성일: 2026-05-25

## 현재 결론

2026-05-25 로컬 `candidate_audit.db`/로그 기반 재점검 결과, 스크리너 강화 방향은 유지하되 지금 당장 live 동작을 더 공격적으로 바꾸지 않는다.

운영자가 직접 변경한 고정 주문금액 값은 이번 이슈에서 제외한다.

## 현재 유지할 것

- KR/US prompt hard cap은 현재 값 유지
  - KR: 32
  - US: 35
- PLAN_A 후보는 자동 매수나 `trade_ready` 승격 신호로 쓰지 않고, Claude prompt 안에서 잘 보이게 하는 visibility 신호로만 유지한다.
- prompt overlay는 live 전환하지 않고 shadow 관찰을 계속한다.
- US KIS ranking과 projected dollar volume 계열은 primary/live 판단에 반영하지 않고 shadow 데이터 축적 대상으로 둔다.

## 지금 하지 않을 것

- prompt cap 확대
- overlay live 전환
- KIS ranking primary 전환
- projected dollar volume을 live 통과 조건으로 반영
- PLAN_A를 자동 주문, 자동 `trade_ready`, hard override로 승격
- 짧은 기간의 좋은 수치만 근거로 전략 철학 변경

## 나중에 할 작업

### 1. Overlay live 전환 재평가

다음 조건을 모두 만족할 때만 overlay live 전환을 다시 검토한다.

- shadow 관찰 10거래일 이상
- overlay 실제 발생일 4일 이상
- top-day contribution 40% 미만
- 30분 forward 기준 PF > 1
- 60분 forward 기준 PF > 1
- overlay로 추가된 후보 성과가 overlay 때문에 빠진 후보 성과보다 우위
- PLAN_B fallback 사용이 0이거나 명시적으로 허용된 별도 subset으로 분리됨

2026-05-25 재점검 기준으로는 overlay 발생일이 3일뿐이고, top-day contribution이 90.54%라 gate를 통과하지 못했다.

### 2. PLAN_A visibility 검증

PLAN_A는 성과 신호가 있으므로 prompt 노출 우선순위에는 계속 활용한다. 다만 다음을 추가로 확인하기 전까지 execution 권한으로 승격하지 않는다.

- PLAN_A 후보가 Claude selection에서 실제로 더 높은 품질의 `WATCH`, `PULLBACK_WAIT`, `PROBE_READY`로 이어지는지
- PLAN_A 후보의 30분/60분 성과가 market별로 안정적인지
- 특정 날짜, 특정 시장, 특정 종목군 의존이 과도하지 않은지
- PLAN_A 후보를 넣으면서 빠지는 기존 후보의 성과가 더 좋지 않은지

### 3. Prompt pool 분석 기준 정리

분석 리포트에서 `screener_quality.input_to_claude`와 실제 최종 prompt 구성을 혼동하지 않도록 기준을 정리한다.

- 수익성 판단 기준은 `candidate_audit`의 실제 prompt ticker 기록을 우선한다.
- `input_to_claude`는 후보 생성/품질 진단 보조 지표로 사용한다.
- 리포트에는 current prompt, trainer prompt, overlay 추가 후보, overlay 제외 후보를 분리해 표기한다.

### 4. US KIS ranking / projected dollar volume shadow 축적

현재 로컬 로그에는 `US_projected_dollar_volume_shadow`, `US_kis_ranking_shadow` 실측 outcome이 충분하지 않다. live 반영 전 다음을 먼저 쌓는다.

- shadow signal 발생 횟수
- 발생일 수
- 30분/60분 forward outcome
- Claude selection 전환율
- 기존 후보 대비 추가 후보 성과
- market/session별 편차

충분한 outcome 없이 primary 전환하지 않는다.

### 5. Degraded fallback 후보 감사

`DEGRADED_PRESERVED` 후보가 prompt/PathB까지 갈 수 있는 구조는 확인됐다. 당장 차단 수정은 하지 않지만, 다음 중 하나를 나중에 검토한다.

- degraded preserved 후보는 fresh evidence 없이는 `PROBE_READY`, `BUY_READY`, `PULLBACK_WAIT` 승격을 막는다.
- 또는 승격은 허용하되 별도 audit tag와 경고 로그를 남긴다.
- degraded 후보가 실제 체결까지 간 경우 별도 리포트에서 성과와 원인을 추적한다.

### 6. 운영 상태 정합성 확인

코드 변경 항목은 아니지만, 다음은 브로커 truth 기준으로 별도 확인한다.

- 과거 US PathB `ORDER_UNKNOWN`: APLD, IBM
- stale active PathB rows: SMCI, CRDO, IONQ, APLD, IBM
- 브로커 보유, 미체결 주문, 체결 내역과 로컬 DB 상태 일치 여부

브로커 truth가 불확실하면 신규 진입 확대보다 기존 상태 정리와 보호를 우선한다.

## 재검토 입력 자료

- `docs/reports/candidate_quality_ranker_sim_20260525_review_current.md`
- `docs/reports/prompt_overlay_shadow_20260525_review_current.md`
- `docs/reports/prompt_overlay_shadow_20260525_review_current_h30.md`
- `docs/reports/20260525_multi_screener_before_after.md`

## 다음 의사결정 원칙

좋은 평균 수익률 하나만으로 live 전환하지 않는다. live 전환은 표본 수, 날짜 분산, 30분/60분 동시 성과, 추가 후보와 제외 후보의 상대 우위, 시장별 안정성을 함께 통과할 때만 검토한다.
