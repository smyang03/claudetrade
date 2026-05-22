# 서브스크리너 라이브 관찰 리포트 (2026-05-22)

## 관찰 범위

- 관찰 시각: 2026-05-22 02:07 ~ 02:52 KST
- 라이브 프로세스: `state/live_trading_bot.pid` 기준 PID `9500`
- 실행 명령: `trading_bot.py --live`
- 확인 대상:
  - 서브스크리너 live 반영 여부
  - final prompt evidence alignment 개선 반영 여부
  - reinvoke, rescreen, PathB 플랜/주문, 브로커 동기화 이상 여부

## 결론

오늘 반영한 두 변경은 모두 실제 live 프로세스에 적용되어 동작했다.

- 서브스크리너는 15~16분 간격으로 총 3회 실행됐고, 3회 모두 감지 및 reinvoke/rescreen까지 성공했다.
- evidence alignment 개선은 재시작 직후부터 `target_rule=phase:mid+alignment_min:28`로 적용됐다.
- 기존 `prompt=35`, `requested=20`, `overlap=0.57` 경고는 재시작 이후 사라졌고, overlap은 0.80으로 맞춰졌다.
- 02:40 세 번째 서브스크리너 트리거 이후 Claude 재판단이 `MILD_BULL size=55%`를 `65%`로 올렸고, 그 결과 NVDA/CRWV PathB 주문이 실제 체결됐다.

운영 관점에서는 기능은 의도대로 작동했지만, CRWV 체결 후 브로커 동기화에서 메타가 일부 유실되는 후속 개선점이 발견됐다.

## 서브스크리너 동작 기록

`state/sub_screener_US_2026-05-21.json` 최종 상태:

- `scan_count=3`
- `detection_count=3`
- `attempt_count=3`
- `success_count=3`
- `max_per_session=5` 대비 3회 사용
- 반복 폭주 없음

실행별 흐름:

| 시각 | 감지 | 결과 |
|---|---:|---|
| 02:08:31 | `new_plan_a=1`, `new_plan_b_high=3` | reinvoke 성공, `CAUTIOUS -> MILD_BULL size=55%`, sub_screener rescreen 64개 |
| 02:23:57 | `new_plan_a=3`, `new_plan_b_high=3` | reinvoke 성공, `MILD_BULL -> MILD_BULL size=55%`, sub_screener rescreen 75개 |
| 02:40:01 | `new_plan_a=0`, `new_plan_b_high=3` | reinvoke 성공, `MILD_BULL -> MILD_BULL size=65%`, sub_screener rescreen 72개 |

첫 두 번은 `ANALYST_MAX_GROSS_EXPOSURE_REACHED`로 신규 진입이 막혔다. 세 번째는 Claude 재판단 결과 size가 55%에서 65%로 완화되면서 NVDA/CRWV 진입이 열렸다. 서브스크리너가 직접 주문한 것은 아니고, 설계대로 기존 reinvoke와 PathB price_plan 경로를 당긴 결과다.

## Evidence Alignment 확인

재시작 전에는 final prompt가 35개인데 evidence 요청이 20개라 overlap 0.57 경고가 반복됐다.

재시작 후 확인된 값:

- `target_rule=phase:mid+alignment_min:28`
- `candidate_count=35`
- `target_limit=28`
- `requested=28`
- `complete=27~28`
- `coverage_ratio=0.9643~1.0`
- `evidence_prompt_overlap_ratio=0.8`
- system log의 `[final prompt evidence alignment]` 경고 재발 없음

US는 yfinance provider라 호출 부담은 문제 없이 지나갔다. KR KIS 호출 증가는 이번 관찰 구간에서 확인 대상이 아니므로 KR 장중 별도 확인이 필요하다.

## 실제 주문 및 포지션 변화

02:40 세 번째 서브스크리너 트리거 이후 다음 주문이 발생했다.

| 종목 | 주문 | 상태 |
|---|---|---|
| NVDA | 1주, limit 221.97, order `0031498928` | 02:44:35 체결 반영, state에 `order_fill/trusted/path_b`로 정상 보존 |
| CRWV | 3주, limit 107.74, order `0031498937` | 02:44:36 체결 반영 후 일시 제거, 02:45:56 broker-held로 재주입 |

최종 상태:

- `state/live_pending_orders.json`: 0개
- `state/live_open_positions.json`: 7개
- 02:49:56 및 02:51:10 `PathB FILLED reconcile`: `checked=7`, `kept_open=7`, `broker_truth_unavailable=0`, `errors=[]`

## 남은 주의점

### 1. CRWV 체결 메타 유실

CRWV는 주문 체결 반영 직후 다음 흐름을 보였다.

1. 02:44:36 체결 반영: `CRWV 3주`, order `0031498937`
2. 02:44:37 브로커 동기화가 `브로커 미보유`로 보고 로컬 포지션 제거
3. 02:45:56 브로커 보유 포지션으로 다시 주입
4. 최종 state에는 `position_origin=broker_injected`, `position_integrity=protected`, `order_no/path_run_id/entry_route`가 비어 있음

보호 상태로 복구됐고 reconcile도 7개 보유를 확인했지만, PathB 주문 메타가 빠져 향후 성과 attribution, PathB run 추적, exit metadata 품질이 낮아질 수 있다.

개선안:

- 갓 체결 반영한 주문은 짧은 grace window 동안 `_sync_runtime_with_broker()`가 stale 제거하지 않도록 보호한다.
- broker-held 주입 시 같은 ticker/qty의 최근 filled pending order가 있으면 `order_no`, `path_run_id`, `entry_route`, `pathb_plan` 메타를 병합한다.

### 2. 서브스크리너 reinvoke가 exposure cap을 간접 완화

02:40 reinvoke 결과 `MILD_BULL size=55% -> 65%`가 되었고, 이 때문에 앞서 막히던 신규 진입이 열렸다.

이는 기존 Claude 재판단 인프라를 재사용한 설계상 자연스러운 결과다. 다만 운영 정책상 “서브스크리너는 새 후보만 당기고 exposure cap은 보수적으로 유지해야 한다”가 의도라면 별도 안전장치가 필요하다.

검토 옵션:

- sub_screener trigger로 발생한 reinvoke에서는 size 상향을 허용하지 않기
- 또는 `SUB_SCREENER_ALLOW_SIZE_RELAXATION=false` 같은 별도 env gate 추가
- 또는 size 상향은 허용하되 첫 1~2일은 shadow/텔레그램 승인형으로 제한

### 3. 기존 잔여 로그

- `PathB profit_review timeout/debounce`: IBM/IREN에서 반복. 이번 변경과 직접 관련은 없지만 계속 관찰 필요.
- `FRVO`, `YSS`: history data insufficient 및 backfill cooldown 반복. 정상 fail-closed 성격.
- `LITE`: `INVALID_PRICE` risk event 반복. 주문으로 이어지지는 않음.
- `ANALYST_MAX_GROSS_EXPOSURE_REACHED`: NVDA/CRWV 체결 이후 다시 신규 진입 차단으로 작동 중.

## 내일 아침 확인 체크리스트

- CRWV가 대시보드/포지션 관리 화면에서 `broker_injected/protected`로 보이는지 확인
- CRWV에 stop/exit 관리가 정상 적용되는지 확인
- `selection_intraday_evidence_coverage`에서 KR 장중 `target_rule=phase:mid+alignment_min:*` 적용 시 KIS timeout 증가 여부 확인
- sub_screener state가 세션당 5회 제한을 넘지 않았는지 확인
- `final prompt evidence alignment` system warning이 재발하지 않았는지 확인
