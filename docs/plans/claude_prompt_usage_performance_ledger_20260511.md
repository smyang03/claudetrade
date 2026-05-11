# Claude Prompt Usage And Performance Ledger - 2026-05-11

## Purpose

이 문서는 Claude 사용 방식과 프롬프트 변경 이력을 한 곳에서 추적하기 위한 운영 기준 문서다.

핵심 질문은 세 가지다.

- 처음에는 Claude를 어디에, 얼마나 썼는가.
- 그때 판단 품질과 비용 구조는 어느 정도였는가.
- 프롬프트/출력 계약/실행 검증을 바꾼 뒤 비용과 성능이 어떻게 변했는가.

이 문서는 계획서가 아니라 ledger다. 변경이 생길 때마다 "변경 전 수치", "변경 내용", "변경 후 수치", "판단"을 누적한다.

## Source Files

| 구분 | 파일 |
|---|---|
| 최초 호출/토큰 baseline | [Claude Call Review](../reports/claude_call_review_20260429.md) |
| 비용 최적화 분석 | [Claude API Cost Optimization Plan](claude_api_cost_optimization_plan_20260507.md) |
| 실거래/시뮬레이션 성과 탐색 | [Web-Inspired Policy Search](../reports/web_inspired_policy_search_post_impl_20260510.md) |
| 2026-05-11 개발 리뷰 | [Today Push Strategy Dev Review](../reports/today_push_strategy_dev_review_20260511.md) |
| 누적 live 사용량 | `state/live_api_usage.json` |

## Version Map

| Version | 상태 | 기준일 | 핵심 구조 |
|---|---|---:|---|
| `v0_initial` | baseline | 2026-04-29 | Claude가 시장 판단, 종목 선택, 가격 타깃, 보유 판단, 튜닝을 넓게 담당 |
| `v1_cost_review` | 분석 완료 | 2026-05-07 | 비용 중심 분석. `select_tickers`, `hold_advisor`, analyst consensus가 주요 사용처 |
| `v2_quality_rework` | 개발/검증 단계 | 2026-05-11 | Evidence Pack, `candidate_actions.v2`, hard/soft gate, exit lifecycle bypass, output compression |
| `v3_live_measured` | 추후 작성 | TBD | 실제 live/paper N세션 후 비용/성과 비교 |

## Initial Usage Baseline

### 2026-04-29 KST Raw Call Baseline

출처: [Claude Call Review](../reports/claude_call_review_20260429.md)

| 구분 | 호출 | 입력 토큰 | 출력 토큰 | 총 토큰 |
|---|---:|---:|---:|---:|
| KR | 66 | 126,825 | 50,578 | 177,403 |
| US | 49 | 141,985 | 47,836 | 189,821 |
| 합계 | 115 | 268,810 | 98,414 | 367,224 |

초기 품질 관찰:

- 모델은 `claude-sonnet-4-6` 중심이었다.
- 저장된 응답/parsed JSON은 115/115건 정상이었다.
- Claude API 호출 오류, 529 overload, JSON 파싱 실패 패턴은 확인되지 않았다.
- 문제는 API 안정성보다 "어디에 얼마나 자주 부르며, 어떤 입력으로 판단하게 했는가"였다.

### 2026-04-29 US Session Correction

미국장은 KST 달력 파일만 보면 전일 잔여 호출과 당일 개장 호출이 섞이므로 세션 기준 보정이 필요하다.

| 구분 | 호출 | 입력 토큰 | 출력 토큰 | 총 토큰 |
|---|---:|---:|---:|---:|
| US 2026-04-29 session | 74 | 133,433 | 52,009 | 185,442 |

## Initial Usage By Function

### KR 2026-04-29

| 기능 | 호출 | 총 토큰 | 판단 |
|---|---:|---:|---|
| `ticker_selection` | 13 | 102,653 | 가장 큰 비용. 후보 분류와 가격 타깃 생성 담당 |
| `parameter_tuning` | 16 | 24,620 | 주기 호출 대비 의사결정 기여가 약함 |
| `daily_analyst` | 6 | 21,771 | 시장 모드/비중 판단. 유지 가치 있음 |
| `hold_advisor` | 30 | 19,270 | 포지션당 bull/bear/neutral 3회 호출로 호출 수 증가 |
| `postmortem` | 1 | 9,089 | 비용은 작지 않지만 학습 루프 가치 있음 |

### US 2026-04-29 Session

| 기능 | 호출 | 총 토큰 | 판단 |
|---|---:|---:|---|
| `ticker_selection` | 13 | 105,019 | KR과 동일하게 최대 비용 축 |
| `parameter_tuning` | 16 | 28,511 | 대부분 유지 판단이면 호출 조건 축소 필요 |
| `daily_analyst` | 6 | 27,938 | 시장 판단 근거로 유지 가치 있음 |
| `hold_advisor` | 39 | 23,974 | 포지션/관점별 반복 호출 비용 큼 |

## Recent Cost Baseline

출처: [Claude API Cost Optimization Plan](claude_api_cost_optimization_plan_20260507.md), `state/live_api_usage.json`

### 2026-05-04 ~ 2026-05-06 Average

| date | total tokens |
|---|---:|
| 2026-05-04 | 581,194 |
| 2026-05-05 | 587,878 |
| 2026-05-06 | 566,530 |
| average | 578,534/day |

### Category Share

| category | calls | tokens | share | avg/call |
|---|---:|---:|---:|---:|
| `select_tickers` | 65 | 617,524 | 35.6% | 9,500 |
| `hold_advisor` | 380 | 472,776 | 27.2% | 1,244 |
| `analyst_consensus` | 96 | 462,997 | 26.7% | 4,823 |
| `tuner` | 68 | 137,436 | 7.9% | 2,021 |
| `postmortem` | 4 | 42,725 | 2.5% | 10,681 |
| `quick_exit` | 3 | 2,144 | 0.1% | 715 |

### Live Usage Snapshot

`state/live_api_usage.json` 기준 누적값:

| 범위 | 입력 토큰 | 출력 토큰 | 비용 |
|---|---:|---:|---:|
| live cumulative | 6,178,593 | 1,915,072 | `$45.049401` |
| 2026-05-11 partial | 265,252 | 101,642 | `$2.267377` |

주의:

- `2026-05-11 partial`은 장중/파일 기록 시점 기준이며 하루 전체 비용이 아니다.
- 비용은 현재 로컬 usage tracker가 기록한 값이다. 청구 계정의 최종 과금과 차이가 있으면 billing export 기준으로 보정한다.

## Initial Quality Baseline

### Selection Quality, 2026-04-20 ~ 2026-05-02

출처: [Claude API Cost Optimization Plan](claude_api_cost_optimization_plan_20260507.md)

| market | rows | trade_ready | watch | traded | trade_ready fwd_3d | watch fwd_3d | trade_ready runup_3d | watch runup_3d |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR live | 387 | 72 | 315 | 14 | +6.19% | -1.76% | +16.85% | +10.81% |
| US live | 667 | 136 | 531 | 9 | +2.49% | +0.85% | +7.81% | +6.83% |

해석:

- Claude `trade_ready`는 watch 대비 성과 차이를 만들고 있었다.
- 따라서 Claude 종목 판단을 제거하는 방향은 맞지 않다.
- 다만 missed watch-only와 weak trade_ready가 동시에 있었다.
- 문제는 Claude 자체보다 입력 정보 부족, 출력 계약 부족, 실행 시점 검증 부족이었다.

### Closed Trade Baseline, 2026-05-10 Simulation

출처: [Web-Inspired Policy Search](../reports/web_inspired_policy_search_post_impl_20260510.md)

| Scenario | Market | N | W/L | Avg | PF | PnL |
|---|---|---:|---:|---:|---:|---:|
| Actual | all | 84 | 29/55 | -0.714% | 0.59 | -87,966 |
| Actual | KR | 45 | 13/32 | -1.276% | 0.43 | -74,893 |
| Actual | US | 39 | 16/23 | -0.066% | 0.94 | -13,073 |
| Current Sell Overlay | all | 84 | 33/51 | -0.057% | 0.95 | -30,965 |
| Current Sell Overlay | KR | 45 | 15/30 | -0.296% | 0.77 | -41,207 |
| Current Sell Overlay | US | 39 | 18/21 | +0.217% | 1.24 | +10,242 |

### Selection Trade Baseline, 2026-05-10 Simulation

| Scenario | Market | N | W/L | Avg | PF |
|---|---|---:|---:|---:|---:|
| Actual | all | 33 | 11/22 | -0.473% | 0.66 |
| Actual | KR | 22 | 7/15 | -0.855% | 0.42 |
| Actual | US | 11 | 4/7 | +0.292% | 1.24 |
| Cap 1.5 | all | 33 | 11/22 | +0.107% | 1.13 |
| Cap 1.5 | KR | 22 | 7/15 | -0.219% | 0.74 |
| Cap 1.5 | US | 11 | 4/7 | +0.758% | 2.02 |

## Current Claude Usage Model

| Stage | Claude 역할 | 입력 | 출력 | 로컬 시스템 역할 |
|---|---|---|---|---|
| Market analyst | 장세/비중/전략 방향 판단 | market digest, breadth, risk context | stance, confidence, risk notes | 장세 판단을 실행 cap/gate에 반영 |
| `select_tickers` | 후보를 WATCH/READY/AVOID로 분류 | 후보 line, market digest, feedback, Evidence Pack | watchlist, trade_ready, candidate_actions, price targets | hard block, route validation, order-time recheck |
| `candidate_actions` | 종목별 액션 구조화 | selection context, evidence | BUY/PROBE/WATCH/PULLBACK/AVOID | v2 parse, action ceiling, soft override validation |
| `hold_advisor` | 보유/매도 자문 | position, PnL, MFE, risk trigger | HOLD/SELL, confidence, reason | hard exit bypass, cache, SLA trigger |
| `tuner` | 전략 파라미터 보정 | recent performance, regime | MAINTAIN/ADJUST | broad loosen 금지, threshold proposal만 반영 |
| `postmortem` | 사후 학습 | trades, misses, prompt/output | lessons, failure patterns | prompt feedback, trainer/cohort update |

## Problems Found Before Rework

| 문제 | 실제 영향 | 개선 방향 |
|---|---|---|
| 후보 age/chase 정보 부족 | 018880처럼 오래된 후보를 fresh momentum처럼 판단 | Evidence Pack에 `first_seen`, `candidate_age`, `price_change_since_first_seen` 추가 |
| late chase를 Claude가 직접 인식하기 어려움 | KR 강세주 추격 매수 반복 | KR late entry gate + fresh confirmation fast lane |
| `BUY_READY` 출력 근거가 약함 | 078150 같은 약한 반등 추격 | `candidate_actions.v2`, `why_not_watch`, `blocking_factors` |
| hard fact와 soft gate가 섞임 | no-carry/hard loss가 Claude HOLD에 막힐 수 있음 | hard fact는 Claude override 불가 |
| exit lifecycle SELL이 shadow에 머묾 | 012610류 방치 가능 | allowlist hard SELL live bypass |
| recovery micro 메타 손실 | no-carry/force_exit_at/hard_loss 조건이 review에서 사라짐 | metadata invariant |
| 수익 보존 review 지연 | 067170처럼 MFE 후 반납 | Profit Preservation SLA |
| 자연어 output 과다 | output token 증가, parser 부담 | compressed schema + reason_code |
| tuning feedback이 넓게만 적용 | 반복 실패가 정밀 gate로 연결되지 않음 | Tuning Feedback Contract |

## Prompt And Contract Changes

### C1. Candidate Evidence Pack

목표:

- Claude가 단순 등락률/거래대금이 아니라 후보의 시간, 가격 추격, 확인 신호, 리스크 계약을 같이 보게 한다.

필수 필드:

| group | fields |
|---|---|
| identity | `ticker`, `market`, `strategy`, `candidate_source` |
| freshness | `first_seen_at`, `first_seen_price`, `candidate_age_min`, `was_trade_ready_before`, `first_ready_at` |
| chase | `price_change_since_first_seen_pct`, `price_change_since_first_ready_pct`, `pullback_from_high_pct` |
| confirmation | `post_open_ret_3m`, `post_open_ret_5m`, `post_open_ret_10m`, `or_high_break`, `vwap_reclaim`, `volume_acceleration` |
| risk | `same_day_stopped`, `hard_blocks`, `soft_gates`, `action_ceiling`, `max_entry_price` |
| trainer | `lifecycle_state`, `lifecycle_rank`, `trainer_tier`, `quarantine_reason`, `cohort_reliability` |

Example:

```json
{
  "ticker": "018880",
  "market": "KR",
  "strategy": "momentum",
  "freshness": {
    "first_seen_at": "08:53",
    "first_seen_price": 7120,
    "candidate_age_min": 130,
    "price_change_since_first_seen_pct": 11.7
  },
  "confirmation": {
    "ret_3m": -0.4,
    "ret_5m": -0.8,
    "or_high_break": false,
    "vwap_reclaim": false,
    "volume_acceleration": "missing"
  },
  "risk_control": {
    "soft_gates": ["late_chase", "at_high"],
    "hard_blocks": [],
    "action_ceiling": "WATCH",
    "override_requires": ["or_high_break", "vwap_reclaim", "ret_3m_positive"]
  }
}
```

### C2. `candidate_actions.v2`

목표:

- Claude의 판단을 자연어 결론이 아니라 검증 가능한 구조로 남긴다.

Required output:

```json
{
  "schema_version": "candidate_actions.v2",
  "candidate_actions": [
    {
      "ticker": "067170",
      "action": "PROBE_READY",
      "confidence": 0.68,
      "entry_type": "fresh_confirmed_pullback",
      "freshness_verdict": "fresh_confirmation",
      "setup_maturity": "early",
      "why_not_watch": "fresh confirmation offsets soft late-risk",
      "blocking_factors": [],
      "soft_gate_overrides": [
        {
          "gate": "late_chase",
          "evidence": "ret_3m_positive_and_or_reclaim"
        }
      ],
      "required_confirmations": ["current <= max_entry_price"],
      "invalid_if": "falls back below OR high",
      "valid_until": "2026-05-11T11:10:00+09:00",
      "max_entry_price": 4255
    }
  ]
}
```

Validation rules:

- `BUY_READY`/`PROBE_READY`에는 `why_not_watch`가 있어야 한다.
- local `action_ceiling=WATCH`를 넘기려면 `action_ceiling_ack`와 `soft_gate_overrides`가 있어야 한다.
- v2 mode에서 parse 실패나 핵심 필드 누락 시 watchlist를 `trade_ready`로 자동 승격하지 않는다.

### C3. Hard Fact / Soft Gate Split

Hard fact는 Claude가 override할 수 없다.

| hard fact | action |
|---|---|
| `same_day_stopped` | no buy |
| `price_missing` | no buy |
| `broker_order_mismatch` | no new risk |
| `recovery_micro_no_carry` | system SELL |
| `force_exit_at_due` | system SELL |
| `hard_loss_breached` | system SELL |
| market close forced liquidation | system SELL |

Soft gate는 Claude가 근거를 제시하면 override 가능하다.

| soft gate | override evidence |
|---|---|
| `late_chase` | OR high break, VWAP reclaim, ret_3m/5m positive, volume acceleration |
| `fade` | pullback reclaim, low-volume selloff recovery |
| `at_high` | consolidation then breakout |
| `or_missing` | alternative confirmation available |
| `gap_pullback_uncertain` | fresh support reclaim |

### C4. Exit And Hold Prompt Changes

목표:

- Claude가 메인 판단자이더라도 system hard SELL은 Claude HOLD에 막히지 않게 한다.
- soft exit은 Claude를 쓰되 반복 호출과 지연을 줄인다.

변경:

- `exit_lifecycle.final_action=SELL && claude_override_allowed=false`는 allowlist reason에서 live SELL로 연결한다.
- recovery micro no-carry, force exit, hard loss, pre-close는 advisor 질문 전에 system route로 처리한다.
- profit floor, trail stop, soft loss review는 Claude review 가능하되 SLA와 cache를 붙인다.
- cache는 hard/recovery/pre-close/broker mismatch에는 적용하지 않는다.

## Cost And Quality Change Ledger

### Measured / Planned Changes

| Version | Date | Change | Usage impact | Quality impact | Status |
|---|---:|---|---|---|---|
| `v0_initial` | 2026-04-29 | broad Claude usage | 115 calls, 367,224 tokens/day KST baseline | trade_ready quality exists, but late chase/exit gaps | measured |
| `v1_cost_review` | 2026-05-07 | cost analysis, no immediate removal of candidate_actions | 578,534 tokens/day recent avg | avoid removing high-value selection context | measured |
| `v2_quality_rework` | 2026-05-11 | Evidence Pack + v2 schema + hard/soft gates | input may rise, output should fall | late chase/weak BUY/hard SELL gaps targeted | in validation |
| `v2_output_compression` | 2026-05-11 | selection compressed max output | paper smoke output avg about 1,838 vs live pre-rework about 4,663 | must preserve parse/route quality | first smoke |
| `v2_hold_cache` | 2026-05-11 | soft hold cache | expected repeat hold calls down | hard exits excluded | needs N-session measure |
| `v2_exit_bypass` | 2026-05-11 | hard SELL allowlist bypass | little direct token impact | 012610-style shadow SELL gap targeted | needs live/paper replay |

### 2026-05-11 Smoke Measurement

출처: [Today Push Strategy Dev Review](../reports/today_push_strategy_dev_review_20260511.md)

| Metric | Value |
|---|---:|
| Paper Claude smoke calls | 3 |
| Input tokens | 16,937 |
| Output tokens | 5,515 |
| Cost | `$0.133536` |
| Average output/call | ~1,838 |
| Pre-rework live `select_tickers` average output | ~4,663 |

해석:

- output compression 방향은 확인됐다.
- Evidence Pack 때문에 input token은 후보 수에 따라 늘 수 있다.
- 실제 비용 평가는 `input + output + call count`를 함께 봐야 한다.

## Performance Target Ledger

### Trading Performance Targets

| Metric | Baseline | Target | Measurement |
|---|---:|---:|---|
| Closed trade PF, all | 0.59 | >= 1.00 | 10+ sessions or replay |
| Closed trade PF, KR | 0.43 | >= 1.00 | KR separate |
| Closed trade PF, US | 0.94 | >= 1.10 | US separate |
| Selection trade PF, all | 0.66 | >= 1.00 | ticker_selection_log |
| KR late chase BUY count | TBD | down without missed winner spike | candidate audit |
| hard SELL shadow repeats | TBD | 0 for allowlist reasons | exit lifecycle audit |
| recovery micro no-carry hold after force time | TBD | 0 | position/review audit |
| profit preservation review latency | TBD | within SLA | `review_latency_sec` |

### Prompt / API Targets

| Metric | Baseline | Target |
|---|---:|---:|
| Daily Claude tokens | 578,534/day recent avg | -7% first, -10% after compression validation |
| `select_tickers` avg output | ~4,663 pre-rework live | <= 2,200 unless retry |
| JSON parse success | 115/115 initial | 100% |
| v2 parse failure | TBD | visible in dashboard, no auto-ready fallback |
| missing price target for executable action | observed risk | 0 live executable |
| hold advisor hard-exit cache usage | should be 0 | 0 |

## Prompt Change QA Checklist

Each prompt change must record:

- raw prompt version
- response schema version
- input tokens
- output tokens
- parse success
- normalized output
- applied action after route validation
- route diff from previous prompt
- forced WATCH/PROBE demotions
- BUY/PROBE later MFE/MAE
- missed watch-only runup
- actual fill result if traded

Minimum gates before live expansion:

| Gate | Required result |
|---|---|
| schema parse | 100% in smoke and shadow sample |
| route safety | hard fact violations 0 |
| v2 fallback | no auto `trade_ready` promotion |
| late chase replay | 018880-style case WATCH/PROBE only unless fresh evidence |
| weak rebound replay | 078150-style case WATCH unless confirmation exists |
| profit preservation replay | 067170-style case review latency visible |
| recovery replay | 012610-style no-carry forced exit path visible |
| cost | no unexpected token/call loop |

## Tuning Feedback Contract

Tuning data must not broadly loosen the system.

Allowed:

- propose soft gate threshold changes
- provide similar failure examples to Claude prompt
- update cohort reliability
- recommend watch/probe/ready threshold candidates
- record rule version and expected impact

Not allowed:

- directly turn WATCH into BUY
- loosen KR late chase globally because one winner was missed
- override hard facts
- inject long natural language feedback into every prompt without token budget

Required output from tuner:

```json
{
  "tuning_contract_version": "tuning_feedback.v1",
  "scope": "KR|momentum|late_chase",
  "proposal_type": "soft_gate_threshold",
  "change": {
    "candidate_age_min": 120,
    "chase_pct": 5.0,
    "override_requires": ["or_high_break", "vwap_reclaim", "ret_3m_positive"]
  },
  "evidence_window": "2026-04-20..2026-05-11",
  "expected_effect": "reduce stale chase BUY without blocking fresh continuation",
  "requires_shadow": true
}
```

## Update Template

Use this table whenever Claude prompt or usage policy changes.

| Date | Version | Change | Before usage | After usage | Before quality | After quality | Decision |
|---|---|---|---:|---:|---:|---:|---|
| YYYY-MM-DD | `vX` | description | tokens/cost/calls | tokens/cost/calls | PF/MFE/MAE/parse | PF/MFE/MAE/parse | keep/revert/shadow |

Required note:

- Separate measured data from expected data.
- Do not count a local replay as live performance.
- Do not count token reduction as success if parse/route quality regresses.
- Do not count higher PF as valid if sample size is too small or only one market improved.

## Current Conclusion

As of 2026-05-11:

- Claude usage itself is justified. Initial data shows `trade_ready` had signal value.
- The main weakness was not "Claude is useless"; it was incomplete inputs, loose output contracts, repeated calls, and execution path gaps.
- The best direction is to keep Claude as the main judgment layer while making inputs evidence-based and outputs auditable.
- The first measurable success should be fewer late chase BUYs, fewer shadow hard SELL misses, lower repeated hold calls, and lower output tokens.
- Final success requires N-session live/paper measurement, not just replay.
