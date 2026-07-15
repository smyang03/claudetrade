# 설계정의 초안 — Selection 재편(rule 압축 + Claude 매수타이밍 + 분업 매도) (2026-07-07)

토론으로 확립할 설계의 **초안(사실 고정 + 쟁점 제시)**. 근거: 두 코드 매핑 에이전트(매수 파이프라인·매도 경로), read-only.

## A. 사실 고정 (토론 전제 — 다툼 없음)

1. **Claude 선정(select_tickers, analysts.py:2510)은 무알파**(AUC 0.507, 내부 확정) — 제거 후보. FINSABER 외부 확증.
2. **검증된 매도 엣지 +152 = 두 컴포넌트**: 가격플랜 TARGET(+131, `execution/claude_price_sell_manager.py:91`, 결정론) + hold advisor(+20.8, `minority_report/hold_advisor.py:1815`). **hold advisor는 14%만.**
3. **가격플랜(buy_zone/sell_target/stop)은 selection과 같은 프롬프트 산물**(analysts.py:2930) — select_tickers 제거 시 가격플랜 생성은 별도로 보존해야 함.
4. **후보 30개 압축까지는 이미 rule**(candidate_pool_runtime: 스크리너 병합·스코어·lifecycle·prompt_cap 30). Claude는 그 30개 중 trade_ready 랭킹만 담당.
5. rule 게이트 다수 이미 존재(유동성/가격/상품, 과열, Path B reward_risk 1.5, red-tape US, action_routing 게이트).

## B. 목표 구조 (제안 = 재편 방향)

```
폭넓은 스크리너(US/KR 다소스)
  → [RULE] candidate_pool 스코어·lifecycle·prompt_cap
  → [RULE] trade_ready 압축 전략  ← ★쟁점1 (지금은 Claude select_tickers)
  → [CLAUDE] 후보별 가격플랜(진입존+목표+손절) + 매수타이밍  ← ★쟁점2 (payload)
  → [RULE] 진입 트리거(현재가 buy_zone 진입) + reward_risk/red-tape/chase 게이트
  → 보유
  → [RULE·결정론] 가격플랜 TARGET 매도(+131, 주력)
  → [CLAUDE] hold advisor 능동 보호 오버레이(+20.8, 조기SELL·protective)
```

## C. 토론이 확립할 쟁점

### 쟁점1 — trade_ready 압축 전략 (rule을 뭘로 짜나)
- 현: Claude가 pool 30개 중 trade_ready 선택.
- 제안 옵션:
  - (1a) 순수 스코어 랭킹 상위 N (candidate_pool 기존 스코어 그대로, Claude만 제거) — 최소변경.
  - (1b) + 저변동 틸트(외부 net근거 有, 내부 KR포켓 약신호) — 저회전 정합.
  - (1c) + catalyst 하드게이트 승격(KR 기검증) — 현재 스코어보너스(기본 off)를 게이트로.
- 제약: **새 예측조건 금지**(내부 진입조건 OOS 양월양수 0). 저변동·catalyst는 예측이 아니라 구조/품질 필터인가? = 토론 핵심.

### 쟁점2 — Claude 가격플랜 payload (무슨 데이터를 주나)
- 현 결함(기검증): 프롬프트가 목표 부풀림 유도 → reward_risk 게이트 장식화(plan 615건 0% 탈락, 목표 4.88% vs 실측 0.81% 6배).
- 제안 방향: 예측 유도 데이터 제거, **체결가 품질·리스크 경계 판단 데이터**만. sell_target 결정론화(ATR/저항 기반) 여부.
- 쟁점: TARGET 엣지(+131)는 sell_target이 "도달했을 때" 100% 승 but 도달률 10%. payload를 바꾸면 이 엣지가 유지되나 훼손되나?

### 쟁점3 — 매도 분업·hold advisor 권한
- 데이터: 주력=결정론 TARGET, 오버레이=hold advisor. "전부 hold advisor"는 +131 폐기.
- 쟁점: hold advisor에게 더 권한 줄까(운영자 직감) vs 결정론 TARGET 주력 유지. INTRADAY 이익보호 리뷰가 hold advisor 소산인데 이익 학살 위험(n=2 관찰).

### 쟁점4 — buy_time_confirm_judge 유지 여부
- 매수 순간 Claude 확인(기본 on). "매수 타이밍 판단"에 해당 → 유지? 아니면 이것도 무알파라 제거?

### 쟁점5 — 이행 안전 순서
- 검증된 것(TARGET·hold advisor·red-tape·reward_risk·브로커 truth·catalyst) 보존하며 select_tickers 랭킹만 제거하는 순서. shadow 동등성 게이트.

## D. 판정 기준
- 각 쟁점은 우리 실제 net·기검증 데이터로 판정. 외부 근거는 방향용. 폐기·중단은 운영자.
