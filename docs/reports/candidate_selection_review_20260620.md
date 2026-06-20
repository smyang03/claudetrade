# 후보군 선정 방식 분석 — 취약점 + 개선방안 (2026-06-20)

> 운영자 지시: 현재 후보군 선정 방식 분석 → 취약점 발견 → 개선방안. 어떤 정보가 더 필요한지, 어떻게 "매수로 이어질" 후보를 찾을지.
> 방법: read-only (event_store funnel + ticker_selection_log forward 성과 + 피처 스키마).

---

## 0. 핵심 결론 (직언)

- **현재 후보 선정은 "이미 오른 종목을 담는 추격형 스크리너"다.** day_gainers 후보의 3일 forward가 **음수(-0.19%)** — 구조적으로 평균회귀에 당한다. 이것이 selection 5회 무엣지의 물리적 원인.
- **funnel 최대 누수 = selection↔execution 단절.** trade_ready의 **79%가 NO_SIGNAL로 주문 안 됨**(504/636). Claude가 "사라"한 종목 대부분이 전략 게이트에서 죽는다.
- **후보 피처가 가격/거래량에 편중, 펀더멘탈·뉴스·기술지표 거의 없음.** 변별에 쓸 정보 자체가 빈약.
- ⚠️ **단, 개선이 곧 수익을 보장하지 않는다.** selection 무엣지는 5회 측정으로 확정됐고, 아래 개선안은 "검증 후 채택" 전제다. 막연한 낙관 금지.

---

## 1. 후보 선정 파이프라인 (현재 구조)

```
raw 스크리너(most_actives/day_gainers/volume_rank/day_losers)
  → dynamic universe (KR40/US40)
  → Claude selection prompt (KR28/US24)
  → trade_ready / watch 분류
  → [전략 신호 게이트] ← 여기서 79% 탈락
  → PathB 가격플랜 / Path A 신호
  → 주문
```

### Funnel 단계별 수량 (event_store)
| 단계 | 수량 | 전환율 |
|---|---|---|
| CLAUDE_TRADE_READY | 757 | — |
| → TRADE_READY_NO_SUBMIT | 636 | **84% 미제출** |
| CLAUDE_PRICE_PLAN_CREATED | 633 | |
| ORDER_SENT | 545 | |
| FILLED | 561 | |
| CLOSED | 304 | |

---

## 2. 취약점 (데이터 근거)

### 🔴 V1. 추격형 소스 편향 — 후보 소스별 forward 성과
| 소스 | n | avg_fwd3d | avg_runup | 승률 |
|---|---|---|---|---|
| most_actives | 3,580 | **+0.83%** | 6.8% | 52% |
| day_gainers | 2,358 | **-0.19%** | 6.82% | 53% |
| volume_rank | 1,282 | +0.23% | **14.39%** | 45% |
| day_losers | 151 | **-2.32%** | 5.53% | 29% |

- **day_gainers(상승률 상위)가 음수** = 이미 오른 걸 담아 평균회귀에 당함(추격 함정).
- **day_losers(-2.32%)** = 명백한 손실 소스. 떨어지는 칼 잡기.
- **volume_rank**: runup 14.4%로 기회는 가장 큰데 승률 45%·forward +0.23 = **기회는 있으나 못 먹음(capture/타이밍 문제)**.
- → most_actives만 간신히 양수. **소스 믹스가 추격에 치우쳐 selection이 구조적으로 못 이긴다.**

### 🔴 V2. selection ↔ execution 단절 (funnel 최대 누수)
TRADE_READY_NO_SUBMIT 636건 사유:
| 사유 | 건수 |
|---|---|
| **NO_SIGNAL** | **504 (79%)** |
| PATHB_HIGH_PRICE_BUDGET_BLOCK | 70 |
| CLAUDE_PRICE_INVALID | 48 |
| PATHB_RISK_OFF_CAP | 12 |

- Claude가 trade_ready로 고른 종목의 **79%가 전략 신호 미발동으로 매수 안 됨.** selection 판단과 execution 게이트가 다른 기준 → "고른 게 안 사진다."
- 이게 운영자가 반복 느낀 "왜 매수가 없냐"의 funnel 근거.

### 🟠 V3. 고가주 차단 (affordability)
NO_SUBMIT 70 + SAFETY_BLOCKED HIGH_PRICE 98 = **168건 누적.** selection 단계에 affordability 필터 부재 → 못 살 종목이 슬롯 점유.

### 🟠 V4. 피처 빈약 — 변별 정보 부족
| 카테고리 | 보유 피처 |
|---|---|
| 가격/모멘텀 | 6개 (change/gap/runup/drawdown) |
| 거래량 | 2개 (vol_ratio, liquidity) |
| 기술적 | **1개 (atr만)** |
| 펀더멘탈 | **0개** (sector뿐, EPS/PE/시총 없음) |
| 뉴스/이벤트 | **0개** |

- RSI/VWAP/MA 등 기술지표, 펀더멘탈, 뉴스 catalyst 전무. **Claude가 받는 정보가 "얼마 올랐나/거래량"에 국한** → 변별 근거 자체가 약함.

---

## 3. 개선방안 (우선순위 + 검증 전제)

### 개선 1순위 — V2 (selection↔execution 단절) **가장 높은 ROI**
- **문제**: 잘 골라도 79%가 NO_SIGNAL로 죽음. 후보 품질을 높여도 이 단절이 있으면 매수로 안 이어짐.
- **방안**: NO_SIGNAL 504건을 분해 — (a) 전략 신호 조건이 selection 기준과 왜 어긋나나 (b) Claude trade_ready 시점과 신호 발동 시점의 갭(타이밍). **selection이 신호 발동 가능성을 미리 보게** 하거나, trade_ready 기준에 신호 근접도를 반영.
- **정량 기준**: NO_SIGNAL 비율 79% → 목표 50% 이하. 미달 시 후보 품질 개선은 무의미.

### 개선 2순위 — V1 (소스 믹스 재조정)
- **방안**: day_losers(-2.32%) 소스 **제거/축소**, day_gainers는 "이미 N% 이상 오른 건 제외"하는 과열 필터(단, US는 모멘텀이 엣지라 KR/US 분리 — 6/17 시뮬에서 US 과열차단=자살골 확인됨). **most_actives 비중 확대.**
- ⚠️ **주의**: US는 과열 추격이 엣지라 day_gainers 차단이 역효과(검증됨). **KR에만 과열 필터, US는 most_actives 비중만.**
- **정량 기준**: 소스별 forward를 분기마다 재측정, 음수 소스만 축소.

### 개선 3순위 — V4 (피처 보강) **단 신중**
- **추가 후보**: RSI/VWAP 거리(과열도), 시총/유동성(소형주 위험), 섹터 강도. PEAD는 입력 피처로만(정책상 전략화 금지).
- ⚠️ **over-build 경계**: 피처를 늘려도 selection이 무엣지면 효과 없을 수 있음. **shadow로 "이 피처가 forward 변별력을 주나" 먼저 측정 후** 프롬프트 투입.
- **정량 기준**: 신규 피처가 forward AUC/상관을 유의하게 올릴 때만 채택.

### 개선 4순위 — V3 (affordability)
- selection 단계에서 1주 가격 > max_entry(US 100만/KR 50만) 종목을 watch로 강등(후보 풀에서 제외 X, 진입 후보에서만). 슬롯을 affordable에 양보.

---

## 4. "어떤 정보가 더 필요한가" (운영자 질문 직답)

| 정보 | 현재 | 필요성 | 비고 |
|---|---|---|---|
| **신호 발동 근접도** | 없음 | 🔴 최우선 | V2 단절 해결의 핵심 — selection이 "신호로 이어질지" 미리 알아야 |
| 과열도 (RSI/VWAP/전일대비) | atr만 | 🟠 높음 | 추격함정(V1) 방어 |
| 시총/유동성 등급 | liquidity_bucket만 | 🟠 중 | 소형주 fade-trap(KR) 식별 |
| 펀더멘탈 (EPS/실적) | 없음 | 🟡 낮 | PEAD 입력으로만, 전략화 금지 |
| 뉴스 catalyst | 없음 | 🟡 낮 | 노이즈 위험, shadow 검증 후 |

**핵심**: "더 많은 정보"보다 **"신호로 이어질 정보"(V2)**가 먼저다. 매수로 안 이어지는 후보를 아무리 잘 골라도 funnel에서 죽는다.

---

## 5. "어떻게 매수로 이어질 후보를 찾나" (운영자 질문 직답)

1. **selection 기준과 execution 신호 기준을 정렬** — 지금은 Claude가 좋다고 한 것과 전략이 사는 것이 따로 논다(NO_SIGNAL 79%). 이 둘을 한 기준으로 묶는 게 "매수로 이어짐"의 핵심.
2. **추격이 아닌 진입 타이밍** — runup 큰 volume_rank(14.4%)를 못 먹는 건 꼭지에서 담아서다. "오르기 전/눌림"에서 잡는 신호 근접도가 필요.
3. **affordable 우선** — 못 살 종목이 슬롯 먹지 않게.

---

## 6. 직언 — 이 분석의 한계

- selection 무엣지는 5회 측정으로 확정. **위 개선안은 "취약점을 줄이는" 것이지 "엣지를 만드는" 보장이 아니다.**
- 가장 확실한 개선은 V2(funnel 단절) — 이건 "더 잘 고르기"가 아니라 "고른 걸 사지게 하기"라 selection 엣지와 독립적으로 효과 가능.
- V1/V4(소스·피처)는 **shadow 검증 후 채택** 전제. 검증 없이 프롬프트에 정보 추가하면 노이즈만 늘 위험(over-build).
- KR/US 분리 필수 — US는 과열 추격이 엣지(6/17 확인), KR은 fade-trap. 같은 처방 금지.

### 다음 실행 후보 (택1)
- **A (권장)**: NO_SIGNAL 504건 분해 → selection↔execution 단절 정밀 진단 (ROI 최고, read-only 가능)
- B: 소스별 forward를 월별·국면별로 분해해 V1 처방 정밀화
- C: 신규 피처(RSI/과열도)의 forward 변별력 shadow 측정

---

## 7. [완료] NO_SIGNAL 504건 정밀 분해 — V2 단절의 정체

각 trade_ready 종목에 봇은 3전략(ORP/변동성돌파/momentum)을 시도하고, 모두 거부 시 NO_SIGNAL. 504건(US 483·KR 21) 전수 분해:

### 7-1. 변동성돌파 = 504건 전부 "비활성" 🔴
- `US_VOLATILITY_BREAKOUT_LIVE_ENABLED` 미설정(=false) → **100% OFF.** 후보가 VB 신호감이어도 무조건 사장.

### 7-2. OR pullback 거부 사유 (타이밍 문제)
| 사유 | 건수 | 의미 |
|---|---|---|
| **orp_entry_window_expired** | **321 (64%)** | **진입 창 이미 만료** |
| orp_not_formed | 114 | 눌림 미형성 |
| orp_range_too_high | 37 | 변동성 과대 |
| 기타(shallow/deep/vol_low) | 32 | 패턴 미달 |

### 7-3. momentum = 시간 대기
- `momentum_wait_window(39m<50m)` 등 — 개장 후 대기시간 미충족 보류.

### 7-4. 진단 결론 — V2 단절 = **타이밍 불일치 + VB 전면 OFF**
1. **타이밍 단절 (ORP expired 321)**: Claude가 trade_ready를 확정한 시점엔 전략 진입 창이 이미 지났다. **selection과 entry가 다른 시계로 돈다** — 가장 큰 단일 원인.
2. **VB OFF (504)**: 변동성돌파 후보가 100% 사장(전략 미활성).
3. **momentum 대기**: 시간 게이트(early_entry로 일부 단축됨).

### 7-5. V2 개선방안 (구체)
| 처방 | 대상 | 효과 | 리스크 |
|---|---|---|---|
| **selection 시점을 entry 창에 맞춰 앞당기기** | ORP expired 321 | 타이밍 단절 해소 | selection 빈도↑(토큰), 조기판단 품질 |
| **VB shadow 검증 후 활성** | VB 504 | 사장 후보 회생 | 성과 미확인(반드시 shadow 먼저) |
| ORP 진입창 연장 | expired 일부 | 더 오래 진입 기회 | 늦은 진입 = 추격 위험 |

⚠️ **단, 이 처방들은 "더 많이 사게" 만든다.** selection 무엣지(5회)·KR fade-trap을 감안하면, **"더 사는 게 net+인지"부터 검증** 필요. ORP expired 321건의 forward를 보면 "놓친 게 손해였나/이득이었나" 측정 가능 → 그게 처방 채택의 선결 조건. (오늘 KR 폭락장에서 봇 0매수가 방어였던 것처럼, 미진입이 항상 손해는 아님.)

**가장 안전한 다음 스텝**: ORP expired 321건의 forward 성과 측정 → 진짜 놓친 기회인지 확인 후 타이밍 처방 결정. (read-only)

---

## 8. [완료·반전] ORP expired 321건 forward 측정 — "타이밍 단절"은 사실 "타이밍 방어"였다

ORP `orp_entry_window_expired`로 놓친 321건(320건 selection_log 조인)의 실제 forward:

| 지표 | 값 | 해석 |
|---|---|---|
| **forward_1d** | **-3.20%** | 놓친 종목 다음날 평균 하락 |
| **forward_3d** | **-9.52%** | 3일 내 평균 -9.5% |
| **forward_3d 양수비율** | **20%** | 80%가 하락 |
| max_runup_3d | +1.64% | 잠깐 오를 여지도 적음 |

### 🔴 결론 — V2 "타이밍 처방"은 자살골이다 (처방 철회)
- ORP 진입창 만료로 놓친 321건은 **사면 평균 -9.5% 손해볼 종목들이었다.** "진입 창 만료"는 단절이 아니라 **추격 진입을 막은 방어장치**.
- 즉 §7-4에서 "타이밍 단절 = 최대 원인"이라 한 진단은 **forward로 반증됨.** selection을 앞당기거나 ORP 창을 연장하면 이 -9.5% 종목들을 사게 됨 = **자살골.**
- 이는 6/17 시뮬(US 과열차단 = 양수net 삭제)과 **동일 함정**. 그리고 오늘 KR 폭락장 봇 0매수 방어와 같은 교훈: **미진입이 손해가 아니다. NO_SIGNAL은 대부분 옳은 방어다.**

### 최종 종합 — selection 개선의 진짜 답
1. **funnel 79% NO_SIGNAL은 "버그/단절"이 아니라 대부분 "옳은 방어"다.** 후보 품질(Claude 판단)과 진입 게이트(전략)가 어긋나는 게 아니라, **전략 게이트가 Claude의 추격 후보를 거르는 안전망으로 작동**한다.
2. **개선 여지가 있는 건 selection을 "더 사게" 만드는 쪽이 아니다.** 그건 -9.5% 종목을 사게 한다. 오히려 **most_actives 비중↑/day_losers 제거(V1)** 같은 소스 정제가 더 안전.
3. **VB OFF(504건)만이 검증 가치 있는 후보** — 단 VB 성과 미확인이라 반드시 shadow 먼저. 나머지 타이밍 처방은 **기각.**
4. **재확인된 큰 그림**: selection은 무엣지일 뿐 아니라, "더 잘/더 많이 고르기" 처방이 대부분 자살골이다. 레버는 selection이 아니라 **capture(청산)** 라는 기존 결론을 6번째로 강화.
