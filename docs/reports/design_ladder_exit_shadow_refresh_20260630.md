# 설계 갱신 — LADDER 출구 개선 shadow (2026-06-30)

> 운영자 지시: "LADDER 출구 개선 shadow 설계안 정리." 설계만, 구현·config 변경은 승인 후.
> 선행: `design_profit_ladder_shadow_ab_20260626.md`(Phase A/B/C 골격). 이 문서는 **전수 데이터 갱신 + 현재 파이프라인 위치 확정 + 즉시 실행할 Phase A 명세**.

---

## 0. 한 줄

LADDER leak(MFE~3% 러너를 +0.40%에 끊음)은 **이미 enforce/shadow로 도는 두 메커니즘(ladder A/B·tail_capture)이 전부 활성 4%라 못 덮는 MFE 2~4% 사각지대**다. → **Phase A(오프라인 활성 스윕, 무위험, 지금 실행 가능)로 "어떤 활성이 분봉 양방향 net Δ>0 & 반전손실 0인가"를 먼저 거른다.** 통과 셀 없으면 거기서 끝(라이브 무접촉).

---

## 1. 근거 데이터 갱신 (전수 n=207, 2026-06-30)

`v2_learning_performance` + `mfe_backfill_yf` COALESCE, US closed 207/252(82% 커버).

| close_reason | n | MFE% | net% | capture | 해석 |
|---|---|---|---|---|---|
| **PROFIT_LADDER** | 30 | +3.04 | +0.40 | **0.13** | 개선 대상 #1 (peak의 13%만 회수) |
| CLAUDE_PRICE_STOP | 18 | +2.74 | -0.11 | -0.04 | 보조 leak(별도) |
| CLAUDE_PRICE_PRE_CLOSE | 19 | +4.43 | +2.09 | 0.47 | 마감청산 |
| **CLAUDE_PRICE_TARGET** | 16 | +5.04 | +5.27 | **1.05** | **작동 출구 = 벤치마크** |

- 6/26 근거(N=32, MFE+3.02→+0.48, capture 0%)와 **거의 동일** → leak은 robust, 단일월 confound 아님.
- TARGET capture 1.05 = "타깃에 닿으면 다 잡는다"가 천장 방증. LADDER는 그 천장 대비 2.6pp 새는 중.

## 2. 현재 파이프라인 위치 (코드 확인)

| 메커니즘 | 상태 | 활성 | give |
|---|---|---|---|
| ladder peak-trail A/B | **US enforce** / KR off | `LADDER_AB_ACT_PCT=4.0` | 2.0 |
| tail_capture 엔진 | **shadow** | `TAIL_CAPTURE_ACTIVATION_PCT=4` | US 3 / KR 1.5 |

→ **둘 다 활성 4%.** LADDER leak 트레이드는 MFE 평균 3.04%(4% 미만)라 **두 메커니즘 어디에도 안 잡히고** ladder 기본 청산이 +0.40%에 끊는다. enforce 중인 A/B가 있어도 leak이 남는 이유가 이것 — A/B 활성 임계 위 구간만 보호되기 때문.

## 3. 명제 (단일 파라미터)

**활성 임계를 LADDER-leak 구간(MFE 2.5~3.5%)으로 낮추면, 러너 capture가 늘어 net이 개선된다 — 단 +X% 찍고 반전하는 트레이드의 추가 반납(좌측 누수)이 그 이득을 넘지 않는 한.**

양날(반드시 분리 측정):
- (a) +3% 가서 더 가는 러너 → 더 잡음 (이득)
- (b) +3% 찍고 반전 → ladder가 잠갔을 이익을 더 토함 (손실)
- **A/B의 전부 = 활성을 *얼마나* 낮춰야 (a)>(b)인가.** 원 설계 경고: 활성 너무 낮음(=진입부터 trail) = −97 대참사.

## 4. Phase A — 오프라인 활성 스윕 (결정 게이트·무위험·지금 실행)

라이브 손대기 전, 과거 LADDER 트레이드를 활성별로 분봉 재생해 (a)>(b) 임계가 *존재하는지* 먼저 본다.

- **도구(기존):** `tools/ladder_capture_sweep.py` / `tools/peak_floor_counterfactual.py` / `tools/tail_capture_sim.py`. 신규 코드 불요.
- **대상:** close_reason=CLOSED_PROFIT_LADDER 전수(n=30, 백필 포함) + 비교군 동기간 전체 PathB 청산.
- **스윕:** `activation ∈ {2.5, 3.0, 3.5, 4.0}` × `give ∈ {2, 3}%` × 시장(US/KR 분리).
- **⚠️ 판정 방법론(메모리 `stop-capture-peaktrail-verdict` 교훈, 필수):** forward(보유 사후) 아님 — **분봉 양방향 replay.** 각 트레이드를 5m 경로로 재생해 "그 활성/give로 trail했다면" 실현가를, **러너(더 탐)와 반전(더 반납) 양쪽 다** 카운트. STOP peak-trail이 정확히 이 양방향에서 give Δ≤0(본전토 회수 ↔ 러너 조기청산 상쇄)으로 기각됐음 — ladder도 같은 바를 넘어야 함.
- **분해(셀별):** capture 이득(러너) − 반전 손실(반납) = **net Δ/trade.** + 좌측 누수 증가 여부.
- **결정 게이트:** 모든 (act, give)가 net Δ≤0 이거나 좌측 누수↑ → **Phase B 안 감, 종료.** net Δ>0 & 좌측 누수 0 셀 있으면 그 값으로 Phase B.
- **함정 통제:** MFE/경로는 yfinance 5m **추정 상단**(체결가 아님) → 재생에 보수적 슬리피지. 절대값 과신 금지, 셀 *간* 상대비교. 생존편향 분리(러너·반전 둘 다).

## 5. Phase B — 라이브 shadow 페어 로깅 (Phase A net Δ>0 셀 있을 때만·무위험)

- **메커니즘:** 기존 tail_capture shadow 훅. 실청산 무접촉.
- **변경(shadow 한정):** ladder-leak 구간 덮는 **shadow 전용 낮은 활성 arm**(`TAIL_CAPTURE_SHADOW_ACTIVATION_PCT`=Phase A 승자값). enforce-path 활성(4)·ladder A/B 불변. 엔진이 그 arm으로 "소유했을" 결정을 ladder 실결정과 **페어 JSONL**.
- **forward 재구성:** carry/HOLD 라이브 포지션을 다음날 yfinance로 "엔진정책이면 얼마" vs actual(오버나잇 보강).
- **표본 바:** ladder-leak 페어 **N≥20(US)**, **약세장 N세션 포함**(약세 통과 전 enforce 금지). US ~주 40청산 → 약 2~3주.
- **kill 바:** net Δ≤0(시장별) **또는** 반전손실 증가 **또는** 약세 미통과 → enforce 금지, 활성 4 복귀.

## 6. Phase C — enforce (게이트 통과 후만·운영자 승인)

- **US 먼저**(꼬리 있음), 약세 shadow 통과 후. **KR off 유지**(KR claude_price net 양수 보존·꼬리 없음).
- 활성 낮춘 값으로 enforce = 엔진이 leak 구간 러너 소유. ladder는 fallback 유지.
- 충돌 우선순위·loss_cap 위임 정합 명문화.

## 7. 안전 계약

- profit_ladder = 수익 보호영역. Phase A(오프라인)·B(shadow)는 ladder 청산 **무접촉 + fallback 유지.**
- 하방은 loss_cap/hard_stop 위임(엔진 하드스톱 X). 슬리피지캡·일일한도·HALT·protective hold 무수정.
- enforce(Phase C)만 보호영역 변경 → 운영자 승인. config 두 소스(.env.live + v2_start_config.json) 동기화.

## 8. 즉시 다음 행동 (운영자 선택)

**Phase A 스윕 실행** — 무위험·신규코드 0·라이브 무접촉. 결과 = "net Δ>0 & 반전손실 0 셀이 존재하나?" 단일 판정. 없으면 leak은 실재하나 capturable 아님 → 종료. 있으면 그 (act, give)로 Phase B 설계 확정.
→ 실행하려면 운영자 "Phase A 돌려" 지시. (분봉 양방향 replay 도구가 현재 추정상단/슬리피지 모델을 어떻게 다루는지 먼저 검수 후 실행.)
