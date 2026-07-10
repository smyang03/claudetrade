# US 5-session swing accelerated evidence lane (2026-07-10)

## Outcome

The calendar wait was replaced with a staged evidence contract:

1. sealed purged historical OOS evidence,
2. immutable pre-open shadow signals and five-session matured KRW returns,
3. `shadow -> micro(0.10x) -> probe(0.25x) -> standard` authority caps.

No live order path is connected. `US_SWING_AUTHORITY_MODE=shadow` is the effective setting, auto-promotion is disabled, and the running trading bot was not restarted.

## Frozen historical result

Dataset: 13,672 point-in-time candidate rows. OOS: 293 sessions from 2025-02-03 through 2026-04-02. Features stop at session close D, entry is D+1 open, exit is the fifth session close, and return includes KRW=X and 0.50% cost.

| Cohort | Mean net | PF | Session block LCB | Ex top-3 days | Mean at 0.80% cost |
|---|---:|---:|---:|---:|---:|
| top3 | +1.357% | 1.512 | -0.152% | +1.080% | +1.057% |
| top5 | +0.779% | 1.348 | -0.291% | +0.583% | +0.479% |

The cost stress keeps the selected trades fixed and deducts the additional 0.30%; it does not retrain the model. MICRO uses top3 evidence because it permits only one new position per day. PROBE and STANDARD require both top3 and top5, with positive historical block LCB.

## Forward gates

MICRO readiness requires at least 5 matured entry sessions and 15 matured signals, positive mean, PF >= 1.0, no critical data error, and the sealed top3 historical gate. PROBE requires 15 sessions, 60 signals, mean >= 0.25%, PF >= 1.2, positive block LCB and positive ex-top3-days, plus top3/top5 historical LCB above zero. STANDARD raises the forward sample to 40 sessions and 150 signals.

Changing the policy invalidates the historical artifact by SHA-256 mismatch. A failed gate always demotes the effective mode; it does not rely on the caller to size safely.

## 2026-07-10 first shadow signal

Fresh eligible universe: 58. ONTO and SAH were rejected because their feature bar was one session stale. RIVN was removed by the dated qualitative event-risk veto for its SEC share-offering prospectus.

Recorded pending ranks: SMCI, AVAV, ORCL, QS, QGEN. Their intended entry reference is the 2026-07-10 open and the matured outcome is the fifth trading-session close, expected 2026-07-16. Raw predicted returns are ranking outputs, not an enforceable absolute-return forecast.

## Components

- `config/us_swing_accelerated.json`: frozen thresholds and authority caps.
- `runtime/us_swing_authority.py`: fail-closed authority contract.
- `tools/us_swing_sealed_validation.py`: ensemble purged OOS validator.
- `tools/us_swing_shadow_runner.py`: daily scoring, veto, ledger and maturation.
- `tools/us_swing_preflight.py`: operator readiness report.
- `data/analysis/us_swing_shadow.db`: isolated signal/outcome ledger.
- `state/us_swing_historical_evidence.json`: sealed evidence.
- `state/us_swing_status.json`: latest forward and authority status.

The pre-open scheduler has a US-only shadow job at ten minutes before the regular open when `US_SWING_SHADOW_SCHEDULER_ENABLED=true`. The existing scheduler process predates this code and must be restarted during the next controlled process restart; today's signal was run manually.

## Commands

```powershell
python tools/us_swing_sealed_validation.py
python tools/us_swing_shadow_runner.py --session-date YYYY-MM-DD
python tools/us_swing_shadow_runner.py --session-date YYYY-MM-DD --mature-only
python tools/us_swing_preflight.py
```

## Remaining limitation

The sealed market history is Yahoo-based. A second vendor or survivorship-bias-free universe audit remains desirable, but it is not represented as completed evidence. Runtime slippage, candidate-pipeline fidelity, and qualitative veto behavior are validated only by the forward ledger.
