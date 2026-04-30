# Soft Exit Arbitration Plan - 2026-04-30

## Goal

Prevent Path A positions from being sold too early by soft exits when Claude already supplied a still-valid target thesis for the same ticker.

The STX case is the reference scenario:

- Path B price plan existed for STX, but the Plan B entry was cancelled by price chase / open-above logic.
- Path A later bought STX in the same direction.
- `profit_floor` sold quickly without asking Claude again.
- Price later traded above the sell price, so the missing bridge between Path B thesis and Path A exit decision became visible.

## Scope

Phase 1 implements only the low-risk bridge needed for this class of case.

1. Copy reference targets into Path A positions at entry time.
   - If a same-day cancelled Path B plan exists for the ticker, copy:
     - `pathb_reference_target`
     - `pathb_reference_stop`
     - `pathb_reference_confidence`
     - `pathb_reference_status`
     - `pathb_reference_path_run_id`
   - Also snapshot the current selection target into:
     - `selection_reference_target`
     - `selection_reference_stop`
     - `selection_reference_confidence`
   - These are reference metadata only. They do not force a buy or sell.

2. Add a lightweight quick exit check.
   - Single Claude call, not the existing 3-view `hold_advisor`.
   - Expected output: `HOLD` or `SELL`.
   - Parse/timeout/API error fallback: `SELL`, meaning existing sell path continues.
   - No retries beyond the single call path.

3. Add soft-exit arbitration gate.
   - Applies only to:
     - `profit_floor`
     - `trail_stop`
     - eligible `max_hold` cases that have a reference target
   - Does not apply to:
     - `loss_cap`
     - `stop_loss`
     - broker/order failures
     - `ORDER_UNKNOWN`
     - session forced close
     - Path B managed positions

4. Gate conditions.
   - Reference target exists.
   - Reference target is above current price by the configured buffer.
   - Position MFE is at least the configured threshold.
   - Current price is above entry price.
   - Position has not exceeded the per-position arbitration count.
   - Position is outside cooldown.

5. HOLD loop protection.
   - `soft_exit_floor_price` is a direct sell trigger and never re-enters arbitration.
   - Each position has a max arbitration count.
   - Each HOLD decision writes a cooldown timestamp.
   - HOLD may raise/protect stored stop metadata; it must not weaken existing protective metadata.

## Deferred To Phase 2

- Reverse dependency write: Plan B cancellation later attaching reference metadata to an already-open Path A position.
- Same-day reentry distinction between loss exits and profitable exits.
- Broad changes to the normal 3-view `hold_advisor` flow outside this arbitration gate.

## Operational Safety

- No live API calls are required for verification.
- No bot process restart is required.
- No orders are placed by tests.
- The quick check fails open to the current sell path: if Claude is slow, unavailable, or returns invalid JSON, the original sell continues.
- Hard stops and broker-risk paths bypass Claude entirely.
- Path B positions remain excluded from `RiskManager.get_exit_candidates()` and are still managed by Path B runtime.

## Implementation Steps

1. Add quick exit checker module under `minority_report/`.
2. Add reference metadata helpers in `trading_bot.py`.
3. Attach reference metadata to Path A pending orders and copy it into positions on fill.
4. Add `soft_exit_floor_price` as a RiskManager exit candidate.
5. Insert soft-exit arbitration before normal sell execution.
6. Persist arbitration metadata into sell audit fields.
7. Add v2 close reason mapping for `soft_exit_floor_price`.
8. Add focused tests.

## Verification Plan

1. Static compile:
   - `python -m compileall trading_bot.py risk_manager.py runtime/v2_lifecycle_runtime.py minority_report/quick_exit_check.py`
2. Unit tests:
   - `python -m pytest tests/test_loss_cap_profit_floor.py tests/test_soft_exit_arbitration.py -q`
3. Safety checks:
   - Confirm `loss_cap` and `stop_loss` bypass arbitration.
   - Confirm `soft_exit_floor_price` bypasses arbitration.
   - Confirm quick-check API failure continues the sell path.
   - Confirm HOLD writes cooldown and review count.
   - Confirm reference metadata is copied from cancelled same-day Path B plan and selection metadata.

## Completion QA

After implementation, create `docs/reports/soft_exit_arbitration_qa_20260430.md` with:

- Implemented item checklist.
- Test command output summary.
- Operational safety review.
- Plan-vs-actual comparison.
- Known deferred items.
