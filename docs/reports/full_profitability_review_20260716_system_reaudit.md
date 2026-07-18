# Full Profitability Review

Generated: 2026-07-16T22:41:24

## Basis

- closed_trades: 331
- selection_rows: 31367
- preopen_rows: 5995
- valid_preopen_rows: 5939
- screener_quality_rows: 121214
- action_routing_events: 3468
- cohort_files: 1112

Notes:
- All inputs are local files or sqlite rows; no broker/API/Claude calls are made.
- Preopen entry simulations are approximate: entry-to-final uses sampled anchor returns, not tick-level fills.
- Forward return fields in ticker_selection_log are post-selection audit labels and must not be used inside live gating without known_at controls.

## Closed Trades By Market
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR | 75 | 26/49 | 34.7% | -0.757% | -0.985% | 0.60 | -13.624% | +23.803% |
| US | 256 | 101/155 | 39.5% | +0.144% | -0.401% | 1.16 | -6.663% | +17.667% |

## Closed Trades By Strategy
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| US|claude_price | 221 | 89/132 | 40.3% | +0.174% | -0.305% | 1.21 | -6.306% | +17.667% |
| KR|momentum | 32 | 10/22 | 31.2% | -0.581% | -1.350% | 0.68 | -12.234% | +23.803% |
| KR|claude_price | 31 | 12/19 | 38.7% | -0.303% | -0.985% | 0.80 | -9.818% | +6.798% |
| US|gap_pullback | 11 | 5/6 | 45.5% | +0.638% | -1.340% | 1.46 | -3.746% | +7.843% |
| US|momentum | 6 | 3/3 | 50.0% | +1.501% | +1.283% | 3.35 | -2.248% | +5.726% |
| KR|continuation | 3 | 0/3 | 0.0% | -6.920% | -8.786% | 0.00 | -9.548% | -2.425% |
| KR|kr_sector_play | 3 | 2/1 | 66.7% | +1.726% | +2.565% | 3.85 | -1.815% | +4.427% |
| KR|gap_pullback | 2 | 1/1 | 50.0% | -0.379% | -0.379% | 0.03 | -0.783% | +0.026% |
| US|mean_reversion | 2 | 2/0 | 100.0% | +0.284% | +0.284% | inf | +0.159% | +0.408% |
| KR|RECOVERY_MICRO | 1 | 0/1 | 0.0% | -0.323% | -0.323% | 0.00 | -0.323% | -0.323% |
| US|MICRO_PROBE | 1 | 0/1 | 0.0% | -0.449% | -0.449% | 0.00 | -0.449% | -0.449% |
| US|RECOVERY_MICRO | 1 | 0/1 | 0.0% | -1.500% | -1.500% | 0.00 | -1.500% | -1.500% |
| US|opening_range_pullback | 1 | 1/0 | 100.0% | +11.346% | +11.346% | inf | +11.346% | +11.346% |

## Broker Sync Operational Cases
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| US|broker_sync | 13 | 1/12 | 7.7% | -2.122% | -1.549% | 0.03 | -6.663% | +0.782% |
| KR|broker_sync | 3 | 1/2 | 33.3% | -4.045% | -0.872% | 0.16 | -13.624% | +2.361% |

## Selection Live Traded By Ready
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR|ready=0 | 10 | 3/7 | 30.0% | -2.172% | -0.794% | 0.14 | -13.624% | +1.780% |
| KR|ready=1 | 18 | 5/13 | 27.8% | -0.971% | -1.759% | 0.37 | -5.010% | +8.374% |
| US|ready=0 | 3 | 1/2 | 33.3% | +1.329% | -0.111% | 2.03 | -3.746% | +7.843% |
| US|ready=1 | 14 | 5/9 | 35.7% | +0.356% | -1.141% | 1.31 | -3.528% | +7.697% |

## Selection Live Traded By Strategy
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR|momentum | 24 | 7/17 | 29.2% | -1.552% | -1.759% | 0.27 | -13.624% | +8.374% |
| US|gap_pullback | 9 | 3/6 | 33.3% | +0.252% | -1.340% | 1.16 | -3.746% | +7.843% |
| US|momentum | 6 | 3/3 | 50.0% | +1.501% | +1.283% | 3.35 | -2.248% | +5.726% |
| KR|RECOVERY_MICRO | 2 | 0/2 | 0.0% | -0.597% | -0.597% | 0.00 | -0.872% | -0.323% |
| KR|gap_pullback | 2 | 1/1 | 50.0% | -0.379% | -0.379% | 0.03 | -0.783% | +0.026% |
| US|RECOVERY_MICRO | 1 | 0/1 | 0.0% | -1.500% | -1.500% | 0.00 | -1.500% | -1.500% |
| US|continuation | 1 | 0/1 | 0.0% | -0.800% | -0.800% | 0.00 | -0.800% | -0.800% |

## Selection Forward Max Runup By Ready
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR|ready=0 | 11542 | 10101/1349 | 87.5% | +12.274% | +7.610% | 38.96 | -29.954% | +126.998% |
| KR|ready=1 | 130 | 116/13 | 89.2% | +16.202% | +8.382% | 56.64 | -8.707% | +119.363% |
| US|ready=0 | 13533 | 11969/1537 | 88.4% | +5.662% | +4.103% | 15.09 | -88.922% | +98.038% |
| US|ready=1 | 527 | 464/62 | 88.0% | +6.769% | +5.140% | 22.89 | -9.484% | +38.776% |

## Preopen By Market
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR | 2970 | 1090/1790 | 36.7% | -1.040% | -1.976% | 0.74 | -50.000% | +177.778% |
| US | 2969 | 1392/1551 | 46.9% | -0.189% | -0.186% | 0.90 | -26.070% | +43.274% |

## Preopen Segments

### KR
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 2970 | 1090/1790 | 36.7% | -1.040% | -1.976% | 0.74 | -50.000% | +177.778% |
| actual_selected | 603 | 267/332 | 44.3% | -0.144% | -1.800% | 0.97 | -30.000% | +30.000% |
| actual_trade_ready | 0 | 0/0 | 0.0% | +0.000% | +0.000% | NA | +0.000% | +0.000% |
| hard_pin_current | 106 | 35/68 | 33.0% | -3.504% | -5.216% | 0.40 | -28.431% | +29.889% |
| soft_b | 830 | 310/473 | 37.4% | -0.846% | -2.131% | 0.80 | -30.000% | +30.000% |
| low_liq_tag | 1864 | 662/1180 | 35.5% | -1.334% | -2.213% | 0.67 | -50.000% | +177.778% |
| rank_1_10 | 498 | 174/310 | 34.9% | -1.310% | -2.832% | 0.71 | -28.431% | +30.000% |
| rank_11_30 | 992 | 377/581 | 38.0% | -0.793% | -1.753% | 0.80 | -49.519% | +30.000% |
| rank_31_plus | 1480 | 539/899 | 36.4% | -1.114% | -1.918% | 0.71 | -50.000% | +177.778% |

### US
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 2969 | 1392/1551 | 46.9% | -0.189% | -0.186% | 0.90 | -26.070% | +43.274% |
| actual_selected | 602 | 296/306 | 49.2% | -0.025% | -0.038% | 0.99 | -26.070% | +18.500% |
| actual_trade_ready | 1 | 1/0 | 100.0% | +6.868% | +6.868% | inf | +6.868% | +6.868% |
| hard_pin_current | 136 | 64/72 | 47.1% | -0.173% | -0.611% | 0.93 | -18.545% | +22.732% |
| soft_b | 1079 | 530/543 | 49.1% | -0.098% | -0.019% | 0.95 | -19.297% | +23.366% |
| low_liq_tag | 167 | 79/84 | 47.3% | -0.200% | -0.072% | 0.86 | -9.310% | +8.959% |
| rank_1_10 | 509 | 236/270 | 46.4% | -0.351% | -0.294% | 0.85 | -19.297% | +23.366% |
| rank_11_30 | 1020 | 493/519 | 48.3% | -0.093% | -0.069% | 0.95 | -16.556% | +36.778% |
| rank_31_plus | 1440 | 663/762 | 46.0% | -0.199% | -0.224% | 0.89 | -26.070% | +43.274% |

## Preopen Rule Simulations

### KR
| Rule | Basis | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| current_hard_pin | final | 106 | 35/68 | 33.0% | -3.504% | -5.216% | 0.40 | -28.431% | +29.889% |
| soft_b_naive | final | 830 | 310/473 | 37.4% | -0.846% | -2.131% | 0.80 | -30.000% | +30.000% |
| soft_b_confirm30 | final | 321 | 235/76 | 73.2% | +6.317% | +3.571% | 5.86 | -19.326% | +30.000% |
| soft_b_confirm30 | entry_30m_to_final | 321 | 114/178 | 35.5% | -1.626% | -0.567% | 0.52 | -32.353% | +28.157% |
| soft_b_confirm60 | final | 279 | 223/54 | 79.9% | +7.546% | +4.909% | 7.23 | -19.326% | +30.000% |
| soft_b_confirm60 | entry_60m_to_final | 279 | 101/158 | 36.2% | -1.795% | -0.709% | 0.47 | -30.696% | +24.132% |
| low_liq_ignite60 | final | 154 | 136/17 | 88.3% | +10.446% | +8.599% | 21.87 | -24.715% | +30.000% |
| low_liq_ignite60 | entry_60m_to_final | 154 | 57/82 | 37.0% | -1.431% | -0.737% | 0.51 | -39.078% | +19.344% |
| late_reclaim_watch | final | 55 | 42/12 | 76.4% | +9.185% | +7.860% | 7.48 | -23.609% | +30.000% |
| late_reclaim_watch | entry_120m_to_final | 55 | 14/34 | 25.4% | -3.767% | -2.542% | 0.34 | -31.592% | +21.132% |

### US
| Rule | Basis | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| current_hard_pin | final | 136 | 64/72 | 47.1% | -0.173% | -0.611% | 0.93 | -18.545% | +22.732% |
| soft_b_naive | final | 1079 | 530/543 | 49.1% | -0.098% | -0.019% | 0.95 | -19.297% | +23.366% |
| soft_b_confirm30 | final | 511 | 389/117 | 76.1% | +3.078% | +2.346% | 7.12 | -10.384% | +23.366% |
| soft_b_confirm30 | entry_30m_to_final | 511 | 247/252 | 48.3% | -0.262% | +0.000% | 0.82 | -12.289% | +14.806% |
| soft_b_confirm60 | final | 416 | 340/76 | 81.7% | +3.810% | +2.980% | 12.78 | -8.006% | +23.366% |
| soft_b_confirm60 | entry_60m_to_final | 416 | 193/219 | 46.4% | -0.231% | -0.200% | 0.82 | -10.912% | +11.508% |
| low_liq_ignite60 | final | 1 | 1/0 | 100.0% | +0.346% | +0.346% | inf | +0.346% | +0.346% |
| low_liq_ignite60 | entry_60m_to_final | 1 | 0/1 | 0.0% | -4.522% | -4.522% | 0.00 | -4.522% | -4.522% |
| late_reclaim_watch | final | 54 | 49/5 | 90.7% | +5.462% | +4.012% | 23.87 | -3.920% | +23.348% |
| late_reclaim_watch | entry_120m_to_final | 54 | 29/24 | 53.7% | +0.382% | +0.071% | 1.34 | -6.417% | +10.850% |

## Missed Strong Preopen Candidates
| session_date | market | ticker | rank | score | risk_tags | selected | trade_ready | final | mfe | mae | ret5 | ret30 | ret60 | hard_pin | soft_b | low_liq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-06-23 | KR | 227100 | 35 | 0.26 | ['low_liquidity'] | False | False | 177.7778 | 233.3333 | 27.7778 | 27.7778 | 66.6667 | 138.8889 | False | False | True |
| 2026-06-01 | US | FLNC | 53 | 0.37 | [] | False | False | 43.2738 | 52.9661 | 23.1155 | 39.1128 | 25.474 | 29.706 | False | False | False |
| 2026-05-14 | US | POET | 26 | 0.43 | [] | False | False | 36.778 | 43.7022 | 20.9882 | 39.6722 | 30.1322 | 26.2352 | False | False | False |
| 2026-05-11 | KR | 012205 | 9 | 0.18 | ['low_liquidity'] | False | False | 30.0 | 30.0 | -6.6346 | 3.1731 | 30.0 | 30.0 | False | False | True |
| 2026-05-11 | KR | 007610 | 36 | 0.18 | ['low_liquidity'] | False | False | 30.0 | 30.0 | -13.8384 | -8.6869 | -10.101 | -11.0101 | False | False | True |
| 2026-05-12 | KR | 439960 | 18 | 0.6 | ['limit_up_chase_risk'] | False | False | 30.0 | 30.0 | 5.0 | 16.0417 | 30.0 | 30.0 | False | True | False |
| 2026-05-13 | KR | 007460 | 20 | 0.6 | ['limit_up_chase_risk'] | False | False | 30.0 | 30.0 | 0.1923 | 20.3846 | 18.0769 | 21.5385 | False | True | False |
| 2026-05-21 | KR | 024840 | 23 | 0.51 | [] | False | False | 30.0 | 30.0 | 12.2642 | 23.5849 | 30.0 | 30.0 | False | True | False |
| 2026-05-26 | KR | 203650 | 28 | 0.18 | ['low_liquidity'] | False | False | 30.0 | 30.0 | 4.9296 | 17.4648 | 30.0 | 30.0 | False | False | True |
| 2026-05-26 | KR | 001740 | 50 | 0.18 | ['low_liquidity'] | False | False | 30.0 | 30.0 | 4.1667 | 12.0238 | 13.0952 | 8.9286 | False | False | True |
| 2026-06-08 | KR | 001740 | 20 | 0.18 | ['low_liquidity'] | True | False | 30.0 | 30.0 | -12.844 | -5.4128 | -5.4128 | 14.1284 | False | False | True |
| 2026-06-12 | KR | 403870 | 14 | 0.58 | [] | True | False | 30.0 | 30.0 | 2.9091 | 4.0 | 16.0 | 20.7273 | False | True | False |
| 2026-06-12 | KR | 079650 | 27 | 0.46 | [] | True | False | 30.0 | 30.0 | 8.2308 | 16.6923 | 10.0 | 12.1538 | False | False | False |
| 2026-06-17 | KR | 079650 | 31 | 0.42 | ['limit_up_chase_risk'] | False | False | 30.0 | 30.0 | 5.614 | 30.0 | 30.0 | 30.0 | False | False | False |
| 2026-06-18 | KR | 198440 | 7 | 0.72 | ['limit_up_chase_risk'] | True | False | 30.0 | 30.0 | 7.0629 | 9.9301 | 20.0699 | 12.2378 | False | True | False |
| 2026-06-22 | KR | 475430 | 6 | 0.3 | ['low_liquidity'] | True | False | 30.0 | 30.0 | 1.2766 | 5.0 | 11.2766 | 16.1702 | False | False | True |
| 2026-07-06 | KR | 002990 | 44 | 0.13 | ['low_liquidity'] | False | False | 30.0 | 30.0 | -8.9474 | -6.1053 | -0.3158 | 6.6316 | False | False | True |
| 2026-07-07 | KR | 365660 | 17 | 0.46 | [] | False | False | 30.0 | 30.0 | -9.3617 | 10.0 | 3.9362 | -6.7021 | False | True | False |
| 2026-07-08 | KR | 058730 | 28 | 0.18 | ['low_liquidity'] | True | False | 30.0 | 30.0 | -7.5 | -2.125 | -5.75 | -2.25 | False | False | True |
| 2026-07-10 | KR | 073240 | 10 | 0.63 | [] | True | False | 30.0 | 30.0 | 0.1667 | 3.1667 | 13.0 | 20.8333 | False | True | False |
| 2026-07-13 | KR | 036420 | 56 | 0.13 | ['low_liquidity'] | False | False | 30.0 | 30.0 | 1.3158 | 14.7368 | 30.0 | 30.0 | False | False | True |
| 2026-07-15 | KR | 003680 | 58 | 0.13 | ['low_liquidity'] | False | False | 29.991 | 29.991 | 5.6401 | 20.3223 | 19.0689 | 16.6517 | False | False | True |
| 2026-05-20 | KR | 066980 | 34 | 0.3 | ['low_liquidity'] | False | False | 29.9862 | 29.9862 | 7.7717 | 25.1719 | 29.6424 | 29.9862 | False | False | True |
| 2026-07-03 | KR | 065170 | 7 | 0.6 | ['limit_up_chase_risk'] | False | False | 29.9859 | 29.9859 | -5.2334 | -1.9802 | 26.4498 | 14.1443 | False | True | False |
| 2026-06-08 | KR | 066430 | 7 | 0.18 | ['low_liquidity'] | True | False | 29.9848 | 29.9848 | 2.8919 | 16.1339 | 19.7869 | 29.9848 | False | False | True |

## Expanded Rule Risks
| session_date | market | ticker | rank | score | risk_tags | selected | trade_ready | final | mfe | mae | ret5 | ret30 | ret60 | hard_pin | soft_b | low_liq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-05-15 | KR | 487580 | 13 | 0.6 | ['limit_up_chase_risk'] | False | False | -30.0 | 30.0 | -30.0 | 26.5 | -9.65 | -17.4 | False | True | False |
| 2026-07-02 | KR | 111710 | 17 | 0.55 | ['limit_up_chase_risk'] | False | False | -29.9435 | -10.8757 | -29.9435 | -10.8757 | -17.3729 | -18.5028 | False | True | False |
| 2026-07-02 | KR | 043260 | 3 | 0.75 | [] | False | False | -28.4314 | -3.7582 | -29.902 | -9.3137 | -19.4444 | -24.6732 | True | True | False |
| 2026-05-29 | KR | 027040 | 9 | 0.6 | ['limit_up_chase_risk'] | False | False | -25.8013 | 12.0192 | -28.8462 | -4.9679 | -12.3397 | -15.5449 | False | True | False |
| 2026-06-26 | KR | 378800 | 13 | 0.5 | [] | False | False | -25.1647 | -10.4084 | -28.5903 | -19.4993 | -22.1344 | -23.0567 | False | True | False |
| 2026-07-07 | KR | 042660 | 1 | 0.79 | [] | True | False | -22.6529 | -19.2076 | -25.0646 | -20.155 | -21.3609 | -22.6529 | True | True | False |
| 2026-07-03 | KR | 101730 | 4 | 0.7 | [] | False | False | -21.9149 | -8.9362 | -26.4894 | -13.7234 | -17.5532 | -18.8298 | False | True | False |
| 2026-06-09 | KR | 271830 | 6 | 0.6 | ['limit_up_chase_risk'] | False | False | -21.4022 | 19.1882 | -25.4613 | 14.0221 | -5.3506 | -8.3026 | False | True | False |
| 2026-05-13 | KR | 006345 | 1 | 0.75 | [] | False | False | -21.3163 | 9.1295 | -21.8259 | -10.828 | -9.9788 | -9.7665 | True | True | False |
| 2026-07-02 | KR | 240810 | 24 | 0.51 | [] | True | False | -20.5314 | -6.2198 | -20.7729 | -8.8164 | -13.7681 | -16.3647 | False | True | False |
| 2026-07-07 | KR | 477850 | 11 | 0.55 | ['limit_up_chase_risk'] | False | False | -20.0658 | -1.3158 | -25.0 | -12.5 | -4.2763 | -13.4868 | False | True | False |
| 2026-06-24 | KR | 177350 | 8 | 0.62 | ['risk_news'] | True | False | -19.346 | 0.1362 | -23.7057 | -9.8093 | -14.1689 | -10.218 | False | True | False |
| 2026-07-02 | KR | 002990 | 4 | 0.72 | ['limit_up_chase_risk'] | True | False | -19.326 | 29.9862 | -29.9862 | 29.9862 | 19.2572 | 14.3741 | False | True | False |
| 2026-05-14 | US | LWLG | 9 | 0.55 | [] | False | False | -19.2973 | -6.5331 | -21.6025 | -14.0269 | -19.2973 | -18.1993 | False | True | False |
| 2026-05-19 | KR | 412350 | 12 | 0.6 | ['limit_up_chase_risk'] | False | False | -19.1051 | 0.4972 | -22.8693 | -8.5227 | -13.7784 | -14.9148 | False | True | False |
| 2026-07-02 | KR | 089030 | 7 | 0.7 | [] | True | False | -18.75 | -5.137 | -19.2637 | -8.0479 | -11.8151 | -14.2123 | False | True | False |
| 2026-07-02 | KR | 000890 | 11 | 0.6 | ['limit_up_chase_risk'] | False | False | -18.7309 | -0.8753 | -21.8818 | -7.221 | -10.2845 | -5.2516 | False | True | False |
| 2026-06-18 | KR | 126640 | 10 | 0.63 | [] | True | False | -18.5552 | -2.1246 | -25.4958 | -7.932 | -5.5241 | -7.932 | False | True | False |
| 2026-07-07 | US | RIVN | 1 | 0.67 | [] | True | False | -18.5452 | -10.1787 | -18.57 | -13.5799 | -12.9345 | -12.86 | True | True | False |
| 2026-05-27 | KR | 021880 | 12 | 0.6 | ['limit_up_chase_risk'] | False | False | -18.4669 | 11.4983 | -19.1638 | 6.9686 | -7.3171 | -9.4077 | False | True | False |
| 2026-06-26 | KR | 419050 | 6 | 0.6 | ['limit_up_chase_risk'] | False | False | -18.389 | 18.442 | -20.4557 | 10.7578 | 9.433 | 11.2878 | False | True | False |
| 2026-06-12 | US | FLY | 6 | 0.67 | [] | False | False | -17.951 | -0.3051 | -20.2438 | -7.6708 | -10.5918 | -11.8364 | False | True | False |
| 2026-07-03 | KR | 090410 | 3 | 0.75 | [] | False | False | -17.8261 | -2.6087 | -20.4348 | -7.7391 | -13.8261 | -12.6957 | True | True | False |
| 2026-05-28 | KR | 229000 | 14 | 0.6 | ['limit_up_chase_risk'] | False | False | -17.682 | 15.0074 | -19.3165 | 14.4131 | -7.578 | -12.0357 | False | True | False |
| 2026-05-07 | KR | 007610 | 1 | 0.75 | [] | False | False | -17.6609 | 4.6311 | -21.4286 | -13.1083 | -19.7802 | -19.3093 | True | True | False |

## Missed Selection Runup Top
| date | market | ticker | trade_ready | signal_fired | blocked_reason | strategy | forward_1d | forward_3d | max_runup_3d | max_drawdown_3d |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-06-25 | KR | 002995 | 0 | 0 | None | None | 34.3183 | 126.998 | 126.998 | 27.6024 |
| 2026-06-25 | KR | 002995 | 0 | 0 | None | None | 34.3183 | 126.998 | 126.998 | 27.6024 |
| 2026-04-29 | KR | 024840 | 1 | 0 | None | gap_pullback | 29.9363 | 119.3631 | 119.3631 | 2.9299 |
| 2026-04-29 | KR | 024840 | 0 | 0 | None | None | 29.9363 | 119.3631 | 119.3631 | 2.9299 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |
| 2026-07-13 | KR | 005360 | 0 | 0 | None | None | 29.902 | 82.8431 | 119.3627 | 0.4902 |

## Daily Entry Caps
| Rule | Kept | N | W/L | Win | Avg | PF |
|---|---:|---:|---:|---:|---:|---:|
| total_cap_1 | 18/45 | 18 | 8/10 | 44.4% | -0.181% | 0.86 |
| total_cap_2 | 31/45 | 31 | 10/21 | 32.3% | -0.755% | 0.55 |
| total_cap_3 | 37/45 | 37 | 12/25 | 32.4% | -0.495% | 0.67 |
| per_market_cap_1 | 25/45 | 25 | 12/13 | 48.0% | +0.485% | 1.40 |
| per_market_cap_2 | 38/45 | 38 | 13/25 | 34.2% | -0.447% | 0.71 |
| per_market_cap_3 | 43/45 | 43 | 14/29 | 32.6% | -0.456% | 0.69 |

## Blocked Signals
| Reason | N | Ready | Fwd1D Avg | Runup3D Avg |
|---|---:|---:|---:|---:|
| ANALYST_MAX_GROSS_EXPOSURE_REACHED | 2 | 2 | +1.329% | +5.032% |
| ANALYST_NEW_BUY_BLOCK | 1 | 0 | -11.954% | -3.932% |
| DAILY_LOSS_LIMIT | 3 | 2 | +0.222% | +6.528% |
| HIGH_PRICE_BUDGET_BLOCK | 1 | 1 | -9.682% | +2.098% |
| INVALID_PRICE | 5 | 5 | +2.255% | +11.008% |
| MAX_DAILY_ENTRIES | 1 | 0 | +2.267% | +2.705% |
| ORDER_UNKNOWN_UNRESOLVED | 1 | 1 | +8.759% | +12.069% |
| PATHB_ORDER_UNKNOWN_SAME_TICKER | 1 | 1 | +7.601% | +13.948% |
| SAME_DAY_REENTRY_AFTER_STOP | 1 | 1 | -3.068% | +11.351% |
| insufficient_cash | 2 | 1 | +4.716% | +16.431% |
| order_rejected | 6 | 4 | +6.424% | +9.421% |
| order_size_too_small | 3 | 2 | +9.678% | +26.011% |
| permanent_order_reject | 1 | 1 | -4.179% | -2.486% |
| qty_zero | 6 | 3 | +4.778% | +10.105% |

## Screener Funnel

### All Status Counts
- US|NOT_IN_PROMPT: 36097
- KR|NOT_IN_PROMPT: 21170
- US|SCREENER_ONLY: 16519
- US|WATCH: 16357
- KR|WATCH: 15495
- KR|SCREENER_ONLY: 13062
- KR|VETO: 1199
- US|VETO: 923
- US|TRADE_READY: 373
- KR|TRADE_READY: 19

### Latest Status Counts
- US|NOT_IN_PROMPT: 917
- KR|NOT_IN_PROMPT: 512
- KR|WATCH: 153
- US|SCREENER_ONLY: 127
- KR|SCREENER_ONLY: 122
- US|WATCH: 116
- KR|VETO: 52
- US|VETO: 6

### Prompt Counts
- US|input=False: 36149
- US|input=True: 34120
- KR|input=True: 29720
- KR|input=False: 21225

## Candidate Lifecycle
- known_at_policy: promotion_demotion_uses_logged_state_only_forward_labels_are_evaluation_labels

### KR
| State | Count |
|---|---:|
| CORE | 0 |
| WATCH | 153 |
| PROBATION | 38 |
| BENCH | 634 |
| QUARANTINE | 14 |

## Lifecycle Transitions
- none

### US
| State | Count |
|---|---:|
| CORE | 0 |
| WATCH | 116 |
| PROBATION | 6 |
| BENCH | 1044 |
| QUARANTINE | 0 |

## Lifecycle Transitions
- none

## Action Routing

### Final Action Counts
- US|WATCH: 29546
- KR|WATCH: 17137
- US|PULLBACK_WAIT: 2063
- US|BUY_READY: 1202
- KR|PULLBACK_WAIT: 478
- KR|BUY_READY: 345
- US|PROBE_READY: 124
- KR|PROBE_READY: 38

### Route Reason Counts
- US|(none)|watch: 26891
- KR|(none)|watch: 15478
- US|PathB.wait|pullback_wait: 2063
- US|(none)|claude_avoid: 1387
- KR|(none)|claude_avoid: 1208
- US|PlanA.buy|buy_ready: 1202
- US|(none)|pullback_wait_blocked_negative_context: 1099
- KR|PathB.wait|pullback_wait: 478
- KR|PlanA.buy|buy_ready: 345
- KR|(none)|pullback_wait_blocked_negative_context: 280
- US|(none)|pullback_wait_soft_block:late_mover: 160
- KR|(none)|buy_ready_price_cap_exceeded: 127
- US|PlanA.probe|probe_ready: 124
- KR|(none)|pullback_wait_soft_block:late_mover: 42
- KR|PlanA.probe|probe_ready: 38
- US|(none)|probe_ready: 9
- KR|(none)|probe_ready: 2

## Cohort Reliability

### KR Worst
| KR|base_universe|unclassified|high|pullback|gap_pullback | -0.4545 | 11 | 2 | 0 | 5 |
| KR|base_universe|unclassified|mid|at_high|gap_pullback | -0.3889 | 18 | 0 | 0 | 7 |
| KR|base_universe|unclassified|high|deep|opening_range_pullback | -0.2963 | 27 | 0 | 0 | 8 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|momentum | -0.2895 | 38 | 0 | 5 | 16 |
| KR|base_universe|unclassified|high|pullback|gap_pullback | -0.2857 | 14 | 8 | 2 | 6 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|momentum | -0.2844 | 109 | 0 | 2 | 33 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|opening_range_pullback | -0.2563 | 398 | 0 | 13 | 115 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|unknown_strategy | -0.25 | 8 | 0 | 1 | 3 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|gap_pullback | -0.2456 | 171 | 0 | 2 | 44 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|gap_pullback | -0.2393 | 489 | 0 | 29 | 146 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|momentum | -0.2269 | 119 | 0 | 1 | 28 |
| KR|base_universe|unclassified|mid|at_high|opening_range_pullback | -0.2222 | 45 | 6 | 0 | 10 |

### KR Best
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|observe | 1.0 | 10 | 0 | 10 | 0 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|observe | 0.9615 | 26 | 0 | 25 | 0 |
| KR|base_universe|unclassified|high|at_high|observe | 0.9091 | 11 | 0 | 10 | 0 |
| KR|base_universe|unclassified|high|at_high|momentum | 0.6154 | 13 | 13 | 9 | 1 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|observe | 0.5556 | 36 | 0 | 20 | 0 |
| KR|base_universe|unclassified|mid|at_high|momentum | 0.5152 | 33 | 10 | 17 | 0 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|mean_reversion | 0.5041 | 121 | 0 | 61 | 0 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|observe | 0.5 | 6 | 0 | 3 | 0 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|observe | 0.4688 | 32 | 0 | 15 | 0 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|mean_reversion | 0.4486 | 185 | 0 | 83 | 0 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|observe | 0.4444 | 18 | 0 | 9 | 1 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|observe | 0.4167 | 24 | 0 | 10 | 0 |

### US Worst
| US|base_universe|most_actives|high|deep|gap_pullback | -0.5714 | 7 | 0 | 0 | 4 |
| US|base_universe|most_actives|mid|deep|opening_range_pullback | -0.5294 | 17 | 0 | 0 | 9 |
| US|base_universe|most_actives|high|near_high|gap_pullback | -0.4 | 5 | 2 | 0 | 2 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|gap_pullback | -0.2973 | 74 | 0 | 0 | 22 |
| US|base_universe|most_actives|high|pullback|opening_range_pullback | -0.2857 | 14 | 4 | 0 | 4 |
| US|base_universe|most_actives|mid|pullback|gap_pullback | -0.2857 | 7 | 0 | 0 | 2 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|mean_reversion | -0.2807 | 171 | 0 | 2 | 50 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|gap_pullback | -0.2338 | 201 | 11 | 11 | 58 |
| US|base_universe|most_actives|mid|deep|gap_pullback | -0.2143 | 14 | 0 | 0 | 3 |
| US|base_universe|day_losers|unknown_liq|unknown_from_high|mean_reversion | -0.2121 | 66 | 0 | 0 | 14 |
| US|base_universe|day_losers|unknown_liq|unknown_from_high|gap_pullback | -0.2059 | 136 | 0 | 4 | 32 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|gap_pullback | -0.1962 | 209 | 0 | 3 | 44 |

### US Best
| US|base_universe|most_actives|unknown_liq|unknown_from_high|momentum | 1.0 | 5 | 2 | 5 | 0 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|momentum | 1.0 | 18 | 0 | 18 | 0 |
| US|base_universe|most_actives|high|at_high|gap_pullback | 0.9 | 20 | 3 | 18 | 0 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|momentum | 0.8889 | 18 | 0 | 16 | 0 |
| US|base_universe|most_actives|high|at_high|opening_range_pullback | 0.8667 | 15 | 0 | 13 | 0 |
| US|base_universe|mega_gap|unknown_liq|unknown_from_high|mean_reversion | 0.75 | 8 | 0 | 6 | 0 |
| US|base_universe|most_actives|high|at_high|opening_range_pullback | 0.678 | 59 | 18 | 40 | 0 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|mean_reversion | 0.6333 | 30 | 0 | 19 | 0 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|mean_reversion | 0.5556 | 99 | 29 | 55 | 0 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|momentum | 0.5526 | 38 | 0 | 21 | 0 |
| US|base_universe|most_actives|high|at_high|gap_pullback | 0.5455 | 22 | 0 | 12 | 0 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|momentum | 0.4835 | 91 | 0 | 44 | 0 |

## Recommendations
- candidate_state: Promote a tier book to source of truth: CORE, WATCH, PROBATION, BENCH, QUARANTINE. Reason: Flat today_tickers replacement loses continuity and cannot express watch-only vs executable risk.
- preopen: Merge hard pins into session_open candidates, but force watch-only until post-open confirmation. Reason: Current hard pins are not reliable enough to auto-buy and can be dropped before Claude selection.
- preopen: Add low-liq ignition and late-reclaim watch buckets with 60m/120m confirmation, not open auction entry. Reason: The best missed KR winners were either low-liq ignition or late reclaim; naive soft expansion is negative.
- replacement: Use trainer/cohort delta gate for both KR and US replacement-in, with looser KR shadow rollout first. Reason: Replacement should require incoming quality to beat outgoing quality instead of rotating by freshness alone.
- execution: Route only final applied trade_ready, not raw Claude trade_ready, and block all new probes under stop-cluster disaster. Reason: Raw action can survive in logs after runtime normalization removes it; disaster blocks must own final execution.
- risk_exit: Keep cap2/MFE protection as the immediate overlay, then move to broker-backed persistent peak stops. Reason: Current local simulation shows the largest positive effect comes from left-tail clipping and MFE preservation.
- observability: Backfill forward labels into screener_quality rows and add known_at snapshots for every promotion/demotion. Reason: Current candidate quality logs explain funnel loss, but not enough forward PnL for rule optimization.
