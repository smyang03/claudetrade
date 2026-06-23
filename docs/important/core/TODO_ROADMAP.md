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
| hold advisor 정식 lesson_validation 편입 (#4a, **구현 완료** 커밋 `8ae6fc8`, **🕒 장기 관찰 라벨 — 2026-06-23 운영자 결정**) | profit_guard 청산 counterfactual(SELL 실현 vs HOLD 지속 forward)을 score_cell로 채점·verdict 축적. `minority_report/hold_advisor_exit_lessons.py` + `tools/run_hold_advisor_exit_validation.py` + 세션마감 hook(토글 게이트). 토글 `HOLD_ADVISOR_EXIT_LESSON_ENABLED=false`(기본). **현황(6/23): 15행 수집·forward 8/15 충진, 셀 전부 insufficient(셀당 3~5건).** **성숙 ETA = 빨라야 2~3개월**(이유: profit_guard SELL ~2.5건/일을 market×regime로 쪼개면 셀당 ~0.6건/일 → min_wo=30 도달에 ~50거래일, **KR risk_off 등 일부 셀은 사실상 영영 미달**; + sessions≥2는 2개월+ 필요). **임계(min_wo=30)는 watch_only용이라 이 교훈엔 미스칼리브레이션이나, 보유 반사실 분산이 극심(−21~+25%/5건)해 낮추면 노이즈 valid 위험 → 낮추지 않고 장기 축적으로 간다.** **전제조건(actionable)**: `run_hold_advisor_exit_validation.py` **주기 실행**(backfill 안 돌면 forward 영영 NULL=성숙 시작 안 함, 현재 7/15 NULL). v2 market_regime full sync(현재 unknown 폴백). **단기 기대 접음 — 수개월 후 verdict 보고 시 재검토.** |
| hold advisor 국면 조건부화 (#4b, A/B 검증 후) | profit_guard '익절 우선'을 bear/weak·반전 active 국면에 한정하고 BULL+고점갱신은 러너 HOLD 강제. prior 표본이 5월 강세장 confound라 일반화 미검증(`docs/reports/hold_advisor_review_20260623.md`). **선행조건**: #4a 정식 편입 verdict 또는 A/B 도구에서 net+ 확인. 청산 행동 변경이므로 운영자 승인 + 검증 선행. |

## P2 / Observe Only

- **[A] 교훈 forward-validation 레이어 — 빌드 완료·enforce ON·축적중 (2026-06-17). 핵심 결론: 지금 검증통과 교훈 0개.** 설계 [`../LESSON_QUALITY_CONFIG_PIPELINE_DESIGN_20260617.md`], 상세 memory `project-entry-discrimination-20260616` §13~20.
  - **🔄 기대 조정 (2026-06-23 운영자 결정):** 이 레이어는 **"이기는 교훈 발굴기"가 아니라 "사후 내러티브를 거르는 진실 필터/가드"**다. 원래 의도("장종료에 적절해 보인 교훈을 비슷한 국면에 재사용")는 — "적절해 보였다"가 사후 합리화(hindsight)이고, 자동 교훈이 전부 진입(watch_only 풀기)에 관한 건데 진입은 무엣지라 — **재사용할 엣지가 교훈 스트림에 없음**으로 판명. 가치는 ① 거짓 교훈의 라이브 주입 차단(invalid_block: KR 진입 풀기 −3% 방어) ② "교훈 층이 아니라 selection/전략/실행 *행동*을 손대라"는 알람. **valid 0이 정상·성공(헛것을 안 믿는 것). "더 좋은 교훈을 기다린다"가 아니라 행동 실험(shadow: flow 게이트 등)으로 검증→재사용한다.** 레이어는 싸고 가드 가치 있어 **유지**(죽이지 않음), 단 edge-generator 기대는 접음.
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
