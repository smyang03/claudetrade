# Claude 사용량/품질 최적화 분석 - 2026-04-29 기준

## 기준

- 원천: `logs/raw_calls/20260429_*.json`, `logs/raw_calls/20260430_US_*.json`, 운영 로그.
- KR 기준: 2026-04-29 KST 한국장.
- US 기준: 2026-04-29 미국장 세션을 KST로 보정한 `2026-04-29 22:20 ~ 2026-04-30 05:00`.
- 목적: 비용 절감, 응답 품질 안정화, 실거래 의사결정 성능 향상.

## 결론

현재 Claude는 시스템에 적합하다. 특히 후보 선별, 가격 타깃 생성, 보유 판단처럼 규칙 기반만으로 설명력이 부족한 영역에서 유의미한 구조화 판단을 준다.

다만 사용량 구조는 비효율적이다. `select_tickers`가 전체 토큰의 약 57%를 차지하고, `tune_*`는 전부 `MAINTAIN`인데도 하루 3.8만 토큰 수준을 사용했다. `hold_advisor`는 3관점 호출 방식 때문에 포지션당 API 호출이 3배로 늘어난다.

품질 측면에서는 JSON/스키마 성공률이 대체로 높지만, US 2026-04-29 postmortem은 2026-04-30 05:00에 JSON 파싱 실패가 발생했다. 학습/사후분석 경로는 의사결정 경로보다 더 엄격한 저장/복구가 필요하다.

## 사용량 요약

| 구분 | 호출 | 입력 토큰 | 출력 토큰 | 총 토큰 |
|---|---:|---:|---:|---:|
| KR 2026-04-29 | 66 | 126,825 | 50,578 | 177,403 |
| US 2026-04-29 세션 | 74 | 133,433 | 52,009 | 185,442 |
| 합계 | 140 | 260,258 | 102,587 | 362,845 |

## 기능별 사용량

| 시장 | 기능 | 호출 | 총 토큰 | 비중 | 판단 |
|---|---|---:|---:|---:|---|
| KR | select_tickers | 13 | 102,653 | 57.9% | 최대 비용원. 품질은 좋지만 rescreen 빈도/출력 길이 최적화 필요 |
| KR | intraday_tune | 14 | 20,930 | 11.8% | 전부 MAINTAIN. LLM 호출 가치 낮음 |
| KR | analyst | 6 | 21,771 | 12.3% | 장 시작 핵심 판단. 유지 권장 |
| KR | hold_advisor | 30 | 19,270 | 10.9% | 호출 수가 많음. 3관점 단일 호출화 가능 |
| KR | postmortem | 1 | 9,089 | 5.1% | 비용 작고 학습 가치 큼. 유지 |
| US | select_tickers | 13 | 105,019 | 56.6% | 최대 비용원. 중복/근접 호출 제어 필요 |
| US | intraday_tune | 11 | 17,552 | 9.5% | 전부 MAINTAIN. 이벤트 기반 호출로 전환 권장 |
| US | analyst | 6 | 27,938 | 15.1% | 장 시작 disagreement 포착. 유지 권장 |
| US | hold_advisor | 39 | 23,974 | 12.9% | 호출 수가 많음. 단일 호출화 우선 |
| US | param_tuner | 5 | 10,959 | 5.9% | session/rescreen 때만 가치 있음 |

## 품질 분석

### 좋은 점

- raw call 기준 응답 저장/파싱은 KR 66건, US 세션 내 비-postmortem 호출 74건 모두 구조화 응답으로 남았다.
- `select_tickers` 가격 구조는 검사한 모든 price target에서 `stop_loss < buy_zone_low <= buy_zone_high < sell_target` 조건을 만족했다.
- KR/US 모두 장 시작 analyst 2라운드 응답은 일관성이 있었다. R2에서 `changed=False`가 반복되어 판단 흔들림은 적었다.
- US 2026-04-29 세션은 최종 `pnl=+2.14%`로 기록되어, 큰 방향성과 실행 결과는 양호했다.

### 문제점

- `select_tickers`가 전체 토큰의 56~58%를 차지한다. 답변의 가격 타깃/이유가 길고, 장중 재선정 때 매번 전체 후보 컨텍스트를 다시 보낸다.
- KR `trade_ready` churn이 높다. 연속 selection 간 trade_ready Jaccard 평균이 0.256이고, 12회 전환 중 10회가 0.5 미만이다. 너무 자주 후보가 바뀌면 실행/관찰 안정성이 떨어진다.
- US는 22:23과 22:24처럼 매우 가까운 selection 호출이 있었다. 중복 방지 쿨다운이 필요하다.
- US 04:04 selection에서 `GEHC`가 `trade_ready`에 포함됐지만 `price_targets`가 없었다. 현재 normalizer는 missing target 자체를 강하게 실패로 보지 않으므로 Path B 실행 계약에는 방어가 더 필요하다.
- `tune_*`는 KR 14/14, US 11/11 모두 `MAINTAIN`이었다. 일부 wait/cutoff 조정은 있었지만 LLM이 아니어도 규칙으로 충분히 처리 가능한 수준이다.
- `hold_advisor`는 포지션당 bull/bear/neutral 3회 호출이다. KR 30건, US 39건으로 호출 수가 빠르게 늘어난다.
- US 2026-04-29 postmortem은 2026-04-30 05:00에 `Expecting ',' delimiter`로 파싱 실패했다. raw call 파일도 남지 않아 실패 응답 원문을 나중에 품질 분석하기 어렵다.
- KR 2026-04-29은 ops review상 hit=80.0%였지만 세션 PnL은 -1.14%였다. 방향 판단보다 실행/진입가/데이터 결측 리스크가 손익을 좌우했다.

## 시스템 적합성 판단

### 유지해야 할 영역

- 장 시작 `analyst` 3관점 판단은 유지한다. 시장 모드, 비중, 전략 방향을 사람이 검토 가능한 형태로 남기는 효과가 크다.
- `select_tickers`는 유지한다. 후보 분류와 가격 타깃 생성은 현재 시스템에서 가장 Claude 의존도가 높은 핵심 기능이다.
- `postmortem`은 유지한다. 비용은 작고 학습 루프에 직접 연결된다.

### 축소/조건부 전환할 영역

- `intraday_tune`는 이벤트 기반으로 바꾼다. 단순 30/60/90분 주기 호출은 비용 대비 정보량이 낮다.
- `hold_advisor`는 3개 API 호출을 1개 API 호출로 합친다. 한 프롬프트에서 bull/bear/neutral votes를 모두 반환하게 하면 의사결정 구조는 유지하면서 호출 수와 지연을 줄일 수 있다.
- analyst R2는 조건부 호출 후보이다. 어제는 KR/US 모두 R2 변경이 없었다. 다만 US처럼 R1 의견이 갈리는 장에서는 R2가 안전장치 역할을 하므로 즉시 제거보다 "분산이 작으면 생략"이 낫다.

## 최적화 제안

### 1. select_tickers 출력 축소

효과: 전체 토큰 15~25% 절감 가능.

- `watch_only` 종목은 이유를 1줄 이하로 강제한다.
- `price_targets`는 `trade_ready`에만 유지한다. 이미 규칙은 있지만 프롬프트와 normalizer 검증을 더 강하게 한다.
- `entry_basis_tags`, `exit_basis_tags`, `invalidation_conditions`는 장중 rescreen에서는 생략하거나 최대 2개로 제한한다.
- 재선정 때 전체 후보 대신 `새 후보 + 기존 trade_ready + watchlist 상위 변화분`만 보내는 delta prompt를 도입한다.

### 2. selection 호출 쿨다운/변화량 게이트

효과: US 중복 호출과 낮은 변화 호출 제거.

- 직전 selection 후 5분 이내에는 강제 재호출 금지. 단, 모드 변경/급락/브로커 오류 해소 같은 이벤트는 예외.
- 후보 상위권 변화가 작으면 기존 selection 재사용.
- KR처럼 churn이 높은 시장은 반대로 "최소 유지 시간"을 둔다. trade_ready가 한번 선정되면 15~30분은 명확한 veto 없이는 교체하지 않는다.

### 3. hold_advisor 단일 호출화

효과: hold_advisor 토큰/호출 약 60% 이상 절감 가능.

- 현재 구조: 포지션 1개 판단에 `hold_advisor_bull`, `hold_advisor_bear`, `hold_advisor_neutral` 3회 호출.
- 개선 구조: 한 번의 프롬프트에서 `votes.bull`, `votes.bear`, `votes.neutral`, `final_action`, `trail_pct`를 반환.
- 품질 장점: 같은 시장 컨텍스트에서 세 관점이 동시에 비교되어 응답 간 불일치가 줄어든다.
- 운영 장점: TP 도달/트레일링 상황에서 지연이 줄어 매도 의사결정 반응성이 좋아진다.

### 4. intraday_tune 이벤트 기반 전환

효과: KR+US 기준 약 38,482토큰 절감 가능.

- 어제 KR 14건, US 11건 모두 `MAINTAIN`.
- 호출 조건을 아래 이벤트로 제한한다:
  - 장중 지수 급변.
  - VIX/환율 급변.
  - 보유 포지션 손익 급변.
  - trade_ready 전환율 급락 또는 watch_miss 급증.
  - 기존 runtime gate와 충돌하는 신규 신호 발생.
- 단순 시간 경과 호출은 deterministic rule로 대체한다.

### 5. postmortem raw-first 저장/복구

효과: 학습 루프 품질 안정화.

- 현재 `minority_report/postmortem.py`는 JSON 파싱 성공 후 raw call을 저장한다. 파싱 실패 시 원문이 남지 않는다.
- API 응답을 받으면 파싱 전에 raw를 먼저 저장하거나, 실패 전용 raw 파일을 저장한다.
- 파싱 실패 시 1회 repair prompt 또는 local JSON repair를 시도한다.
- 실패 fallback은 이미 policy memory 오염을 막도록 고쳐졌지만, 품질 분석용 raw 보존은 별도 개선이 필요하다.

### 6. Path B 계약 검증 강화

효과: "trade_ready인데 실행 가격 없음" 방지.

- `trade_ready`에 포함된 종목은 `price_targets`가 없으면 Path B용 trade_ready에서 제외한다.
- Path A 관찰용 trade_ready와 Path B 실행용 trade_ready를 명시적으로 분리한다.
- 누락 발생 시 warning뿐 아니라 audit event로 남긴다.

## 우선순위

| 우선순위 | 작업 | 기대 효과 | 리스크 |
|---:|---|---|---|
| 1 | postmortem raw-first 저장 + repair | 학습 데이터 손실 방지 | 낮음 |
| 2 | hold_advisor 3회 호출을 단일 호출로 통합 | 호출 수/지연 즉시 절감 | 중간 |
| 3 | intraday_tune 이벤트 기반 전환 | 비용 절감 큼 | 중간 |
| 4 | select_tickers delta/rescreen 쿨다운 | 최대 비용원 최적화 | 중간~높음 |
| 5 | analyst R2 조건부 호출 | 비용 절감 | 중간 |
| 6 | Path B price target 계약 강화 | 실행 안전성 강화 | 낮음 |

## 예상 절감

보수적으로 잡으면 다음 수준이 가능하다.

- hold_advisor 단일 호출화: 하루 KR+US 세션 기준 약 2.5만~3만 토큰 절감.
- intraday_tune 이벤트화: 약 2만~3.8만 토큰 절감.
- select_tickers 출력 축소/쿨다운: 약 4만~7만 토큰 절감 가능.

합산하면 어제와 같은 운영일 기준 총 36만 토큰대 사용량을 22만~27만 토큰대로 낮출 여지가 있다. 단, `select_tickers`는 손익에 직접 연결되므로 한 번에 크게 줄이지 말고 shadow 비교 후 적용해야 한다.

## 최종 판단

Claude 사용 자체는 시스템에 맞다. 문제는 "어디에 Claude를 쓰느냐"보다 "얼마나 자주, 어떤 계약으로 부르느냐"다.

가장 좋은 방향은 `select_tickers`와 `postmortem`은 품질을 강화하고, `hold_advisor`와 `intraday_tune`은 호출 구조를 줄이는 것이다. 즉 판단 핵심은 유지하고, 반복 확인성 호출을 줄이는 쪽이 운영상 가장 안전하다.
