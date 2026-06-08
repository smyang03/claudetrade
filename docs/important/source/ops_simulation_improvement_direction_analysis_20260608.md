# Ops Simulation Improvement Direction Analysis

작성일: 2026-06-08

## 목적

기존 DB 기반 test simulation에서 나온 개선 후보가 실제 운영 개선 방향으로 맞는지 검토한다. 이 문서는 운영 로직 적용 전 분석 문서다. live 주문, PathB live engine, profit ladder, stop, broker truth, config/env는 변경하지 않았다.

## 2026-06-08 재검토 업데이트: live/enforce 관점

AGENTS 기준상 모든 설계/개선 작업은 기본적으로 `enforce`/`live` 적용을 전제로 계획한다. 따라서 이 문서의 기존 "report-only", "분석 queue" 표현은 최종 상태가 아니라 live 적용 전 검증 단계로 해석해야 한다.

수정된 결론:

- US high-confidence high-price 후보는 live 개선 방향이 맞다. 단, 전역 주문금액 증액이 아니라 기존 `PATHB_ALLOW_ONE_SHARE_OVER_BUDGET=true` 정책이 registration/submit sizing에서 실제로 작동하도록 복구/강화한다.
- 현재 live 설정은 `PATHB_FIXED_ORDER_KRW=450000`, `PATHB_ONE_SHARE_OVER_BUDGET_MAX_KRW=700000`, `PATHB_ONE_SHARE_OVER_BUDGET_MAX_ACCOUNT_PCT=30.0`이다. AVGO/ARM급 후보는 이 cap 안에 들어오므로 confidence/cash/broker/risk gate가 통과하면 live 진입 가능해야 한다.
- STRL/LITE급 후보는 1주 필요 금액이 약 122만~130만원으로 현재 cap을 넘는다. 이 그룹은 전역 budget 변경이 아니라 별도 high-confidence tier 후보로 설계해야 한다.
- KR `wait_30m`/`wait_60m`는 report-only가 최종 방향이 아니다. live 중 due time에 재평가하고, 기존 live evidence/routing/safety gate를 통과하면 진입하는 re-evaluation queue가 맞다.
- counterfactual row에서 직접 주문을 내는 것은 여전히 금지한다. live 적용은 반드시 기존 route, broker truth, ORDER_UNKNOWN, risk, affordability, PathB sizing guard를 통과해야 한다.

확인 사항:

- `PATHB_ALLOW_ONE_SHARE_OVER_BUDGET` 코드는 이미 존재한다. 문서의 "복구/강화"는 코드 부재가 아니라 live config 적용 여부와 gate별 실제 차단 위치 검증이 필요하다는 뜻이다.
- 다음 단계에서는 coverage audit을 먼저 보고, AVGO/ARM급 후보가 실제로 registration gate, submit sizing, early gate, cash, broker truth, ORDER_UNKNOWN 중 어디에서 block되는지 확인한다.
- AVGO/ARM급은 현재 700k KRW cap 안 후보로 먼저 live/enforce 복구 대상이다.
- STRL/LITE급은 cap 밖 후보로 별도 high-confidence tier 검토 대상이다.

즉 최종 우선순위는 `coverage audit -> US high-confidence one-share cap repair -> US high-confidence over-cap tier 검토 -> KR wait live re-evaluation queue`로 보정한다.

## 현재 반영 상태

### 반영 완료

| 카테고리 | 항목 | 전 | 후 | 운영 영향 |
|---|---|---|---|---|
| 버그/리포트 정확도 | `block_reasons` 집계 | 같은 case 안의 반복 `ENTRY_BLOCKED` 이벤트가 reason count를 과대 집계 | result 단위 unique reason으로 집계 | simulation report 정확도 개선, 주문 영향 없음 |
| 버그/리포트 정확도 | US KST 자정 이후 counterfactual window | US session_date와 KST trigger day가 달라질 때 `start > end`가 되어 price tape 누락 | `trigger_time`/`signal_time`의 KST local day end를 포함 | US simulation coverage 개선, 주문 영향 없음 |
| 시뮬레이션 기능 | DB 기반 batch 생성 | built-in scenario/tape 중심 | live DB read-only 추출 후 `.runtime` batch 생성 | live DB write 없음 |
| 시뮬레이션 기능 | 상대 sweep | 가격대가 다른 종목에 절대값 sweep 적용 | `buy_zone_padding_pct`, `target_price_mult`, `stop_price_mult`, `fixed_order_krw_mult` 지원 | simulation 전용 |
| 리포트 추적성 | report JSON | 파일 내부에 report/csv path 없음 | `report_path`, `csv_path` 저장 | 분석 추적성 개선 |

### 아직 미반영

| 카테고리 | 항목 | 상태 |
|---|---|---|
| 수익성 개선 | KR `wait_30m`/`wait_60m` follow-up queue | 후보 단계 |
| 수익성 개선 | missed/wait 계열 조건부 buy zone padding | 후보 단계 |
| 수익성 개선 | counterfactual 후보 target extension | 후보 단계 |
| 운영성 개선 | US high-price block 분리 리포트 | 후보 단계 |
| 운영성 개선 | price tape requested window coverage audit | 후보 단계 |
| 보호 영역 | US PathB live target/stop/profit ladder 변경 | 적용 금지, 별도 검증 필요 |

## 시뮬레이션 근거 요약

### 기준 실행

| 실행 | runs | avg_score | 주요 결과 |
|---|---:|---:|---|
| baseline replay | 80 | -0.8214 | 초기 기준선 |
| entry sweep | 2400 | 0.2326 | buy zone padding 효과 확인 |
| focused combo | 2160 | 0.8813 | padding 중심 조합 개선 |
| KR counterfactual wide | 7200 | 2.9195 | KR missed/wait 후보가 가장 강함 |
| US counterfactual wide v2 | 7200 | 0.9719 | US missed 후보도 가능성 있으나 high-price block 동반 |
| US PathB historical | 6156 | 0.8919 | live engine 변경 근거로는 불충분 |

### 시장별 핵심

| 시장/소스 | 핵심 결과 | 판단 |
|---|---|---|
| KR counterfactual | `wait_60m avg 6.2632`, `wait_30m avg 6.2152` | 방향 맞음. follow-up 분석/재평가 후보 |
| US counterfactual | `volume_surge avg 1.3574`, `pullback_reclaim avg 1.3238`, `HIGH_PRICE_BUDGET_BLOCK 927` | 가능성은 있으나 주문 후보보다 분석 queue 우선 |
| US PathB historical | `buy_zone_padding 0 avg 1.0151`, padding 확대는 평균 하락 | PathB live 기본 buy zone 확대 금지 |
| KR PathB historical | usable case 1건 | 판단 불가. price coverage 보강 필요 |

## 코드 흐름 검토

### counterfactual path는 이미 wait 경로를 지원한다

`runtime/counterfactual_paths.py`는 `wait_30m`, `wait_60m`를 공식 path로 생성한다. 즉 KR wait 후보를 새 개념으로 도입할 필요는 없다. 이미 후보 행 생성과 outcome 분석 축이 있다.

판단:

- 방향 맞음.
- 새 live 주문 경로를 만들기보다 기존 counterfactual path를 운영 분석 queue로 승격하는 것이 더 안전하다.

개선 방향:

- `wait_30m`/`wait_60m` 결과를 별도 report로 집계한다.
- live 주문으로 연결하지 않고, "follow-up candidate"로만 우선 노출한다.
- 충분한 표본과 운영자 승인 전에는 order submit과 연결하지 않는다.

재검토 보정:

- 위 문장은 "최종 report-only"가 아니라 "직접 주문 우회 금지"로 해석한다.
- 구현 방향은 live 재평가 queue다. due time에 후보를 다시 평가하고, 기존 live route/gate를 통과할 때만 주문 경로로 들어간다.

### outcome 계산도 wait 경로를 처리한다

`tools/update_counterfactual_outcomes.py`는 minute CSV에서 wait 경로 entry price를 추론하고, `outcome_30m_pct`, `outcome_60m_pct`, `max_runup_60m_pct`, `max_drawdown_60m_pct`를 채운다. 테스트도 `wait_30m` entry inference와 non-immediate outcome을 다룬다.

판단:

- 방향 맞음.
- KR wait follow-up은 이미 저장/성과계산 기반이 있으므로 구현 복잡도는 낮다.

개선 방향:

- DB writer를 새로 만들기보다 기존 `candidate_counterfactual_paths`를 활용한다.
- 추가로 필요한 것은 "좋은 wait 후보를 뽑는 리포트/큐"다.

### trading runtime은 counterfactual을 주문과 분리해 기록한다

`trading_bot.py`의 gate evaluation 흐름은 `COUNTERFACTUAL_PATHS_ENABLED`일 때 counterfactual rows를 기록한다. 이 기록은 `is_virtual_pnl=true`, `metadata_quality=runtime_authoritative` 성격이며 주문 자체가 아니다.

판단:

- 방향 맞음.
- "test로 수익성 개선점을 찾는 목적"과 잘 맞는다.

개선 방향:

- live 주문 로직이 아니라 counterfactual 분석/report 쪽을 먼저 확장한다.
- follow-up queue도 처음에는 report-only 또는 operator review queue로 구현한다.

재검토 보정:

- 초기 구현 산출물은 분석/report일 수 있지만 목표 상태는 live/enforce 재평가다.
- counterfactual row 자체가 주문 권한을 갖지 않고, live 재평가 입력으로만 쓰이면 AGENTS 계약과 맞다.

### routing/PathB와 직접 연결하면 보호 영역을 건드린다

`runtime/action_routing.py`와 `trading_bot.py::_apply_candidate_action_live_routes()`는 `PULLBACK_WAIT`를 PathB wait/live route와 연결한다. KR late gate, PathB shadow, broker truth, sizing과 이어지므로 이 경로를 직접 변경하면 보호 영역 리스크가 커진다.

판단:

- PathB live route 변경은 지금 개선 방향이 아니다.
- simulation 결과만으로 PathB buy zone, target, stop, profit ladder를 변경하면 안 된다.

개선 방향:

- PathB live 기본 동작은 유지한다.
- missed/wait 개선은 PathB route 변경이 아니라 selection/counterfactual analysis queue로 둔다.
- live 적용이 필요하면 별도 PathB-only 검증과 MD 위반 보고가 필요하다.

## 후보별 상세 판단

### 1. KR `wait_30m`/`wait_60m` follow-up queue

카테고리: 수익성 개선

시장: 한국장

근거:

- KR counterfactual wide에서 `wait_60m avg 6.2632`, `wait_30m avg 6.2152`.
- immediate, volume_surge, vwap_reclaim보다 worst가 상대적으로 안정적이다.
- 기존 counterfactual path와 outcome update 구조가 이미 wait 경로를 지원한다.

판단:

- 개선 방향이 맞다.
- 다만 "자동 주문 queue"가 아니라 "follow-up 분석/재평가 queue"가 맞다.

전:

- 좋은 후보가 즉시 trade_ready가 아니면 no_entry 또는 단순 counterfactual로 남는다.
- 운영자가 어떤 후보를 30~60분 후 다시 봐야 하는지 직접 찾기 어렵다.

후 방향:

- `wait_30m`/`wait_60m` 성과가 좋은 후보를 별도 report로 뽑는다.
- 다음 session/장중 운영에서 follow-up 대상 ticker, reason, expected window, entry reference를 표시한다.
- 주문 submit은 하지 않는다.

검증 방법:

- `candidate_counterfactual_paths`에서 KR `wait_30m`/`wait_60m` rows를 집계한다.
- path별 avg, median, worst, drawdown, source strategy를 포함한 report를 만든다.
- 1주 이상 live-read-only로 쌓인 결과와 비교한다.

### 2. missed/wait 계열 조건부 buy zone padding

카테고리: 수익성 개선

시장: 공통, 단 PathB live 제외

근거:

- KR counterfactual: padding 4 avg 5.0311, padding 3 avg 4.6763, padding 0 avg 0.5249.
- US counterfactual: padding 4 avg 1.6917, padding 0 avg 0.8578.
- US PathB historical: padding 0 avg 1.0151, padding 1 avg 0.8390, padding 2 avg 0.8217.

판단:

- counterfactual/missed/wait 후보에는 방향이 맞다.
- PathB live 기본 buy zone 확대는 방향이 틀리다.

전:

- replay/분석에서 buy zone이 좁으면 놓친 후보의 후속 진입 가능성을 과소평가한다.

후 방향:

- simulation/report에서만 conditional padding을 적용해 후보를 비교한다.
- live PathB plan의 buy_zone_low/high는 변경하지 않는다.

검증 방법:

- market, source, path_name별 padding 0/2/3/4 score를 분리한다.
- PathB live historical에는 padding 적용을 금지하는 guard/report rule을 둔다.

### 3. counterfactual target extension

카테고리: 수익성 개선 / 보류

시장: 공통, PathB live 제외

근거:

- KR counterfactual target 1.03 avg 3.4821.
- US counterfactual target 1.03 avg 1.9384.
- US PathB historical target 0.98 avg 1.2153, target 1.03 avg 0.6630.

판단:

- missed/counterfactual 후보 분석에는 방향이 맞다.
- live PathB target extension은 방향이 아니다.

전:

- target 후 run-up 신호는 보이지만, 어떤 source에 적용할지 분리되지 않았다.

후 방향:

- target extension은 counterfactual 후보 score 계산에만 사용한다.
- PathB target/profit ladder/pre-close는 유지한다.

검증 방법:

- `target exit left material run-up` 신호를 path/source별로 분리한다.
- PathB live close reason별 target extension 악화 여부를 별도 리포트로 확인한다.

### 4. US high-price block live/enforce 개선

카테고리: 운영성 개선 / 수익성 보조

시장: 미국장

근거:

- US counterfactual v2에서 `HIGH_PRICE_BUDGET_BLOCK 927`.
- fixed budget 증액 sweep은 1.0/1.25/1.5에서 score 차이가 없고, 0.75만 악화됐다.
- 최종 리포트 240건 기준 high-price block은 19건이며, AVGO/ARM은 현재 one-share cap 안에 들어오는 후보로 확인됐다.
- STRL/LITE는 현재 cap을 넘는 초고가 후보로 분리해야 한다.

판단:

- 전역 예산을 늘리는 방향은 맞지 않다.
- 기존 `PATHB_ALLOW_ONE_SHARE_OVER_BUDGET` live 정책이 고확신 후보에 실제 적용되도록 복구/강화하는 방향이 맞다.
- 현재 cap 안의 AVGO/ARM급 후보는 live/enforce 개선 대상이다.
- 현재 cap 밖의 STRL/LITE급 후보는 별도 high-confidence tier 설계 대상이다.

전:

- 고가주 후보가 수익성 후보인지 예산 불가 후보인지 분리해서 보기 어렵다.
- 설정상 one-share over-budget이 켜져 있어도 registration/submit gate 중 한 지점에서 먼저 `HIGH_PRICE_BUDGET_BLOCK`으로 잘릴 수 있다.

후 방향:

- PathB registration gate와 submit sizing이 같은 max-entry 정책을 쓰는지 검증한다.
- high-confidence 후보가 `PATHB_ONE_SHARE_OVER_BUDGET_MAX_KRW`와 account pct cap 안이면 plan registration과 submit sizing을 통과할 수 있어야 한다.
- 초고가 후보는 별도 `PATHB_HIGH_CONF_ONE_SHARE_*` tier 후보로 분리한다.
- 자동 주문 예산 증액이나 전역 fixed budget 증액은 하지 않는다.

검증 방법:

- `HIGH_PRICE_BUDGET_BLOCK` rows를 ticker/source/path별로 집계한다.
- 실제 filled PathB high-price 후보와 missed high-price 후보를 비교한다.
- `tests/test_pathb_runtime.py`와 `tests/test_live_order_safety.py`에서 registration gate와 submit sizing의 one-share cap 일관성을 검증한다.
- live config effective value에 `PATHB_ALLOW_ONE_SHARE_OVER_BUDGET=true`, `PATHB_ONE_SHARE_OVER_BUDGET_MAX_KRW=700000`이 들어오는지 확인한다.
- AVGO/ARM급 실제 blocked row를 registration gate, submit sizing, early gate, cash, broker truth, ORDER_UNKNOWN으로 stage 분류한다.

### 5. price tape requested-window coverage audit

카테고리: 버그/운영성 개선

시장: 공통

근거:

- US KST 자정 이후 window bug 수정으로 US skipped_count 20 -> 0.
- KR PathB 027360은 파일은 있으나 필요한 session window row가 없어 skip됐다.

판단:

- 개선 방향이 맞다.
- 파일 존재 여부가 아니라 requested time window coverage를 audit해야 한다.

전:

- `price_tape_missing`이 파일 없음인지, 시간 범위 누락인지 알기 어렵다.

후 방향:

- batch builder report에 requested_start, requested_end, actual_min_ts, actual_max_ts, matched_rows를 기록한다.
- skip reason을 `price_file_missing`, `price_window_missing`, `price_rows_empty_after_filter`로 분리한다.

검증 방법:

- AVGO/NVTS/ABVX/ONDS/IREN US 자정 케이스가 skip 0으로 유지되는지 확인한다.
- KR 027360처럼 파일은 있지만 window가 빠진 case를 별도로 보고한다.

## 구현 우선순위

### 1순위: price tape coverage audit

이유:

- 분석 신뢰도를 높이는 기반 작업이다.
- live 주문과 무관하고 보호 영역 영향이 없다.
- 이미 US window bug를 찾았으므로 추가 결함을 발견할 가능성이 높다.

예정 산출물:

- `tools/ops_build_simulation_batch.py` skip diagnostics 확장
- `tests/test_ops_build_simulation_batch.py` window coverage 테스트
- `.runtime` report에 coverage section 추가

### 2순위: KR wait follow-up analysis report

이유:

- 수익성 개선 후보 중 근거가 가장 강하다.
- 기존 counterfactual path와 outcome update 구조를 활용할 수 있다.
- 주문 없이 report-only로 시작 가능하다.
- 단, 최종 목표는 live re-evaluation queue다. due time에 기존 live route/gate를 다시 태우는 구조로 가야 한다.

예정 산출물:

- `tools/ops_followup_candidates.py` 또는 기존 analysis tool 확장
- KR `wait_30m`/`wait_60m` 후보 CSV/MD
- path/source/strategy별 avg, median, worst, MFE/MAE report

### 3순위: US high-price block live repair

이유:

- US missed 후보에서 반복 차단이 보인다.
- 기존 one-share over-budget 설정이 live에서 실제로 살지 않으면 고확신 고가 후보를 계속 놓친다.
- cap 안 후보와 cap 밖 후보를 분리해야 한다.

예정 산출물:

- PathB registration gate / submit sizing one-share cap 일관성 테스트
- AVGO/ARM급 cap 안 후보 live/enforce 복구
- STRL/LITE급 over-cap 후보 별도 tier 요구서
- required budget, current fixed budget, max cap, confidence, broker/risk gate 상태 summary

## 적용 금지 항목

| 항목 | 이유 |
|---|---|
| US PathB buy zone 전역 확대 | PathB historical에서 평균 하락 |
| US PathB target/profit ladder/pre-close 변경 | 보호된 수익 엔진, simulation 단순화 가능성 |
| stop 직접 완화/강화 | risk/protection 경로와 연결, 별도 검증 필요 |
| fixed order budget 전역 증액 | simulation상 개선 근거 약함 |
| counterfactual row 직접 주문 | live 오염/주문 우회 위험. 반드시 기존 live route/gate 재평가를 통과해야 함 |
| broker truth/ORDER_UNKNOWN/risk/sizing guard 우회 | live 안전 계약 위반 |

## 결론

진짜 개선 방향으로 맞는 것은 다음 세 가지다.

1. price tape requested-window coverage audit를 먼저 보강한다.
2. US 고확신 고가 후보는 기존 one-share-over-budget 정책을 live/enforce로 복구한다. AVGO/ARM급 cap 안 후보가 우선이다.
3. STRL/LITE급 over-cap 후보는 전역 예산 증액이 아니라 별도 high-confidence tier 후보로 설계한다.
4. KR `wait_30m`/`wait_60m`는 자동 주문이 아니라 live re-evaluation queue로 구현한다. due time에 기존 live route/gate를 다시 통과해야 한다.

따라서 다음 구현은 `coverage audit -> US high-confidence one-share cap repair -> US over-cap high-confidence tier 설계 -> KR wait live re-evaluation queue` 순서가 맞다. 첫 번째 코드 작업은 새 정책 추가가 아니라 existing one-share-over-budget 정책의 effective config와 blocker stage를 확인하는 audit/테스트여야 한다.
