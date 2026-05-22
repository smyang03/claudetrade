# Claude Selection Compact Output Review - 2026-05-12

## 요약

Claude selection 응답이 잘려서 `candidate_actions`, `recommended_strategy`, `price_targets`가 누락되는 문제가 재발했다. 단순히 출력 토큰 상한을 높이는 방식은 실제 API 테스트에서 비효율적이고 불안정했다. 최종 개선 방향은 Claude 응답을 사람 설명용 JSON이 아니라 시스템 실행용 compact machine schema로 바꾸는 것이다.

최종안:

- `CLAUDE_SELECTION_COMPRESSED_MAX_TOKENS=4000`
- 출력은 `wl`, `tr`, `ca` 3개 top-level key 중심으로 제한
- 자연어 설명, 장문 reason, sizing/risk budget, 중복 price target 제거
- 매수 판단에 필요한 action, strategy, confidence, freshness, blockers, invalidation, price target만 유지
- `stop_reason == max_tokens`이면 정상 응답으로 처리하지 않음

## 왜 했는가

2026-05-11 23:43 US selection raw call에서 primary 응답이 출력 상한에 걸려 JSON이 `price_targets.NVDA.target_basis` 중간에서 잘렸다.

그 후 lightweight retry는 watchlist/reasons만 복구하고 `trade_ready=[]`, `price_targets` 금지 구조로 동작했다. 하지만 별도 action/price plan 재요청이 구현되어 있지 않아 최종 meta에 아래 필드가 비었다.

- `candidate_actions=[]`
- `recommended_strategy={}`
- `price_targets={}`
- `trade_ready=[]`

그 결과 감시 루프에서 후보는 남았지만 실행 가능한 전략이 없어 `missing_strategy`로 막히는 상황이 발생했다.

이번 검토 목적은 다음 두 가지였다.

- 출력 토큰 상한을 크게 높이면 해결되는지 확인
- 설명을 제거하고 시스템 필드만 남긴 compact schema가 품질과 안정성을 유지하는지 확인

## 테스트 방법

실제 운영 로그의 동일 입력을 사용했다.

- 기준 raw call: `logs/raw_calls/20260511_US_select_tickers_234323393133_2f5b474029.json`
- 시장: US
- 상황: intraday live, MODERATE_BULL
- 모델: `claude-sonnet-4-6`
- API 직접 호출
- 평가 기준:
  - `stop_reason`
  - JSON parse 성공 여부
  - 출력 tokens
  - 응답 시간
  - watchlist coverage
  - candidate_actions coverage
  - strategy 누락 여부
  - actionable 종목의 price_targets 누락 여부
  - `pt.ref`와 입력 `p=` 가격 일치 여부

API 키 값은 출력하거나 기록하지 않았다.

## 테스트 케이스

| 케이스 | 설명 | max_tokens |
|---|---|---:|
| 기존 full prompt | 기존 selection prompt 그대로, 상한만 증가 | 4000 / 6000 / 8000 / 12000 |
| compact override | 기존 prompt 앞에 강한 압축 지시 추가 | 4000 |
| selection-only 압축 | 후보 선별만 출력하고 실행계획 제거 | 2500 |
| machine compact top10 | 시스템용 compact schema, watchlist 10개 | 2500 |
| machine compact top15 | 시스템용 compact schema, watchlist 15개 | 3000 / 4000 |

## 테스트 결과

| 방식 | 결과 | 출력 tokens | 시간 | 주요 결과 |
|---|---|---:|---:|---|
| 기존 full prompt, 4000 | 실패 | 4000 | 약 60초 | `stop_reason=max_tokens`, JSON parse 실패 |
| 기존 full prompt, 6000 | 실패 | 6000 | 약 84초 | `stop_reason=max_tokens`, JSON parse 실패 |
| 기존 full prompt, 8000 | 실패 | 8000 | 약 108초 | `stop_reason=max_tokens`, JSON parse 실패 |
| 기존 full prompt, 12000 | 성공 | 8980 | 약 106초 | JSON 성공, 하지만 출력 과다 |
| compact override, 4000 | 성공 | 2447 | 약 31초 | `candidate_actions` 10/10, `price_targets` 5/5 |
| selection-only, 2500 | 성공 | 621 | 약 12초 | watchlist 15, trade_ready_seed 8 |
| machine compact top10, 2500 | 성공 | 1281 | 약 19초 | `candidate_actions` 10/10, price target 정상 |
| machine compact top15, 3000 | 실패 | 3000 | 약 42초 | JSON 잘림 |
| machine compact top15, 4000 | 성공 | 1793 | 약 28초 | `candidate_actions` 15/15, strategy 15/15, price target 정상 |

## 결과 해석

출력 상한만 높이는 방식은 권장하지 않는다.

기존 full prompt는 12000까지 올려야 JSON이 완성됐다. 이 경우 출력이 8980 tokens까지 증가했고 응답 시간도 100초 이상 걸렸다. 또한 불필요한 필드가 많고 `candidate_actions`가 watchlist보다 많은 30개까지 생성되어 시스템 처리 품질이 오히려 떨어졌다.

반면 machine compact top15는 4000 상한에서 실제 출력 1793 tokens로 끝났고, 필요한 실행 필드가 모두 채워졌다.

확인된 compact top15 품질:

- watchlist: 15개
- trade_ready: 5개
- candidate_actions: 15/15
- action coverage: 100%
- strategy 누락: 없음
- actionable: 8개
- actionable price_targets 누락: 없음
- `pt.ref`와 입력 `p=` 불일치: 없음
- extra top-level key: 없음
- extra item key: 없음

즉, 설명을 줄인다고 품질이 낮아진 것이 아니라, 시스템이 쓰는 필드만 강제하니 응답 안정성과 실행 품질이 좋아졌다.

## 최종 개선안

Claude selection 응답을 compact machine schema로 고정한다.

허용 top-level key:

```json
{
  "wl": ["NVDA", "QCOM"],
  "tr": ["NVDA"],
  "ca": []
}
```

`ca` item 허용 key:

```json
{
  "t": "NVDA",
  "a": "BUY_READY",
  "s": "opening_range_pullback",
  "c": 0.72,
  "fr": "FRESH",
  "mat": "CONFIRMED",
  "ceil": "BUY_READY",
  "rc": "OR_PULLBACK_CONFIRMED",
  "blk": [],
  "inv": "break_OR_low",
  "pt": {
    "ref": 218.76,
    "lo": 216.5,
    "hi": 219.5,
    "tgt": 226.0,
    "stp": 213.5,
    "d": 1,
    "cf": 0.72
  }
}
```

내부 변환:

- `wl` -> `watchlist`
- `tr` -> `trade_ready`
- `ca[].t` -> `ticker`
- `ca[].a` -> `action`
- `ca[].s` -> `strategy`, `recommended_strategy`
- `ca[].c` -> `confidence`
- `ca[].fr` -> `freshness_verdict`
- `ca[].mat` -> `setup_maturity`
- `ca[].ceil` -> `action_ceiling_ack`
- `ca[].rc` -> `reason_code`
- `ca[].blk` -> `blocking_factors`
- `ca[].inv` -> `invalidation_condition`
- `ca[].pt` -> `price_targets`

## 검증 규칙

정상 응답 조건:

- `stop_reason != max_tokens`
- JSON parse 성공
- top-level key가 `wl`, `tr`, `ca`만 존재
- `ca`가 `wl` 전 종목을 커버
- `ca[].s` strategy 누락 없음
- `tr`에는 `BUY_READY`, `PROBE_READY` 종목만 포함
- `BUY_READY`, `PROBE_READY`, `PULLBACK_WAIT`는 `pt` 필수
- `WATCH`, `AVOID`는 `pt` 생략
- `pt.ref`는 입력 `p=`와 일치해야 함

실패 처리:

- `stop_reason == max_tokens`이면 정상 응답으로 처리하지 않음
- JSON parse 실패 시 partial success로 승격하지 않음
- `ca` coverage 부족 시 trade_ready 승격 금지
- strategy 누락 시 해당 종목 WATCH 처리
- actionable인데 `pt`가 없으면 trade_ready 승격 금지

## 기대 효과

- Claude 출력 잘림 감소
- 토큰 사용량 감소
- 응답 시간 감소
- `candidate_actions: []` 누락 문제 감소
- `missing_strategy` 감소
- watchlist 후보가 전략/가격목표 없이 남는 상황 감소
- 사람 설명용 필드 제거로 시스템 처리 안정성 증가

## 결론

최대 출력 토큰을 크게 올려서 해결하는 방식은 비싸고 느리며 과다 출력 문제가 있다. 실제 테스트에서 기존 full prompt는 12000 상한에서야 성공했고 출력이 8980 tokens까지 증가했다.

최종 방향은 compact machine schema다. 동일 입력에서 compact top15는 4000 상한, 실제 1793 output tokens로 성공했고 시스템 실행에 필요한 필드가 모두 채워졌다.

따라서 구현은 `CLAUDE_SELECTION_COMPRESSED_MAX_TOKENS=4000`과 compact schema 파서/정규화/검증 로직을 기준으로 진행한다.
