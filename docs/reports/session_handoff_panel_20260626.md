# 세션 핸드오프 — 6인 AI 패널 토론 + 현재 상태 (2026-06-26)

> 목적: 컨텍스트 clear 후 이어가기. 1차·2차 패널 토론 내용 + 오늘 적용 상태 + 다음 할 일.
> 상세 리포트: `panel_6ai_profitability_direction_20260626.md`(1차), `panel_6ai_round2_verdict_20260626.md`(2차).
> 전 세션 분석은 `docs/reports/debate_*_20260626.md` 다수 + 메모리 `debate-net-measurement-gap-20260626.md`.

---

## 0. 한 줄 요약
6인 패널(트레이너·개발자·투자자·리스크·회의론자·혁신가)이 *신선하게* 봐도 결론은 냉정: **active가 passive를 비용후 이긴 증거 없음(전수 net 음수), 화려한 알파 없음. 진짜 할 일은 ①측정 배관 복구 ②오늘 넣은 A2(×1.5) 재고 ③출혈버킷 차단으로 breakeven ④passive 진지검토.** "수익성 강화"는 측정·결판선 통과 전엔 매몰비용 합리화.

---

## 1. 현재 git/라이브 상태
- 브랜치 `feat/net-hygiene-improvements-20260626`(commit 9ce2082, origin 푸쉬됨, **main 아님**, PR 미생성). 운영테스트 후 머지 결정 운영자 몫.
- **라이브 적용(.env.live, gitignore라 로컬에만):** A1 `REQUIRE_TRADE_READY=true`(ready=0 차단, 오늘 KR 2건 차단 확인), A2 `PATHB_READY_BOOST_MULT=1.5`(ready=1 claude_price ×1.5), `TREND_OVERLAY_GATE_MODE=shadow`, `LESSON_VALIDATION_APPLY_MODE=shadow`(enforce→강등), `HOLD_ADVISOR_EXIT_LESSON_ENABLED=true`.
- **DB 백필(로컬에만):** 4·5월 net, MFE(yfinance), USER_MANUAL 재라벨(→CLOSED_CLAUDE_INTRADAY_SELL).
- 봇 정상 가동(PID 10820). 주말이라 월요일(6/29) 장까지 무거래.

---

## 2. 6인 패널 1차 — 각 역할 핵심 (중립 브리프 받고 신선하게)
- **트레이너:** 엣지는 바이모달(즉사 vs +5% target). loss_cap −2%→−2.76% 오버슈트 −24%p 회수. 유일 반복엣지=Claude TARGET. KR·MILD_BEAR 축소.
- **개발자:** net 1%p 최단거리=loss_cap 청산 주문 수정. **단 측정 배관 깨짐**(selection↔net 조인 죽음·MFE 23%·FX 6월만) — 패널 근거 대부분 부분표본.
- **투자자(PM):** 위험조정 음수(Sharpe −0.09), 증액 NO. ready=0 자본 0, ready=1 US 집중, KR probation. 기회비용=패시브 대비 8주 −12%p.
- **리스크:** 60일 단일 드로다운(회복레그 0), loss_cap 22%가 캡초과(−6.5% 꼬리). **ready=1 ×1.5는 음EV에 레버=파산확률↑.** ready=0 차단만 +EV.
- **회의론자:** TARGET 100%승=생존편향(정의). capture 0.045=execution 죽음. W24-25 최악. active>passive 미입증. ready=1 N=14 무의미.
- **혁신가:** net −80%p=흑자(LLM target capture 1.11)+적자(나쁜진입) 상쇄. **분리(코어-새틀라이트), target 브래킷만 active.** 진짜 자산=LLM 가격목표 구조화 능력.

## 3. 6인 패널 2차 — 반박·결판 (1차 낙관론 데이터로 해체)
- **혁신가 자진 철회:** "target 알파 분리"=생존편향. ex-ante target 식별신호 없음(BULL regime target율 16.8%=틸트). capture 1.11은 27건 중 MFE 2건으로 계산=C급. → 분리론 축소: "출혈버킷(비-claude_price 기계전략 −0.87%·비-BULL) 차단으로 −0.28%→breakeven"(수익원 아님).
- **회의론자 강화:** 전수 net 음수(census US −0.124%·KR −0.844%, 부분표본 아님)→표본한계는 PRO를 침. "winner 더 태우기"=양날, 반사실 N=8 더보유 −3.22pp 악화, giveback median capture 0.12. 결판선: passive paired net US≥+0.5%@N≥260·KR≥+0.5%@N≥390.
- **리스크·PM A2 결판:** A2 모집단(ready=1 claude_price) N=189 net **−0.26% breakeven 책**. TARGET 11%(+99.6) vs LOSS_CAP 22%(−110.6)=loss_cap이 알파 다 상쇄. ex-ante 동일모집단. ×1.5→maxDD −42→−63%p, 6/15-16 19연패 −22.9→−34.4%p. **권고: A2 shadow강등 + "target·MFE 실현확인 후 add-only" 재설계.**
- **개발자 증거등급:** capture 1.11=C(N16·MFE 87%추정·생존편향, "A급 착각 대표 C급"), ready=1 +0.36 vs −1.97=C(크기)/B(방향), loss_cap 회수 −24%p=C(슬리피지 인스트루먼트 0=추론), W24-25 −59%p=C(재현X·FX혼합). MFE 21.5%·조인 1.4%·FX 6월만.

---

## 4. 최종 방향성 (2라운드 후)
| 우선 | 행동 | 근거 |
|---|---|---|
| **1** | **측정 배관 복구** ①조인(MATCHED 1.4%→AMBIGUOUS 205 회수) ②**슬리피지 인스트루먼트**(ORDER_SENT.price↔FILLED.fill_price, *원자료 이미 존재=조인만, 싸다*) ③MFE native 전구간 ④FX 4·5월 백필 | 이거 전엔 어느 결정도 A급 안 됨 |
| **2** | **A2 shadow 강등 + add-only 재설계** | ×1.5는 breakeven 책에 레버=maxDD↑ |
| **3** | **A1 유지 + 출혈버킷 ex-ante 차단**(비-claude_price 기계전략·비-BULL·KR 축소) | −0.28%→breakeven |
| **4** | **passive 진지 검토**(코어-새틀라이트 등) | active>passive 미입증 |
| ❌ | target만 트레이드·winner 더 태우기·×1.5 무차별·capture 1.11을 결정근거로 | 2라운드 반증 |

**결판 측정조건(통과 전 강화=희망):** 6월형 하락/횡보 국면 net>0 & PF>1(N≥80) + passive paired net US≥+0.5%@N≥260·KR≥+0.5%@N≥390 + MFE capture A급(native 전구간).

---

## 5. ★ clear 후 이어갈 것 (다음 세션 우선순위)
1. **[결정 대기] A2(×1.5)를 shadow로 내릴지** — 패널 만장일치 재고 권고. 운영자 결정 필요. (가장 구체적·시급)
2. **측정 배관 복구 착수** — 가장 싼 것부터: 슬리피지 조인(원자료 이미 event_store에 있음, 코드만). 그다음 selection↔canonical 조인(AMBIGUOUS 205 회수).
3. **월요일(6/29) 장 후 A1 운영테스트 재측정** — ready=0 차단으로 loss_cap이 실제 줄었나. 오늘 막은 KR 419050·067290 결과(KR yfinance 부정확, 봇 데이터로).
4. **passive 전환(turtlebot) 진지 검토** — active 미입증이면 코어-새틀라이트가 데이터가 가리키는 방향.

## 5-A. ★★ 지시 (다음 세션 명령 — DO / REVIEW)
> clear 후 새 세션에서 "이 핸드오프 §5-A 지시대로 진행해"라고 하면 이 목록을 실행한다.
> [C]=Claude가 바로 실행 가능 / [운영자]=결정만 내리면 Claude 구현 / [관찰]=측정·검토만.

### DO (해야 할 것)
1. **[운영자] A2(×1.5) shadow 강등 결정.** 패널 만장일치 재고 권고(net −0.26% breakeven 책에 레버=maxDD −42→−63%p). 결정만 주면 `.env.live`+`v2_start_config.json` `PATHB_READY_BOOST_MULT=1.5→1.0` + add-only 재설계 착수. 검증=py_compile+preflight.
2. **[C] 슬리피지 인스트루먼트 조인** — `data/v2_event_store.db`의 `ORDER_SENT.price_native`↔`FILLED.fill_price_native`(+ 매도 stop) 조인해 expected-vs-fill 기록. 원자료 이미 존재, 코드만. loss_cap 오버슈트(−24%p 회수) 가설 결판용.
3. **[C] selection↔canonical 조인 복구** — `v2_decision_fill_links` MATCHED 1.4%(12/852), AMBIGUOUS 205건을 order_no/timing 키로 회수. selection 근거↔실현net 조인 살리기.
4. **[C] 월요일(6/29) 장 후 A1 효과 재측정** — ready=0 차단으로 loss_cap 줄었나(`tools/improvement_net_monitor.py` 확장 or 직접 쿼리). 오늘 막은 KR 419050·067290 결과(봇 데이터, KR yfinance 부정확).
5. **[C] MFE native 전구간 백필** — 현 커버리지 21.5%(추정 75%). capture/target 결정의 분모. (yfinance 추가 fetch=API, 운영자 승인 후.)

### REVIEW (검토할 것 — 결정 전 측정/분석)
6. **[운영자/관찰] passive 전환(turtlebot) 진지 검토** — active>passive 미입증(전수 net 음수). 코어-새틀라이트 제3안. 결판선: passive paired net US≥+0.5%@N≥260·KR≥+0.5%@N≥390.
7. **[C] 출혈버킷 ex-ante 차단 분석** — 비-claude_price 기계전략(net −0.87%)·비-BULL 국면·KR 축소가 −0.28%→breakeven 시키는지 shadow 설계.
8. **[관찰] A2 add-only 재설계 가능성** — "target 부근 MFE 도달 + loss_cap 미발생 확인 후 add"가 구현 가능한지(런타임 MFE 관측 배선).

### DON'T (하지 말 것 — 2라운드 반증)
- target만 트레이드(생존편향), winner 더 태우기(giveback 칼날 아래), ×1.5 무차별, capture 1.11을 A급 결정근거로, KR 폐기(축소만), 측정 배관 복구 전 사이즈업.

---

## 6. 주의 (다음 세션이 알아야 할 함정)
- **패널 근거 절반이 C급**(N=14~21·MFE 87% 추정·조인 1.4%). "capture 1.11/target 알파"를 A급으로 쓰지 말 것. 측정 배관 복구 전엔 모든 "강화" 결정이 부분표본 베팅.
- 라이브 토글·DB 백필은 **로컬(.env.live·decisions.db)에만** — 재클론/다른머신시 별도 적용 필요.
- 메모리 누적결론("selection 무엣지·청산이 레버")은 이번 패널도 대체로 재확인했으나, **2라운드는 "청산도 레버 아님(capture는 양날·생존편향), 진짜는 출혈차단+측정+passive"** 로 더 냉정해짐. selection으로 도망치지 말 것.
