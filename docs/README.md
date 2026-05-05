# ClaudeTrade Documentation Hub

이 폴더는 개발 문서를 찾기 위한 진입점입니다. 완료된 plan은 `docs/plans/`에 계속 쌓지 않고 삭제하며, 완료 요약과 active TODO를 아래 기준 문서로 분리합니다.

## 먼저 볼 문서

- [DOCUMENTATION_INDEX.md](DOCUMENTATION_INDEX.md)
  - 전체 문서 분류 기준과 현재 Git 상태 요약
- [ARCHITECTURE_MAP.md](ARCHITECTURE_MAP.md)
  - 시스템 구성도, 런타임 흐름, 상태 저장소 지도
- [DEVELOPED_WORK.md](DEVELOPED_WORK.md)
  - 개발 완료, QA 완료, 검증 리포트가 있는 항목
- [TODO_ROADMAP.md](TODO_ROADMAP.md)
  - 아직 해야 할 일, 진행 중 문서, 후속 작업
- [DOCUMENTATION_INVENTORY.md](DOCUMENTATION_INVENTORY.md)
  - 현재 확인한 전체 Markdown 파일 인벤토리

## 원칙

- 운영 기준 문서는 `docs/` 루트에 둡니다.
- 아직 완료되지 않은 계획 문서는 `docs/plans/`에 둡니다.
- 완료 검증 리포트는 `docs/reports/`에 둡니다.
- 오래된 개발 로그는 `docs/archive/`에 둡니다.
- `data/**` 아래 Markdown은 실행 산출물로 보고, 운영 문서와 분리합니다.

## 자주 쓰는 기존 문서

- [trading_process.md](trading_process.md): 매매 프로세스
- [rsi_threshold_research.md](rsi_threshold_research.md): RSI 기준 연구 메모
- [KIS_API_TODO.md](KIS_API_TODO.md): KIS API 확인 및 보완 목록
- [KIS_WS_FILL_SYNC_PLAN.md](KIS_WS_FILL_SYNC_PLAN.md): KIS 체결통보 WebSocket 연동 계획
