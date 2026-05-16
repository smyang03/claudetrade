# 로직 강화 플랜 — Market Regime Filter / Risk:Reward 자동 검증

작성일: 2026-05-16  
우선순위: P2 (후보군 강화 / Claude 품질 개선 이후)  
상태: 검토 대기

---

## 배경

현재 시스템은 HALT/Risk-Off 상태를 이진으로 처리한다.  
변동성 구간별 전략 비중 자동 조절이나 진입 전 Risk:Reward 검증은 없다.  
이 플랜은 두 가지를 추가하는 방향을 정리한다.

---

## 항목 1. Market Regime Filter

### 현재 상태
- HALT / Risk-Off 이진 상태
- Risk-Off 예외: mean_reversion만 허용, size cap 40%
- ATR 기반 KR 모멘텀 cap 계층 존재 (cap / cap+1% / cap+2% / high_cap)

### 목표
변동성 구간(regime)별로 전략 허용/비중을 자동 조절한다.  
단, 기존 ATR cap 계층과 충돌하지 않도록 설계해야 한다.

### 구현 방향

```
Regime 구분 기준 (VKOSPI 또는 VIX 기반):
  CALM     : VKOSPI < 18
  NORMAL   : 18 <= VKOSPI < 25
  ELEVATED : 25 <= VKOSPI < 35
  STRESSED : VKOSPI >= 35

Regime별 행동:
  CALM     : 기본 동작 유지
  NORMAL   : 기본 동작 유지
  ELEVATED : size cap 70%, continuation 차단
  STRESSED : size cap 40%, mean_reversion 전용, HALT 검토
```

### 주의사항
- VKOSPI 결측이면 NORMAL로 처리 (현재 bear analyst와 동일 기준)
- 기존 ATR cap 계층과 이중 cap이 걸릴 경우 더 낮은 쪽 적용
- `brain.json` 자동 수정 대상 아님 — runtime 파라미터로만 적용
- 배포 전 5거래일 shadow 관찰 필수

### 필요한 작업
1. `_regime_from_vkospi(vkospi: float | None) -> str` 함수 추가 (trading_bot.py)
2. `_entry_size_cap_for_regime(regime: str) -> float` 함수 추가
3. 기존 size cap 계산 경로에 regime cap 병합
4. shadow 로그: `logs/regime/YYYYMMDD_regime.jsonl`
5. 테스트: VKOSPI 결측, 각 구간 경계값

---

## 항목 2. Risk:Reward 자동 검증

### 현재 상태
- 진입 전 affordability / hard risk block 체크 존재
- 예상 손익비(Risk:Reward) 계산 및 threshold 차단 없음

### 목표
진입 전 예상 손익비를 계산하고, 기준 미달이면 차단한다.

### 구현 방향

```
필요한 값:
  - 진입 예정가 (current_price 또는 limit_price)
  - 손절 예정가 (stop_loss_price)
  - 목표가 추정 (ATR 기반 또는 고정 배수)

계산:
  risk  = entry_price - stop_price           (long 기준)
  reward = target_price - entry_price
  rr_ratio = reward / risk  (0이면 skip)

차단 기준:
  rr_ratio < RR_MIN_THRESHOLD (default 1.5)
  → 진입 차단, 이유: "rr_ratio_below_threshold"
```

### 주의사항
- stop_loss_price 없으면 계산 skip (차단하지 않음)
- target_price 추정은 보수적으로: ATR * 1.5 또는 5% 고정 중 작은 값
- 이 로직이 hard stop-loss 해제나 변경에 관여해서는 안 됨
- Claude가 직접 rr_ratio를 계산하거나 threshold를 조정하면 안 됨

### 필요한 작업
1. `_compute_rr_ratio(entry, stop, target) -> float | None` 추가 (trading_bot.py)
2. 진입 필터 체인에 rr check 추가 (affordability check 이후)
3. `RR_MIN_THRESHOLD` env 파라미터화
4. 차단 시 로그: reason_code = "rr_ratio_below_threshold"
5. counterfactual metadata에 rr_ratio 저장
6. 테스트: stop 없는 경우, rr < threshold, rr >= threshold

---

## 배포 순서 제안

1. Regime filter shadow 5거래일 관찰
2. RR check shadow 5거래일 관찰 (차단하지 않고 로그만)
3. shadow 로그 수동 검토 후 live 적용 판단
4. 두 기능 동시 live 적용 금지 — 하나씩 순서대로

## 의존성

- 항목 1: VKOSPI 데이터 digest에 안정적으로 수급되어야 함
- 항목 2: stop_loss_price가 진입 필터 시점에 결정되어 있어야 함

## 보류 조건

- 현재 진행 중인 P0 (KR confirmation gate) / P1 완료 전까지 착수 금지
- Claude 판단 품질 개선 / 후보군 수급 데이터 붙이기 선행 권장
