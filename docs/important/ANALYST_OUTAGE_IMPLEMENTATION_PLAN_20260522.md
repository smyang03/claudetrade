# Analyst API Outage Handling Implementation Plan

작성일: 2026-05-22  
상태: 개발 계획  
기준 문서: [ANALYST_OUTAGE_HANDLING_REQUIREMENTS_20260522.md](ANALYST_OUTAGE_HANDLING_REQUIREMENTS_20260522.md)

## 1. 목적

Anthropic API `529 overloaded_error` 등 provider 일시 장애가 발생했을 때 실패한 분석가를 `NEUTRAL` 의견처럼 취급하지 않도록 수정한다.

핵심 목표:

- 실패 analyst를 `analyst_unavailable`로 명시한다.
- R2 토론에서 unavailable analyst를 제외한다.
- consensus는 available analyst만으로 계산한다.
- quorum 부족 시 신규 진입을 차단한다.
- dashboard에는 raw error 대신 운영용 장애 상태를 표시한다.
- postmortem/brain 학습에서 unavailable analyst를 제외한다.

## 2. 개발 원칙

- API 장애는 시장 `HALT`가 아니다.
- provider outage, selection 품질, execution/risk 문제를 섞지 않는다.
- 1명 실패는 partial consensus로 판단을 이어간다.
- 2명 이상 실패는 신규 진입을 차단한다.
- 기존 포지션 보호, broker truth sync, 청산 로직은 계속 동작해야 한다.
- retry 자동 적용은 이번 작업 범위에서 제외한다.
- PathB 운영 파라미터와 live gate 설정은 변경하지 않는다.

## 3. 작업 범위

### 포함

- `minority_report/analysts.py`
- `minority_report/consensus.py`
- `trading_bot.py`
- `dashboard/dashboard_server.py`
- `minority_report/postmortem.py`
- `claude_memory/brain.py`
- 관련 테스트 추가

### 제외

- 5분 자동 retry scheduler 구현
- live 운영 파라미터 변경
- `state/brain.json` 직접 수정
- PathB live enable gate 변경
- 주문 sizing 정책 변경

## 4. 단계별 플랜

### Phase 1. Analyst fallback contract

대상:

- `minority_report/analysts.py`

작업:

- `_fallback_result()` 반환값에 outage marker 추가
- provider error 분류 helper 추가
- raw error와 dashboard display reason 분리
- fallback confidence를 `0.0`으로 조정

필수 필드:

```python
{
    "stance": "NEUTRAL",
    "confidence": 0.0,
    "analyst_unavailable": True,
    "availability_status": "provider_unavailable",
    "error_type": "...",
    "retryable": True,
    "display_reason": "...",
    "key_reason": "분석가 일시 불가",
    "learning_excluded": True,
}
```

완료 기준:

- 실패 analyst가 더 이상 raw error를 `key_reason`에 직접 담지 않는다.
- `analyst_unavailable=True`가 모든 fallback payload에 포함된다.

### Phase 2. R2 debate contamination guard

대상:

- `minority_report/analysts.py`
- `claude_memory/brain.py`

작업:

- R2 `others`에서 unavailable analyst 제외
- 자기 R1이 unavailable이면 R2 호출 skip
- `r2_skipped=True`, `skip_reason` 기록
- debate history 저장 시 unavailable analyst를 stance change 통계에서 제외

완료 기준:

- bear R1 실패 시 bull/neutral R2 prompt에 bear fallback 의견이 들어가지 않는다.
- unavailable analyst가 debate change로 기록되지 않는다.

### Phase 3. Consensus quorum handling

대상:

- `minority_report/consensus.py`

작업:

- available analyst filtering 추가
- active analyst만 score/confidence/suggested size 계산에 포함
- 2명 available: partial consensus 처리
- 1명 이하 available: quorum failed 처리
- `apply_unanimous_override()`가 unavailable analyst를 포함하지 않도록 보정
- `_analyst_new_buy_constraints()`는 available analyst만 반영

quorum 기준:

| available 수 | 처리 |
|---:|---|
| 3 | 정상 consensus |
| 2 | partial consensus, coverage penalty, 신규 진입 가능 |
| 1 | quorum failed, 신규 진입 차단 |
| 0 | all unavailable, 신규 진입 차단 |

완료 기준:

- 3명 모두 실패해도 `HALT`가 아니라 `NEUTRAL + size=0 + new_buy_permission=block`이 된다.
- consensus payload에 `active_analyst_count`, `unavailable_analysts`, `quorum_failed`가 남는다.

### Phase 4. Runtime entry gate

대상:

- `trading_bot.py`

작업:

- 신규 진입 gate에서 `quorum_failed` 우선 확인
- runtime 평균 confidence 계산에서 unavailable analyst 제외
- 차단 사유를 `analyst_quorum_failed` 또는 `all_analysts_unavailable`로 분리
- ML/eval 기록 시 selection 품질 문제가 아니라 provider outage block으로 기록

완료 기준:

- quorum failed 상태에서 신규 매수 후보는 주문으로 이어지지 않는다.
- 기존 포지션 보호와 청산 로직은 계속 실행된다.

### Phase 5. Dashboard display

대상:

- `dashboard/dashboard_server.py`

작업:

- `/api/judgments` 응답에서 raw provider error 미노출
- analyst card에서 unavailable 상태를 별도 표시
- timeline row도 `display_reason`을 사용
- consensus 영역에 partial/quorum 상태 표시

표시 예:

```text
하락 분석가 일시 불가
원인: Anthropic API 과부하
상태: 나머지 분석가 기준으로 임시 판단 중
```

완료 기준:

- dashboard HTML/API 응답에 `Error code: 529 - { ... }` 형태가 노출되지 않는다.
- 운영자가 3명 중 몇 명이 정상인지 볼 수 있다.

### Phase 6. Postmortem and learning exclusion

대상:

- `minority_report/postmortem.py`
- `claude_memory/brain.py`
- `tools/rebuild_brain.py`

작업:

- unavailable analyst는 `_code_judge_hit_miss()` 대상에서 제외
- unavailable analyst는 `BrainDB.update_analyst()` 호출 제외
- daily record에는 unavailable 상태를 남김
- rebuild 도구도 unavailable 표본 제외

완료 기준:

- API 장애가 analyst performance hit/miss에 반영되지 않는다.
- future analyst weighting이 provider outage 때문에 왜곡되지 않는다.

### Phase 7. Tests

추가 권장 테스트:

- `tests/test_analyst_outage_handling.py`
- `tests/test_consensus_quorum.py`
- `tests/test_dashboard_analyst_outage.py`
- `tests/test_postmortem_analyst_outage.py`

필수 테스트 케이스:

- 1명 실패: partial consensus
- 2명 실패: quorum failed, 신규 진입 차단
- 3명 실패: all unavailable, 신규 진입 차단
- R2 others filtering
- self unavailable R2 skip
- dashboard raw error 미노출
- postmortem learning exclusion

## 5. 검증 명령

우선 실행:

```powershell
python -m pytest tests/test_analyst_outage_handling.py tests/test_consensus_quorum.py -q
python -m pytest tests/test_dashboard_analyst_outage.py tests/test_postmortem_analyst_outage.py -q
python -m py_compile trading_bot.py dashboard/dashboard_server.py claude_memory/brain.py
```

관련 회귀:

```powershell
python -m pytest tests/test_preopen_opening_role_separation.py -q
python -m pytest tests/test_trading_decision_contract_improvements.py -q
python -m pytest tests/test_action_routing.py -q
```

넓은 회귀:

```powershell
python -m pytest -q
```

## 6. 개선 전후 확인표

| 항목 | 개선 전 | 개선 후 |
|---|---|---|
| R1 529 | `NEUTRAL` fallback | `analyst_unavailable=True` |
| R2 토론 | 실패 analyst가 others에 포함 | 실패 analyst 제외 |
| consensus | 3명 모두 정상 가정 | available analyst만 계산 |
| 2명 실패 | 가짜 neutral 포함 판단 가능 | 신규 진입 차단 |
| 3명 실패 | neutral consensus 가능 | all unavailable, size 0 |
| dashboard | raw error 노출 | 운영용 장애 문구 표시 |
| postmortem | 실패 analyst도 HIT/MISS 평가 | 학습 제외 |
| retry | 없음 또는 수동 reinvoke | 이번 범위 제외, 2차 작업 |

## 7. 주요 리스크와 완화

### 리스크 1. 너무 보수적으로 바뀌어 매수 기회 감소

완화:

- 1명 실패는 신규 진입을 완전히 막지 않는다.
- 2명 이상 실패만 신규 진입 차단한다.
- coverage penalty만 적용해 판단 강도를 낮춘다.

### 리스크 2. provider outage를 시장 HALT로 오해

완화:

- quorum failed에서는 `mode=HALT`를 쓰지 않는다.
- `block_reason`과 `judgment_status`로 provider 장애를 표현한다.

### 리스크 3. 기존 코드가 세 analyst 정상 존재를 가정

완화:

- `stance`는 호환을 위해 `NEUTRAL` 유지 가능.
- 의미 판단은 `analyst_unavailable` 플래그로 한다.
- unknown stance 도입은 피한다.

### 리스크 4. 학습 데이터 오염

완화:

- `learning_excluded=True`를 성과 업데이트 전에 확인한다.
- `tools/rebuild_brain.py`에도 같은 제외 규칙을 적용한다.

### 리스크 5. dashboard에서 neutral처럼 보임

완화:

- unavailable badge를 stance보다 우선 표시한다.
- `display_reason`을 analyst card와 timeline에서 사용한다.

### 리스크 6. retry 자동 적용 충돌

완화:

- retry는 이번 작업에서 제외한다.
- 추후 구현 시 주문 없음, 포지션 변화 없음, 미체결 없음 조건에서만 자동 적용한다.

## 8. 롤백 기준

다음 문제가 발생하면 코드 롤백 또는 feature flag 비활성화를 고려한다.

- 정상 3명 판단에서도 consensus 생성 실패
- dashboard `/api/judgments` 응답 오류
- 신규 진입 gate가 정상 consensus에서도 전부 차단
- postmortem 저장 실패
- shared judgment cache 재사용 실패로 세션 시작 불가

권장 feature flag:

```text
ANALYST_OUTAGE_HANDLING_ENABLED=true
ANALYST_QUORUM_MIN_AVAILABLE=2
ANALYST_PARTIAL_SIZE_MULT=0.75
```

단, 운영 파라미터 변경과 혼동되지 않도록 기본값은 코드 상수로 시작하고 env 승격은 별도 승인 후 진행한다.

## 9. 완료 정의

완료 조건:

- 모든 acceptance criteria가 테스트로 고정됨
- dashboard raw error 미노출 확인
- 1명/2명/3명 실패 조합 회귀 통과
- `py_compile` 통과
- quorum failed 상태에서 신규 진입 차단 확인
- unavailable analyst가 brain performance에 반영되지 않음
- PathB live 운영 파라미터 변경 없음
