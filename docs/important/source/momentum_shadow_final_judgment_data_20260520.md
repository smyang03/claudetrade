# Momentum Shadow 최종 판단용 데이터 추가 분석

작성일: 2026-05-20 KST
범위: 로컬 코드, 로컬 SQLite DB, 로컬 `data/price` daily CSV만 조회. 주문, 브로커 API, Claude 호출 없음.

## 결론

최종 판단은 기존 리포트와 동일합니다.

| 항목 | 판단 |
|---|---|
| KR momentum live 재개 | 금지 유지 |
| US RISK_ON momentum | 현행 유지 |
| US RISK_OFF momentum live 재개 | 금지 유지 |
| US RISK_OFF momentum shadow | metadata + close outcome 보강 후 관찰 |
| A+B 구현 | 필요. 단, live 재개용이 아니라 측정/판단 축 복구용 |

이번 추가 분석의 핵심은 `candidate_counterfactual_paths` immediate row에 daily close outcome을 가상 계산해도 momentum live 재개 근거가 나오지 않는다는 점입니다.

## 왜 추가 분석을 했는가

기존 리포트에서 남은 공백은 세 가지였습니다.

1. `candidate_counterfactual_paths` row는 쌓이지만 outcome label이 0개라 shadow 판단에 쓸 수 없는 상태.
2. metadata에 `recommended_strategy`, `mode_family`, `slot_filter_reason`이 없어 momentum row를 직접 필터링할 수 없는 상태.
3. CRDO처럼 `slot_disabled:momentum`으로 막힌 row가 실제로 좋은 기회였는지 판단하려면, 최소한 virtual close outcome이 필요.

따라서 이번에는 코드 수정 없이 현재 DB와 daily CSV만으로 복원 가능한 범위를 계산했습니다.

## 데이터 스냅샷

대상 DB:

| source | 범위/상태 |
|---|---:|
| `data/audit/candidate_audit.db/candidate_counterfactual_paths` | 8,329 rows |
| `data/ticker_selection_log.db/ticker_selection_log` | strategy/mode best-effort 복원용 |
| `data/price/kr/*.csv`, `data/price/us/*.csv` | daily close outcome 계산용 |

`candidate_counterfactual_paths` 상태:

| status | rows |
|---|---:|
| PENDING | 5,963 |
| BASELINE_NO_TRADE | 794 |
| TRIGGERED | 786 |
| DATA_MISSING | 786 |

outcome label 상태:

| 항목 | rows |
|---|---:|
| 30m/60m/close 중 하나라도 채워진 row | 0 |
| immediate + entry_price + trigger_time row | 786 |
| daily close 계산 가능 | 682 |
| 아직 가격 close 미도착 | 104 |

중요: 104건은 대부분 2026-05-20 KR row입니다. 각 가격 CSV의 마지막 날짜가 2026-05-19라서 아직 close가 없는 상태입니다. 이것은 영구 `DATA_MISSING`이 아니라 `PRICE_PENDING` 또는 `FUTURE_CLOSE_UNAVAILABLE`로 두고 다음 daily price 수집 후 재시도해야 합니다.

metadata key 상태:

| metadata key | rows |
|---|---:|
| `recommended_strategy` | 0 |
| `strategy` | 0 |
| `slot_filter_reason` | 0 |
| `mode_family` | 0 |
| `shadow_label` | 0 |
| `is_virtual_pnl` | 0 |
| `label_source` | 0 |

또한 `candidate_key` 기준으로 `audit_candidate_rows`와 join되는 immediate row는 0건입니다. ticker-day 기준으로는 붙일 수 있지만, cycle/action 단위 attribution은 불안정합니다. 따라서 이번 strategy/mode 분석은 `ticker_selection_log`의 같은 날짜/시장/종목 nearest row를 붙인 best-effort 복원입니다.

## 계산 방법

대상:

- `path_name='immediate'`
- `status='TRIGGERED'`
- `entry_price is not null`
- `trigger_time is not null`

계산:

```text
virtual_close_outcome_pct = (daily_close - entry_price) / entry_price * 100
```

주의:

- `entry_price`는 실제 fill price가 아닙니다.
- `runtime/counterfactual_paths.py` 기준 immediate row의 `entry_price`는 `context.current_price`입니다.
- 따라서 이 값은 actual PnL이 아니라 `virtual_immediate_shadow` outcome입니다.

strategy/mode 복원:

- 같은 `market + session_date + ticker`의 `ticker_selection_log` row를 찾음.
- `trigger_time`과 가장 가까운 `selected_at` row를 선택.
- `_mode_family()` 로직과 동일하게 `AGGRESSIVE/MODERATE_BULL/MILD_BULL`은 `RISK_ON`, `BALANCED/CAUTIOUS/NEUTRAL/blank`은 `BALANCED`, 나머지는 `RISK_OFF`로 분류.

선택 row 매칭 품질:

| bucket | rows |
|---|---:|
| no selection row | 169 |
| <= 2m | 278 |
| <= 15m | 28 |
| <= 60m | 81 |
| > 60m | 126 |
| strategy 복원 가능 전체 | 456 |
| strategy 복원 가능, 60분 이내 | 370 |

## Virtual Close Outcome 전체 결과

682개 계산 가능 row 전체:

| n | avg | median | win | PF | min | max |
|---:|---:|---:|---:|---:|---:|---:|
| 682 | -0.188% | +0.000% | 46.0% | 0.865 | -28.844% | +32.450% |

시장별:

| market | n | avg | median | win | PF |
|---|---:|---:|---:|---:|---:|
| KR | 301 | +0.208% | +0.000% | 46.2% | 1.126 |
| US | 381 | -0.501% | -0.113% | 45.9% | 0.580 |

action별:

| market | action | n | avg | median | win | PF |
|---|---|---:|---:|---:|---:|---:|
| KR | BUY_READY | 9 | +4.305% | +6.559% | 55.6% | 9.656 |
| KR | PULLBACK_WAIT | 17 | +1.347% | +0.552% | 52.9% | 2.005 |
| KR | WATCH | 270 | +0.027% | +0.000% | 45.6% | 1.016 |
| US | BUY_READY | 8 | -0.437% | -0.319% | 37.5% | 0.473 |
| US | PROBE_READY | 5 | -0.827% | -0.989% | 20.0% | 0.316 |
| US | PULLBACK_WAIT | 60 | -1.046% | -0.272% | 36.7% | 0.348 |
| US | WATCH | 305 | -0.439% | -0.045% | 47.9% | 0.613 |

US는 immediate virtual 기준에서도 READY/WAIT 계열이 모두 음수입니다. 이 값만 놓고 US RISK_OFF momentum을 live로 열 근거는 없습니다.

## Momentum 복원 결과

strategy/mode는 아직 metadata가 없으므로 ticker selection nearest row 기준 best-effort입니다.

전체 nearest match 기준:

| market | strategy | family | n | avg | median | win | PF |
|---|---|---|---:|---:|---:|---:|---:|
| KR | momentum | RISK_OFF | 32 | -1.317% | -0.666% | 28.1% | 0.287 |
| US | momentum | RISK_OFF | 13 | -0.069% | -0.426% | 30.8% | 0.917 |

60분 이내 match만 사용한 보수 기준:

| market | strategy | family | n | avg | median | win | PF |
|---|---|---|---:|---:|---:|---:|---:|
| KR | momentum | RISK_OFF | 30 | -1.409% | -0.666% | 26.7% | 0.272 |
| US | momentum | RISK_OFF | 11 | -0.573% | -0.565% | 18.2% | 0.415 |

US RISK_OFF momentum action별, nearest match 기준:

| action | n | avg | median | win | PF |
|---|---:|---:|---:|---:|---:|
| WATCH | 8 | +0.124% | -0.307% | 37.5% | 1.165 |
| BUY_READY | 3 | -0.126% | -0.565% | 33.3% | 0.884 |
| PULLBACK_WAIT | 2 | -0.751% | -0.751% | 0.0% | 0.000 |

해석:

- KR momentum은 기존 결론보다 더 강하게 금지입니다.
- US RISK_OFF momentum은 “나쁜 데이터가 충분하다”보다는 “좋다고 볼 데이터가 없다”에 가깝습니다.
- WATCH는 약간 낫지만 READY/WAIT에서 edge가 사라집니다. live 재개가 아니라 shadow 유지가 맞습니다.

## CRDO 2026-05-19 확인

CRDO immediate row는 모두 `TRIGGERED`이며 daily close 계산 대상입니다.

2026-05-19 CRDO close: 168.9900

| action | entry_price | close outcome | nearest strategy | mode | family |
|---|---:|---:|---|---|---|
| WATCH | 164.38 | +2.804% | mean_reversion | MILD_BEAR | RISK_OFF |
| WATCH | 164.38 | +2.804% | mean_reversion | MILD_BEAR | RISK_OFF |
| BUY_READY | 169.95 | -0.565% | momentum | MILD_BEAR | RISK_OFF |
| BUY_READY | 170.31 | -0.775% | mean_reversion | MILD_BEAR | RISK_OFF |
| PROBE_READY | 169.29 | -0.179% | mean_reversion | MILD_BEAR | RISK_OFF |

CRDO 하나만 보면 “초기 WATCH 가격은 좋았고, 나중 READY 가격은 늦었다”는 구조가 보입니다. 하지만 현재 metadata가 비어 있어서 이 현상을 momentum 전반으로 확정하기에는 부족합니다. 다만 live 재개 금지 판단에는 충분히 보수적인 근거입니다.

## WATCH -> READY timing gap

strategy와 무관하게 같은 ticker-day에서 WATCH와 READY/WAIT가 모두 있는 immediate row를 비교했습니다.

| 항목 | 값 |
|---|---:|
| WATCH/READY paired rows | 94 |
| READY 가격이 WATCH보다 비싼 정도 평균 | +2.585% |
| READY close outcome - WATCH close outcome 평균 | -2.570% |
| READY가 WATCH보다 outcome 좋은 비율 | 9.6% |
| READY 가격이 WATCH보다 비싼 비율 | 84.0% |

이건 momentum만의 증거는 아니지만, 현재 시스템이 WATCH 이후 READY로 올라오는 과정에서 이미 가격이 많이 움직인 뒤 따라붙는 케이스가 많다는 신호입니다. CRDO 케이스와 같은 방향입니다.

## 최종 판단

이번 추가 계산을 넣어도 momentum live 재개 판단은 바뀌지 않습니다.

1. KR momentum은 금지 유지.
   - 기존 selection/audit/v2/backtest 모두 음수였고, 이번 virtual close 복원에서도 KR RISK_OFF momentum은 avg -1.317% 또는 보수 기준 -1.409%입니다.

2. US RISK_ON momentum은 현행 유지.
   - 이번 분석 대상은 주로 RISK_OFF slot-disabled shadow gap입니다. 기존 RISK_ON 성과를 건드릴 근거는 없습니다.

3. US RISK_OFF momentum은 live 금지, shadow만 유지.
   - best-effort 전체 nearest 기준도 PF 0.917로 1 미만입니다.
   - 60분 이내 match만 쓰면 PF 0.415로 더 나쁩니다.
   - BUY_READY/PULLBACK_WAIT가 WATCH보다 더 좋다는 증거가 없습니다.

4. A+B 구현은 필요.
   - 하지만 목적은 “momentum 재개”가 아니라 “slot-disabled 후보를 올바르게 labeling해서 다음 판단이 가능하게 만드는 것”입니다.

## 개발 요구사항

### A. Counterfactual metadata 보강

`runtime/counterfactual_paths.py::build_counterfactual_rows()`에 `metadata_overrides` 인자를 추가하는 방식이 가장 깔끔합니다. 호출부에서 `context`에 전략/slot 정보를 섞으면 gate data와 strategy attribution이 뒤섞입니다.

필수 metadata:

```json
{
  "recommended_strategy": "momentum",
  "mode_family": "RISK_OFF",
  "slot_filter_reason": "slot_disabled:momentum",
  "slot_plan": {"mean_reversion": 1},
  "shadow_label": true,
  "shadow_lane": "momentum_slot_disabled",
  "route_original_action": "BUY_READY",
  "route_final_action": "WATCH",
  "label_source": "virtual_immediate_shadow",
  "entry_price_source": "context_current_price",
  "is_virtual_pnl": true
}
```

구현 위치:

- `runtime/counterfactual_paths.py`
  - `build_counterfactual_rows(..., metadata_overrides: dict[str, Any] | None = None)`
  - 기본 metadata 생성 후 top-level merge.
- `trading_bot.py`
  - counterfactual row 생성부에서 `recommended_strategy`, `_mode_family(consensus_mode)`, `_runtime_filtered_trade_ready`의 `slot_disabled:momentum` 정보를 주입.
  - US ticker key는 uppercase 기준으로 조회.

테스트:

- `tests/test_counterfactual_paths.py`에 metadata override merge 케이스 추가.
- US ticker uppercase key로 `slot_filter_reason`이 빠지지 않는 케이스 추가.

### B. Counterfactual close outcome 계산 구현

`tools/update_counterfactual_outcomes.py`는 현재 이름과 달리 outcome 계산을 하지 않습니다. 최소 1차 구현은 close outcome부터 채우면 됩니다.

대상:

```text
path_name = 'immediate'
status = 'TRIGGERED'
entry_price is not null
trigger_time is not null
```

계산:

```text
outcome_close_pct = (daily_close - entry_price) / entry_price * 100
```

source:

- KR: `data/price/kr/kr_<ticker>.csv`
- US: `data/price/us/us_<TICKER>.csv`
- CSV는 BOM이 있으므로 `utf-8-sig`로 읽어야 합니다.

status 정책:

| 상황 | 권장 status/reason |
|---|---|
| close 계산 성공 | `OUTCOME_PARTIAL` 또는 `CLOSE_OUTCOME_FILLED` |
| session_date가 가격 CSV 마지막 날짜보다 미래 | `PRICE_PENDING`, retry 대상 |
| 과거 날짜인데 row가 없음 | `PRICE_UNAVAILABLE` |
| entry/trigger 없음 | `DATA_MISSING` |

metadata에 반드시 남길 것:

```json
{
  "label_source": "virtual_immediate_shadow",
  "entry_price_source": "context_current_price",
  "is_virtual_pnl": true,
  "label_horizons": ["close"],
  "source_attempts": ["daily_close"],
  "final_attempt_at": "..."
}
```

주의:

- 이 값은 actual fill PnL이 아닙니다.
- `audit_candidate_rows.pnl_pct`나 `v2_learning_performance.pnl_pct`와 직접 섞으면 안 됩니다.
- 30m/60m은 minute source가 확인된 뒤 별도 확장해야 합니다.

테스트:

- 임시 DB + 임시 price CSV로 `outcome_close_pct` 계산 검증.
- `PRICE_PENDING` 케이스 검증.
- 기존 `DATA_MISSING` row에 `--retry-missing`을 줬을 때 재시도되는지 검증.

## QA 기준

구현 후 최소 확인:

```text
python -m pytest tests/test_counterfactual_paths.py -q
python -m pytest tests/test_update_counterfactual_outcomes.py -q
python tools/update_counterfactual_outcomes.py --date 2026-05-19 --market US --retry-missing
```

DB 검증 쿼리:

```sql
select count(*)
from candidate_counterfactual_paths
where outcome_close_pct is not null;

select json_extract(metadata_json, '$.label_source'), count(*)
from candidate_counterfactual_paths
where outcome_close_pct is not null
group by 1;

select json_extract(metadata_json, '$.recommended_strategy'),
       json_extract(metadata_json, '$.mode_family'),
       count(*)
from candidate_counterfactual_paths
group by 1, 2;
```

운영 판단 기준:

- US RISK_OFF momentum은 최소 10 labeled sessions 전까지 live 금지.
- virtual close PF > 1.2, 30m/60m 평균 양수, actual audit/v2 PnL 음수 아님을 동시에 만족해야 probe 검토 가능.
- KR momentum은 별도 강한 반증이 나오기 전까지 live 금지.
