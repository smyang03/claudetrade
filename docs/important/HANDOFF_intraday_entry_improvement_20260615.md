# 핸드오프 — 장중 진입 개선 (상승장 미진입 해소)

작성일: 2026-06-15 (KR 장 종료 후, 미국장 전)
이전 세션 ID: e1756bff-b1bd-424f-912e-72a52dfd6525

## 0. 한 줄 목표
상승장인데 진입을 못 하는 구조("눌림 대기 단일 채널 + BLOCKED/WAIT로 실행 차단")를 풀되,
**검증을 수동 대기가 아니라 shadow 능동 수집으로** 바꿔 답답함(미국장·한국장 항상 반복)을 단축한다.

---

## 1. 완료된 것

### 1-A. 이전 세션 커밋됨
| 커밋 | 내용 |
|---|---|
| `d3df6cc` | KIS 토큰 일원화 — KR/US 공유 자격증명인데 따로 발급해 1분 1회 제한(EGW00133) 충돌 → US를 KR 토큰으로 위임, 자정 갱신 US force 제외 |
| `4766f7f` | 모드-성과 측정 인프라 — 진입 시점 시장국면을 `v2_learning_performance.market_regime`에 기록 (capture overhaul net 컬럼과 동일 패턴) |

### 1-B. 2026-06-15 추가 세션 (미커밋 — 운영자 커밋 대기)
| 변경 | 내용 | 라이브 여부 |
|---|---|---|
| Phase B shadow 기록기 | `trading_bot.py::_record_intraday_entry_shadow` — 미진입(WAIT/REJECT/PULLBACK) 'would-enter' 스냅샷을 격리 funnel JSONL(`logs/funnel/intraday_entry_shadow_*`)에 기록. 주문/플랜 무영향 | **오늘밤 ON**(`INTRADAY_ENTRY_SHADOW_MODE=shadow` 기본) |
| Phase B 리뷰 도구 | `tools/intraday_entry_shadow_review.py` — yfinance 전방 재구성으로 MFE/MAE/net·눌림도달율, action·regime별 집계, 실제 대비 비교 | 사후 실행 |
| Phase A 조회 재시도 | `runtime/pathb_runtime.py::_entry_scan_broker_truth_gate` — transient 조회 실패 N회 재시도(fail-closed 불변). 보호영역 → MD 위반 사항 기록(CLAUDE.md) | **OFF**(`PATHB_ENTRY_SCAN_BROKER_TRUTH_RETRY_MAX=0` 기본). 검증 후 ON |

- 검증: 전체 회귀 **2492 passed**(2484+신규8), preflight ok=True FAIL 0, 데이터흐름(기록기→funnel파일→리뷰로더) 스모크 통과.
- 운영자 결정: "오늘은 Phase B shadow만 라이브, Phase A는 검증 후 다음 재시작 때 ON".

- **운영자 액션 (미국장 전 필수)**: **봇 재시작 1회** — `4766f7f`(모드 캡처) + Phase B shadow 기록기가 봇 코드라 재시작해야 반영. (현재 가동 봇 PID 22284는 13:31 기동 = `4766f7f` 미반영.) 재시작 후 오늘 미국장부터 `intraday_entry_shadow` JSONL + `market_regime` 동시 수집 시작.

---

## 2. 오늘 진단 결과 (근본 원인 — 데이터 근거)

### 오늘 KR 장 (상승장 MODERATE_BULL, KOSPI intraday +0.32%)
- 진입 **2건뿐**: 005930 1주(보유 −0.44%), 006800 9주(진입 1분 만에 loss_cap −1.14% 청산).
- Path A(trade_ready) 진입 **0건**, 전부 PathB.

### 왜 못 샀나 (정량)
- **장초반 WAIT 종목 25개 검증**: 장중 저점 −0.5%↓ 도달 **24/25** (눌림은 거의 다 왔음), 장중 고점 +4.68% 평균, 현재 −1.29%(17/25 하락).
  → "안 눌려서 못 산 게" 아니라 **눌림이 왔는데 실행을 못 잡았다.**
- **BLOCKED_BROKER_TRUTH 26회** (재시작 후에도 10회) — 토큰 외 **조회 freshness 실패**가 진입 차단.
- **WAIT_RECHECK 14회** — 눌림 확인 후에도 "더 기다려" 무한 반복.
- **장중판단(모드)은 size 70%까지 올렸으나 진입 게이트가 그 신호를 안 받음** = 상단(모드)과 하단(진입) 단절.

### 모드별 적중확률 (집계 결과)
- **모드가 성과 원장에 거의 저장 안 됨** (전 기간 `decisions.mode` 7건뿐) → 이번에 측정 인프라 추가(`4766f7f`)로 해결. **1~2주 데이터 쌓여야 산출 가능.**
- 참고 경향(표본 작음): US BULL 모드 +(MILD +0.96% 2/3승, MODERATE +2.98% 1/1), **KR BULL 모드 −(−2.65% 0/2)**, NEUTRAL −.
- 전체 live: US 23건 net −0.46%(35%승), KR 3건 −1.58%(0승).

---

## 3. 다음 세션 전체 개선 로드맵

### Phase A — 2-a: BLOCKED 조회 성공률 개선 (보호 유지, 즉시 live 가능)
- **목적**: fail-closed를 푸는 게 아니라, BLOCKED를 유발하는 **조회 실패 자체를 줄임** → 보호 유지하며 진입 기회 ↑.
- **구현 지점**: `runtime/pathb_runtime.py::_entry_scan_broker_truth_gate()` (863~), refresh TTL/min_interval (901~902: `PATHB_ENTRY_SCAN_BROKER_TRUTH_TTL_SEC`=30, `..._MIN_INTERVAL_SEC`). KIS 잔고 조회 재시도/백오프.
- **방법 후보**: ① 조회 실패 시 직전 성공 snapshot을 짧은 grace(예 60s) 동안 허용(단 이건 fail-closed 완화로 볼 수 있어 신중) ② 조회 자체 재시도 1회 추가 ③ TTL/min_interval 상향으로 강제 재조회 빈도↓.
- ⚠️ **보호영역**: `_entry_scan_broker_truth_gate`는 `AGENTS.md`/`CLAUDE.md` 보호계약. 완화 방향이면 **MD 위반 사항 기록 + 운영자 승인 필수.** "조회 성공률 개선"(②③)은 보호 약화 아님 → 비교적 안전.
- **검증**: `tests/test_pathb_runtime.py::test_entry_scan_broker_truth_gate_*` 회귀 유지. BLOCKED 빈도 before/after.

### Phase B — 2-b WAIT 탈출 + 추격 분기 (shadow 우선) ★ 답답함 핵심 처방
- **목적**: 눌림 온 종목 진입 살리기 + 강세주 추격 분기 신설. **검증을 shadow 능동 수집으로** 해 며칠 내 판정.
- **구현 지점**:
  - WAIT_RECHECK: `trading_bot.py:30652, 31044, 31220` / `maybe_run_early_judge_triggers` (31180~). 눌림 실측(저점 −0.5%↓) 조건에서 재확인 루프 끊고 진입 트리거.
  - 추격 분기: PathB buy zone 로직 + 국면 분기. `_market_sharp_reversal_block`(11099) 패턴 참고해 **shadow 토글** 신설(예 `INTRADAY_ENTRY_SHADOW_MODE`).
- **shadow 방식**: 실제 주문 X, "샀다면 어땠을지"만 기록 (shadow_audit_store 활용). 오늘 미국장부터 수집.
- **전환조건(shadow→enforce)**: shadow 진입이 live 대비 net + & 표본 ≥ N건(예 15) & 손실streak 한도 내. **US 우선, KR 보류**(KR BULL 손실 경향).
- **검증**: shadow 기록 vs 실제 비교 리포트. CLAUDE.md shadow 예외 원칙 명시(사유/지표/기간/전환조건).

### Phase C — 모드→진입 공격성 연결 (데이터 후, 1~2주)
- **전제**: Phase 0의 `market_regime` 데이터가 쌓여 **모드별 적중확률이 산출된 뒤.**
- **방법**: MODERATE_BULL+ & US면 buy zone 완화 / WAIT 임계 하향 / 추격 허용. **KR은 BULL 손실 확인돼 보류.**
- **검증**: 모드별 net 확률로 정당화. shadow 먼저.

### (선택) 소액 부분 live — micro probe
- 새 로직을 소액·1포지션·소수 종목 실제 live 검증. `MICRO_PROBE` 구조 활용(`CLAUDE.md` MICRO_PROBE promotion policy 준수).

---

## 4. 주의·보호영역 (반드시 확인)
- `_entry_scan_broker_truth_gate` fail-closed = 보호영역. 완화 시 **MD 위반 사항** 기록.
- `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true`, PathB AUTO_SELL_REVIEW cooldown, profit_ladder floor 등 **수익 구조 보존 영역 무관하게 유지.**
- KR/US 전략 성과 분리 — KR 추격/공격성 강화는 데이터 없이 금지(BULL도 손실).
- shadow는 CLAUDE.md "행동 불확실/데이터 부족 시 예외" 원칙에 맞게 사유·지표·기간·전환조건 명시.

## 5. 새 세션 시작 시 첫 작업 (2026-06-15 갱신)
1. 봇 재시작 됐는지 확인 → `intraday_entry_shadow` JSONL이 생기는지(`logs/funnel/`) + `v2_learning_performance.market_regime` 채워지는지 확인.
2. **미국장 1세션 수집 후**: `python tools/intraday_entry_shadow_review.py --market US` 로 'would-enter' net/MFE/눌림도달율 산출 → 실제 진입 대비 비교.
3. **Phase A ON 검토**: BLOCKED 빈도 재측정 후 `PATHB_ENTRY_SCAN_BROKER_TRUTH_RETRY_MAX=1`로 켜고 사이클 지연/BLOCKED 감소 모니터.
4. **전환 판정**: shadow 진입 net + & 표본 ≥ 15(US) 충족 시 WAIT 탈출/추격 분기 enforce 설계 착수. KR 보류.
5. Phase C(모드→진입 공격성)는 `market_regime` 1~2주 누적 후.

## 6. 검증/실행 명령
```bash
python -m pytest tests/ -q                                  # 전체 회귀
python tools/live_preflight.py --mode live --skip-dashboard --json
python tools/capture_net_review.py                          # capture/net 리포트
# 모드별 성과: v2_learning_performance.market_regime GROUP BY (데이터 쌓인 후)
```
