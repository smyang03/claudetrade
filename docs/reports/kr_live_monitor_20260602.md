# KR Live Monitor - 2026-06-02

작성 상태: 장마감 최종 점검 완료
최신 갱신: 2026-06-02 15:37 KST
목적: 한국장 수익률 개선 관점에서 KR live 운영 상태, 최근 KR 관련 커밋의 정상 동작 여부, 이상 징후와 후속 조치 후보를 장마감까지 추적한다.

## 현재 결론

- KR live 프로세스는 장마감 이후 15:35:14 post-close 사이클까지 정상 완료했다. 15:30/15:35의 신규 진입 차단은 `MARKET_CLOSED`로 기록된 정상 장마감 차단이다.
- KR 보유 종목 0개, KR 미체결 주문 0개, KR PathB run 0개다. 현재까지 한국장 신규 실거래는 발생하지 않았다.
- 최근 KR 커밋으로 추가된 관측 로그는 정상적으로 남고 있다. selection raw, funnel snapshot, gate evaluation, action routing shadow, evidence coverage, watch trigger shadow가 계속 기록된다.
- 현재 병목은 주문 실행 실패가 아니라 진입 후보가 실행 후보로 승격되지 않는 구조다. 15:26 selection/funnel 기준 `trade_ready=[]`, `pathb_wait_tickers=[]`, 후보는 모두 WATCH다.
- KR broker truth 장애는 아직 회복으로 보기 어렵다. 13:15 broker equity 조회 성공 이후에도 13:21, 13:38, 13:54, 14:45에 `BLOCKED_BROKER_TRUTH`가 재발했고 PathB entry scan이 다시 fail-closed로 차단됐다. 13:54에는 KR 잔고 조회 500과 broker sync partial도 함께 발생했다. 14:14에도 KR 잔고 조회 500과 broker sync partial이 재발했고, 14:25에는 KR 잔고 조회 500이 1회/2회 연속으로 다시 발생했다. 14:40에도 KR 잔고 조회 500과 broker sync partial이 재발했다. 14:45 이후 추가 `BLOCKED_BROKER_TRUTH`는 없고, 15:30/15:35 risk event는 장마감에 따른 `MARKET_CLOSED`다.
- 15:26 evidence coverage가 34.62%까지 추가 하락했고 `fail_closed_applied=true`가 유지됐다. 데이터 수집 품질 저하가 현재 가장 큰 신규 진입 차단 요인이다.
- 14:14 60분 tuning은 `MILD_BEAR`를 유지했고 14:44 90분/15:15 120분 tuning gate도 wait/cutoff/ATR cap을 유지했다. 15:26 긴급 재판단은 `MILD_BEAR → NEUTRAL`, size 25%로 끝났지만 이미 ENTRY_BLACKOUT 구간이라 신규 진입으로 이어지지 않았다.
- 15:05/15:21/15:25에는 장마감 전 blackout으로 신규 진입 경로가 차단됐다. 15:25 PathB entry scan은 `ENTRY_BLACKOUT`으로 막혔고, 15:30/15:35 PathB entry scan은 `MARKET_CLOSED`로 막혔다.
- 13:17 session_open은 308초가 걸려 `[slow KR]` 경고가 발생했다. 이후 cycle latency는 대체로 정상 범위였고, 최신 15:35 latency는 3.0초 `alert=false`다.
- live 운영 P1 리스크는 별도 유지된다. `state/brain.json` dirty 상태와 hold-advisor non-stop SELL triage/challenge 우회 가능성은 KR 신규 진입 병목과는 별개지만 live 승인/재시작 전 처리 대상이다.

## 상태 요약

| 항목 | 현재값 |
|---|---:|
| 확인 시각 | 2026-06-02 15:37 KST |
| 최신 KR cycle | 15:35:14 완료, post-close `MARKET_CLOSED` |
| KR open positions | 0 |
| KR pending orders | 0 |
| KR v2_decisions | 3 |
| KR v2_path_runs | 0 |
| KR lifecycle_events | 15 |
| KR raw selection calls | 20 |
| 최신 KR selection raw | `20260602_KR_select_tickers_152658235378_8c899d366b.json` |
| 최신 KR selection parse | `parse_error=false`, `parse_stage=strict_compact` |
| 최신 selection 결과 | `WATCH` 15, `trade_ready=[]` |
| 최신 funnel 결과 | `WATCH` 15, `trade_ready_count=0`, `pathb_wait_tickers=[]` |
| 최신 evidence coverage | 34.62%, 9/26 complete, missing 17건 |
| evidence fail-closed | `true`, reason=`coverage_below_threshold` |
| 최신 KR cycle latency | 3,019.458ms, `alert=false` |
| 최신 session_open 경고 | 308초 소요, slow KR |
| 최신 KR tuning/session mode | 15:35 `NEUTRAL`, size 25%, `MARKET_CLOSED` |
| KR risk events after open | `HIGH_PRICE_BUDGET_BLOCK` 1건, `BLOCKED_BROKER_TRUTH` 10건, `MARKET_CLOSED` 2건 |
| 마지막 `BLOCKED_BROKER_TRUTH` | 14:45:32 |
| 마지막 `MARKET_CLOSED` | 15:35:14 |

## 현재까지 문제점

1. 실행 후보가 없다.
   - 최신 raw selection은 정상 파싱됐지만 결과가 `WATCH` 15개, `trade_ready=[]`다.
   - 15:26 최신 funnel도 `WATCH` 15개, `trade_ready_count=0`, `pathb_wait_tickers=[]`다.
   - 따라서 주문 제출 단계로 넘어가지 않는다.

2. evidence coverage가 장중 계속 악화됐다.
   - 11:17: 80.77%
   - 12:18: 73.08%
   - 12:51: 61.54%
   - 13:17: 53.85%
   - 13:43: 50.00%
   - 14:43: 38.46%
   - 15:15: 38.46%
   - 15:26: 34.62%
   - 15:26에도 threshold 0.60 아래 상태가 계속 유지돼 `fail_closed_applied=true`다. complete 9/26, missing 17건이며, 이때 KIS intraday chart 500이 2건 포함됐다.

3. broker truth 장애가 신규 진입을 여러 차례 차단했다.
   - `BLOCKED_BROKER_TRUTH`는 11:09, 11:14, 12:51, 12:57, 13:02, 13:08, 13:21, 13:38, 13:54, 14:45에 발생했다.
   - KR 잔고 조회 500 실패는 11:09, 11:40, 11:55, 12:00, 12:19, 12:51에 반복됐다.
   - KR 잔고 조회 500 실패는 13:54, 14:14, 14:25, 14:40에도 재발했다.
   - 14:25에는 KR 잔고 조회 500이 1회/2회 연속으로 발생했고 broker sync partial도 반복됐다. 14:40에도 KR 잔고 조회 500과 broker sync partial이 다시 발생했다.
   - 14:45에는 `BLOCKED_BROKER_TRUTH` risk event가 10번째로 추가되고 PathB entry scan이 차단됐다. 14:45 이후 추가 `BLOCKED_BROKER_TRUTH`는 없었다.
   - 15:30/15:35의 `MARKET_CLOSED` risk event는 broker truth 장애가 아니라 장마감 후 신규 진입 차단이다.
   - 13:15/13:43 broker equity 조회 성공만으로는 회복을 입증하지 못했다. 13:21, 13:38, 13:54, 14:45 재발과 14:14/14:25/14:40 잔고 조회 500 때문에 broker truth 안정성은 여전히 주요 차단 요인이다.

4. 시장/튜닝 모드가 보수적이다.
   - 12:50 tuning은 `TIGHTEN`, `CAUTIOUS`, wait+5m, cutoff+0.02, ATR cap -0.01/-0.01을 유지했다.
   - 13:17 session mode는 `MILD_BEAR`, size 22%다.
   - 13:44 긴급 재판단은 지수 급락 -2.15% 트리거로 `MILD_BEAR`를 유지하고 size를 18%로 낮췄다.
   - 14:14 60분 tuning도 `MILD_BEAR` 유지, action `MAINTAIN`이었다. 지수는 -1.64%로 일부 반등했지만 breadth 악화와 vol_spike 때문에 모드 변경 근거가 부족하다는 판단이었다.
   - 14:44 90분 tuning gate도 wait=45분, cutoff=0.20, KR ATR cap 0.06/0.10을 유지했다.
   - 15:15 120분 tuning gate도 wait=45분, cutoff=0.20, KR ATR cap 0.06/0.10을 유지했다.
   - 15:23에는 장중 재시작으로 `session_open`이 다시 실행됐고 기존 판단을 재사용했다.
   - 15:26 긴급 재판단은 수동 명령 `/claude` 트리거로 `MILD_BEAR → NEUTRAL`, size 25%로 끝났다.
   - 15:05, 15:21, 15:25에는 장마감 전 blackout으로 신규 진입 경로가 차단됐다.
   - 15:30/15:35에는 장마감 후 `MARKET_CLOSED`로 신규 진입 경로가 차단됐다.
   - 지수 약세, breadth 악화, 고환율 환경 때문에 신규 진입 문턱을 낮추지 않고 있다.

5. 후보는 생기지만 watch-only/shadow에서 멈춘다.
   - watch trigger shadow는 `promotion_enabled=false`, `shadow_only=true`로 기록된다.
   - 15:35 watch trigger shadow도 `NEUTRAL` 모드에서 `blocked`, `blocked_reason=entry_blackout`으로 끝났고, not-evaluated 일부는 `shadow_cycle_cap_exceeded`였다.
   - 14:44 raw selection도 전부 WATCH였고, candidate health는 `WATCH_STRENGTHENING` 6개, `WATCH_WEAK` 5개, `STABLE_READY` 2개, `OBSERVE` 2개로 기록됐다.
   - `NO_SIGNAL`, `data_incomplete`, `late_chase`, `action_ceiling_watch`, `ATR_BLOCKED`, `FADE_DEEP`, `OR_MISSING`, `LOW_VOL`, `QUALITY_WEAK` 계열 사유가 반복된다.

6. 일부 예산 차단은 정상 정책으로 보인다.
   - 09:16 `000660`은 고가 종목이라 45만원 fixed order로 1주 매수가 불가능해 `HIGH_PRICE_BUDGET_BLOCK`이 발생했다.
   - 이는 주문 실패라기보다 KR 고가 종목 후보를 예산 정책에서 어떻게 다룰지의 설계 문제다.

7. latency/주기 지연이 있다.
   - cycle latency alert는 누적 7회 발생했다.
   - 13:17 cycle latency는 정상(4.3초)이지만, session_open이 308초 소요돼 다음 주기 밀림 가능성이 경고됐다. 15:35 post-close cycle latency는 3.0초로 정상 범위다.

8. live 승인 전 별도 P1 리스크가 남아 있다.
   - `state/brain.json`이 dirty이며 live preflight의 brain memory guard 경고와 일치한다.
   - `HOLD_ADVISOR_TRIAGE_NON_STOP_SELL_ENABLED=true`와 `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true` 조합은 protected PathB/hold-advisor 포지션에서 challenge 우회 SELL 가능성이 있는 별도 운영 리스크다.

## 최근 커밋 동작 확인

- compact selection schema는 정상 파싱 중이다.
- candidate funnel, gate evaluation, action routing shadow, evidence coverage 로그는 정상 기록 중이다.
- `ready_degraded`와 evidence/action ceiling 기반의 보수적 demotion은 작동 중이다.
- broker truth fail-closed는 의도대로 신규 PathB entry scan을 막았다.
- 13:17에는 evidence coverage fail-closed가 후보를 WATCH에 묶었고, 13:21/13:38/13:54/14:45에는 broker truth fail-closed가 다시 PathB entry scan을 막았다.
- 13:43/14:44/15:15/15:26 정시 또는 재시작 재스크리닝과 selection raw도 정상 기록됐지만 결과는 전부 WATCH였고, 15:15 부분교체도 유효 신규 종목 없음으로 스킵됐다. 14:14/14:44/15:15 tuning gate는 `MILD_BEAR`/gate 유지를 선택했고, 15:26 긴급 재판단은 `NEUTRAL` size 25%를 냈지만 ENTRY_BLACKOUT 이후라 신규 진입으로 이어지지 않았다.
- 15:30/15:35 post-close cycle에서 PathB entry scan, `NEW_BUY_BLOCKED`, risk event가 모두 `MARKET_CLOSED`로 기록됐다. 장마감 차단 가시성은 정상이다.
- 관측 기능은 정상이나, 관측 결과는 KR 신규 진입이 너무 보수적으로 닫히고 있음을 가리킨다.

## 후속 조치 후보

- 장마감 후 `NO_SIGNAL` 사유를 종목별로 분해해 PlanA signal feature wiring 문제와 실제 신호 미충족을 분리한다.
- `selection_intraday_evidence_coverage`의 provider timeout/prefetch timeout 원인을 분리한다. 13:17처럼 coverage가 0.60 아래로 내려가면 실행 후보가 자동 차단된다.
- `kr_plan_a_no_signal_pathb_shadow`와 watch trigger shadow의 MFE/MAE를 계산해 watch-only 후보를 실제 PathB wait로 승격했을 때 기대값이 있는지 검증한다.
- KR 고가 종목은 fixed 45만원 예산에서 반복 차단되므로 후보 단계 제외, 별도 micro-probe, 또는 예산 정책 변경 중 하나로 분리 검토한다.
- 장마감 5분 전 재시작/수동 `/claude`는 실행 가능성이 낮은데 Claude 재판단과 종목 재선정을 유발했다. `close_in`이 blackout/market closed 권역이면 재판단은 관측 전용으로 제한할지 검토한다.
- postmortem에서는 `MARKET_CLOSED` 같은 정상 차단과 `BLOCKED_BROKER_TRUTH` 같은 장애성 차단을 분리 집계한다.
- `state/brain.json` dirty 상태는 승인 workflow 또는 revert 대상으로 분리한다.
- hold-advisor live config P1은 KR 수익률 개선 작업과 분리해 운영 승인 전에 처리한다.

## 다음 체크포인트

- 장마감 모니터링은 15:35 post-close cycle 확인으로 종료한다.
- 이후 작업은 장중 신규 진입 부재 원인 postmortem, evidence provider timeout/KIS 500 분해, `NO_SIGNAL` 종목별 분해, live P1 리스크 처리다.

## 근거 위치

- Funnel logs: `logs/funnel/*_20260602_KR.jsonl`
- Raw calls: `logs/raw_calls/20260602_KR_select_tickers_*.json`
- Live log: `logs/system/live_trading_20260602.log`
- Risk log: `logs/risk/live_risk_20260602.log`
- Event DB: `data/v2_event_store.db`
- State files: `state/live_open_positions.json`, `state/live_pending_orders.json`
