# 시스템 전역 데이터 흐름 누락/오염 점검 리포트

작성일: 2026-07-23  
작성자: Codex  
범위: 2026-07-01 이후 live 후보 감사 원장, selection 로그, v2 학습/성과 원장, 외부 데이터 파일, 관련 코드 경로  
성격: 읽기 전용 분석. 매매 로직 변경 없음.

## 0. 결론

다른 모델이 보는 `judge recheck` 큐 선별 문제와 이 리포트는 다른 축이다.

- `judge recheck`: 이미 큐에 들어간 후보 중 세션당 10개를 무엇부터 다시 볼 것인가.
- 이 리포트: 후보/뉴스/장중증거/성과/외부데이터가 실제로 들어왔는데 점수화·라우팅·학습·관측까지 전달되는가.

내 판단은 이렇다.

1. 지금 바로 `CANDIDATE_PROMPT_POOL_REORDER_ENABLED=true`로 전환할 근거는 없다. 기존 문서도 false 유지 결론이고, 이번 점검에서도 reorder 입력으로 쓸 수 있는 핵심 데이터 중 `cohort_reliability`, 뉴스 polarity, spread, sector가 오염 또는 무효 상태다.
2. 매수 전략 방향 자체보다 먼저 고쳐야 할 것은 데이터 계약이다. 현재는 “좋은 후보를 못 산다”와 “샀는데 학습 원장에 안 남는다”가 섞여 있어 전략 성능 판단이 오염된다.
3. KR은 뉴스 catalyst bonus가 방향성 반대로 작동할 수 있다. `risk_negative`, `weak_generic`, `price_action_only`도 `kr_catalyst_bonus`를 받는 실측이 있다.
4. US는 뉴스 타임스탬프 오염이 크다. 7월 US 뉴스 후보 중 `age_min < 0` 행이 6,029건으로, 과거 시점에서 미래 뉴스처럼 보이는 look-ahead 오염 가능성이 있다.
5. MFE/MAE 동기화는 지금 상태로 full sync하면 기존 학습 원장의 excursion 값을 대량으로 잃을 위험이 있다.
6. sleeve는 이미 다른 리포트에서 지적됐지만, 현재 실제 계좌를 움직이는 경로인데 route/net attribution이 관측 밖이다. 이건 수익 개선보다 리스크 관리 P0다.

즉, 다른 모델의 `recheck queue sort shadow`는 진행할 가치가 있지만, 그 정렬 점수에 아래 오염된 feature를 바로 넣으면 안 된다. recheck 정렬은 별도 shadow로 닫고, prompt pool reorder는 계속 false가 맞다.

## 1. 입력과 재현

사용한 데이터:

- `data/audit/candidate_audit.db`
- `data/ml/decisions.db`
- `data/ticker_selection_log.db`
- `data/v2_event_store.db`
- `logs/config/effective_config_20260722_160540_live.redacted.json`
- `data/earnings_calendar.json`
- `data/sector_map.json.disabled_until_restart`
- `data/dart_corp_codes.json`
- `state/candidate_cohort_reliability_KR_20260722.json`
- `state/candidate_cohort_reliability_US_20260722.json`

재현 스크립트:

```text
python tools/system_wide_data_flow_audit.py --start-date 2026-07-01 --skip-mfe-sync-loss
python -m py_compile tools/system_wide_data_flow_audit.py
```

MFE sync 손실 위험 별도 검증:

```text
python - <<PY
import json
from tools.system_wide_data_flow_audit import audit_mfe_sync_loss
print(json.dumps(audit_mfe_sync_loss(), ensure_ascii=False, indent=2, sort_keys=True))
PY
```

문법 검증: `python -m py_compile tools/system_wide_data_flow_audit.py` 통과.

## 2. 운영 반영 상태

현재 live stack은 2026-07-22 16:05 KST에 시작된 프로세스다.

```text
trading_bot PID 47720 start 2026-07-22 16:05:37 KST
latest effective config: logs/config/effective_config_20260722_160540_live.redacted.json
```

이후 여러 데이터 흐름 수정 커밋이 들어갔다.

```text
669f11a fix: 체결 귀속을 플랜 생성 앵커 기준으로 재작성 — route 계열 추정 폐기
962451b docs: 최종 개선안 범위 — 두 AI 점검 통합, 오귀속 교정 반영
2bec8e3 fix: 체결 귀속 오귀속 56건 교정
3d39632 docs: 네 축 심층 점검
e8ac0e6 docs: 개선 적용 후 재시뮬레이션 검증
ce8ef6c fix: 데이터 흐름 누수 4건 수정
```

판정:

- 로컬 코드와 문서가 고쳐졌더라도 현재 live 프로세스에는 반영됐다고 보면 안 된다.
- 매매 로직을 더 고치기 전에 “최종 적용 커밋 기준으로 재시작 → effective config 재스냅샷 → 후보/체결 원장 smoke”가 필요하다.

## 3. 현재 effective config에서 중요한 값

```text
CANDIDATE_PROMPT_POOL_REORDER_ENABLED=false
CANDIDATE_QUALITY_TRAINER_ENABLED=true
CANDIDATE_TRAINER_QUALITY_SCORE_ENABLED=true
CANDIDATE_QUALITY_TRAINER_PROMPT_HINT_ENABLED=false
CANDIDATE_QUALITY_COMPOSITE_SCORE_PROMPT_ENABLED=false
KR_CATALYST_SCORE_BONUS_ENABLED=true
US_CATALYST_SCORE_BONUS_ENABLED=false
PREOPEN_WEAK_NEWS_PENALTY=0.05
SECTOR_MAP_ENABLED=null
CANDIDATE_AUDIT_DB_PATH=data/audit/candidate_audit.db
CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true
```

판정:

- prompt pool reorder false는 의도된 보수 설정으로 보인다.
- 특히 현재는 `KR_CATALYST_SCORE_BONUS_ENABLED=true`가 더 위험하다. 뉴스 polarity 오염이 실제 점수에 들어간다.
- `US_CATALYST_SCORE_BONUS_ENABLED=false`는 유지가 맞다. US는 뉴스 timestamp 문제가 먼저다.

## 4. P0/P1 발견 항목

### P0-1. KR 뉴스 catalyst bonus가 risk/weak/news-present에도 붙는다

실측:

7월 KR 후보 원장에서 `kr_catalyst_bonus`가 붙은 뉴스 유형:

| signal | news_prompt_eligible | rows | kr_catalyst_bonus rows |
|---|---:|---:|---:|
| direct_catalyst | 1 | 2,844 | 2,789 |
| direct_catalyst | 0 | 2,921 | 2,815 |
| risk_negative | 1 | 537 | 511 |
| weak_generic | 0 | 532 | 515 |
| theme_broad | 0 | 467 | 425 |
| price_action_only | 0 | 114 | 114 |
| risk_negative | 0 | 113 | 113 |
| analyst_or_report | 0 | 64 | 43 |

뉴스별 후보 결과:

| market | signal | eligible | rows | final_prompt | watchlist | trade_ready | filled |
|---|---|---:|---:|---:|---:|---:|---:|
| KR | risk_negative | 1 | 537 | 458 | 326 | 0 | 1 |
| US | risk_negative | 1 | 563 | 473 | 360 | 0 | 0 |

코드 대조:

- `preopen/news_quality.py:421-423`: risk면 `signal_type = "risk_negative"`, `prompt_eligible = direct_match and score >= 35`
- `preopen/news_quality.py:459`: score는 `max(0, min(100, ...))`로 양수 magnitude만 저장
- `preopen/scorer.py:57-58`: `risk_negative`는 `risk_tags.append("risk_news")`만 수행
- `runtime/candidate_quality_trainer.py:253-262`: KR catalyst bonus는 `news_or_earnings_sources` 또는 `news_prompt_eligible`만 보고 부여

원인:

- 뉴스 점수는 polarity가 아니라 magnitude다.
- KR trainer는 `news_signal_type`의 방향성을 보지 않고 “뉴스가 있거나 prompt eligible이면 catalyst”로 처리한다.
- 그래서 risk/weak/price-action 뉴스도 catalyst bonus로 들어간다.

시장별 판단:

- KR: 즉시 수정 대상. 현재 config에서 bonus가 켜져 있어 실거래 후보 점수에 영향 가능.
- US: 현재 bonus false라 직접 영향은 제한적. 다만 같은 코드 구조라 true 전환 금지.

개선안:

1. `news_score`와 별도로 `news_polarity` 또는 `news_score_signed`를 저장한다.
2. catalyst 판단식을 다음처럼 제한한다.

```python
is_positive_catalyst = (
    news_signal_type in {"direct_catalyst", "earnings_or_guidance", "disclosure_material"}
    and bool(news_prompt_eligible)
    and not risk_news_summary
)
```

3. `news_or_earnings_sources` 존재만으로 catalyst bonus를 주지 않는다.
4. 회귀 테스트:
   - `risk_negative + prompt_eligible=True`는 `kr_catalyst_bonus` 0
   - `weak_generic/theme_broad/price_action_only`는 `kr_catalyst_bonus` 0
   - `direct_catalyst + prompt_eligible=True`만 bonus 가능

### P0-2. US 뉴스 timestamp/look-ahead 오염

실측:

7월 US 뉴스 후보 중 `top_news_json` 또는 `risk_news_json`에 `"age_min": -...`가 있는 행:

```text
US future age rows: 6,029 / news rows 15,233 = 39.6%
KR same query: 0
```

일자별 예시:

| session_date | news_rows | future_age_rows |
|---|---:|---:|
| 2026-07-01 | 743 | 182 |
| 2026-07-02 | 1,473 | 873 |
| 2026-07-08 | 1,141 | 501 |
| 2026-07-20 | 1,014 | 463 |
| 2026-07-22 | 1,575 | 590 |

코드 대조:

- `phase1_trainer/us_news_collector.py:297`: Finnhub timestamp를 `datetime.fromtimestamp(...).isoformat()`으로 저장한다. timezone offset 없는 naive string이다.
- `preopen/news_quality.py:249-263`: naive datetime을 UTC로 간주한다.
- `tools/collect_preopen_candidate_news.py` 쪽 파서는 naive 값을 KST로 취급하는 경로가 있어 같은 필드 해석이 불일치한다.

원인:

- 수집 시점에는 local timezone naive로 저장되고, 평가 시점에는 UTC naive로 해석된다.
- 결과적으로 아직 발생하지 않은 기사처럼 `age_min`이 음수가 된다.

시장별 판단:

- US: P0/P1. 뉴스 기반 후보 점수, prompt included, 후행 검증이 look-ahead로 오염될 수 있다.
- KR: 같은 실측 오염은 현재 0이지만, timestamp contract는 공통화해야 한다.

개선안:

1. 모든 `published_at`, `collected_at`, `applied_at`을 timezone-aware ISO로 저장한다.
   - 예: `datetime.fromtimestamp(ts, timezone.utc).isoformat()`
2. news quality 단계에서 `published_at <= collected_at/applied_at`을 강제한다.
3. 음수 age는 후보 점수에 쓰지 말고 `future_news_filtered_count`로 별도 저장한다.
4. historical 검증에서 `age_min < 0` 행은 제외하거나 contaminated로 라벨링한다.
5. 테스트:
   - US preopen fixture에서 naive Finnhub timestamp가 들어와도 음수 age가 발생하지 않아야 한다.
   - `published_at > applied_at` 뉴스는 prompt candidate에서 제외되어야 한다.

### P0-3. MFE/MAE sync가 기존 excursion 값을 지울 수 있다

실측:

`v2_learning_performance` live closed 중 excursion 보유:

| market | closed | learning excursion | canonical excursion | learning_not_canonical |
|---|---:|---:|---:|---:|
| KR | 62 | 62 | 16 | 46 |
| US | 255 | 253 | 57 | 196 |

`mfe_backfill_yf`:

| market | backfill rows | excursion rows | time rows | session range |
|---|---:|---:|---:|---|
| KR | 62 | 26 | 17 | 2026-04-27 ~ 2026-07-03 |
| US | 251 | 226 | 161 | 2026-04-27 ~ 2026-07-15 |

현재 sync 함수로 event payload만 다시 만들 경우:

```text
existing learning excursion: 315
event sync would keep: 73
event sync would lose: 242
KR loss: 46 / 62
US loss: 196 / 253
```

추가 실측:

```text
v2_learning_performance mfe_time rows: KR 0 / US 0
v2_learning_performance mae_time rows: KR 0 / US 0
v2_canonical_performance mfe_time rows: KR 0 / US 0
v2_canonical_performance mae_time rows: KR 0 / US 0
```

판정:

- 기존 리포트의 “mfe_time/mae_time 미수집”은 맞다.
- 추가로, 지금 full sync를 잘못 돌리면 기존 `mfe_pct/mae_pct`까지 사라질 수 있다.

개선안:

1. sync merge 우선순위를 명시한다.
   - event payload 값
   - `mfe_backfill_yf`
   - 기존 learning non-null 값
   - 그 외 null
2. `mfe_source`, `mae_source`, `mfe_time_source`, `mae_time_source`를 저장한다.
3. canonical 성과 원장도 learning과 excursion coverage가 불필요하게 벌어지지 않게 맞춘다.
4. `--dry-run`에서 “would null overwrite count”가 0이 아니면 production write 금지한다.

### P0-4. sleeve route/net attribution 공백

실측:

7월 sleeve 성격 rows:

| market | date | ticker | status | strategy | route | path_type | net |
|---|---|---|---|---|---|---|---|
| US | 2026-07-15 | SCHG | CLOSED | us_schg_bil_trend_v1 | unknown | empty | blank |
| US | 2026-07-16 | SCHG | FILLED | us_schg_bil_trend_v1 | unknown | empty | blank |
| KR | 2026-07-20 | 275280 | FILLED | kr_factor_trend_v1 | unknown | empty | blank |
| KR | 2026-07-21 | 275300 | FILLED | kr_factor_trend_v1 | unknown | empty | blank |

판정:

- 현재 실제 계좌를 움직이는 경로 중 하나가 candidate pipeline 밖에 있고, route/net이 비어 있다.
- 이건 수익 개선보다 위험 관리 문제다.

개선안:

1. sleeve 주문 발생 시 `ORDER_SENT`, `FILLED`, `CLOSED` 이벤트를 일반 주문과 같은 이벤트 스키마로 기록한다.
2. canonical에 `route=sleeve`, `path_type=trend_sleeve`, `strategy=...`, `origin_action=sleeve_signal`을 채운다.
3. net basis와 수수료/환율 basis를 명시한다.
4. sleeve는 candidate audit과 별도여도 되지만, v2 performance에는 반드시 동일 attribution contract로 들어와야 한다.

### P1-1. `cohort_reliability`는 state가 있는데 runtime/audit에서는 0이다

실측:

7월 후보 감사 원장:

| market | rows | runtime_gate key rows | runtime_gate nonzero | audit column nonzero | trainer key in payload |
|---|---:|---:|---:|---:|---:|
| KR | 23,550 | 9,927 | 0 | 0 | 0 |
| US | 69,602 | 23,036 | 0 | 0 | 0 |

반면 state 파일은 값이 있다.

| market | file | cohorts | nonzero cohorts | max abs score |
|---|---|---:|---:|---:|
| KR | `candidate_cohort_reliability_KR_20260722.json` | 8 | 4 | 0.9189 |
| US | `candidate_cohort_reliability_US_20260722.json` | 9 | 6 | 1.0000 |

예시 state key:

```text
KR|base_universe|volume_rank|unknown_liq|unknown_from_high|gap_pullback
US|base_universe|most_actives|unknown_liq|unknown_from_high|momentum
```

코드 대조:

- `trading_bot.py:2939-2948`: 후보에 `trainer_cohort_key`, `trainer_cohort_reliability`를 붙이는 경로가 있다.
- `trading_bot.py:6407-6445`: runtime gate에서 key가 없으면 다시 `_candidate_cohort_key()`를 만든다.
- `trading_bot.py:12848-12856`: state에서 key가 맞아야 score가 나온다.

원인 추정:

- state를 만들 때 쓰는 key와 runtime/audit 후보에 남는 key가 다르다.
- runtime/audit snapshot에는 `execution_fit_strategy`가 빠져 `unknown_strategy`류 key가 만들어지고, state에는 `gap_pullback`, `momentum`, `opening_range_pullback` 등이 들어 있다.
- 결과적으로 기능은 켜져 있으나 항상 0으로 작동한다.

개선안:

1. 후보 생성 시점의 `trainer_cohort_key`를 최종 route/audit까지 그대로 전달한다.
2. runtime에서 key 재생성 fallback을 쓰더라도 `recommended_strategy`/`raw_execution_fit_strategy`를 보완 입력으로 사용한다.
3. audit에 `trainer_cohort_key`, `trainer_cohort_reliability`, `cohort_key_source`를 별도 컬럼 또는 payload top-level로 저장한다.
4. 회귀 테스트:
   - state에 nonzero key가 있는 fixture가 audit/runtime_gate에서도 nonzero로 들어와야 한다.

### P1-2. `spread_bps`는 key만 있고 값은 0건이다

실측:

| market | rows | post_open missing | spread key rows | spread value rows | vwap value rows | rvol value rows |
|---|---:|---:|---:|---:|---:|---:|
| KR | 23,550 | 2,029 | 20,472 | 0 | 11,062 | 11,102 |
| US | 69,602 | 3,597 | 65,989 | 0 | 29,202 | 29,312 |

코드 대조:

- `runtime/post_open_features.py:243-245`: bid/ask가 있으면 spread 계산
- `runtime/live_evidence_pack.py:146-162`: `spread_bps`를 읽지만, 값이 없으면 `spread_ok=True`로 통과

판정:

- “spread_bps 배선 있음”은 맞지만, 7월 실측 값은 전부 null이다.
- 비용/슬리피지/유동성 판단에 쓸 수 없다.

시장별 판단:

- KR: 호가 단위/상하한/VI와 함께 중요하다. KIS quote/orderbook에서 채우는 것이 우선.
- US: NBBO 수준까지 아니더라도 현재 quote bid/ask snapshot이 필요하다. 없으면 spread gate는 shadow-only로 남겨야 한다.

개선안:

1. post-open feature snapshot 생성 전 quote provider에서 bid/ask를 채운다.
2. bid/ask가 없으면 `spread_status=missing_quote`를 저장한다.
3. evidence contract에서 “key 존재”가 아니라 “non-null value”를 검사한다.
4. `spread_bps is None`일 때 `spread_ok=True`로 통과시키는 현재 의미를 명시적으로 `spread_not_enforced_missing`으로 audit한다.

### P1-3. sector/ATR/rel_vol selection 데이터가 대부분 관측 밖이다

selection log 실측:

| market | rows | blank sector | rel_vol rows | atr rows |
|---|---:|---:|---:|---:|
| KR | 6,637 | 6,637 | 0 | 0 |
| US | 10,174 | 10,174 | 944 | 1 |

외부 파일 상태:

```text
data/sector_map.json: 없음
data/sector_map.json.disabled_until_restart: 있음, US 486 / KR 0
data/earnings_calendar.json: US 1,498 symbols, KR 10 codes
data/dart_corp_codes.json: 3,978 corp codes
```

코드 대조:

- `universe_manager.py:107-108`: `SECTOR_MAP_ENABLED`가 false면 sector lookup은 항상 빈 문자열 반환
- 주석상 false 기본값은 의도적이다. 과거 Technology concentration이 실제 수익에 기여했기 때문에 갑자기 sector cap을 켜면 후보 구성이 급변할 수 있다는 이유다.

판정:

- sector cap을 바로 켜면 안 된다.
- 하지만 sector를 “관측/노출/사후귀속”으로도 안 쓰는 것은 손실이다.
- ATR도 selection log에 거의 없고 candidate audit schema에는 없다.

시장별 개선안:

KR:

1. KRX/WICS 또는 내부 분류로 KR sector map을 만든다.
2. DART corp code는 있으나 실적 calendar가 아니다. KR `earnings_calendar` 10건은 “최근 공시성 데이터” 수준이라 proactive 실적 회피에는 부족하다.
3. 외국인/기관 수급 feature 코드는 존재하지만 candidate audit에는 `foreign_*`, `institution_*`, `foreign_flow_*`가 없다. KIS/KRX 일별 수급을 point-in-time으로 저장하고 audit까지 전달해야 한다.
4. ATR은 OHLCV로 사전 계산 가능하므로 selection/audit에 `atr_pct`, `atr_source`, `atr_lookback`을 저장한다.

US:

1. sector map US 486개는 있으나 비활성 파일이다. 먼저 shadow exposure report로만 쓴다.
2. earnings calendar는 2026-07-20~2026-08-06 근미래 1,498 symbols가 있어 운영 회피에는 쓸 수 있다. 다만 historical 검증에는 point-in-time backfill이 필요하다.
3. rel_vol은 2026-07-21 이후 일부만 들어온다. selection뿐 아니라 candidate audit까지 연결해야 trainer/recheck에서 쓸 수 있다.
4. ATR/spread는 매수 route와 submit layer가 같이 볼 수 있게 같은 snapshot에 저장한다.

### P1-4. candidate_source blank가 특정 경로에서 계속 발생한다

실측:

| market | source_file | rows | blank | blank_pct |
|---|---|---:|---:|---:|
| KR | trading_bot.screener_filter | 839 | 839 | 100.00% |
| KR | trading_bot.selection_meta | 11,487 | 1,203 | 10.47% |
| US | trading_bot.screener_filter | 1,737 | 1,737 | 100.00% |
| US | trading_bot.selection_meta | 25,597 | 1,928 | 7.53% |
| KR/US | prompt_pool / prompt_pool_excluded | 53,492 | 0 | 0.00% |

판정:

- prompt pool 이후는 source가 채워진다.
- screener_filter와 selection_meta 경로에서는 후보 source contract가 약하다.
- source가 비면 cohort key, 후보군별 성과, hard cap attribution이 흔들린다.

개선안:

1. audit store write 직전에 `candidate_source` fallback을 강제한다.
   - `candidate_source`
   - `source`
   - `source_type`
   - `source_file`
   - `source_tags[0]`
   - 그래도 없으면 `unknown:{source_file}`
2. `candidate_source_blank`을 audit violation으로 집계한다.
3. source blank row는 trainer cohort 계산에서 별도 bucket으로 격리한다.

### P1-5. counterfactual status contract가 분석 도구와 어긋날 수 있다

실측:

`candidate_counterfactual_paths` 7월 status:

| market | status | rows | with_outcome |
|---|---|---:|---:|
| KR | DATA_MISSING | 38,993 | 0 |
| KR | CLOSE_OUTCOME_FILLED | 34,583 | 34,583 |
| KR | BASELINE_NO_TRADE | 8,605 | 0 |
| KR | OUTCOME_PARTIAL | 472 | 472 |
| US | DATA_MISSING | 67,517 | 0 |
| US | CLOSE_OUTCOME_FILLED | 55,240 | 55,240 |
| US | BASELINE_NO_TRADE | 13,459 | 0 |
| US | OUTCOME_PARTIAL | 238 | 213 |

중요한 점:

```text
status='ok' rows = 0
```

판정:

- 유효 outcome이 `ok`가 아니라 `CLOSE_OUTCOME_FILLED`, `OUTCOME_PARTIAL` 같은 상태로 남는다.
- 분석 도구가 `status='ok'`를 기대하면 실측 outcome 전체를 놓친다.

개선안:

1. `outcome_complete_bool`, `outcome_partial_bool`, `missing_reason`을 별도 필드로 둔다.
2. status 문자열은 사람이 읽는 상태로 두고, 학습/분석은 boolean contract를 쓴다.
3. `DATA_MISSING`은 provider/가격/시간대/휴장/상장폐지 등 원인을 분리한다.

### P1-6. prompt pool reorder true 전환 근거 없음

문서 근거:

- `docs/reports/buy_strategy_fix_readiness_review_20260721.md:13`: 후보군 reorder와 Claude `BUY_READY` 직접매수 확대는 근거 부족으로 금지
- `docs/reports/buy_strategy_fix_readiness_review_20260721.md:175`: `CANDIDATE_PROMPT_POOL_REORDER_ENABLED=false`
- `docs/reports/buy_strategy_fix_readiness_review_20260721.md:182`: 전면 true 전환 근거 없음, shadow 누적 후 tail reorder만 검토
- `docs/reports/ultimate_profitability_review_20260721.md:69`: prompt reorder false 유지

이번 점검에서 추가된 반대 근거:

- `cohort_reliability`가 runtime/audit에서 0이라 trainer order 입력으로 쓸 수 없다.
- KR news catalyst bonus가 risk/weak에도 붙는다.
- US news timestamp가 look-ahead 오염 가능성이 있다.
- spread/sector/ATR 같은 비용·노출 feature가 값으로 전달되지 않는다.

판정:

- `CANDIDATE_PROMPT_POOL_REORDER_ENABLED=false`는 누락이 아니라 보수적으로 맞는 설정이다.
- 다른 모델이 제안한 `judge recheck queue sort shadow`는 이 플래그와 별개로 진행 가능하다.
- 단, recheck 정렬 feature에도 위 오염 feature를 바로 쓰면 안 된다. 우선은 “age, actionability, evidence completeness, price validity, previous judge uncertainty”처럼 contract가 살아 있는 feature만 써야 한다.

## 5. 외부/사전 데이터 보강 목록

### KR

| 항목 | 현재 상태 | 채울 수 있는가 | 우선순위 | 판단 |
|---|---|---|---|---|
| sector | active map 없음, disabled 파일도 KR 0 | 가능 | P1 | WICS/KRX/내부 분류로 point-in-time map 생성. cap enforce는 금지, 관측부터 |
| 실적 calendar | DART corp code 3,978, earnings_calendar KR 10 | 부분 가능 | P1 | DART 공시는 가능하지만 실적 예정 calendar로는 부족. 공시 이벤트와 실적 예정은 분리 |
| 외국인/기관 수급 | 코드 feature 존재, audit 전달 0 | 가능 | P1 | KIS/KRX 일별 투자자 수급을 후보 snapshot에 병합 |
| ATR/변동성 | selection/audit 거의 없음 | 가능 | P1 | OHLCV로 사전 계산 가능. route/submit 공통 snapshot 필요 |
| spread/호가 | post_open key는 있으나 값 0 | 가능 | P0/P1 | KIS quote/orderbook에서 bid/ask 저장 |
| 뉴스 polarity | news_signal_type 있음, signed score 없음 | 가능 | P0 | risk/weak/direct catalyst 분리. source 존재만 catalyst 금지 |

### US

| 항목 | 현재 상태 | 채울 수 있는가 | 우선순위 | 판단 |
|---|---|---|---|---|
| 뉴스 timestamp | `age_min < 0` 6,029 rows | 가능 | P0 | timezone-aware 저장과 future filter 필수 |
| earnings calendar | 1,498 symbols, 2026-07-20~2026-08-06 | 가능 | P1 | 운영 회피에는 사용 가능. backtest용 historical point-in-time 필요 |
| sector | disabled map US 486 | 가능 | P1 | cap enforce 금지, exposure 관측부터 |
| rel_vol | selection 944 rows, audit 연결 부족 | 가능 | P1 | selection → candidate audit → recheck/trainer 연결 |
| ATR/변동성 | selection 1 row | 가능 | P1 | OHLCV로 precompute |
| spread/호가 | 값 0 | 가능 | P1 | quote snapshot 필요 |
| short interest/float/fundamental | 현재 로컬 파일/스키마 없음 | 가능하지만 후순위 | P2 | 먼저 위 데이터 계약 복구 후 추가 |

## 6. 우선순위

| priority | 항목 | 이유 | 권장 처리 |
|---|---|---|---|
| P0 | KR news catalyst polarity | 켜진 config에서 점수 방향 오염 | 즉시 수정 + fixture |
| P0 | US news timestamp/look-ahead | 과거 검증과 후보 점수 오염 | timezone contract + future filter |
| P0 | MFE sync null overwrite 방지 | 기존 학습 label 손실 위험 | merge precedence + dry-run guard |
| P0 | sleeve route/net attribution | 실제 돈이 관측 밖 | 이벤트/성과 원장 통합 |
| P1 | cohort_reliability dead feature | state는 있는데 runtime 0 | key contract 단일화 |
| P1 | spread_bps value 0 | 비용/유동성 gate 무효 | bid/ask snapshot 저장 |
| P1 | sector/ATR/rel_vol 관측 | exposure/비용/변동성 분석 불가 | shadow 관측부터 |
| P1 | candidate_source blank | 후보군 성과 귀속 흔들림 | audit fallback + violation |
| P1 | counterfactual status contract | 분석 도구가 valid outcome 놓칠 수 있음 | boolean outcome contract |
| P2 | short/float/fundamental | 추가 alpha 가능하지만 현재 누수보다 후순위 | 데이터 계약 안정 후 |

## 7. 수정 후 반드시 볼 검증 지표

아래 지표가 0 또는 기대 범위로 떨어지지 않으면 전략 성능 분석으로 넘어가면 안 된다.

```text
KR risk_negative rows with kr_catalyst_bonus == 0
KR weak_generic/theme_broad/price_action_only rows with kr_catalyst_bonus == 0
US news rows with age_min < 0 == 0
cohort_reliability runtime_gate_nonzero > 0 when state has nonzero cohorts
spread_value_rows > 0, or spread_status missing reason explicitly logged
sync dry-run would_null_overwrite_mfe_mae == 0
sleeve rows route != unknown and net_basis != ''
candidate_source blank_pct < 1%
counterfactual outcome_complete_bool populated
```

## 8. 실행 방향

바로 매수 전략을 더 공격적으로 여는 순서는 아니다. 먼저 다음 순서가 맞다.

1. 뉴스 polarity/timestamp 수정
2. MFE sync null overwrite guard
3. sleeve route/net attribution
4. cohort key contract 복구
5. spread/sector/ATR/rel_vol 관측 복구
6. 그 다음 judge recheck sort shadow와 prompt reorder shadow를 분리해서 비교

`CANDIDATE_PROMPT_POOL_REORDER_ENABLED=true` 전환은 최소 조건이 필요하다.

- 위 P0가 닫혀야 한다.
- shadow reorder가 세션 단위 손익/기회비용 기준으로 기존 순서를 이겨야 한다.
- tail reorder처럼 제한된 구간부터 시작해야 한다.
- true 전환 전후 후보 구성 변화와 Claude actionable slot 변화가 리포트에 남아야 한다.

현재 결론: false 유지가 맞다.
