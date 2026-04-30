# Brain Postmortem Cleanup Plan - 2026-04-30

## Goal

Prevent system/JSON parsing failures from being stored as Claude policy memory, and remove the existing `postmortem_error` contamination from `state/brain.json`.

This must not change live order decisions, order submission, broker reconciliation, sizing, risk gates, market-data calls, or Claude call frequency.

## Pre-Implementation Scan

- `minority_report/postmortem.py` has one `BrainDB.update_issue_pattern(...)` call.
- `BrainDB.add_daily_record(...)` is the recent-day write path in `postmortem.py`.
- `state/brain.json` has no `next_pattern_id`, `last_pattern_id`, or explicit pattern sequence counter.
- `claude_memory/brain.py:update_issue_pattern()` creates new issue IDs from `len(patterns) + 1`, so deleting bad patterns creates harmless ID gaps but no counter mismatch.
- KIS/API execution errors are stored under execution patterns and are operational diagnostics, not cleanup targets.

## Implementation Steps

### 1. Prevent Recurrence In `postmortem.py`

- On postmortem exception fallback, mark the payload as system-error-only:
  - `_system_error = True`
  - `_skip_issue_pattern = True`
- Keep analyst HIT/MISS code correction and mode/strategy updates.
- Skip `BrainDB.update_issue_pattern(...)` when `_skip_issue_pattern` is true.
- Keep `BrainDB.add_daily_record(...)`, but sanitize policy-memory fields:
  - `key_lesson = ""` for system errors
  - `issue_type = ""` for system errors
- Do not update `correction_guide` for system errors.
- Do not add any broker, Claude, or market-data calls.

### 2. Clean Existing `brain.json`

Remove issue-pattern entries:

- KR: `P066`, `P067`, `P069`
- US: `P045`, `P047`

Keep the affected `recent_days` rows, but sanitize:

- `issue_type: "postmortem_error"` -> `""`
- `key_lesson: "postmortem 응답 실패"` -> `""`

Also sanitize existing `correction_guide` failure notes:

- `correction_guide.KR.today_notes: "postmortem 응답 실패"` -> `""`
- `correction_guide.US.today_notes: "postmortem 응답 실패"` -> `""`

Affected recent days:

- KR: `2026-04-27`, `2026-04-28`, `2026-04-30`
- US: `2026-04-27`, `2026-04-29`

### 3. Check Execution Error Prompt Handling

- Confirm execution/API errors remain under execution summary, separate from issue patterns.
- Do not delete KIS `500 Server Error` records.

## Verification

- Compile:
  - `python -m compileall minority_report/postmortem.py claude_memory/brain.py`
- Tests:
  - targeted postmortem/brain tests from `test_trading_improvements.py`
- State checks:
  - JSON parse succeeds.
- `rg "postmortem_error|Expecting ',' delimiter" state/brain.json` returns no rows.
- `rg "postmortem 응답 실패" state/brain.json` returns no rows.
  - recent-day counts are unchanged.
  - issue-pattern IDs have no duplicates.
  - KIS/API execution error records remain.
- Diff check:
  - `git diff --check -- minority_report/postmortem.py state/brain.json docs/plans/brain_postmortem_cleanup_20260430.md docs/reports/brain_json_postmortem_cleanup_20260430.md`

## Completion Criteria

- Future postmortem parse failures are logged/diagnostic only, not policy issue patterns.
- Existing `postmortem_error` policy-memory rows are removed.
- Daily performance records remain intact.
- No operational trading path is changed.

## Completion Status

| Item | Status |
| --- | --- |
| Full `postmortem.py` scan | Done |
| Recurrence prevention in `postmortem.py` | Done |
| Existing `brain.json` issue-pattern cleanup | Done |
| Existing `recent_days` sanitization | Done |
| Existing `correction_guide` sanitization | Done |
| Execution-pattern prompt impact review | Done |
| Tests and state validation | Done |
| QA and omission review | Done |
