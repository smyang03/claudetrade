# 운영 변경 일지 (CHANGELOG)

운영자가 "언제 무엇을 왜 바꿨고 지금 어떤 상태인지"를 한 파일로 따라가기 위한 일지.
git(코드 diff) + 이 파일(결정·토글 이력) + `/status`(현재 스냅샷) = 통제권 3종 세트.

기록 규칙:
- 코드/config/env/토글을 바꾸면 **무엇 / 왜 / 현재상태 / 롤백조건 / 커밋해시**를 한 줄로 추가.
- **롤백·번복도 기록한다** (조용히 엎지 않기). 기각한 것도 "(기각) 이유"로 남겨 반복 논의 방지.
- 최신 날짜가 위로. 상세 근거는 CLAUDE.md `MD 위반 사항`/메모리에, 여기는 한눈에 보는 요약.

---

## 2026-06-23

### 결정: profit_guard prior 유지/롤백 = 4주 뒤 청산 verdict로 (fresh-brain·교훈 토론 종결)

- **무엇:** 코드/토글 무변경 — **결정·kill 규율 기록만.** `HOLD_ADVISOR_PROFIT_GUARD_ENABLED`(현 true) 유지/롤백을 **#4a 청산 forward-validation verdict로 2026-07-21경(또는 exit n_sell≥30) 재확인**으로 확정. CLAUDE.md 해당 행 갱신(체리픽 −8.91%p 정정 포함).
- **왜:** fresh-brain "왜 만들고 왜 화석화" 점검 → 회의 변호인 2자토론 → regime 백필 후 재채점(A)으로 종결. **① selection 교훈(watch_only 진입완화)은 regime 고쳐도 valid_apply 0 = 무엣지 확정, 영구 폐기.** ② **청산 교훈(profit_guard)은 살아있음·미성숙** — regime 백필로 unknown→risk_on 정상 라벨, 조기 gain 양수(KR+3.96·US+0.71=익절>HOLD)나 n=5/3 insufficient. **원래 설계 의도 재확인: #4a는 추상적 브레인이 아니라 "체리픽 수치로 라이브 주입된 profit_guard prior가 정당한지 검증·미검증 영구주입 종식"이 목적**(`HOLD_ADVISOR_LESSON_VALIDATION_HANDOFF.md`/`hold_advisor_review_20260623.md`).
- **현재상태:** profit_guard 토글 **true 유지**(값 무변경). 청산 verdict 파이프라인 정상 가동(세션마감 자동 rescore). 주문/리스크/broker truth 무변경. regime 백필(이번 정비창)로 재채점 완료(selection 6셀·청산 2셀).
- **롤백조건(=이 결정의 핵심):** 2026-07-21경/n_sell≥30 시점 청산 verdict가 **음수로 뒤집히면 `HOLD_ADVISOR_PROFIT_GUARD_ENABLED=false`**, 양수+sufficient면 유지. selection 교훈은 묻은 채 둠(스코어러 추가 투자 X).
- **잔여:** "brainless 영구"는 틀린 프레임이었음 — 청산 verdict가 v2 브레인 역할을 이미 advisory로 수행 중. 4주 관찰 외 신규 작업 없음.

### fast-fill 매수 재호가 후 로컬 매수 pending stale 정리 (phantom 미체결 버그)

- **무엇:** `runtime/pathb_runtime.py` — `_remove_pathb_pending_buy_order` 신규(매도쪽 `_remove_pathb_pending_sell_orders` 대칭, 매수만·매도 pending 절대 비접촉) + fast-fill 두 분기 배선: ① 취소 전 체결(`_fast_fill_requote_run`) ② 취소확정→재제출(`_fast_fill_resubmit` cancel_plan 직후). 테스트 `tests/test_pathb_fast_fill_pending_cleanup.py`(7).
- **왜:** 2026-06-23 KR 475430(키스트론) — 09:56 매수 지정가 미체결 → 10:02 enforce fast-fill이 broker 주문(`cancel_order`)+path_run(`cancel_plan`)은 취소했으나 **로컬 pending_orders는 안 지움**. stale이 `safety_gate.py:135 PENDING_ORDER_EXISTS`(로컬 pending 기준, broker truth 아님)로 재호가를 막고 대시보드에 유령 미체결 표시. broker엔 보유0·미체결0(reconcile_order_truth unresolved). 매수 fast-fill 취소 경로에 pending 정리 누락(매도 경로엔 있음).
- **현재상태:** 코드 반영. **봇 재시작 시 적용.** 로컬 state 정리 전용 — 주문/수량/하드스톱/loss_cap/broker truth/Claude 호출량 무변경, **금전 노출 0**(broker 무주문). 매도 pending은 side/`pathb_pending_sell_order_no` 단서로 제외. 검증: 신규 7 + fast-fill/reconcile 보호영역 212 passed, mojibake/preflight(ok=True FAIL 0).
- **롤백조건:** 코드 revert(로컬 정리 전용, 라이브 리스크 없음).
- **잔여:** ① 현재 떠 있는 475430 stale 1건은 과거 발생분이라 코드가 소급 정리 못함 → 정비창 봇 정지 시 별도 정리 or 봇 reconcile 자가치유 확인. ② PENDING_ORDER_EXISTS를 broker-truth/path_run-aware로 만드는 보강은 후속. ①②(e6405b3)와 함께 봇 재시작 시 반영.

### KR 수급 진입 게이트 shadow 가동 (운영자 지시 "지금 재시작함")

- **무엇:** `KR_FLOW_ENTRY_GATE_MODE` off → **shadow** (.env.live + config/v2_start_config.json).
- **왜:** 봇 재시작 정비창에서 flow 진입 게이트를 관측 모드로 가동 — flow-negative 진입 would_skip 수집 시작. enforce는 검증(flow_date_matched + kill 바) 전까지 금지라 shadow까지만.
- **현재상태:** **shadow=순수 관측, 주문/플랜/sizing 0 영향.** enforce 아님(실제 진입 차단 없음). fail-open. **봇 재시작 시 적용.** 재시작 후 확인: `state/kr_candidate_flow_*.json`의 flow_date_matched 비율(fetch fix 라이브 검증) + `logs/funnel/kr_flow_entry_gate_*_KR.jsonl` 생성.
- **롤백조건:** `KR_FLOW_ENTRY_GATE_MODE=off`(즉시 no-op). shadow는 무해라 롤백 급하지 않음.

### KR 개선 트랙 1차 — 수급 진입 게이트(코드) + flow 수집 버그 수정 (운영자 지시, 커밋 0186387)

- **무엇:** ① `phase1_trainer/supplement_collector.py::fetch_investor_flow_kr` — `output[0]` 맹신 대신 `stck_bsop_date`로 target_date(전일=정산완료일) 직접 매칭, 미매칭 시 폴백+`flow_date`/`flow_date_matched` 표시. ② `bot/kr_investor_flow_cache.py` — 미매칭 all-zero(=정산 전 당일값)는 untrusted 처리해 재시도 유도(`unsettled_zero_unmatched_date`). ③ 신규 `bot/kr_flow_entry_gate.py` + `runtime/pathb_runtime.py::_pathb_flow_entry_gate`(진입 스캔 `_submit_buy` 직전 훅) — KR 전일 외인+기관 순매도 종목 신규진입 게이트. ④ review 도구 `tools/kr_flow_entry_gate_review.py`. 테스트 `tests/test_kr_flow_entry_gate.py`(10)+`test_kr_investor_flow_cache.py`(16).
- **왜:** flow 수집 버그 자체로 정당화. `output[0]` 맹신 → KIS가 fetch당일 행을 반환하는데 한국수급은 마감후 정산이라 캐시 "전일" 라벨이 실제론 fetch당일 마감수급으로 **1일 밀림**(증거: source 6/22 완료거래일을 6/23아침 fetch=all-zero 불가능값; non-zero는 마감후 15:38만). 아침 fetch는 미정산 all-zero. **주의: 초기 소급검증 "전일 순매수→다음세션 +6.45%"는 이 1일 밀림 때문에 같은날 수급vs같은날 수익=동어반복으로 판명·철회. flow 예측력은 미검증.** 따라서 fix는 수집 정확도, shadow는 flow를 처음부터 검증하기 위함(KR 개선 트랙 1차, CLAUDE.md `KR 개선 트랙 규율`).
- **현재상태:** 코드 반영(stage됨, 미커밋). **봇 재시작 시 적용.** 게이트 토글 `KR_FLOW_ENTRY_GATE_MODE=off`(.env.live+config, **기본 off=완전 no-op**). fetch/캐시 수정은 토글 무관 즉시 활성(폴백으로 무회귀). 주문/리스크/broker truth/Claude 호출량 **무변경**(off). fail-open(수급 결손 시 미차단). MD 위반 사항 기록(PathB 진입 스캔 신규 게이트, off=no-op).
- **롤백조건:** `KR_FLOW_ENTRY_GATE_MODE=off`(기본). fetch/캐시 수정 의심 시 코드 revert(단 폴백이라 무회귀). **검증 경로: 봇 재시작 후 캐시 `flow_date_matched` 비율 확인(낮으면 KIS 당일행만 줌=데이터소스 재검토) → shadow 전환 수집 → `kr_flow_entry_gate_review.py`로 would_skip vs allow net → kill 바(표본≥30·약세 포함·격차유지) 미달 시 "(기각)" 기록 후 veto-only.**
- **잔여:** flow fetch는 라이브 KIS 미검증(운영머신 API 금지). flow 판별력 56% 일치율(약함)+강세장 confound — shadow 검증 필수. 검증: 신규 26 passed, 보호영역 회귀 227 passed, py_compile 5파일, mojibake/preflight(ok=True FAIL 0).

### US 손절 매도 marketable 지정가(②) + 미체결 매도 shadow 측정 복구(①) — 운영자 지시 "둘 다 해"

- **무엇:** `runtime/pathb_runtime.py` — ① `_log_unfilled_sell_shadow` 게이트를 `pathb_closing`(선행 클리어돼 0건 캡처)에서 `pathb_pending_sell_price`로 교체 → 미체결 매도 페이드 계측 복구. ② `_pathb_stop_marketable_sell_price` 신규 + `_submit_sell` 배선: **US 손절성 매도(CLOSED_LOSS_CAP/HARD_STOP/CLAUDE_PRICE_STOP)를 트리거가 −pct%로 호가**해 marketable 지정가(확실 체결)로 깐다. 테스트 `tests/test_pathb_stop_marketable_sell.py`(9).
- **왜:** 실사례 — 6/22 AVGO 시스템 매도가 397.64 지정가에 박혀 급락에 미체결, 운영자가 MTS에서 396.15로 **매도 정정(가격 낮춤)** 해서야 체결(+0.92%는 그 개입 덕). KIS 해외주문은 시장가가 없어 지정가가 트리거에 박히면 급락 시 미체결 방치. 데이터: US loss_cap 51건 중 20%가 −2% 캡 초과(worst −5.4%), 수동/외부 개입 23/221(10%). ①의 전용 shadow는 게이트 버그로 0건이라 enforce 판정 근거가 없었음.
- **현재상태:** 코드 반영(미커밋). **봇 재시작 시 적용.** ②는 **손절 경로만**(익절 target/ladder/claude_sell 무관=가격 양보 금지), **US 한정**(KR은 이미 시장가), 토글 `US_PATHB_STOP_MARKETABLE_LIMIT_ENABLED=true`·offset `US_PATHB_STOP_MARKETABLE_LIMIT_PCT=0.5`(.env.live+config). **주문/리스크 영향: 손절 매도의 지정가만 트리거 −0.5%로 낮춰 체결률↑(체결은 매수호가=가격개선이라 실현가는 −0.5% 전부 양보 아님). 주문수량·하드스톱 임계·loss_cap 임계·broker truth·Claude 호출량 무변경.** 매도엔 슬리피지 거부 가드 없음(매수 전용) → 거부 위험 없음 확인. ①은 측정 전용(실주문 무영향).
- **롤백조건:** `US_PATHB_STOP_MARKETABLE_LIMIT_ENABLED=false`(②) / 코드 revert. offset 과도 의심 시 PCT 하향(상한 1.5% bounded).
- **잔여:** ① shadow 데이터 며칠 쌓이면 "추격 vs 지정가 net" 사후검증 가능. ② offset 0.5%는 운영자확인 파라미터(첫 값, 라이브 체결률·실현가 모니터 후 조정). hard_stop은 기존에도 잘 체결(0건 초과)이라 효과는 loss_cap/claude_price_stop 중심 예상. 검증: 신규 9 passed, 매도/청산 보호영역 회귀 270 passed, py_compile/mojibake/preflight(ok=True FAIL 0).

### 방향 결정: KR을 "개선 트랙"으로 (문서만, 운영자 지시)

- **무엇:** CLAUDE.md `나아갈 방향` 수정 — "KR 구조적 손실 분리 관찰: KR live 확대 금지/축소 우선" → "KR 개선 트랙(포기 아님, 개선해서 나아간다)"으로 방향 재정의. `KR 개선 트랙 규율` 하위 섹션 신설(정량 허들·kill 조건 포함).
- **왜:** 운영자 결정. 데이터(2026-06-23 KIS 실측)는 KR long을 역선택 무엣지로 진단했으나(미진입 forward MFE +17.1% vs 진입 +7.1%), 운영자가 포기 대신 개선 방향 유지를 선택. AI 권고는 veto-only였고 운영자가 번복 — 막연한 낙관 방지를 위해 MD 하우스 스타일대로 측정 허들/kill을 부착하는 조건으로 반영.
- **현재상태:** **문서만 변경.** 코드/config/env/토글/주문/리스크 무변경. `PATHB_KR_LIVE_ENABLED=true` 현행 유지(실탄·동시성·주문금액 확대 없음 — 개선은 shadow 선행). 정량 허들: 표본≥20 & ≥2주 & 비용후 net 양수 & 단일월·단일종목·lookahead 비의존 & 단순보유/지수 초과.
- **롤백조건:** 허들 기한 내 미달 시 해당 가설 철회 → KR veto-only 복귀, 실탄/에너지 US capture로 회수("(기각) 이유" CHANGELOG 기록). 커밋: 미커밋(문서).

### brain KR/US 오염 정리 --apply 적용 (정비창, 운영자 지시)

- **무엇:** `tools/clean_brain_pollution.py --apply` 실행 — `state/brain.json`의 KR issue_patterns 내 US종목 오염 3건(SRPT/BRZE/PAYS 중복체결 관련) 제거. 백업 `state/backups/brain_20260623_085344.json` 자동 선행. KR issue_patterns 40→37.
- **왜:** 무결성 감사 B영역(시장 오염 — KR brain에 US티커 혼입). 도구는 `970204b`에서 추가됐으나 "봇 정지 시에만 apply" 제약으로 미적용 상태였고, 운영자가 봇 재시작 정비창에서 적용 지시.
- **현재상태:** **적용·검증 완료**(재확인 dry-run 오염 0건). 절차: guardian→trading_bot 정지(brain writer down)→`--apply`→봇·guardian 재기동. 봇이 cleaned brain 로드(08:54 재기동, API 11/11). 봇 재기동 후 brain.json 미변경 확인(fresh 모드라 세션마감 전 미기록=재오염 없음). config/env/주문/리스크 무변경. correction_guide 화석화(5월)는 별개 — fresh-brain 가드로 주입 차단 중(데이터 갱신은 별도 숙제 §2-3).
- **롤백조건:** `state/backups/brain_20260623_085344.json`에서 복원.
- **잔여:** correction_guide 신선도 게이트(§2-3)는 미적용 — `V2_FRESH_BRAIN_START=false` 전환 시 5월 stale 재누수하므로 그 전 선행 필수. regime 전체 백필(RUNBOOK §1)도 미실행(별도 정비창).

### [무결성 2-1] PathB MFE/MAE 측정 누락 근본수정 (보호영역, MD 위반 기록)

- **무엇:** `runtime/pathb_runtime.py` — ① `_update_position_excursion`이 새 고점/저점일 때 observed_* excursion을 path_run plan_json에 durable 영속화(`_persist_observed_excursion` 신규, merge·추가전용) ② `_pathb_exit_meta`가 pos=None/observed 없을 때 plan_json durable로 fallback ③ `_finalize_pathb_sell_close`·`on_external_close`가 pos 제거된 청산에서도 durable로 exit_meta 복원. 테스트 `tests/test_pathb_position_excursion.py` +8(총 12).
- **왜:** v2_learning `mfe_pct` 충진율 PathB 청산 261건 중 **241건(87%) NULL**. 핸드오프 진단(rehydrate 유실)을 직접 재쿼리로 반박 — same_day 청산도 92% NULL이라 영속화 문제 아님. 진짜 원인은 **브로커 truth reconcile 청산 시 sync가 로컬 pos를 먼저 제거 → `_find_position`=None → exit_meta 미생성**. market_regime 0/757(d056fad)과 동형 버그(MFE는 진입시점 미지라 plan_json 영속화로 우회).
- **현재상태:** 코드는 `bc22d9f`에 커밋됨 — **동시 세션의 커밋에 staged 변경이 휩쓸려** 'test: Py3.9 호환' 라벨로 무관 테스트픽스와 혼재(내용은 온전·검증됨, history 수술은 라이브+동시활동이라 미실시). **봇 재시작 시 적용.** 주문/수량/하드스톱/loss_cap/profit_ladder/broker truth/Claude 호출량 **무변경** — 순수 측정 배선. `peak_pnl_pct`(ladder 입력) 비접촉, observed_* 전용키만. 신규 동작은 live 중 plan_json에 새 고점/저점마다 DB write 추가(bounded, try-감싸 실패해도 청산 무영향). 검증: 보호영역 회귀 291 passed, 신규 12 passed, py_compile OK, mojibake OK, live_preflight ok=True FAIL 0.
- **롤백조건:** 코드 revert(측정 전용이라 라이브 리스크 없음). 영속화 부담 우려 시 `_persist_observed_excursion` no-op화.
- **잔여:** ① 과거 NULL 244건은 소급 복원 불가(앞으로 안 유실용) ② 영속화 전 첫 1틱에 청산되면 여전히 누락(드묾) ③ Path A는 §3 오독 정정 — 진짜 Path A 청산 9건뿐·이미 5/9 충진이라 별개작업 불필요(gap_pullback/momentum NULL행은 path_type=claude_price=PathB origin라벨). 상세: CLAUDE.md `MD 위반 사항`(2026-06-23 무결성 2-1).

### hold advisor profit_guard 청산 교훈 forward-validation 정식 편입 (#4a, config 토글)

- **무엇:** `minority_report/hold_advisor_exit_lessons.py` 신규 — profit_guard(익절) SELL의 **같은 포지션 'SELL 실현 vs HOLD 지속 forward'** counterfactual을 `score_cell`로 채점·verdict 축적. ① 테이블 `hold_advisor_exit_outcome`(decisions.db) + `collect`(익절 SELL만: hold_mode profit_pullback/target_extension 또는 judge_pnl>0, profit_guard ON 6/16+) ② `backfill_forward`(yfinance, SELL+3거래일 종가→HOLD 지속 net) ③ `rescore`(lesson_validation `score_cell`/`upsert_cells` 재사용, would_be=realized/actual=hold_fwd). 통합 도구 `tools/run_hold_advisor_exit_validation.py`. 세션마감 hook(`trading_bot.py`)에 **토글 게이트** rescore 연결. 테스트 `tests/test_hold_advisor_exit_lessons.py`(9). 설계: `docs/important/HOLD_ADVISOR_LESSON_VALIDATION_HANDOFF.md`.
- **왜:** hold advisor는 자기 판단을 forward-validation 못 받는 **유일 경로**(2자토론 `docs/reports/hold_advisor_review_20260623.md`). 임시 A/B 도구(다른 포지션 SELL vs HOLD = selection bias)와 달리 **같은 포지션 counterfactual**이라 bias 없이 정확. profit_guard prior 효과를 라이브로 검증 → verdict 기반 운영자 토글 판정.
- **현재상태:** 코드 반영. 토글 `HOLD_ADVISOR_EXIT_LESSON_ENABLED=false`(.env.live+config, **기본 off**=동작 변화 없음). **control 자동토글 안 함**(verdict 축적만, profit_guard 토글은 운영자 수동). 주문/리스크/broker truth/Claude 호출량 무변경. 축적=격리 store(validated_lesson)+read-only(decisions.db)+yfinance(봇 루프 밖, 도구). 실행 검증: collected=15·forward 7·cells 2(KR gain +4.52/US +0.71, insufficient=표본 작아 정직). **토글 ON+봇 재시작 시 세션마감 자동 rescore(가벼움). forward 라벨은 `run_hold_advisor_exit_validation.py` 주기 실행 공급.** 전체 QA 2611 passed(65 실패는 사전존재 preopen, 무관).
- **롤백조건:** `HOLD_ADVISOR_EXIT_LESSON_ENABLED=false`(기본). 코드 revert. 축적이라 라이브 무영향.
- **잔여:** regime이 현재 `unknown` 폴백(v2 `market_regime` sync 전) → 운영자 full sync 후 collect 재실행 시 정확한 국면 분할(ON CONFLICT로 갱신). forward 3거래일 성숙 대기 → insufficient→pending→valid 승격은 며칠~2주. #4b 국면 조건부화는 이 verdict 본 뒤(청산 행동 변경, 승인 선행). 커밋: `8ae6fc8`.

---

### brain 오염 정리 turnkey 도구 (봇 정지 정비창용, 미적용)

- **무엇:** `tools/clean_brain_pollution.py` 신규(dry-run 기본). KR issue_patterns 내 US종목 오염(SRPT/BRZE/PAYS 등 3건) 자동 제거 + count<=1 과적합·desc==insight·correction_guide 신선도는 리포트만(판단/주입게이트 영역이라 자동변형 안 함). `--apply` 시 `state/backups/brain_*.json` 백업 선행. 테스트 `tests/test_clean_brain_pollution.py`(3).
- **왜:** 무결성 감사 B영역 — KR brain에 US티커 혼입(시장 오염). **단 brain.json은 봇이 라이브로 씀(version 실시간 증가) → 동시쓰기 경합 위험으로 봇 가동 중 직접 변형 금지.** 그래서 즉시 적용이 아니라 정비창용 turnkey 도구로 제공.
- **현재상태:** 도구만 추가, **brain.json 무변경**(dry-run 검증만: KR 3건 식별 확인). config/env/주문/리스크 무관. fresh-brain 모드(ON)라 brain 오염의 현 라이브 영향은 낮음(selection/judgment/postmortem 주입 차단됨).
- **롤백조건:** 도구 미사용/삭제. `--apply` 후 문제 시 `state/backups/`에서 복원.
- **잔여(운영자, 봇 정지 시):** ① `python tools/clean_brain_pollution.py --apply`(KR US티커 3건 제거) ② regime 전체 백필 `python tools/sync_v2_learning_performance.py --runtime-mode live`. count>=2 주입게이트·correction_guide 신선도 메타는 별도(주입측 코드). 커밋: `970204b`.

### hold advisor prior 정직화 + profit_guard outcome A/B 도구 (변화 검토 후속)

- **무엇:** ① `minority_report/hold_advisor.py::_PROFIT_GUARD_PRIOR` 정직화 — 단일 수치(`-2.36%p` / profit_pullback `-8.91%p` "최악") 제거, "고점 대비 상당 부분 반납(약 70%), 단 **2026-05 강세장 표본 집중·최종 대부분 양수 마감**, 강한 러너 조기절단 금지"로 교체. ② `tools/hold_advisor_outcome_review.py` 신규 — profit_guard ON/OFF(6/16) 전후 HOLD/SELL 실현 net A/B + kill 판정 가이드(read-only). ③ `TODO_ROADMAP` P1에 #4 국면 조건부화 기록.
- **왜:** 2자토론(codex/claude, `docs/reports/hold_advisor_review_20260623.md`) — prior `-8.91%p`는 체리픽(최악 서브셋)+mfe NULL 오염기간(6/19 fix 전)+5월 강세장 confound. 클린 재측정에서 재현 불가(giveback 조인 n=1). 과장 수치가 Claude에 "이익 HOLD가 돈 잃는다"로 읽혀 **최종 88% 양수 마감 승자를 조기절단**할 위험. hold advisor는 forward-validation 밖(자기 교훈 못 받는 유일 경로) → A/B 도구로 라이브 검증.
- **현재상태:** 코드 반영. `HOLD_ADVISOR_PROFIT_GUARD_ENABLED` 토글 **여전히 true**(값 무변경, prior 문구만 정직화). 청산 트리거/주문/broker truth/Claude 호출량 무변경. prior는 hold advisor 프롬프트(Claude 입력) 변경이라 **SELL 성향 소폭 감소(러너 보호) 방향 — 라이브 미검증**, A/B 도구로 추적. **봇 재시작 시 반영.** hold advisor 회귀 61 passed, prior 수치 테스트 하드코딩 없음, mojibake OK.
- **롤백조건:** `HOLD_ADVISOR_PROFIT_GUARD_ENABLED=false`(prior 전체 OFF) 또는 문구 git revert. A/B 도구상 post(ON) SELL이 같은 시기 HOLD보다 net 열위로 굳으면 토글 false. 1차 데이터: post(ON) SELL n=33 `-0.12%`(pre `-0.73%`보다 개선, 표본 작아 판정 보류).
- **잔여:** 정식 lesson_validation `score_cell` 청산-outcome 편입(자동화)은 구조 재작업 → TODO #4. #4 국면 조건부화는 A/B net+ 확인 후(청산 행동 변경, 승인 선행). 커밋: `ae142b0`.

---

### market_regime 0/757 sync-layer fallback 수정 (무결성 감사 C 후속)

- **무엇:** `tools/sync_v2_learning_performance.py`에 `_entry_regime_from_events()` 추가 — CLOSED payload의 `entry_market_regime`이 비었을 때 진입 시점 이벤트(`CLAUDE_PRICE_PLAN_CREATED.plan.context_components_at_creation.consensus_mode` 1순위, `CLAUDE_TRADE_READY.selection_meta.consensus_mode` 2순위)에서 consensus 모드를 읽어 복원. row build의 market_regime을 `close_payload값 or 이벤트fallback`으로 변경. 테스트 `tests/test_v2_learning_regime_fallback.py`(5).
- **왜:** 무결성 감사+회의모델 토론 결론 — regime이 휘발성 in-memory pos에만 저장되고 durable store(path_run/FILLED)에 영속화 안 돼, 멀티데이 보유·봇 재시작으로 pos가 rehydrate되는 청산 경로에서 CLOSED payload까지 전달 실패(0/757). 6/21 커밋(7d03e58)의 자체 롤백조건("신규 진입도 빈 regime이면 진단 재개")이 발동된 상태. sync는 이벤트 스토어(진입 시점 안정 기록)를 읽으므로 register/rehydrate 어느 드롭지점이든 견고하게 우회.
- **현재상태:** 코드 반영. 측정 레이어(`v2_learning_performance.market_regime`)만 변경 — 주문/리스크/broker truth/청산 트리거/Claude 호출량/config/env/brain.json **무변경**. 보호영역(pathb_runtime exit 경로) **무수정**. read-only 검증: 기존 빈 regime closed 279행 중 **172건(62%) 복원 가능**(모드 분포 MILD_BULL 62/MODERATE_BULL 43/NEUTRAL 25/MILD_BEAR 26/CAUTIOUS 16), 나머지 38%는 이벤트에 consensus_mode 없는 구 결정(위조 안 함, 빈값 유지). 신규 청산은 100% 복원(이벤트 존재). **봇 재시작 불필요**(sync는 다음 세션마감에 자동 적용).
- **롤백조건:** `tools/sync_v2_learning_performance.py` 코드 revert(fallback은 close_payload 비었을 때만 작동하므로 기존 정상값 영향 0). 복원된 regime이 실제 진입 모드와 불일치 의심 시 재검토.
- **잔여:** 봇 세션마감 sync는 10일 윈도우라 **최근 10일만 자동 백필**, 전체 279행 백필은 **봇 정지 시 운영자가 full 범위 1회 실행** 필요(`python tools/sync_v2_learning_performance.py --runtime-mode live` — start-date 미지정=전체. 라이브 머신 동시쓰기 경합 때문에 봇 가동 중 실행 금지). mfe 재sync 갭은 별도. 커밋: `d056fad`.

---

### 무결성 전수검토 1차 — 자동검사 도구 + postmortem 가드 + fact 신선도 가드 (배치 A/B/C)

- **무엇:** ① `tools/integrity_audit.py` 신규(8 결정론 모듈: db NULL/freshness/sync, brain 화석화/오염/주입가드, config/state). `state/integrity_audit_report.json` 출력. ② `minority_report/postmortem.py`에 fresh-brain 가드 추가(`_postmortem_fresh_brain_active`) — selection/시장판단과 동일 정책 재사용. ③ `tools/build_claude_decision_facts.py`에 `warn_if_fact_data_stale` 헬퍼 + 소비도구(report/label)가 main()에서 stale이면 exit 2 차단(`--allow-stale`로 강행). ④ `tools/live_preflight.py`에 무결성 감사 **비차단** 표면화(ALERT→WARN). 테스트 `tests/test_postmortem_fresh_brain_guard.py`(5).
- **왜:** brain 5월 화석화 버그(AI·운영자 둘 다 놓침)를 계기로 "코드가 1차로 잡는" 결정론 검사 체계 구축. P0 토론 판정: fact_forward_outcome=죽은 분석파이프라인(26일 동결, 라이브무해/분석함정), execution_id 99.8%NULL=오인헤드라인(watch_only 정상), **market_regime 0/757=확정버그(rehydrate 시 영속화 안 돼 유실)**. postmortem만 fresh-brain 가드 빠져 stale brain이 사후분석 프롬프트에 매번 주입되던 누수 차단.
- **현재상태:** 코드 반영. config/env/토글/brain.json **무변경**. 주문/리스크/broker truth/Claude 호출량 **무변경**(postmortem은 fresh 모드일 때 brain 요약 제거 → 토큰 소폭↓). 현재 `V2_FRESH_BRAIN_START=true`라 postmortem 가드 즉시 효과. **봇 재시작 시 반영.** 전체 QA 2594 passed(65 실패는 사전존재 Py3.9 비호환 `test_preopen_continuation_shadow.py`, 변경 무관).
- **롤백조건:** postmortem 가드는 `V2_FRESH_BRAIN_START=false`면 자동 무효(레거시 주입). fact 가드는 `--allow-stale` 또는 헬퍼 호출 제거. preflight 표면화는 비차단이라 live 시작 영향 없음.
- **잔여(별도 세션):** market_regime 영속화 버그 수정(sync writer가 PLAN_CREATED/TRADE_READY 이벤트 regime fallback 읽기, 런타임 트레이스 선행) + mfe 재sync 갭. brain.json 클린(count≥2 게이트/US티커 제거/신선도 메타)은 운영자 승인 필요. PathB attribution 배선·DB 비대·state 정리는 후속. db_sync 16갭은 정상(오늘 미청산 fill sync 대기), audit_outcomes 41%NULL은 정상(status 태깅, 단 생존편향 주의). 판정 상세: `docs/important/INTEGRITY_AUDIT_PLAN.md §6`, 메모리 `project_integrity_audit_20260623`. 커밋: `3933f12`.

---

### PathB 매도 sell_in_flight 데드락 buster(broker 교차확인) + 미체결 매도 추격 shadow

- **무엇:** ① P0(enforce) — `runtime/pathb_runtime.py::_clear_stale_pathb_closing_lock`에 broker `open_orders` 교차확인(`_broker_confirms_no_pending_sell`) 추가. broker가 "미체결 매도 없음" 확정 + 보유 + grace 60초 경과 시, 로컬 `pending_orders` 잔존과 무관하게 sell_in_flight 락 해제. ② P1(shadow) — exit scan에서 미체결 매도 감지 시 `logs/funnel/unfilled_sell_shadow_*.jsonl`에 gap/지정가pnl/추격pnl 기록(실주문 무영향). 테스트 `tests/test_pathb_closing_lock_broker_truth.py`(11: 데드락 해제 분기 8 + shadow 3).
- **왜:** `_find_pending_order`가 broker 아닌 봇 로컬 메모리만 봐서, 운영자가 broker에서 매도주문을 수동취소하면 TTL 900초가 차도 락이 안 풀리던 데드락(6/22 AVGO 13분 좀비 — 손절·익절 관리 전부 스킵). broker truth 우선 원칙 위반 교정. P1은 "지정가가 판단가에 박혀 가격 하락 시 미체결 방치"되는 누수(AVGO 익절 +4.96%→실현 +1.07%)의 추격(현재가 재제출) net 효과를 enforce 전 측정.
- **현재상태:** 코드 반영. 신규 env 3종 안전 기본값(`PATHB_CLOSING_LOCK_BROKER_CONFIRM_GRACE_SEC=60`, `PATHB_UNFILLED_SELL_SHADOW_ENABLED=true`, `PATHB_UNFILLED_SELL_SHADOW_INTERVAL_SEC=120`) — `.env.live`/config **무변경**(코드 기본값 작동). 주문 수량/가격/청산 트리거/Claude 호출량 **무변경**. broker truth 우선 **강화**(약화 없음, MD 위반 아님). **봇 재시작 시 반영.**
- **롤백조건:** P0는 코드 revert(단 broker 미확인 시 기존 TTL+로컬 fail-closed 경로라 약화 없음). P1은 `PATHB_UNFILLED_SELL_SHADOW_ENABLED=false`. 라이브 데드락 해제 로그(`reason=broker_confirmed_no_pending_sell`) 미발생/오작동 시 재검토.
- **잔여:** 실 broker 조회 경로는 mock 검증(기존 fast_fill과 동일 패턴). 라이브 첫 데드락 케이스 실효 미확인. P1 funnel→추격 net 리뷰 도구 미작성(며칠 shadow 축적 후). 추격매도 enforce(P1 2단계)는 shadow net+ 검증 후. 커밋: `8d0347d`.

---

### selection 프롬프트에 V2 fresh brain 가드 적용 (stale correction_guide 누수 차단)

- **무엇:** `minority_report/analysts.py::select_tickers`에 `_v2_fresh_brain_selection_active()` 가드 추가. `V2_FRESH_BRAIN_START=true`(또는 `V2_BRAIN_POLICY=fresh*`)면 selection 프롬프트에 레거시 `brain_summary[:700]` + `correction_guide[:450]`를 주입하지 않음. 헬퍼는 `trading_bot._v2_fresh_brain_policy_enabled()`와 동일 정책. 테스트 `tests/test_selection_fresh_brain_guard.py`(7: 헬퍼 5 + 실제 프롬프트 캡처 2).
- **왜:** 시장판단 경로(`_brain_context_for_judge`)는 이미 이 가드로 레거시 brain을 차단하는데 **selection 경로만 가드 누락**. 그 결과 `V2_FRESH_BRAIN_START=true`인데도 5/12(US)·5/18(KR)에 동결된 stale `correction_guide`(예: KR "MILD_BULL 이상 합의 차단 / Bull 근거 사용 금지" 급락장 지침)가 6월 강세장 selection 프롬프트에 매일 주입되던 일관성 버그. (correction_guide 자동승격은 5/20 커밋 `4f0cd1c`로 차단됐고 승인큐 consumer 부재로 영구 동결 → "강세장에 급락장 렌즈".)
- **현재상태:** 코드 반영. `V2_FRESH_BRAIN_START`는 이미 `true`(라이브)라 config/env **무변경**. `state/brain.json` **무수정**. 주문/리스크/broker truth/Claude 호출량 무변경(프롬프트 입력 정제, 토큰 소폭↓). **봇 재시작 시 반영.**
- **롤백조건:** `V2_FRESH_BRAIN_START=false`면 레거시 동작 복원(단 시장판단도 함께 레거시로). selection만 되돌리려면 코드 revert. selection 분포/품질 악화 시 재검토 — 단 selection 무엣지 7회 측정이라 위생 처방(개선 보장 아님, 강세장 급락장렌즈 제거가 목적).
- **잔여:** brain.json 오염(count=1 단일사건, KR에 US종목 P036/037/044, market_regime stale)은 fresh brain이면 selection엔 안 가나 telegram/postmortem 등 다른 경로 잔존 — 별도 후속. 커밋: `6a94725`.

---

## 2026-06-21

### 측정 배선 복구 — market_regime 캡처 (regime 0/304 → forward 복구)

- **무엇:** PathB `entry_market_regime`(→`v2_learning_performance.market_regime`)이 항상 빈 값이던 버그 수정. ① `trading_bot._apply_consensus_guards`(consensus 보편 finalizer)에서 안정 per-market 캐시 `self.market_consensus_mode[market]=mode` seed ② PathB에 측정 전용 `_pathb_entry_market_regime()` 추가(캐시 1순위, today_judgment fallback, 없으면 빈 값=위조 금지) → 진입 캡처(11446)가 이걸 사용 ③ intraday_entry_shadow 펀넬 regime도 동일 캐시 fallback.
- **왜:** 진단 결과 `market_regime`이 closed 304건 전부(6/15 이후 진입+청산 41건 포함) 빈 값. 근본 원인 = `today_judgment`가 시장마다 덮어쓰이고 사이클마다 `{}`로 리셋되는 단일 dict라, PathB가 진입/청산/exit-scan 시점에 읽으면 비어있음. 국면조건부 운영·토글 사후검증의 선결 인프라.
- **현재상태:** 코드 반영(라이브 게이팅 `_pathb_consensus_mode`=RISK-OFF cap 입력은 **무변경**, 측정만 복구). **봇 재시작 시 forward 캡처 시작.** 과거 304건은 소급 불가(당시 mode 유실, 빈 값 유지).
- **롤백조건:** 신규 진입이 여전히 빈 regime이면 진단 재개(seed 시점 vs 진입 시점 추가 조사). 라이브 동작 변화는 없어 긴급 롤백 불요.
- **검증:** `tests/test_pathb_entry_regime.py`(7: 캐시우선·per-market격리·fallback·빈값위조금지·라이브게이팅불변·guard seeding) 신규; `test_pathb_runtime.py`(136)·unanimous/consensus(8)·intraday_entry_shadow(5) 통과; 전체 `pytest tests/` 2571 passed(65 fail은 `test_preopen_continuation_shadow.py`의 Python 3.10 전용 `ignore_cleanup_errors` 사용 = 기존 환경 비호환, 본 변경 무관); py_compile OK; mojibake staged OK; `live_preflight --mode live` ok=True FAIL=0.
- **커밋:** 7d03e58
- **남은 배선(미수정, 의도적):** `pnl_pct_net` 203건 NULL = 오염방지 설계대로(forward 정상, 과거 measured net 소급불가). `mfe_backfill_yf` 6/13 정지 = 도구 재실행 필요(별도, 봇 락 경합 감안 go 확인).

### A~F 멀티에이전트 토론 결론 → 검증 안 된 토글 2개 OFF (덜어내기)

- **무엇:** ① C3 당일등락 과열 페널티 OFF (`CANDIDATE_CHANGE_OVERHEAT_ENABLED=false`, env 토글 신규 추가) ② `KR_MOMENTUM_EARLY_ENTRY_ENABLED=false`.
- **왜:** A~F 3자 토론(codex + Claude 빌더 + 사회) 영역 A 사후검증. C3 → US 급등후보(change≥15%) fwd3 +5.4%(live)/+11.5%(backfill)로 페널티가 US 최고 후보를 selection에서 깎음(메모리 "당일등락차단=US −118%p"와 일치), KR도 hit(−3.87)가 non-hit(−6.28)보다 덜 나빠 방향 반전 → 두 시장 모두 근거 반증. KR momentum early → 6/16 enforce 후 체결 0(무발동)·손실경로라 검증 안 된 enforce.
- **현재상태:** 두 토글 OFF(`.env.live` + `config/v2_start_config.json` 동시 반영). **C2(KR vol_ratio) 페널티는 별개 코드 경로로 유지.** 코드(C3 블록·KR momentum early 로직)는 보존, env로 즉시 롤백 가능. **봇 재시작 시 반영**(별도 재시작 트리거 안 함).
- **롤백조건:** C3 재ON은 시장별 live C3 hit 표본 ≥30에서 fwd가 non-hit보다 열위일 때. KR momentum 재ON은 실체결 ≥20·평균 net>0·loss_cap 비율 개선 시.
- **검증:** `tests/test_candidate_overheat_penalty.py`(10, C3 토글 on/off + C2 분리), `tests/test_momentum_early_entry.py`(8) 통과; py_compile OK; JSON 유효.
- **커밋:** 7d03e58
- **보류(미적용):** 사이징 vol-target(REJECT·알파없음), 청산 정밀화/target extension(REJECT·3번째 확인, seed였던 TARGET capture 1.56은 yfinance 오측 아티팩트), cluster throttle shadow(측정배선 선결로 보류), PEAD(현행유지). **다음 본작업 = 측정 배선 복구**(pnl_pct_net 75% NULL / mfe_backfill 6/12 정지 / market_regime 빈값 — 모든 토글 사후검증의 선결).

## 2026-06-20

### net FX 스프레드 정직화 (자 고치기 — 측정만, 거래로직 무관)
- **무엇:** ① 생산자 `execution/claude_price_sell_manager.py::_close_cost_meta`에 FX 인지 net 필드 추가(`fx_spread_pct_round_trip`/`pnl_pct_net_after_fx_est`/`fx_spread_krw_est`/`pnl_krw_net_after_fx_est`). 기존 `pnl_pct_net_est`(수수료만)는 보존. ② 소비자 `tools/capture_net_review.py`에 `DEFAULT_FX_SPREAD_PCT={US:0.2,KR:0}` + `--fx-us/--fx-kr` CLI, net_of/손익분기/헤더 반영.
- **왜:** net이 수수료(0.5%)만 빼고 **환전 스프레드(US 환전 2회)를 0으로 둬서** 과대평가됨(usd_krw=참조환율). 성능을 재려면 자가 정직해야 함.
- **현재상태:** capture_net_review 재실행 **US net합 +1.3%→−61.0%(PF 1.01→0.78)** — 본전 아니라 손실이 드러남(우대0.2% 가정, 무우대면 더). 생산자 필드는 **재시작 후 신규 청산부터** payload 기록(측정 전용, 주문/리스크/exit 무관). env `US_FX_SPREAD_RATE_PER_SIDE`(기본0.001=우대). **11 테스트 통과, py_compile ok, 모지바케 0.**
- **롤백조건:** `--fx-us 0`(리포트) / 생산자 필드는 additive라 무해(기존 net 불변). 우대 확정 시 값 조정.
- **잔여:** 생산자 새 필드가 ledger sync·대시보드 미배선(capture_net_review만 정직, 후속).
- **커밋:** 이 커밋.

## 2026-06-19

### #2 상시 정합성 체크 도구 신설 (정합성 스윕 종결자)
- **무엇:** `tools/integrity_check.py`(read-only 진단) + 테스트 12. 오늘 손으로 캔 두 부류를 자동 탐지: A형(학습원장 핵심필드 충진율 — mfe/mae/regime/net), D형(잡 stale freshness — forward 측정기·sync·outcome 최신 age), sync 커버리지. OK/WARN/FAIL + exit code.
- **왜:** A(mfe 배선)는 손으로, D(forward 측정기 3주 정지)는 우연히 캤다. 사람이 안 보면 사일런트 브레이크를 못 잡는다 → 자동 깃발 필요.
- **현재상태:** ① 단독 실행(`python tools/integrity_check.py`) + `--watch`/`--telegram-alert` 루프 모드. ② **`start_live_stack.bat`에 stack 탭으로 상시 가동 배선**(`--watch --interval-sec 600 --telegram-alert`, live_guardian 패턴). FAIL **변동 시에만** 텔레그램(상태파일 fingerprint, 복구 시 정상알림 — 스팸 방지). 첫 실행: 잡 freshness 전부 🟢(D 치유 확인), mfe/mae/regime 🔴(A fix 배포 직후 전환상태 — 재시작후 청산 누적되면 🟢로, **이게 A fix 자동 검증 루프**). **다음 stack 재기동 시 탭 가동.**
- **롤백조건:** bat 탭 줄 제거 / 토글 없이 프로세스만 종료. read-only라 주문·DB쓰기 무관.
- **잔여:** ① 필드 충진은 절대임계라 배포 직후 전환구간 빨강(전환완료 후 의미) → 추세기반(전주 대비 급락) 보강 후보. ② telegram_reporter 자격 없으면 send_skipped(알림만 누락, 체크는 계속).
- **커밋:** 미커밋.

### 배선 fix: 기존 broker_sync 포지션도 PathB 귀속 복구 (AVGO 자가치유 갭)
- **무엇:** `_sync_runtime_with_broker` verify 경로에, 이미 broker_sync로 굳은(PathB 귀속 없는) 포지션이 단일-호환 PathB run을 가지면 `_pathb_broker_recovery_template`로 귀속 복구 + target/stop 복원 + ack'd run을 FILLED 전환. 회귀 테스트 1건.
- **왜:** 직전 fix(ack'd 주입 복구)는 신규 broker 주입 경로에만 붙어 `seen_keys` 때문에 **이미 broker_sync로 굳은 기존 포지션엔 안 닿았다** → 재시작해도 AVGO 자가치유 안 됨(라이브로 확인). verify 경로 보강으로 메움.
- **현재상태:** **재시작 후** 다음 broker sync 사이클에 AVGO가 broker_sync→claude_price 귀속 + target 404/stop 379 복원 + run FILLED. 검증: broker_sync 메타 7 + reconcile 회귀 177 passed, preflight ok=True FAIL=0.
- **롤백조건:** 코드 되돌림. broker truth 1차·단일매칭·micro_probe/recovery_micro 제외·충돌시 무변경(생성·주문·sizing 무관, 기존 체결 귀속만).
- **잔여:** 동일 ticker 수동 lot 우연일치 오귀속(기존 복구와 동일 리스크, 충돌→manual). 라이브 AVGO 치유 다음 사이클 관측.
- **커밋:** 미커밋.

### 배선 fix: broker sync ack'd run 치유 (AVGO 고아 — 정합성 스윕 보강)
- **무엇:** `_pathb_broker_recovery_template`의 `eligible_statuses`에 `ORDER_ACKED`/`ORDER_SENT` 추가 + 단일-호환 매칭이 ack'd run이면 브로커 보유 truth(실평단·수량)로 `mark_filled`해 run을 FILLED로 전환. 회귀 테스트 1건(AVGO 시나리오).
- **왜:** 매수 ack 후 fill 확정이 끊긴 채(세션경계 재시작) 브로커엔 실제 보유로 남은 PathB run이, 메타 복구 대상(ack'd 제외)에서 빠지고 FILLED 전환도 안 돼 broker_sync generic으로 귀속 → PathB target/stop 관리 밖 고아(AVGO 6/17 d6df4eab). 봇이 fill 확정을 인-세션·당일 today_fills·동일 세션에 의존하던 3중 가정의 구멍.
- **현재상태:** 단일-호환 broker 보유면 자동 PathB 귀속 + target/stop 복원 + run FILLED. **AVGO는 봇 재시작/동기화 사이클에 소급 치유.** 미커밋→커밋. 검증: broker_sync 메타 6 + reconcile 회귀 205 passed, preflight ok=True FAIL=0.
- **롤백조건:** 코드 되돌림(eligible_statuses에서 ack'd 제외). 신규 주문·sizing·하드스톱·broker truth 우선 무변경(기존 체결 귀속 복구). 충돌 시 manual로 빠짐.
- **잔여:** 동일 ticker 수동 lot이 ack'd run qty·가격(5%)과 우연 일치 시 오귀속 가능(기존 FILLED 복구와 동일 리스크, 충돌→manual). 라이브 AVGO 치유는 다음 동기화 사이클에 관측.
- **커밋:** 미커밋.

### 배선 fix: Phase 1c MFE/MAE/regime → 학습 원장 (정합성 스윕 A칸)
- **무엇:** PathB 자동청산 CLOSED 이벤트 발행자 `mark_closed`에 `mfe_pct`/`mae_pct`(운영자) + `entry_market_regime`(완결) 인자 추가 → CLOSED payload에 `position_mfe_pct`/`position_mae_pct`/`entry_market_regime` 포함(값 있을 때만, 0/빈값 위조 안 함). 호출부 2곳(`_finalize_pathb_sell_close`/`on_external_close`)에서 exit_meta·closed_trade의 값 전달. 회귀 테스트 3건.
- **왜:** Phase 1c가 계산한 observed MFE/MAE와 진입국면이 mark_closed 호출에서 끊겨 CLOSED payload 미포함 → sync(`sync_v2_learning_performance.py:971,979`)가 못 읽어 `v2_learning_performance` mfe_pct 97%·mae_pct 96%·market_regime 100% NULL. profit_guard/weak_mfe/국면조건부 capture를 라이브 데이터로 못 재던 근본 원인.
- **현재상태:** 배선 완결(observed→exit_meta→mark_closed→CLOSED payload→sync→learning). **봇 재시작 시 적용**(현재 봇은 옛 코드). 미커밋. 검증: cost_meta 10 + 보호영역 232 passed, mojibake clean, py_compile OK.
- **롤백조건:** 코드 되돌림(인자 제거). observe-only 키라 주문·리스크·ladder(peak_pnl_pct) 무영향이므로 위험 낮음.
- **잔여:** ① 과거분 forward-only(백필 별도). ② mark_closed 안 타는 기타 CLOSED 10건/114 미적용(reconcile/manual, 볼륨 경로 아님). ③ 정합성 스윕 나머지 칸 B(net 41% 누락)/C(미생산 필드)/D(forward 라벨)/E(selection 라이트백) 미착수.
- **커밋:** 미커밋.

### 운영 조치: forward 측정기(v2_daily_loop) 3주 정지 → 재실행 catch-up (정합성 스윕 D칸)
- **무엇:** ① `v2_daily_loop` 1회 재실행(dry-run 검증 후 실제, `--market ALL --forward-lookback-days 20 --skip-simulation --skip-optimizer`) — 백로그 catch-up. ② **영구 배선(코드)**: `trading_bot._run_v2_forward_measure_at_session_close` 추가, 세션마감에 forward 측정을 sync보다 먼저 자동 호출(기존 sync 훅과 동일 subprocess 패턴, env `V2_FORWARD_MEASURE_AT_SESSION_CLOSE` 기본 true). FORWARD_MEASURED 169→389(+220), `forward_complete` 6월 0→42.
- **왜:** 이 잡이 **2026-05-27 이후 정지**(봇이 호출 안 함=외부 cron/수동, 죽음). 입력 price CSV는 신선했으나 측정만 3주 밀려 forward_complete 100% NULL. 하류로 lesson candidate_builder(`forward_complete=any(FORWARD_MEASURED)`)·lesson_forward_validation을 굶겨 **"적용 검증교훈 0개" 결론이 오염**됐을 가능성.
- **현재상태:** 백로그 catch-up 완료(완료가능 6/1~6/12 중 42/68, 최근 42건은 forward 미경과 정상 pending). 영구 배선은 **봇 재시작 후** 다음 세션마감부터 발화 → 라이브 1회 확인 필요(코드만으론 미검증). event store WAL+30s라 US장중 경합 안전. 검증: py_compile OK, cost_meta 10 passed, mojibake clean.
- **롤백조건:** env `V2_FORWARD_MEASURE_AT_SESSION_CLOSE=false`(즉시 무효화). 측정 데이터 기록일 뿐 주문/브로커 무관.
- **잔여:** ① 완료가능인데 미완료 26건(partial-horizon/sync join 의심) 추가 진단. ② 봇이 세션마감에 도달 못하면(크래시·수동중단) 여전히 안 돎 → **#2 상시 정합성 체크**(잡 stale 자동 감지)가 최종 안전망. ③ catch-up 후 lesson forward-validation "0 valid lessons" 재평가 필요.
- **커밋:** 미커밋.

## 2026-06-18

### PathB 진입 fast-fill — enforce 라이브 재호가 (운영자 "수정해서 인포스로")
- **무엇:** `_fast_fill_requote_run`/`_fast_fill_resubmit` 추가. 데드존(미체결+가격>limit)에서 옛 주문 취소→broker truth 취소확정→미체결 확인 시 새 path_run으로 bounded 가격 재진입. config `(US_/KR_)PATHB_FAST_FILL_MODE=enforce`(shadow→enforce 전환).
- **왜:** 운영자 결정 — 측정(shadow)이 아니라 실제로 못 산 걸 bounded로 사 넣어야 함. shadow는 그가 원한 게 아니었음.
- **현재상태:** **config enforce(US+KR). 단 재시작 시 적용**(현재 봇은 옛 코드). 이중매수 가드: 옛 주문 체결시 실체결가 기록만(#1)·재제출X, open이면 대기·재제출X, 취소확정+미체결일 때만 재진입. bound `MAX_CHASE_PCT=1.0`/`MIN_REWARD_PCT=1.5`. 검증: fast_fill 10 + PathB 회귀 194 + preflight ok FAIL0. **라이브 broker 왕복 미검증(로직 테스트만).**
- **롤백조건:** `(US_/KR_)PATHB_FAST_FILL_MODE=off`. 라이브 이상 시 즉시.
- **잔여:** ① 라이브 broker 왕복(취소+재제출) 라이브 미검증 → US장 중 재시작 지양, 마감장/개장전 점검 권고. ② reentry guard가 재진입 차단 가능(그럼 미체결=손해無). ③ #1 cost-basis는 재호가 체결경로만 실체결가 기록, 전체 장부 진실화는 별도.
- **커밋:** 미커밋.

### PathB 진입 fast-fill — 데드존 bounded 재호가 측정 (shadow→enforce로 대체)
- **무엇:** `runtime/fast_fill.py`(순수 결정엔진) + `pathb_runtime.py` cancel_above 데드존 shadow 측정 훅. config `(US_/KR_)PATHB_FAST_FILL_MODE=shadow`, `MAX_CHASE_PCT=1.0`, `MIN_REWARD_PCT=1.5`.
- **왜:** 093370 실측 — 봇 limit(17900) 미체결인데 가격이 cancel 임계(~18795) 아래 "데드존"에 16분 방치 → 깨끗한 진입 미스 → 운영자가 bound 없이 수동 추격 → target 위 과지불 손실 + 봇이 17900으로 잘못 귀속해 가짜 +3% 양수.
- **현재상태:** **shadow 측정(주문 무영향).** 데드존에서 "bounded 재호가하면 잡혔나(REQUOTE) vs 추격하면 손실(MISS)"를 funnel(`logs/funnel/fast_fill_*.jsonl`) 기록. 결정엔진은 enforce-capable이나 **라이브 재호가 실행(주문 취소+재제출)은 의도적으로 미배선**(라이브 주문 교체 = fragile broker-truth flow, 별도 테스트 단계). enforce 현재=shadow와 동일(로깅만). 093370 시뮬: @18000 REQUOTE(승), @18460 MISS. 검증: 테스트10 + PathB회귀174 + preflight ok FAIL0 + 인코딩 clean. **재시작 시 shadow 측정 시작. US·KR 둘 다.**
- **롤백조건:** `(US_/KR_)PATHB_FAST_FILL_MODE=off`. enforce(라이브 재호가)는 shadow가 시장별 net+ 증명 + 주문교체 테스트 통과 후.
- **잔여:** ① 라이브 재호가 실행 미배선(default shadow라 무영향). ② **장부 오염(#1): broker 실체결가 cost-basis 기록**은 별도 — 수동개입/슬리피지 시 봇 limit≠실체결가로 표시손익 부풀림(093370 가짜양수의 근본). 미구현, 운영자 결정 대기.
- **커밋:** 미커밋.

### 꼬리-capture 엔진 — 전체 enforce-capable 완성 + shadow preset (운영자 결정)
- **무엇:** target override(엔진 active 시 ladder+claude_price target 억제, trail이 profit-side 소유) + 오버나잇 carry execution(`should_carry_overnight` → pre_close skip) 추가. **`TAIL_CAPTURE_MODE=shadow`·`HOLD_ADVISOR_CARRY_ALIGN_MODE=shadow`로 preset**(.env.live+config json).
- **왜:** 운영자 결정 "전부 enforce-capable로 짓고 config로 동작 결정, shadow면 무위험." 재시작만 하면 shadow 동작.
- **현재상태:** **shadow(로깅만, 라이브 0).** 전부 enforce-capable(trail/target override/carry/hold align) — config 토글로 enforce. **오버나잇 carry 실행은 서브게이트 `CARRY_ENFORCE=false`**(검증 후). 하방=loss_cap 위임. 검증: 테스트25 + 보호영역 회귀 308 + preflight ok + shadow 안전성. **재시작 시 shadow 가동.**
- **롤백조건:** `TAIL_CAPTURE_MODE=off`. enforce 전환은 forward 재구성 검증(carry net+ & 손실누수0 & 약세장) 후.
- **잔여(게이트됨):** carry-enforce 켤 때 기존 `_pathb_session_close_carry`와 상호작용 검증 필요(현재 CARRY_ENFORCE=false라 비활성).
- **커밋:** 미커밋.

### Track 3-R: tail capture ↔ hold advisor carry-intent 정합 — 빌드 (운영자 #1 레버)
- **무엇:** `minority_report/hold_advisor.py`에 carry-intent 정합. `HOLD_ADVISOR_CARRY_ALIGN_MODE=off`(기본).
- **왜:** tail_capture 엔진이 "carry" 깃발 꽂아도 hold advisor가 conf<0.72 HOLD를 강등(intraday_review/pre_close SELL)시켜 러너를 죽임 = 엔진 사보타주. 정합 없이는 enforce 무의미.
- **현재상태:** **기본 off.** enforce면 carry-intent HOLD(이익+MFE≥4%+RISK_ON)이 conf<0.72여도 bounded HOLD 존중. **손실중·약세장·MFE미달 무영향(0.72=손실방어 불변).** shadow=로깅. 보호영역 회귀 312 passed + preflight ok. 봇 재시작 시 off로 탑재.
- **롤백조건:** `HOLD_ADVISOR_CARRY_ALIGN_MODE=off`. 즉시.
- **커밋:** 미커밋. tail capture와 같은 shadow에서 검증.

## 2026-06-17

### 통합 꼬리-capture 청산 엔진 — shadow 빌드 (운영자 결정)
- **무엇:** `runtime/tail_capture.py`(결정엔진) + `pathb_runtime.py` exit scan 훅(shadow 로깅/enforce trail). config `TAIL_CAPTURE_*` 12키 `.env.example`, **전부 OFF.**
- **왜:** 시스템=멀티데이 꼬리-수확기(net=상위10%). 꼬리의 24%(+67%p) 새는 게 본전의 원인. path-aware 시뮬 +33%p(증명후 wide-trail). selection 아니라 **꼬리 capture가 유일 레버.**
- **현재상태:** **기본 OFF.** shadow=funnel 로깅(라이브0)/enforce=trail 실청산(loss_cap 뒤, ladder 앞=하방위임). **CARRY 실행은 서브게이트 `CARRY_ENFORCE=false`**(cross-day 갭 미검증). 검증: 테스트13 + PathB회귀256 + preflight ok FAIL0. **봇 재시작 시 OFF로 탑재.**
- **롤백조건:** enforce 이상 시 `TAIL_CAPTURE_MODE=shadow/off`. 보호영역(ladder/preclose/claude) 무접촉(additive)+fallback.
- **커밋:** 미커밋. 설계 `docs/important/TAIL_CAPTURE_ENGINE_DESIGN_20260617.md`.

### 후보 프롬프트 풀 캡 축소 — 토큰 절감 (운영자 결정)
- **무엇:** `config/v2_start_config.json` 후보 캡 **US 40→24, KR 32→28** (7키: CANDIDATE_PROMPT_POOL_TARGET/HARD_CAP_KR·US, KR_PROMPT_POOL_CAP, KR_SELECTION_PROMPT_CAP, US_PROMPT_POOL_CAP).
- **왜:** `select_tickers`가 전체 input 토큰의 **40%(1.11M/3일)** = 단일 최대 소비처. 후보확대(v3)는 측정상 **진입품질 0 기여**(memory §3-b: v2≈v3 MFE) + 오늘 KR 수익 종목 전부 **rank 1~4**(top-24면 다 잡음, 확대구간 0). US 40은 CLAUDE.md 문서값(24) 초과 드리프트였음 → 문서값 복귀.
- **현재상태:** US 24 / KR 28 (CLAUDE.md candidate funnel 문서값과 일치). 후보 ~40% 컷 → 전체 토큰 ~15% 절감 추정. 오늘류 수익(rank1-4) 무영향 예상. **봇 재시작 시 반영.**
- **롤백조건:** trade_ready 후보 부족/missed runup 증가 시 KR 28→32·US 24→40 복귀. 즉시.
- **커밋:** 미커밋. `.env.live`엔 해당 키 없음(config json만 보유, override 우위).

### 교훈 forward-validation 레이어 — enforce 적용 (운영자 결정)
- **무엇:** 신규 교훈검증 파이프라인 탑재 + `LESSON_VALIDATION_ENABLED=true`, `LESSON_VALIDATION_APPLY_MODE=enforce`. (`.env.live` + `config/v2_start_config.json` 둘 다)
- **왜:** 기존 lesson은 빈도로만 채점(forward 검증 0) → 함정 교훈(watch_only 완화 forward −5.9%) 통과 위험. 반사실 gain(국면별)+cost_floor+부호일관+신선도+신뢰로 채점, 검증통과만 bounded control(`entry_priority_cutoff_adjust` ±0.05) 국면조건부 반영.
- **현재상태:** **valid_apply 0개 → enforce 켜도 안전 no-op**(기존 tuner값 유지). 세션마감마다 자동 축적(`trading_bot.session_close` hook, config 무관). valid 되면 bounded 적용, 함정(KR/risk_off invalid_block) 차단. brain/broker/hard safety 무접촉. **봇 재시작 시 반영.**
- **롤백조건:** 이상 시 `LESSON_VALIDATION_APPLY_MODE=shadow`(관측만) 또는 `LESSON_VALIDATION_ENABLED=false`(완전 OFF) 후 재시작. 즉시 롤백.
- **커밋:** 미커밋(운영자 검토 후). 검증: 모듈테스트 31 + 인접 32 + preflight ok=True FAIL0 + 운영 enforce 체인테스트.

## 2026-06-16

### 운영 통제 / 원칙
- **현실적 직언 원칙** 추가 — 긍정 편향 말고 데이터대로. [CLAUDE.md]
- **운영자 파악 가능성 원칙** 추가 — 변경 시 무엇/왜/현재상태 매번 명시, 롤백도 보고. [`4c662fc`]
- **운영 변경 일지(이 파일)** 신설.
- 커맨드 신설(.claude/commands, 로컬·gitignore): `/status`(현재 토글+커밋+봇상태), `/check`(검증), `/capture`(성과), `/monitor`(라이브점검), `/saveyou`(세션저장).

### 트레이딩 (재배선 1단계 — 청산 변별)
- **weak_mfe_cut OFF** (US/KR) — 단타 손절은 번지수 오인(손실 HOLD=stop_recovery는 정상). 청산을 hold advisor에 환원. **롤백**: `(US_/KR_)PATHB_WEAK_MFE_CUT_ENABLED=true`. 코드 보존. [`bcbd8ff`]
- **hold advisor 이익보호 prior ON** (`HOLD_ADVISOR_PROFIT_GUARD_ENABLED=true`) — 이익 중 HOLD 반납(70%, profit_pullback -8.91%p) 방지·익절 우선, 단 추세 살아있으면 러너 HOLD 유지. A/B 변별 -10%p→+25%p. **롤백**: 토글 false. **라이브 미검증 → 미국장 모니터.** [`bcbd8ff`]
- **capture_net_review 확장** — 실측 MFE 기반 net capture + 월별 국면 분해(forward 착시 분리). [`bcbd8ff`]
- 분석 도구 신설: hold_advisor_quality_test / ab_test / discriminate_test. [`bcbd8ff`]

### 진입 (momentum 진단)
- **momentum early-entry 진단 임계를 게이트와 통일** — momentum_wait 오분류 제거. [`cbb727b`]
- KR 청산 tp_check 미설정 KeyError 가드. [`67a6549`]

### 기각 (왜 안 했나 — 반복 논의 방지)
- **잡전략 OFF** — 무효. 손실의 PathB 경로(claude_price)라 전략 토글이 안 먹음.
- **profit_ladder 강화** — 보호영역 + 2026-06-14 롤백 이력. 설계+백테스트 선행 필요.
- **진입 빈도↓ (confidence↑)** — 진입 단일경로라 수익(target)도 위축.
- **진입 타이밍 튜닝** — 못 이기는 게임 + 반복 실패. 눌림매수 추종형은 구조적으로 늦음(정상).

### 검증 대기 (다음 행동)
- 미국장 라이브: prior가 giveback 줄이나 + **좋은 러너 오절단 부작용 없나** + weak_mfe OFF가 회복 vs 손실확대. 악화 시 prior 토글 롤백.
- 다음 숙제: 진입 변별 shadow → 측정 재정의 코드 → 수수료/KR.
