# 데이터 흐름 누수 점검 (2026-07-23)

목적: 코드·전략은 완성 단계다. **데이터가 빠지거나 / 안 넘어가거나 / 안 쓰이거나** 하는
지점을 잡는다. 통계 집계가 아니라 **실제 종목 데이터를 파이프라인에 직접 주입**해
어디서 끊기는지를 종목 단위로 지목했다.

도구: `tools/pipeline_integrity_audit.py` (축5·6·7 추가). 읽기 전용.
```
python tools/pipeline_integrity_audit.py --since 2026-07-01 --cases 6
```

## 0. 누수를 세 유형으로 나눈다 — 처방이 다르기 때문

```
빠짐    소비처가 읽는데 생성 자체가 없다
안넘김  생성됐는데 다음 단계 컨텍스트로 전달되지 않는다
안씀    전달까지 됐는데 아무도 읽지 않는다
```

## 1. ★ 안넘김 — 만들어 놓고 안 넘긴다

### 1-1. `data_quality` 미전달 → 자동 강등 (actionable 행에 250배 집중)

`post_open_features`에 `data_quality`가 없으면 `build_live_evidence_pack`이 `'unknown'`을
대입하고, ceiling 규칙이 `data_quality in {first_observed, unknown, missing}` → **PROBE_READY**로
강등한다. 즉 **필드를 안 넣어준 것만으로 매수 자격이 사라진다.**

```
                       미전달/전체        비율
US judge actionable      49/  650       7.54%
US 기타                  19/63072       0.03%   ← 251배 차이
KR judge actionable      13/  235       5.53%
KR 기타                   0/21286       0.00%
```

전체로는 0.1%지만 **judge가 판단을 내린 행에만 집중**된다. 랜덤 결측이 아니다.
주입 검증 30건 중 7건이 이 유형이었고, 필드를 전부 채워도 안 풀린다(게이트가 필드가
아니라 `data_quality`라서).

### 1-2. `vwap_distance_pct` — 9,267건 생성, runtime_gate 전달 0건

```
필드                  [생성]features  [전달]runtime_gate
vwap                       9,258            4,307
vwap_distance_pct          9,267                0   ★
opening_range_high         8,519            4,102
volume_ratio_open          9,278            4,309
```

같은 계산에서 나온 `vwap`은 넘어가는데 `vwap_distance_pct`만 0이다. 전달 누락이다.

### 1-3. `spread_bps` — 방향이 반대인 누수

```
post_open_features   0건       ← evidence가 읽는 곳
runtime_gate        92건       ← KR 마이크로구조 컨텍스트에서 생성
```

evidence는 `features["spread_bps"]`를 읽는데 그쪽엔 한 건도 없다. 결과적으로
KR `fade_recovered_shadow`의 스프레드 게이트가 항상 무조건 통과 상태다(무력화).

## 2. 안씀 — 계산해 놓고 아무도 안 읽는다

| 필드 | 생성 | 전달 | 소비처 |
|---|---:|---:|---|
| `time_normalized_rvol` | 4,995 | 0 | **없음** |
| `vwap` | 9,258 | 4,307 | **없음** |
| `opening_range_high` | 8,519 | 4,102 | **없음** |

`time_normalized_rvol`은 judge가 판정문에서 `rel_vol`로 인용하는 값인데, evidence는
`volume_ratio_open`만 본다. 같은 개념을 두 필드가 나눠 갖고 **한쪽은 소비처가 없다.**

단 이 이원화를 통합해도 ceiling 회복은 0.5%다(별건 검증에서 반증). 계약 정리 자체는
가치가 있으나 매수량 레버는 아니다.

## 3. placeholder / 미수집 — 컬럼은 있는데 데이터가 아니다

```
atr_pct                  값보유 90,661/90,661  고유값     1   ★placeholder
cohort_reliability       값보유      0/90,661            ★미수집(runtime_gate엔 존재)
entry_delay_min          값보유      0/90,661            ★미수집
us_early_entry_size_mult 값보유      0/90,661            ★미수집
position_mfe_pct         값보유      3/90,661
position_mae_pct         값보유      3/90,661
```

`atr_pct`는 **전 행이 값을 갖고 있는데 고유값이 1개**다. NULL 검사로는 절대 안 잡힌다.
`digest_builder`는 실제로 계산한다 — 전파 단계에서 상수로 덮인다.

`position_mfe_pct/mae_pct` 3건은 코드 매핑이 예측한 누수와 정확히 일치한다:
`_update_position_excursion()`이 `pathb_runtime.py:3934` **단 한 곳**에서만 호출되고,
`pathb_path_run_id`가 없으면 즉시 return하므로 PathB를 안 타는 포지션은 MFE/MAE가
아예 생성되지 않는다.

## 4. 종목별 주입 검증 — 어디서 죽었는지 종목 단위로 지목

30개 종목 × 6시나리오(종목 중복 제외)에 실제 데이터를 넣었다.

```
주입 결과 누수 유형 집계
  data_quality 미전달 → 'unknown' 대입되어 강등     7건
  단일필드 병목: opening_range_break               6건
  data_quality=first_observed 게이트               6건
```

**단일 필드 하나로 해소되는 케이스 (KR 7/22):**
```
005930  PROBE_READY  결측 opening_range_break  → 이 필드 하나만 채우면 BUY_READY
034220  PROBE_READY  결측 opening_range_break  → 동일
060720  PROBE_READY  결측 opening_range_break  → 동일
```

**원장 ≠ 재생 (전파 누수 그 자체):**
```
US WULF  2026-07-22  원장 BUY_READY  재생 PROBE_READY
KR 215790            원장 BUY_READY  재생 PROBE_READY
KR 131400            원장 BUY_READY  재생 PROBE_READY
US GS    2026-07-14  원장 PROBE_READY 재생 WATCH
```
원장에 기록된 ceiling과, 원장에 함께 저장된 입력으로 재계산한 ceiling이 다르다.
같은 행 안에서 입력과 결과가 어긋나 있으므로 둘 중 하나는 그 시점 값이 아니다.

## 5. 이번에 내 도구가 틀렸던 것 — 기록해 둔다

첫 실행에서 DELL의 사인을 `volume_ratio_open` 결측으로 지목했다. **틀렸다.**
확인 3필드만 반사실 대상으로 삼았는데, evidence는 코어 모멘텀(`ret_3m_pct`/`ret_5m_pct`)도
함께 세기 때문에 확인 필드를 다 채워도 `partial`에 남는다.

두 번째로 "필드 무관 — momentum_state=sustained"로 찍었다. **이것도 틀렸다.**
`momentum_state`는 pack 최상위 키가 아니라 None이었고, 진짜 게이트는 `data_quality`였다.

교훈: **차단 사유를 추측으로 라벨링하지 말고 pack이 실제로 판정한 값을 읽어야 한다.**
두 번 다 실측으로 잡혔다.

## 6. funnel 스트림 생존 — '안 찍힘 = 누수' 아님

조건부 로거는 사건이 없으면 0건이 정상이다. 코드에서 발화 조건을 확인해 분류했다.

```
system_sell_bypass         51일  EXIT_LIFECYCLE_ALLOWLIST_LIVE off면 영구 0건 → 정상
tail_capture               16일  TAIL_CAPTURE_MODE 기본 off면 영구 0건 → 정상
auto_sell_review_...bypass 51일  강제매도 임계 돌파 시에만 → 판정 유보
exit_lifecycle_decision     8일  청산후보 발생 시에만(쿨다운 중복억제) → 7월 포지션 희소라 정상 가능
상시 로거(6종)              1일  전부 정상
```

주의 하나: `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS`가 켜지면 이벤트 이름이
`auto_sell_review_force_sell_bypass` → `hold_advisor_cache_hard_guard_bypass`로 **이동**한다.
전자의 0건을 누락으로 오판하기 쉽다.

또 하나: `tail_capture_*.jsonl`·`fast_fill_*.jsonl`은 market suffix가 없고
`ENABLE_LIVE_FUNNEL_JSONL`을 안 탄다(별도 싱크). 표준 규칙으로 스캔하는 집계 도구는
이 둘을 통째로 놓친다.

## 7. 고칠 순서

| # | 누수 | 유형 | 규모 |
|---|---|---|---|
| 1 | `data_quality` 미전달 → 자동 강등 | 안넘김 | actionable 행의 US 7.5%·KR 5.5% |
| 2 | 후보 원장 체결축(`filled_count`/`execution_event_id`) | 안넘김 | 5/08 이후 전량, 6월 체결 306건 포함 |
| 3 | `atr_pct` placeholder(고유값 1) | 안넘김 | 90,661행 전량 |
| 4 | `vwap_distance_pct` runtime_gate 미전달 | 안넘김 | 9,267건 |
| 5 | MFE/MAE 단일 호출점 — PathB 외 포지션 미생성 | 빠짐 | 90,661행 중 3건만 보유 |
| 6 | `spread_bps` features 미전달 | 빠짐 | KR fade 게이트 무력화 |
| 7 | `cohort_reliability`·`entry_delay_min`·`us_early_entry_size_mult` | 안넘김 | 전량 미수집 |
| 8 | `time_normalized_rvol`·`vwap`·`opening_range_high` | 안씀 | 소비처 없음 |

1~4는 **판정 결과를 바꾸는** 누수라 먼저다. 5·7은 측정만 막고, 8은 계약 정리 건이다.

## 8. 남은 것

- 매도 방향은 코드 매핑까지만 했고 주입 검증은 아직이다. 7월 청산 4건뿐이라 표본이 없다.
- 시드 B("매수가 아쉬웠던" 미진입 건 — `intraday_entry_shadow` 502건에 `would_entry_price`
  보유 확인) 사후 성과 결합 미실시.
- 위 8건은 전부 **관측 사실**이고, 각각이 실제로 매수를 얼마나 늘리는지는 별도 검증이 필요하다.
