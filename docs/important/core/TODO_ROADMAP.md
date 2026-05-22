# TODO Roadmap

Updated: 2026-05-22

This file is a compact active-roadmap snapshot. The actionable source of truth for new work is [../ACTIVE_WORK.md](../ACTIVE_WORK.md).

## P0

- Live start truth gate: preflight, guardian, broker open orders, quarantine, ORDER_UNKNOWN.
- KIS fill truth verification: real full/partial/cancel payloads and restart recovery.
- V2 canonical performance table: deduped KR/US fill and outcome truth from lifecycle events.
- Candidate audit live link: execution decision/event ids must be written and visible.
- Counterfactual outcome backfill: 30m/60m/close returns, MFE/MAE, source quality.
- Metric contract: raw/dedupe, live/paper, market, source DB, and bucket labels.
- Prompt overlay shadow gate: no live promotion until the data gate passes.

## P1

- KR action schema and route split.
- KR entry timing gate review.
- RiskManager KR/US live adapter migration.
- Safety/equity audit source and lag fields.
- US KIS ranking screener first-source integration with fallback.
- Live config safety for `/setorder` and PathB KR-on/US-on policy.

## P2

- Market index expansion in shadow mode.
- Momentum shadow labels.
- Hybrid-lite/watch trigger analysis.
- PathB EXPIRED and sell pending remainder handling.
- CandidateTierBook shadow.
- KRX/BigKinds/theme injection shadow.

## P3

- L3 price collection inject.
- Dual runtime split.
- Brain Train mode.
- New intraday/VWAP/momentum gates.

## Rule

Do not create separate active plan files for these items. Update [../ACTIVE_WORK.md](../ACTIVE_WORK.md) and keep source evidence only when needed.
