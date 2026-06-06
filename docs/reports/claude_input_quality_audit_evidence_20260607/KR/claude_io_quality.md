# Claude I/O Quality Report

- generated_at: 2026-06-07T02:45:22+09:00
- scope: KR 2026-06-05T00:00:00+09:00 ~ 2026-06-06T00:00:00+09:00
- raw_calls: 62
- tokens: input=315803 output=40537 total=356340
- averages: input=5093.6 output=653.8 duration_ms=13710.0
- duration_coverage: observed=47 missing=15
- parse_errors: 0

## Calls By Label

| label | calls | input | output | total | avg input | avg output | avg ms | ms observed | ms missing | parse errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| select_tickers | 14 | 165502 | 22342 | 187844 | 11821.6 | 1595.9 | 24127.3 | 14 | 0 | 0 |
| analyst_bear_r1 | 4 | 24225 | 1268 | 25493 | 6056.2 | 317.0 | 8610.2 | 4 | 0 | 0 |
| analyst_neutral_r1 | 4 | 23453 | 1207 | 24660 | 5863.2 | 301.8 | 8494.2 | 4 | 0 | 0 |
| analyst_bull_r1 | 4 | 22969 | 1095 | 24064 | 5742.2 | 273.8 | 7918.5 | 4 | 0 | 0 |
| analyst_bear_r2 | 4 | 12341 | 1616 | 13957 | 3085.2 | 404.0 | 7989.5 | 4 | 0 | 0 |
| analyst_neutral_r2 | 4 | 11853 | 1685 | 13538 | 2963.2 | 421.2 | 8914.0 | 4 | 0 | 0 |
| analyst_bull_r2 | 4 | 11389 | 1518 | 12907 | 2847.2 | 379.5 | 8053.0 | 4 | 0 | 0 |
| hold_advisor_triage | 6 | 10203 | 2116 | 12319 | 1700.5 | 352.7 | 7482.0 | 6 | 0 | 0 |
| postmortem | 1 | 8120 | 2500 | 10620 | 8120.0 | 2500.0 | 49702.0 | 1 | 0 | 0 |
| tune_30min | 3 | 4330 | 902 | 5232 | 1443.3 | 300.7 | 0.0 | 0 | 3 | 0 |
| hold_advisor_challenge | 2 | 3391 | 525 | 3916 | 1695.5 | 262.5 | 6039.0 | 2 | 0 | 0 |
| tune_120min | 2 | 3059 | 711 | 3770 | 1529.5 | 355.5 | 0.0 | 0 | 2 | 0 |
| tune_150min | 2 | 3060 | 679 | 3739 | 1530.0 | 339.5 | 0.0 | 0 | 2 | 0 |
| tune_90min | 2 | 3022 | 569 | 3591 | 1511.0 | 284.5 | 0.0 | 0 | 2 | 0 |
| tune_60min | 2 | 3022 | 530 | 3552 | 1511.0 | 265.0 | 0.0 | 0 | 2 | 0 |
| param_tuner | 2 | 2808 | 632 | 3440 | 1404.0 | 316.0 | 0.0 | 0 | 2 | 0 |
| tune_210min | 1 | 1528 | 337 | 1865 | 1528.0 | 337.0 | 0.0 | 0 | 1 | 0 |
| tune_180min | 1 | 1528 | 305 | 1833 | 1528.0 | 305.0 | 0.0 | 0 | 1 | 0 |

## Usage Timeline

| bucket start | calls | input | output | total | avg input | top labels | input issues | output issues |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| 2026-06-05T08:30+09:00 | 3 | 26795 | 4067 | 30862 | 8931.7 | {"select_tickers": 2, "tune_30min": 1} | {"prompt_input_tokens_ge_12000": 2} | {"response_fenced_json": 2, "response_has_preamble_or_wrapper": 2, "response_not_strict_json": 2, "slow_call_30s": 2} |
| 2026-06-05T09:00+09:00 | 8 | 39717 | 3670 | 43387 | 4964.6 | {"analyst_bear_r1": 1, "analyst_bear_r2": 1, "analyst_bull_r1": 1, "analyst_bull_r2": 1, "analyst_neutral_r1": 1} | {"prompt_input_tokens_ge_8000": 1} | {} |
| 2026-06-05T09:30+09:00 | 2 | 13730 | 1880 | 15610 | 6865.0 | {"select_tickers": 1, "tune_90min": 1} | {"prompt_input_tokens_ge_12000": 1} | {} |
| 2026-06-05T10:00+09:00 | 4 | 15344 | 2537 | 17881 | 3836.0 | {"hold_advisor_challenge": 1, "hold_advisor_triage": 1, "select_tickers": 1, "tune_120min": 1} | {"prompt_input_tokens_ge_8000": 1} | {} |
| 2026-06-05T10:30+09:00 | 3 | 15408 | 2242 | 17650 | 5136.0 | {"hold_advisor_triage": 1, "select_tickers": 1, "tune_150min": 1} | {"prompt_input_tokens_ge_12000": 1} | {} |
| 2026-06-05T11:00+09:00 | 3 | 4892 | 1029 | 5921 | 1630.7 | {"hold_advisor_triage": 2, "tune_180min": 1} | {} | {} |
| 2026-06-05T11:30+09:00 | 5 | 19045 | 2682 | 21727 | 3809.0 | {"hold_advisor_challenge": 1, "hold_advisor_triage": 2, "select_tickers": 1, "tune_210min": 1} | {"prompt_input_tokens_ge_12000": 1} | {} |
| 2026-06-05T12:30+09:00 | 16 | 80897 | 8153 | 89050 | 5056.1 | {"analyst_bear_r1": 2, "analyst_bear_r2": 2, "analyst_bull_r1": 2, "analyst_bull_r2": 2, "analyst_neutral_r1": 2} | {"prompt_input_tokens_ge_12000": 2, "prompt_mojibake_hangul_compat_jamo": 6} | {} |
| 2026-06-05T13:00+09:00 | 1 | 1532 | 261 | 1793 | 1532.0 | {"tune_60min": 1} | {} | {} |
| 2026-06-05T13:30+09:00 | 2 | 12369 | 1957 | 14326 | 6184.5 | {"select_tickers": 1, "tune_90min": 1} | {"prompt_input_tokens_ge_8000": 1} | {} |
| 2026-06-05T14:30+09:00 | 2 | 12208 | 1933 | 14141 | 6104.0 | {"select_tickers": 1, "tune_120min": 1} | {"prompt_input_tokens_ge_8000": 1} | {} |
| 2026-06-05T15:00+09:00 | 4 | 25304 | 3782 | 29086 | 6326.0 | {"param_tuner": 1, "select_tickers": 2, "tune_150min": 1} | {"prompt_input_tokens_ge_8000": 2} | {} |
| 2026-06-05T15:30+09:00 | 8 | 40442 | 3844 | 44286 | 5055.2 | {"analyst_bear_r1": 1, "analyst_bear_r2": 1, "analyst_bull_r1": 1, "analyst_bull_r2": 1, "analyst_neutral_r1": 1} | {"prompt_input_tokens_ge_12000": 1, "prompt_mojibake_hangul_compat_jamo": 3} | {} |
| 2026-06-05T16:00+09:00 | 1 | 8120 | 2500 | 10620 | 8120.0 | {"postmortem": 1} | {"prompt_input_tokens_ge_8000": 1} | {"output_tokens_ge_2000": 1, "response_fenced_json": 1, "response_has_preamble_or_wrapper": 1, "response_not_strict_json": 1, "slow_call_30s": 1} |

## Input Issues

- prompt_mojibake_hangul_compat_jamo: 9
- prompt_input_tokens_ge_12000: 8
- prompt_input_tokens_ge_8000: 7

## Output Issues

- response_fenced_json: 3
- response_has_preamble_or_wrapper: 3
- response_not_strict_json: 3
- slow_call_30s: 3
- output_tokens_ge_2000: 1

## Recommendations

- P1 input_quality: Fix the prompt text encoding path before changing trading policy; garbled Korean weakens evidence interpretation and review rationale.
- P1 output_contract: Tighten JSON-only enforcement for affected labels or route non-strict responses through a bounded retry; parser recovery should remain fail-safe.
- P2 latency_cost: Separate timeout-prone labels from normal review flow and keep cache/cooldown guards active for repeated HOLD reviews.
- P2 token_cost: Reduce high-token prompts by trimming repeated calibration blocks and limiting evidence pack rows before model invocation; review labels with 8k+ input tokens even when the session average looks acceptable.

## Issue Samples

- 2026-06-05T12:30:53+09:00 analyst_bear_r1 input=['prompt_mojibake_hangul_compat_jamo'] output=[] path=logs\raw_calls\20260605_KR_analyst_bear_r1_123053556241_2113d4f399.json
- 2026-06-05T12:40:53+09:00 analyst_bear_r1 input=['prompt_mojibake_hangul_compat_jamo'] output=[] path=logs\raw_calls\20260605_KR_analyst_bear_r1_124053076398_f124bb3397.json
- 2026-06-05T15:54:15+09:00 analyst_bear_r1 input=['prompt_mojibake_hangul_compat_jamo'] output=[] path=logs\raw_calls\20260605_KR_analyst_bear_r1_155415189092_76c015529e.json
- 2026-06-05T12:30:43+09:00 analyst_bull_r1 input=['prompt_mojibake_hangul_compat_jamo'] output=[] path=logs\raw_calls\20260605_KR_analyst_bull_r1_123043673914_91a857b1c7.json
- 2026-06-05T12:40:43+09:00 analyst_bull_r1 input=['prompt_mojibake_hangul_compat_jamo'] output=[] path=logs\raw_calls\20260605_KR_analyst_bull_r1_124043908356_561fcb8068.json
- 2026-06-05T15:54:04+09:00 analyst_bull_r1 input=['prompt_mojibake_hangul_compat_jamo'] output=[] path=logs\raw_calls\20260605_KR_analyst_bull_r1_155404231546_d1450163e0.json
- 2026-06-05T12:31:02+09:00 analyst_neutral_r1 input=['prompt_mojibake_hangul_compat_jamo'] output=[] path=logs\raw_calls\20260605_KR_analyst_neutral_r1_123102957068_47dec24594.json
- 2026-06-05T12:41:04+09:00 analyst_neutral_r1 input=['prompt_mojibake_hangul_compat_jamo'] output=[] path=logs\raw_calls\20260605_KR_analyst_neutral_r1_124104063737_7c2faf76a9.json
- 2026-06-05T15:54:25+09:00 analyst_neutral_r1 input=['prompt_mojibake_hangul_compat_jamo'] output=[] path=logs\raw_calls\20260605_KR_analyst_neutral_r1_155425095668_cf599e2c3a.json
- 2026-06-05T16:00:56+09:00 postmortem input=['prompt_input_tokens_ge_8000'] output=['output_tokens_ge_2000', 'response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json', 'slow_call_30s'] path=logs\raw_calls\20260605_KR_postmortem_postmortem_KR_2026-06-05_dfbb273657.json
- 2026-06-05T08:53:57+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json', 'slow_call_30s'] path=logs\raw_calls\20260605_KR_select_tickers_085357394862_cab877f988.json
- 2026-06-05T08:54:57+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json', 'slow_call_30s'] path=logs\raw_calls\20260605_KR_select_tickers_085457385699_e418ddce34.json
- 2026-06-05T09:05:07+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=[] path=logs\raw_calls\20260605_KR_select_tickers_090507314511_07e0f86f67.json
- 2026-06-05T09:55:49+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260605_KR_select_tickers_095549637979_2f5934abb6.json
- 2026-06-05T10:27:06+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=[] path=logs\raw_calls\20260605_KR_select_tickers_102706645892_f973a4349f.json
- 2026-06-05T10:56:38+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260605_KR_select_tickers_105638898810_44012b905d.json
- 2026-06-05T11:57:30+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260605_KR_select_tickers_115730453751_f05a8672a3.json
- 2026-06-05T12:32:30+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260605_KR_select_tickers_123230283796_1d7bfa1763.json
- 2026-06-05T12:58:41+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260605_KR_select_tickers_125841147439_a0e0f5ca94.json
- 2026-06-05T13:59:34+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=[] path=logs\raw_calls\20260605_KR_select_tickers_135934325366_b6e02185a0.json
- 2026-06-05T14:30:51+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=[] path=logs\raw_calls\20260605_KR_select_tickers_143051725424_8d84940a27.json
- 2026-06-05T15:00:25+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=[] path=logs\raw_calls\20260605_KR_select_tickers_150025142081_fd06a596b5.json
- 2026-06-05T15:24:42+09:00 select_tickers input=['prompt_input_tokens_ge_8000'] output=[] path=logs\raw_calls\20260605_KR_select_tickers_152442388966_a2dd8fcffa.json
- 2026-06-05T15:55:40+09:00 select_tickers input=['prompt_input_tokens_ge_12000'] output=[] path=logs\raw_calls\20260605_KR_select_tickers_155540419960_52df314ad3.json

## Prompt Warning Samples

| time | label | input | chars | candidates | evidence requested | evidence pack | lessons chars | top sections | path |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 2026-06-05T08:54:57+09:00 | select_tickers | 12878 | 28964 | 0 | 30 | 5 | 199 | header:13914, hard_soft_boundary:4908, runtime_evidence:3451, digest_news:2512 | logs\raw_calls\20260605_KR_select_tickers_085457385699_e418ddce34.json |
| 2026-06-05T08:53:57+09:00 | select_tickers | 12651 | 28485 | 0 | 30 | 5 | 199 | header:13901, hard_soft_boundary:4866, runtime_evidence:3438, decision_contract:2127 | logs\raw_calls\20260605_KR_select_tickers_085357394862_cab877f988.json |
| 2026-06-05T12:32:30+09:00 | select_tickers | 12427 | 26705 | 0 | 16 | 5 | 199 | candidates:14187, runtime_evidence:3543, digest_news:2527, output_contract:1569 | logs\raw_calls\20260605_KR_select_tickers_123230283796_1d7bfa1763.json |
| 2026-06-05T12:58:41+09:00 | select_tickers | 12414 | 26685 | 0 | 16 | 5 | 199 | candidates:14267, runtime_evidence:3443, digest_news:2527, output_contract:1569 | logs\raw_calls\20260605_KR_select_tickers_125841147439_a0e0f5ca94.json |
| 2026-06-05T11:57:30+09:00 | select_tickers | 12306 | 26469 | 0 | 22 | 5 | 199 | candidates:14020, runtime_evidence:3493, digest_news:2512, output_contract:1569 | logs\raw_calls\20260605_KR_select_tickers_115730453751_f05a8672a3.json |
| 2026-06-05T15:55:40+09:00 | select_tickers | 12254 | 26942 | 0 | 26 | 5 | 199 | candidates:14446, runtime_evidence:3525, digest_news:2527, output_contract:1569 | logs\raw_calls\20260605_KR_select_tickers_155540419960_52df314ad3.json |
| 2026-06-05T09:55:49+09:00 | select_tickers | 12240 | 26333 | 0 | 26 | 5 | 199 | candidates:13887, runtime_evidence:3490, digest_news:2512, output_contract:1569 | logs\raw_calls\20260605_KR_select_tickers_095549637979_2f5934abb6.json |
| 2026-06-05T10:56:38+09:00 | select_tickers | 12187 | 26239 | 0 | 26 | 5 | 199 | candidates:13863, runtime_evidence:3420, digest_news:2512, output_contract:1569 | logs\raw_calls\20260605_KR_select_tickers_105638898810_44012b905d.json |
| 2026-06-05T09:05:07+09:00 | select_tickers | 11773 | 26318 | 0 | 30 | 5 | 199 | candidates:13852, runtime_evidence:3508, digest_news:2512, output_contract:1569 | logs\raw_calls\20260605_KR_select_tickers_090507314511_07e0f86f67.json |
| 2026-06-05T15:24:42+09:00 | select_tickers | 11754 | 26030 | 0 | 26 | 5 | 199 | candidates:13555, runtime_evidence:3500, digest_news:2527, output_contract:1569 | logs\raw_calls\20260605_KR_select_tickers_152442388966_a2dd8fcffa.json |
| 2026-06-05T13:59:34+09:00 | select_tickers | 10837 | 22784 | 0 | 26 | 5 | 199 | candidates:13881, digest_news:2527, output_contract:1569, rules:1131 | logs\raw_calls\20260605_KR_select_tickers_135934325366_b6e02185a0.json |
| 2026-06-05T14:30:51+09:00 | select_tickers | 10676 | 22538 | 0 | 26 | 5 | 199 | candidates:13635, digest_news:2527, output_contract:1569, rules:1131 | logs\raw_calls\20260605_KR_select_tickers_143051725424_8d84940a27.json |
| 2026-06-05T15:00:25+09:00 | select_tickers | 10614 | 22529 | 0 | 26 | 5 | 199 | candidates:13626, digest_news:2527, output_contract:1569, rules:1131 | logs\raw_calls\20260605_KR_select_tickers_150025142081_fd06a596b5.json |
| 2026-06-05T10:27:06+09:00 | select_tickers | 10491 | 22368 | 0 | 26 | 5 | 199 | candidates:13484, digest_news:2512, output_contract:1569, rules:1131 | logs\raw_calls\20260605_KR_select_tickers_102706645892_f973a4349f.json |
| 2026-06-05T16:00:56+09:00 | postmortem | 8120 | 11978 | 0 | 0 | 0 | 0 | header:11978 | logs\raw_calls\20260605_KR_postmortem_postmortem_KR_2026-06-05_dfbb273657.json |

## Slow Calls

- 2026-06-05T16:00:56+09:00 postmortem duration_ms=49702 path=logs\raw_calls\20260605_KR_postmortem_postmortem_KR_2026-06-05_dfbb273657.json
- 2026-06-05T08:53:57+09:00 select_tickers duration_ms=39704 path=logs\raw_calls\20260605_KR_select_tickers_085357394862_cab877f988.json
- 2026-06-05T08:54:57+09:00 select_tickers duration_ms=34470 path=logs\raw_calls\20260605_KR_select_tickers_085457385699_e418ddce34.json
- 2026-06-05T09:05:07+09:00 select_tickers duration_ms=20016 path=logs\raw_calls\20260605_KR_select_tickers_090507314511_07e0f86f67.json
- 2026-06-05T09:55:49+09:00 select_tickers duration_ms=22303 path=logs\raw_calls\20260605_KR_select_tickers_095549637979_2f5934abb6.json
- 2026-06-05T10:27:06+09:00 select_tickers duration_ms=22738 path=logs\raw_calls\20260605_KR_select_tickers_102706645892_f973a4349f.json
- 2026-06-05T10:56:38+09:00 select_tickers duration_ms=20827 path=logs\raw_calls\20260605_KR_select_tickers_105638898810_44012b905d.json
- 2026-06-05T11:57:30+09:00 select_tickers duration_ms=20437 path=logs\raw_calls\20260605_KR_select_tickers_115730453751_f05a8672a3.json
- 2026-06-05T12:32:30+09:00 select_tickers duration_ms=29376 path=logs\raw_calls\20260605_KR_select_tickers_123230283796_1d7bfa1763.json
- 2026-06-05T12:58:41+09:00 select_tickers duration_ms=21767 path=logs\raw_calls\20260605_KR_select_tickers_125841147439_a0e0f5ca94.json
- 2026-06-05T13:59:34+09:00 select_tickers duration_ms=22269 path=logs\raw_calls\20260605_KR_select_tickers_135934325366_b6e02185a0.json
- 2026-06-05T14:30:51+09:00 select_tickers duration_ms=20946 path=logs\raw_calls\20260605_KR_select_tickers_143051725424_8d84940a27.json
- 2026-06-05T15:00:25+09:00 select_tickers duration_ms=19511 path=logs\raw_calls\20260605_KR_select_tickers_150025142081_fd06a596b5.json
- 2026-06-05T15:24:42+09:00 select_tickers duration_ms=22510 path=logs\raw_calls\20260605_KR_select_tickers_152442388966_a2dd8fcffa.json
- 2026-06-05T15:55:40+09:00 select_tickers duration_ms=20908 path=logs\raw_calls\20260605_KR_select_tickers_155540419960_52df314ad3.json
