**1) 결론: 두 제출 스위치를 지금 켜면 안 됩니다. 정상 주문·체결·출구 연결은 있지만, US 일일 한도 우회와 KR 주문결과 불명 처리 누락이 확인됩니다.**

- **감사 기준과 확인 범위:** `git log -1` 출력은 `4aa6697bf7da8fdf8cca37b2a8df91cb7b743e6c`, `HEAD -> feat/net-hygiene-improvements-20260626`입니다. 아래는 파일을 읽어 확인한 정적 감사이며, 테스트·주문·재시작은 실행하지 않았습니다. 저장 포지션에는 SCHG·275280·275300과 코어 전략 귀속이 있습니다. 실행 중 프로세스의 메모리와 실제 계좌 잔고는 이번 감사에서 직접 확인하지 않았습니다. 근거: `state/live_open_positions.json:3,15,493,505,983,995`.

- **설정은 시작 설정이 나중에 덮어씁니다.** `.env.live` 로드 후 `config/v2_start_config.json`의 `env_overrides`를 `os.environ`에 반영합니다. 이후 런타임 설정은 값을 복사해 보관합니다. 따라서 파일 한 곳 변경이 곧 실행 중 봇의 변경이라는 뜻은 아닙니다. 두 파일의 제출 스위치는 모두 false입니다. 근거: [trading_bot.py:68](E:/code/claudetrade/trading_bot.py:68), `trading_bot.py:75–98,977,5340–5344`, `config/runtime_config.py:75–95`, `.env.live:881,1153`, `config/v2_start_config.json:658,675`.

- **실제 진입 경로는 `risk.can_open()`을 통과하지 않습니다.**  
  `run_entry_scan` → 시장별 handoff → `_submit_micro_probe_buy_order` → `_new_buy_block_state` → KIS `precheck_order` → `place_order`입니다. 공통 게이트는 HALT·장 운영시간·진입 금지시간·guardian·손절 집단 차단·주문결과 불명 등을 검사합니다. 그러나 `risk_manager.can_open()`에 있는 모든 검사가 자동 적용되는 구조는 아닙니다. 근거: [trading_bot.py:11134](E:/code/claudetrade/trading_bot.py:11134), `trading_bot.py:11136–11329,14389–14428,14466,35543–35561,35609–35613`.

- **US 정상 경로에는 비교적 촘촘한 방어가 있습니다.** 신호 DB·정책·증거 파일 확인, 권한 판정, ACK, 브로커 동기화, 기존 보유·미체결, 동일 종목 재진입, 신뢰 가능한 잔고, 시세·전일 종가 대조, 갭·추격·하락폭, 현금·슬롯 검사를 거칩니다. 근거: `runtime/us_swing_order_bridge.py:536–562,607–615,675–717`; `runtime/us_swing_order_handoff.py:366–448`.

- **P0 — US의 ‘하루 1건’은 실제 누적 주문 한도가 아닙니다.** 제출된 신호를 조회에서 제외하고, 남은 후보에 `_band_position`을 다시 1부터 붙입니다. `max_new_per_day`는 이 순위와 비교하며, 성공 후 `break`도 현재 함수 호출만 끝냅니다. 따라서 **A 제출 → 다음 스캔에서 A 제외 → B가 새 1순위 → 현금·슬롯이 있으면 B 제출**이 가능한 코드 경로입니다. 동일 종목 재진입 검사는 다른 종목 B를 막지 않습니다. 실제 사고 발생 여부는 미확인이지만 경로는 확인됩니다. 근거: [runtime/us_swing_order_handoff.py:215](E:/code/claudetrade/runtime/us_swing_order_handoff.py:215), `동 파일:324–341`; [runtime/us_swing_order_bridge.py:431](E:/code/claudetrade/runtime/us_swing_order_bridge.py:431), `동 파일:575–590,818–821`; `trading_bot.py:23084–23119`. 기존 멱등성 테스트는 당일 PENDING 신호가 TEST 한 종목인 경우입니다. `tests/test_us_swing_order_bridge_e2e.py:39–47,152–162`.

- **P0 — KR은 `UNKNOWN`을 안전하게 격리하지 않습니다.** 공통 주문 함수는 주문 예외 때 `_last_micro_probe_submit_result.status="UNKNOWN"`을 남기고 false를 반환합니다. US는 이를 `_v2_record_order_unknown`과 신호 원장에 기록하고 중단하지만, KR은 반환값만 보고 `SUBMIT_FAILED`로 기록합니다. 브로커에는 접수됐지만 응답이 유실된 경우, 다음 후보 또는 다음 스캔의 주문을 확실히 막는 동일한 배선이 없습니다. 근거: [trading_bot.py:14465](E:/code/claudetrade/trading_bot.py:14465), `동 파일:14488,14520–14524`; `runtime/us_swing_order_bridge.py:822–851`; [runtime/kr_fallen_order_bridge.py:389](E:/code/claudetrade/runtime/kr_fallen_order_bridge.py:389), `동 파일:406–420`.

- **P0 — 양 시장 모두 HTTP 500 뒤 재제출 위험이 남습니다.** 주문 조회에 실패하면 재시도를 막지만, 조회가 성공하고 해당 주문이 아직 보이지 않으면 한 번 재제출합니다. ‘조회에 아직 없음’이 ‘미접수 확정’인지는 코드만으로 보장되지 않습니다. 접수 정보의 반영 지연이 있으면 중복 주문 위험이 있다는 정적 판단입니다. 근거: [kis_api.py:3665](E:/code/claudetrade/kis_api.py:3665), `동 파일:3677–3695,3806–3836`.

- **KR의 추가 공백:** 브리지 자체에는 US처럼 주문 직전 `_sync_runtime_with_broker()`와 명시적인 broker-trust 필수 검사가 없습니다. 공통 게이트의 직접적인 `degraded/untrusted` 격리는 US에 한정됩니다. KR은 guardian·현금 상태 등 다른 방어에 더 의존합니다. 신호도 ‘정확한 직전 거래일’ 대신 기본 달력 6일까지 허용합니다. 당일 진입 횟수는 현재 보유·미체결에서만 세므로 당일 청산된 진입은 빠질 수 있습니다. 근거: `runtime/kr_fallen_order_bridge.py:225–426,267–279,213–221`; `trading_bot.py:11163–11170,11289–11312`.

- **정상 체결 후 전략·출구 메타데이터 연결은 있습니다.** 접수 후 pending에 `source_strategy`, TP·SL·보유기간 등을 저장하고, 동기화가 부분체결·전량체결을 실제 포지션으로 만듭니다. 저장 포지션·pending을 복구 템플릿으로 사용합니다. 다만 접수와 pending 영속화가 원자적이지 않아, 그 사이 프로세스가 죽었을 때 동일 귀속 복구가 반드시 된다고 보장할 수는 없습니다. 근거: `trading_bot.py:14466,14598–14625,25053–25070,25218–25246,25320–25369,25552–25575,25617–25628`.

- **출구는 연결돼 있지만 Claude가 최종 방어막이라는 전제는 틀립니다.** `risk.get_exit_candidates()`와 고정 만기 후보가 합쳐져 `_execute_sell`로 전달되고, 매도 주문 후 기본적으로 체결 확인을 요구합니다. **TP12·SL25·D7·BE락은 Claude 전체 자동매도 리뷰의 명시적 예외**입니다. BE락은 US에만 있고, 코어 두 전략은 이 TP/SL 평가에서 제외됩니다. D7은 만기 출구 스위치와 장 마감 15분 창에 의존합니다. 근거: [risk_manager.py:1371](E:/code/claudetrade/risk_manager.py:1371), `동 파일:1383–1426`; `trading_bot.py:26754–26760,26851,26897–26928,28075–28087,29202–29206,30412,30455–30463`; `config/v2_start_config.json:724–725`.

- **(a) phantom은 포지션 회계는 분리되지만, 시스템 자원까지 완전히 격리되지는 않습니다.** 별도 JSON·원장과 자체 `_LOCK`을 사용하고, 실제 `risk.positions` 대신 파일에서 읽은 포지션을 출구 함수에 전달합니다. 그러나 US 실주문 신호 DB를 읽기 전용으로 공유하며, 같은 봇·KIS 호출 제한·토큰·로그를 사용합니다. 출구 함수의 `_SLEEVE_TP_LOG_AT`도 종목 기준 글로벌이어서 유령 평가가 실제 TP/BE 로그를 최대 600초 억제할 수 있습니다. 매도 판단 자체를 차단하는 코드는 아닙니다. 근거: [runtime/phantom_book.py:37](E:/code/claudetrade/runtime/phantom_book.py:37), `동 파일:40–78,208–219,314–326,393–449`; `risk_manager.py:1410–1416,1431–1438`.

- **phantom 부하는 실전 운영에 영향을 줄 수 있습니다.** housekeeping에서 실포지션 출구 처리 뒤 유령 시세 조회가 동기적으로 실행됩니다. 예외를 잡는다고 호출 지연까지 없어지는 것은 아닙니다. 또 탐색 접두사 거절보다 시세 일괄 조회가 먼저입니다. 잘못 들어온 탐색 원장 행은 포지션 생성은 막혀도 API 부하는 유발할 수 있습니다. 근거: `trading_bot.py:33155–33164,26864–26882`; `runtime/phantom_book.py:258–268`.

- **(b) 접두사 가드는 실주문 브리지에는 없습니다.** 관측기는 xus/xkr universe를 제외하고 phantom은 `xus_`, `xkr_`, `c_`를 거절합니다. 하지만 두 실주문 브리지와 공통 제출 함수에는 같은 가드가 없습니다. **현재 확인한 producer 연결에는 탐색/후보 북 → 실주문 브리지 자동 전달이 없습니다. 다만 실주문 입력 DB·원장으로 행이 유입됐을 때 접두사로 거절하는 최종 방어도 없습니다.** 종목이 두 풀에 동시에 존재하는 것과 탐색 arm 자체의 승격은 구분해야 합니다. 근거: `tools/observe_arm_picks_realtime.py:91–92`; `runtime/phantom_book.py:267–269`; `runtime/us_swing_order_handoff.py:215–226`; `runtime/kr_fallen_order_bridge.py:158–189`; `trading_bot.py:14326–14466`.

- **09-01 이후 코드의 직접 영향도 있습니다.** US의 `REHEARSAL_READY`는 이제 실주문 신호 조회에서 제외되므로, 같은 세션 도중 제출을 켜도 기존 리허설 픽을 그대로 주문하지 않습니다. KR 브리지는 phantom을 직접 생성하지 않고 리허설 이력만 남깁니다. `virtual_books`의 KR 경로는 급락 원장에서 별도로 후보를 재계산합니다. 따라서 ‘두 브리지가 모두 phantom_book에 동일하게 기록한다’는 설명은 코드와 다릅니다. 근거: `runtime/us_swing_order_handoff.py:218–219`; `runtime/us_swing_order_bridge.py:735–751`; `runtime/kr_fallen_order_bridge.py:378–388`; `tools/virtual_books.py:640–672`.

---

**2) 결론: 후보 arm 승격은 스위치 변경이 아니라 ‘실시간 후보 입력·계약 검증·한도 회계’를 기존 주문 브리지 앞에 추가하는 작업입니다.**

- **후보 정의와 입력 소스는 이미 존재합니다.** `c_us_fallen_regime`은 `xus_fallen3`에 `chg≤−5`, 신호일 지수 MA20 아래를 적용하고, `c_kr_fallen5_nobio`는 `xkr_fallen3`에 `chg≤−5`, 제약·바이오 제외를 적용합니다. 탐색 풀의 원천은 스크리너 DB가 아니라 `data/price/us`, `data/price/kr`의 CSV입니다. 근거: [tools/virtual_books.py:226](E:/code/claudetrade/tools/virtual_books.py:226), `동 파일:228,236,241–258`; `tools/discovery_pools.py:31–32,71–83,187–205`.

- **국면 계산을 새로 발명할 필요는 없습니다.** `tools/discovery_pools.py::_index_regime`이 US SPY CSV와 KR 지수 JSON을 읽어 신호일 종가와 당일 포함 20봉 평균을 비교합니다. `idx_above_ma20=False`는 종가가 MA20 미만이라는 뜻입니다. 이 값은 현재 실주문 브리지의 필수 조건으로 연결돼 있지 않습니다. 승격 시 같은 계산을 공용 모듈로 옮기고, 신호일 일치·20봉 확보·결측 차단을 강제하는 것이 최소 경로입니다. 근거: `tools/discovery_pools.py:167–184,233–236`; `runtime/us_swing_order_bridge.py:385–433`; `runtime/kr_fallen_order_bridge.py:169–189`.

- **US 실시간 입력에 결정적인 문제가 있습니다.** 탐색 생성기는 US 후보의 세션 키를 **다음 CSV 봉 날짜**로 만들고, 다음 봉이 없으면 후보를 버립니다. 이를 그대로 사용하면 다음 장 개장 전에 최신 후보가 나오지 않습니다. 최소 변경은 완결된 신호봉에서 특성을 계산하고 거래일 달력으로 다음 진입 세션을 지정하는 별도 실시간 출력입니다. 다음 봉 OHLCV를 기다려서는 안 됩니다. 근거: [tools/discovery_pools.py:220](E:/code/claudetrade/tools/discovery_pools.py:220).

- **바이오 필터는 있으나 실전용으로는 결측 처리가 부족합니다.** `data/sector_map.json`과 `"제약·바이오"` 분류를 실제 확인했습니다. 다만 파일 읽기 실패나 미등록 종목은 빈 문자열을 반환하고 제외 필터를 통과합니다. 승격 시 분류 결측을 ‘비바이오’로 취급하지 않도록 변경해야 합니다. 근거: `data/sector_map.json:5,29`; [tools/virtual_books.py:255](E:/code/claudetrade/tools/virtual_books.py:255), `동 파일:264–272`.

- **현재 라이브 입력과 후보 풀은 다릅니다.** US는 `us_swing_shadow.db`의 `signals`와 `candidate_pool_all`을 사용하고, `day_losers` 제한은 상류 shadow runner에서 적용합니다. KR은 급락·blindspot JSONL을 읽고 R2/R4를 재판정합니다. 기존 입력에 필터 한 줄만 더하면 넓은 후보 풀과 동일해지지 않습니다. 근거: `runtime/us_swing_order_bridge.py:539,137–158,575–590`; `tools/us_swing_shadow_runner.py:331–336`; `runtime/kr_fallen_order_bridge.py:43–46,158–189`; `tools/kr_fallen_gate_report.py:118–125`.

- **완만 급락 −12%는 두 구현을 구분해야 합니다.** 기존 `us_slow_fallen`은 별도 JSON 가격 캐시·원장을 사용하며, 5일 누적 −12% 이하·각 일 −5% 초과·거래대금 100M 이상 500M 미만입니다. 새 `xus_slow8`은 CSV 기반 확장 풀입니다. 새 풀에서 −12% 변형을 만들려면 `cum5_le=-12` 필터를 추가할 수 있지만, 이것만으로 기존 slow arm의 거래대금 계약까지 같아지지는 않습니다. 근거: `tools/observe_slow_fallen.py:40–46,89–108`; `tools/discovery_pools.py:132–137`; `tools/virtual_books.py:230–232,247–248`.

- **최소 diff 제안 — 아직 구현한 변경이 아닙니다.**
  1. 후보 계산·국면·섹터 판정을 공용 순수 함수로 분리하고, 완결 신호일 기준 실시간 후보 스냅샷을 생성합니다. 기존 위치: `tools/discovery_pools.py:86–155,167–236`, `tools/virtual_books.py:241–272`.
  2. 두 브리지의 후보 로더 앞에 승인된 계약 ID·버전·입력 날짜를 확인하는 어댑터를 추가합니다. `x*/c_` 원본은 거절하고, 승인된 실전 계약만 별도 명시적으로 받게 합니다. 변경 지점: `runtime/us_swing_order_bridge.py:575–590`, `runtime/kr_fallen_order_bridge.py:280–292`.
  3. US 기존 밴드·MAX와 KR R2/R4가 새 계약에 몰래 추가되지 않도록 계약별 선택기를 분리합니다. 현재 선택 지점: `runtime/us_swing_order_bridge.py:399–432`, `runtime/kr_fallen_order_bridge.py:171–189`.
  4. 주문·동기화·출구는 기존 경로를 재사용하되, 포지션과 pending에 새 계약 ID를 보존합니다. `source_strategy`만 c_*로 바꾸면 기존 isolated 출구가 인식하지 못하므로, 기존 출구 소유자를 유지하거나 등록 범위를 함께 바꿔야 합니다. 근거: `trading_bot.py:14607,25340`; `risk_manager.py:33–40`; `trading_bot.py:26906–26911`.
  5. 주문액·일일 누적 진입·동시 슬롯·총 노출을 유한한 실전 값으로 배선합니다. 후보 북의 `daily_cap/slots=1,000,000`, 자본 1조원은 절대 가져오면 안 됩니다. 근거: [tools/virtual_books.py:221](E:/code/claudetrade/tools/virtual_books.py:221), `동 파일:223`; `runtime/kr_fallen_order_bridge.py:301–332`; `runtime/us_swing_order_handoff.py:420–448`.

---

**3) 결론: 98만원으로 현재 코드가 허용하는 KR 급락 보유는 최대 3종목입니다. 20~80종목 분산은 API 이전에 슬롯·정수주·현금 배분·슬리피지 통제부터 바꿔야 합니다.**

- **현재 실효 한도:** Phase3=true이므로 후보 1개면 하루 1건, 2~9개면 2건, 10개 이상이면 3건이고 동시 최대 3슬롯입니다. `KR_FALLEN_MAX_NEW_PER_DAY=1`은 Phase3에서 사용되지 않습니다. 건당 상한은 22만원입니다. 근거: `config/v2_start_config.json:661–665`; [runtime/kr_fallen_order_bridge.py:301](E:/code/claudetrade/runtime/kr_fallen_order_bridge.py:301), `동 파일:318–332`.

- **주문 단위:** 이 구현은 정수주이고 최소 1주입니다. 종목당 최소 원화금액을 KIS 주문 API에서 강제하는 코드는 확인되지 않았습니다. 설정의 `KR_MIN_ORDER_KRW=50000`도 이 브리지에서는 직접 적용되지 않으며, 제출 메타에는 `min_effective_order_krw=0`을 넣습니다. 따라서 ‘무조건 최소 5만원’이라고 계산하면 잘못입니다. 근거: `kis_api.py:3533–3537,3588–3604`; `runtime/kr_fallen_order_bridge.py:360–363,401–404`; `config/v2_start_config.json:56`.

- **호출 제한:** 코드 기본값은 **HTTP 12회/초**입니다. 주문 12건/초가 아닙니다. 주문 한 건에도 hashkey 발급과 주문 POST가 필요하고, 시세·잔고·체결 조회가 추가됩니다. EGW00201은 기본 2회 재시도합니다. 실제 KIS 계정에 적용되는 최신 서버 한도는 저장소만으로 확정하지 못했습니다. 근거: `kis_api.py:86–95,491–502,562,3622–3627`. 두 설정 파일 검색에는 `KIS_RATE_RPS`, `KIS_CROSS_PROC_RATE_ENABLED`가 없어 코드 기본값 기준입니다.

- **다중 프로세스가 호출량을 공유하는 문제가 큽니다.** 프로세스 간 제한은 기본 false이고, 켜더라도 락 획득 실패 시 개별 프로세스 제한으로 돌아갑니다. KR 개장 수집기는 종목당 현재가·체결·호가의 세 종류 조회를 합니다. 80종목이면 한 스냅샷에 최대 약 240회이며, 단순 12회/초 계산만 해도 약 20초입니다. 주문·출구 조회와 경쟁하므로 개장 구간의 매도 우선순위가 필요합니다. 근거: `kis_api.py:94,373–387,441–442`; `tools/kr_open_flow_collector.py:49–81`.

- **비용은 저장소 설정값 기준입니다.** 매수 0.015%, 매도 0.195%이며 주석은 매도를 수수료 0.015%+세금으로 설명합니다. 역산 세금은 0.18%, 동일 가격 왕복 비용은 약 0.21%입니다. 이는 **코드 가정이지 현재 계좌의 실제 수수료·법정 세율 확인 결과가 아닙니다.** 근거: [risk_manager.py:208](E:/code/claudetrade/risk_manager.py:208), `동 파일:217–219`; `config/v2_start_config.json:569–570`.

- **KR fallen의 하드 슬리피지 캡은 확인되지 않았습니다.** `PATHB_KR_SLIPPAGE_CAP=1.003`은 다른 경로의 설정입니다. fallen은 공통 `_compute_order_price`를 사용하고, `ENABLE_LIMIT_ORDER`가 없으면 KR 시장가를 반환합니다. 지정가를 켜도 가격을 정수로 반올림할 뿐 이 함수에는 호가단위 정규화가 없습니다. 신호 종가 대비 +10% 거절은 체결 슬리피지 캡이 아닙니다. 근거: `config/v2_start_config.json:583`; `trading_bot.py:1008–1009,14389,25983–26003`; `runtime/kr_fallen_order_bridge.py:357–358`.

예수금 980,000원, 추가 예약금 없음이라는 **사용자 제공 가정**으로 계산하면 다음과 같습니다. 2% 여유자금은 운영 제안이며 현재 코드의 강제값이 아닙니다.

| 실행 형태 | 종목 수·건당 금액 | 계산 및 한계 |
|---|---:|---|
| 현재 설정 | 최대 3종목 × 최대 220,000원 | 매수대금 최대 660,000원, 설정 매수수수료 약 99원. 현금·후보·기존 슬롯 조건 충족 필요 |
| 건당 50,000원 유지 | 최대 19종목 | 950,000원+매수수수료 약 143원. 현재 슬롯 변경 필요 |
| 20종목 균등 배분 | 종목당 예산 48,020원 | 2% 제외한 960,400원÷20. 실제 주문액은 정수주 내림 |
| 40종목 균등 배분 | 종목당 예산 24,010원 | 각 종목 1주 가격이 예산 이하여야 균등 배분 가능 |
| 80종목 균등 배분 | 종목당 예산 12,005원 | 고가 종목은 매수 불가. ‘풀 전량’ 보장 불가 |

- **현실적인 최대 종목 수를 80이라고 확정할 수는 없습니다.** 모든 후보의 실시간 1주 가격과 거래 가능 여부가 필요합니다. 균등 예산이면 `주가≤건당 예산`, 비균등 최소 1주라면 `Σ주가×(1+매수수수료)≤가용현금`이 조건입니다. 현재 브리지는 이런 전량 배분 최적화가 아니라 후보 순서대로 최대 22만원씩 사용하는 방식입니다. 근거: `runtime/kr_fallen_order_bridge.py:335–361`; `kis_api.py:3551–3558`.

- **실무 판단:** 체결·비용 검증 전 20~80종목으로 넓힐 근거는 없습니다. 먼저 유한 슬롯과 비용 포함 예약 회계를 고치고, 주문 거절·미체결·실현 슬리피지를 기준으로 수용 가능한 종목 수를 정해야 합니다. 현재 수량 계산과 KIS precheck에는 매수수수료 여유분도 명시적으로 포함되지 않습니다. 근거: `runtime/kr_fallen_order_bridge.py:360–361`; `kis_api.py:3551–3558`.

---

**4) 결론: 이 코드에는 NXT 실주문 경로가 없습니다. NX/UN 시세 지원과 NXT 유령 매매만 확인됩니다.**

- 국내 주문 본문은 `EXCG_ID_DVSN_CD="KRX"`로 고정이고, `place_order`에는 국내 거래소를 선택하는 인자가 없습니다. 국내 취소 주문과 미체결 조회도 KRX를 지정합니다. 근거: [kis_api.py:3595](E:/code/claudetrade/kis_api.py:3595), `동 파일:3605,3847–3850,3867,2961`.

- 시세에는 `"J"=KRX`, `"NX"=NXT`, `"UN"=통합` 구분이 설명돼 있고, 공시 이벤트 레인은 NX 현재가·최근 체결을 직접 조회합니다. 근거: `kis_api.py:4236`; `runtime/kr_event_lane.py:358–392`.

- 이벤트 레인의 OPEN은 주문 API 호출이 아니라 유령 dict 생성과 `kr_event_phantom.jsonl` 기록입니다. `venue=NXT` 필드는 실주문 접수를 뜻하지 않습니다. 근거: [runtime/kr_event_lane.py:452](E:/code/claudetrade/runtime/kr_event_lane.py:452), `동 파일:454–465`.

- **필요 변경:** NXT 주문뿐 아니라 거래소별 취소·미체결·체결 확인·재시작 복구까지 함께 지원해야 합니다. 현재의 KRX 고정을 일부만 NX/NXT로 바꾸는 수정은 실전 전환으로 인정할 수 없습니다. 변경 근거 지점: `kis_api.py:2961,3605,3867`; `trading_bot.py:29208–29217`.

---

**5) 결론: 감시·복구 장치는 있지만, 단일 봇 의존과 공유 API 자원, 약한 승격 강제력이 남아 있습니다. ‘몇 번 성공했으니 증액’은 코드가 확실히 막아주지 않습니다.**

- **단일 장애점:** 보유 가격 갱신·계약 출구·실제 매도는 `trading_bot` 프로세스 안에 있습니다. guardian과 스케줄러가 있어도 봇이 죽거나 출구 호출이 지연되는 동안 별도 주문 엔진이 계약 청산을 대신한다는 경로는 이번 감사에서 확인하지 못했습니다. 기동 스크립트에는 전역 mutex와 watchdog 관련 복구가 있습니다. 근거: `trading_bot.py:33145–33164,26754–26851`; `tools/start_live_stack_headless.ps1:15–41`; `tools/integrity_check.py:687–711`.

- **신규 공시·수집기는 포지션 파일을 침범하지 않지만 API·파일 자원은 공유합니다.** 공시 이벤트는 전용 state·원장을 쓰고, 개장 수집기도 `kr_open_flow` 전용 파일을 씁니다. 개장 수집기는 `.env.live`만 직접 로드하며 start-config 병합은 하지 않습니다. 실전 설정 일치와 부하 제어 관점에서는 별도 관리가 필요합니다. 근거: `runtime/kr_event_lane.py:39–45,431–435`; `tools/kr_open_flow_collector.py:26–29,49–81`.

- **토큰 자동 갱신은 있습니다.** 캐시 만료 10분 전부터 재발급하고, 전날 발급 토큰도 무효로 취급합니다. API 응답이 토큰 만료이면 강제 갱신 후 한 번 재요청합니다. 발급 제한 마커와 기본 70초 쿨다운도 있습니다. 하지만 `save_token`은 파일에 직접 덮어쓰며, 발급 전체를 프로세스 간 단일화하는 락은 이 경로에 없습니다. 다중 프로세스 동시 갱신·읽기 경합 가능성이 남습니다. 근거: [kis_api.py:666](E:/code/claudetrade/kis_api.py:666), `동 파일:675–699,580–589,712–714,865–889`. ‘자정 무효화’는 코드의 처리 규칙으로 확인한 것이며 KIS 정책 자체의 최신 검증은 아닙니다.

- **3시간 세션·체인 제한은 미확인입니다.** 저장소 코드·스크립트에서 검색한 `10800`, `PT3H`, `ExecutionTimeLimit`, `3시간`으로 실행 체인 전체를 3시간에 종료하는 설정은 확인하지 못했습니다. 확인한 counterfactual runner는 작업별 기본 timeout 900초, preopen은 작업 종류별 600/1200초 등을 사용합니다. Windows에 실제 등록된 작업 정의나 저장소 밖 체인은 조회하지 않았으므로 3시간 제한이 없다고 단정하지 않습니다. 근거: `tools/run_counterfactual_pipeline.py:394–418,524–527`; `tools/preopen_scheduler.py:315–334`.

- **대시보드 경보는 있습니다.** 주문상태 불명, 브로커 스냅샷 문제, 전략 주문 안전·설정/실행 불일치를 표시합니다. 다만 화면 경보의 존재가 주문을 막는 강제 게이트와 같지는 않습니다. 근거: `dashboard/dashboard_server.py:6770–6801,15381`.

- **integrity_check도 실주문 관련 검사를 합니다.** TP/SL 초과 보유, phantom 혼입, 계약 설정 불일치, D7 설정 불일치, 중요 프로세스 생존을 검사합니다. 그러나 TP/SL 초과는 WARN이고, 해당 함수에는 BE락 미청산·만기 청산 지연 검사가 없습니다. 전체 호출 목록에도 새 KR 개장 스냅샷 전용 검사는 없습니다. 근거: [tools/integrity_check.py:284](E:/code/claudetrade/tools/integrity_check.py:284), `동 파일:325–344,450–473,635–683,856–874`.

- **사전등록은 부분적으로만 강제됩니다.** US 권한 평가기는 증거를 검사하지만, 현재 ACK가 허용하는 override는 표본 부족뿐 아니라 **forward 평균·PF 미달도 우회**합니다. 주문액·슬롯·일한도는 환경값을 받아 설정 변경으로 확대할 수 있습니다. KR은 실행 시 사전등록 성과 보고서를 검사하지 않고 live/ACK·규칙·Phase3 설정을 사용합니다. 주석의 ‘게이트 통과 후’는 그 자체로 실행 강제가 아닙니다. 근거: [runtime/us_swing_order_bridge.py:95](E:/code/claudetrade/runtime/us_swing_order_bridge.py:95), `동 파일:101–130`; `config/v2_start_config.json:677`; `runtime/kr_fallen_order_bridge.py:227–245,280–332`.

- **후보 arm의 사전등록 날짜도 실주문 권한이 아닙니다.** 후보의 forward 시작일과 무제한 가상 자본은 `virtual_books`의 정의이며 두 실주문 브리지에 승인 객체로 연결되지 않았습니다. 확대를 강제 통제하려면 계약 버전·총 노출·손실 한도·승인 유효기간을 주문 직전 검사해야 합니다. 근거: `tools/virtual_books.py:217–237`; `runtime/us_swing_order_bridge.py:759–800`; `runtime/kr_fallen_order_bridge.py:389–404`.

- **‘실주문 레인 두 개뿐’이라는 전제도 보완해야 합니다.** 코어용 `profit_strategy_order_bridge` 호출이 남아 있고, handoff·submit·ACK는 켜져 있습니다. 현재는 `PROFIT_STRATEGY_ENABLED_IDS=""`라 입력 필터가 모든 전략을 제외합니다. 이 목록을 복구하면 별도 신규매수 경로가 다시 열릴 수 있습니다. 근거: `config/v2_start_config.json:708–712`; `trading_bot.py:35563–35570,35617`; `runtime/profit_strategy_order_bridge.py:64–66,150–156`.

- **재시작 스크립트는 새 탐색 코드가 아닙니다.** 해당 파일의 마지막 변경은 `git log` 출력상 `d812ba5 2026-08-24`입니다. 브로커 스냅샷·백업·정지·기동 재시도·PID·사후 스냅샷 검사는 있지만, 전후 수량·미체결의 차이를 반드시 대사 통과시켜야 성공하는 조건은 보이지 않습니다. before/after를 기록한 뒤 신선도를 확인합니다. 재시작 중 주문·귀속 연속성 검증을 추가할 필요가 있습니다. 근거: `tools/restart_live_stack_safely.ps1:153–168,183–216,219–232`.

---

**6) 결론: 지금 유지할 것은 기존 출구·감시·비주문 관측이며, 신규 실매수 재개는 P0 수정과 주문 상태 전이 검증을 통과한 뒤로 미뤄야 합니다.**

아래 P0는 실매수 재개 전 필수, P1은 후보 승격·분산 확대 전 필수입니다. 변경 사항은 모두 제안이며 이번 감사에서는 수정하지 않았습니다.

| 항목 | 현재 상태 | 필요 변경 | 우선순위 |
|---|---|---|---|
| US 일일 진입 한도 | 남은 후보 재순위화로 누적 한도 우회 가능. `us_swing_order_bridge.py:431,818` | 세션별 접수·UNKNOWN·당일 청산 포함 진입 원장과 원자적 한도 예약 | **P0** |
| KR 주문결과 불명 | UNKNOWN을 브리지에서 실패로 축약. `kr_fallen_order_bridge.py:406` | US와 같은 UNKNOWN 영속화·신규 진입 차단·브로커 대사 | **P0** |
| HTTP 500 재제출 | 조회 공백이면 한 번 재제출. `kis_api.py:3693,3834` | 미접수 확정 전 재제출 금지, 지연 조회·복구 상태 도입 | **P0** |
| KR 주문 전 계좌 신뢰 | 브리지 직접 최신 동기화·trust 필수 검사 없음. `kr_fallen_order_bridge.py:335–365` | 최신 잔고·미체결·주문가능금액 확인과 실패 시 차단 | **P0** |
| 입력 계약 격리 | x*/c_ 가드는 phantom에만 존재. `phantom_book.py:267` | 브리지·최종 제출 직전 가상 표식 거절과 승인 계약 allowlist | **P0** |
| 접수 후 복구 | 접수 뒤 pending 기록. `trading_bot.py:14466,14598` | 제출 의도 선기록, 재시작 시 주문번호·귀속·수량 복원 검증 | **P0** |
| 선정 데이터 결측 | US 거래대금/MAX 결측 시 통과 가능. `us_swing_order_bridge.py:279–284,236–239` | 승인 계약의 필수 입력 결측 시 주문 차단 | **P0** |
| KR 신호 신선도 | 기본 달력 6일 허용. `kr_fallen_order_bridge.py:267–279` | 정확한 직전 거래일·수집 완료·신호 날짜 일치 검증 | **P1** |
| 후보 실시간 공급 | US 다음 CSV 봉 필요. `discovery_pools.py:220–222` | 완결 신호봉+거래일 달력 기반 장전 입력 | **P1** |
| 바이오 제외 | 미분류는 필터 통과. `virtual_books.py:264–272` | 분류 누락 차단·분류 버전 기록 | **P1** |
| KR 소액 분산 | 3슬롯, 최대 22만원 순차 배분. `kr_fallen_order_bridge.py:301–361` | 정수주·수수료·예약현금 반영 배분 및 유한 총 노출 | **P1** |
| 체결 가격 통제 | fallen에 PathB 캡 미적용. `trading_bot.py:14389,25993` | 호가단위·스프레드·최대 체결가격·취소 정책 | **P0** |
| API·토큰 공유 | 프로세스별 제한, 토큰 직접 파일 쓰기. `kis_api.py:94,690` | 공통 호출 예산·매도 우선순위·토큰 발급 단일화 | **P1** |
| phantom 부하·로그 | 같은 봇/API·TP 로그 글로벌 공유. `phantom_book.py:425`, `risk_manager.py:1433` | 실행 부하 분리, 가상/실전 로그 키 분리 | **P1** |
| 출구 감시 | TP/SL WARN, BE/D7 지연 감시 부족. `integrity_check.py:325–344` | 가격 신선도·BE/D7 기한·미체결 지속 경보 | **P0** |
| NXT | 주문·취소 KRX 고정. `kis_api.py:3605,3867` | 거래소별 주문부터 복구까지 일괄 지원 | **NXT 개통 전 필수** |
| 재시작·스케줄 | 복구 장치 있음, 실제 작업 제한 미확인. `restart_live_stack_safely.ps1:219–232` | 전후 주문 대사·실제 작업 정의·중단 시간 검증 | **P1** |
| 증액 승인 | env·ACK로 일부 증거 우회 가능. `us_swing_order_bridge.py:101–130` | 버전별 주문액·슬롯·총 손실 한도와 유효 승인 강제 | **P0** |

**지금 켜도 되는 것 — 정확히는 현재 범위에서 유지 가능한 것**

- 기존 TP/SL/D7 및 US BE락 출구, 브로커 동기화와 매도 체결 확인. 다만 코어에는 같은 TP/SL이 적용되지 않고, sleeve 청산은 Claude 리뷰 예외라는 운영 정의를 바로잡아야 합니다. 근거: `risk_manager.py:1383–1426`; `trading_bot.py:26906–26928,28075–28087,29202–29206`.
- guardian·대시보드·integrity 감시. ‘정상 표시’만으로 신규매수 안전성을 승인하지는 않아야 합니다. 근거: `tools/integrity_check.py:856–874`; `dashboard/dashboard_server.py:15381`.
- 두 제출 스위치를 false로 둔 리허설, 별도 가상 북·공시 관측·개장 스냅샷. **API 부하를 더 확대하는 것은 별도 조건**입니다. 근거: `config/v2_start_config.json:658,675`; `runtime/kr_event_lane.py:452–465`; `tools/kr_open_flow_collector.py:49–81`.

**지금 절대 켜면 안 되는 것**

- **`US_SWING_ORDER_SUBMIT_ENABLED=true`**: 일일 누적 한도 우회가 남아 있습니다. 근거: `runtime/us_swing_order_handoff.py:215–219,324–341`; `runtime/us_swing_order_bridge.py:431–432,818–821`.
- **`KR_FALLEN_ORDER_SUBMIT_ENABLED=true`**: UNKNOWN 처리 누락과 Phase3 최대 3건의 실효 설정부터 정리해야 합니다. 근거: `runtime/kr_fallen_order_bridge.py:318–321,389–420`.
- **c_*/x* 원장을 그대로 실주문 입력에 연결하거나 가상 슬롯·자본값을 복사하는 것.** 근거: `tools/virtual_books.py:201–204,221–224`; `runtime/phantom_book.py:267–269`.
- **NXT 시세·유령 체결을 근거로 NXT 실주문이 준비됐다고 보고 개통하는 것.** 근거: `kis_api.py:3605,3847–3850,3867`.
- **20~80종목 전량 시장가 매수, 또는 몇 번의 성공만으로 주문액·슬롯 확대.** 현재 한도·체결비용·승인 강제력이 이를 안전하게 뒷받침하지 않습니다. 근거: `runtime/kr_fallen_order_bridge.py:301–361`; `trading_bot.py:25993–26003`; `runtime/us_swing_order_bridge.py:101–130`.
- **코어 `PROFIT_STRATEGY_ENABLED_IDS` 복원, PathB·레거시 신규매수 재활성화.** 이번 두 레인 수정과 별개로 주문 경로가 추가로 열립니다. 근거: `config/v2_start_config.json:503,509,708–712,783`; `runtime/profit_strategy_order_bridge.py:150–156`.

Codex session ID: 01a07bac-b07c-7a73-8d44-47607e327165
Resume in Codex: codex resume 01a07bac-b07c-7a73-8d44-47607e327165
