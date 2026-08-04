# 지금 이 순간의 운영 상세 (2026-08-04 19:58 기준)

시스템이 "지금 무엇을 어떻게 하고 있는지"의 완전 기술. 큰 그림은 system_status_full_20260804.md, 이 문서는 실행 층위.

---

## 1. 돌아가는 프로세스 (8역할, 봇 PID 24128 — 오늘 17시대 재시작본)

| 역할 | 하는 일 | 주기 |
|---|---|---|
| trading_bot | 세션 사이클(시세→게이트→주문→매도관리), 판단 체인, 브리지 호출 | KR 09:00~15:30 / US 22:30~05:00, 장 초반 2분·이후 5분 사이클 |
| preopen_scheduler | 시각 기반 잡 발사(아래 §3) | 60초 틱 |
| broker_truth_scheduler | 브로커 보유·미체결·현금 스냅샷 갱신 | 세션 중 상시, 장외 절전 |
| live_guardian | 죽은 역할 감시·봇 재기동(ALLOW/BLOCK 게이트) | 워치독이 5분마다 |
| core_shadow_tracker | 코어 sleeve 월간 목표 추적 | 6시간 |
| integrity_check / counterfactual_pipeline / dashboard | 무결성 감시 / 반사실 라벨 / UI | 각자 주기 |

지금(장외): 봇 하트비트 7초 전 — 대기 사이클만 돌며 US 개장(22:30)을 기다리는 중.

## 2. 지금 들고 있는 것 (실계좌)

| 종목 | 수량 | 상태 | 관리 주체 |
|---|---|---|---|
| **FRMI** | 38주 (진입 $5.5225) | 현재 $6.00 = **+8.6% 평가** | `kr`아님`us_swing_5d` 계약: TP $6.185 / SL $4.14 / D5(금) 만기. 오늘 밤이 보유 2일차 세션 |
| SCHG | 5주 | 코어(손절 없음 설계) | 코어 sleeve — 신규신호는 차단, 보유만 유지 |
| 275280/275300 | 각 1주 | 코어, 폭락장 통과 중(−13~15%대) | 동일 |
| 현금 | 약 428만원(통합) + US 주문가능 ~$2.3K | — | 신규 매수는 us_swing 30만 캡만 소비 |

## 3. 오늘 밤~내일의 자동 스케줄 (전부 무개입)

```
22:10  US 뉴스 수집 잡
22:20  us_swing_shadow_runner:
       KIS day_losers 랭킹 수집 → 하드필터 → GBM 스코어링
       → 신호 저장: 오늘부터 top10 (US_SWING_STORE_TOP_K=10, 주문은 여전히 rank1만)
       → 하드필터 5조건 shadow 기록(us_hard_filter_shadow.jsonl)
22:30  US 개장 — 봇 세션 사이클 시작, R1(haiku)×3 → R2(sonnet) → 합의 mode 산출
22:35~23:00  진입창: rank1 신호가 게이트 체인 통과 시 매수 1건(≤30만, TP/SL/D5 부착)
       게이트 순서: authority(override 슬롯3) → 실시세 → 브로커truth(fail-closed)
       → 갭3%/추격0.5% → 예산·현금 → 공통게이트(RISK_HALTED·블랙아웃·클러스터)
세션 중  FRMI 계약 감시(TP 도달 시 즉시 매도, 갭 상방이면 시가 청산)
05:00  US 마감 → 마감 후 정산·라벨 갱신
09:00  KR 세션(내일) — 신규매수 0 유지, 코어 매도관리만, 판단 라벨 축적
16:10  KR 급락 스캔 4일차: 캐시 갱신(641종목)→후보 기록(ma20_disc 포함)→D5 정산
```

## 4. 데이터가 쌓이는 곳 (매일 자동 증가)

| 원장 | 현재 | 용도 |
|---|---|---|
| `data/shadow/kr_fallen_shadow.jsonl` | 13행 (정산 1: 002995 TP +11.75% / 대기 12) | KR 3파전 판정 재료 — R1/R2/R3 전 규칙 소급 판정 가능한 피처 완비 |
| `data/analysis/us_swing_shadow.db` | 신호 75행 (+오늘 밤 10행 예정) | US rank별 forward — 일3건·rank 확대 판정 재료 |
| `data/shadow/us_hard_filter_shadow.jsonl` | 10행 | 5조건 깔때기 forward |
| `data/ml/decisions.db` | 21.8만 행+ | 전 거래·평가 이력 (FRMI 88행 포함) |
| 판단 라벨(daily_judgment) | 매 세션 R1×3+합의 | 국면 게이트 가치의 지속 검증 |

## 5. 게이트 카운터 (지금 값)

- **US 30건 게이트**: forward **1/30** (FRMI 진행 중) — 판정식: net합>0 AND 평균알파(IWM 대비)>0 → 일3건 재론. 60건 net≤0이면 중단 제안
- **KR R2 게이트**: 규칙 충족 정산 **0/10** — rv20의 폭락 기억(7/28~31이 20일 창을 빠지는 ~8/25)까지 구조적 침묵 예상, 9월 초 도달 전망. 판정 도구: `python tools/kr_fallen_gate_report.py` (알파·국면 컬럼 포함)
- **정상 변동 범위**(반증 아님): 연속손실 ≤6건, 구간 낙폭 ≤−30만

## 6. 대기 상태로 준비된 것 (스위치만 남음)

| 준비물 | 상태 | 켜는 조건 |
|---|---|---|
| KR micro 브리지 (`runtime/kr_fallen_order_bridge.py`) | 코드 탑재·테스트 5종·매도 소유권(kr_fallen_5d) 등록 완료, 3중 게이트 off | R2 게이트 통과 + 운영자 승인 → `KR_FALLEN_ORDER_HANDOFF_ENABLED/LIVE_ENABLED/ACK` |
| 일3건 확대 | 코드상 슬롯3 이미 활성, 일일 한도만 1 | US 30건 판정 통과 + 운영자 승인 |
| PathA/PathB 복원 | 코드 무손상, 플래그 오프 보존 | 운영자 결정 시 플래그 원복+재시작 |

## 7. 감시 체계 (지금 걸려 있는 것)

- us_swing 선출·매수·차단 라이브 모니터 (이벤트 즉시 보고+푸시)
- KR 세션 로그 상시 모니터 (차단·청산·에러)
- 신호 DB 폴링 (22:20 선출 즉시 포착)
- 읽기 전용 감사 MCP 7도구 (`check_buy_gate`·`get_shadow_performance` 등 — 운영자도 조회 가능)
- 워치독 로그(`logs/runtime/watchdog.log`) — 5분 틱 기록

## 8. 지금 사람이 할 일

**없음.** 다음 운영자 결정 지점은 ① 8월 말 US 30건 리포트 승인 ② 9월 초 KR 게이트 승인 ③ (선택) 보존 정리 실행(`retention_audit.py --apply-bak`, 25.7GB 회수). 그 사이 시스템은 표본을 쌓고, 이상은 모니터가 물어온다.
