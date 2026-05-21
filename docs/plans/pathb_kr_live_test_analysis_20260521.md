# KR PathB 라이브 테스트 분석

작성일: 2026-05-21

## 현재 live 기준 플래그

| 설정 | 이전 | 이후 | 의미 |
|---|---|---|---|
| `PATHB_KR_LIVE_ENABLED` | false | true | KR PathB live gate ON |
| `KR_CLAUDE_PRICE_LIVE_ENABLED` | false | false | legacy Claude-price live gate는 계속 OFF |
| `KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK` | true | false | KR Claude-price 신규 진입 차단 해제 |
| `PATHB_INTRADAY_ONLY` | true | false | PathB hold-days 정책 허용 |
| `PATHB_US_LIVE_ENABLED` | true | true | US PathB live는 기존 ON 유지 |

주의: KR PathB live eligibility의 기준은 `PATHB_KR_LIVE_ENABLED`이며,
`KR_CLAUDE_PRICE_LIVE_ENABLED`는 legacy gate로 유지한다. 현재 live 정책에서는
`PATHB_INTRADAY_ONLY=false`이므로 PathB plan의 hold-days 정책을 허용한다.

---

## 오늘 KR PathB 매수 없었던 원인

### 1. Claude가 trade_ready=[] 출력

pre-market 선택부터 정시 재스크리닝까지 전 시간대에 걸쳐 `trade_ready=[]`.
파이프라인 필터 문제가 아니라 **Claude raw output 자체가 0개**.

3개 analyst 모두 동일 사유:
- 1d +3.93% 강반등 BUT 5d -3.66% 주간 하락 추세 지속
- GC/DC = 13/17 (DC 우위)
- VKOSPI 결측, 외국인/기관 수급 N/A
- 데이터 품질: `vkospi_missing, kr_corp_news_coverage_low, external_data_empty`
- consensus weighted_score=0.27 → MODERATE_BULL 임계치 미달 → CAUTIOUS

### 2. Claude가 생성한 price_targets는 shadow에만 등록됨

Claude가 3개 종목의 가격 플랜을 실제로 생성했으나 전부 shadow 행:

```
036540: zone=8500~9200 target=10200 stop=8200 conf=0.45
052900: zone=1900~2080 target=2300  stop=1800 conf=0.40
066980: zone=1880~2080 target=2250  stop=1780 conf=0.40
```

두 가지 이유 동시 적용:
- live 활성화 전 pre-market 선택 타이밍 → shadow 등록
- confidence 0.40~0.45 < `PATHB_MIN_CONFIDENCE=0.5` → live 자격 미달

live 켜고 재시작한 09:48 session_open에서는 price_targets 자체 0개.

### 3. KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK=true (오늘 수정 전)

설령 zone hit가 발생해도 `pathb_runtime.py:1203`에서 주문 차단.
오늘 수정으로 false 전환 완료.

---

## US PathB와 구조적 차이

| | KR | US |
|---|---|---|
| 추가 진입 차단 | `KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK` (오늘 false로 변경) | 없음 |
| 확인 게이트 | `KR_CONFIRMATION_GATE_ENABLED=true` | 없음 |
| Shadow plan | `PATHB_KR_SHADOW_PLAN_ENABLED=true` | false |
| 오늘 포지션 출처 | 해당 없음 | 전날 세션 carry-over (Claude 오늘 생성 아님) |
| price_targets confidence | 0.40~0.45 (임계치 미달) | 금일 생성 0개 |

---

## 방향 평가

### 좋은 점
- 구조가 의도대로 동작: CAUTIOUS + 데이터 결측 상황에서 confidence 미달은 합리적 필터
- shadow hit (010170, 125020) 발생 → 파이프라인 자체는 정상
- 내일 live ON 상태에서 시장 조건 개선 시 바로 동작 가능한 준비 상태

### 리스크
- VKOSPI 결측·수급 N/A가 KR에서 반복 발생 → Claude가 구조적으로 confidence를 낮게 내는 경향 가능성
- CAUTIOUS 모드 지속 시 confidence 0.5 미달 → WAITING 0 → 매수 없음 반복
- `KR_CONFIRMATION_GATE_ENABLED=true` 아직 살아있음 (두 번째 진입 게이트)

---

## 모니터링 기준

내일 세션부터 다음을 확인:

1. **confidence 수준**: Claude가 KR price_targets를 0.5 이상으로 내는지
2. **시장 모드**: CAUTIOUS 지속 여부 (VKOSPI 복구, 수급 데이터 유입)
3. **WAITING 등록 여부**: `[PathB shadow plan] KR live off` 대신 `[PathB WAITING]` 로그 출현
4. **CONFIRMATION_GATE 통과 여부**: zone hit 후 gate 통과까지 이어지는지

### 추가 검토 조건 (데이터 확보 후)

- KR confidence가 계속 0.4대만 나온다면 → `PATHB_MIN_CONFIDENCE` KR 전용 임계치 분리 검토
- CONFIRMATION_GATE가 반복 차단한다면 → gate 조건 완화 또는 shadow 전환 검토

**지금 당장 임계치 변경 없음. 내일 세션 결과 먼저 확인.**
