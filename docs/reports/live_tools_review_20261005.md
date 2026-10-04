# 실주문 도구 4종 + 격리 연결부 리뷰 (2026-10-05)

대상: `tools/kr_buyback_canary.py` · `tools/cash_parking.py` · `tools/kr_panic_etf_micro.py` · `tools/us_earnings_t1_live.py`(+ `us_earnings_t1_shadow.py` `_candidates`·`_finnhub_actual`) · `risk_manager.py` 격리부 · `trading_bot.py` 귀속 복원/미체결 대조.
방식: 코드 정독 + 오프라인 시뮬레이션(브로커 호출 없음) + 런타임 상태 파일 읽기. 코드·설정 변경 없음.

증거 등급: **[실측]** 도구로 실행·파일로 확인 / **[코드]** 코드 경로 추론 / **[확인 필요]** 외부 사실(KRX·KIS 규정) 미검증.

## 0. 다음 발화 전 조치 (시간순)

| 시각(KST) | 작업 | 상태 |
|---|---|---|
| **오늘 22:45** | `claudetrade_parking_us_once` (SGOV 지정가 매수) | **보류 권고.** `data/exchange_cache.json`에 `"SGOV": "NYSE"`로 캐시돼 있다 [실측]. 같은 NYSE Arca 상장 ETF인 SPY·IWM·GLD는 `kis_api._US_EXCHANGE_MAP`에서 `AMEX`로 하드코딩돼 있다(kis_api.py:3767). KIS가 SGOV를 어느 EXCD로 서비스하는지 [확인 필요] — WMT 주석(kis_api.py:3743)과 같은 방법(KIS 현재가 조회 → `rsym` 접두 확인)으로 확인 후 진행. 틀리면 주문 거부(손실 없음, 기능 실패). H1(원장 덮어쓰기)은 park 1회 → 다음날 16:20 reconcile 1회 구조에선 터지지 않는다 — 단발 실행은 H1로 막히지 않고, **반복 실행으로 바꾸는 것만 막힌다.** |
| 10-06 08:40 | canary 매수 | 정상 경로. 08:40 장전 시장가 매수가 시가 체결되는 것은 10-01 원장으로 [실측] (030000 `fill_price 18760 == official_open 18760`). |
| 10-06 09:30 | `parking_kr_once` | H2 해당. 09:00 canary 체결분이 `orderable_cash`(=예수금 `dnca_tot_amt`)에 반영되지 않은 채 파킹 수량을 잡는다. 현 잔고(예수금 979,270 / D+2 885,467, 유지 65만)로는 거부까진 안 가지만 유지 현금이 canary 금액만큼 잠식된다. 운영자 선택: (a) canary 매수일엔 파킹 생략, (b) canary 체결 확인 후 D+2 기준으로 수동 실행. |
| 10-06 15:20:30 | panic 매수(신호 시) | 이 계좌에서 **종가 동시호가 시장가 매수는 아직 미실측.** M6(증거금 버퍼) 참고. |
| 10-08 08:41 | canary 첫 매도(030000, 5번째 세션) | **장전 시장가 매도는 미실측.** 매도 체결 후 16:10 reconcile 결과를 확인할 것. |

수정 순서 주의: `_bot_has_isolation`(canary·parking·panic)은 봇 `started_at` < `risk_manager.py`/`trading_bot.py` mtime이면 매수를 거부한다. 아래 수정 중 두 파일을 건드리는 것은 **봇 재시작 전까지 세 도구의 매수를 전부 fail-closed**시킨다(T1은 이 검사가 없음, 매도는 canary·T1은 ENABLED만, panic 매도는 무조건). 편집 → 재시작 → 각 도구 `status`로 `봇격리반영` 확인 순서로 진행.

## 1. High

### H1. cash_parking 원장 — 보유수량 소실·잘못된 CLOSED [실측]
- `cash_parking.py:176-177` park가 매번 `qty_ordered`만 가진 **새 position 행**을 쓴다 → 이전 행의 `filled_qty`가 최신 행에서 사라진다.
- `cash_parking.py:222-223` reconcile은 루프 전 스냅샷 `pos`를 기준으로 `held = p.filled_qty ± filled`를 계산하고, 같은 실행 안에서 갱신하지 않는다.
- `cash_parking.py:217-219` "아직 체결 대기" 가드가 없다(다른 3개 도구는 `filled==0 and 오늘 and 16시 전`이면 건너뜀). 체결 0으로 fill 행을 쓰면 그 주문번호는 `done`이 되어 다시 확인되지 않는다.
- 시뮬레이션(브로커 없음, scratchpad): 1일차 100주 체결 → 2일차 50주 미체결 reconcile 결과 `status: CLOSED, filled_qty: 0`, `cash_parking_manifest().open_tickers = {}`. 같은 실행에서 매수 10 + 매도 30 → `filled_qty 70`(정답 80).
- 피해: 실제 보유가 원장에서 사라진다 → `unpark`가 보유 전량을 못 판다, 매니페스트에서 티커가 빠진다. 봇은 이미 붙인 `source_strategy`를 포지션 파일에 유지하므로 즉시 매도되진 않지만, 포지션이 재주입되는 경우(포지션 파일 유실·브로커 일시 누락 후 재주입) `broker_sync`·`max_hold 1` 일반 포지션이 되어 loss_cap 등 일반 청산 대상이 된다.
- 최소 수정: position 행은 이전 행에 병합(`{**prev, ...}`)해서 쓰고 `filled_qty`를 보존한다. reconcile 루프 안에서 `pos[ticker]`를 즉시 갱신한다. 다른 도구와 같은 "오늘·장마감 전·체결 0이면 건너뜀" 가드를 넣는다(US는 ET 날짜 기준). park에 세션 멱등 키(오늘 같은 market buy 주문 있으면 종료)를 넣는다.

### H2. KR `orderable_cash`가 미결제 매수를 빼지 않는다 [실측]
- `kis_api.py:2855-2864` TTTC8434R output2에는 `ord_psbl_cash` 계열 필드가 없어 `orderable_cash`가 `dnca_tot_amt`(예수금)로 떨어진다.
- 실측: `state/live_broker_truth_snapshot.json`(10-05 00:41 KST) KR `cash = orderable_cash = 979,270`, `d1/d2_settlement_krw = 885,467`. 차이 93,803원 = 10-01 canary 매수 93,800 + 수수료. 즉 결제 전 매수 금액이 주문가능현금에 남아 있다. 미체결 매수 주문도 빠지지 않는다 [코드].
- 영향: canary(`kr_buyback_canary.py:201`)·panic(`kr_panic_etf_micro.py:157`)·parking(`cash_parking.py:162`) 세 도구가 모두 이 값을 쓴다. 특히 parking은 "(현금 − 유지액) 전부"를 사므로 D+2 결제 전에 다시 돌면 같은 현금을 두 번 파킹한다 → 주문 거부, 계좌가 미수 허용이면 미수 발생 → 반대매매 위험(계좌 미수 설정은 [확인 필요]). 현재 파킹 스케줄은 단발이라 즉시 위험은 낮지만 반복으로 바꾸면 바로 걸린다.
- 최소 수정: 도구 쪽에서 `min(cash, d1_settlement_krw, d2_settlement_krw)`를 쓰거나, KIS 매수가능조회(inquire-psbl-order)를 쓴다. 미체결 매수 금액도 차감.

### H3. `place_order` 예외 시 원장 기록이 없다 — 고아 포지션·중복 주문 [코드]
- 4개 도구 모두 `place_order` 호출 후에만 order/position 행을 쓴다(canary :210-216, parking :171-177 / :198-201, panic :160-165 / :181-183, T1 :169-175 / :203-206). `kis_api._place_order_kr/us`는 HTTP 500 후 브로커 truth 조회 실패·재시도 실패 시 **예외를 던진다**(kis_api.py:3712-3738, 3872-3898) — 이때 주문이 실제 접수됐는지 모른다.
- 결과: (1) 접수·체결됐으면 원장에 없으므로 매니페스트에 티커가 없다 → 봇이 `broker_sync` 일반 포지션으로 주입 → loss_cap 등 일반 청산 대상. 주문번호도 매니페스트에 없어 `broker_only_open_order`(trade_affecting)로 잡힌다. (2) 멱등 검사가 "오늘 order 행 존재"라서 재실행하면 같은 종목을 다시 산다. 루프 중간 예외면 뒤 계획도 버려진다.
- 최소 수정: 주문 전에 `kind: order, status: intent`와 OPEN position 행을 먼저 쓰고(매니페스트가 즉시 티커를 보호), 예외는 잡아서 `success: "unknown"`으로 기록한다. reconcile은 unknown 주문을 `inquire_daily_ccld_kr`/`inquire_ccnl_us`의 티커·시각 매칭으로 해소한다. 멱등 검사는 intent 행도 센다.

## 2. Medium

- **M1. canary 손실한도 우회** [코드] `kr_buyback_canary.py:77-78` `_realized_pnl`이 티커별 **마지막** position 행만 합산한다. 같은 종목이 나중에 다시 신호를 내 재진입하면 새 OPEN 행이 CLOSED 행(손익 포함)을 덮어 누적 손실에서 빠진다. 수정: panic처럼 별도 `kind: close` 행을 쌓고 그 합으로 계산.
- **M2. 부분 매도 체결 → CLOSED, 잔여 고아** [코드] canary :298-303, T1 :241-244, panic :215-219 모두 `filled > 0`이면 전량 CLOSED. 하한가 잠김 등으로 일부만 팔리면 잔여 주식이 원장·매니페스트에서 빠진다. 수정: `filled < 보유`면 `filled_qty -= filled`, OPEN 유지.
- **M3. 매도가 ENABLED에 묶여 있다** [코드] canary `cmd_sell` :233-236, T1 `cmd_sell` :181-184. 신규 매수를 끄려고 플래그를 false로 바꾸면 만기 청산도 멈추고, 봇은 격리 때문에 팔지 않는다 → 청산 주체 없음. 수정: 매도·reconcile은 플래그와 무관하게 돌리고(panic처럼), 매수만 게이트.
- **M4. 청산이 reconcile 성공에 의존** [코드] panic `cmd_sell` :173은 `filled_qty`가 있어야 판다. 16:12 reconcile이 실패하면(`get_order_fill_kr`가 내부 예외를 삼키고 None) 다음날 아침 매도가 "대상 없음"으로 조용히 건너뛰어 1박 계약이 2박이 된다. T1 reconcile은 `yf.download`(:228)가 try 밖이고 `_append(out)`이 루프 끝 1회라 yfinance 한 번 실패로 전체 체결 기록이 안 남는다 → `filled_qty` 미설정 → 20세션 만기 매도 불가. 수정: 매도 전에 해당 주문만 inline reconcile 또는 브로커 잔고 수량으로 대체, T1 공식시가 조회를 try로 감싸기.
- **M5. T1 만기 계산이 yfinance에 의존** [코드] `us_earnings_t1_live.py:112-117` IWM 다운로드 실패 → 예외로 그날 매도 전체 중단, 빈 결과 → `n = 1`로 조용히 미청산. 수정: 실패/빈 결과면 경고 후 종료하거나 거래소 캘린더(pandas_market_calendars 등) 사용.
- **M6. panic 시장가 매수 증거금 버퍼 1.3** [확인 필요] `kr_panic_etf_micro.py:158`은 15:19 가격 × 1.3으로 현금을 확인한다. KIS 시장가 매수 증거금이 상한가 기준이라면 기준은 **전일 종가** × (1+가격제한폭)이다. 패닉일은 정의상 전일 대비 하락이라 15:19 가격 × 1.3보다 크고, 233740이 레버리지 ETF라 가격제한폭이 ±30%보다 넓다면(배율 적용 여부 [확인 필요]) 차이가 더 커진다. 현금이 빠듯할 때 거부(손실 없음, 신호 누락). 수정: 전일 종가 × 실제 가격제한폭으로 계산하거나 KIS 매수가능조회 사용.
- **M7. cash_parking unpark** [코드] `cash_parking.py:191` 환율 1400 하드코딩. `--krw` 기본 0이면 `max(1, …)`로 1주를 판다(:192). reconcile 전이면 `qty_ordered`로 대체해 실보유보다 많이 팔 수 있다(거부). US unpark 대금은 USD로 남아 KR 레인 현금이 되지 않는다. 수정: 환율은 `get_balance` US의 `kis_exchange_rate`, `--krw` 필수·양수 검사, 보유 수량은 브로커 잔고로 확인.
- **M8. SGOV 거래소 코드** — §0 참조 [확인 필요].

## 3. Low

- **L1.** `trading_bot.py:23512-23514` 미체결 대조에서 KR 장부 주문번호와 US 장부 주문번호를 시장 구분 없이 합친다. KR/US 주문번호 공간이 겹치면 진짜 미상 주문을 숨긴다. 수정: parking은 `market` 필드로 걸러 시장별로 합집합.
- **L2.** `trading_bot.py:25287-25314` 기존 `broker_sync` 포지션의 PathB 귀속 복구가 `source_strategy`를 격리 여부와 무관하게 덮어쓴다(활성 PathB run이 같은 티커·수량·가격으로 1건 매칭될 때만 — 확률 낮음). 수정: `isolated_strategy_source(pos)`면 건너뜀. 같은 계열로 REVERSE(`trading_bot.py:40508-40513`)는 격리 소스가 아니라 `strategy == "broker_sync"`로 우연히 제외된다 — 격리 소스 검사 추가 권고.
- **L3.** T1 도구는 `_bot_has_isolation`(실행 중인 봇의 코드 반영 확인)이 없다(`us_earnings_t1_live.py:125-126`). 나머지 3개와 맞출 것.
- **L4.** `kis_api.get_order_fill_kr` :3071-3078 주문번호 미일치 시 같은 티커 행, 그것도 없으면 **아무 행**(`rows[0]`)을 돌려준다. ODNO로 조회하므로 실제로는 드물지만, 도구 reconcile이 다른 주문의 체결을 기록할 수 있다. 도구 쪽에서 `f["order_no"]` 일치를 확인 권고.
- **L5.** canary·panic·T1 세션 계산은 KR/US 공휴일(평일)에도 태스크가 돈다. 공휴일 주문은 거부되고 `success: false` 행이 남은 뒤 다음 세션에 재시도된다 — 손실 없음, 로그 잡음.
- **L6.** canary 매수는 08:30 AM 스캔(쉐도우 원장 기록)과, panic 매수는 15:16 쉐도우(15:19 판정 후 파일 rewrite)와 같은 파일을 짧은 간격으로 읽는다. 쓰기 중 읽으면 JSON 파싱 예외로 그날 매수 누락(fail-safe).

## 4. 확인한 정상 동작

- 격리 귀속: 10-01 canary 030000이 봇 포지션 파일에서 `source_strategy/exit_owner = kr_buyback_canary`, `exit_policy = isolated_strategy`로 붙어 있다 [실측]. `trading_bot.py:24027-24036` parking/panic 루프의 `break`는 출처가 이미 있으면 바로 빠지고, 첫 매칭 후 다음 반복에서 빠진다 — 정상 [코드]. KR 티커는 브로커 `pdno`·원장 모두 6자리 문자열(쉐도우 `stock_code` "093050" 등)이라 선행 0 문제 없음 [실측].
- 매니페스트 캐시: `_T1_MANIFEST_CACHE`는 `path is None`(T1 기본 경로)일 때만 쓰여 다른 장부와 섞이지 않는다 [코드].
- 봇 매도 경로 격리: `risk_manager._isolated_strategy_exit_candidate`가 신규 4개 소스를 조기 반환(:1459-1464), `get_exit_candidates`가 일반 청산(loss_cap 포함) 전에 건너뜀(:1538-1542), 고정만기 청산 집합(`trading_bot.py:27019`)에 신규 소스 없음, `trading_bot.py:27161/27480/27792/42174`에서 격리 포지션 필터. stale 제거(:25239-25247)는 브로커 0주 확정 시 장부에서만 지우고 매도하지 않는다. `test_profit_strategy_risk_isolation`은 frozenset 전체를 순회하므로 신규 소스도 그 테스트가 검증하는 범위에 포함된다. **4만 줄 전체의 매도 생성기를 다 읽진 않았다** — 일괄 청산(liquidate-all) 경로는 grep으로 **찾지 못했다**(없다고 단정하지 않음).
- 타이밍: KRX 장 시작 동시호가(08:30~09:00)·장 마감 동시호가(15:20~15:30) 모두 시장가 접수 가능하고 단일가로 체결된다(KRX 규정 — 장전 매수는 실측, 장마감 매수·장전 매도는 이 계좌에서 미실측). reconcile 16:10/16:12는 15:30 체결 이후. T1 07:40 reconcile은 ET 날짜가 아직 주문일이라 체결 0이면 다음날로 넘기는 가드가 맞게 동작한다. US 파킹 22:45 KST = 09:45 EDT로 `market_open` 창(09:35~15:50 ET) 안이지만, 11-01 서머타임 종료 후엔 08:45 EST라 건너뛴다 — 반복 스케줄로 바꿀 때 시각 재설정 필요.
- 현금 경합: KR 유지 65만 vs canary 최대 2×10만×1.3=26만 + panic 최대 20만×1.3=26만 = 52만 → 장부상 충분(단 H2·M6로 실제 여유는 더 작고, 파킹은 자동 해제가 없어 레인이 현금을 쓴 뒤엔 유지액이 회복되지 않는다). US 유지 $900 vs T1 하루 1슬롯 LOO 한도 약 14만/환율×1.25 ≈ $125 → 충분. US `orderable_cash`(`frcr_gnrl_ord_psbl_amt`)는 KR과 달리 주문가능액 필드다.
- 손익 산식: canary 수수료(편도 0.015%)+거래세 0.20%, panic 수수료+파생형 ETF 배당소득세 15.4%(이익에만) — 근사로 타당. T1은 원화 손익을 기록하지 않는다(gross_ret만).
- T1 쉐도우: `_candidates`의 티커별 최신 `fetched_at` 필터는 캐시 수집기가 한 번에 종목당 최대 40행을 같은 `fetched_at`로 쓰므로(`us_earnings_dates_cache.py:69`) 오늘/전일 행을 잘못 버리지 않는다. `_finnhub_actual`은 `epsActual == 0`을 정상 처리하고 추정치 부호 충돌 시 채점하지 않는다. 문제 없음.
- 단위 테스트 5파일 34건 통과(변경 없음 상태).

## 5. 수정 우선순위 (텍스트만, 미적용)

1. 22:45 전 SGOV EXCD 확인(M8). 2. H3 intent 선기록 — 4개 도구 공통, 돈이 실제로 걸린 고아 경로. 3. H1 parking 원장·가드·멱등 — 파킹 반복 전 필수. 4. H2 KR 현금 기준 D+2. 5. M3 매도 게이트 분리, M2 부분 체결, M4 inline reconcile. 6. M1 canary 손실 집계. 나머지 Low.
`risk_manager.py`/`trading_bot.py`를 건드리는 수정(L1·L2)은 봇 재시작이 필요하고, 그 전까지 canary·parking·panic 매수가 막힌다.
