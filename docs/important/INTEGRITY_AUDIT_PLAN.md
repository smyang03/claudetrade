# 시스템 무결성 전수 검토 계획 (2026-06-23 준비)

> 운영자 지시: "상세하게 무결성으로 하나하나 모든 기능·DB·전략 검토". brain 교훈 5월 화석화 버그(AI도 운영자도 일중 검토에서 놓침)를 계기로, **AI 눈이 아니라 코드가 1차로 잡고, 두 모델이 토론으로 판정**하는 체계를 만든다.

## 0. 목적·사용법·규율

**왜:** AI/사람 검토는 "조용한 stale/오염"을 놓친다(brain correction_guide가 35일째 급락장 렌즈를 selection에 주입하던 걸 둘 다 놓침). "검토했고 문제없다"는 단언이 실제로 틀렸다.

**사용법 (다음 세션):**
1. **자동 무결성 검사 도구**(§4)를 돌려 결정론적 "의심 목록"을 뽑는다 (1차 필터).
2. 두 모델이 의심 항목을 **영역별로 하나씩** "버그 vs 정상/과거데이터"로 판정 토론한다.
3. 버그면 수정+검증, **정상이면 "정상인 사유"를 이 문서에 기록**(반복 검토·오인 방지).

**규율 (오늘의 교훈):**
- ⚠️ **아래 1차 스캔 숫자(예: "100% NULL")는 단정이 아니다.** "버그 vs 정상/과거데이터/설계의도" 판정이 본 작업. (예: `execution_decision_id` 99.8% NULL은 "대부분 watch_only라 정상"일 수 있고, `market_regime` NULL은 "과거는 원래 비고 6/21 재시작 후 신규만 채움"일 수 있다 — 검증 전엔 모른다.)
- ⚠️ Explore 1차 인벤토리는 도구 간 불일치가 있다(예: 한 스캔은 `correction_guide.generated_date=null`이라 했으나 실제 brain엔 `5/18`·`5/12`가 있음). **숫자는 다음 세션에 직접 재쿼리로 확인.**
- "문제없다" 금지: 검토 결과는 항상 "검토 범위 + 안 본 축 + 신뢰도"로.

---

## 1. 무결성 위험 유형 (검토 렌즈 — 모든 영역에 이 6개를 댄다)

| 유형 | 정의 | 오늘 발견 예시 |
|---|---|---|
| **A. stale 화석화** | 갱신 멈춘 데이터가 현 국면에 주입 | correction_guide 5월 급락장 → 6월 강세장 |
| **B. 오염** | 잘못된 데이터 혼입 | KR brain에 US종목(SRPT/BRZE/PAYS), description-insight 매칭깨짐 |
| **C. 정합성 끊김** | 측정 파이프라인 NULL/sync 실패 | market_regime/fx/net NULL, fact_forward 0% |
| **D. 일관성 불일치** | config↔env, 문서↔실제 어긋남 | (현재 양호, 자동검증 부재) |
| **E. 배선 끊김(침묵 실패)** | 채워져야 할 게 조용히 안 채워짐 | execution_decision_id 99.8% NULL |
| **F. 과적합/가드누락** | 단일사건 교훈, 주입경로 가드 빠짐 | issue_patterns count=1, selection fresh-brain 가드(수정됨) |

---

## 2. 영역별 체크리스트

### 영역 A — DB·측정 파이프라인

**DB 인벤토리 (1차 스캔, 행수·NULL%는 재확인 대상):**

| DB | 크기 | 핵심 테이블 | 1차 의심 |
|---|---|---|---|
| decisions.db | 33.7MB | decisions(69,790), v2_learning_performance(757), mfe_backfill_yf(222) | market_regime/fx/net NULL |
| ticker_selection_log.db | 5.9MB | ticker_selection_log(15,710) | execution_decision_id 99.8% NULL |
| candidate_audit.db | **2.2GB** | audit_candidate_rows(117,802), outcomes(374,887) | 비대, execution_id 98.8% NULL, outcomes 41% NULL |
| v2_event_store.db | **887MB** | v2_decisions(770), lifecycle_events(8,060) | 비대 |
| agent_call_events.db | **691MB** | agent_call_events(8,283) | 비대 |
| claude_decision_facts.db | 24.3MB | fact_selection/execution/forward_outcome(각 11,004) | **fact_forward_outcome 0% populated** |
| 기타 | - | intraday_strategy_log, lesson_validation(6), exit_decision_scoring(2,905), entry_discrimination, capture_calibration(424) | lesson_validation 표본 6 |

**체크 항목 (각: 자동쿼리 → AI판정 질문):**
- [ ] **P0 fact_forward_outcome 0% populated** — 자동: `SELECT COUNT(*) WHERE forward_1d_pct IS NOT NULL`. AI판정: 별도 배치인가 / 백필 미실행 버그인가? **이게 버그면 fact 기반 분석 전부 무효.**
- [ ] **P0 execution_decision_id NULL 99.8%/98.8%** — 자동: NULL%. AI판정: watch_only라 정상인가 / selection→execution 배선 끊김인가?
- [ ] **P0 market_regime NULL** — 자동: NULL% by 진입일(6/21 재시작 전/후 분리). AI판정: 6/21 "측정배선 복구" 후 신규는 채워지나? (CHANGELOG 2026-06-21 참조)
- [ ] **P1 pnl_pct_net NULL 91% / fx_change_pct NULL 93%** — AI판정: v2 경로 net 미채움이 설계인가 버그인가. (FX는 KIS 미노출 기지)
- [ ] **P1 v2_canonical ↔ v2_learning sync** — 자동: row/id 일치(757/757 OK로 보임). 
- [ ] **P1 lifecycle CLOSED → v2_learning 완전성** — 자동: CLOSED 310 vs pending.
- [ ] **P2 DB 비대**(candidate_audit 2.2GB, event_store 887MB, agent_call 691MB) — AI판정: 정리/아카이브 필요? 성능 영향?
- [ ] **P2 audit_candidate_outcomes 41% NULL**(observed_price/return) — AI판정: future/stale 후보 자연손실인가 수집실패인가(표본편향).
- [ ] **P2 mfe_backfill stale**(~2주 전) — 자동: max(synced_at) vs today.

**기존 도구(재사용, over-build 방지):** `tools/integrity_check.py`(NULL%/freshness), `ml/db_health.py`(schema/fixture), `ml/forward_updater.py`(forward 백필, 수동), `tools/capture_net_review.py`, `tools/reconcile_live_truth.py`.

### 영역 B — brain·교훈·Claude 주입 경로

**주입 경로 매트릭스 (6경로):**

| # | 경로 | 파일 | V2 fresh brain 가드 | 1차 의심 |
|---|---|---|---|---|
| ① | 시장판단 | trading_bot.py:9167 `_brain_context_for_judge` | ✅ 보호 | LOW |
| ② | **Selection** | analysts.py:2750 `select_tickers` | ✅ **오늘 수정** | (수정 검증: 봇 재시작 후) |
| ③ | **Postmortem** | postmortem.py:495 | ❌ **가드 없음** | brain_summary 주입(응답형식이라 위험낮음? 재확인) |
| ④ | Hold Advisor | hold_advisor.py:47 `_MEASURED_PRIORS` | ✅ 하드코딩(brain 미참조) | priors 갱신정책 별도 |
| ⑤ | Active Lessons | active_lessons.py:475 | ✅ DB격리 | 기본 OFF |
| ⑥ | Digest | digest_builder.py | N/A | 로그/요약용 |

**brain 섹션별 신선도 메타 (게이트 가능성):**

| 섹션 | 날짜메타 | 의심 |
|---|---|---|
| correction_guide | generated_date 有(5/18·5/12) ※Explore 불일치, 재확인 | stale 화석화, 신선도게이트 미적용 |
| issue_patterns | ❌ 날짜없음(count만) | count=1 과적합 39/40·44/44, KR-US혼입 P036/037/044 |
| learned_lessons | ❌ 날짜없음 | 급락장 교훈 stale 주입(11개 전부 방어) |
| current_beliefs.market_regime | ❌ 날짜없음 | "하락추세 DEFENSIVE" stale |
| recent_days / debate_history | ✅ date有 | OK |
| analyst/mode_performance | recent_7d/30d有, 전체누적 | 오래된 샘플 오염 가능 |

**체크 항목:**
- [ ] **P0 postmortem(③) 가드 누락** — AI판정: brain_summary가 응답요청에만 들어가 위험낮은가 / selection처럼 수정 필요한가?
- [ ] **P1 selection 수정 검증** — 봇 재시작 후 프롬프트에서 brain 빠졌나 + selection 분포 변화 관찰.
- [ ] **P1 issue_patterns 정리** — count≥2 게이트, KR-US혼입 제거, description-insight 매칭 검사. (brain.json 1회 클린 = 운영자 승인)
- [ ] **P1 신선도 메타 부재** — correction_guide/learned_lessons/beliefs에 날짜 없어 게이트 불가 → 제거 or 당일값 대체 or 날짜구조 추가.
- [ ] **P2 brain 갱신/승격 끊김** — 승인큐 consumer 부재(5/20 `4f0cd1c` 후), lesson_validation valid_apply=0. AI판정: 복구 vs 은퇴(덜어내기).
- [ ] **P2 generate_prompt_summary 비대**(8848자) — tuning_patterns 16개 등 노이즈. 700자 cut이라 selection엔 일부만.

**기존 도구:** `tools/check_brain_quality.py`(모지바케/빈값/구조, **신선도·count 미검사**), `tools/check_brain_commit.py`.

### 영역 C — config·전략·state

**1차 스캔 결과: 비교적 양호(95~100%).**

**체크 항목:**
- [ ] **config ↔ .env.live 일치** — 자동: live_preflight LIVE_CONFIG_KEYS(180키). 1차 양호.
- [ ] **전략 KR/US 분리** — 자동: KR 손실전략(momentum/gap_pullback/ORP) OFF 단언, US momentum ON 단언, CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true 단언. 1차 100%.
- [ ] **문서 ↔ 실제** — CLAUDE.md "운영자 확인 필수 설정값" 표 vs config/env. 자동검증 도구 부재 → 추가 권장.
- [ ] **VB 토글 모호** — US_VOLATILITY_BREAKOUT_LIVE_ENABLED 미설정(=false 추정). 명시 권장.
- [ ] **state 구파일 누적** — 일별 판단/후보 파일 30일+ 누적. 정리 전략.
- [ ] **pead_shadow_state.json 부재** — CLAUDE.md 언급하나 미생성? 기능 단계 확인.

**기존 도구:** `tools/live_preflight.py`(config/env drift, 180키), pre-commit hooks.

---

## 3. 발견된 의심 항목 우선순위 (이번 인벤토리)

**P0 — 측정 신뢰 직결 (이게 버그면 그동안 분석이 오염됐을 수 있음):**
1. `fact_forward_outcome` 11,004행 forward 전부 비어있음
2. `execution_decision_id` 99.8%/98.8% NULL (selection→execution 추적)
3. `market_regime` NULL (6/21 복구 후 신규도 NULL인지)

**P1 — 입력 품질:**
4. brain stale 잔여(postmortem 가드, 날짜메타 부재, issue_patterns 과적합/오염)
5. `pnl_pct_net`/`fx_change_pct` NULL (net 측정 신뢰)
6. selection 수정 라이브 검증

**P2 — 운영 위생:**
7. DB 비대(3.8GB+), state 구파일 정리
8. 문서↔실제 자동검증 도구, audit_outcomes 표본편향

---

## 4. 자동 무결성 검사 도구 스펙 (다음 세션 첫 구현)

**원칙:** 기존 `tools/integrity_check.py` 확장(over-build 금지). 결정론적 체크만 모아 "의심 목록 JSON" 출력 → AI 토론 입력.

**체크 모듈 (전부 결정론적):**
- `db_null_coverage`: 핵심 컬럼 NULL% (market_regime/fx/net/execution_id/forward_outcome) + 임계 경보
- `db_freshness`: max(synced_at/closed_at) vs today (mfe_backfill, forward 측정)
- `db_sync`: v2_canonical↔v2_learning, lifecycle CLOSED↔v2_learning row/id 일치
- `brain_staleness`: correction_guide.generated_date age, learned_lessons/beliefs 날짜메타 유무
- `brain_pollution`: issue_patterns count=1 비율, KR description에 US티커 정규식, description-insight 동일성
- `brain_injection_guards`: 각 주입경로(①~⑥)에 V2 fresh brain 가드 존재 여부(코드 정적검사)
- `config_consistency`: config↔env 키 값 일치, KR손실전략 OFF/US수익전략 ON 단언, 문서표 대조
- `state_freshness`: state 파일 mtime, 구파일 카운트

**출력:** `state/integrity_audit_report.json` — 항목별 {status: OK/WARN/ALERT, value, threshold, needs_ai_judgment: bool}. cron/preflight 편입.

**기존 자산 재사용:** integrity_check.py의 `evaluate_population`/`evaluate_freshness`, db_health.py, check_brain_quality.py 확장.

---

## 5. 다음 세션 진행 순서 (토론형)

1. **자동 도구 구현·실행**(§4) → 의심 목록 JSON 생성.
2. **P0부터** 두 모델(빌더/회의)이 항목별 "버그 vs 정상/과거데이터/설계의도" 판정 토론. 직접 재쿼리로 1차 숫자 확인.
3. **버그 확정 → 수정+검증**(테스트+preflight, brain.json은 운영자 승인). **정상 확정 → 이 문서 §6에 "정상 사유" 기록.**
4. 영역 A→B→C 회전. 각 항목 처리 결과를 체크박스 갱신.
5. 자동 도구를 cron/preflight에 편입해 **상시 감시**(일회성 검토가 아니라).

---

## 6. 판정 기록 (검토하며 채움 — "정상인 사유"도 기록해 반복 오인 방지)

### 2026-06-23 1차 토론 (빌더=Opus 직접 재쿼리 / 회의=독립 재쿼리 sub-agent)

도구: `tools/integrity_audit.py` 신규 구축(§4 8개 모듈, `state/integrity_audit_report.json` 출력). 실행 결과 ALERT 14/WARN 4/AI판정필요 18. 아래는 P0 3건을 두 모델이 직접 재쿼리로 판정한 결과.

**P0-1 fact_forward_outcome 0% / 26일 정체 → [버그=죽은 분석 파이프라인 / 라이브 무해 / 분석 함정]** (양 모델 동의)
- 실측: claude_decision_facts.db(`data/ml/`)의 fact_forward_outcome 11,004행이 전부 session_date 5/20~5/27, updated_at 단일 5/27 배치. forward_1d/3d/5d/runup/drawdown 전부 0%, 30m/60m만 ~32%.
- 소비처: grep 전수 결과 `tools/`(build/report/label)+`tests/`만 읽음. trading_bot/runtime/execution/minority_report/lifecycle/ml 0건. build_claude_decision_facts.py는 cron/훅 미등록 수동전용.
- 판정: **런타임이 안 읽으므로 라이브 무해.** 단 오늘 fact 기반 분석(report_claude_misjudgments 등)을 돌리면 한 달 묵은 5/20~5/27 데이터를 경고 없이 현재로 쓰는 함정. → 개선방향: **은퇴(덜어내기) vs 신선도 가드+자동빌드** 택일. CLAUDE.md 복잡도 덜어내기 원칙상 미사용이면 은퇴 권고.

**P0-2 execution_decision_id 99.8% NULL → [대부분 정상(watch_only) / traded 33%만 P1 attribution 갭]** (양 모델 동의)
- 실측: ticker_selection_log 15,740행 중 traded=1은 78행뿐(99.5% watch_only). watch_only는 NULL이 정상. traded=1 중 26/78(33%)만 execution_decision_id 연결.
- 결정 검증: 6/22 PathB 실체결 4종(SYRE/INTC/TSLA/CWAN)이 selection_log에 traded=0·exec_id=NULL로 존재 → **PathB 진입은 traded를 아예 안 찍음**(Path A/B 분리 구조). 즉 traded=78은 실거래 대폭 과소계상.
- 판정: **"99.8% NULL"은 오인 헤드라인(정상).** 진짜 잔여는 traded 52건 미연결(P1) + PathB 미표기 구조(설계). 라이브 무해, attribution 분석 품질만 영향.

**P0-3 market_regime 0/757 (6/21 이후 신규도 빈값) → [확정 버그 / 단 근본원인은 빌더 진단 반박됨]** (버그엔 동의, 근본원인 회의 모델이 정정)
- 실측: v2_learning_performance.market_regime 0/757(None 490·빈문자 267). 6/22 신규 진입분(INTC 등)도 CLOSED payload에 entry_market_regime 키 부재.
- 빌더 1차 진단: "`_pathb_entry_market_regime`가 진입 시 빈 `bot.market_consensus_mode` 캐시 읽어 '' 반환".
- **회의 모델 반박(채택)**: ① INTC는 decisions.db에 아직 없음(event store 전용, sync 6/19까지) — 증거는 event store 기준으로 명시. ② 6/22 TRADE_READY 이벤트엔 regime 살아있음(`_adaptive_live_condition.market_regime='risk_on'`, consensus_mode='MODERATE_BULL'). ③ 진입 5분 전 봇 재시작 + reuse/reinvoke 경로가 둘 다 `_apply_consensus_guards`를 거쳐 `market_consensus_mode` 시드 → 진입 시점 캐시는 비어있지 않았음. ④ FILLED/CLOSED payload·path_run plan_json 어디에도 entry_market_regime 미영속화.
- **정정된 근본원인(실측 기반 추정)**: regime이 in-memory `pos` dict에만 있고 durable store(path_run/FILLED)에 **영속화 안 됨** → 단기 청산·rehydrate 경로에서 CLOSED payload까지 전달 유실. "진입 캡처 실패"가 아니라 **payload 영속화/전파 버그**. (단정 아님 — `_register_pathb_position` 호출 여부 런타임 트레이스 필요.)
- 판정: **확정 버그.** carry-gate/mode별 적중률 등 regime 의존 분석은 전수 무효. → 개선방향: sync writer가 CLOSED payload 대신 TRADE_READY/PLAN_CREATED 이벤트의 살아있는 regime을 읽거나, entry_market_regime을 path_run/FILLED에 영속화 후 청산 시 복원.
- **✅ 수정 적용(2026-06-23, 커밋 d056fad)**: sync-layer fallback 채택. `sync_v2_learning_performance._entry_regime_from_events()`가 close_payload 비면 PLAN_CREATED/TRADE_READY의 consensus_mode를 읽음. **런타임 트레이스 불요**(이벤트 스토어가 진입 시점 안정 기록이라 드롭지점 무관 우회). 보호영역 pathb_runtime exit **무수정**. read-only 검증 279행 중 172(62%) 복원, 신규 100%. 잔여: 전체 백필은 봇 정지 시 운영자 full sync 1회(봇은 10일 윈도우만).

**부수 발견(mfe/mae 3~4%)**: producer가 최근 CLOSED 12/54건만 position_mfe_pct 방출 + 6/22 백필 mfe가 6/18 synced 기존 learning 행에 재반영 안 됨(sync 미갱신). 이중 갭. regime과 같은 영속화/재sync 축.

**B영역 brain(자동도구 결정론 결과, AI판정 일부 동반):**
- correction_guide KR=5/18(36일)·US=5/12(42일) 화석화 — 신선도 게이트 부재(A형). meta.last_updated=6/23인데 가이드 본문은 5월 급락장("오늘 -6.12% 대폭락") 렌즈.
- issue_patterns count≤1: KR 39/40·US 44/44 과적합(F형). KR에 US티커(SRPT/BRZE/PAYS) 오염 3건(B형).
- 주입경로 가드: ①시장판단·②Selection 가드 있음(②는 6/23 추가 확인). **③Postmortem 가드 없음** — `generate_prompt_summary` 무조건 주입(stale brain이 사후분석 프롬프트에 유입). ④HoldAdvisor·⑤ActiveLessons brain 미참조(안전).
- 판정: brain.json 클린(issue_patterns count≥2 게이트·US티커 제거·신선도 메타)은 **운영자 승인 필요**. postmortem 가드는 selection과 동일 함수 재사용으로 코드 수정 가능(승인 후).

**C영역 config/state:** config↔env 안전토글 7종 전부 일치(GREEN). state/*.json 182/533이 30일+ 미수정(위생 정리 후보). db_sync canonical 772 vs learning 757(15 갭 — sync 지연/미반영, 추적 후보).

---

**연결:** brain 화석화 근본원인·수정은 메모리 `brain-lesson-fossil-fix-20260623`, CHANGELOG 2026-06-23. 이 계획의 1차 인벤토리는 Explore 3종(DB/brain/config) 종합이며 숫자는 재확인 대상.
