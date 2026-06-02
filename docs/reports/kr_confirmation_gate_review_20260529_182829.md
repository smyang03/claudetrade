# KR Confirmation Gate Outcome Review

Generated: 2026-05-29T18:28:29
Market/runtime: KR/live
Window: 2026-05-29 ~ 2026-05-29
Rows: 33
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
| demoted_by_confirmation | 2 | 0 | None | 0 | None | None | None | None | None |
| demoted_by_evidence_ceiling_confirmed_or_unknown | 5 | 0 | None | 0 | None | None | None | None | None |
| demoted_by_evidence_ceiling_with_confirmation_pending | 6 | 1 | -2.439024 | 0 | None | None | None | None | None |
| demoted_by_negative_pullback_context | 18 | 3 | 1.224149 | 3 | -0.962152 | -1.925722 | 33.3333 | 0.427555 | -4.860788 |
| kept_executable | 2 | 1 | 0.0 | 0 | None | None | None | None | None |

## Reason Summary

| Group / Reason | Rows | 60m N | 60m Avg | MFE60 Avg | MAE60 Avg |
|---|---:|---:|---:|---:|---:|
| demoted_by_confirmation\|kr_data_quality_not_confirmed | 2 | 0 | None | None | None |
| demoted_by_evidence_ceiling_confirmed_or_unknown\|evidence_action_ceiling | 5 | 0 | None | None | None |
| demoted_by_evidence_ceiling_with_confirmation_pending\|evidence_action_ceiling | 6 | 0 | None | None | None |
| demoted_by_negative_pullback_context\|negative_pullback_context | 18 | 3 | -0.962152 | 0.427555 | -4.860788 |
| kept_executable\|buy_ready | 2 | 0 | None | None | None |

## From High Proxy

| Group / Proxy | Rows | 60m N | 60m Avg | MFE60 Avg | MAE60 Avg |
|---|---:|---:|---:|---:|---:|
| demoted_by_confirmation\|near_or_at_high_proxy | 2 | 0 | None | None | None |
| demoted_by_evidence_ceiling_confirmed_or_unknown\|near_or_at_high_proxy | 5 | 0 | None | None | None |
| demoted_by_evidence_ceiling_with_confirmation_pending\|below_high_proxy | 3 | 0 | None | None | None |
| demoted_by_evidence_ceiling_with_confirmation_pending\|near_or_at_high_proxy | 3 | 0 | None | None | None |
| demoted_by_negative_pullback_context\|below_high_proxy | 8 | 1 | -1.925722 | -0.137552 | -1.925722 |
| demoted_by_negative_pullback_context\|near_or_at_high_proxy | 10 | 2 | -0.480368 | 0.710109 | -6.328321 |
| kept_executable\|below_high_proxy | 2 | 0 | None | None | None |

## By Date

| Date | Group counts |
|---|---|
| 2026-05-29 | demoted_by_confirmation=2, demoted_by_evidence_ceiling_confirmed_or_unknown=5, demoted_by_evidence_ceiling_with_confirmation_pending=6, demoted_by_negative_pullback_context=18, kept_executable=2 |
