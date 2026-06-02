# Claude I/O Quality Report

- generated_at: 2026-06-01T23:42:05+09:00
- scope: US 2026-06-01T22:30:00+09:00 ~ 2026-06-02T07:00:00+09:00
- raw_calls: 51
- tokens: input=240012 output=25663 total=265675
- averages: input=4706.1 output=503.2 duration_ms=11804.1
- duration_coverage: observed=11 missing=40
- parse_errors: 0

## Calls By Label

| label | calls | input | output | total | avg input | avg output | avg ms | ms observed | ms missing | parse errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| select_tickers | 7 | 83069 | 11491 | 94560 | 11867.0 | 1641.6 | 0.0 | 0 | 7 | 0 |
| analyst_bear_r1 | 5 | 34343 | 1118 | 35461 | 6868.6 | 223.6 | 0.0 | 0 | 5 | 0 |
| analyst_bull_r1 | 5 | 33768 | 1154 | 34922 | 6753.6 | 230.8 | 0.0 | 0 | 5 | 0 |
| analyst_neutral_r1 | 5 | 33728 | 921 | 34649 | 6745.6 | 184.2 | 0.0 | 0 | 5 | 0 |
| analyst_bear_r2 | 5 | 12783 | 1235 | 14018 | 2556.6 | 247.0 | 0.0 | 0 | 5 | 0 |
| hold_advisor_triage | 8 | 9342 | 4402 | 13744 | 1167.8 | 550.2 | 11525.8 | 8 | 0 | 0 |
| analyst_bull_r2 | 5 | 12208 | 1349 | 13557 | 2441.6 | 269.8 | 0.0 | 0 | 5 | 0 |
| analyst_neutral_r2 | 5 | 12168 | 1207 | 13375 | 2433.6 | 241.4 | 0.0 | 0 | 5 | 0 |
| hold_advisor_challenge | 3 | 3801 | 1659 | 5460 | 1267.0 | 553.0 | 12546.3 | 3 | 0 | 0 |
| param_tuner | 1 | 1461 | 677 | 2138 | 1461.0 | 677.0 | 0.0 | 0 | 1 | 0 |
| tune_30min | 1 | 1713 | 231 | 1944 | 1713.0 | 231.0 | 0.0 | 0 | 1 | 0 |
| tune_60min | 1 | 1628 | 219 | 1847 | 1628.0 | 219.0 | 0.0 | 0 | 1 | 0 |

## Input Issues

- prompt_input_tokens_ge_12000: 4
- prompt_input_tokens_ge_8000: 3

## Output Issues

- response_fenced_json: 15
- response_has_preamble_or_wrapper: 15
- response_not_strict_json: 15
- duplicate_candidate_action: 2
- duplicate_watchlist_ticker: 1
- candidate_actions_not_one_per_watchlist: 1

## Recommendations

- P1 output_contract: Tighten JSON-only enforcement for affected labels or route non-strict responses through a bounded retry; parser recovery should remain fail-safe.
- P2 selection_schema: Add a compact-schema self-check or post-parse warning for duplicate watchlist entries and candidate-action coverage gaps.
- P2 token_cost: Reduce high-token prompts by trimming repeated calibration blocks and limiting evidence pack rows before model invocation; review labels with 8k+ input tokens even when the session average looks acceptable.

## Issue Samples

- 2026-06-01T22:41:18+09:00 hold_advisor_challenge input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_hold_advisor_challenge_224118358377_efbb17d08e.json
- 2026-06-01T23:07:01+09:00 hold_advisor_challenge input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_hold_advisor_challenge_230701217366_6d9b6116d3.json
- 2026-06-01T23:07:30+09:00 hold_advisor_challenge input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_hold_advisor_challenge_230730483021_ccf7488cf4.json
- 2026-06-01T22:32:15+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_hold_advisor_triage_223215504466_93c57c4bb8.json
- 2026-06-01T22:41:07+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_hold_advisor_triage_224107658782_feec3ac1e6.json
- 2026-06-01T23:06:10+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_hold_advisor_triage_230610475882_249cccc373.json
- 2026-06-01T23:06:23+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_hold_advisor_triage_230623132482_d72b1a5c0a.json
- 2026-06-01T23:06:48+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_hold_advisor_triage_230648721973_43f664accd.json
- 2026-06-01T23:07:15+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_hold_advisor_triage_230715984097_1eb2dca8ff.json
- 2026-06-01T23:07:55+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_hold_advisor_triage_230755549047_fd95a678b4.json
- 2026-06-01T23:32:13+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_hold_advisor_triage_233213909730_56b1087512.json
- 2026-06-01T22:35:49+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_select_tickers_223549265559_93a82c8add.json
- 2026-06-01T22:36:33+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=[] path=logs\raw_calls\20260601_US_select_tickers_223633986190_0e770481aa.json
- 2026-06-01T22:40:47+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=[] path=logs\raw_calls\20260601_US_select_tickers_224047537688_6844d63c4a.json
- 2026-06-01T22:56:51+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['duplicate_candidate_action', 'duplicate_watchlist_ticker', 'response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_select_tickers_225651877435_9aac36f802.json
- 2026-06-01T23:15:37+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_select_tickers_231537596887_805d7ac836.json
- 2026-06-01T23:22:40+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_select_tickers_232240535354_9df50b8770.json
- 2026-06-01T23:40:01+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['candidate_actions_not_one_per_watchlist', 'duplicate_candidate_action'] path=logs\raw_calls\20260601_US_select_tickers_234001616777_be624c5a9c.json
