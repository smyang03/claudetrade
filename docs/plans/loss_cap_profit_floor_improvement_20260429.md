# Loss Cap / Profit Floor Improvement - 2026-04-29

## Goal

Prevent a single position from dominating a session loss while keeping the current aggressive candidate selection intact.

## Findings

- `MAX_SINGLE_LOSS_PCT=-3.0` exists, but it is only applied during Path A entry `sl_pct` calculation.
- Path B converts Claude `plan.stop_loss` directly into `sl_pct`, so a wide Claude stop can bypass the single-loss cap.
- `RiskManager.get_exit_candidates()` does not currently apply a common loss-cap overlay to already-open positions.
- `PathBRuntime.scan_exits()` has its own exit path and uses `_native_hard_stop()`, which returns `pos["sl"]` without a loss-cap overlay.
- `v2_close_reason()` has no mapping for `loss_cap` or `profit_floor`, so those reasons would otherwise fall back to `CLOSED_USER_MANUAL`.
- `peak_pnl_pct` is already tracked in `RiskManager.update_prices()`, so profit-floor logic can use the existing field.

## Design

### 1. Single Position Loss Cap

Loss cap is a hard risk overlay, not a replacement for strategy stop-loss.

For long positions:

```text
position_loss_budget = entry_value * abs(MAX_SINGLE_LOSS_PCT) / 100
session_loss_budget = session_start_equity * POSITION_SESSION_LOSS_CAP_PCT / 100
loss_budget_krw = min(position_loss_budget, session_loss_budget)
loss_cap_pct = loss_budget_krw / entry_value
loss_cap_price = entry_price * (1 - loss_cap_pct)
effective_stop = max(strategy_stop_price, loss_cap_price)
```

If the loss-cap stop is the active tighter stop, exit reason must be `loss_cap`.

### 2. Profit Floor

Profit floor is a secondary protection layer.

```text
if peak_pnl_pct >= PROFIT_FLOOR_TRIGGER_PCT
and current_pnl_pct <= PROFIT_FLOOR_EXIT_PCT:
    exit_reason = "profit_floor"
```

Default values:

```text
PROFIT_FLOOR_TRIGGER_PCT=2.0
PROFIT_FLOOR_EXIT_PCT=0.5
```

### 3. Exit Reason Ownership

- `loss_cap`: hard risk guard, should count as same-day stop for size penalty.
- `profit_floor`: profit protection, should not count as same-day stop.
- `stop_loss`: original strategy/ATR/Claude stop.
- `trail_stop`: trailing stop.

### 4. Path B Integration

Path B must not rely only on `pos["sl"]`.

`PathBRuntime.scan_exits()` should evaluate the loss-cap native stop before calling the Claude price sell manager. If triggered, it should emit:

```text
ExitSignal(True, "loss_cap", "CLOSED_LOSS_CAP", current, path_run_id)
```

### 5. v2 Mapping

Add:

```text
loss_cap -> CLOSED_LOSS_CAP
profit_floor -> CLOSED_PROFIT_FLOOR
```

## Implementation Checklist

- [x] Add risk constants/envs for session loss cap and profit floor.
- [x] Add RiskManager helpers for current PnL, loss budget, loss-cap price, profit-floor trigger.
- [x] Apply `loss_cap` and `profit_floor` in `RiskManager.get_exit_candidates()`.
- [x] Apply Path B native loss cap in `PathBRuntime.scan_exits()`.
- [x] Add v2 close reason mappings.
- [x] Add trading bot reason priority and stop-count handling.
- [x] Add focused tests for KR/US loss cap, profit floor, Path B loss cap, and v2 close reason mapping.

## QA Checklist

- [x] `python -m py_compile risk_manager.py runtime\pathb_runtime.py runtime\v2_lifecycle_runtime.py trading_bot.py`
- [x] Focused unit tests pass.
- [x] Existing Path B sell/runtime tests pass.
- [x] Verify checklist above has no unimplemented item.

## QA Result

- `python -m py_compile risk_manager.py runtime\pathb_runtime.py runtime\v2_lifecycle_runtime.py trading_bot.py tests\test_loss_cap_profit_floor.py` passed.
- `python -m unittest tests.test_loss_cap_profit_floor` passed.
- `python -m unittest tests.test_pathb_sell tests.test_pathb_runtime` passed.
- `python -m unittest tests.test_pathb_sell_reconcile` passed.

## Omission Review

- Loss cap is applied in both general `RiskManager` exits and Path B native exit scanning.
- Profit floor uses existing `peak_pnl_pct` tracking and does not count as a same-day stop.
- `loss_cap` counts as a stop in the trading bot stop-count path, and Path B loss-cap closes also mark same-day stop state.
- v2 close reason mapping covers both raw reasons and already-normalized `CLOSED_*` values.
