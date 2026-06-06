# Claude 입력 품질 개선 개발 요구서

- 작성일: 2026-06-07
- 기준 감사 보고서: `docs/reports/claude_input_quality_audit_20260607.md`
- 목적: 장판단, lesson candidate, hold advisor에 들어가는 Claude 입력을 코드레벨로 개선한다.
- 기본 적용 방침: 구현은 live/enforce 적용을 전제로 설계한다. 단, prompt token 경량화처럼 모델 출력 품질 변화가 큰 항목은 shadow metric 또는 bounded rollout 조건을 별도로 둔다.

## 공통 개발 계약

### 직접 건드리지 않을 보호 영역

아래 보호 영역은 해당 DEV 항목의 직접 원인이 아니면 수정하지 않는다.

- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard
- PathB broker-truth entry fail-closed
- PathB sizing reason split
- zero-holding stale reconcile
- KIS order normalization
- Path A / Path B routing contract
- `_sync_runtime_with_broker()` broker truth 우선순위
- `state/brain.json` 직접 수정 또는 자동 정책 메모리 승격
- `.env*`, `config/v2_start_config.json` 운영 파라미터

보호 영역을 피할 수 없이 수정해야 하면 `AGENTS.md`의 `MD 위반 사항` 보고 형식을 따른다.

### 공통 수용 기준

- Claude는 주문 수량/금액을 최종 결정하지 않는다.
- hard stop, broker truth, quarantine, PathB live gate는 완화하지 않는다.
- selection 품질 개선과 execution/risk 정책 변경을 한 패치에 섞지 않는다.
- 새 prompt-visible 데이터는 producer, 저장소, consumer, raw_call extra, dashboard/report 가시성까지 추적 가능해야 한다.
- 누락값, 기본값, stale 데이터, 빈 리스트 케이스를 테스트한다.

### 권장 구현 순서

1. `DEV-01` active lesson prompt scope 분리
2. `DEV-03` 장판단 R1 market-specific guide 분리
3. `DEV-02` `brain.json` 정책 메모리 직접 갱신 차단/격리
4. `DEV-05` strict JSON/schema 위반 로깅 강화
5. `DEV-06` hold advisor input completeness 및 PathB 수익 경로 가시성
6. `DEV-07` Claude I/O mojibake false positive 개선
7. `DEV-04` selection prompt token budget 축소

`DEV-04`는 입력 축소가 모델 판단 품질에 직접 영향을 줄 수 있으므로, scope 분리와 schema 위반 가시성을 먼저 갖춘 뒤 진행한다.
`DEV-02`는 brain/postmortem/approval queue가 함께 걸리는 다중 파일 변경이므로, 단일 파일 중심의 `DEV-03` 이후에 진행한다.

## DEV-01. Active Lesson Prompt Scope 분리

### 연결 발견 사항

- F-02: selection lesson이 market judgment prompt에 주입됨

### 목적

`watch_only_missed_runup_ratio`, `trade_ready_signal_conversion` 같은 selection 최적화 lesson이 analyst R1/R2 market judgment에 들어가지 않게 한다. selection lesson은 `select_tickers`에만 주입하고, market judgment에는 market/breadth 판단용 lesson만 허용한다.

### 수정 예정 파일

- `minority_report/active_lessons.py`
- `minority_report/lesson_quality.py`
- `minority_report/analysts.py`
- `trading_bot.py`
- `dashboard/dashboard_server.py`
- `tests/test_active_lessons.py`
- `tests/test_dashboard_active_lessons.py`
- 필요 시 `tools/backfill_lesson_candidate_quality.py`

### 수정 함수 / 지점

- `minority_report.active_lessons.build_active_lesson_context()`
- `minority_report.active_lessons._collect_lesson_candidate_items()`
- `minority_report.lesson_quality.lesson_quality_fields()`
- `minority_report.lesson_quality.apply_lesson_conflict_guards()`
- `minority_report.analysts._lesson_context_for_prompt()`
- `minority_report.analysts.get_three_judgments()`
- `minority_report.analysts.select_tickers()`
- `trading_bot.TradingBot._load_lesson_candidate_summary()`
- `dashboard.dashboard_server._dashboard_active_lessons_payload()`

### 구현 요구사항

1. lesson candidate schema에 prompt scope 필드를 추가한다.
   - 권장 필드: `allowed_prompt_scopes: ["selection"]`
   - 호환 필드: `target_prompt_scope: "selection"`도 읽되 저장은 `allowed_prompt_scopes`로 정규화한다.

2. scope 상수 또는 Literal을 둔다.
   - `selection`
   - `market_judgment`
   - `hold_advisor`
   - `all`은 기본 사용 금지. 수동 승인 항목에만 허용 가능.

3. `build_active_lesson_context()` signature를 확장한다.

```python
def build_active_lesson_context(
    market: str,
    *,
    prompt_scope: str = "selection",
    retry: bool = False,
    max_items: int | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    ...
```

4. 기존 lesson candidate에 scope 필드가 없을 때의 호환 규칙을 둔다.
   - `scope == "selection"` 또는 metric key가 `watch_only_missed_runup_ratio`, `trade_ready_signal_conversion`이면 `selection`
   - `scope in {"execution", "consensus", "strategy"}`이면 prompt injection 금지
   - 수동 source(`manual`, `data_analysis`, `postmortem`)는 `truth_status=fresh`라도 scope 없으면 기본 `selection`으로만 제한

5. market judgment 경로에서 selection lesson을 제외한다.
   - `get_three_judgments()`는 `build_active_lesson_context(market, prompt_scope="market_judgment")`를 호출한다.
   - `call_analyst()`와 `call_analyst_debate()`는 이미 받은 lesson context만 사용하게 유지한다.

6. selection 경로는 기존 lesson injection을 유지한다.
   - `select_tickers()`는 `build_active_lesson_context(market, prompt_scope="selection")`를 호출한다.
   - retry prompt도 `prompt_scope="selection"`을 사용한다.

7. dashboard/API payload에 prompt scope를 노출한다.
   - `/api/active-lessons` payload item에 `allowed_prompt_scopes` 추가
   - metadata에 `prompt_scope`, `scope_filtered_count`, `ignored_reasons.scope_mismatch` 추가

### 금지 변경

- lesson action hint 내용 자체를 공격적으로 바꾸지 않는다.
- `trade_ready`/`watch_only` slot 정책을 같이 바꾸지 않는다.
- broker truth, order routing, PathB registration 로직을 건드리지 않는다.

### 테스트 요구사항

수정/추가 대상:

- `tests/test_active_lessons.py`
  - 기존 `test_get_three_judgments_uses_active_lesson_context_for_r1_and_r2`는 기대값을 바꾼다. selection-only lesson은 R1/R2에 들어가면 안 된다.
  - market_judgment scope lesson이 있을 때만 R1/R2 prompt에 들어가는 테스트 추가
  - selection scope lesson은 `select_tickers` prompt에 계속 들어가는 테스트 유지/추가
  - scope 없는 legacy selection lesson은 selection prompt에만 들어가는 테스트 추가
- `tests/test_dashboard_active_lessons.py`
  - API payload가 `allowed_prompt_scopes`, `prompt_scope`, `scope_mismatch`를 노출하는지 확인

검증 명령:

```powershell
python -m pytest tests/test_active_lessons.py tests/test_dashboard_active_lessons.py -q
python -m pytest tests/test_trading_decision_contract_improvements.py::SelectionPromptContractTests -q
python -m py_compile minority_report/active_lessons.py minority_report/analysts.py minority_report/lesson_quality.py dashboard/dashboard_server.py
```

### 수용 기준

- R1/R2 raw prompt에 `watch_only 종목 중 ... runup` selection lesson이 들어가지 않는다.
- `select_tickers` raw prompt에는 selection lesson이 기존처럼 들어간다.
- active lesson metadata에 scope 필터 결과가 남는다.

## DEV-02. `brain.json` 정책 메모리 직접 갱신 차단 / 격리

### 연결 발견 사항

- F-01: `state/brain.json` 직접 갱신 경로가 운영 계약과 충돌할 수 있음

### 목적

`state/brain.json`이 자동으로 dirty해지고 prompt-visible 정책 메모리가 승인 없이 누적되는 경로를 차단한다. 읽기 경로 guard가 있어 즉각 오염으로 단정하지는 않지만, 승인형 워크플로우 전까지 정책 메모리 승격은 queue/candidate 기반으로 격리한다.

### 수정 예정 파일

- `minority_report/postmortem.py`
- `minority_report/analysts.py`
- `claude_memory/brain.py`
- `learning/approval_queue.py`
- `learning/candidate_builder.py` 또는 새 helper
- `tools/live_preflight.py`
- `tests/test_trading_decision_contract_improvements.py`
- `tests/test_v2_phase4.py`
- `tests/test_live_preflight_ml_and_brain.py`
- `tests/test_brain_execution_integrity.py`

### 수정 함수 / 지점

- `minority_report.postmortem.run()`
- `minority_report.postmortem._append_lesson_candidate()`
- `minority_report.analysts.get_three_judgments()`
- `claude_memory.brain.update_beliefs()`
- `claude_memory.brain.save_debate_result()`
- `claude_memory.brain.update_hold_advisor_performance()`
- `learning.approval_queue.BrainApprovalQueue.submit()`
- `tools.live_preflight` brain dirty guard

### 구현 요구사항

1. prompt-visible policy update를 queue로 전환한다.
   - `brain_updates.new_lesson`
   - `brain_updates.market_regime`
   - correction guide update
   - issue pattern update
   - debate history update
   - hold advisor performance 중 prompt에 들어갈 수 있는 요약

2. 기존 직접 저장 함수는 삭제하지 말고 안전 래퍼로 제한한다.
   - `BrainDB.update_beliefs()`는 수동 승인 적용 경로에서만 호출되도록 call site를 줄인다.
   - 함수 자체는 호환성 때문에 남길 수 있으나 새 runtime 자동 호출을 금지한다.

3. postmortem의 daily record는 두 종류로 분리한다.
   - 운영 통계/감사용 record: 기존 `add_daily_record()` 가능 여부를 검토하되 prompt-visible 여부를 명확히 표시
   - 정책 후보: `BrainApprovalQueue` 또는 `state/lesson_candidates.json`에 `truth_status="manual_review_required"`로 적재

4. debate history는 즉시 R2 prompt에 넣지 않는다.
   - 자동 debate result는 `state/brain_approval_queue.jsonl` 또는 별도 candidate file에 `PENDING_APPROVAL`로 저장
   - `BrainDB.get_debate_summary()`는 approved source만 읽도록 한다.

5. approval candidate schema를 명확히 한다.

```json
{
  "candidate_type": "market_lesson|debate_summary|correction_guide|hold_advisor_performance",
  "market": "KR|US",
  "source": "postmortem|debate|hold_advisor",
  "summary": "...",
  "evidence": {...},
  "prompt_visible": true,
  "requires_operator_approval": true
}
```

6. 이미 dirty인 `state/brain.json`은 자동 정리하지 않는다.
   - 별도 remediation 요구서 또는 운영자 승인 후 처리한다.

### 금지 변경

- `state/brain.json`을 자동 revert, reset, normalize하지 않는다.
- broker truth, execution event store, lifecycle truth를 brain으로 대체하지 않는다.
- live preflight dirty warning을 완화하지 않는다.

### 테스트 요구사항

수정/추가 대상:

- `tests/test_trading_decision_contract_improvements.py`
  - `test_normal_postmortem_requires_approval_before_policy_updates` 유지/강화
  - postmortem 정상 응답이 `update_beliefs`, `update_issue_pattern`, `update_correction_guide`를 직접 호출하지 않는지 확인
  - approval queue candidate가 생성되는지 확인
- `tests/test_v2_phase4.py`
  - `BrainApprovalQueue` candidate type/schema 검증 추가
- `tests/test_live_preflight_ml_and_brain.py`
  - brain dirty guard가 계속 WARN을 내는지 확인
- `tests/test_brain_execution_integrity.py`
  - prompt_policy_excluded record가 prompt-visible 후보로 승격되지 않는지 확인

검증 명령:

```powershell
python -m pytest tests/test_trading_decision_contract_improvements.py::PostmortemContractTests tests/test_v2_phase4.py tests/test_live_preflight_ml_and_brain.py tests/test_brain_execution_integrity.py -q
python -m py_compile minority_report/postmortem.py minority_report/analysts.py claude_memory/brain.py learning/approval_queue.py tools/live_preflight.py
```

### 수용 기준

- 일반 postmortem 실행이 `state/brain.json` prompt-visible 정책 필드를 직접 갱신하지 않는다.
- 승인 후보는 queue/candidate store에 남고, operator approval 전 prompt에는 들어가지 않는다.
- preflight brain dirty guard는 유지된다.

## DEV-03. 장판단 R1 Market-Specific Guide 분리

### 연결 발견 사항

- F-03: 장판단 R1 prompt의 데이터 해석 가이드가 시장별로 분리되어 있지 않음

### 목적

KR 중심 해석 가이드가 US R1 prompt에 들어가는 노이즈를 제거하고, 시장별 지표 해석을 명확히 한다.

### 수정 예정 파일

- `minority_report/analysts.py`
- `tests/test_active_lessons.py`
- `tests/test_trading_decision_contract_improvements.py`

### 수정 함수 / 지점

- `minority_report.analysts.call_analyst()`
- 새 helper: `_market_interpretation_guide(market: str) -> str`

### 구현 요구사항

1. 공통 가이드와 시장별 가이드를 분리한다.
   - 공통: missing/N/A 처리, 이벤트 리스크, 1d vs 5d 과신 금지
   - KR: KOSPI/KOSDAQ, USD/KRW, VKOSPI, 외국인/기관, corp news coverage
   - US: S&P500/NASDAQ, VIX, DXY, 10Y, HYG, sector ETF, megacap concentration

2. R1 prompt는 `market`에 맞는 guide만 포함한다.
   - `market="KR"`: KR guide 포함, US guide 제외
   - `market="US"`: US guide 포함, KR guide 제외

3. R2는 digest 800자 요약 중심이므로 guide 확장은 하지 않는다.

### 금지 변경

- persona별 stance 기준 자체를 바꾸지 않는다.
- consensus guard나 size policy를 같이 바꾸지 않는다.

### 테스트 요구사항

- KR R1 prompt에 `코스피`, `USD/KRW`, `VKOSPI` 가이드가 들어간다.
- US R1 prompt에 `S&P500`, `NASDAQ`, `VIX`, `10Y`, `HYG` 가이드가 들어간다.
- US R1 prompt에는 KR-only guide 문구가 들어가지 않는다.

검증 명령:

```powershell
python -m pytest tests/test_active_lessons.py::ActiveLessonPromptTests tests/test_trading_decision_contract_improvements.py -k "postmortem_prompt_is_market_scoped or r1" -q
python -m py_compile minority_report/analysts.py
```

### 수용 기준

- 시장별 R1 prompt focus가 분리된다.
- raw_call extra의 prompt_version은 변경하거나, 변경 시 `market_judgment_v4_market_scoped`처럼 명시한다.

## DEV-04. Selection Prompt Token Budget 축소

### 연결 발견 사항

- F-04: `select_tickers` 입력이 반복적으로 과대해 비용/지연 리스크가 큼

### 목적

`select_tickers` prompt의 후보/증거/학습/계약 블록 중복을 줄여 지연과 비용을 낮춘다. 단 evidence gate, action ceiling, broker truth, PathA/B routing은 절대 완화하지 않는다.

### 수정 예정 파일

- `minority_report/analysts.py`
- `runtime/selection_compact_schema.py`
- `runtime/live_evidence_pack.py`
- `tools/claude_io_quality_report.py`
- `tests/test_trading_decision_contract_improvements.py`
- `tests/test_active_lessons.py`
- `tests/test_candidate_action_live_mapping.py`

### 수정 함수 / 지점

- `minority_report.analysts.select_tickers()`
- `minority_report.analysts._compact_selection_evidence_item()`
- `minority_report.analysts._json_array_object_cap()`
- `minority_report.analysts._build_tuning_feedback_contract()`
- `runtime.selection_compact_schema.normalize_selection_result()`
- `tools.claude_io_quality_report.build_quality_report()`

### 구현 요구사항

1. 후보 라인 compact v2를 추가한다.
   - 반복 prefix를 줄이고 machine code 중심으로 표현
   - 필수 보존: ticker, change, relative strength, price, liquidity, evidence class, ceiling, post_open state, execution fit, OR/VWAP/ATR 핵심

2. evidence pack 우선순위를 명시한다.
   - 포함 우선순위:
     1. `BUY_READY`/`PROBE_READY` ceiling 후보
     2. Claude가 trade_ready로 오판하기 쉬운 `WATCH` ceiling 후보
     3. post_open state가 `sustained`, `early_strength`, `fade`인 대표 후보
   - metadata 보존:
     - `evidence_requested_count`
     - `evidence_pack_count`
     - `evidence_omitted_count`
     - omitted reason sample

3. digest/brain/correction block을 selection용 summary로 줄인다.
   - full market judgment memory를 그대로 넣지 않는다.
   - active lesson과 tuning feedback은 최대 3개/짧은 JSON으로 제한한다.

4. token/char budget metadata를 raw_call extra에 남긴다.
   - `prompt_chars`
   - `candidate_section_chars`
   - `evidence_section_chars`
   - `lesson_section_chars`
   - `contract_section_chars`
   - `prompt_budget_version`

5. rollout 조건을 둔다.
   - 기존 compact schema가 켜져 있으면 compact v2를 적용
   - schema 위반률이 증가하면 fallback 가능해야 한다.

### 금지 변경

- `ceil=WATCH` 후보를 Claude가 `trade_ready/tr`로 올릴 수 있게 완화하지 않는다.
- `ALLOW_LEGACY_SELECTION_AUTO_READY` 기본값을 바꾸지 않는다.
- PathB `PULLBACK_WAIT` 등록 정책을 같이 바꾸지 않는다.

### 테스트 요구사항

- compact prompt가 필수 evidence/action ceiling 정보를 유지한다.
- prompt char budget이 지정 기준 이하인지 snapshot 테스트로 확인한다.
- evidence omitted metadata가 raw_call extra와 normalized meta에 남는다.
- `ceil=WATCH` 후보가 `tr`에 들어오면 normalize 단계에서 제거된다.

검증 명령:

```powershell
python -m pytest tests/test_trading_decision_contract_improvements.py::SelectionPromptContractTests tests/test_candidate_action_live_mapping.py tests/test_active_lessons.py -q
python -m py_compile minority_report/analysts.py runtime/selection_compact_schema.py tools/claude_io_quality_report.py
python tools/claude_io_quality_report.py --market US --start 2026-06-05 --end 2026-06-06 --out-dir docs/reports/claude_io_quality_us_after_selection_prompt_change
```

### 수용 기준

- 테스트 prompt sample 기준 prompt chars가 기존 대비 의미 있게 감소한다.
- raw-call 품질 리포트가 prompt section별 chars를 보여준다.
- selection safety 회귀 테스트가 모두 통과한다.

## DEV-05. Strict JSON / Schema 위반 로깅 강화

### 연결 발견 사항

- F-05: Output contract 위반 및 parse recovery 샘플이 남아 있음

### 목적

Claude 응답이 strict JSON 또는 compact schema 계약을 어겼을 때, fallback이 안전하게 작동했는지만이 아니라 어떤 계약을 어겼는지 운영자가 볼 수 있게 한다.

### 수정 예정 파일

- `minority_report/raw_call_logger.py`
- `minority_report/analysts.py`
- `minority_report/postmortem.py`
- `preopen/continuation_shadow.py`
- `tools/claude_io_quality_report.py`
- `tests/test_trading_decision_contract_improvements.py`
- `tests/test_us_claude_morning_report.py`

### 수정 함수 / 지점

- `minority_report.raw_call_logger.save()`
- `minority_report.analysts.select_tickers()`
- `minority_report.analysts._extract_json_strict()`
- `minority_report.analysts._recover_compact_watch_selection()`
- `minority_report.postmortem._extract_json()`
- `tools.claude_io_quality_report.build_quality_report()`

### 구현 요구사항

1. raw_call record에 schema violation 필드를 추가한다.

```json
{
  "schema_violations": [
    {"code": "response_fenced_json", "severity": "warn"},
    {"code": "trade_ready_action_not_buy_or_probe", "severity": "warn"}
  ],
  "fallback_policy": "watch_only|hold|skip|retry",
  "fallback_created_execution_authority": false
}
```

2. selection normalize 단계에서 compact schema 위반을 구조화한다.
   - action whitelist 위반
   - `tr`와 `ca` action 불일치
   - `ceil=WATCH`인데 `tr` 포함
   - malformed `pt`

3. parser fallback이 BUY/SELL 권한을 만들지 않았음을 명시한다.
   - selection parse fail: `trade_ready=[]`
   - hold advisor parse fail: `HOLD` fallback, 단 hard risk override는 runtime 소유
   - preopen continuation parse fail: no promotion

4. `tools/claude_io_quality_report.py`는 issue counts와 samples에 schema violation code를 보여준다.

### 금지 변경

- parser recovery를 공격적으로 만들어 BUY/SELL을 복원하지 않는다.
- fallback이 runtime hard gate를 우회하지 않는다.

### 테스트 요구사항

- malformed selection output은 `trade_ready=[]` 또는 safe normalized 결과가 된다.
- raw_call extra에 schema violation이 기록된다.
- quality report가 schema violation count와 sample path를 노출한다.

검증 명령:

```powershell
python -m pytest tests/test_trading_decision_contract_improvements.py::SelectionPromptContractTests tests/test_us_claude_morning_report.py -q
python -m py_compile minority_report/raw_call_logger.py minority_report/analysts.py tools/claude_io_quality_report.py
```

### 수용 기준

- parse/schema 위반 샘플을 raw_call JSON만 보고 원인 분류할 수 있다.
- fallback이 execution authority를 만들지 않는다는 metadata가 남는다.

## DEV-06. Hold Advisor Input Completeness 및 PathB 수익 경로 가시성

### 연결 발견 사항

- F-06: hold advisor 입력 completeness와 결과 편향 가시성이 부족함

### 목적

hold advisor가 어떤 입력 completeness 상태에서 HOLD/SELL을 냈는지, PathB profit ladder / pre-close / target extension 수익 경로에서 action 분포가 어떤지 운영자가 raw log와 dashboard/report로 볼 수 있게 한다.

현재 감사 결론은 "이미 SELL 편향이 있다"가 아니라 "가시성이 부족해서 모른다"이다.

### 수정 예정 파일

- `minority_report/hold_advisor.py`
- `trading_bot.py`
- `runtime/pathb_runtime.py`는 `_pathb_exit_meta()` / `_attach_pathb_position_metadata()` revenue path context 필드 전달만 허용
- `tools/analyze_hold_advisor_latency.py`
- `dashboard/dashboard_server.py`
- `tests/test_trading_decision_contract_improvements.py`
- `tests/test_auto_sell_claude_gate.py`
- `tests/test_plan_a_hold_policy.py`
- `tests/test_price_unit_normalization.py`
- `tests/test_analyze_hold_advisor_latency.py`

### 수정 함수 / 지점

- `minority_report.hold_advisor._triage_case_payload()`
- `minority_report.hold_advisor._ask_triage()`
- `minority_report.hold_advisor._ask_challenge()`
- `minority_report.hold_advisor._ask_one()`
- `minority_report.hold_advisor._log_decision()`
- `trading_bot.TradingBot._run_auto_sell_review_gate()`
- `trading_bot.TradingBot._update_hold_advisor_jsonl_outcome()`
- `runtime.pathb_runtime._run_pathb_sell_review_gate()` metadata pass-through/log extra만 허용
- `runtime.pathb_runtime._pathb_exit_meta()`
- `runtime.pathb_runtime._attach_pathb_position_metadata()`
- `tools.analyze_hold_advisor_latency.analyze_hold_advisor_latency()`
- `tools.analyze_hold_advisor_latency.to_markdown()`

### 구현 요구사항

1. input completeness helper를 추가한다.

```python
def _input_completeness(pos: dict, market: str, decision_stage: str, rt_context: str) -> dict:
    return {
        "entry_ok": bool,
        "current_ok": bool,
        "pnl_ok": bool,
        "target_ok": bool,
        "stop_ok": bool,
        "advisor_context_v2_ok": bool,
        "pathb_reference_ok": bool,
        "market_context_ok": bool,
        "minutes_to_close_ok": bool,
        "missing": [...],
        "score": 0.0,
    }
```

2. PathB revenue path context를 decision log에 추가한다.

```json
{
  "pathb_revenue_path_context": {
    "is_pathb": true,
    "path_run_id": "...",
    "origin_action": "PULLBACK_WAIT",
    "exit_reason": "profit_ladder|pre_close|target|loss_cap|hard_stop|other",
    "reference_target": 0.0,
    "reference_stop": 0.0,
    "profit_ladder_tier": "",
    "minutes_to_close": 0.0
  }
}
```

3. `_log_decision()`이 다음 필드를 JSONL에 남긴다.
   - `input_completeness`
   - `pathb_revenue_path_context`
   - `decision_stage`
   - `decision_source`
   - `fallback`
   - `hold_boundary_invalid`

4. raw_call extra에도 핵심 completeness를 넣는다.
   - triage/challenge/legacy 모두 stage와 completeness score를 남긴다.

5. 분석 도구를 확장한다.
   - `tools/analyze_hold_advisor_latency.py`에 stage별 action count, PathB revenue path별 HOLD/SELL count, fallback count, completeness low count를 추가한다.

6. dashboard는 처음에는 API/JSON 중심으로 추가한다.
   - 권장 endpoint: `/api/hold-advisor/summary?market=US&days=5`
   - UI 패널은 후속으로 분리 가능하지만 API는 먼저 둔다.

### 금지 변경

- PathB profit ladder floor/giveback 계산을 바꾸지 않는다.
- pre-close 청산 조건을 바꾸지 않는다.
- PathB runtime 변경은 revenue path metadata 전달에 한정하며 entry scan, exit decision branch/order, sizing, profit ladder signal은 바꾸지 않는다.
- `AUTO_SELL_REVIEW_HOLD_COOLDOWN_MINUTES`, `PATHB_AUTO_SELL_REVIEW_HOLD_REASK_DROP_PCT`, `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS` 기본 동작을 바꾸지 않는다.
- hard risk override보다 Claude HOLD를 우선하지 않는다.

### 테스트 요구사항

- `tests/test_trading_decision_contract_improvements.py`
  - decision log가 `input_completeness`와 `pathb_revenue_path_context`를 보존
  - triage prompt는 prior raw response를 포함하지 않는 기존 테스트 유지
- `tests/test_auto_sell_claude_gate.py`
  - `test_pathb_loss_cap_hold_respects_reask_cooldown` 반드시 유지
  - PathB profit ladder/target hold 정책 테스트 유지
- `tests/test_price_unit_normalization.py`
  - US native price display와 fallback labels 보존
- `tests/test_analyze_hold_advisor_latency.py`
  - stage/action/pathb revenue path aggregation 테스트 추가

검증 명령:

```powershell
python -m pytest tests/test_trading_decision_contract_improvements.py tests/test_auto_sell_claude_gate.py::AutoSellClaudeGateTests::test_pathb_loss_cap_hold_respects_reask_cooldown tests/test_plan_a_hold_policy.py tests/test_price_unit_normalization.py tests/test_analyze_hold_advisor_latency.py -q
python -m py_compile minority_report/hold_advisor.py trading_bot.py runtime/pathb_runtime.py tools/analyze_hold_advisor_latency.py dashboard/dashboard_server.py
```

### 수용 기준

- 특정 hold advisor decision row만 보고 입력 누락 여부와 PathB 수익 경로를 알 수 있다.
- PathB `AUTO_SELL_REVIEW` HOLD cooldown 보호 테스트가 계속 통과한다.
- 분석 도구에서 PRE_CLOSE_CARRY/profit_ladder stage별 HOLD/SELL 분포가 나온다.

## DEV-07. Claude I/O Quality Report Mojibake False Positive 개선

### 연결 발견 사항

- F-07: Claude I/O 품질 도구의 mojibake 탐지가 false positive 가능성이 있음

### 목적

정상 한국어 문자 U+318D(아래아) 같은 문자와 실제 encoding 깨짐을 분리해 QA 리포트 신뢰도를 높인다.

### 수정 예정 파일

- `tools/claude_io_quality_report.py`
- `tests/test_us_claude_morning_report.py` 또는 새 `tests/test_claude_io_quality_report.py`

### 수정 함수 / 지점

- `tools.claude_io_quality_report.MOJIBAKE_PATTERNS`
- `tools.claude_io_quality_report.build_quality_report()`
- prompt issue sample 생성부

### 구현 요구사항

1. `hangul_compat_jamo` 탐지를 세분화한다.
   - U+318D(아래아)는 정상 한국어 특수문자로 allowlist 또는 별도 low severity warning 처리
   - 실제 replacement char, C1 control, escaped mojibake byte는 계속 issue로 유지

2. issue sample에 matched text를 제한 길이로 포함한다.

```json
{
  "issue": "prompt_mojibake_c1_control",
  "matched_sample": "<escaped_byte_sample>",
  "codepoints": ["U+009F"]
}
```

3. 리포트 recommendations는 false positive와 hard encoding issue를 구분한다.

### 금지 변경

- 실제 mojibake 탐지를 끄지 않는다.
- Korean prompt 문구를 영문화해 문제를 숨기지 않는다.

### 테스트 요구사항

- U+318D(아래아)가 포함된 정상 prompt는 `prompt_mojibake_hangul_compat_jamo` hard issue로 잡히지 않는다.
- replacement char(U+FFFD), C1 control, escaped byte는 계속 issue로 잡힌다.
- markdown report에 matched sample이 과도하게 길게 들어가지 않는다.

검증 명령:

```powershell
python -m pytest tests/test_us_claude_morning_report.py -q
python -m py_compile tools/claude_io_quality_report.py
```

### 수용 기준

- KR raw-call 품질 리포트에서 정상 U+318D(아래아) 때문에 P1 input_quality가 발생하지 않는다.
- 실제 encoding 깨짐은 기존보다 더 구체적인 sample과 code point로 보고된다.

## 전체 QA 명령

개별 DEV 항목 구현 후 관련 테스트를 먼저 돌리고, 여러 항목을 한 번에 반영한 경우 아래 순서로 확인한다.

```powershell
python -m pytest tests/test_active_lessons.py tests/test_dashboard_active_lessons.py -q
python -m pytest tests/test_trading_decision_contract_improvements.py -q
python -m pytest tests/test_auto_sell_claude_gate.py tests/test_plan_a_hold_policy.py tests/test_price_unit_normalization.py -q
python -m pytest tests/test_us_claude_morning_report.py tests/test_analyze_hold_advisor_latency.py tests/test_live_preflight_ml_and_brain.py tests/test_brain_execution_integrity.py tests/test_v2_phase4.py -q
python -m py_compile trading_bot.py minority_report/analysts.py minority_report/active_lessons.py minority_report/lesson_quality.py minority_report/hold_advisor.py minority_report/postmortem.py claude_memory/brain.py dashboard/dashboard_server.py tools/claude_io_quality_report.py tools/analyze_hold_advisor_latency.py
```

주문/리스크/브로커 truth/config/dashboard/log가 연결되는 코드 변경이 포함되면 추가로 아래를 실행한다.

```powershell
python tools/live_preflight.py --mode live --skip-dashboard --json
```

## 개발 완료 보고 형식

각 DEV 항목 구현 완료 시 최종 보고는 아래 형식으로 한다.

- 반영 완료:
  - 변경 파일/함수
  - 변경 동작
  - raw_call/log/dashboard 가시성
- 비차단 잔여 리스크:
  - 미검증 경로
  - 운영자가 판단해야 할 항목
- 범위 밖 후속 개선:
  - 별도 승인/운영 데이터가 필요한 항목
- 검증:
  - 실행한 pytest/py_compile/preflight
  - 실패 또는 미실행 사유
- config/env 영향:
  - 변경 없음 또는 변경 항목 명시
- 보호 영역 영향:
  - 변경 없음 또는 `MD 위반 사항` 섹션 작성
