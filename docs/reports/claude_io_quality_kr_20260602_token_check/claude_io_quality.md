# Claude I/O Quality Report

- generated_at: 2026-06-02T20:51:11+09:00
- scope: KR 2026-06-02T00:00:00+09:00 ~ 2026-06-02T23:59:59+09:00
- raw_calls: 104
- tokens: input=567885 output=57898 total=625783
- averages: input=5460.4 output=556.7 duration_ms=10921.6
- duration_coverage: observed=88 missing=16
- parse_errors: 0

## Calls By Label

| label | calls | input | output | total | avg input | avg output | avg ms | ms observed | ms missing | parse errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| select_tickers | 21 | 263045 | 32111 | 295156 | 12526.0 | 1529.1 | 22650.3 | 21 | 0 | 0 |
| analyst_bear_r1 | 11 | 62696 | 2804 | 65500 | 5699.6 | 254.9 | 7900.8 | 11 | 0 | 0 |
| analyst_bull_r1 | 11 | 60875 | 2863 | 63738 | 5534.1 | 260.3 | 7631.1 | 11 | 0 | 0 |
| analyst_neutral_r1 | 11 | 60966 | 2457 | 63423 | 5542.4 | 223.4 | 6843.5 | 11 | 0 | 0 |
| analyst_bear_r2 | 11 | 29730 | 3161 | 32891 | 2702.7 | 287.4 | 5876.9 | 11 | 0 | 0 |
| analyst_bull_r2 | 11 | 28062 | 3160 | 31222 | 2551.1 | 287.3 | 5790.0 | 11 | 0 | 0 |
| analyst_neutral_r2 | 11 | 28098 | 2911 | 31009 | 2554.4 | 264.6 | 5941.4 | 11 | 0 | 0 |
| postmortem | 1 | 7796 | 2500 | 10296 | 7796.0 | 2500.0 | 45625.0 | 1 | 0 | 0 |
| param_tuner | 3 | 4793 | 1917 | 6710 | 1597.7 | 639.0 | 0.0 | 0 | 3 | 0 |
| tune_30min | 3 | 4905 | 937 | 5842 | 1635.0 | 312.3 | 0.0 | 0 | 3 | 0 |
| tune_60min | 2 | 3315 | 599 | 3914 | 1657.5 | 299.5 | 0.0 | 0 | 2 | 0 |
| tune_120min | 2 | 3321 | 592 | 3913 | 1660.5 | 296.0 | 0.0 | 0 | 2 | 0 |
| tune_90min | 2 | 3323 | 584 | 3907 | 1661.5 | 292.0 | 0.0 | 0 | 2 | 0 |
| tune_150min | 1 | 1740 | 355 | 2095 | 1740.0 | 355.0 | 0.0 | 0 | 1 | 0 |
| tune_210min | 1 | 1739 | 340 | 2079 | 1739.0 | 340.0 | 0.0 | 0 | 1 | 0 |
| tune_180min | 1 | 1742 | 316 | 2058 | 1742.0 | 316.0 | 0.0 | 0 | 1 | 0 |
| tune_240min | 1 | 1739 | 291 | 2030 | 1739.0 | 291.0 | 0.0 | 0 | 1 | 0 |

## Usage Timeline

| bucket start | calls | input | output | total | avg input | top labels | input issues | output issues |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| 2026-06-02T08:30+09:00 | 9 | 64315 | 7543 | 71858 | 7146.1 | {"analyst_bear_r1": 1, "analyst_bear_r2": 1, "analyst_bull_r1": 1, "analyst_bull_r2": 1, "select_tickers": 3} | {"prompt_input_tokens_ge_12000": 3} | {"output_tokens_ge_2000": 1, "response_fenced_json": 3, "response_has_preamble_or_wrapper": 3, "response_not_strict_json": 3, "slow_call_30s": 3} |
| 2026-06-02T09:00+09:00 | 29 | 150106 | 12042 | 162148 | 5176.1 | {"analyst_bear_r1": 4, "analyst_bear_r2": 4, "analyst_bull_r1": 4, "analyst_bull_r2": 4, "analyst_neutral_r1": 4} | {"prompt_input_tokens_ge_12000": 4} | {"response_fenced_json": 3, "response_has_preamble_or_wrapper": 3, "response_not_strict_json": 3} |
| 2026-06-02T09:30+09:00 | 9 | 44718 | 3563 | 48281 | 4968.7 | {"analyst_bear_r1": 1, "analyst_bear_r2": 1, "analyst_bull_r1": 2, "analyst_bull_r2": 1, "analyst_neutral_r1": 1} | {"prompt_input_tokens_ge_12000": 1} | {"response_fenced_json": 1, "response_has_preamble_or_wrapper": 1, "response_not_strict_json": 1} |
| 2026-06-02T10:00+09:00 | 8 | 46322 | 4742 | 51064 | 5790.2 | {"analyst_bear_r1": 1, "analyst_bear_r2": 1, "analyst_bull_r2": 1, "analyst_neutral_r1": 1, "select_tickers": 2} | {"prompt_input_tokens_ge_12000": 2} | {"response_fenced_json": 1, "response_has_preamble_or_wrapper": 2, "response_not_strict_json": 2} |
| 2026-06-02T10:30+09:00 | 3 | 16057 | 2429 | 18486 | 5352.3 | {"param_tuner": 1, "select_tickers": 1, "tune_120min": 1} | {"prompt_input_tokens_ge_12000": 1} | {"response_fenced_json": 1, "response_has_preamble_or_wrapper": 1, "response_not_strict_json": 1} |
| 2026-06-02T11:00+09:00 | 2 | 14468 | 1782 | 16250 | 7234.0 | {"select_tickers": 1, "tune_150min": 1} | {"prompt_input_tokens_ge_12000": 1} | {} |
| 2026-06-02T11:30+09:00 | 7 | 26123 | 1778 | 27901 | 3731.9 | {"analyst_bear_r1": 1, "analyst_bear_r2": 1, "analyst_bull_r1": 1, "analyst_bull_r2": 1, "analyst_neutral_r1": 1} | {} | {} |
| 2026-06-02T12:00+09:00 | 2 | 14586 | 1619 | 16205 | 7293.0 | {"select_tickers": 1, "tune_210min": 1} | {"prompt_input_tokens_ge_12000": 1} | {} |
| 2026-06-02T12:30+09:00 | 2 | 14372 | 1594 | 15966 | 7186.0 | {"select_tickers": 1, "tune_240min": 1} | {"prompt_input_tokens_ge_12000": 1} | {} |
| 2026-06-02T13:00+09:00 | 8 | 37375 | 3561 | 40936 | 4671.9 | {"analyst_bear_r1": 1, "analyst_bear_r2": 1, "analyst_bull_r1": 1, "analyst_bull_r2": 1, "analyst_neutral_r1": 1} | {"prompt_input_tokens_ge_8000": 1} | {} |
| 2026-06-02T13:30+09:00 | 8 | 37809 | 3309 | 41118 | 4726.1 | {"analyst_bear_r1": 1, "analyst_bear_r2": 1, "analyst_bull_r1": 1, "analyst_bull_r2": 1, "analyst_neutral_r1": 1} | {"prompt_input_tokens_ge_12000": 1} | {"response_fenced_json": 1, "response_has_preamble_or_wrapper": 1, "response_not_strict_json": 1} |
| 2026-06-02T14:00+09:00 | 1 | 1579 | 250 | 1829 | 1579.0 | {"tune_60min": 1} | {} | {} |
| 2026-06-02T14:30+09:00 | 2 | 12967 | 1830 | 14797 | 6483.5 | {"select_tickers": 1, "tune_90min": 1} | {"prompt_input_tokens_ge_8000": 1} | {} |
| 2026-06-02T15:00+09:00 | 11 | 65548 | 7664 | 73212 | 5958.9 | {"analyst_bear_r1": 1, "analyst_bear_r2": 1, "analyst_bull_r1": 1, "analyst_bull_r2": 1, "select_tickers": 3} | {"prompt_input_tokens_ge_12000": 3} | {"response_fenced_json": 1, "response_has_preamble_or_wrapper": 1, "response_not_strict_json": 1} |
| 2026-06-02T15:30+09:00 | 2 | 13744 | 1692 | 15436 | 6872.0 | {"select_tickers": 1, "tune_30min": 1} | {"prompt_input_tokens_ge_12000": 1} | {"response_fenced_json": 1, "response_has_preamble_or_wrapper": 1, "response_not_strict_json": 1} |
| 2026-06-02T16:00+09:00 | 1 | 7796 | 2500 | 10296 | 7796.0 | {"postmortem": 1} | {} | {"output_tokens_ge_2000": 1, "response_fenced_json": 1, "response_has_preamble_or_wrapper": 1, "response_not_strict_json": 1, "slow_call_30s": 1} |

## Input Issues

- prompt_input_tokens_ge_12000: 19
- prompt_input_tokens_ge_8000: 2

## Output Issues

- response_has_preamble_or_wrapper: 14
- response_not_strict_json: 14
- response_fenced_json: 13
- slow_call_30s: 4
- output_tokens_ge_2000: 2

## Recommendations

- P1 output_contract: Tighten JSON-only enforcement for affected labels or route non-strict responses through a bounded retry; parser recovery should remain fail-safe.
- P2 latency_cost: Separate timeout-prone labels from normal review flow and keep cache/cooldown guards active for repeated HOLD reviews.
- P2 token_cost: Reduce high-token prompts by trimming repeated calibration blocks and limiting evidence pack rows before model invocation; review labels with 8k+ input tokens even when the session average looks acceptable.

## Issue Samples

- 2026-06-02T16:00:53+09:00 postmortem input=[] output=['output_tokens_ge_2000', 'response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json', 'slow_call_30s'] path=logs\raw_calls\20260602_KR_postmortem_postmortem_KR_2026-06-02_069aa36cb4.json
- 2026-06-02T08:53:42+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json', 'slow_call_30s'] path=logs\raw_calls\20260602_KR_select_tickers_085342675751_826d316340.json
- 2026-06-02T08:55:40+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json', 'slow_call_30s'] path=logs\raw_calls\20260602_KR_select_tickers_085540169950_f092b4445e.json
- 2026-06-02T08:56:49+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['output_tokens_ge_2000', 'response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json', 'slow_call_30s'] path=logs\raw_calls\20260602_KR_select_tickers_085649200571_edc21a871d.json
- 2026-06-02T09:06:53+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_KR_select_tickers_090653837697_36cdac2260.json
- 2026-06-02T09:11:04+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_KR_select_tickers_091104415744_c55faca3a8.json
- 2026-06-02T09:16:24+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_KR_select_tickers_091624216716_6adb9870b2.json
- 2026-06-02T09:26:20+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260602_KR_select_tickers_092620593247_c0272b27fe.json
- 2026-06-02T09:45:30+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_KR_select_tickers_094530259544_5c2555d1db.json
- 2026-06-02T10:01:23+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_KR_select_tickers_100123965391_5397083287.json
- 2026-06-02T10:17:25+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_KR_select_tickers_101725777210_ae2ff5681b.json
- 2026-06-02T10:48:50+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_KR_select_tickers_104850829936_713dbb2296.json
- 2026-06-02T11:18:22+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260602_KR_select_tickers_111822399760_f410a3d205.json
- 2026-06-02T12:19:15+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260602_KR_select_tickers_121915661909_1cf13ee728.json
- 2026-06-02T12:51:49+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260602_KR_select_tickers_125149901637_6af50ac869.json
- 2026-06-02T13:17:29+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=[] path=logs\raw_calls\20260602_KR_select_tickers_131729870073_12ad248d87.json
- 2026-06-02T13:43:28+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_KR_select_tickers_134328337826_5d266c6a8d.json
- 2026-06-02T14:44:23+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=[] path=logs\raw_calls\20260602_KR_select_tickers_144423451927_724579db4f.json
- 2026-06-02T15:15:58+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_KR_select_tickers_151558621195_7e76aa3463.json
- 2026-06-02T15:24:47+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260602_KR_select_tickers_152447294901_a07ad5a109.json
- 2026-06-02T15:26:58+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260602_KR_select_tickers_152658235378_8c899d366b.json
- 2026-06-02T15:54:42+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_KR_select_tickers_155442826130_a504388668.json

## Prompt Warning Samples

| time | label | input | chars | candidates | evidence requested | evidence pack | lessons chars | top sections | path |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 2026-06-02T08:56:49+09:00 | select_tickers | 13392 | 30033 | 0 | 30 | 5 | 199 | header:15170, hard_soft_boundary:4737, runtime_evidence:3449, digest_news:2498 | logs\raw_calls\20260602_KR_select_tickers_085649200571_edc21a871d.json |
| 2026-06-02T08:55:40+09:00 | select_tickers | 13180 | 29748 | 0 | 30 | 5 | 199 | header:14885, hard_soft_boundary:4737, runtime_evidence:3449, digest_news:2498 | logs\raw_calls\20260602_KR_select_tickers_085540169950_f092b4445e.json |
| 2026-06-02T08:53:42+09:00 | select_tickers | 12927 | 29172 | 0 | 30 | 5 | 199 | header:14834, hard_soft_boundary:4695, runtime_evidence:3377, decision_contract:2127 | logs\raw_calls\20260602_KR_select_tickers_085342675751_826d316340.json |
| 2026-06-02T09:45:30+09:00 | select_tickers | 12847 | 27572 | 0 | 26 | 5 | 199 | candidates:15504, runtime_evidence:3496, digest_news:2498, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_094530259544_5c2555d1db.json |
| 2026-06-02T12:19:15+09:00 | select_tickers | 12847 | 27760 | 0 | 19 | 5 | 199 | candidates:15440, runtime_evidence:3553, digest_news:2498, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_121915661909_1cf13ee728.json |
| 2026-06-02T10:01:23+09:00 | select_tickers | 12787 | 27448 | 0 | 26 | 5 | 199 | candidates:15358, runtime_evidence:3518, digest_news:2498, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_100123965391_5397083287.json |
| 2026-06-02T10:17:25+09:00 | select_tickers | 12777 | 27447 | 0 | 26 | 5 | 199 | candidates:15336, runtime_evidence:3539, digest_news:2498, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_101725777210_ae2ff5681b.json |
| 2026-06-02T11:18:22+09:00 | select_tickers | 12728 | 27420 | 0 | 21 | 5 | 199 | candidates:15349, runtime_evidence:3499, digest_news:2498, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_111822399760_f410a3d205.json |
| 2026-06-02T10:48:50+09:00 | select_tickers | 12684 | 27360 | 0 | 26 | 5 | 199 | candidates:15334, runtime_evidence:3454, digest_news:2498, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_104850829936_713dbb2296.json |
| 2026-06-02T12:51:49+09:00 | select_tickers | 12633 | 27342 | 0 | 16 | 5 | 199 | candidates:15287, runtime_evidence:3483, digest_news:2498, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_125149901637_6af50ac869.json |
| 2026-06-02T09:26:20+09:00 | select_tickers | 12593 | 27161 | 0 | 26 | 5 | 199 | candidates:15161, runtime_evidence:3426, digest_news:2498, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_092620593247_c0272b27fe.json |
| 2026-06-02T09:16:24+09:00 | select_tickers | 12568 | 27129 | 0 | 26 | 5 | 199 | candidates:15061, runtime_evidence:3494, digest_news:2498, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_091624216716_6adb9870b2.json |
| 2026-06-02T09:06:53+09:00 | select_tickers | 12485 | 27271 | 0 | 30 | 5 | 199 | candidates:15184, runtime_evidence:3513, digest_news:2498, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_090653837697_36cdac2260.json |
| 2026-06-02T09:11:04+09:00 | select_tickers | 12470 | 26986 | 0 | 30 | 5 | 199 | candidates:14927, runtime_evidence:3485, digest_news:2498, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_091104415744_c55faca3a8.json |
| 2026-06-02T13:43:28+09:00 | select_tickers | 12395 | 27338 | 0 | 26 | 5 | 199 | candidates:15250, runtime_evidence:3558, digest_news:2455, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_134328337826_5d266c6a8d.json |
| 2026-06-02T15:26:58+09:00 | select_tickers | 12176 | 27194 | 0 | 26 | 5 | 199 | candidates:15198, runtime_evidence:3468, digest_news:2455, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_152658235378_8c899d366b.json |
| 2026-06-02T15:54:42+09:00 | select_tickers | 12125 | 26613 | 0 | 26 | 5 | 199 | candidates:14592, runtime_evidence:3493, digest_news:2455, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_155442826130_a504388668.json |
| 2026-06-02T15:15:58+09:00 | select_tickers | 12084 | 26814 | 0 | 26 | 5 | 199 | candidates:14755, runtime_evidence:3529, digest_news:2455, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_151558621195_7e76aa3463.json |
| 2026-06-02T15:24:47+09:00 | select_tickers | 12000 | 26693 | 0 | 26 | 5 | 199 | candidates:14697, runtime_evidence:3466, digest_news:2455, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_152447294901_a07ad5a109.json |
| 2026-06-02T13:17:29+09:00 | select_tickers | 11959 | 26713 | 0 | 26 | 5 | 199 | candidates:14699, runtime_evidence:3484, digest_news:2455, output_contract:1354 | logs\raw_calls\20260602_KR_select_tickers_131729870073_12ad248d87.json |
| 2026-06-02T14:44:23+09:00 | select_tickers | 11388 | 23866 | 0 | 26 | 5 | 199 | candidates:15408, digest_news:2455, output_contract:1354, rules:977 | logs\raw_calls\20260602_KR_select_tickers_144423451927_724579db4f.json |

## Slow Calls

- 2026-06-02T16:00:53+09:00 postmortem duration_ms=45625 path=logs\raw_calls\20260602_KR_postmortem_postmortem_KR_2026-06-02_069aa36cb4.json
- 2026-06-02T08:53:42+09:00 select_tickers duration_ms=35522 path=logs\raw_calls\20260602_KR_select_tickers_085342675751_826d316340.json
- 2026-06-02T08:55:40+09:00 select_tickers duration_ms=33632 path=logs\raw_calls\20260602_KR_select_tickers_085540169950_f092b4445e.json
- 2026-06-02T08:56:49+09:00 select_tickers duration_ms=39412 path=logs\raw_calls\20260602_KR_select_tickers_085649200571_edc21a871d.json
- 2026-06-02T09:06:53+09:00 select_tickers duration_ms=18875 path=logs\raw_calls\20260602_KR_select_tickers_090653837697_36cdac2260.json
- 2026-06-02T09:11:04+09:00 select_tickers duration_ms=20418 path=logs\raw_calls\20260602_KR_select_tickers_091104415744_c55faca3a8.json
- 2026-06-02T09:16:24+09:00 select_tickers duration_ms=22445 path=logs\raw_calls\20260602_KR_select_tickers_091624216716_6adb9870b2.json
- 2026-06-02T09:26:20+09:00 select_tickers duration_ms=18520 path=logs\raw_calls\20260602_KR_select_tickers_092620593247_c0272b27fe.json
- 2026-06-02T09:45:30+09:00 select_tickers duration_ms=19455 path=logs\raw_calls\20260602_KR_select_tickers_094530259544_5c2555d1db.json
- 2026-06-02T10:01:23+09:00 select_tickers duration_ms=21936 path=logs\raw_calls\20260602_KR_select_tickers_100123965391_5397083287.json
- 2026-06-02T10:17:25+09:00 select_tickers duration_ms=29055 path=logs\raw_calls\20260602_KR_select_tickers_101725777210_ae2ff5681b.json
- 2026-06-02T10:48:50+09:00 select_tickers duration_ms=19172 path=logs\raw_calls\20260602_KR_select_tickers_104850829936_713dbb2296.json
- 2026-06-02T11:18:22+09:00 select_tickers duration_ms=21153 path=logs\raw_calls\20260602_KR_select_tickers_111822399760_f410a3d205.json
- 2026-06-02T12:19:15+09:00 select_tickers duration_ms=17245 path=logs\raw_calls\20260602_KR_select_tickers_121915661909_1cf13ee728.json
- 2026-06-02T12:51:49+09:00 select_tickers duration_ms=18214 path=logs\raw_calls\20260602_KR_select_tickers_125149901637_6af50ac869.json
- 2026-06-02T13:17:29+09:00 select_tickers duration_ms=17965 path=logs\raw_calls\20260602_KR_select_tickers_131729870073_12ad248d87.json
- 2026-06-02T13:43:28+09:00 select_tickers duration_ms=20689 path=logs\raw_calls\20260602_KR_select_tickers_134328337826_5d266c6a8d.json
- 2026-06-02T14:44:23+09:00 select_tickers duration_ms=20408 path=logs\raw_calls\20260602_KR_select_tickers_144423451927_724579db4f.json
- 2026-06-02T15:15:58+09:00 select_tickers duration_ms=24570 path=logs\raw_calls\20260602_KR_select_tickers_151558621195_7e76aa3463.json
- 2026-06-02T15:24:47+09:00 select_tickers duration_ms=17743 path=logs\raw_calls\20260602_KR_select_tickers_152447294901_a07ad5a109.json
