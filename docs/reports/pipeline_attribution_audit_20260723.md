# Candidate → Decision → Fill Attribution Audit 2026-07-23

## Summary

- since: `2026-07-01`
- canonical filled: 7
- lifecycle FILLED events: 12
- lifecycle unique filled decisions: 9
- candidate rows with execution_decision_id: 505
- candidate unique execution decisions: 99
- candidate filled rows among linked rows: 3

## Classification Counts

- correct_candidate_fill: 1
- external_strategy_no_candidate_expected: 4
- wrong_row_filled_correct_row_unfilled: 1
- wrong_row_filled_no_correct_row: 1

## Lifecycle filled but not yet canonical

- `dec_20260722_KR_275280_9e861b32`
- `dec_20260722_KR_275300_b3ea79d9`

## Duplicate FILLED event groups

- `dec_20260702_US_IREN_a1e6805b` execution=`0031350130` occurred_at=`2026-07-02T14:21:09+00:00` events=[10927, 10928]
- `dec_20260703_KR_003490_091a0409` execution=`0015829800` occurred_at=`2026-07-03T02:00:11+00:00` events=[11103, 11104]
- `dec_20260706_US_NVDA_b4e1445e` execution=`0030229036` occurred_at=`2026-07-06T14:43:50+00:00` events=[11199, 11200]

## Filled Decision Details

### US IREN / dec_20260702_US_IREN_a1e6805b

- canonical: route=`path_b` path_type=`claude_price` strategy=`claude_price` origin_action=`PULLBACK_WAIT`
- fill: event_id=`10927` at=`2026-07-02T14:21:09+00:00` entry=42.09 pnl_net=-2.6383 close_reason=`CLOSED_LOSS_CAP`
- expected candidate route: `PathB.wait`
- classification: `wrong_row_filled_correct_row_unfilled`
- linked_candidate_count=11 filled_candidate_count=1 correct_candidate_count=6 wrong_filled_count=1 no_submit_and_filled_count=1

| candidate | known_at | action | route | filled | event | no_submit | source |
|---|---|---|---|---:|---:|---|---|
| `cand_0670f2756ac52d781711` | 2026-07-02T22:38:21+09:00 | PROBE_READY | PROBE_READY/PlanA.probe | 0 | None |  | trading_bot.v2_register_trade_ready |
| `cand_a74a5d9f38c1ddd37fea` | 2026-07-02T22:38:21+09:00 | PROBE_READY | PROBE_READY/PlanA.probe | 0 | None |  | trading_bot.v2_register_trade_ready |
| `cand_dcf120d8c39a2f7d72c4` | 2026-07-02T22:38:21+09:00 | PROBE_READY | PROBE_READY/PlanA.probe | 0 | None |  | trading_bot.v2_register_trade_ready |
| `cand_ecfe9783b3bb34728d63` | 2026-07-02T22:38:21+09:00 | PROBE_READY | PROBE_READY/PlanA.probe | 1 | 10927 | NO_SIGNAL | trading_bot.trade_ready_no_submit |
| `cand_2a761536d1904598b12a` | 2026-07-02T22:54:33+09:00 | PULLBACK_WAIT | PULLBACK_WAIT/PathB.wait | 0 | None |  | trading_bot.v2_register_trade_ready |
| `cand_af8fd9becc2f39bd6ad5` | 2026-07-02T22:54:33+09:00 | PULLBACK_WAIT | PULLBACK_WAIT/PathB.wait | 0 | None |  | trading_bot.v2_register_trade_ready |
| `cand_cd7fb639a76a3d0c43f9` | 2026-07-02T22:54:33+09:00 | PULLBACK_WAIT | PULLBACK_WAIT/PathB.wait | 0 | None |  | trading_bot.v2_register_trade_ready |
| `cand_ea97de8ea7e659c4f6eb` | 2026-07-02T22:54:33+09:00 | PULLBACK_WAIT | PULLBACK_WAIT/PathB.wait | 0 | None |  | trading_bot.v2_register_trade_ready |
| `cand_766dc4da8c73a5e7d6f6` | 2026-07-02T23:10:38+09:00 | PULLBACK_WAIT | PULLBACK_WAIT/PathB.wait | 0 | None |  | trading_bot.v2_register_trade_ready |
| `cand_e3430c501cc1b7564f55` | 2026-07-02T23:10:38+09:00 | PULLBACK_WAIT | PULLBACK_WAIT/PathB.wait | 0 | None |  | trading_bot.decision_event |
| `cand_e267e5cb14ae8ffe1295` | 2026-07-02T23:26:47+09:00 | WATCH | HARD_BLOCK/ | 0 | None |  | trading_bot.decision_event |

### KR 003490 / dec_20260703_KR_003490_091a0409

- canonical: route=`path_b` path_type=`claude_price` strategy=`claude_price` origin_action=`PULLBACK_WAIT`
- fill: event_id=`11103` at=`2026-07-03T02:00:11+00:00` entry=28850.0 pnl_net=0.8299 close_reason=`CLOSED_CLAUDE_SELL`
- expected candidate route: `PathB.wait`
- classification: `correct_candidate_fill`
- linked_candidate_count=3 filled_candidate_count=1 correct_candidate_count=1 wrong_filled_count=0 no_submit_and_filled_count=0

| candidate | known_at | action | route | filled | event | no_submit | source |
|---|---|---|---|---:|---:|---|---|
| `cand_0595774383c5928ba41a` | 2026-07-03T10:27:19+09:00 | PULLBACK_WAIT | PULLBACK_WAIT/PathB.wait | 1 | 11103 |  | trading_bot.v2_register_trade_ready |
| `cand_cbf2f5401a7e8bb7753c` | 2026-07-03T10:58:10+09:00 | WATCH | WATCH/ | 0 | None |  | trading_bot.decision_event |
| `cand_da80550ca409fd1e9d3e` | 2026-07-06T09:24:26+09:00 |  | / | 0 | None |  | trading_bot.decision_event |

### US NVDA / dec_20260706_US_NVDA_b4e1445e

- canonical: route=`path_b` path_type=`claude_price` strategy=`claude_price` origin_action=`PULLBACK_WAIT`
- fill: event_id=`11199` at=`2026-07-06T14:43:50+00:00` entry=195.9989 pnl_net=-2.4127 close_reason=`CLOSED_LOSS_CAP`
- expected candidate route: `PathB.wait`
- classification: `wrong_row_filled_no_correct_row`
- linked_candidate_count=3 filled_candidate_count=1 correct_candidate_count=0 wrong_filled_count=1 no_submit_and_filled_count=0

| candidate | known_at | action | route | filled | event | no_submit | source |
|---|---|---|---|---:|---:|---|---|
| `cand_5b18788cafd98954a752` | 2026-07-06T23:07:03+09:00 | WATCH | WATCH/WATCH | 1 | 11199 |  | trading_bot.v2_register_trade_ready |
| `cand_2eb10ae3f739a40f3a60` | 2026-07-06T23:39:31+09:00 | WATCH | WATCH/ | 0 | None |  | trading_bot.decision_event |
| `cand_d30498a8fcdbdade4a6f` | 2026-07-07T22:22:10+09:00 |  | HARD_BLOCK/ | 0 | None |  | trading_bot.decision_event |

### US SCHG / dec_20260715_US_SCHG_af10491b

- canonical: route=`unknown` path_type=`` strategy=`us_schg_bil_trend_v1` origin_action=``
- fill: event_id=`11732` at=`2026-07-15T14:06:38+00:00` entry=34.81 pnl_net=None close_reason=`CLOSED_CLAUDE_INTRADAY_SELL`
- expected candidate route: ``
- classification: `external_strategy_no_candidate_expected`
- linked_candidate_count=0 filled_candidate_count=0 correct_candidate_count=0 wrong_filled_count=0 no_submit_and_filled_count=0
- same_ticker_prompt_count=0

### US SCHG / dec_20260716_US_SCHG_d0066321

- canonical: route=`unknown` path_type=`` strategy=`us_schg_bil_trend_v1` origin_action=``
- fill: event_id=`11781` at=`2026-07-16T13:37:29+00:00` entry=34.7599 pnl_net=None close_reason=``
- expected candidate route: ``
- classification: `external_strategy_no_candidate_expected`
- linked_candidate_count=0 filled_candidate_count=0 correct_candidate_count=0 wrong_filled_count=0 no_submit_and_filled_count=0
- same_ticker_prompt_count=0

### KR 275280 / dec_20260720_KR_275280_de073acc

- canonical: route=`unknown` path_type=`` strategy=`kr_factor_trend_v1` origin_action=``
- fill: event_id=`11800` at=`2026-07-20T00:07:22+00:00` entry=41960.0 pnl_net=None close_reason=``
- expected candidate route: ``
- classification: `external_strategy_no_candidate_expected`
- linked_candidate_count=0 filled_candidate_count=0 correct_candidate_count=0 wrong_filled_count=0 no_submit_and_filled_count=0
- same_ticker_prompt_count=0

### KR 275300 / dec_20260721_KR_275300_584be5e3

- canonical: route=`unknown` path_type=`` strategy=`kr_factor_trend_v1` origin_action=``
- fill: event_id=`11805` at=`2026-07-21T00:06:52+00:00` entry=28720.0 pnl_net=None close_reason=``
- expected candidate route: ``
- classification: `external_strategy_no_candidate_expected`
- linked_candidate_count=0 filled_candidate_count=0 correct_candidate_count=0 wrong_filled_count=0 no_submit_and_filled_count=0
- same_ticker_prompt_count=0
