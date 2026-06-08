# KR Wait Re-evaluation Queue Review

- 작성일: 2026-06-08
- 목적: KR wait counterfactual 후보를 live 오염 없이 재평가 큐로 운영할 수 있는지 검증
- 범위: `data/audit/candidate_audit.db` read-only 분석, `.runtime/ops_simulation_analysis/*` 산출물
- 적용 금지: 이 문서는 자동 매수 정책이 아니다. counterfactual row에서 직접 주문하지 않는다.

## 결론

KR wait는 broad live 적용 대상이 아니다. 전체 wait 후보 평균이 음수이고, 기본 큐 정책도 실제 DB에서 음수로 확인됐다. 다만 live-visible 조건을 더 좁히면 보고/재평가 후보로 쓸 만한 두 그룹이 남는다.

현재 live 코드에 반영할 개선은 주문 로직이 아니라 `read-only queue/report`다. 이 큐는 후보를 기록하고, 나중에 충분한 표본이 쌓였을 때 기존 `RouteDecision`, risk, affordability, broker truth, daily cap, max position gate를 다시 통과시키는 방식으로만 live 전환을 검토한다.

## 실행한 정책별 결과

| 정책 | eligible | queued | avg 60m | median 60m | win rate | worst 60m | worst drawdown | 판단 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 기본 큐: `analyst_reinvoke`, `confirmed/partial`, late 제외, 일 2개 cap | 362 | 10 | -1.8966 | -0.1085 | 30.00% | -16.4782 | -16.6397 | 탈락 |
| `analyst_reinvoke + partial + PROBE_READY + wait_30m` 전체 | 45 | 45 | +0.8314 | +0.8830 | 68.89% | -15.7488 | -16.6238 | 장초반 리스크 큼 |
| 위 정책 + 일 2개 cap | 45 | 7 | -0.7894 | +0.6329 | 71.43% | -15.7488 | -16.6238 | 탈락 |
| 위 정책 + `OPEN_30_60/60_90/90_270`만 | 12 | 12 | +0.7241 | +0.4914 | 66.67% | -2.2018 | -2.7197 | 관찰 후보 |
| 위 정책 + 중반 이후 + 일 2개 cap | 12 | 4 | +1.0051 | +1.1990 | 75.00% | -2.2018 | -2.4771 | 관찰 후보 |
| `session_open + confirmed + WATCH + 중반 이후 bucket` 전체 | 50 | 50 | +0.9147 | +0.9528 | 64.00% | -5.4628 | -6.2215 | 관찰 후보 |
| 위 정책 + 일 2개 cap | 50 | 6 | +1.2183 | +1.8278 | 83.33% | -5.4628 | -6.2215 | 최우선 관찰 후보 |

## 개선 방향

### 수익성

개선 전:
- KR wait 후보를 counterfactual 상위 결과 중심으로 보면 좋아 보였지만, 전체 모집단에서는 평균이 음수였다.
- 기본 큐 정책은 `BUY_READY`를 많이 선택했지만 실제 최근 cap 결과가 `-1.8966%`로 나빠졌다.

개선 후:
- 자동매수 후보가 아니라 `read-only re-evaluation queue`로 제한한다.
- 우선 관찰 후보는 두 그룹으로 좁힌다.
  - `session_open + confirmed + WATCH + OPEN_30_60/OPEN_60_90/OPEN_90_270`
  - `analyst_reinvoke + partial + PROBE_READY + wait_30m + OPEN_30_60/OPEN_60_90/OPEN_90_270`
- 최소 30개 이상 신규 live-visible 후보를 축적하기 전에는 enforce/live 주문 정책으로 승격하지 않는다.

### 버그/오염 방지

개선 전:
- counterfactual row를 그대로 주문 후보로 보면 historical label leakage와 live DB 오염 위험이 있었다.

개선 후:
- 큐 도구는 DB를 read-only URI로 열고 `.runtime` 아래에만 산출물을 쓴다.
- 각 row에 `order_send=false`, `broker_call=false`, `claude_call=false`, `learning_excluded=true`, `must_recheck_live_route_and_risk=true`를 명시한다.
- `labels_used_for_queue_selection=false` 계약을 유지한다. 60분 outcome과 drawdown은 리포트 평가용으로만 쓴다.

### 운영성

개선 전:
- wait 후보가 왜 선택/탈락했는지 정책별로 비교하기 어려웠다.

개선 후:
- `path_name`, `route_source`, `evidence_data_state`, `evidence_action_ceiling`, `required_entry_bucket`, `excluded_entry_bucket`, daily/ticker cap을 CLI로 바꿔가며 비교할 수 있다.
- 리포트는 queued, eligible_before_caps, rejected reason을 분리해서 운영 판단에 쓸 수 있다.

## Live 전환 조건

아래 조건을 충족하기 전까지 KR wait 큐는 자동 주문으로 연결하지 않는다.

- 신규 live-visible 후보 30개 이상 축적
- median 60m > 0
- cap 적용 후에도 avg 60m > 0
- worst drawdown이 허용 가능한 범위로 유지
- 기존 PathA/PathB 후보를 밀어내지 않는 reserved quota 설계
- ORDER_UNKNOWN, 미체결, broker truth 불신 증가 없음
- 실제 주문 전에는 기존 `RouteDecision`, risk, affordability, broker truth, daily cap, max position gate 재통과

## 생성 산출물

- `.runtime/ops_simulation_analysis/kr_wait_re_evaluation_20260608/`
- `.runtime/ops_simulation_analysis/kr_wait_re_evaluation_20260608_analyst_probe_wait30_all/`
- `.runtime/ops_simulation_analysis/kr_wait_re_evaluation_20260608_analyst_probe_wait30_capped/`
- `.runtime/ops_simulation_analysis/kr_wait_re_evaluation_20260608_analyst_probe_wait30_mid_all/`
- `.runtime/ops_simulation_analysis/kr_wait_re_evaluation_20260608_analyst_probe_wait30_mid_capped/`
- `.runtime/ops_simulation_analysis/kr_wait_re_evaluation_20260608_session_watch_mid_all/`
- `.runtime/ops_simulation_analysis/kr_wait_re_evaluation_20260608_session_watch_mid_capped/`

## 최종 판단

지금 개선할 것은 live 매수 정책이 아니다. 개선 대상은 `테스트/시뮬레이션 모드에서 KR wait 후보를 안전하게 반복 검증하는 큐/리포트`다. 현재 수치상 live로 바로 가도 되는 항목은 US one-share-over-budget sizing fix이고, KR wait는 관찰 후보만 남긴다.
