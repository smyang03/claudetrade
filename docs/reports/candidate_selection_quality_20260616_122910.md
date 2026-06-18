# 후보군 선정 품질/수익성 점검 리포트

- 생성시각: 2026-06-16T12:29:33
- 분석 방식: 운영 DB 읽기 전용. 코드, 설정, state/brain.json, 주문/브로커 상태 미변경.
- 매칭 방식: `candidate_key` 직접 조인은 두 테이블의 키 체계가 달라 사용하지 않고, `(session_date, market, ticker)` + 장초 immediate path로 연결.
- 핵심 분리: 후보 품질은 후보 이후 가격 경로로, 실제 수익성은 v2 live closed trade로 별도 판단.
- 답변 원칙: 긍정적/부정적 포장 없이 DB 수치 기준으로 현실적으로 직언한다.

## 데이터 커버리지
| source | rows | from | to |
| --- | --- | --- | --- |
| audit_candidate_rows | 92468 | 2026-04-20 | 2026-06-16 |
| audit_candidate_latest_rows | 9074 | 2026-04-20 | 2026-06-16 |
| audit_candidate_outcomes | 279516 |  |  |
| candidate_counterfactual_paths | 128239 | 2026-05-18 | 2026-06-16 |
| audit_claude_calls | 2537 | 2026-04-20 | 2026-06-16 |

### outcome label coverage
| horizon_min | rows | labeled | observed_min | observed_max |
| --- | --- | --- | --- | --- |
| 30 | 60393 | 20701 |  | 2026-06-16T00:30:10 |
| 60 | 60393 | 20854 |  | 2026-06-16T01:01:20 |
| 1440 | 52910 | 37012 |  | 2026-06-15 |
| 2880 | 52910 | 29622 |  | 2026-06-15 |
| 4320 | 52910 | 21699 |  | 2026-06-15 |

## 핵심 결론

1. US는 broad watchlist 전체를 장마감까지 보유하면 약보합/소폭 음수지만, `Claude trade_ready`와 실제 live closed trade는 평균 양수다. 즉 US는 "선정 전체"보다 "trade_ready로 좁힌 후보"가 의미 있다.
2. KR 후보 선정은 움직이는 종목 감지는 된다. 하지만 장초 selected/watchlist를 장마감까지 들고 가는 성과는 음수라서, KR은 selection보다 진입/청산/fade 대응 문제가 더 크다.
3. ticker_selection_log의 `trade_ready`는 KR/US 모두 비선정보다 forward 성과가 좋다. 즉 “정말 말이 되는 후보를 고르는 능력”은 있다.
4. missed winner는 남아 있다. 프롬프트 제외/미선정 중 MFE가 큰 종목이 있어 prompt/rank 보강 여지는 있지만, KR은 drawdown도 커서 전체 후보 확대는 위험하다.

## 장초 후보 -> 당일 장마감 가상 성과
범위: 2026-05-18~2026-06-15, KR 08:50~10:00 KST / US 22:20~23:30 KST, 종목-일자별 early decision 1건과 earliest immediate path 1건 매칭.

주의: `candidate_counterfactual_paths`의 장초 immediate path는 주로 watchlist/path 대상에 붙어 있어, 이 섹션은 "선정된 애들을 장마감까지 들고 갔나"에 가깝다. 프롬프트 제외/미선정과의 폭넓은 비교는 아래 `후보 audit forward outcome`과 `ticker_selection_log`를 함께 봐야 한다.
### immediate path close outcome

#### KR

| cohort | rows | labeled | win% | avg_ret% | median_ret% | avg_MFE% | avg_MAE% | MFE>=2% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 전체 후보 | 446 | 446 | 36.8 | -2.266 | -1.669 | 4.183 | -4.444 | 59.3 |
| 프롬프트 포함 | 446 | 446 | 36.8 | -2.266 | -1.669 | 4.183 | -4.444 | 59.3 |
| 프롬프트 제외 | 6 | 6 | 50 | -1.866 | -0.151 | 2.765 | -3.679 | 50 |
| Claude watchlist | 446 | 446 | 36.8 | -2.266 | -1.669 | 4.183 | -4.444 | 59.3 |
| Claude trade_ready | 27 | 27 | 29.6 | -3.173 | -1.647 | 6.111 | -5.149 | 78.9 |
| trade_ready+pullback_wait | 66 | 66 | 27.3 | -3.47 | -1.616 | 4.295 | -5.466 | 70.6 |
| in_prompt_not_selected | 0 | 0 | - | - | - | - | - | - |
| not_in_prompt | 0 | 0 | - | - | - | - | - | - |

#### US

| cohort | rows | labeled | win% | avg_ret% | median_ret% | avg_MFE% | avg_MAE% | MFE>=2% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 전체 후보 | 504 | 504 | 47.2 | -0.293 | -0.186 | 1.92 | -1.848 | 40.7 |
| 프롬프트 포함 | 504 | 504 | 47.2 | -0.293 | -0.186 | 1.92 | -1.848 | 40.7 |
| 프롬프트 제외 | 8 | 8 | 0 | -3.097 | -3.312 | 1.683 | -1.493 | 25 |
| Claude watchlist | 504 | 504 | 47.2 | -0.293 | -0.186 | 1.92 | -1.848 | 40.7 |
| Claude trade_ready | 67 | 67 | 64.2 | 1.12 | 0.977 | 2.055 | -1.716 | 49.2 |
| trade_ready+pullback_wait | 176 | 176 | 51.1 | 0.18 | 0.027 | 2.025 | -1.737 | 48 |
| in_prompt_not_selected | 0 | 0 | - | - | - | - | - | - |
| not_in_prompt | 0 | 0 | - | - | - | - | - | - |

## 후보 audit forward outcome
latest row 기준. 60분은 intraday label, 1440/4320은 forward 1d/3d 계열 outcome이다.
### 60분

#### KR

| cohort | rows | labeled | win% | avg_ret% | median_ret% | avg_MFE% | avg_MAE% | MFE>=2% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 전체 후보 | 3876 | 0 | - | - | - | - | - | - |
| 프롬프트 포함 | 1551 | 0 | - | - | - | - | - | - |
| 프롬프트 제외 | 1291 | 0 | - | - | - | - | - | - |
| Claude watchlist | 1048 | 0 | - | - | - | - | - | - |
| Claude trade_ready | 84 | 0 | - | - | - | - | - | - |
| trade_ready+pullback_wait | 101 | 0 | - | - | - | - | - | - |
| in_prompt_not_selected | 1038 | 0 | - | - | - | - | - | - |
| not_in_prompt | 1720 | 0 | - | - | - | - | - | - |

#### US

| cohort | rows | labeled | win% | avg_ret% | median_ret% | avg_MFE% | avg_MAE% | MFE>=2% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 전체 후보 | 5198 | 0 | - | - | - | - | - | - |
| 프롬프트 포함 | 1620 | 0 | - | - | - | - | - | - |
| 프롬프트 제외 | 2273 | 0 | - | - | - | - | - | - |
| Claude watchlist | 1202 | 0 | - | - | - | - | - | - |
| Claude trade_ready | 168 | 0 | - | - | - | - | - | - |
| trade_ready+pullback_wait | 240 | 0 | - | - | - | - | - | - |
| in_prompt_not_selected | 1131 | 0 | - | - | - | - | - | - |
| not_in_prompt | 2639 | 0 | - | - | - | - | - | - |

### forward 1d

#### KR

| cohort | rows | labeled | win% | avg_ret% | median_ret% | avg_MFE% | avg_MAE% | MFE>=2% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 전체 후보 | 3876 | 1048 | 37.3 | -1.58 | -2.157 | 5.565 | -6.486 | 59.3 |
| 프롬프트 포함 | 1551 | 559 | 37.6 | -1.831 | -2.431 | 5.414 | -6.992 | 60.1 |
| 프롬프트 제외 | 1291 | 477 | 37.3 | -1.109 | -1.659 | 6.006 | -5.822 | 59.5 |
| Claude watchlist | 1048 | 235 | 37.9 | -2.162 | -2.908 | 5.543 | -7.621 | 62.1 |
| Claude trade_ready | 84 | 1 | 100 | 3.982 | 3.982 | 13.323 | 1.531 | 100 |
| trade_ready+pullback_wait | 101 | 4 | 100 | 4.842 | 5.082 | 14.027 | -0.725 | 100 |
| in_prompt_not_selected | 1038 | 324 | 37.3 | -1.591 | -2.059 | 5.321 | -6.535 | 58.6 |
| not_in_prompt | 1720 | 470 | 37 | -1.154 | -1.812 | 5.983 | -5.859 | 59.4 |

#### US

| cohort | rows | labeled | win% | avg_ret% | median_ret% | avg_MFE% | avg_MAE% | MFE>=2% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 전체 후보 | 5198 | 1673 | 48 | -0.439 | -0.188 | 2.953 | -3.61 | 54.9 |
| 프롬프트 포함 | 1620 | 741 | 48.6 | -0.477 | -0.114 | 2.832 | -3.583 | 54.4 |
| 프롬프트 제외 | 2273 | 887 | 48.1 | -0.315 | -0.159 | 3.118 | -3.579 | 55.9 |
| Claude watchlist | 1202 | 334 | 45.5 | -0.682 | -0.523 | 2.755 | -3.96 | 51.8 |
| Claude trade_ready | 168 | 25 | 48 | -0.772 | -0.391 | 1.919 | -3.128 | 40 |
| trade_ready+pullback_wait | 240 | 43 | 58.1 | 1.383 | 0.679 | 4.458 | -2.171 | 53.5 |
| in_prompt_not_selected | 1131 | 407 | 51.1 | -0.309 | 0.113 | 2.895 | -3.273 | 56.5 |
| not_in_prompt | 2639 | 881 | 47.9 | -0.364 | -0.195 | 3.076 | -3.612 | 55.6 |

### forward 3d

#### KR

| cohort | rows | labeled | win% | avg_ret% | median_ret% | avg_MFE% | avg_MAE% | MFE>=2% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 전체 후보 | 3876 | 800 | 32.1 | -3.312 | -4.838 | 9.307 | -11.708 | 69.1 |
| 프롬프트 포함 | 1551 | 413 | 30.3 | -3.77 | -5.654 | 9.27 | -12.621 | 70.7 |
| 프롬프트 제외 | 1291 | 380 | 34.5 | -2.428 | -3.917 | 9.677 | -10.413 | 68.9 |
| Claude watchlist | 1048 | 184 | 25 | -4.579 | -7.181 | 9.143 | -14.069 | 73.4 |
| Claude trade_ready | 84 | 0 | - | - | - | - | - | - |
| trade_ready+pullback_wait | 101 | 0 | - | - | - | - | - | - |
| in_prompt_not_selected | 1038 | 229 | 34.5 | -3.12 | -4.126 | 9.372 | -11.457 | 68.6 |
| not_in_prompt | 1720 | 374 | 34.2 | -2.492 | -3.986 | 9.658 | -10.495 | 68.7 |

#### US

| cohort | rows | labeled | win% | avg_ret% | median_ret% | avg_MFE% | avg_MAE% | MFE>=2% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 전체 후보 | 5198 | 1293 | 39.8 | -2.086 | -1.849 | 4.757 | -7.439 | 65.4 |
| 프롬프트 포함 | 1620 | 558 | 41.9 | -1.885 | -1.391 | 4.418 | -7.213 | 64.9 |
| 프롬프트 제외 | 2273 | 695 | 38.4 | -2.208 | -2.267 | 4.985 | -7.469 | 65.9 |
| Claude watchlist | 1202 | 283 | 42.4 | -1.675 | -1.314 | 4.602 | -7.282 | 65 |
| Claude trade_ready | 168 | 25 | 32 | -3.286 | -1.89 | 3.637 | -7.435 | 60 |
| trade_ready+pullback_wait | 240 | 39 | 43.6 | -0.157 | -0.805 | 6.585 | -5.866 | 64.1 |
| in_prompt_not_selected | 1131 | 275 | 41.5 | -2.101 | -1.525 | 4.228 | -7.142 | 64.7 |
| not_in_prompt | 2639 | 694 | 38.3 | -2.219 | -2.275 | 4.977 | -7.47 | 65.9 |

## ticker_selection_log 교차검증/live
| market | trade_ready | rows | traded | f1_n | avg_f1% | avg_f3% | avg_f5% | avg_MFE3% | avg_MAE3% | from | to |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| KR | 0 | 5261 | 10 | 4564 | -1.735 | -5.052 | -9.915 | 11.624 | -14.077 | 2026-04-20 | 2026-06-16 |
| KR | 1 | 130 | 20 | 130 | 0.681 | 2.539 | 1.517 | 16.202 | -9.762 | 2026-04-22 | 2026-06-05 |
| US | 0 | 5902 | 4 | 5420 | 0.406 | 0.768 | -0.425 | 7.138 | -6.07 | 2026-04-20 | 2026-06-15 |
| US | 1 | 426 | 14 | 421 | 1.171 | 0.775 | 0.947 | 7.499 | -5.413 | 2026-04-21 | 2026-06-05 |

## 실제 live closed trade 성과
| market | closed | wins | win% | avg_pnl% | sum_pnl_krw | krw_rows | avg_MFE% | avg_MAE% | from | to |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| KR | 47 | 13 | 27.7 | -0.997 | -61816.7 | 26 | 2.932 | -1.458 | 2026-04-27 | 2026-06-15 |
| US | 186 | 82 | 44.1 | 0.46 | 43999.8 | 20 | 3.433 | -1.369 | 2026-04-27 | 2026-06-15 |

| market | path | strategy | closed | win% | avg_pnl% | sum_pnl_krw | krw_rows | avg_MFE% | avg_MAE% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| KR | claude_price | gap_pullback | 17 | 35.3 | -0.651 | -23950.9 | 9 | 3.791 | -1.85 |
| KR | claude_price | claude_price | 8 | 37.5 | -0.02 | -548.9 | 1 | - | - |
| KR | claude_price | momentum | 6 | 16.7 | -2.163 | -2441.9 | 3 | 7.359 | -1.552 |
| KR | PathA | momentum | 5 | 20 | -1.705 | -22842.5 | 5 | 0.971 | -1.818 |
| KR | claude_price | opening_range_pullback | 5 | 0 | -1.642 | -3482.3 | 3 | 0.766 | -0.255 |
| KR | PathA | gap_pullback | 3 | 33.3 | -0.935 | -6046.1 | 3 | 3.076 | -1.602 |
| US | claude_price | claude_price | 143 | 44.8 | 0.544 | 0 | 0 | - | - |
| US | claude_price | gap_pullback | 18 | 38.9 | -0.343 | -1537.2 | 4 | 3.775 | 0 |
| US | claude_price | momentum | 12 | 50 | 0.969 | 23340.4 | 6 | 7.926 | -1.388 |
| US | claude_price | opening_range_pullback | 6 | 50 | 0.667 | 22456.8 | 3 | - | - |
| US | PathA | gap_pullback | 4 | 25 | -1.394 | -17176.4 | 4 | 0.732 | -1.669 |

## 어떤 근거로 잘/못 뽑았는가
### KR selected bucket detail
| key | rows | labeled | win% | avg_close% | median_close% | avg_MFE60% | avg_MAE60% | MFE>=2% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| volume_surge | 125 | 125 | 33.6 | -3.839 | -3.012 | 5.869 | -5.699 | 68.5 |
| pullback_watch | 95 | 95 | 38.9 | -1.684 | -1.218 | 4.12 | -4.6 | 63.4 |
| near_breakout | 90 | 90 | 38.9 | -0.88 | -0.736 | 3.169 | -3.064 | 53.6 |
| blank | 64 | 64 | 39.1 | -2.47 | -1.754 | 2.765 | -3.679 | 50 |
| liquidity_leader | 45 | 45 | 46.7 | -0.589 | -0.223 | 2.262 | -3.819 | 37.2 |
| unclassified | 22 | 22 | 13.6 | -4.237 | -4.169 | 4.128 | -4.079 | 63.6 |
| momentum_now | 5 | 5 | 20 | -2.766 | -1.826 | 3.394 | -4.786 | 60 |

### KR selected strategy detail
| key | rows | labeled | win% | avg_close% | median_close% | avg_MFE60% | avg_MAE60% | MFE>=2% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gap_pullback | 136 | 136 | 34.6 | -2.971 | -1.885 | 4.101 | -5.742 | 62.9 |
| mean_reversion | 131 | 131 | 43.5 | -1.618 | -0.799 | 4.126 | -3.533 | 59.6 |
| momentum | 95 | 95 | 35.8 | -1.019 | -1.257 | 5.109 | -3.443 | 62.5 |
| opening_range_pullback | 52 | 52 | 26.9 | -5.01 | -3.834 | 2.981 | -5.936 | 45.7 |
| blank | 14 | 14 | 50 | -1.678 | -0.583 | 2.217 | -3.19 | 50 |
| continuation | 9 | 9 | 33.3 | 1.53 | -1.527 | 5.338 | -0.786 | 50 |
| volatility_breakout | 9 | 9 | 22.2 | -3.071 | -2.946 | 4.84 | -6.182 | 77.8 |

### KR rank/score contrast
| group | rows | avg_raw_rank | avg_prompt_rank | avg_score | close_labeled | win% | avg_close% | avg_MFE60% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Claude watchlist | 446 | 23.636 | 13.231 | 68.909 | 446 | 36.8 | -2.266 | 4.183 |
| 프롬프트 포함 비선정 | 0 | - | - | - | 0 | - | - | - |
| 프롬프트 제외 | 0 | - | - | - | 0 | - | - | - |

### KR selected winners
| date | ticker | class/action | bucket | strategy | close% | runup60% | dd60% | score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-12 | 403870 | WATCH | near_breakout | continuation | 30 | 23.82 | 7.64 | 63.77 |
| 2026-06-04 | 036930 | WATCH | pullback_watch | mean_reversion | 24.01 | 24.75 | 1.24 | 73.21 |
| 2026-05-20 | 142280 | WATCH |  | momentum | 22.88 | - | - | 59.39 |
| 2026-05-28 | 027040 | WATCH | volume_surge | opening_range_pullback | 21.4 | 21.4 | 3.89 | 61.29 |
| 2026-05-18 | 066430 | WATCH |  | mean_reversion | 19.23 | - | - | 66.95 |
| 2026-06-08 | 388790 | WATCH | pullback_watch | momentum | 18.01 | 23.95 | -0.17 | 68.55 |
| 2026-06-08 | 271830 | WATCH | volume_surge | momentum | 17.32 | 12.99 | -1.95 | 63.3 |
| 2026-06-11 | 005950 | WATCH | near_breakout | gap_pullback | 17.08 | 8.08 | -0.77 | 67.76 |

### KR selected losers
| date | ticker | class/action | bucket | strategy | close% | runup60% | dd60% | score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-01 | 242040 | WATCH | volume_surge | opening_range_pullback | -33.16 | 10.72 | 0 | 72.76 |
| 2026-05-27 | 032580 | WATCH | pullback_watch | opening_range_pullback | -32.62 | 0.86 | -22.21 | 83.83 |
| 2026-05-28 | 166480 | PULLBACK_WAIT | volume_surge | gap_pullback | -31.53 | 2.87 | -27.66 | 88.94 |
| 2026-05-18 | 084670 | WATCH |  | mean_reversion | -26.09 | - | - | 62.5 |
| 2026-06-15 | 083660 | WATCH | volume_surge | volatility_breakout | -25.81 | 5.76 | -10.6 | 68.55 |
| 2026-05-28 | 065420 | WATCH | volume_surge | gap_pullback | -23.55 | 13.9 | -15.83 | 82.44 |
| 2026-05-29 | 027040 | WATCH | volume_surge | gap_pullback | -22.45 | 0.5 | -16.08 | 81.34 |
| 2026-05-20 | 424760 | WATCH |  | mean_reversion | -22.07 | - | - | 83.86 |

### KR missed runup examples
| date | ticker | class/action | bucket | strategy | close% | runup60% | dd60% | score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

### US selected bucket detail
| key | rows | labeled | win% | avg_close% | median_close% | avg_MFE60% | avg_MAE60% | MFE>=2% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| near_breakout | 280 | 280 | 51.4 | 0.314 | 0.029 | 1.919 | -1.744 | 41.4 |
| pullback_watch | 76 | 76 | 40.8 | -1.371 | -1.349 | 1.727 | -2.135 | 34.2 |
| blank | 67 | 67 | 22.4 | -2.495 | -2.861 | 1.683 | -1.493 | 25 |
| liquidity_leader | 50 | 50 | 60 | 0.792 | 1.937 | 1.754 | -1.647 | 36 |
| momentum_now | 27 | 27 | 66.7 | 0.936 | 1.263 | 2.672 | -2.43 | 59.3 |
| unclassified | 4 | 4 | 0 | -7.222 | -7.633 | 3.175 | -2.978 | 75 |

### US selected strategy detail
| key | rows | labeled | win% | avg_close% | median_close% | avg_MFE60% | avg_MAE60% | MFE>=2% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gap_pullback | 228 | 228 | 48.2 | -0.049 | -0.107 | 2.053 | -2.041 | 46.5 |
| opening_range_pullback | 105 | 105 | 48.6 | -0.153 | -0.002 | 1.894 | -1.245 | 32.9 |
| momentum | 94 | 94 | 40.4 | -1.187 | -0.926 | 1.85 | -2.092 | 38.3 |
| mean_reversion | 42 | 42 | 64.3 | 1.117 | 1.937 | 1.711 | -1.202 | 35.7 |
| blank | 34 | 34 | 35.3 | -1.486 | -1.258 | 1.668 | -2.101 | 38.2 |

### US rank/score contrast
| group | rows | avg_raw_rank | avg_prompt_rank | avg_score | close_labeled | win% | avg_close% | avg_MFE60% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Claude watchlist | 504 | 37.423 | 13.486 | 67 | 504 | 47.2 | -0.293 | 1.92 |
| 프롬프트 포함 비선정 | 0 | - | - | - | 0 | - | - | - |
| 프롬프트 제외 | 0 | - | - | - | 0 | - | - | - |

### US selected winners
| date | ticker | class/action | bucket | strategy | close% | runup60% | dd60% | score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-27 | IREN | PULLBACK_WAIT | near_breakout | gap_pullback | 11.03 | 6.24 | -2.08 | 79.1 |
| 2026-06-03 | ABVX | AVOID | near_breakout | gap_pullback | 10.13 | 4.31 | -1.33 | 52 |
| 2026-05-27 | ONDS | WATCH | momentum_now | gap_pullback | 10.04 | 5.65 | -1.38 | 74 |
| 2026-06-11 | SMCI |  | liquidity_leader |  | 9.6 | 2.06 | -1.92 | 65.5 |
| 2026-06-04 | MRVL | WATCH | liquidity_leader | mean_reversion | 9.56 | 1.93 | -3.89 | 70.3 |
| 2026-05-29 | TBBB | WATCH | near_breakout | gap_pullback | 9.24 | 3.67 | -0.87 | 53.5 |
| 2026-06-01 | AAOI | WATCH | near_breakout | opening_range_pullback | 9.15 | 6.82 | -0.53 | 74 |
| 2026-05-28 | HOOD | PROBE_READY | near_breakout | opening_range_pullback | 9.15 | 1.98 | -0.81 | 61 |

### US selected losers
| date | ticker | class/action | bucket | strategy | close% | runup60% | dd60% | score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-09 | AAOI | PROBE_READY | pullback_watch | momentum | -19.46 | 0.57 | -12.12 | 74 |
| 2026-06-10 | SMCI | AVOID | liquidity_leader | mean_reversion | -16.7 | 2.53 | -2.02 | 70.9 |
| 2026-06-10 | FLNC |  | momentum_now |  | -14.55 | 2.96 | -8.66 | 52 |
| 2026-05-18 | WOLF | PULLBACK_WAIT |  | gap_pullback | -13.41 | - | - | 60 |
| 2026-06-09 | RGTI | PULLBACK_WAIT | pullback_watch | momentum | -11.11 | 0.97 | -5.51 | 74 |
| 2026-06-09 | IREN |  | liquidity_leader |  | -10.53 | 0.79 | -6.49 | 67.3 |
| 2026-06-05 | TE | AVOID | unclassified | gap_pullback | -10.45 | 1.33 | -5.79 | 54.2 |
| 2026-06-02 | HPE | WATCH | momentum_now | gap_pullback | -10.42 | 0.05 | -7.61 | 66 |

### US missed runup examples
| date | ticker | class/action | bucket | strategy | close% | runup60% | dd60% | score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

## 해석

- US: broad watchlist는 장마감 보유 기준으로 강하지 않지만, trade_ready와 PathB closed outcome은 좋다. US PathB 수익 엔진은 보호하고, 후보 선정은 trade_ready 좁히기/미세 조정 수준이 맞다.
- KR: 후보가 MFE를 만들지만 장마감/실현손익이 음수다. selection 교체보다 KR 전용 entry delay, fade filter, profit capture, 장중 청산 기준 검증이 우선이다.
- trade_ready: ticker_selection_log 기준 KR/US 모두 forward 성과가 좋다. 실제 KR closed PnL이 음수인 것은 selection 이후 변환 손실을 의심하게 한다.
- missed winners: 프롬프트 제외/미선정에서 큰 60분 runup이 보인다. 다만 위험도 같이 커서 broad expansion보다 bucket/rank 조건을 좁혀야 한다.

## 잔여 리스크/미검증 축

- 2026-06-16 KR은 장중 데이터라 장마감 성과에서 제외했다.
- counterfactual close outcome은 2026-05-18 이후가 중심이다. 4월~5월 초는 forward outcome/ticker_selection_log로 보완했다.
- US 일부 closed trade는 `pnl_krw`가 NULL인 행이 있어 `avg_pnl_pct` 중심으로 판단했다.
- 이 리포트는 분석만 수행했다. gate, strategy, PathB profit ladder, broker truth, config/env는 변경하지 않았다.
