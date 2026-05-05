# P0 PathB Fill Dashboard Follow-up

## Current Status - 2026-05-05

Keep this plan active. The code-level items in this plan are present, but the current focused QA command is not clean:

- `python -m pytest tests/test_pathb_runtime.py tests/test_kis_ws_fill_notice.py tests/test_dashboard_kis_profile.py tests/test_live_order_reconciliation.py tests/test_live_preflight_credentials.py tests/test_p0_data_quality.py tests/test_p0_data_quality_backfill.py -q`
- Result: `71 passed, 2 failed, 2 warnings`.
- Failing cases:
  - `tests/test_pathb_runtime.py::PathBRuntimeTests::test_register_from_selection_meta_blocks_when_active_order_unknown_exists`
  - `tests/test_pathb_runtime.py::PathBRuntimeTests::test_submit_buy_same_day_reentry_guard_blocks_before_order`

Do not delete this plan until PathB runtime regression QA is green again.

## Purpose

Close the remaining code-level gaps after KIS market profile isolation and fill idempotency hardening.

This pass is limited to:

1. PathB real buy/sell order token routing tests.
2. Fill ledger max-size eviction.
3. Dashboard `kis_profile` JSON/API response tests.

## 1. PathB Real Order Token Tests

### Goal

Ensure US PathB order paths use the market token from `bot._token_for_market("US")`, not a legacy KR token.

### Development

- Add tests in `tests/test_pathb_runtime.py`.
- Use a fake bot that returns `token-US-0` from `_token_for_market("US")`.
- Patch `runtime.pathb_runtime.precheck_order`.
- Patch `runtime.pathb_runtime.place_order`.
- Exercise PathB entry order path.
- Exercise PathB sell order path.

### Acceptance

- Buy path:
  - `precheck_order(..., "token-US-0", market="US")`
  - `place_order(..., "token-US-0", market="US")`
- Sell path:
  - `precheck_order(..., "token-US-0", market="US")`
  - `place_order(..., "token-US-0", market="US")`

## 2. Fill Ledger Max-Size Eviction

### Goal

Prevent `_applied_fill_keys` from growing without bound in long-running sessions.

### Development

- Update `trading_bot._fill_ledger_seen_or_mark`.
- Add `FILL_LEDGER_MAX_KEYS` env config.
- Default: `5000`.
- Use `set` for membership and `deque` for insertion order.
- Lazy initialize both:
  - `_applied_fill_keys`
  - `_applied_fill_key_order`
- When evicting the oldest key from deque, remove it from the set as well.

### Implementation Notes

- Do not allow `max_keys <= 0` to disable eviction accidentally.
- Clamp to a sane minimum, e.g. `max(100, configured_value)`.
- Check duplicate membership before appending to deque so duplicate keys do not create duplicate order entries.
- If `_applied_fill_keys` already exists but `_applied_fill_key_order` is missing, initialize the deque safely.

### Acceptance

- Duplicate key still returns `True`.
- New key returns `False`.
- Oldest key is evicted when max size is exceeded.
- Set and deque remain in sync.
- Missing `_applied_fill_key_order` does not raise.

## 3. Dashboard `kis_profile` JSON/API Tests

### Goal

Ensure `kis_profile` survives JSON serialization and, when practical, API response path.

### Development

- Extend `tests/test_dashboard_kis_profile.py`.
- Assert `_broker_snapshot()` output is JSON serializable with `kis_profile`.
- Assert `kis_profile.US.credential_mode` remains after `json.dumps` / `json.loads`.
- If the endpoint path can be exercised cheaply, use Flask test client to confirm an API response includes `kis_profile`.

### Acceptance

- `json.loads(json.dumps(snapshot))["kis_profile"]["US"]["credential_mode"]` exists.
- Endpoint/API response includes `kis_profile` if the route returns broker snapshot data directly.

## QA Commands

- `python -m py_compile trading_bot.py runtime/pathb_runtime.py dashboard/dashboard_server.py`
- `python -m pytest tests/test_pathb_runtime.py tests/test_kis_ws_fill_notice.py tests/test_dashboard_kis_profile.py -q`
- `python -m pytest tests/test_live_order_reconciliation.py tests/test_broker_truth_snapshot.py tests/test_live_preflight_credentials.py -q`
- `git diff --check`

## Completion

- Compare implementation and tests against this MD.
- Patch any omission found during comparison.
- Keep this MD as the follow-up QA record unless a cleanup request says to delete it.
