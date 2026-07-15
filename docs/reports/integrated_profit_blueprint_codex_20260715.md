# 수익성 극대화 통합 설계도 — Codex 독립 검증판 (2026-07-15)

성격: **분석·설계·재현 랩**. 라이브 설정, 주문, 프로세스, 운영 DB는 변경하지 않았다. 아래 신규 정책은
모두 `SHADOW_ONLY`이며 승격은 운영자 승인 사항이다.

## 0. 최종 결론

Claude 리포트의 큰 방향, 즉 **코어가 기본 복리를 만들고 PathB는 제한된 볼록 수확기 역할만 맡는다**는
구조는 옳다. 다만 공격 후보를 다시 전수 점검한 결과, 다음처럼 수정해야 한다.

1. **확정 수익 기반:** US `SCHG/BIL` 추세 코어, KR 팩터 추세 코어, 자본 확대 후 GQMT 코어.
2. **기존 tactical 핵심:** US 러너 캐리, KR early-tier 대 Split-Runner paired A/B. 시장 간 출구 정책을
   복제하지 않는다.
3. **이번에 새로 발견한 challenger:**
   - `US_CONSENSUS_3D_V1`: 기계 신호 전략명과 Claude 추천 전략명이 같은 US 신호를 D+1 시가에 진입해
     3세션 보유한다.
   - `KR_US_SECTOR_PULSE_3D_V0`: 고정된 5개 US 섹터 중 종가 수익률이 +2% 이상인 최강 섹터를 해당 KR
     섹터 ETF에 다음 시가로 전달해 3세션 보유한다.
4. 두 신규 후보 모두 평균은 양수지만 **현재 승격 기준은 실패**했다. Consensus는 표본·꼬리 의존,
   Sector Pulse는 블록 하한이 음수다. 그러므로 실주문이 아니라 독립 forward 원장을 가동할 가치만 있다.
5. `직전 낙하 필터`와 `1/3 정찰→확인 증액`은 수익 신호가 아니다. US 출혈 국면의 손실을 줄이지만
   자체 기대값은 여전히 음수다. **최근 확정손익 건강도가 음수일 때만 켜지는 방어 모드**로 한정한다.
6. 목표함수는 거래 수가 아니라 **세후·비용 후 포트폴리오 CAGR 최대화, MDD 제한**이다. PathB 신호가
   없거나 건강도가 나쁘면 자본은 현금이 아니라 검증된 코어/단기채 상태로 돌아간다.

## 1. 검증 범위와 재현 계약

### 사용 데이터

- `ticker_selection_log.db`: 2026-04-07~07-15, 29,806행. 신호 발화, 전략명, Claude 추천, forward.
- `v2_learning_performance`: 실현 포트폴리오 거래와 비용·MFE·MAE.
- `candidate_audit.db`: 후보 221,282행, 가상 경로 373,457행. 즉시·30분·60분·VWAP·OR·volume 경로.
- 분봉: `data/price/minute` US 542종목, KR 381종목.
- 장기 외부 조정주가: 5개 US 섹터 ETF, 5개 KR 섹터 ETF, KODEX200·KODEX인버스.

### 공통 원칙

- 신호일 D의 정보만 사용하고 고정호라이즌 전략은 D 다음 실제 거래일 시가에 진입한다.
- US/KR 왕복비용은 각각 0.50%/0.21%를 차감한다.
- US 비용 0.70%는 잘못된 값으로 삭제하지 않고 **0.50% 기준 + 0.20% FX 스트레스**로 분리한다. 실제
  환전 이벤트가 없으면 FX 비용은 거래마다 반복 차감하지 않는다.
- 독립 신호 결과와 한 슬롯 실행 가능 결과를 같이 보고한다.
- 기간 분할, 상위 1·3건 제거, PF, MDD, 5거래 블록 bootstrap 하한을 함께 본다.
- 오늘 탐색한 파라미터는 post-selection임을 숨기지 않는다.

재현 산출물은 §10에 기록했다.

## 2. Claude 공격안 교차감사

| 항목 | 판정 | 독립 검증 및 수정 |
|---|---|---|
| A1 발사횟수 복구 | **유지하되 KPI 수정** | 원시 플랜 10~20건/주를 목표로 하면 나쁜 거래를 강제한다. `평가 가능률→적격 신호율→플랜율→체결률`을 기회 수로 정규화하고, `no_evaluation` 같은 고장만 수리한다. |
| A2 US 러너 캐리 | **최우선 tactical 유지** | MFE≥3% 실현이 US +1.265, KR −0.331로 시장 비대칭이 명확하다. carry 0건 원인과 판정 배관을 수리하되 KR 복제 금지. |
| A3 US 존하단 enforce | **즉시 enforce 반대** | 회피 book Δ+0.207은 실재하지만 승자 미체결 양날 forward가 미완이다. 기존 문서도 존하단 ‘한정’은 격추했다. passive-limit paired shadow 후 결정한다. |
| A4 코어 다이얼 | **유지** | 절대수익은 코어 자본 규모가 좌우한다. 단 사이징은 운영자 결정이며 tactical 손실을 가리는 수단이 아니다. |
| A5 무장벽 US 5d | **challenger 유지** | 레버리지 ETF 제거 후 독립 재OOS가 필요하다. 과거 결과는 표본 8~9건·꼬리 의존이다. |
| D1 존터치 미시상태 | **방어 shadow로 격하** | US 직전 5분 낙하 필터는 평균 −0.519→−0.421, 정찰모드는 −0.519→−0.209로 줄였지만 양수가 아니다. KR에는 수익을 깎았다. |
| D2 비용 정합 | **수정 채택** | 기준 0.50, FX 이벤트 원장, 0.70 스트레스의 3열로 통합한다. 모든 0.70을 0.50으로 일괄 치환하면 스트레스 검증을 없애는 오류다. |
| D3 어닝스 회피 | **공격/방어 분리** | 보유 중 발표는 gap-risk veto, 발표 후에는 PEAD challenger다. 같은 이벤트를 무조건 회피하면 잠재 drift도 버린다. |
| A6 섹터 2차 파도 | **데이터 수리 선행** | `sector` 컬럼 커버리지가 KR/US 모두 0이며, 이미 `kr_sector_play` 코드와 7/13 이벤트 17건이 존재한다. 신규 전략보다 sector map join과 성과원장 동기화가 먼저다. |
| A7 VI/orderbook/premarket | **라벨 공장 채택** | 전략 성과 주장이 아니라 양방향 가설을 사전등록한다. VI는 continuation만 가정하지 않고 reversal도 동시에 측정한다. |
| A8 Claude 러너 심판 | **현재 보류** | TARGET 100% 승률은 기계적 target 체결의 성질이지 Claude 판단력의 증명이 아니다. Claude 관여 출구도 선택편향이 있어 randomized paired arm 전에는 3번째 exit owner를 추가하지 않는다. |
| F7 피라미딩 기각 | **근거 등급 하향** | `최종 +1.26 - 증축점 +3 = -1.74`는 산술 상한이지 진입시각·증축가격을 재현한 반사실이 아니다. 다만 현재 off 유지 결론은 안전하다. |

### Claude가 즉석 기각한 두 아이디어

- TARGET 재장전은 US D+3 −0.63%, KR −4.10%라 기각 유지.
- 단순 신호 동시발화 폭은 단독 f3 +4.06 대 동시 +3.35로 개선이 없어 기각 유지.

## 3. 신규 전략 1 — `US_CONSENSUS_3D_V1`

### 계약

1. 기존 룰 신호가 `signal_fired=1`이어야 한다.
2. `strategy_name == recommended_strategy`이고 빈 문자열이 아니어야 한다.
3. D+1 정규장 시가 진입, 3번째 거래일 종가 청산.
4. US 왕복 0.50%, 한 슬롯. PathB와 자금·exit owner를 분리한다.
5. 전략 합의는 **신규 필터**이지 Claude 확신점수나 프롬프트 문장 점수가 아니다.

### 결과

| 구간 | 실행모형 | n | 평균 | PF | 상위3 제거 합 |
|---|---|---:|---:|---:|---:|
| 전체 | 독립 신호 | 14 | **+3.431%** | 3.33 | **+5.957%p** |
| 전체 | 한 슬롯 | 7 | **+1.498%** | 1.88 | −11.888%p |
| 5월 이후 OOS | 독립 신호 | 10 | **+3.144%** | 2.99 | −2.957%p |
| 5월 이후 OOS | 한 슬롯 | 6 | **+3.340%** | 3.84 | −2.332%p |

호라이즌 판별도 중요하다. OOS 한 슬롯은 1일 −0.387%, 3일 +3.340%, 5일 −1.520%다. 즉 일반적인
“좋은 종목”이 아니라 **현재 표본에서만 보이는 3일 경로 가설**이다.

### 판정

- 장점: 기존 시스템의 룰과 LLM이 독립적으로 같은 기전을 지목할 때만 진입하므로 새 API·데이터가 필요 없다.
- 약점: 한 슬롯 OOS n=6, 상위 3건 제거 후 음수. 현재 enforce 불가.
- 다음 게이트: n≥30, 최소 3개월·하락월 포함, 비용 후 평균>0, PF≥1.2, ex-top3>0, 블록 LCB>0,
  KIS D+1 시가 교차검증. 그 전 주문권한 0.

KR에는 적용하지 않는다. KR 독립 신호는 5월 이후 3일 평균 −3.103%, 5일 −15.903%였다.

## 4. 신규 전략 2 — `KR_US_SECTOR_PULSE_3D_V0`

### 경제적 가설과 계약

국제 산업 간 정보전달 가능성은 학술적으로 보고되어 있으나, 그 존재가 이 계좌에서 비용 후 수익을
보장하지 않는다. 고정된 매핑만 사용했다.

| US 리더 | KR 실행 ETF | 테마 |
|---|---|---|
| SOXX | 091160 | 반도체 |
| XLV | 227550 | 헬스케어 |
| XLF | 139220 | 금융 |
| ITA | 309230 | 방산 |
| LIT | 305720 | 2차전지 |

- US 종가에서 5개 중 최강 섹터 수익률이 +2% 이상이면 해당 KR ETF를 다음 KR 시가에 매수한다.
- 3세션 종가 청산, KR 비용 0.21%, 하루 한 섹터.
- 1/1.5/2% × 1/3/5일 9개 셀을 모두 공개한다. +2%·3일은 결과를 본 뒤 선택한 셀이다.

### 핵심 결과

| 구간 | n | 섹터 ETF 평균 | PF | 상위3 제거 합 | 블록 LCB |
|---|---:|---:|---:|---:|---:|
| Discovery 2018~2022 | 335 | **+0.198%** | 1.18 | **+32.90%p** | −0.138% |
| OOS 2023~2026-07 | 252 | **+0.186%** | 1.13 | **+6.66%p** | −0.300% |

같은 신호로 KODEX200만 산 OOS 평균은 +0.114%였다. 섹터 선택의 OOS 초과분은 +0.072%에 불과하고,
초과분 상위3 제거 합은 −17.55%p다. 50% 섹터+50% 인버스 실제 헤지는 OOS −0.149%였다.

### 판정

- 절대수익 부호와 ex-top3가 discovery/OOS에서 살아 있어 **폐기하지 않을 가치가 있는 새 후보**다.
- 그러나 블록 하한 음수, 9셀 탐색, OOS 초과수익 꼬리 의존 때문에 알파 확정이 아니다.
- 정확한 라벨: **글로벌 risk-on timing + 약한 섹터 선택 가능성이 섞인 post-selection challenger**.
- 다음 게이트: 규칙 동결 후 60 forward 신호, KIS 시가·정수주·분배금 교차검증, 절대/벤치초과
  ex-top3>0, 블록 LCB>0. 그 전 주문권한 0.

기존 `cross_market_frontier_lab`의 QQQ→KODEX200, SMH→KR반도체 단일 규칙이 모두 기각된 사실과
모순되지 않는다. 이번 후보는 5개 섹터의 **강한 최상위 pulse**와 3일 호라이즌을 결합한 별도 가설이다.

## 5. 신규 방어 구조 — `US_HEALTH_ADAPTIVE_PROBE_V1`

### 분봉 반사실

실현 거래 265건 중 분봉 정합 166건(US 148, KR 18)을 사용했다. 체결 분봉은 사전판단에 쓰지 않고,
체결 분 직전까지 완전히 끝난 봉만 사용했다.

| US 정책 | n | 평균/제안거래 | 합계 | MDD | 블록 LCB |
|---|---:|---:|---:|---:|---:|
| 실제 기준 | 148 | −0.519% | −76.81%p | −105.81%p | −0.867% |
| 직전5분≤−0.5%·VWAP하회·3봉중2봉하락이면 skip | 148 | −0.421% | −62.34%p | −86.31%p | −0.734% |
| 1/3 정찰, 15분내 +0.7% 확인 시 2/3 증액 | 148 | −0.209% | −30.99%p | −52.28%p | −0.444% |

정찰 정책은 노출을 148→74단위로 줄였고 순손실/노출도 −0.519→−0.419%로 개선했다. 그러나 여전히
음수다. 더 중요한 것은 국면 분리다.

- 5월까지 US 기준 +0.292%였는데 직전낙하 skip은 +0.082%로 이익을 훼손했다.
- 6~7월 US 기준 −0.676%를 skip −0.519%, 정찰 −0.270%로 줄였다.
- KR n=18 기준은 +1.087%였고 두 정책 모두 총수익을 낮췄다.

### 올바른 사용법

상시 entry gate로 enforce하지 않는다. US 최근 20개 **이미 확정 청산된 거래** 평균이 음수이고 최소
표본 10개일 때만 `ADVERSE`로 전환한다.

- NORMAL: 기존 진입 유지.
- ADVERSE: 1/3 정찰, 15분 이내 +0.7% 종가 확인 시 나머지 2/3 가상증액.
- RECOVERY: 최근 20개 평균과 PF가 복구되고 5개 연속 신규 거래 확인 후 NORMAL.
- KR: off.

기존 온라인 hard health gate도 전수 원장에서 전체 손실 −88.91→−32.14%p, MDD −89.47→−38.87%p로
줄였지만 노출당 기대값은 거의 그대로 음수였다. 따라서 이 구조의 본질은 알파가 아니라 **나쁜 tactical에
자본을 덜 주고 유휴자본을 코어로 반환하는 것**이다.

## 6. 데이터 계약으로 열어둘 신규 패밀리

### 6.1 `KR_VI_RESUME_DUAL_V0`

KR VI 해제를 무조건 모멘텀으로 보지 않는다. KRX 연구는 dynamic VI가 가격 안정·발견에 기여한다고
보고하고, 다른 시장 연구는 call auction 중 continuation 뒤 재개 후 reversal도 보고한다. 따라서 두 arm을
사전등록한다.

- C arm: VI 해제 체결가가 pre-VI 고가 위, 매수호가 불균형 양수, 1·5분 VWAP 유지 → continuation.
- R arm: 해제 auction 과대갭 뒤 호가 불균형 반전·VWAP 재이탈 → reversal 관측. 현물 롱온리이므로
  수익화는 신규진입 회피 또는 인버스/ETF 매핑이 있을 때만 가능.
- 기록: VI 종류, 발동/해제시각, auction 체결가, pre-VI 기준가, 1/5/15/30분 경로, 호가 불균형,
  스프레드, 실제 체결가능가.
- 게이트: arm당 n≥60, KR 0.21%+VI 슬리피지 후 양수, 월별·ex-top3·block LCB 양수.

### 6.2 `US_PEAD_UNCERTAINTY_V0`

어닝스는 하나가 아니라 두 정책이다.

1. PathB/runner가 발표를 가로질러 보유하지 않도록 D−1~발표 직후 carry를 차단하는 gap-risk 방어.
2. 발표시각·실제/예상 surprise·발표 후 최초 체결가능 시가를 point-in-time으로 저장하고, surprise 방향과
   D+1 gap/volume이 같은 경우 3/5/20일 drift를 평가하는 독립 challenger.

실적 발표 위험은 주로 overnight에 재가격화되고, 초기 반응과 지연 반응이 사전 불확실성에 따라 달라질 수
있다는 연구가 있다. 현재 `earnings_calendar.json`은 가까운 창만 보관하므로 과거 발표를 point-in-time으로
백필하기 전에는 성과 주장을 하지 않는다.

### 6.3 섹터 릴레이 계측 수리

- 후보 입력 시 `sector`를 채우고 map version/known_at을 기록한다.
- 기존 `kr_sector_play`의 `PROFIT_EVIDENCE_SHADOW`가 `v2_learning_performance`에 0건인 동기화 단절을
  수리한다.
- ETF→구성종목 기존 arm과 후보 동시발화→지연 종목 신규 arm을 같은 원장으로 비교한다.
- 동일 섹터 중복노출은 알파 측정과 별도로 중앙 allocator가 합산한다.

## 7. 미국장 최종 구조

| 우선 | 전략 ID | 역할 | 현재 상태 |
|---:|---|---|---|
| 1 | `US_SCHG_BIL_TREND_V1` | 소액 실행형 기본 복리·MDD 관리 | 통합 core shadow, 3회 KIS 교차검증 필요 |
| 2 | `GQMT_CORE_V1` | 충분한 자본의 장기 목표 코어 | 자본·정수주 전까지 봉인 shadow |
| 3 | `US_RUNNER_CARRY_V1` | MFE≥3% 검증 러너의 overnight convexity | carry 0건 원인·net 배관 수리 |
| 4 | `US_CONSENSUS_3D_V1` | 룰-LLM 기전 합의 3일 challenger | **신규 shadow**, n≥30 필요 |
| 5 | `US_SWING_5D` | 독립 ML swing challenger | tracker 사망 수리·성숙 재개 |
| 6 | `PATHB_GAP_ESCAPE_CONTINUATION` | cancel-above 뒤 continuation | n=8, 0.10x shadow 한정 |
| 7 | `PATHB_US_ZONE_QUALITY` | 나쁜 체결가 회피 | passive paired shadow, 즉시 enforce 금지 |
| 방어 | `US_HEALTH_ADAPTIVE_PROBE_V1` | 출혈 국면 tactical 손실예산 축소 | 신규 shadow, NORMAL에서는 무개입 |

US에서 하지 않을 것: PathB 전면 확대, 5일 consensus 선택, +3% 피라미딩, 범용 30/60분 대기,
비용 0.70 고정, TARGET 재장전.

## 8. 한국장 최종 구조

| 우선 | 전략 ID | 역할 | 현재 상태 |
|---:|---|---|---|
| 1 | `KR_FACTOR_TREND_CORE_V1` | KODEX 모멘텀/우량주+단기채 기본 복리 | core shadow·3회 KIS 교차검증 |
| 2 | `PATHB_KR_EXIT_PAIRED_V1` | early 전량트레일 A vs 3.6% 50% split B | read-only paired observer, n≥15 |
| 3 | `KR_FLOW_ENTRY_SHADOW` | 외국인/기관 수급 보조 | 117일 shadow 판독 |
| 4 | `KR_US_SECTOR_PULSE_3D_V0` | US 강섹터→KR 3일 risk pulse | **신규 post-selection shadow** |
| 5 | `KR_VI_RESUME_DUAL_V0` | VI 후 continuation/reversal 데이터 공장 | 라벨 계약부터 |
| 계측 | `KR_SECTOR_RELAY_EVIDENCE_V2` | 기존 sector_play와 co-fire relay 비교 | sector join·성과 sync 수리 |

KR에서 하지 않을 것: US 정찰필터 복제, consensus 5일, KR_CONFIRM_5D, US식 runner carry, 단순
QQQ/SMH 선행매수, 범용 intraday momentum 확대.

## 9. 중앙 자본배분 설계

### 9.1 수익 극대화의 실제 형태

```
검증된 Core가 기본 자본을 보유
        │
        ├─ Tactical health 양수 + 승격게이트 통과 → 예약 위험예산만 대여
        │
        └─ health 음수/표본 starvation/stale → 자본을 Core·단기채로 반환

각 challenger: SHADOW → PROBE → MICRO → STANDARD
자동 승격 없음, Core와 exit owner 공유 없음
```

### 9.2 주문 제안 필수 필드

- `strategy_id`, signal known_at, 시장, 종목, 보유기간, exit owner.
- 예상 gross/net, 기준비용, FX-event 비용, stress 비용.
- 정수수량, 최대손실, 배정 위험예산, 잔여현금.
- 동일 종목·섹터·성장베타·시장베타 중복.
- 최근 forward n, 주당 표본 페이스, block LCB, ex-top3, tracker heartbeat.

### 9.3 승격 후 목표 위험예산

현재는 신규 challenger 주문권한이 모두 0이다. 충분한 자본과 공통 게이트 통과 뒤의 목표 상한만 정의한다.

| 레인 | 목표 상한 | 조건 |
|---|---:|---|
| 통합 Core | 70~80% | KIS·세후 truth 원장, tracker heartbeat 정상 |
| US swing/consensus | 합계 10% | 각자 독립 게이트 통과, 중복 종목 합산 |
| US runner PathB | 5% | carry/capture forward 양수 |
| KR split PathB | 5% | paired n≥15·ex-top3·block LCB 양수 |
| event/sector/VI challengers | 합계 5% | 전략별 최소 n·비용·LCB 통과 |
| 즉시 주문가능 현금 | 최소 5% | 결제·비상청산·PathB 주문 여유 |

## 10. 실행 순서와 산출물

### P0 — 판정 시계와 truth

1. A1을 거래 수 확대가 아니라 `no_evaluation`·데이터 결측·스케줄 사망 수리로 수행한다.
2. core/us_swing/paired observer heartbeat와 `/monitor` stale·n/주·n=gate ETA를 통합한다.
3. 비용 원장을 기준 0.50/0.21, FX-event, stress 0.70의 세 열로 통합한다.
4. early-tier 대 Split-Runner paired A/B를 계속 축적한다.

### P1 — 이번 신규 후보

5. `US_CONSENSUS_3D_V1` 가상 D+1 시가·3일 종가 tracker를 독립 원장으로 배선한다.
6. `KR_US_SECTOR_PULSE_3D_V0` 규칙을 동결하고 KIS 가격 forward shadow만 시작한다.
7. US health-adaptive probe를 라이브 분기 없이 read-only observer로 기록한다.

### P2 — 새 데이터 공장

8. sector map ingress+성과 sync를 수리한다.
9. VI/orderbook 라벨을 수집하고 continuation/reversal을 동시에 판정한다.
10. earnings point-in-time history를 백필해 gap-veto와 PEAD를 별도 전략으로 평가한다.

### 재현 파일

- `tools/creative_profit_blueprint_lab.py`
- `reports/creative_profit_blueprint_lab_20260715.json`
- `reports/creative_profit_consensus_ledger_20260715.csv`
- `reports/creative_profit_microstate_ledger_20260715.csv`
- `tools/us_kr_sector_pulse_lab.py`
- `reports/us_kr_sector_pulse_lab_20260715.json`
- `reports/us_kr_sector_pulse_ledger_20260715.csv`
- `tests/test_creative_profit_blueprint_lab.py`
- `tests/test_us_kr_sector_pulse_lab.py`

신규 테스트 7건은 모두 통과했다.

## 11. 외부 근거의 올바른 사용

- 국제 산업 간 lead-lag 연구는 Sector Pulse의 **가설 생성 근거**일 뿐, 본 시스템의 수익 근거는 위 원장이다:
  [Industry return lead-lag relationships between the US and other major countries](https://pmc.ncbi.nlm.nih.gov/articles/PMC9842501/).
- 주문불균형은 단기 가격압력과 미래수익 관계를 가질 수 있지만 장기에는 반전될 수 있어, orderbook은
  방향 신호보다 체결품질·짧은 confirmation에 우선 사용한다:
  [Order imbalance and individual stock returns: Theory and evidence](https://www.sciencedirect.com/science/article/abs/pii/S0304405X03001752).
- KR VI는 종류·국면에 따라 안정화와 가격발견 효과가 다르므로 continuation 단일 가정이 위험하다:
  [Dynamic and Static Volatility Interruptions: Evidence from the Korean Stock Markets](https://www.mdpi.com/1911-8074/15/3/105).
- 실적발표 재가격화는 overnight에 집중되고, 사전 불확실성이 초기·지연 반응을 바꿀 수 있어 veto와 PEAD를
  분리한다:
  [Earnings Announcements: Ex-ante Risk](https://www.aeaweb.org/conference/2024/program/paper/5GGEki7i).

## 12. 한 문장 최종안

**코어로 시장·팩터 복리를 기본 확보하고, US는 러너 캐리와 룰-LLM 합의 3일 전략, KR은 split-runner와
US 섹터 pulse를 독립 challenger로 경쟁시키며, PathB 건강도가 나쁠 때는 정찰모드로 위험만 줄인다.**

수익성은 “매수를 많이”가 아니라 **검증된 자본 소유자에게 오래 자본을 주고, 음수 엔진에서 빠르게 회수하는
구조**에서 극대화한다.
