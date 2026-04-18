# A안: momentum_opening Claude Gate 구현 계획

**작성일**: 2026-04-08
**배경**: 하루 매수 2~3건 미만 → 단일가봉 수정 효과 이틀 관찰 후 필요 시 구현
**목적**: 장초 30분 한정으로 momentum 전략을 Claude 다수결 gate로 허용

---

## 개요

현재 `momentum.py`는 US 전체 disabled, KR은 BEAR 계열에서 disabled.
Claude가 강한 모멘텀 장세로 판단할 때 장초 30분 한정으로 허용.
기본값 False (Claude가 명시적으로 True를 반환해야만 활성화).

---

## 구현 범위

### 1. `minority_report/analysts.py`

분석가 JSON 출력에 `strategy_gates` 블록 추가.
`momentum_opening` 하나만 — 다른 전략은 건드리지 않음.

**프롬프트 JSON 형식 추가 위치**: `_build_r1_prompt()` 또는 R2 프롬프트의 JSON 예시 부분.

```
"strategy_gates": {
  "momentum_opening": true   // 장초 30분 모멘텀 허용 여부 (기본 false)
}
```

**파싱**: `_sanitize_analyst_result()` 내에서

```python
gates = result.get("strategy_gates", {})
momentum_opening = bool(gates.get("momentum_opening", False))
# sanitized result에 추가
result_out["momentum_opening"] = momentum_opening
```

---

### 2. `minority_report/consensus.py`

`build_consensus()` 반환값에 `strategy_gates` 추가.
**조건: 3명 중 2명 이상 True여야 활성화.**

```python
# build_consensus() 내부, result dict 구성 전에
_mo_votes = sum(1 for j in (bull, bear, neut) if j.get("momentum_opening", False))
momentum_opening_gate = _mo_votes >= 2

result = {
    ...기존 필드...,
    "strategy_gates": {
        "momentum_opening": momentum_opening_gate,
    },
    "_mo_votes": _mo_votes,   # 디버그용 (선택)
}
```

로그:
```python
log.info(f"strategy_gates: momentum_opening={momentum_opening_gate} ({_mo_votes}/3)")
```

---

### 3. `trading_bot.py` — 전달 경로

`session_open`에서 consensus 받은 후 `_strategy_gates` 저장:

```python
# 기존
consensus = build_consensus(judgments, market=market)
# 추가
self._strategy_gates[market] = consensus.get("strategy_gates", {})
```

`__init__`에 초기화:
```python
self._strategy_gates: dict = {"KR": {}, "US": {}}
```

---

### 4. `trading_bot.py` — 신호 라우팅

`run_cycle` 내 전략 신호 체크 부분 (`_gap_sig`, `_mr_sig`, `_mom_sig` 등 호출 위치).

**현재**: momentum이 disabled면 `_mom_sig()` 자체가 False 반환.

**변경 후**: strategy_gates에서 momentum_opening이 True이고 장초 30분이면 momentum params에서 disabled 해제.

```python
_gates = self._strategy_gates.get(market, {})
_in_opening = 0 < session_elapsed_min <= 30

# momentum_opening gate 적용
if _gates.get("momentum_opening") and _in_opening:
    _mom_params = momentum.params(mode, conf, market)
    _mom_params.pop("disabled", None)   # disabled 키 제거
else:
    _mom_params = momentum.params(mode, conf, market)   # 기존 (disabled 포함 가능)
```

---

## 구현 시 주의사항

### momentum.py 신호가 장초에 실제로 작동하는가?

현재 `momentum.signal(df, i, params)`는 **daily candle** 기반:
- MA5 > MA20 > MA60
- MACD 골든크로스
- vol_ratio (20일 평균 대비)
- 최근 20일 고점 돌파

장초 30분이면 당일 daily candle 데이터는 전날까지의 값.
→ "전날 기준 추세가 살아있는 종목"만 장초에 진입하는 논리.
→ gap_pullback과 병행하면 "갭 + 추세 둘 다 확인" 더블 필터.

**확인 필요**: `_mom_sig()` 호출 시 주입하는 df가 daily bar인지 intraday bar인지.

### brain.json 피드백 루프

strategy_gates는 brain.json에 직접 저장되지 않음.
간접 경로: momentum으로 진입한 거래의 pnl → `update_strategy_performance("momentum", ...)`.
→ 향후 momentum 성과가 쌓이면 brain.json에서 자연스럽게 반영됨.

### 롤백 방법

`self._strategy_gates[market] = {}` 로 초기화하면 즉시 비활성화.
또는 consensus에서 `strategy_gates` 키 제거 → 기존 동작 그대로.

---

## 구현 판단 기준 (이틀 관찰 후)

| 관찰 결과 | 결정 |
|---|---|
| 단일가봉 수정 후 매수 5건/일 이상 | A안 보류 — 구조 문제 아님 |
| 여전히 2~3건 미만 + gap_pullback 조건 미충족이 원인 | gap_min / vol_mult 파라미터 재검토 먼저 |
| gap_pullback 조건은 맞는데 종목 선택 자체가 모멘텀 종목 위주 | A안 구현 — momentum_opening이 답 |
| 종목 선택은 좋은데 장초 이후에 신호 뜸 | 시간대 문제, A안과 무관 |

---

## 파일별 변경 요약

| 파일 | 변경 내용 | 규모 |
|---|---|---|
| `minority_report/analysts.py` | 프롬프트에 strategy_gates JSON 추가 + 파싱 | ~15줄 |
| `minority_report/consensus.py` | 2/3 집계 + result에 strategy_gates 추가 | ~10줄 |
| `trading_bot.py` | `_strategy_gates` 저장 + 신호 라우팅 조건 | ~20줄 |
| `strategy/momentum.py` | 변경 없음 — disabled 키 pop으로 처리 | 0줄 |
