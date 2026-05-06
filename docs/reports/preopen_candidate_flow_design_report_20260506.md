# Trading System Candidate Execution Development Specification - 2026-05-06

대상: `claudetrade` 장전 후보, 기본 후보, 장중 후보, Claude 판단, PlanA/PathB 진입, 매도/보유 재판단, replay QA

목적: 기존 분석 리포트를 개발 명세서 형태로 재정렬한다. 각 개발 항목은 `무엇을 구현할지`뿐 아니라, 기존 분석에서 나온 코멘트와 판단 근거를 `왜 하는가`로 함께 기록한다.

---

## 1. 결론

현재 시스템은 후보를 못 찾는 시스템이 아니다. 후보를 찾고도 아래 중간 계층이 약하다.

```text
후보군 구성
-> 실시간 상태화
-> Claude 상태 판단
-> PlanA/PathB 실행 분기
-> 수량/예산 검증
-> 수익 보호/매도 재판단
-> replay 검증
```

따라서 이번 개발의 핵심은 새 시스템을 갈아엎는 것이 아니라 기존 구조를 명시적인 계약으로 정리하는 것이다.

```text
장전 후보와 기본 후보는 넓게 본다.
Claude에게는 정리된 prompt_pool만 보낸다.
Claude는 미래 예측자가 아니라 상태 분류자로 쓴다.
매수는 probe/buy/wait/add/avoid로 나눈다.
매도는 system guard가 먼저 수익을 보호하고, Claude는 예외를 검증한다.
모든 판단은 known_at 기준 replay로 검증한다.
```

최종 개발 순서:

```text
schema -> pool -> log -> Claude action -> gate -> routing -> sizing -> exit -> replay
```

---

## 2. 기존 분석 코멘트 요약

### 2.1 지켜야 할 기존 구조

기존 구조의 기본 철학은 맞다.

```text
장전 후보 수집
-> 개장 후 확인
-> Claude 후보 선정
-> PlanA/PathB 실행
-> 보유 중 Claude 재판단
```

기존 코멘트:

- 장전 후보를 바로 매수하지 않고 개장 후 확인하는 방향은 맞다.
- Claude가 후보를 고르고, 실제 주문은 PlanA/PathB/리스크 엔진이 검증하는 역할 분리는 맞다.
- Claude 재판단으로 매도를 다시 보는 구조도 장점이다.
- 문제는 후보, 상태, 실행, 수익 보호 사이의 연결이 약하다는 점이다.

### 2.2 핵심 문제

기존 분석에서 반복 확인된 문제:

| 문제 | 기존 코멘트 | 개발 관점 |
|---|---|---|
| `intraday_live_unconfirmed` 차단 | 좋은 장에서도 `judgment_not_executable`로 0거래 가능 | phase/gate 상태를 명확히 기록하고 조건부 허용 또는 격상 필요 |
| 후보 funnel 단절 | 장전 후보 59개가 full execution funnel에 연결되지 않음 | full_pool/prompt_pool/execution_pool 분리 필요 |
| SOFT/HARD pin 불명확 | SOFT pin이 로드되지만 조용히 버려질 수 있음 | source_tags와 merge 규칙 필요 |
| 여러 rescreen builder | 경로별 후보 구성이 달라 추적이 어려움 | Unified Candidate Pool 필요 |
| 조건이 많고 출력이 `skip`으로 뭉침 | 첫 번째 blocker만 보이고 나머지 gate 상태가 사라짐 | Gate Evaluation Matrix 필요 |
| Claude 상태가 거침 | `watchlist/trade_ready`만으로는 probe/wait/avoid 표현 불가 | `candidate_actions` 필요 |
| 수량/예산 경계 | min_order override가 cap을 뚫을 수 있음 | sizing/budget contract 필요 |
| 매도 판단이 target에 끌림 | 높은 목표가가 수익 보호를 늦출 수 있음 | MFE/giveback/floor 중심 prompt 필요 |
| replay 착시 | 5m/30m 사후 필터는 look-ahead bias 위험 | known_at future-blind replay 필요 |

### 2.3 오늘 KR / 어제 US 분석에서 얻은 교훈

기존 시뮬레이션 해석:

- 장전 후보 전체를 다 사는 구조는 답이 아니다.
- 상태가 확인된 일부 후보는 의미 있는 알파가 있었다.
- 다만 5m/30m 결과를 사후에 보고 필터링하면 실제 성과보다 과대평가된다.
- 실제 시스템은 개장 5분에는 5분 정보만 알고, 30분 정보는 30분 후에야 안다.
- 따라서 "30분 결과가 좋았던 종목을 샀다면"이 아니라 "그 시점에 알 수 있던 정보로 probe/add/exit를 어떻게 했을지"를 replay해야 한다.

기존 코멘트:

```text
P1까지 개선해도 사후 백테스트 숫자가 그대로 나오지는 않는다.
P1은 이론 상한이 아니라 live decision quality를 높이는 입력 개선이다.
```

개발 결론:

```text
known_at feature
candidate action
probe-first entry
profit protection
future-blind replay
```

이 5개를 한 묶음으로 구현해야 한다.

---

## 3. 개발 범위

### 3.1 포함

```text
D1. Data model / schema 계약
D2. Unified Candidate Pool
D3. Post-open feature snapshot
D4. Claude candidate_actions schema
D5. Gate Evaluation Matrix
D6. PlanA/PathB action routing
D7. Sizing/budget contract
D8. Exit lifecycle / hold advisor
D9. Observability
D10. Future-blind replay
D11. QA checklist
D12. Rollout / rollback
```

### 3.2 제외 또는 2차 이후

1차 live 범위에서 제외할 항목:

```text
ADD_READY 실제 추가매수
partial sell 실주문
alternative_opportunity_score 자동 계산
VWAP 고도화가 필요한 정밀 체결 모델
brain.json 자동 정책 변경
```

기존 코멘트:

- `ADD_READY`는 평균단가, profit floor, trailing 재계산이 필요해 1차 live에서 위험하다.
- partial sell은 브로커 API는 가능하더라도 현재 `_execute_sell()`이 전체 청산 중심이므로 별도 lifecycle 설계가 필요하다.
- VWAP/alternative score는 candidate pool과 feature snapshot이 안정된 뒤에 붙여야 한다.

---

## 4. 기존 코드 접점

| 영역 | 현재 코드 |
|---|---|
| 시장 판단 | `minority_report/analysts.py::get_three_judgments()` |
| 후보 선정 | `minority_report/analysts.py::select_tickers()` |
| selection 적용 | `trading_bot.py::_apply_selection_meta()` |
| trade_ready normalize | `trading_bot.py::_normalize_selection_meta_runtime()` |
| entry trade_ready 확인 | `trading_bot.py::_is_trade_ready_ticker()` |
| 장전 후보 처리 | `trading_bot.py` preopen/session_open 경로 |
| rescreen | `trading_bot.py::manual_rescreen()`, `_partial_reselect()`, `_reinvoke_analysts()` |
| PlanA 실행 | `trading_bot.py::run_cycle()` 신규 진입 루프 |
| PathB 등록 | `runtime/pathb_runtime.py::register_from_selection_meta()` |
| PathB 수량 | `runtime/pathb_runtime.py::_pathb_qty()` |
| 매도 후보 | `risk_manager.py::get_exit_candidates()` |
| soft exit | `trading_bot.py::_try_soft_exit_arbitration()` |
| hold advisor | `minority_report/hold_advisor.py::ask()` |
| quick exit | `minority_report/quick_exit_check.py::quick_exit_check()` |
| tuning | `minority_report/tuner.py::tune()` |
| param review | `strategy/param_tuner.py::claude_review()` |

---

## 5. 공통 action 계약

Claude와 system이 공유할 action enum:

| action | 의미 | 실행 |
|---|---|---|
| `WATCH` | 관찰 | 주문 없음 |
| `PROBE_READY` | 작게 먼저 진입 가능 | PlanA probe |
| `BUY_READY` | 정상 진입 가능 | PlanA buy |
| `ADD_READY` | 보유 종목 추가 가능 | 1차는 shadow only |
| `PULLBACK_WAIT` | 지금 추격 금지, 눌림 대기 | PathB plan |
| `AVOID` | 당일 회피 | 주문 없음, 사유 기록 |

기존 코멘트 반영:

- 기존 `trade_ready`는 너무 거칠다.
- 오늘 같은 장에서는 "사도 됨"과 "좋지만 지금 비쌈"과 "작게만 먼저"가 구분되어야 한다.
- `PULLBACK_WAIT`를 단순 `WAIT`로 처리하면 PathB에 전달되지 않고 좋은 후보가 사라진다.

> **[검토 — 섹션 5]**
>
> **Claude action과 system final_action을 구분해야 한다.**
>
> 현재 표에 있는 6개 action은 Claude가 출력하는 값이다. 그런데 `GateEvaluation.final_action`에는 `HARD_BLOCK`이 포함된다. `HARD_BLOCK`은 Claude가 낼 수 없는 system 결과이므로 이 두 enum을 같은 표에 두면 혼동된다.
>
> ```text
> Claude action enum (입력):  WATCH / PROBE_READY / BUY_READY / ADD_READY / PULLBACK_WAIT / AVOID
> System final_action (출력): HARD_BLOCK / WATCH / PROBE_READY / BUY_READY / PULLBACK_WAIT / AVOID / SIZE_CAP / WAIT
> ```
>
> D5 구현 시 Claude action → system final_action 변환 규칙이 명시되어야 한다.
>
> **action 만료 규칙이 없다.**
>
> 장전에 `BUY_READY`로 분류된 종목이 30분 후에도 같은 상태를 유지하면 불리한 가격에 진입할 수 있다. `CandidateAction.expires_at`을 정의했으나 누가 어떤 기준으로 설정하는지, 만료 후 어느 action으로 전환하는지 이 섹션에서 명시해야 한다.

---

## D1. Data model / schema 계약

### 왜 하는가

기존 코멘트:

- `today_tickers`, `today_judgment["universe_tickers"]`, `selection_meta`, `trade_ready_tickers`가 서로 다른 의미로 종목 목록을 관리한다.
- 신규 pool 모듈을 그냥 추가하면 4번째 종목 목록이 생긴다.
- 먼저 공통 schema를 정의해야 구현 중 재설계가 줄어든다.

### 목표

후보, Claude action, post-open feature, gate 평가, 포지션 lifecycle, Claude 호출 trace를 명시적인 schema로 고정한다.

### 신규/수정 파일

| 파일 | 작업 |
|---|---|
| `runtime/candidate_pool_runtime.py` | `CandidateRecord`, pool builder |
| `runtime/post_open_features.py` | `PostOpenFeatureSnapshot` |
| `runtime/gate_evaluation.py` | `GateEvaluation` |
| `minority_report/analysts.py` | `candidate_actions` parse 계약 |
| `trading_bot.py` | `selection_meta`에 신규 schema 보존 |
| `tests/test_candidate_schema.py` | schema normalize 테스트 |

### CandidateRecord

```json
{
  "market": "KR",
  "ticker": "001440",
  "name": "대한전선",
  "session_date": "2026-05-06",
  "asof": "2026-05-06T09:05:00+09:00",
  "source_tags": ["preopen", "opening_fresh"],
  "source_ranks": {"preopen": 16, "opening_fresh": 4},
  "base_score": 0.0,
  "preopen_score": 0.0,
  "intraday_score": 0.0,
  "prompt_score": 0.0,
  "price": 0.0,
  "turnover": 0.0,
  "liquidity_bucket": "high|mid|low|unknown",
  "sector": "",
  "market_type": "KOSPI|KOSDAQ|NASDAQ|NYSE|AMEX|ETF|unknown",
  "preopen_pin": "HARD|SOFT|NONE",
  "data_quality": "good|mixed|poor|unknown",
  "features": {},
  "stale": false,
  "stale_reason": ""
}
```

### CandidateAction

```json
{
  "ticker": "001440",
  "action": "WATCH|PROBE_READY|BUY_READY|ADD_READY|PULLBACK_WAIT|AVOID",
  "confidence": 0.0,
  "entry_style": "none|early_probe|normal_buy|pullback|add",
  "recommended_strategy": "momentum|gap_pullback|mean_reversion|opening_range_pullback|observe",
  "size_intent": "none|probe|small|normal|aggressive",
  "why_now": "",
  "invalidation_condition": "",
  "add_condition": "",
  "avoid_condition": "",
  "path": "PlanA|PathB|Both|None",
  "expires_at": "",
  "price_targets": {
    "reference_price": 0.0,
    "buy_zone_low": 0.0,
    "buy_zone_high": 0.0,
    "sell_target": 0.0,
    "stop_loss": 0.0,
    "reward_risk": 0.0,
    "confidence": 0.0,
    "invalid_if": ""
  }
}
```

### PostOpenFeatureSnapshot

```json
{
  "market": "KR",
  "ticker": "001440",
  "asof": "2026-05-06T09:05:00+09:00",
  "known_at": "2026-05-06T09:05:00+09:00",
  "anchor_time": "2026-05-06T09:00:00+09:00",
  "anchor_price": 0.0,
  "current_price": 0.0,
  "ret_3m_pct": null,
  "ret_5m_pct": null,
  "ret_10m_pct": null,
  "ret_30m_pct": null,
  "open_range_high": 0.0,
  "open_range_low": 0.0,
  "or_formed": false,
  "vwap": null,
  "vwap_distance_pct": null,
  "volume_ratio_open": null,
  "spread_pct": null,
  "momentum_state": "unknown|early_strength|controlled_strength|overextended|fade|pullback_setup|late_mover",
  "data_quality": "good|mixed|poor|unknown"
}
```

### GateEvaluation

```json
{
  "market": "KR",
  "ticker": "001440",
  "asof": "2026-05-06T09:05:20+09:00",
  "selection_action": "PROBE_READY",
  "hard_safety": {"status": "OK|BLOCK", "reasons": []},
  "soft_safety": {"status": "OK|SIZE_CAP|PROBE_ONLY", "reasons": [], "size_cap_pct": 100},
  "timing": {"status": "OK|WAIT|PULLBACK_WAIT", "reasons": []},
  "affordability": {
    "status": "OK|QTY_ZERO|PRICE_TOO_HIGH",
    "budget_krw": 0,
    "price_krw": 0,
    "shortfall_krw": 0
  },
  "final_action": "HARD_BLOCK|WATCH|PROBE_READY|BUY_READY|PULLBACK_WAIT|AVOID|SIZE_CAP|WAIT",
  "final_size_pct": 0,
  "final_reason": ""
}
```

### QA

| 검증 | 기준 |
|---|---|
| schema normalize | 누락 필드는 default로 복구 |
| action enum | 허용 값 외 action은 `WATCH` |
| duplicate ticker | 같은 market/ticker는 record 1개 |
| known_at | decision_time 이후 feature 사용 금지 |

### Rollback

```text
신규 schema는 selection_meta 내부 보조 필드로만 저장한다.
기존 watchlist/trade_ready는 유지한다.
문제 발생 시 candidate_actions를 무시하고 기존 경로로 복귀한다.
```

> **[검토 — D1]**
>
> **CandidateRecord.features와 PostOpenFeatureSnapshot의 관계가 미정의다.**
>
> `CandidateRecord.features = {}`로 비어 있는데, D3의 `PostOpenFeatureSnapshot`이 별도 schema다. feature를 record에 inline할지, snapshot 파일을 참조할지 결정해야 한다. inline이면 `CandidateRecord.features`에 snapshot 필드가 복사되고, 참조면 `CandidateRecord.feature_snapshot_ref`가 필요하다. 결정하지 않으면 D3 구현 시 어느 쪽으로 만들지 모호해진다.
>
> **bot/candidate_policy.py는 이미 존재한다.** 수정 파일 목록에 포함 확인. 단 현재 `normalize_selection_result()` 내부가 `candidate_actions` 필드를 다루는지 먼저 확인 후 확장 범위를 정해야 한다.

---

## D2. Unified Candidate Pool

### 왜 하는가

기존 코멘트:

- 장전 후보 59개가 있었다는 숫자는 보이지만 실제 Claude prompt에 어떤 후보가 들어갔는지 추적이 어렵다.
- SOFT pin은 로드되지만 return에서 조용히 빠질 수 있었다.
- 여러 rescreen 경로가 서로 다른 builder를 쓰면 같은 티커가 경로마다 포함/제외될 수 있다.
- full_pool과 prompt_pool은 다른 개념인데 기존 코드에서는 섞여 있다.

### 목표

장전 후보, 기본 후보, 장중 후보, 보유/재진입 후보를 full_pool에 유지하고, Claude에는 prompt_score로 정리된 prompt_pool만 전달한다.

### 신규/수정 파일

| 파일 | 작업 |
|---|---|
| `runtime/candidate_pool_runtime.py` | pool 생성/병합/ranking |
| `trading_bot.py` | 후보 생성 경로에서 pool builder 호출 |
| `minority_report/analysts.py` | prompt line에 source_tags/prompt_score 포함 |
| `preopen/storage.py` | actual_selected/trade_ready/order 추적 |
| `tests/test_candidate_pool_runtime.py` | merge/ranking/cap 테스트 |

### Pool 계층

```text
full_pool:
  시스템이 계속 감시하는 전체 후보

prompt_pool:
  Claude에게 보낼 상위 후보

execution_pool:
  Claude action + system gate를 통과한 실행 후보
```

### source_tags

```text
base
preopen
preopen_hard_pin
preopen_soft_pin
opening_fresh
intraday_momentum
late_mover
held
reentry
pathb_waiting
```

### 병합 규칙

```text
key = market + normalized_ticker

이미 full_pool에 있으면:
  source_tags 병합
  source_ranks[source] 저장
  최신 price/turnover는 asof가 더 최신인 값 사용
  preopen_pin은 HARD > SOFT > NONE
  stale=false source 우선

새 후보면:
  CandidateRecord 생성
```

### prompt_score 1차 규칙

```text
source bonus:
  held +30
  preopen_hard_pin +25
  opening_fresh +22
  preopen +18
  intraday_momentum +18
  pathb_waiting +15
  base +8

feature bonus:
  controlled_strength +20
  early_strength +12
  pullback_setup +10
  high liquidity +8
  sector strength +5

risk penalty:
  overextended -15
  fade -20
  low liquidity -12
  poor data -20
  spread high -10
```

주의:

```text
prompt_score는 주문 판단이 아니다.
Claude에게 보낼 순서만 정한다.
```

### prompt_pool cap

권장 1차값:

```text
KR prompt_pool: 24~30개
US prompt_pool: 20~28개

held/reentry: 최대 5
preopen confirmed: 최대 10
opening/intraday: 최대 8
pathb_waiting: 최대 5
base: 최대 4
```

slot은 하드 quota가 아니다. 좋은 후보가 없는데 억지로 채우지 않는다.

### QA

| 검증 | 기준 |
|---|---|
| preopen 59개 | full_pool에 유지 |
| prompt cap | cap 초과 시 prompt_score 순서로 제외 |
| duplicate merge | 같은 ticker가 prompt에 2번 나오지 않음 |
| source trace | 후보 포함/제외 이유가 source_tags로 설명 가능 |

### Rollback

```text
ENABLE_UNIFIED_CANDIDATE_POOL=false
기존 candidates list를 select_tickers에 그대로 전달한다.
pool snapshot은 shadow log로 유지 가능하다.
```

> **[검토 — D2]**
>
> **intraday_momentum과 late_mover source 생성 코드가 없다.**
>
> `source_tags`에 `intraday_momentum`, `late_mover`가 포함되어 있으나, 코드베이스에 이 태그를 생성하는 로직이 존재하지 않는다. D2 구현 시 이 두 pool의 생성 로직을 새로 만들어야 한다는 점을 명시해야 한다. 없는 채로 pool에 포함하면 `opening_fresh`만으로 prompt_pool이 채워지게 된다.
>
> **prompt_score 합산 방식이 미명시다.**
>
> source bonus + feature bonus - risk penalty 방식은 설명됐으나, 합산 상한(cap)이 없으면 `held + preopen_hard_pin + controlled_strength`만으로 score=75가 되어 다른 source가 실질적으로 순위에 못 들 수 있다. 합산 상한 또는 정규화 방식을 정해야 한다.

---

## D3. Post-open Feature Snapshot

### 왜 하는가

기존 코멘트:

- 장전 후보는 정답 목록이 아니라 감시 universe다.
- 개장 후 상태 확인이 장전 rank보다 중요했다.
- 다만 5m/30m를 사후 필터로 쓰면 look-ahead bias가 생긴다.
- 특정 시간값보다 `decision_time에 실제로 알 수 있는 정보`가 중요하다.

### 목표

개장 후 후보별 현재 상태를 계산하고 `known_at`과 함께 저장한다. Claude와 gate는 이 snapshot만 보고 판단한다.

### 신규/수정 파일

| 파일 | 작업 |
|---|---|
| `runtime/post_open_features.py` | feature 계산 |
| `trading_bot.py` | `_intraday_high/_intraday_low/_or_high/_or_low`와 연결 |
| `minority_report/analysts.py` | prompt line에 feature 요약 |
| `tools/replay_future_blind_candidate_flow.py` | replay에서 동일 feature 사용 |
| `tests/test_post_open_features.py` | 시점별 feature 테스트 |

### snapshot 시점

```text
opening_snapshot:
  개장 후 3~6분

confirmation_snapshot:
  개장 후 15~30분

intraday_snapshot:
  장중 rescreen 또는 30~60분 단위

hold_snapshot:
  보유/매도 재판단 직전
```

### momentum_state 1차 규칙

```text
early_strength:
  초기 수익률 양수
  거래량 확인
  spread 과도하지 않음

controlled_strength:
  초기 강세 유지
  VWAP/OR 위
  과열 기준 미만

overextended:
  초기 급등
  VWAP 괴리 큼
  spread 확대 또는 추격 위험

fade:
  초기 상승 후 약화
  OR low/VWAP 이탈

pullback_setup:
  강한 종목이 VWAP/OR 근처로 정상 눌림

late_mover:
  장 초반 약했으나 장중 거래대금/수익률 급상승
```

### 시장별 분리

```text
KR:
  변동성이 높다.
  ret 기준은 US보다 넓게 둔다.
  가격제한폭/호가단위/고가 1주 문제를 반영한다.

US:
  gap/news/sector 영향이 크다.
  spread와 뉴스 리스크를 더 강하게 본다.
  ret threshold는 KR보다 낮게 둔다.
```

### prompt line 예시

```text
001440 source=preopen,opening_fresh liq=high
post_open: ret5=+2.8 ret10=+4.1 state=controlled_strength
or=above vwap=+0.7% vol_open=3.2x spread=0.12%
exec_hint=probe_ok risk=not_overextended
```

### QA

| 검증 | 기준 |
|---|---|
| known_at | 미래 feature 사용 금지 |
| missing data | null 허용, data_quality 하향 |
| KR/US 분리 | threshold config 분리 |
| replay/live 일관성 | 같은 시점이면 같은 snapshot |

### Rollback

```text
ENABLE_POST_OPEN_FEATURES=false
기존 candidate prompt 유지
feature는 로그만 남긴다.
```

> **[검토 — D3]**
>
> **VWAP 계산 로직이 현재 코드베이스에 없다.**
>
> `PostOpenFeatureSnapshot.vwap`, `vwap_distance_pct`를 계산하려면 분봉 체결 데이터 누적이 필요하다. `trading_bot.py`에 VWAP 계산 함수가 존재하지 않는다. `runtime/post_open_features.py`에서 VWAP을 직접 계산할지, 브로커 API에서 받을지, 별도 틱 데이터 누적 구조를 만들지 결정해야 한다. KR은 KIS 호가 API, US는 별도 data source가 필요하다.
>
> **momentum_state에 정량 기준이 없다.**
>
> `overextended`, `controlled_strength`, `fade` 등의 정의가 정성적 설명만 있다. 구현 시 개발자마다 다른 임계값을 쓸 수 있다. 최소한 아래 기준이 필요하다.
>
> ```text
> overextended: ret_5m > X% AND vwap_distance > Y%  (KR/US 분리)
> fade:         ret_5m > 0 AND ret_10m < ret_5m * 0.5
> controlled_strength: ret_5m > 0 AND volume_ratio_open > Z AND not overextended
> ```
>
> **opening_snapshot 3~6분 시점은 KR에서 노이즈가 크다.**
>
> KR 개장 직후(09:00~09:06)는 체결 집중 구간으로 가격 변동성이 매우 높다. 이 시점 snapshot을 즉시 진입 decision에 쓰면 노이즈가 크다. `opening_snapshot`은 집계 목적으로만 쓰고, 실제 진입 decision에는 `confirmation_snapshot`(15~30분)부터 사용하는 정책을 명시해야 한다. 단, probe는 이 예외를 둘 수 있다.

---

## D4. Claude Candidate Actions Schema

### 왜 하는가

기존 코멘트:

- `watchlist/trade_ready`만으로는 "작게 먼저", "정상 매수", "눌림 대기", "회피"를 표현할 수 없다.
- Claude에게 미래를 맞히라고 하면 안 된다. 현재 상태를 분류하게 해야 한다.
- PathB 품질은 selection prompt의 `price_targets` 품질에 직접 의존한다.

### 목표

기존 `watchlist/trade_ready` 호환성을 유지하면서 `candidate_actions`를 추가한다.

### 수정 파일

| 파일 | 작업 |
|---|---|
| `minority_report/analysts.py` | selection prompt v4, parser 확장 |
| `bot/candidate_policy.py` | normalize_selection_result 확장 |
| `trading_bot.py` | `_apply_selection_meta()`, `_normalize_selection_meta_runtime()` 확장 |
| `runtime/pathb_runtime.py` | `PULLBACK_WAIT` price plan 등록 |
| `tests/test_candidate_actions_schema.py` | parse/fallback 테스트 |

### prompt schema

```json
{
  "watchlist": ["001440", "018880"],
  "trade_ready": ["001440"],
  "candidate_actions": {
    "001440": {
      "action": "PROBE_READY",
      "confidence": 0.68,
      "entry_style": "early_probe",
      "size_intent": "probe",
      "path": "PlanA",
      "why_now": "controlled opening strength",
      "invalidation_condition": "loses opening range low",
      "add_condition": "holds VWAP and volume remains strong",
      "avoid_condition": "5m spike fades below anchor"
    },
    "018880": {
      "action": "PULLBACK_WAIT",
      "confidence": 0.62,
      "entry_style": "pullback",
      "size_intent": "small",
      "path": "PathB",
      "why_now": "strong but extended",
      "invalidation_condition": "breaks VWAP with weak volume",
      "price_targets": {
        "reference_price": 0,
        "buy_zone_low": 0,
        "buy_zone_high": 0,
        "sell_target": 0,
        "stop_loss": 0,
        "invalid_if": "opening strength fully fades"
      }
    }
  },
  "reasons": {},
  "veto": {}
}
```

### 호환 규칙

```text
candidate_actions가 있으면:
  trade_ready는 legacy 호환 필드로 유지
  PROBE_READY/BUY_READY는 runtime trade_ready 후보로 반영 가능
  PULLBACK_WAIT는 trade_ready에 없어도 PathB 등록 가능

candidate_actions가 없으면:
  기존 trade_ready 기반으로 BUY_READY에 준하는 legacy action 생성

JSON parse 실패:
  기존 partial recovery 사용
  candidate_actions는 빈 dict
  trade_ready/watchlist fallback 유지
```

### validation

```text
허용 action 외 값 -> WATCH
confidence 누락 -> 0.5
PULLBACK_WAIT인데 price_targets 누락 -> PathB 등록 금지, WATCH로 강등 또는 reason 기록
PROBE_READY가 watchlist에 없음 -> watchlist에 추가
```

### QA

| 검증 | 기준 |
|---|---|
| legacy 응답 | 기존 trade_ready만 있어도 동작 |
| 신규 응답 | candidate_actions 저장 |
| PULLBACK_WAIT | PathB로 전달 |
| malformed JSON | 기존 fallback으로 안전 복구 |

### Rollback

```text
ENABLE_CANDIDATE_ACTIONS=false
candidate_actions 무시
기존 watchlist/trade_ready만 사용
```

> **[검토 — D4]**
>
> **PULLBACK_WAIT + price_targets 누락 시 처리 경로를 구체화해야 한다.**
>
> validation 규칙에 "PULLBACK_WAIT인데 price_targets 누락 → PathB 등록 금지, WATCH로 강등 또는 reason 기록"라고 나와 있다. `WATCH`로 강등하면 당일 좋은 종목을 완전히 날리는 것이고, reason만 기록하면 실행이 없다. 운영상 WATCH 강등 쪽이 맞지만, 이 경우 `watchlist`에는 남기고 `execution_pool`에서만 제외한다는 규칙을 명시해야 한다.
>
> **레거시 응답에서 PROBE_READY fallback이 없다.**
>
> `candidate_actions 없을 때 trade_ready 기반 BUY_READY 생성`이라고 명시됐는데, PROBE_READY fallback이 없다. 레거시 응답에서는 모든 trade_ready가 BUY_READY로 올라간다. 이것이 의도된 설계라면 명시하고, 아니라면 fallback 규칙에 `confidence_fallback = BUY_READY if strong else PROBE_READY` 를 추가해야 한다.

---

## D5. Gate Evaluation Matrix

### 왜 하는가

기존 코멘트:

- 조건은 많지만 진짜 문제는 조건 수가 아니라 모든 결과가 `skip/block`으로 뭉치는 것이다.
- 레이어 1에서 막히면 레이어 2~7이 실제로 통과했을지 알 수 없다.
- 오늘처럼 `judgment_not_executable`만 800건 이상 찍히면 근본 원인 진단이 장 끝나고서야 가능하다.

### 목표

각 후보의 gate 상태를 계층별로 기록하고, 최종 action을 명확히 계산한다.

### 수정 파일

| 파일 | 작업 |
|---|---|
| `runtime/gate_evaluation.py` | gate result builder |
| `trading_bot.py` | run_cycle entry gate마다 평가값 축적 |
| `runtime/pathb_runtime.py` | PathB gate도 같은 schema 사용 |
| `logs/funnel` | gate snapshot 저장 |
| `tests/test_gate_evaluation.py` | hard/soft/timing/affordability 테스트 |

### gate 계층

```text
hard_safety:
  MARKET_CLOSED
  BROKER_UNTRUSTED
  daily halt
  critical ORDER_UNKNOWN

soft_safety:
  judgment_unconfirmed
  stop_cluster_first_cooldown
  ATR high
  partial_data
  VIX/risk_off

timing:
  OR not formed
  momentum_wait
  no_signal
  overextended

selection:
  WATCH
  PROBE_READY
  BUY_READY
  PULLBACK_WAIT
  AVOID

affordability:
  qty_zero
  price_too_high
  cash_shortfall
  min_order_conflict
```

### final action mapping

| 조건 | final_action |
|---|---|
| hard_safety BLOCK | `HARD_BLOCK` |
| selection AVOID | `AVOID` |
| selection WATCH | `WATCH` |
| timing PULLBACK_WAIT | `PULLBACK_WAIT` |
| soft_safety PROBE_ONLY | `PROBE_READY` with cap |
| soft_safety SIZE_CAP | original action with cap |
| affordability QTY_ZERO | `SIZE_CAP` 또는 `WATCH`, 사유 기록 |
| all OK + PROBE_READY | `PROBE_READY` |
| all OK + BUY_READY | `BUY_READY` |

### 로그 예시

```json
{
  "event": "gate_evaluation",
  "market": "KR",
  "ticker": "001440",
  "selection_action": "PROBE_READY",
  "hard_safety": "OK",
  "soft_safety": "SIZE_CAP",
  "soft_reasons": ["unconfirmed_cap_70"],
  "timing": "OK",
  "affordability": "OK",
  "final_action": "PROBE_READY",
  "final_size_pct": 35,
  "final_reason": "probe allowed with unconfirmed cap"
}
```

### QA

| 검증 | 기준 |
|---|---|
| judgment_not_executable | 단일 skip 대신 layer별 상태 기록 |
| hard block | 주문 함수까지 도달하지 않음 |
| soft cap | 수량이 줄고 주문 가능 |
| PULLBACK_WAIT | 직접 주문 없이 PathB 위임 |

### Rollback

```text
ENABLE_GATE_MATRIX=false
기존 skip logging 유지
shadow log는 계속 남길 수 있다.
```

> **[검토 — D5]**
>
> **gate 계층에 실제 코드에 있는 조건이 3개 빠졌다.**
>
> 현재 `trading_bot.py`에서 진입을 막는 조건 중 아래 3개가 D5의 gate 계층 목록에 없다.
>
> ```text
> entry_blackout:       timing gate에 추가 필요
>                       (코드: trading_bot.py:3001 _in_entry_blackout)
>
> same_day_reentry:     timing gate에 추가 필요
>                       (코드: trading_bot.py:3167 _v2_same_day_reentry_decision)
>
> STOP_CLUSTER_MARKET_BLOCK: hard_safety로 올려야 함
>                       (코드: trading_bot.py:2969, 당일 신규 진입 전체 차단 정책)
> ```
>
> 특히 `STOP_CLUSTER_MARKET_BLOCK`은 "클러스터 손절 2회 = 당일 신규 진입 차단"이므로 soft_safety가 아니라 hard_safety다. soft_safety에 두면 SIZE_CAP으로 우회될 수 있다.
>
> **STOP_CLUSTER_DISASTER_BLOCK도 hard_safety 목록에 명시해야 한다.**
>
> 현재 hard_safety에 `daily halt`는 있는데 `STOP_CLUSTER_DISASTER_BLOCK`이 없다. 코드 기준으로는 이것도 hard block이다.

---

## D6. PlanA/PathB Action Routing

### 왜 하는가

기존 코멘트:

- PlanA든 PlanB든 공통으로 order reject/permanent 분류와 budget guard가 필요하다.
- 같은 종목이 PlanA와 PathB 양쪽에 걸릴 수 있으므로 shared position gate가 필요하다.
- `PULLBACK_WAIT`는 직접 매수가 아니라 PathB 위임이어야 한다.

### 목표

Claude action을 PlanA와 PathB 실행 경로로 명확히 라우팅하고, 중복 진입을 막는다.

### 수정 파일

| 파일 | 작업 |
|---|---|
| `trading_bot.py` | action intent 기반 PlanA 진입 |
| `runtime/pathb_runtime.py` | PULLBACK_WAIT 등록 |
| `risk_manager.py` | position/pyramid 제한 확인 |
| `runtime/v2_lifecycle_runtime.py` | decision_id와 action 연결 |
| `tests/test_plan_action_routing.py` | routing/중복 방지 테스트 |

### routing table

| action | PlanA | PathB | 주문 |
|---|---|---|---|
| `WATCH` | no | no | 없음 |
| `AVOID` | no | no | 없음 |
| `PROBE_READY` | yes | no | probe buy 가능 |
| `BUY_READY` | yes | optional no | normal buy 가능 |
| `PULLBACK_WAIT` | no | yes | 조건부 대기 |
| `ADD_READY` | shadow in 1차 | no | 1차 구현에서는 주문 보류 |

### 중복 방지 규칙

```text
if live_position exists:
  PROBE_READY/BUY_READY 신규 주문 금지
  ADD_READY만 검토

if pathb_waiting exists:
  PlanA BUY_READY가 나오면 PathB plan cancel 또는 suspend

if PlanA probe filled:
  PathB same ticker plan cancel

if PathB filled:
  PlanA same ticker 신규 진입 금지
```

### QA

| 검증 | 기준 |
|---|---|
| PROBE_READY | PlanA probe 후보 생성 |
| PULLBACK_WAIT | PlanA 주문 없음, PathB plan 생성 |
| same ticker conflict | 중복 주문 없음 |
| ADD_READY | 1차에서는 로그만 남고 주문 없음 |

### Rollback

```text
ENABLE_ACTION_ROUTING=false
기존 trade_ready 기반 경로 사용
```

> **[검토 — D6]**
>
> **PathB plan cancel vs suspend가 미결정이다.**
>
> 중복 방지 규칙에 "PlanA BUY_READY → PathB plan cancel 또는 suspend"로 나와 있다. cancel과 suspend는 동작이 다르다.
>
> ```text
> cancel: PathB plan 삭제. PlanA 진입 실패 시 PathB 재등록 불가.
> suspend: PathB plan 일시 정지. PlanA 진입 실패 시 자동 재활성화 가능.
> ```
>
> PlanA probe가 손절로 끝났을 때 PathB pullback plan이 자동 재활성화되어야 하는지 정책이 없다. 이 경우 운영자가 수동으로 재등록해야 하는지도 명시 필요하다.
>
> **같은 종목이 PlanA probe + PathB 대기 상태일 때 포지션 합산 한도가 없다.**
>
> `if PlanA probe filled → PathB same ticker plan cancel`은 명시됐다. 그러나 probe 진입 전 PathB도 동시에 조건을 충족하면 중복 진입이 발생할 수 있다. 체결 순서에 따라 두 건이 동시에 주문될 가능성을 막는 atomic check가 필요하다.

---

## D7. Sizing / Budget Contract

### 왜 하는가

기존 코멘트:

- PathB `_pathb_qty()`의 min_order override가 fixed order cap을 우회할 수 있었다.
- "주문가능금액 초과", "매수가능금액 부족", "증거금 부족"은 PlanA/PathB 공통 permanent/reject 분류가 필요하다.
- 고가 1주를 허용할지 여부는 config로 분리해야 한다.
- probe 손절을 full position 손절과 동일하게 stop cluster에 반영하면 시스템이 다시 과보수화된다.

### 목표

PlanA, PathB, probe, add, min_order, fixed_order_cap, 고가 1주 케이스의 우선순위를 고정한다.

### 수정 파일

| 파일 | 작업 |
|---|---|
| `trading_bot.py` | PlanA qty/size 계산부 |
| `runtime/pathb_runtime.py` | `_pathb_qty()` cap 재검증 |
| `risk_manager.py` | probe/add sizing helper |
| `config/v2.py` 또는 env 문서 | 신규 config |
| `tests/test_sizing_budget_contract.py` | qty/cap 테스트 |

### sizing 우선순위

```text
1. broker cash / buying power
2. hard max order cap
3. market condition size cap
4. Claude max_order_cap_pct 또는 size_intent
5. action type cap
6. min_order
7. integer share quantity
```

중요:

```text
min_order가 fixed_order_cap을 뚫으면 안 된다.
단, 사용자가 고가 1주 허용 정책을 켠 경우만 예외 가능하다.
```

### 권장 config

```text
PROBE_SIZE_RATIO_KR=0.30
PROBE_SIZE_RATIO_US=0.25
PROBE_MAX_ORDER_KRW=150000
PROBE_STOP_WEIGHT=0.35

BUY_READY_SIZE_RATIO=1.00
ADD_READY_ENABLED=false
ADD_READY_SHADOW_ONLY=true

ALLOW_ONE_SHARE_OVER_CAP_KR=false
ALLOW_ONE_SHARE_OVER_CAP_US=false
ONE_SHARE_OVER_CAP_MAX_MULT=1.2

PATHB_RESPECT_FIXED_ORDER_CAP=true
PATHB_MIN_ORDER_OVERRIDE_RESPECT_CAP=true
```

### qty_zero 처리

```text
price > available_budget:
  if allow_one_share_over_cap and cash >= price and price <= cap * max_mult:
    qty=1
    reason=one_share_over_cap_allowed
  else:
    qty=0
    final_action=WATCH or SIZE_CAP
    reason=price_too_high
```

### probe 손절 카운터

```text
full buy stop:
  stop_cluster_weight = 1.0

probe stop:
  stop_cluster_weight = PROBE_STOP_WEIGHT

recovery_micro stop:
  별도 weight 또는 market block 제외
```

### QA

| 검증 | 기준 |
|---|---|
| PathB min_order | cap 초과 주문 없음 |
| 고가주 | 정책 off면 qty_zero |
| probe | full budget보다 작은 주문 |
| stop cluster | probe 손절이 full 1건으로 집계되지 않음 |

### Rollback

```text
PROBE_READY를 WATCH로 강등 가능
PathB cap guard는 rollback하지 않는 것을 권장한다.
```

> **[검토 — D7]**
>
> **sizing priority와 D5 soft_safety의 market_condition_size_cap이 이중 적용될 수 있다.**
>
> D5에서 soft_safety가 `VIX/risk_off → SIZE_CAP`을 출력하고, D7 sizing priority 3번도 `market condition size cap`을 적용한다. 두 계층에서 독립적으로 cap을 계산하면 같은 종목에 두 번 cap이 걸릴 수 있다. GateEvaluation의 `soft_safety.size_cap_pct`를 D7 sizing의 3번 입력으로 재사용하는 방식으로 단일화해야 한다.
>
> **PROBE_STOP_WEIGHT=0.35의 근거가 없다.**
>
> probe는 full buy의 30% 수준이므로 stop_weight도 0.3 정도가 직관적이다. 0.35로 정한 근거가 없으면 나중에 임의로 바뀔 수 있다. 근거(probe_size_ratio * 1.1 안전 마진, 또는 실측 데이터 등)를 명시해야 한다. 현재는 probe가 실제 운영 전이므로 0.30~0.35 범위에서 보수적으로 시작하고 조정하는 것을 권장.

---

## D8. Exit Lifecycle / Hold Advisor

### 왜 하는가

기존 코멘트:

- 매도 금액을 너무 높게 잡으면 수익 보호가 늦어진다.
- 작은 수익을 먼저 지켜도 Claude가 재판단하므로 강한 종목은 더 가져갈 수 있다.
- Claude HOLD가 hard stop, profit floor, trailing을 무효화하면 안 된다.
- 보유 판단 prompt는 목표가보다 MFE/giveback/floor/thesis_status 중심이어야 한다.

### 목표

수익 보호는 system이 먼저 수행하고, Claude는 HOLD 예외 또는 thesis 무효를 검증한다.

### 수정 파일

| 파일 | 작업 |
|---|---|
| `risk_manager.py` | exit candidate reason priority 명시 |
| `trading_bot.py` | `_process_exit_candidates()`, `_try_soft_exit_arbitration()` 개선 |
| `minority_report/hold_advisor.py` | prompt v4, output 확장 |
| `minority_report/quick_exit_check.py` | giveback/floor 정보 추가 |
| `runtime/pathb_runtime.py` | PathB pre-close/auto sell review 동일 원칙 |
| `tests/test_exit_lifecycle.py` | priority/Claude override 테스트 |

### exit priority

```text
1. broker/position integrity
2. hard loss cap
3. strategy invalidation stop
4. recovery_micro forced exit
5. profit_floor
6. giveback_from_mfe
7. trailing stop
8. quick_exit_check
9. hold_advisor
10. session close / carry review
```

### Claude override 금지

```text
Claude HOLD cannot override:
  hard loss cap
  broker integrity issue
  daily halt
  position mismatch

Claude HOLD can defer:
  soft profit_floor
  soft trailing exit
  TP immediate sell

단, defer 시 protective_stop/profit_floor를 반드시 올린다.
```

### hold_advisor v4 input

```json
{
  "decision_stage": "TP_REVIEW|AUTO_SELL_REVIEW|PRE_CLOSE_CARRY|INTRADAY_REVIEW",
  "ticker": "001440",
  "market": "KR",
  "entry_price": 0,
  "current_price": 0,
  "pnl_pct": 0.0,
  "mfe_pct": 0.0,
  "mae_pct": 0.0,
  "giveback_from_mfe_pct": 0.0,
  "profit_floor_price": 0,
  "profit_floor_triggered": false,
  "trail_price": 0,
  "thesis_target": 0,
  "intraday_target": 0,
  "thesis_status": "intact|weakening|invalid|unknown",
  "last_3m_return_pct": null,
  "last_5m_return_pct": null,
  "last_10m_return_pct": null,
  "volume_since_entry": null,
  "vwap_distance_pct": null,
  "market_condition": "",
  "sector_condition": "",
  "alternative_opportunity_pressure": "low|medium|high|unknown",
  "minutes_to_close": null
}
```

### hold_advisor v4 output

```json
{
  "action": "HOLD|SELL|HOLD_WITH_TIGHT_TRAIL|RAISE_PROFIT_FLOOR",
  "confidence": 0.0,
  "sell_urgency": "now|next_open|wait",
  "trail_pct": 0.03,
  "protective_stop": 0.0,
  "profit_floor_raise_to": 0.0,
  "next_review_min": 15,
  "thesis_status": "intact|weakening|invalid|unknown",
  "invalid_if": "",
  "reason": ""
}
```

1차 구현에서는 `HOLD_WITH_TIGHT_TRAIL`과 `RAISE_PROFIT_FLOOR`를 내부적으로 `HOLD`로 호환 매핑하되, floor/trail 값만 반영한다.

### QA

| 검증 | 기준 |
|---|---|
| hard stop | Claude 호출 없이 매도 |
| profit_floor soft review | quick_exit_check 후 HOLD 가능 |
| HOLD defer | protective floor 설정 |
| target 과신 방지 | high target이어도 giveback 크면 SELL 가능 |

### Rollback

```text
HOLD_ADVISOR_V4=false
기존 hold_advisor_v3 prompt 사용
exit priority hard rule은 유지 권장
```

> **[검토 — D8]**
>
> **exit priority 4번과 5번 순서를 재검토해야 한다.**
>
> 현재:
> ```text
> 4. recovery_micro forced exit
> 5. profit_floor
> ```
>
> 문제: `recovery_micro`는 손실 제한 목적 강제 청산이다. 수익 구간에 있는 포지션이 4번에서 먼저 청산되면 5번 `profit_floor`가 의미 없어진다. 아래 순서가 더 맞다.
>
> ```text
> 4. profit_floor              (수익이 있으면 먼저 보호)
> 5. recovery_micro forced exit (손실 제한은 수익 보호 다음)
> ```
>
> 단, `recovery_micro`가 활성화된 포지션에서 `profit_floor` 기준을 올려두면 두 조건 충돌 없이 동작할 수 있다. 이 경우 `recovery_micro` 포지션에 대해서는 `profit_floor_raise_to` 값을 높게 설정하는 규칙이 필요하다.
>
> **hold_advisor v4 output의 trail_pct=0.03이 KR/US 구분 없이 예시됐다.**
>
> 이 값이 trailing 엔진에 어떻게 전달되는지 연결 경로가 없다. `trail_pct`가 현재 trailing stop 로직의 어느 config를 덮어쓰는지 명시해야 한다.

---

## D9. Observability

### 왜 하는가

기존 코멘트:

- 오늘 분석이 복잡했던 이유는 장중에 "왜 주문이 없었는가"를 볼 수 없었기 때문이다.
- funnel이 실시간으로 보이면 운영자가 장중에 문제를 인지하고 수동 개입할 수 있다.

### 목표

장중에 후보 수, prompt 수, Claude action, gate 상태, PlanA/PathB 결과, exit 판단을 한눈에 볼 수 있게 한다.

### 수정 파일

| 파일 | 작업 |
|---|---|
| `trading_bot.py` | funnel/gate/action 로그 emit |
| `runtime/candidate_pool_runtime.py` | pool snapshot 저장 |
| `runtime/pathb_runtime.py` | PathB decision trace |
| `dashboard/dashboard_server.py` | 요약 표시 후속 |
| `tools/*` | 로그 요약 CLI 후속 |

### 로그 이벤트

```text
candidate_pool_snapshot
candidate_action_decision
gate_evaluation
planA_entry_decision
pathB_plan_decision
exit_decision_trace
claude_call_budget
future_blind_replay_result
```

### 장중 요약 예시

```text
[candidate funnel KR]
full=59 prompt=24 actions probe=3 buy=1 pullback=5 avoid=4
gate hard_block=0 soft_cap=2 wait=8 qty_zero=1
orders planA_probe=1 planA_buy=0 pathB_wait=4 filled=1
```

### QA

| 검증 | 기준 |
|---|---|
| 0거래일 | 이유가 funnel summary로 설명됨 |
| prompt cap | 어떤 후보가 잘렸는지 확인 가능 |
| PathB wait | 대기/취소/체결 trace 확인 가능 |
| exit | Claude HOLD/SELL과 system guard 차이 확인 가능 |

### Rollback

```text
로그 기능은 rollback 부담이 낮다.
성능 문제가 있으면 snapshot 주기를 늘린다.
```

> **[검토 — D9]**
>
> **logs/funnel/ 경로가 현재 존재하지 않는다.**
>
> 로그 이벤트 저장 경로로 `logs/funnel`을 사용하는데, 현재 코드베이스에 이 디렉토리가 없다. D9 구현 시 경로 생성과 함께 기존 로그 경로(`logs/flow/`, `logs/daily_judgment/` 등)와의 관계를 명시해야 한다. `logs/flow/`가 gate/funnel 로그를 이미 일부 담당하는지 확인 필요.
>
> **dashboard 업데이트는 후속으로만 명시했는데 순서가 불명확하다.**
>
> `dashboard/dashboard_server.py`가 수정 대상에 있으나 "요약 표시 후속"으로만 명시됐다. D9의 목표(장중 funnel 상태 한눈에 보기)를 달성하려면 dashboard 업데이트가 어느 단계(R0~R6)에서 진행되는지 D12와 연결해야 한다.

---

## D10. Future-Blind Replay

### 왜 하는가

기존 코멘트:

- 5m/30m 필터 성과는 이미 저장된 결과를 보고 뒤에서 고른 것이므로 이론 상한에 가깝다.
- P1이 완성되어도 사후 백테스트 숫자가 그대로 나오지는 않는다.
- 개선 성과는 그 시점에 알 수 있던 정보만으로 검증해야 한다.

### 목표

사후 결과를 보고 만든 착시 필터를 제거하고, 당시 알 수 있던 정보만으로 개선 정책을 검증한다.

### 신규 파일

| 파일 | 작업 |
|---|---|
| `tools/replay_future_blind_candidate_flow.py` | replay CLI |
| `tests/test_future_blind_replay.py` | known_at 차단 테스트 |
| `docs/reports/*replay*.md` | replay 결과 리포트 |

### replay 입력

```text
state/preopen_KR_YYYYMMDD.json
state/preopen_US_YYYYMMDD.json
state/candidate_health_KR_YYYYMMDD.json
state/candidate_health_US_YYYYMMDD.json
state/live_decisions.jsonl
logs/analysis/live_analysis_YYYYMMDD.jsonl
logs/funnel/*.json
```

### replay 시나리오

```text
baseline_actual:
  실제 로그 기준 진입/청산

baseline_preopen_all:
  장전 후보 전체 동일금액 진입

policy_probe_v1:
  known_at 기준 PROBE_READY만 probe 진입

policy_pullback_v1:
  overextended는 PathB wait

policy_exit_v1:
  profit_floor/giveback 보호 적용
```

### no-lookahead assertion

```text
for each decision:
  assert feature.known_at <= decision_time
  assert outcome_30m is unavailable before anchor+30m
  assert close/360m return is never used for entry decision
```

### 결과 지표

```text
entries
win_rate
avg_pnl_pct
total_pnl_krw
max_drawdown_pct
MFE_avg
MAE_avg
giveback_avg
missed_runup
false_probe
false_avoid
```

### QA

| 검증 | 기준 |
|---|---|
| future leak | known_at 위반 시 실패 |
| actual baseline | 실제 decisions와 진입 수/손익 대조 |
| KR/US 분리 | 시장별 threshold로 replay |
| policy 비교 | baseline 대비 개선/악화 출력 |

### Rollback

```text
운영 코드와 분리된 tools이므로 rollback 부담 낮음.
replay 결과는 자동 brain 반영 금지.
```

> **[검토 — D10]**
>
> **replay 입력 파일 경로 확인 결과.**
>
> ```text
> state/candidate_health_KR_YYYYMMDD.json  → 존재함 ✓
> state/live_decisions.jsonl               → 존재함 ✓
> logs/funnel/*.json                       → 존재하지 않음 ✗ (D9 구현 선행 필요)
> state/preopen_KR/US_YYYYMMDD.json        → 존재함 ✓
> logs/analysis/live_analysis_YYYYMMDD.jsonl → 경로 확인 필요
> ```
>
> replay는 D9(Observability) 구현 이후에야 완전한 입력을 받을 수 있다. D9 전에는 `logs/funnel` 없이 동작하는 baseline replay만 가능하다는 점을 명시해야 한다.
>
> **false_probe와 false_avoid의 정의가 없다.**
>
> 결과 지표에 `false_probe`, `false_avoid`가 있는데 정의가 없다. 제안:
> ```text
> false_probe:  PROBE_READY로 진입했으나 30m 수익률 < -1% (손절 또는 악화)
> false_avoid:  AVOID로 회피했으나 30m 수익률 > +2% (놓친 기회)
> ```
> 이 정의 없이 지표를 구현하면 사람마다 다르게 계산된다.

---

## D11. QA Checklist

### 단위 테스트

| 테스트 | 기준 |
|---|---|
| candidate merge | source_tags 병합 |
| prompt ranking | cap과 score 순서 일관 |
| candidate_actions parse | 신규/기존 schema 모두 동작 |
| gate evaluation | hard/soft/timing/affordability 분리 |
| sizing | probe/buy/pathb cap 보존 |
| exit priority | hard stop이 Claude보다 우선 |
| known_at replay | 미래 feature 차단 |

### 통합 테스트

```text
1. preopen 후보 60개 로드
2. full_pool 유지 확인
3. prompt_pool cap 적용 확인
4. Claude candidate_actions mock 응답 주입
5. PROBE_READY -> PlanA probe 후보 생성
6. PULLBACK_WAIT -> PathB plan 생성
7. BUY_READY 고가주 qty_zero 사유 확인
8. profit_floor exit에서 quick_exit_check HOLD 시 floor 상승 확인
9. hard stop은 Claude 없이 청산 확인
10. funnel summary가 모든 단계 수를 표시하는지 확인
```

### 성공 기준

```text
judgment_not_executable 단일 사유 반복 감소
WATCH missed_runup 감소
PROBE_READY -> filled 전환율 상승
filled -> positive MFE 비율 상승
profit_floor 후 giveback 감소
PathB cap 초과 주문 0건
PlanA/PathB 중복 진입 0건
```

---

## D12. Rollout / Rollback

### 왜 하는가

기존 코멘트:

- 한 번에 pool, feature, prompt, probe, PathB, 매도까지 live로 켜면 디버깅이 불가능하다.
- 설계를 오늘 다 작성하더라도 live 적용은 단계별 flag가 필요하다.

### rollout 단계

```text
R0. schema/log shadow
  candidate_pool, candidate_actions, gate_evaluation을 저장만 함
  실제 주문 경로는 기존 유지

R1. Claude candidate_actions shadow
  Claude에게 신규 schema 요청
  결과는 저장만 하고 trade_ready 실행은 기존 유지

R2. PROBE_READY dry-run
  실제 주문 없이 PlanA probe intent 생성
  sizing/gate/affordability 검증

R3. PULLBACK_WAIT PathB dry-run
  PathB plan 등록만 하고 live order 비활성

R4. small live
  PROBE_READY만 소액 live 허용
  BUY_READY는 기존 경로 유지
  ADD_READY 비활성

R5. PathB live
  PULLBACK_WAIT 조건부 진입 허용
  cap guard 필수

R6. exit prompt v4 live
  quick_exit/hold_advisor v4 적용
  hard exit override 금지 확인
```

### feature flags

```text
ENABLE_UNIFIED_CANDIDATE_POOL=false
ENABLE_POST_OPEN_FEATURES=false
ENABLE_CANDIDATE_ACTIONS=false
ENABLE_GATE_MATRIX=false
ENABLE_ACTION_ROUTING=false
ENABLE_PROBE_ENTRY=false
ENABLE_PULLBACK_WAIT_PATHB=false
ENABLE_HOLD_ADVISOR_V4=false
ENABLE_FUTURE_BLIND_REPLAY=false
ADD_READY_ENABLED=false
ADD_READY_SHADOW_ONLY=true
```

### 즉시 rollback 기준

```text
중복 주문 발생
PathB cap 초과 주문 발생
hard stop이 Claude HOLD로 막힘
candidate_actions parse 실패로 watchlist 전체 손실
funnel/gate 로그 폭증으로 cycle 지연
broker reject 반복
```

### 부분 rollback

```text
candidate_actions 품질 낮음:
  ENABLE_CANDIDATE_ACTIONS=false

probe 손실 과다:
  ENABLE_PROBE_ENTRY=false

PathB 대기/체결 품질 낮음:
  ENABLE_PULLBACK_WAIT_PATHB=false

hold advisor가 계속 HOLD 과다:
  ENABLE_HOLD_ADVISOR_V4=false
```

> **[검토 — D12]**
>
> **R4에서 BUY_READY 종목의 처리 정책이 불명확하다.**
>
> R4는 "PROBE_READY만 소액 live 허용, BUY_READY는 기존 경로 유지"라고 명시됐다. 그런데 "기존 경로 유지"가 구체적으로 무엇인지 불명확하다.
>
> ```text
> 옵션 A: BUY_READY는 candidate_actions 무시하고 legacy trade_ready 경로로 처리
> 옵션 B: BUY_READY는 PROBE_READY로 강등해서 probe 사이즈로 처리
> 옵션 C: BUY_READY는 R4에서 실행 보류, WATCH로 강등
> ```
>
> 옵션 A가 가장 현실적이지만, 이 경우 `PROBE_READY`와 `BUY_READY`가 다른 경로를 타므로 gate_evaluation 로그가 두 action을 섞어 기록하게 된다. R4 운영 중 혼동을 막으려면 옵션을 고정해야 한다.
>
> **즉시 rollback 기준에 "funnel 로그 폭증으로 cycle 지연"이 있는데 기준값이 없다.**
>
> "cycle 지연"의 기준이 없으면 rollback 판단이 주관적이 된다. 예: `cycle_time > 5s 연속 3회` 또는 `log_write_time > 1s` 같은 측정 가능한 기준을 추가해야 한다.

---

## 6. Claude 사용 방식 명세

### 왜 하는가

기존 코멘트:

- Claude를 더 많이 쓰는 것이 답이 아니라 더 좋은 입력과 더 좁은 역할로 쓰는 것이 답이다.
- 시장 regime과 중요한 보유/매도 판단에는 3인 구조가 가치 있다.
- 장 초반 후보 상태 분류는 속도가 중요하므로 단일 structured call이 맞다.

### 역할 분리

| 계층 | Claude 역할 | 방식 |
|---|---|---|
| 시장 regime | risk-on/off, 신규매수 허용도 | 3인 R1/R2 유지 |
| 후보 선정 | prompt_pool에서 action enum 분류 | 단일 Sonnet JSON |
| 진입 타이밍 | post-open feature 기반 상태 분류 | 단일 Sonnet JSON + system gate |
| 수량/주문 | 사용하지 않음 | risk engine / broker guard |
| PathB 계획 | buy zone, invalid_if | selection price_targets |
| soft exit | 자동 매도 신호 확인 | quick_exit 1건 |
| 중요 보유/매도 | TP, pre-close, 큰 수익 반납 | 3인 hold_advisor |
| 사후분석 | 원인 요약/교정 후보 | 장마감 1회 |

### 권장 호출 예산

시장 1개 기준:

```text
보통 날:
  약 11~14건

진입 2~3개 있는 날:
  약 15~26건

급변장:
  약 21~31건
```

KR+US 동시 운영:

```text
30~60건 수준을 현실적 상한으로 본다.
100건 초과일은 과호출 진단 대상으로 본다.
```

---

## 7. 후보군 구성과 매수/매도 동작 명세

### 후보군 구성

```text
1. Base Pool
2. Preopen Pool
3. Opening Confirmation Pool
4. Intraday Momentum / Late Mover Pool
5. Held / Reentry Pool
```

기존 코멘트:

- Base Pool은 fallback이지 메인 알파가 아니다.
- Preopen Pool은 정답 목록이 아니라 감시 universe다.
- Opening Confirmation Pool이 PlanA의 핵심 입력이다.
- Late mover는 preopen confirmation만으로 잡기 어렵기 때문에 별도 pool이 필요하다.
- Held/Reentry는 add/reentry 판단을 위해 별도 상태가 필요하다.

### 매수 동작

```text
PROBE_READY:
  작게 먼저 진입
  빠른 thesis check
  profit_floor 빠르게 활성화

BUY_READY:
  정상 진입
  고확신 + 실행 가능성 + 과열 아님 필요

PULLBACK_WAIT:
  지금 추격 금지
  PathB buy_zone 대기

ADD_READY:
  1차는 shadow only
```

기존 코멘트:

- "작게 먼저 들어가고, 맞으면 늘리고, 틀리면 작게 끝낸다"가 현재 시스템에 맞다.
- 장 초반 좋은 후보는 완전 확인 후에는 늦다.
- 그렇다고 무조건 선진입하면 fade 종목에 물린다.
- 따라서 초기 참여는 probe가 맞다.

### 매도 동작

```text
수익 보호는 system이 먼저 한다.
Claude는 HOLD 예외를 검증한다.
목표가보다 giveback과 thesis_status가 중요하다.
작은 수익을 먼저 지키고, 강한 종목만 다시 열어준다.
```

기존 코멘트:

- 매도 목표가가 높으면 수익 보호가 늦어진다.
- profit_floor/trailing을 먼저 세우고, 강하면 Claude 재판단으로 더 가져간다.
- Claude HOLD가 수익 보호를 막으면 안 된다.

---

## 8. 오늘 개발 적용 순서

오늘 전체를 작성하고 이후 구현한다면 순서는 다음으로 고정한다.

```text
1. D1 schema를 먼저 만든다.
2. D2 pool은 shadow로 붙인다.
3. D9 관측성 로그를 early로 넣는다.
4. D4 candidate_actions를 parser까지 구현하되 실행은 shadow.
5. D5 gate matrix를 붙여 skip 원인을 계층화한다.
6. D6 routing은 PROBE_READY/PULLBACK_WAIT만 먼저 live 후보로 연결한다.
7. D7 sizing guard를 먼저 통과시킨 후 live를 켠다.
8. D8 exit은 hard priority부터 고정하고 prompt v4를 붙인다.
9. D10 replay로 오늘 KR/어제 US를 known_at 기준 재검증한다.
10. D11 QA 통과 후 R4 small live까지 진행한다.
```

최종 의견:

```text
기능을 많이 넣는 것보다 계약을 먼저 고정하는 것이 중요하다.
이번 개발은 후보 연결 + 작은 진입 + 빠른 수익 보호 + 관측성 + future-blind replay를 하나의 흐름으로 닫는 작업이다.
```

> **[검토 — 섹션 8]**
>
> **각 단계의 완료 기준이 없다.**
>
> 10단계가 나열됐으나 각 단계를 완료했다고 판단하는 기준이 없다. 예:
>
> ```text
> 1. D1 완료 기준: test_candidate_schema.py 통과
> 3. D9 완료 기준: 0거래일 funnel summary에 차단 원인이 로그로 설명됨
> 7. D7 완료 기준: PathB cap 초과 주문 QA 0건, probe qty < full buy qty 확인
> 10. D11 완료 기준: QA checklist 7개 단위 + 10개 통합 테스트 전체 통과
> ```
>
> 완료 기준 없이 진행하면 단계 사이에서 "어디까지 됐는지"를 매번 다시 파악해야 한다.
>
> **D3(Post-open feature)이 개발 순서 2번(D2) 다음인데 순서에서 빠졌다.**
>
> 섹션 8 순서에 D3이 없다. D2(pool) → D3(feature) → D4(actions) 순서가 맞는데, D3을 건너뛰면 D4 prompt에 feature 정보 없이 구현된다. D9 로그를 early로 붙인 다음 D3 feature snapshot을 shadow로 붙이는 단계가 필요하다.
>
> **2번 단계(D2 shadow)와 VWAP 구현 선행 관계가 없다.**
>
> D3 구현에 VWAP이 필요한데, VWAP 계산 로직이 현재 없다. D3 앞에 VWAP data source 결정 및 구현을 별도 단계로 두거나, 1차 D3에서는 VWAP 없이 ret/volume/spread만으로 먼저 동작시키는 방향을 명시해야 한다.
