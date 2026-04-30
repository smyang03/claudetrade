# 손상 복구 확정 및 재발 방지 TODO

작성일: 2026-05-01

## 목적

이번 문서는 다음 두 작업을 명확히 분리해 진행하기 위한 기준이다.

1. 손상 복구 패치를 커밋 가능한 단위로 확정한다.
2. 같은 인코딩/문법/상태 손상이 장중에 다시 들어오지 않도록 장 전/장 후 자동 health check를 둔다.

비용 최적화, DB maintenance, R2 조건부 실행, hold advisor cache, budget guard는 이 문서의 실행 범위에서 제외한다. 해당 작업은 복구 안정화 이후 별도 패치로 진행한다.

## 현재 적용 상태

- `trading_bot.py` 컴파일 손상은 복구되어 `py_compile`을 통과한다.
- `_STARTUP_GUARD_SEC`, `_ENTRY_SCAN_REGULAR_INTERVAL_MIN`, `_hist_fill_last_ts`, `_ticker_exclude_log`, `_since_open`, `_holding`, `protected`, `n_replace`, `_SKIP_ACTIONS`, `_type_map`, startup `_mkt` loop가 AST상 존재한다.
- `_STRATEGY_NAME_MAP`의 한글 alias가 정상 키로 복구됐다.
- `state/brain.json`의 prompt-consumed `execution_lessons` mojibake 문자열이 복구됐다.
- `tools/repo_health_check.py`가 추가됐다.
- `trading_bot.py`에 장 전/장 후 자동 health check schedule이 추가됐다.

## 1. 복구 패치 확정 상세 리스트

목표: 손상 복구만 별도 단위로 정리해 안전하게 커밋 가능한 상태를 만든다.

### 1.1 변경 범위 확인

- [ ] `git status --short`로 현재 변경 파일 목록을 확인한다.
- [ ] 이번 복구 커밋 후보를 다음 파일로 제한한다.
  - `trading_bot.py`
  - `state/brain.json`
  - `tools/repo_health_check.py`
  - `docs/plans/recovery_and_followup_todo_20260501.md`
- [ ] 다른 변경 파일은 같은 커밋에 섞지 않는다.
- [ ] `.env`, API key, 운영 로그, DB 파일은 커밋하지 않는다.

### 1.2 `trading_bot.py` 복구 확인

- [ ] 깨진 f-string이 남아 있지 않은지 확인한다.
- [ ] 주석에 삼켜졌던 대입문이 AST상 실제 대입문으로 존재하는지 확인한다.
- [ ] `_SKIP_ACTIONS = { ... }` dict 헤더가 정상 코드인지 확인한다.
- [ ] `_type_map = { ... }` dict 헤더가 정상 코드인지 확인한다.
- [ ] 한글 전략 alias가 다음처럼 정상 매핑되는지 확인한다.
  - `모멘텀` -> `momentum`
  - `평균회귀` -> `mean_reversion`
  - `갭풀백`, `갭 풀백`, `갭눌림` -> `gap_pullback`
  - `연속진입` -> `continuation`
  - `관망` -> empty strategy

### 1.3 `state/brain.json` 복구 확인

- [ ] JSON 파싱이 되는지 확인한다.
- [ ] `execution_lessons`의 깨진 lesson label이 정상 한글로 복구됐는지 확인한다.
  - `청산 실패 패턴`
  - `수익 청산 유효 패턴`
- [ ] 봇 실행 중 자동 갱신된 통계 변경과 lesson 복구 변경을 구분한다.
- [ ] 커밋 시 `state/brain.json` 전체 변경을 포함할지, lesson 복구 부분만 선택할지 결정한다.

### 1.4 복구 커밋 전 필수 검증

```powershell
python -m py_compile trading_bot.py
python -c "import json; json.load(open('state/brain.json', encoding='utf-8')); print('brain.json OK')"
git diff --check -- trading_bot.py state/brain.json
python tools/repo_health_check.py --trigger manual
```

### 1.5 커밋 기준

- [ ] 복구와 재발 방지 파일만 stage한다.
- [ ] 자동 생성 audit/report 파일은 의도한 경우에만 별도 커밋한다.
- [ ] 커밋 메시지는 복구 목적이 드러나게 작성한다.

예시:

```text
fix: recover trading bot encoding damage and add health checks
```

## 2. 재발 방지 상세 리스트

목표: 수동 점검이 가능한 health check를 만들고, 장 시작 전/장 종료 후 자동으로 돌려 손상 상태를 조기에 감지한다.

### 2.1 수동 health check 도구

추가 파일:

- `tools/repo_health_check.py`

수동 실행:

```powershell
python tools/repo_health_check.py --trigger manual
python tools/repo_health_check.py --trigger KR_PREOPEN --json
```

검사 항목:

- [x] `trading_bot.py` `py_compile`
- [x] `state/brain.json` UTF-8 JSON 파싱
- [x] `brain.json` 내부 `execution_lessons` mojibake marker 검사
- [x] `trading_bot.py` AST 구조 검사
- [x] `_STRATEGY_NAME_MAP` 한글 alias 검사
- [x] 복구 관련 파일 `git diff --check`

의도적으로 하지 않는 것:

- 자동 수정
- DB write
- API 호출
- 매매 로직 실행

### 2.2 AST 구조 검사 기준

다음 항목이 코드에 실제 문법 노드로 존재해야 한다.

- top-level assignment:
  - `_STARTUP_GUARD_SEC`
  - `_ENTRY_SCAN_REGULAR_INTERVAL_MIN`
- `self` attribute assignment:
  - `_hist_fill_last_ts`
  - `_ticker_exclude_log`
- local assignment:
  - `_since_open`
  - `_holding`
  - `protected`
  - `n_replace`
  - `_SKIP_ACTIONS`
  - `_type_map`
- startup/housekeeping loop:
  - `_mkt` 대상 `for` loop 2개 이상

이 검사는 "컴파일은 되지만 주석에 코드가 삼켜져 런타임에서 NameError가 나는 상태"를 잡기 위한 것이다.

### 2.3 Mojibake 검사 기준

전체 파일을 무차별 검색하지 않는다. 기존 주석/로그에 과거 mojibake가 많아 false positive가 커지기 때문이다.

대신 다음 prompt/판단 영향 영역만 검사한다.

- `state/brain.json`의 모든 `execution_lessons`
- `trading_bot.py`의 `_STRATEGY_NAME_MAP`

탐지 marker:

- replacement char: `�`
- lesson label 손상 계열: `泥`, `?섏씡`, `?ㅽ뙣`, `?좏슚`, `?⑦꽩`
- strategy alias 손상 계열: `紐⑤찘`, `?됯퇏`, `媛??`, `愿留`, `?곗냽`

### 2.4 자동 실행 스케줄

자동 health check는 봇 내부 scheduler에 연결한다.

KR:

- `08:40` `KR_PREOPEN`
- `16:15` `KR_POSTCLOSE`

US:

- `22:10` `US_PREOPEN`
- `05:10` `US_POSTCLOSE`

실패 처리:

- 로그에 `[repo health] <trigger> FAIL` 기록
- `TG_SYSTEM_ALERTS_ENABLED=true`일 때 Telegram system warning 전송
- 운영 flow는 차단하지 않음
- session open, entry scan, housekeeping schedule은 계속 진행
- 자동 복구는 하지 않음

성공 처리:

- 로그에 `[repo health] <trigger> OK` 기록
- Telegram 성공 알림은 보내지 않음

### 2.5 운영 정책

- 장 전 실패는 세션 시작 전 수동 확인 대상으로 보되, 봇 운영은 계속한다.
- 장 후 실패는 다음 재시작 전 수동 확인 대상으로 보되, 다음 schedule 등록을 막지 않는다.
- false positive가 나오면 `tools/repo_health_check.py`의 검사 범위를 좁히거나 marker를 조정한다.
- 안정화 전까지는 pre-commit/CI 강제 차단보다 장 전/장 후 감지를 우선한다.
- 현재 정책은 "detect and alert only"다. 자동 수정이나 자동 중단은 나중에 별도 설계 후 추가한다.

## 3. 단계별 검증 결과

실행 기준 시각: 2026-05-01

```powershell
python tools/repo_health_check.py --trigger manual
```

결과:

- PASS `trading_bot.py_compile`
- PASS `brain.json_parse`
- PASS `brain.execution_lessons_mojibake`
- PASS `trading_bot.structure`
- PASS `strategy_aliases`
- PASS `git.diff_check`

```powershell
python tools/repo_health_check.py --trigger KR_PREOPEN --json
```

결과:

- `ok=true`
- 6개 check 모두 PASS

```powershell
python -m py_compile trading_bot.py tools/repo_health_check.py
```

결과:

- PASS

```powershell
python -c "import json; json.load(open('state/brain.json', encoding='utf-8')); print('brain.json OK')"
```

결과:

- PASS

```powershell
git diff --check -- trading_bot.py state/brain.json tools/repo_health_check.py docs/plans/recovery_and_followup_todo_20260501.md
```

결과:

- PASS

## 4. QA 체크리스트

### 4.1 정상 동작 시뮬레이션

- [x] manual trigger 실행
- [x] `KR_PREOPEN` JSON trigger 실행
- [x] `KR_POSTCLOSE` JSON trigger 실행
- [x] `US_PREOPEN` JSON trigger 실행
- [x] `US_POSTCLOSE` JSON trigger 실행
- [x] 자동 스케줄 등록 코드가 `py_compile`을 통과하는지 확인
- [x] health check 실패 시 Telegram 알림 경로가 코드상 존재하는지 확인

### 4.2 MD 대비 누락 확인

- [x] 복구 패치 확정 상세 리스트 존재
- [x] `trading_bot.py` 복구 확인 항목 존재
- [x] `state/brain.json` 복구 확인 항목 존재
- [x] 수동 health check 명령 존재
- [x] 장 전/장 후 자동 스케줄 존재
- [x] 자동 수정 금지 원칙 존재
- [x] 실패 처리 정책 존재
- [x] QA 검증 결과 기록 영역 존재

### 4.3 최종 확인

- [x] 4개 자동 trigger 전체를 JSON 모드로 실행해 모두 `ok=true`인지 확인한다.
- [x] 최종 `python tools/repo_health_check.py --trigger manual`을 다시 실행한다.
- [x] 최종 `git status --short`로 이번 작업 파일과 unrelated 변경을 구분한다.

자동 trigger 시뮬레이션 결과:

- `KR_PREOPEN`: `ok=true`, 6 checks PASS
- `KR_POSTCLOSE`: `ok=true`, 6 checks PASS
- `US_PREOPEN`: `ok=true`, 6 checks PASS
- `US_POSTCLOSE`: `ok=true`, 6 checks PASS

최종 manual check:

- `ok=true`, 6 checks PASS

작업트리 주의:

- 이번 작업 파일은 `trading_bot.py`, `state/brain.json`, `tools/repo_health_check.py`, `docs/plans/recovery_and_followup_todo_20260501.md`다.
- 최종 `git status --short`에는 unrelated 기존 변경 파일이 다수 남아 있다.
- 복구/재발 방지 커밋을 만들 때는 위 4개 파일만 선별한다.

## 5. 추후 작업

이 문서의 범위 밖이며 나중에 별도 패치로 진행한다.

- 하루 이상 운영 관찰
- Claude API 비용 최적화 1차
- Claude API 비용 최적화 2차
- DB maintenance 및 인덱스 정리
