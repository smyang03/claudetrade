# Profit-path early-tier relabel walk-forward — 2026-07-15

모든 신호는 진입 시점 피처만 사용하고, expanding train 뒤 하루 purge 후 다음 날짜 블록을 평가했다. ordered bar가 없어 stop과 tier가 모두 관측되면 stop-first로 계산한 보수적 합성 순익을 병기한다.

| 시장/arm | N | tier 성공률 | 60분 net 평균 | 합성 net 평균 | ex-top3 합 | 양수일 비율 |
|---|---:|---:|---:|---:|---:|---:|
| US top1_per_day_upper_bound | 19 | 21.1% | -1.474% | -1.196% | -32.58%p | 21.1% |
| US top3_per_day_upper_bound | 57 | 21.1% | -0.833% | -0.943% | -57.63%p | 26.3% |
| US top5_per_day_upper_bound | 95 | 22.1% | -0.786% | -0.866% | -84.85%p | 26.3% |
| US sequential_p70_cap3 | 57 | 28.1% | -0.591% | -0.703% | -48.74%p | 42.1% |
| US sequential_p80_cap3 | 55 | 29.1% | -0.537% | -0.659% | -41.74%p | 42.1% |
| US sequential_p90_cap3 | 42 | 28.6% | -0.996% | -1.040% | -51.44%p | 27.8% |
| KR top1_per_day_upper_bound | 21 | 23.8% | +0.352% | -0.952% | -24.29%p | 47.6% |
| KR top3_per_day_upper_bound | 63 | 25.4% | +0.277% | -0.944% | -40.53%p | 42.9% |
| KR top5_per_day_upper_bound | 105 | 25.7% | -0.076% | -0.991% | -72.94%p | 47.6% |
| KR sequential_p70_cap3 | 57 | 21.1% | -0.572% | -1.169% | -73.82%p | 36.8% |
| KR sequential_p80_cap3 | 55 | 30.9% | +0.253% | -0.804% | -40.27%p | 36.8% |
| KR sequential_p90_cap3 | 22 | 22.7% | -0.092% | -0.928% | -41.81%p | 54.5% |

`topN_per_day_upper_bound`는 그날 뒤에 올 후보를 아는 비실행 상한이라 승격 대상이 아니다. 실행 후보는 timestamp 순으로 확률기준을 넘는 첫 3개만 받는 `sequential_*` arm이다.

이 결과는 실제 주문 권한이 없는 `SHADOW_ONLY` 발견 결과다. 합성 순익과 실제 60분 net이 동시에 양수이고, 상위 3건 제거·날짜 블록에서도 살아남는 arm만 다음 forward 후보가 된다.
