# Live Config Safety Code Requirements - 2026-05-21

## Purpose

This document replaces the earlier loose improvement direction for the current review findings. The required change is not a cosmetic warning update. It must enforce live operating invariants at code level:

- Telegram `/setorder` must be fail-closed and atomic from an operator perspective.
- Path B live gates must enforce the operator-approved KR-on/US-on policy.
- Tracked live start config must match that policy.
- Unapproved `state/brain.json` runtime memory must stay out of this patch.

## Scope

In scope:

- `telegram_commander.py`
- `tools/live_preflight.py`
- `config/v2_start_config.json`
- `tests/test_telegram_order_size.py`
- `tests/test_live_config_sources.py`
- `state/brain.json` only as a required no-diff file

Out of scope:

- Changing the current Path B operating policy beyond the approved KR-on/US-on gate state.
- Re-enabling KR Claude price live execution.
- Promoting runtime lessons into `state/brain.json`.
- Broad refactors unrelated to these review findings.

## Current Code Problems

### 1. Telegram `/setorder` can destroy start config

Current pattern in `telegram_commander.py`:

```python
try:
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
except Exception:
    data = {}
```

If `config/v2_start_config.json` is malformed, partially written, locked, or temporarily unreadable, `/setorder` treats the config as empty and writes it back. That can drop existing `env_overrides` and leave only the order-size keys.

The current `_cmd_setorder()` also changes `bot.risk.max_order_krw` before persistence succeeds. Even after fixing the file overwrite risk, this would leave current runtime state different from restart state.

### 2. Path B policy mismatch can pass preflight

The original review assumed this state was unsafe:

```text
PATHB_KR_LIVE_ENABLED=true
PATHB_US_LIVE_ENABLED=true
```

The operator has explicitly opened `PATHB_KR_LIVE_ENABLED=true`. Therefore the current policy is KR-on/US-on. Preflight must pass this approved state and warn if either market gate is disabled.

### 3. Tracked live config currently contradicts the test expectation

The effective live config test expects:

```text
PATHB_KR_LIVE_ENABLED=true
KR_CLAUDE_PRICE_LIVE_ENABLED=false
PATHB_US_LIVE_ENABLED=true
```

The tracked `config/v2_start_config.json` and local `.env.live` must agree with those values so live preflight does not report env/start-config conflicts.

### 4. `state/brain.json` has unapproved memory diff

`state/brain.json` is policy memory used by prompts. It is not a normal runtime output to ship with this patch. The current diff includes version/date/training counters and learned observations. That must be removed from the PR unless there is a separate explicit approval workflow.

## Required Behavior

### R1. `/setorder` must be fail-closed and atomic

`/setorder` must only change runtime order size if the start config write succeeds.

Required behavior:

- Invalid JSON start config: do not write file, do not change `bot.risk.max_order_krw`.
- Non-object JSON root: do not write file, do not change runtime order size.
- Read failure other than a deliberate and explicitly supported path: do not write file, do not change runtime order size.
- Normal config: preserve unrelated `env_overrides` keys.
- Normal config: update the common order-size keys consistently.
- Operator response must clearly say the runtime value was not changed when persistence fails.

Recommended implementation:

```python
class StartConfigWriteError(RuntimeError):
    pass


def _load_start_config_for_write(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise StartConfigWriteError(f"start config read failed: {path}") from exc

    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise StartConfigWriteError(f"start config is invalid JSON: {path}") from exc

    if not isinstance(data, dict):
        raise StartConfigWriteError("start config root must be object")

    return data
```

Then `_cmd_setorder()` must persist before mutating runtime state:

```python
old = int(bot.risk.max_order_krw)

try:
    _write_start_config_common_order_krw(amount)
except StartConfigWriteError as exc:
    log.warning(f"[commander] MAX_ORDER_KRW start config update failed: {exc}")
    return (
        "[실패] 다음 시작 설정 저장 실패\n"
        f"  사유: {exc}\n"
        "  현재 주문한도는 변경하지 않았습니다."
    )

bot.risk.max_order_krw = float(amount)
log.info(f"[commander] max_order_krw 변경: {old:,} -> {amount:,}")
```

The file write should be as safe as the surrounding codebase allows. Prefer writing a temporary file in the same directory and replacing the target with `os.replace()` so an interrupted write does not leave truncated JSON.

### R2. Path B market live gates must warn on any policy mismatch

Current policy:

```text
KR Path B live: on
US Path B live: on
```

Only this combination may return `PASS`:

```text
PATHB_KR_LIVE_ENABLED=true
PATHB_US_LIVE_ENABLED=true
```

All other combinations must return `WARN`.

Recommended implementation:

```python
violations = []

if not _truthy(pathb_market_gates["KR"]):
    violations.append("KR Path B live must be enabled")

if not _truthy(pathb_market_gates["US"]):
    violations.append("US Path B live must be enabled")

if violations:
    status = "WARN"
    detail = "Path B market live gates violate KR-on/US-on policy: " + "; ".join(violations)
else:
    status = "PASS"
    detail = "Path B market live gates match KR-on/US-on policy"
```

`CheckResult.data` must include:

```python
{
    "values": pathb_market_gates,
    "policy": "KR-on/US-on",
    "policy_match": not violations,
    "remediation_required": bool(violations),
    "operator_action": "set PATHB_KR_LIVE_ENABLED=true and PATHB_US_LIVE_ENABLED=true",
}
```

### R3. Tracked live start config must be realigned

`config/v2_start_config.json` must be aligned with the current operating policy.

Required effective values:

```text
PATHB_KR_LIVE_ENABLED=true
KR_CLAUDE_PRICE_LIVE_ENABLED=false
PATHB_US_LIVE_ENABLED=true
PATHB_INTRADAY_ONLY=true
```

If KR Claude price live is intentionally being re-enabled separately, that remains a separate policy change and must not be hidden inside this fix. It needs an explicit approval note, updated tests, and live preflight expectations.

Local `.env.live` may also need operator-side alignment, but secrets or machine-local env files must not be added to the patch unless they are already tracked and safe to modify.

### R4. `state/brain.json` must be clean

This patch must not contain `state/brain.json` changes.

Required check:

```bash
git diff --exit-code -- state/brain.json
```

If a lesson candidate is needed, it must go through `state/lesson_candidates.json` or a reviewed candidate workflow, not direct brain memory promotion.

## Required Tests

### Telegram order-size tests

Update `tests/test_telegram_order_size.py`.

Required cases:

- `test_setorder_does_not_change_runtime_or_file_when_start_config_invalid`
  - Write invalid JSON to a temp config.
  - Call `_cmd_setorder(bot, "500000")`.
  - Assert original file contents are unchanged.
  - Assert `bot.risk.max_order_krw` is unchanged.
  - Assert response says the current order limit was not changed.

- `test_setorder_preserves_unrelated_env_overrides`
  - Include unrelated keys such as `ENABLED_MARKETS`, `PATHB_KR_LIVE_ENABLED`, or `KR_CONFIRMATION_GATE_MODE`.
  - Call `_cmd_setorder`.
  - Assert unrelated keys remain exactly present after write.

- Existing normal persistence test must continue to verify:
  - `MAX_ORDER_KRW`
  - `KR_FIXED_ORDER_KRW`
  - `US_FIXED_ORDER_KRW`
  - `PATHB_FIXED_ORDER_KRW`

### Path B preflight tests

Update `tests/test_live_config_sources.py`.

Required matrix:

| KR live | US live | expected |
| --- | --- | --- |
| true | true | PASS |
| false | true | WARN |
| true | false | WARN |
| false | false | WARN |

Required assertions:

- `policy_match` is true only for `KR=true, US=true`.
- KR disabled warnings mention KR.
- US disabled warnings mention US.
- `remediation_required` is true for every warning case.

The existing effective live config test must also pass with the tracked `config/v2_start_config.json`.

## Verification Commands

Run:

```bash
python -m pytest tests/test_telegram_order_size.py tests/test_live_config_sources.py -q
git diff --exit-code -- state/brain.json
python tools/live_preflight.py --mode live --skip-dashboard --json
```

Also run a syntax check if the implementation touches the Telegram or preflight modules:

```bash
python -m py_compile telegram_commander.py tools/live_preflight.py
```

## Merge Criteria

Do not merge until all conditions are true:

- `/setorder` cannot overwrite malformed or unreadable start config.
- `/setorder` does not change runtime order size when persistence fails.
- Path B preflight warns when either approved KR-on/US-on market live gate is disabled.
- `config/v2_start_config.json` effective values match the current policy.
- `state/brain.json` has no diff.
- Required targeted tests pass.
