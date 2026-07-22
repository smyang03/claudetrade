# 개선 실행·검증 결과 (2026-07-23)

`final_improvement_scope_20260723.md`의 범위를 실행하고 검증했다.
**진행 중 내 이전 결론 두 개가 뒤집혔다.** 그것부터 적는다.

---

## 0. ★ 정정 두 건

### 0-1. "후보 파이프라인 마지막 체결 7/06, 이후 16거래일 0건" — 틀렸다

새로 붙인 라이브 귀속 동기화를 검증하다가 **ONDS 2026-07-22 체결**이 잡혔다.

```
2026-07-22T16:43:26Z  CLAUDE_TRADE_READY
2026-07-22T16:47:53Z  SAFETY_PASSED   strategy_used=claude_price_a  qty=40
2026-07-22T16:47:55Z  ORDER_SENT      entry_route=plan_a
2026-07-22T17:15:25Z  FILLED          fill_price 8.37  qty=40
```

`claude_price_a`는 **BUY_READY 즉시매수 경로의 태그**다. 즉 새로 배선한 즉시매수가
실제로 발화해 체결됐다. 현재 라이브 보유 중이다:

```
ONDS  qty=40  entry $8.37  현재 $8.3353  strategy=claude_price_a
```

왜 못 봤나: `v2_canonical_performance`는 **세션 마감에 동기화**되므로 진행 중인 세션의
체결이 없다. 나는 canonical만 보고 "0건"이라고 단정했다. lifecycle을 봤어야 했다.

부수: ONDS의 `source_strategy`가 비어 있다(`claude_price_a/`). 메모리에 기록된
MICRO_PROBE 전략 미귀속과 같은 유형이 즉시매수 경로에도 있다.

### 0-2. "NVDA 오귀속" — 오귀속이 아니었다

타 AI가 "NVDA는 WATCH row에 fill이 붙었다 = 잘못"이라 했고 나도 동의해 미귀속 처리했다.
**둘 다 틀렸다.** lifecycle 앵커로 확인하면:

```
NVDA  CLAUDE_PRICE_PLAN_CREATED 23:07:04 KST → 후보행 23:07:03 (WATCH/WATCH)
```

PathB는 selection이 **WATCH인 스냅샷에서도 가격플랜을 만든다.** 그 WATCH 행이 실제
원 결정 행이다. route 계열로 거르면 이 사실이 지워지고 미귀속이 된다.
계열 필터를 폐기했다. 귀속 행 중 WATCH 기원이 33건이다 — 드문 일이 아니다.

---

## 1. 체결 귀속 — 규칙을 세 번 고쳐 정착

| 판 | 규칙 | 결과 |
|---|---|---|
| 1차 | 세션 내 최초 executable 행 | 246건 중 **56건(22.8%) 오귀속** |
| 2차 | canonical route 계열 대조 + 체결 직전 행 | 위반 0이나 NVDA 등 **54건 과소 귀속** |
| 3차 | **플랜 생성 앵커 + 시각 근접** | 귀속 280/298 · 위반 0 · 자기모순 0 |

3차의 앵커 우선순위:
```
PATHB_SELECTION_RECONCILE.selection_snapshot_ts (플랜과 동시각)
→ CLAUDE_PRICE_PLAN_CREATED.occurred_at
→ ORDER_SENT.occurred_at
```

실증 정확도:
```
IREN    플랜 22:54:34 → 후보행 22:54:33  (1초 차)  PathB.wait
003490  플랜 10:27:34 → 후보행 10:27:19            PathB.wait
NVDA    플랜 23:07:04 → 후보행 23:07:03            WATCH
```

발견한 함정 둘:
- `known_at`은 KST(+09:00), lifecycle은 UTC(+00:00). 문자열 비교하면
  `22:22+09:00 > 14:21+00:00`이 되어 **항상 어긋난다**(수정 후 시각 매칭 1건 → 222건)
- FILLED payload의 `selection_snapshot_ts`는 *체결 시점* 스냅샷이라 원 결정 행이 아니다
  (IREN: 플랜 22:54 vs 체결시 스냅샷 23:10)

---

## 2. 완료 항목

### P0-1·P0-2. FILLED → 후보 원장 라이브 반영 (`ce8ef6c`~`669f11a` + 신규)

`_candidate_audit_update_from_decision_event()`는 `buy_order`/`sell_filled`만 처리하고
**FILLED 분기가 없었다.** 그래서 `filled_count`가 라이브로 채워지지 않았다.

구현:
- `audit/fill_attribution.py` 신설 — 라이브·사후가 **같은 규칙**을 쓰게 공용화
- `CandidateAuditStore.update_execution_by_candidate_key()` 추가.
  ticker 기준 갱신(`latest_only`)은 최신 WATCH 행에 체결을 붙이는 **라이브 경로의
  동일 결함**이라 체결에는 쓰지 않는다
- `TradingBot._maybe_sync_candidate_fill_attribution()` — housekeeping 주기(5분)에서
  lifecycle을 truth로 동기화. **주문 핫패스를 건드리지 않는다**(발행 지점이 여럿이라
  각각 고치면 누락이 남고, 라이브 위험도 크다)
- `candidate_fill_attribution_sync` funnel 이벤트로 linked/unresolved 관측

원장 사본 구동 검증:
```
07-02 IREN   → PULLBACK_WAIT/PathB.wait  entry 42.09
07-03 003490 → PULLBACK_WAIT/PathB.wait  entry 28850
07-06 NVDA   → WATCH/WATCH               entry 195.9989
07-22 ONDS   → BUY_READY/PlanA.buy       entry 8.37     ← canonical엔 아직 없음
07-16 SCHG · 07-20 275280 → 미귀속(sleeve, 정상)
자기모순(NO_SUBMIT+FILLED) 0건
```

### P1-1. FILLED 중복 dedupe

`collect_session_fills()`가 `decision_id` 기준으로 접는다. 같은 체결에 FILLED가
2건씩 발행되므로(IREN·003490·NVDA 실측) 이벤트 수로 세면 2배가 된다.

### 이전 커밋분

`data_quality` 강등 면제(무효 확인) · `spread_bps` evidence 주입 ·
`vwap_distance_pct` runtime_gate 추가 · `_plan_a_signal_flags` 키 계약 교정 ·
도구 컬럼 존재 선확인 · 반사실 A/B 계약 문서화.

---

## 3. 미완 — 남은 범위

| 항목 | 상태 |
|---|---|
| P0-3 `mfe_time`/`mae_time` 수집 복구 | **미착수.** 6월 closed 152건 전량 0 |
| P0-4 sleeve `route`/net 귀속 | **미착수.** 체결 4건 net 0, route=unknown |
| P1-2 `cohort_reliability` 이름 계약 | 미착수(0/91,556) |
| P1-3 PathB raw plan 보존 | 미착수 |
| P1-4 reason attribution 3분할 | 미착수 |
| P1-5 recheck 큐 정렬 shadow | 미착수(n=17로 판정 불가, shadow만) |
| P1-6 `_update_position_excursion` 호출점 | 미착수(P0-4와 같은 뿌리) |

**canonical이 ONDS를 누락한 건**은 신규 과제다 — 세션 마감 동기화라 진행 중 체결이
비는 게 설계상 정상인지, 누락인지 확인이 필요하다.

---

## 4. 내일 장 관련

- 새 코드는 **봇 재시작 후** 적용된다. 현재 PID 47720은 미반영.
- 재시작 타이밍: **US 마감(~05:00 KST) 후 · KR 개장(09:00) 전**.
  단 지금 ONDS·SCHG 등 4개 포지션 보유 중이므로 재시작 전 포지션 상태 확인 권장.
- 매매 행동을 바꾸는 변경은 없다. 체결 귀속은 **기록**이고, evidence 수정은
  실데이터 A/B에서 무효 확인됐다.
- 장 후 확인: `candidate_fill_attribution_sync` funnel 이벤트가 찍히는지,
  `linked`가 체결 수와 맞는지.

---

## 5. 방법론 — 오늘 여덟 번 틀렸다

```
1 DELL 사인 → 코어 모멘텀이었다
2 momentum_state 라벨 → pack에 그 키가 없었다
3 atr_pct placeholder → 컬럼 부재(SQLite 문자열 리터럴)
4 data_quality 강등 원인 → 공변량, 실데이터 효과 0
5 recheck "383건 증발" → consume 스키마 오독
6 체결 backfill 귀속 → route·시각·tz 미대조로 22.8% 오귀속  (타 AI가 지적)
7 NVDA 오귀속 판정 → 오귀속이 아니었다. WATCH 스냅샷이 진짜 기원
8 "7/06 이후 체결 0건" → canonical만 보고 단정. ONDS가 있었다
```

6은 타 AI가, 7·8은 내가 구현을 검증하다 잡았다.
공통 구조는 변하지 않았다 — **원장의 갱신 시점·스키마·타임존을 확인하기 전에
인과를 단정한다.**

누적 가드:
- 컬럼 존재 선확인
- 반사실은 실데이터 A/B로 delta를 낸 뒤 보고
- 시각 비교 전 timezone 확인
- 귀속은 맞는 대상이 없으면 비워 둔다
- **집계 원장의 갱신 시점을 먼저 확인한다** (오늘 신규 — canonical은 세션 마감 동기화라
  진행 중 세션이 비어 있다. 그걸 "0건"으로 읽으면 안 된다)
