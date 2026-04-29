# Path B Sell Truth Reconcile Plan - 2026-04-28

## Goal

Keep live account truth and local V2 lifecycle truth aligned.

This is not a performance tuning patch. It fixes live operational correctness:

- Do not mark a Path B sell as `CLOSED` until broker fill history confirms it.
- Do not treat `balance=0` as a sell confirmation by itself.
- Do not include unconfirmed local-only sells in realized PnL.
- Stop retry loops for permanent unsupported-symbol/order failures.
- Keep Path A aware of Path B price cancellations as shadow evidence.

## Phase 1 - US PRE_CLOSE Time Anchor

Files:

- `bot/market_utils.py`
- `tests/test_market_utils.py`

Required behavior:

- US session close is 05:00 KST on the next calendar day for any time after 05:00 KST.
- At 22:24 KST, minutes to close must be positive, not negative.

Validation:

- 22:24 KST -> close is next-day 05:00.
- 22:30 KST -> close is next-day 05:00.
- 00:30 KST -> close is same-day 05:00.
- 04:55 KST -> close is same-day 05:00.
- 05:01 KST -> close is next-day 05:00.

## Phase 2 - Path B Sell State Machine

Files:

- `runtime/pathb_runtime.py`
- `execution/claude_price_sell_manager.py`
- `risk_manager.py`
- `tests/test_pathb_sell_reconcile.py`

Required behavior:

Old flow to remove:

```text
sell order sent -> immediate balance check -> CLOSED or ORDER_UNKNOWN
```

New flow:

```text
sell signal
-> broker sell order sent
-> SELL_SENT
-> later reconcile ccld + open orders + balance
-> CLOSED only when ccld confirms full sell fill
```

State rules:

- `ccld sell_qty >= requested sell_qty` -> `CLOSED`.
- `0 < ccld sell_qty < requested sell_qty` -> `SELL_PARTIAL_FILLED`.
- `ccld none + open_order exists` -> `SELL_ACKED`.
- `ccld none + open_order none + balance exists` -> `SELL_ACKED`, retry until TTL.
- `ccld none + open_order none + balance missing/zero` -> `SELL_ACKED` with `ambiguous_broker_truth`, retry until TTL.
- TTL exceeded without broker fill evidence -> `ORDER_UNKNOWN`.

PnL rules:

- Realized PnL must be based on confirmed broker fill price and quantity.
- `mark_closed()` must receive broker fill evidence.
- `balance=0` alone must never finalize PnL.

Duplicate-sell guard:

- A local Path B position with `pathb_closing` must not be emitted again by normal exit candidate generation.

## Phase 3 - Pending Sell Reconciliation

Files:

- `runtime/pathb_runtime.py`
- `tests/test_pathb_sell_reconcile.py`

New method:

```text
reconcile_sell_pending(market, force=False)
```

Target statuses:

- `SELL_SENT`
- `SELL_ACKED`
- `SELL_PARTIAL_FILLED`

Default TTLs:

- `PATHB_SELL_PENDING_TTL_MINUTES=15`
- `PATHB_SELL_PARTIAL_TTL_MINUTES=30`

Validation cases:

- SNAP shape: ccld none + balance zero -> not CLOSED.
- CLF/AMZN shape: ccld full sell -> CLOSED.
- partial sell: ccld partial -> SELL_PARTIAL_FILLED.
- no ccld but open order -> SELL_ACKED.
- no ccld after TTL -> ORDER_UNKNOWN.

## Phase 4 - Stale FILLED Recovery

Files:

- `runtime/pathb_runtime.py`
- `tests/test_pathb_sell_reconcile.py`

Required behavior:

For Path B runs still in `FILLED`/`PARTIAL_FILLED`:

- broker position exists -> keep open.
- broker position missing + broker ccld sell fill exists -> recover `CLOSED`.
- broker position missing + no ccld sell fill -> `ORDER_UNKNOWN` / `ambiguous_broker_truth`.

This handles the case where a sell happened outside `_submit_sell()` but Path B status stayed `FILLED`.

## Phase 5 - Permanent ORDER_UNKNOWN Failures

Files:

- `runtime/pathb_runtime.py`
- `tests/test_order_unknown_reconciliation.py`

Required behavior:

For permanent order failures such as:

- `해당종목정보가 없습니다`
- exchange mapping failure
- unsupported symbol
- symbol not found

Do not schedule a 5-minute retry loop.

Record in `plan_json`:

```json
{
  "order_unknown_resolution": "permanent_order_reject",
  "next_broker_truth_recheck_at": ""
}
```

Status remains `ORDER_UNKNOWN`; no new status enum.

## Phase 6 - Path B cancel_if_open_above Shadow

Files:

- `execution/path_arbiter.py`
- `tests/test_path_execution_arbiter.py`

Required behavior:

- Do not add `CANCELLED` to generic active statuses.
- Add a separate same-session lookup for Path B runs cancelled by `cancel_if_open_above`.
- If Path A later enters the same ticker, allow it but add shadow payload:

```json
{
  "pathb_cancel_price_chase": true,
  "pathb_cancel_reason": "cancel_if_open_above"
}
```

No hard block in this patch.

## Phase 7 - Session Close Minor Fixes

Files:

- session close / review / dashboard code if needed

Required behavior:

- `logs/funnel` directory must exist before writing.
- `_get_usd_krw_cached` references must resolve in the module where used.
- Unconfirmed sells must not appear as realized PnL.

## Phase 8 - Dashboard / Telegram Verification

Files:

- `interface/v2_ops_summary.py`
- `interface/v2_telegram.py`
- `dashboard/dashboard_server.py`

Required behavior:

- Path A / Path B labels remain visible.
- `SELL_SENT`, `SELL_ACKED`, `SELL_PARTIAL_FILLED`, `ORDER_UNKNOWN` are displayed clearly.
- Broker truth and local status mismatch are visible.
- Unconfirmed sell PnL is not shown as realized.

## Final QA

Required commands:

```powershell
pytest tests/test_market_utils.py
pytest tests/test_pathb_sell_reconcile.py
pytest tests/test_order_unknown_reconciliation.py
pytest tests/test_path_execution_arbiter.py
pytest tests/test_pathb_runtime.py
pytest tests/test_pathb_sell.py
pytest
```

Manual QA:

- Read this document again after implementation.
- Verify every phase has either code, test, or an explicit "already covered" note.
- If a planned item is missing, patch it before final response.

Go criteria:

- Test FAIL count: 0 for targeted tests.
- No ccld-less `CLOSED` in new Path B sell flow.
- No `balance=0` only sell confirmation.
- No permanent unsupported-symbol retry loop.
- No PRE_CLOSE trigger before real US close window.

## Implementation Verification

Completed items:

- Phase 1 implemented in `bot/market_utils.py` with `tests/test_market_utils.py`.
- Phase 2 and Phase 3 implemented in `runtime/pathb_runtime.py`, `execution/claude_price_sell_manager.py`, `risk_manager.py`, and `tests/test_pathb_sell_reconcile.py`.
- Session close now calls `refresh_broker_truth()`, `finalize_sell_pending_at_session_close()`, and `reconcile_filled_positions()` before ORDER_UNKNOWN finalization.
- `finalize_sell_pending_at_session_close()` forces unconfirmed `SELL_SENT`/`SELL_ACKED`/`SELL_PARTIAL_FILLED` into `ORDER_UNKNOWN` instead of carrying them across the session boundary.
- Phase 4 implemented through `reconcile_filled_positions()` and covered by `tests/test_pathb_sell_reconcile.py`.
- Phase 5 implemented with `permanent_order_reject` resolution and covered by `tests/test_order_unknown_reconciliation.py`.
- Phase 6 implemented as shadow-only arbiter evidence and covered by `tests/test_path_execution_arbiter.py`.
- Phase 7 checked: `logs/funnel` already uses `make_parents=True`; `_get_usd_krw_cached` in `trading_bot.py` was replaced with `self.usd_krw_rate`.
- Phase 8 checked by `tests/test_dashboard_pathb.py` and `tests/test_telegram_path_labels.py`.

QA results:

```powershell
pytest tests/test_market_utils.py
# 6 passed

pytest tests/test_pathb_sell_reconcile.py
# 5 passed

pytest tests/test_order_unknown_reconciliation.py
# 6 passed

pytest tests/test_path_execution_arbiter.py
# 9 passed

pytest tests/test_market_utils.py tests/test_pathb_sell_reconcile.py tests/test_order_unknown_reconciliation.py tests/test_path_execution_arbiter.py tests/test_pathb_runtime.py tests/test_pathb_sell.py
# 34 passed

pytest tests/test_dashboard_pathb.py tests/test_telegram_path_labels.py tests/test_live_config_sources.py tests/test_live_db_integrity.py tests/test_live_order_safety.py tests/test_broker_truth_snapshot.py tests/test_pathb_control.py tests/test_pathb_adapter.py tests/test_pathb_safety.py
# 22 passed

pytest tests
# 119 passed, 2 warnings
```

Root `pytest` note:

- Running plain `pytest` from the repository root currently collects `_sim_test.py`.
- `_sim_test.py` attempts a live KIS token network call during collection and fails in this sandbox with `WinError 10013`.
- The actual `tests/` suite passes.

Manual document comparison:

- Every phase in this document has a corresponding code change, passing test, or explicit already-covered note.
- The late review issue about session-close `SELL_SENT`/`SELL_ACKED` carryover was patched and covered by `test_session_close_pending_sell_without_ccld_becomes_order_unknown_before_ttl`.
- No additional missing item was found after the final comparison.
