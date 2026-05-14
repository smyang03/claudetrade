# Candidate Pipeline Improvement Implementation Plan

작성일: 2026-05-15

## 왜 했는가

이번 작업의 목적은 live 수익률을 바로 키우는 것이 아니라, 손실이 번지는 경로를 막고 후보-프롬프트-관측-실행 데이터를 서로 비교 가능한 상태로 복구하는 것이다.

나중에 봐야 할 핵심 질문은 다음과 같다.

- KR 손실이 `claude_price` live 경로에서 계속 발생하는가, 아니면 격리 후 paper 성과가 개선되는가.
- US 장초반 screener degraded 결과가 cache로 고정되는가.
- 실제 Claude prompt에 들어간 종목과 제외된 종목이 audit DB에서 분리되는가.
- 30m/60m outcome coverage가 ranker/trigger 평가 가능한 수준으로 회복되는가.
- KR 손익이 KOSPI/KOSDAQ beta 때문인지 strategy alpha 문제인지 분리되는가.
- `missing_strategy`가 metadata 부재인지 실제 전략 부재인지 구분되는가.

## 구현 범위

### A. 손실 차단 설정

대상:

- `config/v2_start_config.json`
- `.env.live`
- `runtime/v2_lifecycle_runtime.py`
- `runtime/pathb_runtime.py`

구현:

- `KR_DAILY_ENTRY_CAP=1`, `US_DAILY_ENTRY_CAP=1`.
- live `PATHB_KR_LIVE_ENABLED=false`.
- legacy fallback `KR_CLAUDE_PRICE_LIVE_ENABLED=false`.
- `.env.paper`의 KR PathB paper 관측은 유지.

운영상 기대:

- KR/US 신규 진입이 시장별 1회로 제한된다.
- KR `claude_price` live 신규 진입이 차단된다.

### B. Evidence timeout 완화

대상:

- `config/v2_start_config.json`
- `.env.live`
- `trading_bot.py::_prefetch_selection_intraday_evidence()`

구현:

- `INTRADAY_EVIDENCE_PREFETCH_TIMEOUT_SEC=15`.
- `US_INTRADAY_MAX_WORKERS=1` 유지.

운영상 기대:

- US 장초반 provider 지연 시 evidence coverage가 완화된다.
- worker 증가로 인한 rate burst는 피한다.

### C. US Screener Quality Guard

대상:

- `kis_api.py::screen_market_us()`
- `kis_api.py::_us_post_filter_with_stats()`
- `bot/screener_quality.py::write_candidate_quality_log()`
- `tests/test_screener_quality.py`

구현:

- `_US_SCREEN_CACHE_SCHEMA=3`.
- cache min-count 계산:

```python
absolute_floor = min(US_SCREEN_MIN_CACHE_CANDIDATES, quota_total)
ratio_floor = min(quota_total, ceil(quota_total * US_SCREEN_MIN_CACHE_RATIO))
min_cache_count = max(absolute_floor, ratio_floor)
```

- 첫 배포 기본값은 `US_SCREEN_MIN_CACHE_CANDIDATES=30`, `US_SCREEN_MIN_CACHE_RATIO=0.60`.
- fresh count가 min-cache 미달이면 현재 cycle 반환은 유지하되 cache 저장은 하지 않는다.
- 기존 cache도 `fresh_count >= min_cache_count`일 때만 재사용한다.
- cache skip 로그에 `fresh_count`, `min_cache_count`, `quota_total`, `ratio`, `source`, `mode`, `top_n`을 남긴다.
- screener quality metadata를 candidate JSONL row에 보존한다.
- `effective_min_dollar_vol`은 구현하지 않았다. 시간비례 dollar volume scaling은 후속 관측 후 판단한다.

운영상 기대:

- degraded Yahoo/FMP 결과가 30분 cache로 고정되지 않는다.
- 정상 결과는 cache를 계속 써서 provider 재조회 부담을 줄인다.

### D. Candidate Audit Linkage

대상:

- `trading_bot.py::_write_candidate_audit_live()`
- `dashboard/dashboard_server.py` candidate audit rows API
- `audit/candidate_audit_store.py`

구현:

- call payload에 `actual_prompt_tickers`, `excluded_prompt_tickers`, prompt counts, screener quality를 저장한다.
- candidate payload에도 screener quality를 저장한다.
- 기존 `final_prompt_included`, `prompt_excluded_reason`, `payload_json`을 활용해 additive 호환성을 유지한다.
- dashboard rows API에서 `screener_quality_state`, `screener_degraded`, skip reason을 노출한다.

운영상 기대:

- top30 누락이 screener 문제인지 prompt curation 문제인지 분리된다.
- 기존 audit DB schema와 호환된다.

### E. Outcome Label 자동 갱신

대상:

- `tools/update_candidate_audit_outcomes.py`
- 신규 `tools/candidate_audit_outcome_catchup.py`
- `trading_bot.py::run_housekeeping()`

구현:

- 장중 market/session별 60분 경과 후 1회 `update_candidate_audit_outcomes(... horizons=(30, 60))` 실행.
- idempotent key: `(runtime_mode, market, session_date, 60)`.
- 외부 catch-up wrapper 추가:
  - `--date`
  - `--from-date`
  - `--to-date`
  - `--days`
  - `--market`
  - `--runtime-mode`
  - `--horizons`
  - `--db`
  - `--dry-run`

운영상 기대:

- 30m/60m outcome coverage가 자동으로 쌓인다.
- 장마감/과거 세션 보정은 외부 스케줄러에서 안전하게 실행 가능하다.

### F. KR Benchmark Alpha Report

대상:

- 신규 `tools/kr_benchmark_alpha_report.py`
- `kis_api.py::get_index_snapshot()`
- `audit/candidate_audit_store.py`

구현:

- KIS 국내 지수 API를 1차 소스로 사용한다.
- 실패 시 yfinance `^KS11`, `^KQ11` fallback.
- KIS와 yfinance 모두 실패하면 `source=unavailable`, `change_pct=0.0`으로 non-fatal report를 생성한다.
- KR filled rows의 `pnl_pct` 평균과 KOSPI/KOSDAQ board-weighted benchmark를 비교해 alpha를 계산한다.

운영상 기대:

- KR 손실을 시장 beta와 전략 alpha로 분리해서 본다.

### G. 분석 도구 2종

대상:

- 신규 `tools/analyze_kr_claude_price_cases.py`
- 신규 `tools/analyze_broker_sync_cases.py`

구현:

- 둘 다 read-only.
- live decisions JSONL을 기반으로 case table을 만든다.
- 입력 파일이 없어도 crash하지 않고 warning summary를 반환한다.

운영상 기대:

- KR `claude_price` 복구 여부와 broker_sync runtime metadata 추가 필요성을 사후 판단한다.

### H. `missing_strategy` 축소

대상:

- `trading_bot.py::_watch_trigger_shadow_strategy_for_ticker()`
- `tools/analyze_candidate_audit.py::watch_trigger_funnel_summary()`

구현:

- strategy inference 순서:
  - `recommended_strategy`
  - `candidate_actions[].recommended_strategy`
  - `candidate_actions[].strategy`
  - `candidate_actions[].route_strategy`
  - `candidate_actions[].strategy_name`
  - `primary_bucket`
  - `category`
  - bucket classifier
- `strategy_source` breakdown을 분석 summary에 추가했다.
- live promotion rule은 변경하지 않았다.

운영상 기대:

- shadow trigger의 `missing_strategy`가 실제 metadata 부재일 때만 남는다.

### I. Live Preflight Policy Alignment

대상:

- `tools/live_preflight.py::_config_checks()`
- `tests/test_live_config_sources.py`

구현:

- `PATHB_KR_LIVE_ENABLED=false` 같은 시장별 PathB live gate 비활성화는 `FAIL`이 아니라 `WARN`으로 평가한다.
- `KR_DAILY_ENTRY_CAP=1`, `US_DAILY_ENTRY_CAP=1`을 live effective config 테스트에서 직접 검증한다.
- preflight effective values에 시장별 daily cap을 포함해 운영 점검 출력에서 바로 확인 가능하게 했다.

운영상 기대:

- 의도적으로 KR PathB live를 격리한 상태가 배포 차단 실패로 오인되지 않는다.
- 전역 `V2_MAX_DAILY_ENTRIES=20`과 시장별 cap 1의 역할을 테스트에서 분리해 확인한다.

## 요구서 대비 구현 대조

완료:

- per-market cap 1.
- KR `claude_price` live off.
- evidence timeout 15초.
- US degraded cache 저장/재사용 방지.
- screener quality metadata 보존.
- actual prompt ticker와 excluded prompt ticker call payload 저장.
- candidate audit dashboard rows에 screener quality 노출.
- outcome intraday 60m 이후 1회 자동 갱신.
- outcome catch-up wrapper.
- KR benchmark alpha report.
- KR `claude_price` case review tool.
- broker_sync case review tool.
- `missing_strategy` fallback 및 source breakdown.
- live preflight의 PathB 시장별 gate disabled 판정을 WARN으로 조정.
- live config test에 KR/US per-market daily cap 검증 추가.

의도적으로 보류:

- `effective_min_dollar_vol` payload emit.
- dollar volume 시간비례 scaling.
- trade_ready 수 확대.
- ranker 기반 live 재정렬 강제 적용.
- `soft_b_confirm60` live buy.

차이점:

- `US_SCREEN_MIN_CACHE_RATIO`는 검토 결과 0.80이 아니라 0.60으로 구현했다. live `US_SCREEN_TOP_N=80`에서 0.80은 `min_cache_count=64`가 되어 정상 장중 cache skip이 잦을 수 있기 때문이다.
- KR benchmark alpha report는 KIS/yfinance 모두 실패해도 운영 리포트 생성이 끊기지 않도록 `source=unavailable` fallback을 추가했다.
- Outcome intraday update는 housekeeping에서 실행한다. 실패 시 다음 housekeeping에서 재시도되며, 성공한 session/horizon은 중복 실행하지 않는다.
- PathB 시장별 live gate disabled는 운영 정책일 수 있으므로 FAIL이 아니라 WARN으로 유지한다. 실제 치명 실패는 설정 충돌, DB schema, 주문/토큰/코드 안전성 문제에 남긴다.

## 검증 결과

실행 완료:

```powershell
python -m py_compile kis_api.py bot\screener_quality.py trading_bot.py dashboard\dashboard_server.py tools\candidate_audit_outcome_catchup.py tools\kr_benchmark_alpha_report.py tools\analyze_kr_claude_price_cases.py tools\analyze_broker_sync_cases.py tools\analyze_candidate_audit.py
python -m json.tool config\v2_start_config.json | Out-Null
python -m pytest tests/test_screener_quality.py -q
python -m pytest tests/test_kr_benchmark_alpha_report.py tests/test_analyze_kr_claude_price_cases.py tests/test_analyze_broker_sync_cases.py tests/test_watch_trigger_shadow_strategy.py -q
python -m pytest tests/test_candidate_audit.py -q
python -m pytest tests/test_dashboard_candidate_audit_api.py -q
python -m pytest tests/test_pathb_runtime.py tests/test_entry_risk_controls.py tests/test_dashboard_pathb.py -q
python -m pytest tests/test_trading_bot_intraday_evidence.py -q
python -m pytest tests/test_us_exchange_resolver.py tests/test_screener_quality.py -q
python -m pytest tests/test_live_sell_pending_reconcile.py tests/test_dashboard_execution_contamination.py -q
python -m py_compile tools\live_preflight.py
python -m pytest tests/test_live_config_sources.py -q
```

결과:

- 전체 실행 테스트 통과.
- 일부 테스트에서 eventlet/greenlet distutils deprecation warning만 발생.
- `config.pathb_market_live_gates` 직접 확인 결과 `WARN Path B market live gates disabled: KR`, `FAILS=[]`.

## 운영 모니터링 포인트

- US screener cache skip이 정상 장중 2회 연속 또는 30분 기준 30% 이상 반복되는지 본다.
- `screener_cache_skipped_reason=fresh_count_below_min_cache_count` 빈도가 높으면 `US_SCREEN_MIN_CACHE_RATIO=0.50~0.55`로 완화한다.
- degraded cache가 통과하면 `US_SCREEN_MIN_CACHE_RATIO=0.65`까지 상향 검토한다.
- KR `claude_price` paper closed 15건 이상에서 평균 PnL, PF, loss cap 반복 여부를 본다.
- KR PathB live 재개는 바로 하지 않는다. 먼저 KR장 최소 1회는 `PATHB_KR_LIVE_ENABLED=false`로 관측해 격리 후 손실 경로가 사라지는지 확인한다.
- KR PathB live를 재개할 때는 `PATHB_KR_LIVE_ENABLED=true`, `KR_DAILY_ENTRY_CAP=1` 유지, `KR_CLAUDE_PRICE_LIVE_ENABLED=false` 유지 상태로 1건만 검증한다.
- candidate audit outcome coverage의 60m `audit_sparse` 비율을 본다.
- KR alpha report에서 strategy alpha가 지속 음수인지, 시장 beta 설명력이 큰지 본다.
- watch trigger shadow의 `strategy_source_counts`와 `missing_strategy_rate`를 본다.
