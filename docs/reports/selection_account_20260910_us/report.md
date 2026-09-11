# [VIRTUAL / RESEARCH ONLY] Selection account comparison

Period: 2026-08-12 through 2026-09-08. Market: US.

Retrospective only. No live authorization. Random bands are not confidence intervals.

| Method | NAV return % | MDD % | Closed / open |
|---|---:|---:|---:|
| baseline | -7.4533 | -9.9854 | 13 / 4 |
| risk_diagnostic | -0.7537 | -2.7804 | 2 / 2 |
| random1 (median) | -8.2842 | -9.3045 | see JSON |
| random3 (median) | -7.7390 | -7.8136 | see JSON |
| random1_on_risk_days (median) | -2.3145 | -2.9188 | see JSON |

## Assumptions

- Same daily maximum budget; integer shares; no duplicate holdings or borrowing.
- TP12/close SL25/prior-peak BE4 US only/D7 inclusive; KR cost .25%, US .50%.
- Open fills are hypothetical; no spread, latency, fill rejection or settlement restrictions.
- US FX is constant, not actual KRW FX-inclusive performance.
- Risk IBS>=50 and MA20 discount>=-10 is a rejected diagnostic, not a new strategy.
- Matched dates do not guarantee identical amounts, holding times or exposure.
- Historical input caches may contain revisions and survivorship bias.

## Coverage

Complete: False

```json
{
  "source_rows": 172,
  "outside_us_band_max": 124,
  "short_feature_history": 1,
  "prepared_candidates": 47
}
```

- All observations are retrospective, including rows labelled backfill=0 upstream.
- KR universe is recorded xkr_fallen3 trades, not a complete pre-entry candidate archive; ETFs may be included.
- US band/MAX and rankings use prior-bar recomputation, not guaranteed original decision-time features.
- Missing source/feature data changes the available pool; incomplete coverage prevents inference.
- Absent source sessions are unknown, not proven zero-candidate days. Coverage covers observable inputs only.
