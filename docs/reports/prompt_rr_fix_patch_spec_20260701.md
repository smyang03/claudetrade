# 프롬프트 RR 결함 수정 패치 명세 (2026-07-01) — 마감 후 적용·toggle·shadow

> 장중(US 05:00 전) 라이브 편집 금지로 **명세만**. 마감 후 toggle 뒤 적용. net 미검증이라 default off + shadow.

## 결함 (검증됨)
1. RR 정의 불일치 (`decision/claude_price_plan.py:140-141`): `risk=buy_zone_low-stop_loss`(존바닥) / `reward=sell_target-buy_zone_high`(존꼭대기). 앵커 섞여 RR ~1.5배 과대. 결과: 615 plan 중 1.5게이트 0탈락(장식).
2. 워크드 예시 자기모순 (`minority_report/analysts.py:2908-2917`): label reward_risk:1.5인데 시스템 식으론 (76000-73500)/(73000-71000)=1.25 → 예시가 자기 게이트 미달 + reward_pct/risk_pct 분모 섞임. Claude 앵커 오염.
3. RR 정의 경로별 상이: selection(`claude_price_plan.py:140` risk=low-stop) vs Path B(`execution/single_symbol_judge.py:256` risk=high기준) — 같은 플랜 다른 RR.

## 수정안 (toggle `PATHB_CONSISTENT_REWARD_RISK`, default false)

### A. RR 정의 일관화 (claude_price_plan.py validate)
```
# toggle on일 때만:
risk = self.buy_zone_high - self.stop_loss     # 보수적 fill(존꼭대기) 기준으로 통일
reward = self.sell_target - self.buy_zone_high
# off면 현행 유지(라이브 무변경)
```
경로 통일: single_symbol_judge도 동일 정의 참조.

### B. 워크드 예시 자기일관·현실목표 (analysts.py:2908, toggle on 버전)
```
buy_zone_low: 73000, buy_zone_high: 73500, sell_target: 75300, stop_loss: 72300,
reward_risk: 1.5, risk_pct: 1.64, reward_pct: 2.46   # 일관: reward/risk=1.5, 목표 +2.5%(실측 peak대)
```
(현행 sell 76000=+3.4% 과대 → 75300=+2.5% 현실화. risk_pct/reward_pct 분모=buy_zone_high 통일.)

## 배포 절차 (마감 후)
1. toggle off로 코드 머지 → py_compile + 관련 pytest + check. **라이브 무변경 확인.**
2. shadow: toggle on 경로의 reward_risk를 plan_json에 병기 로깅(실청산 무접촉), 현행 vs 일관 RR 페어 누적.
3. 측정: 일관 RR로 게이트 적용시 통과율(예상 100%→~12%)·걸러질 플랜의 net(분봉 replay, MFE-enforce 금지). 걸러질 ~1.0 setup이 net 음수면 개선.
4. ≥N세션·다국면 통과 + 운영자 승인 후에만 enforce(toggle on).

## 양날 (필수 고지)
- 매수 100%→12% 급감 = 빈도 큰폭↓. 운영자 "매수부족" 우려와 반대 방향(위생수정, 빈도확대 아님).
- net 개선은 가설(걸러질 setup이 본전이하면 개선) — shadow 전 enforce 금지.
- 예측알파 무관(무알파 결론과 충돌 안 함). 순수 게이트 정합성/위생.

## 즉시 가능(저위험) vs shadow
- B(예시 자기일관)만은 toggle 무관하게 "라벨이 자기 식과 모순"인 버그수정이라 저위험이나, 목표 76000→75300은 앵커를 바꿔 행동영향 → **이것도 toggle 안에** 넣어 shadow로.
- 결론: 전부 toggle off로 머지, 라이브 무변경 시작, shadow 누적 후 운영자 승인 시 enforce.
