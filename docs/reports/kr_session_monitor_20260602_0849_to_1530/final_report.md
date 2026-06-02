# US Overnight Monitor Report

- status: completed
- generated_at: 2026-06-02T08:50:13+09:00
- monitor_window: 2026-06-02T08:50:05+09:00 ~ 2026-06-02T15:30:00+09:00
- mode/market/session: live / KR / 2026-06-02
- read_only: True

## Current Operations

- guardian_gate: BLOCK_START ok=False status=blocked
- broker_truth: missing=False stale=False error= last_success=2026-06-01T23:50:08+00:00
- broker_positions/open_orders/fills: 0 / 0 / 0
- open_positions_count: 0
- protected_positions: 0
- pending_sells: 0

## Data Collection

- expected_open/news_due: 2026-06-02T09:00:00+09:00 / 2026-06-02T08:40:00+09:00
- minute_price_latest: data\price\minute\kr\kr_396300.csv age_min=533.19 files=108
- daily_price_latest: data\price\kr\kr_004770.csv age_min=0.01 files=793
- preopen_candidates: exists=True lines=220 age_min=2.24
- preopen_scheduler: exists=True lines=1062 age_min=0.24
- screener_projected_volume: exists=False lines=None age_min=None
- preopen_news: exists=True corp_news_total=31 coverage=0.1 age_min=3.22
- regular_news: exists=True corp_news_total=40 coverage=0.15 age_min=3.02
- daily_digest: exists=True top_news=5 age_min=3.22

## Risk Axes

- broker_exposure: positions=0 open_local_positions=0
- open_orders: broker=0 pending_sell_local=0
- local_unresolved_state: protected=0 order_unknown_events=14
- manual_action_required: 1

## PathB Remediation Separation

- available: True dry_run=True write_supported=False
- current_session_order_unknown: 0 rows=0
- previous_session_order_unknown: 3
- previous_session_stale_active: 6
- apply_eligible_items: 0

## Trading Events Since Monitor Start


### Previous-Session Cleanup Candidates

- US IBM ORDER_UNKNOWN action=broker_reconcile_then_append_audited_resolution apply=False block=broker_truth_not_fresh_trusted
- US HPE ORDER_UNKNOWN action=broker_reconcile_then_append_audited_resolution apply=False block=broker_truth_not_fresh_trusted
- US CRWV ORDER_UNKNOWN action=broker_reconcile_then_append_audited_resolution apply=False block=broker_truth_not_fresh_trusted
- US IBM ORDER_UNKNOWN action=broker_reconcile_required apply=False block=broker_truth_not_fresh_trusted
- US HPE ORDER_UNKNOWN action=broker_reconcile_required apply=False block=broker_truth_not_fresh_trusted
- US CRWV ORDER_UNKNOWN action=broker_reconcile_required apply=False block=broker_truth_not_fresh_trusted
- US NOK FILLED action=verify_position_or_close_event_before_marking_resolved apply=False block=broker_truth_not_fresh_trusted
- US MRVL FILLED action=verify_position_or_close_event_before_marking_resolved apply=False block=broker_truth_not_fresh_trusted
- US EL FILLED action=verify_position_or_close_event_before_marking_resolved apply=False block=broker_truth_not_fresh_trusted
- no entry/closed/hold-review events observed after monitor start

## Claude Usage

- api_usage_delta_since_start: calls=0 input=0 output=0 cost_usd=0.0
- raw_call_files_observed: 0
- by_label: {}
- by_model: {}
- hold_advisor_calls: total=0 by_label={} saved_calls_estimate=0

## State Observations

- no state observations recorded after monitor start

## Guardian Block Causes

- db.order_unknown_unresolved: risk=P2 blocking=True action=로컬 DB 상태와 최근 lifecycle/order_unknown row를 확인 tool=sqlite/manual DB inspection
- db.pathb_stale_active_runs: risk=P1 blocking=True action=PathB stale active run을 broker truth로 대조하고 필요 시 manual reconciliation 처리 tool=PathB ORDER_UNKNOWN/reconcile tools
- db.pathb_lifecycle_window_consistency: risk=P2 blocking=True action=로컬 DB 상태와 최근 lifecycle/order_unknown row를 확인 tool=sqlite/manual DB inspection
- db.pathb_lifecycle_full_consistency: risk=P2 blocking=True action=로컬 DB 상태와 최근 lifecycle/order_unknown row를 확인 tool=sqlite/manual DB inspection
- kis.balance_probe: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- runtime.dashboard_pid_lock: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- state.brain_memory_change_guard: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- broker_truth.kr_stale_state: risk=P1 blocking=True action=broker truth snapshot freshness와 토큰/조회 오류를 먼저 복구 tool=tools/live_preflight.py --mode live --skip-dashboard --json
- broker_truth.us_stale_state: risk=P1 blocking=True action=broker truth snapshot freshness와 토큰/조회 오류를 먼저 복구 tool=tools/live_preflight.py --mode live --skip-dashboard --json
- runtime.process_inventory: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- data.price_csv_integrity.kr: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- data.price_csv_integrity.us: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- ml.decisions_db_health: risk=P2 blocking=True action=로컬 DB 상태와 최근 lifecycle/order_unknown row를 확인 tool=sqlite/manual DB inspection
- external_data.readiness: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- smoke.all: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- [{"classification": "hard_fail", "detail": "unresolved ORDER_UNKNOWN rows=3", "kind": "finding", "name": "db.order_unknown_unresolved", "status": "WARN"}, {"classification": "hard_fail", "detail": "previous-session active Path B rows=6", "kind": "finding", "name": "db.pathb_stale_active_runs", "status": "WARN"}, {"classification": "hard_fail", "detail": "KR snapshot stale", "kind": "finding", "name": "broker_truth.kr_stale_state", "status": "WARN"}, {"classification": "hard_fail", "detail": "US snapshot stale", "kind": "finding", "name": "broker_truth.us_stale_state", "status": "WARN"}]: risk=P1 blocking=True action=PathB stale active run을 broker truth로 대조하고 필요 시 manual reconciliation 처리 tool=PathB ORDER_UNKNOWN/reconcile tools

## Issues

- guardian_block_start: 1

## Recent Issue Samples

- 2026-06-02T08:50:13+09:00 [guardian_block_start] live guardian gate=BLOCK_START
