# de-confound 설계 — 7/2 동시배포 토글 귀속 계획

작성 2026-07-02. read-only 분석·설계(코드/매매 무변경). 오염 감사([메모리 전수 감사, 이 세션])의 후속 실행 1번.

## 0. 왜 이 문서가 먼저인가

7/2 봇 재시작에 **8군 토글이 동시 배포**됐다. 7월 net이 좋아지든 나빠지든 **어느 토글 때문인지 귀속 불가능**하다. 이 상태로 7월 말 데이터를 보면 평균 뒤섞인 채 같은 판정을 반복하게 된다(메모리 `buy-funnel-achilles`: "RTRB+reward_risk ENFORCE confound=de-confound 선결"). 따라서 데이터가 쌓이기 **전에** 토글별 판정지표·최소표본·통과/revert 조건을 못 박는다.

또한 이 계획은 **"저빈도=구조적/흑자전환 불가"라는 6/30 載重 판정이 매수 셧다운 버그(6/8 feasibility strip + 6/26 A1) 위에 섰다**는 오염 감사 결론을 전제한다. 그 판정은 셧다운 복원 후 clean 데이터로 재도출해야 하며, 그 재도출의 입력이 바로 이 de-confound 측정이다.

## 1. 활성 토글 집합 (config/v2_start_config.json 실측 2026-07-02)

| # | 토글 | 값 | 효과 | 소스 메모리 |
|---|---|---|---|---|
| T1 | `STRATEGY_FEASIBILITY_ENFORCE` / `_US` | true / **false** | US ready=1 복원(6/7 strip 해제), KR 유지 | strategy-feasibility-shutdown-fix |
| T2 | `REQUIRE_TRADE_READY` / `_KR` | true / **false** | KR ready=0 언블록(A1), US 유지 | a1-require-trade-ready-buy-shutdown |
| T3a | `PATHB_CONSISTENT_REWARD_RISK` | true | reward_risk 분모 정직화(bzh-stop) | reward-risk-enforce |
| T3b | `PATHB_DETERMINISTIC_SELL_TARGET_CAP_PCT` | 4 | sell_target 재량 상한(우회 차단) | reward-risk-enforce |
| T3c | `SINGLE_SYMBOL_JUDGE_MIN_REWARD_RISK` | 1.5 | PathB 게이트 하한 일치 | reward-risk-enforce |
| T4 | `KR_CATALYST_BONUS` / enabled | 12 / true | KR 스크리너 catalyst 가중 | screener-catalyst-lever |
| T5 | `US_CATALYST_BONUS` / enabled | 10 / true | US 스크리너 catalyst 가중 | screener-catalyst-lever |
| T6 | model `claude-sonnet-5` (R1/BULL/BEAR/NEUTRAL/ANTHROPIC) + `CLAUDE_THINKING_ENABLED=true` (effort medium) | — | 모델 업그레이드 | claude-model-5-upgrade |

추가 위생 3종(feedback lookahead 재프레이밍·lesson_quality actionable=false·analysts 워크드예시)은 코드 변경분(T3 묶음의 프롬프트측)으로 T3와 같은 축에서 움직이므로 T3 묶음에 포함해 판정한다.

### ★2026-07-04 갱신 (인벤토리 stale 보완 — 전면 스캔 후속)

7/2 이후 US 진입 경로에 **두 레버가 더 얹혔는데 위 표·§2에 미반영**이다. 7월 US net 귀속 시 반드시 포함:

| # | 토글 | 값 | 효과 | 발동 |
|---|---|---|---|---|
| **T2′** | `REQUIRE_TRADE_READY_US` | **false** (7/2 결정 채택) | **US A1 해제** — PULLBACK_WAIT 라이브 매수 개방 | 활성 |
| **T7** | `PATHB_RED_TAPE_GATE_MODE_US` / `_THRESHOLD_US` | **enforce** / −0.3 (7/4) | 개장대비 지수<−0.3% US 신규진입 차단(US bleeder) | 봇 01:44 재시작으로 라이브 |

**⚠️ §2 P0 전제 무효화**: §2는 "US A1이 PULLBACK_WAIT를 차단 → US 라이브 매수 구조적 불가"를 전제로 "US는 측정 대상 없음"이라 했다. **T2′(A1_US 해제)로 이 전제는 더 이상 사실이 아니다** — US PULLBACK_WAIT 라이브 매수가 열렸다(§2 재작성 필요, unexamined-layers 감사서 IREN 체결로 확인). 따라서 §3의 "US 측정 불가" 결론도 폐기, US도 KR과 동일하게 clean net 추적 대상.

**현재 동시배포 레버(US net 귀속 불가 집합) = T1~T7 + T2′.** 7월 청산 표본이 n=1이라 아직 어느 것도 판정 불가(전면 스캔 §3). red-tape(T7)만 forward 판독 도구(SPY 소급, sharp_reversal 순서 아티팩트 분리)로 개별 추적 가능.

## 2. 선결 블로커 (측정 이전에 반드시) — P0

**⚠️ 정정(2026-07-02 근본원인 조사): US "0체결"은 배관 버그가 아니라 A1 게이트가 설계대로 작동하는 것이다. US feasibility 복원은 US 라이브 매수를 재개시키지 못한다.** §3.5 W1 정정 참조. 실측:

- **US 라이브 매수 경로 = PULLBACK_WAIT(ready=0) 단일.** `v2_path_runs` 871건 전수: US 실제 체결(FILLED/CLOSED) 189건 **전부 PULLBACK_WAIT**, BUY_READY 체결 역사상 0(BUY_READY는 Path B에서 `SHADOW_CANCELLED`뿐 = shadow_only).
- **`not_patha_trade_ready`는 진입 액션으로 결정**(`trading_bot.py:8755`) → PULLBACK_WAIT는 항상 True. **feasibility 복원(selection trade_ready 축)은 이 플래그를 건드리지 않는다.**
- **US A1(`REQUIRE_TRADE_READY=true`, 유지)이 PULLBACK_WAIT를 차단**(`pathb_runtime.py:4323`). 6/26 이후 US 취소 사유 1위 = `REQUIRE_TRADE_READY_BLOCK` 74/132.
- 따라서 feasibility 복원으로 `trade_ready=1`이 선택 레벨에서 재개(7/2 US 23건)돼도 → 그건 BUY_READY/Path A(shadow_only) 축 → **라이브 체결 없음**. "0플랜"은 재시작/rehydrate 갭이 아니라 ①7/2 US 세션은 미발생(야간) ②직전 US 세션은 플랜 생성 후 A1이 전량 취소.

→ **P0 정정: US는 현 토글로 라이브 매수가 구조적으로 불가**(A1이 유일 경로 차단). "US 복원의 net 측정"은 **애초에 측정 대상이 없다**(라이브 US ready=1 체결은 존재 불가). 반면 **KR은 A1을 언블록해 PULLBACK_WAIT를 라이브로 열었으므로 다음 KR 세션에 실제 재개**된다 = KR만 진짜 복원.

**P0 통과 게이트(KR 한정):** clean KR 세션에서 `v2_path_runs` KR FILLED/CLOSED > 0 발생. US는 별도 결정(§3.5) 필요.

## 3. 토글별 판정 계획

원칙: **forward≠net**(MFE/forward는 낙관편향, enforce 판정은 실현 net = `pnl_pct_net_est`). **단일국면 금지**(최소 강세+약세 스트레치 각 1회). **n<30 enforce 금지**(메모리 반복 규칙).

### T1 — US feasibility 복원 (ready=1 재개) — ⚠️ 무효 판정, §3.5 참조
- **이 토글은 US 라이브 매수를 재개시키지 못한다**(위 §2 정정). 판정지표였던 "US ready=1 코호트 실현 net"은 **라이브로 존재하지 않는다**(US ready=1=BUY_READY=shadow_only). 근거였던 baseline "+0.36%"는 **Path A 착시로 확인됨**(§3.5).
- 남는 판정: T1은 net 레버가 아니라 **selection 지표(trade_ready) 위생 항목**으로 강등. 라이브 US 매수 결정은 A1(§3.5)에서만 가능.
- revert 무관(라이브 매매 무변경). 유지/삭제는 selection 로그 정합성 차원.

### T2 — KR A1 언블록 (ready=0 허용)
- 판정지표: KR ready=0 코호트 실현 net. baseline breakeven·개선중(4월-1.78→6월-0.07)
- 최소표본: KR 체결 N≥20(KR 표본 원래 얇음 — 판정보류 가능성 명시)
- 통과: KR ready=0 net ≥ 0 근방
- revert: KR ready=0 net 음전 → `REQUIRE_TRADE_READY_KR` 삭제(=true 복귀)
- 도구: 동상 `--market KR`

### T3 — reward_risk 3종 묶음 (T3a+b+c+위생3)
- **분리 불가**: 셋 다 같은 진입게이트 축(reward_risk 정직화) → 하나의 효과 단위로 판정
- 판정지표: **거부 코호트 forward_3d**(거른 셋업이 본전 이하였나) + 필터배치 실현 net
- 최소표본: 거부 셋업 N≥30(양 시장), 다국면
- 통과: 거부 셋업 forward 음수(=거른 게 옳음) & 통과분 net 비열화
- revert 신호: 거부 코호트 forward 지속 양수(기회손실) → 캡 완화(4→5~6%) 또는 3토글 off. 조기신호 상충(7/1 KR 거부 bounce 종가 +1.4/+3.0=본전~소폭=중립)
- 도구: `tools/reward_risk_enforce_review.py`(구축됨, 세션마다 재실행)
- ⚠️ 시장 주의: US=모멘텀(오른 종목 계속) → enforce가 US 모멘텀 승자 거를 위험 KR보다 큼 + US 이미 net 적자. **시장별 캡 차등 필요 가능성.**

### T4/T5 — catalyst KR+12 / US+10
- 판정지표: **후보풀 순위변화 아닌 실전 net** — catalyst 교체종목 진입분 forward/net vs 비교체
- 최소표본: 교체로 진입된 종목 N≥20/시장, placebo 통제 + 다국면 OOS(6월 단일 미통과분 정면검증)
- 통과: catalyst 진입분 net > 비catalyst, placebo 초과 유지
- revert: US 3일 착시 재현(net 우위 소멸) → `US_CATALYST_SCORE_BONUS_ENABLED=false`. KR 우위 소멸 → KR도 off
- 도구: `tools/screener_catalyst_shadow_review.py`
- ⚠️ 순위변화≠수익(메모리 명시). 발동빈도 40~62% 실측됐으나 net 미확인

### T6 — sonnet-5 + thinking
- 판정지표: **net 아님(국면분리로만)** — 우선 파싱 안정·형식이탈률·근거품질. selection 형식이탈 ~33% 관측(위생이슈)
- 통과: 형식이탈률 비악화 + before/after 결정 방향 안정
- revert: 형식이탈 급증 or 지연 초과 → `CLAUDE_THINKING_ENABLED=false`(무thinking drop-in) 또는 모델 롤백
- 도구: before/after 리포트(selection/hold_advisor/haiku 20씩, 메모리 명시), raw_calls parse_stage 카운트

## 3.5 W1(매수복구) 프레임 정정 — 근본원인 조사 결과 (2026-07-02)

오염 감사가 지목한 최대 실재 레버 W1("매수 셧다운 복원")를 read-only 실측으로 재검한 결과, **US 절반은 (a)근거 숫자가 착시이고 (b)라이브 효과도 없다.** KR 절반만 진짜 복원이다.

**(1) 아키텍처 사실 (코드+DB 확정)**
- US 라이브 매수 = PULLBACK_WAIT(ready=0) 단일 경로. path_runs 871건 중 US 체결 189건 전부 PULLBACK_WAIT, BUY_READY 체결 0.
- BUY_READY(ready=1) = Path B에서 shadow_only. `not_patha_trade_ready`는 진입 액션으로 결정(`trading_bot.py:8755`), PULLBACK_WAIT는 항상 True.
- US A1(`REQUIRE_TRADE_READY=true`, 유지)이 PULLBACK_WAIT 차단(`pathb_runtime.py:4323`, 취소 74/132). → **feasibility 복원은 US 라이브 매수를 못 연다.**

**(2) "+0.36% 복원가치" = Path A 착시 (소스 실측)**
- handoff의 US ready=1 +0.36% / ready=0 -1.97% / KR ready=1 -0.97% / ready=0 -3.73%는 **`ticker_selection_log`(trade_ready 분할·traded=1·pnl_pct)로 소수 3자리까지 정확 재현** — v2_path_runs(Path B 라이브) 아님.
- "US ready=1 +0.356%"(n=14)의 실체: **14건 중 9건 음수, 중앙값 -1.14%**, 평균은 상위 4 이상치(CRCL +7.70·NVTS +5.73·HPQ +4.43·PI +2.68)가 구동. 소스 혼재(claude_price 5·signal_entry 4·None 4·v2_learning 1). = 전형적 평균의 오류 + 이질 조인.
- **라이브 US 책 실제**: Path B CLOSED n=188, **net_est -0.573%**(n=95). 선택로그는 US traded 33건만 잡음(라이브 188건의 극소 조인) = pathb-truth 경고한 Path A 레그.
- 즉 `STRATEGY_FEASIBILITY_ENFORCE_US=false`(US 복원)의 근거 숫자가 오염이고, 라이브 효과도 없음.

**(3) 정정된 W1 프레임**
- **KR (진짜 복원):** A1 언블록(`REQUIRE_TRADE_READY_KR=false`)이 KR PULLBACK_WAIT를 라이브로 열었음 = 올바른 레버. 다음 KR 세션에 실제 재개 → T2로 판정.
- **US (무효 복원):** feasibility 복원은 라이브 무효. US 라이브 매수를 원하면 **US A1을 손대야** 하고, 그건 -EV로 판정됐던 ready=0 코호트(라이브 net -0.57%) 재유입. 세 갈래(a1 메모리와 동일): ①US A1 유지=US 라이브 매수 off 수용 ②US A1 언블록=매수 재개+-EV 재유입 ③양성 서브셋(거래량 확정 돌파)만 A1 통과 — 단 그 서브셋 net을 평균 아닌 분리+분봉 양방향으로 측정해야(미측정). **운영자 결정 사항.**

**(4) 載重 판정에의 함의**
- "저빈도=구조적/흑자전환 불가"(6/30 만장일치)는 매수 셧다운 기간 데이터 위에 섰는데, 그 셧다운의 US 부분은 **여전히 해소 안 됨**(A1이 라이브 경로 차단). 즉 US "저빈도"는 지금도 config 상태이지 순수 구조가 아님 — 단, 그걸 풀면 라이브 net -0.57% -EV 재유입이라 "본전위생 상한" 결론과 모순되지 않는다. **재도출은 KR 라이브 재개 후 clean 데이터로.**

## 4. 귀속 전략 (동시배포를 어떻게 분리하나)

재시작 비용 때문에 "토글 하나만 끄고 관측"은 비현실. 대신 **구조적 분리** 3축:

1. **시장 분리**: US 활성=T1(false, **라이브 무효**)·T2(US유지=라이브 매수 off)·T3·T4·T5·T6 / KR 활성=T1(KR유지)·T2(false=**KR 라이브 매수 재개**)·T3·T4·T6. → **US는 라이브 신규매수가 A1으로 막혀 있어 7월 US net은 기존 보유분 청산 위주**(신규 진입 거의 없음). KR은 A1 언블록으로 신규 매수 재개 = 7월 KR net이 실질 판정 대상. **즉 W1 관련 실측은 사실상 KR만 가능.**
2. **코호트 clean 로깅**: 각 게이트 거부/통과 코호트를 거부시점 스냅샷으로 로깅해 코호트별 net(메모리 buy-funnel 처방: "ENFORCE 고정 + 차단코호트 clean 로깅"). T3(reward_risk 거부)·T2(RTRB 거부)가 같은 종목에 이중발화하므로 코호트 태깅으로 귀속.
3. **T6는 전역 교란**: 모델은 모든 프롬프트에 영향 → net이 아니라 형식/파싱 지표로 격리(net 귀속에서 제외, 국면분리로만).

**분리 한계 명시**: T3·T4·T5는 같은 selection/진입 파이프를 공유해 완전 분리 불가. 시장차+코호트 로깅으로 최대 분리, 잔여 confound는 정직하게 "묶음 판정"으로 표기.

## 5. 측정 순서 / 일정

1. **P0 (선결)**: 0플랜 근본원인 → US clean 세션에서 체결 재개 확인. **이게 안 되면 나머지 전부 보류.**
2. **표본 축적**: 다국면(특히 약세 스트레치 1회 — 메모리 반복 교훈: 강세 단일 신호는 다국면서 뒤집힘). 최소 2~3주.
3. **토글별 판정**: §3 게이트대로. n<30/단일국면은 판정보류(표기).
4. **載重 재도출**: 위 결과로 "저빈도/천장" 판정을 clean 데이터에서 다시 세운다.

## 6. revert 매트릭스

| 토글 | revert 신호 | revert 방법(env, 양쪽 .env.live+config) |
|---|---|---|
| T1 | (라이브 무효 확인 §3.5) net revert 무관 — selection 위생 항목으로 강등 | `STRATEGY_FEASIBILITY_ENFORCE_US` 유지/삭제는 로그 정합성 차원 |
| T2 | KR ready=0 net 음전 | `REQUIRE_TRADE_READY_KR` 삭제 |
| T3 | 거부 코호트 forward 지속 양수 | 캡 4→5~6 or 3토글 off + 코드3 복원 |
| T4 | KR catalyst net 우위 소멸 | `KR_CATALYST_SCORE_BONUS_ENABLED=false` |
| T5 | US catalyst 3일 착시 재현 | `US_CATALYST_SCORE_BONUS_ENABLED=false` |
| T6 | 형식이탈 급증/지연초과 | `CLAUDE_THINKING_ENABLED=false` or 모델 롤백 |

모든 revert는 운영자 결정(운영자 확인 필수 파라미터 무단변경 금지). 이 문서는 조건만 정의.

## 7. 재개봉 금지 (오염 근거 확실, 이 측정에서 다시 제안 시 쳐냄)

예측·입력증강 / cap-widen / 리랭킹 / peak-trail 확장(STOP·TARGET·A1 floor·LADDER give-width) / 켈리 / 정적 레짐컷 / 목표 낮추기 net 시뮬 / confidence 튜닝. 전부 메모리에 분봉 양방향·placebo·OOS 기각 근거 있음.

## 8. 이 문서의 완료 조건

- [x] 활성 토글 집합 실측 확정(§1)
- [x] 0체결 근본원인 확정(§2·§3.5) — 배관 버그 아님, US A1이 라이브 경로 차단
- [x] W1 프레임 정정(§3.5) — US 복원은 라이브 무효+근거 착시, KR만 진짜 복원
- [x] US 양성 서브셋 carve 검증 → **실패**(결정시점 돌파태그 N6~13·비용후 net음·hold≤1d=lookahead·5월+/6월- OOS붕괴 = 분리 양성 없음)
- [x] **오염 메모리 파일 정정 완료**(handoff·feasibility-fix·a1·MEMORY.md)
- [x] **★결정: US A1 해제**(REQUIRE_TRADE_READY_US=false 양쪽, 다음 재시작 발동) — carve실패나 US off근거(-0.57%)=6월단일 옛데이터라 pre-block 모순, 위생토글 US 미검증→라이브 실측. A1 양시장 off
- [ ] 봇 재시작 후 US·KR 매수 실제 재개 확인(`v2_path_runs` FILLED/CLOSED>0)
- [ ] **새 US net 실측** — 옛 -0.57%가 위생토글로 개선되나(net_profitability_review·reward_risk_enforce_review). §3 토글별 판정
- [ ] 載重(저빈도/천장) 재도출 — clean 데이터로
- [ ] 커밋 정리

즉시 다음 행동(다음 세션): 봇 재시작(운영자) → US/KR 매수 재개 실측 → 새 US net 추적. de-confound §3 계획대로 토글별 판정. revert=`REQUIRE_TRADE_READY_US` 삭제.
