# 대시보드 재시작·운영 점검·다른 커밋 검토 — 2026-09-09

## 실제 조치와 확인

- 승인 범위대로 대시보드만 재시작: PID23964 → PID32848, 2026-09-09 01:56:59 KST. 기존 프로세스 실행 파일/명령행/포트 소유자를 확인하고 스택 시작 mutex 아래에서 교체했다. 별도 창 없이 같은 upbit Python으로 시작했다.
- `/virtual` HTTP200 및 새 카드 `독립 연구 — 다자산 추세 / 실적 개선 후 드리프트` 포함 확인. `/api/research`의 independent_research 항목에서 두 전략 상태와 신호0 확인. 대시보드 시작 오류 없음. Flask 개발 서버 경고는 남아 있다.
- 매매 봇 PID2732는 01:00:30 KST 시작한 기존 프로세스 그대로다. 이번에 매매 봇을 정지/시작하지 않았다.
- `.env.live`의 US_SWING_ORDER_SUBMIT_ENABLED=false, KR_FALLEN_ORDER_SUBMIT_ENABLED=false, LEGACY_NEW_BUY_DISABLED=true, PATHB_KR_LIVE_ENABLED=false, PATHB_US_LIVE_ENABLED=false, PROFIT_STRATEGY_ENABLED_IDS 빈 값 확인. 설정은 수정하지 않았다. 모든 전략/시장 전체에 대해 신규 실주문이 영구적으로 불가능하다고 포괄 보증하는 점검은 아니다.
- `claudetrade_event_ledgers`: Ready, 이전 예약 실행 결과0, 다음 실행09-09 21:05 KST. 다른 수집기/텔레그램을 실행하지 않도록 `--only independent_inputs,independent_research`로 범위를 제한해 수집→계산을 점검했다.
- 실제 예약 작업과 같은 `C:/Users/Unknown/anaconda3/envs/upbit/python.exe`(Python3.11.0/yfinance1.2.0)에서도 위 두 단계 모두 rc0. ETF4/4·각253봉·최종 완결09-04, 실적 캘린더440행, collection_errors={} 확인. 출력 생성 이후 API에서도 새 generated_at을 확인했다.
- 현재 미국장이 이미 열린 시점이므로 추세는 OBSERVING_ENTRY_WINDOW_CLOSED, 실적은 가이던스/비교기준 부족으로 BLOCKED. 전자는 관측 정상/진입시각 종료, 후자는 실제 입력 미완료다. 둘 다 거래/성과 표본이 아니다.

## 다른 세션 커밋 검토

- `76a6d27`: 제목은 독립 연구 레인 전체 구현처럼 보이지만 실제 diff는 검토 보고서와 tests/test_dashboard_strategy_cohort.py, 2개 파일239행이다. 실행 코드는 앞선 `6730656`에 이미 포함돼 있다. 같은 기능을 두 번 구현했다고 집계하면 안 된다. 추가 테스트는 제출/체결/정산/엄격한 CLEAN 표본 구분을 검증한다.
- `6730656`: 독립 신호 엔진·입력 수집·연구 표시, 기존 주문 경로와 분리. 정보 시점/달력/신호 차단 및 입력 원본 보존 관련 테스트 재실행.
- `64258dd`: KR 코어 ETF 및 코스닥 급락일 쉐도우 추가. 실주문을 호출하는 경로는 확인되지 않았지만, 아래 성과 검증 관련 결함/제한이 남는다. 테스트 통과가 수익성 증명이나 전체 코드 무결함을 뜻하지 않는다.

### 보완 필요 사항 — 이번에는 해당 전략 코드/계약/원장을 변경하지 않음

1. **급락일 전략 지연 실행 시 신호 시각 오염 위험.** tools/kr_index_etf_panic_shadow.py의 live()는 `_wait_until(15,19,0)` 후 현재 시각 상한을 확인하지 않는다. 예를 들어15:25에 시작하고 최신 시세가 들어오면 이를 ratio_1519/signal_1519로 기록할 수 있다. 시세가 신선하다는 검사와 지정된 판단 시각을 지켰다는 검사는 다르다. 의도한15:19 전략의 forward 증거로 사용하기 전에 지연 실행 거부와 재진입 규칙이 필요하다.
2. **코어 ETF 보고서의 backfill/live 혼합.** tools/core_etf_book_shadow.py의 report()는 두 mode의 MTM/리밸런스를 합산한 NAV/낙폭/수수료를 표시한다. run()도 캐시 최신 과거 종가로 현재 월의 live 리밸런스를 기록할 수 있다. 독립된 실시간 실행 성과가 아니라 가격 참조 시뮬레이션으로 해석해야 한다. 모드별 성과 분리와 결정/체결 시각 기록 전에는 승격 근거로 쓰지 않는다.
3. **대시보드 기존 forward 카드 안내 문구가 구버전.** 아직 `CANDIDATE_STRONG만`이라는 설명이 남아 있다. 실제 신규 게이트는 REVIEW_REQUIRED/RESEARCH_ONLY이며 신규 캐너리는 execution_validation_pending으로 차단한다. 이번에 추가한 독립 연구 카드의 권한 표시는 올바르지만 기존 설명도 후속 정리가 필요하다.

## 미커밋 변경 분류

- data/dart_corp_codes.json, state/brain.json, PID stale 파일, state/us_swing_historical_evidence.json: 기존 실행에서 바뀐 런타임/캐시 데이터. 원본 보존하고 이번 커밋 제외.
- docs/reports/discovery_breakdown_20260907.md: 기존 자동 집계 재생성 변경. 전체 숫자의 재현 검증을 이번에 하지 않았으므로 포함하지 않음.
- 미추적 tools 6종(offline_claude_decision_review, pipeline_attribution_audit, pipeline_case_injection, pipeline_simulation_matrix, screener_performance_review, system_wide_data_flow_audit)은07-23~24 수정된 과거 분석 도구다. 파일 목록/시점만 확인했으며 전체 코드 검토·새로운 공개/푸시 승인을 추정하지 않아 제외했다. 과거 미추적 보고서·대용량 데이터도 제외.
- 이번 커밋은 이 운영 점검 보고서만 대상으로 한다. 최근 다른 세션 커밋은 이미 origin에 존재함을 확인했으며 이력을 수정하지 않는다.

## 검증 범위

독립 입력/전략/watch, forward/canary 안전, 대시보드 cohort, 코어 ETF/급락일 쉐도우, KR fallen/US swing/profit_strategy 주문 브리지 관련 테스트134건을 기본 Python과 운영용 upbit Python 양쪽에서 각각 통과했다. 기본 환경에서만 eventlet 의존성 deprecation 경고2건. 운영용 Python의 실제 연구 수집/계산과 HTTP 확인도 완료했다. 브로커 주문 API 호출·전체 스택 재시작·실매매 체결 실험은 하지 않았다.
