# US Session Profitability Review

- generated_at: 2026-06-02T07:01:22+09:00
- session_date: 2026-06-01
- monitor_window: 2026-06-01T20:44:05+09:00 ~ 2026-06-02T07:00:00+09:00
- source_dir: E:\code\claudetrade\docs\reports\overnight_us_monitor_20260601_2042_to_20260602_0700
- requested_regular_window: 2026-06-01T22:30:00+09:00 ~ 2026-06-02T07:00:00+09:00
- monitor_source: final_report.json
- monitor_final_ready: True
- read_only: True

## Executive Summary

- decisions observed: entries=7, exits=9, hold_reviews=3
- broker truth: missing=False stale=True error= positions=3 open_orders=0 fills=13
- guardian: gate=BLOCK_START ok=False heartbeat_status=blocked
- unresolved state: protected=0 pending_sells=1 order_unknown_events=19 manual_action_required=2
- Claude calls observed by monitor: 122 labels={'analyst_bear_r1': 5, 'analyst_bear_r2': 5, 'analyst_bull_r1': 5, 'analyst_bull_r2': 5, 'analyst_neutral_r1': 5, 'analyst_neutral_r2': 5, 'hold_advisor_bear': 3, 'hold_advisor_bull': 3, 'hold_advisor_challenge': 7, 'hold_advisor_neutral': 3, 'hold_advisor_triage': 45, 'param_tuner': 2, 'postmortem': 2, 'select_tickers': 18, 'tune_120min': 1, 'tune_150min': 1, 'tune_30min': 4, 'tune_60min': 2, 'tune_90min': 1}

## Broker Performance Snapshot

| ticker | qty | pnl | mfe | mae | strategy | path |
| --- | --- | --- | --- | --- | --- | --- |
| HPQ | 11 | 8.02% | NA | NA |  |  |
| MRVL | 1 | 0.58% | NA | NA |  |  |
| NOK | 18 | 0.34% | NA | NA |  |  |

| time | ticker | side | qty | remaining | status | order |
| --- | --- | --- | --- | --- | --- | --- |
| 222122 | MSFT | sell | 1 | 0 | filled | 0030244707 |
| 222147 | RBRK | sell | 2 | 0 | filled | 0030247850 |
| 222147 | HOOD | sell | 3 | 0 | filled | 0030247854 |
| 222430 | SOFI | sell | 16 | 0 | filled | 0030249777 |
| 224118 | AVGO | sell | 1 | 0 | filled | 0030281818 |
| 231542 | HPE | buy | 3 | 0 | filled | 0030339305 |
| 233019 | EL | sell | 1 | 0 | filled | 0030357390 |
| 234710 | DELL | buy | 1 | 0 | filled | 0030378547 |
| 001622 | ARM | buy | 1 | 0 | filled | 0030409061 |
| 005907 | CRWV | buy | 2 | 0 | filled | 0030442530 |
| 010108 | ARM | sell | 1 | 0 | filled | 0030443791 |
| 011319 | DELL | sell | 1 | 0 | filled | 0030450379 |
| 012418 | NOK | buy | 18 | 0 | filled | 0030455228 |

| time | type | ticker | qty | order | exit | pnl |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-06-01T22:23:53+09:00 | closed | MSFT | 1 | 0030244707 | target | 9.64% |
| 2026-06-01T22:23:54+09:00 | closed | HOOD | 3 | 0030247854 | hard_stop | 3.94% |
| 2026-06-01T22:25:01+09:00 | closed | SOFI | 16 | 0030249777 | profit_ladder | 0.45% |
| 2026-06-01T22:32:00+09:00 | closed | RBRK | 2 | 0030247850 | target | 7.59% |
| 2026-06-01T22:44:04+09:00 | closed | AVGO | 1 | 0030281818 | target | 3.91% |
| 2026-06-01T23:15:54+09:00 | entry | HPE | 3 | 0030339305 |  | NA |
| 2026-06-01T23:31:58+09:00 | closed | EL | 1 | 0030326720 | intraday_review_sell | 0.78% |
| 2026-06-01T23:47:23+09:00 | entry | DELL | 1 | 0030378547 |  | NA |
| 2026-06-02T00:17:11+09:00 | entry | ARM | 1 | 0030361202 |  | NA |
| 2026-06-02T00:28:22+09:00 | HOLD_REVIEW | ARM |  |  |  | NA |
| 2026-06-02T00:50:34+09:00 | HOLD_REVIEW | DELL |  |  |  | NA |
| 2026-06-02T00:50:55+09:00 | HOLD_REVIEW | HPE |  |  |  | NA |
| 2026-06-02T00:59:08+09:00 | entry | CRWV | 2 | 0030442530 |  | NA |
| 2026-06-02T01:01:08+09:00 | closed | ARM | 1 | 0030443791 | target | 3.99% |
| 2026-06-02T01:14:42+09:00 | closed | DELL | 1 | 0030450379 | loss_cap | -2.17% |
| 2026-06-02T01:24:28+09:00 | entry | NOK | 18 | 0030455228 |  | NA |
| 2026-06-02T01:33:30+09:00 | closed | BBY | 2 | 0030328268 | intraday_review_sell | 6.10% |
| 2026-06-02T02:00:19+09:00 | entry | IBM | 1 | 0030467823 |  | NA |
| 2026-06-02T02:00:21+09:00 | entry | MRVL | 1 | 0030467836 |  | NA |

## Buy Non-Execution Causality

- 7 buy/entry events were observed; evaluate ticker-level fills and route reasons rather than treating the session as no-buy.
- Watch-trigger shadow blocks were observed: {'SAME_DAY_REENTRY_AFTER_STOP': 12, 'SAME_DAY_REENTRY_COOLDOWN': 3, 'already_holding': 81, 'entry_blackout': 3, 'entry_blocked': 4, 'same_day_reentry_blocked': 15, 'strategy_unavailable': 3}.

## Sell Non-Execution Causality

- 9 sell/closed events were observed; inspect exit reasons and broker fill confirmation per ticker.
- 1 local pending-sell rows require broker reconciliation.
- Stale or untrusted broker truth also weakens sell forensic certainty; use broker positions/open orders/fills as the final truth.
- Guardian BLOCK_START was present; separate runtime safety blocks from sell-advisor quality.

## Buy Path Review

- candidate rows: 162 latest_checked=162
- prompt/watchlist pool: full=15 prompt=15 watchlist=15
- raw trade_ready=3 normalized=3 applied=3 execution_pool=3
- dropped_after_raw=[]
- runtime_filtered_count=0 reasons={}
- PathB wait tickers=['NOK', 'HPE', 'CRWV', 'MRVL']
- missed winners found=0 at 60m horizon

- no high-confidence missed-winner rows were mature enough in the 60m outcome table.

## Watch And Block Reasons

- SAME_DAY_REENTRY_AFTER_STOP: 12
- SAME_DAY_REENTRY_COOLDOWN: 3
- already_holding: 81
- entry_blackout: 3
- entry_blocked: 4
- same_day_reentry_blocked: 15
- strategy_unavailable: 3

## Watch Bucket Decomposition

- not_in_prompt: 99
- claude_not_selected: 36
- claude_watch_conservative: 9
- data_insufficient: 4
- pathb_zone_or_plan: 3

## Sell Path Review

- exits observed during monitor window: 9
- pending sell local rows: 1
- protected positions: 0
- hold advisor latency/status: {'decision_requests': {'by_market': [{'avg_ms': 17703.167, 'calls': 12, 'duration_count': 12, 'input_tokens': 0, 'market': 'US', 'max_ms': 26125.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 16884.0, 'p95_ms': 26061.75}], 'by_market_stage_decision': [{'avg_ms': 21702.5, 'calls': 6, 'decision': 'SELL', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_count': 6, 'input_tokens': 0, 'market': 'US', 'max_ms': 26125.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 23782.0, 'p95_ms': 26096.25}, {'avg_ms': 11528.5, 'calls': 4, 'decision': 'HOLD', 'decision_stage': 'INTRADAY_REVIEW', 'duration_count': 4, 'input_tokens': 0, 'market': 'US', 'max_ms': 12610.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 11513.5, 'p95_ms': 12494.95}, {'avg_ms': 18054.5, 'calls': 2, 'decision': 'SELL', 'decision_stage': 'INTRADAY_REVIEW', 'duration_count': 2, 'input_tokens': 0, 'market': 'US', 'max_ms': 25720.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 18054.5, 'p95_ms': 24953.45}], 'by_stage': [{'avg_ms': 21702.5, 'calls': 6, 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_count': 6, 'input_tokens': 0, 'max_ms': 26125.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 23782.0, 'p95_ms': 26096.25}, {'avg_ms': 13703.833, 'calls': 6, 'decision_stage': 'INTRADAY_REVIEW', 'duration_count': 6, 'input_tokens': 0, 'max_ms': 25720.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 11513.5, 'p95_ms': 22442.5}], 'by_symbol': [{'avg_ms': 17674.5, 'calls': 2, 'duration_count': 2, 'input_tokens': 0, 'market': 'US', 'max_ms': 24165.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 17674.5, 'p95_ms': 23515.95, 'ticker': 'AVGO'}, {'avg_ms': 9873.5, 'calls': 2, 'duration_count': 2, 'input_tokens': 0, 'market': 'US', 'max_ms': 10389.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 9873.5, 'p95_ms': 10337.45, 'ticker': 'EL'}, {'avg_ms': 25922.5, 'calls': 2, 'duration_count': 2, 'input_tokens': 0, 'market': 'US', 'max_ms': 26125.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 25922.5, 'p95_ms': 26104.75, 'ticker': 'BBY'}, {'avg_ms': 11160.0, 'calls': 2, 'duration_count': 2, 'input_tokens': 0, 'market': 'US', 'max_ms': 11843.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 11160.0, 'p95_ms': 11774.7, 'ticker': 'HPE'}, {'avg_ms': 26010.0, 'calls': 1, 'duration_count': 1, 'input_tokens': 0, 'market': 'US', 'max_ms': 26010.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 26010.0, 'p95_ms': 26010.0, 'ticker': 'MSFT'}, {'avg_ms': 21158.0, 'calls': 1, 'duration_count': 1, 'input_tokens': 0, 'market': 'US', 'max_ms': 21158.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 21158.0, 'p95_ms': 21158.0, 'ticker': 'RBRK'}, {'avg_ms': 23399.0, 'calls': 1, 'duration_count': 1, 'input_tokens': 0, 'market': 'US', 'max_ms': 23399.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 23399.0, 'p95_ms': 23399.0, 'ticker': 'SOFI'}, {'avg_ms': 12610.0, 'calls': 1, 'duration_count': 1, 'input_tokens': 0, 'market': 'US', 'max_ms': 12610.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 12610.0, 'p95_ms': 12610.0, 'ticker': 'HPQ'}], 'slowest': [{'analyst_type': '', 'date': '2026-06-01', 'decision': 'SELL', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 26125, 'market': 'US', 'review_reason': 'SELL only if this automatic sell signal is still valid after fresh review. Retur', 'source': 'hold_advisor_decision', 'ticker': 'BBY'}, {'analyst_type': '', 'date': '2026-06-01', 'decision': 'SELL', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 26010, 'market': 'US', 'review_reason': 'SELL only if this PathB automatic sell signal remains valid after fresh review. ', 'source': 'hold_advisor_decision', 'ticker': 'MSFT'}, {'analyst_type': '', 'date': '2026-06-01', 'decision': 'SELL', 'decision_stage': 'INTRADAY_REVIEW', 'duration_ms': 25720, 'market': 'US', 'review_reason': 'HOLD unless risk/reward has deteriorated or thesis is invalid.', 'source': 'hold_advisor_decision', 'ticker': 'BBY'}, {'analyst_type': '', 'date': '2026-06-01', 'decision': 'SELL', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 24165, 'market': 'US', 'review_reason': 'SELL only if this PathB automatic sell signal remains valid after fresh review. ', 'source': 'hold_advisor_decision', 'ticker': 'AVGO'}, {'analyst_type': '', 'date': '2026-06-01', 'decision': 'SELL', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 23399, 'market': 'US', 'review_reason': 'This profit-protection sell is reviewable. SELL if giveback risk now outweighs r', 'source': 'hold_advisor_decision', 'ticker': 'SOFI'}, {'analyst_type': '', 'date': '2026-06-01', 'decision': 'SELL', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 21158, 'market': 'US', 'review_reason': 'SELL only if this PathB automatic sell signal remains valid after fresh review. ', 'source': 'hold_advisor_decision', 'ticker': 'RBRK'}, {'analyst_type': '', 'date': '2026-06-01', 'decision': 'HOLD', 'decision_stage': 'INTRADAY_REVIEW', 'duration_ms': 12610, 'market': 'US', 'review_reason': 'HOLD unless risk/reward has deteriorated or thesis is invalid.', 'source': 'hold_advisor_decision', 'ticker': 'HPQ'}, {'analyst_type': '', 'date': '2026-06-01', 'decision': 'HOLD', 'decision_stage': 'INTRADAY_REVIEW', 'duration_ms': 11843, 'market': 'US', 'review_reason': 'This PathB position has open profit. Prefer HOLD only with explicit protective_s', 'source': 'hold_advisor_decision', 'ticker': 'HPE'}, {'analyst_type': '', 'date': '2026-06-01', 'decision': 'HOLD', 'decision_stage': 'INTRADAY_REVIEW', 'duration_ms': 11184, 'market': 'US', 'review_reason': 'This PathB position has open profit. Prefer HOLD only with explicit protective_s', 'source': 'hold_advisor_decision', 'ticker': 'AVGO'}, {'analyst_type': '', 'date': '2026-06-01', 'decision': 'HOLD', 'decision_stage': 'INTRADAY_REVIEW', 'duration_ms': 10477, 'market': 'US', 'review_reason': 'This PathB position has open profit. Prefer HOLD only with explicit protective_s', 'source': 'hold_advisor_decision', 'ticker': 'HPE'}, {'analyst_type': '', 'date': '2026-06-01', 'decision': 'SELL', 'decision_stage': 'INTRADAY_REVIEW', 'duration_ms': 10389, 'market': 'US', 'review_reason': 'HOLD unless risk/reward has deteriorated or thesis is invalid.', 'source': 'hold_advisor_decision', 'ticker': 'EL'}, {'analyst_type': '', 'date': '2026-06-01', 'decision': 'SELL', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 9358, 'market': 'US', 'review_reason': 'SELL only if this automatic sell signal is still valid after fresh review. Retur', 'source': 'hold_advisor_decision', 'ticker': 'EL'}], 'summary': {'avg_ms': 17703.167, 'calls': 12, 'duration_count': 12, 'input_tokens': 0, 'max_ms': 26125.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 16884.0, 'p95_ms': 26061.75}}, 'decision_votes': {'by_analyst': [{'analyst_type': 'bull', 'avg_ms': 11638.25, 'calls': 12, 'duration_count': 12, 'input_tokens': 0, 'max_ms': 13450.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 11723.0, 'p95_ms': 13319.1}, {'analyst_type': 'bear', 'avg_ms': 11638.25, 'calls': 12, 'duration_count': 12, 'input_tokens': 0, 'max_ms': 13450.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 11723.0, 'p95_ms': 13319.1}, {'analyst_type': 'neutral', 'avg_ms': 11638.25, 'calls': 12, 'duration_count': 12, 'input_tokens': 0, 'max_ms': 13450.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 11723.0, 'p95_ms': 13319.1}], 'by_market_stage_analyst': [{'analyst_type': 'bull', 'avg_ms': 11670.167, 'calls': 6, 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_count': 6, 'input_tokens': 0, 'market': 'US', 'max_ms': 13450.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 11869.0, 'p95_ms': 13263.25}, {'analyst_type': 'bear', 'avg_ms': 11670.167, 'calls': 6, 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_count': 6, 'input_tokens': 0, 'market': 'US', 'max_ms': 13450.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 11869.0, 'p95_ms': 13263.25}, {'analyst_type': 'neutral', 'avg_ms': 11670.167, 'calls': 6, 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_count': 6, 'input_tokens': 0, 'market': 'US', 'max_ms': 13450.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 11869.0, 'p95_ms': 13263.25}, {'analyst_type': 'bull', 'avg_ms': 11606.333, 'calls': 6, 'decision_stage': 'INTRADAY_REVIEW', 'duration_count': 6, 'input_tokens': 0, 'market': 'US', 'max_ms': 13212.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 11498.0, 'p95_ms': 13057.75}, {'analyst_type': 'bear', 'avg_ms': 11606.333, 'calls': 6, 'decision_stage': 'INTRADAY_REVIEW', 'duration_count': 6, 'input_tokens': 0, 'market': 'US', 'max_ms': 13212.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 11498.0, 'p95_ms': 13057.75}, {'analyst_type': 'neutral', 'avg_ms': 11606.333, 'calls': 6, 'decision_stage': 'INTRADAY_REVIEW', 'duration_count': 6, 'input_tokens': 0, 'market': 'US', 'max_ms': 13212.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 11498.0, 'p95_ms': 13057.75}], 'summary': {'avg_ms': 11638.25, 'calls': 36, 'duration_count': 36, 'input_tokens': 0, 'max_ms': 13450.0, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': 11723.0, 'p95_ms': 13450.0}}, 'generated_at': '2026-06-02T07:01:13+09:00', 'scope': {'db_path': 'E:\\code\\claudetrade\\data\\audit\\agent_call_events.db', 'decision_dir': 'E:\\code\\claudetrade\\logs\\hold_advisor', 'end_date': '2026-06-01', 'market': 'US', 'raw_dir': 'E:\\code\\claudetrade\\logs\\raw_calls', 'single_call_source': 'agent_call_events', 'source': 'auto', 'start_date': '2026-06-01'}, 'single_calls': {'by_analyst': [{'analyst_type': 'triage', 'avg_ms': 11638.25, 'calls': 12, 'duration_count': 12, 'input_tokens': 14437, 'max_ms': 13450.0, 'missing_duration_count': 0, 'output_tokens': 6729, 'p50_ms': 11723.0, 'p95_ms': 13319.1}, {'analyst_type': 'challenge', 'avg_ms': 12079.333, 'calls': 6, 'duration_count': 6, 'input_tokens': 7817, 'max_ms': 14472.0, 'missing_duration_count': 0, 'output_tokens': 3117, 'p50_ms': 11861.0, 'p95_ms': 14172.25}], 'by_market': [{'avg_ms': 11785.278, 'calls': 18, 'duration_count': 18, 'input_tokens': 22254, 'market': 'US', 'max_ms': 14472.0, 'missing_duration_count': 0, 'output_tokens': 9846, 'p50_ms': 11723.0, 'p95_ms': 13603.3}], 'by_market_stage_analyst': [{'analyst_type': 'triage', 'avg_ms': 11670.167, 'calls': 6, 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_count': 6, 'input_tokens': 7496, 'market': 'US', 'max_ms': 13450.0, 'missing_duration_count': 0, 'output_tokens': 3378, 'p50_ms': 11869.0, 'p95_ms': 13263.25}, {'analyst_type': 'triage', 'avg_ms': 11606.333, 'calls': 6, 'decision_stage': 'INTRADAY_REVIEW', 'duration_count': 6, 'input_tokens': 6941, 'market': 'US', 'max_ms': 13212.0, 'missing_duration_count': 0, 'output_tokens': 3351, 'p50_ms': 11498.0, 'p95_ms': 13057.75}, {'analyst_type': 'challenge', 'avg_ms': 11999.2, 'calls': 5, 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_count': 5, 'input_tokens': 6584, 'market': 'US', 'max_ms': 14472.0, 'missing_duration_count': 0, 'output_tokens': 2573, 'p50_ms': 11242.0, 'p95_ms': 14232.2}, {'analyst_type': 'challenge', 'avg_ms': 12480.0, 'calls': 1, 'decision_stage': 'INTRADAY_REVIEW', 'duration_count': 1, 'input_tokens': 1233, 'market': 'US', 'max_ms': 12480.0, 'missing_duration_count': 0, 'output_tokens': 544, 'p50_ms': 12480.0, 'p95_ms': 12480.0}], 'by_stage': [{'avg_ms': 11819.727, 'calls': 11, 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_count': 11, 'input_tokens': 14080, 'max_ms': 14472.0, 'missing_duration_count': 0, 'output_tokens': 5951, 'p50_ms': 11615.0, 'p95_ms': 13961.0}, {'avg_ms': 11731.143, 'calls': 7, 'decision_stage': 'INTRADAY_REVIEW', 'duration_count': 7, 'input_tokens': 8174, 'max_ms': 13212.0, 'missing_duration_count': 0, 'output_tokens': 3895, 'p50_ms': 11831.0, 'p95_ms': 13026.9}], 'slowest': [{'analyst_type': 'challenge', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 14472, 'market': 'US', 'review_reason': 'SELL only if this automatic sell signal is still valid after fresh review. Retur', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'triage', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 13450, 'market': 'US', 'review_reason': 'SELL only if this PathB automatic sell signal remains valid after fresh review. ', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'challenge', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 13273, 'market': 'US', 'review_reason': 'SELL only if this PathB automatic sell signal remains valid after fresh review. ', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'triage', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'INTRADAY_REVIEW', 'duration_ms': 13212, 'market': 'US', 'review_reason': 'HOLD unless risk/reward has deteriorated or thesis is invalid.', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'triage', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 12703, 'market': 'US', 'review_reason': 'SELL only if this PathB automatic sell signal remains valid after fresh review. ', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'triage', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'INTRADAY_REVIEW', 'duration_ms': 12595, 'market': 'US', 'review_reason': 'HOLD unless risk/reward has deteriorated or thesis is invalid.', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'challenge', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'INTRADAY_REVIEW', 'duration_ms': 12480, 'market': 'US', 'review_reason': 'HOLD unless risk/reward has deteriorated or thesis is invalid.', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'triage', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 12123, 'market': 'US', 'review_reason': 'This profit-protection sell is reviewable. SELL if giveback risk now outweighs r', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'triage', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'INTRADAY_REVIEW', 'duration_ms': 11831, 'market': 'US', 'review_reason': 'This PathB position has open profit. Prefer HOLD only with explicit protective_s', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'triage', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 11615, 'market': 'US', 'review_reason': 'SELL only if this automatic sell signal is still valid after fresh review. Retur', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'challenge', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 11242, 'market': 'US', 'review_reason': 'This profit-protection sell is reviewable. SELL if giveback risk now outweighs r', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'triage', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'INTRADAY_REVIEW', 'duration_ms': 11165, 'market': 'US', 'review_reason': 'This PathB position has open profit. Prefer HOLD only with explicit protective_s', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'triage', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 10796, 'market': 'US', 'review_reason': 'SELL only if this PathB automatic sell signal remains valid after fresh review. ', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'challenge', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 10687, 'market': 'US', 'review_reason': 'SELL only if this PathB automatic sell signal remains valid after fresh review. ', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'triage', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'INTRADAY_REVIEW', 'duration_ms': 10465, 'market': 'US', 'review_reason': 'This PathB position has open profit. Prefer HOLD only with explicit protective_s', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'triage', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'INTRADAY_REVIEW', 'duration_ms': 10370, 'market': 'US', 'review_reason': 'HOLD unless risk/reward has deteriorated or thesis is invalid.', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'challenge', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 10322, 'market': 'US', 'review_reason': 'SELL only if this PathB automatic sell signal remains valid after fresh review. ', 'source': 'agent_call_events', 'ticker': ''}, {'analyst_type': 'triage', 'date': '2026-06-01', 'decision': 'unknown', 'decision_stage': 'AUTO_SELL_REVIEW', 'duration_ms': 9334, 'market': 'US', 'review_reason': 'SELL only if this automatic sell signal is still valid after fresh review. Retur', 'source': 'agent_call_events', 'ticker': ''}], 'summary': {'avg_ms': 11785.278, 'calls': 18, 'duration_count': 18, 'input_tokens': 22254, 'max_ms': 14472.0, 'missing_duration_count': 0, 'output_tokens': 9846, 'p50_ms': 11723.0, 'p95_ms': 13603.3}}}
- lifecycle unique fills without close: 5
- lifecycle closed events: 2

## Quality And Contamination

- candidate consistency: prompt_mismatch=0 trace_missing=0 trade_ready_family_mismatch=0
- invalid price observations=0 reasons={}
- outcome coverage 30m={'audit_sparse': 313, 'classification_counts': {'data_insufficient': 11, 'in_prompt_not_selected': 273, 'not_in_prompt': 508, 'unknown': 218}, 'coverage_gap_reasons': {'missing_base': 1, 'too_few_future_samples': 696}, 'coverage_rate': 0.3099, 'insufficient_samples': 697, 'interpretation': 'reference_only', 'maturity': 'partial', 'status_counts': {'audit_sparse': 313, 'insufficient_samples': 697}, 'total': 1010} 60m={'audit_sparse': 370, 'classification_counts': {'data_insufficient': 11, 'in_prompt_not_selected': 273, 'not_in_prompt': 508, 'unknown': 218}, 'coverage_gap_reasons': {'missing_base': 1, 'too_few_future_samples': 639}, 'coverage_rate': 0.3663, 'insufficient_samples': 640, 'interpretation': 'reference_only', 'maturity': 'partial', 'status_counts': {'audit_sparse': 370, 'insufficient_samples': 640}, 'total': 1010}
- latency SLA: status=critical avg_ms=14665.974 p95_ms=37598.101 max_ms=98188.19
- v2 learning gate: rows_by_grade={'CLEAN': 58, 'DIRTY': 2, 'LEGACY_UNKNOWN': 138, 'SUSPECT': 87} excluded=280 reasons={'CLOSED_WITHOUT_FILL': 2, 'FORWARD_NOT_MEASURED': 138, 'FORWARD_PENDING_DATA': 70, 'ORDER_UNKNOWN_UNRESOLVED': 24}
- preflight: ok=True fails=0 warns=15 action_required_warns=5
- PathB remediation: current_unknown=0 stale_active=6 apply_eligible=0

## Issue Counts

- broker_sync_protected: 16
- broker_truth: 1032
- broker_truth_untrusted: 441
- guardian_block_start: 482
- log_error: 22
- log_warning: 1830
- order_unknown: 1244
- pending_sell_local_state: 155
- telegram: 36
- traceback: 2

## Adaptive Live Suggestions

- none

## Misjudgment Label Distribution

- none

## Profitability Improvement Actions

- Restore fresh US broker truth before judging missed buys or sells; stale truth can make entry fail-closed and contaminates exposure/capacity analysis.
- Clear the guardian BLOCK_START causes after verifying they are current-session relevant; stale guardian state can explain why otherwise valid candidates did not enter.
- Review previous-session PathB stale active rows against broker holdings; do not auto-close them without fresh broker evidence.
- Treat same-session 60m performance as reference-only until outcome coverage matures; do not promote a policy from sparse rows.

## Artifacts

- monitor_final_json: E:\code\claudetrade\docs\reports\overnight_us_monitor_20260601_2042_to_20260602_0700\final_report.json
- monitor_final_md: E:\code\claudetrade\docs\reports\overnight_us_monitor_20260601_2042_to_20260602_0700\final_report.md
- candidate_60m_json: E:\code\claudetrade\docs\reports\overnight_us_monitor_20260601_2042_to_20260602_0700\candidate_audit_60m.json
- candidate_30m_json: E:\code\claudetrade\docs\reports\overnight_us_monitor_20260601_2042_to_20260602_0700\candidate_audit_30m.json
- monitoring_ops_json: E:\code\claudetrade\docs\reports\overnight_us_monitor_20260601_2042_to_20260602_0700\monitoring_ops_report.json
- v2_quality_json: E:\code\claudetrade\docs\reports\overnight_us_monitor_20260601_2042_to_20260602_0700\v2_quality_audit.json
- preflight_summary_json: E:\code\claudetrade\docs\reports\overnight_us_monitor_20260601_2042_to_20260602_0700\live_preflight_summary.json
- command_results_json: E:\code\claudetrade\docs\reports\overnight_us_monitor_20260601_2042_to_20260602_0700\post_session_command_results.json

## Command Results

- live_preflight_summary: ok=True returncode=0 output=
- candidate_audit_60m: ok=True returncode=0 output=
- candidate_audit_30m: ok=True returncode=0 output=
- monitoring_ops_report: ok=True returncode=0 output=
- v2_quality_audit: ok=True returncode=0 output=E:\code\claudetrade\docs\reports\overnight_us_monitor_20260601_2042_to_20260602_0700\v2_quality_audit.json
- claude_misjudgments: ok=True returncode=0 output=E:\code\claudetrade\docs\reports\overnight_us_monitor_20260601_2042_to_20260602_0700\claude_misjudgments.json
- adaptive_live_condition_accuracy: ok=True returncode=0 output=

## 한국어 검토 부록

### 1. 최종 상태 요약

- 기준 시각: 2026-06-02 07:00 KST.
- 작업 방식: read-only 모니터링. 주문, 리스크 설정, `.env*`, `config/v2_start_config.json`, `state/brain.json`, PathB 보호 로직은 수정하지 않았다.
- 브로커 기준 최종 보유: `HPQ 11주 +8.02%`, `MRVL 1주 +0.58%`, `NOK 18주 +0.34%`.
- 브로커 기준 미체결: 0건.
- 로컬 기준 불일치: `local_open_positions=4`, `broker_positions=3`, `pending_sells=1`. 로컬 pending은 `CRWV` 매도 주문 `0030510398`, 사유 `CLOSED_CLAUDE_SELL`.
- broker truth는 정규장 마감 이후 `last_success_at=2026-06-01T20:00:03+00:00`에서 stale 상태가 됐다. 마감 후 판단은 브로커 포지션/미체결/체결 스냅샷 불완전성을 감안해야 한다.

### 2. 매도 품질

- 양호: `MSFT(+9.64%)`, `RBRK(+7.59%)`, `HOOD(+3.94%)`, `SOFI(+0.45%)`, `AVGO(+3.91%)`는 장 초반 수익 실현 또는 보호 청산이 정상 작동했다.
- 양호: `DELL`은 진입 후 약세가 확인되자 `loss_cap`으로 `-2.17%`에서 손실을 제한했다. 수익성 자체는 나빴지만 방어 동작은 필요한 쪽이었다.
- 주의: `ARM`은 target 청산 자체는 작동했지만, 로컬 이벤트스토어의 진입 주문/가격(`0030361202`, `402.29`)과 브로커 체결(`0030409061`, `413.82`)이 다르다. 로컬 표기 수익률 `+3.99%`는 브로커 원시 체결가 기준 약 `+1.15%` 수준으로 보이며, 수익률 attribution 오염으로 분류해야 한다.
- 주의: `EL`은 보호 매도 판단 시점 가격대보다 실제 broker fill이 `85.586`으로 낮았다. 슬리피지, 재호가, 주문 취소/재전송 흐름을 별도 확인해야 한다.
- 미확정: `BBY`, `HPE`, `CRWV`, `IBM`은 브로커 보유에서 사라졌거나 로컬이 종료/대기 상태를 기록했지만, 최종 `today_fills` 13건에는 해당 매도 체결 행이 보이지 않는다. 브로커 보유와 미체결을 1차 truth로 보면 노출은 줄었지만, 실현손익 계산에는 체결 행 보강이 필요하다.

### 3. 매수 품질

- 유효 진입: `HPE`, `CRWV`, `NOK`, `MRVL`은 PathB buy-zone 또는 후보 흐름에서 진입했고, 한때 유의미한 MFE가 있었다. `HPE`는 peak `+5.23%`, `CRWV`는 peak `+3.47%`, `MRVL`은 peak `+3.10%`, `NOK`는 peak `+1.73%`로 관찰됐다.
- 실패 진입: `DELL`은 후보/가격대 진입 후 바로 약해져 loss cap으로 종료됐다. 진입 품질 또는 buy-zone 하단 확인 강도를 재검토할 대상이다.
- 오염 진입: `ARM`은 주문 번호와 실제 진입가 attribution이 어긋나 성과 집계가 오염됐다.
- 미확정 진입: `IBM/MRVL`은 로컬 이벤트상 진입이 보였지만, 브로커 `today_fills`에는 최신 체결 행이 따라오지 않는 구간이 있었다. 최종적으로 `IBM`은 브로커 보유에서 사라졌고, `MRVL`만 남았다.

### 4. 왜 매수를 못했는가

- `NVDA/DDOG/CRM/NET/SMCI` 등은 여러 시점에 `trade_ready` 또는 `applied_trade_ready`로 등장했지만 실제 주문으로 이어지지 않았다.
- 주된 차단 이유는 로컬 전략 `NO_SIGNAL`, 이미 보유 중인 종목 필터(`NOK`, `CRWV`, `HPE`), same-day reentry/slot/risk 계열 차단, late-session 보수성이다.
- `CRM`은 특히 `permanent_order_reject`와 `해당종목정보가 없습니다` 브로커 응답이 관찰됐다. 후보 품질 문제가 아니라 주문 가능 종목 검증/security master 문제로 분리해야 한다.
- 후보가 좋아 보였지만 못 산 경우보다, Claude 후보 판단과 로컬 실행 가능 조건이 어긋난 경우가 더 많았다. 후보 프롬프트에 로컬 전략 준비도와 브로커 주문 가능성을 더 일찍 반영해야 한다.

### 5. 왜 매도를 못했거나 늦었는가

- `HPE/CRWV`는 로컬상 매도 주문 또는 종료 사유가 있었고 브로커 보유에서도 사라졌지만, 체결 행이 없어 "못 판 것"인지 "팔렸지만 fill visibility가 누락된 것"인지 확정하기 어렵다.
- `IBM`은 03:41 KST에 `-2.51%`까지 밀렸지만 계획상 stop_loss `315.0`, 실제 당시 가격 약 `318.145`로 하드 스톱에는 닿지 않았다. 이후 브로커 보유에서 사라졌으나 체결 행이 없어 종료 근거가 불완전하다.
- `MRVL/NOK`은 각각 peak 대비 이익을 크게 반납했다. 최종 브로커 PnL은 `MRVL +0.58%`, `NOK +0.34%`이며, 현재 정책이 작은 PathB 수익을 충분히 잠그지 못한 가능성이 있다.
- 다만 PathB profit ladder와 hold-advisor 보호 정책은 핵심 수익 경로이므로 즉시 완화/변경하지 말고 shadow 분석으로 먼저 검증해야 한다.

### 6. 오염도와 운영 리스크

- `order_unknown` 누적 `1244`, `pending_sell_local_state` 누적 `155`, `broker_truth_untrusted` 누적 `441`은 오늘 리포트의 가장 큰 품질 저하 요인이다.
- `ARM`처럼 로컬 entry fill과 broker fill이 다른 경우에는 실현손익, 전략 attribution, 학습 데이터가 동시에 오염된다.
- `HPE/CRWV/BBY/IBM`처럼 broker position은 사라졌는데 fill row가 없는 경우, 포지션 truth와 손익 truth가 분리된다.
- `today_fills_count=13`이 장 후반에도 늘지 않았는데 포지션은 줄었다. broker fill 조회가 일부만 반환되거나, paging/order별 조회/정규화가 부족할 가능성이 있다.
- 후보 audit의 30m/60m outcome coverage는 partial/reference-only 수준이다. 같은 날 sparse outcome만으로 전략 변경을 결정하면 안 된다.

### 7. 수익성 개선 우선순위

1. 브로커 체결 정합성부터 고친다. order_no, side, qty, avg_price가 일치하는 broker fill이 없으면 realized PnL과 전략 성과를 `provisional`로 표시한다.
2. `today_fills` 누락 방지를 위해 장 후반/마감 후 order별 조회 또는 paging 검증을 추가한다. 포지션이 사라졌는데 fill이 없으면 자동 성과 확정 대신 reconciliation queue로 보낸다.
3. `ARM` 케이스처럼 로컬 주문가를 실제 체결가로 승격하지 못하게 한다. broker fill 또는 broker avg evidence가 없으면 entry price를 학습/성과 truth로 쓰지 않는다.
4. `CRM` 같은 주문 불가능 종목은 Claude 후보 승격 전에 broker security/orderability precheck로 제거한다.
5. `NO_SIGNAL`, `permanent_order_reject`, `already_holding` 같은 no-submit 원인을 나중 cycle의 일반 `NO_SIGNAL`이 덮어쓰지 못하게 보존한다.
6. `ORDER_UNKNOWN` 로그는 order/run 단위 cooldown과 상태 집계로 압축한다. 현재처럼 반복 누적되면 실제 신규 장애와 과거 잔여 문제가 섞인다.
7. `MRVL/NOK/CRWV`처럼 MFE 후 이익을 많이 반납하는 PathB 포지션은 장 후반 giveback shadow rule을 검토한다. 예: MFE 1.5% 이상 후 최종 0.5% 이하로 밀리는 패턴을 30건 이상 관찰한 뒤 profit ladder/hold advisor 조정 여부를 판단한다.
8. Claude 후보 프롬프트에 로컬 전략 준비도, same-day reentry, 보유 중 필터, 브로커 주문 가능성 신호를 더 명시한다. 목표는 "좋은 종목 추천"이 아니라 "실행 가능한 trade_ready" 비율 개선이다.
9. PathB 핵심 수익 경로인 target/profit ladder/pre-close/hold-advisor는 보호 영역으로 유지한다. 오늘 결과만으로 정책을 직접 변경하지 않고, fill truth 정리 후 shadow 검증을 먼저 한다.
