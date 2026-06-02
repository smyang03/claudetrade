# US Overnight Monitor Report

- status: completed
- generated_at: 2026-06-02T07:00:02+09:00
- monitor_window: 2026-06-01T22:30:00+09:00 ~ 2026-06-02T07:00:00+09:00
- mode/market/session: live / US / 2026-06-01
- read_only: True

## Current Operations

- guardian_gate: BLOCK_START ok=False status=blocked
- broker_truth: missing=False stale=True error= last_success=2026-06-01T20:00:03+00:00
- broker_positions/open_orders/fills: 3 / 0 / 13
- open_positions_count: 4
- protected_positions: 0
- pending_sells: 1

## Data Collection

- expected_open/news_due: 2026-06-01T22:30:00+09:00 / 2026-06-01T22:10:00+09:00
- minute_price_latest: data\price\minute\us\us_ZS.csv age_min=7.13 files=149
- daily_price_latest: data\price\us\us_YSS.csv age_min=28.95 files=1057
- preopen_candidates: exists=True lines=900 age_min=539.77
- preopen_scheduler: exists=True lines=1626 age_min=420.01
- screener_projected_volume: exists=True lines=29 age_min=433.52
- preopen_news: exists=True corp_news_total=440 coverage=0.8667 age_min=520.22
- regular_news: exists=True corp_news_total=126 coverage=1.0 age_min=498.83
- daily_digest: exists=True top_news=5 age_min=517.1

## Risk Axes

- broker_exposure: positions=3 open_local_positions=4
- open_orders: broker=0 pending_sell_local=1
- local_unresolved_state: protected=0 order_unknown_events=19
- manual_action_required: 2

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
- 2026-06-01T22:32:00+09:00 closed RBRK qty=2 order=0030247850 exit=target pnl=7.5915530314177975
- 2026-06-01T22:44:04+09:00 closed AVGO qty=1 order=0030281818 exit=target pnl=3.9091726217379117
- 2026-06-01T23:15:54+09:00 entry HPE qty=3 order=0030339305 exit=None pnl=None
- 2026-06-01T23:31:58+09:00 closed EL qty=1 order=0030326720 exit=intraday_review_sell pnl=0.782146924829174
- 2026-06-01T23:47:23+09:00 entry DELL qty=1 order=0030378547 exit=None pnl=None
- 2026-06-02T00:17:11+09:00 entry ARM qty=1 order=0030361202 exit=None pnl=None
- 2026-06-02T00:28:22+09:00 HOLD_REVIEW ARM qty=None order=None exit=None pnl=None
- 2026-06-02T00:50:34+09:00 HOLD_REVIEW DELL qty=None order=None exit=None pnl=None
- 2026-06-02T00:50:55+09:00 HOLD_REVIEW HPE qty=None order=None exit=None pnl=None
- 2026-06-02T00:59:08+09:00 entry CRWV qty=2 order=0030442530 exit=None pnl=None
- 2026-06-02T01:01:08+09:00 closed ARM qty=1 order=0030443791 exit=target pnl=3.9853984581461193
- 2026-06-02T01:14:42+09:00 closed DELL qty=1 order=0030450379 exit=loss_cap pnl=-2.1721692909809134
- 2026-06-02T01:24:28+09:00 entry NOK qty=18 order=0030455228 exit=None pnl=None
- 2026-06-02T01:33:30+09:00 closed BBY qty=2 order=0030328268 exit=intraday_review_sell pnl=6.100542604508424
- 2026-06-02T02:00:19+09:00 entry IBM qty=1 order=0030467823 exit=None pnl=None
- 2026-06-02T02:00:21+09:00 entry MRVL qty=1 order=0030467836 exit=None pnl=None

## Claude Usage

- api_usage_delta_since_start: calls=-51 input=-397694 output=-18918 cost_usd=-1.476852
- raw_call_files_observed: 113
- by_label: {'hold_advisor_triage': 42, 'select_tickers': 16, 'analyst_bear_r1': 5, 'analyst_bear_r2': 5, 'analyst_bull_r1': 5, 'analyst_bull_r2': 5, 'analyst_neutral_r1': 5, 'analyst_neutral_r2': 5, 'tune_30min': 4, 'hold_advisor_challenge': 4, 'hold_advisor_bear': 3, 'hold_advisor_bull': 3, 'hold_advisor_neutral': 3, 'tune_60min': 2, 'param_tuner': 2, 'tune_90min': 1, 'tune_120min': 1, 'tune_150min': 1, 'postmortem': 1}
- by_model: {'claude-sonnet-4-6': 113}
- hold_advisor_calls: total=55 by_label={'hold_advisor_triage': 42, 'hold_advisor_challenge': 4, 'hold_advisor_bear': 3, 'hold_advisor_bull': 3, 'hold_advisor_neutral': 3} saved_calls_estimate=0

## State Observations

- protected_position: 66

## Guardian Block Causes

- db.order_unknown_unresolved: risk=P2 blocking=True action=로컬 DB 상태와 최근 lifecycle/order_unknown row를 확인 tool=sqlite/manual DB inspection
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
- [{"classification": "hard_fail", "detail": "unresolved ORDER_UNKNOWN rows=3", "kind": "finding", "name": "db.order_unknown_unresolved", "status": "WARN"}, {"classification": "hard_fail", "detail": "previous-session active Path B rows=6", "kind": "finding", "name": "db.pathb_stale_active_runs", "status": "WARN"}, {"classification": "hard_fail", "detail": "KR snapshot stale", "kind": "finding", "name": "broker_truth.kr_stale_state", "status": "WARN"}, {"classification": "hard_fail", "detail": "US snapshot stale", "kind": "finding", "name": "broker_truth.us_stale_state", "status": "WARN"}]: risk=P1 blocking=True action=PathB stale active run을 broker truth로 대조하고 필요 시 manual reconciliation 처리 tool=PathB ORDER_UNKNOWN/reconcile tools

## Issues

- log_warning: 1750
- order_unknown: 1245
- broker_truth: 990
- guardian_block_start: 389
- broker_truth_untrusted: 348
- pending_sell_local_state: 148
- telegram: 32
- log_error: 20
- broker_sync_protected: 14
- data_collection_minute_price_stale: 8
- traceback: 2

## Recent Issue Samples

- 2026-06-01T23:28:49+09:00 [log_warning] [pending sell reconcile] US BBY BROKER_TRUTH_UNAVAILABLE_KEEP_PENDING order=0030328268
- 2026-06-01T23:28:49+09:00 [log_warning] [risk event] HIGH_PRICE_BUDGET_BLOCK US ARM reason=HIGH_PRICE_BUDGET_BLOCK
- 2026-06-01T23:28:49+09:00 [broker_truth] [PathB FILLED reconcile] {'market': 'US', 'checked': 1, 'kept_open': 1, 'kept_open_local': 0, 'closed': 0, 'order_unknown': 0, 'broker_truth_unavailable': 0, 'errors': []}
- 2026-06-01T23:28:49+09:00 [log_warning] 2026-06-01 23:28:03 [WARNING ] _reconcile_pending_sell_confirmations:22951 | [pending sell reconcile] US EL BROKER_TRUTH_UNAVAILABLE_KEEP_PENDING order=0030326720
- 2026-06-01T23:28:49+09:00 [log_warning] 2026-06-01 23:28:03 [WARNING ] _reconcile_pending_sell_confirmations:22951 | [pending sell reconcile] US BBY BROKER_TRUTH_UNAVAILABLE_KEEP_PENDING order=0030328268
- 2026-06-01T23:28:49+09:00 [log_warning] 2026-06-01 23:28:05 [WARNING ] _split_log:296 | [risk event] HIGH_PRICE_BUDGET_BLOCK US ARM reason=HIGH_PRICE_BUDGET_BLOCK
- 2026-06-01T23:28:49+09:00 [broker_truth] 2026-06-01 23:28:05 [INFO    ] reconcile_filled_positions:5601 | [PathB FILLED reconcile] {'market': 'US', 'checked': 1, 'kept_open': 1, 'kept_open_local': 0, 'closed': 0, 'order_unknown': 0, 'broker_truth_unavailable': 0, 'errors': []}
- 2026-06-01T23:28:52+09:00 [broker_truth_untrusted] US broker truth missing=False stale=True error=
- 2026-06-01T23:28:52+09:00 [guardian_block_start] live guardian gate=BLOCK_START
- 2026-06-01T23:29:54+09:00 [broker_truth_untrusted] US broker truth missing=False stale=True error=
- 2026-06-01T23:29:54+09:00 [guardian_block_start] live guardian gate=BLOCK_START
- 2026-06-01T23:30:58+09:00 [broker_truth_untrusted] US broker truth missing=False stale=True error=
- 2026-06-01T23:30:58+09:00 [guardian_block_start] live guardian gate=BLOCK_START
- 2026-06-01T23:31:58+09:00 [log_warning] [pending sell broker sync protected] US EL order=0030326720 broker_position_absent
- 2026-06-01T23:31:58+09:00 [broker_sync_protected] [pending sell broker sync protected] US EL order=0030326720 broker_position_absent
- 2026-06-01T23:31:58+09:00 [log_warning] 2026-06-01 23:31:58 [WARNING ] _sync_runtime_with_broker:19076 | [pending sell broker sync protected] US EL order=0030326720 broker_position_absent
- 2026-06-01T23:31:58+09:00 [broker_sync_protected] 2026-06-01 23:31:58 [WARNING ] _sync_runtime_with_broker:19076 | [pending sell broker sync protected] US EL order=0030326720 broker_position_absent
- 2026-06-01T23:32:01+09:00 [guardian_block_start] live guardian gate=BLOCK_START
- 2026-06-01T23:33:02+09:00 [log_warning] [pending sell reconcile] US EL BROKER_POSITION_GONE_ASSUME_SOLD order=0030326720
- 2026-06-01T23:33:02+09:00 [log_warning] [pending sell reconcile] US BBY BROKER_OPEN_ORDER_FOUND_KEEP_PENDING order=0030328268
- 2026-06-01T23:33:02+09:00 [log_warning] [pending sell broker sync reconcile] US {'market': 'US', 'checked': 2, 'closed': 1, 'partial': 0, 'kept_pending': 1, 'cleared_stale': 0, 'broker_truth_unavailable': False, 'errors': [], 'audit_trail': [{'market': 'US', 'ticker': 'EL', 'order_no': '0030326720', 'requested_qty': 1, 'local_position_qty': 1, 'stage': 'pending_sell_reconcile', 'resolution': 'BROKER_POSITION_GONE_ASSUME_SOLD', 'broker_fill_confirmed': True, 'filled_qty': 0, 'remaining_qty': 1, 'broker_position_qty': 0, 'open_order_rem
- 2026-06-01T23:33:02+09:00 [broker_truth] [pending sell broker sync reconcile] US {'market': 'US', 'checked': 2, 'closed': 1, 'partial': 0, 'kept_pending': 1, 'cleared_stale': 0, 'broker_truth_unavailable': False, 'errors': [], 'audit_trail': [{'market': 'US', 'ticker': 'EL', 'order_no': '0030326720', 'requested_qty': 1, 'local_position_qty': 1, 'stage': 'pending_sell_reconcile', 'resolution': 'BROKER_POSITION_GONE_ASSUME_SOLD', 'broker_fill_confirmed': True, 'filled_qty': 0, 'remaining_qty': 1, 'broker_position_qty': 0, 'open_order_rem
- 2026-06-01T23:33:02+09:00 [broker_truth] [PathB FILLED reconcile] {'market': 'US', 'checked': 1, 'kept_open': 1, 'kept_open_local': 0, 'closed': 0, 'order_unknown': 0, 'broker_truth_unavailable': 0, 'errors': []}
- 2026-06-01T23:33:02+09:00 [log_warning] [PathB profit_review TRIGGERED] US HPE peak_pnl=+1.88% current=45.455 bridge=protective_stop_not_tighter_than_plan_stop
- 2026-06-01T23:33:02+09:00 [log_warning] 2026-06-01 23:31:59 [WARNING ] _reconcile_pending_sell_confirmations:23041 | [pending sell reconcile] US EL BROKER_POSITION_GONE_ASSUME_SOLD order=0030326720
- 2026-06-01T23:33:02+09:00 [log_warning] 2026-06-01 23:31:59 [WARNING ] _reconcile_pending_sell_confirmations:23016 | [pending sell reconcile] US BBY BROKER_OPEN_ORDER_FOUND_KEEP_PENDING order=0030328268
- 2026-06-01T23:33:02+09:00 [log_warning] 2026-06-01 23:32:00 [WARNING ] _sync_runtime_with_broker:19184 | [pending sell broker sync reconcile] US {'market': 'US', 'checked': 2, 'closed': 1, 'partial': 0, 'kept_pending': 1, 'cleared_stale': 0, 'broker_truth_unavailable': False, 'errors': [], 'audit_trail': [{'market': 'US', 'ticker': 'EL', 'order_no': '0030326720', 'requested_qty': 1, 'local_position_qty': 1, 'stage': 'pending_sell_reconcile', 'resolution': 'BROKER_POSITION_GONE_ASSUME_SOLD', 'broker_fill_confirmed': True, 'filled_qty':
- 2026-06-01T23:33:02+09:00 [broker_truth] 2026-06-01 23:32:00 [WARNING ] _sync_runtime_with_broker:19184 | [pending sell broker sync reconcile] US {'market': 'US', 'checked': 2, 'closed': 1, 'partial': 0, 'kept_pending': 1, 'cleared_stale': 0, 'broker_truth_unavailable': False, 'errors': [], 'audit_trail': [{'market': 'US', 'ticker': 'EL', 'order_no': '0030326720', 'requested_qty': 1, 'local_position_qty': 1, 'stage': 'pending_sell_reconcile', 'resolution': 'BROKER_POSITION_GONE_ASSUME_SOLD', 'broker_fill_confirmed': True, 'filled_qty':
- 2026-06-01T23:33:02+09:00 [broker_truth] 2026-06-01 23:32:00 [INFO    ] reconcile_filled_positions:5601 | [PathB FILLED reconcile] {'market': 'US', 'checked': 1, 'kept_open': 1, 'kept_open_local': 0, 'closed': 0, 'order_unknown': 0, 'broker_truth_unavailable': 0, 'errors': []}
- 2026-06-01T23:33:02+09:00 [log_warning] 2026-06-01 23:32:13 [WARNING ] _maybe_trigger_profit_protection_review:4436 | [PathB profit_review TRIGGERED] US HPE peak_pnl=+1.88% current=45.455 bridge=protective_stop_not_tighter_than_plan_stop
