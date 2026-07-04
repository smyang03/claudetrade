# 6인 패널 — 후보선택 프롬프트 개선 (코드/DB only, API 0) 2026-07-01

운영자 지시: API 금지·코드/DB만. 못고른승자+고른패자 양면, 시장맞춤, 분별력 OR 수익세그먼트. read-only.

## 종합 판정
- **예측/분별력 = 전멸(재확인).** 분별력 AUC 0.485~0.51. 못고른 승자/패자 feature 동일(회수불가). US손실 72%="못오른 픽→loss_cap"(진입fault). KR feature변별=phase-confound 거짓양성(OOS 부호역전).
- **"분별력 없어도 수익 세그먼트" = 없음.** 절대 net양수 robust 부재(레짐지배). 유일 OOS생존 상대틸트=gap3-5%(기존 추격금지 재확인, 새레버 아님).
- **구조/위생 개선 4개 실재(예측 아님).**

## 패널별 핵심
1. **US패자**: net-0.13/거래(본전±). 손실 72%=MFE<1.5 못오른픽→cap, 28%=give-back(약함). 결정feature 무분리. 유일 tail신호=chg≥10 추격(n19, 5/26클러스터편중, 주력손실 못잡음).
2. **KR패자**: LOSS_CAP 좌측꼬리 즉시역행 17/26. pooled feature변별 강하나 OOS 월별 부호역전(5월 눌림이 더손실)=phase-confound.
3. **못고른승자**: 회수불가 양시장. missed-win vs missed-lose feature 거의동일, 9컷 전부 OOS 양전실패.
4. **수익세그먼트**: 절대양수 robust 없음(US Apr-May+2.69→Jun-1.38 반전). OOS생존=gap3-5 상대틸트뿐(KR/US·3시간창 부호유지), gap5+ 최악=추격금지 재확인. forward≠net 캐비엇.
5. **프롬프트구조**: ★결함1=워크드예시(analysts.py:2908)가 RR1.5 위반(=1.25)·목표부풀림 앵커 + RR정의 경로별 상이(selection risk=low-stop vs single_symbol_judge high기준). 결함2=라이브 부호신호(VWAPΔ·runup) 24~28후보중 ~8만 받음(evidence_pack 상한)=최대 입력품질레버. 결함3=trainer점수 앵커링/순환. 결함4=hard_cap ~절반절단(승자절단 미검증). 결함5=abs(change) 부호버림(휴면, OVERHEAT=false).
6. **시장맞춤**: 부호역전 약함(AUC0.49~0.51). 진짜비대칭=구조적 base-rate(KR forward승률31% 단조감쇠=평균회귀 vs US50% 평탄=모멘텀, OOS 양반기 17~23pp 안정). 공유규칙(눌림-0.5%·RR1.5)이 US모멘텀에 마찰소지. ⚠️pool-forward≠net(KR forward최악인데 net흑자, US중립인데 net적자비용)=US완화 net개선 단정불가.

## 개선안 (우선순위)
- **#1 즉시·저위험**: 워크드예시를 RR≥1.5·자기일관(reward_pct/risk_pct=reward_risk, 분모통일)으로 교체 + RR정의 경로통일. reward_risk 위생결함([[prompt-reward-risk-defect-20260701]])의 진짜 원인.
- **#2 최대레버·shadow**: 라이브 부호토큰(vwapΔ/runup/ret_3m)을 더많은 후보에. char예산 측정 후.
- **#3·#4 shadow**: trainer 수치 숨김 A/B, US-한정 눌림규칙 완화 log-only(net미검증).
- 전부 정적컷 금지·shadow-first. 예측알파 주장 없음.

## API fidelity 부록 (운영자 질문)
간소 API테스트 vs production 차이의 범인은 **교훈(active_lessons) 아님**(오늘 0주입, enabled지만 selected=0). 진짜차이=production 워크드예시 앵커(결함1). code분석이 API가 못본 자기모순 예시를 규명 — "code를 써라" 지시 타당.
