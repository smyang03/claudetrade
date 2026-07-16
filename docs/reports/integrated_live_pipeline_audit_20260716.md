# 2026-07-16 통합 라이브 파이프라인 개선·감사 보고서

작성 시각: 2026-07-16 20:30 KST
범위: 7/16 전체 커밋, OpenAlice 참고 구조의 최소 채택, 후보 registry/outcome/consensus 연결, KR·US 시뮬레이션, 라이브 운영 점검

## 1. 최종 판정

- 오늘 변경은 주문 권한을 무분별하게 늘리지 않고 **라이브 전략의 소유권 경계, 후보 전수 원장, 양 시장 관측 가능성**을 강화했다.
- 새로 채택한 OpenAlice 참고 요소는 별도 스케줄러나 별도 DB를 만들지 않았다. 기존 preflight와 기존 candidate registry에 병합했다.
- 라이브 주문 경로는 기존 enforce 설정을 유지한다. 후보 consensus는 계속 `SHADOW_ONLY_NO_ORDER_AUTHORITY`이며 자동 승격되지 않는다.
- 전수 감사에서 신규 운영 결함 2건을 추가로 발견하고 봉합했다.
  1. preopen outcome JSONL이 매 cadence마다 과거 샘플 전체를 다시 저장해 O(n²)로 증가
  2. 동시 기동 프로세스가 고정 `.write_probe`를 경합해 runtime root가 사용자 홈으로 갈라질 수 있음
- 오늘 수정 파일 관련 테스트 340건이 전부 통과했고, live preflight는 `fail=0`이다.

## 2. 기존과 달라진 운영 구조

### 기존

```text
후보 수집
  -> mutable preopen state
  -> 반복 candidate audit
  -> outcome JSONL(과거 샘플 전체 반복 저장)
  -> 단일 consensus status 파일(KR/US가 서로 덮어씀)

브로커 포지션
  -> 로컬 포지션과 수량 비교
  -> "누가 이 포지션을 청산할 책임이 있는가"는 preflight 미검증

여러 프로세스 동시 시작
  -> 동일 .write_probe 경합
  -> 드물게 E:\code\claudetrade 대신 C:\Users\Unknown\.claudetrade 사용
```

### 개선 후

```text
후보 최초 발견
  -> 기존 candidate_registry_first에 immutable first snapshot
     - registration_basis
     - invalidation_conditions
  -> 기존 registry event/quote cadence
  -> preopen outcome append row는 현재 cadence 1개만 저장
  -> audit outcome label
  -> 서로소 consensus shadow
  -> 공통 status + KR/US별 status
  -> preflight에서 양 시장 각각 생존 확인

브로커 포지션
  -> broker truth ↔ local position
  -> source/plan/micro contract로 exit owner 판정
  -> unprotected/orphan/ghost/quantity mismatch/ownership violation 탐지

라이브 스택 시작
  -> CLAUDETRADE_RUNTIME_DIR를 workspace로 명시
  -> 프로세스별 고유 writable probe
  -> runtime data가 다른 root로 갈라지는 경로 차단
```

## 3. 구현 내용

### 3.1 출구 소유권 감사

- `risk_manager.py`의 격리 전략 판정을 공용 순수 함수로 정리했다.
- `trading_bot.py`와 preflight가 동일한 격리 소스/일반 출구 필드 계약을 사용한다.
- 신규 preflight 체크 `position.exit_ownership_reconciliation`은 read-only다.
- FAIL 대상:
  - 브로커에만 존재하는 고아 포지션
  - 로컬에만 존재하는 ghost 포지션
  - 브로커/로컬 수량 불일치
  - 출구 계약이 없는 로컬 포지션
  - 격리 전략 포지션에 일반 intraday sell 플래그가 붙은 소유권 위반
- WARN 대상:
  - broker truth가 없거나 stale
  - 소스 메타데이터가 없어 제한적으로 소유자를 추론한 경우
- 주문, 매도, 포지션 수정 권한은 없다.

### 3.2 candidate registry에 반증 계약 병합

- 새 테이블이나 별도 registry를 만들지 않았다.
- 기존 `candidate_registry_first.first_snapshot_json`에 bounded 필드만 추가했다.
  - `registration_basis`
  - `invalidation_conditions`
- 자동 enforce는 명시적으로 `false`다.
- 소비자가 없는 임의 텍스트를 무제한 저장하지 않고 allowlist/typed 값으로 정규화한다.

### 3.3 outcome 로그 증가 봉합

- mutable preopen state는 재시작 복구와 대시보드를 위해 전체 `outcome_samples`를 계속 보존한다.
- append-only JSONL은 해당 cadence 샘플 1개만 기록한다.
- `outcome_sample_count`로 누적 개수를 별도 노출한다.
- 기존 dashboard는 각 append row의 `offset_min`을 병합하므로 전체 timeline 기능이 유지된다.
- 과거 파일을 삭제하거나 재작성하지 않았다.

### 3.4 KR/US consensus 상태 분리

- scorer/reviewer 프로세스는 기존처럼 한 번만 실행된다.
- 동일 결과를 아래에 원자적으로 기록한다.
  - 공통 호환 파일
  - `candidate_consensus_*_status_KR.json`
  - `candidate_consensus_*_status_US.json`
- preflight가 양 시장을 각각 점검하므로 한 시장의 정상 상태가 다른 시장의 사망을 숨기지 못한다.
- ledger와 모델 로직은 변경하지 않았다.

### 3.5 runtime root 경합 봉합

- 기존 writable 검사는 모든 프로세스가 동일 `.write_probe`를 사용했다.
- 동시 시작 시 한 프로세스가 다른 프로세스의 probe를 먼저 삭제하면 workspace를 쓰기 불가로 오판하고 사용자 홈으로 폴백할 수 있었다.
- 실제 감사 중 KR outcome 프로세스 1건이 `C:\Users\Unknown\.claudetrade`로 잘못 갈라지는 것을 재현했다.
- 수정:
  - PID+UUID 기반 고유 probe 사용
  - headless launcher가 `CLAUDETRADE_RUNTIME_DIR=$Root`를 자식 프로세스에 명시
- 사용자 홈의 과거 runtime 파일은 오염 위험 때문에 자동 병합·삭제하지 않았다. 현재 해당 홈 경로 heartbeat PID들은 살아 있지 않다.

## 4. 데이터 무결성 감사

### candidate audit DB

| 항목 | 실측 |
|---|---:|
| DB 크기 | 5,360,340,992 bytes |
| `audit_candidate_rows` | 227,653 |
| `audit_candidate_outcomes` | 825,714 |
| outcome `(candidate_key, horizon)` 중복 | 0 |
| `candidate_registry_first` | 60 |
| `candidate_registry_events` | 360 |
| `candidate_registry_quotes` | 360 |
| registry key 중복 | 0 |
| first snapshot 평균/최대 | 779.75 / 796 bytes |

7/16 registry는 60종목 × 6 cadence로 정확히 bounded되어 있다. 후보 payload 전체를 registry에 반복 복사하는 누수는 없다.

### preopen outcome 저장

| 항목 | 기존 | compact 모의 계산 |
|---|---:|---:|
| 7/16 KR 파일 | 65,356,180 bytes | 12,358,738 bytes |
| 감소율 | - | 81.09% |
| 샘플 누적 평균/최대 | 20.87 / 78 | 상태 파일에서 유지 |

- outcome 로그 누계: 2.448GB
- 6/15 이후 누계: 2.357GB
- 이번 수정은 향후 증가를 막는다. 과거 2.448GB 회수는 라이브 정지 후 별도 maintenance 작업으로 분리한다.

### 수집→라벨 연결

- 7/16 KR 30/60분 outcome 중 3,218행이 `preopen_outcome_jsonl`을 실제 observation source로 사용했다.
- 즉 `preopen 수집 → JSONL → candidate audit label → consensus outcome review` 연결은 살아 있다.
- `audit_sparse`는 연결 실패가 아니라 5분 cadence 기반 관측 품질 라벨이다.

## 5. KR·US 시뮬레이션

### 5.1 후보 path 검증 재실행

동일 입력과 비용 계약으로 validation을 다시 실행했다.

| 시장/arm | 7월 전체 후보 net | top-3 net | 판정 |
|---|---:|---:|---|
| US 단일 모델 | -0.6953% | -0.8310% | 7월에는 확대 금지 |
| KR system-score 단일 모델 | -0.0920% | +1.1054% (n=36) | 유망하지만 소표본 |
| KR Claude-only 단일 모델 | -0.0920% | +0.4975% (n=36) | LCB 음수 |

단일 모델의 국면 반전 때문에 consensus+ABSTAIN 구조가 필요하다는 진단은 유지된다.

### 5.2 서로소 consensus replay

현재 라이브와 같은 top-3 intersection 계약을 7월 세션에 재생했다.

| 시장 | 후보 records | 선택 | 라벨 존재 | 선택 net | 해석 |
|---|---:|---:|---:|---:|---|
| US | 755 | 0 | 0 | n/a | 전부 기권; 손실 풀 진입은 막지만 수익 증거도 없음 |
| KR | 955 | 3 | 2 | +0.34% | 양수이나 n=2, 승격 불가 |

- US에서 0건은 버그가 아니다. 서로소 두 arm의 top-3가 한 번도 겹치지 않은 결과다.
- KR 선택 티커는 `067290`, `065170`, `033340`이며 특정 티커 반복은 없었다.
- 현재 계약은 계속 shadow여야 한다. top-N 확대나 rank-sum 대체는 새 사전등록 arm으로만 검토한다.

### 5.3 현재 세션 outcome

- KR 7/16:
  - consensus 선택 0
  - ABSTAIN 30분 평균 -0.8194%, 60분 평균 -0.9061%
  - 오늘은 기권이 손실 후보 진입을 피한 형태
- US 7/15:
  - consensus 선택 0
  - ABSTAIN 30분 평균 +0.2050%, 60분 평균 +0.7560%
  - 기권이 양수 세션도 놓칠 수 있음을 보여줌

따라서 consensus는 “항상 수익을 높인다”가 아니라 “불확실할 때 거래하지 않는 정밀도 실험”이다. prospective 표본 없이는 enforce할 수 없다.

### 5.4 Yahoo Finance 교차검증

Yahoo 5분봉은 보조 데이터로만 사용했다. 내부 KIS/audit 관측가가 동일 Yahoo 5분봉 고가-저가 범위 안에 있는지 확인했다.

- US: Yahoo가 제공한 4종목(SPCX, LQDA, MDA, TDC) 4/4 범위 내
- KR: Yahoo가 제공한 5종목(065170, 083470, 215790, 218150, 373200) 5/5 범위 내
- Yahoo 미제공: SKHYV, 062970.KQ

결론: 표본 가격의 큰 단위·타임존·티커 매핑 오류는 발견되지 않았다. Yahoo coverage가 100%가 아니므로 라이브 authority로 승격하지 않고 보조 검증만 유지한다.

## 6. 라이브 권한과 동작 방식

| 레인 | 현재 권한 | 주문 영향 |
|---|---|---|
| Profit strategy core | micro enforce | 기존 설정대로 주문 가능 |
| US swing | operator micro trial | 기존 0.1x/1일 1건/1슬롯 한도 |
| KR PathB split-runner | live enforce | 기존 정책 유지 |
| Tail capture/carry | live enforce | 기존 hard-stop/loss-cap 유지 |
| Candidate immutable registry | observation | 주문 영향 없음 |
| Disjoint consensus | shadow only | 주문 영향 없음 |
| Exit ownership preflight | read-only audit | 주문/청산 영향 없음 |
| Yahoo cross-check | offline/sub data | 주문 영향 없음 |

이번 보완은 라이브 전략의 매수·매도 임계값을 바꾸지 않았다.

## 7. 검증 결과

- 오늘 수정 관련 전체 테스트: `340 passed`
- compile: 통과
- `git diff --check`: 통과
- live preflight:
  - `ok=true`
  - `fail=0`
  - consensus KR/US market-specific status: PASS
  - 신규 주문 오류/Traceback: 없음
- 봇 런타임:
  - 18:26 기동 후 생존
  - RSS 약 220MB
  - 스레드 6
  - 포지션 0

## 8. 남은 경고의 성격

### 현재 주문 차단 버그가 아닌 것

- broker truth stale: KR 종료·US preopen refresh window 이전이라 발생. US 주문창 전 scheduler refresh가 필요하다.
- PathB previous-session active 3건 및 lifecycle evidence 경고: 과거 원장 정합성 문제. 현재 브로커/로컬 포지션과 주문은 0이다.
- `state/brain.json` dirty: 런타임 메모리 변경으로 사용자가 소유한 상태이며 자동 되돌리지 않았다.
- 과거 CSV flat/zero-volume: 최신 active blocking issue는 0.

### 별도 후속 가치가 있는 것

1. 과거 preopen outcome 2.448GB compact/archive는 라이브 정지 maintenance로 분리
2. PathB 과거 stale/lifecycle 3~4건은 DB 백업 후 원장 repair로 분리
3. `fact_forward_outcome`의 오래된 pending/stale 라벨은 일일 backlog job 감사 대상
4. consensus는 US 0건, KR n=2이므로 최소 30 prospective 세션 전 enforce 금지

## 9. 운영 결론

- 오늘 커밋의 라이브 전략 동작은 유지되고, 신규 변경은 관측·복구·무결성에만 권한을 갖는다.
- 후보 데이터 누락을 판단할 수 있는 전 후보 registry와 outcome 경로가 연결됐다.
- 저장 증가와 runtime-root 분할이라는 실제 누수/연결 결함은 봉합됐다.
- 양 시장 price 교차검증에서 큰 가격 오류는 없었다.
- consensus는 수익 전략으로 확정된 것이 아니라, 거래 기근과 독립적으로 정밀도를 측정하는 prospective shadow 실험이다.
- 다음 수익성 판단은 누적된 신규 세션에서 `SELECT_SHADOW`의 net, 동일 티커 제거 후 부호, block LCB가 모두 양수인지로 결정한다.
