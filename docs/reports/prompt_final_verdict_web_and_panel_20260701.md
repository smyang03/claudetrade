# 후보선정 프롬프트 최종 판정 — 웹 리서치 + 6인 코드패널 (2026-07-01)

운영자 지시: 웹에서 타 트레이딩시스템 프롬프트 주입법 조사 + 12관점 토론 + 코드 대량테스트. "정말 개선방안 없는가" 원점 재검.

## 최종 판정: 예측 레버 없음(재확인). 개선=위생/결정론/lookahead제거뿐 — 외부 베스트프랙티스가 정확히 검증.

## 외부 베스트프랙티스 (웹)
- ATLAS(2510.15949)/anonymization-first(2603.17692): **숫자는 결정론 툴, LLM은 추론만**(ROI·지표 deterministic, LLM은 평가창 수익/가격/라벨 못받음). 정적지시 vs 동적placeholder 분리. 엄격 lookahead 차단. 구조화 JSON 강제.
- FinMem/TradingAgents: layered memory + character(bull/bear/neutral) + generate-then-select 2단계.
- → **우리가 이미 보유**(get_three_judgments 3심·single_symbol_judge 2차·PERSONAS·brain). 구조 부족 아니라 입력 무알파.

## 6인 패널 (코드 heavy)
1. **결정론화**: reward_risk(A) 정직화했으나 **분모만** — 분자 sell_target 여전히 Claude재량 = **loophole**(정직기준 RR median1.07·66%<1.2인데 target 부풀려 우회 가능). ★sell_target을 ATR/저항 결정론화가 A 완성·유일 net경로(admission+tp). 단 admission 바꿔 진입↓ shadow필수.
2. **lookahead교훈**: 진짜벡터=교훈아니라 `format_recent_selection_feedback`(매프롬프트 무조건주입·게이트없음) max_runup_3d(peak/미래) 일방향 "missed runup". 교훈자체는 approval_pending 비주입(무해·취약). fix=peak→forward_3d(종가) 또는 avg_dd 대칭.
3. **템플릿위생**: 워크드예시(analysts.py:2910) 3중결함=RR1.5위반(자기모순1.26vs1.5)+눌림규칙위반(존이 현재가위=추격)+경로별RR하한 분열(1.1/1.2/1.5 judge통과→PathB거부 조용한손실). A enforce로 더시급(예시가 정직게이트 실패).
4. **아키텍처**: 2단계select·layered memory·character 전부 이미 라이브, AUC≈0.5. 구조변경 무효(무알파 위 복잡도).
5. **뉴스/catalyst**: 증폭 금물 — US 강catalyst=net열화(score61+ -0.62 승률23%), evidence확대 phase-confound 무근거.
6. **적대검증**: 안판 3각도(evidence확대·후보수·뉴스) 전부 -EV/거짓양성. evidence태그 후보가 forward최악(추격). "위생이 전부" 확정.

## 개선 항목 (전부 위생, 순위)
| # | 항목 | 상태 | 위험 |
|---|---|---|---|
| A | reward_risk 분모 정직화 | **ENFORCE됨**(재시작 활성) | loophole 있음 |
| ★2 | sell_target 결정론화(ATR/저항) | A 완성, 최강 | shadow 필수(진입↓) |
| 3 | 워크드예시 수정(RR/눌림/자기일관) | A와 커플링, 시급 | 목표값=운영자 |
| 4 | feedback lookahead(peak→forward) | 라이브 무조건주입 벡터 | shadow |
| 5 | 경로별 RR하한 통일(1.1/1.2/1.5) | 조용한 퍼널손실 | 저위험 |
| — | confidence 결정론화 | inert | 약함·선택 |

## 사망(재확인, fresh data): 예측·분별력·아키텍처·뉴스증폭·evidence확대·프레이밍문구·세그먼트틸트·게이트완화.

## 한 줄
외부 최고수준도 결론 같다 — 예측 알파는 프롬프트로 못만들고, 개선은 숫자 결정론화·lookahead제거·템플릿위생. A는 그 첫걸음(enforce), 완성은 sell_target 결정론화(shadow후). net은 여전히 출구/비용 축.
