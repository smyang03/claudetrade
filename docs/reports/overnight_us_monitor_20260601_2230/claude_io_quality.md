# Claude I/O Quality Report

- generated_at: 2026-06-02T07:01:05+09:00
- scope: US 2026-06-01T22:30:00+09:00 ~ 2026-06-02T07:00:00+09:00
- raw_calls: 113
- tokens: input=435092 output=75870 total=510962
- averages: input=3850.4 output=671.4 duration_ms=14753.1
- duration_coverage: observed=63 missing=50
- parse_errors: 0

## Calls By Label

| label | calls | input | output | total | avg input | avg output | avg ms | ms observed | ms missing | parse errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| select_tickers | 16 | 189808 | 26856 | 216664 | 11863.0 | 1678.5 | 24175.7 | 7 | 9 | 0 |
| hold_advisor_triage | 42 | 61522 | 30406 | 91928 | 1464.8 | 724.0 | 14269.3 | 42 | 0 | 0 |
| analyst_bear_r1 | 5 | 34343 | 1118 | 35461 | 6868.6 | 223.6 | 0.0 | 0 | 5 | 0 |
| analyst_bull_r1 | 5 | 33768 | 1154 | 34922 | 6753.6 | 230.8 | 0.0 | 0 | 5 | 0 |
| analyst_neutral_r1 | 5 | 33728 | 921 | 34649 | 6745.6 | 184.2 | 0.0 | 0 | 5 | 0 |
| analyst_bear_r2 | 5 | 12783 | 1235 | 14018 | 2556.6 | 247.0 | 0.0 | 0 | 5 | 0 |
| analyst_bull_r2 | 5 | 12208 | 1349 | 13557 | 2441.6 | 269.8 | 0.0 | 0 | 5 | 0 |
| analyst_neutral_r2 | 5 | 12168 | 1207 | 13375 | 2433.6 | 241.4 | 0.0 | 0 | 5 | 0 |
| postmortem | 1 | 7858 | 2500 | 10358 | 7858.0 | 2500.0 | 47750.0 | 1 | 0 | 0 |
| tune_30min | 4 | 7026 | 959 | 7985 | 1756.5 | 239.8 | 0.0 | 0 | 4 | 0 |
| hold_advisor_challenge | 4 | 5471 | 2234 | 7705 | 1367.8 | 558.5 | 12739.0 | 4 | 0 | 0 |
| hold_advisor_bear | 3 | 4224 | 1184 | 5408 | 1408.0 | 394.7 | 7245.0 | 3 | 0 | 0 |
| hold_advisor_neutral | 3 | 4206 | 1130 | 5336 | 1402.0 | 376.7 | 6816.3 | 3 | 0 | 0 |
| hold_advisor_bull | 3 | 4218 | 1075 | 5293 | 1406.0 | 358.3 | 6671.7 | 3 | 0 | 0 |
| param_tuner | 2 | 2922 | 1350 | 4272 | 1461.0 | 675.0 | 0.0 | 0 | 2 | 0 |
| tune_60min | 2 | 3468 | 413 | 3881 | 1734.0 | 206.5 | 0.0 | 0 | 2 | 0 |
| tune_120min | 1 | 1791 | 303 | 2094 | 1791.0 | 303.0 | 0.0 | 0 | 1 | 0 |
| tune_90min | 1 | 1840 | 202 | 2042 | 1840.0 | 202.0 | 0.0 | 0 | 1 | 0 |
| tune_150min | 1 | 1740 | 274 | 2014 | 1740.0 | 274.0 | 0.0 | 0 | 1 | 0 |

## Usage Timeline

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

## Input Issues

- prompt_input_tokens_ge_12000: 11
- prompt_input_tokens_ge_8000: 5

## Output Issues

- response_fenced_json: 61
- response_has_preamble_or_wrapper: 61
- response_not_strict_json: 61
- duplicate_candidate_action: 2
- output_tokens_ge_2000: 1
- slow_call_30s: 1
- duplicate_watchlist_ticker: 1
- candidate_actions_not_one_per_watchlist: 1
- trade_ready_action_not_buy_or_probe: 1

## Recommendations

- P1 output_contract: Tighten JSON-only enforcement for affected labels or route non-strict responses through a bounded retry; parser recovery should remain fail-safe.
- P2 selection_schema: Add a compact-schema self-check or post-parse warning for duplicate watchlist entries and candidate-action coverage gaps.
- P2 latency_cost: Separate timeout-prone labels from normal review flow and keep cache/cooldown guards active for repeated HOLD reviews.
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
- 2026-06-01T23:42:32+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_hold_advisor_triage_234232327304_339a1119c2.json
- 2026-06-02T05:03:35+09:00 postmortem input=[] output=['output_tokens_ge_2000', 'response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json', 'slow_call_30s'] path=logs\raw_calls\20260601_US_postmortem_postmortem_US_2026-06-01_d35af0fb6f.json
- 2026-06-01T22:35:49+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_select_tickers_223549265559_93a82c8add.json
- 2026-06-01T22:36:33+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=[] path=logs\raw_calls\20260601_US_select_tickers_223633986190_0e770481aa.json
- 2026-06-01T22:40:47+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=[] path=logs\raw_calls\20260601_US_select_tickers_224047537688_6844d63c4a.json
- 2026-06-01T22:56:51+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['duplicate_candidate_action', 'duplicate_watchlist_ticker', 'response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_select_tickers_225651877435_9aac36f802.json
- 2026-06-01T23:15:37+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_select_tickers_231537596887_805d7ac836.json
- 2026-06-01T23:22:40+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260601_US_select_tickers_232240535354_9df50b8770.json
- 2026-06-01T23:40:01+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['candidate_actions_not_one_per_watchlist', 'duplicate_candidate_action'] path=logs\raw_calls\20260601_US_select_tickers_234001616777_be624c5a9c.json
- 2026-06-01T23:47:05+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260601_US_select_tickers_234705841825_a73d8742d2.json
- 2026-06-02T00:28:14+09:00 hold_advisor_bear input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_bear_002814252933_ee813d026a.json
- 2026-06-02T00:50:26+09:00 hold_advisor_bear input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_bear_005026449865_c3c9b6e0dc.json
- 2026-06-02T00:50:47+09:00 hold_advisor_bear input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_bear_005047263917_5d56995dc5.json
- 2026-06-02T00:28:06+09:00 hold_advisor_bull input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_bull_002806694410_e320dd3187.json
- 2026-06-02T00:50:19+09:00 hold_advisor_bull input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_bull_005019397981_8a62a2fb5e.json
- 2026-06-02T00:50:38+09:00 hold_advisor_bull input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_bull_005038554645_14e3d2aaad.json
- 2026-06-02T01:01:07+09:00 hold_advisor_challenge input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_challenge_010107504988_e1430d5dac.json
- 2026-06-02T00:28:21+09:00 hold_advisor_neutral input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_neutral_002821766256_14126f1f73.json
- 2026-06-02T00:50:33+09:00 hold_advisor_neutral input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_neutral_005033507785_4afc12b09c.json

## Prompt Warning Samples

| time | label | input | chars | candidates | evidence requested | evidence pack | lessons chars | top sections | path |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 2026-06-01T23:15:37+09:00 | select_tickers | 12207 | 26564 | 35 | 28 | 5 | 199 | candidates:14578, runtime_evidence:3562, digest_news:2349, output_contract:1354 | logs\raw_calls\20260601_US_select_tickers_231537596887_805d7ac836.json |
| 2026-06-02T00:17:01+09:00 | select_tickers | 12201 | 26561 | 35 | 28 | 5 | 199 | candidates:14558, runtime_evidence:3566, digest_news:2362, output_contract:1354 | logs\raw_calls\20260602_US_select_tickers_001701587242_bb0b2458bc.json |
| 2026-06-01T23:22:40+09:00 | select_tickers | 12177 | 26560 | 35 | 28 | 5 | 199 | candidates:14551, runtime_evidence:3573, digest_news:2361, output_contract:1354 | logs\raw_calls\20260601_US_select_tickers_232240535354_9df50b8770.json |
| 2026-06-01T23:40:01+09:00 | select_tickers | 12173 | 26531 | 35 | 28 | 5 | 199 | candidates:14535, runtime_evidence:3573, digest_news:2348, output_contract:1354 | logs\raw_calls\20260601_US_select_tickers_234001616777_be624c5a9c.json |
| 2026-06-01T23:47:05+09:00 | select_tickers | 12168 | 26526 | 35 | 28 | 5 | 199 | candidates:14551, runtime_evidence:3552, digest_news:2348, output_contract:1354 | logs\raw_calls\20260601_US_select_tickers_234705841825_a73d8742d2.json |
| 2026-06-02T02:34:23+09:00 | select_tickers | 12132 | 26497 | 35 | 28 | 5 | 199 | candidates:14466, runtime_evidence:3582, digest_news:2374, output_contract:1354 | logs\raw_calls\20260602_US_select_tickers_023423921136_66e00acf02.json |
| 2026-06-02T01:54:06+09:00 | select_tickers | 12124 | 26504 | 35 | 28 | 5 | 199 | candidates:14489, runtime_evidence:3578, digest_news:2362, output_contract:1354 | logs\raw_calls\20260602_US_select_tickers_015406187108_9bc8825af8.json |
| 2026-06-02T00:59:02+09:00 | select_tickers | 12120 | 26467 | 35 | 28 | 5 | 199 | candidates:14463, runtime_evidence:3555, digest_news:2374, output_contract:1354 | logs\raw_calls\20260602_US_select_tickers_005902872170_95be3921f4.json |
| 2026-06-02T01:24:13+09:00 | select_tickers | 12115 | 26487 | 35 | 28 | 5 | 199 | candidates:14495, runtime_evidence:3555, digest_news:2362, output_contract:1354 | logs\raw_calls\20260602_US_select_tickers_012413411746_f4c43b45d4.json |
| 2026-06-02T04:07:55+09:00 | select_tickers | 12061 | 26275 | 35 | 28 | 5 | 199 | candidates:14362, runtime_evidence:3463, digest_news:2375, output_contract:1354 | logs\raw_calls\20260602_US_select_tickers_040755223717_64310bb0d1.json |
| 2026-06-01T22:56:51+09:00 | select_tickers | 12031 | 26377 | 35 | 28 | 5 | 199 | candidates:14429, runtime_evidence:3522, digest_news:2349, output_contract:1354 | logs\raw_calls\20260601_US_select_tickers_225651877435_9aac36f802.json |
| 2026-06-01T22:36:33+09:00 | select_tickers | 11956 | 26586 | 35 | 30 | 5 | 199 | candidates:14577, runtime_evidence:3569, digest_news:2363, output_contract:1354 | logs\raw_calls\20260601_US_select_tickers_223633986190_0e770481aa.json |
| 2026-06-01T22:35:49+09:00 | select_tickers | 11751 | 26145 | 35 | 30 | 5 | 199 | candidates:14152, runtime_evidence:3553, digest_news:2363, output_contract:1354 | logs\raw_calls\20260601_US_select_tickers_223549265559_93a82c8add.json |
| 2026-06-02T04:37:47+09:00 | select_tickers | 10925 | 22823 | 35 | 28 | 5 | 199 | candidates:14457, digest_news:2363, output_contract:1354, rules:977 | logs\raw_calls\20260602_US_select_tickers_043747812053_b8605e2f57.json |
| 2026-06-02T03:36:18+09:00 | select_tickers | 10893 | 22770 | 35 | 28 | 5 | 199 | candidates:14394, digest_news:2373, output_contract:1354, rules:977 | logs\raw_calls\20260602_US_select_tickers_033618077800_c3eaceca58.json |
| 2026-06-01T22:40:47+09:00 | select_tickers | 10774 | 22978 | 35 | 30 | 5 | 199 | candidates:14609, digest_news:2364, output_contract:1354, rules:977 | logs\raw_calls\20260601_US_select_tickers_224047537688_6844d63c4a.json |

## Slow Calls

- 2026-06-02T05:03:35+09:00 postmortem duration_ms=47750 path=logs\raw_calls\20260601_US_postmortem_postmortem_US_2026-06-01_d35af0fb6f.json
- 2026-06-02T01:17:49+09:00 hold_advisor_triage duration_ms=15333 path=logs\raw_calls\20260602_US_hold_advisor_triage_011749226380_c48b0279c7.json
- 2026-06-02T02:04:08+09:00 hold_advisor_triage duration_ms=18833 path=logs\raw_calls\20260602_US_hold_advisor_triage_020408656326_d5b144d0d7.json
- 2026-06-02T02:36:16+09:00 hold_advisor_triage duration_ms=15608 path=logs\raw_calls\20260602_US_hold_advisor_triage_023616235674_5f75cd973e.json
- 2026-06-02T02:45:37+09:00 hold_advisor_triage duration_ms=15762 path=logs\raw_calls\20260602_US_hold_advisor_triage_024537115836_cd4ee41a89.json
- 2026-06-02T03:01:21+09:00 hold_advisor_triage duration_ms=15689 path=logs\raw_calls\20260602_US_hold_advisor_triage_030121895138_7463ccefb6.json
- 2026-06-02T03:04:01+09:00 hold_advisor_triage duration_ms=16584 path=logs\raw_calls\20260602_US_hold_advisor_triage_030401863332_caf82941dc.json
- 2026-06-02T03:04:39+09:00 hold_advisor_triage duration_ms=15587 path=logs\raw_calls\20260602_US_hold_advisor_triage_030439068683_8f4139c79e.json
- 2026-06-02T03:06:48+09:00 hold_advisor_triage duration_ms=21904 path=logs\raw_calls\20260602_US_hold_advisor_triage_030648356106_9c34c204d5.json
- 2026-06-02T03:08:13+09:00 hold_advisor_triage duration_ms=18499 path=logs\raw_calls\20260602_US_hold_advisor_triage_030813852947_df2850fd51.json
- 2026-06-02T03:38:55+09:00 hold_advisor_triage duration_ms=17590 path=logs\raw_calls\20260602_US_hold_advisor_triage_033855004595_b4db35f19c.json
- 2026-06-02T03:44:15+09:00 hold_advisor_triage duration_ms=15260 path=logs\raw_calls\20260602_US_hold_advisor_triage_034415280771_3a966e0965.json
- 2026-06-02T03:52:05+09:00 hold_advisor_triage duration_ms=16347 path=logs\raw_calls\20260602_US_hold_advisor_triage_035205449669_f9a9b8e2ee.json
- 2026-06-02T03:57:33+09:00 hold_advisor_triage duration_ms=15728 path=logs\raw_calls\20260602_US_hold_advisor_triage_035733267693_22576091d2.json
- 2026-06-02T04:05:56+09:00 hold_advisor_triage duration_ms=16546 path=logs\raw_calls\20260602_US_hold_advisor_triage_040556122763_f58daa4e0c.json
- 2026-06-02T04:06:31+09:00 hold_advisor_triage duration_ms=15320 path=logs\raw_calls\20260602_US_hold_advisor_triage_040631585763_45a2b14f73.json
- 2026-06-02T04:15:06+09:00 hold_advisor_triage duration_ms=16985 path=logs\raw_calls\20260602_US_hold_advisor_triage_041506909851_0fe0ec4009.json
- 2026-06-02T04:29:27+09:00 hold_advisor_triage duration_ms=16264 path=logs\raw_calls\20260602_US_hold_advisor_triage_042927405411_59606cd57a.json
- 2026-06-02T04:45:55+09:00 hold_advisor_triage duration_ms=15111 path=logs\raw_calls\20260602_US_hold_advisor_triage_044555063132_fa4483b556.json
- 2026-06-02T04:50:33+09:00 hold_advisor_triage duration_ms=15179 path=logs\raw_calls\20260602_US_hold_advisor_triage_045033505053_3ac61e7ab4.json
