# Full Profitability Review

Generated: 2026-05-08T12:30:03

## Basis

- closed_trades: 80
- selection_rows: 2720
- preopen_rows: 366
- valid_preopen_rows: 326
- screener_quality_rows: 7300
- action_routing_events: 35
- cohort_files: 99

Notes:
- All inputs are local files or sqlite rows; no broker/API/Claude calls are made.
- Preopen entry simulations are approximate: entry-to-final uses sampled anchor returns, not tick-level fills.
- Forward return fields in ticker_selection_log are post-selection audit labels and must not be used inside live gating without known_at controls.

## Closed Trades By Market
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR | 44 | 13/31 | 29.6% | -1.225% | -1.100% | 0.45 | -12.234% | +23.803% |
| US | 36 | 15/21 | 41.7% | -0.136% | -0.260% | 0.88 | -6.663% | +7.697% |

## Closed Trades By Strategy
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR|momentum | 27 | 9/18 | 33.3% | -0.373% | -0.992% | 0.79 | -12.234% | +23.803% |
| US|claude_price | 20 | 9/11 | 45.0% | +0.245% | -0.024% | 1.35 | -3.634% | +6.609% |
| KR|claude_price | 10 | 2/8 | 20.0% | -2.435% | -1.737% | 0.11 | -9.818% | +2.868% |
| US|gap_pullback | 6 | 2/4 | 33.3% | -0.469% | -1.847% | 0.74 | -3.746% | +7.697% |
| US|broker_sync | 4 | 0/4 | 0.0% | -3.590% | -3.449% | 0.00 | -6.663% | -0.800% |
| US|momentum | 4 | 2/2 | 50.0% | +1.707% | +1.283% | 5.33 | -1.465% | +5.726% |
| KR|continuation | 3 | 0/3 | 0.0% | -6.920% | -8.786% | 0.00 | -9.548% | -2.425% |
| KR|gap_pullback | 2 | 1/1 | 50.0% | -0.379% | -0.379% | 0.03 | -0.783% | +0.026% |
| US|mean_reversion | 2 | 2/0 | 100.0% | +0.284% | +0.284% | inf | +0.159% | +0.408% |
| KR|RECOVERY_MICRO | 1 | 0/1 | 0.0% | -0.323% | -0.323% | 0.00 | -0.323% | -0.323% |
| KR|broker_sync | 1 | 1/0 | 100.0% | +2.361% | +2.361% | inf | +2.361% | +2.361% |

## Selection Live Traded By Ready
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR|ready=0 | 8 | 3/5 | 37.5% | -0.904% | -0.553% | 0.33 | -5.617% | +1.780% |
| KR|ready=1 | 14 | 4/10 | 28.6% | -0.827% | -1.759% | 0.47 | -5.010% | +8.374% |
| US|ready=0 | 2 | 0/2 | 0.0% | -1.928% | -1.928% | 0.00 | -3.746% | -0.111% |
| US|ready=1 | 9 | 4/5 | 44.4% | +0.785% | -0.800% | 1.75 | -3.528% | +7.697% |

## Selection Live Traded By Strategy
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR|momentum | 19 | 6/13 | 31.6% | -0.933% | -1.709% | 0.43 | -5.617% | +8.374% |
| US|gap_pullback | 6 | 2/4 | 33.3% | -0.469% | -1.847% | 0.74 | -3.746% | +7.697% |
| US|momentum | 4 | 2/2 | 50.0% | +1.707% | +1.283% | 5.33 | -1.465% | +5.726% |
| KR|gap_pullback | 2 | 1/1 | 50.0% | -0.379% | -0.379% | 0.03 | -0.783% | +0.026% |
| KR|RECOVERY_MICRO | 1 | 0/1 | 0.0% | -0.323% | -0.323% | 0.00 | -0.323% | -0.323% |
| US|continuation | 1 | 0/1 | 0.0% | -0.800% | -0.800% | 0.00 | -0.800% | -0.800% |

## Selection Forward Max Runup By Ready
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR|ready=0 | 315 | 282/25 | 89.5% | +12.222% | +6.538% | 118.11 | -3.305% | +119.363% |
| KR|ready=1 | 72 | 69/2 | 95.8% | +19.153% | +9.149% | 906.44 | -0.786% | +119.363% |
| US|ready=0 | 418 | 383/29 | 91.6% | +6.833% | +4.899% | 63.29 | -13.736% | +88.487% |
| US|ready=1 | 103 | 97/5 | 94.2% | +7.737% | +5.698% | 343.08 | -0.985% | +33.446% |

## Preopen By Market
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR | 176 | 61/114 | 34.7% | -1.128% | -2.325% | 0.64 | -20.149% | +29.974% |
| US | 150 | 60/89 | 40.0% | -0.615% | -0.464% | 0.65 | -11.337% | +18.021% |

## Preopen Segments

### KR
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 176 | 61/114 | 34.7% | -1.128% | -2.325% | 0.64 | -20.149% | +29.974% |
| actual_selected | 33 | 16/16 | 48.5% | +1.054% | +0.000% | 1.46 | -9.302% | +18.500% |
| actual_trade_ready | 4 | 2/2 | 50.0% | +5.359% | +2.845% | 6.10 | -2.752% | +18.500% |
| hard_pin_current | 5 | 2/3 | 40.0% | -3.630% | -1.453% | 0.39 | -17.661% | +11.149% |
| soft_b | 56 | 16/40 | 28.6% | -2.716% | -3.899% | 0.37 | -17.661% | +16.622% |
| low_liq_tag | 110 | 40/69 | 36.4% | -0.388% | -1.509% | 0.85 | -20.149% | +29.974% |
| rank_1_10 | 28 | 8/19 | 28.6% | -3.705% | -5.115% | 0.21 | -17.661% | +11.149% |
| rank_11_30 | 60 | 19/41 | 31.7% | -1.053% | -2.550% | 0.66 | -15.098% | +29.931% |
| rank_31_plus | 88 | 34/54 | 38.6% | -0.359% | -1.509% | 0.86 | -20.149% | +29.974% |

### US
| Group | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 150 | 60/89 | 40.0% | -0.615% | -0.464% | 0.65 | -11.337% | +18.021% |
| actual_selected | 31 | 13/17 | 41.9% | +0.007% | -0.741% | 1.00 | -11.337% | +18.021% |
| actual_trade_ready | 4 | 4/0 | 100.0% | +9.258% | +8.999% | inf | +1.012% | +18.021% |
| hard_pin_current | 12 | 5/7 | 41.7% | -0.609% | -0.919% | 0.79 | -11.337% | +10.842% |
| soft_b | 75 | 28/47 | 37.3% | -0.460% | -0.480% | 0.76 | -11.337% | +18.021% |
| low_liq_tag | 9 | 3/6 | 33.3% | -1.179% | -0.728% | 0.34 | -8.627% | +2.825% |
| rank_1_10 | 40 | 14/26 | 35.0% | -1.415% | -0.993% | 0.46 | -11.337% | +10.842% |
| rank_11_30 | 80 | 39/41 | 48.8% | +0.198% | -0.020% | 1.17 | -10.685% | +18.021% |
| rank_31_plus | 30 | 7/22 | 23.3% | -1.719% | -1.633% | 0.22 | -7.960% | +5.474% |

## Preopen Rule Simulations

### KR
| Rule | Basis | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| current_hard_pin | final | 5 | 2/3 | 40.0% | -3.630% | -1.453% | 0.39 | -17.661% | +11.149% |
| soft_b_naive | final | 56 | 16/40 | 28.6% | -2.716% | -3.899% | 0.37 | -17.661% | +16.622% |
| soft_b_confirm30 | final | 15 | 10/5 | 66.7% | +2.038% | +1.571% | 2.37 | -8.307% | +12.935% |
| soft_b_confirm30 | entry_30m_to_final | 15 | 5/9 | 33.3% | -3.389% | -0.220% | 0.17 | -10.714% | +2.878% |
| soft_b_confirm60 | final | 8 | 6/2 | 75.0% | +4.517% | +3.757% | 7.78 | -2.815% | +12.935% |
| soft_b_confirm60 | entry_60m_to_final | 8 | 2/6 | 25.0% | -3.391% | -3.541% | 0.12 | -9.021% | +3.352% |
| low_liq_ignite60 | final | 8 | 7/1 | 87.5% | +8.129% | +6.486% | 101.14 | -0.649% | +18.500% |
| low_liq_ignite60 | entry_60m_to_final | 8 | 4/4 | 50.0% | +1.678% | +0.065% | 2.59 | -3.687% | +10.455% |
| late_reclaim_watch | final | 1 | 1/0 | 100.0% | +16.622% | +16.622% | inf | +16.622% | +16.622% |
| late_reclaim_watch | entry_120m_to_final | 1 | 1/0 | 100.0% | +1.478% | +1.478% | inf | +1.478% | +1.478% |

### US
| Rule | Basis | N | W/L | Win | Avg | Median | PF | Worst | Best |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| current_hard_pin | final | 12 | 5/7 | 41.7% | -0.609% | -0.919% | 0.79 | -11.337% | +10.842% |
| soft_b_naive | final | 75 | 28/47 | 37.3% | -0.460% | -0.480% | 0.76 | -11.337% | +18.021% |
| soft_b_confirm30 | final | 24 | 18/6 | 75.0% | +3.582% | +2.490% | 12.77 | -2.794% | +18.021% |
| soft_b_confirm30 | entry_30m_to_final | 24 | 12/12 | 50.0% | -0.254% | -0.333% | 0.80 | -5.835% | +5.229% |
| soft_b_confirm60 | final | 19 | 17/2 | 89.5% | +4.531% | +3.436% | 31.55 | -1.772% | +18.021% |
| soft_b_confirm60 | entry_60m_to_final | 19 | 9/10 | 47.4% | -0.768% | -0.394% | 0.54 | -7.809% | +4.217% |
| low_liq_ignite60 | final | 0 | 0/0 | 0.0% | +0.000% | +0.000% | NA | +0.000% | +0.000% |
| low_liq_ignite60 | entry_60m_to_final | 0 | 0/0 | 0.0% | +0.000% | +0.000% | NA | +0.000% | +0.000% |
| late_reclaim_watch | final | 0 | 0/0 | 0.0% | +0.000% | +0.000% | NA | +0.000% | +0.000% |
| late_reclaim_watch | entry_120m_to_final | 0 | 0/0 | 0.0% | +0.000% | +0.000% | NA | +0.000% | +0.000% |

## Missed Strong Preopen Candidates
| session_date | market | ticker | rank | score | risk_tags | selected | trade_ready | final | mfe | mae | ret5 | ret30 | ret60 | hard_pin | soft_b | low_liq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-05-06 | KR | 076610 | 44 | 0.18 | ['low_liquidity'] | False | False | 29.9735 | 29.9735 | -1.2732 | 5.5703 | 29.9735 | 26.7905 | False | False | True |
| 2026-05-06 | KR | 203650 | 21 | 0.18 | ['low_liquidity'] | False | False | 29.9308 | 29.9308 | -7.0934 | -1.2111 | -2.7682 | -3.9792 | False | False | True |
| 2026-05-06 | KR | 100590 | 40 | 0.18 | ['low_liquidity'] | False | False | 19.8381 | 29.9595 | -6.2078 | -1.2146 | -2.1592 | -2.5641 | False | False | True |
| 2026-05-08 | KR | 079190 | 17 | 0.51 | [] | False | False | 16.622 | 21.4477 | -10.6345 | -6.9705 | -8.311 | -6.2556 | False | True | False |
| 2026-05-08 | KR | 101170 | 57 | 0.18 | ['low_liquidity'] | False | False | 16.5867 | 20.9733 | 2.4674 | 5.7574 | 4.2495 | 5.5517 | False | False | True |
| 2026-05-06 | KR | 001440 | 16 | 0.18 | ['low_liquidity'] | True | False | 14.0033 | 21.911 | -1.1532 | 2.8007 | 9.2257 | 8.5667 | False | False | True |
| 2026-05-07 | KR | 078150 | 23 | 0.6 | ['limit_up_chase_risk'] | True | False | 12.9353 | 23.8806 | 3.2338 | 10.3234 | 13.1841 | 13.806 | False | True | False |
| 2026-05-06 | KR | 018880 | 48 | 0.18 | ['low_liquidity'] | False | False | 11.5464 | 15.0515 | -2.0619 | 2.6804 | 11.5464 | 8.4536 | False | False | True |
| 2026-05-07 | KR | 100590 | 2 | 0.75 | [] | True | False | 11.1486 | 27.8153 | -2.3649 | -2.027 | 23.536 | 15.8784 | True | True | False |
| 2026-05-06 | US | IREN | 2 | 0.55 | [] | True | False | 10.8422 | 12.1569 | 2.4881 | 7.426 | 5.3343 | 6.3573 | True | True | False |
| 2026-05-08 | KR | 332570 | 53 | 0.18 | ['low_liquidity'] | True | False | 10.2081 | 19.5243 | -0.9911 | 0.6938 | 18.4341 | 11.7939 | False | False | True |
| 2026-05-07 | KR | 203650 | 37 | 0.18 | ['low_liquidity'] | True | False | 9.9867 | 24.5007 | -0.7989 | 9.7204 | 5.8589 | 9.5872 | False | False | True |
| 2026-05-05 | US | IREN | 9 | 0.55 | [] | False | False | 9.2759 | 13.4398 | -0.8286 | 1.1116 | -0.2425 | 2.6245 | False | True | False |
| 2026-05-06 | KR | 001510 | 29 | 0.18 | ['low_liquidity'] | False | False | 8.6705 | 15.6069 | 3.0829 | 7.1291 | 6.9364 | 4.6243 | False | False | True |
| 2026-05-07 | KR | 002780 | 31 | 0.51 | [] | False | False | 8.296 | 14.8729 | 3.0643 | 13.3782 | 6.1286 | 4.7833 | False | True | False |
| 2026-05-07 | KR | 024840 | 21 | 0.6 | ['limit_up_chase_risk'] | True | False | 8.0139 | 24.7387 | -14.1696 | 7.0848 | -7.6655 | -3.7166 | False | True | False |
| 2026-05-05 | US | LEGN | 3 | 0.55 | [] | False | False | 7.7547 | 14.717 | -0.2091 | 1.7736 | 10.3585 | 9.566 | True | True | False |
| 2026-05-07 | KR | 059120 | 26 | 0.51 | [] | False | False | 7.2899 | 24.3568 | -2.2298 | 3.7736 | 4.2882 | 0.3431 | False | True | False |
| 2026-05-08 | KR | 001440 | 51 | 0.18 | ['low_liquidity'] | False | False | 6.5523 | 12.3245 | -2.1841 | 5.6162 | 9.0484 | 6.8643 | False | False | True |
| 2026-05-07 | US | LIVN | 18 | 0.55 | [] | False | False | 6.3009 | 7.5158 | 0.815 | 5.4935 | 3.4259 | 4.1352 | False | True | False |
| 2026-05-07 | KR | 003610 | 57 | 0.18 | ['low_liquidity'] | False | False | 6.1453 | 6.7039 | 0.5587 | 3.1657 | 1.4898 | 1.676 | False | False | True |
| 2026-05-07 | KR | 010140 | 36 | 0.43 | [] | False | False | 5.8085 | 5.9655 | 0.157 | 1.4129 | 0.7849 | 0.6279 | False | False | False |
| 2026-05-07 | US | TECH | 52 | 0.25 | [] | False | False | 5.4741 | 6.8664 | -2.0884 | -0.3903 | 3.3013 | 3.1537 | False | False | False |
| 2026-05-05 | US | GXO | 26 | 0.25 | [] | False | False | 5.3923 | 6.2892 | -1.448 | 2.0424 | 5.2302 | 4.4305 | False | False | False |
| 2026-05-06 | US | GPK | 11 | 0.55 | [] | False | False | 5.1724 | 6.0112 | 0.6524 | 4.6132 | 4.9394 | 4.5666 | False | True | False |

## Expanded Rule Risks
| session_date | market | ticker | rank | score | risk_tags | selected | trade_ready | final | mfe | mae | ret5 | ret30 | ret60 | hard_pin | soft_b | low_liq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-05-07 | KR | 007610 | 1 | 0.75 | [] | False | False | -17.6609 | 4.6311 | -21.4286 | -13.1083 | -19.7802 | -19.3093 | True | True | False |
| 2026-05-07 | KR | 037030 | 19 | 0.6 | ['limit_up_chase_risk'] | False | False | -15.0982 | 7.5491 | -17.6836 | -4.4467 | -13.9607 | -13.8573 | False | True | False |
| 2026-05-07 | US | IONQ | 1 | 0.55 | [] | True | False | -11.3373 | -1.2745 | -11.984 | -4.1469 | -2.7392 | -4.1887 | True | True | False |
| 2026-05-07 | US | OKLO | 13 | 0.55 | [] | False | False | -10.6853 | -0.1507 | -10.7259 | -1.5574 | -4.7162 | -1.8651 | False | True | False |
| 2026-05-07 | US | CORZ | 5 | 0.55 | [] | False | False | -10.5156 | -6.6179 | -15.5095 | -8.0593 | -13.7637 | -11.2667 | False | True | False |
| 2026-05-08 | KR | 100590 | 3 | 0.75 | [] | False | False | -10.5051 | 3.4343 | -13.1313 | 1.3131 | -0.202 | -6.3636 | True | True | False |
| 2026-05-08 | KR | 006910 | 20 | 0.51 | [] | False | False | -9.9359 | -2.3077 | -10.2564 | -5.7692 | -5.641 | -6.7308 | False | True | False |
| 2026-05-07 | KR | 356680 | 20 | 0.6 | ['limit_up_chase_risk'] | False | False | -9.7455 | -2.9237 | -10.6659 | -7.9589 | -7.4174 | -8.5544 | False | True | False |
| 2026-05-07 | KR | 025860 | 29 | 0.51 | [] | False | False | -9.3067 | -3.7987 | -9.7816 | -6.1728 | -5.5081 | -6.0779 | False | True | False |
| 2026-05-07 | KR | 018880 | 10 | 0.75 | [] | False | False | -9.2421 | 2.7726 | -10.1664 | -4.4362 | -7.8558 | -10.0739 | False | True | False |
| 2026-05-07 | US | IREN | 2 | 0.55 | [] | True | False | -8.9292 | 2.3778 | -9.1341 | -1.7547 | -1.7383 | 0.8855 | True | True | False |
| 2026-05-08 | KR | 011700 | 23 | 0.51 | [] | False | False | -8.7426 | -1.7682 | -9.4303 | -6.0904 | -4.9116 | -6.4833 | False | True | False |
| 2026-05-08 | KR | 215790 | 10 | 0.63 | [] | False | False | -8.3067 | 4.7923 | -8.9457 | 3.1949 | 1.5974 | -0.8946 | False | True | False |
| 2026-05-07 | US | QBTS | 4 | 0.55 | [] | False | False | -8.0151 | -0.6295 | -8.4767 | -1.5736 | -1.6156 | -1.8044 | False | True | False |
| 2026-05-07 | KR | 060310 | 8 | 0.75 | [] | False | False | -7.6653 | 0.6437 | -9.3037 | -3.1597 | -7.8994 | -7.8994 | False | True | False |
| 2026-05-07 | US | WULF | 3 | 0.55 | [] | True | False | -7.4786 | -2.2533 | -8.7995 | -5.2448 | -5.7498 | -4.798 | True | True | False |
| 2026-05-08 | KR | 003010 | 4 | 0.75 | [] | False | False | -7.4495 | -3.7879 | -7.8283 | -5.9343 | -5.4293 | -6.6919 | False | True | False |
| 2026-05-07 | US | TMC | 7 | 0.55 | [] | False | False | -7.3864 | 0.0 | -8.9286 | -0.6494 | -3.9773 | -2.1656 | False | True | False |
| 2026-05-07 | KR | 267320 | 4 | 0.75 | [] | False | False | -7.1066 | -0.1269 | -7.2335 | -3.4264 | -6.3452 | -6.3452 | False | True | False |
| 2026-05-08 | KR | 054920 | 9 | 0.63 | [] | False | False | -7.0991 | -1.3464 | -8.2007 | -3.5496 | -2.6928 | -5.3856 | False | True | False |
| 2026-05-08 | KR | 092790 | 12 | 0.63 | [] | False | False | -7.0784 | -3.5629 | -7.696 | -4.9881 | -4.8931 | -4.5131 | False | True | False |
| 2026-05-07 | KR | 452450 | 5 | 0.75 | [] | False | False | -6.8921 | 3.1209 | -7.0221 | -3.1209 | -5.7217 | -6.2419 | False | True | False |
| 2026-05-07 | KR | 036540 | 18 | 0.63 | [] | False | False | -6.5242 | 0.6749 | -8.5489 | -4.9494 | -6.6367 | -7.649 | False | True | False |
| 2026-05-08 | KR | 028050 | 14 | 0.6 | ['limit_up_chase_risk'] | False | False | -6.4441 | -3.7267 | -8.3851 | -6.8323 | -7.9193 | -6.3665 | False | True | False |
| 2026-05-08 | KR | 065440 | 21 | 0.51 | [] | False | False | -6.2972 | 5.0378 | -6.6751 | -3.1486 | 2.6448 | -2.267 | False | True | False |

## Missed Selection Runup Top
| date | market | ticker | trade_ready | signal_fired | blocked_reason | strategy | forward_1d | forward_3d | max_runup_3d | max_drawdown_3d |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-04-29 | KR | 024840 | 1 | 0 | None | gap_pullback | 29.9363 | 119.3631 | 119.3631 | 2.9299 |
| 2026-04-29 | KR | 024840 | 0 | 0 | None | None | 29.9363 | 119.3631 | 119.3631 | 2.9299 |
| 2026-04-30 | KR | 024840 | 1 | 0 | None | gap_pullback | 30.0 | 82.3529 | 110.5882 | 12.7451 |
| 2026-04-30 | KR | 024840 | 1 | 0 | None | gap_pullback | 30.0 | 82.3529 | 110.5882 | 12.7451 |
| 2026-04-21 | US | MXL | 0 | 0 | None | None | 0.5638 | 78.9911 | 88.4866 | -3.5015 |
| 2026-04-21 | US | MXL | 0 | 0 | None | None | 0.5638 | 78.9911 | 88.4866 | -3.5015 |
| 2026-04-21 | US | MXL | 0 | 0 | None | None | 0.5638 | 78.9911 | 88.4866 | -3.5015 |
| 2026-04-28 | KR | 006340 | 1 | 0 | None | momentum | 29.9901 | 72.2939 | 84.3098 | -2.3833 |
| 2026-04-28 | KR | 006340 | 0 | 0 | None | None | 29.9901 | 72.2939 | 84.3098 | -2.3833 |
| 2026-04-30 | KR | 006345 | 0 | 0 | None | None | 29.9799 | 68.9135 | 68.9135 | 10.664 |
| 2026-04-30 | KR | 006345 | 0 | 0 | None | None | 29.9799 | 68.9135 | 68.9135 | 10.664 |
| 2026-04-30 | KR | 007610 | 0 | 0 | None | None | 29.9408 | 24.497 | 68.8757 | 18.4615 |
| 2026-04-22 | KR | 006340 | 0 | 0 | None | None | 26.3975 | 57.9193 | 65.528 | 4.3478 |
| 2026-04-27 | KR | 006340 | 0 | 0 | None | None | -0.9833 | 47.9843 | 63.0285 | -4.4248 |
| 2026-04-27 | KR | 006340 | 0 | 0 | None | None | -0.9833 | 47.9843 | 63.0285 | -4.4248 |
| 2026-04-22 | KR | 209640 | 1 | 0 | None | opening_range_pullback | 5.6198 | 49.5868 | 61.4876 | -2.8099 |
| 2026-04-22 | KR | 209640 | 1 | 0 | None | opening_range_pullback | 5.6198 | 49.5868 | 61.4876 | -2.8099 |
| 2026-04-22 | KR | 209640 | 1 | 0 | None | gap_pullback | 5.6198 | 49.5868 | 61.4876 | -2.8099 |
| 2026-04-29 | KR | 006340 | 0 | 0 | None | None | 14.9733 | 37.8151 | 55.0802 | -1.9862 |
| 2026-04-29 | KR | 006340 | 0 | 0 | None | None | 14.9733 | 37.8151 | 55.0802 | -1.9862 |

## Daily Entry Caps
| Rule | Kept | N | W/L | Win | Avg | PF |
|---|---:|---:|---:|---:|---:|---:|
| total_cap_1 | 11/33 | 11 | 6/5 | 54.5% | +0.112% | 1.08 |
| total_cap_2 | 20/33 | 20 | 7/13 | 35.0% | -0.481% | 0.62 |
| total_cap_3 | 25/33 | 25 | 9/16 | 36.0% | -0.148% | 0.87 |
| per_market_cap_1 | 17/33 | 17 | 9/8 | 52.9% | +0.517% | 1.42 |
| per_market_cap_2 | 27/33 | 27 | 10/17 | 37.0% | -0.119% | 0.90 |
| per_market_cap_3 | 31/33 | 31 | 11/20 | 35.5% | -0.160% | 0.86 |

## Blocked Signals
| Reason | N | Ready | Fwd1D Avg | Runup3D Avg |
|---|---:|---:|---:|---:|
| DAILY_LOSS_LIMIT | 3 | 2 | +4.993% | +13.009% |
| ORDER_UNKNOWN_UNRESOLVED | 1 | 1 | +8.759% | +12.069% |
| PATHB_ORDER_UNKNOWN_SAME_TICKER | 1 | 1 | +7.601% | +13.948% |
| SAME_DAY_REENTRY_AFTER_STOP | 1 | 1 | NA | NA |
| insufficient_cash | 2 | 1 | +4.716% | +16.431% |
| order_rejected | 3 | 1 | +2.941% | +8.005% |
| order_size_too_small | 3 | 2 | +9.678% | +26.011% |
| qty_zero | 6 | 3 | +4.778% | +10.105% |

## Screener Funnel

### All Status Counts
- KR|SCREENER_ONLY: 1344
- US|SCREENER_ONLY: 1085
- US|WATCH: 1030
- US|VETO: 955
- KR|VETO: 827
- KR|WATCH: 774
- KR|NOT_IN_PROMPT: 707
- US|TRADE_READY: 238
- US|NOT_IN_PROMPT: 192
- KR|TRADE_READY: 148

### Latest Status Counts
- US|SCREENER_ONLY: 206
- KR|SCREENER_ONLY: 137
- US|VETO: 77
- KR|NOT_IN_PROMPT: 67
- US|WATCH: 64
- KR|VETO: 50
- KR|WATCH: 45
- US|NOT_IN_PROMPT: 15
- US|TRADE_READY: 12
- KR|TRADE_READY: 3

### Prompt Counts
- US|input=True: 3308
- KR|input=True: 3074
- KR|input=False: 726
- US|input=False: 192

## Action Routing

### Final Action Counts
- US|WATCH: 239
- KR|WATCH: 159
- US|PROBE_READY: 43
- US|BUY_READY: 32
- KR|PROBE_READY: 25
- US|PULLBACK_WAIT: 20
- KR|BUY_READY: 16
- KR|PULLBACK_WAIT: 14

### Route Reason Counts
- US|(none)|watch: 191
- KR|(none)|watch: 140
- US|PlanA.probe|probe_ready: 43
- US|PlanA.buy|buy_ready: 32
- US|(none)|claude_avoid: 31
- KR|PlanA.probe|probe_ready: 25
- US|PathB.wait|pullback_wait: 20
- KR|PlanA.buy|buy_ready: 16
- KR|PathB.wait|pullback_wait: 14
- US|(none)|add_shadow_only: 13
- KR|(none)|claude_avoid: 12
- US|(none)|missing_pullback_target: 4
- KR|(none)|add_shadow_only: 4
- KR|(none)|pullback_wait_blocked_negative_context: 2
- KR|(none)|missing_pullback_target: 1

## Cohort Reliability

### KR Worst
| KR|base_universe|unclassified|high|pullback|gap_pullback | -0.5 | 6 | 3 | 0 | 3 |
| KR|base_universe|unclassified|high|deep|gap_pullback | -0.2222 | 18 | 1 | 0 | 4 |
| KR|base_universe|unclassified|high|at_high|opening_range_pullback | -0.1364 | 22 | 3 | 1 | 4 |
| KR|base_universe|unclassified|mid|at_high|opening_range_pullback | -0.0769 | 13 | 1 | 0 | 1 |
| KR|base_universe|unclassified|mid|deep|momentum | -0.0769 | 13 | 0 | 0 | 1 |
| KR|base_universe|unclassified|mid|deep|opening_range_pullback | -0.0667 | 15 | 0 | 0 | 1 |
| KR|base_universe|unclassified|high|at_high|momentum | 0.0 | 7 | 2 | 1 | 1 |
| KR|base_universe|unclassified|high|deep|momentum | 0.0 | 7 | 0 | 0 | 0 |
| KR|base_universe|unclassified|high|pullback|opening_range_pullback | 0.0 | 5 | 0 | 0 | 0 |
| KR|base_universe|unclassified|low|at_high|gap_pullback | 0.0 | 7 | 0 | 0 | 0 |
| KR|base_universe|unclassified|low|at_high|opening_range_pullback | 0.0 | 9 | 0 | 0 | 0 |
| KR|base_universe|unclassified|low|deep|gap_pullback | 0.0 | 14 | 0 | 0 | 0 |

### KR Best
| KR|base_universe|unclassified|high|at_high|gap_pullback | 0.2857 | 7 | 4 | 3 | 1 |
| KR|base_universe|unclassified|mid|at_high|momentum | 0.1667 | 6 | 1 | 1 | 0 |
| KR|base_universe|unclassified|mid|at_high|gap_pullback | 0.1429 | 7 | 2 | 2 | 1 |
| KR|base_universe|unclassified|high|at_high|momentum | 0.0 | 7 | 2 | 1 | 1 |
| KR|base_universe|unclassified|high|deep|momentum | 0.0 | 7 | 0 | 0 | 0 |
| KR|base_universe|unclassified|high|pullback|opening_range_pullback | 0.0 | 5 | 0 | 0 | 0 |
| KR|base_universe|unclassified|low|at_high|gap_pullback | 0.0 | 7 | 0 | 0 | 0 |
| KR|base_universe|unclassified|low|at_high|opening_range_pullback | 0.0 | 9 | 0 | 0 | 0 |
| KR|base_universe|unclassified|low|deep|gap_pullback | 0.0 | 14 | 0 | 0 | 0 |
| KR|base_universe|unclassified|low|deep|momentum | 0.0 | 13 | 0 | 0 | 0 |
| KR|base_universe|unclassified|low|deep|opening_range_pullback | 0.0 | 12 | 0 | 0 | 0 |
| KR|base_universe|unclassified|low|pullback|momentum | 0.0 | 5 | 0 | 0 | 0 |

### US Worst
| US|base_universe|most_actives|high|near_high|gap_pullback | -0.4 | 5 | 2 | 0 | 2 |
| US|base_universe|most_actives|high|pullback|opening_range_pullback | -0.2857 | 14 | 4 | 0 | 4 |
| US|base_universe|most_actives|mid|pullback|gap_pullback | -0.2857 | 7 | 0 | 0 | 2 |
| US|base_universe|most_actives|mid|deep|gap_pullback | -0.2143 | 14 | 0 | 0 | 3 |
| US|base_universe|most_actives|high|pullback|gap_pullback | -0.1667 | 12 | 2 | 0 | 2 |
| US|base_universe|most_actives|high|deep|gap_pullback | -0.1429 | 14 | 0 | 0 | 2 |
| US|base_universe|most_actives|mid|at_high|mean_reversion | -0.1429 | 7 | 0 | 0 | 1 |
| US|base_universe|day_losers|high|deep|opening_range_pullback | -0.125 | 8 | 0 | 0 | 1 |
| US|base_universe|most_actives|mid|at_high|opening_range_pullback | -0.0278 | 36 | 1 | 2 | 3 |
| US|base_universe|day_gainers|high|at_high|opening_range_pullback | 0.0 | 9 | 0 | 0 | 0 |
| US|base_universe|day_gainers|low|at_high|gap_pullback | 0.0 | 45 | 0 | 0 | 0 |
| US|base_universe|day_gainers|low|at_high|opening_range_pullback | 0.0 | 21 | 0 | 0 | 0 |

### US Best
| US|base_universe|day_gainers|high|pullback|gap_pullback | 0.2 | 5 | 3 | 2 | 1 |
| US|base_universe|day_gainers|high|at_high|gap_pullback | 0.1429 | 14 | 2 | 2 | 0 |
| US|base_universe|day_gainers|high|near_high|gap_pullback | 0.1429 | 7 | 1 | 1 | 0 |
| US|base_universe|most_actives|high|at_high|gap_pullback | 0.0476 | 21 | 1 | 1 | 0 |
| US|base_universe|most_actives|high|at_high|opening_range_pullback | 0.025 | 80 | 18 | 17 | 15 |
| US|base_universe|day_gainers|high|at_high|opening_range_pullback | 0.0 | 9 | 0 | 0 | 0 |
| US|base_universe|day_gainers|low|at_high|gap_pullback | 0.0 | 45 | 0 | 0 | 0 |
| US|base_universe|day_gainers|low|at_high|opening_range_pullback | 0.0 | 21 | 0 | 0 | 0 |
| US|base_universe|day_gainers|low|deep|gap_pullback | 0.0 | 8 | 0 | 0 | 0 |
| US|base_universe|day_gainers|low|near_high|gap_pullback | 0.0 | 28 | 0 | 0 | 0 |
| US|base_universe|day_gainers|low|pullback|gap_pullback | 0.0 | 28 | 0 | 0 | 0 |
| US|base_universe|day_gainers|low|pullback|opening_range_pullback | 0.0 | 5 | 0 | 0 | 0 |

## Recommendations
- candidate_state: Promote a tier book to source of truth: CORE, WATCH, PROBATION, BENCH, QUARANTINE. Reason: Flat today_tickers replacement loses continuity and cannot express watch-only vs executable risk.
- preopen: Merge hard pins into session_open candidates, but force watch-only until post-open confirmation. Reason: Current hard pins are not reliable enough to auto-buy and can be dropped before Claude selection.
- preopen: Add low-liq ignition and late-reclaim watch buckets with 60m/120m confirmation, not open auction entry. Reason: The best missed KR winners were either low-liq ignition or late reclaim; naive soft expansion is negative.
- replacement: Use trainer/cohort delta gate for both KR and US replacement-in, with looser KR shadow rollout first. Reason: Replacement should require incoming quality to beat outgoing quality instead of rotating by freshness alone.
- execution: Route only final applied trade_ready, not raw Claude trade_ready, and block all new probes under stop-cluster disaster. Reason: Raw action can survive in logs after runtime normalization removes it; disaster blocks must own final execution.
- risk_exit: Keep cap2/MFE protection as the immediate overlay, then move to broker-backed persistent peak stops. Reason: Current local simulation shows the largest positive effect comes from left-tail clipping and MFE preservation.
- observability: Backfill forward labels into screener_quality rows and add known_at snapshots for every promotion/demotion. Reason: Current candidate quality logs explain funnel loss, but not enough forward PnL for rule optimization.
