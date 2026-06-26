# 토론 리포트 — 교훈 DB·운영 (2026-06-26)

> 명제(운영자: 세 개 모두): ①교훈 forward-validation+param tuner는 net 기여 작동 장치인가 ②enforce 무장 안전한가 ③brain.json이 가치를 보태나.
> 방식: PRO(작동·가치)/CON(무효과·오염·위험) + 사회자 직접 검증. READ-ONLY.

---

## 1. 사회자 검증 (핵심 사실)

| 사실 | 값 | 출처 |
|---|---|---|
| 공식 lesson_validation 셀 verdict | marginal 1·pending 3·insufficient 5·invalid_block 1 — **valid_apply 0** | `data/lesson_validation.db` |
| 스테일 PoC DB verdict | **valid 3**·invalid 1·neutral 2 (공식과 모순=혼선원) | `data/analysis/lesson_validation.db`(#3로 deprecate) |
| enforce 무장 vs docstring | `.env.live` ENABLED=true·APPLY_MODE=**enforce** / docstring "기본 OFF" | `.env.live:543` vs `lesson_validation.py` |
| 입력 오염 | selection_log raw 15,633 vs distinct 3,778 = **4.14x 중복**, trade_ready flip **194키** | dedup 미적용 |
| param tuner | 447세션 / 조정 88 / 진입 6 / 조정→진입 N=1(손실) | `param_sessions` |
| brain 라이브 주입 | **차단**(V2_BRAIN_POLICY=fresh_v2, brain_section skip) | `.env.live:164`, `analysts.py:2750` |
| brain 오염 이력 | 2회 청소(brain_quality 5/1, pollution 6/22) + historical_sim 주입(오늘 가드) | `state/brain.json` meta |

---

## 2. 명제별 판정

### 명제1 — 작동하는 net 기여 장치? → **부분 (설계 작동, net 기여 미입증)**
- **설계 골격 깨끗(양측 합의):** forward-only(would_be=watch_only fwd vs actual=trade_ready fwd) → lookahead 0. invalid_block 1건(KR/risk_off gain −3.32, conf 1.0)이 함정 교훈을 실제 **차단** = 판별기 작동 실증.
- **그러나 net 기여 미측정:** valid_apply=0(실제 cutoff 조정 0), param tuner 447세션/6진입/조정→진입 N=1(손실). **"작동" YES, "net 기여" 미입증/무효과 수렴.** PRO 자백, CON "효능 0≠유해" 자백.

### 명제2 — enforce 무장 안전? → **반대 (현재 무해, 잠복 위험)**
- 현재는 무해(valid_apply=0 → 적용 0). PRO 정당.
- **그러나:** docstring("기본 OFF") vs 운영(enforce) 불일치 + **4.14x dedup 오염 입력**(194 flip로 tr/wo 양쪽 동시기여) 위에 enforce 무장. 표본 게이트(n_wo·confidence)가 중복으로 부풀려져 **valid_apply=0은 보호가 아니라 운**(한 셀이 오염 표본으로 게이트 넘으면 entry_priority_cutoff를 자동 조정 — 그 신호는 직전 분석서 net 역상관). CON 강함.
- **판정: 검증 전 enforce는 잠복 위험. shadow로 내리고 dedup 먼저.**

### 명제3 — brain.json 가치? → **기각 (현재 비활성)**
- **현 fresh_v2 정책이 brain 라이브 주입을 차단**(judge·selection 프롬프트에서 skip). PRO 자진 인정.
- 두 번 청소된 오염 이력 + historical_sim 주입(오늘 #4 가드). 소표본(issue_pattern count 1~2).
- **판정: brain은 현재 능동 기여 0, 전망적 적재일 뿐. "교훈 메모리가 작동 중"이라 오인 금지.**

---

## 3. 합의 / 불일치

**합의:** 설계 골격(forward-only·격리·invalid_block)은 깨끗. 효능은 미측정(valid_apply=0, 6진입). brain은 현재 비활성. 입력 dedup 오염 실재(4.14x·194 flip).

**불일치:** "무해(valid_apply=0)" vs "운(dedup가 게이트 부풀림 → 잠복 위험)" — CON이 데이터로 우세(dedup·docstring 불일치는 측정값).

**미검증:** 교훈/param이 net을 *악화*시켰나 — 조정→진입 N=1로 통계력 0. 유해 미입증(단 가치도 미입증).

---

## 4. 개선 방향 (debate 출력 — 방향, 코드는 승인 후)

| # | 조치 | 형태 | 양측 |
|---|---|---|---|
| 1 | **LESSON_VALIDATION_APPLY_MODE enforce→shadow 강등** | config(두 소스) | 검증 전 무장해제, would_apply 로깅으로 효능 관측 유지. 현재 valid_apply=0이라 라이브 영향 0. **CON 권고·PRO 무반대** |
| 2 | **lesson_scoring dedup**((market,date,ticker)당 1행 + 194 flip 해소) | 코드 | 표본게이트 신뢰 회복 전 valid 승격 신뢰 불가. 감사 지적 |
| 3 | **docstring/운영값 동기화** | 문서 | enforce인데 "기본 OFF" 표기 = 감사 리스크 |
| 4 | **brain 현재 비활성 명시** + 두 lesson DB 혼선 제거(스테일 PoC=#3 deprecate 됨) | 문서/위생 | "교훈 메모리 작동 중" 오인 금지. historical_sim 가드=#4 |

**보존:** forward-only 채점·invalid_block 차단·cost_floor 게이트(무손실 안전장치). 닫지 마라.

---

## 5. 한 줄 결론
교훈 시스템은 **설계는 깨끗(forward-only·격리·함정차단)하나 효능은 미측정**(valid_apply=0·447세션/6진입)이고, **운영은 위험**(docstring=OFF인데 enforce 무장 + 4.14x dedup 오염 입력 → valid_apply=0은 보호 아닌 운)이며, **brain.json은 현 fresh_v2 정책에서 비활성**(능동 기여 0). → **enforce를 shadow로 내리고 dedup부터 고친 뒤, 효능이 측정되면 다시 켜라.** 설계 골격은 보존, brain은 "작동 중"이라 오인 금지.
