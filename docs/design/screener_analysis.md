# 스크리너 상세 분석

작성일: 2026-06-03  
분석 대상: `runtime/sub_screener.py`, `runtime/candidate_prompt_pool.py`, `trading_bot.py`

---

## 1. 전체 구조

```
Raw Market Data
    ↓
screen_market_kr/us(top_n=80)       [kis_api.py:3990, 5210]
    ↓
_screen_quality_guard()              [trading_bot.py:2706]
    ↓
build_trainer_prompt_pool()          [candidate_prompt_pool.py:225]
    ├─ score_candidate_for_trainer()
    ├─ 상태 분류: PLAN_A / PLAN_B / WATCH / BENCH / QUARANTINE
    ├─ 정렬: STATE_RANK → SCORE → RISK → RAW_RANK
    └─ hard_cap 적용 (KR=32, US=35)
    ↓
select_tickers(35개)                 [analysts.py:2100]
    ↓
sub_screener 병렬 동작
    ├─ scan_new_candidates()         [sub_screener.py:202]
    │   ├─ PLAN_A 신규 감지
    │   └─ PLAN_B≥65 신규 감지
    └─ should_trigger → manual_rescreen() → full select_tickers
```

---

## 2. 핵심 파라미터 (실측 데이터 포함)

| 파라미터 | 기본값 | 환경변수 | 파일:라인 |
|---|---|---|---|
| raw 스크린 크기 | 80개 | `KR/US_SCREEN_TOP_N` | kis_api.py:3990 |
| hard_cap (KR) | 32개 | `KR_SELECTION_PROMPT_CAP` | candidate_prompt_pool.py:235 |
| hard_cap (US) | 35개 | `US_SELECTION_PROMPT_CAP` | candidate_prompt_pool.py:235 |
| target | 30개 | `CANDIDATE_PROMPT_POOL_TARGET_*` | candidate_prompt_pool.py:229 |
| sub_screener 간격 | 15분 | `SUB_SCREENER_INTERVAL_MIN` | trading_bot.py:26092 |
| sub_screener 세션 max | 5회 | `SUB_SCREENER_MAX_PER_SESSION` | trading_bot.py:26108 |
| PLAN_B 최소 점수 | 65.0 | `SUB_SCREENER_PLAN_B_MIN_SCORE` | sub_screener.py |
| 장마감 블랙아웃 | 30분 | `SUB_SCREENER_BLACKOUT_BEFORE_CLOSE_MIN` | trading_bot.py:26098 |

---

## 3. 실측 데이터

### 후보 상태 분포 (screener_seen=1, 2026-05-20~)
```
KR: PLAN_A=26%, PLAN_B=60%, QUARANTINE=0%  → 86%가 우량 후보
US: PLAN_A=14%, PLAN_B=65%, QUARANTINE=0%  → 79%가 우량 후보
```

### TR 변화율 (Smart Skip 설계 근거)
```
KR: 82% 동일, 18% 변화
US: 58% 동일, 42% 변화
→ KR은 82% skip 가능, US는 58% skip 가능
```

### 재등판 패턴 (강등→재TR)
```
재등판 케이스: 0건 (2026-05-01~ 측정)
→ 현재 시스템에서 TR이 되면 세션 내 강등이 사실상 없음
→ Downgrade gate가 새로 만들어지는 기능임을 확인
```

---

## 4. 항목별 상세 분석

### 4.1 Raw 스크린 단계 (top_n=80)

**긍정적:**
- 80개를 스크린해서 상위 35개만 Claude로 전달 → 45개 버퍼 존재
- top_n 확장이 환경변수 1개로 가능 (`KR/US_SCREEN_TOP_N`)
- KR/US 각각 독립 스크린 → 시장별 특성 반영
- 5분 캐시 (US) → 불필요한 API 호출 방지

**부정적:**
- top_n=80이 상한 → 더 넓은 universe 탐색 불가 (코드 수준 변경 필요)
- US는 YahooFinance + AlphaVantage 의존 → 실시간성 제한
- KR은 KIS API 의존 → 브로커 상태에 영향받음
- screener_seen 평균 38개 (max 110개) → 80개 요청해도 실제 수령은 더 적음

### 4.2 build_trainer_prompt_pool (점수 계산 + 분류)

**긍정적:**
- PLAN_A/B/WATCH/BENCH/QUARANTINE 5단계 분류 → 품질 계층화
- STATE_RANK → SCORE → RISK 복합 정렬 → 품질 순서 보장
- hard_cap으로 Claude 입력 크기 제어 → 비용 예측 가능
- QUARANTINE=0% → 현재 유해 후보 유입 없음

**부정적:**
- hard_cap 35로 고정 → 환경변수 없이 변경 불가 (운영자 설정 필요)
- QUARANTINE=0%는 장점이기도 하지만 → 필터가 실제로 동작하는지 불확실
- Tier 0 코드 필터 효과: IGNORE 중 3.7%만 제거 가능 (liq=high IGNORE가 대다수)
- trainer_candidate_state가 v2_start_config와 연동 → 파라미터 변경 시 상태 분류 일관성 위험

### 4.3 sub_screener (신규 감지)

**긍정적:**
- PLAN_A/B 신규 감지는 이미 delta 감지 형태 → Smart Skip 설계와 방향 일치
- rate limit으로 과호출 방지 (15분 간격, 세션 5회)
- 상태 파일(`state/sub_screener_{MKT}_{DATE}.json`) 로 추적 가능
- 장마감 블랙아웃 30분 → 불필요한 장마감 직전 호출 차단

**부정적:**
- **핵심 비효율**: 신규 감지 후 `manual_rescreen()` → **전체 80개 full select_tickers** 호출
  → Smart Skip 설계에서 이 경로가 반드시 개선되어야 함
- max_per_session=5 → 장중 기회를 최대 5번밖에 감지 못함
- PLAN_B min_score=65 → 점수 64.9인 후보 누락 (임계값 경직)
- 신규 감지 기준이 PLAN_A/B score만 → 장중 컨텍스트 변화(지수, 뉴스) 미반영
- sub_screener와 main pool이 별도 `build_trainer_prompt_pool` 실행 → 미묘한 불일치 가능

### 4.4 select_tickers 연결 (prompt_pool_override)

**긍정적:**
- `prompt_pool_override` 파라미터로 sub_screener 결과를 덮어쓰기 가능
- 기존 계약 (`prompt_pool`, `evidence_by_ticker` 등) 유지
- `actual_prompt_count` 추적 → audit 가능

**부정적:**
- sub_screener 트리거 시 `candidate_override=screener_rows` (원본 80개 전달) → hard_cap에서 다시 35개로 잘림
  → 신규 후보가 rank36 이하면 Claude가 여전히 못 봄
- prompt_pool_override 검증 로직 (`unknown_override_tickers`) → 복잡한 일치 확인 필요
- hard_cap_cutoff 이후 excluded 목록이 audit에 기록되지만 수익 분석에 활용 안 됨

### 4.5 후보 확장 경로

**긍정적:**
- 환경변수 3개로 35→50개 확장 즉시 가능 (코드 수정 없음):
  ```
  US_SELECTION_PROMPT_CAP=50
  KR_SELECTION_PROMPT_CAP=40
  US_SCREEN_TOP_N=100
  ```
- `_trainer_prompt_hard_cap()` 함수가 env override를 안전하게 처리

**부정적:**
- rank36-60 BR 비율 0.03% → 단순 cap 확장은 실익 없음
- 스크리너 소스(데이터)가 momentum/breakout 중심 → 다양성 부족
- KR KOSDAQ 최소 35% 비율 강제 → 확장 시 KOSPI 편향 방지 추가 고려 필요
- US screener가 Yahoo 데이터 의존 → 실시간 정보 지연 발생 가능

---

## 5. 스크리너 전체 평가

### 긍정 종합
```
✅ PLAN_A/B 품질 분류가 잘 동작 (KR 86%, US 79% 우량)
✅ QUARANTINE=0% → 유해 후보 차단 정상 작동
✅ sub_screener의 신규 감지 방향이 Smart Skip과 일치
✅ 환경변수로 즉시 확장 가능 (코드 수정 불필요)
✅ rate limit이 과호출을 합리적으로 제어
```

### 부정 종합
```
❌ sub_screener 신규 감지 후 full rescreen → 가장 큰 비효율 (Smart Skip 개선 필요)
❌ hard_cap=35 고정 → 단순 수량 제한으로 rank21-35의 일부 기회 놓침
❌ rank36+ BR=0% → screener 소스 다양화 없이 cap 확장은 의미 없음
❌ PLAN_B min_score=65 임계값 경직 → borderline 후보 누락
❌ 신규 감지 기준에 시장 컨텍스트(지수, 뉴스) 미반영
❌ KR/US 스크리너 소스 모두 momentum/breakout 중심 → 다양성 한계
```

---

## 6. Smart Skip 설계와 연결점

### 즉시 연결 가능
```python
# trading_bot.py의 run_cycle 내 select_tickers 호출 전 삽입
def run_cycle(market):
    if smart_skip.should_skip(market):
        return  # Claude 호출 없음

    select_tickers(...)  # 기존과 동일
```

### sub_screener 개선 (Phase 1)
```python
# 현재
scan_new_candidates() → should_trigger → manual_rescreen(전체 80개)

# 개선
scan_new_candidates() → should_trigger → Smart Skip FORCE_CALL 플래그 설정
                                       → run_cycle에서 강제 호출
```

### 후보 확장 (Phase 3, 환경변수만)
```bash
US_SELECTION_PROMPT_CAP=50
KR_SELECTION_PROMPT_CAP=40
US_SCREEN_TOP_N=100
```

---

## 7. 스크리너 개선 로드맵

| 단계 | 개선 | 효과 | 복잡도 |
|---|---|---|---|
| 즉시 | Smart Skip 삽입 (`run_cycle` 전) | KR 82%, US 54% 호출 감소 | 낮음 |
| 즉시 | sub_screener→manual_rescreen 개선 | 불필요한 full rescreen 제거 | 낮음 |
| Phase 3 | cap 35→45-50 (환경변수) | 후보 커버리지 확장 | 없음 |
| 장기 | screener 소스 다양화 | raw 600-900개 → BR 기회 증가 | 높음 |
| 장기 | intraday 신호 추가 (뉴스, 지수 rotation) | 감지 품질 향상 | 높음 |
