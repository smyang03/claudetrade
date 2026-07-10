# US Profit Path Shadow 검증 (2026-07-10)

## 결정

US 전용 profit-path 모델을 학습하고 forward shadow 관측을 활성화한다. 실주문 통제(enforce)는 활성화하지 않는다.

## 독립 validation

- model: `profit_path_shadow_US_20260710T074435Z`
- train rows: 40,678
- calibration rows: 5,389
- validation rows: 6,414
- validation dates: 2026-07-06 ~ 2026-07-08
- validation AUC: 0.6382
- calibration ECE: 0.0096
- max PSI: 0.1462 (`healthy`)
- validation selected rows: 0
- promotion eligible: `false`

분류 순위력은 KR보다 높지만, 비용 차감 기대수익 hurdle을 넘긴 표본은 없다.

## 수익 기준 분해

US 비용 0.50%와 최소 기대 순수익 0.25%를 합쳐 기대 gross 0.75% 이상을 요구했다. 모델의 validation 보정 기대 gross 최대는 0.6965%여서 hurdle에 미달했다.

일별 예측 top-rank 실제 순수익:

| Cohort | N | 평균 net | 승률 | PF | bootstrap LCB |
|---|---:|---:|---:|---:|---:|
| top1/day | 3 | -0.164% | 33.3% | 0.61 | -0.841% |
| top3/day | 9 | -0.416% | 44.4% | 0.52 | -1.349% |
| top5/day | 15 | -0.821% | 46.7% | 0.38 | -1.987% |
| top10/day | 30 | -0.588% | 46.7% | 0.54 | -1.603% |
| top20/day | 60 | -0.382% | 50.0% | 0.62 | -0.906% |

경로별 best-predicted ticker/day 표본도 모두 평균 net이 음수였다.

- immediate: -1.072%
- pullback reclaim: -0.957%
- volume surge: -0.283%
- VWAP reclaim: -0.634%

`volume_surge`가 상대적으로 덜 나쁘지만 수익 전략으로 승격할 근거는 아니다.

## 해석

AUC만 높다고 수익 통제가 가능한 것은 아니다. 현재 모델은 target/stop 라벨의 상대 순위는 일부 구분하지만, 비용을 이길 만큼 큰 양의 return tail을 안정적으로 선별하지 못한다. 여러 경로 중 최대 확률을 고르는 과정에서 생기는 winner's curse도 forward에서 확인해야 한다.

따라서 US는 다음 목적으로만 shadow 활성화한다.

1. 동일 시점에서 immediate/pullback/VWAP/volume 경로의 상대 예측을 저장한다.
2. 60분 outcome을 연결해 실제 비용 차감 net을 측정한다.
3. 최소 20세션·60 matched 표본 전에는 승격을 검토하지 않는다.
4. AUC뿐 아니라 net LCB 양수, PF>1, ECE<=0.10을 함께 요구한다.
5. threshold를 낮춰 억지로 거래 수를 만드는 행위는 금지한다.

## 활성 설정

```text
PROFIT_PATH_SHADOW_ENABLED_US=true
PROFIT_PATH_MODEL_PATH_US=state/models/profit_path_US.json
PROFIT_EVIDENCE_GATE_MODE=shadow
```

현재 실행 중인 live 프로세스에는 hot reload되지 않으며 다음 정상 재시작부터 적용된다.
