# 신규 전략 일괄 편입 — 백필·검증·쉐도우 운영 배선 리포트 (2026-09-07 밤 ~ 09-08 새벽)

운영자 지시: "그렇게 하고 모든 전략 다 붙이고 백필까지 다해서 검증까지 해서 리포트하고 쉐도우 운영하게 만들고 진행해" + "대시보드에도 추가하고".
후보 정본 `new_strategy_candidates_20260907.md`(23개) · 사전등록 `preregistration_event_family_20260907.md` · Codex 리뷰 반영 `codex_review_event_family_20260907.md`.

## 0. 결론 (등급표)

**붙인 것: A등급 10개 arm+2 K1 뷰+패닉 원장 1(백필·검증 완료) / B등급 3 사전등록(forward 대기) / C등급 8 미착수(사유 명시). 확정 0.**
백필 등급(세션 클러스터 t, 판정 아님): **후보(3조건 근사 통과) 4** — US Form 4 내부자 군집 +1.10%(163세션, t 2.97), 패닉일 마감 진입 급락주 +2.83%(55세션, t 3.48)·ETF +2.26%(t 3.62), KR 내부자 군집 거래대금 1위 +2.64%(298세션, t 4.79, **사후 발견**), US 내부자 K1 +1.35%(t 2.7) / **후보(일부) 4** — KR 자사주 공시 후 급락 +1.95%(t 1.99), 자사주 취득 개시일 +1.01%, KR 내부자 전량 +0.36%, US 최초 거래량 +0.38% / **백필 음수 3** — KR 거래계획 매수 −1.38%, 권리락 −0.50%(n 37), US 어닝 가격반응 PEAD −0.23% / 회피 규칙 1(최대주주변경 배제군 −3.9%, 풀 대비 개선 +0.06%p).
US Form 4 군집은 데이터가 2026-03(SEC 2026Q1)에 끊겨 H2가 3개월뿐이고 forward가 0이다 — 2026Q2+ 수집기 전엔 백필 후보에 머문다. K1 두 arm(KR t 4.79·US t 2.7)은 **백필 결과를 본 뒤 추가한 사후 뷰**라 forward만 판정에 쓴다.
패닉 마감 진입은 같은 (신호일, 종목) 짝에서 다음 시가 진입(C6)보다 +0.73%p/세션(t 1.83) — 증분은 약하고 오버나이트 +0.66%가 대부분. 전부 forward 30건(패닉은 10세션)에서 재확인 전엔 후보다.

## 1. 무엇을 붙였나 (등급 A/B/C)

| 등급 | 후보 | 붙인 형태 | 상태 |
|---|---|---|---|
| A | P3 자사주 공시 후 급락 (E1·E1b) | 가상 북 arm `c_kr_fallen_buyback30`·`c_kr_fallen_buyback_active` (xkr_fallen3 뷰, 필터 키 `feat_range`) | 백필 완료 |
| A | P14 최대주주변경 회피 (E2) | `c_kr_fallen_nomajor` (배제 필터 `feat_exclude_range`, `require_true: dart_ok`) | 백필 완료 |
| A | P4 KR 내부자 군집 (E3·E3-K1) | 풀 `xkr_insider`(elestock 소유보고 증가 7일 내 2인↑) → `c_kr_insider_cluster`·`c_kr_insider_k1` | 백필 완료 |
| A | P5 KR 거래계획 매수 (E4) | 풀 `xkr_plan_buy`(본문 "매수(+)" 파싱) → `c_kr_plan_buy` | 백필 완료(제도 2024-07, 표본 얇음) |
| A | P6 무상증자 권리락 (E5) | 풀 `xkr_exright`(본문 신주배정기준일 → 기준일 −2거래일 신호) → `c_kr_exright` TP20/SL10/D10 | 백필 완료 |
| A | Codex N4 자사주 취득 개시일 | 풀 `xkr_buyback_start`(본문 취득예상기간 시작일) → `c_kr_buyback_start` | 백필 완료 |
| A | P7 US 어닝 가격반응 PEAD (E6) | 풀 `xus_earn_gap`(yfinance 발표일 확정분·BMO/AMC 반응봉·갭≥+8%·종가≥시가) → `c_us_earn_gap` D10 | 백필 완료 |
| A | P8 US Form 4 내부자 군집 (E7·E7-K1) | 풀 `xus_insider`(SEC 분기 데이터셋 2025Q3~2026Q1, 코드 P, 제출일 기준) → `c_us_insider_cluster`·`c_us_insider_k1` | 백필 완료, forward는 2026Q2+ 수집기 후 |
| A | Codex N1 최초 거래량 충격 | 풀 `xus_volfirst`(volspike & 60봉 최대 & 직전 20봉 3배 없음, O(n) 롤링) → `c_us_volfirst` | 백필 완료 |
| A | P1/P2 패닉일 마감 진입(종목·ETF) | 가상 북 밖 원장 `tools/us_panic_close_shadow.py` + schtask `claudetrade_us_panic_close`(04:35 KST, 15:40 ET 대기·15:45 진입·일봉 정산) | 백필 완료(55 패닉 세션), forward 09-08 밤부터 |
| B | P11 09:30 4셀 · P9 장전 공시 · P10 NXT 다음날 시가 | 사전등록 §3(코드 변경 없음, forward 원장에서 사후 분해) | forward 대기 |
| C | Codex N2·N8·P12·N3·N5·N6·P13, US Form 4 forward | 미착수(사유: 장중 KIS 호출 예산·매도 감시 경합, 본문 파서 확장, 엔드포인트 미확인) | 사전등록 §4 |
| 관측 | P15 US 프리마켓 갭다운 | 08-01 실측상 배제군 → 미편입 | — |

코드 변경(기존 파일): `tools/discovery_pools.py`(이벤트 원장 로더·`event_features`·`event_pool_pass`·`volume_context`·풀 7종), `tools/virtual_books.py`(필터 키 3종·arm 12종·완전성 대사 범위), `dashboard/dashboard_server.py`(`/api/research` panic_close·ledgers + 카드 2종), `tests/test_discovery_pools.py`(+4 테스트). 신규 파일: `tools/dart_insider_ledger.py`, `tools/dart_corp_action_terms.py`, `tools/us_earnings_dates_cache.py`, `tools/edgar_form4_ledger.py`, `tools/us_panic_close_shadow.py`, `tools/event_family_report.py`.

## 2. 원장 (수집 결과)

| 원장 | 행 | 범위 | 출처 |
|---|---|---|---|
| `data/shadow/kr_insider_ledger.jsonl` | 22,430 | 2025-01~ (1,690 종목) | DART elestock (1회 호출/종목, `--refresh-days 1` 재조회) |
| `data/shadow/kr_insider_plan_ledger.jsonl` | 782 | 12개월 | DART 지분공시(D) 거래계획보고서 본문 (매수 143·매도 168·혼합 14·미상 169) |
| `data/shadow/kr_dart_terms.jsonl` | 347 | 2025-09~ | DART 본문: 자사주 288(장내 255)·무상증자 55(기준일 파싱 52/55) |
| `data/analysis/us_earnings_dates.jsonl` | 60,946 | 종목당 최대 40행(2016~) | yfinance `earnings_dates`(확정분만 사용) |
| `data/shadow/us_insider_ledger.jsonl` | 2,452 | 2025-07~2026-03 | SEC Form 345 데이터셋(코드 P·취득) |
| `data/shadow/us_panic_close.jsonl` | 4,616(거래 4,561·정산 4,501·세션 55) | 패닉 55세션(2025-06~2026-09) | Alpaca SIP 1분봉(백필) / iex 스냅샷(forward) |

## 3. 검증 결과 (백필 = 후보 생성용, 판정 아님; 세션 클러스터 t)

| arm | n | 세션 | 세션 평균 net | 세션 t | 상위2 제외 | H1 / H2 | K=1(필터 후 dvol 1위) | TP/SL | 등급 |
|---|---:|---:|---:|---:|---:|---|---|---|---|
| c_us_insider_cluster | 688 | 163 | +1.10% | 2.97 | +0.97 | +1.67(417) / +0.19(271) | +1.35(163) | 70/2 | 후보(3/3) |
| c_us_insider_k1 | 163 | 163 | +1.35% | 2.70 | +1.22 | +1.89 / +0.49 | — | 25/1 | 후보(3/3) |
| c_kr_insider_k1 (사후 발견) | 298 | 298 | +2.64% | 4.79 | +2.58 | +1.54(141) / +3.63(157) | — | 115/2 | 후보(3/3)·다중비교 의심 |
| c_kr_insider_cluster (전량) | 3,652 | 306 | +0.36% | 1.05 | +0.28 | +0.47 / +0.26 | +2.88(306, t 5.3) | 1369/145 | 후보(일부) |
| c_kr_fallen_buyback30 | 160 | 83 | +1.95% | 1.99 | +1.71 | +4.34(13) / +1.66(147) | +2.05(83) | 67/2 | 후보(일부) |
| c_kr_fallen_buyback_active | 278 | 106 | +0.57% | 0.60 | +0.35 | +4.26(20) / −0.04(258) | +0.72 | 110/5 | 후보(일부)·E1 대비 개선 없음 |
| c_kr_buyback_start (N4) | 99 | 73 | +1.01% | 1.07 | +0.70 | +1.63(21) / +0.79(78) | +1.18 | 28/0 | 후보(일부) |
| c_us_volfirst (N1) | 964 | 208 | +0.38% | 0.96 | +0.28 | +0.66(428) / +0.22(536) | +0.21 | 105/3 | 후보(일부) |
| c_kr_fallen_nomajor (회피) | 17,750 | 241 | −0.67% | −1.79 | −0.77 | +0.42 / −1.17 | −0.03 | 7205/898 | 풀 기저(2025-09-08~ ≤−5% 전량) −0.73%(t −1.95) → 배제 효과 +0.06%p, 배제군 자체 −3.9%(t −2.6) |
| c_kr_plan_buy (E4) | 98 | 82 | −1.38% | −1.03 | −1.70 | −2.60(32) / −0.90(66) | −1.43 | 32/6 | 백필 음수 |
| c_kr_exright (E5) | 37 | 33 | −0.50% | −0.19 | −1.80 | +2.53(11) / −1.63(26) | +0.47 | 13/20 | 백필 음수(SL10 발동 20/37) |
| c_us_earn_gap (E6) | 415 | 137 | −0.23% | −0.40 | −0.41 | −0.18(191) / −0.27(224) | +0.23 | 92/3 | 백필 음수 — 어닝 갭 유지 후 D10 드리프트 없음(우리 유니버스·비용 0.5%) |

**패닉일 마감 진입(가상 북 밖, TP20/SL25/D10, 갭 SL 시가 체결, 후보 상위집합 종가 ≤−1%)**

| 대상 | n | 세션 | 세션 평균 net | 세션 t | 오버나이트 | H1 / H2 (세션 t) | TP/SL |
|---|---:|---:|---:|---:|---:|---|---|
| 급락주(15:40 ≤−5%) | 4,339 | 55 | +2.83% | 3.48 | +0.66% | +2.34(2.21) / +3.52(2.79) | 936/180 |
| ETF 3종 | 162 | 54 | +2.26% | 3.62 | +0.30% | +2.32(3.41) / +2.18(1.86) | 6/0 |
| TQQQ / IWM / SPY | 54 each | — | +4.19 / +1.82 / +0.79 | — | — | 최악 −13.6 / −5.2 / −3.8 | — |
| 짝 비교 vs C6(다음 시가) | 3,785쌍 | 55 | 마감 +2.80 vs 시가 +1.67 | 증분 t 1.83 | 증분 +0.73%p/세션 | — | — |

읽는 법: 세션 t는 세션 등가중 평균의 t(종목 클러스터 미반영). ★4조건 정식 판정은 `discovery_breakdown`(이중 클러스터·인접 칸)으로 forward 축적 후. 백필 원장은 생존편향(현 캐시 종목)·시가 체결 가정·DART 2025-09-08~ 창 한계가 있다. 오늘 밤 lookahead 2건을 폐기했고(종가 필터·미래 공시), Codex P0 2건을 반영해 재계산했다.

## 4. 운영 배선

- 가상 북 체인(07:20 US·16:20 KR)이 새 풀·arm을 자동 갱신. 탐색 풀 생성 205.8s(기존 182s 대비 +24s), 전체 실행 약 4분.
- 원장 갱신: 내부자·거래계획·자사주기간·어닝일은 **일일 배치 미등록** → 운영자 확인 항목(§6). 지금은 수동 실행. DART 키를 공시 레인과 공유하므로 야간(20:30 이후) 실행 권장.
- 패닉 마감 진입: schtask `claudetrade_us_panic_close` 등록(화~토 04:35 KST, 3시간 제한, 다음 실행 09-08 04:35).
- 대시보드 `/virtual`: 연구 카드 "패닉일 마감 진입"·"신규 전략 원장", 탐색 원장 표에 c_* arm 자동 표시. 대시보드 프로세스만 재시작(PID 30848→552, 봇 3624 불변).
- 격리: 새 arm 전부 `candidate=True`(승격 게이트·유령·텔레그램 상세·실주문 브리지 제외). 실매수 스위치 변경 없음.

## 5. Codex 리뷰 반영 (P0 3·P1 13) — `codex_review_event_family_20260907.md`

수정 8건(패닉 후보 상위집합 −3%→−1%, live 진입가 raw→CSV 변환, 갭 SL/TP 시가 체결, ETF 행 항상 기록, 조기 폐장·늦은 실행 스킵, 멱등, 리포트 K=1 필터 후 순위, 짝 비교, 내부자 원장 재조회). 한계 기록 4건(dart_ok 종목 커버리지, 권리락 달력 근사, iex 편향, Form 4 공동 보고자).

## 6. 운영자 확인 항목

1. 원장 일일 갱신 schtask 등록 여부(내부자 elestock 1,690회/일 + 거래계획·자사주기간 본문 + 어닝일 주 1회). DART 일일 한도와 공시 레인 공유.
2. 장전 공시(폴링 08:50→07:30) env 변경.
3. C등급 수집기(KIS 장중 호출) — Codex P1 호출 예산 해결 후.
4. K1 arm 2종은 백필 결과를 본 뒤 추가한 뷰(사전등록 문서에 명시). forward만 판정.
5. 잔여 결함: `event_family_report.py`가 배제 arm(c_kr_fallen_nomajor)을 구분하지 못해 "기각(음수)"로 오라벨 — 풀 기저 대비 증분으로 읽어야 함(다음 수정).
6. 이 밤 체인을 세 번 수동 실행(신규 arm·K1·거래계획 순)해 텔레그램 요약 3건이 나갔다. 데이터 원장(`data/shadow/*`)은 untracked, `discovery_breakdown_20260907.md`는 체인이 갱신하는 파일이라 커밋에서 제외.


## 7. 09-08 새벽 추가 편입 (운영자 "미국 휴장이니 다 준비해")

| 항목 | 붙인 형태 | 운영 | 첫 실행 |
|---|---|---|---|
| P9 장전 공시(07:30~08:59) | 공시 레인 PREOPEN 단계: 감지·본문·판단은 장전, 진입은 09:00:30~09:20 시가 유예(`fill_preopen_entries` — 시가/전일종가 급등 8%·max_open·max_new_per_day 재검사, 유예 원장 `kr_event_preopen_fills.jsonl`, 계약 `kr_event_v1_preopen`). 코드 상수(env 아님) | schtask `claudetrade_kr_event_lane` 08:45→**07:25**, PT12H→PT13H | 09-08 07:25 |
| Codex N8 US 개장 15분 충격 | `tools/us_open_impact_collector.py` — 신호는 가격만(09:30→09:45 ≤−3% & 직전 20세션 창 최저 미만, sip 백필), 09:46 iex ask 진입, TP6/SL6/30분, 10:32 이후 sip 정산·거래대금 특성 | schtask `claudetrade_us_open_impact` 화~토 22:40 KST, PT2H30M | 09-08 22:40 |
| P12 KR 종가 동시호가 눌림 | `tools/kr_close_auction_collector.py` — **네이버 시세**(KIS 장중 호출 없음 → Codex P1 호출 예산 우려 소멸), 15:19/15:31 스냅, 15:19→종가 ≤−2% 신호 → 다음날 시가 진입·다음날 15:19 청산. 유니버스 전일 거래대금 상위 300 + 급락 풀(331종목) | schtask `claudetrade_kr_close_auction` 주중 15:17, PT25M | 09-08 15:17 |
| P13 KRX 투자경고(B등급으로 정정 — 백필 불가) | `tools/krx_market_alert_collector.py` — 네이버 투자경보 3종 일일 스냅 → 지정/해제 diff 원장(KRX data 포털은 400) | 원장 래퍼에 포함 | 09-08 21:05 |
| US Form 4 forward | `tools/edgar_form4_daily.py` — SEC 일일 인덱스 → 유니버스 CIK Form 4 XML(코드 P) → 같은 원장(멱등, 보고자 전원 저장). 2026-04-01~ 공백 백필 중 | 원장 래퍼에 포함 | 09-08 21:05 |
| 원장 일일 갱신 | `tools/refresh_event_ledgers.py` — 내부자 elestock(refresh 1일)·거래계획·자사주기간/권리락·Form 4·KRX 경보·어닝일(7일)·패닉 정산, 단계별 try | schtask `claudetrade_event_ledgers` 주중 21:05, PT1H30M | 09-08 21:05 |
| N6 잠정실적 흑자전환 · N5 공급계약 긍정 정정 | `runtime/kr_event_lane.py` 오프라인 파서 `parse_provisional_results`·`contract_amendment_diff` + 테스트(레인 판단 경로 미연결 — 장중 판단 후 호가 진입 규약은 후속) | — | — |
| 리포트 도구 | 배제 arm은 "같은 풀·기본 필터 기저 대비 증분"으로 출력(c_kr_fallen_nomajor: 기저 −0.73% 대비 +0.06%p) | — | — |
| 대시보드 `/virtual` | 표 제목 어긋남 수리: 전역 `th{text-align:left}`가 페이지의 우측 정렬을 덮어써 숫자 열 제목이 왼쪽으로 밀림 → `.vb-wrap th{text-align:right}` 페이지 범위 CSS. 대시보드 PID 29668 재시작 | — | 즉시 |

**미착수(사유)**: Codex N2 KR 매도 체결 흡수 — KIS WS 단일 연결 제약(봇이 점유) + 틱 수집기 설계 필요. N5/N6는 파서까지(레인 연결은 장중 호가 진입 규약 후).
**등급 정정**: P13은 C(수집기)→B(forward 전용, 백필 불가). US Form 4 군집은 forward 수집기 가동으로 "수집기 후"→09-08부터 forward.


## 8. 09-08 새벽 3차 — 미착수 0 (운영자 "완료하지 멈추지 말고… 커밋·푸시·재시작")

| 항목 | 붙인 형태 | 운영 |
|---|---|---|
| Codex N2 KR 매도 체결 흡수 | **봇 WS 관측 전용 구독**: `kis_api.route_kr_tick`(순수 함수)로 관측 종목 틱을 매매 경로(on_tick·price_cache·risk·무음 카운터)에서 분리해 `runtime/ws_tick_ledger.tick_sink`(원시 문자열 버퍼, 09:40 전만, 200행/2초 flush)로만 보낸다. `trading_bot._start_ws_for_market(KR)`이 `state/ws_observe_kr.json`(오늘 날짜만, `tools/ws_observe_list.py` 08:40 생성)을 읽어 남는 자리(41−매매 구독, 최대 20)만큼 관측 구독. 파일 없음·날짜 불일치·오류 → 관측 0. 무음 재기동도 같은 함수를 타므로 승계. 판정은 봇 밖 `tools/kr_absorption_shadow.py`(09:25): 09:05~09:15 매도체결 비중 ≥55%·가격 비하락·총매수잔량 유지·체결강도<100·매수호가1 유지 → 09:16 매도호가1 진입, TP12/SL25/D7, 21:05 래퍼 정산 | schtask `claudetrade_ws_observe_list` 08:40 · `claudetrade_kr_absorption` 09:25. 매매 경로 무변경 근거: `tests/test_ws_observe_routing.py`(라우팅·관측 목록 분리·버퍼 창·날짜 가드) |
| N6 잠정실적 흑자전환 | 레인 연결: `ENTER_KINDS`+`prelim_earnings`, 본문 `parse_provisional_results`, `decide()`는 영업이익 부호 전환 & 매출 증가만 ENTER(파싱 실패 SKIP), 출구 TP8/SL4/EOD. 3시점 관측 대상 추가 | 07:25 레인부터 |
| N5 공급계약 긍정 정정 | 레인 연결: 정정 공급계약은 본문 "정정항목 정정전/정정후" 표를 `parse_amendment_text`로 읽어 금액 증액 ≥5% & 종료일·상대 불변만 계약 `kr_event_v1_amend`로 ENTER 판단, 그 외는 OBSERVE(진입 없음)+diff 기록. 09-08 실측 문서(기간 연장 정정) → unknown/OBSERVE | 07:25 레인부터 |
| 대시보드 | `/virtual` 연구 카드 "2차 편입 forward 원장": 장전 유예·종가 동시호가·개장 충격·KRX 경보·N2(관측 목록·틱 원장·흡수) | 스택 재시작으로 반영 |

**오늘 09:00 확인(08-19 그렙 4종 + 관측)**: `KR 관측 구독 n종목` 로그(없으면 08:40 생산자 미실행 또는 날짜 불일치), `구독 응답 rt_cd`, `끊김`, `[WS silence]`, `[WS silence restart]`. 관측 구독이 붙은 첫 세션이라 무음 재발 시 관측 20종목부터 의심.


## 9. 09-08 새벽 4차 — "다 해봐, 전부 쉐도우" 6항목 (커밋·푸시·재시작 포함)

| # | 항목 | 결과 |
|---|---|---|
| 1 | Codex P0(실주문 경로) | **코드 완료 7·계약 유지 1·운영자 결정 2.** KR 브리지: 입력 계약 격리(`runtime/order_input_guard.py`, x*/c_*/SHADOW_ONLY 거절), 주문 전 브로커 동기화·신뢰 검사(실제 제출 경로에서만 — REHEARSAL 쉐도우 기록 계약 불변, 테스트로 고정), 선정 데이터 결측 차단(신호 종가·할인 None), 체결가 사전 게이트(스프레드 ≥0.5%·시가 괴리 ≥3% 대기), **UNKNOWN 격리**(응답 유실·주문번호 없음 → ORDER_UNKNOWN 영속화 + 세션 즉시 중단, `_v2_record_order_unknown`), 세션 접수 원장 `kr_fallen_submit_ledger.jsonl`. US 브리지: 입력 격리 + 거래대금 결측 시 `data_missing` 플래그·에러 로그(fail-open은 08-20 계약 → 운영자 결정). 봇 본체: 주문 의도 선기록 `state/order_intents.jsonl`(전송 전 intent → 응답 후 submitted), UNKNOWN 종목 당일 재제출 차단 `state/order_unknown_block.json`. 계약 유지: US 하루 1건 우회(08-22 A안). 운영자 결정: 밴드 결측 fail-closed 전환, KR 지정가 전환. 미구현: 증액 승인 강제·출구 감시 경보(integrity_check 검사 추가는 후속) |
| 2 | forward 판정 자동화 | `tools/forward_gate_watch.py`(래퍼 21:05): arm별 forward 정산·세션 t → ACCUMULATING/WATCH/CANDIDATE_STRONG/REFUTED, 패닉 오버나이트 10세션, ★4조건 셀 신규 출현. **상태 변화 때만** 텔레그램. 대시보드 카드 "forward 판정 자동화" |
| 3 | 공시 레인 2단계 판단 | `kr_event_v1_fast`: 본문 대기(PENDING) 시점에 제목·유동성·급등만으로 유령, 본문 확정 후 ENTER면 승격 종료·아니면 즉시 청산(doc_reject = 속도의 비용), 하루 3건 별도, 원장 `kr_event_fast.jsonl`. **`FAST_ENABLED=False`(코드 상수) — 오늘 07:25 첫 PREOPEN 세션을 한 번 관찰한 뒤 내일 ON 검토** |
| 4 | 패닉 실행 형태 | 원장에 `prev_dvol_usd` 소급, 리포트 exec_form 분해(백필 55세션): **top3_dvol +5.17%(t 3.95, 최악 −45)** · top1 +4.69%(t 2.79) · TQQQ +4.19%(t 3.32, 최악 −13.6) · IWM +1.82%(t 4.13) · SPY +0.79%. 캐너리 후보 = top3(3×5만원) 또는 TQQQ 1주 — forward에서 남는 쪽 |
| 5 | 내부자 본문 구분 | `tools/dart_insider_reason.py`(하루 1,500건, 최근부터, 래퍼 단계): 첫 1,036건 = 장내매수 55%·미상 12%·유상증자 8%·옵션 4%. `discovery_pools`는 사유 원장이 덮는 보고서는 장내매수만 군집에 센다(`insider_reason_coverage`). US 어닝 PIT는 2시즌 전 — 손대지 않음 |
| 6 | 자본 산수·캐너리 | `config/canary_policy.json`(**제안, 미배선**: 동시 1·5만원·손실선 −33,000·CANDIDATE_STRONG만·통과 순서 큐·K1 뷰 제외) + `tools/canary_policy_check.py`(읽기 전용, 지금 ON 가능 0/20) |

테스트: `tests/test_kr_fallen_order_bridge.py`(+3: UNKNOWN 세션 중단·REHEARSAL 불변·신뢰 차단), 입력 가드, fast precheck, 기존 실패 2건(`_open` 픽스처가 실시계 의존 → t0 주입) 수리.
