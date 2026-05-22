# RSI 임계값 근거 연구 정리
> 작성일: 2026-04-02 | "마음이 급할 때 먼저 읽을 것"

---

## 결론부터

**RSI 30 → 32로 올리고 싶은 건 대부분 급한 마음에서 나온다.**
연구 결과는 반대 방향을 가리킨다.

---

## RSI 30의 근거

없다. Wilder가 1978년 원자재 선물 시장에서 **직관적으로** 정한 숫자.
통계적 최적화 없음. 논문도 없음. 달의 28일 주기 절반(14)에서 파생된 휴리스틱.

---

## 핵심 연구 결과

### 1. RSI 30 vs 32 vs 35 — 의미 없는 차이
- 이 범위 내 직접 비교 논문 없음
- 노이즈 수준의 차이 (통계적으로 유의미하지 않음)
- **의미 있는 차이는 RSI 20~25 vs RSI 30 수준**

### 2. 더 극단적 임계값이 성능이 더 좋다
| 설정 | 결과 | 출처 |
|---|---|---|
| 14기간/30/70 (Wilder 원본) | 72% 승률, S&P 대비 아웃퍼폼 | Bulkowski |
| 16기간/20/80 | 위보다 우수한 효율 | Bulkowski |
| RSI(2) / 5~10 임계값 | 14기간/30/70 압도 | Connors |

→ **임계값이 극단적일수록 건당 엣지 상승, 신호 수 감소**

### 3. 핵심: 약세장에서 RSI 분포 자체가 내려앉는다

| 장세 | RSI 활동 구간 | 의미 |
|---|---|---|
| 강세장 | 40~90 (40이 지지선) | RSI 30 = 극단적 과매도 |
| 약세장 | 10~60 (60이 저항선) | RSI 30 = 덜 극단적 신호 |

> **약세장의 RSI 30 ≈ 강세장의 RSI 20~25**
>
> 약세장에서 RSI 30으로 낮추면 이미 "관대한" 기준이다.
> 진정한 약세장 동등 임계값은 RSI 25 이하.

### 4. 평균회귀는 약세장에서 오히려 잘 작동한다
- 2000~2002 닷컴 버블 붕괴: S&P 대비 크게 아웃퍼폼
- 2008~2009 금융위기: 동일
- 2020 코로나 급락: 동일
- 단, **낙하하는 칼 잡기 위험 공존** — 진입 후 계속 빠지는 구간 존재

### 5. 약세장에서의 RSI 사용 권고 (실무)
- 변동성 적응형: **고변동성(약세장) = RSI 25/75, 저변동성 = RSI 35/65**
- 트렌드 필터 병행: 200일 MA 위/아래로 게이팅
- 단기 RSI(2~5) + 극단 임계값 조합이 장기 RSI보다 엣지 명확

---

## 우리 시스템 적용

```python
# mean_reversion.py 현재 설정
"MILD_BEAR":    rsi_thr=30, bb_thr=18
"CAUTIOUS_BEAR":rsi_thr=30, bb_thr=17  # ← 이미 약세장 기준 관대한 편
"NEUTRAL":      rsi_thr=32, bb_thr=20
```

| 충동 | 연구 판단 |
|---|---|
| CAUTIOUS_BEAR에서 신호 안 나옴 → RSI 32로 올리고 싶다 | ✗ 근거 없음. 오히려 역방향 |
| RSI 30이 너무 빡빡한 것 아닌가 | ✗ 약세장에선 30도 이미 관대한 기준 |
| 진짜 엣지를 높이고 싶다 | RSI 25 이하로 낮추는 게 연구 방향 (신호 수 감소 감수) |

**카카오(32.9), 현대차(32.3)가 RSI 30에 안 걸리는 건 시스템이 올바르게 작동하는 것이다.**

---

## 신호가 없을 때 체크리스트

신호 없음 → 뭔가 잘못됐다고 느껴질 때, 이 순서로 확인할 것:

1. **현재 모드 확인** — CAUTIOUS_BEAR/DEFENSIVE는 원래 신호 적음
2. **실제 RSI 값 확인** — 30에 얼마나 근접한지
3. **OHLC/거래량 이슈 확인** — 기술적 버그 먼저 제거
4. **장세 자체 확인** — VIX, VKOSPI, 환율이 실제로 위험한지
5. **마지막으로 임계값 조정 검토** — 위 4가지 다 확인 후

---

## 참고 문헌
- Wilder, J.W. (1978). *New Concepts in Technical Trading Systems*
- Bulkowski, T. — RSI Review, [thepatternsite.com](https://www.thepatternsite.com/RSI.html)
- Connors, L. — *Short Term Trading Strategies That Work* (RSI(2) 연구)
- Arthur Hill (StockCharts, 2016) — Mean Reversion System Test, S&P 500 1990~2016
- QuantifiedStrategies — RSI Mean Reversion Strategy (QQQ)
- Exploratio Journal — Technical Trading and Higher-Order Risks: RSI & MA Strategies
- PMC/NIH — Effectiveness of RSI Signals in Timing the Cryptocurrency Market (2022)
- Anderson & Li — *An Investigation of the Relative Strength Index* (Semantic Scholar)
