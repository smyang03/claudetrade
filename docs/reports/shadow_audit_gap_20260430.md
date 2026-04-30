# Shadow Audit Gap Report - 2026-04-30

Generated at: 2026-04-30T21:19:43

## Summary

- Operating DBs already contain useful signal, block, lifecycle, and price samples.
- No existing live table provides uniform +5m/+15m/+30m/+60m outcome rows for every signal.
- `decision_id` exists in V2 lifecycle data, but a deterministic per-signal `signal_id` is still needed.
- `entry_timing` has candidate/signal/order/fill timing but not enough cross-system keys for full joins.

## Database Inventory

| db | path | exists | tables |
| --- | --- | --- | --- |
| decisions | data\ml\decisions.db | True | decisions, param_sessions, sqlite_sequence |
| v2_event_store | data\v2_event_store.db | True | lifecycle_events, phase_validation_runs, sqlite_sequence, v2_decisions, v2_path_runs |
| ticker_selection_log | data\ticker_selection_log.db | True | micro_probe_log, sqlite_sequence, ticker_selection_log |
| intraday_strategy_log | data\intraday_strategy_log.db | True | intraday_strategy_log, sqlite_sequence |

## decisions

| table | rows | session_rows | key_columns |
| --- | --- | --- | --- |
| decisions | 6 | 6 | market, ticker, session_date, price, block_reason, pnl_pct, forward_1d, entry_priority_score |
| param_sessions | 379 | 31 | session_date, market |
| sqlite_sequence | 2 | 0 |  |

### coverage

| metric | value |
| --- | --- |
| price_rows | 6 |
| score_rows | 0 |
| forward_1d_rows | 1 |

### decision_counts

| market | decision | block_reason | rows |
| --- | --- | --- | --- |
| KR | BUY_SIGNAL |  | 1 |
| KR | NO_SIGNAL |  | 1 |
| KR | SKIPPED | already_holding | 1 |
| KR | SKIPPED | low_confidence | 1 |
| US | BLOCKED | HALT_mode_block | 1 |
| US | BUY_SIGNAL |  | 1 |

## v2_event_store

| table | rows | session_rows | key_columns |
| --- | --- | --- | --- |
| lifecycle_events | 910 | 145 | event_type, market, session_date, ticker, decision_id |
| phase_validation_runs | 391 | 0 |  |
| sqlite_sequence | 2 | 0 |  |
| v2_decisions | 90 | 12 | decision_id, market, session_date, ticker |
| v2_path_runs | 137 | 18 | path_run_id, decision_id, market, session_date, ticker |

### event_counts

| market | event_type | reason_code | rows |
| --- | --- | --- | --- |
| KR | SAFETY_BLOCKED | ORDER_UNKNOWN_UNRESOLVED | 31 |
| KR | CLAUDE_PRICE_PLAN_CREATED |  | 18 |
| KR | CLAUDE_PRICE_WAITING |  | 18 |
| KR | FILLED |  | 13 |
| KR | CLAUDE_TRADE_READY |  | 12 |
| KR | ORDER_SENT |  | 9 |
| KR | CLAUDE_PRICE_EXPIRED |  | 7 |
| KR | CLOSED | CLOSED_USER_MANUAL | 7 |
| KR | CLAUDE_PRICE_CANCELLED |  | 6 |
| KR | CLAUDE_PRICE_HIT |  | 5 |
| KR | CLOSED | CLOSED_CLAUDE_PRICE_PRE_CLOSE | 5 |
| KR | ORDER_ACKED |  | 5 |
| KR | SAFETY_BLOCKED | CLAUDE_PRICE_INVALID | 5 |
| KR | SAFETY_PASSED |  | 3 |
| KR | SAFETY_BLOCKED | ALREADY_HOLDING | 1 |

## ticker_selection_log

| table | rows | session_rows | key_columns |
| --- | --- | --- | --- |
| micro_probe_log | 0 | 0 | session_date, market, ticker, entry_priority_score, pnl_pct |
| sqlite_sequence | 1 | 0 |  |
| ticker_selection_log | 2127 | 51 | date, market, ticker, entry_priority_score, pnl_pct, forward_1d |

### selection_counts

| bot_mode | market | source_type | trade_ready | rows |
| --- | --- | --- | --- | --- |
| live | KR | rescreen | 0 | 25 |
| live | KR | initial | 0 | 13 |
| live | KR | rescreen | 1 | 5 |
| live | KR | partial | 1 | 4 |
| live | KR | initial | 1 | 2 |
| live | US | partial | 1 | 2 |

## intraday_strategy_log

| table | rows | session_rows | key_columns |
| --- | --- | --- | --- |
| intraday_strategy_log | 11599 | 125 | session_date, market, ticker, price, pnl_pct |
| sqlite_sequence | 1 | 0 |  |

### stage_counts

| bot_mode | market | stage | rows |
| --- | --- | --- | --- |
| live | KR | outcome | 78 |
| live | KR | probe | 47 |

## entry_timing JSONL

| path | exists | rows | events | has_state | has_decision_id | has_signal_price |
| --- | --- | --- | --- | --- | --- | --- |
| logs\entry_timing\live_20260430_KR.jsonl | True | 136 | {'candidate_detected': 81, 'first_signal_checked': 15, 'signal_fired': 34, 'order_sent': 3, 'filled': 3} | 136 | 0 | 40 |

## candidate_health

| path | exists | ticker_rows | top_level_keys |
| --- | --- | --- | --- |
| state\candidate_health_KR_20260430.json | True | 41 | ['last_phase', 'market', 'schema_version', 'session_date', 'tickers', 'updated_at'] |

## Gap Decisions

| Requirement | Current Coverage | Action |
| --- | --- | --- |
| Per-signal deterministic key | Missing | Add `signal_id` |
| Episode key for ORDER_UNKNOWN pause | Missing | Add `episode_id` |
| Uniform intraday outcomes | Missing | Add passive price samples + updater |
| No-trade/block joins | Partial | Link `signal_id`, `decision_id`, `episode_id` |
| PathB market-block missed plans | Partial | Add blocked waiting-plan audit |
| Existing operating DB isolation | Good | Keep shadow DB separate |
