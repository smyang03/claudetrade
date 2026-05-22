# Active Work

Updated: 2026-05-22

This is the active work list after cleanup. Individual old plan/report files were removed unless they still provide current source evidence.

## 2026-05-22 Git/Code Review Priority

This priority order comes from the current git state, recent commits, and code inspection. Full details are in [GIT_CODE_REVIEW_PLAN_20260522.md](GIT_CODE_REVIEW_PLAN_20260522.md).

| Priority | Work | Current Finding | Improvement After Fix |
| --- | --- | --- | --- |
| P0-0 | Commit hygiene before staging | Working tree includes code/docs plus runtime state, `state/brain.json`, temp browser/profile files, generated DB sidecars, and many `state/*.json` outputs. | Commit contains only intentional source/docs/config/test changes; policy memory and runtime artifacts stay out unless explicitly approved. |
| P0-1 | Sub-screener live trigger safety | `SUB_SCREENER_ENABLED=true` and `SUB_SCREENER_TRIGGER_ENABLED=true` are in tracked start config; code can reinvoke analysts and rescreen every 15 minutes up to 5 times/session. | Runs shadow-first or with explicit operator-approved trigger gate, preflight visibility, call budget, and audit counters before affecting live candidate flow. |
| P0-2 | Broker-truth destructive reconcile verification | Plan A and PathB now remove/close local positions when fresh broker truth says zero holding. This is safer than blind retry, but depends on real KIS position/open-order/fill payload shape. | Zero-holding reconciliation is only done from verified fresh broker truth; stale/untrusted data blocks and emits risk evidence instead of mutating local positions. |
| P0-3 | PathB entry broker-truth gate live validation | Entry scan now refreshes broker truth and blocks on stale/untrusted truth; env defaults are code-level and not yet visible in `.env.example` or preflight policy. | Live entry gate behavior is observable, configurable, and covered by preflight/guardian before it can silently block or allow entries. |
| P0-4 | Profit review timeout fallback audit | Timeout now records HOLD fallback and marks learning excluded, but dashboard/canonical/reporting need to show advisor outage separately from strategy HOLD. | Operator sees timeout/fallback counts, and learning/performance metrics do not treat advisor-unavailable HOLD as strategy judgment. |
| P1-1 | Prompt pool hard-cap/evidence alignment validation | Latest commits changed KR/US hard caps and trainer prompt pool structure. Runtime now records overlap/missing evidence fields. | Next live/paper sessions prove prompt candidates, evidence fetch, READY count, and execution pool stay aligned after the cap change. |
| P1-2 | US KIS ranking screener implementation | Source requirement remains unimplemented: `screen_market_us()` still has no optional token and no KIS overseas ranking branch. | US screener uses KIS ranking first and safely falls back to Yahoo/FMP/cache without touching order/risk logic. |
| P1-3 | V2 canonical truth operational runbook | Code now has canonical performance, candidate audit live links, dashboard source labeling, and adaptive canonical preference. Daily repair/ops commands still need fixed runbook and evidence. | Live performance, adaptive params, dashboard ML digest, and candidate quality read the same canonical truth basis. |
| P1-4 | Counterfactual outcome updater schedule | Store/updater/analyzer exist, but policy review still depends on regular outcome fill and metadata-quality checks. | Blocked/watch-only candidates get 30m/60m/close outcomes so gate changes are judged by opportunity cost, not anecdotes. |
| P1-5 | KR confirmation data_quality bug and fade shadow | KR intraday features return `minute_complete`, but confirmation currently recognizes only `good`/`normal`/`ok`; fade recovery should be observed KR-only before any live route change. | KR `minute_complete` no longer fails as `kr_data_quality_not_confirmed`; `fade_recovered_shadow` is logged for KR only while US behavior and PathB operating parameters stay unchanged. |

## P0 - Live Truth And Safety

| Work | Reason | Done When |
| --- | --- | --- |
| Live start truth gate | Broker holdings, open orders, quarantine, and ORDER_UNKNOWN must override local cache. | `tools/live_preflight.py --mode live --skip-dashboard --json` and guardian show no hard fail, unresolved open buy order, or broker distrust. |
| KIS fill truth verification | Wrong full/partial/cancel payloads can corrupt position, PnL, and re-entry behavior. | WS/REST fill keys, partial fill, cancel, restart restore, and broker snapshot reconciliation are verified with real payloads. |
| V2 canonical performance table | `decisions.db` is not enough as fill/performance truth, especially for PathB. | V2 lifecycle events produce a deduped canonical performance table split by KR/US and linked to candidate quality. |
| Candidate audit live link | Candidate rows can have fill/PnL data without `execution_decision_id`. | Live decision events write `execution_decision_id`, `execution_event_id` where available, and dashboard exposes link fields. |
| Counterfactual outcome backfill | Blocked or watch-only candidates need 30m/60m/close outcomes to judge gate cost. | Trigger price, 30m/60m/close return, MFE/MAE, price source, sample count, and data-missing reason are populated. |
| Metric contract | Analysis conclusions can flip when raw/dedupe, live/paper, latest/state, and watch buckets are mixed. | Every report labels market, raw/dedupe basis, source DB, and bucket split before policy decisions. |
| Prompt overlay shadow gate | Prompt changes must not move live without evidence. | At least 10 trading days, 4 triggered days, PF > 1.0, top-day contribution < 40%, and no Plan B fallback use. |

## P1 - Execution Quality

| Work | Reason | Done When |
| --- | --- | --- |
| KR action schema and route split | `BUY_READY`, `PROBE_READY`, `PULLBACK_WAIT`, and `ADD_READY` can be confused without route/fill buckets. | Case table shows action, route, fill status, and outcome separately before gate changes. |
| KR entry timing review | Early-session and late-session buckets have shown recurring risk. | Canonical table confirms time bucket performance and gate decisions are documented. |
| RiskManager KR/US live adapter | Shadow exists, but write paths can still depend on global `self.risk`. | `_risk(market)` is used on write paths and KR/US cash, position, halt, and daily return tests pass. |
| Safety/equity audit fields | Operator needs to know broker/local/equity source and lag basis for blocks. | Safety details include `equity_source`, unrealized return, and broker lag suspicion fields. |
| US KIS ranking screener | US screener should use official KIS ranking first, with safe fallback. | KIS trade-volume and up/down ranking are normalized, cached, tested, and fallback to Yahoo/FMP remains intact. |
| KR confirmation quality fix | KR `minute_complete` is a completed evidence quality, not a policy relaxation. | `minute_complete` passes KR confirmation data-quality check, while `minute_partial`/`minute_missing` remain blocked and fade stays watch-only unless shadow data later justifies a separate live change. |
| Live config safety | Runtime order-size changes and PathB live gates need fail-closed behavior. | `/setorder` persists atomically before runtime mutation, and preflight warns if KR-on/US-on PathB policy is violated. |

## P2 - Shadow Experiments

| Work | Rule |
| --- | --- |
| Market index expansion | Add KOSPI200, KOSDAQ150, VKOSPI, Russell2000, and SOX as read-only/shadow first. No order gate until at least 2 weeks of stable data. |
| Momentum shadow labels | Keep slot-disabled momentum as counterfactual metadata until enough labeled sessions justify a live decision. |
| KR fade recovered shadow | Detect KR-only OR/VWAP-recovered fade candidates as shadow evidence first; do not enable US fade relaxation or PathB wait exceptions. |
| Hybrid-lite/watch trigger | Measure pure watch-only missed runups and relaxed-block outcomes before promotion. |
| PathB EXPIRED and sell remainder | Shadow quote refresh, zone re-entry, and partial-sell remainder handling before live behavior changes. |
| CandidateTierBook | Build as shadow state before replacing flat candidate lists. |
| KRX/BigKinds/theme injection | Add only after credentials, dry-run, normalized store, and shadow injection are stable. |

## P3 - Later Structural Work

| Work | Start Condition |
| --- | --- |
| L3 price collection inject | Manual/pinned candidates repeatedly show `HISTORY_UNAVAILABLE` for at least 2 sessions. |
| Dual runtime split | Market risk adapter and canonical performance truth are stable. |
| Brain Train mode | Policy truth and lesson candidate workflow are stable without direct `brain.json` mutation. |
| New intraday/VWAP/momentum gate | P0/P1 truth and shadow data justify a small isolated experiment. |
