# 개선 적용 후 재시뮬레이션 검증 (2026-07-23)

커밋 `ce8ef6c`로 데이터 흐름 수정 4건을 적용한 뒤, 같은 하네스로 재시뮬레이션해
**실제로 고쳐졌는지**를 확인했다. 결과를 있는 그대로 적는다.

## 1. ★ P0-1은 실데이터에서 무효과였다 — 내 진단이 과했다

수정 내용: `data_quality` 라벨이 안 실려오면 pack이 `'unknown'`을 대입해 PROBE_READY로
강등하던 것을, 라벨 부재 + `data_state=confirmed`인 경우에만 면제.

**구규칙 vs 신규칙을 85,889행에 직접 돌린 결과: 변화 0건.**

```
data_quality 라벨이 없는 행의 data_state 분포
  KR actionable  partial   13건
  US actionable  partial   35건 · missing 14건
  US 기타         partial   17건 · missing  2건
  → 라벨 부재 + confirmed = 0건
```

라벨이 없는 행은 **예외 없이 다른 필드도 없다.** 그래서 이미 `data_state`로 강등되고,
`data_quality` 경로는 애초에 구속력이 없었다. 어제 보고서에서 "judge actionable 행의
US 7.54%·KR 5.53% 집중"이라고 쓴 건 맞지만, **그게 곧 강등 원인이라는 추론이 틀렸다.**
공변량(다른 필드도 함께 빔)을 원인으로 오독했다.

수정 자체는 남긴다 — 잘못된 강등 경로를 제거한 것이고 회귀 테스트 6건이 붙었다.
다만 **오늘 매수를 늘리지 못한다.** 우선순위에서 P0를 뗀다.

이건 저장소가 13번 겪은 패턴과 같다: 단위로 그럴듯한 개선이 실제 데이터에서 소멸한다.
재시뮬레이션을 안 했으면 "P0 해결"로 보고할 뻔했다.

## 2. P0-2 체결축 backfill — 실효 확인

```
backfill 전  filled_count>0 : 2026-05-08 이후 전량 0
backfill 후  246행 갱신

월별 체결 귀속 (후)
  2026-05  US 480 · KR 206
  2026-06  US 135 · KR  18
  2026-07  US   2 · KR   1
```

7월 3건이 정확히 복구됐다:
```
07-02 US IREN    entry 42.09  exit 41.19  net -2.6383  CLOSED_LOSS_CAP
07-03 KR 003490  entry 28850  exit 29150  net +0.8299  CLOSED_CLAUDE_SELL
07-06 US NVDA    entry 195.99 exit 192.25 net -2.4127  CLOSED_LOSS_CAP
```
sleeve 체결 7건(SCHG·275280·275300 등)은 후보행이 없는 게 정상이므로 미귀속.
**후보→체결 귀속이 2.5개월 만에 살아났다.** 이제 개선 효과를 측정할 수 있다.

## 3. P1-2·P1-3과 관측 항목 — 다음 세션 기록부터 반영

`spread_bps` evidence 주입, `vwap_distance_pct` runtime_gate 추가, `signal_flags` 키 교정은
전부 **쓰기 경로** 수정이라 과거 원장에는 반영되지 않는다. 축5 수치가 그대로인 건 정상이다.

내일 장 이후 아래로 확인한다:
```
spread_bps           post_open_features 생성 > 0 이 되는가
vwap_distance_pct    runtime_gate 전달 > 0 이 되는가
signal_flags.raw     NO_SIGNAL 행에서 null → false 로 바뀌는가
                     (signals_evaluated=true 가 함께 찍혀야 한다)
```

## 4. 도구 오진 수정 — `atr_pct`는 placeholder가 아니었다

SQLite는 존재하지 않는 식별자를 큰따옴표로 감싸면 **문자열 리터럴로 해석**한다.
`"atr_pct"`가 문자열이 되어 전 행 non-null·고유값 1로 보였고, 나는 그걸
"90,661행 전량 placeholder"로 보고했다. **실제로는 원장에 컬럼 자체가 없다.**
컬럼 존재를 먼저 확인하도록 고쳤고, 이제 `★원장에 컬럼 자체가 없음`으로 출력된다.

남은 진짜 미수집: `cohort_reliability` 0건(runtime_gate에는 존재 = 저장 누락),
`position_mfe_pct`/`position_mae_pct` 3건.

## 5. 오경보 하나를 실측으로 막았다

`REQUIRE_TRADE_READY=true`가 start-config에 있고, 메모리에 "글로벌 true는 지뢰
(2026-07-01 매수 셧다운)"로 기록돼 있어 최우선 경보로 올릴 뻔했다. 실측:

```
route_runtime_gate_reason='require_trade_ready'  US 3건 · KR 0건
US route PROBE_READY 생존 76건  ← 토글이 실제로 전환시키지 않고 있다
```

**지금은 구속하지 않는다.** 경보로 올리지 않는다.

evidence PROBE_READY + judge actionable 사슬도 KR 19 · US 27건으로 작다.

## 6. 그래서 내일 장의 실제 상태

오늘 작업으로 **측정과 관측은 복구됐고, 매수량을 늘리는 변경은 없다.**
그게 맞는 결과다 — 오늘 검증한 것 중 매수를 늘릴 근거가 선 것이 없었다.

7월 진입 퍼널의 실제 병목은 그대로다:
```
judge 호출      세션당 10~39건(대부분 캡 10에 바인딩)
judge 산출      WAIT_RECHECK 84% · BUY_READY 월 5건(전부 7/22 US)
evidence        필드 결측으로 partial/missing — 분봉이 없거나 판정이 이르다
후보 파이프라인  마지막 체결 2026-07-06 NVDA, 이후 16거래일 0건
```

이걸 푸는 건 데이터 배선이 아니라 **judge 처리량·호출 시점·후보 질**의 문제이고,
전부 매매 행동 변경이라 운영자 판단 영역이다. 오늘 밤 단독으로 바꾸지 않는다.

## 7. 내일 장 전 체크리스트

| # | 항목 | 판단 |
|---|---|---|
| 1 | 봇 재시작 여부 | **운영자 결정.** 현재 PID 47720(7/22 16:05 기동)은 오늘 코드 변경을 안 갖고 있다. 변경분은 관측 개선이 대부분이고 매매 행동 변경은 없다(P0-1은 무효과 확인). 재시작하면 관측이 내일부터 붙고, 안 하면 다음 재시작까지 미뤄진다 |
| 2 | 설정 정합성 | 확인 완료. `.env.live` vs start-config 실질 불일치 없음 |
| 3 | `REQUIRE_TRADE_READY` | 현재 구속 안 함(3건). 조치 불요 |
| 4 | 체결축 | 복구됨. 내일 체결 발생 시 후보행에 귀속되는지 확인 |
| 5 | 장 후 검증 | §3의 4개 항목을 `tools/pipeline_integrity_audit.py --since 2026-07-23`로 확인 |

## 8. 방법론

오늘 하루에 내 진단이 **네 번** 틀렸고 전부 실측으로 잡혔다.

1. DELL 사인을 `volume_ratio_open`으로 지목 → 실제는 코어 모멘텀 결측
2. 차단 사유를 `momentum_state=sustained`로 라벨 → 실제는 `data_quality`
3. `atr_pct`를 placeholder로 보고 → 실제는 컬럼 부재(SQLite 리터럴 함정)
4. `data_quality` 미전달을 강등 원인으로 지목 → 실제는 공변량, 효과 0

공통점: **추측한 인과를 실측 전에 보고했다.** 재시뮬레이션이 4번째를 잡았고,
그래서 운영자가 재시뮬레이션을 요구한 것이 옳았다.
