# 멀티에이전트 토론 준비 — A~F 영역 (다음 세션 즉시 시작용)

작성: 2026-06-21 | 선행: `plans/the-cloud-ultraplan-session-glimmering-tide.md`(어제 생존·수익극대화 토론), 메모리 `project_survival_debate_20260621`

운영자 지시(2026-06-21): "A~F를 어제처럼 3자 토론(Claude 빌더 + codex + 악마의 변호인)으로, **데이터를 직접 까서** 개선점 잡자. 과거 탈락한 것도 '이렇게 데이터를 보진 않았으니' 재검토. 권한 위임. 진행은 다음 세션."

---

## 토론 방식 (어제와 동일 — 검증된 포맷)

1. **참가자:** codex(`codex exec --sandbox read-only`, config 기본 gpt-5.5 xhigh — `-c model` 덮어쓰지 말 것) + Claude 빌더(`Agent` tool, `model: opus`, read-only 지시) + 나(사회/심판/악마의 변호인).
2. **단계별:** ①blind 독립 제안(서로 안 보고) → ②내가 decisions.db로 1차 데이터 심판 → ③상대 안 보여주고 적대 교차검증(가장 약한 고리 공격 + 자기 가정 자백) → ④kill 기준 달고 수렴.
3. **실시간 중계:** 서브에이전트/codex는 결과만 옴(글자단위 실시간 불가). 라운드별 발언 전문 중계 + 로그 누적.
4. **운영 노하우:**
   - codex 출력이 큼(쿼리 로그 포함) → `persisted-output` 파일을 `Get-Content -Tail 120~250`로 **최종 답변만 추출**.
   - codex 한글이 PowerShell 콘솔에서 mojibake로 보여도 내용은 읽힘 — 의미 복원해서 중계.
   - Claude 서브에이전트가 **API 529(Overloaded)**로 불발될 수 있음 → 재시도(일시적).
   - DB 경로: `data/ml/decisions.db` (테이블: decisions, v2_learning_performance, v2_canonical_performance, mfe_backfill_yf), `data/ticker_selection_log.db`.

## 공통 제약 / 규율 (어제 확정 — 모든 영역 적용)

- **근거 룰:** 데이터 주장은 출처(쿼리/파일/URL) 동반. 못 대면 `[추측]`/`[가설,검증필요]` 라벨. 외부는 `[외부,미검증]`.
- **이미 robust하게 결론난 것(재토론 금지):** selection 무엣지(약한 위생우위만)·새 종목 엣지 없음·흑자 레버 없음·cluster halt threshold=4 유지.
- **이미 죽은 답(재발명 금지):** selection 게이트(timing/extension), scale-in, profit_ladder floor 상향, weak_mfe_cut 자동절단, loss_cap 자동절단(+53%p 사후지).
- **라이브 안 건드림.** read-only 측정 → 토론 → 운영자 확정 후에만 적용. KR 포기불가/비용·우대 외생상수.
- **순서 원칙:** 측정 도구 먼저(숫자 확보) → 그 숫자로 토론. 데이터 없이 토론부터 하면 모래성.

---

## 영역별 아젠다 (우선순위순)

### 🔴 A. 덜어내기 / 검증 안 된 enforce 토글 사후검증 — **최우선**
- **왜:** 어제 "새 엣지 없음" 확인 → 다음 행동은 *추가*가 아니라 *검증 안 된 것 정리*(CLAUDE.md "덜어내는 처방 우선"). MD 위반 사항으로 토글이 쌓였는데 라이브 미검증 다수.
- **토론 질문:** "현재 enforce로 켜졌으나 라이브 미검증인 토글 중, 효과 없어/악화시켜 꺼야 할 것은? 서로 간섭하는 게이트는?"
- **검증 데이터:** 각 토글 적용 후 관련 close_reason/net 변화. 토글 목록과 검증상태:
  - `HOLD_ADVISOR_PROFIT_GUARD_ENABLED=true` (A/B 50건만, 라이브 미검증) → profit_pullback giveback 줄었나, 좋은 러너 오절단?
  - `KR_MOMENTUM_EARLY_ENTRY_ENABLED=true` (**KR 손실경로**, "악화 시 롤백" 명시) → KR momentum 조기진입 net
  - `US_PATHB_PREOPEN_PROFIT_TARGET_DEFER=enforce` (약세장 미검증)
  - C2/C3 과열 페널티(6/20 enforce, 크기 미검증 — ACTIVE_WORK B)
  - `LESSON_VALIDATION enforce` (valid_apply 0 = no-op) → 며칠 더 0이면 "교훈 아니라 selection 문제"
- **데이터 가용:** 일부 지금 가능, 일부 다음 거래일 후. 선 측정 후 토론.

### 🟢 B. 사이징 / 포트폴리오 — **2순위 (새 엣지 없음 우회 레버)**
- **왜:** 메모리 `profitability_performance`: "vol-target sizing은 종목 엣지 0이어도 위험조정 수익(Sharpe 0.4→0.5) 올림". 빌더(어제): "사이징 알파는 종목엣지와 독립". 어제 fragility 외 사이징 미검토.
- **토론 질문:** "종목 엣지 0에서 사이징(변동성 역비례/vol-target)으로 net 또는 위험조정을 올릴 수 있나? 포지션 집중도(상관 높은 메가캡 동시 carry)는 위험인가?"
- **검증 데이터:** 종목별 변동성(ATR/vol_ratio) ↔ net 상관, 동시보유 종목 상관관계, 현 고정금액(`PATHB_FIXED_ORDER_KRW=500000`)·`max_positions=15`·`daily=40` 적정성. 6/3형 메가캡 7종 carry 집중 사례.
- **데이터 가용:** 지금 가능.

### 🟡 C. 청산 정밀화 (target extension / trailing) — 과거 탈락분 데이터 재검토
- **왜:** 어제는 "loss_cap 막기"만 봄. "이긴 걸 더 보유(target extension)"는 2026-06-14 시뮬에서 탈락했으나 **운영자 지시: 그때 이런 데이터로 본 게 아니다 → 재검토**.
- **토론 질문:** "러너를 목표 넘겨 더 보유 vs 현행, capture가 개선되나? (어제 capture 56~89%, MFE 데이터). ATR trailing+분할익절이 profit_ladder보다 나은가?"
- **검증 데이터:** `mfe_backfill_yf`로 청산 후 추가 상승분(놓친 capture), 청산경로별 capture(pnl/mfe). **주의:** profit_ladder는 보호영역(floor 상향 롤백 이력) — shadow 선행 필수.
- **데이터 가용:** 지금 가능. 단 과거 탈락 데이터(2026-06-14) 먼저 확인.

### 🟡 D. 입력 품질 features (PEAD / 후보 풀 소스) — 어제 안 봄
- **왜:** selection을 "위생"으로 결론냈지만 *후보 풀 소스/이벤트 신호*는 진단 안 함. PEAD surprise는 CLAUDE.md에 정책 다 있는데 5거래일 검증이 멈춤(`state/pead_shadow_state.json`).
- **토론 질문:** "PEAD earnings 신호가 위생 이상의 가치(trade_ready 편향)가 있나? 후보 풀 소스(스크리너/sub_screener) 확장이 net에 기여?"
- **검증 데이터:** `logs/pead/*.json` shadow 기록, `data/ticker_selection_log.db` 후보별 forward. **PEAD 정책 제약 엄수**(CLAUDE.md PEAD 섹션: shadow 게이트, brain.json 격리).
- **데이터 가용:** PEAD shadow 데이터 있음.

### 🟡 E. 데이터 품질 (KR split 오염 / sync freshness) — 어제 측정배선만
- **왜:** KR 절대수치를 못 믿는 근본 원인 = yfinance split 오염(메모리 반복 등장). decisions.db ↔ v2_event_store sync freshness, attribution 누락(Roadmap "현재").
- **토론 질문:** "KR 데이터 신뢰를 어떻게 복구하나(split 보정 소스)? sync/attribution 누락이 측정을 얼마나 왜곡하나?"
- **검증 데이터:** KR forward 극단값(|>30%|) 분포, split 의심 케이스, sync 시각차.
- **데이터 가용:** 지금 가능.

### ⚪ F. 메타 (Claude 비용·토큰 / regime 조건부) — 낮은 우선
- **왜:** 호출 비용 효율(smart skip, intraday review cooldown) 미검토. regime 조건부 운영은 측정(regime 0) 선결이라 막힘.
- **토론 질문:** "Claude 호출/토큰을 줄이면서 품질 유지할 여지? (regime 조건부는 측정 복구 후)"
- **데이터 가용:** 호출 로그 있음. regime 조건부는 선결 막힘.

---

## 진행 순서 권고 (다음 세션)

1. **A부터** (덜어내기 = 어제 토론의 빈 자리, net 직접 도움). 단 일부 토글은 다음 거래일 데이터 필요 → 지금 측정 가능한 것부터.
2. **그다음 B** (vol-target 사이징 = 새 엣지 없음 우회하는 유일 레버).
3. C~F는 A·B 결과 보고 운영자가 취사선택. **전부 다 하지 말 것** — 어제 교훈: 토론은 데이터 있고 답 안 갈린 주제에만.
4. 각 영역: **측정 도구/쿼리 먼저(숫자) → 3자 토론 → kill기준 → 운영자 확정.** 어제 cluster halt 흐름 그대로.

**시작 멘트 예시(다음 세션):** "이 문서(A~F) 기준으로 A부터 3자 토론 시작. 먼저 토글 사후검증 데이터를 codex+빌더에게 독립으로 까게 한다."
