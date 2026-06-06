# Claude I/O Quality Report

- generated_at: 2026-06-07T02:45:22+09:00
- scope: US 2026-06-05T00:00:00+09:00 ~ 2026-06-06T00:00:00+09:00
- raw_calls: 63
- tokens: input=248624 output=37665 total=286289
- averages: input=3946.4 output=597.9 duration_ms=12182.1
- duration_coverage: observed=50 missing=13
- parse_errors: 2

## Calls By Label

| label | calls | input | output | total | avg input | avg output | avg ms | ms observed | ms missing | parse errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| select_tickers | 10 | 117366 | 16399 | 133765 | 11736.6 | 1639.9 | 22696.8 | 10 | 0 | 0 |
| hold_advisor_triage | 19 | 31126 | 6432 | 37558 | 1638.2 | 338.5 | 7307.3 | 19 | 0 | 0 |
| analyst_bear_r1 | 2 | 12594 | 692 | 13286 | 6297.0 | 346.0 | 9237.0 | 2 | 0 | 0 |
| analyst_neutral_r1 | 2 | 12320 | 437 | 12757 | 6160.0 | 218.5 | 6446.0 | 2 | 0 | 0 |
| analyst_bull_r1 | 2 | 12088 | 582 | 12670 | 6044.0 | 291.0 | 8543.5 | 2 | 0 | 0 |
| postmortem | 1 | 9306 | 2500 | 11806 | 9306.0 | 2500.0 | 48547.0 | 1 | 0 | 0 |
| analyst_bear_r2 | 2 | 6355 | 881 | 7236 | 3177.5 | 440.5 | 8713.0 | 2 | 0 | 0 |
| analyst_neutral_r2 | 2 | 6167 | 775 | 6942 | 3083.5 | 387.5 | 9743.5 | 2 | 0 | 0 |
| analyst_bull_r2 | 2 | 5935 | 776 | 6711 | 2967.5 | 388.0 | 7508.0 | 2 | 0 | 0 |
| hold_advisor_challenge | 3 | 4869 | 635 | 5504 | 1623.0 | 211.7 | 4948.7 | 3 | 0 | 0 |
| preopen_continuation_blind_eval_30m | 1 | 2715 | 580 | 3295 | 2715.0 | 580.0 | 15098.0 | 1 | 0 | 0 |
| preopen_continuation_blind_eval_5m | 1 | 2266 | 900 | 3166 | 2266.0 | 900.0 | 19635.0 | 1 | 0 | 1 |
| preopen_continuation_blind_eval_retry | 1 | 2174 | 723 | 2897 | 2174.0 | 723.0 | 17057.0 | 1 | 0 | 0 |
| preopen_continuation_blind_eval_preopen | 1 | 1443 | 619 | 2062 | 1443.0 | 619.0 | 14619.0 | 1 | 0 | 0 |
| tune_240min | 1 | 1655 | 379 | 2034 | 1655.0 | 379.0 | 0.0 | 0 | 1 | 0 |
| tune_300min | 1 | 1655 | 365 | 2020 | 1655.0 | 365.0 | 0.0 | 0 | 1 | 0 |
| tune_150min | 1 | 1602 | 402 | 2004 | 1602.0 | 402.0 | 0.0 | 0 | 1 | 0 |
| param_tuner | 1 | 1364 | 637 | 2001 | 1364.0 | 637.0 | 0.0 | 0 | 1 | 0 |
| tune_210min | 1 | 1654 | 335 | 1989 | 1654.0 | 335.0 | 0.0 | 0 | 1 | 0 |
| tune_330min | 1 | 1605 | 352 | 1957 | 1605.0 | 352.0 | 0.0 | 0 | 1 | 0 |
| tune_270min | 1 | 1655 | 284 | 1939 | 1655.0 | 284.0 | 0.0 | 0 | 1 | 0 |
| tune_60min | 1 | 1639 | 295 | 1934 | 1639.0 | 295.0 | 0.0 | 0 | 1 | 0 |
| tune_180min | 1 | 1654 | 267 | 1921 | 1654.0 | 267.0 | 0.0 | 0 | 1 | 0 |
| tune_360min | 1 | 1565 | 338 | 1903 | 1565.0 | 338.0 | 0.0 | 0 | 1 | 0 |
| tune_390min | 1 | 1565 | 271 | 1836 | 1565.0 | 271.0 | 0.0 | 0 | 1 | 0 |
| tune_420min | 1 | 1567 | 251 | 1818 | 1567.0 | 251.0 | 0.0 | 0 | 1 | 0 |
| tune_30min | 1 | 1477 | 258 | 1735 | 1477.0 | 258.0 | 0.0 | 0 | 1 | 0 |
| preopen_continuation_blind_eval_5m_small | 1 | 1243 | 300 | 1543 | 1243.0 | 300.0 | 13113.0 | 1 | 0 | 1 |

## Usage Timeline

| bucket start | calls | input | output | total | avg input | top labels | input issues | output issues |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| 2026-06-05T00:00+09:00 | 9 | 26828 | 5720 | 32548 | 2980.9 | {"hold_advisor_triage": 2, "preopen_continuation_blind_eval_30m": 1, "preopen_continuation_blind_eval_5m": 1, "preopen_continuation_blind_eval_5m_small": 1, "preopen_continuation_blind_eval_preopen": 1} | {"prompt_input_tokens_ge_12000": 1} | {"parse_error": 2} |
| 2026-06-05T00:30+09:00 | 2 | 3296 | 631 | 3927 | 1648.0 | {"hold_advisor_triage": 1, "tune_180min": 1} | {} | {} |
| 2026-06-05T01:00+09:00 | 4 | 17016 | 2540 | 19556 | 4254.0 | {"hold_advisor_triage": 2, "select_tickers": 1, "tune_210min": 1} | {"prompt_input_tokens_ge_12000": 1} | {} |
| 2026-06-05T01:30+09:00 | 3 | 15394 | 2207 | 17601 | 5131.3 | {"hold_advisor_triage": 1, "select_tickers": 1, "tune_240min": 1} | {"prompt_input_tokens_ge_12000": 1} | {} |
| 2026-06-05T02:00+09:00 | 4 | 17019 | 2738 | 19757 | 4254.8 | {"hold_advisor_triage": 2, "select_tickers": 1, "tune_270min": 1} | {"prompt_input_tokens_ge_12000": 1} | {} |
| 2026-06-05T02:30+09:00 | 3 | 4920 | 1051 | 5971 | 1640.0 | {"hold_advisor_triage": 2, "tune_300min": 1} | {} | {} |
| 2026-06-05T03:00+09:00 | 5 | 18760 | 2957 | 21717 | 3752.0 | {"hold_advisor_triage": 3, "select_tickers": 1, "tune_330min": 1} | {"prompt_input_tokens_ge_12000": 1} | {} |
| 2026-06-05T03:30+09:00 | 5 | 17644 | 2802 | 20446 | 3528.8 | {"hold_advisor_challenge": 1, "hold_advisor_triage": 2, "select_tickers": 1, "tune_360min": 1} | {"prompt_input_tokens_ge_8000": 1} | {} |
| 2026-06-05T04:00+09:00 | 2 | 13727 | 1797 | 15524 | 6863.5 | {"select_tickers": 1, "tune_390min": 1} | {"prompt_input_tokens_ge_12000": 1} | {} |
| 2026-06-05T04:30+09:00 | 1 | 1567 | 251 | 1818 | 1567.0 | {"tune_420min": 1} | {} | {} |
| 2026-06-05T05:00+09:00 | 1 | 9306 | 2500 | 11806 | 9306.0 | {"postmortem": 1} | {"prompt_input_tokens_ge_8000": 1} | {"output_tokens_ge_2000": 1, "response_fenced_json": 1, "response_has_preamble_or_wrapper": 1, "response_not_strict_json": 1, "slow_call_30s": 1} |
| 2026-06-05T22:30+09:00 | 8 | 38245 | 4440 | 42685 | 4780.6 | {"analyst_bear_r1": 1, "analyst_bear_r2": 1, "analyst_bull_r1": 1, "analyst_bull_r2": 1, "analyst_neutral_r1": 1} | {"prompt_input_tokens_ge_8000": 1} | {} |
| 2026-06-05T23:00+09:00 | 9 | 53627 | 5863 | 59490 | 5958.6 | {"analyst_bear_r1": 1, "analyst_bear_r2": 1, "analyst_bull_r1": 1, "analyst_bull_r2": 1, "select_tickers": 2} | {"prompt_input_tokens_ge_12000": 2} | {"trade_ready_action_not_buy_or_probe": 1} |
| 2026-06-05T23:30+09:00 | 7 | 11275 | 2168 | 13443 | 1610.7 | {"hold_advisor_challenge": 2, "hold_advisor_triage": 4, "tune_60min": 1} | {} | {} |

## Input Issues

- prompt_input_tokens_ge_12000: 8
- prompt_input_tokens_ge_8000: 3

## Output Issues

- parse_error: 2
- output_tokens_ge_2000: 1
- response_fenced_json: 1
- response_has_preamble_or_wrapper: 1
- response_not_strict_json: 1
- slow_call_30s: 1
- trade_ready_action_not_buy_or_probe: 1

## Recommendations

- P1 output_contract: Tighten JSON-only enforcement for affected labels or route non-strict responses through a bounded retry; parser recovery should remain fail-safe.
- P1 parser_safety: Review parse-error samples and confirm fallback decisions cannot create BUY/SELL authority without runtime gates.
- P2 latency_cost: Separate timeout-prone labels from normal review flow and keep cache/cooldown guards active for repeated HOLD reviews.
- P2 token_cost: Reduce high-token prompts by trimming repeated calibration blocks and limiting evidence pack rows before model invocation; review labels with 8k+ input tokens even when the session average looks acceptable.

## Issue Samples

- 2026-06-05T05:00:52+09:00 postmortem input=['prompt_input_tokens_ge_8000'] output=['output_tokens_ge_2000', 'response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json', 'slow_call_30s'] path=logs\raw_calls\20260604_US_postmortem_postmortem_US_2026-06-04_946033d76b.json
- 2026-06-05T00:23:29+09:00 preopen_continuation_blind_eval_5m input=[] output=['parse_error'] path=logs\raw_calls\20260605_US_preopen_continuation_blind_eval_5m_002329788922_70ef41d085.json
- 2026-06-05T00:25:06+09:00 preopen_continuation_blind_eval_5m_small input=[] output=['parse_error'] path=logs\raw_calls\20260605_US_preopen_continuation_blind_eval_5m_small_002506556614_cd79c4d2a0.json
- 2026-06-05T00:25:17+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260605_US_select_tickers_002517047284_3d54b58a5b.json
- 2026-06-05T01:25:55+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260605_US_select_tickers_012555851234_9717ab6b9b.json
- 2026-06-05T01:57:32+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260605_US_select_tickers_015732316499_6c0090c21d.json
- 2026-06-05T02:26:37+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260605_US_select_tickers_022637677845_6306831b59.json
- 2026-06-05T03:27:22+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260605_US_select_tickers_032722847713_35a6ec8d72.json
- 2026-06-05T03:58:45+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=[] path=logs\raw_calls\20260605_US_select_tickers_035845016205_fe9d77120c.json
- 2026-06-05T04:28:04+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260605_US_select_tickers_042804740256_569d91ec4c.json
- 2026-06-05T22:37:28+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=[] path=logs\raw_calls\20260605_US_select_tickers_223728318963_2f672d1fdd.json
- 2026-06-05T23:05:30+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['trade_ready_action_not_buy_or_probe'] path=logs\raw_calls\20260605_US_select_tickers_230530478695_de47961049.json
- 2026-06-05T23:07:12+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260605_US_select_tickers_230712273133_aada2209f4.json

## Prompt Warning Samples

| time | label | input | chars | candidates | evidence requested | evidence pack | lessons chars | top sections | path |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 2026-06-05T23:05:30+09:00 | select_tickers | 12287 | 27066 | 35 | 28 | 5 | 199 | candidates:14656, runtime_evidence:3517, digest_news:2450, output_contract:1569 | logs\raw_calls\20260605_US_select_tickers_230530478695_de47961049.json |
| 2026-06-05T23:07:12+09:00 | select_tickers | 12252 | 26951 | 35 | 28 | 5 | 199 | candidates:14522, runtime_evidence:3528, digest_news:2459, output_contract:1569 | logs\raw_calls\20260605_US_select_tickers_230712273133_aada2209f4.json |
| 2026-06-05T03:27:22+09:00 | select_tickers | 12176 | 26580 | 35 | 28 | 5 | 199 | candidates:14357, runtime_evidence:3569, digest_news:2210, output_contract:1569 | logs\raw_calls\20260605_US_select_tickers_032722847713_35a6ec8d72.json |
| 2026-06-05T04:28:04+09:00 | select_tickers | 12162 | 26608 | 35 | 28 | 5 | 199 | candidates:14394, runtime_evidence:3577, digest_news:2193, output_contract:1569 | logs\raw_calls\20260605_US_select_tickers_042804740256_569d91ec4c.json |
| 2026-06-05T02:26:37+09:00 | select_tickers | 12140 | 26587 | 35 | 28 | 5 | 199 | candidates:14371, runtime_evidence:3549, digest_news:2223, output_contract:1569 | logs\raw_calls\20260605_US_select_tickers_022637677845_6306831b59.json |
| 2026-06-05T00:25:17+09:00 | select_tickers | 12139 | 26566 | 35 | 28 | 5 | 199 | candidates:14435, runtime_evidence:3477, digest_news:2210, output_contract:1569 | logs\raw_calls\20260605_US_select_tickers_002517047284_3d54b58a5b.json |
| 2026-06-05T01:57:32+09:00 | select_tickers | 12112 | 26488 | 35 | 28 | 5 | 199 | candidates:14379, runtime_evidence:3442, digest_news:2223, output_contract:1569 | logs\raw_calls\20260605_US_select_tickers_015732316499_6c0090c21d.json |
| 2026-06-05T01:25:55+09:00 | select_tickers | 12094 | 26446 | 35 | 28 | 5 | 199 | candidates:14341, runtime_evidence:3438, digest_news:2223, output_contract:1569 | logs\raw_calls\20260605_US_select_tickers_012555851234_9717ab6b9b.json |
| 2026-06-05T03:58:45+09:00 | select_tickers | 10971 | 22988 | 35 | 28 | 5 | 199 | candidates:14424, digest_news:2192, output_contract:1569, rules:1131 | logs\raw_calls\20260605_US_select_tickers_035845016205_fe9d77120c.json |
| 2026-06-05T05:00:52+09:00 | postmortem | 9306 | 14209 | 0 | 0 | 0 | 0 | header:14209 | logs\raw_calls\20260604_US_postmortem_postmortem_US_2026-06-04_946033d76b.json |
| 2026-06-05T22:37:28+09:00 | select_tickers | 9033 | 21185 | 22 | 22 | 5 | 199 | candidates:8787, runtime_evidence:3493, digest_news:2460, output_contract:1569 | logs\raw_calls\20260605_US_select_tickers_223728318963_2f672d1fdd.json |

## Slow Calls

- 2026-06-05T05:00:52+09:00 postmortem duration_ms=48547 path=logs\raw_calls\20260604_US_postmortem_postmortem_US_2026-06-04_946033d76b.json
- 2026-06-05T00:23:44+09:00 preopen_continuation_blind_eval_30m duration_ms=15098 path=logs\raw_calls\20260605_US_preopen_continuation_blind_eval_30m_002344923773_156ae979a1.json
- 2026-06-05T00:23:29+09:00 preopen_continuation_blind_eval_5m duration_ms=19635 path=logs\raw_calls\20260605_US_preopen_continuation_blind_eval_5m_002329788922_70ef41d085.json
- 2026-06-05T00:18:37+09:00 preopen_continuation_blind_eval_retry duration_ms=17057 path=logs\raw_calls\20260605_US_preopen_continuation_blind_eval_retry_001837983158_c94342fee8.json
- 2026-06-05T00:25:17+09:00 select_tickers duration_ms=22697 path=logs\raw_calls\20260605_US_select_tickers_002517047284_3d54b58a5b.json
- 2026-06-05T01:25:55+09:00 select_tickers duration_ms=20308 path=logs\raw_calls\20260605_US_select_tickers_012555851234_9717ab6b9b.json
- 2026-06-05T01:57:32+09:00 select_tickers duration_ms=20740 path=logs\raw_calls\20260605_US_select_tickers_015732316499_6c0090c21d.json
- 2026-06-05T02:26:37+09:00 select_tickers duration_ms=24227 path=logs\raw_calls\20260605_US_select_tickers_022637677845_6306831b59.json
- 2026-06-05T03:27:22+09:00 select_tickers duration_ms=25061 path=logs\raw_calls\20260605_US_select_tickers_032722847713_35a6ec8d72.json
- 2026-06-05T03:58:45+09:00 select_tickers duration_ms=24536 path=logs\raw_calls\20260605_US_select_tickers_035845016205_fe9d77120c.json
- 2026-06-05T04:28:04+09:00 select_tickers duration_ms=21556 path=logs\raw_calls\20260605_US_select_tickers_042804740256_569d91ec4c.json
- 2026-06-05T22:37:28+09:00 select_tickers duration_ms=20618 path=logs\raw_calls\20260605_US_select_tickers_223728318963_2f672d1fdd.json
- 2026-06-05T23:05:30+09:00 select_tickers duration_ms=23110 path=logs\raw_calls\20260605_US_select_tickers_230530478695_de47961049.json
- 2026-06-05T23:07:12+09:00 select_tickers duration_ms=24115 path=logs\raw_calls\20260605_US_select_tickers_230712273133_aada2209f4.json
