# Candidate Prediction Pipeline Implementation — 2026-07-16

## Outcome

The approved direction is implemented as a research-only observation lane. It
does not change candidate ranking, buy gates, order sizing, exit ownership, or
order submission.

## 기존 방식과 달라지는 방향

| 영역 | 기존 방식 | 변경 방식 | 수익성 측면의 의미 |
|---|---|---|---|
| 후보 원장 | Claude 호출별 mutable row 중심. 같은 종목의 최초 모습이 후속 평가로 덮이거나 분석자가 다시 복원해야 했음 | 세션·종목별 최초 관측을 immutable row로 봉인하고 이후 변화는 append-only event로 저장 | PYPL처럼 후보 풀에는 있었지만 어느 단계에서 탈락했는지 사후 복원이 가능해져, 파이프라인 누락과 전략 기각을 구분할 수 있음 |
| 후보 가격 추적 | 후보 판단이 다시 일어난 시점의 드문 가격만 존재 | 최초 관측 + 5분 버킷 bounded quote + 기존 30/60분 outcome 연결 | 저장소 폭증 없이 “안 산 종목이 이후 어떻게 움직였는가”를 전수 측정할 수 있음 |
| 거래량 해석 | 누적 거래량을 일평균 거래량에 경과시간 비율로 단순 배분 | 직전 20세션의 동일 경과분 누적 거래량 중앙값과 비교하는 time-normalized RVOL 병행 | 개장 초 거래량 쏠림을 정상 거래량으로 오판하거나, 장중 거래량을 과대평가하는 오류를 줄임 |
| 합의 모델 | 같은 시스템 피처를 공유하는 모델끼리 합의해 독립 증거처럼 보일 수 있었음 | 피처군과 모델 종류가 모두 다른 두 arm만 사용 | 같은 정보의 중복 투표를 제거하고, 서로 다른 증거가 일치할 때만 후보를 올림 |
| 후보가 애매할 때 | 모델별 top-N 결과가 존재하면 그중 하나를 해석해야 했음 | 동일 세션·동일 종목 top-3 교집합이 아니면 명시적으로 `ABSTAIN` | 매수 횟수를 억지로 만들지 않고 정밀도 우선 전략으로 전환 |
| KR 공시 | 실적 공시 외 유상증자·공급계약이 후보 판단 원장과 구조적으로 연결되지 않음 | DART 공시를 사전 수집해 observer metadata로만 기록 | 유상증자 회피와 수주공시 익일 효과를 look-ahead 없이 검증할 기반 확보 |
| 운영 생존성 | 분석 스크립트를 수동 실행하면 조용히 중단될 수 있음 | preopen scheduler, status file, preflight stale 검사 연결 | 표본 시계가 멈춘 사실을 운영자가 즉시 확인 가능 |

### 전략 철학의 변화

기존 방향은 모델 점수를 바로 개선하거나 게이트를 조정해 매수 후보를
늘리는 쪽에 가까웠다. 변경 후 방향은 다음 순서를 강제한다.

1. 모든 후보의 최초 상태와 이후 경로를 먼저 보존한다.
2. 시스템 점수와 외부·가격 증거를 서로 분리해 평가한다.
3. 독립 증거가 합의하지 않으면 기권한다.
4. prospective 원장에서 실제 net 성과가 확인된 코호트만 별도 승인으로
   live 권한을 검토한다.

즉 현재 매매 전략 자체를 즉시 교체한 것이 아니라, 향후 전략 변경이
“놓친 종목 몇 개”나 사후 선택 수치가 아니라 전체 후보 원장과 비용 후
성과에 의해 결정되도록 바뀌었다.

### 현재 라이브 행동에서 바뀌지 않는 것

- 후보 pool hard cap과 기존 screener ranking
- Claude/judge의 action과 PathB 진입 게이트
- 주문 금액, 슬롯, 손절, early-tier, split-runner
- 코어·swing·PathB의 order authority와 exit owner
- KR 공시 observer tag에 의한 자동 차단 또는 가점
- consensus shadow에 의한 자동 매수

## Implemented contracts

### 1. Immutable prospective candidate registry

- One immutable first row per `runtime_mode / market / session_date / ticker`.
- Repeated evaluations are append-only events.
- Quote observations are bounded to one row per five-minute bucket.
- Verbose prompt/news blobs are excluded from registry event storage.
- The first registry row retains the original call-level candidate key, so the
  existing 30/60-minute and daily outcome ledger remains backward compatible.
- `clear_session()` does not delete the immutable registry.

Tables:

- `candidate_registry_first`
- `candidate_registry_events`
- `candidate_registry_quotes`
- `candidate_registry_first_outcomes` view

Authority: `SHADOW_ONLY_NO_ORDER_AUTHORITY`.

### 2. Time-of-day normalized RVOL

- Current cumulative volume is compared with the median cumulative volume of
  prior sessions at the same elapsed market minute.
- Reference sessions are strictly earlier than the current session.
- Incomplete historical sessions are rejected.
- The same pure calculation is used by live serving and replay tests.
- Existing `volume_ratio_open` remains unchanged; the new fields are parallel
  observation features:
  - `time_normalized_rvol`
  - `rvol_profile_sessions`
  - `rvol_profile_status`

### 3. Disjoint consensus shadow

The prior consensus pairs shared feature families. They were replaced with
feature-disjoint, model-class-disjoint pairs:

- US: `US_BASELINE_LOGIT` AND `US_DAILY_ONLY_FOREST`
- KR: `KR_SYSTEM_SCORES_LOGIT` AND `KR_DAILY_RICH_FOREST`

Decision contract:

- Each arm ranks independently.
- Only the same-session, same-ticker top-3 intersection is `SELECT_SHADOW`.
- Every other candidate is `ABSTAIN`.
- The ledger is append-only and idempotent by event id.
- The latest training session is purged before scoring.

Historical exploratory holdout after redefining the pairs:

| Market | n | Dates | Unique tickers | Mean policy net | Positive rate |
|---|---:|---:|---:|---:|---:|
| US | 4 | 4 | 4 | +0.423% | 75% |
| KR | 2 | 2 | 2 | +3.390% | 100% |

These figures are existence evidence only, not expected live performance.

Prospective KR smoke run on 2026-07-16:

- candidates scored: 80
- consensus selections: 0
- result: all candidates correctly abstained

### 4. KR disclosure observer

- DART types B and I are collected outside the order path.
- Rights offerings and supply contracts are stored as observer metadata only.
- Corrections are explicitly marked with `is_correction`.
- Observer tags are written only to the immutable registry snapshot.
- Existing live `risk_tags` are unchanged.

First refresh:

- DART requests: 11
- tickers: 110
- supply-contract records: 78
- rights-offering records: 59

### 5. Scheduling and stale detection

- KR disclosure refresh: 35 minutes before the KR open.
- Consensus shadow: once at market open +15 minutes for each market.
- Both jobs are enabled in `.env.live` and
  `config/v2_start_config.json`.
- Both write status files and are checked by live preflight.

## Verification

- Focused and regression tests: 168 passed.
- Python compile checks: passed.
- `git diff --check`: passed.
- Live preflight after restart: fail 0.
- New observer heartbeats: PASS.
- Safe restart:
  - checkpoint:
    `data/backups/live_maintenance_20260716_080912_before_restart_candidate_registry_consensus_shadow_20260716`
  - all roles alive: true
  - KR/US broker truth fresh: true
  - positions/open orders before and after restart: 0/0

## Promotion boundary

No consensus or disclosure observation may receive live order authority without
a separate operator decision and a prospective gate. Minimum review conditions:

- at least 30 independent sessions;
- positive performance after duplicate-ticker removal;
- positive block lower confidence bound after costs and stop slippage;
- train/serve feature coverage audit;
- no material concentration in a single ticker, date, or market regime.
