# US Preopen Candidate Replay With News Summary

- Cases: US 2026-06-01 through 2026-06-05
- Input: latest preopen candidate snapshot plus preopen news overlay
- Baseline all-candidate daily close average: -0.9007%
- Total Claude tokens across six prompt runs: 770,278

| prompt_version | promotes | avg/day | daily close avg, empty=cash | ticker weighted close | ticker win % |
|---|---:|---:|---:|---:|---:|
| strict_loss_filter_v1 | 12 | 2.4 | 1.5976 | 2.2438 | 58.33 |
| us_liquid_quality_v4 | 17 | 3.4 | 0.7074 | 0.8138 | 41.18 |
| us_slate_adaptive_v6 | 17 | 3.4 | 0.5329 | 1.0202 | 47.06 |
| us_edge_hunter_v5 | 25 | 5.0 | -0.1124 | -0.1124 | 44.00 |
| market_balanced_v2 | 25 | 5.0 | -1.1594 | -1.1594 | 44.00 |
| market_growth_tape_v3 | 20 | 4.0 | -1.2390 | -1.5487 | 40.00 |

## Best Candidate

`strict_loss_filter_v1` became the best US prompt once preopen news was available.

Per-case promote close:

| date | promotes | promote close | all-candidate close |
|---|---:|---:|---:|
| 2026-06-01 | 3 | 7.1400 | 4.3009 |
| 2026-06-02 | 5 | 1.2700 | -0.1599 |
| 2026-06-03 | 2 | -1.6498 | -2.5647 |
| 2026-06-04 | 2 | 1.2279 | 1.8062 |
| 2026-06-05 | 0 | 0.0000 | -7.8862 |

## Interpretation

News helped most when the prompt was allowed to stay empty. The weak US day on 2026-06-05 was avoided by `strict_loss_filter_v1`, while broader prompts still forced three to five names and took large losses.

The result supports a US prompt direction that requires a concrete catalyst or exceptional tape and permits no-trade days.

