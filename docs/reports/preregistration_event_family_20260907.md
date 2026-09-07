# 사전등록 — family C_EVENT_V1 + 패닉 마감 진입 + B등급 사전등록 셀 (2026-09-07 밤)

운영자 지시: "모든 전략 다 붙이고 백필까지 다해서 검증까지 해서 리포트하고 쉐도우 운영하게 만들고 진행해." 후보 정본 `new_strategy_candidates_20260907.md`.
이 문서는 등록·판정 규칙을 **결과를 보기 전에** 고정한다. 판정 잣대는 C_REGIME_V1과 동일(`preregistration_regime_gate_20260907.md`): 발견 = ★4조건(종목·날짜 이중 클러스터 t≥2.5 · 인접 칸 동일 부호 · 앞뒤 반기 OOS 동일 부호 · K=1·실운영 금액 재계산 양수), 조기 기각 = forward 30건 세션 t<0.

## 1. 등록 arm (가상 북 `tools/virtual_books.py`, universe xkr/xus, `candidate=True` — 승격 게이트·유령·텔레그램 상세·실주문 브리지에서 자동 제외)

| id | 풀/필터 | 계약 | 백필 시작 | forward 시작 | 반증 |
|---|---|---|---|---|---|
| c_kr_fallen_buyback30 | xkr_fallen3 · 전일 ≤−5% & 자사주 취득결정 공시 후 0~30일 | TP12/SL25/D7 | 2025-09-08 | 2026-09-08 | forward 30건 세션 t<0 또는 "없음" 군 대비 증분 소멸 |
| c_kr_fallen_buyback_active | 위 + 본문 장내취득 기간 내 | 동일 | 2025-09-08 | 2026-09-08 | E1 대비 개선 없음 |
| c_kr_fallen_nomajor | xkr_fallen3 · 전일 ≤−5% · 최대주주변경 0~7일 후 배제(dart_ok 필수) | 동일 | 2025-09-08 | 2026-09-08 | 배제군이 나머지보다 낮지 않음 |
| c_kr_insider_cluster | xkr_insider · 소유보고 증가(+) 7일 내 2인↑ | TP12/SL25/D10 | 2025-06-02 | 2026-09-08 | forward 30건 t<0 |
| c_kr_plan_buy | xkr_plan_buy · 거래계획(매수) 0~3일 | TP12/SL25/D10 | 2025-06-02 | 2026-09-08 | forward 20건 t<0 |
| c_kr_exright | xkr_exright · 권리락일 시가(기준일 −2거래일 신호) | TP20/SL10/D10 | 2025-09-08 | 2026-09-08 | forward 20건 t<0 |
| c_kr_buyback_start | xkr_buyback_start · 장내취득 시작일 시가 | TP12/SL25/D7 | 2025-09-08 | 2026-09-08 | 발표 다음날 대조군 대비 개선 없음 |
| c_us_earn_gap | xus_earn_gap · 어닝 반응일 갭 ≥+8% & 종가≥시가 | TP12/SL25/D10 | 2025-06-02 | 2026-09-08 | forward 30건 t<0 또는 xus_rise5 비어닝 급등 대비 증분 없음 |
| c_us_insider_cluster | xus_insider · Form 4 매수 7일 내 2인↑(제출일) | TP12/SL25/D10 | 2025-07-01 | 데이터 도착 후(2026Q2+ 수집기) | forward 30건 t<0 |
| c_us_volfirst | xus_volfirst · 최초 거래량 충격 | TP12/SL25/D7 | 2025-06-02 | 2026-09-08 | xus_volspike 반복 사건 대비 증분 ≤0 |
| c_kr_insider_k1 | xkr_insider · 군집 중 전일 거래대금 1위 1종목 | TP12/SL25/D10 | 2025-06-02 | 2026-09-08 | **사후 발견(백필 +2.88%, 306세션 t 5.3 — 전량 +0.36%와 대비)** → 다중비교 의심. forward 30건 t<0이면 기각 |
| c_us_insider_k1 | xus_insider · 군집 중 거래대금 1위 1종목 | TP12/SL25/D10 | 2025-07-01 | 수집기 후 | 백필 +1.35%(t 2.7). forward 30건 t<0이면 기각 |

주: K1 두 arm은 첫 백필 결과를 본 뒤 추가한 뷰다(09-07 23:40). 백필 수치는 근거가 아니라 가설이며 forward만 판정에 쓴다.

원장·수집기: `tools/dart_insider_ledger.py`(elestock 소유보고·거래계획 본문), `tools/dart_corp_action_terms.py`(자사주 기간·무상증자 기준일), `tools/us_earnings_dates_cache.py`(yfinance 발표일, 확정분만), `tools/edgar_form4_ledger.py`(SEC 분기 데이터셋). 특성 결합은 `tools/discovery_pools.py::event_features`(공시일 ≤ 신호일만, 원장 결측은 fail-closed).

## 2. 가상 북 밖 원장 — 패닉일 마감 진입 (`tools/us_panic_close_shadow.py`, `data/shadow/us_panic_close.jsonl`)

- 신호: 시장 급락일(하락 종목 비율 ≥65%) 15:40 ET 시점 ≤−5% & 전일 거래대금 ≥50M → 15:45 매수. ETF TQQQ/IWM/SPY 1주 행 동시 기록.
- 계약: TP20/SL25/D10, 비용 0.50%. 정산은 일봉.
- **백필 vs forward 정의 차이(명시)**: 백필은 breadth가 EOD 태그이고 후보가 "종가 ≤−1% & 전일 거래대금 ≥50M" 상위집합 근사(Alpaca SIP 분봉을 그 종목만 받음; Codex P0 반영으로 −3%→−1% 확장, 잔여 편향 = 15:40 ≤−5%인데 마감까지 +4%p↑ 되돌린 종목 누락). forward는 15:40 Alpaca iex 스냅샷 breadth·전 유니버스. 매 세션 `breadth_1540`과 `breadth_eod`를 함께 남겨 괴리를 본다.
- 09-07 폐기한 lookahead: ① 종가 등락률로 15:45 진입 종목을 고른 것 ② 자사주 창에 급락 뒤 공시를 포함한 것. 재발 금지.
- 반증: forward 패닉 세션 10개에서 오버나이트 세션 평균 ≤0, 또는 마감진입 D10 − 시가진입(C6) D10 증분 ≤0. 장기 프록시(2005~)에서 약세장 음수 → 국면 조건부·총 노출 한도.
- 운영: schtask `claudetrade_us_panic_close` 매일 04:35 KST → `live`(15:40 ET 대기) → `settle`.
- **실행 형태(09-08)**: forward 판정을 exec_form별로 낸다 — top3_dvol(전일 거래대금 상위 3, 15:40 정보만) · top1 · TQQQ/IWM/SPY 1주. 백필 top3 +5.17%(t 3.95)·TQQQ +4.19%(t 3.32). 캐너리 후보는 이 중 forward에서 남는 쪽.

## 3. B등급 사전등록 셀 (코드 변경 없음, forward 원장에서 사후 분해)

| 셀 | 정의 | 원장 | 판정 |
|---|---|---|---|
| KR 09:30 ① | 전일 ≤−5% 비바이오 & 09:30 체결강도 ≥120 | `kr_open_flow.jsonl`(09-08~) + 가상 북 meta.flow | forward 30건 세션 t, 4셀 다중비교 보정(Bonferroni 4) |
| KR 09:30 ② | 09:05→09:30 매수 총잔량 증가 & 가격 비하락 | 동일 | 동일 |
| KR 09:30 ③ | 시가 갭 되돌림 ≥50%(09:30가 ≥ 시가+(전일종가−시가)×0.5) | 동일 | 동일 |
| KR 09:30 ④ | 09:30 누적 거래대금 ≥ 전일 거래대금 20% | 동일 | 동일 |
| KR 장전 공시 | 07:30~08:59 판단 완료 공급계약≥30%·무상증자 → 09:00:30~09:20 시가 유예 진입(급등 8%·한도 재검사), 계약 kr_event_v1_preopen | **09-08 구현** — 레인 PREOPEN 단계, schtask 07:25, 유예 원장 `kr_event_preopen_fills.jsonl` | 30건 |
| NXT 저녁→다음날 시가 | NXT 단계 감지·판단 완료 공시 NX 매수(19:40 전) → 다음날 09:05 매도 | 공시 레인 NXT 원장 | 30건 |

## 4. 미착수 → 09-08 새벽 대부분 편입(launch report §7). 남은 것: N2(KIS WS 단일 연결), N5/N6 레인 연결(파서는 완료)

(원문 09-07 밤 기록)

- Codex N2 KR 매도 체결 흡수(WS 틱 수집기), N8 US 개장 15분 충격(분봉·호가 실시간), P12 KR 종가 동시호가 눌림(15:19 KIS 600종목 스냅): **장중 KIS 호출 예산·매도 감시 경합(Codex P1)** 해결 전 schtask 등록 금지 → 스크립트 스펙만.
- N3 선급금 계약·N5 긍정 정정·N6 잠정실적 흑자전환: 공시 레인 본문 파서 확장(장중 판단 후 호가 진입 규약 필요). 다음 작업.
- P13 KRX 투자경고: 엔드포인트 미확인.
- US Form 4 forward(2026Q2+): SEC 일일 인덱스 수집기 미작성 → c_us_insider_cluster는 백필 판정만.


## 5. 09-08 추가 사전등록 (forward 전용 원장)

| 전략 | 신호·계약 | 원장 | 반증 |
|---|---|---|---|
| N8 US 개장 15분 충격 | 09:30→09:45 ≤−3% & 직전 20세션 창 최저 미만 & 전일 거래대금 ≥50M → 09:46 ask, TP6/SL6/30분(10:16 bid) | `us_open_impact.jsonl` | 30건 세션 t<0 또는 ask→bid 전환 시 net≤0 |
| P12 KR 종가 동시호가 눌림 | 15:19→종가 ≤−2%(거래대금 상위 300+급락 풀) → 다음날 시가 → 다음날 15:19 | `kr_close_auction.jsonl` | 30건 세션 t<0 |
| P13 KRX 투자경고 해제 | 해제 다음 시가 D5 / 지정일 급락 회피 | `krx_market_alert.jsonl`(지정/해제 diff) | 20건 |
| US Form 4 군집 forward | 기존 c_us_insider_cluster·k1 — 일일 수집기로 forward 시작 | `us_insider_ledger.jsonl` | 기존 |
| N2 KR 매도 체결 흡수 | 09:05~09:15 매도체결 비중 ≥55% & 가격 ≥−0.5% & 총매수잔량 ≥90% 유지 & 체결강도<100 & 매수호가1 유지 → 09:16 매도호가1, TP12/SL25/D7 | `kr_absorption.jsonl` + 틱 `kr_ws_ticks/` | 30건 세션 t<0 또는 09:30 확인 진입(L3) 대비 증분 ≤0 |
| N6 잠정실적 흑자전환 | 영업이익 당기>0 & 전년동기≤0 & 매출 증가 → 판단 후 시세, TP8/SL4/EOD | 공시 레인(kind prelim_earnings) | 30건 |
| N5 공급계약 긍정 정정 | 정정전/후 금액 ≥+5% & 종료일 불변 → 계약 kr_event_v1_amend, 그 외 OBSERVE | 공시 레인 | 30건 |
| KR 공시 2단계(fast) | 본문 대기 중 제목·유동성·급등만으로 진입(계약 kr_event_v1_fast), 본문 확정 후 비ENTER면 즉시 청산 | `kr_event_fast.jsonl` + phantom | doc_reject 손익(속도의 비용) 합이 승격분 이익보다 크면 기각. FAST_ENABLED 기본 OFF |
