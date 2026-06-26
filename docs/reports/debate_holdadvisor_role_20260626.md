# 토론 리포트 — Hold Advisor 청산 개입의 역할 (손절·익절·매도) 2026-06-26

> 운영자 주제: hold advisor의 역할 — 손절/매도/익절 결정의 문제점·장단점·개선점.
> 명제(운영자 선택): **"Hold advisor가 자동청산(손절·익절·매도)에 개입해 Claude가 한 번 더 HOLD/SELL을 판단하는 것이 기계적 즉시 실행보다 net이 낫다."** PRO=개입 가치 / CON=개입 잡음·해로움.
> 방식: PRO/CON 입론 + 사회자 직접 sqlite 재검증. READ-ONLY, 코드/주문/config 무변경.

배경(역할): `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true` → Path A 자동매도(loss_cap·stop·trail)가 즉시 실행 안 되고 Claude 판단 거침. `AUTO_SELL_REVIEW_FORCE_SELL_LOSS_PCT=2.5` → −2.5%에서 강제매도. + profit_guard 익절우선 prior. **즉 hold advisor가 세 청산 전부에 개입.**

---

## 1. 사회자 직접 재검증 (decisions.db, 6월 measured)

### 피벗 1 — US loss_cap 실현 분포: review가 −2% stop을 끄는가?
US CLOSED_LOSS_CAP N=57 gross 분포:
| 구간 | 건수 | 의미 |
|---|---:|---|
| > −2.0% (cap 이전 청산) | 10 | review가 안 끌고 일찍 청산 |
| **−2.0 ~ −2.5% (review HOLD 창)** | **35 (61%)** | **−2% cap을 −2.5% 강제층까지 끎** |
| ~ −2.5% (강제층 군집) | 6 | force-sell에서 청산 |
| ≤ −2.6% (overshoot/갭) | 6 | 강제층 넘김(갭, review 무관) |

→ **61%가 −2.0~−2.5% 창** = review가 −2% 즉시청산을 막고 끌었다는 CON 주장 **부분 확인.** 단 평균 −2.28%, drift는 **cap 대비 ~0.28%p**(작음). −2% 손실 자체는 불가피(MFE≈0, 진입 즉시 직하강한 나쁜 진입). 10건은 −2% 전에 청산 → **항상 끄는 건 아님.**

### 피벗 2 — 익절/매도 counterfactual: 개입의 SELL이 보유를 이기는가?
`hold_advisor_exit_outcome`(6/15~6/22, profit_guard 익절 SELL의 매도실현 vs 보유가정 forward) paired N=8:
| | 매도 실현(realized) | 보유 forward(hold_fwd) | 매도 우세 |
|---|---:|---:|---:|
| 전체 | **+3.55%** | +0.32% | 5/8 |
| US (N=3) | −0.08% | **−2.64%** | 2/3 |
| KR (N=5) | +5.72% | +2.10% | 3/5 |

→ **개입의 SELL 판단이 보유를 이김.** US는 매도가 −2.64% 손실을 회피. + CLOSED_CLAUDE_SELL net +1.10%(N=5 win100%), CLOSED_CLAUDE_PRICE_TARGET +3.68%(N=8 win100%). **PRO 확정(익절/매도 전선).**

---

## 2. 명제별 판정 (세 전선, 양다리 금지)

### 익절 / 매도 개입 → **net 기여 (찬성)**
- counterfactual에서 매도 실현 +3.55% > 보유 +0.32%(N=8). 개입의 SELL이 보유를 이김.
- CLAUDE_SELL/PRICE_TARGET net 양수. **CON 자진 반증** — "승자 조기절단"은 라이브로 반증됨.
- (단 표본 N=8·6월·생존편향 일부, profit_pullback +2.2%/88%는 5월 confound·mfe-NULL 오염이라 절대값 신뢰 불가 — 방향만.)

### 손절(KR) 개입 → **중립~양**
- KR Claude-path는 loss_cap 포함해도 net 양수(+1.15%, sum +17.3%, N=15). **CON 자백.** KR에선 개입이 손실원이 아님.

### 손절(US loss_cap) 개입 → **약하게 유해 (CON), 단 효과 작고 A1이 이미 처리**
- review가 회복을 0건 잡음(win 0%, MFE≈0), 61%가 −2.0~−2.5% 창 = cap 넘겨 끎.
- **단:** drift ~0.28%p/건(작음), −2% 자체는 불가피(나쁜 진입). **+ A1(ready=0 차단)이 loss_cap의 78%를 이미 제거** — US loss_cap 잔존은 ready=1 N=16뿐.
- per-trade HOLD 플래그가 없어 drift가 "review 탓"인지 "갭/슬리피지 탓"인지 단정 불가(CON 자백).

### 종합 판정 — **명제 "개입 전체가 net 기여" → 대체로 찬성, US 손절 한 곳만 예외**
개입의 net 가치는 **익절·매도·KR손절에서 확인**되고, 유일한 약점은 **US loss_cap review가 −2%를 ~−2.3%로 끄는 것**인데 그 효과는 작고(0.28%p) A1이 이미 78%를 잘랐다.

---

## 3. 합의 / 불일치 / 미검증

**합의 (양측)**
1. 익절/매도 개입은 net 기여 — 매도가 보유를 이김, CLAUDE_SELL/TARGET 양수.
2. US loss_cap이 유일 약점 — review가 회복 0건, cap 넘겨 끎.
3. KR Claude-path는 loss_cap 포함도 양수 — 시장 분리 필수.

**불일치**
- US loss_cap drift(−2.0→−2.3)가 "review HOLD 탓"인지 "갭/슬리피지 탓"인지 — per-trade HOLD 플래그 없어 단정 불가.

**미검증**
- 개입 net+의 라이브 verdict — paired N=8·약 10일·6월 단일국면. #4 forward-validation(`HOLD_ADVISOR_EXIT_LESSON_ENABLED=true`, 방금 켬) verdict ~2026-07-21 대기.

---

## 4. 개선 방향 (방향만, 코드는 승인 후)

| # | 방향 | 형태 | 근거 |
|---|---|---|---|
| 1 | **익절/매도 개입 보존** (CLAUDE_SELL·PRICE_TARGET·profit_guard) | 유지 | 매도가 보유 이김(+3.55 vs +0.32), 건드리면 양의 표면 죽임 |
| 2 | **US loss_cap는 A1 후 재측정** | 측정 | A1이 78% 제거 → 잔존(ready=1 N=16) drift가 유의미한지 먼저 본다 |
| 3 | 잔존 drift 유의미 시 **US loss_cap만 즉시청산 강화** (AUTO_SELL_REVIEW_FORCE_SELL_LOSS_PCT US 2.5→2.0, 또는 US loss_cap review 제외) | shadow A/B | review가 −2%를 −2.3%로 끎. **KR은 양수라 제외 — 시장 분리** |
| 4 | **#4 forward-validation verdict(~7/21) 기다려 익절 prior 라이브 검증** | 관측(진행 중) | 개입 net+ 라이브 표본 N=8뿐 |

**안 할 것:** 익절/매도 개입 끄기(반증됨), KR loss_cap review 제거(KR 양수), CLAUDE_REVIEW 전체 off(익절·매도 가치 죽임). 보호 파라미터(FORCE_SELL_LOSS_PCT) 무단 변경 금지 — 잔존 측정 후 운영자 결정.

---

## 5. 한 줄 결론

**Hold advisor 개입은 익절·매도·KR손절에서 net을 기여한다(매도가 보유를 +3.55 vs +0.32로 이김) — "승자 조기절단"은 라이브로 반증됐다.** 유일한 약점은 US loss_cap review가 −2% stop을 ~−2.3%로 끄는 것인데, 효과는 작고(0.28%p) A1이 그 진입의 78%를 이미 잘랐다. → **개입은 보존하고, US loss_cap만 A1 후 재측정해 잔존 drift가 남으면 US-한정 즉시청산 강화(shadow A/B), KR은 손대지 마라.**
