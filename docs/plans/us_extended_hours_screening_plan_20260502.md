# US Extended-Hours Screening Plan - 2026-05-02

## Goal

Design a dedicated premarket/after-hours discovery path for US stocks so the bot can know which names are moving before the regular US session opens.

This is an analysis and implementation plan only. It must not change live trading behavior until data quality, execution risk, and backtest/shadow results are reviewed.

## Background

The 2026-05-01 US session showed that several large movers were already up before or near the regular-market open:

- `QCOM`, `GKOS`, and `SFM` were already showing large gains in the first US screen near the open.
- `TWLO` was first detected after the open with a large existing gain, then continued higher.
- The main missed opportunity was not the full gap move. It was the post-gap continuation after the ticker was already visible.

If the system only observes regular-session screeners, premarket moves are discovered late. They appear as a large gap at or after the open.

## Key Question

Can we reliably identify strong US premarket movers early enough to prepare watchlists, without chasing thin extended-hours prints that fail after the regular session opens?

## Non-Goals

- Do not auto-buy purely because a ticker is up in premarket.
- Do not replace regular-session ORP, gap-pullback, or PathB price-plan logic.
- Do not use a single thin premarket quote as a trade-ready signal.
- Do not increase live order size based only on extended-hours movement.
- Do not add live broker/order calls during the research phase.

## External-Hours Windows

Use KST session windows and keep daylight-saving handling explicit:

- US premarket, daylight-saving period: approximately `17:00-22:30 KST`
- US regular session, daylight-saving period: approximately `22:30-05:00 KST`
- US after-hours, daylight-saving period: approximately `05:00-09:00 KST`

Korea Investment & Securities has supported expanded US trading hours in its retail environment. The OpenAPI data and order coverage must be verified separately before relying on it for automated scanning.

Reference links:

- KIS Open API service page: https://www.trueetn.com/main/customer/systemdown/OpenAPI.jsp
- KIS expanded US trading-hour coverage report: https://view.asiae.co.kr/en/article/2023051809584450369

## Data Sources To Evaluate

### 1. KIS OpenAPI

Questions:

- Does the overseas stock quote endpoint return premarket and after-hours prices?
- Does the WebSocket overseas quote stream include extended-hours prints?
- Are bid/ask and volume available in extended hours?
- Does it provide ranking/search for US premarket gainers, or only per-symbol quote lookup?
- Are the prices real-time, delayed, or dependent on market/exchange entitlement?

Validation:

- Query known liquid symbols before regular open: `NVDA`, `TSLA`, `AAPL`, `QCOM`.
- Compare KIS quote values against another source at the same timestamp.
- Confirm whether fields distinguish regular, premarket, and after-hours sessions.

### 2. Current US Screener Sources

The current US screener path is not a KIS premarket ranking path. It primarily relies on the existing US screener flow and fallback data sources.

Questions:

- Can the current provider expose `preMarketPrice`, `preMarketChange`, or equivalent fields?
- Can it return premarket gainers before `22:30 KST`?
- Is premarket volume available?
- Does it include OTC/low-quality names that need filtering?

### 3. Additional Market-Data Providers

Evaluate only if KIS/current sources are insufficient:

- FMP premarket gainers and quote fields
- Finnhub quote and market-news context
- Polygon or Nasdaq data for extended-hours aggregates
- IBKR/Alpaca if account/data entitlement exists

## Required Candidate Fields

For every extended-hours candidate, store:

- `ticker`
- `name`
- `market`
- `session_date`
- `source`
- `detected_at`
- `extended_session`: `premarket` or `afterhours`
- `extended_price`
- `regular_prev_close`
- `extended_change_pct`
- `extended_volume`
- `extended_dollar_volume`
- `bid`
- `ask`
- `spread_pct`
- `last_regular_close`
- `news_or_earnings_flag`
- `liquidity_bucket`
- `risk_tags`
- `first_detected_at`
- `last_detected_at`
- `seen_count`

Derived fields:

- `gap_vs_prev_close_pct`
- `premarket_mfe_pct`
- `premarket_mae_pct`
- `open_gap_pct`
- `open_to_30m_return_pct`
- `open_to_60m_return_pct`
- `post_open_mfe_pct`
- `post_open_drawdown_pct`

## Storage Plan

Add a research-only log first:

- `logs/extended_hours/YYYYMMDD_US_premarket_candidates.jsonl`
- `state/extended_hours_US_YYYYMMDD.json`

Do not write into operational selection state during the first phase.

## Phase 1. Shadow Collection

Run a premarket scanner every 5-10 minutes during `17:00-22:25 KST`.

Collect:

- Top premarket gainers
- Top premarket dollar-volume names
- Current regular watchlist names
- Prior-session strong names
- Earnings/news names already known to the system

Filter out:

- Price below configured minimum
- Low dollar volume
- Extreme spread
- OTC or unsupported symbols
- Symbols with missing quote integrity

Output only shadow logs and dashboard visibility.

## Phase 2. Open-Behavior Study

For each premarket candidate, measure:

- Did the move hold into the regular open?
- Did it fade in the first 5/15/30/60 minutes?
- Did it offer a better pullback entry than immediate open buy?
- Was the best entry premarket, open, ORP, or later continuation?
- How often did premarket top gainers become valid regular-session `TRADE_READY`?

Classify patterns:

- `premarket_gap_hold`
- `premarket_gap_fade`
- `premarket_pullback_reclaim`
- `premarket_thin_print`
- `premarket_news_continuation`
- `premarket_event_risk`

## Phase 3. Decision Contract Design

Premarket data should influence preparation, not direct live buying.

Possible runtime uses after validation:

1. Add to regular-session watchlist before open.
2. Raise priority for the first regular-session rescreen.
3. Allow small probe only after regular-session confirmation.
4. Add stricter filters for names that gap too far on weak volume.
5. Add `premarket_context` into Claude selection prompt.

Suggested first live rule after shadow validation:

If a ticker is:

- premarket gainer with `extended_change_pct >= 8%`
- `extended_dollar_volume` above threshold
- spread below threshold
- no hard event-risk veto
- still holding above VWAP or premarket midpoint after regular open
- regular-session volume confirms

Then allow a `WATCH_PRIORITY` upgrade, not automatic `TRADE_READY`.

## TWLO Reference Scenario

TWLO should be used as a study case:

- It was already strong when first detected.
- It did not require buying the first print of the day.
- The missed trade was the later post-gap continuation after a valid signal.

The extended-hours scanner should have placed TWLO on a high-priority watchlist before the regular-session decision path. The trade still should have required regular-session confirmation.

## Acceptance Criteria

Before enabling any live behavior change:

- At least 10 US sessions of shadow premarket data.
- Data source reliability report with missing/late/stale quote rates.
- Spread and volume quality report.
- Comparison of premarket immediate-buy vs regular-session confirmed-entry outcomes.
- Clear rule showing which premarket patterns have positive expectancy.
- No live order path touched by Phase 1 shadow collection.

## Open Questions

- Which provider gives the most reliable extended-hours top-gainer ranking?
- Does KIS OpenAPI provide enough extended-hours data for ranking, or only per-symbol lookup?
- What minimum premarket dollar volume is needed for tradable quality?
- Should earnings/news names have separate rules from non-news momentum?
- Should after-hours movers feed the next day premarket watchlist?
- How should daylight-saving changes be represented in scheduler code?

## Initial Implementation Tasks

1. Verify KIS extended-hours quote behavior with direct API calls outside the restricted sandbox.
2. Add a provider adapter interface for extended-hours candidates.
3. Add shadow JSONL storage.
4. Add dashboard section for premarket watch candidates.
5. Add a post-open outcome updater.
6. Produce the first 10-session analysis report before any trading rule change.

