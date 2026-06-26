# 설계 — PROFIT_LADDER capture leak shadow A/B (2026-06-26)

> 운영자 지시: PROFIT_LADDER 한정 shadow A/B 설계(설계만, 구현은 승인 후).
> 근거: `capture_leak_review_20260626.md` — PROFIT_LADDER N=32, MFE +3.02% → 실현 +0.48%(capture 0%, 반납 2.55pp). TARGET은 capture ~100%(타깃 정의시 다 잡음) = 개선여지 방증.
> **핵심: 새로 만들지 않는다.** `tail_capture` 엔진(`runtime/tail_capture.py`, `TAIL_CAPTURE_MODE=shadow`)이 이미 이 문제(러너 조기절단)를 위해 빌드돼 shadow로 가동 중. 이 설계는 그 엔진을 **ladder-leak 구간에 맞게 검증·조정**하는 것.

---

## 1. 문제 정의 (정밀)

exit-scan 우선순위(`runtime/pathb_runtime.py:3595~3624`):
```
mfe → weak_mfe → loss_cap → hard_stop → tail_capture_signal
   → tail_capture_owns_profit (러너 소유 시 ladder/target 억제·HOLD)
   → 그 외에만 profit_ladder_signal
```
- 엔진이 러너를 소유하면 ladder를 **이미 억제**한다 = ladder A/B 메커니즘이 존재.
- **그런데 엔진 활성 임계 `TAIL_CAPTURE_ACTIVATION_PCT=4`.** PROFIT_LADDER leak은 **MFE 평균 +3.02%(4% 미만)** → 엔진이 활성 안 됨 → ladder가 발동 → 러너를 +0.48%에 끊음.
- 즉 **ladder leak = MFE 2~4% 중간 구간**이고, 현 엔진(MFE≥4% 대형 꼬리 전용)의 사각지대다.

**명제(A/B로 검증):** `TAIL_CAPTURE_ACTIVATION_PCT`를 낮춰(예 2.5~3%) ladder-leak 구간(MFE~3%)을 엔진이 소유하게 하면, 러너 capture가 늘어 **net이 개선된다 — 단 반전 트레이드의 추가 반납(좌측 누수)이 그 이득을 넘지 않는 한.**

**양날(반드시 측정):** 활성을 낮추면 (a) +3% 갔다 그대로 가는 러너는 더 잡지만 (b) +3% 찍고 반전하는 트레이드는 ladder가 잠갔을 이익을 더 토해낸다. 설계서 원본 경고: "activation 너무 낮음(=진입부터 trail) = −97 대참사." → **활성을 *얼마나* 낮춰야 (a)>(b)인지가 A/B의 전부.**

---

## 2. A/B 구조 — 3단계 (전부 ladder 보호영역 무접촉, additive)

### Phase A — 오프라인 counterfactual 활성 스윕 (결정 게이트, 무위험·즉시)
**목적:** 라이브 손대기 전, 과거 PROFIT_LADDER 트레이드를 활성 임계별로 재생해 (a)>(b)인 임계가 *존재하는지* 먼저 본다.
- **도구:** 기존 `tools/tail_capture_sim.py`(forward 재구성 방법론) + 이번 MFE 백필(yfinance 5m, 커버리지 91%).
- **대상:** close_reason=CLOSED_PROFIT_LADDER 트레이드 전수(현 N=32, 백필로 확보). + 비교군으로 같은 기간 전체 PathB 청산.
- **스윕:** `activation ∈ {2.5, 3.0, 3.5, 4.0}` × `trail give ∈ {2, 3}%` × 시장(US/KR 분리).
- **각 셀에서 재구성:** 5m 경로로 "엔진이 그 임계로 소유했다면" 실현 vs **실제 ladder 실현**을 **페어로**. 핵심 분해:
  - capture 이득(러너 더 탐) − 반전 손실(반납 증가) = **net Δ per trade**.
  - 좌측 누수 증가 여부(반전 트레이드가 ladder보다 얼마나 더 토했나).
- **결정 게이트:** 어떤 (activation, trail)도 net Δ ≤ 0 이거나 좌측 누수↑면 → **Phase B 안 감(여기서 종료).** net Δ>0 & 좌측 누수 0인 셀이 있으면 그 파라미터로 Phase B.
- **함정 통제:** MFE/경로는 yfinance 5m **추정 상단**(체결 가능가 아님) → 재구성에 **보수적 슬리피지 모델**(설계 §7 잔여리스크). 절대값 과신 금지, 셀 *간* 상대비교. 생존편향 분리(러너 vs 반전 둘 다 카운트).

### Phase B — 라이브 shadow 페어 로깅 + forward 재구성 (검증, 무위험)
**조건:** Phase A에서 net Δ>0 셀 확인 시에만.
- **메커니즘:** 기존 `tail_capture` shadow 훅(`pathb_runtime` exit-scan, 설계 §2 line 3320 직후). 실청산 무접촉.
- **변경(shadow 한정):** ladder-leak 구간을 덮도록 **shadow 전용 낮은 활성 arm** 추가 — `TAIL_CAPTURE_SHADOW_ACTIVATION_PCT`(예 Phase A 승자값). 기존 enforce-path 활성(4)은 불변. 엔진이 shadow arm으로 "소유했을" 결정을 ladder 실제 결정과 **페어 JSONL** 로깅.
- **forward 재구성:** carry/HOLD 플래그된 라이브 포지션을 다음날 yfinance로 "엔진정책이면 얼마" vs actual 재구성(설계 §3, 오버나잇 포함 — shadow가 당일청산이라 못 보는 부분 보강).
- **측정:** `tools/tail_capture_forward_review.py` 확장 — PROFIT_LADDER 셀만 필터, A(ladder) vs B(shadow arm) net, 시장별·국면별(약세 포함)·반전손실.
- **표본 바:** ladder-leak 트레이드 N≥20 paired(시장별), **약세장 N세션 포함**(설계 §7: 약세 통과 전 enforce 금지).

### Phase C — enforce (게이트 통과 후만)
- **US 먼저**(꼬리 있음), 약세장 shadow 통과 후. **KR은 tight/off**(설계: KR 꼬리 없음, claude_price는 net 양수라 보존).
- 활성을 낮춘 값으로 enforce = 엔진이 ladder-leak 구간 러너를 소유. ladder는 fallback 유지.
- 충돌 우선순위·loss_cap 위임 정합 명문화(설계 §6 안전계약).

---

## 3. 측정 지표 (Phase A·B 공통)

| 지표 | 정의 | 합격 방향 |
|---|---|---|
| **net Δ/trade** | B(엔진 소유) net − A(ladder) net, 페어 | > 0 |
| capture | 실현/MFE | A 대비 ↑ |
| **반전 손실(좌측 누수)** | +X% 찍고 반전한 트레이드의 추가 반납 | **증가 0** (필수) |
| 시장 분리 | US vs KR 따로 | US만 통과 가능, KR tight |
| 국면 | 약세/강세 분리 | 약세 N세션 net Δ>0 |
| 오버나잇 | carry vs 당일청산 forward | carry는 RISK_ON만 |

**kill 바:** Phase B에서 net Δ ≤ 0 (시장별) **또는** 반전 손실 증가 **또는** 약세장 미통과 → enforce 금지, 활성 4 복귀.

---

## 4. config (신규, 기본 보수적 — Phase B 빌드 시)
```
TAIL_CAPTURE_SHADOW_ACTIVATION_PCT=3.0   # ladder-leak 구간 shadow arm (Phase A 승자값)
                                          # enforce-path 활성(=4)은 불변
TAIL_CAPTURE_LADDER_AB_ENABLED=false      # Phase B shadow 로깅 토글 (기본 off)
```
- 두 소스(.env.live + v2_start_config.json) 동기화. 기본 off.
- enforce 전환은 별도 토글 + 운영자 승인(보호영역).

---

## 5. 안전 계약 (설계 §6 계승)
- **profit_ladder = 수익 핵심 보호영역.** Phase A(오프라인)·B(shadow)는 **기존 ladder 청산 무접촉 + fallback 유지.**
- 하방은 loss_cap/hard_stop에 **위임**(엔진 자체 하드스톱 X). 슬리피지캡·일일한도·HALT 무수정.
- 오버나잇 carry = 새 행동 → cross-day 일일한도/HALT 커버 검증(설계 §7).
- enforce(Phase C)만 보호영역 행동 변경 → 운영자 승인.
- **운영자 확인 필수 파라미터 무단 변경 금지**(슬리피지·protective hold).

---

## 6. 왜 이 순서인가 (직언)
- **Phase A부터인 이유:** ladder 완화는 양날(러너 이득 vs 반전 반납)이고 MFE는 5m 추정이라, *라이브 shadow를 켜기 전에* "어떤 활성이 net Δ>0인가"를 **오프라인 무위험으로 먼저 거른다.** Phase A에서 net Δ>0 셀이 없으면 — leak은 실재하나 *capturable하지 않다*는 뜻이고, 거기서 끝낸다(라이브 안 건드림).
- **엔진 재사용:** tail_capture가 이미 ladder 앞 삽입 + shadow + forward 재구성 + 시장 분리 + 약세 게이트까지 설계돼 있어, **새 A/B 인프라가 거의 필요 없다.** 이 설계는 "활성 임계를 ladder-leak 구간(MFE~3%)으로 낮춰도 (a)>(b)인가"라는 **단일 파라미터 질문**으로 좁힌다.
- **KR 제외:** KR claude_price는 net 양수(보존), KR은 꼬리 없음(설계) → US 한정.

---

## 7. 한 줄 요약
PROFIT_LADDER leak(MFE~3% 러너를 +0.48%에 끊음)은 **이미 빌드된 tail_capture 엔진의 사각지대**(활성 4% > leak MFE 3%)다. → **Phase A(오프라인 활성 스윕 2.5~4%, 무위험)로 "어떤 활성이 net Δ>0 & 반전손실 0인가"를 먼저 거르고**, 통과 셀이 있으면 기존 shadow 인프라로 Phase B(라이브 페어 로깅+forward 재구성, 약세 포함), 그 다음 US-한정 enforce. ladder는 전 과정 보호영역·fallback 유지.
