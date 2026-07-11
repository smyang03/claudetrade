# 수익성 개선 후속 작업 상세 계획 — 2026-07-11

## 1. 문서 목적

이 문서는 2026-07-10~11 분석·구현에서 남은 작업을 실제로 실행하고 검증하기 위한 기준서다.

핵심 원칙은 다음과 같다.

1. `검정 불가`, `표본 부족`, `결과 미달`을 실패 낙인으로 끝내지 않는다.
2. 원인을 `배선 문제 / 데이터 문제 / 표본 문제 / 전략 무효 / 운영 잠금`으로 분리한다.
3. 비용·세금·FX를 무시하지 않고, 비용 차감 후 수익으로만 승격을 판단한다.
4. shadow 결과가 좋다는 이유만으로 주문 권한을 자동으로 열지 않는다.
5. 독립 세션 수, 국면 일관성, 좌측 꼬리, 소수 이벤트 집중도를 함께 본다.
6. enforce 전환은 확정 보고서와 운영자 승인으로만 진행한다.

최신 반영 커밋:

- `01c5d96bf43c9898d26cf7c73dc6feb35805cb5b`
- 브랜치: `feat/net-hygiene-improvements-20260626`
- 포함 내용: KR bullish strength shadow probe, Claude WATCH 재평가 계약, 비용 포함 판독 도구, preflight 메모리 개선

---

## 2. 전체 우선순위와 의존관계

```text
통제된 재시작
  ├─ broker truth fresh/trusted 확인
  ├─ Profit Path shadow 실제 발화 확인
  ├─ KR bullish probe 감사 DB 기록 확인
  └─ US Swing 스케줄러/0.5% chase 계약 확인
       ↓
forward 표본 축적
  ├─ US Swing executable shadow 3건
  ├─ KR bullish probe 20개 독립 강세 세션
  ├─ Profit Path 시장별 20세션·60 matched
  └─ Breadth 진단 40세션
       ↓
수익·꼬리·국면·비용 검증
       ↓
size challenger 검토
       ↓
운영자 승인 후에만 제한적 enforce
```

P0는 다음 실거래 세션 전에 끝내야 한다. P1은 forward 수익 근거를 만드는 작업이며, P2는 잘못된 분석이나 청산 구조가 승격 판단을 오염시키지 않도록 하는 작업이다.

---

## 3. P0-1 — 통제된 재시작과 배포 확인

### 현재 상태

- 현재 확인된 live PID는 `44320`이다.
- 이 프로세스는 2026-07-11 03:11 KST에 시작했기 때문에 커밋 `01c5d96`의 KR bullish probe 코드는 메모리에 올라가 있지 않다.
- 신규 코드는 디스크와 원격 브랜치에는 반영됐지만 실행 중 프로세스에는 hot patch되지 않는다.

### 필요한 사유

코드가 커밋됐다는 사실과 운영 중이라는 사실은 다르다. 재시작 없이 기다리면 신규 shadow 표본이 쌓이지 않으며, 이를 시장에서 신호가 없었다고 오판할 수 있다.

### 개선·실행 절차

1. KR/US 주문·미체결·보유 포지션을 broker truth로 확인한다.
2. live preflight의 `FAIL=0`을 확인한다.
3. 운영자가 정한 안전 시간에 정상 종료 후 `start_live_stack.bat` 경로로 재시작한다.
4. 신규 trading bot PID와 시작시각을 기록한다.
5. 신규 PID의 환경 및 effective config를 확인한다.
6. dashboard, scheduler, bot 프로세스가 중복 실행되지 않았는지 확인한다.

### 점검 사항

- [ ] 신규 PID의 시작시각이 커밋 이후다.
- [ ] `ADAPTIVE_LIVE_CONDITION_SHADOW_ENABLED=true`가 적용된다.
- [ ] KR probe는 `shadow_only=true`, `local_promotion_allowed=false`다.
- [ ] US Swing은 `max_entry_slippage_pct=0.5`를 사용한다.
- [ ] US Swing handoff, submit, live ACK 잠금은 계속 닫혀 있다.
- [ ] 후보 선정 전후 broker 주문 수가 변하지 않는다.
- [ ] dashboard/API health가 정상이다.
- [ ] 동일 역할 프로세스가 두 개 이상 존재하지 않는다.

### 완료 조건

- 재시작 후 preflight `FAIL=0`.
- 신규 PID에서 KR probe와 Profit Path shadow 로그가 생성됨.
- broker 포지션·주문·체결 상태가 재시작 전후 일치함.

### 금지사항

- shadow 확인을 위해 주문 권한을 임시로 열지 않는다.
- 실행 중 포지션이 존재할 때 broker truth 확인 없이 강제 종료하지 않는다.

---

## 4. P0-2 — Profit Path shadow 실제 발화 확인

### 현재 상태

2026-07-11 점검 결과:

| 시장 | prediction_n | matched_n | forward_sessions | snapshot |
|---|---:|---:|---:|---|
| KR | 0 | 0 | 0 | 없음 |
| US | 0 | 0 | 0 | 없음 |

모델 파일은 존재하고 preflight상 `ready=true`지만, 실제 주문 직전 prediction과 forward 연결은 아직 한 건도 없다.

### 필요한 사유

현재 0건은 모델이 나빠서가 아니라 다음 세 가지 중 하나일 수 있다.

1. 변경 전 프로세스가 실행 중이어서 코드가 발화하지 않음.
2. 신규매수 후보가 해당 지점까지 도달하지 않음.
3. 배선이나 candidate-key 연결이 잘못됨.

이 원인을 분리하지 않고 기다리면 배선 버그를 표본 부족으로 오인한다.

### 개선·실행 절차

1. 정상 재시작 후 첫 KR/US 후보 선택 사이클을 관찰한다.
2. `PROFIT_PATH_SHADOW_ENABLED_KR/US=true`를 effective config에서 확인한다.
3. 주문 직전 후보마다 prediction 생성 여부를 확인한다.
4. prediction이 없으면 다음 순서로 원인을 분해한다.
   - 모델 artifact 로드 실패
   - 필수 feature 결측
   - ticker/candidate key 불일치
   - runtime path 미호출
   - exception이 로그에서 삼켜짐
5. prediction은 생기지만 matched가 0이면 outcome linker의 session/ticker/known_at 연결을 점검한다.

### 점검 사항

- [ ] 첫 정상 후보 사이클 후 `prediction_n > 0`.
- [ ] 각 prediction에 시장, ticker, known_at, model_version이 있다.
- [ ] 비용 차감 기대수익과 abstain 결과가 함께 기록된다.
- [ ] 60분 이후 `matched_n` 또는 pending 상태가 증가한다.
- [ ] 중복 prediction이 동일 후보를 표본 여러 건으로 부풀리지 않는다.
- [ ] prediction이 주문 액션을 직접 변경하지 않는다.

### 완료 조건

- 시장별 최소 1개 실제 prediction 생성.
- mature 가능한 prediction이 outcome과 연결됨.
- 다음 선택 사이클에도 0건이면 `표본 부족`이 아니라 `배선 결함`으로 전환해 수정한다.

### 재현 명령

```powershell
python tools/profit_evidence_preflight.py
python tools/profit_path_forward_monitor.py --market KR
python tools/profit_path_forward_monitor.py --market US
```

---

## 5. P0-3 — Broker truth 신선도 복구

### 현재 상태

- 전체 live preflight는 `ok=true`, `FAIL=0`이었다.
- 다만 broker truth snapshot은 `age_gt_ttl`로 stale/untrusted 경고였다.
- 당시 로컬 상태는 포지션·미체결·당일체결 0이었지만, stale snapshot은 신규 진입 판단 근거로 사용할 수 없다.

### 필요한 사유

로컬 DB가 0이라고 해서 broker가 0이라는 보장은 없다. stale truth 상태에서 재시작·신규매수·포지션 cap을 판단하면 중복 주문이나 보유량 불일치가 발생할 수 있다.

### 개선·실행 절차

1. 다음 거래 세션 전 KIS 잔고, 미체결, 당일체결을 새로 조회한다.
2. 조회 성공시각과 TTL을 확인한다.
3. 로컬 lifecycle/path-run 상태와 broker 상태를 대조한다.
4. 차이가 있으면 주문을 내기 전에 reconcile 원인을 분석한다.

### 점검 사항

- [ ] `fresh=true`, `trusted=true`.
- [ ] positions/open_orders/today_fills가 broker와 일치한다.
- [ ] orderable cash와 통화가 올바르다.
- [ ] stale fallback equity가 아닌 broker equity를 사용한다.
- [ ] 불일치가 있으면 자동 수정 전에 read-only 보고서를 남긴다.

### 완료 조건

- 신규 진입 직전 broker truth age가 TTL 이내다.
- 로컬과 broker의 보유·미체결 차이가 0이다.

---

## 6. P1-1 — US Swing executable forward 성숙

### 현재 상태

- historical OOS: 293 decision sessions.
- 선택 정책: `rank1_skip_v1`, 1슬롯, 정수주.
- 0.5% adverse entry 가정 결과: 55 trades, mean net 약 +2.09%, PF 약 1.51, sleeve return 약 +10.33%, realized-equity MDD 약 -2.61%.
- forward: observation session 1, executable matured 0.
- ledger: total 5, pending 5.
- authority: shadow, 주문 제출 불가.

### 필요한 사유

역사 결과는 긍정적이지만 단일 공급원, 연도별 성과 차이, worst 약 -27%라는 꼬리 위험이 있다. 실제 전략과 동일한 rank1·정수주·1슬롯 경로에서 forward 성숙 결과가 필요하다.

### 개선·실행 절차

1. 매 US 세션 preopen scheduler가 signal을 생성하는지 확인한다.
2. rank1 후보의 affordability와 1슬롯 점유 상태를 기록한다.
3. reference 대비 0.5% chase 이내에만 executable shadow entry를 기록한다.
4. TP 12%, catastrophe SL 25%, 최대 5세션 계약으로 성숙시킨다.
5. 한 세션에서 여러 후보를 forward 권한 표본으로 세지 않는다.

### 점검 사항

- [ ] strategy-matched executable trade만 권한 표본에 포함.
- [ ] rank2~5는 diagnostic이며 authority 표본에서 제외.
- [ ] 한 주 가격이 5만원 cap을 초과하면 skip 처리.
- [ ] slot이 점유 중이면 신규 rank1을 건너뜀.
- [ ] entry FX, exit FX, fee, slippage가 net KRW에 포함됨.
- [ ] missing outcome은 critical data error로 처리.

### 완료 조건

최소 조건:

- executable matured trades ≥ 3
- mean net ≥ 0
- profit factor ≥ 1
- critical data error = 0
- operator가 결과를 확인하고 설정 변경을 승인

이 조건은 MICRO 검토의 최소 조건이지 충분조건이 아니다. 한 이벤트에 수익이 집중되면 추가 표본을 요구한다.

### 재현 명령

```powershell
python tools/us_swing_preflight.py
python tools/us_swing_shadow_runner.py --session-date YYYY-MM-DD
```

---

## 7. P1-2 — KR 강세장 bullish probe forward 검증

### 현재 상태

신규 규칙:

- KR만 대상
- `MILD_BULL`, `MODERATE_BULL`, `AGGRESSIVE`
- KOSPI +2% 이상
- 상승 종목 비율 60% 이상
- 개장 후 90분 이내
- trainer `PLAN_A ≥ 65`
- trainer risk score ≤ 35
- evidence ceiling `BUY_READY`
- 하드블록 없음
- 하루 최대 1종목
- 20만원 cap, 정수주, 왕복비용 0.5%
- shadow only

2026-07-10 소급 결과:

| 선택 | 경로 | 비용 후 net | 20만원 cap 손익 |
|---|---|---:|---:|
| 003280 | immediate | +1.0836% | 약 +2,162원 |
| 003280 | volume_surge | +1.0836% | 약 +2,162원 |
| 003280 | wait_30m | +1.4423% | 약 +2,867원 |
| 003280 | wait_60m | +1.1432% | 약 +2,279원 |

### 필요한 사유

7월 10일은 강한 장에서 evidence상 BUY_READY 후보가 존재했지만 Claude가 전부 WATCH로 남겼다. 다만 하루 결과만으로 진입을 강제하면 시장 베타와 사후 규칙 설계 착시를 분리할 수 없다.

### 개선·실행 절차

1. 정상 재시작 후 강세 조건이 충족된 시점의 최초 eligible cohort를 고정한다.
2. Plan-A score 1위 한 종목만 선택한다.
3. 같은 종목의 immediate, volume_surge, wait_30m, wait_60m을 동시에 기록한다.
4. Claude가 WATCH를 선택한 구체 사유와 재평가 조건을 저장한다.
5. 종가 이후 비용 차감 net과 정수주 KRW 손익을 성숙시킨다.

### 점검 사항

- [ ] market context가 120초 이내 fresh다.
- [ ] preopen/stale digest가 강세 조건으로 사용되지 않는다.
- [ ] 최초 eligible 시점 이후 더 좋은 가격을 소급 선택하지 않는다.
- [ ] 하루 한 종목만 독립 표본으로 계산한다.
- [ ] WATCH reason이 `WATCH_ONLY` 같은 일반 문구가 아니다.
- [ ] recheck condition이 가격·시간 기준으로 관측 가능하다.
- [ ] 비용 차감 전후를 모두 저장하되 판정은 net으로만 한다.

### 완료 조건

- 독립 강세 세션 ≥ 20
- immediate/volume/wait 경로별 성숙률과 결측률 확인
- 평균, 중앙값, PF, worst, 좌측꼬리, top-3-day 제외 결과 보고
- benchmark/시장상승률 대비 초과수익 확인
- enforce가 아니라 먼저 제한적 size challenger 여부를 운영자가 판단

### 재현 명령

```powershell
python tools/kr_bullish_probe_report.py
```

---

## 8. P1-3 — Profit Path 승격 근거 축적

### 현재 상태

- KR validation AUC 약 0.538.
- US validation AUC 약 0.638.
- 두 시장 모두 validation selected n=0.
- forward prediction/matched도 현재 0이다.
- 모델은 `SHADOW`, 자동 승격 없음.

### 필요한 사유

AUC가 0.5보다 높다는 사실만으로 수익성이 증명되지 않는다. 중요한 것은 비용 차감 후 경로 간 상대 기대값과 abstain이 실제 forward에서 손실 진입을 줄이는지다.

### 개선·실행 절차

1. 시장별로 prediction과 60분 outcome을 축적한다.
2. 즉시진입, pullback reclaim, VWAP reclaim, abstain을 분리한다.
3. 국면별 calibration과 수익 hurdle을 별도로 계산한다.
4. 같은 session/ticker의 반복 호출을 하나의 독립 판단 단위로 정리한다.
5. 성과가 좋은 경로가 소수 이벤트에 집중되는지 검사한다.

### 점검 사항

- [ ] 시장별 forward sessions ≥ 20.
- [ ] matched predictions ≥ 60.
- [ ] 최소 2개 시장 국면에 표본 존재.
- [ ] calibration ECE가 허용 범위 이내.
- [ ] validation net LCB > 0.
- [ ] top-3 event 제거 후에도 mean net > 0.
- [ ] abstain이 손실을 피하면서 지나치게 많은 우측꼬리를 제거하지 않음.

### 완료 조건

모든 조건을 통과해도 처음에는 enforce blocker가 아니라 `size challenger`로만 검토한다. 승격은 운영자 승인 후 별도 커밋으로 진행한다.

---

## 9. P1-4 — 실제 FX 비용 확정

### 현재 상태

- US fee 왕복 약 0.50%는 KIS 실측 근거가 있다.
- FX 왕복 0.20%는 코드 기본 가정이다.
- 시스템은 달러 예수금을 계속 회전시키므로 거래마다 KRW↔USD 환전이 발생하지 않을 가능성이 있다.

### 필요한 사유

FX를 과소평가하면 가짜 수익이 생기고, 과대평가하면 실제 알파를 버릴 수 있다. 비용을 수익률로 이기려면 먼저 어떤 비용이 거래별 변동비이고 어떤 비용이 초기 고정비인지 분리해야 한다.

### 개선·실행 절차

운영자가 KIS MTS/명세서에서 다음을 1회 확인한다.

1. 2026년 4~7월 실제 환전 횟수.
2. 환전별 적용환율과 기준환율 차이.
3. 자동환전 또는 통합증거금 사용 여부.
4. 매수·매도 때마다 환전됐는지, 달러잔고가 유지됐는지.

그 결과에 따라:

- 거래별 환전이면 실제 median/p75 FX 비용을 반영.
- 달러잔고 회전이면 거래별 FX 비용을 0에 가깝게 하고 초기·최종 환전을 고정비로 분리.
- 혼합이면 실제 환전 발생 거래에만 비용을 붙임.

### 점검 사항

- [ ] 명세서 근거 파일/수치를 별도 보관.
- [ ] `US_FX_SPREAD_RATE_PER_SIDE` 변경 전·후 전체 성과 재계산.
- [ ] 역사 데이터와 live 신규 데이터가 같은 비용 정의를 사용.
- [ ] 비용을 줄였다는 이유만으로 전략을 승격하지 않음.

### 완료 조건

- FX 비용의 실측 근거와 적용 방식이 문서화됨.
- 모든 US 분석 도구가 같은 비용 상수를 사용함.

---

## 10. P1-5 — US Swing 독립 데이터 공급원 교차검증

### 현재 상태

- 역사 OOS는 긍정적이지만 단일 market-data vendor 의존이다.
- 모델·선택·exit 재생은 point-in-time 규율을 따르지만 원천 가격 오류를 독립 검증하지 못했다.

### 필요한 사유

분할, 배당, 비정상 시가, 누락 캔들, survivorship 문제 하나가 소수 대형 승자의 수익을 부풀릴 수 있다.

### 개선·실행 절차

1. rank1 55건 전체 또는 최소 모든 TP/SL/최악손실 거래를 독립 공급원과 대조한다.
2. adjusted/unadjusted 가격 정의를 통일한다.
3. 다음 필드를 비교한다.
   - 전일 종가
   - 다음 세션 시가
   - 5세션 고가·저가·종가
   - split/dividend event
4. 불일치가 큰 거래를 제외한 민감도 결과를 다시 계산한다.

### 점검 사항

- [ ] 가격 차이 tolerance를 사전 고정.
- [ ] 불일치 거래를 임의로 유리하게 교체하지 않음.
- [ ] 제외 전·후 mean, PF, MDD, worst를 함께 제시.
- [ ] top 이벤트의 원천 가격이 독립적으로 확인됨.

### 완료 조건

- 주요 수익·손실 거래의 독립 가격 일치.
- 교차검증 후에도 비용 포함 방향이 유지됨.

---

## 11. P1-6 — 순수익 원장과 equity curve 완전성

### 현재 상태

2026-07-10 설계 감사에서는 `pnl_krw_net` 결측 203건이 기록됐다. 이 수치는 현재 DB에서 다시 확인해야 하며, 결측이 남아 있으면 자본가중 equity curve와 실제 MDD를 확정할 수 없다.

### 필요한 사유

거래별 수익률 평균은 계좌 수익률이 아니다. 포지션 크기, 동시 보유, 비용, 환율, 부분청산이 들어간 KRW 원장이 있어야 전략이 실제 계좌에서 돈을 버는지 판단할 수 있다.

### 개선·실행 절차

1. 현재 DB에서 결측 수와 원인을 다시 집계한다.
2. 결측을 다음으로 분리한다.
   - entry/exit 가격 결측
   - FX 결측
   - fee 결측
   - 부분체결/부분청산 연결 실패
   - legacy schema
3. 원천 사실이 있는 행만 backfill한다.
4. 추정값은 `estimated`로 표시하고 실측값과 혼합하지 않는다.
5. 일자순 realized equity curve를 생성한다.

### 점검 사항

- [ ] 동일 execution을 중복 합산하지 않음.
- [ ] 부분청산 KRW 손익 합계가 최종 포지션 손익과 일치.
- [ ] fee/FX 정의가 최신 비용 계약과 일치.
- [ ] daily equity, MDD, turnover, gross/net 차이를 재현 가능.
- [ ] 결측 backfill 전·후 결과를 함께 보존.

### 완료 조건

- enforce 판정 대상 구간의 `pnl_krw_net` coverage가 95% 이상.
- 미복구 행은 이유별로 명시됨.
- 자본가중 equity curve와 MDD가 생성됨.

---

## 12. P2-1 — 상관 클러스터 판독 버그와 가설 재정의

### 현재 상태

현재 `correlation_cluster_review.py` 실행 결과:

- 3종목 이상 동시청산일: 28일
- 상관 계산 성공: 28일
- 평균상관 ≥ 0.5 고상관일: 0일
- 저상관일: 28일, 결합 net 합 -28.1
- 그런데 최종 문구는 “고상관일 손실이 더 큼”으로 출력됨.

### 필요한 사유

고상관 표본이 0인데 고상관 위험을 주장하는 것은 분석 버그다. 이 결과를 A3 상관 사이징이나 S1 청산순위 근거로 사용하면 존재하지 않는 패턴으로 위험 제한을 만들게 된다.

### 개선·실행 절차

1. 비교 집단 중 하나가 0이면 `판정 불가`를 출력하도록 수정한다.
2. 평균상관 threshold 0.5가 현재 종목군에서 과도한지 분포를 먼저 출력한다.
3. 연속형 상관값과 결합손실의 관계를 분석한다.
4. 산업·테마·시장베타 중복을 별도 변수로 본다.
5. 동시청산일과 동시보유일을 혼동하지 않는다.

### 점검 사항

- [ ] 빈 집단에서 방향 결론을 내리지 않음.
- [ ] threshold를 결과를 본 뒤 유리하게 바꾸지 않음.
- [ ] 상관 계산은 진입 전 60일 데이터만 사용.
- [ ] 같은 event-day를 독립 거래 여러 건으로 세지 않음.
- [ ] 고상관이 아니라 단순 시장 급락이 원인인지 통제.

### 완료 조건

- 빈 표본·단일 집단 테스트 추가.
- 연속형/버킷 분석 결과 보고.
- 근거가 없으면 A3/S1을 폐기하는 것이 아니라 `상관 아닌 다른 집중 원인`으로 재분석.

---

## 13. P2-2 — 조기익절 tier shadow 재검증

### 현재 상태

현재 read-only 판독:

| 시장 | n | 실제 평균 | tier 평균 | 평균 개선 | 실제 최대 | tier 최대 |
|---|---:|---:|---:|---:|---:|---:|
| US | 240 | -0.182% | +0.067% | +0.249%p | +17.167% | +9.484% |
| KR | 51 | -0.218% | +0.824% | +1.042%p | +8.164% | +5.777% |

평균은 개선되지만 우측 꼬리를 줄인다.

### 필요한 사유

현재 시스템의 수익은 소수 큰 승자에 의존할 수 있다. 평균만 높이기 위해 큰 승자를 자르면 장기 PF나 계좌 복리에는 오히려 나쁠 수 있다.

### 개선·실행 절차

1. 부분익절 비율 25%, 33%, 50%를 고정 비교한다.
2. 시장별 activation level을 train/test로 분리한다.
3. session-block 또는 월별 walk-forward로 검증한다.
4. 평균뿐 아니라 P90/P95/max와 top-event 제외 성과를 본다.
5. 기존 ladder, no-tier, risk-recovery runner를 동일 경로에서 비교한다.

### 점검 사항

- [ ] 비용과 추가 매도 수수료 반영.
- [ ] 실제 시간순 MFE를 사용하고 사후 peak를 진입 판단에 쓰지 않음.
- [ ] 우측꼬리 감소가 risk budget 내인지 확인.
- [ ] 시장별 서로 다른 threshold의 과적합 여부 확인.
- [ ] n이 적은 KR은 별도 보수 기준 적용.

### 완료 조건

- OOS Δnet > 0.
- PF와 MDD가 함께 개선되거나, MDD 개선 대가로 손실된 tail이 명시적 risk budget 이내.
- top-3 이벤트 제거 후에도 방향 유지.

---

## 14. P2-3 — Risk-recovery runner counterfactual

### 현재 상태

원금 전액회수 free-carry는 소액·정수주 계좌에서 잔량이 거의 남지 않을 수 있다. 대안은 `초기 위험금액 회수 + 잔량 runner`다. 시간축 필드는 배선됐지만 충분한 실제 MFE/MAE 순서 표본 검증이 남았다.

### 필요한 사유

조기익절의 평균 개선과 우측꼬리 훼손 사이에서, 위험금액만 회수하고 잔량 target cap을 제거하면 손실 위험을 줄이면서 볼록성을 보존할 가능성이 있다.

### 후보 규칙

```text
if MFE >= 2R
and MFE occurred before MAE
and qty >= 4
and sharp market reversal is false:
    초기 risk_krw만큼 이익이 확정되도록 일부 매도
    residual stop = max(entry + all_in_cost, current protective floor)
    residual target cap 제거
```

### 개선·실행 절차

1. 시간순 minute path가 있는 종결 거래만 사용한다.
2. 기존 ladder, early-tier, risk-recovery runner를 동일 체결 경로에서 재생한다.
3. 정수주 반올림 후 잔량이 실제로 남는지 확인한다.
4. runner giveback과 event gap 손실을 분리한다.

### 점검 사항

- [ ] 사후 MFE peak를 미리 안 것으로 가정하지 않음.
- [ ] qty<4에서는 자동 제외.
- [ ] 부분매도 추가 비용 반영.
- [ ] 잔량 stop이 비용 포함 breakeven 아래로 내려가지 않음.
- [ ] 우측꼬리 보존과 좌측꼬리 제한을 동시에 측정.

### 완료 조건

- 기존 ladder 대비 net 개선.
- high-MFE 승자 tail을 훼손하지 않음.
- giveback 증가가 사전 risk budget 이내.
- 처음에는 `PROBE_EXIT` A/B shadow로만 적용.

---

## 15. P2-4 — 청산 시간축과 지연 구간 정규화

### 현재 상태

과거 `stop_trigger_price/at` primary 표본은 매우 적고 약 9시간 차이가 관측돼 timezone 또는 필드 의미 오염 가능성이 제기됐다.

### 필요한 사유

청산 손실이 전략 stop 문제인지, Claude review 지연인지, 주문 전송·체결 지연인지 분리해야 개선 방향이 달라진다.

### 개선·실행 절차

다음 시각을 timezone-aware UTC ISO로 분리 저장한다.

- `triggered_at`
- `detected_at`
- `review_started_at`
- `order_sent_at`
- `broker_acked_at`
- `filled_at`

그리고 다음 구간을 측정한다.

- trigger → detect
- detect → review
- review → sent
- sent → ack
- ack → fill

### 점검 사항

- [ ] naive datetime 저장 금지.
- [ ] KST 표시와 UTC 저장을 혼동하지 않음.
- [ ] 부분체결은 최초·최종 fill을 분리.
- [ ] 오래된 행은 신뢰 가능한 원천이 있을 때만 backfill.
- [ ] 측정이 정상화되기 전 stop threshold를 변경하지 않음.

### 완료 조건

- 신규 stop/exit 이벤트 95% 이상이 모든 핵심 시각을 가짐.
- 지연 구간별 median/p90과 손익 영향 보고.

---

## 16. P2-5 — US spread와 participation 측정 배선

### 현재 상태

- US 단일종목 판단에서 `spread_bps` 결측이 반복 관측됐다.
- 주문금액 대비 ADV 참여율은 명시적으로 저장되지 않는다.

### 필요한 사유

gross alpha가 있어도 넓은 spread와 높은 참여율은 실현수익을 훼손한다. 이를 측정하지 않으면 모델 문제와 실행비용 문제를 구분할 수 없다.

### 개선·실행 절차

1. US KIS 호가에서 bid/ask를 저빈도로 수집한다.
2. point-in-time `spread_bps`와 quote age를 기록한다.
3. `participation_rate = order_krw / ADV_krw`를 계산한다.
4. shadow 기록만 하고 초기에는 주문 차단에 사용하지 않는다.
5. 실현 slippage와 spread/participation 관계를 분석한다.

### 점검 사항

- [ ] stale quote를 fresh spread로 사용하지 않음.
- [ ] bid/ask가 역전되거나 0이면 결측 처리.
- [ ] ADV는 결정시점 이전 데이터로 계산.
- [ ] spread가 넓다는 이유로 시장가 전환하지 않음.
- [ ] 데이터 coverage와 결측 원인을 함께 보고.

### 완료 조건

- US 신규 주문 의도 표본의 spread coverage ≥ 90%.
- participation coverage ≥ 95%.
- slippage 설명력이 확인된 뒤에만 대기/사이징 레버로 검토.

---

## 17. 관찰 항목 — Breadth S3

### 현재 판정

`narrow melt-up`은 검증된 수익 엔진이 아니다. prior-close breadth는 US Swing ledger의 diagnostic attribution으로만 유지한다.

현재 확인된 문제:

- 역사 OOS에서 breadth 관계가 단조롭지 않다.
- 실제 entry 42세션의 방향과 역사 결과가 충돌한다.
- “좁을수록 좋다”는 가설은 기각됐다.

### 재검토 조건

다음을 모두 충족할 때만 레버 후보로 다시 검토한다.

1. 독립 forward entry sessions ≥ 40.
2. 비교 상태별 최소 10세션 또는 충분한 연속형 회귀 coverage.
3. frozen historical OOS와 forward 방향 일치.
4. session-block bootstrap contrast LCB > 0.
5. top-3-day 제외 후에도 contrast > 0.
6. 비용 0.80%에서 생존.
7. base strategy 전체 net을 훼손하지 않음.
8. 운영자 승인.

### 금지사항

- 현재 breadth로 후보 순위, 사이즈, 주문 권한을 변경하지 않는다.
- 동일 날짜의 breadth와 당일 이전 시점 진입을 연결하지 않는다.

---

## 18. 종료 항목 — VIX term structure S2

### 최종 판정

- 방어 throttle: 전략 일치 OOS에서 기각.
- 공격 boost: VIX level별 부호가 불안정하고 소수 episode 집중.
- 별도 shadow tag: 추가하지 않음.

### 종료 사유

검정 불가로 포기한 것이 아니라, 전략과 동일한 top-selection·비용·FX 조건으로 재검한 결과 안정적인 레버가 아니었다. 따라서 현재는 더 기다리는 항목이 아니라 닫힌 가설이다.

### 재개 조건

완전히 다른 평균회귀 전략을 독립적으로 설계하고, 별도 entry/exit 계약과 OOS를 사전 등록할 때만 새 가설로 시작한다.

---

## 19. 운영 점검 주기

### 매 세션

- broker truth freshness
- 신규 prediction/shadow 발화 수
- critical data error
- duplicate candidate/session 여부
- 실제 주문 불변 여부

### 매주

- US Swing matured 수
- KR bullish probe 독립 세션 수
- Profit Path matched 수
- 경로별 결측률
- Claude WATCH review contract 완성률
- 비용 포함 누적 net과 worst event

### 승격 검토 시

- 평균과 중앙값
- PF와 MDD
- 독립 event-day 수
- top-1/top-3 이벤트 제외 결과
- 국면별 부호 일관성
- 비용/FX stress
- benchmark excess
- 데이터 공급원 독립성
- 운영자 승인 기록

---

## 20. 권장 실행 순서

1. 통제된 재시작과 broker truth 갱신.
2. KR/US Profit Path prediction 발화 확인.
3. KR bullish probe 감사 DB 기록 확인.
4. 상관 클러스터 빈 표본 판정 버그 수정.
5. FX 명세서 대조 및 비용 정의 확정.
6. 순수익 원장 coverage 재감사.
7. US Swing·KR probe·Profit Path forward 수집.
8. 조기익절 tier와 risk-recovery runner read-only 비교.
9. spread/participation 측정 배선.
10. 최소 표본 충족 후 통합 수익성 판정 보고서 작성.

이 순서를 지키면 `기능이 동작하지 않아서 표본이 없는 상태`와 `정상 동작했지만 수익 근거가 부족한 상태`를 혼동하지 않게 된다.
