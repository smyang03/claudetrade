# KR Promotion Candidate Audit

Generated: 2026-07-23T14:16:19
Filters: {'market': 'KR', 'runtime_mode': 'live', 'start_date': 'ALL', 'end_date': 'ALL'}

## Decision

- operator_action: `do_not_enable_live`
- should_enable_live: `False`
- verdict_counts: `{'BLOCK': 41, 'NO_DATA': 9, 'SHADOW_ONLY': 4}`

## Live Ready

| verdict | name | n | days | avg_pct | pf | top_day_share | reasons |
|---|---|---:|---:|---:|---:|---:|---|
|  | none |  |  |  |  |  |  |

## Probe Ready

| verdict | name | n | days | avg_pct | pf | top_day_share | reasons |
|---|---|---:|---:|---:|---:|---:|---|
|  | none |  |  |  |  |  |  |

## Live Filled Micro-Probes

| verdict | name | n | days | avg_pct | pf | top_day_share | reasons |
|---|---|---:|---:|---:|---:|---:|---|
|  | none |  |  |  |  |  |  |

## Blocked

| verdict | name | n | days | avg_pct | pf | top_day_share | reasons |
|---|---|---:|---:|---:|---:|---:|---|
| BLOCK | counterfactual:path=wait_30m\|horizon=30m | 13684 | 40 | -0.204035 | 0.780226 | 0.05867 | negative edge avg=-0.204 pf=0.7802 |
| BLOCK | counterfactual:path=wait_60m\|horizon=30m | 12689 | 40 | -0.217869 | 0.748821 | 0.062038 | negative edge avg=-0.2179 pf=0.7488 |
| BLOCK | preopen:d60_ret60_ge_3_top10\|fwd_to_120 | 507 | 53 | -0.261455 | 0.854791 | 0.061845 | negative edge avg=-0.2615 pf=0.8548 |
| BLOCK | counterfactual:path=immediate\|horizon=30m | 14713 | 40 | -0.293727 | 0.752578 | 0.06362 | negative edge avg=-0.2937 pf=0.7526 |
| BLOCK | preopen:d60_ret60_ge_8_top10\|fwd_to_120 | 338 | 53 | -0.320611 | 0.826323 | 0.078804 | negative edge avg=-0.3206 pf=0.8263 |
| BLOCK | closed:route=path_b | 52 | 17 | -0.355983 | 0.78241 | 0.387674 | negative edge avg=-0.356 pf=0.7824 |
| BLOCK | counterfactual:path=volume_surge\|horizon=30m | 10606 | 40 | -0.4068 | 0.697173 | 0.059519 | negative edge avg=-0.4068 pf=0.6972 |
| BLOCK | counterfactual:path=wait_30m\|horizon=60m | 12689 | 40 | -0.409426 | 0.694615 | 0.060447 | negative edge avg=-0.4094 pf=0.6946 |
| BLOCK | preopen:d60_ret60_ge_8_top5\|fwd_to_120 | 242 | 53 | -0.41229 | 0.770419 | 0.069458 | negative edge avg=-0.4123 pf=0.7704 |
| BLOCK | preopen:d60_ret60_ge_5_top10\|fwd_to_120 | 449 | 53 | -0.448674 | 0.762493 | 0.074618 | negative edge avg=-0.4487 pf=0.7625 |
| BLOCK | counterfactual:path=wait_60m\|horizon=60m | 11613 | 40 | -0.449765 | 0.654053 | 0.062611 | negative edge avg=-0.4498 pf=0.6541 |
| BLOCK | counterfactual:path=or_break\|horizon=30m | 2936 | 39 | -0.469723 | 0.605716 | 0.089092 | negative edge avg=-0.4697 pf=0.6057 |
| BLOCK | audit:evidence=BUY_READY\|strategy=momentum | 222 | 14 | -0.483404 | 0.602384 | 0.387458 | negative edge avg=-0.4834 pf=0.6024 |
| BLOCK | counterfactual:path=immediate\|horizon=60m | 13607 | 40 | -0.498093 | 0.690042 | 0.06008 | negative edge avg=-0.4981 pf=0.69 |
| BLOCK | counterfactual:path=vwap_reclaim\|horizon=30m | 4671 | 39 | -0.533049 | 0.589263 | 0.061068 | negative edge avg=-0.533 pf=0.5893 |
| BLOCK | closed:KR_live_overall | 62 | 21 | -0.553973 | 0.662717 | 0.382367 | negative edge avg=-0.554 pf=0.6627 |
| BLOCK | counterfactual:path=pullback_reclaim\|horizon=30m | 3906 | 39 | -0.559903 | 0.608686 | 0.059036 | negative edge avg=-0.5599 pf=0.6087 |
| BLOCK | counterfactual:path=volume_surge\|horizon=60m | 10072 | 40 | -0.656946 | 0.63784 | 0.055993 | negative edge avg=-0.6569 pf=0.6378 |
| BLOCK | audit:claude_action=BUY_READY | 78 | 17 | -0.725114 | 0.571487 | 0.389988 | negative edge avg=-0.7251 pf=0.5715 |
| BLOCK | counterfactual:path=or_break\|horizon=60m | 2720 | 39 | -0.778932 | 0.527913 | 0.099483 | negative edge avg=-0.7789 pf=0.5279 |

## Notes

- This is local-data-only analysis. It does not call broker APIs or Claude.
- Promotion labels are decision aids; live config changes still require operator review.
