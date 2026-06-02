# Counterfactual Path Review

Generated: 2026-06-01T14:53:02
Rows: 21870
Candidates: 2187
3+ path candidates: 100.0%
Trigger eval rate: 85.2172%

## Status

| Status | Rows |
|---|---:|
| BASELINE_NO_TRADE | 2187 |
| CLOSE_OUTCOME_FILLED | 4133 |
| DATA_MISSING | 10194 |
| PENDING | 3233 |
| PRICE_UNAVAILABLE | 589 |
| TRIGGERED | 1534 |

## Metadata Quality

| Metadata quality | Rows |
|---|---:|
| (blank) | 1506 |
| backfill_diagnostic | 3594 |
| runtime_authoritative | 16770 |

## Label Source

| Label source | Rows |
|---|---:|
| (blank) | 11156 |
| counterfactual_outcome_updater | 6581 |
| virtual_immediate_shadow | 4133 |

## By Path

| Path | Rows | Missing | 30m N | 30m Avg | 30m PF | 60m N | 60m Avg | 60m PF | Close N | Close Avg | Close PF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| KR\|immediate | 2187 | 0 | 725 | -1.073133 | 0.452235 | 694 | -1.566626 | 0.391714 | 1514 | -1.861238 | 0.420332 |
| KR\|no_entry | 2187 | 0 | 0 | 0.0 | None | 0 | 0.0 | None | 0 | 0.0 | None |
| KR\|or_break | 2187 | 1384 | 88 | -1.894614 | 0.149189 | 88 | -2.673433 | 0.093081 | 130 | -2.90317 | 0.258975 |
| KR\|orderbook_support | 2187 | 2159 | 0 | 0.0 | None | 0 | 0.0 | None | 0 | 0.0 | None |
| KR\|pullback_reclaim | 2187 | 1310 | 136 | -2.265571 | 0.254781 | 136 | -3.107863 | 0.183207 | 204 | -3.832816 | 0.252068 |
| KR\|vi_safe_reclaim | 2187 | 2159 | 0 | 0.0 | None | 0 | 0.0 | None | 0 | 0.0 | None |
| KR\|volume_surge | 2187 | 885 | 469 | -1.607628 | 0.360979 | 469 | -2.189977 | 0.312689 | 629 | -3.472343 | 0.285343 |
| KR\|vwap_reclaim | 2187 | 1277 | 150 | -2.407368 | 0.239235 | 150 | -3.180073 | 0.16626 | 237 | -4.160512 | 0.228948 |
| KR\|wait_30m | 2187 | 510 | 694 | -0.458755 | 0.622234 | 649 | -0.951476 | 0.485357 | 725 | -2.048547 | 0.375149 |
| KR\|wait_60m | 2187 | 510 | 649 | -0.472129 | 0.556691 | 619 | -0.887151 | 0.46571 | 694 | -1.671302 | 0.421653 |
