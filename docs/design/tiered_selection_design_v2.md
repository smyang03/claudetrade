# 최적 선택 아키텍처 설계 v2.1

작성일: 2026-06-03  
목표: Claude 사용량 절감 + 후보 폭 확장 + 기존 운영 유사 유지

---

## 데이터 기반 분석 결과 (설계 근거)

### 현재 낭비 측정
```
KR: 19콜/일 중 84%(16콜) TR 변화 없음 → 불필요한 재판단
US: 18콜/일 중 57%(10콜) TR 변화 없음 → 불필요한 재판단
평균 호출 간격: KR 21분, US 20분
```

### BUY_READY 포착 경로 (US, 2026-05-01~06-02)
```
첫 판단 IGNORE → 나중에 BUY_READY: 81건 (37%)  ← 핵심 갭
첫 판단 WATCH  → 나중에 BUY_READY: 76건 (35%)
처음부터 BUY_READY:                 25건 (11%)

IGNORE 후보 풀 잔존율: 100% (모두 이후 호출에도 등장)
IGNORE→BR 소요시간: 30분내 37%, 60분내 51%, 평균 84분
```

### WATCH→BUY_READY 전환 시 실제 신호 (핵심 발견)
```
US: 전환 시 chg delta 평균 +0.59%
    70%가 chg 변화 1% 미만 (가격 사실상 동일)
    30%는 chg 오히려 하락하면서 승격
    → US는 가격 신호로 승격 감지 불가
    → digest/intraday context 변화가 실제 트리거

KR: 전환 시 chg delta 평균 +0.25%, 43%가 chg 하락
    BUT 평균 vol_ratio +2.45x 급증
    → KR은 거래량이 핵심 승격 신호
```

### 후보 풀 확장 가능성
```
rank36-60 (현재 hard_cap_cutoff 제외분) BUY_READY 비율: 0.03%
plan_a_score 평균: 37.8 (rank21-35 대비 -10점)
제외 이유 80%: hard_cap_cutoff (이미 과열)
→ 35→60 확장은 실익 없음. 35→45로 소폭 확장만 유효.
```

### TRADE_READY 강등 패턴
```
강등 케이스: 49건
30분 이내 강등: 36.7% (18건) → 이 중 매수 성사: 0건
61~120분 강등: 30.6% → 매수 성사: 4건 (재등판 패턴)
강등 평균 시점: 244분 후
→ 빠른 강등(30분내)은 잘못된 BUY_READY 판정이었던 것
→ TTL 3시간 + 가격급락 기준이 적절
```

### KR BUY_READY 특성
```
WATCH 전환율(9.1%) > IGNORE 전환율(3.6%) — WATCH 관리가 핵심
IGNORE→BR 평균 소요: 120분 (US 84분보다 느림)
rank21-35 BR비율 1.4% — 중하위권도 포착 가치 있음
```

### 1차 프롬프트 품질 검증

**테스트 도구**: `tools/test_tier1_quality.py`, `tools/test_tier1_expanded.py`  
**모델**: claude-haiku-4-5-20251001  
**데이터**: audit_candidate_rows (2026-05-18~06-01, US)

**테스트 방법**:
1. audit DB에서 실제 세션 데이터 재구성 (in_prompt=1 후보 + seen_not_prompt 확장분)
2. 후보를 압축 포맷(6필드)으로 변환하여 1차 프롬프트에 투입
3. 1차 SHORTLIST 결과와 실제 claude_action 비교
4. 핵심 지표: BUY_READY false negative율 (0%이어야 함)

**프롬프트 버전별 비교 (35개 풀, 10세션)**:

| 버전 | BR FN | VAL FN | 압축률 | 특징 |
|---|---|---|---|---|
| V1_baseline | 21.4% | 23.2% | 42% | 단순 liq/chg 필터 — 탈락 |
| V3_score_aware | 8.3% | 7.9% | 31% | score 임계값 — AMD 1건 누락 |
| **V4_strict_safe** | **0%** | **0.7%** | 8% | liq=high 절대 보존 |
| **V5_two_pass** | **0%** | **0%** | 3% | 2단계 pass, 최단 프롬프트 |

**최종 채택: V5_two_pass** — 0% false negative, 짧은 프롬프트, Haiku 사용

**V5 프롬프트 구조**:
```
[PASS 1 — 무조건 SHORTLIST 확정]
  A. liq=high
  B. bucket: liquidity_leader, gap_pullback, opening_range_pullback, pullback_watch
  C. s >= 60

[PASS 2 — PASS 1 미해당 후보만]
  liq=low AND s < 55 AND chg < +4%  → SKIP
  liq=mid AND s < 52 AND chg < +3%  → SKIP
  그 외 → SHORTLIST
```

**V1이 실패한 이유 (교훈)**:
- liq=high인 AMD(-0.6%), NOK(+3.7%), CLS(+8.5%) 등이 단순 chg 기준으로 SKIP됨
- BUY_READY 중 liq=high 비율 54%, 이들은 chg와 무관하게 반등/PROBE 후보
- 데이터: BUY_READY 후보 avg rank=12.2, liq_high=54% → 단순 rank 커트라인 금지

**60개 확장 풀 테스트 (10세션)**:

| 항목 | 결과 |
|---|---|
| BR false negative | **0%** (10세션 전체) |
| VAL false negative | **0%** |
| 평균 압축률 | 14.8% (60개→51개) |
| 확장 후보 평균 SKIP | 8.1개/세션 |
| API 비용 | $0.0233 (10세션) |

압축률이 낮은 이유: US 확장 후보(rank36-60)도 대부분 liq=high → PASS 1 통과.
→ 60개 확장 실익 없음 확인 → 35→45개 소폭 확장으로 결정.

---

### Fast-Track 프롬프트 품질 검증

**테스트 도구**: `tools/test_fasttrack_quality.py`  
**모델**: claude-haiku-4-5-20251001  
**데이터**: audit_candidate_rows (2026-05-20~06-01, US)

**테스트 방법**:
1. audit DB에서 WATCH → 이후 BUY_READY로 전환된 케이스 = positive (PROMOTE 기대)
2. WATCH → 끝까지 BUY_READY 안 된 케이스 = negative (KEEP 기대)
3. 세션당 positive 3~4개 + negative 3~4개 혼합 투입
4. Fast-Track 프롬프트 판정 결과와 실제 결과 비교

**세션별 결과 (6세션)**:

| 날짜 | PROMOTE 정답 | KEEP 정답 | 오류 |
|---|---|---|---|
| 2026-06-01 | 4/4 | 4/4 | 없음 |
| 2026-05-29 | 2/3 | 4/4 | FLEX KEEP(기대PROMOTE) |
| 2026-05-28 | 2/4 | 3/4 | ARM/ASTS KEEP, APLD PROMOTE(기대KEEP) |
| 2026-05-26 | 3/4 | 4/4 | NOK KEEP(기대PROMOTE) |
| 2026-05-22 | 3/4 | 4/4 | NOK KEEP(기대PROMOTE) |
| 2026-05-20 | 2/2 | 4/4 | 없음 |

**종합**:
- PROMOTE 정확도: **76%** (16/21)
- KEEP 정확도: **96%** (23/24)
- API 비용: $0.0112 (6세션 18콜)

**76% PROMOTE가 허용되는 이유**:
- 놓친 24%(5건)는 SOFT_IGNORE 재진입 또는 다음 20분 타이머에서 재포착 가능
- KEEP 96% = 잘못된 승격(false positive) 4% → 매수 신뢰도 유지에 중요
- Fast-Track의 실패 비용 비대칭: PROMOTE 누락 < KEEP 오판

**Fast-Track 프롬프트 구조**:
```
[Fast-Track 승격 체크] 장중
목적: WATCH 후보가 지금 BUY_READY 조건 충족하는지 판단.

현재 시장: {market_ctx}
후보: {ticker} {watch_chg}→{now_chg} liq={liq} bucket={bucket} s={score} [WATCH]

PROMOTE: 지금 BUY_READY 조건 충족
KEEP:    계속 관찰
EXPIRE:  조건 악화 또는 기회 소멸

JSON: {"results":[{"ticker":"X","verdict":"PROMOTE/KEEP/EXPIRE","reason":"한줄"}]}
```

**Entry Validation 프롬프트 미검증 (Phase 1 shadow에서 검증 예정)**:
- 현재 설계만 완료, 품질 테스트 미실시
- Phase 1에서 hold_advisor 판단과 비교하여 검증

---

## 핵심 문제 정의

```
해결할 것:
  1. 변화 없는 재판단 낭비 (KR 84%, US 57%)
  2. TR 강등 기준 없음 → 조건 나빠진 TR이 매수로 이어질 위험
  3. 매수 시점 정보 stale → 판단이 몇 시간 전일 때 entry 신뢰도 낮음

건드리지 않을 것:
  - PathB zone hit → 매수 흐름
  - Hold advisor → HOLD/SELL 판단
  - Risk manager, 주문 실행, 브로커 동기화
```

---

## 설계 원칙

1. **변화 없으면 Claude 안 부른다** — 낭비 근원 차단
2. **변화 있을 때만, 변화된 것만 판단한다** — delta 원칙
3. **정보 staleness는 매수 시점에 보강한다** — 판단은 최신 정보 기준
4. **강등은 코드가, 승격은 Claude가 한다** — 역할 분리

---

## 전체 구조

```
┌──────────────────────────────────────────────────────────────────┐
│ 스크리너 (코드, 무료)                                              │
│ 5분마다 · 입력 35→45개 범위로 확장 · 후보 pool 유지               │
└────────────────────────┬─────────────────────────────────────────┘
                         │
          ┌──────────────▼───────────────┐
          │  세션 시작 (1회)              │
          │  1차: 45개 → Haiku 스크린    │
          │  2차: shortlist → Sonnet 판단│
          │  → Snapshot 생성             │
          │  TRADE_READY / WATCH /        │
          │  SOFT_IGNORE / HARD_IGNORE   │
          └──────────────┬───────────────┘
                         │
              ┌──────────▼──────────┐
              │  Selection Snapshot  │
              │  (state/ JSON)       │
              └──────────┬──────────┘
                         │
     ┌───────────────────┼──────────────────────┐
     │                   │                      │
┌────▼───────┐   ┌───────▼──────┐   ┌───────────▼────────┐
│ Layer A     │   │ Layer B      │   │ Layer C             │
│ 신규 Delta  │   │ WATCH 승격   │   │ SOFT_IGNORE 재진입  │
│ 5분마다     │   │ KR: 신호기반 │   │ 20분 TTL 만료 시    │
│ 1차+조건2차 │   │ US: 20분주기 │   │ 1차 재통과          │
└─────────────┘   └──────────────┘   └────────────────────┘
                         │
              ┌──────────▼──────────┐
              │  강등 (코드 전용)    │
              │  TTL / 가격급락 /    │
              │  시장모드 급변       │
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  매수 시점 보강      │  ← 신규 추가
              │  zone hit + 30분+   │
              │  → 소형 Claude      │
              │  BUY / SKIP         │
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  PathB / 주문 실행   │  변경 없음
              └─────────────────────┘
```

---

## 변화 감지 기준 (레이어별)

| 레이어 | 변화 있음 (Claude 호출) | 변화 없음 (호출 없음) |
|---|---|---|
| **Layer A** 신규 | snapshot에 없는 ticker 등장 | 없음 |
| **Layer B KR** | vol_ratio > 기준치 2배+ 상승 OR chg +1.5%+ | 두 조건 모두 미충족 |
| **Layer B US** | **20분 타이머** (가격신호 불가, 데이터로 확인) | 20분 미경과 |
| **Layer C** SOFT_IGNORE | eligible_after 타이머 만료 | 아직 만료 안 됨 |
| **강등** 트리거 | TTL 초과 / chg -3% / RISK_OFF / 장마감 30분전 | 조건 미충족 |
| **Entry Validation** | zone hit + 등록 후 30분+ 경과 | 30분 미만 경과 |
| **강제 Full Refresh** | 지수 ±1.5% 급변 / 시장모드 변경 / 수동 | 해당 없음 |

**US Layer B가 타이머 기반인 이유 (데이터 근거)**:
- US WATCH→BR 전환 시 chg delta 평균 +0.59%, 70%가 1% 미만 변화
- 30%는 chg 오히려 하락하면서 승격 → 가격 신호로 감지 불가
- 승격 원인은 digest/intraday context 업데이트 (코드 측정 불가)
- 따라서 US는 20분 주기 Fast-Track이 현실적 최선

---

## 세션 시작 (1회)

**목적**: 전체 후보 판단 + snapshot 초기화

```
스크리너 45개 → 1차(Haiku, V5_two_pass 프롬프트) → shortlist ~35개
                                                    ↓
                               2차(Sonnet, 기존 select_tickers 구조)
                                                    ↓
                 TRADE_READY / WATCH / SOFT_IGNORE / HARD_IGNORE
```

**후보 폭 확장 (35→45)**:
- 1차가 Haiku로 45개를 Claude 품질 기준으로 스크린
- 현재 score 순위 기반 35개보다 선별 품질 향상
- 2차 비용 동일 (shortlist ~35개)
- rank36-60 확장은 0.03% BR 비율로 실익 없어 불채택

**2차 출력에 추가되는 필드**:
```json
{
  "ignore_tier": "SOFT",  // SOFT(20분 재진입) or HARD(세션 종료까지 제외)
  "ignore_reason": "setup_unclear"
}
```

**HARD_IGNORE 기준** (2차가 판단):
- 이미 chg +15% 이상 과열
- 구조적 veto (브로커 제한, 유동성 없음)
- 명백한 펀더멘털 이슈

**비용**: 1차 $0.002 + 2차 $0.033 = **$0.035/세션**

---

## Layer A: 신규 Delta (5분마다)

**목적**: 스크리너가 새로 감지한 후보 즉시 처리

```python
new_tickers = screener.current_pool - snapshot.all_tickers

if not new_tickers:
    return  # Claude 호출 없음

shortlist = tier1_filter(new_tickers)  # Haiku, V5 프롬프트
if shortlist:
    judgments = tier2_select(shortlist, context=snapshot.summary)  # Sonnet
    snapshot.merge(judgments, layer="new_delta")
```

**비용**: 신규 없으면 $0, 신규 있으면 $0.002~0.005  
**하루 예상**: ~$0.010 (평균 3회 트리거 기준)

---

## Layer B: WATCH 승격 (KR/US 분리)

**목적**: 기존 WATCH가 조건 개선 시 TRADE_READY로 승격

### KR — 신호 기반

```python
# Step 1: 코드 변화 감지
for ticker in snapshot.watch:
    current = screener.get(ticker)
    if any([
        current.vol_ratio > ticker.watch_vol * 2.0,   # vol 2배+ 급증 (KR 핵심 신호)
        current.chg > ticker.watch_chg + 1.5,         # chg +1.5%+ 상승
        current.chg < ticker.watch_chg - 2.0,         # chg -2% 하락 (EXPIRE 후보)
    ]):
        trigger_fast_track.append(ticker)

# Step 2: 트리거된 것만 Fast-Track (Haiku)
```

### US — 타이머 기반 (가격 신호 불가)

```python
# 20분마다 현재 WATCH 전체 Fast-Track
# (US chg/vol로 승격 감지 불가 — 데이터 확인됨)
if elapsed_since_last_us_watch_review >= 20_min:
    fast_track_review(snapshot.watch["US"])
```

**Fast-Track 프롬프트**:
```
[Fast-Track 승격 체크] {market} 장중
목적: WATCH 후보가 지금 BUY_READY 조건 충족하는지 빠르게 판단.

현재 시장: {market_ctx}

후보 (현재 상태):
{ticker} {chg_when_watched}→{chg_now} liq={liq} bucket={bucket} s={score} [WATCH]

PROMOTE: 지금 BUY_READY 조건 충족
KEEP:    계속 관찰
EXPIRE:  조건 악화 또는 기회 소멸

JSON: {"results":[{"ticker":"X","verdict":"PROMOTE/KEEP/EXPIRE","reason":"한줄"}]}
```

**검증 결과** (6세션 테스트):
- PROMOTE 정확도: 76% (놓친 24%는 다음 사이클 또는 SOFT_IGNORE 재진입에서 커버)
- KEEP 정확도: 96% (잘못된 승격 4%)

**비용**:
- KR: 신호 기반 ~5트리거/일 × $0.001 = $0.005
- US: 20분 타이머 13회/일 × $0.001 = $0.013
- **합계 ~$0.018/일**

---

## Layer C: SOFT_IGNORE 재진입 (20분 TTL)

**목적**: IGNORE→BR 갭 해소

```
IGNORE→BR 데이터:
  전체 87건 중 30분내 37%, 60분내 51%
  IGNORE 후보 100%가 pool에 잔존 (사라지지 않음)
  → 주기적 재평가만 있으면 결국 다 포착 가능

해소 방법:
  2차에서 SOFT_IGNORE 판정 → 20분 TTL
  → eligible_after 도달 시 Layer A에서 신규처럼 1차 재통과
  → 1차 SHORTLIST면 2차 투입
  → 1차 SKIP이면 다음 20분 후 재시도 (최대 3회)
```

**비용**: 1차만 = $0.001 × ~10회/일 = **$0.010/일**

---

## 강등 (코드 전용, Claude 없음)

```python
DEMOTION_RULES = [
    # TTL 3시간 (데이터: 강등 평균 244분, 30분내 강등은 매수 성사 0건)
    lambda t: (now - t.added_at).hours > 3,

    # 가격 급락: TR 등록 시점 대비 -3%
    lambda t: current_chg < t.ready_chg - 3.0,

    # 시장 모드 급변
    lambda t: market_mode == "RISK_OFF" and t.strategy not in ["mean_reversion"],

    # 장 마감 임박
    lambda t: minutes_to_close < 30 and not t.strategy.startswith("CLOSED_"),
]
# 강등 시 → WATCH (IGNORE 아님, 재판단 가능)
```

---

## 매수 시점 정보 보강

**목적**: TR 판단이 stale할 때 entry 신뢰도 보강

```python
# zone hit 발생 시
if (now - trade_ready.added_at).minutes > 30:
    result = entry_validation(ticker, trade_ready)
    if result.verdict == "SKIP":
        block_entry()
        snapshot.demote(ticker, to="WATCH")
```

**프롬프트** (Haiku, ~400토큰):
```
[매수 직전 확인] {ticker}

원 판단: {original_reason} ({elapsed}분 전, Layer={layer})
현재 상태: chg={chg} liq={liq} bucket={bucket}
시장: {market_ctx}

지금도 매수 유효한가?
BUY:  원 판단 유효
SKIP: 조건 변화로 패스

JSON: {"verdict":"BUY/SKIP","reason":"한줄"}
```

**비용**: $0.001 × ~3회/일 = **$0.003/일**

---

## Snapshot 구조

```json
{
  "market": "US",
  "session_date": "2026-06-03",
  "session_open_at": "2026-06-03T22:35:00",
  "last_updated_at": "2026-06-03T23:10:00",
  "version": 12,
  "last_us_watch_review_at": "2026-06-03T23:05:00",
  "trade_ready": [{
    "ticker": "NVDA",
    "added_at": "2026-06-03T22:35:00",
    "ready_chg": 4.2,
    "expires_at": "2026-06-04T01:35:00",
    "layer": "session_open",
    "price_targets": {}
  }],
  "watch": [{
    "ticker": "AMD",
    "added_at": "2026-06-03T22:35:00",
    "watch_chg": -0.8,
    "watch_vol": 1.2,
    "watch_score": 54
  }],
  "soft_ignore": [{
    "ticker": "FLEX",
    "added_at": "2026-06-03T22:35:00",
    "eligible_after": "2026-06-03T22:55:00",
    "retry_count": 0
  }],
  "hard_ignore": ["RLAY", "HSAI"],
  "all_tickers": ["NVDA", "AMD", "FLEX", "RLAY"]
}
```

---

## 갭 해소 전체 매핑

| 갭 | 원인 | 해소 방법 |
|---|---|---|
| TR 강등 기준 없음 | 기존 미구현 | 코드 강등 규칙 (TTL/가격/모드) |
| 매수 시점 stale | 기존 미구현 | Entry Validation (Haiku 소형) |
| IGNORE→BR 포착 | delta만으로 불가 | SOFT_IGNORE 20분 TTL 재진입 |
| WATCH→TR 지연 | 기존 13분 재판단 의존 | KR 신호 + US 20분 타이머 |
| 신규 후보 즉시 | 기존 13분 재판단 의존 | Layer A 5분 Delta |
| 재시작 복구 | snapshot 소실 | state/ JSON TTL + 즉시 Full |
| Race condition | 다중 레이어 동시 쓰기 | version 기반 낙관적 잠금 |
| Audit 추적 | label 미분리 | label 확장 (tier1/fast_track/entry_val) |
| 롤아웃 안전성 | 없음 | 3단계 shadow |

---

## 비용 모델 (최종)

### 하루 비용 (US 기준)

| 레이어 | 모델 | 회수/일 | 단가 | 비용 |
|---|---|---|---|---|
| 세션 시작 1차 | Haiku | 1 | $0.002 | $0.002 |
| 세션 시작 2차 | Sonnet | 1 | $0.033 | $0.033 |
| Layer A 신규 Delta | Haiku+Sonnet | ~3 트리거 | $0.003 | $0.010 |
| Layer B Fast-Track | Haiku | 13 (타이머) | $0.001 | $0.013 |
| Layer C SOFT_IGNORE | Haiku | ~10 | $0.001 | $0.010 |
| Entry Validation | Haiku | ~3 | $0.001 | $0.003 |
| **합계** | | | | **$0.071** |

### 하루 비용 (KR 기준)

| 레이어 | 모델 | 회수/일 | 비용 |
|---|---|---|---|
| 세션 시작 1차+2차 | Haiku+Sonnet | 1 | $0.035 |
| Layer A 신규 Delta | Haiku+Sonnet | ~2 트리거 | $0.006 |
| Layer B Fast-Track | Haiku | ~5 신호 기반 | $0.005 |
| Layer C SOFT_IGNORE | Haiku | ~8 | $0.008 |
| Entry Validation | Haiku | ~2 | $0.002 |
| **합계** | | | **$0.056** |

### 현재 vs 설계

| 항목 | 현재 | 설계 | 변화 |
|---|---|---|---|
| US 일일 | $0.657 | $0.071 | **89%↓** |
| KR 일일 | $0.657 | $0.056 | **91%↓** |
| **월간 합산 (×20일)** | **$26.3** | **$2.7** | **$23.6 절감** |
| 후보 입력 | 35개 | **45개** | 1차 품질 선별 |
| 신규 감지 지연 | ~13분 | **~5분** | 개선 |
| WATCH 승격 지연 | ~13분 | **~20분 US / ~5분 KR** | KR 개선, US 유사 |
| IGNORE 포착 지연 | ~13분 | **~20~60분** | 일부 지연 허용 |
| BUY_READY 커버 | 100% | **~97%** | HARD_IGNORE 제외 |
| 매수 신뢰도 | stale | **Entry Validation 보강** | 개선 |
| PathB/HA 로직 | 불변 | **불변** | 안전 |

---

## KR 전용 조정

| 항목 | US | KR |
|---|---|---|
| Layer B 트리거 | 20분 타이머 | vol 2배+ OR chg +1.5% |
| Layer B 이유 | 가격신호 측정 불가 | vol_ratio 정상 작동 (2% 불량) |
| IGNORE→BR 평균 | 84분 | **133분** (더 느림) |
| WATCH→BR 평균 | 59분 | **123분** (더 느림) |
| SOFT_IGNORE TTL | 20분 | **30분** (전환이 느리므로) |
| Full Refresh 추가 | 없음 | 10:00, 11:30, 13:30 고정 (장중 주요 시점) |

---

## 구현 순서

### Phase 1 — Shadow (2주)
1. `analysts.py`: `tier1_filter()` + `fast_track_review()` + `entry_validation()` 추가
2. `state/selection_snapshot.py`: Snapshot 클래스 (read/write/merge/expire/lock)
3. shadow 실행: 기존 로직 유지 + 새 구조 병렬 실행 → 결과 비교 로그
4. KR 1차 품질 테스트 (US V5 프롬프트 동일 적용)
5. 비교 지표: BUY_READY 일치율, 감지 지연, 비용 실측

### Phase 2 — Hybrid (1주)
6. `sub_screener.py`: Layer A (신규 Delta) 활성화
7. `trading_bot.py`: Layer B (KR 신호 + US 타이머) + 코드 강등 규칙
8. Layer C (SOFT_IGNORE) + Entry Validation 활성화
9. 세션 시작 1차+2차 전환

### Phase 3 — Full
10. 기존 13분 full select_tickers 비활성화
11. 후보 입력 35→45개 확장
12. Audit label 전환 완료
13. KR SOFT_IGNORE TTL 30분 적용

---

## 현행 대비 최종 검토표

| 항목 | 현재 동작 | 설계 동작 | 평가 |
|---|---|---|---|
| TR 신규 포착 | 13분 재판단 | Layer A Delta(5분) + SOFT_IGNORE | ✓ 동일~개선 |
| TR 강등 | 자동(다음 cycle) | TTL+코드(즉시) | ✓ 개선 |
| WATCH 승격 US | 13분 재판단 | 20분 타이머 Fast-Track | △ 7분 지연 허용 |
| WATCH 승격 KR | 13분 재판단 | 신호 기반 Fast-Track | ✓ 동일~개선 |
| IGNORE→BR | 13분 재판단 | SOFT_IGNORE 재진입 | △ 지연 허용 |
| 매수 신뢰도 | stale 판단 그대로 | Entry Validation | ✓ 개선 |
| 후보 품질 | score 순위 35개 | 1차 품질선별 45→35개 | ✓ 개선 |
| PathB 수익 경로 | 불변 | 불변 | ✓ 안전 |
| 운영 복잡도 | 단순 | 4 레이어 | △ 증가 (shadow로 완충) |
| **월 비용** | **$26.3** | **$2.7** | **✓ 90% 절감** |

---

## Codex 검토 보강 (2026-06-03)

### 결론

v2.1의 방향은 맞다. 다만 현재 운영 코드와 DB 계약 기준으로는 그대로 구현하면 아래 4가지 문제가 생긴다.

1. `tier1_filter()`를 Haiku로 호출하는 설계는 V5_two_pass 규칙이 거의 결정형이므로 비용 낭비다.
2. Fast-Track이 `PROMOTE/KEEP/EXPIRE`만 반환하면 기존 `candidate_actions`/`price_targets` 계약과 맞지 않아 승격 후보가 런타임에서 다시 강등될 수 있다.
3. 새 레이어별 call label/source_type을 분리하지 않으면 `candidate_audit.db`에서 세션 시작 판단, delta, fast-track, entry validation 성과가 섞인다.
4. 현재 비용 모델은 raw call 실측과 공식 Claude 가격 기준을 다시 반영해야 한다.

따라서 최종 설계는 **code-first tier1 + compact structured Claude calls + selection snapshot/audit label 분리**로 조정한다.

### 현재 시스템과 맞춰야 할 사실

- 실제 selection 호출 경로는 `minority_report/analysts.py::select_tickers()`이며, 별도 루트 `analysts.py`는 없다.
- 현재 compact selection은 `runtime/selection_compact_schema.py`의 `wl/tr/ca` 계약을 사용한다.
- `candidate_actions`는 `runtime/candidate_actions.py`와 `bot/candidate_policy.py::normalize_selection_result()`가 정규화한다.
- `trading_bot.py`의 모든 `select_tickers()` 호출은 `prompt_pool_override`, `prompt_pool_meta_override`, `evidence_by_ticker`를 넘기도록 테스트로 보호된다.
- `runtime/sub_screener.py`는 이미 신규 후보 감지 역할을 일부 수행하지만, 현재 live trigger는 `_reinvoke_analysts()` + `manual_rescreen()`으로 이어져 full selection 호출을 만든다. Layer A는 이 경로 위에 추가하지 말고 이 경로를 대체해야 한다.
- `candidate_audit.db`의 `selection_meta_live` row는 토큰이 0으로 저장될 수 있다. 비용 측정은 `logs/raw_calls/*.json` 또는 `agent_call_events.db`를 같이 봐야 한다.

### 실측 기반 비용 보정

2026-06-03 raw call 기준:

| 시장 | label | calls | avg input | avg output | avg duration |
|---|---|---:|---:|---:|---:|
| US | select_tickers compact | 16 | 약 12,160 | 약 1,693 | 약 23.4초 |
| KR | select_tickers compact | 16 | 약 12,099 | 약 1,656 | 약 22.4초 |

compact 출력은 성공했지만 input prompt가 여전히 12k tokens 수준이다. 따라서 비용 절감의 핵심은 "한 번의 prompt를 싸게 만드는 것"보다 **full select_tickers 호출 횟수를 줄이는 것**이다.

공식 Claude 모델/가격 기준(2026-06-03 확인):

| 모델 | API ID | input $/MTok | output $/MTok | 비고 |
|---|---|---:|---:|---|
| Sonnet | `claude-sonnet-4-6` | 3 | 15 | 현재 기본 selection 모델과 일치 |
| Haiku | `claude-haiku-4-5-20251001` | 1 | 5 | 기존 테스트 스크립트의 0.8/4.0 가정보다 높음 |

`credit_tracker.py`의 Haiku 기본 가격은 0.8/4.0이라 비용 리포트가 낮게 잡힐 수 있다. Phase 0에서 가격 상수 또는 env override를 정리해야 한다.

### 수정된 레이어 설계

#### Tier 0: 코드 사전 분류

V5_two_pass는 Claude 호출이 아니라 코드로 구현한다.

```python
def tier1_code_filter(rows):
    for row in rows:
        if row.liq == "high":
            shortlist(row)
        elif row.bucket in {"liquidity_leader", "gap_pullback", "opening_range_pullback", "pullback_watch"}:
            shortlist(row)
        elif row.score >= 60:
            shortlist(row)
        elif row.liq == "low" and row.score < 55 and row.chg < 4:
            skip(row)
        elif row.liq == "mid" and row.score < 52 and row.chg < 3:
            skip(row)
        else:
            shortlist(row)
```

Haiku tier1은 Phase 1 shadow에서만 돌려 code tier1과 불일치율을 측정한다. live 후보 흐름에서는 코드 결과를 1차 truth로 사용한다.

#### Tier 1: 세션 시작 compact Sonnet

- 입력 후보는 raw pool 45개까지 넓힌다.
- Sonnet에는 code tier1 SHORTLIST만 넣는다.
- Sonnet prompt pool은 기존 35개 수준을 넘기지 않는다.
- 기존 `selection_compact.v1`의 `wl/tr/ca` 계약을 유지한다.
- `tr` 승격은 `ca[].a in BUY_READY/PROBE_READY`이고 `pt`가 유효할 때만 허용한다.

#### Tier 2: Layer A 신규 Delta

`runtime/sub_screener.py`를 재사용하되 full rescreen으로 연결하지 않는다.

새 동작:

1. 5분마다 screener refresh
2. snapshot에 없는 ticker만 추출
3. code tier1 통과분만 compact delta prompt로 판단
4. 결과를 snapshot에 merge

기존 `_reinvoke_analysts()`는 Layer A 경로에서 호출하지 않는다. 시장 모드 급변은 별도 forced refresh 조건으로만 처리한다.

#### Tier 3: Layer B WATCH Fast-Track

Fast-Track 출력은 `PROMOTE/KEEP/EXPIRE` 단독이면 부족하다. 승격 후보는 실행 계약까지 같이 반환해야 한다.

권장 출력:

```json
{
  "results": [
    {
      "ticker": "AMD",
      "verdict": "PROMOTE",
      "action": "PROBE_READY",
      "strategy": "opening_range_pullback",
      "confidence": 0.62,
      "freshness_verdict": "FRESH",
      "setup_maturity": "CONFIRMED",
      "price_targets": {
        "reference_price": 127.2,
        "buy_zone_low": 126.4,
        "buy_zone_high": 127.6,
        "sell_target": 131.0,
        "stop_loss": 124.8,
        "hold_days": 1,
        "confidence": 0.62
      },
      "reason_code": "FASTTRACK_OR_PULLBACK"
    }
  ]
}
```

runtime rule:

- `PROMOTE`라도 `price_targets`가 없으면 WATCH 유지
- `PROMOTE`라도 `action`이 `BUY_READY/PROBE_READY`가 아니면 WATCH 유지
- `EXPIRE`는 snapshot에서 hard delete가 아니라 `WATCH_EXPIRED` 또는 `SOFT_IGNORE`로 기록
- 기존 risk/order/broker truth gate는 그대로 둔다

#### Tier 4: Layer C SOFT_IGNORE

SOFT/HARD ignore를 Claude에게 장문으로 분류시키지 않는다.

- compact Sonnet 결과에서 `wl`에 없는 후보는 기본 `SOFT_IGNORE`
- product block, trainer quarantine, same-day hard loss, broker 제한, 유동성 구조 문제는 코드로 `HARD_IGNORE`
- `SOFT_IGNORE`는 market별 TTL 후 code tier1부터 다시 통과
- KR TTL은 30분, US TTL은 20분 유지
- 최대 retry 3회 후 세션 종료까지 `HARD_IGNORE_BY_RETRY`가 아니라 `SOFT_EXHAUSTED`로 분리한다. 이는 전략 실패가 아니라 selection 후보 생명주기 상태다.

#### Tier 5: Entry Validation

Entry Validation은 주문 승인자가 아니다. stale selection을 막는 마지막 advisory gate다.

- trigger: zone hit + selection age > 30분
- output: `BUY` 또는 `SKIP`
- `BUY`는 기존 PathB/risk/broker truth gate를 통과해야만 의미가 있다.
- `SKIP`은 trade_ready 해제 + WATCH demotion + audit 기록만 수행한다.
- 가격 목표를 새로 계산하지 않는다. 가격 목표 변경이 필요하면 별도 price-plan refresh로 분리한다.

### API 사용 방식 권장

현재 `client.messages.create(model, max_tokens, messages=[...])` 형태는 동작한다. 다만 신규 소형 호출은 아래를 적용한다.

1. `system`에 고정 계약을 두고, 후보/시장 delta만 user message에 둔다.
2. `system` 또는 고정 contract block에는 `cache_control: {"type": "ephemeral"}`를 적용한다.
3. Fast-Track, Entry Validation, Tier1 shadow는 `output_config.format` JSON schema를 우선 검토한다.
4. `stop_reason == "max_tokens"`는 기존 compact selection과 동일하게 fail-safe 처리한다.
5. raw call 저장에는 `prompt_version`, `selection_layer`, `snapshot_id`, `snapshot_version`, `shadow_only`를 반드시 넣는다.

### DB / Audit 계약

새 레이어는 아래 call label을 사용한다.

| 레이어 | raw_call label | prompt_version | ticker_selection_log source_type |
|---|---|---|---|
| 세션 시작 Sonnet | `selection_session_open_tier2` | `tiered_selection.session_open_compact_v1` | `tiered_session_open` |
| Tier1 shadow Haiku | `selection_tier1_shadow` | `tiered_selection.tier1_v5_shadow` | 기록 안 함 또는 audit only |
| 신규 Delta | `selection_new_delta` | `tiered_selection.delta_compact_v1` | `tiered_delta` |
| WATCH Fast-Track | `selection_watch_fasttrack` | `tiered_selection.fasttrack_v1` | `tiered_fasttrack` |
| SOFT_IGNORE 재진입 | `selection_soft_ignore_retry` | `tiered_selection.soft_retry_v1` | `tiered_soft_retry` |
| Entry Validation | `selection_entry_validation` | `tiered_selection.entry_validation_v1` | audit only |
| 강제 Full Refresh | `selection_forced_refresh` | `tiered_selection.forced_refresh_v1` | `tiered_forced_refresh` |

`audit_candidate_rows.payload_json`에는 최소 아래 키를 넣는다.

```json
{
  "selection_layer": "watch_fasttrack",
  "snapshot_id": "US_20260603_open",
  "snapshot_version": 12,
  "lifecycle_state_before": "WATCH",
  "lifecycle_state_after": "TRADE_READY",
  "ttl_policy": "US_WATCH_FASTTRACK_20M",
  "review_trigger": "timer_20m",
  "entry_validation_verdict": "",
  "shadow_only": true
}
```

기존 컬럼 중 아래는 적극 사용한다.

- `lifecycle_state`
- `candidate_age_min`
- `candidate_source`
- `first_seen_price`
- `first_ready_at`
- `first_ready_price`
- `review_called_at`
- `review_latency_sec`
- `selection_trace_id`
- `actual_prompt_call_id`
- `actual_prompt_included`
- `actual_prompt_rank`

초기 Phase에서는 DB schema 추가 없이 payload로 시작한다. schema 추가는 dashboard/분석 쿼리에서 반복 사용이 확인된 뒤에 진행한다.

### Snapshot 계약

Snapshot은 runtime truth가 아니다. 포지션/주문 truth는 계속 broker/risk/PathB local run이 우선이다.

필수 필드:

```json
{
  "schema_version": "selection_snapshot.v1",
  "snapshot_id": "US_20260603_open",
  "market": "US",
  "session_date": "2026-06-03",
  "version": 12,
  "updated_at": "2026-06-03T23:10:00+09:00",
  "last_full_refresh_at": "",
  "last_watch_fasttrack_at": "",
  "candidates": {
    "AMD": {
      "state": "WATCH",
      "first_seen_at": "",
      "last_seen_at": "",
      "first_ready_at": "",
      "last_review_call_id": "",
      "source_layer": "session_open",
      "retry_count": 0,
      "price_targets": {},
      "candidate_action": {}
    }
  }
}
```

쓰기 규칙:

- 파일 쓰기는 `filelock` + atomic replace를 사용한다.
- merge 전 version을 확인한다.
- stale snapshot이면 full refresh가 아니라 "rebuild from current selection_meta + current screener"를 먼저 시도한다.
- `state/brain.json`에는 쓰지 않는다.

### 구현 순서 수정안

Phase 0 - 설계 정합성 정리:

1. `credit_tracker.py` Haiku 가격 또는 env override 확인
2. raw-call 기반 비용 산출 스크립트에 tiered label 지원
3. `selection_snapshot.v1` schema 문서화
4. structured output 사용 가능 여부 SDK 테스트

Phase 1 - Shadow:

1. `runtime/selection_snapshot.py`
2. `runtime/tiered_selection.py`에 code tier1, lifecycle merge, demotion rules
3. 기존 `sub_screener` 결과를 snapshot shadow에만 merge
4. `selection_tier1_shadow`, `selection_watch_fasttrack` raw call 저장
5. 기존 selection 결과와 tiered shadow의 BR 포착/지연/비용 비교

Phase 2 - Hybrid:

1. Layer A 신규 Delta만 live merge
2. Layer B Fast-Track은 WATCH→TRADE_READY 승격만 허용
3. Layer C SOFT_IGNORE retry 활성
4. Entry Validation은 `SKIP`만 live 적용하고 `BUY`는 advisory로 기록

Phase 3 - Full:

1. 정시 full `select_tickers` 반복 호출 비활성화
2. 세션 시작 + forced refresh만 full compact Sonnet 유지
3. raw pool 45개, Sonnet prompt pool 35개 상한 유지
4. dashboard에 layer별 call/cost/latency/BR conversion 표시

### 최종 판단

이 설계는 바로 live full 전환하면 안 된다. 하지만 code-first tier1과 DB label 분리를 반영하면 현재 구조보다 더 넓은 후보를 보면서도 Claude 사용량과 진입 지연을 줄일 수 있다. 가장 먼저 구현할 것은 prompt가 아니라 `selection_snapshot`과 audit label이다. 이 둘이 없으면 비용 절감 여부와 수익률 개선 여부를 분리해서 증명할 수 없다.
