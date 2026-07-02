# 외부 리서치 기반 개선 리포트 (세금 제외) — 2026-06-25

## 0. 범위와 방법

5축 외부 리서치(세금·데이터소스·regime·패시브상품·LLM문헌) 중 **세금은 TODO #1/#2로 분리(turtlebot 영역)**. 이 리포트는 나머지를 "기존 claudetrade 시스템에서 *무엇을 어떻게* 바꿀지"로 정리한다.

핵심 메타: **4축이 독립적으로 같은 곳을 가리킨다 — 활매매 부활 각도(데이터·regime)는 비용 후 수익 추가 불가로 닫혔고, LLM 문헌은 우리 무엣지를 6~8회 확증한 데 더해 *기계적 이유*(다중검정 위양성·암기 누수)까지 줬다.** 활매매 쪽에 남은 단 하나의 실행 가능한 교훈은 "청산 규칙을 풀지 마라"이다. 따라서 이 리포트는 *새 기능 추가*가 아니라 **보존·보정·측정 규율 강화**가 중심이다(CLAUDE.md "덜어내는 처방 우선").

본 리포트는 분석/설계만 담는다. 코드 변경은 운영자 지시("수정해/구현해") 후에만.

---

## 1. [최우선] 청산 = LLM 지능이 아니라 규칙 스캐폴딩

### 발견 (외부)
- 우리 라이브 결론(selection 무엣지, 청산이 레버)은 외부 컨센서스와 정확히 일치. *LiveTradeBench*(arXiv:2511.03628, 21 LLM·50일 라이브): "벤치마크 똑똑한 LLM ≠ 더 나은 트레이더, 일관된 라이브 알파 없음." Lopez-Lira 헤드라인 알파도 25bps 비용에서 반토막·소형주 집중.
- **반전(핵심):** 외부 문헌은 "LLM이 청산을 잘한다"고 말하지 **않는다.** *arXiv:2602.07023*(2026): 제약 없는 LLM 에이전트는 **disposition effect를 그대로 재현**(패자 잡고 승자 판다). *Odean 1998*: disposition effect는 실제 비용(잡은 패자가 판 승자보다 underperform). *Kaminski & Lo 2014*: stop-loss의 "stopping premium"은 **모멘텀/regime 구조**에서 나오지 청산의 영리함에서 나오지 않음.

### 기존 시스템 매핑
우리 청산 가치의 진짜 원천은 LLM 재량이 아니라 **네가 깐 규칙 스캐폴딩**이다:
- 하드스톱 / loss_cap (`runtime/pathb_runtime.py`)
- `_pathb_profit_ladder_floor/_signal` (CLOSED_PROFIT_LADDER)
- `AUTO_SELL_REVIEW` HOLD cooldown guard + `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true`
- 손으로 주입한 `HOLD_ADVISOR_PROFIT_GUARD_ENABLED` prior (← *손으로 넣어야 했다는 사실 자체가 증거*: LLM이 본능적으로 익절하지 않아 외부 규칙으로 강제한 것)

즉 이 규칙들이 disposition effect를 자동으로 제거하는 장치이고, LLM은 그 **안에서** 조건부 판단자일 뿐이다.

### 개선안
1. **보존(코드 무변경):** 위 보호 규칙들은 이미 CLAUDE.md 보호영역. 외부 근거가 그 계약을 *강화*한다. "LLM이 청산 잘하니 규칙 풀자"는 정확히 함정(arXiv:2602.07023이 직접 경고). → 보호영역 완화 시도를 외부 근거로 한 번 더 봉인.
2. **프레이밍 보정(메모리/문서):** `project_exit_is_the_edge_20260620` 메모리의 "청산이 엣지"를 **"청산 엣지 = 규칙 스캐폴딩이 disposition effect를 제거한 결과, LLM 지능 아님"**으로 재정의. 함의: 레버는 "LLM에게 더 맡기기"가 아니라 "규칙을 더 정확히/촘촘히 깔기"(트레일링 청산 레버가 정확히 이 형태 — `project_trailing_exit_lever_20260621`).
3. **진행 중 A/B 재해석:** `HOLD_ADVISOR_PROFIT_GUARD_ENABLED` forward-validation(verdict ~2026-07-21, exit n_sell≥30)을 이 렌즈로 본다 — prior가 *disposition effect 교정*으로 작동하는지(익절 우선이 net 개선하는지)가 측정 질문. 음수로 뒤집히면 prior가 교정이 아니라 승자 조기절단 → 롤백. 측정창은 cutoff 이후 클린 데이터만(아래 §3).

### 안 할 것
- 청산 규칙(하드스톱/ladder/AUTO_SELL_REVIEW) 완화. 외부+내부 근거 모두 반대.
- "LLM 청산 지능"을 가정한 신규 재량 확대.

---

## 2. regime 필드 재건 — 느린·매크로·drawdown 전용으로만

### 발견 (외부)
- 신뢰 순위: **credit spread / Excess Bond Premium(가장 견고)** > NFCI(금융여건지수) > trend/200dma·TSMOM(*drawdown만, 수익 추가 X, 강세장 whipsaw*) > 수익률곡선(방향 O, 타이밍 X·선행 8~20개월) > VIX레벨·breadth(취약/contested).
- *Zakamulin 2014*: MA·TSMOM 백테스트 초과수익의 상당부분은 **데이터마이닝/look-ahead**, 적정 OOS+비용 적용 시 후반기 통계적 유의성 소멸.
- 메타: **regime 타이밍은 drawdown은 줄여도 비용 후 수익을 추가하지 못한다.** "time in market" 지배(Asness: best/worst day 대칭). 빠른(일중) regime flip은 whipsaw 비용으로 죽는다.

### 기존 시스템 매핑
- `market_regime` 필드 **0/757 = 확정버그**(무결성 감사 P0, `project_integrity_audit_20260623`). 근본원인 = 영속화/전파 버그(진입 캡처 아님).
- 메모리상 우리 "유일 분리자 = regime/베타"(`project_passive_pivot_decision`) → 즉 regime은 *베타 노출*일 뿐 selection 알파가 아니라는 외부 증거와 정확히 일치.

### 개선안
1. **재건 시 설계 원칙(외부가 정해줌):** regime 필드를 고칠 때(영속화 버그 수정과 별개로) — **느린 매크로 신호 기반 + drawdown-reduction 용도로만.** 빠른 technical 일중 flip 금지(whipsaw). 입력 후보: HY credit spread(FRED `BAMLH0A0HYM2`), NFCI(`NFCI`), 200dma trend. 전부 FRED 무료.
2. **알파로 쓰지 마라:** regime을 종목 selection 알파가 아니라 **exposure/sizing 축소 게이트**로만. 우리 RISK_ON/OFF 모드가 이미 그 방향 → credit spread를 *입력 보강*으로만 검토(신규 전략 아님).
3. **정직한 대안 제시:** regime이 곧 베타 노출이라면, *활매매로 베타를 타이밍*하기보다 **패시브로 베타를 직접 사는 게 더 정직**(이게 turtle 전환의 논리). regime 재건은 "활매매 살리기"가 아니라 "기존 RISK_ON/OFF 게이트의 입력 품질 복구"로 한정.

### 안 할 것
- 빠른 일중 regime classifier. 비용/whipsaw로 죽음(Zakamulin·Faber).
- regime을 알파 엔진으로 기대. 외부 메타가 명시적으로 반대.
- regime 필드 값 위조/추정 채움(이전 금지 규율 유지) — 버그는 고치되 값은 실측만.

---

## 3. 데이터 입력 — KR flow 타이밍만 살리고 나머지는 닫는다

### 발견 (외부)
- 거의 모든 무료/저가 소스가 약하거나 과차익: 13F(45일 stale 🔴), 다크풀 무료(1주 지연 🔴), Google Trends(비용 후 사망 🔴), AAII(극단에서만 🔴), short interest(월2회 지연 🟡).
- 그나마 정당한 3개(전부 "새 알파"가 아니라 입력 품질 보강):
  - **KR 외국인·기관 확정수급** — KRX 발표 타이밍 확인: **잠정 ~15:45 / 확정 ~18:00**(`data.krx.co.kr` MDCSTAT024). 우리 1일 밀림 버그의 근본 = 같은날 종가 후 수급을 같은날 수익과 맞춘 동어반복.
  - **Form 4 insider 매수** — 거래 후 2영업일 공시(13F보다 신선), 매수만 예측력(매도는 0), 단 시간축 6~12개월·소형주 편중.
  - **FRED 매크로** — regime 입력용(§2).

### 기존 시스템 매핑
- `KR_FLOW_ENTRY_GATE_MODE=shadow`(현재), `supplement_collector.fetch_investor_flow_kr` stck_bsop_date 전일매칭 fix(미검증), `bot/kr_investor_flow_cache.py` untrusted 가드. 메모리 `project_kr_flow_lever_20260623`: 소급검증은 1일 밀림으로 동어반복=무효 판명·철회 → flow 예측력 **미검증**.

### 개선안
1. **KR flow는 올바른 타이밍으로 shadow 검증만:** 외부가 확인해준 발표 타이밍(**T일 18:00 확정치 → T+1 진입 판단**)을 기준으로, KRX를 KIS와 *독립 cross-check* 소스로. `flow_date_matched` 비율 확인 → shadow funnel 누적 → **kill 바 통과 전 enforce 절대 금지**(KR 개선 트랙 규율). 매칭률 낮으면 KIS가 당일행만 주는 것 → 데이터 소스 재검토.
2. **Form 4 insider 매수:** 멀티데이 hold PathB 후보의 *conviction bias 입력*으로만 *검토 후보*(PEAD와 동급, override 아님). 단 시간축 미스매치(6~12개월 vs 우리 멀티데이)로 **우선순위 낮음** — 지금 만들지 않음, 백로그.
3. **"새 데이터로 알파 찾기" 트랙을 명시적으로 닫는다:** 메모리 6번 경고 + 외부 확증("데이터 얇아서 무엣지가 아님"). 이게 *덜어내는 개선*이다. 데이터 흥분이 또 오면 이 리포트로 닫는다.

### 안 할 것
- 13F/다크풀/Google Trends/AAII 통합. 전부 우리 비용 구조에서 죽음.
- KR flow enforce 전환(검증 전). shadow→kill 바만.

---

## 4. 측정 규율 — 누수/PBO (cross-cutting, 가장 저비용 개선)

### 발견 (외부)
- **암기 누수:** Lopez-Lira(2504.14765) + *Profit Mirage*(2510.07920): LLM은 cutoff 이전 값을 recall, "역사적 경계 지키라"는 지시·마스킹으로도 못 막음. cutoff 이전 백테스트 = 오염. *FactFin/counterfactual*로만 인과 학습 강제 가능.
- **다중검정:** PBO(López de Prado), Deflated Sharpe, Harvey-Liu-Zhu(다중검정 보정 시 **새 팩터는 t>3.0 필요**, t>2는 무의미). 설정을 많이 시도할수록 백테스트 과적합은 확실.

### 기존 시스템 매핑
- 우리 "6/19부터 클린 측정" 규율 = 문헌상 **유일하게 valid한 길**(독립 확증).
- 메모리 KR 무엣지의 "단일월·outlier·lookahead corr 0.77" 함정 = 버그가 아니라 **다중검정 위양성의 교과서적 서명**.
- `lesson_validation.py` 파이프라인(forward-validation, `LESSON_VALIDATION_*`) 이미 존재.

### 개선안
1. **명문화:** "cutoff 이전 백테스트는 검증으로 인정하지 않는다, forward(cutoff 이후)만 valid"를 측정 규율로 문서화(CLAUDE.md 또는 lesson_validation 계약). 이미 사실상 하고 있으나 이유(암기 누수)를 외부 근거와 함께 박는다.
2. **통계 바 추가 검토:** 새 가설 채택 바를 단순 부호일관(`LESSON_VALIDATION_MIN_SESSIONS=2`)에서 **다중검정 인지(시도 횟수 추적 → Deflated Sharpe/Harvey t>3 정신)**로 강화 검토. 토글 A/B 반복이 PBO를 올린다는 자각 → A/B 횟수 자체를 줄이고 표본을 키운다.
3. **저비용 즉효:** 이건 코드 거의 안 건드리고 *규율 강화*만으로 가장 큰 함정(위양성 채택)을 막는다 — 4축 중 ROI 최고.

### 안 할 것
- cutoff 이전 데이터로 LLM 전략 "검증". 프롬프트로도 못 막으니 무의미.

---

## 5. 범위 밖 (분리) — 패시브 상품 → turtlebot

패시브 상품 스펙(SPYM 0.02%/SPY 회피, TIGER 미국S&P500 실부담 0.1387%<KODEX 0.2281%, KODEX 200TR 과세이연, 단기채 ETF sleeve, 연1회+5/25밴드+신규자금 우선 리밸런싱)은 **claudetrade 개선이 아니라 turtlebot 설계**다. 세금 TODO(#1/#2)와 함께 `E:\code\turtlebot` 핸드오프로 이관. 이 리포트 범위 아님.

---

## 6. 우선순위 + 직언

| # | 개선 | 형태 | ROI | 비고 |
|---|---|---|---|---|
| 1 | 청산 규칙 스캐폴딩 **보존** + 메모리 프레이밍 보정 | 문서/메모리 | 높음 | 코드 무변경, 보호영역 강화 |
| 2 | 측정 규율(누수·PBO) 명문화 + 통계 바 검토 | 문서/규율 | 높음 | 가장 저비용·위양성 방어 |
| 3 | regime 재건 시 설계원칙(느린·매크로·drawdown·credit spread) | 설계 가이드 | 중 | 영속화 버그 수정과 별개, 알파 아님 |
| 4 | KR flow 올바른 타이밍 shadow 검증 | 기존 shadow 규율 | 중 | enforce 전 kill 바, 진행 중 |
| 5 | Form 4 insider 매수 입력 | 백로그 | 낮음 | 시간축 미스매치, 지금 안 함 |

**직언:** 이 리포트의 절반은 "새로 만들지 마라"다. 4축 외부 리서치가 독립적으로 같은 결론에 도달했다 — **활매매 부활 레버는 데이터에도 regime에도 없고, LLM 문헌은 우리 무엣지를 기계적으로 설명한다.** 가장 가치 있는 개선은 ①청산 규칙을 *지키는 것* ②측정 규율을 *조이는 것*이지 새 기능이 아니다. 패시브 전환(turtlebot)이 옳다는 걸 외부가 5방향에서 재확인했고, 활매매 쪽엔 "규칙 풀지 마라" 한 줄만 남는다.

**유일하게 의미 있는 신규 가설:** KR flow(올바른 타이밍) — 그러나 이것도 shadow→kill 바, 미달 시 닫는다.

### 출처 (대표)
- LLM: arXiv:2511.03628(LiveTradeBench), 2304.07619(Lopez-Lira), 2504.14765(Memorization), 2510.07920(Profit Mirage), 2602.07023(disposition replication), Harvey-Liu-Zhu(RFS 2016), López de Prado(Deflated Sharpe).
- regime: Gilchrist-Zakrajšek(AER 2012, EBP), Zakamulin(JAM 2014), Faber(SSRN 962461), AQR(Century of Trend), Asness(N Best Days), Chicago Fed NFCI.
- 데이터: FINRA short volume, KRX MDCSTAT024, SEC Form 4, FRED.
- (세부 출처·날짜는 5개 리서치 에이전트 원본 브리프에 수록)

---

## 7. Red-team 정정 (2026-06-25, 토론 + 코드검증 후) — §1~6을 일부 supersede

운영자 지시로 위 5개 권고를 코드로 직접 검증 후 토론. **§1~6은 메모리 기반이라 일부 틀렸다. 아래가 최종이다(조용히 안 엎고 이력 보존).**

**검증으로 드러난 사실:**
- `market_regime`은 consensus_mode에서 계산·DB 저장되나 **라이브 게이트가 아니다**(진입 차단/사이징 어디서도 미사용, `adaptive_live_condition.py`). 0/757 버그는 d056fad로 이미 수정(측정 전용). 외부 신호 입력 없음.
- `lesson_validation.py`는 **forward-only**(would_be_fwd vs actual_fwd), lookahead 누수 0, enforce 현재 안전 no-op(valid_apply=0). → 측정 규율 명문화 대상 아님(이미 깨끗).
- **진짜 누수 표면:** `historical_sim.py`·`preopen_candidate_replay.py`·`simulate_hold_advisor_decision_modes.py`·`tier1/fasttrack_quality_check.py` 5개 분석도구가 **과거 날짜를 `client.messages.create()`로 재생** → cutoff 이전이면 모델이 결과를 "알고" 답(Lopez-Lira 2504.14765 / Profit Mirage). **라이브 무해, 분석/결론 오염.**

**정정된 verdict:**

| # | §1~6 원래 | red-team 최종 |
|---|---|---|
| ① 청산 보존+보정 | "개선" | 보존=no-op. 메모리 1줄 주석만(싸구려 가드레일) |
| ② 측정 규율 | "최고 ROI" | **타깃 틀림.** lesson_validation 깨끗 → 진짜는 **5개 LLM replay 도구 pre-cutoff 결과 검증 불인정 + 과거결정 의존 점검**(hold_advisor 원prior −8.91%p가 그 사례). 분석 위생, 라이브 무변경 |
| ③ regime 재건 | "느린·credit spread 재건" | **🔴 방향 거꾸로·기각.** regime은 게이트 아님 → 없는 게이트 재건 + 수익없는 신호 추가 = over-build. **손대지 마라, 측정 정확도만** |
| ④ KR flow | "개선" | 신규 액션 없음(진행 중 shadow). 외부는 KRX 18:00 확정 타이밍만 추가 |
| ⑤ Form 4 insider | "백로그" | **완전 기각**(시간축 미스매치 + 패시브 전환 + 데이터 흥분 경고) |

**최종 결론 — 살아남는 행동 2개(둘 다 코드 아닌 규율/메모리):**
1. **②:** pre-cutoff LLM replay 도구 5종 결과는 검증으로 인정 안 함 + 과거 청산/prior 결정이 거기 기댄 적 없는지 점검.
2. **①:** 메모리 1줄 — 청산 엣지 = 규칙 스캐폴딩이 disposition effect 제거(arXiv 2602.07023), LLM 지능 아님 → 규칙 풀지 마라.

§1~6의 "5개 개선"은 과대청구였고, 코드 검증 후 사실상 ②(타깃 정정)·①(주석)만 남는다. 4축 리서치 메타("새로 만들지 마라, 패시브가 맞다")와 일치.
