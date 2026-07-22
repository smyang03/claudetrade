# 최종 개선안 범위 (2026-07-23)

두 AI의 점검 결과를 합치고 상충·중복을 정리한 **단일 범위 문서**다.
각 항목은 근거 강도와 상태(완료 / 대기 / 반증)를 함께 적는다.

입력:
- (A) `pipeline_attribution_audit_20260723.md` 외 — 타 AI, 귀속 축
- (B) `deep_dive_four_axes_20260723.md` 외 — 본 세션, 데이터 흐름 축

---

## 0. 오늘 확정된 사실 — 내 1차 backfill이 원장을 오염시켰다

(A)의 지적이 **정확했다.** 실측으로 확인하고 교정했다.

```
1차 backfill 246건 중 오귀속 56건(22.8%)
  route=WATCH ∉ PathB.wait                      29
  route=빈값 / PlanA.buy / PlanA.probe ∉ PathB   16
  NO_SUBMIT(NO_SIGNAL·CLAUDE_PRICE_INVALID)      9
  후보 파이프라인 밖(sleeve) 체결인데 귀속됨        1
```

원인 넷:
1. canonical `route`(path_b/plan_a)와 후보 행 route 계열을 **대조하지 않았다**
2. "세션 내 최초 executable 행"을 골라 **체결보다 이른 행**에 붙였다
3. `no_submit_reason_code` 있는 행을 배제하지 않아 **"NO_SIGNAL이면서 FILLED"** 자기모순 행이 생겼다
4. `known_at`은 KST(+09:00), `earliest_fill_at`은 UTC(+00:00)인데 **문자열로 비교**했다
   → 시각 비교가 항상 어긋남(교정 후 `nearest_before_fill` 1건 → 222건)

**교정 완료**(커밋 `2bec8e3`): 해제 56 · 재귀속 60 · **정합성 위반 0건**.
IREN/003490은 `PathB.wait` 행으로 정정, NVDA는 해당 행이 없어 **미귀속**으로 남겼다.
sleeve 4건도 미귀속. 자기모순 행 잔존 0건.

> **원칙으로 고정**: 맞는 행이 없으면 억지로 붙이지 않는다.
> **틀린 귀속은 빈 칸보다 나쁘다** — 분석이 조용히 오염되고, 오늘처럼 나중에야 드러난다.

---

## 1. P0 — 즉시 (측정·귀속 정확성)

### P0-1. FILLED lifecycle 이벤트를 후보 원장에 직접 반영 【(A) 제안 · 채택】

`trading_bot.py:39978` `_candidate_audit_update_from_decision_event()`는
`buy_order`/`buy_signal`과 `sell_filled`/`sell_executed`만 처리한다.
**FILLED 이벤트를 처리하는 분기가 없다.** 그래서 `filled_count`가 라이브로 채워지지 않았고
5/08 이후 축이 죽은 것이다. backfill은 사후 봉합일 뿐 근본이 아니다.

조치: FILLED / PARTIAL_FILLED 분기 신설. 대상 행은 **resolver가 고른 행**이어야 한다.

### P0-2. fill target resolver를 라이브 경로에도 적용 【(A) 제안 · 채택】

`store.update_execution_by_ticker(...)`는 **ticker로 매칭**한다. 같은 ticker의 최신
WATCH/NO_SIGNAL 행에 체결이 붙을 수 있다 — 내 backfill이 낸 것과 **동일한 결함**이
라이브 경로에도 있다.

조치: `tools/backfill_candidate_fill_attribution.py:resolve_target()`의 규칙
(route 계열 대조 → no_submit 배제 → tz-aware 시각 근접)을 공용 함수로 빼서
라이브 기록 경로가 같은 규칙을 쓰게 한다.

### P0-3. `mfe_time`/`mae_time` 수집 복구 【(B) 확정】

```
6월 closed 152건 — mfe_pct 152/152 · mae_pct 152/152 · mfe_time 0/152 · mae_time 0/152
```
값 축은 완전한데 **시간 축만 통째로 빈다.** 초기경로·30분 마크 분석의 근거 축인데
라이브 수집이 0이다. 지금까지 결론은 전부 백필·대체 소스로 만든 것이다.

원인 3중 조건부: ① 신고점 갱신 없으면 `observed_peak_at` 미기록
② durable 영속화가 갱신 사이클에만 발생 ③ `mark_closed`에서 빈 문자열이면 키 생략.
조치: ②를 먼저 — 종료 시 1회 확정 기록.

### P0-4. sleeve 레인 `route`/net 귀속 【(B) 확정】

```
claude_price            체결 154 → net 보유 149 (97%)
us_schg_bil_trend_v1    체결   2 → net 보유   0,  route=unknown
kr_factor_trend_v1      체결   2 → net 보유   0,  route=unknown
```
7/15 이후 계좌를 움직인 유일한 레인이고 **현재 포지션 3개가 전부 여기 소속**인데
손익이 canonical에 net으로 안 잡힌다. 수익 레버가 아니라 **위험 관리**다.

---

## 2. P1 — 관측·계약

### P1-1. lifecycle FILLED 중복 dedupe 【(A) 제안 · 채택】

```
IREN 2026-07-02 · 003490 2026-07-03 · NVDA 2026-07-06  각 FILLED 2건
sleeve(SCHG·275280·275300)                              각 1건
```
후보 파이프라인 체결만 2건씩 발행된다. 이벤트 수로 체결을 세면 2배가 된다.

### P1-2. `cohort_reliability` 이름 계약 불일치 【(B) 신규】

후보에는 `trainer_cohort_reliability`로 붙고(`trading_bot.py:2948`)
`action_routing`·`candidate_post_rank`가 그 이름으로 읽는데,
audit 컬럼은 `cohort_reliability`라 **한 번도 기록되지 않는다**(0/91,556).
`_plan_a_signal_flags`와 **동일 유형**(이름 계약 불일치)이다.

### P1-3. PathB raw plan 보존 위치 【(A) 제안 · 채택】

가격계획이 `no_submit_block_meta_json.raw_plan`에만 남고 `payload_json`에 action/route
원본이 없어 PathB route/등록 재생이 안 된다.

### P1-4. reason attribution 3분할 【(A) 제안 · 채택】

`route_reason`·`route_runtime_gate_reason`·`no_submit_reason_code`가 분산돼 분석이 흔들린다.
`first_blocking_layer`·`final_blocking_layer`·`all_blocking_reasons`로 분리.
**(B) 보강**: 원장 ceiling과 재생 ceiling이 어긋나는 행이 실재하므로 저장 시점 스탬프 동반.

### P1-5. recheck 큐 정렬 shadow 【(B) 코드 확정 / 성과 미검증】

```python
trading_bot.py:32644   selected_items = due[:max_per_run]
```
큐 평균 23.4개에서 예산 10건을 **도착 순서**로 자른다. 우선순위·점수 없음.
dropped 421건 사유 100% `expired` — 품질 기반 축출 0건.

예산 확대는 기반증이지만 **"같은 10개를 무엇으로 고르는가"는 미검증 축**이다.
단 사후 성과는 호출 n=17·4세션으로 **판정 불가** → shadow 15세션 관측 후 판단.
**지금 enforce하지 않는다.**

### P1-6. `_update_position_excursion` 단일 호출점 【(B) 확정】

`pathb_runtime.py:3934` 한 곳에서만 호출되고 `pathb_path_run_id` 없으면 즉시 return.
PathB 외 포지션은 MFE/MAE가 아예 생성되지 않는다(`position_mfe_pct` 3/91,556).
sleeve 포지션이 정확히 여기 걸린다 — P0-4와 같은 뿌리.

---

## 3. 완료

| 항목 | 커밋 |
|---|---|
| 체결 귀속 오귀속 56건 교정 · 정합성 위반 0 | `2bec8e3` |
| 체결축 backfill(측정 복구) | `ce8ef6c` |
| `spread_bps` evidence 주입 · `vwap_distance_pct` runtime_gate 추가 | `ce8ef6c` |
| `_plan_a_signal_flags` 키 계약 교정(`*_fired`, `signals_evaluated`) | `ce8ef6c` |
| 도구 컬럼 존재 선확인(SQLite 문자열 리터럴 함정) | `ce8ef6c` |
| 반사실 A/B 보고 규칙 문서화 | `3d39632` |

---

## 4. 반증·종결 — 재시도 금지

| 항목 | 근거 |
|---|---|
| **H1** `volume_ratio_open` 결측 해소 | ceiling 회복 0.3%(24/7,520). 두 AI 독립 일치 |
| **H2** `time_normalized_rvol` 대체 인정 | 0.5%(19/3,704) |
| **H3** route `PlanA.probe` 배선 완화 | judge BUY_READY 386건 중 probe 4건 |
| **P0-1(구)** `data_quality` 미전달 강등 | 85,889행 A/B 변화 **0건**. 공변량이었다 |
| exit 중복억제 과소집계 | close_reason 분포 정상, 쏠림 없음 |
| judge 예산 확대 | 기반증(세션 −0.128%) |
| (A) "signal check에 runtime_gate 병합" | `sig_df`=`calc_all()` 출력에 해당 컬럼 부재. 룰은 정상 동작했고 NO_SIGNAL 77건 전부 정당한 판정. 병합하면 train/serve skew |
| `entry_delay_min`·`us_early_entry_size_mult` 미수집 | **정정**: 배선 누락이 아니라 `buy_order` 분기에서만 기록되는데 7월에 매수가 없어서다 |

---

## 5. 미규명 — 다음 확인

- **ONDS 2026-07-22 FILLED 이벤트**가 lifecycle에는 있는데 canonical July filled(7건)에 없다.
  귀속 갭이 하나 더 있다.
- `no_row_with_route_in(PathB.wait)` 52건 — PathB 체결인데 후보 원장에 `PathB.wait` 행이
  아예 없다. PathB 등록이 후보 행을 만들지 않는 경로가 있는지 확인 필요.
- 매도 방향 주입 검증(7월 청산 4건뿐 → 6월 180건 구간 필요)

---

## 6. 적용 순서

```
1  P0-1  FILLED 이벤트 → 후보 원장 직접 반영      근본 수정. backfill은 봉합일 뿐
2  P0-2  resolver를 라이브 경로에 공용화          1과 한 묶음. 안 하면 같은 오귀속 재발
3  P0-3  mfe_time/mae_time 수집 복구             초기경로 분석의 근거 축
4  P0-4  sleeve route/net 귀속                   실제 돈이 있는 경로가 계측 밖
5  P1-2  cohort_reliability 이름 계약            단순·저위험
6  P1-1  FILLED 중복 dedupe
7  P1-4  reason attribution 3분할                이후 분석의 기반
8  P1-3  PathB raw plan 보존
9  P1-5  recheck 큐 정렬 shadow                  15세션 관측 후 판단
10 P1-6  _update_position_excursion 호출점       P0-4와 함께
```

**1·2가 최우선인 이유**: 지금 라이브 경로는 여전히 ticker 매칭이라, 내일 체결이 나면
오늘 교정한 것과 **같은 오귀속이 다시 생긴다.** backfill로 계속 뒤따라가는 건 해법이 아니다.

**매매 행동을 바꾸는 항목은 이 범위에 없다.** 전부 기록·귀속·관측이고, P1-5만 shadow다.

---

## 7. 방법론 — 오늘 배운 것

내 진단이 오늘 **여섯 번** 틀렸고 전부 실측·타 AI 지적으로 교정됐다.

```
1 DELL 사인 → 코어 모멘텀이었다
2 momentum_state 라벨 → pack에 키가 없었다
3 atr_pct placeholder → 컬럼 부재(SQLite 문자열 리터럴 함정)
4 data_quality 강등 원인 → 공변량, 효과 0
5 recheck "383건 증발" → consume 스키마 오독, 실제 31.9% 재호출
6 체결 backfill 귀속 → route 계열·시각·tz 미대조로 22.8% 오귀속
```

공통 구조: **스키마·계약을 확인하기 전에 인과를 보고했다.**
6번은 내가 못 잡고 타 AI가 잡았다. 교차 검토가 실제로 작동했다.

고정한 가드:
- 컬럼 존재 선확인(도구 반영)
- 반사실은 실데이터 A/B로 delta를 낸 뒤 보고(도구 문서에 계약)
- **시각 비교 전 timezone 확인** — 오늘 신규 추가
- 귀속은 맞는 대상이 없으면 비워 둔다
