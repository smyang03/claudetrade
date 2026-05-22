# Candidate Pipeline Root Cause Review - 2026-05-19

- generated_at: 2026-05-19 KST
- scope: local DB/log/code review only; no broker/API/Claude calls
- primary DBs: `data/audit/candidate_audit.db`, `data/v2_event_store.db`, `data/intraday_strategy_log.db`, `data/ticker_selection_log.db`
- primary code refs: `runtime/candidate_quality_trainer.py`, `runtime/candidate_prompt_pool.py`, `audit/candidate_audit_store.py`, `trading_bot.py`

## Question

The investigation started from a practical operating question:

> The scanner found candidates, but the system did not promote some of them into buy candidates. Is the problem the market, the screener, the prompt pool, Claude selection, or PLAN_A/B execution?

The target pipeline was treated as:

```text
screener -> candidate pool / prompt cap -> Claude selection -> PLAN_A/B routing -> signal / fill / exit
```

The analysis deliberately avoided adding new categories or promotion lanes before identifying where the current structure fails.

## Executive Conclusion

The current evidence does not support widening PLAN_B, adding new categories, raising cap quotas, or changing Claude first.

The strongest conclusions are:

1. The screener is not the primary failure. It is finding candidates.
2. Historical audit rows cannot always reproduce the exact runtime decision because input snapshots were incomplete or later overwritten.
3. US scorer drift was mostly an audit input snapshot problem, not a scorer-quality discovery.
4. KR 2026-05-18 drift was mostly missing `kr_quality_score_bonus` inputs in the audit row.
5. Stored PLAN_B winners and losers often share the same recorded static features, so broad PLAN_B promotion is not justified.
6. US 09:30-10:30 entries are weak in audit unique-fill data, but lifecycle final PnL shows some later recovery, so a hard block is too aggressive.
7. KR `CLOSED_LOSS_CAP` cases are mainly bad entry timing or failed immediate momentum, not evidence that loss-cap exits are wrong.

The correct next work is:

1. Forward audit storage fix.
2. US early-entry soft tightening.
3. KR entry-pattern audit around chase / momentum decay.

## Data Coverage

Candidate audit DB coverage used in the core funnel analysis:

| market | live rows | dates | tickers | rows with 60m labels |
|---|---:|---:|---:|---:|
| KR | 10,156 | 19 | 454 | 808 |
| US | 10,897 | 22 | 699 | 725 |

Stored trainer state/score rows:

| market | stored state/score rows | labeled 60m rows |
|---|---:|---:|
| KR | 3,674 | 105 |
| US | 4,170 | 87 |

Both markets used `candidate_pool_version=trainer_quality_v1` / `prompt_pool_version=trainer_prompt_pool_v1`. This version string was too coarse because behavior changed while the version string stayed stable.

## Stage Funnel

The first pass grouped rows by where they stopped:

- `screener_not_prompt`: seen by screener but not in prompt
- `prompt_not_selected`: in prompt but not selected by Claude
- `claude_watch_only`: Claude selected for watchlist only
- `claude_ready_no_fill`: Claude ready, but no fill
- `filled`: actually filled

### KR

| stage | n | avg 60m | pos_rate | avg MAE |
|---|---:|---:|---:|---:|
| screener_not_prompt | 384 | +0.307% | 41.41% | -1.241 |
| prompt_not_selected | 183 | -0.367% | 30.60% | -1.477 |
| claude_watch_only | 189 | +0.152% | 39.68% | -1.574 |
| claude_ready_no_fill | 36 | -0.653% | 47.22% | -1.406 |
| filled | 16 | -0.706% | 56.25% | -2.029 |

Initial interpretation:

- KR has candidate leakage before the prompt pool.
- But filled trades are also weak, so this is not only a prompt visibility problem.

### US

| stage | n | avg 60m | pos_rate | avg MAE |
|---|---:|---:|---:|---:|
| screener_not_prompt | 261 | -0.183% | 45.59% | -0.626 |
| prompt_not_selected | 182 | -0.124% | 39.56% | -0.517 |
| claude_watch_only | 214 | +0.204% | 49.07% | -0.623 |
| claude_ready_no_fill | 53 | +0.420% | 58.49% | -0.188 |
| filled | 15 | -0.366% | 40.00% | -0.735 |

Initial interpretation:

- US is not mainly a prompt expansion problem.
- `ready_no_fill` looked better than filled, so entry timing / blocker audit needed separation.

## Claude Selection

Claude was not identified as the first thing to change.

| market | group | n | avg 60m |
|---|---|---:|---:|
| KR | prompt_not_selected | 183 | -0.367% |
| KR | Claude selected watch/ready | 253 | +0.039% |
| KR | Claude raw ready | 55 | -0.844% |
| KR | route executable | 12 | -3.023% |
| US | prompt_not_selected | 182 | -0.124% |
| US | Claude selected watch/ready | 296 | +0.184% |
| US | Claude raw ready | 82 | +0.237% |
| US | route executable | 12 | +0.313% |

Conclusion:

- Claude selection was not the highest-confidence root cause.
- KR route/execution looked worse than the selection layer.
- US Claude-ready rows looked useful, but some did not become good actual fills.

## Audit Reproducibility

The most important correction was discovering that current rescoring did not always reproduce the runtime-stored state.

### Stored State vs Current Row Rescore

| market | rows | state match | avg abs score delta | median delta | p90 delta |
|---|---:|---:|---:|---:|---:|
| KR | 3,674 | 89.52% | 2.396 | 2.0 | 6.263 |
| US | 4,170 | 38.94% | 8.598 | 8.0 | 16.0 |

At first glance, US looked like a major scorer drift problem. That was incomplete.

### Source Tags Reconstruction

Rows were rescored two ways:

1. using current DB row fields
2. reconstructing the scorer input from stored `source_tags_json`

| market | current row rescore match | `source_tags_json` reconstructed match |
|---|---:|---:|
| KR | 89.52% | 93.28% |
| US | 38.94% | 99.38% |

This proved that US drift was mostly audit-row input drift, not a new edge in the scorer.

### US Root Cause

US audit rows often had:

- `primary_bucket=''`
- `market_type=''`
- `liquidity_bucket` later filled as `high`, `mid`, or `low`

But the stored `source_tags_json` showed the actual scorer snapshot:

- `day_gainers`
- `day_losers`
- `most_actives`
- `unknown_liq`
- `unknown_board`

Current rescoring treated blank `primary_bucket` as `unclassified` and applied:

- `us_unclassified_penalty = -8`

Stored components had that penalty only 8 times. Current-row rescoring applied it to all 4,170 US scored rows.

US source-level recovery:

| source | current row match | source-tags reconstructed match |
|---|---:|---:|
| `trading_bot.prompt_pool_excluded` | 15.9% | 100.0% |
| `trading_bot.selection_meta` | 72.4% | 97.7% |
| `trading_bot.prompt_pool` | 80.7% | 100.0% |

Conclusion:

- US scorer drift was primarily an audit input snapshot problem.
- Historical causal analysis must use stored runtime state or reconstructed source-tag inputs, not current merged row fields.

### KR 2026-05-18 Root Cause

KR overall state match was acceptable, but 2026-05-18 was an outlier:

| date | rows | current row match |
|---|---:|---:|
| 2026-05-12 | 758 | 92.74% |
| 2026-05-13 | 733 | 94.68% |
| 2026-05-14 | 409 | 96.58% |
| 2026-05-15 | 938 | 93.39% |
| 2026-05-18 | 836 | 74.28% |

On 2026-05-18, all 836 KR scored rows had stored `kr_quality_score_bonus`.

| date | rows | quality bonus rows | avg bonus | max bonus |
|---|---:|---:|---:|---:|
| 2026-05-12 | 758 | 0 | n/a | n/a |
| 2026-05-13 | 733 | 0 | n/a | n/a |
| 2026-05-14 | 409 | 0 | n/a | n/a |
| 2026-05-15 | 938 | 0 | n/a | n/a |
| 2026-05-18 | 836 | 836 | +4.132 | +12.09 |

KR match recovery:

| method | all KR match | 2026-05-18 match |
|---|---:|---:|
| current row | 89.52% | 74.28% |
| source tags only | 93.28% | 71.65% |
| source tags + stored quality bonus | n/a | 98.44% |

Conclusion:

- 2026-05-18 KR current-row rescoring is not comparable to runtime decisions.
- Audit rows do not preserve `candidate_quality_score` and `quality_data_gaps`, so quality bonus cannot be reproduced from row fields alone.

## PLAN_A Cap Miss Reinterpretation

An earlier hypothesis was:

> PLAN_A candidates were being cut by the prompt cap.

That hypothesis was weakened after stored-state analysis.

For 2026-05-18 KR hard-cap excluded rows with 60m labels:

| stored state | rows | labeled | avg 60m | pos_rate |
|---|---:|---:|---:|---:|
| PLAN_B | 316 | 60 | +0.634% | 25.0% |
| PLAN_A | 0 labeled | 0 | n/a | n/a |

The apparent PLAN_A miss came from current rescoring turning some runtime PLAN_B rows into retro PLAN_A. Stored runtime state says they were PLAN_B at decision time.

Conclusion:

- Do not describe this as a confirmed PLAN_A cap miss.
- The correct question is why some stored PLAN_B rows later performed well.

## Stored PLAN_B Winners

KR 2026-05-18 hard-cap excluded stored PLAN_B rows with non-null 60m labels:

| metric | value |
|---|---:|
| rows | 60 |
| avg 60m | +0.634% |
| median 60m | 0.00% |
| pos_rate | 25.0% |
| avg MFE60 | +2.573% |
| avg MAE60 | -1.778% |

Unique ticker view:

| method | tickers | avg 60m | pos_rate | big winners >=3% | big losers <=-3% |
|---|---:|---:|---:|---:|---:|
| best ret per ticker | 31 | +1.206% | 22.58% | 4 | 4 |
| last known per ticker | 31 | +0.066% | 22.58% | 3 | 7 |

Top winners included:

| ticker | 60m | MFE60 | stored score | raw rank | board | quality bonus |
|---|---:|---:|---:|---:|---|---:|
| 011000 | +25.52% | +25.52% | 54.007 | 33 | KOSPI | -0.993 |
| 001740 | +17.43% | +17.43% | 64.255 | 11 | KOSPI | +9.255 |
| 021880 | +11.38% | +11.38% | 67.394 | 50 | KOSDAQ | -9.606 |
| 003280 | +3.65% | +3.65% | 57.811 | 35 | KOSPI | +2.811 |

Worst losers included:

| ticker | 60m | MFE60 | stored score | raw rank | board | quality bonus |
|---|---:|---:|---:|---:|---|---:|
| 452260 | -10.00% | 0.00% | 62.464 | 40 | KOSPI | +7.464 |
| 034220 | -7.87% | 0.00% | 54.070 | 5 | KOSPI | -0.930 |
| 018880 | -5.70% | 0.00% | 57.037 | 7 | KOSPI | +2.037 |
| 001510 | -4.93% | 0.00% | 64.384 | 47 | KOSPI | +9.384 |

The winners and losers shared nearly identical recorded static features:

- `category=unclassified`
- `liquidity=unknown_liq`
- `change_bin=0~3`
- usually `from_high=deep`
- mostly KOSPI, with one KOSDAQ winner

`kr_quality_score_bonus` did not cleanly separate winners from losers:

- `011000` and `021880` had negative quality bonus and won strongly.
- `452260` and `001510` had positive quality bonus and lost strongly.

Conclusion:

- The recorded static audit features do not explain the winners.
- The differentiating signal is likely missing from the audit row: order book behavior, real-time tape, intraday momentum continuation/decay, recent volume impulse, or entry-time confirmation.
- Broad PLAN_B promotion is not justified.

## Trainer Rank Storage Gap

Past `prompt_pool_excluded` rows had no stored `trainer_score_rank`.

| market | excluded rows | rank stored |
|---|---:|---:|
| KR | 1,869 | 0 |
| US | 2,578 | 0 |

This blocks exact reconstruction of how far below the cap an excluded row was.

Forward-fix status:

- The `trainer_score_rank` wrapper-to-inner merge bug was fixed on 2026-05-19 in commit `0f2c5b2` (B-plan price target display and audit `trainer_score_rank` persistence fix).
- Forward data after that commit should retain excluded-row rank.
- Historical rows before the fix remain NULL and cannot be fully repaired without reconstructing the original ordered prompt pool.

Conclusion:

- Past exact cap-order analysis is limited.
- Do not re-implement this fix unless a regression is confirmed.
- Future analysis should treat historical NULL ranks as a data limitation, not as evidence that current code still drops the rank.

## US Entry Timing

Two views were compared:

1. audit unique filled trades
2. lifecycle closed events

Audit unique-fill view showed early entries were weak.

| US entry band | n | avg PnL | median PnL | pos_rate | loss_cap |
|---|---:|---:|---:|---:|---:|
| 09:30-09:45 | 12 | -0.574% | -1.083% | 25.0% | 1 |
| 09:45-10:30 | 12 | -0.554% | -1.460% | 37.5% | 5 |
| 10:30-12:00 | 17 | +1.467% | +0.643% | 58.8% | 3 |
| 12:00-14:00 | 2 | -0.346% | -0.346% | 0.0% | 0 |
| 14:00-15:30 | 3 | +0.071% | +0.248% | 66.7% | 0 |
| 15:30+ | 3 | +2.332% | +2.980% | 100.0% | 0 |

Audit-based simple tightening simulation:

| scenario | kept | removed | avg PnL | median PnL | pos_rate | loss_cap |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 44 | 0 | +0.459% | -0.064% | 45.5% | 9 |
| skip 09:30-09:45 | 36 | 8 | +0.688% | +0.021% | 50.0% | 8 |
| skip 09:30-10:30 | 28 | 16 | +1.043% | +0.216% | 53.6% | 3 |
| 10:30+ only | 25 | 19 | +1.258% | +0.643% | 60.0% | 3 |

Lifecycle closed-event view was less negative because some early entries recovered later or closed in profit after longer holds.

Conclusion:

- US early entries are weak enough to justify tightening.
- Hard blocking 09:30-10:30 is too aggressive because lifecycle PnL shows some recovery.
- Prefer size reduction or confirmation gate for 09:30-10:30.
- All US time bands in this section are US Eastern Time. Runtime code must convert from KST/UTC to ET before applying any 09:30-10:30 rule.

## KR Loss-Cap Review

Lifecycle closed trades:

| market | closed events | avg PnL | pos_rate |
|---|---:|---:|---:|
| KR | 48 | -1.015% | 29.17% |
| US | 81 | +0.560% | 48.15% |

KR close reasons:

Important counting note:

- This table uses lifecycle `CLOSED` events from `data/v2_event_store.db`.
- Earlier audit unique-filled-trade analysis showed roughly 9 KR `CLOSED_USER_MANUAL` trades because it grouped duplicated audit rows by fill identity.
- The two counts are not contradictory; they use different grains. Use lifecycle event counts for close-reason event review, and unique filled trades for trade-level PnL review.

| close reason | n | avg PnL | pos_rate |
|---|---:|---:|---:|
| CLOSED_USER_MANUAL | 18 | -1.524% | 16.67% |
| CLOSED_CLAUDE_PRICE_PRE_CLOSE | 9 | -0.007% | 44.44% |
| CLOSED_LOSS_CAP | 8 | -2.860% | 0.00% |
| CLOSED_TRAILING_STOP | 5 | +1.492% | 40.00% |
| CLOSED_PROFIT_FLOOR | 3 | +0.310% | 100.00% |
| CLOSED_HARD_STOP | 2 | -8.599% | 0.00% |

KR loss-cap details:

| date | ticker | entry band | hold min | PnL | notes |
|---|---|---|---:|---:|---|
| 2026-05-06 | 452190 | 12:00-14:00 | 10.75 | -2.391% | immediate failure |
| 2026-05-07 | 078150 | 09:30-10:30 | 5.60 | -3.210% | candidate later had strong 60m MFE |
| 2026-05-07 | 001440 | 09:30-10:30 | 12.68 | -2.264% | weak after entry |
| 2026-05-07 | 024840 | 10:30-12:00 | 2.77 | -3.648% | candidate had 60m MFE but entry failed |
| 2026-05-08 | 010170 | 09:30-10:30 | 32.22 | -2.799% | MFE 0 in position metadata |
| 2026-05-08 | 054450 | 09:30-10:30 | 31.78 | -2.436% | weak after entry |
| 2026-05-08 | 264850 | 14:00-15:00 | 31.38 | -3.498% | late failed entry |
| 2026-05-11 | 078150 | 10:30-12:00 | 13.32 | -2.629% | MFE 0 in position metadata |

Aggregate:

| metric | value |
|---|---:|
| KR loss-cap count | 8 |
| avg PnL | -2.860% |
| avg hold | 17.56 min |
| zero/unknown MFE count | 8 |

Conclusion:

- Loss-cap is not the root problem. It is cutting positions that failed almost immediately.
- The problem is entry quality: entering after momentum decay, chasing too late, or entering without sufficient confirmation.
- Cases like `078150` and `024840` show that the candidate can be good while the actual entry is bad.

## What Not To Do

This analysis ruled out several tempting but unsafe changes:

1. Do not broadly promote PLAN_B.
2. Do not create new live categories to hide weak causality.
3. Do not raise cap quota before audit reproducibility is fixed.
4. Do not change Claude selection first.
5. Do not treat current-row retro state as historical truth.
6. Do not interpret 2026-05-18 KR as confirmed PLAN_A cap miss.

## Recommended Next Work

### P0: Audit Forward Fix

Store the exact runtime scorer input and config used at decision time.

Fields already present in the schema and still important to preserve correctly:

- `raw_rank`
- `trainer_score_rank` - forward rank-loss bug fixed in commit `0f2c5b2`; historical `prompt_pool_excluded` rows remain NULL
- `trainer_score_components_json`
- `source_tags_json`
- `candidate_pool_version`
- `prompt_pool_version`
- `source_file`

Fields that are missing or too coarse and should be added or strengthened:

- `scorer_input_snapshot_json`
- `candidate_quality_score`
- `quality_data_gaps`
- `category` / `primary_bucket` as used by scorer
- `liquidity_bucket` as used by scorer
- `market_type` as used by scorer
- scorer code version
- env/config hash
- input schema version

Use the existing `source_file` semantics instead of adding a new source discriminator unless the current values become ambiguous:

- screener-quality source files, including `trading_bot.screener_quality` or `logs/screener_quality/...`: historical or screener-side quality rows
- `trading_bot.prompt_pool_excluded`: actual runtime prompt-cap exclusion
- `trading_bot.prompt_pool`: actual runtime prompt inclusion
- `trading_bot.selection_meta`: Claude selection metadata

The main forward gap is not source separation itself; it is preserving the exact scorer input snapshot and quality-score inputs that produced the stored state.

### P1: US Early Entry Soft Tightening

Do not hard-block 09:30-10:30 ET. Use one of:

- smaller size before 10:30
- confirmation requirement before 10:30
- stricter loss-cap / retry cooldown for early failed entries

The audit unique-fill simulation supports tightening, but lifecycle final PnL argues against a blanket ban.

Implementation caution:

- `09:30-10:30` means US Eastern Time, not KST.
- Any live gate should convert `now` / fill candidates into ET before checking the early-entry window.

### P1: KR Entry Pattern Audit

Add or review entry-time features for filled trades and loss-cap candidates:

- price change since first seen
- price change since first ready
- entry price vs first seen / first ready price
- post-open momentum state
- 1m / 3m / 5m volume impulse
- VWAP reclaim / rejection
- order book support if available
- whether entry followed a stale ready signal
- whether entry was above the recent micro high

The current static screener fields do not separate winner PLAN_B from loser PLAN_B.

### P2: Revisit Scorer Only After Forward Data

Once audit reproducibility is fixed and enough forward rows exist:

1. Compare stored state vs outcome using exact runtime inputs.
2. Test whether entry-time confirmation separates PLAN_B winners from losers.
3. Only then consider scorer changes, quotas, or category design.

## Final Position

The most valuable conclusion is negative:

- The system should not be patched by adding promotion categories.
- The system should not trust retro rescoring from mutable audit rows.
- The system should not assume Claude or cap quota is the first root cause.

The current root cause is split:

- observability gap: historical decisions are not fully reproducible from audit rows
- US execution gap: early entries need soft tightening
- KR execution gap: loss-cap trades show poor entry timing, not bad loss-cap logic

The next code changes should be small and structural: improve audit fidelity first, then tighten entry behavior where actual trade timestamps already justify it.
