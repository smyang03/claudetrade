# Profit Path Shadow Challenger 구현 결과 (2026-07-10)

## 결론

핵심 기능을 제거하지 않고 `KR counterfactual path rank`를 실제 런타임의 forward-shadow challenger로 제품화했다.

- 주문 권한: 없음. 모델 artifact의 상태는 항상 `SHADOW`다.
- 적용 시장: KR과 US 모두 전용 모델을 shadow 활성화한다.
- 주문 영향: 현재 `PROFIT_EVIDENCE_GATE_MODE=shadow`이므로 기존 주문을 차단하지 않는다.
- 목표: 실제 주문 직전 point-in-time 입력으로 경로별 확률·비용 차감 순수익을 예측하고, 60분 결과를 축적해 승격 근거를 만든다.
- 자동 승격: 없음. monitor가 요건 충족 여부만 보고하며 설정을 바꾸지 않는다.

## 이번에 완성한 구조

1. `tools/train_profit_path_shadow.py`
   - candidate audit DB의 backward-only 매칭 데이터로 학습한다.
   - train → calibration → independent validation → purge 순서를 유지한다.
   - 연구용 sklearn/joblib artifact와 운영용 portable JSON artifact를 함께 만든다.
2. `runtime/profit_path_predictor.py`
   - 주문 직전의 candidate, 진입가, post-open return/volume/VWAP/pullback, path 정보를 사용한다.
   - 미래 outcome, Claude 최종 action, trainer composite score를 입력으로 사용하지 않는다.
   - NumPy만으로 선형 모델·one-hot·isotonic calibration을 재현한다.
3. 공통 buy gate와 Path B
   - 저장된 profit evidence가 없을 때만 challenger가 예측한다.
   - Path B는 실제 signal limit price, signal reason, reference price와 경로명을 전달한다.
4. lifecycle forward ledger
   - `PROFIT_EVIDENCE_SHADOW`를 정식 non-status 이벤트로 등록했다.
   - 예측 시점의 feature snapshot과 model version을 불변 payload로 저장한다.
   - 중복 억제 키는 ticker 전체가 아니라 `경로 + model + 분 단위 시점` 기준이다.
5. `tools/profit_path_forward_monitor.py`
   - shadow 예측을 동일 시장·세션·ticker·path의 60분 outcome과 연결한다.
   - 순수익 평균, 승률, PF, AUC, ECE, bootstrap net LCB를 계산한다.
   - 최소 60건·20세션·AUC 0.52·ECE 0.10 이하·net LCB 양수일 때만 `promotion_eligible_forward=true`를 보고한다.
6. preflight
   - 활성화된 시장의 portable artifact 존재, 형식, calibrator, `SHADOW` 상태를 확인한다.

## 현재 KR artifact

- model: `profit_path_shadow_KR_20260710T072509Z`
- runtime format: `portable_linear_v1`
- train rows: 24,993
- calibration rows: 3,501
- independent validation rows: 4,149
- validation AUC: 0.5377
- calibration ECE: 0.0523
- max PSI: 0.1121 (`healthy`)
- validation selected rows: 0
- validation net LCB: 없음
- backtest promotion eligible: `false`

해석: 분별력은 무작위보다 약간 높지만, 현재 수익 hurdle을 모두 만족한 독립 validation 표본이 없으므로 enforce 근거는 아직 없다. 기능을 폐기할 이유도 없지만, 실주문 권한을 줄 근거도 없다. 따라서 forward shadow로 실제 분포를 더 모으는 것이 맞다.

## 현재 US artifact

- model: `profit_path_shadow_US_20260710T074435Z`
- validation AUC: 0.6382
- calibration ECE: 0.0096
- max PSI: 0.1462 (`healthy`)
- validation selected rows: 0
- backtest promotion eligible: `false`

US는 분류 순위력이 KR보다 높지만, 비용 0.50% + 최소 net 0.25%를 합친 gross hurdle 0.75%를 넘는 기대값이 없었다. 따라서 enforce가 아니라 수익 근거 탐색용 forward shadow로 활성화한다.

## 운영 환경 호환성 검증

실제 봇 인터프리터 `C:\Users\Unknown\anaconda3\envs\upbit\python.exe`에는 sklearn과 joblib이 없다. 이를 발견한 뒤 런타임 artifact를 portable JSON으로 변경했다.

- sklearn raw probability: `0.42195023711905527`
- portable raw probability: `0.4219502371190552`
- 절대 차이: `5.55e-17`
- live upbit 환경 모델 로드/예측: 성공

따라서 연구 모델과 운영 추론의 계산 차이는 사실상 0이며, live 환경에 sklearn을 추가 설치할 필요가 없다.

## 검증 결과

- py_compile: 통과
- profit/path/gate 단위 묶음: 23 passed
- Path B/lifecycle/preflight 확장 회귀: 216 passed
- profit evidence preflight: `ok=true`
- 기존 forward prediction: 0건
- 현재 forward promotion eligible: `false`

## 운영 명령

```powershell
python tools/train_profit_path_shadow.py --markets KR
python tools/profit_evidence_preflight.py --markets KR,US
python tools/profit_path_forward_monitor.py --market KR
```

## 다음 정상 재시작 전 주의

현재 live bot은 2026-07-10 09:44 KST부터 실행 중이며, 변경 전 모듈과 runtime snapshot을 메모리에 보유하고 있다. 그래서 full live preflight의 유일한 FAIL은 `config.runtime_snapshot_drift`다. 이번 변경은 실행 중인 프로세스에 hot patch되지 않으며 다음 정상 재시작부터 적용된다. 안전상 작업 중인 live bot을 임의 재시작하지 않았다.

재시작 후 확인할 항목:

1. `PROFIT_EVIDENCE_SHADOW` 이벤트가 발생하는지 확인한다.
2. 60분이 지난 뒤 forward monitor의 `matched_n`이 증가하는지 확인한다.
3. unmatched matured 비율이 높으면 outcome linker의 허용 시간만 검토한다. 모델 threshold를 먼저 낮추지 않는다.
4. 최소 20세션 전에는 enforce 전환을 검토하지 않는다.

## 수익성 개선의 다음 단계

현재 전략의 고유성은 “좋은 종목 하나를 맞히는 모델”이 아니라, 같은 후보에 대해 `즉시진입 / pullback reclaim / VWAP reclaim / 포기`의 상대 기대값을 비용 차감 후 비교하는 데 있다. 다음 개선은 forward 표본을 기반으로 다음 순서로 진행한다.

1. KR path별 calibration을 분리해 하나의 확률축이 경로 차이를 뭉개는지 검증한다.
2. session regime별 최소 표본을 두어 6월/7월 tail 의존성을 분리한다.
3. `abstain`을 정식 행동으로 포함해 top-rank라도 기대 순수익이 음수면 진입하지 않는다.
4. 승격 후에도 처음에는 enforce가 아니라 size challenger로 제한한다.
