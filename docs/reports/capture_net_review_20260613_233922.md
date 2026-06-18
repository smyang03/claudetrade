# Capture / Net 성과 리뷰 (2026-06-13T23:39:22)

- 대상: closed=225건, runtime_mode=live
- 수수료 가정(왕복%): {'US': 0.5, 'KR': 0.5}
- selection runup 매칭: 2949건

## 시장별 (gross vs net)
| 시장 | n | 승률 | gross평균 | gross합 | net평균 | net합 | net승률 | net손익분기통과 | PF(net) |
|---|--|--|--|--|--|--|--|--|--|
| US | 180 | 45.0% | +0.45% | +80.2% | -0.05% | -9.8% | 36.7% | 36.7% | 0.96 |
| KR | 45 | 28.9% | -0.98% | -44.0% | -1.48% | -66.5% | 20.0% | 20.0% | 0.33 |

## Capture (진입종목 runup 대비 실현)
| 시장 | n | 실현평균(gross) | runup_3d평균 | capture |
|---|--|--|--|--|
| US | 119 | +0.66% | +9.02% | 7.3% |
| KR | 34 | -1.02% | +17.54% | -5.8% |

## US 청산경로별 (net 기준 정렬)
| 청산경로 | n | net평균 | net합 | net승률 | capture |
|---|--|--|--|--|--|
| CLOSED_CLAUDE_PRICE_TARGET | 16 | +4.95% | +79.2% | 100.0% | 59.5% (n=12) |
| CLOSED_CLAUDE_PRICE_PRE_CLOSE | 34 | +0.94% | +32.0% | 47.1% | 22.6% (n=25) |
| CLOSED_CLAUDE_SELL | 9 | +2.42% | +21.8% | 88.9% | 88.9% (n=6) |
| CLOSED_TRAILING_STOP | 3 | +3.5% | +10.5% | 66.7% | 134.7% (n=2) |
| CLOSED_PROFIT_LADDER | 27 | +0.34% | +9.1% | 48.1% | 12.2% (n=17) |
| CLOSED_PROFIT_FLOOR | 2 | -0.13% | -0.3% | 0.0% | 2.6% (n=1) |
| CLOSED_MFE_BREAKEVEN | 2 | -0.5% | -1.0% | 0.0% | 7.1% (n=1) |
| CLOSED_USER_MANUAL | 13 | -0.65% | -8.5% | 30.8% | -18.5% (n=8) |
| CLOSED_CLAUDE_PRICE_STOP | 13 | -0.8% | -10.4% | 30.8% | -4.8% (n=5) |
| CLOSED_HARD_STOP | 15 | -0.78% | -11.7% | 20.0% | -21.3% (n=10) |
| CLOSED_AUDITED_BROKER_SELL | 5 | -4.28% | -21.4% | 0.0% | -100.2% (n=4) |
| CLOSED_LOSS_CAP | 41 | -2.66% | -109.2% | 0.0% | -96.8% (n=28) |

## KR 청산경로별 (net 기준 정렬)
| 청산경로 | n | net평균 | net합 | net승률 | capture |
|---|--|--|--|--|--|
| CLOSED_CLAUDE_PRICE_TARGET | 1 | +7.44% | +7.4% | 100.0% | 546.8% (n=1) |
| CLOSED_TRAILING_STOP | 5 | +0.99% | +5.0% | 40.0% | 17.1% (n=3) |
| CLOSED_PROFIT_LADDER | 1 | +2.68% | +2.7% | 100.0% | - |
| CLOSED_CLAUDE_PRICE_STOP | 1 | +2.37% | +2.4% | 100.0% | - |
| CLOSED_PROFIT_FLOOR | 3 | -0.19% | -0.6% | 0.0% | 4.1% (n=3) |
| CLOSED_TIME_STOP | 1 | -0.82% | -0.8% | 0.0% | - |
| CLOSED_CLAUDE_PRICE_PRE_CLOSE | 9 | -0.51% | -4.6% | 44.4% | 19.1% (n=6) |
| CLOSED_HARD_STOP | 1 | -10.32% | -10.3% | 0.0% | -70.4% (n=1) |
| CLOSED_USER_MANUAL | 11 | -2.66% | -29.2% | 0.0% | -91.2% (n=10) |
| CLOSED_LOSS_CAP | 12 | -3.2% | -38.4% | 0.0% | -84.1% (n=10) |

## 보유시간 버킷별 net (ALL)
| 버킷 | n | net평균 | net승률 |
|---|--|--|--|
| 0-30분 | 39 | -2.09% | 15.4% |
| 30분-2시간 | 66 | -1.04% | 19.7% |
| 2-6시간 | 60 | +0.51% | 50.0% |
| 6-24시간 | 34 | +0.29% | 38.2% |
| 1일+ | 22 | +1.67% | 54.5% |
