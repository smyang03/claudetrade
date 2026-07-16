# 시스템 수익성 통합 개선 보고서 (2026-07-17)

## 0. 최종 판정

이번 작업은 “매수 횟수를 늘리는 튜닝”이 아니라, 실제 순손익 원장을 기준으로
시장별로 이기는 경로와 지는 경로를 분리하고 손실 누수·데이터 왜곡·출구 소유권
충돌을 제거한 작업이다.

- 시스템의 과거 실현 수익성은 아직 흑자가 아니다.
  - canonical net KR: 62건, 평균 -0.554%, PF 0.66
  - canonical net US: 250건, 평균 -0.220%, PF 0.81
- 따라서 이번 변경을 “수익 보장”으로 표현하지 않는다.
- 다만 적자 전략을 계속 주문하던 경로, 고가 존 추격, 코어 포지션의 가짜 일중
  TP/SL, 재시작 후 오래된 RVOL 피처 재사용, 예산 이중 축소와 같은 구조적 손실
  원인은 실제 라이브에서 제거했다.
- 최종 라이브 preflight는 `fail=0`이고 관련 회귀 테스트 533건이 통과했다.

## 1. 시스템의 강점과 약점

### 강점

1. 브로커 실잔고를 최종 진실로 사용하는 구조가 있다.
   재시작 전후 포지션·미체결 주문을 비교할 수 있고, 이번 재시작에서도 SCHG 5주가
   손실 없이 이어졌다.
2. 전략별 주문 권한, 출구 소유자, 시장별 설정을 분리할 기반이 있다.
   이를 이용해 코어·PathB·US swing을 서로 다른 계약으로 운영할 수 있다.
3. 후보·판단·주문·청산·forward 결과가 여러 원장에 축적돼 있어 반사실 검증이 가능하다.
4. US momentum과 opening-range-pullback, KR Claude-price처럼 양수 코호트가 일부 존재한다.
5. fail-closed, kill switch, 주문 상한, 일일/슬롯 상한, preflight가 이미 있어 공격
   레인을 제한된 크기로 시험할 수 있다.

### 약점

1. 운영 이벤트 원장과 canonical 순손익 원장이 달라 과거에는 수익성을 잘못 읽을 수 있었다.
2. 전략별 성과 차이가 큰데도 일부 레인이 같은 live allowlist를 공유했다.
3. 저회전 코어 포지션에 일반 일중 TP/SL·hold review가 개입할 수 있었다.
4. 재시작 시 같은 세션의 오래된 장초 분봉 피처가 최신 후보 평가에 섞일 수 있었다.
5. 후보 모델은 결측 피처가 많을 때 train/serve 계약이 깨질 수 있었다.
6. US swing은 운영자 micro 시험은 가능하지만 정식 forward 근거는 아직 부족하다.
7. 과거 PathB lifecycle 고아 행과 execution attribution 누락이 남아 학습 원장의
   정확도를 떨어뜨린다.

## 2. 수익 원장 재검증

수익성 판단의 기준을 `v2_canonical_performance` 순손익 원장으로 통일했다.
`state/live_decisions.jsonl`은 운영 이벤트 관측용으로만 남긴다.

### 시장·전략별 canonical 결과

| 시장/전략 | N | 평균 net | PF | 판정 |
|---|---:|---:|---:|---|
| KR Claude-price | 23 | +0.893% | 2.14 | 생존 레인 |
| US opening-range-pullback | 7 | +1.192% | 3.37 | 유망하지만 소표본 |
| US momentum | 13 | +0.222% | 1.18 | 제한적 live 유지 |
| US Claude-price | 205 | -0.186% | 0.84 | 진입가·출구 개선 필요 |
| US gap-pullback | 22 | -1.034% | 0.28 | live 차단 |
| KR gap-pullback | 20 | -0.903% | 0.53 | Plan A 차단 유지 |
| KR momentum | 11 | -2.165% | 0.26 | Plan A 차단 유지 |
| KR opening-range-pullback | 6 | -1.530% | 0.01 | Plan A 차단 유지 |

단순히 하루 진입 건수를 1~3건으로 줄이는 시뮬레이션도 전부 음수였다.
`per_market_cap_1`조차 평균 -0.406%, PF 0.69이므로 “적게 사면 자동으로
수익이 난다”는 가설은 기각했다. 전략·진입 품질을 먼저 고쳐야 한다.

## 3. 이번에 적용한 구조적 개선

### 3.1 수익 원장 단일화

- `tools/full_profitability_review.py`의 헤드라인과 일일 진입 상한 시뮬레이션을
  canonical net 원장 기준으로 변경했다.
- 운영 close event와 실제 net portfolio를 별도 표로 분리했다.
- 앞으로 전략 승격·폐기는 canonical net 기준으로만 판단한다.

### 3.2 US 적자 전략 차단

- `US_GAP_PULLBACK_LIVE_ENABLED=false`
- 후보 생성과 shadow 관측은 유지하지만 실제 주문만 차단한다.
- 근거: canonical 22건, 평균 -1.034%, PF 0.28, 월별 안정성도 부족했다.
- US momentum은 제한적으로 유지한다.

### 3.3 US PathB 고가 존 추격 제한

- `PATHB_ZONE_FILL_MODE_US=enforce_wait`
- buy zone 상단 67% 이상이면서 목표거리 5% 이상인 최악 셀에서는 주문을 취소하지
  않고 기존 PathB plan을 `WAITING`으로 유지한다.
- 가격이 임계값 아래로 내려오면 같은 plan과 같은 주문 경로로 재평가한다.
- 새 주문 파이프라인을 만들지 않아 이중 주문 위험이 없다.
- KR에는 적용하지 않는다. KR 표본은 같은 규칙의 방향이 반대였기 때문이다.

근거:

- US 전체 231건 평균 -0.416%
- 최악 셀 48건 평균 -1.152%, 승률 16%
- 최악 셀 제외 시 평균 -0.224%, 거래당 약 +0.193%p 개선
- 이 수치는 “회피한 승자의 기회비용”을 포함하지 않은 과거 코호트 분석이므로,
  live 원장에는 blocked/released/missed outcome을 계속 기록한다.

### 3.4 저회전 코어의 출구 소유권 완전 분리

- SCHG/BIL·KR factor core 포지션은 일반 일중 TP/SL을 저장하지 않는다.
- 기존 포지션의 가짜 TP/SL도 재시작 시 0으로 정리한다.
- `exit_owner`와 `exit_contract=strategy_rebalance_only`를 명시한다.
- 대시보드에는 “전략 전용 출구”로 표시해 일반 손절이 없는 것을 오류로 오인하지 않게 했다.

### 3.5 US swing 30만원 예산 계약 수정

- 운영자 override의 30만원 상한이 0.1배 sizing에 다시 곱해져 3만원으로 축소되던
  이중 scaling을 제거했다.
- 현재 execution authority:
  - effective mode: micro
  - 절대 주문 상한: 300,000원
  - 하루 최대 1건
  - 동시 슬롯 1개
- 정식 연구 권한은 forward 표본 부족으로 shadow이며, 운영자 trial만 별도로 허용된다.

### 3.6 RVOL 시간축·재시작 캐시 누수 수정

- US naive timestamp를 KST로 해석한 뒤 뉴욕 시장 시각으로 변환하도록 수정했다.
- 과거에는 22:36 KST가 US 개장 후 6분이 아니라 786분으로 저장될 수 있었다.
- 재시작 연속성 JSONL과 runtime handoff 모두 같은 신선도 필터를 사용한다.
- 같은 세션이라도 시장 개장 경과 시간이 5분 넘게 어긋난 피처는 복원·소비하지 않는다.
- 오래된 피처는 최신 분봉 또는 `first_observed` 피처로 교체된다.
- preflight는 최신 캡처 배치의 RVOL 시계 계약을 검사한다.
- 최종 preflight에서 `candidate_audit.rvol_clock_contract` 경고가 사라졌다.

### 3.7 후보 consensus의 결측 내성

- 학습·서빙 양쪽에 실제 존재하는 피처만 effective feature set으로 사용한다.
- 요청 피처, 실제 사용 피처, 탈락 피처를 상태 파일에 모두 기록한다.
- 결측 필드 하나 때문에 scorer 전체가 죽거나 잘못된 기본값으로 주문 근거가 되는 것을 막았다.
- 현재 US shadow scorer는 정상적으로 `SCORED` 상태이며 live 주문과는 연결하지 않았다.

### 3.8 운영 관측성

- preflight에 다음을 추가했다.
  - 후보 consensus 실행환경 의존성
  - US swing 실행 권한·예산
  - PathB US zone-fill 이중 소스 설정
  - US live 전략 allowlist
  - RVOL 시장 시계 계약
- 대시보드와 Telegram에 다음을 표시한다.
  - US momentum live 여부
  - US gap-pullback 관측 전용 여부
  - PathB zone-fill mode와 임계값
  - 코어 포지션의 전략 전용 출구

## 4. 시장별 최종 운영 구조

### 미국장

1. 코어: SCHG/BIL 추세 슬리브
   - 현재 SCHG 5주 보유
   - 일반 일중 출구와 완전 격리
   - US 코어 주문 상한 30만원
2. 선택적 단기 레인
   - momentum live 유지
   - gap-pullback 실제 주문 차단
   - opening-range-pullback은 양수지만 N=7이므로 확대하지 않음
3. PathB
   - Claude-price 후보는 유지
   - 상단 추격 최악 셀은 `enforce_wait`
   - tail capture/기존 hard risk 계약은 유지
4. US swing
   - 운영자 micro trial만 30만원, 1일 1건, 1슬롯
   - 정식 승격은 forward sessions·matured·mean·PF 게이트 통과 후

### 한국장

1. KR Claude-price/PathB를 주력 선택 레인으로 유지한다.
   canonical 23건 평균 +0.893%, PF 2.14로 현재 가장 명확한 종목 선택 엣지다.
2. KR Plan A momentum/gap/ORP는 음수이므로 현재 차단 상태를 유지한다.
3. KR split-runner와 paired observer는 기존 계약을 유지한다.
4. US zone-fill 규칙은 KR에 적용하지 않는다.
5. KR factor core는 별도 저회전 슬리브이며 일반 analyst 방향·일중 출구와 격리한다.

## 5. 시뮬레이션에서 적용하지 않은 것

다음 아이디어는 수치가 좋아 보이는 구간이 있었지만 강제 적용하지 않았다.

- 고정 손절 확대: 국면별 부호가 바뀌어 안정적이지 않았다.
- peak-trail 최적값 추종: 상위 3개 기여 제거 후 견고하지 않았다.
- preopen 30/60분 확인 진입: 최종 수익률로 보면 양수지만 실제 확인 시점
  진입가부터 종가까지는 KR -1.626~-1.795%, US -0.230~-0.264%로 음수였다.
  이는 대표적인 lookahead 착시다.
- 일일 진입 수만 축소: canonical replay에서 전부 음수였다.
- US gap-pullback 재활성: 현 원장이 명확히 반대한다.
- consensus 모델 live enforce: 아직 shadow 표본과 티커 중복 제거 검증이 부족하다.

저회전 코어 시뮬레이션은 방어 슬리브의 근거로 유지한다.

- QQQ SMA10+BIL OOS: CAGR 20.55%, Sharpe 1.31, MDD -14.37%
- MULTI_EW_TREND_BIL: CAGR 11.66%, Sharpe 1.38, MDD -12.97%
- SECTOR_TOP3_MOM_BIL: CAGR 18.15%, Sharpe 1.15, MDD -15.70%

이는 개별 종목 알파가 아니라 베타 참여와 낙폭 관리 레인이다.

## 6. 최종 라이브 검증

- 안전 재시작: 완료
- 체크포인트:
  `data/backups/live_maintenance_20260716_150101_before_restart_profitability_final_cache_freshness_20260716`
- 봇 PID: 44856
- 모든 역할 프로세스 생존: true
- 브로커 truth: fresh
- US 포지션: SCHG 5주
- 평균가: $34.7599
- 미체결 주문: 0
- TP/SL: 0/0
- exit owner: `us_schg_bil_trend_v1`
- exit contract: `strategy_rebalance_only`
- live policy:
  - US momentum: true
  - US gap-pullback: false
  - PathB US zone-fill: enforce_wait
- 테스트: 533 passed
- py_compile: 통과
- git diff check: 오류 없음
- preflight: `ok=True`, `fail=0`

## 7. 남은 문제와 우선순위

현재 주문을 막는 신규 장애는 없다. 다만 아래는 다음 개선 사이클에서 처리해야 한다.

1. 과거 PathB lifecycle 위생
   - previous-session active 3건
   - full terminal lifecycle missing event 4건
   - 다른 run을 참조하는 closed evidence 1건
   현재 세션의 주문·포지션 불일치는 아니지만 학습 원장 정리가 필요하다.
2. execution attribution
   - traded 49건 중 execution_decision_id 누락 23건
   - 주문 안전 문제는 아니지만 후보 모델 학습 품질을 약화한다.
3. us_swing 정식 forward 근거
   - 운영자 micro override와 연구 승격은 분리돼 있다.
   - matured 표본과 PF가 기준을 통과하기 전 자동 확대 금지.
4. 후보 outcome backlog
   - stale daily pending과 insufficient sample이 많다.
   - prospective registry와 장후 outcome 작업의 처리량·watermark 감시가 필요하다.
5. canonical 전체 손익은 아직 음수
   - 이번 변경의 성공 여부는 과거 백테스트가 아니라 신규 체결의 net으로 판정해야 한다.

## 8. Forward 승격·폐기 기준

1. US PathB zone wait
   - blocked/released/missed outcome을 모두 기록
   - release 체결의 canonical net과 미체결 기회비용을 함께 비교
   - N 15 미만에는 임계값 확대 금지
2. early/tail capture
   - observed MFE 대비 realized net capture
   - N 15 이상, 비용 후 평균 양수, 상위 3건 제거 후 양수
3. us_swing
   - forward sessions 5 이상
   - matured 15 이상
   - 평균 net 0 이상
   - PF 1.0 이상
4. candidate consensus
   - 신규 세션만 사용
   - 동일 티커 중복 제거
   - 월/반월 블록 부호 유지
   - live 주문 연결 전 paired quote/슬리피지 검증

## 9. 결론

현재 시스템의 수익성 강화 방향은 다음 네 줄로 요약된다.

1. KR은 검증된 Claude-price/PathB를 중심으로 운용하고 음수 Plan A 레인을 열지 않는다.
2. US는 저회전 코어 + 제한적 momentum + 개선된 PathB 진입가/출구 구조로 운용한다.
3. 음수인 US gap-pullback과 오래된 장중 피처, 가짜 코어 TP/SL 같은 구조적 누수를 제거한다.
4. 확대는 “매수가 적다”는 감정이 아니라 신규 canonical net과 forward 게이트로만 결정한다.

이번 변경은 과거 적자 시스템을 즉시 흑자로 선언하는 작업이 아니다. 대신 어떤 경로가
돈을 벌고 잃는지 잘못 읽던 문제를 고치고, 확인된 적자 경로를 닫고, 유망 경로가 실제로
수익을 만드는지 측정 가능한 라이브 구조로 바꾼 작업이다.
