# Soft Exit Arbitration Follow-up Plan - 2026-05-01

## Goal

Tighten the Phase 1 soft-exit arbitration implementation without changing its operational behavior.

This follow-up addresses review findings:

1. Raw Claude call logging should record the actual model used by `quick_exit_check`.
2. The soft-exit arbitration comment should describe all covered soft exits, not only `max_hold`.
3. Unreachable protective-floor fallback code should be removed.
4. Regression tests should cover common bypass paths and target priority.

## Code Changes

1. `minority_report/quick_exit_check.py`
   - Add `model=MODEL` to `save_raw_call(...)`.
   - Expected effect: audit log accuracy only.

2. `trading_bot.py`
   - Replace the misleading `max_hold` comment above `_try_soft_exit_arbitration(...)`.
   - Remove unreachable branch after:
     - `protective_floor = max(existing_floor, suggested_floor, entry_native)`
   - Expected effect: no runtime behavior change.

## Test Additions

Add focused tests to `tests/test_soft_exit_arbitration.py`:

1. `pathb_reference` beats `selection_reference` when both are present.
2. `trail_stop` is eligible for arbitration.
3. `max_reviews` limit bypasses quick Claude check.
4. cooldown bypasses quick Claude check.
5. missing reference target bypasses quick Claude check.
6. `current_price <= entry_price` bypasses quick Claude check.
7. KRW `soft_exit_floor_price` path is explicit.

## Verification

Run only local, non-live checks:

```powershell
python -m compileall trading_bot.py minority_report\quick_exit_check.py tests\test_soft_exit_arbitration.py
python -m pytest tests\test_soft_exit_arbitration.py tests\test_loss_cap_profit_floor.py -q
git diff --check -- trading_bot.py minority_report\quick_exit_check.py tests\test_soft_exit_arbitration.py docs\plans\soft_exit_arbitration_followup_20260501.md docs\reports\soft_exit_arbitration_qa_20260430.md
```

## Operational Safety

- No KIS API calls.
- No broker/order API calls.
- No bot restart.
- No live order path execution.
- All new tests use stubs, local objects, or temporary SQLite only.

## Completion QA

Update `docs/reports/soft_exit_arbitration_qa_20260430.md` with:

- follow-up change list,
- additional test list,
- verification results,
- plan-vs-actual comparison.
