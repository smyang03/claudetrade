# Active Work

Updated: 2026-06-11

This is the single active work ledger. One-off plans and generated reports are removed after their unfinished work is absorbed here and in [core/TODO_ROADMAP.md](core/TODO_ROADMAP.md). Detailed improvement sequencing from the latest DB/code review lives in [IMPROVEMENT_WORKLIST_20260607.md](IMPROVEMENT_WORKLIST_20260607.md). Completed implementation notes belong in [core/DEVELOPED_WORK.md](core/DEVELOPED_WORK.md) or Git history, not in the active backlog.

## 동결 해제 + 6/17 안건 조기 처리 (2026-06-11 운영자 지시 "전부 진행해")

당초 동결이었으나 운영자 지시로 즉시 착수 가능 항목 7건을 6/11에 전부 처리 완료:
1. ✅ 함정구간 가드 PULLBACK_WAIT 확장 (7f70a8a) — 역산 n=26 중앙값 -1.18% 근거
2. ✅ net 원장 KIS 체결가 통일 + 저장결측 3종 (cc5f7f4)
3. ✅ 일 단위 forward 라벨 백로그 자동화 (e7c3b0f) — daily_forward 0 → 51,229건
4. ✅ C1 selection 프롬프트 캐시 활성 (0745379) — 실 A/B 검증, 정적부 1,342토큰
5. ✅ 시장 급반전 감지 shadow (1ce5b9b) — enforce 전환은 6/24 판정 유지
6. ✅ session_evidence_degraded 분리 (4241e32)
7. ✅ 개장 판단 refresh 중복 방지 + guardian 테스트 env 누수 수정 (e5643c3)
→ 전체 회귀 2,424 passed, preflight FAIL 0. **봇 재시작 필요** (현 프로세스는 09:32 기동).
→ 6/17에 남는 것: A1 존 4지표 판정(데이터 필요), A1 코드 게이트 여부.
→ 6/24 판정 유지: 채널 ROI 쿼터, rel_vol 게이트 연결, mega_gap 승격, 급반전 enforce,
   PathB 진입 하한(+2% 미만 진입 평균 +0.01% — 역산에서 신규 발견) 검토.

## (이력) 변경 동결 + 관찰 주간 (2026-06-11 ~ 06-16, 운영자 확정 → 6/11 운영자 지시로 해제)

2026-06-10~11 대규모 배포(12커밋: 게이트 3종, KR off, net 원장, A1 존 규칙, rel_vol 체인,
attribution, mega_gap watch, cap 재설정 15/15/5, stop cluster 5, 1주 예외 100만) 직후라
**효과 귀속을 위해 최소 5일간 신규 변경 동결.** 발견된 개선 후보도 아래 일정으로만 처리한다.
예외: 차단급 장애(체결 고갈, 게이트 오작동, 비용 폭증)의 긴급 대응만 허용.

**관찰 주간 일과 (매일 아침, 변경 없이 측정만):**
- 신규 플랜 존 깊이 분포 (A1 준수율 — 기준가 대비 -0.5% 이하 비율)
- rvol 표기 후보의 선택률 / judge 호출 대상 구성 (단골 비중 감소 확인)
- 체결 수 (소급 시뮬 예측 "기존의 85%" 대조)
- net 손익 첫 기록들 (pnl_krw_net_est — CPNG/IONQ 청산 시)
- 일일 API 비용 (R1 Haiku 반영 후 ~$4± 예상, KR select 49콜/일 낭비 추이)

**2026-06-17 — 1차 판정일 (작업 재개, 그룹 우선순위):**

지배 원칙(2026-06-11 확정): ① 데이터가 결정한다 — 변경은 판정일에만, 그날도 표본 역산 선행.
② 경로 일관성 — 가드는 진입의 전 경로(selection·judge·bridge)에 동일하게. 신설 시 경로 체크 필수.
③ 판단보다 구조 — Claude 판정 품질은 6/10 손실 리뷰로 입증됨(IONQ judge가 A1 규칙·무효조건 명시).
투자처는 프롬프트가 아니라 게이트 일관성 → 국면 인지 → 자금 구조.

A그룹 — 6/10 손실 직접 처방 (오전):
1. 함정구간(+2~5%) 가드를 PULLBACK_WAIT까지 확장 — mega_gap 가드 패턴 재사용, judge 산출도
   selection meta 합류라 한 곳 수정으로 양 경로 커버. 6/10 손실 3건 중 2건(IONQ +4.6%/CPNG +4.8%
   진입)이 이 우회로 발생. **선행: 과거 PathB 체결 중 진입 시점 +2~5% 그룹 성과 역산 후 enforce.**
2. 저장 결측 수선 + 진입가 기준 통일 (net 원장 완결성, 2026-06-11 운영자 확정: KIS 체결가 기준):
   - **진입가 기준 = KIS 체결 평균가로 통일** — 현재 일부 fill 경로가 주문가(지정가)를 기록해
     브로커 실체결가와 불일치(CPNG 15.645 vs 15.75), net이 gross보다 후한 모순 발생
     (6/9 CPNG net -1.01% > gross -1.29%). mark_filled 전 경로 브로커 avg 우선 +
     `entry_price_source` 표기, _close_cost_meta는 브로커 포지션 단가 → plan 기록가 순.
   - judge 출력 스키마에 `reference_price` 필드 부재 → judge 출신 플랜 전부 ref=None
     (IONQ 사례 — 판단문엔 현재가 59.72 명시, 받아 적을 칸이 없었음). 스키마 추가 또는 등록 시 백필.
   - ORDER_ACKED 중 청산 시 entry 유실 — 매수 fill reconcile(주기)과 hard stop(즉시)의 레이스
     (FUN 사례: 00:38 주문→00:56 손절, net 미기록 재현 확인). mark_closed에서 plan에
     entry 없으면 브로커 체결가/포지션 단가로 백필.
   - 후속 확인: KIS 체결 내역 API에 실제 제비용 필드 존재 여부 → 있으면 수수료도 추정(0.25%)
     대신 브로커 실비용으로 전환.

B그룹 — 예정된 데이터 판정:
3. A1 존 규칙 4지표 판정 → 유지 / -0.3% 완화 / 강화 (NOK -0.74% 깊이 = 신규 플랜 첫 준수 사례)
4. A1 코드 게이트(등록 단계 zone vs 기준가 검사) 추가 여부 — 판정 결과 따라
5. candidate outcome 라벨 버그 수정 (daily_pending 1,551건 `target_at` 빈 값)

C그룹 — 비용:
6. ~~KR rescreen 임시 감속~~ — 해소 (2026-06-11 운영자 결정으로 KR PathB 재개, 30분 rescreen이 유효해짐)
7. C1 selection prompt caching 착수 (paper 검증 포함 — P1 항목 참조)
8. KR 개장 판단 중복 검토 — 09:05 세션판단 + 09:07 market_open_refresh가 2분 간격 풀세트(12콜) 중복 (일 ~$0.3)

D그룹 — 설계만 (라이브 연결은 6/24):
8. 시장 급반전 보호 미니판 — Claude invalid_if의 "broad market reverses"를 시스템이 모니터링하지
   않는 갭(IONQ 패인). 이미 수집 중인 market risk shadow(`ENABLE_MARKET_RISK_SHADOW=true`) 기록
   검토 → 임계 설계 (장중 지수 급락 시 PathB 전 보유 INTRADAY_REVIEW 트리거 + 신규 제출 일시 보류.
   강제 청산 아님 — 보호영역 비접촉). 7월 국면 스위치의 선행판.

금지 항목 (6/10 리뷰 근거): judge·청산 엔진·loss_cap -2% 변경 금지 — 손실의 원인이 아님이 확인된 곳.

**유사 시스템 차용 패턴 (2026-06-11 운영자 승인, ③은 즉시 구현 완료):**
- ③ ✅ 벤치마크 정직성 지표 — tools/benchmark_compare.py (시스템 net vs SPY/QQQ + 알파).
  첫 실측: 최근 7일 시스템 -0.78% vs SPY -4.18% = 알파 +3.4%p (하락장 방어 작동 증거).
  매주 채점·6/30 판정에 포함
- ① 테마 동시 진입 제한 → 6/17: 과거 플랜 섹터 동시성·동반 손실 역산 후 같은 세션
  같은 섹터 PathB 신규 2건 제한 게이트 (6/10 반도체 5건 동시 승인 사례)
- ② 에쿼티 커브 스로틀 → 6/24: 연속 손실일/주간 드로다운 시 사이즈 자동 절반·회복 시 복원
  (stop cluster의 다일 버전). 임계는 "연패 중 거래 기대값" 역산 후
- ④ 타임 스톱 shadow → 6/17: N세션째 손익 정체 포지션의 최종 결말 측정만 (행동 변경 없음)

**웹 리서치 차용 후보 (2026-06-12 등록, 구현은 판정일에):**
- [6/24] lesson_candidates 계층화 + 중요도 감쇠 — FinMem(arxiv 2311.13743) 차용.
  오래되고 재확인 안 된 교훈 자동 강등 → brain 자동 승격의 안전한 전 단계
- [6/24] 존 터치 시점 거래량 방향 확인 — "감소 거래량 눌림 → rvol 급증 반등" 패턴.
  rel_vol 3단계에서 judge 입력에 추가 → 칼날(IONQ형) vs 건강한 눌림 구분
- [7월 국면 스위치 설계 입력] Statistical Jump Model 국면 신호(arxiv 2402.05272) —
  지수+VIX 2변수 경량 통계 국면을 Claude consensus의 독립 제2 의견으로
- (참고) fractional Kelly 문헌이 7월 사이징 설계(고정 기반+0.7~1.5x 보정) 지지 확인.
  full Kelly 금지. TradingAgents류 다중 에이전트 구조는 기보유 — 신규 액션 없음

**~2026-06-24 — 2차 데이터 판정:**
- 채널 ROI (candidate_source 2주치) → most_actives/day_gainers 쿼터 재배분
- rel_vol 분포·예측력 검증 → 전략 게이트 연결 여부 (US PathB 보호영역, `MD 위반 사항` 절차)
- mega_gap watch forward → 진입 채널 승격 여부
- D그룹 시장 급반전 보호 라이브 연결 여부 (shadow 검토 결과 따라)

**2026-06-30 — 월말 판정 기준 (운영자 합의 2026-06-11, 감정 배제용 사전 확정):**
- 통과 기준 (둘 다 충족): ① 거래당 net 평균 >= +0.3% (수수료 후, pnl_pct_net_est 기준)
  ② 최악 하루 >= -3만원 (클러스터 방어 작동 증명)
- 통과 → 7월 구조 단계 진행, 사이징·러너 검증 후 증자(씨드) 논의 개시
- 미달 → 증자 금지. 선정/체결/청산 단계별 손익 분해 → 원인 수선.
  반복 미달 시 축소 경로(US 단독, paper 병행) 검토
- 원칙: "증자는 보상이지 베팅이 아니다" — 검증 전 증자 금지 (7월 단계 #5와 동일)
- 운영 수칙: 손익 확인은 주 1회 (일 단위는 노이즈), 매일 채점은 구조 작동 여부만

**2026-07 — 구조 단계 (6월 net 원장 마감 채점 후, 순서 고정):**
1. 확신도 사이징 — rr/rvol/채널 팩터별 에지 확정 후 50만 고정 → 0.7×~1.5× 차등
2. 러너 부분 보유 (core+runner 분할, ladder 60~70% 청산 + 잔여 trailing multi-day) — 보호영역, shadow 선행
3. 국면 스위치 (추세/횡보 × 변동성 → 게이트 세트 전환) — 나쁜 달 방어
4. 실적 캘린더 연결 (보유 포지션 실적 경고 + mega_gap 이벤트 태그, PEAD 인프라 재사용)
5. 1·2가 데이터로 확인된 뒤에만 증자(스케일) 논의 — 검증 전 증자 금지

**운영자 대기 (날짜 무관):** 환전 우대율 확인(US_FEE_RATE_PER_SIDE 조정).

**KR PathB 재개 (2026-06-11 운영자 결정, 동결 예외 — 운영 설정 변경):** 신규 게이트 체계
(존 -0.5%·rr>=1.5·stop cluster 5) 하에서 KR 재검증. 6/10 중단 사유였던 KR gross 음수는
구 게이트 시절 데이터라는 판단. 주의: A3 함정구간·rel_vol·mega_gap은 US 전용이라 KR엔 미적용 —
KR 성과는 존 규칙+rr 게이트만의 효과로 읽어야 함. preflight 정책 KR-on/US-on 복원 완료.

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
