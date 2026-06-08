# Ops Simulation Improvement Live DB Recheck

작성일: 2026-06-08

## 목적

기존 시뮬레이션 리포트의 개선 후보가 실제 live 운영 개선으로 볼 수 있는지, 운영 DB와 코드/설정 기준으로 재검토한다. 이 문서는 읽기 전용 검토 결과이며 live 주문, 런타임 코드, `.env*`, `config/v2_start_config.json`, `state/brain.json`은 변경하지 않았다.

## 결론

실제 개선 방향이 맞는 항목은 있다. 다만 구현 방향은 기존 리포트보다 더 좁혀야 한다.

1. 공통 price coverage audit은 실제 개선이 맞다.
2. US high-price 후보 개선은 실제 운영 이슈가 맞다. 단, "one-share cap 설정 누락"이 아니라 "장 초반 soft gate와 submit sizing/retry 흐름"이 핵심이다.
3. STRL/LITE급 over-cap 후보는 현재 cap 밖이므로 버그가 아니라 별도 high-confidence tier 설계 대상이다.
4. KR wait_30m/wait_60m은 전체 적용하면 개선이 아니다. 고득점 후보만 live 재평가 큐에 넣어야 한다.
5. PathB live buy zone, target, stop, profit ladder 변경은 현재 근거로는 금지한다.

## 2026-06-08 구현 반영 업데이트

US within-cap high-price 버그는 코드에 반영했다. `runtime/pathb_runtime.py`에서 fixed sizing 예산과 one-share entry cap을 분리했다.

반영 후 동작:

- `pathb_fixed_order_krw=450000`은 계속 일반 수량 산정 기준이다.
- `PATHB_ALLOW_ONE_SHARE_OVER_BUDGET=true`이고 one-share cap이 700,000원이면 450,000~700,000원 1주 후보는 `can_buy_1_share=true`가 된다.
- 장 초반 soft gate 중에는 600,000원 후보를 즉시 주문하지 않고 `ORDER_SIZE_TOO_SMALL_GATE`로 waiting 재평가에 남긴다.
- 장 초반이 아니면 600,000원 후보는 `qty=1`로 기존 one-share-over-budget 정책을 탄다.
- 60,000원처럼 최소주문금액 때문에 2주가 필요한 케이스는 fixed budget 100,000원을 초과하면 계속 `qty=0`이다. one-share cap이 multi-share sizing으로 새지 않는다.
- 700,000원 cap 초과 후보는 계속 high-price block으로 유지한다.

수정 파일:

- `runtime/pathb_runtime.py`
- `tests/test_live_order_safety.py`
- `tests/test_pathb_runtime.py`

보호 영역 영향:

PathB sizing reason split 보호 영역을 직접 수정했다. 변경은 one-share-over-budget 정책의 registration/submit 불일치를 맞추는 범위이며 broker truth, 주문 제출 정책, profit ladder, target, stop, `.env*`, `config/v2_start_config.json`, `state/brain.json`은 변경하지 않았다.

## 실제 확인 결과

### 공통: price coverage audit

카테고리: 버그/운영성

시뮬레이션 결과 240건 중 price coverage는 complete 229건, partial 11건이었다. partial 원인은 모두 `end_before_requested`였다. 수익성 판단 전에 price window 누락을 분리해야 하므로 coverage audit은 실제 개선이 맞다.

전/후:

| 항목 | 개선 전 | 개선 후 방향 |
|---|---|---|
| price tape 신뢰도 | 파일 존재 여부 중심 | requested/actual window, matched rows, partial flag를 보고 수익성 판단에서 분리 |
| 리포트 판단 | 누락 window가 수익성 평균에 섞일 수 있음 | incomplete row는 data-quality 케이스로 먼저 분리 |

### 미국장: high-price one-share 후보

카테고리: 수익성 + 운영성

live 설정은 다음처럼 적용되어 있었다.

| 설정 | 값 |
|---|---|
| `PATHB_FIXED_ORDER_KRW` | `450000` |
| `PATHB_ALLOW_ONE_SHARE_OVER_BUDGET` | `true` |
| `PATHB_ONE_SHARE_OVER_BUDGET_MAX_KRW` | `700000` |
| `PATHB_ONE_SHARE_OVER_BUDGET_MAX_ACCOUNT_PCT` | `30.0` |

코드상 등록 gate는 one-share cap을 반영한다. 따라서 설정이 완전히 빠진 상태는 아니다. 실제 확인된 문제는 submit sizing에서 장 초반 soft gate가 켜지면 one-share-over-budget 허용이 꺼지고, 450,000원이 225,000원으로 축소된 상태에서 `high_price_one_share_blocked`가 발생하는 흐름이다.

운영 DB 기준 US live high-price 관련 이벤트 66건을 분류했다.

| 분류 | 건수 | 판단 |
|---|---:|---|
| submit within-cap blocked by early gate | 37 | 실제 개선 후보 |
| registration over current cap | 14 | 현재 정책상 정상 차단 |
| registration unknown/companion event | 10 | blocker payload 보강 필요 |
| submit over cap | 1 | 현재 정책상 정상 차단 |
| 기타 unknown | 4 | 분석 제외 |

within-cap early-gate block은 unique path 기준 16건이었다. 이 중 13건은 이후 `CLOSED`, 3건은 `CANCELLED` 또는 미청산으로 남았다. `CLOSED` 13건의 평균 PnL은 +1.4593%, median은 +0.1925%, 이익 7건/손실 6건이었다.

이 해석이 중요하다. 이 문제는 "고가 후보를 전부 놓쳤다"가 아니라 "장 초반에 기존 cap 내 후보가 일시 차단되고, 일부는 나중에 진입했지만 일부는 취소/미진입으로 끝났다"가 정확하다. 따라서 개선 방향은 early gate 전체 해제가 아니다.

전/후:

| 항목 | 개선 전 | 개선 후 방향 |
|---|---|---|
| AVGO/ARM급 within-cap 후보 | early gate 중 `HIGH_PRICE_BUDGET_BLOCK` 또는 `ORDER_SIZE_TOO_SMALL_GATE`로 waiting 유지, 일부 cancel 가능 | 기존 cap 내 후보는 early gate 만료/재평가 시 반드시 다시 submit sizing까지 도달하도록 retry/visibility 강화 |
| one-share cap 정책 | registration에는 반영, submit early-gate에는 제한 | registration/submit/retry 단계별 blocker를 분리 보고하고, cap 내 후보가 어느 단계에서 멈췄는지 audit |
| 즉시 주문 | early gate가 크기 축소 의도로 작동 | 무조건 즉시 주문 금지. 고확신/현금/broker/risk 통과 + early-gate 예외 조건을 별도 테스트 후 live 적용 |

구현 후보 및 처리 상태:

1. `temporary_early_entry_size_gate`로 waiting이 유지된 path가 early gate 만료 뒤 재평가됐는지 audit한다. 아직 후속.
2. within-cap인데 cancel된 path는 cancel reason과 retry 기회 유무를 리포트한다. 아직 후속.
3. one-share cap이 submit sizing의 `original_budget_krw`/`can_buy_1_share` 판정에 반영되도록 수정했다. 반영 완료.
4. early gate 전체 해제는 적용하지 않았다. within-cap 후보는 임시 size gate로 waiting 재평가에 남긴다. 반영 완료.

### 미국장: STRL/LITE급 over-cap 후보

카테고리: 수익성 후보, 현재 버그 아님

STRL/LITE급 후보는 필요 1주 금액이 현재 700,000원 cap을 크게 넘었다. 이 그룹은 AVGO/ARM과 분리해야 한다.

전/후:

| 항목 | 개선 전 | 개선 후 방향 |
|---|---|---|
| over-cap 후보 | `HIGH_PRICE_BUDGET_BLOCK`로 차단 | 정상 차단으로 유지 |
| 개선 방식 | 전역 fixed budget 증액 유혹 | 별도 high-confidence tier 후보로 분리, 운영자 승인 전 live 미적용 |

적용 조건 후보:

- 별도 max KRW cap
- 더 높은 confidence threshold
- 하루/시장별 tier entry limit
- 별도 PnL attribution
- 기존 450,000원 fixed order budget은 그대로 유지

### 한국장: wait_30m/wait_60m

카테고리: 수익성 후보, 제한 적용 필요

기존 focused 리포트의 top 후보 9건은 강한 양수 성과를 보였다. 하지만 전체 `candidate_counterfactual_paths`를 넓게 보면 KR wait 경로는 개선이 아니다.

운영 DB 전체 KR wait close-outcome 기준:

| 집합 | count | avg 60m | median 60m | best | worst | 60m +3% 이상 비율 |
|---|---:|---:|---:|---:|---:|---:|
| all | 3204 | -0.4462 | -0.3757 | 26.8346 | -20.1327 | 0.0868 |
| watch/probe 계열 | 2947 | -0.3982 | -0.3584 | 26.8346 | -20.1327 | 0.0899 |
| fresh/partial 계열 | 2778 | -0.4837 | -0.3997 | 26.8346 | -19.5796 | 0.0860 |

따라서 KR wait은 자동 주문 큐가 아니라 제한된 live re-evaluation queue가 맞다.

전/후:

| 항목 | 개선 전 | 개선 후 방향 |
|---|---|---|
| KR wait 후보 | counterfactual top 후보 중심으로 긍정 해석 가능 | 전체 평균은 음수이므로 엄격한 후보 필터 필요 |
| 주문 연결 | counterfactual row에서 직접 주문 위험 | due time에 live evidence/routing/safety gate를 다시 통과할 때만 진입 |
| 적용 범위 | wait_30m/60m 전반 | 고득점, 낮은 drawdown, 충분한 evidence, 재평가 시 fresh 데이터 조건 |

필수 조건:

- counterfactual row에서 직접 주문 금지
- 기존 live route/gate 재평가
- broker truth, ORDER_UNKNOWN, affordability, risk, PathB sizing guard 우회 금지
- 최소 1주 이상 read-only/live-rehearsal로 후보 품질 재확인

## 최종 구현 우선순위

1. price coverage audit 보강
2. US high-price blocker-stage audit: registration/submit/early-gate/retry/cancel reason 분리
3. US within-cap one-share retry/재평가 보강: AVGO/ARM급부터
4. US over-cap high-confidence tier 설계: STRL/LITE급, 운영자 승인 필요
5. KR wait live re-evaluation queue: 전체 적용 금지, 제한 후보만

## 적용 금지

- US PathB live buy zone 전역 확장 금지
- US PathB target/stop/profit ladder/pre-close 변경 금지
- fixed order budget 전역 증액 금지
- early gate 전체 해제 금지
- counterfactual row 직접 주문 금지
- broker truth, ORDER_UNKNOWN, risk, affordability, PathB sizing guard 우회 금지

## 재검토 결론

실제 개선은 맞지만, 개선 범위는 더 좁다. 가장 확실한 live 개선은 coverage audit과 US within-cap high-price 후보의 blocker-stage/retry 보강이다. KR wait은 수익성 후보 발굴에는 유용하지만 전체 live 적용은 손실 가능성이 더 크므로 제한된 재평가 큐로만 진행해야 한다.
