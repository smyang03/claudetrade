# US VIX term / breadth S2-S3 revalidation (2026-07-11)

## Decision

- S2 VIX term is closed. Do not add a defensive throttle, offensive boost, or a separate VIX-term shadow tag.
- S3 `narrow melt-up` is not a validated profit engine. Keep prior-close breadth as continuous diagnostic attribution inside the existing US 5-session swing shadow ledger only.
- Breadth does not affect rank, size, authority, or orders. Reconsider it only after historical and forward signs agree with adequate independent sessions.

## Why the earlier S2 result was insufficient

The earlier comparison used the mean five-session return of the whole point-in-time candidate pool. The live challenger selects the exact top3 from a three-seed ensemble, so the whole-pool result was not strategy-matched.

The corrected test reconstructs 293 monthly OOS sessions with a seven-session purge, the production three-seed ensemble, top3 selection, KRW FX and 0.50% cost.

| Term state | Sessions | Mean net | PF | P10 | Ex top-3 days |
|---|---:|---:|---:|---:|---:|
| Contango | 255 | +1.339% | 1.503 | -8.371% | +1.021% |
| Backwardation | 38 | +1.474% | 1.577 | -8.214% | +0.351% |

The small aggregate advantage in backwardation is more concentrated, not a defensive tail improvement. There are only 11 independent backwardation episodes.

VIX-level controls also reject a stable offensive interpretation:

- VIX 20-25: backwardation +1.787% versus contango +4.720%.
- VIX 25-30: backwardation +2.273%, but ex-top3-days -0.488%.
- VIX 30-40: backwardation -0.777%, PF 0.799, ex-top3-days -4.655%.
- VIX 40+: four sessions only; mean +3.640% but ex-top3-days -14.214%.

Therefore the correct S2 outcome is not “wait for more backwardation.” It is “term structure does not provide a stable strategy-matched lever.”

## S3 window identity audit

The proposed dates are not one homogeneous profit window:

| Entry date | Trades | Winners / losers | Mean eventual net | Known PnL KRW | Identity |
|---|---:|---:|---:|---:|---|
| 2026-04-28 | 0 | 0 / 0 | n/a | n/a | no new filled profit window |
| 2026-05-07 | 4 | 2 / 2 | +2.749% | +23,715 | positive but right-tail concentrated |
| 2026-06-17 | 9 | 3 / 6 | -0.812% | -39,316 | losing entry window |

Calling all three “narrow melt-up profit windows” mixes debate dates, entries and exits.

## Breadth lead-lag result

Timing is controlled as follows:

- Historical swing OOS: breadth at D close, entry next session open.
- Actual system entries: strictly previous available US market close. Same-day close is never used for an intraday entry.
- Holiday alignment uses the previous observed market session, not a generic business-day shift.

Historical top3 OOS is non-monotonic:

- Lowest advancer quartile: +2.422%, PF 2.016.
- Next advancer quartile: +0.128%, PF 1.043.
- Most extreme five-day RSP/SPY contraction: -0.458%, PF 0.866.
- Strongest five-day RSP/SPY expansion: +2.338%, PF 2.218.
- Extreme same-day narrow excess: +0.701%, while the near-balanced quartile is +2.550%.

This rejects “the narrower, the better.”

Actual 42 entry sessions show a suggestive but unconfirmed relationship:

- Prior-close narrow excess correlation with eventual daily mean net: -0.294.
- Two-sided permutation p-value: 0.0626.
- The strongest positive equal-weight excess quartile had mean -1.445%, PF 0.026.

That actual direction conflicts with the OOS result, where positive breadth was not consistently harmful. It cannot be promoted as a gate or size multiplier.

## Implemented improvement

The existing `us_swing_shadow.db` signal rows now store:

- `breadth_context_date`
- `prior_spy_return_pct`
- `prior_narrow_excess_pct`
- `prior_rsp_spy_ratio_5d_pct`
- `prior_adv_pct`
- `breadth_context_state` (`NARROW`, `BALANCED`, `BROAD`, `MISSING`)

These fields are diagnostic only. `summarize_forward()` reports matured results by breadth state but the authority contract does not consume them.

The 2026-07-10 signal was backfilled with 2026-07-09 context:

- state `BALANCED`
- SPY return +0.847%
- RSP-minus-SPY return -0.234%
- five-day RSP/SPY change -0.750%
- advancer ratio 68.0%

## Reconsideration contract

Breadth becomes a lever candidate only if all are true:

1. At least 40 independent forward entry sessions.
2. At least 10 sessions in each compared state, or a continuous regression with adequate coverage.
3. Direction agrees with a frozen historical OOS definition.
4. Session-block bootstrap lower bound for the proposed state contrast is positive.
5. Ex-top3-days contrast remains positive.
6. Net benefit survives 0.80% cost and does not reduce the base strategy's overall net.
7. Explicit operator approval before any authority or sizing change.

Until then, Claude may explain the context or apply a dated event-risk veto, but cannot promote a candidate because of breadth.

## Reproduction

```powershell
python tools/us_regime_lead_lag_review.py
python tools/us_swing_shadow_runner.py --session-date YYYY-MM-DD
python tools/us_swing_preflight.py
```

Machine-readable output: `reports/us_regime_lead_lag_review_20260711.json`.
