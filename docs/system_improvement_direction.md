# 자동매매 시스템 최종 개선 방향과 추가 설계 포인트

작성일: 2026-06-07 KST  
입력 문서: `docs/system_analysis_input_template.md`  
종합 대상: 현재 흐름/수익률 분석, 부정적 비판, 긍정적 평가  
수정 범위: 문서 작성만 수행. 코드/config/state/DB는 변경하지 않음.

## 1. 최종 결론

지금 시스템의 개선 방향은 "더 공격적인 매매"가 아니다.

정확한 방향은 다음이다.

```text
US PathB 수익 엔진 보호
  + 성과 ledger 신뢰도 복구
  + KR no-signal 원인 분해
  + selection / execution / broker truth / risk 문제 분리
  + dashboard/report 가시성 강화
  + 샘플이 부족한 것은 shadow가 아니라 read-only 분석부터
```

현재 시스템은 US PathB `claude_price`에서 실제 수익축을 보여준다. 반면 KR은 live closed 성과가 부진하고 최근 `trade_ready -> signal_fired`가 끊겨 있다. 따라서 US 수익 경로를 건드리지 않고, KR과 reporting/ledger를 좁게 고쳐야 한다.

## 2. 의사결정 원칙

앞으로 개선 작업은 아래 원칙을 따른다.

| 원칙 | 의미 |
|---|---|
| US PathB 보호 | `target`, `pre-close`, `profit_ladder`, broker truth, sizing, cooldown을 함부로 변경하지 않는다. |
| KR/US 분리 | 같은 strategy 이름이라도 시장별 성과가 다르므로 같은 패치로 공유 전략을 바꾸지 않는다. |
| selection/execution/risk 분리 | 후보 품질 문제를 risk gate 완화로 풀지 않는다. execution miss를 Claude prompt 문제로 덮지 않는다. |
| ledger first | 성과 DB가 믿을 수 없으면 전략 판단도 믿을 수 없다. |
| live/enforce 전제 | 확실한 운영/리포트/DB 보정은 enforce/live 기준으로 설계한다. |
| shadow 예외 제한 | 행동 자체가 불확실하거나 sample gate가 부족할 때만 shadow를 쓴다. |
| broker truth 우선 | holdings/open orders/fills가 local cache나 AI 판단보다 우선이다. |

## 3. 유지 / 강화 / 버릴 것

### 3.1 유지할 것

| 항목 | 이유 |
|---|---|
| US PathB `claude_price` | 현재 가장 뚜렷한 수익축 |
| `CLOSED_CLAUDE_PRICE_TARGET` | US 평균 +5.311%, 승률 100% |
| `CLOSED_CLAUDE_PRICE_PRE_CLOSE` | US 평균 +1.442%, 장마감 리스크 제어 |
| `CLOSED_PROFIT_LADDER` | US 평균 +1.097%, peak giveback 제한 |
| broker truth fail-closed | 상태 오염 방지 |
| PathA/PathB `RouteDecision` | action traceability의 중심 |
| PathB sizing reason split | 주문 실패 원인 분리 |
| candidate audit/event store | 개선 근거 |
| AUTO_SELL_REVIEW cooldown | Claude 반복 호출 방지 |

### 3.2 강화할 것

| 항목 | 강화 방향 |
|---|---|
| performance ledger | `portfolio_realized`, `strategy_attribution`, `learning_allowed` 분리 |
| KR no-signal report | `trade_ready -> NO_SIGNAL` 원인을 전략/시간/window/evidence별로 분해 |
| PathB miss quality | `INVALID_PRICE`, expired, reentry block 등 execution miss를 별도 리포트화 |
| candidate source attribution | 신규 audit row의 `candidate_source` blank 방지 |
| outcome freshness | `daily_pending`, `audit_sparse`, `insufficient_samples` 상태를 리포트에서 명확히 표시 |
| runtime config truth | 파일 값과 실제 runtime snapshot drift를 preflight/dashboard에서 표시 |
| Claude usage 품질 | call count, token, latency, strict JSON warning, cooldown effect를 outcome과 연결 |

### 3.3 버리거나 보류할 것

| 항목 | 판단 |
|---|---|
| KR live 확장 | no-signal 원인 분석 전 보류 |
| KR shared strategy 직접 튜닝 | US PathB 영향 확인 전 금지 |
| broker truth gate 완화 | 금지 |
| PathB sizing/one-share 정책 변경 | 운영자 승인 전 금지 |
| `state/brain.json` 자동 승격 | 승인형 workflow 안정화 전 보류 |
| candidate audit legacy bulk mutation | audited remediation 없이 금지 |
| `CLOSED_AUDITED_BROKER_SELL`을 strategy 성과로 사용 | 금지, portfolio realized와 분리 |

## 4. P0 개선 방향

### P0-1. 성과 ledger 신뢰도 복구

현재 문제:

- `v2_learning_performance`에 `portfolio_realized`, `strategy_attribution`이 없다.
- `pnl_pct`는 165 closed row에 있지만 `pnl_krw`는 45 row에만 있다.
- `CLOSED_AUDITED_BROKER_SELL` 5건은 `exit_price`/`pnl_krw`가 비어 있다.

작업 방향:

1. `data/ml/decisions.db`, `data/v2_event_store.db` 백업.
2. `tools/sync_v2_learning_performance.py --market ALL --runtime-mode live --dry-run` 실행.
3. 이상 count 확인 후 live sync 실행.
4. sync 후 `portfolio_realized`, `strategy_attribution`, `learning_allowed` 기준 리포트 재계산.

Acceptance:

- portfolio realized report와 strategy learning report가 분리된다.
- audited broker backfill은 portfolio realized에는 들어가되 strategy learning에는 들어가지 않는다.
- KR/US, PathA/PathB, close reason, strategy별 성과가 같은 기준으로 산출된다.

주의:

- 이 작업은 DB operational sync이며 strategy/runtime behavior를 바꾸면 안 된다.

### P0-2. 성과 리포트 표준 view 정의

필수 view:

| view | 기준 |
|---|---|
| portfolio realized | 실제 broker/audited close 포함 |
| strategy realized | strategy attribution이 있는 row만 |
| learning allowed | `learning_allowed=1`만 |
| market split | KR/US 분리 |
| route split | PathA/PathB 분리 |
| close reason split | target/pre-close/profit ladder/loss cap/hard stop/manual 분리 |
| data coverage | `pnl_pct`, `pnl_krw`, exit price, qty coverage 표시 |

설계 포인트:

- 모든 리포트 첫 부분에 raw row count와 coverage를 표시한다.
- `합산 pnl%`와 계좌 수익률을 절대 섞지 않는다.
- capital-weighted return이 없으면 "계좌 수익률"이라고 쓰지 않는다.

### P0-3. Runtime config drift와 broker truth preflight 강화

현재 문제:

- 파일 기준 `KR_PATHB_SELECTION_RECONCILE_MODE=enforce`.
- 과거 runtime snapshot에는 `KR_PATHB_SELECTION_RECONCILE_MODE=shadow`.
- broker truth stale/untrusted가 monitor에서 반복적으로 action_required로 등장.

작업 방향:

1. live stack process/cwd/startup command 기록.
2. live restart 또는 config refresh 후 `tools/live_preflight.py --mode live --skip-dashboard --json`.
3. 최신 `logs/config/effective_config_*_live.redacted.json`에서 market-specific key 확인.
4. dashboard/preflight에 파일 값, env source, runtime snapshot 값을 나란히 표시.

Acceptance:

- runtime snapshot에서 KR/US market-specific reconcile mode가 의도 값과 일치한다.
- broker truth stale이면 entry blocked reason이 dashboard/preflight에서 즉시 보인다.
- global shadow와 market-specific enforce가 어떤 우선순위인지 명확히 표시된다.

주의:

- 원인 확인 없이 global `PATHB_SELECTION_RECONCILE_MODE`를 enforce로 바꾸지 않는다.

### P0-4. KR `trade_ready -> NO_SIGNAL` 원인 리포트

Worklist cross-reference: `docs/important/IMPROVEMENT_WORKLIST_20260607.md`의 `P0-8`.

현재 문제:

- 2026-06-01 ~ 2026-06-05 KR `trade_ready=8`, `signal_fired=0`, `traded=0`.
- KR ORP block은 `orp_entry_window_expired=395`가 가장 많다.
- 이 항목은 기존 worklist의 P1 분석 항목이었지만, 최신 종합 판단에서는 KR live 확장 여부를 가르는 선행 조건이므로 P0로 승격한다.

검증 기간:

| window | 기본 범위 | 목적 |
|---|---|---|
| recent | 2026-06-01 ~ 2026-06-05 | 최근 알려진 KR `trade_ready=8`, `signal_fired=0`, `traded=0` 증상 재현 |
| primary | 기본 `--lookback-days 30` | 실제 판단 기준. 최근 5일 착시를 줄이고 최근 운용 흐름을 본다. |
| full_available | `ticker_selection_log` live 전체 가용 기간 | 누적 경향 확인. 현재 DB 기준 KR은 2026-04-08부터 가용하다. |

작업 방향:

1. `ticker_selection_log`의 KR trade_ready row와 `intraday_strategy_log`를 날짜/티커/전략 기준으로 조인한다.
2. 원인을 아래 bucket으로 분리한다.
   - ORP entry window expired.
   - ORP not formed.
   - ORP range too high/low.
   - gap_pullback threshold miss.
   - momentum wait/cutoff miss.
   - evidence missing/degraded.
   - market/blackout/risk/order gate.
3. 결과를 read-only report로 생성한다.

Acceptance:

- KR trade_ready row마다 "왜 signal이 안 났는지"가 하나 이상의 원인 bucket으로 설명된다.
- strategy threshold 변경은 이 리포트 이후 별도 작업으로만 제안한다.
- 공유 strategy 변경 전 US PathB 영향 범위를 따로 검토한다.
- recent, primary, full_available 세 window의 count와 원인 분포가 함께 산출된다.
- 완료 판정은 5일 recent 재현만으로 하지 않고 primary/full_available 경향까지 확인한다.

### P0-5. Candidate audit source/outcome freshness 보강

현재 문제:

- `candidate_source` blank가 53,055 / 53,123.
- `daily_pending=1551`.
- `actual_prompt_included IS NULL=33008`.

작업 방향:

1. 신규 live audit row에서 `source_file` 또는 stage source를 `candidate_source` fallback으로 기록한다.
2. legacy row는 자동 bulk mutate하지 않는다.
3. outcome updater를 dry-run으로 돌려 pending 원인을 확인한다.
4. candidate audit summary에 source/freshness coverage를 항상 표시한다.

Acceptance:

- 신규 row는 source를 알 수 있는데 `candidate_source`가 blank로 남지 않는다.
- outcome freshness가 회복되기 전에는 candidate audit을 selection 학습 근거로 쓰지 않는다.

### P0-6. PathB `INVALID_PRICE` miss quality 분석

Worklist cross-reference: `docs/important/IMPROVEMENT_WORKLIST_20260607.md`의 `P0-9`.

현재 문제:

- US `INVALID_PRICE` 29건.
- 26건이 cancel 후 zone reentry.
- 평균 30m MFE +1.222%.

검증 기간:

| window | 기본 범위 | 목적 |
|---|---|---|
| recent | 최근 30 calendar days 또는 명시한 `--date-from/--date-to` | 최신 miss 양상이 계속되는지 확인 |
| full_available | `pathb_miss_quality` 전체 가용 기간 | 기준 수치인 US `INVALID_PRICE n=29`, `zone_reentered=26`, `avg_mfe_30m=+1.222%` 재현 |

작업 방향:

1. `pathb_miss_quality`에서 cancel reason, zone reentry, 30m MFE/MAE를 정기 리포트화한다.
2. `INVALID_PRICE`를 가격 source, tick size, stale quote, native/KRW conversion, order timing으로 분해한다.
3. safety gate 완화 없이 diagnostics를 먼저 보강한다.

Acceptance:

- `INVALID_PRICE`가 실제 가격 조회 실패인지, 단위 변환 문제인지, stale price인지 분리된다.
- broker truth fail-closed, sizing, slippage cap은 유지된다.
- full_available 기준 baseline 수치를 재현하고, recent window와의 차이를 표시한다.

## 5. P1 개선 방향

### P1-1. Hold advisor outcome linkage

현재 문제:

- hold advisor 호출 수, latency, HOLD/SELL 비율은 보이지만 outcome 연결이 약하다.

작업 방향:

- HOLD 후 MFE/MAE/giveback.
- SELL 후 missed runup.
- cooldown skip 후 재호출 절감.
- hard guard review bypass 여부.
- advisor fallback과 learning exclusion.

Acceptance:

- hold advisor가 수익을 보호했는지, 손실을 키웠는지, 비용만 늘렸는지 판단할 수 있다.
- AUTO_SELL_REVIEW cooldown 보호 경로는 유지한다.

### P1-2. Selection funnel KPI 고정화

필수 KPI:

| KPI | 의미 |
|---|---|
| prompt candidates | Claude가 본 후보 수 |
| watchlist | 관찰 후보 수 |
| trade_ready | 실행 후보 수 |
| pathb_wait | PathB 대기 후보 수 |
| signal_fired | strategy 실제 신호 수 |
| order_submitted | 주문 제출 수 |
| fill | 체결 수 |
| no_submit reason | 주문 미제출 이유 |
| forward outcome | 놓친 후보 결과 |

목표:

- "후보가 나빴다"와 "실행이 막혔다"를 분리한다.
- KR/US를 같은 표에 넣되 결론은 분리한다.

### P1-3. Claude usage 품질 리포트

작업 방향:

- label별 call count.
- input/output token.
- latency.
- strict JSON warning.
- duplicate candidate action.
- hold advisor triage/challenge 비율.
- cooldown으로 절약된 호출 추정.
- 결과 성과와 연결.

목표:

- Claude 호출이 수익성 개선에 기여하는지, 아니면 복잡도와 비용만 늘리는지 판단한다.

### P1-4. US loss-cap cluster 분석

현재 US `CLOSED_LOSS_CAP`은 22건, 평균 -2.254%다.

작업 방향:

- loss-cap 발생 시간대.
- strategy.
- entry phase.
- market mode.
- previous stop cluster.
- Claude price plan quality.
- broker truth state.

주의:

- loss cap 자체를 완화하지 않는다.
- `CLOSED_AUDITED_BROKER_SELL`과 섞지 않는다.
- 충분한 표본 전에는 shadow/report만 한다.

## 6. P2 / Observe-only 방향

아래는 지금 live behavior를 바꿀 단계가 아니다.

| 항목 | 조건 |
|---|---|
| KR shadow veto | performance sync와 KR no-signal report 이후 |
| KR first-entry/exit overlay | sample gate, broker-fill-aware replay 이후 |
| US KIS ranking primary | shadow/smoke/latency/coverage/rate-limit 통과 이후 |
| prompt overlay | 10 trading days, 4 trigger days, PF > 1.0, top-day contribution < 40% 이후 |
| lesson promotion | refreshed ledger + truth_status fresh 이후 |

## 7. 추가 설계 포인트

### 7.1 Report contract

모든 성과 문서는 아래 header를 가진다.

```text
source_db:
runtime_mode:
market:
date_range:
raw_rows:
deduped_rows:
pnl_pct_coverage:
pnl_krw_coverage:
portfolio_realized_filter:
strategy_attribution_filter:
learning_allowed_filter:
generated_at:
```

이 contract가 없으면 수익률 표를 신뢰하지 않는다.

### 7.2 Runtime truth dashboard

dashboard/preflight에 다음을 표시한다.

- `.env.live` 값.
- `config/v2_start_config.json` env_overrides 값.
- runtime effective snapshot 값.
- source precedence.
- drift 여부.
- broker truth last_success/stale/missing/error.
- entry scan blocked reason.

목표는 운영자가 "지금 실제로 어떤 설정으로 돌고 있는지"를 바로 보는 것이다.

### 7.3 Miss taxonomy

실패 원인을 아래 taxonomy로 고정한다.

| bucket | 예 |
|---|---|
| selection_quality | Claude 후보 부적합, strategy mismatch |
| evidence_quality | missing/degraded intraday data |
| strategy_signal | ORP expired, gap threshold miss |
| execution_price | INVALID_PRICE, stale quote |
| risk_gate | loss cluster, daily cap, position cap |
| broker_truth | stale/missing/untrusted |
| sizing | too small, high price, min order |
| operator/manual | manual close, audited broker backfill |

이 taxonomy가 있어야 후속 개선이 엉뚱한 곳을 건드리지 않는다.

### 7.4 Protected-path change gate

아래 영역 변경은 항상 별도 MD 위반 사항 보고를 요구한다.

- profit ladder.
- pre-close.
- hold advisor AUTO_SELL_REVIEW.
- broker truth fail-closed.
- sizing reason split.
- zero-holding stale reconcile.
- KIS order normalization.
- PathA/PathB routing.

보고에는 변경 전/후, broker truth/risk/order/Claude/config 영향, 테스트, 남은 위험을 포함한다.

## 8. 단기 로드맵

기간: 다음 1~3 작업 단위

1. V2 learning performance sync를 백업/dry-run/apply/verify 순서로 실행한다.
2. portfolio realized vs strategy learning 성과 리포트를 재계산한다.
3. runtime config drift와 broker truth preflight를 최신 snapshot으로 확인한다.
4. KR no-signal/ORP timing read-only report를 만든다.
5. candidate audit source fallback 작업 범위를 코드 변경으로 분리한다.

성공 조건:

- 운영자가 실제 수익률과 전략 성과를 분리해서 볼 수 있다.
- KR에서 왜 signal이 안 나는지 첫 번째 원인표가 나온다.
- US PathB 보호 영역은 변경되지 않는다.

## 9. 중기 로드맵

기간: 다음 2~4주 또는 충분한 live session 이후

1. PathB miss quality 리포트를 정례화한다.
2. hold advisor outcome linkage를 구축한다.
3. KR trade-ready carry 효과를 측정한다.
4. US loss-cap cluster를 shadow/report로 분석한다.
5. candidate audit outcome freshness를 회복한다.
6. Claude usage quality와 수익 기여를 연결한다.

성공 조건:

- selection, execution, risk, broker truth 문제를 dashboard/report에서 분리해 볼 수 있다.
- KR 개선안이 shared US strategy를 건드리기 전에 검증된다.
- hold advisor가 수익 보호인지 비용/지연인지 판단 가능하다.

## 10. 장기 로드맵

기간: 충분한 표본과 ledger 안정화 이후

1. KR market-specific strategy 개선.
2. KR shadow veto 또는 first-entry overlay 검토.
3. US KIS ranking primary 전환 검토.
4. prompt overlay promotion.
5. lesson candidate -> 승인형 memory workflow.
6. capital-weighted portfolio return과 risk-adjusted metric 고도화.

성공 조건:

- 전략 변경이 수익 엔진을 훼손하지 않는다.
- live/enforce 변경은 사전에 report/test/preflight로 검증된다.
- shadow는 "불확실한 행동 관찰" 용도로만 쓰이고, 영구 대기 상태가 되지 않는다.

## 11. 최종 우선순위

가장 먼저 할 일은 다음 5개다.

1. `v2_learning_performance` sync와 성과 리포트 재계산.
2. KR `trade_ready -> NO_SIGNAL` 원인 리포트.
3. runtime config drift/broker truth preflight 확인.
4. candidate audit source/outcome freshness 보강.
5. PathB `INVALID_PRICE` miss diagnostics.

하지 말아야 할 일:

1. KR 손실을 이유로 US PathB profit ladder/pre-close를 건드리는 것.
2. execution miss를 해결하려고 broker truth fail-closed를 완화하는 것.
3. incomplete ledger로 strategy promotion을 하는 것.
4. candidate audit source가 비어 있는데 selection 학습을 강하게 돌리는 것.
5. `state/brain.json`에 자동 정책 교훈을 승격하는 것.

## 12. 최종 문장

이 시스템은 버릴 시스템이 아니다. 하지만 지금 확장할 시스템도 아니다.

정확한 개선 방향은 다음이다.

> US PathB 수익 엔진은 보호하고, 성과 ledger와 KR no-signal 원인 분해를 먼저 끝낸 뒤, selection/execution/risk/broker truth를 분리한 상태에서만 live 개선을 진행한다.

이 순서를 지키면 현재 시스템은 복잡한 실험 장치에서 운영 가능한 수익 시스템으로 갈 수 있다. 이 순서를 무시하면, 수익이 나는 부분까지 같이 망가뜨릴 가능성이 높다.
