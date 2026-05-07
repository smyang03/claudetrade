# Trading System Candidate Execution Development Specification - 2026-05-06

대상: `claudetrade`의 장전 후보, 기본 후보, 장중 후보, Claude 판단, PlanA/PathB 진입, 매도/보유 재판단, replay QA, live 설정 운영 구조.

목적: 기존 분석 리포트를 개발 명세서로 재정리한다. 이 문서는 "무엇을 고칠지"보다 "왜 고치며, 어디에 연결하고, 무엇으로 검증할지"를 기준으로 작성한다.

---

## 2026-05-06 구현 상태

중요: 이 문서의 전체 방향은 유지한다. 이번 반영은 R0~R8의 핵심 코드 경로를 구현하되, 실전 운영 리스크 때문에 신규 행동은 feature flag로 단계적으로 켜는 구조다. 즉 "코드 경로는 연결 완료, 기본 운영값은 shadow/guarded"가 현재 상태다.

이번 패치에서 실제 반영된 범위:

| 구분 | 상태 | 반영 내용 | live 주문 영향 |
|---|---|---|---|
| D1 Data model / schema | 완료 | `CandidateAction`, `CandidateRecord`, post-open snapshot, routing, sizing, exit, replay 계약 모듈 추가 | 없음 |
| D2 Unified Candidate Pool | shadow 완료 | 후보 merge, source_tags 병합, prompt cap, exclusion/deferred reason 계산 | 없음 |
| D3 Post-open Feature Snapshot | shadow 입력 연결 완료 | `known_at` 기반 feature snapshot, look-ahead 차단, market별 momentum_state 추론, rescreen 후보 prompt hint 연결 | 없음 |
| D4 Claude Candidate Actions | 구현 완료 / live flag on | 신규 `candidate_actions` prompt block, 파싱, TTL cap, invalid action 강등, legacy fallback | 사용자 지시로 true |
| D5 Gate Evaluation Matrix | 부분 완료 | `intraday_live_unconfirmed` 70% cap 단일 적용, gate evaluation log 기반 추가 | 제한적 안전 보정 |
| D6 PlanA/PathB Routing | 구현 완료 / live flag on | `BUY_READY/PROBE_READY/PULLBACK_WAIT/ADD_READY`를 PlanA trade_ready, probe size cap, PathB wait 등록으로 변환. PathB active order/대기 plan 충돌 방어 포함 | 사용자 지시로 true |
| D7 Sizing / Budget Contract | 계약/검증 완료 | size_intent 비율, min_order cap, high-price one-share policy, probe stop weight 테스트 | 기존 주문 sizing 전체 교체 아님 |
| D8 Exit Lifecycle | 구현 완료 / 기본 shadow | 기존 hard stop/profit_floor/trail 우선순위를 유지하고 `exit_lifecycle_decision` event 연결. live flag on 시 system guard 우선 적용 | 제한적 보조 |
| D9 Observability | 구현 완료 | funnel jsonl, trace id, config snapshot, action routing shadow/live route, candidate quality report, cycle latency event/alert hook | 없음 |
| D10 Future-Blind Replay | CLI 1차 완료 | known_at 필터, baseline_actual helper, preopen replay CLI, replay contract 테스트 | 없음 |
| D11 QA | 완료 | 신규 테스트 추가 및 전체 unittest 통과 기준 수립 | 없음 |
| D12 Rollout / Rollback | 구현 완료 | 신규 flag는 config에서 명시 제어, shadow flag 기본 on. `candidate_actions`/`action_routing`은 사용자 지시로 true. latency rollback 기준 및 alert hook 추가 | flag controlled |
| D13 Env / Config | 1차 완료 | `EffectiveRuntimeConfig` shim, redacted snapshot, source_granularity 표시 | 없음 |
| D14 Large Patch Operations | 부분 완료 | trace id 포맷, flag 분리, 회귀 테스트 기반 마련 | 없음 |

사용자 지시 반영 후에도 아직 기본값으로 켜지 않은/후속 범위:

- 2026-05-07 사용자 지시 반영: `ENABLE_CLAUDE_CANDIDATE_ACTIONS=true`, `ENABLE_ACTION_ROUTING=true`가 의도값이다. 이 두 값은 실수로 off로 되돌리지 않는다.
- post-open feature snapshot을 실시간 주문 gate의 필수 입력으로 강제하는 정책. 현재는 funnel 기록과 Claude 후보 prompt hint까지 연결했다.
- Unified Candidate Pool을 실제 `today_tickers`, `universe_tickers`, `trade_ready_tickers`의 단일 source of truth로 완전 승격하는 작업. 현재는 shadow merge와 prompt exclusion 추적이다.
- `ADD_READY` live 추가매수, partial sell, ADD_ON_STRENGTH, RAISE_PROFIT_FLOOR의 실제 주문 실행. 이번 범위에서는 shadow/후속 R10로 둔다.
- dashboard 화면 변경. 현재는 기존 대시보드 유지, jsonl/Telegram system alert hook으로 운영성을 보강한다.
- live latency baseline 수치 확정. 코드 기록은 들어갔고, 실제 운영 로그에서 p50/p95를 채워야 한다.

운영 판단:

- 현재 상태는 "전체 구조의 핵심 계약, 관측 기반, R5+ live 연결 코드가 들어갔고, 신규 행동 활성화는 flag로 통제하는 상태"다.
- 2026-05-07 기준 `candidate_actions`와 `action_routing` live flag는 사용자 지시로 켜서 운영한다.
- 이미 실행 중인 `python trading_bot.py --live` 프로세스는 재시작 전까지 이번 코드 변경을 로드하지 않는다.
- R5 이상 live canary에서는 shadow 로그에서 legacy 선택과 신규 action/routing 차이를 계속 비교해야 한다.

---

## 구현 전 필수 확인 체크리스트

아래 항목은 설계 방향을 바꾸기 위한 검토가 아니라, 구현 중 기존 코드와 충돌하지 않게 하기 위한 pre-check다. 각 항목은 개발 착수 직후 확인하고, 확인 결과를 해당 D 섹션 또는 구현 PR/작업 로그에 반영한다.

| 체크 | 항목 | 확인 기준 | 연결 섹션 |
|---|---|---|---|
| [x] | P0 `intraday_live_unconfirmed` 70% cap 적용 위치 | `unconfirmed_soft_cap()` + `apply_size_cap_once()` 경로로 단일 적용. 기존 직접 곱셈/대입은 제거 | D5, D7 |
| [x] | Telegram alert helper 위치 | `system_alert()` hook을 cycle latency alert에 연결. `TG_SYSTEM_ALERTS_ENABLED`가 켜져 있을 때만 발송 | D9 |
| [x] | 기존 `lesson_candidates` 저장 포맷 | 확인 완료. 저장 포맷은 `{"generated_at","markets":{"KR":[],"US":[]}}`, 항목 필수 축은 `id/market/scope/action/summary/metric_key/sample_count/breached/severity/confidence/generated_at/expires_at` | D10 |
| [x] | VIX cap의 KR 영향 여부 | 확인 완료. 기존 직접 VIX size 보정은 `market == "US"`에서만 적용. KR은 `cross_asset`에서 VKOSPI를 사용하며 신규 기본값은 `KR_GLOBAL_RISK_CAP_ENABLED=false` | D5, D7 |
| [x] | 기존 live cycle latency baseline | `candidate_cycle_latency` jsonl 기록과 alert 기준 key 추가. 실제 `baseline_p95` 숫자는 다음 live 운영 로그에서 확정 | D4, D12 |
| [x] | 직접 `os.getenv` 교체 대상 파일 | 이번 패치 신규 key는 `EffectiveRuntimeConfig` shim 사용. `source_granularity=env_only_v1`로 혼재 상태 명시 | D13 |

이 체크리스트는 R5 이상 live canary 진입 전까지 모두 완료되어야 한다. 미완료 항목이 있으면 shadow/paper 단계는 가능하지만 live 주문 연결은 보류한다.

---

## 0. 최종 판단

현재 시스템은 후보를 못 찾는 시스템이 아니다. 후보는 장전/장중에서 충분히 발견되고 있었지만, 다음 중간 계층이 약해서 실제 수익으로 연결되지 않았다.

```text
후보군 구성
-> 개장 후 상태 관측
-> Claude 상태 분류
-> system gate 평가
-> PlanA/PathB routing
-> sizing/budget 검증
-> exit lifecycle
-> future-blind replay
```

따라서 이번 개선은 단타성 패치가 아니라 전체 실행 구조의 연결 계층을 정리하는 작업이다. 기존 철학인 "Claude가 후보를 고르고 PlanA/PathB가 실행하며, 보유 중 Claude가 재판단한다"는 유지한다.

핵심 변경 방향:

```text
1. 후보는 full_pool에 넓게 보존한다.
2. Claude에는 prompt_pool만 정리해서 보낸다.
3. Claude는 미래 예측자가 아니라 현재 상태 분류자로 사용한다.
4. system gate는 hard safety와 soft quality/timing을 분리한다.
5. PlanA/PathB는 action별로 라우팅하고 같은 종목 중복 진입을 막는다.
6. 매도는 system guard가 먼저 수익을 보호하고 Claude는 예외와 thesis를 검증한다.
7. 모든 판단은 known_at 기준으로 replay 가능해야 한다.
8. 대규모 패치는 feature flag, shadow, funnel log, rollback 단위로 운영한다.
9. `.env.live`와 `config`는 파일 하나로 합치지 않고 EffectiveRuntimeConfig로 통합한다.
```

---

## 1. 기존 문제와 이번 결정

| 문제 | 실제 증상 | 이번 결정 |
|---|---|---|
| `intraday_live_unconfirmed` 차단 | 좋은 장에서도 `judgment_not_executable`으로 0거래 가능 | phase/gate를 명시화하고 재판단 성공 시 실행 가능한 상태로 승격 |
| 장전 후보 단절 | 장전 후보가 있어도 execution funnel로 연결되지 않음 | Unified Candidate Pool로 full/prompt/execution pool 분리 |
| 후보군 품질 판단 부족 | 오른 종목과 빠진 종목이 같은 등급에 섞임 | 개장 후 snapshot과 momentum_state를 Claude 입력에 연결 |
| 조건 과다/중복 | 첫 blocker만 보이고 나머지 gate 상태가 사라짐 | Gate Evaluation Matrix로 전체 gate 결과를 기록 |
| Claude 출력이 거침 | `watchlist/trade_ready`만으로 probe/wait/avoid 표현 불가 | `candidate_actions` schema 도입 |
| PlanA/PathB 충돌 가능 | 같은 종목이 두 경로에서 동시에 주문될 수 있음 | same-ticker action lock과 routing 우선순위 추가 |
| sizing cap 중복 | ATR/VIX/risk_off/selection cap이 중복 적용 가능 | Gate soft cap을 sizing의 단일 입력으로 사용 |
| 수익 보호 약함 | Claude HOLD가 수익 보호를 늦출 수 있음 | hard stop/profit floor/trailing은 Claude보다 우선 |
| replay 착시 | 5m/30m 결과를 사후에 보고 필터링하면 과대평가 | known_at 기반 future-blind replay만 인정 |
| 설정 출처 분산 | `.env.live`, `v2_start_config.json`, `config/v2.py`, 직접 `os.getenv`가 혼재 | 단일 EffectiveRuntimeConfig와 startup config snapshot 도입 |

---

## 2. 전체 개발 범위

개발 단위는 다음 순서로 진행한다.

```text
D1. Data model / schema 계약
D2. Unified Candidate Pool
D3. Post-open Feature Snapshot
D4. Claude Candidate Actions Schema
D5. Gate Evaluation Matrix
D6. PlanA/PathB Action Routing
D7. Sizing / Budget Contract
D8. Exit Lifecycle / Hold Advisor
D9. Observability / Funnel Snapshot
D10. Future-Blind Replay
D11. QA Checklist
D12. Rollout / Rollback
D13. Env/Config Unification
D14. Large Patch Operations
```

구현 순서는 schema를 먼저 고정하고, pool과 관측 로그를 만든 뒤, Claude action과 실행 경로를 붙인다. replay는 모든 단계의 검증 기준이므로 마지막에 도구만 만드는 것이 아니라 각 단계의 산출물이 replay 입력으로 남도록 설계한다.

---

## 3. 공통 계약

### 3.1 Claude action과 system final_action 분리

Claude가 출력하는 값과 system이 최종 판단하는 값은 다르다. 같은 enum으로 섞으면 `HARD_BLOCK` 같은 system 상태를 Claude가 낼 수 있는 것처럼 보이므로 분리한다.

Claude action:

```text
WATCH
PROBE_READY
BUY_READY
ADD_READY
PULLBACK_WAIT
AVOID
```

System final_action:

```text
BLOCKED
WATCH
PROBE_READY
BUY_READY
ADD_READY
PULLBACK_WAIT
HARD_BLOCK
EXPIRED
```

변환 규칙:

```text
ClaudeAction + GateEvaluation + PositionState + RouteLock
-> SystemFinalAction
```

예시:

| Claude action | gate 결과 | position 상태 | system final_action |
|---|---|---|---|
| `BUY_READY` | hard safety 통과 | 미보유 | `BUY_READY` |
| `BUY_READY` | `BROKER_UNTRUSTED` | 무관 | `HARD_BLOCK` |
| `PROBE_READY` | soft cap 30% | 미보유 | `PROBE_READY` with size cap |
| `ADD_READY` | 미보유 | 포지션 없음 | `WATCH` |
| `PULLBACK_WAIT` | price_targets 없음 | 무관 | `WATCH` with reason `missing_pullback_target` |
| action 만료 | 무관 | 무관 | `EXPIRED` |

### 3.2 Action expiration

장전 또는 장초에 나온 action을 장중 후반까지 그대로 쓰면 불리한 가격에서 진입할 수 있다. 모든 `CandidateAction`은 만료 규칙을 갖는다.

기본 만료:

| action | 기본 TTL | 만료 후 |
|---|---:|---|
| `PROBE_READY` | 5분 | `WATCH` |
| `BUY_READY` | 3분 | `WATCH` |
| `ADD_READY` | 3분 | `WATCH` |
| `PULLBACK_WAIT` | 30분 또는 price target 무효 시 | `WATCH` |
| `AVOID` | 30분 | 재평가 가능 |
| `WATCH` | 다음 Claude 호출까지 | 유지 |

운영 규칙:

- TTL은 market별 config로 둔다.
- `expires_at`은 Claude가 직접 임의로 정하지 않는다. runtime이 action 생성 시 부여한다.
- Claude가 `valid_until`을 제공하면 runtime TTL 상한을 넘을 수 없다.
- 만료된 action은 주문 후보에서 제외하고 funnel log에 `action_expired`로 기록한다.

만료 후 재평가 정책:

- action이 만료되어도 `CandidateRecord`는 full_pool에 유지한다.
- 만료되는 것은 action이지 후보 자체가 아니다.
- 다음 Claude 호출에서 prompt_score와 최신 feature가 기준을 만족하면 다시 재평가 대상이 될 수 있다.
- 같은 종목의 반복 probe를 막기 위해 `same_day_probe_attempts`, `last_probe_exit_reason`, `last_action_expired_at`을 gate 입력에 넣는다.
- `PULLBACK_WAIT` 만료 후 재진입하려면 새 feature snapshot 또는 새 Claude action이 필요하다.

### 3.3 Decision owner

같은 종목에 여러 판단이 들어올 수 있으므로 owner를 명시한다.

| owner | 책임 |
|---|---|
| `preopen_collector` | 장전 후보 수집 |
| `intraday_scanner` | 장중 후보 발굴 |
| `claude_selection` | 후보 action 분류 |
| `gate_runtime` | 실행 가능 여부 |
| `plana_runtime` | 즉시/확인 진입 |
| `pathb_runtime` | 눌림/지정가/재진입 |
| `exit_lifecycle` | 손절/수익보호/매도 |
| `hold_advisor` | 보유 thesis 재판단 |

충돌 우선순위:

```text
hard safety > existing position state > active order lock > exit lifecycle > gate result > Claude action
```

### 3.4 Feature flags

대규모 패치 운영을 위해 모든 주요 변경은 flag로 분리한다.

```text
ENABLE_UNIFIED_CANDIDATE_POOL=false
ENABLE_POST_OPEN_FEATURES=false
ENABLE_CLAUDE_CANDIDATE_ACTIONS=true
ENABLE_GATE_EVALUATION_MATRIX=false
ENABLE_ACTION_ROUTING=true
ENABLE_EXIT_LIFECYCLE_V2=false
ENABLE_FUTURE_BLIND_REPLAY=false
ENABLE_PARTIAL_SELL=false
PARTIAL_SELL_SHADOW_ONLY=true
```

2026-05-07 사용자 지시로 `ENABLE_CLAUDE_CANDIDATE_ACTIONS=true`, `ENABLE_ACTION_ROUTING=true`를 정식 운영 의도값으로 둔다. flag는 `.env.live`에서 emergency kill switch로 덮을 수 있어야 한다. 정식 기본값은 config에서 관리한다.

---

## 4. 기존 코드 접점

이번 변경은 새 모듈을 추가하더라도 기존 runtime과 명확히 연결되어야 한다. 단순히 새 pool을 만들면 `today_tickers`, `today_judgment["universe_tickers"]`, `selection_meta`, `trade_ready_tickers`에 이은 네 번째 종목 목록이 생길 수 있다. 따라서 아래 접점별로 읽기/쓰기 책임을 고정한다.

| 영역 | 현재 코드 접점 | 변경 원칙 |
|---|---|---|
| env 로드 | `trading_bot.py` 상단 `load_dotenv`, `_apply_v2_start_config_env()` | D13 `EffectiveRuntimeConfig`로 단계적 흡수 |
| v2 config | `config/v2.py::V2Config.from_env()` | 기존 호환 유지, 신규 config와 비교 테스트 |
| live preflight | `tools/live_preflight.py` | env/config 충돌, snapshot, dangerous flag 검사 추가 |
| 후보 선정 | `minority_report/analysts.py::select_tickers()` | legacy `trade_ready`와 신규 `candidate_actions` 동시 지원 |
| 판단 수집 | `minority_report/analysts.py::get_three_judgments()` | market regime 판단은 유지 |
| selection 적용 | `trading_bot.py::_apply_selection_meta()` | `CandidateAction`과 prompt trace 보존 |
| trade_ready 판별 | `trading_bot.py::_is_trade_ready_ticker()` | legacy only가 아니라 system final_action 참조로 확장 |
| 장전 후보 | `tools/preopen_collector.py`, `trading_bot.py` preopen 경로 | D2 full_pool source `preopen`으로 병합 |
| 장중 rescreen | `trading_bot.py::manual_rescreen()`, `_partial_reselect()`, `_reinvoke_analysts()` | 서로 다른 builder 대신 Unified Candidate Pool 사용 |
| PlanA 진입 | `trading_bot.py::run_cycle()` 신규 진입 루프 | D6 routing 결과만 주문 후보로 사용 |
| PathB 등록 | `runtime/pathb_runtime.py::register_from_selection_meta()` | `PULLBACK_WAIT` + target 있는 경우만 등록 |
| PathB 수량 | `runtime/pathb_runtime.py::_pathb_qty()` | D7 budget contract 준수 |
| risk profile | `runtime/risk_profile.py` | D5 soft cap과 D7 sizing 입력 정합성 유지 |
| lifecycle | `runtime/v2_lifecycle_runtime.py` | same-day/reentry/order arbiter와 D6 lock 연결 |
| 매도 후보 | `risk_manager.py::get_exit_candidates()` | D8 exit priority와 충돌하지 않게 정렬 |
| soft exit | `trading_bot.py::_try_soft_exit_arbitration()` | Claude HOLD override 금지 |
| hold advisor | `minority_report/hold_advisor.py::ask()` | MFE/giveback/floor 중심 입력 |
| quick exit | `minority_report/quick_exit_check.py::quick_exit_check()` | hard guard보다 낮은 우선순위 |
| replay/측정 | `tools/v2_forward_measurer.py`, 신규 replay tool | known_at 검증 필수 |

---

## D1. Data Model / Schema 계약

### 목적

후보, Claude action, post-open feature, gate 평가, routing, 포지션 lifecycle, Claude 호출 trace를 명시적인 schema로 고정한다. schema가 먼저 고정되어야 feature, prompt, replay가 같은 데이터를 바라본다.

### 신규/수정 파일

| 파일 | 목적 |
|---|---|
| `runtime/candidate_pool_runtime.py` | `CandidateRecord`, pool merge/build |
| `runtime/post_open_features.py` | 개장 후 feature snapshot |
| `runtime/gate_evaluation.py` | gate matrix와 final_action |
| `runtime/action_routing.py` | PlanA/PathB routing |
| `runtime/exit_lifecycle.py` | 포지션 exit 상태 |
| `tests/test_candidate_pool_runtime.py` | pool merge/priority |
| `tests/test_post_open_features.py` | feature/known_at |
| `tests/test_gate_evaluation.py` | gate 우선순위 |
| `tests/test_action_routing.py` | PlanA/PathB 충돌 방어 |
| `tests/test_exit_lifecycle.py` | exit 우선순위 |

### CandidateRecord

```python
@dataclass
class CandidateRecord:
    ticker: str
    market: str
    name: str | None
    sources: list[str]
    source_ranks: dict[str, int]
    source_scores: dict[str, float]
    first_seen_at: str
    last_seen_at: str
    preopen_anchor_at: str | None
    preopen_price: float | None
    current_price: float | None
    grade: str | None
    prompt_score: float
    prompt_score_components: dict[str, float]
    feature_snapshot_ref: str | None
    latest_features: dict[str, Any]
    policy_tags: list[str]
    screen_bucket: str | None
    status: str
```

`latest_features`와 `feature_snapshot_ref`의 관계:

- `PostOpenFeatureSnapshot`은 별도 로그/상태 파일에 append한다.
- `CandidateRecord.latest_features`에는 최신 snapshot의 요약 필드만 inline 복사한다.
- `feature_snapshot_ref`는 원본 snapshot id를 가리킨다.
- replay는 원본 snapshot을 기준으로 하고, prompt 생성은 `latest_features`를 사용한다.

이렇게 해야 prompt 생성은 빠르고, replay는 원본 이력으로 검증 가능하다.

### PostOpenFeatureSnapshot

```python
@dataclass
class PostOpenFeatureSnapshot:
    snapshot_id: str
    ticker: str
    market: str
    known_at: str
    anchor_at: str
    anchor_price: float
    current_price: float
    ret_3m_pct: float | None
    ret_5m_pct: float | None
    ret_10m_pct: float | None
    ret_30m_pct: float | None
    from_open_high_pct: float | None
    pullback_from_high_pct: float | None
    opening_range_break: bool | None
    volume_ratio_open: float | None
    spread_bps: float | None
    vwap_distance_pct: float | None
    momentum_state: str
    data_quality: str
```

`vwap_distance_pct`는 1차 구현에서 optional이다. KR WebSocket 누적 VWAP이 아직 명확하지 않으므로 1차는 수익률, 고점 대비 눌림, OR, 거래량, 스프레드로 동작한다. VWAP은 D3 확장 항목으로 둔다.

### CandidateAction

```python
@dataclass
class CandidateAction:
    ticker: str
    market: str
    action: Literal["WATCH", "PROBE_READY", "BUY_READY", "ADD_READY", "PULLBACK_WAIT", "AVOID"]
    confidence: float
    size_intent: Literal["micro", "probe", "normal", "reduced", "none"]
    reason: str
    invalidation_condition: str
    price_targets: dict[str, float]
    created_at: str
    expires_at: str
    source_prompt_id: str
    schema_version: str
```

`PULLBACK_WAIT`는 prompt 단계에서는 `price_targets.entry_below` 또는 `price_targets.entry_zone_low/high` 같은 느슨한 힌트를 허용한다. 다만 live PathB 등록은 `parse_plan_from_claude()`가 실행 가능한 plan으로 파싱할 수 있어야 하므로 `buy_zone_low`, `buy_zone_high`, `sell_target`, `stop_loss`, `hold_days`, `confidence`가 모두 있어야 한다. 이 full plan이 없으면 watchlist에만 유지하고 `missing_pullback_target`을 남긴다.

`valid_until/expires_at`은 optional이다. Claude가 장 현지시각 또는 timezone 없는 과거 시각을 반환하면 runtime은 즉시 만료시키지 않고 해당 값을 무시한 뒤 action별 runtime TTL을 적용한다. 즉 `created_at`보다 이전인 `valid_until`은 `raw_valid_until_before_created_ignored` warning으로 기록한다.

### GateEvaluation

```python
@dataclass
class GateEvaluation:
    ticker: str
    market: str
    known_at: str
    claude_action: str
    final_action: str
    hard_safety: dict[str, Any]
    soft_safety: dict[str, Any]
    timing: dict[str, Any]
    sizing: dict[str, Any]
    affordability: dict[str, Any]
    route_lock: dict[str, Any]
    blocker: str | None
    warnings: list[str]
```

중요: `blocker`는 첫 번째 대표 사유만 기록하고, 전체 gate 결과는 dict에 모두 남긴다. 운영 중에는 "왜 주문이 0건인가"를 첫 blocker만 보고 판단하면 안 된다.

### QA

- schema round-trip 테스트.
- old `trade_ready` list만 있는 legacy Claude 응답 fallback 테스트.
- `CandidateRecord.latest_features`와 snapshot ref 불일치 테스트.
- schema_version 증가 시 migration/backward compatibility 테스트.

---

## D2. Unified Candidate Pool

### 목적

장전 후보, 기본 후보, 장중 후보, 보유/재진입 후보를 full_pool에 보존하고, Claude에는 prompt_score로 정리된 prompt_pool만 전달한다. 실행은 Claude action과 system gate를 통과한 execution_pool에서만 발생한다.

### Pool 계층

```text
full_pool
  모든 후보 보존. 중복 ticker는 source_tags 병합.

prompt_pool
  Claude에게 보낼 후보. cap이 있어도 왜 포함/제외됐는지 기록.

execution_pool
  Claude action + gate를 통과한 주문 가능 후보.
```

### source_tags

```text
preopen
base_universe
opening_fresh
intraday_momentum
late_mover
held
reentry
manual_pin
soft_pin
hard_pin
pead
day_losers
```

검토 보완: 현재 코드에 `intraday_momentum`, `late_mover` source generator가 명확히 없으면 1차 구현에서는 tag만 예약하지 말고 다음 중 하나를 선택한다.

- 실제 generator를 구현한다.
- 구현 전까지 해당 tag는 `deferred_sources`에 기록하고 prompt_score에 반영하지 않는다.

### merge 규칙

같은 `(market, ticker)`는 하나의 `CandidateRecord`로 병합한다.

```text
sources = union(all_sources)
source_ranks[source] = rank
source_scores[source] = score
first_seen_at = min(first_seen_at)
last_seen_at = max(last_seen_at)
grade = 가장 높은 신뢰 source의 grade
policy_tags = union(policy_tags)
```

예시:

```text
076610이 preopen에도 있고 opening_fresh에도 있으면
sources=["preopen", "opening_fresh"]
```

중복 후보를 별도 후보로 만들면 prompt cap과 position limit을 잘못 소모한다.

### prompt_score

`prompt_score`는 0~100으로 정규화한다. 상한이 없으면 여러 source가 붙은 종목이 과도하게 높은 점수를 받아 prompt_pool을 왜곡한다.

기본 계산:

```text
base = 0
preopen confirmed bonus = +30
opening_fresh bonus = +25
intraday_momentum bonus = +20
held/reentry bonus = +15
hard_pin bonus = +20
soft_pin bonus = +8
bad_data penalty = -30
day_losers buy penalty = -25
overextended penalty = -15
score = clamp(base + bonuses - penalties, 0, 100)
```

시장별 cap:

```text
KR_PROMPT_POOL_CAP=30
US_PROMPT_POOL_CAP=30
```

prompt_pool 선택 기준:

1. hard safety 제외.
2. 이미 보유/active order 종목은 별도 owner 규칙으로 유지.
3. `BUY_READY/PROBE_READY` 가능성이 높은 source를 우선.
4. 같은 sector/source 쏠림을 제한.
5. 제외 후보도 `excluded_from_prompt` 로그에 reason 기록.

### full_pool과 prompt_pool 분리 이유

장전 후보 60개를 모두 Claude에게 매번 보내는 것은 비용과 latency를 증가시킨다. 그러나 full_pool에서 버리면 좋은 종목이 장중 확인 후 execution으로 올라올 기회를 잃는다. 따라서 full_pool은 넓게, prompt_pool은 좁게 유지한다.

### QA

- 장전 60개 + 기본 후보 + 장중 후보 병합 시 중복 ticker 1개로 유지.
- soft_pin이 조용히 사라지지 않고 full_pool에 유지.
- prompt cap으로 제외된 종목의 reason 기록.
- prompt_score가 0~100을 넘지 않음.
- source generator가 없는 tag는 prompt_score에 반영하지 않음.

---

## D3. Post-Open Feature Snapshot

### 목적

후보군 품질은 장전 rank만으로 충분하지 않다. 오늘/어제 분석에서도 오른 종목과 빠진 종목이 같은 장전 후보군에 섞였다. 따라서 개장 후 실제 상태를 known_at과 함께 기록하고 Claude에게 제공한다.

### 핵심 원칙

미래를 모른다고 가정한다. 09:05 판단에는 09:05까지 알 수 있는 값만 사용한다. 09:30 판단에는 09:30까지 알 수 있는 값만 사용한다.

### 계산 타이밍

```text
T+3m: opening probe 후보 분류
T+5m: 초기 강세/과열/꺾임 1차 분류
T+10m: 지속성 확인
T+30m: sustained/fade 확인
이후: Claude 재판단 직전 snapshot 생성
```

3분 값은 데이터 수신 안정성 이슈가 있으므로 `PROBE_READY`까지만 허용한다. 3분 snapshot만으로 `BUY_READY` 승격은 금지한다.

### momentum_state

1차는 규칙 기반으로 생성하고 Claude에게 설명 필드로 전달한다. Claude에게 enum 자체를 만들게 하지 않는다.

```text
early_strength
sustained
fade
overextended
pullback_watch
late_mover
weak
unknown
```

시장별 기본 threshold는 config에서 분리한다.

예시:

```text
KR_EARLY_STRENGTH_5M_PCT=1.5
KR_SUSTAINED_30M_PCT=2.0
KR_OVEREXTENDED_5M_PCT=6.0
US_EARLY_STRENGTH_5M_PCT=0.8
US_SUSTAINED_30M_PCT=1.2
US_OVEREXTENDED_5M_PCT=3.0
```

수치는 고정 답이 아니라 시작값이다. replay 결과로 조정한다.

### VWAP 처리

현재 코드에는 intraday high/low 추적은 있으나 VWAP 누적 상태가 명확하지 않다. 따라서 D3 1차 완료 기준에는 VWAP을 필수로 넣지 않는다.

1차 필수:

- `ret_3m_pct`
- `ret_5m_pct`
- `ret_10m_pct`
- `ret_30m_pct`
- `from_open_high_pct`
- `pullback_from_high_pct`
- `opening_range_break`
- `volume_ratio_open`
- `spread_bps`
- `data_quality`

2차 확장:

- tick 누적 VWAP
- VWAP distance
- sector-relative strength

### QA

- 09:05 replay에서 09:30 feature가 보이면 테스트 실패.
- data_quality가 `partial`이면 sizing cap이 적용됨.
- 3분 snapshot만 있는 후보는 `BUY_READY`가 아니라 `PROBE_READY`까지만 가능.
- KR/US threshold가 같은 상수로 묶이지 않음.

---

## D4. Claude Candidate Actions Schema

### 목적

Claude를 더 많이 쓰는 것이 아니라 더 좁고 선명한 역할로 쓴다. Claude는 "오를 종목을 맞혀라"가 아니라 "현재 상태에서 probe/buy/wait/avoid 중 무엇인가"를 판단한다.

### 입력

Claude prompt에는 다음을 제공한다.

```json
{
  "market": "KR",
  "known_at": "2026-05-06T09:05:00+09:00",
  "market_mode": "mild_bull",
  "candidate_pool_summary": {
    "full_pool_count": 59,
    "prompt_pool_count": 30,
    "excluded_count": 29
  },
  "candidates": [
    {
      "ticker": "001440",
      "name": "대한전선",
      "sources": ["preopen", "opening_fresh"],
      "preopen_rank": 16,
      "prompt_score": 84,
      "latest_features": {
        "ret_5m_pct": 2.8,
        "ret_30m_pct": null,
        "momentum_state": "early_strength",
        "data_quality": "good"
      },
      "position_state": "flat",
      "existing_order": false
    }
  ]
}
```

### 출력

```json
{
  "candidate_actions": [
    {
      "ticker": "001440",
      "action": "PROBE_READY",
      "confidence": 0.72,
      "size_intent": "probe",
      "reason": "preopen candidate with early strength and acceptable pullback risk",
      "invalidation_condition": "falls below opening range or ret_5m turns negative",
      "price_targets": {
        "entry_below": 2870,
        "stop_below": 2750
      }
    }
  ],
  "market_commentary": "opening momentum broad but several names are already extended",
  "risk_notes": ["avoid chasing overextended 5m spikes without pullback"]
}
```

### legacy fallback

기존 `trade_ready: [...]` 응답만 오면 graceful degradation한다.

```text
trade_ready ticker -> ClaudeAction BUY_READY
watchlist ticker -> ClaudeAction WATCH
missing action -> WATCH
```

단, legacy fallback으로 생성된 `BUY_READY`는 `legacy_schema=true` warning을 붙이고 size cap을 적용한다. 신규 schema 파싱 실패가 반복되면 feature flag로 기존 path로 rollback한다.

### 호출 빈도

Claude 호출은 무제한 증가시키지 않는다.

| 상황 | 호출 |
|---|---|
| 개장 전 후보 확정 | 1회 |
| 개장 후 T+3~5m | 1회 |
| T+10~15m | 필요 시 1회 |
| T+30m | 필요 시 1회 |
| 보유 종목 exit 재판단 | 이벤트 기반 |
| 급격한 market mode 변경 | rate limit 내 1회 |

품질 향상은 호출 횟수가 아니라 입력 feature와 action schema로 만든다.

### 호출 예산과 latency 기준

초기 운영 목표:

```text
KR+US 합산 Claude calls/day 목표: 30~60
KR+US 합산 hard cap: 80 calls/day
candidate_actions market별 기본 호출: 3~4회/day
hold_advisor: 이벤트 기반, position별 cooldown 적용
```

candidate_actions 호출은 prompt_pool 전체를 한 번에 structured JSON으로 분류한다. 종목별 개별 호출을 금지한다.

기본 latency 예산:

```text
pool build p95 <= 500ms
post-open feature p95 <= 800ms
Claude candidate_actions p95 <= 20s
gate/routing/sizing p95 <= 500ms
cycle total excluding Claude p95 <= 2s
cycle total including Claude p95 <= 25s
```

baseline은 구현 전 현재 live cycle log에서 p50/p95를 먼저 측정한다. D12 rollback의 "기존 대비 2배" 기준은 이 baseline을 기준으로 한다.

### QA

- JSON 파싱 실패 시 기존 리스트 fallback.
- `PULLBACK_WAIT` + price target 없음은 execution 제외.
- action TTL 부여 확인.
- prompt에 future feature가 섞이지 않음.
- Claude가 `HARD_BLOCK`을 출력해도 무시하고 invalid action으로 기록.

---

## D5. Gate Evaluation Matrix

### 목적

진입 조건이 많은 것은 문제 자체가 아니다. 문제는 hard safety, quality, timing, sizing이 섞여 첫 번째 blocker만 남는 것이다. Gate Matrix는 모든 조건을 평가하고 계층별로 기록한다.

### gate 분류

Hard safety:

```text
MARKET_CLOSED
BROKER_UNTRUSTED
ORDER_UNKNOWN_UNRESOLVED
DAILY_LOSS_LIMIT
STOP_CLUSTER_MARKET_BLOCK
STOP_CLUSTER_DISASTER_BLOCK
HALT
POSITION_LIMIT
ACTIVE_ORDER_LOCK
```

Soft safety:

```text
intraday_live_unconfirmed
partial_data
ATR_HIGH
VIX_HIGH
risk_off
STOP_CLUSTER_FIRST_STOP_COOLDOWN
```

Timing:

```text
opening_range_not_formed
momentum_wait
entry_blackout
late_session
same_day_reentry
pullback_target_not_hit
```

Sizing/affordability:

```text
qty_zero
min_order_not_met
budget_cap
cash_shortfall
high_price_one_share
```

### 정책 결정

- `STOP_CLUSTER_MARKET_BLOCK`은 hard safety다. 우회 금지.
- `STOP_CLUSTER_FIRST_STOP_COOLDOWN`은 soft safety다. probe 손절과 full 손절의 가중치를 분리한다.
- `entry_blackout`은 범위에 따라 다르다. 장 마감/시장 폐쇄성 blackout은 hard, opening wait 성격은 timing wait로 처리한다.
- `intraday_live_unconfirmed`는 무조건 hard block이 아니다. fresh Claude action과 post-open feature가 있으면 size cap을 걸고 제한적으로 허용할 수 있다.
- `intraday_live_unconfirmed` 허용 시 size cap은 D5에서만 계산한다. 기본값은 `UNCONFIRMED_ENTRY_SIZE_CAP_PCT=70`이며 `GateEvaluation.soft_safety.size_cap_pct=70`으로 전달한다.
- 기존 P0 코드에서 별도로 70%를 곱하는 경로가 남아 있으면 D7에서 이중 cap이 발생하므로, 신규 구조에서는 해당 로직을 GateEvaluation 생성 위치로 이동한다.
- VIX는 US 직접 risk signal이다. KR에는 기본적으로 직접 적용하지 않고, `KR_GLOBAL_RISK_CAP_ENABLED=true`일 때만 global risk cap으로 약하게 반영한다.

초기 config:

```text
UNCONFIRMED_ENTRY_SIZE_CAP_PCT=70
US_VIX_RISK_CAP_ENABLED=true
KR_GLOBAL_RISK_CAP_ENABLED=false
KR_GLOBAL_RISK_CAP_PCT=80
```

### 출력

```json
{
  "ticker": "001440",
  "claude_action": "PROBE_READY",
  "final_action": "PROBE_READY",
  "blocker": null,
  "hard_safety": {"passed": true},
  "soft_safety": {
    "passed": true,
    "size_cap_pct": 50,
    "warnings": ["unconfirmed_phase_size_cap"]
  },
  "timing": {"passed": true},
  "sizing": {"max_budget_krw": 50000},
  "affordability": {"qty": 17}
}
```

### QA

- hard safety는 Claude action과 무관하게 차단.
- soft safety는 final_action을 유지하되 size cap/warning 제공.
- 전체 gate vector가 funnel log에 남음.
- 첫 blocker만으로 나머지 gate 결과가 사라지지 않음.

---

## D6. PlanA / PathB Action Routing

### 목적

Claude action을 PlanA와 PathB 실행 경로로 명확히 라우팅하고, 같은 종목의 중복 진입과 충돌을 막는다.

### routing

| System final_action | Route |
|---|---|
| `PROBE_READY` | PlanA probe |
| `BUY_READY` | PlanA normal/reduced buy |
| `ADD_READY` | existing position add 검토 |
| `PULLBACK_WAIT` | PathB plan 등록 |
| `WATCH` | watch 유지 |
| `EXPIRED` | action 폐기 |
| `HARD_BLOCK` | 주문 금지 |

### PlanA/PathB 충돌 정책

같은 `(market, ticker)`에 대해 atomic route lock을 둔다.

```text
PlanA order pending -> PathB suspend
PlanA fill -> PathB cancel
PlanA reject/no fill -> PathB resume if action still valid
PlanA stop loss -> PathB 자동 resume 금지, Claude/gate 재평가 필요
PathB active order -> PlanA buy 금지
ADD_READY -> 보유 포지션이 있을 때만 검토
```

`BUY_READY`와 기존 PathB waiting plan이 동시에 있으면 즉시 추격 매수와 눌림 대기 중 하나를 선택해야 한다. 기본 정책은 다음과 같다.

```text
BUY_READY confidence >= high threshold and not overextended -> PlanA 우선, PathB cancel
overextended 또는 pullback risk 높음 -> PathB 유지, PlanA watch
```

초기 threshold:

```text
PLANB_CANCEL_CONFIDENCE_MIN=0.75
PLANB_CANCEL_REQUIRE_NOT_OVEREXTENDED=true
PLANB_CANCEL_REQUIRE_GOOD_DATA=true
```

confidence가 0.75 미만이면 `BUY_READY`라도 기존 PathB waiting plan을 자동 취소하지 않는다. 이 경우 system final_action은 market 상태에 따라 `PROBE_READY` 또는 `WATCH`로 낮출 수 있다.

### ADD_READY 검증

`ADD_READY`는 Claude가 낼 수 있지만 최종 검증은 system이 broker truth 기준으로 수행한다.

```text
broker position exists and local position exists -> ADD_READY 검토
broker position missing or local position missing -> WATCH/add_without_position
active sell pending -> WATCH/add_blocked_by_exit_order
ADD_READY flag off -> WATCH/add_shadow_only
```

Claude prompt에는 position_state를 제공하되, Claude 입력을 broker truth로 신뢰하지 않는다.

### EXPIRED 처리

`EXPIRED`는 route가 아니다. action이 만료되면 실행 후보에서 제외하고 full_pool에는 유지한다. 다음 Claude 호출에서 최신 feature와 cooldown이 허용하면 다시 action을 받을 수 있다.

### PathB price target 요구

`PULLBACK_WAIT`가 live PathB로 가려면 runtime 등록 가능한 full plan이 필요하다.

필수 필드:

- `buy_zone_low`
- `buy_zone_high`
- `sell_target`
- `stop_loss`
- `hold_days`
- `confidence`

`entry_below`, `entry_zone_low/high`, OR/VWAP/pullback target은 Claude 판단과 prompt에는 사용할 수 있지만, 위 필드로 변환되지 않으면 PathB plan을 만들지 않고 `WATCH` 처리한다. 검토 반영: 느슨한 target으로 PathB wait run을 만들면 등록 직후 실패하거나 잘못된 대기 주문이 생길 수 있으므로 live 라우팅은 보수적으로 둔다.

### QA

- 같은 종목에 PlanA/PathB 주문이 동시에 나가지 않음.
- pending order가 있으면 다른 route가 suspend.
- PlanA reject 후 action TTL이 살아 있을 때만 PathB resume.
- 손절 후 PathB 자동 재진입 없음.

---

## D7. Sizing / Budget Contract

### 목적

고가주 1주 문제, probe size, PathB cap, min_order override를 하나의 계약으로 정리한다.

### sizing 우선순위

```text
1. hard max order cap
2. Claude size_intent
3. GateEvaluation.soft_safety.size_cap_pct
4. market/strategy profile cap
5. probe/add/full multiplier
6. affordability/min order
7. final broker precheck
```

중요: VIX/risk_off/ATR cap은 D5에서 `soft_safety.size_cap_pct`로 계산하고 D7에서는 그 값을 사용한다. 중복 계산하지 않는다.

### 기본 size_intent

| size_intent | 의미 |
|---|---|
| `micro` | 아주 작은 확인 진입 |
| `probe` | 작게 먼저 잡는 진입 |
| `normal` | 일반 진입 |
| `reduced` | 리스크 축소 진입 |
| `none` | 주문 없음 |

초기 multiplier:

```text
MICRO_SIZE_RATIO=0.10
PROBE_SIZE_RATIO=0.30
REDUCED_SIZE_RATIO=0.50
NORMAL_SIZE_RATIO=1.00
ADD_SIZE_RATIO=0.30
```

이 값은 확정 성능값이 아니라 초기 운영값이다. D10 replay에서 market별로 calibration한다.

cap 적용 규칙:

```text
raw_budget
-> size_intent multiplier
-> GateEvaluation.soft_safety.size_cap_pct
-> market/strategy cap
-> hard budget cap
```

`UNCONFIRMED_ENTRY_SIZE_CAP_PCT=70`은 D5에서 `GateEvaluation.soft_safety.size_cap_pct`로 들어온다. D7에서는 이 값을 다시 계산하지 않는다.

### 고가 1주 정책

고가 1주 허용 여부는 market별 config로 분리한다.

```text
KR_ALLOW_ONE_SHARE_OVER_BUDGET=false
US_ALLOW_ONE_SHARE_OVER_BUDGET=true
KR_ONE_SHARE_MAX_ACCOUNT_PCT=5.0
US_ONE_SHARE_MAX_ACCOUNT_PCT=7.0
```

허용하지 않으면 `qty_zero`가 아니라 `high_price_one_share_blocked`로 기록한다. 운영자가 "돈이 없어서 0주"와 "정책상 1주를 막음"을 구별해야 한다.

### min_order override

min_order를 맞추기 위해 예산 상한을 뚫으면 안 된다.

```text
qty_by_budget = floor(max_budget / price)
qty_by_min_order = ceil(min_order / price)
candidate_qty = max(qty_by_budget, qty_by_min_order)

if candidate_qty * price > hard_budget_cap:
    block or downgrade
```

PathB에서 이미 발견된 예산 우회 문제는 이 계약으로 재발 방지한다.

### probe stop weighting

probe 손절은 full 손절과 같은 STOP_CLUSTER 가중치로 집계하지 않는다.

```text
stop_weight = min(1.0, order_notional / normal_order_notional)
probe_stop_weight_default = 0.25
daily_sl_weighted += stop_weight
```

운영 로그에는 raw stop count와 weighted stop count를 모두 남긴다.

### QA

- min_order override가 hard cap을 넘지 않음.
- high price 1주 차단 사유가 `qty_zero`와 분리됨.
- D5 size cap이 D7에서 한 번만 적용됨.
- probe stop이 STOP_CLUSTER에 1.0으로 집계되지 않음.

---

## D8. Exit Lifecycle / Hold Advisor

### 목적

작게 먼저 잡고 Claude가 재판단하게 하려면 매도/보유 정보의 품질이 중요하다. 다만 Claude HOLD가 손절, 수익 보호, trailing을 막으면 안 된다.

### exit 우선순위

```text
1. broker/position integrity
2. hard stop
3. recovery_micro hard loss
4. profit floor
5. trailing stop
6. time/no-carry recovery_micro
7. soft exit advisor
8. Claude hold/sell thesis check
9. session close
```

검토 반영: recovery_micro를 한 덩어리로 두면 profit_floor보다 먼저 수익 포지션을 닫을 수 있다. 따라서 `hard_loss` 성격은 profit_floor보다 앞, `time/no-carry` 성격은 profit_floor/trailing 뒤로 분리한다.

구현 반영: `recovery_micro_time_stop`, `recovery_micro_no_carry`, `pre_close`, `session_close`는 Claude HOLD가 무효화하지 못하는 system guard reason으로 분류한다. 실제 후보 정렬 순서는 기존 `reason_priority`가 유지하므로 time/no-carry가 profit_floor/trailing보다 먼저 실행되는 구조는 만들지 않는다.

### Claude override 금지

Claude HOLD가 무효화할 수 없는 것:

```text
hard stop
broker position mismatch
profit floor breach
trailing stop breach
session close hard rule
```

Claude가 유예할 수 있는 것:

```text
soft thesis exit
weak momentum exit
minor giveback warning
```

### Hold Advisor prompt 입력

```json
{
  "ticker": "001440",
  "entry_price": 2860,
  "current_price": 3020,
  "pnl_pct": 5.6,
  "mfe_pct": 7.2,
  "giveback_from_mfe_pct": 1.6,
  "profit_floor_pct": 3.0,
  "trailing_stop_pct": 2.0,
  "momentum_state": "sustained",
  "alternative_opportunity_summary": {
    "stronger_candidates": 2,
    "best_prompt_score": 88
  }
}
```

`alternative_opportunity_summary`는 D2 pool이 안정된 뒤 활성화한다. 그 전에는 null로 두고 prompt에서 사용하지 않는다.

활성화 단계:

```text
R0~R5: alternative_opportunity_summary=null
R6 이후: candidate_pool과 prompt_score가 안정되면 shadow로 제공
R8 이후: exit prompt에서 참고 가능
```

### Hold Advisor 호출 조건

Hold Advisor는 매 cycle 호출하지 않는다. position별 이벤트 기반으로 호출하고 cooldown을 둔다.

호출 조건:

```text
giveback_from_mfe_pct >= 1.0
pnl_pct가 profit_floor 근처 또는 profit_floor breach 직전
momentum_state 변화
soft exit advisor가 SELL/REDUCE 후보로 분류
session close 전 보유 지속 여부 확인
큰 수익 포지션에서 thesis_status 재확인 필요
```

throttle:

```text
HOLD_ADVISOR_POSITION_COOLDOWN_MIN=15
HOLD_ADVISOR_MAX_CALLS_PER_POSITION_DAY=4
HOLD_ADVISOR_MAX_CALLS_PER_MARKET_DAY=20
```

hard stop, broker mismatch, profit_floor breach, trailing stop breach는 Hold Advisor 호출 없이 system이 먼저 처리한다.

### 부분 매도

`PARTIAL_SELL`은 이번 대규모 패치의 live 범위에서 제외한다. schema와 shadow log만 남기고 `ENABLE_PARTIAL_SELL=false`, `PARTIAL_SELL_SHADOW_ONLY=true`를 유지한다.

deferred 이유:

- 현재 `_execute_sell()`이 전체 청산 중심일 가능성이 높다.
- 부분 수량, 평균단가, 잔여 profit_floor/trailing 재계산이 필요하다.
- live rollout R0~R9에는 포함하지 않는다.
- 별도 후속 명세에서 R10 `PARTIAL_SELL limited live`를 정의한다.

### QA

- hard stop은 Claude 호출 없이 청산.
- profit floor breach는 Claude HOLD로 막히지 않음.
- trailing config override 경로가 명시적으로 테스트됨.
- partial sell flag off에서는 부분 매도 action이 실행되지 않음.

---

## D9. Observability / Funnel Snapshot

### 목적

운영 중 "왜 오늘 0건인가"를 장 끝나고 분석하면 늦다. 후보 수, prompt 수, Claude action, gate, route, sizing, exit 상태를 장중에 확인할 수 있어야 한다.

### 로그 경로

```text
logs/funnel/candidate_funnel_YYYYMMDD.jsonl
logs/funnel/gate_evaluation_YYYYMMDD.jsonl
logs/funnel/action_routing_YYYYMMDD.jsonl
logs/funnel/exit_lifecycle_YYYYMMDD.jsonl
```

현재 코드에 `logs/funnel` 생성 경로가 있어도 명세상 소유권을 D9로 고정한다. 경로가 없으면 runtime이 생성한다.

### cycle summary

매 cycle 또는 상태 변화 시 다음을 기록한다.

```json
{
  "known_at": "2026-05-06T09:05:00+09:00",
  "market": "KR",
  "full_pool_count": 59,
  "prompt_pool_count": 30,
  "execution_pool_count": 3,
  "claude_actions": {
    "PROBE_READY": 2,
    "BUY_READY": 1,
    "PULLBACK_WAIT": 4,
    "AVOID": 5,
    "WATCH": 18
  },
  "blocked": {
    "hard_safety": 0,
    "soft_safety": 2,
    "timing": 5,
    "sizing": 1
  },
  "orders_submitted": 1
}
```

### candidate_quality_report

후보군 품질은 매수/매도와 분리해서 별도 이벤트로 기록한다. cycle summary에는 aggregate만 넣고, 상세 품질 리포트는 별도 event로 남긴다.

```json
{
  "event_type": "candidate_quality_report",
  "known_at": "2026-05-06T09:05:00+09:00",
  "market": "KR",
  "full_pool_count": 59,
  "gainers_ratio": 0.42,
  "bucket_distribution": {
    "preopen": 59,
    "opening_fresh": 8,
    "day_losers": 3
  },
  "external_catalyst_needed_count": 12,
  "quality_warnings": ["preopen_grade_not_discriminative"]
}
```

이 이벤트는 "후보군 자체가 좋았는가"를 판단하기 위한 것이며, 주문 실행 실패 분석과 분리한다.

쓰기 정책:

- 종목 상태 변화 시 append.
- cycle summary는 `FUNNEL_SUMMARY_THROTTLE_SEC=60` 기준으로 throttle.
- 같은 사유 반복은 집계 필드로 압축.

### 운영 알림

다음은 live 중 경고 대상이다.

```text
full_pool > 0 and prompt_pool == 0
prompt_pool > 0 and all actions WATCH/AVOID
BUY_READY/PROBE_READY > 0 and execution_pool == 0
execution_pool > 0 and orders_submitted == 0
same blocker dominates > 80%
legacy fallback count > threshold
config conflict detected
```

전달 매체:

```text
WARNING 이상: live logger + Telegram
INFO summary: live logger
CRITICAL: Telegram 즉시 전송 + rollback recommendation 기록
```

Telegram helper 위치는 구현 전 코드에서 확인한다. helper를 찾지 못하면 D9는 logger-only로 끝내지 말고 alert adapter를 신규로 만든다.

### QA

- 장중 0거래일 때 어디서 막혔는지 한 줄 summary로 확인 가능.
- 첫 blocker뿐 아니라 전체 gate 분포 확인 가능.
- dashboard는 후속이어도 jsonl만으로 운영 진단 가능.

---

## D10. Future-Blind Replay

### 목적

사후에 5m/30m 결과를 보고 고르면 당연히 좋아 보인다. replay는 "그 시각에 알 수 있었던 정보만" 사용해야 한다.

### 입력

```text
preopen candidate snapshots
post-open feature snapshots with known_at
Claude prompt/response trace
gate_evaluation jsonl
action_routing jsonl
orders/fills
price candles/ticks
baseline actual logs
```

D9 이전 과거 로그는 funnel jsonl이 없을 수 있다. 이 경우 baseline replay는 가능한 로그만 사용하고 `missing_funnel_log=true`를 표시한다.

### replay 시나리오

```text
baseline_actual
pool_only
pool_plus_features
pool_plus_features_plus_actions
full_execution_model
```

### false metrics

시장별 threshold를 config로 둔다.

```text
false_probe:
  PROBE_READY 진입 후 max favorable excursion이 threshold 미만이고 stop/time exit

false_avoid:
  AVOID/WATCH 처리 후 known future window에서 threshold 이상 상승
```

예시:

```text
KR_FALSE_AVOID_RETURN_PCT=3.0
US_FALSE_AVOID_RETURN_PCT=1.5
KR_FALSE_PROBE_MFE_PCT=0.5
US_FALSE_PROBE_MFE_PCT=0.3
```

이 threshold는 확정 성능 기준이 아니라 초기값이다. replay 결과가 5거래일 이상 쌓이면 ATR, market volatility, 실제 stop 분포를 기준으로 calibration한다.

### 원칙

- replay 결과로 `brain.json`이나 live policy를 자동 변경하지 않는다.
- replay는 lesson candidate만 만들고 사람이 승인한다.
- lesson candidate 생성 주체는 replay tool이다. Claude는 lesson 후보의 설명을 보강할 수 있지만 저장 여부와 live policy 반영 여부를 결정하지 않는다.
- 기존 `lesson_candidates` 저장 포맷은 구현 전 확인하고, 포맷이 맞지 않으면 adapter를 둔다.
- look-ahead feature 사용 시 테스트 실패.

### QA

- 09:05 replay에서 09:30 snapshot 사용 금지.
- baseline_actual과 replay 결과 차이를 출력.
- 주문 latency/slippage/position limit 반영.
- buy-and-hold 수익률과 실제 exit lifecycle 수익률을 분리.

---

## D11. QA Checklist

### 단위 테스트

```text
candidate merge
prompt cap exclusion reason
post-open known_at
Claude schema parse/fallback
action TTL expiration
gate hard/soft/timing/sizing
PlanA/PathB route lock
sizing cap/min_order/high_price
probe stop weighting
exit priority
future-blind replay
effective config precedence
```

테스트 파일 소유권:

```text
tests/test_candidate_pool_runtime.py
tests/test_post_open_features.py
tests/test_claude_candidate_actions.py
tests/test_gate_evaluation.py
tests/test_action_routing.py
tests/test_sizing_contract.py
tests/test_exit_lifecycle.py
tests/test_future_blind_replay.py
tests/test_effective_runtime_config.py
```

대규모 기존 테스트 파일 하나에 계속 추가하지 않는다. 신규 구조별 테스트 파일을 분리하고, 핵심 회귀 테스트 명령에 위 파일들을 추가한다.

### 통합 테스트

```text
장전 60개 -> full_pool 60개 유지
prompt cap 30 -> excluded reason 기록
Claude mock PROBE/BUY/WAIT/AVOID -> gate -> route
PathB target 없는 PULLBACK_WAIT -> watch
PlanA pending 중 PathB suspend
hard stop 발생 시 Claude 없이 매도
config conflict 발생 시 preflight fail
```

### 운영 리허설

```text
paper KR 1일
paper US 1일
shadow candidate_actions 1일
execution flag off 상태에서 funnel log 검증
소액/probe only canary
rollback flag 확인
```

운영 리허설 pass/fail:

```text
candidate_actions parse_failure_rate < 5%
funnel log write error = 0
gate_evaluation missing trace_id = 0
same ticker duplicate live order = 0
hard safety bypass = 0
config conflict unresolved = 0
cycle p95 latency <= baseline * 2
```

---

## D12. Rollout / Rollback

### 단계

```text
R0. Schema/log only
R1. Unified pool shadow
R2. Post-open feature shadow
R3. Claude candidate_actions shadow
R4. Gate matrix shadow
R5. PROBE_READY live canary
R6. BUY_READY limited live
R7. Exit lifecycle v2 shadow
R8. Exit lifecycle v2 live
R9. Future-blind replay batch
```

R4에서 `BUY_READY`를 실제 매수로 바꾸지 않는다. R4는 shadow 비교 단계다. 실제 주문은 R5 probe canary부터 시작한다.

`PARTIAL_SELL`은 R0~R9에 포함하지 않는다. R9 이후 별도 후속 명세에서 R10 `PARTIAL_SELL limited live`를 검토한다.

### 2026-05-06 구현 반영 상태

코드 기준으로 R5~R8의 실행 연결부는 구현되어 있다.

```text
R5/R6:
candidate_actions -> action routing -> PlanA trade_ready / probe size cap / PathB wait registration

R7/R8:
exit candidates / intraday Claude review -> exit_lifecycle_decision event
ENABLE_EXIT_LIFECYCLE_V2=true일 때 system guard 우선 적용

운영 기본값:
ENABLE_CLAUDE_CANDIDATE_ACTIONS=true
ENABLE_ACTION_ROUTING=true
ENABLE_EXIT_LIFECYCLE_V2=false
shadow flags=true
```

따라서 지금 상태는 "구현 완료, candidate action/routing live flag는 사용자 지시로 켜고, exit lifecycle v2와 후속 행동은 별도 flag로 분리"다. 실전 장에서는 동일 세션 shadow log에서 `candidate_actions_source`, `candidate_action_routes`, `pathb_wait_tickers`, `candidate_cycle_latency`를 계속 확인한다.

### rollout 진입 기준

R7에서 R8로 넘어가는 기준:

```text
exit lifecycle shadow 3 trading sessions 이상
hard stop override miss = 0
profit_floor breach 감지 성공률 = 100%
trailing breach 감지 성공률 = 100%
high severity exit decision mismatch = 0
hold_advisor call budget 초과 = 0
```

R5에서 R6로 넘어가는 기준:

```text
PROBE_READY live canary 1 trading session 이상
same ticker duplicate order = 0
probe stop weighted count가 raw count보다 낮게 집계됨
BUY_READY shadow와 legacy trade_ready 차이 분석 완료
```

### Step / Rollout 매핑

| 구현 Step | rollout 단계 |
|---|---|
| Step 1 Schema / Config skeleton | R0 |
| Step 2 Observability foundation | R0 |
| Step 3 Unified Candidate Pool shadow | R1 |
| Step 4 Post-open Feature Snapshot shadow | R2 |
| Step 5 Claude action schema shadow | R3 |
| Step 6 Gate Matrix shadow | R4 |
| Step 7 Routing canary | R5 |
| Step 8 Sizing contract | R5~R6 |
| Step 9 Exit lifecycle v2 | R7~R8 |
| Step 10 Future-blind replay | R9 |
| Step 11 Live rollout | R5 이후 단계별 |

### rollback 기준

즉시 rollback:

```text
hard safety bypass 발견
같은 ticker 중복 주문
config conflict with live mode
order unknown unresolved 증가
funnel log write failure가 반복되어 상태 추적 불가
```

cycle lag 기준:

```text
candidate cycle p95 latency > baseline_p95 * 2
candidate cycle excluding Claude p95 > 2s for 3 cycles
candidate cycle including Claude p95 > 25s for 3 cycles
Claude action parse failure > 20% for 3 cycles
execution_pool > 0 but route errors > 30% for 3 cycles
```

rollback 방법:

```text
ENABLE_ACTION_ROUTING=false
ENABLE_CLAUDE_CANDIDATE_ACTIONS=false
ENABLE_UNIFIED_CANDIDATE_POOL=false
ENABLE_EXIT_LIFECYCLE_V2=false
```

flag off 시 기존 `watchlist/trade_ready` 루프가 동작해야 한다.

주의: 위 `false` 값은 emergency rollback 절차다. 평상시 운영 의도값은 2026-05-07 사용자 지시에 따라 `ENABLE_CLAUDE_CANDIDATE_ACTIONS=true`, `ENABLE_ACTION_ROUTING=true`다.

---

## D13. Env / Config 통합 명세

### 현재 상태

현재 live 설정은 여러 출처에 흩어져 있다.

```text
.env.live / .env.paper / .env
config/v2_start_config.json
config/v2.py::V2Config.from_env()
trading_bot.py 직접 os.getenv
runtime/* 직접 os.getenv
tools/live_preflight.py 별도 검사
```

`trading_bot.py`는 `--live` 여부로 `.env.live` 또는 `.env.paper`를 로드하고, 그 뒤 `_apply_v2_start_config_env()`가 `config/v2_start_config.json`을 env로 다시 적용한다. 이후 `config/v2.py`와 여러 runtime이 직접 env를 읽는다.

문제:

- 같은 설정이 env와 json에 동시에 있으면 우선순위가 헷갈린다.
- live 중 어떤 값이 실제로 적용됐는지 한눈에 보기 어렵다.
- feature flag와 전략 파라미터가 secrets와 섞인다.
- 대규모 패치에서 잘못된 env 하나가 전체 동작을 바꿀 수 있다.

### 결정

`.env.live`와 `config`를 물리적으로 하나의 파일로 합치지 않는다. 대신 단일 `EffectiveRuntimeConfig` 로더로 통합한다.

이유:

- `.env.live`에는 API key, 계좌, 토큰 같은 secret이 들어갈 수 있다.
- 전략/운영 파라미터는 versioning과 diff가 쉬운 config 파일에 있어야 한다.
- secrets를 config json/yaml에 넣으면 git/로그 유출 위험이 커진다.

### 책임 분리

`.env.live`에 남길 것:

```text
KIS/브로커 credentials
ANTHROPIC/API credentials
TELEGRAM credentials
계좌 식별값
RUN_MODE/live-paper selector
emergency kill switch flags
```

deployment selector로 둘 것:

```text
V2_START_CONFIG_PATH
CONFIG_PROFILE
LIVE_CONFIG_PATH
```

`V2_START_CONFIG_PATH`는 secret이 아니다. `.env.live`에 있을 수는 있지만 redaction 대상이 아니며, effective config snapshot에 어떤 config 파일이 로드됐는지 반드시 표시한다.

config로 옮길 것:

```text
candidate pool cap
post-open thresholds
Claude action TTL
market별 sizing cap
PlanA/PathB routing policy
exit lifecycle thresholds
replay thresholds
observability throttle
```

코드 default로만 둘 것:

```text
상수적 안전 default
schema version fallback
파일 경로 기본값
```

### precedence

우선순위는 명시적으로 고정한다.

```text
1. CLI explicit override
2. emergency env override from .env.live
3. config/live.json 또는 config/v2_start_config.json
4. .env.live non-secret operational fallback
5. code default
```

단, secret은 config 파일에서 읽지 않는다.

### 신규 로더

권장 파일:

```text
config/runtime_config.py
```

역할:

```python
class EffectiveRuntimeConfig:
    secrets: SecretConfig
    candidate_pool: CandidatePoolConfig
    post_open: PostOpenConfig
    claude_actions: ClaudeActionConfig
    gates: GateConfig
    routing: RoutingConfig
    sizing: SizingConfig
    exit: ExitConfig
    replay: ReplayConfig
    observability: ObservabilityConfig
    source_report: dict[str, Any]
```

startup에서 한 번 로드하고, 주요 runtime에는 config object를 주입한다. 기존 직접 `os.getenv`는 한 번에 다 제거하지 않고, 이번 패치가 만지는 모듈부터 단계적으로 제거한다.

Step 1 완료 기준은 "초안 생성"이 아니라 다음 최소 기능까지 포함한다.

```python
cfg.get("KEY", default=None)
cfg.get_bool("KEY", default=False)
cfg.get_int("KEY", default=0)
cfg.get_float("KEY", default=0.0)
cfg.source_of("KEY")
cfg.source_report()
```

이 shim은 기존 `os.getenv`와 호환되는 migration layer다. 이후 각 Step에서 수정하는 모듈은 해당 모듈의 신규 key에 대해 직접 `os.getenv`를 남기지 않는다.

migration 정책:

```text
Step 1: runtime_config shim + source_report
Step 2~3: observability/candidate pool 신규 key는 cfg 사용
Step 4~6: Claude action/gate/routing 신규 key는 cfg 사용
Step 7~9: sizing/exit/replay 신규 key는 cfg 사용
legacy key: 기존 경로 유지하되 source_report에 direct_env_legacy로 표시
```

### startup config snapshot

live 시작 시 다음을 로그로 남긴다.

```text
logs/config/effective_config_YYYYMMDD_HHMMSS.redacted.json
```

포함:

- config source path
- env file path
- effective value
- value source
- secret redaction
- config hash
- 이전 실행 대비 diff

금지:

- API key/token/account secret 평문 기록.

### preflight 강화

`tools/live_preflight.py`에 추가한다.

```text
env/config 동일 key 충돌 검사
live mode에서 paper-only key 감지
secret 누락 검사
dangerous flag 조합 검사
threshold range 검사
config schema_version 검사
effective config snapshot 생성 가능 여부 검사
```

충돌 정책:

- secret 충돌: hard fail.
- live/paper mode 충돌: hard fail.
- strategy threshold 충돌: warning 또는 hard fail을 config로 선택.
- emergency env override는 source_report에 반드시 표시.

### QA

- `.env.live` + config 충돌 시 preflight fail.
- emergency flag가 config보다 우선.
- redacted snapshot에 secret이 없음.
- 기존 `V2Config.from_env()` 값과 EffectiveRuntimeConfig 값 비교 테스트.
- feature flag off 시 기존 경로 동작.

---

## D14. Large Patch Operations

### 목적

이번 변경은 후보, Claude, gate, 주문, 매도, config를 건드리는 대규모 패치다. 미스가 나지 않는 것보다 미스가 난 위치를 즉시 확인할 수 있게 만드는 것이 중요하다.

### 운영상 가장 위험한 미스

| 미스 | 영향 | 감지 방법 |
|---|---|---|
| 후보가 full_pool에서 누락 | 좋은 종목이 아예 평가되지 않음 | source별 input count vs full_pool count |
| prompt cap에서 조용히 제외 | Claude가 좋은 후보를 못 봄 | excluded_from_prompt reason |
| Claude schema 파싱 실패 | action이 전부 WATCH fallback | parse_failure rate |
| hard safety 우회 | 손실/중복주문 위험 | gate hard_safety audit |
| PlanA/PathB 중복 주문 | 포지션 과다 | route lock event |
| sizing cap 이중 적용 | 너무 작게 사거나 0주 | cap trace |
| min_order가 cap 우회 | 예상보다 큰 주문 | budget trace |
| exit priority 오류 | 손절/수익보호 실패 | exit decision trace |
| config 충돌 | 운영 의도와 다른 동작 | effective config snapshot |
| replay look-ahead | 성과 과대평가 | known_at validator |

### trace id

각 후보에는 cycle 내 trace id를 붙인다.

```text
candidate_trace_id = {date}|{market}|{ticker}|{first_seen_compact}|{cycle_id}
```

예시:

```text
20260506|KR|001440|20260506T090500|cycle_0007
```

구분자 `:`는 사용하지 않는다. ISO8601 timestamp에는 `:`가 포함되므로 `split(":")` 기반 분석 도구가 깨질 수 있다.

이 id가 다음 로그를 관통해야 한다.

```text
candidate_pool
post_open_snapshot
Claude prompt/response
gate_evaluation
action_routing
sizing_decision
order_submit
fill
exit_lifecycle
replay
```

### patch safety checklist

구현 전:

- schema_version 결정.
- feature flag default off.
- config key와 env key 충돌 목록 작성.
- 기존 dirty 파일과 unrelated 변경 확인.

구현 중:

- 모듈별 테스트 추가 후 구현.
- live 주문 경로는 마지막에 연결.
- action/routing은 shadow 로그로 먼저 확인.

구현 후:

- unit + integration + preflight 실행.
- paper replay 실행.
- funnel summary 수동 검토.
- rollback flag 동작 확인.

### 운영 KPI

새 구조의 성공 여부는 수익률 하나로만 보지 않는다.

```text
full_pool retention rate
prompt_pool inclusion reason coverage
Claude action parse success rate
BUY/PROBE -> execution conversion rate
blocked by hard/soft/timing/sizing distribution
qty_zero vs high_price_one_share_blocked split
probe stop weighted count
profit giveback reduction
future-blind replay vs actual gap
```

KPI 계산 정의:

```text
profit_giveback_pct = MFE_pct - realized_exit_pnl_pct
profit_giveback_reduction = median(old_profit_giveback_pct) - median(new_profit_giveback_pct)
measurement_set = realized positions with MFE_pct >= 1.0
```

정의할 수 없는 KPI는 사용하지 않는다. 모든 KPI는 jsonl 로그에서 재계산 가능해야 한다.

---

## 15. 구현 순서와 완료 기준

### Step 1. Schema / Config skeleton

완료 기준:

- D1 dataclass 또는 typed dict 생성.
- `EffectiveRuntimeConfig` shim 생성: `get/get_bool/get_int/get_float/source_of/source_report`.
- direct `os.getenv` 신규 사용 금지 기준 문서화.
- 기존 live cycle p50/p95 latency baseline 측정.
- schema round-trip 테스트 통과.
- feature flag default off.

### Step 2. Observability foundation

완료 기준:

- `logs/funnel` jsonl writer 생성.
- candidate/gate/route/exit trace id 형식 고정.
- feature flag off 상태에서도 cycle summary 기록 가능.
- 0거래 cycle에서 대표 차단 원인과 전체 gate 분포가 보임.

### Step 3. Unified Candidate Pool shadow

완료 기준:

- 장전/기본/장중 후보가 full_pool에 병합.
- source_tags 병합.
- prompt_pool cap과 제외 reason 기록.
- 기존 실행 경로에는 영향 없음.

### Step 4. Post-open Feature Snapshot shadow

완료 기준:

- known_at snapshot 생성.
- 3m/5m/10m/30m feature 저장.
- KR/US threshold config 분리.
- look-ahead validator 테스트 통과.

### Step 5. Claude action schema shadow

완료 기준:

- 신규 prompt/response schema 적용.
- legacy fallback 작동.
- action TTL 부여.
- 실제 주문은 기존 경로 유지.

### Step 6. Gate Matrix shadow

완료 기준:

- hard/soft/timing/sizing 전체 gate vector 기록.
- 기존 skip reason과 gate blocker 비교.
- `judgment_not_executable` 같은 단일 사유에 묻히지 않음.

### Step 7. Routing canary

완료 기준:

- `PROBE_READY`만 제한 live/paper 주문 허용.
- PlanA/PathB lock 검증.
- PathB target 없는 wait는 주문 없음.
- rollback flag 확인.

### Step 8. Sizing contract

완료 기준:

- min_order override cap 재검증.
- high price 1주 정책 분리.
- cap trace 로그.
- probe stop weight 적용.

### Step 9. Exit lifecycle v2

완료 기준:

- hard stop/profit floor/trailing 우선순위 테스트.
- Claude HOLD override 금지 확인.
- partial sell은 flag off.

### Step 10. Future-blind replay

완료 기준:

- baseline_actual 비교.
- look-ahead 방지 테스트.
- false_probe/false_avoid 산출.
- replay 결과가 live policy를 자동 변경하지 않음.

### Step 11. Live rollout

완료 기준:

- KR paper 1일.
- US paper 1일.
- 소액/probe only canary.
- 운영 funnel summary가 장중 판단 가능 수준.

---

## 16. 내 의견

기존 방향을 갈아엎을 필요는 없다. 사용자가 처음 설계한 "Claude 후보 선정 -> PlanA/PathB 매수 -> 보유 중 Claude 재판단" 구조는 맞다. 오늘처럼 후보가 좋았는데 수익으로 연결되지 않은 이유는 철학이 틀려서가 아니라 연결 계층이 약해서다.

다만 지금 상태에서 단순히 조건을 완화하거나 Claude 호출을 늘리면 안 된다. 그렇게 하면 좋은 날에는 수익이 날 수 있지만 나쁜 날에는 왜 손실이 났는지 추적하기 어렵다.

내가 보는 정답은 다음이다.

```text
후보는 넓게 보존한다.
Claude 입력은 좁고 품질 좋게 만든다.
진입은 probe/buy/wait로 쪼갠다.
hard safety는 절대 우회하지 않는다.
soft/timing 조건은 기록하고 조절 가능하게 만든다.
매도는 system guard가 먼저 수익을 지킨다.
모든 개선은 known_at replay로 검증한다.
설정은 EffectiveRuntimeConfig로 운영 가능하게 만든다.
```

이 방향이면 욕심으로 무리하게 매매하는 구조가 아니라, 이미 찾고 있던 기회를 더 적절한 시점과 크기로 연결하는 구조가 된다.
