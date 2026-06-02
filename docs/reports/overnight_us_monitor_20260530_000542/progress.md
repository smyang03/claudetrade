# US Overnight Monitor Report

- status: running
- generated_at: 2026-05-30T00:05:43+09:00
- monitor_window: 2026-05-30T00:05:42+09:00 ~ 2026-05-30T06:00:00+09:00
- mode/market/session: live / US / 2026-05-29
- read_only: True

## Current Operations

- guardian_gate: None ok=None status=running
- broker_truth: missing=False stale=False error= last_success=2026-05-29T15:05:30+00:00
- broker_positions/open_orders/fills: 8 / 0 / 15
- open_positions_count: 8
- protected_positions: 1
- pending_sells: 0

## Risk Axes

- broker_exposure: positions=8 open_local_positions=8
- open_orders: broker=0 pending_sell_local=0
- local_unresolved_state: protected=1 order_unknown_events=3
- manual_action_required: 0

## Trading Events Since Monitor Start

- no entry/closed/hold-review events observed after monitor start

## Claude Usage

- api_usage_delta_since_start: calls=0 input=0 output=0 cost_usd=0.0
- raw_call_files_observed: 0
- by_label: {}
- by_model: {}
- hold_advisor_calls: total=0 by_label={} saved_calls_estimate=0

## State Observations

- protected_position: 1

## Issues

- no warning/error keyword issues observed after monitor start
