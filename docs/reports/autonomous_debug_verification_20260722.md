# 자율 디버깅 검증 보고 — 2026-07-22

US 세션(07-21 22:30 ~ 07-22 05:00) 동안 진행. 실거래 진입 0건, 수익 0.
아래는 그 세션에서 실측·검증한 내용과 판정이다.

## 1. 결론 요약

**오늘 찾은 "수익 레버" 5개 중 4개는 검증에서 반증되어 롤백했다.**
실제로 돈이 되는 것은 새 레버가 아니라 **이미 있던 설계(즉시매수)를 막던 버그**였다.

| 항목 | 내부 근거 | 독립 검증 | 판정 |
|---|---|---|---|
| 즉시매수 버그 수정 | BUY_READY 4건 전멸 | 반사실 2h **+2.89%**, TARGET 1건 | ✅ **유효** |
| 일일상한 5건 | +26.52%p | 외부 404신호에서 상한 5 최적 | ✅ 유지 |
| 국면게이트 | +59.05%p | 세션단위 p=0.102 | 🔴 shadow |
| 조기고점 정리 | +52.28%p (p=0.0006) | 외부 21,465건 차이 −0.0012%p | 🔴 shadow |
| 요일 게이트 | +39.55%p | forward 세션단위 +0.508%(p=0.486) | 🔴 shadow |
| 눌림존 완화 | (진입 확대 기대) | 거부 건 샀다면 2h **−1.075%** | 🔴 기각 |
| 즉시매수 기준 완화 | (진입 확대 기대) | 1h **−0.158%** | 🔴 기각 |

## 2. 핵심 발견 — 진입 확대의 답은 즉시매수뿐이다

세 갈래를 같은 방식(반사실 + gross)으로 재보면 방향이 갈린다.

```
judge 선별 즉시매수  →  2h +2.89%   (4/4 양수, 손절 0건, HUT TARGET +7.50%)
기준 완화            →  1h −0.158%  (judge가 "약하다"고 본 건은 실제로 약했다)
눌림 완화            →  2h −1.075%  (무작정 추격은 나쁘다)
```

즉 **judge의 선별이 작동하고 있다.** "더 사게 만들자"는 방향은 전부 반증됐고,
유일하게 유효한 것은 judge가 이미 사겠다고 한 건이 버그로 죽지 않게 하는 것이었다.

### 죽고 있던 버그 (수정 완료)
- `_apply_single_symbol_judge_result`가 재normalize 시 `market_regime`을 넘기지 않아
  BUY_READY가 fail-closed로 강등 → WAIT_RECHECK (WDC 2건)
- `validate_immediate_buy_plan`이 현재가를 `features.current_price`에서만 읽어,
  candidate에 가격이 있는데도 `missing_current_price_for_immediate_buy`로 탈락 (AMAT·HUT 2건)

## 3. 검증 방법론 (재사용)

오늘 세 번 "내부에서 유의"했다가 독립 검증에서 뒤집혔다. 순서를 고정한다.

1. **세션 단위로 볼 것** — 같은 날 거래는 독립이 아니다. 거래 단위 평균은 특정일
   쏠림에 오염된다(요일 게이트가 이 함정이었다: 거래단위 −1.72% vs 세션단위 +0.508%).
2. **다중비교 보정** — 25개·64개 조합에서 최적을 고르면 당연히 좋아 보인다.
3. **외부 독립 표본으로 재현** — 내부 p=0.0006도 외부에서 무너졌다.
4. **단, 판정은 우리 net으로** — 외부 gross로 우리 레버를 판정하면 안 된다.
   우리는 추격 전략이라 약세장 진입 승률 0%인데, 외부 일반종목은 +0.777%로 정반대다.

### 남긴 도구
```
tools/early_peak_rule_external_validation.py   조기고점 룰 외부 검증
tools/daily_cap_external_validation.py         일일상한 외부 검증
tools/regime_gate_external_validation.py       국면게이트 외부 검증
tools/pullback_rule_counterfactual.py          눌림존 거부 건 반사실
tools/buy_ready_counterfactual.py              즉시매수 반사실
tools/gate_effect_review.py                    라이브 발동·진입·net 즉시 판정
```

## 4. 진입 0의 실제 구조

게이트도 예산도 아니었다. `gate_effect_review` 판정: **진입 0인데 게이트 차단도 0.**

- 7월 US "진입" 75건 중 실제 체결은 **4건**. 나머지는 플랜만 생성되고 미체결.
- 미체결 이유: 눌림존(현재가 −0.5%)에 가격이 닿지 않음.
- judge 거부 사유도 같은 축: *"price sits right at VWAP … fills immediately as a chase"*

**즉 눌림 진입 방식이 현재 시장에 맞지 않는다.** 그런데 완화는 −1.075%로 기각됐다.
→ 해법은 눌림 완화가 아니라 즉시매수(buy_zone 불필요)이고, 그게 버그로 막혀 있었다.

## 5. 현재 라이브 설정

```
US_DAILY_ENTRY_CAP          = 5        유일한 enforce (외부 검증 통과)
REGIME_ENTRY_GATE_MODE      = shadow
US_WEEKDAY_ENTRY_BLOCK_MODE = shadow
EARLY_PEAK_EXIT_SHADOW_MODE = shadow
SINGLE_SYMBOL_JUDGE_ALLOW_BUY_READY = true (US 한정, MILD_BULL/MODERATE_BULL/AGGRESSIVE)
EARLY_JUDGE: global 50 / US 50 / hour 15 / ticker 2 / run 2
```

롤백은 각각 env 한 줄이며 매도·기존 포지션에 영향 없다.

## 6. 다음 세션 체크리스트

1. `python tools/gate_effect_review.py` — 진입·차단·net 즉시 판정
2. 즉시매수 발동 시 `python tools/buy_ready_counterfactual.py`로 실제 성과 대조
3. 진입이 0이고 **게이트 차단도 0**이면 게이트 탓이 아니다. 상류(judge 판단·후보·예산)를 본다
4. 조기익절 tier(2026-07-14 적용) 효과는 청산 20~30건이 쌓여야 4~6월 대비로 검증 가능
   (4~6월 봉우리 반납 54건 → 7/14 이후 0건이나 표본이 2건뿐)

## 7. 미해결 / 열린 축

- **섹터 캡**: 입력이 없어 영구 미작동이었고 캐시를 만들었으나 `SECTOR_MAP_ENABLED=false`로
  꺼둠. Technology 54.5% 집중 상태에서 캡(sector=3)이 갑자기 걸리면 후보 구성이 급변한다.
- **즉시매수 기준**: `"strong continuation in a strong regime"`이 엄격해 하루 4건.
  완화는 1h −0.158%로 근거 부족이나 표본 7건이라 재검토 여지.
- **US sector 필드**: selection 원장에 여전히 NULL(캐시는 있으나 토글 off).
- `test_price_collector_incremental` 4건: exchange_calendars 미래 세션 의존, 세션 전부터 실패.
