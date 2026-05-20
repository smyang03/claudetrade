# KR PathB Shadow Registration Plan - 2026-05-20

## 목적

KR PathB는 현재 운영 기본값이 live off이기 때문에 `PATHB_KR_LIVE_ENABLED=false` 상태에서는 실주문뿐 아니라 `v2_path_runs` 검증 기록도 같이 사라진다. 이 상태가 유지되면 한국장 PathB는 live를 켜기 전까지 성과 검증 표본을 만들 수 없다.

목표는 KR 실주문을 켜는 것이 아니라, 실주문은 계속 차단한 채 PathB 가격 계획, 대기, hit, outcome을 shadow 데이터로 남기는 것이다. 핵심 판단 기준은 "한국장 live ON"이 아니라 "live OFF 상태에서도 검증 DB가 쌓이는가"이다.

## 비목표

- KR PathB 실주문 활성화는 이번 단계의 목표가 아니다.
- `state/brain.json` 정책 메모리를 자동 변경하지 않는다.
- shadow 성과를 live PnL, 브로커 truth, regular sizing 승격 근거와 섞지 않는다.
- 표본 50건 이상과 데이터 품질 기준 통과 전에는 KR PathB live 승격을 하지 않는다.

## 현재 코드 검증/하드닝 필요점

1. `runtime/pathb_runtime.py::PathBRuntime.register_from_selection_meta()`
   - `PATHB_KR_LIVE_ENABLED=false`에서도 `PATHB_KR_SHADOW_PLAN_ENABLED=true`이면 `SHADOW_WAITING` plan을 만들 수 있다.
   - 남은 핵심 검증은 shadow plan이 어떤 조건에서도 주문 제출로 이어지지 않는 fail-closed 보장이다.

2. `runtime/pathb_runtime.py::scan_waiting_entries()`
   - live disabled + shadow enabled 상태에서는 `_scan_shadow_waiting_entries()`가 가격대 hit를 관찰한다.
   - shadow hit는 신규 이벤트 타입이 아니라 `CLAUDE_PRICE_HIT` + `path_status=SHADOW_HIT` + `reason_code=shadow_buy_zone_hit`로 구분한다.

3. `trading_bot.py` candidate action routing
   - `_pathb_wait_tickers`, `_pathb_price_targets`, `_pathb_wait_origins`, `_pathb_registration_scope`와 별도로 `_pathb_shadow_*` metadata를 보존한다.
   - `BUY_READY -> WATCH reason=kr_data_quality_not_confirmed`, `PROBE_READY`, `PULLBACK_WAIT` 같은 후보가 실주문 없이 shadow 검증 대상으로 남는지 회귀 테스트가 필요하다.

4. `runtime/counterfactual_paths.py`와 `tools/update_counterfactual_outcomes.py`
   - `immediate`, `wait_30m`, `wait_60m`, `vwap_reclaim`, `pullback_reclaim`, `or_break`, `volume_surge`, `vi_safe_reclaim`, `orderbook_support`별 outcome 비교를 운영 승격 기준으로 고정해야 한다.
   - KR microstructure, VI, orderbook, minute 데이터가 없으면 `DATA_MISSING`이 누적되어 조건부 진입 우위를 판단할 수 없다.

## 추가 플랜 항목

| 단계 | 항목 | 왜 하는가 | 완료 조건 |
| --- | --- | --- | --- |
| P1-10 | KR PathB shadow registration 분리 | KR live off에서도 PathB 검증 표본을 만들기 위해 registration과 broker execution을 분리한다. | `PATHB_KR_LIVE_ENABLED=false` 상태에서 shadow `v2_path_runs` 생성, `ORDER_SENT` 없음, lifecycle에 `CLAUDE_PRICE_PLAN_CREATED`, `CLAUDE_PRICE_WAITING`, `CLAUDE_PRICE_HIT` + `path_status=SHADOW_HIT` 기록 |
| P1-11 | KR PathB conditional outcome 라벨 복구 | 즉시추격과 조건부 진입을 비교하려면 `DATA_MISSING`을 줄이고 entry 형태별 outcome을 채워야 한다. | `candidate_counterfactual_paths` KR `DATA_MISSING` 비율 20% 이하, `outcome_60m_pct`와 `outcome_close_pct` backfill 가능 |
| P2-8 | KR PathB reclaim/pullback shadow 실험 | 급등주 즉시 진입 대신 눌림/재돌파 조건이 실제로 우위인지 검증한다. | shadow hit 50건 이상, `avg_60m > 0`, `avg_close >= 0`, win rate 45% 이상, worst tail -5% 이내, immediate 대비 우위 확인 |
| P2-9 | KR 전용 reward/risk shadow gate | 상한가/VI/호가 공백이 많은 KR에서 일반 RR 기준을 그대로 쓰면 목표가가 과대평가될 수 있다. | 상한가 근접 target 금지, stop은 VWAP 이탈/OR low/직전 눌림 저점 중 가까운 값, RR 1.5 미만은 shadow only |

## 코드 레벨 작업 계획

### 1. PathB registration과 execution 분리

- 파일: `runtime/pathb_runtime.py`
- 대상 함수: `register_from_selection_meta()`, `_market_live_enabled()`, `scan_waiting_entries()`, `_submit_buy()`
- 변경 방향:
  - `PATHB_KR_LIVE_ENABLED=false`가 registration을 막지 않도록 별도 gate를 둔다.
  - env: `PATHB_KR_SHADOW_PLAN_ENABLED=true` 또는 legacy 호환 `KR_CLAUDE_PRICE_SHADOW_PLAN_ENABLED=true`
  - live disabled + shadow enabled이면 plan을 생성하되 plan metadata에 아래 값을 저장한다.

```json
{
  "shadow_only": true,
  "shadow_reason": "market_live_disabled",
  "live_order_enabled": false,
  "execution_allowed": false,
  "origin_action": "PULLBACK_WAIT",
  "origin_reason": "kr_data_quality_not_confirmed",
  "demoted_from": "BUY_READY",
  "demotion_reason": "kr_data_quality_not_confirmed",
  "pathb_shadow_reason": "kr_data_quality_not_confirmed",
  "registration_scope": "candidate_actions_wait_only"
}
```

- 안전 조건:
  - `plan.shadow_only == true`이면 `_submit_buy()`는 무조건 return한다.
  - `_market_live_enabled("KR") == false`이면 `_submit_buy()`는 무조건 return한다.
  - shadow hit는 주문 대신 `CLAUDE_PRICE_HIT` + `path_status=SHADOW_HIT` lifecycle만 기록한다.

### 2. KR candidate action을 shadow plan 대상으로 보존

- 파일: `trading_bot.py`
- 대상 데이터: `_pathb_wait_tickers`, `_pathb_price_targets`, `_pathb_wait_origins`, `_candidate_action_routes`
- 매핑 정책:
  - `PULLBACK_WAIT`: PathB wait plan 생성 대상이다.
  - `BUY_READY`: KR data quality가 확인되지 않으면 live 후보가 아니라 shadow plan 후보로 먼저 저장한다.
  - `PROBE_READY`: live 금지, shadow 또는 별도 micro-probe 후보로 저장한다.
  - `WATCH`: 일반 watch와 PathB wait 가능 watch를 분리한다. price target 또는 reclaim 조건이 있는 watch만 shadow plan 대상으로 삼는다.
  - `AVOID`: PathB plan 생성 금지.
- 보존해야 할 metadata:
  - `origin_action`
  - `origin_route`
  - `origin_reason`
  - `demoted_from`
  - `demotion_reason`
  - `microstructure_data_quality`
  - `pathb_shadow_reason`

### 3. 조건부 entry label을 counterfactual DB에 남김

- 파일: `runtime/counterfactual_paths.py`
- 파일: `audit/candidate_counterfactual_store.py`
- 파일: `tools/update_counterfactual_outcomes.py`
- 파일: `tools/analyze_counterfactual_paths.py`
- 라벨 대상:
  - `immediate`
  - `wait_30m`
  - `wait_60m`
  - `vwap_reclaim`
  - `pullback_reclaim`
  - `or_break`
  - `volume_surge`
  - `vi_safe_reclaim`
  - `orderbook_support`
- 저장 원칙:
  - `actual_path`와 `path_name`을 분리한다.
  - shadow plan row는 `metadata_quality=runtime_authoritative` 또는 명시적 shadow 품질 값을 갖는다.
  - live PnL 계산에서는 shadow row를 제외한다.
  - `DATA_MISSING`은 단순 실패로 보지 않고 `missing_features`에 원인을 저장한다.

### 4. KIS microstructure/MFE를 PathB 선행조건으로 승격

- 파일: `trading_bot.py`
- 대상 함수: `_kr_microstructure_context()`와 KR confirmation gate 주변 로직
- 필요한 입력:
  - VI state와 VI data quality
  - orderbook spread와 호가 공백
  - opening range high/low
  - VWAP
  - minute high/low
  - volume surge
  - MFE/MAE
- 정책:
  - 위 데이터가 degraded이면 KR PathB 실주문에는 영향이 없어야 한다.
  - degraded 상태에서도 shadow plan과 `DATA_MISSING` 원인 기록은 허용한다.
  - live 승격 전제 조건은 `microstructure_data_quality=OK` 비율과 `DATA_MISSING` 비율로 판단한다.

### 5. 분석 기준 고정

- 파일: `tools/analyze_counterfactual_paths.py`
- 비교 축:
  - immediate 대비 `vwap_reclaim`
  - immediate 대비 `pullback_reclaim`
  - immediate 대비 `or_break`
  - immediate 대비 `wait_30m`, `wait_60m`
- 최소 승격 조건:
  - shadow hit 50건 이상
  - `DATA_MISSING <= 20%`
  - `avg_60m > 0`
  - `avg_close >= 0`
  - win rate 45% 이상
  - worst tail -5% 이내
  - immediate 대비 평균 outcome 개선

## 운영 리스크와 대응

| 리스크 | 운영상 문제 | 대응 |
| --- | --- | --- |
| shadow registration이 주문 경로를 우회 | KR live off인데 실제 `ORDER_SENT`가 발생할 수 있다. | `_submit_buy()`와 order adapter 직전에 `shadow_only`와 `_market_live_enabled()` 이중 가드, 테스트에서 broker order mock 미호출 검증 |
| shadow와 live 성과 혼합 | PathB가 실제보다 좋아 보이거나 나빠 보일 수 있다. | `shadow_only`, `execution_allowed`, `label_source`, `metadata_quality`를 필수 저장하고 live performance 집계에서 제외 |
| 중복 path run 증가 | 같은 후보가 여러 번 등록되어 hit/outcome이 부풀 수 있다. | `session_date`, `market`, `ticker`, `known_at`, `path_name`, `registration_scope` 기준 idempotency 유지 |
| `DATA_MISSING` 과다 | 조건부 진입 우위 판단이 불가능하다. | `missing_features`를 구조화하고, KIS minute/orderbook/VI 수집 품질을 P1 선행조건으로 둔다. |
| KIS rate limit 증가 | shadow scan이 호가/분봉 호출을 늘릴 수 있다. | TTL cache, batch fetch, scan interval, max shadow candidates per session 제한 |
| 오래된 WAITING shadow 누적 | 대시보드와 운영 판단에 노이즈가 생긴다. | `cancel_waiting()`/`cancel_waiting_for_ticker()`와 session close expire에서 `SHADOW_WAITING`, `SHADOW_HIT`을 `SHADOW_CANCELLED`로 정리 |
| 표본 과적합 | 몇 건의 급등주 결과로 live를 켤 위험이 있다. | 최소 50 hit, 여러 세션 분산, worst tail 기준, immediate 대비 비교를 모두 통과해야 승격 |

## 기대 장점

- KR live를 켜지 않고도 PathB 검증 DB가 쌓인다.
- 즉시추격 진입과 눌림/재돌파 조건 진입의 성과 차이를 숫자로 비교할 수 있다.
- `BUY_READY -> WATCH` demotion이 버려지지 않고 shadow evidence로 남는다.
- selection 품질 문제와 microstructure 데이터 품질 문제를 분리해서 판단할 수 있다.
- live 승격 전 micro live probe 여부를 근거 기반으로 결정할 수 있다.

## 단계별 검증

1. 단위 테스트

```powershell
python -m pytest tests/test_pathb_runtime.py tests/test_candidate_action_live_mapping.py -q
```

필수 추가 케이스:
- `PATHB_KR_LIVE_ENABLED=false`, `PATHB_KR_SHADOW_PLAN_ENABLED=true`에서 KR shadow `v2_path_runs` 생성
- 같은 조건에서 broker order submit 미호출
- price zone hit 시 `CLAUDE_PRICE_HIT` + `path_status=SHADOW_HIT` 기록
- `PULLBACK_WAIT`, demoted `BUY_READY`, `PROBE_READY`의 origin metadata 보존

2. Counterfactual 테스트

```powershell
python -m pytest tests/test_counterfactual_paths.py tests/test_update_counterfactual_outcomes.py -q
```

필수 추가 케이스:
- KR conditional paths가 `immediate`, `wait_30m`, `wait_60m`, `vwap_reclaim`, `pullback_reclaim`, `or_break`, `volume_surge`, `vi_safe_reclaim`을 생성
- `DATA_MISSING`이면 `missing_features`가 채워짐
- retry/backfill 후 기존 outcome을 덮어쓰지 않고 누락 close/minute label만 채움

3. 운영 smoke

```powershell
$env:PATHB_KR_LIVE_ENABLED="false"
$env:PATHB_KR_SHADOW_PLAN_ENABLED="true"
python -m py_compile trading_bot.py runtime/pathb_runtime.py runtime/counterfactual_paths.py
```

확인 항목:
- `v2_path_runs`에 KR shadow plan 생성
- `ORDER_SENT`, `ORDER_ACKED`, `FILLED` 이벤트 없음
- `CLAUDE_PRICE_PLAN_CREATED`, `CLAUDE_PRICE_WAITING`, `CLAUDE_PRICE_HIT` + `path_status=SHADOW_HIT` 기록
- `candidate_counterfactual_paths`의 KR `DATA_MISSING` 비율 산출 가능

## 추천 실행 순서

1. `register_from_selection_meta()`에서 KR shadow registration을 live flag와 분리한다.
2. `scan_waiting_entries()`에서 live disabled shadow plan은 cancel하지 않고 hit만 기록하게 한다.
3. `_submit_buy()`에 `shadow_only`와 `_market_live_enabled()` fail-closed 가드를 추가한다.
4. `trading_bot.py`에서 demoted `BUY_READY`, `PROBE_READY`, `PULLBACK_WAIT`를 shadow plan metadata로 보존한다.
5. `candidate_counterfactual_paths`의 KR `DATA_MISSING` 원인을 구조화하고 backfill 경로를 보강한다.
6. conditional entry별 성과 리포트를 만든다.
7. shadow hit 50건 이상과 승격 조건 통과 후에만 KR micro live probe를 별도 승인한다.
