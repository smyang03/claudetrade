# OpenAlice 차용 항목 재검토·채택 설계 (2026-07-16)

작성: Claude. 설계 전용 — 코드 미변경. 원칙: **기존 배선 재사용, 신규 배선 최소화** (preflight 152개 체크·integrity_check·restart 연속성 기록·코덱스 P0 registry가 이미 존재 — 이중 배선 금지).

## 0. 재검토 결론

| 항목 | 재검토 판정 | 이유 |
|---|---|---|
| A. 라이브 회귀 시나리오(S1~S14) | **채택 — 단 신규 배선은 체크 1개뿐** | 152개 체크와 대조 결과 대부분 이미 커버. 진짜 구멍은 "출구 소유권 감사" 하나 — 이번 주 SCHG 사고(일반 리뷰가 코어 포지션 매도)가 정확히 이 구멍 |
| B. freshness 프롬프트 계약 | **축소 채택 — 메타데이터만, 행동 지시문 금지** | 문구 주입=통제불가 실증(7/1) 때문에 "선언 요구" 지시문은 위험. 신선도 숫자 블록만 추가하는 것은 "위생이 전부" 판정과 정합 |
| C. registry 반증 조건 필드 | **채택 — 코덱스 P0 스키마에 병합, 별도 파이프라인 금지** | P0 immutable registry가 유일 배선. 필드 스펙만 기여 |

---

## A. 출구 소유권 감사 (`exit_ownership.audit`) — 유일한 신규 코드

### A-1. 기존 커버리지 대조 (이중 배선 방지 근거)

S-카탈로그 14개 중 우리 기존 체크가 커버하는 것:
- S1 PnL 드리프트 → `integrity.audit`(integrity_check --watch) 기존
- S2~S4, S6 주문 체결 인지·정정 → `order_unknown.*` 5종 + `code.order_acked_stuck_recovery` 기존
- S7 외부 주문 관찰 → `broker_truth.open_orders_from_broker`·`positions_from_broker` 기존
- S8 재시작 캐시 생존 → `runtime.handoff_cache_hygiene`(f66cb5f) + `config.runtime_snapshot_drift` + restart before/after 브로커 대조(`live_restart_last.json`) 기존
- 브로커>장부 원칙 → `broker_truth.*` 계열 전체 기존

**커버 안 되는 것 = S5 "무방비 포지션"**: 지금 어떤 체크도 "모든 라이브 포지션에 유효한 출구 소유자가 붙어 있고 그 소유자가 살아 있는가"를 묻지 않는다. SCHG 사고는 코드 수정(cc4b9bd)으로 막았지만, **회귀를 탐지하는 런타임 감사가 없다**(예: 새 격리 소스 추가 시 ISOLATED_STRATEGY_SOURCES 누락, 일반 포지션의 sl/tp/plan 전부 결손, 브로커에만 있는 고아 포지션에 보호장치 부재).

### A-2. 설계

- **위치**: `tools/live_preflight.py`에 체크 함수 1개 추가(신규 파일·스케줄러·프로세스 없음). preflight는 이미 재시작 직후·장전에 돌므로 실행 훅도 기존 것.
- **입력**(전부 읽기 전용, 기존 산출물): `state/live_open_positions.json` + `state/live_broker_truth_snapshot.json` + effective config.
- **판정 로직** — 포지션별로 출구 소유자를 분류하고 하나 이상 성립해야 PASS:
  1. `isolated_strategy` (source_strategy ∈ ISOLATED_STRATEGY_SOURCES — risk_manager에서 import, 목록 하드코딩 금지): `exit_owner`/`exit_policy` 필드 부착 확인 + **일반 매도 플래그(pending_next_open_sell 등)가 남아 있으면 FAIL**(cc4b9bd 회귀 탐지).
  2. `pathb_plan`: pathb_path_run_id 존재 + 해당 run이 터미널 상태 아님.
  3. `path_a_policy`: sl/tp/trail/loss_cap 중 실효 값 존재(placeholder −99%/+999%만 있고 다른 소유자도 없으면 `unprotected`).
  4. `micro_probe/recovery_micro`: 계약 필드로 소유 확인.
- **FAIL 조건**: ① 어느 소유자도 성립 안 함(`unprotected_position`) ② 격리 포지션에 일반 매도 플래그 잔존(`ownership_violation`) ③ 브로커에는 있는데 내부 기록 없는 포지션(`orphan_position` — 기존 `_pathb_recoverable_*`가 PathB만 보므로 전 포지션으로 확장) — 단 기존 체크와 중복되는 세부는 기존 체크 이름을 detail로 참조만 하고 재판정하지 않는다.
- **등급**: live-start 블로커 아님(warn) — 첫 2주 관측 후 fail 승격 여부 운영자 판단.
- **테스트**: 합성 포지션 4종(정상 격리/플래그 잔존/무방비/고아) 유닛 4건.
- **예상 규모**: preflight +80~120줄, 테스트 1파일. 다른 파일 변경 없음.

### A-3. 자동화 불가 시나리오 → 문서 러닝북 (배선 0)

`docs/runbooks/live_regression_scenarios.md` 신설 — S-카탈로그를 우리 사고 이력에 매핑한 체크리스트. 각 행 = 시나리오 / 자동 체크 이름(있으면) / 수동 확인 절차(없으면). 수동 항목 예: 주문 정정 후 identity 추적(KIS 주문번호 승계), VI/거래정지 중 포지션 대응, 장중 재시작 직후 첫 청산의 exit_owner 로그 확인. 코드 없음 — 운영 문서.

---

## B. 프롬프트 신선도 메타데이터 (stage 1만)

### B-1. 축소 근거

OpenAlice 원형은 "신선도 확인 강제 + 못 본 것 선언 요구"의 두 부분. 후자(행동 지시문)는 우리 실증(문구 주입 → "공격적으로" 한 마디에 매수 0)과 충돌 위험 — **기각**. 전자(사실 메타데이터 제공)만 채택: 모델에게 지시하지 않고 데이터에 타임스탬프를 붙여주는 것은 순수 위생.

### B-2. 설계

- **위치**: `phase1_trainer/digest_builder.py`의 다이제스트에 고정 블록 1개(현재 신선도 관련 필드 0개 — grep 실측):
  ```
  [데이터 기준시각] 가격: {price_as_of} | 뉴스 최신: {news_age_min}분 전 | 분봉: {minute_coverage}
  ```
- **재계산 금지**: 값은 이미 계산되는 것 재사용 — selection freshness 메트릭(`_record_selection_freshness_metrics`), 뉴스 stale 카운트(`_enrich_selection_candidates_with_news`의 flagged/stale/weak), 가격 fetch 시각. 새 수집 없음.
- **배포 안전장치**: prompt_version 태그 증가(기존 필드) → 전후 비교 가능. 행동 지시문 0개 원칙을 코드 주석에 명시.
- **판정**: 5거래일 후 quick_exit/hold advisor 응답 분포(SELL 비율·사유 분포)가 전후 유의 변화 없으면 유지, 이상하면 즉시 롤백(블록 제거만).
- **예상 규모**: digest_builder +15~25줄.

---

## C. Registry 반증 조건 필드 (코덱스 P0에 병합 — 별도 배선 금지)

P0 immutable registry 스키마에 필드 2개 + 평가 훅 스펙만 기여한다. **새 파이프라인·새 잡 없음** — 등록은 P0 registry가, 평가는 기존 장후 outcome 잡이 소유.

- `registration_basis` (JSON): `{source_axis, rule_version, evidence: {…}}` — 예: `{source_axis: "high52_setup", rule_version: "v1", evidence: {from_52w_high_pct: -2.1}}`. 어느 축·어느 규칙 버전이 이 후보를 등록했는지 — 소스별 forward 분해의 키.
- `invalidation_conditions` (JSON 배열): **기계 판정 가능한 술어만** — 예: `[{type:"disclosure", value:"유상증자"}, {type:"price", value:"52주고가 이탈 -8%"}, {type:"rvol_below", value:1.0}]`. 재량 문장 금지.
- 평가: 기존 장후 outcome 수집 잡이 각 등록 후보의 invalidation 술어를 평가해 `invalidated_at`/`invalidated_by` 기록. 무효화된 후보의 이후 성과는 "축이 늦게 죽는가"의 forward 증거가 된다.
- 예상 규모: 스키마 필드 2개 + outcome 잡 내 평가 함수 1개. P0 착수 시 함께.

---

## 실행 순서·승인 요청

1. **A(출구 소유권 감사)** — 이번 주 사고 직결, 신규 배선 1개뿐. **구현 승인 요청.**
2. **A-3 러닝북 문서** — 무해, A와 함께 작성.
3. **B(신선도 블록)** — 프롬프트 변경 = 라이브 출구 행동에 영향 가능. **운영자 판단**(5일 관측·롤백 계약 포함).
4. **C(registry 필드)** — P0 착수 결정에 종속. P0 승인 시 스키마에 포함.

기각 확정(재론 불요): 인간 승인 게이트의 라이브 루프 도입, simulate 수치 차용, "못 본 것 선언" 행동 지시문, AGPL 코드 복사.
