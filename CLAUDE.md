# CLAUDE.md

## PEAD Input Policy (2026-04-24)

- PEAD is an input-quality feature, not a standalone strategy.
- Do not let PEAD override entry timing, stop-loss, trailing stop, or session-close logic.
- Keep PEAD event data out of `brain.json`. Store it in digest/candidate metadata and shadow logs only.

### Source Rules

- US:
  - `earnings_date`: yfinance calendar
  - `surprise_sign` / `surprise_strength`: yfinance `earnings_dates` using `Reported EPS` and `EPS Estimate`
- KR:
  - use `earnings_date` / `earnings_window` first
  - do not infer EPS beat/miss from Naver news or DART headlines
  - if structured KR actual/estimate is unavailable, keep `surprise_sign=unknown`

### Trust Tiers

- `high`: actual EPS and estimate both available
- `medium`: earnings date available, surprise unavailable
- `low`: news/disclosure only

Only `high` may produce `surprise_sign` / `surprise_strength`.
`medium` may only produce `earnings_window`.
`low` must not affect PEAD bias.

### Rollout Rules

- `earnings_date` / `earnings_window`: prompt-visible immediately
- `surprise_sign` / `surprise_strength`: 5 trading days shadow-only first
- During shadow:
  - compute and store values
  - write logs for manual inspection
  - do not expose surprise fields to Claude prompts
- Prompt exposure must be blocked by `state/pead_shadow_state.json`, not by memory or comments only.
- `prompt_surprise_enabled=true` is not sufficient by itself. The manual review checklist below must also pass.

### PEAD manual review gate

Before enabling surprise fields in analyst/selection prompts, all checks must be true in `state/pead_shadow_state.json`.

- `trading_days_observed >= 5`
- `manual_review.tier_null_rate_checked=true`: inspect 5 trading days of shadow logs and tier-level null rates.
- `manual_review.surprise_sample_10_checked=true`: manually verify at least 10 `surprise_sign` cases against source EPS values.
- `manual_review.prompt_leak_zero_checked=true`: confirm zero prompt leaks of surprise text while `prompt_applied=false`.
- `prompt_surprise_enabled=true`: explicit final operator switch after the checklist is complete.
- `manual_review_passed` is derived from checklist values. Do not treat a hand-edited boolean as sufficient.

### PEAD gate completion boundary

The first implementation step is complete only when:

- `state/pead_shadow_state.json` is generated from shadow logs.
- 5 trading day gating is enforced in code.
- surprise prompt leakage is covered by tests.
- `prompt_applied` remains false until the state gate and checklist pass.

Do not add PEAD weighting, prompt tuning, or source expansion in the same step.

### Prompt Scope

- Allowed:
  - watchlist prioritization
  - trade_ready conviction bias
  - strategy-fit bias
- Not allowed:
  - automatic trade_ready promotion from PEAD alone
  - entry rule override
  - exit rule override

### Logging

- Keep US shadow records under runtime logs for 5 trading days.
- Minimum fields:
  - ticker
  - earnings_date
  - earnings_window
  - reported_eps
  - eps_estimate
  - surprise_sign
  - surprise_strength
  - confidence_tier
  - prompt_applied

### MICRO_PROBE promotion policy

`MICRO_PROBE` is a separate experiment path for order-size-too-small signals, not a regular strategy.

- Keep `MICRO_PROBE` performance separate from normal trades.
- Do not promote to regular sizing before at least `30` filled probe trades or `4` calendar weeks of observation.
- Promotion review must use net performance after fees, max loss, loss streak, and separate probe reports.
- Probe records must keep original order cost, adjusted order cost, oversize ratio, and probe reason.
- Runtime defaults are defensive: `MICRO_PROBE_ENABLED=false`, `MICRO_PROBE_PAPER_ONLY=true`, max `2` daily probes, max `2` open probe positions.
- A probe can only convert an `order_size_too_small` signal when market/mode allow it, entry priority is at least `0.45`, adjusted order is at most `50,000 KRW`, and oversize ratio is at most `2.0`.
- Probe entry/outcome data is stored in `ticker_selection_db.micro_probe_log`; do not mix it into normal strategy promotion decisions.

## Operations Rules (2026-04-22)

Use adaptive operation, not fast strategy rotation.

### Review metrics

For the rolling 2-week review, track these 10 metrics:

1. consensus directional hit rate
2. best analyst - consensus hit gap
3. unanimous mismatch count
4. trade_ready -> signal_fired conversion
5. watch_only missed runup ratio
6. trade_ready forward_3d average
7. ATR-blocked missed runup
8. entry_blackout ratio
9. watch_only blocked ratio
10. continuation average pnl

Storage contract:

- `1~3`: persisted in each session judgment record as `judgment_eval`
- `4~10`: persisted or derivable from:
  - `data/ticker_selection_log.db`
  - `data/ml/decisions.db`
- `1~10 aggregate snapshot`: persisted at `session_close` as `ops_review_snapshot`
  in runtime/live judgment records

### Unanimous override

If all three analysts point to the same directional bucket, final consensus
must not end on the opposite side.

- all bull -> final consensus cannot be bear/flat
- all bear -> final consensus cannot be bull/flat
- all neutral -> final consensus cannot be bull/bear

This is a structural guard, not a tuning rule.

### Claude post-tuning

Claude may only tune bounded runtime controls:

- `momentum_wait_adjust_min`: `-10 .. +10`
- `entry_priority_cutoff_adjust`: `-0.05 .. +0.05`
- `kr_momentum_atr_cap_adjust`: `-0.01 .. +0.02`
- `kr_momentum_atr_cap_high_adjust`: `-0.01 .. +0.02`
- slot bias / replacement aggressiveness: one-step changes only

Claude must not:

- disable hard safety rules
- override unanimous direction guards
- replace market priors with a different strategy philosophy

### Trigger rules

Do not tune every cycle. Tune only at:

- `session_open`
- scheduled intraday tuning windows
- explicit event-driven triggers

Event-driven tuning is allowed when one of these conditions is true:

- recent 10-session `trade_ready -> signal_fired` conversion is too low
  - KR `< 15%`
  - US `< 10%`
- recent 10-session `watch_only missed runup ratio >= 30%`
- recent 10-session `ATR-blocked avg runup >= +4%` with sample `>= 10`
- recent 10-session `consensus hit rate < 45%`
- recent 10-session `best analyst - consensus >= 10%p`
- `unanimous mismatch >= 1`

### Review thresholds

After 2 weeks, modify logic only when thresholds are breached:

- `consensus hit rate < 45%` => review consensus weighting
- `best analyst - consensus >= 10%p` => review aggregation
- `unanimous mismatch >= 1` => immediate fix
- `watch_only missed runup ratio >= 30%` => relax soft promotion rules
- `trade_ready forward_3d avg <= 0%` => review selection quality
- `entry_blackout ratio >= 15%` => reduce late-session churn
- `watch_only blocked ratio >= 25%` => review hard/soft split
- `continuation avg pnl <= -3%` with trades `>= 5` => reduce continuation usage

### Adaptation principle

- Keep strategy families stable.
- Adapt slot mix, wait windows, cutoffs, and ATR handling.
- Apply shrinkage on short windows before changing runtime behavior.
- KR momentum ATR handling:
  - `<= cap`: normal
  - `cap~cap+1%`: size cap `70%`
  - `cap+1~cap+2%`: size cap `50%`
  - `cap+2~high_cap`: size cap `35%`
  - `> high_cap`: block
- Risk-Off exception:
  - default: no new entry
  - exception: `mean_reversion` only
  - constraints: no `HALT`, one position max per market, no same-day reentry,
    no panic index move, size cap `40%`

### Candidate funnel (2026-04-23)

Apply expansion only at the front of the funnel. Do not widen `trade_ready`
or live order concurrency until the new feed proves stable.

- Raw scanner defaults:
  - KR `80`
  - US `80`
- Dynamic universe defaults:
  - KR `40`
  - US `40`
- Claude selection prompt cap:
  - KR starts at `28`
  - US stays at `24` until parse stability is confirmed
- Keep runtime `trade_ready` slot caps unchanged.
- `low_gap_continuation` is not a standalone live strategy yet.
  - Use it only as a promotion/support signal first.

### KR screener policy

For KR, candidate expansion must preserve KOSDAQ visibility.

- Do not merge `KOSPI + KOSDAQ` and then blindly truncate.
- Merge with a minimum KOSDAQ share first, then rank the combined pool.
- Default KOSDAQ minimum share is `35%`.
- Environment overrides:
  - `KR_SCREEN_KOSDAQ_MIN_RATIO`
  - `KR_SCREEN_KOSDAQ_MIN`

### Screener audit

Before evaluating whether candidate expansion worked, persist raw funnel logs.

- KR screener audit path:
  - `logs/screener/YYYYMMDD_KR_screen.jsonl`
- The audit should make these stages inspectable:
  - KOSPI raw
  - KOSDAQ raw
  - merged candidates
  - post-product-filter candidates
- Use these logs before changing caps again.
  - KR prompt cap path: `20 -> 28 -> 32`
  - Only raise the next step after parse stability is acceptable.

### Deferred Follow-ups

These are intentionally deferred. Do not auto-promote them into live behavior
until more data is available or a human explicitly approves the change.

- `brain.json` automatic mutation from lesson scoring
  - keep `lesson_candidates.json` append/score only
  - promoted memory remains approval-based
- automatic hard-block generation from short-window evidence
  - scoring may propose candidates
  - live hard blocks require human review
- `low_gap_continuation` live strategy rollout
  - keep it as observation/promotion support first
  - only move to live after repeated shadow evidence
- strategy-level full replacement
  - avoid swapping strategy philosophy from short windows
  - prefer shrink/observe over full replacement
- history auto-fill expansion for repeated insufficient-history names
  - keep as a later reliability pass, not a live-behavior change

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 프로젝트 철학

- Claude는 시장 판단, 종목 selection, 보유 재량 판단을 맡는다.
- 로직은 진입 조건, 주문 수량/금액, 손절/트레일링, 브로커 동기화, 복구를 맡는다.
- 상태 오염이 의심되면 내부 캐시보다 브로커 truth를 우선한다.
- 짧은 기간 데이터로 전략 철학을 갈아엎지 않는다. 먼저 축소, 관찰, 검증 순서로 간다.

### 핵심 중점 사항 — 데이터 품질과 오염 방어

- `state/brain.json`, `data/ml/decisions.db`, `data/ticker_selection_log.db`는 서로 다른 역할을 가진다.
  - `brain.json`: 정책 메모리
  - `decisions.db`: 의사결정/성과 기록
  - `ticker_selection_log.db`: selection 근거 기록
- 세 파일 중 하나라도 오염되면 Claude 입력 품질이 떨어진다.
- 라이브 복구는 내부 state가 아니라 아래 3가지를 1차 truth로 본다.
  - 브로커 보유 종목
  - 브로커 미체결 주문
  - 가능하면 브로커 체결 내역

### 로그 원칙

- 로그는 디버깅의 1차 수단이다. 사람이 읽는 문구는 한국어로 유지한다.
- 에러/위험/정상 로그는 분리해서 남긴다.
- 깨진 한글, 특수문자, 모지바케는 발견 즉시 복원한다.
- 로그만 보고 원인 추적이 가능해야 한다.

#### 로그 분석 순서

1. `logs/system/` — 전체 상태 전이와 주문 흐름
2. `logs/risk/` — 차단, HALT, 리스크 판정
3. `logs/normal/` — 정상 사이클/진입/청산 흐름
4. `logs/daily_judgment/` — Claude 판단 근거 JSON
5. `logs/screener/` — 후보 풀 축소 단계 점검

## Claude Working Contract

- Claude가 직접 해도 되는 것
  - 시장 모드 판단
  - 종목 watchlist / trade_ready 후보 제안
  - conviction / strategy fit 판단
  - 재량형 HOLD/SELL 의견
- Claude가 직접 하면 안 되는 것
  - 최종 주문 수량 계산
  - 하드 손절 해제
  - 브로커 truth 무시
  - 이벤트 데이터의 장기 메모리 자동 승격
- 튜닝 데이터는 raw 로그를 그대로 주입하지 않는다.
  - `lesson_candidates.json` 같은 후보 규칙으로 점수화한 뒤
  - 저위험 요약만 프롬프트에 넣는다.
  - `brain.json` 자동 수정은 보류한다.

## 나아갈 방향 (Roadmap)

### 현재

- live 기준 브로커 truth 정렬
- 대시보드 live 데이터 소스 정리
- selection / execution / 수익률 계산 QA 강화
- PEAD 입력 품질 개선

### 다음 단계

- KR momentum 축소 관찰
- PEAD surprise shadow 5거래일 검증
- `lesson_candidates` 기반 저위험 프롬프트 요약 정교화
- live dashboard 원장/자산곡선 broker 기준 검증 강화

### 장기

- brain 자동 승격은 승인형 워크플로우가 안정화된 뒤 검토
- 전략 추가보다 입력 품질과 실행 품질 개선 우선

## 코드 작업 원칙

1. 작은 수정으로 큰 사고를 막는다.
2. 라이브 수익률/손실 계산 축은 KR/US를 섞지 않는다.
3. 브로커 불신 상태에서는 신규 진입보다 보호를 우선한다.
4. selection과 execution 문제를 섞지 않는다.
5. 수정 후에는 단계별 검증 + 마지막 통합 QA를 반드시 한다.

### 기능 변경 후 검증 절차

1. 관련 단위 테스트 실행
2. 관련 통합 테스트 실행
3. 수익률/자산곡선/브로커 truth 계산축 점검
4. 로그/대시보드 문구 확인
5. 전체 QA 재실행

### Git 원칙

- runtime 산출물과 정책 메모리는 구분한다.
- 사용자 작업이 섞인 파일은 함부로 되돌리지 않는다.
- 커밋에는 변경 목적이 분명한 파일만 포함한다.

## 실행 명령

```bash
# 봇 실행 — 모의투자
python trading_bot.py --paper

# 봇 실행 — 실거래
python trading_bot.py --live

# 배포 전 구문 검사
python -m py_compile trading_bot.py dashboard/dashboard_server.py claude_memory/brain.py

# 핵심 회귀 테스트
python -m unittest test_trading_improvements.py
python -m unittest test_broker_sync_cash.py

# ML DB 검증
python -m unittest ml.test_full
```

## 아키텍처 — 큰 그림

### 실행 흐름

1. 브로커/시장 상태 수집
2. digest 및 intraday context 생성
3. Claude 시장 판단 / selection
4. 로직 기반 진입 필터링
5. 주문/체결/복구
6. 성과 기록 및 lesson candidate 적재

### 진입 결정 파이프라인

- 후보 풀 생성
- selection raw
- normalized trade_ready
- applied trade_ready
- 전략 신호 검사
- affordability / 리스크 검사
- 주문 생성

### 판단 재사용 로직 — 봇 재시작 시 주의

- 미국장은 KST 자정이 넘어도 active US session date를 유지할 수 있다.
- 재시작 시 당일 판단 재사용은 가능하지만, 브로커 truth 검증이 선행돼야 한다.
- stale legacy 포지션은 holdings/pending 기준으로 정리한다.

### 매수 차단 조건

- 브로커 상태 불신
- affordability fail
- hard risk block
- same-day reentry block
- late-session / blackout 조건
- watch_only 상태 유지

### `_sync_runtime_with_broker()` 주의사항

- 보유 종목 + 미체결 주문 기준으로 stale 포지션을 정리한다.
- 브로커 응답이 불신이면 해당 시장만 quarantine한다.
- quarantine 상태에서는 신규 진입보다 기존 포지션 관리가 우선이다.
- HALT / daily_return은 시장별 baseline 기준으로 계산한다.

### 주요 데이터 흐름

- `state/brain.json`: Claude 정책 메모리
- `state/lesson_candidates.json`: 자동 점수화된 교훈 후보
- `data/ml/decisions.db`: 의사결정/성과 데이터
- `data/ticker_selection_log.db`: selection 로그
- `logs/pead/*.json`: surprise shadow 기록

## TODO / 미완성 작업 목록

### 진행 중

- PEAD surprise shadow 5거래일 관찰
- KR structured earnings source 안정성 확인
- live dashboard broker truth 검증 범위 확대

### 완료

- soft watch 승격 기본 차단
- continuation live 중단, shadow-only 전환
- live HALT/daily_return 시장별 분리
- stale legacy 포지션 정리
- `brain.json` 중복/상충 기록 정규화
- 브레인/대시보드 한글 깨짐 복원

### 예정

- brain 자동 승격 승인형 워크플로우
- history auto-fill 안정화
- low-gap continuation 전략화 여부 재검토

## 재시작 / 장애 복구 절차

1. 브로커 보유 종목 조회
2. 브로커 미체결 주문 조회
3. 가능하면 브로커 체결 내역 조회
4. legacy state와 비교
5. stale 포지션 제거 또는 보호 상태 전환
6. 시장별 HALT / baseline 계산 확인
7. 신규 진입 허용 여부 판단

## 모의투자 → 실거래 전환 체크리스트

- 실거래 키/계좌 확인
- 모의 토큰/상태 제거 여부 확인
- KR/US 최대 주문 금액 확인
- 리스크 한도 확인
- 브로커 동기화 테스트 1회
- 대시보드 live 값과 실제 계좌 대조

## 전략 추가/수정 시

- 전략 추가보다 입력 품질 개선이 먼저다.
- selection 품질, 진입 시점 품질, 로그 설명 가능성을 먼저 본다.
- 새 전략은 shadow로 검증한 뒤 live로 올린다.

## Telegram 운영 명령어

- `/status` 현재 상태
- `/pos` 보유 포지션
- `/review` 보유 종목 재검토
- `/setorder [금액]` 최대 주문 금액 변경
- `/setloss [%]` 일일 손실 한도 변경
- `/trail on|off` 트레일링 on/off
- `/entry on|off` entry priority on/off
- `/brain` 브레인 요약
- `/credit` API 사용량 확인

## 커밋 전 체크리스트

- `git diff --stat`
- `git status --short`
- 단계별 테스트 통과
- 전체 QA 통과
- 수익률 계산축/KR·US 분리 재확인
- 대시보드 live truth 경로 재확인
