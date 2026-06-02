# Candidate Improvement Simulation

- generated_at: 2026-06-01T14:51:28
- scope: local DB/log simulation only; no broker/API/Claude calls

## Data Coverage

| source | rows | date_min | date_max | by_market |
|---|---:|---|---|---|
| long_backtest | 5766 | 2018-01-02 | 2026-04-23 | {"KR": 4087, "US": 1679} |
| ticker_selection_log | 6679 | 2026-04-07 | 2026-06-01 | {"US": 3769, "KR": 2910} |
| screener_quality | 33300 | 2026-04-28 | 2026-06-01 | {"US": 17518, "KR": 15782} |
| raw_selection_calls | 1126 | 2026-04-22 | 2026-06-01 | {"US": 731, "KR": 395} |
| action_routing_shadow | 6406 | 2026-05-06 | 2026-06-01 | {"US": 3203, "KR": 3203} |
| preopen_state | 2163 | 2026-05-02 | 2026-06-01 | {"KR": 1082, "US": 1081} |

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
- ready forward_1d baseline: n=120 avg=0.9289% pf=1.2999
| scenario | kept n | kept f1 avg | demoted n | demoted f1 avg | demoted runup avg | demoted drawdown avg |
|---|---:|---:|---:|---:|---:|---:|
| watch_trigger_demote_all_at_high | 34 | 0.5511 | 86 | 1.0782 | 17.1165 | -8.46 |
| watch_trigger_demote_extreme_at_high | 67 | 0.5915 | 53 | 1.3554 | 20.385 | -8.9198 |
| watch_trigger_with_fast_lane_proxy | 44 | 0.5913 | 76 | 1.1243 | 17.4867 | -8.1509 |

### US
- ready forward_1d baseline: n=303 avg=2.0724% pf=3.2443
| scenario | kept n | kept f1 avg | demoted n | demoted f1 avg | demoted runup avg | demoted drawdown avg |
|---|---:|---:|---:|---:|---:|---:|
| watch_trigger_demote_all_at_high | 161 | 2.4769 | 142 | 1.6138 | 8.5804 | -3.7014 |
| watch_trigger_demote_extreme_at_high | 180 | 2.2869 | 123 | 1.7585 | 8.9443 | -3.8377 |
| watch_trigger_with_fast_lane_proxy | 175 | 2.347 | 128 | 1.697 | 8.7604 | -3.9008 |

## Prompt Visibility

| market | matched events | avg raw rows | avg actual prompt | avg reported input_true | events missing top30 | avg missing top30 | avg score36 gain |
|---|---:|---:|---:|---:|---:|---:|---:|
| KR | 252 | 61.9127 | 29.9524 | 49.3492 | 252 | 11.6111 | 14.3849 |
| US | 281 | 60.5267 | 27.8932 | 52.573 | 280 | 13.5338 | 15.8399 |

## Routing Shadow

- KR: route_rows=3203 plan_a=215 runtime_overextended=0 selection_join_overextended=41
- US: route_rows=3203 plan_a=541 runtime_overextended=0 selection_join_overextended=93

## Preopen Low Liquidity

- KR: valid=1073 low_liq_ignite60 n=60 avg=11.6834% pf=31.1007 late_reclaim n=16 avg=10.4508%
- US: valid=1050 low_liq_ignite60 n=0 avg=0.0% pf=None late_reclaim n=29 avg=5.1459%

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
