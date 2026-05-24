# 스크리너 개선안 재검토 및 시뮬레이션 리포트

- 작성일: 2026-05-24 KST
- 범위: KR/US 후보 스크리너, 후보 품질 trainer, prompt cap, shadow overlay
- 원칙: broker/API/Claude 호출 없이 로컬 DB와 로그만 사용
- 핵심 산출물:
  - `docs/reports/candidate_quality_ranker_sim_20260524_screener_review.md`
  - `docs/reports/candidate_improvement_simulation_20260524_101408.md`
  - `docs/reports/prompt_overlay_shadow_20260524_screener_review.md`

## 1. 최종 결론

현재 스크리너는 “후보를 못 찾는” 상태가 아니다. 감사 DB와 로그를 재검토하면, 최종 품질을 더 올릴 수 있는 병목은 raw 후보 수 자체보다 `후보가 Claude prompt까지 도달하기 전의 ranking`, `bucket/source metadata 전달`, `장초반 US 달러 거래대금 필터`, `데이터 소스 단일화 리스크`에 있다.

따라서 다음 조치는 `trade_ready` 확대나 주문 한도 확대가 아니라, 아래 순서가 맞다.

1. KR/US 공통: prompt cap 앞단 ranking과 overlay shadow를 계속 검증한다.
2. KR: `screener_quality`에는 존재하는 bucket metadata를 trainer/audit/prompt pool 앞단으로 전달한다.
3. US: 장초반 dollar volume 필터를 projected dollar volume shadow로 검증한다.
4. US: KIS overseas ranking을 1차 소스로 추가하되 Yahoo/FMP fallback은 유지한다.
5. KR: `vol_ratio` cap/log 점수는 live 승격하지 말고 shadow 유지한다.

재검토 중 수정된 판단도 있다. 기존 리뷰에서는 `PROMPT_OVERLAY_MODE`를 기본 off로 봤지만, 현재 `config/v2_start_config.json`에는 이미 `PROMPT_OVERLAY_MODE=shadow`로 설정되어 있다. 그러므로 실행 권고는 “shadow 활성화”가 아니라 “현재 shadow 결과를 분석하고 gate 통과 전까지 live cap/order에는 반영하지 않기”로 조정한다.

## 2. 원 리뷰 내용 저장 요약

원 리뷰의 핵심 판단은 다음과 같다.

- KR 스크리너는 KIS 국내 거래량순위 API 중심이다.
  - `/uapi/domestic-stock/v1/quotations/volume-rank`
  - TR ID `FHPST01710000`
  - KOSPI `input_iscd=0001`, KOSDAQ `input_iscd=1001`
  - KOSDAQ 최소 비중 정책은 최근 로그에서 대체로 작동한다.
- KR raw score는 `log1p(turnover) + positive_change*2 + vol_ratio*4` 계열이라 `vol_ratio` 급등이 rank를 과도하게 지배할 수 있다.
- US 스크리너는 현재 Yahoo predefined screener 중심이다.
  - `most_actives`, `day_gainers`, `day_losers`
  - FMP와 하드코딩 fallback 존재
  - 가격, 등락률, 달러 거래대금, ETF/워런트/유닛 추정 필터가 있다.
- 후보는 바로 Claude로 가지 않고, history filter, quality feature, trainer prompt pool, prompt cap, Claude selection, PathA/PathB routing을 통과한다.
- 따라서 단순히 raw 후보 수를 늘리는 것보다 cap 앞단 ranking 품질이 중요하다.
- 권장 개선은 다음이었다.
  - US KIS ranking 1차 소스 구현
  - KR bucket metadata를 trainer 전단으로 shadow 주입
  - KR `vol_ratio` cap/log score shadow
  - US 장초반 projected dollar volume shadow
  - prompt near-cap overlay shadow 검증
  - 이후 KR/US multi-source provider consensus 확장
- 피해야 할 방향은 `trade_ready` slot 확대, PathB/order cap 변경, KR `PLAN_B` 전체 승격, 짧은 기간 winner 기반 weight 급변, `state/brain.json` 자동 변경이다.

## 3. 사용한 근거와 실행한 시뮬레이션

### 3.1 로컬 데이터 커버리지

`tools/simulate_candidate_improvement.py` 실행 결과 기준:

| source | rows | date_min | date_max | 비고 |
|---|---:|---|---|---|
| long_backtest | 5,766 | 2018-01-02 | 2026-04-23 | 장기 일봉 backtest |
| ticker_selection_log | 4,742 | 2026-04-07 | 2026-05-22 | selection/trade log |
| screener_quality | 23,929 | 2026-04-28 | 2026-05-23 | 후보 품질 로그 |
| raw_selection_calls | 966 | 2026-04-22 | 2026-05-23 | Claude selection raw call |
| action_routing_shadow | 4,355 | 2026-05-06 | 2026-05-22 | route shadow |
| preopen_state | 1,624 | 2026-05-02 | 2026-05-22 | preopen 후보 |

`data/audit/candidate_audit.db`는 다음 주요 테이블을 갖는다.

| table | rows |
|---|---:|
| `audit_candidate_rows` | 30,485 |
| `audit_candidate_outcomes` | 45,143 |
| `audit_claude_calls` | 1,007 |
| `candidate_counterfactual_paths` | 22,196 |

주의: 60분 outcome label은 모든 날짜에 균등하게 붙어 있지 않다. 2026-05-12 이후 row는 존재하지만, 실제 60분 label이 붙은 row는 주로 2026-05-15 이후에 집중되어 있다. 따라서 수익률 수치는 “후보 품질 방향성”으로 봐야 하며, 정식 live 승격 조건으로는 부족하다.

### 3.2 실행 명령

```powershell
python tools\candidate_quality_ranker_sim.py --stamp 20260524_screener_review
python tools\simulate_candidate_improvement.py
python tools\analyze_prompt_overlay_shadow.py --date-from 2026-05-12 --horizon-min 60 --md-out docs\reports\prompt_overlay_shadow_20260524_screener_review.md --json-out docs\reports\prompt_overlay_shadow_20260524_screener_review.json
```

추가로 inline SQLite/log 분석을 수행했다. 추가 분석은 파일을 변경하지 않았고, 로컬 DB와 jsonl 로그만 읽었다.

## 4. 현 구조 재검토

### 4.1 US는 아직 Yahoo 중심이다

`kis_api.py`의 `screen_market_us(top_n, mode)`는 현재 Yahoo predefined screener를 먼저 사용하고, FMP/fallback 경로를 둔다. 반면 `docs/important/source/us_kis_ranking_screener_requirements_20260522.md`에는 KIS overseas ranking 요구서가 이미 있다.

| 목적 | KIS endpoint | TR ID | 내부 category |
|---|---|---|---|
| 해외 거래량순위 | `/uapi/overseas-stock/v1/ranking/trade-vol` | `HHDFS76310010` | `most_actives` |
| 해외 상승률/하락률 | `/uapi/overseas-stock/v1/ranking/updown-rate` | `HHDFS76290000` | `day_gainers`, `day_losers` |

재검토 판단:

- KIS US ranking은 현재 로컬 outcome으로 성능 비교할 수 없다. 아직 live 경로에 구현되어 있지 않기 때문이다.
- 그래도 P0 후보로 유지할 이유는 있다. 주문 브로커와 데이터 소스를 맞추고, Yahoo 응답 변동 리스크를 줄이며, NAS/NYS/AMS 거래소별 coverage를 직접 관리할 수 있다.
- 단, 성능 개선을 가정해 live ranking weight를 바로 바꾸면 안 된다. 최초 구현은 `source=kis`, `fallback=yf/fmp`, `source_overlap`, `source_disagreement`를 로그로 남기는 방식이 맞다.

### 4.2 KR은 volume-rank 중심이고 score가 volume spike에 민감하다

`kis_api.py`에는 다음 구조가 있다.

- `_kis_volume_rank()`
- `_merge_kr_market_buckets()`
- `_cap_kr_screen_candidates()`
- `_kr_screen_score()`

최근 `logs/screener_quality`에는 이미 shadow score가 같이 찍힌다.

- `score_current`
- `score_vol_ratio_capped`
- `score_vol_ratio_log`
- `score_turnover_weighted`

2026-05-22 KR preopen 예시에서는 `vol_ratio=86.43` 후보의 `score_current=371.103`, `score_vol_ratio_capped=76.963`, `score_vol_ratio_log=80.7297`로 큰 차이가 난다. 즉 “현 score가 volume spike에 과민할 수 있다”는 구조적 우려는 맞다.

하지만 outcome 기반 shadow 시뮬레이션에서는 cap/log 변형이 현재 점수보다 일관되게 좋지는 않았다. 따라서 live 교체가 아니라 shadow 유지가 맞다.

## 5. 시뮬레이션 1: prompt ranker/cap 비교

`tools/candidate_quality_ranker_sim.py`는 후보 감사 DB의 call group을 기준으로 현재 prompt cap과 trainer 기반 prompt pool을 비교했다. label은 scoring 이후 평가에만 사용했다.

| scenario | n | ret60_n | ret60_avg | ret30_avg | good_rate | bad_rate | pf60 |
|---|---:|---:|---:|---:|---:|---:|---:|
| current_prompt_cap25 | 13,634 | 1,332 | +0.2922% | +0.2492% | 27.18% | 31.83% | 1.3020 |
| trainer_prompt_cap25 | 15,620 | 1,381 | +0.4241% | +0.3376% | 27.66% | 32.26% | 1.5114 |
| trainer_plan_a_shadow_cap25 | 3,752 | 358 | +1.4742% | +0.9961% | 38.27% | 30.76% | 2.7859 |
| current_prompt_cap30 | 14,771 | 1,461 | +0.3517% | +0.2691% | 27.17% | 32.12% | 1.3735 |
| trainer_prompt_cap30 | 17,882 | 1,621 | +0.4044% | +0.3010% | 27.88% | 32.52% | 1.4733 |
| trainer_plan_a_shadow_cap30 | 3,884 | 379 | +1.5343% | +1.0109% | 39.31% | 30.42% | 2.6926 |
| current_prompt_cap36 | 15,111 | 1,508 | +0.3831% | +0.2749% | 27.72% | 31.77% | 1.4174 |
| trainer_prompt_cap36 | 19,993 | 1,834 | +0.4142% | +0.2638% | 28.68% | 32.38% | 1.4725 |
| trainer_plan_a_shadow_cap36 | 3,924 | 386 | +1.5147% | +1.0169% | 39.64% | 30.31% | 2.6099 |

해석:

- trainer prompt pool은 current prompt 대비 평균 60분 수익률과 PF를 소폭 개선했다.
- 가장 강한 신호는 `PLAN_A` shadow다. 단, 이 결과는 “PLAN_A 후보를 더 잘 보이게 하라”는 뜻이지 “자동 매수하라”는 뜻이 아니다.
- cap을 25에서 36으로 키울수록 current도 개선되지만, 차이는 크지 않다. cap 확대 자체보다 cap 안에 들어오는 순서가 중요하다.

## 6. 시뮬레이션 2: prompt visibility와 near-cap 후보

`tools/simulate_candidate_improvement.py` 결과:

| market | matched events | avg raw rows | avg actual prompt | events missing top30 | avg missing top30 | avg score36 gain |
|---|---:|---:|---:|---:|---:|---:|
| KR | 171 | 61.5205 | 28.9825 | 171 | 11.2573 | 14.4912 |
| US | 228 | 57.0658 | 26.2412 | 227 | 13.1711 | 15.4693 |

해석:

- raw screener rows는 평균 57~62개 수준인데 실제 prompt는 26~29개 수준이다.
- KR/US 모두 top30 누락 이벤트가 거의 항상 발생한다.
- 따라서 “raw 후보를 더 늘리기”보다 “prompt cap 근처 후보를 어떤 기준으로 끌어올릴지”가 더 중요하다.

2026-05-14 이후 60분 label이 있는 row 기준 near-cap 구간은 다음과 같다.

| market | bucket | n | ret60_avg | pos_rate | pf60 | mfe2_rate |
|---|---|---:|---:|---:|---:|---:|
| KR | prompt_rank 1-24 | 201 | +1.1673% | 46.77% | 1.8488 | 30.35% |
| KR | prompt_rank 25-32 | 36 | +1.5554% | 50.00% | 4.3613 | 22.22% |
| US | prompt_rank 1-24 | 357 | +0.6130% | 65.27% | 2.2500 | 15.41% |
| US | prompt_rank 25-32 | 82 | +0.6387% | 51.22% | 3.4928 | 14.63% |
| US | prompt_rank 33-40 | 24 | +1.6771% | 70.83% | 11.6297 | 29.17% |

해석:

- near-cap 구간에도 좋은 후보가 있다.
- 다만 표본이 작고 중복/날짜 편향이 있으므로 live cap 확대 근거로는 부족하다.
- 현재 결론은 “near-cap overlay를 shadow로 유지하고, trade_ready/order cap은 건드리지 않는다”이다.

## 7. 시뮬레이션 3: source_file 및 trainer state 성과

2026-05-14~2026-05-22 중 60분 label이 붙은 row 기준이다.

### 7.1 source_file별 성과

| market | source_file | n | ret60_avg | pos_rate | pf60 | mfe2_rate |
|---|---|---:|---:|---:|---:|---:|
| KR | `trading_bot.prompt_pool` | 108 | +0.7714% | 45.37% | 1.6194 | 30.56% |
| KR | `trading_bot.prompt_pool_excluded` | 338 | +0.8627% | 39.64% | 1.6491 | 28.11% |
| KR | `trading_bot.selection_meta` | 129 | +1.6070% | 48.84% | 2.3073 | 27.91% |
| US | `trading_bot.prompt_pool` | 230 | +0.6584% | 60.43% | 2.7630 | 14.78% |
| US | `trading_bot.prompt_pool_excluded` | 484 | -0.1228% | 51.65% | 0.8361 | 8.06% |
| US | `trading_bot.selection_meta` | 230 | +0.6588% | 65.22% | 2.3295 | 16.52% |

해석:

- KR은 prompt 밖 후보도 평균 수익률이 나쁘지 않다. prompt visibility 개선 여지가 크다.
- US는 prompt 밖 후보 평균이 음수이고 PF도 1 미만이다. US는 현재 trainer/cap이 상대적으로 더 잘 걸러낸다.
- selection_meta는 양 시장 모두 prompt_pool보다 낫다. Claude/후단 라우팅이 완전히 무작위는 아니다.

### 7.2 trainer state별 성과

| market | trainer_state | n | ret60_avg | pos_rate | pf60 | mfe2_rate |
|---|---|---:|---:|---:|---:|---:|
| KR | PLAN_A | 167 | +2.1506% | 48.50% | 2.4700 | 37.72% |
| KR | PLAN_B | 352 | +0.4526% | 42.05% | 1.3677 | 25.00% |
| KR | WATCH | 13 | -0.4887% | 38.46% | 0.5811 | 23.08% |
| US | PLAN_A | 132 | +0.6216% | 69.70% | 3.7125 | 12.12% |
| US | PLAN_B | 593 | +0.4488% | 58.68% | 1.9158 | 13.15% |
| US | WATCH | 156 | -0.6700% | 41.03% | 0.4501 | 8.33% |

해석:

- `PLAN_A`는 양 시장 모두 우수하다.
- KR `PLAN_B`는 평균 양수지만 분산이 있어 전체 승격하면 위험하다.
- US `WATCH`는 명확히 나쁘다. US에서는 현재 trainer 분리력이 꽤 있다.

## 8. 시뮬레이션 4: prompt overlay shadow 재검토

현재 설정은 이미 `PROMPT_OVERLAY_MODE=shadow`다.

`tools/analyze_prompt_overlay_shadow.py` 결과:

| pool | n | avg | median | win_rate | pf |
|---|---:|---:|---:|---:|---:|
| current | 700 | +0.8601% | +0.2489% | 57.71% | 2.2214 |
| shadow_overlay | 490 | +1.0859% | +0.2940% | 61.22% | 2.9096 |
| overlay_triggered | 8 | +4.1267% | +1.4668% | 75.00% | 20.8080 |

Gate 결과:

| gate | pass |
|---|---:|
| shadow_days_min_10 | true |
| overlay_days_min_4 | false |
| overlay_triggered_pf_gt_1 | true |
| top_day_contribution_lt_40 | false |
| plan_b_fallback_zero | true |
| 전체 gate_pass | false |

해석:

- overlay shadow는 방향성이 좋다.
- 그러나 triggered 표본이 8개뿐이고, top day contribution이 90.54%라 특정 하루 의존도가 너무 크다.
- 결론은 live 승격 보류다. shadow를 유지하고 overlay day 수와 triggered 표본을 더 쌓아야 한다.

## 9. 시뮬레이션 5: KR `vol_ratio` cap/log shadow

`logs/screener_quality/202605*_KR_candidates.jsonl`를 event 단위로 묶고, `data/audit/candidate_audit.db`의 date/ticker 60분 outcome 평균과 결합했다. 중복/label 편향이 있으므로 ranking churn과 방향성만 본다.

### 9.1 cap 28

| score | labeled n | ret60_avg | median | pos_rate | pf60 |
|---|---:|---:|---:|---:|---:|
| current | 2,005 | +1.8521% | +0.6608% | 56.06% | 2.4849 |
| vol_ratio_capped | 1,959 | +1.7478% | +0.4494% | 54.93% | 2.3301 |
| vol_ratio_log | 1,964 | +1.7961% | +0.6608% | 56.31% | 2.3762 |
| turnover_weighted | 1,946 | +1.6844% | +0.4631% | 55.65% | 2.2739 |
| candidate_quality_score | 2,078 | +1.0865% | +0.2923% | 53.90% | 1.8951 |

### 9.2 cap 32

| score | labeled n | ret60_avg | median | pos_rate | pf60 |
|---|---:|---:|---:|---:|---:|
| current | 2,267 | +1.6627% | +0.4494% | 55.54% | 2.3309 |
| vol_ratio_capped | 2,248 | +1.5437% | +0.3762% | 54.14% | 2.1793 |
| vol_ratio_log | 2,241 | +1.5535% | +0.4292% | 54.75% | 2.1825 |
| turnover_weighted | 2,243 | +1.5087% | +0.4156% | 54.21% | 2.1431 |
| candidate_quality_score | 2,345 | +1.1323% | +0.2837% | 53.52% | 1.9555 |

### 9.3 rank overlap

| cap | capped Jaccard vs current | log Jaccard vs current | turnover_weighted Jaccard vs current |
|---:|---:|---:|---:|
| 28 | 0.7649 | 0.7827 | 0.7069 |
| 32 | 0.8295 | 0.8296 | 0.7696 |
| 40 | 0.9366 | 0.8962 | 0.8557 |

해석:

- 구조적으로 current score가 volume spike에 민감한 것은 맞다.
- 그러나 2026년 5월 로컬 label 기준으로는 current score가 cap/log 변형보다 성과가 낮지 않았다.
- 따라서 `KR_SCREEN_SCORE_VERSION=v2` live 전환은 아직 근거 부족이다.
- 가장 안전한 조치는 기존처럼 shadow score를 계속 저장하고, 특정 조건에서만 비교하는 것이다.
  - 극단 `vol_ratio`
  - `turnover_spike_chase_risk`
  - `from_52w_high_pct`가 0 근처인 고점 추격 후보
  - `PLAN_A`인데 current score만 낮은 후보

## 10. 시뮬레이션 6: KR bucket metadata 전달 누락

감사 DB와 screener_quality 로그를 비교했다.

### 10.1 감사 DB의 `primary_bucket`

2026-05-12 이후 `audit_candidate_rows` 기준:

| market | 주요 source_file | rows | missing_bucket_pct |
|---|---|---:|---:|
| KR | `trading_bot.prompt_pool_excluded` | 3,762 | 100.00% |
| KR | `trading_bot.prompt_pool` | 1,851 | 100.00% |
| KR | `trading_bot.selection_meta` | 1,800 | 100.00% |
| US | `trading_bot.prompt_pool_excluded` | 5,510 | 100.00% |
| US | `trading_bot.prompt_pool` | 1,665 | 100.00% |
| US | `trading_bot.selection_meta` | 2,032 | 100.00% |

`source_tags`도 KR은 `KR:unclassified`, `KR:base_universe`, `KR:unknown_liq`가 대부분이다. 즉 trainer/audit 단계에서는 bucket classifier 정보가 충분히 보존되지 않는다.

### 10.2 screener_quality에는 bucket이 존재한다

2026년 5월 `logs/screener_quality` 기준:

| market | rows | primary_bucket 분포 |
|---|---:|---|
| KR | 9,339 | `volume_surge` 5,288, `liquidity_leader` 1,718, `unclassified` 1,578, `momentum_now` 755 |
| US | 12,539 | `momentum_now` 6,270, `liquidity_leader` 4,424, `unclassified` 1,845 |

해석:

- bucket classifier는 이미 의미 있는 metadata를 만든다.
- 하지만 prompt pool/trainer/audit row에는 대부분 전달되지 않는다.
- 이 문제는 새 모델이나 주문 로직 없이도 개선 가능하다.
- 추천 구현은 `_filter_candidates_by_history()` 이후, trainer prompt pool 전단에서 `primary_bucket`, `secondary_buckets`, `bucket_seen_count`, `bucket_reasons`를 보존하는 shadow 주입이다.

## 11. 시뮬레이션 7: US 장초반 dollar volume reject

US screener_quality event 단위로 `dollar_volume_reject_count_by_category`를 집계했다.

| 구간 | events | events_with_rejects | total_rejects | avg_rejects | max_rejects |
|---|---:|---:|---:|---:|---:|
| preopen/early elapsed < 0 | 10 | 10 | 47 | 4.700 | 6 |
| early 0-60분 | 23 | 23 | 770 | 33.478 | 51 |
| regular > 60분 | 58 | 58 | 701 | 12.086 | 20 |
| elapsed missing | 118 | 0 | 0 | 0.000 | 0 |

상위 reject event 예시:

| timestamp | phase | elapsed_min | rejects | degraded_reason |
|---|---|---:|---:|---|
| 2026-05-20T22:35:56 | session_reuse_rescreen | 5.32 | 51 | `fresh_count_below_min_cache_count,early_dollar_volume_rejects` |
| 2026-05-20T22:36:14 | opening_fresh_observe | 6.24 | 50 | `fresh_count_below_min_cache_count,early_dollar_volume_rejects` |
| 2026-05-18T22:36:40 | opening_fresh_observe | 6.67 | 45 | `fresh_count_below_min_cache_count,early_dollar_volume_rejects` |
| 2026-05-21T22:35:49 | opening_fresh_observe | 5.82 | 45 | `fresh_count_below_min_cache_count,early_dollar_volume_rejects` |

해석:

- 장초반 0~60분 reject 평균은 regular session의 약 2.77배다.
- 이는 “현재 누적 dollar volume만 보면 장초반 정상 후보도 탈락할 수 있다”는 리뷰 판단을 지지한다.
- 다만 reject된 개별 ticker와 forward outcome이 로그에 직접 남아 있지 않아 성과 시뮬레이션은 불가능하다.
- 따라서 live 필터 교체가 아니라 shadow field 추가가 맞다.

추천 shadow 산식:

```text
projected_dollar_volume =
  current_price * current_volume / max(elapsed_session_fraction, floor)
```

추가로 저장할 field:

- `current_dollar_vol`
- `projected_dollar_vol`
- `elapsed_session_fraction`
- `early_volume_filter_version`
- `would_pass_projected_dollar_vol`
- `current_filter_rejected_reason`

## 12. 개선안별 비교 분석

| 개선안 | 로컬 근거 강도 | 기대 효과 | 주요 리스크 | 최종 판단 |
|---|---|---|---|---|
| US KIS ranking 1차 소스 | 중간 | broker-aligned source, Yahoo 변동 리스크 축소 | 아직 outcome 비교 불가 | 구현 P0, shadow/source tag 필수 |
| KR bucket metadata trainer 전단 주입 | 강함 | 이미 계산된 정보를 trainer가 사용 가능 | 기존 source_tags 계약 변경 주의 | P0, shadow부터 적용 |
| KR `vol_ratio` cap/log score live 전환 | 약함 | 극단 volume spike 완화 | 현재 label에서는 current가 더 좋음 | live 금지, shadow 유지 |
| US projected dollar volume shadow | 강함 | 장초반 recall 개선 후보 검증 | 저유동성 후보 오통과 가능 | P0 shadow, live 교체 금지 |
| near-cap overlay shadow | 중간 | cap 밖 PLAN_A/상위 후보 visibility 개선 | 표본 작고 특정일 편향 큼 | 이미 shadow, gate 통과 전 live 금지 |
| KR/US multi-source consensus | 중간 | 단일 source 급등주보다 consensus 우대 | endpoint별 정규화 필요 | P1 shadow |
| trade_ready/order cap 확대 | 약함 | 단기 매수 수 증가 | selection 품질 문제와 execution/risk 혼선 | 금지 |

## 13. 재검토 후 최종 우선순위

### P0-1. KR bucket metadata 전달 복구

가장 직접적인 로컬 근거가 있다. `screener_quality`에는 bucket이 있는데 `audit_candidate_rows`와 trainer source_tags에는 거의 없다.

구현 범위:

- history filter 이후 trainer prompt pool 전단에서 metadata 보존
- `primary_bucket`
- `secondary_buckets`
- `bucket_seen_count`
- `bucket_reasons`
- audit row와 scorer snapshot에 shadow 저장

금지:

- bucket만 보고 `trade_ready` 자동 승격
- `PLAN_B` 전체 승격

### P0-2. US projected dollar volume shadow

장초반 reject가 명확히 과도하다. 하지만 reject 개별 ticker outcome이 없으므로 live 필터를 바꾸지 말고 shadow부터 넣는다.

구현 범위:

- early session에서 projected dollar volume 계산
- 기존 current dollar volume 결과와 나란히 기록
- `would_pass_projected_dollar_vol`만 shadow로 저장
- 5거래일 이상 outcome 비교

### P0-3. US KIS ranking source 추가

성능 시뮬레이션은 아직 불가능하지만 구조적 필요성이 크다.

구현 범위:

- `screen_market_us(..., token: str | None = None)` 확장
- KIS `trade-vol`을 `most_actives`로 normalize
- KIS `updown-rate`를 `day_gainers/day_losers`로 normalize
- Yahoo/FMP fallback 유지
- source별 raw_count, filtered_count, overlap_count 기록

주의:

- `.env.live`, `config/v2_start_config.json` live 운영값 자동 변경 금지
- 주문/risk/PathB cap 변경 금지

### P0-4. prompt overlay shadow 분석 지속

이미 `PROMPT_OVERLAY_MODE=shadow`다. 이번 분석에서 gate는 통과하지 못했다.

다음 gate:

- overlay_days >= 4
- triggered sample >= 30
- top_day_contribution < 40%
- PF > 1 유지
- PLAN_B fallback이 0 또는 명시적으로 제한됨

### P1. KR `vol_ratio` cap/log score는 shadow만 유지

현 score가 과민한 것은 맞지만, label 비교에서는 cap/log가 current를 이기지 못했다. 그래서 live 점수 교체는 보류한다.

추가 검증:

- `vol_ratio >= 20`
- `from_52w_high_pct >= -3`
- `turnover_vs_20d >= 5`
- `candidate_quality_flags`에 `turnover_spike_chase_risk` 포함
- 이 조건에서 current vs capped/log의 winner/loser 분리력 비교

### P1. Nasdaq Symbol Directory product filter

현재 로컬 outcome 시뮬레이션 대상은 아니지만, ETF/test issue/financial status 필터 정확도를 높일 수 있다. US KIS source 구현 후 product normalization layer에서 같이 붙이는 것이 좋다.

## 14. 최종 판단

이번 재검토에서 원 리뷰의 큰 방향은 유지된다. 다만 두 가지는 조정한다.

1. `PROMPT_OVERLAY_MODE`는 이미 shadow다. “켜기”가 아니라 “결과를 더 쌓고 gate 통과 전 live 반영 금지”가 맞다.
2. KR `vol_ratio` cap/log score는 구조적으로 타당하지만, 현재 로컬 label에서는 live 교체 근거가 없다. shadow 검증만 유지한다.

가장 먼저 손댈 곳은 주문이 아니라 후보 metadata와 로그 계약이다. 특히 KR은 `screener_quality`에서 계산한 bucket을 trainer/audit 앞단에 전달하지 못하고 있어, 좋은 후보를 더 잘 보여줄 수 있는 정보를 버리는 상태다.

최종 실행 순서:

1. KR bucket metadata를 trainer/audit/prompt pool 전단에 shadow 주입
2. US projected dollar volume shadow 추가
3. US KIS ranking 1차 source 구현 및 Yahoo/FMP fallback 유지
4. overlay shadow gate 재분석 자동화
5. KR `vol_ratio` cap/log는 극단 후보 subset에서만 추가 검증

`trade_ready`, PathB live cap, 주문 금액, daily entry cap은 이번 개선 범위에서 제외한다.

## 15. 2026-05-25 재검토 반영

운영자 재검토 결과를 반영해, 이 리포트의 결론은 유지하되 근거 강도와 실행 해석을 아래처럼 조정한다.

### 15.1 PLAN_A shadow 수치 해석 하향

`trainer_plan_a_shadow_cap25`의 `ret60_avg=+1.4742%`, `pf60=2.7859`는 강한 숫자지만, 이것을 “PLAN_A 방향으로 live selection을 기울이면 성과가 좋아진다”는 근거로 쓰지 않는다.

이유:

- PLAN_A는 trainer가 이미 고품질 subset으로 마킹한 후보군이다.
- 사후에 PLAN_A subset만 꺼내 성과를 보면 selection bias가 생긴다.
- 성과 근거로 쓰려면 PLAN_A 후보가 실제 prompt에 들어갔을 때 Claude가 선택했는지, 선택 후 route/signal/fill까지 이어졌는지를 분리해서 봐야 한다.
- 기존 Promoted Examples 일부는 `ret60=None`이라 실측 label이 없다.

수정된 판단:

- PLAN_A shadow는 “자동 승격 근거”가 아니라 “관측 우선순위”다.
- PLAN_A 후보를 prompt visibility 개선 후보로 추적할 수는 있지만, `trade_ready`나 live 주문 게이트로 연결하지 않는다.

### 15.2 WATCH_TRIGGER 결과는 최근 2주 재검증 전까지 보류

기존 `WATCH_TRIGGER` proxy 결과는 역설적이다.

| market | kept forward_1d avg | demoted forward_1d avg |
|---|---:|---:|
| KR | +0.52~0.58% | +1.08~1.36% |
| US | +1.24~1.44% | +1.61~1.76% |

즉, `at_high`로 demote된 종목의 forward return이 kept보다 높았다. 이 결과만 보면 현재 at_high 차단이 좋은 후보를 놓쳤을 가능성이 있다.

수정된 판단:

- WATCH_TRIGGER demotion은 이번 리포트의 실행 근거에서 제외한다.
- 2026-04-07~2026-05-08 범위가 최근 시황을 충분히 반영하지 못하므로 최근 2주 기준으로 재검증한다.
- 재검증 전에는 at_high 후보를 추가 차단하지 않는다.
- 확인할 항목은 forward return뿐 아니라 entry 이후 drawdown, MFE capture, 실제 fill 가능성, 고점 추격 후 손절 빈도다.

### 15.3 long backtest baseline은 구조 확인 전 보조 근거로만 사용

KR long backtest baseline은 `avg=-0.6425%`, `pf=0.7219`로 음수다. 이 값이 현재 screener 구조의 raw universe를 정확히 반영한다면, Claude와 routing이 음수 기대값 풀에서 양수 후보를 골라낸다는 해석이 가능하다.

다만 장기 backtest가 현재 KIS volume-rank, prompt cap, trainer, PathA/PathB routing을 그대로 재현하는지는 확인이 필요하다.

수정된 판단:

- long backtest는 gap guard 방향성 확인용 보조 근거로만 둔다.
- 현재 live screener 개선의 1차 근거는 `candidate_audit.db`, `screener_quality`, raw prompt 관측으로 제한한다.
- gap guard 자체는 demoted 집합이 더 나쁘게 나온 점 때문에 방향성은 유지하되, live threshold 변경에는 쓰지 않는다.

### 15.4 신뢰도 높은 결론 유지

다음 두 결론은 재검토 후에도 신뢰도가 높다.

첫째, bucket metadata 전달 누락은 명확하다.

- `audit_candidate_rows.primary_bucket`은 KR/US 주요 source_file에서 100% missing이다.
- 반면 `logs/screener_quality`에는 KR `volume_surge` 5,288건, `liquidity_leader` 1,718건, US `momentum_now` 6,270건 등이 존재한다.
- 따라서 계산된 metadata가 trainer/audit/prompt pool에 전달되지 않는 문제가 맞다.

둘째, overlay gate 실패 판단은 맞다.

- `overlay_triggered n=8`
- `top_day_contribution=90.54%`
- `overlay_days=3`
- `gate_pass=False`

이 숫자는 live 반영을 막아야 하는 전형적인 조건이다. shadow 유지 외에 live cap/order 변경은 하지 않는다.

### 15.5 prompt observability를 P0 범위에 포함

`input_to_claude`와 실제 prompt count의 괴리는 중요한 관측 문제다.

| market | avg raw rows | avg actual prompt | avg input_true |
|---|---:|---:|---:|
| KR | 61.5 | 29.0 | 55.7 |
| US | 57.1 | 26.2 | 54.8 |

수정된 판단:

- `screener_quality.input_to_claude=true`는 실제 prompt 포함을 의미하지 않을 수 있다.
- `select_tickers` 내부 추가 trim 이후의 실제 prompt ticker 목록을 별도 저장해야 한다.
- prompt visibility 개선 전에는 `actual_prompt_tickers`, `actual_prompt_count`, `trim_reason`, `prompt_rank_after_trim` 관측을 먼저 보강한다.

### 15.6 US projected dollar volume 일정

US projected dollar volume shadow는 구현 우선순위가 높지만, 결론은 데이터가 쌓인 뒤 낸다.

수정된 일정:

- 2026-05-25 이후 구현 가능
- 최소 5거래일 shadow label 축적
- 2026년 6월 초 이후 첫 판정
- reject된 ticker의 `would_pass_projected_dollar_vol`와 forward outcome을 연결한 뒤 live 필터 교체 여부 판단

## 16. 수정된 실행 순서

재검토 후 실행 순서는 다음으로 확정한다.

1. prompt observability 보강
   - 실제 prompt ticker 목록과 trim reason 저장
   - `input_to_claude` 과대 보고 여부를 분리

2. KR bucket metadata 전달 복구
   - `screener_quality`에서 계산된 `primary_bucket`, `secondary_buckets`, `bucket_seen_count`, `bucket_reasons`를 trainer/audit/prompt pool 전단에 shadow로 전달
   - 자동 `trade_ready` 승격 금지

3. US projected dollar volume shadow 구현
   - 장초반 reject 후보에 대해 projected dollar volume과 would-pass 여부 저장
   - 2026년 6월 초 이후 outcome으로 판정

4. US KIS ranking source 추가
   - KIS/Yahoo/FMP source overlap과 disagreement를 먼저 기록
   - source 추가를 곧바로 주문 확대 근거로 쓰지 않음

5. overlay shadow와 WATCH_TRIGGER는 관측만 지속
   - overlay는 gate 통과 전 live 반영 금지
   - WATCH_TRIGGER는 최근 2주 재검증 전 demotion 강화 금지

제외 항목은 유지한다.

- `trade_ready` slot 확대 금지
- PathB live/order cap 변경 금지
- KR `PLAN_B` 전체 승격 금지
- KR `vol_ratio` cap/log live 점수 교체 금지
- `state/brain.json` 자동 변경 금지

## 17. 운영 배포 안전성 평가

수정된 개선 순서는 운영상 큰 리스크 없이 진행할 수 있다. 단, 첫 배포는 모두 shadow/observability 성격이어야 하며, 주문 수량, PathB, `trade_ready`, daily entry cap은 변경하지 않는다.

여기서 shadow를 권하는 이유는 자신감 부족이 아니라 변경 종류가 다르기 때문이다. 관측을 개선하는 live 변경은 바로 적용할 수 있다. 반면 실제 매수 후보, prompt 포함 여부, `trade_ready`, 주문 발생을 바꾸는 behavioral live 변경은 guard 조건 없이 바로 적용하면 원인 추적이 어려워진다.

### 17.0 live 적용 가능 범위 구분

즉시 live 적용 가능:

- 실제 prompt ticker 저장
- trim reason 저장
- `actual_prompt_count` 저장
- `prompt_rank_after_trim` 저장
- KR/US bucket metadata를 audit/log에 보존
- KIS/Yahoo source overlap 기록
- projected dollar volume 계산값 기록

위 항목은 selection/order 판단을 바꾸지 않으므로 live 운영에 바로 올려도 된다.

guard 후 live 검토:

- bucket metadata를 trainer score에 반영
- projected dollar volume으로 기존 US dollar volume reject를 통과시킴
- KIS US ranking을 Yahoo 대신 primary로 사용
- near-cap overlay 후보를 실제 prompt/order 후보로 승격
- PLAN_A를 자동 우대해 `trade_ready` 또는 주문 후보로 연결

위 항목은 실제로 사는 종목을 바꿀 수 있으므로 shadow 또는 guarded-live 단계를 거친다.

### 17.1 안전한 변경

다음 변경은 기록과 관측을 보강하는 작업이라 운영 리스크가 낮다.

1. prompt observability
   - 실제 prompt ticker 목록 저장
   - `actual_prompt_count` 저장
   - trim reason 저장
   - final prompt rank 저장
   - selection/order 로직 변경 없음

2. KR bucket metadata 전달
   - `primary_bucket`, `secondary_buckets`, `bucket_seen_count`, `bucket_reasons`를 trainer/audit/prompt pool 전단에 shadow로 전달
   - metadata 기록은 하되, 이 값만으로 ranking, `trade_ready`, 주문 판단을 바로 바꾸지 않음

3. US projected dollar volume shadow
   - 기존 dollar volume 필터는 그대로 유지
   - `would_pass_projected_dollar_vol`만 shadow 저장
   - reject된 ticker의 forward outcome을 추적할 수 있게 candidate audit에 남김
   - 최소 5거래일 이상 label 축적 후 판단

### 17.2 주의가 필요한 변경

US KIS ranking source 추가는 운영 영향이 있을 수 있으므로 바로 primary 전환하지 않는다.

첫 단계는 `kis_shadow_candidates` 또는 동등한 shadow 기록이어야 한다.

필수 확인 항목:

- NAS/NYS/AMS exchange code 정상화
- ETF/ADR/우선주/워런트/유닛 제거
- KIS API 실패 또는 rate limit 시 Yahoo/FMP fallback
- 중복 ticker merge
- premarket/regular session 응답 차이
- KIS/Yahoo overlap 및 disagreement 비율
- source별 raw_count, filtered_count, rejected_count

위 항목을 확인한 뒤에야 `KIS primary + Yahoo/FMP fallback` 전환을 검토한다.

### 17.3 권장 PR 순서

운영 리스크를 낮추려면 한 번에 모두 바꾸지 않고 다음 순서로 나눈다.

1. PR 1: prompt observability live + KR bucket metadata audit/log live
2. PR 2: US projected dollar volume 계산 live, 필터 통과는 기존 로직 유지
3. PR 3: US KIS ranking 병렬 수집 live, primary 전환은 보류
4. PR 4 이후: guard 조건을 충족한 항목만 behavioral live 전환 검토

이 순서는 봇을 “더 많이 사는 구조”로 바꾸지 않는다. 먼저 “후보가 왜 들어오고 왜 빠졌는지 더 정확히 보이는 구조”로 바꾸는 접근이다. 따라서 현재 운영 상태에서는 이 순서가 가장 안전하다.

### 17.4 behavioral live 전환 guard 예시

US KIS ranking primary 전환 guard:

- Yahoo 결과와 KIS 결과의 overlap이 충분히 높음
- KIS API 실패, timeout, rate limit 시 Yahoo/FMP fallback 정상
- NAS/NYS/AMS exchange code normalize 정상
- ETF/ADR/우선주/워런트/유닛 제거 정상
- source별 raw_count, filtered_count, rejected_count가 로그에 남음

US projected dollar volume 필터 완화 guard:

- reject된 ticker의 projected pass 여부와 forward outcome이 최소 5거래일 이상 축적됨
- projected pass 후보가 current reject 후보보다 forward return 또는 MFE/MAE에서 명확히 우수함
- 최소 current dollar volume floor를 유지함
- quoteType, price, exchange, product filter 정상

KR bucket metadata trainer 반영 guard:

- 먼저 prompt evidence/audit에만 노출
- trainer score weight는 0에서 시작
- bucket별 forward outcome이 충분히 쌓인 뒤 제한 weight만 부여
- bucket 단독으로 `trade_ready` 승격하지 않음

near-cap overlay live 전환 guard:

- triggered sample이 최소 30개 이상
- overlay day가 최소 4거래일 이상
- top day contribution이 40% 미만
- PF > 1 유지
- PLAN_B fallback이 없거나 명시적으로 제한됨
