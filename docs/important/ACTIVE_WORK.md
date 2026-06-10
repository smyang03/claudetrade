# Active Work

Updated: 2026-06-10

This is the single active work ledger. One-off plans and generated reports are removed after their unfinished work is absorbed here and in [core/TODO_ROADMAP.md](core/TODO_ROADMAP.md). Detailed improvement sequencing from the latest DB/code review lives in [IMPROVEMENT_WORKLIST_20260607.md](IMPROVEMENT_WORKLIST_20260607.md). Completed implementation notes belong in [core/DEVELOPED_WORK.md](core/DEVELOPED_WORK.md) or Git history, not in the active backlog.

## Scope Guard

- This cleanup does not change `.env*`, `config/v2_start_config.json`, live PathB gates, order size, max positions, cooldown, confidence, slippage, hard stops, broker truth priority, or `state/brain.json`.
- Selection quality, execution/risk behavior, broker truth, and performance-sync work stay separate.
- Protected PathB areas still require an `MD 위반 사항` report before runtime behavior changes.

## Direct Verification Snapshot

- `tools/sync_v2_learning_performance.py` has code for `audited_broker_backfill`, `portfolio_realized`, and `learning_allowed` separation, and lesson prompt injection blocks stale/manual-review `truth_status`.
- Live `data/ml/decisions.db` is not fully synced yet: `v2_learning_performance` is missing `portfolio_realized` and `strategy_attribution`; current `CLOSED_AUDITED_BROKER_SELL` rows are 5, all with `exit_price=NULL` and `learning_allowed=0`.
- Read-only sync dry-run returned `selected=550`, `filled=205`, `closed=182`, `skipped=0`, `written=0`, `update=512`, `strategy_attribution_counts.audited_broker_backfill=8`.
- `tools/audit_ticker_selection_attribution.py --mode live --market ALL --sample-limit 20` returned `traded=48`, `contaminated=23`, `missing_execution_id=23`, `watch_only_traded=14`, `exact_backfill_candidates=0`, `watch_split_reviews=10`, `no_touch=10`.
- `data/audit/candidate_audit.db` still has `daily_pending=1551` outcome rows and many `audit_sparse`/`insufficient_samples` rows, so candidate audit daily outcomes are not a clean learning basis yet.
- KR `trade_ready` carry is implemented in `trading_bot.py::_apply_kr_trade_ready_carry()` and covered by `tests/test_candidate_action_live_mapping.py` carry/veto/TTL/source tests. The carry implementation itself is no longer active work.
- General `INTRADAY_REVIEW` cooldown/daily max is implemented in `trading_bot.py` with `skipped_daily_max_regular`, `skipped_cooldown_regular`, and emergency bypass reasons. The throttle implementation itself is no longer active work.
- KR/KIS evidence logs count provider/prefetch timeout and KIS 500 errors, but there is still no `session_evidence_degraded` split from ticker-level `fail_closed`.
- Static live ops config is aligned in code: `US_EARLY_ENTRY_SOFT_GATE_END_MIN=60` and `start_live_stack.bat` runs broker truth scheduler with `--refresh-interval-min 2 --ttl-sec 180`. Runtime restart/freshness still needs live preflight confirmation.
- KR PathB reconcile config is aligned in current files (`KR_PATHB_SELECTION_RECONCILE_MODE=enforce` in `.env.live` and `config/v2_start_config.json`), but the latest live runtime snapshot `logs/config/effective_config_20260606_024649_live.redacted.json` still has `KR_PATHB_SELECTION_RECONCILE_MODE=shadow`. No code path was found that rewrites it to shadow after startup, so treat this as restart/startup-env/config-path verification first.
- Recent KR selection evidence shows `trade_ready=8`, `signal_fired=0`, `traded=0` for 2026-06-01 through 2026-06-05. Recent trade-ready rows are `gap_pullback=5`, `opening_range_pullback=2`, `mean_reversion=1`, all no-signal.
- KR ORP strategy logs are dominated by `orp_entry_window_expired=395`, so KR `NO_SIGNAL` review must include ORP selection-time versus entry-window expiry analysis before any ORP window/threshold change.
- System synthesis on 2026-06-07 promotes KR `NO_SIGNAL` / ORP timing reporting from P1 to P0 because it is now a gating analysis before any KR live expansion or strategy threshold work. Five recent sessions are only a smoke/reproduction window; primary judgment must use a 30-day window and full available live history.
- PathB miss quality shows US `INVALID_PRICE` cancel rows `n=29`, `zone_reentered=26`, `avg_mfe_30m=+1.222%`; this is a P0 diagnostics/reporting item, not a broker-truth or sizing-policy relaxation item. Full available `pathb_miss_quality` is the baseline window, with recent-window comparison added for drift.
- Working-tree implementation on 2026-06-07 added read-only reports `tools/kr_nosignal_orp_report.py` and `tools/pathb_invalid_price_miss_report.py`, with focused tests in `tests/test_kr_nosignal_orp_report.py` and `tests/test_pathb_invalid_price_miss_report.py`.
- KR no-signal read-only live run reproduced recent `selection=1299`, `trade_ready=8`, `signal_fired=0`, `traded=0`, `no_signal=8`. Primary 30-day run returned `trade_ready=37`, `signal_fired=6`, `traded=6`, `no_signal=31`; full available returned `trade_ready=130`, `signal_fired=24`, `traded=20`, `no_signal=106`.
- PathB `INVALID_PRICE` read-only live run reproduced US full-available baseline `n=29`, `zone_reentered=26`, `zone_reentered_rate=89.66%`, `avg_mfe_30m_pct=1.2224`, `avg_mae_30m_pct=0.0294`. The same rows are also the current 30-day recent window because the table's available US `INVALID_PRICE` history is 2026-05-11 through 2026-05-26; all 29 rows are `plan_current_missing` with follow-up quotes available, and all are `cent_tick_plausible`.
- Candidate audit schema has `candidate_source`, but recent live rows still have it mostly blank while `source_file` is populated. Treat source attribution fallback for new audit rows as a separate code/test item from ticker-selection execution attribution review.

## P0 / Do First

| Area | Remaining Work | Acceptance |
| --- | --- | --- |
| V2 performance sync | Back up `data/ml/decisions.db` and `data/v2_event_store.db`, rerun live dry-run, execute `python tools/sync_v2_learning_performance.py --market ALL --runtime-mode live`, then verify audited broker sell rows. | Post-write DB has `portfolio_realized` and `strategy_attribution`; audited broker backfill rows have native exit price/qty where available, `portfolio_realized=1`, `strategy_attribution='audited_broker_backfill'`, and `learning_allowed=0`. |
| Performance report recalculation | Recompute KR/US, PathA/PathB, `strategy` vs `audited_broker_backfill`, `portfolio_realized=1`, and `learning_allowed=1` views after sync. | Realized portfolio loss includes audited broker backfill, while strategy PF/promotion/lesson inputs exclude non-strategy or `learning_allowed=0` rows. |
| Ticker selection attribution | Review the 10 `watch_only_traded` split candidates, 3 time-delta rows, and legacy-only `selection_log_id=7742` IREN row. Keep no-touch rows excluded from learning rather than auto-fixing them. | No `watch_only` row is flipped into `trade_ready=1`; only causal execution evidence may create/link a separate execution row; remaining no-touch rows are excluded in analysis queries. |
| Candidate audit source attribution | Standardize `candidate_source` fallback for new live audit rows across prompt, excluded, screener-filter, and runtime-filter write paths. | New rows do not leave `candidate_source` blank when `source_file`/stage source is known; existing legacy rows are not bulk-mutated without audited remediation. |
| Candidate audit outcome freshness | Run daily outcome catch-up in dry-run first, then update outcomes if counts are expected. Re-check `candidate_audit.outcome_update` and daily pending rows. | `daily_pending=1551` is reduced or explained; candidate audit daily outcomes are not used as KR selection evidence until freshness is restored. |
| Live ops runtime reflection | After live stack restart/refresh, run live preflight and broker truth scheduler once. | `US_EARLY_ENTRY_SOFT_GATE_END_MIN` runtime drift is gone, `KR_PATHB_SELECTION_RECONCILE_MODE` runtime snapshot is `enforce`, and KR/US broker truth snapshots are fresh within TTL. |
| KR/KIS evidence fail-closed split | Separate ticker hard fail-closed from session/provider degraded warning. Preserve full hard fail-closed for provider disabled, session-open resolve failure, and complete-zero cases. | `minute_complete` ticker evidence remains confirmed during partial KR timeout; missing/partial tickers alone get fail-closed; logs expose session degradation separately. |
| KR `NO_SIGNAL` / ORP report output review | Use `tools/kr_nosignal_orp_report.py` output to decide whether KR no-signal is selection timing, strategy condition, evidence quality, or risk/order/broker gate. | Any KR threshold/window proposal cites primary/full_available distributions and forward-outcome evidence; no shared strategy change proceeds without US PathB impact review. |
| PathB `INVALID_PRICE` remediation design | Use `tools/pathb_invalid_price_miss_report.py` buckets and lifecycle metadata to identify whether `current_at_plan` misses come from price provider, adapter capture, stale quote, or unit scale. | Proposed fix targets diagnostics/price-source quality only; broker-truth fail-closed, sizing reason split, slippage cap, and order submit policy remain unchanged. |

## P1 / Develop Next

| Area | Remaining Work | Acceptance |
| --- | --- | --- |
| Claude API 최적화 — selection prompt caching (2단계, 2026-06-10 검토 반영) | **실측 (2026-06-09):** Sonnet 4.6 캐시 최소 1,024 tokens (1,369 tokens 테스트 cache_creation=1370 확인). `input_tokens`는 캐시 미포함 일반 토큰만. **추가 실측 (2026-06-10):** 호출 간격 — select KR 중앙값 7.8분/US 27분, 5분내 연속 호출 KR 38%/US 24% → 기본 5분 TTL 히트율 1/4~1/3, **`ttl=1h`(쓰기 2배) 필수** (60분내 88~95%). 예상 절감 월 $5~10 (정적 prefix ~3k tokens 가정). **범위 확정:** select_tickers만. R1은 Haiku 4.5(문서상 최소 4,096, 미실측) 제외. R2는 정적부 실측 후 판단. hold_advisor 캐시는 보류 — 절감 상한 월 ~$2, prefix 인위 확장은 본말전도. (2026-06-10 완료: hold_advisor/single_symbol_judge 계약 인라인 중복 제거 + system 블록 무조건 첨부, no-op 주석 명시.) **구현 순서:** ① select_tickers 재구조화 — 정적(계약 4종+규칙+JSON 스키마)을 system 블록 + cache_control ttl=1h, 동적(candidates/digest/lesson/feedback/phase)만 user; ② 규칙 블록 보간 변수(watch_max/trade_max/slot_text)가 세션 내 불변인지 확인, 가변이면 user로 분리(아니면 prefix 무효); ③ messages.count_tokens() preflight 1,024+ 확인 + 운영 중 cache_read_input_tokens>0 로그; ④ smart skip semantic signature(ticker+action 기반) 영향 없음 확인; ⑤ single_symbol_judge messages.parse는 캐시와 분리 적용. **선행 조건:** A1 buy zone 판정(2026-06-17경) 이후 시작. paper/replay에서 재정렬 전후 동일 풀 trade_ready 일치율·parse 성공률 비교, 라이브 직행 금지. | select_tickers cache_read_input_tokens > 0; trade_ready 일치율 회귀 없음; compact parse error rate 증가 없음. |
| KR selection after trade-ready carry | Measure carry effects by day: `trade_ready_count`, `signal_fired`, `traded`, `NO_SIGNAL`, and `watch_only` transitions. | If `trade_ready` rises but `NO_SIGNAL` dominates, move to KR strategy signal review; if signals rise but orders do not, split risk/order/broker gate analysis. |
| KR screener exposure/ranking | Analyze hard-cap cutoff, prompt-pool exclusion, and watch-miss forward outcomes. | Add only bounded live-limited overlay exposure if evidence supports it; do not auto-promote discovery/overlay candidates to BUY_READY. |
| Lesson candidates | Add `basis_source`, `basis_max_session`, `basis_synced_at`, and `truth_status` to manual/pinned lessons after refreshed ledger sync. | Only refreshed ledger-backed lessons become `truth_status=fresh`; no automatic `state/brain.json` promotion. |
| Hold advisor follow-ups | Review `PRE_CLOSE_CARRY` challenge cost, add pending intraday recheck retry state machine if repeat-call risk is confirmed, connect missed-runup bucket report, and add read-only US PathB block reporting. | Hard stop/loss cap/broker safety bypass retry throttles; no PathB profit ladder, pre-close, or AUTO_SELL_REVIEW cooldown behavior changes without protected review. |
| Existing strategy-flow backlog | Keep actual-prompt outcomes, entry/exit shadow, bucket/source/score quality, zero-holding fixtures, PathB TTL/order matching QA, sizing reason split QA, canonical fallback exclusion, brain/sub-screener guards, runtime tuning cleanup, and PathB fill-truth monitoring visible until commit/QA or live DB evidence closes them. | Each item closes only with direct code/test or DB/log evidence, not by dated plan text. |
| US vol_ratio 입력 품질 (선택품질 #7-2 후속) | US 후보 `vol_ratio`가 Yahoo/FMP 스크리너에서 `1.0` placeholder로 고정(`kis_api.py`). 실값은 계측이 아니라 실행 영향 항목(`bot/bucket_classifier.py` US `>=1.1`, `strategy/continuation.py` `>=1.5`, `strategy/mean_reversion.py`, `strategy/volatility_breakout.py`가 소비). 별도 producer로 US 일평균 거래량 수집 + 세션 진행률(elapsed/total) 보정 vol_ratio 산출 → producer→writer→전략 소비처 흐름 일괄 검증. naive 실값(장중 부분거래량/일평균)은 placeholder보다 왜곡되므로 금지. | 실값을 live 전략에 연결하기 전 US PathB 성과 데이터 확인 + shadow 관찰 선행. 장중 부분거래량 보정이 검증된 producer만 소비처에 연결. selection/execution 분리 유지, US PathB 보존 영역 변경 시 `MD 위반 사항` 보고. |

## P2 / Observe Only

| Area | Rule |
| --- | --- |
| KR shadow veto | Design only after performance sync and report recalculation. Use entry-time-known features only and write shadow records, not live blocks. |
| US loss-cap cluster shadow | Recalculate after refreshed ledger. Do not mix `CLOSED_AUDITED_BROKER_SELL` into US loss-cap cluster judgment. |
| US KIS ranking/intraday primary | Keep Yahoo/FMP/AV and yfinance primary until KIS shadow/smoke coverage, latency, overlap, and rate-limit gates pass. Broker truth never falls back to Yahoo/FMP/AV. |
| Prompt overlay / PLAN_A, KR confirmation, KR first-entry/exit overlay | Observe until sample gates pass: enough trading days, labeled outcomes, concentration checks, and broker-fill-aware replay. |

## Protected Boundaries

- US PathB `CLOSED_CLAUDE_PRICE_PRE_CLOSE` and `CLOSED_PROFIT_LADDER`.
- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard.
- PathB broker-truth entry fail-closed behavior.
- PathB sizing reason split and one-share/early-gate sizing policy.
- Zero-holding stale reconcile.
- KIS order normalization with `remaining_qty` preservation.
- Path A/Path B `RouteDecision` contract.
- Broker truth priority and market quarantine behavior.
- `state/brain.json` no direct automatic policy write.

## Removed From Active

- KR `trade_ready` carry implementation: code and tests exist; only post-carry measurement remains.
- General `INTRADAY_REVIEW` cooldown/daily max implementation: code and tests exist; only operational observation and pending-recheck retry design remain.
- Static live ops code/config alignment for early soft gate and broker truth scheduler: code/config are aligned; only runtime reflection/freshness verification remains.
- KR `NO_SIGNAL` / ORP timing report implementation: working-tree read-only report and tests exist; output review remains active above before any threshold/window proposal.
- PathB `INVALID_PRICE` miss diagnostics implementation: working-tree read-only report and tests exist; remediation design remains active above and must preserve protected safety gates.
- One-off `docs/reports` follow-up plan files: remaining work is absorbed here.
