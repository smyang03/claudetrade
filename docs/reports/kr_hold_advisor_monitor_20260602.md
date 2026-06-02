# KR hold advisor / execution tuning monitor - 2026-06-02

## 10:44 KST 중간 점검

- live bot PID `34008`, dashboard PID `60664` 정상 생존.
- KR broker truth fresh: 보유 0, 미체결 0, 당일체결 0, 주문가능 현금 `1,003,079 KRW`.
- KR live status: `CAUTIOUS`, size `35%`, 포지션 0, pending 0, daily PnL 0.0, halt 비활성.
- KR DB: `v2_decisions`는 `TRADE_READY_NO_SUBMIT` 3개, `v2_path_runs` 0개.
- KR 이벤트: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1.
- KR hold_advisor 호출: 0건. 포지션이 없어 HOLD/SELL 판단 대상이 아직 없었음.
- Execution Advisor: live config에는 활성 설정 없음. 시뮬레이션 기준 KR read-only, 주문 0, Claude 0, 이벤트 0.

## 현재 문제점

1. `run_tuning(KR)`이 시장 필터 없이 전체 포지션을 프롬프트에 넣고 있음.
   - KR 튜너 raw call에 US 포지션 `HPQ/NOK/MRVL`이 "보유 포지션"으로 들어감.
   - 모델 응답도 `HPQ +8.6% 익절선 상향 검토`처럼 US 포지션을 KR 튜닝 판단에 반영할 수 있는 문구를 냄.

2. `SL adjusted` 적용도 시장 필터 없이 전체 `self.risk.positions`를 순회함.
   - 10:17 KR 튜너가 `sl_adj=0.02`를 반환했고, 로그에 `SL adjusted: +0.020` 기록.
   - 오늘 KR 포지션은 0이고 US 포지션은 3개라, KR 튜닝 결과가 US 로컬 포지션 SL 값을 건드렸을 가능성이 큼.
   - 현재까지 이로 인한 KR 주문/체결은 없음. US 장 전 별도 리스크로 후속 수정 필요.

3. KR trade_ready는 있었지만 실제 제출은 정상 차단됨.
   - `000660`: `HIGH_PRICE_BUDGET_BLOCK`.
   - `005930`, `036930`: 반복 `NO_SIGNAL`.
   - 매수 미세조정/주문 경로가 위험하게 열린 흔적은 없음.

4. hold_advisor KR 개선은 아직 실전 검증 이벤트가 없음.
   - KR 보유가 0이라 hold_advisor 호출 0건.

## 11:00 KST 추가 점검

- KR broker truth fresh: 보유 0, 미체결 0, 당일체결 0.
- KR live status: `CAUTIOUS`, size `35%`, 포지션 0, pending 0, daily PnL 0.0.
- `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개 유지.
- `v2_path_runs`: 0개 유지.
- KR hold_advisor: 0건 유지.
- 10:48 튜닝은 `MAINTAIN`이라 `SL adjusted` 재발 없음.
- watchlist 부분교체: `[000660, 036930] -> [066575, 021080]`.
- 주문 제출 없음.

## 11:18 KST 추가 점검

- live bot PID `34008`, dashboard PID `60664` 정상 생존.
- KR broker truth snapshot은 다시 fresh: 보유 0, 미체결 0, 당일체결 0, 주문가능 현금 `1,003,079 KRW`.
- KR live status: `CAUTIOUS`, size `35%`, 포지션 0, pending 0, daily PnL 0.0.
- `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개 유지.
- `v2_path_runs`: 0개 유지.
- KR 이벤트: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1 유지.
- KR hold_advisor: 0건 유지.
- 11:09 / 11:14에 KIS KR 잔고 조회 500 오류로 broker truth degraded 발생.
  - 시스템은 `BLOCKED_BROKER_TRUTH`로 PathB entry scan을 차단.
  - 이 동작은 신규 진입 fail-closed로 정상 안전 동작.
  - 11:18 점검 시 snapshot은 fresh로 복구됨.
- 11:17 `final prompt evidence alignment` 경고 발생: overlap `0.66 < 0.80`.
  - 후보 evidence 정렬 품질 경고이며 주문/체결은 없음.
  - selection 품질 축으로 분리해서 관찰 필요.
- 10:17 이후 `SL adjusted` 재발은 확인되지 않음.

## 11:21 KST 추가 점검

- KR broker truth fresh: 보유 0, 미체결 0, 당일체결 0.
- KR live status: `CAUTIOUS`, size `35%`, 포지션 0, pending 0, daily PnL 0.0.
- KR DB/event count 변화 없음:
  - `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개.
  - `v2_path_runs`: 0개.
  - 이벤트: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1.
- KR hold_advisor: 0건 유지.
- 11:19 튜너에서 `SL adjusted: +0.020` 재발.
  - raw call: `20260602_KR_tune_150min_111913859587_a4d40bc792.json`
  - parsed: `action=TIGHTEN`, `sl_adj=0.02`, `momentum_wait_adjust_min=5`, `entry_priority_cutoff_adjust=0.02`, `kr_momentum_atr_cap_adjust=-0.01`, `kr_momentum_atr_cap_high_adjust=-0.01`.
  - warning에 `HPQ +8.6%`, `NOK`, `MRVL` 등 US 포지션이 다시 언급됨.
  - cross-market 포지션 컨텍스트/SL 오염 문제가 반복 확인됨.
  - KR 포지션은 0이므로 한국장 주문/체결에는 영향 없음.

## 11:23 KST 추가 점검

- 11:21 이후 새 KR 로그 없음.
- KR broker truth fresh, 보유 0, 미체결 0, 당일체결 0 유지.
- KR local position/pending 0 유지.
- DB/event count 변화 없음.
- KR hold_advisor 0건 유지.

## 11:45 KST 추가 점검

- live bot PID `34008`, dashboard PID `60664` 정상 생존.
- KR live status: `CAUTIOUS`, size `35%`, 포지션 0, pending 0, daily PnL 0.0.
- KR local position/pending 0 유지.
- `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개 유지.
- `v2_path_runs`: 0개 유지.
- KR 이벤트/reason count 변화 없음.
- KR hold_advisor: 0건 유지.
- 11:40에 KIS KR 잔고 조회 500 오류 재발.
  - `[broker sync partial] keep pre-sync cash because broker truth degraded` 로그 확인.
  - 11:45 최초 점검 snapshot은 stale로 보였으나, read-only Execution Advisor 시뮬레이션/재확인 후 11:45:59 snapshot은 fresh로 복구됨.
  - 복구 후 KR broker truth: 보유 0, 미체결 0, 당일체결 0, 주문가능 현금 `1,003,079 KRW`.
- 11:23 이후 `SL adjusted` 재발 없음.

## 12:04 KST 추가 점검

- KR live status: `CAUTIOUS`, size `35%`, 포지션 0, pending 0, daily PnL 0.0.
- KR local position/pending 0 유지.
- `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개 유지.
- `v2_path_runs`: 0개 유지.
- KR 이벤트/reason count 변화 없음.
- KR hold_advisor: 0건 유지.
- 11:55 / 12:00에 KIS KR 잔고 조회 500 오류 재발.
  - 12:01 점검 시 broker truth stale, Execution Advisor read-only 시뮬레이션도 `broker_truth_fresh=false`.
  - 12:03 entry scan 이후 snapshot fresh 복구.
  - 복구 후 KR broker truth: 보유 0, 미체결 0, 당일체결 0, 주문가능 현금 `1,003,079 KRW`.
- 11:49 지수 급락 트리거로 긴급 재판단: `CAUTIOUS -> CAUTIOUS`, size `35%` 유지.
- 11:45 이후 `SL adjusted` 재발 없음.

## 12:30 KST 추가 점검

- live bot PID `34008`, dashboard PID `60664` 정상 생존.
- KR broker truth fresh: 보유 0, 미체결 0, 당일체결 0.
- KR live status: `CAUTIOUS`, size `35%`, session active, 포지션 0, pending 0, halt 비활성, 주문가능 현금 `1,003,079 KRW`.
- KR local position/pending 0 유지.
- `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개 유지.
- `v2_path_runs`: 0개 유지.
- KR 이벤트: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1 유지.
- KR hold_advisor: 0건 유지.
- Execution Advisor read-only 시뮬레이션: broker truth fresh, decisions 0, events 0, orders 0, Claude calls 0.
- 12:18 `final prompt evidence alignment` 경고 재발: overlap `0.59 < 0.80`.
  - 후보 evidence 정렬 품질 경고이며 주문/체결은 없음.
- 12:19에 KIS KR 잔고 조회 500 오류 재발.
  - `[broker sync partial] keep pre-sync cash because broker truth degraded` 로그 확인.
  - 12:30 점검 시 snapshot은 fresh로 복구됨.
- 12:20 `universe filter bypass` 경고 발생.
  - 후보 필터링 품질/커버리지 축으로 관찰 필요.
- 12:20 튜닝 210분 로그 이후 `SL adjusted` 재발 없음.
- 활성 live 상태 파일은 `state/live_live_status_KR.json`.
  - `state/live_status_KR.json`은 2026-04-17에 멈춘 과거 파일이므로 현재 판단 기준에서 제외.

## 12:47 KST 추가 점검

- live bot PID `34008`, dashboard PID `60664` 정상 생존.
- KR broker truth fresh: 보유 0, 미체결 0, 당일체결 0.
- KR live status: `CAUTIOUS`, size `35%`, session active, 포지션 0, pending 0, halt 비활성, 주문가능 현금 `1,003,079 KRW`.
- KR local position/pending 0 유지.
- `data/v2_event_store.db` 기준:
  - `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개 유지.
  - `v2_path_runs`: 0개 유지.
  - lifecycle events: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1 유지.
- KR hold_advisor: 0건 유지.
- Execution Advisor read-only 시뮬레이션: broker truth fresh, decisions 0, events 0, orders 0, Claude calls 0.
- 12:30 이후 `ERROR`/`WARNING`/`SL adjusted`/주문 관련 신규 로그 없음.

## 13:03 KST 추가 점검

- KR live status: `CAUTIOUS`, size `35%`, session active, 포지션 0, pending 0, halt 비활성, 주문가능 현금 `1,003,079 KRW`.
- KR local position/pending 0 유지.
- `data/v2_event_store.db` 기준:
  - `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개 유지.
  - `v2_path_runs`: 0개 유지.
  - lifecycle events: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1 유지.
- KR hold_advisor: 0건 유지.
- 12:51 KIS KR 잔고 조회 500 오류 재발 후 broker truth가 stale 상태.
  - 12:51, 12:57, 13:02에 `BLOCKED_BROKER_TRUTH`로 PathB entry scan 차단.
  - Execution Advisor read-only 시뮬레이션도 `broker_truth_fresh=false`.
  - 신규 진입 fail-closed는 정상 안전 동작이나, 현재 KR 신규 주문 가능 상태는 아님.
- 12:50 `universe filter bypass`, 12:51 `final prompt evidence alignment` 경고 재발: overlap `0.50 < 0.80`.
- 12:47 이후 `SL adjusted` 재발 없음.
- 주문/체결/미체결 발생 없음.

## 13:09 KST 추가 점검

- KR live status: `CAUTIOUS`, size `35%`, session active, 포지션 0, pending 0, halt 비활성, 주문가능 현금 `1,003,079 KRW`.
- KR local position/pending 0 유지.
- `data/v2_event_store.db` 기준 count 변화 없음:
  - `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개.
  - `v2_path_runs`: 0개.
  - lifecycle events: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1.
- KR broker truth는 stale 유지.
  - 마지막 성공 기록은 13:07경 있으나 이후 KR 잔고 조회 500 오류가 다시 발생.
  - 13:08에도 `BLOCKED_BROKER_TRUTH`로 PathB entry scan 차단.
- Execution Advisor read-only 시뮬레이션도 `broker_truth_fresh=false`.
- 13:03 이후 `SL adjusted`/주문/체결/미체결 신규 로그 없음.

## 13:20 KST 추가 점검

- KR broker truth fresh 복구: 보유 0, 미체결 0, 당일체결 0.
- KR live status: `MILD_BEAR`, size `22%`, session active, 포지션 0, pending 0, halt 비활성, 주문가능 현금 `1,003,079 KRW`.
  - 13:17 기준 시장 모드가 `CAUTIOUS`에서 `MILD_BEAR`로 내려가 주문 사이즈가 축소됨.
- KR local position/pending 0 유지.
- `data/v2_event_store.db` 기준 count 변화 없음:
  - `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개.
  - `v2_path_runs`: 0개.
  - lifecycle events: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1.
- Execution Advisor read-only 시뮬레이션: broker truth fresh, decisions 0, events 0, orders 0, Claude calls 0.
- 13:12 KIS KR 잔고 조회 정상 로그로 broker truth 복구 확인.
- 13:16 `universe filter bypass`, 13:17 `intraday evidence coverage` 경고 발생: complete_ratio `0.54 < 0.60`.
- 13:17 `[slow KR] session_open 308s` 경고 발생.
  - 다음 주기 밀림 가능성 경고이며 주문/체결 영향은 아직 없음.
- 13:09 이후 `SL adjusted`/주문/체결/미체결 신규 로그 없음.

## 13:35 KST 추가 점검

- 기존 live bot PID `34008`, dashboard PID `60664`는 종료되어 있음.
- 새 프로세스 확인:
  - live bot PID `8228`, 시작 `13:12:24`, command `python trading_bot.py --live`.
  - dashboard PID `58520`, 시작 `13:12:25`, command `python dashboard\dashboard_server.py`.
  - live guardian PID `53772`, preopen scheduler PID `35832`, counterfactual pipeline PID `28504`도 13:12에 함께 시작.
- 13:12 재시작 후 startup health check는 OK=11 / FAIL=0.
- KR broker truth fresh: 보유 0, 미체결 0, 당일체결 0.
- KR live status: `MILD_BEAR`, size `22%`, session active, 포지션 0, pending 0, halt 비활성, 주문가능 현금 `1,003,079 KRW`.
- KR local position/pending 0 유지.
- `data/v2_event_store.db` 기준 count 변화 없음:
  - `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개.
  - `v2_path_runs`: 0개.
  - lifecycle events: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1.
- Execution Advisor read-only 시뮬레이션: broker truth fresh, decisions 0, events 0, orders 0, Claude calls 0.
- 13:21에 `BLOCKED_BROKER_TRUTH`가 한 번 더 기록됐으나 13:32 snapshot은 fresh.
- 13:20 이후 `SL adjusted`/주문/체결/미체결 신규 로그 없음.
- 재시작 원인 로그는 아직 특정되지 않음. KR 포지션 0이라 `pre_session_position_review` 스킵의 직접 영향은 없음.

## 13:52 KST 추가 점검

- live bot PID `8228`, dashboard PID `58520` 정상 생존.
- KR broker truth fresh: 보유 0, 미체결 0, 당일체결 0.
- KR live status: `MILD_BEAR`, size `18%`, session active, 포지션 0, pending 0, halt 비활성, 주문가능 현금 `1,003,079 KRW`.
  - 13:43 지수 급락 `-2.15%` 트리거로 긴급 재판단 수행.
  - 결과는 `MILD_BEAR -> MILD_BEAR`, size `18%`로 축소.
- KR local position/pending 0 유지.
- `data/v2_event_store.db` 기준 count 변화 없음:
  - `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개.
  - `v2_path_runs`: 0개.
  - lifecycle events: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1.
- Execution Advisor read-only 시뮬레이션: broker truth fresh, decisions 0, events 0, orders 0, Claude calls 0.
- 13:38에 `BLOCKED_BROKER_TRUTH`가 한 번 더 발생했으나 13:49 snapshot은 fresh.
- 13:43 `intraday evidence coverage` 경고 재발: complete_ratio `0.50 < 0.60`.
- 13:43 `universe filter bypass` 경고 재발.
- 13:35 이후 `SL adjusted`/주문/체결/미체결 신규 로그 없음.

## 14:08 KST 추가 점검

- KR broker truth fresh: 보유 0, 미체결 0, 당일체결 0.
- KR live status: `MILD_BEAR`, size `18%`, session active, 포지션 0, pending 0, halt 비활성, 주문가능 현금 `1,003,079 KRW`.
- KR local position/pending 0 유지.
- `data/v2_event_store.db` 기준 count 변화 없음:
  - `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개.
  - `v2_path_runs`: 0개.
  - lifecycle events: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1.
- Execution Advisor read-only 시뮬레이션: broker truth fresh, decisions 0, events 0, orders 0, Claude calls 0.
- 13:54에 KIS KR 잔고 조회 500 오류와 `BLOCKED_BROKER_TRUTH`가 한 번 발생.
  - 14:04 snapshot 기준 fresh로 복구.
- 13:52 이후 `SL adjusted`/주문/체결/미체결 신규 로그 없음.

## 14:24 KST 추가 점검

- KR broker truth fresh: 보유 0, 미체결 0, 당일체결 0.
- KR live status: `MILD_BEAR`, size `18%`, session active, 포지션 0, pending 0, halt 비활성, 주문가능 현금 `1,003,079 KRW`.
- KR local position/pending 0 유지.
- `data/v2_event_store.db` 기준 count 변화 없음:
  - `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개.
  - `v2_path_runs`: 0개.
  - lifecycle events: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1.
- Execution Advisor read-only 시뮬레이션: broker truth fresh, decisions 0, events 0, orders 0, Claude calls 0.
- 14:14 `universe filter bypass` 경고 재발.
- 14:14 KIS KR 잔고 조회 500 오류와 broker sync partial 발생.
  - 14:20 snapshot 기준 fresh로 복구.
  - 이번 구간에서는 `BLOCKED_BROKER_TRUTH` entry scan 차단 로그는 확인되지 않음.
- 14:08 이후 `SL adjusted`/주문/체결/미체결 신규 로그 없음.

## 14:40 KST 추가 점검

- KR live status: `MILD_BEAR`, size `18%`, session active, 포지션 0, pending 0, halt 비활성, 주문가능 현금 `1,003,079 KRW`.
- KR local position/pending 0 유지.
- `data/v2_event_store.db` 기준 count 변화 없음:
  - `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개.
  - `v2_path_runs`: 0개.
  - lifecycle events: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1.
- 14:25 KIS KR 잔고 조회 500 오류가 2회 연속 발생하고 broker sync partial 기록.
- 14:40 최초 확인 snapshot은 stale였으나, read-only Execution Advisor 시뮬레이션/재확인 후 14:40:27 snapshot은 fresh로 복구.
  - 복구 후 KR broker truth: 보유 0, 미체결 0, 당일체결 0.
  - Execution Advisor read-only 시뮬레이션: broker truth fresh, decisions 0, events 0, orders 0, Claude calls 0.
- 14:24 이후 `BLOCKED_BROKER_TRUTH` entry scan 차단 로그는 확인되지 않음.
- 14:24 이후 `SL adjusted`/주문/체결/미체결 신규 로그 없음.

## 14:56 KST 추가 점검

- KR broker truth fresh: 보유 0, 미체결 0, 당일체결 0.
- KR live status: `MILD_BEAR`, size `18%`, session active, 포지션 0, pending 0, halt 비활성, 주문가능 현금 `1,003,079 KRW`.
- KR local position/pending 0 유지.
- `data/v2_event_store.db` 기준 count 변화 없음:
  - `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개.
  - `v2_path_runs`: 0개.
  - lifecycle events: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1.
- Execution Advisor read-only 시뮬레이션: broker truth fresh, decisions 0, events 0, orders 0, Claude calls 0.
- 14:40 KIS KR 잔고 조회 500 오류와 broker sync partial 발생.
- 14:45 `BLOCKED_BROKER_TRUTH`로 PathB entry scan 차단.
  - 14:55 snapshot 기준 fresh로 복구.
- 14:43 `intraday evidence coverage` 경고 재발: complete_ratio `0.38 < 0.60`.
- 14:44 `universe filter bypass` 경고 재발.
- 14:40 이후 `SL adjusted`/주문/체결/미체결 신규 로그 없음.

## 15:11 KST 추가 점검

- KR broker truth fresh: 보유 0, 미체결 0, 당일체결 0.
- KR live status: `MILD_BEAR`, size `18%`, session active, 포지션 0, pending 0, halt 비활성, 주문가능 현금 `1,003,079 KRW`.
- KR local position/pending 0 유지.
- `data/v2_event_store.db` 기준 count 변화 없음:
  - `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개.
  - `v2_path_runs`: 0개.
  - lifecycle events: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1.
- Execution Advisor read-only 시뮬레이션: broker truth fresh, decisions 0, events 0, orders 0, Claude calls 0.
- 15:05 `[sub_screener] KR blackout before close` 확인.
  - 장마감 전 보조 스크리너 차단이 정상 작동.
- 14:56 이후 `SL adjusted`/주문/체결/미체결/`BLOCKED_BROKER_TRUTH` 신규 로그 없음.

## 15:26 KST 장마감 직전 점검

- live bot/dashboard가 15:23에 다시 재시작됨.
  - 새 live bot PID `50252`, dashboard PID `58100`.
  - live guardian PID `60976`, preopen scheduler PID `58896`, counterfactual pipeline PID `39508`도 함께 시작.
- 15:23 startup health check는 OK=11 / FAIL=0.
- KR broker truth fresh: 보유 0, 미체결 0, 당일체결 0.
- KR live status file은 `MILD_BEAR`, size `18%`, session active, 포지션 0, pending 0, halt 비활성, 주문가능 현금 `1,003,079 KRW`.
- KR local position/pending 0 유지.
- `data/v2_event_store.db` 기준 count 변화 없음:
  - `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개.
  - `v2_path_runs`: 0개.
  - lifecycle events: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1.
- 15:23 KIS KR 잔고 조회 500이 1회 발생했으나 15:23:44 잔고 조회 성공으로 회복.
- 15:23 mid-session restart 후 `session_open` 재실행.
  - `trade_ready=[]`.
  - KR 포지션 0이라 `pre_session_position_review` 스킵의 직접 영향 없음.
  - `startup guard bypass (trigger=startup_mid_session, close_in=311s)` 기록.
- 15:25 `ENTRY_BLACKOUT`으로 PathB entry scan 및 신규 매수 차단.
  - `[NEW_BUY_BLOCKED] KR * kr_sector_play ENTRY_BLACKOUT`.
  - 장마감 직전 신규 진입 차단은 정상 안전 동작.
- 15:24 / 15:25 수동 명령 `/claude` 재판단 로그 확인.
  - judgment log에는 `MILD_BEAR -> NEUTRAL` 기록이 있으나 15:26 상태 파일은 아직 `MILD_BEAR` 유지.
- 15:11 이후 `SL adjusted`/주문/체결/미체결 신규 로그 없음.

## 15:37 KST 장마감 후 종료 검증

- live bot PID `50252`, dashboard PID `58100`, live guardian PID `60976` 정상 생존.
- KR broker truth fresh: 보유 0, 미체결 0, 당일체결 0.
- KR local position/pending 0 유지.
- `data/v2_event_store.db` 기준 최종 count:
  - `v2_decisions`: `TRADE_READY_NO_SUBMIT` 3개.
  - `v2_path_runs`: 0개.
  - lifecycle events: `CLAUDE_TRADE_READY` 3, `TRADE_READY_NO_SUBMIT` 11, `SAFETY_BLOCKED` 1.
- Execution Advisor read-only 시뮬레이션: broker truth fresh, decisions 0, events 0, orders 0, Claude calls 0.
- 15:26 수동 `/claude` 재판단 완료: `MILD_BEAR -> NEUTRAL`, size `25%`.
- 15:26 `intraday evidence coverage` 경고 재발: complete_ratio `0.35 < 0.60`.
- 15:30 / 15:35에 `MARKET_CLOSED`로 PathB entry scan 및 신규 매수 차단.
  - `[NEW_BUY_BLOCKED] KR * kr_sector_play MARKET_CLOSED`.
  - 장마감 후 신규 진입 차단은 정상 안전 동작.
- 15:37 상태 파일은 `mode=NEUTRAL`, size `25%`, 포지션 0, pending 0이나 `session_active=true`로 남아 있음.
  - 실제 주문 게이트는 `MARKET_CLOSED`로 작동하므로 주문 리스크는 낮음.
  - 다만 dashboard/status 표시 정확성 측면에서는 장 종료 후 `session_active` 반영 지연 또는 불일치 가능성 있음.
- 장중 전체 기준 KR hold_advisor 호출은 0건.
  - KR 보유 포지션이 없어서 HOLD/SELL 판단 실전 이벤트는 발생하지 않음.
- 장중 전체 기준 KR 주문/체결/미체결/PathB path run 없음.
- `SL adjusted`는 11:19 이후 재발 없음.

## 운영 판단

- 장중 코드 변경은 보류했고, KR 주문/체결 리스크는 낮게 유지됨.
- 장 종료 후 `run_tuning`의 포지션 컨텍스트, `sl_adj`, `REVERSE` 대상을 시장별로 필터링하는 패치 필요.
- KIS 500 일시 오류 시 신규 진입 fail-closed는 정상 동작으로 확인됨.
- Execution Advisor는 live에서 비활성/shadow-only 상태라 실제 매수/매도 미세조정 기능으로는 동작하지 않음.
- KR 포지션이 0이어서 hold_advisor 개선은 한국장 실전 HOLD/SELL 이벤트로 검증되지 않음.
- 장중 프로세스 재시작이 13:12, 15:23 두 차례 발생했으며 원인 특정 필요.
- 장 종료 후 status 파일의 `session_active=true` 표시는 market gate와 불일치하므로 dashboard/status 표시축 점검 필요.
