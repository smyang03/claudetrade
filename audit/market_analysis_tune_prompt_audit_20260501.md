# Market Analysis / Tuning Prompt Audit - 2026-05-01

## Scope

Reviewed:

- `phase1_trainer/digest_builder.py`
- `universe_manager.py`
- `minority_report/analysts.py`
- `minority_report/tuner.py`
- `strategy/param_tuner.py`
- `trading_bot.py`
- `logs/raw_calls/*.json`
- `data/daily_digest/*.json`
- `data/ml/decisions.db`
- `data/ticker_selection_log.db`

Goal:

- Identify why market analysis and tuning explanations repeatedly cite specific tickers such as `NVDA`, `AAPL`, `GOOGL`, `AMD`, `AMZN`, `INTC`.
- Separate model weakness from prompt/data-contract weakness.
- Propose safer prompt and data-contract improvements before changing live behavior.

## Current Prompt Inventory

### 1. Morning Market Analysts

Code path:

- `trading_bot.py` builds digest with `build_kr_digest` / `build_us_digest`.
- `digest_to_prompt()` converts it to a text block.
- `minority_report/analysts.py:get_three_judgments()` calls bull, bear, neutral R1 and then R2.

Main prompt shape:

- persona-specific role
- shared decision contracts
- portfolio context
- memory / correction guide
- full daily digest text
- JSON response schema

Important behavior:

- Bull persona says: if two or more technical signals exist, choose `MODERATE_BULL` or above.
- The wording does not clearly say whether this is a market-wide rule or per-stock rule.
- Since the digest is a long list of per-stock technical lines, Claude naturally applies the rule to named stocks and cites examples.

### 2. Daily Digest

Code path:

- `phase1_trainer/digest_builder.py`
- `universe_manager.py`

Default hardcoded tickers:

- US: `NVDA`, `TSLA`, `AAPL`, `GOOGL`, `NFLX`
- KR: `005930`, `068270`, `035420`, `035720`, `005380`, `051910`

Dynamic universe behavior:

- `build_universe_from_candidates()` always prepends core tickers before dynamic candidates.
- `digest_to_prompt()` prints technical rows in dict order.
- Therefore core tickers are always first in the prompt when dynamic universe is enabled.

This is not purely "hardcoded market analysis", but it creates a strong ordering and salience bias.

### 3. Intraday Tuner

Code path:

- `trading_bot.py` builds `current_state`.
- `minority_report/tuner.py:tune()` builds prompt.

Tuner prompt includes:

- morning mode
- morning Bull key reason
- morning Bear key reason
- brain summary
- current index change
- current 30m slope
- volume trend
- alerts
- positions
- runtime overrides
- execution profile
- ops review

Missing from tuner prompt:

- current market breadth
- current sector/industry breadth
- current GC/DC count
- current overbought/oversold count
- current top movers vs morning top movers
- current VIX/DXY/HYG/TNX delta

Result:

- Tuning keeps reusing morning ticker-heavy reasons because no fresh ticker/breadth evidence is supplied.
- On 2026-05-01 US, tune 90m through 270m repeatedly carried the same morning Bull/Bear phrases.

### 4. Param Tuner

Code path:

- `strategy/param_tuner.py`

Prompt includes:

- market
- mode
- VIX
- USD/KRW
- analyst average confidence
- base/regime/perf/guard params
- recent strategy performance

Observed DB output:

- Recent rows repeatedly say "VIX 부재", "데이터 공백", or rely mostly on recent strategy win rate.

Missing:

- breadth/regime summary
- sector distribution
- index/credit/rate context with null-safe handling

## Log Findings

### US Morning Analyst Raw Calls

Sample:

- recent `20260424+` US analyst R1 raw calls: 26 rows
- average prompt technical tickers: 32.0
- first prompt tickers consistently: `NVDA`, `TSLA`, `AAPL`, `GOOGL`, `NFLX`

Top prompt tickers:

- `NVDA`: 26
- `TSLA`: 26
- `AAPL`: 26
- `GOOGL`: 26
- `NFLX`: 26
- `NOK`: 26
- `AAL`: 26
- `AMD`: 23
- `ONDS`: 21
- `INTC`: 21

Top mentioned in analyst responses:

- `AMD`: 22
- `NVDA`: 22
- `INTC`: 15
- `AMZN`: 15
- `QCOM`: 11
- `AAPL`: 8
- `NVTS`: 7
- `ERAS`: 7
- `TSLA`: 6

Core-ticker mention share:

- 45 / 252 mentions

Interpretation:

- The model is not only citing the first five tickers.
- It is heavily citing semiconductors and extreme movers, but the always-first core block increases salience.
- This still weakens market-level analysis because named equities become proxies for the whole market.

### KR Morning Analyst Raw Calls

Sample:

- recent `20260424+` KR analyst R1 raw calls: 24 rows
- average prompt technical tickers: 36.5
- core tickers are also fixed at the top.

Top mentioned in analyst responses:

- `035420`: 16
- `051910`: 16
- `005930`: 12
- `005380`: 6
- `006345`: 6
- `057540`: 5

Core-ticker mention share:

- 53 / 133 mentions

Interpretation:

- KR is more core-heavy than US in response mentions.
- Because many KR dynamic names are less semantically familiar to Claude, it falls back to large familiar names unless strong numeric anomalies exist.

### Daily Digest Quality

Recent US daily digests:

- `2026-04-30_US.json`: 33 tickers
- GC/DC count from actual digest: 22 golden crosses, 11 dead crosses
- RSI > 70 count: 13
- RSI < 30 count: 4
- `VIX`: 0.0
- `DXY`: 0
- `HYG`: -0.34%

Important issue:

- VIX/DXY are represented as `0` instead of explicit `N/A`.
- For market analysis, `VIX 0.0` is not a valid market state. It should be treated as missing data.

Another issue:

- A real US ticker `RSI` appears in the prompt.
- This collides with the `RSI` indicator label and can confuse both extraction and model reasoning.

### Tune Raw Calls

Observed 2026-05-01 US tune calls:

- `90min`, `120min`, `150min`, `180min`, `210min`, `240min`, `270min`
- all returned `MAINTAIN`
- all reused morning Bull/Bear ticker-heavy reasoning in the prompt

Representative morning Bull reason reused:

- `NVDA(RSI 88.8+MACD GC+52주 100%), AAPL(...), GOOGL(...) ...`

Representative morning Bear reason reused:

- `NVDA/AMD/AMZN/INTC RSI 87~90 광범위 과매수 + AAPL 실적 pre ...`

Interpretation:

- Tune is anchored to morning explanations.
- It receives current index/slope/positions, but not current market breadth.
- Therefore it cannot independently decide whether the ticker-level morning thesis has decayed.

## Diagnosis

The main issue is not Claude model quality.

Estimated cause:

- 75-85% prompt/data-contract structure
- 10-15% data quality gaps
- 5-10% model behavior

Root causes:

1. Core tickers are always injected and shown first.
2. Market regime prompt asks for market mode but supplies mostly per-stock technical rows.
3. Bull rule says "2+ signals -> MODERATE_BULL" without clarifying market-level breadth threshold.
4. Bear persona is KR-centric even for US. It emphasizes VKOSPI/USDKRW/foreign flow, so US bear analysis falls back to mega-cap overbought examples.
5. Neutral has to count signals from 30+ raw ticker lines. Counts can drift because the model is doing arithmetic from text.
6. Tune prompt repeats morning ticker-heavy key reasons every cycle.
7. Current tune state lacks fresh breadth and sector evidence.
8. VIX/DXY missing data is encoded as zero.
9. Ticker `RSI` collides with RSI indicator wording.

## Recommended Direction

### Phase 1 - Add Market Breadth Summary

Do this before changing live behavior.

Add a deterministic summary block to digest:

- `universe_count`
- `advancers / decliners / unchanged`
- `golden_cross_count / dead_cross_count`
- `rsi_overbought_count / rsi_oversold_count`
- `volume_spike_count`
- `near_52w_high_count`
- `earnings_pre_count / earnings_post_count`
- `top_positive_movers`
- `top_negative_movers`
- `sector_or_category_counts`
- `data_quality_flags`

Then tell Claude:

- use supplied counts; do not recount from raw rows
- use individual tickers only as examples
- cite at most three tickers in `key_reason`

### Phase 2 - Separate Market Proxy From Selection Universe

Current universe mixes:

- market proxy names
- mega-cap examples
- dynamic trade candidates

Better split:

- market proxy layer:
  - US: SPY, QQQ, IWM, XLK, SMH, HYG, VIX, DXY, TNX
  - KR: KOSPI, KOSDAQ, futures, USD/KRW, VKOSPI, sector indexes
- mega-cap influence layer:
  - fixed core tickers with index-weight role only
- opportunity universe:
  - dynamic candidates used for selection, not as market proxy

Core names should not be removed blindly, but they should not dominate market-regime reasoning.

### Phase 3 - Rewrite Analyst Prompt Contract

Add a market-level hierarchy:

1. Decide regime from index/macro/credit/rates/breadth first.
2. Use ticker examples only to support the regime.
3. Do not infer market mode from 1-3 individual tickers.
4. If breadth and named examples conflict, breadth wins.
5. If VIX/DXY are missing, mark data quality as mixed and do not treat zero as calm.

Bull contract should change from:

- "2+ signals -> MODERATE_BULL"

to:

- "market-level MODERATE_BULL requires breadth confirmation, not just several stocks with 2+ signals"

Bear US contract should add:

- VIX level/change
- HYG credit stress
- TNX/rate shock
- DXY move
- sector ETF weakness
- breadth deterioration
- mega-cap overbought only as secondary risk

Neutral should use supplied breadth counts and should not manually count raw rows.

### Phase 4 - Rewrite Tune Prompt

Replace repeated morning key reasons with structured state:

- morning mode
- morning confidence
- morning breadth counts
- morning top positive/negative examples, max 3 each
- current index change
- current 30m slope
- current breadth delta
- current sector delta
- current VIX/DXY/HYG/TNX delta
- position PnL distribution
- previous tune action
- maintain streak
- current runtime overrides

Tune should answer:

- what changed since morning?
- did breadth confirm or reject the morning thesis?
- are current overrides still necessary?

This directly addresses repeated `MAINTAIN` reasoning.

### Phase 5 - Fix Data Quality Contracts

Required fixes:

- VIX/DXY missing should be `null` / `N/A`, not `0`.
- Add `data_quality_flags` to digest and raw log.
- Disambiguate ticker `RSI` in prompt, e.g. `ticker=RSI, name=Rush Street`.
- Preserve old digest fields for compatibility; add new summary fields only.

### Phase 6 - Shadow Evaluation

Before live change:

- run old prompt vs new prompt in shadow for 5-10 sessions
- compare:
  - mode changes
  - confidence changes
  - ticker concentration in reasons
  - count accuracy
  - tune `MAINTAIN` rate
  - token usage
  - postmortem hit/miss

Success criteria:

- fewer repeated mega-cap-only reasons
- more breadth/count-based reasons
- fewer impossible `VIX 0.0` interpretations
- no increase in parse failure
- no regression in selection/PathB contracts

## Immediate Implementation Order

1. Add deterministic `market_breadth_summary` to daily digest and prompt.
2. Change analyst prompt to use breadth first and tickers as examples only.
3. Fix US bear persona to use US risk axes.
4. Add tune current-breadth delta and maintain streak.
5. Change VIX/DXY zero handling to missing/null.
6. Add ticker disambiguation for `RSI`.
7. Shadow compare old/new analyst and tune prompts.

Do not start with model routing or full prompt split. The current problem is contract shape, not model selection.
