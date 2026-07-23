# 데이터·전략 기반 매수 강화 — 코드 재현 검토 (2026-07-23)

운영자 지시: Claude가 예측을 못 하니 데이터·전략(룰)으로 사야 하는데 매수가 안 나온다.
프롬프트/정보 개선이 가능한지 **API 없이 코드로 judge 판정을 재현**해 다양하게 검토.

## 0. 실측 — 룰 전략은 죽어 있다

```
decisions 원장(라이브 81,727행, 6~7월):
  mom_fired 1 · gap 5 · vb 0 · mr 0 · 전체 발화 6건(0.0%)
```
momentum/gap/volatility_breakout/mean_reversion 4개 룰이 2개월에 6번 발화. 사실상 죽음.
실제 매수는 전부 PathB(claude_price)·즉시매수 = **judge(single_symbol_judge, Claude) 경로**.
judge는 WAIT_RECHECK 84%(모멘텀 소멸 118 · 데이터 미형성 28 · ORB 미형성 25).

## 1. judge 판정을 룰로 재현 — 정보/임계 변형별 매수 수 + forward net

US 프롬프트 후보 31,249건(60분 forward 보유, 6~7월)에 judge BUY_READY 기준을 룰 근사.

```
현행 근사(지속모멘텀+ORB+RVOL>2+VWAP상단)   매수 3048 · net -0.361% · 승 33%
변형: RVOL 소스교체(trvol)                  매수  266 · net -0.740%   더 나쁨
변형: 완화(ret30>2 OR ORB+RVOL>3)          매수 7053 · net -0.391%   더 사도 음수
변형: VWAP 제거                            매수 3134 · net -0.367%
변형: ret5>5 극단모멘텀 단독                 매수  556 · net +0.062%  ★유일 양수
```

**매수를 늘리면(완화) net이 더 나빠진다. 정보 교체(trvol)·VWAP 제거도 악화/무변화.**
= 프롬프트/정보를 어떻게 바꿔도 현행 확인-기반 기준은 net을 못 살린다.

## 2. ★ 유일 양수(극단 모멘텀) 심층 — judge의 확인이 오히려 해친다

```
ret5>5 (556건):  60분 +0.062% · 1일 +0.859%(승률 60%)   오래 들수록 좋아짐
국면별(60분):  CAUTIOUS +0.874 · MILD_BULL +0.163 · NEUTRAL +0.492  vs  MILD_BEAR -0.869
조합:
  ret5>5 + ORB      -0.274%   ← ORB 요구가 수익건을 제거
  ret5>5 + ret30>3  -0.440%   ← 지속모멘텀 요구가 제거
  ret5>5 + RVOL>3   +0.162%   ← RVOL 만 약간 도움
```

**judge의 다중 확인(ORB+지속+VWAP)이 수익나는 극단 스파이크를 걸러낸다.**
확인을 더 요구할수록 net이 나빠진다 — judge는 강모멘텀에 확인을 "줄여야" 산다.

## 3. 깨끗한 룰 후보

```
ret5>5 AND NOT MILD_BEAR (470건):
  60분(우리 하한): net +0.232% 승률 47%
  1일(우리 상한):  net +0.238% 승률 55%   horizon 무관 안정적 양수
  빈도: 세션당 ~11.8건  (현행 judge BUY_READY 월 5건)

대조 현행 judge 엄격: net -0.361%(음수)
```

단순 룰(극단 5분 모멘텀 + 나쁜 국면 회피)이 judge보다 훨씬 많이·양수 net으로 매수한다.

## 4. 판정과 주의 (오늘 규율)

**발견**: 매수를 못 하는 건 후보가 없어서가 아니라 **judge의 확인 기준이 (a) 수익건을
걸러내고 (b) 그래도 남는 건 net-음수**라서다. 데이터·전략 매수의 실체는 "확인을 더하기"가
아니라 "강모멘텀에 확인을 빼기 + 나쁜 국면 회피"다.

**주의(단정 금지)**:
- forward gross-fee(수수료만) · 후보 시점 진입/청산 가정 = 우리 실제 진입타이밍·출구룰 아님.
  +0.232%는 이상화된 상한 근처. 실제 슬리피지·타이밍에 줄어들 수 있다.
- 11.8/세션은 후보 수지 체결 수 아님(affordability·포지션상한 후 훨씬 적음).
- 다중검정: ret5>5가 여러 변형 중 최고 → 일부 선택편의. 단 국면 일관·horizon 단조가 실재 시사.
- 프롬프트 진입 후보(in_prompt=1) 사전필터된 집합.
- **미검증. 라이브 매수 행동 변경 = 운영자 결정. shadow 선행 필수.**

## 5. 다음 — shadow 관측기 (제안)

라이브 주문 무영향으로 "ret5>5 + NOT MILD_BEAR 룰이 발동했을 후보와 그 우리-net 결과"를
세션 단위로 쌓는 관측기. 오늘 배포한 tighten shadow와 같은 형태(기록만).
살아남으면 룰 기반 매수 경로로 승격 검토(운영자), 무너지면 종결. 지금은 착수 금지 — 관측 먼저.

## 5b. 프롬프트 실체 — judge가 강모멘텀을 거르는 구조적 이유

`execution/single_symbol_judge.py` build_single_symbol_judge_prompt 확인:
1. **기본값은 BUY_READY 금지**: buy_ready 게이트 off면 "Do not use BUY_READY or PROBE_READY",
   PULLBACK_WAIT/WAIT/REJECT만. PULLBACK_WAIT은 눌림 존 요구.
2. **PULLBACK_ZONE_RULE**(prompt_contracts.py): "buy_zone_high must sit at least 0.5% BELOW
   current price. A zone that fills immediately is a chase, not a pullback." → 고점에서 달리는
   강모멘텀은 눌림 존이 없어 WAIT_RECHECK 강제.
3. **BUY_READY 게이트**(config): US만 · `MILD_BULL,MODERATE_BULL,AGGRESSIVE`만 허용.
   내 데이터의 수익 국면 **CAUTIOUS(+0.874)·NEUTRAL(+0.492)이 목록에 없어** 눌림-대기로 강제.

= 프롬프트·config가 "눌림을 기다려라" 철학이라 수익나는 극단 모멘텀 스파이크를 구조적으로 금지.
단 BUY_READY guide 자체는 이미 "strong momentum, keep stop TIGHT, winners run"으로 옳은
방향 — 게이트가 너무 좁을 뿐이다.

## 5c. 외부 레퍼런스 — 유사 LLM 트레이딩 시스템은 어떻게 하나

QuantAgent(arxiv 2509.09995) 등 다중에이전트 LLM 트레이딩:
- Decision Agent: "**Favour strong momentum and decisive price action**(MACD crossover,
  breakout candle)" — 기계적 눌림 규칙보다 강신호 우선.
- 고점 extended여도 자동 눌림 대기 안 함. **breakout continuation 또는 pullback 둘 다 유효**.
- 과매수(RSI>70)면 차단이 아니라 "**tighten stops, scale position size**"(손절 조임·사이즈 축소).

= 우리 데이터(극단모멘텀 수익·확인 과요구가 해침)와 업계 관행이 **일치**한다:
  강모멘텀은 사되(breakout), extension은 사이즈·손절로 관리하지 **차단하지 않는다.**

## 6. 이 검토가 답한 것

- "프롬프트/정보로 개선되나" → 정보 교체·완화 전부 악화. 현행 확인기준은 못 살린다.
- "데이터·전략 매수 강화되나" → **된다, 단 방향은 '확인 빼기 + 국면 회피'**. 극단 모멘텀이 핵심.
- "매수가 왜 없나" → judge가 강모멘텀 스파이크에 확인을 과요구해 걸러낸다.
