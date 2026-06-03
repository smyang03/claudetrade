# 최적 선택 아키텍처 설계 v3.1

작성일: 2026-06-03  
상태: 설계 확정 (재검토 완료) — 구현 대기  
이전 버전: v2.1 (delta) → v3.0 → v3.1 (재검토 수정)  
관련 문서: `docs/design/screener_analysis.md`

---

## 문서 기준선

이 문서는 **Claude 후보군 판단 구현 기준 문서**다.

- `docs/reports/trainer_positive_negative_improvement_review_20260603.md`는 DB replay와 screener 근거 문서다.
- `docs/reports/trainer_improvement_action_review_20260603.md`는 audit truth/read-only 운영 개선 문서다.
- 세 문서 표현이 충돌하면 이 문서의 **최종 구현 계약**을 따른다.
- 이 문서에서 `EXPANSION`은 설계 설명 용어이고, 구현/DB/audit 값은 기존 `DISCOVERY` 계약을 사용한다.

### Claude 후보군 판단 최종 구현 계약

1. 오늘 live 1차는 **US CORE +5 strict DISCOVERY**만 적용한다.
2. `75개`는 1차 live config 목표가 아니라 future upper-bound/cost model이다. 실제 prompt를 75개로 강제 채우지 않는다.
3. `US_SELECTION_PROMPT_CAP` 또는 trainer hard cap은 1차에서 75로 열지 않는다. 이후 75를 열더라도 별도 `discovery_limit=5`에 해당하는 제한이 없으면 적용하지 않는다.
4. `candidate_pool_role=EXPANSION`과 `expansion_reason`은 구현하지 않는다. cap 밖 회수 후보는 `candidate_pool_role=DISCOVERY`, `discovery_reason`으로 기록한다.
5. KR은 오늘 live 후보군 판단 변경 대상이 아니다. 향후 KR DISCOVERY를 쓰더라도 Claude 입력 후보 확대와 주문 권한 확대를 분리한다.
6. Smart Skip은 오늘 1차 후보군 판단 변경과 섞지 않는다. 원인 분리를 위해 후속 phase로 둔다.
7. watch/TR output cap, `candidate_actions`, `price_targets`, PathB/broker truth/risk/order 로직은 변경하지 않는다.

---

## 설계 전환 배경

v2.1(delta 구조)은 87% 비용 절감에는 효과적이나:
- 현재 시스템 핵심 강점(전체 후보 최신 context 재평가) 훼손
- 4레이어 + snapshot + TTL + race condition → 복잡도 폭발
- delta 컨텍스트 격리로 판단 품질 저하 위험

**"자동매매에서 비용 절감보다 비싼 것은 수익 경로의 판단 품질 저하"**

따라서 v3.1의 live 1차는 Smart Skip-first가 아니라, 기존 시스템 강점을 보존한 **Claude 후보군 판단 폭 확대**로 한정한다. Smart Skip, Tier 0, Downgrade, Entry Validation, price-plan split은 후속 phase에서 분리 검증한다.

### 문서 기준과 구현 계약

이 문서는 screener/selection 구현 설계 기준이다. `docs/reports/trainer_positive_negative_improvement_review_20260603.md`는 DB replay와 운영 판단 근거로 사용하되, 구현 필드명과 config/env 계약은 이 문서의 "구현 계약"을 따른다.

핵심 계약:

```text
EXPANSION = 설계 용어
구현 role = DISCOVERY
reason field = discovery_reason
signal field = discovery_signal_family
rank field = discovery_overlay_rank
```

`candidate_pool_role=EXPANSION` 또는 `expansion_reason`을 DB/audit/learning에 새로 만들지 않는다. 기존 `candidate_pool_role=DISCOVERY`와 `discovery_*` 필드를 사용한다.

또한 "live 후보 확대"는 기본적으로 Claude live prompt 입력 노출을 뜻한다. 주문 권한 확대가 아니다. `DISCOVERY_ALLOW_BUY_READY`, `DISCOVERY_ALLOW_PROBE_READY`, `DISCOVERY_ALLOW_PULLBACK_WAIT`는 별도 승인 전까지 false를 유지한다.

75개는 1차 구현 config 목표가 아니라 future upper-bound/cost model이다. 1차 screener 개선은 기존 CORE prompt를 유지하고 `DISCOVERY_MAX_SLOTS_US=5` 수준의 strict DISCOVERY append부터 검증한다.

---

## 데이터 기반 분석 요약

### 현재 시스템 낭비
```
KR: 19콜/일 중 86% TR 변화 없음 → skip 가능
US: 18콜/일 중 57% TR 변화 없음 → skip 가능
단, US 18콜/일은 전체 raw call 근사치이며 모든 호출이 full 후보 재판정이라는 뜻은 아님

Smart Skip 적용 후 full-rescreen 상한:
  KR: 19→3.5콜/일 (82%↓)
  US: 18→8콜/일 (54%↓, 상한 예시)

최종 US 목표:
  장 시작 CORE+5 strict DISCOVERY 1콜 + 장중 신규 후보 only triage
  75개는 future upper-bound/cost model
```

### 후보 품질 분포
```
rank01-20: BR 9.1%, score 55.8  ← 핵심 구간
rank21-35: BR 1.6%, score 47.8  ← 가치 있음
rank36+:   BR 0.0%, score 41.5  ← 현재 스크리너 한계

Tier 0 필터 효과: IGNORE 후보 중 3.7%만 안전 제거
  (나머지 96.3%는 liq=high → 반드시 Claude가 봐야 함)
```

### 1차/Fast-Track 프롬프트 검증 (v2 연구 결과 보존)
```
V5_two_pass (10세션): BR false negative 0%
Fast-Track (6세션): PROMOTE 76%, KEEP 96%
→ delta v2는 shadow 증거 확보 후 검토
```

### 400토큰/후보 구성 (실측)
```
input: 246토큰/후보 (고정 254 + 후보당 251)
output: 139토큰/후보 (TR당 +630 — 가격플랜이 주범)
실측 compact: 12,160 input + 1,693 output = $0.062/콜
```

---

## 현재 시스템 평가

### 강점 (보존할 것)
- **단순함**: 1콜, 상태 없음, 자기복구
- **최신 context**: 매 호출 fresh digest/intraday 반영
- **검증된 수익**: US PathB 누적 +71%
- **Race condition 없음**: 단일 경로

### 단점 (보강할 것)
- 84%/57% 중복 호출 낭비
- IGNORE 후보에도 246토큰 input 지불
- TR 강등 기준 없음
- 매수 시점 stale 정보
- Output 팽창 (가격플랜 — TR=4시 4,004토큰)

---

## 5개 장치 역할 분리

```
Smart Skip     = 중복 호출 절감 장치
Tier 0         = Claude 전 쓰레기 후보 사전 제거 장치
Downgrade gate = 나빠진 TR 즉시 강등 장치
Entry freshness= stale 매수 방지 장치
Price-plan split= output 절감 장치
```

Smart Skip만으로는 비용은 줄지만 품질 문제 일부가 그대로 남음.  
5개 모두 적용 시 기존 단점이 제대로 보강됨.

---

## v3.0 → v3.1 재검토 수정 사항

### 데이터로 확인된 수정 필요 항목

**수정 1: Selection/price_plan split 우선순위 상향 (4→2순위)**
```
TR=4일 때 output 4,004토큰 → $0.040/콜 (콜당 비용의 67%)
Smart Skip으로 콜 수를 줄여도 콜당 비용은 그대로
→ split이 콜당 비용 절감의 핵심 → 2순위로 격상
```

**수정 2: Downgrade gate 초기값 보수적으로 조정**
```
재등판 케이스: 0건 (2026-05-01~ 측정)
→ 현재 시스템에서 TR은 세션 내 강등이 사실상 없음
→ -3% 기준 즉시 적용 시 멀쩡한 TR 강등 위험
→ 초기: TTL 3시간만 적용, 가격 기준(-3%)은 shadow 관찰 후 활성화
```

**수정 3: Phase 1 분할 (한번에 3개 → 순차 배포)**
```
현재 위험: Smart Skip + screener expansion + Tier 0 + Downgrade를 동시에 넣으면 원인 분리가 안 됨
수정:
  Phase S1: Screener strict DISCOVERY만
  Phase S2: sub_screener FORCE_CALL / 신규 후보 triage 정리
  Phase K1: Smart Skip만 (S1/S2 지표 안정 후)
  Phase T1: Tier 0 + Downgrade TTL (검증 후)
  Phase T2: Downgrade 가격 기준 (shadow 후)
```

**확인: Tier 0 실효성 데이터**
```
QUARANTINE=0% (KR/US 모두) → 유해 후보 이미 차단됨
Tier 0 제거 가능: IGNORE 중 3.7% (195/5,247건) — BR=0 확인
주 가치: input 비용 절감보다 "쓰레기 후보 개념적 차단"
```

**확인: Smart Skip US 42% 변화 커버 여부 (미검증)**
```
US TR 변화 42% → fail-open 9조건이 이를 충분히 감지하는가?
→ Phase K1 배포 후 shadow 지표로 반드시 검증
→ missed_winner_after_skip 지표가 핵심 판단 기준
```

---

## 구현 우선순위 (v3.1 수정)

| 순위 | 장치 | 효과 | 복잡도 | 비고 |
|---|---|---|---|---|
| **1** | **Screener strict DISCOVERY** | hard_cap_cutoff 유망 후보 회수 | 낮음 | Phase S1 |
| **2** | **Selection/price_plan split** | 콜당 비용 67%↓ | 중간 | Phase 2 (상향) |
| **3** | **Smart Skip** | KR 82%↓, US 54%↓ | 낮음 | Phase K1 |
| **4** | **Tier 0 코드 pre-filter** | IGNORE 3.7% 제거 | 낮음 | Phase T1 |
| **5** | **Downgrade gate (TTL만)** | TR 강등 기준 확립 | 낮음 | Phase T1 |
| **5** | **Downgrade gate (가격)** | -3%/-5% 기준 | 낮음 | shadow 후 |
| **6** | **Entry Validation shadow** | Path A stale 방지 | 중간 | Phase 3 |
| 보류 | delta v2 구조 | 87% 절감 | 높음 | missed_winner 증거 후 |

---

## 전체 구조

```
┌─────────────────────────────────────────────────────┐
│  스크리너 (코드, 무료)                                │
│  현재: raw 38-80개/콜                                │
│  단기 목표: raw 60-110개/콜                          │
│  장기 목표: raw 600-900개 (스크리너 개선 후)          │
└──────────────────────┬──────────────────────────────┘
                       │
          ┌────────────▼────────────┐
          │  Tier 0 코드 pre-filter │  무료
          │  V5 규칙 코드 구현      │
          │  liq=high → 통과        │
          │  special_bucket → 통과  │
          │  score≥60 → 통과        │
          │  저품질 → 제거(3.7%)    │
          └────────────┬────────────┘
                       │
          ┌────────────▼────────────┐
          │  Smart Skip 판단        │  무료 (skip_state 조회)
          │  변화 있음? → 호출      │
          │  변화 없음? → SKIP      │
          └────────────┬────────────┘
                       │
           ┌───────────┴────────────┐
           │ SKIP                   │ 호출
           │ 기존 TR/WATCH 유지     ▼
           │                ┌───────────────┐
           │                │ select_tickers │  Sonnet
           │                │ (기존 구조 유지)│
           │                │ 35→US 75개     │
           │                └───────┬───────┘
           │                        │
           └────────────────────────┤
                                    │
          ┌─────────────────────────▼──────────┐
          │  Downgrade gate (코드, 매 cycle)   │  무료
          │  TTL 3시간 / chg -3% / RISK_OFF    │
          │  → WATCH로 강등                    │
          └─────────────────────────┬──────────┘
                                    │
          ┌─────────────────────────▼──────────┐
          │  zone hit + 30분+                  │
          │  Entry Validation (Path A)          │  Haiku
          │  BUY / SKIP advisory gate           │
          └─────────────────────────┬──────────┘
                                    │
          ┌─────────────────────────▼──────────┐
          │  PathB / 주문 실행                  │  변경 없음
          │  Hold advisor / Risk gate           │  변경 없음
          └────────────────────────────────────┘
```

---

## Smart Skip 상세 설계

### skip_state (최소 상태, snapshot 아님)
```python
skip_state = {
    "last_call_at": datetime,
    "last_index_pct": float,          # SPY/KOSPI 마지막 지수값
    "last_screener_tickers": set,     # 마지막 스크리너 출력 ticker set
    "last_digest_hash": str,          # digest 변경 감지용
    "active_tr_chg_map": dict,        # {ticker: chg_at_TR_registration}
}
```

### fail-open 조건 (하나라도 해당 → 반드시 Claude 호출)
```python
FORCE_CALL_CONDITIONS = [
    # 신규 ticker 감지
    lambda: new_tickers_in_screener(),

    # 지수 변화
    lambda: abs(current_index_pct - skip_state.last_index_pct) > 0.5,

    # KR vol spike (WATCH 후보)
    lambda: market == "KR" and any_watch_vol_doubled(),

    # digest 갱신
    lambda: digest_hash_changed(),

    # active TR 가격 급변
    lambda: any(
        abs(current_chg - skip_state.active_tr_chg_map[t]) > 1.5
        for t in active_tr_tickers
    ),

    # 시장 모드 변경
    lambda: market_mode_changed(),

    # 장초반/장마감 전환 (처음 15분, 마지막 30분)
    lambda: in_open_window() or in_close_window(),

    # fallback 타이머 (US: 20분, KR: 30분)
    lambda: elapsed_since_last_call() > fallback_threshold,

    # broker/risk 상태 변화
    lambda: broker_state_changed() or risk_mode_changed(),
]
```

**원칙**: 헷갈리면 호출. Skip은 "확실히 안전할 때만".

### 데이터 기반 임계값
- 지수 변화: ±0.5% (TR 변화와 상관관계 확인 필요)
- KR vol spike: 2배+ (WATCH→BR 전환 시 avg +2.45x 확인)
- fallback 타이머: US 20분 (WATCH→BR 중앙값 59분), KR 30분 (중앙값 123분)
- active TR 가격: ±1.5% (강등 기준 -3%의 절반, 선제적 감지)

### Skip 대상 명확화
```
SKIP 대상: select_tickers 호출 (Claude selection call)
계속 실행: PathB entry scan / broker truth / risk gate /
           profit ladder / pre-close / hard stop / loss cap /
           hold advisor safety path
```

---

## Tier 0 코드 pre-filter

### 구현
```python
def tier0_code_filter(rows: list[dict]) -> list[dict]:
    shortlist = []
    for row in rows:
        liq    = row.get("liquidity_bucket", "")
        bucket = row.get("primary_bucket", "")
        score  = row.get("trainer_plan_a_score", 0) or 0
        chg    = row.get("change_pct", 0) or 0

        # PASS 1: 절대 제거 금지
        if liq == "high":
            shortlist.append(row); continue
        if bucket in {"liquidity_leader", "gap_pullback",
                      "opening_range_pullback", "pullback_watch"}:
            shortlist.append(row); continue
        if score >= 60:
            shortlist.append(row); continue

        # PASS 2: 명백한 저품질만 제거
        if liq == "low" and score < 55 and chg < 4.0:
            continue  # SKIP
        if liq == "mid" and score < 52 and chg < 3.0:
            continue  # SKIP

        shortlist.append(row)
    return shortlist
```

### 실제 효과 (데이터 확인)
- 제거 가능: IGNORE 중 3.7% (195/5,247건)
- BR 없음 확인: tier0_skip 195건 모두 BR=0
- 주 가치: liq=high IGNORE의 input 비용은 여전히 발생 (구조적 한계)

---

## Downgrade Gate (코드 전용)

```python
DOWNGRADE_RULES = [
    # TTL: 3시간 (데이터: 강등 평균 244분)
    lambda t, now: (now - t.added_at).total_seconds() > 3 * 3600,

    # 가격 급락: TR 등록 시점 대비 -3%
    lambda t, now: current_chg(t.ticker) < t.ready_chg - 3.0,

    # 시장 모드 급변
    lambda t, now: (market_mode() == "RISK_OFF"
                    and t.strategy not in {"mean_reversion"}),

    # 장 마감 임박 (30분 전)
    lambda t, now: minutes_to_close() < 30
                   and not t.strategy.startswith("CLOSED_"),
]
# 강등: TRADE_READY → WATCH (IGNORE 아님, 재판단 가능)
```

**데이터 근거**: 30분 내 강등 사례 18건 → 매수 성사 0건 (잘못된 TR 판정 확인)

---

## Selection / Price-plan Split (4순위)

### 문제
```
TR=0: output 1,472토큰
TR=4: output 4,004토큰  (TR당 +633토큰)
→ 가격플랜이 output 팽창 주범
```

### 설계
```
1단계 (selection): WATCH / TR / IGNORE 판단만 → output ~600토큰
2단계 (price-plan): TR 후보만 별도 소형 콜 → ~400토큰 × TR수
```

### 계약 유지 방법
```json
{
  "ticker": "NVDA",
  "action": "TRADE_READY",
  "price_targets": "pending",   ← 기존 계약 필드 유지
  "price_plan_status": "queued"
}
```

→ price_plan 콜이 완료되면 price_targets 채움  
→ PathB는 별도 claude_price_plan 이미 운영 중 (중복 최소화)  
→ Path A만 price_plan 콜 필요

---

## Entry Validation (5순위 — Path A 한정)

**PathB는 이미 zone hit + broker truth + risk gate 완비 → 불필요**

### Path A 적용
```python
# zone hit 발생 + TR 판단이 30분+ stale 시
if path == "A" and (now - tr.added_at).minutes > 30:
    result = haiku_entry_check(ticker, tr)
    if result == "SKIP":
        demote_to_watch(ticker)
        log_skip(ticker, reason=result.reason)
    # 가격 목표 재계산 안 함
```

**프롬프트** (Haiku, ~400토큰):
```
[매수 직전 확인] {ticker} — Path A advisory
원 판단: {reason} ({elapsed}분 전)
현재: chg={chg} liq={liq} 시장={market_ctx}

BUY: 유효 / SKIP: 조건 변화
JSON: {"verdict":"BUY/SKIP","reason":"한줄"}
```

---

## 후보 풀 확장 계획

### 현재 → 단기 → 장기
```
현재:  raw 38-80개 → Tier 0 → Claude 35개
오늘 US live 목표: raw 100-120개 검토 → 기존 CORE 유지 + strict DISCOVERY +5 append
장기:  raw 600-900개 (스크리너 개선 후) → Tier 0 → 75개 상한 유지 또는 별도 검증 후 90개
```

### Claude 후보 수 가이드
```
보수적:  35-40개
중간:    50-55개
공격적:  65개
오늘 US 실제 1차 상한: 기존 hard cap 35 + strict DISCOVERY +5
future US hard envelope: 75개
오늘 US 실제 1차: CORE +5 strict DISCOVERY
비추천:  90개+   (상대 비교 피로도 + tail 품질 불확실)
```

### KR/US 분리
- **US**: live prompt 1차 확대 대상. 기존 CORE prompt를 보존하고 `DISCOVERY_MAX_SLOTS_US=5` 수준의 strict DISCOVERY append부터 적용한다. 75개는 future upper-bound/cost model이며 1차 config 변경 목표가 아니다. watch/TR 출력 cap은 유지한다.
- **KR**: 주문 권한 확대 대상이 아니다. DB replay상 KR strict +5/+10 prompt 노출은 개선 가능성이 있지만, 1차 live에서는 `DISCOVERY_ALLOW_*` false를 유지해 BUY_READY/PROBE_READY/PULLBACK_WAIT 승격을 막는다. KR 후보 확대는 "Claude 입력 노출"과 "주문 가능 후보 확대"를 분리해서 다룬다.

---

## Smart Skip 검증 지표 (운영 후 필수)

| 지표 | 설명 | 목표 |
|---|---|---|
| missed_winner_after_skip | skip 후 실제 매수 기회 놓친 수 | 0건/주 |
| skipped_watch_to_tr | skip 사이클에서 WATCH→TR 됐어야 할 케이스 | 측정 후 판단 |
| active_tr_mfe_after_skip | skip 시 활성 TR의 30/60분 MFE | 기존 대비 ±10% 이내 |
| stale_tr_entry | freshness gate 없이 30분+ stale TR 매수 수 | 0 (gate 효과 확인) |
| skip_rate_actual | 실제 skip 비율 | KR 70%+, US 40%+ |
| cost_actual | 실측 비용 | 추정치 ±20% 이내 |

---

## 비용 모델 (최종)

### 현재
```
US: 18콜 × $0.062 = $1.11/일
KR: 19콜 × $0.062 = $1.18/일
합산: $2.29/일 = $45.8/월
```

### Smart Skip 적용 후 (full-rescreen 상한 모델)
```
US: ~8콜 × $0.062 = $0.50/일  (54%↓)
KR: ~3.5콜 × $0.062 = $0.22/일  (82%↓)
합산: $0.72/일 = $14.4/월

절감: $31.4/월
```

위 `US 8콜/일`은 최종 설계가 아니라, 기존처럼 전체 selection을 다시 돌리는 full-rescreen 호출이 Smart Skip 후에도 하루 8번 발생한다고 가정한 **보수적 상한**이다. 최종 운영 설계는 아래처럼 "최초 CORE+5 strict DISCOVERY 1회 + 이후 신규 후보만 triage"가 맞다. 75개는 future upper-bound/cost model로만 둔다.

### 현재 시스템 호출 구조 정정

`US 18콜/일`은 raw call audit에서 본 전체 호출량의 근사치이지, "75개 또는 35개 전체 후보를 하루 18번 계속 본다"는 뜻이 아니다. 현재 시스템도 호출 경로가 섞여 있다.

| 경로 | 현재 동작 | 전체 후보 재평가 여부 | v3 최종 설계에서의 취급 |
|---|---|---|---|
| session_open | 장 시작 후보 생성 후 `select_tickers()` | full prompt pool | CORE+5 strict DISCOVERY부터 시작, 75개는 future upper-bound |
| scheduled `run_rescreen()` | `manual_rescreen(source_type="rescreen")` | full prompt pool | material change 없으면 skip 또는 신규 triage로 축소 |
| `sub_screener` trigger | 현재는 `manual_rescreen(candidate_override=screener_rows)`로 이어질 수 있음 | trigger 시 full prompt pool | 신규 후보 only triage로 전환 대상 |
| `_partial_reselect()` | 교체 후보를 새로 골라 `select_tickers()` | 신규/교체 후보 중심 | 기존 장점으로 유지 |
| analyst reinvoke 후 refresh | consensus 변화가 의미 있을 때 재스크리닝 | 조건부 full prompt pool | material change일 때만 full 허용 |

따라서 비용 계산은 고정 `8콜/일`이 아니라 아래처럼 나눠야 한다.

```
US 일일 selection 비용 =
  full_candidate_call_count × actual_prompt_cost
  + new_candidate_triage_call_count × triage_call_cost
  + material_change_full_reselect_count × actual_prompt_cost
```

목표는 `full_candidate_call_count=1`에 가깝게 유지하고, 장중에는 신규 후보 triage만 누적하는 것이다. `actual_prompt_cost`는 오늘 live 1차 CORE+5 기준 약 `$0.069`, 75개 upper-bound 비용 기준 약 `$0.114`로 본다.

### US CORE+5 strict DISCOVERY + 신규 후보 only triage (비용 모델)
```
콜당 비용 증가: +$0.052 (40개 추가 × 후보 10개당 약 $0.013)
최초 full selection 상한: 1콜 × $0.114 = $0.114/일
실제 1차 CORE+5: 1콜 × 약 $0.069 = $0.069/일
신규 후보 triage: 3~6콜/일 × 약 $0.023~$0.030 = $0.069~$0.180/일
US 합산 예상:
- CORE+5 실제 1차: $0.138~$0.249/일 = $2.8~$5.0/월
- 75개 upper-bound: $0.183~$0.294/일 = $3.7~$5.9/월

비교:
- US 현재 full 35개 18콜/일: 약 $22.3/월
- US 75개 full-rescreen upper-bound 8콜/일: 약 $18.2/월
- US CORE+5 + 신규 후보 only triage: 약 $2.8~$5.0/월
- US 75개 + 신규 후보 only triage: 약 $3.7~$5.9/월
```

---

## 구현 순서 (v3.1 수정)

### Phase S1 — Screener strict DISCOVERY only
1. `runtime/candidate_discovery_overlay.py`: KR/US market-specific strict eligibility를 추가한다.
2. `DISCOVERY_MAX_SLOTS_US=5`부터 시작한다. KR은 prompt-only +5 검토가 가능하지만 주문 권한은 열지 않는다.
3. `US_SELECTION_PROMPT_CAP`, `CANDIDATE_PROMPT_POOL_HARD_CAP_US`를 75로 바로 변경하지 않는다. 75는 upper-bound/cost model로만 둔다.
4. `candidate_pool_role=DISCOVERY`, `discovery_reason`, `discovery_signal_family`, `discovery_overlay_rank`를 기존 audit/learning 계약에 맞춰 보존한다.
5. `DISCOVERY_ALLOW_BUY_READY`, `DISCOVERY_ALLOW_PROBE_READY`, `DISCOVERY_ALLOW_PULLBACK_WAIT`는 false를 유지한다.
6. live audit 지표 로깅을 시작한다: prompt_count, core_retention, discovery_count, discovery_outcome, evidence_coverage, candidate_actions_missing_contract, PathB PULLBACK_WAIT retention.

**검증 기준**: core_retention 100% 근접, US DISCOVERY +5 유지, candidate_actions 누락률 증가 없음, evidence overlap 기존 경고 기준 유지, PathB PULLBACK_WAIT 감소 없음.

### Phase S2 — sub_screener FORCE_CALL / 신규 후보 triage
1. `runtime/sub_screener.py` trigger는 full rescreen 반복이 아니라 다음 selection의 FORCE_CALL reason 또는 신규 후보 triage로 연결한다.
2. S1 strict eligibility helper를 sub_screener trigger와 공유한다.
3. material change가 아닌 단순 신규 후보 감지는 기존 CORE full selection을 무조건 다시 열지 않는다.

### Phase K1 — Smart Skip only (S1/S2 지표 안정 후)
1. `trading_bot.py`: skip_state 구조체 추가
   ```python
   skip_state = {
       "last_call_at", "last_index_pct",
       "last_screener_tickers", "last_digest_hash", "active_tr_chg_map"
   }
   ```
2. `trading_bot.py`: Smart Skip 판단 삽입 (`run_cycle` 내 select_tickers 호출 전)
3. skip audit 지표 로깅 시작 (missed_winner, skip_rate, actual_tr_change_captured)

**검증 기준**: skip_rate KR≥70%, US≥40%, missed_winner=0건/주

### Phase T1 — Tier 0 + Downgrade TTL (S/K phase 결과 확인 후)
1. `runtime/candidate_prompt_pool.py`: `tier0_code_filter()` 함수 추가
2. `trading_bot.py`: Downgrade gate TTL 3시간 (가격 기준 제외)
3. Downgrade 발생 시 audit 기록 (`downgrade_reason`, `downgrade_at`)

### Phase 2 — Selection/Price-plan Split (2주)
8. `minority_report/analysts.py`: `selection_only` 모드 추가 (output 압축)
9. 별도 `price_plan_call()` 함수 구현 (TR 후보만, Haiku 또는 Sonnet)
10. `pending_price_plan` 상태로 기존 계약 유지
11. PathB는 기존 `claude_price_adapter.py` 사용 → 변경 없음

### Phase T2 — Downgrade 가격 기준 (Phase T1 shadow 2주 후)
12. Downgrade gate 가격 기준 활성화 (-5% 시작, 이후 -3% 검토)
13. shadow 2주: 강등 건 중 실제 매수 성사 비율 측정

### Phase 3 — Entry Validation
14. Path A Entry Validation (Haiku advisory gate, shadow 먼저)

### Phase 4 — 검증 후 (별도)
16. Smart Skip 검증 지표 누적 분석
17. delta v2 shadow 연구 (missed_winner 증거 기반 판단)

---

## 현행 대비 최종 검토표

| 항목 | 현재 | v3.0 Smart Skip | 평가 |
|---|---|---|---|
| 단순함 | ✅ 단순 | ✅ 거의 유지 (skip_state만 추가) | ✓ |
| 최신 context | ✅ 매 호출 fresh | ✅ 호출 시 유지, skip 시 "변화없음" 전제 | ✓ |
| PathB 수익 경로 | ✅ 불변 | ✅ 불변 | ✓ |
| Race condition | ✅ 없음 | ✅ 거의 없음 | ✓ |
| 중복 호출 낭비 | ❌ 84%/57% | ✅ 82%/54% 감소 | ✓ |
| IGNORE input 낭비 | ❌ | △ Tier 0으로 3.7% 제거 (구조적 한계) | △ |
| TR 강등 기준 없음 | ❌ | ✅ TTL + 코드 강등 | ✓ |
| 매수 stale 정보 | ❌ | ✅ Path A Entry Validation | ✓ |
| Output 팽창 | ❌ | △ price_plan split (4순위) | 예정 |
| 후보 커버리지 | 35개 | **US 75개 상한 / 실제 CORE+5** | ✓ |
| 월 비용 | $45.8 | **US 신규only $2.8~$5.0 + KR 적용범위별** | ✓ |

---

## 라이브 반영 전 최종 장단점 리포트 (Codex 검토)

### 최종 판정

최종 확정 후보는 **v3.1 US Claude 후보군 판단 live 확대 방식**이다. v2 delta snapshot 방식은 호출량을 더 줄일 수 있지만, 현재 시스템의 가장 큰 장점인 "매 호출 최신 context + 단순한 selection 계약 + 낮은 race condition"을 훼손할 가능성이 더 크므로 보류한다. Smart Skip은 유효한 비용 절감 후보지만, 오늘 1차 live에서는 후보군 확대 효과와 원인 분리를 위해 후속 phase로 둔다.

운영자 결정 반영: **오늘 미국장부터 live 적용을 전제로 검토한다.** 여기서 "shadow"는 배포를 2주 미루자는 뜻이 아니라, live 동작 중 품질 지표를 계측해서 다음 변경의 원인 분리를 하자는 뜻으로 제한한다. 1차 live 후보는 기존 CORE를 유지하고 cap 밖 strict DISCOVERY를 +5까지만 append하는 방식으로 제한한다. US 75개는 비용/상한 모델이며 1차 config 목표가 아니다.

즉시 라이브에 넣을 수 있는 최종 최소 형태는 아래로 제한한다.

1. 기존 `select_tickers()` 프롬프트와 compact schema는 그대로 둔다.
2. US Claude 입력 후보는 실제 1차 live에서 CORE +5 strict DISCOVERY로 시작하고 watchlist/TR 출력 cap은 그대로 유지한다. 75개는 future upper-bound/cost model로 둔다.
3. `candidate_pool_role=EXPANSION` / `expansion_reason`은 쓰지 않는다. 구현과 audit은 `candidate_pool_role=DISCOVERY`, `discovery_reason`을 사용한다.
4. Smart Skip은 오늘 1차 live 변경과 섞지 않는다. broker truth, risk, PathB exit/entry scan, 보유 종목 보호 로직은 계속 실행한다.
5. 상태가 없거나 애매하면 반드시 Claude를 호출하는 fail-open 구조로 둔다.
6. Tier 0, Downgrade, Entry Validation, price-plan split은 오늘 1차 live 변경과 섞지 않는다.
7. 후보확대 결정은 DB 스키마 변경 없이 별도 audit log 또는 기존 meta payload 확장으로 먼저 검증한다.

### 기존 시스템에서 반드시 가져갈 장점

| 기존 장점 | 최종 방식에서의 보존 방법 |
|---|---|
| 최신 context 기반 판단 | 실제 Claude 호출 시 현재처럼 fresh digest, market judgment, evidence, lesson context를 모두 포함한다. |
| 단순한 호출 계약 | `minority_report/analysts.py`의 `wl/tr/ca` compact schema와 `candidate_actions` 계약을 Phase S1에서 변경하지 않는다. |
| 운영 audit 축 | `ticker_selection_log`, raw call audit, candidate audit의 기존 source/payload 계약을 유지한다. |
| PathB 수익 경로 안정성 | 후보군 확장은 selection 입력 폭과 audit meta만 바꾸며 PathB price plan, ladder, pre-close, hold advisor, broker-truth gate는 건드리지 않는다. |
| 복구 용이성 | 별도 selection snapshot truth를 만들지 않고, skip state는 캐시/판단 보조 데이터로만 취급한다. |

### 기존 시스템의 단점과 개선 방식

| 기존 단점 | v3.1 개선 | 라이브 1차 적용 여부 |
|---|---|---|
| 변화 없는 장에서 Claude 반복 호출 | Smart Skip으로 no-change cycle 생략 | 적용 |
| sub_screener trigger 후 full rescreen 비용 과다 | 신규 후보만 triage하고, material change 때만 full selection 강제 | 적용 후보 |
| IGNORE 후보 input 낭비 | Tier 0 코드 필터 | shadow 후 적용 |
| 오래된 trade_ready 유지 | TTL downgrade, Entry Validation | 후속 적용 |
| output에 price plan이 섞여 토큰 증가 | Selection/Price-plan split | 후속 적용 |
| 후보 수 35개 제한으로 신규 진입 포착 약함 | US 후보 입력 cap 75개 상한 + CORE 뒤 DISCOVERY append | 오늘 미국장 live 1차 적용 후보 |

### 최종 방식의 장점

1. 현재 시스템의 품질 장점을 대부분 보존한다. 최초 CORE+DISCOVERY selection은 기존과 동일한 최신 context를 Claude가 본다.
2. 구현 복잡도가 v2 delta보다 낮다. 4-layer snapshot, partial prompt merge, snapshot TTL race를 만들지 않는다.
3. 비용 절감 효과가 크다. US는 장 시작 기준판 이후 신규 후보만 triage하면 full-rescreen 재호출 비용을 대부분 피할 수 있다.
4. 신규 후보 확장 여지를 만든다. 절감된 호출 비용을 후보 cap 확대에 재투자할 수 있다.
5. fail-open 설계가 가능하다. 불확실하면 호출하므로 수익 기회 손실을 제한하기 쉽다.

### 최종 방식의 단점과 남는 위험

1. skip 조건에 잡히지 않은 context 변화는 놓칠 수 있다. 특히 US PathB는 42% change cycle이 실제 신규 수익 후보와 연결되는지 추가 검증이 필요하다.
2. skip state가 새 운영 상태를 만든다. corrupted, stale, cross-market contamination을 방지하기 위해 market별 session reset과 audit가 필요하다.
3. sub_screener 변경을 잘못하면 기존보다 신규 진입이 늦어질 수 있다. `manual_rescreen()`을 단순 제거하지 말고, 신규 후보 triage 또는 material-change full selection으로 반드시 연결해야 한다.
4. price-plan split은 현재 `price_targets` 요구 계약과 충돌할 수 있다. Phase 2 전까지는 `price_targets: pending` 호환 정책과 PathA/PathB adapter 테스트가 필요하다.
5. Tier 0의 직접 비용 절감은 작다. 데이터상 3.7% 제거 수준이므로 live 1차 핵심이 아니라 품질/안전 보조 장치로 봐야 한다.
6. 후보 수 확장은 품질 저하 가능성이 있다. DB replay 기준 US 75개 full 확대는 과하므로, 75개는 상한으로만 두고 실제 live 1차는 CORE +5 strict DISCOVERY로 제한해야 한다.

### 후보군 수량별 Claude 사용량 / 운영 리스크

계산 가정:
- 현재 실측: 35개 후보 기준 selection 1콜 = 약 `$0.062`.
- 후보 10개 추가당 비용 증가: 약 `$0.013/콜`로 계산한다.
- US 현재 호출량: 약 18콜/일.
- `US 8콜/일`은 full-rescreen 상한 모델이다. 최종 설계는 최초 CORE+5 strict DISCOVERY 1콜 + 신규 후보 triage 3~6콜/일을 기준으로 본다. 75개는 future upper-bound/cost model이다.
- 월 계산은 20거래일 기준이다.

| Claude 입력 후보 수 | 예상 비용/콜 | US 18콜/일 비용 | full-rescreen 8콜/일 비용 | 8콜 상한 월 비용 | 운영 리스크 | 판단 |
|---:|---:|---:|---:|---:|---|---|
| 35 | $0.062 | $1.12/일 | $0.50/일 | $9.9/월 | 현재 안정권. 신규 후보 tail 포착 약함 | 기존 |
| 50 | $0.082 | $1.47/일 | $0.65/일 | $13.0/월 | 입력 확대 효과는 있으나 75개 목표에는 부족 | 보수적 확대 |
| 55 | $0.088 | $1.58/일 | $0.70/일 | $14.1/월 | 기존 v3 단기 목표. 운영 리스크 낮음 | 중간안 |
| 65 | $0.101 | $1.82/일 | $0.81/일 | $16.2/월 | cycle latency/evidence coverage 관리 필요. 그래도 비용은 현재 US 18콜 대비 낮음 | 공격적 하한 |
| **75** | **$0.114** | **$2.05/일** | **$0.91/일** | **$18.2/월** | Claude attention 분산, evidence 누락, screener tail 품질 저하 가능성 증가 | **hard envelope / 상한 비용 모델** |
| 90 | $0.134 | $2.40/일 | $1.07/일 | $21.4/월 | rank36+ 실익 데이터가 약해 비용 대비 품질 불확실. raw screener 다양화 없으면 비추천 | 1차 live 비추천 |

후보 수 확대는 주문 수 확대가 아니다. `CLAUDE_SELECTION_COMPACT_WATCH_MAX=15`, `CLAUDE_SELECTION_COMPACT_TRADE_READY_MAX=5`가 유지되면 Claude가 보는 입력만 넓어지고, 실제 watch/TR 출력 폭은 기존과 같다. 따라서 주문/리스크 폭증보다 **입력 품질, evidence coverage, cycle latency**가 핵심 리스크다.

### Screener 개선 리포트 반영 (2026-06-03)

`docs/reports/trainer_positive_negative_improvement_review_20260603.md`의 section 8은 현재 v3 설계에 반드시 반영해야 한다. 결론은 아래와 같다.

```text
교체형 개선 금지.
기존 CORE 후보 유지.
cap 밖 후보는 strict rule 통과분만 DISCOVERY로 CORE 뒤에 append.
US 75개는 비용상 가능하지만 현재 DB replay 기준으로 full 확대는 과함.
US live 1차는 CORE +5 DISCOVERY.
```

검증 요약:

| Market | Scenario | Avg 60m | PF60 | 판단 |
|---|---|---:|---:|---|
| KR | current prompt | +0.4651% | 1.2977 | baseline |
| KR | CORE +5 strict DISCOVERY replay | +0.6203% | 1.4169 | 개선, 단 KR 주문 확대와 분리 |
| KR | CORE +10 strict DISCOVERY replay | +0.7432% | 1.5074 | 가장 강함, 단 KR 주문 확대와 분리 |
| US | current prompt | +0.7085% | 2.9918 | baseline |
| US | CORE +5 DISCOVERY replay | +0.7366% | 3.2481 | 1차 live 적정 |
| US | CORE +10 DISCOVERY replay | +0.6981% | 3.0507 | 평균 약화 |
| US | all available tail | +0.6538% | 2.7587 | tail 품질 저하 |

따라서 v3의 75개 설계는 다음처럼 보정한다.

| 항목 | 보정 전 | 보정 후 |
|---|---|---|
| 후보 확대 의미 | 75개 full prompt 목표 | 75개 hard envelope |
| live 1차 실제 확장 | 75개 전체 | 기존 CORE +5 strict DISCOVERY |
| 후보 순서 | trainer 재정렬 가능 | CORE 순서 보존, DISCOVERY는 뒤에 append |
| tail 후보 사용 | cap 밖 넓게 포함 | strict rule 통과 후보만 |
| 운영 KPI | prompt_count, cost 중심 | CORE retention, DISCOVERY count/outcome, PathB retention 추가 |

필수 metadata:

```text
candidate_pool_role = DISCOVERY for cap-out strict candidates
discovery_reason = hard_cap_recovered / near_breakout / source_consensus / core_cap_signal_candidate
trainer_score_rank
trainer_prompt_score
trainer_candidate_state
primary_bucket
evidence_class
quality_data_gaps
source_tags
```

계약 주의: `EXPANSION`은 이 문서의 설계 용어다. DB/audit/learning schema에는 새 `EXPANSION` role을 만들지 않고 기존 `candidate_pool_role=DISCOVERY`와 `discovery_*` 필드를 사용한다. 기존 core 후보는 role blank 또는 기존 값 그대로 보존하며, 확장 후보 여부와 원인은 `discovery_reason`/`discovery_signal_family`/`discovery_overlay_rank`로 구분한다.

US DISCOVERY 허용 기준:

```text
PLAN_A or PLAN_B with high score
momentum_now
near_breakout
high liquidity
source consensus candidate
```

US DISCOVERY 약화/제외:

```text
pullback_watch only
low liquidity
extreme chase
quarantine
data degraded
```

최종 반영 판단: **지금 설계에 필요한 것은 75개 full 확대나 즉시 hard-cap 변경이 아니라, 기존 CORE 뒤에 strict DISCOVERY +5를 append하는 것**이다. 75개는 future upper-bound/cost model로 유지한다.

### 최종 호출 구조

| 단계 | Claude 입력 | 목적 | 비용 성격 |
|---|---|---|---|
| 장 시작 1차 | 기존 CORE + strict DISCOVERY +5 | 오늘 watch/TR/PathB wait 후보의 기준판 생성 | CORE+5부터 시작, 75개는 future upper-bound |
| 장중 변화 없음 | Claude 호출 없음 | 기존 watch/TR/PathB wait 유지 | 0 |
| sub_screener 신규 후보 감지 | 신규 후보만 + 현재 watch/TR 요약 | ADD/WATCH/IGNORE/PULLBACK_WAIT triage | 소형 triage 콜 |
| 시장/리스크/material context 변화 | full selection 재호출 | 기존 기준판 자체를 새로 갈아야 하는 경우 | 75개는 별도 검증 후 upper-bound |

따라서 `8콜/일`은 최종 운영 목표가 아니라 fail-open이 많이 발생했을 때의 full-rescreen 상한이다. 정상 목표는 **CORE+5 strict DISCOVERY 1콜 + 신규 후보 triage 콜**이다. 75개는 이 목표가 안정화된 뒤 검토할 상한이다.

### 75개 upper-bound + CORE/DISCOVERY 운영 설정 점검 리스트

| 영역 | 현재 문서/운영상 확인된 상태 | 1차 적용 판단 |
|---|---|---|
| raw screener | `US_SCREEN_TOP_N` 기본 80 | DISCOVERY 후보 확보를 위해 100-120부터 검토. 150은 tail 품질 확인 후 |
| selection prompt cap | `US_SELECTION_PROMPT_CAP` 기본 35 | 1차에서는 75로 변경하지 않는다. 기존 CORE + DISCOVERY append 결과를 본다 |
| trainer hard cap | `CANDIDATE_PROMPT_POOL_HARD_CAP_US` 현재 35 | 1차에서는 75로 변경하지 않는다. hard_cap 밖 후보를 DISCOVERY overlay로 회수한다 |
| trainer target | `CANDIDATE_PROMPT_POOL_TARGET_US` 현재 24 | target은 CORE 품질 유지, `DISCOVERY_MAX_SLOTS_US=5`부터 분리 적용 |
| intraday evidence | `INTRADAY_EVIDENCE_MAX_TICKERS` 현재 30 | CORE와 DISCOVERY 상위만 우선 evidence. 75 전체 evidence는 latency 확인 후 |
| full evidence text | `SELECTION_FULL_EVIDENCE_MAX=5` | 유지 권장. DISCOVERY 전체에 긴 evidence를 붙이면 prompt가 과도하게 커진다 |
| compact output | watch 15 / TR 5 | 유지 권장. 후보 입력만 확대하고 주문 가능 후보 수는 늘리지 않는다 |

최종 판단: **75개는 오늘 live config 목표가 아니다.** 실제 1차 live prompt는 기존 CORE +5 strict DISCOVERY가 맞다. 65개/75개/90개 같은 숫자보다 중요한 것은 CORE를 밀어내지 않고 cap 밖 후보를 검증된 strict rule로 append하는 것이다.

### 75개 최종 점검 결과

75개 upper-bound는 장기적으로 검토할 수 있다. 단, 1차 개선의 본질은 주문 후보를 늘리는 것이 아니라 **Claude가 비교하는 입력 후보 폭을 통제해서 넓히는 것**이다. 따라서 기존 시스템의 강점인 full-context selection, compact output, PathB price plan 계약을 유지해야 개선으로 본다.

| 점검 항목 | 최종 판단 | 이유 |
|---|---|---|
| 신규 진입 포착 확률 | 개선 가능 | 35개 cap 밖 strict DISCOVERY 후보를 Claude가 직접 비교하게 되어 sub_screener/partial replacement 의존도가 줄어든다. |
| Claude 사용량 | 수용 가능 | US CORE+5 full 1콜 + 신규 후보 triage 기준 약 $2.8~$5.0/월이다. 75개 full 기준 $3.7~$5.9/월은 상한으로만 본다. |
| Claude 판단 품질 | 조건부 개선 | DISCOVERY +5는 attention 분산이 작지만, prompt pool rank, trainer score, evidence class가 같이 들어가야 한다. 75개는 별도 검증 전 적용하지 않는다. |
| 주문/리스크 영향 | 직접 영향 없음 | watch 15 / TR 5 cap을 유지하면 실제 주문 가능 후보 폭은 늘지 않는다. |
| PathB 수익 경로 | 보존 가능 | `candidate_actions`와 `price_targets` 계약을 유지하면 PathB wait/price-plan 경로를 깨지 않는다. |
| 운영 복잡도 | 증가 | raw screener, prompt cap, trainer cap, evidence cap, latency audit를 함께 봐야 한다. |

strict DISCOVERY가 실제 개선이 되려면 아래 조건을 만족해야 한다.

1. 기존 CORE prompt 후보를 밀어내지 않는다.
2. EXPANSION은 설계 용어로만 쓰고, 구현은 `candidate_pool_role=DISCOVERY`로 남긴다.
3. DISCOVERY는 cap 밖 후보 중 strict rule 통과분만 CORE 뒤에 append한다.
4. `US_SELECTION_PROMPT_CAP`와 `CANDIDATE_PROMPT_POOL_HARD_CAP_US`는 1차에서 75로 열지 않는다.
5. `CANDIDATE_PROMPT_POOL_TARGET_US`는 hard cap과 다르게 다룬다. target은 CORE 품질 유지, discovery_limit은 +5부터 시작한다.
6. `INTRADAY_EVIDENCE_MAX_TICKERS`는 전략적으로 결정해야 한다. CORE와 DISCOVERY 상위 후보 우선 evidence가 맞다.
7. `CLAUDE_SELECTION_COMPACT_WATCH_MAX=15`, `CLAUDE_SELECTION_COMPACT_TRADE_READY_MAX=5`는 유지한다. 이 cap을 같이 올리면 selection 개선이 아니라 주문 후보 확장 실험이 된다.

strict DISCOVERY 적용 후 즉시 봐야 할 운영 지표는 아래다.

| 지표 | 허용 기준 | 초과 시 해석 |
|---|---|---|
| `prompt_pool_count` | CORE + DISCOVERY 결과 | 75 미만이어도 정상. strict DISCOVERY가 부족한 것일 수 있음 |
| `core_retention` | 100%에 근접 | 기존 CORE가 밀리면 설계 위반 |
| `discovery_count` | US +5 이하 | 과도하게 늘면 tail 품질 저하 가능 |
| `discovery_role_coverage` | 확장 후보 100% `DISCOVERY` | `EXPANSION` role이 DB/audit에 쓰이면 schema 계약 오염 |
| `new_candidate_triage_calls` | sub_screener trigger 수와 일치 | 신규 후보만 봐야 할 구간에서 full selection을 반복하면 비용 설계가 깨진 것 |
| `excluded_from_prompt` tail 성격 | hard_cap_cutoff가 줄어야 함 | 여전히 고품질 후보가 잘리면 raw/trainer 재조정 필요 |
| `evidence_prompt_overlap_ratio` | 기존 경고 기준 유지 | 낮으면 75개 후보 중 evidence 없는 후보가 많음 |
| `CANDIDATE_CYCLE_MAX_MS` 근접 여부 | 25초 이내 유지 | 초과하면 evidence cap 또는 raw top_n을 낮춰야 함 |
| `candidate_actions_missing_contract` | 증가하면 안 됨 | 출력 압박 또는 prompt 과밀 가능성 |
| PathB `PULLBACK_WAIT` 등록 수 | 감소하면 안 됨 | 75개 확대가 PathB 가격 플랜 품질을 훼손한 신호 |

최종 체크: **75개는 오늘 live config 목표가 아니다.** 실제 1차는 US CORE +5 strict DISCOVERY다. 90개는 1차 live에서 제외한다. 적용 후 문제가 생기면 후보 cap을 되돌리기보다 먼저 CORE retention, DISCOVERY rule, evidence cap, compact output 누락률을 확인한다.

### 프롬프트/API/DB 계약 최종 점검

| 영역 | 최종 판단 | 이유 |
|---|---|---|
| Claude selection prompt | Phase S1에서 compact schema 유지 | 현재 compact schema, evidence, candidate_actions 계약이 이미 테스트와 운영 audit에 연결되어 있다. |
| Claude model/API 호출 | 기존 호출 함수를 재사용 | 후보군 판단 변경은 pool 구성만 바꿔야 하며, prompt format 변경은 비용/품질 원인 분리를 어렵게 만든다. |
| price_targets | Phase S1에서 유지 | PathA/PathB downstream이 price plan 존재를 기대하는 구간이 있으므로 split은 별도 phase에서 처리한다. |
| DB schema | Phase S1에서 신규 schema 금지 | 1차는 기존 payload/meta와 `DISCOVERY` 계약을 사용한다. 새 `EXPANSION` role/schema를 만들지 않는다. |
| source_type | 기존 값 유지 + meta reason 추가 | `initial`, `rescreen`, `sub_screener_rescreen` 의미를 바꾸지 않고 `discovery_reason` 같은 보조 필드만 추가한다. |
| candidate audit | 후보 payload merge 계약 유지 | `data/audit/candidate_audit.db`의 source_file/payload 계약을 깨지 않는다. |

### 라이브 반영 전 Go/No-Go 기준

**Go 조건**

1. dry-run 또는 live audit에서 `core_retention`이 100%에 근접한다.
2. `discovery_count`가 US +5 이하이고, prompt를 75개로 강제 채우지 않는다.
3. 모든 확장 후보가 `candidate_pool_role=DISCOVERY`와 `discovery_reason`을 가진다.
4. watch/TR output cap이 기존 watch 15 / TR 5를 유지한다.
5. `candidate_actions_missing_contract`, parse recovery, duplicate action이 증가하지 않는다.
6. PathB `PULLBACK_WAIT` 등록 수와 US PathB candidate retention이 감소하지 않는다.
7. KR은 `DISCOVERY_ALLOW_*` false를 유지하고 주문 권한 확대가 없다.

**No-Go 조건**

1. 기존 CORE 후보가 밀리거나 순서가 깨진다.
2. `DISCOVERY`가 +5를 넘거나 75개 full prompt를 강제로 채운다.
3. `candidate_pool_role=EXPANSION` 또는 `expansion_reason`이 DB/audit에 기록된다.
4. KR DISCOVERY 후보가 BUY_READY/PROBE_READY/PULLBACK_WAIT 또는 주문 가능 후보로 승격된다.
5. watch/TR output cap이 함께 증가한다.
6. PathB, broker truth, risk/sizing, 주문/청산 로직 변경이 필요해진다.
7. Smart Skip을 같은 패치에 넣어 성과 원인 분리가 불가능해진다.

### 라이브 구현 전 최종 작업 범위

1차 작업은 아래 파일 축으로 한정하는 것이 맞다.

| 파일/영역 | 허용 작업 | 금지 작업 |
|---|---|---|
| 후보 pool builder / selection 호출부 | 기존 CORE 보존, cap 밖 strict DISCOVERY +5 append, prompt_count/audit 기록 | 75개 강제 채우기, CORE 재정렬, KR 주문 권한 확대 |
| `runtime/sub_screener.py` 또는 호출부 | 후속 phase에서 trigger 발생 시 신규 후보 triage 또는 material-change full selection으로 라우팅 | full reinvoke를 무조건 삭제해서 신규 후보 탐지를 늦추는 변경 |
| audit/log helper | 기존 meta payload에 `DISCOVERY` role/reason 보존 | 새 `EXPANSION` schema/role 추가, 검증 전 DB schema 고정 변경 |
| tests | CORE retention, DISCOVERY +5 cap, role/reason 보존, selection call contract 테스트 | PathB 수익 보호 테스트 기대값 완화 |

### 2026-06-03 구현 후 재점검 결과

실제 반영값:

- `CANDIDATE_PROMPT_POOL_HARD_CAP_US=35`
- `DISCOVERY_PROMPT_ENABLED=true`
- `DISCOVERY_MAX_SLOTS_US=5`
- `DISCOVERY_ALLOW_BUY_READY=false`
- `DISCOVERY_ALLOW_PROBE_READY=false`
- `DISCOVERY_ALLOW_PULLBACK_WAIT=false`
- KR Plan A live 확장 플래그는 false 유지

US live DB replay 기준:

| 구분 | 후보 수 | ret60 표본 | ret60 평균 | ret60 중앙값 | MFE60 평균 | MAE60 평균 | PF60 |
|---|---:|---:|---:|---:|---:|---:|---:|
| current prompt | 5,534 | 1,831 | 0.5252% | 0.3251% | 1.1177% | -0.4117% | 1.9541 |
| CORE +5 strict DISCOVERY | 6,073 | 2,016 | 0.5738% | 0.3357% | 1.1690% | -0.4066% | 2.0393 |
| strict DISCOVERY only | 539 | 185 | 1.0554% | 0.5494% | 1.6762% | -0.3569% | 2.8556 |

Replay 판정:

- `core_retention_bad=0`
- `role_bad=0`
- DISCOVERY 추가 call group 108개, max added 5
- 주요 reject reason은 `no_useful_signal`, `low_liquidity`, `not_cap_excluded`

검증 결과:

- `python -m pytest -q`: 2,156 passed, 2 skipped
- live preflight: `ok=true`, `fail_count=0`, `blocked_if_live_start_warn_count=0`
- 남은 운영 경고는 broker truth stale 및 previous-session active PathB row 1건이며, 이번 후보군 판단 변경의 blocker는 아니다. live 시작 전 fresh broker truth 갱신은 별도 운영 절차로 수행한다.

### 최종 권고

라이브 최종 확정 방식은 **"장 시작에는 기존 CORE selection 품질을 보존하고, cap 밖 후보는 US +5 strict DISCOVERY부터 CORE 뒤에 append하며, 75개는 즉시 config 목표가 아니라 future upper-bound/cost model로 두는 방식"**이다. 이후 장중 신규 후보 triage와 material-change full selection은 S2에서 분리한다. v3.1의 나머지 장치들은 모두 타당하지만, 한 번에 적용하면 비용 절감, 신규 후보 확장, stale 방지, price-plan 분리의 원인 분리가 어려워진다. 따라서 첫 패치는 US CORE +5 strict DISCOVERY + 기존 audit/meta 보존까지만 진행하고, Smart Skip, sub_screener 라우팅, Tier 0, Downgrade, Entry Validation, price-plan split은 이후 실제 live 지표를 보고 순서대로 넣는 것이 가장 안전하다.
