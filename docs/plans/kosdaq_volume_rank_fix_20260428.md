# KOSDAQ Volume Rank Fix - 2026-04-28

## Goal

Fix the KR screener so KOSPI and KOSDAQ volume-rank calls are separated by the KIS-supported input index code, not by an unsupported `FID_COND_MRKT_DIV_CODE="Q"` value.

This is live-code work. The change must not place orders, call Claude, or mutate account state during verification.

## Source Evidence

- Stored screener audit for 2026-04-28 showed `kosdaq_raw=0` for every premarket and intraday snapshot.
- The zero count happened before product/history filtering, so this is not a local filter issue.
- Local KIS sample for `volume-rank` documents `FID_COND_MRKT_DIV_CODE` as `J`, `NX`, `UN`, `W`; it does not list `Q`.
- Local KIS samples repeatedly use `FID_INPUT_ISCD="0001"` for KOSPI/KRX and `FID_INPUT_ISCD="1001"` for KOSDAQ.

## Required Changes

### Phase 1 - Documented Call Shape

- Add `input_iscd` support to `_kis_volume_rank()`.
- Default remains safe/backward-compatible: `market_div="J"`, `input_iscd="0000"`.
- Market label must be derived from `input_iscd`:
  - `0001` -> `KOSPI`
  - `1001` -> `KOSDAQ`
  - otherwise -> `ALL`

### Phase 2 - KR Screener Wiring

- Premarket KOSPI call must use:
  - `FID_COND_MRKT_DIV_CODE="J"`
  - `FID_INPUT_ISCD="0001"`
- Premarket KOSDAQ call must use:
  - `FID_COND_MRKT_DIV_CODE="J"`
  - `FID_INPUT_ISCD="1001"`
- Intraday KOSPI/KOSDAQ calls must use the same split.
- Remove live dependence on `market_div="Q"` for KOSDAQ volume-rank.

### Phase 3 - Diagnostics

- KOSDAQ raw zero warnings must include:
  - phase
  - market_div
  - input_iscd
  - vol_cnt
  - reserve_n
  - kospi_raw count
- This allows the next live run to confirm whether KOSDAQ is still zero despite the corrected call shape.

### Phase 4 - Tests

- Update existing KR screener tests to use `input_iscd` rather than `market_div="Q"`.
- Add/keep coverage that verifies:
  - KOSPI screen call uses `market_div="J", input_iscd="0001"`.
  - KOSDAQ screen call uses `market_div="J", input_iscd="1001"`.
  - KOSDAQ rows are labeled `market_type="KOSDAQ"`.
  - KOSDAQ raw zero warning includes `input_iscd=1001`.

### Phase 5 - Verification

- Run the focused test file:
  - `pytest test_trading_improvements.py -q`
- Run syntax compilation:
  - `python -m py_compile kis_api.py test_trading_improvements.py`
- Re-read this document and compare implemented items against Required Changes.

## Explicit Non-Goals

- Do not place real orders.
- Do not call broker balance/order APIs for verification.
- Do not change Path B historical rows in this phase.
- Do not modify KOSDAQ ranking thresholds or introduce new screener scoring logic.

## Completion Checklist

- [x] Phase 1 implemented.
- [x] Phase 2 implemented.
- [x] Phase 3 implemented.
- [x] Phase 4 tests updated/added.
- [x] Phase 5 verification passed.
- [x] Final MD comparison completed.

## Verification Results

- `python -m py_compile kis_api.py test_trading_improvements.py` passed.
- `pytest test_trading_improvements.py -q` passed: 124 passed, 2 warnings.
- Static search found no remaining live `market_div="Q"` KOSDAQ screen call in `kis_api.py`.

## Final Comparison

- Required Changes Phase 1: matched.
- Required Changes Phase 2: matched.
- Required Changes Phase 3: matched.
- Required Changes Phase 4: matched.
- Required Changes Phase 5: matched.

No omissions found against this document.
