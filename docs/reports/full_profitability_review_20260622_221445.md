# Full Profitability Review

Generated: 2026-06-22T22:15:02

## Basis

- closed_trades: 278
- selection_rows: 15558
- preopen_rows: 3899
- valid_preopen_rows: 3791
- screener_quality_rows: 74708
- action_routing_events: 1657
- cohort_files: 766

Notes:
- All inputs are local files or sqlite rows; no broker/API/Claude calls are made.
- Preopen entry simulations are approximate: entry-to-final uses sampled anchor returns, not tick-level fills.
- Forward return fields in ticker_selection_log are post-selection audit labels and must not be used inside live gating without known_at controls.

## Closed Trades By Market
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR | 71 | 23/48 | 32.4% | -0.843% | -1.144% | 0.58 | -13.624% | +23.803% |
| US | 207 | 89/118 | 43.0% | +0.337% | -0.252% | 1.38 | -6.663% | +17.667% |

## Closed Trades By Strategy
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| US|claude_price | 173 | 77/96 | 44.5% | +0.410% | -0.153% | 1.53 | -5.381% | +17.667% |
| KR|momentum | 32 | 10/22 | 31.2% | -0.581% | -1.350% | 0.68 | -12.234% | +23.803% |
| KR|claude_price | 27 | 9/18 | 33.3% | -0.461% | -1.209% | 0.74 | -9.818% | +6.798% |
| US|gap_pullback | 11 | 5/6 | 45.5% | +0.638% | -1.340% | 1.46 | -3.746% | +7.843% |
| US|momentum | 6 | 3/3 | 50.0% | +1.501% | +1.283% | 3.35 | -2.248% | +5.726% |
| KR|continuation | 3 | 0/3 | 0.0% | -6.920% | -8.786% | 0.00 | -9.548% | -2.425% |
| KR|kr_sector_play | 3 | 2/1 | 66.7% | +1.726% | +2.565% | 3.85 | -1.815% | +4.427% |
| KR|gap_pullback | 2 | 1/1 | 50.0% | -0.379% | -0.379% | 0.03 | -0.783% | +0.026% |
| US|mean_reversion | 2 | 2/0 | 100.0% | +0.284% | +0.284% | inf | +0.159% | +0.408% |
| KR|RECOVERY_MICRO | 1 | 0/1 | 0.0% | -0.323% | -0.323% | 0.00 | -0.323% | -0.323% |
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
| KR|ready=0 | 5470 | 4670/764 | 85.4% | +12.053% | +7.955% | 28.55 | -29.954% | +119.363% |
| KR|ready=1 | 130 | 116/13 | 89.2% | +16.202% | +8.382% | 56.64 | -8.707% | +119.363% |
| US|ready=0 | 5898 | 5240/631 | 88.8% | +6.741% | +4.965% | 12.11 | -88.922% | +88.487% |
| US|ready=1 | 421 | 369/51 | 87.7% | +7.499% | +6.037% | 45.01 | -3.925% | +38.776% |

## Preopen By Market
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR | 1902 | 674/1153 | 35.4% | -0.925% | -1.980% | 0.74 | -40.000% | +30.000% |
| US | 1889 | 908/965 | 48.1% | -0.031% | -0.084% | 0.98 | -26.070% | +43.274% |

## Preopen Segments

### KR
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 1902 | 674/1153 | 35.4% | -0.925% | -1.980% | 0.74 | -40.000% | +30.000% |
| actual_selected | 416 | 171/242 | 41.1% | -0.033% | -2.444% | 0.99 | -24.715% | +30.000% |
| actual_trade_ready | 4 | 2/2 | 50.0% | +4.831% | +1.138% | 12.37 | -1.453% | +18.500% |
| hard_pin_current | 67 | 23/41 | 34.3% | -3.143% | -3.429% | 0.36 | -21.316% | +17.223% |
| soft_b | 567 | 198/324 | 34.9% | -1.027% | -2.004% | 0.74 | -30.000% | +30.000% |
| low_liq_tag | 1169 | 407/749 | 34.8% | -1.161% | -2.277% | 0.68 | -40.000% | +30.000% |
| rank_1_10 | 318 | 109/196 | 34.3% | -1.093% | -2.395% | 0.73 | -25.801% | +30.000% |
| rank_11_30 | 636 | 244/365 | 38.4% | -0.504% | -1.729% | 0.85 | -30.000% | +30.000% |
| rank_31_plus | 948 | 321/592 | 33.9% | -1.152% | -2.078% | 0.67 | -40.000% | +30.000% |

### US
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 1889 | 908/965 | 48.1% | -0.031% | -0.084% | 0.98 | -26.070% | +43.274% |
| actual_selected | 382 | 198/181 | 51.8% | +0.638% | +0.300% | 1.34 | -26.070% | +36.778% |
| actual_trade_ready | 5 | 5/0 | 100.0% | +8.780% | +6.868% | inf | +1.012% | +18.021% |
| hard_pin_current | 97 | 46/51 | 47.4% | +0.426% | -0.442% | 1.19 | -15.770% | +22.732% |
| soft_b | 734 | 364/367 | 49.6% | +0.091% | -0.001% | 1.04 | -19.297% | +23.348% |
| low_liq_tag | 89 | 41/47 | 46.1% | -0.263% | -0.491% | 0.83 | -9.310% | +8.159% |
| rank_1_10 | 329 | 153/175 | 46.5% | -0.193% | -0.491% | 0.92 | -19.297% | +23.348% |
| rank_11_30 | 660 | 333/323 | 50.5% | +0.086% | +0.083% | 1.05 | -16.556% | +36.778% |
| rank_31_plus | 900 | 422/467 | 46.9% | -0.057% | -0.123% | 0.97 | -26.070% | +43.274% |

## Preopen Rule Simulations

### KR
| Rule | Basis | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| current_hard_pin | final | 67 | 23/41 | 34.3% | -3.143% | -3.429% | 0.36 | -21.316% | +17.223% |
| soft_b_naive | final | 567 | 198/324 | 34.9% | -1.027% | -2.004% | 0.74 | -30.000% | +30.000% |
| soft_b_confirm30 | final | 205 | 148/47 | 72.2% | +5.930% | +2.952% | 6.14 | -16.319% | +30.000% |
| soft_b_confirm30 | entry_30m_to_final | 205 | 77/110 | 37.6% | -1.556% | -0.339% | 0.51 | -25.191% | +26.457% |
| soft_b_confirm60 | final | 168 | 136/30 | 81.0% | +7.407% | +4.406% | 8.42 | -16.319% | +30.000% |
| soft_b_confirm60 | entry_60m_to_final | 168 | 61/96 | 36.3% | -1.763% | -0.776% | 0.42 | -30.696% | +19.155% |
| low_liq_ignite60 | final | 99 | 89/10 | 89.9% | +10.199% | +7.914% | 18.19 | -24.715% | +30.000% |
| low_liq_ignite60 | entry_60m_to_final | 99 | 39/51 | 39.4% | -1.450% | -0.235% | 0.51 | -39.078% | +19.344% |
| late_reclaim_watch | final | 31 | 24/6 | 77.4% | +9.215% | +8.266% | 9.82 | -11.043% | +30.000% |
| late_reclaim_watch | entry_120m_to_final | 31 | 10/19 | 32.3% | -3.211% | -2.542% | 0.38 | -21.762% | +21.132% |

### US
| Rule | Basis | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| current_hard_pin | final | 97 | 46/51 | 47.4% | +0.426% | -0.442% | 1.19 | -15.770% | +22.732% |
| soft_b_naive | final | 734 | 364/367 | 49.6% | +0.091% | -0.001% | 1.04 | -19.297% | +23.348% |
| soft_b_confirm30 | final | 333 | 252/79 | 75.7% | +3.451% | +2.780% | 6.95 | -10.384% | +22.732% |
| soft_b_confirm30 | entry_30m_to_final | 333 | 175/156 | 52.5% | -0.130% | +0.266% | 0.92 | -12.289% | +14.806% |
| soft_b_confirm60 | final | 268 | 220/48 | 82.1% | +4.332% | +3.558% | 13.68 | -6.771% | +22.732% |
| soft_b_confirm60 | entry_60m_to_final | 268 | 135/132 | 50.4% | -0.114% | +0.056% | 0.92 | -10.912% | +11.508% |
| low_liq_ignite60 | final | 0 | 0/0 | 0.0% | +0.000% | +0.000% | NA | +0.000% | +0.000% |
| low_liq_ignite60 | entry_60m_to_final | 0 | 0/0 | 0.0% | +0.000% | +0.000% | NA | +0.000% | +0.000% |
| late_reclaim_watch | final | 44 | 39/5 | 88.6% | +4.782% | +3.729% | 17.32 | -3.920% | +23.348% |
| late_reclaim_watch | entry_120m_to_final | 44 | 20/23 | 45.5% | +0.028% | -0.150% | 1.02 | -6.417% | +7.999% |

## Missed Strong Preopen Candidates
| session_date | market | ticker | rank | score | risk_tags | selected | trade_ready | final | mfe | mae | ret5 | ret30 | ret60 | hard_pin | soft_b | low_liq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-06-01 | US | FLNC | 53 | 0.37 | [] | False | False | 43.2738 | 52.9661 | 23.1155 | 39.1128 | 25.474 | 29.706 | False | False | False |
| 2026-05-14 | US | POET | 26 | 0.43 | [] | True | False | 36.778 | 43.7022 | 20.9882 | 39.6722 | 30.1322 | 26.2352 | False | False | False |
| 2026-05-11 | KR | 012205 | 9 | 0.18 | ['low_liquidity'] | True | False | 30.0 | 30.0 | -6.6346 | 3.1731 | 30.0 | 30.0 | False | False | True |
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
| 2026-05-20 | KR | 066980 | 34 | 0.3 | ['low_liquidity'] | False | False | 29.9862 | 29.9862 | 7.7717 | 25.1719 | 29.6424 | 29.9862 | False | False | True |
| 2026-06-08 | KR | 066430 | 7 | 0.18 | ['low_liquidity'] | True | False | 29.9848 | 29.9848 | 2.8919 | 16.1339 | 19.7869 | 29.9848 | False | False | True |
| 2026-05-13 | KR | 014910 | 19 | 0.6 | ['limit_up_chase_risk'] | False | False | 29.9803 | 29.9803 | 10.4536 | 19.9211 | 29.9803 | 29.9803 | False | True | False |
| 2026-06-08 | KR | 271830 | 4 | 0.3 | ['low_liquidity'] | True | False | 29.976 | 29.976 | -2.3981 | 11.0312 | 14.3885 | 21.5827 | False | False | True |
| 2026-05-06 | KR | 076610 | 44 | 0.18 | ['low_liquidity'] | False | False | 29.9735 | 29.9735 | -1.2732 | 5.5703 | 29.9735 | 26.7905 | False | False | True |
| 2026-05-19 | KR | 011000 | 4 | 0.75 | [] | False | False | 29.9715 | 29.9715 | 2.7593 | 13.0352 | 28.4491 | 20.8373 | False | True | False |
| 2026-05-13 | KR | 439960 | 17 | 0.6 | ['limit_up_chase_risk'] | False | False | 29.9679 | 29.9679 | 12.1795 | 23.8782 | 29.9679 | 29.9679 | False | True | False |
| 2026-05-11 | KR | 004710 | 31 | 0.18 | ['low_liquidity'] | True | False | 29.9632 | 29.9632 | 3.0331 | 6.8934 | 16.636 | 29.9632 | False | False | True |
| 2026-05-14 | KR | 439960 | 17 | 0.6 | ['limit_up_chase_risk'] | False | False | 29.963 | 29.963 | 12.2072 | 28.2367 | 29.963 | 29.963 | False | True | False |
| 2026-05-21 | KR | 125020 | 47 | 0.18 | ['low_liquidity'] | True | False | 29.9595 | 29.9595 | 2.969 | 6.8826 | 21.5924 | 29.4197 | False | False | True |

## Expanded Rule Risks
| session_date | market | ticker | rank | score | risk_tags | selected | trade_ready | final | mfe | mae | ret5 | ret30 | ret60 | hard_pin | soft_b | low_liq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-05-15 | KR | 487580 | 13 | 0.6 | ['limit_up_chase_risk'] | False | False | -30.0 | 30.0 | -30.0 | 26.5 | -9.65 | -17.4 | False | True | False |
| 2026-05-29 | KR | 027040 | 9 | 0.6 | ['limit_up_chase_risk'] | False | False | -25.8013 | 12.0192 | -28.8462 | -4.9679 | -12.3397 | -15.5449 | False | True | False |
| 2026-06-09 | KR | 271830 | 6 | 0.6 | ['limit_up_chase_risk'] | False | False | -21.4022 | 19.1882 | -25.4613 | 14.0221 | -5.3506 | -8.3026 | False | True | False |
| 2026-05-13 | KR | 006345 | 1 | 0.75 | [] | False | False | -21.3163 | 9.1295 | -21.8259 | -10.828 | -9.9788 | -9.7665 | True | True | False |
| 2026-05-14 | US | LWLG | 9 | 0.55 | [] | True | False | -19.2973 | -6.5331 | -21.6025 | -14.0269 | -19.2973 | -18.1993 | False | True | False |
| 2026-05-19 | KR | 412350 | 12 | 0.6 | ['limit_up_chase_risk'] | False | False | -19.1051 | 0.4972 | -22.8693 | -8.5227 | -13.7784 | -14.9148 | False | True | False |
| 2026-06-18 | KR | 126640 | 10 | 0.63 | [] | True | False | -18.5552 | -2.1246 | -25.4958 | -7.932 | -5.5241 | -7.932 | False | True | False |
| 2026-05-27 | KR | 021880 | 12 | 0.6 | ['limit_up_chase_risk'] | False | False | -18.4669 | 11.4983 | -19.1638 | 6.9686 | -7.3171 | -9.4077 | False | True | False |
| 2026-06-12 | US | FLY | 6 | 0.67 | [] | False | False | -17.951 | -0.3051 | -20.2438 | -7.6708 | -10.5918 | -11.8364 | False | True | False |
| 2026-05-28 | KR | 229000 | 14 | 0.6 | ['limit_up_chase_risk'] | False | False | -17.682 | 15.0074 | -19.3165 | 14.4131 | -7.578 | -12.0357 | False | True | False |
| 2026-05-07 | KR | 007610 | 1 | 0.75 | [] | False | False | -17.6609 | 4.6311 | -21.4286 | -13.1083 | -19.7802 | -19.3093 | True | True | False |
| 2026-05-20 | KR | 290690 | 6 | 0.75 | [] | True | False | -17.1927 | 7.6412 | -19.8505 | 4.3189 | -7.1429 | -5.3156 | False | True | False |
| 2026-05-15 | KR | 007460 | 22 | 0.51 | [] | False | False | -17.0775 | 2.4648 | -20.0704 | -6.162 | -6.162 | -8.0986 | False | True | False |
| 2026-05-13 | KR | 024840 | 21 | 0.51 | [] | False | False | -16.9565 | -3.587 | -17.3913 | -12.1739 | -12.0652 | -8.0435 | False | True | False |
| 2026-06-09 | US | AAOI | 11 | 0.55 | [] | True | False | -16.5556 | 5.5736 | -18.1906 | 0.8467 | -5.5838 | -8.1132 | False | True | False |
| 2026-05-14 | KR | 114450 | 15 | 0.6 | ['limit_up_chase_risk'] | False | False | -16.4609 | 5.0754 | -17.6955 | -6.1728 | -6.9959 | -6.1728 | False | True | False |
| 2026-05-15 | KR | 439960 | 18 | 0.6 | ['limit_up_chase_risk'] | False | False | -16.3188 | 29.981 | -25.0474 | 25.9962 | 11.8596 | 11.0057 | False | True | False |
| 2026-05-14 | KR | 007460 | 18 | 0.6 | ['limit_up_chase_risk'] | False | False | -15.9763 | 15.3846 | -18.6391 | 7.5444 | 5.7692 | 3.6982 | False | True | False |
| 2026-05-15 | KR | 095910 | 15 | 0.6 | ['limit_up_chase_risk'] | False | False | -15.8954 | -0.2012 | -17.505 | -4.2254 | -6.0362 | -10.0604 | False | True | False |
| 2026-06-05 | US | FLNC | 8 | 0.67 | [] | False | False | -15.8932 | -3.0571 | -18.1584 | -8.3978 | -14.9908 | -12.4125 | False | True | False |
| 2026-06-05 | US | INOD | 2 | 0.67 | [] | False | False | -15.7704 | -1.893 | -17.5391 | -3.7346 | -7.8848 | -8.5638 | True | True | False |
| 2026-06-05 | US | WOLF | 11 | 0.67 | [] | True | False | -15.7098 | -5.3042 | -19.3111 | -10.3489 | -12.0489 | -9.3722 | False | True | False |
| 2026-06-02 | KR | 003550 | 1 | 0.87 | [] | False | False | -15.5006 | -6.9964 | -19.7226 | -15.5609 | -14.1134 | -17.1894 | True | True | False |
| 2026-06-16 | US | AXTI | 22 | 0.5 | [] | False | False | -15.3377 | 0.0406 | -15.8389 | -2.0137 | -2.1221 | -9.1476 | False | True | False |
| 2026-05-20 | KR | 274090 | 2 | 0.75 | [] | False | False | -15.29 | -4.3937 | -17.5747 | -11.5993 | -14.0598 | -15.1142 | True | True | False |

## Missed Selection Runup Top
| date | market | ticker | trade_ready | signal_fired | blocked_reason | strategy | forward_1d | forward_3d | max_runup_3d | max_drawdown_3d |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-04-29 | KR | 024840 | 1 | 0 | None | gap_pullback | 29.9363 | 119.3631 | 119.3631 | 2.9299 |
| 2026-04-29 | KR | 024840 | 0 | 0 | None | None | 29.9363 | 119.3631 | 119.3631 | 2.9299 |
| 2026-06-12 | KR | 079650 | 0 | 0 | None | None | 29.8817 | 119.2308 | 119.2308 | 29.8817 |
| 2026-04-30 | KR | 024840 | 1 | 0 | None | gap_pullback | 30.0 | 82.3529 | 110.5882 | 12.7451 |
| 2026-04-30 | KR | 024840 | 1 | 0 | None | gap_pullback | 30.0 | 82.3529 | 110.5882 | 12.7451 |
| 2026-05-11 | KR | 012205 | 0 | 0 | None | None | 29.9556 | 35.355 | 98.9645 | 21.1538 |
| 2026-05-28 | KR | 066570 | 0 | 0 | None | gap_pullback | 29.9335 | 74.0576 | 94.235 | 10.1996 |
| 2026-05-28 | KR | 066570 | 0 | 0 | None | gap_pullback | 29.9335 | 74.0576 | 94.235 | 10.1996 |
| 2026-05-28 | KR | 066570 | 0 | 0 | None | gap_pullback | 29.9335 | 74.0576 | 94.235 | 10.1996 |
| 2026-04-21 | US | MXL | 0 | 0 | None | None | 0.5638 | 78.9911 | 88.4866 | -3.5015 |
| 2026-04-21 | US | MXL | 0 | 0 | None | None | 0.5638 | 78.9911 | 88.4866 | -3.5015 |
| 2026-04-21 | US | MXL | 0 | 0 | None | None | 0.5638 | 78.9911 | 88.4866 | -3.5015 |
| 2026-06-11 | KR | 079650 | 0 | 0 | None | momentum | 10.3854 | 86.1528 | 86.1528 | -8.0993 |
| 2026-06-11 | KR | 079650 | 0 | 0 | None | momentum | 10.3854 | 86.1528 | 86.1528 | -8.0993 |
| 2026-06-11 | KR | 079650 | 0 | 0 | None | volatility_breakout | 10.3854 | 86.1528 | 86.1528 | -8.0993 |
| 2026-06-11 | KR | 079650 | 0 | 0 | None | momentum | 10.3854 | 86.1528 | 86.1528 | -8.0993 |
| 2026-06-11 | KR | 079650 | 0 | 0 | None | momentum | 10.3854 | 86.1528 | 86.1528 | -8.0993 |
| 2026-06-11 | KR | 079650 | 0 | 0 | None | momentum | 10.3854 | 86.1528 | 86.1528 | -8.0993 |
| 2026-04-28 | KR | 006340 | 1 | 0 | None | momentum | 29.9901 | 72.2939 | 84.3098 | -2.3833 |
| 2026-04-28 | KR | 006340 | 0 | 0 | None | None | 29.9901 | 72.2939 | 84.3098 | -2.3833 |

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
- US|SCREENER_ONLY: 16251
- US|NOT_IN_PROMPT: 14324
- KR|SCREENER_ONLY: 12049
- KR|NOT_IN_PROMPT: 10501
- US|WATCH: 8792
- KR|WATCH: 8297
- KR|VETO: 1943
- US|VETO: 1807
- US|TRADE_READY: 554
- KR|TRADE_READY: 190

### Latest Status Counts
- US|NOT_IN_PROMPT: 554
- US|SCREENER_ONLY: 371
- KR|NOT_IN_PROMPT: 331
- KR|SCREENER_ONLY: 228
- KR|WATCH: 140
- US|WATCH: 112
- KR|VETO: 76
- US|VETO: 34
- US|TRADE_READY: 4

### Prompt Counts
- US|input=True: 27387
- KR|input=True: 22451
- US|input=False: 14341
- KR|input=False: 10529

## Candidate Lifecycle
- known_at_policy: promotion_demotion_uses_logged_state_only_forward_labels_are_evaluation_labels

### KR
| State | Count |
|---|---:|
| CORE | 0 |
| WATCH | 140 |
| PROBATION | 68 |
| BENCH | 559 |
| QUARANTINE | 8 |

## Lifecycle Transitions
- none

### US
| State | Count |
|---|---:|
| CORE | 4 |
| WATCH | 112 |
| PROBATION | 34 |
| BENCH | 925 |
| QUARANTINE | 0 |

## Lifecycle Transitions
- none

## Action Routing

### Final Action Counts
- US|WATCH: 10708
- KR|WATCH: 8358
- US|PULLBACK_WAIT: 1286
- US|BUY_READY: 828
- KR|PULLBACK_WAIT: 304
- KR|BUY_READY: 254
- US|PROBE_READY: 121
- KR|PROBE_READY: 78

### Route Reason Counts
- US|(none)|watch: 9206
- KR|(none)|watch: 7401
- US|PathB.wait|pullback_wait: 1286
- US|PlanA.buy|buy_ready: 828
- US|(none)|pullback_wait_blocked_negative_context: 765
- KR|(none)|claude_avoid: 660
- US|(none)|claude_avoid: 632
- KR|PathB.wait|pullback_wait: 304
- KR|PlanA.buy|buy_ready: 254
- KR|(none)|pullback_wait_blocked_negative_context: 209
- US|PlanA.probe|probe_ready: 121
- KR|PlanA.probe|probe_ready: 78
- US|(none)|pullback_wait_soft_block:late_mover: 77
- KR|(none)|buy_ready_price_cap_exceeded: 62
- US|(none)|add_shadow_only: 24
- KR|(none)|pullback_wait_soft_block:late_mover: 21
- US|(none)|missing_pullback_target: 4
- KR|(none)|add_shadow_only: 4
- KR|(none)|missing_pullback_target: 1

## Cohort Reliability

### KR Worst
| KR|base_universe|unclassified|high|pullback|gap_pullback | -0.4545 | 11 | 2 | 0 | 5 |
| KR|base_universe|unclassified|mid|at_high|gap_pullback | -0.3889 | 18 | 0 | 0 | 7 |
| KR|base_universe|unclassified|high|deep|opening_range_pullback | -0.2963 | 27 | 0 | 0 | 8 |
| KR|base_universe|unclassified|high|pullback|gap_pullback | -0.2857 | 14 | 8 | 2 | 6 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|momentum | -0.2844 | 109 | 0 | 2 | 33 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|gap_pullback | -0.2393 | 489 | 0 | 29 | 146 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|momentum | -0.2269 | 119 | 0 | 1 | 28 |
| KR|base_universe|unclassified|mid|at_high|opening_range_pullback | -0.2222 | 45 | 6 | 0 | 10 |
| KR|base_universe|unclassified|high|pullback|momentum | -0.2222 | 9 | 6 | 1 | 3 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|momentum | -0.1935 | 31 | 4 | 0 | 6 |
| KR|base_universe|unclassified|mid|pullback|momentum | -0.1818 | 11 | 2 | 0 | 2 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|mean_reversion | -0.1788 | 151 | 0 | 2 | 29 |

### KR Best
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|observe | 1.0 | 10 | 0 | 10 | 0 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|observe | 0.9615 | 26 | 0 | 25 | 0 |
| KR|base_universe|unclassified|high|at_high|observe | 0.9091 | 11 | 0 | 10 | 0 |
| KR|base_universe|unclassified|high|at_high|momentum | 0.6154 | 13 | 13 | 9 | 1 |
| KR|base_universe|unclassified|mid|at_high|momentum | 0.5152 | 33 | 10 | 17 | 0 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|observe | 0.5 | 6 | 0 | 3 | 0 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|observe | 0.4688 | 32 | 0 | 15 | 0 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|observe | 0.4444 | 18 | 0 | 9 | 1 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|observe | 0.4167 | 24 | 0 | 10 | 0 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|momentum | 0.3091 | 55 | 0 | 17 | 0 |
| KR|base_universe|unclassified|unknown_liq|unknown_from_high|observe | 0.3061 | 49 | 0 | 27 | 12 |
| KR|base_universe|volume_rank|unknown_liq|unknown_from_high|observe | 0.25 | 16 | 0 | 4 | 0 |

### US Worst
| US|base_universe|most_actives|high|deep|gap_pullback | -0.5714 | 7 | 0 | 0 | 4 |
| US|base_universe|most_actives|mid|deep|opening_range_pullback | -0.5294 | 17 | 0 | 0 | 9 |
| US|base_universe|most_actives|high|near_high|gap_pullback | -0.4 | 5 | 2 | 0 | 2 |
| US|base_universe|most_actives|high|pullback|opening_range_pullback | -0.2857 | 14 | 4 | 0 | 4 |
| US|base_universe|most_actives|mid|pullback|gap_pullback | -0.2857 | 7 | 0 | 0 | 2 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|mean_reversion | -0.2807 | 171 | 0 | 2 | 50 |
| US|base_universe|most_actives|mid|deep|gap_pullback | -0.2143 | 14 | 0 | 0 | 3 |
| US|base_universe|day_losers|unknown_liq|unknown_from_high|mean_reversion | -0.2121 | 66 | 0 | 0 | 14 |
| US|base_universe|day_losers|unknown_liq|unknown_from_high|gap_pullback | -0.2059 | 136 | 0 | 4 | 32 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|gap_pullback | -0.1962 | 209 | 0 | 3 | 44 |
| US|base_universe|day_losers|unknown_liq|unknown_from_high|opening_range_pullback | -0.1786 | 28 | 0 | 0 | 5 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|mean_reversion | -0.1687 | 1073 | 0 | 51 | 232 |

### US Best
| US|base_universe|most_actives|unknown_liq|unknown_from_high|momentum | 1.0 | 5 | 2 | 5 | 0 |
| US|base_universe|most_actives|high|at_high|gap_pullback | 0.9 | 20 | 3 | 18 | 0 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|momentum | 0.8889 | 18 | 0 | 16 | 0 |
| US|base_universe|most_actives|high|at_high|opening_range_pullback | 0.8667 | 15 | 0 | 13 | 0 |
| US|base_universe|most_actives|high|at_high|opening_range_pullback | 0.678 | 59 | 18 | 40 | 0 |
| US|base_universe|most_actives|high|at_high|gap_pullback | 0.5455 | 22 | 0 | 12 | 0 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|momentum | 0.4167 | 24 | 0 | 10 | 0 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|mean_reversion | 0.4074 | 27 | 0 | 11 | 0 |
| US|base_universe|day_gainers|high|at_high|gap_pullback | 0.375 | 24 | 3 | 9 | 0 |
| US|base_universe|most_actives|unknown_liq|unknown_from_high|gap_pullback | 0.3725 | 51 | 0 | 27 | 8 |
| US|base_universe|day_gainers|high|near_high|gap_pullback | 0.3333 | 6 | 2 | 2 | 0 |
| US|base_universe|day_gainers|unknown_liq|unknown_from_high|momentum | 0.3333 | 6 | 2 | 2 | 0 |

## Recommendations
- candidate_state: Promote a tier book to source of truth: CORE, WATCH, PROBATION, BENCH, QUARANTINE. Reason: Flat today_tickers replacement loses continuity and cannot express watch-only vs executable risk.
- preopen: Merge hard pins into session_open candidates, but force watch-only until post-open confirmation. Reason: Current hard pins are not reliable enough to auto-buy and can be dropped before Claude selection.
- preopen: Add low-liq ignition and late-reclaim watch buckets with 60m/120m confirmation, not open auction entry. Reason: The best missed KR winners were either low-liq ignition or late reclaim; naive soft expansion is negative.
- replacement: Use trainer/cohort delta gate for both KR and US replacement-in, with looser KR shadow rollout first. Reason: Replacement should require incoming quality to beat outgoing quality instead of rotating by freshness alone.
- execution: Route only final applied trade_ready, not raw Claude trade_ready, and block all new probes under stop-cluster disaster. Reason: Raw action can survive in logs after runtime normalization removes it; disaster blocks must own final execution.
- risk_exit: Keep cap2/MFE protection as the immediate overlay, then move to broker-backed persistent peak stops. Reason: Current local simulation shows the largest positive effect comes from left-tail clipping and MFE preservation.
- observability: Backfill forward labels into screener_quality rows and add known_at snapshots for every promotion/demotion. Reason: Current candidate quality logs explain funnel loss, but not enough forward PnL for rule optimization.
