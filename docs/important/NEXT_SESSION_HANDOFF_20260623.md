# 다음 세션 핸드오프 (2026-06-23 작성)

> 컨텍스트 꽉 차서 이어가기용. 이 문서 + 메모리 + CHANGELOG로 재유도 없이 바로 착수. **핵심 결정 1개가 대기 중(FX 실측).** 아래 §0부터.

---

## 0. 한 줄 — 어디까지 왔나 / 다음 단 하나

**ultrathink 3자 토론 + 데이터 테스트로 도착한 보유자 결론: 우리에겐 증명된 활(active) 엣지가 없다("알파"는 강세장 베타였다). 정직한 비용 반영 시 패시브 QQQ한테 진다. → 판 전환: "활 트레이더" 버리고 "패시브 인덱스 코어 + leash 채운 실험 sleeve(방어 오버레이/신호 인큐베이터) + 하드 kill".**

**다음 단 하나(운영자 인간 액션): 한투 MTS에서 실제 환전 우대율 확인** — 이게 "−82%p(우대)냐 −478%p(무우대)냐"를 가르는 마스터 변수. 코드 API로는 구조적 불가(매매환율 미노출). 그 숫자 받은 뒤 (a)패시브 코어 전환 설계 or (b)활매매 즉시 종료 결정.

---

## 1. 전략 결론 (보유자가 받아들인 것)

세션 후반, 운영자가 결정권 위임("너가 보유자, AI는 네 오더 받는 애"). 3개 sub-agent(구조재설계/비용해부/레드팀) 병렬 토론 → 독립 수렴.

**확정된 현실:**
- **종목선택/진입 엣지 = 0 (8번 확증).** 이번 세션 결정타: TARGET승자(n=35) vs 손절패자(n=87) 진입 feature 비교 → **strategy·origin·Claude confidence(0.580 vs 0.565) 전부 분리 못 함. 유일 분리자=regime(MILD_BULL 54% vs 26%)인데 이건 "강세장이면 더 오른다" 동어반복(베타)이지 국면 예측력 아님.** → 승자/패자 진입시점 구별 불가 = 활 sleeve 근거 소멸.
- **비용이 엣지를 먹는다 (비용해부가, 220건 균일모델):** GROSS +71.43%p지만 **수수료만 −38.57%p / FX우대가정 −82.57%p / FX무우대 −478.57%p.** "net 본전"은 과대평가였음. 거래당 gross 0.325% < 비용바 0.5% → **손익분기 빈도 없음(빈도 줄여도 흑자 전환 불가).**
- **당일청산 구조:** US 87% / KR 100% same-day close, median 보유 US 2.6h/KR 43min = 데이트레이더 = 매 거래 왕복비용 풀로 문다.
- **capture 알파는 국면의존:** US 월별 net 4월 −14.86 / 5월 +22.48(강세) / 6월 −90.20. 양수는 5월 단일월. 진짜 신호는 TARGET +4.18%p(QQQ조정, 100%알파승)지만 **n=19·약세 미검증·드레인(−152)+수수료(−103)에 잠식 → 순 net −89.**
- **QQQ +11.76% > 우리 활매매(같은 강세장).** 패시브가 우릴 이김.

**보유자 결론(받아들임):**
1. **코어 = 패시브 인덱스.** 잃던 베타 되찾기(확실). promise = "시장을 이긴다"가 아니라 "시장만큼 간다(지금 지던 걸 멈춘다)".
2. **활 기계 = 죽이지 말고 leash 실험실로 격하.** 종목고르기 죽음. 남는 활: ⓐ 방어 오버레이(확정 risk-off 노출축소 = 알파 아니라 6월−90%p/CIEN갭 꼬리방어용) ⓑ 신호 인큐베이터(미래 엣지 shadow 증명 시 배치). 둘 다 shadow 먼저·실탄~0·kill.
3. **하드 kill:** 방어 오버레이가 shadow서 buy&hold 못 이기면 떼고 순수 패시브. FX 무우대로 나오면 활매매 즉시 종료.

**왜 받아들이나:** 갈림길 어떻게 풀리든 현 상태 지배(최악=100% 패시브=지금보다 나음). 터질 수 없고·옵션 남기고·정직(알파 가장 안 함). "시장수익+제한하방+알파 공짜옵션" = 증명된 엣지 없을 때 합리적 보유자의 자리.

---

## 2. 결정 대기 (다음 세션 착수점)

| # | 액션 | 누가 | 비고 |
|---|---|---|---|
| **A** | **FX 우대율 MTS 실측** | 운영자(인간) | **마스터 변수.** MTS 환전내역서 매매환율 vs 참조환율 차이 역산. 우대(0.2%RT)/무우대(2%RT) → 전체 net 4배 차이 |
| B | A 결과로 "패시브 코어 전환 설계" or "활 종료" 결정 | 보유자(AI)+운영자 | 무우대면 활 즉시축소가 답 |
| C | net 컬럼에 FX 반영 배선(`pnl_pct_net_after_fx_est` 전 청산 일관기록) | 코드 | 측정 정직화. 현재 measured net이 손실건 편중이라 부분집합 결론 뒤집힘 |
| D | 방어 오버레이(risk-off de-risk) shadow 설계 — *만약* 활 유지 결정 시 | 코드 | 알파 아니라 꼬리방어. regime 라벨 의존(56% 충진됨) |

**규율(운영자 합의):** FX부터. 그 숫자가 "패시브 전환 시급한가 여유있나"를 정함.

---

## 3. 데이터 앵커 (재계산 말고 재사용, 단 직접 재쿼리로 검증)

- 비용 상수: `execution/claude_price_sell_manager.py` `_fee_rates_for_market`/`_fx_spread_rate_per_side`. US 수수료 왕복0.5%, FX 편도 0.1%(우대 자동가정, 무우대 1%). KR 수수료 0.21%(거래세 포함), FX 없음. **`US_FX_SPREAD_RATE_PER_SIDE`가 config/env에 없어 코드 default(우대)로 돎 — 이게 위험.**
- 리포트: `docs/reports/capture_net_review_20260622_221440.md`(US net capture 0.045, loss_cap −141%p), `docs/reports/full_profitability_review_20260622_221445.md`(보유버킷·전략별), `docs/reports/capture_net_review_20260619_125156.md`(6/19 클린 baseline −19%p vs 6/22 −61%p=약세 악화).
- 보유버킷(생존편향 주의!): 0~30분 net −1.57%/승17% · 1일+ +1.87%/승62.5%. **레드팀 경고: "오래 들면 번다"가 아니라 "이긴 놈이 살아남아 오래 들렸다". 보유연장=자살골(메모리 exit_is_the_edge).**
- 알파/베타 감사(메모리 `project_exit_is_the_edge_20260620`): TARGET 실현+5.41%=베타+1.23+**알파+4.18(100%알파승)**, loss_cap=음수알파−107. broad-extension 시뮬 이미 기각(승자연장=알파음수).

---

## 4. 이번 세션 코드 (전부 커밋·푸쉬·라이브, origin/main)

봇 13:50 재시작으로 전부 반영. 스택 7/1개 정상.

- `bc22d9f` 무결성 2-1: PathB MFE/MAE durable 영속화(`_persist_observed_excursion`+finalize fallback). 'test:' 라벨 혼재(동시세션). MD위반 기록 CLAUDE.md.
- `e6405b3` **② US 손절 marketable 지정가**(`_pathb_stop_marketable_sell_price`, US loss_cap/hard_stop/claude_price_stop만 트리거−0.5% 호가) + **① 미체결 매도 shadow 복구**. **② 라이브 검증됨**: CWAN 21초 체결+가격개선(24.4074→24.53), CIEN. 토글 `US_PATHB_STOP_MARKETABLE_LIMIT_ENABLED=true`/PCT=0.5.
- `b2e8aba` fast-fill 매수 phantom pending 정리(`_remove_pathb_pending_buy_order`). KR 475430 키스트론 유령 stale 버그. 475430 stale은 정비창서 수동 제거 완료.
- `d15d4e8` profit_guard prior 유지/롤백 = **2026-07-21경(또는 exit n_sell≥30) 청산 verdict로** 결정. 조기데이터 KR+3.96/US+0.71(insufficient).
- `78e3cd7` **`docs/important/HONEST_ASSESSMENT_20260623.md`** — "비용 이기는가" 질문 회피 말라는 본심 기록.
- 정비창(봇정지): brain 오염정리 --apply(KR US티커 3건 제거, 백업) + regime 전체백필(0%→56%). 동시세션 `0186387` KR flow gate(shadow/off).

---

## 5. 다른 대기 항목

- **2026-07-21경: profit_guard 청산 verdict 재확인** → 음수반전시 `HOLD_ADVISOR_PROFIT_GUARD_ENABLED=false`. 재채점: `python tools/run_hold_advisor_exit_validation.py`.
- **brain 5월 correction_guide 중립화(1-B): 미실행**(운영자 fresh-brain 부활여부 미응답). fresh ON이라 라이브 영향 0. off 전환 시 선행 필수. selection 교훈은 regime 고쳐도 valid_apply 0=폐기 확정.
- lesson_validation 재채점됨(regime 반영): selection 6셀 여전 0 valid_apply, 청산 2셀 risk_on 정상라벨·gain양수·insufficient.

---

## 6. 규율 (이번 세션에서 배운 것 — 다음 세션 지킬 것)

- **활 알파 연기 금지.** 8번 무엣지. 운영자가 압박/속상해도 "새 묘수 있다"로 hopium 던지지 말 것. 동시에 "묘수 없다"로 후퇴해 같은 말 반복도 금지 — 데이터로 *판정*하고 *행동*을 제안.
- **보유연장=자살골**(생존편향). "오래 들면 번다" 데이터는 인과 아님.
- **FX가 모든 net의 전제.** 우대율 모르면 net은 추정. MTS 실측 전엔 절대수치 단정 금지.
- 보호영역(pathb_runtime exit/order/broker truth) 변경은 MD위반 절차+focused테스트.
- 라이브 머신: 봇/대시보드 직접 launch는 운영자 지시 때만. brain.json/decisions.db 동시쓰기 금지(정비는 봇 정지 후).
- 동시 세션 주의: 이 레포에 다른 에이전트가 동시 커밋함(0186387 등). `git add` 전 staged 섞임 확인.

---

## 7. 시스템 현재 상태

- 봇 13:50 재시작 인스턴스 가동(미국장 진행 중, MILD_BEAR). 스택 7개 1개씩, 에러 0.
- 전부 origin/main 푸쉬됨(HEAD ~84ac7d1 이후 78e3cd7 등). 미커밋=런타임 산출물(brain.json/pid.stale/dart)뿐, 커밋 금지.
- 라이브 토글 무변경(운영자확인 설정값 그대로). ②만 신규 활성(손절 marketable).

---

## 한 줄 재확인
다음 세션 첫 질문: **"운영자가 MTS에서 FX 우대율 확인했나?"** 했으면 그 숫자로 패시브 전환/종료 결정 설계. 안 했으면 그것부터 요청. 그게 이 전체 전환의 게이트.
