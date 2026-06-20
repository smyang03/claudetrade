# 시스템 전체 파이프라인 정합성 점검 (2026-06-20)

> 운영자 지시: "모든 기능 연결이 플로우대로 끊김없이 연결됐는지 — DB 생성, 매수/매도, 후보 등 모든 기능 점검. 끊기거나 오염된 것 없는지 상세 점검 → 문제 리포트 + 개선방안."
> 방법: read-only (DB/로그/프로세스 스냅샷). 라이브 매수/매도/broker truth/원장 무수정. 봇 미가동 상태라 정적 스냅샷 분석.

---

## 0. 요약 — 최초 6건 의심 → 정밀검증 후 실제 문제 1건

> ⚠️ 정직성 노트: 1차 스냅샷에서 "끊김/오염"으로 보인 6건을 hop별로 정밀 추적한 결과, **5건은 과거데이터·구조적 경로차·고아파일로 오진**이었고 라이브 배선은 정상이었다. 진짜 조치 필요한 건 P0-1(운영) 1건. 이 패턴(NULL=버그로 보였다가 추적하면 정상) 자체가 운영자 통점인 "침묵 배선" 진단의 어려움을 보여준다.

| # | 문제 | 최종 판정 | 조치 |
|---|---|---|---|
| **P0-1** | 봇 전체 미가동 (재부팅 04:32 후) | 🔴 **실재(해소됨)** | **봇 13:57 재시작 완료.** 재발방지(자동복구) 필요 |
| **P0-2** | selection attribution 99.8% NULL | 🟢 오진 | Path B는 selection_log 미기입(설계), 연결은 event_store에 생존 |
| **P1-1** | mfe/mae 97% NULL | 🟢 오진 | 배선 정상, fix 이전 과거데이터 + 재시작 후 청산 0건 |
| **P1-2** | live_status 4월 stale | 🟢 오진 | 실제 writer는 `live_live_status_*`(6/19 갱신), 본 파일은 고아 |
| **P2-1** | v2_learning pnl 65% NULL | 🟢 오진 | 488/489가 미청산(closed=0), closed인데 NULL은 1건뿐 |
| **P2-2** | KR yfinance split 오염 | 🟡 실재(기존인지) | 측정시 등락률만 사용(코드무관) |

### 진짜 후속 조치 (작은 것)
- **재발방지**: 봇/스케줄러 재부팅 자동복구(서비스화/작업스케줄러) + WU 강제재부팅 차단
- **청소(선택)**: 고아 파일 `state/live_status_US/KR.json`(4월자) — 대시보드가 안 읽는지 확인 후 제거 가능
- **확인거리**: closed=1인데 exit_price NULL/0 = 49건 (pnl은 있음, 소소)
- **라이브 검증 대기**: 재시작 후 자동청산 1건 발생 시 mfe 채움률 🔴→🟢 재측정

---

## 1. P0-1 — 봇 전체 미가동 (치명)

**증거**: 재부팅(2026-06-20 04:32:42, Windows Update 계획 재부팅) 이후 생존 python 프로세스 = `instiwatch dashboard`(타 프로젝트) 1개뿐.

**죽은 프로세스** (어제 가동 → 재부팅 후 미복구):
- `trading_bot.py --live` ← **거래 엔진 자체**
- `broker_truth_scheduler` (KR,US 30초 폴링)
- `preopen_scheduler`, `live_guardian`, `run_counterfactual_pipeline`
- codex_shadow 러너 (US/KR — 어제 마감이라 데이터는 보존)

**DB 정지 증거**: decisions.db 청산 최신 = `2026-06-18T19:52`, lifecycle 최신 = `2026-06-19T07:01` 이후 **신규 사이클 0건.**

**개선방안**:
1. **즉시**: 운영자가 봇 + 스케줄러 수동 재시작 (US 밤장 22:30 KST 전 필수). ※ 본 세션은 운영 머신 launch 금지 원칙으로 직접 안 띄움.
2. **근본**: 재부팅 자생존 — ① 작업 스케줄러에 "시스템 시작 시 `trading_bot.py --live` 자동 실행" 등록 (관리자), 또는 ② NSSM 등으로 봇/스케줄러를 Windows 서비스화 → 재부팅 자동복구.
3. **윈도우 업데이트 통제**: `NoAutoRebootWithLoggedOnUsers=1` + 활성시간 설정으로 장중 강제 재부팅 차단(보안패치는 수신).

---

## 2. P0-2 — selection attribution 99.8% NULL [정정: 구조적 경로차, 데이터는 event_store에 생존]

**증거**: `ticker_selection_log` 15,277건 중 **15,251건(99.8%)** attribution NULL. 날짜별로 매일 0/337·0/720·0/726... = 현재도 안 채워짐(과거데이터 아님).

**정밀 추적 결과 — selection_log 단독 단절이나, 연결 자체는 event_store에 살아있다**:
- attribution 기입 함수 `update_traded`(ticker_selection_db.py)는 정규 매수경로에서 정상 호출됨 — trading_bot.py:35716 (`execution_source_type="signal_entry"`, `execution_decision_id=_v2_decision_id`). 코드 정상.
- **단, 이 경로는 Path A(`signal_entry`)다.** 봇 실제 매수는 거의 전부 **Path B(`claude_price`)** (decisions 기준 US claude_price 164 vs 타전략 수십건). **Path B는 selection_log의 `update_traded`를 호출하지 않고** event_store(`v2_path_runs` 633건, `v2_decision_fill_links` 757건)에 기록한다.
- 즉 "선정→체결" 연결은 **끊긴 게 아니라 event_store에 있다.** selection_log 관점에서만 비어 보임.

**개선방안 (선택)**:
1. **분석 시 올바른 소스 사용** — selection→체결 귀속은 selection_log가 아니라 `v2_path_runs`/`fill_links`/`v2_learning_performance`로 조인. (가장 안전, 코드무관)
2. (원하면) Path B 진입 시에도 selection_log에 attribution back-link 추가 — 단 Path B는 selection_log 라이프사이클과 분리돼 있어 over-build 위험. event_store로 충분하면 불필요.
3. forward 컬럼(forward_3d 17% NULL)은 양호 → selection_log 자체는 forward 측정엔 정상 작동.

**중요**: 이는 "성과 역추적 불가"가 아니다. event_store 경유로 가능. selection_log의 execution_* 컬럼은 Path A 전용 필드로 이해해야 한다(스키마 의미 명확화 필요).

---

## 3. P1-1 — mfe/mae 배선 [정정: 버그 아님, 과거 데이터 잔존]

**최초 관찰**: `v2_learning_performance.mfe_pct` 732/757 = 97% NULL. CLOSED payload close_reason별로 자동청산 경로(loss_cap/ladder/target/stop) mfe 거의 0/N.

**정밀 추적 결과 — 배선은 정상이다 (내 최초 진단 P1-1 '중대 버그'는 과잉, 정정)**:
- 코드 경로: 자동청산 → `_finalize_pathb_sell_close`(7456/7925/8615/10080) → `_pathb_exit_meta`(11514, `position_mfe_pct`를 항상 생성) → `mark_closed`(9326, mfe 전달) → CLOSED payload `closed_extra`(sell_manager 334-335, `mfe_pct is not None`이면 실음). **끊긴 hop 없음.**
- mark_closed 호출처는 단 2곳(on_external_close 3703, finalize 9315) — 자동청산도 finalize를 타므로 mfe가 흐른다.
- **결정적 증거**: 어제 mfe fix는 봇 재시작(6/19 12:51 KST)에 반영됐고, **재시작 이후 CLOSED 청산이 0건.** 즉 NULL인 청산들은 전부 **fix 이전 과거 데이터**다. fix 직전 마지막 청산(KLAC 6/18, mfe=1.77 채워짐)이 새 배선 작동의 증거.

**결론**: 라이브 배선 정상. 97% NULL은 **과거 청산 잔존 + 재시작 후 신규 청산 0건**(메모리 `project_pipeline_integrity_rot` 기록과 일치). 별도 수정 불필요.

**남은 검증 1건 (코드수정 아님)**: 재시작 후 자동청산이 **1건이라도 발생하면** `integrity_check.py`로 close_reason별 mfe 채움률 재측정 → 🔴→🟢 가면 라이브 배선 검증 완료 = capture 측정 본격 시작 신호. (현재는 청산 데이터 자체가 없어 미검증 상태일 뿐.)

---

## 4. P1-2 — live_status stale (중)

**증거**: `state/live_status_US.json`·`live_status_KR.json` 모두 trading_date `2026-04-17~18`. 봇은 그 후로도 가동됐으나 이 파일만 4월 이후 미갱신.

**의미**: 대시보드/`/status`가 이 파일을 truth로 읽으면 4월 값을 표시 → 운영자 상태 오인 위험. (broker_truth_snapshot은 6/19까지 정상이라 실거래엔 무영향, 표시 계층 문제.)

**개선방안**: live_status writer가 세션마다 갱신하는지 점검. broker_truth_snapshot(정상)을 소스로 live_status를 파생하거나, 대시보드가 stale 파일 대신 broker_truth를 우선 읽도록.

---

## 5. P2 — 낮은 우선순위

- **P2-1**: v2_learning pnl 65%/exit_price 71%/closed_at 64% NULL → 다수가 미청산(open) 행이라 정상일 수 있으나, closed=1인데 NULL이면 문제. 청산분만 필터해 재검증 필요.
- **P2-2**: KR yfinance split 오염(삼성 362,500 등) — 측정 시 절대값 금지, 등락률만. 기존 인지된 항목.
- **path_runs CANCELLED 293/633(46%)**: 정상(plan 생성 후 미체결 만료 다수)이나 CANCELLED 사유 분포는 별도 확인 가치.

---

## 6. 정상 확인된 것 (끊김 없음)

- **event_store lifecycle 체인**: SELECTION→TRADE_READY→PLAN→ORDER→FILLED→CLOSED→FORWARD 이벤트 전 단계 존재(7,929건). 흐름 자체는 연결됨.
- **forward 측정기**: FORWARD_MEASURED 395건, 6/18 이후 226건 정상 동작(어제 자동훅 fix 유효).
- **fill_links**: 757건, decisions와 1:1 대응.
- **candidate_audit**: outcomes 366K/calls 3.1K/rows 114K, 활발히 적재.
- **mfe_backfill_yf**: capture_net_review가 이걸로 우회 생존(라이브 배선 끊겨도 측정은 유지).

---

## 7. 개선 실행 우선순위

| 순위 | 작업 | 유형 | 즉시성 |
|---|---|---|---|
| 1 | 봇+스케줄러 재시작 | 운영(운영자) | **US 밤장 전 필수** |
| 2 | 재부팅 자동복구(서비스화/작업스케줄러) + WU 재부팅 차단 | 시스템 설정 | 단기 |
| 3 | mfe/mae 자동청산 경로 배선 | 코드(보호영역, MD기록) | capture 측정 전 |
| 4 | selection attribution back-link 복구 | 코드 | selection 분석 전 |
| 5 | live_status 갱신/대시보드 소스 교체 | 코드 | 표시 정확성 |

### 공통 원칙 (모든 코드 수정 시)
- KR/US 전략 분리, claude_price/청산경로 보호영역 → MD 위반 기록
- 수정 후 producer→event_store→learning→dashboard hop별 재측정(integrity_check)
- preflight + 회귀 + 매수/매도/broker truth 오염 전수 검토
