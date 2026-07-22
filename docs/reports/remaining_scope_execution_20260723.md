# 남은 범위 실행 결과 (2026-07-23)

`final_improvement_scope_20260723.md`의 잔여 항목을 실행했다.
**두 건은 "누수"가 아니라 표본 문제였다.** 고치기 전에 실측해서 잡았다.

---

## 1. P0-3 `mfe_time` 수집 — ★ 누수가 아니었다

앞선 보고에서 "6월 closed 152건 전량 `mfe_time` 0 = 3중 조건부 누락"이라고 썼다.
사슬을 단계별로 재보니 **첫 영속화에서 이미 시간축만 비어 있었다**:

```
v2_path_runs.plan_json (6월 이후 475건)
  observed_mfe_pct    37건
  observed_peak_price 37건
  observed_peak_at     0건   ← 같은 함수가 쓰는데 한쪽만 빔
```

같은 `_persist_observed_excursion()`이 쓰는데 한쪽만 0이면 예외이거나 falsy다.
그런데 실제 원인은 **시점**이었다:

```
시간축 코드 도입   커밋 010700b (2026-07-10)
observed_* 보유 path_run  7/02 IREN · 7/03 003490 · 7/06 NVDA — 전부 7/10 이전
7/10 이후 FILLED   SCHG·275280·275300(sleeve) · ONDS(PlanA) — PathB는 0건
```

**7/10 이후 PathB 체결이 한 건도 없어서 코드가 호출된 적이 없다.**
버그가 아니라 미실행이다. 고칠 것이 없다 — 다만 **검증도 안 된 상태**라
다음 PathB 체결에서 `observed_peak_at`이 실제로 찍히는지 확인해야 한다.

## 2. P1-6 — 이쪽이 진짜였다. 수정함

`_update_position_excursion()`은 PathB 청산 스캔에서만 호출되고 `pathb_path_run_id`가
없으면 영속화도 건너뛴다. 실측:

```
현재 보유 4종목 — SCHG · 275280 · 275300 (MICRO_PROBE) · ONDS (claude_price_a)
  pathb_path_run_id  전부 없음
  observed_* 키      전부 없음
```

**지금 보유한 포지션 전부가 MFE/MAE 미수집 상태다.** 초기경로·capture 분석의
입력이 통째로 비는 구간이 생긴다.

조치: `TradingBot._track_non_pathb_excursion()` 신설. housekeeping에서
`risk.update_prices()` 직후 실행, PathB 포지션은 건너뛴다(중복 방지).
`observed_*` 전용 키에만 쓰므로 ladder가 읽는 `peak_pnl_pct`는 무영향이고,
포지션 dict에 쓰므로 `_save_positions`로 자연히 durable해진다.

구동 검증:
```
ONDS   entry 8.37 → 8.60 : mfe +2.748  (peak_at 기록됨)
       이후 8.10 하락    : peak 8.60 유지 · low 8.10 · mae -3.226
PathB 포지션              : 건너뜀 (observed_* 미기록)
```

## 3. P0-4 sleeve net — 대부분 정상이었다

"sleeve 체결 4건 전부 net 0"이라고 썼는데, 실제로는:

```
SCHG   7/15  closed=1  pnl_pct -0.4487  net None   ← 진짜 갭
SCHG   7/16  closed=0                              ← 미청산, 정상
275280 7/20  closed=0                              ← 미청산, 정상
275300 7/21  closed=0                              ← 미청산, 정상
```

**3건은 아직 보유 중이라 net이 비는 게 맞다.** 갭은 청산된 1건뿐이었다.
기존 도구를 재사용해 해소:

```
python tools/backfill_net_apr_may.py --months 2026-07 --apply
→ SCHG 7/15  gross -0.449% → net -0.889%  (fee 0.44%, basis=backfilled_fee_only)
```

수수료 0.44%는 [[session-handoff-pipeline-leaks-20260723]]의 US 실측치(0.4390%)와 일치한다.

남은 것: `route='unknown'`은 그대로다. sleeve는 후보 파이프라인 밖이라 route가 없는 게
설계상 맞는지, 별도 라벨(`sleeve`)이 필요한지는 운영자 판단 영역이다.

## 4. P1-2 `cohort_reliability` — 수정함

후보에는 `trainer_cohort_reliability`로 붙고(`trading_bot.py:2948`)
`action_routing`·`candidate_post_rank`가 그 이름으로 읽는데, audit 컬럼은
`cohort_reliability`라 `_candidate_extra_value()`가 **한 번도 찾지 못했다**(0/91,556).

`_plan_a_signal_flags`와 같은 부류(이름 계약 불일치)라 **별칭 테이블**로 일반화했다:

```python
_CANDIDATE_COLUMN_ALIASES = {"cohort_reliability": ("trainer_cohort_reliability",)}
```

검증: 별칭 경유 0.73 · 정식 이름 0.5 · payload 경유 0.61 · 무관 컬럼 영향 없음.

## 5. P1-5 recheck 큐 정렬 — shadow만 구현

```python
trading_bot.py  selected_items = due[:max_per_run]   # 순수 FIFO
```

`_log_recheck_queue_order_shadow()` 신설. 현재 FIFO 선택과, 품질 점수
(`trainer_prompt_score` → `candidate_quality_score` → `raw_score_current`)로 정렬했을 때의
선택을 **둘 다 기록만** 한다. 주문 무영향.

구동 검증(합성): 큐 8건·예산 3에서 FIFO는 T0~T2, 정렬은 T5~T7 — **겹침 0**.
실제 큐에서도 겹침률이 낮으면 "무엇을 고르는가"가 실질 레버라는 뜻이다.

**enforce하지 않는다.** 사후 성과 비교가 호출 n=17·4세션으로 판정 불가라,
근거 없이 바꾸면 오늘 P0-1과 같은 실수가 된다. 15세션 관측 후 판단.

---

## 6. 미착수 — 사유와 함께

| 항목 | 사유 |
|---|---|
| P1-3 PathB raw plan 보존 | 원장 스키마/payload 구조 변경. 라이브 기록 경로라 단독 야간 변경은 위험 대비 이득이 낮다 |
| P1-4 reason attribution 3분할 | 동상. 컬럼 3개 신설 + 기록 경로 수정이라 별도 세션에서 설계 검토 후 |
| canonical ONDS 누락 | 세션 마감 동기화가 설계상 정상인지 확인 필요. 마감 후 실측해야 판정 가능 |

---

## 7. 이번 라운드 요약

```
실제 수정   P1-6 non-PathB excursion · P1-2 이름 계약 · P1-5 shadow · P0-4 net 1건
누수 아님   P0-3 mfe_time(표본 부재) · P0-4 3건(미청산이라 정상)
미착수      P1-3 · P1-4 · canonical 동기화 시점
```

**매매 행동을 바꾸는 변경은 없다.** 전부 기록·관측이거나 shadow다.

## 8. 방법론 — 아홉·열 번째

```
9  "mfe_time 3중 조건부 누락"     → 7/10 이후 PathB 체결 0건. 미실행이었다
10 "sleeve net 4건 전부 0"       → 3건은 미청산. 정상이었다
```

둘 다 **"0건"을 결함으로 읽은 것**이다. 오늘 canonical ONDS(진행 중 세션이라 비어 있음)와
같은 구조다. 가드에 하나 더 추가한다:

> **0건을 결함으로 부르기 전에 "그 코드가 실행될 조건이 있었는가"를 먼저 확인한다.**
> 미실행·미청산·미마감은 결함이 아니다.
