# Order / Equity Reconciliation Improvement Plan - 2026-04-29

## Goal

Fix live-order state drift and broker-equity lag issues found in the 2026-04-28 US session and the 2026-04-29 KR session review.

The plan has three hard goals:

- Preserve live order state by order number, not only by ticker.
- Detect broker/local open-order mismatches before they can create uncontrolled exposure.
- Use a daily-loss equity reference that is resilient to KIS cash/evaluation update lag.

## Non-Code Prerequisite

- [ ] Operator must check KIS app/HTS/API for KR `006340` and `047040` open buy orders.
- [ ] If any open buy order remains for those tickers, cancel it manually before trusting new Path B entries.
- [ ] Confirm broker open order quantity is zero for those tickers.

This cannot be replaced by a code patch because the current broker may already hold live orders that local state does not fully represent.

## Implementation Checklist

### P1. Order State Integrity

- [x] Update `_add_pending_order()` to preserve orders by `(market, order_no)` when `order_no` exists.
  - If `order_no` exists, upsert only the matching order number.
  - If `order_no` is missing, keep the existing same-ticker temporary replacement behavior.
  - Do not delete an older broker-submitted order just because the same ticker receives another order.
  - Mark duplicate same-ticker pending exposure for later blocking/ops visibility.

- [x] Promote broker truth `open_orders` to a reconcile source.
  - Use existing `BrokerTruthSnapshot` and KIS daily order/fill query output.
  - Trigger broker truth refresh only at bounded points: session open, ORDER_UNKNOWN, cancel TTL, same-ticker reorder preflight, and session close.
  - Compare broker open orders with local pending orders by `(market, order_no)`.
  - Report `broker_only_open_orders`, `local_only_pending_orders`, and `duplicate_ticker_orders`.

- [x] Extend ORDER_UNKNOWN tracking to order-number granularity.
  - Track `order_no`, `market`, `ticker`, `side`, `qty`, `remaining_qty`, `local_pending`, `broker_open`.
  - Track `cancel_requested_at`, `cancel_attempts`, `last_checked_at`, `next_check_at`, `resolution`, `resolved_at`.
  - Add market/global block helpers used by Path B.

- [x] Add cancel-confirm TTL behavior on top of the order-number registry.
  - Recheck broker truth after TTL.
  - If broker still shows the order open, keep blocking and surface ops warning.
  - If broker no longer shows it, resolve the registry entry.

### P1. Equity / Safety

- [x] Add US sell-proceeds lag compensation.
  - Record recent sell proceeds in a short-TTL ledger after a sell order is accepted/closed locally.
  - Apply only while broker cash appears not to include the proceeds.
  - Remove/ignore the adjustment after TTL or broker cash recovery.
  - Do not modify raw broker snapshots.

- [x] Add KR internal session-equity reference.
  - Compute `session_start_equity + market_realized_pnl + market_unrealized_pnl`.
  - Use market-specific session baseline from `state/daily_baseline.json`.
  - Keep KR and US realized PnL separated.
  - Treat broker `cash + eval` as audit data when KIS cash lag is suspected.

- [x] Add `_market_equity_reference_context()` fallback boundaries.
  - Preferred total: adjusted/internal equity when positive.
  - Fallback: broker current total when positive.
  - Fallback: last trusted broker total when positive.
  - Last fallback: position value or `0`.
  - Never allow an initialization gap to create a `-100%` daily return.

- [ ] Keep `SafetyContext` schema changes for a separate audit patch.
  - First patch keeps feeding stabilized return into existing `daily_pnl_pct`.
  - Later patch can add `equity_return_pct`, `realized_return_pct`, `unrealized_return_pct`, `equity_source`, and `broker_lag_suspected`.

### P1. New-Buy Safety Gate

- [x] Unify KR session-close timing around the real KIS new-order cutoff.
  - Use the market close anchor (`15:30` KST for KR) for `_seconds_until_session_close()`.
  - Use the same anchor for `_in_entry_blackout()`.
  - This intentionally moves KR new-entry blackout from about `15:50` to about `15:20` when `close_before_min=10`.

- [x] Add a common new-buy block-state helper.
  - Return structured state: `allowed`, `reason`, `scope`, and `details`.
  - Check order: market orderability, entry blackout, global ORDER_UNKNOWN block, market ORDER_UNKNOWN block, ticker ORDER_UNKNOWN block.
  - Keep this helper strictly for new buys; sell, cancel, and reconcile flows must not be blocked by it.

- [x] Apply the common gate before Path A buy submission.
  - The signal can still be recorded.
  - The broker `precheck_order()` and `place_order()` calls must not run when the gate blocks.
  - Record blocked decisions with the shared reason code.

- [x] Apply the common gate before micro-probe buy submission.
  - Micro-probe orders are real buy orders and must follow the same orderability and ORDER_UNKNOWN rules.

- [x] Apply the common gate to US/KR Tier2 sector-play buys.
  - Run the market-level gate before calling `run_sector_plays()` / `run_kr_sector_plays()` to avoid unnecessary Claude calls.
  - Run the ticker-level gate again immediately before broker precheck/order submission.

- [x] Apply the common gate to Path B buy submission.
  - `reconcile_order_unknowns()` and `reconcile_buy_pending_cancel_above()` must still run.
  - New waiting-entry scans and `_submit_buy()` must stop when the gate blocks.
  - Keep this as a runtime precheck instead of mixing time gates into `SafetyContext`.

### P2. Ops / Metrics

- [ ] Deduplicate v2 close metrics by `path_run_id`.
- [ ] Keep dashboard and ops review grouped by `session_date`.
- [ ] Surface broker/local order mismatches in dashboard ops.
- [ ] Escalate repeated Telegram timeout as an ops visibility warning.

### P3. Strategy Follow-Up

- [ ] Split `gap_pullback` wait from momentum wait.
- [ ] Make Path B inline replacement consider no-signal, price deterioration, zone distance, and broker order state.
- [ ] Track watch-only missed runups with exclusion reason and later runup.

## QA Checklist

- [x] Duplicate same-ticker pending orders with different order numbers remain in local pending state.
- [x] Order without `order_no` still replaces the temporary same-ticker pending order.
- [x] Broker-only open order is detected and blocks new risk exposure.
- [x] ORDER_UNKNOWN registry tracks order numbers independently.
- [x] Cancel request and cancel resolution are tracked by order number.
- [x] US SNAP-like sell proceeds lag does not trigger false daily-loss block.
- [x] KR cash-stale/eval-updated snapshot does not overstate daily equity.
- [x] Market realized PnL calculation ignores realized PnL from other markets.
- [x] `_market_daily_return_pct()` returns `0.0` rather than `-100%` when equity context is not initialized.
- [x] Existing partial-fill persistence behavior still passes.
- [x] Existing Path B partial-fill quantity behavior still passes.
- [x] Existing `/api/trades/list` `seen` initialization remains covered by smoke tests.
- [x] KR 15:21 KST new buy is blocked by `ENTRY_BLACKOUT` when `close_before_min=10`.
- [x] KR 15:31 KST new buy is blocked by `MARKET_CLOSED`.
- [x] ORDER_UNKNOWN market block is exposed by the common new-buy gate before Tier2 generation.
- [x] Path B still performs ORDER_UNKNOWN/cancel-above reconcile while new buys are blocked.
- [x] Micro-probe buy submission uses the same new-buy gate.

## Execution Notes

- `_broker_snapshot_from_balance()` must remain a raw snapshot builder.
- Equity corrections belong in the consumer/reference layer, primarily `_market_equity_reference_context()`.
- Broker truth collection is heavier than balance lookup; do not call it every normal cycle.
- Manual KIS cleanup is a prerequisite for current live exposure, not a software fix.
- The common new-buy gate must not block sell, cancel, or broker reconciliation flows.
- KR `15:20` new-entry cutoff is an intentional consequence of using KIS `15:30` orderability close plus `close_before_min=10`.

## Progress

- [x] Detailed improvement plan saved.
- [x] Order-state code changes complete.
- [x] Equity reference code changes complete.
- [x] Tests added/updated.
- [x] QA commands passed.
- [x] Final document review completed.
- [x] New-buy safety gate code changes complete.
- [x] New-buy safety gate QA commands passed.
- [x] New-buy safety gate document review completed.

## QA Result

- `python -m py_compile trading_bot.py execution\order_state.py runtime\pathb_runtime.py tests\test_order_equity_reconciliation_improvement.py` passed.
- `python -m unittest tests.test_order_equity_reconciliation_improvement` passed.
- `python -m unittest tests.test_live_order_reconciliation tests.test_order_unknown_reconciliation` passed.
- `python -m unittest tests.test_pathb_runtime` passed.
- `python -m unittest tests.test_dashboard_pathb` passed.
- `python -m py_compile trading_bot.py bot\market_utils.py runtime\pathb_runtime.py config\v2.py tests\test_order_equity_reconciliation_improvement.py tests\test_pathb_runtime.py` passed.
- `python -m unittest tests.test_order_equity_reconciliation_improvement` passed.
- `python -m unittest tests.test_live_order_reconciliation tests.test_order_unknown_reconciliation` passed.
- `python -m unittest tests.test_pathb_safety tests.test_v2_phase1` passed.
- `python -m unittest tests.test_dashboard_pathb` passed.

## Remaining Items

- Manual KIS cleanup for `006340` and `047040` remains an operator action.
- SafetyContext audit-field expansion remains intentionally deferred.
- Dashboard display for broker/local mismatch remains a follow-up item.
- Strategy follow-ups remain separate tuning work.
