# US Overnight Monitor Report

- status: completed
- generated_at: 2026-05-30T00:20:23+09:00
- monitor_window: 2026-05-30T00:20:23+09:00 ~ 2026-05-30T06:00:00+09:00
- mode/market/session: live / US / 2026-05-29
- read_only: True

## Current Operations

- guardian_gate: BLOCK_START ok=False status=blocked
- broker_truth: missing=False stale=False error= last_success=2026-05-29T15:20:21+00:00
- broker_positions/open_orders/fills: 7 / 0 / 16
- open_positions_count: 7
- protected_positions: 1
- pending_sells: 0

## Risk Axes

- broker_exposure: positions=7 open_local_positions=7
- open_orders: broker=0 pending_sell_local=0
- local_unresolved_state: protected=1 order_unknown_events=3
- manual_action_required: 1

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

## Guardian Block Causes

- db.order_unknown_unresolved: risk=P2 blocking=True action=로컬 DB 상태와 최근 lifecycle/order_unknown row를 확인 tool=sqlite/manual DB inspection
- db.pathb_stale_active_runs: risk=P1 blocking=True action=PathB stale active run을 broker truth로 대조하고 필요 시 manual reconciliation 처리 tool=PathB ORDER_UNKNOWN/reconcile tools
- db.pathb_lifecycle_window_consistency: risk=P2 blocking=True action=로컬 DB 상태와 최근 lifecycle/order_unknown row를 확인 tool=sqlite/manual DB inspection
- db.pathb_lifecycle_full_consistency: risk=P2 blocking=True action=로컬 DB 상태와 최근 lifecycle/order_unknown row를 확인 tool=sqlite/manual DB inspection
- kis.balance_probe: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- runtime.bot_pid_lock: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- runtime.dashboard_pid_lock: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- broker_truth.kr_stale_state: risk=P1 blocking=True action=broker truth snapshot freshness와 토큰/조회 오류를 먼저 복구 tool=tools/live_preflight.py --mode live --skip-dashboard --json
- runtime.process_inventory: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- market.session_calendar: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- data.price_csv_integrity.kr: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- data.price_csv_integrity.us: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- ml.decisions_db_health: risk=P2 blocking=True action=로컬 DB 상태와 최근 lifecycle/order_unknown row를 확인 tool=sqlite/manual DB inspection
- external_data.readiness: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- us.today_order_unknown_review: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- smoke.all: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- [{"classification": "hard_fail", "detail": "previous-session active Path B rows=11", "kind": "finding", "name": "db.pathb_stale_active_runs", "status": "WARN"}, {"classification": "hard_fail", "detail": "KR snapshot stale", "kind": "finding", "name": "broker_truth.kr_stale_state", "status": "WARN"}]: risk=P1 blocking=True action=PathB stale active run을 broker truth로 대조하고 필요 시 manual reconciliation 처리 tool=PathB ORDER_UNKNOWN/reconcile tools

## Issues

- guardian_block_start: 1

## Recent Issue Samples

- 2026-05-30T00:20:23+09:00 [guardian_block_start] live guardian gate=BLOCK_START
