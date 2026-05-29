# Screener Diversity Trainer Development Requirements

Updated: 2026-05-28

## Purpose

이 문서는 기존 KR/US 스크리너를 유지하면서, 상위 랭커만 보는 구조 때문에 놓치는 중하위/다음 상승 후보를 관찰하고 후보군에 소량 편입하기 위한 트레이너형 개발 요구서다.

핵심 방향은 다음과 같다.

- 기존 raw screener와 Claude prompt cap을 무작정 키우지 않는다.
- 기존 상위 후보는 `CORE`로 유지한다.
- raw rank 중하위, 장전 후보, 후보감사 outcome, 외부 이벤트 데이터를 이용해 `CHALLENGER`와 `SCOUT` 후보를 별도 역할로 편입한다.
- 초기에는 매수 권한을 늘리지 않고 관찰, prompt 노출, PathB WAIT 검증 순서로 진행한다.
- forward/outcome 라벨은 live 입력에 직접 쓰지 않고, cohort prior와 승격 게이트 산출에만 쓴다.

## Non-Goals

- PathB live enable, 주문금액, 최대 포지션 수, daily entry cap, confidence, slippage cap, hard stop, broker truth 우선순위는 변경하지 않는다.
- `state/brain.json`에 자동 정책 메모리를 쓰지 않는다.
- 중하위 후보를 즉시 `trade_ready`로 승격하지 않는다.
- selection 품질 개선과 execution/risk 문제를 한 패치에서 섞지 않는다.
- PEAD/공시/뉴스 데이터는 입력 품질 기능으로만 사용하며 stop-loss, trailing stop, session-close logic을 override하지 않는다.

## Current System Read

### KR Screener

현재 KR raw screener는 KIS `volume-rank`를 KOSPI/KOSDAQ으로 나누어 호출한 뒤, 자체 점수로 다시 정렬한다.

```text
kr_screen_score =
log1p(price * volume)
+ max(change_rate, 0) * 2
+ vol_ratio * 4
```

KOSDAQ visibility를 보존하기 위해 기본 `KR_SCREEN_KOSDAQ_MIN_RATIO=0.35`가 적용된다. 따라서 KR 상위 후보는 단순 거래량 순위가 아니라 거래대금, 양수 등락률, 회전율, KOSDAQ 최소비중을 반영한 상위권이다.

관련 경로:

- `kis_api.py::_kis_volume_rank`
- `kis_api.py::_kr_screen_score`
- `kis_api.py::_merge_kr_market_buckets`
- `kis_api.py::screen_market_kr`

### US Screener

현재 US raw screener는 Yahoo `most_actives`, `day_gainers`, `day_losers`를 기본 소스로 쓰고 FMP fallback을 둔다. 기본 필터는 가격, 절대 등락률, 달러거래대금, loser 하락폭, 상품 제외 조건이다.

기본 NEUTRAL quota는 `actives 15 / gainers 10 / losers 5`이고, `top_n=80` 요청 시 비례 확대된다. 따라서 US 상위 후보는 활발한 종목, 상승 종목, 하락 종목을 quota로 섞은 후보군이다.

관련 경로:

- `kis_api.py::_yf_screen_candidates`
- `kis_api.py::_us_post_filter_with_stats`
- `kis_api.py::screen_market_us`
- `bot/candidate_policy.py::filter_tradable_candidates`

### Prompt Pool And Trainer

raw screener 후보는 그대로 Claude에게 들어가지 않는다. `runtime/candidate_prompt_pool.py`에서 trainer score를 붙이고, `PLAN_A > PLAN_B > WATCH > BENCH > QUARANTINE`, `trainer_prompt_score`, `trainer_risk_score`, `raw_rank` 순으로 정렬한다.

현재 prompt hard cap은 KR `32`, US `35`다. 이 cap이 상위권 집중을 만든다. raw 후보가 60~100개여도 실제 Claude 판단은 그중 32/35개에 집중된다.

관련 경로:

- `runtime/candidate_quality_trainer.py::score_candidate_for_trainer`
- `runtime/candidate_prompt_pool.py::build_trainer_prompt_pool`
- `minority_report/analysts.py`

### Preopen And Candidate Audit Assets

이미 장전 후보와 후보감사 데이터는 분리된 관찰 자산으로 존재한다.

- 장전 상태/로그: `state/preopen_*`, `logs/preopen/*`, `preopen/storage.py`
- 후보감사 DB: `data/audit/candidate_audit.db`
- selection/outcome 로그: `data/ticker_selection_log.db`
- counterfactual path: `candidate_counterfactual_paths`
- 보조 분석: `tools/full_profitability_review.py`, `tools/simulate_candidate_improvement.py`

이 구조는 “기존 스크리너를 유지하면서 아래층 후보를 별도 관찰하는 트레이너”를 만들기에 적합하다.

## Local Evidence Snapshot

분석 기준은 2026-05-28 로컬 DB와 로그다. 수치는 live 데이터 기준이며, 표본이 작은 bucket은 승격 근거가 아니라 관찰 힌트로만 본다.

### Candidate Audit Coverage

`audit_candidate_rows` 기준:

| market | rows | days | screener_seen | prompt_seen | pnl_rows |
|---|---:|---:|---:|---:|---:|
| KR | 16,506 | 25 | 14,282 | 8,581 | 468 |
| US | 18,799 | 28 | 15,777 | 8,900 | 492 |

`audit_candidate_outcomes`는 30m/60m 라벨이 주로 채워져 있고, 1D/2D/3D row는 있으나 return 값이 비어 있다. 따라서 P0에서 1D/3D outcome backfill 또는 별도 label join을 먼저 보강해야 한다.

### Rank Bucket Short-Horizon Outcomes

후보감사 DB 30m/60m 기준:

| market | rank bucket | candidates | prompt rows | ret_30m | ret_60m | mfe_60m | mae_60m |
|---|---:|---:|---:|---:|---:|---:|---:|
| KR | r01_10 | 3,192 | 4,682 | 0.192 | -0.022 | 2.021 | -1.738 |
| KR | r11_30 | 5,367 | 7,654 | 0.350 | 0.245 | 2.051 | -1.446 |
| KR | r31_60 | 4,583 | 2,933 | 0.356 | 0.250 | 1.556 | -0.993 |
| KR | r61_100 | 344 | 173 | -0.415 | -0.445 | 2.531 | -2.812 |
| US | r01_10 | 3,367 | 4,549 | 0.181 | 0.076 | 0.758 | -0.639 |
| US | r11_30 | 5,725 | 7,021 | 0.235 | 0.287 | 0.859 | -0.504 |
| US | r31_60 | 4,876 | 4,627 | 0.351 | 0.547 | 1.102 | -0.219 |
| US | r61_100 | 1,755 | 437 | 0.084 | -0.032 | 0.607 | -0.568 |

해석:

- KR/US 모두 `r31_60`에서 60분 outcome이 죽지 않는다.
- US는 `r31_60`이 60분 기준으로 상위 rank보다 좋게 나온다.
- `r61_100`은 KR/US 모두 신뢰도가 낮거나 불안정하다. 초기 challenger 대상은 `r31_60`이 적절하다.

### Prompt Included vs Not Prompt

| market | prompt flag | candidates | ret_30m | ret_60m | mfe_60m | mae_60m |
|---|---:|---:|---:|---:|---:|---:|
| KR | prompt | 8,581 | 0.390 | 0.262 | 2.088 | -1.456 |
| KR | not_prompt | 7,925 | 0.134 | 0.087 | 1.603 | -1.255 |
| US | prompt | 8,900 | 0.265 | 0.326 | 0.930 | -0.425 |
| US | not_prompt | 9,899 | 0.155 | 0.221 | 0.824 | -0.496 |

해석:

- prompt 포함 후보가 평균적으로 더 좋다. 기존 trainer는 완전히 틀린 구조가 아니다.
- 그러나 not-prompt 후보도 특히 US에서 양수 60m/MFE가 있다. 즉 상위권 밖에 버릴 수 없는 탐색 후보가 있다.

### Ticker Selection Longer-Horizon Labels

`ticker_selection_log`는 selection prompt에 들어온 후보 중심이라 rank 20 이후 coverage가 약하다. 그래도 현재 prompt 내부에서 KR은 rank 11~20이 rank 1~10보다 3D가 좋다.

| market | rank bucket | n | f1 | f3 | f5 | mfe3 | mae3 |
|---|---:|---:|---:|---:|---:|---:|---:|
| KR | r01_10 | 988 | 0.046 | 1.591 | -3.870 | 16.932 | -8.989 |
| KR | r11_20 | 467 | 0.460 | 2.298 | -3.210 | 15.373 | -8.255 |
| US | r01_10 | 1329 | 1.159 | 2.989 | 2.505 | 7.892 | -4.935 |
| US | r11_20 | 664 | 0.536 | 0.229 | 0.020 | 5.293 | -5.911 |

해석:

- US는 기존 상위권이 강하다. US challenger는 상위권 대체가 아니라 보조 슬롯이 맞다.
- KR은 “최상위만 우선”보다 “상위권 내부에서도 중간 랭크/눌림/확인형”을 더 봐야 한다.

### Preopen Simulation Signal

`tools/simulate_candidate_improvement.py`가 생성한 보조 리포트 기준:

- coverage: preopen state 1,863 rows, KR 902 / US 961
- KR `low_liq_ignite60`: n=54, avg final return `12.7902%`, profit factor `143.9157`
- KR `late_reclaim`: n=13, avg final return `12.5734%`
- US `late_reclaim`: n=27, avg final return `5.3583%`

주의:

- 이 값은 tick-level fill simulation이 아니라 sampled outcome approximation이다.
- 표본이 작고 survivor/selection bias가 있을 수 있다.
- 바로 매수 승격이 아니라 `SCOUT`/`WATCH_TRIGGER` 후보 생성 근거로만 사용해야 한다.

보조 산출물:

- `docs/reports/candidate_improvement_simulation_20260528_014834.md`
- `docs/reports/candidate_improvement_simulation_20260528_014834.json`

## Additional Verification From Review

### Rank Semantics Gap

현재 `audit_candidate_rows.raw_rank`는 이미 일부 채워져 있지만, 의미가 명확히 분리되어 있지 않다.

- KR/US `screen_market_kr/us()`가 반환하는 row에는 provider 원천 순위가 직접 저장되지 않는다.
- `runtime/candidate_prompt_pool.py::build_trainer_prompt_pool()`는 입력 후보 순서를 기준으로 `raw_rank`를 기본 세팅한다.
- 장전 후보는 `provider_rank`, `shadow_preopen_rank`가 별도로 있다.
- 후보감사 DB에는 `raw_rank`, `trainer_score_rank`, `prompt_rank`, `actual_prompt_rank`가 섞여 존재한다.

따라서 이 문서의 `raw_rank_band`는 Phase 0에서 다음 rank 의미를 분리한 뒤 사용해야 한다.

| field | meaning | initial use |
|---|---|---|
| `provider_rank` | KIS/Yahoo/FMP/OpenDART 등 원천 provider 내부 순위 | source 품질 분석 |
| `category_rank` | US `most_actives/day_gainers/day_losers`, KR KOSPI/KOSDAQ bucket 내부 순위 | category별 편향 분석 |
| `screen_rank` | product filter, KOSPI/KOSDAQ merge, quota merge 이후의 최종 screener 순위 | `raw_rank_band`의 기준 |
| `trainer_input_rank` | trainer prompt pool에 들어간 입력 순서. 기존 `raw_rank`와 호환 | legacy 비교 |
| `trainer_score_rank` | trainer score 정렬 이후 순위 | trainer quality 분석 |
| `prompt_rank` / `actual_prompt_rank` | Claude prompt 포함 순위 | prompt visibility 분석 |

P1의 `raw_rank_band`는 반드시 `screen_rank` 기준으로 계산한다. `screen_rank`가 없는 과거 row는 `raw_rank`를 legacy fallback으로만 쓰고 report에 `rank_source=legacy_raw_rank`를 표시한다.

### Cohort Dimensionality Check

리뷰 지적대로 9차원 cohort key는 초기 표본을 과도하게 쪼갠다. 2026-05-28 로컬 후보감사 DB에서 차원별 표본 분포는 다음과 같았다.

| dims | market | cohorts | avg n | cohorts n>=20 | cohorts n>=50 |
|---|---|---:|---:|---:|---:|
| 3 dims: rank_band + primary_bucket + liquidity | KR | 100 | 167.5 | 72 | 57 |
| 3 dims: rank_band + primary_bucket + liquidity | US | 103 | 185.2 | 75 | 52 |
| 4 dims: + from_high_bin | KR | 214 | 78.3 | 121 | 75 |
| 4 dims: + from_high_bin | US | 219 | 87.1 | 124 | 75 |
| 5 dims: + board/category | KR | 388 | 43.2 | 159 | 92 |
| 5 dims: + board/category | US | 219 | 87.1 | 124 | 75 |
| 6 dims: + source_file | KR | 1,769 | 9.5 | 175 | 77 |
| 6 dims: + source_file | US | 1,365 | 14.0 | 162 | 74 |

초기 cohort prior는 3~4차원만 사용한다. 5차원 이상은 diagnostic/report 전용이며, `n>=50`과 top-day concentration gate를 통과할 때만 promotion 설명에 사용할 수 있다.

### Regime-Controlled Interpretation

KR `r31_60`의 60분 outcome이 `r01_10`보다 좋아 보인다고 해서 상위권이 나쁘다는 결론을 내리면 안 된다. rank bucket 비교는 반드시 다음 상대 기준을 함께 계산한다.

- same market/session/call의 CORE median 대비 초과수익
- 같은 session의 market index 대비 초과수익
- 실제 prompt에 들어간 CORE 최하위 후보와 challenger 후보의 pairwise 비교
- market mode, phase, source, data_quality별 stratified 비교

promotion gate는 절대 ret_60m만으로 열리지 않는다.

### PEAD And Catalyst Boundary

`catalyst_score`는 PEAD 정책 gate를 우회할 수 없다.

- `surprise_sign`, `surprise_strength`는 기존 PEAD shadow/manual-review gate를 통과하기 전까지 prompt-visible feature로 올리지 않는다.
- KR은 구조화된 actual/estimate가 없으면 `surprise_sign=unknown`을 유지한다.
- 뉴스/DART 제목만으로 EPS beat/miss를 추론하지 않는다.
- catalyst는 `event_present`, `source_overlap`, `recency`, `disclosure_type` 같은 입력 품질 신호로만 점수화한다.
- event/catalyst 단독으로 `CHALLENGER` 이상 승격하거나 `trade_ready`를 만들 수 없다.

### Phase Gate Tightening

Phase 0이 완료되기 전 Phase 1을 시작하지 않는다. Phase 0 완료 기준은 다음 수치 조건을 모두 만족해야 한다.

- 최근 10 trading sessions 기준 `screen_rank` coverage >= 95%.
- actual prompt visibility measured rows >= 90%.
- eligible candidate 1D and 3D outcome label non-null rate >= 80%.
- market별 `r31_60` 후보의 30m/60m/1D/3D report가 모두 생성됨.
- promotion 후보 cohort는 primary cohort 기준 `n>=50`, diagnostic cohort 기준 `n>=20`.
- top-day contribution < 40%.
- report가 absolute return과 same-session excess return을 모두 표시함.

## External Research And Data Sources

외부 근거는 “상위 모멘텀만 보는 구조를 유지하되, 거래량/관심도/이벤트/탐색 슬롯을 함께 둬야 한다”는 방향을 지지한다.

- Momentum: Jegadeesh and Titman(1993)은 과거 winners가 3~12개월 보유 구간에서 양의 수익을 낸다는 고전적 momentum 근거를 제시한다. 단, 이 근거는 이미 뜨거운 종목 추적의 타당성을 말할 뿐, 당일 상위 rank만 보라는 뜻은 아니다. Source: <https://ideas.repec.org/a/bla/jfinan/v48y1993i1p65-91.html>
- Volume and momentum lifecycle: Lee and Swaminathan(1998/2000)은 과거 거래량이 향후 momentum의 크기와 지속성을 예측한다고 보고한다. 특히 volume은 investor interest와 reversal imminence를 간접적으로 알려준다. Source: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=92589>
- Exploration vs exploitation: diversified recommendation 연구는 유사도/기존 상위 후보만 보면 관심 영역이 좁아질 수 있고, contextual bandit 방식으로 exploration/exploitation을 균형 있게 선택할 수 있다고 설명한다. 이 시스템에서는 `CORE`가 exploitation, `CHALLENGER/SCOUT`가 exploration이다. Source: <https://epubs.siam.org/doi/10.1137/1.9781611973440.53>
- PEAD/event drift: Bernard and Thomas(1989)는 post-earnings-announcement drift의 대표 근거다. 이 repo의 PEAD boundary에 맞춰 독립 전략이 아니라 입력 품질 feature로만 사용해야 한다. Source: <https://cir.nii.ac.jp/crid/1360576121027825152>
- US data: SEC EDGAR API는 submissions와 XBRL company facts를 JSON으로 제공하고, 인증키 없이 사용할 수 있다. 단, User-Agent와 SEC access policy를 준수해야 한다. Source: <https://www.sec.gov/search-filings/edgar-application-programming-interfaces>
- KR data: OpenDART는 DART 원문 공시, 주요 공시, 정기보고서 재무정보, 대량 재무정보를 API로 제공한다. Source: <https://engopendart.fss.or.kr/intro/main.do>
- KR price/volume: 공공데이터포털의 금융위원회 주식시세정보는 KRX 제공 가격, 거래량, 거래대금 분석에 활용 가능하나 일부 업데이트 지연이 있으므로 live broker/KIS truth와 섞으면 안 된다. Source: <https://www.data.go.kr/en/data/15094808/openapi.do>

## Target Architecture

### Candidate Roles

후보군을 하나의 순위 리스트로만 보지 않고 역할을 분리한다.

| role | source | prompt behavior | execution permission |
|---|---|---|---|
| `CORE` | 기존 screener 상위 + trainer 상위 | 기존과 동일 | 기존 routing/risk만 허용 |
| `CHALLENGER` | screen rank 31~60, preopen pin, cohort prior 우수, source consensus | prompt에 소량 포함 | 초기에는 WATCH/PathB WAIT 중심 |
| `SCOUT` | 더 낮은 rank, 장전 low-liq/late-reclaim, 이벤트 후보 | prompt에는 요약 또는 shadow log | 주문 금지 |
| `QUARANTINE` | data bad, ETF/파생, hard safety, stale truth | prompt 제외 | 주문 금지 |

### Prompt Budget

초기에는 hard cap을 늘리지 않고 재배분한다.

권장 초기값:

| market | hard cap | core slots | challenger slots | scout prompt |
|---|---:|---:|---:|---|
| KR | 32 | 26~28 | 4~6 | summary only |
| US | 35 | 29~31 | 4~6 | summary only |

장점:

- Claude 토큰/판단 폭증을 막는다.
- 기존 상위 후보 품질을 유지한다.
- challenger 효과를 실제 prompt 포함/제외 outcome으로 비교할 수 있다.

단점:

- core 후보 일부가 밀릴 수 있다.
- challenger 선정 기준이 약하면 noise를 넣는 결과가 된다.

완화:

- challenger는 core 최하위 일부와 비교해 cohort score가 높을 때만 편입한다.
- 각 challenger row에 `exploration_reason`, `cohort_key`, `promotion_gate_state`를 반드시 붙인다.

## Discovery Feature Design

### Cohort Key

후보를 ticker 단위가 아니라 bucket/cohort 단위로 학습한다. 다만 초기 live-adjacent prior는 낮은 차원으로 시작한다.

```text
primary_cohort_key =
market
+ screen_rank_band
+ primary_bucket
+ liquidity_bucket
```

선택적 diagnostic 차원:

```text
diagnostic_cohort_key =
primary_cohort_key
+ from_high_bin
+ market_type/category
+ phase
+ source_overlap_bucket
+ catalyst_bucket
```

운영 규칙:

- Phase 0/1의 `cohort_prior_score`는 `primary_cohort_key`만 사용한다.
- `from_high_bin`은 `from_high_bucket` 컬럼이 없으면 `from_high_pct`에서 계산한다.
- `market_type/category`, `phase`, `source_overlap`, `catalyst`는 초기에는 점수 feature 또는 report split으로만 사용한다.
- diagnostic key는 `n>=50`일 때만 prior로 승격 검토할 수 있다.

초기 예시:

- `KR:r31_60:near_breakout:low`
- `US:r31_60:momentum_now:high`
- `KR:preopen:low_liq_ignite60:confirmed_60m`
- `US:late_reclaim:post_open:mid_liq`

### Discovery Score

live 후보 점수는 미래 라벨을 직접 읽으면 안 된다. 따라서 과거 rolling cohort 통계만 prior로 사용한다.

```text
discovery_score =
current_signal_score
+ cohort_prior_score
+ source_consensus_score
+ catalyst_score
+ confirmation_score
+ exploration_bonus
- risk_penalty
- data_quality_penalty
- chase_penalty
```

필수 원칙:

- `forward_1d/3d/5d`, `return_pct`, `mfe/mae`는 live row scoring에 직접 사용하지 않는다.
- rolling cutoff는 `known_at < current_known_at` 조건을 강제한다.
- sample size가 작으면 prior를 shrink한다.
- top-day contribution이 크면 승격을 막는다.
- `catalyst_score`는 PEAD gate를 우회하지 않으며, `surprise_sign`/`surprise_strength`를 prompt-visible로 승격하지 않는다.

### Exploration Bonus

상위권을 완전히 고착시키지 않기 위해 bandit식 탐색 보너스를 둔다.

간단한 초기식:

```text
exploration_bonus =
sqrt(log(total_cohort_observations + 1) / (cohort_observations + 1))
* EXPLORATION_WEIGHT
```

운영 제약:

- market별 challenger slots를 초과할 수 없다.
- 하루 동일 cohort 최대 1~2개만 허용한다.
- `data_quality_bad`, `liquidity_too_thin`, `wide_spread`, `broker_truth_untrusted`는 exploration bonus보다 우선한다.

## Candidate Sources To Add Without Replacing Existing Screener

### 1. Raw Rank Band Sampler

목적:

- raw rank 31~60에서 prompt cap 때문에 잘리는 후보를 소량 승격한다.

요구:

- raw screener row에 `screen_rank`와 `raw_rank_band`를 저장한다.
- `provider_rank`와 `category_rank`는 있으면 보존하되, challenger band 기준은 `screen_rank`로 통일한다.
- `r31_60` 중 `trainer_candidate_state != QUARANTINE`이고 `cohort_prior_score`가 양수인 후보만 challenger eligible로 둔다.
- `r61_100`은 초기에는 scout-only로 둔다.
- legacy row에서 `screen_rank`가 없으면 report-only fallback으로 기존 `raw_rank`를 쓰고, live 역할 부여에는 사용하지 않는다.

근거:

- 후보감사 DB에서 US `r31_60`의 60m return/MFE가 강했다.
- KR `r31_60`도 `r01_10`보다 60m return과 MAE가 나쁘지 않았다.

장점:

- 기존 raw screener를 유지하면서 아래층을 볼 수 있다.

단점:

- 하위 rank로 갈수록 데이터 품질과 체결 품질이 나빠질 수 있다.

### 2. Preopen Ignition And Late Reclaim

목적:

- 장전 후보 중 기존 intraday top rank에 늦게 반영되는 후보를 scout/watch로 유지한다.

요구:

- `low_liq_ignite60`는 `SCOUT`으로 시작하고 60분 confirmation 후 `CHALLENGER`로만 승격한다.
- `late_reclaim_watch`는 90~120분 reclaim 조건이 맞을 때 PathB WAIT 후보로만 편입한다.
- 장전 후보는 preopen phase에서는 watch-only를 강제한다.

장점:

- 기존 top screener가 놓치는 초기/후행 움직임을 잡는다.

단점:

- KR low-liq는 slippage/VI/호가 공백 위험이 크다.

### 3. Sector Relay Candidate

목적:

- 대장주가 이미 과열된 섹터에서 2등/3등이 뒤늦게 움직이는 상황을 잡는다.

요구:

- 같은 sector/theme 내 core 후보가 `at_high` 또는 chase risk일 때, raw rank 31~80의 같은 sector 후보 중 `from_high_bucket`이 덜 과열된 종목을 scout로 지정한다.
- 단, sector 정보가 blank면 적용하지 않는다.
- sector relay는 처음부터 trade_ready를 만들 수 없다.

장점:

- “비슷비슷한 대장주 반복” 문제를 완화한다.

단점:

- sector tagging 품질이 낮으면 noise가 증가한다.

### 4. Source Consensus Candidate

목적:

- 여러 소스에서 반복적으로 보이지만 prompt cap 때문에 밀린 후보를 잡는다.

소스:

- raw screener
- preopen state
- candidate audit repeated seen
- price collection priority
- US Yahoo/FMP/KIS ranking shadow overlap
- KR KIS/KRX/DART event tag

요구:

- `source_overlap_count >= 2`면 challenger 점수에 보너스를 준다.
- source disagreement는 `candidate_source_disagreement`로 audit에 남긴다.
- KIS broker truth와 Yahoo/FMP context data를 섞어 broker truth처럼 취급하지 않는다.

### 5. Event/Catalyst Candidate

목적:

- 실적, 공시, 거래량 동반 뉴스가 아직 screener 상위에는 안 올라왔지만 다음 상승 후보가 될 수 있는 경우를 관찰한다.

US 후보 소스:

- SEC submissions/companyfacts
- earnings calendar provider
- existing news enrichment

KR 후보 소스:

- OpenDART disclosure list/detail
- KRX/public price/volume EOD data
- existing news/DART title tags

요구:

- event는 `catalyst_score`로만 쓰고 단독 매수 조건이 될 수 없다.
- KR은 구조화된 actual/estimate가 없으면 `surprise_sign=unknown`을 유지한다.
- US도 EPS beat/miss가 불명확하면 catalyst_present만 둔다.
- `catalyst_score`는 `event_present`, `source_overlap`, `recency`, `disclosure_type` 같은 입력 품질 신호만 반영한다.
- `surprise_sign`/`surprise_strength`는 PEAD shadow와 수동 검토 gate 전까지 discovery score와 prompt-visible field에 포함하지 않는다.
- event/catalyst만으로 `SCOUT`에서 `CHALLENGER`로 승격할 수 없으며, 반드시 price/volume confirmation 또는 source consensus가 함께 필요하다.

## Development Requirements

### P0. Measurement And Label Integrity

#### P0-1. Outcome Label Backfill

문제:

- `audit_candidate_outcomes`의 1D/2D/3D row는 있으나 return 값이 비어 있다.
- 후보 diversity 평가가 30m/60m에 치우칠 위험이 있다.

요구:

1. `tools/update_candidate_audit_outcomes.py`가 1D/2D/3D return, MFE, MAE를 실제 가격 CSV/DB에서 채우도록 보강한다.
2. label source, price source, missing reason을 `payload_json`에 남긴다.
3. 1D/3D 라벨이 없는 후보는 promotion gate에서 제외한다.
4. Phase 1 착수 전 최근 10 trading sessions의 eligible candidate 기준 1D/3D non-null label rate가 각각 80% 이상이어야 한다.
5. 1D/3D label coverage가 80% 미만이면 report는 생성하되 모든 promotion gate를 `blocked_label_coverage`로 닫는다.

검증:

```powershell
python tools/update_candidate_audit_outcomes.py --mode live
sqlite3 data/audit/candidate_audit.db "select horizon_min, count(*), sum(return_pct is not null) from audit_candidate_outcomes group by 1"
```

#### P0-2. Screener Diversity Analyzer

요구:

새 도구를 추가한다.

```text
tools/analyze_screener_diversity.py
```

입력:

- `data/audit/candidate_audit.db`
- `data/ticker_selection_log.db`
- `state/preopen_*`
- `logs/preopen/*`

출력:

- `docs/reports/screener_diversity_YYYYMMDD_HHMMSS.md`
- `docs/reports/screener_diversity_YYYYMMDD_HHMMSS.json`

필수 섹션:

- raw rank band 성과
- `rank_source`별 coverage: `screen_rank`, `legacy_raw_rank`, `provider_rank`, `prompt_rank`
- prompt included vs excluded 성과
- preopen bucket 성과
- source overlap 성과
- cohort prior table
- primary cohort와 diagnostic cohort 표본 분포
- same-session excess return
- same-call CORE replacement comparison
- top-day concentration
- candidate samples
- promotion blockers

#### P0-3. Audit Field Expansion

`audit_candidate_rows` 또는 payload에 다음 field를 보존한다.

- `candidate_pool_role`: `CORE|CHALLENGER|SCOUT|QUARANTINE`
- `provider_rank`
- `category_rank`
- `screen_rank`
- `trainer_input_rank`
- `raw_rank_band`
- `rank_source`
- `cohort_key`
- `primary_cohort_key`
- `diagnostic_cohort_key`
- `cohort_prior_score`
- `cohort_observation_count`
- `cohort_win_rate`
- `cohort_avg_return_60m`
- `cohort_avg_return_1d`
- `cohort_avg_mfe_3d`
- `cohort_avg_mae_3d`
- `exploration_score`
- `exploration_reason`
- `source_overlap_count`
- `source_disagreement_json`
- `promotion_gate_state`
- `promotion_gate_reason`

수용 기준:

- blank `primary_bucket/source` 비율이 market별 10% 미만이거나 report에 원인별로 문서화된다.
- actual prompt included/excluded outcome이 timestamp-nearest가 아니라 `actual_prompt_*` 기준으로 집계된다.
- 최근 10 trading sessions 기준 `screen_rank` coverage가 95% 이상이다.
- `raw_rank_band`가 `screen_rank` 기준인지 legacy fallback 기준인지 `rank_source`로 구분된다.

### P1. Role-Aware Prompt Pool

#### P1-1. Candidate Role Builder

새 모듈 후보:

```text
runtime/candidate_discovery_pool.py
```

역할:

- raw candidates에 role을 붙인다.
- core/challenger/scout quota를 계산한다.
- duplicate ticker는 가장 높은 role/score를 보존하고 source_tags를 merge한다.

초기 정책:

```text
CORE:
  trainer order 상위 N개

CHALLENGER:
  raw_rank_band in r31_60
  not QUARANTINE
  data_quality good
  cohort_prior_score >= threshold
  same-day stopped false

SCOUT:
  preopen low_liq_ignite60
  late_reclaim_watch
  event/catalyst present
  r61_100 source_overlap_count >= 2
```

#### P1-2. Prompt Pool Reallocation

`runtime/candidate_prompt_pool.py`에 role-aware selection을 추가한다.

요구:

- hard cap은 유지한다.
- core slots와 challenger slots를 분리한다.
- challenger가 없으면 core로 채운다.
- challenger가 들어오면 `prompt_pool_role`, `exploration_reason`을 prompt line에 표시한다.
- scout는 기본적으로 full row를 prompt에 넣지 않고 summary만 제공한다.

예시:

```text
KR hard_cap=32
core_slots=27
challenger_slots=5
```

수용 기준:

- 기존 hard cap 초과 없음.
- prompt에 들어간 challenger 수가 audit에 기록된다.
- 기존 `PLAN_A/PLAN_B/WATCH` state ordering과 same-day stop last rule을 보존한다.

#### P1-3. Claude Prompt Contract

`minority_report/analysts.py` prompt에 다음 계약을 추가한다.

```text
CORE candidates are existing screener leaders.
CHALLENGER candidates are discovery candidates from lower raw ranks or preopen/event cohorts.
Do not promote CHALLENGER to trade_ready unless it has live confirmation that beats a comparable CORE candidate.
SCOUT candidates are observation-only and must not be trade_ready.
```

Claude output에는 challenger 선택 사유가 필요하다.

```json
{
  "ticker": "T",
  "candidate_role": "CHALLENGER",
  "promotion_reason": "sector relay + volume confirmation",
  "replaced_core_ticker": "X",
  "confirmation_needed": ["vwap_reclaim", "volume_ratio_open"]
}
```

### P2. Confirmation And Limited Live Experiment

#### P2-1. WATCH_TRIGGER / PathB WAIT Gate

CHALLENGER는 초기 live에서 다음 권한만 갖는다.

- `WATCH`
- `PULLBACK_WAIT`
- `PathB WAIT`
- `SCOUT summary`

금지:

- 즉시 Plan A BUY
- risk gate bypass
- hard stop 완화
- same-day reentry block 우회

승격 조건:

- 10 trading days shadow
- market별 50 labeled challenger outcomes
- top-day contribution < 40%
- challenger cohort PF > core replacement PF
- MAE가 core보다 악화되지 않음
- actual prompt included 기준으로 측정

#### P2-2. Micro-Probe Boundary

중하위 후보 regular sizing 승격은 금지한다. 필요하면 `MICRO_PROBE` 별도 실험 경로만 허용한다.

조건:

- 최소 30 filled probes 또는 4 calendar weeks
- regular strategy 성과와 분리
- 후보군 role, cohort, source, confirmation reason이 DB에 남아야 함

## Pros And Cons

### 장점

- 기존 스크리너의 강한 후보 포착 능력을 유지한다.
- prompt cap을 무작정 늘리지 않아 Claude 비용과 판단 노이즈를 통제한다.
- 상위 랭커 밖의 수익성 bucket을 데이터로 확인할 수 있다.
- “종목이 비슷비슷한 문제”를 sector relay, source consensus, preopen scout로 완화한다.
- 후보 품질 개선과 execution/risk 변경을 분리한다.

### 단점

- 초기에는 관찰/라벨링 중심이라 즉시 수익 개선이 크지 않을 수 있다.
- cohort prior가 과최적화되면 하위 rank noise를 좋은 후보로 오인할 수 있다.
- KR low-liq 후보는 수익률이 좋아 보여도 실제 체결/호가/slippage 위험이 크다.
- 외부 이벤트 데이터는 결측, 지연, provider 차이가 크다.
- prompt가 role-aware로 복잡해지면 Claude output normalize 테스트가 필요하다.

### 주요 리스크와 방지책

| risk | mitigation |
|---|---|
| lookahead leakage | live scoring은 rolling cutoff 이전 cohort prior만 사용 |
| overfitting | sample size, top-day concentration, market split gate |
| prompt overload | hard cap 유지, scout summary only |
| execution contamination | challenger 초기 WATCH/WAIT only |
| data provider instability | source tag와 data_quality 기록, broker truth와 context data 분리 |
| KR low-liq slippage | micro-probe or shadow only, spread/turnover/VI guard |
| repeated same-sector crowding | sector/day/correlation cap |

## Implementation Plan

### Phase 0: Report-Only

수정 파일:

- `tools/analyze_screener_diversity.py`
- `tools/update_candidate_audit_outcomes.py`
- `tests/test_screener_diversity_report.py`
- `tests/test_candidate_audit_outcome_labels.py`

검증:

```powershell
python tools/analyze_screener_diversity.py --mode live --output-dir docs/reports
python -m pytest tests/test_candidate_audit.py tests/test_screener_quality.py -q
```

완료 기준:

- rank band별 30m/60m/1D/3D 성과가 report에 나온다.
- prompt included/excluded가 actual prompt 기준으로 집계된다.
- `r31_60` 후보의 market별 outcome이 확인된다.

### Phase 1: Shadow Role Assignment

수정 파일:

- `runtime/candidate_discovery_pool.py`
- `runtime/candidate_prompt_pool.py`
- `audit/candidate_audit_store.py`
- `bot/screener_quality.py`
- 관련 tests

검증:

```powershell
python -m pytest tests/test_candidate_quality_trainer.py tests/test_screener_quality.py tests/test_candidate_audit.py -q
```

완료 기준:

- 모든 prompt row에 `candidate_pool_role`이 있다.
- challenger/scout가 audit에는 기록되지만 live 주문 권한은 변하지 않는다.
- hard cap이 유지된다.

### Phase 2: Prompt Challenger Overlay

수정 파일:

- `minority_report/analysts.py`
- `runtime/candidate_prompt_pool.py`
- `runtime/selection_compact_schema.py`
- `bot/candidate_policy.py`
- 관련 tests

검증:

```powershell
python -m pytest tests/test_candidate_quality_trainer.py tests/test_candidate_action_live_mapping.py tests/test_trading_bot_intraday_evidence.py -q
```

완료 기준:

- Claude prompt에 core/challenger 구분이 보인다.
- challenger가 trade_ready가 되어도 runtime route에서 WATCH/WAIT gate가 적용된다.
- scout는 trade_ready로 normalize되지 않는다.

### Phase 3: Limited Live Experiment

전제:

- Phase 0/1/2 report가 최소 10 trading days 축적된다.
- market별 challenger labeled outcome 50개 이상.
- PnL truth/canonical freshness 경고가 없다.

허용:

- PathB WAIT 후보 소량 편입
- MICRO_PROBE 별도 실험

금지:

- regular sizing 승격
- risk/sizing/stop 완화
- broker truth fallback 변경

## Acceptance Criteria

1. 기존 raw screener 호출 폭과 product filter가 유지된다.
2. prompt hard cap KR 32 / US 35가 유지되거나, 변경 시 별도 운영 승인과 토큰 영향 보고가 있다.
3. challenger slots는 market별 4~6개를 넘지 않는다.
4. `SCOUT`는 주문으로 연결되지 않는다.
5. 1D/3D label은 live scoring에 직접 쓰이지 않는다.
6. all changes are observable through audit/report.
7. `state/brain.json` 자동 변경 경로가 없다.
8. PathB auto-sell cooldown, broker-truth fail-closed, sizing reason split, zero-holding stale reconcile, KIS order normalization, RouteDecision contract를 건드리지 않는다.
9. Phase 0 완료 전 Phase 1 role assignment를 시작하지 않는다.
10. Phase 0 완료 기준은 `screen_rank` coverage >= 95%, actual prompt visibility measured rows >= 90%, 1D/3D label non-null rate >= 80%다.
11. promotion 판단은 절대 return만 쓰지 않고 same-session excess return과 same-call CORE replacement comparison을 함께 쓴다.
12. `catalyst_score`는 PEAD prompt-visible gate를 우회하지 않는다.

## Test Plan

문서/분석 도구:

```powershell
python tools/analyze_screener_diversity.py --mode live --output-dir docs/reports
python tools/update_candidate_audit_outcomes.py --mode live
```

후보/감사:

```powershell
python -m pytest tests/test_candidate_audit.py tests/test_screener_quality.py tests/test_candidate_quality_trainer.py -q
```

prompt/routing:

```powershell
python -m pytest tests/test_candidate_action_live_mapping.py tests/test_trading_bot_intraday_evidence.py tests/test_action_routing.py -q
```

문법:

```powershell
python -m py_compile trading_bot.py runtime/candidate_prompt_pool.py runtime/candidate_quality_trainer.py minority_report/analysts.py
```

## Suggested First Patch

첫 패치는 live behavior를 바꾸지 않는 report-only가 맞다.

1. `tools/analyze_screener_diversity.py` 추가
2. `candidate_pool_role`을 계산만 하는 helper 추가
3. 1D/3D outcome null 문제 점검 리포트 추가
4. `screen_rank`, `provider_rank`, `category_rank`, `trainer_input_rank`, `rank_source`를 report-only로 계산
5. primary cohort key를 3차원으로 제한하고 diagnostic key는 report split으로만 사용
6. same-session excess return과 same-call CORE replacement comparison 추가
7. PEAD/catalyst gate 우회 여부를 assertion으로 검사
8. `docs/reports/screener_diversity_*.md` 생성

이 패치의 결과를 보고 `r31_60`, `preopen_low_liq_ignite60`, `late_reclaim`, `US day_gainers/high`, `KR near_breakout/low` 중 어떤 cohort를 challenger로 올릴지 결정한다.

## Trainer Judgment

이 시스템을 아는 트레이너라면 기존 상위 후보를 버리지 않는다. 상위 후보는 시장의 현재 중심을 보여주기 때문이다. 대신 지금 필요한 것은 “다음에 강해질 후보를 보는 눈”이다.

따라서 개발 방향은 다음 한 문장으로 정리된다.

> 기존 스크리너는 exploitation으로 유지하고, 장전/감사/outcome/web-event 기반의 exploration 후보를 소량 role-aware로 넣어, 매수 권한이 아니라 관찰과 검증부터 시작한다.
