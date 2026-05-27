# Repository Guidelines

This file is the shared operating guide for agentic coding tools working in this repository. `CLAUDE.md` contains the longer Claude-specific operations contract; keep the core safety rules in both files aligned.

## Project Structure & Module Organization

이 저장소는 Python 기반 KR/US 자동매매 시스템입니다. `trading_bot.py`가 메인 실행 루프이며, `kis_api.py`, `risk_manager.py`, `ticker_selection_db.py`가 브로커 연동, 리스크 관리, 종목 선택을 지원합니다. 핵심 도메인 코드는 `runtime/`, `execution/`, `strategy/`, `bot/`, `minority_report/`, `audit/`, `lifecycle/`, `ml/`, `preopen/`, `learning/`에 나뉘어 있습니다. 운영 도구는 `tools/`, Flask 대시보드는 `dashboard/`, 문서와 보고서는 `docs/`에 둡니다. 테스트는 주로 `tests/`에 있으며, 일부 레거시 테스트는 루트의 `test_*.py`와 `test/audit_lab/`에 있습니다. `data/`, `state/`, `logs/`는 런타임 산출물 위치이므로 생성 DB, PID, 캐시, 로컬 보고서는 커밋하지 마세요.

## AI Operating Contract

- AI는 시장 모드 판단, 종목 watchlist/trade_ready 후보 제안, conviction/strategy-fit 판단, 보유 종목 HOLD/SELL 의견 제시를 맡을 수 있습니다.
- AI가 최종 주문 수량/금액 계산, 하드 손절 해제, 브로커 truth 무시, 이벤트 데이터의 장기 메모리 자동 승격을 직접 수행하면 안 됩니다.
- 상태 오염이 의심되면 내부 캐시보다 브로커 보유 종목, 미체결 주문, 가능하면 체결 내역을 1차 truth로 봅니다.
- 브로커 불신 또는 quarantine 상태에서는 신규 진입보다 기존 포지션 보호와 복구를 우선합니다.
- selection 품질 문제와 execution/risk 문제를 섞지 말고, 원인과 수정 범위를 분리하세요.
- 짧은 기간 성과만으로 전략 철학을 교체하지 마세요. 축소, shadow 관찰, 검증, 승인 순서로 진행합니다.
- `state/brain.json` 자동 변경은 승인형 워크플로우가 안정화되기 전까지 보류합니다. 교훈 후보는 `state/lesson_candidates.json`에 append/score하는 흐름을 우선합니다.

## Trading Safety & Runtime Rules

- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard는 Claude 사용량 폭증 방지용 안전장치입니다. `runtime/pathb_runtime.py`의 `_pathb_auto_sell_review_cooldown_payload()` / `_run_pathb_sell_review_gate()` 흐름과 `tests/test_auto_sell_claude_gate.py::test_pathb_loss_cap_hold_respects_reask_cooldown`을 삭제하거나 완화하지 마세요. 변경이 필요하면 작업 설명, 커밋 메시지, PR 본문 중 하나에 반드시 이유, 예상 Claude 호출/토큰 영향, 대체 반복호출 방지책, 실행한 테스트를 명시하세요.
- `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true` 상태에서는 PathB `loss_cap`/`hard_stop`/`profit_ladder` review가 다시 열릴 수 있으므로 위 cooldown guard가 필수입니다. 이 플래그 또는 `AUTO_SELL_REVIEW_HOLD_COOLDOWN_MINUTES`, `PATHB_AUTO_SELL_REVIEW_HOLD_REASK_DROP_PCT`를 건드릴 때도 동일하게 사유와 검증 계획을 남기세요.
- Path A는 `trading_bot.py`의 `TradingBot` 흐름이며 Claude selection, 전략 신호, 주문으로 이어집니다.
- Path B는 `runtime/pathb_runtime.py`의 `PathBRuntime` 흐름이며 Claude 가격 플랜 기반 진입/청산을 담당합니다. 현재 KR/US 모두 live 활성 상태입니다 (`PATHB_KR_LIVE_ENABLED=true`, `PATHB_US_LIVE_ENABLED=true`).
- 두 경로는 `runtime/action_routing.py`의 `RouteDecision`으로 합류합니다.
- 진입 결정은 후보 풀 생성, Claude selection raw, normalized/applied trade_ready, 전략 신호, affordability/risk, 주문 생성 순서로 추적하세요.
- 매수 차단 조건에는 브로커 상태 불신, affordability fail, hard risk block, same-day reentry block, late-session/blackout, watch_only 유지가 포함됩니다.
- `_sync_runtime_with_broker()` 변경 시 보유 종목과 미체결 주문 기준 stale 포지션 정리, 시장별 quarantine, HALT/daily_return의 시장별 baseline 계산을 반드시 확인하세요.

## Data, Memory, and Logging Contracts

- `state/brain.json`: 정책 메모리입니다. 런타임 truth 또는 원장으로 취급하지 마세요.
- `state/lesson_candidates.json`: 자동 점수화된 교훈 후보입니다.
- `data/audit/candidate_audit.db`: 후보 감사 DB이며 `source_file`/payload merge 계약을 보존해야 합니다.
- `data/ml/decisions.db`: 의사결정/성과 데이터입니다.
- `data/ticker_selection_log.db`: selection 근거 기록입니다.
- 로그는 디버깅의 1차 수단입니다. 사람이 읽는 문구는 한국어로 유지하고, 에러/위험/정상 로그를 분리하세요.
- 로그 분석은 `logs/system/`, `logs/risk/`, `logs/normal/`, `logs/daily_judgment/`, `logs/screener/` 순서로 보는 것이 기본입니다.

## PEAD and Experiment Boundaries

- PEAD는 독립 전략이 아니라 입력 품질 기능입니다. entry timing, stop-loss, trailing stop, session-close logic을 override하지 못합니다.
- PEAD event data는 `brain.json`에 넣지 말고 digest/candidate metadata와 shadow logs에만 둡니다.
- `surprise_sign`/`surprise_strength`는 5거래일 shadow와 수동 검토 gate 통과 전까지 prompt-visible로 올리지 마세요.
- KR은 구조화된 actual/estimate가 없으면 `surprise_sign=unknown`을 유지하고, 뉴스/DART 제목만으로 EPS beat/miss를 추론하지 않습니다.
- `MICRO_PROBE`는 order-size-too-small 신호용 별도 실험 경로입니다. 일반 전략 성과와 섞지 말고, 최소 `30` filled probes 또는 `4` calendar weeks 관찰 전 regular sizing으로 승격하지 마세요.

## Build, Test, and Development Commands

- `python -m pip install -r requirements.txt`: 실행 의존성을 설치합니다.
- `python trading_bot.py --paper`: 모의투자 모드로 봇을 실행합니다.
- `python trading_bot.py --live`: 실전 모드 실행입니다. 반드시 preflight와 설정 검토 후 사용하세요.
- `python dashboard/dashboard_server.py`: 대시보드 서버를 시작합니다.
- `python tools/live_preflight.py --mode paper --skip-dashboard --json`: paper 설정을 점검합니다.
- `python tools/live_preflight.py --mode live --skip-dashboard --json`: live 설정을 점검합니다.
- `python -m py_compile trading_bot.py dashboard/dashboard_server.py claude_memory/brain.py`: 주요 경로의 문법 오류를 빠르게 확인합니다.
- `python -m pytest -q`: 전체 회귀 테스트를 실행합니다.
- `python -m pytest tests/test_action_routing.py -q`: 개발 중 특정 테스트 파일만 실행합니다.
- `python -m unittest ml.test_full`: ML DB 검증을 실행합니다.

## Coding Style & Naming Conventions

Python 코드는 4칸 들여쓰기를 사용합니다. 함수와 변수는 `snake_case`, 클래스는 `PascalCase`, 모듈명은 기능을 설명하는 이름을 사용하세요. 새 추상화를 만들기 전에 기존 패턴을 우선 따르며, 특히 런타임 설정, 브로커 truth, 감사 저장 로직은 주변 구현과 일관성을 맞춥니다. `.editorconfig` 기준은 UTF-8, LF 줄바꿈, 파일 끝 newline, trailing whitespace 제거입니다. 주석은 짧게 쓰고, 명확하지 않은 매매 안전장치나 복구 로직을 설명할 때만 추가하세요.

## Testing Guidelines

변경한 동작과 가까운 위치에 테스트를 추가하거나 갱신하세요. 새 테스트 파일은 `tests/test_<feature>.py`, 테스트 함수는 `test_<expected_behavior>` 형식을 따릅니다. 개발 중에는 관련 pytest만 먼저 실행하고, `trading_bot.py`, 주문 실행, live 설정, audit store, DB 스키마를 건드린 경우 더 넓은 범위의 테스트를 실행하세요. 오케스트레이터나 대시보드 변경에는 `py_compile` 확인도 포함합니다.

기능 변경 후에는 관련 단위 테스트, 관련 통합 테스트, 수익률/자산곡선/브로커 truth 계산축 점검, 로그/대시보드 문구 확인, 전체 QA 순서로 검증하세요.

## Commit & Pull Request Guidelines

최근 커밋 히스토리는 `feat:`, `fix:` 같은 Conventional Commit 접두사와 짧은 한국어 또는 영어 요약을 사용합니다. 커밋은 하나의 동작 변경 단위로 묶고 런타임 상태 파일은 제외하세요. PR에는 변경 내용, 위험 영역, 실행한 테스트 명령, config/env 영향, 필요한 경우 대시보드 또는 Telegram UI 스크린샷을 포함합니다.

커밋 전에는 `git diff --stat`, `git status --short`, 단계별 테스트 결과, 전체 QA 결과, 수익률 계산축 KR/US 분리, 대시보드 live truth 경로를 확인하세요.

## Security & Configuration Tips

실제 `.env`, `.env.live`, `.env.paper`, 토큰 파일, 브로커 자격 증명, 로컬 `*API*.txt` 메모는 절대 커밋하지 마세요. 설정 예시는 `.env.example`에 문서화합니다. 설정된 pre-commit hook은 `python tools/check_mojibake.py --staged`를 실행하므로, 한국어 문서나 주석을 수정할 때 인코딩 깨짐이 없도록 확인하세요.

`trading_bot.py` 시작 시 `--live` 플래그에 따라 `.env.live` 또는 `.env.paper`를 먼저 로드하고, 없으면 `.env`로 fallback합니다. live 모드에서는 `config/v2_start_config.json`의 `env_overrides`가 `os.environ`을 덮어쓸 수 있으므로 live 설정 변경 시 두 경로를 함께 검토하세요.

## PathB 운영 파라미터 (운영자 확인 필수)

**아래 값을 변경하기 전에 반드시 운영자에게 먼저 알린다. 코드, config, 자동 수정 어떤 경로로도 무단 변경 금지.**

### 현재 활성화 게이트 (2026-05-21 기준)

| 설정 | 현재값 | 의미 |
|---|---|---|
| `PATHB_KR_LIVE_ENABLED` | `true` | KR PathB live 활성 |
| `PATHB_US_LIVE_ENABLED` | `true` | US PathB live 활성 |
| `PATHB_KR_SHADOW_PLAN_ENABLED` | `false` | KR shadow 플랜 비활성 |
| `KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK` | `false` | KR zone hit 주문 차단 해제 |
| `PATHB_INTRADAY_ONLY` | `false` | multi-day hold 허용 |

### 공통 운영 파라미터 (KR = US)

| 파라미터 | 현재값 |
|---|---|
| 고정 주문금액 | 500,000 KRW |
| 최대 포지션 수 | 15 |
| 일일 최대 진입 수 | 40 |
| 최소 confidence | 0.5 |
| 재진입 쿨다운 | 60분 |
| 장 초반 soft gate | 0~60분 size × 0.5 |

### KR/US 차이

| 파라미터 | KR | US |
|---|---|---|
| 슬리피지 캡 | 1.003 | 1.002 |
| Protective hold 최소 거리 | 0.5% | 0.3% |

설정 소스: `.env.live` + `config/v2_start_config.json` (env_overrides). 두 곳이 모두 일치해야 반영된다.
