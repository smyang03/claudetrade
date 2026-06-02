# Claude I/O Quality Report

- generated_at: 2026-06-01T23:34:29+09:00
- scope: US 2026-06-01T22:30:00+09:00 ~ 2026-06-02T07:00:00+09:00
- raw_calls: 44
- tokens: input=199941 output=22393 total=222334
- averages: input=4544.1 output=508.9 duration_ms=11804.1
- duration_coverage: observed=11 missing=33
- parse_errors: 0

## Calls By Label

| label | calls | input | output | total | avg input | avg output | avg ms | ms observed | ms missing | parse errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| select_tickers | 6 | 70896 | 9718 | 80614 | 11816.0 | 1619.7 | 0.0 | 0 | 6 | 0 |
| analyst_bear_r1 | 4 | 27478 | 891 | 28369 | 6869.5 | 222.8 | 0.0 | 0 | 4 | 0 |
| analyst_bull_r1 | 4 | 27018 | 896 | 27914 | 6754.5 | 224.0 | 0.0 | 0 | 4 | 0 |
| analyst_neutral_r1 | 4 | 26986 | 731 | 27717 | 6746.5 | 182.8 | 0.0 | 0 | 4 | 0 |
| hold_advisor_triage | 8 | 9342 | 4402 | 13744 | 1167.8 | 550.2 | 11525.8 | 8 | 0 | 0 |
| analyst_bear_r2 | 4 | 10190 | 954 | 11144 | 2547.5 | 238.5 | 0.0 | 0 | 4 | 0 |
| analyst_bull_r2 | 4 | 9730 | 1067 | 10797 | 2432.5 | 266.8 | 0.0 | 0 | 4 | 0 |
| analyst_neutral_r2 | 4 | 9698 | 948 | 10646 | 2424.5 | 237.0 | 0.0 | 0 | 4 | 0 |
| hold_advisor_challenge | 3 | 3801 | 1659 | 5460 | 1267.0 | 553.0 | 12546.3 | 3 | 0 | 0 |
| param_tuner | 1 | 1461 | 677 | 2138 | 1461.0 | 677.0 | 0.0 | 0 | 1 | 0 |
| tune_30min | 1 | 1713 | 231 | 1944 | 1713.0 | 231.0 | 0.0 | 0 | 1 | 0 |
| tune_60min | 1 | 1628 | 219 | 1847 | 1628.0 | 219.0 | 0.0 | 0 | 1 | 0 |

## Input Issues

- prompt_input_tokens_ge_8000: 3
- prompt_input_tokens_ge_12000: 3

## Output Issues

- response_fenced_json: 15
- response_has_preamble_or_wrapper: 15
- response_not_strict_json: 15
- duplicate_candidate_action: 1
- duplicate_watchlist_ticker: 1

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
