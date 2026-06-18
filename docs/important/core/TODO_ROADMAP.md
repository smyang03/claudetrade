# TODO Roadmap

Updated: 2026-06-17

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

- **[A] 교훈 forward-validation 레이어 — 빌드 완료·enforce ON·축적중 (2026-06-17). 핵심 결론: 지금 검증통과 교훈 0개.** 설계 [`../LESSON_QUALITY_CONFIG_PIPELINE_DESIGN_20260617.md`], 상세 memory `project-entry-discrimination-20260616` §13~20.
  - **빌드/배선 완료:** `minority_report/lesson_validation.py`(반사실 채점+격리 store+국면조건부 apply) + `lesson_scoring.py`(축적, `trading_bot.session_close` hook=config 무관 항상 축적) + `tuner.py` hook(enforce 시 bounded `entry_priority_cutoff_adjust ±0.05` 반영). config `.env.live`+`config/v2_start_config.json` enforce ON(테스트 31 + preflight ok). **재시작 시 세션마다 자동 축적.**
  - **honest 결론(측정으로 확정): 적용할 검증 교훈이 현재 0개.** watch_only=약함(would_be 비용미달=marginal), **unanimous=국면 confound**(KR −13%는 만장일치 아니라 KR/risk_on 국면 −12.25%가 정체, gain +0.82 only), signal_fired=guard성격. **= 검증기계가 헛것을 정직하게 거름(실패 아님).**
  - **전략 분기(니말이 맞다, 운영자 합의): "새로 쌓기"는 신호를 *만드는* 게 아니라 *있으면 잡는* 것.** 시스템 행동(selection/전략) 안 바뀌면 깨끗한 데이터로도 "확정 없음" 반복 가능. **→ 며칠 축적 모니터(`[lesson validation] session-close 재채점 N셀` 로그). valid 안 뜨면 lesson 레이어가 아니라 *selection/전략 자체*를 손대야 한다는 신호.**
  - **남은 후속(저우선):** signal_fired·unanimous 별도 스코어링 패밀리(단 unanimous는 confound로 가치 의문) / 컴포넌트② per-session 지연검증 상태기계 / DB 자동갱신은 session_close hook으로 배선 완료.
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
