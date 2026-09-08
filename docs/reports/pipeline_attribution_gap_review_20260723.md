# Pipeline Attribution Gap Review 2026-07-23

## 목적

`candidate → v2_decision → lifecycle/order/fill → canonical performance → candidate audit` 체인이 실제 DB에서 일관되게 이어지는지 검증했다. 이번 검토는 매매 전략 품질이 아니라, 체결 결과가 후보 row에 올바르게 귀속되는지에 집중했다.

사용한 감사 도구:

- `tools/pipeline_attribution_audit.py`
- 결과: `docs/reports/pipeline_attribution_audit_20260723.md`
- JSON: `docs/reports/pipeline_attribution_audit_20260723.json`

## 실측 요약

기간: `2026-07-01` 이후

| 항목 | 값 |
|---|---:|
| canonical filled | 7 |
| lifecycle `FILLED` events | 12 |
| lifecycle unique filled decisions | 9 |
| candidate rows with `execution_decision_id` | 505 |
| candidate unique execution decisions | 99 |
| candidate filled rows among linked rows | 3 |

분류:

| 분류 | 건수 | 의미 |
|---|---:|---|
| `correct_candidate_fill` | 1 | fill이 올바른 후보 route row에 붙음 |
| `wrong_row_filled_correct_row_unfilled` | 1 | 올바른 PathB row가 있는데 다른 row에 fill이 붙음 |
| `wrong_row_filled_no_correct_row` | 1 | candidate에는 같은 decision이 있으나 fill이 WATCH row에 붙음 |
| `external_strategy_no_candidate_expected` | 4 | candidate pipeline 외부 전략 fill로 보이며 candidate row 없음 |

## 결론

candidate pipeline에 해당하는 PathB fill 3건 중 1건만 정확히 붙었다. 2건은 candidate audit attribution이 오염됐다.

- KR 003490: 정상
- US IREN: 잘못된 row에 fill
- US NVDA: 잘못된 row에 fill

따라서 현재 candidate audit의 `filled_count`, `first_fill_at`, `entry_price`, `pnl_pct`를 그대로 학습/개선 판단 기준으로 쓰면 안 된다. 특히 “어떤 Claude action/route가 실제 체결을 만들었는가”가 IREN/NVDA에서 왜곡된다.

## 케이스 상세

### 1. US IREN — 잘못된 row에 fill

canonical:

- decision: `dec_20260702_US_IREN_a1e6805b`
- route: `path_b`
- path_type: `claude_price`
- origin_action: `PULLBACK_WAIT`
- first fill event: `10927`
- fill: `2026-07-02T14:21:09+00:00`
- entry: `42.09`
- net pnl: `-2.6383`

candidate audit:

- linked candidate rows: 11
- correct PathB candidate rows: 6
- filled candidate rows: 1
- wrong filled rows: 1
- no-submit과 fill이 같은 row에 공존: 1

문제:

- 실제 체결은 PathB `PULLBACK_WAIT/PathB.wait`
- candidate audit에서 fill이 붙은 row는 `PROBE_READY/PlanA.probe`
- 해당 row에는 `no_submit_reason_code=NO_SIGNAL`도 같이 남아 있음

즉 같은 candidate row가 “NO_SIGNAL으로 주문 안 됨”과 “FILLED 됨”을 동시에 나타낸다. 이 row는 학습/리포트 기준으로 사용할 수 없다.

### 2. KR 003490 — 정상에 가까움

canonical:

- decision: `dec_20260703_KR_003490_091a0409`
- route: `path_b`
- path_type: `claude_price`
- origin_action: `PULLBACK_WAIT`
- fill event: `11103`
- entry: `28850`
- net pnl: `0.8299`

candidate audit:

- `PULLBACK_WAIT/PathB.wait` row에 `filled_count=1`
- attribution 정상

단, 같은 decision이 이후 WATCH/blank row에도 `entry_price` 또는 `pnl_pct`로 일부 전파된다. 이건 성과 attribution 자체보다 후속 row 오염 가능성으로 별도 관리가 필요하다.

### 3. US NVDA — WATCH row에 fill

canonical:

- decision: `dec_20260706_US_NVDA_b4e1445e`
- route: `path_b`
- path_type: `claude_price`
- origin_action: `PULLBACK_WAIT`
- fill event: `11199`
- entry: `195.9989`
- net pnl: `-2.4127`

candidate audit:

- linked candidate rows: 3
- correct PathB candidate row: 0
- filled candidate row: 1
- filled row: `WATCH/WATCH`, reason=`selection_quality:us_midrange_momentum_trap`

문제:

- 실제 체결은 PathB인데 candidate audit에는 PathB executable row가 연결되어 있지 않다.
- fill은 WATCH row에 붙었다.
- 이 경우 candidate audit만 보면 WATCH가 체결된 것처럼 보인다.

### 4. SCHG / 275280 / 275300 — candidate 외부 전략

아래 4건은 canonical filled지만 candidate prompt row가 없다.

- `dec_20260715_US_SCHG_af10491b`
- `dec_20260716_US_SCHG_d0066321`
- `dec_20260720_KR_275280_de073acc`
- `dec_20260721_KR_275300_584be5e3`

strategy:

- `us_schg_bil_trend_v1`
- `kr_factor_trend_v1`

판정:

- candidate pipeline 외부 전략으로 보이므로 candidate audit 미연결 자체는 오류로 보지 않는다.
- 단, 전체 수익 리포트에서는 candidate pipeline 성과와 sleeve/core strategy 성과를 분리해야 한다.

## lifecycle 중복 fill 이벤트

`FILLED` lifecycle event는 12개지만 unique filled decision은 9개다.

중복 그룹:

- IREN: event `10927`, `10928`
- 003490: event `11103`, `11104`
- NVDA: event `11199`, `11200`

각각 동일 decision/execution/occurred_at에 대해 `FILLED`가 2번 남았다. canonical은 dedupe하는 것으로 보이나 raw lifecycle 품질 문제다.

## canonical sync 지연

lifecycle에는 fill이 있지만 canonical에 아직 반영되지 않은 decision:

- `dec_20260722_KR_275280_9e861b32`
- `dec_20260722_KR_275300_b3ea79d9`

2026-07-22 KR fill 2건은 lifecycle에는 존재하지만 `v2_canonical_performance`에는 아직 filled로 안 들어왔다. 실시간 리포트에서는 canonical sync freshness를 별도 표시해야 한다.

## 코드 레벨 원인 후보

### 1. live candidate audit update가 `FILLED` event를 직접 처리하지 않음

`trading_bot.py:39947`의 `_candidate_audit_update_from_decision_event()`는 다음 action만 candidate audit에 반영한다.

- `buy_order`
- `buy_signal`
- `sell_filled`
- `sell_executed`

하지만 실제 체결은 `FILLED` lifecycle event로 남는다.

체결 발생 지점 예:

- `trading_bot.py:25165` — polling/reconcile fill
- `trading_bot.py:31905` — websocket fill
- `runtime/pathb_runtime.py:12694` — PathB fill event

따라서 live 경로에서 `FILLED`가 candidate audit의 `filled_count/first_fill_at/execution_event_id`로 직접 업데이트되지 않고, 사후 backfill 또는 ticker latest-row 업데이트에 의존한다.

### 2. `latest_only=True` 업데이트가 원인 row가 아니라 최신 row를 덮음

`audit/candidate_audit_store.py:1486`의 `update_execution_by_ticker(... latest_only=True)`는 session/market/ticker의 최신 row 하나를 업데이트한다.

이 방식은 다음 상황에서 오염된다.

- 같은 종목이 여러 cycle에 반복 등장
- 최초 executable row는 `PULLBACK_WAIT/PathB.wait`
- 이후 WATCH/NO_SIGNAL row가 더 최신으로 생성
- fill 업데이트가 최신 row에 붙음

NVDA가 이 패턴이다.

### 3. 기존 backfill 도구가 route 일치 검증을 하지 않음

`tools/backfill_candidate_fill_attribution.py:64`의 `pick_target_row()`는 “최초 executable row”를 고르지만, canonical fill의 route/path/origin과 candidate row의 route/action이 맞는지 검증하지 않는다.

실제 dry-run:

- canonical filled 7
- `no_candidate_row`: 4
- `execution_decision_id`: 2
- `execution_decision_id+no_executable_row`: 1
- 갱신 대상: 0

하지만 IREN/NVDA는 이미 `filled_count`가 다른 row에 붙어 있어서 “갱신 대상 0”으로 끝난다. 즉 misattribution을 고치지 못한다.

## 개선 필요 여부

수정 필요. 이유는 명확하다.

1. 체결 row가 잘못 붙으면 candidate action별 성과가 오염된다.
2. PathB가 손실/수익을 냈는데 PlanA/NO_SIGNAL/WATCH row가 성과를 가져간다.
3. 이후 학습이 “어떤 판단이 돈을 벌었는지/잃었는지”를 잘못 배운다.
4. 운영 리포트에서 “Claude가 사라고 했는데 안 샀다/샀다” 판단이 흔들린다.

## 권장 수정안

### P0. fill attribution target resolver 추가

`update_execution_by_ticker(latest_only=True)`로 fill을 붙이지 말고, fill event 기준으로 target candidate row를 결정해야 한다.

우선순위:

1. `execution_decision_id` 일치
2. canonical/event `path_run_id` 일치
3. canonical route/path_type 기준 candidate route/action 일치
   - `path_b/claude_price/PULLBACK_WAIT` → `PULLBACK_WAIT/PathB.wait`
   - `path_a` → `PlanA.buy` 또는 `PlanA.probe`
4. fill 시각 이전의 가장 가까운 executable row
5. 없으면 `candidate_fill_attribution_status=unresolved`로 별도 표시

### P0. `FILLED` lifecycle event를 candidate audit에 직접 반영

`_candidate_audit_update_from_decision_event()` 또는 별도 handler가 `FILLED`/`PARTIAL_FILLED`를 처리해야 한다.

저장 필드:

- `filled_count=1`
- `first_fill_at`
- `entry_price`
- `execution_event_id`
- `execution_decision_id`
- `execution_link_source='v2_event_store.lifecycle_fill'`
- `payload_json.fill_attribution`

### P1. 기존 오염 row 교정 backfill

기존 `backfill_candidate_fill_attribution.py`를 확장한다.

추가해야 할 검증:

- 이미 `filled_count>0`이어도 route/action mismatch면 교정 대상
- no-submit row에 fill이 붙은 경우 교정 대상
- WATCH row에 PathB fill이 붙은 경우 교정 대상
- 기존 wrong row는 `filled_count=0`으로 되돌리거나 `fill_attribution_superseded=true` 표시

### P1. lifecycle `FILLED` dedupe

동일 `(decision_id, execution_id, occurred_at)`의 `FILLED` event가 2개 생기는 경로를 정리한다.

최소한 canonical sync와 candidate audit update는 deduped first fill event만 사용해야 한다.

### P2. candidate 외부 전략 분리

`us_schg_bil_trend_v1`, `kr_factor_trend_v1` 같은 sleeve/core strategy는 candidate audit과 별도 attribution namespace를 가져야 한다.

추천 필드:

- `strategy_family=candidate_pipeline|core_sleeve|factor_trend`
- `candidate_scope=prompt_candidate|external_strategy`

## 다음 단계

수정에 들어간다면 순서는 다음이 맞다.

1. `pipeline_attribution_audit.py` 기준으로 현재 실패 케이스 고정
2. fill target resolver 구현
3. live `FILLED` handler에서 resolver 사용
4. 기존 backfill 도구에 mismatch correction 추가
5. IREN/NVDA/003490 fixture 테스트 추가
6. live DB는 dry-run 리포트 확인 후 교정 적용

