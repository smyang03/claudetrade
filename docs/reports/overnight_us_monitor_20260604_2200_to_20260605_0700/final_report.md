# US Overnight Monitor Report

- status: completed
- generated_at: 2026-06-05T07:00:02+09:00
- monitor_window: 2026-06-04T22:00:00+09:00 ~ 2026-06-05T07:00:00+09:00
- mode/market/session: live / US / 2026-06-04
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
- broker_truth: missing=False stale=True error= last_success=2026-06-04T20:13:02+00:00
- broker_positions/open_orders/fills: 0 / 0 / 8
- open_positions_count: 0
- protected_positions: 0
- pending_sells: 0

## Data Collection

- expected_open/news_due: 2026-06-04T22:30:00+09:00 / 2026-06-04T22:10:00+09:00
- minute_price_latest: data\price\minute\us\us_VRT.csv age_min=6.55 files=226
- daily_price_latest: data\price\us\us_YSS.csv age_min=28.85 files=1089
- preopen_candidates: exists=True lines=660 age_min=539.75
- preopen_scheduler: exists=True lines=1460 age_min=420.64
- screener_projected_volume: exists=True lines=34 age_min=420.33
- preopen_news: exists=True corp_news_total=378 coverage=0.85 age_min=519.51
- regular_news: exists=True corp_news_total=135 coverage=1.0 age_min=499.84
- daily_digest: exists=True top_news=5 age_min=518.6

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

- 2026-06-04T22:22:22+09:00 closed NVDA qty=1 order=0031612367 exit=hard_stop pnl=-0.30462089049301877
- 2026-06-04T22:22:23+09:00 closed GOOGL qty=1 order=0031616402 exit=hard_stop pnl=-0.43380079739128363
- 2026-06-04T22:22:24+09:00 closed HPE qty=5 order=0031616690 exit=claude_price_stop pnl=-4.006728061828722
- 2026-06-04T22:26:13+09:00 closed MSFT qty=1 order=0031644893 exit=hard_stop pnl=1.2903056217581503
- 2026-06-04T23:36:37+09:00 entry ARM qty=1 order=0031735655 exit=None pnl=None
- 2026-06-05T00:27:05+09:00 entry RKLB qty=2 order=0031772650 exit=None pnl=None
- 2026-06-05T03:05:32+09:00 closed ARM qty=1 order=0031819552 exit=target pnl=3.9482039611752424
- 2026-06-05T03:54:51+09:00 closed RKLB qty=2 order=0031828352 exit=profit_ladder pnl=2.182101081812042

## Claude Usage

- api_usage_delta_since_start: calls=63 input=267628 output=42200 cost_usd=0.0
- raw_call_files_observed: 63
- by_label: {'hold_advisor_triage': 21, 'select_tickers': 13, 'hold_advisor_challenge': 2, 'tune_30min': 1, 'param_tuner': 1, 'analyst_bear_r1': 1, 'analyst_bear_r2': 1, 'analyst_bull_r1': 1, 'analyst_bull_r2': 1, 'analyst_neutral_r1': 1, 'analyst_neutral_r2': 1, 'tune_60min': 1, 'tune_90min': 1, 'tune_120min': 1, 'preopen_continuation_blind_eval_retry': 1, 'preopen_continuation_blind_eval_30m': 1, 'preopen_continuation_blind_eval_5m': 1, 'preopen_continuation_blind_eval_preopen': 1, 'preopen_continuation_blind_eval_5m_small': 1, 'tune_150min': 1, 'tune_180min': 1, 'tune_210min': 1, 'tune_240min': 1, 'tune_270min': 1, 'tune_300min': 1, 'tune_330min': 1, 'tune_360min': 1, 'tune_390min': 1, 'tune_420min': 1, 'postmortem': 1}
- by_model: {'claude-sonnet-4-6': 63}
- hold_advisor_calls: total=23 by_label={'hold_advisor_triage': 21, 'hold_advisor_challenge': 2} saved_calls_estimate=0
- hard_guard_review_bypass: total=0 events={} latest={}

## State Observations

- no state observations recorded after monitor start

## Guardian Block Causes

- broker_truth.us_stale_state: risk=P1 blocking=True action=broker truth snapshot freshness와 토큰/조회 오류를 먼저 복구 tool=tools/live_preflight.py --mode live --skip-dashboard --json

## Issues

- log_warning: 386
- broker_truth_untrusted: 155
- guardian_block_start: 153
- pending_sell_local_state: 8
- data_collection_preopen_news_missing: 5
- data_collection_minute_price_stale: 4
- log_error: 4
- order_unknown: 2
- traceback: 2
- broker_sync_protected: 2

## Recent Issue Samples

- 2026-06-04T22:37:44+09:00 [log_warning] [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:37:44+09:00 [log_warning] [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:37:44+09:00 [log_warning] [NEW_BUY_BLOCKED] US * sector_play STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:37:44+09:00 [log_warning] [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:37:44+09:00 [log_warning] [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:37:44+09:00 [log_warning] 2026-06-04 22:36:50 [WARNING ] _log_entry_scan_blocked:670 | [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:37:44+09:00 [log_warning] 2026-06-04 22:37:00 [WARNING ] _log_entry_scan_blocked:670 | [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:37:44+09:00 [log_warning] 2026-06-04 22:37:10 [WARNING ] _log_entry_scan_blocked:670 | [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:37:44+09:00 [log_warning] 2026-06-04 22:37:20 [WARNING ] _log_entry_scan_blocked:670 | [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:37:44+09:00 [log_warning] 2026-06-04 22:37:23 [WARNING ] _record_new_buy_block:9119 | [NEW_BUY_BLOCKED] US * sector_play STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:37:44+09:00 [log_warning] 2026-06-04 22:37:30 [WARNING ] _log_entry_scan_blocked:670 | [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:37:44+09:00 [log_warning] 2026-06-04 22:37:40 [WARNING ] _log_entry_scan_blocked:670 | [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:38:47+09:00 [log_warning] [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:38:47+09:00 [log_warning] [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:38:47+09:00 [log_warning] [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:38:47+09:00 [log_warning] [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:38:47+09:00 [log_warning] [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:38:47+09:00 [log_warning] [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:38:47+09:00 [log_warning] 2026-06-04 22:37:50 [WARNING ] _log_entry_scan_blocked:670 | [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:38:47+09:00 [log_warning] 2026-06-04 22:38:00 [WARNING ] _log_entry_scan_blocked:670 | [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:38:47+09:00 [log_warning] 2026-06-04 22:38:10 [WARNING ] _log_entry_scan_blocked:670 | [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:38:47+09:00 [log_warning] 2026-06-04 22:38:20 [WARNING ] _log_entry_scan_blocked:670 | [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:38:47+09:00 [log_warning] 2026-06-04 22:38:31 [WARNING ] _log_entry_scan_blocked:670 | [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:38:47+09:00 [log_warning] 2026-06-04 22:38:40 [WARNING ] _log_entry_scan_blocked:670 | [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:39:50+09:00 [log_warning] [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:39:50+09:00 [log_warning] [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:39:50+09:00 [log_warning] [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:39:50+09:00 [log_warning] [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
- 2026-06-04T22:39:50+09:00 [log_warning] [stop cluster reset] US count 4->0 by=dashboard keep_stopped=True stopped=['GOOGL', 'HPE', 'MSFT', 'NVDA']
- 2026-06-04T22:39:50+09:00 [log_warning] 2026-06-04 22:38:50 [WARNING ] _log_entry_scan_blocked:670 | [PathB entry scan blocked] US STOP_CLUSTER_MARKET_BLOCK scope=market
