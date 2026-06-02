# US Claude Morning Review / 미국장 Claude 아침 리뷰

- generated_at: 2026-06-01T23:34:30+09:00
- window: 2026-06-01T22:30:00+09:00 ~ 2026-06-02T07:00:00+09:00
- scope: live / US / 2026-06-01
- source_reports: docs\reports\overnight_us_monitor_20260601_2230\interim_2246\final_report.md / docs\reports\overnight_us_monitor_20260601_2230\interim_2246\claude_io_quality.md

## 한국어 요약

- 운영 상태: monitor=running guardian=BLOCK_START ok=False
- 브로커 truth: missing=False stale=True error=
- Claude 사용량: api_calls=44 input_tokens=199941 output_tokens=22393 cost_usd=0.935718
- raw call 관측: files=44 input=199941 output=22393 duration_ms=129845
- Claude I/O 품질: calls=44 parse_errors=0 avg_input=4544.1 avg_output=508.9
- 지연시간 커버리지: observed=11 missing=33 avg_duration_ms=11804.1
- 사용량 일관성: calls_match=True input_match=True output_match=True
- 입력 이슈: {"prompt_input_tokens_ge_12000": 3, "prompt_input_tokens_ge_8000": 3}
- 출력 이슈: {"duplicate_candidate_action": 1, "duplicate_watchlist_ticker": 1, "response_fenced_json": 15, "response_has_preamble_or_wrapper": 15, "response_not_strict_json": 15}

## Operations

- monitor_status: running
- guardian_gate: BLOCK_START ok=False
- broker_truth: missing=False stale=True error=
- broker_positions/open_orders/fills: 3 / 1 / 7
- decision_events: 4
- log_issue_counts: {"broker_sync_protected": 6, "broker_truth": 30, "broker_truth_untrusted": 45, "data_collection_minute_price_stale": 2, "guardian_block_start": 56, "log_error": 12, "log_warning": 146, "order_unknown": 2, "pending_sell_local_state": 5, "telegram": 4}

## Claude Usage

- api_usage_delta: calls=44 input=199941 output=22393 cost_usd=0.935718
- raw_call_files: 44
- raw_tokens: input=199941 output=22393 duration_ms=129845
- raw_by_label: {"analyst_bear_r1": 4, "analyst_bear_r2": 4, "analyst_bull_r1": 4, "analyst_bull_r2": 4, "analyst_neutral_r1": 4, "analyst_neutral_r2": 4, "hold_advisor_challenge": 3, "hold_advisor_triage": 8, "param_tuner": 1, "select_tickers": 6, "tune_30min": 1, "tune_60min": 1}
- raw_by_model: {"claude-sonnet-4-6": 44}
- hold_advisor_calls: 11 by_label={"hold_advisor_challenge": 3, "hold_advisor_triage": 8}

## Claude I/O Quality

- quality_calls: 44
- quality_tokens: input=199941 output=22393
- parse_errors: 0
- averages: input=4544.1 output=508.9 duration_ms=11804.1
- duration_coverage: observed=11 missing=33
- input_issues: {"prompt_input_tokens_ge_12000": 3, "prompt_input_tokens_ge_8000": 3}
- output_issues: {"duplicate_candidate_action": 1, "duplicate_watchlist_ticker": 1, "response_fenced_json": 15, "response_has_preamble_or_wrapper": 15, "response_not_strict_json": 15}

## Claude I/O By Label

| label | calls | input | output | total | avg input | avg output | avg ms | ms observed | ms missing | input issues | output issues |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| select_tickers | 6 | 70896 | 9718 | 80614 | 11816.0 | 1619.7 | 0.0 | 0 | 6 | {"prompt_input_tokens_ge_12000": 3, "prompt_input_tokens_ge_8000": 3} | {"duplicate_candidate_action": 1, "duplicate_watchlist_ticker": 1, "response_fenced_json": 4, "response_has_preamble_or_wrapper": 4, "response_not_strict_json": 4} |
| analyst_bear_r1 | 4 | 27478 | 891 | 28369 | 6869.5 | 222.8 | 0.0 | 0 | 4 | {} | {} |
| analyst_bull_r1 | 4 | 27018 | 896 | 27914 | 6754.5 | 224.0 | 0.0 | 0 | 4 | {} | {} |
| analyst_neutral_r1 | 4 | 26986 | 731 | 27717 | 6746.5 | 182.8 | 0.0 | 0 | 4 | {} | {} |
| hold_advisor_triage | 8 | 9342 | 4402 | 13744 | 1167.8 | 550.2 | 11525.8 | 8 | 0 | {} | {"response_fenced_json": 8, "response_has_preamble_or_wrapper": 8, "response_not_strict_json": 8} |
| analyst_bear_r2 | 4 | 10190 | 954 | 11144 | 2547.5 | 238.5 | 0.0 | 0 | 4 | {} | {} |
| analyst_bull_r2 | 4 | 9730 | 1067 | 10797 | 2432.5 | 266.8 | 0.0 | 0 | 4 | {} | {} |
| analyst_neutral_r2 | 4 | 9698 | 948 | 10646 | 2424.5 | 237.0 | 0.0 | 0 | 4 | {} | {} |
| hold_advisor_challenge | 3 | 3801 | 1659 | 5460 | 1267.0 | 553.0 | 12546.3 | 3 | 0 | {} | {"response_fenced_json": 3, "response_has_preamble_or_wrapper": 3, "response_not_strict_json": 3} |
| param_tuner | 1 | 1461 | 677 | 2138 | 1461.0 | 677.0 | 0.0 | 0 | 1 | {} | {} |
| tune_30min | 1 | 1713 | 231 | 1944 | 1713.0 | 231.0 | 0.0 | 0 | 1 | {} | {} |
| tune_60min | 1 | 1628 | 219 | 1847 | 1628.0 | 219.0 | 0.0 | 0 | 1 | {} | {} |

## Consistency Checks

- calls: {"api": 44, "quality": 44, "raw": 44} match=True
- input_tokens: {"api": 199941, "quality": 199941, "raw": 199941} match=True
- output_tokens: {"api": 22393, "quality": 22393, "raw": 22393} match=True

## Evidence Samples / 근거 샘플

### Claude I/O Issue Samples
- 2026-06-01T22:41:18+09:00 hold_advisor_challenge input=[] output=["response_fenced_json", "response_has_preamble_or_wrapper", "response_not_strict_json"] path=logs\raw_calls\20260601_US_hold_advisor_challenge_224118358377_efbb17d08e.json
- 2026-06-01T23:07:01+09:00 hold_advisor_challenge input=[] output=["response_fenced_json", "response_has_preamble_or_wrapper", "response_not_strict_json"] path=logs\raw_calls\20260601_US_hold_advisor_challenge_230701217366_6d9b6116d3.json
- 2026-06-01T23:07:30+09:00 hold_advisor_challenge input=[] output=["response_fenced_json", "response_has_preamble_or_wrapper", "response_not_strict_json"] path=logs\raw_calls\20260601_US_hold_advisor_challenge_230730483021_ccf7488cf4.json
- 2026-06-01T22:32:15+09:00 hold_advisor_triage input=[] output=["response_fenced_json", "response_has_preamble_or_wrapper", "response_not_strict_json"] path=logs\raw_calls\20260601_US_hold_advisor_triage_223215504466_93c57c4bb8.json
- 2026-06-01T22:41:07+09:00 hold_advisor_triage input=[] output=["response_fenced_json", "response_has_preamble_or_wrapper", "response_not_strict_json"] path=logs\raw_calls\20260601_US_hold_advisor_triage_224107658782_feec3ac1e6.json
- 2026-06-01T23:06:10+09:00 hold_advisor_triage input=[] output=["response_fenced_json", "response_has_preamble_or_wrapper", "response_not_strict_json"] path=logs\raw_calls\20260601_US_hold_advisor_triage_230610475882_249cccc373.json
- 2026-06-01T23:06:23+09:00 hold_advisor_triage input=[] output=["response_fenced_json", "response_has_preamble_or_wrapper", "response_not_strict_json"] path=logs\raw_calls\20260601_US_hold_advisor_triage_230623132482_d72b1a5c0a.json
- 2026-06-01T23:06:48+09:00 hold_advisor_triage input=[] output=["response_fenced_json", "response_has_preamble_or_wrapper", "response_not_strict_json"] path=logs\raw_calls\20260601_US_hold_advisor_triage_230648721973_43f664accd.json
- 2026-06-01T23:07:15+09:00 hold_advisor_triage input=[] output=["response_fenced_json", "response_has_preamble_or_wrapper", "response_not_strict_json"] path=logs\raw_calls\20260601_US_hold_advisor_triage_230715984097_1eb2dca8ff.json
- 2026-06-01T23:07:55+09:00 hold_advisor_triage input=[] output=["response_fenced_json", "response_has_preamble_or_wrapper", "response_not_strict_json"] path=logs\raw_calls\20260601_US_hold_advisor_triage_230755549047_fd95a678b4.json
- 2026-06-01T23:32:13+09:00 hold_advisor_triage input=[] output=["response_fenced_json", "response_has_preamble_or_wrapper", "response_not_strict_json"] path=logs\raw_calls\20260601_US_hold_advisor_triage_233213909730_56b1087512.json
- 2026-06-01T22:35:49+09:00 select_tickers input=["prompt_input_tokens_ge_8000"] output=["response_fenced_json", "response_has_preamble_or_wrapper", "response_not_strict_json"] path=logs\raw_calls\20260601_US_select_tickers_223549265559_93a82c8add.json

### Operational Issue Samples
- 2026-06-01T23:33:02+09:00 log_warning WARNING: [pending sell reconcile] US EL BROKER_POSITION_GONE_ASSUME_SOLD order=0030326720 path=logs\system\live_trading_20260601.jsonl
- 2026-06-01T23:33:02+09:00 log_warning WARNING: [pending sell reconcile] US BBY BROKER_OPEN_ORDER_FOUND_KEEP_PENDING order=0030328268 path=logs\system\live_trading_20260601.jsonl
- 2026-06-01T23:33:02+09:00 log_warning WARNING: [pending sell broker sync reconcile] US {'market': 'US', 'checked': 2, 'closed': 1, 'partial': 0, 'kept_pending': 1, 'cleared_stale': 0, 'broker_truth_unavailable': False, 'errors': [], 'audit_trail': [{'market': 'US', 'ticker': 'EL', 'order_no': '0030326720', 'requested_qty': 1, 'local_position_qty': 1, 'stage': 'pending_sell_reconcile', 'resolution': 'BROKER_POSITION_GONE_ASSUME_SOLD', 'broker_fill_confirmed': True, 'filled_qty': 0, 'remaining_qty': 1, 'broker_position_qty': 0, 'open_order_rem path=logs\system\live_trading_20260601.jsonl
- 2026-06-01T23:33:02+09:00 broker_truth WARNING: [pending sell broker sync reconcile] US {'market': 'US', 'checked': 2, 'closed': 1, 'partial': 0, 'kept_pending': 1, 'cleared_stale': 0, 'broker_truth_unavailable': False, 'errors': [], 'audit_trail': [{'market': 'US', 'ticker': 'EL', 'order_no': '0030326720', 'requested_qty': 1, 'local_position_qty': 1, 'stage': 'pending_sell_reconcile', 'resolution': 'BROKER_POSITION_GONE_ASSUME_SOLD', 'broker_fill_confirmed': True, 'filled_qty': 0, 'remaining_qty': 1, 'broker_position_qty': 0, 'open_order_rem path=logs\system\live_trading_20260601.jsonl
- 2026-06-01T23:33:02+09:00 broker_truth INFO: [PathB FILLED reconcile] {'market': 'US', 'checked': 1, 'kept_open': 1, 'kept_open_local': 0, 'closed': 0, 'order_unknown': 0, 'broker_truth_unavailable': 0, 'errors': []} path=logs\system\live_trading_20260601.jsonl
- 2026-06-01T23:33:02+09:00 log_warning WARNING: [PathB profit_review TRIGGERED] US HPE peak_pnl=+1.88% current=45.455 bridge=protective_stop_not_tighter_than_plan_stop path=logs\system\live_trading_20260601.jsonl
- 2026-06-01T23:33:02+09:00 log_warning WARNING: 2026-06-01 23:31:59 [WARNING ] _reconcile_pending_sell_confirmations:23041 | [pending sell reconcile] US EL BROKER_POSITION_GONE_ASSUME_SOLD order=0030326720 path=logs\system\live_trading_20260601.log
- 2026-06-01T23:33:02+09:00 log_warning WARNING: 2026-06-01 23:31:59 [WARNING ] _reconcile_pending_sell_confirmations:23016 | [pending sell reconcile] US BBY BROKER_OPEN_ORDER_FOUND_KEEP_PENDING order=0030328268 path=logs\system\live_trading_20260601.log
- 2026-06-01T23:33:02+09:00 log_warning WARNING: 2026-06-01 23:32:00 [WARNING ] _sync_runtime_with_broker:19184 | [pending sell broker sync reconcile] US {'market': 'US', 'checked': 2, 'closed': 1, 'partial': 0, 'kept_pending': 1, 'cleared_stale': 0, 'broker_truth_unavailable': False, 'errors': [], 'audit_trail': [{'market': 'US', 'ticker': 'EL', 'order_no': '0030326720', 'requested_qty': 1, 'local_position_qty': 1, 'stage': 'pending_sell_reconcile', 'resolution': 'BROKER_POSITION_GONE_ASSUME_SOLD', 'broker_fill_confirmed': True, 'filled_qty': path=logs\system\live_trading_20260601.log
- 2026-06-01T23:33:02+09:00 broker_truth WARNING: 2026-06-01 23:32:00 [WARNING ] _sync_runtime_with_broker:19184 | [pending sell broker sync reconcile] US {'market': 'US', 'checked': 2, 'closed': 1, 'partial': 0, 'kept_pending': 1, 'cleared_stale': 0, 'broker_truth_unavailable': False, 'errors': [], 'audit_trail': [{'market': 'US', 'ticker': 'EL', 'order_no': '0030326720', 'requested_qty': 1, 'local_position_qty': 1, 'stage': 'pending_sell_reconcile', 'resolution': 'BROKER_POSITION_GONE_ASSUME_SOLD', 'broker_fill_confirmed': True, 'filled_qty': path=logs\system\live_trading_20260601.log
- 2026-06-01T23:33:02+09:00 broker_truth INFO: 2026-06-01 23:32:00 [INFO    ] reconcile_filled_positions:5601 | [PathB FILLED reconcile] {'market': 'US', 'checked': 1, 'kept_open': 1, 'kept_open_local': 0, 'closed': 0, 'order_unknown': 0, 'broker_truth_unavailable': 0, 'errors': []} path=logs\system\live_trading_20260601.log
- 2026-06-01T23:33:02+09:00 log_warning WARNING: 2026-06-01 23:32:13 [WARNING ] _maybe_trigger_profit_protection_review:4436 | [PathB profit_review TRIGGERED] US HPE peak_pnl=+1.88% current=45.455 bridge=protective_stop_not_tighter_than_plan_stop path=logs\system\live_trading_20260601.log

### Guardian Block Causes
- P2 config.runtime_snapshot_drift: runtime config snapshot predates current pid
- P1 db.pathb_stale_active_runs: previous-session active Path B rows=1
- P2 db.pathb_lifecycle_window_consistency: recent-window Path B lifecycle diagnostic warnings: recent_window_missing_events_count=1 recent_window_size_events=1000 recent_window_size_runs=500; PathB post-run lifecycle events missing payload_json.path_run_id=0
- P2 db.pathb_lifecycle_full_consistency: full terminal lifecycle missing events=1
- P2 kis.balance_probe: default preflight avoids direct balance APIs; broker-truth snapshot and live smoke cover read-only balance checks
- P2 runtime.bot_pid_lock: pid lock is active; expected process appears alive
- P2 runtime.dashboard_pid_lock: pid lock is active; expected process appears alive
- P2 state.brain_memory_change_guard: state/brain.json has uncommitted changes
- P1 broker_truth.kr_stale_state: KR snapshot stale
- P1 broker_truth.us_stale_state: US snapshot stale

## Improvement Actions / 개선 액션

- P1 output_contract: Tighten JSON-only enforcement for affected labels or route non-strict responses through a bounded retry; parser recovery should remain fail-safe.
- P2 selection_schema: Add a compact-schema self-check or post-parse warning for duplicate watchlist entries and candidate-action coverage gaps.
- P2 token_cost: Reduce high-token prompts by trimming repeated calibration blocks and limiting evidence pack rows before model invocation; review labels with 8k+ input tokens even when the session average looks acceptable.
- P1 operations: Resolve guardian BLOCK_START causes before treating the session as operationally clean; include any repeated block events observed during the window, not just the final snapshot.
- P1 broker_truth: Refresh broker truth and keep new entries fail-closed while snapshot freshness is untrusted; review repeated stale/untrusted snapshots from the full window.
- P1 reconciliation: Review manual-action-required local state before the next live start window.
- P1 runtime_errors: Inspect error samples and confirm they did not affect broker truth, order routing, or Claude fallback behavior.
- P2 order_state: Separate current-session unresolved ORDER_UNKNOWN from historical event noise in the morning review.
