# PathB 후보 교체 흐름

작성일: 2026-05-21

## 개요

PathB 후보 교체는 크게 네 경로로 이루어진다.

---

## 1. 인라인 교체 — 무신호 누적

**트리거**: KR 60분(`_KR_NO_SIGNAL_SWAP_MIN`) / US N cycles(`_US_NO_SIGNAL_SWAP_CYCLES`) 무신호 지속

**위치**: `trading_bot.py` line ~23021 (`_swap_due` 판정)

**과정**:
1. 엔트리 스캔 루프에서 ticker별 무신호 시간 누적
2. 임계 초과 + 미보유 상태면 `today_tickers` 목록에서 직접 교체
3. `_selection_meta_apply_inline_replacement()` 호출
   - `selection_meta`의 `watchlist`, `trade_ready`, `_pathb_wait_tickers`, `_pathb_wait_origins` 동기 업데이트
   - `_runtime_filtered_trade_ready[old_ticker] = "inline_replacement_no_signal:new_ticker"` 기록
4. Telegram `watchlist_change_alert` 발송

**PathB run 처리**: 기존 old_ticker의 WAITING run은 **자동 취소 없음**. 다음 `scan_waiting_entries()` 사이클에서 가격 조건 또는 TTL로 자연 소멸 대기.

---

## 2. 인라인 교체 — 가격 오류 / 이상가격

**트리거**: `_invalid_price_count` 누적

**위치**: `trading_bot.py` line ~21944, ~22029

**과정**: `_selection_meta_apply_inline_replacement()` 동일 호출. reason이 `inline_replacement_invalid_price` 또는 `inline_replacement_outlier_price`로 기록됨.

**PathB run 처리**: 동일하게 자동 취소 없음.

---

## 3. 부분교체 — 2시간 주기

**트리거**: `_partial_reselect_last`가 115분 이상 경과

**위치**: `trading_bot.py` line 25685 (`_partial_reselect()`)

**과정**:
1. `_partial_replace_score()`로 비활성도 점수화 → 하위 2~3개 `replace_out` 선정 (포지션 보유 종목 보호)
2. `_screen_market_candidates()` 재스크리닝
3. `select_tickers()` Claude 재선정
4. `_pick_partial_replace_in()` 교체 확정
5. `selection_meta` 갱신 — `price_targets` merge 포함
6. `today_tickers` 업데이트, 쿨다운 60분 등록

**PathB run 처리**: `replace_out` ticker의 WAITING run **자동 취소 없음**. `selection_meta` 갱신만 이루어지고 기존 WAITING run은 다음 scan 사이클에서 자연 소멸 대기.

---

## 4. Action Routing 경유 — 명시적 취소

**트리거**: action routing 결과 `decision.cancel_pathb = True`

**위치**: `trading_bot.py` line 5763

**과정**: `cancel_waiting_for_ticker(market, ticker)`가 **명시적으로** 호출됨.

PathA BUY_READY가 결정된 ticker의 PathB WAITING run을 즉시 취소.

---

## 핵심 주의사항

PathB WAITING run이 명시적으로 취소되는 경로는 **action routing 한 곳뿐**이다.

인라인 교체나 부분교체 후에는 `selection_meta` 갱신만 이루어지고, 교체된 ticker의 기존 WAITING run은 다음 `scan_waiting_entries()` 사이클의 가격/TTL 조건에 의해 자연 소멸된다.

따라서 **교체 직후에도 old_ticker의 PathB WAITING run이 살아 있으면, 가격이 진입 레벨에 닿을 경우 주문이 발송될 수 있다.**

이 동작이 의도한 것인지 확인이 필요하다. 인라인/부분교체 시 `cancel_waiting_for_ticker()`를 함께 호출하는 개선을 검토할 수 있다.
