# Profit Evidence 1·2단계 DB 검증 결과 (2026-07-10)

상태: **계약 replay 완료 / purged walk-forward 완료 / 라이브 승격 없음**

후속 개선: [Profit Trainer v2 경로 라벨 검증](./validation_profit_trainer_v2_path_20260710.md)

## 결론

1. 새 gate 계약은 기존 DB에서 정확히 replay된다.
2. 과거 `profit_evidence`가 0건이므로 strict enforce의 정확한 결과는 신규진입 0건이다.
3. 과거 point-in-time 후보 feature로 별도 수익예측기를 만들어 walk-forward한 결과, logistic과 random forest 모두 승격 창이 0개였다.
4. 따라서 현재 기본 `shadow`가 맞고, 수익예측 `enforce` 승격 근거는 아직 없다.

## 1. 저장 계약 replay

도구:

```powershell
python tools/profit_evidence_db_replay.py --mode shadow
python tools/profit_evidence_db_replay.py --mode enforce
```

대상은 종목-일 최초 prompt 후보와 1일 forward outcome을 결합한 KR 2,070건, US 2,430건이다.

| 시장 | 후보 | 저장 profit evidence | shadow | enforce |
|---|---:|---:|---:|---:|
| KR | 2,070 | 0 | 모두 허용, 2,070 would-block | 허용 0 |
| US | 2,430 | 0 | 모두 허용, 2,430 would-block | 허용 0 |

후보 baseline 1일 forward:

| 시장 | 평균 | 중앙값 | 양수 비율 | PF proxy |
|---|---:|---:|---:|---:|
| KR | -1.706% | -2.566% | 35.1% | 0.608 |
| US | -0.420% | -0.223% | 47.2% | 0.805 |

이 결과는 broker net이 아니라 후보 가격 대비 다음 거래일 종가 수익 proxy다.

## 2. 연구용 purged walk-forward

도구:

```powershell
python tools/profit_evidence_walkforward.py --classifier logistic
python tools/profit_evidence_walkforward.py --classifier random_forest
```

분할은 다음 순서만 허용했다.

```text
expanding train
  -> calibration fit
  -> 독립 validation
  -> 1 session purge
  -> 다음 session test
```

1일 label의 `target_at`이 다음 거래일임을 DB에서 확인했으므로 test 직전 한 세션을 purge했다. calibration에서 isotonic을 학습하고 별도 validation에서 AUC, ECE, 비용 후 순수익 하한을 평가했다.

사용 feature:

- 가격, 등락률, 거래량비, 거래대금
- prompt rank, 고점 대비 위치, 후보 age
- 뉴스 점수/건수, raw screener score
- primary bucket, 유동성, 시장구분, 추천전략, 후보출처

제외 feature:

- `candidate_quality_score`
- `trainer_prompt_score`
- Claude action/confidence
- 미래 execution/fill/exit field

비용 및 label hurdle:

- KR: 비용 0.21% + 최소 기대 net 0.25% = gross label hurdle 0.46%
- US: 비용 0.50% + 최소 기대 net 0.25% = gross label hurdle 0.75%

승격 조건:

- 독립 validation AUC >= 0.52
- ECE <= 0.10
- validation 선택 표본 >= 20
- 선택 표본 실제 net bootstrap 95% 하한 > 0
- PSI <= 0.25
- evidence 확률·expected net·uncertainty·OOD gate 통과

## 3. Logistic 결과

| 시장 | test 창 | test 후보 | 승격 창 | enforce 허용 | 승격 전 raw 후보 성과 |
|---|---:|---:|---:|---:|---:|
| KR | 13 | 872 | 0 | 0 | 1건, -10.903% |
| US | 11 | 811 | 0 | 0 | 12건, 평균 -2.819%, PF 0.302 |

Validation AUC 범위:

- KR: 0.445~0.534
- US: 0.424~0.554

비용 후 순수익 하한이 양수이면서 나머지 승격 조건까지 만족한 창은 없었다.

## 4. Random Forest 결과

| 시장 | test 창 | test 후보 | 승격 창 | enforce 허용 | 승격 전 raw 후보 성과 |
|---|---:|---:|---:|---:|---:|
| KR | 13 | 872 | 0 | 0 | 3건, 평균 -5.785%, PF 0.150 |
| US | 11 | 811 | 0 | 0 | 26건, 평균 -2.635%, PF 0.213 |

Validation AUC 범위:

- KR: 0.427~0.586
- US: 0.400~0.630

US 2026-06-23 test 창 직전 validation은 AUC 0.630, net LCB +0.520%였지만 ECE 0.125로 기준을 실패했다. ECE 기준을 완화해 이 창을 승격했다고 가정할 경우 test의 raw 후보 9건은 평균 -2.574%, PF 0.040이었다. calibration gate가 잘못된 승격을 실제로 막은 사례다.

## 5. 시뮬레이터 버그 발견 및 수정

초기 구현은 isotonic calibration을 학습한 같은 구간에서 ECE와 승격 성능을 측정해 ECE가 0으로 과도하게 좋아졌다. 이를 발견해 다음처럼 수정했다.

```text
이전: train -> calibration(학습+평가) -> purge -> test
수정: train -> calibration-fit -> validation-eval -> purge -> test
```

수정 후 ECE는 KR 최대 0.263, US 최대 0.318로 현실적인 수준이 되었고 승격 창은 0개로 판정됐다. 이 문제는 라이브 gate가 아니라 새 연구 시뮬레이터의 검증 누수였으며 수정 후 chronology 단위 테스트를 추가했다.

## 6. DB가 지원하는 다음 연구

`candidate_counterfactual_paths` coverage:

- KR immediate close 11,637, wait30 9,935, wait60 9,193
- US immediate close 14,707, wait30 13,887, wait60 12,868

따라서 다음에는 단순 1일 종가 대신 실제 진입 경로별 60분/종가/MFE/MAE label을 만들 수 있다. 다만 모든 표본에 target-first/stop-first 순서와 broker 비용이 있는 것은 아니므로 exact execution backtest로 과장하면 안 된다.

## 최종 판정

```text
계약 replay: PASS
shadow 계측: READY
profit model enforce: NOT READY
현재 기본 mode: shadow 유지
```

현재 시스템이 신규 수익예측 모델을 `enforce`로 거절하는 것은 거래기회를 놓친 것이 아니라, 비용 후 엣지가 검증되지 않은 모델이 자본에 접근하지 못하게 한 정상 동작이다.
