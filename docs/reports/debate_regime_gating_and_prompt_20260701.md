# 토론 판정 — P1 regime-adaptive 게이트 / P2 프롬프트·아웃풋 개선 (2026-07-01)

12관점(PRO/CON ×2 + 사회자 검증), read-only.

## P1: "regime별 능동 게이트 조정이 net 개선" → **미결 / 현재 not actionable** (강한형 기각, 약한형 생존)

**합의:** regime은 게이트별로 부호를 뒤집는 변별 축. neg_context 블록→종가: 강세 -1.6% (막는게 옳음) / 약세 +2.3% (막는게 손해). trade_ready는 반대. = regime×gate 상호작용 실재.

**기각(강한형 "강세 완화로 net 개선"):**
- **표본 불안정:** REQUIRE_TRADE_READY MODERATE_BULL 블록→종가 평균이 식별법에 따라 +2.44%(median<mean=승자꼬리, 로그식별 n14) vs -0.31%(median>mean=패자꼬리, audit식별 n25)로 **부호 충돌**.
- **단일기간:** REQUIRE_TRADE_READY 2026-06-26부터만 로그, 약세 0. neg_context가 단일세션 +1.63%→다국면 -2.02%로 뒤집힌 전례(검증 전 위치 동일).
- **베타 미보정:** 강세장 +2%의 시장초과분 미측정.
- **phase-confound:** regime 정적조정은 US/KR 부호반대(regime-exposure-C), realized-trade regime net은 5월(흑자)/6월(적자) 풀링 산물(6월 MILD_BULL -0.96=최악).
- 운영자 "강세 완화" 처방은 데이터상 **inverted** — 회수 풀이 큰 건 약세(단 그것도 confound).

**생존(약한형):** "regime이 변별 축"까지는 실측 지지. 단 능동 적용은 **게이트별 regime별 close-path를 베타보정+다국면(약세 포함) 누적 후**에만. = open-thread 다음스텝과 동일.

## P2: "프롬프트/아웃풋에 고칠 문제 있다" → **부분 찬성** (예측축 기각, 구조축 검증)

**기각(예측축):** 라이브 Path B confidence vs 실현 net **r=-0.042 (n=271)**, change_pct spearman 0.018, robust 무알파. 프롬프트로 selection 예측알파 못 짬.

**찬성(구조/캘리브레이션, 검증됨):**
- **★reward_risk 진입게이트가 장식:** plan 615건 중 **reward_risk<1.2 탈락 0건 = 100% 통과**(중앙 2.25, p10 1.53). 목표가(reward_pct 중앙 4.88%)가 실측 MFE(US 0.81%)의 6배 → reward/risk 항상 1.2 초과 → 게이트가 한 번도 거부 안 함. 출력 캘리브레이션 결함이 진입 게이트를 무력화.
- confidence inert: [0.50,0.70] stdev 0.047, 게이트(MIN_CONFIDENCE 0.5) 856건 중 0발동, 사이징 미사용.

**단 net 경로:** confidence 고치기=zero net(이미 미사용). reward_risk 재캘리=게이트를 실제 작동하게 하는 **위생/정합성** 개선이나 net 이득은 미측정(현실목표 reward_risk가 패자를 거를지 불명).

**판정:** "고칠 문제 있다"=YES(검증). "고치면 net 오른다"=미증명. 유일 실질후보 = **reward_risk 게이트 복구(목표가 캘리브레이션)** — 예측 아닌 위생.

## 종합
P1: 능동 regime-adaptive는 아직 못 함(표본불안정·단일기간·베타미보정). 약세 스트레치+베타보정 누적이 선결. P2: 예측은 못 고치나, **장식이 된 reward_risk 진입게이트는 실재 결함**(100% 통과) — 위생 차원 수정 후보, net 효과는 별도 측정 필요.
