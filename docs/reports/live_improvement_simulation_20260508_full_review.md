# Live Improvement Simulation

Generated: 2026-05-08T12:25:03

## Basis

- Closed trades: 80 from `state/live_decisions.jsonl`.
- Selection closed trades: 33 from `data/ticker_selection_log.db`.
- Blocked signals: 20 from `data/ticker_selection_log.db`.
- No Claude calls and no broker/API calls were made.

Assumptions:
- No intraday tick replay was available, so loss caps are simulated by clipping realized return at the cap.
- MFE protection assumes a protective stop would have filled at the simulated floor after the recorded MFE was reached.
- Estimated KRW PnL scales the recorded KRW PnL by simulated_pct / realized_pct where possible.
- trade_ready and daily-entry simulations use ticker_selection_log rows because live_decisions does not consistently include selection gate metadata.

## Scenario Result

| Scenario | N | W/L | Win | Avg pct | PF | Est. PnL KRW | Delta KRW |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 80 | 28/52 | 35.0% | -0.735% | 0.58 | -84,312 | +0 |
| loss_cap_3_only | 80 | 28/52 | 35.0% | -0.155% | 0.87 | -44,006 | +40,307 |
| loss_cap_2_only | 80 | 28/52 | 35.0% | +0.079% | 1.09 | -18,265 | +66,047 |
| loss_cap_1_5_only | 80 | 28/52 | 35.0% | +0.253% | 1.34 | +1,828 | +86,140 |
| current_code_cap3_floor0_5_at_mfe2 | 80 | 32/48 | 40.0% | -0.052% | 0.95 | -28,303 | +56,009 |
| proposed_cap2_mfe_protection | 80 | 32/43 | 40.0% | +0.405% | 1.54 | +33,105 | +117,417 |
| aggressive_cap1_5_mfe_protection | 80 | 37/43 | 46.2% | +0.581% | 1.96 | +55,004 | +139,316 |

## Market Split

| Scenario | Market | N | W/L | Win | Avg pct | PF | Est. PnL KRW | Delta KRW |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | KR | 44 | 13/31 | 29.5% | -1.225% | 0.45 | -67,933 | +0 |
| baseline | US | 36 | 15/21 | 41.7% | -0.136% | 0.88 | -16,380 | +0 |
| loss_cap_3_only | KR | 44 | 13/31 | 29.5% | -0.356% | 0.74 | -45,410 | +22,523 |
| loss_cap_3_only | US | 36 | 15/21 | 41.7% | +0.091% | 1.10 | +1,404 | +17,784 |
| loss_cap_2_only | KR | 44 | 13/31 | 29.5% | -0.071% | 0.93 | -32,014 | +35,918 |
| loss_cap_2_only | US | 36 | 15/21 | 41.7% | +0.262% | 1.36 | +13,749 | +30,129 |
| loss_cap_1_5_only | KR | 44 | 13/31 | 29.5% | +0.151% | 1.18 | -21,042 | +46,891 |
| loss_cap_1_5_only | US | 36 | 15/21 | 41.7% | +0.378% | 1.61 | +22,870 | +39,250 |
| current_code_cap3_floor0_5_at_mfe2 | KR | 44 | 15/29 | 34.1% | -0.234% | 0.82 | -35,239 | +32,694 |
| current_code_cap3_floor0_5_at_mfe2 | US | 36 | 17/19 | 47.2% | +0.171% | 1.20 | +6,935 | +23,315 |
| proposed_cap2_mfe_protection | KR | 44 | 15/26 | 34.1% | +0.277% | 1.31 | -9,776 | +58,157 |
| proposed_cap2_mfe_protection | US | 36 | 17/17 | 47.2% | +0.561% | 1.96 | +42,880 | +59,260 |
| aggressive_cap1_5_mfe_protection | KR | 44 | 18/26 | 40.9% | +0.504% | 1.72 | +1,557 | +69,490 |
| aggressive_cap1_5_mfe_protection | US | 36 | 19/17 | 52.8% | +0.674% | 2.36 | +53,446 | +69,826 |

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

## Selection Gate Simulation

| Filter | N | W/L | Win | Avg pct | PF |
|---|---:|---:|---:|---:|---:|
| baseline | 33 | 11/22 | 33.3% | -0.473% | 0.66 |
| trade_ready_1 | 23 | 8/15 | 34.8% | -0.196% | 0.86 |
| trade_ready_0 | 10 | 3/7 | 30.0% | -1.109% | 0.24 |
| KR_ready_1 | 14 | 4/10 | 28.6% | -0.827% | 0.47 |
| KR_ready_0 | 8 | 3/5 | 37.5% | -0.904% | 0.33 |
| US_ready_1 | 9 | 4/5 | 44.4% | +0.785% | 1.75 |
| US_ready_0 | 2 | 0/2 | 0.0% | -1.928% | 0.00 |
| block_not_ready | 23 | 8/15 | 34.8% | -0.196% | 0.86 |
| block_kr_momentum | 14 | 5/9 | 35.7% | +0.152% | 1.15 |
| us_only | 11 | 4/7 | 36.4% | +0.292% | 1.24 |
| us_ready_only | 9 | 4/5 | 44.4% | +0.785% | 1.75 |

## Daily Entry Caps

| Rule | Kept | N | W/L | Win | Avg pct | PF |
|---|---:|---:|---:|---:|---:|---:|
| max_total_daily_entries_1 | 11/33 | 11 | 6/5 | 54.5% | +0.112% | 1.08 |
| max_total_daily_entries_2 | 20/33 | 20 | 7/13 | 35.0% | -0.482% | 0.62 |
| max_total_daily_entries_3 | 25/33 | 25 | 9/16 | 36.0% | -0.148% | 0.87 |
| max_market_daily_entries_1 | 17/33 | 17 | 9/8 | 52.9% | +0.517% | 1.42 |
| max_market_daily_entries_2 | 27/33 | 27 | 10/17 | 37.0% | -0.119% | 0.90 |
| max_market_daily_entries_3 | 31/33 | 31 | 11/20 | 35.5% | -0.160% | 0.86 |

## Blocked Opportunity Check

| Reason | N | Ready | Forward 1D Avg | Runup 3D Avg |
|---|---:|---:|---:|---:|
| DAILY_LOSS_LIMIT | 3 | 2 | +4.993% | +13.009% |
| ORDER_UNKNOWN_UNRESOLVED | 1 | 1 | +8.759% | +12.069% |
| PATHB_ORDER_UNKNOWN_SAME_TICKER | 1 | 1 | +7.601% | +13.948% |
| SAME_DAY_REENTRY_AFTER_STOP | 1 | 1 | NA | NA |
| insufficient_cash | 2 | 1 | +4.716% | +16.431% |
| order_rejected | 3 | 1 | +2.941% | +8.005% |
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
