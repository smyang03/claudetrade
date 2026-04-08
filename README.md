# claudetrade

Claude 기반 KR/US 자동매매 봇입니다.  
현재 구조는 `Claude가 시장과 종목을 고르고`, 규칙/적응형 파라미터가 진입을 판단하며, 결과는 `brain.json`과 DB에 누적하는 방식입니다.

## 핵심 구조

- 시장 판단: Bull / Bear / Neutral 3개 분석가가 토론 후 컨센서스 생성
- 종목 선택: 스크리너 후보를 Claude가 최종 선택
- 진입 판단: `gap_pullback`, `mean_reversion`, `momentum` 등 전략 + `adaptive_params`
- 실행 관리: 리스크, 주문, TP/SL, 트레일링, 재스크리닝
- 학습 누적:
  - `state/brain.json`: 분석가/모드/전략 성과 요약
  - `data/ml/decisions.db`: 신호, 차단, 체결, 결과 원자료
  - `data/ticker_selection_log.db`: 선택 종목 추적용 로그

## 현재 운영 원칙

- KR과 US는 동일한 구조를 쓰되, 재선정/교체 정책은 분리
- 장초와 장중을 다르게 봄
- 전략 파라미터는 기본값 + adaptive overlay 구조
- ML은 아직 진입 우선순위 데이터 수집 단계

## 주요 문서

- [docs/trading_process.md](/E:/code/claudetrade/docs/trading_process.md)
  - 실제 세션 시작, 장중, 청산 흐름
- [DATA.md](/E:/code/claudetrade/DATA.md)
  - 상태/로그/DB 파일 설명
- [docs/README.md](/E:/code/claudetrade/docs/README.md)
  - 상세 문서 인덱스

## 주요 디렉터리

```text
claudetrade/
├── trading_bot.py                 # 메인 실행 루프
├── kis_api.py                     # KIS / 시세 / 스크리닝 연동
├── strategy/                      # 전략, adaptive, cross-asset, 우선순위
├── minority_report/               # Claude 분석가, 컨센서스, 튜닝
├── ml/                            # decisions DB, backfill, feature 분석
├── dashboard/                     # 대시보드 서버
├── docs/                          # 운영/연구/아카이브 문서
├── data/                          # 가격, 캐시, 백테스트, DB
├── state/                         # 런타임 상태
└── logs/                          # 판단/분석/시스템 로그
```

## 자주 보는 파일

- [trading_bot.py](/E:/code/claudetrade/trading_bot.py)
- [kis_api.py](/E:/code/claudetrade/kis_api.py)
- [adaptive_params.py](/E:/code/claudetrade/strategy/adaptive_params.py)
- [analysts.py](/E:/code/claudetrade/minority_report/analysts.py)
- [dashboard_server.py](/E:/code/claudetrade/dashboard/dashboard_server.py)

## 실행 예시

```powershell
cd E:\code\claudetrade
python trading_bot.py
```

시뮬레이션:

```powershell
cd E:\code\claudetrade
python -m phase1_trainer.sim_runner --market ALL --engine both --start 2022-01-01 --top 15
```

## 문서 정리 원칙

- 루트에는 사용자/운영 기준 문서만 둠
- 상세 로그성 문서는 `docs/archive/`
- 보류 중인 설계안은 `docs/plans/`
