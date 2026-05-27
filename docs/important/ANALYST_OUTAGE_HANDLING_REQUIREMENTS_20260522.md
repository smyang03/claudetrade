# Analyst API Outage Handling Requirements

작성일: 2026-05-22  
상태 업데이트: 2026-05-25 - core unavailable/quorum/learning-exclusion code is implemented; remaining UI polish/tests are tracked in `ACTIVE_WORK.md`. This file is retained as source evidence, not as an active plan.
대상: `minority_report/analysts.py`, `minority_report/consensus.py`, `minority_report/postmortem.py`, `dashboard/dashboard_server.py`, `trading_bot.py`

## 1. 배경

Anthropic API `529 overloaded_error` 같은 provider 일시 장애가 발생하면 일부 분석가 호출이 실패한다. 이 요구사항 작성 당시 구현은 실패한 분석가를 `NEUTRAL`, confidence `0.3`, `key_reason="오류:..."` 형태로 반환했다.

이 방식은 다음 문제를 만든다.

- 실패한 분석가가 실제 중립 의견처럼 consensus에 참여한다.
- R1 실패 결과가 R2 토론의 `others` 입력으로 들어가 다른 분석가 판단까지 오염시킨다.
- 3명 모두 실패해도 `NEUTRAL/NEUTRAL/NEUTRAL` consensus가 생성될 수 있다.
- 대시보드에 raw provider error 문자열이 그대로 노출된다.
- postmortem 및 `brain.json` analyst performance 학습에 실패 analyst가 HIT/MISS 표본으로 들어갈 수 있다.

## 2. 목표

- provider 장애와 시장 판단을 분리한다.
- 실패한 분석가를 `NEUTRAL`로 위장하지 않는다.
- 가용한 분석가만으로 partial consensus를 만들되, quorum 미달 시 신규 진입을 차단한다.
- R2 토론에서 실패 analyst 의견을 제거한다.
- 대시보드에는 사람이 읽을 수 있는 장애 상태를 표시하고 raw error는 노출하지 않는다.
- postmortem/learning에서 unavailable analyst를 성과 표본에서 제외한다.
- 5분 자동 retry는 2차 단계로 분리하고, 1차 구현에서는 안전한 degraded 판단 상태만 만든다.

## 3. 비목표

- Anthropic API 장애를 시장 `HALT`로 직접 변환하지 않는다.
- PathB 운영 파라미터, live enable gate, 주문 금액, 포지션 한도, slippage cap은 변경하지 않는다.
- `state/brain.json`을 런타임 truth로 사용하거나 자동 정책 메모리로 승격하지 않는다.
- retry 성공 결과를 장중 주문/포지션 상태와 무관하게 자동 적용하지 않는다.

## 4. 용어

| 용어 | 의미 |
|---|---|
| available analyst | R1 또는 R2 판단을 정상 반환했고 `analyst_unavailable`이 아닌 분석가 |
| unavailable analyst | API overload, timeout, rate limit, provider error, parse failure 등으로 정상 판단을 반환하지 못한 분석가 |
| partial consensus | 3명 중 일부만 available인 상태에서 생성한 제한적 consensus |
| quorum failed | 정상 available analyst 수가 최소 판단 기준보다 낮아 신규 진입을 차단해야 하는 상태 |
| provider outage | Anthropic API 과부하/일시 장애. 시장 위험 신호가 아니다 |

## 5. Analyst Result Contract

### R1. 실패 fallback은 `NEUTRAL` 의견으로 취급하면 안 된다

`_fallback_result()`는 다음 필드를 포함해야 한다.

```python
{
    "stance": "NEUTRAL",
    "confidence": 0.0,
    "analyst_unavailable": True,
    "availability_status": "provider_unavailable",
    "error_type": "anthropic_overloaded",
    "retryable": True,
    "display_reason": "Anthropic API 과부하로 분석가 일시 불가",
    "key_reason": "분석가 일시 불가",
    "learning_excluded": True,
}
```

설명:

- `stance`는 기존 코드 호환성을 위해 `NEUTRAL`을 유지할 수 있다.
- 단, 모든 판단/집계/학습 로직은 `analyst_unavailable=True`를 우선 확인해야 한다.
- unknown stance인 `UNAVAILABLE`을 새로 넣는 방식은 피한다. 현재 postmortem은 모르는 stance를 neutral처럼 처리할 수 있다.
- raw error 전문은 dashboard payload에 넣지 않는다.
- raw error는 로그에만 남기고, 필요 시 provider code와 짧은 원인만 구조화 필드로 보관한다.

### R2. 오류 분류

최소 다음 오류는 retryable analyst outage로 분류한다.

| 조건 | `error_type` | `retryable` |
|---|---|---|
| Anthropic 529 / overloaded_error | `anthropic_overloaded` | true |
| rate limit | `rate_limited` | true |
| network timeout | `timeout` | true |
| temporary connection error | `network_error` | true |
| JSON parse failure | `parse_error` | false 또는 제한적 true |
| 인증/키 오류 | `auth_error` | false |

## 6. R2 Debate Requirements

### R3. unavailable analyst는 `others`에서 제외한다

`get_three_judgments()`에서 R2 호출 시 다음 규칙을 적용한다.

- `others`에는 `analyst_unavailable=True`인 R1 결과를 넣지 않는다.
- 자기 자신의 R1 결과가 unavailable이면 해당 analyst의 R2 호출을 skip한다.
- skip된 analyst는 R1 fallback payload를 그대로 유지하되 `r2_skipped=True`, `skip_reason="analyst_unavailable"`을 추가한다.

### R4. R2 prompt는 1명 또는 0명의 other analyst를 자연스럽게 처리해야 한다

현재 prompt는 "2명"이라고 고정하지는 않지만, 다음 문장을 추가하는 것이 좋다.

```text
가용한 다른 분석가 판단만 표시됩니다. 표시되지 않은 분석가는 API 장애로 토론에서 제외되었습니다.
```

`others`가 비어 있으면 R2 호출은 하지 않는 것을 기본으로 한다. 이 경우 R1 판단을 유지하고 `r2_skipped=True`, `skip_reason="no_available_peers"`를 남긴다.

## 7. Consensus Requirements

### R5. consensus는 available analyst만으로 계산한다

`build_consensus()`는 다음을 먼저 계산해야 한다.

```python
available_roles = [
    role for role in ("bull", "bear", "neutral")
    if not judgments.get(role, {}).get("analyst_unavailable")
]
unavailable_roles = [...]
active_analyst_count = len(available_roles)
```

점수 계산은 available analyst만 사용한다. missing analyst를 암묵적으로 neutral로 넣으면 안 된다.

가중 평균은 다음 원칙을 따른다.

- active analyst score만 사용한다.
- denominator는 active weights 합으로 계산한다.
- coverage 부족에 따른 보수성은 별도 penalty 필드로 반영한다.

### R6. quorum 기준

| available 수 | 처리 |
|---:|---|
| 3 | 정상 consensus |
| 2 | `partial_consensus=True`, active 2명으로 mode 계산, confidence/size coverage penalty 적용, 신규 진입 가능 |
| 1 | `quorum_failed=True`, 신규 진입 차단, 기존 포지션 보호만 허용 |
| 0 | `all_analysts_unavailable=True`, 신규 진입 차단, retry/review 유도 |

### R7. quorum failed는 시장 `HALT`가 아니다

1명 이하 available 상태에서 consensus는 다음 형태를 사용한다.

```python
{
    "mode": "NEUTRAL",
    "size": 0,
    "new_buy_permission": "block",
    "partial_consensus": True,
    "quorum_failed": True,
    "active_analyst_count": 1,
    "unavailable_analysts": ["bear", "neutral"],
    "block_reason": "analyst_quorum_failed",
}
```

3명 모두 실패한 경우:

```python
{
    "mode": "NEUTRAL",
    "size": 0,
    "new_buy_permission": "block",
    "partial_consensus": False,
    "quorum_failed": True,
    "all_analysts_unavailable": True,
    "active_analyst_count": 0,
    "unavailable_analysts": ["bull", "bear", "neutral"],
    "block_reason": "all_analysts_unavailable",
}
```

반복 실패 N회 후 운영 경보를 올릴 수는 있으나, 그 상태도 `AI judgment outage`로 표현한다. 시장 `HALT`는 실제 시장 위험 판단 또는 기존 hard risk 조건에서만 사용한다.

### R8. coverage penalty

2명 available인 partial consensus에는 다음 보수 조정을 적용한다.

- `partial_consensus=True`
- `coverage_ratio=0.6667`
- `confidence`는 active analyst 평균 confidence에 coverage ratio를 반영한다.
- `size`는 계산된 size에 coverage multiplier를 적용한다.

권장 기본값:

| available 수 | size multiplier | 신규 진입 |
|---:|---:|---|
| 3 | 1.00 | 허용 |
| 2 | 0.70~0.85 | 허용 |
| 1 | 0.00 | 차단 |
| 0 | 0.00 | 차단 |

정확한 multiplier는 코드 상수로 두되, live env/config 운영 파라미터는 변경하지 않는다.

### R9. analyst new-buy constraints는 available analyst만 사용한다

`_analyst_new_buy_constraints()` 입력에는 available analyst만 넣는다.

단, quorum failed 상태에서는 analyst vote와 무관하게:

- `new_buy_permission="block"`
- `size=0`
- `block_reason="analyst_quorum_failed"` 또는 `"all_analysts_unavailable"`

## 8. Trading Runtime Requirements

### R10. 신규 진입 gate는 quorum status를 확인한다

`trading_bot.py` 신규 진입 경로는 consensus의 다음 필드를 확인해야 한다.

- `quorum_failed`
- `all_analysts_unavailable`
- `new_buy_permission`
- `block_reason`

`quorum_failed=True`이면:

- 신규 매수는 차단한다.
- 기존 포지션 보호 로직, 손절, 청산, 브로커 truth sync는 계속 수행한다.
- selection 품질 문제로 기록하지 않는다. 원인은 `analyst_quorum_failed`로 분리한다.

### R11. confidence 계산은 unavailable analyst를 제외한다

현재 runtime의 평균 confidence 계산은 3명을 고정으로 나눌 수 있다. 변경 후에는:

- available analyst만 평균에 포함한다.
- quorum failed이면 confidence와 무관하게 신규 진입을 차단한다.
- unavailable analyst의 confidence `0.0`을 평균에 섞어 암묵적으로 판단하지 않는다.

## 9. Dashboard Requirements

### R12. raw provider error는 dashboard에 표시하지 않는다

대시보드 analyst card에는 다음 상태를 표시한다.

```text
하락 분석가 일시 불가
원인: Anthropic API 과부하
상태: 나머지 분석가 기준으로 임시 판단 중
```

짧은 표시:

```text
API 과부하로 하락 분석가 제외됨
```

### R13. partial consensus 상태를 표시한다

대시보드는 consensus 영역 또는 analyst card 주변에 다음 정보를 표시해야 한다.

- `3명 중 2명 정상`
- `partial consensus`
- `신규 진입 가능/차단 여부`
- `다음 조치: /review 또는 재시도 대기`

quorum failed 상태에서는 명확히 표시한다.

```text
분석가 quorum 부족으로 신규 진입 차단 중. 기존 포지션 보호 로직은 유지됩니다.
```

## 10. Postmortem and Brain Learning Requirements

### R14. unavailable analyst는 HIT/MISS 성과 표본에서 제외한다

`postmortem.py`에서 `BrainDB.update_analyst()` 호출 전에 각 analyst payload를 확인한다.

```python
if not judgment.get("analyst_unavailable"):
    BrainDB.update_analyst(...)
```

unavailable analyst는 다음을 남길 수 있다.

- daily record에는 stance/reason 대신 availability status를 기록
- `*_result`는 `"UNAVAILABLE"` 또는 빈 값 사용
- `learning_excluded=True`

단, `BrainDB.update_analyst()`에는 전달하지 않는다.

### R15. debate history 오염 방지

`BrainDB.save_debate_result()`에 unavailable analyst를 그대로 저장하면 이후 R2 prompt history가 오염될 수 있다.

요구사항:

- unavailable analyst는 debate change로 계산하지 않는다.
- debate history에는 `availability_status`를 남기되 stance change 통계에는 포함하지 않는다.
- `get_debate_summary()`는 unavailable 기록을 "API 장애로 제외" 정도로 짧게 표시하거나 생략한다.

## 11. Retry Requirements

### R16. retry는 2차 구현으로 분리한다

1차 구현에서는 다음만 수행한다.

- analyst unavailable 상태 기록
- partial/quorum consensus 생성
- dashboard 표시
- 신규 진입 차단 여부 반영
- postmortem learning 제외

### R17. 2차 retry 정책

추후 retry 구현 시 기준:

| 상태 | 처리 |
|---|---|
| 첫 진입 전 | 5분 후 1회 자동 retry 허용, 성공 시 consensus 자동 갱신 가능 |
| 주문 생성 후 | retry 결과는 dashboard/log에 표시만 하고 자동 적용 금지 |
| 포지션 보유 중 | 기존 포지션 보호 우선, 자동 mode 전환 적용 금지 |
| 반복 실패 | `AI judgment outage` 운영 경보, 신규 진입 차단 유지 |

기존 `_reinvoke_analysts()`는 성공 시 `today_judgment["consensus"]`를 즉시 갱신하고 종목 재선정까지 할 수 있다. retry 경로는 이 함수와 구분하거나, 자동 적용 가능 상태를 명시적으로 검사해야 한다.

## 12. Logging Requirements

### R18. 로그는 원인과 상태를 분리한다

로그에는 다음을 남긴다.

- analyst role
- round: R1/R2
- provider error code
- retryable 여부
- availability status
- raw error truncated

사람이 읽는 운영 로그는 한국어를 유지한다.

예:

```text
[bear R1] Anthropic API 과부하로 분석가 제외: retryable=true code=529
[consensus KR] partial_consensus active=2 unavailable=bear size_mult=0.75
[entry gate KR] analyst_quorum_failed active=1 -> 신규 진입 차단
```

## 13. Acceptance Criteria

### AC1. 1명 실패

Given bull/neutral 정상, bear 529 실패  
When session judgment 생성  
Then:

- bear payload has `analyst_unavailable=True`
- R2에서 bull/neutral은 bear fallback을 others로 받지 않는다
- consensus has `partial_consensus=True`
- `active_analyst_count=2`
- 신규 진입은 coverage penalty 적용 후 허용 가능
- dashboard는 raw error 없이 "하락 분석가 일시 불가" 표시
- postmortem에서 bear는 `update_analyst()` 대상이 아니다

### AC2. 2명 실패

Given bull만 정상, bear/neutral 실패  
When consensus 생성  
Then:

- `quorum_failed=True`
- `active_analyst_count=1`
- `new_buy_permission="block"`
- `size=0`
- 신규 진입 차단
- 기존 포지션 보호/청산 로직은 계속 동작
- `mode`는 `HALT`가 아니라 `NEUTRAL` 또는 기존 보수 fallback이다

### AC3. 3명 모두 실패

Given bull/bear/neutral 모두 529 실패  
When consensus 생성  
Then:

- `all_analysts_unavailable=True`
- `quorum_failed=True`
- `size=0`
- `new_buy_permission="block"`
- 대시보드는 "분석가 API 장애로 신규 진입 차단" 표시
- 시장 `HALT`로 기록하지 않는다

### AC4. R2 실패

Given R1 정상, 특정 analyst R2 호출 실패  
When R2 결과 병합  
Then:

- 해당 analyst는 R1 결과 유지
- `r2_unavailable=True` 또는 `r2_failed=True` 기록
- R1이 정상이라면 consensus에는 available analyst로 포함 가능
- raw error는 dashboard에 노출하지 않는다

### AC5. postmortem learning exclusion

Given bear analyst unavailable  
When postmortem 실행  
Then:

- `BrainDB.update_analyst(market, "bear", ...)`는 호출되지 않는다
- daily record에는 bear unavailable 상태가 남는다
- bull/neutral 정상 analyst는 기존대로 평가된다

## 14. Test Requirements

추가 또는 갱신할 테스트:

- `tests/test_analyst_outage_handling.py`
  - `_fallback_result()` contract
  - R2 others filtering
  - self unavailable R2 skip
- `tests/test_consensus_quorum.py`
  - 3/3 정상
  - 2/3 partial consensus
  - 1/3 quorum failed
  - 0/3 all unavailable
- `tests/test_dashboard_analyst_outage.py`
  - raw error 미노출
  - display_reason 표시
  - partial/quorum 상태 표시
- `tests/test_postmortem_analyst_outage.py`
  - unavailable analyst `update_analyst()` 제외
  - available analyst만 성과 업데이트

기본 검증 명령:

```powershell
python -m pytest tests/test_analyst_outage_handling.py tests/test_consensus_quorum.py -q
python -m pytest tests/test_dashboard_analyst_outage.py tests/test_postmortem_analyst_outage.py -q
python -m py_compile trading_bot.py dashboard/dashboard_server.py claude_memory/brain.py
```

## 15. Code-Level Re-Review Checklist

구현 전후 리뷰는 아래 순서로 확인한다. 핵심은 provider 장애를 시장 판단, selection 품질, execution/risk 문제와 섞지 않는 것이다.

### 15.1 `minority_report/analysts.py`

점검 항목:

- `_fallback_result()`가 `analyst_unavailable=True`와 `learning_excluded=True`를 반환하는가.
- fallback의 `key_reason`이 raw provider error가 아닌 운영용 짧은 문구인가.
- raw error는 log에만 남고 dashboard payload로 직접 전달되지 않는가.
- R1 실패 analyst가 R2 `others`에 포함되지 않는가.
- 자기 R1이 unavailable이면 R2 Claude 호출을 skip하는가.
- R2 호출 실패는 R1 정상 판단을 유지하되 `r2_failed=True` 같은 별도 상태로 표시하는가.
- `_sanitize_analyst_result()`가 정상 analyst payload에만 적용되고 unavailable marker를 제거하지 않는가.

개선 전 확인:

```python
return {
    "stance": "NEUTRAL",
    "confidence": 0.3,
    "key_reason": f"오류:{str(error)[:60]}",
}
```

개선 후 확인:

```python
return {
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

### 15.2 `minority_report/consensus.py`

점검 항목:

- `judgments["bull"]`, `judgments["bear"]`, `judgments["neutral"]`를 무조건 정상으로 가정하지 않는가.
- available analyst만 score, confidence, suggested size, new-buy constraints 계산에 포함하는가.
- active weight denominator가 3 고정이 아니라 active weight 합인가.
- 2명 available이면 `partial_consensus=True`와 coverage penalty가 적용되는가.
- 1명 이하 available이면 `quorum_failed=True`, `size=0`, `new_buy_permission="block"`인가.
- 0명 available이면 `all_analysts_unavailable=True`인가.
- provider outage 때문에 `mode="HALT"`가 자동 설정되지 않는가.
- `apply_unanimous_override()`가 unavailable analyst를 포함해 3:0 만장일치로 오판하지 않는가.

개선 전 확인:

```python
scores = {
    "bull": STANCE_SCORE.get(bull["stance"], 0.0),
    "bear": STANCE_SCORE.get(bear["stance"], 0.0),
    "neutral": STANCE_SCORE.get(neut["stance"], 0.0),
}
weighted_score = sum(scores[k] * weights[k] for k in scores) / 3.0
avg_conf = (...) / 3.0
```

개선 후 확인:

```python
active = [(role, judgments[role]) for role in roles if not judgments[role].get("analyst_unavailable")]
if len(active) < 2:
    return fail_closed_quorum_result(...)
weighted_score = sum(score(role) * weight(role) for role, _ in active) / sum(weight(role) for role, _ in active)
avg_conf = sum(conf(j) for _, j in active) / len(active)
```

### 15.3 `trading_bot.py`

점검 항목:

- 신규 진입 gate가 `consensus.quorum_failed`와 `all_analysts_unavailable`을 먼저 확인하는가.
- `_avg_conf` 계산에서 unavailable analyst를 제외하는가.
- quorum failed 차단 사유가 `low_confidence`, selection 품질, affordability fail과 분리되는가.
- 기존 포지션 보호, broker truth sync, 미체결 주문 정리, 손절/청산 경로는 quorum failed에서도 유지되는가.
- `_reinvoke_analysts()`를 retry로 재사용할 경우 자동 적용 가능 상태를 검사하는가.
- session reuse/shared cache가 outdated unavailable 판단을 정상 판단처럼 재사용하지 않는가.

개선 전 확인:

```python
_avg_conf = (
    sum(_judgments.get(a, {}).get("confidence", 0.5) for a in ("bull", "bear", "neutral")) / 3.0
    if _judgments else 0.5
)
```

개선 후 확인:

```python
active = [j for j in _judgments.values() if not j.get("analyst_unavailable")]
if consensus.get("quorum_failed"):
    block_reason = consensus.get("block_reason", "analyst_quorum_failed")
elif active:
    _avg_conf = sum(float(j.get("confidence", 0.0) or 0.0) for j in active) / len(active)
```

### 15.4 `dashboard/dashboard_server.py`

점검 항목:

- `/api/judgments`가 raw error를 그대로 반환하지 않는가.
- analyst card가 `analyst_unavailable=True`를 별도 상태로 표시하는가.
- timeline row의 analyst reason에도 raw error가 노출되지 않는가.
- partial/quorum 상태가 consensus 영역에서 보이는가.
- `koMode("NEUTRAL")`와 unavailable display가 혼동되지 않는가.

개선 전 확인:

```javascript
const keyReason = escapeHtml(info.key_reason || '-');
```

개선 후 확인:

```javascript
const unavailable = !!info.analyst_unavailable;
const keyReason = unavailable
  ? escapeHtml(info.display_reason || '분석가 일시 불가')
  : escapeHtml(info.key_reason || '-');
```

### 15.5 `minority_report/postmortem.py` and `claude_memory/brain.py`

점검 항목:

- unavailable analyst에 대해 `_code_judge_hit_miss()`를 호출하지 않는가.
- unavailable analyst에 대해 `BrainDB.update_analyst()`를 호출하지 않는가.
- daily record에는 unavailable 상태가 남되 analyst performance 표본에는 들어가지 않는가.
- debate history 통계에서 unavailable analyst change가 stance change로 집계되지 않는가.
- `tools/rebuild_brain.py` 같은 재계산 도구가 `UNAVAILABLE` 또는 `learning_excluded`를 표본 제외하는가.

개선 전 확인:

```python
BrainDB.update_analyst(market, "bear", pm["bear_result"] == "HIT", recent)
```

개선 후 확인:

```python
if not judgments.get("bear", {}).get("analyst_unavailable"):
    BrainDB.update_analyst(market, "bear", pm["bear_result"] == "HIT", recent)
```

## 16. Before/After Verification Matrix

| 시나리오 | 개선 전 | 개선 후 확인 |
|---|---|---|
| bear R1 529 | bear가 `NEUTRAL`로 consensus 참여 | bear `analyst_unavailable=True`, consensus `active_analyst_count=2` |
| bear R1 529 후 R2 | bull/neutral이 가짜 bear neutral 의견을 보고 토론 | bull/neutral R2 `others`에 bear 없음 |
| 3명 모두 529 | `neutral/neutral/neutral` consensus 가능 | `all_analysts_unavailable=True`, `size=0`, 신규 진입 차단 |
| 2명 실패 | 남은 1명 + 가짜 neutral 2명으로 판단 가능 | `quorum_failed=True`, 신규 진입 차단 |
| dashboard 표시 | `Error code: 529 - {...}` 노출 | "API 과부하로 분석가 일시 불가" 표시 |
| postmortem | 실패 analyst가 HIT/MISS로 성과 반영 | unavailable analyst는 performance update 제외 |
| runtime confidence | 실패 confidence가 평균에 섞임 | active analyst만 평균, quorum failed는 별도 차단 |
| unanimous override | unavailable neutral 포함으로 방향 왜곡 가능 | available analyst 기준만 사용 또는 quorum failed 우선 |
| retry | 재판단 성공 시 상태 무관 자동 적용 위험 | 2차 구현 전까지 자동 retry 없음 |

## 17. Code-Level Risk Mitigation Plan

### 17.1 Backward Compatibility Risk

리스크:

- `stance="UNAVAILABLE"`을 새로 도입하면 기존 `STANCE_SCORE`, `koMode`, postmortem scoring이 예상하지 못한 값을 neutral처럼 처리하거나 화면에 그대로 노출할 수 있다.

완화:

- `stance`는 기존 호환을 위해 `NEUTRAL` 유지 가능.
- 의미 판단은 반드시 `analyst_unavailable=True` 플래그를 우선한다.
- test에서 unknown stance를 쓰지 않고 marker-based exclusion을 검증한다.

### 17.2 Consensus Math Risk

리스크:

- active analyst만 사용하면서 denominator를 여전히 3으로 두면 score가 과도하게 중립으로 눌린다.
- 반대로 denominator를 active 수로만 두고 coverage penalty가 없으면 2명 판단이 3명 판단처럼 강해진다.

완화:

- score 평균은 active weight 합으로 계산한다.
- size/confidence에는 coverage multiplier를 별도로 적용한다.
- consensus output에 `coverage_ratio`와 `size_before_coverage_penalty`를 남긴다.

### 17.3 Entry Safety Risk

리스크:

- quorum failed인데 `mode=NEUTRAL`만 보고 신규 진입이 계속될 수 있다.

완화:

- 신규 진입 gate에서 `quorum_failed`를 `low_confidence`보다 먼저 검사한다.
- block reason은 `analyst_quorum_failed`로 기록한다.
- ML/eval 기록도 selection 실패가 아니라 provider outage block으로 남긴다.

### 17.4 Dashboard Misleading Display Risk

리스크:

- unavailable analyst가 `NEUTRAL` badge로 보이면 운영자가 실제 중립 의견으로 오해한다.

완화:

- analyst card에 unavailable badge를 별도 표시한다.
- stance 라벨 옆에 "일시 불가" 상태를 우선 표시한다.
- timeline에서도 raw key_reason 대신 display_reason을 사용한다.

### 17.5 Learning Contamination Risk

리스크:

- unavailable analyst가 MISS로 누적되면 analyst performance, future weighting, R1 feedback prompt가 장기간 왜곡된다.

완화:

- `learning_excluded=True`면 `update_analyst()` skip.
- daily record에는 unavailable count를 남겨 운영 관측만 가능하게 한다.
- `tools/rebuild_brain.py`도 unavailable rows 제외 규칙을 맞춘다.

### 17.6 Debate History Contamination Risk

리스크:

- R1 fallback과 R2 skip이 debate history에 stance 유지/변경으로 저장되면 이후 R2 prompt history가 오염된다.

완화:

- unavailable analyst는 `changes` 계산에서 제외한다.
- debate entry에는 `unavailable_analysts`를 별도 필드로 남긴다.
- `get_debate_summary()`는 unavailable rows를 성과 통계에서 제외한다.

### 17.7 Retry Auto-Apply Risk

리스크:

- 5분 retry 성공 후 `_reinvoke_analysts()`가 consensus와 watchlist를 자동 갱신하면 이미 생성된 주문/포지션 판단과 충돌할 수 있다.

완화:

- retry는 2차 구현으로 분리한다.
- retry 결과의 자동 적용은 "주문 없음, 포지션 변화 없음, pending order 없음" 조건에서만 허용한다.
- 그 외에는 dashboard/log에만 표시하고 `/review`를 유도한다.

### 17.8 Shared Cache Risk

리스크:

- partial/quorum failed judgment가 shared judgment cache에 저장되어 다른 paper/live 런타임이 정상 판단처럼 재사용할 수 있다.

완화:

- persisted judgment에 availability metadata를 포함한다.
- shared cache reuse 시 `quorum_failed` 또는 `all_analysts_unavailable`이면 fresh judgment 또는 신규 진입 차단 상태로만 재사용한다.
- dashboard에는 cache source와 degraded 상태를 함께 표시한다.

### 17.9 Test Blind Spot Risk

리스크:

- 정상 3명 판단 테스트만 통과하고 outage 조합이 회귀에서 빠질 수 있다.

완화:

- 1명, 2명, 3명 실패 조합을 각각 unit test로 고정한다.
- R2 self unavailable skip과 others filtering을 mock call count로 검증한다.
- dashboard test는 raw `"Error code:"` 문자열이 HTML/API 응답에 없는지 확인한다.

## 18. Implementation Order

1. `analysts.py`
   - fallback payload contract 변경
   - provider error 분류 helper 추가
   - R2 others filtering
   - unavailable self R2 skip

2. `consensus.py`
   - available analyst filtering
   - active weight 평균 계산
   - partial consensus metadata 추가
   - quorum failed fail-closed 추가
   - new-buy constraints를 available analyst만 반영

3. `trading_bot.py`
   - runtime confidence 평균 계산에서 unavailable 제외
   - quorum failed entry gate 추가
   - block reason을 selection/execution 품질 문제와 분리

4. `dashboard_server.py`
   - analyst unavailable display 변환
   - raw error 미노출
   - partial/quorum 상태 표시

5. `postmortem.py` / `claude_memory/brain.py`
   - unavailable analyst 성과 업데이트 제외
   - debate history 오염 방지

6. Tests
   - 위 acceptance criteria별 단위 테스트 추가
   - 관련 regression 실행

## 19. Operational Notes

- 529는 코드 버그가 아니라 provider overload다.
- provider overload는 시장 위험 판단이 아니다.
- partial consensus는 정상 판단보다 낮은 신뢰도의 운영 상태다.
- quorum failed는 신규 진입 차단 사유이며, 기존 포지션 보호를 중지하는 사유가 아니다.
- retry는 필요하지만 자동 적용 범위가 주문/포지션 상태에 민감하므로 2차 단계에서 구현한다.
