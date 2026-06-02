# KR Confirmation Gate Outcome Review

Generated: 2026-06-01T14:53:41
Market/runtime: KR/live
Window: (all) ~ (all)
Rows: 398
Decision note: `hold_parameter_change: 60m labels are too sparse to justify WATCH_TRIGGER/KR confirmation demotion changes.`

## Interpretation

- This is a read-only audit DB review. It does not change live config, PathB gates, order sizing, or state files.
- `demoted_by_confirmation` means the route itself demoted a KR executable candidate to WATCH for a KR confirmation reason.
- `demoted_by_evidence_ceiling_with_confirmation_pending` means evidence ceiling blocked first, while confirmation was also pending.
- `from_high_proxy` uses `from_high_pct >= -1.5` because historical audit sign conventions are mixed; it is not an exact `at_high` bucket.

## Warnings

- `kept_executable_ret60_label_n_below_30`
- `demoted_by_confirmation_ret60_label_n_below_30`

## Group Summary

| Group | Rows | 30m N | 30m Avg | 60m N | 60m Avg | 60m Median | 60m Win% | MFE60 Avg | MAE60 Avg |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| demoted_by_confirmation | 66 | 22 | -0.782693 | 25 | -0.964571 | -1.005414 | 28.0 | 0.710109 | -1.89338 |
| demoted_by_evidence_ceiling_confirmed_or_unknown | 9 | 1 | -0.981997 | 0 | None | None | None | None | None |
| demoted_by_evidence_ceiling_with_confirmation_pending | 24 | 2 | -2.824649 | 1 | -2.247191 | -2.247191 | 0.0 | -1.605136 | -4.173355 |
| demoted_by_negative_pullback_context | 70 | 10 | 0.083655 | 11 | -2.33499 | -1.925722 | 27.2727 | 0.880567 | -4.198765 |
| demoted_other_watch | 57 | 4 | -1.286474 | 9 | 0.575415 | 0.956284 | 66.6667 | 1.924621 | -1.163214 |
| hard_block_after_ready_action | 38 | 3 | 0.05892 | 5 | -2.538421 | -1.570681 | 20.0 | -0.423056 | -2.86026 |
| kept_executable | 134 | 25 | -2.890272 | 23 | -2.94233 | -2.592166 | 21.7391 | 0.224353 | -3.787302 |

## Reason Summary

| Group / Reason | Rows | 60m N | 60m Avg | MFE60 Avg | MAE60 Avg |
|---|---:|---:|---:|---:|---:|
| demoted_by_confirmation\|kr_data_quality_not_confirmed | 47 | 15 | -0.01795 | 0.931665 | -0.964135 |
| demoted_by_confirmation\|kr_fast_trigger_not_confirmed | 17 | 8 | -1.353416 | -0.460145 | -2.303276 |
| demoted_by_confirmation\|kr_overextended_not_confirmed | 2 | 2 | -6.50885 | 3.729456 | -7.223135 |
| demoted_by_evidence_ceiling_confirmed_or_unknown\|evidence_action_ceiling | 9 | 0 | None | None | None |
| demoted_by_evidence_ceiling_with_confirmation_pending\|evidence_action_ceiling | 23 | 1 | -2.247191 | -1.605136 | -4.173355 |
| demoted_by_evidence_ceiling_with_confirmation_pending\|kr_late_fresh_buy_demoted_to_probe | 1 | 0 | None | None | None |
| demoted_by_negative_pullback_context\|negative_pullback_context | 70 | 11 | -2.33499 | 0.880567 | -4.198765 |
| demoted_other_watch\|data_quality | 3 | 0 | None | None | None |
| demoted_other_watch\|kr_late_entry_closed | 22 | 0 | None | None | None |
| demoted_other_watch\|kr_late_replacement_watch_only | 8 | 2 | 5.009964 | 5.009964 | -0.109663 |
| demoted_other_watch\|kr_stale_late_entry_watch_only | 21 | 5 | 0.951616 | 0.951616 | 0.170481 |
| demoted_other_watch\|missing_pullback_target | 1 | 1 | -10.555556 | 1.587302 | -10.555556 |
| demoted_other_watch\|soft_gate_override_failed | 2 | 1 | 0.956284 | 0.956284 | -0.546448 |
| hard_block_after_ready_action\|SAME_DAY_REENTRY_AFTER_STOP | 11 | 0 | None | None | None |
| hard_block_after_ready_action\|failed_ready | 2 | 0 | None | None | None |
| hard_block_after_ready_action\|loss_cap_exited | 2 | 0 | None | None | None |
| hard_block_after_ready_action\|negative_pullback_context | 2 | 1 | 1.268116 | 3.985507 | 1.268116 |
| hard_block_after_ready_action\|ready_degraded | 7 | 2 | -5.668453 | -2.420194 | -6.473051 |
| hard_block_after_ready_action\|same_day_reentry_blocked | 2 | 0 | None | None | None |
| hard_block_after_ready_action\|same_day_stopped | 11 | 2 | -1.311656 | -0.630201 | -1.311656 |
| hard_block_after_ready_action\|trail_stop_exited | 1 | 0 | None | None | None |
| kept_executable\|buy_ready | 49 | 12 | -2.992343 | 0.567703 | -3.791933 |
| kept_executable\|evidence_action_ceiling | 1 | 0 | None | None | None |
| kept_executable\|probe_ready | 36 | 6 | -4.122818 | -0.876493 | -4.782423 |
| kept_executable\|pullback_wait | 48 | 5 | -1.405713 | 0.721328 | -2.582042 |

## From High Proxy

| Group / Proxy | Rows | 60m N | 60m Avg | MFE60 Avg | MAE60 Avg |
|---|---:|---:|---:|---:|---:|
| demoted_by_confirmation\|below_high_proxy | 10 | 2 | 2.821638 | 3.039384 | 0.955966 |
| demoted_by_confirmation\|near_or_at_high_proxy | 55 | 23 | -1.293807 | 0.507563 | -2.141149 |
| demoted_by_confirmation\|unknown | 1 | 0 | None | None | None |
| demoted_by_evidence_ceiling_confirmed_or_unknown\|below_high_proxy | 1 | 0 | None | None | None |
| demoted_by_evidence_ceiling_confirmed_or_unknown\|near_or_at_high_proxy | 8 | 0 | None | None | None |
| demoted_by_evidence_ceiling_with_confirmation_pending\|below_high_proxy | 3 | 0 | None | None | None |
| demoted_by_evidence_ceiling_with_confirmation_pending\|near_or_at_high_proxy | 16 | 1 | -2.247191 | -1.605136 | -4.173355 |
| demoted_by_evidence_ceiling_with_confirmation_pending\|unknown | 5 | 0 | None | None | None |
| demoted_by_negative_pullback_context\|below_high_proxy | 17 | 6 | 0.081235 | 2.729146 | -1.221352 |
| demoted_by_negative_pullback_context\|near_or_at_high_proxy | 16 | 5 | -5.234461 | -1.337728 | -7.771662 |
| demoted_by_negative_pullback_context\|unknown | 37 | 0 | None | None | None |
| demoted_other_watch\|near_or_at_high_proxy | 13 | 8 | 1.966786 | 1.966786 | 0.010829 |
| demoted_other_watch\|unknown | 44 | 1 | -10.555556 | 1.587302 | -10.555556 |
| hard_block_after_ready_action\|near_or_at_high_proxy | 9 | 1 | -3.448276 | -3.448276 | -5.057471 |
| hard_block_after_ready_action\|unknown | 29 | 4 | -2.310957 | 0.333248 | -2.310957 |
| kept_executable\|below_high_proxy | 6 | 1 | 5.932203 | 10.59322 | 0.282486 |
| kept_executable\|near_or_at_high_proxy | 19 | 10 | -3.732515 | -0.078051 | -4.198942 |
| kept_executable\|unknown | 109 | 12 | -3.023387 | -0.387716 | -3.783417 |

## By Date

| Date | Group counts |
|---|---|
| 2026-05-07 | demoted_other_watch=4, kept_executable=33 |
| 2026-05-08 | demoted_by_negative_pullback_context=2, hard_block_after_ready_action=13, kept_executable=27 |
| 2026-05-11 | demoted_by_negative_pullback_context=6, demoted_other_watch=7, hard_block_after_ready_action=6, kept_executable=18 |
| 2026-05-12 | demoted_by_negative_pullback_context=12, demoted_other_watch=8, hard_block_after_ready_action=6, kept_executable=12 |
| 2026-05-13 | demoted_by_negative_pullback_context=3, demoted_other_watch=6, hard_block_after_ready_action=3, kept_executable=1 |
| 2026-05-14 | demoted_by_evidence_ceiling_with_confirmation_pending=3, demoted_by_negative_pullback_context=3, demoted_other_watch=6, kept_executable=5 |
| 2026-05-15 | demoted_by_evidence_ceiling_with_confirmation_pending=2, demoted_by_negative_pullback_context=4, demoted_other_watch=4, hard_block_after_ready_action=1, kept_executable=3 |
| 2026-05-18 | demoted_by_confirmation=1, demoted_by_negative_pullback_context=7, demoted_other_watch=9, kept_executable=10 |
| 2026-05-20 | demoted_by_confirmation=3 |
| 2026-05-21 | demoted_by_confirmation=6, demoted_other_watch=12 |
| 2026-05-22 | demoted_by_confirmation=24, demoted_by_evidence_ceiling_with_confirmation_pending=3, demoted_other_watch=1 |
| 2026-05-26 | demoted_by_confirmation=13, demoted_by_evidence_ceiling_confirmed_or_unknown=2, demoted_by_evidence_ceiling_with_confirmation_pending=5, demoted_by_negative_pullback_context=3, hard_block_after_ready_action=1, kept_executable=7 |
| 2026-05-27 | demoted_by_confirmation=16, demoted_by_evidence_ceiling_with_confirmation_pending=4, demoted_by_negative_pullback_context=1, hard_block_after_ready_action=7, kept_executable=7 |
| 2026-05-28 | demoted_by_confirmation=1, demoted_by_evidence_ceiling_confirmed_or_unknown=2, demoted_by_evidence_ceiling_with_confirmation_pending=1, demoted_by_negative_pullback_context=10, kept_executable=2 |
| 2026-05-29 | demoted_by_confirmation=2, demoted_by_evidence_ceiling_confirmed_or_unknown=5, demoted_by_evidence_ceiling_with_confirmation_pending=6, demoted_by_negative_pullback_context=18, kept_executable=2 |
| 2026-06-01 | demoted_by_negative_pullback_context=1, hard_block_after_ready_action=1, kept_executable=7 |
