# 통합 꼬리-capture 청산 엔진 — 최종 설계안

작성일: 2026-06-17
근거: memory `project-entry-discrimination-20260616` §21~22 (뼈대 발견 + path-aware 검증)
상태: **설계 확정. 라이브 미적용. Phase 0(shadow)부터 운영자 승인 후 빌드.**

---

## 0. 뼈대 (검증됨)

- **시스템 = 멀티데이 꼬리-수확기.** net = 상위10% 거래(US net의 209%). 나머지 90%는 합쳐서 음수.
- **꼬리의 24%(+67%p ≈ 전체 net)를 샌다.** 큰 승자(MRVL +33 MFE→+17 실현)를 일찍 자름.
- **레버 = 꼬리 capture (selection 아님 — 구조가 40% 승률이면 충분).**
- **검증(path-aware 시뮬, US +33%p):** "MFE≥4% 증명 후 wide-trail" = 오버나잇 +56.7 − 당일더드 −23.7.
- **activation이 핵심:** 진입부터 trailing(act=0)=−97 대참사(=6/14 floor역효과). 증명 후만.
- **시장 조건부:** US(꼬리有)=wide-trail+오버나잇캐리 / KR(꼬리無)=tight·정리.

---

## 1. 아키텍처 — 3층 (엔진은 메커니즘만, 리스크는 위임)

```
A. 꼬리-capture 엔진 (runtime/tail_capture.py) = 메커니즘
     하드스톱(위임) / activation(MFE≥4%) / trail(US3%·KR1.5%) / 마감 carry-강도 결정
B. 교훈 시스템 (오늘 빌드) = 적응형 forward-검증 국면조건부 *param 튜너* (훅 now / 청산-교훈 later)
C. 기존 가드레일 = 리스크 *위임* (복제 안 함)
     loss_cap · DAILY_LOSS_LIMIT · HALT · regime · MARKET_SHARP_REVERSAL_GUARD · hold advisor · 슬리피지캡
```

**엔진이 *하는 것* (추가):** peak 추적(observed_mfe) · activation · trailing · 마감 carry/close 결정.
**엔진이 *위임하는 것*:** 하방=loss_cap / 포트폴리오=일일한도 / 약세차단=regime+sharp_reversal / carry판단=hold advisor / param=교훈. → **엔진이 얇고, 리스크는 검증된 기존 스택이 진다.**

---

## 2. 훅 지점

`runtime/pathb_runtime.py` exit scan **line 3320 직후**(`_update_position_excursion` 뒤 — pos·current·market·observed_mfe 가용, 실청산 3321+ 무접촉).
- **shadow:** 엔진 결정 + *실제 청산 결과*를 **페어로** funnel JSONL 로깅. read-only·additive.
- **enforce:** (Phase 2) 충돌 우선순위 + loss_cap 위임 정합 후.

---

## 3. 검증 설계 (점검에서 잡은 구멍 1 수정)

**문제:** shadow는 "캐리했을 것"을 로깅하지만 실시스템이 당일 청산 → **오버나잇 결과(+56.7)를 관찰 불가.**
**수정:** **shadow 로깅 + daily *forward 재구성*** — 엔진이 CARRY/trail 플래그한 *라이브 포지션*에 다음날 yfinance로 "엔진정책이면 얼마" vs actual 재구성(`tail_capture_sim` 방법론을 라이브-forward에). **이게 무위험 + 오버나잇 포함 검증의 유일한 길.** 약세장·갭·슬리피지·carry게이트 예측력 전부 여기서.

---

## 4. 롤아웃 단계

| Phase | 내용 | 라이브 |
|---|---|---|
| **0 빌드** | 엔진 + 훅(페어 로깅) + regime/마감분 입력 + forward 재구성 도구 + config(OFF) + 테스트 | shadow only |
| **1 검증** | shadow + daily forward 재구성. 국면별(약세 포함)·갭·슬리피지·carry예측력 측정 | 무위험 |
| **2 enforce** | 충돌 우선순위 명문화 + loss_cap 위임 정합 → **KR tight 먼저** → US(약세shadow 통과 후) | 게이트 |
| **3 이관** | ladder/preclose/hold advisor를 엔진으로 통합 | 보호영역·MD위반·승인 |

---

## 5. config (기본 전부 OFF/보수적)

```
TAIL_CAPTURE_MODE=off            # off/shadow/enforce
TAIL_CAPTURE_ACTIVATION_PCT=4    # MFE 이 % 넘어야 trailing 활성(증명 후)
TAIL_CAPTURE_GIVE_US=3           # US wide trail giveback
TAIL_CAPTURE_GIVE_KR=1.5         # KR tight
TAIL_CAPTURE_CARRY_US=true       # US 오버나잇 캐리
TAIL_CAPTURE_CARRY_KR=false      # KR 캐리 안 함
TAIL_CAPTURE_CARRY_STRENGTH_PCT=3   # 마감 net 이 이상=강함=캐리후보
TAIL_CAPTURE_CARRY_REGIMES=risk_on  # 캐리 허용 국면(약세 베타증폭 차단)
TAIL_CAPTURE_PRECLOSE_WINDOW_MIN=15
# 하드스톱은 자체값 아니라 기존 loss_cap에 위임(Phase 2)
```

---

## 6. 안전 계약 / 보호영역

- profit_ladder/pre_close/claude_price = **수익 핵심 보호영역.** Phase 0~2는 **additive(shadow)**, 기존 청산 무접촉 + **fallback 유지.**
- 엔진은 **하방을 loss_cap에 위임**(자체 병행 하드스톱 X). 하드스톱·broker truth·리스크 한도 **무수정.**
- 오버나잇 = 새 행동 → **기존 일일한도/HALT의 cross-day 커버 검증**, 빠지면 갭스톱 보강.
- Phase 3 이관만 보호영역 수정 → **MD 위반 기록 + 운영자 승인.**

---

## 7. 잔여 리스크 & 처리

| 잔여 | 처리 |
|---|---|
| 약세장 표본 부족 | regime-gated enforce (shadow에서 약세 N세션 통과 전 enforce 금지) |
| 오버나잇 갭 | loss_cap/일일한도 위임 + cross-day 커버 검증 + 필요시 갭스톱 |
| carry-게이트 예측력 | 튜너블 + shadow 측정 + hold advisor 판단 + **기본 no-carry** |
| 슬리피지 | forward 재구성에 보수적 모델 + 슬리피지캡 |
| 교훈 시스템 미검증(0 valid) | param 기본값 사용, 검증 후 적응. 학습기 = 안전망 보호 |
| 베타 증폭 | regime 게이트(RISK_ON만) + 약세장 enforce 생사게이트 |

---

## 7-R. Track 3-R: hold advisor ↔ tail capture 정합 (운영자 #1, 데이터 정교화 2026-06-18)

**운영자 통찰:** 엔진이 "carry" 깃발 꽂아도 hold advisor(conf<0.72)가 intraday_review·pre_close로 SELL 강등시켜 러너를 죽인다 → 정합 없이는 enforce 무의미.

**데이터 정교화(꼬리 누수 15건 close_reason):** PRE_CLOSE **7** / TARGET **4** / TRAILING 2 / CLAUDE_SELL(hold advisor) **2**. → **hold advisor는 2/15(생각보다 작음). 더 큰 범인=pre_close+target(11/15).**
- **이 둘은 엔진이 처리:** carry가 pre_close override(설계 §1), **단 trail이 TARGET도 override해야**(MRVL +17.7 vs MFE +33.6=target에서 잘림). **현 엔진은 ladder 앞에만 삽입 → sell_manager의 claude_price TARGET은 뒤라 못 막음. 보강 필요: tail-trail 활성 시 TARGET도 엔진에 양보(보호영역 — 신중).**
- **Track 3-R(hold advisor)=작지만 필수 conflict 해소:** 엔진 HOLD/carry건을 intraday_review SELL이 가로채지 않게.

**처방(Track 3-R):** carry-intent(이익중 + MFE≥4% 증명 + 추세살아있음 + RISK_ON) → hold advisor가 **bounded HOLD 존중.** 손실중·추세꺾임·약세장 = **0.72 현행 유지(손실방어 불변).** tail capture와 **같은 shadow에서 검증.** enforce 바: carry-intent건 net+ & 손실누수 0 & 약세장 통과. 미달 시 0.72 복귀.

**우선순위 재정렬(데이터 기반):** ① 엔진 carry+pre_close override (큰 범인 7건) ② 엔진 trail이 TARGET override (4건, MRVL류) ③ Track 3-R hold advisor 정합 (2건 + 충돌방지). **셋 다 shadow 검증 후 enforce.**

## 8. 한 줄 요약

**진입=꼬리후보 생성기(selection 안 만짐). 청산=통합 꼬리-capture 엔진(MFE≥4% 증명 후 wide-trail + 강한 것만 RISK_ON 오버나잇 캐리). 리스크=기존 가드레일에 위임. 적응=교훈 시스템. 검증=shadow+forward재구성. US 한정, 약세장 통과 후 enforce. KR=tight/정리.**
