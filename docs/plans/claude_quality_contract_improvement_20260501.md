# Claude Quality Contract Improvement Plan - 2026-05-01

## Goal

Improve Claude-assisted trading quality without breaking existing BrainDB, PathB, selection metadata, or live database contracts.

The work is split into three categories:

- Apply now: fixes for data loss, incorrect logging, unsafe defaults, and contract preservation.
- Apply with observation: low-risk prompt/schema reductions while keeping runtime contracts backward compatible.
- Shadow first: changes that alter actual trading decisions, call frequency, or model routing.

## Apply Now

### 1. Raw call and model observability

- Add a stable `call_id` to raw Claude call logs.
- Prevent same-second raw log filename collisions.
- Record actual model per call instead of falling back to `ANTHROPIC_MODEL`.
- Preserve existing raw log fields: `timestamp`, `date`, `market`, `label`, `model`, `prompt`, `raw_response`, `parsed`, `tokens`.
- Add optional metadata fields only: `parse_error`, `parse_stage`, `duration_ms`, `prompt_mode`, `prompt_version`, `extra`.
- Keep raw logging best-effort; log failure should not crash trading logic.

Verification:

- Two saves with the same label in the same second must not overwrite each other unless the same `call_id` is intentionally reused.
- R1 analyst logs must show `R1_MODEL`.
- Existing raw log readers must continue to work with old fields.

### 2. Credit tracker model accounting

- Add optional `model` to `credit_tracker.record`.
- Preserve existing `total`, `daily`, and `sessions` structures.
- Add additive `by_model` stats at top level and per day.
- Keep Sonnet pricing as default for unknown models.
- Fix the existing `all_days` summary bug.

Verification:

- Existing usage JSON can be loaded without migration.
- Old callers that do not pass `model` still work.
- `summary()` works with existing and new usage files.

### 3. Postmortem raw-first save

- Save raw response before JSON parsing.
- If parse fails, keep raw response on disk with `parse_error=True`.
- Do not write system-error placeholders into issue-pattern learning.
- Preserve BrainDB daily-record schema.

Verification:

- A parse failure still leaves a raw call log.
- BrainDB update can continue with the existing fallback postmortem dict.

### 4. Select ticker dead-code cleanup

- Remove the first, shadowed `select_tickers` implementation.
- Keep the second implementation as the only runtime implementation.

Verification:

- `from minority_report.analysts import select_tickers` resolves normally.
- Existing tests around selection metadata still pass.

### 5. PathB price contract preservation

- Merge `new_meta.price_targets` into partial reselect final metadata.
- Preserve existing valid price targets for retained trade-ready names.
- Add audit/block event when a trade-ready ticker has no price target.
- Revalidate stored `PricePlan` when reloading from PathB runs.

Verification:

- Partial reselect keeps old price targets for retained tickers.
- Partial reselect imports new price targets for replacement tickers.
- Invalid persisted plan is skipped instead of entering runtime scans.

### 6. Hold advisor safety and prompt quality

- Parse/API failure must return `HOLD`, not `SELL`.
- Invalid/missing action defaults to `HOLD`.
- Keep deterministic trailing as the fallback path.
- Clarify trail interpretation: `0.02` is tight, `0.05` is wide.
- Separate persona axes:
  - bull: upside continuation and trend persistence.
  - bear: downside risk and profit giveback protection.
  - neutral: ATR/statistical fit and expected value.
- Add a low-confidence SELL gate.

Verification:

- Three failed analyst calls cannot create a SELL decision.
- Trail output remains clamped to the allowed range.
- Existing caller still receives `{"action", "trail_pct", "votes"}`.

### 7. Select ticker output reduction

- Remove unused optional target fields from the prompt:
  - `entry_basis_tags`
  - `exit_basis_tags`
  - `invalidation_conditions`
- Keep parser and `PricePlan` dataclass fields for backward compatibility.
- Do not lower `max_tokens` yet.

Verification:

- `price_targets` still include required execution fields.
- Existing PathB parser still accepts old and new target payloads.
- Output token usage and target coverage are observed before token budget changes.

### 8. SQLite runtime stability

- Add WAL and busy timeout to ticker selection DB connections.
- Do not migrate or rewrite existing rows.

Verification:

- Existing DB opens normally.
- `init()` remains idempotent.

## Observe Before Changing Runtime Behavior

- Output token usage after select ticker prompt reduction.
- Price target coverage: `len(price_targets) / len(trade_ready)`.
- Hold advisor trail distribution.
- Hold advisor SELL ratio.
- Postmortem parse failure rate.
- R1 actual model cost and quality.
- Raw log size growth.
- PathB invalid plan block count.

## Shadow First

Do not apply these directly to live decision paths:

- Tune actual skip.
- R2 actual skip.
- Haiku model routing for hold advisor or selection.
- Select ticker mode split.
- Rescreen without price targets.
- Legacy `watchlist -> trade_ready` fallback removal.
- Full parser unification.
- Trade-ready promotion tightening.

Shadow outputs must not feed BrainDB performance learning until explicitly promoted.

## Final QA Checklist

- Compile touched Python modules.
- Run focused tests for:
  - raw logger collision and stable `call_id` overwrite.
  - model-specific credit tracking on old and new usage schemas.
  - postmortem parse-failure raw persistence.
  - hold advisor parse-failure fallback.
  - PathB missing/invalid price target handling.
  - partial reselect price-target merge helper.
  - ticker selection DB WAL connection.
- Inspect existing BrainDB compatibility:
  - no required key removal.
  - no shadow results mixed into BrainDB learning.
  - postmortem system errors do not create issue patterns.
- Inspect existing DB compatibility:
  - no destructive migration.
  - SQLite connections still create existing tables idempotently.
  - added fields are JSON-only or additive stats.
- Compare final diff against this plan and document any intentionally deferred item.

## QA Result - 2026-05-01

Completed:

- Raw logger now writes `call_id`, model, optional parse metadata, and collision-resistant filenames.
- Credit tracker keeps legacy totals and adds additive model buckets.
- Postmortem saves raw response before JSON parsing and marks parse failures.
- The shadowed first `select_tickers` implementation was removed.
- Select ticker prompt no longer asks for `entry_basis_tags`, `exit_basis_tags`, or `invalidation_conditions`.
- Slot and execution-hint rules are now directly in the prompt instead of patched by `str.replace`.
- Selection metadata now includes additive `_price_target_coverage`.
- Partial reselect merges existing and new `price_targets` for final trade-ready tickers.
- PathB records missing Claude price targets and rejects invalid reloaded plans.
- Hold advisor parse/API failures now fall back to `HOLD`, not `SELL`.
- Ticker selection DB uses WAL, busy timeout, and a closing connection context.
- `trading_bot.DECISIONS_FILE` compatibility alias was restored.

Validation:

- `python -m py_compile` passed for touched runtime, Claude, DB, and test modules.
- Focused contract tests passed: 32 passed.
- Related soft-exit/shadow tests passed: 31 passed.
- `python -m pytest tests -q` passed: 212 passed, 2 warnings.

Known non-code test blocker:

- `python -m pytest -q` from repo root still collects `_sim_test.py`, which requests a live KIS token during collection. In the current sandbox this fails with a network/permission error before normal tests run. This is external-test harness behavior, not a failure of the changed modules.

Deferred by design:

- Actual tune skip.
- Actual R2 skip.
- Haiku routing changes.
- Select ticker full mode split.
- Rescreen without price targets.
- Legacy `watchlist -> trade_ready` fallback removal.
- Full parser unification.
