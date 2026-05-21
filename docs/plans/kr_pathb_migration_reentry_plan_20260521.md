# KR PathB 전환 및 리엔트리 쿨다운 단축 계획

작성일: 2026-05-21

---

## 배경

오늘(2026-05-21) KR PathA 전면 매수 차단 원인 분석 결과:
- `kr_data_quality_not_confirmed` (9:08) → 전 종목 shadow 강등
- 125020이 9:09 buy zone(8,000~8,500) 진입 후 +25.1% 상승했으나 미진입
- US PathB는 zone hit 기반으로 더 민첩하게 대응

→ KR도 US PathB 방식(Claude 가격 플랜 zone hit 기반 진입)으로 전환하는 방향 검토

---

## 현재 KR PathB 상태

```
PATHB_KR_LIVE_ENABLED=true       # PathB 인프라 ON
KR_CLAUDE_PRICE_LIVE_ENABLED=false  # Claude 가격 플랜 실행은 OFF (shadow만)
KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK=false
```

PathB 인프라는 이미 켜진 상태. shadow 관찰만 하고 실제 주문은 PathA(전략 신호)로만 나가고 있음.

---

## 전환 시 변경되는 것

| 항목 | 현재 KR PathA | 전환 후 KR PathB |
|---|---|---|
| 진입 트리거 | momentum 등 전략 신호 | Claude 가격 zone hit |
| Confirmation gate | FAST_TRIGGER_WITH_HARD_VETO | PathB 슬리피지 캡(1.003)으로 대체 |
| 데이터 품질 차단 | kr_data_quality_not_confirmed 시 전체 차단 | zone hit 시 슬리피지 캡 내 실행 |
| 오늘 케이스 | 125020 9:09 진입 차단 | zone hit 시 바로 주문 시도 |

---

## 전환 전 사전 조건

### 필수 (전환 전 반드시 해결)

1. **Bug 2 수정** — KR live+shadow 동시 상태에서 `SHADOW_WAITING` 미등록 버그
   - 현재 오전 shadow 기록이 이 버그로 누락됨
   - 실행 판단 근거가 없는 상태에서 live 전환 불가
   - 관련 파일: `runtime/pathb_runtime.py` → `register_from_selection_meta()`
   - live batch와 shadow batch 분리 등록 필요

2. **KR shadow 관찰 기간** — KR PathB shadow는 2026-05-21부터 본격 시작
   - US는 shadow 기간이 충분했음
   - 최소 3~5일 shadow hit rate, 슬리피지 실측값 확인 필요

### 권고 (위험 완화)

3. `KR_CLAUDE_PRICE_LIVE_ENABLED=true` 전환 전:
   - shadow zone hit rate 확인
   - 실제 슬리피지 분포 확인 (KOSDAQ 호가 스프레드 큼)
4. KR 슬리피지 캡 1.003 적정성 재검토

---

## 위험도 평가

| 항목 | 위험도 | 근거 |
|---|---|---|
| PathB 코드 구조 | 낮음 | KR에 이미 구축돼 있어 코드 변경 폭 작음 |
| Bug 2 미수정 상태 전환 | 높음 | shadow 기록 없어 성과 추적 불가 |
| Confirmation gate 제거 | 중간 | 오전 변동성 구간 불리한 체결 위험 |

---

## 리엔트리 쿨다운 단축 검토

현재: `KR_REENTRY_COOLDOWN_MINUTES=120`  
US PathB: `US_REENTRY_COOLDOWN_MINUTES=90`

| 옵션 | 효과 | 위험 |
|---|---|---|
| 120 → 90분 (US와 동일) | 하루 2~3건 추가 기회 | 낮음. 가장 보수적 변경 |
| 120 → 60분 | 더 적극적 리엔트리 | 같은 종목 반복 손실 가능성 |
| 120 → 30분 | 거의 제한 없음 | 과매매 위험 |

**권고**: 90분 먼저 적용 후 관찰. 설정 변경만으로 가능, 위험도 낮음.

---

## 권고 실행 순서

| 단계 | 작업 | 우선순위 |
|---|---|---|
| 1 | Bug 2 수정 — KR shadow SHADOW_WAITING 정상 등록 | P0 |
| 2 | KR 리엔트리 쿨다운 120→90분 적용 | 즉시 가능 |
| 3 | 3~5일 shadow 관찰 — zone hit rate, 슬리피지 실측 | 관찰 기간 |
| 4 | 확인 후 `KR_CLAUDE_PRICE_LIVE_ENABLED=true` 전환 | 관찰 완료 후 |

---

## 설정 변경 대상 파일

리엔트리 쿨다운 단축 시 수정 필요:
- `config/v2_start_config.json` — `KR_REENTRY_COOLDOWN_MINUTES` 값
- `.env.live` — 동일 키 존재 여부 확인 후 동기화

`KR_CLAUDE_PRICE_LIVE_ENABLED` 전환 시 수정 필요:
- `config/v2_start_config.json`
- `.env.live`
- **운영자 확인 필수** (CLAUDE.md 운영자 확인 필수 설정값 참조)

---

## 관련 이슈

- Bug 2 (PathB shadow 미등록): `docs/CODE_REVIEW_IMPROVEMENT_REQUIREMENTS_20260521.md`
- pathb_sell_shadow R2: `docs/reports/pathb_sell_shadow_code_requirements_20260521.md`
- KR PathB 오늘 분석: `docs/reports/` (20260521 forensic)
