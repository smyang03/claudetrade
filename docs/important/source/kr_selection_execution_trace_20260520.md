# 2026-05-20 KR selection/execution trace report

작성일: 2026-05-20 KST
범위: 2026-05-20 한국장 Claude 종목선정, trade_ready 정규화, candidate action routing, 실행 게이트 추적
목적: 오늘 후보군 중 왜 실제 매수로 이어지지 않았는지, 어떤 데이터를 보았고 어떤 사유로 차단됐는지 기록한다.

## 결론

오늘 KR 후보 발굴은 강한 종목을 여러 개 포착했지만, 실제 주문 가능한 `trade_ready`로 남은 종목은 없었다.

핵심 원인은 세 가지다.

| 구분 | 대상 | 요약 |
|---|---|---|
| Raw READY -> 정규화 강등 | `080220`, `142280` | Claude raw 응답에는 `PROBE_READY`와 `tr`가 있었지만, blocker가 함께 있어 compact normalizer가 `WATCH`로 강등했다. |
| 정규화 READY -> route 강등 | `456010`, `032580` | 정규화는 `PROBE_READY`/`BUY_READY` 통과했지만 KR confirmation gate에서 `kr_data_quality_not_confirmed`로 `WATCH` 처리됐다. |
| BUY_READY ceiling -> Claude WATCH 유지 | `142280`, `052900`, `066980`, `084650`, `114450`, `253840`, `000660`, `005930`, 일부 `456010` | runtime evidence ceiling은 BUY_READY까지 허용했지만 Claude action은 WATCH였고, local promotion이 꺼져 있어 실행 후보로 승격되지 않았다. |

따라서 오늘의 실패는 단일 주문 버그가 아니라 `Claude raw`, `compact normalization`, `KR confirmation gate`, `market mode`, `late/stale gate`가 각각 다른 단계에서 보수적으로 작동한 결과다.

## 본 자료

주로 아래 로컬 런타임 산출물을 확인했다.

| 자료 | 확인 목적 |
|---|---|
| `logs/raw_calls/20260520_KR_select_tickers_*.json` | Claude 원본 selection 응답의 `wl`, `tr`, `ca` 확인 |
| `logs/raw_calls/20260520_KR_select_tickers_090459114461_fbf1c0386c.json` | 09:04 `080220`, `142280` raw `PROBE_READY` 확인 |
| `logs/raw_calls/20260520_KR_select_tickers_091032525136_5704485d7b.json` | 09:10 `456010`, `032580`, `357880` raw action 확인 |
| `logs/funnel/candidate_funnel_snapshot_20260520_KR.jsonl` | raw/normalized/applied trade_ready stage와 candidate_action_routes 확인 |
| `logs/funnel/gate_evaluation_20260520_KR.jsonl` | 종목별 gate, evidence ceiling, soft gates, final action 확인 |
| `logs/funnel/watch_trigger_not_evaluated_20260520_KR.jsonl` | watch trigger가 왜 실제 평가되지 않았는지 확인 |
| `logs/system/live_trading_20260520.log` | selection normalize 로그, 모드 전환, PathB registration skip, run cycle 확인 |
| `logs/daily_judgment/live_20260520_KR.json` | KR 일중 판단, `DEFENSIVE`, size 0, 신규매수 block 확인 |
| `state/shared_judgment_KR_20260520.json` | 공유 판단 상태 확인 |
| `state/live_open_positions.json` | KR 실제 보유/매도 후보 존재 여부 확인 |
| Naver Finance polling API | 후보군의 실제 장중/종가 결과 보조 확인 |

## 시장 및 런타임 상태

KR daily judgment는 방어적이었다.

| 항목 | 값 |
|---|---|
| 최종 KR consensus | `DEFENSIVE` |
| weighted score | `-0.7` |
| new buy permission | `block` |
| size | `0` |
| analyst votes | `bear`, `bear`, `bear` |
| minority triggered | `true` |

장중 모드 흐름은 다음과 같았다.

| 시각 | 상태 |
|---|---|
| 09:04 | `NEUTRAL`, size 23%, selection refresh |
| 09:24 | `CAUTIOUS_BEAR`, size 0% |
| 11:24 | `MILD_BEAR`, size 18% |
| 14:27 | `DEFENSIVE`, size 0% |
| 14:30 이후 | watch trigger 대부분 `mode_blocked`, `promotion_enabled=false`, `shadow_only=true` |

또한 KR live에서는 PathB price plan registration이 비활성 정책으로 스킵됐다.

```text
[PathB paper-only] KR live Claude Price registration skipped
```

즉 PathA `trade_ready`가 살아남지 못하면 실제 신규매수로 이어질 경로가 없었다.

## 실제 장 데이터 요약

확인한 주요 후보의 종가 기준 결과는 강했다. 다만 이 결과는 사후 결과이며, 주문 판단은 각 시점의 live evidence와 gate 기준으로 이루어졌다.

| 종목 | 종가 등락 | 장중/종가 메모 |
|---|---:|---|
| `142280` 녹십자엠에스 | +28.19% | 09:04 raw `PROBE_READY`였으나 blocker로 정규화 강등. 이후 evidence ceiling BUY_READY 반복. |
| `052900` KX하이텍 | +4.18% | BUY_READY ceiling 구간 있었으나 Claude action은 WATCH, late/low liquidity/ATR/DEFENSIVE 차단. |
| `381620` 제닉스로보틱스 | +14.79% | 14:28 이후 늦게 등장. 데이터 partial/unknown/fade, late chase, DEFENSIVE. |
| `010170` 대한광통신 | +6.26% | 종가는 강했지만 selection 시점 대부분 fade/ATR/or_missing/stale. |
| `086960` MDS테크 | +4.06% | 초반 partial/probe ceiling 이후 fade/ATR/rs60 negative. |
| `046970` 우리로 | +6.63% | 종가 강했지만 장중 판단은 fade, from_high_deep, ATR, rs20 negative. |
| `080220` 제주반도체 | 확인 대상 | 09:04 raw `PROBE_READY`, `tr` 포함. blocker로 정규화 강등. |
| `456010` | 확인 대상 | 09:10 raw/normalized `PROBE_READY`, route에서 confirmation 미충족. |
| `032580` | 확인 대상 | 09:10 raw/normalized `BUY_READY`, route에서 confirmation 미충족. |

## Raw READY가 정규화에서 사라진 케이스

2026-05-20 KR 전체 raw selection call을 순회한 결과, `raw tr`에 있었는데 normalized `trade_ready`에서 사라진 종목은 `080220`, `142280` 두 개였다.

### 09:04 raw call

파일:

```text
logs/raw_calls/20260520_KR_select_tickers_090459114461_fbf1c0386c.json
```

원본 응답:

```json
{
  "tr": ["080220", "142280"]
}
```

정규화 결과:

```text
parsed.tr = ['080220', '142280']
normalized.trade_ready = []
warnings = [
  '080220:ready_with_blockers_demoted',
  '142280:ready_with_blockers_demoted',
  'trade_ready_non_ready_actions_removed'
]
```

### 종목별 사유

| 종목 | raw action | raw blocker | 정규화 결과 | 정규화 사유 |
|---|---|---|---|---|
| `080220` | `PROBE_READY` | `opening_range_break_null`, `blackout_now`, `data_incomplete` | `WATCH` | `ready_with_blockers_demoted` |
| `142280` | `PROBE_READY` | `blackout_now`, `opening_range_break_null`, `liq_mid`, `risk26` | `WATCH` | `ready_with_blockers_demoted` |

정규화 로직은 `runtime/selection_compact_schema.py`에 있다. `BUY_READY` 또는 `PROBE_READY`인데 `blocking_factors`가 하나라도 있으면 실행 가능한 ready로 보지 않고 `WATCH`로 강등한다.

```text
if action in READY_ACTIONS and blocking_factors:
    ready_with_blockers_demoted
    action = WATCH
```

따라서 `142280`은 Claude가 매수 후보로 찍었지만, 동시에 `blackout_now`, `opening_range_break_null` 같은 blocker를 같이 제출했기 때문에 시스템 계약상 주문 후보가 아니게 됐다.

## 정규화는 통과했지만 route에서 막힌 케이스

09:10 raw call에서는 실제 `trade_ready`가 있었다.

파일:

```text
logs/raw_calls/20260520_KR_select_tickers_091032525136_5704485d7b.json
```

정규화 단계:

| 종목 | raw action | normalized action | normalized trade_ready |
|---|---|---|---|
| `456010` | `PROBE_READY` | `PROBE_READY` | 포함 |
| `032580` | `BUY_READY` | `BUY_READY` | 포함 |
| `357880` | `PULLBACK_WAIT` | `PULLBACK_WAIT` | PathB wait 후보 |

그러나 funnel snapshot에서는 최종 적용 `trade_ready=[]`였다.

```text
09:10 selection_stages:
raw        = ['456010', '032580']
normalized = []
applied    = []
```

candidate_action_routes에서 다음처럼 route가 강등됐다.

| 종목 | requested | final | 사유 | 추가 경고 |
|---|---|---|---|---|
| `456010` | `PROBE_READY` | `WATCH` | `kr_data_quality_not_confirmed` | `kr_confirmation_required`, `early_session_full_entry_window` |
| `032580` | `BUY_READY` | `WATCH` | `kr_data_quality_not_confirmed` | `kr_confirmation_required`, `early_session_full_entry_window` |
| `357880` | `PULLBACK_WAIT` | `WATCH` | `kr_data_quality_not_confirmed` | `kr_confirmation_required`, `early_session_full_entry_window` |

### `456010` route 사유

확인된 gate 값:

| 항목 | 값 |
|---|---|
| data_quality | `minute_partial` |
| evidence data_state | `partial` |
| missing field | `opening_range_break` |
| ret_3m | +3.64% |
| ret_5m | +4.28% |
| vwap_reclaim | `true` |
| orderbook_support | `false` |
| kr_confirmation_confirmed | `false` |
| kr_confirmation_reason | `kr_data_quality_not_confirmed` |

가격/모멘텀은 나쁘지 않았지만 데이터 품질이 `minute_partial`이고 orderbook support가 없어서 KR confirmation gate를 통과하지 못했다.

### `032580` route 사유

확인된 gate 값:

| 항목 | 값 |
|---|---|
| data_quality | `minute_partial` |
| evidence data_state | `partial` |
| missing field | `opening_range_break` |
| ret_3m | +7.61% |
| ret_5m | +5.53% |
| vwap_ok | `false` |
| orderbook_support | `true` |
| kr_confirmation_confirmed | `false` |
| kr_confirmation_reason | `kr_data_quality_not_confirmed` |

`BUY_READY`였지만 opening range가 확정되지 않았고 vwap 조건도 실패해 route가 `WATCH`로 강등됐다.

## BUY_READY ceiling이 있었지만 WATCH로 남은 케이스

runtime evidence ceiling은 "증거상 허용 가능한 최대 액션"이다. 이것은 실제 Claude action이나 주문 권한이 아니다.

오늘 `BUY_READY ceiling`인데 Claude action이 `WATCH`였던 종목은 다음과 같다.

| 종목 | 횟수 | 예시 시각 | 주된 사유 |
|---|---:|---|---|
| `142280` | 9 | 09:24, 09:50, 12:53, 13:54, 14:54, 15:55 | overextended, late_chase, stale_candidate, action_ceiling_watch, risk high |
| `052900` | 3 | 13:54, 14:54, 15:55 | low liquidity, ATR blocked, or_missing, late_chase, DEFENSIVE |
| `066980` | 3 | 10:51 이후 | sustained였으나 stale/late/action_ceiling_watch |
| `084650` | 2 | 09:24 | early_strength, 그러나 모드/확인 부족 |
| `114450` | 2 | 09:24 | overextended |
| `253840` | 1 | 09:50 | sustained, 이후 WATCH 유지 |
| `000660` | 1 | 11:52 | late_chase |
| `005930` | 1 | 10:51 | late_chase |
| `456010` | 1 | 09:24 | watch_weak, action_ceiling_watch |

이 유형은 raw READY 누락이 아니다. Claude가 최종 action을 `WATCH`로 둔 상태이고, `decision_trace.local_promotion_allowed=false`, `system_may_promote=false`, `reask_claude_required_for_promotion=true`라서 시스템이 임의로 `BUY_READY`로 올릴 수 없었다.

## 사용자 지정 관심 종목별 판단

### `142280` 녹십자엠에스

가장 아쉬운 종목이다.

| 시각 | 상태 |
|---|---|
| 09:04 | raw `PROBE_READY`, `tr` 포함. 그러나 `blackout_now`, `opening_range_break_null`, `liq_mid`, `risk26` 때문에 정규화에서 `WATCH` 강등 |
| 09:10 | 이미 `overextended_state`, high risk로 WATCH |
| 09:24 이후 | `BUY_READY ceiling`이 반복됐지만 Claude action은 WATCH |
| 12:53~15:55 | sustained, opening range break, vwap/volume context 확인. 그러나 late_chase/stale/action_ceiling_watch |
| 14:30 이후 | market mode `DEFENSIVE`, 신규매수 사실상 차단 |

사유 요약:

```text
09:04: raw READY + blockers -> compact demotion
이후: BUY_READY evidence ceiling + Claude WATCH + local promotion disabled
장후반: late_chase/stale + DEFENSIVE mode
```

### `052900` KX하이텍

일부 시점에 `BUY_READY ceiling`은 있었지만 raw action은 계속 WATCH였다.

주요 blocker:

```text
liq_low
atr_blocked
or_missing
late_chase
defensive_mode
ep_low
```

따라서 raw ready 누락이 아니라 Claude WATCH 유지와 시장모드 차단에 가깝다.

### `381620` 제닉스로보틱스

14:28 이후 늦게 등장한 후보였다.

주요 blocker:

```text
or_missing
atr_blocked
state_unknown
fade
risk_35 / risk_28
late_chase
defensive_mode
```

실행 관점에서는 discovery가 늦었고, 데이터도 확정되지 않았다.

### `010170` 대한광통신

종가는 강했지만 selection 시점의 live evidence는 대체로 부정적이었다.

주요 blocker:

```text
fade
atr_blocked
or_missing
ep_low
late_chase
action_ceiling_watch
stale_candidate
```

### `086960` MDS테크

초반에는 `PROBE_READY ceiling` 정도가 있었지만 raw action은 WATCH였다.

주요 blocker:

```text
atr_blocked
from_high_deep
blackout_now
or_missing
fade
rs60_negative
defensive_mode
```

### `046970` 우리로

종가 결과는 좋았지만, 장중 selection 기준에서는 고점 대비 밀림과 fade가 컸다.

주요 blocker:

```text
fade
atr_blocked
or_missing
from_high_deep
rs20_negative
ep_low
```

## 매도 후보 확인

KR 실제 보유 기반 매도 후보는 확인되지 않았다.

`state/live_open_positions.json` 기준 실제 포지션은 KR이 아니라 US `AXTI`였다. `logs/hold_advisor/decisions_2026-05-20.jsonl`에도 실질 KR sell decision은 없고 테스트성 항목만 확인됐다.

따라서 오늘 KR "매도 후보"는 실제 보유 청산 후보가 아니라 selection/audit 관점의 `AVOID`, `WATCH_WEAK`, risk classification으로 보는 것이 맞다.

## 주요 사유 분류

오늘 신규매수가 막힌 사유를 계층별로 나누면 다음과 같다.

| 계층 | 사유 | 영향 |
|---|---|---|
| Compact schema | `ready_with_blockers_demoted` | raw `PROBE_READY`라도 blocker가 있으면 `WATCH`로 강등 |
| Compact schema | `trade_ready_non_ready_actions_removed` | raw `tr`에 있어도 action이 READY가 아니면 trade_ready에서 제거 |
| KR confirmation gate | `kr_data_quality_not_confirmed` | 정규화 READY라도 data quality, OR/VWAP/orderbook 확인 미충족 시 route에서 `WATCH` |
| Market mode | `CAUTIOUS_BEAR size=0`, `DEFENSIVE size=0` | 신규매수 권한 축소 또는 차단 |
| Watch trigger | `mode_blocked`, `promotion_enabled=false`, `shadow_only=true` | WATCH 후보 재평가/승격이 실제 주문으로 이어지지 않음 |
| Runtime evidence | `action_ceiling_watch` | stale/watch_weak/blocked 상태에서 ceiling이 WATCH로 낮아짐 |
| Timing | `blackout_now`, `late_chase`, `stale_candidate` | 개장 직후/장후반/오래된 후보 추격 방지 |
| Execution data | `or_missing`, `opening_range_break_null`, `data_partial`, `minute_partial` | opening range 및 분봉 데이터 확정 전 진입 방지 |
| Risk/quality | `atr_blocked`, `risk_41`, `liq_low`, `ep_low`, `fade` | 변동성/유동성/진입우선순위/모멘텀 품질 차단 |
| PathB policy | `KR live Claude Price registration skipped` | KR live에서 PathB price plan 경로가 실제 신규진입으로 이어지지 않음 |

## 개선 검토 포인트

### 1. 로그 명확화

현재 `_apply_selection_meta()`의 `raw_trade` 로그는 Claude 원본 compact `tr`가 아니라 이미 정규화된 `raw_meta.trade_ready`를 찍는다.

오늘 09:04처럼 원본 `tr=["080220","142280"]`였는데 로그에는 `raw_trade=[]`로 보여 혼동이 생긴다.

개선안:

```text
selection_stages.raw_compact_tr
selection_stages.raw_compact_actions
selection_stages.normalization_warnings
```

를 별도로 보존한다.

### 2. `PROBE_READY + blocker` 정책 재검토

현재는 blocker가 하나라도 있으면 ready를 즉시 WATCH로 강등한다.

오늘 `142280`, `080220`처럼 `PROBE_READY`가 있으나 opening range가 형성 중인 케이스는 아래 중 하나로 정책을 명확히 정해야 한다.

| 선택지 | 의미 | 위험 |
|---|---|---|
| 현행 유지 | blocker 포함 READY는 즉시 WATCH | 급등 초입 포착 실패 가능 |
| `PULLBACK_WAIT` 변환 | `blackout_now`/`or_missing`은 대기 플랜으로 보존 | KR live PathB off면 효과 제한 |
| 재질문 예약 | OR 확정 후 Claude re-ask | 비용 증가, 늦은 추격 위험 |
| micro-probe 예외 | 강한 ret/vwap/volume 조건에서 소액 probe 허용 | 손실/VI/과열 리스크 증가 |

현재 안전계약상 현행 유지가 더 보수적이다. 다만 missed runup 개선 목적이라면 "대기 또는 재질문"이 주문보다 먼저 검토돼야 한다.

### 3. KR confirmation gate 재검토

`456010`, `032580`은 정규화 ready까지 통과했지만 `kr_data_quality_not_confirmed`로 막혔다.

검토할 점:

```text
minute_partial 상태에서 ret_3m/ret_5m/vwap/volume/orderbook 중 몇 개가 충족되면 probe만 허용할지
fast_window_min=5 이후에도 partial grace를 줄지
opening_range_break 미확정 시 BUY_READY를 PROBE_READY 또는 WAIT로 낮출지
```

다만 오늘 케이스에서는 orderbook/vwap 실패가 섞여 있어 단순 완화는 위험하다.

### 4. BUY_READY ceiling WATCH 재질문 정책

`142280`, `052900`처럼 evidence ceiling은 BUY_READY인데 Claude action이 WATCH인 케이스가 반복됐다.

현재:

```text
local_promotion_allowed=false
system_may_promote=false
reask_claude_required_for_promotion=true
```

개선안:

```text
BUY_READY ceiling이 2회 이상 반복되고
data_state=confirmed이고
late_chase/stale가 아니며
mode가 신규매수 허용이면
Claude re-ask를 예약
```

이 방향은 local auto promotion보다 안전하다.

### 5. KR PathB live policy 확인

오늘 KR live에서는 PathB price plan registration이 paper-only로 스킵됐다.

`PULLBACK_WAIT`나 `PROBE_READY + blocker`를 대기 플랜으로 살리고 싶다면, KR PathB live off 정책과 충돌한다. 정책적으로 KR PathB를 계속 끌지, shadow만 유지할지 먼저 정해야 한다.

## 최종 판단

오늘 매수 실패의 가장 실질적인 missed-runup 후보는 `142280`이다. 그러나 실제 원인은 주문 모듈이 놓친 것이 아니라 `raw PROBE_READY + blocker`를 실행 불가로 보는 compact 계약과, 이후 시장모드/late/stale/local promotion 차단이 겹친 것이다.

추가로 `456010`, `032580`은 정상적으로 READY까지 올라왔지만 KR confirmation gate가 막았다. 이 둘은 정규화 문제가 아니라 실행 확인 데이터 품질 문제다.

나머지 관심 종목 `052900`, `381620`, `010170`, `086960`, `046970`은 raw READY가 아니었고, 대부분 WATCH 유지 사유가 명확했다.

따라서 개선 우선순위는 다음이 합리적이다.

1. 원본 compact `tr`와 정규화 warning을 funnel/stage 로그에 명시한다.
2. `PROBE_READY + opening blocker`를 무조건 폐기할지, WAIT/re-ask로 보존할지 정책을 정한다.
3. KR confirmation gate의 partial data 처리 기준을 시뮬레이션으로 검증한다.
4. BUY_READY ceiling이 반복되는 WATCH 후보의 re-ask 조건을 만든다.
5. KR PathB live off 정책과 대기 플랜 활용 여부를 분리해서 결정한다.
