# Claude 입력 품질 감사 보고서

- 작성일: 2026-06-07
- 기준 요청서: `docs/claude_input_quality_audit_request.md`
- 범위: 장판단 / lesson candidate / hold advisor Claude 입력 흐름
- 작업 방식: 읽기 전용 코드 추적 + 최근 raw-call 품질 리포트 생성

## 감사 범위와 보호 영역

이번 감사에서 런타임 코드, 주문/리스크 로직, broker truth, `.env*`, `config/v2_start_config.json`, `state/brain.json`은 변경하지 않았다.

보호 영역 영향:

- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard: 변경 없음
- PathB broker-truth entry fail-closed: 변경 없음
- PathB sizing reason split: 변경 없음
- zero-holding stale reconcile: 변경 없음
- KIS order normalization: 변경 없음
- Path A / Path B routing contract: 변경 없음
- `_sync_runtime_with_broker()` broker truth 우선순위: 변경 없음
- `state/brain.json` 자동 정책 메모리 승격: 코드 변경 없음, 단 리스크 발견

추가 생성한 증거 리포트:

- `docs/reports/claude_input_quality_audit_evidence_20260607/US/claude_io_quality.md`
- `docs/reports/claude_input_quality_audit_evidence_20260607/KR/claude_io_quality.md`

## 1. 데이터 흐름 요약

### 장판단 / Market Judgment

관련 파일 / 함수:

- `trading_bot.py:8009` `_brain_context_for_judge()`
- `trading_bot.py:14771` `_build_market_judgment_prompt()`
- `trading_bot.py:14809` `_build_intraday_context()`
- `trading_bot.py:27460` 부근 session open judgment 생성 흐름
- `trading_bot.py:33727` `_reinvoke_analysts()`
- `minority_report/analysts.py:1686` `call_analyst()`
- `minority_report/analysts.py:1793` `call_analyst_debate()`
- `minority_report/analysts.py:1930` `get_three_judgments()`
- `minority_report/raw_call_logger.py:117` `save()`

입력 필드 / 데이터 출처:

- digest: `build_kr_digest()` / `build_us_digest()` 결과를 `digest_to_prompt()`로 변환
- current override: 장중이면 KIS live index, breadth, 현재 보유 포지션, 장중 컨텍스트를 추가
- brain context: `_brain_context_for_judge()`에서 V2 fresh brain 문구 또는 `BrainDB.generate_prompt_summary()`
- active lessons: `minority_report.active_lessons.build_active_lesson_context()`
- portfolio info: `_build_portfolio_info()`

Claude 전달 구조:

- R1: persona + breadth contract + hard/soft boundary + active lessons + brain/correction + digest full prompt
- R2: 자기 R1, 다른 분석가 R1, debate history, active lessons, digest 800자 요약

저장 / 소비:

- raw prompt/response: `logs/raw_calls/YYYYMMDD_{market}_analyst_*`
- runtime state: `today_judgment`
- 공유 캐시: `shared_judgment_cache`
- 세션 기록: `logs/daily_judgment/{mode}_{YYYYMMDD}_{market}.json`
- 소비자: consensus guard, selection, risk sizing, dashboard `/api/claude/status`

### 교훈 후보군 / Lesson Candidates

관련 파일 / 함수:

- `trading_bot.py:2019` `_build_ops_review_snapshot()`
- `trading_bot.py:2522` `_build_lesson_candidates()`
- `trading_bot.py:2638` `_load_lesson_candidate_summary()`
- `trading_bot.py:2709` `_persist_lesson_candidates()`
- `minority_report/lesson_quality.py:48` `lesson_quality_fields()`
- `minority_report/active_lessons.py:383` `build_active_lesson_context()`
- `minority_report/postmortem.py:364` `run()`
- `dashboard/dashboard_server.py:445` `_dashboard_active_lessons_payload()`

입력 필드 / 데이터 출처:

- ops review metrics: trade_ready conversion, watch_only missed runup, continuation PnL, unanimous mismatch 등
- session decision events: affordability fail cluster
- postmortem lesson candidate append
- manual/data_analysis/postmortem source는 pinned로 보존

저장 / 소비:

- 저장소: `state/lesson_candidates.json`
- prompt 소비: `active_lessons`가 market별로 selection scope, `truth_status=fresh`, `breached=true`, `action_hint` 존재, `min_sample` 충족 항목만 선택
- dashboard: `/api/active-lessons`, `/api/claude/status` 일부 payload

현재 품질 장치:

- `watch_only_missed_runup_ratio`, `trade_ready_signal_conversion`만 Claude-actionable
- execution/strategy/consensus scope는 `ops_flag=True`로 prompt injection 차단
- stale/manual review/not actionable/below min sample/expired 필터 존재
- 상충하는 watch_only vs trade_ready lesson은 conflict guard로 한쪽 suppression

### Hold Advisor

관련 파일 / 함수:

- `minority_report/hold_advisor.py:393` `_triage_case_payload()`
- `minority_report/hold_advisor.py:452` `_build_triage_prompt()`
- `minority_report/hold_advisor.py:516` `_build_challenge_prompt()`
- `minority_report/hold_advisor.py:833` `_ask_triage()`
- `minority_report/hold_advisor.py:626` `_ask_challenge()`
- `minority_report/hold_advisor.py:1207` `_ask_one()`
- `minority_report/hold_advisor.py:1461` `ask()`
- `minority_report/hold_advisor.py:1751` `_log_decision()`
- `runtime/pathb_runtime.py:10285` `_attach_pathb_position_metadata()`

입력 필드 / 데이터 출처:

- position: ticker, market, strategy, entry/current, PnL, peak PnL, hold time, TP/SL/trail
- PathB metadata: `pathb_reference_target`, `pathb_reference_stop`, `pathb_plan`, entry route
- advisor context: `advisor_context_v2`, exit signal, OR status, quote quality, hard stop distance, invalid_if
- live market context: `phase1_trainer.digest_builder.build_intraday_advisor_context()`
- stage policy: `TP_REVIEW`, `PRE_SESSION`, `INTRADAY_REVIEW`, `MAX_HOLD`, `PRE_CLOSE_CARRY`, `SOFT_EXIT`, `AUTO_SELL_REVIEW`

Claude 전달 구조:

- triage/challenge: 구조화 JSON case payload
- legacy 3 analyst: 사람이 읽는 position/context prompt
- HOLD는 `protective_stop`, `invalid_if`, `next_review_min`을 요구

저장 / 소비:

- raw prompt/response: `logs/raw_calls/*hold_advisor*`
- decision log: `logs/hold_advisor/decisions_YYYY-MM-DD.jsonl`
- runtime consumer: PathA/PathB auto sell review, pre-session review, intraday review, max hold, TP review

## 2. 품질 평가

| 대상 | 평가 | 근거 |
| --- | --- | --- |
| 장판단 | 운영 리스크 있음 | live index override, raw-call 저장, daily_judgment 저장은 양호하다. 다만 selection scope active lesson이 market mode R1/R2에도 들어가고, `brain.json` 직접 갱신 경로가 남아 있다. |
| lesson candidate | 비차단 개선 필요 | actionability/scope/truth/sample/expiry 필터는 적합하다. 다만 active lesson이 selection 전용인지 market judgment 전용인지 더 강하게 분리해야 한다. |
| hold advisor | 비차단 개선 필요 | triage/challenge JSON 구조와 bounded HOLD 요구는 적합하다. stage별 입력 completeness와 PathB profit-ladder/pre-close 결과 가시성은 더 보강할 필요가 있다. |

최근 raw-call 근거:

- US 2026-06-05~06: raw_calls 63건, parse_errors 2건, select_tickers 평균 input 11,736 tokens
- KR 2026-06-05~06: raw_calls 62건, parse_errors 0건, select_tickers 평균 input 11,821 tokens
- 양 시장 모두 select_tickers prompt가 반복적으로 8k~12k tokens 이상
- postmortem은 KR/US 모두 약 49초 slow call 및 non-strict JSON wrapper/fence 감지
- KR `prompt_mojibake_hangul_compat_jamo` 감지는 샘플 확인상 U+318D(아래아) 같은 정상 한국어 문자도 잡는 false positive 가능성이 높아, 도구 규칙 개선 필요

## 3. 발견 사항

### F-01. `state/brain.json` 직접 갱신 경로가 운영 계약과 충돌할 수 있음

- 심각도: 운영 리스크 있음, 설계 확정 후 수정
- 대상: 장판단 / postmortem / hold advisor memory
- 위치: `claude_memory/brain.py:702`, `claude_memory/brain.py:993`, `claude_memory/brain.py:1132`, `minority_report/analysts.py:1930`, `minority_report/postmortem.py:364`
- 현재 동작: R1/R2 debate, postmortem beliefs, issue pattern, daily record, hold advisor performance가 `BrainDB.save()`를 통해 `state/brain.json`에 직접 저장될 수 있다.
- 왜 문제인지: 저장소 운영 계약은 `state/brain.json`을 runtime truth가 아닌 정책 메모리로 취급하고, 승인형 워크플로우 전 자동 정책 메모리 승격을 보류하도록 요구한다. 다만 `active_lessons.py` 읽기 경로에는 `truth_status`, `breached`, `min_sample`, scope 필터가 있어 즉각적인 판단 오염으로 단정할 증거는 없다.
- 운영 영향: 직접 갱신 경로 때문에 `state/brain.json`이 계속 dirty해질 수 있고, 승인되지 않은 debate/history/belief 항목이 별도 guard를 우회하는 prompt 경로에 노출될 여지가 있다.
- 권장 수정: `brain.json` 직접 정책 갱신을 approval queue 또는 `state/lesson_candidates.json` 후보 append로 전환한다. runtime 통계가 필요하면 별도 metrics store로 분리하고 prompt-visible 정책 필드는 승인 후 반영한다.
- 필요한 테스트: postmortem이 `new_lesson`을 바로 `update_beliefs()`하지 않는 테스트, debate 저장이 approval queue로 가는 테스트, active_lessons가 legacy brain을 기본 차단하는 테스트, live preflight brain dirty guard 회귀.

### F-02. Selection lesson이 market judgment prompt에 주입됨

- 심각도: 운영 리스크 있음
- 대상: 장판단 / lesson candidate
- 위치: `minority_report/analysts.py:1686`, `minority_report/analysts.py:1930`, `minority_report/active_lessons.py:383`
- 현재 동작: `watch_only_missed_runup_ratio` 같은 selection scope lesson이 analyst R1/R2 prompt의 `[recent lesson candidates]`에 들어간다.
- 왜 문제인지: market mode 판단은 breadth/지수/매크로가 1차 근거여야 하는데, trade_ready 승격 힌트가 mode/size 판단을 공격적으로 기울게 할 수 있다.
- 운영 영향: 장판단과 종목 선정 품질 이슈가 섞여, selection 개선용 lesson이 market mode 또는 `new_buy_permission` 판단에 영향을 줄 수 있다.
- 권장 수정: active lesson에 `target_prompt_scope`를 추가해 `market_judgment`, `selection`, `hold_advisor`를 분리한다. selection lesson은 기본적으로 `select_tickers`에만 주입하고, market judgment에는 market breadth/consensus 관련 lesson만 허용한다.
- 필요한 테스트: analyst R1/R2 raw prompt에 selection-only lesson이 들어가지 않는 테스트, select_tickers에는 selection lesson이 들어가는 테스트, dashboard active lessons scope 표시 테스트.

### F-03. 장판단 R1 prompt의 데이터 해석 가이드가 시장별로 분리되어 있지 않음

- 심각도: 비차단 개선 필요
- 대상: 장판단
- 위치: `minority_report/analysts.py:1686`
- 현재 동작: R1 prompt에 코스피, USD/KRW, VKOSPI, 외국인/기관 등 KR 중심 해석 가이드가 공통으로 포함된다.
- 왜 문제인지: US judgment에서는 일부 항목이 노이즈이며, 이미 큰 prompt에 불필요한 토큰과 해석 부담을 추가한다.
- 운영 영향: 판단 품질을 직접 깨는 증거는 없지만, US R1 prompt 평균 input tokens가 6k 수준이고 prompt focus가 흐려진다.
- 권장 수정: `_market_interpretation_guide(market)`를 두고 KR/US 가이드를 분리한다. US는 S&P500/NASDAQ/VIX/DXY/10Y/HYG/sector ETF 중심으로 좁힌다.
- 필요한 테스트: KR R1에는 KR guide, US R1에는 US guide만 들어가는 prompt snapshot 테스트.

### F-04. `select_tickers` 입력이 반복적으로 과대해 비용/지연 리스크가 큼

- 심각도: 비차단 잔여 리스크
- 대상: selection prompt
- 위치: `minority_report/analysts.py:2123`
- 현재 동작: 후보 라인, runtime evidence pack, digest news, brain/correction, strategy history, active lessons, selection feedback, tuning feedback, contract blocks가 한 prompt에 결합된다.
- 왜 문제인지: 2026-06-05~06 raw-call 기준 KR/US 모두 select_tickers 평균 input tokens가 11k 이상이고, 일부 호출은 20~39초대 지연을 보였다.
- 운영 영향: Claude 비용/지연 증가, 장초/장중 재선정 타이밍 지연, 응답 길이/계약 위반 가능성 증가.
- 권장 수정: compact schema를 기본화하고, 후보 라인의 반복 필드를 코드화한다. evidence pack은 `BUY_READY` ceiling 후보와 top uncertainty 후보 중심으로 줄이고, digest news/brain/correction은 selection에 필요한 짧은 summary만 보낸다. 단 evidence gate와 broker truth gate는 완화하지 않는다.
- 필요한 테스트: raw-call prompt chars/tokens budget snapshot, evidence included/omitted metadata 보존 테스트, trade_ready ceiling 회귀 테스트.

### F-05. Output contract 위반 및 parse recovery 샘플이 남아 있음

- 심각도: 비차단 잔여 리스크
- 대상: selection / postmortem / preopen continuation
- 위치: `minority_report/raw_call_logger.py:117`, `tools/claude_io_quality_report.py`
- 현재 동작: US preopen continuation 2건 parse error, US selection 1건 `trade_ready_action_not_buy_or_probe`, KR/US postmortem non-strict JSON wrapper/fence가 감지됐다.
- 왜 문제인지: 대부분 fail-safe fallback으로 흡수되는 구조지만, output contract 위반은 재시도/파싱 비용과 판단 누락을 만든다.
- 운영 영향: selection의 경우 runtime normalize가 trade_ready를 축소해 보호하더라도, Claude 입력 품질 평가에서는 모델이 계약을 혼동하고 있다는 신호다.
- 권장 수정: strict JSON labels는 bounded retry를 유지하되, schema 위반 종류를 raw_call extra에 구조화한다. preopen continuation은 max_tokens와 JSON fence 금지 contract를 강화한다.
- 필요한 테스트: parse error fallback이 BUY/SELL 권한을 만들지 않는 테스트, compact schema action whitelist 테스트, postmortem fenced JSON repair 테스트.

### F-06. Hold advisor 입력 completeness와 결과 편향 가시성이 부족함

- 심각도: 비차단 개선 필요
- 대상: hold advisor / PathB 수익 경로 보호
- 위치: `minority_report/hold_advisor.py:393`, `minority_report/hold_advisor.py:1751`, `runtime/pathb_runtime.py:10285`
- 현재 동작: triage payload와 decision log는 존재하지만, dashboard/report에서 stage별 `advisor_context_v2` completeness, target/stop 존재 여부, current price age, broker truth freshness, PathB close reason별 HOLD/SELL 분포가 한눈에 보이지 않는다.
- 왜 문제인지: PathB profit ladder, pre-close carry, target extension은 수익 핵심 경로라 hold advisor의 SELL 편향이 생기면 조기 청산으로 이어질 수 있다.
- 운영 영향: 개별 raw-call을 열면 재현 가능하지만, 운영자가 경향성으로 감시하기 어렵다.
- 권장 수정: hold advisor decision log에 `input_completeness`와 `pathb_revenue_path_context`를 추가하고, dashboard에 stage/reason/action 분포를 표시한다. 코드 변경 시 PathB cooldown guard와 hard-risk override는 건드리지 않는다.
- 필요한 테스트: PathB position에 reference target/stop이 decision log로 이어지는 테스트, AUTO_SELL_REVIEW HOLD cooldown 회귀, pre-close/profit-ladder stage별 summary 테스트.

### F-07. Claude I/O 품질 도구의 mojibake 탐지가 false positive 가능성이 있음

- 심각도: 범위 밖 후속 개선
- 대상: QA 도구
- 위치: `tools/claude_io_quality_report.py`
- 현재 동작: KR analyst R1 prompt에서 `prompt_mojibake_hangul_compat_jamo`가 9건 감지됐다.
- 왜 문제인지: 샘플 확인상 실제 깨진 문장보다는 U+318D(아래아) 같은 정상 한국어 문자도 감지되는 것으로 보인다.
- 운영 영향: 실제 encoding 문제와 정상 한국어 특수문자를 구분하지 못하면 운영자가 잘못된 리스크를 추적할 수 있다.
- 권장 수정: 탐지 결과에 matched char/code point/sample을 넣고, U+318D(아래아)는 별도 `korean_middle_dot` warning 또는 allowlist로 분리한다.
- 필요한 테스트: 정상 U+318D 포함 prompt는 mojibake로 분류하지 않고, 실제 replacement character/C1 control은 계속 잡는 테스트.

## 4. 개선 방안 우선순위

### P0 / 설계 확정 후 수정

1. `state/brain.json` 자동 정책 메모리 갱신 차단
   - `update_beliefs()`, `save_debate_result()`, `update_hold_advisor_performance()`의 prompt-visible 갱신을 approval queue 또는 lesson candidate 후보로 전환한다.
   - 이미 저장된 runtime 통계가 필요하면 별도 non-prompt metrics store로 분리한다.

2. Active lesson prompt scope 분리
   - lesson candidate에 `target_prompt_scope` 또는 `allowed_prompt_scopes`를 추가한다.
   - selection-only lesson은 analyst R1/R2에서 제외한다.

### P1 / 다음 코드 패치 후보

1. Market-specific R1 guide 분리
   - KR/US guide를 분리해 불필요한 토큰과 해석 노이즈를 줄인다.

2. Selection prompt token budget 축소
   - candidate line compact화, evidence pack row cap 조정, digest/brain/correction summary 축소를 적용한다.
   - evidence gate, action ceiling, broker truth, PathA/B routing은 유지한다.

3. Strict JSON output contract 보강
   - parser fallback은 fail-safe로 유지하고, schema 위반 종류를 raw_call extra와 report에 구조화한다.

### P2 / 운영 가시성 보강

1. Hold advisor input completeness dashboard
   - stage, source route, PathB revenue path, target/stop 존재 여부, current price source/age, broker truth freshness를 요약한다.

2. Hold advisor stage별 성과 분석
   - `AUTO_SELL_REVIEW`, `PRE_CLOSE_CARRY`, `TP_REVIEW`, `SOFT_EXIT`별 HOLD/SELL 결과와 이후 PnL을 분리한다.

3. Claude I/O quality report false positive 개선
   - matched character와 code point를 저장해 encoding 이슈와 정상 특수문자를 분리한다.

## 5. 처리 결과 분류

### 반영 완료

- `docs/claude_input_quality_audit_request.md` 기준으로 실제 코드 흐름을 추적했다.
- KR/US 최근 raw-call 품질 리포트를 생성했다.
- 본 감사 보고서에 데이터 흐름, 품질 평가, 발견 사항, 개선 방안, 필요한 테스트를 정리했다.

### 운영 리스크 있음 / 설계 확정 후 수정

- `state/brain.json` 직접 정책 메모리 갱신 경로는 운영 계약과 충돌할 수 있다. 읽기 경로 guard가 있어 즉각 오염으로 보기는 어렵지만, dirty 상태와 승인되지 않은 prompt-visible memory 노출 가능성은 설계 확정 후 정리해야 한다.
- selection-only active lesson이 market judgment에도 주입되는 구조는 장판단/selection 책임 분리를 약화시킨다.

### 비차단 잔여 리스크

- `select_tickers` prompt가 큰 편이며 장초/장중 재선정 지연과 비용 증가를 유발할 수 있다.
- strict JSON 계약 위반 샘플이 일부 남아 있다.
- hold advisor 입력 completeness와 stage별 경향성은 raw log로 재현 가능하지만 dashboard 수준의 요약 가시성이 부족하다.

### 범위 밖 후속 개선

- 위 개선안의 실제 코드 반영과 회귀 테스트 작성
- `tools/claude_io_quality_report.py` mojibake 탐지 false positive 개선
- 이미 dirty 상태인 `state/brain.json`의 운영자 승인/정리 여부 판단

## 6. 검증

실행한 검증:

- `python tools/claude_io_quality_report.py --market US --start 2026-06-05 --end 2026-06-06 --out-dir docs/reports/claude_input_quality_audit_evidence_20260607/US`
- `python tools/claude_io_quality_report.py --market KR --start 2026-06-05 --end 2026-06-06 --out-dir docs/reports/claude_input_quality_audit_evidence_20260607/KR`
- 관련 코드 정적 추적: `trading_bot.py`, `minority_report/analysts.py`, `minority_report/active_lessons.py`, `minority_report/hold_advisor.py`, `minority_report/postmortem.py`, `claude_memory/brain.py`, `dashboard/dashboard_server.py`

미실행 검증:

- pytest는 실행하지 않았다. 이번 작업은 코드 변경 없이 감사 보고서 생성 범위다.
- live/paper preflight는 실행하지 않았다. 주문/리스크/config 경로를 변경하지 않았기 때문이다.
- dashboard 화면 확인은 하지 않았다. API/코드 경로만 정적 확인했다.
