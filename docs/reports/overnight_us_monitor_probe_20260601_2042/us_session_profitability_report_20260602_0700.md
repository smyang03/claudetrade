# US Session Profitability Review

- generated_at: 2026-06-01T21:17:19+09:00
- session_date: 2026-06-01
- monitor_window: 2026-06-01T20:44:33+09:00 ~ 2026-06-02T07:00:00+09:00
- source_dir: E:\code\claudetrade\docs\reports\overnight_us_monitor_probe_20260601_2042
- requested_regular_window: 2026-06-01T22:30:00+09:00 ~ 2026-06-02T07:00:00+09:00
- monitor_source: final_report.json
- monitor_final_ready: True
- read_only: True

## Executive Summary

- decisions observed: entries=0, exits=0, hold_reviews=0
- broker truth: missing=False stale=True error= positions=8 open_orders=0 fills=0
- guardian: gate=BLOCK_START ok=False heartbeat_status=blocked
- unresolved state: protected=1 pending_sells=0 order_unknown_events=3 manual_action_required=2
- Claude calls observed by monitor: 0 labels={}

## Broker Performance Snapshot

| ticker | qty | pnl | mfe | mae | strategy | path |
| --- | --- | --- | --- | --- | --- | --- |
| AVGO | 1 | 1.62% | NA | NA |  |  |
| BBY | 2 | 6.14% | NA | NA |  |  |
| EL | 1 | 1.31% | NA | NA |  |  |
| HOOD | 3 | 8.47% | NA | NA |  |  |
| HPQ | 11 | -0.52% | NA | NA |  |  |
| MSFT | 1 | 5.95% | NA | NA |  |  |
| RBRK | 2 | 5.54% | NA | NA |  |  |
| SOFI | 16 | 0.58% | NA | NA |  |  |

- no broker fills in latest snapshot

- no decision events observed during monitor window

## Buy Non-Execution Causality

- No US candidate rows were present in candidate audit for the session, so missed buys cannot be attributed to selection quality yet.
- No US Claude calls were observed by the monitor after start, so no Claude-side buy decision was visible in this window.
- US broker truth was stale, missing, or errored; live entry gates should be treated as fail-closed until fresh broker truth returns.
- Guardian gate was BLOCK_START; this can block startup/entry independent of candidate quality.
- Previous-session PathB active rows remain, so entry capacity and reconciliation state need broker-truth review before policy changes.

## Sell Non-Execution Causality

- No sell/closed decision events were observed during the monitor window.
- Latest broker snapshot still had 8 US positions; absence of sells should be judged against hold advisor and stop/target triggers.
- Latest broker snapshot had no same-day fills, so broker evidence does not show a missed submitted sell in the snapshot.
- 1 protected positions were present; protective hold/reconcile status can suppress automatic cleanup.
- Stale or untrusted broker truth also weakens sell forensic certainty; use broker positions/open orders/fills as the final truth.
- Guardian BLOCK_START was present; separate runtime safety blocks from sell-advisor quality.

## Buy Path Review

- candidate rows: 0 latest_checked=0
- prompt/watchlist pool: full=None prompt=None watchlist=None
- raw trade_ready=None normalized=None applied=None execution_pool=None
- dropped_after_raw=[]
- runtime_filtered_count=None reasons={}
- PathB wait tickers=[]
- missed winners found=0 at 60m horizon

- no high-confidence missed-winner rows were mature enough in the 60m outcome table.

## Watch And Block Reasons

- none

## Watch Bucket Decomposition

- none

## Sell Path Review

- exits observed during monitor window: 0
- pending sell local rows: 0
- protected positions: 1
- hold advisor latency/status: {'decision_requests': {'by_market': [], 'by_market_stage_decision': [], 'by_stage': [], 'by_symbol': [], 'slowest': [], 'summary': {'avg_ms': None, 'calls': 0, 'duration_count': 0, 'input_tokens': 0, 'max_ms': None, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': None, 'p95_ms': None}}, 'decision_votes': {'by_analyst': [], 'by_market_stage_analyst': [], 'summary': {'avg_ms': None, 'calls': 0, 'duration_count': 0, 'input_tokens': 0, 'max_ms': None, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': None, 'p95_ms': None}}, 'generated_at': '2026-06-01T21:17:10+09:00', 'scope': {'db_path': 'E:\\code\\claudetrade\\data\\audit\\agent_call_events.db', 'decision_dir': 'E:\\code\\claudetrade\\logs\\hold_advisor', 'end_date': '2026-06-01', 'market': 'US', 'raw_dir': 'E:\\code\\claudetrade\\logs\\raw_calls', 'single_call_source': 'raw_calls', 'source': 'auto', 'start_date': '2026-06-01'}, 'single_calls': {'by_analyst': [], 'by_market': [], 'by_market_stage_analyst': [], 'by_stage': [], 'slowest': [], 'summary': {'avg_ms': None, 'calls': 0, 'duration_count': 0, 'input_tokens': 0, 'max_ms': None, 'missing_duration_count': 0, 'output_tokens': 0, 'p50_ms': None, 'p95_ms': None}}}
- lifecycle unique fills without close: 0
- lifecycle closed events: 0

## Quality And Contamination

- candidate consistency: prompt_mismatch=0 trace_missing=0 trade_ready_family_mismatch=0
- invalid price observations=0 reasons={}
- outcome coverage 30m=None 60m=None
- latency SLA: status=missing avg_ms=None p95_ms=None max_ms=None
- v2 learning gate: rows_by_grade={'CLEAN': 58, 'DIRTY': 2, 'LEGACY_UNKNOWN': 138, 'SUSPECT': 87} excluded=280 reasons={'CLOSED_WITHOUT_FILL': 2, 'FORWARD_NOT_MEASURED': 138, 'FORWARD_PENDING_DATA': 70, 'ORDER_UNKNOWN_UNRESOLVED': 24}
- preflight: ok=True fails=0 warns=14 action_required_warns=4
- PathB remediation: current_unknown=0 stale_active=6 apply_eligible=0

## Issue Counts

- broker_truth_untrusted: 1
- guardian_block_start: 1

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

- monitor_final_json: E:\code\claudetrade\docs\reports\overnight_us_monitor_probe_20260601_2042\final_report.json
- monitor_final_md: E:\code\claudetrade\docs\reports\overnight_us_monitor_probe_20260601_2042\final_report.md
- candidate_60m_json: E:\code\claudetrade\docs\reports\overnight_us_monitor_probe_20260601_2042\candidate_audit_60m.json
- candidate_30m_json: E:\code\claudetrade\docs\reports\overnight_us_monitor_probe_20260601_2042\candidate_audit_30m.json
- monitoring_ops_json: E:\code\claudetrade\docs\reports\overnight_us_monitor_probe_20260601_2042\monitoring_ops_report.json
- v2_quality_json: E:\code\claudetrade\docs\reports\overnight_us_monitor_probe_20260601_2042\v2_quality_audit.json
- preflight_summary_json: E:\code\claudetrade\docs\reports\overnight_us_monitor_probe_20260601_2042\live_preflight_summary.json
- command_results_json: E:\code\claudetrade\docs\reports\overnight_us_monitor_probe_20260601_2042\post_session_command_results.json

## Command Results

- live_preflight_summary: ok=True returncode=0 output=
- candidate_audit_60m: ok=True returncode=0 output=
- candidate_audit_30m: ok=True returncode=0 output=
- monitoring_ops_report: ok=True returncode=0 output=
- v2_quality_audit: ok=True returncode=0 output=E:\code\claudetrade\docs\reports\overnight_us_monitor_probe_20260601_2042\v2_quality_audit.json
- claude_misjudgments: ok=True returncode=0 output=E:\code\claudetrade\docs\reports\overnight_us_monitor_probe_20260601_2042\claude_misjudgments.json
- adaptive_live_condition_accuracy: ok=True returncode=0 output=

