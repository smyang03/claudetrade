# 자동매매 시스템 현황, 전체 흐름, 수익률 정리

작성일: 2026-06-07 KST  
작성 범위: 현재 worktree, live DB, logs/state snapshot 기준 읽기 전용 분석  
수정 범위: 문서 작성만 수행. 코드, config, state, DB 원본은 변경하지 않음.

## 1. 결론 요약

이 시스템은 KR/US 주식 자동매매를 대상으로 하는 Python 기반 live 자동매매 시스템이다. 핵심 구조는 Claude selection을 후보 판단 계층으로 쓰되, 실제 주문은 `RouteDecision`, strategy signal, affordability/risk, broker truth, PathB runtime gate를 거쳐야 하는 다단계 구조다.

현재 성과는 시장별로 명확히 갈린다.

| 기준 | 거래 수 | 승률 | 평균 pnl% | 합산 pnl% | 판단 |
|---|---:|---:|---:|---:|---|
| 전체 live closed, `pnl_pct` 있음 | 165 | 44.24% | +0.269% | +44.362% | US 수익이 KR 손실을 덮는 구조 |
| KR | 43 | 27.91% | -1.055% | -45.347% | 손실 구조 |
| US | 122 | 50.00% | +0.735% | +89.708% | PathB 중심 수익 구조 |

주의: 위 `합산 pnl%`는 거래별 `pnl_pct` 단순 합이며 계좌 수익률이 아니다. `pnl_krw`가 있는 closed row는 45건뿐이라 금액 기준 성과는 불완전하다. live DB의 `v2_learning_performance`에는 아직 `portfolio_realized`, `strategy_attribution` 컬럼이 없어 portfolio realized와 strategy learning 판단을 완전히 분리하지 못한다.

## 2. 근거 범위와 한계

이번 문서는 다음 자료를 읽어 작성했다.

| 자료 | 용도 |
|---|---|
| `trading_bot.py` | Path A, selection normalization, broker sync, exit processing |
| `runtime/pathb_runtime.py` | PathB price plan, buy zone hit, entry/exit scan, profit ladder, pre-close, sell review |
| `runtime/action_routing.py` | Claude candidate action을 PlanA/PathB/WATCH로 라우팅하는 계약 |
| `config/v2.py`, `.env.live`, `config/v2_start_config.json` | live 운영 파라미터 확인 |
| `data/ml/decisions.db` | `v2_learning_performance`, `v2_canonical_performance` 성과 집계 |
| `data/v2_event_store.db` | lifecycle event, PathB run 상태, miss quality |
| `data/ticker_selection_log.db` | selection -> trade_ready -> signal -> traded 전환율 |
| `data/intraday_strategy_log.db` | strategy no-signal/block 원인 |
| `data/audit/candidate_audit.db` | candidate audit/source/outcome 상태 |
| `state/live_open_positions.json`, `state/live_broker_truth_snapshot.json` | 검토 시점 broker/local position 상태 |
| `docs/important/ACTIVE_WORK.md`, `docs/important/IMPROVEMENT_WORKLIST_20260607.md` | 현재 알려진 운영 리스크와 개선 backlog |

성과 해석 한계:

- `pnl_pct` 기준 closed row는 165건이지만 `pnl_krw`가 있는 row는 45건뿐이다.
- `CLOSED_AUDITED_BROKER_SELL` 5건은 `pnl_pct`가 있으나 `exit_price`/`pnl_krw`가 비어 있다.
- `learning_allowed=1` closed row가 극히 적어 학습 가능한 성과와 portfolio realized 성과를 아직 같은 신뢰도로 볼 수 없다.
- broker truth snapshot은 검토 시점 상태일 뿐 TTL이 짧다.

## 3. 전체 시스템 흐름도

```text
시장/가격/뉴스/브로커 데이터
  -> 시장 모드 판단
  -> 후보 종목 수집
  -> Claude selection
  -> candidate_actions / watchlist / trade_ready 정규화
  -> runtime/action_routing.py::RouteDecision
       -> BUY_READY / PROBE_READY / ADD_READY: Path A
       -> PULLBACK_WAIT: Path B wait
       -> WATCH / AVOID / blocked: 관찰 또는 제외
  -> V2 decision/lifecycle 등록
  -> candidate audit / ticker selection log 기록

Path A:
  trade_ready
  -> strategy signal 확인
  -> affordability/risk/slippage/blackout/reentry/broker gate
  -> 주문 생성
  -> fill/reconcile
  -> risk_manager exit candidate
  -> sell execution

Path B:
  PULLBACK_WAIT + complete price plan
  -> claude_price plan 등록
  -> WAITING
  -> buy zone hit 감시
  -> broker truth entry scan gate
  -> affordability/sizing/safety gate
  -> buy submit/fill
  -> FILLED position management
  -> loss_cap / hard_stop / profit_ladder / target / pre-close / hold advisor
  -> sell submit/reconcile
  -> CLOSED

Truth / 관측:
  broker holdings, open orders, fills
  -> runtime sync / quarantine / reconcile
  -> V2 event store
  -> decisions DB
  -> candidate audit DB
  -> logs/dashboard/reports
```

## 4. Path A 구조

Path A는 `trading_bot.py`의 `TradingBot` 중심 흐름이다.

핵심 단계:

1. Claude selection 결과를 normalize한다.
2. `trade_ready` 후보를 `v2_decisions`와 `ticker_selection_log`에 연결한다.
3. strategy별 signal을 확인한다.
4. `risk_manager`, affordability, same-day reentry, blackout, broker state, slippage 등의 gate를 통과해야 한다.
5. 주문이 나가면 fill/reconcile 이후 position으로 관리한다.
6. 청산은 `risk_manager.get_exit_candidates()`와 `_process_exit_candidates()`를 통해 loss cap, stop, trail, soft exit, pre-close 등의 우선순위로 처리된다.

Path A의 중요한 특징:

- Claude가 `BUY_READY`를 제안해도 즉시 주문으로 이어지지 않는다.
- KR Plan A signal은 별도 live allow flag가 있어 selection과 실제 signal execution이 분리되어 있다.
- US live allowlist는 `US_MOMENTUM_LIVE_ENABLED=true`가 중요하며, `US_VOLATILITY_BREAKOUT_LIVE_ENABLED`는 미설정이면 false다.
- 현재 DB 기준 Path A로 추정되는 `path_type=''` closed 성과는 US는 소폭 플러스, KR은 마이너스다.

## 5. Path B 구조

Path B는 `runtime/pathb_runtime.py::PathBRuntime` 중심 흐름이며 `claude_price` 기반 가격 계획을 실행한다.

핵심 단계:

1. Claude가 `PULLBACK_WAIT`와 complete price target을 제시한다.
2. `RouteDecision`이 `PathB.wait`로 라우팅한다.
3. `register_from_selection_meta()`가 PathB plan을 등록한다.
4. plan은 `WAITING` 상태로 buy zone을 감시한다.
5. 현재가가 buy zone에 들어오면 `CLAUDE_PRICE_HIT`가 발생한다.
6. entry scan에서 broker truth가 fresh/trusted인지 확인한다. live에서 token/provider unavailable, stale/error broker truth는 `BLOCKED_BROKER_TRUTH`로 fail-closed된다.
7. 가격/예산/sizing/position cap/daily cap/confidence/reentry/slippage gate를 통과하면 매수 주문을 제출한다.
8. fill 이후 `FILLED` 상태에서 exit scan이 돈다.
9. exit 우선순위는 loss cap, hard stop, MFE breakeven, profit ladder, policy auto sell, target/pre-close 등으로 이어진다.
10. 필요한 경우 `AUTO_SELL_REVIEW` hold advisor가 개입하되, hard guard와 cooldown이 반복 호출을 제한한다.

보호해야 하는 PathB 수익 경로:

- `CLOSED_CLAUDE_PRICE_TARGET`
- `CLOSED_CLAUDE_PRICE_PRE_CLOSE`
- `CLOSED_PROFIT_LADDER`
- broker truth entry fail-closed
- sizing reason split
- AUTO_SELL_REVIEW HOLD cooldown guard

## 6. 주문, 리스크, broker truth 흐름

주문 전 주요 차단 조건:

| 축 | 역할 |
|---|---|
| broker truth | holdings/open orders/fills를 1차 truth로 사용 |
| market quarantine | broker state 불신 시 신규 진입보다 보호/복구 우선 |
| affordability | 현금/예산/최소주문/고가종목 차단 |
| sizing gate | `INVALID_PRICE`, `ORDER_SIZE_TOO_SMALL_GATE`, `HIGH_PRICE_BUDGET_BLOCK` 분리 |
| daily/max position cap | 일일 진입 수, 포지션 수 제한 |
| same-day reentry/cooldown | 손절/익절 후 재진입 제한 |
| entry blackout/late session | 장 초반/장 막판 정책 |
| hard stop/loss cap | AI 판단보다 우선하는 보호 장치 |

검토 시점 snapshot:

| 항목 | KR | US |
|---|---:|---:|
| local live open positions | 0 | 0 |
| broker positions | 0 | 0 |
| broker open orders | 0 | 0 |
| broker snapshot generated_at | 2026-06-06T17:13:55Z | 2026-06-06T17:13:55Z |

주의: broker truth snapshot TTL은 60초로 표시되어 있어, 위 표는 검토 시점 상태다.

## 7. 운영 파라미터

파일 기준으로 확인한 주요 live 운영 파라미터:

| 파라미터 | 값 |
|---|---:|
| `PATHB_KR_LIVE_ENABLED` | true |
| `PATHB_US_LIVE_ENABLED` | true |
| `PATHB_KR_SHADOW_PLAN_ENABLED` | false |
| `KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK` | false |
| `PATHB_FIXED_ORDER_KRW` | 450000 |
| `PATHB_MAX_POSITIONS` | 15 |
| `PATHB_MAX_DAILY_ENTRIES` | 40 |
| `PATHB_MIN_CONFIDENCE` | 0.5 |
| `PATHB_INTRADAY_ONLY` | false |
| `PATHB_KR_SLIPPAGE_CAP` | 1.003 |
| `PATHB_US_SLIPPAGE_CAP` | 1.002 |
| `KR_REENTRY_COOLDOWN_MINUTES` | 60 |
| `US_REENTRY_COOLDOWN_MINUTES` | 60 |
| `US_MOMENTUM_LIVE_ENABLED` | true |
| `PATHB_SELECTION_RECONCILE_MODE` | shadow |
| `US_PATHB_SELECTION_RECONCILE_MODE` | enforce |
| `KR_PATHB_SELECTION_RECONCILE_MODE` | enforce |

주의할 drift:

- `.env.live`와 `config/v2_start_config.json`은 KR PathB selection reconcile을 `enforce`로 둔다.
- 최신으로 확인된 과거 runtime effective snapshot `logs/config/effective_config_20260606_024649_live.redacted.json`에는 `KR_PATHB_SELECTION_RECONCILE_MODE=shadow`가 기록되어 있다.
- 이는 코드 변경보다 runtime restart/startup env/config path 검증이 먼저 필요한 운영 문제다.

## 8. 수익률 현황

### 8.1 `pnl_pct` 기준 live closed 성과

| 시장 | 거래 수 | 승 | 패 | 승률 | 평균 pnl% | 합산 pnl% | 최소 | 최대 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 전체 | 165 | 73 | 92 | 44.24% | +0.269% | +44.362% | -9.818% | +12.761% |
| KR | 43 | 12 | 31 | 27.91% | -1.055% | -45.347% | -9.818% | +8.374% |
| US | 122 | 61 | 61 | 50.00% | +0.735% | +89.708% | -3.778% | +12.761% |

해석:

- 전체 플러스는 US가 만든다.
- KR은 승률과 평균 pnl이 모두 약하다.
- US는 승률 50%에 평균 pnl이 플러스라 구조적으로 살릴 가치가 있다.

### 8.2 `pnl_krw` 기준 live closed 성과

`pnl_krw`가 있는 closed row만 보면 다음과 같다.

| 기준 | 거래 수 | 승률 | 실현손익 KRW | 평균 KRW | profit factor |
|---|---:|---:|---:|---:|---:|
| 전체 | 45 | 31.11% | -38,192.79 | -848.73 | 0.673 |
| KR | 26 | 23.08% | -61,816.70 | -2,377.57 | 미계산 |
| US | 19 | 42.11% | +23,623.91 | +1,243.36 | 미계산 |

해석:

- KRW 기준도 US는 플러스, KR은 마이너스다.
- 다만 `pnl_krw` coverage가 낮아 전체 portfolio 손익으로 단정하면 안 된다.
- `pnl_krw` 기준 누적 최대 낙폭은 closed-with-krw 정렬 기준 약 41,866.59 KRW로 계산되지만, coverage 한계가 있다.

## 9. PathA vs PathB 성과

`path_type='claude_price'`를 PathB로 보고, `path_type=''`를 PathA/기타로 보면 다음과 같다.

| 시장 | path_type | 거래 수 | 승률 | 평균 pnl% | 합산 pnl% | 판단 |
|---|---|---:|---:|---:|---:|---|
| KR | blank | 10 | 30.00% | -1.374% | -13.735% | 약함 |
| KR | claude_price | 33 | 27.27% | -0.958% | -31.612% | KR PathB도 손실 |
| US | blank | 5 | 40.00% | +0.454% | +2.268% | 표본 작음 |
| US | claude_price | 117 | 50.43% | +0.747% | +87.440% | 핵심 수익축 |

해석:

- US PathB `claude_price`가 현재 가장 중요한 수익 엔진이다.
- KR은 PathA/PathB 모두 손실이므로 KR 개선은 selection만이 아니라 signal/execution/market-fit을 분리해서 봐야 한다.
- PathA/기타는 route/metadata coverage가 완전하지 않으므로 정밀 판단은 V2 sync 이후 다시 해야 한다.

## 10. 전략별 성과

| 시장 | 전략 | 거래 수 | 승률 | 평균 pnl% | 합산 pnl% | 평가 |
|---|---|---:|---:|---:|---:|---|
| KR | claude_price | 4 | 50.00% | +0.337% | +1.348% | 표본 작음 |
| KR | gap_pullback | 20 | 35.00% | -0.693% | -13.866% | 손실 |
| KR | mean_reversion | 1 | 0.00% | -0.707% | -0.707% | 표본 부족 |
| KR | opening_range_pullback | 6 | 16.67% | -1.320% | -7.919% | 약함 |
| KR | momentum | 11 | 18.18% | -1.955% | -21.507% | 가장 약함 |
| US | opening_range_pullback | 7 | 57.14% | +1.692% | +11.847% | 강함, 표본 작음 |
| US | claude_price | 79 | 54.43% | +1.047% | +82.685% | 핵심 수익 |
| US | momentum | 11 | 45.45% | +0.655% | +7.206% | 유지 가치 |
| US | gap_pullback | 24 | 37.50% | -0.403% | -9.675% | 개선 필요 |
| US | mean_reversion | 1 | 0.00% | -2.354% | -2.354% | 표본 부족 |

중요 해석:

- 같은 전략 이름이라도 KR/US 성과가 다르다.
- KR momentum/gap/ORP를 고치기 위해 공유 전략 파일을 직접 조정하면 US PathB 성과를 훼손할 수 있다.
- KR 개선은 market-specific gate/report부터 해야 한다.

## 11. 청산 사유별 성과

### 11.1 US

| close reason | 거래 수 | 승률 | 평균 pnl% | 합산 pnl% | 판단 |
|---|---:|---:|---:|---:|---|
| `CLOSED_CLAUDE_PRICE_TARGET` | 10 | 100.00% | +5.311% | +53.115% | 강한 수익 경로 |
| `CLOSED_CLAUDE_SELL` | 6 | 100.00% | +3.913% | +23.476% | 양호 |
| `CLOSED_TRAILING_STOP` | 2 | 50.00% | +3.793% | +7.586% | 표본 작음 |
| `CLOSED_CLAUDE_PRICE_PRE_CLOSE` | 34 | 55.88% | +1.442% | +49.040% | 핵심 보호 경로 |
| `CLOSED_PROFIT_LADDER` | 20 | 65.00% | +1.097% | +21.945% | 핵심 보호 경로 |
| `CLOSED_HARD_STOP` | 6 | 50.00% | +0.197% | +1.180% | 손실 제한 역할 |
| `CLOSED_LOSS_CAP` | 22 | 0.00% | -2.254% | -49.594% | 손실 클러스터 분석 필요 |
| `CLOSED_AUDITED_BROKER_SELL` | 5 | 0.00% | -3.778% | -18.888% | learning 제외/ledger sync 필요 |

참고: `CLOSED_HARD_STOP` 평균이 플러스로 보이는 것은 hard stop 정책이 수익 청산이라는 뜻이 아니다. close reason 라벨과 실제 체결 시점 사이의 가격 회복, trailing/보호 로직 경계 사례, reconcile된 종료 이벤트가 섞일 수 있으므로 손실 제한 경로로 해석해야 한다.

### 11.2 KR

| close reason | 거래 수 | 승률 | 평균 pnl% | 합산 pnl% | 판단 |
|---|---:|---:|---:|---:|---|
| `CLOSED_CLAUDE_PRICE_TARGET` | 1 | 100.00% | +7.937% | +7.937% | 표본 1 |
| `CLOSED_TRAILING_STOP` | 5 | 40.00% | +1.492% | +7.461% | 일부 보호 |
| `CLOSED_CLAUDE_PRICE_PRE_CLOSE` | 9 | 44.44% | -0.007% | -0.065% | 중립 |
| `CLOSED_USER_MANUAL` | 11 | 9.09% | -2.158% | -23.738% | 좋지 않음 |
| `CLOSED_LOSS_CAP` | 11 | 0.00% | -2.782% | -30.599% | 손실 중심 |
| `CLOSED_HARD_STOP` | 1 | 0.00% | -9.818% | -9.818% | 큰 손실 |

## 12. Selection 전환율

`data/ticker_selection_log.db` 전체 기간:

| 시장 | rows | trade_ready | signal_fired | traded | trade_ready rate | traded rate |
|---|---:|---:|---:|---:|---:|---:|
| KR | 3,982 | 130 | 54 | 43 | 3.26% | 1.08% |
| US | 4,958 | 426 | 59 | 34 | 8.59% | 0.69% |

최근 2026-06-01 ~ 2026-06-05:

| 날짜 | 시장 | rows | trade_ready | signal_fired | traded |
|---|---|---:|---:|---:|---:|
| 2026-06-05 | KR | 152 | 1 | 0 | 0 |
| 2026-06-05 | US | 120 | 5 | 0 | 0 |
| 2026-06-04 | KR | 453 | 0 | 0 | 0 |
| 2026-06-04 | US | 126 | 5 | 0 | 0 |
| 2026-06-03 | KR | 210 | 0 | 0 | 0 |
| 2026-06-03 | US | 394 | 19 | 1 | 1 |
| 2026-06-02 | KR | 243 | 3 | 0 | 0 |
| 2026-06-02 | US | 302 | 36 | 0 | 0 |
| 2026-06-01 | KR | 241 | 4 | 0 | 0 |
| 2026-06-01 | US | 247 | 31 | 1 | 0 |

최근 KR `trade_ready` 8건은 모두 `signal_fired=0`, `traded=0`이다. 전략 분포는 `gap_pullback=5`, `opening_range_pullback=2`, `mean_reversion=1`이다.

## 13. Strategy no-signal/block 원인

`data/intraday_strategy_log.db` 최근 2026-06-01 이후 주요 block:

| 시장 | 전략 | blocked_reason | count |
|---|---|---|---:|
| KR | opening_range_pullback | `orp_entry_window_expired` | 395 |
| KR | opening_range_pullback | `orp_not_formed` | 174 |
| KR | opening_range_pullback | `orp_range_too_high` | 111 |
| US | opening_range_pullback | `orp_entry_window_expired` | 678 |
| US | opening_range_pullback | `orp_not_formed` | 189 |

해석:

- KR은 selection이 trade_ready를 만들더라도 strategy signal 단계에서 막힌다.
- ORP는 entry window timing mismatch 가능성이 크다.
- 하지만 ORP window를 바로 넓히면 US와 공유 전략 영향이 생길 수 있으므로 read-only 원인 리포트가 먼저다.

## 14. Candidate audit 상태

| 항목 | 값 |
|---|---:|
| `audit_candidate_rows` | 53,123 |
| `candidate_source` blank | 53,055 |
| `source_file` blank | 0 |
| `primary_bucket` blank | 19,648 |
| `actual_prompt_included IS NULL` | 33,008 |
| `audit_candidate_outcomes.daily_pending` | 1,551 |
| `audit_candidate_outcomes.audit_sparse` | 19,716 |
| `audit_candidate_outcomes.insufficient_samples` | 62,810 |

해석:

- candidate audit은 양은 많지만 freshness/source attribution이 아직 부족하다.
- selection 품질 학습에 바로 쓰기에는 위험하다.
- `source_file`은 채워져 있으므로 신규 row의 `candidate_source` fallback을 표준화할 여지가 있다.

## 15. PathB lifecycle와 miss quality

`data/v2_event_store.db` 기준 PathB run 상태:

| 시장 | status | rows |
|---|---|---:|
| KR | CANCELLED | 59 |
| KR | EXPIRED | 35 |
| KR | CLOSED | 18 |
| KR | SHADOW_CANCELLED | 4 |
| US | CANCELLED | 152 |
| US | CLOSED | 135 |
| US | EXPIRED | 55 |

최근 2026-06-01 이후 lifecycle event:

| event | 시장 | rows |
|---|---|---:|
| `TRADE_READY_NO_SUBMIT` | US | 492 |
| `CLAUDE_TRADE_READY` | US | 67 |
| `FILLED` | US | 63 |
| `CLAUDE_PRICE_PLAN_CREATED` | US | 39 |
| `CLAUDE_PRICE_HIT` | US | 35 |
| `CLOSED` | US | 31 |
| `TRADE_READY_NO_SUBMIT` | KR | 24 |
| `CLAUDE_TRADE_READY` | KR | 13 |

PathB miss quality:

| 시장 | cancel reason | n | zone reentered | avg 30m MFE |
|---|---|---:|---:|---:|
| US | INVALID_PRICE | 29 | 26 | +1.222% |
| US | EXPIRED | 14 | 9 | -0.072% |
| KR | EXPIRED | 8 | 6 | +3.551% |
| US | ALREADY_HOLDING | 7 | 6 | +1.243% |
| US | SAME_DAY_REENTRY_AFTER_STOP | 4 | 3 | +1.787% |

해석:

- US PathB는 수익도 만들지만 miss도 있다.
- 특히 `INVALID_PRICE` 취소 후 zone 재진입이 많아 execution quality 분석이 필요하다.
- 단, 이를 broker truth fail-closed나 sizing gate 완화로 해결하면 안 된다.

## 16. 현재 객관적 상태

유지해야 할 강점:

- US PathB `claude_price` 수익 엔진.
- `CLOSED_CLAUDE_PRICE_TARGET`, `CLOSED_CLAUDE_PRICE_PRE_CLOSE`, `CLOSED_PROFIT_LADDER`.
- broker truth fail-closed.
- PathA/PathB route 분리.
- candidate audit, v2 event store, ticker selection log 등 사후 분석 기반.

가장 큰 약점:

- KR live 성과 부진.
- 최근 KR `trade_ready -> signal_fired` 단절.
- performance ledger sync 미완성.
- candidate audit source/outcome freshness 부족.
- runtime config drift 가능성.
- PathB miss quality 중 `INVALID_PRICE` 취소 후 재진입 문제.

즉시 판단:

- US PathB는 보호하면서 측정/리포트/ledger를 보강해야 한다.
- KR은 live 확장보다 no-signal 원인 분석과 evidence 품질 개선이 먼저다.
- selection 문제, execution/risk 문제, broker truth 문제를 한 작업에서 섞으면 안 된다.
