# KR 진입 0건 진단 — selection 응답 계약 실패 (2026-07-31, 미완)

성격: 읽기 전용 진단. 코드·설정 변경 없음. **원인 후보 2개까지 좁혔고 확정은 미완.**

## 0. 결론

KR 신규 진입이 8일째(마지막 실거래 2026-07-23) 없는 원인은 **국면이 아니라 selection 응답 계약 실패**다.
`trade_ready`가 **7월 한 달 내내 0건**이고, 후보 액션의 97%가 v2 스키마가 아닌 **레거시 폴백**으로 합성된다.

07-28의 "매수 0건은 MILD_BEAR 국면에서 설계대로"라는 판정은 **더 이상 유효하지 않다.** 오늘 모드는 `MILD_BULL`이고 7월 내내 국면이 바뀌었는데도 계속 0이다.

## 1. 실측 사슬

| 확인 항목 | 값 | 판정 |
|---|---|---|
| KR `trade_ready` (audit_claude_calls) | 07-01~07-31 **전 기간 0** | 구조적 |
| KR `candidate_action_count` | 700~950/일 정상 | 응답 자체는 옴 |
| KR `claude_reason` | `legacy_watchlist` **178/242 (97%)** | **v2 폴백 경로** |
| `legacy_watchlist` 생성 지점 | `runtime/candidate_actions.py:214` | `ca` 부재 시 watchlist로 합성, ready 아니면 무조건 WATCH·conf 0.0 |
| KR `claude_action` 분포 (07-31) | WATCH 178 / PULLBACK_WAIT 3 / 공란 61 / **READY 0** | |
| KR `evidence_action_ceiling` | **BUY_READY 23 / PROBE_READY 89** | 증거는 충분 |
| trade_ready 슬롯 (MILD_BULL→RISK_ON) | 총 6개 (momentum 1·gap_pullback 2·ORP 1·mean_reversion 1·unassigned 2) | **원인 아님** |
| US 대조 (같은 기간) | trade_ready 6~21건 정상 | KR 전용 문제 |

**핵심 모순:** 증거상 BUY_READY가 허용되는 후보가 23건 있는데, 실제 액션은 0건이다.

## 2. 메커니즘 (코드 대조)

`runtime/selection_compact_schema.py::canonicalize_compact_selection`:

```
ca(candidate_actions) 부재       → errors.append("candidate_actions_missing")
stop_reason == "max_tokens"      → errors.append("stop_reason_max_tokens")
     ↓
fatal_contract = bool(errors or stop_reason == "max_tokens")
     ↓
if fatal_contract and action in ACTIONABLE_ACTIONS:
    action = "WATCH"                    # 전부 강등
    target = {}
if fatal_contract:
    trade_ready = []                    # 통째로 비움
```

즉 **응답 계약이 한 번 깨지면 그 호출의 모든 후보가 WATCH가 되고 `trade_ready`가 빈다.** KR에서 관측되는 현상과 정확히 일치한다.

## 3. 남은 가설 2개 (다음 세션 시작점)

**(가) Claude 응답에 `ca` 키가 없다**
- compact schema는 `ca`를 필수로 요구한다(`ca exactly one item per wl ticker, same order`)
- KR에서만 계약을 못 지키는 이유가 있는지 확인 필요

**(나) `stop_reason=max_tokens`로 응답이 잘린다**
- `KR_SELECTION_PROMPT_CAP=28`, `CLAUDE_SELECTION_COMPACT_WATCH_MAX=15`
- 실제 KR watchlist_count가 **30~31**로 관측됨 → watch_max 15를 넘는다
- 응답이 길어져 잘리면 `stop_reason_max_tokens` → fatal_contract

**(나)가 더 유력하다.** watchlist 30~31 vs compact watch_max 15의 불일치가 실측으로 확인된다.

### 검증 방법

1. `data/ticker_selection_log.db` 또는 selection 메타에서 `_compact_validation.errors` / `_selection_stop_reason` 확인
2. `_partial_contract_recovery_watch_only` 플래그가 KR에서 True인지
3. US의 watchlist_count와 비교 (US는 왜 통과하는가)

**주의:** `logs/raw_calls/`에 selection 원문이 **2026-07-08 이후 저장되지 않는다**(오늘 파일은 `tune_60min`·`single_symbol_judge`뿐). 원문 대조를 하려면 로깅부터 살려야 한다.

## 4. 이번 세션에서 배제된 것

- **국면** — MILD_BULL(RISK_ON)인데도 0
- **슬롯 한도** — 6개 열려 있음
- **증거 부족** — evidence_action_ceiling에 BUY_READY 23건
- **Claude 응답 자체의 부재** — candidate_action_count 700~950으로 응답은 옴

## 5. 참고 — 유사 전례

US에서 같은 유형이 두 번 있었다.
- 07-29: rescreen이 judge overlay를 덮어써 BUY_READY 승격이 죽음 (수정 완료)
- 07-30: 함정구간 게이트가 US BUY_READY 2건을 전부 강등 (수정 완료)

**KR 경로에는 같은 수정이 들어가지 않았을 가능성**을 함께 확인해야 한다.
