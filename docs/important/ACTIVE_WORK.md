# Active Work

Updated: 2026-06-21

## 멀티에이전트 토론 — 시스템 생존·수익 극대화 (2026-06-21)

배경: /ultraplan 발 + 로컬 3자 토론(Opus 4.8 빌더 + codex gpt-5.5 xhigh + 사회/심판). 5단계 아젠다. 전체 로그: `plans/the-cloud-ultraplan-session-glimmering-tide.md`.

**핵심 결론(두 AI가 decisions.db 직접 쿼리로 독립 수렴):** 수익=Claude 재량청산에서만(US +173%p vs 자동청산 -82%p), 손실=자동청산(loss_cap). KR/US 동일. selection·새 엣지 없음(5+경로 확정). 살리는 길=새 엣지 아니라 **출혈정지+측정정직화+청산재량 보존**. 흑자 전환(net>0)은 어느 레버도 못 만듦 — 출혈 약간 줄일 뿐(거래당 gross +0.33% < 비용 0.5% 구조 불변).

**완료(이번 세션, 라이브 코드/config/봇 무변경):**
- ✅ `tools/cluster_halt_counterfactual.py` (read-only 측정) + `tests/test_cluster_halt_counterfactual.py`(7건). 운영성: threshold=4 cluster halt 순효과 net -2.75%p(약한 손실방어, 표본 US 8건), **threshold↓(3)면 right-tail +12.76%p 자르고 순효과 중립화 → 현 라이브 `STOP_CLUSTER_HARD_BLOCK_COUNT=4` 유지·인하 금지 확인**.
- ✅ 진단: 측정 배선(regime/mfe/net producer→sync) 정상(`tests/test_market_regime_capture.py` 등 13통과). regime 0/mfe 3.3%/net 25% 낮음은 코드버그 아니라 과거데이터+진입시 consensus mode 부재+빠른청산+net 입력부족. regime은 "수정보류·위조금지" 영역 미접촉.

**TODO (우선순위순, 전부 측정/검증 선행 — enforce는 표본·운영자 확정 후):**
- ⏳ **cluster halt enforce 결정** — counterfactual 표본 US 8→30건 누적 후 판정. threshold 인하 금지(도구 경고). 이미 라이브 4로 동작 중이라 "현행 유지"가 기본값.
- ⏳ **거래-바깥 축(두 AI 공통 맹점, net 영향 최대)**: ① **FX 스프레드 net 정직 반영**(우대0.2%/무우대2%×환전2회, KIS API 미노출→운영자 우대율 확인 선결) ② **거래 빈도 줄이기**(과매매=리테일 킬러, 비용 직접 정지). 4레버에 없던 축이라 가장 큰 미탐 레버.
- ⏳ **loss_cap전 Claude 재량 노출 shadow** — 자동절단 아님(+53%p는 사후지 천장 기각), 보호계약(hard stop/loss cap) 불변, KR 우선(KR Claude청산 +2.10%). 생존편향·약세장 검증.
- ⏳ **fragility sizing** — 측정복구 후 식별자(`zone_hit_context_changed`) tier 분리 shadow 재검증. codex 살림 vs 빌더 kill 미결(서로 다른 대조군). 켜면 전 PathB 0.5x 위험.
- ⏳ **USER_MANUAL 누수 분류** — KR -24.7%(2번째 누수). 운영자 패닉컷 vs 시스템 미흡 떠안기 라벨링(read-only).
- ⏳ **exit attribution 측정**(빌더 아이디어 A) — `exit_seen_by_claude`(청산 전 hold advisor 리뷰 통과 여부) 태깅 → 청산 주체별 net 정밀화.
- ⏳ **early-path shape**(빌더 아이디어 B) — 진입 후 첫 5~10분 fast-fail(MFE≈0 & MAE 벌어짐) 분류. selection 아닌 사후 경로, fragility 입력.
- ⏳ **KR 전용**: Plan A 3전략(momentum -21.5/gap_pullback -13.9/orp -7.9 전부 적자) shadow/축소, `claude_price`(+18.78%, 유일 흑자) 보존. US 로직 비접촉.
- ⏳ **[운영자 확정 필요] regime 런타임 보강** — consensus mode 비면 실측추세(`_tail_capture_regime`) fallback. "수정보류·위조금지" 영역 + 봇 재시작 필요라 운영자 승인 전 미착수.
- ⏳ **메모리 정정**: selection "무엣지" → "지수/비용은 못 이기나 미선정 대비 약한 위생 우위 존재(US 선정 forward_3d 중앙 +0.286% vs 미선정 +0.088%, KR 함정회피)". 알파 아닌 위생.

## 진입 후보 순위/타이밍 개선 (2026-06-16) — selection/entry-timing

운영자 통점: "매수 후보 순위가 너무 달라 타이밍 안 맞다. watch 15인데 trade_ready 5가 너무 놓치고, 5가 우선순위 정렬도 아니다." 분석으로 구조 원인 확정(`trading_bot.py` `_normalize_selection_meta_runtime` 슬롯배분 + `selection_compact_schema` 캡).

**완료(이번 세션, 봇 재시작 반영):**
- ✅ #1 trade_ready conviction 정렬: `_sort_trade_ready_by_priority` — Claude confidence 내림차순, stable, 캡/쿼터 불변. 토글 `TRADE_READY_PRIORITY_SORT_ENABLED`(기본 true). 테스트 `tests/test_trade_ready_priority_sort.py`.
- ✅ #2(부분) US 모멘텀 쿼터 상향: `US_RISK_ON_TRADE_READY_SLOT_MOMENTUM=3`(2→3, config). 스키마 계약 미변경.

**TODO (다음 집중 패스):**
- ⏳ **개장 진입 대기 단축** — KR/US 개장 후 momentum 진입이 `wait_base=45`분(`_effective_momentum_wait_window`, 5~60 clamp) 대기. 운영자 요청: "최초 장 판단(개장+5분, `*_OPENING_JUDGMENT_REFRESH_MIN=5`) 후 매수 가능하게." continuation/ATR/pullback과 얽혀 라이브 진입 직결 → 별도 집중 패스 + shadow/회귀 필수. 후보: momentum_wait를 개장판단 완료 후 단축, 또는 첫 판단 후 즉시진입 허용 게이트.
- ⏳ **#2 나머지: US compact trade_ready 캡 5→6** — `selection_compact_schema.py:50,283` `min(5,…)` 하드캡을 market-aware로(US 6, KR 5). 선택 계약/다수 테스트 변경 동반 → 신중.
- ⏳ **#3 모드별 캡 안정화** — RISK_ON/BALANCED/RISK_OFF 총슬롯(6/5/1) 세션 간 출렁임 shrinkage 완화. 점진(전략 갈아엎기 금지 원칙).
- ⏳ **#4 KR sector_play data_quality 결측 해소** — kr_sector_play가 강세장 KR 진입 채널인데 data_quality=missing으로 차단(sector_play_gate). MODERATE_BULL이 수익으로 연결되는 경로 복구. KR momentum 축소는 의도된 손실보호라 별건.
- ⏳ **B: C2/C3 후보 과열 페널티 크기 사후검증** (2026-06-20 enforce 적용분) — `runtime/candidate_pool_runtime.py` 과열 페널티(KR vol_ratio ≥1.5→-12/≥2.0→-20, change_pct ≥15%→-12)의 **크기가 net 최적인지 미검증**. 현재 값은 forward 상관 기반 추정치. **재시작 후 며칠** KR 후보 구성 변화(과열 후보 trade_ready 탈락률) + 그 후보들 실제 forward를 측정해 페널티 과/소 판정. 과하면 좋은 후보까지 깎임(단 base bonus 25~30 위에서 깎여 완전배제 아님), 약하면 효과 미미. 임계(1.5/2.0/15%)도 재검토. shadow 시뮬 또는 라이브 후보로그 비교. **C2 KR전용**(US vol_ratio 실값 부재 — US producer 연결이 선결, 별도 후속). 테스트 `tests/test_candidate_overheat_penalty.py`.

## 거래비용 분석 — 2달 net 본전의 진짜 원인 (2026-06-13)

KIS 실계좌 기간손익 전수(207건, 4월~6/13) 분석 결론:
- US 매매 **net 실현손익 = +3,440원 (2달 사실상 본전)**, 달러 기준도 +$4.91.
- 매매차익(gross, 수수료 전) +308,695원(+0.44%) ↔ **수수료+세금 -305,255원(-0.44%)** =
  수수료가 매매차익의 99%를 회수. 매매 실력(승률 39%·손익비 양호)은 정상.
- 수수료: 한투 미국 **온라인 0.25%(편도)=왕복 0.5%**, 우대 이벤트 없음(운영자 확인).
  측정 중앙값 0.497% 일치. 거래금액 무관 정률.
- 환율: 부차적(USD/KRW 둘 다 본전, 변동 -4,057원). **환전 스프레드/우대율은 KIS API에
  안 나옴**(잔고·기간손익에 고시환율만, `exrt=0`) → 운영자 앱/명세서에서 우대율 직접 확인.

후속 처리:
1. ⏳ **6/24 "진입 하한" → "청산 net 손익분기 인지"로 방향 전환**. 진입 reward 하한은
   효과 작음(진입 95%가 이미 target 3%+). 문제는 청산 — 전체 38%만 gross>0.5%(수수료
   초과), 62%가 net 본전/손실. hard_stop(+0.5%→net 0)·MFE_breakeven(~0→net -0.5%)
   "본전 탈출" 청산이 실제 손실.
2. ⏳ **net 인지 청산 shadow 계측** (행동 불변): profit_ladder floor·MFE_breakeven 손익분기를
   `entry + 왕복수수료(0.5%) + 최소마진` 기준으로 환산했다면 어떻게 바뀌었을지 기록만.
   profit_ladder는 US PathB 보호영역이므로 shadow 선행 필수, enforce는 계측 후.
3. ⚠️ pre_close/loss_cap은 carry/손실회피 목적이라 net 무관하게 청산 유지(수수료 핑계로
   손절 미루기 금지). "수수료 아끼려 연장 보유"는 1주 지평 손해라 금지(2026-06-13 시뮬 확정).
4. ⏳ [운영자] 환전 우대율 확인 + (가능하면) 한투 미국주식 수수료 우대 재문의.

## Hold advisor 실측 prior 주입 + 신뢰 단계 승격 트랙 (2026-06-13 운영자 승인)

배경: 2026-05~06 청산 원장 전수 재채점 — HOLD 실패 26건 중 8건(-19.4%p, 크기 기준 ~40%)은
판단 시점에 인지 가능한 신호(시장 급반전 4건, 손실 상태 HOLD 4건)가 있었고, 성공 HOLD는
전부 시장 상승일이라 보수화 prior로 죽는 케이스 없음. HOLD 성패는 장세 베타(상승일 +0.33%
vs 하락일 -0.77%, 종목 상관 +0.02).

1. ✅ 실측 prior 4종 system 주입 (`minority_report/hold_advisor.py` `_MEASURED_PRIORS`):
   급반전/약세 SELL 기본, 손실상태 HOLD 금지(촉매 없으면), 연장 1~2세션 한도, 갭 한계.
2. ✅ portfolio_context (`runtime/pathb_runtime.py::_pathb_portfolio_context`): 동시 보유
   수/티커를 advisor_context_v2에 제공 — 6/3형 메가캡 7종 동시 carry 집중 인지용.
   테스트: `tests/test_hold_advisor_priors.py` 7건.
3. ⏳ **6/27 재채점**: 오늘과 동일 잣대(HOLD 시점가 vs 최종 청산가, 장세 분리)로 신/구
   advisor 비교. 통과 기준: HOLD 원장 기대값 +0.08% → +0.4%/건, 성공 케이스 보존.
4. ⏳ 승격 조건: 3 통과 시에만 advisor 권한 확대(gain-lock 창 완화 등) 검토. 그 전에는
   강제 전환 가드·loss_cap·hard_stop 현행 유지 (시뮬 근거: 연장 보유 13건 중 10건 악화).
5. 매도 엔진 전수 판정 (2026-06-13 확정): loss_cap/ladder/pre_close/hard_stop/target 전량
   청산 모두 현행 유지. 1주 지평에서 전부 정당, target 연장 시뮬 탈락. 재론 시 이 데이터 먼저.
6. ⏳ 7월 말 (n≥30): target 청산 가상 연장 재측정 (시장 모드 필드 포함). 손실상태 HOLD
   보수화는 6/24 관찰 항목.

## 동결 해제 + 6/17 안건 조기 처리 (2026-06-11 운영자 지시 "전부 진행해")

This is the single active work ledger. One-off plans and generated reports are removed after their unfinished work is absorbed here and in [core/TODO_ROADMAP.md](core/TODO_ROADMAP.md). Detailed improvement sequencing from the latest DB/code review lives in [IMPROVEMENT_WORKLIST_20260607.md](IMPROVEMENT_WORKLIST_20260607.md). Completed implementation notes belong in [core/DEVELOPED_WORK.md](core/DEVELOPED_WORK.md) or Git history, not in the active backlog.

## 동결 해제 + 6/17 안건 조기 처리 (2026-06-11 운영자 지시 "전부 진행해")

당초 동결이었으나 운영자 지시로 즉시 착수 가능 항목 7건을 6/11에 전부 처리 완료:
1. ✅ 함정구간 가드 PULLBACK_WAIT 확장 (7f70a8a) — 역산 n=26 중앙값 -1.18% 근거
2. ✅ net 원장 KIS 체결가 통일 + 저장결측 3종 (cc5f7f4)
3. ✅ 일 단위 forward 라벨 백로그 자동화 (e7c3b0f) — daily_forward 0 → 51,229건
4. ✅ C1 selection 프롬프트 캐시 활성 (0745379) — 실 A/B 검증, 정적부 1,342토큰
5. ✅ 시장 급반전 감지 shadow (1ce5b9b) — enforce 전환은 6/24 판정 유지
6. ✅ session_evidence_degraded 분리 (4241e32)
7. ✅ 개장 판단 refresh 중복 방지 + guardian 테스트 env 누수 수정 (e5643c3)
→ 전체 회귀 2,424 passed, preflight FAIL 0. **봇 재시작 필요** (현 프로세스는 09:32 기동).
→ 6/17에 남는 것: A1 존 4지표 판정(데이터 필요), A1 코드 게이트 여부.
→ 6/24 판정 유지: 채널 ROI 쿼터, rel_vol 게이트 연결, mega_gap 승격, 급반전 enforce,
   PathB 진입 하한(+2% 미만 진입 평균 +0.01% — 역산에서 신규 발견) 검토.

## (이력) 변경 동결 + 관찰 주간 (2026-06-11 ~ 06-16, 운영자 확정 → 6/11 운영자 지시로 해제)

2026-06-10~11 대규모 배포(12커밋: 게이트 3종, KR off, net 원장, A1 존 규칙, rel_vol 체인,
attribution, mega_gap watch, cap 재설정 15/15/5, stop cluster 5, 1주 예외 100만) 직후라
**효과 귀속을 위해 최소 5일간 신규 변경 동결.** 발견된 개선 후보도 아래 일정으로만 처리한다.
예외: 차단급 장애(체결 고갈, 게이트 오작동, 비용 폭증)의 긴급 대응만 허용.

**관찰 주간 일과 (매일 아침, 변경 없이 측정만):**
- 신규 플랜 존 깊이 분포 (A1 준수율 — 기준가 대비 -0.5% 이하 비율)
- rvol 표기 후보의 선택률 / judge 호출 대상 구성 (단골 비중 감소 확인)
- 체결 수 (소급 시뮬 예측 "기존의 85%" 대조)
- net 손익 첫 기록들 (pnl_krw_net_est — CPNG/IONQ 청산 시)
- 일일 API 비용 (R1 Haiku 반영 후 ~$4± 예상, KR select 49콜/일 낭비 추이)

**2026-06-17 — 1차 판정일 (작업 재개, 그룹 우선순위):**

지배 원칙(2026-06-11 확정): ① 데이터가 결정한다 — 변경은 판정일에만, 그날도 표본 역산 선행.
② 경로 일관성 — 가드는 진입의 전 경로(selection·judge·bridge)에 동일하게. 신설 시 경로 체크 필수.
③ 판단보다 구조 — Claude 판정 품질은 6/10 손실 리뷰로 입증됨(IONQ judge가 A1 규칙·무효조건 명시).
투자처는 프롬프트가 아니라 게이트 일관성 → 국면 인지 → 자금 구조.

A그룹 — 6/10 손실 직접 처방 (오전):
1. 함정구간(+2~5%) 가드를 PULLBACK_WAIT까지 확장 — mega_gap 가드 패턴 재사용, judge 산출도
   selection meta 합류라 한 곳 수정으로 양 경로 커버. 6/10 손실 3건 중 2건(IONQ +4.6%/CPNG +4.8%
   진입)이 이 우회로 발생. **선행: 과거 PathB 체결 중 진입 시점 +2~5% 그룹 성과 역산 후 enforce.**
2. 저장 결측 수선 + 진입가 기준 통일 (net 원장 완결성, 2026-06-11 운영자 확정: KIS 체결가 기준):
   - **진입가 기준 = KIS 체결 평균가로 통일** — 현재 일부 fill 경로가 주문가(지정가)를 기록해
     브로커 실체결가와 불일치(CPNG 15.645 vs 15.75), net이 gross보다 후한 모순 발생
     (6/9 CPNG net -1.01% > gross -1.29%). mark_filled 전 경로 브로커 avg 우선 +
     `entry_price_source` 표기, _close_cost_meta는 브로커 포지션 단가 → plan 기록가 순.
   - judge 출력 스키마에 `reference_price` 필드 부재 → judge 출신 플랜 전부 ref=None
     (IONQ 사례 — 판단문엔 현재가 59.72 명시, 받아 적을 칸이 없었음). 스키마 추가 또는 등록 시 백필.
   - ORDER_ACKED 중 청산 시 entry 유실 — 매수 fill reconcile(주기)과 hard stop(즉시)의 레이스
     (FUN 사례: 00:38 주문→00:56 손절, net 미기록 재현 확인). mark_closed에서 plan에
     entry 없으면 브로커 체결가/포지션 단가로 백필.
   - 후속 확인: KIS 체결 내역 API에 실제 제비용 필드 존재 여부 → 있으면 수수료도 추정(0.25%)
     대신 브로커 실비용으로 전환.

B그룹 — 예정된 데이터 판정:
3. A1 존 규칙 4지표 판정 → 유지 / -0.3% 완화 / 강화 (NOK -0.74% 깊이 = 신규 플랜 첫 준수 사례)
4. A1 코드 게이트(등록 단계 zone vs 기준가 검사) 추가 여부 — 판정 결과 따라
5. candidate outcome 라벨 버그 수정 (daily_pending 1,551건 `target_at` 빈 값)

C그룹 — 비용:
6. ~~KR rescreen 임시 감속~~ — 해소 (2026-06-11 운영자 결정으로 KR PathB 재개, 30분 rescreen이 유효해짐)
7. C1 selection prompt caching 착수 (paper 검증 포함 — P1 항목 참조)
8. KR 개장 판단 중복 검토 — 09:05 세션판단 + 09:07 market_open_refresh가 2분 간격 풀세트(12콜) 중복 (일 ~$0.3)

D그룹 — 설계만 (라이브 연결은 6/24):
8. 시장 급반전 보호 미니판 — Claude invalid_if의 "broad market reverses"를 시스템이 모니터링하지
   않는 갭(IONQ 패인). 이미 수집 중인 market risk shadow(`ENABLE_MARKET_RISK_SHADOW=true`) 기록
   검토 → 임계 설계 (장중 지수 급락 시 PathB 전 보유 INTRADAY_REVIEW 트리거 + 신규 제출 일시 보류.
   강제 청산 아님 — 보호영역 비접촉). 7월 국면 스위치의 선행판.

금지 항목 (6/10 리뷰 근거): judge·청산 엔진·loss_cap -2% 변경 금지 — 손실의 원인이 아님이 확인된 곳.

**유사 시스템 차용 패턴 (2026-06-11 운영자 승인, ③은 즉시 구현 완료):**
- ③ ✅ 벤치마크 정직성 지표 — tools/benchmark_compare.py (시스템 net vs SPY/QQQ + 알파).
  첫 실측: 최근 7일 시스템 -0.78% vs SPY -4.18% = 알파 +3.4%p (하락장 방어 작동 증거).
  매주 채점·6/30 판정에 포함
- ① 테마 동시 진입 제한 → 6/17: 과거 플랜 섹터 동시성·동반 손실 역산 후 같은 세션
  같은 섹터 PathB 신규 2건 제한 게이트 (6/10 반도체 5건 동시 승인 사례)
- ② 에쿼티 커브 스로틀 → 6/24: 연속 손실일/주간 드로다운 시 사이즈 자동 절반·회복 시 복원
  (stop cluster의 다일 버전). 임계는 "연패 중 거래 기대값" 역산 후
- ④ 타임 스톱 shadow → 6/17: N세션째 손익 정체 포지션의 최종 결말 측정만 (행동 변경 없음)

**웹 리서치 차용 후보 (2026-06-12 등록, 구현은 판정일에):**
- [6/24] lesson_candidates 계층화 + 중요도 감쇠 — FinMem(arxiv 2311.13743) 차용.
  오래되고 재확인 안 된 교훈 자동 강등 → brain 자동 승격의 안전한 전 단계
- [6/24] 존 터치 시점 거래량 방향 확인 — "감소 거래량 눌림 → rvol 급증 반등" 패턴.
  rel_vol 3단계에서 judge 입력에 추가 → 칼날(IONQ형) vs 건강한 눌림 구분
- [7월 국면 스위치 설계 입력] Statistical Jump Model 국면 신호(arxiv 2402.05272) —
  지수+VIX 2변수 경량 통계 국면을 Claude consensus의 독립 제2 의견으로
- (참고) fractional Kelly 문헌이 7월 사이징 설계(고정 기반+0.7~1.5x 보정) 지지 확인.
  full Kelly 금지. TradingAgents류 다중 에이전트 구조는 기보유 — 신규 액션 없음

**모델/데이터/ML 차용 (2026-06-12 운영자 방향 확정):**
- [6/17+, 측정 선행] judge·hold advisor 상위 모델(Opus 4.8) 시험 — 운영자: "성능 측정부터".
  측정 설계: 같은 입력 N=20~30건 Sonnet vs Opus 병행 호출(paper) → 판정 일치율,
  존 깊이/rr 분포, invalid_if 구체성, 지연, 비용 비교 후 전환 판단. 예상 비용 +$0.1~0.2/일
- [6/17+] Finnhub 실적 캘린더 연결 — 인벤토리 완료(2026-06-12):
  · 무엇: /calendar/earnings (date·symbol·epsEstimate·BMO/AMC), 무료 접근 실측 확인(주 61건)
  · 얼마나: 일 1회 갱신 + 주 1회 풀 스캔 — 무료 티어(60콜/분) 내 미미
  · Claude 주입 3곳: ①보유/플랜 종목 실적 D-1/D0 경고(hold advisor·digest 한 줄)
    ②후보 라인 earn=D-1 토큰(PEAD 정책상 earnings_date 즉시 노출 허용)
    ③실적 D-1 신규 PathB 등록 보류 게이트(ORCL 거래정지 사건 처방)
  · 기존 사용: quote/candle/profile/company-news — 캘린더만 신규
- [등록만, 판단 보류] 자체 ML 후보 스코어러 — decisions.db+daily_forward 라벨(51k건)로
  XGBoost trainer 점수 학습 버전. 라벨 2~3개월 축적 후 판단 (운영자: "등록만 해놓고 판단부터")

**입력 품질·교훈 컨베이어 일괄 구현 (2026-06-12 운영자 "전체 개선 진행" — 완료):**
- ✅ B1/B2 분봉 표적 백필 (judge·evidence 시점, TTL 10분+빌드당 6건) — KR NO_EV_PACK 뿌리 처방
- ✅ B3 US evidence 식탁: compact 모드 + 한도 8→16 (드리프트 관찰 — C1 절차 동일)
- ✅ A1 교훈 승인 손잡이: 텔레그램 /lessons 목록·승인·기각, state/lesson_approvals.json 영속
- ✅ A1' 승인 게이팅 통일: LESSONS_REQUIRE_APPROVAL=true — select 주입·분석가 주입 모두
  승인 후에만 (무승인 권고문 14일 무효과 실증 근거)
- 조사 종결: B4 US 호가 = KIS 무료 시세 범위 밖(유료 구독 필요) → 보류, KR만 운영.
  B5 KR sector = 랭크 응답 정규화에 업종 필드 부재 → 6/24 추출 조사
- 남은 A2(승인 시 env 게이트 자동 제안) — 6/24 (FinMem 감쇠·국면 태그와 함께)

**Claude 입력 품질 감사 후속 (2026-06-12 점검에서 발견, 미해결분):**
- [6/17] KR post-open features 수집 빈곤 — evidence pack 빈 값(NO_EV_PACK)·judge ret_*m
  결측·exec= 0건의 공통 뿌리는 플래그/배관이 아니라 KR 분봉 피처 수집층.
  session_evidence_degraded 집계가 이미 측정 중 — 수집 보강 설계 필요
- [6/24] spread_bps 전수 배선 — judge 시점 KR 보강은 적용됨(2026-06-12),
  스냅샷 전수 배선은 KIS 호가 콜 부하 검토 후 (US는 신뢰할 bid/ask 소스 확보 필요)
- [6/24] sector= KR 후보 라인 보강 — 동반 손절(반도체형) 인지용

**미사용 시스템 전수 감사 결과 (2026-06-12, 운영자 "A~B까지" 지시 처리 완료):**
- ✅ ml/forward_updater 실행자 부재 → session_close 자동 연결 + 백로그 즉시 해소(5,198행)
- ✅ PEAD 수동 리뷰 3종 데이터 수행 완료: 티어 규율 완벽(high만 sign 22건), surprise 22건
  전수 정확, prompt 누출 0 — **운영자 최종 스위치(prompt_surprise_enabled) 결정 대기**
- ✅ buy_time_confirm 재분류: 죽음 아님 — Path A 전용+신선 선정 스킵 구조라 발화 기회가
  없던 것 (Path A 매수 재개 시 자동 가동). 현행 유지
- ✅ KR confirmation gate 재분류: watch_trigger 시스템으로 가동 중 (일 375건)
- 휴면 확정: preopen_planb_bridge — seed 공급 8일간 0 (장전 selection이 PULLBACK_WAIT를
  안 냄, 상류 의존성 마름). [6/17] 장전 프롬프트에 PULLBACK_WAIT 유도 여부 결정
- 휴면 의심 잔존: discovery 슬롯(풀 충분 시 미발동 추정), KR post_rank, US quality shadow
  — [6/17] 확인

**~2026-06-24 — 2차 데이터 판정:**
- 채널 ROI (candidate_source 2주치) → most_actives/day_gainers 쿼터 재배분
- rel_vol 분포·예측력 검증 → 전략 게이트 연결 여부 (US PathB 보호영역, `MD 위반 사항` 절차)
- mega_gap watch forward → 진입 채널 승격 여부
- D그룹 시장 급반전 보호 라이브 연결 여부 (shadow 검토 결과 따라)

**2026-06-30 — 월말 판정 기준 (운영자 합의 2026-06-11, 감정 배제용 사전 확정):**
- 통과 기준 (둘 다 충족): ① 거래당 net 평균 >= +0.3% (수수료 후, pnl_pct_net_est 기준)
  ② 최악 하루 >= -3만원 (클러스터 방어 작동 증명)
- 통과 → 7월 구조 단계 진행, 사이징·러너 검증 후 증자(씨드) 논의 개시
- 미달 → 증자 금지. 선정/체결/청산 단계별 손익 분해 → 원인 수선.
  반복 미달 시 축소 경로(US 단독, paper 병행) 검토
- 원칙: "증자는 보상이지 베팅이 아니다" — 검증 전 증자 금지 (7월 단계 #5와 동일)
- 운영 수칙: 손익 확인은 주 1회 (일 단위는 노이즈), 매일 채점은 구조 작동 여부만

**2026-07 — 구조 단계 (6월 net 원장 마감 채점 후, 순서 고정):**
1. 확신도 사이징 — rr/rvol/채널 팩터별 에지 확정 후 50만 고정 → 0.7×~1.5× 차등
2. 러너 부분 보유 (core+runner 분할, ladder 60~70% 청산 + 잔여 trailing multi-day) — 보호영역, shadow 선행
3. 국면 스위치 (추세/횡보 × 변동성 → 게이트 세트 전환) — 나쁜 달 방어
4. 실적 캘린더 연결 (보유 포지션 실적 경고 + mega_gap 이벤트 태그, PEAD 인프라 재사용)
5. 1·2가 데이터로 확인된 뒤에만 증자(스케일) 논의 — 검증 전 증자 금지

**운영자 대기 (날짜 무관):** 환전 우대율 확인(US_FEE_RATE_PER_SIDE 조정).

**KR PathB 재개 (2026-06-11 운영자 결정, 동결 예외 — 운영 설정 변경):** 신규 게이트 체계
(존 -0.5%·rr>=1.5·stop cluster 5) 하에서 KR 재검증. 6/10 중단 사유였던 KR gross 음수는
구 게이트 시절 데이터라는 판단. 주의: A3 함정구간·rel_vol·mega_gap은 US 전용이라 KR엔 미적용 —
KR 성과는 존 규칙+rr 게이트만의 효과로 읽어야 함. preflight 정책 KR-on/US-on 복원 완료.

## Scope Guard

- This cleanup does not change `.env*`, `config/v2_start_config.json`, live PathB gates, order size, max positions, cooldown, confidence, slippage, hard stops, broker truth priority, or `state/brain.json`.
- Selection quality, execution/risk behavior, broker truth, and performance-sync work stay separate.
- Protected PathB areas still require an `MD 위반 사항` report before runtime behavior changes.

## Direct Verification Snapshot

- `tools/sync_v2_learning_performance.py` has code for `audited_broker_backfill`, `portfolio_realized`, and `learning_allowed` separation, and lesson prompt injection blocks stale/manual-review `truth_status`.
- Live `data/ml/decisions.db` is not fully synced yet: `v2_learning_performance` is missing `portfolio_realized` and `strategy_attribution`; current `CLOSED_AUDITED_BROKER_SELL` rows are 5, all with `exit_price=NULL` and `learning_allowed=0`.
- Read-only sync dry-run returned `selected=550`, `filled=205`, `closed=182`, `skipped=0`, `written=0`, `update=512`, `strategy_attribution_counts.audited_broker_backfill=8`.
- `tools/audit_ticker_selection_attribution.py --mode live --market ALL --sample-limit 20` returned `traded=48`, `contaminated=23`, `missing_execution_id=23`, `watch_only_traded=14`, `exact_backfill_candidates=0`, `watch_split_reviews=10`, `no_touch=10`.
- `data/audit/candidate_audit.db` still has `daily_pending=1551` outcome rows and many `audit_sparse`/`insufficient_samples` rows, so candidate audit daily outcomes are not a clean learning basis yet.
- KR `trade_ready` carry is implemented in `trading_bot.py::_apply_kr_trade_ready_carry()` and covered by `tests/test_candidate_action_live_mapping.py` carry/veto/TTL/source tests. The carry implementation itself is no longer active work.
- General `INTRADAY_REVIEW` cooldown/daily max is implemented in `trading_bot.py` with `skipped_daily_max_regular`, `skipped_cooldown_regular`, and emergency bypass reasons. The throttle implementation itself is no longer active work.
- KR/KIS evidence logs count provider/prefetch timeout and KIS 500 errors, but there is still no `session_evidence_degraded` split from ticker-level `fail_closed`.
- Static live ops config is aligned in code: `US_EARLY_ENTRY_SOFT_GATE_END_MIN=60` and `start_live_stack.bat` runs broker truth scheduler with `--refresh-interval-min 2 --ttl-sec 180`. Runtime restart/freshness still needs live preflight confirmation.
- KR PathB reconcile config is aligned in current files (`KR_PATHB_SELECTION_RECONCILE_MODE=enforce` in `.env.live` and `config/v2_start_config.json`), but the latest live runtime snapshot `logs/config/effective_config_20260606_024649_live.redacted.json` still has `KR_PATHB_SELECTION_RECONCILE_MODE=shadow`. No code path was found that rewrites it to shadow after startup, so treat this as restart/startup-env/config-path verification first.
- Recent KR selection evidence shows `trade_ready=8`, `signal_fired=0`, `traded=0` for 2026-06-01 through 2026-06-05. Recent trade-ready rows are `gap_pullback=5`, `opening_range_pullback=2`, `mean_reversion=1`, all no-signal.
- KR ORP strategy logs are dominated by `orp_entry_window_expired=395`, so KR `NO_SIGNAL` review must include ORP selection-time versus entry-window expiry analysis before any ORP window/threshold change.
- System synthesis on 2026-06-07 promotes KR `NO_SIGNAL` / ORP timing reporting from P1 to P0 because it is now a gating analysis before any KR live expansion or strategy threshold work. Five recent sessions are only a smoke/reproduction window; primary judgment must use a 30-day window and full available live history.
- PathB miss quality shows US `INVALID_PRICE` cancel rows `n=29`, `zone_reentered=26`, `avg_mfe_30m=+1.222%`; this is a P0 diagnostics/reporting item, not a broker-truth or sizing-policy relaxation item. Full available `pathb_miss_quality` is the baseline window, with recent-window comparison added for drift.
- Working-tree implementation on 2026-06-07 added read-only reports `tools/kr_nosignal_orp_report.py` and `tools/pathb_invalid_price_miss_report.py`, with focused tests in `tests/test_kr_nosignal_orp_report.py` and `tests/test_pathb_invalid_price_miss_report.py`.
- KR no-signal read-only live run reproduced recent `selection=1299`, `trade_ready=8`, `signal_fired=0`, `traded=0`, `no_signal=8`. Primary 30-day run returned `trade_ready=37`, `signal_fired=6`, `traded=6`, `no_signal=31`; full available returned `trade_ready=130`, `signal_fired=24`, `traded=20`, `no_signal=106`.
- PathB `INVALID_PRICE` read-only live run reproduced US full-available baseline `n=29`, `zone_reentered=26`, `zone_reentered_rate=89.66%`, `avg_mfe_30m_pct=1.2224`, `avg_mae_30m_pct=0.0294`. The same rows are also the current 30-day recent window because the table's available US `INVALID_PRICE` history is 2026-05-11 through 2026-05-26; all 29 rows are `plan_current_missing` with follow-up quotes available, and all are `cent_tick_plausible`.
- Candidate audit schema has `candidate_source`, but recent live rows still have it mostly blank while `source_file` is populated. Treat source attribution fallback for new audit rows as a separate code/test item from ticker-selection execution attribution review.

## P0 / Do First

| Area | Remaining Work | Acceptance |
| --- | --- | --- |
| V2 performance sync | Back up `data/ml/decisions.db` and `data/v2_event_store.db`, rerun live dry-run, execute `python tools/sync_v2_learning_performance.py --market ALL --runtime-mode live`, then verify audited broker sell rows. | Post-write DB has `portfolio_realized` and `strategy_attribution`; audited broker backfill rows have native exit price/qty where available, `portfolio_realized=1`, `strategy_attribution='audited_broker_backfill'`, and `learning_allowed=0`. |
| Performance report recalculation | Recompute KR/US, PathA/PathB, `strategy` vs `audited_broker_backfill`, `portfolio_realized=1`, and `learning_allowed=1` views after sync. | Realized portfolio loss includes audited broker backfill, while strategy PF/promotion/lesson inputs exclude non-strategy or `learning_allowed=0` rows. |
| Ticker selection attribution | Review the 10 `watch_only_traded` split candidates, 3 time-delta rows, and legacy-only `selection_log_id=7742` IREN row. Keep no-touch rows excluded from learning rather than auto-fixing them. | No `watch_only` row is flipped into `trade_ready=1`; only causal execution evidence may create/link a separate execution row; remaining no-touch rows are excluded in analysis queries. |
| Candidate audit source attribution | Standardize `candidate_source` fallback for new live audit rows across prompt, excluded, screener-filter, and runtime-filter write paths. | New rows do not leave `candidate_source` blank when `source_file`/stage source is known; existing legacy rows are not bulk-mutated without audited remediation. |
| Candidate audit outcome freshness | Run daily outcome catch-up in dry-run first, then update outcomes if counts are expected. Re-check `candidate_audit.outcome_update` and daily pending rows. | `daily_pending=1551` is reduced or explained; candidate audit daily outcomes are not used as KR selection evidence until freshness is restored. |
| Live ops runtime reflection | After live stack restart/refresh, run live preflight and broker truth scheduler once. | `US_EARLY_ENTRY_SOFT_GATE_END_MIN` runtime drift is gone, `KR_PATHB_SELECTION_RECONCILE_MODE` runtime snapshot is `enforce`, and KR/US broker truth snapshots are fresh within TTL. |
| KR/KIS evidence fail-closed split | Separate ticker hard fail-closed from session/provider degraded warning. Preserve full hard fail-closed for provider disabled, session-open resolve failure, and complete-zero cases. | `minute_complete` ticker evidence remains confirmed during partial KR timeout; missing/partial tickers alone get fail-closed; logs expose session degradation separately. |
| KR `NO_SIGNAL` / ORP report output review | Use `tools/kr_nosignal_orp_report.py` output to decide whether KR no-signal is selection timing, strategy condition, evidence quality, or risk/order/broker gate. | Any KR threshold/window proposal cites primary/full_available distributions and forward-outcome evidence; no shared strategy change proceeds without US PathB impact review. |
| PathB `INVALID_PRICE` remediation design | Use `tools/pathb_invalid_price_miss_report.py` buckets and lifecycle metadata to identify whether `current_at_plan` misses come from price provider, adapter capture, stale quote, or unit scale. | Proposed fix targets diagnostics/price-source quality only; broker-truth fail-closed, sizing reason split, slippage cap, and order submit policy remain unchanged. |

## P1 / Develop Next

| Area | Remaining Work | Acceptance |
| --- | --- | --- |
| Claude API 최적화 — selection prompt caching (2단계, 2026-06-10 검토 반영) | **실측 (2026-06-09):** Sonnet 4.6 캐시 최소 1,024 tokens (1,369 tokens 테스트 cache_creation=1370 확인). `input_tokens`는 캐시 미포함 일반 토큰만. **추가 실측 (2026-06-10):** 호출 간격 — select KR 중앙값 7.8분/US 27분, 5분내 연속 호출 KR 38%/US 24% → 기본 5분 TTL 히트율 1/4~1/3, **`ttl=1h`(쓰기 2배) 필수** (60분내 88~95%). 예상 절감 월 $5~10 (정적 prefix ~3k tokens 가정). **범위 확정:** select_tickers만. R1은 Haiku 4.5(문서상 최소 4,096, 미실측) 제외. R2는 정적부 실측 후 판단. hold_advisor 캐시는 보류 — 절감 상한 월 ~$2, prefix 인위 확장은 본말전도. (2026-06-10 완료: hold_advisor/single_symbol_judge 계약 인라인 중복 제거 + system 블록 무조건 첨부, no-op 주석 명시.) **구현 순서:** ① select_tickers 재구조화 — 정적(계약 4종+규칙+JSON 스키마)을 system 블록 + cache_control ttl=1h, 동적(candidates/digest/lesson/feedback/phase)만 user; ② 규칙 블록 보간 변수(watch_max/trade_max/slot_text)가 세션 내 불변인지 확인, 가변이면 user로 분리(아니면 prefix 무효); ③ messages.count_tokens() preflight 1,024+ 확인 + 운영 중 cache_read_input_tokens>0 로그; ④ smart skip semantic signature(ticker+action 기반) 영향 없음 확인; ⑤ single_symbol_judge messages.parse는 캐시와 분리 적용. **선행 조건:** A1 buy zone 판정(2026-06-17경) 이후 시작. paper/replay에서 재정렬 전후 동일 풀 trade_ready 일치율·parse 성공률 비교, 라이브 직행 금지. | select_tickers cache_read_input_tokens > 0; trade_ready 일치율 회귀 없음; compact parse error rate 증가 없음. |
| KR selection after trade-ready carry | Measure carry effects by day: `trade_ready_count`, `signal_fired`, `traded`, `NO_SIGNAL`, and `watch_only` transitions. | If `trade_ready` rises but `NO_SIGNAL` dominates, move to KR strategy signal review; if signals rise but orders do not, split risk/order/broker gate analysis. |
| KR screener exposure/ranking | Analyze hard-cap cutoff, prompt-pool exclusion, and watch-miss forward outcomes. | Add only bounded live-limited overlay exposure if evidence supports it; do not auto-promote discovery/overlay candidates to BUY_READY. |
| Lesson candidates | Add `basis_source`, `basis_max_session`, `basis_synced_at`, and `truth_status` to manual/pinned lessons after refreshed ledger sync. | Only refreshed ledger-backed lessons become `truth_status=fresh`; no automatic `state/brain.json` promotion. |
| Hold advisor follow-ups | Review `PRE_CLOSE_CARRY` challenge cost, add pending intraday recheck retry state machine if repeat-call risk is confirmed, connect missed-runup bucket report, and add read-only US PathB block reporting. | Hard stop/loss cap/broker safety bypass retry throttles; no PathB profit ladder, pre-close, or AUTO_SELL_REVIEW cooldown behavior changes without protected review. |
| Existing strategy-flow backlog | Keep actual-prompt outcomes, entry/exit shadow, bucket/source/score quality, zero-holding fixtures, PathB TTL/order matching QA, sizing reason split QA, canonical fallback exclusion, brain/sub-screener guards, runtime tuning cleanup, and PathB fill-truth monitoring visible until commit/QA or live DB evidence closes them. | Each item closes only with direct code/test or DB/log evidence, not by dated plan text. |
| US vol_ratio 입력 품질 (선택품질 #7-2 후속) | US 후보 `vol_ratio`가 Yahoo/FMP 스크리너에서 `1.0` placeholder로 고정(`kis_api.py`). 실값은 계측이 아니라 실행 영향 항목(`bot/bucket_classifier.py` US `>=1.1`, `strategy/continuation.py` `>=1.5`, `strategy/mean_reversion.py`, `strategy/volatility_breakout.py`가 소비). 별도 producer로 US 일평균 거래량 수집 + 세션 진행률(elapsed/total) 보정 vol_ratio 산출 → producer→writer→전략 소비처 흐름 일괄 검증. naive 실값(장중 부분거래량/일평균)은 placeholder보다 왜곡되므로 금지. | 실값을 live 전략에 연결하기 전 US PathB 성과 데이터 확인 + shadow 관찰 선행. 장중 부분거래량 보정이 검증된 producer만 소비처에 연결. selection/execution 분리 유지, US PathB 보존 영역 변경 시 `MD 위반 사항` 보고. |

## P2 / Observe Only

| Area | Rule |
| --- | --- |
| 인스타그램 일일 장판단 콘텐츠 (2026-06-13 운영자 아이디어, 플랜 등록만) | 컨셉: "AI 트레이딩 봇의 일지" — 일일 시장 모드 판단+근거, 전일 거래 사후 회고, 주간 누적 곡선을 이미지로 자동 생성해 **장 마감 후** 게시. 파이프라인: `logs/daily_judgment/` JSON → 이미지 템플릿(Pillow/HTML 렌더) → Instagram Graph API(비즈니스 계정), session_close 훅. **수위 규칙(필수)**: 종목+매수존/목표가 사전 공개 금지(유사투자자문업 리스크), 사후 회고·시황 중심, 고정 면책 문구, 존 가격·진입 로직 상세 비공개. 수익화(구독/멤버십) 시점에는 유사투자자문업 신고 재검토. |
| KR shadow veto | Design only after performance sync and report recalculation. Use entry-time-known features only and write shadow records, not live blocks. |
| US loss-cap cluster shadow | Recalculate after refreshed ledger. Do not mix `CLOSED_AUDITED_BROKER_SELL` into US loss-cap cluster judgment. |
| US KIS ranking/intraday primary | Keep Yahoo/FMP/AV and yfinance primary until KIS shadow/smoke coverage, latency, overlap, and rate-limit gates pass. Broker truth never falls back to Yahoo/FMP/AV. |
| Prompt overlay / PLAN_A, KR confirmation, KR first-entry/exit overlay | Observe until sample gates pass: enough trading days, labeled outcomes, concentration checks, and broker-fill-aware replay. |

## Protected Boundaries

- US PathB `CLOSED_CLAUDE_PRICE_PRE_CLOSE` and `CLOSED_PROFIT_LADDER`.
- PathB `AUTO_SELL_REVIEW` HOLD cooldown guard.
- PathB broker-truth entry fail-closed behavior.
- PathB sizing reason split and one-share/early-gate sizing policy.
- Zero-holding stale reconcile.
- KIS order normalization with `remaining_qty` preservation.
- Path A/Path B `RouteDecision` contract.
- Broker truth priority and market quarantine behavior.
- `state/brain.json` no direct automatic policy write.

## Removed From Active

- KR `trade_ready` carry implementation: code and tests exist; only post-carry measurement remains.
- General `INTRADAY_REVIEW` cooldown/daily max implementation: code and tests exist; only operational observation and pending-recheck retry design remain.
- Static live ops code/config alignment for early soft gate and broker truth scheduler: code/config are aligned; only runtime reflection/freshness verification remains.
- KR `NO_SIGNAL` / ORP timing report implementation: working-tree read-only report and tests exist; output review remains active above before any threshold/window proposal.
- PathB `INVALID_PRICE` miss diagnostics implementation: working-tree read-only report and tests exist; remediation design remains active above and must preserve protected safety gates.
- One-off `docs/reports` follow-up plan files: remaining work is absorbed here.
