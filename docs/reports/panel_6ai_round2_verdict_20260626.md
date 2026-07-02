# 6인 AI 패널 2라운드 — 반박·결판 (2026-06-26)

> 1라운드 낙관론(분리/target알파/capture레버)을 2라운드 반박이 데이터로 검증. 사회자 종합.

---

## 1. 2라운드가 깬 것 (낙관론 붕괴)

**① 혁신가 자진 철회 — "target 알파 분리"는 생존편향.**
- ex-ante로 target 닿는 셋업 식별: BULL regime target율 16.8%(나머지 4.4%)·claude_price 11% — *틸트*일 뿐 "콕 집는 신호" 없음. claude_price+BULL net **+0.06%(노이즈 breakeven)** = 수익원 아님.
- capture 1.11 자체가 **22% MFE 커버리지**(TARGET capture는 27건 중 mfe 있는 **2건**으로 계산) — 로버스트 X.
- → 분리론은 "흑자 떼어 키우기"가 아니라 **"ex-ante 출혈버킷(비-claude_price 기계전략 −0.87%·비-BULL) 차단으로 −0.28%→breakeven"** 으로 축소. **수익원 분리가 아니라 손실원 차단.**

**② 회의론자 강화 — "winner 더 태우기=capture↑"는 양날, 칼날은 아래.**
- 전수 net 음수(census US −0.124%·KR −0.844%, 부분표본 아님) → active>passive 미입증, 표본한계는 *양수 주장(PRO)*을 친다.
- **반사실(hold_advisor N=8): 더 보유 시 −3.22pp 악화**(5/8 더 나쁨, KR006220 +6.68→−21.16). giveback median capture **0.12**(winner 74%가 MFE 절반+ 반납). WEAK_MFE −22.6%.
- 결판선: passive 대비 paired net **US ≥+0.5% @N≥260 / KR ≥+0.5% @N≥390**. 현재 US −0.12(N=212)·KR −0.84(N=60) = 검정력 미달 + 부호 틀림.

**③ 리스크·PM A2 결판 — ×1.5는 알파가 아니라 maxDD를 키운다.**
- A2 모집단(ready=1 claude_price/PathB) N=189 net **−0.26% = breakeven 책**. TARGET 11%(+99.6) vs **LOSS_CAP 22%(−110.6)** — loss_cap이 target 알파를 혼자 다 상쇄. ex-ante 동일 모집단(close_reason은 ex-post).
- ×1.5 드로다운: 6/15~16 **19연패 −22.9%p → −34.4%p**. maxDD **−42%p → −63%p**. June claude_price −42%p → −63%p. 기대수익 0인데 변동성·DD만 1.5배 = 음(陰)시너지.

**④ 개발자 증거등급 — 패널 근거 절반이 C급.**
| 수치 | 등급 | 이유 |
|---|---|---|
| capture 1.11(target) | **C** | N=16·MFE 87% 추정·생존편향. "A급으로 착각한 대표 C급" |
| ready=1 +0.36 vs −1.97 | C(크기)/B(방향) | 실거래 N=14/19 |
| loss_cap −2.76% / −24%p 회수 | B(실현)/**C(귀속)** | 슬리피지 인스트루먼트 0 = 추론 |
| W24-25 −59%p | **C** | 재현 안 됨·FX basis 혼합(비교불가) |
- MFE 커버리지 21.5%, 조인 MATCHED **1.4%**, FX 6월 US만, net measured 34.7%.

---

## 2. 결판 (사회자)

### ★ A2(ready=1 ×1.5) — **shadow 강등 + 재설계 (오늘 한 일에 대한 패널 직격)**
2라운드 전원이 데이터로 확인: A2 모집단은 **순수 알파가 아니라 net −0.26% breakeven 책**이고, ×1.5는 maxDD를 −42→−63%p로 키운다. ex-ante로 target과 loss_cap을 구분 못 해 알파만 못 키운다.
- **권고: A2를 shadow로 내리고 "target·MFE 실현 확인 후 add-only"로 재설계** — 진입 ×1.0, 그 트레이드가 (i)target 부근 MFE 도달 + (ii)loss_cap/stop 미발생을 *실현으로 확인* 후에만 add. 그러면 ×1.5가 TARGET 버킷에만 ex-post 결합. (운영자 승인 사항.)

### A1(ready=0 차단) — **유지 (2라운드도 +EV 일치)**
ex-ante 식별 가능한 출혈버킷 차단. 분리론이 축소돼도 이건 산다.

### "강화" 자체의 지위 — **미입증, 측정 선결**
- net census 음수 + 결판선(N≥260-390 @+0.5%) 한참 멀음 → **active>passive 미입증**. 그 전 "수익성 강화"는 매몰비용 합리화(회의론자).
- 패널 근거 절반 C급 → **측정 배관이 모든 결정의 선결**.

---

## 3. 방향성 (2라운드 최종)

| 우선 | 행동 | 근거·등급 |
|---|---|---|
| 1 | **측정 배관 복구** ① selection↔canonical 조인(MATCHED 1.4%→AMBIGUOUS 205 회수) ② **슬리피지 인스트루먼트**(ORDER_SENT.price↔FILLED.fill_price, *원자료 이미 존재, 조인만*=싸다) ③ MFE native ④ FX 백필 | 개발자: 이거 전엔 어느 결정도 A급 안 됨 |
| 2 | **A2 shadow 강등 + add-only 재설계** | 리스크·PM: ×1.5는 maxDD만 키움 |
| 3 | **출혈버킷 ex-ante 차단** A1 유지 + 비-claude_price 기계전략(−0.87%)·비-BULL·KR 축소 | 혁신가(축소형): −0.28%→breakeven |
| 4 | **passive 진지 검토** | PM·회의론자: active>passive 미입증, census 음수 |
| — | **하지마:** target만 트레이드(생존편향), winner 더 태우기(giveback 칼날 아래), ×1.5 무차별, capture 1.11을 A급 근거로 | 2라운드 반증 |

**결판 측정조건(통과 전 강화=매몰비용):** 6월형 하락/횡보 국면 net>0 & PF>1 (N≥80) + passive paired net US≥+0.5%@N≥260·KR≥+0.5%@N≥390 + MFE capture A급(native 전구간).

---

## 4. 한 줄 종합
2라운드는 1라운드 낙관(분리/target알파/capture)을 **데이터로 해체**했다 — target 알파는 생존편향(C급·N16·MFE 87%추정), winner 더 태우기는 giveback 양날(−3.22pp), 그리고 **오늘 넣은 A2(×1.5)는 알파가 아니라 net −0.26% breakeven 책에 건 레버라 maxDD를 −42→−63%p로 키운다.** 남는 건 화려한 강화가 아니라 **냉정한 셋:** ①측정 배관 복구(슬리피지 조인은 원자료 있어 싸다) ②A2 shadow강등+add-only 재설계 ③A1 유지+출혈버킷 차단(−0.28%→breakeven) ④passive 진지검토. active가 passive를 비용후 이긴 증거는 아직 데이터에 없고, 그 입증선은 N≥260-390 @+0.5%로 멀다 — 그 전 "강화"는 희망이다.
