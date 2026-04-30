# Soft Exit Arbitration QA - 2026-04-30

## Summary

Implemented Phase 1 soft-exit arbitration for Path A positions that still have a Claude reference target.

The change is scoped to soft exits. Hard risk exits and broker/order-risk paths continue without Claude gating.

## Implemented Checklist

- [x] Saved implementation plan:
  - `docs/plans/soft_exit_arbitration_20260430.md`
- [x] Added single-call quick exit checker:
  - `minority_report/quick_exit_check.py`
- [x] Added Path A entry reference metadata snapshot:
  - cancelled same-day Path B reference
  - selection `price_targets` reference
- [x] Copied reference metadata from pending order into filled position.
- [x] Added soft-exit arbitration gate before sell execution.
- [x] Limited arbitration to:
  - `profit_floor`
  - `trail_stop`
  - eligible `max_hold` with reference target
- [x] Excluded hard/safety paths:
  - `loss_cap`
  - `stop_loss`
  - Path B managed positions
  - broker/order failures
  - `ORDER_UNKNOWN`
  - session forced close
- [x] Added HOLD loop protection:
  - `soft_exit_floor_price` is a direct sell reason.
  - per-position review count limit.
  - per-position cooldown.
  - protective floor is not weakened below existing protective metadata.
- [x] Added audit/decision metadata fields for soft-exit review data.
- [x] Added v2 close reason:
  - `soft_exit_floor_price` -> `CLOSED_SOFT_EXIT_FLOOR`
- [x] Added focused tests.

## Verification

Static compile:

```powershell
python -m compileall trading_bot.py risk_manager.py runtime\v2_lifecycle_runtime.py minority_report\quick_exit_check.py tests\test_soft_exit_arbitration.py tests\test_loss_cap_profit_floor.py
```

Result: passed.

Unit tests:

```powershell
python -m pytest tests\test_loss_cap_profit_floor.py tests\test_soft_exit_arbitration.py -q
```

Result:

```text
14 passed, 2 warnings
```

Warnings were existing `eventlet` / `distutils` deprecation warnings from dependency imports, not test failures.

Whitespace check:

```powershell
git diff --check -- risk_manager.py runtime\v2_lifecycle_runtime.py tests\test_loss_cap_profit_floor.py trading_bot.py minority_report\quick_exit_check.py tests\test_soft_exit_arbitration.py docs\plans\soft_exit_arbitration_20260430.md
```

Result: no whitespace errors. Git reported CRLF normalization warnings only.

## Operational Safety Review

- No live order was placed during verification.
- No KIS price/balance/order API was called by tests.
- Tests use local stubs and temporary SQLite only.
- Quick Claude check fails open to the original sell path:
  - timeout -> `SELL`
  - API error -> `SELL`
  - invalid parse -> `SELL`
- `loss_cap` and `stop_loss` bypass arbitration.
- `soft_exit_floor_price` bypasses arbitration and sells directly.
- Path B managed positions remain excluded from normal RiskManager exit candidates.
- The only runtime latency added is when all arbitration gate conditions pass on a soft exit.

## Plan vs Actual

| Plan item | Actual result | Status |
|---|---|---|
| Quick exit checker | Added `minority_report.quick_exit_check.quick_exit_check()` with strict JSON and SELL fallback | Done |
| Path B reference copy at Plan A entry | Implemented same-day cancelled Path B lookup via `v2_path_runs` | Done |
| Selection reference snapshot | Implemented from `selection_meta.price_targets` at order creation | Done |
| Position metadata copy | Implemented in `_make_position_from_broker()` | Done |
| RiskManager direct floor | Added `soft_exit_floor_price` candidate generation | Done |
| Soft exit gate | Implemented in `_process_exit_candidates()` before normal sell/max-hold handling | Done |
| Sell audit fields | Persisted soft-exit and reference metadata through sell event metadata | Done |
| v2 close reason | Added `CLOSED_SOFT_EXIT_FLOOR` mapping | Done |
| Infinite HOLD loop guard | Implemented cooldown, max reviews, and direct `soft_exit_floor_price` sell reason | Done |
| Tests | Added focused tests plus v2 close reason assertion | Done |
| QA document | This file | Done |

## Deferred Items

- Reverse attachment when Plan A is already open and Plan B is cancelled later.
- Same-day reentry rule split between loss exits and profitable exits.
- Broader redesign of existing 3-view `hold_advisor`; Phase 1 only bypasses it for max-hold cases that pass this new quick arbitration gate.

## Known Limits

- Existing open positions will not automatically gain reference metadata unless they were entered through the updated pending-order path.
- If a HOLD decision keeps an existing protective floor above the current price, the next risk pass may sell via `soft_exit_floor_price`. This is intentional because Phase 1 does not allow Claude HOLD to weaken existing protection.

## 2026-05-01 Follow-up QA

Plan document:

- `docs/plans/soft_exit_arbitration_followup_20260501.md`

Follow-up changes:

- Added `model=MODEL` to `minority_report/quick_exit_check.py` `save_raw_call(...)` so raw Claude call logs store the actual quick-exit model.
- Replaced the misleading `max_hold`-only comment before soft-exit arbitration with a comment that covers all eligible soft exits.
- Removed unreachable protective-floor fallback code after `protective_floor = max(...)`.
- Added regression tests for Path B target priority, `trail_stop`, review limit bypass, cooldown bypass, missing target bypass, `current_price <= entry_price` bypass, and KRW `soft_exit_floor_price`.

Additional safety repair:

- During verification, a text encoding/comment repair issue had left several real statements on comment lines in `trading_bot.py`.
- These statements were restored before QA. The restored paths include balance initialization, trailing metadata update, fee estimate, `max_hold` extension state, scan interval fallback, startup guard timing, ML context setup, no-signal replacement guard, inverse ETF guard, ATR fallback stop-loss, partial reselection protection, and KR screen-cache save guard.
- A targeted scan for swallowed-code patterns was run after repair.

Verification:

```powershell
python -m compileall trading_bot.py minority_report\quick_exit_check.py tests\test_soft_exit_arbitration.py
```

Result: passed.

```powershell
python -m pytest tests\test_soft_exit_arbitration.py tests\test_loss_cap_profit_floor.py -q
```

Result:

```text
21 passed, 2 warnings
```

Warnings were existing `eventlet` / `distutils` deprecation warnings from dependency imports.

```powershell
git diff --check -- trading_bot.py minority_report\quick_exit_check.py tests\test_soft_exit_arbitration.py docs\plans\soft_exit_arbitration_followup_20260501.md docs\reports\soft_exit_arbitration_qa_20260430.md
```

Result: no whitespace errors.

Additional static scan:

```powershell
rg -n "\?\? if|#.*\s{4,}(if|for|return|[A-Za-z_][A-Za-z0-9_]*\s*=)" trading_bot.py
```

Result: no remaining matches.

Operational safety:

- No KIS API call was made.
- No broker/order API call was made.
- No bot restart was performed.
- No live order path was executed.
- Verification used compile checks, local unit tests, and static text scans only.

Plan vs actual:

| Follow-up plan item | Actual result | Status |
|---|---|---|
| Add quick-exit model logging | `save_raw_call(..., model=MODEL)` added | Done |
| Fix misleading arbitration comment | Comment now describes eligible soft exits generally | Done |
| Remove unreachable protective-floor fallback | Dead branch removed | Done |
| Add 7 missing regression tests | Added and passing with existing soft-exit tests | Done |
| Local non-live verification | Compile, pytest, diff check, swallowed-code scan passed | Done |
| QA document update | This section | Done |
