# 시스템 전면 스캔 — 수익성 정교화 리뷰 (2026-07-04)

4개 도메인 병렬 스캔(수익성 실측·토글 정합성·출구 leak·silent breakage) + 실측 기준선. advisor 도구 불가로 다중 서브에이전트로 수행. read-only, 코드/config 무변경.

## 0. 한 줄 결론
**새 수익 엔진은 없다(재확인).** 지금 시스템의 최대 문제는 "안 버는 것"이 아니라 **"버는지 안 버는지 측정이 불가능한 것"**이다. 7월 청산 n=1, net_est 필드가 비용 과소계상, 죽은 측정 3건(would_carry·trend_overlay·ladder_ab). **정교화 = 알파 찾기가 아니라 측정 복구 + 정합성 + red-tape forward + 비용.**

## 1. 수익성 진실 (실측, dedup, 일관비용 = 정직한 바닥)

| 시장 | n | gross | **일관비용 net** | 승률 | 판정 |
|---|---|---|---|---|---|
| US 통산 | 219~230 | +0.19 | **−0.515** | 30% | 적자 확정 |
| US 5월 | 91 | +0.89 | +0.17 | 42% | 본전(국면 우호) |
| US 6월 | 127 | −0.29 | **−1.01** | **20%** | 붕괴 |
| US 7월 | 1 | — | — | — | 측정불가 |
| KR 통산 | 26~35 | +0.44 | **+0.17** | 43% | 약흑/판정불능(중앙 −0.94) |
| KR 6월 | 18 | +1.11 | +1.07 | 50% | 흑자(유일 밝은 지점) |

- US 적자 확정, 6월 국면 붕괴(승률 20%)가 주범. 5월은 본전. **국면 의존**(US 5월 좋고 KR 6월 좋음 = 어긋난 우호월 = 분산).
- **⚠️ net_est 필드 결함**: `pnl_pct_net_est`는 US 왕복비용을 0.23%만 반영(진짜 0.70%). 이 필드 그대로 쓰면 US 적자를 0.19~0.45%p 과소평가. **net 판정은 반드시 일관비용으로.**

## 2. US 적자 원인 재정의 — capture leak 아니라 손실꼬리

**예전 "PROFIT_LADDER가 #1 leak"은 더 이상 사실 아님.** 현재 US 출구사유별 net_SUM:
- **LOSS_CAP −96%p**(n=35, net −2.75, 승률 0%) — #1, 하지만 운영자 스톱(−2% 최적, 손대지마).
- HARD_STOP −16.5 · **CLAUDE_INTRADAY_SELL −14.2(신규 플래그, Claude 재량 장중매도 net −1.18)**.
- PROFIT_LADDER는 net −0.28(rank #7, 사실상 본전) — **capture leak 해소됨.**
- 승자: TARGET +55.3 · CLAUDE_SELL +7.4 = 좌측꼬리 못 덮음.

→ **US 적자 = 손실꼬리(나쁜 진입이 LOSS_CAP/HARD_STOP 도달), 출구 give-back 아님.** red-tape가 그 나쁜 진입을 입구에서 차단 = **지금 #1 문제를 정확히 겨냥**(정합).

## 3. 메타 발견 — 시스템이 지금 측정 불가

며칠 토글 폭주의 효과를 볼 수 없는 이유가 겹쳐 있다:
1. **7월 청산 n=1** — reward_risk 컷(매수 100%→12%)으로 진입 급감 → 청산 표본 없음. 토글 효과 판정 불가.
2. **net_est 비용 과소계상**(§1) — US 적자 폭 낙관 왜곡.
3. **죽은 측정 3건**(§4). 
4. **US 단일 진입경로(PULLBACK_WAIT)에 게이트 6~7개 직렬** → 개별 레버 net 귀속 원리적 불가. deconfound 문서도 stale(A1해제·red-tape 미포함).

## 4. 실행 항목 (랭크: 측정 복구 우선)

### A. 측정 복구 (버는지 보이게 — 최고 레버)
- **A1 🔴 would_carry D3 측정 죽음**: 복구 커밋(70cd050)이 `would_carry_meta`를 `close_all_open()`(kill/수동만 호출) 안에 넣어 정상세션 미호출 → **여전히 0건**. floor_shadow와 동일 클래스. 정상 pre-close 경로로 옮겨야 keep/kill 게이트(n≥25) 누적.
- **A2 🔴 net_est 비용 과소계상**: `pnl_pct_net_est` 계산이 US 왕복 0.23%만 반영. 진짜 0.70%로 교정하거나, 모든 net 판정 도구를 일관비용 기준으로 통일.
- **A3 🟠 ladder_ab_review 실명**: `data/ml/decisions.db`(v2_learning_performance)가 6/26~7/1 sync gap → 도구 N=1. **학습 DB 언더싱크**가 ladder A/B 판독을 막음. sync 복구 필요.
- **A4 🟠 deconfound 문서 stale**: §1 인벤토리에 A1_US 해제 + red-tape enforce 미포함 → "새 US net 실측" 설계가 실제 활성 게이트와 어긋남. 갱신.

### B. 정합성/위생
- **B1 🟠 trend_overlay 게이트 stale-dead**: 신호 6/25 생성, freshness 7일 → 8일+ 경과로 항상 `stale_signal` fail-open. shadow는 쓰레기 기록, enforce로 켜면 대부분 기간 silent no-op(부비트랩). refresh 스케줄 없음. freshness를 신호 주기(월)로 넓히거나 refresh 배선.
- **B2 🟠 reward_risk = 단일 게이트를 2토글이 이중조임**(CONSISTENT + DETERMINISTIC_CAP), net 미검증, revert 시 분리 불가. 하나씩 A/B 하려면 분리 로깅 필요.
- **B3 🟡 dedup 잔존**: 과거 이중 CLOSED 7건(store에 상주, CRCL은 부호반전). 신규는 ffe2612가 차단. `tools/net_profitability_review.py`는 아직 미dedup(6월 US 3건 이중카운트) → dedup 추가.
- **B4 🟡 config↔.env.live 불일치 2건**: `CLAUDE_SELECTION_COMPRESSED_MAX_TOKENS`(2600 vs 2200), `US_TRADE_READY_SLOT_OPENING_RANGE_PULLBACK`(5 vs 3). 실효=config지만 2-소스 계약 위반. 일치시켜라.
- **B5 🟡 죽은 값 정리**: 글로벌 `REQUIRE_TRADE_READY=true`(양시장 override로 무효), 제출단 A1 분기 dead, `PATHB_READY_BOOST_MULT=1.0`, `MAX_SECTOR_POSITIONS=5`(미배선). "A1이 US 차단" 전제 도구/메모리 정정.
- **B6 🟡 테스트가 라이브 funnel 로그 오염**: `FUNNEL_DIR` 모듈레벨·테스트 격리 없음 → pytest가 `logs/funnel/trend_overlay_20260427.jsonl`에 합성행 기록. 도구가 glob하면 유령행 흡수.

### C. Forward 관찰 (red-tape 이미 라이브)
- **C1 🔴 red-tape 라이브 발동 검증**: 봇 01:44 재시작으로 **enforce 라이브**(config MODE_US=enforce). BUT `_entry_tape_idx`가 `_session_open_index_change` 미채움 시 **None→shadow도 enforce도 silent no-op**. **다음 US 세션서 red_tape_shadow 기록 + RED_TAPE_ENTRY_GATE 발동 실제 확인 필수.**
- **C2 red-tape 순서 아티팩트**: sharp_reversal이 먼저 실행→return이라 급락일 차단은 sharp_reversal로 기록, red_tape 고유 코호트 = 옅은 빨강(−0.3~−1.5%)뿐. forward 판독 도구가 SPY 소급으로 고유 코호트 분리(라이브 blocked 카운트로 판단 금지).
- **C3 CLAUDE_INTRADAY_SELL 관찰**: 신규 #3 drain(net −1.18, n=12). 표본 얇음, 누적 관찰.

### D. 손대지 마라 (재확인)
selection 무알파 · mode 사이징 · 목표낮추기 · give-back blanket(ladder B n=5도 A보다 −0.5%p 열위) · loss_cap 임계(−2% 최적, #1 drain이나 운영자 스톱) · 매수확대 · RR 완화.

## 5. 정교화 방향 (결론)
1. **측정을 고쳐라(A1~A4)** — 못 재는 수익은 못 정교화한다. 지금 최고 레버.
2. **red-tape가 실제 발동하는지 확인(C1)** — 안 그러면 이번 세션 작업이 silent no-op.
3. **비용/FX**(운영자) — 유일 분별무관 net 레버, US 적자 폭을 직접 줄임.
4. red-tape forward 누적 → 6월 편중 확증 → 밴드 넓히기.

새 알파는 없다. 이번 스캔의 성과 = **시스템을 측정가능·정합 상태로 만드는 구체 항목 리스트**.
