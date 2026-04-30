# ORDER_UNKNOWN Auto-Resolve and PathB Pre-Close Carry Plan - 2026-04-30

## Goal

Fix two live-operation failure modes without weakening hard risk controls.

1. `ORDER_UNKNOWN` must not keep blocking an entire market across sessions when broker truth can prove the stale state is gone.
2. PathB pre-close handling must not sell a clean profitable position only because the clock reached the intraday cutoff; it should ask whether the position should be carried, while hard stops remain immediate.

This plan is intentionally split into implementation phases with validation gates. Do not start broad refactors before the Phase 1 and Phase 2 acceptance checks pass.

## Current Failure Summary

### Issue A - KR ORDER_UNKNOWN Market Pause

Observed state:

- `state/live_v2_order_unknown.json` has `paused_markets.KR`.
- `market_consecutive_unknown.KR = 2`.
- The market pause reason is `two_consecutive_order_unknown`.
- New KR entries are blocked with `ORDER_UNKNOWN_UNRESOLVED scope=market`.

Problem orders from the 2026-04-29 KR session:

- `0007603600` / `006340`: local pending, broker open false, unresolved.
- `0008147800` / `047040`: local pending, broker open false, unresolved.
- Same ticker stale broker-only entries also exist:
  - `0008146000` / `006340`: broker-only open-order record.
  - `0008646700` / `047040`: broker-only open-order record.

Current code gaps:

- `PathBRuntime._order_unknown_runs()` queries only the current session date. Previous-session `ORDER_UNKNOWN` rows are not reconciled at the next session open.
- `PathBRuntime.reconcile_order_unknowns()` updates PathB DB state, but does not clear `OrderUnknownEscalator.paused_markets`, `paused_tickers`, or `orders`.
- `PathBRuntime._order_unknown_blocked()` checks `bot.v2_order_unknown.should_block_market(market)` first. If `paused_markets.KR` survives, the market remains blocked regardless of PathB DB reconciliation.

### Issue B - PathB Pre-Close Forced Sell

Observed state:

- US PathB positions `INTC`, `NXPI`, and `BE` were sold around 04:49 KST.
- All were closed with `CLOSED_CLAUDE_PRICE_PRE_CLOSE`.
- The sell was caused by `PATHB_INTRADAY_ONLY=true` and the pre-close cutoff, not by a fresh Claude sell opinion.

Current code behavior:

- `PathBRuntime.scan_exits()` checks hard stop, Claude target, Claude stop, then pre-close force exit.
- `_pre_close_force_exit()` returns true when `pathb_intraday_only` is true and `int(minutes_to_close) <= new_entry_cutoff_minutes_before_close`.
- `pre_close_exit_needed()` only checks PathB run status and minutes-to-close.
- No carry review is performed before pre-close sell.

## Non-Code Operator Checks

Before enabling auto-clear on a live account, the operator should verify the currently stuck KR orders once in KIS app/HTS/API:

- `0007603600` / `006340`
- `0008147800` / `047040`
- `0008146000` / `006340`
- `0008646700` / `047040`

If any order is still open at the broker, cancel it manually or allow the new auto-resolve flow to restore it to pending. If broker truth proves no open order and no position, it can be auto-cleared.

## Design Principles

- Broker truth resolves order-state uncertainty; Claude must not resolve `ORDER_UNKNOWN`.
- Claude may decide carry-vs-sell only for clean positions with trusted broker truth.
- Hard risk exits are never delegated to Claude.
- A carry approval must not disable target, stop, hard stop, or loss-cap monitoring before the market closes.
- PathB origin attribution must be preserved even if the carried position is managed by the general hold-review layer on the next session.

## Phase 1 - ORDER_UNKNOWN Cross-Session Auto-Resolve

### Files

- `runtime/pathb_runtime.py`
- `execution/order_state.py`
- `trading_bot.py`
- tests under `tests/`

### 1. Add cross-session PathB unknown query

Add a method similar to:

```text
_order_unknown_runs_cross_session(market, lookback_sessions=N)
```

Required filters:

- `market` matches.
- `runtime_mode` matches.
- `path_type = claude_price`.
- `status = ORDER_UNKNOWN`.
- Include only unresolved or retryable resolutions:
  - empty or missing resolution
  - `ambiguous_broker_truth`
  - `broker_no_evidence`
  - `broker_truth_unavailable`
  - `session_end_unresolved`
- Exclude already-handled resolutions:
  - `path_a_origin_possible`
  - `permanent_order_reject`
  - `pathb_fill_recovered`
  - `pathb_open_order_recovered`
  - `auto_cleared_no_broker_evidence`
- Limit by recent session window so old archived rows are not reprocessed forever.

### 2. Add session-open reconcile entry point

Add:

```text
reconcile_order_unknowns_at_open(market)
```

Expected order:

1. Force refresh broker truth for the market.
2. Reconcile current-session and cross-session PathB `ORDER_UNKNOWN` runs.
3. Call `OrderUnknownEscalator.auto_clear_at_session_open(...)`.
4. Log remaining market/ticker blocks.
5. Return a structured summary.

### 3. Add OrderUnknownEscalator auto-clear

Add to `execution/order_state.py`:

```text
auto_clear_at_session_open(market, broker_snapshot)
```

It must evaluate both:

- `paused_tickers[market]`
- `orders` entries for the same market/ticker, including:
  - `ORDER_UNKNOWN_UNRESOLVED`
  - `BROKER_ONLY_OPEN_ORDER`
  - duplicate-open-order related records

Auto-clear is allowed only when broker truth is fully trusted:

- snapshot missing is false
- snapshot stale is false
- snapshot error is empty
- open-order query succeeded
- position query succeeded
- fill query succeeded, or the flow explicitly treats fill recovery as unavailable and uses no-position/no-open-order clear only

Per ticker/order resolution rules:

- Broker open order exists for ticker or order number:
  - keep block
  - update order record as `RESTORED_TO_PENDING` or `BROKER_OPEN_ORDER_CONFIRMED`
  - do not clear market pause unless every ticker is resolved
- Broker position exists:
  - keep or hand off to recovery flow
  - mark `RESTORED_TO_POSITION` only when enough evidence exists to recover local state
- Broker fill evidence exists by order number:
  - mark `RESTORED_TO_POSITION` or `RECOVERED`
  - local PathB state must be updated by the PathB reconcile layer
- No open order, no current position, no fill evidence:
  - mark `AUTO_CLEARED_NO_BROKER_EVIDENCE`
  - set `local_pending=false`
  - set `broker_open=false`
  - set `resolved_at`
  - remove `paused_tickers[market][ticker]`

Market release rules:

- If no unresolved paused ticker remains for the market:
  - remove `paused_markets[market]`
  - set `market_consecutive_unknown[market] = 0`
- Do not clear global halt, panic halt, manual halt, or unrelated hard-risk pauses.

### 4. Connect from session_open

Replace the current session-open call:

```text
self.pathb.reconcile_order_unknowns(market, force=True)
```

with:

```text
self.pathb.reconcile_order_unknowns_at_open(market)
```

This must run on both scheduled `session_open` and `startup_mid_session`.

### 5. Required logs

Add one concise log summary:

```text
[ORDER_UNKNOWN open reconcile] market=KR checked_pathb=... auto_cleared=... restored_pending=... restored_position=... remaining_tickers=... market_pause_cleared=...
```

Also preserve enough detail for dashboard/error inspection:

- order number
- ticker
- previous resolution
- new resolution
- broker evidence booleans
- release decision

## Phase 1 Validation

Unit cases:

- Previous-session `ORDER_UNKNOWN` with `ambiguous_broker_truth` is included by cross-session reconcile.
- Previous-session `ORDER_UNKNOWN` with `path_a_origin_possible` is excluded.
- Previous-session `ORDER_UNKNOWN` with `permanent_order_reject` is excluded.
- Broker no evidence clears ticker pause and order records.
- Broker no evidence for all tickers clears `paused_markets[market]` and resets `market_consecutive_unknown`.
- Broker open order keeps ticker block and does not clear market if any ticker remains blocked.
- Broker snapshot stale/error/missing does not auto-clear.
- Same ticker stale `BROKER_ONLY_OPEN_ORDER` records are resolved with the same ticker group.

Integration smoke:

- Seed KR state with the 2026-04-29 stuck order pattern.
- Run `reconcile_order_unknowns_at_open("KR")` with clean broker snapshot.
- Assert:
  - `paused_markets.KR` removed.
  - `market_consecutive_unknown.KR == 0`.
  - `006340` and `047040` removed from `paused_tickers.KR`.
  - matching `orders` entries have resolved status and `resolved_at`.
  - new KR PathB entry gate no longer returns `ORDER_UNKNOWN_UNRESOLVED scope=market`.

## Phase 2 - PathB Pre-Close Carry Review

### Files

- `runtime/pathb_runtime.py`
- `execution/claude_price_sell_manager.py` if needed
- `trading_bot.py` if a hold-advisor helper is reused
- tests under `tests/`

### 1. Add carry review timing

Add a carry-review check in `PathBRuntime.scan_exits()` before the pre-close force-sell window.

Suggested timing:

- Carry review trigger: `minutes_to_close <= 15`
- Force-sell cutoff remains: existing `new_entry_cutoff_minutes_before_close`, currently 10

Do not call Claude every scan. Cache the result in `plan_json`.

Cache fields:

- `carry_reviewed_at`
- `carry_decision`: `SELL` or `CARRY`
- `carry_reason`
- `carry_confidence`
- `carry_source = pathb_preclose`
- `carry_review_error` if failed

### 2. Keep PathB status active until close

Important state rule:

- At T-15, do not change PathB run status to `CARRIED`.
- Keep status as `FILLED` or `PARTIAL_FILLED`.

Reason:

- `scan_exits()` only monitors `FILLED` and `PARTIAL_FILLED`.
- If status changes to `CARRIED` at T-15, hard stop, loss cap, target, and stop monitoring can disappear for the final minutes.

### 3. Carry gate before Claude call

Only ask Claude if all gate conditions pass:

- broker position truth is trusted
- local position exists and maps to the PathB run
- no active hard block for the ticker
- no broker mismatch or order uncertainty
- current loss is within configured carry loss floor
- market mode is not a hard risk-off mode
- overnight count and exposure limits pass

If any gate fails:

- cache `carry_decision=SELL`
- include `carry_review_error` or `carry_reject_reason`

### 4. Claude carry decision

Prompt should ask for a strict binary decision:

- `SELL`
- `CARRY`

Input context:

- ticker, market, strategy/path
- entry price, current price, PnL percent
- target and stop
- hard stop/loss-cap status
- current market mode
- latest hold-advisor opinion if available
- minutes to close
- overnight exposure count

Failure behavior:

- timeout -> `SELL`
- malformed response -> `SELL`
- confidence below threshold -> `SELL`

### 5. Read carry cache in pre-close force exit

Update `_pre_close_force_exit(...)` logic:

- If hard risk exit already triggered, this method is irrelevant because the hard exit happens before pre-close.
- If pre-close cutoff is reached:
  - cached `carry_decision=CARRY` -> return false for pre-close force sell
  - cached `carry_decision=SELL` -> return true
  - no cache / stale cache / failed review -> return true

This means `CARRY` only skips the time-based pre-close sell. It does not skip hard stop, loss cap, Claude stop, or Claude target.

### 6. Confirm carry at session_close

At `session_close`, for still-open PathB positions with `carry_decision=CARRY`:

- mark path run as `CARRIED` or `CARRIED_OUT`
- set `carried_at`
- preserve `origin_path_run_id`
- preserve `buy_path=path_b`
- set position metadata:
  - `carry_source=pathb_preclose`
  - `origin_path_run_id`
  - `buy_path=path_b`

The position is not converted to Path A. It becomes a PathB-origin position managed by the general hold-review layer.

### 7. Next session handling

On the next `session_open`:

- `_pre_session_position_review()` sees the carried position.
- hold_advisor decides SELL, HOLD, or TRAIL.
- If SELL, sell through the existing pre-session sell queue after startup guard.
- If HOLD/TRAIL, continue normal position management.
- Attribution remains PathB origin.

## Phase 2 Validation

Unit cases:

- T-15 carry review writes cache but keeps status `FILLED`.
- T-10 with cached `CARRY` skips only pre-close force sell.
- T-10 with cached `SELL` triggers pre-close sell.
- T-10 with no cache triggers pre-close sell.
- Claude timeout/malformed response caches or implies `SELL`.
- Hard stop still sells even if cached `CARRY`.
- Claude target still sells even if cached `CARRY`.
- Loss cap still sells even if cached `CARRY`.

Integration smoke:

- Seed a PathB `FILLED` position with positive PnL and trusted broker truth.
- Simulate T-15 and Claude `CARRY`.
- Assert status remains `FILLED`.
- Simulate T-10.
- Assert no `CLOSED_CLAUDE_PRICE_PRE_CLOSE` sell is submitted.
- Simulate session_close with position still open.
- Assert run marked `CARRIED` or `CARRIED_OUT` and position metadata preserves PathB origin.
- Simulate next session_open and assert hold-advisor review can queue SELL or keep HOLD.

## Phase 3 - Reporting and Ops Visibility

Add or verify visibility in dashboard/Telegram/ops summary:

- ORDER_UNKNOWN auto-cleared count.
- Remaining unresolved tickers.
- Market pause clear event.
- Carry review decisions.
- PathB carried positions.
- Forced pre-close sells versus carried positions.
- Postmortem should separate:
  - hard risk exits
  - Claude target/stop exits
  - pre-close forced sells
  - pre-close carry approvals
  - ORDER_UNKNOWN auto-clear events

## Phase 4 - Final QA Checklist

Run focused tests first:

- `tests/test_order_unknown_reconciliation.py`
- `tests/test_pathb_runtime.py`
- `tests/test_pathb_sell.py`
- `tests/test_pathb_sell_reconcile.py`
- `tests/test_order_equity_reconciliation_improvement.py`
- any new tests added for auto-clear and carry review

Then run contract tests:

- `tests/test_pathb_claude_contract.py`
- `tests/test_path_execution_arbiter.py`
- `tests/test_v2_phase6.py`

Manual QA checklist:

- Start from a clean broker snapshot fixture.
- Verify no sell/cancel/reconcile path is blocked by `ORDER_UNKNOWN` new-buy gate.
- Verify only new buy entries are blocked while unresolved uncertainty remains.
- Verify a stale KR market pause can clear automatically only after trusted broker truth.
- Verify a carried PathB position does not lose stop/target monitoring before close.
- Verify next session review handles carried positions.

## MD Coverage Check

User-requested item coverage:

- Full detailed list-up: covered in Phases 1-4.
- ORDER_UNKNOWN auto release: covered in Phase 1.
- Cross-session missing lookup gap: covered in Phase 1.1 and validation.
- PathB reconcile / OrderUnknownEscalator disconnect: covered in Phase 1.2-1.4.
- Orders dictionary cleanup: covered in Phase 1.3.
- Previous-day fill limitation: covered in Phase 1.3.
- Pre-close carry review: covered in Phase 2.
- Do not mark CARRIED at T-15: covered in Phase 2.2.
- Read cached carry decision at T-10: covered in Phase 2.5.
- Session_close carry confirmation: covered in Phase 2.6.
- Development-stage validation: covered in Phase 1 Validation and Phase 2 Validation.
- Final QA: covered in Phase 4.
- Missing-part comparison before implementation: this section is the comparison baseline.

## Known Deferred Work

- Full historical fill lookup for previous-day order-number recovery may require broker API support beyond the current `today_fills` snapshot.
- A richer PathB carried-position performance report can be added after initial live data is collected.
- `CARRIED` status must be added carefully to reporting lists without treating it as an active scan status before session close.

## Proceed Criteria

Before implementation starts:

- Re-read this MD and confirm no user-requested behavior is outside the coverage checklist.
- Confirm no planned change clears hard halt, panic halt, manual halt, or broker-untrusted quarantine.
- Confirm `CARRIED` is not used as a scan-active status before session close.
- Confirm Phase 1 can be implemented and tested without touching unrelated strategy selection logic.
- Confirm Phase 2 can be implemented and tested without changing PathB entry logic.

Before final handoff:

- All Phase 1 and Phase 2 validation cases must pass.
- Final QA checklist must be run or explicitly marked with the reason it could not run.
- This MD must be compared against the actual implementation.
- Any difference must be listed as either completed differently, intentionally deferred, or still missing.

## Implementation Order

1. Implement Phase 1 cross-session ORDER_UNKNOWN auto-resolve.
2. Run Phase 1 validation and confirm KR market block can clear from the reproduced stuck state.
3. Implement Phase 2 pre-close carry review cache and force-exit behavior.
4. Run Phase 2 validation and confirm hard exits still bypass carry.
5. Add Phase 3 visibility if not already covered by existing summary output.
6. Run Phase 4 final QA.
7. Re-open this MD and compare completed behavior against the coverage checklist before considering the task done.

## Implementation Result - 2026-04-30

Implemented files:

- `execution/order_state.py`
- `runtime/pathb_runtime.py`
- `trading_bot.py`
- `tests/test_order_unknown_reconciliation.py`
- `tests/test_order_equity_reconciliation_improvement.py`
- `tests/test_pathb_runtime.py`

Phase 1 completed:

- Added `PathBRuntime.reconcile_order_unknowns_at_open(market)`.
- Added cross-session PathB `ORDER_UNKNOWN` lookup with a 5-session recent-session cap.
- Included only retryable unresolved resolutions:
  - empty or missing
  - `ambiguous_broker_truth`
  - `broker_no_evidence`
  - `broker_truth_unavailable`
  - `session_end_unresolved`
- Excluded handled rows such as `path_a_origin_possible`, `permanent_order_reject`, recovered rows, and auto-cleared rows.
- Added `OrderUnknownEscalator.auto_clear_at_session_open(market, broker_snapshot)`.
- Added trusted-snapshot guard: no auto-clear when snapshot is missing, stale, or has an error.
- Added ticker-group cleanup for `ORDER_UNKNOWN_UNRESOLVED`, `BROKER_ONLY_OPEN_ORDER`, `CANCEL_REQUESTED`, and duplicate-open-order records.
- Clean broker no-evidence path marks records as `AUTO_CLEARED_NO_BROKER_EVIDENCE`, sets `local_pending=false`, `broker_open=false`, records `resolved_at`, and removes ticker pause.
- PathB DB no-evidence-at-open marks the stale PathB run `CANCELLED` with `order_unknown_resolution=auto_cleared_no_broker_evidence`.
- Broker position or fill evidence remains recoverable through the existing PathB reconcile path.
- A single broker open order is treated as known pending: order record becomes `RESTORED_TO_PENDING`, ticker-level block remains, and market-level pause can clear when there is no unresolved ambiguity.
- Duplicate open orders remain unresolved and keep the market pause.
- `trading_bot.session_open()` now calls `self.pathb.reconcile_order_unknowns_at_open(market)`.

Phase 2 completed:

- Added pre-close carry review in `PathBRuntime.scan_exits()` before time-based pre-close force sell.
- Carry review writes cache fields into `plan_json` and keeps status `FILLED` or `PARTIAL_FILLED`.
- Existing hard exits still run first:
  - loss cap
  - hard stop
  - Claude stop
  - Claude target
- Carry review uses broker-truth and local-position gates before calling `hold_advisor`.
- `hold_advisor` actions are normalized:
  - `SELL` -> `SELL`
  - any non-SELL action such as `HOLD` or `TRAIL` -> `CARRY`
- Failed carry review or rejected gate caches `SELL` plus `carry_review_error`.
- `_pre_close_force_exit()` now skips only time-based pre-close sell when cached `carry_decision=CARRY`.
- No cache or cached `SELL` still follows existing pre-close sell behavior.
- The review handles the `int(minutes_to_close)` edge by allowing carry review at values like `10.9` minutes before the integer cutoff would force a sell.
- `trading_bot.session_close()` now calls `finalize_carried_positions_at_session_close(market)`.
- Session-close carry finalization marks PathB runs as `CARRIED_OUT` and writes position metadata:
  - `carry_source=pathb_preclose`
  - `origin_path_run_id`
  - `buy_path=path_b`

## Final QA Result - 2026-04-30

Compile check:

- `python -m compileall execution\order_state.py runtime\pathb_runtime.py trading_bot.py` - passed

Focused and contract test run:

- `python -m unittest tests.test_order_unknown_reconciliation tests.test_order_equity_reconciliation_improvement tests.test_pathb_runtime tests.test_pathb_sell tests.test_pathb_sell_reconcile tests.test_pathb_claude_contract tests.test_live_pathb_stuck tests.test_path_execution_arbiter tests.test_v2_phase6`
- Result: 61 tests passed

Additional checks:

- `git diff --check` passed with line-ending warnings only.

## MD Comparison After Implementation

Completed as planned:

- Cross-session `ORDER_UNKNOWN` lookup.
- Session-open broker-truth refresh and reconcile entry point.
- Escalator cleanup connected to PathB session-open reconcile.
- Orders dictionary cleanup for unresolved, broker-only, cancel-requested, and duplicate-open-order related records.
- Broker no-evidence auto-clear.
- Market-level pause clear and `market_consecutive_unknown` reset after trusted broker truth.
- Carry review cache before pre-close forced sell.
- Status remains `FILLED` or `PARTIAL_FILLED` during the final minutes.
- Cached `CARRY` skips only the time-based pre-close sell.
- Target and stop exits still override cached `CARRY`.
- Session-close carry handoff to `CARRIED_OUT`.
- PathB origin metadata preserved on carried positions.

Completed differently:

- For a single broker open order, implementation keeps the ticker-level block but clears the market-level pause when no ambiguous ticker remains. This is more practical than keeping the whole market blocked for a known pending order.
- PathB no-evidence-at-open uses status `CANCELLED` with `order_unknown_resolution=auto_cleared_no_broker_evidence`; no new `AUTO_CLEARED` status was introduced.
- Carry review reuses existing `minority_report.hold_advisor` instead of adding a new Claude prompt endpoint. The result is normalized to binary `SELL` or `CARRY`.

Intentionally deferred:

- Full previous-day fill lookup by order number. Current broker snapshot supports current positions, open orders, and `today_fills`; previous-day fill recovery still needs broker API support or an additional historical-fill provider.
- Dedicated overnight exposure and carry loss-floor config. Current gate covers trusted broker truth, local mapped position, current price, positive quantity, and active order-unknown blocks. Existing hard exits still protect downside before carry review.
- Reporting/dashboard expansion beyond structured logs and plan fields.

No remaining blocker found against the requested two fixes.
