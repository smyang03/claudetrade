# Claude 판단 라벨링 코드레벨 개선 요구서

작성일: 2026-05-21 KST

## 0. 코드레벨 재검토 결론

이 요구서는 **그대로 전면 구현하지 않고 Phase 0 builder부터 작게 구현**한다.

최초 구현 범위:

```text
tools/build_claude_decision_facts.py
tests/test_claude_decision_facts.py
data/ml/claude_decision_facts.db
```

최초 생성 테이블:

```text
fact_selection
fact_forward_outcome
fact_execution
fact_build_runs
```

Phase 0에서 하지 않는 것:

```text
tools/label_claude_judgments.py
tools/report_claude_misjudgments.py
fact_market_judgment
decision_labels
lesson proposal
dashboard/runtime integration
state/brain.json 변경
state/lesson_candidates.json 변경
```

핵심 구현 원칙:

- `audit_candidate_rows` call-level row를 기본 source로 보존한다.
- `audit_candidate_latest_rows`는 report dedupe 또는 `--latest-only` 옵션에서만 사용한다.
- execution 연결은 `candidate_key`가 아니라 `execution_decision_id`, `v2_decision_id`, `path_run_id`를 우선한다.
- ambiguous session/ticker execution match는 연결하지 않고 `ambiguous_match`로 남긴다.
- source DB는 read-only로 열고 수정하지 않는다.

Phase 0이 실제 DB에서 안정적으로 재생성되는 것을 확인한 뒤에만 labeler, report, lesson proposal을 순차 구현한다.

## 1. 목적

이번 개선의 목적은 Claude 판단을 한 덩어리로 평가하지 않고, 실제 손익 훼손 원인을 `selection`, `execution`, `risk_policy`, `data_quality`, `market_mode`로 분리해 고정 fact로 남기는 것이다.

현재 DB에는 판단 재료가 이미 있다.

- Claude 선택: `data/ticker_selection_log.db`, `data/audit/candidate_audit.db`
- 실행/청산: `data/ml/decisions.db`, `data/v2_event_store.db`
- counterfactual: `data/audit/candidate_audit.db.candidate_counterfactual_paths`
- 시장 판단: `logs/daily_judgment/`, `state/brain.json`
- 교훈 후보: `state/lesson_candidates.json`

문제는 이 재료가 하나의 기준 테이블로 결합되어 있지 않다는 점이다. 그래서 같은 손실도 어떤 리포트에서는 Claude selection 오판으로 보이고, 다른 리포트에서는 execution 문제로 보일 수 있다. 이 개선은 원본 운영 DB를 바꾸지 않고 분석 전용 mart DB를 새로 만들어 같은 분모와 라벨 규칙으로 반복 평가할 수 있게 한다.

## 2. 비목표

- 주문 수량, 주문 금액, 손절, 익절, PathA/PathB 실행 정책을 바로 변경하지 않는다.
- `state/brain.json`을 자동 수정하지 않는다.
- 짧은 기간 성과만으로 US `trade_ready`를 차단하거나 KR selection 자체를 폐기하지 않는다.
- 원본 DB의 row를 삭제, 보정, backfill하지 않는다. source DB는 읽기 전용으로 사용한다.
- market mode hit와 종목 selection 성과를 하나의 승패로 섞지 않는다.

## 3. 왜 하는가

리뷰 기준 핵심 결론은 다음과 같다.

- US `trade_ready`는 평균과 양수율이 양호하므로 보존 가치가 크다.
- KR은 큰 상승 후보를 찾는 능력은 있지만 실패 꼬리와 변동성이 커서 진입 확인, sizing, 손절/익절 쪽 보수화가 필요하다.
- watch-only missed winner가 존재하지만, 이것을 모두 Claude 오판으로 보면 안 된다. 저유동성, blackout, same-day reentry, broker untrusted 같은 사유는 `risk_justified_miss`로 분리해야 한다.
- 실제 체결 PnL이 손실이어도 후보 forward가 좋으면 selection 문제가 아니라 execution 또는 risk 문제일 수 있다.
- clean learning sample이 아직 적으므로 전체 실손익을 바로 lesson이나 brain 정책으로 승격하면 안 된다.

따라서 먼저 필요한 것은 신규 gate가 아니라 라벨링 계층이다. 라벨링이 생겨야 어떤 개선이 selection prompt에 들어가야 하는지, 어떤 개선이 execution/risk로 가야 하는지, 어떤 케이스는 데이터 품질 문제로 버려야 하는지 결정할 수 있다.

## 4. 무엇을 만든다

새 분석 DB를 추가한다.

```text
data/ml/claude_decision_facts.db
```

새 도구를 추가한다.

```text
tools/build_claude_decision_facts.py
tools/label_claude_judgments.py
tools/report_claude_misjudgments.py
```

새 테스트를 추가한다.

```text
tests/test_claude_decision_facts.py
tests/test_label_claude_judgments.py
tests/test_report_claude_misjudgments.py
```

초기 구현은 dashboard와 runtime에 연결하지 않는다. 매일 장 종료 후 CLI로 실행하고, 결과 markdown/json 리포트를 `docs/reports/` 또는 운영 산출물 위치에 저장한다.

## 5. 대상 코드와 현재 연결점

### 5.1 입력 source

| 입력 | 현재 코드 | 사용 목적 |
| --- | --- | --- |
| `audit_candidate_rows` | `audit/candidate_audit_store.py` | prompt 포함 여부, Claude action, route final action, risk tags, classification |
| `audit_candidate_outcomes` | `audit/candidate_audit_store.py`, `tools/update_candidate_audit_outcomes.py` | 30m/60m/1d/3d/5d forward outcome |
| `candidate_counterfactual_paths` | `audit/candidate_counterfactual_store.py`, `tools/update_counterfactual_outcomes.py` | 차단/대기 경로의 가상 entry/outcome |
| `ticker_selection_log` | `ticker_selection_db.py` | 기존 selection log, 1d/3d/5d forward, trade_ready/watch_only |
| `v2_canonical_performance` | `tools/sync_v2_learning_performance.py` | 실제 체결/청산, PnL, MFE/MAE, learning_allowed |
| `logs/daily_judgment/` | 파일 로그 | 시장 mode 판단과 actual direction 비교 |
| `state/lesson_candidates.json` | `trading_bot.py`, `minority_report/active_lessons.py` | label aggregate를 교훈 후보로 연결할 때만 사용 |

### 5.2 코드레벨 재검토 보정

아래 보정은 실제 코드 스키마를 기준으로 한 구현 안전장치다.

1. `audit_candidate_latest_rows`만 mart source로 쓰면 안 된다.
   - 이 view는 `audit/candidate_audit_store.py`에서 `(runtime_mode, market, session_date, ticker)` 기준 최신 row 1개만 남긴다.
   - 같은 세션에서 preopen/rescreen/runtime_filter가 여러 번 생긴 경우 초기 Claude 오판 또는 route 전이를 잃을 수 있다.
   - mart는 기본적으로 `audit_candidate_rows` call-level row를 보존하고, report 단계에서만 `--latest-only` dedupe를 선택한다.

2. `candidate_counterfactual_paths.candidate_key`는 `audit_candidate_rows.candidate_key`와 항상 같은 key가 아니다.
   - `runtime/counterfactual_paths.py`는 candidate_key가 없으면 자체 deterministic key를 만든다.
   - 따라서 counterfactual은 `candidate_key` direct join을 1순위로 쓰되, 실패 시 `(runtime_mode, market, session_date, ticker, known_at)`와 `path_name` 기반 보조 매칭으로만 붙인다.
   - 매칭 실패 또는 다중 매칭은 `source_quality='ambiguous_match'`로 남긴다.

3. execution 연결은 `candidate_key`가 아니라 decision/path key가 우선이다.
   - `audit_candidate_rows` extra column에는 `execution_decision_id`, `execution_link_source`, `execution_event_id`가 있다.
   - `ticker_selection_log`에도 `execution_decision_id`가 있다.
   - `v2_canonical_performance`는 `v2_decision_id`, `canonical_key`, `path_run_id`가 중심이다.
   - 따라서 execution match 우선순위는 `execution_decision_id/v2_decision_id -> path_run_id -> unique session/ticker` 순서다.

4. 실제 prompt 포함 여부는 `in_prompt`만으로 부족하다.
   - `audit_candidate_rows` extra column에 `final_prompt_included`, `raw_rank`, `trainer_score_rank`, `prompt_excluded_reason`, `trainer_candidate_state`, `candidate_pool_version`, `prompt_pool_version`가 있다.
   - labeler가 "Claude가 봤는가"를 판단할 때는 `final_prompt_included`와 `input_to_claude_reported`를 우선하고, `in_prompt`는 legacy 보조값으로만 사용한다.

5. risk/data veto는 여러 컬럼에 흩어져 있다.
   - `risk_tags_json`, `route_reason`, `route_runtime_gate_reason`만 보지 말고 `hard_blocks`, `soft_gates`, `data_quality_flags_json`, `data_quality`, `history_status`, `evidence_data_state`, `quarantine_reason`, `prompt_excluded_reason`도 함께 파싱한다.

6. `logs/daily_judgment/live_*.json`은 큰 JSON 파일이다.
   - Phase 0에서는 market judgment fact를 만들지 않는다.
   - market fact는 후속 단계에서 별도 `parse_status`를 두고 best-effort로 생성한다.
   - market fact 파싱 실패가 selection label 생성을 막으면 안 되며, selection label owner 결정에는 초기 버전에서 사용하지 않는다.

### 5.3 출력 source

| 출력 | 생성 도구 | 의미 |
| --- | --- | --- |
| `fact_selection` | `build_claude_decision_facts.py` | Claude 선택과 route 결과를 종목 단위로 정규화 |
| `fact_forward_outcome` | `build_claude_decision_facts.py` | 후보 이후 가격 성과 |
| `fact_execution` | `build_claude_decision_facts.py` | 실제 체결/청산 성과 |
| `fact_market_judgment` | 후속 도구 또는 builder 확장 | 시장 판단과 실제 방향 |
| `decision_labels` | `label_claude_judgments.py` | 정답/오판/소유자 라벨 |
| markdown/json report | `report_claude_misjudgments.py` | 사람이 보는 개선 우선순위 |

## 6. DB 스키마 요구사항

### 6.1 공통 메타

모든 fact table은 아래 필드를 가진다.

```sql
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL,
source_quality TEXT NOT NULL DEFAULT 'unknown',
source_refs_json TEXT NOT NULL DEFAULT '{}'
```

`source_quality` 허용값:

```text
complete
partial
missing_outcome
missing_execution
ambiguous_match
data_quality_blocked
unknown
```

`source_refs_json`에는 원본 DB/table/key를 넣는다.

예:

```json
{
  "candidate_key": "...",
  "call_id": "...",
  "ticker_selection_log_id": 123,
  "v2_decision_id": "...",
  "path_run_id": "..."
}
```

### 6.2 `fact_selection`

종목 선택과 route 결과의 중심 테이블이다.

```sql
CREATE TABLE IF NOT EXISTS fact_selection (
    selection_key TEXT PRIMARY KEY,
    runtime_mode TEXT NOT NULL,
    session_date TEXT NOT NULL,
    market TEXT NOT NULL,
    ticker TEXT NOT NULL,
    candidate_key TEXT,
    call_id TEXT,
    known_at TEXT,
    source TEXT NOT NULL,
    source_file TEXT,
    dedupe_key TEXT NOT NULL,

    prompt_included INTEGER NOT NULL DEFAULT 0,
    final_prompt_included INTEGER,
    input_to_claude_reported INTEGER NOT NULL DEFAULT 0,
    prompt_rank INTEGER,
    raw_rank INTEGER,
    trainer_score_rank INTEGER,
    prompt_excluded_reason TEXT,
    classification TEXT,

    raw_action TEXT,
    normalized_action TEXT,
    final_action TEXT,
    route_route TEXT,
    route_reason TEXT,
    route_demoted_to TEXT,
    route_runtime_gate_reason TEXT,

    claude_watchlist INTEGER NOT NULL DEFAULT 0,
    claude_trade_ready INTEGER NOT NULL DEFAULT 0,
    trade_ready INTEGER NOT NULL DEFAULT 0,
    selected_reason TEXT,
    veto_reason TEXT,
    claude_reason TEXT,
    claude_veto_reason TEXT,

    recommended_strategy TEXT,
    risk_tags_json TEXT NOT NULL DEFAULT '[]',
    hard_blocks_json TEXT NOT NULL DEFAULT '[]',
    soft_gates_json TEXT NOT NULL DEFAULT '[]',
    data_quality_flags_json TEXT NOT NULL DEFAULT '[]',
    data_quality TEXT,
    evidence_data_state TEXT,
    trainer_candidate_state TEXT,
    liquidity_bucket TEXT,
    market_type TEXT,
    primary_bucket TEXT,
    change_pct REAL,
    gap_pct REAL,
    from_high_pct REAL,
    volume_ratio REAL,
    turnover REAL,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_quality TEXT NOT NULL DEFAULT 'unknown',
    source_refs_json TEXT NOT NULL DEFAULT '{}'
);
```

인덱스:

```sql
CREATE INDEX IF NOT EXISTS idx_fact_selection_session
    ON fact_selection(runtime_mode, market, session_date, ticker);
CREATE INDEX IF NOT EXISTS idx_fact_selection_action
    ON fact_selection(market, session_date, final_action, classification);
CREATE INDEX IF NOT EXISTS idx_fact_selection_dedupe
    ON fact_selection(runtime_mode, market, session_date, ticker, dedupe_key);
```

`selection_key` 생성 규칙:

1. `candidate_key`가 있으면 `audit:{candidate_key}`.
2. 없고 `ticker_selection_log.id`가 있으면 `selection_log:{id}`.
3. 둘 다 없으면 `fallback:{runtime_mode}:{market}:{session_date}:{ticker}:{known_at}`.

`dedupe_key` 생성 규칙:

```text
{runtime_mode}:{market}:{session_date}:{ticker}
```

`dedupe_key`는 report에서 latest/session-ticker 기준 집계를 만들 때만 사용한다. fact table 자체는 call-level row를 보존한다.

### 6.3 `fact_forward_outcome`

후보 자체가 맞았는지 보는 가격 결과 테이블이다.

```sql
CREATE TABLE IF NOT EXISTS fact_forward_outcome (
    selection_key TEXT PRIMARY KEY,
    runtime_mode TEXT NOT NULL,
    session_date TEXT NOT NULL,
    market TEXT NOT NULL,
    ticker TEXT NOT NULL,

    forward_30m_pct REAL,
    forward_60m_pct REAL,
    forward_1d_pct REAL,
    forward_3d_pct REAL,
    forward_5d_pct REAL,
    max_runup_3d_pct REAL,
    max_drawdown_3d_pct REAL,
    max_runup_5d_pct REAL,
    max_drawdown_5d_pct REAL,

    outcome_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    outcome_source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_quality TEXT NOT NULL DEFAULT 'unknown',
    source_refs_json TEXT NOT NULL DEFAULT '{}'
);
```

우선순위:

1. `audit_candidate_outcomes`의 horizon별 outcome을 우선 사용한다.
2. 없으면 `ticker_selection_log.forward_1d/3d/5d`, `max_runup_3d/5d`를 사용한다.
3. 둘 다 없으면 `outcome_status='MISSING'`, `source_quality='missing_outcome'`.

### 6.4 `fact_execution`

실제 주문/청산 결과 테이블이다.

```sql
CREATE TABLE IF NOT EXISTS fact_execution (
    execution_key TEXT PRIMARY KEY,
    selection_key TEXT,
    runtime_mode TEXT NOT NULL,
    session_date TEXT NOT NULL,
    market TEXT NOT NULL,
    ticker TEXT NOT NULL,

    v2_decision_id TEXT,
    execution_decision_id TEXT,
    legacy_decision_id INTEGER,
    canonical_key TEXT,
    path_type TEXT,
    path_run_id TEXT,
    strategy TEXT,
    origin_action TEXT,

    filled INTEGER NOT NULL DEFAULT 0,
    closed INTEGER NOT NULL DEFAULT 0,
    earliest_fill_at TEXT,
    first_closed_at TEXT,
    last_closed_at TEXT,
    entry_price REAL,
    exit_price REAL,
    pnl_pct REAL,
    mfe_pct REAL,
    mae_pct REAL,
    close_reason TEXT,
    quality_grade TEXT,
    learning_allowed INTEGER NOT NULL DEFAULT 0,

    match_quality TEXT NOT NULL DEFAULT 'unknown',
    execution_link_source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_quality TEXT NOT NULL DEFAULT 'unknown',
    source_refs_json TEXT NOT NULL DEFAULT '{}'
);
```

매칭 우선순위:

1. `audit_candidate_rows.execution_decision_id`, `ticker_selection_log.execution_decision_id`, `selection_meta.v2_decision_ids` 중 하나가 있으면 `v2_decision_id`로 직접 연결한다.
2. `path_run_id`가 있으면 `v2_canonical_performance.path_run_id` 또는 `v2_path_runs.path_run_id`로 연결한다.
3. legacy `decisions.id`가 payload 또는 link table에 있으면 `v2_decision_fill_links.legacy_decision_id`를 보조로 사용한다.
4. 없으면 `(runtime_mode, market, session_date, ticker)`로 연결하되 1건일 때만 `match_quality='session_ticker_unique'`.
5. 2건 이상이면 연결하지 말고 `match_quality='ambiguous_session_ticker'`로 남긴다.
6. `candidate_key`는 execution 연결의 직접 키로 사용하지 않는다. counterfactual 또는 audit source ref로만 보존한다.

### 6.5 `fact_market_judgment`

시장 mode 판단은 selection label과 분리한다.

```sql
CREATE TABLE IF NOT EXISTS fact_market_judgment (
    market_key TEXT PRIMARY KEY,
    session_date TEXT NOT NULL,
    market TEXT NOT NULL,
    consensus_mode TEXT,
    consensus_dir TEXT,
    actual_dir TEXT,
    market_change_pct REAL,
    hit INTEGER,
    parse_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_quality TEXT NOT NULL DEFAULT 'unknown',
    source_refs_json TEXT NOT NULL DEFAULT '{}'
);
```

초기 구현에서 로그 파싱이 불안정하면 `parse_status='UNSUPPORTED_LOG_FORMAT'`로 남기고 selection 라벨에는 사용하지 않는다.

### 6.6 `decision_labels`

최종 라벨 테이블이다.

```sql
CREATE TABLE IF NOT EXISTS decision_labels (
    label_key TEXT PRIMARY KEY,
    selection_key TEXT NOT NULL,
    runtime_mode TEXT NOT NULL,
    session_date TEXT NOT NULL,
    market TEXT NOT NULL,
    ticker TEXT NOT NULL,

    label TEXT NOT NULL,
    owner TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    label_rule TEXT NOT NULL,
    improvement_hint TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

`label` 허용값:

```text
correct_positive
false_positive
correct_negative
false_negative
risk_justified_miss
execution_issue
data_quality_issue
market_mode_issue
unknown
```

`owner` 허용값:

```text
claude_selection
execution
risk_policy
data_quality
market_mode
none
unknown
```

## 7. 라벨링 규칙

라벨러는 설정 가능한 threshold를 가진다. 기본값은 코드 상수로 두고 CLI에서 override할 수 있게 한다.

```python
DEFAULT_LABEL_CONFIG = {
    "positive_forward_3d_pct": 3.0,
    "strong_forward_3d_pct": 5.0,
    "false_positive_1d_pct": -3.0,
    "false_positive_3d_pct": -5.0,
    "missed_runup_3d_pct": 5.0,
    "kr_strong_missed_runup_3d_pct": 10.0,
    "bad_drawdown_3d_pct": -5.0,
    "execution_issue_forward_3d_pct": 3.0,
    "execution_issue_actual_pnl_pct": -1.0,
}
```

### 7.1 action family

```python
POSITIVE_ACTIONS = {"BUY_READY", "TRADE_READY", "PROBE_READY", "ADD_READY"}
WAIT_ACTIONS = {"WATCH", "PULLBACK_WAIT", "WATCH_CONFIRM"}
NEGATIVE_ACTIONS = {"AVOID", "SKIP", "DO_NOT_TRADE", "HARD_BLOCK", "BLOCKED"}
```

판단 기준은 `final_action`을 우선한다. 단, Claude raw action과 runtime final action이 다르면 둘 다 evidence에 남긴다.

action source 우선순위:

1. `route_final_action`
2. `claude_action`
3. `claude_trade_ready=1` 또는 `ticker_selection_log.trade_ready=1`이면 `TRADE_READY`
4. `classification='watch_only'` 또는 `trade_ready=0`이면 `WATCH`
5. 그래도 비어 있으면 `UNKNOWN`

### 7.2 `correct_positive`

조건:

- positive action family
- `forward_3d_pct >= positive_forward_3d_pct` 또는 `max_runup_3d_pct >= strong_forward_3d_pct`
- source outcome이 complete 또는 partial

owner:

```text
none
```

의미:

- Claude selection을 보존해야 하는 케이스다.
- US `trade_ready`의 좋은 edge를 차단하지 않도록 보고서에서 별도 bucket으로 집계한다.

### 7.3 `false_positive`

조건:

- positive action family
- `forward_1d_pct <= false_positive_1d_pct` 또는 `forward_3d_pct <= false_positive_3d_pct` 또는 `max_drawdown_3d_pct <= bad_drawdown_3d_pct`
- runtime data quality가 충분하고, execution issue로 설명되지 않음

owner:

```text
claude_selection
```

improvement hint 예:

```text
과열, 갭, 고ATR, 상단 추격 후보를 BUY_READY로 바로 올리지 말고 PROBE_READY 또는 WATCH_CONFIRM으로 낮춘다.
```

### 7.4 `false_negative`

조건:

- wait 또는 negative action family
- 명확한 risk veto가 없음
- `max_runup_3d_pct >= missed_runup_3d_pct`
- KR에서 강한 missed winner 보고서는 `max_runup_3d_pct >= kr_strong_missed_runup_3d_pct`를 별도 sub bucket으로 표시

owner:

```text
claude_selection
```

의미:

- watch-only에 오래 둔 missed winner다.
- 단, 아래 `risk_justified_miss` 조건이 먼저 평가되어야 한다.

### 7.5 `risk_justified_miss`

조건:

- wait 또는 negative action family
- 이후 runup이 있었더라도 risk veto가 명확함

risk veto 키워드:

```text
low_liquidity
liquidity_bad
blackout
same_day_reentry
broker_untrusted
broker_quarantine
affordability_fail
hard_risk_block
late_session
order_unknown
halt
data_degraded
```

키워드 검색 대상:

```text
risk_tags_json
hard_blocks_json
soft_gates_json
data_quality_flags_json
route_reason
route_runtime_gate_reason
prompt_excluded_reason
quarantine_reason
data_quality
history_status
evidence_data_state
source_refs_json
```

owner:

```text
risk_policy
```

의미:

- Claude 오판으로 집계하지 않는다.
- 기회비용 리포트에는 표시하되 lesson candidate로 자동 승격하지 않는다.

### 7.6 `execution_issue`

조건:

- 후보 forward는 좋음: `forward_3d_pct >= execution_issue_forward_3d_pct` 또는 `max_runup_3d_pct >= strong_forward_3d_pct`
- 실제 체결 PnL은 나쁨: `pnl_pct <= execution_issue_actual_pnl_pct`
- `learning_allowed=1` 또는 `quality_grade`가 clean 계열

owner:

```text
execution
```

improvement hint 예:

```text
후보 선택은 맞았지만 진입 지연, 익절/트레일링 실패, PathB price plan 불일치, broker truth 오염 여부를 execution 쪽에서 점검한다.
```

### 7.7 `correct_negative`

조건:

- wait 또는 negative action family
- `forward_3d_pct <= 0`
- `max_runup_3d_pct < missed_runup_3d_pct`
- source outcome이 complete 또는 partial

owner:

```text
none
```

의미:

- AVOID/WATCH가 실제로 하락 또는 무상승으로 끝난 케이스다.
- selection prompt를 바꿀 때 이 bucket을 훼손하면 안 된다.

### 7.8 `data_quality_issue`

조건:

- outcome이 없거나 stale
- execution match가 ambiguous
- candidate source가 중복되어 latest/dedupe 판단이 불가능
- 시장 또는 session_date가 비어 있음

owner:

```text
data_quality
```

의미:

- 학습과 lesson 승격에서 제외한다.
- 리포트에는 source fix 필요 항목으로만 표시한다.

### 7.9 `market_mode_issue`

초기 버전에서는 자동 부여하지 않는다. 시장 판단 라벨은 `fact_market_judgment`에만 저장한다. 추후 market mode가 종목 선택을 막은 명확한 증거가 있는 경우에만 별도 rule로 추가한다.

## 8. 구현 요구사항

### 8.1 `tools/build_claude_decision_facts.py`

역할:

- source DB를 읽기 전용 attach한다.
- mart schema를 생성하거나 migration한다.
- 지정 기간의 selection, outcome, execution, market judgment fact를 upsert한다.

CLI:

```bash
python tools/build_claude_decision_facts.py --start-date 2026-04-07 --end-date 2026-05-20 --market ALL --runtime-mode live
python tools/build_claude_decision_facts.py --date 2026-05-20 --market KR --runtime-mode live
```

옵션:

```text
--db data/ml/claude_decision_facts.db
--candidate-audit-db data/audit/candidate_audit.db
--selection-db data/ticker_selection_log.db
--ml-db data/ml/decisions.db
--event-db data/v2_event_store.db
--logs-root logs/daily_judgment
--dry-run
--json
```

필수 동작:

- source DB attach는 가능한 경우 SQLite URI `mode=ro`를 사용한다.
- source DB가 없으면 실패하지 말고 해당 source만 `source_quality='missing_*'`로 기록한다.
- mart 생성은 `audit_candidate_rows` call-level row를 기본으로 한다.
- `audit_candidate_latest_rows`는 report 또는 `--latest-only` 옵션에서만 사용한다.
- call-level row에는 `dedupe_key`, latest rank, source_file, prompt/trainer extra fields를 함께 저장한다.
- `ticker_selection_log`는 audit row가 없는 경우 fallback source로 사용한다.
- execution match가 모호하면 임의로 연결하지 않는다.
- 실행 결과 summary를 JSON으로 출력할 수 있어야 한다.

### 8.2 `tools/label_claude_judgments.py`

역할:

- `claude_decision_facts.db`의 fact를 읽어 `decision_labels`를 생성한다.
- label rule은 순서가 중요하다.

평가 순서:

1. `data_quality_issue`
2. `risk_justified_miss`
3. `execution_issue`
4. `false_positive`
5. `false_negative`
6. `correct_positive`
7. `correct_negative`
8. `unknown`

CLI:

```bash
python tools/label_claude_judgments.py --start-date 2026-04-07 --end-date 2026-05-20 --market ALL --write
python tools/label_claude_judgments.py --date 2026-05-20 --market US --dry-run --json
```

필수 동작:

- 기본은 dry-run이다. 실제 write에는 `--write`가 필요하다.
- threshold override를 CLI로 받는다.
- label overwrite는 같은 `label_key`에 대해 idempotent upsert만 허용한다.
- evidence에는 사용한 forward, runup, drawdown, action, risk tags, execution metrics를 모두 넣는다.

### 8.3 `tools/report_claude_misjudgments.py`

역할:

- label 결과를 사람이 볼 수 있는 개선 리포트로 만든다.
- selection 개선과 execution/risk 개선을 분리해 보여준다.

CLI:

```bash
python tools/report_claude_misjudgments.py --start-date 2026-04-07 --end-date 2026-05-20 --market ALL --format md --output docs/reports/claude_misjudgments_20260521.md
python tools/report_claude_misjudgments.py --date 2026-05-20 --market KR --format json
```

리포트 필수 섹션:

- 시장별 label 분포
- US `correct_positive` 보존 bucket
- KR `false_positive` 과열/갭/고ATR 추적 bucket
- `false_negative`와 `risk_justified_miss` 분리
- `execution_issue` 상위 케이스
- `data_quality_issue` source별 목록
- lesson candidate로 올려도 되는 항목과 올리면 안 되는 항목

## 9. lesson 연결 요구사항

초기 구현은 `state/lesson_candidates.json`을 자동 수정하지 않는다.

2차 구현에서 lesson 후보 연결이 필요하면 별도 옵션을 둔다.

```bash
python tools/label_claude_judgments.py --date 2026-05-20 --market KR --write-lesson-candidates
```

필수 guard:

- `label in ('false_positive', 'false_negative')`이고 `owner='claude_selection'`인 aggregate만 후보가 될 수 있다.
- `risk_justified_miss`, `execution_issue`, `data_quality_issue`는 Claude prompt lesson으로 승격하지 않는다.
- `sample_count >= min_sample` 조건을 만족해야 한다.
- `minority_report.lesson_quality.lesson_quality_fields()`를 재사용하거나 같은 품질 필드를 생성한다.
- JSON은 `encoding='utf-8'`, `ensure_ascii=False`, LF newline로 쓴다.
- 한국어 깨짐이 있으면 write를 중단한다.

권장 구현:

- `--write-lesson-candidates`는 바로 기존 파일에 append하지 말고 먼저 `docs/reports/lesson_candidate_proposals_YYYYMMDD.json`를 생성한다.
- 운영자가 검토한 뒤 별도 승인형 도구로 `state/lesson_candidates.json`에 반영한다.

## 10. 테스트 요구사항

### 10.1 fact builder

`tests/test_claude_decision_facts.py`

필수 케이스:

- schema init이 idempotent여야 한다.
- source DB가 없어도 mart DB는 생성되고 summary에 missing source가 표시되어야 한다.
- 기본 mart 생성은 같은 ticker/session의 여러 call-level row를 모두 보존해야 한다.
- `--latest-only` 또는 report dedupe 모드에서는 `audit_candidate_latest_rows`와 같은 기준으로 최신 row만 집계되어야 한다.
- audit row가 없으면 `ticker_selection_log` row가 fallback fact가 되어야 한다.
- `execution_decision_id` 또는 `v2_decision_id`가 있는 execution은 직접 연결되어야 한다.
- `candidate_key`만 있는 경우에는 execution direct match를 하지 않고 source ref로만 보존해야 한다.
- `(market, session_date, ticker)` 매칭이 2건 이상이면 `ambiguous_session_ticker`로 남기고 임의 연결하지 않아야 한다.
- source DB row count 또는 content를 수정하지 않아야 한다.

### 10.2 labeler

`tests/test_label_claude_judgments.py`

필수 케이스:

- US positive action과 좋은 3d forward는 `correct_positive`.
- positive action과 나쁜 1d/3d forward는 `false_positive`, owner `claude_selection`.
- WATCH missed runup에 명확한 risk tag가 없으면 `false_negative`.
- WATCH missed runup이지만 `low_liquidity` 또는 `broker_untrusted`가 있으면 `risk_justified_miss`.
- 후보 forward는 좋고 실제 PnL은 나쁘며 clean sample이면 `execution_issue`.
- outcome 결측 또는 ambiguous execution match는 `data_quality_issue`.
- 같은 입력을 두 번 실행해도 `decision_labels` row가 중복되지 않아야 한다.

### 10.3 reporter

`tests/test_report_claude_misjudgments.py`

필수 케이스:

- markdown 출력에 market별 label count가 포함되어야 한다.
- `risk_justified_miss`가 `false_negative` 합계에 섞이지 않아야 한다.
- `execution_issue`가 selection 개선 목록에 섞이지 않아야 한다.
- `data_quality_issue`가 lesson candidate 목록에 포함되지 않아야 한다.

## 11. 일일 운영 루프

장 종료 후 순서는 다음과 같다.

```bash
python tools/update_candidate_audit_outcomes.py --date YYYY-MM-DD --market KR --horizons 30,60,1440,2880,4320
python tools/update_candidate_audit_outcomes.py --date YYYY-MM-DD --market US --horizons 30,60,1440,2880,4320

python tools/update_counterfactual_outcomes.py --date YYYY-MM-DD --market KR --retry-missing
python tools/update_counterfactual_outcomes.py --date YYYY-MM-DD --market US --retry-missing

python tools/sync_v2_learning_performance.py --market ALL --start-date YYYY-MM-DD --end-date YYYY-MM-DD --repair-decisions

python tools/build_market_judgment_facts.py --date YYYY-MM-DD --market ALL --runtime-mode live
python tools/build_claude_decision_facts.py --date YYYY-MM-DD --market ALL --runtime-mode live
python tools/label_claude_judgments.py --date YYYY-MM-DD --market ALL --write
python tools/report_claude_misjudgments.py --date YYYY-MM-DD --market ALL --format md --output docs/reports/claude_misjudgments_YYYYMMDD.md
```

주의:

- `build_claude_decision_facts.py`는 앞선 outcome/sync 도구가 실패해도 실행 가능해야 한다. 다만 해당 source는 `source_quality`로 결측 표시한다.
- labeler는 결측을 임의로 채우지 않는다.
- report는 label count와 data quality count를 함께 보여줘야 한다.

## 12. rollout 단계

### Phase 0 - read-only mart

범위:

- `build_claude_decision_facts.py`
- schema와 fact 생성 테스트

완료 조건:

- source DB를 수정하지 않는다.
- `fact_selection`, `fact_forward_outcome`, `fact_execution`이 지정 기간에 대해 생성된다.
- source 결측과 ambiguous match가 summary에 표시된다.

### Phase 1 - labeler

범위:

- `label_claude_judgments.py`
- label rule 테스트

완료 조건:

- label owner가 selection/execution/risk/data로 분리된다.
- `risk_justified_miss`가 `false_negative`에 섞이지 않는다.
- `execution_issue`가 selection 오판에 섞이지 않는다.

### Phase 2 - report

범위:

- `report_claude_misjudgments.py`
- markdown/json 리포트

완료 조건:

- KR/US를 분리해 보여준다.
- US `correct_positive` 보존 항목이 별도 표시된다.
- KR false positive와 false negative가 각각 개선 방향으로 연결된다.

### Phase 3 - lesson proposal

범위:

- label aggregate 기반 lesson proposal 생성
- 운영 승인 전까지 prompt-visible 반영 금지

완료 조건:

- proposal JSON이 UTF-8로 생성된다.
- `risk_policy`, `execution`, `data_quality` owner는 prompt lesson으로 올라가지 않는다.
- `ACTIVE_LESSONS_SHADOW=false` 환경에서도 깨진 한국어가 prompt에 들어가지 않는다.

## 13. 개선 방향

### 13.1 selection 개선

라벨 결과에서 `false_positive`가 반복되는 패턴만 prompt 또는 route guard 후보가 된다.

우선 후보:

- KR `at_high + gap + high ATR + OR missing`인데 `BUY_READY`로 올라간 케이스
- 상한가/급등 직후 추격 매수로 1d 또는 3d drawdown이 큰 케이스
- 명확한 veto 없이 WATCH에 둔 뒤 3일 내 큰 runup이 나온 케이스

개선 방향:

- KR은 selection 폐기가 아니라 `BUY_READY -> PROBE_READY/WATCH_CONFIRM` 강등 조건을 정교화한다.
- US `trade_ready`는 edge가 있으므로 전체 차단하지 않고 false positive cluster만 좁게 본다.
- prompt lesson은 clean label aggregate가 충분할 때만 shadow로 올린다.

### 13.2 execution/risk 개선

`execution_issue`는 Claude selection prompt를 고치는 근거가 아니다.

우선 후보:

- 후보 forward는 좋지만 실제 PnL이 나쁜 케이스
- MFE가 있었지만 익절/트레일링/손익보호가 실패한 케이스
- 진입 지연으로 `entry_price`가 first seen 대비 불리한 케이스
- PathB price plan과 실제 시장 가격이 맞지 않은 케이스
- broker truth 또는 `ORDER_UNKNOWN` 때문에 상태가 오염된 케이스

개선 방향:

- execution report로 분리해 `risk_manager.py`, `runtime/pathb_runtime.py`, broker truth 복구 쪽 요구사항으로 보낸다.
- clean sample만 학습 승격한다.
- broker untrusted/quarantine 상태에서는 신규 진입 개선보다 기존 포지션 보호와 복구를 우선한다.

### 13.3 data quality 개선

`data_quality_issue`가 많으면 prompt나 gate를 바꾸면 안 된다.

우선 후보:

- outcome 결측
- execution ambiguous match
- session_date/market/ticker 불일치
- counterfactual entry/outcome source quality 불량
- 깨진 lesson text

개선 방향:

- outcome updater와 canonical performance sync를 먼저 고친다.
- labeler는 결측을 추론하지 않고 결측 bucket으로 남긴다.
- 한국어 문서와 lesson JSON은 UTF-8 검증을 통과해야 한다.

## 14. 검증 명령

구현 후 최소 검증:

```bash
python -m pytest tests/test_claude_decision_facts.py tests/test_label_claude_judgments.py tests/test_report_claude_misjudgments.py -q
python -m py_compile tools/build_claude_decision_facts.py tools/label_claude_judgments.py tools/report_claude_misjudgments.py
```

관련 회귀:

```bash
python -m pytest tests/test_candidate_audit.py tests/test_update_counterfactual_outcomes.py tests/test_v2_learning_performance_sync.py -q
```

문서/인코딩:

```bash
python tools/check_mojibake.py --staged
```

## 15. 완료 기준

- `data/ml/claude_decision_facts.db`가 지정 기간에 대해 재생성 가능하다.
- label 결과에서 `claude_selection`, `execution`, `risk_policy`, `data_quality`, `market_mode` owner가 분리된다.
- `risk_justified_miss`와 `false_negative`가 분리 집계된다.
- `execution_issue`는 selection 오판 리포트에 섞이지 않는다.
- lesson candidate proposal은 `claude_selection` owner의 충분한 표본만 포함한다.
- `state/brain.json`은 변경되지 않는다.
- 원본 source DB는 수정되지 않는다.

## 16. 코드레벨 재검토 후 요구서 개선안

이 요구서는 최초 아이디어 그대로 구현하면 안 된다. 실제 코드 스키마와 운영 흐름을 대조한 결과, 아래처럼 구현 요구사항을 좁히고 보정한다.

### 16.1 바꿀 것

1. fact builder의 기본 입력을 `audit_candidate_latest_rows`에서 `audit_candidate_rows`로 바꾼다.
   - 이유: latest view는 같은 세션/종목의 여러 Claude call과 route 전이를 1건으로 압축한다.
   - 개선: mart에는 call-level row를 모두 보존하고, report에서만 latest/dedupe 집계를 선택한다.

2. execution match 기준을 `candidate_key` 중심에서 `decision/path key` 중심으로 바꾼다.
   - 이유: `candidate_counterfactual_paths.candidate_key`는 audit candidate key와 항상 같지 않다.
   - 개선: `execution_decision_id`, `v2_decision_id`, `path_run_id`, `legacy_decision_id` 순서로 연결한다.

3. prompt 포함 여부를 `in_prompt` 단일 기준에서 실제 prompt/trainer field 기준으로 바꾼다.
   - 이유: current prompt, overlay shadow/live, trainer excluded row가 분리되어 있다.
   - 개선: `final_prompt_included`, `input_to_claude_reported`, `prompt_excluded_reason`, `trainer_candidate_state`를 fact에 저장한다.

4. risk veto 탐지를 `risk_tags_json` 단일 기준에서 route/data/evidence field 전체 기준으로 넓힌다.
   - 이유: broker untrusted, quarantine, degraded data, hard block은 여러 컬럼에 흩어져 있다.
   - 개선: `risk_tags_json`, `hard_blocks`, `soft_gates`, `data_quality_flags_json`, `route_reason`, `route_runtime_gate_reason`, `quarantine_reason`, `evidence_data_state`를 함께 파싱한다.

5. market judgment는 Phase 0에서 만들거나 selection label 판단에 쓰지 않는다.
   - 이유: `logs/daily_judgment/live_*.json`은 크고, parsing/actual direction 정의가 불안정하다.
   - 개선: `fact_market_judgment`는 Phase 4에서 best-effort로 저장하되, 초기 label owner 산정에서는 제외한다.

### 16.2 하지 말 것

아래는 구현 중 금지한다.

- source DB row를 update/delete/backfill하지 않는다.
- `audit_candidate_latest_rows`만 보고 selection label을 만들지 않는다.
- `candidate_key`만으로 execution을 direct match하지 않는다.
- `risk_justified_miss`를 `false_negative`에 합산하지 않는다.
- `execution_issue`를 Claude prompt 개선 후보로 올리지 않는다.
- `data_quality_issue`를 lesson candidate로 올리지 않는다.
- `state/brain.json` 또는 prompt-visible active lesson을 자동 수정하지 않는다.

### 16.3 1차 구현 범위

첫 구현은 Phase 0만 한다.

대상:

```text
tools/build_claude_decision_facts.py
tests/test_claude_decision_facts.py
data/ml/claude_decision_facts.db
```

Phase 0에서 만드는 테이블:

```text
fact_selection
fact_forward_outcome
fact_execution
fact_build_runs
```

Phase 0에서 보류하는 것:

```text
fact_market_judgment
decision_labels
lesson proposal
dashboard integration
runtime integration
```

Phase 0 완료 조건:

- `audit_candidate_rows` call-level row가 fact로 들어간다.
- 같은 ticker/session의 여러 row가 보존된다.
- `dedupe_key`와 latest rank가 저장된다.
- outcome이 없으면 `missing_outcome`으로 남긴다.
- execution direct match는 `execution_decision_id/v2_decision_id/path_run_id`가 있을 때만 한다.
- ambiguous session/ticker match는 연결하지 않는다.
- source DB는 read-only로 열고 수정하지 않는다.

### 16.4 2차 구현 범위

Phase 0 검증 후에만 labeler를 만든다.

대상:

```text
tools/label_claude_judgments.py
tests/test_label_claude_judgments.py
decision_labels
```

Phase 1 완료 조건:

- `correct_positive`, `false_positive`, `false_negative`, `risk_justified_miss`, `execution_issue`, `data_quality_issue`, `correct_negative`, `unknown`이 규칙 순서대로 생성된다.
- owner가 `claude_selection`, `execution`, `risk_policy`, `data_quality`, `none`, `unknown`으로 분리된다.
- label evidence에 action, forward, runup/drawdown, risk veto, execution metric이 들어간다.

### 16.5 3차 구현 범위

labeler 검증 후 report를 만든다.

대상:

```text
tools/report_claude_misjudgments.py
tests/test_report_claude_misjudgments.py
```

Phase 2 완료 조건:

- KR/US label 분포가 분리된다.
- US `correct_positive` 보존 bucket이 따로 표시된다.
- KR false positive cluster와 false negative cluster가 분리된다.
- `risk_justified_miss`, `execution_issue`, `data_quality_issue`는 selection 개선 후보에서 제외된다.

### 16.6 남은 작업 플랜

Phase 0 builder는 구현과 검증이 끝난 상태로 본다. 남은 작업은 아래 순서로만 진행한다.

1. Phase 1 labeler
   - `tools/label_claude_judgments.py`
   - `tests/test_label_claude_judgments.py`
   - `decision_labels`
   - 목표: `selection`, `execution`, `risk_policy`, `data_quality` owner를 분리하고 `risk_justified_miss`와 `execution_issue`를 Claude selection 오판에서 제외한다.

2. Phase 2 report
   - `tools/report_claude_misjudgments.py`
   - `tests/test_report_claude_misjudgments.py`
   - 목표: KR/US label 분포, US 보존 bucket, KR false positive/false negative cluster, data quality count를 markdown/json으로 보여준다.

3. Phase 3 lesson proposal
   - label aggregate 기반 proposal JSON 생성
   - 목표: `claude_selection` owner의 충분한 표본만 lesson 후보로 올리고, `risk_policy`, `execution`, `data_quality` owner는 prompt lesson에서 제외한다.
   - `state/lesson_candidates.json`은 직접 append하지 않고 운영자 승인 전 proposal 파일만 만든다.

4. Phase 4 market judgment fact
   - `fact_market_judgment`
   - 목표: `logs/daily_judgment/`를 별도 parse_status와 함께 best-effort로 정리한다.
   - 초기 selection label owner 계산에는 사용하지 않는다.

5. Phase 5 dashboard/runtime integration
   - dashboard 표시와 runtime/prompt 반영은 label/report/lesson proposal이 실제 운영 DB에서 안정화된 뒤 진행한다.
   - `state/brain.json` 자동 변경과 prompt-visible active lesson 반영은 계속 금지한다.

### 16.7 요구서 결론

개선 방향은 "Claude 판단을 더 세게 차단"이 아니다. 먼저 code-level fact mart로 분모를 고정하고, 그 위에서 오판 소유자를 분리한다.

최초 PR은 builder만 구현한다. labeler와 report는 builder 출력이 실제 DB에서 안정적으로 재생성되는 것을 확인한 뒤 붙인다.

## 17. 구현 진행 현황 - 2026-05-21

### 17.1 완료된 항목

Phase 0부터 Phase 4까지 구현했다.

```text
tools/build_claude_decision_facts.py
tools/label_claude_judgments.py
tools/report_claude_misjudgments.py
tools/build_market_judgment_facts.py
tests/test_claude_decision_facts.py
tests/test_label_claude_judgments.py
tests/test_report_claude_misjudgments.py
tests/test_market_judgment_facts.py
```

생성/확장되는 mart table:

```text
fact_selection
fact_forward_outcome
fact_execution
fact_build_runs
decision_labels
fact_market_judgment
```

lesson 연결은 `state/lesson_candidates.json`을 직접 수정하지 않고 proposal JSON만 생성한다.

```text
docs/reports/lesson_candidate_proposals_YYYYMMDD.json
```

### 17.2 요구서 대비 구현 차이

1. `fact_market_judgment`는 `build_claude_decision_facts.py`에 섞지 않고 `tools/build_market_judgment_facts.py`로 분리했다.
   - 이유: daily judgment 로그 파싱은 selection/execution mart와 source 성격이 다르다.
   - 효과: market mode fact 실패가 selection label 생성을 막지 않는다.

2. `lesson proposal`은 `label_claude_judgments.py --write-lesson-candidates` 옵션으로만 생성한다.
   - `state/lesson_candidates.json` append는 하지 않는다.
   - proposal은 `claude_selection` owner의 `false_positive`/`false_negative` aggregate만 포함한다.

3. `market_mode_issue` label은 아직 자동 부여하지 않는다.
   - 시장 판단은 `fact_market_judgment`에만 저장한다.
   - selection label owner 산정에는 사용하지 않는다.

### 17.3 QA 결과

실행한 검증:

```bash
python -m py_compile tools/build_claude_decision_facts.py tools/label_claude_judgments.py tools/report_claude_misjudgments.py tools/build_market_judgment_facts.py
python -m pytest tests/test_claude_decision_facts.py tests/test_label_claude_judgments.py tests/test_report_claude_misjudgments.py tests/test_market_judgment_facts.py tests/test_candidate_audit.py tests/test_v2_learning_performance_sync.py -q
```

결과:

```text
64 passed
```

운영 DB 기준 2026-05-20 live ALL 테스트:

```text
fact_selection: 1852
fact_forward_outcome: 1852
fact_execution: 1852
decision_labels: 1852
fact_market_judgment: 2
```

2026-05-20 label 분포:

```text
data_quality_issue / data_quality: 1676
unknown / unknown: 176
```

이 날짜는 outcome 결측이 많아 selection 오판 라벨로 승격하지 않는 것이 정상이다. 장 종료 후 `update_candidate_audit_outcomes.py`와 `update_counterfactual_outcomes.py`를 먼저 돌린 뒤 labeler/report를 실행해야 한다.

2026-05-20 market judgment fact:

```text
KR DEFENSIVE -> bear, actual bear, hit=1
US MILD_BULL -> bull, actual bull, hit=1
```

생성된 운영 리포트:

```text
docs/reports/claude_misjudgments_20260520.md
docs/reports/lesson_candidate_proposals_20260520.json
```

### 17.4 아직 하지 않는 항목

Phase 5 dashboard/runtime integration은 아직 하지 않는다.

보류 이유:

- label/report/proposal이 실제 운영 DB에서 며칠 이상 안정적으로 재생성되는지 먼저 확인해야 한다.
- prompt-visible active lesson 반영은 표본 수와 UTF-8 품질을 확인한 뒤 승인형으로 진행해야 한다.
- `state/brain.json` 자동 변경은 계속 금지한다.

구체적인 판단 근거:

- Phase 1~4는 분석 mart, label, report, proposal 파일 생성까지라 주문/수량/risk gate/prompt에 직접 영향을 주지 않는다.
- Phase 5는 dashboard 또는 runtime/prompt 연결 단계이므로 잘못된 label이 운영자 판단이나 Claude prompt를 오염시킬 수 있다.
- 2026-05-20 운영 테스트에서는 `decision_labels` 1852건 중 `data_quality_issue`가 1676건이었다. 이 상태는 selection 오판보다 outcome 결측이 많은 상태다.
- 따라서 지금 dashboard/runtime에 붙이면 "Claude selection 오판"이 아니라 "outcome updater 미완료"를 운영 신호처럼 보이게 할 위험이 있다.
- 2026-05-20 기준 lesson proposal은 0건이 정상이다. 표본이 없는 상태에서 prompt-visible lesson으로 연결하면 학습 오염 가능성이 있다.
- `fact_market_judgment`는 생성했지만 selection label owner 계산에는 쓰지 않았다. 시장 mode 판단과 종목 selection 성과를 섞지 않는다는 원칙을 유지하기 위해서다.

Phase 5 진입 조건:

- `decision_labels`가 최소 여러 장 종료일에 대해 정상 생성된다.
- `data_quality_issue`가 outcome updater 누락 때문인지 실제 데이터 품질 문제인지 분리된다.
- `lesson_candidate_proposals_YYYYMMDD.json`에서 `risk_policy`, `execution`, `data_quality` owner가 제외되는 것이 반복 확인된다.
- 운영자가 dashboard/runtime 반영을 승인한다.

Phase 5에서 처음 붙일 범위:

1. Dashboard read-only 표시
   - `decision_labels` label 분포
   - `data_quality_issue` 비율
   - `execution_issue`와 `risk_justified_miss` 별도 bucket
   - prompt-visible 또는 주문 로직과 연결하지 않는다.

2. 운영 리포트 링크
   - 최신 `claude_misjudgments_YYYYMMDD.md`
   - 최신 `lesson_candidate_proposals_YYYYMMDD.json`
   - proposal은 승인 전까지 보기 전용으로만 둔다.

3. Runtime/prompt 연결은 별도 승인 후 shadow부터 시작
   - `state/brain.json` 자동 변경 금지
   - `state/lesson_candidates.json` 직접 append 금지
   - `ACTIVE_LESSONS_SHADOW=true` 또는 동등한 shadow gate에서만 검증
