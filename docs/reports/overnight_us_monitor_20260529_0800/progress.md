# US Overnight Monitor Report

- status: running
- generated_at: 2026-05-29T08:00:00+09:00
- monitor_window: 2026-05-29T00:35:49+09:00 ~ 2026-05-29T08:00:00+09:00
- mode/market/session: live / US / 2026-05-28
- read_only: True

## Current Operations

- guardian_gate: None ok=None status=running
- broker_truth: missing=False stale=True error= last_success=2026-05-28T20:00:40+00:00
- broker_positions/open_orders/fills: 5 / 0 / 14
- open_positions_count: 5
- protected_positions: 2
- pending_sells: 0

## Trading Events Since Monitor Start

- 2026-05-29T00:50:19+09:00 entry RGTI qty=11 order=0031339851 exit=None pnl=None
- 2026-05-29T02:58:25+09:00 entry NBIS qty=1 order=0031381366 exit=None pnl=None

## Claude Usage

- api_usage_delta_since_start: calls=196 input=380634 output=89807 cost_usd=2.489007
- raw_call_files_observed: 196
- by_label: {'hold_advisor_bull': 60, 'hold_advisor_bear': 60, 'hold_advisor_neutral': 60, 'select_tickers': 7, 'tune_30min': 1, 'tune_60min': 1, 'tune_90min': 1, 'tune_120min': 1, 'tune_150min': 1, 'tune_180min': 1, 'tune_210min': 1, 'tune_240min': 1, 'postmortem': 1}
- by_model: {'claude-sonnet-4-6': 196}

## Issues

- protected_position: 444
- broker_truth_untrusted: 404
- guardian_block_start: 404
- broker_truth: 202
- log_warning: 180
- order_unknown: 56
- pending_sell_local_state: 33
- broker_sync_protected: 8

## Recent Issue Samples

- 2026-05-29T01:47:06+09:00 [protected_position] 2 protected US positions
- 2026-05-29T01:47:06+09:00 [guardian_block_start] live guardian gate=BLOCK_START
- 2026-05-29T01:48:06+09:00 [broker_truth_untrusted] US broker truth missing=False stale=True error=
- 2026-05-29T01:48:06+09:00 [protected_position] 2 protected US positions
- 2026-05-29T01:49:06+09:00 [log_warning] [universe filter bypass] candidates=67 filtered=25 min_keep=12 min_ratio=0.50
- 2026-05-29T01:49:06+09:00 [log_warning] 2026-05-29 01:48:59 [WARNING ] _restrict_candidates_to_universe:10316 | [universe filter bypass] candidates=67 filtered=25 min_keep=12 min_ratio=0.50
- 2026-05-29T01:49:06+09:00 [broker_truth_untrusted] US broker truth missing=False stale=True error=
- 2026-05-29T01:49:06+09:00 [protected_position] 2 protected US positions
- 2026-05-29T01:49:06+09:00 [guardian_block_start] live guardian gate=BLOCK_START
- 2026-05-29T01:50:06+09:00 [broker_truth] [PathB FILLED reconcile] {'market': 'US', 'checked': 4, 'kept_open': 4, 'kept_open_local': 0, 'closed': 0, 'order_unknown': 0, 'broker_truth_unavailable': 0, 'errors': []}
- 2026-05-29T01:50:06+09:00 [broker_truth] 2026-05-29 01:50:06 [INFO    ] reconcile_filled_positions:5600 | [PathB FILLED reconcile] {'market': 'US', 'checked': 4, 'kept_open': 4, 'kept_open_local': 0, 'closed': 0, 'order_unknown': 0, 'broker_truth_unavailable': 0, 'errors': []}
- 2026-05-29T01:50:06+09:00 [protected_position] 2 protected US positions
- 2026-05-29T01:50:06+09:00 [guardian_block_start] live guardian gate=BLOCK_START
- 2026-05-29T01:51:06+09:00 [log_warning] [risk event] BROKER_UNTRUSTED US HOOD reason=BROKER_UNTRUSTED
- 2026-05-29T01:51:06+09:00 [log_warning] [risk event] BROKER_UNTRUSTED US NBIS reason=BROKER_UNTRUSTED
- 2026-05-29T01:51:06+09:00 [log_warning] 2026-05-29 01:50:10 [WARNING ] _split_log:297 | [risk event] BROKER_UNTRUSTED US HOOD reason=BROKER_UNTRUSTED
- 2026-05-29T01:51:06+09:00 [log_warning] 2026-05-29 01:50:10 [WARNING ] _split_log:297 | [risk event] BROKER_UNTRUSTED US NBIS reason=BROKER_UNTRUSTED
- 2026-05-29T01:51:06+09:00 [log_warning] [risk event] BROKER_UNTRUSTED US HOOD reason=BROKER_UNTRUSTED
- 2026-05-29T01:51:06+09:00 [log_warning] [risk event] BROKER_UNTRUSTED US NBIS reason=BROKER_UNTRUSTED
- 2026-05-29T01:51:06+09:00 [log_warning] 2026-05-29 01:50:10 [WARNING ] _split_log:296 | [risk event] BROKER_UNTRUSTED US HOOD reason=BROKER_UNTRUSTED
- 2026-05-29T01:51:06+09:00 [log_warning] 2026-05-29 01:50:10 [WARNING ] _split_log:296 | [risk event] BROKER_UNTRUSTED US NBIS reason=BROKER_UNTRUSTED
- 2026-05-29T01:51:06+09:00 [broker_truth_untrusted] US broker truth missing=False stale=True error=
- 2026-05-29T01:51:06+09:00 [protected_position] 2 protected US positions
- 2026-05-29T01:51:06+09:00 [guardian_block_start] live guardian gate=BLOCK_START
- 2026-05-29T01:52:07+09:00 [broker_truth_untrusted] US broker truth missing=False stale=True error=
- 2026-05-29T01:52:07+09:00 [protected_position] 2 protected US positions
- 2026-05-29T01:52:07+09:00 [guardian_block_start] live guardian gate=BLOCK_START
- 2026-05-29T01:53:07+09:00 [broker_truth_untrusted] US broker truth missing=False stale=True error=
- 2026-05-29T01:53:07+09:00 [protected_position] 2 protected US positions
- 2026-05-29T01:53:07+09:00 [guardian_block_start] live guardian gate=BLOCK_START
