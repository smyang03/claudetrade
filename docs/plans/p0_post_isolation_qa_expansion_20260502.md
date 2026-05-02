# P0 Post-Isolation QA Expansion

## Purpose

After KIS market profile isolation and WS/REST fill idempotency changes, expand regression coverage around the execution paths most likely to break in live operation.

This is not a new trading feature. It is a QA hardening pass.

## Scope

### 1. PathB Token Routing

- Verify PathB helper uses `bot._token_for_market(market)` when the bot supports it.
- Verify fallback still uses `bot.token` for older lightweight test doubles.
- Verify PathB broker truth provider forwards the requested market to the bot token helper.
- Expected result: US PathB calls do not accidentally reuse KR-only token state.

### 2. Broker Truth Token Provider

- Verify `BrokerTruthSnapshot` calls token providers with the market argument for KR and US.
- Verify legacy no-argument token providers remain compatible.
- Expected result: broker truth snapshots can fetch KR/US with market-specific credentials.

### 3. Dashboard KIS Runtime/Profile

- Verify dashboard `_kis_runtime()` sets US profile fields (`BASE_URL_US`, `WS_URL_US`, US credentials).
- Verify dashboard broker snapshot uses market-specific token calls.
- Verify broker snapshot exposes `kis_profile` so UI/API consumers can inspect `credential_mode`.
- Expected result: dashboard/preflight displays do not hide whether US is fallback-shared or separate.

### 4. Live Preflight Credential Mode

- Verify `kis.us_credentials` reports `fallback_shared_kr` when US-specific credentials are empty but KR fallback exists.
- Verify it reports `separate_us` when US-specific credentials are present.
- Expected result: operator can confirm actual KIS credential mode before live.

### 5. WS Fill Idempotency QA

- Keep existing duplicate raw hash test.
- Confirm parser emits `raw_hash`.
- Confirm REST cumulative delta tests remain green.
- Expected result: WS transport duplicate suppression does not become accounting-level time-bucket dedupe.

## QA Commands

- `python -m py_compile kis_api.py trading_bot.py dashboard/dashboard_server.py runtime/broker_truth_snapshot.py runtime/pathb_runtime.py tools/live_preflight.py`
- `python -m pytest tests/test_pathb_runtime.py tests/test_broker_truth_snapshot.py tests/test_dashboard_kis_profile.py tests/test_live_preflight_credentials.py tests/test_kis_ws_fill_notice.py tests/test_live_order_reconciliation.py -q`
- `python -m pytest tests/test_pid_lock.py tests/test_startup_token_refresh.py tests/test_kis_market_profile.py tests/test_kis_token_auto_refresh.py tests/test_live_token_balance.py tests/test_order_equity_reconciliation_improvement.py tests/test_v2_phase5.py -q`
- `git diff --check`

## Completion Check

- Compare this checklist against code/test changes.
- Patch omissions found in the comparison.
- Leave this MD in place as the QA expansion record unless explicitly asked to delete it.
