# Claude 매수 판단·Hold Advisor 실데이터 오프라인 검증

- 기준 시각: 2026-07-23 23:52 KST
- 검증 구간: Judge 2026-07-01~2026-07-23, Hold 반사실은 가용 6~7월 원장
- 검증 원칙: 기존 Claude 호출 로그만 사용, 이번 검증에서 Claude API 호출 0건
- 재현 도구: `tools/offline_claude_decision_review.py`
- 고정 블라인드 판정: `docs/reports/verify_20260723/codex_offline_decision_blind_verdicts.json`
- 집계 결과: `docs/reports/verify_20260723/offline_claude_decision_summary_20260723.json`
- 주의: 장중 프로세스가 로그를 계속 추가하므로 raw call 수는 실행 시점에 따라 소폭 변할 수 있다.

## 1. 결론

현재 Claude를 **종목을 고르면 곧바로 사게 하는 최종 매수 허가자**로 쓰는 근거는 부족하다.

시장별로 역할을 분리하면 결론은 다음과 같다.

| 기능 | KR | US | 현재 판단 |
|---|---:|---:|---|
| `BUY_READY` 즉시 매수 | 표본 없음 | 고유 6건, 60분 gross 평균 -0.313%, 6/6 음수 | 실거래 확대 금지, shadow |
| `PULLBACK_WAIT` 가격 계획 | 25계획 중 19체결, 비용 후 평균 +1.077%, 중앙값 +0.830% | 34계획 중 23체결, 비용 후 평균 -0.704%, 중앙값 -1.980% | KR 조건부 유지, US shadow/재설계 |
| Hold Advisor의 손절 판단 | 모델에 맡기면 안 됨 | 모델에 맡기면 안 됨 | hard stop은 규칙이 먼저 실행 |
| Hold Advisor의 이익 보호·연장 | 현재 018880 한 건은 정상 동작 | 과거 SELL 판별력이 약함 | 제한된 bounded-hold 보조자로 사용 |
| 판단 결과 학습 연결 | PathB 청산 결과 누락 | 같은 구조적 위험 | P0 배선 수정 필요 |

따라서 권장 운영 구조는 다음과 같다.

1. 규칙 엔진이 데이터 신선도, 가격 일치, hard stop, 비용, 수량 가능성을 먼저 확정한다.
2. Claude는 이 경계 안에서 `REJECT / WAIT / PULLBACK 계획`과 이익 포지션의 `bounded HOLD / SELL`만 제안한다.
3. 즉시 매수는 별도의 시장별 실측 기준을 통과할 때까지 shadow로 둔다.
4. 모든 판단은 실제 대안 정책과 짝지은 net 반사실 결과로 평가한다.

## 2. 다른 모델이 진행 중인 recheck 큐 검증과의 차이

두 작업은 중복이 아니라 직렬로 연결되는 서로 다른 문제다.

| 작업 | 묻는 질문 | 입력 | 출력 |
|---|---|---|---|
| recheck 큐 선별 실험 | 고정된 예산 10개로 대기 후보 중 무엇을 다시 볼 것인가 | queued 후보 383개와 만료·소비 이벤트 | 큐 우선순위 정책 |
| 이번 검증 | 실제로 다시 본 뒤 Claude의 BUY/WAIT/SELL 판단은 유효한가 | raw prompt, Claude 응답, 분봉, 체결·청산 원장 | 판단 역할·시장별 허용 범위 |
| 이번 데이터 배선 점검 | 판단 결과가 다음 학습과 검증으로 정확히 돌아오는가 | PathB 계획, Hold JSONL, 성과 DB | 누락·오라벨링 수정 목록 |

recheck 큐 실험은 계속할 가치가 있다. 다만 큐 순위를 `재호출률`이나 gross 후보 수익으로 최적화하면 안 된다. 이번 검증에서 확인한 시점·가격 계약을 먼저 통과시킨 뒤, **세션 단위 비용 후 손익과 실제 실행 가능성**으로 A/B 해야 한다. 그렇지 않으면 더 신선하거나 더 유망한 후보가 아니라, 입력이 깨진 판단을 더 빨리 호출하는 정책이 될 수 있다.

## 3. 검증 방법

### 3.1 신뢰도 순서

결과는 아래 순서로 신뢰했다.

1. 실제 체결·청산과 비용 후 원장
2. Claude 응답 시각 이후 첫 분봉부터 시작한 no-lookahead 재생
3. 주문 생성 시각을 보존한 `PULLBACK_WAIT` 체결·청산 인과 재생
4. 후보 audit의 forward gross 결과

후보 audit는 실행 주문의 수익이 아니고 sparse 상태가 섞여 있다. 실제로 KR `REJECT`를 audit로 보면 60분 평균 +2.333%였지만, 같은 요청을 로컬 분봉으로 재생하면 -1.412%였다. 따라서 본 결론에는 로컬 분봉과 실체결을 우선했다.

### 3.2 직접 사례 주입

raw prompt에서 당시 Claude가 받은 입력을 복원하고 Claude action과 미래 결과를 가린 상태에서 Codex가 먼저 판정했다. 판정을 JSON에 고정한 뒤 Claude 응답과 미래 분봉·반사실을 공개했다.

- Judge 6건: NOK, AMD, 001210, ORCL, 005930, DELL
- Hold 6건: AMZN, 018000, 080220, CRDO, CIEN, AAL
- 결과를 보고 유리한 사례를 고르지 않도록 계층별 deterministic hash로 선택했다.
- 005930은 raw prompt 일부가 초기 extractor에서 잘려 Codex가 Claude보다 적은 정보를 본 사실이 확인되어 승패 비교에서 제외했다. 이 제외 자체가 스키마 확인을 먼저 해야 한다는 하네스 규칙의 사례다.

## 4. Judge 입력 계약 점검

### 4.1 전체 현황

2026-07-01~23 raw call 571건 중 파싱 가능한 판단은 480건이었다. 60분 로컬 분봉 결과는 355건에 붙었다.

입력 신선도와 가격 계약은 다음과 같았다.

| 항목 | 실측 |
|---|---:|
| feature age 계산 가능 | 472 |
| feature age 중앙값 | 563.1초, 약 9.4분 |
| 5분 초과 | 340 |
| 15분 초과 | 156 |
| candidate.price와 feature.current 비교 가능 | 452 |
| 가격 차이 0.5% 초과 | 258 |
| 가격 차이 2% 초과 | 112 |
| Claude reference가 feature.current에 더 가까움 | 320 |
| Claude reference가 candidate.price에 더 가까움 | 132 |

한 prompt 안에 서로 다른 시각의 가격과 기술지표가 들어가지만 어느 값이 기준 가격인지 명시되지 않는다. `single_symbol_judge`의 즉시매수 plan validator는 target, stop, R/R을 `features.current_price`와 비교하지만, Claude가 반환한 `reference_price`가 canonical current와 같은지 강제하지 않는다.

이 문제는 단순 데이터 품질 저하가 아니다. Claude가 오래된 VWAP·OR·수익률과 더 새로운 현재가를 합쳐 존재하지 않았던 시장 상태를 판단할 수 있는 **시점 혼합 오류**다.

### 4.2 ORCL 사례

`logs/raw_calls/20260709_US_single_symbol_judge_225555894857_b3e7b99f60.json`

- 호출: 22:55:55 KST
- feature known_at: 22:33:18 KST
- feature current: 142.26
- candidate.price: 147.62
- 기존 VWAP: 141.30
- feature는 약 22.6분 늦었고 Claude는 147.62를 reference로 사용하면서 오래된 VWAP과 결합했다.

Claude와 Codex 모두 `WAIT`를 골랐지만 이후 강하게 상승했다. 이 사례를 모델의 순수한 오판으로만 세면 안 된다. 입력 스냅샷이 같은 시점의 시장을 표현하지 않았기 때문이다.

### 4.3 필요한 입력 계약

Judge에 전달하는 모든 시장 데이터는 아래 구조를 가져야 한다.

```text
decision_as_of
canonical_price
canonical_price_as_of
feature_as_of
feature_age_sec
price_source
price_conflict_pct
news_as_of
missing_fields
missing_reason
```

- `canonical_price` 하나만 plan 검증의 기준으로 사용한다.
- feature와 quote가 허용 시차를 넘으면 호출 전에 갱신한다.
- 갱신 실패 시 후보를 삭제하지 말고 `WAIT_DATA` 또는 보수적 ceiling으로 남긴다.
- 외부 뉴스·실적·기업행사 데이터로 사전 채울 수 있으면 point-in-time 시각과 출처를 함께 DB에 저장한다.
- 외부 데이터가 없으면 빈 문자열로 숨기지 말고 `missing_reason`을 기록해 모델과 검증기가 같은 결손을 보게 한다.

## 5. 매수 판단 실측

### 5.1 US `BUY_READY`

호출 중복을 제거한 세션×티커 고유 6건은 WDC, DELL, CBRS, WULF, SMCI, ONDS였다.

| 지표 | 결과 |
|---|---:|
| 고유 표본 | 6 |
| 60분 gross 평균 | -0.3127% |
| 60분 gross 중앙값 | -0.3204% |
| 양수 비율 | 0/6 |
| US 왕복비용 proxy 반영 평균 | 약 -0.813% |

일부는 30분 안에 잠깐 올랐다가 60분에 되돌렸다. 이는 현재 `BUY_READY`가 지속 추세보다 짧은 초기 모멘텀에 반응할 가능성을 시사한다. 다만 Claude plan의 보유기간은 2~3일이고 최근 표본은 아직 완전히 성숙하지 않았으므로 “장기적으로 확정 손실”이라고 단정할 수는 없다.

그럼에도 현재 표본은 `SINGLE_SYMBOL_JUDGE_ALLOW_BUY_READY=true`를 실거래 확대할 근거가 아니다. 허용 플래그가 true여도 운영 라우팅은 shadow 또는 최소 규모로 제한해야 한다.

직접 사례 DELL에서는 Claude가 `BUY_READY`, Codex가 `PULLBACK_WAIT`를 선택했다. 이후 60분 수익은 호출별 약 -0.70~-0.81%였다. 이 사례에서는 즉시매수보다 대기 판단이 안전했다.

### 5.2 KR `PULLBACK_WAIT`

Claude가 만든 buy zone을 “응답 생성 이후에만 체결 가능”하게 하고, KR 왕복비용 0.21%를 차감해 재생했다.

| 지표 | 결과 |
|---|---:|
| 고유 계획 | 25 |
| 체결 | 19 |
| zone 미접촉 | 6 |
| 체결 후 비용 순손익 평균 | +1.0769% |
| 중앙값 | +0.8300% |
| 승률 | 52.63% |

실운영 PathB에서는 7월 8일 이후 KR plan 3건, 체결 1건, 청산 1건이며 그 한 건이 018880의 +1.3495% net이다. 역사 재생은 긍정적이지만 라이브 표본은 한 건이므로 “검증 완료”가 아니라 **조건부 유지**가 맞다.

### 5.3 US `PULLBACK_WAIT`

같은 인과 재생에서 US 왕복비용 0.50%를 차감했다.

| 지표 | 결과 |
|---|---:|
| 고유 계획 | 34 |
| 체결 | 23 |
| zone 미접촉 | 9 |
| 생성 뒤 분봉 없음 | 2 |
| 체결 후 비용 순손익 평균 | -0.7044% |
| 중앙값 | -1.9800% |
| 승률 | 30.43% |

KR과 정반대다. 하나의 Claude prompt/policy를 양 시장에 같이 적용할 근거가 없다. 현재 실운영 US PathB plan은 8건이지만 체결 0건이라 실현손실은 없었다. 그러나 이는 전략 품질 통과가 아니라 실행 표본 부재다.

US는 신규 plan을 shadow로 돌리고, 멀티데이 convex 전략이 별도로 검증될 때까지 현재 KR형 눌림 로직의 실거래 확대를 막아야 한다.

### 5.4 WAIT와 REJECT

고유 세션×티커의 no-lookahead 60분 결과는 다음과 같다.

| 시장·action | n | 평균 gross | 중앙값 | 양수 비율 |
|---|---:|---:|---:|---:|
| KR WAIT_RECHECK | 87 | -0.0207% | -0.1984% | 44.83% |
| KR REJECT | 9 | -1.4117% | -1.2597% | 44.44% |
| US WAIT_RECHECK | 103 | -0.0940% | -0.0751% | 41.75% |
| US PULLBACK_WAIT의 호출 직후 60분 | 30 | -0.1976% | -0.0179% | 50.00% |

KR REJECT는 평균 기준으로 위험 후보를 거르는 역할을 했다. WAIT도 평균적으로 큰 기회비용을 만들었다고 보기는 어렵다. 다만 ORCL처럼 입력 시점 혼합 때문에 놓친 급등은 별도 오류군으로 분리해야 한다.

## 6. Hold Advisor 실측

### 6.1 현재 triage 구조

현재 로그 29건의 분포는 다음과 같다.

| 항목 | 결과 |
|---|---:|
| SELL / HOLD | 17 / 12 |
| AUTO_SELL / INTRADAY / PRE_CLOSE | 4 / 15 / 10 |
| challenge 사유가 있는 결정 | 26 |
| fallback | 4 |
| 결과가 JSONL에 들어온 결정 | 2 |
| 평균 입력 완결성 | 0.7893 |
| 완결성 0.8 미만 | 10 |
| category/driver 불일치 | 2 |

현재 triage+challenge는 29개 판단을 만들었지만 성숙 outcome이 거의 없어 새 구조의 수익성을 판단할 수 없다. challenge가 거의 모든 건에 붙는 것도 triage가 실제 비용·지연을 얼마나 줄이는지 추가 계측이 필요하다는 뜻이다.

### 6.2 과거 SELL 대 3세션 강제 HOLD 반사실

과거 exit 23건의 `realized_net`과 `hold_fwd_net`을 비교하면 다음과 같다.

| 시장 | n | SELL 우위 평균 | SELL 우위 중앙값 | SELL 승률 |
|---|---:|---:|---:|---:|
| KR | 6 | +4.3977%p | +4.6056%p | 66.67% |
| US | 17 | +0.3390%p | -0.4101%p | 47.06% |

KR은 평균과 중앙값 모두 SELL 쪽이 우세하지만 n=6이다. US는 평균만 소폭 양수이고 중앙값과 승률은 HOLD 쪽이다. 즉 미국 Hold Advisor의 과거 SELL은 일반적인 alpha 판별기라기보다 일부 큰 하락을 피하는 대신 큰 상승도 잘라내는 tail-risk reducer에 가깝다.

중요한 제한이 있다.

- DB 컬럼명은 `realized_net`, `hold_fwd_net`이지만 현재 collector는 gross `pnl_pct`를 넣는다.
- HOLD 대안은 보호 스톱과 재검토를 가진 실제 bounded-hold가 아니라 무조건 3세션 종가 보유다.
- 현재 triage prompt와 과거 legacy prompt가 섞여 있다.

따라서 이 표는 새 Hold Advisor의 인과 성과가 아니라, 기존 SELL 성향의 방향을 보는 진단값이다.

### 6.3 직접 사례

#### 018880: 정책 동작 성공, outcome 배선 실패

실제 경로는 다음과 같다.

1. 09:23:58, 3,655원 진입
2. 10:19:20, HOLD와 target extension, 보호 스톱 3,650원 저장
3. 10:54:54, HOLD 재판정, 보호 스톱 3,680원으로 상향
4. 11:04:45, 시장 급반전 근거로 SELL 정책 저장
5. 11:04:45 주문, 11:04:56 3,712원 체결 완료
6. 비용 후 +1.3495%

`runtime/pathb_runtime.py`의 정책 저장·평가 경로는 active policy, valid_until, protective stop breach를 실제로 검사한다. 이 사례는 bounded hold가 단순 prompt 문구가 아니라 실행 경로에 연결됐음을 보여준다.

그러나 `_finalize_pathb_sell_close`에는 `trading_bot.py`의 `_record_hold_advisor_outcome`에 해당하는 호출이 없다. 그 결과 018880의 Hold JSONL 여러 행은 청산 뒤에도 `outcome=null`이다. 모델이 맞아도 다음 검증·학습에는 성공이 전달되지 않는다.

#### ONDS: 올바른 stop이 실패로 라벨됨

- Claude 판단 시 손익 약 -1.553%, hard stop 8.24
- 실제 청산 약 -1.799%, 판단 후 체결까지 -0.246%p 추가 악화
- JSONL의 SELL success는 최종 pnl이 0보다 큰지로 판정되어 `false`

손실을 줄인 올바른 stop도 음수로 끝나면 실패가 된다. 또한 hard stop이 이미 충족된 뒤 Claude 응답을 기다리는 구조는 불필요한 손실을 키울 수 있다.

hard stop은 즉시 규칙으로 실행하고 Claude는 사후 설명 또는 더 타이트한 stop만 제안해야 한다. Claude가 hard stop을 지연하거나 취소할 권한을 가지면 안 된다.

#### 275300: 장 마감 뒤 단계 의미가 깨짐

raw challenge에는 `minutes_to_close=-30.3`이 있었지만 결정 로그에서는 null이 되었고 completeness는 minutes 누락으로 기록됐다. 16:00 이후 SELL은 다음 세션 시가 주문으로 큐잉됐는데 stage는 `PRE_CLOSE_CARRY`였다.

논리적 SELL 여부와 별개로 이는 `POST_CLOSE_QUEUE / NEXT_OPEN`으로 분리해야 한다. 그렇지 않으면 pre-close 판단 성과와 overnight queue 성과가 한 집단에 섞인다.

### 6.4 outcome 계약 오류

현재 결과 라벨은 다음 이유로 학습 목표로 쓰기 어렵다.

1. SELL `success`는 SELL이 HOLD보다 나았는지가 아니라 최종 pnl이 양수인지다.
2. SELL `hold_delta_pct`는 판단 시점 pnl과 체결 pnl의 차이로, HOLD 반사실이 아니라 실행 지연·슬리피지다.
3. Hold JSONL patch는 오늘 파일의 같은 ticker 중 최신 미결 행만 갱신한다.
4. PathB 청산은 Hold outcome callback을 호출하지 않는다.
5. `realized_net`과 `hold_fwd_net`은 이름과 달리 gross다.
6. 고정 3세션 HOLD는 실제 bounded-hold 정책과 다르다.

## 7. 논문과 내부 실측의 관계

LLM이 금융 뉴스에서 단기 반응·드리프트 신호를 추출할 수 있다는 연구는 존재한다. Lopez-Lira와 Tang은 뉴스 headline 기반 LLM score가 후속 수익을 예측할 수 있지만 도입 확산과 함께 수익이 감소한다고 보고한다. Chen, Kelly, Xiu도 뉴스 임베딩이 전통 특성에 추가 정보를 제공하며 소형주에서 수일간의 news momentum이 나타난다고 보고한다.

반면 가격 시계열을 직접 보고 단기 수익을 예측하게 하면 LLM이 추세를 과잉 외삽하고, 낙관적이며, 극단 상승을 과소평가하는 등 보정하기 어려운 편향이 나타난다는 결과도 있다. 금융 LLM 평가에서는 look-ahead, survivorship, objective, cost bias를 명시적으로 통제해야 실제 배포 근거가 된다는 최근 검토도 같은 방향이다.

이 문헌은 “Claude가 쓸모없다”거나 “바로 사도 된다”는 어느 쪽도 지지하지 않는다. 더 타당한 해석은 다음과 같다.

- LLM은 시점이 보존된 뉴스·텍스트 해석에서 추가 신호를 만들 수 있다.
- 가격·체결·손절은 deterministic contract와 비용 후 검증이 맡아야 한다.
- LLM의 action을 직접 주문으로 바꾸기 전에 시장·기간별 out-of-sample net 성과를 별도로 증명해야 한다.

참고:

- Alejandro Lopez-Lira, Yuehua Tang, [Can ChatGPT Forecast Stock Price Movements?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4412788)
- Yifei Chen, Bryan T. Kelly, Dacheng Xiu, [Expected Returns and Large Language Models](https://papers.ssrn.com/sol3/Delivery.cfm/4416687.pdf?abstractid=4416687)
- Shuaiyu Chen 외, [What Does ChatGPT Make of Historical Stock Returns?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4941906)
- Yaxuan Kong 외, [Evaluating LLMs in Finance Requires Explicit Bias Consideration](https://arxiv.org/abs/2602.14233)

## 8. 수정 우선순위

### P0 — 결과 해석 전에 반드시 수정

1. **canonical 시점·가격 계약**
   - quote, feature, VWAP, OR, 뉴스에 각각 as_of를 저장한다.
   - 허용 age와 price conflict를 넘으면 refresh한다.
   - refresh 실패는 명시적인 `WAIT_DATA`로 남긴다.
   - plan validator는 Claude reference_price와 canonical_price 일치도까지 검사한다.

2. **hard stop의 LLM 우회**
   - broker-truth 기준 stop breach는 Claude 호출 전에 주문한다.
   - Claude는 hard stop을 느슨하게 하거나 실행을 지연할 수 없다.
   - 호출이 필요하면 비동기 사후 review로 돌린다.

3. **PathB Hold outcome 연결**
   - 모든 Hold 판단에 `decision_id`를 부여한다.
   - PathB 청산 시 ticker가 아니라 decision_id로 관련 HOLD/SELL 판단 전부를 닫는다.
   - 실제 체결 net, 판단 후 drift, bounded-hold 반사실을 별도 필드로 기록한다.

4. **outcome 의미 수정**
   - `success = pnl>0`를 제거한다.
   - `decision_advantage_net = chosen_policy_net - paired_policy_net`으로 정의한다.
   - gross 필드는 gross로 이름을 바꾸고 fee, tax, FX를 분리한다.

### P1 — 시장별 정책

5. **BUY_READY shadow**
   - 시장별 성숙 표본을 통과하기 전까지 실거래 확대를 막는다.
   - US 현재 6건은 모두 60분 음수이므로 true 설정 자체가 매수 근거가 되지 않는다.

6. **KR/US PULLBACK 분리**
   - KR은 제한 규모로 forward 검증을 계속한다.
   - US는 현재 plan 등록을 shadow로 돌리고 멀티데이 convex 조건을 별도 설계한다.

7. **실제 bounded-hold 반사실**
   - 고정 3일 보유 대신 당시 보호 스톱, target, valid_until, reask를 분봉으로 재생한다.
   - SELL/HOLD의 평균뿐 아니라 중앙값, max drawdown, 우측 꼬리 보존율을 같이 본다.

8. **POST_CLOSE 단계 분리**
   - 음수 minutes_to_close를 null로 버리지 않는다.
   - `PRE_CLOSE_CARRY`, `POST_CLOSE_QUEUE`, `NEXT_OPEN_EXECUTION`을 분리한다.

### P2 — recheck 큐 실험과 결합

9. **같은 예산 10개의 선별 A/B**
   - control: 현재 정책
   - treatment 후보: 신선도, price conflict 없음, 실행 가능성, 예상 순가치, 시장별 action prior
   - 평가 단위: 호출 건이 아니라 세션
   - 결과: 비용 후 손익, 체결률, 최대 손실, 놓친 상위 기회, API 지연
   - 만료된 후보는 “나쁜 후보”로 자동 라벨하지 않고, 만료 시점의 실행 가능 반사실을 계산한다.

10. **하네스의 반사실 강제**
    - schema assertion이 통과하지 않으면 성과 결론 생성 금지
    - treatment가 실제로 control과 다른 케이스를 만들었는지 0이 아닌지 확인
    - 미래 결과를 보지 않고 정책·판정을 먼저 파일에 고정
    - 최소 한 개의 실제 입력을 함수 전체 경로에 주입
    - gross가 아닌 net 세션 결과까지 붙기 전에는 “개선”으로 승격 금지

## 9. 승격 기준

다음 기준은 첫 운영안이며 표본이 쌓이면 power analysis로 조정해야 한다.

| 항목 | 승격 기준 |
|---|---|
| 입력 계약 | 호출의 99% 이상에서 as_of 존재, 허용 age 초과 시 자동 refresh/WAIT_DATA |
| 가격 계약 | canonical 대비 0.25% 초과 충돌 0건 또는 명시적 차단 |
| outcome 연결 | PathB를 포함한 청산 decision_id 연결률 100% |
| BUY_READY | 시장별 고유 성숙 거래 최소 30건, 비용 후 평균·중앙값 모두 양수 |
| KR PULLBACK | forward 체결 최소 30건, 비용 후 중앙값 0 이상, 세션 손익 개선 |
| US PULLBACK | shadow 체결 최소 30건에서 비용 후 중앙값 0 이상 전까지 live 금지 |
| Hold Advisor | 시장별 bounded SELL/HOLD pair 최소 30건, net 개선과 drawdown 개선을 함께 확인 |
| hard stop | Claude latency로 인한 추가 손실 0건 |
| recheck A/B | 같은 호출 예산에서 세션 net 개선, 손실 꼬리 악화 없음 |

## 10. 최종 판단

현재 방향인 “Claude가 후보를 해석하고 가격 계획·반증 조건을 만든다”는 것은 맞다. 그러나 “Claude가 고른 종목을 바로 산다”는 결론은 아직 맞지 않다.

가장 근거가 있는 역할은 **KR 눌림 가격 계획 생성기**와 **규칙 경계 안의 이익 보호용 bounded-hold 보조자**다. 가장 근거가 약한 역할은 **US 즉시매수 허가자**, **US KR형 눌림 계획**, **hard stop 최종 결정권자**다.

다른 모델의 recheck 큐 선별 실험은 이 결론과 별개로 진행할 가치가 있다. 다만 그 실험이 올바른 후보를 더 자주 고르게 하더라도, 이번에 확인한 입력 시점 혼합과 outcome 배선 오류를 고치지 않으면 최종 수익 검증은 다시 왜곡된다. 두 작업의 결합 순서는 `입력 계약 → 큐 선별 → Claude 판단 → 규칙 기반 실행 → paired net outcome`이어야 한다.
