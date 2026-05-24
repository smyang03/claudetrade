# Debate Metadata and Watchdog Safety 개발 요구서

작성일: 2026-05-25

대상 범위:

- `claude_memory/brain.py`
- `state/brain.json`
- `auto_start_if_missing.bat`
- `tools/live_guardian.py`
- `tools/live_preflight.py`
- `tests/test_analyst_unavailable_quorum.py`
- `tests/test_live_guardian.py`
- `tests/test_live_process_inventory.py`
- `tests/test_watchdog_launcher.py`

## 1. 목적

리뷰에서 지적된 두 가지 운영 리스크를 코드 수준에서 제거한다.

- debate history의 `changes`, `r2.*.stance`, `consensus_shifted` 불일치가 향후 prompt 통계를 오염시키지 않게 한다.
- Windows watchdog이 Python 이미지명(`python.exe`) 또는 템플릿 프로세스명만 보고 봇 실행 여부를 오판하지 않게 한다.

이 변경은 주문 수량, 주문 금액, hard risk block, PathB live gate, broker truth, `.env.live`, `config/v2_start_config.json` 운영 파라미터를 변경하지 않는다.

## 2. 현재 코드 진단

### 2.1 Debate metadata

`claude_memory/brain.py::save_debate_result()`는 현재 아래 조건으로 `changes`를 만든다.

```python
if r1s != r2s or r2[atype].get("changed"):
    changes.append(...)
```

이 구조에서는 R2가 `changed=true`를 반환했지만 최종 `stance`가 R1과 같은 경우에도 `changes`가 생길 수 있다. 또한 과거 저장 데이터가 수동 편집 또는 partial write로 깨지면 `changes`와 `r2`가 서로 다른 사실을 담을 수 있다.

문제가 된 `state/brain.json` US `2026-05-22` entry는 다음과 같은 충돌 상태다.

- `changes[0].analyst = neutral`
- `changes[0].r1_stance = NEUTRAL`
- `changes[0].r2_stance = MILD_BULL`
- 실제 `r2.neutral.stance = NEUTRAL`
- `consensus_shifted = false`

`get_debate_summary()`는 hit-rate 통계에는 `consensus_shifted`를 쓰고, 표시에는 `changes`를 사용한다. 따라서 같은 entry가 통계상으로는 keep, 표시상으로는 changed로 해석된다.

### 2.2 Watchdog launcher

기존 `auto_start_if_missing.bat` 템플릿은 아래 방식이었다.

```bat
tasklist /FI "IMAGENAME eq %PROCESS_NAME%" | find /I "%PROCESS_NAME%"
```

Python 봇의 일반 실행 명령은 `python trading_bot.py --live` 또는 `python trading_bot.py --paper`다. Windows `tasklist`의 이미지명은 `trading_bot.py`가 아니라 `python.exe`로 보인다.

따라서 아래 두 오판이 가능하다.

- `PROCESS_NAME=trading_bot.py`: 매 interval마다 미실행으로 오판해 중복 시작할 수 있다.
- `PROCESS_NAME=python.exe`: 무관한 Python 프로세스가 봇 재시작을 막을 수 있다.

현재 변경안은 배치 파일이 `tools/live_guardian.py --watch --start-bot`으로 위임하는 방향으로 개선되어 있다. 다만 guardian가 `PID 파일 없음 + 실제 trading_bot.py 프로세스 존재` 상황을 start guard로 막지 못하면 여전히 중복 시작 위험이 남는다.

## 3. Debate 개선 요구사항

### 3.1 저장 시 stance 변경만 `changes`로 기록

`save_debate_result()`는 `changed` 플래그만으로 stance change를 기록하면 안 된다.

필수 조건:

- `changes`는 `r1[analyst].stance != r2[analyst].stance`인 경우에만 생성한다.
- `r2.changed`는 reason 보강 또는 별도 diagnostic field로만 사용하고, `consensus_shifted` 계산 기준으로 사용하지 않는다.
- `consensus_shifted`는 저장 직전 `len(changes) > 0`와 항상 일치해야 한다.
- unavailable analyst는 기존 `unavailable_roles` 계약을 유지한다.

예상 구현:

```python
if r1s != r2s:
    changes.append({
        "analyst": atype,
        "r1_stance": r1s,
        "r2_stance": r2s,
        "reason": r2[atype].get("change_reason", ""),
    })
```

### 3.2 요약 시 저장된 metadata를 무조건 신뢰하지 않기

`get_debate_summary()`는 과거 `brain.json`에 깨진 entry가 있어도 prompt 통계를 오염시키지 않아야 한다.

필수 조건:

- 요약/통계용 change 여부는 `r1`과 `r2`의 실제 stance를 다시 비교해서 산출한다.
- 저장된 `changes`는 reason source로만 사용한다.
- `consensus_shifted`는 표시/통계의 최종 truth로 사용하지 않는다.
- `changes`가 있어도 실제 `r1/r2` stance가 같으면 keep으로 표시한다.
- 실제 `r1/r2` stance가 다르면 `consensus_shifted=false`로 저장되어 있어도 changed로 표시하고 통계도 changed bucket에 넣는다.
- `unavailable_roles`에 포함된 analyst는 기존처럼 changed 표시에서 제외한다.

권장 helper:

```python
def _actual_debate_changes(entry: dict) -> list[dict]:
    ...
```

### 3.3 기존 brain 상태 보정

문제가 된 US `2026-05-22` entry는 실제 `r2.neutral.stance`가 `NEUTRAL`이고 `consensus_shifted=false`다. 보수적 보정은 아래와 같다.

```json
"changes": [],
"consensus_shifted": false
```

상태 파일 자동 변경은 승인형 워크플로우가 안정화되기 전까지 제한한다. 따라서 이 보정은 명시적인 코드 리뷰/운영 승인 아래 수동 패치하거나, dry-run 출력이 있는 전용 repair tool로만 수행한다.

## 4. Watchdog 개선 요구사항

### 4.1 배치 파일은 process image name을 직접 검사하지 않기

`auto_start_if_missing.bat`는 `tasklist /FI "IMAGENAME eq ..."` 방식으로 봇 실행 여부를 판단하지 않는다.

허용되는 방식:

- `tools/live_guardian.py --watch --ensure-bot`에 위임한다.
- 기존 명령 호환을 위해 `--start-bot`을 유지하더라도 watchdog launcher는 `--ensure-bot`을 우선 사용한다.
- 또는 `Win32_Process.CommandLine` / `psutil.cmdline()`에서 `trading_bot.py`와 `--live` 또는 `--paper`를 동시에 확인한다.

필수 조건:

- `live`와 `paper` mode를 명확히 구분한다.
- 기본값은 paper로 두되 live 실행은 명시 인자 또는 환경변수로만 허용한다.
- Python 실행 파일 경로는 환경변수 override를 허용한다.
- repo root는 배치 파일 위치(`%~dp0`) 기준으로 결정한다.

### 4.2 Guardian start guard는 command line inventory를 start 차단 조건으로 사용

`tools/live_guardian.py --ensure-bot` 또는 `--start-bot`은 PID 파일만 보고 중복 실행 여부를 판단하면 안 된다.

필수 조건:

- preflight `runtime.process_inventory` 결과에서 현재 mode와 일치하는 `trading_bot.py` 프로세스가 1개 이상 발견되면 `start_bot`은 `SKIP` 또는 hard fail로 처리한다.
- PID 파일이 없더라도 command line inventory에 matching bot이 있으면 새 봇을 시작하지 않는다.
- PID 파일이 stale이고 command line inventory에도 matching bot이 없을 때만 stale PID 제거 후 start를 허용한다.
- process inventory를 사용할 수 없는 경우 live mode에서는 start를 보수적으로 막고 operator 확인을 요구한다.
- paper mode에서도 inventory unavailable이면 적어도 warning과 restart cooldown을 남긴다.

### 4.3 Duplicate detection은 mode-aware여야 한다

프로세스 분류 기준:

- live bot: command line에 `trading_bot.py`와 `--live`가 모두 존재
- paper bot: command line에 `trading_bot.py`가 있고 `--live`가 없거나 `--paper`가 존재
- guardian: command line에 `tools/live_guardian.py`와 `--watch`가 존재

주의:

- 무관한 `python.exe`는 봇으로 분류하지 않는다.
- 경로 구분자는 Windows/Unix 모두 처리한다.
- `python -m ...` 형태가 추가될 경우 별도 테스트를 추가한 뒤 허용한다.

## 5. 테스트 요구사항

### 5.1 Debate tests

추가/수정할 테스트:

- `changes`가 있지만 실제 `r1/r2` stance가 같으면 summary가 kept로 표시되는지 확인한다.
- `consensus_shifted=false`지만 실제 `r1/r2` stance가 다르면 changed bucket으로 통계화되는지 확인한다.
- `save_debate_result()`가 `r2.changed=true`만으로 `changes`를 만들지 않는지 확인한다.
- unavailable analyst는 summary changed 표시에서 제외되는지 기존 테스트를 유지한다.

권장 실행:

```bash
python -m pytest tests/test_analyst_unavailable_quorum.py -q
python -m py_compile claude_memory/brain.py
```

### 5.2 Watchdog tests

추가/수정할 테스트:

- `auto_start_if_missing.bat`에 `YourProgram.exe`, `C:\Path\To\YourProgram.exe`, raw `tasklist /FI "IMAGENAME` 패턴이 남아 있지 않은지 확인한다.
- launcher가 `tools\live_guardian.py`, `--watch`, `--start-bot`, `--mode`를 포함하는지 확인한다.
- process inventory에 matching live bot이 있고 PID 파일이 없어도 guardian start가 차단되는지 확인한다.
- process inventory unavailable + live mode + `--start-bot`일 때 start가 차단되는지 확인한다.
- stale PID + no matching process일 때만 cleanup/start가 허용되는지 확인한다.

권장 실행:

```bash
python -m pytest tests/test_watchdog_launcher.py tests/test_live_guardian.py tests/test_live_process_inventory.py -q
python -m py_compile tools/live_guardian.py tools/live_preflight.py
```

## 6. 완료 기준

완료로 보려면 아래 조건을 모두 만족해야 한다.

- `get_debate_summary()`의 통계와 표시가 동일한 actual stance comparison 기준을 사용한다.
- `save_debate_result()`가 `changed` 플래그만으로 stance change를 만들지 않는다.
- 깨진 US `2026-05-22` debate entry가 prompt summary에서 changed로 표시되지 않는다.
- watchdog launcher가 image name 기반 직접 감시를 하지 않는다.
- guardian start path가 PID 파일 부재 상황에서도 command line inventory로 중복 봇 시작을 막는다.
- 관련 pytest와 `py_compile`이 통과한다.

## 7. 비범위

이번 요구서에서 다루지 않는다.

- 주문 수량/금액 계산 변경
- hard stop, stop loss, trailing stop, affordability/risk 정책 변경
- PathB KR/US live gate 변경
- `.env.live`, `.env.paper`, `config/v2_start_config.json` 운영 파라미터 변경
- `state/brain.json` 자동 학습 승격 정책 변경
- 브로커 truth reconciliation 로직 변경
