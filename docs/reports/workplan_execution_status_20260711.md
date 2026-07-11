# 수익성 후속 워크플랜 실행 결과 — 2026-07-11

기준서: `profitability_followup_workplan_20260711.md`. 이 문서는 각 항목을 실제 실행·측정한 결과와
남은 게이트를 원인별(데이터/시간/운영자/라이브경로)로 분리해 기록한다. 실패 낙인 대신 사유와 다음 행동으로 맺는다.

## 요약 표

| 항목 | 상태 | 근거·측정 | 게이트 |
|---|---|---|---|
| P0-1 통제된 재시작 | ⚠️ 미완료 | 현재 PID 44320은 03:11 시작, KR probe 커밋 `01c5d96`은 11:48 생성 → 신규 코드 미적재 | 운영자 통제 재시작 |
| P0-2 Profit Path 발화 | ⏳ 미검증 | prediction_n **0**. 실제 hook 통과 전이라 표본부족과 배선결함을 아직 구분할 수 없음 | 재시작 후 첫 eligible 매수경로 |
| P0-3 broker truth | 🔁 세션 점검 | 03:11 재시작 당시 정합은 확인했으나 truth freshness는 영구 완료 항목이 아님 | 다음 거래 직전 fresh/trusted 재확인 |
| P1-1 US Swing 성숙 | ⏳ 시간 | ledger matured **0**/pending 5, breadth_tagged 5, handoff UNTOUCHED | 7/16 첫성숙→7/23 |
| P1-2 KR bullish probe | ⏳ forward | `kr_bullish_probe_report` rows **0** | 재시작+강세세션 |
| P1-3 Profit Path 승격 | ⏳ forward | forward sessions 0, matched 0 | 20세션·60matched |
| P1-4 실제 FX 확정 | ⏳ 보수 가정 유지 | US `pnl_pct_net` fee-only 행은 FX가 확정되지 않음. 명세 확인을 생략해도 canonical realized KRW로 승격할 수는 없음 | 실측 FX 또는 measured KRW 원장 |
| P1-5 2차 벤더 교차검증 | 🔒 데이터 | 독립 벤더 부재(Yahoo 단일) | 벤더 확보 |
| **P1-6 순수익 원장** | ⚠️ 부분완료·복구 | 추정 US 135건+불완전 파생 23건을 canonical에서 제거. coverage **49.7%**(KR 100%·US 37.4%), canonical curve 28일 cum −331,635원 | US measured KRW 원장 |
| **P2-1 상관 빈표본 버그** | ✅ 완료·커밋 | 4b3e994. max상관 0.46(≥0.5 0일)→"판정 불가" | — |
| **P2-2 조기익절 tier** | ⚠️ 재검증 | 정수주 반영 후 f=0.5: US executable 70/240, Δ+0.150%p; KR 34/51, Δ+0.979%p. 기존 결과는 실행불가능 부분매도 포함 | 정수주 OOS·forward |
| P2-3 risk-recovery runner | 🛠️ fail-closed | 사후 MAE를 초기 R로 쓰던 계산 제거. entry stop risk 277건 확인, ordered time 0건이라 수익 counterfactual 미출력 | minute-path·시간축 |
| P2-4 청산 시간축 세분 | 🔒 라이브경로 | mfe_time/mae_time은 7/10 배선완료. 세분(triggered/detected/sent/ack) 미배선 | 운영자·exit경로 |
| P2-5 spread/participation | 🔒 라이브경로 | spread_bps 결측 반복, participation 미저장 | 운영자·계측배선 |
| 관찰 Breadth S3 | 📌 diagnostic | 현재 레버는 닫았지만 40 forward 세션 재검 계약은 유지 | 재검조건 8개 |
| 종료 VIX S2 | ✅ 종결 | 방어 OOS기각·공격 부호불안정 | 별도 전략시 재개 |

## 이번 세션 실제 실행분 (신규 코드·측정)

### P2-1 — 상관 클러스터 빈 표본 판정 버그 (완료·커밋 4b3e994)
- 버그: 고상관(평균상관≥0.5) n=0인데 "고상관일 손실 더 큼" 하드코딩 출력.
- 수정: 평균상관 분포 출력 + 한 집단 비면 "판정 불가" + 데이터기반 판정.
- 측정: 동시청산 28일 평균상관 min0.16/중앙0.26/**max0.46**(≥0.5 **0일**). → A3/S1을 상관위험으로 정당화 불가, 동시손실=시장베타로 재분석.

### P1-6 — pnl_krw_net 백필 + equity curve (부분완료·오염 복구)
- 검토에서 US 135건이 실제 체결원금이 아니라 고정 500,000원으로 추정돼 canonical `pnl_krw_net`에 기록된 것을 확인했다.
- 추가로 US gross 파생 23건도 fee-only/FX 미확정이라 canonical realized KRW로 확정할 수 없었다.
- SQLite 일관 백업 `state/backups/decisions.db.bak_20260711_before_pnl_krw_repair` 생성 후 158건을 NULL로 복구했다.
- 유지: KR exact 62/62, US measured 95/254. canonical coverage **157/316=49.7%**.
- canonical measured/exact curve: 28일, cum **−331,635원**, MDD **−331,635원**. 이 역시 US coverage 37.4%라 전체 계좌곡선으로 부르지 않는다.
- 도구는 KR exact만 write하고, US measured notional/FX가 없으면 fail-closed하도록 수정했다. 모든 write는 자동 SQLite backup을 만든다.

### P2-2 — 조기익절 tier 정수주 교정 (재검증 필요)
- 기존 도구는 qty를 읽지 않아 1주 보유에도 25~50% 부분매도를 가정했다.
- 정수주 `sell_qty=floor(qty×f)`, `0 < sell_qty < qty`인 경우만 발화하도록 수정했다.
- f=0.5 재산출: US signal reach 53.3%지만 executable 29.2%(70/240), Δmean **+0.150%p**. KR executable 66.7%(34/51), Δmean **+0.979%p**.
- US 실제 최대값은 +17.167%로 보존됐지만 P90은 3.410→2.960으로 감소한다. OOS·forward 전에는 완료/승격 근거가 아니다.

## 검증
- py_compile: backfill_pnl_krw_net·early_tier_shadow_review·risk_recovery_runner_review 통과.
- 테스트: backfill 안전복구, early-tier 정수주, risk-recovery entry-stop/fail-closed 포함 **11 pass**.
- 백필 정합: unsafe US label 0, canonical coverage 49.7%, estimated canonical 포함 0 확인.

## 다음 행동 (게이트 해제 순)
1. **운영자**: 커밋 `01c5d96` 이후 통제 재시작 → P0-1 완료 여부 재확인.
2. **forward 자동축적**: P0-2/P1-1/P1-2/P1-3 → 실제 hook 발화와 성숙을 구분해 관찰.
3. **P1-6**: US measured KRW notional/FX 원천이 생길 때만 canonical coverage 확장.
4. **P2-3**: ordered minute path가 확보된 뒤 first-2R crossing·정수주·breakeven stop을 재생.
5. **운영자·라이브경로**: P2-4 시간축 세분·P2-5 spread/participation 계측 배선(다음 통제 재시작 동반).
6. **P2-2 조기익절**: 정수주 forward shadow Δ 유지 확인 → 운영자 승인 전 enforce 금지.

관련: [[target-calibration-lever-20260711]], [[session-handoff-signal-discovery-20260710]], [[today-plan-status-restart-gated-20260710]]
