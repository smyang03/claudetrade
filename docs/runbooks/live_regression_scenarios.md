# Live trading regression scenarios

This runbook maps the useful parts of OpenAlice's live-broker scenario catalog
to Claudetrade's existing checks. It is an operating checklist, not a second
execution pipeline.

Principles:

- Broker truth wins over local state.
- Run the applicable scenarios after changing order, fill, position, restart,
  or exit-owner paths.
- Use paper/demo where a scenario requires placing test orders.
- Leave the account at its pre-test position and open-order baseline.
- Every newly found live-path bug must add a deterministic regression test or
  a named preflight check.

| Scenario | Claudetrade check | Manual acceptance when required |
|---|---|---|
| Read-state/PnL agreement | `broker_truth.*`, integrity audit, dashboard broker integrity | Compare broker total evaluation/PnL with the sum of normalized positions. |
| Buy lifecycle and fill awareness | order lifecycle events, pending confirmation reconciliation | Paper order: submit, confirm broker fill, confirm local position quantity and fill price. |
| Resting-order stability | `order_unknown.*`, open-order integrity | Leave a paper limit open across several broker-truth refreshes; status and order id must remain stable. |
| Order amendment identity | pending-order reconciliation | Verify whether KIS retains or replaces the order number and that the new identity is tracked. |
| Protected position / exit owner | `position.exit_ownership_reconciliation` | For software-managed exits, verify broker holding, local owner, owner policy, and bot liveness. For broker-attached orders, verify the protective order at the broker. |
| Standalone stop/order namespace | broker open-order integrity | If broker-native stops are introduced, confirm KIS reports them through the queried order surface. |
| External/manual order observation | broker truth reconciliation | Place/cancel only in paper; verify external state cannot silently overwrite a strategy-owned local position. |
| Restart survival | restart snapshot, handoff cache hygiene, broker truth | Restart with a paper pending order/position and verify quantity, order identity, source strategy, and exit owner survive. |
| Partial close | partial-fill/partial-close reconciliation | Verify broker remainder equals local remainder and the original exit owner remains attached. |
| Cash/notional sizing | order precheck and strategy caps | Verify requested cash cap, integer quantity, fees, FX conversion, and actual broker notional. |
| Error observability | preflight, Telegram error reporting | Force a harmless paper validation error and require an actionable reason rather than a bare exception. |
| Staged rollback | dual-source config and restart procedure | Roll back both `.env.live` and config overrides, restart, then verify effective config. |
| Instrument identity | ticker/market metadata check | Ensure ticker, market, currency, and broker product identity agree before order authority. |
| Units/signs | broker normalization tests | Recheck if derivatives, inverse products, or multiplier-based instruments are introduced. |

## Exit-owner acceptance

`position.exit_ownership_reconciliation` is the single preflight entry point.
It reuses the runtime ownership contract and must not submit, cancel, or modify
orders.

Immediate failures:

- broker position absent from local state;
- local position absent from fresh broker truth;
- broker/local quantity mismatch;
- no valid software exit contract;
- isolated sleeve carrying a generic advisor SELL/recheck flag.

Warnings:

- broker truth is missing or stale, so reconciliation cannot be completed;
- an isolated owner is safely inferable from `source_strategy`, but the
  persisted `exit_owner` metadata is missing.

Dependencies such as bot/process health, Path B run state, and strategy config
remain owned by their existing preflight checks. This runbook deliberately
does not duplicate those checks.
