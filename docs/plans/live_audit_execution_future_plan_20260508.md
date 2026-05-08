# Live Audit Execution Future Plan

Date: 2026-05-08

These items are intentionally deferred. They require counterfactual shadow data or additional live safety validation before any live gate changes.

## 1. Counterfactual Shadow Infrastructure

The current audit DB records what actually happened. Live improvement experiments also need records for what an experimental rule would have done.

Planned decision table/log:

```text
audit_counterfactual_decisions
- shadow_decision_id
- candidate_key
- experiment_name
- market
- session_date
- ticker
- decision_at
- base_price
- current_logic_result
- experiment_result
- blocked_by_current_rules
- triggered_experiment_rules
- risk_snapshot_json
- created_at
```

Planned outcome table/log:

```text
audit_counterfactual_outcomes
- shadow_decision_id
- horizon_min
- return_pct
- max_runup_pct
- max_drawdown_pct
- status
- source
- known_at
```

## 2. US ready_no_signal Shadow

Goal:

- Capture READY cases that skipped because no live signal fired.
- Record whether an experimental relaxed signal rule would have entered.
- Attach 30m/60m outcome labels.

Live relaxation is allowed only after checking:

- median return
- positive return rate
- MAE risk
- p90 MFE
- sample size
- spread/liquidity/fill feasibility

## 3. KR not_in_prompt / Prompt Expansion Shadow

Goal:

- Store actual 30 candidates shown to Claude.
- Store trim reasons, rank, bucket, dynamic-universe status, hard pin, and soft pin status.
- Record candidates that an experimental prompt expansion would have shown.

Do not expand live prompt input only because p90 MFE is high. Require median/MAE/hit-rate validation.

## 4. Strategy Mismatch Shadow Gate

Goal:

- Track cases where `recommended_strategy` and `strategy_used` differ.
- Simulate whether an experimental rule would have blocked or reduced the entry.
- Compare avoided losses and missed upside.

No live blocking until enough filled samples show a repeatable loss pattern.

## 5. Limited Live Rollout

Only after shadow validation:

US ready_no_signal:

- high confidence
- normal spread
- normal liquidity
- acceptable MAE
- small probe sizing

KR prompt expansion:

- no unlimited cap expansion
- only validated bucket/rank patterns
- exclude low liquidity
- exclude overextended
- cap additional names per day

Strategy mismatch:

- limit only repeat loss patterns
- allow legitimate strategy transitions
- start with warning or reduced size

## 6. AVOID Relaxation Deferred

Broad AVOID relaxation remains deferred.

Reason:

- AVOID mostly acted defensively in current data.
- Misses are isolated.
- Broad relaxation could increase false positives.
