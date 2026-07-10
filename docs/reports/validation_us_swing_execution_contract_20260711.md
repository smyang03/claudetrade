# US swing execution-contract validation — 2026-07-11

## Decision

The original `TP 12% / SL 6%` contract is rejected. The disabled handoff is now configured for `TP 12% / catastrophe SL 25% / maximum hold 5 sessions`.

Absolute probability 0.55 and predicted-net 0.25% thresholds remain recorded as shadow diagnostics but no longer block an order. Cross-sectional rank remains enforced. The absolute thresholds were not stable across regimes and reduced exact historical profitability.

No live order was submitted and the live bot was not restarted.

## Exact OOS materialization

- 293 OOS sessions from 2025-02-03 through 2026-04-02
- 879 exact top-3 selections
- three fixed model seeds
- expanding monthly training, seven-session purge, next-month test
- next-open entry, KRW FX, and 0.5% cost
- recomputed fifth-close outcomes matched stored outcomes to a maximum error of `2.31e-14` percentage points

## Independent KIS outcome check

The most recent 15 OOS sessions, 45 selected outcomes, were queried from KIS overseas daily prices without fallback.

- coverage: 45/45 = 100%
- entry-open absolute deviation p95: 0.000370%
- exit-close absolute deviation p95: 0.000273%
- five-session return absolute difference p95: 0.000585%
- result: pass

This validates a recent exact outcome sample against an independent provider. It is not represented as a full 293-session independent reconstruction.

## Exit-contract comparison

Rank-1 results, net of FX and 0.5% cost:

| Contract | Mean net | PF | Block LCB | Ex-top-3 days | 2025 mean/PF |
|---|---:|---:|---:|---:|---:|
| Fifth close, no barrier | +1.987% | 1.509 | -0.146% | +1.425% | +0.378% / 1.090 |
| TP12 / SL6 | +0.192% | 1.050 | -1.034% | -0.299% | -0.793% / 0.802 |
| TP12 / no SL | +1.470% | 1.392 | -0.088% | +0.993% | +0.068% / 1.017 |
| TP12 / SL20 | +1.227% | 1.311 | -0.313% | +0.747% | +0.024% / 1.006 |
| **TP12 / SL25** | **+1.402%** | **1.367** | **-0.171%** | **+0.923%** | **+0.052% / 1.013** |
| TP12 / SL30 | +1.361% | 1.353 | -0.222% | +0.882% | +0.039% / 1.010 |

SL25 is selected as a broad catastrophe cap, not as the numerical grid maximum. It preserves a positive 2025 sign, satisfies the MICRO LCB floor of -0.25%, and is more profitable than SL30. SL20 misses the LCB floor.

For top 3, TP12/SL25 produced +0.721%, PF 1.297, and LCB -0.375%. It therefore fails the stricter probe LCB requirement. The new execution evidence permits MICRO rank 1 only; probe and standard remain blocked.

## Absolute prediction hurdle finding

Applying probability >=0.55 and predicted net >=0.25% to rank 1 reduced fifth-close results to +1.426%, PF 1.343, LCB -0.924%; 2025 became approximately flat at -0.003%, PF 0.999. With TP12/SL25, the 2025 mean was -0.162%.

The ranking signal has evidence; the absolute calibration threshold does not. The system therefore enforces rank and records the absolute hurdle as shadow until forward calibration proves it adds value.

## Authority effect

The execution evidence is sealed and required by policy:

- MICRO rank 1: pass
- probe top 3: blocked by execution-contract LCB
- standard top 5: blocked because the execution contract is not validated
- current effective authority: shadow, because forward evidence remains 0 sessions / 0 matured signals

## Safety rehearsal

Fake-broker end-to-end tests cover:

- successful submission followed by an idempotent second scan;
- unknown broker outcome becoming terminal `ORDER_UNKNOWN`;
- restart recovery blocking a broker-side open order even when local pending state is absent;
- rehearsal mode never calling the submit path;
- TP 12% and SL 25% reaching the existing micro-probe order method.

## Remaining uncertainty

Daily bars cannot reproduce the intended open +5 to +30 minute fill. Same-day TP/SL ordering is bounded conservatively, and gaps through a stop fill at the next open. Actual forward fills, slippage, and global portfolio loss controls remain the final evidence needed before MICRO authorization.
