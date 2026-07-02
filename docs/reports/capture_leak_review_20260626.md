# Capture Leak 리뷰 — MFE 백필 후 항목③ 결판 (2026-06-26)

> 토론 #4(상류/극대화)에서 항목③(우측꼬리 capture)이 "유망하나 라이브 MFE 데이터 희소(N=2~7)로 측정불가"로 미결.
> 운영자 "진행" → MFE 백필(기존 `mfe_backfill_yf` 조인)로 커버리지 21%→80% 확보 후 재측정.
> 도구: `tools/capture_leak_monitor.py` (read-only, 신규 API 0 — 6/13 yfinance 백필 재활용).

---

## 1. 측정 복구 — MFE 커버리지 21% → 80%
- v2_learning_performance.mfe_pct는 6/19 배선 이후만(라이브 67/310). 
- `mfe_backfill_yf`(yfinance 5m 추정, 6/13 백필 222행)를 v2_decision_id로 조인 → **247/310 (80%)**. 신규 API 호출 없음.
- 잔여 63건('none')은 pre-백필 + no_bars. 필요 시 fresh yfinance 실행으로 채울 수 있음(API 호출 — 별도 승인).

## 2. close_reason별 capture (결합 MFE, 양수 MFE만)
| close_reason | N | 실현 | MFE | giveback | capture |
|---|---:|---:|---:|---:|---:|
| CLAUDE_PRICE_TARGET | 18 | +5.20% | +5.28% | +0.08pp | **95%** |
| CLAUDE_SELL | 13 | +2.25% | +4.08% | +1.83pp | 47% |
| **PROFIT_LADDER** | 29 | +0.55% | +3.15% | **+2.60pp** | **2%** |
| **PRE_CLOSE** | 33 | +1.26% | +3.43% | **+2.17pp** | **3%** |
| **TRAILING_STOP** | 7 | +2.29% | +7.37% | +5.09pp | 11% |
| (손절 경로 LOSS_CAP/STOP/MANUAL) | — | 음수 | +1~3% | — | 음수 |

## 3. 결판 — 항목③에 실측 capture leak 존재
**진짜 leak(실현 양수인데 MFE 못 잡음):**
- **PROFIT_LADDER N=29:** +3.15% MFE까지 갔다가 +0.55%만 — 반납 2.60pp/건. ladder 조기분할이 러너를 끊음.
- **PRE_CLOSE N=33:** +3.43% MFE → +1.26%. 세션 종료 강제청산이 인트라데이 러너를 끊음.
- **TRAILING_STOP N=7:** +7.37% MFE → +2.29%(반납 5.09pp). 표본 작으나 가장 큰 per-trade 반납.
- 합 PROFIT_LADDER+PRE_CLOSE **N=62, 평균 ~2.4pp 반납.**

**대조:** PRICE_TARGET은 capture 95%(타깃이 고점 근처) — *타깃이 정의되면* 거의 다 잡는다 = ladder/pre_close에 개선 여지가 있다는 방증.

## 4. 정직 caveat (절대값 과신 금지)
1. **MFE는 yfinance 5m 추정 상단** → 실제 체결 가능가 아님. giveback 일부는 un-capturable. 경로 *간 상대비교*로만.
2. **생존편향:** TARGET은 승자만 그 경로로 마감(capture 95% 당연). ladder/pre_close는 혼합.
3. **양날:** ladder/pre_close는 *반전 전 이익을 잠그는* 장치. 트레일 넓히면 일부는 더 반납(반전손실↑). net 효과 미확정.

## 5. 개선 방향 (방향만, 코드는 승인 후)
| # | 방향 | 형태 | 근거 |
|---|---|---|---|
| 1 | PROFIT_LADDER 분할 완화 / 트레일 폭 확대 (러너 더 타기) | **shadow A/B** | N=29 반납 2.60pp, capture 2% vs TARGET 95% |
| 2 | PRE_CLOSE 인트라데이 강제청산 → 멀티데이 carry 검토(PATHB_INTRADAY_ONLY=false 이미 허용) | shadow | N=33 반납 2.17pp, 세션종료가 러너 끊음. 단 오버나잇 갭 위험 |
| 3 | 잔여 63건 fresh yfinance MFE 백필 | 측정(API) | 커버리지 80→~95%, 표본 확대 |

**안 할 것:** 즉시 enforce(양날 — 트레일 넓히면 반전손실↑). loss_cap 좌측꼬리(A1 처리중) 고정이 선행. MFE 추정 절대값으로 청산 즉시 변경 금지.

## 6. 한 줄 결론
MFE 백필(21%→80%)로 항목③이 처음 측정 가능해졌고, **PROFIT_LADDER(N=29)·PRE_CLOSE(N=33)가 +3% MFE를 +0.5~1.3%로만 잡는 ~2.4pp 반납 leak이 실측**됐다 — TARGET은 95% capture라 개선 여지의 방증. 단 MFE는 5m 추정이고 트레일 완화는 반전손실 양날이라, **즉시 변경이 아니라 shadow A/B로 giveback↓ vs 반전손실↑를 재는 것**이 다음 단계다.
