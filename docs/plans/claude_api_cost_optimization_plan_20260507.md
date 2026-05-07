# Claude API Cost Optimization Plan - 2026-05-07

## 목적

Claude API 비용을 줄이되, 종목 선정 성능과 방어 판단 품질은 낮추지 않는다.

핵심 원칙:

- 먼저 중복 호출과 저위험 stage를 줄인다.
- `select_tickers`의 의미 정보는 바로 줄이지 않는다.
- prompt/output 압축은 DB와 shadow 로그로 성능 열화가 없음을 확인한 뒤 적용한다.
- hard exit, broker truth, daily loss, force close 같은 시스템 안전 규칙은 Claude 절감 대상이 아니다.

## 분석 소스

- 비용/토큰: `logs/raw_calls/*.json`, `state/live_api_usage.json`
- selection 품질: `data/ticker_selection_log.db`
- PathB/Claude Price 상태: `data/v2_event_store.db`
- candidate action routing: `logs/funnel/candidate_funnel_snapshot_*.jsonl`, `logs/funnel/action_routing_shadow_*.jsonl`
- hold advisor 판단: `logs/hold_advisor/decisions_*.jsonl`

API 재호출은 이번 분석에서 하지 않았다. 기존 raw prompt/response와 DB outcome만으로 1차 판단이 가능했고, selection 재호출은 실제 비용이 들며 결과 변동성까지 있어 shadow 설계 후 최소 샘플로만 하는 편이 낫다.

## 최근 비용 베이스라인

최근 운영 구간 `2026-05-04` ~ `2026-05-06` raw call 기준:

| date | total tokens |
|---|---:|
| 2026-05-04 | 581,194 |
| 2026-05-05 | 587,878 |
| 2026-05-06 | 566,530 |
| average | 578,534/day |

카테고리별 합계:

| category | calls | tokens | share | avg/call |
|---|---:|---:|---:|---:|
| select_tickers | 65 | 617,524 | 35.6% | 9,500 |
| hold_advisor | 380 | 472,776 | 27.2% | 1,244 |
| analyst_consensus | 96 | 462,997 | 26.7% | 4,823 |
| tuner | 68 | 137,436 | 7.9% | 2,021 |
| postmortem | 4 | 42,725 | 2.5% | 10,681 |
| quick_exit | 3 | 2,144 | 0.1% | 715 |

우선순위는 `select_tickers`, `hold_advisor`, analyst consensus 순서다. 다만 analyst consensus는 market judgment 품질에 직접 연결되므로 이번 절감 1차 대상에서는 제외한다.

## 기존 DB 성능 비교

`ticker_selection_log.db`에서 forward 값이 채워진 `2026-04-20` ~ `2026-05-02` 구간:

| market | rows | trade_ready | watch | traded | trade_ready fwd_3d | watch fwd_3d | trade_ready runup_3d | watch runup_3d |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KR live | 387 | 72 | 315 | 14 | +6.19% | -1.76% | +16.85% | +10.81% |
| US live | 667 | 136 | 531 | 9 | +2.49% | +0.85% | +7.81% | +6.83% |

해석:

- 기존 selection은 `trade_ready`와 watch 사이에 성과 차이를 만들고 있다.
- 따라서 prompt 정보량을 무작정 줄이면 성능 열화 위험이 있다.
- 특히 missed watch-only도 크다.
  - KR: watch 315개 중 runup_3d >= 5%가 148개
  - US: watch 531개 중 runup_3d >= 5%가 203개
- weak trade_ready도 존재한다.
  - KR: trade_ready 72개 중 forward_3d <= 0이 37개
  - US: trade_ready 136개 중 forward_3d <= 0이 35개

`2026-05-04` ~ `2026-05-06` 구간은 아직 forward_3d가 채워지지 않았다.

| market | rows | trade_ready | watch | traded |
|---|---:|---:|---:|---:|
| KR live | 124 | 21 | 103 | 7 |
| US live | 209 | 30 | 179 | 3 |

이 구간은 비용 분석에는 쓸 수 있지만 성과 검증에는 아직 이르다.

## 증가 원인

### 1. select_tickers prompt/output 확장

`select_tickers`는 4월 말 이후 다음 정보가 함께 들어가며 커졌다.

- 후보 24~30개 line
- market digest
- intraday context
- brain/correction summary
- tuner context
- recent selection feedback
- decision contract
- sizing contract
- price plan contract
- hard/soft rule contract
- 일부 구간에서 candidate_actions schema

예시: `2026-05-06 US 23:45` selection call

- total 13,347 tokens
- input 8,271 / output 5,076
- prompt chars 18,526
- 후보 line chars 7,897로 prompt의 42.6%
- response 주요 필드 크기:
  - `candidate_actions`: 3,713 chars
  - `price_targets`: 2,475 chars
  - `reasons`: 1,258 chars
  - `veto`: 942 chars

### 2. candidate_actions는 비용이 크지만 성능상 바로 끄면 위험

`2026-05-06 US` candidate funnel snapshot 10건:

- candidate action source:
  - `candidate_actions_v1`: 9건
  - legacy shadow: 1건
- action counts:
  - WATCH 71
  - BUY_READY 11
  - PROBE_READY 14
  - PULLBACK_WAIT 11
  - AVOID 5
- route result:
  - BUY_READY 9
  - PROBE_READY 9
  - PULLBACK_WAIT 7
  - WATCH 60
  - EXPIRED 10

예시:

- `2026-05-06 22:48 US`
  - AMD/NVDA -> `BUY_READY`
  - RIOT/IFF -> `PROBE_READY`
  - INTC/SMCI -> `PULLBACK_WAIT`

이 필드는 단순 설명이 아니라 PlanA/PathB routing에 영향을 준다. 끄면 비용은 줄지만 성능 저하 가능성이 있다.

### 3. hold_advisor stage contract 확장

`hold_advisor`는 5월 2일부터 호출당 평균이 약 0.6k에서 1.2k로 늘었다. 이유는 stage별 default policy, hard/soft contract, reviewable exit 설명이 붙었기 때문이다.

하지만 stage별 기존 판단을 보면 1인 판단으로 줄여도 안전한 구간이 있다.

`2026-05-02` ~ `2026-05-06` hold advisor decision 291건:

| stage | final decisions | note |
|---|---|---|
| AUTO_SELL_REVIEW | SELL 30 / HOLD 0 | bear, neutral이 최종과 30/30 일치 |
| INTRADAY_REVIEW | HOLD 160 / SELL 6 | neutral이 최종과 166/166 일치 |
| PRE_CLOSE_CARRY | SELL 27 / HOLD 2 | neutral 29/29 일치지만 고위험 stage라 3인 유지 권장 |
| PRE_SESSION | SELL 1 / HOLD 1 | 표본 2건뿐 |
| TP_REVIEW | HOLD 59 | 기존 로그상 모두 HOLD, confidence 값 일부 비정상 |

AUTO_SELL_REVIEW 예시:

- `2026-05-06 US AMD`: final SELL, bull/bear/neutral 모두 SELL
- `2026-05-06 US INTC`: final SELL, bull/bear/neutral 모두 SELL
- `2026-05-05 US CRCL`: final SELL, bull만 HOLD, bear/neutral SELL

따라서 `AUTO_SELL_REVIEW`는 1차로 neutral 1회 판단 또는 deterministic rule + neutral confirmation으로 줄일 수 있다.

## 절감 시나리오

최근 3일 평균 578,534 tokens/day 기준 추정:

| 시나리오 | 예상 절감 | 새 평균 | 성능 리스크 |
|---|---:|---:|---|
| 설정만 조정: selection max_tokens 3500 + prompt cap 24/26 | 1.2% | 571.5k/day | 중간 |
| 저위험 운영 최적화: AUTO_SELL 1인 + preopen 중복 cache + hold 10분 cache + 보수적 cap | 7.2% | 536.7k/day | 낮음~중간 |
| 구조 압축 포함: candidate_actions/price_targets 중복 축소 + selection schema slim | 10.4% | 518.1k/day | 중간~높음, shadow 필수 |

주의:

- `CLAUDE_SELECTION_MAX_TOKENS=3500`만 먼저 낮추면 응답이 잘릴 수 있다.
- 5월 6일 selection call 중 output이 3,500을 넘은 call이 11건 있었다.
- 잘리면 `price_targets` 누락, JSON parse 실패, PathB plan 누락이 생길 수 있다.
- 따라서 max token cap은 schema를 먼저 줄인 뒤 낮추는 편이 안전하다.

## 최적화 옵션별 상세

### A. AUTO_SELL_REVIEW 1인 판단

제안:

- `AUTO_SELL_REVIEW`는 3인 분석 대신 neutral 1회 판단으로 시작한다.
- 단, 아래 조건이면 기존 3인으로 escalate한다.
  - neutral confidence < 0.75
  - neutral action이 HOLD인데 default policy는 SELL
  - pnl이 +3% 이상이고 peak drawdown만으로 sell이 걸린 경우
  - minutes_to_close <= 15
  - force_exit_window true
  - broker truth 불신, daily loss, unknown order 등 hard safety 관련

장점:

- 5월 4~6일 기준 약 76.6k tokens, 전체 4.4% 절감 가능.
- 기존 로그에서 AUTO_SELL_REVIEW는 bear/neutral이 최종 판단과 30/30 일치.
- 빠른 방어 판단의 latency도 줄어든다.

단점:

- 강한 반등 종목에서 bull minority가 제공하던 carry 예외 근거가 줄 수 있다.
- 표본은 30건으로 충분히 크지는 않다.

리스크 완화:

- `neutral_only_result != default_sell`이면 3인 재판정.
- 동일 ticker/stage에서 2회 연속 SELL이면 deterministic sell로 처리.
- pnl이 이익권이고 장세가 강하면 3인 유지.

예시:

```text
AUTO_SELL_REVIEW, INTC, pnl -2.04%, final SELL
votes: bull SELL, bear SELL, neutral SELL
=> neutral 1회로 충분
```

### B. hold_advisor 5~10분 캐시

제안:

- cache key:
  - market
  - ticker
  - decision_stage
  - strategy
  - position entry bucket
  - current price bucket
  - pnl bucket
  - tp/sl/trail status
  - default_policy hash
- 기본 TTL:
  - AUTO_SELL_REVIEW: 5분
  - INTRADAY_REVIEW: 10분
  - PRE_CLOSE_CARRY: 0분 또는 3분
  - TP_REVIEW: 10분, 단 target hit 후 첫 판단은 uncached

장점:

- 같은 종목을 몇 분 간격으로 반복 검토하는 비용 제거.
- 기존 10분 캐시 시뮬레이션 기준 5월 2~6일 약 14.8k tokens 절감.
- 가격 변화가 작을 때 판단 품질 영향이 작다.

단점:

- 최근 로그 기준 10분 TTL만으로는 절감폭이 크지 않다.
- 60~90분 TTL은 절감폭이 커지지만 stale risk가 커진다.

리스크 완화:

- 아래 조건에서 cache 무효화:
  - current price가 cache 시점 대비 0.3% 이상 변화
  - pnl bucket 변화: 손실권/본전/이익권 전환
  - new intraday high/low 갱신
  - TP/SL/trail status 변화
  - minutes_to_close <= 20
  - hard safety event 발생

### C. preopen selection 중복 cache

현상:

- `2026-05-06` preopen_watch selection 4회, 33k tokens.
- 같은 시장/세션에서 preopen watch는 executable decision이 아니므로 반복 호출 가치가 낮다.

제안:

- market/session당 preopen_watch full selection은 1회만 허용.
- 두 번째 호출은 기존 watchlist 재사용.
- 단, 후보 pool 변화가 크면 재호출 허용.

장점:

- 절감폭은 작지만 성능 리스크가 낮다.
- judgment_not_executable 상태의 낭비 호출을 줄인다.

단점:

- 장전 급등락/뉴스 반영이 늦을 수 있다.

리스크 완화:

- 아래 조건이면 cache 무효화:
  - full pool top 10 중 30% 이상 변경
  - preopen price change 상위 종목이 1% 이상 급변
  - pinned ticker 신규 등장
  - regular open 5분 이내 opening_confirm 전환

### D. selection prompt cap

현재:

- 실제 selection cap 변수는 `US_SELECTION_PROMPT_CAP`, `KR_SELECTION_PROMPT_CAP`.
- `config/v2_start_config.json`에는 `US_PROMPT_POOL_CAP`, `KR_PROMPT_POOL_CAP`가 있지만 selection 코드가 읽는 이름과 다르다.

제안:

- 바로 24/26으로 자르기보다 shadow에서 먼저 비교한다.
- 후보를 줄이더라도 diversity cap과 hard pin 보존은 유지한다.
- US 24, KR 26은 1차 목표. 성능 저하가 없으면 이후 US 22, KR 24 검토.

장점:

- input token 직접 감소.
- prompt 후보 line이 전체 prompt의 39~43%라 구조적으로 의미 있다.

단점:

- missed watch-only가 이미 높다.
- 후보 수를 줄이면 다음 급등 후보를 누락할 수 있다.

리스크 완화:

- trimming 기준:
  - hard pin은 항상 보존
  - high liquidity top movers 보존
  - day_gainers/day_losers/most_actives 최소 quota 보존
  - sector/category 쏠림 제한
  - 기존 watchlist carry-over 일부 보존
- shadow 검증:
  - full prompt selected set과 capped prompt selected set overlap
  - trade_ready overlap
  - omitted candidates의 max_runup_3d
  - omitted candidates 중 later traded 발생 여부

### E. candidate_actions/price_targets schema slim

바로 끄지 않는다.

이유:

- candidate_actions가 PlanA/PathB routing에 실제로 쓰이고 있다.
- 5월 6일 US snapshot에서 BUY_READY 9, PROBE_READY 9, PULLBACK_WAIT 7이 발생했다.

대신 다음처럼 중복을 줄인다.

현재 응답 구조 예:

```json
{
  "price_targets": {
    "NVDA": {
      "buy_zone_low": 202.5,
      "buy_zone_high": 205.5,
      "sell_target": 213,
      "stop_loss": 199,
      "entry_rationale": "...",
      "exit_rationale": "...",
      "rationale": "..."
    }
  },
  "candidate_actions": [
    {
      "ticker": "NVDA",
      "action": "BUY_READY",
      "reason": "...",
      "invalidation_condition": "...",
      "price_targets": {
        "buy_zone_low": 202.5,
        "buy_zone_high": 205.5,
        "sell_target": 213,
        "stop_loss": 199
      }
    }
  ]
}
```

최적화안:

```json
{
  "price_targets": {
    "NVDA": {
      "bzl": 202.5,
      "bzh": 205.5,
      "tgt": 213,
      "sl": 199,
      "rr": 1.65,
      "conf": 0.68
    }
  },
  "candidate_actions": [
    {
      "t": "NVDA",
      "a": "BUY_READY",
      "c": 0.68,
      "sz": "normal",
      "pt": "NVDA",
      "inv": "lose 200/OR low"
    }
  ]
}
```

장점:

- action semantics는 유지하면서 output token만 줄인다.
- `candidate_actions`와 `price_targets`의 중복 target을 제거한다.

단점:

- parser/normalizer 변경이 필요하다.
- 기존 dashboard/debug readability가 낮아질 수 있다.

리스크 완화:

- 내부 normalized schema는 기존과 동일하게 복원한다.
- raw compact response와 normalized expanded response를 둘 다 로그에 남긴다.
- 3거래일 shadow에서 old/new normalized output이 같은지 비교한다.

### F. selection two-stage split

제안:

1. Stage 1: rank/select only
   - watchlist
   - trade_ready
   - candidate_actions without detailed price target
2. Stage 2: price plan only for executable names
   - trade_ready
   - PULLBACK_WAIT
   - ADD_READY

장점:

- watch_only에 대한 긴 price/risk schema 부담 제거.
- price plan이 필요한 종목만 세부 호출.
- trade_ready가 0이면 Stage 2 호출 생략.

단점:

- 호출 수가 늘 수 있다.
- latency가 늘 수 있다.
- Stage 1/2 사이에 가격이 움직이면 plan stale risk가 생긴다.

리스크 완화:

- Stage 2는 max 3~5 tickers.
- Stage 2 전 current price recheck.
- 가격이 Stage 1 reference 대비 0.3% 이상 움직이면 plan skip.
- Stage 2 cache TTL 3분.

## API 테스트 계획

이번 문서 작성 중에는 API를 추가 호출하지 않았다.

이유:

- 기존 raw call에 full prompt/response가 있어 비용 분석은 충분하다.
- hold_advisor 1인화는 기존 3인 vote 로그로 offline 비교가 가능하다.
- selection 압축은 재호출 시 stochastic drift가 있어 1~2건으로 성능을 판단하면 위험하다.

필요 시 최소 API 테스트:

- 샘플 수: 6 prompts
  - KR opening 2
  - KR intraday 1
  - US opening 2
  - US intraday 1
- 각 prompt에 대해:
  - 기존 prompt 그대로 1회
  - compact prompt 1회
  - 총 12 calls 이하
- 비교 지표:
  - watchlist overlap >= 80%
  - trade_ready overlap >= 70%
  - price_target coverage 100%
  - JSON parse success 100%
  - invalid price target 0건
  - candidate_action route diff는 high-risk tickers 수동 검토

단, 이 테스트는 실제 과거 outcome이 이미 있는 날짜로 해야 한다. `2026-05-04` ~ `2026-05-06`은 아직 forward outcome이 부족하므로, `2026-04-22` ~ `2026-05-02` 샘플이 더 적합하다.

## 권장 적용 순서

### Phase 0: 기록/계측 보강

구현 전 확인:

- selection raw/normalized/applied를 이미 남기는지 확인
- candidate_actions route diff 저장
- selection parse failure, missing price target, demoted tickers 집계
- hold_advisor cache hit/miss 계측 추가

성공 기준:

- 현재와 동일한 output quality 지표를 매일 자동 산출 가능

### Phase 1: 성능 리스크 낮은 절감

적용 후보:

- AUTO_SELL_REVIEW neutral 1회 + escalation
- hold_advisor 5~10분 price-aware cache
- preopen_watch duplicate cache

예상 절감:

- 약 7.2%
- 578.5k/day -> 536.7k/day

성공 기준:

- AUTO_SELL_REVIEW에서 missed protective sell 0건
- cache 사용 후 hard exit override 정상
- forced exit, daily loss, broker mismatch는 cache 우회

### Phase 2: selection cap shadow

적용 후보:

- `US_SELECTION_PROMPT_CAP=24`
- `KR_SELECTION_PROMPT_CAP=26`
- 아직 live 적용하지 않고 shadow 비교

성공 기준:

- selected overlap >= 80%
- trade_ready overlap >= 70%
- omitted candidate의 runup_3d가 기존 대비 악화되지 않음
- weak trade_ready 비율 증가 없음

### Phase 3: schema slim shadow

적용 후보:

- candidate_actions compact schema
- price_targets short keys
- reason/veto length cap
- target duplication 제거

성공 기준:

- normalized output이 기존 schema와 동등
- PathB missing price target 증가 없음
- action routing route diff가 허용 범위 이내
- forward_3d/traded conversion 열화 없음

### Phase 4: max_tokens 하향

schema slim 이후에만 적용한다.

권장 순서:

1. 6000 -> 4500
2. parse failure와 missing target 3거래일 확인
3. 4500 -> 3800
4. 최종 3500은 output 안정화 후 검토

3500부터 바로 적용하지 않는 이유:

- 5월 6일 US selection output이 5,000 tokens를 넘은 call이 여러 건 있었다.
- schema를 그대로 둔 상태에서 cap만 낮추면 JSON truncation과 price target 누락이 생길 수 있다.

## 리스크 매트릭스

| 변경 | 비용 절감 | 성능 리스크 | 운영 리스크 | 권장 |
|---|---:|---|---|---|
| AUTO_SELL_REVIEW 1인화 | 높음 | 낮음~중간 | 낮음 | 1순위 |
| hold_advisor 10분 캐시 | 낮음~중간 | 낮음 | 중간 | 1순위 |
| preopen 중복 cache | 낮음 | 낮음 | 낮음 | 1순위 |
| prompt cap 24/26 | 낮음 | 중간 | 낮음 | shadow 후 |
| max_tokens 3500 | 낮음 | 중간~높음 | 중간 | schema slim 후 |
| candidate_actions 제거 | 중간 | 높음 | 높음 | 금지 |
| candidate_actions compact | 중간 | 중간 | 중간 | shadow 후 |
| two-stage selection/price plan | 중간~높음 | 중간 | 중간 | 후순위 |

## 최종 권고

성능 저하를 허용하지 않는 조건에서는 다음이 가장 안전하다.

1. candidate_actions는 끄지 않는다.
2. `CLAUDE_SELECTION_MAX_TOKENS=3500`은 바로 적용하지 않는다.
3. 먼저 AUTO_SELL_REVIEW 1인화 + escalation을 적용한다.
4. hold_advisor cache는 price-aware로 짧게 시작한다.
5. preopen 중복 selection은 세션당 1회로 제한한다.
6. selection 압축은 shadow에서 기존 DB outcome과 비교한 뒤 적용한다.

1차 목표 절감률은 약 7%가 현실적이다. 10% 이상 절감은 가능하지만, candidate action/price target 구조를 건드리므로 최소 3거래일 shadow 검증 후 적용해야 한다.
