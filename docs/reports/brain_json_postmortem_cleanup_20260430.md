# Brain JSON Postmortem Cleanup QA - 2026-04-30

## Scope

Prevent postmortem parsing/system failures from becoming Claude policy memory, and clean existing contamination from `state/brain.json`.

No live trading behavior was changed.

## Pre-Scan Results

- `minority_report/postmortem.py` has one `BrainDB.update_issue_pattern(...)` call.
- `BrainDB.add_daily_record(...)` is the daily `recent_days` write path for postmortem.
- `state/brain.json` has no explicit `next_pattern_id` or sequence counter.
- `claude_memory/brain.py:update_issue_pattern()` creates new IDs with `len(patterns) + 1`; ID gaps from cleanup are safe.
- Execution/API errors are stored under `execution_patterns`, not `issue_patterns`.

## Code Changes

- `minority_report/postmortem.py`
  - Postmortem exception fallback now sets `_system_error=True` and `_skip_issue_pattern=True`.
  - System-error fallback no longer writes `postmortem_error` to `issue_patterns`.
  - System-error fallback still writes the daily performance row, but `key_lesson` and `issue_type` are blank.
  - System-error fallback does not update `correction_guide`.

- `test_trading_improvements.py`
  - Added coverage that postmortem exception fallback does not call `update_issue_pattern`.
  - Added coverage that fallback daily records have blank `key_lesson` and `issue_type`.
  - Added coverage that fallback does not call `update_correction_guide`.

## State Cleanup

Removed `issue_patterns` entries:

- KR: `P066`, `P067`, `P069`
- US: `P045`, `P047`

Sanitized affected `recent_days` rows without deleting the rows:

- KR: `2026-04-27`, `2026-04-28`, `2026-04-30`
- US: `2026-04-27`, `2026-04-29`

For those rows:

- `issue_type` was changed from `postmortem_error` to `""`.
- `key_lesson` was changed from `postmortem 응답 실패` to `""`.

Sanitized existing correction guide failure notes:

- `correction_guide.KR.today_notes` -> `""`
- `correction_guide.US.today_notes` -> `""`

## Execution Pattern Review

KIS/API execution error records were intentionally preserved.

`generate_prompt_summary()` renders these under the separate recent execution pattern/lesson sections, not under `Recent issue patterns`. The `500 Server Error` detail itself is not included in the generated summary because `_build_execution_summary()` prints action, strategy, reason, count, recency, and average PnL only.

## Verification

- `python -m compileall minority_report\postmortem.py claude_memory\brain.py test_trading_improvements.py` passed.
- `python -m pytest test_trading_improvements.py -q -k "PostmortemSelectionFeedbackTests or BrainSummarySelectionFeedbackTests"` passed: `3 passed`.
- `state/brain.json` parses as UTF-8 JSON.
- `state/brain.json` consistency checks passed:
  - no duplicate issue pattern IDs
  - no analyst `total != hit + miss` mismatch
  - no analyst rate mismatch
  - no out-of-range win rates
- `rg "postmortem_error|Expecting ',' delimiter|postmortem 응답 실패" state\brain.json` returns no rows.
- KIS/API execution error records remain present.
- `git diff --check -- minority_report/postmortem.py state/brain.json test_trading_improvements.py docs/plans/brain_postmortem_cleanup_20260430.md` passed.

## Plan Comparison

| Plan Item | Status | Notes |
| --- | --- | --- |
| Full postmortem scan | Done | One `update_issue_pattern` call found. |
| Recurrence prevention | Done | `_skip_issue_pattern` and `_system_error` guard added. |
| Daily row retained but sanitized | Done | `key_lesson` and `issue_type` blank for system errors. |
| `brain.json` ID structure checked | Done | No explicit counter; ID gaps safe. |
| Existing bad issue patterns removed | Done | 5 issue pattern entries removed. |
| Existing recent days sanitized | Done | 5 daily records kept and sanitized. |
| Correction guide contamination handled | Done | Existing notes cleared; future system errors skip guide update. |
| Execution error prompt impact checked | Done | Execution errors remain diagnostic and separated from issue patterns. |
| Tests and QA | Done | Compile, targeted pytest, JSON/state checks, diff check passed. |

## Residual Notes

- ID gaps remain in `issue_patterns`; this is acceptable because the code does not use a stored counter.
- Runtime KIS/API failure diagnostics remain in `execution_patterns` and should not be deleted.
