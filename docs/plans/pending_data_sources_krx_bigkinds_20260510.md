# Pending Data Sources - KRX and BigKinds

## Status

Waiting for credentials or approval:

- `KRX_AUTH_KEY`
- `BIGKINDS_KEY`

## KRX Open API Plan

Target data:

- VKOSPI replacement or official fallback.
- Korean short-selling ratio by ticker.
- Foreign futures or derivatives investor flow if the approved endpoint supports it.
- Official KRX index fallback for KOSPI/KOSDAQ context.

Implementation sequence after key arrives:

1. Add key to `.env.live`.
2. Run endpoint-level dry-run without printing secrets.
3. Confirm response fields, row count, date format, market coverage, and limits.
4. Add normalized SQLite storage under `data/external_market_data.sqlite`.
5. Feed compact fields into KR supplement/digest:
   - `vkospi`
   - `short`
   - `foreign_futures`
   - `data_quality_flags`
6. Add tests with mocked KRX responses.

Main risks:

- Endpoint approval may not include all desired datasets.
- Terms may restrict commercial use, redistribution, or high-volume use.
- Date availability and official delayed data rules must be handled explicitly.

## BigKinds Plan

Target data:

- Korean market news.
- Corporate news by candidate ticker/name.
- Theme keywords and compressed sentiment proxy.

Implementation sequence after key arrives:

1. Add key to `.env.live`.
2. Dry-run current `tools.kinds.or.kr/search/news` payload.
3. Confirm schema against existing `phase1_trainer/kr_news_collector.py` assumptions.
4. Store only metadata and short excerpts/titles needed for trading context.
5. Feed compact fields into KR digest:
   - `market_news_count`
   - `corp_news_count`
   - `negative_news_count`
   - `theme_keywords`
   - top N titles
6. Add copyright-safe prompt policy tests.

Main risks:

- API approval and response schema need real-account verification.
- Full article body storage or prompt injection can create copyright and token risks.
- Sentiment logic can add noise unless kept conservative.
