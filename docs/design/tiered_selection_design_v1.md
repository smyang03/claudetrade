# 2-Tier + Fast-Track 선택 아키텍처 설계서 v1.0

작성일: 2026-06-03  
상태: 설계 완료 (구현 전 검토 필요)

---

## 1. 목표

| 항목 | 현재 | 목표 |
|---|---|---|
| 일일 비용 (US+KR) | ~$1.38 | **~$0.75 (45%↓)** |
| 후보 커버리지 | 35개 | **60~80개** |
| 평균 감지 지연 | ~6분 | **~10분** |
| BUY_READY 커버리지 | 100% | **~100%** |
| 운영 방식 변경 | — | **PathB/매수 로직 불변** |

---

## 2. 아키텍처 전체 구조

```
┌─────────────────────────────────────────────────────────────┐
│  스크리너 (코드, 무료)                                         │
│  5분마다 실행 → 60~80개 후보 풀 유지                           │
└──────────────┬──────────────────────────────────────────────┘
               │
       ┌───────▼────────┐
       │  Layer 1        │  5분마다
       │  신규 감지       │  Haiku · ~300tok · ~$0.002/회
       │  (신규만 1차)    │  → 신규 SHORTLIST → Layer 3 트리거
       └───────┬─────────┘
               │
       ┌───────▼────────┐
       │  Layer 2        │  10분마다
       │  Fast-Track     │  Haiku · ~800tok · ~$0.001/회
       │  WATCH+상위IGN  │  → PROMOTE / KEEP / EXPIRE
       └───────┬─────────┘
               │
       ┌───────▼────────┐
       │  Layer 3        │  30분마다 (+ 신규 트리거)
       │  Full Refresh   │  1차(Haiku) + 2차(Sonnet)
       │  60개 전체 판단  │  → Snapshot 갱신
       └───────┬─────────┘
               │
       ┌───────▼────────┐
       │  Selection      │
       │  Snapshot       │  state/selection_snapshot_{MKT}_{DATE}.json
       │  TRADE_READY    │  TTL + 버전 관리
       │  WATCH          │
       │  SOFT_IGNORE    │
       │  HARD_IGNORE    │
       └───────┬─────────┘
               │ 기존과 동일
       ┌───────▼────────┐
       │  PathB / 주문   │  변경 없음
       │  Hold Advisor   │  변경 없음
       │  Risk Manager   │  변경 없음
       └────────────────┘
```

---

## 3. Layer별 상세 설계

### Layer 1: 신규 감지 (5분마다)

**목적**: 스크리너가 새로 감지한 후보를 즉시 처리  
**모델**: Haiku  
**트리거**: 스크리너 5분 주기 실행 후 신규 후보 존재 시

```python
# 신규 = 현재 snapshot에 없는 ticker
new_candidates = screener_results - snapshot.all_tickers

if not new_candidates:
    return  # Claude 호출 없음

# 1차 필터 (V5 프롬프트)
shortlist = tier1_filter(new_candidates, max=15)

if shortlist:
    # 신규 후보만 2차 판단 (기존 snapshot context 포함)
    new_judgments = tier2_select(shortlist, snapshot_summary)
    snapshot.merge(new_judgments)
```

**비용**: ~$0.002/회 × 20회/일 = $0.04/일  
**지연**: ~5분

---

### Layer 2: Fast-Track (10분마다)

**목적**: WATCH/상위IGNORE 승격·만료 빠르게 처리  
**모델**: Haiku  
**대상**: 현재 WATCH 전체 + rank01-20 SOFT_IGNORE (liq=high)

```
[Fast-Track 승격 체크] US 장중
목적: WATCH/IGNORE 후보가 지금 BUY_READY 조건 충족하는지 판단.

현재 시장: {market_ctx}

후보:
{ticker} {chg} liq={liq} bucket={bucket} s={score} [WATCH/SOFT_IGNORE]
...

PROMOTE: 지금 BUY_READY 조건 충족
KEEP:    조건 미충족, 계속 관찰
EXPIRE:  조건 악화 또는 기회 소멸

JSON: {"results":[{"ticker":"X","verdict":"PROMOTE/KEEP/EXPIRE","reason":"한줄"}]}
```

**검증 결과**:
- PROMOTE 정확도: 76% (5/21 누락 → 30분 Full에서 커버)
- KEEP 정확도: 96% (false positive 4% — 잘못된 매수 방지)
- EXPIRE로 TRADE_READY 강등: 30분내 강등 14건/51건 커버

**비용**: ~$0.001/회 × 27회/일 = $0.027/일  
**지연**: ~10분 (WATCH→PROMOTE)

---

### Layer 3: Full Refresh (30분마다 + 신규 트리거)

**목적**: 전체 후보 재판단, IGNORE→BUY_READY 최종 포착  
**모델**: 1차 Haiku + 2차 Sonnet  
**데이터**: 60~80개 후보 풀 (screener_seen 확장)

```
[30분 주기]
1차(V5_two_pass, 60개, Haiku) → SHORTLIST ~50개
2차(select_tickers 기존 구조, 50개, Sonnet) → 전체 재판단
→ Snapshot 완전 갱신 (TRADE_READY / WATCH / SOFT_IGNORE / HARD_IGNORE)
```

**IGNORE 분류 기준 (2차 출력 추가)**:
```json
{
  "ignore_tier": "SOFT",   // 30분 후 재평가 대상
  "ignore_tier": "HARD"    // 재평가 안 함 (overextended, structural veto)
}
```

**비용**: ($0.002 + $0.033)/회 × 9회/일 = $0.315/일  
**커버리지**: IGNORE 후보 100% 풀 잔존 확인 → 2사이클(60분)내 포착

---

## 4. Selection Snapshot 설계

### 파일 경로
```
state/selection_snapshot_US_20260603.json
state/selection_snapshot_KR_20260603.json
```

### 구조
```json
{
  "market": "US",
  "session_date": "2026-06-03",
  "last_full_refresh_at": "2026-06-03T22:45:00",
  "last_fast_track_at": "2026-06-03T23:00:00",
  "version": 42,
  "trade_ready": [
    {
      "ticker": "NVDA",
      "added_at": "2026-06-03T22:45:00",
      "expires_at": "2026-06-03T25:45:00",
      "reason": "momentum breakout",
      "price_targets": {...},
      "layer": "full_refresh"
    }
  ],
  "watch": [
    {
      "ticker": "AMD",
      "added_at": "...",
      "rank_at_add": 8,
      "liq": "high"
    }
  ],
  "soft_ignore": [
    {
      "ticker": "TSLA",
      "added_at": "...",
      "eligible_after": "2026-06-03T23:15:00"
    }
  ],
  "hard_ignore": ["RLAY", "HSAI"]
}
```

### TTL 정책
| 상태 | TTL | 만료 후 |
|---|---|---|
| TRADE_READY | 3시간 | EXPIRE → WATCH로 강등 |
| WATCH | 세션 종료 | 자동 삭제 |
| SOFT_IGNORE | 30분 | Full Refresh 재평가 대상 |
| HARD_IGNORE | 세션 종료 | 자동 삭제 |

---

## 5. 갭 #1-9 해소 방안

### 갭1: TRADE_READY 만료/강등
**해소**: TTL 3시간 + Fast-Track EXPIRE 판정으로 조기 강등  
**코드**: `snapshot.trade_ready` 각 항목에 `expires_at` 필드, Layer 2에서 EXPIRE 처리

### 갭2: Fast-Track 미검증
**해소**: 6개 세션 테스트 완료  
- PROMOTE 정확도 76% (놓친 24%는 30분 Full에서 커버)
- KEEP 정확도 96% (false positive 4%)

### 갭3: 장중 급변 대응
**해소**: Full Refresh 강제 트리거 조건 추가
```python
FORCE_REFRESH_CONDITIONS = [
    lambda: index_change_since_last_refresh > 1.5%,
    lambda: vix_spike_detected(),
    lambda: market_mode_changed(),
    lambda: operator_manual_trigger()
]
```

### 갭4: Snapshot Race Condition
**해소**: 레이어 간 쓰기 잠금
```python
# snapshot 쓰기는 version 증가로 충돌 감지
# Layer 3(Full)이 진행 중이면 Layer 1/2 업데이트 대기
snapshot.acquire_write_lock(timeout=5s)
```

### 갭5: 봇 재시작 복구
**해소**: state/ JSON 파일 영속 저장 + TTL 검증
```python
def load_snapshot(market, date):
    path = f"state/selection_snapshot_{market}_{date}.json"
    if exists and not expired:
        return cached_snapshot
    else:
        trigger_immediate_full_refresh()
```
재시작 후 최대 30분 내 Full Refresh로 복구 (현재 13분 복구와 유사)

### 갭6: KR 별도 검증
**해소 계획**: KR 1차 품질 테스트 필요 (US와 동일 방법)  
**KR 특성 반영**:
- vol_ratio 정상 → Fast-Track 트리거에 volume spike 조건 추가
- 오전 9시~9시30분 집중 → 장 시작 10분 후 즉시 Full Refresh
- IGNORE→BR avg 133분 (US 84분보다 느림) → 30분 주기 동일 적용
- KR 전용 Full Refresh 추가: 10:00, 11:00, 13:00 (시장 중반 체크)

### 갭7: 60개 확장 후보 품질
**데이터**: rank36+ BUY_READY 비율 0% (현재 in_prompt 기준)  
**위험**: hard_cap_cutoff 후보의 실제 품질 미검증  
**해소**: 1차(V5)가 안전망 역할 — liq=high/bucket=pullback 외 저품질 후보 필터  
**추가 검증**: 60개 확장 후 2주 shadow 운영으로 rank36-60 BUY_READY 비율 측정

### 갭8: Audit 추적
**해소**: 각 Layer 호출을 audit_claude_calls에 기록
```python
# 기존 label 패턴 확장
label = "tier1_new_candidates"    # Layer 1
label = "fast_track_review"       # Layer 2
label = "full_refresh_tier1"      # Layer 3 1차
label = "full_refresh_tier2"      # Layer 3 2차 (기존 selection_meta_live)
```
snapshot 갱신 원인(layer, version)도 audit_candidate_rows에 기록

### 갭9: 점진적 롤아웃
**해소**: 3단계 배포
```
Phase 1 (shadow): 새 구조 병렬 실행, 기존 select_tickers 유지
  → 2주간 snapshot vs 기존 결과 비교
  → BUY_READY 일치율, 감지 지연 측정

Phase 2 (hybrid): Layer 3 Full Refresh만 새 구조로 전환
  → Layer 1/2는 기존 select_tickers가 보완
  → 비용 30% 절감, 리스크 낮음

Phase 3 (full): 전체 전환
  → Phase 1/2 결과 이상 없을 때
```

---

## 6. KR 전용 조정

| 항목 | US | KR |
|---|---|---|
| Full Refresh 주기 | 30분 | 30분 + 10:00/11:00/13:00 고정 |
| Fast-Track 트리거 | 10분 타이머 | 10분 타이머 + vol_ratio > 2x |
| 후보 풀 확장 | 35→60개 | 35→60개 (KOSDAQ 35% 최소 유지) |
| IGNORE→BR avg | 84분 | 133분 (US보다 느림) |
| 1차 검증 | 완료 | **별도 테스트 필요** |

---

## 7. 비용 모델 (최종)

### US 하루 비용
| Layer | 모델 | 회수/일 | 토큰/회 | 비용 |
|---|---|---|---|---|
| Layer 1 신규감지 | Haiku | 20 | ~300 | $0.004 |
| Layer 2 Fast-Track | Haiku | 27 | ~800 | $0.027 |
| Layer 3 1차(Haiku) | Haiku | 9 | ~1,500 | $0.018 |
| Layer 3 2차(Sonnet) | Sonnet | 9 | ~4,946 | $0.297 |
| **합계** | | | | **$0.346** |

### 현행 vs 개선
| | 현행 | 개선 후 | 절감 |
|---|---|---|---|
| US/일 | $0.657 | $0.346 | **47%** |
| KR/일 | $0.657 | $0.346 | **47%** |
| **월간(×20거래일)** | **$26.3** | **$13.8** | **$12.5/월** |
| 후보 커버리지 | 35개 | 60~80개 | **+71%** |
| 평균 감지 지연 | ~6분 | ~10분 | -4분 |

---

## 8. 구현 순서

### Phase 1 (shadow, 2주)
1. `analysts.py`: `tier1_filter()` 함수 추가 (V5 프롬프트)
2. `analysts.py`: `fast_track_review()` 함수 추가
3. `state/selection_snapshot_*.json`: 구조 정의 + 읽기/쓰기
4. shadow 모드: 기존 로직 그대로, 새 구조 병렬 실행 + 비교 로그

### Phase 2 (hybrid, 1주)
5. `sub_screener.py`: 신규 감지 + Layer 1 연결
6. `trading_bot.py`: 30분 Full Refresh 스케줄 + snapshot 관리
7. Layer 3 새 구조 전환 (기존 select_tickers 보완 유지)

### Phase 3 (full)
8. Layer 2 Fast-Track 활성화
9. 기존 13분 select_tickers 비활성화
10. 후보 풀 35→60개 확장
11. KR 전용 조정 적용

---

## 9. 기존 시스템 대비 최종 검토 — 데이터 기반 수정사항

### 수정 1: 후보 풀 60개 확장 — 실익 없음 (설계 수정)

**데이터**:
- rank36-60 (seen_not_prompt) BUY_READY 비율: **0.03%** (4/11,558건)
- 평균 plan_a_score: 37.8 (rank21-35 대비 -10점)
- 제외 이유 80%가 hard_cap_cutoff (이미 과열)

**결론**: 35→60 확장은 BUY_READY 포착에 실질적 기여 없음.  
**수정**: 후보 풀 확장 목표를 **35→45개** (검증된 범위 소폭 확장)로 조정.  
→ 비용 상승 없이 안전한 범위.

---

### 수정 2: TRADE_READY 강등 — 현재도 자주 발생

**데이터**:
- 30분 이내 강등: 36.7% (18/49건)
- 0-30분 강등 후 실제 매수: **0건**

**결론**: 빠른 강등(30분 이내)은 이미 잘못된 BUY_READY였던 것.  
Fast-Track EXPIRE + TTL 3시간 설계 **적절** — 변경 없음.

---

### 수정 3: KR 전략 — 30분 주기 충분

**데이터**:
- KR IGNORE→BR avg: 120분, WATCH→BR avg: 123분
- KR WATCH 전환율(9.1%) > IGNORE 전환율(3.6%) — **WATCH 유지가 핵심**
- rank별 BR 비율: rank21-35에서 1.4% — rank 후반부도 포착 가치 있음

**결론**: KR은 US보다 전환이 느려서 30분 주기가 더 여유로움.  
WATCH 후보를 Fast-Track에서 잘 관리하는 것이 KR 수익의 핵심.

---

### 수정 4: 재시작 복구 — 현행 유사

**데이터**: 30분+ 갭 후 75%는 trade_ready=0로 시작 → 현행도 같은 패턴.  
**결론**: 30분 내 Full Refresh 복구는 현재(13분)와 실질 차이 없음. ✓

---

### 최종 검토 체크리스트

| 항목 | 현재 | 최종 설계 | 상태 |
|---|---|---|---|
| BUY_READY 포착률 | 100% | ~100% | ✓ |
| WATCH→TR 지연 | ~6분 | ~10분 | 허용 |
| TRADE_READY 강등 | 자동(13분) | TTL+EXPIRE | ✓ |
| 급변 대응 | 자동 | 강제 트리거 | ✓ |
| 재시작 복구 | ~13분 | ~30분 | 허용 |
| **후보 풀 확장** | 35개 | **35→45개** (60개→하향) | ✓ |
| KR Fast-Track | — | WATCH 중심 | ✓ |
| KR 1차 검증 | — | Phase 1 shadow로 검증 | ⚠ |
| Audit 추적 | 완전 | label 확장 | ✓ |
| PathB/매수 로직 | — | 변경 없음 | ✓ |
| 롤아웃 | — | 3단계 shadow | ✓ |

---

### 최종 비용 (수정 후)

| | 현행 | 최종 설계 |
|---|---|---|
| 후보 풀 | 35개 | 45개 |
| Full Refresh | 21회/일 $0.657 | 9회/일 $0.315 |
| Fast-Track | 없음 | 27회/일 $0.027 |
| 신규감지 | 포함됨 | 20회/일 $0.004 |
| **US 합계** | **$0.657** | **$0.346** |
| **월간 절감 (US+KR)** | — | **~$12/월** |
| **감지 지연** | ~6분 | **~10분** |
| **BUY_READY 커버** | 100% | **~100%** |
