# US Claude Morning Review / 미국장 Claude 아침 리뷰

- generated_at: 2026-06-02T07:02:01+09:00
- window: 2026-06-01T22:30:00+09:00 ~ 2026-06-02T07:00:00+09:00
- scope: live / US / 2026-06-01
- source_reports: docs\reports\overnight_us_monitor_20260601_2230\final_report.md / docs\reports\overnight_us_monitor_20260601_2230\claude_io_quality.md

## 한국어 요약

- 운영 상태: monitor=completed guardian=BLOCK_START ok=False
- 브로커 truth: missing=False stale=True error=
- Claude 사용량(검토 기준): source=quality_report_raw_call_scan calls=113 input_tokens=435092 output_tokens=75870 total_tokens=510962
- API usage delta: calls=-51 input_tokens=-397694 output_tokens=-18918 cost_usd=-1.476852 trusted=False
- raw call 관측: files=113 input=435092 output=75870 duration_ms=929447
- Claude I/O 품질: calls=113 parse_errors=0 avg_input=3850.4 avg_output=671.4
- 지연시간 커버리지: observed=63 missing=50 avg_duration_ms=14753.1
- 사용량 일관성: calls_match=False input_match=False output_match=False
- 입력 이슈: {"prompt_input_tokens_ge_12000": 11, "prompt_input_tokens_ge_8000": 5}
- 출력 이슈: {"candidate_actions_not_one_per_watchlist": 1, "duplicate_candidate_action": 2, "duplicate_watchlist_ticker": 1, "output_tokens_ge_2000": 1, "response_fenced_json": 61, "response_has_preamble_or_wrapper": 61, "response_not_strict_json": 61, "slow_call_30s": 1, "trade_ready_action_not_buy_or_probe": 1}

## Operations

- monitor_status: completed
- guardian_gate: BLOCK_START ok=False
- broker_truth: missing=False stale=True error=
- broker_positions/open_orders/fills: 3 / 0 / 13
- decision_events: 16
- log_issue_counts: {"broker_sync_protected": 14, "broker_truth": 990, "broker_truth_untrusted": 348, "data_collection_minute_price_stale": 8, "guardian_block_start": 389, "log_error": 20, "log_warning": 1750, "order_unknown": 1245, "pending_sell_local_state": 148, "telegram": 32}

## Claude Usage

- review_usage: source=quality_report_raw_call_scan calls=113 input=435092 output=75870 total=510962 api_cost_trusted=False
- api_usage_delta: calls=-51 input=-397694 output=-18918 cost_usd=-1.476852
- raw_call_files: 113
- raw_tokens: input=435092 output=75870 duration_ms=929447
- raw_by_label: {"analyst_bear_r1": 5, "analyst_bear_r2": 5, "analyst_bull_r1": 5, "analyst_bull_r2": 5, "analyst_neutral_r1": 5, "analyst_neutral_r2": 5, "hold_advisor_bear": 3, "hold_advisor_bull": 3, "hold_advisor_challenge": 4, "hold_advisor_neutral": 3, "hold_advisor_triage": 42, "param_tuner": 2, "postmortem": 1, "select_tickers": 16, "tune_120min": 1, "tune_150min": 1, "tune_30min": 4, "tune_60min": 2, "tune_90min": 1}
- raw_by_model: {"claude-sonnet-4-6": 113}
- hold_advisor_calls: 55 by_label={"hold_advisor_bear": 3, "hold_advisor_bull": 3, "hold_advisor_challenge": 4, "hold_advisor_neutral": 3, "hold_advisor_triage": 42}

## Claude Lightweighting

- status: mixed
- conclusion: 후단 hold/r2 경량화는 대체로 작동하지만 selection/R1 입력 집중이 높아 전단 경량화는 미흡합니다.
- total_tokens: 510962
- front_load_share_pct: 63.0 selection=42.4 analyst_r1=20.6
- hold_advisor_share_pct: 22.6
- high_input_issue_count: 16 max_avg_input_tokens_by_label=11863.0
- positive_signals: ["analyst_r2_reduced_vs_r1"]
- concerns: ["selection_token_share_high", "large_prompt_input_observed"]

### Lightweighting Groups

| group | calls | input | output | total | share % | avg total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| selection | 16 | 189808 | 26856 | 216664 | 42.4 | 13541.5 |
| analyst_r1 | 15 | 101839 | 3193 | 105032 | 20.6 | 7002.1 |
| analyst_r2 | 15 | 37159 | 3791 | 40950 | 8.0 | 2730.0 |
| hold_advisor | 55 | 79641 | 36029 | 115670 | 22.6 | 2103.1 |
| tuning | 8 | 13416 | 2722 | 16138 | 3.2 | 2017.2 |
| other | 0 | 0 | 0 | 16508 | 3.2 | 0.0 |

### Lightweighting Actions
- select_tickers 후보/evidence pack 행 수와 반복 calibration 블록을 줄이고 8k+ 입력 프롬프트를 별도 경고 기준으로 관리합니다.

## Claude I/O Quality

- quality_calls: 113
- quality_tokens: input=435092 output=75870
- parse_errors: 0
- averages: input=3850.4 output=671.4 duration_ms=14753.1
- duration_coverage: observed=63 missing=50
- input_issues: {"prompt_input_tokens_ge_12000": 11, "prompt_input_tokens_ge_8000": 5}
- output_issues: {"candidate_actions_not_one_per_watchlist": 1, "duplicate_candidate_action": 2, "duplicate_watchlist_ticker": 1, "output_tokens_ge_2000": 1, "response_fenced_json": 61, "response_has_preamble_or_wrapper": 61, "response_not_strict_json": 61, "slow_call_30s": 1, "trade_ready_action_not_buy_or_probe": 1}

## Claude I/O By Label

| label | calls | input | output | total | avg input | avg output | avg ms | ms observed | ms missing | input issues | output issues |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| select_tickers | 16 | 189808 | 26856 | 216664 | 11863.0 | 1678.5 | 24175.7 | 7 | 9 | {"prompt_input_tokens_ge_12000": 11, "prompt_input_tokens_ge_8000": 5} | {"candidate_actions_not_one_per_watchlist": 1, "duplicate_candidate_action": 2, "duplicate_watchlist_ticker": 1, "response_fenced_json": 5, "response_has_preamble_or_wrapper": 5, "response_not_strict_json": 5, "trade_ready_action_not_buy_or_probe": 1} |
| hold_advisor_triage | 42 | 61522 | 30406 | 91928 | 1464.8 | 724.0 | 14269.3 | 42 | 0 | {} | {"response_fenced_json": 42, "response_has_preamble_or_wrapper": 42, "response_not_strict_json": 42} |
| analyst_bear_r1 | 5 | 34343 | 1118 | 35461 | 6868.6 | 223.6 | 0.0 | 0 | 5 | {} | {} |
| analyst_bull_r1 | 5 | 33768 | 1154 | 34922 | 6753.6 | 230.8 | 0.0 | 0 | 5 | {} | {} |
| analyst_neutral_r1 | 5 | 33728 | 921 | 34649 | 6745.6 | 184.2 | 0.0 | 0 | 5 | {} | {} |
| analyst_bear_r2 | 5 | 12783 | 1235 | 14018 | 2556.6 | 247.0 | 0.0 | 0 | 5 | {} | {} |
| analyst_bull_r2 | 5 | 12208 | 1349 | 13557 | 2441.6 | 269.8 | 0.0 | 0 | 5 | {} | {} |
| analyst_neutral_r2 | 5 | 12168 | 1207 | 13375 | 2433.6 | 241.4 | 0.0 | 0 | 5 | {} | {} |
| postmortem | 1 | 7858 | 2500 | 10358 | 7858.0 | 2500.0 | 47750.0 | 1 | 0 | {} | {"output_tokens_ge_2000": 1, "response_fenced_json": 1, "response_has_preamble_or_wrapper": 1, "response_not_strict_json": 1, "slow_call_30s": 1} |
| tune_30min | 4 | 7026 | 959 | 7985 | 1756.5 | 239.8 | 0.0 | 0 | 4 | {} | {} |
| hold_advisor_challenge | 4 | 5471 | 2234 | 7705 | 1367.8 | 558.5 | 12739.0 | 4 | 0 | {} | {"response_fenced_json": 4, "response_has_preamble_or_wrapper": 4, "response_not_strict_json": 4} |
| hold_advisor_bear | 3 | 4224 | 1184 | 5408 | 1408.0 | 394.7 | 7245.0 | 3 | 0 | {} | {"response_fenced_json": 3, "response_has_preamble_or_wrapper": 3, "response_not_strict_json": 3} |

## Claude Usage Timeline

- timeline_summary: status=distributed total_calls=113 total_tokens=510962
- peak_bucket: 2026-06-01T22:30+09:00 tokens=147644 share_pct=28.9
- first_bucket: 2026-06-01T22:30+09:00 tokens=147644 share_pct=28.9
- issue_buckets: high_input=11 non_strict_json=13
- conclusion: Claude 사용량이 여러 시간 버킷에 분산됐습니다. peak bucket과 고입력 버킷을 중심으로 조정합니다.

| bucket start | calls | input | output | total | avg input | top labels | input issues | output issues |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| 2026-06-01T22:30+09:00 | 26 | 135283 | 12361 | 147644 | 5203.2 | {"analyst_bear_r1": 3, "analyst_bear_r2": 3, "analyst_bull_r1": 3, "analyst_bull_r2": 3, "select_tickers": 4} | {"prompt_input_tokens_ge_12000": 1, "prompt_input_tokens_ge_8000": 3} | {"duplicate_candidate_action": 1, "duplicate_watchlist_ticker": 1, "response_fenced_json": 5, "response_has_preamble_or_wrapper": 5, "response_not_strict_json": 5} |
| 2026-06-01T23:00+09:00 | 17 | 63487 | 9515 | 73002 | 3734.5 | {"analyst_bear_r1": 1, "analyst_bear_r2": 1, "hold_advisor_challenge": 2, "hold_advisor_triage": 5, "select_tickers": 2} | {"prompt_input_tokens_ge_12000": 2} | {"response_fenced_json": 9, "response_has_preamble_or_wrapper": 9, "response_not_strict_json": 9} |
| 2026-06-01T23:30+09:00 | 10 | 54603 | 6025 | 60628 | 5460.3 | {"analyst_bear_r1": 1, "analyst_bear_r2": 1, "analyst_bull_r1": 1, "hold_advisor_triage": 2, "select_tickers": 2} | {"prompt_input_tokens_ge_12000": 2} | {"candidate_actions_not_one_per_watchlist": 1, "duplicate_candidate_action": 1, "response_fenced_json": 2, "response_has_preamble_or_wrapper": 2, "response_not_strict_json": 2} |
| 2026-06-02T00:00+09:00 | 7 | 20587 | 4006 | 24593 | 2941.0 | {"hold_advisor_bear": 1, "hold_advisor_bull": 1, "hold_advisor_neutral": 1, "hold_advisor_triage": 2, "select_tickers": 1} | {"prompt_input_tokens_ge_12000": 1} | {"response_fenced_json": 5, "response_has_preamble_or_wrapper": 5, "response_not_strict_json": 5, "trade_ready_action_not_buy_or_probe": 1} |
| 2026-06-02T00:30+09:00 | 10 | 25067 | 5744 | 30811 | 2506.7 | {"hold_advisor_bear": 2, "hold_advisor_bull": 2, "hold_advisor_neutral": 2, "hold_advisor_triage": 3, "select_tickers": 1} | {"prompt_input_tokens_ge_12000": 1} | {"response_fenced_json": 9, "response_has_preamble_or_wrapper": 9, "response_not_strict_json": 9} |
| 2026-06-02T01:00+09:00 | 6 | 20067 | 4798 | 24865 | 3344.5 | {"hold_advisor_challenge": 1, "hold_advisor_triage": 3, "param_tuner": 1, "select_tickers": 1} | {"prompt_input_tokens_ge_12000": 1} | {"response_fenced_json": 4, "response_has_preamble_or_wrapper": 4, "response_not_strict_json": 4} |
| 2026-06-02T01:30+09:00 | 2 | 13861 | 1971 | 15832 | 6930.5 | {"select_tickers": 1, "tune_30min": 1} | {"prompt_input_tokens_ge_12000": 1} | {} |
| 2026-06-02T02:00+09:00 | 3 | 4673 | 2348 | 7021 | 1557.7 | {"hold_advisor_triage": 3} | {} | {"response_fenced_json": 3, "response_has_preamble_or_wrapper": 3, "response_not_strict_json": 3} |
| 2026-06-02T02:30+09:00 | 4 | 17132 | 3735 | 20867 | 4283.0 | {"hold_advisor_triage": 2, "select_tickers": 1, "tune_30min": 1} | {"prompt_input_tokens_ge_12000": 1} | {"response_fenced_json": 2, "response_has_preamble_or_wrapper": 2, "response_not_strict_json": 2} |
| 2026-06-02T03:00+09:00 | 7 | 11225 | 5376 | 16601 | 1603.6 | {"hold_advisor_triage": 6, "tune_60min": 1} | {} | {"response_fenced_json": 6, "response_has_preamble_or_wrapper": 6, "response_not_strict_json": 6} |
| 2026-06-02T03:30+09:00 | 6 | 19058 | 5318 | 24376 | 3176.3 | {"hold_advisor_triage": 4, "select_tickers": 1, "tune_90min": 1} | {"prompt_input_tokens_ge_8000": 1} | {"response_fenced_json": 4, "response_has_preamble_or_wrapper": 4, "response_not_strict_json": 4} |
| 2026-06-02T04:00+09:00 | 7 | 21648 | 6283 | 27931 | 3092.6 | {"hold_advisor_triage": 5, "select_tickers": 1, "tune_120min": 1} | {"prompt_input_tokens_ge_12000": 1} | {"response_fenced_json": 5, "response_has_preamble_or_wrapper": 5, "response_not_strict_json": 5} |
| 2026-06-02T04:30+09:00 | 6 | 18986 | 5140 | 24126 | 3164.3 | {"hold_advisor_triage": 4, "select_tickers": 1, "tune_150min": 1} | {"prompt_input_tokens_ge_8000": 1} | {"response_fenced_json": 5, "response_has_preamble_or_wrapper": 5, "response_not_strict_json": 5} |
| 2026-06-02T05:00+09:00 | 2 | 9415 | 3250 | 12665 | 4707.5 | {"hold_advisor_triage": 1, "postmortem": 1} | {} | {"output_tokens_ge_2000": 1, "response_fenced_json": 2, "response_has_preamble_or_wrapper": 2, "response_not_strict_json": 2, "slow_call_30s": 1} |

## Consistency Checks

- calls: {"api": -51, "quality": 113, "raw": 113} match=False
- input_tokens: {"api": -397694, "quality": 435092, "raw": 435092} match=False
- output_tokens: {"api": -18918, "quality": 75870, "raw": 75870} match=False
- raw_quality_match: calls=True input=True output=True
- api_negative_delta_detected: True fields=["calls", "input_tokens", "output_tokens"]
- usage_source_for_final_review: quality_report_raw_call_scan

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
- 2026-06-01T23:42:32+09:00 hold_advisor_triage input=[] output=["response_fenced_json", "response_has_preamble_or_wrapper", "response_not_strict_json"] path=logs\raw_calls\20260601_US_hold_advisor_triage_234232327304_339a1119c2.json

### Prompt Warning Samples
| time | label | input | chars | candidates | evidence requested | evidence pack | lessons chars | top sections | path |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 2026-06-01T23:15:37+09:00 | select_tickers | 12207 | 26564 | 35 | 28 | 5 | 199 | [{"chars": 14578, "lines": 37, "section": "candidates"}, {"chars": 3562, "lines": 2, "section": "runtime_evidence"}, {"chars": 2349, "lines": 42, "section": "digest_news"}, {"chars": 1354, "lines": 23, "section": "output_contract"}, {"chars": 977, "lines": 14, "section": "rules"}, {"chars": 845, "lines": 3, "section": "tuning_contract"}, {"chars": 780, "lines": 11, "section": "decision_contract"}, {"chars": 718, "lines": 6, "section": "hard_soft_boundary"}] | logs\raw_calls\20260601_US_select_tickers_231537596887_805d7ac836.json |
| 2026-06-02T00:17:01+09:00 | select_tickers | 12201 | 26561 | 35 | 28 | 5 | 199 | [{"chars": 14558, "lines": 37, "section": "candidates"}, {"chars": 3566, "lines": 2, "section": "runtime_evidence"}, {"chars": 2362, "lines": 42, "section": "digest_news"}, {"chars": 1354, "lines": 23, "section": "output_contract"}, {"chars": 977, "lines": 14, "section": "rules"}, {"chars": 845, "lines": 3, "section": "tuning_contract"}, {"chars": 780, "lines": 11, "section": "decision_contract"}, {"chars": 718, "lines": 6, "section": "hard_soft_boundary"}] | logs\raw_calls\20260602_US_select_tickers_001701587242_bb0b2458bc.json |
| 2026-06-01T23:22:40+09:00 | select_tickers | 12177 | 26560 | 35 | 28 | 5 | 199 | [{"chars": 14551, "lines": 37, "section": "candidates"}, {"chars": 3573, "lines": 2, "section": "runtime_evidence"}, {"chars": 2361, "lines": 42, "section": "digest_news"}, {"chars": 1354, "lines": 23, "section": "output_contract"}, {"chars": 977, "lines": 14, "section": "rules"}, {"chars": 845, "lines": 3, "section": "tuning_contract"}, {"chars": 780, "lines": 11, "section": "decision_contract"}, {"chars": 718, "lines": 6, "section": "hard_soft_boundary"}] | logs\raw_calls\20260601_US_select_tickers_232240535354_9df50b8770.json |
| 2026-06-01T23:40:01+09:00 | select_tickers | 12173 | 26531 | 35 | 28 | 5 | 199 | [{"chars": 14535, "lines": 37, "section": "candidates"}, {"chars": 3573, "lines": 2, "section": "runtime_evidence"}, {"chars": 2348, "lines": 42, "section": "digest_news"}, {"chars": 1354, "lines": 23, "section": "output_contract"}, {"chars": 977, "lines": 14, "section": "rules"}, {"chars": 845, "lines": 3, "section": "tuning_contract"}, {"chars": 780, "lines": 11, "section": "decision_contract"}, {"chars": 718, "lines": 6, "section": "hard_soft_boundary"}] | logs\raw_calls\20260601_US_select_tickers_234001616777_be624c5a9c.json |
| 2026-06-01T23:47:05+09:00 | select_tickers | 12168 | 26526 | 35 | 28 | 5 | 199 | [{"chars": 14551, "lines": 37, "section": "candidates"}, {"chars": 3552, "lines": 2, "section": "runtime_evidence"}, {"chars": 2348, "lines": 42, "section": "digest_news"}, {"chars": 1354, "lines": 23, "section": "output_contract"}, {"chars": 977, "lines": 14, "section": "rules"}, {"chars": 845, "lines": 3, "section": "tuning_contract"}, {"chars": 780, "lines": 11, "section": "decision_contract"}, {"chars": 718, "lines": 6, "section": "hard_soft_boundary"}] | logs\raw_calls\20260601_US_select_tickers_234705841825_a73d8742d2.json |
| 2026-06-02T02:34:23+09:00 | select_tickers | 12132 | 26497 | 35 | 28 | 5 | 199 | [{"chars": 14466, "lines": 37, "section": "candidates"}, {"chars": 3582, "lines": 2, "section": "runtime_evidence"}, {"chars": 2374, "lines": 42, "section": "digest_news"}, {"chars": 1354, "lines": 23, "section": "output_contract"}, {"chars": 977, "lines": 14, "section": "rules"}, {"chars": 845, "lines": 3, "section": "tuning_contract"}, {"chars": 780, "lines": 11, "section": "decision_contract"}, {"chars": 718, "lines": 6, "section": "hard_soft_boundary"}] | logs\raw_calls\20260602_US_select_tickers_023423921136_66e00acf02.json |
| 2026-06-02T01:54:06+09:00 | select_tickers | 12124 | 26504 | 35 | 28 | 5 | 199 | [{"chars": 14489, "lines": 37, "section": "candidates"}, {"chars": 3578, "lines": 2, "section": "runtime_evidence"}, {"chars": 2362, "lines": 42, "section": "digest_news"}, {"chars": 1354, "lines": 23, "section": "output_contract"}, {"chars": 977, "lines": 14, "section": "rules"}, {"chars": 845, "lines": 3, "section": "tuning_contract"}, {"chars": 780, "lines": 11, "section": "decision_contract"}, {"chars": 718, "lines": 6, "section": "hard_soft_boundary"}] | logs\raw_calls\20260602_US_select_tickers_015406187108_9bc8825af8.json |
| 2026-06-02T00:59:02+09:00 | select_tickers | 12120 | 26467 | 35 | 28 | 5 | 199 | [{"chars": 14463, "lines": 37, "section": "candidates"}, {"chars": 3555, "lines": 2, "section": "runtime_evidence"}, {"chars": 2374, "lines": 42, "section": "digest_news"}, {"chars": 1354, "lines": 23, "section": "output_contract"}, {"chars": 977, "lines": 14, "section": "rules"}, {"chars": 845, "lines": 3, "section": "tuning_contract"}, {"chars": 780, "lines": 11, "section": "decision_contract"}, {"chars": 718, "lines": 6, "section": "hard_soft_boundary"}] | logs\raw_calls\20260602_US_select_tickers_005902872170_95be3921f4.json |
| 2026-06-02T01:24:13+09:00 | select_tickers | 12115 | 26487 | 35 | 28 | 5 | 199 | [{"chars": 14495, "lines": 37, "section": "candidates"}, {"chars": 3555, "lines": 2, "section": "runtime_evidence"}, {"chars": 2362, "lines": 42, "section": "digest_news"}, {"chars": 1354, "lines": 23, "section": "output_contract"}, {"chars": 977, "lines": 14, "section": "rules"}, {"chars": 845, "lines": 3, "section": "tuning_contract"}, {"chars": 780, "lines": 11, "section": "decision_contract"}, {"chars": 718, "lines": 6, "section": "hard_soft_boundary"}] | logs\raw_calls\20260602_US_select_tickers_012413411746_f4c43b45d4.json |
| 2026-06-02T04:07:55+09:00 | select_tickers | 12061 | 26275 | 35 | 28 | 5 | 199 | [{"chars": 14362, "lines": 37, "section": "candidates"}, {"chars": 3463, "lines": 2, "section": "runtime_evidence"}, {"chars": 2375, "lines": 42, "section": "digest_news"}, {"chars": 1354, "lines": 23, "section": "output_contract"}, {"chars": 977, "lines": 14, "section": "rules"}, {"chars": 845, "lines": 3, "section": "tuning_contract"}, {"chars": 780, "lines": 11, "section": "decision_contract"}, {"chars": 718, "lines": 6, "section": "hard_soft_boundary"}] | logs\raw_calls\20260602_US_select_tickers_040755223717_64310bb0d1.json |
| 2026-06-01T22:56:51+09:00 | select_tickers | 12031 | 26377 | 35 | 28 | 5 | 199 | [{"chars": 14429, "lines": 37, "section": "candidates"}, {"chars": 3522, "lines": 2, "section": "runtime_evidence"}, {"chars": 2349, "lines": 42, "section": "digest_news"}, {"chars": 1354, "lines": 23, "section": "output_contract"}, {"chars": 977, "lines": 14, "section": "rules"}, {"chars": 845, "lines": 3, "section": "tuning_contract"}, {"chars": 780, "lines": 11, "section": "decision_contract"}, {"chars": 718, "lines": 6, "section": "hard_soft_boundary"}] | logs\raw_calls\20260601_US_select_tickers_225651877435_9aac36f802.json |
| 2026-06-01T22:36:33+09:00 | select_tickers | 11956 | 26586 | 35 | 30 | 5 | 199 | [{"chars": 14577, "lines": 37, "section": "candidates"}, {"chars": 3569, "lines": 2, "section": "runtime_evidence"}, {"chars": 2363, "lines": 42, "section": "digest_news"}, {"chars": 1354, "lines": 23, "section": "output_contract"}, {"chars": 977, "lines": 14, "section": "rules"}, {"chars": 845, "lines": 3, "section": "tuning_contract"}, {"chars": 780, "lines": 11, "section": "decision_contract"}, {"chars": 718, "lines": 6, "section": "hard_soft_boundary"}] | logs\raw_calls\20260601_US_select_tickers_223633986190_0e770481aa.json |

### Slow Call Samples
- 2026-06-02T05:03:35+09:00 postmortem duration_ms=47750 path=logs\raw_calls\20260601_US_postmortem_postmortem_US_2026-06-01_d35af0fb6f.json
- 2026-06-02T01:17:49+09:00 hold_advisor_triage duration_ms=15333 path=logs\raw_calls\20260602_US_hold_advisor_triage_011749226380_c48b0279c7.json
- 2026-06-02T02:04:08+09:00 hold_advisor_triage duration_ms=18833 path=logs\raw_calls\20260602_US_hold_advisor_triage_020408656326_d5b144d0d7.json
- 2026-06-02T02:36:16+09:00 hold_advisor_triage duration_ms=15608 path=logs\raw_calls\20260602_US_hold_advisor_triage_023616235674_5f75cd973e.json
- 2026-06-02T02:45:37+09:00 hold_advisor_triage duration_ms=15762 path=logs\raw_calls\20260602_US_hold_advisor_triage_024537115836_cd4ee41a89.json
- 2026-06-02T03:01:21+09:00 hold_advisor_triage duration_ms=15689 path=logs\raw_calls\20260602_US_hold_advisor_triage_030121895138_7463ccefb6.json
- 2026-06-02T03:04:01+09:00 hold_advisor_triage duration_ms=16584 path=logs\raw_calls\20260602_US_hold_advisor_triage_030401863332_caf82941dc.json
- 2026-06-02T03:04:39+09:00 hold_advisor_triage duration_ms=15587 path=logs\raw_calls\20260602_US_hold_advisor_triage_030439068683_8f4139c79e.json

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
- P2 db.order_unknown_unresolved: unresolved ORDER_UNKNOWN rows=3
- P1 db.pathb_stale_active_runs: previous-session active Path B rows=6
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
- P2 latency_cost: Separate timeout-prone labels from normal review flow and keep cache/cooldown guards active for repeated HOLD reviews.
- P2 token_cost: Reduce high-token prompts by trimming repeated calibration blocks and limiting evidence pack rows before model invocation; review labels with 8k+ input tokens even when the session average looks acceptable.
- P2 token_lightweighting: select_tickers 후보/evidence pack 행 수와 반복 calibration 블록을 줄이고 8k+ 입력 프롬프트를 별도 경고 기준으로 관리합니다.
- P1 operations: Resolve guardian BLOCK_START causes before treating the session as operationally clean; include any repeated block events observed during the window, not just the final snapshot.
- P1 broker_truth: Refresh broker truth and keep new entries fail-closed while snapshot freshness is untrusted; review repeated stale/untrusted snapshots from the full window.
- P1 reconciliation: Review manual-action-required local state before the next live start window.
- P1 runtime_errors: Inspect error samples and confirm they did not affect broker truth, order routing, or Claude fallback behavior.
- P2 order_state: Separate current-session unresolved ORDER_UNKNOWN from historical event noise in the morning review.
- P1 observability: API usage delta is negative for calls, input_tokens, output_tokens; treat the quality report's raw-call scan as the Claude usage source of truth for this review.
