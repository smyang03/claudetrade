# Shadow Audit Review Fix Plan - 2026-04-30

## Goal

Resolve review findings that can corrupt shadow-audit data or let audit work block session-boundary processing.

This change must not alter live trading decisions, order sizing, order submission, fill handling, sell decisions, broker calls, Claude calls, or market-data calls.

## Already Confirmed In Current Code

- `make_episode_id()` includes `ticker`.
- `_audit_active_episode()` cache key includes `session_date`.
- close outcome requires a post-signal price sample instead of reusing the signal-time sample.
- `tools/shadow_audit_gap.py` strips `AS alias` before building `GROUP BY`.
- `ShadowAuditStore.write_events([])` returns early.

These items still need regression coverage where practical.

## Implementation Items

### 1. Batch Outcome Writes

Issue:

- `ShadowOutcomeUpdater.update_pending()` writes outcomes once per signal.
- If the audit DB is locked, timeout can accumulate by signal count during session close.

Fix:

- Collect all outcome events in memory during the read pass.
- After closing the read connection, call `write_events(all_events)` once.
- Skip write entirely when `all_events` is empty.
- Set `summary["written"]` from `write_events()` return value.
- Keep per-signal calculation errors separate from final batch write errors.

Operational safety:

- No trading decisions touched.
- No new API calls.
- Reduces session-close blocking risk.

### 2. Close Active ORDER_UNKNOWN Pause Episode

Issue:

- Session-open reconcile currently creates a separate cleared episode, leaving the original `ORDER_UNKNOWN_PAUSE` open.

Fix:

- Add `_audit_close_active_episode(...)`.
- It looks up the in-memory active episode cache, emits the same `episode_id` with `ended_at`, `status="cleared"`, and `clear_reason`, then removes the cache entry.
- Session-open clear path calls this for `ORDER_UNKNOWN_PAUSE`.
- If cache is empty, it silently skips.

Known limitation:

- Cross-restart pause closure is not handled in this phase because the active episode cache is in memory.
- DB lookup fallback is intentionally deferred to avoid adding complexity to the runtime path.

Operational safety:

- Best-effort audit emit only.
- Exceptions swallowed.

### 3. Include Signal Price In Outcome Extrema

Issue:

- If the signal-time sample is missing/dropped and the first later sample is above signal price, drawdown can become positive.

Fix:

- Build extrema candidates as `[signal_price] + window_prices`.
- Do not add clamp logic; including signal price makes runup/drawdown bounds natural.

Operational safety:

- Pure audit math change.

### 4. Record Path A Passive Signal-Check Prices

Issue:

- Path A blocked signals may have only the fired price sample, even when later signal checks already observed prices.

Fix:

- Emit a passive audit price sample in `_entry_timing_signal_check()`.
- Use the existing `price` parameter only.
- Do not call `get_price()` or any other API.
- Use `source="entry_timing:signal_check"` and payload `{"stage": "signal_check"}`.

Operational safety:

- No new API calls.
- Audit call is best-effort and disabled by default.

### 5. Gap Report Alias Regression

Issue:

- Previous review found invalid `GROUP BY ... AS alias`.

Fix:

- Keep `_strip_alias()` behavior.
- Add regression coverage for alias expressions.

## Verification Checklist

- Compile touched modules.
- Run `tests/test_shadow_audit.py`.
- Run related PathB/ORDER_UNKNOWN/entry timing tests:
  - `tests/test_order_unknown_reconciliation.py`
  - `tests/test_pathb_runtime.py`
  - `tests/test_pathb_sell.py`
  - `tests/test_entry_timing.py`
- Run `git diff --check`.
- Run gap/report CLIs.
- Update QA document and compare this plan against implementation.

## QA Expectations

- `SHADOW_AUDIT_ENABLED=false` must still avoid creating an audit DB.
- Queue-full and DB issues must not propagate to trading code.
- Outcome updater should not perform per-signal writes.
- No additional broker, Claude, or market data requests.
- Cross-restart ORDER_UNKNOWN pause close limitation must be documented.

## Completion Status

| Item | Status |
| --- | --- |
| Batch outcome writes | Done |
| Close cached `ORDER_UNKNOWN_PAUSE` | Done |
| Include signal price in extrema | Done |
| Path A passive signal-check price sample | Done |
| Gap report alias regression coverage | Done |
| Tests and CLI verification | Done |
| QA and omission review | Done |

No intentional trading behavior change was made.
