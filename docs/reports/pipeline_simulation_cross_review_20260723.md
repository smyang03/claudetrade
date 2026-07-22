# 전면 파이프라인 시뮬레이션 — 두 AI 분석 교차 검토 (2026-07-23)

대상:
- (A) `docs/reports/full_pipeline_simulation_review_20260723.md` + `tools/pipeline_simulation_matrix.py` — 타 AI
- (B) `docs/reports/pipeline_simulation_result_20260723.md` + `tools/pipeline_simulation.py` — 본 세션

두 하네스는 서로를 모른 채 같은 원장을 같은 방식(순수 함수 재생)으로 돌렸다.

## 1. 독립 재현으로 확정된 것 — 신뢰도 최상

숫자가 사실상 일치한다. 서로 다른 코드로 같은 값이 나왔으므로 실측으로 취급한다.

| 항목 | (A) | (B) |
|---|---|---|
| evidence 재생 충실도 | 99.83% | 99.83% |
| route 재생 충실도 | 98.77% / 98.60% | 98.77% / 98.60% |
| route 불일치 top | HARD_BLOCK→WATCH 232 · PULLBACK_WAIT→WATCH 136 | 동일 |
| US `volume_ratio_open`만 채움 | 24건 (0.3%) | 24건 (0.3%) |
| US `opening_range_break`만 채움 | 1,313건 | 1,313건 |
| US `vwap_distance_pct`만 채움 | 0건 | 0건 |
| rvol 대체 인정 | 19/3,704 | 19/3,702 |
| KR 3필드 전부 | 522건 (7.3%) | 522건 (7.3%) |
| 0~15분 confirmed | US 0.0% · KR 0.0% | 동일 |
| ceiling 강제 시 PlanA.buy 전환 | 13/39 | 13/39 |

**확정 결론 3개:**
1. **H1 반증** — `volume_ratio_open` 결측 해소의 ceiling 레버 효과는 0.3%다.
2. **H2 반증** — `time_normalized_rvol` 대체 인정 효과는 0.5%다. 필드 이원화는 실재하나 레버가 아니다.
3. **evidence 강등은 actionable 경로의 병목이 아니다** — 4단계 생존 US 98.66% · KR 96.40%.

어제 세션이 최우선으로 세운 가설 두 개가 독립 검증 두 번으로 죽었다.

## 2. (A)가 더 깊이 간 것 — 채택

### 2-1. 체결 truth를 `execution_decision_id`로 연결해 끊긴 지점을 특정했다

(B)는 "후보 원장 체결축이 5/08 이후 죽었다"까지만 봤다. (A)는 사슬의 어느 고리가
끊겼는지를 짚었다.

```
execution_decision_id   US 463행 · KR 38행   ← 붙는다
execution_event_id      US   0행 · KR  0행   ← 안 붙는다
filled_count>0          US   0행 · KR  0행   ← 안 붙는다
```

decision까지는 귀속되고 event/fill에서 끊긴다. 처방(`execution_decision_id` 기준
canonical/lifecycle backfill)도 (A)가 더 구체적이다. **(A) 채택.**

### 2-2. candidate 파이프라인 밖 체결의 존재를 짚었다

SCHG(`us_schg_bil_trend_v1`), 275280·275300(`kr_factor_trend_v1`)은 후보 파이프라인
소산이 아니다. (B)는 lifecycle FILLED 12건을 뭉뚱그렸는데 이건 잘못된 집계였다.
**(A) 채택.**

### 2-3. KR route 차단 사유 `WATCH:-` 124건을 관측성 결함으로 분류

사유 없는 강등을 P0 관측성 문제로 올린 판단은 타당하다. **(A) 채택.**

## 3. (B)가 더 깊이 간 것 — (A)의 결론 2개를 무효화

### 3-1. ★ (A)의 병목 #2와 P1-2는 퇴역한 경로를 분석한 것이다

(A)는 "프롬프트 진입 후 Claude actionable 전환율이 극히 낮다(2.31%)"를 5대 병목 중
2번으로, "KR에서 Claude가 적극 판단한 후보가 매수 경로로 거의 연결되지 않는다"를
P1-2로 올렸다. 그 근거인 `claude_action`은 **2026-07-08 운영자 결정(rule_direct 전환,
selection Claude 제거)으로 퇴역한 경로**다.

세션별 실측:

```
US claude_action=BUY_READY   7/01 68 · 7/02 82 · 7/06 193 · 7/07 31 · 7/22 13
                             → 386건 중 374건(96.9%)이 전환 이전
KR claude_action=BUY_READY   7/01 10 · 7/02 2 · 7/03 46 · 7/06 11 · 7/07 10 · 7/08 13
                             → 92건 전부 전환 이전. 7/09 이후 0건
```

(A)의 P1-2 근거인 "KR BUY_READY 92건 중 route missing 82건"은 **전량 7/08 이전 행**이다.
지금 돌지 않는 경로의 강등률을 고치자는 처방이 된다.

단서: (A)의 표에도 `actionable sessions = US 5 / KR 10` (전체 15세션)이 이미 찍혀 있었다.
15세션 중 5세션에서만 발화한다는 신호가 표 안에 있었는데 추적되지 않았다.

**정정된 사실:** 7/09 이후 `claude_action`은 selection이 아니라 judge 산출을 담는다.
judge가 거의 쏘지 않아 대부분 비어 있다. 7/22 US BUY_READY 13건은 실제 judge 산출이며
funnel 로그의 judge BUY_READY 5건과 정합한다.

### 3-2. 라이브 진입 경로의 실체 — (A)에 없는 축

라이브 결정은 `single_symbol_judge`이고 원장은 DB가 아니라 funnel 로그에 있다.

```
US 289콜  WAIT_RECHECK 244(84.4%) · PULLBACK_WAIT 40(13.8%) · BUY_READY 5(1.7%)
KR 212콜  WAIT_RECHECK 178(84.0%) · PULLBACK_WAIT 25(11.8%) · REJECT 9(4.2%)
```

세션당 10~39콜(대부분 정확히 10 = 캡 바인딩). KR은 7월 통틀어 BUY_READY 0건.
US WAIT_RECHECK의 36%가 "아직 판단할 재료가 없다"(`Only ~5.7min post-open,
momentum_state unknown, no opening range break`)다.

### 3-3. 체결축 단절 시점을 특정 — 7월 문제가 아니다

```
audit_candidate_rows filled_count>0 마지막 = 2026-05-08
lifecycle FILLED  5월 254 · 6월 306 · 7월 12
```

**6월 체결 306건도 후보 원장에 한 건도 없다.** (A)는 이걸 7월 현상으로 기술했으나
2.5개월째 끊긴 상태다. 백필 대상 규모가 달라진다.

## 4. 둘을 합쳐야 나오는 사실 — 가장 중요

(A)의 sleeve 발견과 (B)의 시점 추적을 합치면 두 리포트 어느 쪽도 명시하지 않은
결론이 나온다.

7월 canonical 체결 7건 전수:

```
07-02 US IREN     path_b/claude_price   net −2.64%   ← 후보 파이프라인
07-03 KR 003490   path_b/claude_price   net +0.83%   ← 후보 파이프라인
07-06 US NVDA     path_b/claude_price   net −2.41%   ← 후보 파이프라인
07-15 US SCHG     unknown/us_schg_bil_trend_v1       ← sleeve
07-16 US SCHG     unknown/us_schg_bil_trend_v1       ← sleeve
07-20 KR 275280   unknown/kr_factor_trend_v1         ← sleeve
07-21 KR 275300   unknown/kr_factor_trend_v1         ← sleeve
```

**후보 파이프라인에서 나온 마지막 체결은 2026-07-06 NVDA다. 이후 16거래일 연속 0건이다.**
7/15 이후 계좌를 움직인 건 전부 sleeve 레인이다.

즉 두 하네스가 정밀하게 시뮬레이션한 그 파이프라인은 **3주째 아무것도 체결하지 않았다.**
그리고 그 3건(IREN·NVDA·003490)의 net은 −2.64 / −2.41 / +0.83이다.

부수 규명 — (B)가 미규명으로 남긴 `ORDER_SENT 6 < FILLED 12`:
후보 파이프라인 체결은 ORDER_SENT + FILLED 쌍으로 찍히고, sleeve 체결(SCHG·275280·275300)은
**ORDER_SENT 없이 FILLED만** 발행한다. 이벤트 계약이 경로별로 다르다.

## 5. (A) 처방 중 재고가 필요한 것

### P1-1 "early 전용 evidence state 분리(`early_confirmed_without_or`)" — 우선순위 하향

0~15분 confirmed 0%는 두 하네스가 함께 확인했다. 다만 처방은 재고가 필요하다.

- 그 구간에 실제로 쓰이는 judge 예산은 US 6.9%(20콜)뿐이고, 그 20콜의 유효산출은 0%다.
  ORB 요구를 풀어 회수되는 양이 작다.
- 우리 시스템은 추격 전략이라 **약세/미형성 구간 진입의 승률이 0%**로 실측돼 있다
  ([[path-order-is-the-signal-20260722]]). ORB 형성 전 진입 허용은 그 방향이다.
- 즉시매수 기준 완화·눌림 완화는 이미 반증됐다(각각 세션 단위 소멸, −1.075%).

(A)가 "live 완화 금지, shadow 선행"으로 규율을 건 것은 맞다. 다만 P1으로 올릴 근거는
약하다. **관측 항목으로 유지.**

### P1-3 "prompt hard cap 탈락 후보 성과 분석" — 부분 중복

judge 예산 확대는 이미 반증됐고(세션 −0.128%), `tools/judge_capacity_drop_counterfactual.py`로
버려진 후보 반사실이 수행됐다. 재실행 시 그 결과부터 확인해야 중복을 피한다.

### 헤드라인 "5개 병목이 동시에 확인됐다" — 4개로 정정

#2(Claude actionable 전환율)는 퇴역 경로 통계다. 나머지 4개는 유효하다.

## 6. 합의된 P0 — 두 리포트가 같은 결론

(A) "가장 먼저 고칠 것은 매수 로직이 아니라 관측/귀속이다"
(B) "체결축 원장 복구가 최우선. 이게 죽어 있으면 어떤 개선도 검증 불가"

**일치한다.** 다만 (B) 관점을 더하면 순서가 하나 앞선다.

## 7. 통합 우선순위

1. **체결축 귀속 복구** — `execution_decision_id` 기준 backfill((A) 처방 채택).
   범위는 7월이 아니라 **5/08 이후 전체**(6월 306건 포함).
2. **후보 파이프라인 체결 0건(7/06 이후 16거래일) 규명** — 두 리포트가 정밀 측정한
   대상이 3주째 산출이 없다. judge 501콜 중 BUY_READY 5건이 그 직접 원인이고,
   그마저 체결로 이어지지 않았다. 여기가 수익 경로의 실제 지점이다.
3. **sleeve 레인 분리 계측** — 현재 계좌를 움직이는 유일한 경로인데 route='unknown',
   ORDER_SENT 미발행이라 후보 파이프라인과 같은 원장에서 안 보인다.
4. **KR `WATCH:-` 124건 관측성 결함**((A) 채택) — 단 퇴역 경로 행이 섞여 있으므로
   7/09 이후 행으로 한정해 재집계.
5. **US evidence 후반 결손** — 3시간+에도 confirmed 39.5%(KR은 69.4%). 타이밍으로
   설명되지 않는 US 전용 결손.
6. 종결: H1 · H2 · H3(배선 완화, 회수 4건). ORB 조기 완화는 관측 항목으로 하향.

## 8. 방법론 — 이번 교차 검토가 보여준 것

- **독립 재현은 강력하다.** 서로 모르는 두 하네스가 같은 값을 냈고, 그래서 H1/H2
  반증을 실측으로 확정할 수 있었다.
- **그러나 같은 사각지대는 함께 갖는다.** 둘 다 `claude_action`을 라이브 경로로 읽었다.
  숫자 검증이 아니라 **원장 정의 검증**이 이 함정을 잡았다. 재현성은 정의 오류를
  잡아주지 못한다.
- **표 안의 신호를 추적할 것.** `sessions = 5/15`는 (A)의 표에 이미 있었다.
