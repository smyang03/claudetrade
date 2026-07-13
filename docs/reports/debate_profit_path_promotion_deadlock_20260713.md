# 토론 판정 — profit_path/profit_evidence 레인: 기다리면 되는가

**날짜**: 2026-07-13 · **사회자**: Claude (read-only) · **명제**: "profit_path/profit_evidence 레인은 배선 수정(b2e7520) 외 **추가 투자 없이 forward 축적만 기다리면 된다.**"

**판정: 명제 기각 (반대).** 단 기각 사유는 양측 누구의 예상과도 다르다 — **승격 게이트가 수학적으로 통과 불가능**하기 때문이다. 기다림의 문제는 "느리다"가 아니라 "**끝이 없다**"이다.

---

## 1. 합의된 사실 (양측 + 사회자 검증 일치)

| 사실 | 근거 |
|---|---|
| **배선 수정(b2e7520)은 실제로 컸다** | PRO가 실제 스크리너 후보 **31,569행**(KR 10,951/US 20,618, 2026-06-25~)으로 재현: **OOD 비율 KR 100%→1.5%, US 100%→0.1%**. 결측 25개 중 22개→6.9(KR)/7.1(US). 예측이 상수(0.5369)에서 고유값 74개(−4.05~+0.54) 분포로 전환. `post_open_features_json`은 표본 100% 존재. |
| **오늘 13건의 병리는 "모델이 나쁘다"가 아니라 "입력이 비었다"** | `_ood` 발화조건 = `observed < 5`. 배선 전 시장레벨 dict는 numeric 15개 중 관측 0~1개 → 무조건 OOD. |
| **섹터플레이 OOD abstain은 승격 원장을 오염시키지 않는다** | 5종목이 `candidate_counterfactual_paths`에 오늘 0행(003670·006400은 전기간 0행) → 조인 불가. 모니터 실행 실측: `prediction_n=13, matched_n=0, unmatched_matured_n=13`. AUC·ECE·LCB 어느 통계에도 안 들어간다. |
| **shadow는 매수를 막지 못한다** | `allowed = passed or mode != "enforce"` (gate:360). 오늘 차단된 주문 0건. |

→ **선택지 A(섹터플레이를 원장에서 제외)는 no-op이다.** 조인이 이미 배제한다. 제가 처음 제기한 "승격 시계 오염" 우려는 **틀렸다.**

---

## 2. ★판정을 가른 사실 — 승격 순환 데드락 (사회자 직접 검증)

게이트가 통과하려면 **두 조건을 동시에** 만족해야 한다:
- `runtime/profit_evidence_gate.py:304` → `p_calibrated >= PROFIT_EVIDENCE_MIN_PROB(0.55)`
- `runtime/profit_evidence_gate.py:349-352` → `validation_net_lcb_pct > 0`

그런데:
- `tools/train_profit_path_shadow.py:106` → `IsotonicRegression(out_of_bounds="clip")` → **보정확률 출력이 학습 y의 최댓값으로 클립**된다.
- 실측 상한: **KR 0.4917 / US 0.2975** (`state/models/profit_path_{KR,US}.json`).
- `train_profit_path_shadow.py:117` → `selection = (prob_val >= 0.55) & ...` → **0건 선택** → `validation_selected_n = 0`, `validation_net_lcb_pct = null`.

**순환 구조:**
```
net_lcb > 0  ←요구─  selected_n > 0  ←요구─  p >= 0.55  ←불가─  p 상한 0.49(KR)/0.30(US)
```
→ `promotion_eligible_backtest = False`가 **영구 고정**. 오늘 13건의 사유가 이를 그대로 찍었다: `['model_not_promoted', 'probability_below_hurdle', 'ood_or_ood_missing', 'validation_net_lcb_not_positive']` — 13/13 동일.

**forward를 20세션·60매칭 모아도 이 임계는 1mm도 움직이지 않는다.** 명제가 기각되는 결정적 이유다.

### 왜 상한이 0.49/0.30인가 — 우리가 이미 아는 병과 같은 병
모델의 라벨은 "**sell_target에 스탑 전에 도달**"이다. 그런데 그 목표가 도달 가능 MFE 대비 **2.3배 과대**([[target-calibration-lever-20260711]])라 base rate가 낮게 갇힌다:

| | 타깃 도달 base rate | 보정확률 p50 / p90 / **max** | 임계 |
|---|---|---|---|
| KR | 31.8% (n=3,501) | 0.285 / 0.492 / **0.492** | 0.55 ❌ |
| US | 22.7% (n=5,389) | 0.219 / 0.297 / **0.297** | 0.55 ❌ |

**임계 0.55는 애초에 천장 위에 놓인 숫자다.** 모델이 무능해서가 아니라, 물어본 질문("과대목표에 도달하겠나")의 답이 원래 드물어서다. 신호가 0인 것도 아니다 — 상위 10% 확률군의 실제 도달률은 **KR 49.2%(전체 31.8% 대비 1.55배), US 29.7%(22.7% 대비 1.31배)**, US AUC 0.638.

---

## 3. 불일치 → 사회자 재정

| 쟁점 | PRO | CON | 판정 |
|---|---|---|---|
| 표본이 쌓이는가 | 배선 후 would_block 100% → 티커 게이트 호출 100% 적재. 20세션(≈4주) 바닥이 구속 | KR 매수 트리거 6/24 이후 13세션 중 1건 → 60매칭에 수개월~수년 | **CON 우세, 단 무의미**. 쌓여도 §2 데드락으로 승격 불가. 페이스 논쟁은 승격이 가능해진 뒤에나 의미가 있다 |
| 섹터플레이 피처 산출(선택지 B) | 무용·유해: outcome 원장에 행이 없어 matched 기여 0, unmatched만 부풀림. `raw_score_current`·버킷은 스크리너 산출물이라 합성 시 **학습분포 위조** | 우선순위는 원장 확장이 먼저 | **양측 일치 = 선택지 B 기각** |
| 라이브 경로 재편집 | 검증표본 0인데 실주문 차단기를 또 건드리는 건 손해보는 거래 | (반대 안 함) | **동의. 게이트 호출부 확대 금지** |

---

## 4. ⚠️ 지뢰 (이번 토론의 최대 수확)

**`PROFIT_EVIDENCE_GATE_MODE=enforce`로 켜면 매수가 100% 차단된다.**
현 아티팩트의 정책은 검증셋 4,149행 중 **0건**을 선택한다(`validation_selected_n=0`). enforce 시 전 종목 abstain → **A1 `REQUIRE_TRADE_READY` 매수셧다운 사고([[a1-require-trade-ready-buy-shutdown-20260701]])의 재현 구조**다. 지금은 shadow라 무해하지만, **이 아티팩트로 enforce 전환은 금지**한다.

---

## 5. 처방 — 수익 방향 (기다림 대신 무엇을)

### P0 (무위험·즉시, 코드 아님)
- **승격 시계를 "정지"로 선언.** 현 상태는 "축적 중"이 아니라 "구조적 미승격". `matched_n=0`을 진행률로 읽지 말 것.
- **enforce 전환 금지**를 명시 (§4).

### P1 (핵심 처방 — 우리 최강 레버와 합류)
**라벨을 "도달 가능한 tier"로 재정의하고 재학습한다.** "sell_target 도달"이 아니라 "**조기익절 tier(US ~2.3% / KR ~3.6%) 도달**"을 예측하게 한다.

base rate 실측(closed 표본):
| | 현행 라벨(sell_target) | 재정의 라벨(조기 tier) |
|---|---|---|
| US | 22.7% | **51.6%** (MFE≥2.3%, 130/252) |
| KR | 31.8% | **62.9%** (MFE≥3.6%, 39/62) |

→ base rate가 2배 이상 오르면 **보정확률 상한이 0.55를 넘어서고**, `selected_n>0` → `net_lcb` 계산 가능 → **데드락이 풀린다.** 그리고 이 라벨은 우리가 검증한 최강 레버(목표 캘리브레이션·조기익절 tier: US net −0.18→+0.48, KR −0.22→+2.15)와 **같은 것을 예측한다.** 게이트가 살아나면 "어떤 종목이 조기 tier에 닿을까"를 고르게 되고, 이건 곧 그 레버의 종목 선별기가 된다.

*caveat: base rate는 `mfe_pct`(forward-window 의심) 기반이다. 원천 일봉 held-window로 재검증 시 early-tier 효과는 성립 확인됨([[target-calibration-lever-20260711]])이나, 이 base rate 숫자 자체는 낙관 가능. 재학습 전 clean MFE로 라벨 재산출 필요.*

### P2 (위생, 재학습과 함께)
- 트레이너에 **`validation_selected_n == 0`이면 아티팩트 배포 fail-fast** (지금은 죽은 모델이 조용히 배포됐다).
- 확률 임계를 **절대값 0.55 대신 달성가능 분포 기준**(상위 분위 등)으로 재정의 — 또는 `expected_net LCB > 0`을 주 관문으로.
- 예측 이벤트에 `candidate_key`/`known_at`를 실어 ±10분 휴리스틱 조인 제거(CON: 현 조인 유실 ~17%).

### 기각
- **선택지 A(섹터플레이 원장 제외)**: no-op. 조인이 이미 배제.
- **선택지 B(섹터플레이 온디맨드 피처)**: 무용(outcome 행 없음) + 유해(학습분포 위조). 양측 일치 기각.
- **게이트 호출부 확대**: 조인 불가 표본만 늘려 착시 증폭.

---

## 6. 미검증 (다음 관측으로 결판)
재시작 후 3~5세션이면 코드 투자 없이 확인된다:
1. `PROFIT_EVIDENCE_SHADOW`에 **sector_play 이외 strategy**가 등장하는가 (= 게이트가 스크리너 후보 경로에 닿는가). 오늘까지 0건.
2. 스크리너 후보 예측의 **`ood=False` 비율**이 PRO의 재현(98.5%/99.9%)과 일치하는가.
3. `matched_n`이 0에서 떨어지는가.

**단 이 셋이 전부 참이어도 §2 데드락 때문에 승격은 불가하다.** 관측은 배선 수정의 검증이지, 명제의 구제가 아니다.

관련: [[target-calibration-lever-20260711]], [[a1-require-trade-ready-buy-shutdown-20260701]], [[goal-profit-first-mindset]]
