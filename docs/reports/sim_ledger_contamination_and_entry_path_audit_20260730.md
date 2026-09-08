# 시뮬 원장 오염과 진입 경로 감사 (2026-07-30)

## 요약

라이브 모니터링에서 시작해 진입 경로 관통 확인까지 진행했고, 그 과정에서 **시뮬레이터가
라이브 원장 두 곳에 쓰고 있었다**는 사실을 발견했다. 실주문은 차단 가드가 있었으나 DB 쓰기는
막혀 있지 않았다. 이 오염이 이번 세션에서 낸 판정 세 건을 뒤집었고, 그중 하나는
"전날 수정이 최상위 병목을 뚫었다"는 잘못된 성공 판정이었다.

오염 차단과 ORP 진단 컬럼 추가를 커밋·푸시하고 라이브 스택을 재시작했다(`1154d86`).
mean_reversion 파라미터 수정은 근거가 오염 데이터였음이 확인돼 보류했다.

---

## 1. 시뮬 원장 오염

### 규모

| 원장 | 오염 행수 | 기간 | 식별 |
|---|---|---|---|
| `data/intraday_strategy_log.db` | SIMTK 1,411 | 07-29 13:14 ~ 07-30 01:34 | 티커만 |
| `data/ml/decisions.db` | SIMTK 835 | 07-29 13:13 ~ 07-30 01:34 | 티커만 |

`decisions.db` 쪽은 `is_simulated=0` / `data_source='live'`로 기록됐다. 시뮬 여부를 표시하는
컬럼이 있는데도 채워지지 않아 플래그 필터가 무용지물이었다.

`intraday_strategy_log.db`는 `bot_mode='paper'`로 남았으나 **페이퍼 봇도 같은 값을 쓴다**
(TSLA 143행, INTC 135행 등 정상 데이터). 즉 두 원장 모두 `SIMTK` 티커에만 의존해 구분된다.
시뮬이 실제 티커를 썼다면 영구히 구분 불가였다.

### 원인

`tools/sim_entry_path_gates.py`는 `trading_bot`을 실제로 태운다(`run_cycle` 호출).
모듈 레벨 `place_order`/`cancel_order`/`precheck_order`는 차단본으로 덮어썼으나,
`isdb.insert_probe()`와 `ml.db_writer.write_decision()` 경로는 그대로 살아 있었다.

### 조치

세 시뮬 도구(`sim_entry_path_gates` / `sim_exit_path_gates` / `sim_pathb_entry_gates`)에
원장 쓰기 가드를 추가했다.

- `isdb.insert_probe` / `update_outcome` / `init` 무력화
- `trading_bot._ML_DB_ENABLED = False` + `_ml_write` 계열 무력화
  (`_ml_write_eval`이 이 플래그를 먼저 보므로 가장 확실한 지점)
- PathB 시뮬은 `trading_bot`을 import하지 않아 `isdb` 가드만 유효. 선제 차단으로 둠

검증: 시뮬 재실행 후 두 원장 행수 불변(835 / 1411). 가드 전에는 실행마다 증가했다.

---

## 2. 오염이 만든 오판

세션 중 낸 판정 중 오염 데이터에 기댄 것들이다.

| 보고한 내용 | 실제 (`ticker <> 'SIMTK'`) |
|---|---|
| 07-29 US watch_only 100% → **73.7%로 개선** | **100.0%** — 개선 없음 |
| 전략 평가 도달 1건 → **544건** | **0건** |
| "7e43301이 최상위 병목을 뚫었다" | 근거 없음 |
| ORP 발화 **66건**인데 진입 0 | 발화 **0건** (66건 전부 SIMTK) |
| mean_reversion `ma_ok=0/92` | 92건 중 84건 SIMTK, 실제 표본 **IREN 8건** |

07-29 US의 787행이 전부 SIMTK였고, 그것이 "watch_only 탈출분"으로 읽힌 숫자다.

### 오판 메커니즘

`vol_ok=0`인 84건의 `price/ma60`이 **전부 정확히 0.916**, `vol_ratio`가 **전부 4.17**이었다.
실제 시장 데이터에서 나올 수 없는 분포이고, 이 값이 시뮬 합성값과 일치하는 것이 단서였다.
값의 분포를 보기 전까지 네 차례 오판이 누적됐다.

---

## 3. 확정 사실 (오염 제외 실측)

### US 진입 병목

```
전체 평가 143,856 (전 기간)
├─ SKIPPED    120,932 (84%)
│   └─ watch_only 116,721   ← 전체의 81%. selection이 trade_ready로 승격하지 않음
├─ NO_SIGNAL   22,317 (15.5%)
├─ BUY_SIGNAL   1,842 (1.3%)  → filled 12건
└─ BLOCKED        262 (0.2%)
```

US 세션별 watch_only 비율(SIMTK 제외): 07-24 99.9% / 07-27 97.2% / 07-28 100.0% / 07-29 **100.0%**.
**병목은 selection 단계에 그대로 있다.**

### US 라이브 base 전략 2종의 상태

**mean_reversion** — `mr_fired=1`의 마지막 기록이 2026-05-12. 이후 79일간 0건.
기록 배관은 정상(재시작 후에도 기록됨). 국면별 RSI 임계표(`strategy/mean_reversion.py:41-51`)가
AGGRESSIVE 31 → NEUTRAL 25 → CAUTIOUS_BEAR 23 → DEFENSIVE 20으로 내려가는데 `ma60_thr=0.95`는
고정이다. 04월(임계 32) US 발화 570회 → 05월 이후 0회.

단, 양립 불가 근거의 실제 표본은 IREN 8건(`price/ma60` 0.803~0.832)뿐이다.
`ma60_thr`을 0.90으로 낮춰도 발화는 0건 — vol 조건과 배타적이다.

**ORP** — 라이브(`bot_mode='live'`) 07월 1,932회 평가 중 발화 **3건(0.2%)**.

| 차단 사유 | 건수 | 비중 |
|---|---|---|
| orp_entry_window_expired | 1,265 | 65.5% |
| orp_not_formed | 242 | 12.5% |
| orp_range_too_high | 138 | 7.1% |
| orp_pullback_too_shallow | 86 | 4.5% |
| orp_pullback_too_deep | 71 | 3.7% |
| orp_forming | 63 | 3.3% |
| orp_volume_low | 50 | 2.6% |
| 발화 | 3 | 0.2% |

시간창 밖 1,570 / 창 안 362 → **창 안 발화율 0.8%**. 시간 사유 81.3%는 결함이 아니다 —
창이 60분(개장 15~75분)이고 세션이 390분이므로 산술적 귀결이다. 창 안 병목은
OR 레인지 상한 초과 38%, 눌림 깊이 이탈 43%.

### 진입 경로 관통 (시뮬, 160 시나리오)

```
주문루프 도달 36/160 | 하네스 오류 0
  MILD_BULL 18/40   BUY_READY(judge)  32/80
  CAUTIOUS  18/40   기술신호(ORP)       4/40
  DEFENSIVE  0/40   신호없음            0/40
  HALT       0/40
```

DEFENSIVE·HALT 80건은 설계상 0이 정답이므로 제외하면 **BUY_READY 32/40(80%)**,
기술신호 4/20(20%). BUY_READY 미도달 8건은 전부 경과 370분 `late_session_cutoff`(설계).
기술신호 20%는 ORP 시간창과 시나리오 경과시간(10/40/120/300/370분) 분포의 결과이며 결함이 아니다.

`from_high_block,from_high_EXEMPT`가 함께 찍힌 케이스가 전부 통과 — 7e43301의 BUY_READY 면제가
시뮬 수준에서 작동한다. PathB는 플랜 등록 24/24(진입 실행·부분체결은 도구 범위 밖).

### 국면 도달률 (US 세션)

| 날짜 | 허용 국면 | 차단 국면 | 허용 비율 |
|---|---|---|---|
| 07-27 | MILD_BULL 16, CAUTIOUS 3 | MILD_BEAR 3 | 86% |
| 07-28 | NEUTRAL 25, MILD_BULL 19, CAUTIOUS 3 | MILD_BEAR 31 | 60% |
| 07-29 | MILD_BULL 48, CAUTIOUS 11, NEUTRAL 8 | MILD_BEAR 7, CAUTIOUS_BEAR 6 | 84% |
| 07-30 | — | CAUTIOUS_BEAR 17 | 0% |

`SINGLE_SYMBOL_JUDGE_BUY_READY_REGIMES=MILD_BULL,MODERATE_BULL,AGGRESSIVE,CAUTIOUS,NEUTRAL`.
`CAUTIOUS`와 `CAUTIOUS_BEAR`는 둘 다 실재하는 별개 국면이며 후자가 빠진 것은 일관된 설계다.
07-27~29는 US 세션의 60~86%가 허용 국면이었다 — **국면이 상시 닫혀 있던 것이 아니다.**

### early judge 재큐 무한 루프

재시작 후 `early_judge_trigger_eval` 26건 전부 `post_open_feature_not_ready`.
큐 8종목 중 ADBE·KO·APH·MDLZ·THC는 현재 후보 풀에 없어 피처 갱신 경로가 없다.
재큐마다 `queued_at`·`attempts`가 리셋돼 `MAX_AGE_MIN=60`·`MAX_ATTEMPTS=3` 만료가 걸리지 않는다.

단, 매수를 막는 원인은 아니다 — `hard_skip`은 예산을 차감하지 않고 `selected_rows`에도
들어가지 않아 신규 종목 슬롯을 뺏지 않는다.

---

## 4. 적용한 변경 (`1154d86`)

| 파일 | 내용 |
|---|---|
| `ml/schema.sql` | ORP 진단 6컬럼 |
| `ml/db_writer.py` | 마이그레이션·INSERT·params·docstring |
| `trading_bot.py` | `fired_col` 매핑 2곳 + 장마감 검토 isolated 제외 |
| `tools/sim_entry_path_gates.py` | 원장 쓰기 가드 + 주석 정정 |
| `tools/sim_exit_path_gates.py` | 원장 쓰기 가드 |
| `tools/sim_pathb_entry_gates.py` | 원장 쓰기 가드(선제) |

**ORP 진단 컬럼** — `mr_*`/`vb_*`/`mom_*`/`gap_*` 4종은 있는데 `opening_range_pullback`만
없어서 US live base 전략 하나의 발화·차단 사유를 `decisions.db`에서 추적할 수 없었다.
`diagnostics()`가 이미 반환하는 값이라 저장과 매핑만 추가했다.

**장마감 검토 isolated 제외** — 전략이 청산을 소유하는 코어인데 generic hold advisor가
SELL을 걸고 다음 개장 전에 `_clear_isolated_strategy_generic_exit_flags`가 다시 지우는 왕복이
매 세션 반복되며 Claude 호출만 소모했다(07-29 KR 275280/275300 실측).
`_post_session_position_review`에는 이미 같은 가드가 있었고 `session_close`에만 없었다.

**시뮬 주석 정정** — "분봉이 없어 ORP를 발화시킬 수 없다"는 사실과 달랐다.
`build_bot`이 `_or_high`/`_or_low`/`_or_formed`를 주입한다. ORP 시간창과 mean_reversion
라이브 무발화도 함께 적어 결과 오독을 막았다.

### 검증

- `py_compile` 6파일 OK
- 관련 테스트 107 passed / DB·스키마 72 passed
- mojibake OK
- 전체 스위트 3,367 passed / 10 failed (아래 5절 — 기존 결함, 변경과 무관)

### 재시작

`tools/restart_live_stack_safely.ps1`, DryRun 후 본 실행.

| 항목 | 결과 |
|---|---|
| 봇 PID | 29960 → 38524 (12:50:42), 중복 없음 |
| 보조 8종 | 전부 재기동 |
| ORP 컬럼 마이그레이션 | 6개 적용 확인 |
| 포지션 복구 | 4건 (SCHG·275280·275300·AXTI) |
| 사이클 재개 | 12:52:02, 12:57:19 — 로그 라인 39541→39546으로 새 코드 반영 확인 |
| 재시작 후 에러 | 0건 |

---

## 5. 보류·미해결

**A — mean_reversion 파라미터.** 양립 불가 자체는 사실이나 실제 표본이 IREN 8건(단일 종목)이고,
`ma60_thr`을 0.90으로 낮춰도 vol 조건과 배타적이어서 발화가 0이다. 임계를 임의로 정해
커밋하면 반증 불가능한 변경을 라이브에 넣게 된다. 표본 축적 후 재판정.

**SIMTK 오염 정리.** `decisions.db` 835행 + `intraday_strategy_log.db` 1,411행.
삭제는 파괴적이라 운영자 판단 영역. 분석 시 `ticker <> 'SIMTK'`로 우회 가능.

**테스트 날짜 종속성.** 전체 스위트에서 PathB 10건이 `EARNINGS_WINDOW_BLOCK`으로 실패한다.
`runtime/earnings_calendar.py`가 모듈 전역 `_MEM_CACHE`로 실적 캘린더를 들고 있고,
커밋되지 않는 런타임 파일 `data/earnings_calendar.json`을 읽는다. `000660`의 실적일이
07-29로 들어 있어 차단 창(±1일) 안이다. 단독 실행 시엔 캐시가 비어 fail-open으로 통과하므로
**전체 스위트에서만 실패하고, 날짜에 따라 결과가 달라진다.** 내 변경과 무관하며
검증 4회로 확인했다(단독 142 passed / DB 조합 149 passed / earnings 조합 148 passed / 전체 2회 동일 실패).

**US 진입 = BUY_READY 단일 경로.** mean_reversion 무발화 + ORP 발화율 0.8%이므로
US 진입은 실질적으로 judge 경로 하나다. judge 경로 장애가 곧 진입 전면 정지를 뜻한다.
`[judge overlay 이월]` 로그는 아직 0건 — c5ecbc5의 라이브 발동은 국면이 열려야 확인된다.

**보유 포지션.** 4건 전부 손실, 전부 `exit_policy=isolated_strategy`.
AXTI −19.05%(`sl_pct=0.25`로 손절 미발동, `max_hold` 5일이라 07-31 만기).
hold advisor·weak_mfe 효과는 07-23 이후 청산 0건이라 검증 표본이 없다.

---

## 6. 재발 방지

이번 세션의 오판 원인은 하나로 수렴한다 — **한 소스만 보고 결론을 냈다.**
ORP 관련으로 세 번, 오염 관련으로 두 번 연속 틀렸고, 티커 컬럼 하나만 확인했으면
즉시 걸릴 것이었다.

1. **분석 착수 전 기존 규칙·판정 확인을 고정 단계로.** 이번에 `audited_broker_backfill`
   분리 규칙이 저장소 4곳에 명문화돼 있었는데 모르고 섞었고, (B) 순환성도 같은 유형이었다.
2. **집계 전 값 분포를 본다.** 동일값 반복(0.916 × 84건)은 합성 데이터의 신호다.
3. **원장 분석에는 `ticker <> 'SIMTK'`를 기본으로.** 과거 오염은 남아 있다.
4. **시뮬 도구는 주문뿐 아니라 원장 쓰기도 막는다.** 이번에 가드를 넣었으나,
   새 도구를 만들 때 같은 실수가 반복될 수 있다.
