# 라이브 파이프라인 흐름 관찰 — 2026-06-17 미국장 (운영자 취침 중 자율관찰)

목적: 봇 PID 39056이 만드는 데이터 흐름 전 단계를 read-only로 ~30분 간격 추적, 각 단계가 "적절했나"까지 도출. 마감 KST 05:00까지. 봇/프로세스 미기동.

추적 단계(운영자 지시 2026-06-17): ① 스크리너/sub_screener 후보군 적절성(과열추격/거래량/품질) ② Claude 후보선정(trade_ready 무엇을·근거 전략/score·watch vs ready 적절성) ③ smart skip(reuse/fresh/waiting·재사용 판단 적절성) ④ hold advisor(보유종목 votes bull/bear/neutral conf+reason·HOLD/SELL·`logs/hold_advisor/decisions_2026-06-17.jsonl`) ⑤ 매수/매도/청산(사유 CLOSED_*·고가주 사이징) ⑥ 에러/차단.

연계 맥락: [[project_entry_discrimination_20260616]] — selection 음수alpha / 측정인프라 공백 / hold advisor HOLD 11% / 고가주 사이징 누수. 사후 DB에 안 남는 걸 실시간으로 보충 관찰.

---

## 회차 1 — 00:52 (베이스라인)

- **스크리너/sub_screener**: 00:42 triage_applied 추가 HIMS/MRVL/RGTI/HOOD/QBTS(strong=True). 00:52 new_plan_a=0 trigger=False.
- **selection freshness**: fresh=12 reuse=0 waiting=1 — **smart skip 재사용 0**(매 사이클 fresh selection, Claude 호출 절감 안 됨).
- **사이클**: 00:52 포지션 2개, cash 4,229,438 KRW.
- **매수/매도/청산**:
  - 00:15 **AAL 청산 CLOSED_PROFIT_LADDER −0.19%(−451원)** ← 어젯밤 유일익(+0.85%)이 본전 반납. capture 누수 실시간 목격.
  - 00:12 TSLA 1주 진입, 00:33 **HLNE $86.56×3주 진입**(고가주 소량, WDC $713 패턴 반복).
- **현재 보유**: TSLA(1), HLNE(3).
- **에러/차단**: 없음.
- **도출(잠정)**: ① profit_ladder가 소액익을 본전에서 끊는 패턴 또 관측(AAL). ② 고가주 소량진입 반복(HLNE) — 사이징 누수 후보. ③ smart skip reuse=0 = 비용절감 미작동, 추적 필요.

---
## 회차 2 — 01:26

- **① 스크리너**: sub_screener 활발 — 후보풀 15→**26개**로 확대(01:24 triage_added T/HOOD/QBTS/SOFI/PL). 단 `capacity exhausted` + `dedupe_suppressed` 반복 = 공급 과잉인데 처리용량/중복으로 막힘.
- **② selection**: 01:00·01:04 watch 15개씩인데 **trade_ready=[] 지속**(신규 진입후보 0). 모드는 **MODERATE_BULL size65%(강세)**. → 강세장인데 selection이 아무것도 안 올림.
- **③ smart skip**: fresh=13→14 **reuse=0 지속**, waiting 3. 비용절감 미작동(매 사이클 fresh).
- **⑤ 매수/매도**: 00:33 HLNE 이후 **신규거래 0**(~53분 정적). 보유 TSLA(1)·HLNE(3) 유지.
- **⑥ 차단**: 🔑 **`[PathB 미 정오 진입 보류] US utc_hour=16 block_hour=16 tickers=CARG`** — CARG가 진입 대기 중인데 **US 정오(ET12시) 진입보류 게이트**에 막힘.
- **도출(2회차)**: 어젯밤 "강세장 미진입" 통점의 **실시간 재현**. 강세(MODERATE_BULL)인데 진입 정체. **병목 2개**: (a) selection capacity exhausted/dedupe로 후보 과잉이 trade_ready로 안 이어짐 (b) **US 정오 진입보류 게이트**(CARG). + smart skip reuse=0 비용누수. **직전대비**: 후보풀 15→26 확대, 거래는 여전히 정적, 정오게이트 등장.
- **다음 추적**: 정오 게이트(utc_hour 16) 풀린 뒤 CARG 진입되나, trade_ready 0이 언제 깨지나, 모드 변화.

---
## 회차 3 — 01:58

- **① 스크리너**: 🔑 `candidate quality rows=77 {WATCH:15, SCREENER_ONLY:30, NOT_IN_PROMPT:32}` + `candidate_health {OBSERVE:10, STABLE_READY:1, WATCH_WEAK:4}` — **77개 후보 중 STABLE_READY 1개뿐.** capacity exhausted(soft_block) 지속.
- **② selection**: trade_ready=[] **약 2시간째 0**(01:52·01:57). 모드 MODERATE_BULL 65% 유지.
- **③ smart skip**: fresh=17→18 **reuse=0 지속**, waiting 1.
- **⑤ 매수/매도**: 00:33 HLNE 이후 **신규거래 0**(~1h25m 정적). 보유 TSLA(1)·HLNE(3).
- **⑥ 차단**: 정오 진입보류 게이트 대상 **CARG→NVDA로 이동**(utc_hour=16 여전, ET13시면 풀릴 듯). 진입 대기 종목이 정오대 계속 보류.
- **도출(3회차)**: **trade_ready=0의 진짜 원인이 게이트가 아니라 후보 품질일 수 있음** — STABLE_READY 1/77. 즉 *후보를 2배 넓혔지만(funnel 확대) 질적으로 살 만한 건 1개*. 깔때기 분석(6/8경 후보 200→480 2배, 매수 안 늘어 전환율 8%→4%)과 정확히 일치: **양적 확대 ≠ 질적 진입.** 정오게이트(NVDA)는 부차적 요인. smart skip reuse=0 비용누수 지속.
- **직전대비**: 정오게이트 CARG→NVDA, trade_ready 0 지속(2h), 거래 정적 지속, candidate_health STABLE_READY 1/77 신규포착.
- **다음 추적**: ④ hold advisor 보유종목(TSLA/HLNE) 판단 확인(이번 회차 미관측), 정오게이트(ET13시) 풀린 뒤 NVDA 진입되나, STABLE_READY 늘어 trade_ready 깨지나.

---
## [특별 분석 — 결정타] v2≈v3 진입품질 동일·capture가 갈림 (02:25)

실측 MFE(mfe_backfill_yf ↔ v2_decisions.session_date join, US):
| 구간 | n | avgMFE | 못오름% | avgPnl | **capture(pnl/mfe)** |
|---|---|---|---|---|---|
| 5월(강세) | 113 | +3.19% | 15% | +0.88% | 0.28 |
| **v2 6월초** | 10 | **+2.03%** | 30% | +1.70% | **0.84** |
| 전환기 | 19 | +0.78% | 68% | −0.75% | −0.97 |
| **v3(6/8~)** | 29 | **+2.06%** | 17% | −0.45% | **−0.22** |

**결론(오늘 밤 전체 종합):**
- **진입 품질 v2≈v3** — MFE +2.03 vs +2.06, 못오름% v2 30/v3 17. **후보 2배 확대가 진입품질에 0 기여 = 후보확대 입증 실패.**
- **capture는 정반대** — v2 0.84(기회 84% 회수) vs v3 −0.22(기회를 마이너스로 날림). **같은 종목 사고, v2는 멀티데이로 챙기고(오버나잇36%·4.8h) v3는 단타로 날림(1.6h).**
- ∴ 진입(selection/후보확대)이 아니라 **capture(보유/청산)가 v2/v3 수익 차이의 본질.**

**처방(제안·운영자 승인 필요·미실행):**
1. **후보확대 폐기/축소** — MFE 동일이므로 후보 cap↓ + sub_screener rescreen 트리거 둔감화(watch_floor·new_plan_a 임계↑) = 경량화 복구 + 진입품질 유지(어차피 동일). 무의미 비용 제거.
2. **capture 복원이 수익 레버** — v3 단타화(1.6h)를 v2 멀티데이(4.8h·오버나잇36%) 수준으로. = 어젯밤 capture·hold advisor·보유기간 트랙. **수익은 구조(v2/v3)가 아니라 여기.**
3. **전면 롤백은 과함** — v3 인프라가 진입품질을 v2 수준 유지. 버릴 건 후보확대+단타화지 다단계 전체가 아님.
- **함정 경고**: "v2 롤백=수익복구"는 환상. v2도 같은 종목(MFE 동일) 샀고 수익은 멀티데이 capture로 챙긴 것. 롤백해도 단타+약세장이면 v3와 동일 손실.

표본 가드: v2_6월초 n=10(작음), capture는 장세영향(전환기 −0.97 바닥). MFE 동일은 견고.

---

## [특별 분석] v2 vs v3 구조 진단 — 롤백 vs 개선 (운영자 요청, 02:11)

구조: **v2(~6/2): 스크리너→Claude→매수**(직선 1회판단). **v3(6/8~): 스크리너→1차판단(selection)→2차판단(sub_screener 재screening)→매수 + smart skip 루프**(다단계+재선정 루프).

### 구간별 데이터 (US PathB, event_store)
| 구간 | 청산 net | win% | 보유 중앙 | 오버나잇% | 사용량 일당 |
|---|---|---|---|---|---|
| 5월(강세) | +0.68% | 50% | 2.9h | 25% | 258 |
| **v2 6월초** | **+1.95%** | 55% | **4.8h** | **36%** | 188 |
| 전환기6/3~7 | −0.76% | 20% | 3.5h | 20% | 180 |
| **v3(6/8~)** | **−0.39%** | 30% | **1.6h** | 22% | **205** |

### 경량화 실패 인과 (2h12m 라이브 정량, 00:00~02:11)
- sub_screener 91회 / **rescreen 48회(2.7분마다)** / run_cycle 25회 / **smart skip reuse=0 / 신규진입 0**.
- fresh source 전부 rescreen류, 트리거=`new_plan_a_above_watch_floor:74~80`.
- **인과**: 2차판단(sub_screener)이 새 후보를 watch_floor(60) 넘게 계속 발굴 → 1차 selection 재트리거(rescreen 폭주) → 후보풀·signature 매번 변동 → smart skip 무력(reuse=0) → **경량화 역효과(v3 205 > v2 188).** 후보 품질도 희석(STABLE_READY 1/77).

### 판정 (롤백 vs 개선)
- **사용량/경량화**: v3 명백히 망가짐. 단 **구조적 결함 아니라 rescreen 트리거 과민 = 튜닝 가능**(watch_floor·new_plan_a 임계 둔감화로 복구). → **롤백보다 개선.**
- **멀티데이 단축**(v2 중앙4.8h/오버나잇36% → v3 1.6h/22%): v3+약세장 손절 교란. 분리엔 반사실(측정인프라) 필요.
- **net 손실(−0.39%)**: **1차 원인 장세**(v3 후기 −0.39 > 전환기 −0.76 회복, v3 무르익을수록 개선=장세 설명력). v2 수익도 강세장빨 가능(5월 +0.68/6월초 +1.95). **v2 롤백해도 약세장 손실은 그대로.**
- **결론**: 롤백/개선 논쟁은 *사용량*을 가르지 *수익*을 못 가름. 경량화는 v3 튜닝으로 개선 가능(즉시 롤백 과함). 수익 레버는 v2/v3 구조가 아니라 **진입품질+장세적응**([[project_entry_discrimination_20260616]] 진입변별·OR_pullback·측정인프라).
- **권고**: ① rescreen 트리거 둔감화 → 경량화 복구 → 멀티데이 유지 관찰 ② 수익복구를 v3↔v2에서 기대 말 것. **모두 운영자 승인 필요, 미실행.**

---
## 회차 4 — 마감 후 (08:42)

- 04:58 마지막 사이클 **MILD_BULL size60%**, positions 0. 현재 보유 **[]** (장마감 전 전량 정리).
- 새벽 진입분 청산: HLNE 3@86.7, CARG 11@29.9, NFLX 4@77.85, **TSLA CLOSED_CLAUDE_PRICE_PRE_CLOSE −1.09%(−6,700)**.
- **④ hold advisor 관측(드디어)**: HLNE 이익중 HOLD(+0.23)→SELL(+0.168, 반납), TSLA 손실중 HOLD(conf0.52 약함)→늦은 SELL(conf0.72→0.82, −0.84). **votes bull/bear/neutral 전부 동일 confidence(0.72/0.72/0.72)=analyst 분산 0.** 어젯밤 패턴 재현: 이익중 HOLD 반납(HLNE) + 손실중 늦은 손절(TSLA).
- ② trade_ready 마감까지 **계속 0** / ③ reuse **0 지속**(fresh=31) / ① STABLE_READY 계속 1~3.

---

## ★ 밤 전체 종합 도출 (2026-06-17 미국장, 00:00~05:00)

### 파이프라인 단계별 발견
1. **스크리너**: 후보 과잉(15→26→80) but 품질 병목 — STABLE_READY 1/77~3/80. 양적확대≠질적.
2. **selection**: 강세장(MODERATE_BULL)인데 trade_ready **수시간 0**. 후보 넘치는데 진입후보 없음.
3. **smart skip**: reuse **밤새 0** — 경량화 완전 실패. sub_screener rescreen 폭주(2h12m에 48회/진입0)가 signature 매번 바꿔 무력화.
4. **hold advisor**: votes 분산 0(3 analyst 동일 conf), 이익중 HOLD 반납(HLNE)·손실중 늦은손절(TSLA). HOLD11%/SELL39% 약점 재현.
5. **매수/매도**: 고가주 소량진입 반복(HLNE $86×3, TSLA $405×1), TSLA pre_close −1.09%. 보유 다 단타.
6. **차단**: 정오게이트(utc16, CARG→NVDA)가 강세장 진입 일부 보류.

### v2 vs v3 핵심 결론 (특별분석 2건)
- **진입품질 v2≈v3** (MFE +2.03 vs +2.06) — 후보 2배 확대가 진입품질에 0 기여. **후보확대 입증 실패.**
- **capture 정반대** (v2 0.84 vs v3 −0.22) — 같은 종목 사고 v2는 멀티데이로 챙기고(4.8h·오버나잇36%) v3는 단타로 날림(1.6h).
- **경량화 역효과** (v3 205 > v2 188 일당호출) — rescreen 과민이 원인(튜닝 가능).
- **net 손실 1차는 장세** (v3후기 −0.39 > 전환기 −0.76 회복). v2 롤백해도 약세장 손실 그대로.

### 🔑 02:00~05:00 거래 + capture 누수 코드 메커니즘 (루프 공백 보완)
진입: CARG 02:02 $29.8×11, NFLX 02:02 $79.04×4. 청산(03:00경 일괄):
| 종목 | 청산 | 손익 | 사유 | 보유 |
|---|---|---|---|---|
| HLNE | 02:59 | −0.08% | intraday_review_sell | 2.4h |
| CARG | 03:00 | +0.12% | intraday_review_sell | 1h |
| NFLX | 03:00 | −1.36%(−6.4k) | intraday_review_sell | 1h |
| TSLA | 03:02 | −1.09%(−6.7k) | CLAUDE_PRICE_PRE_CLOSE | 2.8h |
- **메커니즘 발견**: 03:00 일괄청산 전부 hold advisor `triage=HOLD final=SELL reason=hold_confidence_below_threshold` — **HOLD하려는데 confidence 임계 미달이라 SELL로 강등.** CARG/HLNE 본전 청산(capture 0), NFLX 1h 단타 −1.36%.
- **= v3 capture −0.22의 코드 정체**: hold advisor가 확신 없으면 멀티데이 유지를 *못 함* → 같은 종목(MFE동일)을 v2처럼 못 챙기고 본전/소액손실 청산. intraday_review가 진입 1h만에 트리거.
- 오늘 전체 성적(강세장인데 수익 0): 어제밤 QBTS/POET/WDC(−24k)/SOFI/AAL + 새벽 TSLA/HLNE/CARG/NFLX(−6.4k) = 거의 손실·본전.

### 개선방안 1순위 정밀화 (capture 복원)
- **`hold_confidence_below_threshold` 게이트 완화** — 손실 아닌 포지션은 conf 낮아도 HOLD 유지(현재 "확신없으면 무조건 SELL"이 멀티데이 원천차단). **prior보다 이 임계가 상위 결정권.**
- **`intraday_review` 빈도/쿨다운 조정** — 진입 1h만에 본전청산 패턴 억제.
- (모두 보호영역·hold advisor 핵심 → 운영자 승인 필요, 미실행.)

### 한 줄 결론
> **v2든 v3든 같은 품질 종목을 산다(MFE 동일). 수익은 "산 다음"=capture(멀티데이 유지)에서 갈린다. v3 후보확대·경량화는 실패했지만, 그게 수익의 원인은 아니다 — capture가 본질이고, 그 코드 정체는 hold advisor의 hold_confidence_below_threshold SELL 강등이다.**

(개선방안은 메모리 [[project_entry_discrimination_20260616]] 및 운영자 보고 참조. 모든 처방 코드/config/보호영역 → 운영자 승인 필요, 미실행.)

