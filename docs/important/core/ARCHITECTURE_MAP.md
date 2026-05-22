# Architecture Map

Updated: 2026-05-22

## Runtime Shape

```text
trading_bot.py
  -> session and market orchestration
  -> candidate selection
  -> Claude selection and judgment
  -> strategy signals and PathB price plans
  -> action routing
  -> affordability and risk checks
  -> broker order execution
  -> lifecycle, audit, ML, dashboard, logs
```

## Main Components

| Area | Main Paths | Role |
| --- | --- | --- |
| Main loop | `trading_bot.py` | KR/US session flow, candidate handling, route merge, order path. |
| Broker/KIS | `kis_api.py`, `runtime/broker_truth_snapshot.py` | Order, balance, fill, and broker truth integration. |
| Risk | `risk_manager.py`, `runtime/`, `execution/` | Affordability, exposure, halt, quarantine, and market risk gates. |
| Path A | `trading_bot.py` | Claude selection, strategy signal, affordability/risk, order creation. |
| Path B | `runtime/pathb_runtime.py` | Claude price-plan driven entry/exit, now live for KR and US by approved gate. |
| Routing | `runtime/action_routing.py` | RouteDecision merge point for Path A and Path B actions. |
| Strategy | `strategy/` | Signal logic, adaptive params, market policy inputs. |
| Audit | `audit/`, `data/audit/` | Candidate audit, counterfactual stores, traceability. |
| Lifecycle/ML | `lifecycle/`, `ml/`, `data/ml/` | V2 events, canonical performance, legacy decision logs. |
| Dashboard | `dashboard/` | Live truth, status, PnL, candidate audit, and operator views. |
| Tools | `tools/` | Preflight, guardian, sync, backfill, analysis, and operational scripts. |

## Truth Priority

1. Broker holdings, open orders, and fills.
2. V2 lifecycle and canonical performance for live fill/performance truth.
3. Candidate audit and ticker selection DB for candidate trace and quality.
4. Legacy `data/ml/decisions.db` for signal/evaluation history, not sole fill truth.
5. `state/brain.json` for policy memory only.

## Safety Principles

- Broker distrust or quarantine blocks new entries before local strategy preference.
- Selection quality and execution/risk failures must be diagnosed separately.
- PathB live gates and order-size settings are operator-controlled configuration.
- AI can advise selection and HOLD/SELL reasoning, but cannot override broker truth, hard stops, or final order amount.

## Current Docs

- Active work: [../ACTIVE_WORK.md](../ACTIVE_WORK.md)
- Always analyze: [../ALWAYS_ANALYZE.md](../ALWAYS_ANALYZE.md)
- Inventory: [DOCUMENTATION_INVENTORY.md](DOCUMENTATION_INVENTORY.md)
