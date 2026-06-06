# US Overnight Monitor Report

- status: running
- generated_at: 2026-06-07T03:14:45+09:00
- monitor_window: 2026-06-06T02:51:43+09:00 ~ 2026-06-07T09:00:00+09:00
- mode/market/session: live / US / 2026-06-05
- read_only: True

## Operator Summary

- status: action_required
- action_required: ['broker_truth_untrusted', 'guardian_block_start']
- attention: []
- normal: ['no_previous_session_cleanup_backlog']
- current_trading_risk: {'broker_open_orders': 0, 'pending_sells': 0, 'current_order_unknown': 0, 'manual_action_required': 0, 'guardian_action_required': 1, 'broker_truth_action_required': 1}
- previous_session_cleanup: {'previous_order_unknown': 0, 'stale_active': 0, 'apply_eligible_items': 0, 'historical_order_unknown_total': 19}

## Current Operations

- guardian_gate: BLOCK_START ok=False top_level_gate=BLOCK_START status=blocked
- broker_truth: missing=False stale=True error= last_success=2026-06-06T17:20:28+00:00
- broker_positions/open_orders/fills: 0 / 0 / 0
- open_positions_count: 0
- protected_positions: 0
- pending_sells: 0

## Data Collection

- expected_open/news_due: 2026-06-05T22:30:00+09:00 / 2026-06-05T22:10:00+09:00
- minute_price_latest: data\price\minute\us\us_WBD.csv age_min=862.88 files=249
- daily_price_latest: data\price\us\us_YSS.csv age_min=277.49 files=1111
- preopen_candidates: exists=True lines=660 age_min=1754.31
- preopen_scheduler: exists=True lines=1085 age_min=1635.41
- screener_projected_volume: exists=True lines=42 age_min=1638.45
- preopen_news: exists=True corp_news_total=411 coverage=0.8667 age_min=1734.29
- regular_news: exists=True corp_news_total=136 coverage=1.0 age_min=1715.6
- daily_digest: exists=True top_news=5 age_min=1718.87

## Risk Axes

- broker_exposure: positions=0 open_local_positions=0
- open_orders: broker=0 pending_sell_local=0
- current_unresolved_state: protected=0 current_order_unknown=0
- previous_cleanup_state: previous_order_unknown=0 stale_active=0 historical_order_unknown_total=19
- manual_action_required: 0
- guardian_action_required: 1 broker_truth_action_required=1

## PathB Remediation Separation

- available: True dry_run=True write_supported=False
- current_session_order_unknown: 0 rows=0
- previous_session_order_unknown: 0
- previous_session_stale_active: 0
- apply_eligible_items: 0

## Trading Events Since Monitor Start

- 2026-06-06T03:23:24+09:00 closed AMZN qty=1 order=0032252889 exit=loss_cap pnl=-1.787009468314844
- 2026-06-06T04:47:43+09:00 closed GOOGL qty=1 order=0032282825 exit=intraday_review_sell pnl=-0.9421182164917296

## Claude Usage

- api_usage_delta_since_start: calls=23 input=115093 output=15698 cost_usd=0.0
- raw_call_files_observed: 23
- by_label: {'select_tickers': 5, 'hold_advisor_triage': 4, 'hold_advisor_challenge': 2, 'param_tuner': 1, 'tune_30min': 1, 'analyst_bear_r1': 1, 'analyst_bear_r2': 1, 'analyst_bull_r1': 1, 'analyst_bull_r2': 1, 'analyst_neutral_r1': 1, 'analyst_neutral_r2': 1, 'tune_60min': 1, 'tune_90min': 1, 'tune_120min': 1, 'postmortem': 1}
- by_model: {'claude-sonnet-4-6': 23}
- hold_advisor_calls: total=6 by_label={'hold_advisor_triage': 4, 'hold_advisor_challenge': 2} saved_calls_estimate=0
- hard_guard_review_bypass: total=0 events={} latest={}

## State Observations

- no state observations recorded after monitor start

## Guardian Block Causes

- broker_truth.us_stale_state: risk=P1 blocking=True action=broker truth snapshot freshness와 토큰/조회 오류를 먼저 복구 tool=tools/live_preflight.py --mode live --skip-dashboard --json

## Issues

- log_warning: 274
- broker_truth_untrusted: 265
- analyst_new_buy_block: 240
- guardian_block_start: 237
- data_collection_minute_price_stale: 170
- telegram: 4
- broker_sync_protected: 2
- pending_sell_local_state: 1

## Recent Issue Samples

- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] 2026-06-06 03:01:55 [WARNING ] _log_entry_scan_blocked:672 | [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] 2026-06-06 03:01:55 [WARNING ] _log_entry_scan_blocked:672 | [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] 2026-06-06 03:02:05 [WARNING ] _log_entry_scan_blocked:672 | [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] 2026-06-06 03:02:05 [WARNING ] _log_entry_scan_blocked:672 | [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] 2026-06-06 03:02:15 [WARNING ] _log_entry_scan_blocked:672 | [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] 2026-06-06 03:02:15 [WARNING ] _log_entry_scan_blocked:672 | [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] 2026-06-06 03:02:25 [WARNING ] _log_entry_scan_blocked:672 | [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [analyst_new_buy_block] 2026-06-06 03:02:25 [WARNING ] _log_entry_scan_blocked:672 | [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
- 2026-06-06T03:06:54+09:00 [log_warning] 2026-06-06 03:02:35 [WARNING ] _log_entry_scan_blocked:672 | [PathB entry scan blocked] US ANALYST_NEW_BUY_BLOCK scope=market
