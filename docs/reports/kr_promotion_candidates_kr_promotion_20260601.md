# KR Promotion Candidate Audit

Generated: 2026-06-01T15:21:04
Filters: {'market': 'KR', 'runtime_mode': 'live', 'start_date': 'ALL', 'end_date': 'ALL'}

## Decision

- operator_action: `do_not_enable_live`
- should_enable_live: `False`
- verdict_counts: `{'BLOCK': 39, 'NO_DATA': 9, 'PROBE_READY': 2, 'SHADOW_ONLY': 4}`

## Live Ready

| verdict | name | n | days | avg_pct | pf | top_day_share | reasons |
|---|---|---:|---:|---:|---:|---:|---|
|  | none |  |  |  |  |  |  |

## Probe Ready

| verdict | name | n | days | avg_pct | pf | top_day_share | reasons |
|---|---|---:|---:|---:|---:|---:|---|
| PROBE_READY | preopen:d60_ret60_ge_3_top10\|fwd_to_120 | 170 | 18 | 0.292202 | 1.192272 | 0.117472 | passes probe thresholds but not live thresholds; latest-hold return is negative; any probe must be short-hold only |
| PROBE_READY | preopen:d60_ret60_ge_8_top10\|fwd_to_120 | 109 | 18 | 0.286409 | 1.185943 | 0.164814 | passes probe thresholds but not live thresholds; latest-hold return is negative; any probe must be short-hold only |

## Blocked

| verdict | name | n | days | avg_pct | pf | top_day_share | reasons |
|---|---|---:|---:|---:|---:|---:|---|
| BLOCK | preopen:d60_ret60_ge_8_top5\|fwd_to_120 | 80 | 18 | -0.273102 | 0.828677 | 0.201261 | negative edge avg=-0.2731 pf=0.8287 |
| BLOCK | preopen:d5_ret5_ge_1_top10\|fwd_to_120 | 179 | 18 | -0.366776 | 0.901428 | 0.13517 | negative edge avg=-0.3668 pf=0.9014 |
| BLOCK | counterfactual:path=wait_30m\|horizon=30m | 694 | 4 | -0.458755 | 0.622234 | 0.504507 | negative edge avg=-0.4588 pf=0.6222 |
| BLOCK | counterfactual:path=wait_60m\|horizon=30m | 649 | 4 | -0.472129 | 0.556691 | 0.483856 | negative edge avg=-0.4721 pf=0.5567 |
| BLOCK | audit:evidence=BUY_READY | 122 | 7 | -0.650966 | 0.657131 | 0.214941 | negative edge avg=-0.651 pf=0.6571 |
| BLOCK | closed:strategy=gap_pullback | 20 | 8 | -0.693316 | 0.614172 | 0.485448 | negative edge avg=-0.6933 pf=0.6142 |
| BLOCK | closed:strategy=mean_reversion | 1 | 1 | -0.707071 | 0.0 | None | negative edge avg=-0.7071 pf=0.0 |
| BLOCK | counterfactual:path=wait_60m\|horizon=60m | 619 | 4 | -0.887151 | 0.46571 | 0.54499 | negative edge avg=-0.8872 pf=0.4657 |
| BLOCK | counterfactual:path=wait_30m\|horizon=60m | 649 | 4 | -0.951476 | 0.485357 | 0.520206 | negative edge avg=-0.9515 pf=0.4854 |
| BLOCK | closed:route=path_b | 33 | 8 | -0.957929 | 0.516471 | 0.317344 | negative edge avg=-0.9579 pf=0.5165 |
| BLOCK | closed:route=unknown | 5 | 3 | -1.041693 | 0.196892 | 1.0 | negative edge avg=-1.0417 pf=0.1969 |
| BLOCK | closed:KR_live_overall | 43 | 12 | -1.054578 | 0.437844 | 0.303383 | negative edge avg=-1.0546 pf=0.4378 |
| BLOCK | counterfactual:path=immediate\|horizon=30m | 725 | 4 | -1.073133 | 0.452235 | 0.474099 | negative edge avg=-1.0731 pf=0.4522 |
| BLOCK | preopen:d30_ret30_ge_1_top10\|fwd_to_120 | 179 | 18 | -1.161656 | 0.578143 | 0.117239 | negative edge avg=-1.1617 pf=0.5781 |
| BLOCK | preopen:d30_ret30_ge_3_top10\|fwd_to_120 | 169 | 18 | -1.228503 | 0.559275 | 0.116499 | negative edge avg=-1.2285 pf=0.5593 |
| BLOCK | closed:strategy=opening_range_pullback | 6 | 4 | -1.319847 | 0.035584 | 1.0 | negative edge avg=-1.3198 pf=0.0356 |
| BLOCK | counterfactual:path=immediate\|horizon=60m | 694 | 4 | -1.566626 | 0.391714 | 0.519411 | negative edge avg=-1.5666 pf=0.3917 |
| BLOCK | counterfactual:path=volume_surge\|horizon=30m | 469 | 4 | -1.607628 | 0.360979 | 0.538075 | negative edge avg=-1.6076 pf=0.361 |
| BLOCK | audit:claude_action=BUY_READY | 27 | 8 | -1.636702 | 0.285735 | 0.432049 | negative edge avg=-1.6367 pf=0.2857 |
| BLOCK | counterfactual:path=wait_60m\|horizon=close | 694 | 4 | -1.671302 | 0.421653 | 0.569081 | negative edge avg=-1.6713 pf=0.4217 |

## Notes

- This is local-data-only analysis. It does not call broker APIs or Claude.
- Promotion labels are decision aids; live config changes still require operator review.
