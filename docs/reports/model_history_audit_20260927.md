# 모델·예측·LLM 판단 시도 전수 감사 (2026-03 ~ 2026-09-27)

read-only 감사. 파일 수정은 이 리포트 하나뿐이다.
근거: git log(1,352커밋), `docs/reports/`, 코드(`ml/`, `learning/`, `minority_report/`, `runtime/`, `tools/`, `tools/research/`, `state/models/`), 메모리(`memory/` 235개).
수치는 원문 그대로다. 원문을 다시 연 것은 5건이다(09-01 rank1 −7.16%·t −3.76, Brier 0.2642, E5-V6 −0.65%, B2 BUY_READY −2.146%, AUC 0.5075). 나머지는 문서 인용이다.

## 결론

1. **실주문 권한을 가진 학습 모델은 지금 0개다.**
   - 03~06월에는 fit한 ML이 없었다. 있었던 것은 다음뿐이다.
     - Claude 프롬프트 판단
     - 손으로 정한 가중합 점수(trainer)
     - brain.json 카운터 누적
     - 격자 탐색
   - 첫 sklearn 모델은 07-10 profit_path(SGD+isotonic)와 07-11 us_swing HGB다.
   - XGBoost 스코어러는 06-12에 "등록만" 하고 끝났다(`docs/important/ACTIVE_WORK.md:206`, 3da8ada).
2. **판정이 방법 결함 때문에 무효·불완전한 것 중 부활 가치가 있는 것은 사실상 1개다. US swing 모델의 09-01 제거다.**
   - 제거 근거는 12세션 좁은 라이브 부분집합(−7.16%)이었다.
   - 09-27 넓은 풀 워크포워드(256일)에서는 역선별이 재현되지 않았다.
   - 풀 학습 변형 S1은 top-3 +0.995%p(날짜 t 3.09, 양 반기 양수)였다.
   - 단 후보 등급이다. 11개 검정 중 보조 셀이고, 변동성 쏠림·생존편향·단일 국면이 걸려 있으며, IWM 초과는 미측정이다.
3. **profit_path의 07-13 "데드락"은 모델 품질 판정이 아니라 배선·보정 구조의 산물이었다.**
   - 런타임 OOD가 거의 100%(빈 피처)였다.
   - isotonic 상한(KR 0.49 / US 0.30)이 문턱 0.55보다 낮았다.
   - 09-27에 결정 시점 피처로 다시 계산한 결과, 품질 자체도 기각이었다. US t −0.70이고, KR 통과는 장전 호가 라벨 아티팩트였다.
   - 모델은 살릴 것이 없다. 다만 **런타임 피처 공급 결함은 여전히 열려 있다.**
4. **Claude를 "고르는 자"로 정직하게 시험한 적이 없다.** 선별 AUC 0.49~0.51 판정은 다음 조건 위에 있었다.
   - 선정일 종가 기준 forward gross(체결 불가)
   - 약 37 독립일, 단일 국면
   - 4.14배 중복 행
   - 09-22 시험은 Claude 판단이 아니라 Claude가 만든 기계 규칙이었다.
   - 09-27 패닉 필터 "통과"(t 2.25)는 입력으로 준 강신호 플래그의 재현에 가깝다(아래 §2-5).
5. 한 번도 안 해본 것 가운데 증거로 확인되는 것은 다음과 같다(§3).
   - 생존·위험률 출구 모델
   - 쌍대·리스트 학습 순위(LambdaMART)
   - conformal 기권
   - HMM·jump 국면 모델
   - **이미 배선돼 있는 Claude 공시 본문 판정의 결과 채점**(2,550 신호 중 LLM 응답 12건, 채점 0건)
6. 현재 자본 기준 월 수익 효과는 **산출 불가**다. 부활 1순위(S1)의 우위는 "같은 날 풀 평균 대비" 수치이고, IWM·규모매칭 초과와 체결 용량을 아직 재지 않았다.

## 결함 코드 (표에서 사용)

| 코드 | 뜻 | 부정 판정을 무효화하나 |
|---|---|---|
| F1 | 라벨 lookahead·미래정보(사후 MFE 순서, 장중 플랜을 시가에 체결 등) | 양의 판정을 부풀린다. 부정 판정은 대개 유지된다 |
| F2 | 체결 불가 진입가(선정일 종가, 장전 호가) | **둘 다 가능**하다. 라벨이 실행과 다르면 판정 대상이 다른 전략이 된다 |
| F3 | t 단위 부풀림(종목·행 클러스터, D7 겹침 미보정) | 양의 판정만 부풀린다. 부정 판정은 구제하지 않는다 |
| F4 | 원 net 판정·벤치 없음·틀린 벤치(1689395) | 강세장이면 양을 부풀린다. 부정 판정은 대개 유지된다 |
| F5 | 생존편향(현재 구성종목, 상폐 누락) | 양을 부풀린다 |
| F6 | 소표본(≤12 독립일 또는 n≤20) | **부정 판정 무효화 가능**(검정력 부족) |
| F7 | 배선 결함(빈 피처·train/serve skew·정렬 버그·필드 퇴화) | **부정 판정 무효화 가능** |
| F8 | 단일 국면 | 양쪽 다 일반화할 수 없다 |
| F9 | 비용 모델 오류 | 양쪽 다 |
| F10 | 좁은 라이브 부분집합이나 학습·평가 모집단 불일치 | **부정 판정 무효화 가능** |
| F11 | 인샘플·사후선택·다중검정 | 양을 부풀린다 |
| F12 | 라벨 ≠ 실제 계약·보유기간(D5 vs D7, 60분 vs 7일) | **부정 판정 무효화 가능** |
| F13 | LLM 학습 암기 누수(과거 날짜 재생) | 양을 부풀린다 |
| F14 | 문제 구조에 안 맞는 모델 클래스·결정 규칙(고정 0.5 등) | **부정 판정 불완전** |

## 1. 전체 표

독립일은 원문에 있을 때만 적었다. 없으면 "미기재"다.

### A. Claude(LLM) 판단

| ID | 이름 | 날짜 | 결정 | 라벨/타깃 | 피처·데이터 | 구조 | 검증 | 표본(행 / 독립일) | 결과 | 당시 판정 | 결함 | 부활? | 출처 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | 분석가 3인 + consensus 가중(mode 결정) | 03-19~ | 국면(mode → 사이즈·매수 차단) | 없음(사후 stance vs 지수 방향 채점) | digest·뉴스·brain·lesson | Claude 3페르소나 R1/R2, 적중률 가중 | 누적 인샘플 | KR mode 채점 n=24 | mode 방향 적중 US 60% / KR 52%. 재빌드 후 bull 50.0 / bear 52.9 / neutral 44.1%. 토큰 40% 소비 | 기여 미입증, live 유지 | F4 F6 F7(구 TR 결측이 채점 오염) | 아니오. 동전 수준이고 net 기여 측정 없음 | minority_report/analysts.py·consensus.py, 3b0b84a, memory unexamined-layers-audit-20260703 |
| A2 | historical_sim 과거 재생 "학습" | 03-19 | brain 갱신 | 지수 방향 | 과거 digest | Claude 재호출 | 과거 날짜 재생 | trained_days KR 122 / US 130 | — | 06-26 "검증 무효, 오염면" | **F13** | 아니오 | phase1_trainer/historical_sim.py, memory full-system-audit-20260912 |
| A3 | postmortem·brain·active lessons·lesson_validation | 03-20~06-26 | 텍스트 판단 입력 | lesson별 forward | brain.json, lesson DB | LLM 메모리 + 규칙 게이트 | forward | 447세션. raw 15,633 / distinct 3,778(4.14배) | valid_apply 0. param tuner 조정 → 진입 N=1. 교훈 14일 무효과 | enforce → shadow 강등 | F1(교훈 통계 lookahead) F3(중복) F6 | 아니오 | db208e9, minority_report/lesson_*.py, memory debate-net-measurement-gap-20260626 |
| A4 | tuner·param_tuner(Claude 파라미터 검토) | 04-14~ | 파라미터 튜닝 | 없음 | adaptive 결과·VIX | Claude + 범위 제한 | 없음 | 447세션 / 조정 진입 1 | 손실 1건 | "죽은 소비처"(07-03) | F6 | 아니오 | 4472347, strategy/param_tuner.py |
| A5 | Claude selection trade_ready(랭킹 콜) | 03-19 ~ 07-08 제거 | 진입 필터·랭킹 | fwd 1/3/5d(선정일 종가 기준), 실현 net | 후보 풀·trainer 힌트·뉴스 | Claude | 인샘플 사후, 창마다 판정 바뀜 | Claude 판단 행 58,044 / **37일**(04-02~06-30). "2년 73,479" 표기는 과장 | AUC 0.5075(1d) / 0.5128(3d) n=21,389. 방향확신 corr −0.048. 06-01 US rdy0 +1.33 > rdy1 +0.52(역전). 06-26 US rdy1 +0.36 vs rdy0 −1.97. 매수 추천 net 합 −69.3. 07월 픽 +1.00 vs 대조 +0.19 | "무알파" → 07-08 rule_direct로 제거 | **F2** F3(4.14배) F4 F6(37일) F8(5월 강세) | 아마. 정직하게 재시험한 적 없음(§2-3) | gross_alpha_hunt_verdict_20260707.md:62, 9219aaf, candidate_selection_quality_FINAL_20260616.md |
| A6 | Claude confidence 필드 | 06-17 ~ 06-30 | 사이징·필터 | fwd3d, 실현 PF | PathB plan conf, hold conf, bull_conf | Claude 출력값 | 인샘플 상관 | n=50,055. 교차 2,511. ≥0.7은 N=3 | r +0.043. conf≥0.80 −8.24%. PathB corr −0.04. hold 3인 conf가 전원 0.72(분산 0) | 역신호로 기각, 사이즈업 금지 | **F7(필드 퇴화: floor 0.5, 0.5~0.7 압축, stdev 0.047)** F2 F6 | 아니오. 필드에 정보가 없다. 다시 하려면 출력 설계부터 바꿔야 한다 | memory confidence-prediction-verdict-20260629, debate-brain-improvability-20260630 |
| A7 | preopen PROMOTE 프롬프트 replay(v2~v6, strict, news overlay) | 06-08 | 장전 승격 | 개장 후 X분 수익 | 장전 스냅샷 60/일, 뉴스 | sonnet-4-6 프롬프트 7종 | train 3일 / holdout 2세션 → holdout 소진 후 news 재선택 | 2~5세션, promote 6~16건 | US v4 train +7.22 → holdout −2.63. strict news +1.60/일(12건) → 06-08 hard pin | 운영 반영 | **F11 F13** F6 F2 | 아니오 | preopen_candidate_replay_*_20260608.md, memory(06-25 암기 lookahead 지적) |
| A8 | single_symbol_judge·early judge 모델 A/B(sonnet→opus-4-8→opus-5→haiku) | 06-12, 07-26, 08-02 | 진입 텍스트 판단(PathB 가격 플랜) | 없음(판정 일치·플랜 산출률) | judge 프롬프트 | Claude | A/B 10건 | 10건 | 플랜 산출 opus 8/10 vs sonnet 2/10. 진입 승인 21→40% | 교체 적용. 08-02 비용 때문에 haiku | F6, net 미측정 | 아니오(PathB 신규 진입 off) | bdfde51, be51b98, 3349956 |
| A9 | Claude 5.0(sonnet-5 + thinking) 이관 | 07-02 | 전 호출부 | 없음(신/구 방향 일치율) | 캡처 프롬프트 | sonnet-5 | replay 일치율 | select 20 / hold 20 / analyst 30 / single 10 | 일치율 select 90%, hold 100%, analyst 80%, single 70%. selection JSON 이탈 약 33% | 배포 | net 라벨 없음 | 해당 없음(성과 시험이 아님) | 1a5782e, model5_beforeafter_report_20260702.md |
| A10 | 패자 필터 프롬프트·후보 분별 패널·프레이밍 주입 | 07-01 | 진입 필터(텍스트) | fwd1d 패자, 승/패 | 결정 시점 정보 | sonnet-4-6 | 6월 OOS, 종목당 1관측 | 43종목 × 7재스캔 | 1관측으로 접으면 AUC ≈ chance(0.50, 일별 0.34~0.82). 패널 AUC 0.485~0.51. 문구 주입 시 tr 0 | 기각 | F2 F6 F8 | 아니오 | memory debate-lookahead-loser-filter-20260701, panel-prompt-improvement-20260701 |
| A11 | judge BUY_READY / PULLBACK_WAIT / REJECT 오프라인 재생 | 07-23 | 진입(LLM)·가격 계획 | 60분 gross, 체결 후 net | raw call 재생·분봉 | Claude | 응답 생성 이후만 체결되게 재생 | BUY_READY US 고유 6. PB_WAIT KR 19 / US 23 체결 | BUY_READY 60분 −0.31, **0/6 양수**. PB_WAIT KR +1.077 / US −0.704 | 확대 금지. KR 조건부 유지 | F6 F12(계획 보유 2~3일 vs 60분) F7(feature age 중앙 9.4분) | 아니오 | claude_buy_hold_advisor_offline_review_20260723.md |
| A12 | B2-4~6 Claude action 변별력 | 08-11 | 선정·veto | 3일 고정 수익 | candidate_audit US 242,242 / KR 87,818행(04-20~08-11) | Claude action | 사후 소급 | 행 33만 / 독립일 미기재 | 급락 코호트 WATCH +3.404 > 무판단 −0.746 > AVOID −1.668. 전체 BUY_READY −2.146(claude) / −3.162(route) | "Claude는 veto 전담" | F3(행 단위) F12 F4. KR 3일 수익이 전 구간 음수 → 통화 혼입(08-28 수리) 가능성(불확실) | 아마. WATCH/AVOID 분리가 veto 신호인지 날짜 클러스터로 재검 가치 있음 | 099d25a, verification_backlog_master_20260811.md:260-272 |
| A13 | Claude 급락 저점 선정 | 08-01 | 선별 | fwd5d | 급락 −3%↓ selection | Claude | 인샘플 | 미기재 | KR rank1-3 −4.65%(승 24.6%). US +0.89%(rank 무의미) | Claude = veto 확정 | F2 F4 | 아니오 | memory restructure-phase0-executed-20260801 |
| A14 | hold advisor(SELL vs HOLD), intraday_review_sell, exit_decision_scorer | 03-22 ~ 07-23 | 출구(LLM) | SELL − HOLD 반사실 | 청산 원장 | Claude(3인 → triage) | 페어 반사실 | 개입 N=8. SELL KR 6 / US 17. intraday 33 | 개입 +3.55 vs 기계 +0.32(5/8). SELL 우위 KR +4.40%p(n6) / US +0.339%p(중앙 −0.410). intraday −1.35 | "tail-risk 축소기이지 알파 아님". 07-08 "Claude = 매도 전담" | **F6 F7**(realized_net 컬럼이 실제 gross, PathB 청산 outcome 미배선) F12(HOLD 대안이 무조건 3세션) | 아마(낮음). 한 번도 제대로 채점되지 않았다. 마지막 호출 로그가 07-31(코드 리더 보고, 미검증) | minority_report/hold_advisor.py, 8ae6fc8, claude_buy_hold_advisor_offline_review_20260723.md §6, debate_holdadvisor_role_20260626.md |
| A15 | Claude 가격 플랜 TARGET 청산·목표가 캘리브레이션 | 06-03 ~ 08-01 | 출구 가격 | 실현, 도달률 | buy_zone / sell_target | Claude + 규칙 | 사후 버킷 | US 27 | TARGET +138.5(ex-top2 +4.47). 목표가 = 도달 가능 MFE의 약 2.3배. k≈0.4 조기익절 → 08-01 해제 | 번복 3회 | F1(결과로 조건을 건 생존편향) F6 | 아니오 | gross_alpha_hunt_verdict_20260707.md, memory early-tier-target-fraction-is-the-loss-source-20260801 |
| A16 | Claude vs Codex 동일 프롬프트 A/B, 06-18 AI 트레이더 shadow | 06-18, 07-15 | judge·방향 | action 일치, 당일 지수 초과 | — | LLM 2종 | 1세션 / 4건 | 4건, 1세션 | 일치 2/4. KR BUY −1.85 / AVOID +1.78(역) | 교체 금지, 우위 없음 | F6 | 아니오 | claude_codex_prompt_ab_review_20260715.md, strategy_review_20260620 §1 |
| A17 | 09-22 "Claude 직접 선별" 시험 | 09-22 | 선별(KR) | 픽 − 후보 전량, 지수 대비 | 시드 고정 무작위 20종목/일 | **Claude가 만든 기계 규칙**(ma20_disc<0 → rv20 오름차순 top3) | 20일 시험 → 319일 전 표본 | 20일 → 315일 | 20일: vs KOSDAQ150 +2.51%p t 2.01. 나머지 296일: vs K200 −0.151 t −0.51 / vs KQ150 −0.011 t −0.04. 무작위 3과 구분 불가 | 사실상 기각 | F11(규칙 출신 데이터가 같은 저장소). 20일 F6. **Claude 판단 시험이 아니다** | 아니오(규칙). "Claude 판단" 시험은 여전히 미실시 | 2fd19e0, claude_rule_fullsample_20260922.md |
| A18 | 패닉 밤 필터 — Claude opus-5 제로샷 블라인드 | 09-27 | 진입 필터(메타라벨) | 받은 밤 − 버린 밤 net | chg·gap·**strong 플래그**·ret5·rv20·ma20·spy_prev·dow | Claude 제로샷 | 2018~2026, 반기 | 234밤 / 234일 | +0.978%p t 2.25, 전반 +1.080 / 후반 +0.959. B'(conf≥0.6) t 1.49 | "통과" | **F13(날짜 없음이지만 2020-03 같은 조합은 인식 가능, docstring 자인)**, F11(같은 날 판단기 9종 이상을 문턱 t≥2로 검정, 변형은 2.64), strong 플래그 재현(§2-5) | 아니오(단독). 강신호 규칙(t 2.70)을 넘지 못한다 | panic_filter_model_claude_20260927.md, data/analysis/panic_filter_20260927_claude.jsonl |
| A19 | KR 공시 이벤트 레인 LLM 본문 판정 | 09-03~ | 이벤트 진입 필터(텍스트) | 없음(채점 안 함) | DART document.xml 본문 4,000자 + 정규식 필드 | sonnet-4-6 → related_party / new_business / quality | **미채점** | signals 2,550행 중 `available: true` 12건 | — | 보조 판정(주문 권한 없음) | 표본 0 | **예**(§3·§4) | e327871, runtime/kr_event_lane.py:298-322, data/shadow/kr_event_signals.jsonl |

### B. 점수식·규칙형 "모델"(학습 없음)

| ID | 이름 | 날짜 | 결정 | 라벨 | 피처·데이터 | 구조 | 검증 | 표본 | 결과 | 당시 판정 | 결함 | 부활? | 출처 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | candidate quality trainer(trainer_score, PLAN_A/B/WATCH) + ranker sim | 05-12 / 06-01 | 프롬프트 풀 랭킹 | 평가만: ret60 | 등락 bin·고점 거리·유동성·catalyst·chase | 손 가중합(임계 62/55/45) | 가중치를 정한 같은 기간 재생 | 41,357행. ret60 존재 약 17%. 약 30세션 | plan_a_shadow +0.529 PF 1.31(cap30). 07-10 5분위 중 최고 분위가 최악(KR 7월 −6.611%). KR PLAN_A −1.94 < WATCH +1.91 < BENCH +6.14 | live reorder·sizing 금지, shadow만 | F11 F2 F3 F7(trainer_tier 추출 0% → 15d410d / 9988a32) | 아니오 | runtime/candidate_quality_trainer.py, candidate_quality_ranker_sim_kr_all_sim_20260601.md, design_candidate_discrimination_claude_trainer_profit_20260710.md |
| B2 | catalyst 가점, trainer 힌트 숨김 A/B | 07-01 / 07-02 | 랭킹 | fwd 1~3d | 뉴스·어닝 플래그 | 규칙 가점, 50:50 A/B | placebo 통제 / A/B | 미기재 | KR catalyst−placebo 1d +0.74. US 3d −0.14. A/B 결과 문서 없음 | KR·US enforce → US OFF(07-21) | F2 F8. A/B 미판정 | 아니오 | 9e862e2, memory screener-catalyst-lever-20260701 |
| B3 | 국면 게이트(MILD_BEAR ×0.5, red-tape, past-only MA20 4패널, KR MA20 위 급락 금지) | 06-27 ~ 09-10 | 국면 | 실현 / 계약 net | 지수 MA20·breadth·QQQ 개장 | 규칙 | IS → OOS 3패널 | MILD_BEAR 44. KR 아래 224 / 위 284세션 | MILD_BEAR −0.333 → −0.097. red-tape RED −1.23 / 승 14%. 4패널 역효과. **KR MA20 위: OOS 연도 6/6 음수** | KR MA20 회피만 생존 | F11 F8. 벤치 KOSPI(불확실) | 해당 없음(규칙이고 이미 살아 있음) | memory kr-regime-gate-oos-20260910, shadow_feature_simulation_20260711.md |
| B4 | 손실 배제·특성 WF·arm 선택·winner vs loser | 09-07 ~ 09-17 | 진입 필터(메타라벨형) | 가상 북 net, 지수 초과 | 14~18 특성 분위 | 규칙 | H1 → H2, 월별 WF | KR H2 15,130 / 160세션. 09-17 KR 77,049 / 311일 | H1 규칙이 H2에서 역전(KR 남김 −0.29 / 배제 +0.19). 유일 생존 nobio는 초과 기준 음수. 09-17 KR max21 −1.17%p(t −6.69), rv20 −1.21(t −6.35) | 특성 필터 기각 | F11 F8. 09-17 절대 수준은 틀린 벤치 F4, D7 겹침 F3 | 아마. max21·rv20 역축은 §4-1 S1 대조군으로 쓸 것 | 3437971, winner_vs_loser_features_20260917.md |

### C. 학습형 ML — 진입·랭킹

| ID | 이름 | 날짜 | 결정 | 라벨/타깃 | 피처·데이터 | 구조 | 검증 | 표본(행 / 독립일) | 결과 | 당시 판정 | 결함 | 부활? | 출처 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C1 | profit_path v2 KR/US(shadow artifact) | 07-10 학습, 이후 재학습 없음 | 진입 게이트 | net60 = ret60 − cost(KR 0.21 / US 0.50) ≥ 0.25 **AND** MAE60 > −2.5 | 수치 15 + 범주 10(candidate_audit·counterfactual path) | OneHot → SGD log elastic-net → isotonic. 런타임은 portable_linear_v1 | 단일 분할 train / cal 3일 / val 3일 / purge 1일 | KR train 24,993 · val 4,149. US 40,678 · 6,414. val 약 3세션 | AUC KR 0.538 / US 0.638. **보정 상한 KR 0.4917 / US 0.2975 < 0.55 → selected 0** | shadow, "데드락"(07-13) | **F7**(런타임 OOD ≈100%, `change_pct` 등 null. `market_open_elapsed_min` 학습 100% / 추론 0%. entry_delay 의미 불일치). F6(val 3세션). 구조적 발화 불가 | 아니오(모델). 배선은 수리 필요 | tools/train_profit_path_shadow.py, state/models/profit_path_{KR,US}.json, debate_profit_path_promotion_deadlock_20260713.md, ac9b56d, b2e7520 |
| C2 | profit_path 09-27 부활 시험 | 09-27 | 동일 | 동일 | 결정 시점 피처 재계산(S3) | portable 점수 | 사전등록 T2(상위 20% − 당일 평균) | KR 3,119 / 51일. US 3,284 / 53일 | US T2 −0.08 t −0.70. KR T2 +0.54 t 2.93 → 개장 전 행(상위분위의 73%) 라벨이 장전 호가 진입. **체결 가능 층 top 절대 net −0.66(t −3.36).** "관측 이른 순" 단일 피처가 +0.94(t 4.27)로 모델보다 낫다 | 기각 | F2(판정에서 식별해 반영함). 정당한 기각 | 아니오. KR 장전 행의 시가 기준 재라벨 질문만 남음 | model_revival_profit_path_20260927.md |
| C3 | profit_path tier relabel lab, profit_evidence WF | 07-10, 07-15 | 진입 필터 | tier 도달(stop-first 합성) | 동일 | SGD, logit, RF | expanding WF, 1일 purge | 약 20세션 | 전 arm 합성 net 음수(US p80 −0.659 / KR p80 −0.804). 승격 창 0/17 · 0/16 | 폐기 | F6. topN은 lookahead 상한(문서 인정) | 아니오 | profit_path_tier_relabel_lab_20260715.md, validation_profit_trainer_v2_path_20260710.md |
| C4 | candidate path prediction lab(3상태 +3.6 / −2.5 first-passage) | 07-16 | 랭킹 | TARGET_FIRST | 첫 관측 스냅샷 점수·Claude 상태·일봉·KR rich | 다항 logit(C=0.25), RF, logit∩RF 합의 | 7월 고정 holdout | US 9세션 27픽. KR 약 12세션 | 최초 US +1.174 LCB +0.586 → **교차세션 버그 수정 후 −0.831**. KR Claude top-1은 동률·티커정렬 착시. KR system-score top-3 +1.105 LCB +0.515(3번째 관찰). 합의 shadow 07-25 재생 US 선택 0 | US 기각, KR 관찰 | F7(교차세션 진입 14/27, tie-break) F6 F11 | 아니오 | candidate_path_prediction_improvement_20260716.md, prediction_candidate_universe_strategy_20260716.md |
| C5 | 진입 피처 → 승패 AUC, ret_5m → 대박 | 07-23 | 진입 필터·사이징 | 승패, MFE≥3% | 11피처 | 단변량 AUC | IS | US 203거래 | change_pct AUC 0.591. ret_5m AUC 0.703(n48) → **정정 후 0.557(n203)** | "진입 예측 사망" | F7(canonical.mfe_pct 전파 갭, 7a2c5ae) F11 F8 | 아니오 | prediction_power_debate_20260723.md |
| C6 | us_swing_5d HGB(교재 봉인) | 07-10 / 07-11 | 랭킹(rank1 → 주문) | 5일 net ≥0.25 분류 + 5일 net 회귀 | Yahoo 수정주가 27피처, 교재 13,672행(≤2026-04-02) | HGB 회귀+분류 × seed 3, 순위 50:50 | 월 expanding WF, 7세션 purge | OOS 879건 / 293세션 | top3 +1.357% PF 1.512, 중앙 −0.030, block LCB −0.152 | micro 실주문 | F4 F5(불확실) F8(2026 집중) **F12(5일 고정 vs 계약)** F10(교재 128~168 대형주 vs 라이브 소형 급락주) | → C10 | implementation_us_swing_accelerated_20260710.md, c22726b |
| C7 | day_losers × 모델 선별 | 08-01 | 소스 필터 + 랭킹 | 계약 net | 70신호 | C6 | 무작위 17건 추출 20,000회 | 17건 / 3주 | 모델 +6.94 vs 모집단 −1.48, p=0.0000(AXTI 제외 4.23, p=0.0054) | 채택 | F6 F3(종목 단위 p) F8 | → 09-01 반전 | memory day-losers-lane-is-the-real-edge-20260801, e9add64 |
| C8 | 08-07 실험 묶음: calibration·계약 라벨·매트릭스 E1~E4·국면 분리·F6·W1 | 08-07 ~ 08 | 랭킹·허들·라벨 | L0 5일 / L1 TP12·SL25·D5 | 교재 13,672, 프록시 chg≤−5 807행 | HGB / RF / ENet, isotonic, 국면별 분리 | purged WF, seed 3 | 프록시 195세션. W1 5세션 | Brier 0.2642 > 상수 0.249. prob≥0.65 실측 35.4%·−1.106%. L0 −0.635 → L1 +0.123. HGB +0.123 > RF −0.275 > ENet −0.326. 나쁜장 분리 +1.899 LCB +0.39. W1 M0 +22.9%p | 허들 폐지(C안), 나머지는 재봉인 후보 | F10(프록시 ≠ day_losers) F12(D5) F4 F6(W1 5세션을 허들 폐지 근거로 씀) F11(9~15셀) | 아마(국면 분리는 후반 편중) | us_swing_dayloser_calibration_20260807.md, us_swing_model_matrix_20260807.md, us_swing_regime_capacity_20260807.md, us_swing_week_counterfactual_20260808.md |
| C9 | 교재 최신화 재학습 A/B | 08-24 | 랭킹 | 5일 net | 168종목, 교재 04-02 → 08-21 | HGB 봉인 vs as-of 재학습 | 세션별 as-of shadow | 60세션 | 쌍대 평균 −1.41%p, 중앙 +0.00, t −0.82, 승 24/60 | 기각(구분 불가) | F7(하네스 재현 0/6 → 16/29, 불일치 자동 제외) F6 F10 | → C10 S1이 답함 | f9f5818, memory textbook-refresh-no-benefit-20260824 |
| C10 | **09-01 US swing 모델 제거(역선별)** + 구제 3안 | 08-30 ~ 09-01 | 랭킹 | 계약 / 만기 net | 라이브 후보 풀(08-12~) | C6 | 사후 12세션 | **12세션** | rank1 −7.16%(승 8%). prob≥0.55 −4.50%(t −3.76, 단위 미기재). 계약 net r −0.113, MFE r +0.170. 현행 −5.83%(t −2.35) vs 제거 +0.89%(t 0.43). TP 재학습: 교재 +5.46 → 풀 −3.56 | 모델 제거 → `US_SWING_PICK_ORDER=dvol_desc` | **F6 F10 F12**. 사후 Codex 검토로 "역선별 입증"이 아니라 "무가치"로 정정. 정렬 버그(session_date 하루 밀림)로 일부 결론 철회 | **예(후보)**. §2-1 | system_diagnosis_and_selection_20260901.md:50,274-275, a41488e, memory session-handoff-20260901-model-removal |
| C11 | **09-27 US swing 부활 시험(주 모델 + 변형 6 + S1 / S2 / S3)** | 09-27 | 랭킹 | net_pct(TP12 / SL25 / D7 + BE4, 비용 0.5) − 같은 날 풀 평균 | `xus_fallen3` 35,288건, 27피처 신호봉 재계산 | HGB(주), logit / GB / RF × 라벨 2종, S1 = 27 + 풀 특성 8 | 월 확장 WF, −8세션 purge, 날짜 클러스터 t | 30,312건 / **256일** | 주 top-3 +0.619 t 1.70(전반 −0.331). 변형 최고 t 1.84. **S1 top-3 +0.995 t 3.09(+0.339 / +1.651). S2 top-3 +1.437 t 3.59, 교재 동결 후 t 0.67.** dvol 기준선 +0.594 t 1.83. max21 단일 t 2.56. 라이브 부분집합 안에서 S2 t −0.01 | 주·변형 기각, S1·S2 후보 | F5(현재 상장 CSV) F8(한 국면) F4(풀 대비이지 IWM 대비 아님) F11(S1 / S2는 보조 셀, 약 11검정) | **예(S1)**. §4-1 | model_revival_us_swing_20260927.md |
| C12 | KR E5-V6(US식 HGB), KR 알파 모델, KR 랭킹 12셀·E5 풀 | 08-01 ~ 09-11 | KR 랭킹 | 계약 D5, cost 0.25 | 급락 밴드 6,211행 / 12피처. 34,203행 | HGB, 규칙 V0~V6 | 월 WF, 연도 분리, 무작위 200 백분위 | 6,211 / 387세션(시험 332). E5 2026 785 / 55세션, 2025 81 / 23 | 모델 top1 −0.65 vs 할인순 −0.48 vs 무작위 −0.89 · 균등 +0.25. 알파 모델 top5 −3.58 vs 무작위 −3.28(p 0.70). V0 / V6 무작위와 구분 불가 | 기각. "알파 = 용량" | F12(D5) F4 F10(원시 밴드 ≠ R2 / R4). 가격 캐시 가변으로 08-07 재현 불가 | 아니오. 09-23 규모매칭 벤치에서 KR 고르기 가치 0으로 독립 재확인 | kr_us_style_model_20260810.md:6-21, kr_rank_verdict_20260911.md, universe_bench_20260923.md |
| C13 | 장기 종목 선택(12-1 모멘텀·저변동) | 09-24 | 랭킹(반기 20종목) | 반기 EW 초과 | 현재 S&P500 501 / KR 247종목, 2015~2026 | 규칙 | 사전등록, 반기 독립 | 반기 21개 | US 모멘텀 +10.13%p t 2.66 → 실제 ETF(MTUM vs SPY +1.2%p t 0.62)에서 소멸 | 기각 | **F5** F6(21단위) | 아니오 | longterm_selection_20260924.md |

### D. 학습형 ML — 방향·필터·출구

| ID | 이름 | 날짜 | 결정 | 라벨 | 피처·데이터 | 구조 | 검증 | 표본 | 결과 | 당시 판정 | 결함 | 부활? | 출처 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| D1 | 지수 ETF 방향 모델 36칸 | 09-27 | 방향 | T1 종가→종가 / T2 오버나이트 / T3 5일 | 069500·229200·SPY OHLCV + 거시 12종(KR 28 / SPY 25피처) | logit / HGB / RF / MLP 기본값 + base1 | 연 확장 WF, purge, 플라시보·양성대조 | 069500 3,585일 · 229200 1,626 · SPY 5,465 | AUC 0.455~0.560. 36칸 중 33칸 적중률 < 기저율. 초과 t 최고 +0.57. 비용 전 T2 보유 10bp vs 비보유 −1bp | 36 전부 기각 | **F14(p>0.5 매일 진입 → 보유비율 53~95%, 문턱 규칙 미시험, 보고서 자인)**. 비용 벽은 구조적 | 아마(낮음). 229200 T2 상위 분위 문턱은 패닉 v2와 겹치는지 먼저 확인 | direction_models_20260927.md |
| D2 | 패닉 밤 ML 필터(logit) + 변형 6종 | 09-27 | 진입 필터(메타라벨) | net > 0 | 233740(+229200) 신호, 8피처 | logit / HGB / RF | 연 WF | 201~234밤 | logit 받음−버림 +0.141 t 0.29. 변형 최고 +0.433 t 0.98. 기준선 "5일 누적 하락 큰 절반" +0.972 t 2.23 | 전부 기각 | F6(약 200밤 / 8피처). 정당한 기각 | 아니오 | panic_filter_model_claude_20260927.md, panic_filter_variants_20260927.md |
| D3 | 보유 중 SELL / HOLD 모델 | 09-27 | 출구 | y = 계약 최종 net < 지금 매도 net | 미실현·MFE / MAE·보유일·모멘텀·rv20·거래량비·지수 | logit / HGB / RF / MLP, 임계 0.5 고정 | 월 확장 WF, 라벨 해소 purge | KR 33,478거래 / 135,321시점. US 31,353 / 132,535 | KR RF +0.56%p t 1.24(4모델 모두 전반 음수). US 0~음수(logit t −2.20). 조기매도 49~74%. AUC 0.50~0.60. b2 / b3 단순 손절·트레일도 계약보다 나쁨 | 기각, TP12 / SL25 / D7 유지 | **F14(분류 + 고정 0.5가 절반 이상을 조기매도해 TP 꼬리를 깎음. 위험률 구조 미사용, 보고서 자인)** F8 | 아마. 생존 / 위험률 구조로 1회(§3) | exit_models_20260927.md |
| D4 | 원시 급락 스크린·조기 진입·경로 규칙(예측 8전8패 계열) | 07-19 ~ 08-06 | 타이밍·출구 | 실현 net | 분봉 | 반사실 규칙 | IS | 조기 진입 172건(0~15분 n=6) | 조기 진입 시뮬 +202%p → 미래정보 착시. 보유기간 부호 반전 → 선택편향 | 기각 | F1 F6. KST / UTC 혼동으로 검정 4개 무효(holding-period) | 아니오 | memory prediction-vs-structure-and-transplant-refuted-20260806, holding-period-is-selection-bias-20260728 |

**"예측 8전8패" 대응**(memory prediction-vs-structure-and-transplant-refuted-20260806):

| 항목 | 대응 행 |
|---|---|
| 바닥·경로 예측 | C4 |
| 국면 게이트 4패널 | B3 |
| 모델 확신도 | A6, C8 |
| 후보 풀 조성(월 스위치) | 상세 미기록 |
| Claude 선별(KR 역선별) | A13 |
| 조기 진입 | D4 |
| 추격 | antichase-net-replay-verdict-20260701(본문 미열람) |
| 무기한 보유 | debate-infinite-hold-refuted |

이 중 모델 품질 판정이 아니라 **표본·배선 판정**에 가까운 것은 "모델 확신도"(A6 필드 퇴화, C10 12세션)와 "Claude 선별"(A13 fwd5d gross)이다.

## 2. 방법이 잘못돼서 판정이 무효·불완전한 것

기준: 부정 판정을 뒤집을 수 있는 결함(F6·F7·F10·F12·F14, 그리고 라벨 자체의 F2)만 넣었다. 종목 클러스터 t(F3)는 양의 판정만 부풀리므로 부정 판정을 구제하지 않는다.

| # | 대상 | 원판정 | 결함과 증거 | 지금 상태 |
|---|---|---|---|---|
| 2-1 | **US swing 모델 제거(C10)** | "역선별", rank1 −7.16%, 12세션 | F6(12세션). F10(dvol 밴드 + MAX 통과 좁은 부분집합, 교재 128~168 대형주로 학습해 소형 급락주에 적용). F12(5일 고정 라벨 vs 계약 D7). 사후에 "입증이 아니라 무가치"로 정정(memory session-handoff-20260901-model-removal) | 09-27 넓은 풀 256일: 하위10%가 음수가 아니라 **역선별 재현 안 됨**. 풀 학습·월 재학습 S1 top-3 t 3.09. **다만 라이브 부분집합 안에서는 모델 우위 증거도 역선별 증거도 확정되지 않는다.** 09-27 S2 t −0.01, S1 t 1.37. 그 전 기록은 양쪽으로 갈린다: 모델 쪽으로는 08-20 모델 rank1 +0.89%(15/27) vs ATR −1.23%, 갈린 16세션 +2.80 vs −0.78(memory atr-ranking-refuted-20260820). 반대쪽으로는 08-15~21 승자 p 0.578 < 패자 0.623(n=11, memory rank1-winner-conditions-deep-review-20260815)과 08-25 밴드 내 모델순 +4.20 vs 역모델순 +5.22(n=11, 5bef8b7)가 있다. 셋 다 n≤27이다. 무효가 된 것은 "역선별" 판정이다. "라이브 규칙 안 순위로 쓸 가치"는 여전히 없다 |
| 2-2 | **profit_path 승격 데드락(C1)** | forward 13건 matched 0, selected_n 0 | F7: 런타임 OOD ≈100%(피처 25개 중 22개 결측, 상수 0.5369, b2e7520 / ac9b56d). 09-27에도 스냅샷 원장 KR 99.9% / US 100% OOD. 구조: isotonic 상한 < 문턱 0.55라 발화 불가. 토론문 라벨 서술("sell_target 스탑 전 도달")이 코드 라벨(net60 & MAE60)과 모순 | 09-27 결정 시점 재계산으로 **품질 판정은 새로 정당하게 기각됨**(C2). 남은 결함은 **런타임 피처 공급**이다. 어떤 모델을 매수 게이트에 붙이든 먼저 고쳐야 한다(runtime/profit_path_predictor.py:133, 게이트·스냅샷 호출부) |
| 2-3 | **Claude selection 무알파(A5)** | AUC 0.5075 / 0.5128, 07-08 콜 제거 | F2: fwd 기준가가 선정일 종가(ticker_selection_db.py:737, June 리더 보고). F6: 독립 37일. F3: 4.14배 중복. F8: 5월 강세 / 6월 약세 2개월. F4: 벤치 없음 | 판정은 "신호 없음"이라기보다 **검정력 없음**이다. 그런데 A12(33만 행)에서 WATCH +3.40 > AVOID −1.67 > BUY_READY −2.15로 순서 정보가 보인다(행 단위라 F3). **날짜 클러스터·지수 초과·체결 가능 가격으로 Claude를 판단기로 시험한 적이 없다.** A17(09-22)은 Claude 판단이 아니라 규칙이었다 |
| 2-4 | **보유 중 매도 모델(D3)** | 8검정 전부 기각 | F14: 분류 + 임계 0.5 고정 → 조기매도 49~74%. 비대칭 계약(TP12 꼬리)에서 "지금 파는 게 나은가"의 확률이 0.5를 넘는 것만으로는 기대값 판단이 안 된다. 비용·꼬리를 반영한 결정 규칙(기대 잔여 net < 0)은 회귀 변형으로 등록만 되고 메인 검정이 아니었다(docstring). 보고서도 "위험률 구조가 맞다"고 자인 | 불완전. 단 사전확률은 낮다. 단순 손절·트레일(b2 / b3)도 계약을 못 이겼다 |
| 2-5 | **패닉 Claude 필터 "통과"(A18)** | +0.978%p t 2.25 | ① 프롬프트에 `Signal strength flag: strong/normal`을 줌. TAKE 95건 중 30건이 strong이고 strong 41건 중 30건을 TAKE했다. ② **09-27 이 감사의 재계산(원장 `data/shadow/kr_index_etf_panic.jsonl` × 캐시 `panic_filter_20260927_claude.jsonl`, 234밤): 보통 신호 층 안에서 TAKE − SKIP = +0.439%p, Welch t 1.00(65 vs 128). 강신호 층 안 +1.729%p, t 1.55(30 vs 11).** memory universe-bench-and-design-direction-20260923(09-27 갱신분)에도 "보통 신호 안 t 1.0"으로 기록됨. ③ F13(docstring 자인). ④ 같은 날 판단기 9종 이상을 t≥2로 검정(변형은 2.64) | 5일 하락 기준선과의 겹침도 확인했다(같은 234밤, 해마다 ret5 하위 절반 119밤). TAKE 95 중 57이 그 절반 안에 있다. 절반 안에서 TAKE − SKIP은 +1.348%p, t 1.92(57 vs 62). 밖에서는 +0.263, t 0.53. 결론: **강신호 플래그를 상당 부분 재현했고, 어느 층 안에서도 t<2다.** 독립 가치 미입증이므로 원판정을 "후보 이하"로 낮춘다. ret5 절반 안에서의 t 1.92는 사후 층화라 가설로만 적는다 |
| 2-6 | 방향 모델(D1) | 36칸 기각 | F14: 매일 p>0.5 진입. 문턱·분위 규칙 미시험(보고서 한계 절 자인) | 불완전. 다만 비용 벽(KR 10bp, SPY 44bp)이 구조적이라 되살릴 공간이 작다 |
| 2-7 | 교재 최신화 A/B(C9) | t −0.82 기각 | F7: 하네스가 라이브 rank1을 첫 실행 0/6, 수정 후 16/29만 재현했고 불일치 세션은 자동 제외. F6: 60세션 | 09-27 S2 "교재 동결 후 89일 t 0.67" vs S1 "풀 학습·월 재학습 t 3.09"는 **재학습·학습 모집단이 중요하다**는 반대 방향 증거다. 08-24 기각은 이 질문에 답하지 못했다 |
| 2-8 | 밴드 + MAX OOS(b3f6151, 부록 X9) | "재현 실패" | F10: 일봉 재구성 프록시 ≠ 스크리너 모집단. 프록시가 IS 밴드 효과조차 재현 못 함 | 9266972에서 이미 "판정 불가"로 정정. 이후 09-12 IWM 초과로 밴드 증분 −0.05%p |
| 2-9 | hold advisor 가치(A14) | "tail-risk 축소기" | F7: `realized_net` / `hold_fwd_net`이 실제 gross, PathB 청산 outcome 미배선. F12: HOLD 대안이 무조건 3세션. F6: n 6~17 | 판정 자체가 성립하지 않았다. 다만 현재 sleeve 출구는 계약이 쥐고(isolated_exit_owner) 있어 적용처가 좁다 |
| 2-10 | trade_ready 이분 판정(06월, A5 하위) | 06-01 역전 → 06-26 enforce → 06-30 기각 | F2(선정일 종가) F6(실거래 10~28건). FORWARD ↔ 실거래 매칭 0 | 무효. 2-3과 같은 결론 |
| 2-11 | Claude confidence(A6) | 역신호 기각 | F7: 필드 퇴화(floor 0.5, 0.5~0.7 압축, hold conf 전원 0.72). bull_conf와 plan conf는 다른 필드 | 판정은 "필드에 정보가 없다"로 읽어야 한다. confidence 개념 자체의 기각은 아니다. 다만 재시험하려면 출력 설계(보정된 확률·근거 분리)를 바꿔야 해 우선순위가 낮다 |
| 2-12 | 모델 확률의 방향(5bef8b7 vs 09-01) | 08-25 "우위 측정 안 됨" / 09-01 "역선별" | 두 기록이 서로 반대다. 08-25 pooled 확률 3분위 net −2.85 / +2.41 / +3.81(단조, TP 완주율 15.8 → 31.6%). 09-01 §4-3 prob≥0.55 −4.50 vs <0.55 −1.53(역전, r −0.113). 창·모집단이 다르고 한 번도 맞춰 보지 않았다(F10 F6) | 미해결. 09-27 넓은 풀(C11)에서 Spearman(net) +0.016(t 1.41)이라 강한 역전도 강한 정순도 아니다 |
| 2-13 | us_swing forward 45건 "무엣지 −239%p"(08-01 오전) | corr −0.098, rank1 −4.33 | **F12의 가장 깨끗한 사례**: shadow 원장이 TP / SL 없는 5일 고정 보유라 실거래 계약과 다른 전략을 채점했다. 같은 날 철회(memory us-swing-5d-no-edge-axti-was-luck-20260801, day-losers-lane-is-the-real-edge-20260801) | 무효로 처리됨. 교훈: 라벨 = 실제 계약이 아니면 부정 판정도 무효 |

반대로 **정당한 기각**(방법 결함이 판정을 뒤집지 못함):

| 대상 | 이유 |
|---|---|
| profit_path 품질(C2) | 결정 시점 재계산, 사전등록, 층화 |
| KR 랭킹 모델 계열(C12) | HGB · 규칙 · 무작위 200 · 규모매칭 벤치로 네 번 독립 재확인 |
| 패닉 ML(D2) | |
| 장기 선택(C13) | 실물 ETF 교차검증 |
| 경로 lab US(C4) | 버그 수정 후 음수 |
| 07-01 텍스트 필터(A10) | |

## 3. 한 번도 안 해본 것 (증거로 확인된 것만)

코드 검색 `LGBMRanker|lambdarank|XGBRanker|conformal|lifelines|CoxPH|GaussianHMM|hmmlearn|jumpmodel|MAPIE` 결과: `*.py` 0건(09-27).

| 결정 × 방법·데이터 | 시도 여부 | 증거 | 비고 |
|---|---|---|---|
| 출구 × 생존·위험률(Cox, discrete-time hazard) | **없음** | 코드 0건. exit_models_20260927.md 결론 "다음 설계에서 다룬다" | 표본 KR 13.5만 / US 13.3만 결정 시점이 이미 있다 |
| 랭킹 × 쌍대·리스트 LTR(LambdaMART) | **없음** | 코드 0건. us_swing은 점별(회귀 순위 + 확률 순위 50:50). E3 csrank는 "횡단면 순위를 회귀 타깃"으로 한 것이지 쌍대 손실이 아니다(us_swing_model_matrix_20260807.md) | memory에 "횡단면 rank 목표 무의미 — 종결"이 있다. LTR 손실과는 다른 시험이다 |
| 진입 × conformal 기권 | **없음** | 코드 0건 | 보정 기권 유사물은 있었다: isotonic(C1, C8), B' conf≥0.6(A18), logit∩RF 합의(C4 — 상시 기권으로 검증 불가) |
| 국면 × HMM / Statistical Jump Model | **없음**(등록만) | f51a41d, ACTIVE_WORK.md:190. 코드 0건 | 규칙 국면 게이트(B3)와 Claude 분석가 국면(A1)만 있었다 |
| 텍스트 × Claude 공시 본문 판정의 결과 채점 | **배선됨·미채점** | kr_event_lane.py:298-322(sonnet-4-6이 본문 4,000자를 읽어 quality 출력). signals 2,550 중 응답 12건. 채점 리포트 0건 | memory universe-bench-and-design-direction-20260923 "Claude 공시 원문 판독 = 09-27 다음 과제" |
| 텍스트 × 뉴스 LLM(PIT·결과 채점) | 사실상 없음 | 뉴스는 selection 힌트(f7366e3)와 preopen overlay(A7, 암기·holdout 소진)에만 쓰였다 | |
| 사이징 × 모델 기반(예측 크기 → 비중) | 없음 | 사이징은 규칙(2단 사이징, A2 ×1.5, MILD_BEAR ×0.5)만. confidence 사이징은 필드 퇴화로 기각(A6) | 패닉 레인 강신호 2배 → 3배는 규칙 레버(+1.16%p, memory full-system-audit-20260912) |
| 펀더멘털 횡단면 모델 | 증거상 없음(불확실) | 장기 선택은 가격 팩터만(C13). "퀄리티는 PIT 불가"(alpha_hunt_and_design_20260719.md) | PIT 재무 데이터 소스가 선결 과제 |
| 이종 모델 스태킹·앙상블 | 부분 | seed 평균, 회귀 + 분류 순위 융합(C6), logit∩RF 합의(C4)만 있었다 | 모델 + 규칙 스태킹은 없음 |
| lesson 감쇠(FinMem식 중요도 감쇠) | 없음(등록만) | f51a41d, ACTIVE_WORK.md:186. 감쇠 구현 커밋 없음 | lesson 주입 자체가 무효과(A3)라 우선순위 낮음 |
| 스트리밍 온라인 학습(증분 fit) | 없음 | 휴리스틱 온라인(brain 카운터, adaptive_params, consensus 가중)과 월 재학습 WF만 있었다 | 우선순위 낮음 |

**이미 해본 것**(요청 예시였지만 "안 해본 것"이 아님):

| 항목 | 시도 |
|---|---|
| 라이브 신호 메타라벨링 | D2, A18, B4 H1 / H2, dip_entry_gate(loss_cap_entry_selection_20260721.md) |
| 확률 보정 | C1, C8 |
| 국면 분리 학습 | C8 |
| 월 재학습 | C11 |

## 4. 살려야 할 것 (우선순위)

| 순위 | 대상 | 근거 | 먼저 할 것 | 반대 근거 |
|---|---|---|---|---|
| 1 | **US swing S1**(HGB 27 + 풀 특성 8, 넓은 −3% 풀 학습, 월 재학습)을 `virtual_books` 쉐도우 arm으로 | 256일 WF top-3 +0.995%p, 날짜 t 3.09, 양 반기 양수(model_revival_us_swing_20260927.md). 09-01 제거 근거(2-1)가 무효화됨 | ① IWM·규모매칭 계약 벤치 초과 재측정. ② 대조 arm = max21 단일 정렬(t 2.56) + dvol 정렬. ③ 고변동 소형주 슬리피지 추가 스트레스. 쉐도우 편입은 운영자 승인 필요 | 보조 셀(약 11검정 중). 우위 상당 부분이 변동성 / MAX 쏠림. 생존편향(상폐 누락)이 이 방향을 특히 부풀림. 한 국면. 09-12 라이브 규칙은 IWM 초과 0 |
| 2 | **Claude 공시 본문 판정 채점**(A19) | 이미 배선돼 있어 추가 비용이 작다. 이 저장소에서 유일하게 "가격 밖 새 정보"를 Claude가 읽는 경로 | 응답 12 / 2,550 → LLM 호출 실패·비활성 원인부터 확인. quality(strong / weak / skip)별 유령 체결 net을 지수 초과·날짜 클러스터로 사전등록 채점 | 표본 0. 공급계약 다음날 0(DART 12개월 재생)이라 장중 반응 속도 싸움 |
| 3 | **출구 위험률 모델**(D3 재설계) | 모델 클래스 오류로 판정 불완전(2-4). 결정 시점 26.8만 개가 이미 있다 | 기대 잔여 net < 0 결정 규칙, TP 꼬리 보존 제약, 날짜 클러스터 사전등록 | 단순 손절·트레일이 계약을 못 이김. KR 모델은 후반기만 양수 |
| 4 | **Claude 판단기 정직 재시험**(선별 veto) | 2-3: 한 번도 체결 가능 가격·날짜 클러스터·지수 초과로 시험 안 함. A12 WATCH / AVOID 순서 정보 | 가상 북 풀에서 날짜·연도를 가린 블라인드 **AVOID(배제)** 판정 → 받은 것 − 버린 것, 날짜 t, 규모매칭 벤치. 피처에 기존 규칙 플래그를 넣지 않는다(2-5 교훈) | A13 KR 급락 선정 −4.65%. 09-23 KR 고르기 가치 0. F13 암기 누수를 완전히 막기 어렵다 |
| 5 | profit_path **런타임 피처 공급 수리** | 모델 부활이 아니라, 매수 게이트에 어떤 모델이든 붙이기 위한 선결 조건(2-2) | 게이트·스냅샷 호출부가 audit DB 피처를 넘기도록. schema-hash 단일 builder(memory kr-foreign-flow-already-live-and-calendar-gap-20260718) | 코드 변경이므로 운영자 지시 필요. 현재 붙일 모델이 없다 |
| 6 | KR 장전 관측 행의 **시가 기준 재라벨** | C2 사후 진단: 장전 행 상위분위 절대 net +0.60(t 1.43) | 시가 진입 60분 라벨로 재측정 + 지수 초과 + 패닉 야간 축과 중복 확인 | 호라이즌(60분)이 현 매수 레인(7일)과 다르다 |

**살리지 않을 것**: profit_path(결정권자), KR 랭킹 모델 전 계열, 방향 매일 판정, confidence 사이징, preopen PROMOTE 프롬프트, historical_sim 재생 학습, 09-22 규칙, 패닉 Claude 필터(단독).

현재 자본 기준 월 금액: **산출 불가**. 1순위 S1도 풀 평균 대비 수치뿐이고 IWM 초과·체결 용량이 없다.

## 부록 — 본표에 합치지 않은 모델 인접 시도 (한 줄씩)

| ID | 시도 | 날짜 | 결과 | 결함 | 출처 |
|---|---|---|---|---|---|
| X1 | Claude READY vs pool bootstrap CI | 07-10 | US 6월 −2.49 [−3.35, −1.65], 7월 −4.34 | F3(비독립 202k 행으로 CI) F2 | design_candidate_discrimination_claude_trainer_profit_20260710.md §2.3 |
| X2 | PathB 위험점수 배분기(고정 / 적응) | 07-15~16 | 전체 −88.91 → −39.01 / −42.30, 4~5월은 악화 | F11 F8 | current_system_profit_max_lab_20260715.md, _20260716_reaudit.md |
| X3 | dip_entry_gate(US ret_5d < −5 배제) | 07-21 | 배제 51건 −42.2%p, 잔존 −13.6 | F11(임계를 같은 표본에서 선택) | loss_cap_entry_selection_20260721.md, 183f1a1 |
| X4 | MAX 회피 + 외부 72종목 검증 | 07-19 | 외부 월간 高MAX가 오히려 +2.67 vs +1.09 | F5 | alpha_hunt_and_design_20260719.md |
| X5 | 무봉우리(MFE5d<4%) 예고 변수, 모델확률 포함 | 08-28 | 모델확률 d −0.35, 밴드 내 역전 −1.00%p, t<2 | F3(티커 클러스터) F10 | tools/research_no_bump_predictors.py |
| X6 | rank2 폴백 제안 | 08-12 | 조건 전량 n=177 +1.92% vs rank1 +0.12% | 건 가중 vs 세션 1픽 단위 혼용 | proposal_rank2_fallback_20260812.md |
| X7 | 거래대금 밴드 재선택 | 08-20 | rank1 +0.03% t 0.04 → 밴드 +3.67% t 4.80(종목 클러스터 2.63). IWM 초과 증분 −0.05%p | F3 F4 | 94336bd, us_swing_excess_20260912.md |
| X8 | FINRA 공매도 비율 | 08-18 | 모델확률 통제 시 소멸 | 모델 점수와 공선 | memory finra-short-ratio-refuted-20260818 |
| X9 | 밴드 + MAX OOS 2023~2025 | 08-25 | 21,559건 / 1,445종목, 변별 없음 → "판정 불가" | F10(프록시 검정력 부족) | b3f6151, 9266972 |
| X10 | 모델 역공학 US 5조건 / KR 8조건 | 08-01 | US +1.152 / 건, KR +3.347 / 건(n=141) | F11(검증기간 참조) F6 | memory candidate-selection-and-exit-verified-20260801 |
| X11 | Claude 가격 플랜 RR 필터 WF | 08-01 | RR≥2 & 목표 3~6% WF +62.2 / +194.4 / +28.0, 현실 보유 D1~3에선 2/3 적자 | 회전율·포지션 상한 미반영 | memory us-profit-path-rr-filter-and-hold-20260801 |
| X12 | A2 ready1 × 1.5, entry_priority 분위 사이징 | 06-26 | 모집단 −0.26%, maxDD −42 → −63%p. KR HIGH −4.32 | F6(N=17) F8 | improvement_before_after_20260626, debate_system_improvement_20260626 |
| X13 | 청산 오버레이 cap2 + MFE | 06-01 | −0.172 → +0.679 PF 2.01 | F1(경로 순서 모름) | live_improvement_simulation_kr_all_sim_20260601 |
| X14 | KR 진입경로 10종 반사실 | 06-01 | 전부 음수, DATA_MISSING 47% | F7 | counterfactual_path_review_kr_all_sim_20260601.md |
| X15 | TSMOM / 저변동 외부 백테스트 | 07-07 | 저변동 −3.05%/월(t −2.59), TSMOM +4.72%/yr(t 1.82) → 재실행 t 0.61 / 1.22 | F5 | gross_alpha_hunt_verdict_20260707.md §5c~d |
| X16 | adaptive_params 4단, grid_search, entry_priority, ExecutionAdvisor | 03-30 ~ 06-02 | 평가 기록 없음. ExecutionAdvisor는 off | 인샘플 룩백 | strategy/adaptive_params.py, phase1_trainer/, execution/execution_advisor.py |
| X17 | ml/ 결정 로그 DB(학습 재료) | 04-08~ | 약 26.8만 행 중 pnl_pct가 채워진 행 14개 | F7 | ml/, 코드 리더 보고 |
| X18 | forward_gate_watch 승격 판정 | 09-09 | n 30, 2세션, t 29.7 → CANDIDATE_STRONG | F6 F3 → P0 수리 | independent_training_bot_review_20260909.md §4 |

## 불확실 표기

- A5 "37 독립일"은 June 리더가 decisions.db를 읽기 전용으로 실측한 값이다. 이 감사에서 재실행하지 않았다.
- hold advisor 마지막 호출 07-31은 코드 리더의 로그 확인이다. 런타임 경로는 미검증이다.
- C10 t −3.76의 클러스터 단위는 원문에 없다.
- C6 교재 유니버스 크기(128 vs 168)는 문서 간 불일치가 있고 풀지 못했다.
- B3 KR MA20 OOS 벤치가 KOSPI인지 KOSPI200인지 확인하지 못했다.
- 2-5 층별 t는 이 감사의 사후 계산이다. 사전등록 밖이므로 판정 강등 근거로만 쓴다.
