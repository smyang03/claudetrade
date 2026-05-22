# Immediate Code Requirements

Date: 2026-05-22

Scope: only the work that should be developed now. Do not implement deferred backlog from `CODE_LEVEL_REQUIREMENTS_20260522.md` in this pass.

## Goals

- Make sub-screener live/shadow state impossible to misread.
- Add safety fixture coverage before touching broker-truth zero-holding reconcile logic.
- Keep config and live behavior unchanged unless the operator explicitly confirms the policy.

## Non-Goals

- Do not change `SUB_SCREENER_*` config values.
- Do not implement US KIS ranking screener in this pass.
- Do not change PathB order sizing, live gates, stop logic, or broker truth priority.
- Do not promote KR `fade_recovered_shadow` to live action.
- Do not edit `state/brain.json`.

## NOW-0 - Commit Hygiene Guardrail

Current state:

- The working tree contains runtime artifacts and policy memory alongside source/docs.
- Risk files include `state/brain.json`, generated `state/*.json`, DB sidecars, temp browser profiles, screenshots, and runtime data.

Requirement:

- Stage by explicit path.
- Exclude `state/brain.json` unless separately approved.
- Exclude runtime/generated artifacts unless the change explicitly targets them.
- Before commit, run:

```powershell
git status --short
git diff --stat
```

Done when:

- The staged diff contains only intentional source/docs/config/test changes.

## NOW-1 - Sub-Screener Effective Trigger Visibility

Current code truth:

```python
scoped_name = f"SUB_SCREENER_{market_key}_TRIGGER_ENABLED"
if os.getenv(scoped_name) is not None:
    return _env_bool(scoped_name, False)
return _env_bool("SUB_SCREENER_TRIGGER_ENABLED", True)
```

Current tracked config:

```text
SUB_SCREENER_ENABLED=true
SUB_SCREENER_TRIGGER_ENABLED=false
SUB_SCREENER_KR_TRIGGER_ENABLED=true
SUB_SCREENER_US_TRIGGER_ENABLED=true
```

Therefore:

- KR effective trigger is `true`.
- US effective trigger is `true`.
- Global false alone does not mean shadow.
- If scoped and global trigger flags are both absent, current code defaults to live trigger.

Required behavior:

- Add read-only effective trigger calculation in preflight or ops summary.
- Use the same precedence rule as `_sub_screener_trigger_enabled()`.
- Show the following fields per market:
  - `enabled`
  - `global_trigger`
  - `scoped_trigger`
  - `effective_trigger`
  - `resolution_source`: `scoped`, `global`, or `default_live`
  - `max_per_session`
  - `interval_min`
  - `min_interval_min`
  - `blackout_before_close_min`
  - `state_file_exists`
  - `scan_count`
  - `detection_count`
  - `attempt_count`
  - `success_count`
  - `last_scan_at`
  - `last_detection`
  - `last_attempt_at`
  - `last_success_at`

Operator confirmation gate:

- Read-only visibility may be implemented now.
- Config changes must wait for operator confirmation.
- The UI/preflight text must state that scoped trigger overrides global trigger.

Tests:

- Global false + scoped true -> effective true, `resolution_source=scoped`.
- Global true + scoped false -> effective false, `resolution_source=scoped`.
- Global false + scoped absent -> effective false, `resolution_source=global`.
- Global absent + scoped absent -> effective true, `resolution_source=default_live`.
- Disabled trigger records scan only and does not call `record_attempt()`, `_reinvoke_analysts()`, or `manual_rescreen()`.

Done when:

- Operator can identify KR/US effective trigger without reading code.
- No config value is changed by this implementation.

## NOW-2 - Broker-Truth Zero-Holding Fixture Tests

Current code truth:

- Plan A path: `TradingBot._sell_zero_holding_broker_evidence()`.
- PathB path: `PathBRuntime._pathb_zero_holding_broker_evidence()`.
- Local position removal/PathB closure is allowed only when broker truth is fresh, position qty is zero, and open remaining qty is zero.

Requirement:

- Add fixture tests before changing reconcile logic.
- Tests must use realistic KR/US broker payload shapes for:
  - positions
  - open orders
  - today fills
  - side fields
  - remaining quantity fields
  - ticker fields

Required test cases:

- KR fresh zero position + no open order -> stale local position can be removed.
- US fresh zero position + no open order -> stale local position can be removed.
- KR/US open order with remaining qty -> local position is not removed.
- stale broker truth -> local position is not removed.
- broker error/missing snapshot -> local position is not removed.
- unrecognized ticker key -> no false ticker match.
- sell fill evidence exists but position/open-order condition is unsafe -> manual reconciliation remains required.

Done when:

- Destructive reconcile behavior is covered by KR and US fixtures.
- Any parser adjustment is made only if fixture tests expose a real mismatch.

## Verification Commands

Run the focused tests for the immediate work:

```powershell
python -m pytest tests/test_sub_screener_integration.py tests/test_auto_sell_claude_gate.py tests/test_pathb_runtime.py -q
```

If preflight/ops code is touched:

```powershell
python -m pytest tests/test_live_guardian.py tests/test_v2_phase6.py -q
```

If `trading_bot.py` or `runtime/pathb_runtime.py` is touched:

```powershell
python -m py_compile trading_bot.py runtime/pathb_runtime.py
```

## Deferred Backlog

Do not include these in the immediate implementation:

- PathB entry broker-truth gate ops visibility.
- Profit review timeout fallback dashboard/canonical display.
- US KIS ranking screener.
- V2 canonical truth runbook.
- Counterfactual outcome schedule.
- Prompt pool/evidence next-session review.
- KR fade recovery live promotion.
