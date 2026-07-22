# 데이터 흐름 개선 상세 리스트 (2026-07-23)

두 AI의 주입 검증 결과를 합치고, 상충하는 건은 코드·원장 실측으로 판정했다.

- (A) `tools/pipeline_case_injection.py` + `pipeline_case_injection_20260723.md` — 타 AI
- (B) `tools/pipeline_integrity_audit.py` 축5·6·7 + `data_flow_leak_audit_20260723.md` — 본 세션

---

## 0. 먼저 판정: (A)의 핵심 발견 1은 처방이 틀렸다

(A)의 주장:
> `no_submit_signal_flags_json.raw`의 mom/gap/mr/vb가 77/77 전부 null인데
> 같은 row의 `runtime_gate`에는 데이터가 있다(75/77). evidence/runtime에는 데이터가
> 있는데 PlanA signal row에는 안 들어간 흔적이다.
> **처방: PlanA signal check 입력에 runtime_gate/post_open_features 값을 병합해야 한다.**

### 사실관계 — 절반은 맞다

`raw` 4필드가 77/77 전부 null인 것은 사실이다. `runtime_gate`에 데이터가 있는 것도 사실이다
(volume_ratio_open 75/77 · vwap_reclaim 75/77 · ret_5m_pct 76/77 — 재검증 일치).

### 그러나 원인이 다르다 — 코드 실측

```
trading_bot.py:36421   _no_signal_row = sig_df.iloc[i].to_dict()
trading_bot.py:35638   sig_df = calc_all(candles)
trading_bot.py:9517    raw = {"mom": row.get("mom", row.get("momentum", row.get("momentum_signal"))),
                              "gap": ..., "vb": ..., "mr": ...}
```

`indicators.calc_all()`이 만드는 컬럼 전수: `ma5/ma20/ma60/ma120`, `vol_avg20`, `vol_ratio`,
`rsi`, `macd`, `macd_signal`, `macd_hist`, `bb_upper/lower/pct`, `atr`, `high52/low52/pos52`,
`gap_pct`, `change_pct`, `ma_align`, `macd_cross`, `high20`, `new_high20` + 원본 OHLCV.

**`mom`·`momentum`·`momentum_signal`·`gap`·`vb`·`mr` 컬럼은 하나도 없다.**
따라서 `raw`가 null인 것은 데이터가 안 흘러서가 아니라, **조회하는 키 이름이 그 DataFrame에
존재한 적이 없기 때문**이다. 구조적으로 항상 null이다. 같은 이유로 최상위 불리언
(`momentum`/`gap_pullback`/…)도 항상 false다 — `_truthy_signal()`이 같은 키를 찾기 때문이다.

### 룰은 정상 동작했다 — `reason_detail`에 전부 남아 있다

```
OR pullback: reason=orp_range_too_high range=5.44% pullback=0.00% vol=0.00 elapsed=48m
| 변동성돌파: 전략 비활성(US MILD_BULL)
| momentum: ma=True, macd=True, vol=False, high=True
| mean_reversion: RSI=74.5 BB=115.6 vol=0.85 ma60_ok=...
```

momentum은 `vol=False` 하나 때문에 안 터졌다. 실제 값으로 평가된 결과다.
`block_meta.rejection_reason` 분포도 정확하다:

```
orp_entry_window_expired  52 · orp_range_too_high 15 · orp_not_formed 4
orp_pullback_too_shallow   4 · orp_forming        1 · orp_volume_low  1   (계 77)
```

**77건 전부 정당한 룰 판정이다. "데이터가 안 들어와서 신호가 죽은" 건은 0건이다.**

### 처방대로 하면 해롭다

`signal_flags`의 의미는 "어떤 PlanA 룰 전략이 발화했는가"다. 여기에 evidence 피처
(`volume_ratio_open`·`vwap_reclaim`)를 병합하면 서로 다른 두 개념이 한 필드에 섞인다.
룰은 이미 자기 입력(candles → `calc_all`)으로 정상 동작 중이므로 얻는 것도 없다.
train/serve skew는 이 저장소에서 이미 한 번 사고를 낸 유형이다.

**→ (A) 개선안 #1은 기각. 아래 #1로 대체한다.**

---

## 1. 개선 리스트 — 판정 결과를 바꾸는 것 (우선)

### P0-1. `data_quality` 미전달 → 자동 강등  【(B) 발견 · 신규】

`post_open_features`에 `data_quality`가 없으면 `build_live_evidence_pack`이 `'unknown'`을
대입하고, ceiling 규칙(`runtime/live_evidence_pack.py:255-261`)이 곧바로 PROBE_READY로 강등한다.

```
                    미전달/전체      비율
US judge actionable    49/  650    7.54%
US 기타                19/63072    0.03%   ← 251배 집중
KR judge actionable    13/  235    5.53%
KR 기타                 0/21286    0.00%
```

- **왜 P0**: 필드를 안 넣어준 것만으로 매수 자격이 사라진다. judge가 판단을 내린 행에만
  집중되므로 랜덤 결측이 아니다.
- **조치**: `data_quality` 미주입 시 `'unknown'` 대입 대신 (a) 상류에서 항상 채우거나
  (b) 미주입과 실제 unknown을 구분해 미주입은 강등 사유에서 제외.
- **검증**: 주입 30건 중 7건이 이 유형. 다른 필드를 전부 채워도 안 풀린다.

### P0-2. 후보 원장 체결축 단절  【(A)(B) 양쪽 합의】

```
execution_decision_id   US 463행 · KR 38행   ← 붙는다
execution_event_id      US   0행 · KR  0행   ← 안 붙는다
filled_count>0          2026-05-08 이후 전량 0
lifecycle FILLED        5월 254 · 6월 306 · 7월 12
```

- **범위 정정**: 7월 현상이 아니라 **5/08 이후 2.5개월**이다. 6월 체결 306건도 미반영.
- **조치**: `execution_decision_id` 기준 canonical/lifecycle → 후보 원장 backfill.
  최소 필드 `filled_count`, `first_fill_at`, `execution_event_id`, `entry_price`,
  `exit_price`, `pnl_pct`.

### P0-3. `atr_pct` placeholder  【(B) 발견】

```
atr_pct   값보유 90,661/90,661   고유값 1개
```
전 행이 값을 갖고 있어 **NULL 검사로는 절대 안 잡힌다.** `digest_builder`는 실제로
계산하는데 전파 단계에서 상수로 덮인다. 랭킹·필터가 ATR 축을 못 본다.

### P1-1. `vwap_distance_pct` runtime_gate 미전달  【(B) 발견】

```
vwap                9,258 생성 → 4,307 전달
vwap_distance_pct   9,267 생성 →     0 전달   ★
opening_range_high  8,519 생성 → 4,102 전달
```
같은 계산에서 나온 `vwap`은 넘어가는데 파생값만 0이다.

### P1-2. `spread_bps` — features 쪽에만 없다  【(B) 발견】

```
post_open_features   0건    ← evidence가 읽는 곳
runtime_gate        92건    ← KR 마이크로구조에서 생성
```
KR `fade_recovered_shadow`의 스프레드 게이트가 항상 무조건 통과 상태(무력화).

---

## 2. 개선 리스트 — 관측·재현성 (판정은 안 바뀌나 분석이 막힌다)

### P1-3. `_plan_a_signal_flags` 키 계약 불일치  【(A) 발견 · (B) 원인 교정】

- **증상**: `raw` 4필드가 영구 null, 최상위 불리언도 영구 false. 유효한 건
  `strategy_signal`·`plan_a_signal_allowed` 둘뿐.
- **원인**: `trading_bot.py:9517`이 `mom`/`gap`/`vb`/`mr`를 찾는데 `sig_df`(=`calc_all` 출력)에
  그 컬럼이 없다.
- **조치**: `mom_fired`/`gap_fired`/`vb_fired`/`mr_fired` 키로 조회하도록 교정하거나,
  `trading_bot.py:36486-36495`에서 이미 올바른 이름으로 만들고 있는 dict를 넘긴다.
  **evidence 피처를 병합하는 것이 아니다.**
- **참고**: 진짜 사유는 `no_submit_reason_detail`과 `block_meta.rejection_reason`에 이미
  온전히 있다. 그래서 이건 편의성 결함이지 진단 불능은 아니다.

### P1-4. NO_SIGNAL 기록 계약 보강  【(A) 제안 채택 · 문구 수정】

(A)의 `metadata_contract_violation=plan_a_signal_raw_empty` 제안은 타당하다.
단 P1-3을 고치면 raw가 채워지므로, 플래그는 **고친 뒤에도 비는 경우**를 잡는 용도로 둔다.

### P1-5. PathB raw plan 보존 위치  【(A) 발견 · 채택】

가격계획이 `no_submit_block_meta_json.raw_plan`에만 남고 `payload_json`에 action/route
원본이 없어 PathB route/등록 재생이 안 된다. `payload_json.pathb_raw_plan`(또는
`payload_json.action.price_targets`)에도 저장.

### P1-6. reason attribution 분산  【(A) 발견 · 채택】

`route_reason`·`route_runtime_gate_reason`·`no_submit_reason_code`가 흩어져 분석이 흔들린다.
`first_blocking_layer`·`final_blocking_layer`·`all_blocking_reasons`로 분리 저장.
**(B) 보강**: 원장 ceiling과 재생 ceiling이 어긋나는 행이 실재하므로
(WULF·215790·131400·GS), 저장 시점 스탬프도 함께 남겨야 한다.

### P2-1. MFE/MAE 단일 호출점  【(B) 발견】

`_update_position_excursion()`이 `pathb_runtime.py:3934` 한 곳에서만 호출되고
`pathb_path_run_id` 없으면 즉시 return → PathB 외 포지션은 MFE/MAE가 아예 생성되지 않는다.
`position_mfe_pct` 실측 3/90,661.

### P2-2. 미수집 컬럼  【(B) 발견】

`cohort_reliability`·`entry_delay_min`·`us_early_entry_size_mult` 전량 0건.
`cohort_reliability`는 `runtime_gate`에는 존재 → 저장 단계 누락.

### P2-3. 소비처 없는 계산  【(B) 발견】

`time_normalized_rvol`(4,995) · `vwap`(9,258) · `opening_range_high`(8,519) — 읽는 곳 없음.
계약 정리 대상이나 **매수량 레버는 아니다**(ceiling 회복 0.5%로 반증됨).

---

## 3. 정상으로 분류 — 고치지 말 것

| 항목 | 판정 근거 |
|---|---|
| NO_SIGNAL 77건 | 전부 정당한 룰 판정. `rejection_reason` 6종 모두 조건 미달·타이밍 |
| KR `entry_price_cap_exceeded` | (A) 확인대로 cap 안쪽 주입 시 route 열림 = 정상 방어 |
| `system_sell_bypass` 51일 무기록 | `EXIT_LIFECYCLE_ALLOWLIST_LIVE` off면 영구 0건이 정상 |
| `tail_capture` 16일 무기록 | `TAIL_CAPTURE_MODE` 기본 off면 영구 0건이 정상 |
| `exit_lifecycle_decision` 8일 | 7월 청산 4건뿐이라 표본 부재. 누수 아님 |

---

## 4. 확인 필요 (판정 유보)

- `volume_state=missing` 72/77 — ORP 룰의 장중 거래량이 `vol=0.00`으로 찍힌다.
  `mean_reversion`은 같은 행에서 `vol=0.85/1.37`을 갖는다(일봉 `vol_ratio`).
  둘은 다른 소스이므로 ORP 쪽 장중 거래량이 실제로 비는지 별도 확인.
- `CLAUDE_PRICE_INVALID` 91건 — (A)가 `confidence_below_minimum`·
  `reward_risk_below_minimum`으로 분류. 임계값이 타당한지는 별건(메모리상 confidence 완화는
  이미 반증).
- 매도 방향 주입 검증 — 7월 청산 4건뿐이라 6월 구간으로 내려가야 표본이 선다.

---

## 5. 실행 순서 제안

```
1  P0-1  data_quality 미전달        판정 결과가 바뀐다. 규모도 actionable에 집중
2  P0-2  체결축 backfill            이게 없으면 1~9의 효과를 측정할 수 없다
3  P0-3  atr_pct placeholder        NULL 검사로 안 잡혀 계속 숨는다
4  P1-1  vwap_distance_pct 전달
5  P1-3  signal_flags 키 교정       (A) #1을 이걸로 대체
6  P1-6  reason attribution 정리    이후 분석의 기반
7  P1-5  PathB raw plan 보존
8  P2-*  MFE 호출점 · 미수집 · 소비처 없는 계산
```

1·2가 먼저인 이유: **1은 지금 매수를 막고 있고, 2는 나머지 개선의 효과 측정을 막고 있다.**

## 6. 방법론 메모

이번 교차 검토에서 (A)의 처방 하나가 기각됐고, 그 근거는 통계가 아니라 **`calc_all()`이
실제로 만드는 컬럼 목록**이었다. 앞선 교차 검토에서도 `claude_action`이 퇴역 경로라는
**원장 정의**가 결론을 뒤집었다. 두 번 다 같은 교훈이다 —
**숫자 대조로는 계약 오류를 못 잡는다. 코드가 실제로 무엇을 만들고 무엇을 읽는지 봐야 한다.**
