# 모델용 데이터 인벤토리 — 일봉 OHLCV 밖의 새 정보 (2026-09-27)

읽기 전용 실측(SQLite `mode=ro`, 대용량은 표본·카운트). 경로는 저장소 루트 기준.
결론: **새 정보가 실제로 쌓여 있는 곳은 DART 공시(KR)·US 어닝 일정/서프라이즈·SEC Form 4·Claude 호출 코퍼스 4곳**이다. 뉴스는 제목/스니펫뿐이고, 분봉·호가는 KR은 전향 축적만 가능, US는 Alpaca SIP로 무료 백필 가능하다. KR 수급 원본 데이터는 디스크에 없다(재수집 필요).

## 1. 전체 표

PIT 등급: A=공시/수신 시각 있음 · B=날짜만(다음 시가 규칙 필요) · C=조회 시점 스냅샷(소급 재계산·생존편향) · D=PIT 불가.
"사용 이력"은 grep과 메모리에 기록된 판정 기준.

| # | 소스 / 경로 | 시장 | 내용(주요 필드) | 단위 | 기간 · 행수 | PIT | 커버리지 | 사용 이력 | 쓸 곳 | 공백/차단 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `data/shadow/kr_insider_ledger.jsonl` (DART elestock) | KR | 보고자·직위·주요주주 여부·증감 수량·비율, rcept_no | 이벤트 | 2025-01-02~2026-09-23 · 23,306 (증가 18,898) | B (rcept_dt 날짜만) | 1,482종목 | 가상 북 arm(`tools/discovery_pools.py`). KR 내부자 K1 +2.64% → 삼성전자 착시로 기각(09-09) | 진입·랭킹 | 접수 시각 없음 |
| 2 | `data/shadow/kr_insider_reason.jsonl` (본문 파싱) | KR | 변동 사유(market_buy 5,187 / conversion 4,754 / unknown 3,710 / appoint·rights·option…), 본문 발췌 `reason_text` | 이벤트 | 2025-06-02~2026-09-23 · 15,461 | B | 1,292종목 | 내부자 전량 +0.36 수준(판정 아님) | 진입 필터(장내매수만 분리) | 발췌만 저장(전문 아님) |
| 3 | `data/shadow/kr_insider_plan_ledger.jsonl` | KR | 거래계획 보고(매수/매도, 기간, 수량) | 이벤트 | 2025-09-08~2026-09-23 · 811 | B(예정일은 미래 정보 아님) | — | 거래계획 매수 −1.38(백필 음수) | 회피 | 표본 작음 |
| 4 | `data/analysis/dart_events_12m.jsonl` · `dart_corp_actions_12m.jsonl` | KR | 공시 종류(공급계약 4,957 / 유상증자 2,338 / 최대주주변경 1,093 / 자사주 345 / 무상 112), 제목, rcept_no | 이벤트 | 2025-09-01~2026-09-04 · 8,845 + 723 | B | 1,347종목 | 자사주 후 급락 OOS −2.0 기각, 소각 −1.06 기각, 최대주주변경 회피 −3.9% | 진입·회피 | **제목·종류만. 본문 없음** |
| 5 | `data/shadow/kr_dart_terms.jsonl` | KR | 자사주 취득기간·방법·수량, 무상 기준일·비율 (본문 파싱) | 이벤트 | 2025-09~2026-09 · 360 | B | — | 자사주 개시일 +1.01(후보) | 진입 타이밍 | 표본 작음 |
| 6 | `data/shadow/kr_event_signals.jsonl` · `kr_event_observations.jsonl` · `kr_event_phantom.jsonl` · `kr_event_docs/*.txt` | KR | 실시간 DART 감지 시각 `ts_detected`, 본문 파싱 필드(계약금액·매출대비%·상대방), LLM 판정, 5m/30m/15:20 가격 | 이벤트(초) | 2026-09-04~09-23 · 2,550 / 149 / 57 · **본문 142건(1.9~6.8KB, 6,000자 컷)** | **A** (감지 시각) | 1,083종목 | shadow 레인(kr_event_v1) 표본 축적 중 | 진입(장중)·텍스트 판단 | 본문 보관은 09-07 이후 142건뿐 |
| 7 | DART OpenAPI `document.xml` (코드: `runtime/kr_event_lane.py:dart_document_text`, `tools/dart_corp_action_terms.py`, `tools/dart_insider_reason.py`; 키는 env `DART_API_KEY`) | KR | **공시 원문 전체**(zip→XML) | 이벤트 | 과거 rcept_no 전부 조회 가능 | B (원문 자체는 PIT, 시각은 날짜만) | 전 상장사 | 실시간 레인·insider 사유·terms에서 이미 호출 | 텍스트 모델 입력 | 감지 직후 빈 응답(재시도 60s/15분 규칙 있음). 일 호출 한도 확인 필요 |
| 8 | `data/news/kr/*.json` | KR | 종목 뉴스 제목·출처·`published_at`·스니펫(KIS는 content 빈 값, Naver 검색 API 스니펫 중앙 122자), DART 제목 5,950 | 기사 | 파일 920개, 실제 밀집은 2026-05-15~ · 19,769건 | A (published_at) | 520종목 | Claude 프롬프트 입력(`phase1_trainer/digest_builder.py`)만. 모델 검증 없음 | 텍스트 판단 | **본문 없음**, 699파일 빈 값, 이력 4.5개월 |
| 9 | `data/news/us/*.json` | US | Finnhub 19,136 / KIS 6,247 / SEC EDGAR 5,347 / AlphaVantage 3,379, 스니펫(중앙 145자·p90 500자), market_news 6,205 | 기사 | 2023-10~, 밀집은 2026-01-07~ · 34,207건 | A(published_at 22,150건에만) | 682종목 | `us_swing_news_arm_shadow.jsonl`(38세션, 플래그 arm) | 텍스트 판단·회피 | 스니펫, 2026 이전 희박 |
| 10 | `data/analysis/us_earnings_dates.jsonl` | US | 발표일·**hour(AMC/BMO)**·eps_est·eps_act | 이벤트 | 1998~2027 (과거 180,416, 실적치 179,436; 2014~ 연 1.1~1.7만) | B+ (hour로 반영 시점 확정. eps_est는 조회 시점 값) | 1,633종목 | **US 어닝 가격반응 PEAD −0.23(n 415) 백필 음수(09-07)**. 서프라이즈 크기·부호를 피처로 쓴 검정은 없음(PEAD는 입력 품질 기능, surprise_sign 게이트 미통과) | 진입·회피·보유(어닝 전 청산) | fetched_at 3회 스냅샷 — 날짜 정정 이력 없음 |
| 11 | `data/shadow/earnings_pit_ledger.jsonl` · `earnings_calendar_snapshots/`(27일) · `data/earnings_calendar.json` | US(+KR 키) | 최초 관측 시각·정정 이력 append-only | 이벤트 | 2026-08-31~ · 852 | **A** | 551종목 | 원장만 | 위 10의 PIT 교차검증 | 09-01부터라 이력 짧음 |
| 12 | `data/shadow/us_insider_ledger.jsonl` + `data/analysis/edgar_form345/*_form345.zip`(2025q3~2026q1) | US | Form 4 **TRANS_CODE 'P' + 취득 'A'만**(공개시장 매수), 보고자·관계·수량·가격·금액 | 이벤트 | filing 2025-07-01~**2026-05-22** · 3,055 | A-(filing_date; submissions의 acceptanceDateTime 사용 가능) | 556종목 | **US Form4 군집 +1.10%(163세션 t 2.97) 후보**, US 내부자 K1 +1.35(t 2.7) | 진입·랭킹 | 일별 수집 `daily_state.json`이 05-22에서 멈춤, 09-08 백필 로그 0바이트(403 추정 — HTTP 상태는 미확인) |
| 13 | `data/analysis/edgar_cache/CIK*.json` | US | EDGAR submissions(최근 1,000건 폼·접수시각·items) | 이벤트 | 64개 CIK | A | 64종목 | 거의 없음 | 8-K 이벤트 | 소수 |
| 14 | `data/supplement/kr/*.json` | KR | 외국인·기관·개인 순매수(수량), usd_krw, vkospi | 일 | 2026-03-19~09-27 · 191일 | C (flow_date_matched 756/2,115 — 대부분 T-2~T-4 지연값) | **12종목** | 프롬프트 입력 | — | 대형주 12개뿐, 날짜 불일치 |
| 15 | KR 투자자별 수급(네이버 모바일 `m.stock.naver.com/api/stock/{code}/trend`, 수집기 `tools/research/research_naver_mobile_flow_collect.py`) | KR | 개인·기관·외국인 일별 순매수 + 거래량 | 일 | 09-11에 1,507종목 100% 수집했다고 기록 | B | 1,507 | **KR 기관 축 기각**(창A 증분 −0.77pp). 버리는 쪽(기관 > −8%)은 음수 → 회피 규칙 성격 | 회피 | **수집 결과 파일이 저장소·스크래치패드 어디에도 없음 → 재수집 필요.** KRX(pykrx) 자동수집은 약관 위반·IP 1일 차단, KRX Open API 키 미승인(401), 네이버 데스크톱 차단 |
| 16 | `data/shadow/kr_open_flow.jsonl` | KR | 09:05~09:30 스냅샷: 체결강도, **총매수/총매도 잔량·불균형·스프레드**, 누적 거래대금 | 스냅샷 | 2026-09-08~09-25 · 1,910 (14일) | A | 262종목 | 수집만 | 진입(시가 직후) | 짧음 |
| 17 | `data/shadow/kr_ws_ticks/*.jsonl` | KR | KIS H0STCNT0 원시 체결틱(가격·체결량·매수/매도 잔량 합계 포함 ^ 구분) | 틱 | 12일(09-08~09-23), **09:00~09:40만** · 파일당 1.2~2.8만 줄 | A (rx 수신 시각) | 26종목 | `tools/kr_absorption_shadow.py` | 진입(흡수 패턴) | 관측 구독 종목만, 오전 40분만 |
| 18 | `data/price/minute/{kr,us}/*.csv` | KR/US | 1분봉 OHLCV + collected_at | 분 | KR 2026-05-26~09-23 · 1.84M행 · **4,715 종목-일(종목당 중앙 2일)** / US 2026-04-08~09-26 · 1.98M행 · 8,350 종목-일 | A | KR 779 / US 939 | 출구·BE락·캡 시뮬(`tools/be_lock_minute_validation.py` 등 6개) | 출구·진입 타이밍 | 후보 카운터팩추얼 위주라 희소. KR 과거 분봉 백필 경로 없음 |
| 19 | Alpaca SIP 1Min (코드: `tools/us_panic_close_shadow.py`, `us_open_impact_collector.py`, `be_lock_minute_validation.py`; 키 `.env.alpaca`) | US | 통합 분봉(프리마켓 포함) | 분 | 무료, 15분 지연 조건, 수년 이력 | A | 상폐 포함 | `data/analysis/us_open_window_returns.jsonl` 35,992행(1,521종목, 08-10~09-24), `premarket_backfill_us.jsonl` 170행 → 프리마켓 축 기각(08-25) | 진입·출구 | 대량 분봉 백필은 미실행 |
| 20 | `data/market_data/yfinance/intraday/US/5m` | US | 5분봉 | 5분 | 2026-01-29~04-24 · 23종목 | A | 23 | 없음 | — | 소수 |
| 21 | `data/audit/candidate_audit.db` → `candidate_registry_quotes` | KR/US | 후보 가격 스냅샷 | 스냅샷 | 07-16~09-26 · 169,969 | A | 후보 풀 | 1곳 | — | **bid/ask 전 행 NULL** — 호가 데이터 아님 |
| 22 | `data/shadow/kr_short_ratio.jsonl` · `data/analysis/krx_short_cache/`(10일) | KR | 공매도 비중 | 일 | 2026-08-07~09-23 · 554 | B(captured_at 지연) | 260종목 | 없음 | 회피 | KRX 자동수집 금지 |
| 23 | `data/analysis/finra_shvol_cache/`(14일) | US | FINRA 장외 보고 거래량(공매도 거래량) | 일 | 2026-07-10~08-10 | B | 전종목 | FINRA 장외 비중 축 **기각**(4항목 중 3 불통과) | — | 2023~ 전체 수집분은 디스크에 없음 |
| 24 | 옵션·IV·공매도 잔고(short interest) | KR/US | — | — | **없음** | — | — | 없음(grep 0건) | — | 수집기 없음 |
| 25 | `data/shadow/krx_market_alert.jsonl` | KR | 투자주의/경고 지정 목록 | 일 | 2026-09-08~09-25 · 115 | B | 53종목 | 2곳 | 회피 | 짧음 |
| 26 | `data/shadow/kr_ipo_ledger.jsonl` · `kr_ipo_calendar.json` (38.co.kr) | KR | 공모가·시초가·종가 | 이벤트 | 2024-11~ · 168 (비SPAC 상장 52) | B | — | 공모주 원장(청약은 수동) | 별도 레인 | 표본 작음 |
| 27 | `data/sector_map.json` · `data/dart_corp_codes.json`(3,993) | KR/US | KSIC 업종·섹터 / 종목↔corp_code | 정적 | 현재 스냅샷 | C | KR·US | sector_map 15곳 | 그룹핑 | 현 시점 분류 → 과거 적용 시 lookahead·생존편향 |
| 28 | `data/shadow/us_sector_context.jsonl` · `us_market_width.jsonl` · `data/analysis/{kr,us}_breadth_proxy_daily.csv` · `vix_term_daily.csv` · `data/supplement/us`(VIX·DXY·FOMC/CPI/NFP 플래그) | KR/US | 섹터 ETF 수익률, 등락 폭, VIX 기간구조, 매크로 캘린더 | 일 | 대부분 2026-08~ (breadth·vix_term은 더 김) | B(`feature_basis=last_close_before_session`) | 시장 단위 | breadth 34곳, 국면 게이트 연구(기각) | 사이징·국면 | 국면 게이트 기각 이력 |
| 29 | `logs/raw_calls/*.json` (+ `data/audit/agent_call_events.db` 15,787행 인덱스) | KR/US | **Claude 프롬프트 전문 + raw_response + parsed + timestamp**, 라벨별(select_tickers 2,143 / single_symbol_judge 1,916 / analyst bull·bear·neutral r1·r2 약 5,800 / hold_advisor 약 3,800 / postmortem 183 …) | 호출 | 2026-05-15~09-25 · 15,391파일 · 1.97GB | A (timestamp, known_at) | 후보 풀 | 모델 역선별 규명(09-01, 제거 시 12세션 −5.83%→+0.89%) | 라벨·텍스트 피처·메타 판단 | 한 국면 4개월. 프롬프트가 같은 OHLCV 피처를 담고 있어 새 정보는 응답 텍스트뿐 |
| 30 | `data/audit/candidate_audit.db` (15.5GB): `audit_candidate_rows` 492,770 / `audit_candidate_outcomes` 2,094,112(30·60·1440·2880·4320분) / `candidate_counterfactual_paths` 1,193,519 / `audit_claude_calls` 13,652 | KR/US | 후보별 prompt_rank·in_prompt·claude_action·claude_reason·veto + 전향 수익률 라벨 | 후보×시점 | 2026-04-20~09-25 | A (known_at) | KR 약 680 / US 약 1,340 종목(표본 1/20 기준) | 52곳(감사·리뷰) | **라벨 소스**(텍스트→전향수익) | claude_reason 실질 텍스트(>40자)는 약 5% — 추정 KR 5.3천·US 7.7천 행 |
| 31 | `data/ml/decisions.db` `decisions` | KR/US | 전략 신호 진단(RSI·BB·MACD 등 가격 파생) + forward_1d/3d/5d | 결정 | KR 2025-02-19~ 105,305 / US 2024-06-27~ 184,070 | A | — | 다수 | 라벨 | **가격 파생뿐 — 새 정보 아님** |
| 32 | `data/ticker_selection_log.db` | KR/US | 선정 사유(대부분 `rule_direct(...)` 규칙 문자열)·forward_1d/3d/5d·runup/drawdown | 선정 | 2026-04-07~09-25 · 96,018 | A | KR 853 / US 888 | 다수 | 라벨 | 사유 텍스트는 규칙 생성 |
| 33 | `data/v2_event_store.db` (`v2_decisions` 1,059 · `lifecycle_events` 12,528 · `v2_path_runs` 906) · `data/ml/decisions.db` `v2_canonical_performance` 1,068 | KR/US | Claude 가격 플랜(sell_target·stop·invalid_if 텍스트)·체결·실제 net | 결정 | 2026-04-27~09-21 | A | — | 판정 정본 | 실제 net 라벨 | 1천 건 규모 |
| 34 | `data/ml/claude_decision_facts.db` | KR/US | selection×execution×forward 조인 팩트 | 결정 | 2026-05-20~05-27 · 11,004 | A | — | 1회 빌드 | — | 1주만 |
| 35 | `data/daily_digest/*.json` | KR/US | 일일 다이제스트(universe·context·technicals·breadth·top_news·prev_result) | 일 | 2024-10-01~2026-09-25 · 591 | A(built_at) | 시장 단위 | Claude 입력 | 국면 텍스트 | — |
| 36 | `data/analysis/panic_filter_20260927_claude.jsonl` | KR | Claude의 패닉일 SKIP/TAKE 판단+이유 | 일 | 2018-01~2026-09 · 234 | **D** (모델이 과거 날짜를 알 수 있음 — lookahead 위험) | 지수 | 오늘 시험 | — | 사후 판단 오염 |
| 37 | 가격 원천: `data/price/{kr,us}` (1,800/1,698종목, 2025-05~) · `data/price_backfill_alpaca/us` (1,546, 2023-01~2025-04, adj=all, 금지 7종목) · `data/price_backfill_yf/kr_fallen_price_cache_2018_2026.json` (1,746, 2018~, 생존편향 하한 3.3%) · `data/analysis/us_yahoo_2y` (169) · `data/cache/*.pkl` 2,070(가격 파생 지표 22열) | KR/US | 일봉 | 일 | — | C(수정주가 소급) | — | 소진 | — | 이번 인벤토리의 대상 밖(소진) |
| 38 | 빈/죽은 파일 | — | `data/candidate_audit.db`·`events.db`·`pathb.db`·`trading.db`·`v2_event_store_paper.db` = 0바이트. `data/external_market_data.sqlite`는 dart_disclosures·fred_observations·public_krx_* 스키마만 있고 행 0 | — | — | — | — | — | — | — |
| 39 | `state/` | — | brain.json(정책 메모리 737KB), canary 신호, cohort reliability, PID·락·API 사용량 | — | — | — | — | — | — | 모델 입력용 새 정보 없음 |
| 40 | `logs/`(분봉·호가 확인) | — | `logs/flow/`=봇 실행 흐름 로그(투자자 수급 아님), `logs/screener/`=스크리너 행(가격·거래대금), `logs/funnel/`=감시 트리거 shadow, `logs/preopen/`=스케줄러 이벤트 | — | — | — | — | — | — | **호가·분봉 스냅샷은 로그에 없음** |

## 2. 요청 항목별 답

- **(a) DART 원문**: 저장된 것은 제목·종류(8,845+723건)와 파싱 필드, 원문은 `data/shadow/kr_event_docs/` 142건(6,000자 컷)뿐. 원문 조회 코드는 이미 있다(`runtime/kr_event_lane.py:dart_document_text`, env `DART_API_KEY`) → 과거 rcept_no 전량(내부자 23,306 + 이벤트 8,845) 원문 백필 가능. 시각: DART list에 접수시각이 없어 `rcept_dt`만 → **장중 공시도 다음 시가에 반영된 것으로 취급**해야 lookahead가 없다. 정정공시([기재정정])는 원 rcept_no와 분리.
- **(b) 뉴스 본문**: 없음. 제목+스니펫(KR 중앙 122자, US 중앙 145자). 밀집 이력 US 2026-01~, KR 2026-05~.
- **(c) 분봉·호가**: KR 분봉 4,715 종목-일(희소), 틱 12일×26종목(오전 40분), 잔량 스냅샷 14일×262종목. KR 과거 분봉 백필 경로 없음 → 전향 축적만. US는 분봉 8,350 종목-일 보유 + Alpaca SIP 1Min로 수년 무료 백필 가능(미실행). 호가(bid/ask)는 KR 잔량 합계 외에 없다.
- **(d) 투자자 수급**: 운영 보유분은 12종목(supplement, 지연값). 1,507종목 수집분은 디스크에 없음. 축 자체는 매수 신호로 기각, 회피 규칙 성격. US는 투자자별 수급이 원천적으로 없음.
- **(e) 펀더멘털**: 재무제표 수집기 없음(grep 0). US는 EPS 추정·실적(180k)만. KR 잠정실적은 공시 레인 kind `prelim_earnings`(표본 11건)로만.
- **(f) US Form 4·어닝**: Form 4 매수 3,055건(05-22 이후 공백), 어닝 180k(hour 포함).
- **(g) 옵션·IV·공매도**: 옵션·IV 전무. 공매도는 KR 비중 554행·US FINRA 14일(축 기각).
- **(h) Claude 로그**: raw_calls 15,391(1.97GB) + candidate_audit 49만 후보×209만 전향 라벨. 실질 reason 텍스트는 약 1.3만 행.

## 3. 합법·저비용 추가 가능 외부 데이터 (lookahead 주의)

| 소스 | 비용 | 가져올 것 | PIT 주의 |
|---|---|---|---|
| DART OpenAPI `document.xml` | 무료(키 보유) | 내부자·자사주·공급계약·유상증자·잠정실적 원문 | 날짜만 → T+1 시가. 정정공시 분리. 일 호출 한도 내 스로틀 |
| DART OpenAPI `fnlttSinglAcntAll` | 무료(키 보유) | 분기 재무제표 | 기준일이 아니라 **정기보고서 접수일(rcept_dt)** 기준으로 결합 |
| SEC `form345` 분기 zip | 무료 | Form 4 2006~ 전량(이미 3분기 보유) → 일별 403 공백도 분기 zip으로 메움 | FILING_DATE(+acceptanceDateTime) 기준. 분기 zip은 분기 종료 후 공개 → 백필 전용 |
| SEC `companyfacts` XBRL · EDGAR full-text search | 무료(UA 필수, ≤10req/s) | US 재무·8-K 본문 | `filed` 날짜 기준. companyfacts 값은 재작성분 섞임 → 최초 filed 값만 |
| Alpaca SIP 1Min | 무료(15분 지연) | US 분봉·프리마켓 | 수정주가 변환 필요(raw vs 수정, TQQQ 분할 사례) |
| FINRA short interest(격주) | 무료 | US 공매도 잔고 | 기준일 아닌 **공표일**(약 8영업일 지연)로 결합 |
| yfinance 재무·옵션 | 무료 | 재무 스냅샷, 옵션 체인 | **PIT 아님**(현재 스냅샷·재작성). 옵션은 과거 없음 → 전향 수집만 |
| KRX Open API | 무료(서비스 승인 필요) | KR 수급·공매도 | 키 401(미승인) → 운영자 신청 필요. data.krx.co.kr 자동수집은 금지 |
| 네이버 모바일 trend | 무료 | KR 수급 재수집 | ETF 패닉 레인과 호스트 공유 → 스로틀 1초·예산 상한 필수 |

## 4. 새 정보 후보 우선순위

| 순위 | 소스 | 표본 추정 | PIT | 기존 검정 | 이유 |
|---|---|---|---|---|---|
| 1 | **DART 공시 원문(KR)** — 기존 원장 + `document.xml` 백필 | 내부자 23,306(장내매수 약 5,200) + 이벤트 8,845(공급계약 4,957·유상증자 2,338) · 1,347~1,482종목 · 20개월 / 원문 백필 시 과거 수년 확장 가능 | B(T+1 시가 규칙) / 실시간 레인은 A | 제목·종류 기반 규칙은 대부분 기각(자사주 OOS −2.0, 소각 −1.06, 내부자 K1 착시). **원문 필드(계약금액/매출비·상대방·취득 목적·자금 용도)를 피처로 쓴 모델은 없음** | 가격에 없는 정보가 가장 많고, 수집 코드·키가 이미 가동 중. 텍스트 판정(LLM)과 결합 가능 |
| 2 | **US 어닝 서프라이즈** (`us_earnings_dates.jsonl`) | 과거 약 179k 이벤트 · 1,633종목 · 2014~ 연 1.1~1.7만 | B+(hour로 반영 시점 확정; eps_est는 조회 시점 값) | 가격반응 PEAD −0.23(n 415)만 음수. **서프라이즈 크기·부호 피처는 미검정** | 표본이 가장 크고 12년 OOS가 가능 → 인샘플 밖 검정 조건 충족. 어닝 전 보유 회피(출구) 용도도 있음 |
| 3 | **SEC Form 4 (US)** — form345 분기 zip 확장 | 현재 3,055 · 556종목. 2006~ zip 백필 시 매수 수만 건 | A-(filing_date/acceptance) | Form4 군집 +1.10%(163세션 t 2.97) 후보, 내부자 K1 +1.35(t 2.7) — 인샘플 13개월뿐 | 후보 신호가 이미 있고, 무료 bulk로 OOS 창을 바로 만들 수 있다. 05-22 이후 공백도 zip으로 메움 |
| 4 | **Claude 호출 코퍼스 × 후보 전향 라벨** | 후보 492,770 · 라벨 2.09M(5개 horizon) · raw_calls 15,391 · 실질 reason 약 1.3만 | A(known_at) | 역선별 규명(09-01) — 선택 신호로는 음수 | 새 시장 정보가 아니라 **"Claude 판단이 틀리는 조건" 라벨**이다. 필터·거부권 모델용. 기간 4~5개월·한 국면이라 OOS 불가 → shadow 전용 |
| 5 | **US 장중 분봉 (Alpaca SIP 1Min 백필)** | 현재 8,350 종목-일 + open-window 35,992행. 백필 시 1,500종목×수년 | A | 프리마켓 거래량 축 기각(08-25), 개장 충격 원장 110행 | 일봉에 없는 장중 경로(개장 15분·마감 급락·흡수)를 과거까지 만들 수 있는 유일한 무료 경로. KR은 전향 축적만 가능 |

제외 사유: 뉴스(제목·스니펫뿐, 밀집 이력 4~9개월), KR 수급(축 기각 + 원본 파일 부재), 옵션/IV(과거 데이터 없음), 펀더멘털 스냅샷(PIT 아님).

**다음 행동(수익 방향)**: 1순위는 DART 원문 백필(내부자 장내매수 5.2천 + 공급계약 5천 건의 원문 필드 추출) → 사전등록 후 OOS 창(2023~2024 공시)으로 검정. 2순위는 US 어닝 서프라이즈를 2014~2022 인샘플 / 2023~ OOS로 나눠 우리 D7 보유 초과수익(IWM 벤치) 기준으로 1회 검정. 현재 자본 월 기대 금액은 검정 전이라 산출 불가.
