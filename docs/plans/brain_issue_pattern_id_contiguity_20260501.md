# Brain Issue Pattern ID Contiguity Fix - 2026-05-01

## Goal

Prevent duplicate `issue_patterns[].id` values during future postmortem learning updates.

## Current Findings

- `state/brain.json` has non-contiguous issue pattern IDs.
- US `issue_patterns` has 45 rows with max ID `P046`; missing ID is `P045`.
- KR `issue_patterns` has 66 rows with max ID `P068`; missing IDs are `P066` and `P067`.
- `claude_memory/brain.py:update_issue_pattern()` creates a new ID with `len(patterns) + 1`.
- With the current data, the next unmatched postmortem can create an ID that already exists.
- No external references to the affected IDs were found outside `issue_patterns` in `state/brain.json`.

## Improvement List

1. Repair existing runtime data.
   - Rename US trailing `P046` issue pattern to `P045`.
   - Rename KR trailing `P068` issue pattern to `P066`.
   - Preserve issue pattern order and all non-ID fields.

2. Harden new ID allocation.
   - Replace `len(patterns) + 1` with a max-existing-ID allocator.
   - Ignore malformed IDs when calculating the max.
   - Use `P001` when the market has no valid existing issue pattern ID.

3. Add regression coverage.
   - Verify a gap does not produce a duplicate ID.
   - Verify malformed IDs do not break allocation.
   - Verify an empty pattern list starts at `P001`.

4. Validate runtime data integrity.
   - Parse `state/brain.json`.
   - Confirm every market has unique issue pattern IDs.
   - Confirm every market's IDs are contiguous from `P001` to `P{count}`.
   - Confirm the next ID implied by max existing ID cannot collide.

5. Final QA.
   - Run targeted unit tests for BrainDB issue pattern allocation.
   - Compile `claude_memory/brain.py`.
   - Run JSON integrity scan for `state/brain.json`.
   - Run `git diff --check` on touched files.
   - Compare completed work against this plan and list any omission or difference.

## Completion Criteria

- US issue pattern IDs are `P001..P045` with no gaps or duplicates.
- KR issue pattern IDs are `P001..P066` with no gaps or duplicates.
- `update_issue_pattern()` uses max existing ID plus one for new issue pattern IDs.
- Regression tests cover the gap, malformed ID, and empty-list cases.
- Final QA reports no plan omissions.

## Execution Results

- Data repair completed.
  - US trailing issue pattern ID changed from `P046` to `P045`.
  - KR trailing issue pattern ID changed from `P068` to `P066`.
- Runtime allocation hardening completed.
  - Added `_next_issue_pattern_id()`.
  - New issue pattern IDs now use max valid existing `P###` plus one.
  - Existing-pattern lookup now ignores malformed rows and only matches when `matched_id` is present.
- Regression coverage completed.
  - Added max-valid-ID allocation test.
  - Added empty/malformed-ID allocation test.
  - Added `update_issue_pattern()` gap/no-duplicate test.

## QA Results

- `python -m unittest test_trading_improvements.BrainIntegrityTests`
  - Passed: 4 tests.
- `python -m py_compile claude_memory\brain.py`
  - Passed.
- `state/brain.json` integrity scan
  - KR: `count=66`, `max=66`, `next=P067`, no duplicates, no malformed IDs, contiguous.
  - US: `count=45`, `max=45`, `next=P046`, no duplicates, no malformed IDs, contiguous.
- `git diff --check -- claude_memory\brain.py test_trading_improvements.py state\brain.json docs\plans\brain_issue_pattern_id_contiguity_20260501.md`
  - Passed; Git reported CRLF normalization warnings only.

## Plan Comparison

- No planned item is missing.
- No implementation difference was found for the issue-pattern ID fix.
- Working tree note: `git diff` also shows unrelated existing changes in touched files, including other `state/brain.json` runtime updates and `claude_memory/brain.py` correction-guide changes. They are outside this plan and were not treated as part of this fix.
