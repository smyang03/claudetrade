# 토론 판정 — 클로드 두뇌(selection·confidence·가격) 개선 가능성

날짜: 2026-06-30 · 사회자 판정 · read-only(주문/코드/config 무변경)
명제: "병목(입력 모멘텀편향·confidence floor/앵커부재·목표가 무피드백·REQUIRE_TRADE_READY 봉쇄)을
개선하면 클로드 selection/가격예측에 측정가능한 알파가 생긴다 = 클로드는 개선 가능하다."

## 진단(코드확인)
- 입력: analysts.py:2602 후보토큰 = 지수상대모멘텀+거래량+이격 변주. 종목별 RSI/MACD/일봉·펀더멘털 빠짐.
  후보풀(candidate_pool_runtime.py:448)은 prompt_score(거래량·등락) 상위30 → 추격편향. 펀더멘털 인프라는 0행(빈 수도관).
- confidence: 클로드 raw 클램프만(룰 이산화 없음). PATHB_MIN_CONFIDENCE=0.5 floor가 좌측절단. 프롬프트에 confidence 앵커 0줄.
- 가격: buy_zone/sell_target/stop 전부 클로드 직접산출, 과거 MFE 피드백 0줄. observed_peak/mfe는 plan_json에 적재됨.
- 게이트: REQUIRE_TRADE_READY=true(config:449)가 PULLBACK_WAIT/WATCH 진입차단+plan cancel.

## 사회자 직접검증 (판정 가른 2건)
1. **confidence 3값은 측정 아티팩트.** 실제 21 distinct 연속(0.55·0.58·0.60...), stdev 0.047. 내 첫측정 round(cf,1)이 {0.5/0.6/0.7}로 뭉갬을 재현. → 병목은 이산화 아닌 floor절단+앵커부재 범위압축(0.5~0.7).
2. **change_pct는 비robust.** KR3d pearson 0.206 vs spearman 0.018 = 이상치 의존. 순위상관 0 → 기존 강피처도 robust 무알파(P2 우세).

## 6패널 요지
- P1 입력증강: 펀더멘털 0행. 이격강도가 ma60 이진으로 양자화 손실·종목 lesson 미주입은 사실. 단 개별지표 robust 약함 자인.
- P2 입력회의: ml feature_scores 14피처 전부 |spearman|<0.10. 과거 입력증강 전부 실패(cap완화 -1.4%·타이밍처방 -9.5% 자살골). **실측 우세.**
- P3 confidence: 3값은 아티팩트(실제 연속). floor위도 KR r=-0.31·US r=0.001. "측정가능해질뿐 알파보장 없음" 자인.
- P4 목표가: 캘리브레이션 net 죽음 인정. 살아남는 건 hold_days/peak타이밍 학습 → 출구(capture) 개선 = execution 레버.
- P5 게이트: enforce 2거래일·39건뿐, churn 실측(SLS 10회 재차단). KR ready는 역신호(ready1 -1.58 < ready0 +0.59). "끄지말고 log-only shadow로 모수복구".
- P6 본질회의: 2년 corr -0.048, 정보최대경로(시장판단)도 out-of-sample 무알파 = 정보량≠예측력. 반증조건 제시.

## 판정: 명제 **기각** (미결 아님)
"클로드 selection/가격 예측을 개선해 알파를 만든다"는 데이터가 일관 반대 — robust 무알파(2년 corr, spearman≈0, 과거시도 전부 실패). **클로드를 '더 잘 예측하게' 만드는 길은 막혀있다.**

단 A/B 이분법 넘어서: 클로드 두뇌가 기여할 **유일한 생존경로 = "예측"이 아니라 "관리판단(출구 타이밍)" 학습**(P4). observed_peak/mfe를 price_targets 프롬프트에 피드백 → 산 종목을 언제 팔지가 정교해질 수 있음. 두뇌개선이면서 검증된 레버(execution/capture)와 만남.

## 개선 방향 (운영자 결정)
| 축 | 판정 | 행동 |
|---|---|---|
| #3 입력증강(selection 예측) | ❌ 알파생성 막힘 | **하지마**(펀더멘털 0행·robust무알파·과거 backfire). 단 이격강도 이진→연속 복원, 종목 lesson ticker화는 위생(알파 무관) |
| #1 confidence | ❌ 알파보장 없음 | 프롬프트 앵커 주입 + sub-0.5 shadow 로깅 = **측정위생**(반증조건 인프라) |
| #2 목표가 캘리브레이션 | ❌ net 죽음 | **하지마** |
| #2' peak타이밍/hold_days 학습 | ✅ 유일 생존 | observed_peak 피드백 배선 → 출구개선. shadow 다국면 후 enforce |
| #5 게이트 | 측정모수 죽임 | REQUIRE_TRADE_READY를 log-only shadow로 전환해 카운터팩추얼 복구. KR/US ready 부호 재검증 |

## P6 반증조건 (이 판정을 뒤집으려면)
① confidence 분산복원 후 calibration r>+0.2,p<0.05 ② 신규입력후 selection fwd3d corr 유의 양전(out-of-sample) ③ 레짐신호 다국면 net+. 셋 다 못넘으면 두뇌예측 개선=매몰비용 확정.

## 기각/주의
- "입력 더 넣으면 selection 알파" ❌ (robust 무알파, 과거 전부 backfire).
- "목표 캘리브레이션으로 net↑" ❌ (net 죽음).
- "confidence 고치면 수익" ❌ (측정가능해질 뿐).
- 표본 6월 비중 큼·enforce 2일·KR n작음 → 전부 shadow 다국면 누적 후 판정.
