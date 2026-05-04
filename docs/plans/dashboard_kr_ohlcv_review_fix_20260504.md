# Dashboard / KR OHLCV Review Fix - 2026-05-04

## Goal

Resolve the two review findings and verify that no requested item is missed:

- Today dashboard must not break when `/api/judgments` includes `basis.digest_built_at` or `basis.warning`.
- KR candidate history filtering must try the yfinance daily OHLCV fallback when the primary KIS daily OHLCV request raises.

## Scope

In scope:

- `dashboard/dashboard_server.py`
- `trading_bot.py`
- Focused tests under `tests/`

Out of scope:

- Trading rule changes, sizing changes, order submission changes, broker reconciliation changes, Claude prompt changes, or new market-data providers.

## Development Checklist

- [x] 1. Dashboard common escape helper
  - Move or define `escapeHtml` where the Today page can access it.
  - Keep the Logs page behavior intact.
  - Verify the Today page source includes the helper before `loadJudgments()` uses it.

- [x] 2. Dashboard regression coverage
  - Add a focused test proving the Today page has `escapeHtml`.
  - Add coverage for warning/digest rendering risk by checking the exact Today-page call sites are backed by the common helper.

- [x] 3. KR OHLCV primary exception fallback
  - Wrap the primary `get_daily_ohlcv()` call in `_fetch_signal_ready_ohlcv()`.
  - For KR only, when primary raises, continue into `_daily_ohlcv_kr_yf()` if fallback is enabled.
  - Preserve current US fallback behavior.
  - Preserve current KR behavior for empty or insufficient primary frames.

- [x] 4. KR OHLCV fallback regression coverage
  - Add a test where KR primary raises and yfinance fallback succeeds.
  - Assert the returned frame is usable and the source shows yfinance fallback.
  - Assert no exception escapes to candidate history filtering for that path.

- [x] 5. QA and omission check
  - Run targeted dashboard tests.
  - Run targeted trading bot OHLCV tests.
  - Run syntax compile checks for changed Python files.
  - Compare final code/tests against this checklist and mark completion status.

## Verification Log

- `python -m pytest tests\test_dashboard_pathb.py -q`
  - Result: 19 passed, 2 warnings.
- `python -m pytest tests\test_kr_ohlcv_fallback.py -q`
  - Result: 2 passed, 2 warnings.
- `python -m py_compile dashboard\dashboard_server.py trading_bot.py tests\test_dashboard_pathb.py tests\test_kr_ohlcv_fallback.py`
  - Result: passed.
- `python -m pytest -q`
  - Result: 575 passed, 2 skipped, 2 warnings.

## Final Omission Check

- Dashboard P1: covered by common `escapeHtml` in `COMMON_JS_BLOCK` and Today-page regression test.
- KR OHLCV P2: covered by primary exception handling in `_fetch_signal_ready_ohlcv()` and fallback/filter regression tests.
- No planned checklist item remains unchecked.
