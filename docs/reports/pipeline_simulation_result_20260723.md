# 전면 파이프라인 시뮬레이션 결과 (2026-07-23)

대상: 2026-07-01~07-22, 후보 90,100행(US 66,550 / KR 23,550) + judge 원장 501콜.
도구: `tools/pipeline_simulation.py` (신설, 읽기 전용).

## 0. 하네스 충실도 — 결론보다 먼저 증명한 것

원장에 evidence 계산의 실제 입력(`post_open_features_json`)과 route의 실제
execution_context(`payload_json.runtime_gate`)가 남아 있어, 라이브 판정을 **API 호출 없이
순수 함수로 재생**할 수 있었다.

```
evidence data_state    32,095/32,151 (99.83%)
evidence action_ceiling 32,095/32,151 (99.83%)
route final_action     31,756/32,151 (98.77%)
route 문자열            31,701/32,151 (98.60%)
```

불일치는 스냅샷 시점차(features가 재계산된 뒤 원장이 쓰인 경우)와 HARD_BLOCK 232건이다.
이 충실도가 아래 반사실 결론의 근거다. 낮았다면 전부 무효로 봐야 했다.

## 1. ★ 어제 진단의 전제가 틀렸다 — 죽은 경로를 보고 있었다

어제 세션은 `US 후보 65,241 → 프롬프트 25,533 → trade_ready 256 → filled 0`을 근거로
"Claude가 trade_ready를 내는데 evidence 강등으로 사멸"이라고 결론냈다.

**그 `claude_action`/`claude_trade_ready`는 2026-07-08 운영자 결정(rule_direct 전환,
selection Claude 제거)으로 퇴역한 경로다.**

```
US selection 액션 (프롬프트 노출 건 기준)
  7/01~7/07  BUY_READY 374 · PULLBACK_WAIT 145 · PROBE_READY 79 · WATCH 2,546
  7/16~7/21  WATCH 7,228 — 예외 0건
KR actionable  7/09~7/15 5세션 연속 0건
claude_trade_ready  7/08~7/21 전 세션 0, 7/22 US만 8
```

7/08 이후 selection이 전량 WATCH인 것은 **결함이 아니라 설계 이행**이다.
어제의 "trade_ready 256"은 7/01~7/07 잔재였고, 그 뒤에 붙은 "evidence가 죽였다"는
인과는 성립하지 않는다.

**라이브 진입을 정하는 건 `single_symbol_judge`이고, 그 원장은 DB가 아니라
`logs/funnel/single_symbol_judge_*.jsonl`에 있다.**

## 2. 라이브 경로의 실체 — 월 501콜, BUY_READY 5건

```
US  총 289콜  WAIT_RECHECK 244(84.4%) · PULLBACK_WAIT 40(13.8%) · BUY_READY 5(1.7%)
KR  총 212콜  WAIT_RECHECK 178(84.0%) · PULLBACK_WAIT 25(11.8%) · REJECT 9(4.2%)
```

세션당 10~39콜(대부분 정확히 10 = 캡 바인딩). BUY_READY 5건은 전부 7/22 US다.
KR은 7월 통틀어 BUY_READY 0건.

judge가 기다리는 이유는 두 계열로 갈린다:

```
US WAIT_RECHECK 244건   데이터부재/조기 88(36%) · 모멘텀소멸 73(30%) · 기타 83(34%)
KR WAIT_RECHECK 178건   모멘텀소멸 98(55%) · 데이터부재/조기 39(22%) · 기타 41(23%)
```

"데이터부재/조기" 실제 문구: `Only ~5.7min post-open, momentum_state unknown, no opening
range break` / `Only 0.28 min since open with first_observed data` / `Data quality is
preopen_anchor with all post-open features flat`.

즉 **US judge 호출의 36%가 "아직 판단할 재료가 없다"에 소모된다.**

## 3. 예산이 언제 쓰이고 무엇을 낳는가

```
US (n=289)                                     유효산출(BUY_READY+PULLBACK_WAIT)
  0~10분    20콜 (예산 6.9%)                     0.0%
  10~20분   46콜 (15.9%)                        13.0%
  20~40분   33콜 (11.4%)                        15.2%
  40~90분   69콜 (23.9%)                        18.8%
  90분+    121콜 (41.9%)                        17.4%

KR (n=212)
  0~10분    14콜 (6.6%)                         14.3%
  10~20분   33콜 (15.6%)                         9.1%
  20~40분   16콜 (7.5%)                         25.0%
  40~90분   61콜 (28.8%)                        19.7%
  90분+     88콜 (41.5%)                         4.5%
```

US 0~10분 구간은 20콜 전부 WAIT_RECHECK — **유효산출 0%**. OR 창(US 15분·KR 10분)이
닫히기 전이라 `opening_range_break`가 설계상 존재할 수 없는 시점이다.

단 규모는 크지 않다(예산 6.9%). 이걸 옮겨도 회수는 20콜 남짓이다.
KR 90분+ 구간(예산 41.5%, 유효산출 4.5%)이 더 크지만, **"시간대 재배분"은 어제
반증된 축**이므로 새 제안으로 올리지 않고 관측 항목으로만 남긴다.

## 4. ★ 어제 세운 가설 H1·H2 반증

강등된 행(US 7,520 · KR 7,118)에 결측 필드를 하나씩 채워 ceiling을 재계산했다.

```
US 강등 7,520건
  volume_ratio_open만 채움      → BUY_READY 회복     24건 ( 0.3%)   ← H1 반증
  opening_range_break만 채움    → BUY_READY 회복  1,313건 (17.5%)
  vwap_distance_pct만 채움      → BUY_READY 회복      0건 ( 0.0%)
  확인 3필드 전부 채움           → BUY_READY 회복  1,337건 (17.8%)  ← 상한
  time_normalized_rvol 대체 인정 → 회복 19건/3,702건  ( 0.5%)      ← H2 반증

KR 강등 7,118건
  volume_ratio_open만            8건 (0.1%) · opening_range_break만 502건 (7.1%)
  3필드 전부 522건 (7.3%) · rvol 대체 3건
```

- **H1(`volume_ratio_open` 결측만 해소하면 강등이 풀린다) — 반증.** 0.3%다.
- **H2(`time_normalized_rvol`을 evidence가 인정하면 즉시 해결) — 반증.** 0.5%다.
  필드 이원화는 실재하지만 ceiling에 대한 레버가 아니다.
- 회복 동인은 `opening_range_break` 단독(17.5%)인데, 이건 채울 수 있는 값이 아니라
  **OR 창이 닫히기를 기다려야 생기는 값**이다. 데이터 결함이 아니라 타이밍이다.

시점별로 보면 명확하다:
```
US  0~15분 confirmed  0.0% (ORB 결측 82.8%) → 3시간+ confirmed 39.5%
KR  0~15분 confirmed  0.0% (ORB 결측 86.5%) → 30~60분 confirmed 69.4%
```

부수 발견: **US는 3시간이 지나도 confirmed가 39.5%에 그친다**(KR은 69%까지 회복).
US 쪽 `volume_ratio_open`/`vwap_distance_pct`가 장 중반 이후에도 53% 결측이다.
이건 타이밍으로 설명되지 않는 US 전용 결손이라 별도 과제다.

## 5. evidence 강등은 actionable 경로의 병목이 아니다

9단계 매트릭스에서 4단계(evidence) 생존율은 **US 98.66% · KR 96.40%**, 차단 8건이다.
어제 인용된 "강등 7,423건"은 **애초에 Claude가 WATCH를 낸 행까지 포함한 수**라
진입 차단량으로 읽으면 안 된다.

```
US  후보 66,439 → 프롬프트 25,852(38.9%) → judge액션 598(2.3%) → evidence 590(98.7%)
    → route 493(83.6%) → 진입배선 343(69.6%) → 안전게이트 276(80.5%) → 체결 0
KR  23,550 → 13,421(57.0%) → 222(1.7%) → 214(96.4%) → 39(18.2%) → 10(25.6%) → 10 → 0
```

6단계 진입배선 차단 사유는 `route=PathB.wait 78건` · `route=PlanA.probe 72건`.
어제 지목한 PlanA.probe 미매칭은 실재하나, judge BUY_READY 385건 중 probe로 흐른 건은
**4건**이다(346건은 이미 PlanA.buy였다). H3(강등≠금지 배선 완화)의 회수량은 작다.

## 6. ★ 새로 발견한 누수 — 후보 원장의 체결축이 2026-05-08 이후 사망

```
audit_candidate_rows: filled_count>0 마지막 세션 = 2026-05-08
  since 07-01: 행 90,100 · filled>0 0건 · entry_price 3건

대조군 lifecycle_events(실체결 원장)
  2026-05  ORDER_SENT 250 · FILLED 254 · CLOSED 115
  2026-06  ORDER_SENT 319 · FILLED 306 · CLOSED 180
  2026-07  ORDER_SENT   6 · FILLED  12 · CLOSED   4
```

**6월 체결 306건이 후보 원장에 한 건도 반영되지 않았다.** 즉 5/08 이후
"후보 → 체결" 귀속 분석은 불가능한 상태였고, 그 죽은 컬럼을 0으로 읽으면
"체결 0건"이라는 잘못된 진단이 나온다. 어제 결론이 정확히 그 경로였다.

`candidate_audit_outcome_update` 잡은 지금도 5분마다 돌지만 forward-return 라벨
(`audit_candidate_outcomes`)만 갱신한다 — 체결 백필은 별개 경로이고 그게 끊겼다.

부수 이상: 7월 `ORDER_SENT 6 < FILLED 12`. 이벤트 발행 경로가 일치하지 않는다(미규명).

## 7. 그래서 7월에 실제로 무슨 일이 있었나

```
FILLED   6월 306건 → 7월 12건 (96% 감소)
CLOSED   6월 180건 → 7월  4건
CLAUDE_PRICE_PLAN_CREATED  6월 445건 → 7월 30건
```

이건 [[pathb-plan-collapse-july-20260722]]가 기록한 "플랜 생성 96% 붕괴"와 같은 사건이며,
시뮬레이션은 그것이 PathB 플랜에 국한되지 않고 **진입 퍼널 전체의 붕괴**임을 보여준다.
7/08 rule_direct 전환 이후 selection이 닫혔는데, 그 자리를 받기로 한 judge 경로는
세션당 10콜·유효산출 15% 규모라 공백을 메우지 못했다.

## 8. 판정과 다음 행동

**병목은 evidence 강등도, 필드 이원화도, 배선 미매칭도 아니다.
진입 결정의 처리량 자체가 세션당 10~39콜이고 그 84%가 WAIT_RECHECK로 끝난다.**

수익 방향의 다음 경로 — 우선순위 순:

1. **체결축 원장 복구(최우선, 측정 인프라).** 후보→체결 귀속이 죽어 있으면 이후 어떤
   개선도 검증할 수 없다. 5/08에 무엇이 끊겼는지부터 실측한다. 6월 306건 백필 가능.
2. **judge 처리량이 아니라 WAIT_RECHECK 84%의 내용을 판다.** 예산 확대는 이미 반증됐다
   (세션 −0.128%). 대신 WAIT_RECHECK된 종목이 이후 어떻게 됐는지를 붙여
   "기다린 게 옳았는가"를 세션 단위로 판정한다. `early_judge_recheck_consume` 원장이 있다.
3. **US evidence 후반 결손**(3시간+에도 confirmed 39.5%, KR은 69%) 규명. 타이밍으로
   설명되지 않는 US 전용 결손이다.
4. H1·H2·H3은 반증·소량으로 종결. `volume_ratio_open` 백필과 rvol 필드 통합은
   ceiling 레버가 아니다(계약 정리 자체는 별개 가치).

## 9. 방법론 — 이번에 확인된 것

- **원장 정의를 먼저 확인한다.** `claude_action`이 퇴역 경로인 걸 모른 채 읽으면
  "Claude가 판단을 멈췄다"는 정반대 결론이 나온다. 이번에 두 번 걸렸다(퇴역 경로, 죽은 체결축).
- **재생 충실도를 먼저 증명한다.** 99.8%를 확인한 뒤에야 반사실을 신뢰할 수 있었다.
- **대조군을 둔다.** 후보 원장의 체결 0은 lifecycle_events와 대조해서야 결함으로 판명됐다.
- 하네스는 재사용 가능하다: `python tools/pipeline_simulation.py --since 2026-07-01`,
  충실도만 볼 때는 `--fidelity`.
