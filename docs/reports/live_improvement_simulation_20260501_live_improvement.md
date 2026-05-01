# Live Improvement Simulation

Generated: 2026-05-01T13:14:03

## Basis

- Closed trades: 55 from `state/live_decisions.jsonl`.
- Selection closed trades: 20 from `data/ticker_selection_log.db`.
- Blocked signals: 17 from `data/ticker_selection_log.db`.
- No Claude calls and no broker/API calls were made.

Assumptions:
- No intraday tick replay was available, so loss caps are simulated by clipping realized return at the cap.
- MFE protection assumes a protective stop would have filled at the simulated floor after the recorded MFE was reached.
- Estimated KRW PnL scales the recorded KRW PnL by simulated_pct / realized_pct where possible.
- trade_ready and daily-entry simulations use ticker_selection_log rows because live_decisions does not consistently include selection gate metadata.

## Scenario Result

| Scenario | N | W/L | Win | Avg pct | PF | Est. PnL KRW | Delta KRW |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 55 | 20/35 | 36.4% | -0.892% | 0.51 | -50,116 | -0 |
| loss_cap_3_only | 55 | 20/35 | 36.4% | -0.178% | 0.84 | -25,343 | +24,773 |
| loss_cap_2_only | 55 | 20/35 | 36.4% | +0.041% | 1.05 | -12,498 | +37,618 |
| loss_cap_1_5_only | 55 | 20/35 | 36.4% | +0.201% | 1.28 | -1,787 | +48,329 |
| current_code_cap3_floor0_5_at_mfe2 | 55 | 20/35 | 36.4% | -0.169% | 0.84 | -23,969 | +26,146 |
| proposed_cap2_mfe_protection | 55 | 20/32 | 36.4% | +0.188% | 1.24 | +8,660 | +58,776 |
| aggressive_cap1_5_mfe_protection | 55 | 23/32 | 41.8% | +0.347% | 1.55 | +21,047 | +71,163 |

## Market Split

| Scenario | Market | N | W/L | Win | Avg pct | PF | Est. PnL KRW | Delta KRW |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | KR | 30 | 8/22 | 26.7% | -1.583% | 0.40 | -50,248 | -0 |
| baseline | US | 25 | 12/13 | 48.0% | -0.063% | 0.92 | +133 | -0 |
| loss_cap_3_only | KR | 30 | 8/22 | 26.7% | -0.337% | 0.76 | -29,421 | +20,827 |
| loss_cap_3_only | US | 25 | 12/13 | 48.0% | +0.013% | 1.02 | +4,078 | +3,946 |
| loss_cap_2_only | KR | 30 | 8/22 | 26.7% | -0.036% | 0.97 | -22,779 | +27,470 |
| loss_cap_2_only | US | 25 | 12/13 | 48.0% | +0.133% | 1.21 | +10,281 | +10,149 |
| loss_cap_1_5_only | KR | 30 | 8/22 | 26.7% | +0.185% | 1.22 | -17,270 | +32,978 |
| loss_cap_1_5_only | US | 25 | 12/13 | 48.0% | +0.221% | 1.41 | +15,483 | +15,351 |
| current_code_cap3_floor0_5_at_mfe2 | KR | 30 | 8/22 | 26.7% | -0.337% | 0.76 | -29,421 | +20,827 |
| current_code_cap3_floor0_5_at_mfe2 | US | 25 | 12/13 | 48.0% | +0.032% | 1.04 | +5,452 | +5,320 |
| proposed_cap2_mfe_protection | KR | 30 | 8/19 | 26.7% | +0.136% | 1.15 | -17,007 | +33,241 |
| proposed_cap2_mfe_protection | US | 25 | 12/13 | 48.0% | +0.251% | 1.40 | +25,667 | +25,535 |
| aggressive_cap1_5_mfe_protection | KR | 30 | 11/19 | 36.7% | +0.339% | 1.47 | -12,121 | +38,127 |
| aggressive_cap1_5_mfe_protection | US | 25 | 12/13 | 48.0% | +0.356% | 1.67 | +33,169 | +33,036 |

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

## Selection Gate Simulation

| Filter | N | W/L | Win | Avg pct | PF |
|---|---:|---:|---:|---:|---:|
| baseline | 20 | 6/14 | 30.0% | -1.132% | 0.31 |
| trade_ready_1 | 13 | 3/10 | 23.1% | -1.168% | 0.30 |
| trade_ready_0 | 7 | 3/4 | 42.9% | -1.063% | 0.32 |
| KR_ready_1 | 8 | 1/7 | 12.5% | -1.963% | 0.02 |
| KR_ready_0 | 6 | 3/3 | 50.0% | -0.616% | 0.49 |
| US_ready_1 | 5 | 2/3 | 40.0% | +0.103% | 1.09 |
| US_ready_0 | 1 | 0/1 | 0.0% | -3.746% | 0.00 |
| block_not_ready | 13 | 3/10 | 23.1% | -1.168% | 0.30 |
| block_kr_momentum | 8 | 3/5 | 37.5% | -0.499% | 0.61 |
| us_only | 6 | 2/4 | 33.3% | -0.539% | 0.66 |
| us_ready_only | 5 | 2/3 | 40.0% | +0.103% | 1.09 |

## Daily Entry Caps

| Rule | Kept | N | W/L | Win | Avg pct | PF |
|---|---:|---:|---:|---:|---:|---:|
| max_total_daily_entries_1 | 6/20 | 6 | 3/3 | 50.0% | -0.798% | 0.45 |
| max_total_daily_entries_2 | 12/20 | 12 | 4/8 | 33.3% | -1.044% | 0.24 |
| max_total_daily_entries_3 | 15/20 | 15 | 4/11 | 26.7% | -0.977% | 0.21 |
| max_market_daily_entries_1 | 10/20 | 10 | 5/5 | 50.0% | -0.348% | 0.74 |
| max_market_daily_entries_2 | 15/20 | 15 | 6/9 | 40.0% | -0.659% | 0.51 |
| max_market_daily_entries_3 | 18/20 | 18 | 6/12 | 33.3% | -0.667% | 0.46 |

## Blocked Opportunity Check

| Reason | N | Ready | Forward 1D Avg | Runup 3D Avg |
|---|---:|---:|---:|---:|
| DAILY_LOSS_LIMIT | 1 | 0 | +4.993% | NA |
| ORDER_UNKNOWN_UNRESOLVED | 1 | 1 | NA | NA |
| PATHB_ORDER_UNKNOWN_SAME_TICKER | 1 | 1 | +7.601% | NA |
| insufficient_cash | 2 | 1 | NA | NA |
| order_rejected | 3 | 1 | +2.605% | +6.269% |
| order_size_too_small | 3 | 2 | +9.678% | +26.011% |
| qty_zero | 6 | 3 | +4.778% | +7.497% |

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
