# 운영 변경 일지 (CHANGELOG)

운영자가 "언제 무엇을 왜 바꿨고 지금 어떤 상태인지"를 한 파일로 따라가기 위한 일지.
git(코드 diff) + 이 파일(결정·토글 이력) + `/status`(현재 스냅샷) = 통제권 3종 세트.

기록 규칙:
- 코드/config/env/토글을 바꾸면 **무엇 / 왜 / 현재상태 / 롤백조건 / 커밋해시**를 한 줄로 추가.
- **롤백·번복도 기록한다** (조용히 엎지 않기). 기각한 것도 "(기각) 이유"로 남겨 반복 논의 방지.
- 최신 날짜가 위로. 상세 근거는 CLAUDE.md `MD 위반 사항`/메모리에, 여기는 한눈에 보는 요약.

---

## 2026-06-23

### PathB 매도 sell_in_flight 데드락 buster(broker 교차확인) + 미체결 매도 추격 shadow

- **무엇:** ① P0(enforce) — `runtime/pathb_runtime.py::_clear_stale_pathb_closing_lock`에 broker `open_orders` 교차확인(`_broker_confirms_no_pending_sell`) 추가. broker가 "미체결 매도 없음" 확정 + 보유 + grace 60초 경과 시, 로컬 `pending_orders` 잔존과 무관하게 sell_in_flight 락 해제. ② P1(shadow) — exit scan에서 미체결 매도 감지 시 `logs/funnel/unfilled_sell_shadow_*.jsonl`에 gap/지정가pnl/추격pnl 기록(실주문 무영향). 테스트 `tests/test_pathb_closing_lock_broker_truth.py`(11: 데드락 해제 분기 8 + shadow 3).
- **왜:** `_find_pending_order`가 broker 아닌 봇 로컬 메모리만 봐서, 운영자가 broker에서 매도주문을 수동취소하면 TTL 900초가 차도 락이 안 풀리던 데드락(6/22 AVGO 13분 좀비 — 손절·익절 관리 전부 스킵). broker truth 우선 원칙 위반 교정. P1은 "지정가가 판단가에 박혀 가격 하락 시 미체결 방치"되는 누수(AVGO 익절 +4.96%→실현 +1.07%)의 추격(현재가 재제출) net 효과를 enforce 전 측정.
- **현재상태:** 코드 반영. 신규 env 3종 안전 기본값(`PATHB_CLOSING_LOCK_BROKER_CONFIRM_GRACE_SEC=60`, `PATHB_UNFILLED_SELL_SHADOW_ENABLED=true`, `PATHB_UNFILLED_SELL_SHADOW_INTERVAL_SEC=120`) — `.env.live`/config **무변경**(코드 기본값 작동). 주문 수량/가격/청산 트리거/Claude 호출량 **무변경**. broker truth 우선 **강화**(약화 없음, MD 위반 아님). **봇 재시작 시 반영.**
- **롤백조건:** P0는 코드 revert(단 broker 미확인 시 기존 TTL+로컬 fail-closed 경로라 약화 없음). P1은 `PATHB_UNFILLED_SELL_SHADOW_ENABLED=false`. 라이브 데드락 해제 로그(`reason=broker_confirmed_no_pending_sell`) 미발생/오작동 시 재검토.
- **잔여:** 실 broker 조회 경로는 mock 검증(기존 fast_fill과 동일 패턴). 라이브 첫 데드락 케이스 실효 미확인. P1 funnel→추격 net 리뷰 도구 미작성(며칠 shadow 축적 후). 추격매도 enforce(P1 2단계)는 shadow net+ 검증 후. 커밋: (이번 커밋).

---

### selection 프롬프트에 V2 fresh brain 가드 적용 (stale correction_guide 누수 차단)

- **무엇:** `minority_report/analysts.py::select_tickers`에 `_v2_fresh_brain_selection_active()` 가드 추가. `V2_FRESH_BRAIN_START=true`(또는 `V2_BRAIN_POLICY=fresh*`)면 selection 프롬프트에 레거시 `brain_summary[:700]` + `correction_guide[:450]`를 주입하지 않음. 헬퍼는 `trading_bot._v2_fresh_brain_policy_enabled()`와 동일 정책. 테스트 `tests/test_selection_fresh_brain_guard.py`(7: 헬퍼 5 + 실제 프롬프트 캡처 2).
- **왜:** 시장판단 경로(`_brain_context_for_judge`)는 이미 이 가드로 레거시 brain을 차단하는데 **selection 경로만 가드 누락**. 그 결과 `V2_FRESH_BRAIN_START=true`인데도 5/12(US)·5/18(KR)에 동결된 stale `correction_guide`(예: KR "MILD_BULL 이상 합의 차단 / Bull 근거 사용 금지" 급락장 지침)가 6월 강세장 selection 프롬프트에 매일 주입되던 일관성 버그. (correction_guide 자동승격은 5/20 커밋 `4f0cd1c`로 차단됐고 승인큐 consumer 부재로 영구 동결 → "강세장에 급락장 렌즈".)
- **현재상태:** 코드 반영. `V2_FRESH_BRAIN_START`는 이미 `true`(라이브)라 config/env **무변경**. `state/brain.json` **무수정**. 주문/리스크/broker truth/Claude 호출량 무변경(프롬프트 입력 정제, 토큰 소폭↓). **봇 재시작 시 반영.**
- **롤백조건:** `V2_FRESH_BRAIN_START=false`면 레거시 동작 복원(단 시장판단도 함께 레거시로). selection만 되돌리려면 코드 revert. selection 분포/품질 악화 시 재검토 — 단 selection 무엣지 7회 측정이라 위생 처방(개선 보장 아님, 강세장 급락장렌즈 제거가 목적).
- **잔여:** brain.json 오염(count=1 단일사건, KR에 US종목 P036/037/044, market_regime stale)은 fresh brain이면 selection엔 안 가나 telegram/postmortem 등 다른 경로 잔존 — 별도 후속. 커밋: (미커밋, 운영자 지시 후).

---

## 2026-06-21

### 측정 배선 복구 — market_regime 캡처 (regime 0/304 → forward 복구)

- **무엇:** PathB `entry_market_regime`(→`v2_learning_performance.market_regime`)이 항상 빈 값이던 버그 수정. ① `trading_bot._apply_consensus_guards`(consensus 보편 finalizer)에서 안정 per-market 캐시 `self.market_consensus_mode[market]=mode` seed ② PathB에 측정 전용 `_pathb_entry_market_regime()` 추가(캐시 1순위, today_judgment fallback, 없으면 빈 값=위조 금지) → 진입 캡처(11446)가 이걸 사용 ③ intraday_entry_shadow 펀넬 regime도 동일 캐시 fallback.
- **왜:** 진단 결과 `market_regime`이 closed 304건 전부(6/15 이후 진입+청산 41건 포함) 빈 값. 근본 원인 = `today_judgment`가 시장마다 덮어쓰이고 사이클마다 `{}`로 리셋되는 단일 dict라, PathB가 진입/청산/exit-scan 시점에 읽으면 비어있음. 국면조건부 운영·토글 사후검증의 선결 인프라.
- **현재상태:** 코드 반영(라이브 게이팅 `_pathb_consensus_mode`=RISK-OFF cap 입력은 **무변경**, 측정만 복구). **봇 재시작 시 forward 캡처 시작.** 과거 304건은 소급 불가(당시 mode 유실, 빈 값 유지).
- **롤백조건:** 신규 진입이 여전히 빈 regime이면 진단 재개(seed 시점 vs 진입 시점 추가 조사). 라이브 동작 변화는 없어 긴급 롤백 불요.
- **검증:** `tests/test_pathb_entry_regime.py`(7: 캐시우선·per-market격리·fallback·빈값위조금지·라이브게이팅불변·guard seeding) 신규; `test_pathb_runtime.py`(136)·unanimous/consensus(8)·intraday_entry_shadow(5) 통과; 전체 `pytest tests/` 2571 passed(65 fail은 `test_preopen_continuation_shadow.py`의 Python 3.10 전용 `ignore_cleanup_errors` 사용 = 기존 환경 비호환, 본 변경 무관); py_compile OK; mojibake staged OK; `live_preflight --mode live` ok=True FAIL=0.
- **커밋:** 7d03e58
- **남은 배선(미수정, 의도적):** `pnl_pct_net` 203건 NULL = 오염방지 설계대로(forward 정상, 과거 measured net 소급불가). `mfe_backfill_yf` 6/13 정지 = 도구 재실행 필요(별도, 봇 락 경합 감안 go 확인).

### A~F 멀티에이전트 토론 결론 → 검증 안 된 토글 2개 OFF (덜어내기)

- **무엇:** ① C3 당일등락 과열 페널티 OFF (`CANDIDATE_CHANGE_OVERHEAT_ENABLED=false`, env 토글 신규 추가) ② `KR_MOMENTUM_EARLY_ENTRY_ENABLED=false`.
- **왜:** A~F 3자 토론(codex + Claude 빌더 + 사회) 영역 A 사후검증. C3 → US 급등후보(change≥15%) fwd3 +5.4%(live)/+11.5%(backfill)로 페널티가 US 최고 후보를 selection에서 깎음(메모리 "당일등락차단=US −118%p"와 일치), KR도 hit(−3.87)가 non-hit(−6.28)보다 덜 나빠 방향 반전 → 두 시장 모두 근거 반증. KR momentum early → 6/16 enforce 후 체결 0(무발동)·손실경로라 검증 안 된 enforce.
- **현재상태:** 두 토글 OFF(`.env.live` + `config/v2_start_config.json` 동시 반영). **C2(KR vol_ratio) 페널티는 별개 코드 경로로 유지.** 코드(C3 블록·KR momentum early 로직)는 보존, env로 즉시 롤백 가능. **봇 재시작 시 반영**(별도 재시작 트리거 안 함).
- **롤백조건:** C3 재ON은 시장별 live C3 hit 표본 ≥30에서 fwd가 non-hit보다 열위일 때. KR momentum 재ON은 실체결 ≥20·평균 net>0·loss_cap 비율 개선 시.
- **검증:** `tests/test_candidate_overheat_penalty.py`(10, C3 토글 on/off + C2 분리), `tests/test_momentum_early_entry.py`(8) 통과; py_compile OK; JSON 유효.
- **커밋:** 7d03e58
- **보류(미적용):** 사이징 vol-target(REJECT·알파없음), 청산 정밀화/target extension(REJECT·3번째 확인, seed였던 TARGET capture 1.56은 yfinance 오측 아티팩트), cluster throttle shadow(측정배선 선결로 보류), PEAD(현행유지). **다음 본작업 = 측정 배선 복구**(pnl_pct_net 75% NULL / mfe_backfill 6/12 정지 / market_regime 빈값 — 모든 토글 사후검증의 선결).

## 2026-06-20

### net FX 스프레드 정직화 (자 고치기 — 측정만, 거래로직 무관)
- **무엇:** ① 생산자 `execution/claude_price_sell_manager.py::_close_cost_meta`에 FX 인지 net 필드 추가(`fx_spread_pct_round_trip`/`pnl_pct_net_after_fx_est`/`fx_spread_krw_est`/`pnl_krw_net_after_fx_est`). 기존 `pnl_pct_net_est`(수수료만)는 보존. ② 소비자 `tools/capture_net_review.py`에 `DEFAULT_FX_SPREAD_PCT={US:0.2,KR:0}` + `--fx-us/--fx-kr` CLI, net_of/손익분기/헤더 반영.
- **왜:** net이 수수료(0.5%)만 빼고 **환전 스프레드(US 환전 2회)를 0으로 둬서** 과대평가됨(usd_krw=참조환율). 성능을 재려면 자가 정직해야 함.
- **현재상태:** capture_net_review 재실행 **US net합 +1.3%→−61.0%(PF 1.01→0.78)** — 본전 아니라 손실이 드러남(우대0.2% 가정, 무우대면 더). 생산자 필드는 **재시작 후 신규 청산부터** payload 기록(측정 전용, 주문/리스크/exit 무관). env `US_FX_SPREAD_RATE_PER_SIDE`(기본0.001=우대). **11 테스트 통과, py_compile ok, 모지바케 0.**
- **롤백조건:** `--fx-us 0`(리포트) / 생산자 필드는 additive라 무해(기존 net 불변). 우대 확정 시 값 조정.
- **잔여:** 생산자 새 필드가 ledger sync·대시보드 미배선(capture_net_review만 정직, 후속).
- **커밋:** 이 커밋.

## 2026-06-19

### #2 상시 정합성 체크 도구 신설 (정합성 스윕 종결자)
- **무엇:** `tools/integrity_check.py`(read-only 진단) + 테스트 12. 오늘 손으로 캔 두 부류를 자동 탐지: A형(학습원장 핵심필드 충진율 — mfe/mae/regime/net), D형(잡 stale freshness — forward 측정기·sync·outcome 최신 age), sync 커버리지. OK/WARN/FAIL + exit code.
- **왜:** A(mfe 배선)는 손으로, D(forward 측정기 3주 정지)는 우연히 캤다. 사람이 안 보면 사일런트 브레이크를 못 잡는다 → 자동 깃발 필요.
- **현재상태:** ① 단독 실행(`python tools/integrity_check.py`) + `--watch`/`--telegram-alert` 루프 모드. ② **`start_live_stack.bat`에 stack 탭으로 상시 가동 배선**(`--watch --interval-sec 600 --telegram-alert`, live_guardian 패턴). FAIL **변동 시에만** 텔레그램(상태파일 fingerprint, 복구 시 정상알림 — 스팸 방지). 첫 실행: 잡 freshness 전부 🟢(D 치유 확인), mfe/mae/regime 🔴(A fix 배포 직후 전환상태 — 재시작후 청산 누적되면 🟢로, **이게 A fix 자동 검증 루프**). **다음 stack 재기동 시 탭 가동.**
- **롤백조건:** bat 탭 줄 제거 / 토글 없이 프로세스만 종료. read-only라 주문·DB쓰기 무관.
- **잔여:** ① 필드 충진은 절대임계라 배포 직후 전환구간 빨강(전환완료 후 의미) → 추세기반(전주 대비 급락) 보강 후보. ② telegram_reporter 자격 없으면 send_skipped(알림만 누락, 체크는 계속).
- **커밋:** 미커밋.

### 배선 fix: 기존 broker_sync 포지션도 PathB 귀속 복구 (AVGO 자가치유 갭)
- **무엇:** `_sync_runtime_with_broker` verify 경로에, 이미 broker_sync로 굳은(PathB 귀속 없는) 포지션이 단일-호환 PathB run을 가지면 `_pathb_broker_recovery_template`로 귀속 복구 + target/stop 복원 + ack'd run을 FILLED 전환. 회귀 테스트 1건.
- **왜:** 직전 fix(ack'd 주입 복구)는 신규 broker 주입 경로에만 붙어 `seen_keys` 때문에 **이미 broker_sync로 굳은 기존 포지션엔 안 닿았다** → 재시작해도 AVGO 자가치유 안 됨(라이브로 확인). verify 경로 보강으로 메움.
- **현재상태:** **재시작 후** 다음 broker sync 사이클에 AVGO가 broker_sync→claude_price 귀속 + target 404/stop 379 복원 + run FILLED. 검증: broker_sync 메타 7 + reconcile 회귀 177 passed, preflight ok=True FAIL=0.
- **롤백조건:** 코드 되돌림. broker truth 1차·단일매칭·micro_probe/recovery_micro 제외·충돌시 무변경(생성·주문·sizing 무관, 기존 체결 귀속만).
- **잔여:** 동일 ticker 수동 lot 우연일치 오귀속(기존 복구와 동일 리스크, 충돌→manual). 라이브 AVGO 치유 다음 사이클 관측.
- **커밋:** 미커밋.

### 배선 fix: broker sync ack'd run 치유 (AVGO 고아 — 정합성 스윕 보강)
- **무엇:** `_pathb_broker_recovery_template`의 `eligible_statuses`에 `ORDER_ACKED`/`ORDER_SENT` 추가 + 단일-호환 매칭이 ack'd run이면 브로커 보유 truth(실평단·수량)로 `mark_filled`해 run을 FILLED로 전환. 회귀 테스트 1건(AVGO 시나리오).
- **왜:** 매수 ack 후 fill 확정이 끊긴 채(세션경계 재시작) 브로커엔 실제 보유로 남은 PathB run이, 메타 복구 대상(ack'd 제외)에서 빠지고 FILLED 전환도 안 돼 broker_sync generic으로 귀속 → PathB target/stop 관리 밖 고아(AVGO 6/17 d6df4eab). 봇이 fill 확정을 인-세션·당일 today_fills·동일 세션에 의존하던 3중 가정의 구멍.
- **현재상태:** 단일-호환 broker 보유면 자동 PathB 귀속 + target/stop 복원 + run FILLED. **AVGO는 봇 재시작/동기화 사이클에 소급 치유.** 미커밋→커밋. 검증: broker_sync 메타 6 + reconcile 회귀 205 passed, preflight ok=True FAIL=0.
- **롤백조건:** 코드 되돌림(eligible_statuses에서 ack'd 제외). 신규 주문·sizing·하드스톱·broker truth 우선 무변경(기존 체결 귀속 복구). 충돌 시 manual로 빠짐.
- **잔여:** 동일 ticker 수동 lot이 ack'd run qty·가격(5%)과 우연 일치 시 오귀속 가능(기존 FILLED 복구와 동일 리스크, 충돌→manual). 라이브 AVGO 치유는 다음 동기화 사이클에 관측.
- **커밋:** 미커밋.

### 배선 fix: Phase 1c MFE/MAE/regime → 학습 원장 (정합성 스윕 A칸)
- **무엇:** PathB 자동청산 CLOSED 이벤트 발행자 `mark_closed`에 `mfe_pct`/`mae_pct`(운영자) + `entry_market_regime`(완결) 인자 추가 → CLOSED payload에 `position_mfe_pct`/`position_mae_pct`/`entry_market_regime` 포함(값 있을 때만, 0/빈값 위조 안 함). 호출부 2곳(`_finalize_pathb_sell_close`/`on_external_close`)에서 exit_meta·closed_trade의 값 전달. 회귀 테스트 3건.
- **왜:** Phase 1c가 계산한 observed MFE/MAE와 진입국면이 mark_closed 호출에서 끊겨 CLOSED payload 미포함 → sync(`sync_v2_learning_performance.py:971,979`)가 못 읽어 `v2_learning_performance` mfe_pct 97%·mae_pct 96%·market_regime 100% NULL. profit_guard/weak_mfe/국면조건부 capture를 라이브 데이터로 못 재던 근본 원인.
- **현재상태:** 배선 완결(observed→exit_meta→mark_closed→CLOSED payload→sync→learning). **봇 재시작 시 적용**(현재 봇은 옛 코드). 미커밋. 검증: cost_meta 10 + 보호영역 232 passed, mojibake clean, py_compile OK.
- **롤백조건:** 코드 되돌림(인자 제거). observe-only 키라 주문·리스크·ladder(peak_pnl_pct) 무영향이므로 위험 낮음.
- **잔여:** ① 과거분 forward-only(백필 별도). ② mark_closed 안 타는 기타 CLOSED 10건/114 미적용(reconcile/manual, 볼륨 경로 아님). ③ 정합성 스윕 나머지 칸 B(net 41% 누락)/C(미생산 필드)/D(forward 라벨)/E(selection 라이트백) 미착수.
- **커밋:** 미커밋.

### 운영 조치: forward 측정기(v2_daily_loop) 3주 정지 → 재실행 catch-up (정합성 스윕 D칸)
- **무엇:** ① `v2_daily_loop` 1회 재실행(dry-run 검증 후 실제, `--market ALL --forward-lookback-days 20 --skip-simulation --skip-optimizer`) — 백로그 catch-up. ② **영구 배선(코드)**: `trading_bot._run_v2_forward_measure_at_session_close` 추가, 세션마감에 forward 측정을 sync보다 먼저 자동 호출(기존 sync 훅과 동일 subprocess 패턴, env `V2_FORWARD_MEASURE_AT_SESSION_CLOSE` 기본 true). FORWARD_MEASURED 169→389(+220), `forward_complete` 6월 0→42.
- **왜:** 이 잡이 **2026-05-27 이후 정지**(봇이 호출 안 함=외부 cron/수동, 죽음). 입력 price CSV는 신선했으나 측정만 3주 밀려 forward_complete 100% NULL. 하류로 lesson candidate_builder(`forward_complete=any(FORWARD_MEASURED)`)·lesson_forward_validation을 굶겨 **"적용 검증교훈 0개" 결론이 오염**됐을 가능성.
- **현재상태:** 백로그 catch-up 완료(완료가능 6/1~6/12 중 42/68, 최근 42건은 forward 미경과 정상 pending). 영구 배선은 **봇 재시작 후** 다음 세션마감부터 발화 → 라이브 1회 확인 필요(코드만으론 미검증). event store WAL+30s라 US장중 경합 안전. 검증: py_compile OK, cost_meta 10 passed, mojibake clean.
- **롤백조건:** env `V2_FORWARD_MEASURE_AT_SESSION_CLOSE=false`(즉시 무효화). 측정 데이터 기록일 뿐 주문/브로커 무관.
- **잔여:** ① 완료가능인데 미완료 26건(partial-horizon/sync join 의심) 추가 진단. ② 봇이 세션마감에 도달 못하면(크래시·수동중단) 여전히 안 돎 → **#2 상시 정합성 체크**(잡 stale 자동 감지)가 최종 안전망. ③ catch-up 후 lesson forward-validation "0 valid lessons" 재평가 필요.
- **커밋:** 미커밋.

## 2026-06-18

### PathB 진입 fast-fill — enforce 라이브 재호가 (운영자 "수정해서 인포스로")
- **무엇:** `_fast_fill_requote_run`/`_fast_fill_resubmit` 추가. 데드존(미체결+가격>limit)에서 옛 주문 취소→broker truth 취소확정→미체결 확인 시 새 path_run으로 bounded 가격 재진입. config `(US_/KR_)PATHB_FAST_FILL_MODE=enforce`(shadow→enforce 전환).
- **왜:** 운영자 결정 — 측정(shadow)이 아니라 실제로 못 산 걸 bounded로 사 넣어야 함. shadow는 그가 원한 게 아니었음.
- **현재상태:** **config enforce(US+KR). 단 재시작 시 적용**(현재 봇은 옛 코드). 이중매수 가드: 옛 주문 체결시 실체결가 기록만(#1)·재제출X, open이면 대기·재제출X, 취소확정+미체결일 때만 재진입. bound `MAX_CHASE_PCT=1.0`/`MIN_REWARD_PCT=1.5`. 검증: fast_fill 10 + PathB 회귀 194 + preflight ok FAIL0. **라이브 broker 왕복 미검증(로직 테스트만).**
- **롤백조건:** `(US_/KR_)PATHB_FAST_FILL_MODE=off`. 라이브 이상 시 즉시.
- **잔여:** ① 라이브 broker 왕복(취소+재제출) 라이브 미검증 → US장 중 재시작 지양, 마감장/개장전 점검 권고. ② reentry guard가 재진입 차단 가능(그럼 미체결=손해無). ③ #1 cost-basis는 재호가 체결경로만 실체결가 기록, 전체 장부 진실화는 별도.
- **커밋:** 미커밋.

### PathB 진입 fast-fill — 데드존 bounded 재호가 측정 (shadow→enforce로 대체)
- **무엇:** `runtime/fast_fill.py`(순수 결정엔진) + `pathb_runtime.py` cancel_above 데드존 shadow 측정 훅. config `(US_/KR_)PATHB_FAST_FILL_MODE=shadow`, `MAX_CHASE_PCT=1.0`, `MIN_REWARD_PCT=1.5`.
- **왜:** 093370 실측 — 봇 limit(17900) 미체결인데 가격이 cancel 임계(~18795) 아래 "데드존"에 16분 방치 → 깨끗한 진입 미스 → 운영자가 bound 없이 수동 추격 → target 위 과지불 손실 + 봇이 17900으로 잘못 귀속해 가짜 +3% 양수.
- **현재상태:** **shadow 측정(주문 무영향).** 데드존에서 "bounded 재호가하면 잡혔나(REQUOTE) vs 추격하면 손실(MISS)"를 funnel(`logs/funnel/fast_fill_*.jsonl`) 기록. 결정엔진은 enforce-capable이나 **라이브 재호가 실행(주문 취소+재제출)은 의도적으로 미배선**(라이브 주문 교체 = fragile broker-truth flow, 별도 테스트 단계). enforce 현재=shadow와 동일(로깅만). 093370 시뮬: @18000 REQUOTE(승), @18460 MISS. 검증: 테스트10 + PathB회귀174 + preflight ok FAIL0 + 인코딩 clean. **재시작 시 shadow 측정 시작. US·KR 둘 다.**
- **롤백조건:** `(US_/KR_)PATHB_FAST_FILL_MODE=off`. enforce(라이브 재호가)는 shadow가 시장별 net+ 증명 + 주문교체 테스트 통과 후.
- **잔여:** ① 라이브 재호가 실행 미배선(default shadow라 무영향). ② **장부 오염(#1): broker 실체결가 cost-basis 기록**은 별도 — 수동개입/슬리피지 시 봇 limit≠실체결가로 표시손익 부풀림(093370 가짜양수의 근본). 미구현, 운영자 결정 대기.
- **커밋:** 미커밋.

### 꼬리-capture 엔진 — 전체 enforce-capable 완성 + shadow preset (운영자 결정)
- **무엇:** target override(엔진 active 시 ladder+claude_price target 억제, trail이 profit-side 소유) + 오버나잇 carry execution(`should_carry_overnight` → pre_close skip) 추가. **`TAIL_CAPTURE_MODE=shadow`·`HOLD_ADVISOR_CARRY_ALIGN_MODE=shadow`로 preset**(.env.live+config json).
- **왜:** 운영자 결정 "전부 enforce-capable로 짓고 config로 동작 결정, shadow면 무위험." 재시작만 하면 shadow 동작.
- **현재상태:** **shadow(로깅만, 라이브 0).** 전부 enforce-capable(trail/target override/carry/hold align) — config 토글로 enforce. **오버나잇 carry 실행은 서브게이트 `CARRY_ENFORCE=false`**(검증 후). 하방=loss_cap 위임. 검증: 테스트25 + 보호영역 회귀 308 + preflight ok + shadow 안전성. **재시작 시 shadow 가동.**
- **롤백조건:** `TAIL_CAPTURE_MODE=off`. enforce 전환은 forward 재구성 검증(carry net+ & 손실누수0 & 약세장) 후.
- **잔여(게이트됨):** carry-enforce 켤 때 기존 `_pathb_session_close_carry`와 상호작용 검증 필요(현재 CARRY_ENFORCE=false라 비활성).
- **커밋:** 미커밋.

### Track 3-R: tail capture ↔ hold advisor carry-intent 정합 — 빌드 (운영자 #1 레버)
- **무엇:** `minority_report/hold_advisor.py`에 carry-intent 정합. `HOLD_ADVISOR_CARRY_ALIGN_MODE=off`(기본).
- **왜:** tail_capture 엔진이 "carry" 깃발 꽂아도 hold advisor가 conf<0.72 HOLD를 강등(intraday_review/pre_close SELL)시켜 러너를 죽임 = 엔진 사보타주. 정합 없이는 enforce 무의미.
- **현재상태:** **기본 off.** enforce면 carry-intent HOLD(이익+MFE≥4%+RISK_ON)이 conf<0.72여도 bounded HOLD 존중. **손실중·약세장·MFE미달 무영향(0.72=손실방어 불변).** shadow=로깅. 보호영역 회귀 312 passed + preflight ok. 봇 재시작 시 off로 탑재.
- **롤백조건:** `HOLD_ADVISOR_CARRY_ALIGN_MODE=off`. 즉시.
- **커밋:** 미커밋. tail capture와 같은 shadow에서 검증.

## 2026-06-17

### 통합 꼬리-capture 청산 엔진 — shadow 빌드 (운영자 결정)
- **무엇:** `runtime/tail_capture.py`(결정엔진) + `pathb_runtime.py` exit scan 훅(shadow 로깅/enforce trail). config `TAIL_CAPTURE_*` 12키 `.env.example`, **전부 OFF.**
- **왜:** 시스템=멀티데이 꼬리-수확기(net=상위10%). 꼬리의 24%(+67%p) 새는 게 본전의 원인. path-aware 시뮬 +33%p(증명후 wide-trail). selection 아니라 **꼬리 capture가 유일 레버.**
- **현재상태:** **기본 OFF.** shadow=funnel 로깅(라이브0)/enforce=trail 실청산(loss_cap 뒤, ladder 앞=하방위임). **CARRY 실행은 서브게이트 `CARRY_ENFORCE=false`**(cross-day 갭 미검증). 검증: 테스트13 + PathB회귀256 + preflight ok FAIL0. **봇 재시작 시 OFF로 탑재.**
- **롤백조건:** enforce 이상 시 `TAIL_CAPTURE_MODE=shadow/off`. 보호영역(ladder/preclose/claude) 무접촉(additive)+fallback.
- **커밋:** 미커밋. 설계 `docs/important/TAIL_CAPTURE_ENGINE_DESIGN_20260617.md`.

### 후보 프롬프트 풀 캡 축소 — 토큰 절감 (운영자 결정)
- **무엇:** `config/v2_start_config.json` 후보 캡 **US 40→24, KR 32→28** (7키: CANDIDATE_PROMPT_POOL_TARGET/HARD_CAP_KR·US, KR_PROMPT_POOL_CAP, KR_SELECTION_PROMPT_CAP, US_PROMPT_POOL_CAP).
- **왜:** `select_tickers`가 전체 input 토큰의 **40%(1.11M/3일)** = 단일 최대 소비처. 후보확대(v3)는 측정상 **진입품질 0 기여**(memory §3-b: v2≈v3 MFE) + 오늘 KR 수익 종목 전부 **rank 1~4**(top-24면 다 잡음, 확대구간 0). US 40은 CLAUDE.md 문서값(24) 초과 드리프트였음 → 문서값 복귀.
- **현재상태:** US 24 / KR 28 (CLAUDE.md candidate funnel 문서값과 일치). 후보 ~40% 컷 → 전체 토큰 ~15% 절감 추정. 오늘류 수익(rank1-4) 무영향 예상. **봇 재시작 시 반영.**
- **롤백조건:** trade_ready 후보 부족/missed runup 증가 시 KR 28→32·US 24→40 복귀. 즉시.
- **커밋:** 미커밋. `.env.live`엔 해당 키 없음(config json만 보유, override 우위).

### 교훈 forward-validation 레이어 — enforce 적용 (운영자 결정)
- **무엇:** 신규 교훈검증 파이프라인 탑재 + `LESSON_VALIDATION_ENABLED=true`, `LESSON_VALIDATION_APPLY_MODE=enforce`. (`.env.live` + `config/v2_start_config.json` 둘 다)
- **왜:** 기존 lesson은 빈도로만 채점(forward 검증 0) → 함정 교훈(watch_only 완화 forward −5.9%) 통과 위험. 반사실 gain(국면별)+cost_floor+부호일관+신선도+신뢰로 채점, 검증통과만 bounded control(`entry_priority_cutoff_adjust` ±0.05) 국면조건부 반영.
- **현재상태:** **valid_apply 0개 → enforce 켜도 안전 no-op**(기존 tuner값 유지). 세션마감마다 자동 축적(`trading_bot.session_close` hook, config 무관). valid 되면 bounded 적용, 함정(KR/risk_off invalid_block) 차단. brain/broker/hard safety 무접촉. **봇 재시작 시 반영.**
- **롤백조건:** 이상 시 `LESSON_VALIDATION_APPLY_MODE=shadow`(관측만) 또는 `LESSON_VALIDATION_ENABLED=false`(완전 OFF) 후 재시작. 즉시 롤백.
- **커밋:** 미커밋(운영자 검토 후). 검증: 모듈테스트 31 + 인접 32 + preflight ok=True FAIL0 + 운영 enforce 체인테스트.

## 2026-06-16

### 운영 통제 / 원칙
- **현실적 직언 원칙** 추가 — 긍정 편향 말고 데이터대로. [CLAUDE.md]
- **운영자 파악 가능성 원칙** 추가 — 변경 시 무엇/왜/현재상태 매번 명시, 롤백도 보고. [`4c662fc`]
- **운영 변경 일지(이 파일)** 신설.
- 커맨드 신설(.claude/commands, 로컬·gitignore): `/status`(현재 토글+커밋+봇상태), `/check`(검증), `/capture`(성과), `/monitor`(라이브점검), `/saveyou`(세션저장).

### 트레이딩 (재배선 1단계 — 청산 변별)
- **weak_mfe_cut OFF** (US/KR) — 단타 손절은 번지수 오인(손실 HOLD=stop_recovery는 정상). 청산을 hold advisor에 환원. **롤백**: `(US_/KR_)PATHB_WEAK_MFE_CUT_ENABLED=true`. 코드 보존. [`bcbd8ff`]
- **hold advisor 이익보호 prior ON** (`HOLD_ADVISOR_PROFIT_GUARD_ENABLED=true`) — 이익 중 HOLD 반납(70%, profit_pullback -8.91%p) 방지·익절 우선, 단 추세 살아있으면 러너 HOLD 유지. A/B 변별 -10%p→+25%p. **롤백**: 토글 false. **라이브 미검증 → 미국장 모니터.** [`bcbd8ff`]
- **capture_net_review 확장** — 실측 MFE 기반 net capture + 월별 국면 분해(forward 착시 분리). [`bcbd8ff`]
- 분석 도구 신설: hold_advisor_quality_test / ab_test / discriminate_test. [`bcbd8ff`]

### 진입 (momentum 진단)
- **momentum early-entry 진단 임계를 게이트와 통일** — momentum_wait 오분류 제거. [`cbb727b`]
- KR 청산 tp_check 미설정 KeyError 가드. [`67a6549`]

### 기각 (왜 안 했나 — 반복 논의 방지)
- **잡전략 OFF** — 무효. 손실의 PathB 경로(claude_price)라 전략 토글이 안 먹음.
- **profit_ladder 강화** — 보호영역 + 2026-06-14 롤백 이력. 설계+백테스트 선행 필요.
- **진입 빈도↓ (confidence↑)** — 진입 단일경로라 수익(target)도 위축.
- **진입 타이밍 튜닝** — 못 이기는 게임 + 반복 실패. 눌림매수 추종형은 구조적으로 늦음(정상).

### 검증 대기 (다음 행동)
- 미국장 라이브: prior가 giveback 줄이나 + **좋은 러너 오절단 부작용 없나** + weak_mfe OFF가 회복 vs 손실확대. 악화 시 prior 토글 롤백.
- 다음 숙제: 진입 변별 shadow → 측정 재정의 코드 → 수수료/KR.
