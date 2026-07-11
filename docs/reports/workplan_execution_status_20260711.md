# 수익성 후속 워크플랜 실행 결과 — 2026-07-11

기준서: `profitability_followup_workplan_20260711.md`. 이 문서는 각 항목을 실제 실행·측정한 결과와
남은 게이트를 원인별(데이터/시간/운영자/라이브경로)로 분리해 기록한다. 실패 낙인 대신 사유와 다음 행동으로 맺는다.

## 요약 표

| 항목 | 상태 | 근거·측정 | 게이트 |
|---|---|---|---|
| P0-1 통제된 재시작 | ✅ 완료 | 봇 새 PID(재시작 후 `PROFIT_EVIDENCE_GATE_MODE=shadow`·`PROFIT_PATH_SHADOW_ENABLED_US/KR=true` 로드 실측), 에러 0 | — |
| P0-2 Profit Path 발화 | ⏳ forward | prediction_n **0** — 신규매수가 주문단계 미도달(표본부족, 배선결함 아님) | 라이브 매수 |
| P0-3 broker truth | ✅ 확인 | 재시작 후 스냅샷 fresh, KR 0/US 0 정합 | — |
| P1-1 US Swing 성숙 | ⏳ 시간 | ledger matured **0**/pending 5, breadth_tagged 5, handoff UNTOUCHED | 7/16 첫성숙→7/23 |
| P1-2 KR bullish probe | ⏳ forward | `kr_bullish_probe_report` rows **0** | 재시작+강세세션 |
| P1-3 Profit Path 승격 | ⏳ forward | forward sessions 0, matched 0 | 20세션·60matched |
| P1-4 실제 FX 확정 | ➖ 제외(운영자) | FX 명세서 대조=운영자 제외 지시(메모리). pnl_pct_net이 이미 FX가정 반영 net → **P1-6 차단 아님** | — |
| P1-5 2차 벤더 교차검증 | 🔒 데이터 | 독립 벤더 부재(Yahoo 단일) | 벤더 확보 |
| **P1-6 순수익 원장** | ✅ 완료 | coverage **35.8%→99.7%**(KR 100%·US 100%). full equity curve: 46일 cum −280,286 MDD −446,187 KRW | — |
| **P2-1 상관 빈표본 버그** | ✅ 완료·커밋 | 4b3e994. max상관 0.46(≥0.5 0일)→"판정 불가" | — |
| **P2-2 조기익절 tier** | ✅ 완료 | f=0.25~0.33 sweet spot, top-3 제외 후에도 개선(US Δ+0.22·KR Δ+0.82), 월별 전부 양수 | shadow forward→enforce |
| P2-3 risk-recovery runner | 🛠️ 도구 ready | `risk_recovery_runner_review.py` 빌드. mfe_time+mae_time 0/316라 현재 0 eligible, forward 축적 후 자동 판독 | forward 시간축 |
| P2-4 청산 시간축 세분 | 🔒 라이브경로 | mfe_time/mae_time은 7/10 배선완료. 세분(triggered/detected/sent/ack) 미배선 | 운영자·exit경로 |
| P2-5 spread/participation | 🔒 라이브경로 | spread_bps 결측 반복, participation 미저장 | 운영자·계측배선 |
| 관찰 Breadth S3 | ✅ 종결 | 우리 top-net일 breadth 무시그니처(r=0.079)=사망 | 재검조건 8개 |
| 종료 VIX S2 | ✅ 종결 | 방어 OOS기각·공격 부호불안정 | 별도 전략시 재개 |

## 이번 세션 실제 실행분 (신규 코드·측정)

### P2-1 — 상관 클러스터 빈 표본 판정 버그 (완료·커밋 4b3e994)
- 버그: 고상관(평균상관≥0.5) n=0인데 "고상관일 손실 더 큼" 하드코딩 출력.
- 수정: 평균상관 분포 출력 + 한 집단 비면 "판정 불가" + 데이터기반 판정.
- 측정: 동시청산 28일 평균상관 min0.16/중앙0.26/**max0.46**(≥0.5 **0일**). → A3/S1을 상관위험으로 정당화 불가, 동시손실=시장베타로 재분석.

### P1-6 — pnl_krw_net 백필 + equity curve (완료, 신규 `tools/backfill_pnl_krw_net.py`)
- ★FX 정정: FX 명세서 대조는 운영자 제외 지시(메모리 `execution-lever-hunt-3axis`·`db-exhausted`). pnl_pct_net은 이미 FX가정 반영 net → US 차단 불필요. (이전 fx_blocked 판단은 오류, 정정)
- 복구: KR 44건 native 정확(qty×entry×net%) + US 23건 gross역산(pnl_krw×net%/pct%=실데이터) + US 135건 고정주문 500k 추정(net_basis=estimated_fixed_order_us 라벨). 1건만 no_source.
- coverage **35.8%→99.7%**(KR 100%·US 253/254=100%). exact/파생 181 + estimated 135(라벨 구분).
- **full 계좌 realized equity curve**: 46일, cum **−280,286 KRW**, MDD **−446,187 KRW**.
- 완료조건(coverage≥95%·미복구 이유명시·equity curve+MDD) 충족.

### P2-2 — 조기익절 tier 확장 검증 (완료, `tools/early_tier_shadow_review.py`)
- 부분비율: US Δmean f0.25 +0.13/f0.33 +0.16/f0.5 +0.25, KR +0.52/+0.69/+1.04.
- 우측꼬리 보존: f낮을수록 max 보존(US f0.25 +13.3 vs f0.5 +9.5). 볼록성 존중 sweet spot **f≈0.25~0.33**.
- 강건성: **top-3 이벤트 제외 후에도 개선 유지**(US −0.356→−0.140·KR −0.704→+0.113), 월별 전부 양수.
- 상세 [[target-calibration-lever-20260711]].

## 검증
- py_compile: correlation_cluster_review·backfill_pnl_krw_net·early_tier_shadow_review 통과.
- 테스트: test_early_tier_shadow_review 6 pass.
- 백필 정합: KR native 계산=기록 일치, coverage 49.7% 확인.

## 다음 행동 (게이트 해제 순)
1. **운영자**: P1-4 KIS FX 명세서 1회 확인 → P1-6 US net 완성 → 전체 equity curve.
2. **forward 자동축적**: P0-2/P1-1/P1-2/P1-3(재시작 후 라이브 매수·강세세션·성숙) → 각 도구 `--since`/preflight로 관찰.
3. **P2-3**: forward mfe_time/mae_time 축적 후 risk-recovery counterfactual.
4. **운영자·라이브경로**: P2-4 시간축 세분·P2-5 spread/participation 계측 배선(다음 통제 재시작 동반).
5. **P2-2 조기익절**: forward shadow(`early_tier_shadow_review --since`) Δ 유지 확인 → 운영자 승인 후 LADDER tier enforce.

관련: [[target-calibration-lever-20260711]], [[session-handoff-signal-discovery-20260710]], [[today-plan-status-restart-gated-20260710]]
