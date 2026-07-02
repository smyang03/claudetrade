# 토론 리포트 — 스크리너→후보군 선정 개선점 (2026-06-26)

> 운영자 주제: 스크리너 → 후보군 선정 방식의 개선점 토론.
> 방식: 사회자(Claude) 주재 PRO/CON 입장 배정 + **사회자 직접 sqlite 재검증**. 전 과정 READ-ONLY, 코드/주문/config 무변경. 결론·적용은 운영자.
> 운영자 결정: 명제 1·2·3 모두 토론.

---

## 0. 명제 (PRO=funnel이 새는 레버 / CON=funnel은 레버 아님)

| # | 명제 | PRO | CON |
|---|---|---|---|
| 1 | 스크리너→후보군 선정이 net 병목이다(개선 시 net 추가) | 찬성 | 반대(하류가 레버) |
| 2 | 후보 풀을 넓히는 방향이 net에 유리 | 찬성(넓혀라) | 반대(넓히면 잡음·거래불가) |
| 3 | Claude selection이 스크리너 순위 대비 추가 선별력 보탬 | 찬성 | 반대(과잉 레이어) |

---

## 1. 사회자 직접 재검증 (판정을 가른 피벗)

### 피벗 — PRO의 핵심 P1 주장 "ready=1 90% 미실행 = 누수"가 깨짐
PRO 입론의 최강 P1 근거는 "funnel이 자기 최고 등급(ready=1)을 90%+ 안 채운다"였다. 직접 분해(`ticker_selection_log`, distinct day-ticker):

| | ready=1 distinct | 체결 | **미체결 중 signal_fired=0** | signal=1인데 미체결 | blocked_reason 있음 |
|---|---:|---:|---:|---:|---:|
| KR | 97 | 20 (21%) | **73 / 77** | 4 | 4 |
| US | 208 | 15 (7%) | **180 / 193** | 13 | 13 |

→ 미실행 ready=1의 **압도적 다수가 signal_fired=0** = 전략 진입 신호 자체가 안 남(매수 구간 미도달·풀백 미발생). **차단된 누수가 아니다.** `blocked_reason`은 KR 4·US 13건만(order_size/qty_zero/exposure/cash 등 운영 실패). **trade_ready=1은 *후보 자격* 라벨이지 *진입 트리거*가 아니다** — 강제 체결 = 진입조건 없이 매수 = 더 나쁨.

### 회수 net 가치도 한계적 (decisions.db, net 백필 포함)
- KR claude_price(=ready 경로) net **+0.742%** (PF 1.84, N=21) — 살아있음.
- US claude_price net **−0.094%** (PF 0.91, N=168) — breakeven(loss_cap 꼬리 포함).
→ ready=1을 더 회수해도 net 기여는 breakeven(US)~소폭 양(KR). **net 레버가 아니다.**

### CON의 하류 레버 주장 재확인 (직전 토론과 일치)
US 6월 measured-net: full book −58.2%(N=91) → **excl LOSS_CAP PF 0.99 breakeven**(N=70), LOSS_CAP 21건이 −58.0% 전부. net 손익은 청산 좌측꼬리가 지배.

---

## 2. 명제별 판정 (양다리 금지)

### 명제 1 — 선정이 net 병목? → **반대 (CON)**
- net 손실 질량은 선정이 아니라 **loss_cap 좌측꼬리**(하류). 선정 ex-tail은 breakeven.
- PRO의 "ready=1 미실행 누수"는 검증서 **signal_fired=0**(진입조건 미충족)으로 판명 — 고칠 누수가 아니고, 회수해도 net breakeven.
- **판정: 스크리너→후보군 선정은 net 병목이 아니다.** (steelman: KR ready=1 signal_fired=0 일부가 entry 기준 과엄격일 순 있으나 — 이는 *진입 기준* 문제지 선정 문제 아님, net 효과도 소폭.)

### 명제 2 — 넓혀라? → **반대 (넓히기 불리)**
- 일일 캡 시뮬 PF 단조: per_market `cap_1 1.40 > cap_2 0.71 > cap_3 0.69`. 더 담아 더 거래할수록 PF 하락.
- soft 확장의 "최종가" 매력은 **진입 기준에서 증발**: KR soft_b_confirm60 final PF 8.42 → entry_60m_to_final **0.42**.
- 누락 winner는 거래불가 지배: 큐레이트 표 KR 23건 중 19건(83%) low_liquidity/limit_up_chase. (단 risk_tags 98% null이라 전수 카운트는 불가 — CON 자백.)
- PRO 자진 최약점(라이브 확장 net 데이터 0).
- **판정: 무차별 풀 확장은 net에 불리. 넓히기 기각.**

### 명제 3 — Claude selection 변별력? → **찬성, 단 게이트에 한해**
- trade_ready 게이트는 forward 변별력 양 시장 우위(KR ready=1 +2.54% vs ready=0 −4.19%, US +0.85% vs +0.17%). **CON 자진 반증** — "selection=0"은 데이터가 안 받침.
- **단, conviction 점수(entry_priority_score)는 죽음**: traded 분위별 KR HIGH −4.23%(역상관)·US 역U. 점수로 자본 몰면 손실 증폭.
- **판정: 선별력은 trade_ready 이분 게이트에 있다(보존). entry_priority_score 점수-사이징은 과잉 레이어(도려낼 것).**

---

## 3. 합의 / 불일치 / 미검증

**합의 (양측 + 데이터)**
1. trade_ready 게이트는 forward 변별력 있음 — 보존(A1으로 이미 ready=0 차단 enforce).
2. 풀 확장·점수 기반 자본집중은 PF 하락 — 하지 말 것.
3. net 레버는 하류(loss_cap 좌측꼬리). 선정은 net 병목 아님.
4. 누락 winner는 거래불가가 지배 — 회수 가능분 제한적.

**불일치 (남음)**
- 선정 ex-tail breakeven(US PF 0.99)이 "비용에 묻힌 약한 양의 엣지"인가 "무엣지"인가 → 표본 1국면(6월 measured)이라 미결. (4·5월은 백필 근사.)

**미검증**
- KR ready=1 signal_fired=0 73건 중 *진입 기준 과엄격으로 놓친 net-양수 기회*가 있는지 — KR claude_price가 net 양수(+0.74%)라 유일하게 측정 가치 있는 갈래. 단 이는 선정이 아니라 **진입 기준** 영역.
- 워스트 cohort 회피(KR unclassified gap_pullback −0.24 N=489 등)가 net 개선하는지 — known_at cohort 게이트 shadow 미측정.

---

## 4. 개선 방향 (데이터가 허락하는 것 — 방향만, 코드는 승인 후)

> 핵심: **스크리너→후보군 선정 자체는 net 레버가 아니다.** 게이트는 작동하니 보존하고, 넓히기·점수사이징은 하지 말고, 진짜 net은 하류(loss_cap)다. 선정 쪽에 남는 *측정 가치* 있는 갈래만 shadow로.

| # | 방향 | 형태 | 근거 | 우선 |
|---|---|---|---|---|
| 1 | trade_ready 게이트 보존 (ready=0 차단 유지) | enforce(완료, A1) | 양 시장 forward 변별력 | — |
| 2 | **풀 확장 트랙 닫기** | 규율 | 캡 PF 단조하락·entry-basis 붕괴 | 즉시 |
| 3 | **entry_priority_score 점수-사이징 금지** | 규율/도려냄 | 분위 역상관(KR)·역U(US) | 즉시 |
| 4 | 워스트 cohort 회피 게이트 (selection 품질 sharpen) | shadow | full_review Cohort Reliability(KR/US worst 다수 음수) | 중 |
| 5 | KR ready=1 signal_fired=0 진입기준 점검 (entry-side, 선정 아님) | shadow 측정 | KR claude_price net +0.74% 양수 경로 | 중 |

**안 할 것:** 무차별 풀 확장, 점수 기반 자본집중, 누락 winner(low-liq/limit-up) 무리한 회수, 선정 게이트를 "과잉"으로 끄기(양의 표면 죽임).

---

## 5. 한 줄 결론

**스크리너→후보군 선정은 "고칠 누수"가 아니라 "이미 작동하는 게이트"다.** ready=1 미실행은 차단 누수가 아니라 signal_fired=0(진입조건 미충족)이고, 회수해도 net breakeven이며, 넓히기·점수사이징은 PF를 떨어뜨린다 — 즉 **선정은 net 병목이 아니다(P1·P2 반대).** 살아있는 건 trade_ready 이분 게이트의 변별력(P3 찬성·보존)뿐이고, 선정 쪽 개선의 유일한 측정 가치는 워스트 cohort 회피(shadow)와 KR ready=1 진입기준 점검(entry-side)이다. net을 움직이려면 여기가 아니라 하류(loss_cap 좌측꼬리)다.
