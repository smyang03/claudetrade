# 시스템 수익성 극대화 점검 리포트

작성일: 2026-07-21  
범위: 후보군 생성, Claude 판단, 매수 빈도, 뉴스/점수 게이트, 실제 체결 성과, API 운영비, 향후 개발 방향

## 1. 결론

현재 시스템의 핵심 문제는 “후보가 부족해서 매수가 안 된다”가 아니다. 후보는 여전히 많이 생성된다. 문제는 다음 두 가지다.

1. 실제 돈을 넣어도 되는 양의 기대값 전략이 아직 분리·검증·승격되지 않았다.
2. Claude 호출과 후보 평가 비용은 계속 발생하지만, 최근 게이트 강화 후 매수로 이어지는 positive edge pipeline이 거의 멈췄다.

따라서 지금 방향은 절반은 맞다. 검증 안 된 점수와 뉴스 보너스를 끄고, 바로 매수하지 않게 막은 것은 맞다. 다만 그 다음 단계인 “살 만한 소수 전략을 증명하고 그 전략에만 자본과 API 비용을 집중하는 구조”가 아직 부족하다. 이게 없으면 시스템은 손실을 줄이는 방어형 상태에 머물고, 운영비만 나간다.

내 판단은 다음과 같다.

- Claude가 고른 종목을 바로 사는 전략은 현재 근거 부족이다.
- Claude는 매수 주체가 아니라 “뉴스/맥락/리스크/논리 추출기”로 써야 한다.
- 매수 권한은 전략별 실증 성과, 비용 차감 기대값, 시간대/시장/경로별 통계가 가져야 한다.
- 지금 당장 더 많은 매수를 만들기보다, “양의 기대값이 확인된 경로만 작게 재가동하고 나머지는 shadow”가 맞다.

## 2. 현재 관측된 핵심 숫자

### 2.1 실제 체결 성과

`data/ml/decisions.db` 기준 canonical closed 성과:

| 시장 | closed | 평균 gross PnL % | 평균 net PnL % | 승률 |
| --- | ---: | ---: | ---: | ---: |
| KR | 62 | -0.3947 | -0.5540 | 35.48% |
| US | 255 | +0.1642 | -0.2285 | 38.82% |

US는 gross가 약간 양수여도 net이 음수다. 즉 진입 아이디어가 완전히 무가치하다고 보긴 어렵지만, 수수료/슬리피지/환산비용/실행 품질을 넘지 못한다. KR은 전체 평균이 음수지만 특정 경로에서는 양호한 흔적이 있다.

### 2.2 월별 추이

| 시장 | 월 | closed | 평균 net PnL % |
| --- | --- | ---: | ---: |
| KR | 2026-04 | 22 | -1.6142 |
| KR | 2026-05 | 21 | -0.8983 |
| KR | 2026-06 | 18 | +1.0667 |
| KR | 2026-07 | 1 | +0.8299 |
| US | 2026-04 | 21 | -0.5075 |
| US | 2026-05 | 101 | +0.4248 |
| US | 2026-06 | 130 | -0.6507 |
| US | 2026-07 | 3 | -2.5255 |

7월 매수가 거의 없는 것은 단순 장애라기보다, 7월 중순 이후 게이트가 강해져서 entry intent가 줄어든 결과에 가깝다. 손실 경로를 줄인 것은 맞지만, 대체 수익 경로가 아직 충분히 올라오지 않았다.

### 2.3 후보는 계속 많다

`data/audit/candidate_audit.db` 기준 후보 생성은 계속 많다.

| 시장 | 월 | 후보 rows | prompt rows | entry intent rows | 평균 ret60 % |
| --- | --- | ---: | ---: | ---: | ---: |
| KR | 2026-07 | 22,345 | 12,477 | 41 | -0.0435 |
| US | 2026-07 | 58,133 | 22,807 | 516 | -0.0477 |

후보는 충분하다. 하지만 2026-07-08 이후 실제 entry intent가 급감했고, 2026-07-16~20에는 US가 0에 가까웠다. 이는 “매수 안 하는 시스템”이라기보다 “현재 조건에서 살 이유를 못 찾는 시스템”이다.

### 2.4 전략 경로별 성과 차이

가장 중요한 관찰은 경로별 성과가 완전히 다르다는 점이다.

| 시장 | route/path | closed | 평균 net PnL % | 해석 |
| --- | --- | ---: | ---: | --- |
| KR | path_b / claude_price | 23 | +0.8930 | 현재 가장 살릴 가치가 있는 경로 |
| KR | path_b / gap_pullback | 17 | -0.8606 | 축소/비활성 후보 |
| KR | path_b / momentum | 6 | -2.3734 | 비활성 후보 |
| KR | path_b / opening_range_pullback | 5 | -1.8523 | 비활성 후보 |
| US | path_b / claude_price | 208 | -0.2054 | 거래 수는 많지만 비용 차감 후 약함 |
| US | path_b / gap_pullback | 20 | -0.7050 | 비활성 후보 |
| US | path_b / momentum | 11 | +0.1551 | small-n, shadow/소액 검증 후보 |
| US | path_b / opening_range_pullback | 6 | +0.1673 | small-n, shadow/소액 검증 후보 |

결론적으로 “시스템 전체를 켜거나 끄는 방식”은 맞지 않다. 전략별 allowlist/denylist/자본 배분이 필요하다.

## 3. Claude 판단을 바로 매수로 쓰면 안 되는 이유

논문과 현재 시스템 데이터가 같은 방향을 가리킨다.

- LLM은 뉴스 헤드라인과 텍스트에서 수익률 예측 신호를 만들 수 있다는 연구가 있다. 예: Lopez-Lira & Tang의 “Can ChatGPT Forecast Stock Price Movements?”는 뉴스 기반 LLM 점수가 이후 주가 반응과 연관될 수 있음을 보였다.  
  Source: https://arxiv.org/abs/2304.07619
- 뉴스 흐름을 이용해 LLM representation을 fine-tuning하면 포트폴리오 성과가 개선될 수 있다는 연구도 있다.  
  Source: https://arxiv.org/abs/2407.18103
- LLM 기반 sentiment가 전통 사전식 sentiment보다 나은 결과를 낸다는 연구도 있다.  
  Source: https://arxiv.org/abs/2412.19245

하지만 이 근거들은 “LLM이 바로 매수 버튼이 된다”는 뜻이 아니다. 연구들은 보통 다음 조건을 둔다.

- 다수 종목 포트폴리오
- 통계적 신호 aggregation
- 비용/리밸런싱/롱숏 또는 long-only 포트폴리오 구성
- out-of-sample 검증
- 뉴스 시점과 가격 반응 시점 통제

우리 시스템의 실전 문제는 더 어렵다.

- 단일 종목 또는 소수 종목으로 체결된다.
- 장중 진입 위치와 체결 품질이 성과를 크게 바꾼다.
- US는 gross edge가 있어도 net cost를 못 넘는다.
- 뉴스 보너스가 실제로 US에서 손실 쪽으로 작동한 흔적이 있다.
- Claude action과 실제 수익 사이의 calibration이 아직 부족하다.

따라서 Claude의 역할은 다음으로 제한하는 것이 맞다.

- 뉴스/공시/이벤트의 실질성 판별
- catalyst가 가격에 이미 반영됐는지 설명
- 리스크 요인 추출
- 후보 간 상대 비교
- 사람이 읽을 수 있는 decision rationale 생성

매수 권한은 deterministic strategy gate와 비용 차감 expected value 모델이 가져야 한다.

## 4. 현재 시스템에서 궁극적으로 부족한 것

### 4.1 단일 수익 목표 함수

지금은 여러 DB와 리포트가 존재한다.

- 후보 감사 DB
- Claude call audit
- v2 decisions
- fills/link
- candidate outcomes
- usage/cost JSON

각각은 유용하지만, “이 의사결정 1건이 전체 자본에 얼마를 벌었고 Claude/API 비용까지 포함하면 얼마인가?”를 한 줄로 안정적으로 말하는 계층이 약하다.

필요한 목표 함수:

```text
expected_net_profit =
  expected_trade_pnl
  - broker_cost
  - slippage_cost
  - fx_cost
  - opportunity_cost
  - allocated_ai_cost
```

지금처럼 net PnL만 봐도 US는 음수다. 여기에 Claude 운영비까지 배분하면 더 엄격해져야 한다.

### 4.2 전략별 자본 배분 체계

현재는 “후보/점수/뉴스/Claude 판단”이 한 파이프라인에 섞이기 쉽다. 실제 성과는 전략별로 다르다. 따라서 전체 시스템 점수 하나로 매수하면 안 된다.

필요한 구조:

- KR PathB Claude-price: live allowlist 후보
- US PathB Claude-price: 축소 또는 shadow 재검증
- US momentum/opening-range: small-n 소액 검증 후보
- gap_pullback 계열: 비활성 또는 shadow
- 뉴스 보너스: US는 off 유지, KR도 확대 금지

자본은 전략별 sleeve로 나눠야 한다.

```text
capital_sleeve = market + strategy + entry_route + holding_profile
```

각 sleeve는 독립적으로 승격/강등되어야 한다.

### 4.3 “매수 없음”을 성공으로 측정하는 기준

최근 매수가 없다는 것은 나쁜 현상일 수 있지만, 무조건 고쳐야 할 장애는 아니다. 음수 기대값 시장에서 매수를 안 하는 것은 손실 회피다. 문제는 abstain 자체가 아니라, abstain이 돈을 아끼는지 아니면 기회를 놓치는지 측정하는 기준이 없다는 점이다.

필요한 지표:

- rejected 후보의 이후 ret30/ret60/ret1d
- rejected 후보 중 실제로 매수했으면 net positive였을 비율
- abstain으로 줄인 예상 손실
- abstain 때문에 놓친 예상 이익
- Claude 호출 비용 대비 실제 trade-ready/filled 전환율

현재 7월은 후보 rows와 Claude call은 많은데 filled가 거의 없다. 이 상태에서는 “좋은 보수성”인지 “비싼 무의사결정”인지 분리해야 한다.

### 4.4 API 비용 게이트

`state/live_api_usage.json` 기준 live 누적 Claude/API 비용은 약 $304.37이다.

월별 live 비용:

| 월 | 호출 수 | 비용 USD | 평균 비용/호출 |
| --- | ---: | ---: | ---: |
| 2026-04 | 1,085 | $17.51 | $0.0161 |
| 2026-05 | 6,240 | $95.90 | $0.0154 |
| 2026-06 | 5,614 | $135.46 | $0.0241 |
| 2026-07 | 2,436 | $55.50 | $0.0228 |

7월에는 매수가 줄었는데도 호출비는 계속 나간다. 이건 구조적으로 손봐야 한다.

필요한 정책:

- expected value가 음수인 시장/경로에서는 Claude 호출 자체를 줄인다.
- cheap prefilter 통과 후에만 expensive selection call을 호출한다.
- trade-ready 가능성이 낮은 시간대에는 호출을 sampling/shadow로 낮춘다.
- 같은 후보/뉴스/스냅샷은 캐시하고 재호출하지 않는다.
- “일일 Claude 비용 한도”가 아니라 “예상 수익 대비 호출 예산”으로 관리한다.

### 4.5 실행/진입 모델

현재 데이터는 “좋은 종목을 골랐는가”보다 “어디서 들어갔는가”가 성과를 많이 갈랐다는 쪽에 가깝다.

US는 특히 gross가 약간 양수인데 net이 음수라서 진입 품질이 중요하다. 즉 더 좋은 종목명보다 더 좋은 entry zone, 시간대, 체결 방식이 필요하다.

필요한 개선:

- 시장별 시간대 gate 재검증
- chase 방지
- pullback 대기 조건의 실제 fill 후 성과 검증
- MFE/MAE 기반 진입 후 관리
- 신호 발생 후 몇 분 뒤 들어가는 게 유리한지 walk-forward 검증
- 시장별로 다른 체결 비용/슬리피지 모델

### 4.6 수익 실현/청산 엔진

평균 MFE는 존재하는데 net이 음수인 경우가 많다. 이는 진입 후 한때 수익 기회가 있었지만, 포착하지 못했을 가능성을 의미한다.

필요한 것은 단일 TP/SL이 아니라 전략별 exit policy다.

- KR PathB Claude-price: 수익 구간에서 trailing/giveback 최소화
- US PathB: 빠른 scalp형인지 swing형인지 분리
- 뉴스 catalyst: 이벤트 소멸 시 exit
- momentum: 시간 정지 time-stop 필수
- MAE가 빠르게 커지는 진입은 early cut

### 4.7 뉴스 데이터의 사용 방식

뉴스는 강화해야 할 데이터지만, 지금 방식의 보너스는 위험하다.

관측:

- US `news_prompt_eligible` 후보의 ret60 평균은 약 -0.0211%로 약했다.
- US 실제 filled 중 news_in_prompt는 평균 gross -0.6757%, net -0.8666%, 승률 22.22%로 나빴다.
- 반대로 KR news_in_prompt는 표본 8건이지만 평균 net +0.8945%로 나쁘지 않았다.

해석:

- US 뉴스는 “좋은 뉴스라서 오른다”보다 “이미 오른 뉴스/과밀 뉴스/소음”일 가능성이 높다.
- `direct_catalyst`처럼 보이는 뉴스도 가격 반영 여부를 통제하지 않으면 추격 매수로 바뀐다.
- `news_or_earnings_sources` 존재 여부만으로 catalyst 취급하는 것은 과하다.

필요한 개선:

- 뉴스 존재 여부와 catalyst 강도를 분리
- stale/unknown/broad/newswire성 뉴스는 보너스 금지
- 가격 선반영 여부 gap/chase 체크
- 시장별 뉴스 정책 분리: US off 유지, KR은 shadow로만 확대 검증
- 뉴스는 score bonus가 아니라 “리스크/이벤트 태그 + 검증된 조건부 feature”로 저장

### 4.8 외부 데이터 백필

사용자가 말한 “MD에는 DB를 쌓는 것도 필요하지만, 사전 외부데이터로 채울 수 있으면 검증하고, 안 되는 건 포기하지 말고 개선 메모리에 저장” 원칙은 맞다.

현재 guardian 로그에도 `external_data.readiness production_ready=False total_data_rows=0 latest_api_run_at=2026-05-10T02:58:45`가 관측된다. 즉 외부 데이터 기반 사전 학습/백필 준비가 약하다.

필요한 외부 데이터:

- 종목별 과거 뉴스/공시와 timestamp
- intraday 가격/거래대금
- earnings calendar/surprise
- sector/market regime
- short interest/borrow 가능 시
- analyst rating/revision 가능 시
- ETF/테마 basket exposure

외부 데이터는 실시간 매매보다 먼저 offline 검증에 써야 한다. 검증 불가한 데이터는 포기 항목이 아니라 `improvement backlog`로 남겨야 한다.

## 5. 수정해야 하는가?

예. 다만 “매수를 늘리는 수정”이 아니라 “수익성 있는 매수만 살아남게 하는 수정”이 필요하다.

즉시 코드 변경 후보는 다음 순서가 맞다.

### P0: 손실 경로 확장 방지

- `CANDIDATE_PROMPT_POOL_REORDER_ENABLED=false` 유지
- `US_CATALYST_SCORE_BONUS_ENABLED=false` 유지
- US 뉴스 보너스/뉴스 기반 우선순위 승격 금지
- gap_pullback 계열 live 확장 금지
- Claude action 단독 BUY 권한 금지

### P1: 전략별 allowlist/denylist를 실제 매수 권한에 연결

현재 전체 평균으로 판단하면 좋은 경로가 묻히고, 나쁜 경로가 섞인다. 다음과 같이 분리해야 한다.

- KR `path_b/claude_price`: 제한적 live 유지 또는 소액 증액 후보
- KR 기타 PathB route: shadow 또는 block
- US `path_b/claude_price`: 신규 증액 금지, 조건 재검증
- US `momentum/opening_range_pullback`: small-n shadow 또는 매우 작은 probe
- US `gap_pullback`: block

### P2: 비용-aware Claude governor

다음 조건 중 하나라도 해당하면 expensive Claude selection을 줄여야 한다.

- 최근 N일 trade-ready 전환율이 임계값 미만
- 해당 시장/경로의 rolling net expectancy가 음수
- 같은 뉴스/후보 snapshot 재평가
- 장중 불리한 시간대
- 유동성/가격대/스프레드 조건 미충족

### P3: abstain audit

매수 안 한 후보가 이후 올랐는지/내렸는지를 매일 기록해야 한다. 이게 없으면 최근 무매수가 좋은 방어인지, 과도한 필터링인지 판단할 수 없다.

### P4: exit capture 개선

MFE가 있는데 net이 안 남는 경로를 찾아 strategy-specific exit을 붙여야 한다.

## 6. 목표 아키텍처

권장 구조:

```text
External data/backfill
  -> candidate feature store
  -> cheap quant prefilter
  -> Claude text/catalyst/risk extraction
  -> deterministic strategy gate
  -> execution/entry timing model
  -> portfolio sleeve allocator
  -> exit policy
  -> canonical realized PnL + AI cost attribution
  -> trainer/shadow challenger
```

핵심은 Claude가 가운데에 있지만 최종 권한자가 아니라는 점이다. Claude는 feature를 만들고 설명을 제공한다. 최종 매수는 검증된 strategy gate가 한다.

## 7. 30/60/90일 로드맵

### 0~7일

- 손실 경로 live 확장 금지
- 전략별 route 성과 리포트 자동화
- Claude 비용/호출 대비 trade-ready/filled 전환율 대시보드화
- abstain audit 추가
- US 뉴스 보너스 재활성 방지 테스트 추가
- KR PathB Claude-price만 별도 sleeve로 추적

### 2~4주

- 외부 뉴스/공시/earnings 데이터 백필 파이프라인 구축
- 후보 feature store 정규화
- 전략별 expected net model 생성
- 시간대/진입위치별 walk-forward 검증
- US momentum/opening-range 소액 probe 기준 정의
- MFE/MAE 기반 exit shadow replay

### 1~2개월

- 전략별 promotion contract 적용
- 비용 포함 objective로 trainer 재학습
- Claude prompt를 “매수 추천”이 아니라 “구조화 feature extraction” 중심으로 개편
- KR/US 완전 분리 정책 적용
- portfolio sleeve별 자본 배분 자동화

### 3개월

- live capital allocation을 성과 기반으로 자동 조절
- 수익 경로는 증액, 음수 경로는 자동 shadow 전환
- AI 비용이 기대수익 대비 과도하면 호출 자동 축소
- strategy challenger를 상시 운영

## 8. 최종 판단

지금 시스템이 수익성을 내려면 가장 부족한 것은 더 강한 Claude 판단이 아니다. “검증된 전략별 기대값을 기준으로 자본과 API 비용을 배분하는 엔진”이 부족하다.

현재 방향 중 맞는 부분:

- 검증 안 된 composite score 권한 축소
- prompt pool reorder 비활성
- US catalyst/news 보너스 비활성
- Claude 단독 매수 권한 제한
- shadow 검증 확대

현재 방향 중 부족한 부분:

- 방어 후 대체 수익 경로를 승격하는 체계가 약함
- 전략별 자본 sleeve가 약함
- API 비용을 expected value와 연결하지 않음
- abstain의 성과 측정이 약함
- 외부 데이터 백필/사전 검증 체계가 아직 production-ready가 아님
- exit capture가 충분히 최적화되지 않음

따라서 다음 개발의 중심은 “매수를 많이 하게 만드는 것”이 아니라 다음 한 문장이어야 한다.

> 전략별로 비용 차감 후 양의 기대값이 증명된 경우에만 Claude 비용과 매수 자본을 배정한다.

이 원칙으로 보면, 지금은 무리해서 `CANDIDATE_PROMPT_POOL_REORDER_ENABLED=true`나 뉴스 보너스를 켤 단계가 아니다. 먼저 전략별 수익 엔진, 비용 게이트, abstain audit, 외부 데이터 백필을 붙여야 한다.
