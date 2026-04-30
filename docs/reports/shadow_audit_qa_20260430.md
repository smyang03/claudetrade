# Shadow Audit QA - 2026-04-30

## Scope

Implemented isolated shadow audit infrastructure for passive analysis only.

Runtime behavior constraints checked:

- `SHADOW_AUDIT_ENABLED=false` keeps writer disabled and does not create an audit DB.
- Audit DBs are separate from operating DBs: `data/audit/live_shadow_audit.db`, `data/audit/paper_shadow_audit.db`.
- Hooks use existing in-memory prices/orders/state only; no new broker, Claude, or market data API calls.
- Hook calls are non-blocking and swallow audit exceptions.
- Existing operating DB schemas are not modified.

## Verification

- `python -m compileall audit tools\shadow_audit_gap.py tools\shadow_audit_report.py trading_bot.py runtime\pathb_runtime.py tests\test_shadow_audit.py` passed.
- `python -m pytest tests/test_shadow_audit.py -q` passed: `10 passed`.
- `python -m pytest tests/test_order_unknown_reconciliation.py tests/test_pathb_runtime.py tests/test_pathb_sell.py tests/test_entry_timing.py -q` passed: `25 passed`.
- `python tools/shadow_audit_gap.py --date 2026-04-30` generated `docs/reports/shadow_audit_gap_20260430.md`.
- `python tools/shadow_audit_report.py --date 2026-04-30 --market KR --mode live` generated `docs/reports/shadow_audit_report_live_20260430_KR.md`. The report correctly shows DB missing because shadow audit is disabled by default and no live audit DB exists yet.
- `git diff --check -- audit tools trading_bot.py runtime/pathb_runtime.py tests/test_shadow_audit.py docs/plans/shadow_audit_review_fix_20260430.md docs/reports/shadow_audit_qa_20260430.md` passed.

## MD Comparison

| Plan Item | Status | Implementation |
| --- | --- | --- |
| Gap audit tool and report | Done | `tools/shadow_audit_gap.py`, `docs/reports/shadow_audit_gap_20260430.md` |
| ID helpers | Done | `audit/shadow_audit_ids.py` |
| DB schema and store | Done | `audit/shadow_audit_store.py` |
| Non-blocking writer | Done | `audit/shadow_audit_writer.py` with `queue.Queue` and worker thread |
| Passive price sample support | Done | `_audit_emit_price_sample()` with context filter |
| Path A hooks | Done | pending signals, ORDER_UNKNOWN blocks, arbiter/reentry/safety blocks, order/fill timing |
| ORDER_UNKNOWN episode hooks | Done | active episodes, blocked signal links, session-open clear, session-close unresolved |
| PathB hooks | Done | entry scan block, price seen, zone hit, buy sent/fill, exit signal, sell sent, sell close |
| Outcome updater | Done | `audit/shadow_outcome_updater.py` for +5/+15/+30/+60 and close |
| Report CLI | Done | `tools/shadow_audit_report.py` with episodes, blocked signals, outcomes, score/time/path buckets, missing price, writer health |
| Tests | Done | `tests/test_shadow_audit.py` plus related PathB/ORDER_UNKNOWN/entry timing tests |

## Review Fix Comparison

| Review Item | Status | Verification |
| --- | --- | --- |
| Batch outcome writes | Done | `ShadowOutcomeUpdater` now collects events and performs one `write_events()` call; test asserts one write call for two signals. |
| Close original `ORDER_UNKNOWN_PAUSE` | Done for in-memory active episodes | `_audit_close_active_episode()` closes cached pause episodes and removes the cache key; test covers close and empty-cache skip. |
| Include signal price in extrema | Done | outcome test asserts first later sample at +5% keeps `max_drawdown_pct` at `0.0`. |
| Record Path A passive check prices | Done | `_entry_timing_signal_check()` emits `entry_timing:signal_check` with the existing price parameter; test covers same-price forwarding. |
| Real post-signal close sample | Done | close outcome is `missing_price` when only the signal-time sample exists. |
| Ticker/session separated episode IDs | Done | tests cover ticker-scoped ID separation and session-date cache separation. |
| Gap report `GROUP BY` alias | Done | test covers `COALESCE(reason_code, '') AS reason_code`; regenerated gap report contains populated counts. |

## Operational Safety Review

- The Path A passive check hook uses the existing `_entry_timing_signal_check(..., price)` argument and does not call `get_price()`.
- Outcome updater batching reduces audit DB lock exposure from once per signal to once per flush.
- All new runtime audit close/sample calls remain best-effort and catch or route through existing audit wrappers.
- No order submission, broker reconciliation decision, sell decision, sizing, or risk gate behavior was changed.

## Intentional Phase 2 Items

- `avoided_loss_pnl` simulation is not implemented. It requires virtual entry/exit rules and should be added after enough shadow data accumulates.
- Recovery-mode trading behavior is not implemented. This task records the data needed to evaluate it later without changing live decisions.
- Cross-restart `ORDER_UNKNOWN_PAUSE` closure is not implemented. If a pause opens before process restart, the in-memory cache is gone and session-open clear cannot close that old episode without a DB lookup fallback. This is intentionally deferred.

## Residual Risk

- If the audit DB is locked for a long period, events may drop after the queue fills. This is expected and recorded in writer health; trading remains unaffected.
- Future report quality depends on passive price sample density. Missing horizons are recorded as `missing_price` rather than inferred.
