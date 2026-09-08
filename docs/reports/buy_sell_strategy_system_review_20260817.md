# 미국·한국 매수·매도 전략 및 시스템 개선 리포트

- 기준시각: 2026-08-17 14:33 KST
- 분석대상: 현재 live 설정, 실계좌 스냅샷, 주문·체결·청산 원장, US/KR shadow 원장, 실제 주문·위험·청산 코드
- 문서상태: 토론 결과를 반영한 보수적 기본안
- 변경범위: 분석 및 권고만 수행. 코드·환경설정·실주문·포지션은 변경하지 않음

## 0. 해석과 판정 원칙

이 리포트는 현재 시스템의 목적을 **미국·한국 급락 종목의 5거래일 반등을 포착하는 단기 전략을 안전하게 검증·운영하는 것**으로 해석한다. 저평가 기업을 장기 보유하는 가치전략은 현재 활성 전략과 입력 데이터가 다르므로 별도 전략으로 분리한다.

성과는 다음 네 층을 섞지 않고 판정한다.

1. **브로커 확정 실현손익**: 실제 매수·매도 체결이 확인된 거래
2. **현재 live 포지션**: 브로커 잔고와 로컬 전략 귀속을 대조한 미실현 상태
3. **Forward/shadow**: 신호 생성 이후 사전 고정 계약으로 정산된 관측
4. **과거 봉인 검증**: point-in-time·purge 규약을 적용한 역사적 근거

주문 접수 가격, 미체결 주문, 백필 데이터는 실체결 성과로 취급하지 않는다.

## 1. 요약 결론

1. **미국 `day_losers` rank1 스윙만 유지 가치가 확인된다.** 실거래 4건과 역사적 검증은 긍정적이지만, 현재 정확한 계약 ID의 공식 Forward 성숙 표본은 0건이다. 확대가 아니라 제한적 검증 단계다.
2. **미국 rank2 폴백은 현재 근거가 없다.** 최근 10건 중앙값과 최대 수익 제외 평균이 모두 음수다. rank1과 별도 전략·별도 승격 기준으로 관리해야 한다.
3. **한국 신규 매수에는 아직 검증된 live edge가 없다.** KR 폴른 실체결은 0건이고 R4 Forward 4건은 평균·벤치마크 대비 알파가 음수다. R2는 정산 표본이 없다.
4. **코어 포지션 3개는 자동 청산 주인이 사라진 orphan 상태다.** 일반 청산에서 격리되지만 코어 manifest는 비활성·무신호라 리밸런싱 청산도 실행되지 않는다.
5. **주문 접수와 체결이 원장에서 혼동된다.** DIOD 미체결 주문이 실행품질 원장에는 체결로 기록됐다. 이 문제가 해결되기 전에는 체결률·슬리피지·shortfall 통계를 신뢰할 수 없다.
6. **현재 `micro`라는 명칭과 실제 위험이 일치하지 않는다.** 정책상 micro는 10% 크기·1슬롯인데 운영자 오버라이드가 50만원·3슬롯을 허용한다. 동시 계약손실은 미국 운용자산의 약 9.6%, 역사적 최악 기준 약 10.4%다.
7. **추가 Claude 판단보다 주문 진실·전략 소유권·Forward 증거가 우선이다.** 비활성 Path A/B를 위한 관측 호출은 비용을 만들지만 live 수익성 증거를 만들지 않는다.

## 2. 현재 실제 매수·매도 구조

### 2.1 live 권한 요약

| 경로 | 현재 상태 | 신규 매수 | 청산 방식 |
|---|---|---:|---|
| US Swing `us_swing_5d` | micro operator override | 가능 | TP +12% / catastrophe SL -25% / D5 |
| KR Fallen `kr_fallen_5d` | R2+R4, blindspot 포함, live ACK | 가능 | TP +12% / catastrophe SL -25% / D5 |
| 일반 Path A | `LEGACY_NEW_BUY_DISABLED=true` | 차단 | 기존 포지션 일반 risk/advisor |
| 일반 Path B | KR·US market live false | 차단 | 기존 Path B 소유권 규칙 |
| US/KR Core | enabled IDs 없음, manifest blocked | 차단 | 전략 리밸런싱 전용이나 현재 무신호 |

최신 실행 설정의 주요 값은 다음과 같다.

| 설정 | 값 |
|---|---:|
| `MAX_ORDER_KRW` | 500,000원 |
| `US_MAX_ORDER_KRW` | 500,000원 |
| `US_SWING_ORDER_MAX_KRW` | 500,000원 |
| `US_SWING_RANK2_FALLBACK_ENABLED` | true |
| `US_SWING_ORDER_ABSOLUTE_HURDLES_ENFORCED` | false |
| `PATHB_KR_LIVE_ENABLED` | false |
| `PATHB_US_LIVE_ENABLED` | false |
| `KR_FALLEN_ACTIVE_RULE` | R2+R4 |
| `KR_FALLEN_BLINDSPOT_ENTRY_ENABLED` | true |
| `KR_FALLEN_LIVE_ENABLED` | true |
| `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS` | true |

근거: `logs/config/effective_config_20260816_194245_live.redacted.json`.

### 2.2 미국 스윙 흐름

```text
KIS day_losers 후보
  -> Yahoo point-in-time OHLCV·시장상대 피처
  -> 회귀+분류 ensemble alpha_score
  -> rank1 (현재 rank2 조건부 폴백)
  -> 개장 5~30분 fresh quote
  -> 시가갭·추격·fade·전일종가 대조·현금·슬롯 가드
  -> KIS 지정가 제출
  -> TP12 / SL25 / D5 청산
```

모델 입력은 RSI, 볼린저 위치, 거래량비, MACD, MA20·60 이격, ATR, 당일 갭·등락, 5·20·60일 모멘텀, 고점 대비 하락, 실현변동성, QQQ 상대강도, SPY·QQQ·IWM 모멘텀이다. 재무·밸류에이션·실적 추정치는 사용하지 않는다.

진입 가드는 다음을 확인한다.

- 브로커 신뢰와 guardian market gate
- 로컬·브로커 미체결 주문
- 동일 티커 보유·당일 재진입
- 개장 5~30분
- fresh quote의 현재가·시가·거래량
- signal reference close와 독립 전일종가의 편차
- 절대 시가갭, 시가 대비 추격·fade
- 현금·시장예산·주문상한·슬롯

장점은 주문 직전 가격계약과 독립 전일종가 대조가 명시적이라는 점이다. 단점은 bid/ask spread·호가잔량·quote age를 사용하지 않고, 현재가를 그대로 미국 지정가로 제출한다는 점이다.

### 2.3 한국 폴른 흐름

```text
장마감 가격 캐시
  -> 급락·갭·MA20 할인·변동성·거래대금 후보
  -> R2 또는 R4, 선택적으로 blindspot
  -> MA20 할인 깊은 순 1개
  -> 익일 개장 2~20분 현재가
  -> 현금·공통 매수 게이트
  -> KIS 주문
  -> TP12 / SL25 / D5 청산
```

현재 규칙은 다음과 같다.

- R2: `ma20_disc <= -25%` 및 `rv20 <= 8`
- R4: `gap <= -4%` 및 `ma20_disc <= -15%`
- 공통 관측피처: 당일 낙폭, 종가 위치, 거래량 급증, 20일 모멘텀, 20일 고점 대비 하락, 가격
- 유동성 사전필터: 20일 평균 거래대금 10억원 이상
- 하한가 잠김과 무효 가격 제외
- 신호일 종가 대비 익일 가격이 +10% 이상이면 TP 여지 소진으로 차단

DART 공시·실적일·ETF/우선주/일반주 유형을 수집하지만 현재 주문 조건으로 사용하지 않는다.

## 3. 실계좌 성과와 현재 포지션

### 3.1 2026-07-01 이후 브로커 확정 청산

| 시장·전략 | 건수 | 승 | 평균 수익률 | 실현손익 |
|---|---:|---:|---:|---:|
| KR 기존 `claude_price` | 2 | 2 | +1.102% | +7,517원 |
| US Path A `claude_price_a` | 7 | 4 | -0.175% | -9,465원 |
| US Path B `claude_price` | 2 | 0 | -2.843% | -20,759원 |
| US broker_sync | 1 | 1 | +2.555% | +19,572원 |
| US 코어 `us_schg_bil_trend_v1` | 1 | 0 | -0.449% | -233원 |
| US Swing `us_swing_5d` | 4 | 4 | +15.068% | +175,195원 |

US Swing 실청산은 AXTI +34.91%, FRMI +12.32%, CVI +0.59%, MXL +12.46%다. AXTI를 제외한 3건 평균도 약 +8.46%이므로 단일 거래만의 플러스는 아니다. 그러나 n=4로 확대 근거에는 부족하다.

### 3.2 현재 브로커 포지션

브로커 스냅샷은 2026-08-17 00:01 KST 저장본이다.

| 시장 | 티커 | 수량 | 전략 | 평가손익률 | 계약 상태 |
|---|---|---:|---|---:|---|
| US | SCHG | 5 | `us_schg_bil_trend_v1` | 약 +2.96% | TP/SL 0, rebalance only |
| US | FA | 10 | `us_swing_5d` | 약 +2.64% | TP12/SL25/D5 |
| KR | 275280 | 1 | `kr_factor_trend_v1` | 약 -3.91% | TP/SL 0, rebalance only |
| KR | 275300 | 1 | `kr_factor_trend_v1` | 약 -3.61% | TP/SL 0, rebalance only |

계좌 요약:

- KR 총자산 약 1,036,904원, 평가손익 -2,915원
- US market asset 약 3,915,310원
- KIS 총자산 약 4,878,194원
- US 포지션 평가액 약 558,531원

SCHG 이익을 원화 환산하고 KR 코어 손실을 합치면 코어 바스켓은 수수료 전 약 +4,372원이다.

## 4. 미국 전략 분석

### 4.1 근거 층별 판정

| 증거 층 | 표본 | 결과 | 판정 |
|---|---:|---|---|
| 역사적 봉인 rank1 | 293세션 | 평균 +1.402%, 중앙값 +0.703%, PF 1.367, 상위3일 제외 +0.923% | 전략 가설 지지 |
| 역사적 2025 | 230세션 | 평균 +0.052%, PF 1.013 | 거의 무엣지 |
| 역사적 2026 | 63세션 | 평균 +6.330%, PF 3.231 | 강한 국면 의존 가능성 |
| 최근 day_losers rank1 진단 | 11건 | 평균 +9.585%, 중앙값 +8.248%, 승률 72.7% | 유망하지만 작음 |
| rank1 최대값 제외 | 10건 | 평균 +7.929% | 최근 표본은 한 종목만의 결과는 아님 |
| live 청산 | 4건 | 평균 +15.068%, 4승 | 긍정적이나 매우 작음 |
| 현재 정확한 contract ID | 성숙 0건 | observation 23, legacy 성숙 5건 제외 | 공식 승격 불가 |

역사적 최악 net은 -27.185%로, -25% stop이 오버나이트 갭에서 초과될 수 있음도 확인된다.

### 4.2 rank2 폴백

최근 day_losers rank2 정산 10건:

- 평균 +1.908%
- 승률 40%
- 중앙값 -0.457%
- 최대 수익 +26.099% 제외 평균 -0.780%

평균 플러스가 한 종목에 의존한다. rank1이 종목 고유 가드에 막혔다는 사실은 rank2의 기대값을 자동으로 보장하지 않는다. rank2는 별도 전략 ID 또는 최소한 별도 cohort fingerprint·승격 게이트를 가져야 한다.

### 4.3 계약과 위험예산

TP +12%, SL -25%는 단순 이항 손익비 기준 비용 전 손익분기 승률이 약 67.6%다. 실제 계약은 D5 time exit와 gap TP/SL이 있어 이항과 동일하지 않지만 음의 비대칭 계약이라는 사실은 같다.

현재 policy의 micro 정의는 size multiplier 0.1, max open slots 1이다. 그러나 operator override는 absolute cap 500,000원, slots 3을 반환한다. 기본 max order 500,000원 기준 원래 micro cap은 50,000원이지만 실제 cap은 10배다.

| 위험 시나리오 | 동시 손실 | US market asset 대비 | KIS 총자산 대비 |
|---|---:|---:|---:|
| 50만원 × 3 × 25% | 375,000원 | 9.58% | 7.69% |
| 50만원 × 3 × 27.185% | 약 407,775원 | 10.41% | 8.36% |
| 30만원 × 3 × 25% | 225,000원 | 5.75% | 4.61% |

코드 주석의 최악 동시손실 22.5만원은 과거 30만원 상한을 전제로 하므로 현재 설정과 불일치한다. 동일 섹터 포지션 제한도 현재 관측 전용이며 risk gate에 배선되지 않았다.

### 4.4 뉴스·실적·섹터

뉴스·실적 제외 A/B는 shadow에만 기록된다. 최근 9세션 중 5세션에서 news/earnings flagged 후보를 제외하면 rank1이 바뀐다. 반면 AXON과 FA처럼 flagged이면서 양호한 결과도 있어 이진 veto는 반등 엣지를 제거할 수 있다.

필요한 것은 `news=true/false`가 아니라 이벤트 유형별 cohort다.

- 실적 miss·guidance 하향
- 증자·희석
- 회계·규제·소송
- analyst downgrade
- 시장·섹터 동반 하락
- 호재 후 차익실현

섹터 breadth는 2026-08-16 관측 기능이 추가됐지만 아직 이후 미국 세션 표본이 없어 실행 gate로 승격할 근거가 없다.

## 5. 한국 전략 분석

### 5.1 Forward와 live 증거

메인 KR 원장 리포트:

- 관측 11영업일: 최소 15일 미달
- R1 정산 0
- R2 정산 0
- R4 정산 4
- R4 평균 -0.54%
- R4 승률 50%, PF 0.88
- KODEX200 대비 알파 -2.95%
- 1개 주간에만 분포
- 실계좌 신규 매수·청산 0건

R4의 현재 Forward 결과는 승격을 지지하지 않는다. R2는 판정할 표본이 없다.

### 5.2 blindspot 검증 우회

`kr_fallen_blindspot_shadow.jsonl` 105건은 2026-08-13에 과거 2026-02~08 데이터를 일괄 생성한 백필이며 Forward가 아니다. 스캐너는 이 행을 `observe_only=true`로 기록한다.

그러나 현재 `KR_FALLEN_BLINDSPOT_ENTRY_ENABLED=true`이면 주문 브리지가 blindspot 원장을 live 후보로 읽는다. 반면 `kr_fallen_gate_report.py`는 메인 원장만 집계한다. 결과적으로 blindspot은 공식 게이트 통계에는 들어가지 않으면서 주문 후보에는 포함되는 검증 우회 경로다.

### 5.3 게이트 미배선

KR 게이트 리포트는 15영업일, 규칙별 정산 10~15건, 2주 분산을 정의하지만 주문 브리지는 이 결과를 확인하지 않는다. live flag와 ACK, 시간·슬롯·현금·공통 gate만 통과하면 주문한다.

게이트를 운영자가 읽는 문서가 아니라 서명된 authority artifact로 materialize하고 브리지가 직접 검증해야 한다.

### 5.4 동일 티커 교차전략 충돌

US Swing은 `already_holding`과 broker open order를 확인한다. KR Fallen은 자기 source의 로컬 position·pending 수만 세며, 다른 전략이 같은 티커를 보유했는지 확인하지 않는다. 공통 micro submit과 KIS buy precheck도 매수 시 기존 보유를 차단하지 않고 현금만 확인한다.

따라서 KR Fallen이 기존 코어 티커를 다시 사면 브로커 평균단가 한 포지션에 두 전략의 lot이 합쳐지고 청산 소유권이 깨질 수 있다.

독립 subaccount나 신뢰 가능한 virtual lot ledger가 없는 동안 동일 시장·동일 티커의 교차전략 신규 매수를 금지해야 한다.

### 5.5 이벤트·종목 유형

스캐너는 다음을 기록하지만 차단하지 않는다.

- DART 최근 공시
- 실적일 근접
- ETF·우선주·일반주 추정 유형

급락 원인이 유상증자, CB/BW, 감사의견, 거래정지·상장 이슈 등 구조적 악재여도 가격 조건만 맞으면 후보가 될 수 있다. 단순 모든 공시 제외가 아니라 구조적 악재 taxonomy와 Forward 결과가 필요하다.

KR 기본 주문 30만원은 KR 총자산의 약 28.9%다. SL -25% 한 건은 KR 계좌의 약 7.23% 손실이다. 10만원이면 계약손실은 약 2.41%로 내려간다. 실체결 0건인 전략에는 30만원이 과도하다.

## 6. 매도 전략 분석

### 6.1 고정계약 sleeve

US Swing과 KR Fallen은 진입 전에 TP12/SL25/D5를 고정하고 Claude review를 우회한다. 실험의 재현성과 표본 일관성 측면에서 올바른 구조다.

개선 필요점:

- SL은 장중 stop이 아니라 gap을 포함한 catastrophe stop이므로 실제 최악손실을 별도 집계
- D5 세션 계산과 `held_days`를 브로커 거래일 기준으로 통일
- TP·SL·D5 주문도 제출과 체결을 분리
- 계약 변경 시 contract ID를 새로 발급하고 이전 cohort와 혼합 금지

### 6.2 일반 Path A 자동매도

`CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true`일 때 loss cap·stop loss·hard stop도 hold advisor review 경로를 거친다. 극단 손실 임계는 review 후 시스템이 SELL로 덮어쓸 수 있지만 모델 호출 지연·오류·HOLD 정책 생성이 위험 경로에 포함된다.

권한을 다음처럼 분리한다.

- 결정론적 즉시 실행: hard stop, account loss halt, strategy maturity, operator kill, orphan fallback
- 모델 조언 가능: profit-side target, 이익보호, 다음 세션 carry
- 모델 실패: 원래 보호 매도 실행

### 6.3 코어 orphan 포지션

`risk_manager._isolated_strategy_exit_candidate`는 `us_schg_bil_trend_v1`과 `kr_factor_trend_v1`이면 `(True, None)`을 반환해 일반 청산에서 제외한다.

`profit_strategy_order_bridge.run_profit_strategy_handoff`는 당일 signal이 없으면 `no_exact_session_signal`로 리밸런싱 청산 호출 전에 반환한다. 리밸런싱 함수도 `desired_by_source`에 없는 source를 건너뛴다.

현재 KR·US core manifest는 모두:

- `authority=NO_LIVE_AUTHORITY`
- `status=blocked`
- `enabled_ids=[]`
- `signals=[]`

KR은 `core_asset_not_allowed:153130.KS` 오류도 가진다. 8월 shadow target은 KR 275300 50%, 153130 50%여서 기존 275280은 더 이상 목표가 아니다.

따라서 단순 재활성화도 불가능하다. 전략을 유지하려면 enabled ID, 자산 allowlist, 서명 manifest, owner heartbeat, orphan policy를 함께 복구해야 한다. 그렇지 않으면 다음 거래 가능 창에 코어 3포지션을 정리하는 것이 일관된 선택이다.

## 7. 시스템 이슈 우선순위

| ID | 심각도 | 확인된 이슈 | 영향 | 권고 |
|---|---|---|---|---|
| S1 | P0 | core owner 비활성인데 일반 exit 격리 | 실계좌 무기한 보유 | authority heartbeat + orphan liquidation/quarantine |
| S2 | P0 | 주문 접수를 fill로 해석 | 체결률·shortfall·성과 오염 | 브로커 확정 order lifecycle 원장 |
| S3 | P0 | micro policy와 override 위험 불일치 | 이름보다 최대 30배 총노출 | mode별 절대 위험예산 강제 |
| S4 | P0 | KR 동일 티커 교차전략 중복 가능 | 평균단가·청산 소유권 붕괴 | broker position/open order duplicate gate |
| S5 | P1 | KR Forward gate 주문 경로 미배선 | 검증 미달 전략 주문 | signed authority artifact 강제 |
| S6 | P1 | blindspot observe-only가 live 후보, gate 제외 | 검증 우회 | flag off, 별도 원장·게이트 |
| S7 | P1 | rank2 최근 robust 지표 음수 | outlier 의존 확대 | 별도 전략·별도 승격 전 shadow |
| S8 | P1 | US 지정가에 spread·quote age·재호가 없음 | 미체결·기회손실 | execution state machine |
| S9 | P1 | hard-risk exit에 모델 review | 지연·외부 의존 | deterministic risk owner |
| S10 | P1 | MFE/MAE·held_days 불일치 | exit 최적화 불가 | broker/bar 기반 canonical path |
| S11 | P1 | 특별 lane이 일반 candidate audit에 미귀속 | 선택→체결→성과 추적 단절 | strategy order ID 중심 attribution |
| S12 | P1 | 30→50만원, rank2, sector 관측 변경이 근접 | 성과 원인 분리 불가 | cohort fingerprint와 단일 변경 원칙 |
| S13 | P2 | Path A/B live off인데 single judge 관측 지속 | API 비용·운영복잡성 | 소비자 없는 호출 저빈도화 |
| S14 | P2 | `market_context_refresher` 중복 실행 이력 | 데이터·프로세스 중복 | process lock + startup uniqueness test |
| S15 | P2 | 로컬 `broker_position_confirmed=false` 잔존 | 상태 해석 모호 | broker snapshot을 canonical truth로 명시 |
| S16 | P2 | guardian이 KR·US 모두 stale broker truth로 `BLOCK_START` | 다음 개장 전 refresh 실패 시 전체 진입 차단 | preopen 강제 refresh와 gate 해제 확인 |

2026-08-17 14:16 KST 기준 guardian 프로세스 자체는 healthy이나 KR·US broker truth age가 TTL을 초과해 두 시장 모두 `BLOCK_START`다. 비개장·휴장 시간에는 보수적 정상 동작일 수 있지만, 다음 실제 개장 전 강제 refresh 후 `ALLOW_START`로 바뀌는 것을 운영 체크리스트에서 확인해야 한다.

## 8. 주문·원장 결함의 실제 사례

DIOD 주문번호 `0031286459`:

1. 2026-08-14 22:35 KST 3주 매수 접수
2. `live_decisions` entry는 `broker_fill_confirmed=false`, `broker_filled_qty=0`
3. 2026-08-15 05:00 KST session close에서 `DIOD(3주)` 미체결 정리
4. `execution_shortfall_ledger.jsonl`은 `fill_px=98.715` BUY 체결로 기록

원인은 `_submit_micro_probe_buy_order`가 브로커 접수 직후 `[LIVE MICRO_PROBE BUY]`를 남기고, `execution_shortfall_report.py`가 이 로그 가격을 체결가로 가정하기 때문이다.

현재 US Swing 제출 6건 중 브로커 포지션으로 확인되는 체결은 5건, DIOD 1건은 미체결이다. 실제 제출 대비 체결률은 약 83.3%이나 오염 원장에서는 더 높게 보일 수 있다.

목표 lifecycle:

```text
SIGNAL
  -> SUBMIT_REQUESTED
  -> SUBMITTED(order_no)
  -> PARTIAL_FILL | FILLED | REJECTED | CANCELLED | EXPIRED_UNFILLED | ORDER_UNKNOWN
  -> POSITION_OPENED
  -> SELL_SUBMITTED
  -> CLOSED
```

성과·shortfall·MFE/MAE는 `FILLED` 또는 broker-confirmed partial fill만 사용한다.

## 9. 추가 정보 필요성

### 9.1 확대 전에 반드시 필요

1. 주문번호 중심 브로커 확정 체결·부분체결·취소·미체결 원장
2. 실제 수수료·세금·환전·spread·slippage를 포함한 전략별 net PnL
3. bid/ask, spread bps, quote timestamp, 최근 체결가와 주문 후 fill latency
4. 전략별 virtual lot 또는 동일 티커 중복 금지
5. 정확한 거래일 기반 보유기간과 intraday/daily MFE·MAE
6. 이벤트 taxonomy별 Forward 결과
7. 독립 가격 공급자 대조; 미국 역사 검증은 단일 Yahoo vendor 의존
8. 섹터·상관그룹별 동시 계약손실
9. 전략·설정·모델·계약·주문크기를 묶는 immutable cohort fingerprint

### 9.2 현재 전략에는 우선순위가 낮음

- PER, PBR, FCF, 부채, 이익 추정치, 동종업계 할인율
- 장기 analyst target

이 정보는 5일 반등 전략의 필수 입력이 아니다. 사용자가 저평가 중장기 전략을 원할 경우 별도 전략 정의와 검증에 필요하다.

### 9.3 추가할 필요가 없는 것

- 소비자가 없는 Claude 판단 호출 확대
- 같은 가격 피처의 임계값 추가 탐색
- Forward 표본이 없는 상태에서 새 rank·rule live 승격
- 백필 성과를 live 증거로 재명명하는 작업

## 10. 권고 운영안

### 10.1 즉시 운영 기본안

실제 적용은 운영자 승인 후 별도 변경으로 수행한다.

#### 미국

- rank1만 live 후보
- rank2 폴백 off
- 현재 exact contract Forward 10건 전까지 10만원·1슬롯
- absolute probability/predicted-net hurdle은 당분간 shadow A/B 유지
- spread·quote age·fill lifecycle 구현 전 50만원 확대 금지

#### 한국

- `KR_FALLEN_ORDER_HANDOFF_ENABLED=false`
- `KR_FALLEN_LIVE_ENABLED=false`
- `KR_FALLEN_BLINDSPOT_ENTRY_ENABLED=false`
- R2·R4·blindspot을 별도 Forward cohort로 계속 정산
- authority gate·동일티커 가드·이벤트 분류 구현 후 첫 live는 10만원·1슬롯

#### 코어

- 코어 전략을 복구하지 않는다면 SCHG·275280·275300을 다음 거래 가능 창에 정리
- 복구하려면 enabled IDs, 153130 allowlist 결정, manifest 서명, monthly target, owner heartbeat, orphan fallback을 먼저 완료

#### 매도

- hard stop·account halt·D5·orphan은 모델 review 없이 실행
- profit-side 판단에만 모델 사용
- 주문 접수와 체결을 분리한 뒤에만 실현손익 기록

### 10.2 단계별 승격

#### US Stage A — 10만원·1슬롯

현재 exact contract 기준 최소:

- 정산 10건
- 최소 3개 주간에 분산
- mean net > 0
- median net > 0
- 최대 1건 제외 mean > 0
- PF >= 1.2
- broker-confirmed fill ratio >= 90% 또는 미체결 원인·정책 명확화
- critical lifecycle/attribution error 0

통과 시 30만원·최대 3슬롯을 검토한다.

#### US Stage B — 30만원

- 30만원 cohort 정산 20건 이상
- 동시 계약손실 US sleeve의 5% 이하
- 월·시장 breadth·섹터별 한 구간에 손익이 집중되지 않음
- ex-top3 net > 0
- gap-through 초과손실 예산 확인

통과 시 50만원을 검토한다.

#### US rank2

rank1과 혼합하지 않고 별도 최소 10건에서:

- mean·median·max 제외 mean 모두 > 0
- PF >= 1.2
- rank1 차단 사유별 결과 양수

실패 시 제거한다.

#### KR Stage A — shadow

규칙별로:

- 관측 15영업일 이상
- 정산 10건 이상
- 2개 이상 주간
- mean net > 0
- KODEX200 대비 alpha > 0
- median > 0
- max 제외 mean > 0
- PF >= 1.2

R2·R4·blindspot·종목유형·이벤트 유형을 분리한다.

#### KR Stage B — 10만원·1슬롯

- broker duplicate gate 통과
- signed authority 통과
- 실제 체결 10건
- live net·alpha 양수
- 구조적 악재 cohort 손실 제한

이후 30만원을 검토한다.

## 11. 목표 시스템 구조

### 11.1 전략 authority

```text
RESEARCH_ONLY
  -> SHADOW_ELIGIBLE
  -> MICRO_ELIGIBLE
  -> PROBE_ELIGIBLE
  -> STANDARD_ELIGIBLE
  -> SUSPENDED / RETIRED
```

각 상태는 서명된 evidence artifact, contract ID, sizing cap, slot cap, expiry, owner heartbeat를 가져야 한다. operator override는 기간·최대손실·만료시각을 가진 일회성 예외여야 하며 mode 이름을 바꾸지 않고 위험만 확대하면 안 된다.

### 11.2 포지션 소유권

모든 broker lot에 다음을 연결한다.

- `strategy_id`
- `contract_id`
- `signal_id`
- `order_id`
- `fill_id`
- `position_id`
- `exit_owner`
- `authority_expires_at`

owner heartbeat가 만료되면 `ORPHANED`로 전환하고 신규매수 차단, 경고, 사전 정의 청산을 실행한다.

### 11.3 위험예산

슬롯 수가 아니라 계약손실로 계산한다.

```text
position_contract_risk = entry_value × stop_pct + gap_buffer + costs
group_risk = 같은 섹터·상관 충격 position_contract_risk 합
portfolio_risk = 모든 open·pending group risk 합
```

실험 단계는 시장 sleeve의 3%, 검증 단계는 5% 이내를 기본 상한으로 권고한다.

## 12. 필수 회귀 테스트

현재 관련 테스트 55건은 통과했지만 아래 운영 시나리오를 다루지 않는다.

1. core disabled + 기존 broker position -> orphan 감지·청산
2. manifest signals empty -> 기존 desired source가 없어도 orphan 처리
3. SUBMITTED 후 미체결 session-close -> `EXPIRED_UNFILLED`, 성과 제외
4. partial fill -> 실제 수량만 position·PnL 반영
5. execution shortfall -> broker-confirmed fill만 입력
6. KR 다른 전략 동일 ticker 보유 -> 신규매수 차단
7. KR broker-only open order -> 신규매수 차단
8. blindspot `observe_only` -> authority 없으면 주문 불가
9. KR gate 미충족 -> live flag가 켜져도 주문 불가
10. mode micro -> policy의 size·slot cap 초과 불가
11. config cap 변경 -> concurrent loss budget 자동 재계산
12. hard-risk exit -> Claude timeout·HOLD와 무관하게 실행
13. contract ID 변경 -> 이전 성과와 새 cohort 분리
14. MFE/MAE·held session -> broker fill과 거래일 캘린더로 재현

## 13. 구현 우선순위

### P0 — 자금 보호·진실 복구

1. core orphan 감지와 운영자 결정
2. order lifecycle canonical ledger
3. 미체결 DIOD를 shortfall에서 제거하고 전체 원장 재생성
4. micro absolute risk cap과 mode semantics 일치
5. KR 동일티커·broker open order 차단

### P1 — 전략 authority·실행 품질

1. US rank2와 KR live/blindspot을 shadow로 전환
2. KR signed authority gate 배선
3. deterministic hard exit
4. bid/ask·quote age·cancel/reprice 정책
5. strategy attribution 통합
6. MFE/MAE·보유세션 canonicalization

### P2 — 데이터·비용 최적화

1. 뉴스·공시 이벤트 taxonomy A/B
2. sector/breadth·correlation risk 관측
3. independent price vendor cross-check
4. 비활성 Path A/B Claude 호출 저빈도화
5. process singleton lock과 preopen freshness 확인

## 14. 완료 판정 기준

개선 완료는 테스트 통과만으로 선언하지 않는다. 아래 증거가 모두 필요하다.

- 모든 live order가 단일 lifecycle에서 최종 상태를 가짐
- 미체결 주문이 체결·성과 원장에 0건 포함
- 모든 broker position에 유효한 exit owner 또는 명시적 orphan policy 존재
- 현재 contract ID별 Forward 표본과 과거 contract가 분리됨
- US/KR 승격이 signed authority 결과와 일치
- 동일 티커 교차전략 주문이 정책대로 차단 또는 virtual lot으로 분리됨
- concurrent loss budget이 설정·실계좌 자산과 일치
- hard-risk exit가 모델 가용성과 무관하게 작동
- 실현손익·수수료·환율·체결수량이 브로커 내역과 대사됨

## 15. 한계와 남은 운영자 결정

- 본 분석은 로컬에 저장된 최신 브로커 스냅샷을 사용했으며 외부 브로커 API를 새로 호출하지 않았다.
- 표본이 작아 기대수익을 확정하지 않고 승격·중단 기준을 제시했다.
- 현재 활성 목적을 5일 급락 반등으로 가정했다. 저평가 중장기 전략을 원하면 별도 전략 설계가 필요하다.
- 이 문서의 설정 off·포지션 청산·사이징 변경은 권고이며 아직 실행하지 않았다.

운영자에게 남은 핵심 결정은 두 가지다.

1. 코어 전략을 완전히 복구할 것인지, 기존 코어 3포지션을 정리할 것인지
2. US rank1 검증 크기를 엄격한 10만원·1슬롯으로 낮출지, 기존 30만원을 유지할지

## 16. 최종 판정

현재 수익성의 핵심은 복잡한 Claude·Path A/B 파이프라인이 아니라 **미국 day_losers rank1의 제한된 5일 반등 계약**이다. 이 전략은 유지 가치가 있으나 정확한 현재 계약 Forward는 아직 0건이고 운영 크기가 정책상 micro보다 훨씬 크다.

한국은 아직 live edge가 확인되지 않았고 검증 gate·blindspot·동일티커 소유권에 구조적 공백이 있다. 매수 신호를 늘리기보다 shadow 검증으로 되돌리는 것이 맞다.

가장 먼저 해결할 것은 새 전략 발굴이 아니라 다음 세 가지다.

1. 청산 주인이 없는 포지션 제거
2. 주문 접수와 체결 진실 분리
3. 전략 증거와 실제 위험 크기의 일치

이 세 가지가 완료된 뒤에만 미국 rank1을 단계 확대하고, KR 전략을 별도 Forward 근거로 승격해야 한다.
