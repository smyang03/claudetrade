# Trading Decision Contract Improvement - 2026-05-01

## Goal

Stabilize Claude-assisted trading decisions by making the decision contract explicit:

- Claude proposes risk-adjusted judgments, caps, and price plans.
- The system owns final order budget, quantity, broker checks, hard exits, and forced liquidation.
- Prompt output remains backward compatible with existing live metadata and PathB contracts.

## Implementation Order

### P0. Common Claude Decision Contract

Status: completed

Tasks:

- Add a reusable common contract for Claude prompts.
- Apply it to market judgment, ticker selection, hold advisor, and quick exit prompts.
- Require JSON-only output and explicit prompt/schema version fields where compatible.
- State that Claude must not invent unavailable data or override hard risk rules.

Validation:

- Prompt text includes the common decision-assistant contract.
- Existing JSON parsers continue to accept legacy responses.

### P1. Buy Sizing Contract

Status: completed

Tasks:

- Clarify that `suggested_size_pct` is analyst sizing intent used by consensus, not final order size.
- Clarify that `max_position_pct` is a legacy per-candidate order cap.
- Add additive selection fields:
  - `allocation_intent`
  - `max_order_cap_pct`
  - `risk_budget_pct`
  - `size_reason`
- Keep `max_position_pct` for backward compatibility.
- Make runtime size cap logic prefer `max_order_cap_pct`, then fall back to `max_position_pct`.
- Add order sizing audit fields near final Path A order signal logging.

Validation:

- Candidate normalization preserves new fields.
- Existing `max_position_pct` tests still pass.
- Final entry signal logs include sizing-contract fields without changing order calculation.

### P2. Selection And Execution-Plan Separation Contract

Status: completed

Tasks:

- Keep the current single Claude call for runtime safety.
- Split the prompt contract into two conceptual phases:
  - rank candidates into WATCH and TRADE_READY.
  - create price plans only for TRADE_READY.
- Add prompt version markers for `selection_rank_v3` and `execution_plan_v1`.
- Do not add an extra live Claude call in this change.

Validation:

- Selection prompt contains phase separation instructions.
- `price_targets` are still normalized only for TRADE_READY names.

### P3. Price Plan Input And Output Contract

Status: completed

Tasks:

- Ask for richer execution-plan fields:
  - `reference_price`
  - `reward_risk`
  - `risk_pct`
  - `reward_pct`
  - `target_basis`
  - `invalid_if`
- Keep older rationale fields for compatibility.
- Explicitly require native prices: KR=KRW, US=USD.
- Explicitly forbid unsupported price fabrication when data is missing.

Validation:

- Parser accepts the new fields.
- Legacy price targets still parse.

### P4. PricePlan Validation

Status: completed

Tasks:

- Preserve existing hard checks:
  - `sell_target > buy_zone_high`
  - `stop_loss < buy_zone_low`
  - minimum reward/risk
  - minimum confidence
- Validate additive reward/risk metadata when present.
- Keep `reward_risk >= 1.2` as the hard reject threshold.
- Recommend `reward_risk >= 1.5` in prompts.

Validation:

- Low reward/risk plans are rejected.
- New optional fields round-trip through `to_dict()`.

### P5. Hold Advisor Stage Contract

Status: completed

Tasks:

- Add `decision_stage` support:
  - `TP_REVIEW`
  - `PRE_SESSION`
  - `INTRADAY_REVIEW`
  - `MAX_HOLD`
  - `PRE_CLOSE_CARRY`
  - `SOFT_EXIT`
  - `MANUAL_REVIEW`
- Add stage-specific default policy text.
- Extend output contract with:
  - `sell_urgency`
  - `protective_stop`
  - `next_review_min`
  - `invalid_if`
- Keep existing caller contract: `{"action", "trail_pct", "votes"}`.

Validation:

- Old callers still work without passing `decision_stage`.
- Stage-aware callers pass the correct stage.
- Parse/API failures still return HOLD.

### P6. Hard Rule And Soft Rule Boundary

Status: completed

Tasks:

- State in prompts that hard rules are deterministic system rules.
- Allow Claude only to advise on soft exceptions:
  - target-trailing review
  - carry exception review
  - soft exit recheck
  - candidate risk cap
  - price plan proposal

Validation:

- Prompt text contains the hard-rule override prohibition.
- No system hard-exit path is changed to depend on Claude.

### P7. Market Judgment Output Enrichment

Status: completed

Tasks:

- Add additive analyst output fields:
  - `market_regime`
  - `data_quality`
  - `new_buy_permission`
  - `max_gross_exposure_pct`
  - `key_confirmations`
  - `key_contradictions`
- Keep existing consensus engine behavior unchanged.

Validation:

- Sanitizer preserves new fields.
- Existing consensus tests remain compatible.

### P8. Prompt Versioning

Status: completed

Tasks:

- Centralize reusable prompt contract text in code.
- Add prompt/schema version references to prompts and parsed metadata where safe.
- Defer full external prompt-file migration.

Validation:

- Prompt versions are visible in raw-call prompts.
- No runtime path depends on external prompt files.

### P9. Tests And QA

Status: completed

Tasks:

- Add focused tests for new sizing, price-plan, prompt, and hold-stage contracts.
- Run compile checks for touched modules.
- Run focused tests.
- Compare final diff against this MD and document any deferred item.

Validation:

- Focused tests pass.
- Final QA section below is completed.

## QA Result

Completed on 2026-05-01.

Validation commands:

- `python -m py_compile minority_report\prompt_contracts.py minority_report\analysts.py minority_report\hold_advisor.py minority_report\quick_exit_check.py bot\candidate_policy.py decision\claude_price_plan.py trading_bot.py runtime\pathb_runtime.py dashboard\dashboard_server.py tests\test_trading_decision_contract_improvements.py`
- `python -m pytest tests\test_trading_decision_contract_improvements.py -q`
- `python -m pytest tests\test_claude_quality_contracts.py -q`
- `python -m pytest tests\test_pathb_plan.py tests\test_pathb_claude_contract.py -q`
- `python -m pytest test_trading_improvements.py -q`
- `python -m pytest tests\test_soft_exit_arbitration.py -q`
- `python -m pytest tests\test_pathb_runtime.py tests\test_pathb_safety.py -q`
- `python -m pytest tests -q`
- `git diff --check`

Results:

- Compile check passed.
- New decision-contract tests passed: 6 passed.
- Existing Claude quality contract tests passed: 5 passed.
- PathB plan/Claude contract tests passed: 8 passed.
- Existing trading improvement tests passed: 131 passed.
- Soft-exit arbitration tests passed: 13 passed.
- PathB runtime/safety tests passed: 19 passed.
- Full `tests/` suite passed: 219 passed, 2 warnings.
- `git diff --check` passed.

Warnings:

- Pytest reports two existing `eventlet`/`distutils` deprecation warnings.
- Git reports line-ending normalization warnings for several files.

## Final MD Comparison

Completed against this MD after QA.

Implemented:

- P0 common prompt contract was centralized in `minority_report/prompt_contracts.py` and inserted into analyst, selection, hold-advisor, and quick-exit prompts.
- P1 sizing contract was added to prompts and normalization. Runtime now preserves `allocation_intent`, `max_order_cap_pct`, `risk_budget_pct`, and `size_reason`, while keeping `max_position_pct` compatibility. Path A entry-signal logs now include sizing audit fields.
- P2 selection/execution-plan separation was implemented as a prompt contract without adding an extra live Claude call.
- P3 price-plan input/output contract was expanded with optional `reference_price`, `reward_risk`, `risk_pct`, `reward_pct`, `target_basis`, and `invalid_if`.
- P4 `PricePlan` parsing, `to_dict()`, and validation now support the additive fields and reject declared reward/risk below the hard threshold.
- P5 `hold_advisor` now supports `decision_stage`, stage default policies, expanded vote fields, and stage-aware caller wiring for pre-session, intraday, max-hold, TP review, pre-close carry, PathB carry review, and dashboard manual review.
- P6 hard/soft rule boundaries are explicit in shared prompt text and no hard-exit path was moved under Claude control.
- P7 analyst market judgment output is enriched with additive regime/data-quality/buy-permission/exposure fields while leaving consensus behavior unchanged.
- P8 prompt version metadata is attached to raw-call logs where safe.
- P9 focused tests were added in `tests/test_trading_decision_contract_improvements.py`.

Deferred by design:

- Physical prompt-file migration under `prompts/` was not done. The reusable contract was centralized in code to avoid runtime file dependency risk.
- Runtime selection was not split into two Claude API calls. The rank/plan split is enforced as a prompt contract only in this change.
- Market regime fields are preserved in analyst outputs but are not yet wired into deterministic exposure gates.

## Deferred Follow-Up Plan

These items are safe to defer now. They should be promoted only after observing live/shadow behavior from the current contract changes.

### F1. Observe Market-Regime Fields Before Runtime Gates

Priority: first follow-up

Do not immediately connect `market_regime`, `data_quality`, `new_buy_permission`, or `max_gross_exposure_pct` to live sizing gates. First observe:

- frequency of `risk_on|balanced|risk_off|panic`
- mismatch between regime output and actual index/portfolio behavior
- whether `new_buy_permission=block` would have prevented profitable entries
- whether `allow/selective/block` is stable across bull/bear/neutral analysts

Promotion path:

1. log-only observation
2. shadow exposure-gate decision
3. dashboard/report comparison
4. live deterministic gate only after review

### F2. Prompt File Migration

Priority: second follow-up

Move prompts to `prompts/` only after the current in-code contract is stable. Migration should not change prompt content and should include tests that missing prompt files fail safely.

Target structure:

- `prompts/common_contract_v1.txt`
- `prompts/ticker_selection_rank_v3.txt`
- `prompts/execution_plan_v1.txt`
- `prompts/hold_advisor_v3.txt`
- `prompts/quick_exit_v2.txt`

### F3. Real Two-Call Selection Split

Priority: last follow-up

Do not split selection into two live Claude calls until token cost, latency, and price-target coverage are observed. The first safe rollout should be shadow-only:

- live path keeps the current single call
- shadow path runs rank-only then execution-plan-only for top TRADE_READY names
- compare selected names, target coverage, parse failures, latency, and token cost
- promote only if target quality improves without unacceptable delay

No unexplained gap found between this MD and the final implementation. The deferred items above are intentional scope controls, not missed work.
