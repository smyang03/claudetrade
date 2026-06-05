# 장전/장 시작 후 장판단 운영 개선 요구서

작성일: 2026-06-05
상태: 최종 재검토 완료 요구서
대상: KR/US 자동매매 장전 판단과 장 시작 후 판단의 권한 분리

## 1. 결론

최종 운영 원칙은 다음과 같이 고정한다.

```text
장전 판단(preopen_watch) = 후보/리스크 준비 전용, 주문 권한 없음
장 시작 후 판단(opening_confirm) = 첫 실행 가능 장판단, 기본 T+5분
장중 판단(intraday_live) = 실행 판단 갱신
장마감 전 판단(pre_close/carry) = 이월/청산 판단, 신규 매수 권한 없음
```

이 문서에서 "장후"는 장 마감 후가 아니라 "장 시작 후" 판단을 의미한다. 장 마감 후 사후 분석, post-close 리포트, 익일 준비 판단은 별도 요구서 범위로 분리한다.

## 2. 목적

현재 시스템은 장전 후보, Claude selection, 시장 모드 판단, Path A/Path B 진입, 보유 종목 매도 판단이 연결되어 있다. 따라서 장전 판단과 장 시작 후 판단을 같은 수준의 "장판단"으로 취급하면 다음 위험이 생긴다.

- 장전 데이터만으로 신규 매수 또는 PathB wait plan이 생성될 위험
- 본장 유동성 확인 전 보유 종목 SELL이 확정될 위험
- 장전 후보 성과와 장중 실행 성과가 섞여 원인 분석이 어려워질 위험
- KR/US의 서로 다른 장전 후보 품질을 같은 정책으로 처리할 위험
- market mode 판단이 hard stop, loss cap, profit ladder 같은 보호 청산을 약화할 위험

이번 요구서는 장전 판단과 장 시작 후 판단의 권한을 코드 레벨 계약으로 분리하고, 매수/매도에 미치는 영향을 명확히 정의한다.

## 3. 범위와 비범위

### 범위

- 장전 판단, 장 시작 후 판단, 장중 판단, 장마감 전 판단의 역할 정의
- 각 판단이 신규 매수, PathA, PathB, 보유 종목 매도에 미치는 권한 정의
- 실패 시 fail-closed 정책 정의
- 로그, 대시보드, 저장 judgment의 phase/authority 가시성 요구
- 구현 시 필요한 테스트와 검증 명령 정의

### 비범위

이번 요구서는 아래 변경을 승인하지 않는다.

- `.env*`, `config/v2_start_config.json`의 운영값 즉시 변경
- 주문금액, max position, confidence, slippage cap, cooldown 변경
- PathB profit ladder, pre-close 청산, hold advisor 보호 정책 변경
- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard 완화
- broker truth fail-closed 완화
- PathB sizing reason split 변경
- hard stop, loss cap, zero-holding reconcile, KIS order normalization 변경
- `state/brain.json` 자동 변경 또는 정책 메모리 자동 승격
- 장전 신호만으로 live 매수/매도 허용

보호 영역을 피할 수 없이 건드리는 구현이 필요해지면 AGENTS.md의 `MD 위반 사항` 보고 형식을 따른다.

## 4. 현재 구조 검토 요약

검토 기준은 현재 코드와 최근 운영 로그다.

- `preopen_watch` phase는 장전 후보 준비 단계로 존재한다.
- preopen selection은 watch-only로 강제되며 `trade_ready=[]`가 기대 동작이다.
- 저장된 preopen judgment는 장 시작 후 refresh 시점이 지나면 intraday/opening 판단으로 갱신되어야 한다.
- 신규 진입 gate는 실행 가능한 phase만 통과해야 한다.
- pre-session sell queue는 본장 전 직접 실행하지 않고 장 시작 후 재확인을 거쳐야 한다.
- PathB는 기존 wait plan과 보유 포지션 청산 경로가 독립적으로 움직일 수 있으므로 preopen 데이터가 PathB 신규 진입 플랜으로 새지 않도록 명시 guard가 필요하다.
- 현재 opening refresh 기본값은 KR/US 모두 3분이다. T+5 요구는 현재보다 2분 늦추는 보수화 제안이며, 운영자 승인 전 실제 live 설정을 변경하지 않는다.
- 장 시작 후 SELL 재확인 구조는 `pending_next_open_sell`, `pending_next_open_sell_recheck_status`, `pending_next_open_sell_recheck_phase`, `pending_next_open_sell_recheck_session` 계열 필드가 이미 존재한다. 새 DB schema를 전제로 하지 않고 기존 포지션 상태 필드를 우선 사용한다.
- 현재 preopen demotion은 `trade_ready`, `price_targets` 등 실행 필드를 지우지만, 구현 단계에서는 `_pathb_wait_tickers`, `_pathb_price_targets`, `_pathb_wait_origins`, `_pathb_registration_scope` 같은 PathB 등록 필드가 demotion 전 등록으로 새지 않는지 별도 확인해야 한다.
- 코드상 `opening_confirm` phase label은 정규장 시작 직후부터 opening window 동안 반환될 수 있다. 이 요구서의 "T+5 첫 실행 판단"은 phase label 자체가 아니라, T+5 refresh barrier를 통과해 fresh judgment가 저장된 뒤에만 `execution_authority`를 부여한다는 뜻이다.

현재 구조는 큰 방향에서 "장전 watch-only, 장후 실행 판단"에 맞지만, 운영 요구서에는 권한과 실패 정책이 더 명시적으로 필요하다.

## 5. 용어와 권한 모델

| Phase | 권한명 | 설명 | 주문 권한 |
| --- | --- | --- | --- |
| `preopen_watch` | `NONE` | 장전 후보, 뉴스, 프리마켓/전일 맥락 정리 | 없음 |
| `opening_confirm` | `BUY_SELL_RECHECK` | 장 시작 후 첫 실행 가능 판단. 기본 T+5분 refresh 이후 | 제한적 있음 |
| `intraday_live` | `BUY_SELL_LIVE` | 장중 판단 갱신 | 있음 |
| `intraday_live_unconfirmed` | `NO_NEW_BUY` | live context 미확정 상태 | 신규 매수 없음 |
| `pre_close_carry` | `SELL_CARRY_ONLY` | 장마감 전 이월/청산 판단. 현재 코드 상수라기보다 PathB/hold-advisor stage 개념 | 신규 매수 없음 |

저장 judgment에는 phase와 별개로 `execution_authority` 또는 동등한 의미의 authority 필드를 남기는 것을 목표로 한다. phase만으로도 동작은 가능하지만, 로그와 대시보드에서 사람이 확인하기 어렵다. 특히 `phase=opening_confirm`이어도 T+5 refresh barrier와 live context 조건을 통과하지 못했으면 `execution_authority=NONE` 또는 동등한 비실행 상태여야 한다.

## 6. 1차 요구사항

### REQ-01. 장전 판단 명칭 분리

장전 판단은 화면과 로그에서 "장판단"이 아니라 "장전 후보판단" 또는 `PREOPEN_WATCH`로 표시한다.

수용 기준:

- 장전 phase의 표시명이 실행 가능한 장판단처럼 보이지 않는다.
- 운영자가 장전 판단만 보고 주문 가능 상태로 오해하지 않는다.
- 저장 judgment에 `phase=preopen_watch`가 남는다.

### REQ-02. 장전 판단 watch-only 강제

`preopen_watch`에서는 아래 값이 항상 비실행 상태여야 한다.

- `trade_ready=[]`
- 신규 매수 주문 없음
- PathA 전략 신호 주문 없음
- 신규 PathB wait plan 등록 없음
- 직접 SELL queue 실행 없음

수용 기준:

- Claude가 장전 응답에서 `trade_ready`, `price_targets`, candidate action을 반환해도 적용 결과는 watch-only다.
- 장전 판단으로 생성된 후보는 장 시작 후 판단의 보조 입력으로만 사용된다.

### REQ-03. 장 시작 후 첫 실행 판단은 T+5분 기준

첫 실행 가능 장판단은 정규장 시작 후 5분을 기본 기준으로 한다.

현재 코드 기본값은 `KR_OPENING_JUDGMENT_REFRESH_MIN=3`, `US_OPENING_JUDGMENT_REFRESH_MIN=3`이다. 따라서 이 요구는 매수 허용 가능 시점을 앞당기는 변경이 아니라, 장초반 노이즈를 줄이기 위해 3분에서 5분으로 늦추는 변경이다. 실제 운영값 변경은 Phase 2에서 운영자 승인 후 별도 적용한다.

주의: `opening_confirm` phase label은 장 시작 직후부터 존재할 수 있다. 주문 권한은 label이 아니라 "T+5 이후 fresh opening judgment가 성공했고 live context가 포함되었는가"로 판단한다.

수용 기준:

- `opening_confirm`은 live index, 현재가, 체결/호가, 장초반 방향을 포함한다.
- 장전 digest와 후보는 참고 정보로만 포함한다.
- T+5분 판단 실패 시 신규 매수는 fail-closed 한다.
- 기존 보호 청산은 T+5분 판단 실패와 무관하게 유지된다.

### REQ-04. 장중 판단은 opening 판단의 갱신이다

`intraday_live`는 별도 철학의 판단이 아니라 `opening_confirm`의 갱신이다.

수용 기준:

- 장중 판단은 market mode, new buy permission, max gross exposure, watch/trade_ready를 갱신할 수 있다.
- 장중 판단 악화는 신규 매수 차단 또는 축소로 연결될 수 있다.
- 장중 판단 개선은 재스크리닝 또는 trade_ready 재평가로 연결될 수 있다.

### REQ-05. 신규 매수 gate는 실행 가능 phase만 통과

신규 매수는 `opening_confirm` 또는 `intraday_live`에서만 가능하다.

수용 기준:

- `preopen_watch`는 신규 매수 gate를 통과하지 않는다.
- `intraday_live_unconfirmed`는 별도 승인 플래그 없이는 신규 매수를 통과하지 않는다.
- broker truth 불신, quarantine, affordability fail, hard risk block, blackout은 market judgment보다 우선한다.

### REQ-06. PathB 신규 진입 플랜은 preopen에서 생성하지 않는다

PathB wait plan 등록은 장 시작 후 실행 가능 판단에서만 허용한다.

수용 기준:

- `preopen_watch` selection meta에 PathB 관련 필드가 들어와도 등록하지 않는다.
- preopen에서 발견된 PathB 후보는 T+5 판단의 보조 후보로 carry할 수 있지만, carry 데이터는 watchlist/universe/risk flag 수준이어야 한다.
- `_pathb_wait_tickers`, `_pathb_price_targets`, `_pathb_wait_origins`, `_pathb_registration_scope`는 preopen phase에서 실행 등록 입력으로 사용하지 않는다.
- 이미 존재하는 PathB run의 exit, hard stop, loss cap, profit ladder는 이 요구로 중단하지 않는다.
- PathB broker-truth entry fail-closed는 완화하지 않는다.

### REQ-07. 장전 보유 종목 SELL 의견은 재확인 플래그만 허용

장전에는 보유 종목에 대한 위험 의견을 만들 수 있지만 직접 SELL 주문 또는 확정 sell queue 실행은 금지한다.

수용 기준:

- 장전 SELL 후보는 기존 `pending_next_open_sell=True`와 `pending_next_open_sell_recheck_status=needs_opening_recheck` 또는 동등한 재확인 상태로 남는다.
- 신규 DB schema나 별도 장전 SELL queue를 먼저 만들지 않고, 기존 포지션 상태 필드를 우선 사용한다.
- 장 시작 후 T+5 판단에서 broker holding, open order, current price를 확인한 뒤 SELL/HOLD를 결정한다.
- 장전 market mode bearish만으로 일괄 매도하지 않는다.

### REQ-08. 매도 우선순위는 보호 청산을 우선한다

보유 종목 매도에서 시장 판단은 보조 context다. hard stop, loss cap, profit ladder, pre-close 보호 경로는 우선권을 유지한다.

수용 기준:

- market judgment가 hard stop 또는 loss cap을 해제하지 않는다.
- market judgment가 profit ladder floor를 완화하지 않는다.
- HOLD/SELL advisor 판단은 포지션별 근거를 요구한다.
- PathB AUTO_SELL_REVIEW cooldown guard는 유지한다.

### REQ-09. 판단 실패 시 fail-closed 정책

판단 실패는 "안전한 무동작"으로 처리한다.

수용 기준:

- T+5 opening judgment 실패: 신규 매수 금지
- live index/context 불충분: 신규 매수 금지 또는 unconfirmed phase 유지
- Claude selection 실패: 기존 포지션 보호 경로는 계속 작동
- broker truth stale/error: 신규 진입 금지
- sell 판단 실패: hard stop/loss cap/profit ladder를 제외한 advisor 기반 SELL은 보류

T+5 판단 실패가 하루 전체 신규 매수 금지를 뜻하지는 않는다. 이후 fresh `opening_confirm` 또는 `intraday_live` judgment가 성공하고 broker/risk gate를 통과하면 신규 매수 권한을 다시 열 수 있다. 금지되는 것은 preopen 또는 실패한 same-day judgment를 자동 재사용해 신규 매수를 여는 동작이다.

### REQ-10. KR/US 정책 차등

KR과 US는 같은 phase 계약을 쓰되 장전 후보 활용 강도를 다르게 둔다.

수용 기준:

- KR: 장전 후보는 엄격히 watch-only, T+5 confirmation 필수
- US: 장전 후보는 watchlist 품질 향상에 적극 사용하되 execution authority는 T+5 이후에만 부여
- 공유 전략 파일 변경 시 KR/US 성과 영향을 분리 검토한다.

### REQ-11. 로그와 대시보드 가시성

운영자가 현재 판단의 권한을 즉시 볼 수 있어야 한다.

수용 기준:

- daily judgment 또는 dashboard에 `phase`, `execution_authority`, `built_at`, `source`, `trigger`, `live_index_context_ok`, `intraday_context_included`를 표시한다.
- 장전 판단 화면에는 `trade_ready=0`, `orders_allowed=false`가 명확히 표시된다.
- T+5 판단이 아직 없으면 "실행 장판단 미확정"으로 표시한다.

### REQ-12. 테스트와 QA

구현 시 phase 권한, 매수 차단, 매도 재확인, PathB 누수를 모두 테스트한다.

수용 기준:

- preopen watch-only 테스트가 실패하면 배포하지 않는다.
- opening T+5 판단 이후에만 trade_ready와 PathB 신규 등록이 가능해야 한다.
- pre-session sell queue는 opening confirm 전 직접 실행되지 않아야 한다.
- PathB AUTO_SELL_REVIEW cooldown, profit ladder, sizing 보호 테스트를 유지한다.

## 7. 항목별 적합성 검토

| ID | 적합성 | 검토 근거 | 재검토 조정 |
| --- | --- | --- | --- |
| REQ-01 | 적합 | 현재 phase는 존재하지만 운영 표시가 "장판단"으로 뭉치면 오해 가능 | 명칭 분리를 요구사항으로 유지 |
| REQ-02 | 적합 | preopen은 watch-only 계약이 이미 필요하며 매수 오염 방지에 직접적 | PathB 관련 필드 누수 방지도 수용 기준에 포함 |
| REQ-03 | 적합, 보강 필요 | 현재 기본값은 KR/US 모두 T+3이며, T+5는 2분 늦추는 보수화 변경 | 즉시 config 변경이 아니라 요구 기준으로 정의 |
| REQ-04 | 적합 | 장중 판단은 opening 판단을 대체하는 새 철학이 아니라 live context 갱신 | 개선/악화 양방향 동작을 명시 |
| REQ-05 | 적합 | 신규 매수는 phase gate와 risk/broker gate를 동시에 통과해야 함 | broker truth와 risk gate 우선순위 명시 |
| REQ-06 | 적합, 중요 | PathB는 기존 run이 독립적으로 움직이므로 preopen 신규 등록 누수 방지가 필요 | 보조 후보 carry는 허용하되 `_pathb_*` 실행 등록 필드는 차단 |
| REQ-07 | 적합 | 장전 SELL은 가격/체결 truth가 약해 직접 주문으로 쓰기 위험 | 기존 `pending_next_open_sell*` 재확인 필드 사용 |
| REQ-08 | 적합 | 시장 판단이 보호 청산을 완화하면 수익 엔진과 리스크 계약을 훼손 | hard stop/loss cap/profit ladder 우선권 고정 |
| REQ-09 | 적합 | 판단 실패는 주문보다 관망이 안전 | 기존 포지션 보호 경로는 중단하지 않도록 분리 |
| REQ-10 | 적합 | KR/US 장전 후보 품질과 전략 성과가 다르므로 동일 실행 정책은 위험 | phase 계약은 공통, 활용 강도만 차등 |
| REQ-11 | 적합 | 운영자가 권한 상태를 못 보면 preopen 판단을 실행 판단으로 오인 가능 | dashboard/로그 수용 기준 추가 |
| REQ-12 | 적합 | phase 계약은 회귀 위험이 크므로 테스트 없이는 유지 불가 | 보호 테스트 유지 조건 포함 |

## 8. 재검토 후 확정안

재검토 결과, 요구사항은 다음 형태로 확정한다.

1. 장전 판단은 `preopen_watch` 하나로 유지하되 실행 권한을 절대 부여하지 않는다.
2. 첫 실행 가능 장판단은 `opening_confirm`이며 기준 시각은 정규장 시작 후 5분으로 둔다.
3. `opening_confirm`이 실패하거나 live context가 부족하면 신규 매수는 금지한다.
4. 장전 후보는 장 시작 후 판단의 후보 universe, watchlist, 리스크 flag로만 사용한다.
5. PathA 신규 매수와 PathB 신규 wait plan 등록은 실행 가능 phase에서만 허용한다.
6. 기존 PathB 보유 포지션의 hard stop, loss cap, profit ladder, pre-close 보호 경로는 phase 변경으로 중단하지 않는다.
7. 장전 SELL 의견은 직접 주문이 아니라 장 시작 후 재확인 플래그로만 남긴다.
8. 시장 판단은 보유 종목 SELL의 context가 될 수 있지만 hard stop/loss cap/profit ladder를 완화하지 않는다.
9. KR은 장전 후보를 더 엄격히 watch-only로 쓰고, US는 후보 품질 개선에는 활용하되 실행 권한은 동일하게 T+5 이후만 허용한다.
10. 로그와 대시보드에는 phase와 execution authority를 분리 표시한다.
11. preopen PathB 후보는 완전 폐기하지 않고 T+5 판단의 보조 후보로 carry할 수 있다. 단, carry는 watchlist/universe/risk flag 수준이며 신규 PathB wait plan 등록 입력이 아니다.
12. T+5 판단 실패 시 기존 same-day judgment를 자동 재사용해 신규 매수를 열지 않는다. 신규 매수는 다음 fresh executable judgment 성공 전까지 차단하고 기존 포지션 보호 경로만 유지한다.

## 9. 구현 시 권장 변경 범위

구현은 작은 단계로 나눈다.

배포 단위 원칙:

- `execution_authority` 필드 추가와 preopen 주문/PathB 등록 차단 guard는 같은 배포 단위로 묶는다.
- authority 필드를 표시만 추가하고 실제 guard가 없는 상태로 배포하지 않는다.
- Phase 1은 기존 phase gate를 완화하지 않는 hardening 변경이어야 한다.
- Phase 2의 T+5 적용은 운영값 변경을 포함하므로 Phase 1과 별도 승인 단위로 둔다.

### Phase 1. 가시성 및 명시 guard

대상 후보:

- `trading_bot.py`
- `minority_report/analysts.py`
- dashboard 표시 경로
- `tests/test_preopen_opening_role_separation.py`
- `tests/test_candidate_action_live_mapping.py`

작업:

- 저장 judgment에 `execution_authority` 또는 동등한 필드 추가
- preopen 화면/로그 표시명을 `장전 후보판단`으로 분리
- `preopen_watch`에서 PathB 신규 등록 관련 필드가 남아도 등록하지 않는 guard 추가
- preopen에서 carry하는 후보는 watchlist/universe/risk flag로만 저장하고 `_pathb_*` 실행 등록 입력으로 쓰지 않도록 차단
- T+5 판단 전 dashboard에 "실행 장판단 미확정" 표시

### Phase 2. T+5 기준 적용

대상 후보:

- `trading_bot.py`
- `.env.example` 또는 운영 문서
- 관련 테스트

작업:

- KR/US opening judgment refresh 기준을 현재 3분에서 5분으로 정리
- 실제 `.env.live`, `.env.paper`, `config/v2_start_config.json` 변경은 운영자 승인 후 별도 작업으로 수행

### Phase 3. 매도 재확인 계약 강화

대상 후보:

- `trading_bot.py`
- `runtime/pathb_runtime.py`
- `tests/test_pre_session_sell_queue.py`
- PathB sell 관련 보호 테스트

작업:

- 장전 SELL 후보는 기존 `pending_next_open_sell*` 재확인 필드로만 남기는지 테스트 보강
- opening confirm 이후 broker truth 기반으로만 SELL/HOLD 확정하는지 테스트
- hard stop/loss cap/profit ladder 우선권 회귀 테스트 유지

## 10. 구현 시 검증 명령

문서만 변경한 현재 단계에서는 아래 확인으로 충분하다.

```powershell
git diff --stat
git diff -- docs/reports/preopen_opening_judgment_requirements_20260605.md
```

코드 구현 단계에서는 최소 아래 검증을 수행한다.

```powershell
python -m pytest tests/test_preopen_opening_role_separation.py tests/test_pre_session_sell_queue.py tests/test_candidate_action_live_mapping.py -q
python -m pytest tests/test_auto_sell_claude_gate.py::AutoSellClaudeGateTests::test_pathb_loss_cap_hold_respects_reask_cooldown -q
python -m py_compile trading_bot.py runtime/pathb_runtime.py minority_report/analysts.py
```

PathB 신규 등록 guard 또는 sell path를 직접 건드리면 관련 PathB 회귀 테스트를 추가로 실행한다.

```powershell
python -m pytest tests/test_pathb_runtime.py tests/test_pathb_sell_reconcile.py tests/test_pathb_profit_protection.py -q
```

## 11. 남은 결정 사항

요구서 기준 권장값은 아래와 같다. 실제 live env/config 반영은 운영자 승인 후 별도 작업으로 수행한다.

| 결정 항목 | 권장 기본값 | 영향 범위 | 검토 상태 |
| --- | --- | --- | --- |
| KR/US T+5 통일 여부 | KR/US 모두 T+5 | Phase 2, 신규 매수 허용 시점 | 현재 T+3에서 2분 늦추는 변경 |
| dashboard 문구 변경 | `장전 후보판단` / `실행 장판단` 분리 | Phase 1, 운영 가시성 | 적용 권장 |
| preopen PathB 후보 carry 여부 | carry allowed, 보조 후보로만 | Phase 1 guard 설계 | `_pathb_*` 실행 필드는 차단 |
| T+5 실패 시 judgment 재사용 여부 | 다음 fresh executable judgment 전까지 신규 매수 차단, 기존 포지션 보호만 유지 | Phase 2 fail-closed 정책 | 적용 권장 |

확정 권장 기본값:

```text
KR: preopen 후보 carry only, T+5 실행 판단 필수, 실패 시 신규 매수 차단
US: preopen 후보 carry allowed, T+5 실행 판단 필수, 실패 시 신규 매수 차단
기존 보유 포지션: hard stop/loss cap/profit ladder/pre-close는 계속 작동
```

## 12. 2차 검토 반영 사항

운영 검토에서 지적된 항목은 다음과 같이 반영했다.

| 검토 항목 | 확인 결과 | 문서 반영 |
| --- | --- | --- |
| T+5 기준의 의미 | 현재 KR/US 기본값은 T+3 | T+5는 현재보다 늦추는 보수화 변경으로 명시 |
| 재확인 플래그 실재 여부 | `pending_next_open_sell*` 계열 필드가 이미 존재 | 신규 schema 전제 제거, 기존 필드 우선 사용 |
| preopen PathB 후보 처리 | 후보 carry 여부가 Phase 1 guard 설계에 직접 영향 | carry allowed로 정리하되 `_pathb_*` 실행 등록 금지 |
| Phase 1/2 분리 배포 위험 | authority 표시만 있고 guard가 없으면 반쪽 배포 위험 | authority 필드와 preopen guard를 같은 배포 단위로 명시 |

## 13. 최종 재검토 결과

최종 재검토 결과, 요구서 자체의 blocking issue는 없다. 구현 착수 전 기준 문서로 사용할 수 있다.

최종 보강으로 확정한 사항:

- `opening_confirm` phase label과 주문 권한을 분리한다. T+5 refresh barrier 전에는 phase가 opening이어도 실행 권한을 주지 않는다.
- T+5 판단 실패는 다음 fresh executable judgment 성공 전까지의 신규 매수 차단이다. 이후 fresh intraday 판단이 성공하면 신규 매수 권한을 다시 열 수 있다.
- preopen PathB carry는 보조 후보 carry만 허용하고 `_pathb_*` 실행 등록 필드는 차단한다.
- Phase 1 테스트 범위에는 preopen 역할 분리 테스트뿐 아니라 candidate action live mapping 테스트를 포함한다.

남은 위험:

- 실제 구현에서 `_apply_selection_meta()` 호출 전에 preopen PathB 등록 필드가 이미 등록되는 경로를 막아야 한다.
- dashboard 표시만 먼저 배포되고 guard가 빠지면 운영자가 실행 권한을 오해할 수 있다. authority 표시와 guard는 같은 배포 단위여야 한다.
- T+5 운영값 변경은 live 진입 타이밍을 늦추므로, 적용 후 KR/US별 체결 기회 손실과 장초반 노이즈 감소 효과를 별도로 모니터링해야 한다.

## 14. 구현 완료 검토

구현 일자: 2026-06-05

구현 반영:

- `trading_bot.py`: KR/US opening refresh 기본값을 5분으로 조정했다.
- `trading_bot.py`: `execution_authority` 상수와 judgment authority 계산을 추가했다.
- `trading_bot.py`: `opening_confirm` phase라도 T+5 refresh barrier 전에는 신규 매수 gate를 열지 않는다.
- `trading_bot.py`: `preopen_watch` 저장 judgment에 `execution_authority=NONE`을 남긴다.
- `trading_bot.py`: preopen selection meta에서 `trade_ready`, `price_targets`, `_pathb_wait_tickers`, `_pathb_price_targets`, `_pathb_wait_origins`, `_pathb_registration_scope` 실행 필드를 제거한다.
- `trading_bot.py`: preopen source에서는 `pathb.register_from_selection_meta()`를 호출하지 않는다.
- `dashboard/dashboard_server.py`: judgment basis API에 `phase`, `execution_authority`, `judgment_label`, `authority_label`, `orders_allowed`를 추가했다.
- `tests/test_preopen_opening_role_separation.py`: T+5 barrier, authority gate, unconfirmed block, preopen PathB 필드 제거 테스트를 추가했다.
- `tests/test_candidate_action_live_mapping.py`: preopen `PULLBACK_WAIT`가 watch-only로 demote되고 PathB 등록이 발생하지 않는 테스트를 추가했다.
- `tests/test_pre_session_sell_queue.py`: preopen phase에서 pending next-open sell recheck가 실행되지 않는 테스트를 추가했다.
- `tests/test_dashboard_broker_integrity.py`: dashboard judgment label/authority payload 테스트를 추가했다.

요구서 대비 차이/누락 점검:

| 항목 | 결과 |
| --- | --- |
| 장전 판단 주문 권한 없음 | 구현 완료 |
| 장전 PathB 신규 wait plan 등록 차단 | 구현 완료 |
| T+5 첫 실행 판단 | 구현 완료. env/config override가 있으면 override 값이 우선한다. |
| T+5 실패 시 신규 매수 차단 | 구현 완료. 다음 fresh executable judgment 성공 전까지 차단한다. |
| 장전 SELL 직접 실행 금지 | 기존 구조 유지 + preopen recheck 차단 테스트 보강 |
| dashboard 구분 표시 | API basis payload 구현 완료 |
| `.env*`, `config/v2_start_config.json`, `state/brain.json` 변경 금지 | 이번 작업에서 변경하지 않음 |

검증 결과:

- `python -m pytest tests/test_preopen_opening_role_separation.py tests/test_pre_session_sell_queue.py tests/test_candidate_action_live_mapping.py -q` -> 151 passed
- `python -m pytest tests/test_dashboard_broker_integrity.py -q` -> 5 passed
- `python -m pytest tests/test_preopen_opening_role_separation.py tests/test_pre_session_sell_queue.py tests/test_candidate_action_live_mapping.py tests/test_dashboard_broker_integrity.py -q` -> 156 passed
- `python -m pytest tests/test_auto_sell_claude_gate.py::AutoSellClaudeGateTests::test_pathb_loss_cap_hold_respects_reask_cooldown tests/test_gate_evaluation.py -q` -> 5 passed
- `python -m pytest tests/test_pathb_runtime.py::PathBRuntimeTests::test_register_from_selection_meta_creates_waiting_run tests/test_pathb_runtime.py::PathBRuntimeTests::test_register_from_selection_meta_preserves_pullback_wait_origin tests/test_pathb_runtime.py::PathBRuntimeTests::test_register_from_selection_meta_audits_missing_price_targets tests/test_pathb_runtime.py::PathBRuntimeTests::test_register_from_selection_meta_skips_structurally_unaffordable_us_pathb_plan tests/test_pathb_runtime.py::PathBRuntimeTests::test_register_from_selection_meta_keeps_us_pathb_plan_inside_one_share_cap -q` -> 5 passed
- `python -m pytest tests/test_pathb_runtime.py tests/test_pathb_sell_reconcile.py tests/test_pathb_profit_protection.py -q` -> 155 passed
- `python -m pytest -q` -> 2267 passed, 2 skipped
- `python -m py_compile trading_bot.py runtime/pathb_runtime.py minority_report/analysts.py dashboard/dashboard_server.py` -> pass
- `python tools/live_preflight.py --mode live --skip-dashboard --json` -> `ok=true`, `fail_count=0`

확장 QA 참고:

- 확장 QA는 최종 워크트리 기준 통과했다.
- 초기 실행에서는 `PathBRuntime._submit_sell()` 테스트 fixture의 local position 누락으로 3건 실패가 있었으나, 현재 워크트리의 `tests/test_pathb_runtime.py` fixture 보강 이후 155건이 통과했다.
- 해당 축은 PathB sell 보호 테스트 fixture 이슈였고, 이번 작업은 `runtime/pathb_runtime.py`의 보호 sell 로직을 변경하지 않았다.

기존 DB read-only 시뮬레이션:

- `data/ticker_selection_log.db`, `data/audit/candidate_audit.db`, `data/v2_event_store.db`, `data/pathb.db`를 SQLite `mode=ro`와 `PRAGMA query_only=ON`으로 조회했다.
- 조회 전후 주요 DB mtime 변경 없음.
- 최근 live daily judgment 65건 점검 결과, preopen 실행 누수(`trade_ready` 또는 `_pathb_wait_tickers`) 0건.

운영 테스트 참고:

- live preflight는 `ok=true`, `fail_count=0`으로 종료했다.
- 2026-06-05 12:10 KST 재점검 기준 경고는 12건이며, `blocked_if_live_start_warn_count=1`이다.
- preopen scheduler heartbeat 경고는 운영 프로세스 재기동 후 해소되었다. 재기동 PID는 `61240`이며 `runtime.live_preopen_scheduler_heartbeat`는 PASS다.
- 남은 live-start 차단성 경고는 `state.brain_memory_change_guard` 1건이다. 이는 이번 코드 변경 문제가 아니라 `state/brain.json`의 미승인 정책 메모리 변경이며, 운영자가 명시적으로 승인하거나 되돌리기 전까지 preflight가 차단성 경고로 표시한다.
- `runtime.process_inventory` 경고는 live와 paper 프로세스 동시 실행 감지이며 accepted warning이다. paper process가 의도된 실행이면 live 시작 차단 사유가 아니다.
- 이번 변경은 broker truth fail-closed를 완화하지 않았으며, broker truth가 stale/error 상태가 되면 신규 PathB entry가 차단되는 기존 기대 동작을 유지한다.

## 15. 최종 수용 기준

이 요구서가 구현 완료되었다고 보려면 아래가 모두 충족되어야 한다.

- 장전 판단만 존재하는 상태에서 신규 매수 주문이 생성되지 않는다.
- 장전 판단만 존재하는 상태에서 PathB 신규 wait plan이 등록되지 않는다.
- 장전 판단만 존재하는 상태에서 advisor 기반 SELL 주문이 직접 실행되지 않는다.
- T+5 opening confirm 이후에만 첫 실행 장판단 권한이 생긴다.
- T+5 judgment 실패 시 신규 매수는 fail-closed 한다.
- 기존 포지션 보호 청산은 opening judgment 실패로 중단되지 않는다.
- dashboard와 로그에서 현재 판단이 후보판단인지 실행 장판단인지 구분된다.
- KR/US의 preopen 활용 강도 차이가 문서와 테스트에 반영된다.
