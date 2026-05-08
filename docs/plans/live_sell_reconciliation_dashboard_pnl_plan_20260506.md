# Live Sell Reconciliation / Dashboard PnL Hotfix Plan - 2026-05-06

## Goal

Fix the operationally risky gaps found in live sell confirmation, first-stop recovery entry gating, and dashboard realized PnL aggregation.

This plan separates mandatory fixes from optional hardening so the first implementation batch stays small and safe.

## Current Status - 2026-05-08

Keep this plan active for the remaining low-risk dashboard source-label cleanup.

Implemented/covered in code:

- Pending sell reconciliation path exists: `_reconcile_pending_sell_confirmations()`, `_clear_sell_confirmation_pending()`, `_close_position_from_pending_sell()`.
- Focused pending-sell reconciliation tests pass: `python -m pytest tests/test_live_sell_pending_reconcile.py tests/test_pathb_realized_pnl_dedupe.py -q` -> `9 passed, 2 warnings`.
- `_write_live_status()` now writes market-scoped realized PnL fields including `market_daily_pnl_krw` and `market_realized_pnl_krw`.
- Dashboard lifetime realized PnL tests are isolated from live broker/session state.
- `_new_buy_block_state()` allows `RECOVERY_MICRO` through only `STOP_CLUSTER_FIRST_STOP_COOLDOWN` with market scope.
- Recovery micro guard tests cover normal strategy block, first-stop recovery allow, same stopped ticker block, second-stop market block, and zero-freeze normal flow.
- Broker-confirmed local PnL matching consumes order-number and loose `(ticker, qty)` slots.
- Focused regression batch now passes: `python -m pytest tests/test_order_equity_reconciliation_improvement.py tests/test_dashboard_pathb.py tests/test_auto_sell_claude_gate.py -q` -> `70 passed, 2 warnings`.

Still open:

- Dashboard fallback source labeling still needs review: legacy `daily_pnl` fallback is not clearly labeled separately from market-scoped live status.

## Scope Decision

### Must Fix

- Add a minimal but reliable reconciliation path for `sell_confirmation_pending`.
- Prevent `_sync_runtime_with_broker()` from silently removing pending-sell positions without a closed event.
- Allow `RECOVERY_MICRO` through only the first-stop market cooldown.
- Consume broker-confirmed dashboard sell fills only once.

### Should Fix In Same Batch If Small

- Add market-scoped live realized PnL fields to `live_status`.
- Make dashboard live-status fallback prefer market-scoped PnL and label legacy global fallback explicitly.

### Defer

- Full partial-fill accounting in `RiskManager`.
- Broad `_daily_stop_cluster_state()` signature changes.
- Removing legacy `daily_pnl` fallback entirely.

## 1. Pending Sell Reconciliation

### Problem

`_mark_sell_confirmation_pending()` sets:

- `sell_confirmation_pending`
- `pending_sell_order_no`
- `pending_sell_qty`
- `pending_sell_reason`
- `pending_sell_price`
- `pending_sell_created_at`
- `pending_sell_status`

but there is no normal lifecycle path that clears these fields, records a closed decision, or releases the position after broker truth catches up.

### Required Implementation

- Add `_reconcile_pending_sell_confirmations(market, *, reason="cycle")`.
- Add `_clear_sell_confirmation_pending(pos, status, detail="")`.
- Add a closed-order idempotency guard:
  - in-memory key by `session_date|market|order_no`
  - decisions-file scan fallback for existing `type="closed"` rows with the same `order_no`
- Extract the post-fill close logic from `_execute_sell()` into a helper that both direct sell and reconcile can use.

Recommended helper shape:

```python
_finalize_confirmed_sell(
    cand,
    market,
    reason,
    order_no,
    fill=None,
    fallback_native_price=0.0,
    hold_advice=None,
    source="direct_sell",
)
```

### Price Rules

- If broker fill query returns `fill_price > 0`, use that as native execution price.
- Otherwise use `pending_sell_price`.
- If both are missing, use current raw price cache only as a last fallback.
- For US, convert native USD execution price to KRW before calling `risk.close_position()`.
- Record the source in decision metadata:
  - `broker_fill_query`
  - `pending_sell_price_fallback`
  - `price_cache_fallback`
  - `broker_balance_closed`

### Reconcile Outcomes

- Full fill confirmed:
  - call `_finalize_confirmed_sell()`
  - record `closed` decision
  - update `_session_closed_tickers`
  - update stop cluster if the close reason is stop-like
  - save positions and live status
- Cancelled/rejected/no open order and no fill:
  - clear pending fields
  - allow future sell attempts
  - record a non-closed operational event such as `sell_pending_released`
- Broker balance says position is gone but fill query is missing:
  - do not silently drop the position
  - finalize with `source="broker_balance_closed"` and fallback price
- Broker truth unavailable:
  - keep pending lock
  - update `pending_sell_status="broker_truth_unavailable"`
  - do not remove the position
- Partial fill:
  - do not attempt full PnL accounting in the first batch
  - update local remaining quantity only if broker evidence is trusted
  - if the remaining sell order is still open, keep sell skip active
  - if no remaining open order exists, clear pending so the remaining local holding can be sold again

### Sync Guard

Inside `_sync_runtime_with_broker()`, before the stale-position removal branch, detect:

```python
if pos.get("sell_confirmation_pending"):
    # reconcile first; if still unresolved, keep or finalize with explicit evidence
```

This guard is required even if reconciliation is also called before broker sync.

### Acceptance Tests

- A pending sell later becomes fully filled and records exactly one `closed` decision.
- The same pending `order_no` cannot create duplicate PnL or duplicate closed decisions.
- A cancelled/unfilled pending sell clears the pending fields and allows a future sell.
- `_sync_runtime_with_broker()` does not silently remove a pending-sell position.
- A broker-balance-gone pending sell creates a closed event instead of disappearing.
- Partial fill with an open remaining order keeps sell skip active.
- Partial fill with no remaining open order releases the remaining position for another sell.

## 2. RECOVERY_MICRO First-Stop Cooldown Exemption

### Problem

After the first stop, `_daily_stop_cluster_state()` returns `STOP_CLUSTER_FIRST_STOP_COOLDOWN`. `RECOVERY_MICRO` is created only after the first stop, but `_new_buy_block_state()` blocks it again.

### Required Implementation

Implement the exemption inside `_new_buy_block_state()`, not by changing `_daily_stop_cluster_state()`.

Allow `RECOVERY_MICRO` only when all conditions match:

- strategy upper-case is `RECOVERY_MICRO`
- stop cluster reason is `STOP_CLUSTER_FIRST_STOP_COOLDOWN`
- stop cluster scope is `market`

Keep these blocks active:

- `SAME_DAY_REENTRY_AFTER_STOP`
- `STOP_CLUSTER_MARKET_BLOCK`
- `STOP_CLUSTER_DISASTER_BLOCK`
- market closed
- entry blackout
- order unknown blocks

### Acceptance Tests

- First stop cooldown blocks normal strategies.
- First stop cooldown allows `RECOVERY_MICRO`.
- Same stopped ticker still blocks `RECOVERY_MICRO`.
- Second stop market block still blocks `RECOVERY_MICRO`.
- `STOP_CLUSTER_FIRST_FREEZE_MINUTES=0` allows normal flow without using exemption logic.

## 3. Dashboard Broker-Confirmed Fill Consumption

### Problem

`_broker_confirmed_local_realized_pnl()` uses an `order_nos` set. Duplicate local `closed` rows with the same broker-confirmed `order_no` are all counted.

### Required Implementation

- Replace simple set membership with consumed-order tracking.
- Prefer an `order_no -> broker fill row` map so the matching broker slot is explicit.
- When a local record matches by `order_no`, consume that `order_no` once.
- Also decrement the matching loose `(ticker, qty)` slot for the consumed broker fill row.
- Only use loose fallback when no unconsumed order-number match exists.

### Acceptance Tests

- Two local `closed` rows with the same confirmed `order_no` count once.
- An order-number match cannot be counted again by loose fallback.
- Broker fills without `order_no` can still be matched by loose `(ticker, qty)` fallback.
- Loose fallback cannot count more rows than broker fill slots.

## 4. Market-Scoped Live PnL Fallback

### Problem

Dashboard fallback uses `live_status["daily_pnl"]`, which is backed by global `RiskManager.daily_pnl` and can mix KR/US realized PnL.

### Required Implementation

In `_write_live_status()`:

- Add `market_daily_pnl_krw`.
- Optionally add `daily_pnl_by_market`.
- Compute the new field using `_market_realized_pnl_krw(market)`.
- Wrap the computation in `try/except` so live-status writes continue if the new field fails.

In dashboard fallback:

- Prefer `market_daily_pnl_krw`.
- Then try `daily_pnl_by_market[market]`.
- Use legacy `daily_pnl` only as a compatibility fallback.
- Label legacy fallback as `live_status_daily_pnl_legacy`.

### Acceptance Tests

- KR dashboard fallback does not include US realized PnL.
- US dashboard fallback does not include KR realized PnL.
- Existing live-status files without new fields still work with legacy source labeling.
- Broker-confirmed and deduped local sources still take priority over live-status fallback.

## Implementation Order

1. `RECOVERY_MICRO` first-stop cooldown exemption.
2. Dashboard broker-confirmed fill consumption.
3. Market-scoped live PnL fallback.
4. Pending sell reconciliation.

The first three are small, low-risk changes. Pending sell reconciliation is mandatory but should be implemented after tests are written because it touches the sell close lifecycle.

## QA Commands

- `python -m py_compile trading_bot.py dashboard/dashboard_server.py risk_manager.py`
- `python -m pytest tests/test_order_equity_reconciliation_improvement.py tests/test_dashboard_pathb.py tests/test_auto_sell_claude_gate.py -q`
- Add and run focused pending-sell reconciliation tests before modifying the sell finalize path.
- `git diff --check`

## Non-Goals For First Patch

- Do not redesign `RiskManager.close_position()` for partial close accounting.
- Do not remove global `daily_pnl`.
- Do not broaden `RECOVERY_MICRO` beyond the first-stop cooldown exemption.
- Do not rely on broker sync to clean pending sell state silently.
