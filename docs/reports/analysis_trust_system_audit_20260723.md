# 분석 신뢰도 — 전체 시스템 점검 (2026-07-23)

운영자 지적: 거짓 레버를 하나씩 찾지 말고 전체적으로 상세 점검해라. 몇 번째인지도 모르겠다.

맞다. 오늘 밟은 함정(ret_5m 대박예측 · KR 가드 완화 · KR capture)은 우연이 아니라
**분석 표면 전체가 같은 지뢰밭 위에 있어서**다. 하나씩 잡는 대신 그 부류 전체를 뽑았다.

도구: `tools/analysis_trust_audit.py`(신설, 재실행 가능) + tools/ 40개 스캔.

## 0. ★★ 근본 — 저장소 전 capture 분석이 오염돼 있다

```
mfe_time: 0 / 318 (canonical·learning 양쪽)
mfe_pct 316건 전부(100%) backfill — live-observed 0건
```

**우리 보유 중 실제 고점 시각(mfe_time)이 단 한 건도 수집된 적이 없다.** 따라서
`capture = net / mfe_pct`를 계산하는 모든 분석은 분모가 일봉 backfill(그날 고/저)이라
**우리 보유기간 고점이 아니다.** 오늘 "KR capture −8%"가 착시였던 이유가 여기다 —
전 capture 분석이 같은 오염 위에 있었다.

원인: `observed_peak_at`은 `v2_path_runs.plan_json`에 일부 있으나(6월 37건),
learning/canonical의 `mfe_time`으로 **전파되지 않는다.** + 라이브 excursion 추적이
PathB에만 있고 7/10 이후 PathB 체결이 0이라 신규 수집도 멈췄다(오늘 non-PathB 추적 추가).

## 1. 실패 모드 7종 (거짓 레버의 부류)

```
T1  gross(수수료·FX 미반영)를 net 대신 판정에 사용
T2  고정 horizon(1일 forward)을 우리 보유기간 대신 사용
T3  backfill MFE를 우리 보유 중 고점처럼 capture 계산
T4  편향 부분표본(NOT NULL만)을 전체 대신 분석
T5  타임존 불일치(KST vs UTC 문자열 비교)
T6  테이블 간 전파 갭(canonical vs learning 커버리지 상이)
T7  퇴역/의미변경 필드를 라이브 축으로 오독
```

오늘 3건 매핑: ret_5m=T4(편향표본) · KR가드=T1+T2(gross·1일) · KR capture=T3(backfill).

## 2. 데이터 측 신뢰맵 (analysis_trust_audit.py 출력)

```
[T6] 전파 갭: mfe_time 0/318 (정정 후 mfe_pct 자체는 canonical=learning 316 일치)
[T3] backfill: mfe_pct 100% backfill — capture 분석 전부 무효
[T1] gross만: closed 318 중 2건(net 판정 불가)
[T2] 보유기간: US 중앙 2.55h(→240분) · KR 중앙 0.65h(→60분). 1일 forward 남용 금지
[T7] claude_action 의미변경(7/08 selection→judge)
```

## 3. 도구 측 신뢰맵 — tools/ 40개 스캔

### 판정에 gross/backfill/raw-forward를 쓰는 위험 도구 상위 5 (수정 후보)

| # | 도구 | 함정 | 스테이크 |
|---|---|---|---|
| 1 | `analyze_kr_promotion_candidates.py` | T1+T2+T4 (gross + raw h60 로 LIVE_READY 판정) | **라이브 활성 판정** |
| 2 | `pathb_capture_leak_review.py` | T1 (leak/capture/손익비 전부 순수 gross) | net 부재 |
| 3 | `hold_advisor_outcome_review.py` | T1+T5 (gross로 profit_guard kill/rollback + 문자열 tz) | **라이브 config 토글** |
| 4 | `market_state_exposure_backtest.py` | T1 (gross − 가정 cost 0.5%로 노출축소 판정) | 조작된 비용 |
| 5 | `diamond_hands_target_only_backtest.py` | T1 (순수 gross·수수료0으로 출구정책 판정) | 보유연장 구조적 유리 착시 |

### backfill-MFE(T3)를 capture 판정에 쓰는 도구 4개
`capture_net_review.py:208`(헤드라인 net_capture) · `capture_tier_effect_review.py:72` ·
`peak_floor_counterfactual.py:131` · `capture_leak_monitor.py:35`.
→ §0 때문에 **넷 다 현재 무효**(분모가 100% backfill). mfe_time 복구 전엔 capture 수치 인용 금지.

### tz 문자열비교(T5) — 판정 영향
`cluster_halt_counterfactual.py:89` · `reward_risk_enforce_review.py:112` ·
hold_advisor 3종 · `candidate_consensus_outcome_review.py:151`.

### 모범 사례 (이 패턴을 표준으로)
- `risk_recovery_runner_review.py:83` — mfe_time/mae_time 존재 + `mfe_at<mae_at` 강제
- `early_path_our_net_validation.py` · `candidate_path_prediction_lab.py` ·
  `simulate_ladder_floor_change.py` — 저장 MFE 대신 봉에서 보유창 내 MFE 재구성(T3 회피)
- `net_profitability_review.py` · `improvement_net_monitor.py` · `bleed_bucket_analysis.py` — net 전용
- `monitoring_ops_report.py` — 모든 출력에 `*_change_allowed:False` 강제(판정 차단 설계)

## 4. 판정 게이트 — 분석 전 반드시 통과 (코드·문서 계약)

```
① net?        pnl_pct_net 사용. gross(pnl_pct)로 라이브 판정 금지.
② horizon?    시장별 보유 중앙(US 240·KR 60분). 1일 forward 남용 금지.
③ MFE live?   capture 는 mfe_time 있는 행만(audit/mfe_trust.py). backfill 분모 금지.
④ 전체표본?   NOT NULL 필터 편향 확인. coverage 표기.
⑤ 테이블일치? canonical vs learning 갭 없는 쪽을 truth.
⑥ tz-aware?   datetime 파싱 후 비교. 문자열 비교 금지.
```

`audit/mfe_trust.py`(오늘 신설)가 ③을 코드로 강제한다. `tools/analysis_trust_audit.py`가
전체를 재실행 가능하게 스캔한다. **이제 몇 번째 함정인지 셀 필요 없다 — 분석 전 이 감사를 돌린다.**

## 5. 개선 우선순위

| # | 항목 | 성격 |
|---|---|---|
| P0 | **mfe_time 수집·전파 복구** — observed_peak_at → learning.mfe_time. 없으면 전 capture 분석이 영구 무효 | 측정 근본 |
| P0 | 위험 도구 상위 5개 net 전환 (특히 라이브 판정하는 #1·#3) | 거짓 레버 발생원 차단 |
| P1 | capture 4종에 mfe_trust 게이트 적용 (backfill 분모 차단) | 오염 차단 |
| P1 | tz 문자열비교 5곳 tz-aware 파싱 전환 | 국면·최신선택 오류 |
| 상시 | 분석 전 `analysis_trust_audit.py` 실행 | 재발 방지 표준 |

## 6. 이 점검이 답한 것

"몇 번째인지 모르겠다"는 정확한 진단이었다 — 하나씩 잡는 방식 자체가 틀렸다.
근본은 **분석 표면이 gross·backfill·고정horizon 위에 있고, net·live·우리horizon 게이트가
없던 것.** 오늘 신설한 `mfe_trust.py`(게이트) + `analysis_trust_audit.py`(스캔) + 위
6게이트 계약이 그 부류 전체를 막는다. 남은 건 P0 두 개(mfe_time 복구 · 위험도구 net 전환)다.
