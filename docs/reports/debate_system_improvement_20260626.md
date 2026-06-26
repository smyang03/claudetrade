# 시스템 개선 방향 토론 리포트 (2026-06-26)

> 운영자 주제: 전체 구성·실적 분석 → 개선점·개선방향, 과적합/욕심 기능 도려내기, 핵심 기능(후보선정·hold advisor) 거취(놔둘 것인가/개선), 프롬프트·데이터 개선, 수익 추가, 항목별 재검토, 개선 맞으면 코드 설계, 최종 리포트.
> 방식: 사회자(Claude) 주재 PRO(개선파)/CON(미니멀파) 입장 배정 + 강제 반박 + **사회자 직접 sqlite 재검증**. 전 과정 READ-ONLY. **코드/주문/config 무변경. 개선은 방향·설계 제시(운영자 승인 후 별도).**
> 운영자 결정: 명제 1·2·3 토론, **명제 4(KR 거취)는 제외 — KR은 계속 개선 대상**.

---

## 0. 명제 (PRO/CON 입장 배정)

| # | 명제 | PRO=개선파 | CON=미니멀파 |
|---|---|---|---|
| 1 | 후보선정에 프롬프트/데이터/룰 개선으로 끌어낼 실측 엣지가 있다(패시브 대비 net 추가 가능) | 찬성 | 반대 |
| 2 | 수익 개선의 유일 레버는 청산 규칙뿐, selection/hold 개선은 net 기여 0 | 반대 | 찬성 |
| 3 | 과적합/욕심 기능을 적극 도려내야 net·운영단순성 개선 | 신중(진짜 과적합만) | 찬성 |

---

## 1. 사회자 직접 재검증 (decisions.db `v2_learning_performance`, live closed)

토론자 양측 수치를 사회자가 직접 재집계해 **판정을 가르는 3개 피벗을 확정**했다.

### 피벗 A — net 측정은 6월 한 달뿐 (가장 중요)
| market | 4월 | 5월 | 6월 |
|---|---|---|---|
| US net_basis=measured | 0건 | **0건** | 86건 |
| US gross | N=21 | **N=100 (claude_price 76, PF 2.26, +79.2%)** | N=123 |
| KR net_basis=measured | 0건 | 0건 | 17건 |

→ **모든 net 판정(US PF 0.71·0.46, KR PF 2.11)은 6월 MILD_BEAR 단일국면 위에 섰다.** US 5월 gross PF 2.26(+79.2%, N=76)은 net으로 **한 번도 측정된 적이 없다.** "US net 무엣지"는 정확히는 "US 6월 net 음수"이고, 강세였던 5월 net은 측정된 적이 없어 알 수 없다. 양측 헤드라인 net 주장 전부가 표본 1국면.

### 피벗 B — net 손실 전부가 loss_cap 좌측꼬리 (selection 아님)
US 6월 measured-net:
| 구간 | N | win | mean | sum | PF |
|---|---:|---:|---:|---:|---:|
| **full book** | 86 | 27% | −0.561% | **−48.3%** | 0.46 |
| **excl CLOSED_LOSS_CAP** | 67 | 34% | +0.070% | **+4.7%** | **1.13** |
| CLOSED_LOSS_CAP only | 19 | 0% | −2.786% | **−52.9%** | 0.00 |

→ **US net 손실(−48.3%)은 사실상 loss_cap 버킷 1개(−52.9%)가 전부.** 그걸 빼면 나머지 selection은 breakeven(PF 1.13). **CON("좌측꼬리가 손실 질량")과 PRO("좌측꼬리 클리핑이 net 지배, selection 아님")가 같은 사실을 가리킨다.** 청산/리스크가 1순위 레버라는 데 양측·데이터 합의.

### 피벗 C — KR claude_price는 살아있다 (리포트 PF 0.74는 gross 혼입)
| 구간 | N | win | mean | PF |
|---|---:|---:|---:|---:|
| KR claude_price net (6월 measured) | 17 | 47% | +0.955% | **2.11** |
| KR measured-net excl loss_cap | 11 | 73% | +2.517% | 9.65 |
| KR gross 월별 | — | — | −1.40(4월)→−0.69(5월)→**+1.11(6월)** | 0.35→0.56→2.38 |

→ KR claude_price는 **net 양수로 확정**(full_profitability_review의 PF 0.74는 gross + broker_sync 혼입 그룹핑 차이). 같은 6월 국면에서 **KR net PF 2.11 vs US net PF 0.46** — gross 내러티브("US 좋고 KR 나쁨")가 net·6월에선 역전. KR 4→5→6월 단조 개선 방향은 반박 불가(단 6월 N=17, 상위 4건 의존, ~1.4σ로 단월 noise 경계).

### 피벗 D — Claude price-target 청산 net 양수 = 생존편향 + selection-time 산물 (양면)
6월 measured-net by close_reason: CLOSED_CLAUDE_PRICE_TARGET **N=13 net +57.8% win 100%**, CLAUDE_SELL N=5 +5.5% win 100% / 반대편 LOSS_CAP N=25 −64.4% win 0%.
→ CON: target은 "도달한 승자만 그 경로로 마감"되는 **생존편향**(맞음). PRO: target/stop은 진입 시 Claude가 가격플랜으로 박는 **selection-time 산물**(맞음). 둘 다 참 — 플랜 도달(+3.68%)/loss_cap 관통(−2.79%)의 net 6.5%p 갈림은 진입 thesis 적중 여부 자체이나, aggregate는 좌측꼬리가 지배.

---

## 2. 명제별 판정 (사회자, 양다리 금지)

### 명제 1 — selection 엣지 회복 가능? → **미결, 단 "회복 불가"는 기각**
- net-measured 표본이 6월 한 달뿐이라 "전반 selection 엣지 있음"은 **미검증**(증명 안 됨).
- 그러나 같은 6월에 KR claude_price net PF 2.11, US selection ex-loss_cap PF 1.13(breakeven) → **"무엣지라 회복 불가"도 데이터가 받치지 못한다(기각).**
- **판정: 미결.** selection을 손대 net을 추가하는 게 *증명*된 적 없지만 *불가능*도 아니다. 결판 조건 = §4 측정 백필.

### 명제 2 — 청산만 유일 레버? → **부분 찬성 (전반부 확정, 후반부 기각)**
- "청산 규칙이 net 1순위 레버" = **확정.** loss_cap 1버킷이 US net −48% 전부, 제거 시 +4.7%.
- "selection/hold 개선은 net 기여 0" = **기각.** KR claude_price net 양수, Claude price-target net +57.8%가 반례. CON 본인도 "selection 전부 무가치" 포기.
- **판정: 청산=최우선 레버 YES / "selection·hold 기여 0" NO.**

### 명제 3 — 과적합 적극 도려내기? → **선별적 YES**
| 분류 | 대상 | 처분 | 근거 | 양측 |
|---|---|---|---|---|
| 가짜 과적합(잘라낸다) | pre-cutoff LLM replay 5종 결론(historical_sim 등), hold prior −8.91%p 오염·체리픽 수치 | **분석 결론 채택 금지 / 수치 정직화** | 암기누수(arXiv 2504.14765), mfe NULL 오염기간 도출 | **합의** |
| 살아있다(보존) | KR claude_price, Claude price-target/sell, target_extension HOLD(N=20 +1.05%) | **보존·강화** | net 양수 검증 | **합의** |
| 애매(축소) | KR momentum(net 미측정 gross PF 0.29 N=11), ORP(N=6 승률0%), continuation(N=3) | **축소→shadow (즉시 끄기 아님)** | 전부 6월 0건 휴면 + N=3~11은 소표본 단죄 함정 | PRO 신중/CON 동의 |
- **판정: "적극 도려내기"는 가짜 과적합(replay·오염수치)에 한해 YES, 살아있는 cohort엔 NO, 휴면 전략은 축소.** 일괄 prune은 net 양수 경로(KR claude_price 등)까지 죽이므로 기각.

---

## 3. 합의 / 불일치 / 미검증 분리

**합의 (양측 + 데이터)**
1. 비용 후 전체 net은 (측정된 6월에 한해) US 음수·KR 양수. gross 헤드라인은 비용·국면에 취약.
2. **청산 좌측꼬리(loss_cap)가 net 손실 질량 — 1순위 레버.**
3. 가짜 과적합(pre-cutoff replay 결론, hold prior 오염수치)은 검증 불인정·정직화.
4. KR claude_price·Claude price-target은 살아있어 prune 금지.

**불일치 (남음)**
- US selection ex-tail breakeven(+4.7%)이 "약한 생존 엣지"인가 "비용에 묻힌 무엣지"인가 → 표본 1국면이라 미결.
- target-path 양수가 selection 예측력인가 순수 생존편향인가 → 양면 다 참, aggregate론 좌측꼬리 지배.

**미검증 (표본/측정 공백)**
- US 5월(강세) net — **측정 자체가 없음.** 모든 net verdict가 6월 단일국면.
- selection/hold *프롬프트 개선*의 라이브 net 기여 — 표본 0(미입증, 단 미반증).
- KR 6월 양전이 추세인가 단월 noise인가 — N=17 ~1.4σ.

---

## 4. 개선 설계 (데이터 ROI 순 — 방향·설계만, 구현은 승인 후)

> 원칙(CLAUDE.md): 덜어내는 처방 우선, 축소→shadow→검증→승인. 새 기능 추가엔 데이터·외부리서치 모두 회의적 → "추가"보다 **측정 복구 + 좌측꼬리 정밀화**가 net 레버.

### [1순위] net 측정 백필 (4·5월) — 측정 인프라, enforce
- **문제:** 모든 net 판정이 6월 한 달. US 5월 gross PF 2.26이 net으로 미측정 → selection 엣지 결판 자체가 불가능.
- **설계:** `capture_net_review`의 net 계산식(`fee_pct_round_trip` 왕복수수료 + `fx_change_pct` FX)을 과거 closed trade에 소급 적용해 `pnl_pct_net`·`net_basis='measured'`를 4·5월 행에 백필. 입력(체결가·환율·수수료 모델)은 이미 보유 → 신규 데이터·lookahead 없음. **이것이 명제1 결판 조건.**
- **단계:** 백필 → 5월 net 산출 → "US 5월 net이 양수인가"로 명제1 재판정.

### [2순위] loss_cap 좌측꼬리 정밀화 — net 최대 레버, shadow→enforce
- **근거:** US loss_cap N=19 net −52.9%가 전체 net 손실. 이 버킷만 줄이면 US net이 breakeven→양전 가능.
- **설계 A(이미 존재):** 트렌드 방어 오버레이(현재 shadow, 기본 off, commit efaf0fc) = 적대 국면 진입 차단. PRO 반박 결론("MILD_BEAR 42건 빼면 나머지 156건 PF 1.56")과 데이터 정합. → **shadow 누적 후 net 기준 A/B kill 바.**
- **설계 B:** loss_cap 발동 종목의 MAE/진입 타이밍 분포 분석 → 발동 거리·진입 게이트 튜닝. **단 selection 패치와 섞지 말 것**(risk 경계). 운영자 확인 필수 파라미터(슬리피지·protective hold 거리) 무단 변경 금지.

### [3순위] KR claude_price 보존 + cohort delta 게이트 — shadow
- **근거:** KR 6월 net PF 2.11 살아있음. 축소/prune 금지(보존 대상).
- **설계:** replacement-in 시 incoming cohort 실측 PF가 outgoing보다 높을 때만 교체(freshness 회전 금지). cohort당 known_at PF 표본 작음 → **shadow 누적 후 enforce.**

### [4순위] hold advisor forward-validation 편입 — shadow/관측 (진행 중)
- **근거:** hold prior가 자기 후속결과로 재검증 안 되는 유일 경로(6/23 리포트 #2·#3). profit_guard A/B verdict ~2026-07-21.
- **설계:** profit_guard ON/OFF outcome을 `lesson_validation` 셀에 축적 → valid 뜨면 유지, net 음전이면 토글 false kill. prior 문구는 클린데이터 재측정값으로 정직화(−8.91%p → 전체값 + "단 88% 녹색 마감" 병기). **청산 행동 변경 아님.**

### [도려낼 것] — 분석 위생
- pre-cutoff LLM replay 5종(historical_sim·preopen_candidate_replay·simulate_hold_advisor_decision_modes·tier1/fasttrack_quality_check) 결과는 **검증으로 인정 금지**(암기누수). 과거 prior/청산 결정이 여기 기댄 적 없는지 점검.
- KR momentum/ORP/continuation: **축소→shadow**(6월 0건 휴면, N=3~11 즉시 kill은 소표본 함정).

### [수익 "추가"] — 데이터가 허용하는 유일 신규 가설
- KR 외국인·기관 확정수급(KRX 18:00 확정 → T+1 진입) **shadow 검증만.** enforce 전 kill 바, 미달 시 닫음(소급검증 1일밀림 동어반복 무효 전례). 그 외 신규 데이터(13F·다크풀·Trends·Form4)는 비용구조상 닫음.

---

## 4b. 공격 전환 검증 (운영자 "청산이 레버·새로 만들지 마라 무시" 지시 후)

"새 데이터 추가"가 아니라 기존 신호를 공격적으로 쓰는 각도를 라이브로 테스트.

- **conviction(entry_priority_score) 사이징 → 기각.** traded=1 3분위(N=74): KR HIGH −4.32%(역상관·최악), US 역U자(MID +3.37%만 양수). 확신점수로 자본 몰면 KR 손실 증폭. 사이징 신호로 못 씀.
- **trade_ready → 양 시장 변별(known_at, lookahead 아님).** ready=1 vs ready=0: US +0.36%/PF1.31 vs −1.97%/PF0.67, KR −0.97%/PF0.37 vs **−3.73%/PF0.08**. ready=0가 출혈 싱크인데 traded의 절반 이상이 ready=0.
- 공격 = 새 전략/데이터 아님. ①ready=0 차단 ②양수 셀(ready=1+claude_price) 자본 집중 ③측정 표면(5월 net) 확장.

## 4c. 적용 결정 — 구현 스펙 (운영자 승인 2026-06-26, 즉시 전면 enforce)

> 운영자 결정: 세 레버 다 적용 + enforce + 초기 손실 감수. 표본 N=33~74·6월 단일국면 위양성 위험은 고지됨(운영자 수용).

| # | 개선 | 결정값 | 위치(소스) | 형태 | 위험/주의 |
|---|---|---|---|---|---|
| **A1** | **ready=0 신규 진입 완전 차단** | ready=1만 진입 | trade_ready 진입 게이트(`runtime/action_routing.py`·candidate gating) | **enforce 즉시** | 진입 빈도 급감(ready=1 표면 US 14·KR 18/표본). 거래량↓·기회비용. ready 승격 프롬프트가 진짜 ready 놓치면 기회 동반 상실 |
| **A2** | **양수 셋업 자본 집중 ×1.5** | ready=1 + claude_price/price-target 경로 = 75만(50만×1.5) | sizing(`execution/` sizing). **고정 50만 = 운영자 확인 필수 파라미터** | **enforce 즉시** | `.env.live` + `config/v2_start_config.json`(env_overrides) **두 소스 동기화 필수**(한 곳만 바꾸면 미반영). conviction 아닌 셋업종류 기준. 포지션 캡·일일 리스크 캡과 충돌 점검 |
| **A3** | **net 측정 백필(4·5월)** | `capture_net` 비용식(fee_round_trip+fx) 소급 → `pnl_pct_net`/`net_basis='measured'` | decisions.db 백필 스크립트 | **즉시(측정, 무행동)** | lookahead 없음. A1·A2 net 검증의 결판 조건 |
| **A4** | net kill-switch 가드(A2용) | 사이징 net 음전 자동 경보(롤백은 수동) | 기존 모니터/대시보드 | enforce(관측 가드) | 즉시 전면 enforce 선택 → bounded 대신 안전망만 |

**연계(이미 합의·진행)**
| # | 개선 | 형태 |
|---|---|---|
| B1 | loss_cap 좌측꼬리 정밀화(US net −48% 전부가 이 1버킷) — 트렌드 방어 오버레이 shadow→net A/B | **별도 결정 필요**(슬리피지·protective hold = 보호 파라미터). net 최대 레버지만 risk 경계 |
| B2 | hold advisor forward-validation 편입 + profit_guard A/B(verdict ~7/21) | shadow/관측(진행 중) |
| C1 | pre-cutoff LLM replay 5종 결론 검증 불인정 | 분석 위생 |
| C2 | hold prior −8.91%p 오염수치 정직화(전체값+"88% 녹색 마감" 병기) | 문서/프롬프트 |
| C3 | KR momentum/ORP/continuation 휴면 전략 — A1(ready=0 차단)에 상당부분 흡수, 잔여는 축소 | shadow |
| D1 | KR claude_price·Claude price-target 보존(net 양수) — prune 금지 | 보존 |
| E1 | KR 외국인·기관 확정수급(KRX 18:00→T+1) shadow 검증만, enforce 전 kill바 | 백로그 shadow |

## 5. 한 줄 결론

**"selection을 갈아엎어 수익을 낸다"도 "selection은 무가치다"도 데이터가 받치지 않는다.** 검증된 사실은 셋 — (1) net 측정이 6월 단일국면뿐이라 selection 엣지는 *결판 불가*(미결), (2) net 손실은 selection이 아니라 **loss_cap 좌측꼬리 1버킷**이 전부라 **청산이 1순위 레버**, (3) KR claude_price·Claude price-target은 net 양수로 **살아있어 보존**. → 다음 행동은 selection 프롬프트 churn이 아니라 **①4·5월 net 백필(결판 조건) ②좌측꼬리 정밀화(트렌드 오버레이 shadow→net A/B) ③살아있는 KR/price-target 보존 ④hold forward-validation**, 그리고 가짜 과적합(pre-cutoff replay 결론·오염 prior 수치)만 잘라낸다.
