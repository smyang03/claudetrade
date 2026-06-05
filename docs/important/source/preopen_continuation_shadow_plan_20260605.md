# US 장전 연속상승 후보 Shadow 평가 플랜

작성일: 2026-06-05  
대상: US 장전 후보 중 정규장 추가 상승 가능성이 있는 후보군의 Claude 판단 품질 검증  
상태: 구현 완료 / shadow-only 운영 테스트 준비  

## 1. 목적

US 장전 후보 중 장전에서 이미 상승했고 정규장에서도 추가 상승하는 후보군이 존재하는지 검토했고, 이 후보군을 실제 매수 경로에 직접 연결하지 않고 Claude 후보 판단 품질을 1주일 이상 관찰하기 위한 shadow-only 구조를 설계한다.

핵심 목적은 다음과 같다.

- 장전 연속상승 후보군이 실제로 유의미한 후보 공급원이 될 수 있는지 검증한다.
- Claude가 장전/초반 정보만 보고 PROMOTE / KEEP / DROP 판단을 얼마나 잘 분리하는지 기록한다.
- 실제 selection, PathB, 주문, 리스크 경로에는 영향을 주지 않는다.
- 1주일 이상 운영 후 DISCOVERY/WATCH 후보군 편입 여부를 결정할 수 있는 근거 데이터를 만든다.

## 2. 비목표

이번 설계는 매수 전략 구현이 아니다.

- 자동매수 없음
- PathB wait 등록 없음
- PlanA trade_ready 승격 없음
- 주문 수량/예산/리스크 정책 변경 없음
- `state/brain.json` 자동 학습/정책 승격 없음
- 기존 PathB 수익 엔진, hold advisor, broker truth, cooldown guard 변경 없음

## 3. 검토한 내용

### 3.1 데이터 소스

검토에는 아래 로컬 데이터를 사용했다.

- `state/preopen_US_*.json`
- `logs/preopen/*_US_candidates.jsonl`
- `logs/preopen/*_US_outcome.jsonl`
- `data/ticker_selection_log.db`
- `data/audit/candidate_audit.db`
- 기존 Claude raw call 로그: `logs/raw_calls/`

완료 세션 통계는 주로 `2026-05-04`부터 `2026-06-03`까지의 US 정규장 outcome을 사용했다. `2026-06-04` 세션은 당시 진행 중이었으므로 90분 partial outcome으로만 별도 참고했다.

### 3.2 장전 후보 성과 검토

장전 상승 후보군에는 실제 edge가 관찰됐다.

완료 세션 기준 주요 결과:

| 구분 | 후보 수 | 정규장 종가 수익률 평균 | 승률 | Profit Factor |
|---|---:|---:|---:|---:|
| 전체 후보 | 1,170 | +0.32% | 49.6% | 1.22 |
| 장전 상승 후보 | 707 | +0.60% | 53.0% | 1.42 |
| 장전 상승 + 뉴스/이벤트 | 344 | +1.00% | 58.7% | 1.82 |
| 장전 상승 + 5분 후에도 상승 | 288 | +2.15% | 67.7% | 4.14 |
| gap 2~20% + 거래대금 100M+ + rank<=20 | 338 | +0.65% | 53.3% | 1.39 |
| 위 조건 + 5분 확인 후 5분가 진입 | 130 | +1.02% | 61.5% | 1.84 |

해석:

- 장전 상승 후보군 자체는 시장 평균보다 낫다.
- 다만 장전 정보만으로 매수하면 초반 역회전과 테마 동반 하락 리스크가 크다.
- `gap > 20%` 구간은 성과가 나빠 기본 제외가 타당하다.
- `gap 2~20%`, `extended_dollar_volume >= 50M~100M`, `rank <= 20~40`, 뉴스/이벤트는 후보군 조건으로 유효하다.

### 3.3 리스크 검토

장전 상승 후보는 평균 MFE도 크지만 MAE도 크다.

| 항목 | 값 |
|---|---:|
| 장전 상승 후보 평균 MFE | +3.70% |
| 장전 상승 후보 평균 MAE | -3.15% |
| MAE 중앙값 | -2.48% |
| 하위 10% MAE | -6.81% |

단순 `-2%` 손절은 전략 기대값을 크게 훼손했다. 따라서 이 후보군은 즉시 매수 전략이 아니라 후보 공급/관찰 전략으로 다뤄야 한다.

### 3.4 기존 selection 포착률 검토

장전 상승 후 정규장에서 `MFE +3% 이상`이고 종가도 플러스였던 후보 275개 중 기존 selection이 포착한 것은 약 51.3%였다.

해석:

- 현재 시스템도 일부 승자를 보고 있지만 놓치는 후보가 많다.
- 별도 후보 bucket을 통해 Claude가 더 많은 연속상승 후보를 검토하게 만들 가치는 있다.
- 단, 기존 selection/PathB에 바로 섞으면 주문 경로가 열릴 수 있으므로 shadow-first가 필요하다.

### 3.5 Claude 블라인드 평가 테스트

정답 라벨과 미래 수익률을 숨기고 Claude에게 당시 정보만 제공하여 PROMOTE / KEEP / DROP을 판단시켰다.

테스트 조건:

- preopen only: 장전 정보만 제공
- 5m: 장전 정보 + 개장 후 5분 정보 제공
- 30m: 장전 정보 + 개장 후 30분 정보 제공

요약 결과:

| 테스트 | 정보 시점 | PROMOTE 성과 | 판단 |
|---|---|---:|---|
| preopen only | 장전 정보만 | 평균 -0.61%, 승률 42.9% | PROMOTE 신뢰 낮음 |
| 5m blind | 장전 + 개장 5분 | PROMOTE -0.53%, KEEP +2.72%, DROP -1.59% | DROP은 유효, PROMOTE/KEEP 구분 약함 |
| 30m blind | 장전 + 개장 30분 | PROMOTE +9.55%, 승률 100% | 분리력 있음 |

추가 관찰:

- 5분 조건에서 Claude가 JSON contract를 깨고 설명문을 길게 쓰는 사례가 있었다.
- 운영 반영 시 compact schema 또는 강한 JSON enforcement가 필요하다.
- 초반에는 Claude를 적극 선별자라기보다 DROP/veto 필터로 보는 것이 맞다.
- 30분 이후 PROMOTE는 우선순위 상승 신호로 사용할 가능성이 있다.

## 4. 설계 결론

장전 연속상승 후보군은 후보 공급 전략으로 가능성이 있다. 하지만 자동매수 또는 PathB 직접 연결은 아직 금지한다.

초기 설계 방향:

```text
장전 후보 수집
-> deterministic filter
-> 별도 shadow DB 저장
-> 개장 30분 1회 Claude 블라인드 판단
-> PROMOTE / KEEP / DROP 저장
-> 이후 60m / 120m / 종가 / MFE / MAE outcome 저장
-> selection_meta / PathB / 주문 영향 없음
```

최초 1주일은 실제 후보군 편입도 하지 않고 DB 기록만 한다.

## 5. 기능 요구사항

### 5.1 Shadow DB

신규 DB:

```text
data/preopen_continuation.db
```

이 DB는 운영 truth가 아니며, 후보 품질 실험/리포트용 shadow DB다.

### 5.2 테이블: `preopen_candidates`

장전 후보 snapshot을 저장한다.

필수 컬럼:

| 컬럼 | 설명 |
|---|---|
| id | PK |
| session_date | US 세션 날짜 |
| market | `US` |
| ticker | 티커 |
| name | 종목명 |
| source | `day_gainers`, `most_actives` 등 |
| preopen_rank | `shadow_preopen_rank` |
| provider_rank | provider rank |
| gap_pct | 장전 상승률 |
| extended_price | 장전 가격 |
| extended_volume | 장전 거래량 |
| extended_dollar_volume | 장전 달러 거래대금 |
| news_or_earnings_flag | 뉴스/실적 flag |
| news_sample_title | 뉴스 샘플 제목 |
| preopen_reason_json | preopen reason 배열 |
| quality_tags_json | quality tags |
| risk_tags_json | risk tags |
| eligible | deterministic filter 통과 여부 |
| deterministic_score | 룰 기반 후보 점수 |
| exclusion_reason | 제외 사유 |
| captured_at | 수집 시각 |
| created_at | DB 기록 시각 |

유니크 키:

```text
session_date, market, ticker
```

### 5.3 테이블: `preopen_feature_snapshots`

개장 후 특정 offset의 관측값을 저장한다.

필수 컬럼:

| 컬럼 | 설명 |
|---|---|
| id | PK |
| session_date | 세션 날짜 |
| market | `US` |
| ticker | 티커 |
| offset_min | 5, 30, 60, 120, close |
| regular_open_price | 정규장 시가 |
| observed_price | offset 가격 |
| return_from_open_pct | 시가 대비 수익률 |
| mfe_from_open_pct | 시가부터 offset_min까지의 rolling 최고 수익률 |
| mae_from_open_pct | 시가부터 offset_min까지의 rolling 최저 수익률 |
| volume | 관측 거래량 |
| price_source | 가격 소스 |
| captured_at | 관측 시각 |

유니크 키:

```text
session_date, market, ticker, offset_min
```

### 5.4 테이블: `preopen_claude_checks`

Claude 평가 batch 단위를 저장한다.

필수 컬럼:

| 컬럼 | 설명 |
|---|---|
| id | PK |
| session_date | 세션 날짜 |
| market | `US` |
| eval_offset_min | 기본 30 |
| prompt_version | 프롬프트 버전 |
| model | Claude 모델 |
| candidate_count | 평가 후보 수 |
| fingerprint | 후보/feature fingerprint |
| smart_skip | smart skip 여부 |
| skip_reason | skip 사유 |
| raw_call_path | raw call JSON 경로 |
| input_tokens | input tokens |
| output_tokens | output tokens |
| parse_ok | JSON parse 성공 여부 |
| parse_error | parse error |
| created_at | 생성 시각 |

유니크 키:

```text
session_date, market, eval_offset_min, fingerprint
```

### 5.5 테이블: `preopen_claude_decisions`

Claude의 후보별 판단을 저장한다.

필수 컬럼:

| 컬럼 | 설명 |
|---|---|
| id | PK |
| check_id | `preopen_claude_checks.id` |
| session_date | 세션 날짜 |
| market | `US` |
| ticker | 티커 |
| visible_offset_min | 평가에 사용한 정보 시점 |
| decision | `PROMOTE`, `KEEP`, `DROP` |
| confidence | 0.0~1.0 |
| reason_code | 짧은 reason |
| action_ceiling | 항상 `WATCH` |
| would_inject_candidate_pool | 실험상 후보군 편입 대상 여부 |
| actually_injected | 초기에는 항상 false |
| created_at | 생성 시각 |

초기 정책:

| Claude decision | shadow 해석 |
|---|---|
| PROMOTE | 후보군 편입 후보로 기록 |
| KEEP | 후보군 유지 가능성으로 기록 |
| DROP | 특수 bucket 제외 후보로 기록 |

단, 초기 1주일은 `actually_injected=false` 고정이다.

### 5.6 테이블: `preopen_outcomes`

후행 outcome을 저장한다.

필수 컬럼:

| 컬럼 | 설명 |
|---|---|
| id | PK (autoincrement) |
| session_date | 세션 날짜 |
| market | `US` |
| ticker | 티커 |
| ret_5m | 시가 대비 5분 수익률 |
| ret_30m | 시가 대비 30분 수익률 |
| ret_60m | 시가 대비 60분 수익률 |
| ret_120m | 시가 대비 120분 수익률 |
| ret_close | 시가 대비 종가 수익률 |
| mfe | 시가 이후 최대 상승률 |
| mae | 시가 이후 최대 하락률 |
| selected_by_live_claude | 기존 live selection에 포함 여부 |
| live_trade_ready | 기존 live trade_ready 여부 |
| ordered | 실제 주문 여부 |
| updated_at | 갱신 시각 |

## 6. Deterministic 후보 조건

초기 eligible 조건:

```text
market = US
extended_change_pct > 0
2% <= extended_change_pct <= 20%
extended_dollar_volume >= 50,000,000
shadow_preopen_rank <= 40
regular_open_price available
```

우선순위 점수의 초기 가이드:

| 항목 | 가점/감점 |
|---|---|
| gap 2~10% | 강한 가점 |
| gap 10~20% | 중간 가점 |
| gap >20% | 제외 또는 강한 감점 |
| dollar volume >= 300M | 강한 가점 |
| dollar volume 100M~300M | 중간 가점 |
| dollar volume 50M~100M | 약한 가점 |
| rank <=10 | 강한 가점 |
| rank <=20 | 중간 가점 |
| news_or_earnings_flag | 가점 |

초기 후보 수:

```text
PREOPEN_CONTINUATION_MAX_CANDIDATES=15
```

## 7. 실행 타이밍 및 트리거 방법

`tools/preopen_continuation_shadow.py`를 독립 스크립트로 분리한다. trading_bot과 독립 실행하여 runtime 영향을 원천 차단한다. `start_live_stack.bat`에 별도 탭으로 추가하거나 preopen_scheduler 완료 후 hook으로 호출한다.

| 단계 | 실행 시각 (KST) | 설명 |
|---|---|---|
| 장전 후보 수집 | 22:00~22:20 | preopen_scheduler 완료 후 즉시 실행 |
| 5분 feature 저장 | 22:35 | 개장 5분 후 |
| 30분 feature + Claude | 23:00 | 개장 30분 후, 하루 1회 Claude 호출 |
| 60분 feature | 23:30 | outcome 저장만 |
| 120분 feature | 00:30 | outcome 저장만 |
| 종가 feature | 05:10 | 세션 종료 후 |

실행 명령 예:

```bash
python tools/preopen_continuation_shadow.py --market US --step collect
python tools/preopen_continuation_shadow.py --market US --step feature --offset 5
python tools/preopen_continuation_shadow.py --market US --step eval --offset 30
python tools/preopen_continuation_shadow.py --market US --step feature --offset 60
```

## 8. Claude 운영 정책

### 8.1 호출 시점

초기 1주일 운영 기준:

| 시점 | Claude 호출 | 설명 |
|---|---:|---|
| 장전 후보 수집 | 0회 | deterministic 저장만 |
| 개장 5분 | 0회 | feature 저장만 |
| 개장 30분 | 1회 | Claude 블라인드 판단 |
| 60분/120분/종가 | 0회 | outcome 저장만 |
| parse 실패 | 최대 1회 재시도 | compact JSON만 재요청 |

기본 호출량:

```text
US 하루 1회
parse 실패 포함 하루 최대 2회
```

사용 모델:

```text
claude-sonnet-4-6 (기본)
```

오류 처리 구분:

| 오류 유형 | 처리 |
|---|---|
| JSON parse 실패 | compact retry 1회 허용 |
| rate limit (EGW00133 등) | retry 없음, skip 기록 후 다음 세션 |
| network / API 오류 | retry 없음, skip 기록 |
| timeout | retry 없음, skip 기록 |

parse 실패 retry는 parse 실패에만 적용한다. rate limit·network·timeout은 하루 할당량 소진 없이 `smart_skip=true`, `skip_reason=api_error`로 기록한다.

### 8.2 프롬프트 계약

프롬프트에는 미래 outcome을 넣지 않는다.

제공 가능 정보:

- 장전 rank
- gap
- extended dollar volume
- source
- 뉴스/실적 flag
- 뉴스 샘플 제목
- preopen reason
- risk/quality tags
- 정규장 시가
- 30분 기준 수익률/MFE/MAE/volume

제공 금지 정보:

- 60분 이후 결과
- 120분 결과
- 종가
- 최종 MFE/MAE
- winner/fader 라벨
- 기존 분석에서 도출한 정답 힌트

응답 형식:

```json
{
  "cases": [
    ["C01", "PROMOTE", 0.72, "STRONG_30M_CONTINUATION"],
    ["C02", "DROP", 0.40, "OPENING_FADE"]
  ]
}
```

필수 제약:

- Markdown 금지
- 설명문 금지
- JSON 외 텍스트 금지
- reason은 짧은 machine code
- decision은 `PROMOTE`, `KEEP`, `DROP`만 허용

### 8.3 Smart Skip

Claude 호출 전 fingerprint를 계산한다.

fingerprint 구성 요소:

```text
session_date
market
eval_offset_min
eligible ticker list
ticker
gap bucket
dollar volume bucket
rank bucket
news flag
r5 direction/bucket
r30 direction/bucket
mfe30 bucket
mae30 bucket
risk tags
```

스킵 조건:

- eligible 후보가 0개
- 같은 `session_date / market / eval_offset_min / fingerprint` 평가가 이미 있음
- 직전 평가 이후 신규 후보 없음
- 후보별 feature 변화가 bucket 기준으로 동일
- 30분 feature가 아직 충분히 수집되지 않음

스킵 시에도 `preopen_claude_checks`에 `smart_skip=true`, `skip_reason`을 남긴다.

## 9. Runtime 안전 요구사항

초기 상태에서는 실제 trading runtime에 영향을 주지 않는다.

필수 env/config 기본값:

```text
PREOPEN_CONTINUATION_SHADOW_ENABLED=true
PREOPEN_CONTINUATION_CLAUDE_EVAL_ENABLED=true
PREOPEN_CONTINUATION_INJECT_DISCOVERY=false
PREOPEN_CONTINUATION_MAX_CANDIDATES=15
PREOPEN_CONTINUATION_EVAL_OFFSET_MIN=30
PREOPEN_CONTINUATION_CLAUDE_MAX_CALLS_PER_DAY=1
PREOPEN_CONTINUATION_CLAUDE_RETRY_MAX=1
```

실제 후보군 편입을 켜는 경우에도 아래를 유지한다.

```text
candidate_pool_role=DISCOVERY
discovery_action_ceiling=WATCH
DISCOVERY_ALLOW_BUY_READY=false
DISCOVERY_ALLOW_PROBE_READY=false
DISCOVERY_ALLOW_PULLBACK_WAIT=false
```

금지 사항:

- `_pathb_wait_tickers`에 추가 금지
- `trade_ready`에 추가 금지
- `price_targets`를 live 실행용으로 전달 금지
- PathB plan registration 금지
- 주문/리스크/브로커 truth 경로 변경 금지

## 10. 리포트 요구사항

1주일 shadow 운영 후 아래 리포트를 생성한다.

경로 예:

```text
docs/reports/preopen_continuation_shadow_report_YYYYMMDD.md
```

리포트 항목:

- 운영 기간
- 총 후보 수
- eligible 후보 수
- Claude 호출 수 / skip 수 / parse 실패 수
- PROMOTE / KEEP / DROP별 후보 수
- decision별 ret_60m / ret_120m / ret_close / MFE / MAE
- PROMOTE 대비 KEEP/DROP 성과 차이
- DROP 제외 필터로서의 유효성
- KEEP 안의 대형 승자 비율
- 기존 live selection이 놓친 PROMOTE/KEEP 승자 수
- 실제 주문과의 충돌 여부
- 다음 단계 추천

핵심 판단 지표:

| 지표 | 통과 기준 초안 |
|---|---|
| DROP 평균 ret_close | PROMOTE/KEEP보다 낮아야 함 |
| DROP bad 비율 | 60% 이상 |
| PROMOTE 평균 MFE | KEEP/DROP보다 높아야 함 |
| PROMOTE 대형 실패율 | 과도하게 높으면 보류 |
| KEEP 대형 승자 비율 | 높으면 PROMOTE 단독 사용 금지 |
| parse success rate | 95% 이상 |

### 10.1 성과 비교 및 반영 판단 기준

이 실험의 목적은 자동매수가 아니라 후보군 품질 개선 여부를 판단하는 것이다. 따라서 리포트는 매수 성과를 직접 단정하지 않고, 아래 세 가지 결론 중 하나만 제시한다.

```text
shadow_continue
consider_discovery_watch
block_or_discard
```

평가 가능 최소 조건:

| 항목 | 최소 기준 |
|---|---|
| 운영 기간 | 7거래일 이상 |
| eligible 후보 수 | 50개 이상 |
| Claude 평가 후보 수 | 35개 이상 |
| PROMOTE 표본 | 8개 이상 |
| DROP 표본 | 8개 이상 |
| outcome complete 비율 | 90% 이상 |
| parse success rate | 95% 이상 |
| Claude 호출량 | US 하루 1회 원칙 유지 |

위 조건을 만족하지 못하면 결과가 좋아 보여도 `shadow_continue`로 둔다.

`consider_discovery_watch` 판단 조건:

| 판단 축 | 통과 조건 |
|---|---|
| 후보군 alpha | `PROMOTE+KEEP`이 `DROP`보다 ret_60m, ret_120m, ret_close 중 2개 이상에서 우위 |
| DROP 필터 유효성 | DROP bad 비율이 60% 이상이고, DROP 평균 MFE가 `PROMOTE+KEEP`보다 낮음 |
| PROMOTE 품질 | PROMOTE가 KEEP/DROP보다 MFE 또는 win rate에서 우위. 단, KEEP 안의 대형 승자가 많으면 PROMOTE 단독 사용 금지 |
| 리스크 호환성 | 5분 상승 subset의 MAE 중앙값이 현재 PathB stop 레벨과 충돌하지 않음. 충돌 시 후보 편입 보류 |
| 기존 후보 대비 추가성 | 기존 live selection이 놓친 승자 후보가 확인됨 |
| 운영 비용 | Smart Skip 포함 Claude 호출량이 기존 운영량을 밀어내지 않음 |
| 안전 경계 | 편입하더라도 `candidate_pool_role=DISCOVERY`, `discovery_action_ceiling=WATCH`까지만 허용 |

`shadow_continue` 판단 조건:

- 표본 수가 부족하거나 특정 하루 결과에 의존하는 경우
- PROMOTE와 KEEP/DROP 차이가 방향성은 있으나 통계적으로 약한 경우
- KEEP 안에 대형 승자가 많아 Claude의 PROMOTE/DROP 분리력이 애매한 경우
- 장전 anchor 기준 성과는 좋지만 정규장 open 기준 성과가 약한 경우
- Claude 호출량, parse 실패, source stale 문제가 운영일 일부에서 반복된 경우

`block_or_discard` 판단 조건:

- DROP이 PROMOTE/KEEP보다 같거나 더 좋은 성과를 반복해서 보이는 경우
- PROMOTE의 MAE/급락 빈도가 현 PathB stop 레벨과 명확히 충돌하는 경우
- 기존 live selection과 대부분 중복되어 추가 후보 가치가 없는 경우
- parse success rate가 95% 미만이거나 case id/raw decision 재현성이 떨어지는 경우
- Claude 호출량이 하루 1회 원칙을 지키지 못하거나 smart skip이 실효성이 없는 경우

최종 반영 단위:

1. 1차 반영 가능 범위는 `DISCOVERY/WATCH` 후보군 편입만이다.
2. `trade_ready`, `PULLBACK_WAIT`, `BUY_READY`, `PROBE_READY` 직접 승격은 이 요구서 범위에서 금지한다.
3. 실제 selection/PathB 연결이 필요해지면 별도 작업 설명, 보호 영역 영향 분석, 회귀 테스트 범위를 다시 승인받는다.

리포트 생성 방법:

```bash
python tools/preopen_continuation_shadow_report.py --market US --from YYYY-MM-DD --to YYYY-MM-DD
```

overnight monitor에 통합하지 않고 독립 실행한다. 1주일 shadow 종료 후 수동으로 생성하여 운영자가 확인한다.

## 11. 구현 플랜

### Phase 0: 문서화 및 요구사항 확정

산출물:

- 본 요구서
- 보호 영역/비목표 합의

상태:

- 완료

### Phase 1: Shadow DB 및 기록기 구현

작업:

- `data/preopen_continuation.db` schema 생성 유틸 추가
- 장전 후보 snapshot 저장
- 5분/30분/60분/120분/종가 feature snapshot 저장
- 기존 `state/preopen_US_*.json` 또는 preopen outcome updater와 읽기 전용 연계

검증:

```text
python -m py_compile tools/preopen_continuation_shadow.py
python tools/preopen_continuation_shadow.py --date 2026-06-04 --market US --dry-run
```

### Phase 2: Claude 30분 평가 + Smart Skip

작업:

- 30분 offset 기준 후보 15개 이하 compact prompt 구성
- fingerprint 계산
- smart skip 적용
- raw call 저장
- `preopen_claude_checks`, `preopen_claude_decisions` 저장
- parse 실패 시 compact retry 1회

검증:

```text
python tools/preopen_continuation_shadow.py --market US --eval-offset 30 --dry-run
python tools/preopen_continuation_shadow.py --market US --eval-offset 30 --no-claude
```

### Phase 3: Outcome backfill 및 daily report

작업:

- 기존 완료 세션 outcome backfill
- decision별 성과 리포트 생성
- parse 실패/skip/호출량 요약
- **5분 subset MAE 분포 별도 집계**: `eligible=true` 후보 중 개장 5분 상승 확인 subset의 MAE 분포를 분리해서 현재 PathB stop 레벨(-1.5~-2%)과 호환 여부 확인. MAE 중앙값이 -2% 이상이면 Phase 5 진입 시 PathB stop 정책 변경 필요 여부 재검토.

검증:

```text
python tools/preopen_continuation_shadow_report.py --market US --from YYYY-MM-DD --to YYYY-MM-DD
```

### Phase 4: 1주일 Shadow 운영

운영:

- `PREOPEN_CONTINUATION_INJECT_DISCOVERY=false`
- 하루 1회 Claude 평가
- 매일 리포트 또는 누적 리포트 확인

판단:

- DROP이 안정적으로 나쁜 후보를 제거하는지
- PROMOTE가 KEEP/DROP 대비 우월한지
- KEEP 안에 승자가 많아 PROMOTE 단독 사용이 위험한지

### Phase 5: DISCOVERY/WATCH 후보군 편입 검토

전제 조건:

- 1주일(7거래일) 이상 shadow 성과 확인
- eligible 후보 50개 이상, Claude 평가 후보 35개 이상, PROMOTE/DROP 각각 8개 이상 확보
- DROP 평균 ret_close가 PROMOTE/KEEP보다 낮음
- `PROMOTE+KEEP`이 `DROP`보다 ret_60m, ret_120m, ret_close 중 2개 이상에서 우위
- DROP bad 비율 60% 이상 또는 DROP 평균 MFE가 `PROMOTE+KEEP`보다 낮음
- 기존 live selection이 놓친 승자 후보가 확인됨
- parse 성공률 95% 이상
- Claude 호출량: 7일 × 1회 = 7회, 총 input tokens 70,000 이하
- 5분 subset MAE 분포 확인 완료 (Phase 3 산출물)
- 5분 상승 subset의 MAE가 현재 PathB stop 레벨과 충돌하면 편입 보류
- 운영자 승인

편입 방식:

```text
PREOPEN_CONTINUATION_INJECT_DISCOVERY=true
candidate_pool_role=DISCOVERY
discovery_action_ceiling=WATCH
```

계속 금지:

```text
DISCOVERY_ALLOW_PULLBACK_WAIT=false
DISCOVERY_ALLOW_BUY_READY=false
DISCOVERY_ALLOW_PROBE_READY=false
```

## 12. 테스트 계획

### Unit tests

추가 대상:

- DB schema 생성
- deterministic eligible filter
- score 계산
- fingerprint 안정성
- smart skip 조건
- Claude response parser
- parse 실패 retry 제한
- `INJECT_DISCOVERY=false`일 때 selection_meta 미변경

예상 테스트 파일:

```text
tests/test_preopen_continuation_shadow.py
```

### Integration tests

검증 항목:

- 완료 세션 파일에서 후보 저장
- 30분 feature snapshot 저장
- Claude mock 응답 저장
- outcome backfill
- report 생성
- discovery injection off 상태에서 PathB/PlanA 영향 없음

### Safety tests

필수 검증:

- `_pathb_wait_tickers`가 생성되지 않음
- `trade_ready`가 변경되지 않음
- `price_targets`가 live 실행용으로 주입되지 않음
- PathB `register_from_selection_meta`가 호출되지 않음
- `state/brain.json` 변경 없음

## 13. 남은 리스크

- 1주일 표본은 여전히 작다.
- 장전 후보는 이벤트/테마에 따라 일별 편차가 크다.
- Claude가 JSON contract를 깰 수 있어 compact parser와 retry가 필요하다.
- PROMOTE만 쓰면 KEEP 안의 승자를 놓칠 수 있다.
- 30분 판단은 분리력은 좋지만 실제 후보 공급 시점으로는 늦을 수 있다.
- 초기에는 매매 수익보다 판단 품질 검증에 집중해야 한다.
- **5분 subset MAE가 -2% 이상이면 현재 PathB stop 레벨과 호환 불가** — Phase 5 진입 전 반드시 확인 필요.
- KR 시장이 accidentally trigger되지 않도록 코드 레벨 guard 필요 (`market="US"` 강제 필터).

## 14. 코드레벨 검토 및 수정 요구사항

### 14.1 현재 코드 구조 검토 결과

현재 저장소에는 이미 장전 후보 수집/결과 추적의 shadow 기반 구조가 있다.

| 영역 | 현재 코드 | 검토 결과 |
|---|---|---|
| 장전 후보 수집 | `tools/preopen_collector.py::collect_once()` | `market`, `session_date`, `shadow_preopen_rank`, `extended_change_pct`, `extended_dollar_volume`, `news_or_earnings_flag` 등을 이미 저장한다. 새 기능은 이 산출물을 읽어야 하며 수집기를 중복 구현하지 않는다. |
| 장전 후보 모델 | `preopen/models.py::PreopenCandidate`, `normalize_candidate()` | 필요한 기본 필드는 대부분 있다. continuation 전용 eligible/decision/outcome은 기존 모델에 섞지 말고 별도 DB row로 분리한다. |
| 장전 점수 | `preopen/scorer.py::score_us_candidate()` | 현재 점수는 일반 장전 후보용이다. continuation deterministic score는 `gap 2~20%`, dollar volume, rank, news flag 기준이 다르므로 별도 함수로 둔다. |
| 장전 상태/로그 | `preopen/storage.py::state_path()`, `log_path()`, `save_candidate_records()`, `save_outcome_record()` | 기존 `state/preopen_US_*.json`, `logs/preopen/*_US_candidates.jsonl`, `logs/preopen/*_US_outcome.jsonl`를 읽기 전용 source로 사용한다. |
| 장후 outcome | `tools/preopen_outcome_updater.py::update_once()` | 5/30/60/120분 outcome과 `post_open_mfe_pct`, `post_open_mae_pct` 계열 필드를 이미 기록한다. continuation DB backfill은 이 결과를 가져와야 한다. |
| 스케줄러 | `preopen/scheduler.py::due_jobs()`, `tools/preopen_scheduler.py::run_scheduler_once()` | collector/news/outcome job은 이미 있다. continuation eval job은 Phase 4 이후 scheduler hook으로 붙이고, Phase 1~3은 독립 CLI로 검증한다. |
| Claude raw call | `minority_report/raw_call_logger.py::save()` | `logs/raw_calls/YYYYMMDD_US_<label>_<call_id>.json` 저장 형식이 이미 표준이다. continuation eval도 이 유틸을 써야 한다. |
| Claude 사용량 | `credit_tracker.py::record()`, `throttle_state()` | 호출 후 usage 기록과 budget throttle이 이미 있다. continuation eval 전 `throttle_state(label="preopen_continuation_eval")`를 확인한다. |
| selection smart skip | `runtime/selection_smart_skip.py` | selection 전용이며 entry-actionable 캐시 fail-open 정책이 있다. continuation shadow에는 직접 재사용하지 않고 fingerprint 함수/패턴만 참고한다. |
| selection 적용 | `trading_bot.py::_apply_selection_meta()` | 여기서 `trade_ready`, PathB wait, v2 decision 등록, `pathb.register_from_selection_meta()`가 연결된다. Phase 1~4에서는 이 경로를 절대 호출하지 않는다. |
| DISCOVERY ceiling | `trading_bot.py::_apply_candidate_pool_role_ceiling()` | Phase 5에서 실제 후보군 편입을 검토할 때만 사용한다. 이미 `DISCOVERY_ALLOW_BUY_READY/PROBE_READY/PULLBACK_WAIT=false`면 WATCH로 강등하는 안전장치가 있다. |
| 후보풀 점수 | `runtime/candidate_pool_runtime.py::build_candidate_pool()` | `preopen_confirmed` 개념이 있으나 이번 shadow DB와 바로 연결하지 않는다. Phase 5 편입 시 별도 source로 낮은 권한의 DISCOVERY 후보만 넣는다. |
| 기존 테스트 | `tests/test_preopen_shadow.py`, `tests/test_selection_smart_skip.py`, `tests/test_candidate_action_live_mapping.py` | 새 기능 테스트는 `tests/test_preopen_continuation_shadow.py`를 추가하고, safety는 기존 selection/PathB 테스트와 함께 회귀 확인한다. |

중요 재검토 결과:

- `tools/preopen_outcome_updater.py`의 return 계산은 현재 `anchor_price` 기준이다. 본 설계의 `ret_5m`, `ret_30m`, `ret_close`는 정규장 시가 기준이어야 하므로 continuation DB 저장 시 `regular_open_price` 기준 필드를 별도로 계산해야 한다.
- `preopen.scheduler.default_outcome_offsets_min()`는 30분 단위 전체 offset을 만들 수 있다. continuation shadow에는 5/30/60/120/close만 필요하므로 report/backfill 단계에서 필요한 offset만 선택한다.
- selection smart skip은 안전상 그대로 두고, continuation 전용 smart skip은 DB unique key와 fingerprint로 구현한다.
- Phase 1~4에서는 `trading_bot.py`, `runtime/pathb_runtime.py`, `execution/safety_gate.py`, `.env*`, `config/v2_start_config.json`, `state/brain.json` 수정이 필요 없다.

### 14.2 신규 파일/수정 파일 요구사항

초기 구현에서 직접 수정할 파일은 아래로 제한한다.

| 파일 | 작업 | 보호영역 영향 |
|---|---|---|
| `preopen/continuation_shadow.py` | 신규 모듈. DB schema, eligible filter, fingerprint, prompt builder, parser, 저장/조회 함수 구현 | 없음. 독립 shadow 모듈 |
| `tools/preopen_continuation_shadow.py` | 신규 CLI. `collect`, `feature`, `eval`, `backfill-outcome` step 실행 | 없음. trading_bot 미호출 |
| `tools/preopen_continuation_shadow_report.py` | 신규 CLI. 누적 성과 리포트 생성 | 없음. read-only report |
| `tests/test_preopen_continuation_shadow.py` | 신규 테스트. schema/filter/fingerprint/parser/safety 검증 | 없음 |
| `preopen/scheduler.py` | Phase 4 이후 선택 수정. `continuation_eval` job 추가 | scheduler job 추가만. 주문 영향 없음 |
| `tools/preopen_scheduler.py` | Phase 4 이후 선택 수정. 새 job 실행 허용 | scheduler job 실행만. 주문 영향 없음 |
| `dashboard/dashboard_server.py` | Phase 5 이후 선택 수정. shadow 결과 표시 필요 시 API/UI 추가 | 표시 전용. 초기 구현 제외 |

초기 구현에서 수정하지 않을 파일:

```text
trading_bot.py
runtime/pathb_runtime.py
execution/safety_gate.py
strategy/*
config/v2_start_config.json
.env*
state/brain.json
```

### 14.3 `preopen/continuation_shadow.py` 코드 요구사항

신규 모듈은 아래 public 함수를 제공한다.

```python
def db_path() -> Path: ...
def ensure_schema(path: Path | None = None) -> None: ...
def load_source_candidates(market: str, session_date: str, mode: str = "live") -> list[dict]: ...
def deterministic_candidate(row: dict, *, market: str, session_date: str) -> dict: ...
def upsert_candidates(rows: list[dict], *, db: Path | None = None) -> dict: ...
def upsert_feature_snapshot(row: dict, *, offset_min: int, db: Path | None = None) -> None: ...
def build_eval_cases(session_date: str, market: str = "US", offset_min: int = 30, limit: int = 15) -> list[dict]: ...
def continuation_fingerprint(cases: list[dict], *, session_date: str, market: str, offset_min: int) -> str: ...
def should_skip_eval(fingerprint: str, *, session_date: str, market: str, offset_min: int, db: Path | None = None) -> dict: ...
def build_claude_prompt(cases: list[dict], *, session_date: str, offset_min: int) -> str: ...
def parse_claude_response(raw: str, case_ids: set[str]) -> tuple[list[dict], dict]: ...
def record_claude_check(...): ...
def record_claude_decisions(...): ...
def backfill_outcomes(...): ...
```

필수 구현 원칙:

- `market != "US"`이면 `ValueError("preopen_continuation_shadow_supports_us_only")`로 즉시 중단한다.
- `data/preopen_continuation.db`는 `get_runtime_path("data", "preopen_continuation.db")`를 통해 얻는다.
- DB write는 `sqlite3` transaction 안에서 수행하고, unique key 충돌 시 update한다.
- JSON 컬럼은 `json.dumps(..., ensure_ascii=False, sort_keys=True)`로 저장한다.
- ticker key는 US 기준 uppercase로 정규화한다.
- source 후보는 `preopen.storage.load_preopen_state()`와 `preopen.storage.log_path("candidates", ...)`를 읽기 전용으로 사용한다.
- feature/outcome은 기존 `logs/preopen/*_US_outcome.jsonl`와 state 후보의 `outcome_samples`를 읽어온다.
- 정규장 시가 기준 수익률은 `regular_open_price`가 있을 때만 계산한다. 없으면 해당 ret 필드는 null로 두고 `price_basis_missing=true`를 기록한다.
- `ret_*`는 정규장 시가 기준, `anchor_ret_*`는 기존 anchor 기준으로 분리한다.
- MFE/MAE도 가능하면 정규장 시가 기준으로 재계산하고, 기존 outcome의 `post_open_mfe_pct`/`post_open_mae_pct`를 그대로 복사하지 않는다.

### 14.4 DB schema 코드 요구사항

`ensure_schema()`는 다음 index를 반드시 만든다.

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_preopen_candidates_session_market_ticker
ON preopen_candidates(session_date, market, ticker);

CREATE UNIQUE INDEX IF NOT EXISTS ux_preopen_feature_snapshots_session_market_ticker_offset
ON preopen_feature_snapshots(session_date, market, ticker, offset_min);

CREATE UNIQUE INDEX IF NOT EXISTS ux_preopen_claude_checks_session_market_offset_fingerprint
ON preopen_claude_checks(session_date, market, eval_offset_min, fingerprint);

CREATE INDEX IF NOT EXISTS ix_preopen_claude_decisions_session_market_decision
ON preopen_claude_decisions(session_date, market, decision);

CREATE UNIQUE INDEX IF NOT EXISTS ux_preopen_outcomes_session_market_ticker
ON preopen_outcomes(session_date, market, ticker);
```

`preopen_claude_checks`에는 skip도 row로 남긴다.

| 상황 | 저장값 |
|---|---|
| 후보 없음 | `smart_skip=1`, `skip_reason="no_eligible_candidates"`, `parse_ok=0` |
| fingerprint 중복 | `smart_skip=1`, `skip_reason="fingerprint_seen"`, `parse_ok=1` |
| 30분 feature 부족 | `smart_skip=1`, `skip_reason="feature_not_ready"`, `parse_ok=0` |
| API rate/network/timeout | `smart_skip=1`, `skip_reason="api_error:<type>"`, `parse_ok=0` |
| parse 실패 후 retry 실패 | `smart_skip=0`, `parse_ok=0`, `parse_error` 기록 |

### 14.5 CLI 코드 요구사항

`tools/preopen_continuation_shadow.py`는 아래 step을 지원한다.

```bash
python tools/preopen_continuation_shadow.py --market US --step collect --mode live
python tools/preopen_continuation_shadow.py --market US --step feature --offset 30 --mode live
python tools/preopen_continuation_shadow.py --market US --step eval --offset 30 --mode live
python tools/preopen_continuation_shadow.py --market US --step backfill-outcome --mode live
```

옵션:

| 옵션 | 설명 |
|---|---|
| `--market` | 초기에는 `US`만 허용 |
| `--date` | 지정 세션 backfill/replay |
| `--mode` | `live`/`paper`; 기본 `live` |
| `--step` | `collect`, `feature`, `eval`, `backfill-outcome`, `all` |
| `--offset` | feature/eval offset. 기본 30 |
| `--max-candidates` | 기본 `PREOPEN_CONTINUATION_MAX_CANDIDATES` 또는 15 |
| `--dry-run` | DB write/Claude call 없이 대상만 출력 |
| `--no-claude` | prompt와 fingerprint까지만 생성 |
| `--ticker-selection-db` | 기존 selection DB read-only backfill source. 미지정 시 `data/ticker_selection_log.db` |
| `--candidate-audit-db` | 기존 audit DB read-only backfill source. 미지정 시 `data/audit/candidate_audit.db` |
| `--ml-decisions-db` | 기존 ML/V2 decisions DB read-only backfill source. 미지정 시 `data/ml/decisions.db` |

CLI 안전 조건:

- `--offset`은 `feature`/`eval`에서만 해석한다. `collect`, `init`, `backfill-outcome`은 잘못된 offset 인자가 있어도 해당 단계 실행을 막지 않는다.
- `--step all`은 부분 실행 오염을 막기 위해 시작 전에 offset을 먼저 검증한다.
- `--step eval`에서도 `PREOPEN_CONTINUATION_CLAUDE_EVAL_ENABLED=false`이면 Claude를 호출하지 않고 skip row만 남긴다.
- `PREOPEN_CONTINUATION_CLAUDE_MAX_CALLS_PER_DAY` 초과 시 `skip_reason="daily_call_cap"`을 남긴다.
- `throttle_state(label="preopen_continuation_eval")`가 `allowed=false`이면 호출하지 않는다.
- `save_raw_call(label="preopen_continuation_eval", ...)`를 사용해 raw call을 표준 위치에 남긴다.
- `credit_tracker.record(..., label="preopen_continuation_eval", model=model)`을 호출한다.
- 기존 `ticker_selection_log.db`, `candidate_audit.db`, `ml/decisions.db`는 SQLite `mode=ro` URI로만 열고, join 결과는 shadow DB에만 쓴다.

### 14.6 Claude prompt/parser 코드 요구사항

prompt는 case id 기반으로 ticker를 숨기거나 최소화한다. ticker 자체를 숨기는 목적은 정답 누설 방지가 아니라 유명 종목 bias를 줄이는 것이다. DB에는 case id와 ticker mapping을 저장한다.

입력 case 예:

```json
{"id":"C01","gap":6.2,"rank":4,"dv_bucket":"100M_300M","news":true,"r5":1.1,"r30":3.4,"mfe30":4.2,"mae30":-0.8,"risk":["wide_spread"]}
```

parser 규칙:

- strict JSON만 허용한다.
- top-level key는 `cases`만 허용한다.
- 각 case는 `[case_id, decision, confidence, reason_code]` 또는 object `{id,d,c,rc}`만 허용한다.
- decision은 `PROMOTE`, `KEEP`, `DROP`만 허용한다.
- confidence는 0~1로 clamp하지 말고 범위 밖이면 parse error로 처리한다.
- unknown case id, 중복 case id, 누락 case id는 parse error로 처리한다.
- parse retry prompt에는 원래 시장 데이터 전문을 다시 보내지 않고, 실패 raw response와 schema만 보내 compact repair를 요청한다.

### 14.7 Smart Skip 코드 요구사항

selection smart skip을 직접 호출하지 않는다. continuation 전용 fingerprint는 아래 bucket 함수로 만든다.

```python
def bucket_pct(value: float | None, cuts: tuple[float, ...]) -> str: ...
def bucket_dollar_volume(value: float | None) -> str: ...
def bucket_rank(value: int | None) -> str: ...
def canonical_case_for_fingerprint(case: dict) -> dict: ...
```

fingerprint payload:

```json
{
  "schema": "preopen_continuation_shadow.fp.v1",
  "session_date": "2026-06-04",
  "market": "US",
  "offset_min": 30,
  "cases": [
    {"t":"RDDT","gap":"5_10","dv":"300M_PLUS","rank":"1_10","news":1,"r5":"pos_1_3","r30":"pos_3_5","mfe30":"pos_3_5","mae30":"neg_0_1","risk":["atr_high"]}
  ]
}
```

같은 fingerprint가 이미 있으면 Claude를 재호출하지 않는다. 단, parse 실패 row만 있고 retry 가능 횟수가 남아 있으면 retry를 허용한다.

### 14.8 Report 코드 요구사항

`tools/preopen_continuation_shadow_report.py`는 DB만 읽어서 markdown을 생성한다.

필수 산출물:

```text
docs/reports/preopen_continuation_shadow_report_YYYYMMDD.md
```

집계 축:

- decision별 후보 수, ret_30m/60m/120m/close 평균, win rate, MFE, MAE
- `PROMOTE` vs `KEEP` vs `DROP`
- `PROMOTE+KEEP` vs `DROP`
- 기존 selection 포착 여부: `selected_by_live_claude`, `live_trade_ready`, `ordered`
- 5분 상승 subset의 MAE 분포: 평균, 중앙값, p10, p25
- parse success rate, skip reason count, Claude call count, token count

합격/보류 판정은 report가 자동으로 단정하지 않고 `recommendation="shadow_continue|consider_discovery_watch|block_or_discard"` 정도만 제시한다.

운영 오염 방지:

- shadow DB가 없으면 default report 파일을 만들지 않고 빈 JSON payload에 `missing_db`를 포함해 출력한다.
- shadow DB schema가 맞지 않으면 예외로 중단하지 않고 `schema_error`와 `recommendation="shadow_continue"`를 반환한다.
- 명시적으로 `--output`을 준 경우에만 missing/schema-error payload도 파일로 저장할 수 있다.

### 14.9 Phase 5 DISCOVERY 연결 코드 요구사항

Phase 5는 별도 승인 전까지 구현하지 않는다. 구현하게 되면 아래 순서만 허용한다.

1. `preopen/continuation_shadow.py`에서 `eligible_for_discovery_watch()`를 추가한다.
2. 새 후보 row에는 `candidate_pool_role="DISCOVERY"`, `discovery_signal_family="preopen_continuation"`, `discovery_action_ceiling="WATCH"`를 넣는다.
3. selection prompt pool에 넣는 경우에도 `candidate_actions`에는 `WATCH`만 허용한다.
4. `trading_bot.py::_apply_candidate_pool_role_ceiling()`가 최종 방어선으로 작동하는지 테스트한다.
5. `DISCOVERY_ALLOW_PULLBACK_WAIT=false`, `DISCOVERY_ALLOW_BUY_READY=false`, `DISCOVERY_ALLOW_PROBE_READY=false`를 유지한다.

Phase 5에서 건드릴 가능성이 있는 파일:

```text
runtime/candidate_discovery_overlay.py
runtime/candidate_pool_runtime.py
trading_bot.py
tests/test_candidate_action_live_mapping.py
```

이 단계는 selection/PathB 연결 가능성이 있으므로 구현 전 별도 작업 설명과 회귀 테스트 범위를 다시 승인받는다.

### 14.10 코드레벨 테스트 계획 상세

1차 문서 이후 실제 구현 시 실행할 검증 명령:

```bash
python -m py_compile preopen/continuation_shadow.py tools/preopen_continuation_shadow.py tools/preopen_continuation_shadow_report.py
python -m pytest tests/test_preopen_continuation_shadow.py -q
python -m pytest tests/test_preopen_shadow.py tests/test_selection_smart_skip.py tests/test_candidate_action_live_mapping.py -q
```

필수 테스트 케이스:

| 테스트 | 기대 |
|---|---|
| US 외 market 차단 | KR 입력 시 `preopen_continuation_shadow_supports_us_only` |
| deterministic filter | gap<=0, gap>20, dollar volume 부족, rank>40 제외 |
| DB idempotent upsert | 같은 session/ticker 재실행 시 row 중복 없음 |
| regular-open basis 계산 | anchor 기준과 open 기준 ret가 별도 저장됨 |
| 30분 feature 미준비 | Claude 호출 없이 skip row 저장 |
| fingerprint 동일 | 두 번째 eval에서 Claude 호출 없음 |
| fingerprint bucket 변화 | 의미 있는 feature bucket 변화 시 새 eval 가능 |
| parse strict | markdown/설명문/unknown case id 거부 |
| parse retry cap | parse 실패 retry 최대 1회 |
| API error | retry 없이 skip row 기록 |
| no-claude/dry-run | raw call/usage/DB write 없음 또는 의도된 write만 수행 |
| 기존 source DB read-only | `ticker_selection_log.db`, `candidate_audit.db`, `ml/decisions.db`를 read-only로 열고 shadow DB에만 backfill |
| 잘못된 shadow DB schema | eval/report가 예외 대신 `db_schema_unavailable`/`schema_error` 반환 |
| report missing DB | default report 파일을 만들지 않고 `missing_db` payload만 출력 |
| selection safety | `trading_bot._apply_selection_meta()` 미호출 |
| PathB safety | `pathb.register_from_selection_meta()` 미호출 |
| brain safety | `state/brain.json` 미변경 |

### 14.11 재검토 결론

사용자 수정으로 추가된 실행 타이밍, 5분 subset MAE, US guard는 모두 유지하는 것이 맞다.

보강 후 최종 설계 판단:

- 현재 코드 구조상 `tools/preopen_continuation_shadow.py` 독립 CLI + `preopen/continuation_shadow.py` storage 모듈이 가장 안전하다.
- 기존 `preopen_outcome_updater.py`가 만든 outcome을 재사용하되, continuation DB에는 정규장 시가 기준 수익률을 새로 계산해야 한다.
- selection smart skip은 그대로 두고, continuation 전용 fingerprint/unique key로 skip을 구현한다.
- Phase 1~4는 trading runtime과 완전히 분리 가능하다.
- Phase 5 DISCOVERY 편입은 기존 ceiling guard가 있으나, 이때부터는 selection/PathB 영향권이므로 별도 승인과 회귀 테스트가 필요하다.

### 14.12 최종 DB 누락 재검토 결과

실제 `state/preopen_US_*.json`, `logs/preopen/*_US_outcome.jsonl`, `data/ticker_selection_log.db`, `data/audit/candidate_audit.db`, `data/ml/decisions.db`를 대조하면 최초 DB 설계에는 핵심 판단값은 있으나 아래 항목이 부족하다. 이 항목은 구현 시 schema에 추가한다.

#### 신규 테이블: `preopen_shadow_runs`

실행 단위 추적 테이블을 추가한다. 이 테이블이 없으면 어떤 소스 파일/offset/옵션으로 DB가 갱신됐는지 재현하기 어렵다.

| 컬럼 | 설명 |
|---|---|
| id | PK |
| run_id | 실행 단위 UUID 또는 stable id |
| session_date | 세션 날짜 |
| market | `US` |
| runtime_mode | `live`/`paper` |
| step | `collect`, `feature`, `eval`, `backfill-outcome`, `report` |
| offset_min | feature/eval offset |
| status | `started`, `success`, `skipped`, `error` |
| started_at | 시작 시각 |
| finished_at | 종료 시각 |
| source_state_path | 읽은 `state/preopen_US_*.json` |
| source_candidate_log_path | 읽은 candidates jsonl |
| source_outcome_log_path | 읽은 outcome jsonl |
| source_state_captured_at | source state captured_at |
| source_state_age_min | source state age |
| source_candidate_count | source 후보 수 |
| eligible_count | eligible 후보 수 |
| evaluated_count | Claude 평가 후보 수 |
| error_type | 오류 유형 |
| error_message | 오류 메시지 요약 |
| config_json | 실행 옵션/env snapshot |
| created_at | DB 기록 시각 |

유니크 키:

```text
run_id
```

#### `preopen_candidates` 추가 컬럼

후보 테이블은 eligible 후보만 저장하면 안 된다. 후보군 품질과 제외 사유를 재검토하려면 source 상위 후보 전체를 저장하고 `eligible=false`를 함께 남긴다.

추가 필수 컬럼:

| 컬럼 | 설명 |
|---|---|
| runtime_mode | `live`/`paper` |
| schema_version | `preopen_continuation_shadow.v1` |
| run_id | 수집 run id |
| source_status | 기존 preopen collector source_status |
| provider | `us_screen_market`, `screen_cache` 등 |
| data_quality | source data quality |
| stale | source stale 여부 |
| source_file | 원천 state/jsonl 파일 |
| source_row_hash | 후보 row canonical hash |
| source_row_json | 원천 row 축약 JSON |
| first_detected_at | 최초 감지 시각 |
| last_detected_at | 마지막 감지 시각 |
| detected_at | 감지 시각 |
| preopen_score | 기존 preopen_score |
| preopen_grade | 기존 preopen_grade |
| screen_score | 기존 screen_score |
| change_rate | source change_rate |
| volume_ratio | volume ratio |
| spread_pct | bid/ask spread |
| bid | bid |
| ask | ask |
| regular_prev_close | 전일 종가 |
| anchor_price | 기존 preopen anchor price |
| anchor_price_source | anchor source |
| anchor_price_at | anchor timestamp |
| category | day_gainers, most_actives 등 |
| sector | sector |
| market_type | market type |
| liquidity_bucket | liquidity bucket |
| from_high_pct | 고점 대비 위치 |
| from_high_bucket | 고점 대비 bucket |
| above_ma60 | MA60 상회 여부 |
| source_overlap_count | source overlap count |
| pattern_tags_json | pattern tags |
| news_or_earnings_count | 뉴스/실적 count |
| news_or_earnings_sources_json | 뉴스/실적 source 배열 |
| eligible_rule_version | deterministic filter version |
| eligible_components_json | gap/dollar volume/rank/news 등 조건별 판정 |
| evaluation_case_id | 최신 eval case id. 여러 eval을 허용하면 decisions 테이블을 우선 truth로 본다. |

기록 원칙:

- `PREOPEN_CONTINUATION_MAX_CANDIDATES=15`는 Claude 평가 후보 수 cap일 뿐 DB 저장 cap이 아니다.
- DB에는 source 후보 전체 또는 최소 source top 60을 저장한다.
- `source_row_json`은 전체 row를 무제한 저장하지 않고 분석 재현에 필요한 key만 축약한다.

#### `preopen_feature_snapshots` 추가 컬럼

기존 outcome updater의 return은 `anchor_price` 기준이다. continuation 평가는 정규장 시가 기준이므로 두 기준을 분리해서 저장한다.

추가 필수 컬럼:

| 컬럼 | 설명 |
|---|---|
| run_id | feature run id |
| candidate_id | `preopen_candidates.id` |
| snapshot_status | `sampled`, `missing`, `provider_error`, `basis_missing` |
| token_status | source outcome token_status |
| source_outcome_path | 읽은 outcome jsonl |
| source_outcome_ts | outcome row ts |
| source_offset_min | source offset |
| anchor_price | 기존 anchor price |
| anchor_return_pct | anchor 기준 offset return |
| high_price | offset까지 high |
| low_price | offset까지 low |
| high_return_from_open_pct | open 기준 high return |
| low_return_from_open_pct | open 기준 low return |
| high_return_from_anchor_pct | anchor 기준 high return |
| low_return_from_anchor_pct | anchor 기준 low return |
| price_basis | `regular_open_price` 또는 `anchor_price` |
| price_basis_missing | 정규장 시가 기준 계산 불가 여부 |
| sample_json | source sample 축약 JSON |
| created_at | 최초 기록 시각 |
| updated_at | 갱신 시각 |

#### `preopen_claude_checks` 추가 컬럼

Claude batch는 비용/재시도/재현성을 위해 실행 상태를 더 자세히 남겨야 한다.

추가 필수 컬럼:

| 컬럼 | 설명 |
|---|---|
| run_id | eval run id |
| runtime_mode | `live`/`paper` |
| status | `called`, `skipped`, `parse_failed`, `api_error` |
| attempt_no | 1부터 시작 |
| retry_of_check_id | retry 원본 check id |
| max_tokens | Claude max_tokens |
| duration_ms | 호출 소요 |
| prompt_hash | prompt sha256 |
| response_hash | raw response sha256 |
| prompt_chars | prompt 길이 |
| response_chars | response 길이 |
| prompt_case_count | prompt에 들어간 case 수 |
| case_map_json | `C01 -> ticker/candidate_id` mapping |
| throttle_enabled | budget throttle 활성 여부 |
| throttle_allowed | 호출 허용 여부 |
| throttle_tier | `normal`, `warn`, `hard_cap` 등 |
| daily_call_count_before | 호출 전 당일 eval call count |
| daily_call_count_after | 호출 후 당일 eval call count |
| api_error_type | `rate_limit`, `network`, `timeout`, `unknown` |
| api_error_message | 오류 메시지 요약 |

#### `preopen_claude_decisions` 추가 컬럼

case id mapping과 parser 결과를 남겨야 blind 평가를 재현할 수 있다.

추가 필수 컬럼:

| 컬럼 | 설명 |
|---|---|
| candidate_id | `preopen_candidates.id` |
| case_id | `C01` 등 prompt case id |
| rank_in_prompt | prompt 내 순서 |
| raw_decision | Claude 원문 decision |
| raw_confidence | Claude 원문 confidence |
| parse_warning | parser warning |
| visible_feature_hash | 평가에 보인 feature hash |
| decision_payload_json | decision row 원문 축약 |
| candidate_pool_role | 초기에는 `SHADOW_ONLY`, Phase 5 이후 `DISCOVERY` 가능 |
| discovery_signal_family | `preopen_continuation` |
| discovery_action_ceiling | 초기/Phase 5 모두 `WATCH` |
| injection_eligible_after_shadow | shadow 결과상 향후 편입 후보 여부 |

#### `preopen_outcomes` 추가 컬럼

후속 성과는 실제 live selection/주문과 연결해 봐야 한다. 최소한 기존 selection DB/audit DB와 조인 가능한 정보를 남긴다.

추가 필수 컬럼:

| 컬럼 | 설명 |
|---|---|
| candidate_id | `preopen_candidates.id` |
| latest_decision_id | 최신 `preopen_claude_decisions.id` |
| outcome_status | `partial`, `complete`, `missing`, `provider_error` |
| open_price | 정규장 시가 |
| close_price | 종가 또는 최종 관측가 |
| ret_90m | 시가 대비 90분 수익률 |
| ret_150m | 시가 대비 150분 수익률 |
| ret_180m | 시가 대비 180분 수익률 |
| anchor_ret_5m | anchor 기준 5분 수익률 |
| anchor_ret_30m | anchor 기준 30분 수익률 |
| anchor_ret_60m | anchor 기준 60분 수익률 |
| anchor_ret_120m | anchor 기준 120분 수익률 |
| anchor_ret_close | anchor 기준 종가 수익률 |
| mfe_open_basis | 시가 기준 MFE |
| mae_open_basis | 시가 기준 MAE |
| mfe_anchor_basis | anchor 기준 MFE |
| mae_anchor_basis | anchor 기준 MAE |
| outcome_samples_json | offset sample 축약 JSON |
| actual_selected | 기존 preopen rank_diff/live selection 포함 여부 |
| actual_selection_rank | 기존 selection rank |
| actual_trade_ready | 기존 live trade_ready 여부 |
| actual_ordered | 실제 주문 여부 |
| ticker_selection_log_id | `ticker_selection_log.id` 매칭 시 |
| audit_candidate_key | `audit_candidate_rows.candidate_key` 매칭 시 |
| v2_decision_id | decisions/v2 매칭 시 |
| path_run_id | PathB run 매칭 시 |
| route_final_action | 기존 route final action |
| route_route | 기존 route |
| entry_price | 실제 진입가가 있으면 기록 |
| pnl_pct | 실제 거래 PnL이 있으면 기록 |
| updated_at | 갱신 시각 |

기록 원칙:

- `actual_selected`, `actual_trade_ready`, `actual_ordered`는 source preopen state에 있으면 우선 사용하고, 없으면 `ticker_selection_log.db`와 `candidate_audit.db`에서 backfill한다.
- 실제 주문/PathB 여부는 운영 truth가 아니라 shadow 분석용 join 결과다. 주문 상태 판단에 사용하지 않는다.
- outcome이 아직 장중이면 `outcome_status="partial"`로 저장하고 종가 backfill 후 `complete`로 바꾼다.

#### 추가 index

최종 schema에는 아래 index도 추가한다.

```sql
CREATE INDEX IF NOT EXISTS ix_preopen_candidates_session_eligible_score
ON preopen_candidates(session_date, market, eligible, deterministic_score DESC);

CREATE INDEX IF NOT EXISTS ix_preopen_candidates_source_hash
ON preopen_candidates(source_row_hash);

CREATE INDEX IF NOT EXISTS ix_preopen_feature_snapshots_candidate_offset
ON preopen_feature_snapshots(candidate_id, offset_min);

CREATE INDEX IF NOT EXISTS ix_preopen_claude_decisions_case
ON preopen_claude_decisions(check_id, case_id);

CREATE INDEX IF NOT EXISTS ix_preopen_outcomes_decision_join
ON preopen_outcomes(session_date, market, ticker, actual_selected, actual_trade_ready, actual_ordered);
```

#### 최종 누락 판단

위 보강을 넣으면 DB 관점의 큰 누락은 없다. 특히 아래 질문에 답할 수 있어야 한다.

- 어떤 원천 후보가 왜 eligible 또는 excluded 됐는가?
- Claude가 실제로 본 정보는 어느 offset까지인가?
- Claude 판단은 어떤 raw call, prompt hash, case id에서 나왔는가?
- 같은 후보/feature라서 skip된 것인지, API 문제로 skip된 것인지 구분되는가?
- `PROMOTE`, `KEEP`, `DROP`별 성과가 open 기준과 anchor 기준에서 어떻게 다른가?
- 기존 live selection이 해당 후보를 봤는지, trade_ready로 올렸는지, 실제 주문됐는지 추적 가능한가?
- 5분 상승 subset의 MAE가 현재 PathB stop 레벨과 호환되는지 계산 가능한가?

## 15. 최종 권고

진행 권고:

```text
shadow-only DB 실험은 진행
실제 selection/PathB 연결은 보류
Claude 호출은 US 하루 1회, 30분 offset 기준
Smart Skip 적용
1주일 이상 PROMOTE/KEEP/DROP outcome 수집 후 재판단
```

최초 운영 판단:

- `PROMOTE` 단독 후보 편입은 아직 위험하다.
- `PROMOTE + KEEP`을 watch 후보로 유지하고 `DROP`만 제외하는 방식이 더 현실적이다.
- 하지만 이 역시 1주일 shadow 검증 후에만 실제 후보군 편입을 고려한다.

## 16. 구현 완료 및 QA 결과

구현 일자: 2026-06-05

구현 산출물:

| 파일 | 역할 |
|---|---|
| `preopen/continuation_shadow.py` | 독립 shadow DB schema, 후보 수집, feature snapshot, Claude eval, parser, smart skip, outcome/report helper |
| `tools/preopen_continuation_shadow.py` | `collect`, `feature`, `eval`, `backfill-outcome`, `all` CLI |
| `tools/preopen_continuation_shadow_report.py` | shadow DB 기반 markdown/json 리포트 CLI |
| `tests/test_preopen_continuation_shadow.py` | schema, idempotent upsert, open/anchor basis, strict parser, no-claude skip, parse retry, 기존 DB read-only join, report/schema guard 검증 |

구현 범위:

- Phase 1~4 shadow-only 범위 구현 완료.
- Phase 5 `DISCOVERY/WATCH` 실제 편입은 구현하지 않음.
- dashboard 표시는 구현하지 않음. 초기 운영은 DB와 CLI report로만 검증한다.
- 실제 selection, PathB, 주문, 리스크, broker truth, config/env, `state/brain.json` 변경 없음.

MD 대비 차이 및 누락점 재검토:

| 항목 | 결과 |
|---|---|
| Shadow DB schema | 요구서의 `preopen_shadow_runs`, `preopen_candidates`, `preopen_feature_snapshots`, `preopen_claude_checks`, `preopen_claude_decisions`, `preopen_outcomes` 구현 |
| source 후보 저장 | eligible만 저장하지 않고 source top 60 저장. CLI `--source-limit`로 조정 가능 |
| regular open 기준 | explicit date replay 가능하도록 `--date` 지정 시 state stale age 체크 비활성화 |
| open/anchor basis 분리 | feature snapshot과 outcome에 open 기준, anchor 기준을 분리 저장 |
| close 기준 정합성 | `ret_close`는 해당 세션의 마지막 scheduled outcome offset이 저장됐을 때만 채운다. 30/60/120분만 있으면 `ret_close=null`, `outcome_status=partial` 유지 |
| Claude parser | strict JSON, unknown/duplicate/missing case id, confidence 범위 검증 구현 |
| parse retry | 1차 parse 실패 시 compact repair prompt 1회 재시도 구현 |
| smart skip | fingerprint 중복, 후보 없음, feature 미준비, no-claude, daily cap, throttle/API 오류를 `preopen_claude_checks`에 기록. 같은 fingerprint 반복 skip도 attempt를 증가시켜 기록 |
| 기존 DB join | `ticker_selection_log.db`, `candidate_audit.db`, `ml/decisions.db`를 SQLite `mode=ro` read-only source로 사용해 shadow outcome에 join 결과만 backfill |
| CLI offset | `--offset close` 입력 시 기존 preopen scheduler의 마지막 scheduled outcome offset으로 변환. offset은 `feature`/`eval` 단계에서만 해석 |
| dry-run/report 오염 방지 | `eval --dry-run`과 report 생성은 shadow DB가 없으면 파일을 만들지 않고 빈 payload 또는 `db_missing`/`missing_db` 상태를 반환 |
| schema guard | 잘못된 shadow DB schema는 eval/report에서 예외 대신 `db_schema_unavailable`/`schema_error`로 보고하고 `shadow_continue` 유지 |
| 운영 연결 | `trade_ready`, `PULLBACK_WAIT`, `BUY_READY`, `PROBE_READY`, PathB registration 구현 없음 |
| 남은 제한 | 실제 Claude 호출 품질은 shadow 운영 중 raw call/parse success로 추가 확인 필요 |

QA 실행 결과:

```text
python -m py_compile preopen/continuation_shadow.py tools/preopen_continuation_shadow.py tools/preopen_continuation_shadow_report.py tests/test_preopen_continuation_shadow.py
python -m pytest tests/test_preopen_continuation_shadow.py -q
16 passed

python -m pytest tests/test_preopen_shadow.py tests/test_selection_smart_skip.py tests/test_candidate_action_live_mapping.py -q
134 passed, 2 warnings

python -m pytest -q
2254 passed, 2 skipped, 2 warnings
```

기존 DB 기반 시뮬레이션:

```text
source sessions: 2026-06-01 ~ 2026-06-04
write target: %TEMP%/preopen_continuation_shadow_multi_*.db
source read-only: state/preopen_US_*.json, logs/preopen/*_US_outcome.jsonl,
                  data/ticker_selection_log.db, data/audit/candidate_audit.db,
                  data/ml/decisions.db
Claude call: --no-claude
```

누적 결과:

| 항목 | 값 |
|---|---:|
| source 후보 | 240 |
| eligible 후보 | 107 |
| outcome row | 240 |
| eval checks | 4 |
| Claude 실제 호출 | 0 |
| skip reason | `no_claude` 4 |
| 30m 평균 수익률 | -0.2335% |
| 60m 평균 수익률 | +0.4566% |
| 120m 평균 수익률 | +0.6993% |
| 120m 기준 승률 | 57.5% |
| close 평균 수익률 | close offset 미수집이면 null |
| 평균 MFE | +3.2276% |
| 평균 MAE | -2.5543% |
| 5분 상승 subset MAE 중앙값 | -1.0435% |
| 5분 상승 subset MAE p25 | -1.8621% |

재검토 보강 결과:

- 같은 fingerprint로 `--no-claude` 또는 skip eval을 반복 실행해도 unique constraint 충돌 없이 attempt row가 누적된다.
- `ret_close`는 120분 fallback으로 채우지 않는다. 종가/마지막 scheduled offset이 수집되기 전에는 partial outcome으로 남긴다.
- report 날짜 필터 SQL은 alias 문자열 치환 방식이 아니라 테이블별 explicit where clause로 분리했다.
- report와 `eval --dry-run`은 빈 shadow DB를 만들지 않는다.
- 기존 selection/audit/ML decisions DB 조회는 SQLite read-only URI(`mode=ro`)로 고정했다.
- `collect`/`init`/`backfill-outcome`은 offset이 필요 없는 단계이므로 잘못된 `--offset` 인자 때문에 실패하지 않는다.
- 잘못된 schema의 shadow DB는 eval/report에서 예외 대신 안전 상태로 보고한다.
- report CLI는 missing/schema-error DB에서 `--output`이 없으면 default report 파일을 만들지 않고 JSON 상태만 출력한다.

운영 오염성 검토:

- 시뮬레이션 write는 `%TEMP%`의 별도 shadow DB에만 발생했다.
- shadow backfill은 기존 DB를 `mode=ro`로 열어 읽고, write는 shadow DB에만 수행한다.
- 전체 pytest 실행 중 기존 운영성 DB 수정 시각이 변할 수 있으므로, 운영 오염성 판단은 shadow CLI의 명시 `--db-path`/기본 `data/preopen_continuation.db` write 경계와 read-only source 연결로 확인한다.
- `state/brain.json` 변경 경로 없음.
- `trading_bot.py`, `runtime/pathb_runtime.py`, `execution/safety_gate.py`, live config/env 변경 없음.
- 새 CLI는 기본적으로 독립 실행이며 trading runtime에서 import/call하지 않는다.

운영 테스트 권고:

1. 첫 운영 테스트는 아래처럼 no-claude 또는 eval disabled로 시작한다.

```bash
python tools/preopen_continuation_shadow.py --market US --step collect --mode live
python tools/preopen_continuation_shadow.py --market US --step feature --offset 30 --mode live
python tools/preopen_continuation_shadow.py --market US --step eval --offset 30 --mode live --no-claude
python tools/preopen_continuation_shadow_report.py --market US --from YYYY-MM-DD --to YYYY-MM-DD --dry-run
```

2. DB write 대상은 `data/preopen_continuation.db` 하나만 허용한다.
3. Claude 실제 호출을 켜는 경우에도 `PREOPEN_CONTINUATION_CLAUDE_MAX_CALLS_PER_DAY=1`, `PREOPEN_CONTINUATION_CLAUDE_RETRY_MAX=1`을 유지한다.
4. 7거래일 shadow 데이터가 쌓이기 전까지 `PREOPEN_CONTINUATION_INJECT_DISCOVERY=false`를 유지한다.
5. Phase 5 편입 검토 전에는 이 문서의 `10.1 성과 비교 및 반영 판단 기준`으로 재판정한다.
