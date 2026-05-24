# Prompt Overlay Shadow Analysis

- generated_at: 2026-05-24T10:19:06
- db_path: data\audit\candidate_audit.db
- call_count: 262
- mode_counts: {'current_only': 248, 'shadow': 14}
- overlay_day_count: 3
- plan_a_coverage_day_count: 7
- plan_a_zero_cycles: 160
- plan_b_fallback_count: 0

| pool | n | avg | median | win_rate_pct | pf |
|---|---:|---:|---:|---:|---:|
| current | 700 | 0.8601 | 0.2489 | 57.71 | 2.2214 |
| shadow_overlay | 490 | 1.0859 | 0.294 | 61.22 | 2.9096 |
| overlay_triggered | 8 | 4.1267 | 1.4668 | 75.0 | 20.808 |

- top_day: 2026-05-20
- top_day_contribution_pct: 90.54
- gate_pass: False

| gate | pass |
|---|---:|
| shadow_days_min_10 | True |
| overlay_days_min_4 | False |
| overlay_triggered_pf_gt_1 | True |
| top_day_contribution_lt_40 | False |
| plan_b_fallback_zero | True |
