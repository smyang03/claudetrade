# US swing day_losers 정렬 검증 + calibration (2026-08-07)

교재 13672행 (2024-07-23~2026-04-02), day_losers 프록시(chg<=-5.0) 807행. seeds=[20260710, 20260711, 20260712], cost=0.5, horizon=5, purge=5.

## [1] 분포 정렬 비교 (세션당 top-k 평균 net, %)

A. 전체학습->전체평가: top1 {'sessions': 293, 'mean_net_pct': 1.767, 'median_net_pct': -1.36, 'win_rate': 0.468, 'profit_factor': 1.44, 'lcb5_pct': -0.312}
   top3 {'sessions': 293, 'mean_net_pct': 1.067, 'median_net_pct': -0.238, 'win_rate': 0.481, 'profit_factor': 1.43, 'lcb5_pct': -0.321}

B. 전체학습->day_losers평가(라이브 미러): top1 {'sessions': 195, 'mean_net_pct': 0.778, 'median_net_pct': -1.009, 'win_rate': 0.477, 'profit_factor': 1.16, 'lcb5_pct': -1.625}
   top3 {'sessions': 195, 'mean_net_pct': 1.094, 'median_net_pct': 0.699, 'win_rate': 0.518, 'profit_factor': 1.28, 'lcb5_pct': -0.935}

C. day_losers학습->day_losers평가: top1 {'sessions': 195, 'mean_net_pct': 0.937, 'median_net_pct': -1.257, 'win_rate': 0.467, 'profit_factor': 1.2, 'lcb5_pct': -1.336}
   top3 {'sessions': 195, 'mean_net_pct': 0.973, 'median_net_pct': -0.148, 'win_rate': 0.497, 'profit_factor': 1.25, 'lcb5_pct': -0.98}
   (학습 표본 807행 — 얇음. 판정보다 방향 참고)

## [2] 확률 calibration (walk-forward 시험구간, target=net>=0.25)

### 전체 시험행
  {'bin': '[0.00,0.45)', 'n': 5609, 'predicted_mean': 0.359, 'realized_rate': 0.47, 'mean_net_pct': 0.194}
  {'bin': '[0.45,0.50)', 'n': 1630, 'predicted_mean': 0.475, 'realized_rate': 0.444, 'mean_net_pct': 0.124}
  {'bin': '[0.50,0.55)', 'n': 1442, 'predicted_mean': 0.525, 'realized_rate': 0.46, 'mean_net_pct': 0.624}
  {'bin': '[0.55,0.60)', 'n': 1115, 'predicted_mean': 0.573, 'realized_rate': 0.483, 'mean_net_pct': 0.845}
  {'bin': '[0.60,0.65)', 'n': 692, 'predicted_mean': 0.623, 'realized_rate': 0.49, 'mean_net_pct': 0.377}
  {'bin': '[0.65,1.00)', 'n': 949, 'predicted_mean': 0.713, 'realized_rate': 0.491, 'mean_net_pct': 0.232}
  {'bin': 'TOTAL', 'n': 11437, 'brier': 0.2642, 'base_rate': 0.469}

### day_losers 프록시 행
  {'bin': '[0.00,0.45)', 'n': 271, 'predicted_mean': 0.371, 'realized_rate': 0.494, 'mean_net_pct': 1.475}
  {'bin': '[0.45,0.50)', 'n': 94, 'predicted_mean': 0.474, 'realized_rate': 0.468, 'mean_net_pct': 0.621}
  {'bin': '[0.50,0.55)', 'n': 91, 'predicted_mean': 0.527, 'realized_rate': 0.549, 'mean_net_pct': 3.742}
  {'bin': '[0.55,0.60)', 'n': 64, 'predicted_mean': 0.576, 'realized_rate': 0.516, 'mean_net_pct': 1.975}
  {'bin': '[0.60,0.65)', 'n': 66, 'predicted_mean': 0.625, 'realized_rate': 0.561, 'mean_net_pct': 2.274}
  {'bin': '[0.65,1.00)', 'n': 99, 'predicted_mean': 0.73, 'realized_rate': 0.354, 'mean_net_pct': -1.106}
  {'bin': 'TOTAL', 'n': 685, 'brier': 0.2746, 'base_rate': 0.486}

## [3] forward 원장 교차 (라이브 신호, 5d 원장 회계 — 참고용 소표본)
  {'bin': '[0.00,0.45)', 'n': 1, 'predicted_mean': 0.426, 'realized_rate': 0.0, 'mean_net_pct': -44.588}
  {'bin': '[0.45,0.50)', 'n': 7, 'predicted_mean': 0.484, 'realized_rate': 0.429, 'mean_net_pct': 3.075}
  {'bin': '[0.50,0.55)', 'n': 16, 'predicted_mean': 0.521, 'realized_rate': 0.625, 'mean_net_pct': 6.253}
  {'bin': '[0.55,0.60)', 'n': 11, 'predicted_mean': 0.576, 'realized_rate': 0.182, 'mean_net_pct': -4.323}
  {'bin': '[0.60,0.65)', 'n': 12, 'predicted_mean': 0.618, 'realized_rate': 0.333, 'mean_net_pct': -3.745}
  {'bin': '[0.65,1.00)', 'n': 18, 'predicted_mean': 0.716, 'realized_rate': 0.222, 'mean_net_pct': -7.253}
  {'bin': 'TOTAL', 'n': 65, 'brier': 0.3162, 'base_rate': 0.354}

판정 메모: 이 리포트는 계측이다 — 허들·모델 변경은 운영자 결정+재봉인 절차로만.

## 해석 (계측 결과 요약 — 판정 아님)

1. **회계 한계 먼저**: 이 리포트의 net은 고정 5일 보유 회계다. 실거래 계약(TP12/SL25/D5)
   회계가 아니므로 레인 성과와 직접 비교 금지(−239%p 교훈). 모델 변별력·확률 품질 진단 전용.
2. **분포 정렬(B vs C)**: day_losers 전용 학습(C)이 라이브 미러(B)보다 top1 소폭 우위
   (+0.94 vs +0.78)나 표본 807행으로 얇아 결론 불가. 재봉인 때 소스 라벨 정본 부착 후 재검증.
3. **확률 무보정 확인**: 전체 Brier 0.264는 상수 예측(base 0.469 → 0.249)보다 나쁘다.
   구간별 실측 성공률이 45~49%에 평평 — predict_proba 절대값은 성공확률이 아니다.
4. **고확률 역방향 (가장 중요한 발견)**: day_losers에서 prob>=0.65 구간이 실측 35.4%,
   평균 net −1.11%로 역방향. 반면 0.50~0.65 구간은 +2~3.7%. forward 원장(n=65 소표본)도
   같은 모양(0.50~0.55 최고 +6.25%, >=0.55 구간들 음수). **0.55 허들이 좋은 구간의 하단을
   자르고 나쁜 구간(>=0.65)은 통과시키는 형태일 가능성.** 단 두 소스 모두 확정 표본이
   아니며, 허들 변경은 30건 판정 + A2 counterfactual 누적 + 운영자 결정으로만.

## 후속 (계측 유지)

- A2 뷰(차단 rank1 사후 성과)가 이 가설의 forward 검증 축 — 이미 가동 중.
- 재봉인(8월말) 때: as-of 계약 + day_losers 소스 라벨 + 이 리포트 재실행이 한 묶음.
