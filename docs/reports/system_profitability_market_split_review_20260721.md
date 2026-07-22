# KR/US별 수익성 개선 상세 점검

작성일: 2026-07-21  
기준 DB: `data/ml/decisions.db`, `data/audit/candidate_audit.db`  
전제: 2번 비용 governor는 사용자가 “어쩔 수 없는 비용”으로 판단했으므로, 비용 절감 자체는 우선순위에서 제외하고 “같은 비용을 어디에 써야 수익성이 올라가는가”로 해석한다.

## 1. 시장별 결론

KR과 US는 같은 로직으로 다루면 안 된다.

| 구분 | KR | US |
| --- | --- | --- |
| 현재 문제 | 좋은 후보를 너무 막고 있을 가능성 | Claude/route가 ready로 본 후보도 단기 성과가 약함 |
| 살릴 경로 | PathB / Claude-price 중심 | Momentum, opening-range 계열을 소액 검증 |
| 줄일 경로 | PlanA momentum, gap_pullback, volatility_breakout | PathB claude_price 과다 의존, gap_pullback, probe_ready |
| 뉴스 | 일부 catalyst는 쓸 여지 있음 | score bonus 금지 유지 |
| 핵심 개발 | 과차단 해소, KR 전용 confirmation | 추격 방지, 시간대/entry quality, 비용 차감 edge |
| 운영 방향 | 제한적 live 재개 후보 | shadow/probe 중심, full live 확장 금지 |

내 판단은 다음이다.

- KR은 “너무 안 사서 놓치는 문제”를 우선 점검해야 한다.
- US는 “사면 비용 차감 후 안 남는 문제”가 우선이다.
- 따라서 KR은 gate 완화 후보를 찾고, US는 gate 강화/전략 교체 후보를 찾아야 한다.

## 2. 항목별 상세 개선

## 2.1 후보군/Claude action

### KR

7월 후보 감사에서 중요한 신호가 있다.

| 조건 | rows | avg ret60 | win60 |
| --- | ---: | ---: | ---: |
| Claude `BUY_READY` → route `WATCH` | 82 | +2.7806% | 71.43% |
| Claude `PULLBACK_WAIT` → route `WATCH` | 85 | +1.2665% | 50.00% |
| Claude `PULLBACK_WAIT` → route `PULLBACK_WAIT` | 31 | +1.0259% | 28.57% |
| Claude `WATCH` → route `WATCH` | 8,389 | +0.0439% | 44.19% |

KR은 route가 Claude의 적극 판단을 너무 많이 WATCH로 낮춘 흔적이 있다. 특히 `BUY_READY → WATCH`가 ret60 기준 강하다. 이건 즉시 매수하라는 뜻은 아니지만, “KR에서 과차단이 발생 중인지”는 P0로 봐야 한다.

개선안:

1. KR 전용 `BUY_READY_demoted_to_watch` 감사 리포트 추가
   - 왜 WATCH로 낮췄는지 `route_reason`, `hard_blocks`, `soft_gates`, `no_submit_reason_code`별로 집계한다.
   - demotion 사유별 ret30/ret60을 계산한다.

2. KR `BUY_READY → WATCH` 중 강한 조건만 소액 probe 후보로 승격
   - ret60 사후 결과가 좋다고 모두 살리면 lookahead다.
   - 실시간 조건으로는 다음이 필요하다.
     - 스프레드/호가 안정
     - 첫 발견 이후 chase 제한
     - 거래대금/체결강도 확인
     - 지수 급락/브레드스 악화 차단
     - 뉴스 stale/weak 제외

3. KR `WATCH` 전체는 매수 대상이 아님
   - `WATCH → WATCH` 평균은 +0.0439%라 비용/슬리피지를 넘기 어렵다.
   - 살릴 대상은 `Claude 적극 판단이 route에서 막힌 케이스`다.

### US

US 7월 후보 감사는 반대다.

| 조건 | rows | avg ret60 | win60 |
| --- | ---: | ---: | ---: |
| Claude `BUY_READY` → route `BUY_READY` | 343 | -0.3049% | 39.47% |
| Claude `PROBE_READY` → route `PROBE_READY` | 72 | -1.7544% | 0.00% |
| Claude `PULLBACK_WAIT` → route `PULLBACK_WAIT` | 101 | -0.8023% | 42.42% |
| Claude `BUY_READY` → route `WATCH` | 26 | +1.6332% | 100.00% |
| Claude `PULLBACK_WAIT` → route `WATCH` | 73 | +0.3714% | 51.85% |

US는 route가 BUY_READY로 허용한 그룹이 오히려 약하고, WATCH로 낮춘 일부가 좋아 보인다. 이건 US route/gate가 “무엇을 통과시켜야 하는지”를 잘못 학습했을 가능성을 의미한다.

개선안:

1. US `BUY_READY/PROBE_READY` 승격 조건 재검토
   - 7월 기준 `PROBE_READY`는 특히 위험하다.
   - sample이 작아도 win60 0%는 live 확장 금지 신호다.

2. US는 Claude action을 매수 방향으로 쓰지 말고 feature로만 사용
   - `BUY_READY` 자체가 단기 edge를 보장하지 않는다.
   - Claude가 본 강점보다 entry timing, chase, spread, sector regime이 더 중요하다.

3. US `BUY_READY → WATCH`의 demotion 사유를 역추적
   - 이 그룹은 rows 26, win60 100%라 표본은 작지만 무시하면 안 된다.
   - 단, 바로 live로 켜지 말고 조건을 추출해서 shadow challenger로 둔다.

## 2.2 비용/API 항목

사용자가 말한 대로 이 비용 자체가 어쩔 수 없는 측면은 있다. 따라서 “줄이자”가 1차 목표가 아니다.

다만 시장별로 비용의 사용 목적은 달라야 한다.

### KR

KR은 비용을 “과차단 해소”에 쓰는 것이 맞다.

- Claude가 BUY_READY/PULLBACK_WAIT로 본 후보 중 route가 WATCH로 낮춘 케이스를 분석한다.
- Claude 추가 호출은 신규 후보 확대보다 demotion reason 해석에 쓰는 편이 낫다.
- KR은 소수 고품질 후보를 live probe로 연결할 가능성이 있다.

### US

US는 비용을 “매수 판단”에 쓰면 효율이 낮다.

- Claude가 BUY_READY라고 해도 ret60이 음수였다.
- US Claude 호출은 매수 추천보다 risk extraction, catalyst validation, overreaction/chase 판별에 써야 한다.
- US는 expensive 판단 전에 가격/시간대/스프레드 prefilter를 먼저 통과시켜야 한다.

요약:

- KR: Claude 비용 = 막힌 좋은 후보를 찾는 비용
- US: Claude 비용 = 나쁜 매수를 막는 리스크 검증 비용

## 2.3 전략/route

### KR

`v2_learning_performance`와 canonical 집계 기준:

| 전략/route | 월 | closed | avg net |
| --- | --- | ---: | ---: |
| claude_price / path_b | 2026-06 | 18 | +1.0667% |
| claude_price / path_b | 전체 관측 | 23 | +0.8930% |
| momentum / plan_a | 2026-05 | 5 | -1.9153% |
| gap_pullback / path_b | 2026-04 | 13 | -1.0316% |

KR은 `claude_price / path_b`가 현재 유일하게 live 유지·개선할 근거가 있다.

개선안:

1. KR live allowlist를 `path_b + claude_price` 중심으로 제한
2. PlanA momentum은 기본 off 또는 shadow
3. gap_pullback은 live 확장 금지
4. KR route가 BUY_READY를 WATCH로 낮춘 조건을 재학습
5. KR은 “즉시 매수”보다 “가격대 조건부 재진입”이 맞다

주의:

- 4월 KR PathB는 나빴고 6월은 좋았다. regime 영향 가능성이 있다.
- 따라서 KR도 무제한 확대는 안 된다.
- 하지만 현재 시스템에서 가장 먼저 살릴 후보는 KR PathB Claude-price다.

### US

US 실제 성과:

| 전략/route | 월/구간 | closed | avg net |
| --- | --- | ---: | ---: |
| claude_price / path_b | 2026-05 | 77 | +0.5419% |
| claude_price / path_b | 2026-06 | 126 | -0.6220% |
| claude_price / path_b | 전체 canonical | 208 | -0.2054% |
| gap_pullback | 전체 | 24 | -0.9031% |
| momentum | 전체 | 13 | +0.2219% |
| opening_range_pullback | 전체 | 7 | +1.1925% |

US는 5월에는 됐고 6월에는 무너졌다. 즉 “전략이 항상 나쁘다”보다 regime/시간대/진입 품질에 민감하다. 하지만 전체 net이 음수라 live 확대 근거는 부족하다.

개선안:

1. US PathB Claude-price는 기본 축소
   - 208건 closed로 표본은 충분한 편이고 평균 net이 -0.2054%다.
   - 더 사면 손실과 비용이 누적될 가능성이 높다.

2. US momentum/opening-range는 small-n challenger
   - momentum avg net +0.2219%, opening-range +1.1925%지만 표본이 작다.
   - live 증액이 아니라 소액 probe 또는 shadow replay가 맞다.

3. US gap_pullback은 block 후보
   - 실제 closed 평균 net -0.9031%.
   - 후보 감사에서도 gap_pullback 관련 신호가 강하지 않다.

4. US는 regime filter 필수
   - 5월과 6월 성과 차이가 크다.
   - 같은 Claude-price라도 시장 상태가 바뀌면 음수화된다.

## 2.4 뉴스/catalyst

### KR

뉴스 품질별 ret60:

| news_quality / signal_type | rows | eligible | avg ret60 | win60 |
| --- | ---: | ---: | ---: | ---: |
| normal / direct_catalyst | 3,326 | 2,788 | +0.3169% | 45.51% |
| mixed / direct_catalyst | 3,805 | 2,454 | +0.0798% | 46.35% |
| weak / direct_catalyst | 4,952 | 0 | -0.0623% | 40.97% |
| weak / risk_negative | 635 | 485 | +0.5987% | 47.45% |

KR은 뉴스가 완전히 무용하다고 보기 어렵다. 다만 품질 구분이 매우 중요하다.

개선안:

1. KR catalyst bonus는 확대하지 말고 조건부 feature로 유지
2. `normal/direct_catalyst`만 후보 우선순위 feature로 사용
3. `weak/direct_catalyst`는 보너스 금지
4. `mixed/direct_catalyst`는 단독 보너스 금지, 가격/거래대금 확인 필요
5. `risk_negative`는 해석 주의
   - 악재성 뉴스가 ret60 양수로 보이는 것은 반등/과매도/분류 오류 가능성이 있다.
   - 매수 보너스가 아니라 “리스크 태그 + 반등 후보 여부”로 분리해야 한다.

### US

뉴스 품질별 ret60:

| news_quality / signal_type | rows | eligible | avg ret60 | win60 |
| --- | ---: | ---: | ---: | ---: |
| normal / direct_catalyst | 23,109 | 17,165 | -0.0081% | 43.99% |
| mixed / direct_catalyst | 1,780 | 1,573 | -0.0795% | 41.99% |
| normal / earnings_or_guidance | 73 | 73 | +0.1441% | 51.16% |
| normal / risk_negative | 1,707 | 1,707 | +0.3384% | 46.97% |
| mixed / risk_negative | 111 | 111 | -0.9596% | 0.00% |

US direct catalyst는 rows가 많고 평균 ret60이 거의 0 또는 음수다. 따라서 US catalyst score bonus를 켜면 안 된다.

개선안:

1. `US_CATALYST_SCORE_BONUS_ENABLED=false` 유지
2. direct_catalyst는 매수 보너스가 아니라 “이미 반영됐는지 확인할 대상”
3. earnings_or_guidance만 별도 shadow feature
4. risk_negative는 반등 후보와 진짜 악재를 분리
5. US 뉴스는 가격 반응 이후 chase 여부가 핵심

## 2.5 진입/시간대/체결

### KR

KR은 실전 표본상 `PathB + claude_price`가 강하지만, MFE/MAE 변동이 크다.

관측:

- KR claude_price 평균 net +0.8930%
- 평균 MFE +2.0734%
- 평균 MAE +0.0642%로 canonical 일부에서는 양호
- v2_learning 6월 기준 MFE +5.4677%, MAE -4.3545%로 변동성 큼

개선안:

1. KR은 entry zone 품질을 더 엄격히 본다.
2. 첫 발견 후 급등 추격은 금지한다.
3. BUY_READY라도 즉시 market order가 아니라 limit/zone 기반이 맞다.
4. PathB 대기 중 가격이 너무 멀어지면 자동 취소한다.
5. 체결 후 빠른 MFE가 발생하면 일부 익절/트레일링을 붙인다.

### US

US는 gross가 약간 좋아도 net이 음수다. 즉 entry quality가 수익성을 결정한다.

관측:

- US claude_price 전체 avg net -0.2054%
- avg MFE +1.2262%, avg MAE -1.0147%
- MFE가 있음에도 net이 안 남음
- momentum은 avg MFE +5.9972%, avg MAE -1.5420%로 수익 포착 개선 여지 있음

개선안:

1. US는 chase 방지가 최우선
2. spread/price impact 조건 미충족 시 Claude 판단과 무관하게 block
3. opening 이후 과열 구간과 중반 약한 시간대 분리
4. momentum/opening-range는 빠른 partial take-profit shadow 필요
5. PathB claude_price는 손익비가 개선될 때만 재확대

## 2.6 청산/수익 포착

### KR

KR은 일부 경로에서 MFE가 큰데 최종 net이 음수인 전략들이 있다.

| 전략 | avg net | avg MFE | avg MAE |
| --- | ---: | ---: | ---: |
| claude_price | +0.8930% | +2.0734% | +0.0642% |
| gap_pullback | -0.9033% | +3.2550% | -1.6640% |
| momentum | -2.1652% | +3.1007% | -1.7292% |

gap_pullback/momentum은 “한때 오르지만 못 지킨다”보다 “진입 후 위험도도 크다”에 가깝다. 단순 trailing만 붙인다고 해결된다고 보면 안 된다.

개선안:

- claude_price에는 수익 보존형 trailing 적용
- gap_pullback/momentum은 exit 개선보다 entry 차단이 우선
- MFE가 +1.5~2.0% 이상 발생 후 음전하는 패턴을 별도 리플레이

### US

US는 momentum과 opening-range에서 MFE가 크다.

| 전략 | avg net | avg MFE | avg MAE |
| --- | ---: | ---: | ---: |
| opening_range_pullback | +1.1925% | n/a | n/a |
| momentum | +0.2219% | +5.9972% | -1.5420% |
| claude_price | -0.2054% | +1.2262% | -1.0147% |
| gap_pullback | -0.9031% | +1.4818% | -1.3353% |

US 개선 포인트는 “좋은 종목을 더 찾기”보다 “MFE를 수익으로 잠그기”다.

개선안:

- momentum/opening-range에 빠른 partial TP 실험
- claude_price는 holding time을 줄이거나 entry 조건을 더 낮춘다.
- gap_pullback은 exit 개선보다 block 우선
- MFE 대비 최종 PnL 훼손률을 전략별 지표로 추가한다.

## 2.7 외부 데이터/백필

### KR

KR은 외부 데이터가 특히 중요하다.

필요 데이터:

- 장전/장중 공시
- 테마/섹터 수급
- 거래대금 급증 이력
- VI/상한가 근접 이력
- 종목별 뉴스 timestamp
- 수급 주체 가능 시

목표:

- `BUY_READY → WATCH`로 막힌 후보가 어떤 조건에서 실제로 좋았는지 사전 검증한다.
- KR catalyst가 어떤 뉴스 타입에서만 작동하는지 분리한다.
- 테마 broad 뉴스와 개별 종목 catalyst를 분리한다.

### US

US는 외부 데이터가 “뉴스 품질”보다 “가격 반응 통제”에 필요하다.

필요 데이터:

- earnings calendar/surprise
- premarket gap
- intraday VWAP/relative volume
- spread/market cap/liquidity
- sector ETF movement
- analyst revision
- short interest 가능 시

목표:

- direct catalyst가 이미 가격에 반영됐는지 판단한다.
- earnings/guidance만 별도 검증한다.
- 5월에는 되고 6월에는 안 된 regime 차이를 설명한다.

## 3. 시장별 우선순위

### KR 우선순위

1. `BUY_READY/PULLBACK_WAIT → WATCH` demotion 감사
2. KR PathB Claude-price allowlist 유지
3. PlanA momentum/gap_pullback live 확장 금지
4. KR normal/direct_catalyst만 조건부 feature 유지
5. 소액 probe 기준 정의
6. 수익 보존형 trailing/partial TP shadow
7. KR 외부 뉴스/공시 백필

### US 우선순위

1. `BUY_READY/PROBE_READY` 승격 조건 재검토
2. US catalyst/news score bonus off 유지
3. PathB claude_price 축소 또는 stricter gate
4. gap_pullback block
5. momentum/opening-range small-n challenger 설계
6. chase/spread/time-of-day prefilter 강화
7. earnings/guidance와 regime 외부데이터 백필

## 4. 최종 판단

KR과 US의 개선 방향은 반대다.

KR은 너무 막고 있는 좋은 후보를 찾아야 한다.  
US는 너무 쉽게 ready가 되는 나쁜 후보를 막아야 한다.

따라서 다음 개발은 하나의 글로벌 점수 개선이 아니라 시장별 정책 분리여야 한다.

```text
KR = 과차단 해소 + PathB Claude-price 집중 + catalyst 조건부 사용
US = 과승격 차단 + chase 방지 + momentum/opening-range 소액 검증
```

2번 비용 문제는 사용자가 말한 대로 어느 정도 감수하더라도, 같은 비용을 쓰는 목적은 달라야 한다. KR에서는 기회 손실을 줄이는 데 쓰고, US에서는 손실 진입을 막는 데 써야 한다.
