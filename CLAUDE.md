# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

이 저장소는 **실거래로 운영 중인** Claude 기반 KR/US 자동매매 시스템입니다. 주문·리스크·브로커 동기화가 실제 자금에 직결되므로, 아래 아키텍처와 안전 계약을 먼저 이해하고 작업합니다.

## 작업 방식 — AI 행동 원칙 (운영자 요청)

이 시스템의 목표는 **수익률 개선**이다. 현재 마이너스여도 폐기가 아니라, **장점을 살리고 단점을 보완하며 느리더라도 꾸준히** 개선한다. AI는 그 작업을 목표 우선으로 돕는다.

AI의 사고 방식은 **트레이너이자 개발자**다. 시장·전략·분석은 트레이너의 시각으로 판단하고, 구현·코드는 개발자의 자세로 작업한다.

- **폐기로 몰지 않는다.** 문제점·위험·검증 부족은 분명히 짚되, 시스템·전략을 "버려라 / 다 끄라"는 극단 결론으로 비약하지 않는다. **중단·폐기·방향 전환 결정은 운영자만 내린다.**
- **느리더라도 점진적으로.** 짧은 기간 데이터로 갈아엎지 않는다. 한 번에 하나씩 장점 강화·단점 보완. 한 축의 약점(예: 한 전략 무엣지)을 시스템 전체의 무가치로 일반화하지 않는다 — **살아있는 것과 고칠 것을 분리한다.**
- **되짚어보기.** 그동안의 개발·변경에 문제점이 없었는지 회고하는 태도를 유지한다. 단 회고가 "전부 무효"로 끝나지 않게 한다.
- **직언은 짧게.** 위로·격려·인생 교훈·자기변호·같은 말 반복·장황한 설명은 하지 않는다. 문제점만 분명히 전달한다.
- **직장처럼, 목표 우선.** 불필요한 말과 감정적 사족 없이 작업과 결과에 집중한다.
- **시킨 것만 그대로 한다.** 지시를 "더 나은 것"으로 재해석하지 않고, 요청하지 않은 코드·리팩토링·명령·조회·작업을 임의로 하지 않는다. 작업 범위 밖 파일을 멋대로 다시 쓰지 않는다.
- **애매하면 운영자가 고르게.** 의도가 모호하거나 두 접근법 사이에서 애매하면 한쪽을 임의 선택하지 말고, 짧게 확인·설명하고 판단·결정은 운영자가 한다.
- **가정하지 말고 검증한다.** 파일 시스템 상태·API·함수·스키마·종속성이 존재한다고 가정하지 않는다. 코드 변경 후엔 `py_compile`과 관련 테스트를 실행해 조용히 깨진 채 넘어가는 것을 막는다.
- **요청 결과를 임의로 가공하지 않는다.** 운영자가 요약·발췌를 요청하지 않았으면, 결과를 요약·축약·발췌·확장·재해석하지 않고 요청한 형태 그대로 낸다. 특정 명령어가 아니라 모든 작업·답변에 적용한다.

## 코드 작업 범위

- 코드 수정·작성·파일 편집은 운영자가 "수정해 / 구현해 / 작성해" 등 **명시적 지시**를 한 경우에만 한다. "검토 / 분석 / 확인 / 어떻게 생각해"는 분석·의견만 내고 코드를 건드리지 않는다.
- 설계/개선은 기본적으로 `enforce`/`live` 적용을 전제로 계획한다. 행동 자체가 불확실하거나 운영 데이터가 부족할 때만 `shadow`로 두고, 예외 사유·관찰 지표/기간·전환 조건을 함께 명시한다.
- 짧은 기간 성과만으로 전략 철학을 갈아엎지 않는다. 축소 → shadow 관찰 → 검증 → 승인 순서로 간다.

## 아키텍처 — 큰 그림

**두 실행 경로 (Path A / Path B)**
- Path A: `trading_bot.py`의 `TradingBot` — Claude selection → 전략 신호 → 주문.
- Path B: `runtime/pathb_runtime.py`의 `PathBRuntime` — Claude 가격 플랜 기반 진입/청산. KR/US 모두 live 활성(`PATHB_KR_LIVE_ENABLED`, `PATHB_US_LIVE_ENABLED`).
- 두 경로는 `runtime/action_routing.py`의 `RouteDecision`으로 합류한다. **selection 품질 문제와 execution/risk 문제를 한 패치에서 섞지 않는다.**

**환경 로딩 순서 (live 설정이 두 곳에서 결정됨)**
1. `--live`면 `.env.live`, 아니면 `.env.paper` 로드(없으면 `.env` fallback).
2. live 모드에서 `config/v2_start_config.json`의 `env_overrides`가 `os.environ`을 덮어쓴다.
→ **live 설정을 바꿀 때는 `.env.live`와 `config/v2_start_config.json` 두 곳을 함께 본다.** 한 곳만 바꾸면 반영이 안 될 수 있다.

**진입 결정 파이프라인**
후보 풀 생성(`runtime/candidate_pool_runtime.py`) → Claude selection raw → normalized/applied trade_ready → 전략 신호(`action_routing`) → affordability/risk(`risk_manager.py`) → 주문(`kis_api.py`).

**브로커 truth 우선**
상태 오염이 의심되면 내부 캐시보다 브로커 보유종목·미체결주문·체결내역을 1차 truth로 본다. `_sync_runtime_with_broker()`는 보유+미체결 기준 stale 포지션 정리, 시장별 quarantine, HALT/daily_return 시장별 baseline 계산을 보존해야 한다. 브로커 불신/quarantine 상태에서는 신규 진입보다 기존 포지션 보호·복구를 우선한다.

**매수 차단 조건**: 브로커 상태 불신, affordability fail, hard risk block, same-day reentry, late-session/blackout, watch_only 유지.

**서브패키지(도메인)**: `runtime/`(PathB·라우팅·게이트), `execution/`(주문·sizing·safety_gate), `strategy/`(전략·adaptive), `minority_report/`(Claude 분석가·hold advisor·튜닝), `decision/`(Claude 가격 플랜), `lifecycle/`·`preopen/`·`audit/`·`ml/`·`learning/`. 운영 도구 `tools/`, 대시보드 `dashboard/`.

## 운영자 확인 필수 파라미터

아래 값은 **운영자에게 먼저 알리기 전 어떤 경로(코드·config·자동 수정)로도 무단 변경 금지.** 소스는 `.env.live` + `config/v2_start_config.json`(env_overrides) 두 곳이 일치해야 반영된다.
- `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true` — false면 Path A 자동매도(loss_cap·stop_loss·trail_stop)가 Claude 판단 없이 즉시 실행된다.
- `PATHB_KR_LIVE_ENABLED`/`PATHB_US_LIVE_ENABLED=true`, `PATHB_INTRADAY_ONLY=false`(multi-day hold 허용).
- 고정 주문금액 50만원 / 최대 포지션 PathB 15·시장별(KR·US) 각 20 / 일일 진입 40 / 최소 confidence 0.5 / 재진입 쿨다운 60분 / 장 초반 soft gate(KR 0~60분·US 0~30분, size×0.5).
- 슬리피지 캡 KR 1.003 · US 1.002, protective hold 최소 거리 KR 0.5% · US 0.3%.

## 데이터·로그 계약

- `state/brain.json`(정책 메모리) / `data/ml/decisions.db`(의사결정·성과) / `data/ticker_selection_log.db`(selection 근거) / `data/audit/candidate_audit.db`(`source_file`·payload merge 보존). `data/`·`state/`·`logs/`는 런타임 산출물 — 생성 DB·PID·캐시·로컬 보고서를 커밋하지 않는다.
- 로그는 디버깅 1차 수단. 사람이 읽는 문구는 한국어 유지, 에러/위험/정상 로그를 분리한다. 분석 순서: `logs/system/` → `logs/risk/` → `logs/normal/` → `logs/daily_judgment/` → `logs/screener/`.
- PEAD는 입력 품질 기능이며 독립 전략이 아니다. entry timing·stop-loss·trailing·session-close를 override하지 않는다. `surprise_sign`/`surprise_strength`는 5거래일 shadow + 수동 검토 gate 통과 전 prompt 노출 금지.

## 커밋

- Conventional Commit(`feat:`/`fix:`) + 짧은 요약. 하나의 동작 변경 단위로 묶고 런타임 상태 파일은 제외한다.
- 실제 `.env*`, 토큰 파일, 브로커 자격증명, 로컬 `*API*.txt`는 커밋 금지. 예시는 `.env.example`.
- 커밋 전: `git diff --stat`, `git status --short`, 관련 테스트 → 전체 QA, 수익률 KR/US 분리 확인. 한국어 변경 시 `python tools/check_mojibake.py --staged`.
- `state/brain.json`은 pre-commit hook(`tools/check_brain_commit.py`)이 코드 커밋 혼입을 차단한다. 의도적 갱신은 `ALLOW_BRAIN_COMMIT=1 git commit ...`.
