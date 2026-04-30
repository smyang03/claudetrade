# Path B ORDER_UNKNOWN Dashboard Reconcile Plan - 2026-05-01

## Scope

Investigate `/pathb` live dashboard rows that show US Path B runs as `ORDER_UNKNOWN`, `path_a_origin_possible`, and no broker evidence while the broker-truth snapshot has fills or positions.

## Findings

- `/pathb` reads `/api/v2/ops?market=US&mode=live`.
- The dashboard is mostly reflecting the API response; this is not only a frontend rendering issue.
- `data/v2_event_store.db` has four current-session US Path B runs stuck in `ORDER_UNKNOWN`.
- The stuck runs have `order_unknown_resolution=path_a_origin_possible` and saved broker evidence fields set to false.
- Broker truth has current evidence for the same symbols:
  - `TEVA`: broker position and buy fill.
  - `GOOGL`, `QCOM`, `VIAV`: buy fills and later sell fills.
- The Path B reconciler misread generic V2 `FILLED` lifecycle events as Path A evidence because those generic events lacked `path_type/path_run_id` even though they shared the same Path B decision/order identity.
- Generic sell execution later emitted `CLOSED` lifecycle events with Path B `path_run_id`, but the Path B run status was not updated because the sell path did not notify Path B storage.
- Broker truth refresh is stale because the local environment cannot connect to KIS (`WinError 10013`). This is an environment/network permission issue and separate from the Path B state mismatch.

## Improvement List

1. Prevent Path B from treating its own generic lifecycle fill events as Path A evidence.
2. Let Path B reconcile unknown rows against broker truth before returning `path_a_origin_possible`.
3. Add a Path B external-close sync hook and call it when a generic sell closes a Path B position.
4. Make `/api/v2/ops` enrich compact Path B rows with current broker-truth evidence so the dashboard does not show stale `없음` when the snapshot has evidence.
5. Add focused tests for the false Path A evidence case and external Path B close sync.
6. Validate API output against the existing `/pathb` data after changes.

## QA Checklist

- [x] Unit tests cover generic Path B fill events not being treated as Path A.
- [x] Unit tests cover generic sell close updating a Path B run to `CLOSED`.
- [x] `/api/v2/ops` Path B rows show current broker evidence when broker truth has matching rows.
- [x] Existing Path B/order-unknown tests still pass.
- [x] Compare this plan against final changes and list any gaps.

## Final QA Notes

- Direct `build_v2_ops_summary(market="US", runtime_mode="live", session_date="2026-04-30")` now reports `status_counts={'CLOSED': 4, 'CANCELLED': 7, 'WAITING': 1, 'ORDER_UNKNOWN': 1}`.
- The affected rows resolve as:
  - `TEVA`: remains `ORDER_UNKNOWN`, but now shows broker position and today-fill evidence.
  - `GOOGL`, `QCOM`, `VIAV`: show effective `CLOSED` from lifecycle, with stored status preserved as `ORDER_UNKNOWN` for audit.
- Existing server at `127.0.0.1:5000` was still serving the old imported code during QA; restart/reload is required for the browser to show the new API shape.
- Remaining external risk: KIS broker truth refresh still fails from this environment with `WinError 10013`, so account snapshots can remain stale until network/socket permission is fixed.
