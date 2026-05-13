# ClaudeTrade Documentation Hub

이 폴더는 개발 문서를 찾기 위한 진입점이다. active plan은 [TODO_ROADMAP.md](TODO_ROADMAP.md) 하나로 통합하고, 완료 요약은 [DEVELOPED_WORK.md](DEVELOPED_WORK.md)에 둔다.

## 먼저 볼 문서

- [DOCUMENTATION_INDEX.md](DOCUMENTATION_INDEX.md)
  - 전체 문서 분류 기준과 cleanup policy
- [ARCHITECTURE_MAP.md](ARCHITECTURE_MAP.md)
  - 시스템 구성도, 런타임 흐름, 상태 저장소 지도
- [TODO_ROADMAP.md](TODO_ROADMAP.md)
  - 아직 해야 할 일, 우선순위, 사유, 개선 전후 리뷰
- [DEVELOPED_WORK.md](DEVELOPED_WORK.md)
  - 개발 완료, QA 완료, 삭제한 plan 요약
- [DOCUMENTATION_INVENTORY.md](DOCUMENTATION_INVENTORY.md)
  - 현재 Markdown 인벤토리

## 원칙

- 운영 기준 문서는 `docs/` 루트에 둔다.
- active plan은 `docs/TODO_ROADMAP.md`에만 둔다.
- 완료 검증 리포트는 `docs/reports/`에 둔다.
- `data/**` 아래 Markdown은 실행 산출물로 보고, 운영 문서와 분리한다.

## 자주 쓰는 기존 문서

- [trading_process.md](trading_process.md): 매매 프로세스
- [rsi_threshold_research.md](rsi_threshold_research.md): RSI 기준 연구 메모
