# Candidate Improvement Simulation

- generated_at: 2026-05-28T01:48:41
- scope: local DB/log simulation only; no broker/API/Claude calls

## Data Coverage

| source | rows | date_min | date_max | by_market |
|---|---:|---|---|---|
| long_backtest | 5766 | 2018-01-02 | 2026-04-23 | {"KR": 4087, "US": 1679} |
| ticker_selection_log | 5601 | 2026-04-07 | 2026-05-27 | {"US": 3353, "KR": 2248} |
| screener_quality | 28223 | 2026-04-28 | 2026-05-28 | {"US": 15433, "KR": 12790} |
| raw_selection_calls | 1033 | 2026-04-22 | 2026-05-28 | {"US": 696, "KR": 337} |
| action_routing_shadow | 5210 | 2026-05-06 | 2026-05-27 | {"US": 2725, "KR": 2485} |
| preopen_state | 1863 | 2026-05-02 | 2026-05-27 | {"US": 961, "KR": 902} |

## Long Backtest Gap Guard

### KR
- baseline: n=4087 avg=-0.6425% win=32.98% pf=0.7219
| scenario | kept n | kept avg | kept pf | demoted n | demoted avg | demoted pf |
|---|---:|---:|---:|---:|---:|---:|
| gap_guard_3_demote_all | 3611 | -0.6019 | 0.7293 | 476 | -0.9503 | 0.68 |
| gap_guard_3_fast_lane_momentum_gap | 4045 | -0.6414 | 0.7232 | 42 | -0.7433 | 0.5373 |
| gap_guard_5_demote_all | 3815 | -0.6125 | 0.7285 | 272 | -1.0634 | 0.6533 |
| gap_guard_5_fast_lane_momentum_gap | 4069 | -0.642 | 0.7224 | 18 | -0.7433 | 0.5373 |
| gap_guard_8_demote_all | 3934 | -0.6269 | 0.725 | 153 | -1.0421 | 0.6637 |
| gap_guard_8_fast_lane_momentum_gap | 4081 | -0.6423 | 0.7221 | 6 | -0.7433 | 0.5373 |

### US
- baseline: n=1679 avg=-0.2084% win=34.9% pf=0.8138
| scenario | kept n | kept avg | kept pf | demoted n | demoted avg | demoted pf |
|---|---:|---:|---:|---:|---:|---:|
| gap_guard_2_demote_all | 1539 | -0.1973 | 0.823 | 140 | -0.3303 | 0.7175 |
| gap_guard_2_fast_lane_momentum_gap | 1590 | -0.2031 | 0.8164 | 89 | -0.3022 | 0.7753 |
| gap_guard_4_demote_all | 1646 | -0.2011 | 0.8199 | 33 | -0.5697 | 0.5358 |
| gap_guard_4_fast_lane_momentum_gap | 1661 | -0.2029 | 0.8179 | 18 | -0.7111 | 0.5311 |
| gap_guard_6_demote_all | 1656 | -0.199 | 0.8217 | 23 | -0.8826 | 0.343 |
| gap_guard_6_fast_lane_momentum_gap | 1667 | -0.2007 | 0.8199 | 12 | -1.2667 | 0.2762 |

## Recent WATCH_TRIGGER Proxy

### KR
- ready forward_1d baseline: n=114 avg=0.8566% pf=1.2853
| scenario | kept n | kept f1 avg | demoted n | demoted f1 avg | demoted runup avg | demoted drawdown avg |
|---|---:|---:|---:|---:|---:|---:|
| watch_trigger_demote_all_at_high | 28 | 0.1758 | 86 | 1.0782 | 17.1165 | -8.46 |
| watch_trigger_demote_extreme_at_high | 61 | 0.4232 | 53 | 1.3554 | 20.385 | -8.9198 |
| watch_trigger_with_fast_lane_proxy | 38 | 0.3211 | 76 | 1.1243 | 17.4867 | -8.1509 |

### US
- ready forward_1d baseline: n=240 avg=1.7796% pf=2.7553
| scenario | kept n | kept f1 avg | demoted n | demoted f1 avg | demoted runup avg | demoted drawdown avg |
|---|---:|---:|---:|---:|---:|---:|
| watch_trigger_demote_all_at_high | 98 | 2.0198 | 142 | 1.6138 | 8.5804 | -3.7014 |
| watch_trigger_demote_extreme_at_high | 117 | 1.8018 | 123 | 1.7585 | 8.9443 | -3.8377 |
| watch_trigger_with_fast_lane_proxy | 112 | 1.8739 | 128 | 1.697 | 8.7604 | -3.9008 |

## Prompt Visibility

| market | matched events | avg raw rows | avg actual prompt | avg reported input_true | events missing top30 | avg missing top30 | avg score36 gain |
|---|---:|---:|---:|---:|---:|---:|---:|
| KR | 205 | 61.5122 | 29.4829 | 53.4537 | 205 | 11.0683 | 14.0293 |
| US | 252 | 59.2183 | 27.0754 | 54.5952 | 251 | 13.3452 | 15.631 |

## Routing Shadow

- KR: route_rows=2485 plan_a=188 runtime_overextended=0 selection_join_overextended=41
- US: route_rows=2725 plan_a=438 runtime_overextended=0 selection_join_overextended=93

## Preopen Low Liquidity

- KR: valid=893 low_liq_ignite60 n=54 avg=12.7902% pf=143.9157 late_reclaim n=13 avg=12.5734%
- US: valid=930 low_liq_ignite60 n=0 avg=0.0% pf=None late_reclaim n=27 avg=5.3583%

## Interpretation

- observability: Prompt visibility must be fixed first. Existing screener_quality can overstate input_to_claude when select_tickers trims internally. Action: Persist actual prompt tickers and curation deferred_reason before changing live behavior.
- WATCH_TRIGGER: Use routing-level demotion first. It preserves Claude output while preventing immediate high-zone execution. Action: Shadow BUY_READY/PROBE_READY -> WATCH_TRIGGER for at_high/near_high until OR/VWAP/volume confirmation exists.
- candidate_pool: Cap expansion is a visibility change, not a buy permission change. Action: Raise KR overextended cap gradually and track score-ranked top30/top36 misses before enabling any extra execution.
- low_liq: Low-liquidity ignition should be a separate small-probe path after confirmation, not a relaxation of Claude VETO. Action: Keep low_liq_ignite60 in shadow until sample size grows.

## Limits

- 2018 long backtest has daily entry_gap/returns, not Claude prompt or intraday VWAP/OR features.
- WATCH_TRIGGER simulation on ticker_selection_log uses from_high_bucket/change/liquidity proxies from 2026-04-07..2026-05-08.
- Prompt visibility simulation uses raw_calls only where a raw Claude selection prompt exists.
- low_liq_ignite60 uses preopen sampled outcome data and is an entry-offset approximation, not tick-level fill simulation.
