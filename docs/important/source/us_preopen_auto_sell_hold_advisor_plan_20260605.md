# US 장전 Auto-Sell / Hold Advisor 개선 플랜 - 2026-06-05

작성 일자: 2026-06-05
대상: US PathB `AUTO_SELL_REVIEW`, 장전 hard stop / loss stop, hold advisor 판단, 정규장 개장 재검증
결론: 조건부 적합. `AUTO_SELL`은 유지하고, 장전의 얕은 손절성 신호만 개장 재검증으로 넘긴다. 수익 보호/target/profit ladder/pre-close와 심각 손실 차단은 즉시 청산 가능 경로로 유지한다.

## 1. 목적

2026-06-04 US 장전 구간에서 NVDA, GOOGL, HPE, MSFT가 정규장 개장 전 매도됐다. 이 중 NVDA/GOOGL은 얕은 hard stop 위반 상태에서 정규장 개장 전 바로 매도됐고, 이후 반등 가능성이 확인됐다. HPE는 손실과 stop breach가 커서 즉시 매도 판단이 방어적이었으며, MSFT는 수익 상태의 보호성 hard stop으로 매도 결과가 상대적으로 적절했다.

개선 목적은 다음이다.

- 장전 가격만으로 얕은 hard stop/loss stop 매도가 바로 실주문으로 이어지는 것을 막는다.
- `AUTO_SELL`을 끄지 않고, 신호 감지기와 최종 실행 게이트를 분리한다.
- hold advisor가 판단할 때 장전/정규장/개장 직후 맥락을 명확히 받도록 한다.
- 이미 매도 진행 중이거나 청산된 포지션에 대해 hold advisor/auto-sell이 중복 매도 시도를 만들지 않게 한다.
- profit ladder, target, pre-close, broker truth, sizing, hard risk 보호 계약은 완화하지 않는다.

## 2. 보호 범위와 비목표

이번 요구서는 코드 변경이 아니라 개선 요구 정의다. 이후 구현 시 아래 보호 영역을 직접 건드리면 작업 설명, 커밋 메시지 또는 PR 본문에 `MD 위반 사항`을 남겨야 한다.

건드리지 않을 보호 영역:

- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard 완화 금지
- broker truth fail-closed 완화 금지
- PathB sizing reason split, fixed sizing, one-share-over-budget 정책 변경 금지
- profit ladder tier/env 값 변경 금지
- pre-close 청산 경로 비활성화 금지
- KIS order normalization / zero-holding stale reconcile 완화 금지
- `state/brain.json` 자동 정책 승격 또는 직접 수정 금지

비목표:

- 모든 auto-sell을 hold advisor가 무조건 override하게 만들지 않는다.
- 장전 매도를 전부 금지하지 않는다.
- 단기 사례만으로 profit ladder, target, pre-close 정책을 바꾸지 않는다.
- 매수 cap, 주문 수량, 주문 금액, 신규 진입 allowlist는 이번 요구 범위에 포함하지 않는다.

## 3. 재검증 근거

### 3.1 2026-06-04 장전 매도 사실

US 정규장 개장은 2026-06-04 22:30 KST다. 아래 네 종목은 모두 22:30 이전에 매도 주문/청산됐다.

| 종목 | advisor/신호 시각 | SELL SENT | CLOSED | 사유 | 매도가 | 실현 PnL | 판단 |
|---|---:|---:|---:|---|---:|---:|---|
| NVDA | 22:20:15 | 22:20:16 | 22:22:22 | hard_stop | 214.0756 | -0.30% | 얕은 장전 stop breach. 개장 재검증 후보 |
| GOOGL | 22:20:25 | 22:20:26 | 22:22:23 | hard_stop | 358.35 | -0.43% | 얕은 장전 stop breach. 개장 재검증 후보 |
| HPE | PathB 22:20:27 | 22:20:27 | 22:22:24 | claude_price_stop | 52.87 | -4.01% | 심각 손실. 즉시 매도 허용 후보 |
| MSFT | 22:25:41 | 22:25:42 | 22:26:13 | hard_stop | 431.48 | +1.29% | 수익 보호성 stop. 즉시 매도 허용 후보 |

주요 로그 근거:

- `logs/system/live_trading_20260604.log`: 22:20:15~22:20:27 NVDA/GOOGL advisor SELL, HPE PathB SELL SENT
- `logs/system/live_trading_20260604.log`: 22:22:21 preopen position review가 pending SELL 3건을 보고 개장 재검증을 스킵/대기 상태로 기록
- `logs/system/live_trading_20260604.log`: 22:22:22~22:22:25 NVDA/GOOGL/HPE CLOSED
- `logs/system/live_trading_20260604.log`: 22:22:43~22:22:54 HPE post-close advisor SELL 뒤 중복 매도 precheck block
- `logs/system/live_trading_20260604.log`: 22:25:41~22:26:14 MSFT advisor SELL, SELL SENT, CLOSED

### 3.2 Hold advisor 입력의 부족

2026-06-04 장전 `AUTO_SELL_REVIEW` 입력에는 `or_formed=false`, `or_high=0`, `or_low=0`이 들어갔지만, 이것이 "정규장이 아직 열리지 않아 OR이 형성되지 않음"인지, 데이터 누락인지 구분하는 session context가 부족했다.

| 종목 | decision_stage | decision | driver | PnL | hard_stop_distance | OR | selected_reason |
|---|---|---|---|---:|---:|---|---|
| NVDA | AUTO_SELL_REVIEW | SELL | hard_stop | -0.742% | -0.124% | false | 비어 있음 |
| GOOGL | AUTO_SELL_REVIEW | SELL | hard_stop | -0.838% | -0.596% | false | 비어 있음 |
| HPE | AUTO_SELL_REVIEW | SELL | hard_stop | -4.467% | -2.782% | false | 있음 |
| MSFT | AUTO_SELL_REVIEW | SELL | bounded_hold | +0.872% | -0.055% | false | 비어 있음 |

MSFT 보정:

- 22:22:34의 `INTRADAY_REVIEW`는 HOLD였다.
- 22:25:41의 `AUTO_SELL_REVIEW`는 triage가 처음 HOLD였지만 challenge 이후 최종 `STOP_LOSS/SELL`이 됐다.
- 따라서 MSFT는 "advisor HOLD를 시스템 hard_stop이 무시한 케이스"가 아니라 "profit-protective stop 성격의 auto-sell을 hold advisor challenge가 최종 SELL로 확정한 케이스"로 분류한다.
- R-02 수용 기준의 MSFT 항목은 이 경로를 기준으로 검증한다.

개선 필요점:

- `session_phase`, `regular_open_at`, `minutes_to_regular_open` 제공
- `or_formed=false`의 이유를 `regular_market_not_open` / `data_missing`으로 분리
- 장전 quote 품질, spread, recent tick age, price source 제공
- shallow stop breach와 severe stop breach를 advisor에게 명확히 표시
- `selected_reason`이 빈 hard_stop 경로는 원래 진입 thesis와 PathB plan 근거를 복구해서 전달

### 3.3 전체 기간 성과 재검증

재계산 기준:

- `logs/hold_advisor/decisions_*.jsonl`: 2026-05-04 ~ 2026-06-05
- `state/live_decisions.jsonl`: US closed 137건
- `data/ml/decisions.db`: US live `claude_price` closed 117건
- `data/v2_event_store.db`: PathB auto-sell metadata

US `AUTO_SELL_REVIEW` episode 요약:

| 구간 | action | episode | linked | review PnL avg | close PnL avg | win |
|---|---|---:|---:|---:|---:|---:|
| preopen | HOLD | 4 | 4 | +4.60% | +4.24% | 4/4 |
| preopen | SELL | 12 | 11 | +2.35% | +3.12% | 6/11 |
| regular | HOLD | 40 | 33 | +4.18% | +4.82% | 26/33 |
| regular | SELL | 100 | 85 | +0.48% | +0.49% | 39/85 |

US PathB `claude_price` closed 성과:

| close_reason | n | avg PnL | win |
|---|---:|---:|---:|
| CLOSED_CLAUDE_PRICE_TARGET | 10 | +5.31% | 10/10 |
| CLOSED_CLAUDE_SELL | 6 | +3.91% | 6/6 |
| CLOSED_TRAILING_STOP | 2 | +3.79% | 1/2 |
| CLOSED_CLAUDE_PRICE_PRE_CLOSE | 34 | +1.44% | 19/34 |
| CLOSED_PROFIT_LADDER | 20 | +1.10% | 13/20 |
| CLOSED_HARD_STOP | 6 | +0.20% | 3/6 |
| CLOSED_CLAUDE_PRICE_STOP | 3 | -0.03% | 2/3 |
| CLOSED_LOSS_CAP | 19 | -2.30% | 0/19 |

해석:

- target, Claude sell, trailing stop, pre-close, profit ladder는 전체 기간 성과가 양호하므로 비활성화하면 안 된다.
- loss_cap은 손실을 기록하지만 목적이 수익 창출이 아니라 손실 제한이다.
- preopen SELL 전체 평균은 나쁘지 않지만, 얕은 hard_stop과 수익 보호성/target/profit 매도가 섞여 있어 한 정책으로 다루면 안 된다.

### 3.4 관련 요구서와의 정합성

이 문서의 `OPEN_CONFIRM_RECHECK`는 `docs/reports/preopen_opening_judgment_requirements_20260605.md`의 `opening_confirm` phase와 같은 개장 후 실행 가능 판단 체계를 사용한다. 다만 대상과 저장 책임은 분리한다.

| 구분 | preopen/opening judgment 요구서 | 이 요구서 |
|---|---|---|
| 공통 phase | `opening_confirm`, 기본 T+5 | `opening_confirm` 안에서 PathB exit recheck 수행 |
| 대상 | 신규 매수, PathA/PathB 신규 후보, 보유 종목 pre-session sell flag | US PathB preopen shallow stop defer |
| 저장 책임 | TradingBot phase/authority, 기존 position recheck flag | PathB run plan metadata 또는 기존 event/state 기반 defer record |
| 연결 방식 | phase/timer/fresh quote trigger 제공 | trigger를 받아 broker truth 기준으로 recheck |

구현 권장:

- 공통 이름은 `opening_confirm`으로 둔다.
- PathB exit defer의 이벤트명은 `OPEN_CONFIRM_RECHECK`로 두되, 별도 phase를 만들지 않는다.
- TradingBot은 개장 후 판단 시점과 fresh quote trigger만 제공한다.
- PathB는 defer record 소유권, severity, original signal, broker truth 재확인을 자체 관리한다.
- "PathB 전용 defer queue"는 새 DB schema를 뜻하지 않는다. 구현 기본값은 기존 PathB run plan metadata, 기존 event store, 또는 런타임 state/audit 기록을 사용한다. 별도 영구 저장소는 운영자 승인 전 만들지 않는다.
- 공유 mutable state로 `pending_next_open_sell`을 직접 확장하기보다 이벤트/트리거 방식으로 연결한다.

## 4. 최종 개선 요구사항

### R-01. ExitSignal을 감지와 실행으로 분리

현재 문제: PathB `scan_exits()`에서 hard_stop/loss 계열 신호가 감지되면 장전 여부와 무관하게 `_submit_sell()`로 이어질 수 있다.

요구:

- `ExitSignal`은 `detected_exit_signal`로 먼저 기록한다.
- 실행 전 `preopen_exit_policy` 또는 동등한 정책 게이트가 `SELL_NOW`, `DEFER_OPEN_RECHECK`, `SKIP_STALE_OR_CLOSED`, `NON_REVIEWABLE_SELL_NOW` 중 하나로 분류한다.
- hard risk, broker truth, zero holding, emergency/system close는 advisor가 override하지 않는다.
- `DEFER_OPEN_RECHECK`는 live 주문 실행이 아니므로 `AUTO_SELL_REVIEW` 실행 cooldown을 소비하지 않는다. 대신 defer record 자체에 동일 ticker/signal 반복 방지 throttle을 둔다.

수용 기준:

- NVDA/GOOGL형 장전 얕은 hard_stop은 22:30 전 `SELL SENT`가 아니라 `DEFER_OPEN_RECHECK`로 기록된다.
- HPE형 심각 손실은 `SELL_NOW`가 허용된다.
- profit ladder/target/pre-close는 기존 수익 경로를 유지한다.

### R-02. 장전 stop 신호 severity 분류

요구:

- 장전 stop 계열을 `shallow_loss_stop`, `severe_loss_stop`, `profit_protective_stop`으로 구분한다.
- 초기 기준값은 shadow 관찰 후 조정 가능하게 코드 상수 또는 env-readonly config로 관리하되, 운영 env 값을 무단 변경하지 않는다.

초기 정책 기준:

| 조건 | 정책 |
|---|---|
| `pnl_pct > -1.5%` and `hard_stop_distance_pct >= -0.75%` | `DEFER_OPEN_RECHECK` |
| `pnl_pct <= -2.5%` or `hard_stop_distance_pct <= -1.0%` | `SELL_NOW` |
| 위 두 조건 사이의 중간 구간 | shadow 기간 기본 `SELL_NOW`, `severity_boundary_case=true` 기록 |
| `pnl_pct > 0` and stop이 profit floor/protective stop 성격 | profit protection으로 분류, advisor 확인 뒤 `SELL_NOW` 허용 |
| quote stale/invalid 또는 hard_stop_distance 비정상값 | 매도 근거로 사용 금지, defer/audit |

수용 기준:

- NVDA: PnL -0.742%, stop distance -0.124% -> defer
- GOOGL: PnL -0.838%, stop distance -0.596% -> defer
- HPE: PnL -4.467%, stop distance -2.782% -> sell now
- MSFT: PnL +0.872%, stop distance -0.055%, profit protective 성격 -> advisor 확인 뒤 sell now 가능
- PnL -1.8% 또는 stop distance -0.85% 같은 중간 구간 -> shadow 기간에는 기존 방어 정책을 유지해 sell now로 처리하되, `severity_boundary_case`로 별도 집계한다. live defer 확장은 이 구간의 shadow 결과를 본 뒤 별도 승인한다.

### R-03. 정규장 개장 재검증 단계 추가

요구:

- 장전 defer는 `opening_confirm` phase에서 실행되는 `OPEN_CONFIRM_RECHECK`로 넘긴다.
- PathB 전용 defer record를 기본 설계로 둔다. 새 DB schema를 전제하지 않고 기존 PathB run plan metadata/event/state 기록을 우선 사용한다.
- `trading_bot.py`의 기존 `pending_next_open_sell` / `_maybe_recheck_pending_next_open_sells()`는 phase/timer/fresh quote trigger의 참고 경로로만 사용하고, PathB defer 상태를 직접 이중 저장하지 않는다.
- 첫 실행 시점은 첫 요구서의 `opening_confirm` 기준을 따른다. 즉 T+5 fresh opening judgment와 live context가 성공한 뒤, 개장 후 0~15분 안에 fresh quote, spread, opening print, OR/VWAP 가능 여부를 보고 최종 실행한다.

수용 기준:

- defer된 포지션은 정규장 개장 전 주문이 나가지 않는다.
- 정규장 개장 후 fresh quote가 없으면 sell/order submit을 하지 않고 `WAIT_FRESH_OPEN_QUOTE`로 남긴다.
- 개장 후 여전히 stop 아래이고 회복 근거가 없으면 `SELL_NOW_AFTER_OPEN_CONFIRM`로 매도한다.
- 개장 후 회복하면 `CLEAR_DEFERRED_STOP` 또는 hold advisor HOLD로 정리한다.
- PathB defer record와 TradingBot opening phase 사이에 상태 이중화나 레이스가 생기지 않는다. TradingBot은 "지금 recheck 가능" 이벤트만 보내고, PathB가 broker truth 기준 최종 상태를 결정한다.

### R-04. Hold advisor prompt/context 보강

요구:

- `minority_report/hold_advisor.py` prompt에 아래 context를 넣는다.
  - `session_phase`
  - `regular_open_at`
  - `minutes_to_regular_open`
  - `or_status_reason`
  - `premarket_quote_quality`
  - `bid_ask_spread_pct`
  - `last_quote_age_sec`
  - `exit_signal_severity`
  - `recover_above`
  - `opening_recheck_deadline`
  - `original_selected_reason`
  - `pathb_plan_target/stop`
- 장전 `AUTO_SELL_REVIEW`에서 HOLD가 가능하려면 `recover_above`, `invalid_if`, `next_review_min`, `protective_stop`을 명시해야 한다.
- 장전 SELL은 `why_not_wait_for_open` 필드를 요구한다.
- 장전 shallow stop에서 정책 게이트가 advisor 호출 전에 `DEFER_OPEN_RECHECK`를 결정한 경우에는 Claude hold advisor를 호출하지 않는다.
- advisor가 이미 호출된 뒤 최종 결론이 `DEFER_OPEN_RECHECK`인 경우에는 `AUTO_SELL_REVIEW` HOLD cooldown이 아니라 defer record throttle만 기록한다.

수용 기준:

- `or_formed=false`만 보고 "반등 근거 없음"으로 단정하지 않는다.
- 장전 shallow stop에서 advisor가 `DEFER_OPEN_RECHECK` 또는 `HOLD_UNTIL_OPEN_CONFIRM` 성격의 결론을 낼 수 있다.
- 기존 `AUTO_SELL_REVIEW` HOLD cooldown guard는 그대로 적용된다.
- defer 때문에 개장 후 필요한 recheck가 `AUTO_SELL_REVIEW_HOLD_COOLDOWN_MINUTES`에 막히지 않는다. 동시에 개장 전 동일 ticker에 대해 Claude 재호출이 반복되지 않는다.

### R-05. post-close / pending sell 중복 리뷰 차단

문제: HPE는 22:22:24에 이미 CLOSED됐지만 22:22:43/22:22:52에 hold advisor SELL이 다시 발생했고, 22:22:54에 `SELL_PRECHECK_INSUFFICIENT_HOLDING`으로 차단됐다.

요구:

- hold advisor 또는 auto-sell review 호출 전에 아래 상태를 확인한다.
  - broker qty 0
  - local position missing
  - PathB run already CLOSED
  - pending sell order exists
  - sell reconcile in-flight
- 위 조건이면 advisor 호출 없이 `SKIP_STALE_OR_CLOSED`로 audit만 남긴다.

수용 기준:

- HPE형 post-close duplicate advisor SELL이 발생하지 않는다.
- safety precheck block에 의존하지 않고 상위 단계에서 중복 리뷰를 제거한다.
- broker truth가 stale이면 자동 정리하지 않고 fail-closed / wait 상태로 남긴다.

### R-06. 로그와 대시보드 가시성 추가

요구:

- 아래 이벤트를 JSONL 또는 lifecycle/audit 로그에 남긴다.
  - `preopen_exit_signal_detected`
  - `preopen_exit_policy_decision`
  - `deferred_open_recheck_created`
  - `open_confirm_recheck_result`
  - `skip_stale_or_closed_review`
- 대시보드에는 "장전 즉시 매도", "개장 재검증 대기", "개장 후 확인 매도", "중복 리뷰 차단"을 구분해서 보여준다.

수용 기준:

- 2026-06-04 사례를 리플레이/테스트할 때 NVDA/GOOGL/HPE/MSFT가 각각 어떤 정책 분기로 갔는지 한 화면 또는 로그에서 확인 가능하다.
- `AUTO_SELL_REVIEW` raw decision과 최종 execution decision이 분리 기록된다.

### R-07. Shadow 우선 롤아웃

요구:

1. 1단계: shadow/log-only. 실제 매도 동작은 바꾸지 않고 정책 분류만 기록한다.
2. 2단계: 얕은 장전 hard_stop/loss_stop에 한해 `DEFER_OPEN_RECHECK`를 live 적용한다.
3. 3단계: 개장 후 재검증 성과를 5거래일 이상 비교한다.
4. 4단계: loss_cap/claude_price_stop 일부를 severity 기준으로 확장할지 별도 승인한다.

수용 기준:

- shadow 기간에는 주문 API 호출 수와 실주문 결과가 기존과 동일하다.
- live 적용은 shallow preopen stop에만 제한된다.
- miss loss, saved rebound, delayed sell slippage를 모두 기록한다.

## 5. 구현 대상 파일

예상 수정 파일:

- `runtime/pathb_runtime.py`
  - `scan_exits()`에서 exit signal 감지 후 session/severity gate 적용
  - `_run_pathb_sell_review_gate()` 입력 context 확장
  - `_submit_sell()` 전 stale/closed/pending-sell guard 강화
  - profit ladder tier, pre-close, sizing, broker truth fail-closed는 변경 금지
- `minority_report/hold_advisor.py`
  - prompt/context/schema에 session phase와 장전 quote 품질 추가
  - 장전 SELL/HOLD/DEFER 판단 필드 추가
- `trading_bot.py`
  - 기존 opening recheck trigger와 PathB defer record 연결
  - 개장 후 recheck 로그/상태 반영
- `tests/test_auto_sell_claude_gate.py`
  - cooldown guard 회귀 유지
  - preopen shallow stop이 반복 Claude 호출로 번지지 않는지 검증
- `tests/test_pathb_runtime.py`
  - severe vs shallow stop policy 검증
- 신규 후보: `tests/test_preopen_auto_sell_recheck.py`
  - NVDA/GOOGL/HPE/MSFT형 fixture 검증

## 6. 항목별 적합성 검토

| 항목 | 적합성 | 근거 | 보완 조건 |
|---|---|---|---|
| `AUTO_SELL` 유지 | 적합 | 전체 기간 target/profit ladder/pre-close 성과가 양호 | 감지와 실행을 분리 |
| hold advisor 중심 강화 | 조건부 적합 | 장전 session context 부족이 확인됨 | reviewable exit에 한정 |
| 모든 장전 매도 보류 | 부적합 | HPE 같은 심각 손실과 target/profit 보호 매도가 존재 | shallow stop만 defer |
| 얕은 hard_stop 개장 재검증 | 적합 | NVDA/GOOGL은 stop breach가 작고 OR 미형성 | fresh quote/개장 확인 필수 |
| threshold 중간 구간 | 조건부 적합 | -1.5%~-2.5%, -0.75%~-1.0% 공백이 있었음 | shadow 기간 기본 sell now + boundary 집계 |
| MSFT bounded_hold 분류 | 적합, 보정 완료 | triage HOLD 뒤 challenge 최종 STOP_LOSS/SELL | "시스템 override"가 아니라 advisor challenge SELL로 기록 |
| PathB defer record 분리 | 적합 | PathB 상태 독립성과 레이스 방지 필요 | TradingBot은 trigger만 제공 |
| defer와 cooldown 분리 | 필수 | 실행되지 않은 preopen SELL이 opening recheck를 막으면 안 됨 | auto-sell cooldown 대신 defer throttle |
| profit ladder/pre-close 유지 | 필수 | DB 평균 PnL 양수, 보호 수익 경로 | tier/env 변경 금지 |
| HPE 중복 리뷰 차단 | 적합 | post-close advisor SELL과 safety block 확인 | broker truth stale이면 자동 정리 금지 |
| threshold 즉시 최적화 | 보류 | preopen 표본이 작음 | shadow 5거래일 이상 필요 |
| 코드 구현 시 보호 영역 수정 | 조건부 적합 | 원인이 PathB auto-sell/hold advisor 경로에 있음 | `MD 위반 사항` 기록 필수 |

## 7. 재검토 반영 결과

초기 아이디어 중 "오토셀이 있어도 hold advisor 위주로 전체를 가져간다"는 방향은 그대로 적용하면 부적합하다. 전체 기간 DB에서 target, profit ladder, pre-close가 수익을 만들고 있고, loss_cap은 손실 제한 역할을 한다. 따라서 최종 설계는 다음처럼 축소한다.

- `AUTO_SELL`은 끄지 않는다.
- hold advisor는 reviewable exit의 최종 판단 품질을 높이는 역할로 둔다.
- 장전 얕은 stop은 advisor SELL이어도 바로 주문하지 않고 open confirm으로 넘긴다.
- threshold 중간 구간은 shadow 기간에는 기존 방어 정책을 유지해 sell now로 처리하고, boundary case로 별도 집계한다.
- 장전 심각 손실은 advisor 대기 없이 즉시 매도 가능하다.
- 수익 보호성 hard_stop, target, profit ladder, pre-close는 현행 수익 구조를 유지한다.
- stale/closed/pending sell guard는 advisor 호출 전 단계에 둔다.
- `OPEN_CONFIRM_RECHECK`는 별도 phase가 아니라 첫 요구서의 `opening_confirm` phase 안에서 수행되는 PathB exit recheck 이벤트다.
- defer는 `AUTO_SELL_REVIEW` cooldown을 소비하지 않고, 별도 defer throttle로 반복 호출을 막는다.

재검토 결론: 진행 적합. 단, 구현은 shadow -> 제한 적용 -> 성과 비교 순서로 진행해야 하며, 첫 live 적용 범위는 `US preopen shallow hard_stop/loss_stop defer`로 제한한다.

## 8. 검증 계획

개발 중 최소 검증:

```powershell
python -m pytest tests/test_preopen_auto_sell_recheck.py -q
python -m pytest tests/test_auto_sell_claude_gate.py tests/test_pathb_runtime.py -q
python -m py_compile runtime/pathb_runtime.py minority_report/hold_advisor.py trading_bot.py
```

보호 영역 회귀:

```powershell
python -m pytest tests/test_auto_sell_claude_gate.py::AutoSellClaudeGateTests::test_pathb_loss_cap_hold_respects_reask_cooldown -q
python -m pytest tests/test_pathb_profit_protection.py tests/test_plan_a_hold_policy.py -q
```

운영 전 read-only 확인:

```powershell
python tools/live_preflight.py --mode live --skip-dashboard --json
```

리플레이/fixture 수용 기준:

- NVDA 2026-06-04 22:20:15: `DEFER_OPEN_RECHECK`
- GOOGL 2026-06-04 22:20:25: `DEFER_OPEN_RECHECK`
- HPE 2026-06-04 22:20:27: `SELL_NOW`
- MSFT 2026-06-04 22:25:41: `AUTO_SELL_REVIEW` triage HOLD -> challenge 최종 STOP_LOSS/SELL -> profit-protective `SELL_NOW`
- HPE 2026-06-04 22:22:43 이후: `SKIP_STALE_OR_CLOSED`
- 중간 severity fixture: shadow 기간 `SELL_NOW`, `severity_boundary_case=true`, 향후 defer 확장 후보로 집계
- deferred fixture: opening recheck가 `AUTO_SELL_REVIEW` HOLD cooldown 때문에 누락되지 않고, 동일 ticker 반복 Claude 호출은 defer throttle로 차단

## 9. 남은 위험

- preopen 표본이 작아 threshold를 바로 넓히면 손실 제한 기능이 약해질 수 있다.
- 장전 quote 품질이 낮은 경우 defer가 유리할 수도, 더 큰 손실로 이어질 수도 있다.
- PathB와 TradingBot의 pending next open 상태 연결이 어긋나면 중복 상태가 생길 수 있다.
- 구현은 보호 영역을 직접 지나갈 가능성이 높으므로 `MD 위반 사항` 기록과 집중 회귀 테스트가 필요하다.

## 10. 최종 판정

개선 요구는 적합하다. 다만 적합한 방향은 "hold advisor가 모든 auto-sell을 대신한다"가 아니라 "auto-sell 신호를 유지하되 장전 얕은 손절성 신호의 실행만 개장 확인으로 늦추고, advisor에는 session context를 보강하는 것"이다.

다음 구현 승인 시 첫 패치 범위는 다음으로 제한한다.

- US preopen shallow hard_stop/loss_stop defer
- open confirm recheck 연결
- hold advisor session context 추가
- stale/closed/pending sell 중복 리뷰 차단
- shadow/audit 로그 추가
