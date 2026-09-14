# PIT 가드 감사 — brain.json·교훈 피드백 (2026-09-14)

계기: TradingAgents 저장소 이슈 **#1251**(*"Decision-log lessons lack a point-in-time guard —
possible lookahead in backtests"*, 완료)·**#1300**(*fundamentals가 분석일이 아니라 wall-clock
`now()`로 찍힘*, 완료)·**#1275**(*FRED가 미발표·수정 데이터 반환*, 완료).
우리도 `state/brain.json` 정책 메모리와 교훈을 프롬프트에 먹인다. **같은 구멍이 있는지 본다.**

우리는 이미 같은 계열을 세 번 잡았다 — 판정 배선 결함 2건(08-22) · 3차 재발(08-25) ·
PEAD PIT 부재(09-03). 남의 저장소에서 본 게 네 번째 사례다.

---

## 0. 결론 먼저

**라이브 lookahead 없음. 판정 오염 없음.** 잠재 함정 2건을 기록으로 남긴다.

**그리고 오늘까지의 모든 판정 수치는 이 경로를 거치지 않았다.**
09-12 전면 감사, 패닉 레인, KR 8년, US swing 측정은 전부 `virtual_books.db` ·
가격 캐시 · `bench_bars`만 읽는다. **brain.json도 교훈도 안 읽는다.**

---

## 1. 경로 지도 — 누가 brain/교훈을 읽나

| 경로 | 파일 | 성격 |
|---|---|---|
| 라이브 프롬프트 | `minority_report/analysts.py:2318·2840·3581` | 시장판단·선정 프롬프트에 주입 |
| 표시 | `dashboard/dashboard_server.py:451` | 대시보드 표시용 |
| 샌드박스 | `runtime/rehearsal/context.py` | brain을 샌드박스로 **복사**(쓰기 격리) |
| 과거 재생 | `phase1_trainer/historical_sim.py` · `brain_backfill.py` | 과거 날짜 LLM 재생 |

`ticker_selection_db.py`는 소비자가 아니다 — 주석이 명시한다: *"brain.json에는 튜닝하지 않고
별도 DB에 raw 데이터 누적."*

---

## 2. 발견 ① `active_lessons.py:401 today = date.today()` — 잠재 함정

```python
today = date.today()                                    # :401
recency = max(0, 20 - _days_old(item.get("generated_at") or item.get("date"), today))   # :188
```

**`generated_at`은 recency 점수에만 쓰이고 컷오프로는 쓰이지 않는다.** 즉 "분석일 이후에
생성된 교훈을 배제하는 필터가 없다." #1300과 정확히 같은 모양이다.

**그런데 지금은 터지지 않는다** — 호출자가 대시보드(표시)와 라이브 분석가뿐이고,
**과거 날짜로 부르는 경로가 없다.** 라이브에서는 `today == 분석일`이라 맞는 값이다.

**판정: 라이브 결함 아님. 재생 경로가 하나라도 생기는 순간 즉시 구멍.**

**→ 09-14 수리 완료** (운영자 지시). `as_of: date | None = None`을 공개 API부터
`_select_items`까지 얇게 관통시키고 `None → date.today()`. **라이브 동작 불변.**
`_is_future_item`이 `generated_at`(brain은 `date`) > `as_of` 인 항목을 `future_lesson`으로
버린다. 날짜를 못 읽으면 **통과**시킨다 — 가드가 기능을 죽이는 쪽이 더 나쁘다.
테스트 16건. 기존 교훈 테스트 95건 회귀 없음.

**검증 플래그 누수는 이 코드엔 없다 — 최초 우려를 정정한다.** `:267~269`가
`truth_status != "fresh"`인 항목을 **이미 전부 버린다.** 즉 검증·수동검토를 거친 교훈은
애초에 주입 대상이 아니라, T+30 검증 플래그가 T+10 재생에 새는 경로가 없다.

**가드가 하나 더 있었다(감사 시 못 봤던 것).** `:448~` 승인 컨베이어(2026-06-12) —
*"운영자 승인 전 교훈은 프롬프트 주입 금지"*. 근거가 주석에 있다: 무승인 권고문 주입
14일간 지표 무변화 실증. 실제로 PIT 필터보다 **뒤에서** 거의 전부를 막는다.

---

## 3. 발견 ② `historical_sim.py:355` 읽기 방향 가드 없음 — 밀봉돼 무해

```python
brain_summary = BrainDB.generate_prompt_summary(market)    # :355 — 현재 brain
```
과거 날짜를 판단하는 프롬프트에 **오늘의 누적 정책 메모리**를 넣는다.

**쓰기 방향은 이미 막혀 있다**(`_brain_write_allowed`, 2026-06-26) — 재생 postmortem 교훈이
라이브 brain을 오염시키지 못하게 기본 차단, opt-in env로만 허용. 암기누수 논문 2편을
주석에 인용해뒀다. **읽기 방향에는 같은 가드가 없다.**

**그런데 출력이 밀봉돼 있다.** `sim_verify.py:125~137`과 대시보드가
`mode == "historical_sim"` 파일을 **재사용 거부**한다:

> `# 케이스 B: historical_sim 파일 존재 → 재사용 거부`

**판정: 밀봉된 도구. 판정 오염 아님. 사용을 재개하려면 read-side 가드를 먼저 넣어야 한다.**

부수 확인: `save_universe_snapshot`(`:339`)이 공유 스냅샷을 덮어쓰지만,
`build_universe_from_price_history`가 **target_date까지의 가격 이력으로만** 구성하므로
구성상 PIT 정합이다.

---

## 4. 안 걸린 것

- `runtime/rehearsal/context.py` — 과거 재생이 아니라 **샌드박스 격리**다. brain을 샌드박스
  루트로 복사해 리허설 쓰기가 라이브에 닿지 못하게 한다. 09-03 REHEARSAL 설계 그대로.
- `ticker_selection_db.py` — 소비자 아님(§1).

---

## 5. 우리가 남의 사례에서 실제로 건진 것

TradingAgents의 세 버그는 우리에게 **전략이 아니라 규약**을 준다.

1. **FRED는 빈티지를 박아야 한다.** `observation_start/end`만 주면 FRED는 *그때 발표된 값*이
   아니라 **나중에 수정된 값**을 준다. `realtime_start/end`를 함께 줘야 한다.
   우리 CLAUDE.md는 이미 *"출처·시점·지연·수정주가 명시"*를 요구하는데, **거시 데이터에는
   '빈티지'라는 한 겹이 더 있다.** 나중에 거시 축을 열면 이걸 안 박으면 조용히 미래를 본다.
2. **교훈 피드백은 타임스탬프 + 검증시각 두 개를 다 걸러야 한다**(§2).
3. **재생 도구는 출력을 밀봉해야 한다.** 우리는 이미 하고 있다(§3) — 이게 왜 필요한지의 실례.

---

## 6. 운영자 결정 대기

- §2 `as_of` 가드를 **지금 넣을지, 재생 경로를 만들 때 넣을지.**
  지금은 라이브 결함이 아니라 넣지 않았다(코드 변경은 지시가 있을 때만).
  다만 재생 경로를 만드는 사람이 이 문서를 못 보면 그대로 빠진다.
