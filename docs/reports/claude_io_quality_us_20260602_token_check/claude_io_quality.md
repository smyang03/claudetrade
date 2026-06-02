# Claude I/O Quality Report

- generated_at: 2026-06-02T20:51:11+09:00
- scope: US 2026-06-02T00:00:00+09:00 ~ 2026-06-02T23:59:59+09:00
- raw_calls: 60
- tokens: input=181719 output=47969 total=229688
- averages: input=3028.7 output=799.5 duration_ms=15446.5
- duration_coverage: observed=51 missing=9
- parse_errors: 0

## Calls By Label

| label | calls | input | output | total | avg input | avg output | avg ms | ms observed | ms missing | parse errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| select_tickers | 8 | 94571 | 13742 | 108313 | 11821.4 | 1717.8 | 24175.7 | 7 | 1 | 0 |
| hold_advisor_triage | 33 | 50987 | 25389 | 76376 | 1545.1 | 769.4 | 15008.3 | 33 | 0 | 0 |
| postmortem | 1 | 7858 | 2500 | 10358 | 7858.0 | 2500.0 | 47750.0 | 1 | 0 | 0 |
| tune_30min | 3 | 5313 | 728 | 6041 | 1771.0 | 242.7 | 0.0 | 0 | 3 | 0 |
| hold_advisor_bear | 3 | 4224 | 1184 | 5408 | 1408.0 | 394.7 | 7245.0 | 3 | 0 | 0 |
| hold_advisor_neutral | 3 | 4206 | 1130 | 5336 | 1402.0 | 376.7 | 6816.3 | 3 | 0 | 0 |
| hold_advisor_bull | 3 | 4218 | 1075 | 5293 | 1406.0 | 358.3 | 6671.7 | 3 | 0 | 0 |
| hold_advisor_challenge | 1 | 1670 | 575 | 2245 | 1670.0 | 575.0 | 13317.0 | 1 | 0 | 0 |
| param_tuner | 1 | 1461 | 673 | 2134 | 1461.0 | 673.0 | 0.0 | 0 | 1 | 0 |
| tune_120min | 1 | 1791 | 303 | 2094 | 1791.0 | 303.0 | 0.0 | 0 | 1 | 0 |
| tune_90min | 1 | 1840 | 202 | 2042 | 1840.0 | 202.0 | 0.0 | 0 | 1 | 0 |
| tune_60min | 1 | 1840 | 194 | 2034 | 1840.0 | 194.0 | 0.0 | 0 | 1 | 0 |
| tune_150min | 1 | 1740 | 274 | 2014 | 1740.0 | 274.0 | 0.0 | 0 | 1 | 0 |

## Usage Timeline

| bucket start | calls | input | output | total | avg input | top labels | input issues | output issues |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
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

- prompt_input_tokens_ge_12000: 6
- prompt_input_tokens_ge_8000: 2

## Output Issues

- response_fenced_json: 45
- response_has_preamble_or_wrapper: 45
- response_not_strict_json: 45
- output_tokens_ge_2000: 1
- slow_call_30s: 1
- trade_ready_action_not_buy_or_probe: 1

## Recommendations

- P1 output_contract: Tighten JSON-only enforcement for affected labels or route non-strict responses through a bounded retry; parser recovery should remain fail-safe.
- P2 latency_cost: Separate timeout-prone labels from normal review flow and keep cache/cooldown guards active for repeated HOLD reviews.
- P2 token_cost: Reduce high-token prompts by trimming repeated calibration blocks and limiting evidence pack rows before model invocation; review labels with 8k+ input tokens even when the session average looks acceptable.

## Issue Samples

- 2026-06-02T05:03:35+09:00 postmortem input=[] output=['output_tokens_ge_2000', 'response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json', 'slow_call_30s'] path=logs\raw_calls\20260601_US_postmortem_postmortem_US_2026-06-01_d35af0fb6f.json
- 2026-06-02T00:28:14+09:00 hold_advisor_bear input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_bear_002814252933_ee813d026a.json
- 2026-06-02T00:50:26+09:00 hold_advisor_bear input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_bear_005026449865_c3c9b6e0dc.json
- 2026-06-02T00:50:47+09:00 hold_advisor_bear input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_bear_005047263917_5d56995dc5.json
- 2026-06-02T00:28:06+09:00 hold_advisor_bull input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_bull_002806694410_e320dd3187.json
- 2026-06-02T00:50:19+09:00 hold_advisor_bull input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_bull_005019397981_8a62a2fb5e.json
- 2026-06-02T00:50:38+09:00 hold_advisor_bull input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_bull_005038554645_14e3d2aaad.json
- 2026-06-02T01:01:07+09:00 hold_advisor_challenge input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_challenge_010107504988_e1430d5dac.json
- 2026-06-02T00:28:21+09:00 hold_advisor_neutral input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_neutral_002821766256_14126f1f73.json
- 2026-06-02T00:50:33+09:00 hold_advisor_neutral input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_neutral_005033507785_4afc12b09c.json
- 2026-06-02T00:50:54+09:00 hold_advisor_neutral input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_neutral_005054704954_552e393287.json
- 2026-06-02T00:13:26+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_001326241708_a9a2e5eb3b.json
- 2026-06-02T00:17:31+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_001731782458_1d60a5b965.json
- 2026-06-02T00:47:03+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_004703925338_42f3626171.json
- 2026-06-02T00:48:26+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_004826100664_ea456de5ab.json
- 2026-06-02T00:58:17+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_005817546769_e19ba78af1.json
- 2026-06-02T01:00:54+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_010054167377_5f2e96a9f7.json
- 2026-06-02T01:13:18+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_011318856373_b2f3d1cfc1.json
- 2026-06-02T01:17:49+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_011749226380_c48b0279c7.json
- 2026-06-02T02:00:17+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_020017358741_bc5052f19f.json
- 2026-06-02T02:04:08+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_020408656326_d5b144d0d7.json
- 2026-06-02T02:04:30+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_020430275202_2dc6b6a4c9.json
- 2026-06-02T02:36:16+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_023616235674_5f75cd973e.json
- 2026-06-02T02:45:37+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_024537115836_cd4ee41a89.json
- 2026-06-02T03:01:21+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_030121895138_7463ccefb6.json
- 2026-06-02T03:04:01+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_030401863332_caf82941dc.json
- 2026-06-02T03:04:39+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_030439068683_8f4139c79e.json
- 2026-06-02T03:04:56+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_030456114631_4787ef2d52.json
- 2026-06-02T03:06:48+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_030648356106_9c34c204d5.json
- 2026-06-02T03:08:13+09:00 hold_advisor_triage input=[] output=['response_fenced_json', 'response_has_preamble_or_wrapper', 'response_not_strict_json'] path=logs\raw_calls\20260602_US_hold_advisor_triage_030813852947_df2850fd51.json

## Prompt Warning Samples

| time | label | input | chars | candidates | evidence requested | evidence pack | lessons chars | top sections | path |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 2026-06-02T00:17:01+09:00 | select_tickers | 12201 | 26561 | 35 | 28 | 5 | 199 | candidates:14558, runtime_evidence:3566, digest_news:2362, output_contract:1354 | logs\raw_calls\20260602_US_select_tickers_001701587242_bb0b2458bc.json |
| 2026-06-02T02:34:23+09:00 | select_tickers | 12132 | 26497 | 35 | 28 | 5 | 199 | candidates:14466, runtime_evidence:3582, digest_news:2374, output_contract:1354 | logs\raw_calls\20260602_US_select_tickers_023423921136_66e00acf02.json |
| 2026-06-02T01:54:06+09:00 | select_tickers | 12124 | 26504 | 35 | 28 | 5 | 199 | candidates:14489, runtime_evidence:3578, digest_news:2362, output_contract:1354 | logs\raw_calls\20260602_US_select_tickers_015406187108_9bc8825af8.json |
| 2026-06-02T00:59:02+09:00 | select_tickers | 12120 | 26467 | 35 | 28 | 5 | 199 | candidates:14463, runtime_evidence:3555, digest_news:2374, output_contract:1354 | logs\raw_calls\20260602_US_select_tickers_005902872170_95be3921f4.json |
| 2026-06-02T01:24:13+09:00 | select_tickers | 12115 | 26487 | 35 | 28 | 5 | 199 | candidates:14495, runtime_evidence:3555, digest_news:2362, output_contract:1354 | logs\raw_calls\20260602_US_select_tickers_012413411746_f4c43b45d4.json |
| 2026-06-02T04:07:55+09:00 | select_tickers | 12061 | 26275 | 35 | 28 | 5 | 199 | candidates:14362, runtime_evidence:3463, digest_news:2375, output_contract:1354 | logs\raw_calls\20260602_US_select_tickers_040755223717_64310bb0d1.json |
| 2026-06-02T04:37:47+09:00 | select_tickers | 10925 | 22823 | 35 | 28 | 5 | 199 | candidates:14457, digest_news:2363, output_contract:1354, rules:977 | logs\raw_calls\20260602_US_select_tickers_043747812053_b8605e2f57.json |
| 2026-06-02T03:36:18+09:00 | select_tickers | 10893 | 22770 | 35 | 28 | 5 | 199 | candidates:14394, digest_news:2373, output_contract:1354, rules:977 | logs\raw_calls\20260602_US_select_tickers_033618077800_c3eaceca58.json |

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
