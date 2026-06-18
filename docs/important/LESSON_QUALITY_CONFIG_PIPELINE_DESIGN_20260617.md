# 교훈 품질평가 → 검증DB → 국면조건부 config 파이프라인 설계

작성일: 2026-06-17
출처: 2026-06-17 진입변별/교훈검증 세션 (memory `project-entry-discrimination-20260616` §13~17)
상태: **설계 (DESIGN). 라이브 미적용. 구현은 운영자 승인 후 Phase별.**

---

## 0. 배경과 전제 (오늘 측정으로 확정된 것)

1. **현 lesson 시스템은 forward 검증이 0이다.** `minority_report/lesson_quality._evidence_score`·`active_lessons._score_item` 둘 다 severity/confidence/sample/recency(빈도)로만 채점. "그 교훈대로 했으면 실제 좋아졌나"를 보지 않음 → 함정 교훈(watch_only 완화, forward −5.90%)이 자주 울려서 고점수.
2. **교훈 평가는 손실률이 아니라 반사실(counterfactual)이어야 한다.** 교훈은 실제 반영된 적 없으니 realized 성적은 "교훈 없는 시스템"의 성적일 뿐. 평가축 = "적용했더라면 forward가 개선됐나"(gain = would-be − actual).
3. **교훈 validity는 국면×시장 의존이고 sign이 뒤집힌다.** watch_only 승격 교훈: US/STRONG_UP valid(+2.04)·US/UP invalid(−3.08)·KR/STRONG_UP invalid(−5.29)·KR/UP valid(+2.50). → 검증DB는 **국면×시장 인덱싱 필수**, 적용은 **국면 매칭 시에만**.
4. **천장 인지(낙관 금지):** 오늘 측정상 정적 알파 신호 없음. 이 파이프라인의 목표는 **"국면 미스매치 손실 축소"**지 alpha 창조가 아니다. 기대치를 거기 맞춘다.

### 기존 touchpoint (재사용)
- 교훈 생성: `minority_report/postmortem.py::run()` (장 종료 Claude 사후분석) + metric 재발 교훈(`lesson_recurrence.json`).
- 교훈 후보/스코프: `state/lesson_candidates.json`, `minority_report/active_lessons.py`, `lesson_quality.py`.
- **런타임 국면 판단**: `runtime/adaptive_live_condition.py::infer_market_regime()`.
- **bounded 튜닝 적용점**: `minority_report/tuner.py` (`momentum_wait_adjust_min` ±10, `entry_priority_cutoff_adjust` ±0.05, `kr_momentum_atr_cap_adjust` 등 — CLAUDE.md "Claude post-tuning" 범위).
- 시드 데이터: `data/analysis/entry_discrimination.db::lesson_validation_regime` (오늘 생성, watch_only 1축).

---

## 1. 컴포넌트 ① — 기존 교훈 품질평가 + 검증DB 구축

**목표:** 기존 재발 교훈 전부를 반사실로 채점해 **국면×시장별 품질**을 매기고, 고품질만 검증DB에 넣는다.

### 1.1 교훈별 반사실 정의 (각 교훈은 "적용=무엇" + "forward축"이 다름)
| 교훈 | 적용의 의미 | 반사실 gain |
|---|---|---|
| watch_only_missed_runup | watch_only 더 승격 | wo_fwd3d − tr_fwd3d (시드 완료) |
| trade_ready_conversion | trade_ready 임계 완화 | 완화선 근처 후보 fwd − 현 trade_ready fwd |
| unanimous_override | 만장일치 신뢰 | 만장일치-override 케이스 outcome (구조가드라 튜닝축 약함 — 관찰만 후보) |
| (narrative postmortem) | 케이스별 | 교훈이 지목한 행동의 forward 대조 (반자동, 일부 수동) |

### 1.2 품질 게이트 (고품질 정의)
검증DB 삽입 조건 (AND):
- `n_wo >= 30 AND n_tr >= 10` (셀 표본 floor, 노이즈 차단)
- `|gain| >= 1.0%p` 이고 부호 명확 (valid 또는 invalid 둘 다 "고품질 지식" — invalid는 "여기선 적용금지" 지식)
- `sessions_confirmed >= 2` (같은 국면셀에서 ≥2 독립 구간 같은 부호 — 운영자 "비슷한 게 나오고 성과로 이어지면 맞는 것")

### 1.3 스키마 (`validated_lesson`, 국면 인덱싱)
```
validated_lesson(
  lesson_key, market, regime,         -- PK
  counterfactual_gain REAL,           -- + 적용가치 / − 함정
  n_sample INT, confidence REAL,      -- confidence = f(n, |gain|, sessions_confirmed)
  sessions_confirmed INT, last_seen,
  verdict TEXT,                       -- valid_apply / invalid_block / neutral / pending
  mapped_control TEXT,                -- 컴포넌트③ 매핑 (예: entry_priority_cutoff_adjust)
  updated_at
)
```
시드 = 오늘 `lesson_validation_regime`(watch_only 1축) → 이 스키마로 이관·확장.

---

## 2. 컴포넌트 ② — 신규 장종료 교훈 온라인 품질평가

**문제:** 장종료 시점엔 그 교훈을 검증 불가 (forward 3~5일 미성숙).

**해결: 지연 검증 상태기계.**
```
NEW(장종료 생성) → PENDING_VALIDATION (forward 미성숙, 격리 staging)
   → [3~5 거래일 후 forward 성숙] → 반사실 gain 채점
       → 품질게이트 통과: validated_lesson 삽입(verdict=valid_apply / invalid_block)
       → 미달: REJECTED (또는 sessions_confirmed 부족 시 PENDING 유지)
```
- 신규 교훈은 **절대 즉시 config 반영 안 됨** (미검증). staging에만.
- 채점 잡: 장종료 후 N거래일 배치(`tools/lesson_forward_validation.py` 확장) — pending 교훈을 성숙분으로 일괄 채점.
- 국면 라벨은 `session_regime`(VIX/지수 5일추세, 향후 breadth 추가)로 부여.

---

## 3. 컴포넌트 ③ — 검증DB → config 매핑 (구성+적용)

**핵심: lesson → bounded control 매핑 + 국면조건부.**

### 3.1 매핑표 (lesson → CLAUDE.md 범위 내 컨트롤)
| validated lesson | mapped_control | 방향 |
|---|---|---|
| watch_only_missed (valid_apply) | `entry_priority_cutoff_adjust` (−, 완화) / slot bias | 해당 국면셀에서만 trade_ready 소폭 완화 |
| watch_only_missed (invalid_block) | (적용 안 함 — 명시적 차단 지식) | 그 국면셀에선 완화 금지 |
| trade_ready_conversion (valid) | `entry_priority_cutoff_adjust` | 동일 |
| momentum 계열 | `momentum_wait_adjust_min` | 진입 대기 ± |
| KR ATR 계열 | `kr_momentum_atr_cap_adjust` | KR 한정 |

### 3.2 적용 규칙
- **국면조건부:** 런타임 `infer_market_regime()` 결과 == validated_lesson.regime 일 때만 그 셀의 adjustment 활성.
- **크기:** `adjustment = clamp(base × gain_factor × confidence, control_bound)`. gain·confidence 클수록 크되 **항상 CLAUDE.md 범위 내**(±10분/±0.05 등). hard safety·만장일치 가드 무수정.
- **invalid_block 셀:** 그 국면에서 해당 완화/조정 **금지**(다른 교훈이 같은 컨트롤 밀어도 차단). = 함정 방어.

---

## 4. 컴포넌트 ④ — 신규 교훈 추가 시 구성(조합)

여러 validated lesson이 같은 컨트롤/국면을 가리킬 때:
- **충돌 해소: gain×confidence 가중합** 후 bound clamp. (현 `apply_lesson_conflict_guards`의 "더 시끄러운 쪽" → "gain 가중"으로 교체.)
- **invalid_block 우선:** 같은 셀에 valid_apply와 invalid_block 충돌 시 **block 우선**(보수적).
- **누적/재확인:** 같은 셀 교훈 재발 + forward 재확인 → `sessions_confirmed++`, confidence↑. (운영자 "조합이 키".)
- **감쇠(decay):** `last_seen` N거래일 초과 미재확인 셀은 confidence 감쇠 → 0 되면 비활성 (국면 변질 대응).

조합 산출물 = **(market, regime) → {control: adjustment}** 룩업 테이블 (= "교훈DB가 config로 변환된 형태").

---

## 5. 컴포넌트 ⑤ — 조합 → 라이브 반영

### 5.1 적용 경로
```
장중 사이클 → infer_market_regime() (현재 국면)
  → validated_lesson 룩업 (market, regime) → adjustment 테이블
  → tuner.py 의 bounded control에 주입 (기존 Claude post-tuning 경로 재사용)
  → selection cutoff / momentum wait / slot bias 에 반영
```
별도 신규 실행경로 없음 — **기존 bounded 튜닝 슬롯에 "국면조건부 검증교훈" 소스를 추가**하는 것.

### 5.2 안전 계약 (불변)
- CLAUDE.md "Claude post-tuning" 범위 절대 불변: hard safety 무수정, 만장일치 방향가드 무수정, 컨트롤 bound 무수정.
- `brain.json` 자동변이 금지 원칙 준수 → 이 파이프라인은 brain이 아니라 **별도 검증DB + bounded 튜닝 슬롯**으로만 흐름.
- 보호영역(PathB 수익경로·broker truth·loss_cap 등) 무접촉. lesson은 selection/timing의 bounded 컨트롤만 움직임.

### 5.3 롤아웃 (shadow → enforce, 바·기한 명시)
- **Phase S (shadow):** 적용 경로 전부 구성하되 adjustment를 **실주문에 반영 안 함**. "이 국면에서 이렇게 바꿨을 것" funnel 로깅만. 최소 2~3주.
- **enforce 전환 바:** ① out-of-sample 1사이클에서 shadow adjustment net + ② valid 셀이 ≥2 독립구간 재확인 ③ 손실streak 한도 내. 미달 시 **중단·롤백**(낙관 금지).
- **승인:** enforce 전환은 운영자 명시 승인 + per-control 토글(즉시 롤백 가능).

---

## 6. 가드레일 / 정량 기준 요약 (바와 기한)

| 항목 | 기준 |
|---|---|
| 셀 표본 floor | n_wo≥30, n_tr≥10 |
| 품질 임계 | |gain|≥1.0%p, 부호명확 |
| 재확인 | sessions_confirmed≥2 (독립구간) |
| 신규교훈 | 즉시반영 금지, PENDING→3~5일 forward→채점 |
| 적용 범위 | CLAUDE.md bounded control만, 국면조건부 |
| 롤아웃 | shadow ≥2~3주 → out-of-sample 통과 → 승인 → enforce |
| 실패 시 | 바 미달이면 중단/롤백, "더 측정"으로 무한연장 금지 |

---

## 7. 단계별 구현 로드맵

- **Phase 0 (선결, read-only):** out-of-sample 사이클 수집 — 오늘 in-sample 결과(US/STRONG_UP 등)가 다음 구간에도 같은 부호인지. **이게 통과 못 하면 전체 보류.**
- **Phase 1 (컴포넌트①):** `lesson_validation_regime`을 `validated_lesson` 스키마로 이관 + 교훈 2축(trade_ready_conversion) 추가 채점. read-only.
- **Phase 2 (컴포넌트②):** 신규 교훈 PENDING→지연검증 상태기계 + 채점 배치잡. read-only(staging).
- **Phase 3 (컴포넌트③④):** lesson→control 매핑 + 조합 룩업테이블 산출. 아직 미적용(테이블만).
- **Phase 4 (컴포넌트⑤ shadow):** tuner.py에 국면조건부 검증교훈 소스 추가, **shadow 로깅만**.
- **Phase 5 (enforce):** Phase 4 바 충족 + 운영자 승인 후 per-control enforce.

각 Phase는 검증 후 다음 진행. Phase 0 미통과 시 파이프라인 자체 보류(오늘 측정이 in-sample 한 사이클뿐임을 정직히 반영).

---

## 8. 미해결 / 리스크 (정직)
- 오늘 검증은 in-sample 2개월·국면프록시 1개·검증축 1개·일부 셀 소표본(KR/UP tr12). **Phase 0 out-of-sample이 1차 관문.**
- 반사실 gain은 wo_fwd3d를 "승격 시 실현"의 프록시로 씀(진입/청산 타이밍 차이 무시) — 근사.
- 국면 라벨 = 전일 지수 5일추세. breadth/VIX 다축 교차검증 필요.
- 이 시스템의 천장은 "국면 미스매치 손실 축소"로 bounded. 정적 알파 창조 아님 — 기대치 관리.
