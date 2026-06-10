# TODO Roadmap

Updated: 2026-06-10

Compact active backlog only. Details and verification notes live in [../ACTIVE_WORK.md](../ACTIVE_WORK.md) and [../IMPROVEMENT_WORKLIST_20260607.md](../IMPROVEMENT_WORKLIST_20260607.md). Do not keep separate active plan files under `docs/reports/`.

## P0

| Area | Task |
| --- | --- |
| V2 performance sync | Back up DBs, rerun live dry-run, execute live sync, verify audited broker backfill rows and new columns. |
| Performance reports | Recalculate KR/US, PathA/PathB, strategy vs audited backfill, portfolio-realized vs learning-allowed views. |
| Ticker selection DB | Review remaining 23 contaminated live traded rows: 10 watch-only split candidates, 3 time-delta rows, 1 legacy-only IREN row, and 10 no-touch exclusions. |
| Candidate audit source | Standardize `candidate_source` fallback for new live audit rows; do not bulk-mutate legacy rows without audited remediation. |
| Candidate audit outcomes | Clear or explain `daily_pending=1551` before using daily candidate outcomes as KR selection evidence. |
| Live ops reflection | Confirm runtime drift is gone, `KR_PATHB_SELECTION_RECONCILE_MODE` runtime snapshot is `enforce`, and broker truth snapshots are fresh after restart/refresh. |
| KR/KIS evidence | Split ticker-level fail-closed from session/provider degraded warning while preserving hard fail-closed for full evidence outages. |

## P1

| Area | Task |
| --- | --- |
| KR post-carry evaluation | Measure daily `trade_ready`, signal, trade, `NO_SIGNAL`, and watch-only transitions after KR carry. |
| KR signal quality | Analyze `trade_ready -> NO_SIGNAL` by strategy, including ORP selection-time versus entry-window expiry, before changing KR-only thresholds or strategy order. |
| KR exposure/ranking | Review prompt cap, excluded candidates, and watch misses; consider only bounded overlay exposure. |
| Lessons | Add basis metadata and `truth_status` to lesson candidates after refreshed ledger sync; do not auto-promote to `state/brain.json`. |
| Hold advisor cost/risk | Review `PRE_CLOSE_CARRY` challenge cost, pending intraday retry state, missed-runup bucket reporting, and read-only PathB block reporting. |
| Existing audit backlog | Keep actual-prompt outcomes, entry/exit shadow, bucket/source/score quality, zero-holding fixtures, PathB TTL/order matching, sizing reason QA, canonical fallback exclusion, guard tests, tuning cleanup, and fill-truth monitoring open until direct evidence closes them. |
| US vol_ratio 입력 품질 (#7-2 후속) | US `vol_ratio` 1.0 placeholder를 실값으로. 별도 producer(일평균 거래량) + 세션 진행률 보정. 실행 영향(bucket/continuation/mean_reversion/VB 소비)이므로 live 연결 전 US PathB 성과 확인 + shadow 선행. naive 실값 금지. |

## P2 / Observe Only

- KR shadow veto design after refreshed performance ledger.
- US loss-cap cluster shadow after refreshed ledger, without mixing audited broker backfill into strategy-loss judgment.
- US KIS ranking/intraday primary only after smoke/shadow coverage, latency, rate-limit, overlap, and outcome gates.
- Prompt overlay, KR confirmation/WATCH_TRIGGER, and KR first-entry/exit overlay remain observe-only until sample gates pass.

## Protected Boundaries

- US PathB pre-close and profit ladder revenue paths.
- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard.
- PathB broker-truth entry fail-closed.
- PathB sizing reason split and one-share/early-gate policy.
- Zero-holding stale reconcile.
- KIS `remaining_qty` order normalization.
- Path A/Path B `RouteDecision`.
- Broker truth priority and market quarantine.
- `state/brain.json` no direct automatic policy write.
