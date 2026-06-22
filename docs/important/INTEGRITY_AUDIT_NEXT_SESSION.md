# 무결성 감사 — 다음 세션 작업 준비 (2026-06-23 작성)

> 1차 감사(자동도구+P0 토론판정+배치 A/C/brain도구)는 끝. 이 문서는 **남은 것을 다음 세션이 재조사 없이 바로 착수**하도록 코드 앵커·접근·보호영역 주의·검증기준을 박아둔 핸드오프. 1차 판정은 `INTEGRITY_AUDIT_PLAN.md §6`, 메모리 `project_integrity_audit_20260623`.

## 0. 현재 상태 (이번 세션 커밋 3개)

- `3933f12` 배치 A: `tools/integrity_audit.py`(상시감시) + postmortem fresh-brain 가드 + fact 신선도 가드 + preflight 비차단 편입.
- `d056fad` C: market_regime 0/757 → sync-layer fallback(`_entry_regime_from_events`). 신규 100%, 소급 62%.
- `970204b` brain 오염 정리 turnkey 도구(dry-run 검증, 미적용).

라이브 무변경(config/env/brain.json/주문/리스크/broker truth 전부). QA 2594 passed(65 fail=Py3.9 비호환 `test_preopen_continuation_shadow.py`, 무관 — 다음 세션도 이 65는 무시).

---

## 1. 봇 정지 정비창 RUNBOOK (turnkey, ~5분) — 코드 아니라 운영 실행

**봇이 brain.json/decisions.db를 라이브로 쓰므로 반드시 봇 정지 후.**

```bash
# ① brain KR US티커 오염 3건 제거 (백업 자동 state/backups/)
python tools/clean_brain_pollution.py            # dry-run 먼저 확인
python tools/clean_brain_pollution.py --apply

# ② regime 전체 백필 (close_payload 빈 행을 이벤트 consensus_mode로 복원)
python tools/sync_v2_learning_performance.py --runtime-mode live    # start-date 미지정=전체

# ③ 검증
python tools/integrity_audit.py                  # market_regime/brain 오염 WARN 줄었나
```
검증 기준: `v2_learning.market_regime` 충진율 0%→~60%+, KR issue_patterns US티커 0건.
주의: ②는 봇 자동 sync(10일 윈도우)와 달리 전체 재처리 — 봇 가동 중 실행 금지(decisions.db 경합).

---

## 2. 다음 세션 코드 작업 (우선순위순)

### 2-1. [P0 측정] mfe/mae 영속화 — regime의 자매 버그

- **현상:** `v2_learning.mfe_pct`/`mae_pct` 최근 14일 3~4% 충진. 최근 CLOSED 12/54건만 `position_mfe_pct` 방출.
- **근본원인(regime과 동일):** `observed_mfe_pct`/`observed_peak_price`가 **휘발성 pos에만** 저장(`runtime/pathb_runtime.py:11638-11642` `_update_position_excursion`), exit_meta가 거기서 읽음(`:11662-11663`). 멀티데이 보유·재시작 rehydrate 시 pos의 observed_* 유실 → CLOSED payload에 mfe 안 들어감.
- **regime과 다른 점:** MFE는 **진입 시점에 모름**(보유 중 peak) → regime처럼 PLAN_CREATED 이벤트 fallback 불가. yfinance 백필(`mfe_backfill_yf`)은 **2026-06-14 MD에서 의도적 오염격리**(5분봉≠실시간) → canonical mfe_pct에 join 금지.
- **접근(택1, 보호영역이라 MD 위반 절차 필요):**
  1. `observed_peak_price`/`observed_low_price`를 durable store(path_run plan_json 또는 PATHB_ZONE_UPDATED류 이벤트)에 주기적 영속화 → rehydrate 시 pos에 복원 → exit_meta가 정상 방출. (regime 근본fix와 동형, 가장 정합)
  2. 청산 직전 exit-scan이 current로 observed_* 재계산 보장(이미 `_update_position_excursion` 호출되나 rehydrate 직후 1틱은 peak 유실) — 부분 완화만.
- **보호영역:** `pathb_runtime` exit/excursion. `peak_pnl_pct`(ladder 입력) **절대 비접촉** — observed_* 전용키만(2026-06-14 계약).
- **테스트:** rehydrate된 pos(observed_* 없음)가 청산 시 mfe 0이 아니라 복원값 방출. `tests/test_pathb_position_excursion.py` 확장.
- **검증:** 재시작→보유→청산 시뮬에서 mfe_pct 채워지나. 봇-다운 full sync 후 mfe 충진율.
- **참고:** 이미 방출된 12/54는 봇 full sync(RUNBOOK ②)로 learning에 반영됨 — 코드fix는 "앞으로 안 유실"용.

### 2-2. [P1 attribution] PathB 진입 → ticker_selection_log 연결

- **현상:** ticker_selection_log.execution_decision_id traded행 26/78(33%). PathB 실체결(SYRE/INTC 등)이 traded=0·미연결 = **PathB가 selection_log를 아예 안 찍음**(Path A/B 분리).
- **코드 앵커:**
  - writer: `ticker_selection_db.py:379-506` `update_signal_execution`류(execution_decision_id 인자 받음, COALESCE upsert).
  - Path A 호출처: `trading_bot.py:35671` `_v2_ensure_execution_decision_id` → `:35740/35750` writer 호출. PathB 진입 경로엔 이 연결 없음.
- **접근:** PathB 진입 체결 시 같은 writer로 execution_decision_id(=v2 decision_id) + traded 마킹. **attribution 기록만 — 주문/실행 로직 무변경.**
- **보호영역/주의:** route-merge(`runtime/action_routing.py RouteDecision`). **selection↔execution 한 패치 금지(CLAUDE.md)** → 이건 순수 attribution 기록이라 execution 로직 무변경임을 커밋에 명시. PathB sizing/gate 비접촉.
- **테스트:** PathB 체결 1건 → ticker_selection_log에 traded=1·execution_decision_id 채워짐. `tests/test_audit_ticker_selection_attribution.py` 확장.
- **검증:** 정비 후 traded행 연결율, integrity_audit selection.execution_decision_id WARN.

### 2-3. [P1 brain주입] count>=2 게이트 + correction_guide 신선도 메타

- **현상:** issue_patterns count<=1 = KR 39/40·US 44/44(과적합). correction_guide 5월 화석화(신선도 게이트 부재).
- **중요:** 이건 brain.json **삭제가 아니라 주입측(`claude_memory/brain.py::generate_prompt_summary`) 게이트**. 현재 `V2_FRESH_BRAIN_START=true`라 selection/judgment/postmortem 주입은 이미 차단 → **현 라이브 영향 낮음**(우선순위 中). fresh-brain off 전환 대비 + telegram/digest 등 잔여 경로용.
- **접근:** generate_prompt_summary에서 issue_patterns `count>=2`만 포함(또는 count=1은 "관측" 라벨). correction_guide는 generated_date age > N일이면 주입 제외 or "stale" 표기. brain.json 구조는 무변경(주입 필터만).
- **테스트:** count=1 패턴이 summary에서 빠지나, stale correction_guide age 게이트. `tools/check_brain_quality.py` 확장(현재 신선도·count 미검사).
- **검증:** integrity_audit brain_pollution/brain_staleness.

### 2-4. [P2 위생] DB 비대 / state 정리 — 파괴적, 규칙 선합의

- **현상:** candidate_audit 2.3GB, event_store 933MB, agent_call 727MB(합 ~4GB). state/*.json 182/533이 30일+.
- **선행 조사(코드 아님):** 비대 driver 규명 — agent_call_events/lifecycle_events 행 증가율, payload 크기. retention 가능 여부(forward 측정 끝난 구 이벤트 아카이브 vs 참조됨).
- **접근:** ① 구 이벤트 아카이브 테이블/파일 분리 + VACUUM ② state 일별판단/PID 구파일 retention 규칙(예: 30일). **둘 다 파괴적 → 운영자 OK + 백업 선행. 막 삭제 금지.**
- **검증:** DB 크기, 봇 재시작/조회 성능 무영향, 참조 무결성.

### 2-5. [P1/P2] 남은 AI판정 회전

1차 토론은 P0 3건만. integrity_audit가 needs_ai로 표시한 나머지(pnl_pct_net/fx_change_pct NULL 의미, db_sync 참고치, fact 26일 등)를 같은 "버그 vs 정상" 토론으로 §6에 채울 것. fx_change_pct는 KIS 미노출 기지(설계상 낮음) — 확정만.

---

## 3. 규율 (1차에서 배운 것)

- 1차 숫자는 단정 아님 — 직접 재쿼리. (예: execution_id "99.8% NULL"은 watch_only라 정상이었음.)
- 회의 모델 토론으로 빌더 근본원인 검증(regime 근본원인 1차 진단이 반박당함).
- 보호영역(pathb_runtime/route-merge/broker truth) 변경은 `MD 위반 사항` 절차 + focused 테스트 + 전체 QA.
- 라이브 머신: 봇/대시보드 launch 금지, brain.json/decisions.db 동시쓰기 금지(read-only 검증만).
- 측정 복구는 수익 레버 아님 — 분석을 신뢰가능하게 만드는 토대공사임을 계속 분리 인지.
