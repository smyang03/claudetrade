# 매수·매도 파이프라인 전수 점검 및 정상화 — 2026-07-14

선행 리포트 `path_a_commit_and_entry_window_review_20260714.md`의 후속 작업 결과다.

## 결론

- **매수 0의 주 원인(RR 게이트 배선)은 커밋돼 있었으나 실행 중인 봇에 미반영 상태였다.** 00:32 통제 재시작으로 반영했고, 재시작 6분 만에 **US CRM 플랜이 등록(WAITING)됐다 — 7/10 이후 첫 플랜 생산**.
- judge→플랜→등록→진입감시→주문 체인은 HEAD 기준 **끊긴 곳 없음**(하드코딩 RR 1.2 잔재 없음, 시장별 단일 소스 확인).
- 진입 차단 게이트 전수 점검: **상시 차단 상태에 빠진 게이트 없음**. 대부분 무포지션 상태에서 자기제한적(스톱 클러스터·HALT·리스크오프 캡은 거래가 있어야 발동).
- 선행 리포트 §6의 1~3(후보 단위 attribution)을 구현·실측했다. **최대 병목은 `expired`(57% 착시)가 아니라 `no_evaluation`**이며, 이는 대부분 rule_direct WATCH-only **정책 효과**다(아래 §3).

## 1. 수행한 작업

| 작업 | 결과 |
|---|---|
| 라이브 스택 통제 재시작 (00:32) | 미반영 커밋 3개(55be620·33c019e·7989983) 반영. 역할별 단일 프로세스, preflight ALLOW_START(hard 0), 에러 0, WS 29/29 구독 |
| 플랜 생산 재개 확인 | 00:38 US CRM zone=168–169.2 RR게이트 통과·등록(WAITING). guardian 게이트 신선(<900s) |
| 죽은 로거 수정 라이브 검증 | kis_api 로그가 파일(jsonl)에 실기록되는 것 확인 |
| ORP attribution 후보단위 v2 구현 | `tools/orp_timing_attribution_report.py` — 첫 평가·첫 실패 사유·지연 버킷·미평가 원인 태그. 테스트 2건 추가 |
| 침묵 누수 2건 관측화 | ① waiting scan 가격≤0 조용한 skip → 세션당 1회 경고 ② 후보 feasibility 주석 실패 삼킴 → 경고 로그 (커밋 3307960) |
| stale 주석 정정 | 존재하지 않는 "RR 1.2 fallback" 언급 3곳 |

## 2. 후보 단위 최초 실패 사유 기준선 (2026-06-15~, live)

상호배타 분류. raw probe 행 비율(57%)은 병목률로 쓰지 않는다.

| 사유 | KR (n=215) | US (n=275) |
|---|---:|---:|
| **no_evaluation** | **174 (81%)** | **194 (71%)** |
| expired | 34 | 61 |
| range | 3 | 3 |
| pullback | 0 | 9 |
| volume | 0 | 2 |
| not_formed/forming | 1 | 6 |
| disabled | 3 | 0 |

첫 평가 지연 버킷: KR 0–2분 6 / 2분 초과 35 / 미평가 174 · US 0–2분 30 / 2분 초과 51 / 미평가 194.

미평가 원인 태그:
- KR: 세션 전체 ORP 평가 0 = **122** (ORP 비활성/평가루프 미도달 세션), 그 종목만 미평가 34, 선정이 마지막 probe 이후 18
- US: **그 종목만 미평가 187** (세션에 다른 종목 평가는 존재)

## 3. no_evaluation의 뿌리 = watch_only 정책 (배선 버그 아님)

`trading_bot.py`의 티커 루프에서 **watch_only skip(continue)이 전략 평가·probe 기록보다 앞**에 있다. `SELECTION_RULE_DIRECT_KR/US=true`가 후보를 전원 WATCH로 만들므로, Path A 전략은 trade_ready 후보가 없는 한 **구조적으로 침묵**한다(오늘 US 세션 entry_skip reason=watch_only 232건 실측). 이는 7/8 운영자 결정(룰 후보 + judge 플랜 + Claude 출구)의 의도된 효과다.

→ **운영자 결정 필요 (선행 리포트 §6-5와 동일)**: ① 현 구조 유지(진입은 Path B judge 플랜 단독, Path A WATCH-only를 정상으로 문서화) 또는 ② Path A 재가동을 원하면 rule-direct 이후 단일 종목 judge에 PROBE_READY/BUY_READY 권한 부여를 별도 설계·검증. 현 구조에서 매수 재개의 실효 레버는 Path B 플랜 생산이며, 이는 RR 배선 수정 반영으로 이미 재개됐다.

## 4. 감사에서 확인된 사항 (수정 불요·관찰 대상)

- **Guardian 게이트 = 잠재 fail-closed 리스크**: `live_guardian`이 15분 이상 죽으면 해당 시장 매수 100% 조용히 차단(`trading_bot.py:10708`, scan 진입 `pathb_runtime.py:3216`). 현재는 정상(신선). watchdog이 guardian을 되살리는 구조라 상시 위험은 아니나, "매수가 다시 0이 되면 1순위로 확인할 곳".
- **strict-feature fail-closed 연쇄**: 분봉 커버리지 실패 시 후보가 희소 피처로 judge에 도달해 3중 거부(`post_open_feature_quality_fail_closed` 등)로 전멸 가능. 이번에 feasibility 주석 실패를 관측화해 추적 가능해짐.
- **US midday block**: UTC 16시 고정 1시간 차단 — DST로 개장 대비 위치가 계절 이동하나 영구 차단 아님.
- **sharp reversal guard**: 지수 피드가 None을 지속 반환하면 마지막 True가 잔류할 수 있는 구조(피드 정상이면 매 사이클 재계산). 관찰만.
- **출구 fail-safe 확인**: Claude 검토(AUTO_SELL_REVIEW) 실패 시 기본 HOLD지만 `policy_protective_stop`/`policy_hard_stop`은 SELL로 강제, 긴급 사유(daily_loss_stop·broker_mismatch·operator_kill·pathb_kill)는 검토 우회 — 매도 영구 차단 경로 없음.

## 5. 운영자 판단 대기 항목

1. **zombie ORDER_ACKED 3건** (US ARM 6/11·FUN 6/10·MUSA 6/10, 브로커 실포지션 0): 라이브 경로는 전부 session_date 스코프라 현재 무해. 단 `trading_bot.py:22199`(브로커 동기화 메타데이터 복구)만 세션 무관 조회라, 향후 동일 종목 보유 시 stale 메타가 붙을 수 있음. `tools/reconcile_live_truth.py`는 이 케이스에 `keep_position`을 제안(브로커 0인데도)해 자동 정리 부적합 — 수동 정리 여부 결정 필요.
2. **US RR 임계 1.5**: RR 분모 변경(7/1) 후 분포가 낮아진 상태에서 US만 1.5 유지 중. KR(1.1)과 달리 재캘리 미실시 — US 플랜 생산이 계속 얇으면 재캘리 검토 대상(변경은 운영자 승인 후).
3. **Path A 정책** (§3).

## 6. 검증

- 집중 테스트 29 passed(재시작 전) + pathb/orp/price_plan/waiting 579 passed(수정 후) + attribution 테스트 2건 신규.
- mojibake 통과. 커밋: 3307960.

## 7. 선제 시뮬레이션 (개장 대기 없이 검증 — 2026-07-14 02:00 추가)

**리허설 하네스**(`tools/ops_rehearsal.py`, 라이브 상태 보호 가드 내장) 전 시나리오 통과:
`kr_patha_buy` · `us_pathb_buy` · `us_pathb_sell_target` · `broker_truth_fail_closed` · `order_unknown_reconcile` — 5/5 ok.

**RR 매트릭스 시뮬**(라이브 env 로딩 순서 재현: `.env.live` → `env_overrides` 덮어쓰기, 실판정 함수에 합성 플랜 주입):

| 검증 항목 | 결과 |
|---|---|
| 실효 임계 해석 | KR 1.1 / US 1.5 (등록·zone update·judge 경로 모두 동일) |
| 등록 경로 RR 매트릭스 | KR: 1.15~2.0 PASS, ≤1.1 거부 / US: 1.5~2.0 PASS, ≤1.45 거부 |
| judge 경로(strict features) | KR 1.15 PASS·1.05 거부 / US 1.55 PASS·1.3 거부 — 시장별 분리 작동 |
| confidence 게이트 | 0.5 이상 통과(경계 포함), 0.49 거부 |
| RR 분모 | consistent(존상단 앵커) 확인 — 구분모였다면 1.5 설계 플랜이 2.25로 과대 |
| judge strict 필수필드 | `invalid_if`·구조적 근거 없는 플랜은 RR 무관 거부 (CRM 실플랜은 보유 확인) |

**경계값 노트**: RR이 산술상 정확히 1.1인 플랜은 부동소수점 오차로 KR에서 탈락할 수 있다(실효 임계 = 1.1 + ε). 실플랜은 경계에 정확히 앉는 일이 드물어 무해하나, 임계 재캘리 시 epsilon 처리 여부를 함께 결정할 것.

→ **KR 09:00을 기다리지 않고도 게이트 로직은 양 시장 검증 완료.** 개장 후 남는 확인은 "judge가 1.1~1.5 밴드 플랜을 실제로 생산하는가"(생산량 문제)뿐이며, 게이트가 그것을 막지 않음은 확정됐다.
