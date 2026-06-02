# Live Improvement Simulation

Generated: 2026-06-01T14:52:59

## Basis

- Closed trades: 162 from `state/live_decisions.jsonl`.
- Selection closed trades: 41 from `data/ticker_selection_log.db`.
- Blocked signals: 32 from `data/ticker_selection_log.db`.
- No Claude calls and no broker/API calls were made.

Assumptions:
- No intraday tick replay was available, so loss caps are simulated by clipping realized return at the cap.
- MFE protection assumes a protective stop would have filled at the simulated floor after the recorded MFE was reached.
- Estimated KRW PnL scales the recorded KRW PnL by simulated_pct / realized_pct where possible.
- trade_ready and daily-entry simulations use ticker_selection_log rows because live_decisions does not consistently include selection gate metadata.

## Scenario Result

| Scenario | N | W/L | Win | Avg pct | PF | Est. PnL KRW | Delta KRW |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 162 | 67/95 | 41.4% | -0.172% | 0.87 | +29,923 | -0 |
| loss_cap_3_only | 162 | 67/95 | 41.4% | +0.204% | 1.21 | +99,635 | +69,712 |
| loss_cap_2_only | 162 | 67/95 | 41.4% | +0.375% | 1.46 | +148,137 | +118,214 |
| loss_cap_1_5_only | 162 | 67/95 | 41.4% | +0.523% | 1.78 | +194,351 | +164,428 |
| current_code_cap3_floor0_5_at_mfe2 | 162 | 76/86 | 46.9% | +0.313% | 1.34 | +139,141 | +109,217 |
| proposed_cap2_mfe_protection | 162 | 76/75 | 46.9% | +0.679% | 2.01 | +270,312 | +240,389 |
| aggressive_cap1_5_mfe_protection | 162 | 87/75 | 53.7% | +0.840% | 2.54 | +324,193 | +294,270 |

## Market Split

| Scenario | Market | N | W/L | Win | Avg pct | PF | Est. PnL KRW | Delta KRW |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | KR | 56 | 16/40 | 28.6% | -1.387% | 0.40 | -98,396 | +0 |
| baseline | US | 106 | 51/55 | 48.1% | +0.471% | 1.54 | +128,319 | +0 |
| loss_cap_3_only | KR | 56 | 16/40 | 28.6% | -0.505% | 0.65 | -54,076 | +44,319 |
| loss_cap_3_only | US | 106 | 51/55 | 48.1% | +0.579% | 1.76 | +153,711 | +25,392 |
| loss_cap_2_only | KR | 56 | 16/40 | 28.6% | -0.203% | 0.82 | -30,718 | +67,677 |
| loss_cap_2_only | US | 106 | 51/55 | 48.1% | +0.681% | 2.03 | +178,856 | +50,537 |
| loss_cap_1_5_only | KR | 56 | 16/40 | 28.6% | +0.030% | 1.03 | -11,790 | +86,606 |
| loss_cap_1_5_only | US | 106 | 51/55 | 48.1% | +0.783% | 2.40 | +206,141 | +77,822 |
| current_code_cap3_floor0_5_at_mfe2 | KR | 56 | 19/37 | 33.9% | -0.381% | 0.71 | -41,030 | +57,366 |
| current_code_cap3_floor0_5_at_mfe2 | US | 106 | 57/49 | 53.8% | +0.680% | 1.97 | +180,170 | +51,851 |
| proposed_cap2_mfe_protection | KR | 56 | 19/34 | 33.9% | +0.145% | 1.15 | -680 | +97,715 |
| proposed_cap2_mfe_protection | US | 106 | 57/41 | 53.8% | +0.961% | 2.84 | +270,992 | +142,673 |
| aggressive_cap1_5_mfe_protection | KR | 56 | 22/34 | 39.3% | +0.390% | 1.52 | +19,365 | +117,760 |
| aggressive_cap1_5_mfe_protection | US | 106 | 65/41 | 61.3% | +1.077% | 3.46 | +304,828 | +176,509 |

## Proposed Scenario Changed Trades

| Time | Market | Ticker | Strategy | Exit | Old | MFE | Sim | Old KRW | Sim KRW |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| 2026-04-21T09:24:50+09:00 | KR | 011000 | continuation | stop_loss | -9.548% | +0.000% | -2.000% | -455 | -95 |
| 2026-04-21T10:45:09+09:00 | KR | 065420 | continuation | max_hold | -2.425% | +0.000% | -2.000% | -122 | -100 |
| 2026-04-22T09:39:41+09:00 | KR | 424760 | continuation | stop_loss | -8.786% | +0.000% | -2.000% | -5,037 | -1,147 |
| 2026-04-22T14:55:14+09:00 | KR | 078130 | momentum | stop_loss | -12.234% | +0.000% | -2.000% | -1,446 | -236 |
| 2026-04-23T23:23:37+09:00 | US | OKLO | gap_pullback | max_hold | -3.746% | +0.000% | -2.000% | -8,715 | -4,653 |
| 2026-04-27T13:38:36+09:00 | KR | 001250 | momentum | intraday_review_sell | -2.695% | +0.000% | -2.000% | -2,797 | -2,076 |
| 2026-04-27T22:56:18+09:00 | US | QCOM | gap_pullback | intraday_review_sell | -3.528% | +0.000% | -2.000% | -8,209 | -4,653 |
| 2026-04-28T09:13:47+09:00 | KR | 002780 | claude_price | stop_loss | -9.818% | +0.000% | -2.000% | -9,947 | -2,026 |
| 2026-04-28T10:40:12+09:00 | KR | 452190 | momentum | intraday_review_sell | -5.617% | +0.000% | -2.000% | -5,914 | -2,106 |
| 2026-04-28T10:40:31+09:00 | KR | 452260 | momentum | intraday_review_sell | -5.010% | +0.000% | -2.000% | -5,140 | -2,052 |
| 2026-04-28T10:41:12+09:00 | KR | 001440 | momentum | intraday_review_sell | -2.008% | +0.000% | -2.000% | -2,819 | -2,808 |
| 2026-04-29T09:17:13+09:00 | KR | 058430 | claude_price | stop_loss | -7.381% | +0.000% | -2.000% | -7,381 | -2,000 |
| 2026-04-30T03:00:56+09:00 | US | TEVA | claude_price | intraday_review_sell | +0.392% | +2.447% | +1.101% | +404 | +1,134 |
| 2026-04-30T13:48:11+09:00 | KR | 046890 | momentum | intraday_review_sell | -2.915% | +1.575% | +0.000% | -3,369 | +0 |
| 2026-04-30T13:48:29+09:00 | KR | 006340 | momentum | intraday_review_sell | -1.936% | +1.246% | +0.000% | -2,175 | +0 |
| 2026-04-30T14:50:37+09:00 | KR | 125020 | claude_price | intraday_review_sell | -1.209% | +1.016% | +0.000% | -1,285 | +0 |
| 2026-04-30T23:16:26+09:00 | US | STX | gap_pullback | profit_floor | +0.457% | +3.775% | +1.699% | +4,415 | +16,421 |
| 2026-05-01T00:31:57+09:00 | US | VIAV | claude_price | intraday_review_sell | -3.634% | +0.000% | -2.000% | -5,631 | -3,099 |
| 2026-05-01T01:33:04+09:00 | US | QCOM | claude_price | intraday_review_sell | +0.184% | +2.613% | +1.176% | +491 | +3,142 |
| 2026-05-04T13:26:43+09:00 | KR | 006910 | momentum | profit_floor | +0.292% | +2.789% | +1.255% | +293 | +1,260 |
| 2026-05-04T14:58:20+09:00 | KR | 199820 | momentum | trail_stop | +0.985% | +5.437% | +2.447% | +1,041 | +2,587 |
| 2026-05-05T00:48:53+09:00 | US | EAT | gap_pullback | intraday_review_sell | -2.354% | +1.636% | +0.000% | -5,225 | +0 |
| 2026-05-05T02:08:13+09:00 | US | CELC | momentum | trail_stop | -0.111% | +3.162% | +1.423% | -238 | +3,056 |
| 2026-05-05T22:30:20+09:00 | US | EAT | broker_sync | stop_loss | -5.599% | +0.000% | -2.000% | -12,372 | -4,419 |
| 2026-05-05T22:58:47+09:00 | US | EAT | broker_sync | stop_loss | -6.663% | +0.000% | -2.000% | -14,724 | -4,420 |
| 2026-05-06T02:49:46+09:00 | US | SFM | claude_price | intraday_review_sell | -2.782% | +1.534% | +0.000% | -3,334 | +0 |
| 2026-05-06T13:25:21+09:00 | KR | 007610 | momentum | profit_floor | +0.360% | +3.791% | +1.706% | +383 | +1,815 |
| 2026-05-07T10:00:11+09:00 | KR | 078150 | momentum | loss_cap | -3.210% | +0.000% | -2.000% | -6,382 | -3,976 |
| 2026-05-07T10:02:14+09:00 | KR | 001440 | claude_price | loss_cap | -2.264% | +0.159% | -2.000% | -4,259 | -3,762 |
| 2026-05-07T11:04:52+09:00 | KR | 024840 | claude_price | loss_cap | -3.648% | +2.679% | +1.206% | -7,191 | +2,376 |
| 2026-05-07T12:15:59+09:00 | KR | 042940 | claude_price | claude_price_stop | +2.868% | +7.471% | +3.362% | +5,417 | +6,350 |
| 2026-05-08T01:51:12+09:00 | US | FTNT | broker_sync | stop_loss | -1.298% | +2.190% | +0.986% | -2,054 | +1,560 |
| 2026-05-08T10:19:15+09:00 | KR | 010170 | momentum | loss_cap | -2.799% | +0.000% | -2.000% | -5,149 | -3,680 |
| 2026-05-08T10:34:55+09:00 | KR | 078150 | momentum | trail_stop | -0.992% | +3.792% | +1.706% | -1,938 | +3,334 |
| 2026-05-08T10:45:14+09:00 | KR | 054450 | claude_price | loss_cap | -2.436% | +0.000% | -2.000% | -4,620 | -3,793 |
| 2026-05-08T14:31:43+09:00 | KR | 264850 | claude_price | loss_cap | -3.498% | +0.000% | -2.000% | -6,960 | -3,979 |
| 2026-05-08T22:50:40+09:00 | US | DKNG | gap_pullback | loss_cap | -2.576% | +0.000% | -2.000% | -5,079 | -3,943 |
| 2026-05-11T11:03:34+09:00 | KR | 067170 | momentum | profit_floor | +0.277% | +4.137% | +1.862% | +550 | +3,701 |
| 2026-05-11T11:25:11+09:00 | KR | 078150 | momentum | loss_cap | -2.629% | +0.000% | -2.000% | -5,185 | -3,944 |
| 2026-05-12T10:17:24+09:00 | KR | 010170 | momentum | trail_stop | -0.869% | +3.885% | +1.748% | -1,544 | +3,105 |
| 2026-05-12T10:37:41+09:00 | KR | 018880 | broker_sync | stop_loss | -13.624% | +0.000% | -2.000% | -26,681 | -3,917 |
| 2026-05-13T23:13:25+09:00 | US | AAOI | claude_price | profit_ladder | +0.508% | +3.809% | +1.714% | +1,478 | +4,990 |
| 2026-05-13T23:15:14+09:00 | US | ON | gap_pullback | profit_floor | +0.290% | +2.141% | +0.963% | +482 | +1,604 |
| 2026-05-15T01:53:09+09:00 | US | IREN | claude_price | loss_cap | -2.001% | +0.170% | -2.000% | -5,379 | -5,377 |
| 2026-05-16T03:24:45+09:00 | US | HUBS | claude_price | profit_ladder | -0.249% | +1.432% | +0.000% | -742 | +0 |
| 2026-05-16T03:25:29+09:00 | US | DXCM | claude_price | loss_cap | -2.197% | +0.000% | -2.000% | -6,165 | -5,612 |
| 2026-05-18T22:22:18+09:00 | US | MSFT | broker_sync | hard_stop | -1.549% | +1.092% | +0.000% | -9,828 | +0 |
| 2026-05-18T23:46:09+09:00 | US | D | claude_price | loss_cap | -2.046% | +0.000% | -2.000% | -4,213 | -4,119 |
| 2026-05-20T04:03:03+09:00 | US | MXL | claude_price | loss_cap | -2.651% | +0.000% | -2.000% | -7,666 | -5,784 |
| 2026-05-20T22:39:50+09:00 | US | CLSK | claude_price | profit_ladder | -0.148% | +1.393% | +0.000% | -201 | +0 |
| 2026-05-21T01:00:21+09:00 | US | AXTI | claude_price | loss_cap | -5.381% | +2.433% | +1.095% | -9,193 | +1,871 |
| 2026-05-22T23:13:13+09:00 | US | AAOI | claude_price | loss_cap | -2.922% | +0.261% | -2.000% | -7,772 | -5,319 |
| 2026-05-23T02:09:44+09:00 | US | IONQ | claude_price | profit_ladder | +0.582% | +3.403% | +1.531% | +1,683 | +4,432 |
| 2026-05-23T02:25:21+09:00 | US | QBTS | claude_price | profit_ladder | +3.082% | +7.427% | +3.342% | +8,213 | +8,905 |
| 2026-05-26T10:15:03+09:00 | KR | 027360 | claude_price | loss_cap | -2.419% | +0.000% | -2.000% | -4,992 | -4,128 |
| 2026-05-26T22:31:35+09:00 | US | IONQ | claude_price | profit_ladder | -1.443% | +2.033% | +0.915% | -5,553 | +3,521 |
| 2026-05-26T22:36:51+09:00 | US | SMCI | claude_price | profit_ladder | -0.936% | +1.593% | +0.000% | -4,064 | +0 |
| 2026-05-27T00:35:23+09:00 | US | WULF | claude_price | profit_ladder | -0.153% | +1.954% | +0.000% | -292 | +0 |
| 2026-05-27T00:57:19+09:00 | US | NVTS | claude_price | mfe_breakeven | -0.403% | +3.897% | +1.754% | -781 | +3,398 |
| 2026-05-27T01:23:15+09:00 | US | IBM | claude_price | intraday_review_sell | +3.472% | +8.969% | +4.036% | +12,657 | +14,712 |
| 2026-05-27T09:47:57+09:00 | KR | 456010 | momentum | loss_cap | -2.754% | +0.000% | -2.000% | -5,370 | -3,900 |
| 2026-05-27T11:20:58+09:00 | KR | 232680 | momentum | loss_cap | -2.551% | +0.000% | -2.000% | -11,293 | -8,854 |
| 2026-05-27T23:49:17+09:00 | US | TSM | claude_price | claude_price_stop | +0.193% | +2.423% | +1.090% | +1,205 | +6,824 |
| 2026-05-27T23:53:40+09:00 | US | HPE | broker_sync | loss_cap | -2.082% | +0.000% | -2.000% | -3,546 | -3,406 |
| 2026-05-29T00:11:27+09:00 | US | MDB | claude_price | loss_cap | -2.124% | +0.000% | -2.000% | -10,468 | -9,857 |
| 2026-05-29T23:08:43+09:00 | US | NBIS | claude_price | claude_price_stop | -0.509% | +2.711% | +1.220% | -1,738 | +4,168 |
| 2026-05-29T23:26:59+09:00 | US | QCOM | claude_price | loss_cap | -2.095% | +1.897% | +0.000% | -8,050 | +0 |
| 2026-05-29T23:30:16+09:00 | US | IREN | broker_sync | loss_cap | -3.919% | +0.000% | -2.000% | -15,104 | -7,708 |

## Selection Gate Simulation

| Filter | N | W/L | Win | Avg pct | PF |
|---|---:|---:|---:|---:|---:|
| baseline | 41 | 13/28 | 31.7% | -0.731% | 0.56 |
| trade_ready_1 | 28 | 9/19 | 32.1% | -0.437% | 0.69 |
| trade_ready_0 | 13 | 4/9 | 30.8% | -1.364% | 0.39 |
| KR_ready_1 | 18 | 5/13 | 27.8% | -0.971% | 0.37 |
| KR_ready_0 | 10 | 3/7 | 30.0% | -2.172% | 0.14 |
| US_ready_1 | 10 | 4/6 | 40.0% | +0.525% | 1.46 |
| US_ready_0 | 3 | 1/2 | 33.3% | +1.329% | 2.03 |
| block_not_ready | 28 | 9/19 | 32.1% | -0.437% | 0.69 |
| block_kr_momentum | 17 | 6/11 | 35.3% | +0.428% | 1.42 |
| us_only | 13 | 5/8 | 38.5% | +0.710% | 1.61 |
| us_ready_only | 10 | 4/6 | 40.0% | +0.525% | 1.46 |

## Daily Entry Caps

| Rule | Kept | N | W/L | Win | Avg pct | PF |
|---|---:|---:|---:|---:|---:|---:|
| max_total_daily_entries_1 | 15/41 | 15 | 7/8 | 46.7% | -0.262% | 0.80 |
| max_total_daily_entries_2 | 27/41 | 27 | 9/18 | 33.3% | -0.857% | 0.51 |
| max_total_daily_entries_3 | 33/41 | 33 | 11/22 | 33.3% | -0.548% | 0.64 |
| max_market_daily_entries_1 | 22/41 | 22 | 11/11 | 50.0% | +0.521% | 1.43 |
| max_market_daily_entries_2 | 34/41 | 34 | 12/22 | 35.3% | -0.491% | 0.69 |
| max_market_daily_entries_3 | 39/41 | 39 | 13/26 | 33.3% | -0.496% | 0.66 |

## Blocked Opportunity Check

| Reason | N | Ready | Forward 1D Avg | Runup 3D Avg |
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

## Direction Assessment

Additive overlays:
- loss_cap and profit_floor improve the left tail without changing candidate generation.
- MFE-based profit preservation is still an overlay unless it is backed by persistent peak/stop state and broker-side reconciliation.

Structural changes:
- trade_ready=0 blocking must live at the final execution gate with explicit override_reason logging.
- KR momentum demotion is a policy-router change, not just a threshold change.
- strategy PF demotion to paper-only needs a per-strategy lifecycle state table.
- Exact replay requires a unified fill ledger and intraday price path snapshots; current logs support only approximate overlay simulation.

Verdict: Use risk overlays immediately, but do not keep adding isolated rules as the main path. The durable fix is an execution contract plus policy state: gate -> size -> order -> fill ledger -> exit ownership -> promotion/demotion.
