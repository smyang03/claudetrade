# Candidate Tier State Machine Plan

Date: 2026-05-07
Status: future implementation plan
Scope: replace `today_tickers` as the primary candidate state with a tiered state machine

## Purpose

The current live fix keeps the existing `today_tickers` flow and adds trainer guards around it: health-aware replacement score, incoming/outgoing delta gate, source/cohort reliability penalty, no-signal grace, and advisory tier snapshots.

That is the correct low-risk live step, but it is not the final trainer model. The final model should make the candidate pool itself stateful. The system should stop thinking in terms of one rotating list and start managing candidates as a coached roster.

## Current State

Primary runtime lists:

- `today_tickers[market]`: active watchlist used by live cycles.
- `trade_ready_tickers[market]`: executable Plan A subset.
- `selection_meta[market]`: Claude selection metadata, candidate actions, price targets, routing records.
- `today_judgment["universe_tickers"]`: replacement source pool.
- `CandidateHealthTracker`: raw health state, ready count, MFE/MAE from first ready.
- `candidate_cohort_reliability`: source/cohort quality feedback, currently used as a penalty.
- `candidate_trainer_{market}_{date}.json`: advisory tier snapshot only.

Limitation:

- `today_tickers` is still a flat list.
- Replacement still edits that list directly.
- Advisory tiers do not own lifecycle transitions.
- `BENCH` and `QUARANTINE` are not first-class runtime states.
- `CORE` protection exists through score/gate behavior, not through a hard state transition contract.

## Target Model

Make a per-market candidate tier book the source of truth:

```text
CandidateTierBook[market]
  CORE
  WATCH
  PROBATION
  BENCH
  QUARANTINE
```

`today_tickers`, `trade_ready_tickers`, `selection_meta.watchlist`, and partial replacement output become derived views from the tier book, not independent mutable lists.

### Tier Meanings

| Tier | Meaning | Can Enter? | Can Be Replaced? | Typical Source |
|---|---|---:|---:|---|
| `CORE` | Proven live candidate: ready action, valid signal, positive MFE, held position, or strong health | Yes, if route/gates pass | No, except hard safety/session end | trade_ready, PROBE_READY, BUY_READY, active holding |
| `WATCH` | Valid candidate under observation | No direct buy unless promoted by action/signal | Yes, if health weakens or no-signal matures | Claude watchlist, fresh screener |
| `PROBATION` | Candidate is deteriorating or stale but not yet invalid | No | Yes, primary replacement-out pool | no-signal, WATCH_WEAK, soft block cluster |
| `BENCH` | Reserve candidates not active in live cycle | No | N/A; can be promoted to WATCH only through gate | screener, theme, sector, relative strength |
| `QUARANTINE` | Unsafe for the session | Never | N/A; cannot be promoted same day unless explicit recovery policy allows | same-day stop, failed ready, hard block, source contamination |

`ACTIVE_READY` should be a flag or route state inside `CORE`/`WATCH`, not a sixth primary tier. That keeps the state machine simple while preserving execution readiness.

## Core Invariants

1. `QUARANTINE` cannot generate Plan A orders, PathB waits, add-ready routes, or replacement-in promotions.
2. `BENCH` cannot be traded directly. It must first pass promotion into `WATCH` or `CORE`.
3. `CORE` cannot be removed by no-signal counters alone.
4. Held positions are always treated as `CORE` or a separate `POSITION_LOCKED` flag.
5. Incoming replacement must beat outgoing by score delta before promotion.
6. A stop/loss-cap on a ticker moves that ticker to `QUARANTINE` for the session.
7. Disaster stop-cluster can still force market-wide new-buy block regardless of candidate tiers.
8. Tier state must be reconstructable after restart from persisted state plus broker/risk positions.
9. Claude output is advisory. Runtime safety owns tier promotion and order eligibility.

## Proposed State Schema

File:

```text
state/candidate_tier_book_{MARKET}_{YYYYMMDD}.json
```

Shape:

```json
{
  "schema_version": "candidate_tier_book.v1",
  "market": "US",
  "session_date": "2026-05-07",
  "updated_at": "2026-05-07T23:05:00",
  "tiers": {
    "CORE": ["AAPL"],
    "WATCH": ["MSFT"],
    "PROBATION": ["AMD"],
    "BENCH": ["NVDA", "TSLA"],
    "QUARANTINE": ["EAT"]
  },
  "records": {
    "AAPL": {
      "tier": "CORE",
      "first_seen_at": "2026-05-07T22:30:00",
      "last_seen_at": "2026-05-07T23:05:00",
      "last_transition_at": "2026-05-07T22:45:00",
      "transition_reason": "BUY_READY",
      "source": "candidate_actions_v1",
      "cohort_key": "US|base_universe|volume_surge|high|shallow|momentum",
      "trainer_score": 8.4,
      "health_state": "STRONG_READY",
      "ready_count": 2,
      "mfe_pct": 3.2,
      "mae_pct": -0.4,
      "flags": ["ACTIVE_READY"],
      "price_targets": {},
      "route": {
        "final_action": "BUY_READY",
        "expires_at": "2026-05-07T22:50:00"
      }
    }
  }
}
```

## Transition Rules

### Promotion

| From | To | Condition |
|---|---|---|
| `BENCH` | `WATCH` | incoming score passes min score and cohort penalty is acceptable |
| `WATCH` | `CORE` | `PROBE_READY`, `BUY_READY`, real strategy signal, filled position, or strong MFE with healthy MAE |
| `PROBATION` | `WATCH` | health recovers and no hard block remains |
| `PROBATION` | `CORE` | fresh ready action plus healthy price target/route gate |
| any non-quarantine | `QUARANTINE` | stop/loss-cap, failed ready, same-day reentry block, hard safety block |

### Demotion

| From | To | Condition |
|---|---|---|
| `WATCH` | `PROBATION` | no-signal threshold, `WATCH_WEAK`, repeated soft blocks |
| `CORE` | `WATCH` | ready route expired with no signal and health still neutral |
| `CORE` | `PROBATION` | MFE spike reversed into degraded ready state, no holding, no active route |
| `WATCH`/`PROBATION` | `BENCH` | active capacity exceeded and candidate loses delta comparison |
| `CORE`/`WATCH`/`PROBATION` | `QUARANTINE` | stop/loss-cap, failed ready, hard safety |

### Session Boundary

At session open:

- Restore prior persisted tier book only for restart continuity within the same session date.
- For a new session date, start with empty tiers.
- Carry no `QUARANTINE` across sessions unless a multi-day cooldown feature is explicitly enabled.
- Rebuild `CORE` from broker/risk positions after reconnect.
- Rebuild `WATCH/BENCH` from fresh screener and Claude selection.

## Market-Specific Policy

### US

Default stricter replacement discipline:

- Delta gate enabled by default.
- Larger ready-action grace window than KR.
- Concentrated stop-cluster may relax market-wide block until disaster threshold.
- Premarket/regular-session candidates should carry source tags so extended-hours sources can be scored separately.
- `BENCH` should be larger because US has broader liquidity and more substitute candidates.

Recommended initial caps:

| Key | Suggested |
|---|---:|
| `CORE_MAX` | 4 |
| `WATCH_MAX` | 8 |
| `PROBATION_MAX` | 4 |
| `BENCH_MAX` | 20 |
| `QUARANTINE_MAX` | unlimited |

### KR

Default more conservative migration:

- Keep delta gate opt-in until replay proves no missed strong names.
- Opening range and VI/liquidity risks should weigh more heavily in promotion.
- `CORE` protection should be strong once `PROBE_READY` or valid opening signal appears.
- `BENCH` can be smaller because tradable high-quality substitutes are narrower intraday.

Recommended initial caps:

| Key | Suggested |
|---|---:|
| `CORE_MAX` | 3 |
| `WATCH_MAX` | 7 |
| `PROBATION_MAX` | 3 |
| `BENCH_MAX` | 12 |
| `QUARANTINE_MAX` | unlimited |

## Source/Cohort Reliability Integration

Source quality should affect tier transitions, not only candidate scores.

Track per cohort:

- sample count
- ready conversion count
- healthy count
- weak count
- stop/loss-cap count
- average MFE/MAE
- average time-to-signal
- replacement-in success rate

Use cases:

- Bad source/cohort cannot promote directly from `BENCH` to `CORE`.
- Bad source/cohort requires higher incoming delta.
- Repeated source failure moves new candidates from that source into `BENCH`, not `WATCH`.
- Strong source can get larger `BENCH` allocation but still cannot bypass hard safety.

## Derived Views

After state machine migration:

```text
today_tickers =
  CORE + WATCH + PROBATION
  capped by market watchlist size
  excluding QUARANTINE

trade_ready_tickers =
  CORE records with ACTIVE_READY or executable route
  plus WATCH records with fresh BUY_READY and price target guard pass

selection_meta.watchlist =
  derived from today_tickers

selection_meta.trade_ready =
  derived from trade_ready_tickers

candidate replacement source =
  BENCH only
```

This prevents accidental direct trading from raw screener output.

## Implementation Plan

### Phase 0: Keep Current Live Behavior

No code change. Current implementation remains:

- advisory tier snapshot
- health-aware replacement score
- delta gate
- cohort reliability penalty
- no-signal grace
- stop-cluster concentrated US scope

Exit criteria:

- At least 2 KR sessions and 2 US sessions of logs with no trainer regression.
- Review candidate trainer snapshots after each session.

### Phase 1: Build Tier Book Module in Shadow

Add:

- `runtime/candidate_tier_state.py`
- `CandidateTier`
- `CandidateTierRecord`
- `CandidateTierBook`
- load/save helpers
- pure transition functions

No runtime decisions consume it yet.

Tests:

- transition purity
- restart load/save
- same-day quarantine
- held-position core reconstruction
- US/KR cap behavior

Exit criteria:

- Shadow tier book matches current advisory snapshot within expected differences.

### Phase 2: Derive Lists Without Changing Orders

Add adapter:

```python
_candidate_tier_book(market)
_derive_today_tickers_from_tiers(market)
_derive_trade_ready_from_tiers(market)
```

Run in shadow:

- compute derived `today_tickers_shadow`
- compute diff against current `today_tickers`
- write funnel/tier diff event

Do not replace live lists yet.

Exit criteria:

- No unexplained `QUARANTINE` leakage.
- No missing held position.
- Strong ready candidates are not demoted unexpectedly.

### Phase 3: Move Partial Replacement to Tier Transitions

Change partial replacement:

- replacement-out is selected from `PROBATION`, not the flat list.
- replacement-in is selected from `BENCH`.
- `WATCH` can become `PROBATION`.
- `CORE` can only demote under explicit rules.

Keep `today_tickers` as derived output but still write it for compatibility.

Tests:

- CORE no-signal does not replace.
- PROBATION replaced only if BENCH incoming clears delta.
- no incoming clears delta means slot can remain empty or candidate remains probation.
- QUARANTINE never returns through replacement.

Exit criteria:

- Partial replacement no longer directly mutates arbitrary watchlist members.

### Phase 4: Consume Tier Book in Live Cycle

Change live scanning:

- iterate `CORE + WATCH + PROBATION`
- skip `BENCH`
- hard block `QUARANTINE`
- route Plan A/PathB from executable tier records

Compatibility:

- still populate `today_tickers`, `trade_ready_tickers`, and `selection_meta` for dashboard and old code.
- mark them as derived, not authoritative.

Exit criteria:

- Full pytest.
- One market in shadow/live hybrid before both markets.
- Manual review of all candidate route diffs.

### Phase 5: Make Tier Book Source of Truth

After stable shadow/hybrid sessions:

- `today_tickers` becomes a cached derived field only.
- `selection_meta.watchlist` is generated from tier book.
- replacement and live route decisions read tier records directly.
- dashboard shows tier book as primary candidate state.

Exit criteria:

- No direct write path to `today_tickers` remains except adapter compatibility.
- All candidate lifecycle events have transition reason.
- Restart rebuild test passes for KR and US.

## Rollout Flags

Suggested flags:

```text
CANDIDATE_TIER_BOOK_ENABLED=false
CANDIDATE_TIER_BOOK_SHADOW_ONLY=true
KR_CANDIDATE_TIER_BOOK_LIVE=false
US_CANDIDATE_TIER_BOOK_LIVE=false
CANDIDATE_TIER_BOOK_DERIVE_TODAY_TICKERS=false
CANDIDATE_TIER_BOOK_BLOCK_QUARANTINE_LIVE=true
```

Rollout order:

1. shadow only
2. derive-only diff
3. one market live for replacement only
4. one market live for scan source
5. both markets live

## Risks And Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| State machine too strict misses valid intraday moves | lower trade count, missed profit | shadow diff, promotion threshold tuning, keep manual override |
| Restart loads stale tiers | wrong watchlist or quarantine | session date guard, broker position reconciliation, startup sanity checks |
| `today_tickers` compatibility breaks old paths | hidden runtime bug | derived adapter phase before source-of-truth switch |
| BENCH becomes a hidden fourth list | confusing ops | dashboard primary tier display and transition logs |
| CORE locks weak candidates too long | stale capital usage | CORE demotion rule based on degraded ready and no active position |
| Source reliability overfits one bad session | good source penalized too much | minimum sample, capped penalty, per-market separate state |
| US/KR behavior diverges too much | hard to debug | shared module, market policy config only at thresholds |

## QA Plan

Required unit tests:

- tier transition matrix
- quarantine is absorbing for the session
- CORE protection from no-signal
- degraded CORE demotion only when no holding and no active route
- BENCH cannot be traded directly
- incoming delta gate controls BENCH to WATCH promotion
- source reliability changes promotion threshold
- persisted state reload preserves transition reasons
- US and KR cap defaults differ correctly

Required integration tests:

- `select_tickers` result populates tier book.
- candidate actions promote WATCH to CORE.
- PathB wait only registers from eligible tier.
- stop/loss-cap moves ticker to QUARANTINE.
- partial reselect modifies tiers and derived `today_tickers`.
- dashboard status resolves tier before legacy watch/trade_ready.

Required replay checks:

- Compare old flat-list behavior vs tier-derived behavior for recent KR/US sessions.
- Count missed ready candidates.
- Count prevented weak replacements.
- Count quarantine leaks, expected zero.
- Compare trade count, daily PnL gate behavior, and stop-cluster behavior.

Required live shadow checks:

- `candidate_tier_diff` event every candidate cycle.
- `tier_transition` event for every tier move.
- alert if `QUARANTINE` appears in derived `trade_ready`.
- alert if held position is not `CORE`.
- alert if `today_tickers` derived size is zero while BENCH has valid candidates.

## Definition Of Done

The migration is complete only when:

- `CandidateTierBook` is the authoritative per-market candidate state.
- `today_tickers` is derived and compatibility-only.
- `trade_ready_tickers` is derived from executable tier records.
- `BENCH` and `QUARANTINE` are enforced by runtime, not just logged.
- both KR and US have restart recovery tests.
- full pytest passes.
- at least one KR and one US live shadow session show no critical tier diff.

## Notes For Future Implementation

Do not combine this migration with unrelated signal or order-routing changes. This is a state ownership refactor. The safest path is to keep the current trainer guards active, add the tier book in shadow, then progressively move write ownership from `today_tickers` into the tier book.

The current live changes are intentionally a bridge, not throwaway work. The health score, cohort reliability, no-signal grace, delta gate, and stop-cluster policy should become inputs into the tier transition engine.
