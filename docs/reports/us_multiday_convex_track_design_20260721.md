# US 멀티데이 볼록트랙 — 반사실 실측 + 설계 (2026-07-21)

재현: `python tools/us_multiday_counterfactual.py --start 2026-06-01` (읽기 전용)

## 실측 (US live closed 129건, 6/1~, 청산가 대비 추가수익률)

| 청산사유 | n | next_close | plus5_close | 판정 |
|---|---|---|---|---|
| PROFIT_LADDER | 13 | +0.79% | **+4.62% (med +6.64, 62%)** | ★최대 누수 — 조기익절 러너 잘림 |
| CLAUDE_PRICE_TARGET | 12 | +2.72% | **+3.09% (med +3.91, 67%)** | 러너 잘림(단 TARGET은 수익엔진 — 신중) |
| LOSS_CAP | 36 | +0.94% | +1.86% (med −0.49) | mean은 소수 반등 꼬리, median 음수 → 손절 유지 |
| WEAK_MFE | 6 | −1.27% | **−5.34%** | 컷 정당(제대로 버림) |
| CLAUDE_SELL | 7 | −2.14% | −2.89% | Claude 매도 정당 |
| 전체 | 129 | −0.07% | +1.01% | next_open +0.51% (오버나이트 드리프트 양수) |

## 결론 — 연장 가치는 "이익 계열"에만 있다

- **멀티데이 흑자 명제는 코호트 분리로만 참**: 이익 청산(LADDER·TARGET)을 연장하면 +3~5%p 추가, 손실 청산(WEAK_MFE·CLAUDE_SELL) 연장은 −3~5%p 반증. 전체 평균(+1.0%)으로 판단하면 평균의 오류.
- LOSS_CAP 연장은 median 음수 — **손절 완화 아님 재확인**(cap-widen 기각 이력과 정합).
- path_genome의 ride_candidate(확인된 승자 연장)와 정확히 같은 방향 — 두 관측이 교차 검증.

## 설계 (shadow, 주문경로 무접촉)

1. **관측(가동중)**: path_genome shadow(청산부) + 본 도구 주기 실행으로 ride 코호트 반사실 축적.
2. **전환 조건(운영자 승인 게이트)**: ride_candidate 코호트 n≥20에서 연장 반사실 net(+수수료·FX)이 유의하게 양수면, "이익 계열 청산 시 일부 수량 러너 carry"(LADDER tier 러너 uncapped의 멀티데이 확장)를 enforce 제안.
3. **금지**: 손실 포지션 보유연장·LOSS_CAP 완화 목적 사용. TSMOM sleeve(별도 shadow, 7/8 inception)와 혼동 금지.

한계: FX 미반영·생존 청산만·n(reason별) 소표본. med/pos 병기로 꼬리 왜곡 통제.
