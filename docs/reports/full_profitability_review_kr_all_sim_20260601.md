# Full Profitability Review

Generated: 2026-06-01T14:53:02

## Basis

- closed_trades: 162
- selection_rows: 6679
- preopen_rows: 2163
- valid_preopen_rows: 2123
- screener_quality_rows: 33300
- action_routing_events: 427
- cohort_files: 512

Notes:
- All inputs are local files or sqlite rows; no broker/API/Claude calls are made.
- Preopen entry simulations are approximate: entry-to-final uses sampled anchor returns, not tick-level fills.
- Forward return fields in ticker_selection_log are post-selection audit labels and must not be used inside live gating without known_at controls.

## Closed Trades By Market
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR | 56 | 16/40 | 28.6% | -1.387% | -1.459% | 0.40 | -13.624% | +23.803% |
| US | 106 | 51/55 | 48.1% | +0.470% | -0.069% | 1.54 | -6.663% | +12.761% |

## Closed Trades By Strategy
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| US|claude_price | 78 | 42/36 | 53.9% | +0.728% | +0.154% | 2.16 | -5.381% | +12.761% |
| KR|momentum | 32 | 10/22 | 31.2% | -0.581% | -1.350% | 0.68 | -12.234% | +23.803% |
| KR|claude_price | 12 | 2/10 | 16.7% | -2.522% | -2.341% | 0.09 | -9.818% | +2.868% |
| US|gap_pullback | 10 | 4/6 | 40.0% | +0.092% | -1.582% | 1.06 | -3.746% | +7.843% |
| US|momentum | 4 | 2/2 | 50.0% | +1.707% | +1.283% | 5.33 | -1.465% | +5.726% |
| KR|continuation | 3 | 0/3 | 0.0% | -6.920% | -8.786% | 0.00 | -9.548% | -2.425% |
| KR|kr_sector_play | 3 | 2/1 | 66.7% | +1.726% | +2.565% | 3.85 | -1.815% | +4.427% |
| KR|gap_pullback | 2 | 1/1 | 50.0% | -0.379% | -0.379% | 0.03 | -0.783% | +0.026% |
| US|mean_reversion | 2 | 2/0 | 100.0% | +0.284% | +0.284% | inf | +0.159% | +0.408% |
| KR|RECOVERY_MICRO | 1 | 0/1 | 0.0% | -0.323% | -0.323% | 0.00 | -0.323% | -0.323% |
| US|opening_range_pullback | 1 | 1/0 | 100.0% | +11.346% | +11.346% | inf | +11.346% | +11.346% |

## Broker Sync Operational Cases
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| US|broker_sync | 11 | 0/11 | 0.0% | -2.417% | -1.549% | 0.00 | -6.663% | -0.777% |
| KR|broker_sync | 3 | 1/2 | 33.3% | -4.045% | -0.872% | 0.16 | -13.624% | +2.361% |

## Selection Live Traded By Ready
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR|ready=0 | 10 | 3/7 | 30.0% | -2.172% | -0.794% | 0.14 | -13.624% | +1.780% |
| KR|ready=1 | 18 | 5/13 | 27.8% | -0.971% | -1.759% | 0.37 | -5.010% | +8.374% |
| US|ready=0 | 3 | 1/2 | 33.3% | +1.329% | -0.111% | 2.03 | -3.746% | +7.843% |
| US|ready=1 | 10 | 4/6 | 40.0% | +0.524% | -1.070% | 1.46 | -3.528% | +7.697% |

## Selection Live Traded By Strategy
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR|momentum | 24 | 7/17 | 29.2% | -1.552% | -1.759% | 0.27 | -13.624% | +8.374% |
| US|gap_pullback | 8 | 3/5 | 37.5% | +0.401% | -1.582% | 1.25 | -3.746% | +7.843% |
| US|momentum | 4 | 2/2 | 50.0% | +1.707% | +1.283% | 5.33 | -1.465% | +5.726% |
| KR|RECOVERY_MICRO | 2 | 0/2 | 0.0% | -0.597% | -0.597% | 0.00 | -0.872% | -0.323% |
| KR|gap_pullback | 2 | 1/1 | 50.0% | -0.379% | -0.379% | 0.03 | -0.783% | +0.026% |
| US|continuation | 1 | 0/1 | 0.0% | -0.800% | -0.800% | 0.00 | -0.800% | -0.800% |

## Selection Forward Max Runup By Ready
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR|ready=0 | 1468 | 1357/99 | 92.4% | +14.448% | +10.075% | 84.18 | -20.675% | +119.363% |
| KR|ready=1 | 114 | 103/10 | 90.3% | +16.912% | +9.522% | 82.96 | -8.707% | +119.363% |
| US|ready=0 | 2081 | 1890/181 | 90.8% | +7.565% | +5.645% | 30.13 | -27.494% | +88.487% |
| US|ready=1 | 265 | 257/7 | 97.0% | +8.940% | +7.466% | 418.26 | -2.491% | +38.776% |

## Preopen By Market
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR | 1073 | 378/686 | 35.2% | -1.053% | -2.394% | 0.72 | -30.000% | +30.000% |
| US | 1050 | 496/538 | 47.2% | +0.081% | -0.078% | 1.05 | -23.236% | +36.778% |

## Preopen Segments

### KR
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 1073 | 378/686 | 35.2% | -1.053% | -2.394% | 0.72 | -30.000% | +30.000% |
| actual_selected | 222 | 91/129 | 41.0% | -0.007% | -2.711% | 1.00 | -17.193% | +30.000% |
| actual_trade_ready | 4 | 2/2 | 50.0% | +4.831% | +1.138% | 12.37 | -1.453% | +18.500% |
| hard_pin_current | 38 | 14/24 | 36.8% | -3.351% | -5.180% | 0.39 | -21.316% | +17.223% |
| soft_b | 347 | 130/211 | 37.5% | -1.224% | -2.780% | 0.72 | -30.000% | +30.000% |
| low_liq_tag | 647 | 219/426 | 33.9% | -1.054% | -2.376% | 0.71 | -24.863% | +30.000% |
| rank_1_10 | 178 | 61/114 | 34.3% | -1.670% | -3.083% | 0.62 | -25.801% | +30.000% |
| rank_11_30 | 360 | 143/213 | 39.7% | -0.489% | -2.246% | 0.87 | -30.000% | +30.000% |
| rank_31_plus | 535 | 174/359 | 32.5% | -1.228% | -2.390% | 0.66 | -24.863% | +30.000% |

### US
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 1050 | 496/538 | 47.2% | +0.081% | -0.078% | 1.05 | -23.236% | +36.778% |
| actual_selected | 198 | 96/99 | 48.5% | +0.508% | -0.050% | 1.27 | -19.297% | +36.778% |
| actual_trade_ready | 5 | 5/0 | 100.0% | +8.780% | +6.868% | inf | +1.012% | +18.021% |
| hard_pin_current | 57 | 26/31 | 45.6% | +0.589% | -0.635% | 1.27 | -11.337% | +22.732% |
| soft_b | 427 | 214/210 | 50.1% | +0.473% | +0.006% | 1.27 | -19.297% | +23.348% |
| low_liq_tag | 51 | 20/30 | 39.2% | -0.494% | -0.662% | 0.66 | -8.627% | +7.292% |
| rank_1_10 | 190 | 88/101 | 46.3% | +0.075% | -0.615% | 1.03 | -19.297% | +23.348% |
| rank_11_30 | 380 | 190/186 | 50.0% | +0.201% | +0.018% | 1.12 | -14.445% | +36.778% |
| rank_31_plus | 480 | 218/251 | 45.4% | -0.011% | -0.120% | 0.99 | -23.236% | +28.837% |

## Preopen Rule Simulations

### KR
| Rule | Basis | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| current_hard_pin | final | 38 | 14/24 | 36.8% | -3.351% | -5.180% | 0.39 | -21.316% | +17.223% |
| soft_b_naive | final | 347 | 130/211 | 37.5% | -1.224% | -2.780% | 0.72 | -30.000% | +30.000% |
| soft_b_confirm30 | final | 129 | 98/30 | 76.0% | +6.341% | +2.983% | 6.06 | -16.319% | +30.000% |
| soft_b_confirm30 | entry_30m_to_final | 129 | 52/70 | 40.3% | -1.750% | -0.339% | 0.49 | -25.191% | +26.457% |
| soft_b_confirm60 | final | 110 | 89/20 | 80.9% | +7.406% | +3.933% | 7.73 | -16.319% | +30.000% |
| soft_b_confirm60 | entry_60m_to_final | 110 | 38/64 | 34.5% | -1.746% | -0.770% | 0.43 | -24.615% | +19.155% |
| low_liq_ignite60 | final | 60 | 56/4 | 93.3% | +11.683% | +8.292% | 31.10 | -14.920% | +30.000% |
| low_liq_ignite60 | entry_60m_to_final | 60 | 26/27 | 43.3% | -1.014% | +0.000% | 0.61 | -31.152% | +19.344% |
| late_reclaim_watch | final | 16 | 13/2 | 81.2% | +10.451% | +7.795% | 21.25 | -5.390% | +30.000% |
| late_reclaim_watch | entry_120m_to_final | 16 | 4/10 | 25.0% | -1.840% | -2.458% | 0.51 | -11.099% | +13.155% |

### US
| Rule | Basis | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| current_hard_pin | final | 57 | 26/31 | 45.6% | +0.589% | -0.635% | 1.27 | -11.337% | +22.732% |
| soft_b_naive | final | 427 | 214/210 | 50.1% | +0.473% | +0.006% | 1.27 | -19.297% | +23.348% |
| soft_b_confirm30 | final | 176 | 144/30 | 81.8% | +4.028% | +3.060% | 13.47 | -6.320% | +22.732% |
| soft_b_confirm30 | entry_30m_to_final | 176 | 98/76 | 55.7% | +0.283% | +0.399% | 1.25 | -11.472% | +14.806% |
| soft_b_confirm60 | final | 135 | 120/15 | 88.9% | +4.924% | +3.668% | 29.79 | -4.618% | +22.732% |
| soft_b_confirm60 | entry_60m_to_final | 135 | 73/61 | 54.1% | +0.158% | +0.263% | 1.14 | -10.497% | +11.508% |
| low_liq_ignite60 | final | 0 | 0/0 | 0.0% | +0.000% | +0.000% | NA | +0.000% | +0.000% |
| low_liq_ignite60 | entry_60m_to_final | 0 | 0/0 | 0.0% | +0.000% | +0.000% | NA | +0.000% | +0.000% |
| late_reclaim_watch | final | 29 | 27/2 | 93.1% | +5.146% | +3.543% | 30.88 | -3.306% | +23.348% |
| late_reclaim_watch | entry_120m_to_final | 29 | 12/16 | 41.4% | +0.031% | -0.221% | 1.03 | -6.147% | +7.999% |

## Missed Strong Preopen Candidates
| session_date | market | ticker | rank | score | risk_tags | selected | trade_ready | final | mfe | mae | ret5 | ret30 | ret60 | hard_pin | soft_b | low_liq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-05-14 | US | POET | 26 | 0.43 | [] | True | False | 36.778 | 43.7022 | 20.9882 | 39.6722 | 30.1322 | 26.2352 | False | False | False |
| 2026-05-11 | KR | 012205 | 9 | 0.18 | ['low_liquidity'] | True | False | 30.0 | 30.0 | -6.6346 | 3.1731 | 30.0 | 30.0 | False | False | True |
| 2026-05-11 | KR | 007610 | 36 | 0.18 | ['low_liquidity'] | False | False | 30.0 | 30.0 | -13.8384 | -8.6869 | -10.101 | -11.0101 | False | False | True |
| 2026-05-12 | KR | 439960 | 18 | 0.6 | ['limit_up_chase_risk'] | False | False | 30.0 | 30.0 | 5.0 | 16.0417 | 30.0 | 30.0 | False | True | False |
| 2026-05-13 | KR | 007460 | 20 | 0.6 | ['limit_up_chase_risk'] | False | False | 30.0 | 30.0 | 0.1923 | 20.3846 | 18.0769 | 21.5385 | False | True | False |
| 2026-05-21 | KR | 024840 | 23 | 0.51 | [] | False | False | 30.0 | 30.0 | 12.2642 | 23.5849 | 30.0 | 30.0 | False | True | False |
| 2026-05-26 | KR | 203650 | 28 | 0.18 | ['low_liquidity'] | False | False | 30.0 | 30.0 | 4.9296 | 17.4648 | 30.0 | 30.0 | False | False | True |
| 2026-05-26 | KR | 001740 | 50 | 0.18 | ['low_liquidity'] | False | False | 30.0 | 30.0 | 4.1667 | 12.0238 | 13.0952 | 8.9286 | False | False | True |
| 2026-05-20 | KR | 066980 | 34 | 0.3 | ['low_liquidity'] | False | False | 29.9862 | 29.9862 | 7.7717 | 25.1719 | 29.6424 | 29.9862 | False | False | True |
| 2026-05-13 | KR | 014910 | 19 | 0.6 | ['limit_up_chase_risk'] | False | False | 29.9803 | 29.9803 | 10.4536 | 19.9211 | 29.9803 | 29.9803 | False | True | False |
| 2026-05-06 | KR | 076610 | 44 | 0.18 | ['low_liquidity'] | False | False | 29.9735 | 29.9735 | -1.2732 | 5.5703 | 29.9735 | 26.7905 | False | False | True |
| 2026-05-19 | KR | 011000 | 4 | 0.75 | [] | False | False | 29.9715 | 29.9715 | 2.7593 | 13.0352 | 28.4491 | 20.8373 | False | True | False |
| 2026-05-13 | KR | 439960 | 17 | 0.6 | ['limit_up_chase_risk'] | False | False | 29.9679 | 29.9679 | 12.1795 | 23.8782 | 29.9679 | 29.9679 | False | True | False |
| 2026-05-11 | KR | 004710 | 31 | 0.18 | ['low_liquidity'] | True | False | 29.9632 | 29.9632 | 3.0331 | 6.8934 | 16.636 | 29.9632 | False | False | True |
| 2026-05-14 | KR | 439960 | 17 | 0.6 | ['limit_up_chase_risk'] | False | False | 29.963 | 29.963 | 12.2072 | 28.2367 | 29.963 | 29.963 | False | True | False |
| 2026-05-21 | KR | 125020 | 47 | 0.18 | ['low_liquidity'] | True | False | 29.9595 | 29.9595 | 2.969 | 6.8826 | 21.5924 | 29.4197 | False | False | True |
| 2026-05-14 | KR | 048770 | 13 | 0.6 | ['limit_up_chase_risk'] | False | False | 29.9584 | 29.9584 | 3.0513 | 3.0513 | 5.8252 | 13.7309 | False | True | False |
| 2026-05-18 | KR | 036930 | 3 | 0.18 | ['low_liquidity'] | True | False | 29.9572 | 29.9572 | 7.1327 | 13.1241 | 23.6091 | 27.8887 | False | False | True |
| 2026-05-12 | KR | 012860 | 11 | 0.75 | [] | True | False | 29.9569 | 29.9569 | 3.6638 | 10.5603 | 29.9569 | 29.9569 | False | True | False |
| 2026-05-19 | KR | 021880 | 26 | 0.47 | ['limit_up_chase_risk'] | False | False | 29.9539 | 29.9539 | -7.8341 | 15.2074 | 11.0599 | 14.2857 | False | False | False |
| 2026-05-22 | KR | 032580 | 17 | 0.6 | ['limit_up_chase_risk'] | True | False | 29.9539 | 29.9539 | -1.3825 | 9.063 | 2.765 | 9.063 | False | True | False |
| 2026-05-19 | KR | 439960 | 37 | 0.18 | ['low_liquidity'] | False | False | 29.9465 | 29.9465 | -7.754 | 0.2674 | -4.1444 | 3.877 | False | False | True |
| 2026-05-18 | KR | 021880 | 51 | 0.18 | ['low_liquidity'] | False | False | 29.9401 | 29.9401 | 1.1976 | 13.1737 | 6.5868 | 9.5808 | False | False | True |
| 2026-05-20 | KR | 014910 | 27 | 0.5 | [] | False | False | 29.9376 | 29.9376 | -0.8316 | 1.2474 | 18.2952 | 14.7609 | False | True | False |
| 2026-05-26 | KR | 019180 | 47 | 0.18 | ['low_liquidity'] | False | False | 29.9329 | 29.9329 | 14.7651 | 22.5503 | 26.5772 | 24.0268 | False | False | True |

## Expanded Rule Risks
| session_date | market | ticker | rank | score | risk_tags | selected | trade_ready | final | mfe | mae | ret5 | ret30 | ret60 | hard_pin | soft_b | low_liq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-05-15 | KR | 487580 | 13 | 0.6 | ['limit_up_chase_risk'] | False | False | -30.0 | 30.0 | -30.0 | 26.5 | -9.65 | -17.4 | False | True | False |
| 2026-05-29 | KR | 027040 | 9 | 0.6 | ['limit_up_chase_risk'] | False | False | -25.8013 | 12.0192 | -28.8462 | -4.9679 | -12.3397 | -15.5449 | False | True | False |
| 2026-05-13 | KR | 006345 | 1 | 0.75 | [] | False | False | -21.3163 | 9.1295 | -21.8259 | -10.828 | -9.9788 | -9.7665 | True | True | False |
| 2026-05-14 | US | LWLG | 9 | 0.55 | [] | True | False | -19.2973 | -6.5331 | -21.6025 | -14.0269 | -19.2973 | -18.1993 | False | True | False |
| 2026-05-19 | KR | 412350 | 12 | 0.6 | ['limit_up_chase_risk'] | False | False | -19.1051 | 0.4972 | -22.8693 | -8.5227 | -13.7784 | -14.9148 | False | True | False |
| 2026-05-27 | KR | 021880 | 12 | 0.6 | ['limit_up_chase_risk'] | False | False | -18.4669 | 11.4983 | -19.1638 | 6.9686 | -7.3171 | -9.4077 | False | True | False |
| 2026-05-28 | KR | 229000 | 14 | 0.6 | ['limit_up_chase_risk'] | False | False | -17.682 | 15.0074 | -19.3165 | 14.4131 | -7.578 | -12.0357 | False | True | False |
| 2026-05-07 | KR | 007610 | 1 | 0.75 | [] | False | False | -17.6609 | 4.6311 | -21.4286 | -13.1083 | -19.7802 | -19.3093 | True | True | False |
| 2026-05-20 | KR | 290690 | 6 | 0.75 | [] | True | False | -17.1927 | 7.6412 | -19.8505 | 4.3189 | -7.1429 | -5.3156 | False | True | False |
| 2026-05-15 | KR | 007460 | 22 | 0.51 | [] | False | False | -17.0775 | 2.4648 | -20.0704 | -6.162 | -6.162 | -8.0986 | False | True | False |
| 2026-05-13 | KR | 024840 | 21 | 0.51 | [] | False | False | -16.9565 | -3.587 | -17.3913 | -12.1739 | -12.0652 | -8.0435 | False | True | False |
| 2026-05-14 | KR | 114450 | 15 | 0.6 | ['limit_up_chase_risk'] | False | False | -16.4609 | 5.0754 | -17.6955 | -6.1728 | -6.9959 | -6.1728 | False | True | False |
| 2026-05-15 | KR | 439960 | 18 | 0.6 | ['limit_up_chase_risk'] | False | False | -16.3188 | 29.981 | -25.0474 | 25.9962 | 11.8596 | 11.0057 | False | True | False |
| 2026-05-14 | KR | 007460 | 18 | 0.6 | ['limit_up_chase_risk'] | False | False | -15.9763 | 15.3846 | -18.6391 | 7.5444 | 5.7692 | 3.6982 | False | True | False |
| 2026-05-15 | KR | 095910 | 15 | 0.6 | ['limit_up_chase_risk'] | False | False | -15.8954 | -0.2012 | -17.505 | -4.2254 | -6.0362 | -10.0604 | False | True | False |
| 2026-05-20 | KR | 274090 | 2 | 0.75 | [] | False | False | -15.29 | -4.3937 | -17.5747 | -11.5993 | -14.0598 | -15.1142 | True | True | False |
| 2026-05-07 | KR | 037030 | 19 | 0.6 | ['limit_up_chase_risk'] | False | False | -15.0982 | 7.5491 | -17.6836 | -4.4467 | -13.9607 | -13.8573 | False | True | False |
| 2026-05-15 | KR | 009830 | 7 | 0.75 | [] | False | False | -15.0628 | -1.8828 | -16.318 | -6.1715 | -8.2636 | -13.1799 | False | True | False |
| 2026-05-14 | KR | 092200 | 6 | 0.75 | [] | False | False | -14.9462 | 3.4409 | -15.1613 | -1.9355 | -7.957 | -9.0323 | False | True | False |
| 2026-05-29 | KR | 035890 | 12 | 0.6 | ['limit_up_chase_risk'] | False | False | -14.7664 | 5.9813 | -17.0093 | -1.8692 | -11.7757 | -13.6449 | False | True | False |
| 2026-05-18 | US | AXTI | 11 | 0.43 | [] | False | False | -14.445 | 1.3896 | -15.0347 | -2.2594 | -9.0887 | -10.7045 | False | True | False |
| 2026-05-22 | KR | 007610 | 18 | 0.6 | ['limit_up_chase_risk'] | False | False | -14.3478 | 2.3478 | -14.7826 | -4.087 | -3.7391 | -5.2174 | False | True | False |
| 2026-05-15 | KR | 081150 | 1 | 0.75 | [] | False | False | -14.0919 | 3.6534 | -14.7182 | 0.8351 | -1.9833 | -1.9833 | True | True | False |
| 2026-05-20 | KR | 356680 | 17 | 0.6 | ['limit_up_chase_risk'] | False | False | -13.7895 | -8.8421 | -17.6316 | -11.4737 | -13.7895 | -14.2105 | False | True | False |
| 2026-05-12 | US | ASTS | 7 | 0.55 | [] | False | False | -13.7795 | -3.2503 | -15.2998 | -5.2423 | -11.6172 | -12.0291 | False | True | False |

## Missed Selection Runup Top
| date | market | ticker | trade_ready | signal_fired | blocked_reason | strategy | forward_1d | forward_3d | max_runup_3d | max_drawdown_3d |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-04-29 | KR | 024840 | 1 | 0 | None | gap_pullback | 29.9363 | 119.3631 | 119.3631 | 2.9299 |
| 2026-04-29 | KR | 024840 | 0 | 0 | None | None | 29.9363 | 119.3631 | 119.3631 | 2.9299 |
| 2026-04-30 | KR | 024840 | 1 | 0 | None | gap_pullback | 30.0 | 82.3529 | 110.5882 | 12.7451 |
| 2026-04-30 | KR | 024840 | 1 | 0 | None | gap_pullback | 30.0 | 82.3529 | 110.5882 | 12.7451 |
| 2026-05-11 | KR | 012205 | 0 | 0 | None | None | 29.9556 | 35.355 | 98.9645 | 21.1538 |
| 2026-04-21 | US | MXL | 0 | 0 | None | None | 0.5638 | 78.9911 | 88.4866 | -3.5015 |
| 2026-04-21 | US | MXL | 0 | 0 | None | None | 0.5638 | 78.9911 | 88.4866 | -3.5015 |
| 2026-04-21 | US | MXL | 0 | 0 | None | None | 0.5638 | 78.9911 | 88.4866 | -3.5015 |
| 2026-04-28 | KR | 006340 | 1 | 0 | None | momentum | 29.9901 | 72.2939 | 84.3098 | -2.3833 |
| 2026-04-28 | KR | 006340 | 0 | 0 | None | None | 29.9901 | 72.2939 | 84.3098 | -2.3833 |
| 2026-05-19 | KR | 032580 | 0 | 0 | None | mean_reversion | 6.5957 | 80.0 | 80.0 | -6.1702 |
| 2026-05-19 | KR | 032580 | 0 | 0 | None | mean_reversion | 6.5957 | 80.0 | 80.0 | -6.1702 |
| 2026-05-19 | KR | 032580 | 0 | 0 | None | momentum | 6.5957 | 80.0 | 80.0 | -6.1702 |
| 2026-05-19 | KR | 032580 | 0 | 0 | None | mean_reversion | 6.5957 | 80.0 | 80.0 | -6.1702 |
| 2026-05-21 | KR | 001740 | 0 | 0 | None | opening_range_pullback | 2.8152 | 39.2901 | 70.869 | -1.1016 |
| 2026-05-21 | KR | 001740 | 0 | 0 | None | opening_range_pullback | 2.8152 | 39.2901 | 70.869 | -1.1016 |
| 2026-05-21 | KR | 001740 | 0 | 0 | None | opening_range_pullback | 2.8152 | 39.2901 | 70.869 | -1.1016 |
| 2026-04-30 | KR | 006345 | 0 | 0 | None | None | 29.9799 | 68.9135 | 68.9135 | 10.664 |
| 2026-04-30 | KR | 006345 | 0 | 0 | None | None | 29.9799 | 68.9135 | 68.9135 | 10.664 |
| 2026-04-30 | KR | 007610 | 0 | 0 | None | None | 29.9408 | 24.497 | 68.8757 | 18.4615 |

## Daily Entry Caps
| Rule | Kept | N | W/L | Win | Avg | PF |
|---|---:|---:|---:|---:|---:|---:|
| total_cap_1 | 15/41 | 15 | 7/8 | 46.7% | -0.262% | 0.80 |
| total_cap_2 | 27/41 | 27 | 9/18 | 33.3% | -0.857% | 0.51 |
| total_cap_3 | 33/41 | 33 | 11/22 | 33.3% | -0.548% | 0.64 |
| per_market_cap_1 | 22/41 | 22 | 11/11 | 50.0% | +0.521% | 1.43 |
| per_market_cap_2 | 34/41 | 34 | 12/22 | 35.3% | -0.491% | 0.69 |
| per_market_cap_3 | 39/41 | 39 | 13/26 | 33.3% | -0.496% | 0.66 |

## Blocked Signals
| Reason | N | Ready | Fwd1D Avg | Runup3D Avg |
|---|---:|---:|---:|---:|
| ANALYST_MAX_GROSS_EXPOSURE_REACHED | 2 | 2 | +1.329% | +5.032% |
| ANALYST_NEW_BUY_BLOCK | 1 | 0 | -11.954% | -3.932% |
| DAILY_LOSS_LIMIT | 3 | 2 | +0.222% | +6.528% |
| INVALID_PRICE | 5 | 5 | +2.255% | +11.008% |
| MAX_DAILY_ENTRIES | 1 | 0 | +2.267% | +2.705% |
| ORDER_UNKNOWN_UNRESOLVED | 1 | 1 | +8.759% | +12.069% |
| PATHB_ORDER_UNKNOWN_SAME_TICKER | 1 | 1 | +7.601% | +13.948% |
| SAME_DAY_REENTRY_AFTER_STOP | 1 | 1 | -3.068% | +11.351% |
| insufficient_cash | 2 | 1 | +4.716% | +16.431% |
| order_rejected | 6 | 4 | +2.941% | +8.005% |
| order_size_too_small | 3 | 2 | +9.678% | +26.011% |
| qty_zero | 6 | 3 | +4.778% | +10.105% |

## Screener Funnel

### All Status Counts
- US|SCREENER_ONLY: 9487
- KR|SCREENER_ONLY: 7482
- US|WATCH: 3814
- KR|WATCH: 3512
- KR|NOT_IN_PROMPT: 3152
- US|NOT_IN_PROMPT: 2434
- KR|VETO: 1450
- US|VETO: 1328
- US|TRADE_READY: 455
- KR|TRADE_READY: 186

### Latest Status Counts
- US|SCREENER_ONLY: 476
- KR|SCREENER_ONLY: 210
- US|NOT_IN_PROMPT: 198
- KR|NOT_IN_PROMPT: 162
- US|WATCH: 114
- KR|WATCH: 112
- KR|VETO: 78
- US|VETO: 43
- US|TRADE_READY: 10
- KR|TRADE_READY: 1

### Prompt Counts
- US|input=True: 15084
- KR|input=True: 12611
- KR|input=False: 3171
- US|input=False: 2434

## Candidate Lifecycle
- known_at_policy: promotion_demotion_uses_logged_state_only_forward_labels_are_evaluation_labels

### KR
| State | Count |
|---|---:|
| CORE | 1 |
| WATCH | 112 |
| PROBATION | 74 |
| BENCH | 372 |
| QUARANTINE | 4 |

## Lifecycle Transitions
- none

### US
| State | Count |
|---|---:|
| CORE | 10 |
| WATCH | 114 |
| PROBATION | 43 |
| BENCH | 674 |
| QUARANTINE | 0 |

## Lifecycle Transitions
- none

## Action Routing

### Final Action Counts
- KR|WATCH: 2895
- US|WATCH: 2352
- US|BUY_READY: 432
- US|PULLBACK_WAIT: 310
- KR|BUY_READY: 143
- US|PROBE_READY: 109
- KR|PULLBACK_WAIT: 93
- KR|PROBE_READY: 72

### Route Reason Counts
- KR|(none)|watch: 2717
- US|(none)|watch: 2126
- US|PlanA.buy|buy_ready: 432
- US|PathB.wait|pullback_wait: 310
- US|(none)|pullback_wait_blocked_negative_context: 146
- KR|PlanA.buy|buy_ready: 143
- US|PlanA.probe|probe_ready: 109
- KR|PathB.wait|pullback_wait: 93
- KR|(none)|pullback_wait_blocked_negative_context: 90
- KR|(none)|claude_avoid: 83
- KR|PlanA.probe|probe_ready: 72
- US|(none)|claude_avoid: 52
- US|(none)|add_shadow_only: 24
- US|(none)|missing_pullback_target: 4
- KR|(none)|add_shadow_only: 4
- KR|(none)|missing_pullback_target: 1

## Cohort Reliability

### KR Worst
| KR|base_universe|unclassified|high|pullback|gap_pullback | -0.4545 | 11 | 2 | 0 | 5 |
| KR|base_universe|unclassified|mid|at_high|gap_pullback | -0.3889 | 18 | 0 | 0 | 7 |
| KR|base_universe|unclassified|high|deep|opening_range_pullback | -0.2963 | 27 | 0 | 0 | 8 |
| KR|base_universe|unclassified|high|pullback|gap_pullback | -0.2857 | 14 | 8 | 2 | 6 |
| KR|base_universe|unclassified|mid|at_high|opening_range_pullback | -0.2222 | 45 | 6 | 0 | 10 |
| KR|base_universe|unclassified|high|pullback|momentum | -0.2222 | 9 | 6 | 1 | 3 |
| KR|base_universe|unclassified|mid|pullback|momentum | -0.1818 | 11 | 2 | 0 | 2 |
| KR|base_universe|unclassified|high|deep|gap_pullback | -0.1667 | 18 | 6 | 4 | 7 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|momentum | -0.1667 | 24 | 3 | 0 | 4 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|mean_reversion | -0.1628 | 129 | 2 | 4 | 25 |
| KR|base_universe|unclassified|high|at_high|opening_range_pullback | -0.1509 | 53 | 14 | 2 | 10 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|mean_reversion | -0.1408 | 284 | 4 | 17 | 57 |

### KR Best
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|observe | 1.0 | 10 | 0 | 10 | 0 |
| KR|base_universe|unclassified|high|at_high|observe | 0.9091 | 11 | 0 | 10 | 0 |
| KR|base_universe|unclassified|high|at_high|momentum | 0.6154 | 13 | 13 | 9 | 1 |
| KR|base_universe|unclassified|mid|at_high|momentum | 0.5152 | 33 | 10 | 17 | 0 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|observe | 0.4167 | 24 | 0 | 10 | 0 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|observe | 0.3061 | 49 | 0 | 27 | 12 |
| KR|base_universe|unclassified|mid|at_high|observe | 0.2381 | 21 | 7 | 5 | 0 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|observe | 0.2 | 5 | 0 | 1 | 0 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|mean_reversion | 0.1935 | 62 | 0 | 12 | 0 |
| KR|base_universe|unclassified|high|at_high|gap_pullback | 0.1765 | 51 | 11 | 11 | 2 |
| KR|base_universe|unclassified|mid|at_high|momentum | 0.1667 | 6 | 1 | 1 | 0 |
| KR|base_universe|unclassified|mid|at_high|gap_pullback | 0.1538 | 13 | 8 | 3 | 1 |

### US Worst
| US|base_universe|most_actives|high|deep|gap_pullback | -0.5714 | 7 | 0 | 0 | 4 |
| US|base_universe|most_actives|mid|deep|opening_range_pullback | -0.5294 | 17 | 0 | 0 | 9 |
| US|base_universe|most_actives|high|near_high|gap_pullback | -0.4 | 5 | 2 | 0 | 2 |
| US|base_universe|most_actives|high|pullback|opening_range_pullback | -0.2857 | 14 | 4 | 0 | 4 |
| US|base_universe|most_actives|mid|pullback|gap_pullback | -0.2857 | 7 | 0 | 0 | 2 |
| US|base_universe|most_actives|mid|deep|gap_pullback | -0.2143 | 14 | 0 | 0 | 3 |
| US|base_universe|day_losers|unknown_liq|unknown_from_high|opening_range_pullback | -0.1786 | 28 | 0 | 0 | 5 |
| US|base_universe|most_actives|high|pullback|gap_pullback | -0.1667 | 12 | 2 | 0 | 2 |
| US|base_universe|day_gainers|unknown_liq|unknown_from_high|gap_pullback | -0.1538 | 39 | 0 | 1 | 7 |
| US|base_universe|most_actives|high|deep|gap_pullback | -0.1429 | 14 | 0 | 0 | 2 |
| US|base_universe|most_actives|mid|at_high|mean_reversion | -0.1429 | 7 | 0 | 0 | 1 |
| US|base_universe|day_losers|high|deep|opening_range_pullback | -0.125 | 8 | 0 | 0 | 1 |

### US Best
| US|base_universe|most_actives|unknown_liq|unknown_from_high|momentum | 1.0 | 5 | 2 | 5 | 0 |
| US|base_universe|most_actives|high|at_high|gap_pullback | 0.9 | 20 | 3 | 18 | 0 |
| US|base_universe|most_actives|high|at_high|opening_range_pullback | 0.8667 | 15 | 0 | 13 | 0 |
| US|base_universe|most_actives|high|at_high|opening_range_pullback | 0.678 | 59 | 18 | 40 | 0 |
| US|base_universe|most_actives|high|at_high|gap_pullback | 0.5455 | 22 | 0 | 12 | 0 |
| US|base_universe|day_gainers|high|at_high|gap_pullback | 0.375 | 24 | 3 | 9 | 0 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|gap_pullback | 0.3725 | 51 | 0 | 27 | 8 |
| US|base_universe|day_gainers|high|near_high|gap_pullback | 0.3333 | 6 | 2 | 2 | 0 |
| US|base_universe|day_gainers|unknown_liq|unknown_from_high|gap_pullback | 0.3008 | 256 | 63 | 78 | 1 |
| US|base_universe|day_gainers|high|pullback|gap_pullback | 0.3 | 10 | 3 | 3 | 0 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|gap_pullback | 0.2941 | 85 | 13 | 25 | 0 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|gap_pullback | 0.2769 | 195 | 21 | 56 | 2 |

## Recommendations
- candidate_state: Promote a tier book to source of truth: CORE, WATCH, PROBATION, BENCH, QUARANTINE. Reason: Flat today_tickers replacement loses continuity and cannot express watch-only vs executable risk.
- preopen: Merge hard pins into session_open candidates, but force watch-only until post-open confirmation. Reason: Current hard pins are not reliable enough to auto-buy and can be dropped before Claude selection.
- preopen: Add low-liq ignition and late-reclaim watch buckets with 60m/120m confirmation, not open auction entry. Reason: The best missed KR winners were either low-liq ignition or late reclaim; naive soft expansion is negative.
- replacement: Use trainer/cohort delta gate for both KR and US replacement-in, with looser KR shadow rollout first. Reason: Replacement should require incoming quality to beat outgoing quality instead of rotating by freshness alone.
- execution: Route only final applied trade_ready, not raw Claude trade_ready, and block all new probes under stop-cluster disaster. Reason: Raw action can survive in logs after runtime normalization removes it; disaster blocks must own final execution.
- risk_exit: Keep cap2/MFE protection as the immediate overlay, then move to broker-backed persistent peak stops. Reason: Current local simulation shows the largest positive effect comes from left-tail clipping and MFE preservation.
- observability: Backfill forward labels into screener_quality rows and add known_at snapshots for every promotion/demotion. Reason: Current candidate quality logs explain funnel loss, but not enough forward PnL for rule optimization.
