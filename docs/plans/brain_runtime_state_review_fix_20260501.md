# Brain Runtime State Review Fix - 2026-05-01

## Goal

Address the review findings against `state/brain.json` without discarding unrelated runtime statistics:

- Preserve learned `correction_guide` entries instead of replacing them with an empty rebuild result.
- Restore readable `execution_lessons` text before it is consumed by analyst and ticker-selection prompts.
- Prevent the same two state regressions from being written again.

This work must not change live order submission, sizing, broker reconciliation, market-data calls, or Claude call frequency.

## Scope

In scope:

- `state/brain.json`
- `claude_memory/brain.py`
- focused tests in `test_trading_improvements.py`

Out of scope:

- unrelated `trading_bot.py` local edits already present in the worktree
- unrelated untracked audit reports
- broad cleanup of existing mojibake comments/docstrings outside the prompt-consumed state path

## Improvement Order

### 1. Preserve Existing Correction Guides

- Add an empty-guide detector for `bull_adjustments`, `bear_adjustments`, `tuning_rules`, and `today_notes`.
- Change `BrainDB.update_correction_guide()` so an empty incoming guide does not overwrite a non-empty existing guide.
- Still allow a non-empty guide to update the current market.
- Add focused unit coverage for both behaviors.

Validation:

- Empty incoming guide keeps the previous non-empty guide.
- Non-empty incoming guide replaces or updates the guide as before.

### 2. Restore Readable Execution Lesson Templates

- Replace mojibake lesson templates in `BrainDB.update_execution_pattern()` with readable UTF-8 Korean text.
- Keep the existing behavior and retention limit of the latest 12 lessons.
- Add focused unit coverage for buy failure, sell failure, loss sell, and profitable sell lesson labels.

Validation:

- New lessons contain readable Korean labels.
- New lessons do not contain common mojibake markers.

### 3. Sanitize Existing Prompt-Consumed State

- Restore `correction_guide.KR` and `correction_guide.US` from the last known non-empty guide in `HEAD^:state/brain.json`.
- Rewrite existing `execution_lessons` entries in `state/brain.json` to readable text while preserving the event reason suffix.
- Leave unrelated runtime metric updates intact.

Validation:

- `state/brain.json` parses as UTF-8 JSON.
- Both markets have non-empty correction-guide policy entries.
- Prompt-consumed `execution_lessons` no longer contain mojibake markers.

### 4. Final QA

- Run targeted unit tests for the changed behavior.
- Run compile checks for changed Python files.
- Run JSON and state assertions against `state/brain.json`.
- Run `git diff --check` for touched files.

## Completion Checklist

| Item | Status | Evidence |
| --- | --- | --- |
| Related code paths inspected | Done | `BrainDB.update_execution_pattern()`, `BrainDB.update_correction_guide()`, prompt summary consumers |
| MD plan written before implementation | Done | This file |
| Correction-guide overwrite guard implemented | Done | `BrainDB.update_correction_guide()` skips empty incoming guides when a non-empty guide exists |
| Correction-guide tests added | Done | `BrainIntegrityTests` focused guide tests |
| Execution lesson templates fixed | Done | `BrainDB.update_execution_pattern()` writes readable UTF-8 labels |
| Execution lesson tests added | Done | `BrainIntegrityTests` focused execution lesson test |
| Existing `state/brain.json` sanitized | Done | execution lesson label normalized; KR/US correction guides restored |
| Item-level validation passed | Done | focused guide/lesson tests, JSON state assertions, execution lesson prompt-section assertion |
| Final QA passed | Done | `compileall`, `BrainIntegrityTests`, `git diff --check` |
| MD comparison and omission check completed | Done | see notes below |

## MD Comparison Notes

- Planned items completed: all four implementation and QA sections are complete.
- Differences from the plan: no functional difference; validation also checked the generated execution-lesson prompt section directly.
- Omitted items: none from this plan.
- Residual risks: unrelated pre-existing worktree changes and unrelated mojibake comments/docstrings remain outside this plan's scope.
