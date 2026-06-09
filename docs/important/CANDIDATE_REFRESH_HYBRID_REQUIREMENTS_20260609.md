# Candidate Refresh Hybrid Requirements - 2026-06-09

## 1. Final Judgment

최종 판정은 명확하다.

`1. 주문 직전 조건부 single confirm`, `2. smart skip context hash 보강`, `3. 강한 sub-screener 발생 시 full rescreen 허용`을 하나의 패키지로 구현하면 현재 버전 대비 구조 개선이다.

다만 이 구조는 6/2 대비 단점이 전혀 없는 전면 우위 구조가 아니다. 6/2의 단순하고 강한 재후보 탐색력을 일부 조건부 로직으로 대체하기 때문에 복잡도, trigger 품질 의존, confirm latency, config 불일치 리스크가 생긴다. 최종 목적은 Claude 사용량을 지금 수준으로 극단적으로 억제하는 것이 아니라, 지금보다 조금 늘더라도 strong 신규 후보의 재경쟁 지연을 제거하는 것이다.

따라서 최종 설계의 핵심 조건은 아래 하나다.

```text
buy-time confirm은 full pool refresh의 대체재가 아니다.
full pool freshness와 strong-trigger full rescreen을 반드시 같이 유지한다.
```

이 조건을 지키면 구조 개선이다. 이 조건을 어기고 `오래된 후보 + 주문 직전 single confirm`만 두면 6/2보다 나빠진다.

## 2. Scope

이번 요구서는 후보 선정 freshness와 주문 직전 후보 검증 구조를 개선하기 위한 코드레벨 설계 기준이다.

직접 대상:

- `trading_bot.py`의 sub-screener, scheduled rescreen, selection meta 적용, Path A 주문 직전 흐름
- `minority_report/analysts.py`의 Claude selection prompt pool, smart skip 호출, selection meta 기록
- `runtime/selection_smart_skip.py`의 semantic signature, reuse 조건, audit state
- `runtime/candidate_prompt_pool.py`의 prompt pool cap/metrics
- `execution/single_symbol_judge.py`와 분리된 신규 `execution/buy_time_confirm_judge.py`
- `runtime/pathb_runtime.py`의 PathB zone hit 이후 context drift audit (1차 buy-time confirm 미호출)
- 관련 tests, audit/log/report visibility

직접 비대상 및 보호 영역:

- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard 완화 금지
- PathB broker-truth entry fail-closed 완화 금지
- PathB sizing reason split, one-share policy, fixed sizing 정책 변경 금지
- KIS order normalization, `remaining_qty` 계약 변경 금지
- zero-holding stale reconcile 변경 금지
- hard stop, loss cap, profit ladder, pre-close sell 구조 변경 금지
- `state/brain.json` 직접 수정 또는 자동 정책 메모리 승격 금지

이번 작업은 selection 품질과 후보 freshness 문제다. 주문 수량, 주문 금액, broker truth, hard risk gate 문제와 섞지 않는다.

## 3. 6/2 구조 흐름

기준 커밋: `bf81d26` (`2026-06-02 21:44:48 +0900`)

6/2의 체감 장점은 "30분마다 무조건 Claude full selection"이 아니라, 아래 흐름 때문에 신규 후보가 빠르게 다시 Claude 판단까지 올라간 점이다.

```text
entry scan
  -> maybe_run_sub_screener()
  -> _screen_market_candidates(force_refresh=True)
  -> sub_screener.scan_new_candidates()
  -> result.should_trigger
  -> sub_screener.record_attempt()
  -> _reinvoke_analysts(market, "sub_screener")
  -> manual_rescreen(source_type="sub_screener_rescreen", candidate_override=screener_rows)
  -> select_tickers full Claude call
  -> watchlist / trade_ready / PathB price plan 반영
```

정시 rescreen도 존재했다.

```text
schedule.every(_RESCREEN_SCHEDULE_TICK_MIN=30).minutes.do(run_rescreen)
  -> run_rescreen()
  -> _RESCREEN_INTERVAL_MIN=60 guard
  -> manual_rescreen(source_type="rescreen")
```

즉 스케줄 tick은 30분이지만 full scheduled rescreen 기본 간격은 60분이었다. 6/2의 실제 강점은 scheduled rescreen보다 `strong sub-screener trigger -> analyst reinvoke -> manual_rescreen` 경로가 단순하고 직접적이었다는 점이다.

6/2 장점:

- 신규 강한 후보 발견 시 Claude full reselection로 바로 연결됨
- 후보 간 우선순위가 다시 계산됨
- stale watchlist가 오래 버티는 위험이 작음
- 흐름이 단순해서 trigger 누락 가능성이 낮음

6/2 단점:

- Claude 사용량이 크다
- 비슷한 후보군에 대한 반복 판단이 많다
- 주문 시점에서 "이 후보가 지금도 맞는가"를 별도로 방어하지 못한다
- 새 안전장치, PathB 플랜 안정화, smart skip 비용 절감 이점이 부족하다

## 4. 현재 구조 흐름

현재 HEAD: `e41bf87` (`2026-06-09 02:49:25 +0900`)

현재 sub-screener 흐름은 아래처럼 바뀌었다.

```text
maybe_run_sub_screener()
  -> _screen_market_candidates(force_refresh=True)
  -> sub_screener.scan_new_candidates()
  -> result.should_trigger
  -> duplicate/early judge/triage 처리
  -> _apply_sub_screener_triage() 성공 시 return
  -> triage disabled/fail and fail-open enabled일 때만
       _reinvoke_analysts()
       manual_rescreen(candidate_override=screener_rows)
```

현재 설정상 주요 값:

```text
SUB_SCREENER_ENABLED=true
SUB_SCREENER_INTERVAL_MIN=15
SUB_SCREENER_MIN_INTERVAL_MIN=20
SUB_SCREENER_MAX_PER_SESSION=5
SUB_SCREENER_PLAN_A_THRESHOLD=1
SUB_SCREENER_PLAN_A_MIN_SCORE=70
SUB_SCREENER_PLAN_B_THRESHOLD=2
SUB_SCREENER_PLAN_B_MIN_SCORE=65
SUB_SCREENER_TRIAGE_ENABLED=true
SUB_SCREENER_TRIAGE_FAIL_OPEN_FULL_RESCREEN=true
```

현재 smart skip 흐름:

```text
select_tickers()
  -> prompt_candidates 구성
  -> selection_smart_skip.semantic_signature()
  -> maybe_reuse()
  -> reuse 가능하면 full Claude call 생략
  -> 아니면 Claude full selection
```

현재 smart skip signature는 후보 ticker, evidence class, action ceiling, trainer state, source role, score bucket, mode, phase, cap, config hash, lesson hash 중심이다.

문제는 시장 맥락 변화가 충분히 들어가지 않는다는 점이다. 현재 signature는 `market_change_pct`, `secondary_change_pct`, breadth, 30분 slope, risk mode, intraday context 변화, preopen/news enrichment 변화 등을 충분히 반영하지 못한다.

현재 single-symbol judge 흐름:

```text
execution/single_symbol_judge.py
  -> allowed action: PULLBACK_WAIT, WAIT_RECHECK, REJECT
  -> BUY_READY / PROBE_READY 금지
  -> PathB waiting price plan 생성용
```

따라서 현재 `single_symbol_judge`는 주문 직전 Plan A 매수 확정용이 아니다. buy-time confirm은 별도 모듈이어야 한다.

현재 구조의 개선점:

- Claude 사용량 절감
- PathB price plan fast-pass와 soft-block 복구 경로 생김
- selection output compact cap으로 비용 관리
- broker/risk/order safety guard가 6/2보다 강함

현재 구조의 후퇴점:

- 강한 신규 후보가 triage로만 흡수되고 full reselection까지 가지 않을 수 있음
- full pool 재우선순위가 6/2보다 느림
- smart skip이 context 변화를 놓치면 stale 후보가 더 오래 유지됨
- PathB reconcile은 `_smart_skip_reused`일 때 스킵되므로 smart skip reuse가 PathB plan freshness에도 영향을 준다

## 5. Root Cause

나빠지는 구조는 아래다.

```text
full pool refresh 축소
  -> 후보군 오래 유지
  -> 주문 직전 single confirm만 믿음
  -> 더 좋은 신규 후보를 못 봄
```

이 구조는 개선안이 아니다. single confirm은 "이미 선정된 후보가 지금도 유효한지"만 검증한다. 새 후보 발견과 후보 간 우선순위 재설정은 full selection 또는 full rescreen만 할 수 있다.

따라서 최종 구조는 역할을 분리해야 한다.

```text
30분 pool freshness:
  새 후보 발견, 후보 간 우선순위 재설정, Claude selection 재판단

smart skip context hash:
  같은 상황에서만 Claude full call 생략

strong sub-screener full rescreen:
  30분 cadence 안에서도 강한 신규 후보를 full selection으로 올림

buy-time confirm:
  주문 직전 이 후보가 지금도 맞는지 확인
```

## 6. Target Flow

최종 목표 흐름:

```text
raw screener / preopen / sub-screener
  -> cheap rank + trainer + preopen pin
  -> Claude prompt pool 35~40
  -> smart skip context/core/tail 판단
       context 변화 없음 + core 변화 없음 + strong 신규 후보 없음
         -> reuse 가능
       context 변화 있음 or core 변화 있음 or strong 신규 후보 있음
         -> Claude full selection
  -> compact output
       watchlist max 15
       trade_ready max 5
  -> Path A / Path B routing
  -> Path A 매수 신호 발생
       selection age < 30분 and context unchanged  (임시: pre-open age reset 전까지 30분)
         -> confirm 생략, 기존 order gate 진행
       selection age >= 30분 or context changed
         -> buy_time_confirm_judge (CONFIRM_BUY / DEFER / REJECT)
         -> timeout/parse fail → CONFIRM_UNAVAILABLE_PROCEED
            + adverse context 없으면 진행, adverse context 이면 DEFER
       selection age >= 60분
         -> single confirm만으로 매수 금지, full_rescreen_requested + DEFER
  -> Path B zone hit
       context unchanged (context_hash_at_creation 비교 기준)
         -> context_drift=false audit, 기존 path 진행
       risk_mode 변화 or < -3% 급락
         -> context_drift=true audit, 기존 path 진행
  -> 기존 risk / affordability / broker truth / safety / precheck / place_order
```

이 구조가 개선인 이유:

- 6/2의 신규 후보 발견력을 strong-trigger full rescreen으로 복구한다
- 현재 버전의 Claude 절감 효과는 context-aware smart skip으로 유지한다
- 6/2에 없던 주문 직전 stale 후보 방어가 추가된다
- prompt pool을 40까지 넓혀 후보 recall을 개선하되, 최종 watch/trade cap은 유지해서 매수 남발을 막는다

## 6.1. This Is Not Another Buy-Blocking Safety Gate

이 요구서의 confirm/freshness 구조는 오늘 새벽에 문제를 만든 "매수 차단 안전장치 추가" 계열로 구현하면 안 된다.

목표는 매수를 더 어렵게 만드는 것이 아니라, 오래된 후보와 신규 강한 후보 미탐색 문제를 분리해서 고치는 것이다.

구분:

```text
나쁜 구현:
  기존 risk/safety gate 앞에 Claude confirm을 항상 추가
  -> fresh momentum/breakout도 2~3초 대기
  -> DEFER/timeout/parse 실패가 또 다른 매수 차단 사유가 됨
  -> 오늘 새벽 문제와 같은 계열

좋은 구현:
  fresh selection + context unchanged
    -> confirm 호출 없음
    -> 기존 주문 흐름 그대로 진행

  stale/context changed
    -> 이 후보가 지금도 맞는지 확인
    -> CONFIRM_BUY면 기존 주문 흐름 진행
    -> DEFER면 full rescreen 또는 재확인으로 돌림
    -> REJECT면 오래된 후보만 제외
```

따라서 buy-time confirm은 safety gate가 아니라 freshness gate다. 기존 broker/risk/sizing/safety gate를 강화하거나 새 hard block을 넓히는 장치가 아니다.

운영 원칙:

```text
fresh 후보는 더 빨라져야 한다.
stale 후보만 더 신중해야 한다.
신규 강한 후보는 single confirm이 아니라 full rescreen으로 발견해야 한다.
```

Acceptance:

- fresh 후보에서 confirm 호출이 발생하면 실패다
- confirm timeout/parse fail이 fresh 후보 주문을 막으면 실패다
- confirm이 수량, 주문금액, risk/broker gate를 바꾸면 실패다
- stale 후보의 `DEFER`는 "매수 금지"가 아니라 `full_rescreen_requested` 또는 `recheck_requested`로 기록되어야 한다
- buy block reason이 늘어나는 효과가 관찰되면 confirm threshold를 완화하거나 fresh skip 조건을 수정해야 한다

## 7. Code-Level Requirements

### R1. 30분 pool freshness를 복구한다

목표:

- 30분마다 적어도 raw/screener/trainer pool freshness를 재평가한다
- Claude full call은 매번 하지 않는다
- context/core/strong trigger가 있으면 full selection으로 올라간다

기존 시스템과의 중복 검토:

```text
현재 이미 작동 중인 메커니즘:
  - sub-screener: SUB_SCREENER_INTERVAL_MIN=15 → 15분마다 raw screener 수집
  - smart skip TTL: SELECTION_SMART_SKIP_TTL_MIN=30 → TTL 내에서만 캐시 유효. TTL 만료 시 full selection 강제 (context hash 무관)

이 두 메커니즘이 이미 pool freshness 역할을 나눠서 담당한다.
SELECTION_POOL_FRESHNESS_MIN=30을 별도 세 번째 타이머로 구현하면 복잡도만 늘어난다.
```

실질적 요구:

```text
R1의 목표는 별도 타이머 구현이 아니라 역할 분리 명문화다.

pool freshness (원시 데이터 재수집) = sub-screener 15분 주기 시도가 담당
  단, rate-limit / session cap / min_interval / close blackout 시 실제 refresh가 보장되지 않음
  → 차단 시 pool_freshness_unknown으로 audit 기록 필요

Claude call freshness (캐시 유효 기간) = smart skip TTL 30분이 담당 (TTL 내만 reuse 가능, 만료 시 full call)
full selection 조건 (TTL 내에서 언제 full call로 갈 것인가) = R2 context hash + R3 strong trigger가 담당

RESCREEN_INTERVAL_MIN은 단순 full Claude interval로만 쓰지 말고,
위 세 역할을 명확히 분리해서 운영한다.
```

smart skip TTL과의 관계:

```text
SELECTION_SMART_SKIP_TTL_MIN=30  (기존 smart skip TTL)

실제 코드 동작 (runtime/selection_smart_skip.py):
  TTL 안: context/core hash 일치 여부로 reuse 결정
  TTL 만료: ttl_expired fail-open → 무조건 full selection 실행 (context hash 무관)

즉 TTL은 reuse를 "허용"하는 것이 아니라 캐시 유효 기간이다.
TTL 만료 = 무조건 fresh Claude call. R2 context hash는 TTL 안에서만 작동한다.

pool fresh → TTL 안 → context/core hash 일치 → reuse 가능
pool fresh → TTL 안 → context/core hash 불일치 → full call
pool fresh → TTL 만료 → full call (hash 무관)
```

코드 대상:

- `trading_bot.py::run_rescreen` (RESCREEN_INTERVAL_MIN 역할 재정의)
- `trading_bot.py::maybe_run_sub_screener` (R3와 연동)
- `minority_report/analysts.py::select_tickers` (pool freshness와 Claude call 분리)

Acceptance:

- sub-screener 15분 주기로 raw/screener pool이 갱신된다
- smart skip TTL 30분 만료 시 context/core hash 여부와 무관하게 full selection 실행 (ttl_expired fail-open)
- context/core 변화가 있으면 full selection
- selection meta에 `pool_refreshed_at`, `pool_age_min`, `pool_refresh_reason`이 기록된다
- SELECTION_POOL_FRESHNESS_MIN 별도 타이머는 구현하지 않는다 (기존 sub-screener 주기 활용)

### R2. smart skip에 market context hash를 추가한다

목표:

- Claude를 아끼되 시장 맥락이 바뀌면 재판단한다

코드 대상:

- `runtime/selection_smart_skip.py::semantic_signature`
- `runtime/selection_smart_skip.py::maybe_reuse`
- `minority_report/analysts.py`의 smart skip hash 생성부

추가할 hash 입력:

```text
market_change_bucket       (3단계: down/flat/up, 예: < -1% / -1~+1% / > +1%)
market_change_severity_bucket (2~3단계: normal/severe_down, 예: severe_down < -3%)
secondary_change_bucket    (3단계 동일)
breadth_bucket             (3단계: weak/neutral/strong, 예: adv/dec < 0.8 / 0.8~1.2 / > 1.2)
30m_slope_bucket           (3단계: falling/flat/rising)
session_phase              (pre/morning/midday/afternoon/near-close — 5단계, 시간 경과가 아닌 phase 전환만 반영)
risk_mode                  (NORMAL/RISK_OFF/HALT)
consensus_mode             (bull/bear/flat)
prompt_pool_core_hash      (rank 1~25 ticker 조합)
prompt_pool_tail_hash      (rank 26~40 ticker 조합)
```

제거한 입력 및 이유:

```text
제거: session_elapsed_bucket
이유: 시간이 지날수록 자동으로 변화 → 시간 경과만으로 context hash가 달라져 smart skip reuse 불가
     session_phase가 역할을 대신함 (단계 전환만 반영)

제거: intraday_context_version
이유: 버전 번호가 사이클마다 바뀌면 reuse가 불가능해짐
     context를 실질적으로 표현하는 market_change, breadth, phase로 대체

제거: preopen_news_enrichment_hash
이유: preopen 데이터는 매일 아침 1회 갱신됨. 종일 동일하므로 hash 가치 없음
     preopen HARD pin은 R3 strong trigger 조건 3번에서 직접 처리
```

bucket 크기 원칙:

```text
bucket은 실질적인 상황 변화를 반영할 만큼 coarse하게 잡는다.
0.1% 등락마다 hash가 바뀌면 smart skip이 무력화된다.
3단계 기준이 기본값이며, 운영 데이터 확인 후 조정한다.
```

core/tail 기준:

```text
core = prompt rank 1~25
tail = prompt rank 26~40
```

재사용 정책:

```text
context hash changed
  -> reuse 금지, full selection

core hash changed
  -> reuse 금지, full selection

tail hash changed only
  -> strong trigger가 있으면 full selection
  -> strong trigger가 없으면 reuse 허용 가능

cached trade_ready or actionable price plan
  -> 기존처럼 reuse 금지 유지
```

기존 context 감지 시스템과의 관계:

```text
기존 _should_reinvoke_analysts / _should_refresh_selection_after_reinvoke:
  event-driven으로 _reinvoke_analysts + manual_rescreen을 트리거
  감지 대상: market crash (-2%), mode flip (bear↔bull), permission 완화, phase 전환

R2 context hash:
  smart skip reuse 여부 결정 (Claude call을 생략할지 판단)
  감지 대상: market_change_bucket, breadth_bucket, risk_mode, consensus_mode, session_phase

두 시스템은 역할이 다르다:
  기존: "rescreen을 실행해야 하는가"
  R2: "cached selection을 재사용해도 되는가"

기존 reinvoke가 발동되면 자연히 smart skip도 무효화되므로 충돌 없음.
단, R2 hash가 변해도 기존 reinvoke cooldown이 남아있으면 full selection이 늦게 실행될 수 있다.
이것은 기존 시스템의 cooldown 설계를 바꾸지 않는 범위에서 허용된다.
```

Acceptance:

- 기존 `selection_smart_skip.semantic.v1`은 `semantic.v2`로 schema version up
- context mismatch 시 `fail_open_reason=market_context_changed` 기록
- tail-only change 재사용과 core change full call이 테스트로 분리된다
- smart skip reuse log에 `context_hash`, `core_hash`, `tail_hash`, `reuse_block_reason`이 남는다
- 기존 `_should_reinvoke_analysts` 로직은 변경하지 않는다

### R3. 강한 sub-screener trigger는 triage로 끝내지 않고 full rescreen을 연다

목표:

- 6/2의 핵심 장점인 신규 후보 발견력을 복구한다

코드 대상:

- `trading_bot.py::maybe_run_sub_screener`
- 신규 helper 후보: `TradingBot._sub_screener_requires_full_rescreen(market, result, screener_rows)`

strong full rescreen 조건:

```text
아래 중 하나라도 참이면 strong trigger → full rescreen

1. max(new_plan_a scores) > current_watchlist_floor_score
   현재 watchlist 최하위 점수보다 높은 신규 후보 존재
   → 새 후보가 기존 감시 대상을 밀어낼 수 있음

2. max(new_plan_a scores, new_plan_b_high scores) > current_trade_ready_floor_score
   현재 trade_ready 최하위 점수보다 높은 신규 후보 존재
   → 매수 우선순위가 바뀔 수 있음

3. preopen HARD pin이 신규 발견되고 current watch에 없음
   → 핀 후보는 항상 full 재판단 필요

4. Plan A 신규 후보 score >= SUB_SCREENER_STRONG_PLAN_A_SCORE_MIN (default: 80)
   현재 triage threshold(70)보다 명확히 높은 절대 기준
   → 점수 차이가 충분히 커서 우선순위 역전이 확실한 경우
```

제거한 조건 및 이유:

```text
제거: new_plan_a count >= SUB_SCREENER_PLAN_A_THRESHOLD and max score >= SUB_SCREENER_PLAN_A_MIN_SCORE
이유: 현재 triage trigger 조건과 동일 → 모든 trigger가 strong이 되어 항상 full rescreen 실행

제거: score_delta >= 8
이유: score 분포 근거 없음. 70~85 클러스터이면 delta 8이 대부분 잡힘 → 사실상 전부 strong

제거: market context severity changed
이유: "severity"가 코드에 없는 개념. context 변화는 R2 context hash mismatch로 처리

제거: selection_snapshot_age >= 30분 and strong 신규 후보 있음
이유: "strong 신규 후보"가 정의하려는 바로 그 개념 → 순환 정의
     selection age는 smart skip TTL이 자연히 처리
```

current_watchlist_floor_score / current_trade_ready_floor_score:

```python
# 봇 실제 state: today_tickers, trade_ready_tickers, selection_meta
# _current_watchlist/_current_trade_ready는 확인된 state가 아님 → 실제 구현 시 정확한 state 확인 필요
# 추천 source: selection_meta의 _final_prompt_pool 또는 last_selection_meta의 watchlist scores

watch_scores = [
    r.get("trainer_prompt_score", 0)
    for r in self._get_current_watchlist_meta(market)  # 구현 시 실제 state key 확인
    if r.get("trainer_prompt_score") is not None
]
trade_scores = [
    r.get("trainer_prompt_score", 0)
    for r in self._get_current_trade_ready_meta(market)
    if r.get("trainer_prompt_score") is not None
]
watch_floor = min(watch_scores) if watch_scores else None
trade_floor = min(trade_scores) if trade_scores else None
```

floor score 없을 때 fallback:

```text
watch_floor is None (watchlist 비어 있음, 예: 세션 초반)
  → 조건 1 평가 생략, 조건 4 (절대 기준 >= 80)만 사용
  → 세션 초반 중복 full rescreen 방지

trade_floor is None (trade_ready 비어 있음)
  → 조건 2 평가 생략
```

주의 — strong trigger 판단 순서:

```text
strong trigger 판단은 반드시 triage 실행 전에 현재 watchlist를 스냅샷해야 한다.

잘못된 순서:
  triage 실행 → watchlist 업데이트 → strong trigger 판단 (이미 바뀐 floor로 비교)

올바른 순서:
  pre_triage_watch_floor = min(현재 watchlist scores)  ← 스냅샷
  pre_triage_trade_floor = min(현재 trade_ready scores) ← 스냅샷
  strong = max(new_plan_a scores) > pre_triage_watch_floor  ...
  triage 실행 (watchlist 변경)
  if strong: full rescreen

triage가 먼저 고점수 후보를 추가하면 watchlist floor가 높아져
strong trigger가 발동되지 않는 오류가 생긴다.
```

triage 추가 후보의 selection_snapshot_ts:

```text
triage로 추가된 후보는 selection_snapshot_ts를 triage 실행 시각으로 설정한다.
full selection 후보와 동일한 age 기준을 적용한다.
triage 후보에 selection_snapshot_ts가 없으면 R5 age 계산이 undefined 동작을 한다.
```

요구 동작:

```text
strong trigger
  -> early judge/triage는 보조로 실행 가능
  -> 그러나 triage success만으로 return하지 않음
  -> _reinvoke_analysts(market, "sub_screener")
  -> manual_rescreen(source_type="sub_screener_rescreen", candidate_override=screener_rows)
  -> smart skip reuse 금지, 반드시 Claude full selection 호출

weak trigger
  -> triage만 허용 가능
  -> full rescreen 생략 가능
```

SUB_SCREENER_MAX_PER_SESSION 최종 정책:

```text
현재 코드: max_per_session=5 도달 시 maybe_run_sub_screener() 상단에서 즉시 return
  → R3 strong trigger 평가 코드 자체가 실행되지 않음
  → 세션 후반 강한 신규 후보 탐지 불가

최종 정책:
  - SUB_SCREENER_MAX_PER_SESSION은 scan 제한으로 사용하지 않는다.
  - SUB_SCREENER_MAX_PER_SESSION은 strong full rescreen hard cap으로도 사용하지 않는다.
  - scan/record_scan/strong 판단은 busy guard, blackout, interval 조건만 통과하면 수행한다.
  - weak trigger는 triage로 처리하고 Claude full selection을 호출하지 않는다.
  - strong trigger는 dedupe TTL/min interval/blackout/busy guard를 통과하면 full Claude reselection을 수행한다.

반복 호출 방지:
  - SUB_SCREENER_INTERVAL_MIN
  - SUB_SCREENER_MIN_INTERVAL_MIN
  - SUB_SCREENER_DEDUPE_TTL_MIN
  - market task owner busy guard
  - blackout before close

이 정책은 current 대비 Claude 호출량 증가를 허용한다. 그 대신 6/2의 핵심 장점인
"강한 신규 후보가 full selection으로 다시 경쟁하는 구조"를 복구한다.
```

Acceptance:

- strong trigger test에서 `_reinvoke_analysts`와 `manual_rescreen`이 호출된다
- weak trigger test에서 triage만 적용되고 full call은 생략될 수 있다
- duplicate trigger라도 context/core 변화가 있으면 dedupe suppressed로 끝내지 않는다
- SUB_SCREENER_MAX_PER_SESSION이 초과된 상태에서도 scan과 strong 판단은 실행된다
- strong trigger full rescreen은 hard session cap에 막히지 않는다
- strong trigger full rescreen에서는 smart skip reuse가 금지된다
- audit에 `sub_screener_full_rescreen_reason`이 기록된다

### R4. buy_time_confirm_judge를 single_symbol_judge와 분리한다

목표:

- PathB plan 생성용 judge와 주문 직전 매수 확인 judge를 분리한다

코드 대상:

- 신규 `execution/buy_time_confirm_judge.py`
- 신규 tests `tests/test_buy_time_confirm_judge.py`

계약:

```json
{
  "ticker": "AAPL",
  "market": "US",
  "decision": "CONFIRM_BUY",
  "confidence": 0.74,
  "reason": "setup still valid",
  "invalid_if": "price loses VWAP or breakout fails",
  "reject_reason_code": ""
}
```

허용 decision:

```text
CONFIRM_BUY              → setup 유효, 주문 진행
DEFER                    → 이번 사이클 skip. 다음 사이클 신호 재발화 시 재평가 (재시도 queue 없음)
REJECT                   → 이 후보 이번 사이클 제외. 영구 제거 아님
```

CONFIRM_UNAVAILABLE_PROCEED (internal audit state, Claude response 아님):

```text
Claude API timeout / parse fail / invalid decision 발생 시
  → decision=CONFIRM_UNAVAILABLE_PROCEED (Claude normalize 결과가 아닌 시스템 내부 state)
  → confirm_unavailable_reason 기록 (timeout / parse_fail / api_error)
  → 아래 adverse context 조건 미해당이면 주문 게이트 진행
  → adverse context 해당이면 full rescreen 요청 또는 DEFER

adverse context (CONFIRM_UNAVAILABLE일 때 매수 금지):
  risk_mode == RISK_OFF 또는 HALT
  OR market_change_severity_bucket == "severe_down" (< -3%)
  → 이 조건에서 Claude 확인 불가 상태로 매수하는 것은 불확실성이 너무 큼
  → full_rescreen_requested 기록 후 DEFER
```

DEFER semantics 명확화:

```text
DEFER = "이번 사이클 이 신호 skip"
  _pending_signals는 사이클 로컬 변수. DEFER된 신호는 사라짐.
  다음 사이클에서 전략 신호가 자연히 재발화되면 자동으로 다시 confirm 대상이 됨.
  재시도 queue나 persistent deferred list를 만들지 않는다.
  "full_rescreen_requested" 기록은 다음 선택 사이클에서 fresh rescreen을 유도하기 위한 hint다.
```

금지:

- 주문 수량 결정 금지
- 주문 금액 결정 금지
- risk/broker gate override 금지
- PathB buy zone, stop, target 생성 금지
- `BUY_READY`, `PROBE_READY`, `PULLBACK_WAIT` 같은 selection action 출력 금지

입력:

```text
selection snapshot age
selection source
candidate action/reason/risk tags
latest price and intraday features
strategy signal payload
market context hash/current context
held/pending/same-day stopped flags
```

실패 정책:

```text
fresh 후보는 confirm을 호출하지 않으므로 실패 영향 없음

stale/context changed 후보에서 Claude timeout/parse fail/API fail 발생
  → decision = CONFIRM_UNAVAILABLE_PROCEED (내부 audit state)
  → confirm_unavailable_reason 기록
  → adverse context 아니면: 주문 게이트 진행
  → adverse context 이면: full_rescreen_requested + DEFER
```

adverse context 정의:

```text
risk_mode == RISK_OFF 또는 HALT
OR market_change_severity_bucket == "severe_down" (< -3%)
→ Claude 확인 불가 상태에서 시장 악화 중 매수 금지
→ CONFIRM_UNAVAILABLE_PROCEED여도 진행 불가
```

CONFIRM_BUY와 CONFIRM_UNAVAILABLE_PROCEED 구분:

```text
CONFIRM_BUY: Claude가 실제로 setup을 확인하고 승인
CONFIRM_UNAVAILABLE_PROCEED: Claude 확인 불가, 기존 gate만으로 진행
→ 두 경로가 섞이면 "Claude가 확인했다"는 audit record가 부정확해짐
→ token usage와 audit record는 반드시 분리
```

Acceptance:

- `single_symbol_judge.py`는 PathB price plan 생성 계약으로 유지된다
- buy-time confirm은 `CONFIRM_BUY/DEFER/REJECT`만 Claude normalize 결과로 허용한다
- parse 실패, timeout, invalid decision은 `CONFIRM_UNAVAILABLE_PROCEED`로 처리된다
- CONFIRM_UNAVAILABLE_PROCEED는 adverse context 없을 때만 주문 게이트 진행
- adverse context 시 CONFIRM_UNAVAILABLE_PROCEED는 DEFER + full_rescreen_requested
- token usage label은 `buy_time_confirm_judge`로 분리된다

### R5. Path A 주문 직전 confirm gate를 조건부로 넣는다

목표:

- fresh selection에서는 latency 없이 주문한다
- stale/context changed 후보만 주문 직전 검증한다

코드 대상:

- `trading_bot.py`의 `_pending_signals` 처리 구간
- 현재 신호 수집 후 `_pending_signals.sort()`와 order sizing/precheck/place_order 사이
- 기존 helper `TradingBot._selection_snapshot_age_min()` 재사용

삽입 위치:

```text
strategy signal fired
  -> _pending_signals에 후보 추가
  -> 점수 정렬
  -> 각 signal 처리 시작
  -> buy-time confirm gate
  -> affordability / qty / safety / precheck / place_order
```

조건:

```text
selection age < 30분 and context unchanged
  -> confirm skip

selection age >= 30분 or context changed
  -> buy_time_confirm_judge 호출

selection age >= 60분
  -> single confirm만으로 매수 금지
  -> full_rescreen_requested + DEFER
```

"context changed" 정의:

```text
context changed = R2의 context hash mismatch (market_change_bucket,
                  market_change_severity_bucket, breadth_bucket,
                  risk_mode, consensus_mode, session_phase 중 하나 이상 변화)

session_elapsed_bucket 단독 변화는 context changed로 처리하지 않는다.
시간이 지났다는 이유만으로 confirm이 발동되면 결국 모든 후보가 항상 confirm 대상이 됨.
```

selection age 기준 — pre-open selection 처리:

```text
_selection_snapshot_age_min()은 wall-clock 기준이다.
US pre-open에 selection 수행 (22:25 KST) 후 장 열리고 21분에 첫 신호 발생하면
age = 26분 → confirm 발동. 이는 fresh pre-open setup을 stale로 오분류한다.

1차 구현 기준:
  BUY_TIME_CONFIRM_STALE_MIN=30으로 단일화한다.
  pre-open age reset은 후속 개선으로 둔다.
  BUY_TIME_CONFIRM_STALE_MIN=20으로 남겨두면 pre-open 후보가 오분류되므로 위험.

후속 분리 후보:
  - selection age를 장 시작 이후 경과 시간으로 재계산
  - pre-open selection_snapshot_ts를 market open 시각으로 갱신
  OPENING_STALE_MIN=30
  INTRADAY_STALE_MIN=20
  단, 장 시작 age reset 또는 장 시작 이후 경과 재계산이 먼저 구현되어야 한다.
```

latency guard:

```text
BUY_TIME_CONFIRM_TIMEOUT_MS=2500
BUY_TIME_CONFIRM_MAX_CALLS_PER_SESSION=20 (per market)
  → 세션당 20회 초과 시 이후 confirm은 CONFIRM_UNAVAILABLE_PROCEED 처리
  → confirm_unavailable_reason=cap_exceeded
  → adverse context 없으면 주문 게이트 진행, adverse context면 DEFER
fresh skip이면 호출하지 않음
```

Acceptance:

- fresh selection test에서 Claude confirm 호출 없음
- stale 30~60분 test에서 confirm 호출 후 `CONFIRM_BUY`면 기존 order gate 진행
- `DEFER/REJECT`면 `place_order` 호출 없음
- 60분 이상 stale test에서 confirm만으로 주문하지 않음
- session cap 초과 시 `CONFIRM_UNAVAILABLE_PROCEED` + `confirm_unavailable_reason=cap_exceeded`로 기록한다
- normal context의 `CONFIRM_UNAVAILABLE_PROCEED`는 주문 게이트를 진행하고, adverse context에서는 DEFER한다
- blocked/audit reason은 `buy_time_confirm_defer`, `buy_time_confirm_reject`, `selection_snapshot_too_stale`로 분리된다

### R6. PathB zone hit은 1차에서 live confirm하지 않고 context drift audit만 추가한다

목표:

- PathB zone hit은 실시간 가격 신호다. plan 생성 시점이 오래돼도 zone hit 자체는 현재 정보다.
- age 기반으로 PathB zone hit을 막으면 multi-day PULLBACK_WAIT 전략이 사실상 무력화된다.
- PathB는 현재 수익 핵심 경로이므로 1차 개선에서 Claude confirm latency를 주문 직전에 추가하지 않는다.
- 보호된 PathB sizing, broker truth, safety gate는 건드리지 않는다.

PathB와 PlanA의 차이:

```text
PlanA: "이 후보를 이 시점에 사는 게 맞나" → selection staleness가 의미 있음
PathB: "이 가격에 들어가는 게 맞나" → zone hit이 실시간 신호, plan age가 아님
```

코드 대상:

- `runtime/pathb_runtime.py::scan_waiting_entries`
- plan payload의 `context_hash_at_creation`
- zone hit 시 audit payload

1차 삽입 위치:

```text
scan_waiting_entries()
  -> adapter.check_entry()
  -> signal.signal true
  -> risky-origin confirmation gate  ← 기존 보호장치 (_kr_pathb_risky_origin_confirmation_gate, 이미 존재)
  -> context-drift audit             ← R6 신규 (주문 차단/지연 없음)
  -> burst cap
  -> _submit_buy()
```

기존 risky-origin gate (변경 금지):

```text
_kr_pathb_risky_origin_confirmation_gate (runtime/pathb_runtime.py:1005-1065)
  감지: OR_MISSING, OPENING_RANGE_MISSING, ATR_BLOCKED, RISK_HIGH, RISK_EXTREME, PA_LOW, FADE
  조건: data_quality == "minute_complete" AND momentum_ok AND (opening_break OR vwap_reclaim)
  실패 시: 해당 후보 skip, 다음 후보 진행

R6는 이 gate를 건드리지 않는다. 뒤에 context-drift audit만 추가한다.
```

1차 조건:

```text
context unchanged (R2 hash 기준)
  -> audit: pathb_context_drift=false
  -> 기존 path 진행

context changed 또는 current adverse context
  -> audit: pathb_context_drift=true
  -> audit fields: context_hash_at_creation, current_context_hash, current_risk_mode, market_change_severity_bucket
  -> 기존 path 진행

plan age (20분/60분 기준)는 PathB에 적용하지 않는다
  이유: PATHB_INTRADAY_ONLY=false, multi-day plan이 정상 운영 중
       plan 생성 후 2~4시간 뒤 zone hit도 정상 시나리오
```

context_hash_at_creation 저장 요구사항:

```text
현재 single_symbol_judge normalize 결과에 context_hash 필드 없음.
R6에서 "plan 생성 시 context vs 현재 context audit"을 하려면
single_symbol_judge 호출 시점의 context_hash를 plan payload에 저장해야 한다.

저장 경로:
  single_symbol_judge 호출부 (trading_bot.py 또는 pathb_runtime.py)
  → 호출 직전 context_hash 계산 (R2와 동일 hash 함수)
  → judge result에 context_hash_at_creation 주입
  → plan payload에 포함해서 waiting plan에 저장

비교 시:
  current_context_hash vs plan.context_hash_at_creation
  → 변화 없음: pathb_context_drift=false
  → 변화 있음: pathb_context_drift=true
```

후속 live confirm 전환 조건:

```text
1차 audit에서 아래를 확인한 뒤 별도 구현한다.

- context_drift=true zone hit의 이후 성과가 materially 나쁨
- confirm latency가 PathB zone hit 진입 품질을 해치지 않음
- confirm unavailable 처리 규칙이 PathA와 동일하게 audit 분리됨
- waiting 유지/취소 정책이 PathB 보호 영역과 충돌하지 않음
```

Acceptance:

- PathB zone hit은 1차 구현에서 buy_time_confirm_judge를 호출하지 않는다
- fresh context (context_hash 변화 없음) PathB zone hit은 기존 path 진행
- risk_mode 변화 또는 < -3% 급락 시에도 1차에서는 audit만 남기고 기존 path 진행
- plan payload에 `context_hash_at_creation` 필드가 저장된다
- plan age 기준(20분/60분)으로 zone hit이 차단되면 실패
- context drift audit 누락은 실패
- `_pathb_qty_with_context`, `safety_gate.evaluate`, broker precheck 동작은 변경하지 않는다

### R7. Claude prompt pool은 40까지 넓히되 output cap은 유지한다

목표:

- 후보 recall을 개선한다
- 최종 매수/감시 대상 수는 늘리지 않는다

코드 대상:

- `minority_report/analysts.py::_selection_candidate_cap`
- `minority_report/analysts.py::_trainer_prompt_hard_cap`
- `runtime/candidate_prompt_pool.py::build_trainer_prompt_pool`
- `config/v2_start_config.json`의 prompt cap 관련 key

현재 cap:

```text
CANDIDATE_PROMPT_POOL_TARGET_KR=28
CANDIDATE_PROMPT_POOL_TARGET_US=24
CANDIDATE_PROMPT_POOL_HARD_CAP_KR=32
CANDIDATE_PROMPT_POOL_HARD_CAP_US=35
KR_PROMPT_POOL_CAP=32
US_PROMPT_POOL_CAP=35
KR_SELECTION_PROMPT_CAP default=32
US_SELECTION_PROMPT_CAP default=35
CLAUDE_SELECTION_COMPACT_WATCH_MAX=15
CLAUDE_SELECTION_COMPACT_TRADE_READY_MAX=5
```

요구 변경:

```text
input prompt pool hard cap:
  KR 40
  US 40

output cap:
  watchlist 15 유지
  trade_ready 5 유지
```

Acceptance:

- cap 관련 env/config가 서로 어긋나지 않는다
- 실제 `prompt_pool_count`가 40까지 늘 수 있다
- final watchlist는 15 초과하지 않는다
- final trade_ready는 5 초과하지 않는다
- 40개 확장은 buy permission 확장이 아니라 visibility 확장으로 audit에 기록된다

### R8. actual prompt visibility audit을 보강한다

목표:

- "후보군을 넓혔다"가 실제 Claude 입력에 반영됐는지 검증 가능하게 한다

코드 대상:

- `minority_report/analysts.py`
- `runtime/candidate_prompt_pool.py`
- `ticker_selection_db.py` 또는 existing selection trace/audit writer
- 관련 report tools

필수 기록:

```text
actual_prompt_count
actual_prompt_tickers
actual_prompt_ranks
excluded_from_prompt
excluded_reason
top30_missing_count
top36_missing_count
top40_missing_count
core_hash
tail_hash
hard_preopen_pin_prompt_count
hard_preopen_pin_missing_tickers
```

Acceptance:

- DB/log/report에서 raw rows, curated candidates, actual prompt, Claude output을 구분한다
- `input_to_claude` 같은 추정값이 actual prompt count로 오해되지 않는다
- top30/top36/top40 missing이 시장별로 집계된다

### R9. 운영 가시성 metric을 추가한다

목표:

- 개선이 실제로 작동하는지 운영 중 확인한다

필수 metric:

```text
selection_pool_age_min
selection_snapshot_age_min_at_order
smart_skip_reuse_count
smart_skip_block_context_changed_count
smart_skip_block_core_changed_count
sub_screener_strong_trigger_count
sub_screener_full_rescreen_count
sub_screener_triage_only_count
sub_screener_strong_full_rescreen_smart_skip_blocked_count
buy_time_confirm_called_count
buy_time_confirm_skipped_fresh_count
buy_time_confirm_unavailable_proceed_count
buy_time_confirm_unavailable_reason_count
buy_time_confirm_defer_count
buy_time_confirm_reject_count
buy_time_confirm_latency_ms_p50/p95
stale_order_block_count
pathb_context_drift_audit_count
pathb_context_hash_missing_count
```

Acceptance:

- normal/system log에서 사람이 읽을 수 있는 한국어 reason이 남는다
- funnel/audit event에서 machine-readable reason이 남는다
- dashboard 또는 ops report에서 호출량과 block/defer 수를 볼 수 있다

### R10. 테스트 요구사항

필수 테스트:

```text
tests/test_selection_smart_skip.py
  - context hash changed -> reuse false
  - core changed -> reuse false
  - tail-only changed without strong trigger -> reuse allowed
  - actionable cached trade_ready/price plan -> reuse false 유지

tests/test_sub_screener_integration.py
  - strong trigger -> _reinvoke_analysts + manual_rescreen
  - weak trigger -> triage only 가능
  - duplicate trigger but context changed -> dedupe suppressed로 끝나지 않음
  - SUB_SCREENER_MAX_PER_SESSION 초과 상태에서도 scan과 strong 판단 실행
  - strong trigger full rescreen은 smart skip reuse 금지

tests/test_buy_time_confirm_judge.py
  - CONFIRM_BUY normalize
  - DEFER/REJECT normalize
  - invalid/parse fail -> CONFIRM_UNAVAILABLE_PROCEED (internal state, not Claude response)
  - timeout -> CONFIRM_UNAVAILABLE_PROCEED
  - adverse context (RISK_OFF/HALT/-3%) + CONFIRM_UNAVAILABLE -> DEFER + full_rescreen_requested
  - normal context + CONFIRM_UNAVAILABLE -> 주문 게이트 진행
  - no qty/amount/risk override accepted

tests/test_candidate_action_live_mapping.py 또는 신규 Path A test
  - fresh selection (< 30분, context unchanged) -> confirm skip and place_order path can proceed
  - pre-open selection + market opened + 31분 경과 -> stale 30분 threshold 적용
  - triage-added 후보 -> selection_snapshot_ts = triage 실행 시각으로 age 계산
  - stale selection -> confirm required
  - reject/defer -> place_order not called
  - age >= 60분 -> confirm alone cannot buy
  - session cap 초과 -> CONFIRM_UNAVAILABLE_PROCEED + confirm_unavailable_reason=cap_exceeded

tests/test_pathb_runtime.py
  - context unchanged PathB zone hit -> context_drift=false audit, 기존 path 진행
  - risk_mode 변화 후 zone hit -> context_drift=true audit, buy_time_confirm_judge 미호출
  - plan age 20분/60분 기준으로 zone hit 차단되면 실패
  - context_hash_at_creation missing -> audit missing=true, hard block 금지
  - 기존 risky-origin gate 동작 불변 확인

tests/test_candidate_quality_trainer.py
  - prompt pool cap 40
  - watchlist 15 / trade_ready 5 output cap 유지
```

QA:

```text
python -m py_compile trading_bot.py runtime/selection_smart_skip.py runtime/pathb_runtime.py minority_report/analysts.py execution/buy_time_confirm_judge.py
python -m pytest tests/test_selection_smart_skip.py tests/test_sub_screener_integration.py tests/test_buy_time_confirm_judge.py -q
python -m pytest tests/test_candidate_action_live_mapping.py tests/test_pathb_runtime.py tests/test_candidate_quality_trainer.py -q
python tools/live_preflight.py --mode live --skip-dashboard --json
```

## 8. Acceptance Matrix

| Requirement | Pass condition | Fail condition |
| --- | --- | --- |
| 30분 pool freshness | 30분마다 raw/prompt pool freshness 재평가 | watchlist가 30분 이상 stale인데 pool 평가 없음 |
| smart skip context | context/core 변화 시 reuse 금지 | 시장 맥락 변화에도 cached selection reuse |
| strong sub-screener | 강한 신규 후보가 full rescreen으로 연결 | strong 후보가 triage-only로 끝남 |
| buy-time confirm | stale/context changed 후보만 confirm | fresh 후보에도 매번 Claude 호출 |
| 60분 stale rule | single confirm만으로 매수 금지 | 60분 이상 stale 후보가 confirm 하나로 주문 |
| prompt pool 40 | actual prompt 40까지 가능 | config cap 불일치로 실제 35 이하 유지 |
| output cap | watch 15, trade_ready 5 유지 | 후보 확장이 매수 후보 수 증가로 이어짐 |
| audit visibility | skip/full/confirm/defer 이유 확인 가능 | 왜 full rescreen 또는 skip됐는지 추적 불가 |

## 9. 6/2 대비 재분석

최종 개선 구조가 6/2보다 좋아지는 부분:

```text
6/2:
  강한 후보 발견 -> full rescreen 강함
  하지만 Claude 호출량 큼
  주문 시점 stale 방어 약함

개선안:
  강한 후보 발견 -> full rescreen 복구
  context 변동 없으면 smart skip으로 Claude 절감
  stale/context changed 주문만 buy-time confirm
  prompt pool 40으로 후보 visibility 확대
```

현재 버전 대비 좋아지는 부분:

```text
현재:
  triage/early judge가 full reselection을 대체하는 경우가 많음
  smart skip context가 부족하면 stale 후보 유지 가능
  single_symbol_judge는 PathB plan 생성용이라 주문 직전 검증이 아님

개선안:
  strong sub-screener는 full reselection으로 복귀
  smart skip이 context/core 변화를 보면 fail-open full call
  주문 직전 stale 후보만 confirm
```

6/2 대비 남는 단점:

```text
1. 구조 복잡도 증가
   6/2는 trigger -> full rescreen이 단순했다.
   개선안은 context hash, core/tail, strong trigger, confirm age gate가 필요하다.

2. watchlist floor 이하 신규 후보 미탐지
   현재 watchlist보다 점수가 낮은 신규 후보는 strong trigger가 발동되지 않는다.
   6/2는 watchlist 품질에 관계없이 모든 trigger에서 full rescreen을 실행했다.

3. 완만한 시장 변화 미감지
   market_change_bucket이 3단계 coarse buckets이므로 -1.5% 하락은 flat으로 처리될 수 있다.
   6/2는 이런 완만한 변화에도 다음 sub-screener trigger 시 full rescreen이 실행됐다.

4. smart skip 조건 의존
   context hash가 부족하면 6/2보다 신규 후보 반영이 늦을 수 있다.

5. pre-open selection age 오분류
   장 열리기 전 수행된 selection은 wall-clock 기준으로 빠르게 stale이 된다.
   6/2에는 이 문제가 없었다 (confirm gate 자체가 없었으므로).

6. prompt pool 40 노이즈
   후보 recall은 좋아지지만 Claude 입력 노이즈가 늘 수 있다.
   output cap 15/5 유지와 actual prompt audit이 필수다.

7. config cap 불일치 리스크
   `KR_PROMPT_POOL_CAP`, `US_PROMPT_POOL_CAP`, `*_SELECTION_PROMPT_CAP`,
   `CANDIDATE_PROMPT_POOL_HARD_CAP_*`가 어긋나면 실제 확장이 반영되지 않는다.
```

최종 판정:

```text
개선안은 6/2보다 단점이 없는 구조가 아니다.
하지만 6/2의 핵심 장점인 신규 후보 full reselection을 복구하고,
현재 버전의 Claude 절감과 안전장치를 유지하며,
6/2에 없던 주문 시점 stale 방어를 추가한다.

따라서 구조 품질은 개선된다.
단, single confirm만 추가하거나 strong full rescreen을 약화하면 퇴보한다.
```

## 10. Implementation Order

1차 구현:

```text
R2 smart skip context/core/tail hash
R3 strong sub-screener full rescreen
R8 actual prompt visibility audit
```

사유:

- 6/2 대비 나빠질 수 있는 핵심 원인인 신규 후보 미탐색을 먼저 막는다
- buy-time confirm보다 pool freshness 복구가 우선이다
- audit이 있어야 cap 40과 smart skip 효과를 검증할 수 있다

2차 구현:

```text
R4 buy_time_confirm_judge
R5 Path A conditional confirm
R6 PathB context drift audit only
```

사유:

- confirm은 full refresh 대체재가 아니라 stale 방어 장치다
- pool freshness가 보강된 뒤 적용해야 confirm이 오래된 후보 방어 역할만 맡는다

3차 구현:

```text
R7 prompt pool 40 cap alignment
R9 dashboard/ops report metric
R10 broader QA
```

사유:

- 후보 확장은 visibility 개선이지만 noise와 token 영향이 있다
- output cap 15/5 유지와 actual prompt audit을 먼저 확보해야 한다

## 11. Config Proposal

신규 또는 조정 후보:

```text
# R1: 별도 타이머 불필요 (sub-screener 15분 주기 활용)
# SELECTION_POOL_FRESHNESS_MIN 구현하지 않음

SELECTION_CONTEXT_HASH_ENABLED=true
SELECTION_SMART_SKIP_CORE_SIZE=25
SELECTION_SMART_SKIP_TAIL_STRONG_ONLY=true

SUB_SCREENER_STRONG_FULL_RESCREEN_ENABLED=true
SUB_SCREENER_STRONG_PLAN_A_SCORE_MIN=80
# SUB_SCREENER_MAX_PER_SESSION은 scan/strong full rescreen hard cap으로 사용하지 않음
# strong 반복 호출 방지는 interval/min_interval/dedupe/busy/blackout으로 수행

BUY_TIME_CONFIRM_ENABLED=true
BUY_TIME_CONFIRM_STALE_MIN=30          # pre-open age reset 구현 전까지 30 유지 (20은 위험)
BUY_TIME_CONFIRM_TOO_STALE_MIN=60
BUY_TIME_CONFIRM_TIMEOUT_MS=2500
BUY_TIME_CONFIRM_MAX_CALLS_PER_SESSION=20  # 초과 시 CONFIRM_UNAVAILABLE_PROCEED (매수 차단 금지)
BUY_TIME_CONFIRM_ADVERSE_CONTEXT_BLOCK=true  # adverse context + unavailable → DEFER
PATHB_ZONE_HIT_CONTEXT_DRIFT_AUDIT_ENABLED=true  # PathB 1차는 audit only, 주문 차단/confirm 호출 금지

KR_SELECTION_PROMPT_CAP=40
US_SELECTION_PROMPT_CAP=40
CANDIDATE_PROMPT_POOL_HARD_CAP_KR=40
CANDIDATE_PROMPT_POOL_HARD_CAP_US=40
KR_PROMPT_POOL_CAP=40
US_PROMPT_POOL_CAP=40

CLAUDE_SELECTION_COMPACT_WATCH_MAX=15
CLAUDE_SELECTION_COMPACT_TRADE_READY_MAX=5
CLAUDE_SELECTION_COMPRESSED_MAX_TOKENS=2600  # 40개 pool 확장 시 2200 → 2600 (초과 시 trade_ready=[] fallback 위험)

PATHB_CONTEXT_DRIFT_AUDIT_ENABLED=true  # 1차는 audit only, buy-time confirm 미호출
```

주의:

- prompt input cap 40은 watch/trade output cap 확장이 아니다
- `CLAUDE_SELECTION_COMPRESSED_MAX_TOKENS=2200`을 그대로 두면 40개 입력 시 max_tokens 초과 빈도 증가 → trade_ready=[] fallback (매수 후보 전부 사라짐). CLAUDE.md에도 "25/7 cap 실험 시 2600으로 올릴 것" 명시됨.
- PathB 운영 파라미터, fixed order amount, max positions, daily entries, slippage, confidence, cooldown은 이 요구서 범위에서 변경하지 않는다

## 12. Remaining Risks And Improvements

비차단 잔여 리스크:

- context hash bucket이 너무 세밀하면 smart skip reuse 불가, 6/2 수준 Claude 사용량으로 회귀
- context hash bucket이 너무 거칠면 시장 급변 시 stale reuse 잔존
- watchlist floor 이하 신규 후보는 strong trigger 발동 안 됨 (6/2 대비 탐지력 부족)
- 완만한 변화 (-1.5%)가 bucket 경계 안이면 context hash 유지 → 6/2보다 반응 늦음
- confirm judge false REJECT는 운영 데이터 추적 필요
- pre-open selection age 오분류: BUY_TIME_CONFIRM_STALE_MIN=30으로 완화하지 않으면 발생
- triage 추가 후보의 selection_snapshot_ts 미처리 시 age 계산 undefined
- CONFIRM_UNAVAILABLE_PROCEED 처리 경로가 adverse context 판단과 맞지 않으면 매수 차단 or 위험 매수
- strong trigger가 너무 자주 발생하면 current 대비 Claude 사용량 증가
- PathB context_hash_at_creation 저장 없이 R6 구현하면 drift audit 불가
- 40개 pool + CLAUDE_SELECTION_COMPRESSED_MAX_TOKENS=2200 유지 시 trade_ready fallback 증가
- SUB_SCREENER_STRONG_PLAN_A_SCORE_MIN=80이 실제 score 분포와 맞는지 운영 후 확인 필요
- smart skip v2 최초 배포 시 v1 캐시 전부 무효화 → 첫 사이클 Claude call 스파이크 (인지 필요)

개선 방안:

- context hash는 처음부터 audit field로 기록하고 mismatch reason을 집계한다
- confirm REJECT result와 실제 이후 가격 흐름을 `reject after-outcome`으로 추적한다
- strong trigger threshold는 watchlist/trade_ready displacement 기준으로 기록하고 threshold 조정 여부를 판단한다
- cap 40 적용 후 top30/top36/top40 missing과 output 채택률을 시장별로 비교한다
- Claude 호출량은 `select_tickers`, `single_symbol_judge`, `buy_time_confirm_judge` label로 분리 집계한다
- context hash bucket 변화 빈도를 집계해서 너무 자주 바뀌면 bucket 크기를 키운다
- pre-open selection age: 1차는 BUY_TIME_CONFIRM_STALE_MIN=30 단일 기준, 장 시작 age reset은 후속 개선으로 검토
- strong trigger miss (watchlist floor 이하): 운영 후 `sub_screener_weak_trigger_new_score` 로그로 miss 빈도 추적
- strong trigger Claude 사용량은 `sub_screener_strong_trigger_count`와 `sub_screener_full_rescreen_count`로 추적하고, 과다하면 threshold만 조정한다

차단 조건:

```text
strong sub-screener가 session cap에 막혀 full rescreen으로 가지 못하는 구현
SUB_SCREENER_MAX_PER_SESSION 때문에 scan 또는 strong 판단 자체가 실행되지 않는 구현
strong trigger full rescreen이 smart skip reuse로 대체되는 구현
TTL 만료 후에도 context hash 일치를 이유로 reuse하는 구현
context/core 변화에도 TTL 안에서 smart skip reuse되는 구현
PlanA: 60분 이상 stale 후보가 single confirm만으로 주문되는 구현
PathB: plan age 기준(20분/60분)으로 zone hit이 차단되는 구현
PathB: 1차 구현에서 buy_time_confirm_judge 호출로 zone hit latency를 추가하는 구현
PathB: context_hash_at_creation 없이 R6 audit 로직이 구현되는 구현
fresh 후보마다 confirm을 호출해서 momentum/breakout latency를 키우는 구현
CONFIRM_UNAVAILABLE_PROCEED를 CONFIRM_BUY audit record로 기록해 섞는 구현
timeout/parse fail + adverse context 없는 상황에서 주문이 차단되는 구현
timeout/parse fail + adverse context 있는 상황에서 주문이 진행되는 구현
prompt pool 40 확장이 output trade_ready 확장으로 이어지는 구현
CLAUDE_SELECTION_COMPRESSED_MAX_TOKENS=2200 유지 상태로 40개 pool 운영하는 구현
session cap 초과 시 confirm skip 대신 매수 차단으로 처리하는 구현
session cap 초과 시 confirm skip을 CONFIRM_BUY로 기록하는 구현
BUY_TIME_CONFIRM_STALE_MIN=20을 pre-open age reset 없이 사용하는 구현
```

위 조건 중 하나라도 발생하면 최종 개선안이 아니라 6/2 대비 퇴보 가능성이 있는 구현이다.

## 13. Conclusion

최종 설계는 아래 조합으로 확정한다.

```text
30분 pool freshness
+ context-aware smart skip
+ strong sub-screener full rescreen
+ 조건부 buy-time confirm
+ prompt pool 40 visibility 확장
+ output cap 15/5 유지
+ PathB 1차 context drift audit only
```

이 조합이면 현재 버전의 약점인 실시간 후보 반영 지연을 개선하고, 6/2의 후보 재탐색 장점을 대부분 복구하면서 Claude 사용량 절감과 기존 안정화 구조를 유지한다.

strong trigger full rescreen은 hard session cap으로 막지 않는다. 따라서 강한 신규 후보가 cap 때문에 후보 간 재경쟁에 올라가지 못하는 리스크는 1차 설계에서 제거한다. 대신 strong trigger 빈도가 높을 경우 current 대비 Claude 사용량이 증가할 수 있으며, 이는 interval/min_interval/dedupe/threshold와 운영 metric으로 제어한다.

단, 6/2 대비 단점이 사라지는 것은 아니다. 아래는 6/2 대비 명확히 남는 갭이다:
- watchlist floor 이하 신규 후보 탐지력 부족
- 완만한 시장 변화(-1.5% 수준) bucket 미감지
- pre-open selection age 오분류 (해결 전까지)
- 구조 복잡도 증가 (trigger/hash 품질 의존)
- current 대비 Claude 호출량 증가 가능성 (strong trigger가 자주 발생하는 장세)

그래서 요구사항의 acceptance gate는 "confirm 추가"가 아니라 "freshness 복구와 skip 오판 방지"에 맞춰야 한다.

최종 한 줄 판정:

```text
1~3번을 함께 구현하면 구조 개선이다.
1번만 구현하면 개선이 아니라 6/2 대비 퇴보할 수 있다.
```
