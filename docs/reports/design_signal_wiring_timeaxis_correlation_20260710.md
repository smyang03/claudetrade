# 설계: 신호 정보 활용·배선 — 시간축 + 상관관리 + 진입/청산 품질 (2026-07-10)

지시(운영자): "각 정보를 어떻게 활용하고 배선 연결까지 어떻게 할지, 시간축 포함 설계."

## 0. 원칙 (전 항목 공통)

- **측정 배관 우선**: 정보를 "쓰기(enforce)" 전에 "기록·판독(shadow)"부터. 진입/청산 로직 무변경으로 시작.
- **판정 기준 = 우리 실제 net**(수수료·FX·보유기간), KR/US 분리, no-lookahead, 국면·분포 확인.
- **enforce는 검증 통과 + 운영자 승인 후.** 이 문서는 배선·검증까지의 설계.
- 실측 근거: 손실 90%가 "3종목+ 동시청산일"에 집중(상관관리 정당), 순서 정보 부재(시간축 정당), hold_advisor가 이미 R:R 재채점(S2 중복 제외).

---

## 1. 시간축 (mfe_time / mae_time) — ★최우선, 여러 판정을 여는 열쇠

### 활용
- "MFE와 MAE 중 뭐가 먼저 왔나"를 DB로 판정 → B breakeven 러너학살 실수(순서상 kill 여부), 낙관/비관 갭 실측, capture 판정, 손절 조기성 — **분봉 replay 없이 SQL로**.

### 배선 (전방, 진입/청산 무변경)
1. **관측 시점 기록** — `pathb_runtime.py:12346` `observed_peak_price`/`observed_low_price` 갱신 분기에 시각 추가:
   ```
   새 고점 갱신 시  → pos["observed_peak_at"] = now_iso
   새 저점 갱신 시  → pos["observed_low_at"]  = now_iso
   ```
2. **영속화** — `_persist_observed_excursion`(12358) payload에 두 시각 필드 추가(이미 peak/low price 싣는 곳).
3. **청산 전달** — `claude_price_sell_manager.mark_closed`의 cost_meta(pnl_krw 옆)에 `mfe_at`/`mae_at` 실어 CLOSED payload로.
4. **sync·스키마** — `sync_v2_learning_performance.py`에 `mfe_time`/`mae_time` 컬럼 추가, close_payload에서 읽어 기록.

### 검증·한계
- 전방만: 과거 소급 불가(분봉 필요). 내일부터 축적 → 표본 n≥30(수 주) 후 B breakeven·순서 판정 개방.
- 판독 도구: `tools/exit_order_review.py`(신규) — mfe_time<mae_time(상승먼저)/역순 버킷 × close_reason별 net.

---

## 2. 상관/집중 관리 (A3 진입사이징 + S1 청산순위) — ★손실 90% 겨냥

### 활용
- **A3 진입**: 신규 후보가 기존 보유와 고상관(과거 60일 ≥0.7)이면 사이즈 축소 or 우선순위 하향. "독립 리스크당 노출" 유지. 죽은 `MAX_SECTOR_POSITIONS`(범주형 환상)를 연속 상관으로 대체.
- **S1 청산**: 리스크오프일(지수 red, red-tape index_history 재활용)에 고상관·저확신 클러스터부터 청산 우선순위 → TARGET 러너는 마지막 보호.

### 배선 (측정 먼저 — 로직 무변경)
1. **동시보유 상관 스냅샷** — `pathb_runtime.py:5403` 동시보유 스냅샷 함수 재활용(이미 존재). 여기에 **보유 포지션쌍의 과거 60일 수익률 상관**(로컬 data/price CSV, no-lookahead) 계산 필드 추가 → funnel 이벤트 `holding_correlation_snapshot`로 기록만.
2. **진입시점 상관** — 신규 후보 vs 현 보유의 상관을 진입 audit에 기록(shadow).
3. 로직 무변경 — 사이징 헬퍼(12956)·청산순위는 **판독 후** 반영.

### 검증
- `tools/correlation_cluster_review.py`(신규): 보유기간 겹친 포지션쌍 상관 계산 → **고상관 클러스터 동시보유일의 결합 net vs 저상관일** 비교(이미 손실 90% 동시집중 확인). "상관 사이징했다면" counterfactual net.
- 판정: 고상관 클러스터가 결합 drawdown을 유의 악화 + KR/US·국면 일치 → A3/S1 설계 착수.

---

## 3. 진입 체결 품질 (A1 스프레드 / A2 참여율) — 병목 무충돌

### 활용
- **A1**: 진입시점 스프레드·호가깊이로 지정가 peg 위치 정밀화(존 상단 추격 회피의 정밀화). 스킵 아님 → fill 유지.
- **A2**: 주문금액/거래대금(참여율)으로 주문유형(지정가 여유) 결정. `liquidity_bucket`(범주형) → 연속값.

### 배선
- **A1 US 스프레드 결측 선결**: `single_symbol_judge.py` 주석 실측 "spread_bps 12/12 결측". KR은 `_kr_microstructure_context` 보강 존재 → **US 동등 배선**(KIS 호가 조회, judge 호출 시점만 저빈도). 기록부터.
- **A2**: 진입 audit에 `participation_rate = order_krw / adv_krw` 기록(계산만, 로직 무변경).

### 검증
- A1: 진입 스프레드 분위 × net_after_fx(KR 즉시 가능, US 배선 후). 넓은 스프레드 진입 net 열위 확인 → peg 규칙.
- A2: 참여율 분위 × 슬리피지·net, liquidity_bucket 대비 추가 변별 있나.

---

## 4. 청산 품질 (S3 free-carry / S6 갭리스크) — 볼록성 보존

### 활용
- **S3 free-carry**: 원금(cost basis) 회수 후 **잔량은 캡 없이(uncapped) 러너로 태움** — 현 LADDER(각 rung 상방 캡)와 구별. 리스크만 덜고 볼록성 온존.
- **S6 갭리스크**: 예정 이벤트(실적) 전 부분 트림 → 좌측꼬리(갭 손실) 방어. `data/earnings_calendar.json` 존재. ★surprise 방향 아님(CLAUDE.md 게이트) — "이벤트 임박=갭리스크" 방향중립만.

### 배선
- S3: `mark_sell_partial` 인프라 존재. free-carry는 **capture 도구 counterfactual 먼저**(라이브 무변경) — LADDER 발동 종목의 실측 MFE 경로에서 "원금회수+잔량 uncapped" vs 현 LADDER capture 재생.
- S6: earnings_calendar × 보유 포지션 조인 → 실적 전날 갭 분포·net 판독(도구, 라이브 무변경). 좌측꼬리 기여 유의하면 트림 규칙.

### 검증
- S3: Δnet>0 + 러너(고MFE) 학살 없음(우측꼬리 보존) 이중 확인.
- S6: 실적 전날 보유 갭 좌측꼬리가 우리 손실에 유의 기여하나(작으면 후순위).

---

## 5. 배선 선결 후 (S4 RS / S5 거래량) + 기각

- **S4 RS-vs-지수·S5 거래량 divergence**: 결정시점 로깅 부재 → 배선 선결. 예측-인접이라 배선 후에도 가장 엄격한 net 검증. **후순위.**
- **기각**: S2(hold_advisor R:R 이미 구현)·B1 VIX/B2 breadth(정적 레짐컷 재포장+red-tape 중복)·Chandelier·목표낮추기·순진 scale-out·climax익절·맹목 time-stop.

---

## 6. 실행 순서 (우선순위 + 타이밍)

| # | 항목 | 성격 | 타이밍 |
|---|---|---|---|
| 1 | **시간축 배선** (mfe_time/mae_time) | 전방 배선, 무변경 | KR 마감 후 |
| 2 | **상관 클러스터 판독 도구** | read-only | 즉시 가능 |
| 3 | **상관 스냅샷 배선** | shadow 기록 | 마감 후 |
| 4 | S6 갭리스크 판독 (earnings_calendar) | read-only | 즉시 가능 |
| 5 | S3 free-carry counterfactual (capture 도구) | read-only | 즉시 가능 |
| 6 | A1 US 스프레드 배선 | shadow 기록 | 마감 후 |
| 7 | A2 참여율 기록 | shadow | 마감 후 |
| 8 | S4/S5 로깅 배선 | 후순위 | 검증 큐 뒤 |

## 7. 안전
- 1·3·6·7 배선 = 기록만, 진입/청산 로직 무접촉. 2·4·5 = read-only 판독. enforce(A3 사이징·S1 순위·S3·S6 트림)는 전부 판독 통과 + 운영자 승인 후.
- 장중 라이브 코드 변경 금지 → 배선은 마감 후. 판독 도구는 언제든.

— 배선 지점 실측: observed_peak(pathb_runtime.py:12346)·_persist_observed_excursion(:12358)·mark_closed cost_meta·동시보유스냅샷(:5403)·사이징헬퍼(:12956)·single_symbol_judge spread 결측. 손실 90% 동시집중·hold_advisor R:R 중복은 세션 실측.
