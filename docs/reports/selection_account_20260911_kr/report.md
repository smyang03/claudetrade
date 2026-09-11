# [VIRTUAL / RESEARCH ONLY] Selection account comparison

Period: 2025-06-04 through 2026-09-09. Market: KR.

Retrospective only. No live authorization. Random bands are not confidence intervals.

| Method | NAV return % | MDD % | Closed / open |
|---|---:|---:|---:|
| baseline | -19.1032 | -34.0546 | 243 / 3 |
| live_rule | -56.5085 | -60.3101 | 297 / 5 |
| risk_diagnostic | -30.5688 | -43.0847 | 241 / 2 |
| random1 (median) | -13.9657 | -38.0535 | see JSON |
| random3 (median) | -18.7390 | -35.9756 | see JSON |
| random1_on_risk_days (median) | -13.2732 | -31.4708 | see JSON |

## Assumptions

- Same daily maximum budget; integer shares; no duplicate holdings or borrowing.
- TP12/close SL25/prior-peak BE4 US only/D7 inclusive; KR cost .25%, US .50%.
- Open fills are hypothetical; no spread, latency, fill rejection or settlement restrictions.
- US FX is constant, not actual KRW FX-inclusive performance.
- Risk IBS>=50 and MA20 discount>=-10 is a rejected diagnostic, not a new strategy.
- live_rule reproduces the live ordering: KR deepest MA20 discount first (kr_fallen_order_bridge), US largest drop-day dollar volume first (us_swing_order_bridge with US_SWING_PICK_ORDER=dvol_desc). US live_rule is identical to baseline by construction.
- KR live MA20 averages the 20 bars before the signal bar, excluding it (kr_fallen_shadow_scan). The ma20_disc diagnostic field includes it.
- Matched dates do not guarantee identical amounts, holding times or exposure.
- Historical input caches may contain revisions and survivorship bias.

## Coverage

Complete: True

```json
{
  "source_rows": 37193,
  "prepared_candidates": 37193
}
```

- All observations are retrospective, including rows labelled backfill=0 upstream.
- KR universe is recorded xkr_fallen3 trades, not a complete pre-entry candidate archive; ETFs may be included.
- US band/MAX and rankings use prior-bar recomputation, not guaranteed original decision-time features.
- live_rule re-ranks this same pool; it does not reproduce the live lane's own universe.
- Missing source/feature data changes the available pool; incomplete coverage prevents inference.
- Absent source sessions are unknown, not proven zero-candidate days. Coverage covers observable inputs only.
