# US Swing MICRO capacity and executable-shadow validation — 2026-07-11

## Decision

- Keep `rank1_skip`: trade rank 1 only; do not replace an unaffordable rank 1 with rank 2/3.
- Validate and enforce a maximum `0.5%` chase from the session open. The `1.0%` stress case nearly removed the historical edge.
- Count only the one-slot, whole-share, TP12/SL25 execution-shadow path as forward authority evidence.
- Remain `shadow`. MICRO requires three matured executable-shadow trades and an operator configuration change; there is no automatic promotion.

## Contract reproduced

- OOS input: 879 top-3 rows across 293 decision sessions.
- MICRO sleeve: KRW 500,000 reference capital.
- Per-order cap: KRW 500,000 base × 0.10 multiplier = KRW 50,000.
- One open slot, one new entry per day, whole shares only.
- Entry: next-session open plus adverse entry-slippage sensitivity.
- Exit: TP +12%, catastrophe SL -25%, fifth-session close, SL-first same-bar tie, gap at open.
- Cost: 0.50% and historical USD/KRW conversion.
- Same-day exit/re-entry is disabled.

## Capacity-path result

| Policy / entry slippage | Trades | Net P&L | Sleeve return | PF | 2025 mean / PF | Realized-equity MDD |
|---|---:|---:|---:|---:|---:|---:|
| rank1 / 0.00% | 55 | +KRW 62,381 | +12.48% | 1.690 | +0.595% / 1.136 | -2.61% |
| rank1 / 0.25% | 55 | +KRW 53,194 | +10.64% | 1.538 | +0.421% / 1.094 | -2.72% |
| rank1 / 0.50% | 55 | +KRW 51,648 | +10.33% | 1.514 | +0.420% / 1.092 | -2.61% |
| rank1 / 1.00% stress | 55 | +KRW 1,920 | +0.38% | 1.031 | -2.284% / 0.602 | -10.11% |
| affordable top3 fallback / 0.00% | 69 | +KRW 37,899 | +7.58% | 1.246 | -0.076% / 0.984 | -4.55% |
| affordable top3 fallback / 0.50% | 70 | +KRW 41,350 | +8.27% | 1.284 | -0.418% / 0.917 | -4.83% |

The fallback policy produced more trades but less P&L in three of four slippage scenarios and lost money in the 2025 cohort. It is rejected rather than used to increase utilization.

The one-slot constraint reduced the rank1 path from 293 theoretical daily entries to 55 trades. At zero slippage, 170 decision sessions were blocked by the occupied slot and 68 had an unaffordable rank1. This is the core difference from the earlier per-signal OOS aggregate.

## Executable shadow wiring

Each signal now records:

- selection eligibility and reason;
- target budget, whole-share quantity, FX and KRW price;
- reference-close proxy and conservative entry fill;
- contract exit date/price/reason;
- strategy-matched net KRW return and KRW P&L.

Only `execution_shadow_eligible=1` and a matured `execution_shadow_net_krw_pct` feed the authority gate. The former all-candidate fifth-close average remains diagnostic only. A matured selected trade without an execution outcome becomes a critical forward-data error and blocks authority.

For the 2026-07-10 shadow session, SMCI rank1 was selected at the reference proxy: KRW 50,000 budget, USD/KRW 1,501.15, one whole share. Ranks 2–5 were tagged `rank_outside_micro_contract`.

## Promotion boundary

- Historical OOS was resealed after the policy change: 293 sessions, policy hash matched.
- Capacity evidence passes MICRO at 0–0.5% adverse entry slippage.
- Current executable forward evidence: 0 matured trades; authority remains shadow.
- MICRO forward minimum: 3 matured executable trades, mean net ≥ 0 and PF ≥ 1. This should normally require roughly three one-slot holding cycles, not three months.
- The running process still has its startup snapshot with a 1.0% chase value. Handoff and submission remain disabled, so this creates no order risk; the 0.5% configuration takes effect on the next operator-controlled restart.

## Limits

- Daily bars cannot identify intraday ordering when both TP and SL are hit; SL-first is used.
- Entry slippage is a conservative sensitivity, not observed 5–30 minute fills.
- Drawdown is computed on realized exit equity, not mark-to-market equity.
- Historical market data is primarily Yahoo-based; the separate KIS outcome cross-check supports outcomes but is not a fully independent feature-data reconstruction.
