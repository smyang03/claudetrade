# 독립 연구 전략 2종 추가 — 2026-09-09

상태: 신호 계산 엔진·일일 실행 등록·연구 카드 코드 구현. 실주문/가상 체결/손익 정산 엔진은 연결하지 않았다. 두 전략 모두 RESEARCH_ONLY이며 기존 forward 승격/캐너리 큐와 분리한다. 아래 숫자는 연구 초기 계약이지 최적화된 값이나 기대수익 약속이 아니다.

## 1. r_multiasset_trend_v1

- 연구 유니버스: 미국 상장 SPY/EFA/IEF/GLD. 기존 KR 상장 코어 ETF 북의 ew/absmom과 다른 계약·다른 표본이다. 중복 전략 간 성적을 합산하지 않는다.
- 분할·분배금 조정 주가 253봉 이상, 동일 최종 세션·정렬·중복 없음·정보 가용시각 확인. 하나라도 부족하면 전체 배분 차단.
- 126·252세션 수익률 모두 양수이고 200세션 평균 위인 자산만 보유 대상.
- 전체 4자산의 63세션 변동성 역수로 기준 비중을 계산한 뒤 자산당 25% 상한. 탈락 자산 몫과 상한 초과 몫은 현금, 통과 자산에 재배분하지 않음. 선물·공매도·레버리지 없음.
- 월말 확정 데이터로 다음 월 첫 개장 시각에 리밸런스한다는 계약. 일일 계산은 관측일 뿐, 월중에는 새 리밸런스 신호 없음. 같은 종가를 보고 같은 종가에 체결됐다고 기록하지 않는다.
- 비교 대상: 같은 ETF의 월간 등가중. 계좌 통화·환율·분배금·수수료·세금·정수주·미체결 적용 전에는 수익 비교 금지.

## 2. r_earnings_quality_drift_v1

- 기존 c_us_earn_gap은 가격반응 전략이다. 신규 전략은 당시 실적 기대치·실제치·가이던스가 필요하며 가격 갭으로 대체하지 않는다.
- 양수인 비교 가능한 반복적 희석 EPS가 발표 전 기대치 대비 10% 이상, 매출이 2% 이상 상회. 음수 EPS에서의 개선은 이 버전 대상 아님.
- 동일 기간·동일 지표의 가이던스 중간값 상향. 이전 가이던스/기대치는 발표 전에 관측했어야 하고, 실제치/새 가이던스는 판단 시점까지 수신했어야 함. 원문 출처 필수. 회계기준·통화·단위 정규화는 입력 공급자가 검증해야 한다.
- 공개 후 7달력일 이내, 완결 반응봉 종가 > 시가 및 전일 종가, 거래량 ≥ 직전20세션 평균의 1.5배, 직전20세션 평균 거래대금 ≥2천만 USD.
- EPS 서프라이즈 내림차순, 동률은 ticker/event_id 사전순, 하루 K1 후보. 한 event_id의 중복 입력은 제거.
- 실행 계약 명세: 다음 세션 시가, 최대4포지션·종목당25%, 사건당1회·동일종목 중첩 금지. 진입일 포함20세션 만기 종가 또는 종가 기준 −8% 이탈 확인 후 다음 시가 청산. 손절 가격 보장 없음. 고정 익절 없음.
- 위 보유/청산/현금 규칙은 **향후 실행 엔진에 요구하는 명세**다. 현재 구현은 후보 선정까지만이며 신호가 정수주 주문이나 체결을 의미하지 않는다. 동일 사건의 며칠 연속 관측을 여러 거래로 세면 안 된다.

## 입력과 실행

기본 입력: `data/shadow/independent_research_inputs_v1.json`.

공통 필드:

```
schema_version: independent_research_inputs_v1
source: 실제 입력 공급자/버전
observed_at, cutoff_at: timezone 포함 ISO timestamp
asof_session, entry_session: YYYY-MM-DD
prices: ticker -> {price_basis: split_and_distribution_adjusted,
                  bars: [{session, known_at, adjusted_close}, ...]}
earnings: [{event_id, ticker, published_at, estimate_observed_at,
 actual_observed_at, source_document, eps_basis: comparable_recurring_diluted,
 eps_actual, eps_estimate, revenue_actual, revenue_estimate,
 previous_guidance_observed_at, guidance_observed_at,
 guidance_period, previous_guidance_period, guidance_metric, previous_guidance_metric,
 guidance_mid, previous_guidance_mid,
 reaction_bar: {session, closed_at, open, close, previous_close, volume,
                prior20_mean_volume, prior20_mean_dollar_volume}}]
```

`known_at`은 조정가격 스냅샷을 실제 사용할 수 있었던 시각이며 미래 재수정 데이터를 과거로 소급하면 안 된다. 반응봉 평균에는 반응봉 자체를 넣지 않는다. 모든 가격은 양수 유한값, 통화·단위·EPS 기준은 실제치와 기대치 사이 동일해야 한다.

실행: `python tools/independent_research_watch.py`.

- 거래소 달력으로 최신 완결 세션과 미래 진입 세션을 확인한다. 달력 오류/미래 입력/오래된 입력/결측은 차단.
- `data/analysis/independent_research_report.json`: 최신 진단.
- `data/shadow/independent_research_observations.jsonl`: 입력 스냅샷·입력 지문·계약 지문 포함 관측 이력. 날짜/입력/계약이 같은 재실행은 중복 기록하지 않음. 거래 원장이 아니며 forward 실현손익 표본 아님.
- 기존 `refresh_event_ledgers.py`에 독립 실행 단계 추가. 대시보드 `/virtual` 연구 카드에 상태·신호 수·차단 사유 표시 코드 추가. 서버 재시작은 하지 않아 현재 실행 중인 서버에는 아직 카드가 없을 수 있다.

## 현재 제한과 다음 작업

검증 가능한 입력 공급 어댑터는 아직 없다. 기존 캘린더는 기대 EPS/실제 EPS의 일부 관측 이력을 제공하지만 매출/비교 가능한 가이던스까지 보장하지 않는다. 기존 CSV를 분배금 조정 시계열로 단정하지 않는다. 따라서 실제 데이터가 갖춰질 때까지 BLOCKED가 정상이다. 테스트용 가짜 입력을 운영 입력으로 만들지 않았다.

다음은 입력 공급자의 시점/회계/가격 기준을 검증해 연결하고, 한 전략씩 정수주·현금·실제 체결 시각·비용을 적용한 실행 쉐도우를 구현하는 것이다. 현 단계에는 수익률·샤프·승률을 표시하지 않는다. 실주문 스위치, 캐너리 정책, 기존 원장은 변경하지 않았다.

## 검증

신규 28건 + 기존 forward/canary 안전 테스트 34건 = 62건 통과. 미래 정보·결측·비정상 수치·수정주가 미확인·미완결 역사·발표 후 기대치·가이던스 불일치·이미 지난 최초 진입 시각·관측 중복을 검사했다. Python 컴파일과 수정 대상 diff 공백 검사 통과. 최초 실제 실행은 입력 파일이 없어 두 전략 BLOCKED, 관측 원장에 거래가 아닌 데이터 대기 상태를 기록했다. 커밋·푸시·재시작은 하지 않았다.
