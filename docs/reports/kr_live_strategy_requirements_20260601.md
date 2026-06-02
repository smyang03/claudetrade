# KR Live Strategy Requirements

작성일: 2026-06-01
대상: KR live 전략 재설계 및 수익성 회복 제한 실험
상태: 통합 개발 요구서

## 1. 문서 목적

이 문서는 기존 두 문서를 하나로 합친 KR live 적용 기준이다.

- 기존 `kr_strategy_redesign_requirements_20260531.md`: KR 손실 축 차단, Plan A 즉시 진입 차단, PathB 선별, trailing 후보값, shadow 검증, runtime 적용 분리
- 기존 `kr_live_profitability_improvement_dev_requirements_20260601.md`: BUY_READY evidence, guarded reask, momentum wait, Plan A momentum-only 제한 live 실험

통합 후 기준은 다음과 같다.

- 기본 정책은 방어적 구조 재설계다.
- 수익성 회복은 기본 안전장치를 폐기하지 않고 그 위에 제한 live 실험으로 얹는다.
- live 반영은 파일/config 변경만으로 인정하지 않는다.
- live bot 재시작 후 새 effective config snapshot에서 신규 KR key 존재를 확인한 시점부터 적용 검증으로 본다.

## 2. 최종 결론

오늘 KR 분석의 결론은 "수익성 증가 방법이 없다"가 아니다.

정확한 결론:

- 현재 허용된 튜닝 범위 안에서는 즉시 안전하게 수익성을 올릴 단일 레버가 약하다.
- 그러나 KR 로그 기준으로 수익성 개선 후보는 있다.
- 병목은 `ENTRY_BLACKOUT` 단일 원인이 아니라 `evidence 지연/부분 수집 -> Claude 보수 판단 -> trade_ready 부족 -> 전략 신호 no_signal` 순서다.
- 따라서 손실 축은 먼저 막고, 수익성 회복은 `BUY_READY evidence` 기반 guarded live 실험으로 제한적으로 연다.

## 3. 현재 적용 상태

### 3.1 파일 적용 상태

`config/v2_start_config.json` 파일 기준으로 다음 KR redesign key가 존재한다.

```json
"AUTO_TRAIL_PCT_KR": "0.06",
"KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED": "false",
"KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED": "false",
"KR_PLAN_A_ORP_SIGNAL_ENABLED": "false",
"KR_PATHB_BULL_MODE_GATE_ENABLED": "false",
"KR_PATHB_BULL_MODE_GATE_SHADOW": "true",
"KR_PATHB_BULL_MODE_GATE_ALLOWED_MODES": "BULL,MILD_BULL,CAUTIOUS_BULL",
"KR_PATHB_STRATEGY_FILTER_ENABLED": "false",
"KR_PATHB_STRATEGY_FILTER_SHADOW": "true",
"KR_PATHB_STRATEGY_ALLOWLIST": "claude_price,gap_pullback"
```

### 3.2 현재 live runtime 적용 상태

2026-06-01 12:42 read-only 확인 기준:

- live bot PID `29924`는 `2026-05-30 23:31:11`에 시작됐다.
- 현재 live bot이 참조한 snapshot은 `logs/config/effective_config_20260530_233115_live.redacted.json`이다.
- 해당 runtime snapshot에는 위 KR redesign 신규 key가 모두 missing이었다.
- 따라서 2026-06-01에 보정된 파일/config 방향은 현재 실행 중인 live bot에 적용됐다고 판단하지 않는다.

주의:

- `tools/live_preflight.py --mode live --skip-dashboard --json`의 `runtime_snapshot_drift=PASS`만으로 신규 KR key 적용을 증명할 수 없다.
- preflight drift PASS는 현재 프로세스 snapshot과 추적 대상 값의 drift가 없다는 뜻이지, 신규 KR redesign key가 runtime에 로드됐다는 뜻이 아니다.
- 후속 개발로 preflight tracked/critical key 목록에 KR redesign key를 추가한다.

### 3.3 KR 주문 및 PathB 상태

같은 시점의 KR 상태:

- KR `v2_decisions=4`
- KR `v2_path_runs=0`
- KR `ORDER_UNKNOWN=0`
- KR active PathB row 없음
- KR broker positions/open orders/today fills 없음

US 과거 `ORDER_UNKNOWN` row는 별도 remediation 대상이며, 오늘 KR 개선 판단과 섞지 않는다.

### 3.4 brain.json 상태

`state/brain.json`은 dirty 상태이며 `version`, `last_updated`, insight/count/rate 갱신 흔적이 있다.

다만 `state/brain.json`은 runtime truth가 아니라 정책 메모리다. KR 재설계 적용 여부 판단에는 사용하지 않고, 커밋/승격 전 별도 diff 검토 대상으로 둔다.

## 4. 보호 원칙

이번 통합 요구서는 다음 보호 영역을 변경하지 않는다.

- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard
- PathB broker-truth entry fail-closed
- PathB sizing reason split
- zero-holding stale reconcile
- KIS order normalization
- Path A/Path B `RouteDecision` 합류 계약
- broker truth 우선순위와 `_sync_runtime_with_broker()`
- hard stop, loss cap, broker truth, fixed sizing
- `state/brain.json` 자동 수정 또는 자동 승격

보호 영역을 직접 수정해야 하는 경우 별도 작업으로 분리하고 `MD 위반 사항` 섹션을 작성한다.

## 5. 문제 정의

### 5.1 손실 축

KR live 성과 기준:

- Plan A 즉시 진입은 손실 축이다.
- PathB도 전체 확대하면 안 된다.
- 특히 momentum/gap_pullback/opening_range_pullback 태그 손실이 섞여 있다.
- KR은 US처럼 진입 빈도를 늘리는 구조가 아니라, KR 전용으로 좁게 선별해야 한다.

기본 결론:

- 막아야 할 것: Plan A 신호 즉시 진입, 품질 낮은 PathB 태그 진입
- 살릴 것: 검증된 시장 조건에서의 PathB 가격 계획 대기 진입, BUY_READY evidence 기반 제한 실험

### 5.2 수익성 병목

2026-06-01 KR 장중 관측:

- 09:11:53 전까지 intraday evidence가 missing/partial이라 Claude가 WATCH로 가기 쉬웠다.
- evidence 기준 BUY_READY 후보는 있었지만 Claude 최종 BUY_READY는 제한적이었다.
- Claude가 BUY_READY를 냈더라도 Plan A 전략 신호가 `none`으로 끝났다.
- no_signal 사유는 momentum wait window, OR range 과대, gap/pullback 조건 미충족, quarantine/candidate health block 등이었다.

따라서 개선 순서는 다음이 맞다.

```text
관측성 보강
-> guarded reask
-> 현재 계약 안의 momentum wait 35분
-> momentum-only Plan A 제한 live
-> 20/25분 wait, probe, OR/gap 완화는 shadow 이후 별도 승인
```

## 6. Live 적용 기준

live 반영은 다음 순서로만 인정한다.

```text
파일/config 준비
-> live bot 재시작
-> 새 effective config snapshot 생성
-> KR redesign 신규 key 존재 확인
-> preflight 재확인
-> broker truth fresh 확인
-> 제한 live guard 적용
-> 장중 모니터링
```

재시작 전 모니터링은 기존 프로세스 안전성 확인이다. 재시작 후 모니터링부터 이번 KR 재설계 적용 검증으로 본다.

## 7. 요구사항

## R-KR-00. KR redesign truth audit

### 요구사항

KR 재설계 판단의 기준 데이터를 read-only로 재현한다.

필수 구현:

- `tools/analyze_kr_strategy_redesign.py` read-only audit 도구
- KR 성과 truth는 `data/ml/decisions.db::v2_learning_performance`를 기준으로 집계
- PathB metadata truth는 `data/v2_event_store.db::v2_path_runs.plan_json`을 기준으로 분리
- Plan A / PathB / strategy / close reason 별 손익 분리
- `plan_json.strategy` 결측률과 sample row 출력
- trailing 2/4/6/8% 후보 replay 또는 근사 replay
- `state/config/env` 변경 없음

### 장점

- Plan A 손실 축, PathB metadata 결측, trailing 후보값을 같은 truth 기준으로 검증할 수 있다.
- live 설정 변경 전에 read-only로 재현 가능하다.

### 단점

- minute-level 체결 경로, VI, gap fill, broker fill delay를 완전히 모델링하지 못하면 trailing replay는 후보값 선별용으로만 써야 한다.

### 검증

```bash
python tools/analyze_kr_strategy_redesign.py --market KR --runtime-mode live --trail-replay 0.02,0.04,0.06,0.08 --json
python -m pytest tests/test_analyze_kr_strategy_redesign.py -q
```

## R-KR-01. KR Plan A 즉시 진입 차단

### 요구사항

KR Plan A `momentum`, `gap_pullback`, `opening_range_pullback` 신호가 주문으로 바로 이어지지 않도록 한다.

기본값:

```env
KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED=false
KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED=false
KR_PLAN_A_ORP_SIGNAL_ENABLED=false
```

이 차단은 selection pool이나 Claude prompt를 줄이는 기능이 아니다. Plan A 전략 신호가 실제 주문으로 이어지기 직전만 차단한다.

### 장점

- 손실 축이었던 KR Plan A 즉시 진입을 우선 차단한다.
- Claude selection과 PathB wait 등록을 오염시키지 않는다.
- US 전략에 영향을 주지 않는다.

### 단점

- 오늘처럼 BUY_READY evidence가 있었지만 주문이 없던 날에는 수익 기회도 줄어들 수 있다.
- 따라서 momentum-only 제한 live 실험을 후속 Phase로 둔다.

### 구현 상태

`TradingBot._live_plan_a_signal_allowed()` 훅과 dispatch 차단 경로를 기준으로 검증한다.

## R-KR-02. KR trailing 후보값 0.06

### 요구사항

KR trailing 2%는 KR 종목의 일반 noise 대비 타이트하므로 `AUTO_TRAIL_PCT_KR=0.06`을 후보값으로 둔다.

주의:

- 파일 기준 후보값 반영이지 runtime 적용 또는 수익성 검증 완료가 아니다.
- 오늘처럼 KR fill=0인 상황에서는 즉시 수익성 레버가 아니다.
- 실제 trailing close 표본 또는 replay로 검증한다.

### 장점

- MFE 대비 조기 청산을 줄일 수 있다.
- KR 종목 noise 폭을 더 현실적으로 반영한다.

### 단점

- 반락 후 손실 전환이 커질 수 있다.
- gap fill, VI, 체결 지연은 trailing 폭만으로 해결되지 않는다.
- execution/risk 변경이므로 selection 변경과 한 패치에서 섞지 않는다.

## R-KR-03. KR PathB market-mode shadow gate

### 요구사항

PathB 전체 확대가 아니라 시장 모드가 맞는 경우만 선별한다.

기본값:

```env
KR_PATHB_BULL_MODE_GATE_ENABLED=false
KR_PATHB_BULL_MODE_GATE_SHADOW=true
KR_PATHB_BULL_MODE_GATE_ALLOWED_MODES=BULL,MILD_BULL,CAUTIOUS_BULL
```

### 장점

- look-ahead 없이 market mode block 효과를 shadow로 측정한다.
- live block 전 winner 차단률과 loss 절감 효과를 비교할 수 있다.

### 단점

- live 차단 전까지 실제 주문 감소 효과는 없다.
- 시장 모드 입력의 `known_at` 오염을 주의해야 한다.

## R-KR-04. KR PathB strategy allowlist shadow

### 요구사항

PathB strategy metadata가 보존되는지 확인하고, 결측이 남으면 live filter를 켜지 않는다.

기본값:

```env
KR_PATHB_STRATEGY_FILTER_ENABLED=false
KR_PATHB_STRATEGY_FILTER_SHADOW=true
KR_PATHB_STRATEGY_ALLOWLIST=claude_price,gap_pullback
```

### 장점

- PathB 전체 확대를 막고, 상대적으로 품질 좋은 path만 선별할 수 있다.
- metadata 결측으로 인한 잘못된 live block을 방지한다.

### 단점

- 결측 metadata가 많으면 실제 live 전환이 늦어진다.
- route 표시와 실제 wait 등록이 어긋나지 않도록 `_candidate_action_routes`까지 일관되게 처리해야 한다.

## R-KR-05. Healthy pullback shadow

### 요구사항

negative context로 막힌 PULLBACK_WAIT 중 살릴 후보를 shadow로 식별한다.

이 기능은 주문 확대 장치가 아니라 관측성 장치다.

### 장점

- candidate health block이 과도했는지 확인할 수 있다.
- 바로 주문하지 않고 missed opportunity 데이터를 쌓을 수 있다.

### 단점

- shadow 데이터가 충분히 쌓이기 전 live 전환하면 오판 위험이 있다.

## R-KR-06. Missed BUY_READY evidence 분석기

### 요구사항

`tools/analyze_kr_live_replay.py`에 missed opportunity 집계를 추가한다.

집계 대상:

- `evidence_action_ceiling=BUY_READY`
- Claude candidate action이 WATCH 또는 PULLBACK_WAIT
- final action/route
- no_signal detail
- candidate health block 여부
- affordability 여부
- 가능하면 +5/+15/+30/+60분 후속 수익률

### 장점

- Claude가 WATCH로 누른 후보가 실제 missed alpha였는지 검증할 수 있다.
- 주문 동작 변경 없이 read-only로 수행 가능하다.

### 단점

- 즉시 수익성 개선은 아니다.
- forward return 계산을 위한 가격 로그 join 품질이 필요하다.

## R-KR-06A. PlanA no_signal shadow 로그 생성 보장

### 요구사항

`PlanA.buy` 후보였지만 strategy signal이 `none`으로 끝난 KR 후보를 별도 shadow 로그에 남긴다.

필수 로그:

- `logs/funnel/kr_plan_a_no_signal_pathb_shadow_<date>_KR.jsonl`

필수 필드:

- ticker
- price
- mode
- candidate action
- final route
- evidence action ceiling
- evidence data state
- strategy order
- no_signal detail
- rejection reason
- volume state
- elapsed minute

현재 관측 이슈:

- 2026-06-01 모니터링에서 `kr_plan_a_no_signal_pathb_shadow` 로그가 생성되지 않았다.
- 이 로그가 없으면 "Claude/route는 매수 후보였는데 전략 신호가 안 난 케이스"를 사후 분석할 수 없다.
- R-KR-06 missed BUY_READY 분석과 R-KR-09 momentum shadow의 근거 데이터이므로 Phase 2에서 우선 보강한다.

코드레벨 확인 지점:

- `trading_bot.py`의 KR `signal_fired == false` / `reason=no_signal` 분기에서 `_log_kr_plan_a_no_signal_pathb_shadow()`가 호출되는지 확인한다.
- 발화 조건은 "KR, PlanA.buy 후보, strategy signal none"이어야 하며, PathB live 등록 여부나 PathB wait 등록 성공 여부에 의존하면 안 된다.
- `_write_funnel_event("kr_plan_a_no_signal_pathb_shadow", "KR", payload)`가 실제 파일 `logs/funnel/kr_plan_a_no_signal_pathb_shadow_<date>_KR.jsonl`을 생성하는지 fixture로 고정한다.
- 로그가 다시 생성되지 않으면 `candidate route가 PlanA.buy에서 WATCH로 사라진 것인지`, `no_signal 분기에 도달하지 않은 것인지`, `파일 writer 조건이 막은 것인지`를 분리해 진단한다.

### 장점

- BUY_READY 후보가 주문으로 이어지지 못한 최종 병목을 별도 분석할 수 있다.
- 주문 동작 변경 없이 관측성만 보강한다.

### 단점

- 로그량이 증가한다.
- 조건이 과도하게 좁으면 다시 파일이 생성되지 않을 수 있으므로 테스트 fixture로 생성 조건을 고정해야 한다.

### 검증

```bash
python -m pytest tests/test_analyze_kr_live_replay.py -q
python -m pytest tests/test_kr_plan_a_no_signal_shadow.py -q
python -m py_compile trading_bot.py tools/analyze_kr_live_replay.py
```

## R-KR-07. BUY_READY evidence guarded reask

### 요구사항

Claude가 WATCH로 판단했지만 evidence가 BUY_READY인 후보를 제한적으로 재질문한다.

허용 조건:

- market = KR
- live mode
- evidence data state = confirmed 또는 minute_complete
- evidence action ceiling = BUY_READY
- Claude final action = WATCH
- candidate health block 없음
- quarantine 없음
- hard block 없음
- entry blackout 종료
- broker truth fresh
- 1주 매수 가능 또는 fixed budget 내 매수 가능

호출 제한:

- cycle당 최대 2개
- 세션당 최대 8개
- ticker별 cooldown 20분
- provider error 또는 timeout 발생 시 해당 cycle reask 중단
- reask 결과가 다시 WATCH면 같은 ticker는 cooldown 동안 재질문 금지

KR/US 격리 조건:

- reask는 KR 전용 기능이다.
- US 후보는 `evidence_action_ceiling=BUY_READY`이고 Claude final action이 WATCH여도 reask 대상이 되면 안 된다.
- 구현 테스트에는 "US BUY_READY evidence 후보에 대해 reask가 발생하지 않는다"는 assertion을 반드시 포함한다.

### 장점

- 주문 규칙 완화 없이 selection 품질만 개선한다.
- AI 역할 계약상 후보 제안/conviction 판단 범위 안에 있다.

### 단점

- Claude 호출량과 토큰 사용량이 증가한다.
- 장초 latency가 악화될 수 있다.
- cooldown guard가 없으면 호출 폭증 가능성이 있다.

## R-KR-08. KR momentum wait 35분 live

### 요구사항

현재 Claude tuning contract 안에서 KR momentum wait를 45분에서 35분으로 낮추는 경로를 검토한다.

우선순위:

1. Claude runtime override `momentum_wait_adjust_min=-10`
2. 필요 시 KR 전용 base wait 분리

적용 방식 주의:

- `momentum_wait_adjust_min=-10`은 Claude가 세션 tuning 응답에 포함해야 runtime override로 적용된다.
- 운영자가 env/config에 `momentum_wait_adjust_min=-10`을 직접 쓰는 방식으로 적용되는 값이 아니다.
- config/env로 고정 적용하려면 별도 KR 전용 base wait 설정을 새로 설계해야 하며, 이 경우 R-KR-08의 2번 경로로 본다.

### 장점

- 현재 허용 범위 안에서 KR 장초 기회창에 조금 더 가까워진다.
- 20~25분 변경보다 안전하다.

### 단점

- 35분도 KR 5~30분 기회창에는 늦을 수 있다.
- 20~25분은 별도 정책/코드 변경이며 shadow가 먼저다.

## R-KR-09. KR momentum 20/25분 shadow

### 요구사항

live order 동작은 바꾸지 않고 no_signal 로그에 momentum wait shadow 진단을 추가한다.

필드 예:

- `momentum_wait_effective_min`
- `would_momentum_ready_at_20`
- `would_momentum_ready_at_25`
- `would_momentum_ready_at_35`
- `momentum_signal_at_20`
- `momentum_signal_at_25`
- `momentum_signal_at_35`
- `candidate_action`
- `evidence_action_ceiling`

### 장점

- 20~25분 live 정책 변경의 근거를 확보할 수 있다.

### 단점

- 로그량이 증가한다.
- 진단이 실제 strategy signal과 동일한 입력을 써야 한다.

## R-KR-10. KR Plan A momentum-only 제한 live

### 요구사항

기본 방어 상태는 유지한다.

```env
KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED=false
KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED=false
KR_PLAN_A_ORP_SIGNAL_ENABLED=false
```

다만 Phase B 제한 실험으로 momentum만 열 수 있다.

```env
KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED=true
KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED=false
KR_PLAN_A_ORP_SIGNAL_ENABLED=false
```

이 항목은 R-KR-01 Plan A 차단을 폐기하지 않는다. R-KR-01은 기본 방어 상태이며, R-KR-10은 그 위에 얹는 제한 live 실험이다.

적용 조건:

- live bot 재시작 후 새 effective config snapshot에서 KR redesign key 존재 확인
- 운영자 별도 승인
- evidence confirmed
- Claude BUY_READY 또는 guarded reask BUY_READY
- strategy momentum signal fired
- risk/affordability 통과
- broker truth fresh
- candidate quarantine 없음
- entry blackout 종료
- ticker/cycle/session cooldown과 호출량 guard 적용
- gap_pullback/ORP는 계속 false 또는 shadow 유지

### 장점

- 시스템을 더 신뢰하는 방향으로 실제 live 진입 가능성을 높인다.
- gap_pullback/ORP보다 조건이 명확하다.
- 기존 `_live_plan_a_signal_allowed()` 훅을 활용할 수 있다.

### 단점

- 실제 주문 가능성이 증가하므로 손실 노출도 증가한다.
- runtime snapshot 확인 없이 config만 보고 적용됐다고 판단하면 적용 전/후 성과가 섞인다.

## R-KR-11. Session open / evidence pipeline 개선

### 요구사항

장초 evidence complete 시점을 앞당긴다.

KR 격리 원칙:

- 변경은 KR evidence 경로에만 적용한다.
- `trading_bot.py`와 `runtime/post_open_features.py`는 KR/US 공통 파일이므로, US evidence path와 US post-open feature 생성 결과가 바뀌지 않는 것을 테스트로 확인한다.
- 공통 helper를 수정해야 할 경우 market guard를 명시하고, US fixture 회귀 테스트를 함께 추가한다.

개선 후보:

- top candidate 우선 prefetch
- phase별 target limit 조정
- KIS timeout/retry 결과를 ticker별로 더 명확히 기록
- provider timeout ticker는 다음 cycle에서 우선 재시도
- partial evidence fail-open/fail-closed 기준 replay 검증

### 장점

- 주문/risk 완화 없이 기회 손실을 줄일 수 있다.
- Claude 판단 품질도 같이 좋아질 수 있다.

### 단점

- KIS rate limit에 민감하다.
- 병렬화가 과하면 timeout과 partial data가 늘 수 있다.
- stale evidence 오염을 주의해야 한다.

## R-KR-12. Gap pullback / OR threshold 완화는 후순위

### 요구사항

당장 live 완화하지 않는다. KR-only shadow threshold 비교를 먼저 구현한다.

shadow 후보:

- OR range cap 완화
- gap lower bound 완화
- pullback recovery threshold 완화
- high volatility candidate exclusion 유지 여부

### 장점

- 수익성 후보일 수 있다.

### 단점

- 가장 위험한 레버다.
- 고변동 추격 매수가 늘 수 있다.
- KR/US 공유 전략 파일을 건드리면 US 성과에도 영향을 줄 수 있다.

## R-KR-13. Preflight tracked/critical key 보강

### 요구사항

`tools/live_preflight.py --mode live --skip-dashboard --json`가 신규 live 운영 key의 runtime 적용 여부를 검증하도록 tracked/critical key 목록을 보강한다.

필수 tracked key:

- `AUTO_TRAIL_PCT_KR`
- `KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED`
- `KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED`
- `KR_PLAN_A_ORP_SIGNAL_ENABLED`
- `KR_PATHB_BULL_MODE_GATE_ENABLED`
- `KR_PATHB_BULL_MODE_GATE_SHADOW`
- `KR_PATHB_BULL_MODE_GATE_ALLOWED_MODES`
- `KR_PATHB_STRATEGY_FILTER_ENABLED`
- `KR_PATHB_STRATEGY_FILTER_SHADOW`
- `KR_PATHB_STRATEGY_ALLOWLIST`

수용 기준:

- 파일/env effective config에는 key가 있는데 live runtime snapshot에 없으면 `runtime_snapshot_drift=PASS`만으로 적용 완료로 보지 않는다.
- 신규 live gate key가 runtime snapshot에서 missing이면 action-required warning 또는 critical drift로 표시한다.
- KR key뿐 아니라 향후 `US_MOMENTUM_LIVE_ENABLED` 같은 US live gate key도 같은 tracked-key 규칙에 포함할 수 있게 목록을 확장 가능한 구조로 둔다.

### 장점

- 재시작 전/후 적용 상태 혼동을 줄인다.
- 신규 live key가 snapshot에 빠져도 preflight가 감지한다.

### 단점

- tracked key 목록 관리가 필요하다.
- optional/shadow key와 critical/live-blocking key의 severity를 구분해야 한다.

### 검증

```bash
python -m pytest tests/test_live_preflight.py -q
python tools/live_preflight.py --mode live --skip-dashboard --json
```

## 8. 개발 순서

### Phase 0. Truth audit

- R-KR-00 KR redesign truth audit
- KR 성과 truth를 `data/ml/decisions.db::v2_learning_performance`로 재현
- PathB metadata truth를 `data/v2_event_store.db::v2_path_runs.plan_json`으로 분리
- Plan A/PathB/strategy별 손익 분리
- `plan_json.strategy` 결측률 확인
- trailing 2/4/6/8% 후보 replay
- state/config/env 변경 없음

### Phase 1. 기본 방어 적용

Phase 1은 같은 운영 묶음이지만 커밋/검증 단위는 분리한다. selection 품질 문제와 execution/risk 문제를 한 패치에서 섞지 않는다.

#### Phase 1a. Selection 품질 방어

- R-KR-01 Plan A 즉시 진입 차단
- R-KR-03 PathB market-mode shadow gate
- R-KR-04 strategy allowlist shadow
- R-KR-05 healthy pullback shadow

별도 커밋 예:

```text
feat: KR selection 방어 및 PathB shadow gate 정리
```

#### Phase 1b. Execution/risk 후보값

- R-KR-02 trailing 후보값 파일 반영 및 runtime 적용 분리

별도 커밋 예:

```text
chore: KR trailing 후보값 적용
```

목표:

- 손실 축을 우선 막는다.
- PathB 전체 확대를 피한다.
- live block은 shadow/QA 이후 결정한다.
- Plan A 차단과 trailing 후보값은 서로 다른 패치와 테스트 결과로 관리한다.

### Phase 2. 관측성 보강

- R-KR-06 missed BUY_READY evidence 분석기
- R-KR-06A PlanA no_signal shadow 로그 생성 보장
- R-KR-09 momentum 20/25/35분 shadow
- R-KR-13 preflight tracked/critical key 보강

목표:

- 수익성 후보를 계량화한다.
- runtime 적용 전/후 혼동을 막는다.

### Phase 3. 제한 live 실험

전제:

- live bot 재시작 완료
- 새 effective config snapshot에서 신규 KR key 존재 확인
- preflight 재확인
- broker truth fresh
- 운영자 승인

실험 후보:

1. KR momentum wait 35분
2. BUY_READY evidence guarded reask
3. KR Plan A momentum-only 제한 live

목표:

- 현재 계약 안에서 시스템을 더 신뢰한다.
- Claude 판단을 한 번 더 활용하되 호출량 guard를 둔다.
- gap_pullback/ORP는 아직 열지 않는다.

### Phase 4. 공격적 후보 shadow

- 20~25분 momentum wait 정책 변경 검토
- probe-only early lane 검토
- gap_pullback/OR threshold KR-only shadow

목표:

- 더 큰 수익성 레버를 검증한다.
- 최소 5거래일 또는 충분한 후보 표본 이후 live 전환을 결정한다.

## 9. 1차 Live Rollout 후보

이는 즉시 적용안이 아니라 Phase 3 제한 실험 후보다.

적용 전 필수:

- live bot 재시작
- 새 effective config snapshot에서 신규 KR key 존재 확인
- 운영자 승인
- reask/cooldown guard 준비
- broker truth fresh 확인

후보 조합:

- KR momentum wait 35분
- BUY_READY evidence guarded reask
- KR Plan A momentum-only 제한 허용
- KR gap_pullback/ORP 계속 차단
- early soft gate 유지
- fixed order budget 유지
- hard stop 유지
- broker truth fail-closed 유지

## 10. 중단 조건

다음 중 하나가 발생하면 해당 live 실험을 즉시 중단한다.

- Claude reask 호출량이 세션 cap 초과
- reask timeout/provider error 연속 발생
- KR 신규 진입 후 당일 loss cap 연속 발생
- broker truth stale/error 상태에서 신규 진입 시도 발견
- ORDER_UNKNOWN 또는 local/broker mismatch 발생
- 같은 ticker 반복 reask가 cooldown을 우회
- effective config snapshot 없이 파일 기준만으로 적용 판단

## 11. 테스트 계획

### 문서/분석 도구

```bash
python tools/analyze_kr_strategy_redesign.py --market KR --runtime-mode live --json
python tools/analyze_kr_live_replay.py --date 20260601 --market KR --runtime-mode live --json
python -m pytest tests/test_analyze_kr_strategy_redesign.py tests/test_analyze_kr_live_replay.py -q
```

### Plan A / routing / shadow

```bash
python -m pytest tests/test_kr_plan_a_no_signal_shadow.py -q
python -m pytest tests/test_action_routing.py tests/test_candidate_action_live_mapping.py -q
python -m pytest tests/test_trading_decision_contract_improvements.py -q
python -m py_compile trading_bot.py runtime/action_routing.py
```

### Evidence / post-open

```bash
python -m pytest tests/test_post_open_features.py tests/test_trading_bot_intraday_evidence.py -q
python -m py_compile trading_bot.py runtime/post_open_features.py
```

### reask guard

```bash
python -m pytest tests/test_kr_buy_ready_reask.py -q
python -m pytest tests/test_claude_quality_contracts.py -q
python -m pytest tests/test_auto_sell_claude_gate.py::AutoSellClaudeGateTests::test_pathb_loss_cap_hold_respects_reask_cooldown -q
python -m py_compile trading_bot.py
```

필수 assertion:

- KR `evidence_action_ceiling=BUY_READY` + Claude WATCH + guard 통과 후보는 reask 후보가 될 수 있다.
- US `evidence_action_ceiling=BUY_READY` + Claude WATCH 후보는 reask 후보가 되면 안 된다.
- reask guard가 PathB `AUTO_SELL_REVIEW` cooldown guard를 건드리지 않는다.

### KR evidence pipeline 격리

```bash
python -m pytest tests/test_post_open_features.py tests/test_trading_bot_intraday_evidence.py -q
python -m py_compile trading_bot.py runtime/post_open_features.py
```

필수 assertion:

- KR evidence prefetch/coverage 개선이 KR 경로에만 적용된다.
- US evidence path와 US post-open feature row schema/count가 변경되지 않는다.

### live 설정 변경 전

```bash
python tools/live_preflight.py --mode live --skip-dashboard --json
python -m py_compile trading_bot.py dashboard/dashboard_server.py claude_memory/brain.py
```

필수 assertion:

- effective config에 존재하는 KR redesign key가 runtime snapshot에서 missing이면 preflight가 action-required warning 또는 critical drift로 표시한다.
- tracked live gate 목록은 KR key에 한정하지 않고 US live gate key 추가도 가능한 구조다.

### 오염 확인

```bash
git diff -- state/brain.json
git diff -- config/v2_start_config.json
```

## 12. 최종 운영 판단

시스템을 더 믿는 방향으로 가는 것은 가능하다.

다만 믿어야 할 대상은 Claude 단독이 아니라 다음 전체 체인이다.

```text
fresh evidence
-> Claude BUY_READY 또는 guarded reask BUY_READY
-> KR momentum signal
-> affordability/risk gate
-> broker truth
-> order submit
```

기본 정책은 방어적 구조 재설계다. 수익성 회복은 그 기본 정책을 폐기하지 않고 Phase 3 제한 live 실험으로 연다.

1차 live 전환 후보는 `BUY_READY evidence guarded reask + KR momentum 35분 + momentum-only Plan A 제한 허용` 조합이다. 단, live bot 재시작 후 새 effective config snapshot에서 신규 KR key 존재를 확인하고, 운영자 승인과 호출량/cooldown guard가 갖춰진 뒤에만 적용한다.
