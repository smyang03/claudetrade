# 이번 주 정산 완료분 — 종목별 전건 (2026-09-14 ~ 2026-09-16)

read-only. `tools/research/research_week_closed_by_ticker_20260916.py`(규약은 docstring).
**창은 정산일 기준**(진입일 아님). **판정은 지수 초과분**(KR=069500 / US=IWM).
**전부 쉐도우다 — 실제 손익이 아니다**(09-01 운영자 결정으로 실매수 0).

## A. 문턱 통과 레인 — 패닉 ETF v2 (유일하게 사전등록 통과한 축)

| 종목 | 진입일 | 강도 | 단위 | net | 계약 |
|---|---|---|---:|---:|---|
| `229200` | 2026-09-14 | normal | 1 | **-0.20%** | 종가매수/익일시가매도 |
| `233740` | 2026-09-14 | normal | 1 | **-0.83%** | 종가매수/익일시가매도 |

정산 2건 · 단순합 -1.02% · 평균 -0.51%
⚠️ 이 레인은 1박 오버나이트라 지수 초과 창이 다르다 — 벤치는 같은 밤 지수라 위 net이 이미 '지수 대비'가 아니다. 축 판정은 사전등록 연도매칭 표로만 한다.

종가 rung(09-13 신설, 관측 전용): forward **0건** — 이번 주 정산 0.

## B. 라이브 규칙 쉐도우 — 실제 돈이 나갔다면 이대로였다

이번 주 진입·정산된 KR 급락 라이브 규칙 통과 건 없음(전체 pass_all 정산 누적 4건).

## C. 인큐베이터 — 종목별 전건 (판정 대상 arm만)

행 176건이 **74종목·진입일 조합**으로 묶인다(같은 픽을 여러 arm이 잡으면 net은 같다 — arm 수는 중복이지 확인이 아니다).
⚠️ **TP 행의 net은 계약 상수다**(KR +11.75% / US +11.50% = TP12 − 비용). 여기서 정보는 금액이 아니라 **몇 종목이 +12% 벽을 쳤는가**라는 건수다.

| 종목 | 시장 | 진입일 | 출구 | net | **초과** | D | 잡은 arm |
|---|---|---|---|---:|---:|---:|---|
| **TYRA** | US | 2026-09-10 | TP | +19.50% | 조기청산 | 10 | `c_us_panic_all` |
| **290550** | KR | 2026-09-08 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen_nomajor` |
| **441270** | KR | 2026-09-08 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen_nomajor` |
| **462860** | KR | 2026-09-08 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen_nomajor` |
| **160190** | KR | 2026-09-09 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen_nomajor` |
| **456160** | KR | 2026-09-09 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen_nomajor` |
| **062970** | KR | 2026-09-09 | TP | +11.75% | 조기청산 | 10 | `c_kr_insider_cluster` |
| **336260** | KR | 2026-09-10 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen_nomajor` |
| **462860** | KR | 2026-09-10 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen_nomajor` |
| **004310** | KR | 2026-09-10 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen_nomajor` |
| **014950** | KR | 2026-09-10 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen_nomajor` |
| **032940** | KR | 2026-09-11 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen_nomajor` |
| **043260** | KR | 2026-09-11 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen_nomajor` |
| **048770** | KR | 2026-09-11 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen_nomajor` |
| **218410** | KR | 2026-09-11 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen_nomajor` |
| **459550** | KR | 2026-09-11 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen_nomajor` |
| **010580** | KR | 2026-09-14 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen5_nobio_rg`, `c_kr_fallen_nomajor` 외 2 |
| **058610** | KR | 2026-09-14 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen5_nobio_rg`, `c_kr_fallen_nomajor` 외 2 |
| **064290** | KR | 2026-09-14 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen5_nobio_rg`, `c_kr_fallen_nomajor` 외 2 |
| **089890** | KR | 2026-09-14 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen5_nobio_rg`, `c_kr_fallen_nomajor` 외 2 |
| **228340** | KR | 2026-09-14 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen5_nobio_rg`, `c_kr_fallen_nomajor` 외 2 |
| **293580** | KR | 2026-09-14 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen5_nobio_rg`, `c_kr_fallen_nomajor` 외 2 |
| **413630** | KR | 2026-09-14 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen5_nobio_rg`, `c_kr_fallen_nomajor` 외 2 |
| **437730** | KR | 2026-09-14 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen5_nobio_rg`, `c_kr_fallen_nomajor` 외 2 |
| **452450** | KR | 2026-09-14 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen5_nobio_rg`, `c_kr_fallen_nomajor` 외 2 |
| **072950** | KR | 2026-09-15 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen_nomajor` |
| **140430** | KR | 2026-09-15 | TP | +11.75% | 조기청산 | 7 | `c_kr_fallen5_nobio`, `c_kr_fallen_nomajor` |
| **ASAN** | US | 2026-09-08 | TP | +11.50% | 조기청산 | 7 | `us_live_dvol`, `us_wide_all_v2`, `us_wide_dvol` 외 4 |
| **WIX** | US | 2026-09-08 | TP | +11.50% | 조기청산 | 7 | `us_wide_all_v2`, `us_wide_dvol_k3` |
| **TWST** | US | 2026-09-08 | TP | +11.50% | 조기청산 | 7 | `c_us_slow8` |
| **GDDY** | US | 2026-09-09 | TP | +11.50% | 조기청산 | 7 | `c_us_fallen_regime`, `us_live_dvol`, `us_wide_all_v2` 외 1 |
| **MNDY** | US | 2026-09-09 | TP | +11.50% | 조기청산 | 7 | `c_us_fallen_regime`, `us_wide_all_v2` |
| **APPN** | US | 2026-09-09 | TP | +11.50% | 조기청산 | 7 | `c_us_fallen_regime` |
| **SAIL** | US | 2026-09-09 | TP | +11.50% | 조기청산 | 7 | `c_us_fallen_regime` |
| **IT** | US | 2026-09-09 | TP | +11.50% | 조기청산 | 7 | `c_us_fallen_regime` |
| **AFRM** | US | 2026-09-10 | TP | +11.50% | 조기청산 | 7 | `c_us_fallen_regime` |
| **ASAN** | US | 2026-09-10 | TP | +11.50% | 조기청산 | 7 | `c_us_fallen_regime` |
| **TYRA** | US | 2026-09-10 | TP | +11.50% | 조기청산 | 7 | `c_us_fallen_regime` |
| **QLYS** | US | 2026-09-11 | TP | +11.50% | 조기청산 | 7 | `c_us_fallen_regime` |
| **DSGX** | US | 2026-09-11 | TP | +11.50% | 조기청산 | 7 | `c_us_slow8`, `c_us_slow8_regime` |
| **HUBS** | US | 2026-09-11 | TP | +11.50% | 조기청산 | 7 | `c_us_slow8`, `c_us_slow8_regime` |
| **QLYS** | US | 2026-09-14 | TP | +11.50% | 조기청산 | 7 | `c_us_fallen_regime` |
| **TENB** | US | 2026-09-14 | TP | +11.50% | 조기청산 | 7 | `c_us_fallen_regime` |
| **DAVE** | US | 2026-09-14 | TP | +11.50% | 조기청산 | 7 | `c_us_slow8`, `c_us_slow8_regime` |
| **TEM** | US | 2026-09-14 | TP | +11.50% | 조기청산 | 7 | `c_us_slow8`, `c_us_slow8_regime` |
| **TEAM** | US | 2026-09-09 | TP | +7.50% | 조기청산 | 7 | `b2_leader_pb` |
| **TEAM** | US | 2026-09-10 | TP | +7.50% | 조기청산 | 7 | `b2_leader_pb` |
| **WIX** | US | 2026-09-04 | D_MAT | +2.72% | **+5.64%** | 7 | `us_live_dvol`, `us_wide_all_v2`, `us_wide_chg` 외 8 |
| **TSN** | US | 2026-09-04 | D_MAT | +2.34% | **+5.25%** | 7 | `us_wide_nomax` |
| **THC** | US | 2026-09-03 | D_MAT | -0.57% | **+2.34%** | 7 | `b2_leader_pb` |
| **DPC** | US | 2026-09-10 | BE | -0.67% | 조기청산 | 10 | `c_us_fallen_regime`, `c_us_panic_all` |
| **MMYT** | US | 2026-09-10 | BE | -0.98% | 조기청산 | 10 | `c_us_fallen_regime`, `c_us_panic_all` |
| **GLXY** | US | 2026-09-10 | BE | -1.04% | 조기청산 | 10 | `c_us_fallen_regime`, `c_us_panic_all`, `us_wide_all_v2` 외 1 |
| **NIO** | US | 2026-09-10 | BE | -1.05% | 조기청산 | 7 | `c_us_slow8`, `c_us_slow8_regime` |
| **VSAT** | US | 2026-09-10 | BE | -1.27% | 조기청산 | 10 | `c_us_fallen_regime`, `c_us_panic_all` |
| **QS** | US | 2026-09-10 | BE | -1.29% | 조기청산 | 10 | `c_us_fallen_regime`, `c_us_panic_all` |
| **BKV** | US | 2026-09-10 | BE | -1.37% | 조기청산 | 10 | `c_us_fallen_regime`, `c_us_panic_all` |
| **HPE** | US | 2026-09-11 | BE | -2.20% | 조기청산 | 10 | `c_us_fallen_regime`, `c_us_panic_all` |
| **FIGS** | US | 2026-09-09 | BE | -2.49% | 조기청산 | 7 | `c_us_fallen_regime` |
| **IESC** | US | 2026-09-10 | BE | -2.54% | 조기청산 | 10 | `c_us_fallen_regime`, `c_us_panic_all` |
| **CART** | US | 2026-09-09 | BE | -2.78% | 조기청산 | 7 | `c_us_fallen_regime` |
| **OMC** | US | 2026-09-03 | D_MAT | -3.18% | **-0.26%** | 7 | `us_wide_nomax` |
| **FTAI** | US | 2026-09-10 | BE | -3.84% | 조기청산 | 10 | `c_us_fallen_regime`, `c_us_panic_all` |
| **HUT** | US | 2026-09-11 | BE | -4.92% | 조기청산 | 10 | `c_us_fallen_regime`, `c_us_panic_all`, `us_wide_all_v2` 외 1 |
| **FPS** | US | 2026-09-11 | BE | -5.03% | 조기청산 | 10 | `c_us_fallen_regime`, `c_us_panic_all` |
| **BZ** | US | 2026-09-10 | BE | -5.07% | 조기청산 | 7 | `c_us_slow8`, `c_us_slow8_regime` |
| **COIN** | US | 2026-09-14 | BE | -5.40% | 조기청산 | 7 | `c_us_slow8`, `c_us_slow8_regime` |
| **BBWI** | US | 2026-09-10 | BE | -5.50% | 조기청산 | 10 | `c_us_fallen_regime`, `c_us_panic_all` |
| **IP** | US | 2026-09-02 | D_MAT | -7.07% | **-5.91%** | 7 | `us_slow_fallen` |
| **WOLF** | US | 2026-09-10 | BE | -8.52% | 조기청산 | 10 | `c_us_fallen_regime`, `c_us_panic_all` |
| **FIG** | US | 2026-09-03 | D_MAT | -9.79% | **-6.88%** | 7 | `us_wide_all_v2`, `us_wide_chg`, `us_wide_dvol_k3` |
| **KVYO** | US | 2026-09-03 | D_MAT | -12.44% | **-9.53%** | 7 | `us_wide_all_v2`, `us_wide_dvol_k3`, `us_wide_dvolasc` 외 1 |
| **224060** | KR | 2026-09-11 | SL | -25.89% | 조기청산 | 10 | `c_kr_insider_cluster`, `c_kr_insider_k1`, `c_kr_insider_k1_ex` |
| **475460** | KR | 2026-09-02 | SL | -65.71% | 조기청산 | 20 | `kr_bonus_issue` |

### 출구 사유별 — 승자 편향이 어디서 오는가

| 출구 | 종목수 | net 평균 | **초과 평균** | 비고 |
|---|---:|---:|---:|---|
| TP | 47 | +11.64% | 산출 불가(조기청산) | 익절 — **먼저 닫힌다**. 편향의 원천 |
| BE | 18 | -3.11% | 산출 불가(조기청산) | BE락 발동(US 한정) — 본전 근처에서 끊김 |
| D_MAT | 7 | -4.00% | **-1.33%** (n=7) | **만기 도달 — 편향 없음** |
| SL | 2 | -45.80% | 산출 불가(조기청산) | 손절 |

**이 표의 net 평균으로 전략을 판정하지 않는다.** 3일짜리 정산 창은 TP가 47/74를 차지해 구성 자체가 승자 쪽으로 기울어 있다. 판정은 [[full_system_audit_20260912]]의 지수 초과·날짜 클러스터 t로 한다.

### 편향 없는 표본만 — 만기 도달(D_MAT) 종목

| 종목 | 시장 | 진입일 | net | **초과** | 잡은 arm |
|---|---|---|---:|---:|---|
| **WIX** | US | 2026-09-04 | +2.72% | **+5.64%** | `us_live_dvol`, `us_wide_all_v2`, `us_wide_chg` 외 8 |
| **TSN** | US | 2026-09-04 | +2.34% | **+5.25%** | `us_wide_nomax` |
| **THC** | US | 2026-09-03 | -0.57% | **+2.34%** | `b2_leader_pb` |
| **OMC** | US | 2026-09-03 | -3.18% | **-0.26%** | `us_wide_nomax` |
| **IP** | US | 2026-09-02 | -7.07% | **-5.91%** | `us_slow_fallen` |
| **FIG** | US | 2026-09-03 | -9.79% | **-6.88%** | `us_wide_all_v2`, `us_wide_chg`, `us_wide_dvol_k3` |
| **KVYO** | US | 2026-09-03 | -12.44% | **-9.53%** | `us_wide_all_v2`, `us_wide_dvol_k3`, `us_wide_dvolasc` 외 1 |

만기 도달 7종목 · net 평균 -4.00% · **초과 평균 -1.33%** · 초과 양수 3/7
표본이 이 크기면 판정이 아니다 — 이번 주가 어땠나의 기록이다.

## D. 기저선(`x*`) — 판정 대상 아님

787건 정산(조건 전량 매수 모집단). net 평균 -0.86% · 범위 -27.32% ~ +11.75%.
후보를 뽑아낸 denominator라 여기서 성과를 판정하지 않는다. 종목 나열은 생략.

