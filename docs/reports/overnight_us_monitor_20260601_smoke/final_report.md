# US Overnight Monitor Report

- status: completed
- generated_at: 2026-06-01T20:47:42+09:00
- monitor_window: 2026-06-01T20:47:37+09:00 ~ 2026-06-02T07:00:00+09:00
- mode/market/session: live / US / 2026-06-01
- read_only: True

## Current Operations

- guardian_gate: BLOCK_START ok=False status=blocked
- broker_truth: missing=False stale=True error= last_success=2026-06-01T06:59:10+00:00
- broker_positions/open_orders/fills: 8 / 0 / 0
- open_positions_count: 8
- protected_positions: 1
- pending_sells: 0

## Data Collection

- expected_open/news_due: 2026-06-01T22:30:00+09:00 / 2026-06-01T22:10:00+09:00
- minute_price_latest: data\price\minute\us\us_ZETA.csv age_min=3353.19 files=139
- daily_price_latest: data\price\us\us_YSS.csv age_min=826.64 files=1046
- preopen_candidates: exists=True lines=540 age_min=16.62
- preopen_scheduler: exists=True lines=1248 age_min=0.6
- screener_projected_volume: exists=True lines=9 age_min=16.62
- preopen_news: exists=False corp_news_total=None coverage=None age_min=None
- regular_news: exists=True corp_news_total=130 coverage=1.0 age_min=825.9
- daily_digest: exists=False top_news=None age_min=None

## Risk Axes

- broker_exposure: positions=8 open_local_positions=8
- open_orders: broker=0 pending_sell_local=0
- local_unresolved_state: protected=1 order_unknown_events=3
- manual_action_required: 2

## PathB Remediation Separation

- available: True dry_run=True write_supported=False
- current_session_order_unknown: 0 rows=0
- previous_session_order_unknown: 0
- previous_session_stale_active: 6
- apply_eligible_items: 0

## Trading Events Since Monitor Start


### Previous-Session Cleanup Candidates

- US SOFI FILLED action=verify_position_or_close_event_before_marking_resolved apply=False block=broker_truth_not_fresh_trusted
- US MSFT FILLED action=verify_position_or_close_event_before_marking_resolved apply=False block=broker_truth_not_fresh_trusted
- US HOOD FILLED action=verify_position_or_close_event_before_marking_resolved apply=False block=broker_truth_not_fresh_trusted
- US RBRK FILLED action=verify_position_or_close_event_before_marking_resolved apply=False block=broker_truth_not_fresh_trusted
- US AVGO FILLED action=verify_position_or_close_event_before_marking_resolved apply=False block=broker_truth_not_fresh_trusted
- US EL FILLED action=verify_position_or_close_event_before_marking_resolved apply=False block=broker_truth_not_fresh_trusted
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

- db.pathb_stale_active_runs: risk=P1 blocking=True action=PathB stale active run을 broker truth로 대조하고 필요 시 manual reconciliation 처리 tool=PathB ORDER_UNKNOWN/reconcile tools
- db.pathb_lifecycle_window_consistency: risk=P2 blocking=True action=로컬 DB 상태와 최근 lifecycle/order_unknown row를 확인 tool=sqlite/manual DB inspection
- db.pathb_lifecycle_full_consistency: risk=P2 blocking=True action=로컬 DB 상태와 최근 lifecycle/order_unknown row를 확인 tool=sqlite/manual DB inspection
- kis.balance_probe: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
- runtime.bot_pid_lock: risk=P2 blocking=True action=guardian finding 세부 로그 확인 tool=tools/live_guardian.py
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
- [{"classification": "hard_fail", "detail": "previous-session active Path B rows=6", "kind": "finding", "name": "db.pathb_stale_active_runs", "status": "WARN"}, {"classification": "hard_fail", "detail": "KR snapshot stale", "kind": "finding", "name": "broker_truth.kr_stale_state", "status": "WARN"}, {"classification": "hard_fail", "detail": "US snapshot stale", "kind": "finding", "name": "broker_truth.us_stale_state", "status": "WARN"}]: risk=P1 blocking=True action=PathB stale active run을 broker truth로 대조하고 필요 시 manual reconciliation 처리 tool=PathB ORDER_UNKNOWN/reconcile tools

## Issues

- broker_truth_untrusted: 1
- guardian_block_start: 1

## Recent Issue Samples

- 2026-06-01T20:47:42+09:00 [broker_truth_untrusted] US broker truth missing=False stale=True error=
- 2026-06-01T20:47:42+09:00 [guardian_block_start] live guardian gate=BLOCK_START
